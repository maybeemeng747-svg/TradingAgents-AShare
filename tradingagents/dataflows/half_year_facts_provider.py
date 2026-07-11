# [HY-003] half_year_facts_provider
"""半年报事实表本地索引与只读查询 provider。

在 HY-001 半年报契约 lint、KB-010 缓存/freshness 之上，从 Tree Work 已消化的
``wiki/investment/`` 半年报/财报页（``report_type ∈ {财报分析, 半年报, 中报}``）
抽取**结构化事实表**，供 TA 报告 / TradeFlow 候选 / HY-005 事实反证 / HY-006
半年报因子等下游任务复用。

设计约束（对应任务 HY-003）：
  - **只读本地知识库**：仅用 ``open(..., "r", encoding="utf-8")`` 与 ``Path.iterdir``，
    绝不向知识库写文件；不写生产 DB。
  - **只抽取摘要字段和数值事实**：不输出长篇原文；每条事实裁剪到上限。
  - **字段来源保留**：每条事实/管理层表述/风险都带 ``rel_path``、``source_type``、
    ``financial_period``，便于 HY-005 反证与 citation 审计追溯。
  - **接入 KB-010 cache/freshness**：传入 :class:`KnowledgeCache` 时跳过全量扫描，
    直接基于缓存做事实抽取（语义与全量扫描等价）。
  - **缺字段/过期/事实冲突显式输出 data_status**：不得假装可用；stale / opinion_only /
    missing_period / missing_facts / conflict 各自有独立状态。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 KB-001 / HY-001 只读解析助手。
  - **不影响现有 local knowledge 普通查询**：本模块是独立的查询入口，不修改
    ``local_knowledge_provider`` 的任何行为。

data_status 状态机（单页粒度，与 raw_evidence 契约口径一致）：
  - ``fresh``：有 financial_period + financial_facts，非 stale，非 opinion_only。
  - ``stale``：valid_until 已过期 或 stale_risk=高（事实可能已过时）。
  - ``opinion_only``：source_type 全为 broker/media（HYF-007，观点冒充事实）。
  - ``missing_period``：缺 financial_period（HYF-001，无法建事实表主键）。
  - ``missing_facts``：缺 financial_facts（HYF-005，无可机读事实）。
  - ``conflict``：同 symbol 同 period 多页且关键指标数值不一致（见 ``_detect_conflicts``）。

查询级 status（聚合）：
  - ``HAS_FACTS``：至少一页 data_status=fresh。
  - ``NORMAL_NO_DATA``：无任何半年报页命中。
  - ``FAILED``：知识库不可读 / 路径不存在 / 解析异常。
  - ``STALE``：仅命中 stale 页。
  - ``LOW_CONFIDENCE``：仅命中 opinion_only / missing_facts / missing_period 页。

使用示例::

    from tradingagents.dataflows.half_year_facts_provider import (
        query_half_year_facts,
        render_half_year_facts_block,
    )
    result = query_half_year_facts(
        "/Users/maybee/Documents/knowledge", symbol="000977"
    )
    print(render_half_year_facts_block(result))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-001 只读解析助手，保持单一解析实现。
from tradingagents.dataflows.local_knowledge_audit import (
    INVESTMENT_SUBDIR,
    _audit_single_page,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
    default_knowledge_root,
)
# 复用 HY-001 lint 常量与归一化助手，确保与契约口径一致。
from tradingagents.dataflows.local_knowledge_lint import (
    HALF_YEAR_REPORT_TYPES,
    SOURCE_TYPE_FACT_VALUES,
    SOURCE_TYPE_OPINION_VALUES,
    _is_half_year_report,
    _is_valid_financial_period,
    _normalize_source_type_list,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    _normalize_str_list,
    _normalize_symbol_list,
    _symbol_matches,
)


# ── 常量 ──────────────────────────────────────────────────────────────

VENDOR = "tree_work_wiki"
TASK_CODE = "HY-003"

# data_status 单页枚举。
DATA_FRESH = "fresh"
DATA_STALE = "stale"
DATA_OPINION_ONLY = "opinion_only"
DATA_MISSING_PERIOD = "missing_period"
DATA_MISSING_FACTS = "missing_facts"
DATA_CONFLICT = "conflict"

# 事实字段裁剪上限（避免输出长篇原文）。
_FACT_MAX_CHARS = 120
_COMMENTARY_MAX_CHARS = 120
_RISK_MAX_CHARS = 80
_MAX_FACTS_ENTRIES = 8
_MAX_SEGMENT_ENTRIES = 5
_MAX_COMMENTARY_ENTRIES = 3
_MAX_GUIDANCE_ENTRIES = 3
_MAX_RISK_ENTRIES = 6
_MAX_SOURCE_LINK_ENTRIES = 5
_MAX_PAGES = 5

# 关键指标识别关键词（按优先级；首条命中即止）。
# metric_key → 触发关键词元组（中文/英文，子串匹配，大小写不敏感）。
_METRIC_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "revenue": ("营收", "营业收入", "营业总收入", "revenue", "总收入"),
    "net_profit": ("归母净利", "归母净利润", "净利润", "净利", "net profit", "net income"),
    "gross_margin": ("毛利率", "gross margin"),
    "operating_cash_flow": (
        "经营性现金流",
        "经营现金流",
        "经营活动现金流",
        "operating cash flow",
        "现金流净额",
    ),
}

# 数值 + 单位正则：``150.2亿`` / ``25.3%`` / ``22.1亿`` / ``-20.0%``。
_VALUE_RE = re.compile(
    r"(?:约|大约|近)?\s*([-+]?\d+(?:\.\d+)?)\s*(亿|万|万亿|亿元|万元|万亿|%|pp|个百分点)?"
)
# 同比/环比变化：``(+30.1% YoY)`` / ``(-2pp YoY)`` / ``+18%`` / ``同比 +60%``。
_CHANGE_RE = re.compile(
    r"(?:同比|环比|YoY|yoy|YoY)?\s*"
    r"([-+]\s*\d+(?:\.\d+)?\s*(?:%|pp|个百分点|倍))"
)
# 日期 YYYY-MM-DD。
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class ParsedMetric:
    """从 ``financial_facts`` 单条文本抽取的结构化指标。

    抽取失败时 ``value`` / ``change`` 为 ``None``，但 ``raw`` 始终保留原文本。
    """

    metric_key: str  # revenue / net_profit / gross_margin / operating_cash_flow / other
    metric_label: str  # 原始指标名（如 "营收" / "归母净利"）
    raw: str  # 原始事实文本（裁剪到 _FACT_MAX_CHARS）
    value: Optional[str] = None  # "150.2亿" / "25.3%"
    change: Optional[str] = None  # "+30.1%" / "-2pp"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_key": self.metric_key,
            "metric_label": self.metric_label,
            "raw": self.raw,
            "value": self.value,
            "change": self.change,
        }


@dataclass
class HalfYearFactsPage:
    """单页半年报事实表（只读，不携带长篇原文）。

    所有 list 字段都是裁剪后的摘要条目，每条裁剪到对应上限。
    """

    rel_path: str
    title: str
    financial_period: Optional[str]
    disclosure_date: Optional[str]
    source_type: List[str] = field(default_factory=list)
    financial_facts: List[ParsedMetric] = field(default_factory=list)
    segment_facts: List[str] = field(default_factory=list)
    management_commentary: List[str] = field(default_factory=list)
    forward_guidance: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    source_links: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    is_stale: bool = False
    is_opinion_only: bool = False
    missing_fields: List[str] = field(default_factory=list)
    data_status: str = DATA_FRESH
    updated_at: Optional[str] = None
    conflict_detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "title": self.title,
            "financial_period": self.financial_period,
            "disclosure_date": self.disclosure_date,
            "source_type": list(self.source_type),
            "financial_facts": [m.to_dict() for m in self.financial_facts],
            "segment_facts": list(self.segment_facts),
            "management_commentary": list(self.management_commentary),
            "forward_guidance": list(self.forward_guidance),
            "risk_factors": list(self.risk_factors),
            "source_links": list(self.source_links),
            "symbols": list(self.symbols),
            "is_stale": self.is_stale,
            "is_opinion_only": self.is_opinion_only,
            "missing_fields": list(self.missing_fields),
            "data_status": self.data_status,
            "updated_at": self.updated_at,
            "conflict_detail": self.conflict_detail,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HalfYearFactsPage":
        facts_raw = data.get("financial_facts") or []
        facts: List[ParsedMetric] = []
        for item in facts_raw:
            if isinstance(item, ParsedMetric):
                facts.append(item)
            elif isinstance(item, dict):
                facts.append(ParsedMetric(**{
                    k: item.get(k)
                    for k in (
                        "metric_key",
                        "metric_label",
                        "raw",
                        "value",
                        "change",
                    )
                    if k in item
                }))
        return cls(
            rel_path=str(data.get("rel_path") or ""),
            title=str(data.get("title") or ""),
            financial_period=data.get("financial_period"),
            disclosure_date=data.get("disclosure_date"),
            source_type=list(data.get("source_type") or []),
            financial_facts=facts,
            segment_facts=list(data.get("segment_facts") or []),
            management_commentary=list(data.get("management_commentary") or []),
            forward_guidance=list(data.get("forward_guidance") or []),
            risk_factors=list(data.get("risk_factors") or []),
            source_links=list(data.get("source_links") or []),
            symbols=list(data.get("symbols") or []),
            is_stale=bool(data.get("is_stale") or False),
            is_opinion_only=bool(data.get("is_opinion_only") or False),
            missing_fields=list(data.get("missing_fields") or []),
            data_status=str(data.get("data_status") or DATA_FRESH),
            updated_at=data.get("updated_at"),
            conflict_detail=data.get("conflict_detail"),
        )


@dataclass
class HalfYearFactsQueryResult:
    """整次半年报事实查询的聚合结果。"""

    status: str = STATUS_NORMAL_NO_DATA
    vendor: str = VENDOR
    task: str = TASK_CODE
    symbol: str = ""
    name: str = ""
    pages: List[HalfYearFactsPage] = field(default_factory=list)
    latest_period: Optional[str] = None
    latest_disclosure_date: Optional[str] = None
    summary: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    data_status: str = DATA_FRESH
    knowledge_root: str = ""
    query: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vendor": self.vendor,
            "task": self.task,
            "symbol": self.symbol,
            "name": self.name,
            "pages": [p.to_dict() for p in self.pages],
            "latest_period": self.latest_period,
            "latest_disclosure_date": self.latest_disclosure_date,
            "summary": list(self.summary),
            "risks": list(self.risks),
            "data_status": self.data_status,
            "knowledge_root": self.knowledge_root,
            "query": dict(self.query),
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HalfYearFactsQueryResult":
        """Reconstruct a query result from its dict form.

        [HY-004] half_year_report_block — used by the report attach chain to
        reuse the cached ``raw_evidence.half_year_facts`` payload produced by
        the data collector, mirroring :meth:`LocalKnowledgeQueryResult.from_dict`.
        Tolerant of missing keys so partial payloads (e.g. legacy rows) do not
        raise.
        """
        if not isinstance(data, dict):
            return cls()
        pages_raw = data.get("pages") or []
        pages: List[HalfYearFactsPage] = []
        if isinstance(pages_raw, list):
            for item in pages_raw:
                if isinstance(item, HalfYearFactsPage):
                    pages.append(item)
                elif isinstance(item, dict):
                    try:
                        pages.append(HalfYearFactsPage.from_dict(item))
                    except Exception:
                        continue
        return cls(
            status=str(data.get("status") or STATUS_NORMAL_NO_DATA),
            vendor=str(data.get("vendor") or VENDOR),
            task=str(data.get("task") or TASK_CODE),
            symbol=str(data.get("symbol") or ""),
            name=str(data.get("name") or ""),
            pages=pages,
            latest_period=data.get("latest_period"),
            latest_disclosure_date=data.get("latest_disclosure_date"),
            summary=list(data.get("summary") or []),
            risks=list(data.get("risks") or []),
            data_status=str(data.get("data_status") or DATA_FRESH),
            knowledge_root=str(data.get("knowledge_root") or ""),
            query=dict(data.get("query") or {}),
            errors=list(data.get("errors") or []),
        )


# ── 事实抽取 ──────────────────────────────────────────────────────────


def _classify_metric(text: str) -> Tuple[str, str]:
    """识别单条事实文本对应的指标 key 与 label。

    返回 ``(metric_key, metric_label)``。未命中已知指标时返回 ``("other", "")``。
    判断顺序：revenue → net_profit → gross_margin → operating_cash_flow。
    """
    lower = (text or "").lower()
    for key, keywords in _METRIC_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in lower:
                return key, kw
    return "other", ""


def _parse_value_and_change(text: str) -> Tuple[Optional[str], Optional[str]]:
    """从事实文本中抽取数值/单位 与 同比/环比变化。

    返回 ``(value, change)``。``"营收 150.2亿 (+30.1% YoY)"`` →
    ``("150.2亿", "+30.1%")``。解析失败对应位置为 ``None``。
    """
    if not text:
        return None, None
    value: Optional[str] = None
    change: Optional[str] = None

    m_change = _CHANGE_RE.search(text)
    if m_change:
        change = re.sub(r"\s+", "", m_change.group(1))

    m_val = _VALUE_RE.search(text)
    if m_val:
        num = m_val.group(1)
        unit = m_val.group(2) or ""
        # 毛利率类指标常带 %，避免把 change 的 % 误当 value 单位。
        if unit == "%" and change and change.endswith("%"):
            # 毛利率值：优先取首个 % 前的数值；change 取 +/- 开头的。
            value = f"{num}%"
        else:
            value = f"{num}{unit}".strip()
    return value, change


def _parse_single_fact(raw_text: str) -> ParsedMetric:
    """解析单条 financial_facts 文本为 ParsedMetric。

    永远保留 ``raw``（裁剪到 _FACT_MAX_CHARS）；``value`` / ``change`` 解析失败为 None。
    """
    raw = (raw_text or "").strip()
    if len(raw) > _FACT_MAX_CHARS:
        raw = raw[:_FACT_MAX_CHARS].rstrip() + "…"
    metric_key, metric_label = _classify_metric(raw)
    value, change = _parse_value_and_change(raw)
    return ParsedMetric(
        metric_key=metric_key,
        metric_label=metric_label,
        raw=raw,
        value=value,
        change=change,
    )


def _normalize_facts_list(raw: Any) -> List[str]:
    """把 frontmatter financial_facts/segment_facts/... 字段归一为字符串列表。

    接受 list / 单字符串。剥离每条首尾空白与引号，裁剪到 _FACT_MAX_CHARS。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = [str(s) for s in raw if s is not None]
    else:
        return []
    out: List[str] = []
    for item in items:
        text = str(item).strip().strip('"').strip("'").strip()
        if not text:
            continue
        out.append(text)
    return out


