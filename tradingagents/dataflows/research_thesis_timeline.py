# [KB-018] research_thesis_timeline
"""同股研报观点版本演化与共识漂移时间线。

在 KB-015（研报观点/事实分离）、KB-016（共识/分歧矩阵）和 KB-017（citation 审计）
之上，本模块按**时间**还原同一只股票同一核心观点如何被强化、削弱、反转或自然
过期，避免只看最新一篇研报丢失上下文。

核心输出：
  - ``thesis_version_status`` ∈ ``{new, reinforced, weakened, reversed,
    stale, pending_fact_check}``：每个时间线节点相对前一节点的版本演化状态。
  - ``research_thesis_timeline``：按 (symbol, theme, direction) 聚合的时间线，
    保留 report_date / rel_path / source_quality_tier / citation_audit_status。
  - ``consensus_drift_score`` ∈ [0, 1+]：观点漂移程度（反转 > 削弱 > 增强 >
    重复）。区分**真实新增证据**和**同机构重复报告**（后者不制造漂移）。

设计约束（对应任务 KB-018）：
  - **不调用 live LLM / 不抓取新研报正文**：纯标准库 + 复用 KB-015 / KB-016 /
    KB-017 / HY-005 只读解析结果。
  - **时间线只描述观点和证据变化，不输出买卖动作**：``ThesisVersion`` /
    ``ThesisTimeline`` 不携带 ``decision`` / ``execution_action`` /
    ``action_label`` / ``buy_level``；输出文案刻意避免 ``买入 / 卖出 / 加仓 /
    减仓 / 强烈推荐`` 等强动作词。
  - **同机构重复覆盖必须去重**：同机构 + 同主题 + 同方向 + 文本高度相似 → 标
    ``duplicate_of``，不计入 ``consensus_drift_score``，避免虚假共识增强。
  - **弱来源不得覆盖强来源**：``weak_source_overlay`` 显式标记"弱来源晚于强
    来源出现、试图覆盖"的事件；漂移计分时**强来源优先**，弱来源 overlay 不
    改变 dominant direction。
  - **缺日期节点稳定排序**：无 report_date 的节点排在带日期节点之后（同
    ``rel_path`` 字典序），保证乱序输入得到稳定时间线。
  - **只读本地知识库**：仅用 ``open(..., "r", encoding="utf-8")`` 与
    ``Path.iterdir``，绝不向知识库写文件；不写生产 DB。

版本状态判定规则（按时间线顺序，对每个 thesis 的第 i 个非重复节点 i≥1）：

    prev = 上一个**非重复**节点（同机构重复不计入 prev）
    if 节点页 stale_status == stale:
        status = stale            # 过期，自然衰减
    elif citation_audit_status ∈ {pending, insufficient_data}:
        status = pending_fact_check
    elif direction 与 prev 相反（positive <-> negative）:
        if 节点 tier 严格低于 prev tier:
            weak_source_overlay = True  # 弱来源试图覆盖，被阻止
            status = weakened           # 不让弱来源反转强来源
        else:
            status = reversed
    elif direction 与 prev 相同:
        if 新机构（institution != prev.institution）:
            status = reinforced         # 真实新增证据
        else:
            status = reinforced         # 同机构但确有新证据（非重复）
    else:  # 至少一方 neutral
        status = weakened               # 从明确观点退化为中性

第一个非重复节点 → ``new``。

``consensus_drift_score`` 合成（只统计**非重复**节点）::

    drift = 0.0
    for each相邻非重复节点对 (prev, cur):
        if cur.status == reversed:         drift += 1.0
        elif cur.status == weakened:       drift += 0.5
        elif cur.status == stale:          drift += 0.3
        elif cur.status == pending_fact_check: drift += 0.2
        elif cur.status == reinforced:     drift += 0.0
        # new 不参与（首节点）
    drift = drift / max(1, 非重复节点对数)
    # 重复节点单独计数（duplicate_count），不计入 drift。

使用示例::

    from tradingagents.dataflows.research_thesis_timeline import (
        build_research_thesis_timeline,
        render_research_thesis_timeline_report,
    )
    result = build_research_thesis_timeline(
        "/Users/maybee/Documents/knowledge", symbol="603296"
    )
    print(render_research_thesis_timeline_report(result))
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 复用 KB-001 只读解析助手，保持单一解析实现。
from tradingagents.dataflows.local_knowledge_audit import (
    INVESTMENT_SUBDIR,
    _iter_markdown_files,
    _parse_frontmatter,
    _read_text_safe,
    _safe_str,
    _split_frontmatter,
    default_knowledge_root,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    _normalize_symbol_list,
    _symbol_matches,
)
from tradingagents.dataflows.citation_policy import (  # [KB-014] citation_policy
    OPINION_ONLY_TIERS,
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_OFFICIAL_NOTICE,
    TIER_ORIGINAL_FILING,
    TIER_UNKNOWN,
    TIER_USER_NOTE,
)
from tradingagents.dataflows.research_attention import (  # [KB-007]
    _split_symbol_entry,
)
from tradingagents.dataflows.research_attention_decay import (  # [KB-009]
    split_institution as _kb009_split_institution,
)
from tradingagents.dataflows.research_fact_opinion_index import (  # [KB-015]
    CLAIM_FACT,
    CLAIM_FORECAST,
    CLAIM_OPINION,
    CLAIM_RISK,
    CLAIM_UNKNOWN,
    STALE_FRESH,
    STALE_LOW_CONFIDENCE,
    STALE_STALE,
    ResearchClaimItem,
    ResearchFactOpinionIndexResult,
    ResearchFactOpinionPage,
    build_research_fact_opinion_index,
)
from tradingagents.dataflows.thesis_fact_check import (  # [HY-005] 比对助手
    DIR_NEGATIVE,
    DIR_NEUTRAL,
    DIR_POSITIVE,
    _classify_direction,
    _extract_segment_topic,
    _match_opinion_to_metric,
)
# KB-017 citation audit（可选输入）。
from tradingagents.dataflows.citation_fact_audit import (  # [KB-017]
    STATUS_CONTRADICTED,
    STATUS_INSUFFICIENT_DATA,
    STATUS_PENDING,
    STATUS_SUPPORTED,
    STATUS_WEAKENED,
    CitationAuditResult,
)


# ── 常量 ──────────────────────────────────────────────────────────────

VENDOR = "tree_work_wiki"
TASK_CODE = "KB-018"

# thesis_version_status 枚举（与任务原文严格一致）。
VERSION_NEW = "new"
VERSION_REINFORCED = "reinforced"
VERSION_WEAKENED = "weakened"
VERSION_REVERSED = "reversed"
VERSION_STALE = "stale"
VERSION_PENDING_FACT_CHECK = "pending_fact_check"
ALL_VERSION_STATUSES: Tuple[str, ...] = (
    VERSION_NEW,
    VERSION_REINFORCED,
    VERSION_WEAKENED,
    VERSION_REVERSED,
    VERSION_STALE,
    VERSION_PENDING_FACT_CHECK,
)

# drift 权重（用于 consensus_drift_score 合成）。
_DRIFT_WEIGHT_REVERSED = 1.0
_DRIFT_WEIGHT_WEAKENED = 0.5
_DRIFT_WEIGHT_STALE = 0.3
_DRIFT_WEIGHT_PENDING = 0.2
_DRIFT_WEIGHT_REINFORCED = 0.0

# KB-014 tier 优先级（与 KB-017 保持一致，数字越大优先级越高）。
_TIER_PRIORITY: Dict[str, int] = {
    TIER_ORIGINAL_FILING: 60,
    TIER_OFFICIAL_NOTICE: 50,
    TIER_BROKER_RESEARCH: 40,
    TIER_MEDIA: 20,
    TIER_USER_NOTE: 10,
    TIER_UNKNOWN: 0,
}

# 文本裁剪上限（与 KB-015 / KB-017 保持一致，避免长篇原文）。
_CLAIM_MAX_CHARS = 120
_REASON_MAX_CHARS = 160
_MAX_THESES = 30
_MAX_VERSIONS_PER_THESIS = 15
_MAX_TIMELINE_PAGES = 20

# 强动作词黑名单（输出文档不含）。
_FORBIDDEN_ACTION_WORDS: Tuple[str, ...] = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL",
    "立即买入", "全仓", "止损",
)

# 同机构重复检测：claim 文本归一化后 Jaccard 相似度阈值。
_DUP_TEXT_SIMILARITY_THRESHOLD = 0.6


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class ThesisVersion:
    """时间线上的单个节点（一条观点在某篇研报中的版本）。

    携带报告日期、来源路径、质量等级、citation audit 状态和相对前一节点的
    ``thesis_version_status``。``weak_source_overlay=True`` 表示该节点是
    "弱来源晚于强来源出现、试图覆盖"，被本模块阻止计入漂移。
    """

    rel_path: str
    """观点所在页面相对路径。"""

    claim_text: str
    """观点文本摘要（裁剪到 _CLAIM_MAX_CHARS）。"""

    report_date: Optional[str]
    """报告日期（YYYY-MM-DD）；无日期时为 None。"""

    source_quality_tier: str = TIER_UNKNOWN
    """页面 KB-014 source tier。"""

    stale_status: str = STALE_FRESH
    """页面 stale_status（fresh/stale/low_confidence）。"""

    confidence: str = "low"
    """页面 confidence（high/medium/low）。"""

    institution: str = ""
    """推断的机构名（KB-009 split_institution）。"""

    direction: str = DIR_NEUTRAL
    """本节点的隐含方向（positive/negative/neutral）。"""

    citation_audit_status: str = ""
    """KB-017 citation_audit_status（supported/weakened/contradicted/
    pending/insufficient_data）；未传入 KB-017 结果时为空串。"""

    thesis_version_status: str = VERSION_NEW
    """相对前一非重复节点的版本演化状态。"""

    is_duplicate: bool = False
    """是否为同机构重复覆盖（不计入 drift）。"""

    duplicate_of: str = ""
    """重复时指向被重复的节点 rel_path（首条出现者保留）。"""

    weak_source_overlay: bool = False
    """弱来源晚于强来源、试图覆盖被阻止时为 True。"""

    change_reason: str = ""
    """版本状态变化的可读说明（裁剪到 _REASON_MAX_CHARS）。"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "claim_text": self.claim_text,
            "report_date": self.report_date,
            "source_quality_tier": self.source_quality_tier,
            "stale_status": self.stale_status,
            "confidence": self.confidence,
            "institution": self.institution,
            "direction": self.direction,
            "citation_audit_status": self.citation_audit_status,
            "thesis_version_status": self.thesis_version_status,
            "is_duplicate": self.is_duplicate,
            "duplicate_of": self.duplicate_of,
            "weak_source_overlay": self.weak_source_overlay,
            "change_reason": self.change_reason,
        }


