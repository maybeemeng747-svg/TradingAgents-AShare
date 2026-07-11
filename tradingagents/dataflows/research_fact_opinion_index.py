# [KB-015] research_fact_opinion_index
"""研报观点事实分离与 TA 可消费摘要索引。

在 KB-001 只读**审计**、KB-002 契约 **lint**、KB-003 raw_evidence 接入、
KB-014 citation policy 之上，本模块把 Tree Work 已消化的
``wiki/investment/`` 页面中的「观点 / 事实 / 预测 / 风险 / 待验证事项」拆成
TA 可消费的结构化摘要索引，避免研报观点被误当成公告/财报事实。

设计约束（对应任务 KB-015）：
  - **只读本地知识库**：仅用 ``open(..., "r", encoding="utf-8")`` 与
    ``Path.iterdir``，绝不向知识库写文件；不写生产 DB。
  - **不抓取新研报正文**：只读取 KB-001/KB-002 已解析的 frontmatter / 章节信息，
    不向外网发请求。
  - **不复制长篇原文**：每条 claim 文本裁剪到上限（``_CLAIM_MAX_CHARS``）。
  - **不把券商观点直接转成交易动作**：``claim_type`` 只做事实/观点分离标签，
    不改变强动作门禁（Buy/Risk Level 仍由 DECISION-001/TF-QUALITY-001 等控制）。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 KB-001 / KB-014 只读解析助手。
  - **接入 KB-010 cache/freshness**：传入 :class:`KnowledgeCache` 时跳过全量扫描，
    直接基于缓存做 claim 抽取（语义与全量扫描等价）。索引损坏时可重建。
  - **弱来源/过期来源只降置信，不覆盖动作语义**：``stale_status`` /
    ``confidence`` 反映来源可信度，但不会把观点变成事实。

claim_type 分类（与 KB-014 citation policy 联动）：
  - ``fact``：来自 ``original_filing`` / ``official_notice`` 的事实类字段
    （``financial_facts`` / ``segment_facts`` / ``management_commentary``）。
  - ``opinion``：来自 ``broker_research`` / ``media`` 的事实类字段，或正文中
    的「核心观点 / 投资逻辑 / 一句话总结」段——**观点冒充事实**会被标为
    ``opinion`` 而非 ``fact``。
  - ``forecast``：``forward_guidance`` 字段或正文中的前瞻指引段。
  - ``risk``：``risk_factors`` 字段或正文「风险提示 / 风险」段。
  - ``unknown``：无法确定来源或字段类型时兜底。

stale_status 状态机（单页粒度）：
  - ``fresh``：非 stale、非 low_confidence、非待补充。
  - ``stale``：``stale_risk=高`` 或 ``valid_until`` 已过期。
  - ``low_confidence``：``evidence_level`` 为低 / 待补充 / 弱来源。

查询级 status（聚合）：
  - ``HAS_DATA``：至少一页 stale_status=fresh 且有 claim。
  - ``NORMAL_NO_DATA``：无任何命中页。
  - ``FAILED``：知识库不可读 / 路径不存在 / 解析异常。
  - ``STALE``：仅命中 stale 页。
  - ``LOW_CONFIDENCE``：仅命中 low_confidence 页。

使用示例::

    from tradingagents.dataflows.research_fact_opinion_index import (
        build_research_fact_opinion_index,
        render_research_fact_opinion_report,
    )
    result = build_research_fact_opinion_index(
        "/Users/maybee/Documents/knowledge", symbol="603296"
    )
    print(render_research_fact_opinion_report(result))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-001 只读解析助手，保持单一解析实现，避免行为分叉。
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
from tradingagents.dataflows.local_knowledge_lint import (
    HIGH_STALE_RISK_VALUES,
    LOW_CONFIDENCE_EVIDENCE_LEVELS,
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
from tradingagents.dataflows.citation_policy import (  # [KB-014] citation_policy
    OPINION_ONLY_TIERS,
    SOURCE_TYPE_FACT_VALUES,
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_ORIGINAL_FILING,
    TIER_OFFICIAL_NOTICE,
    TIER_UNKNOWN,
    TIER_USER_NOTE,
    CitationAssessment,
    apply_tier_to_confidence,
    classify_source_quality_tier,
    compute_tier_confidence_weight,
)


# ── 常量 ──────────────────────────────────────────────────────────────

VENDOR = "tree_work_wiki"
TASK_CODE = "KB-015"

# claim_type 取值（与任务原文严格一致）。
CLAIM_FACT = "fact"
CLAIM_OPINION = "opinion"
CLAIM_FORECAST = "forecast"
CLAIM_RISK = "risk"
CLAIM_UNKNOWN = "unknown"
CLAIM_TYPES: Tuple[str, ...] = (
    CLAIM_FACT,
    CLAIM_OPINION,
    CLAIM_FORECAST,
    CLAIM_RISK,
    CLAIM_UNKNOWN,
)

# stale_status 单页枚举。
STALE_FRESH = "fresh"
STALE_STALE = "stale"
STALE_LOW_CONFIDENCE = "low_confidence"

# 文本裁剪上限（避免输出长篇原文）。
_CLAIM_MAX_CHARS = 120
_VERIFICATION_MAX_CHARS = 100
_MAX_CLAIMS_PER_CATEGORY = 6
_MAX_VERIFICATION_ENTRIES = 5
_MAX_PAGES = 10

# 正文段落标题（复用 KB-003 口径，保持一致）。
_OPINION_HEADERS = (
    "一句话总结",
    "核心观点",
    "核心结论",
    "投资逻辑",
    "核心数据",
)
_RISK_HEADERS = (
    "风险提示",
    "风险",
    "风险与不确定性",
)
_FORECAST_HEADERS = (
    "前瞻指引",
    "未来展望",
    "业绩展望",
    "指引",
)
# 观点段（核心观点/投资逻辑等）在正文中出现时，无论来源 tier 如何，都视为 opinion。
_OPINION_BODY_ORIGIN = "body:opinion"
_RISK_BODY_ORIGIN = "body:risk"
_FORECAST_BODY_ORIGIN = "body:forecast"

# 观点含数值断言的检测：数字 + 单位/百分比，用于 verification_needs。
_NUMERIC_ASSERTION_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:亿|万|万亿|亿元|万元|%|pp|个百分点|倍|元)"
)


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class ResearchClaimItem:
    """单条研报观点/事实/预测/风险记录。

    每条记录都携带任务要求的溯源字段：
    ``symbol / name / source_path / source_quality_tier / report_date / stale_status``。
    """

    claim_type: str
    text: str
    origin_field: str
    symbol: str = ""
    name: str = ""
    source_path: str = ""
    source_quality_tier: str = TIER_UNKNOWN
    report_date: Optional[str] = None
    stale_status: str = STALE_FRESH
    confidence: str = "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_type": self.claim_type,
            "text": self.text,
            "origin_field": self.origin_field,
            "symbol": self.symbol,
            "name": self.name,
            "source_path": self.source_path,
            "source_quality_tier": self.source_quality_tier,
            "report_date": self.report_date,
            "stale_status": self.stale_status,
            "confidence": self.confidence,
        }


@dataclass
class ResearchFactOpinionPage:
    """单页研报的观点/事实/预测/风险分离结果。

    所有 list 字段都是裁剪后的摘要条目，每条裁剪到 ``_CLAIM_MAX_CHARS``。
    """

    rel_path: str
    title: str
    symbols: List[str] = field(default_factory=list)
    symbol: str = ""
    name: str = ""
    report_type: str = ""
    source_quality_tier: str = TIER_UNKNOWN
    report_date: Optional[str] = None
    stale_status: str = STALE_FRESH
    confidence: str = "low"
    research_claims: List[ResearchClaimItem] = field(default_factory=list)
    reported_facts: List[ResearchClaimItem] = field(default_factory=list)
    forecast_items: List[ResearchClaimItem] = field(default_factory=list)
    risk_items: List[ResearchClaimItem] = field(default_factory=list)
    verification_needs: List[str] = field(default_factory=list)
    citation_signals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "title": self.title,
            "symbols": list(self.symbols),
            "symbol": self.symbol,
            "name": self.name,
            "report_type": self.report_type,
            "source_quality_tier": self.source_quality_tier,
            "report_date": self.report_date,
            "stale_status": self.stale_status,
            "confidence": self.confidence,
            "research_claims": [c.to_dict() for c in self.research_claims],
            "reported_facts": [c.to_dict() for c in self.reported_facts],
            "forecast_items": [c.to_dict() for c in self.forecast_items],
            "risk_items": [c.to_dict() for c in self.risk_items],
            "verification_needs": list(self.verification_needs),
            "citation_signals": list(self.citation_signals),
        }


@dataclass
class ResearchFactOpinionIndexResult:
    """整次研报观点/事实分离索引的聚合结果。"""

    status: str = STATUS_NORMAL_NO_DATA
    vendor: str = VENDOR
    task: str = TASK_CODE
    pages: List[ResearchFactOpinionPage] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    names: List[str] = field(default_factory=list)
    claim_counts: Dict[str, int] = field(default_factory=dict)
    verification_count: int = 0
    knowledge_root: str = ""
    query: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vendor": self.vendor,
            "task": self.task,
            "pages": [p.to_dict() for p in self.pages],
            "symbols": list(self.symbols),
            "names": list(self.names),
            "claim_counts": dict(self.claim_counts),
            "verification_count": self.verification_count,
            "knowledge_root": self.knowledge_root,
            "query": dict(self.query),
            "errors": list(self.errors),
        }


# ── 文本裁剪与归一化 ──────────────────────────────────────────────────


def _clip_text(text: str, max_chars: int = _CLAIM_MAX_CHARS) -> str:
    """裁剪到 max_chars，超长加省略号。"""
    text = (text or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text


def _normalize_list_field(raw: Any) -> List[str]:
    """把 frontmatter list/str 字段归一为非空字符串列表（不裁剪，由调用方裁剪）。"""
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
        if text:
            out.append(text)
    return out


def _dedup_clip(items: List[str], max_chars: int, max_entries: int) -> List[str]:
    """裁剪每条到 max_chars，去重保序，截断到 max_entries。"""
    out: List[str] = []
    for item in items:
        text = _clip_text(item, max_chars)
        if not text:
            continue
        if text not in out:
            out.append(text)
        if len(out) >= max_entries:
            break
    return out


# ── stale / confidence 判定 ───────────────────────────────────────────


def _compute_stale_status(
    *,
    frontmatter: Dict[str, Any],
    page_audit: Any,
    citation: CitationAssessment,
    today: Optional[date] = None,
) -> str:
    """计算单页 stale_status：fresh / stale / low_confidence。

    优先级：stale > low_confidence > fresh。
    - stale：``stale_risk=高`` 或 ``valid_until`` 已过期。
    - low_confidence：``evidence_level`` 为低 / 待补充 / 弱来源 tier。
    """
    stale_val = _safe_str(frontmatter.get("stale_risk")) or ""
    if stale_val in HIGH_STALE_RISK_VALUES:
        return STALE_STALE
    if page_audit is not None and getattr(page_audit, "valid_until_expired", False):
        return STALE_STALE
    valid_until = _safe_str(frontmatter.get("valid_until"))
    if valid_until:
        from tradingagents.dataflows.local_knowledge_audit import (
            _is_valid_until_expired,
        )

        if _is_valid_until_expired(valid_until, today=today):
            return STALE_STALE

    evidence = _safe_str(frontmatter.get("evidence_level")) or ""
    if evidence in LOW_CONFIDENCE_EVIDENCE_LEVELS:
        return STALE_LOW_CONFIDENCE
    if page_audit is not None and getattr(page_audit, "is_to_be_supplemented", False):
        return STALE_LOW_CONFIDENCE
    # 弱来源 tier 也标 low_confidence（不删除，只降级）。
    if citation.weak_source_risk:
        return STALE_LOW_CONFIDENCE
    return STALE_FRESH


def _compute_stale_status_from_cache(
    *,
    page: Any,
    frontmatter: Dict[str, Any],
    citation: CitationAssessment,
    today: Optional[date] = None,
) -> str:
    """缓存驱动的 stale_status 判定（与全量扫描口径一致）。"""
    if getattr(page, "stale_risk_high", False):
        return STALE_STALE
    if getattr(page, "valid_until_expired", False):
        return STALE_STALE
    valid_until = _safe_str(frontmatter.get("valid_until"))
    if valid_until:
        from tradingagents.dataflows.local_knowledge_audit import (
            _is_valid_until_expired,
        )

        if _is_valid_until_expired(valid_until, today=today):
            return STALE_STALE

    evidence = _safe_str(frontmatter.get("evidence_level")) or ""
    if evidence in LOW_CONFIDENCE_EVIDENCE_LEVELS:
        return STALE_LOW_CONFIDENCE
    if getattr(page, "is_to_be_supplemented", False):
        return STALE_LOW_CONFIDENCE
    if citation.weak_source_risk:
        return STALE_LOW_CONFIDENCE
    return STALE_FRESH


def _page_confidence(
    *,
    page_audit: Any,
    stale_status: str,
    citation: CitationAssessment,
) -> str:
    """计算单页 confidence：high/medium/low（复用 KB-003 口径 + KB-014 软降级）。"""
    if stale_status != STALE_FRESH:
        base = "low"
    else:
        readiness = getattr(page_audit, "machine_readiness", "low")
        if readiness == "high":
            base = "high"
        elif readiness == "medium":
            base = "medium"
        else:
            base = "low"
    return apply_tier_to_confidence(base, citation)


def _page_confidence_from_cache(
    *,
    page: Any,
    stale_status: str,
    citation: CitationAssessment,
) -> str:
    """缓存驱动的 confidence 判定（与全量扫描口径一致）。"""
    if stale_status != STALE_FRESH:
        base = "low"
    else:
        readiness = getattr(page, "machine_readiness", "low")
        if readiness == "high":
            base = "high"
        elif readiness == "medium":
            base = "medium"
        else:
            base = "low"
    return apply_tier_to_confidence(base, citation)


# ── claim_type 分类 ───────────────────────────────────────────────────


def _is_fact_source_tier(tier: str) -> bool:
    """来源 tier 是否可作为事实（KB-014 citation policy）。"""
    return tier in (TIER_ORIGINAL_FILING, TIER_OFFICIAL_NOTICE)


def _classify_field_claim_type(
    *,
    field_name: str,
    tier: str,
    source_type: List[str],
) -> str:
    """根据字段名 + 来源 tier/source_type 判断 claim_type。

    规则：
      - ``forward_guidance`` → forecast（无论来源，前瞻指引都是预测）。
      - ``risk_factors`` / 风险段 → risk。
      - ``financial_facts`` / ``segment_facts`` / ``management_commentary``：
        - 事实类来源（original_filing / official_notice）→ fact。
        - 观点类来源（broker/media/user_note/unknown）→ opinion（观点冒充事实）。
        - management_commentary 来自公司口径 source_type → fact（半事实）。
      - 其余 → unknown。
    """
    if field_name == "forward_guidance":
        return CLAIM_FORECAST
    if field_name in ("risk_factors", _RISK_BODY_ORIGIN):
        return CLAIM_RISK
    if field_name in ("financial_facts", "segment_facts", "source_links"):
        return CLAIM_FACT if _is_fact_source_tier(tier) else CLAIM_OPINION
    if field_name == "management_commentary":
        # management_commentary 的 source_type 显式声明时视作公司口径半事实。
        if "management_commentary" in source_type or _is_fact_source_tier(tier):
            return CLAIM_FACT
        return CLAIM_OPINION
    if field_name == _OPINION_BODY_ORIGIN:
        return CLAIM_OPINION
    if field_name == _FORECAST_BODY_ORIGIN:
        return CLAIM_FORECAST
    return CLAIM_UNKNOWN


# ── 正文段落抽取 ──────────────────────────────────────────────────────


def _extract_body_list_items(
    body: str,
    headers: Tuple[str, ...],
    max_items: int,
) -> List[str]:
    """从正文 ``## <header>`` 段提取列表项（``- xxx`` / ``* xxx`` / ``1. xxx``）。

    与 KB-003 ``_extract_list_items`` 语义一致，本模块自带一份避免循环导入。
    """
    if max_items <= 0 or not headers:
        return []
    lines = body.splitlines()
    in_section = False
    items: List[str] = []
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            header_text = line.lstrip("#").strip()
            if in_section:
                break
            if any(p in header_text for p in headers):
                in_section = True
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped:
            if items:
                break
            continue
        m = re.match(r"^(?:[-*•]|\d+[.)、])\s*(.+)$", stripped)
        if m:
            items.append(m.group(1).strip())
            if len(items) >= max_items:
                break
    return items


def _extract_body_paragraph(
    body: str,
    headers: Tuple[str, ...],
    max_chars: int,
) -> str:
    """提取正文 ``## <header>`` 段的首段文本（到下一个 ``##`` 前），裁剪到 max_chars。"""
    lines = body.splitlines()
    in_section = False
    collected: List[str] = []
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            header_text = line.lstrip("#").strip()
            if in_section:
                break
            if any(p in header_text for p in headers):
                in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if not stripped:
                if collected:
                    break
                continue
            collected.append(stripped)
            if sum(len(c) for c in collected) >= max_chars:
                break
    text = " ".join(collected).strip()
    return _clip_text(text, max_chars)