def _clip_list(items: List[str], max_chars: int, max_entries: int) -> List[str]:
    """裁剪每条到 max_chars，去重保序，截断到 max_entries。"""
    out: List[str] = []
    for item in items:
        text = item.strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        if text not in out:
            out.append(text)
        if len(out) >= max_entries:
            break
    return out


# ── 单页处理 ──────────────────────────────────────────────────────────


def _is_valid_date(value: Optional[str]) -> bool:
    if not value:
        return False
    return bool(_DATE_RE.match(str(value).strip()))


def _compute_page_data_status(
    *,
    financial_period: Optional[str],
    financial_facts: List[ParsedMetric],
    source_type: List[str],
    is_stale: bool,
) -> str:
    """计算单页 data_status。

    优先级：missing_period > stale > opinion_only > missing_facts > fresh。
    conflict 在聚合阶段由 :func:`_detect_conflicts` 标注。
    """
    if not financial_period:
        return DATA_MISSING_PERIOD
    if is_stale:
        return DATA_STALE
    is_opinion_only = bool(source_type) and all(
        s in SOURCE_TYPE_OPINION_VALUES for s in source_type
    )
    if is_opinion_only:
        return DATA_OPINION_ONLY
    if not financial_facts:
        return DATA_MISSING_FACTS
    return DATA_FRESH