@dataclass
class ThesisTimeline:
    """单条核心观点（同 symbol + theme + 基础方向）的时间线。"""

    symbol_key: str
    """标的代码（与 KB-016 symbol_key 口径一致）。"""

    theme: str
    """主题/指标维度（revenue / net_profit / gross_margin /
    operating_cash_flow / segment / risk / other）。"""

    theme_label: str
    """主题中文标签（业绩/利润/毛利率/现金流/分业务/风险/其他）。"""

    thesis_key: str
    """核心观点指纹（theme + 基础方向 + 主题 token），用于跨节点对齐。"""

    theme_token: str = ""
    """主题细分 token（用于跨节点对齐，如 segment 主题下的"AI服务器"）。"""

    base_direction: str = DIR_NEUTRAL
    """时间线首节点方向（positive/negative/neutral）。"""

    current_direction: str = DIR_NEUTRAL
    """时间线末节点方向（展示当前观点倾向）。"""

    versions: List[ThesisVersion] = field(default_factory=list)
    """按时间顺序排列的版本节点（含重复节点，重复节点标 is_duplicate=True）。"""

    version_count_total: int = 0
    version_count_effective: int = 0
    """非重复节点数（effective = total - duplicate_count）。"""

    duplicate_count: int = 0
    reversed_count: int = 0
    weakened_count: int = 0
    reinforced_count: int = 0
    stale_count: int = 0
    pending_fact_check_count: int = 0
    new_count: int = 0

    consensus_drift_score: float = 0.0
    """本 thesis 的共识漂移分数 ∈ [0, 1+]（基于非重复节点对）。"""

    weak_source_overlay_count: int = 0
    """弱来源试图覆盖被阻止的事件数。"""

    summary: str = ""
    """一句话中性摘要（无强动作词）。"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_key": self.symbol_key,
            "theme": self.theme,
            "theme_token": self.theme_token,
            "theme_label": self.theme_label,
            "thesis_key": self.thesis_key,
            "base_direction": self.base_direction,
            "current_direction": self.current_direction,
            "versions": [v.to_dict() for v in self.versions],
            "version_count_total": self.version_count_total,
            "version_count_effective": self.version_count_effective,
            "duplicate_count": self.duplicate_count,
            "reversed_count": self.reversed_count,
            "weakened_count": self.weakened_count,
            "reinforced_count": self.reinforced_count,
            "stale_count": self.stale_count,
            "pending_fact_check_count": self.pending_fact_check_count,
            "new_count": self.new_count,
            "consensus_drift_score": round(self.consensus_drift_score, 3),
            "weak_source_overlay_count": self.weak_source_overlay_count,
            "summary": self.summary,
        }


@dataclass
class SymbolThesisTimelineResult:
    """单 symbol 的全部 thesis 时间线聚合。"""

    symbol_key: str
    bare_code: str
    name: str
    theses: List[ThesisTimeline] = field(default_factory=list)
    total_versions: int = 0
    effective_versions: int = 0
    duplicate_versions: int = 0
    reversed_versions: int = 0
    weakened_versions: int = 0
    reinforced_versions: int = 0
    stale_versions: int = 0
    pending_fact_check_versions: int = 0
    consensus_drift_score: float = 0.0
    """symbol 级漂移分数：所有 thesis drift 的均值。"""

    weak_source_overlay_count: int = 0
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_key": self.symbol_key,
            "bare_code": self.bare_code,
            "name": self.name,
            "theses": [t.to_dict() for t in self.theses],
            "total_versions": self.total_versions,
            "effective_versions": self.effective_versions,
            "duplicate_versions": self.duplicate_versions,
            "reversed_versions": self.reversed_versions,
            "weakened_versions": self.weakened_versions,
            "reinforced_versions": self.reinforced_versions,
            "stale_versions": self.stale_versions,
            "pending_fact_check_versions": self.pending_fact_check_versions,
            "consensus_drift_score": round(self.consensus_drift_score, 3),
            "weak_source_overlay_count": self.weak_source_overlay_count,
            "summary": self.summary,
        }


@dataclass
class ResearchThesisTimelineResult:
    """整次同股研报观点版本演化时间线构建结果。"""

    status: str = STATUS_NORMAL_NO_DATA
    vendor: str = VENDOR
    task: str = TASK_CODE
    knowledge_root: str = ""
    as_of_date: str = ""
    query: Dict[str, Any] = field(default_factory=dict)
    symbols: List[SymbolThesisTimelineResult] = field(default_factory=list)
    has_citation_audit: bool = False
    """是否消费了 KB-017 citation audit 结果。"""
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vendor": self.vendor,
            "task": self.task,
            "knowledge_root": self.knowledge_root,
            "as_of_date": self.as_of_date,
            "query": dict(self.query),
            "symbols": [s.to_dict() for s in self.symbols],
            "has_citation_audit": self.has_citation_audit,
            "errors": list(self.errors),
        }


# ── 文本工具 ──────────────────────────────────────────────────────────


def _clip_text(text: str, max_chars: int = _CLAIM_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text


def _parse_report_date(raw: Any) -> Optional[date]:
    """解析 ``updated / disclosure_date / 2026-08-30`` 形态的日期。

    返回 ``date`` 或 ``None``。无日期 / 无法解析 → None（不抛异常）。
    """
    if not raw:
        return None
    cleaned = re.sub(r"[/-]", "", str(raw).strip())
    if not re.fullmatch(r"\d{8}", cleaned):
        return None
    try:
        return date(int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]))
    except ValueError:
        return None


def _page_report_date(page: ResearchFactOpinionPage) -> Optional[date]:
    """从 KB-015 page 提取报告日期（report_date 优先）。"""
    return _parse_report_date(page.report_date)


def _split_institution_helper(source_alias: str) -> str:
    """安全调用 KB-009 split_institution。"""
    try:
        return _kb009_split_institution(source_alias) or ""
    except Exception:  # pragma: no cover - 容错
        return ""


def _page_institution(page: ResearchFactOpinionPage) -> str:
    """从 KB-015 page 推断机构名。

    KB-015 page 不直接携带 sources alias，从 title / rel_path 提取（与 KB-016
    口径一致）。提取失败时用 rel_path 作独立 key（避免误折叠）。
    """
    for text in (page.title, page.rel_path):
        if not text:
            continue
        inst = _split_institution_helper(text)
        if inst and inst not in ("wiki", "investment"):
            return inst
    return page.rel_path


_SUFFIX_RE = re.compile(r"\.(SH|SZ|BJ|HK|US|SS)$", re.IGNORECASE)


def _norm_text_for_sim(text: str) -> str:
    """归一化文本用于相似度比对：去空白/标点/数字，保留中文字符。"""
    return re.sub(r"[\s\d\W_]+", "", (text or ""))


def _text_similarity(a: str, b: str) -> float:
    """字符级 Jaccard 相似度（用于同机构重复检测）。

    归一化后取 bigram 集合的 Jaccard 系数。短文本（< 4 char）退化为子集判定。
    """
    na = _norm_text_for_sim(a)
    nb = _norm_text_for_sim(b)
    if not na or not nb:
        return 0.0
    if len(na) < 4 or len(nb) < 4:
        # 短文本：子集判定。
        return 1.0 if na in nb or nb in na else 0.0
    def _bigrams(s: str) -> set:
        return {s[i:i + 2] for i in range(len(s) - 1)}
    sa, sb = _bigrams(na), _bigrams(nb)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    return inter / union


def _tier_priority(tier: str) -> int:
    return _TIER_PRIORITY.get(tier or TIER_UNKNOWN, 0)


# ── 主题与 thesis_key 合成 ────────────────────────────────────────────


_THEME_LABELS: Dict[str, str] = {
    "revenue": "营收",
    "net_profit": "利润",
    "gross_margin": "毛利率",
    "operating_cash_flow": "现金流",
    "segment": "分业务",
    "risk": "风险",
    "other": "其他",
}


def _claim_theme(claim: ResearchClaimItem) -> Tuple[str, str]:
    """从单条 claim 推断 (theme, theme_token)。

    - risk 类 claim → (``risk``, 提取的风险关键词)。
    - forward_guidance / body:forecast → 按文本匹配 metric（前瞻指引常含
      营收/利润展望）。
    - 其余 → ``_match_opinion_to_metric`` + ``_extract_segment_topic`` 兜底。

    返回 ``(theme, theme_token)``。theme_token 用于 thesis_key 合成，使同主题
    下不同细分（如 "AI服务器" vs "手机业务"）能各自成线。
    """
    if claim.claim_type == CLAIM_RISK or claim.origin_field in (
        "risk_factors", "body:risk",
    ):
        kw = _extract_risk_keyword(claim.text) or "risk"
        return ("risk", kw)
    metric = _match_opinion_to_metric(claim.text)
    if metric in ("revenue", "net_profit", "gross_margin",
                  "operating_cash_flow"):
        return (metric, metric)
    if metric == "segment":
        seg = _extract_segment_topic(claim.text) or "segment"
        return ("segment", seg)
    # other：尝试分业务兜底，否则归 other。
    seg = _extract_segment_topic(claim.text)
    if seg:
        return ("segment", seg)
    return ("other", "other")


def _extract_risk_keyword(text: str) -> str:
    """从风险观点中提取一个关键词作为 theme_token（与 KB-017 口径一致）。"""
    if not text:
        return ""
    for kw in (
        "客户集中度", "上游", "GPU", "汇率", "原材料", "客户流失",
        "竞争加剧", "技术替代", "政策", "需求", "库存", "应收",
    ):
        if kw in text:
            return kw
    return ""


def _thesis_key(symbol: str, theme: str, theme_token: str,
                direction: str) -> str:
    """合成 thesis_key：``symbol|theme:token|direction``。

    方向进 key 是为了让 positive / negative 各自成线（反转时新节点会落到**相反
    方向的 thesis**？—— 不，反转应留在同一条线里才能体现"反转"。所以方向**不进
    key**，base_direction 用首节点方向固化，反转由 status=reversed 体现）。
    """
    return f"{symbol}|{theme}:{theme_token}"


# ── 单 claim → 候选节点 ───────────────────────────────────────────────


@dataclass
class _CandidateNode:
    """时间线构建中间结构：单条 claim + 所属 page 的元数据。"""

    symbol: str
    name: str
    rel_path: str
    claim: ResearchClaimItem
    report_date: Optional[date]
    report_date_raw: Optional[str]
    source_quality_tier: str
    stale_status: str
    confidence: str
    institution: str
    theme: str
    theme_token: str
    direction: str


def _collect_candidate_nodes(
    pages: List[ResearchFactOpinionPage],
    *,
    symbol: str,
    name: str,
) -> List[_CandidateNode]:
    """从 KB-015 pages 收集所有可入线的 claim 节点。

    入线范围（与 KB-016 / KB-017 对齐）：
      - research_claims（opinion/unknown）
      - forecast_items（前瞻指引）
      - risk_items（风险）
      - reported_facts 中**含方向词**的 claim（兜底：broker 事实字段含方向）

    neutral 方向的 claim 仍入线（体现"从明确观点退化为中性"的削弱路径），
    但只在没有非 neutral 同 thesis 节点时单独成线（避免噪音）。
    """
    nodes: List[_CandidateNode] = []
    for page in pages:
        rd = _page_report_date(page)
        institution = _page_institution(page)
        candidates: List[ResearchClaimItem] = []
        candidates.extend(page.research_claims)
        candidates.extend(page.forecast_items)
        candidates.extend(page.risk_items)
        for fact in page.reported_facts:
            if _classify_direction(fact.text) != DIR_NEUTRAL:
                candidates.append(fact)
        for claim in candidates:
            if not (claim.text or "").strip():
                continue
            theme, token = _claim_theme(claim)
            direction = _classify_direction(claim.text)
            nodes.append(_CandidateNode(
                symbol=symbol,
                name=name,
                rel_path=page.rel_path,
                claim=claim,
                report_date=rd,
                report_date_raw=page.report_date,
                source_quality_tier=(
                    claim.source_quality_tier or page.source_quality_tier
                    or TIER_UNKNOWN
                ),
                stale_status=(
                    claim.stale_status or page.stale_status or STALE_FRESH
                ),
                confidence=page.confidence or "low",
                institution=institution,
                theme=theme,
                theme_token=token,
                direction=direction,
            ))
    return nodes


# ── citation audit 状态映射 ───────────────────────────────────────────


def _build_claim_audit_map(
    audit: Optional[CitationAuditResult],
) -> Dict[Tuple[str, str], str]:
    """从 KB-017 CitationAuditResult 构建 (rel_path, claim_text) → status 映射。

    KB-017 的 flag 携带 ``source_path``（= KB-015 page rel_path）和裁剪后的
    ``claim_text``。这里用裁剪后的文本做 key（与 KB-015 claim.text 裁剪口径一致）。
    未传入 audit 时返回空 dict（thesis_version_status 不走 pending_fact_check
    分支，但仍可从 stale_status 判定 stale）。
    """
    out: Dict[Tuple[str, str], str] = {}
    if not isinstance(audit, CitationAuditResult):
        return out
    for flag in audit.flags:
        key = (flag.source_path or "", (flag.claim_text or "").strip())
        if key[1]:
            out[key] = flag.audit_status
    return out


def _lookup_audit_status(
    audit_map: Dict[Tuple[str, str], str],
    rel_path: str,
    claim_text: str,
) -> str:
    """查 citation_audit_status；缺失返回空串。"""
    key = (rel_path or "", (claim_text or "").strip())
    return audit_map.get(key, "")


# ── 同机构重复检测 ────────────────────────────────────────────────────


def _find_duplicate_prev(
    node: _CandidateNode,
    prev_nodes: List[_CandidateNode],
) -> Optional[int]:
    """在同 thesis 的已有非重复节点里找"同机构重复覆盖"。

    判定（保守，宁可漏标也不误杀真实新增证据）：
      - 同 institution + 同 rel_path + **文本相似度 ≥ 阈值** → 重复
        （同篇报告里 opinion/forecast 不同视角的 claim 不算重复）。
      - 同 institution + 不同 rel_path + 文本相似度 ≥ 阈值 → 重复
        （同机构多份报告重复同一观点）。

    返回被重复节点的 prev_nodes 索引；未命中返回 None。
    """
    if not node.institution:
        return None
    for i, prev in enumerate(prev_nodes):
        if prev.institution != node.institution:
            continue
        sim = _text_similarity(prev.claim.text, node.claim.text)
        if sim >= _DUP_TEXT_SIMILARITY_THRESHOLD:
            return i
    return None


# ── 版本状态判定 ──────────────────────────────────────────────────────


def _decide_version_status(
    node: _CandidateNode,
    prev: Optional[_CandidateNode],
    audit_status: str,
) -> Tuple[str, bool, str]:
    """判定单节点相对前一非重复节点的 (status, weak_source_overlay, reason)。

    优先级：stale > pending_fact_check > reversed（含弱来源覆盖阻止）> weakened
    > reinforced > new。
    """
    # 1) 过期：自然衰减（stale_status 来自 KB-015）。
    if node.stale_status == STALE_STALE:
        return (
            VERSION_STALE,
            False,
            _clip_text(
                "页面已过期（stale_risk=高 或 valid_until 过期），"
                "观点自然衰减不计入真实漂移。",
                _REASON_MAX_CHARS,
            ),
        )

    # 2) citation audit 待验证：观点指向未来报告期或缺对应事实。
    if audit_status in (STATUS_PENDING, STATUS_INSUFFICIENT_DATA):
        return (
            VERSION_PENDING_FACT_CHECK,
            False,
            _clip_text(
                f"KB-017 citation audit={audit_status}，观点仍待事实核验。",
                _REASON_MAX_CHARS,
            ),
        )

    if prev is None:
        # 首个非重复节点（同时非 stale / pending）。
        return (VERSION_NEW, False, "首条观点（无前置版本）。")

    prev_tier_pri = _tier_priority(prev.source_quality_tier)
    cur_tier_pri = _tier_priority(node.source_quality_tier)

    # 3) 方向反转。
    if (
        (prev.direction == DIR_POSITIVE and node.direction == DIR_NEGATIVE)
        or (prev.direction == DIR_NEGATIVE and node.direction == DIR_POSITIVE)
    ):
        # 弱来源不得覆盖强来源：当前 tier 严格低于 prev → 阻止反转。
        if cur_tier_pri < prev_tier_pri:
            return (
                VERSION_WEAKENED,
                True,
                _clip_text(
                    f"弱来源（{node.source_quality_tier}）晚于强来源"
                    f"（{prev.source_quality_tier}）出现且方向相反，"
                    "阻止覆盖，计为削弱而非反转。",
                    _REASON_MAX_CHARS,
                ),
            )
        return (
            VERSION_REVERSED,
            False,
            _clip_text(
                f"方向由 {prev.direction} 反转为 {node.direction}，"
                "观点出现实质性反转。",
                _REASON_MAX_CHARS,
            ),
        )

    # 4) 同方向：增强（新机构或同机构新证据）。
    if node.direction == prev.direction and node.direction != DIR_NEUTRAL:
        same_inst = node.institution == prev.institution
        inst_note = (
            "同机构确有新证据" if same_inst else "新机构加入，真实新增证据"
        )
        return (
            VERSION_REINFORCED,
            False,
            _clip_text(
                f"方向延续（{node.direction}），{inst_note}，观点被强化。",
                _REASON_MAX_CHARS,
            ),
        )

    # 5) 至少一方 neutral：从明确观点退化为中性 → 削弱。
    return (
        VERSION_WEAKENED,
        False,
        _clip_text(
            f"方向由 {prev.direction} 变为 {node.direction}（中性化），"
            "观点强度下降。",
            _REASON_MAX_CHARS,
        ),
    )


# ── drift 计分 ────────────────────────────────────────────────────────


def _drift_weight(status: str) -> float:
    return {
        VERSION_REVERSED: _DRIFT_WEIGHT_REVERSED,
        VERSION_WEAKENED: _DRIFT_WEIGHT_WEAKENED,
        VERSION_STALE: _DRIFT_WEIGHT_STALE,
        VERSION_PENDING_FACT_CHECK: _DRIFT_WEIGHT_PENDING,
        VERSION_REINFORCED: _DRIFT_WEIGHT_REINFORCED,
        VERSION_NEW: 0.0,
    }.get(status, 0.0)


def _compute_drift_score(versions: List[ThesisVersion]) -> float:
    """基于非重复节点对的 drift 均值。

    首节点（new）不参与；重复节点（is_duplicate=True）不参与。若无有效节点对，
    返回 0.0。
    """
    effective = [v for v in versions if not v.is_duplicate]
    if len(effective) < 2:
        return 0.0
    pairs = effective[1:]  # 从第二个非重复节点开始。
    total = sum(_drift_weight(v.thesis_version_status) for v in pairs)
    return total / len(pairs)


# ── 单 thesis 时间线构建 ──────────────────────────────────────────────


def _sort_key(node: _CandidateNode) -> Tuple[int, int, date, str]:
    """稳定排序键：(date_known, report_date, rel_path)。

    - 有日期的节点排在前面（date_known=0），无日期的排后面（date_known=1）。
    - 无日期节点之间按 rel_path 字典序，保证乱序输入稳定。
    """
    date_known = 0 if node.report_date is not None else 1
    fallback_date = node.report_date or date(1970, 1, 1)
    return (date_known, 0, fallback_date, node.rel_path)


def _build_thesis_timeline(
    symbol: str,
    name: str,
    theme: str,
    theme_token: str,
    nodes: List[_CandidateNode],
    *,
    audit_map: Dict[Tuple[str, str], str],
) -> ThesisTimeline:
    """从同 thesis 的候选节点构建时间线。

    步骤：
      1. 按 _sort_key 稳定排序。
      2. 逐节点判定 is_duplicate / thesis_version_status / weak_source_overlay。
      3. 聚合统计 + drift + summary。
    """
    sorted_nodes = sorted(nodes, key=_sort_key)
    theme_label = _THEME_LABELS.get(theme, theme)
    # nodes 分组时已按 (theme, theme_token) 聚合，取首节点的 token。
    theme_token = nodes[0].theme_token if nodes else theme
    thesis_key = _thesis_key(symbol, theme, theme_token, "")

    versions: List[ThesisVersion] = []
    # 非重复节点列表（用于 prev 查找）。
    effective_nodes: List[_CandidateNode] = []

    for node in sorted_nodes:
        audit_status = _lookup_audit_status(
            audit_map, node.rel_path, node.claim.text,
        )
        # 同机构重复检测：只在非 stale / pending 路径前做（重复节点仍记录）。
        dup_idx = _find_duplicate_prev(node, effective_nodes)

        if dup_idx is not None:
            prev_node = effective_nodes[dup_idx]
            versions.append(ThesisVersion(
                rel_path=node.rel_path,
                claim_text=_clip_text(node.claim.text),
                report_date=node.report_date_raw,
                source_quality_tier=node.source_quality_tier,
                stale_status=node.stale_status,
                confidence=node.confidence,
                institution=node.institution,
                direction=node.direction,
                citation_audit_status=audit_status,
                thesis_version_status=(
                    VERSION_STALE
                    if node.stale_status == STALE_STALE
                    else VERSION_REINFORCED  # 同机构重复默认标强化，
                    # 但 is_duplicate=True，不计入 drift。
                ),
                is_duplicate=True,
                duplicate_of=prev_node.rel_path,
                weak_source_overlay=False,
                change_reason=_clip_text(
                    f"同机构「{node.institution}」重复覆盖，"
                    "保留首条，不计入真实漂移。",
                    _REASON_MAX_CHARS,
                ),
            ))
            continue

        # 非重复节点：判定版本状态。
        prev = effective_nodes[-1] if effective_nodes else None
        status, weak_overlay, reason = _decide_version_status(
            node, prev, audit_status,
        )
        versions.append(ThesisVersion(
            rel_path=node.rel_path,
            claim_text=_clip_text(node.claim.text),
            report_date=node.report_date_raw,
            source_quality_tier=node.source_quality_tier,
            stale_status=node.stale_status,
            confidence=node.confidence,
            institution=node.institution,
            direction=node.direction,
            citation_audit_status=audit_status,
            thesis_version_status=status,
            is_duplicate=False,
            duplicate_of="",
            weak_source_overlay=weak_overlay,
            change_reason=reason,
        ))
        effective_nodes.append(node)

    # 聚合统计。
    timeline = ThesisTimeline(
        symbol_key=symbol,
        theme=theme,
        theme_token=theme_token,
        theme_label=theme_label,
        thesis_key=thesis_key,
        versions=versions,
    )
    timeline.version_count_total = len(versions)
    timeline.version_count_effective = sum(
        1 for v in versions if not v.is_duplicate
    )
    timeline.duplicate_count = sum(1 for v in versions if v.is_duplicate)
    timeline.reversed_count = sum(
        1 for v in versions
        if v.thesis_version_status == VERSION_REVERSED
    )
    timeline.weakened_count = sum(
        1 for v in versions
        if v.thesis_version_status == VERSION_WEAKENED
    )
    timeline.reinforced_count = sum(
        1 for v in versions
        if v.thesis_version_status == VERSION_REINFORCED
        and not v.is_duplicate
    )
    timeline.stale_count = sum(
        1 for v in versions
        if v.thesis_version_status == VERSION_STALE
    )
    timeline.pending_fact_check_count = sum(
        1 for v in versions
        if v.thesis_version_status == VERSION_PENDING_FACT_CHECK
    )
    timeline.new_count = sum(
        1 for v in versions
        if v.thesis_version_status == VERSION_NEW
    )
    timeline.weak_source_overlay_count = sum(
        1 for v in versions if v.weak_source_overlay
    )
    timeline.consensus_drift_score = _compute_drift_score(versions)
    # base / current direction。
    effective_versions = [v for v in versions if not v.is_duplicate]
    if effective_versions:
        timeline.base_direction = effective_versions[0].direction
        timeline.current_direction = effective_versions[-1].direction
    timeline.summary = _render_thesis_summary(timeline)
    return timeline


def _render_thesis_summary(timeline: ThesisTimeline) -> str:
    """渲染单 thesis 的中性摘要（含负面信息，无强动作词）。"""
    dir_label = {
        DIR_POSITIVE: "偏正面",
        DIR_NEGATIVE: "偏负面",
        DIR_NEUTRAL: "中性",
    }.get(timeline.current_direction, timeline.current_direction)
    parts: List[str] = [
        f"{timeline.theme_label}主题时间线：{timeline.version_count_total}"
        f" 个版本（有效 {timeline.version_count_effective}、"
        f"重复 {timeline.duplicate_count}），当前倾向 {dir_label}，"
        f"drift={timeline.consensus_drift_score:.2f}"
    ]
    negatives: List[str] = []
    if timeline.reversed_count:
        negatives.append(f"反转 {timeline.reversed_count}")
    if timeline.weakened_count:
        negatives.append(f"削弱 {timeline.weakened_count}")
    if timeline.stale_count:
        negatives.append(f"过期 {timeline.stale_count}")
    if timeline.pending_fact_check_count:
        negatives.append(f"待验证 {timeline.pending_fact_check_count}")
    if timeline.weak_source_overlay_count:
        negatives.append(
            f"弱来源覆盖被阻止 {timeline.weak_source_overlay_count}"
        )
    if negatives:
        parts.append("变化：" + "、".join(negatives))
    return "；".join(parts) + "。"


# ── 单 symbol 时间线构建 ──────────────────────────────────────────────


def _build_symbol_timeline(
    symbol_key: str,
    bare_code: str,
    name: str,
    pages: List[ResearchFactOpinionPage],
    *,
    audit: Optional[CitationAuditResult],
) -> SymbolThesisTimelineResult:
    """从 KB-015 pages 构建单 symbol 的全部 thesis 时间线。"""
    result = SymbolThesisTimelineResult(
        symbol_key=symbol_key,
        bare_code=bare_code,
        name=name,
    )
    audit_map = _build_claim_audit_map(audit)

    nodes = _collect_candidate_nodes(pages, symbol=symbol_key, name=name)
    if not nodes:
        return result

    # 按 (theme, theme_token) 分组。
    grouped: Dict[Tuple[str, str], List[_CandidateNode]] = {}
    for node in nodes:
        key = (node.theme, node.theme_token)
        grouped.setdefault(key, []).append(node)

    theses: List[ThesisTimeline] = []
    for (theme, token), group_nodes in grouped.items():
        # 只保留 non-neutral 优先的 thesis；若全部 neutral 也保留（体现衰减）。
        try:
            timeline = _build_thesis_timeline(
                symbol_key, name, theme, token, group_nodes,
                audit_map=audit_map,
            )
        except Exception as exc:  # pragma: no cover - 容错
            result_summary = (
                f"{symbol_key} {theme}:{token} 时间线构建失败 {exc!r}"
            )
            # 通过 summary 字段记录错误（不抛异常）。
            timeline = ThesisTimeline(
                symbol_key=symbol_key,
                theme=theme,
                theme_token=token,
                theme_label=_THEME_LABELS.get(theme, theme),
                thesis_key=_thesis_key(symbol_key, theme, token, ""),
                summary=result_summary,
            )
        if timeline.versions:
            theses.append(timeline)

    # 排序：drift 高的优先；其次版本数多；最后 thesis_key。
    theses.sort(
        key=lambda t: (
            -t.consensus_drift_score,
            -t.version_count_effective,
            t.thesis_key,
        )
    )
    # 截断到上限（防爆炸）。
    if len(theses) > _MAX_THESES:
        theses = theses[:_MAX_THESES]

    result.theses = theses
    # 聚合 symbol 级统计。
    result.total_versions = sum(t.version_count_total for t in theses)
    result.effective_versions = sum(
        t.version_count_effective for t in theses
    )
    result.duplicate_versions = sum(t.duplicate_count for t in theses)
    result.reversed_versions = sum(t.reversed_count for t in theses)
    result.weakened_versions = sum(t.weakened_count for t in theses)
    result.reinforced_versions = sum(t.reinforced_count for t in theses)
    result.stale_versions = sum(t.stale_count for t in theses)
    result.pending_fact_check_versions = sum(
        t.pending_fact_check_count for t in theses
    )
    result.weak_source_overlay_count = sum(
        t.weak_source_overlay_count for t in theses
    )
    if theses:
        result.consensus_drift_score = (
            sum(t.consensus_drift_score for t in theses) / len(theses)
        )
    result.summary = _render_symbol_summary(result)
    return result


def _render_symbol_summary(result: SymbolThesisTimelineResult) -> str:
    """渲染单 symbol 的中性摘要（无强动作词）。"""
    if not result.theses:
        return f"{result.symbol_key} {result.name}：无可用观点时间线。"
    parts: List[str] = [
        f"{result.symbol_key} {result.name}："
        f"{len(result.theses)} 条主题时间线，"
        f"共 {result.total_versions} 个版本"
        f"（有效 {result.effective_versions}、"
        f"重复 {result.duplicate_versions}），"
        f"drift={result.consensus_drift_score:.2f}"
    ]
    flags: List[str] = []
    if result.reversed_versions:
        flags.append(f"反转 {result.reversed_versions}")
    if result.weakened_versions:
        flags.append(f"削弱 {result.weakened_versions}")
    if result.stale_versions:
        flags.append(f"过期 {result.stale_versions}")
    if result.pending_fact_check_versions:
        flags.append(f"待验证 {result.pending_fact_check_versions}")
    if result.weak_source_overlay_count:
        flags.append(
            f"弱来源覆盖被阻止 {result.weak_source_overlay_count}"
        )
    if flags:
        parts.append("变化：" + "、".join(flags))
    return "；".join(parts) + "。"


# ── 主入口 ────────────────────────────────────────────────────────────


def build_research_thesis_timeline(
    knowledge_root: str,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    citation_audit: Optional[CitationAuditResult] = None,
    today: Optional[date] = None,
) -> ResearchThesisTimelineResult:
    """构建同股研报观点版本演化时间线。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        symbol: A 股代码或代码+后缀（如 ``603296`` / ``603296.SH``）。
            ``None`` 时不限 symbol（全库索引模式）。
        name: 公司简称（与页面 symbols 简称或 title 子串匹配）。
        citation_audit: 可选的 KB-017 ``CitationAuditResult``（单 symbol 时）。
            传入时每个节点携带 ``citation_audit_status``，并触发
            ``pending_fact_check`` 状态分支。**调用方负责确保 audit 与 symbol
            对齐**（本模块不重新跑 KB-017）。
        today: 用于 stale 判定的基准日期（测试注入）；``None`` 用
            ``date.today()``。

    返回:
        :class:`ResearchThesisTimelineResult`。单页解析失败不抛异常，记入
        ``errors``。知识库不存在时返回 status=FAILED。

    约束（对应任务 KB-018）:
        - 不调用 live LLM，不抓取新研报正文。
        - 时间线只描述观点和证据变化，不输出买卖动作。
        - 同机构重复覆盖去重（不计入 drift）。
        - 弱来源不得覆盖强来源（``weak_source_overlay`` 显式记录）。
        - 乱序输入得到稳定时间线。
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
    result = ResearchThesisTimelineResult(
        knowledge_root=str(root),
        as_of_date=today.strftime("%Y-%m-%d"),
        query=query_desc,
        has_citation_audit=isinstance(citation_audit, CitationAuditResult),
    )

    if not root.exists():
        result.status = STATUS_FAILED
        result.errors.append(f"knowledge_root 不存在: {root}")
        return result

    investment_dir = root / INVESTMENT_SUBDIR
    if not investment_dir.exists():
        result.errors.append(f"investment 分区不存在: {investment_dir}")
        result.status = STATUS_NORMAL_NO_DATA
        return result

    md_files = _iter_markdown_files(investment_dir)
    if not md_files:
        result.status = STATUS_NORMAL_NO_DATA
        return result

    # ── 1. 逐页读 frontmatter 做 symbol/name 预筛 → 分组 ──
    grouped: Dict[str, Dict[str, Any]] = {}
    for md in md_files:
        rel = str(md.relative_to(root))
        try:
            text = _read_text_safe(md)
            fm_text, _body = _split_frontmatter(text)
            frontmatter = _parse_frontmatter(fm_text)
        except Exception as exc:  # pragma: no cover - 容错
            result.errors.append(f"{rel}: frontmatter 解析失败 {exc!r}")
            continue

        page_symbols = _normalize_symbol_list(frontmatter.get("symbols"))
        title = _safe_str(frontmatter.get("title")) or md.stem

        matched = False
        if not symbol and not name:
            matched = True
        elif symbol and _symbol_matches(symbol, page_symbols):
            matched = True
        elif name:
            q = name.lower()
            if q in (title or "").lower():
                matched = True
            for entry in page_symbols:
                parts = entry.split(maxsplit=1)
                sname = parts[1] if len(parts) > 1 else ""
                if sname and q in sname.lower():
                    matched = True
                    break

        if not matched:
            continue

        for entry in page_symbols:
            code_norm, bare, sname = _split_symbol_entry(entry)
            if not code_norm and not sname:
                continue
            skey = code_norm or sname
            if symbol:
                if not _symbol_matches(symbol, [entry]):
                    continue
            if skey not in grouped:
                grouped[skey] = {
                    "bare_code": bare,
                    "name": sname,
                    "paths": [],
                }
            grouped[skey]["paths"].append(rel)

    if not grouped:
        result.status = STATUS_NORMAL_NO_DATA
        return result

    # ── 2. 按 symbol 分组构建 KB-015 index + timeline ──
    sym_results: List[SymbolThesisTimelineResult] = []
    for skey, info in grouped.items():
        try:
            kb015_result = build_research_fact_opinion_index(
                knowledge_root,
                symbol=skey,
                name=info.get("name") or None,
                today=today,
                max_pages=_MAX_TIMELINE_PAGES,
            )
        except Exception as exc:  # pragma: no cover
            result.errors.append(f"{skey}: KB-015 索引失败 {exc!r}")
            continue

        if not kb015_result.pages:
            continue
        result.errors.extend(kb015_result.errors or [])

        # citation_audit 只在单 symbol 且 symbol 对齐时消费。
        audit_for_sym: Optional[CitationAuditResult] = None
        if isinstance(citation_audit, CitationAuditResult):
            if _symbol_equivalent(skey, citation_audit.symbol):
                audit_for_sym = citation_audit

        try:
            sym_res = _build_symbol_timeline(
                skey,
                info.get("bare_code", ""),
                info.get("name", ""),
                kb015_result.pages,
                audit=audit_for_sym,
            )
        except Exception as exc:  # pragma: no cover
            result.errors.append(f"{skey}: 时间线构建失败 {exc!r}")
            continue
        if sym_res.theses:
            sym_results.append(sym_res)

    # 排序：drift 高优先；其次 effective_versions 多；最后 symbol_key。
    sym_results.sort(
        key=lambda s: (
            -s.consensus_drift_score,
            -s.effective_versions,
            s.symbol_key,
        )
    )
    result.symbols = sym_results

    # 状态机。
    if not sym_results:
        result.status = STATUS_NORMAL_NO_DATA
    else:
        # 任一 symbol 有非 stale 版本 → HAS_DATA；否则按 stale/low 聚合。
        any_non_stale = any(
            v.thesis_version_status != VERSION_STALE
            for s in sym_results
            for t in s.theses
            for v in t.versions
            if not v.is_duplicate
        )
        any_fresh = any(
            v.stale_status == STALE_FRESH
            for s in sym_results for t in s.theses for v in t.versions
        )
        if any_fresh and any_non_stale:
            result.status = STATUS_HAS_DATA
        elif any_fresh:
            result.status = STATUS_LOW_CONFIDENCE
        else:
            result.status = STATUS_STALE

    return result


