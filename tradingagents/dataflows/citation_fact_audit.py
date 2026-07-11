# [KB-017] citation_fact_audit
"""研报观点 vs 公告/半年报事实 citation 审计。

在 KB-015（研报观点/事实分离）、HY-003（半年报事实表）和 KB-014（citation policy）
之上，本模块把研报观点与公告/半年报事实做**交叉审计**，逐条标注观点的
``citation_audit_status``：

  - ``supported``         — 已被事实支持
  - ``weakened``          — 已被事实削弱（同向但幅度明显低于预期）
  - ``contradicted``      — 已被事实打脸（方向相反）
  - ``pending``           — 仍待验证（前瞻指引指向的事实期尚未披露）
  - ``insufficient_data`` — 缺对应事实，无法判定

设计约束（对应任务 KB-017）：
  - **只做证据一致性审计，不输出交易动作**：``citation_audit_status`` 只描述证据
    状态，绝不映射到 Buy/Sell；输出文案刻意避免 ``买入 / 卖出 / 加仓 / 减仓 /
    强烈推荐`` 等强动作词。
  - **缺半年报事实时不得强行判定观点错误**：缺数据只能落到
    ``insufficient_data`` 或 ``pending``，不得为了让数字漂亮就把观点标为
    ``contradicted``。
  - **事实源优先级按 KB-014**：``公告/财报原文(original_filing) > 官方公告
    (official_notice) > 券商研报(broker_research) > 媒体(media)/用户笔记
    (user_note)``。``_build_tiered_fact_index`` 按 tier 顺序挑选基准事实，
    ``弱来源不得覆盖强来源`` —— ``weak_source_blocked`` 显式标记每一次"弱来源
    试图覆盖强来源被阻止"的事件。
  - **不改 prompts / 不调用 live LLM / 不写生产 DB**：纯标准库 + 复用
    HY-003 / KB-015 / HY-005 只读解析结果；本模块不重新扫描知识库。
  - **TA 报告动作语义不被 audit 直接覆写**：``CitationAuditResult`` 不携带
    ``decision`` / ``execution_action`` 字段；``audit_summary`` 只输出证据描述，
    最终是否进入报告、是否调整 confidence 由调用方（HY-004 / KB-003）决定。

与 HY-005 (``thesis_fact_check``) 的差异：
  - HY-005 关注"旧研报核心观点 vs 最新半年报"的 4 状态方向比对；本模块在此
    基础上**增加**：
    1. ``pending`` 状态 — 前瞻指引指向的未来报告期尚未披露时，观点不进
       contradicted/weakened，只标"仍待验证"。
    2. 显式 KB-014 source tier 排序与"弱来源不得覆盖强来源"硬约束。
    3. ``audit_summary`` 直接供 TA 报告"本地知识补充/半年报事实对照"区块
       复用，结构稳定、字段可序列化。

使用示例::

    from tradingagents.dataflows.citation_fact_audit import (
        audit_citation_against_facts,
        render_citation_audit_report,
    )
    from tradingagents.dataflows.half_year_facts_provider import query_half_year_facts
    from tradingagents.dataflows.research_fact_opinion_index import (
        build_research_fact_opinion_index,
    )

    facts = query_half_year_facts(kb_root, symbol="300750")
    opinions = build_research_fact_opinion_index(kb_root, symbol="300750")
    result = audit_citation_against_facts(opinions, facts)
    print(render_citation_audit_report(result))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.dataflows.citation_policy import (  # [KB-014]
    OPINION_ONLY_TIERS,
    SOURCE_TYPE_FACT_VALUES,
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_OFFICIAL_NOTICE,
    TIER_ORIGINAL_FILING,
    TIER_UNKNOWN,
    TIER_USER_NOTE,
)
from tradingagents.dataflows.half_year_facts_provider import (
    DATA_CONFLICT,
    DATA_FRESH,
    DATA_MISSING_FACTS,
    DATA_MISSING_PERIOD,
    DATA_OPINION_ONLY,
    DATA_STALE,
    HalfYearFactsPage,
    HalfYearFactsQueryResult,
    ParsedMetric,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_HAS_DATA,
    STATUS_NORMAL_NO_DATA,
)
from tradingagents.dataflows.research_fact_opinion_index import (
    CLAIM_FACT,
    CLAIM_FORECAST,
    CLAIM_OPINION,
    CLAIM_RISK,
    CLAIM_UNKNOWN,
    ResearchClaimItem,
    ResearchFactOpinionIndexResult,
    ResearchFactOpinionPage,
    STALE_FRESH,
)
from tradingagents.dataflows.thesis_fact_check import (  # [HY-005] 复用比对助手
    DIR_NEGATIVE,
    DIR_NEUTRAL,
    DIR_POSITIVE,
    _classify_direction,
    _extract_segment_topic,
    _match_opinion_to_metric,
    _parse_pct,
)


# ── 常量 ──────────────────────────────────────────────────────────────

TASK_CODE = "KB-017"

# citation_audit_status 枚举（与任务原文严格一致）。
STATUS_SUPPORTED = "supported"
STATUS_WEAKENED = "weakened"
STATUS_CONTRADICTED = "contradicted"
STATUS_PENDING = "pending"
STATUS_INSUFFICIENT_DATA = "insufficient_data"
ALL_AUDIT_STATUSES: Tuple[str, ...] = (
    STATUS_SUPPORTED,
    STATUS_WEAKENED,
    STATUS_CONTRADICTED,
    STATUS_PENDING,
    STATUS_INSUFFICIENT_DATA,
)

# 证据等级（沿用 HY-005 口径，确保跨模块一致）。
EVIDENCE_STRONG = "strong"
EVIDENCE_MODERATE = "moderate"
EVIDENCE_WEAK = "weak"
EVIDENCE_NONE = "none"

# KB-014 tier 优先级（数字越大优先级越高）。
# 公告/财报原文 > 官方公告 > 券商研报 > 媒体 > 用户笔记 > 未知。
_TIER_PRIORITY: Dict[str, int] = {
    TIER_ORIGINAL_FILING: 60,
    TIER_OFFICIAL_NOTICE: 50,
    TIER_BROKER_RESEARCH: 40,
    TIER_MEDIA: 20,
    TIER_USER_NOTE: 10,
    TIER_UNKNOWN: 0,
}

# 事实页 data_status 是否足够可靠用于反证（fresh / conflict 才算可作 ground truth）。
_RELIABLE_FACT_STATUSES = frozenset({DATA_FRESH, DATA_CONFLICT})

# 文本裁剪上限（与 HY-005 / KB-015 保持一致，避免长篇原文）。
_TEXT_MAX_CHARS = 120
_REASON_MAX_CHARS = 160
_SUMMARY_MAX_ENTRIES = 8
_SUMMARY_ENTRY_MAX_CHARS = 200
_MAX_FLAGS = 30

# pending 触发关键词：观点文本命中以下任一 → 视为指向"未来报告期"。
# 这些词通常出现在前瞻指引 / 全年展望 / 明年预期中。
_FUTURE_PERIOD_KEYWORDS: Tuple[str, ...] = (
    "全年", "FY", "明年", "下一年", "2026", "2027", "2028",
    "下半年", "下半年展望", "H2", "下个半年", "下一报告期",
    "预计未来", "未来三年", "未来一年",
)
# 显式的前瞻指引类 origin_field（KB-015 forecast_items 走 forward_guidance）。
_FORECAST_ORIGIN_FIELDS = ("forward_guidance", "body:forecast")

# 数值百分比解析（复用 HY-005，保持一致语义）。
_PCT_RE = re.compile(r"([-+])\s*(\d+(?:\.\d+)?)\s*(?:%|pp|个百分点)")


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class CitationAuditFlag:
    """单条观点的 citation 审计结果。

    每条 flag 对应一条观点（claim），携带比对状态、证据强度、来源层级和
    可读 ``reason``。``weak_source_blocked`` 显式记录"弱来源试图覆盖强来源
    被阻止"事件。
    """

    claim_text: str
    """原始观点文本（裁剪到 _TEXT_MAX_CHARS）。"""

    metric_key: str
    """比对的指标维度（revenue / net_profit / gross_margin / operating_cash_flow
    / segment / risk / other）。"""

    audit_status: str = STATUS_INSUFFICIENT_DATA
    """本条 flag 的审计状态（见 :data:`ALL_AUDIT_STATUSES`）。"""

    evidence_level: str = EVIDENCE_NONE
    """本条 flag 的证据强度。"""

    opinion_source_tier: str = TIER_UNKNOWN
    """观点所在页面的 KB-014 source tier。"""

    fact_source_tier: str = TIER_UNKNOWN
    """作为基准的事实的来源 tier（若使用了事实）。"""

    opinion_direction: str = DIR_NEUTRAL
    """观点隐含方向。"""

    fact_direction: str = DIR_NEUTRAL
    """事实隐含方向。"""

    fact_text: str = ""
    """对应的事实文本（裁剪到 _TEXT_MAX_CHARS）。无匹配事实时为空串。"""

    opinion_change: Optional[str] = None
    """观点中隐含的预期变化（如 ``+60%``）。"""

    fact_change: Optional[str] = None
    """事实同比变化（如 ``-20.0%``）。"""

    weak_source_blocked: bool = False
    """弱来源观点试图覆盖强来源事实时为 True（被审计阻止）。"""

    pending_reason: str = ""
    """当 audit_status=pending 时，记录为什么仍待验证。"""

    reason: str = ""
    """可读说明（裁剪到 _REASON_MAX_CHARS）。"""

    source_path: str = ""
    """观点来源页面路径。"""

    fact_source_path: str = ""
    """事实来源页面路径。"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_text": self.claim_text,
            "metric_key": self.metric_key,
            "audit_status": self.audit_status,
            "evidence_level": self.evidence_level,
            "opinion_source_tier": self.opinion_source_tier,
            "fact_source_tier": self.fact_source_tier,
            "opinion_direction": self.opinion_direction,
            "fact_direction": self.fact_direction,
            "fact_text": self.fact_text,
            "opinion_change": self.opinion_change,
            "fact_change": self.fact_change,
            "weak_source_blocked": self.weak_source_blocked,
            "pending_reason": self.pending_reason,
            "reason": self.reason,
            "source_path": self.source_path,
            "fact_source_path": self.fact_source_path,
        }