def _is_page_stale(
    *,
    frontmatter: Dict[str, Any],
    page_audit: Any,
    today: Optional[date] = None,
) -> bool:
    """复用 KB-001 stale 判定口径（stale_risk=高 或 valid_until 已过期）。"""
    from tradingagents.dataflows.local_knowledge_lint import HIGH_STALE_RISK_VALUES

    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    if stale_val in HIGH_STALE_RISK_VALUES:
        return True
    if page_audit is not None and getattr(page_audit, "valid_until_expired", False):
        return True
    # 兜底：直接看 valid_until。
    valid_until = _safe_str(frontmatter.get("valid_until"))
    if valid_until:
        from tradingagents.dataflows.local_knowledge_audit import _is_valid_until_expired

        if _is_valid_until_expired(valid_until, today=today):
            return True
    return False


def _build_facts_page(
    rel_path: str,
    abs_path: Path,
    today: Optional[date] = None,
) -> Tuple[Optional[HalfYearFactsPage], Optional[str]]:
    """解析单页半年报为 HalfYearFactsPage。

    返回 ``(page, error)``：
      - 非半年报页 → ``(None, None)``（静默跳过，不报错）。
      - 半年报页解析成功 → ``(page, None)``。
      - 解析异常 → ``(None, error_msg)``。
    """
    try:
        page_audit = _audit_single_page(rel_path, abs_path)
    except Exception as exc:  # pragma: no cover - 容错
        return None, f"{rel_path}: 审计失败 {exc!r}"

    try:
        text = _read_text_safe(abs_path)
        fm_text, _body = _split_frontmatter(text)
        frontmatter = _parse_frontmatter(fm_text)
    except Exception as exc:  # pragma: no cover
        return None, f"{rel_path}: 解析失败 {exc!r}"

    if not _is_half_year_report(frontmatter):
        return None, None

    # financial_period：缺或非法都视为 None（与 HYF-001 口径一致）。
    period_raw = _safe_str(frontmatter.get("financial_period"))
    period_text = period_raw or ""
    if not period_text or not _is_valid_financial_period(period_text):
        period_text = None

    disclosure = _safe_str(frontmatter.get("disclosure_date"))
    disclosure_str = disclosure or ""
    if not disclosure_str or not _is_valid_date(disclosure_str):
        # 保留原值供调试，但 data_status 不依赖它（HYF-003 是 warning 不阻塞）。
        disclosure_str = disclosure_str or None

    source_type = _normalize_source_type_list(frontmatter.get("source_type"))
    symbols = _normalize_symbol_list(frontmatter.get("symbols"))
    updated_at = _safe_str(frontmatter.get("updated"))

    facts_raw = _normalize_facts_list(frontmatter.get("financial_facts"))
    facts_parsed = [_parse_single_fact(t) for t in facts_raw[:_MAX_FACTS_ENTRIES]]

    segment = _clip_list(
        _normalize_facts_list(frontmatter.get("segment_facts")),
        _FACT_MAX_CHARS,
        _MAX_SEGMENT_ENTRIES,
    )
    commentary = _clip_list(
        _normalize_facts_list(frontmatter.get("management_commentary")),
        _COMMENTARY_MAX_CHARS,
        _MAX_COMMENTARY_ENTRIES,
    )
    guidance = _clip_list(
        _normalize_facts_list(frontmatter.get("forward_guidance")),
        _COMMENTARY_MAX_CHARS,
        _MAX_GUIDANCE_ENTRIES,
    )
    risks = _clip_list(
        _normalize_facts_list(frontmatter.get("risk_factors")),
        _RISK_MAX_CHARS,
        _MAX_RISK_ENTRIES,
    )
    source_links = _clip_list(
        _normalize_facts_list(frontmatter.get("source_links")),
        _FACT_MAX_CHARS,
        _MAX_SOURCE_LINK_ENTRIES,
    )

    is_stale = _is_page_stale(
        frontmatter=frontmatter, page_audit=page_audit, today=today
    )

    missing: List[str] = []
    if not period_text:
        missing.append("financial_period")
    if not facts_parsed:
        missing.append("financial_facts")
    if not disclosure_str:
        missing.append("disclosure_date")
    if not source_type:
        missing.append("source_type")
    if not risks:
        missing.append("risk_factors")

    data_status = _compute_page_data_status(
        financial_period=period_text,
        financial_facts=facts_parsed,
        source_type=source_type,
        is_stale=is_stale,
    )

    page = HalfYearFactsPage(
        rel_path=rel_path,
        title=page_audit.title or abs_path.stem,
        financial_period=period_text,
        disclosure_date=disclosure_str,
        source_type=source_type,
        financial_facts=facts_parsed,
        segment_facts=segment,
        management_commentary=commentary,
        forward_guidance=guidance,
        risk_factors=risks,
        source_links=source_links,
        symbols=symbols,
        is_stale=is_stale,
        is_opinion_only=(data_status == DATA_OPINION_ONLY),
        missing_fields=missing,
        data_status=data_status,
        updated_at=updated_at,
    )
    return page, None


