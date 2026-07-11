# [HY-002] half_year_task_pack
"""半年报资料优先队列与 Tree Work 补录任务包。

在 HY-001 半年报契约 lint、KB-002 通用 lint、KB-007 研究关注度之上，把**持仓 /
观察仓 / 昊天候选 / TradeFlow 候选 / 研报关注度高但知识过期**五类输入合并为一份
按优先级排序的半年报补录任务包，导出为 Markdown / JSON，方便 OpenClaw / Tree Work
直接复制执行。

设计约束（对应任务 HY-002）：
  - **只读**：只读 ``~/Documents/knowledge/`` 与传入的持仓/观察仓/候选列表，绝不
    向知识库写文件，不修改任何 knowledge 文件。
  - **不抓取付费研报正文**：只生成"需要补录什么资料"的任务，不复制研报原文段落。
  - **不输出交易建议**：任务包面向 Tree Work ingest 流程，按"缺什么 HY-001 字段"
    分组，给出字段要求与建议来源类型，不给买卖动作或强动作词。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 HY-001 / KB-002 / KB-007 只读
    解析能力。
  - **可空库运行**：知识库缺失或空库时返回稳定空结构，绝不抛异常。
  - **对已有 wiki 页输出"补字段"而不是重复新建**。

优先级排序（与任务描述对齐）::
    P1_HOLDINGS      >  已持仓
    P2_OBSERVATION   >  观察仓
    P3_HAOTIAN       >  昊天主候选
    P4_TRADEFLOW     >  TradeFlow 主候选
    P5_STALE_ATTENTION > 研报关注度高但知识过期

每条 :class:`HalfYearTaskItem` 必含字段：
  - ``symbol``：标的代码（bare code，如 ``603296``）。
  - ``name``：标的简称（如 ``华勤技术``）。
  - ``priority_tier``：上述优先级枚举。
  - ``reason``：为什么需要补录（简短，≤2 句）。
  - ``missing_fields``：缺哪些 HY-001 字段（如 ``["financial_period", "financial_facts"]``）。
  - ``suggested_source_type``：建议来源类型（如 ``["exchange_filing", "fact_table"]``）。
  - ``existing_page``：已有 wiki 页路径（``None`` 表示需新建）。
  - ``action``：``add_fields``（已有页补字段）或 ``ingest_new``（新建半年报页）。

使用示例::

    from tradingagents.dataflows.half_year_task_pack import (
        build_half_year_task_pack,
        render_half_year_task_pack_report,
    )
    pack = build_half_year_task_pack(
        "/Users/maybee/Documents/knowledge",
        holdings=[{"symbol": "603296", "name": "华勤技术"}],
        observation=[{"symbol": "000977", "name": "浪潮信息"}],
    )
    print(render_half_year_task_pack_report(pack))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tradingagents.dataflows.local_knowledge_audit import (
    INVESTMENT_SUBDIR,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
    default_knowledge_root,
)
from tradingagents.dataflows.local_knowledge_lint import (
    HALF_YEAR_REPORT_TYPES,
    KnowledgeLintResult,
    PageLintResult,
    lint_local_knowledge,
)
from tradingagents.dataflows.local_knowledge_provider import (
    _normalize_symbol_list,
    _split_symbol_entry,
)
from tradingagents.dataflows.research_attention import (
    ResearchAttentionResult,
    SymbolAttention,
    compute_research_attention,
)


# ── 常量 ──────────────────────────────────────────────────────────────

CONTRACT_VERSION = "hy-002-v1"
TASK_CODE = "HY-002"

# 优先级层级（数值越小越靠前）。
TIER_HOLDINGS = "P1_HOLDINGS"
TIER_OBSERVATION = "P2_OBSERVATION"
TIER_HAOTIAN = "P3_HAOTIAN"
TIER_TRADEFLOW = "P4_TRADEFLOW"
TIER_STALE_ATTENTION = "P5_STALE_ATTENTION"

TIER_ORDER: Tuple[str, ...] = (
    TIER_HOLDINGS,
    TIER_OBSERVATION,
    TIER_HAOTIAN,
    TIER_TRADEFLOW,
    TIER_STALE_ATTENTION,
)

TIER_TITLES: Dict[str, str] = {
    TIER_HOLDINGS: "已持仓（半年报补录最高优先）",
    TIER_OBSERVATION: "观察仓（半年报补录次优先）",
    TIER_HAOTIAN: "昊天主候选",
    TIER_TRADEFLOW: "TradeFlow 主候选",
    TIER_STALE_ATTENTION: "研报关注度高但知识过期",
}

# 动作枚举。
ACTION_ADD_FIELDS = "add_fields"
ACTION_INGEST_NEW = "ingest_new"

# 默认建议来源类型（优先交易所公告 + 事实表）。
DEFAULT_SUGGESTED_SOURCE_TYPE: List[str] = ["exchange_filing", "fact_table"]

# HY-001 半年报扩展字段（与 docs/local_knowledge_contract.md §10 同源）。
HYF_FIELDS: Tuple[str, ...] = (
    "financial_period",
    "disclosure_date",
    "source_type",
    "financial_facts",
    "segment_facts",
    "management_commentary",
    "forward_guidance",
    "risk_factors",
    "source_links",
)

# HYF rule_id → 缺失字段名映射（用于从 lint findings 提取缺失字段）。
_HYF_RULE_TO_FIELD: Dict[str, str] = {
    "HYF-001": "financial_period",
    "HYF-003": "disclosure_date",
    "HYF-004": "source_type",
    "HYF-005": "financial_facts",
    "HYF-006": "risk_factors",
}

# 研报关注度"高"阈值（复用 KB-012 hot_but_thin 阈值）。
_STALE_ATTENTION_SCORE_THRESHOLD = 1.5

# 去后缀正则（复用 local_knowledge_provider 同款）。
_SUFFIX_RE = re.compile(r"\.(SH|SZ|BJ|HK|US)$", re.IGNORECASE)


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class HalfYearTaskItem:
    """一条半年报补录任务。"""

    symbol: str
    name: str
    priority_tier: str
    reason: str = ""
    missing_fields: List[str] = field(default_factory=list)
    suggested_source_type: Optional[List[str]] = None
    existing_page: Optional[str] = None
    action: str = ACTION_INGEST_NEW
    sources: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.suggested_source_type is None:
            self.suggested_source_type = list(DEFAULT_SUGGESTED_SOURCE_TYPE)

    @property
    def sort_key(self) -> Tuple[int, str]:
        tier_idx = {
            TIER_HOLDINGS: 0,
            TIER_OBSERVATION: 1,
            TIER_HAOTIAN: 2,
            TIER_TRADEFLOW: 3,
            TIER_STALE_ATTENTION: 4,
        }
        return (tier_idx.get(self.priority_tier, 9), self.symbol)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "priority_tier": self.priority_tier,
            "reason": self.reason,
            "missing_fields": list(self.missing_fields),
            "suggested_source_type": list(self.suggested_source_type or []),
            "existing_page": self.existing_page,
            "action": self.action,
            "sources": list(self.sources),
            "extra": dict(self.extra),
        }


@dataclass
class HalfYearTaskPack:
    """半年报补录任务包聚合。"""

    knowledge_root: str
    generated_at: str
    as_of_date: str
    contract_version: str = CONTRACT_VERSION
    task: str = TASK_CODE
    items_by_tier: Dict[str, List[HalfYearTaskItem]] = field(default_factory=dict)
    upstream_summary: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def all_items(self) -> List[HalfYearTaskItem]:
        """按 TIER_ORDER → symbol 排序返回所有任务项。"""
        items: List[HalfYearTaskItem] = []
        for tier in TIER_ORDER:
            items.extend(self.items_by_tier.get(tier, []))
        items.sort(key=lambda it: it.sort_key)
        return items

    def tier_counts(self) -> Dict[str, int]:
        return {tier: len(self.items_by_tier.get(tier, [])) for tier in TIER_ORDER}

    def total(self) -> int:
        return sum(len(items) for items in self.items_by_tier.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_root": self.knowledge_root,
            "generated_at": self.generated_at,
            "as_of_date": self.as_of_date,
            "contract_version": self.contract_version,
            "task": self.task,
            "items_by_tier": {
                tier: [i.to_dict() for i in self.items_by_tier.get(tier, [])]
                for tier in TIER_ORDER
            },
            "tier_counts": self.tier_counts(),
            "total": self.total(),
            "upstream_summary": dict(self.upstream_summary),
            "errors": list(self.errors),
        }


# ── 符号归一化 ────────────────────────────────────────────────────────


def _bare_code(symbol: str) -> str:
    """``603296.SH`` → ``603296``；``603296`` → ``603296``。"""
    if not symbol:
        return ""
    return _SUFFIX_RE.sub("", symbol.strip()).strip()


def _symbols_match(query: str, page_symbol_entry: str) -> bool:
    """查询 symbol 是否匹配页面 symbols 列表中的某一项。

    - bare code 精确匹配（``603296`` == ``603296``）。
    - 全代码大小写不敏感（``603296.SH`` == ``603296.sh``）。
    - 简称子串（``华勤技术`` ∈ ``603296.SH 华勤技术``）。
    """
    if not query or not page_symbol_entry:
        return False
    q = query.strip()
    q_bare = _bare_code(q)
    q_lower = q.lower()
    entry_code, entry_name = _split_symbol_entry(page_symbol_entry)
    entry_bare = _bare_code(entry_code)
    if q_bare and entry_bare and q_bare == entry_bare:
        return True
    if entry_code and entry_code.lower() == q_lower:
        return True
    if entry_name and q_lower and q_lower in entry_name.lower():
        return True
    return False


# ── 知识库 symbol → page 索引 ─────────────────────────────────────────


@dataclass
class _PageHYInfo:
    """单页半年报视角信息（只读）。"""

    rel_path: str
    symbols: List[str]
    is_half_year: bool
    half_year_period: Optional[str]
    is_stale: bool
    missing_hyf_fields: List[str]
    has_financial_facts: bool


def _extract_hyf_missing_fields(page: PageLintResult) -> List[str]:
    """从 lint findings 中提取 HYF 规则检测到的缺失字段。"""
    missing: List[str] = []
    for finding in page.findings:
        field = _HYF_RULE_TO_FIELD.get(finding.rule_id)
        if field and field not in missing:
            missing.append(field)
    return missing


def _scan_page_hy_info(
    page: PageLintResult, frontmatter: Dict[str, Any]
) -> _PageHYInfo:
    """从 lint 结果 + frontmatter 构建单页半年报信息。"""
    symbols = _normalize_symbol_list(frontmatter.get("symbols"))
    missing = _extract_hyf_missing_fields(page)

    # financial_facts 可能通过 frontmatter 直接判断是否非空。
    ff = frontmatter.get("financial_facts")
    has_ff = bool(ff) if ff is not None else "financial_facts" not in missing

    return _PageHYInfo(
        rel_path=page.rel_path,
        symbols=symbols,
        is_half_year=page.is_half_year_report,
        half_year_period=page.half_year_period,
        is_stale=page.is_stale,
        missing_hyf_fields=missing,
        has_financial_facts=has_ff,
    )


def _build_symbol_page_index(
    lint_result: Optional[KnowledgeLintResult],
    knowledge_root: Path,
) -> Dict[str, List[_PageHYInfo]]:
    """构建 bare_code → pages 索引（只读）。

    如果 lint_result 为 None（上游失败），回退到直接扫描 investment 目录。
    """
    index: Dict[str, List[_PageHYInfo]] = {}

    if lint_result is not None:
        for page in lint_result.page_results:
            abs_path = knowledge_root / page.rel_path
            fm_text, _body = _split_frontmatter(_read_text_safe(abs_path))
            frontmatter = _parse_frontmatter(fm_text)
            info = _scan_page_hy_info(page, frontmatter)
            for entry in info.symbols:
                code, _name = _split_symbol_entry(entry)
                bare = _bare_code(code)
                if bare:
                    index.setdefault(bare, []).append(info)
        return index

    # 回退：直接扫描 investment 目录（lint 不可用时）。
    inv_dir = knowledge_root / INVESTMENT_SUBDIR
    if not inv_dir.exists():
        return index
    for md in _iter_markdown_files(inv_dir):
        text = _read_text_safe(md)
        fm_text, _body = _split_frontmatter(text)
        frontmatter = _parse_frontmatter(fm_text)
        symbols = _normalize_symbol_list(frontmatter.get("symbols"))
        report_type = _safe_str(frontmatter.get("report_type")) or ""
        is_hy = report_type in HALF_YEAR_REPORT_TYPES
        period = _safe_str(frontmatter.get("financial_period"))
        ff = frontmatter.get("financial_facts")
        info = _PageHYInfo(
            rel_path=str(md.relative_to(knowledge_root)),
            symbols=symbols,
            is_half_year=is_hy,
            half_year_period=period,
            is_stale=False,
            missing_hyf_fields=[],
            has_financial_facts=bool(ff),
        )
        for entry in symbols:
            code, _name = _split_symbol_entry(entry)
            bare = _bare_code(code)
            if bare:
                index.setdefault(bare, []).append(info)
    return index


def _find_pages_for_symbol(
    symbol: str,
    name: str,
    index: Dict[str, List[_PageHYInfo]],
) -> List[_PageHYInfo]:
    """在索引中查找属于该 symbol 的所有页面。"""
    bare = _bare_code(symbol)
    if bare and bare in index:
        return index[bare]
    # 按简称兜底匹配。
    results: List[_PageHYInfo] = []
    if name:
        for _code, pages in index.items():
            for page in pages:
                for entry in page.symbols:
                    if _symbols_match(name, entry):
                        if page not in results:
                            results.append(page)
                        break
    return results


# ── 任务生成 ──────────────────────────────────────────────────────────


def _classify_symbol(
    symbol: str,
    name: str,
    pages: List[_PageHYInfo],
) -> Tuple[str, List[str], Optional[str]]:
    """对单个 symbol 决定 action / missing_fields / existing_page。

    返回 ``(action, missing_fields, existing_page_rel_path)``。
    """
    if not pages:
        return ACTION_INGEST_NEW, list(HYF_FIELDS), None

    # 取第一个半年报页（如有），否则取第一个页。
    hy_pages = [p for p in pages if p.is_half_year]
    target = hy_pages[0] if hy_pages else pages[0]

    if not target.is_half_year:
        # 有 wiki 页但不是半年报页 → 需要新建半年报专属页或升级 report_type。
        return ACTION_INGEST_NEW, list(HYF_FIELDS), target.rel_path

    # 已有半年报页，收集缺失字段。
    missing = list(target.missing_hyf_fields)
    # 去重合并所有同 symbol 半年报页的缺失字段。
    for p in hy_pages[1:]:
        for f in p.missing_hyf_fields:
            if f not in missing:
                missing.append(f)
    if not missing:
        # 页面字段齐全，但仍生成一条"复核"任务（过期复核或确认完整性）。
        missing = []
    return ACTION_ADD_FIELDS, missing, target.rel_path


def _build_task_item(
    symbol: str,
    name: str,
    tier: str,
    pages: List[_PageHYInfo],
    reason_hint: str = "",
    sources: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> HalfYearTaskItem:
    """构建单条半年报补录任务。"""
    action, missing, existing = _classify_symbol(symbol, name, pages)

    # reason 组装。
    if action == ACTION_INGEST_NEW and existing is None:
        reason = f"知识库无 {symbol} {name} 的半年报页面，需新建"
    elif action == ACTION_INGEST_NEW and existing is not None:
        reason = (
            f"已有页面 `{existing}` 但非半年报类型，需新建半年报专属页"
            f"（或升级 report_type 为半年报/中报/财报分析）"
        )
    else:
        if missing:
            fields_text = "、".join(missing)
            reason = f"已有半年报页 `{existing}` 缺字段：{fields_text}"
        else:
            reason = f"已有半年报页 `{existing}` 字段齐全，建议复核确认时效"
    if reason_hint:
        reason = f"{reason}；{reason_hint}"

    # 过期标记。
    is_stale = any(p.is_stale for p in pages)
    if is_stale and "过期" not in reason:
        reason += "；页面已过期，需复核"

    return HalfYearTaskItem(
        symbol=symbol,
        name=name,
        priority_tier=tier,
        reason=reason,
        missing_fields=missing,
        suggested_source_type=list(DEFAULT_SUGGESTED_SOURCE_TYPE),
        existing_page=existing,
        action=action,
        sources=sources or [],
        extra=extra or {},
    )


def _normalize_input_list(
    items: Optional[Sequence[Any]],
) -> List[Dict[str, str]]:
    """把输入列表归一为 [{symbol, name}, ...]。"""
    if not items:
        return []
    result: List[Dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            sym = str(item.get("symbol") or item.get("code") or "")
            nm = str(item.get("name") or item.get("short_name") or "")
        elif isinstance(item, str):
            sym = item
            nm = ""
        else:
            continue
        sym = sym.strip()
        if not sym:
            continue
        # 如果 symbol 字段含空格（如 "603296.SH 华勤技术"），拆分。
        if " " in sym:
            parts = sym.split(maxsplit=1)
            sym = parts[0]
            if not nm:
                nm = parts[1]
        result.append({"symbol": sym, "name": nm})
    return result


def _dedup_items(pack: HalfYearTaskPack) -> None:
    """同一 tier 内按 symbol 去重（保留首次出现的，合并 sources/extra）。"""
    for tier, items in list(pack.items_by_tier.items()):
        if not items:
            continue
        seen: Dict[str, HalfYearTaskItem] = {}
        for it in items:
            bare = _bare_code(it.symbol)
            if bare in seen:
                existing = seen[bare]
                for s in it.sources:
                    if s not in existing.sources:
                        existing.sources.append(s)
                for k, v in it.extra.items():
                    if k not in existing.extra:
                        existing.extra[k] = v
                # 取更严重的 missing_fields（超集）。
                for f in it.missing_fields:
                    if f not in existing.missing_fields:
                        existing.missing_fields.append(f)
                # 如果新条目 action 更明确（add_fields > ingest_new），保留更明确的。
                if it.action == ACTION_ADD_FIELDS and existing.action == ACTION_INGEST_NEW:
                    existing.action = it.action
                    existing.existing_page = it.existing_page or existing.existing_page
            else:
                seen[bare] = it
        pack.items_by_tier[tier] = sorted(seen.values(), key=lambda i: i.sort_key)


def _remove_duplicates_across_tiers(pack: HalfYearTaskPack) -> None:
    """跨 tier 去重：高优先 tier 已出现的 symbol 不在低 tier 重复。"""
    seen_symbols: set = set()
    for tier in TIER_ORDER:
        items = pack.items_by_tier.get(tier, [])
        filtered: List[HalfYearTaskItem] = []
        for it in items:
            bare = _bare_code(it.symbol)
            if bare in seen_symbols:
                continue
            seen_symbols.add(bare)
            filtered.append(it)
        pack.items_by_tier[tier] = filtered


# ── 上游摘要 ──────────────────────────────────────────────────────────


def _build_upstream_summary(
    lint_result: Optional[KnowledgeLintResult],
    attention: Optional[ResearchAttentionResult],
    input_counts: Dict[str, int],
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    summary["inputs"] = dict(input_counts)
    if lint_result is not None:
        summary["hy001_lint"] = {
            "page_count": lint_result.page_count,
            "half_year_pages": len(lint_result.pages_half_year),
            "half_year_period_missing": len(
                lint_result.pages_half_year_period_missing
            ),
            "half_year_facts_missing": len(
                lint_result.pages_half_year_facts_missing
            ),
            "half_year_opinion_only": len(
                lint_result.pages_half_year_opinion_only
            ),
        }
    if attention is not None:
        a_share = [
            s for s in attention.symbols if s.asset_class == "A_SHARE"
        ]
        summary["kb007_attention"] = {
            "symbol_count": attention.symbol_count,
            "a_share_count": len(a_share),
            "stale_mention_symbols": sum(
                1 for s in a_share if s.stale_mention_count > 0
            ),
            "top_symbols": [
                {
                    "symbol": s.symbol_key,
                    "name": s.name,
                    "score": round(s.research_attention_score, 2),
                    "stale": s.stale_mention_count,
                }
                for s in attention.top_symbols[:5]
            ],
        }
    return summary


# ── 主构建逻辑 ────────────────────────────────────────────────────────


def build_half_year_task_pack(
    knowledge_root: str,
    *,
    holdings: Optional[Sequence[Any]] = None,
    observation: Optional[Sequence[Any]] = None,
    haotian_candidates: Optional[Sequence[Any]] = None,
    tradeflow_candidates: Optional[Sequence[Any]] = None,
    collect_attention: bool = True,
) -> HalfYearTaskPack:
    """合并持仓/观察仓/候选/关注度信号，产出半年报补录任务包。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        holdings: 持仓列表，每项为 ``{"symbol": "603296", "name": "华勤技术"}``
            或 ``"603296"`` 字符串。
        observation: 观察仓列表（同上格式）。
        haotian_candidates: 昊天主候选列表（同上格式）。
        tradeflow_candidates: TradeFlow 主候选列表（同上格式）。
        collect_attention: 是否收集 KB-007 关注度信号（stale attention 层）。

    返回:
        :class:`HalfYearTaskPack`。任何上游模块抛异常都会被吞掉并记入
        ``errors``，任务包仍能产出。
    """
    root = Path(knowledge_root).expanduser()
    today = date.today()
    pack = HalfYearTaskPack(
        knowledge_root=str(root),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        as_of_date=today.strftime("%Y-%m-%d"),
    )

    if not root.exists():
        pack.errors.append(f"knowledge_root 不存在: {root}")
        return pack

    # ── 1. 运行 HY-001 lint ──
    lint_result: Optional[KnowledgeLintResult] = None
    try:
        lint_result = lint_local_knowledge(str(root))
    except Exception as exc:  # pragma: no cover - 容错
        pack.errors.append(f"HY-001 lint 失败: {exc!r}")
        lint_result = None
    if lint_result is not None:
        pack.errors.extend(lint_result.errors)

    # ── 2. 构建 symbol → page 索引 ──
    symbol_index = _build_symbol_page_index(lint_result, root)

    # ── 3. 运行 KB-007 研究关注度 ──
    attention: Optional[ResearchAttentionResult] = None
    if collect_attention:
        try:
            attention = compute_research_attention(str(root))
        except Exception as exc:  # pragma: no cover
            pack.errors.append(f"KB-007 attention 失败: {exc!r}")
            attention = None

    # ── 4. 归一化输入 ──
    holdings_list = _normalize_input_list(holdings)
    observation_list = _normalize_input_list(observation)
    haotian_list = _normalize_input_list(haotian_candidates)
    tradeflow_list = _normalize_input_list(tradeflow_candidates)

    # ── 5. 按优先级生成任务 ──
    for item_dict in holdings_list:
        pages = _find_pages_for_symbol(
            item_dict["symbol"], item_dict["name"], symbol_index
        )
        task = _build_task_item(
            item_dict["symbol"],
            item_dict["name"],
            TIER_HOLDINGS,
            pages,
            reason_hint="持仓标的，半年报补录最高优先",
            sources=["holdings"],
        )
        pack.items_by_tier.setdefault(TIER_HOLDINGS, []).append(task)

    for item_dict in observation_list:
        pages = _find_pages_for_symbol(
            item_dict["symbol"], item_dict["name"], symbol_index
        )
        task = _build_task_item(
            item_dict["symbol"],
            item_dict["name"],
            TIER_OBSERVATION,
            pages,
            reason_hint="观察仓标的，半年报补录次优先",
            sources=["observation_warehouse"],
        )
        pack.items_by_tier.setdefault(TIER_OBSERVATION, []).append(task)

    for item_dict in haotian_list:
        pages = _find_pages_for_symbol(
            item_dict["symbol"], item_dict["name"], symbol_index
        )
        task = _build_task_item(
            item_dict["symbol"],
            item_dict["name"],
            TIER_HAOTIAN,
            pages,
            reason_hint="昊天主候选",
            sources=["haotian_candidate"],
        )
        pack.items_by_tier.setdefault(TIER_HAOTIAN, []).append(task)

    for item_dict in tradeflow_list:
        pages = _find_pages_for_symbol(
            item_dict["symbol"], item_dict["name"], symbol_index
        )
        task = _build_task_item(
            item_dict["symbol"],
            item_dict["name"],
            TIER_TRADEFLOW,
            pages,
            reason_hint="TradeFlow 主候选",
            sources=["tradeflow_candidate"],
        )
        pack.items_by_tier.setdefault(TIER_TRADEFLOW, []).append(task)

    # ── 6. 研报关注度高但知识过期 ──
    if attention is not None:
        for sym in attention.symbols:
            if sym.asset_class != "A_SHARE":
                continue
            score = sym.research_attention_score
            stale_count = sym.stale_mention_count or 0
            if score < _STALE_ATTENTION_SCORE_THRESHOLD:
                continue
            if stale_count == 0:
                continue
            pages = _find_pages_for_symbol(
                sym.symbol_key, sym.name, symbol_index
            )
            if not pages:
                continue
            # 只把有过期页或缺失半年报事实的纳入。
            has_stale = any(p.is_stale for p in pages)
            has_no_hy = not any(p.is_half_year for p in pages)
            if not has_stale and not has_no_hy:
                continue
            task = _build_task_item(
                sym.symbol_key or sym.bare_code,
                sym.name,
                TIER_STALE_ATTENTION,
                pages,
                reason_hint=(
                    f"研报关注度 score={score:.2f}，"
                    f"stale_mention={stale_count}"
                ),
                sources=["KB-007:stale_attention"],
                extra={
                    "research_attention_score": round(score, 2),
                    "stale_mention_count": stale_count,
                    "mention_count": sym.mention_count,
                },
            )
            pack.items_by_tier.setdefault(TIER_STALE_ATTENTION, []).append(task)

    # ── 7. 去重 ──
    _dedup_items(pack)
    _remove_duplicates_across_tiers(pack)

    # ── 8. 上游摘要 ──
    input_counts = {
        "holdings": len(holdings_list),
        "observation": len(observation_list),
        "haotian_candidates": len(haotian_list),
        "tradeflow_candidates": len(tradeflow_list),
    }
    pack.upstream_summary = _build_upstream_summary(
        lint_result, attention, input_counts
    )

    return pack


# ── 报告渲染 ──────────────────────────────────────────────────────────


def _render_tier_section(
    pack: HalfYearTaskPack, tier: str, lines: List[str]
) -> None:
    items = pack.items_by_tier.get(tier, [])
    title = TIER_TITLES.get(tier, tier)
    lines.append(f"### {title}（{len(items)}）")
    lines.append("")
    if not items:
        lines.append("_（无）_")
        lines.append("")
        return
    lines.append(
        "| symbol | name | 动作 | 缺失字段 | 已有页 | 原因 | 来源 |"
    )
    lines.append("|--------|------|------|----------|--------|------|------|")
    for it in items:
        missing = "、".join(it.missing_fields) if it.missing_fields else "-"
        existing = f"`{it.existing_page}`" if it.existing_page else "-"
        sources_text = ", ".join(f"`{s}`" for s in it.sources) if it.sources else "-"
        reason = it.reason.replace("|", "/") if it.reason else "-"
        lines.append(
            f"| `{it.symbol}` | {it.name or '-'} | `{it.action}` | "
            f"{missing} | {existing} | {reason} | {sources_text} |"
        )
    lines.append("")


def render_half_year_task_pack_report(pack: HalfYearTaskPack) -> str:
    """渲染 Markdown 半年报补录任务包报告。

    报告结构：
      1. 概览（总任务数 / 分层统计 / 上游摘要）。
      2. 按优先级分层列出任务。
      3. 半年报 ingest 模板（HY-001 字段骨架）。
      4. 建议执行顺序。
      5. 免责声明。
    """
    lines: List[str] = []
    lines.append(
        f"# 半年报资料优先队列与 Tree Work 补录任务包 — {pack.as_of_date}"
    )
    lines.append("")
    lines.append(
        f"> [{TASK_CODE}] half_year_task_pack — 基于持仓 / 观察仓 / 候选 / "
        "研报关注度，只读生成半年报补录优先队列；"
        "**不修改知识库、不复制研报原文、不输出交易建议**。"
    )
    lines.append("")

    # 1. 概览
    lines.append("## 1. 概览")
    lines.append("")
    lines.append(f"- knowledge_root: `{pack.knowledge_root}`")
    lines.append(f"- generated_at: {pack.generated_at}")
    lines.append(f"- as_of_date: {pack.as_of_date}")
    lines.append(f"- contract_version: `{pack.contract_version}`")
    lines.append(f"- 任务总数: **{pack.total()}**")
    if pack.errors:
        lines.append(f"- ⚠️ 上游错误 ({len(pack.errors)}):")
        for err in pack.errors[:10]:
            lines.append(f"  - {err}")
    lines.append("")

    # 2. 输入统计
    lines.append("## 2. 输入统计")
    lines.append("")
    inputs = pack.upstream_summary.get("inputs", {})
    lines.append("| 输入来源 | 数量 |")
    lines.append("|----------|------|")
    lines.append(f"| 持仓 | {inputs.get('holdings', 0)} |")
    lines.append(f"| 观察仓 | {inputs.get('observation', 0)} |")
    lines.append(f"| 昊天候选 | {inputs.get('haotian_candidates', 0)} |")
    lines.append(f"| TradeFlow 候选 | {inputs.get('tradeflow_candidates', 0)} |")
    lines.append("")

    # 3. 上游信号摘要
    lines.append("## 3. 上游信号摘要")
    lines.append("")
    upstream = pack.upstream_summary
    if "hy001_lint" in upstream:
        hy = upstream["hy001_lint"]
        lines.append(
            f"- **HY-001 lint**：{hy.get('page_count', 0)} 页 / "
            f"半年报页 {hy.get('half_year_pages', 0)} "
            f"（缺报告期 {hy.get('half_year_period_missing', 0)} / "
            f"缺事实 {hy.get('half_year_facts_missing', 0)} / "
            f"观点冒充事实 {hy.get('half_year_opinion_only', 0)}）"
        )
    if "kb007_attention" in upstream:
        kb = upstream["kb007_attention"]
        lines.append(
            f"- **KB-007 attention**：symbols={kb.get('symbol_count', 0)} "
            f"(A_SHARE {kb.get('a_share_count', 0)}) / "
            f"stale_mention_symbols={kb.get('stale_mention_symbols', 0)}"
        )
        top = kb.get("top_symbols", [])
        if top:
            top_text = ", ".join(
                f"`{s['symbol']}`({s['score']})" for s in top[:5]
            )
            lines.append(f"  - Top 5：{top_text}")
    if not upstream:
        lines.append("_（无上游信号）_")
    lines.append("")

    # 4. 分层统计
    lines.append("## 4. 分层统计")
    lines.append("")
    lines.append("| 层级 | 数量 | 说明 |")
    lines.append("|------|------|------|")
    counts = pack.tier_counts()
    for tier in TIER_ORDER:
        cnt = counts.get(tier, 0)
        lines.append(
            f"| `{tier}` | {cnt} | {TIER_TITLES.get(tier, '')} |"
        )
    lines.append("")

    # 5. 按层级列出任务
    lines.append("## 5. 补录任务（按优先级分层）")
    lines.append("")
    for tier in TIER_ORDER:
        _render_tier_section(pack, tier, lines)

    # 6. ingest 模板
    lines.append("## 6. 半年报 ingest 模板（HY-001 字段骨架）")
    lines.append("")
    lines.append(
        "新建或补字段时，按以下 frontmatter 填写；"
        "对应契约见 `docs/local_knowledge_contract.md` §10。"
    )
    lines.append("")
    lines.append("```markdown")
    lines.append("---")
    lines.append("title: <公司名>—<YYYYH1>半年报")
    lines.append(f"created: {pack.as_of_date}")
    lines.append(f"updated: {pack.as_of_date}")
    lines.append("sources:")
    lines.append('  - "[[../../raw/<公告文件名>.md|公司公告]]"')
    lines.append('symbols: ["<6位代码>.SH <简称>"]')
    lines.append("report_type: 半年报")
    lines.append("evidence_level: A")
    lines.append("valid_until: 2099-12-31")
    lines.append("source_quality: 高")
    lines.append("stale_risk: 低")
    lines.append("# ── HY-001 半年报扩展字段 ──")
    lines.append("financial_period: 2025H1")
    lines.append("disclosure_date: <YYYY-MM-DD>")
    lines.append("source_type: [exchange_filing, fact_table, management_commentary]")
    lines.append("financial_facts:")
    lines.append("  - 营收 <数值>亿 (+/-<%> YoY)")
    lines.append("  - 归母净利 <数值>亿 (+/-<%> YoY)")
    lines.append("segment_facts:")
    lines.append("  - <分业务事实>")
    lines.append("management_commentary:")
    lines.append("  - <管理层表述/公司口径>")
    lines.append("forward_guidance:")
    lines.append("  - <公司指引>")
    lines.append("risk_factors: [<风险1>, <风险2>]")
    lines.append("source_links:")
    lines.append("  - 巨潮资讯 <公告URL>")
    lines.append("---")
    lines.append("")
    lines.append("# <标题>")
    lines.append("")
    lines.append("## 一句话总结")
    lines.append("")
    lines.append("<1-2 句核心事实摘要>")
    lines.append("")
    lines.append("## 投资逻辑")
    lines.append("")
    lines.append("- <核心事实 1>")
    lines.append("")
    lines.append("## 风险提示")
    lines.append("")
    lines.append("- <风险 1>")
    lines.append("")
    lines.append("## 原始资料")
    lines.append("")
    lines.append("- [[../../raw/<原始资料>.md|<来源别名>]]")
    lines.append("```")
    lines.append("")

    # 7. 建议执行顺序
    lines.append("## 7. 建议执行顺序")
    lines.append("")
    lines.append("1. **先补已持仓（P1_HOLDINGS）**：")
    lines.append("   - 持仓标的的半年报事实直接影响 TA 报告和 investment-controller。")
    lines.append("   - 已有页 → 补 HY-001 字段；无页 → 新建半年报页。")
    lines.append("2. **再补观察仓（P2_OBSERVATION）**：")
    lines.append("   - 观察仓标的接近关注区间，需提前消化半年报事实。")
    lines.append("3. **昊天主候选（P3_HAOTIAN）**：")
    lines.append("   - 左侧埋伏候选需半年报事实验证投资逻辑。")
    lines.append("4. **TradeFlow 主候选（P4_TRADEFLOW）**：")
    lines.append("   - 技术候选的半年报事实作为背景证据。")
    lines.append("5. **研报关注度高但知识过期（P5_STALE_ATTENTION）**：")
    lines.append("   - 关注度高但页面过期/缺事实，优先复核或补事实。")
    lines.append("")
    lines.append(
        "> 每条任务完成后，在对应 wiki 页 frontmatter 更新 `updated`；"
        "后续运行 `scripts/half_year_task_pack.py` 会自动剔除已完成项。"
    )
    lines.append("")

    # 8. 免责声明
    lines.append("## 8. 免责声明")
    lines.append("")
    lines.append(
        "- 本任务包只提供 Tree Work 半年报 ingest 字段要求与优先级，"
        "**不构成任何买卖建议或强动作词**。"
    )
    lines.append(
        "- 所有任务来源可追溯到 HY-001 lint / KB-007 关注度信号；"
        "执行后可重跑对应 CLI 验证。"
    )
    lines.append("- 任务包不含研报原文段落，仅引用 symbol / 字段缺口 / 简短原因。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"_由 `scripts/half_year_task_pack.py` 只读生成；"
        f"对应模块 `tradingagents.dataflows.half_year_task_pack`。_"
    )
    lines.append("")
    return "\n".join(lines)


# ── CLI 便利 ─────────────────────────────────────────────────────────


def suggest_output_path(
    docs_dir: str = "docs/knowledge_reports",
) -> str:
    """生成默认输出路径 ``docs/knowledge_reports/half_year_tree_work_tasks-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"half_year_tree_work_tasks-{today}.md")


__all__ = [
    "CONTRACT_VERSION",
    "TASK_CODE",
    "TIER_HOLDINGS",
    "TIER_OBSERVATION",
    "TIER_HAOTIAN",
    "TIER_TRADEFLOW",
    "TIER_STALE_ATTENTION",
    "TIER_ORDER",
    "TIER_TITLES",
    "ACTION_ADD_FIELDS",
    "ACTION_INGEST_NEW",
    "DEFAULT_SUGGESTED_SOURCE_TYPE",
    "HYF_FIELDS",
    "HalfYearTaskItem",
    "HalfYearTaskPack",
    "build_half_year_task_pack",
    "render_half_year_task_pack_report",
    "suggest_output_path",
    "default_knowledge_root",
]