@dataclass
class CitationAuditResult:
    """整次研报观点 vs 公告/半年报事实 citation 审计的聚合结果。"""

    symbol: str = ""
    name: str = ""
    citation_audit_status: str = STATUS_INSUFFICIENT_DATA
    """聚合状态（优先级 contradicted > weakened > supported > pending
    > insufficient_data）。"""

    flags: List[CitationAuditFlag] = field(default_factory=list)

    supported_flags: List[CitationAuditFlag] = field(default_factory=list)
    weakened_flags: List[CitationAuditFlag] = field(default_factory=list)
    contradicted_flags: List[CitationAuditFlag] = field(default_factory=list)
    pending_flags: List[CitationAuditFlag] = field(default_factory=list)
    insufficient_flags: List[CitationAuditFlag] = field(default_factory=list)

    weak_source_override_blocked_count: int = 0
    """弱来源观点试图覆盖强来源事实、被审计阻止的事件总数。"""

    needs_tree_work_review: bool = False
    """是否存在需要 Tree Work 人工复核的审计结果（contradicted / 多条 weakened）。"""

    fact_period: Optional[str] = None
    """作为比对基准的事实报告期。"""

    fact_source_tier: str = TIER_UNKNOWN
    """基准事实的来源 tier（体现 KB-014 优先级）。"""

    fact_data_status: Optional[str] = None
    """事实页的 data_status（fresh / stale / ...）。"""

    checked_claim_count: int = 0
    """参与了比对（非 insufficient_data）的观点数。"""

    total_claim_count: int = 0
    """审计覆盖的观点总数。"""

    audit_summary: List[str] = field(default_factory=list)
    """可进入 TA 报告"本地知识补充/半年报事实对照"的摘要条目
    （最多 _SUMMARY_MAX_ENTRIES 条，每条裁剪到 _SUMMARY_ENTRY_MAX_CHARS）。"""

    task: str = TASK_CODE
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "citation_audit_status": self.citation_audit_status,
            "flags": [f.to_dict() for f in self.flags],
            "supported_flags": [f.to_dict() for f in self.supported_flags],
            "weakened_flags": [f.to_dict() for f in self.weakened_flags],
            "contradicted_flags": [f.to_dict() for f in self.contradicted_flags],
            "pending_flags": [f.to_dict() for f in self.pending_flags],
            "insufficient_flags": [f.to_dict() for f in self.insufficient_flags],
            "weak_source_override_blocked_count": (
                self.weak_source_override_blocked_count
            ),
            "needs_tree_work_review": self.needs_tree_work_review,
            "fact_period": self.fact_period,
            "fact_source_tier": self.fact_source_tier,
            "fact_data_status": self.fact_data_status,
            "checked_claim_count": self.checked_claim_count,
            "total_claim_count": self.total_claim_count,
            "audit_summary": list(self.audit_summary),
            "task": self.task,
            "errors": list(self.errors),
        }


# ── 文本工具 ──────────────────────────────────────────────────────────