# ── 缓存驱动的单页处理（KB-010 集成）──────────────────────────────────


def _build_facts_page_from_cache(
    page: Any,
    today: Optional[date] = None,
) -> Optional[HalfYearFactsPage]:
    """从 :class:`CachedPageData` 构造 HalfYearFactsPage（不重新读文件）。

    与 :func:`_build_facts_page` 语义等价：非半年报页返回 ``None``（静默跳过）。
    复用 HY-001 / KB-001 的 stale / source_type 判定，确保缓存命中与全量扫描
    产出**语义等价**的事实表。
    """
    frontmatter = page.frontmatter if isinstance(page.frontmatter, dict) else {}
    if not _is_half_year_report(frontmatter):
        return None

    period_raw = _safe_str(frontmatter.get("financial_period"))
    period_text = period_raw or ""
    if not period_text or not _is_valid_financial_period(period_text):
        period_text = None

    disclosure = _safe_str(frontmatter.get("disclosure_date"))
    disclosure_str = disclosure or ""
    if not disclosure_str or not _is_valid_date(disclosure_str):
        disclosure_str = disclosure_str or None

    source_type = _normalize_source_type_list(frontmatter.get("source_type"))
    facts_raw = _normalize_facts_list(frontmatter.get("financial_facts"))
    facts_parsed = [_parse_single_fact(t) for t in facts_raw[:_MAX_FACTS_ENTRIES]]

    segment = _clip_list(
        _normalize_facts_list(frontmatter.get("segment_facts")),
        _FACT_MAX_CHARS,
        _MAX_SEGMENT_ENTRIES,
    )
    commentary = _clip_list(
        _normalize_facts_list(frontmatter.get("management_commentary")),
        _COMMENTARY_MAX_CHARS,
        _MAX_COMMENTARY_ENTRIES,
    )
    guidance = _clip_list(
        _normalize_facts_list(frontmatter.get("forward_guidance")),
        _COMMENTARY_MAX_CHARS,
        _MAX_GUIDANCE_ENTRIES,
    )
    risks = _clip_list(
        _normalize_facts_list(frontmatter.get("risk_factors")),
        _RISK_MAX_CHARS,
        _MAX_RISK_ENTRIES,
    )
    source_links = _clip_list(
        _normalize_facts_list(frontmatter.get("source_links")),
        _FACT_MAX_CHARS,
        _MAX_SOURCE_LINK_ENTRIES,
    )

    is_stale = _is_page_stale_from_cache(page, today=today)

    missing: List[str] = []
    if not period_text:
        missing.append("financial_period")
    if not facts_parsed:
        missing.append("financial_facts")
    if not disclosure_str:
        missing.append("disclosure_date")
    if not source_type:
        missing.append("source_type")
    if not risks:
        missing.append("risk_factors")

    data_status = _compute_page_data_status(
        financial_period=period_text,
        financial_facts=facts_parsed,
        source_type=source_type,
        is_stale=is_stale,
    )

    return HalfYearFactsPage(
        rel_path=page.rel_path,
        title=page.title,
        financial_period=period_text,
        disclosure_date=disclosure_str,
        source_type=source_type,
        financial_facts=facts_parsed,
        segment_facts=segment,
        management_commentary=commentary,
        forward_guidance=guidance,
        risk_factors=risks,
        source_links=source_links,
        symbols=list(page.symbols or []),
        is_stale=is_stale,
        is_opinion_only=(data_status == DATA_OPINION_ONLY),
        missing_fields=missing,
        data_status=data_status,
        updated_at=page.updated_at,
    )