# ── 单页 claim 构建 ───────────────────────────────────────────────────


def _pick_primary_symbol_name(
    symbols: List[str],
    title: str,
    query_symbol: Optional[str],
    query_name: Optional[str],
) -> Tuple[str, str]:
    """从页面 symbols 列表中选 primary symbol/name。

    优先选与查询条件匹配的条目；否则取首条。返回 ``(symbol_bare, name)``。
    """
    if not symbols:
        return (query_symbol or "", query_name or title or "")
    # 优先命中查询 symbol/name 的条目。
    suffix_re = re.compile(r"\.(SH|SZ|BJ|HK|US|SS)$", re.IGNORECASE)
    q_bare = suffix_re.sub("", (query_symbol or "")).strip()
    for entry in symbols:
        parts = entry.split(maxsplit=1)
        code = parts[0] if parts else ""
        sname = parts[1] if len(parts) > 1 else ""
        bare = suffix_re.sub("", code).strip()
        if q_bare and bare == q_bare:
            return (bare, sname)
        if query_name and query_name.lower() in (sname or "").lower():
            return (bare, sname)
    # 取首条。
    parts = symbols[0].split(maxsplit=1)
    code = parts[0] if parts else ""
    sname = parts[1] if len(parts) > 1 else ""
    bare = suffix_re.sub("", code).strip()
    return (bare, sname or title or "")