def _clip(text: str, max_chars: int = _TEXT_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text


def _strip_symbol_to_code(entry: str) -> str:
    """``000977.SZ 浪潮信息`` → ``000977``。

    取首个 token，去交易所后缀，只保留 6 位数字代码；无代码时原样返回。
    """
    if not entry:
        return ""
    code = entry.split()[0] if " " in entry else entry
    return re.sub(r"\.(SH|SZ|BJ|HK|US|SS)$", "", code, flags=re.IGNORECASE).strip()


def _looks_like_future_period_claim(text: str, origin_field: str = "") -> bool:
    """判断观点是否指向"未来报告期"（触发 pending 状态候选）。

    判断规则（任一命中即为 True）：
      - ``origin_field`` 是 forward_guidance / body:forecast（KB-015 前瞻指引类）。
      - 观点文本含 ``_FUTURE_PERIOD_KEYWORDS`` 中的关键词。
    """
    if origin_field in _FORECAST_ORIGIN_FIELDS:
        return True
    if not text:
        return False
    return any(kw in text for kw in _FUTURE_PERIOD_KEYWORDS)


# ── Tier 优先级事实索引（KB-014）───────────────────────────────────────


@dataclass
class _TieredFactIndex:
    """按 KB-014 tier 优先级构建的事实快照。

    - ``metrics``：metric_key → ParsedMetric，**始终取最高 tier 页面的事实**。
    - ``segments`` / ``risks``：合并所有 fresh/conflict 页（按 tier 排序后去重）。
    - ``period`` / ``data_status`` / ``source_path`` / ``source_tier``：基准事实
      页的报告期 / 状态 / 路径 / 来源 tier。
    """

    metrics: Dict[str, ParsedMetric] = field(default_factory=dict)
    segments: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    period: Optional[str] = None
    data_status: Optional[str] = None
    source_path: str = ""
    source_tier: str = TIER_UNKNOWN
    available: bool = False
    """是否有任何可靠事实页（fresh / conflict）可作为基准。"""


def _page_source_tier(page: HalfYearFactsPage) -> str:
    """从 HY-003 页面的 ``source_type`` 推断 KB-014 tier。

    HY-003 页面的 ``source_type`` 是 lint 归一化后的字符串列表（如
    ``["exchange_filing", "fact_table"]``）。本函数按"事实类 > 观点类"的硬约束
    选出最高 tier。
    """
    if not page.source_type:
        return TIER_UNKNOWN
    best_tier = TIER_UNKNOWN
    best_pri = -1
    for st in page.source_type:
        st_lower = (st or "").lower()
        if st_lower in ("exchange_filing", "fact_table", "management_commentary"):
            tier = TIER_ORIGINAL_FILING
        elif st_lower in ("official_notice", "regulatory_notice"):
            tier = TIER_OFFICIAL_NOTICE
        elif st_lower == "broker_report":
            tier = TIER_BROKER_RESEARCH
        elif st_lower == "media":
            tier = TIER_MEDIA
        else:
            tier = TIER_UNKNOWN
        pri = _TIER_PRIORITY.get(tier, 0)
        if pri > best_pri:
            best_pri = pri
            best_tier = tier
    return best_tier


def _build_tiered_fact_index(facts: HalfYearFactsQueryResult) -> _TieredFactIndex:
    """按 KB-014 tier 优先级构建基准事实索引。

    选择策略（严格遵循"弱来源不得覆盖强来源"）：
      1. 只考虑 ``data_status ∈ _RELIABLE_FACT_STATUSES``（fresh / conflict）的页面；
         opinion_only / missing / 无任何可靠页 → ``available=False``。
      2. 在可靠页中，按 ``(tier_priority, period)`` 排序选出**基准页**：
         tier 高的优先；同 tier 取最新报告期。
      3. 基准页的 ``source_tier`` 即为本次审计的"事实源 tier"。
      4. ``metrics`` / ``segments`` / ``risks`` 都从基准页取，保证一致性。

    stale 页**不进入**反证（与 HY-005 口径一致），KB-017 不重复 stale 降级逻辑。
    """
    idx = _TieredFactIndex()
    if not isinstance(facts, HalfYearFactsQueryResult):
        return idx
    if not facts.pages:
        return idx

    reliable_pages = [
        p for p in facts.pages if p.data_status in _RELIABLE_FACT_STATUSES
    ]
    if not reliable_pages:
        return idx

    def _rank(p: HalfYearFactsPage) -> Tuple[int, Tuple[int, int]]:
        tier_pri = _TIER_PRIORITY.get(_page_source_tier(p), 0)
        period_key = _period_sort_key(p.financial_period or "")
        return (tier_pri, period_key)

    reliable_pages.sort(key=_rank, reverse=True)
    base = reliable_pages[0]
    idx.available = True
    idx.period = base.financial_period
    idx.data_status = base.data_status
    idx.source_path = base.rel_path
    idx.source_tier = _page_source_tier(base)

    for m in base.financial_facts:
        if m.metric_key in (
            "revenue",
            "net_profit",
            "gross_margin",
            "operating_cash_flow",
        ):
            if m.metric_key not in idx.metrics:
                idx.metrics[m.metric_key] = m

    idx.segments = list(base.segment_facts or [])
    idx.risks = list(base.risk_factors or [])
    return idx


def _period_sort_key(period: str) -> Tuple[int, int]:
    """报告期排序键：``2025H1`` → ``(2025, 1)``；``2025H2`` → ``(2025, 2)``。"""
    m = re.match(r"(\d{4})", period)
    year = int(m.group(1)) if m else 0
    half = 2 if ("H2" in period or "年报" in period or "三季报" in period) else 1
    return (year, half)


# ── 观点收集 ──────────────────────────────────────────────────────────


def _collect_claims(
    index_result: ResearchFactOpinionIndexResult,
) -> List[Tuple[ResearchClaimItem, str, str]]:
    """从 KB-015 索引收集可参与审计的观点。

    收集范围：
      - ``research_claims``（opinion/unknown）。
      - ``forecast_items``（前瞻指引类，可能触发 pending）。
      - ``reported_facts`` 中**含方向关键词**的 claim（兜底：broker 观点冒充事实
        时，KB-015 可能标为 fact，但文本含方向词 → 仍是观点）。

    risk 类由 :func:`_collect_risk_claims` 单独处理。

    返回 ``(claim, page_rel_path, page_source_tier)`` 列表。
    """
    out: List[Tuple[ResearchClaimItem, str, str]] = []
    if not isinstance(index_result, ResearchFactOpinionIndexResult):
        return out
    for page in index_result.pages:
        page_tier = page.source_quality_tier or TIER_UNKNOWN
        for claim in page.research_claims + page.forecast_items:
            out.append((claim, page.rel_path, page_tier))
        for claim in page.reported_facts:
            if _classify_direction(claim.text) != DIR_NEUTRAL:
                out.append((claim, page.rel_path, page_tier))
    return out


def _collect_risk_claims(
    index_result: ResearchFactOpinionIndexResult,
) -> List[Tuple[ResearchClaimItem, str, str]]:
    """从 KB-015 索引收集风险类观点。"""
    out: List[Tuple[ResearchClaimItem, str, str]] = []
    if not isinstance(index_result, ResearchFactOpinionIndexResult):
        return out
    for page in index_result.pages:
        page_tier = page.source_quality_tier or TIER_UNKNOWN
        for claim in page.risk_items:
            out.append((claim, page.rel_path, page_tier))
    return out


# ── 弱来源覆盖检测（KB-014 硬约束）────────────────────────────────────


def _is_weak_source_override(
    opinion_tier: str,
    fact_tier: str,
    opinion_dir: str,
    fact_dir: str,
) -> bool:
    """检测"弱来源观点试图覆盖强来源事实"。

    触发条件（同时满足）：
      - 观点与事实方向相反（``opinion_dir != fact_dir``，且都不是 neutral）。
      - 观点 tier 严格**低于**事实 tier（按 :data:`_TIER_PRIORITY`）。

    命中时审计会**阻止覆盖**：弱来源观点不会把强来源事实的反证"洗白"，
    最终 ``audit_status`` 仍按强来源事实的方向判定（contradicted）。
    """
    if opinion_dir == DIR_NEUTRAL or fact_dir == DIR_NEUTRAL:
        return False
    if opinion_dir == fact_dir:
        return False
    op_pri = _TIER_PRIORITY.get(opinion_tier, 0)
    fact_pri = _TIER_PRIORITY.get(fact_tier, 0)
    return op_pri < fact_pri


# ── 单观点审计 ────────────────────────────────────────────────────────


def _metric_evidence_level(
    fact_idx: _TieredFactIndex,
    *,
    strong: bool,
) -> str:
    """根据基准事实页 data_status + tier 决定证据等级。

    - ``fresh`` + 高 tier (original_filing / official_notice) → strong / moderate。
    - ``fresh`` + 中 tier (broker_research) → moderate / weak。
    - ``fresh`` + 低 tier → weak。
    - ``conflict`` → 一律 weak（事实本身有冲突，证据强度受限）。
    """
    if fact_idx.data_status == DATA_CONFLICT:
        return EVIDENCE_WEAK
    if fact_idx.data_status != DATA_FRESH:
        return EVIDENCE_WEAK
    tier = fact_idx.source_tier
    if tier in (TIER_ORIGINAL_FILING, TIER_OFFICIAL_NOTICE):
        return EVIDENCE_STRONG if strong else EVIDENCE_MODERATE
    if tier == TIER_BROKER_RESEARCH:
        return EVIDENCE_MODERATE if strong else EVIDENCE_WEAK
    return EVIDENCE_WEAK


def _audit_financial_metric_claim(
    claim: ResearchClaimItem,
    rel_path: str,
    opinion_tier: str,
    metric_key: str,
    fact_idx: _TieredFactIndex,
) -> CitationAuditFlag:
    """审计一个财务指标观点。

    判定优先级：
      1. ``pending`` — 观点指向未来期 + 事实期早于观点目标期 → 仍待验证。
      2. 无对应事实 → ``insufficient_data``。
      3. 方向相反 → ``contradicted``（若弱来源覆盖 → ``weak_source_blocked=True``）。
      4. 方向一致但幅度低于预期 → ``weakened``。
      5. 方向一致 → ``supported``。
    """
    opinion_text = _clip(claim.text)
    opinion_dir = _classify_direction(claim.text)
    opinion_pct = _parse_pct(claim.text)

    # pending 检测：前瞻指引 / 未来期观点，事实期可能尚未覆盖。
    if _looks_like_future_period_claim(claim.text, claim.origin_field):
        # 观点含明确未来期关键词 → 标 pending（不进 contradicted）。
        return CitationAuditFlag(
            claim_text=opinion_text,
            metric_key=metric_key,
            audit_status=STATUS_PENDING,
            evidence_level=EVIDENCE_NONE,
            opinion_source_tier=opinion_tier,
            fact_source_tier=fact_idx.source_tier,
            opinion_direction=opinion_dir,
            fact_direction=DIR_NEUTRAL,
            opinion_change=(
                f"+{opinion_pct}%" if opinion_pct is not None else None
            ),
            pending_reason=_clip(
                "观点指向未来报告期，当前半年报事实尚未覆盖该期间，仍待验证。",
                _REASON_MAX_CHARS,
            ),
            reason=_clip(
                "观点属前瞻指引 / 未来期展望，当前事实期未覆盖，仍待验证。",
                _REASON_MAX_CHARS,
            ),
            source_path=rel_path,
            fact_source_path=fact_idx.source_path,
        )

    metric = fact_idx.metrics.get(metric_key)
    if metric is None:
        return CitationAuditFlag(
            claim_text=opinion_text,
            metric_key=metric_key,
            audit_status=STATUS_INSUFFICIENT_DATA,
            evidence_level=EVIDENCE_NONE,
            opinion_source_tier=opinion_tier,
            fact_source_tier=fact_idx.source_tier,
            opinion_direction=opinion_dir,
            fact_direction=DIR_NEUTRAL,
            opinion_change=(
                f"+{opinion_pct}%" if opinion_pct is not None else None
            ),
            reason=_clip(
                f"观点讨论 {metric_key}，但半年报事实中无对应指标，无法判定。",
                _REASON_MAX_CHARS,
            ),
            source_path=rel_path,
            fact_source_path=fact_idx.source_path,
        )

    fact_text = _clip(metric.raw)
    fact_dir = _classify_direction(metric.raw)
    fact_pct = _parse_pct(metric.raw or "") or _parse_pct(metric.change or "")

    base = CitationAuditFlag(
        claim_text=opinion_text,
        metric_key=metric_key,
        opinion_source_tier=opinion_tier,
        fact_source_tier=fact_idx.source_tier,
        opinion_direction=opinion_dir,
        fact_direction=fact_dir,
        fact_text=fact_text,
        opinion_change=(
            f"+{opinion_pct}%" if opinion_pct is not None else None
        ),
        fact_change=metric.change,
        source_path=rel_path,
        fact_source_path=fact_idx.source_path,
    )

    # 方向相反 → contradicted。
    if (
        opinion_dir in (DIR_POSITIVE, DIR_NEGATIVE)
        and fact_dir in (DIR_POSITIVE, DIR_NEGATIVE)
        and opinion_dir != fact_dir
    ):
        # 弱来源覆盖检测（KB-014 硬约束）。
        blocked = _is_weak_source_override(
            opinion_tier, fact_idx.source_tier, opinion_dir, fact_dir
        )
        base.weak_source_blocked = blocked
        base.audit_status = STATUS_CONTRADICTED
        base.evidence_level = _metric_evidence_level(fact_idx, strong=True)
        base.reason = _clip(
            f"观点预期 {metric_key} {opinion_dir}，"
            f"但事实（{metric.change or fact_text}）为 {fact_dir}，方向相反。"
            + (" 弱来源观点不得覆盖强来源事实。" if blocked else ""),
            _REASON_MAX_CHARS,
        )
        return base

    # 方向一致 → 比较 amplitude。
    if opinion_dir == fact_dir and opinion_dir != DIR_NEUTRAL:
        if opinion_pct is not None and fact_pct is not None:
            expected = abs(opinion_pct)
            actual = abs(fact_pct)
            if expected > 0 and actual < expected * 0.5:
                base.audit_status = STATUS_WEAKENED
                base.evidence_level = _metric_evidence_level(fact_idx, strong=False)
                base.reason = _clip(
                    f"观点预期 {metric_key} 增长约 {expected:.1f}%，"
                    f"但事实仅 {actual:.1f}%，增幅明显低于预期。",
                    _REASON_MAX_CHARS,
                )
                return base
        base.audit_status = STATUS_SUPPORTED
        base.evidence_level = _metric_evidence_level(fact_idx, strong=False)
        base.reason = _clip(
            f"观点预期 {metric_key} {opinion_dir}，事实同向"
            f"（{metric.change or fact_text}），方向一致。",
            _REASON_MAX_CHARS,
        )
        return base

    # 至少一方 neutral。
    if fact_dir != DIR_NEUTRAL and opinion_dir == DIR_NEUTRAL:
        if opinion_pct is not None and fact_pct is not None:
            if (opinion_pct > 0) != (fact_pct > 0):
                base.audit_status = STATUS_CONTRADICTED
                base.evidence_level = _metric_evidence_level(fact_idx, strong=True)
                base.reason = _clip(
                    f"观点隐含 {metric_key} 正向预期（+{opinion_pct:.1f}%），"
                    f"但事实为 {fact_pct:.1f}%，方向相反。",
                    _REASON_MAX_CHARS,
                )
                return base
        base.audit_status = STATUS_INSUFFICIENT_DATA
        base.evidence_level = EVIDENCE_WEAK
        base.reason = _clip(
            f"观点无明确方向，无法与 {metric_key} 事实做方向比对。",
            _REASON_MAX_CHARS,
        )
        return base

    # 双方 neutral。
    base.audit_status = STATUS_INSUFFICIENT_DATA
    base.evidence_level = EVIDENCE_WEAK
    base.reason = _clip(
        f"观点与 {metric_key} 事实均无明确方向，无法审计。",
        _REASON_MAX_CHARS,
    )
    return base


def _audit_segment_claim(
    claim: ResearchClaimItem,
    rel_path: str,
    opinion_tier: str,
    fact_idx: _TieredFactIndex,
) -> CitationAuditFlag:
    """审计一个分业务观点。"""
    topic = _extract_segment_topic(claim.text) or "segment"
    opinion_text = _clip(claim.text)
    opinion_dir = _classify_direction(claim.text)

    matched_seg = ""
    for seg in fact_idx.segments:
        if topic in seg:
            matched_seg = seg
            break
    if not matched_seg:
        for m in fact_idx.metrics.values():
            if topic in (m.raw or ""):
                matched_seg = m.raw
                break

    if not matched_seg:
        return CitationAuditFlag(
            claim_text=opinion_text,
            metric_key="segment",
            audit_status=STATUS_INSUFFICIENT_DATA,
            evidence_level=EVIDENCE_NONE,
            opinion_source_tier=opinion_tier,
            fact_source_tier=fact_idx.source_tier,
            opinion_direction=opinion_dir,
            reason=_clip(
                f"观点讨论分业务「{topic}」，半年报分业务事实中无对应记录。",
                _REASON_MAX_CHARS,
            ),
            source_path=rel_path,
            fact_source_path=fact_idx.source_path,
        )

    fact_dir = _classify_direction(matched_seg)
    fact_text = _clip(matched_seg)
    flag = CitationAuditFlag(
        claim_text=opinion_text,
        metric_key="segment",
        opinion_source_tier=opinion_tier,
        fact_source_tier=fact_idx.source_tier,
        opinion_direction=opinion_dir,
        fact_direction=fact_dir,
        fact_text=fact_text,
        source_path=rel_path,
        fact_source_path=fact_idx.source_path,
    )

    if (
        opinion_dir in (DIR_POSITIVE, DIR_NEGATIVE)
        and fact_dir in (DIR_POSITIVE, DIR_NEGATIVE)
    ):
        if opinion_dir != fact_dir:
            blocked = _is_weak_source_override(
                opinion_tier, fact_idx.source_tier, opinion_dir, fact_dir
            )
            flag.weak_source_blocked = blocked
            flag.audit_status = STATUS_CONTRADICTED
            flag.evidence_level = _metric_evidence_level(fact_idx, strong=True)
            flag.reason = _clip(
                f"观点预期分业务「{topic}」{opinion_dir}，"
                f"事实显示 {fact_dir}（{fact_text}），方向相反。"
                + (" 弱来源不得覆盖强来源。" if blocked else ""),
                _REASON_MAX_CHARS,
            )
        else:
            flag.audit_status = STATUS_SUPPORTED
            flag.evidence_level = _metric_evidence_level(fact_idx, strong=False)
            flag.reason = _clip(
                f"观点预期分业务「{topic}」{opinion_dir}，事实同向"
                f"（{fact_text}）。",
                _REASON_MAX_CHARS,
            )
    else:
        flag.audit_status = STATUS_INSUFFICIENT_DATA
        flag.evidence_level = EVIDENCE_WEAK
        flag.reason = _clip(
            f"观点或事实方向不明确，分业务「{topic}」无法审计。",
            _REASON_MAX_CHARS,
        )
    return flag


def _audit_risk_claim(
    claim: ResearchClaimItem,
    rel_path: str,
    opinion_tier: str,
    fact_idx: _TieredFactIndex,
) -> CitationAuditFlag:
    """审计一个风险观点。

    风险观点在事实 ``risk_factors`` 中复现 → ``supported``（风险被确认）；
    否则 → ``insufficient_data``（不轻易说风险被"打脸"，因为没列入不代表不存在）。
    """
    opinion_text = _clip(claim.text)
    risk_kw = _extract_risk_keyword(claim.text)

    matched = ""
    for r in fact_idx.risks:
        if risk_kw and risk_kw in r:
            matched = r
            break

    flag = CitationAuditFlag(
        claim_text=opinion_text,
        metric_key="risk",
        opinion_source_tier=opinion_tier,
        fact_source_tier=fact_idx.source_tier,
        fact_text=_clip(matched),
        source_path=rel_path,
        fact_source_path=fact_idx.source_path,
    )
    if matched:
        flag.audit_status = STATUS_SUPPORTED
        flag.evidence_level = EVIDENCE_MODERATE
        flag.reason = _clip(
            f"风险观点「{risk_kw}」在半年报风险因素中复现，风险被事实确认。",
            _REASON_MAX_CHARS,
        )
    else:
        flag.audit_status = STATUS_INSUFFICIENT_DATA
        flag.evidence_level = EVIDENCE_NONE
        flag.reason = _clip(
            f"风险观点「{risk_kw}」未在半年报风险因素中找到对应记录。",
            _REASON_MAX_CHARS,
        )
    return flag


def _extract_risk_keyword(text: str) -> str:
    """从风险观点文本中提取关键词（复用 HY-005 口径）。"""
    seg = _extract_segment_topic(text)
    if seg:
        return seg
    for kw in (
        "客户集中度", "原材料", "汇率", "政策", "GPU供应", "供应紧张",
        "宏观经济", "批价", "资产质量", "库存", "应收", "现金流",
    ):
        if kw in text:
            return kw
    return text[:4].strip()


# ── 聚合 ──────────────────────────────────────────────────────────────


# 聚合状态优先级：contradicted > weakened > supported > pending > insufficient。
_STATUS_PRIORITY: Dict[str, int] = {
    STATUS_CONTRADICTED: 5,
    STATUS_WEAKENED: 4,
    STATUS_SUPPORTED: 3,
    STATUS_PENDING: 2,
    STATUS_INSUFFICIENT_DATA: 1,
}


def _aggregate_audit_status(flags: List[CitationAuditFlag]) -> str:
    """聚合 flag 状态为整体 citation_audit_status。"""
    if not flags:
        return STATUS_INSUFFICIENT_DATA
    best = STATUS_INSUFFICIENT_DATA
    best_pri = 0
    for f in flags:
        pri = _STATUS_PRIORITY.get(f.audit_status, 0)
        if pri > best_pri:
            best_pri = pri
            best = f.audit_status
    return best


def _aggregate_evidence_level(flags: List[CitationAuditFlag]) -> str:
    """聚合证据等级：取所有非 insufficient flag 中最高的。"""
    if not flags:
        return EVIDENCE_NONE
    rank = {
        EVIDENCE_STRONG: 3,
        EVIDENCE_MODERATE: 2,
        EVIDENCE_WEAK: 1,
        EVIDENCE_NONE: 0,
    }
    best = EVIDENCE_NONE
    for f in flags:
        if f.audit_status == STATUS_INSUFFICIENT_DATA:
            continue
        if rank.get(f.evidence_level, 0) > rank.get(best, 0):
            best = f.evidence_level
    return best


def _build_audit_summary(result: CitationAuditResult) -> None:
    """生成 ``audit_summary``，供 TA 报告"本地知识补充/半年报事实对照"复用。

    每条摘要都是人类可读的字符串，裁剪到 ``_SUMMARY_ENTRY_MAX_CHARS``。
    摘要**不含**任何买卖建议词，只描述证据状态。
    """
    summary: List[str] = []
    status_label = {
        STATUS_CONTRADICTED: "有观点被事实打脸",
        STATUS_WEAKENED: "有观点被事实削弱",
        STATUS_SUPPORTED: "观点被事实支持",
        STATUS_PENDING: "存在仍待验证的观点",
        STATUS_INSUFFICIENT_DATA: "事实不足以审计",
    }.get(result.citation_audit_status, result.citation_audit_status)

    summary.append(
        _clip(
            f"[{TASK_CODE}] 审计覆盖 {result.total_claim_count} 条观点，"
            f"其中 {result.checked_claim_count} 条有对应事实可比对；"
            f"整体结论：{status_label}。",
            _SUMMARY_ENTRY_MAX_CHARS,
        )
    )

    if result.contradicted_flags:
        n = len(result.contradicted_flags)
        detail = result.contradicted_flags[0]
        summary.append(
            _clip(
                f"⚠️ {n} 条观点被半年报事实打脸，"
                f"示例：{detail.claim_text[:50]}（{detail.metric_key}）"
                f"vs 事实 {detail.fact_text[:40]}。",
                _SUMMARY_ENTRY_MAX_CHARS,
            )
        )

    if result.weakened_flags:
        n = len(result.weakened_flags)
        detail = result.weakened_flags[0]
        summary.append(
            _clip(
                f"{n} 条观点被事实削弱，"
                f"示例：{detail.claim_text[:50]}（{detail.metric_key}）。",
                _SUMMARY_ENTRY_MAX_CHARS,
            )
        )

    if result.supported_flags:
        n = len(result.supported_flags)
        summary.append(_clip(f"{n} 条观点被半年报事实支持。", _SUMMARY_ENTRY_MAX_CHARS))

    if result.pending_flags:
        n = len(result.pending_flags)
        detail = result.pending_flags[0]
        summary.append(
            _clip(
                f"{n} 条观点指向未来报告期，仍待验证，"
                f"示例：{detail.claim_text[:50]}。",
                _SUMMARY_ENTRY_MAX_CHARS,
            )
        )

    if result.weak_source_override_blocked_count > 0:
        summary.append(
            _clip(
                f"检测到 {result.weak_source_override_blocked_count} 次"
                "弱来源观点试图覆盖强来源事实，已被审计阻止（KB-014 硬约束）。",
                _SUMMARY_ENTRY_MAX_CHARS,
            )
        )

    if result.fact_period:
        summary.append(
            _clip(
                f"事实基准报告期：{result.fact_period}"
                f"（data_status={result.fact_data_status}，"
                f"source_tier={result.fact_source_tier}）。",
                _SUMMARY_ENTRY_MAX_CHARS,
            )
        )

    if result.needs_tree_work_review:
        summary.append(
            _clip(
                "建议回 Tree Work 复核相关研报观点，更新或标注已过时。"
                "（本审计只描述证据状态，不改变动作语义。）",
                _SUMMARY_ENTRY_MAX_CHARS,
            )
        )

    result.audit_summary = summary[:_SUMMARY_MAX_ENTRIES]


# ── 主入口 ────────────────────────────────────────────────────────────


def audit_citation_against_facts(
    opinion_index: ResearchFactOpinionIndexResult,
    facts: HalfYearFactsQueryResult,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
) -> CitationAuditResult:
    """把研报观点与公告/半年报事实做交叉审计。

    参数:
        opinion_index: KB-015 ``build_research_fact_opinion_index`` 的结果。
        facts: HY-003 ``query_half_year_facts`` 的结果。
        symbol: 标的代码（可选，默认从 facts/opinion_index 推断）。
        name: 标的名称（可选）。

    返回:
        :class:`CitationAuditResult`。永远不会因单条观点审计失败而抛异常。

    约束（对应任务 KB-017）:
        - 事实页 ``data_status`` 不是 fresh/conflict 时，整体 insufficient_data。
        - 缺半年报事实时不得强行判定观点错误（只能 insufficient_data 或 pending）。
        - 弱来源观点不得覆盖强来源事实（``weak_source_blocked`` 标记）。
        - 输出 ``audit_summary`` 不含买卖建议词，TA 动作语义不被 audit 覆写。
    """
    result = CitationAuditResult(
        symbol=(symbol or "").strip(),
        name=(name or "").strip(),
    )

    # 推断 symbol / name。
    if not result.symbol and isinstance(facts, HalfYearFactsQueryResult):
        result.symbol = facts.symbol
    if not result.name and isinstance(facts, HalfYearFactsQueryResult):
        result.name = facts.name
    if isinstance(opinion_index, ResearchFactOpinionIndexResult):
        if not result.symbol and opinion_index.symbols:
            result.symbol = _strip_symbol_to_code(opinion_index.symbols[0])
        if not result.name and opinion_index.names:
            result.name = opinion_index.names[0]

    # 构建 KB-014 tier 优先级事实索引。
    fact_idx = _build_tiered_fact_index(facts)
    result.fact_period = fact_idx.period
    result.fact_data_status = fact_idx.data_status
    result.fact_source_tier = fact_idx.source_tier

    # 事实页不可用 → 整体 insufficient_data（不得强行判定观点错误）。
    if not fact_idx.available:
        result.citation_audit_status = STATUS_INSUFFICIENT_DATA
        result.total_claim_count = 0
        result.checked_claim_count = 0
        if isinstance(facts, HalfYearFactsQueryResult) and facts.status not in (
            STATUS_HAS_DATA,
        ):
            result.errors.append(
                f"半年报事实状态不可用（status={facts.status}），无法做审计。"
            )
        _build_audit_summary(result)
        return result

    # 收集观点。
    claims = _collect_claims(opinion_index)
    risk_claims = _collect_risk_claims(opinion_index)
    result.total_claim_count = len(claims) + len(risk_claims)

    if result.total_claim_count == 0:
        result.citation_audit_status = STATUS_INSUFFICIENT_DATA
        result.errors.append("无可用观点（opinion/forecast/risk）参与审计。")
        _build_audit_summary(result)
        return result

    flags: List[CitationAuditFlag] = []

    # 审计财务指标 / 分业务观点。
    for claim, rel_path, opinion_tier in claims:
        try:
            metric_key = _match_opinion_to_metric(claim.text)
            if metric_key in (
                "revenue",
                "net_profit",
                "gross_margin",
                "operating_cash_flow",
            ):
                flag = _audit_financial_metric_claim(
                    claim, rel_path, opinion_tier, metric_key, fact_idx
                )
            elif metric_key == "segment":
                flag = _audit_segment_claim(
                    claim, rel_path, opinion_tier, fact_idx
                )
            else:
                # other：尝试分业务兜底；否则无法匹配。
                if _extract_segment_topic(claim.text):
                    flag = _audit_segment_claim(
                        claim, rel_path, opinion_tier, fact_idx
                    )
                else:
                    # pending 检测：即使是"other"维度，前瞻指引仍标 pending。
                    if _looks_like_future_period_claim(
                        claim.text, claim.origin_field
                    ):
                        flag = CitationAuditFlag(
                            claim_text=_clip(claim.text),
                            metric_key="other",
                            audit_status=STATUS_PENDING,
                            evidence_level=EVIDENCE_NONE,
                            opinion_source_tier=opinion_tier,
                            fact_source_tier=fact_idx.source_tier,
                            opinion_direction=_classify_direction(claim.text),
                            pending_reason=_clip(
                                "观点指向未来报告期，仍待验证。",
                                _REASON_MAX_CHARS,
                            ),
                            reason=_clip(
                                "观点属前瞻指引，当前事实期未覆盖，仍待验证。",
                                _REASON_MAX_CHARS,
                            ),
                            source_path=rel_path,
                            fact_source_path=fact_idx.source_path,
                        )
                    else:
                        flag = CitationAuditFlag(
                            claim_text=_clip(claim.text),
                            metric_key="other",
                            audit_status=STATUS_INSUFFICIENT_DATA,
                            evidence_level=EVIDENCE_NONE,
                            opinion_source_tier=opinion_tier,
                            fact_source_tier=fact_idx.source_tier,
                            opinion_direction=_classify_direction(claim.text),
                            reason=_clip(
                                "观点未命中已知指标维度，无法做事实审计。",
                                _REASON_MAX_CHARS,
                            ),
                            source_path=rel_path,
                            fact_source_path=fact_idx.source_path,
                        )
            flags.append(flag)
        except Exception as exc:  # pragma: no cover - 容错
            result.errors.append(
                f"观点审计失败（{claim.text[:30]}）: {exc!r}"
            )

    # 审计风险观点。
    for claim, rel_path, opinion_tier in risk_claims:
        try:
            flag = _audit_risk_claim(claim, rel_path, opinion_tier, fact_idx)
            flags.append(flag)
        except Exception as exc:  # pragma: no cover - 容错
            result.errors.append(
                f"风险观点审计失败（{claim.text[:30]}）: {exc!r}"
            )

    # 截断 flag 数量（按状态优先级保留）。
    if len(flags) > _MAX_FLAGS:
        flags.sort(key=lambda f: -_STATUS_PRIORITY.get(f.audit_status, 0))
        flags = flags[:_MAX_FLAGS]

    result.flags = flags
    result.contradicted_flags = [
        f for f in flags if f.audit_status == STATUS_CONTRADICTED
    ]
    result.weakened_flags = [
        f for f in flags if f.audit_status == STATUS_WEAKENED
    ]
    result.supported_flags = [
        f for f in flags if f.audit_status == STATUS_SUPPORTED
    ]
    result.pending_flags = [f for f in flags if f.audit_status == STATUS_PENDING]
    result.insufficient_flags = [
        f for f in flags if f.audit_status == STATUS_INSUFFICIENT_DATA
    ]
    result.checked_claim_count = sum(
        1 for f in flags if f.audit_status != STATUS_INSUFFICIENT_DATA
    )
    result.weak_source_override_blocked_count = sum(
        1 for f in flags if f.weak_source_blocked
    )

    # 聚合状态。
    result.citation_audit_status = _aggregate_audit_status(flags)

    # needs_tree_work_review：有 contradicted 或 ≥2 weakened 或 有弱来源覆盖被阻止。
    result.needs_tree_work_review = bool(
        result.contradicted_flags
        or len(result.weakened_flags) >= 2
        or result.weak_source_override_blocked_count > 0
    )

    _build_audit_summary(result)
    return result


# ── 报告渲染 ──────────────────────────────────────────────────────────


_STATUS_LABELS = {
    STATUS_SUPPORTED: "观点被事实支持",
    STATUS_WEAKENED: "观点被事实削弱",
    STATUS_CONTRADICTED: "观点被事实打脸",
    STATUS_PENDING: "仍待验证",
    STATUS_INSUFFICIENT_DATA: "事实不足以审计",
}

_EVIDENCE_LABELS = {
    EVIDENCE_STRONG: "强",
    EVIDENCE_MODERATE: "中",
    EVIDENCE_WEAK: "弱",
    EVIDENCE_NONE: "无",
}

# 强动作词黑名单（输出文档不含）。
_FORBIDDEN_ACTION_WORDS: Tuple[str, ...] = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL",
    "立即买入", "全仓", "止损",
)