def _is_page_stale_from_cache(
    page: Any,
    today: Optional[date] = None,
) -> bool:
    """缓存驱动的 stale 判定（与全量扫描口径一致）。"""
    from tradingagents.dataflows.local_knowledge_lint import HIGH_STALE_RISK_VALUES

    if getattr(page, "stale_risk_high", False):
        return True
    if getattr(page, "valid_until_expired", False):
        return True
    frontmatter = page.frontmatter if isinstance(page.frontmatter, dict) else {}
    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    if stale_val in HIGH_STALE_RISK_VALUES:
        return True
    valid_until = _safe_str(frontmatter.get("valid_until"))
    if valid_until:
        from tradingagents.dataflows.local_knowledge_audit import _is_valid_until_expired

        if _is_valid_until_expired(valid_until, today=today):
            return True
    return False


# ── 冲突检测 ──────────────────────────────────────────────────────────


_CONFLICT_METRIC_KEYS = ("revenue", "net_profit", "gross_margin")


def _detect_conflicts(pages: List[HalfYearFactsPage]) -> None:
    """检测同 symbol 同 period 多页关键指标数值冲突。

    命中时把相关页 ``data_status`` 改为 ``conflict``，并写入 ``conflict_detail``。
    冲突定义：同一 ``(symbol, financial_period)`` 下，同一指标 key 出现 ≥2 个
    不同的 ``value``。只对 fresh / stale 页检测，missing_period 页无主键跳过。
    """
    # 按 (period) 聚合（symbol 由查询层保证一致；多 symbol 页按各自 period 归组）。
    by_period: Dict[str, List[HalfYearFactsPage]] = {}
    for p in pages:
        if not p.financial_period:
            continue
        by_period.setdefault(p.financial_period, []).append(p)

    for period, group in by_period.items():
        if len(group) < 2:
            continue
        # 每个指标 key 收集所有 (value, rel_path)。
        metric_values: Dict[str, List[Tuple[str, str]]] = {}
        for p in group:
            for m in p.financial_facts:
                if m.metric_key not in _CONFLICT_METRIC_KEYS:
                    continue
                if not m.value:
                    continue
                metric_values.setdefault(m.metric_key, []).append(
                    (m.value, p.rel_path)
                )
        for metric_key, pairs in metric_values.items():
            distinct = {v for v, _ in pairs}
            if len(distinct) > 1:
                detail_parts = [f"{v}({rp})" for v, rp in pairs]
                detail = (
                    f"{period} {metric_key} 冲突："
                    + " vs ".join(detail_parts)
                )
                for p in group:
                    # 只标涉及该指标的页。
                    page_has_metric = any(
                        m.metric_key == metric_key and m.value
                        for m in p.financial_facts
                    )
                    if page_has_metric and p.data_status == DATA_FRESH:
                        p.data_status = DATA_CONFLICT
                        p.conflict_detail = detail


# ── 主查询逻辑 ────────────────────────────────────────────────────────