def _build_claim(
    *,
    text: str,
    origin_field: str,
    claim_type: str,
    symbol: str,
    name: str,
    rel_path: str,
    tier: str,
    report_date: Optional[str],
    stale_status: str,
    confidence: str,
) -> ResearchClaimItem:
    """构造单条 ResearchClaimItem（文本裁剪到上限）。"""
    return ResearchClaimItem(
        claim_type=claim_type,
        text=_clip_text(text),
        origin_field=origin_field,
        symbol=symbol,
        name=name,
        source_path=rel_path,
        source_quality_tier=tier,
        report_date=report_date,
        stale_status=stale_status,
        confidence=confidence,
    )


def _build_verification_needs(
    *,
    all_claims: List[ResearchClaimItem],
    citation: CitationAssessment,
    stale_status: str,
    has_sources: bool,
    source_type: List[str],
) -> List[str]:
    """生成本页的 verification_needs 列表。

    触发条件（与任务验收「缺来源」fixture 对齐）：
      - 弱来源（unknown/media/user_note）且含数值断言的 claim → 需事实核验。
      - 无 sources / 无 source_type → 需补来源。
      - stale 页 → 需更新核验。
      - 观点含数值断言（opinion_as_fact 风险）→ 需对照公告/财报。
    """
    needs: List[str] = []
    tier = citation.tier

    # 1) 缺来源。
    if not has_sources and not source_type:
        needs.append("页面缺 sources / source_type，来源不可追溯，建议回 Tree Work 补来源。")

    # 2) stale 页需更新核验。
    if stale_status == STALE_STALE:
        needs.append("页面已过期（stale_risk=高 或 valid_until 过期），观点/事实需重新核验。")

    # 3) 弱来源 + 数值断言 → 观点冒充事实风险。
    weak_tiers = {TIER_UNKNOWN, TIER_MEDIA, TIER_USER_NOTE}
    if tier in weak_tiers:
        numeric_opinions = [
            c for c in all_claims
            if c.claim_type in (CLAIM_OPINION, CLAIM_FACT, CLAIM_FORECAST)
            and _NUMERIC_ASSERTION_RE.search(c.text)
        ]
        if numeric_opinions:
            needs.append(
                f"弱来源（{tier}）含 {len(numeric_opinions)} 条数值断言，"
                "需对照公告/财报原文核验，不得直接当作事实。"
            )

    # 4) broker_research 的 fact-origin 字段被标为 opinion → 观点冒充事实。
    fact_origin_fields = ("financial_facts", "segment_facts", "management_commentary")
    broker_fact_origin = [
        c for c in all_claims
        if c.origin_field in fact_origin_fields
        and c.claim_type == CLAIM_OPINION
        and tier == TIER_BROKER_RESEARCH
    ]
    if broker_fact_origin:
        needs.append(
            f"券商研报中有 {len(broker_fact_origin)} 条事实类字段"
            "（financial_facts/segment_facts/management_commentary）属卖方观点，"
            "需与交易所公告/定期报告交叉核验。"
        )

    return _dedup_clip(needs, _VERIFICATION_MAX_CHARS, _MAX_VERIFICATION_ENTRIES)