def has_forbidden_action_words(text: str) -> bool:
    """检查文本是否含强动作词（用于报告 lint）。"""
    return any(w in text for w in _FORBIDDEN_ACTION_WORDS)


def render_citation_audit_report(result: CitationAuditResult) -> str:
    """渲染完整的 citation 审计 Markdown 报告。

    只展示摘要和 flag 列表，**不输出买卖建议词**。状态为 insufficient_data 时
    返回最小化报告。
    """
    lines: List[str] = []
    lines.append(f"# 研报观点 vs 公告/半年报事实 citation 审计 — {TASK_CODE}")
    lines.append("")
    lines.append(
        f"- 标的：`{result.symbol}`"
        f"{f' {result.name}' if result.name else ''}"
    )
    status_label = _STATUS_LABELS.get(
        result.citation_audit_status, result.citation_audit_status
    )
    lines.append(
        f"- 整体结论：**{result.citation_audit_status}**（{status_label}）"
    )
    lines.append(
        f"- 审计覆盖：{result.total_claim_count} 条观点，"
        f"{result.checked_claim_count} 条有对应事实可比对"
    )
    if result.fact_period:
        lines.append(
            f"- 事实基准：{result.fact_period}"
            f"（data_status={result.fact_data_status}，"
            f"source_tier={result.fact_source_tier}）"
        )
    if result.weak_source_override_blocked_count > 0:
        lines.append(
            f"- 弱来源覆盖被阻止：{result.weak_source_override_blocked_count} 次"
            "（KB-014 硬约束）"
        )
    if result.needs_tree_work_review:
        lines.append("- **建议回 Tree Work 复核**")
    lines.append("")
    lines.append(
        "> 本审计只做证据一致性标注，不输出买卖建议。缺半年报事实时只标"
        "insufficient_data / pending，不强行判定观点错误。弱来源不得覆盖强来源。"
    )
    lines.append("")

    if result.audit_summary:
        for s in result.audit_summary:
            lines.append(f"- {s}")
        lines.append("")

    if not result.flags:
        lines.append("（无审计 flag）")
        return "\n".join(lines)

    # 按 contradicted > weakened > supported > pending > insufficient 排序展示。
    sorted_flags = sorted(
        result.flags,
        key=lambda f: -_STATUS_PRIORITY.get(f.audit_status, 0),
    )
    for idx, f in enumerate(sorted_flags, 1):
        s_label = _STATUS_LABELS.get(f.audit_status, f.audit_status)
        e_label = _EVIDENCE_LABELS.get(f.evidence_level, f.evidence_level)
        blocked_tag = " · ⚠️弱来源被阻止" if f.weak_source_blocked else ""
        lines.append(
            f"## {idx}. [{f.audit_status}] {f.metric_key} — {s_label}"
            f"（证据：{e_label}{blocked_tag}）"
        )
        lines.append("")
        lines.append(f"- 观点：{f.claim_text}")
        if f.opinion_source_tier:
            lines.append(f"  - 观点来源 tier：{f.opinion_source_tier}")
        if f.opinion_direction != DIR_NEUTRAL:
            lines.append(f"  - 观点方向：{f.opinion_direction}")
        if f.fact_text:
            lines.append(f"- 事实：{f.fact_text}")
            if f.fact_source_tier:
                lines.append(f"  - 事实来源 tier：{f.fact_source_tier}")
            if f.fact_direction != DIR_NEUTRAL:
                lines.append(f"  - 事实方向：{f.fact_direction}")
        else:
            lines.append("- 事实：（无对应记录）")
        if f.reason:
            lines.append(f"- 说明：{f.reason}")
        if f.source_path:
            lines.append(f"- 观点来源：`{f.source_path}`")
        if f.fact_source_path:
            lines.append(f"- 事实来源：`{f.fact_source_path}`")
        lines.append("")

    return "\n".join(lines)