# ── 单 symbol 查询便利 ────────────────────────────────────────────────


def lookup_research_thesis_timeline(
    knowledge_root: str,
    symbol: Optional[str],
    *,
    citation_audit: Optional[CitationAuditResult] = None,
    today: Optional[date] = None,
) -> Optional[SymbolThesisTimelineResult]:
    """单 symbol 查询观点演化时间线。

    无命中返回 ``None``；永远不会因单页解析失败而抛异常。
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return None
    result = build_research_thesis_timeline(
        knowledge_root, symbol=symbol,
        citation_audit=citation_audit, today=today,
    )
    for s in result.symbols:
        if _symbol_equivalent(symbol, s.symbol_key, s.bare_code):
            return s
    return None


def _symbol_equivalent(
    query: str, sym_key: str, bare_code: str = "",
) -> bool:
    """宽松等价判定（与 KB-016 口径一致）。"""
    if not query or not sym_key:
        return False
    q = query.strip().upper()
    s = sym_key.strip().upper()
    if q == s:
        return True
    q_bare = _SUFFIX_RE.sub("", q).strip()
    s_bare = _SUFFIX_RE.sub("", s).strip()
    if q_bare and s_bare and q_bare == s_bare:
        return True
    if s_bare and s_bare.isdigit():
        return False
    if bare_code and q_bare and q_bare == bare_code.upper():
        return True
    return False


# ── TA 可消费扁平摘要 ────────────────────────────────────────────────


def timeline_to_ta_consumable_summary(
    sym: Optional[SymbolThesisTimelineResult],
) -> Dict[str, Any]:
    """把单 symbol 时间线转为 TA/TradeFlow 可复用的扁平字典。

    无命中（``sym is None``）时返回空结构（NORMAL_NO_DATA）。
    **不携带 decision / action_label / buy_level**，调用方决定动作语义。
    """
    if sym is None:
        return {
            "has_hit": False,
            "consensus_drift_score": 0.0,
            "total_versions": 0,
            "effective_versions": 0,
            "reversed_versions": 0,
            "weakened_versions": 0,
            "reinforced_versions": 0,
            "stale_versions": 0,
            "pending_fact_check_versions": 0,
            "weak_source_overlay_count": 0,
            "theses_brief": [],
            "timeline_summary": "",
        }
    theses_brief: List[Dict[str, Any]] = []
    for t in sym.theses:
        # 每条 thesis 只输出摘要 + 首末节点路径，不复制 claim 原文。
        first = next(
            (v for v in t.versions if not v.is_duplicate), None
        )
        last = next(
            (v for v in reversed(t.versions) if not v.is_duplicate), None
        )
        theses_brief.append({
            "thesis_key": t.thesis_key,
            "theme": t.theme,
            "theme_label": t.theme_label,
            "base_direction": t.base_direction,
            "current_direction": t.current_direction,
            "consensus_drift_score": round(t.consensus_drift_score, 3),
            "version_count_effective": t.version_count_effective,
            "reversed_count": t.reversed_count,
            "weakened_count": t.weakened_count,
            "reinforced_count": t.reinforced_count,
            "stale_count": t.stale_count,
            "pending_fact_check_count": t.pending_fact_check_count,
            "weak_source_overlay_count": t.weak_source_overlay_count,
            "first_version_path": first.rel_path if first else "",
            "latest_version_path": last.rel_path if last else "",
            "first_report_date": first.report_date if first else None,
            "latest_report_date": last.report_date if last else None,
        })
    return {
        "has_hit": True,
        "consensus_drift_score": round(sym.consensus_drift_score, 3),
        "total_versions": sym.total_versions,
        "effective_versions": sym.effective_versions,
        "reversed_versions": sym.reversed_versions,
        "weakened_versions": sym.weakened_versions,
        "reinforced_versions": sym.reinforced_versions,
        "stale_versions": sym.stale_versions,
        "pending_fact_check_versions": sym.pending_fact_check_versions,
        "weak_source_overlay_count": sym.weak_source_overlay_count,
        "theses_brief": theses_brief,
        "timeline_summary": sym.summary,
    }


# ── 报告渲染 ──────────────────────────────────────────────────────────


_STATUS_LABELS = {
    STATUS_HAS_DATA: "已生成观点演化时间线",
    STATUS_NORMAL_NO_DATA: "无研报观点命中",
    STATUS_FAILED: "观点演化时间线构建失败",
    STATUS_STALE: "研报观点全部已过期（仅供参考）",
    STATUS_LOW_CONFIDENCE: "研报观点低置信（仅供参考）",
}

_VERSION_LABELS = {
    VERSION_NEW: "新增",
    VERSION_REINFORCED: "强化",
    VERSION_WEAKENED: "削弱",
    VERSION_REVERSED: "反转",
    VERSION_STALE: "过期",
    VERSION_PENDING_FACT_CHECK: "待验证",
}

_DIRECTION_LABELS = {
    DIR_POSITIVE: "偏正面",
    DIR_NEGATIVE: "偏负面",
    DIR_NEUTRAL: "中性",
}


def render_research_thesis_timeline_report(
    result: ResearchThesisTimelineResult,
    *,
    top: int = 20,
) -> str:
    """渲染完整的观点演化时间线 Markdown 报告。

    只展示摘要和路径，不展示长正文。状态为 NORMAL_NO_DATA / FAILED 时返回
    最小化报告（不抛异常）。**不含买卖建议或强动作词**。
    """
    lines: List[str] = []
    lines.append(f"# 研报观点演化时间线报告 — {TASK_CODE}")
    lines.append("")
    lines.append(f"- 知识库：`{result.knowledge_root}`")
    lines.append(f"- 查询：`{result.query or '全库'}`")
    lines.append(f"- 基准日期：{result.as_of_date}")
    lines.append(
        f"- citation_audit 接入：{'是' if result.has_citation_audit else '否'}"
    )
    status_label = _STATUS_LABELS.get(result.status, result.status)
    lines.append(f"- 状态：{result.status}（{status_label}）")
    lines.append(f"- 命中标的数：{len(result.symbols)}")
    if result.errors:
        lines.append(f"- 错误：{'; '.join(result.errors[:5])}")
    lines.append("")
    lines.append(
        "> 本时间线按时间还原同股核心观点的强化/削弱/反转/过期演化，"
        "**不构成买卖建议**；只描述观点和证据变化，不输出交易动作。"
        "同机构重复覆盖已去重（不计入漂移）；弱来源不得覆盖强来源。"
    )
    lines.append("")

    if result.status in (STATUS_NORMAL_NO_DATA, STATUS_FAILED):
        lines.append("（无研报观点命中或构建失败）")
        return "\n".join(lines)

    # 概览表。
    top_syms = result.symbols[:top]
    if top_syms:
        lines.append("## 概览")
        lines.append("")
        lines.append(
            "| 标的 | 主题线 | 总版本 | 有效 | 重复 | drift | 反转 | "
            "削弱 | 强化 | 过期 | 待验证 |"
        )
        lines.append("|------|--------|--------|------|------|-------|------|"
            "------|------|------|--------|")
        for s in top_syms:
            lines.append(
                f"| `{s.symbol_key}` {s.name} | "
                f"{len(s.theses)} | {s.total_versions} | "
                f"{s.effective_versions} | {s.duplicate_versions} | "
                f"{s.consensus_drift_score:.2f} | "
                f"{s.reversed_versions} | {s.weakened_versions} | "
                f"{s.reinforced_versions} | {s.stale_versions} | "
                f"{s.pending_fact_check_versions} |"
            )
        lines.append("")

    # 详细时间线。
    for idx, s in enumerate(top_syms[:5], 1):
        lines.append(f"## {idx}. `{s.symbol_key}` {s.name or '未命名'}")
        lines.append("")
        lines.append(f"- 摘要：{s.summary}")
        lines.append("")

        for t in s.theses[:8]:
            lines.append(
                f"### {t.theme_label}（{t.theme}:{t.theme_token}）"
            )
            lines.append("")
            lines.append(
                f"- drift={t.consensus_drift_score:.2f} ｜ "
                f"有效版本 {t.version_count_effective}（重复 "
                f"{t.duplicate_count}）｜ "
                f"base={_DIRECTION_LABELS.get(t.base_direction, t.base_direction)}"
                f" → current={_DIRECTION_LABELS.get(t.current_direction, t.current_direction)}"
            )
            if t.weak_source_overlay_count:
                lines.append(
                    f"- ⚠️ 弱来源试图覆盖被阻止 {t.weak_source_overlay_count} 次"
                )
            lines.append(f"- 摘要：{t.summary}")
            lines.append("")

            # 版本表。
            lines.append("| 报告日期 | 路径 | 来源 | 方向 | 版本状态 |"
                         " audit | 重复 | 说明 |")
            lines.append("|----------|------|------|------|----------|"
                         "-------|------|------|")
            for v in t.versions[:_MAX_VERSIONS_PER_THESIS]:
                dup_tag = "是" if v.is_duplicate else "—"
                audit_tag = v.citation_audit_status or "—"
                dir_label = _DIRECTION_LABELS.get(
                    v.direction, v.direction
                )
                ver_label = _VERSION_LABELS.get(
                    v.thesis_version_status, v.thesis_version_status
                )
                reason = v.change_reason or "—"
                lines.append(
                    f"| {v.report_date or '无日期'} | "
                    f"`{v.rel_path}` | {v.source_quality_tier} | "
                    f"{dir_label} | {ver_label} | {audit_tag} | "
                    f"{dup_tag} | {reason} |"
                )
            lines.append("")

    # 计分口径。
    lines.append("## 计分口径")
    lines.append("")
    lines.append("```")
    lines.append("# thesis_version_status 优先级：")
    lines.append("#   stale > pending_fact_check > reversed > weakened > reinforced > new")
    lines.append("# consensus_drift_score = mean(非重复节点对的 drift 权重)")
    lines.append(
        f"#   drift 权重：reversed={_DRIFT_WEIGHT_REVERSED} / "
        f"weakened={_DRIFT_WEIGHT_WEAKENED} / "
        f"stale={_DRIFT_WEIGHT_STALE} / "
        f"pending={_DRIFT_WEIGHT_PENDING} / reinforced=0"
    )
    lines.append("# 同机构重复覆盖（同 institution + 文本相似度 ≥ "
        f"{_DUP_TEXT_SIMILARITY_THRESHOLD}）不计入 drift。")
    lines.append("# 弱来源晚于强来源且方向相反 → 阻止覆盖，计为削弱而非反转。")
    lines.append("```")
    lines.append("")
    lines.append(
        f"_由 `tradingagents.dataflows.research_thesis_timeline` 只读生成；"
        f"任务编号 {TASK_CODE}。_"
    )
    lines.append("")
    return "\n".join(lines)


# ── 便利函数 ──────────────────────────────────────────────────────────


def suggest_report_output_path(
    docs_dir: str = "docs/knowledge_reports",
) -> str:
    """建议的报告输出路径：``docs/knowledge_reports/research_thesis_timeline-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"research_thesis_timeline-{today}.md")


def has_forbidden_action_words(text: str) -> bool:
    """检查文本是否含强动作词（防回归辅助）。"""
    for word in _FORBIDDEN_ACTION_WORDS:
        if word in (text or ""):
            return True
    return False


__all__ = [
    "VENDOR",
    "TASK_CODE",
    "VERSION_NEW",
    "VERSION_REINFORCED",
    "VERSION_WEAKENED",
    "VERSION_REVERSED",
    "VERSION_STALE",
    "VERSION_PENDING_FACT_CHECK",
    "ALL_VERSION_STATUSES",
    "ThesisVersion",
    "ThesisTimeline",
    "SymbolThesisTimelineResult",
    "ResearchThesisTimelineResult",
    "build_research_thesis_timeline",
    "lookup_research_thesis_timeline",
    "timeline_to_ta_consumable_summary",
    "render_research_thesis_timeline_report",
    "suggest_report_output_path",
    "has_forbidden_action_words",
]