def _build_page_from_scan(
    rel_path: str,
    abs_path: Path,
    *,
    query_symbol: Optional[str],
    query_name: Optional[str],
    today: Optional[date] = None,
) -> Tuple[Optional[ResearchFactOpinionPage], Optional[str]]:
    """全量扫描路径：解析单页为 ResearchFactOpinionPage。

    返回 ``(page, error)``：解析异常 → ``(None, error_msg)``；正常 → ``(page, None)``。
    """
    try:
        page_audit = _audit_single_page(rel_path, abs_path)
    except Exception as exc:  # pragma: no cover - 容错
        return None, f"{rel_path}: 审计失败 {exc!r}"

    try:
        text = _read_text_safe(abs_path)
        fm_text, body = _split_frontmatter(text)
        frontmatter = _parse_frontmatter(fm_text)
    except Exception as exc:  # pragma: no cover
        return None, f"{rel_path}: 解析失败 {exc!r}"

    return _assemble_page(
        rel_path=rel_path,
        title=page_audit.title or abs_path.stem,
        frontmatter=frontmatter,
        body=body or "",
        page_audit=page_audit,
        query_symbol=query_symbol,
        query_name=query_name,
        today=today,
    )


def _build_page_from_cache(
    page: Any,
    *,
    query_symbol: Optional[str],
    query_name: Optional[str],
    today: Optional[date] = None,
) -> Optional[ResearchFactOpinionPage]:
    """缓存路径：从 CachedPageData 构造 ResearchFactOpinionPage（不重新读文件）。

    与 :func:`_build_page_from_scan` 语义等价。
    """
    frontmatter = page.frontmatter if isinstance(page.frontmatter, dict) else {}
    # 缓存中没有完整 body，用 page.summary / page.risks 等预抽取字段替代正文段落。
    # 构造一个"伪 body"让正文抽取函数能从预抽取字段工作——但更直接的方式是
    # 在 _assemble_page 中区分缓存/非缓存来源。这里用预抽取字段直接拼装。
    return _assemble_page_from_cache(
        page=page,
        frontmatter=frontmatter,
        query_symbol=query_symbol,
        query_name=query_name,
        today=today,
    )