def render_citation_audit_inline(result: CitationAuditResult) -> str:
    """渲染单行内联摘要，适合放入 HY-004 / KB-003 区块。

    格式：``[KB-017] 3 条观点被事实打脸，1 条被削弱，2 条仍待验证，建议复核。``
    无审计结果时返回空串。
    """
    if not isinstance(result, CitationAuditResult):
        return ""
    if result.citation_audit_status == STATUS_INSUFFICIENT_DATA:
        return ""
    parts: List[str] = []
    if result.contradicted_flags:
        parts.append(f"{len(result.contradicted_flags)} 条观点被事实打脸")
    if result.weakened_flags:
        parts.append(f"{len(result.weakened_flags)} 条被削弱")
    if result.supported_flags:
        parts.append(f"{len(result.supported_flags)} 条被支持")
    if result.pending_flags:
        parts.append(f"{len(result.pending_flags)} 条仍待验证")
    if not parts:
        return ""
    suffix = ""
    if result.needs_tree_work_review:
        suffix = "，建议回 Tree Work 复核"
    return f"[{TASK_CODE}] " + "，".join(parts) + suffix + "。"


def audit_to_ta_consumable_summary(
    result: CitationAuditResult,
) -> Dict[str, Any]:
    """把审计结果转为 TA/TradeFlow 可消费的扁平摘要字典。

    供 HY-004 / KB-003 路径直接读取；不携带长正文，只携带统计 + 状态 + 摘要。
    输出**不包含**任何 ``decision`` / ``execution_action`` 字段，TA 动作语义
    不被本审计直接覆写。
    """
    if not isinstance(result, CitationAuditResult):
        return {
            "task": TASK_CODE,
            "citation_audit_status": STATUS_INSUFFICIENT_DATA,
            "counts": {s: 0 for s in ALL_AUDIT_STATUSES},
            "weak_source_override_blocked_count": 0,
            "needs_tree_work_review": False,
            "fact_period": None,
            "fact_source_tier": TIER_UNKNOWN,
            "audit_summary": [],
        }

    counts = {s: 0 for s in ALL_AUDIT_STATUSES}
    for f in result.flags:
        if f.audit_status in counts:
            counts[f.audit_status] += 1

    return {
        "task": TASK_CODE,
        "citation_audit_status": result.citation_audit_status,
        "counts": counts,
        "total_claim_count": result.total_claim_count,
        "checked_claim_count": result.checked_claim_count,
        "weak_source_override_blocked_count": (
            result.weak_source_override_blocked_count
        ),
        "needs_tree_work_review": result.needs_tree_work_review,
        "fact_period": result.fact_period,
        "fact_source_tier": result.fact_source_tier,
        "fact_data_status": result.fact_data_status,
        "audit_summary": list(result.audit_summary),
    }


# ── 便利函数 ──────────────────────────────────────────────────────────


def suggest_report_output_path(
    docs_dir: str = "docs/knowledge_reports",
) -> str:
    """建议的报告输出路径：``docs/knowledge_reports/citation_fact_audit-YYYY-MM-DD.md``。"""
    from datetime import date

    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"citation_fact_audit-{today}.md")


__all__ = [
    "TASK_CODE",
    "STATUS_SUPPORTED",
    "STATUS_WEAKENED",
    "STATUS_CONTRADICTED",
    "STATUS_PENDING",
    "STATUS_INSUFFICIENT_DATA",
    "ALL_AUDIT_STATUSES",
    "EVIDENCE_STRONG",
    "EVIDENCE_MODERATE",
    "EVIDENCE_WEAK",
    "EVIDENCE_NONE",
    "CitationAuditFlag",
    "CitationAuditResult",
    "audit_citation_against_facts",
    "render_citation_audit_report",
    "render_citation_audit_inline",
    "audit_to_ta_consumable_summary",
    "has_forbidden_action_words",
    "suggest_report_output_path",
]