def query_half_year_facts(
    knowledge_root: str,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    cache: Any = None,
    today: Optional[date] = None,
    max_pages: int = _MAX_PAGES,
) -> HalfYearFactsQueryResult:
    """按 symbol/name 只读查询半年报事实表。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        symbol: A 股代码或代码+后缀（如 ``000977`` / ``000977.SZ``）。
        name: 公司简称（与页面 symbols 简称或 title 子串匹配）。
        cache: [KB-010] 可选的 :class:`KnowledgeCache`。传入时跳过全量扫描，
            直接基于缓存做事实抽取（语义与全量扫描等价）。``None`` 时走全量扫描。
        today: 用于 stale 判定的基准日期（测试注入）；``None`` 用 ``date.today()``。
        max_pages: 最多返回的命中页数。

    返回:
        :class:`HalfYearFactsQueryResult`。永远不会因单页解析失败而抛异常：
        单页失败记入 ``errors`` 并跳过；知识库不存在时返回 status=FAILED。

    匹配规则（与 KB-003 口径一致）：
        - symbol 命中页面 frontmatter ``symbols``；
        - name 命中页面 title 或 symbols 简称子串；
        - 同时只保留 ``report_type ∈ {财报分析, 半年报, 中报}`` 的页面。
    """
    symbol = (symbol or "").strip() or None
    name = (name or "").strip() or None
    if today is None:
        today = date.today()

    query_desc: Dict[str, Any] = {}
    if symbol:
        query_desc["symbol"] = symbol
    if name:
        query_desc["name"] = name

    root = Path(knowledge_root).expanduser()
    result = HalfYearFactsQueryResult(
        knowledge_root=str(root),
        symbol=symbol or "",
        name=name or "",
        query=query_desc,
    )

    if not root.exists():
        result.status = STATUS_FAILED
        result.data_status = DATA_MISSING_FACTS
        result.errors.append(f"knowledge_root 不存在: {root}")
        return result

    if not any([symbol, name]):
        result.status = STATUS_NORMAL_NO_DATA
        result.data_status = DATA_MISSING_FACTS
        result.errors.append("未提供查询条件（symbol/name 任一）")
        return result

    pages: List[HalfYearFactsPage] = []

    if cache is not None:
        # [KB-010] 缓存路径：基于预解析页面做事实抽取，不重新读文件。
        result.errors.extend(cache.errors or [])
        for _rel, cached_page in cache.pages.items():
            try:
                if not _page_matches_query(cached_page, symbol=symbol, name=name):
                    continue
                hy_page = _build_facts_page_from_cache(cached_page, today=today)
            except Exception as exc:  # pragma: no cover - 容错
                result.errors.append(f"{_rel}: 缓存事实抽取失败 {exc!r}")
                continue
            if hy_page is None:
                continue
            pages.append(hy_page)
    else:
        # 全量扫描路径。
        investment_dir = root / INVESTMENT_SUBDIR
        if not investment_dir.exists():
            result.errors.append(f"investment 分区不存在: {investment_dir}")
            result.status = STATUS_NORMAL_NO_DATA
            result.data_status = DATA_MISSING_FACTS
            return result
        md_files = _iter_markdown_files(investment_dir)
        for md in md_files:
            rel = str(md.relative_to(root))
            # 先读 frontmatter 做 symbol/name 与 report_type 预筛，避免对非半年报
            # 页跑完整 audit。
            try:
                text = _read_text_safe(md)
                fm_text, _body = _split_frontmatter(text)
                frontmatter = _parse_frontmatter(fm_text)
            except Exception as exc:  # pragma: no cover
                result.errors.append(f"{rel}: 解析失败 {exc!r}")
                continue

            if not _is_half_year_report(frontmatter):
                continue
            page_symbols = _normalize_symbol_list(frontmatter.get("symbols"))
            if not _query_matches_page(symbol, name, page_symbols, frontmatter, md):
                continue

            try:
                hy_page, err = _build_facts_page(rel, md, today=today)
            except Exception as exc:  # pragma: no cover
                result.errors.append(f"{rel}: 事实抽取失败 {exc!r}")
                continue
            if err:
                result.errors.append(err)
                continue
            if hy_page is None:
                continue
            pages.append(hy_page)

    # 冲突检测（在排序/截断前，确保同 period 多页都被检查）。
    _detect_conflicts(pages)

    # 排序：fresh 优先，其次 stale，最后 missing/opinion/conflict；同优先级按 period 倒序。
    pages.sort(key=_page_rank_key)
    if len(pages) > max_pages:
        pages = pages[:max_pages]
    result.pages = pages

    _aggregate_result(result)
    return result


def _page_matches_query(
    cached_page: Any,
    *,
    symbol: Optional[str],
    name: Optional[str],
) -> bool:
    """缓存驱动的查询命中判定（report_type + symbol/name）。"""
    frontmatter = cached_page.frontmatter if isinstance(cached_page.frontmatter, dict) else {}
    if not _is_half_year_report(frontmatter):
        return False
    page_symbols = list(cached_page.symbols or [])
    return _query_matches_page(
        symbol, name, page_symbols, frontmatter, cached_page
    )


def _query_matches_page(
    symbol: Optional[str],
    name: Optional[str],
    page_symbols: List[str],
    frontmatter: Dict[str, Any],
    page: Any,
) -> bool:
    """统一的查询命中判定：symbol 或 name 任一命中即可。

    - symbol：复用 KB-003 ``_symbol_matches``（bare code / 全代码 / 简称子串）。
    - name：页面 title 或 symbols 简称子串匹配（大小写不敏感）。
    """
    title = getattr(page, "title", None) or str(
        frontmatter.get("title") or ""
    )
    if symbol:
        if _symbol_matches(symbol, page_symbols):
            return True
    if name:
        q = name.lower()
        if q and q in (title or "").lower():
            return True
        for entry in page_symbols:
            _, sname = _split_symbol_entry(entry)
            if sname and q in sname.lower():
                return True
    return False


def _split_symbol_entry(entry: str) -> Tuple[str, str]:
    """``603296.SH 华勤技术`` → ``("603296.SH", "华勤技术")``。"""
    parts = entry.split(maxsplit=1)
    code = parts[0] if parts else ""
    sname = parts[1] if len(parts) > 1 else ""
    return code, sname


def _page_rank_key(page: HalfYearFactsPage) -> Tuple[int, str, str]:
    """排序键：(data_status_rank, period_desc, rel_path)。

    fresh → conflict → stale → missing_facts → opinion_only → missing_period。
    同优先级按 financial_period 倒序（最新报告期在前）。
    """
    status_rank = {
        DATA_FRESH: 0,
        DATA_CONFLICT: 1,
        DATA_STALE: 2,
        DATA_MISSING_FACTS: 3,
        DATA_OPINION_ONLY: 4,
        DATA_MISSING_PERIOD: 5,
    }.get(page.data_status, 9)
    period = page.financial_period or ""
    return (status_rank, _period_sort_desc(period), page.rel_path)