def _assemble_page(
    *,
    rel_path: str,
    title: str,
    frontmatter: Dict[str, Any],
    body: str,
    page_audit: Any,
    query_symbol: Optional[str],
    query_name: Optional[str],
    today: Optional[date],
) -> Tuple[Optional[ResearchFactOpinionPage], Optional[str]]:
    """从已解析的 frontmatter + body 组装 ResearchFactOpinionPage（全量扫描路径）。"""
    citation = classify_source_quality_tier(frontmatter)
    source_type = _normalize_source_type_list(frontmatter.get("source_type"))
    symbols = _normalize_symbol_list(frontmatter.get("symbols"))
    symbol, name = _pick_primary_symbol_name(
        symbols, title, query_symbol, query_name
    )

    stale_status = _compute_stale_status(
        frontmatter=frontmatter,
        page_audit=page_audit,
        citation=citation,
        today=today,
    )
    confidence = _page_confidence(
        page_audit=page_audit, stale_status=stale_status, citation=citation
    )
    report_date = _safe_str(frontmatter.get("updated")) or _safe_str(
        frontmatter.get("disclosure_date")
    )
    report_type = _safe_str(frontmatter.get("report_type")) or ""

    tier = citation.tier
    page_obj = ResearchFactOpinionPage(
        rel_path=rel_path,
        title=title,
        symbols=symbols,
        symbol=symbol,
        name=name,
        report_type=report_type,
        source_quality_tier=tier,
        report_date=report_date,
        stale_status=stale_status,
        confidence=confidence,
        citation_signals=list(citation.signals or []),
    )

    # ── 从 frontmatter 字段抽取 claims ──
    has_sources_field = bool(
        _normalize_list_field(frontmatter.get("sources"))
    )

    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("financial_facts")),
        field_name="financial_facts",
        tier=tier,
        source_type=source_type,
        category="reported_facts",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )
    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("segment_facts")),
        field_name="segment_facts",
        tier=tier,
        source_type=source_type,
        category="reported_facts",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )
    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("management_commentary")),
        field_name="management_commentary",
        tier=tier,
        source_type=source_type,
        category="reported_facts",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )
    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("forward_guidance")),
        field_name="forward_guidance",
        tier=tier,
        source_type=source_type,
        category="forecast_items",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )
    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("risk_factors")),
        field_name="risk_factors",
        tier=tier,
        source_type=source_type,
        category="risk_items",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )

    # ── 从正文段落抽取 claims ──
    opinion_items = _extract_body_list_items(body, _OPINION_HEADERS, _MAX_CLAIMS_PER_CATEGORY)
    if not opinion_items:
        para = _extract_body_paragraph(body, _OPINION_HEADERS, _CLAIM_MAX_CHARS)
        if para:
            opinion_items = [para]
    for item in opinion_items:
        claim_type = _classify_field_claim_type(
            field_name=_OPINION_BODY_ORIGIN, tier=tier, source_type=source_type
        )
        page_obj.research_claims.append(
            _build_claim(
                text=item,
                origin_field=_OPINION_BODY_ORIGIN,
                claim_type=claim_type,
                symbol=symbol,
                name=name,
                rel_path=rel_path,
                tier=tier,
                report_date=report_date,
                stale_status=stale_status,
                confidence=confidence,
            )
        )

    risk_body_items = _extract_body_list_items(body, _RISK_HEADERS, _MAX_CLAIMS_PER_CATEGORY)
    for item in risk_body_items:
        page_obj.risk_items.append(
            _build_claim(
                text=item,
                origin_field=_RISK_BODY_ORIGIN,
                claim_type=CLAIM_RISK,
                symbol=symbol,
                name=name,
                rel_path=rel_path,
                tier=tier,
                report_date=report_date,
                stale_status=stale_status,
                confidence=confidence,
            )
        )

    forecast_body_items = _extract_body_list_items(
        body, _FORECAST_HEADERS, _MAX_CLAIMS_PER_CATEGORY
    )
    for item in forecast_body_items:
        page_obj.forecast_items.append(
            _build_claim(
                text=item,
                origin_field=_FORECAST_BODY_ORIGIN,
                claim_type=CLAIM_FORECAST,
                symbol=symbol,
                name=name,
                rel_path=rel_path,
                tier=tier,
                report_date=report_date,
                stale_status=stale_status,
                confidence=confidence,
            )
        )

    # ── verification_needs ──
    all_claims = (
        page_obj.research_claims
        + page_obj.reported_facts
        + page_obj.forecast_items
        + page_obj.risk_items
    )
    page_obj.verification_needs = _build_verification_needs(
        all_claims=all_claims,
        citation=citation,
        stale_status=stale_status,
        has_sources=has_sources_field,
        source_type=source_type,
    )

    return page_obj, None


def _assemble_page_from_cache(
    *,
    page: Any,
    frontmatter: Dict[str, Any],
    query_symbol: Optional[str],
    query_name: Optional[str],
    today: Optional[date],
) -> Optional[ResearchFactOpinionPage]:
    """缓存路径：从 CachedPageData 组装 ResearchFactOpinionPage。

    与 :func:`_assemble_page` 语义等价，但用预抽取字段（page.summary / page.risks）
    替代正文段落抽取。
    """
    citation = classify_source_quality_tier(frontmatter)
    source_type = _normalize_source_type_list(frontmatter.get("source_type"))
    symbols = list(page.symbols or [])
    symbol, name = _pick_primary_symbol_name(
        symbols, page.title, query_symbol, query_name
    )

    stale_status = _compute_stale_status_from_cache(
        page=page, frontmatter=frontmatter, citation=citation, today=today
    )
    confidence = _page_confidence_from_cache(
        page=page, stale_status=stale_status, citation=citation
    )
    report_date = _safe_str(frontmatter.get("updated")) or _safe_str(
        frontmatter.get("disclosure_date")
    )
    report_type = _safe_str(frontmatter.get("report_type")) or ""
    tier = citation.tier
    has_sources_field = bool(list(page.sources or []))

    page_obj = ResearchFactOpinionPage(
        rel_path=page.rel_path,
        title=page.title,
        symbols=symbols,
        symbol=symbol,
        name=name,
        report_type=report_type,
        source_quality_tier=tier,
        report_date=report_date,
        stale_status=stale_status,
        confidence=confidence,
        citation_signals=list(citation.signals or []),
    )

    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("financial_facts")),
        field_name="financial_facts",
        tier=tier,
        source_type=source_type,
        category="reported_facts",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )
    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("segment_facts")),
        field_name="segment_facts",
        tier=tier,
        source_type=source_type,
        category="reported_facts",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )
    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("management_commentary")),
        field_name="management_commentary",
        tier=tier,
        source_type=source_type,
        category="reported_facts",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )
    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("forward_guidance")),
        field_name="forward_guidance",
        tier=tier,
        source_type=source_type,
        category="forecast_items",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )
    _add_claims_from_field(
        page_obj,
        items_raw=_normalize_list_field(frontmatter.get("risk_factors")),
        field_name="risk_factors",
        tier=tier,
        source_type=source_type,
        category="risk_items",
        max_entries=_MAX_CLAIMS_PER_CATEGORY,
    )

    # 缓存路径：用预抽取的 summary（观点段）和 risks（风险段）作为 body claims 来源。
    if page.summary:
        page_obj.research_claims.append(
            _build_claim(
                text=page.summary,
                origin_field=_OPINION_BODY_ORIGIN,
                claim_type=CLAIM_OPINION,
                symbol=symbol,
                name=name,
                rel_path=page.rel_path,
                tier=tier,
                report_date=report_date,
                stale_status=stale_status,
                confidence=confidence,
            )
        )
    for risk_text in list(page.risks or [])[:_MAX_CLAIMS_PER_CATEGORY]:
        page_obj.risk_items.append(
            _build_claim(
                text=risk_text,
                origin_field=_RISK_BODY_ORIGIN,
                claim_type=CLAIM_RISK,
                symbol=symbol,
                name=name,
                rel_path=page.rel_path,
                tier=tier,
                report_date=report_date,
                stale_status=stale_status,
                confidence=confidence,
            )
        )

    all_claims = (
        page_obj.research_claims
        + page_obj.reported_facts
        + page_obj.forecast_items
        + page_obj.risk_items
    )
    page_obj.verification_needs = _build_verification_needs(
        all_claims=all_claims,
        citation=citation,
        stale_status=stale_status,
        has_sources=has_sources_field,
        source_type=source_type,
    )

    return page_obj


def _add_claims_from_field(
    page_obj: ResearchFactOpinionPage,
    *,
    items_raw: List[str],
    field_name: str,
    tier: str,
    source_type: List[str],
    category: str,
    max_entries: int,
) -> None:
    """从 frontmatter 字段抽取 claims，分类后追加到 page_obj 对应类别列表。

    根据 field_name + tier 决定 claim_type，再按 claim_type 分流到
    research_claims / reported_facts / forecast_items / risk_items。
    """
    if not items_raw:
        return
    items_clipped = _dedup_clip(items_raw, _CLAIM_MAX_CHARS, max_entries)
    for text in items_clipped:
        claim_type = _classify_field_claim_type(
            field_name=field_name, tier=tier, source_type=source_type
        )
        claim = _build_claim(
            text=text,
            origin_field=field_name,
            claim_type=claim_type,
            symbol=page_obj.symbol,
            name=page_obj.name,
            rel_path=page_obj.rel_path,
            tier=tier,
            report_date=page_obj.report_date,
            stale_status=page_obj.stale_status,
            confidence=page_obj.confidence,
        )
        # 分流：forward_guidance → forecast_items；risk_factors → risk_items；
        # 其余 fact/opinion 按 claim_type 与 category 双重判定。
        if category == "forecast_items":
            page_obj.forecast_items.append(claim)
        elif category == "risk_items":
            page_obj.risk_items.append(claim)
        elif claim_type == CLAIM_FACT:
            page_obj.reported_facts.append(claim)
        else:
            # opinion / unknown → research_claims。
            page_obj.research_claims.append(claim)


# ── 查询命中判定 ──────────────────────────────────────────────────────


def _query_matches_page(
    symbol: Optional[str],
    name: Optional[str],
    page_symbols: List[str],
    title: str,
) -> bool:
    """查询命中判定：symbol 或 name 任一命中即可。无查询条件时命中全部（索引模式）。"""
    if not symbol and not name:
        return True
    if symbol and _symbol_matches(symbol, page_symbols):
        return True
    if name:
        q = name.lower()
        if q and q in (title or "").lower():
            return True
        for entry in page_symbols:
            parts = entry.split(maxsplit=1)
            sname = parts[1] if len(parts) > 1 else ""
            if sname and q in sname.lower():
                return True
    return False


# ── 主索引构建逻辑 ────────────────────────────────────────────────────