def _period_sort_desc(period: str) -> str:
    """让 period 按倒序排（最新在前）。

    用一个高位反转：对 YYYY 开头的 period，取负年份前缀让大的排前面。
    简化实现：直接返回 period，依赖字符串比较 + 调用方对符号的选择。
    这里返回 ``period`` 的反转串不可靠，改为返回原值并在 sort 中用 reverse
    风格——但 Python sort 不支持单字段 reverse。
    改为：返回一个能让更大 period 排更前的键。
    """
    # 提取年份与期次，构造倒序键。
    m = re.match(r"(\d{4})", period)
    if not m:
        return "0_" + period
    year = int(m.group(1))
    # H1 < H2 < 中报 < 年报；用粗粒度期次权重。
    half = 0
    if "H2" in period or "年报" in period or "三季报" in period:
        half = 1
    return f"{9999 - year:04d}_{9 - half}_{period}"


def _aggregate_result(result: HalfYearFactsQueryResult) -> None:
    """聚合 pages → status / latest_period / summary / risks / data_status。"""
    pages = result.pages
    if not pages:
        result.status = STATUS_NORMAL_NO_DATA
        result.data_status = DATA_MISSING_FACTS
        return

    # latest_period / latest_disclosure_date：取所有页最大值（period 字符串比较）。
    periods = [p.financial_period for p in pages if p.financial_period]
    result.latest_period = max(periods) if periods else None
    disclosures = [p.disclosure_date for p in pages if p.disclosure_date]
    result.latest_disclosure_date = max(disclosures) if disclosures else None

    # summary：每页取首条 financial_fact raw（去重，最多 3 条）。
    summary_entries: List[str] = []
    for p in pages:
        if p.financial_facts and p.financial_facts[0].raw:
            text = f"[{p.financial_period or '?'}] {p.financial_facts[0].raw}"
            if text not in summary_entries:
                summary_entries.append(text)
        if len(summary_entries) >= 3:
            break
    result.summary = summary_entries

    # risks：聚合所有页 risk_factors（去重，最多 6 条）。
    risk_seen: List[str] = []
    for p in pages:
        for r in p.risk_factors:
            if r not in risk_seen:
                risk_seen.append(r)
    result.risks = risk_seen[:_MAX_RISK_ENTRIES]

    # 状态机：是否有 fresh / conflict 页？
    has_fresh = any(p.data_status == DATA_FRESH for p in pages)
    has_conflict = any(p.data_status == DATA_CONFLICT for p in pages)
    has_only_stale = all(
        p.data_status == DATA_STALE for p in pages
    )
    has_only_low = all(
        p.data_status
        in (DATA_OPINION_ONLY, DATA_MISSING_FACTS, DATA_MISSING_PERIOD)
        for p in pages
    )

    if has_fresh:
        result.status = STATUS_HAS_DATA
        result.data_status = DATA_FRESH
    elif has_conflict:
        result.status = STATUS_HAS_DATA
        result.data_status = DATA_CONFLICT
    elif has_only_stale:
        result.status = STATUS_STALE
        result.data_status = DATA_STALE
    elif has_only_low:
        result.status = STATUS_LOW_CONFIDENCE
        # 取首个非 fresh 页的 data_status 作为聚合。
        for p in pages:
            if p.data_status in (
                DATA_OPINION_ONLY,
                DATA_MISSING_FACTS,
                DATA_MISSING_PERIOD,
            ):
                result.data_status = p.data_status
                break
    else:
        # 混合（stale + missing 等），按低置信处理。
        result.status = STATUS_LOW_CONFIDENCE
        result.data_status = DATA_MISSING_FACTS


# ── 报告渲染 ──────────────────────────────────────────────────────────


_DATA_STATUS_LABELS = {
    DATA_FRESH: "事实可用",
    DATA_STALE: "已过期（仅供参考）",
    DATA_OPINION_ONLY: "观点冒充事实（不进入反证）",
    DATA_MISSING_PERIOD: "缺报告期（无法建事实表主键）",
    DATA_MISSING_FACTS: "缺机读事实",
    DATA_CONFLICT: "事实冲突（需 Tree Work 复核）",
}

_STATUS_LABELS = {
    STATUS_HAS_DATA: "已命中半年报事实",
    STATUS_NORMAL_NO_DATA: "无半年报事实命中",
    STATUS_FAILED: "半年报事实查询失败",
    STATUS_STALE: "半年报事实已过期",
    STATUS_LOW_CONFIDENCE: "半年报事实低置信",
}