def build_research_fact_opinion_index(
    knowledge_root: str,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    cache: Any = None,
    today: Optional[date] = None,
    max_pages: int = _MAX_PAGES,
) -> ResearchFactOpinionIndexResult:
    """构建研报观点/事实分离索引。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        symbol: A 股代码或代码+后缀（如 ``603296`` / ``603296.SH``）。
            ``None`` 时不限 symbol（索引模式，扫描全部 investment 页）。
        name: 公司简称（与页面 symbols 简称或 title 子串匹配）。
        cache: [KB-010] 可选的 :class:`KnowledgeCache`。传入时跳过全量扫描，
            直接基于缓存做 claim 抽取（语义与全量扫描等价）。``None`` 时走全量扫描。
        today: 用于 stale 判定的基准日期（测试注入）；``None`` 用 ``date.today()``。
        max_pages: 最多返回的命中页数。

    返回:
        :class:`ResearchFactOpinionIndexResult`。永远不会因单页解析失败而抛异常：
        单页失败记入 ``errors`` 并跳过；知识库不存在时返回 status=FAILED。
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
    result = ResearchFactOpinionIndexResult(
        knowledge_root=str(root),
        query=query_desc,
    )

    if not root.exists():
        result.status = STATUS_FAILED
        result.errors.append(f"knowledge_root 不存在: {root}")
        return result

    pages: List[ResearchFactOpinionPage] = []

    if cache is not None:
        # [KB-010] 缓存路径。
        result.errors.extend(cache.errors or [])
        for _rel, cached_page in cache.pages.items():
            try:
                page_symbols = list(cached_page.symbols or [])
                if not _query_matches_page(
                    symbol, name, page_symbols, cached_page.title
                ):
                    continue
                rfo_page = _build_page_from_cache(
                    cached_page,
                    query_symbol=symbol,
                    query_name=name,
                    today=today,
                )
            except Exception as exc:  # pragma: no cover - 容错
                result.errors.append(f"{_rel}: 缓存 claim 抽取失败 {exc!r}")
                continue
            if rfo_page is None:
                continue
            pages.append(rfo_page)
    else:
        # 全量扫描路径。
        investment_dir = root / INVESTMENT_SUBDIR
        if not investment_dir.exists():
            result.errors.append(f"investment 分区不存在: {investment_dir}")
            result.status = STATUS_NORMAL_NO_DATA
            return result
        md_files = _iter_markdown_files(investment_dir)
        for md in md_files:
            rel = str(md.relative_to(root))
            # 先读 frontmatter 做 symbol/name 预筛，避免对无关页跑完整 audit。
            try:
                text = _read_text_safe(md)
                fm_text, _body = _split_frontmatter(text)
                frontmatter = _parse_frontmatter(fm_text)
            except Exception as exc:  # pragma: no cover
                result.errors.append(f"{rel}: 解析失败 {exc!r}")
                continue

            page_symbols = _normalize_symbol_list(frontmatter.get("symbols"))
            title = _safe_str(frontmatter.get("title")) or md.stem
            if not _query_matches_page(symbol, name, page_symbols, title):
                continue

            try:
                rfo_page, err = _build_page_from_scan(
                    rel,
                    md,
                    query_symbol=symbol,
                    query_name=name,
                    today=today,
                )
            except Exception as exc:  # pragma: no cover
                result.errors.append(f"{rel}: claim 抽取失败 {exc!r}")
                continue
            if err:
                result.errors.append(err)
                continue
            if rfo_page is None:
                continue
            pages.append(rfo_page)

    # 排序：fresh 优先，其次 stale，最后 low_confidence；同优先级按 rel_path。
    pages.sort(key=_page_rank_key)
    if len(pages) > max_pages:
        pages = pages[:max_pages]
    result.pages = pages

    _aggregate_result(result)
    return result


def _page_rank_key(page: ResearchFactOpinionPage) -> Tuple[int, str]:
    """排序键：(stale_status_rank, rel_path)。

    fresh → stale → low_confidence；同优先级按 rel_path。
    """
    status_rank = {
        STALE_FRESH: 0,
        STALE_STALE: 1,
        STALE_LOW_CONFIDENCE: 2,
    }.get(page.stale_status, 9)
    return (status_rank, page.rel_path)


def _aggregate_result(result: ResearchFactOpinionIndexResult) -> None:
    """聚合 pages → status / symbols / names / claim_counts / verification_count。"""
    pages = result.pages
    if not pages:
        result.status = STATUS_NORMAL_NO_DATA
        return

    symbols_seen: List[str] = []
    names_seen: List[str] = []
    claim_counts: Dict[str, int] = {ct: 0 for ct in CLAIM_TYPES}
    verification_total = 0
    for p in pages:
        for s in p.symbols:
            if s not in symbols_seen:
                symbols_seen.append(s)
        if p.name and p.name not in names_seen:
            names_seen.append(p.name)
        for c in (
            p.research_claims + p.reported_facts + p.forecast_items + p.risk_items
        ):
            ct = c.claim_type
            if ct in claim_counts:
                claim_counts[ct] += 1
            else:
                claim_counts[CLAIM_UNKNOWN] += 1
        verification_total += len(p.verification_needs)

    result.symbols = symbols_seen[:20]
    result.names = names_seen[:20]
    result.claim_counts = claim_counts
    result.verification_count = verification_total

    # 状态机。
    has_fresh = any(p.stale_status == STALE_FRESH for p in pages)
    has_only_stale = all(p.stale_status == STALE_STALE for p in pages)
    has_only_low = all(
        p.stale_status == STALE_LOW_CONFIDENCE for p in pages
    )
    if has_fresh:
        result.status = STATUS_HAS_DATA
    elif has_only_stale:
        result.status = STATUS_STALE
    elif has_only_low:
        result.status = STATUS_LOW_CONFIDENCE
    else:
        # 混合 stale + low，按低置信处理。
        result.status = STATUS_LOW_CONFIDENCE


# ── 报告渲染 ──────────────────────────────────────────────────────────


_STATUS_LABELS = {
    STATUS_HAS_DATA: "已命中研报观点/事实索引",
    STATUS_NORMAL_NO_DATA: "无研报观点/事实命中",
    STATUS_FAILED: "研报观点/事实索引构建失败",
    STATUS_STALE: "研报观点/事实已过期（仅供参考）",
    STATUS_LOW_CONFIDENCE: "研报观点/事实低置信（仅供参考）",
}

_CLAIM_TYPE_LABELS = {
    CLAIM_FACT: "事实",
    CLAIM_OPINION: "观点",
    CLAIM_FORECAST: "预测",
    CLAIM_RISK: "风险",
    CLAIM_UNKNOWN: "未知",
}


def render_research_fact_opinion_report(
    result: ResearchFactOpinionIndexResult,
) -> str:
    """渲染完整的研报观点/事实分离索引 Markdown 报告。

    只展示摘要和路径，不展示长正文。状态为 NORMAL_NO_DATA / FAILED 时返回
    最小化报告（不抛异常）。
    """
    lines: List[str] = []
    lines.append(f"# 研报观点/事实分离索引报告 — {TASK_CODE}")
    lines.append("")
    lines.append(f"- 知识库：`{result.knowledge_root}`")
    lines.append(f"- 查询：`{result.query}`")
    status_label = _STATUS_LABELS.get(result.status, result.status)
    lines.append(f"- 状态：{result.status}（{status_label}）")
    lines.append(f"- 命中页数：{len(result.pages)}")
    if result.claim_counts:
        counts_str = ", ".join(
            f"{_CLAIM_TYPE_LABELS.get(k, k)}={v}"
            for k, v in result.claim_counts.items()
            if v
        )
        lines.append(f"- claim 统计：{counts_str or '（无）'}")
    lines.append(f"- verification_needs：{result.verification_count} 条")
    if result.errors:
        lines.append(f"- 错误：{'; '.join(result.errors[:5])}")
    lines.append("")
    lines.append(
        "> 本索引把 Tree Work 已消化研报中的观点/事实/预测/风险拆成 TA 可消费的"
        "结构化摘要。券商/媒体观点不会被误当成公告/财报事实；弱来源/过期来源"
        "只降置信，不覆盖动作语义。"
    )
    lines.append("")

    if result.status in (STATUS_NORMAL_NO_DATA, STATUS_FAILED):
        lines.append("（无研报观点/事实命中）")
        return "\n".join(lines)

    for idx, p in enumerate(result.pages[:_MAX_PAGES], 1):
        stale_label = {
            STALE_FRESH: "fresh",
            STALE_STALE: "stale",
            STALE_LOW_CONFIDENCE: "low_confidence",
        }.get(p.stale_status, p.stale_status)
        lines.append(
            f"## {idx}. {p.title}"
        )
        lines.append("")
        lines.append(
            f"- 路径：`{p.rel_path}`"
        )
        lines.append(
            f"- 标的：{', '.join(p.symbols[:5]) or '(未标注)'}"
            f"（primary：{p.symbol if p.symbol else ''}"
            f"{f' {p.name}' if p.name else ''}）"
        )
        lines.append(
            f"- 来源层级：`{p.source_quality_tier}` · "
            f"stale_status: {stale_label} · confidence: {p.confidence}"
        )
        if p.report_date:
            lines.append(f"- 报告日期：{p.report_date}")
        if p.report_type:
            lines.append(f"- report_type：{p.report_type}")
        lines.append("")

        if p.reported_facts:
            lines.append("### 事实（reported_facts）")
            lines.append("")
            for c in p.reported_facts[:_MAX_CLAIMS_PER_CATEGORY]:
                lines.append(f"- [{c.origin_field}] {c.text}")
            lines.append("")

        if p.research_claims:
            lines.append("### 观点（research_claims）")
            lines.append("")
            for c in p.research_claims[:_MAX_CLAIMS_PER_CATEGORY]:
                lines.append(f"- [{c.origin_field}] {c.text}")
            lines.append("")

        if p.forecast_items:
            lines.append("### 预测（forecast_items）")
            lines.append("")
            for c in p.forecast_items[:_MAX_CLAIMS_PER_CATEGORY]:
                lines.append(f"- [{c.origin_field}] {c.text}")
            lines.append("")

        if p.risk_items:
            lines.append("### 风险（risk_items）")
            lines.append("")
            for c in p.risk_items[:_MAX_CLAIMS_PER_CATEGORY]:
                lines.append(f"- [{c.origin_field}] {c.text}")
            lines.append("")

        if p.verification_needs:
            lines.append("### 待验证（verification_needs）")
            lines.append("")
            for v in p.verification_needs[:_MAX_VERIFICATION_ENTRIES]:
                lines.append(f"- ⚠️ {v}")
            lines.append("")

    return "\n".join(lines)


# ── 便利函数 ──────────────────────────────────────────────────────────


def suggest_report_output_path(
    docs_dir: str = "docs/knowledge_reports",
) -> str:
    """建议的报告输出路径：``docs/knowledge_reports/research_fact_opinion_index-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"research_fact_opinion_index-{today}.md")


def to_ta_consumable_summary(
    result: ResearchFactOpinionIndexResult,
) -> Dict[str, Any]:
    """把索引结果转为 TA/TradeFlow 后续任务可复用的扁平摘要字典。

    输出结构与 KB-004 ``compute_local_knowledge_score`` 风格一致，便于下游
    直接消费。不携带长正文，只携带统计 + 摘要 + 路径。

    返回字段：
      - ``status`` / ``claim_counts`` / ``verification_count``
      - ``has_fact``：是否有 fact 类 claim（可作为事实反证候选）。
      - ``has_opinion_only``：是否仅命中观点（不进入事实反证）。
      - ``symbols`` / ``names``
      - ``pages_brief``：精简列表（rel_path / title / tier / stale_status /
        claim_counts / top_verification_needs）。
    """
    if not isinstance(result, ResearchFactOpinionIndexResult):
        return {
            "status": STATUS_NORMAL_NO_DATA,
            "claim_counts": {ct: 0 for ct in CLAIM_TYPES},
            "verification_count": 0,
            "has_fact": False,
            "has_opinion_only": False,
            "symbols": [],
            "names": [],
            "pages_brief": [],
        }

    has_fact = any(
        p.reported_facts and p.source_quality_tier in (
            TIER_ORIGINAL_FILING, TIER_OFFICIAL_NOTICE,
        )
        for p in result.pages
    )
    has_opinion_only = bool(result.pages) and not has_fact

    # 确保所有 claim_type 都在 claim_counts 中（空结果兜底）。
    claim_counts = {ct: 0 for ct in CLAIM_TYPES}
    claim_counts.update(result.claim_counts or {})

    pages_brief: List[Dict[str, Any]] = []
    for p in result.pages[:_MAX_PAGES]:
        pages_brief.append({
            "rel_path": p.rel_path,
            "title": p.title,
            "source_quality_tier": p.source_quality_tier,
            "stale_status": p.stale_status,
            "confidence": p.confidence,
            "report_date": p.report_date,
            "claim_counts": {
                "fact": len(p.reported_facts),
                "opinion": len(p.research_claims),
                "forecast": len(p.forecast_items),
                "risk": len(p.risk_items),
            },
            "top_verification_needs": list(
                p.verification_needs[:3]
            ),
        })

    return {
        "status": result.status,
        "claim_counts": claim_counts,
        "verification_count": result.verification_count,
        "has_fact": has_fact,
        "has_opinion_only": has_opinion_only,
        "symbols": list(result.symbols),
        "names": list(result.names),
        "pages_brief": pages_brief,
    }


__all__ = [
    "VENDOR",
    "TASK_CODE",
    "CLAIM_FACT",
    "CLAIM_OPINION",
    "CLAIM_FORECAST",
    "CLAIM_RISK",
    "CLAIM_UNKNOWN",
    "CLAIM_TYPES",
    "STALE_FRESH",
    "STALE_STALE",
    "STALE_LOW_CONFIDENCE",
    "ResearchClaimItem",
    "ResearchFactOpinionPage",
    "ResearchFactOpinionIndexResult",
    "build_research_fact_opinion_index",
    "render_research_fact_opinion_report",
    "suggest_report_output_path",
    "to_ta_consumable_summary",
]