def render_half_year_facts_block(result: HalfYearFactsQueryResult) -> str:
    """渲染 Markdown "半年报事实" 区块。

    最多展示每页关键指标 + 分业务事实 + 管理层表述 + 风险 + 来源路径。
    不输出长篇原文。状态为 NORMAL_NO_DATA / FAILED 时返回空字符串，让上层
    可以选择隐藏区块。

    本区块只作为背景事实源，**不改变强动作门禁**。
    """
    if not isinstance(result, HalfYearFactsQueryResult):
        return ""
    if result.status in (STATUS_NORMAL_NO_DATA, STATUS_FAILED):
        return ""

    lines: List[str] = []
    lines.append("### 半年报事实")
    lines.append("")
    status_label = _STATUS_LABELS.get(result.status, result.status)
    data_label = _DATA_STATUS_LABELS.get(result.data_status, result.data_status)
    lines.append(
        f"> 来源：Tree Work `wiki/investment` 财报页（vendor=tree_work_wiki）— "
        f"{status_label}；data_status={data_label}；"
        f"半年报事实只作背景证据，不替代行情/资金/公告原文，不改变强动作门禁。"
    )
    lines.append("")

    for idx, p in enumerate(result.pages[:_MAX_PAGES], 1):
        ds_label = _DATA_STATUS_LABELS.get(p.data_status, p.data_status)
        stale_tag = " · STALE" if p.is_stale else ""
        opinion_tag = " · OPINION_AS_FACT" if p.is_opinion_only else ""
        conflict_tag = " · CONFLICT" if p.data_status == DATA_CONFLICT else ""
        missing_tag = (
            f" · 缺字段:{','.join(p.missing_fields)}" if p.missing_fields else ""
        )
        tag_suffix = stale_tag + opinion_tag + conflict_tag + missing_tag
        lines.append(
            f"**{idx}. {p.title}**（{p.financial_period or '报告期缺失'}"
            f"{f' · 披露 {p.disclosure_date}' if p.disclosure_date else ''}"
            f"{tag_suffix}）"
        )
        lines.append("")

        if p.financial_facts:
            lines.append("- 关键指标：")
            for m in p.financial_facts[:5]:
                val_part = m.value or "?"
                change_part = f" ({m.change})" if m.change else ""
                lines.append(f"  - {m.metric_label or m.metric_key}: {val_part}{change_part}")
            lines.append("")

        if p.segment_facts:
            lines.append("- 分业务事实：")
            for seg in p.segment_facts[:3]:
                lines.append(f"  - {seg}")
            lines.append("")

        if p.management_commentary:
            lines.append("- 管理层表述（公司口径，半事实）：")
            for mc in p.management_commentary[:2]:
                lines.append(f"  - {mc}")
            lines.append("")

        if p.forward_guidance:
            lines.append("- 前瞻指引（公司指引，半事实）：")
            for fg in p.forward_guidance[:2]:
                lines.append(f"  - {fg}")
            lines.append("")

        if p.risk_factors:
            lines.append("- 结构化风险：")
            for r in p.risk_factors[:4]:
                lines.append(f"  - {r}")
            lines.append("")

        if p.data_status == DATA_CONFLICT and p.conflict_detail:
            lines.append(f"- ⚠️ 事实冲突：{p.conflict_detail}")
            lines.append("")

        lines.append(f"- 原页面：`{p.rel_path}`；source_type: {','.join(p.source_type) or '(未声明)'}")
        lines.append("")

    if result.risks:
        lines.append("**风险聚合（半年报事实源）：**")
        lines.append("")
        for r in result.risks[:_MAX_RISK_ENTRIES]:
            lines.append(f"- {r}")
        lines.append("")

    if result.latest_period:
        lines.append(
            f"_最新报告期：{result.latest_period}"
            f"{f' · 披露 {result.latest_disclosure_date}' if result.latest_disclosure_date else ''}"
            f"；data_status={data_label}_"
        )
        lines.append("")

    return "\n".join(lines)


# ── DataCollector 接入辅助 ────────────────────────────────────────────


def build_half_year_facts_raw_evidence_entry(
    result: HalfYearFactsQueryResult,
    trade_date: str,
    fetched_at: str,
) -> Dict[str, Any]:
    """把查询结果转为 ``DataCollector.build_raw_evidence`` 兼容的 raw_evidence 条目。

    [HY-003] half_year_facts_provider — 供 HY-004 TA 报告区块接入使用。
    """
    payload = result.to_dict()
    return {
        "raw": payload,
        "field": "half_year_facts",
        "status": result.status,
        "vendor": VENDOR,
        "endpoint": "wiki/investment#half_year",
        "as_of": trade_date,
        "fetched_at": fetched_at,
        "record_count": len(result.pages),
        "unit": None,
        "error": "; ".join(result.errors) if result.errors else None,
        "fallback_from": None,
        "source_url": None,
        "is_realtime_patched": False,
        "source_type": "tree_work_wiki",
        "unit_verified": None,
        "query_mode": None,
        "adjustment": None,
        "force_reason": None,
    }


def query_failed_entry(
    trade_date: str,
    fetched_at: str,
    error: str,
) -> Dict[str, Any]:
    """构造 FAILED 状态的 raw_evidence 条目（知识库不可读/异常时使用）。"""
    return {
        "raw": None,
        "field": "half_year_facts",
        "status": STATUS_FAILED,
        "vendor": VENDOR,
        "endpoint": "wiki/investment#half_year",
        "as_of": trade_date,
        "fetched_at": fetched_at,
        "record_count": 0,
        "unit": None,
        "error": (error or "")[:200] or None,
        "fallback_from": None,
        "source_url": None,
        "is_realtime_patched": False,
        "source_type": "tree_work_wiki",
        "unit_verified": None,
        "query_mode": None,
        "adjustment": None,
        "force_reason": None,
    }


# ── 便利函数 ──────────────────────────────────────────────────────────


def suggest_query_output_path(docs_dir: str = "docs/knowledge_reports") -> str:
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"half_year_facts_query-{today}.md")


def render_half_year_facts_report(result: HalfYearFactsQueryResult) -> str:
    """渲染完整的半年报事实查询报告（含元信息），用于 CLI 输出。"""
    lines: List[str] = []
    lines.append(f"# 半年报事实查询报告 — {TASK_CODE}")
    lines.append("")
    lines.append(f"- 知识库：`{result.knowledge_root}`")
    lines.append(f"- 查询：`{result.query}`")
    lines.append(f"- 状态：{result.status}（data_status={result.data_status}）")
    lines.append(f"- 命中页数：{len(result.pages)}")
    if result.latest_period:
        lines.append(f"- 最新报告期：{result.latest_period}")
    if result.errors:
        lines.append(f"- 错误：{'; '.join(result.errors[:5])}")
    lines.append("")
    block = render_half_year_facts_block(result)
    if block:
        lines.append(block)
    else:
        lines.append("（无半年报事实命中）")
    return "\n".join(lines)


__all__ = [
    "VENDOR",
    "TASK_CODE",
    "DATA_FRESH",
    "DATA_STALE",
    "DATA_OPINION_ONLY",
    "DATA_MISSING_PERIOD",
    "DATA_MISSING_FACTS",
    "DATA_CONFLICT",
    "ParsedMetric",
    "HalfYearFactsPage",
    "HalfYearFactsQueryResult",
    "query_half_year_facts",
    "render_half_year_facts_block",
    "render_half_year_facts_report",
    "build_half_year_facts_raw_evidence_entry",
    "query_failed_entry",
    "suggest_query_output_path",
]
