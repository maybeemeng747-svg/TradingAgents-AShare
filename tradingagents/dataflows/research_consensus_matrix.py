# [KB-016] research_consensus_matrix
"""多研报一致性 / 分歧矩阵与关注度去重回放。

在 KB-015（研报观点/事实分离）、KB-007（多研报重复提及因子）、KB-009（机构级
去重 + 时效衰减）之上，本模块把同一只标的多份研报按 symbol 聚合，输出：

  - ``consensus_score`` ∈ [0, 1]：观点一致程度（越高越一致）。
  - ``disagreement_score`` ∈ [0, 1+]：四个维度的分歧总量（越高越分裂）。
  - ``attention_count_effective``：去重 + 衰减后的有效关注篇数。
  - 四维分歧矩阵：业绩预测 / 产业链角色 / 风险判断 / 估值假设。
  - ``needs_fact_check`` 候选：高分歧或弱来源共识 → 交 HY-003/HY-005 做事实反证。

设计约束（对应任务 KB-016）：
  - **不是买入信号**：``consensus_score`` 只表达"多份研报观点是否一致"，
    **绝不**直接转成交易动作；输出文档刻意避免 ``买入 / 卖出 / 加仓 / 减仓 /
    强烈推荐`` 等强动作词。
  - **只读本地知识库**：仅用 ``open(..., "r", encoding="utf-8")`` 与
    ``Path.iterdir``，绝不向知识库写文件；不写生产 DB。
  - **不复制研报正文**：每条 claim 文本裁剪到 ``_CLAIM_MAX_CHARS``。
  - **不调用 LLM / 不访问外网**：纯标准库 + 复用 KB-015 只读解析。
  - **机构级 / 标题级去重**：同机构 + 同标题 + 同立场 → 只计一次主权重，
    避免虚假共识增强；过期观点 → 衰减为弱证据而非删除。
  - **多研报高关注只能提高研究优先级**：不会绕过 TradeFlow / TA 强动作门禁。

立场检测（规则版，无 LLM）：
  四维（``performance`` / ``supply_chain_role`` / ``risk`` / ``valuation``）每维
  在 ``bullish`` / ``bearish`` / ``neutral`` / ``unknown`` 中取值。规则基于
  关键词命中 + 数值断言符号；**不做任何买卖建议推断**。

合成公式（解释可读、可复现）::

    # 1. per-page stance：从 KB-015 claims 按维度合成 stance
    # 2. per-dimension disagreement：1 - max(bullish, bearish) / total
    # 3. consensus_score  = 1 - mean(dim_disagreement)
    # 4. disagreement_score = sum(dim_disagreement)
    # 5. attention_count_effective：fresh 页数 - 机构级重复数
    # 6. needs_fact_check：disagreement 高 / 弱来源共识 / 过期主导 / 验证缺口

使用示例::

    from tradingagents.dataflows.research_consensus_matrix import (
        build_research_consensus_matrix,
        render_research_consensus_matrix_report,
    )
    result = build_research_consensus_matrix(
        "/Users/maybee/Documents/knowledge", symbol="603296"
    )
    print(render_research_consensus_matrix_report(result))
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
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_ORIGINAL_FILING,
    TIER_OFFICIAL_NOTICE,
    TIER_UNKNOWN,
    TIER_USER_NOTE,
    CitationAssessment,
    classify_source_quality_tier,
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
    STALE_FRESH,
    STALE_LOW_CONFIDENCE,
    STALE_STALE,
    ResearchClaimItem,
    ResearchFactOpinionPage,
    build_research_fact_opinion_index,
)


# ── 常量 ──────────────────────────────────────────────────────────────

VENDOR = "tree_work_wiki"
TASK_CODE = "KB-016"

# 立场取值（per dimension）。
STANCE_BULLISH = "bullish"
STANCE_BEARISH = "bearish"
STANCE_NEUTRAL = "neutral"
STANCE_UNKNOWN = "unknown"
ALL_STANCES: Tuple[str, ...] = (
    STANCE_BULLISH,
    STANCE_BEARISH,
    STANCE_NEUTRAL,
    STANCE_UNKNOWN,
)

# 四维分歧矩阵（与任务原文严格一致）。
DIM_PERFORMANCE = "performance"          # 业绩预测分歧
DIM_SUPPLY_CHAIN_ROLE = "supply_chain_role"  # 产业链角色分歧
DIM_RISK = "risk"                         # 风险判断分歧
DIM_VALUATION = "valuation"              # 估值假设分歧
DIMENSIONS: Tuple[str, ...] = (
    DIM_PERFORMANCE,
    DIM_SUPPLY_CHAIN_ROLE,
    DIM_RISK,
    DIM_VALUATION,
)
_DIMENSION_LABELS: Dict[str, str] = {
    DIM_PERFORMANCE: "业绩预测",
    DIM_SUPPLY_CHAIN_ROLE: "产业链角色",
    DIM_RISK: "风险判断",
    DIM_VALUATION: "估值假设",
}

# 时间窗口（月）。
WINDOW_3M = 3
WINDOW_6M = 6
WINDOW_12M = 12
DEFAULT_WINDOW_MONTHS = WINDOW_12M
ALL_WINDOWS: Tuple[int, ...] = (WINDOW_3M, WINDOW_6M, WINDOW_12M)

# 文本裁剪上限。
_CLAIM_MAX_CHARS = 120
_SUMMARY_MAX_CHARS = 200
_MAX_REASONS = 8
_MAX_MATRIX_PAGES = 15

# needs_fact_check 触发阈值。
DISAGREEMENT_THRESHOLD = 0.34   # 单维分歧 > 1/3 → 疑似分裂
WEAK_SOURCE_BULLISH_THRESHOLD = 2  # 弱来源共识 ≥2 篇 → 需核实
STALE_DOMINANCE_RATIO = 0.5     # 过期页占比 ≥ 50% → 需刷新

# ── 立场关键词（规则版，无 LLM）────────────────────────────────────────

# 业绩预测 bullish：增长 / 超预期 / 上修 / 高增 / 改善
_PERF_BULLISH_KW: Tuple[str, ...] = (
    "增长", "高增", "超预期", "好于预期", "优于预期", "上修", "上调",
    "改善", "提升", "加速", "突破", "创新高", "强劲", "放量增长",
    "扭亏", "减亏", "盈利恢复",
)
# 业绩预测 bearish：下降 / 不及预期 / 下修 / 恶化 / 亏损扩大
_PERF_BEARISH_KW: Tuple[str, ...] = (
    "下降", "下滑", "不及预期", "低于预期", "差于预期", "下修", "下调",
    "恶化", "萎缩", "承压", "亏损扩大", "续亏", "增速放缓", "负增长",
    "同比转负",
)

# 产业链角色 bullish：龙头 / 核心受益 / 主力 / 一体化 / 卡位
_ROLE_BULLISH_KW: Tuple[str, ...] = (
    "龙头", "核心受益", "主力", "主导", "一体化", "卡位", "稀缺",
    "壁垒", "领先", "头部", "关键供应商", "核心环节", "深度布局",
)
# 产业链角色 bearish：边缘 / 替代 / 受益有限 / 缺位 / 竞争加剧
_ROLE_BEARISH_KW: Tuple[str, ...] = (
    "边缘", "替代", "受益有限", "缺位", "竞争加剧", "份额下滑",
    "技术落后", "客户流失", "掉队", "出局",
)

# 风险判断 bullish（风险可控 / 下降）：风险可控、风险下降、风险释放
_RISK_BULLISH_KW: Tuple[str, ...] = (
    "风险可控", "风险下降", "风险释放", "风险缓和", "风险缓解",
    "风险较低", "无明显风险", "下行空间有限",
)
# 风险判断 bearish（风险加剧 / 上升）：风险加剧、风险上升、担忧
_RISK_BEARISH_KW: Tuple[str, ...] = (
    "风险加剧", "风险上升", "风险加大", "风险提升", "担忧", "承压",
    "隐患", "不确定性显著", "下行风险", "较大风险", "高风险",
)

# 估值 bullish（低估 / 合理）：低估、估值合理、估值修复
_VAL_BULLISH_KW: Tuple[str, ...] = (
    "低估", "估值合理", "估值修复", "估值提升", "性价比", "安全边际",
    "估值偏低", "具有吸引力", "估值优势",
)
# 估值 bearish（高估 / 贵）：高估、估值贵、估值偏高
_VAL_BEARISH_KW: Tuple[str, ...] = (
    "高估", "估值贵", "估值偏高", "估值较高", "透支", "泡沫",
    "估值压力", "估值偏贵", "缺乏安全边际",
)

# 按维度组织关键词表。
_BULLISH_KW_BY_DIM: Dict[str, Tuple[str, ...]] = {
    DIM_PERFORMANCE: _PERF_BULLISH_KW,
    DIM_SUPPLY_CHAIN_ROLE: _ROLE_BULLISH_KW,
    DIM_RISK: _RISK_BULLISH_KW,
    DIM_VALUATION: _VAL_BULLISH_KW,
}
_BEARISH_KW_BY_DIM: Dict[str, Tuple[str, ...]] = {
    DIM_PERFORMANCE: _PERF_BEARISH_KW,
    DIM_SUPPLY_CHAIN_ROLE: _ROLE_BEARISH_KW,
    DIM_RISK: _RISK_BEARISH_KW,
    DIM_VALUATION: _VAL_BEARISH_KW,
}

# 数值变化符号辅助（+25% / -10pp / 增长 50% / 下降 30%）。
_POSITIVE_NUMBER_RE = re.compile(r"[+]?\d+(?:\.\d+)?\s*(?:%|pp|个百分点|倍)")
_NEGATIVE_NUMBER_RE = re.compile(
    r"-\s*\d+(?:\.\d+)?\s*(?:%|pp|个百分点|倍)|下降\s*\d|下滑\s*\d|减少\s*\d"
)

# 强动作词黑名单（输出文档不含）。
_FORBIDDEN_ACTION_WORDS: Tuple[str, ...] = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL",
    "立即买入", "全仓", "止损",
)


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class DimensionStance:
    """单页在某维度的立场判定。

    携带 stance + 命中关键词（便于审查与 explain），不携带原文段落。
    """

    dimension: str
    stance: str = STANCE_UNKNOWN
    hit_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "stance": self.stance,
            "hit_keywords": list(self.hit_keywords),
        }


@dataclass
class ReportStance:
    """单份研报（KB-015 page）在四个维度上的立场 + 元数据。"""

    rel_path: str
    title: str
    symbol: str = ""
    name: str = ""
    source_quality_tier: str = TIER_UNKNOWN
    report_date: Optional[str] = None
    stale_status: str = STALE_FRESH
    confidence: str = "low"
    institution: str = ""
    is_duplicate: bool = False
    duplicate_reason: str = ""
    overall_stance: str = STANCE_UNKNOWN
    dimensions: Dict[str, DimensionStance] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "title": self.title,
            "symbol": self.symbol,
            "name": self.name,
            "source_quality_tier": self.source_quality_tier,
            "report_date": self.report_date,
            "stale_status": self.stale_status,
            "confidence": self.confidence,
            "institution": self.institution,
            "is_duplicate": self.is_duplicate,
            "duplicate_reason": self.duplicate_reason,
            "overall_stance": self.overall_stance,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
        }


@dataclass
class DimensionDisagreement:
    """单维度分歧聚合。

    - ``bullish_count`` / ``bearish_count`` / ``neutral_count`` /
      ``unknown_count``：去重后各立场页数（duplicate=True 不计入）。
    - ``disagreement_score`` ∈ [0, 1]：1 - max(bull, bear) / total。
    """

    dimension: str
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    unknown_count: int = 0
    disagreement_score: float = 0.0
    dominant_stance: str = STANCE_UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "neutral_count": self.neutral_count,
            "unknown_count": self.unknown_count,
            "disagreement_score": round(self.disagreement_score, 3),
            "dominant_stance": self.dominant_stance,
        }


@dataclass
class SymbolConsensusMatrix:
    """单标的的研报共识 / 分歧矩阵聚合结果。"""

    symbol_key: str
    bare_code: str
    name: str
    window_months: int = DEFAULT_WINDOW_MONTHS
    report_count_total: int = 0
    report_count_in_window: int = 0
    report_count_duplicate: int = 0
    attention_count_effective: int = 0
    stale_count: int = 0
    fresh_count: int = 0
    low_confidence_count: int = 0
    consensus_score: float = 0.0
    disagreement_score: float = 0.0
    dominant_stance: str = STANCE_UNKNOWN
    needs_fact_check: bool = False
    fact_check_reasons: List[str] = field(default_factory=list)
    fact_check_priority: str = "low"  # low / medium / high
    dimensions: Dict[str, DimensionDisagreement] = field(default_factory=dict)
    reports: List[ReportStance] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_key": self.symbol_key,
            "bare_code": self.bare_code,
            "name": self.name,
            "window_months": self.window_months,
            "report_count_total": self.report_count_total,
            "report_count_in_window": self.report_count_in_window,
            "report_count_duplicate": self.report_count_duplicate,
            "attention_count_effective": self.attention_count_effective,
            "stale_count": self.stale_count,
            "fresh_count": self.fresh_count,
            "low_confidence_count": self.low_confidence_count,
            "consensus_score": round(self.consensus_score, 3),
            "disagreement_score": round(self.disagreement_score, 3),
            "dominant_stance": self.dominant_stance,
            "needs_fact_check": self.needs_fact_check,
            "fact_check_reasons": list(self.fact_check_reasons),
            "fact_check_priority": self.fact_check_priority,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "reports": [r.to_dict() for r in self.reports],
            "summary": self.summary,
        }


@dataclass
class ResearchConsensusMatrixResult:
    """整次研报共识 / 分歧矩阵聚合结果。"""

    status: str = STATUS_NORMAL_NO_DATA
    vendor: str = VENDOR
    task: str = TASK_CODE
    knowledge_root: str = ""
    window_months: int = DEFAULT_WINDOW_MONTHS
    as_of_date: str = ""
    query: Dict[str, Any] = field(default_factory=dict)
    symbols: List[SymbolConsensusMatrix] = field(default_factory=list)
    needs_fact_check_symbols: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vendor": self.vendor,
            "task": self.task,
            "knowledge_root": self.knowledge_root,
            "window_months": self.window_months,
            "as_of_date": self.as_of_date,
            "query": dict(self.query),
            "symbols": [s.to_dict() for s in self.symbols],
            "needs_fact_check_symbols": list(self.needs_fact_check_symbols),
            "errors": list(self.errors),
        }


# ── KB-007 / KB-009 复用桥接 ─────────────────────────────────────────

# KB-009 的 split_institution 是权威实现（KB-007 也复用它）。
# 这里做一次安全解析，便于 KB-016 在 KB-009 缺失时回退到内置实现，
# 避免循环依赖 / 模块加载顺序问题。
def _resolve_split_institution() -> Any:
    """安全解析 KB-009 的机构分割函数；缺失时回退到内置实现。"""
    try:
        return _kb009_split_institution
    except NameError:  # pragma: no cover
        return _fallback_split_institution


def _fallback_split_institution(source_alias: str) -> str:
    """内置兜底：取首个 ``- / — / ： / :`` 之前的部分。"""
    text = str(source_alias).strip()
    if not text:
        return ""
    m = re.search(r"[-—：:]", text)
    if m:
        inst = text[: m.start()].strip()
        return inst or text
    return text


# 解析一次，模块级常量。
_split_institution_fn = _resolve_split_institution()


def split_institution_helper(source_alias: str) -> str:
    """对外暴露的稳定机构分割函数（被 KB-016 测试与 report 共用）。"""
    return _split_institution_fn(source_alias) or ""


# ── 文本裁剪与归一化 ──────────────────────────────────────────────────


def _clip_text(text: str, max_chars: int = _CLAIM_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text


def _is_valid_date(text: Optional[str]) -> Optional[date]:
    """解析 ``updated`` / ``disclosure_date`` / ``2026-08-30`` 形态的日期。"""
    if not text:
        return None
    cleaned = re.sub(r"[/-]", "", str(text).strip())
    if not re.fullmatch(r"\d{8}", cleaned):
        return None
    try:
        return date(int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]))
    except ValueError:
        return None


def _within_window(
    report_date: Optional[str], today: date, window_months: int
) -> bool:
    """报告日期是否在 window_months 内。无日期 → 视作在窗口内（不误杀）。"""
    if window_months <= 0:
        return True
    if not report_date:
        return True
    rd = _is_valid_date(report_date)
    if rd is None:
        return True
    # 近似 month 差：用 30 天近似 1 月。
    delta_days = (today - rd).days
    return 0 <= delta_days <= window_months * 31


# ── 立场检测（规则版，无 LLM）─────────────────────────────────────────


def _detect_dimension_stance(
    dimension: str, claims: List[ResearchClaimItem]
) -> DimensionStance:
    """对单维度做关键词命中统计，返回 bullish/bearish/neutral/unknown。

    规则：
      - 命中 bullish 关键词 → +1 bull 命中。
      - 命中 bearish 关键词 → +1 bear 命中。
      - bull > bear → bullish；bear > bull → bearish；同分且 > 0 → neutral；
        无命中 → unknown。
      - 数值符号辅助：仅在该 claim 已命中文字关键词时叠加，不单独触发
        （避免"营收 100 亿"被误判）。
    """
    bull_kws = _BULLISH_KW_BY_DIM.get(dimension, ())
    bear_kws = _BEARISH_KW_BY_DIM.get(dimension, ())
    bull_hits: List[str] = []
    bear_hits: List[str] = []

    for claim in claims:
        text = claim.text or ""
        if not text:
            continue
        text_lower = text  # 中文不做 lower
        # 文字关键词。
        for kw in bull_kws:
            if kw in text_lower and kw not in bull_hits:
                bull_hits.append(kw)
        for kw in bear_kws:
            if kw in text_lower and kw not in bear_hits:
                bear_hits.append(kw)
        # 数值符号辅助（仅在已有文字命中时叠加）。
        if bull_hits and _POSITIVE_NUMBER_RE.search(text):
            if "+数值" not in bull_hits:
                bull_hits.append("+数值")
        if bear_hits and _NEGATIVE_NUMBER_RE.search(text):
            if "-数值" not in bear_hits:
                bear_hits.append("-数值")

    if not bull_hits and not bear_hits:
        return DimensionStance(dimension=dimension, stance=STANCE_UNKNOWN)

    if len(bull_hits) > len(bear_hits):
        stance = STANCE_BULLISH
    elif len(bear_hits) > len(bull_hits):
        stance = STANCE_BEARISH
    else:
        stance = STANCE_NEUTRAL

    return DimensionStance(
        dimension=dimension,
        stance=stance,
        hit_keywords=bull_hits + bear_hits,
    )


def _page_dimension_claims(
    page: ResearchFactOpinionPage, dimension: str
) -> List[ResearchClaimItem]:
    """把 KB-015 page claims 按维度归类。

    - performance：forecast_items（前瞻指引）+ research_claims 中含业绩关键词。
    - supply_chain_role：research_claims + reported_facts 中含角色关键词。
    - risk：risk_items。
    - valuation：research_claims 中含估值关键词。
    """
    if dimension == DIM_RISK:
        return list(page.risk_items)
    if dimension == DIM_PERFORMANCE:
        # 前瞻指引 + 含业绩关键词的观点/事实。
        base = list(page.forecast_items)
        for c in page.research_claims + page.reported_facts:
            if c.claim_type in (CLAIM_FORECAST, CLAIM_FACT, CLAIM_OPINION):
                base.append(c)
        return base
    if dimension == DIM_VALUATION:
        # 只看观点类（估值假设多在观点段）。
        return list(page.research_claims)
    if dimension == DIM_SUPPLY_CHAIN_ROLE:
        return list(page.research_claims + page.reported_facts)
    return []


def _detect_overall_stance(
    dimensions: Dict[str, DimensionStance],
) -> str:
    """从四维立场合成 overall_stance。

    - 全部 bullish（无 bearish）→ bullish。
    - 全部 bearish（无 bullish）→ bearish。
    - 同时有 bullish 和 bearish → neutral（内部分裂）。
    - 全部 unknown → unknown。
    """
    bull = sum(1 for d in dimensions.values() if d.stance == STANCE_BULLISH)
    bear = sum(1 for d in dimensions.values() if d.stance == STANCE_BEARISH)
    if bull > 0 and bear > 0:
        return STANCE_NEUTRAL
    if bull > 0:
        return STANCE_BULLISH
    if bear > 0:
        return STANCE_BEARISH
    return STANCE_UNKNOWN


# ── 机构 / 标题去重 ────────────────────────────────────────────────────


def _extract_institution(page: ResearchFactOpinionPage) -> str:
    """从 KB-015 page 的 sources（通过 citation）推断机构名。

    KB-015 page 不直接携带 sources alias，但携带 source_quality_tier 与
    rel_path。这里用 rel_path / title 关键词做兜底，避免错误折叠。
    """
    # KB-015 page 没有暴露 sources，从 title / rel_path 提取机构名。
    candidates = [page.title, page.rel_path]
    for text in candidates:
        if not text:
            continue
        inst = split_institution_helper(text)
        if inst and inst not in ("wiki", "investment"):
            return inst
    return page.rel_path  # 兜底：用 rel_path 作独立 key，避免误折叠


def _norm_title(title: str) -> str:
    """标题归一化：去空白 / 去日期 / 去常见后缀，用于跨页去重。"""
    t = (title or "").strip()
    # 去日期前缀（2026-07-11- / 20260711-）。
    t = re.sub(r"^\d{4}[-/]?\d{2}[-/]?\d{2}[-_\s]*", "", t)
    # 去常见后缀。
    t = re.sub(r"[-_—（(].*$", "", t).strip()
    return t.lower()


def _mark_duplicates(reports: List[ReportStance]) -> None:
    """原地标记重复报告（同机构 + 同标题 + 同立场 → 重复）。

    规则（保守，宁可漏标也不误杀真实多机构共识）：
      - 同机构 + 同标题（norm）+ 同 overall_stance → 重复（保留首条）。
      - 同 rel_path → 重复。
      - 同标题 + 同 overall_stance + 同日报日期 → 重复。
    """
    seen_keys: List[Tuple[str, str, str]] = []
    seen_paths: List[str] = []
    for r in reports:
        if r.rel_path in seen_paths:
            r.is_duplicate = True
            r.duplicate_reason = "同文件重复导入"
            continue
        key = (r.institution or "", _norm_title(r.title), r.overall_stance)
        # 只在 institution + title 都非空时做机构级去重。
        if key[0] and key[1] and key[2] != STANCE_UNKNOWN:
            if key in seen_keys:
                r.is_duplicate = True
                r.duplicate_reason = (
                    f"同机构「{key[0]}」同标题「{key[1][:20]}」"
                    f"重复覆盖，保留首条"
                )
                seen_paths.append(r.rel_path)
                continue
            seen_keys.append(key)
        seen_paths.append(r.rel_path)


# ── 共识 / 分歧合成 ──────────────────────────────────────────────────


def _dimension_disagreement(
    dim: str, reports: List[ReportStance]
) -> DimensionDisagreement:
    """聚合单维度的分歧（仅非重复页）。"""
    bull = bear = neut = unk = 0
    for r in reports:
        if r.is_duplicate:
            continue
        ds = r.dimensions.get(dim)
        if ds is None:
            unk += 1
            continue
        if ds.stance == STANCE_BULLISH:
            bull += 1
        elif ds.stance == STANCE_BEARISH:
            bear += 1
        elif ds.stance == STANCE_NEUTRAL:
            neut += 1
        else:
            unk += 1

    total = bull + bear + neut + unk
    if total <= 0:
        score = 0.0
        dominant = STANCE_UNKNOWN
    else:
        max_count = max(bull, bear, neut, unk)
        score = max(0.0, 1.0 - max_count / total)
        # dominant：取计数最多的 stance（并列时优先 bull > bear > neut > unk）。
        if bull >= bear and bull >= neut and bull >= unk and bull > 0:
            dominant = STANCE_BULLISH
        elif bear >= bull and bear >= neut and bear >= unk and bear > 0:
            dominant = STANCE_BEARISH
        elif neut > 0 and neut >= unk:
            dominant = STANCE_NEUTRAL
        else:
            dominant = STANCE_UNKNOWN if unk == total else (
                STANCE_NEUTRAL if neut > 0 else STANCE_UNKNOWN
            )

    return DimensionDisagreement(
        dimension=dim,
        bullish_count=bull,
        bearish_count=bear,
        neutral_count=neut,
        unknown_count=unk,
        disagreement_score=score,
        dominant_stance=dominant,
    )


def _symbol_dominant_stance(
    dim_results: Dict[str, DimensionDisagreement],
) -> str:
    """从四维聚合结果推断 symbol 级 dominant_stance。

    规则：四维 dominant_stance 投票，bull/bear 各自计数，多数胜出；平票 neutral。
    """
    bull = sum(1 for d in dim_results.values() if d.dominant_stance == STANCE_BULLISH)
    bear = sum(1 for d in dim_results.values() if d.dominant_stance == STANCE_BEARISH)
    if bull > bear and bull > 0:
        return STANCE_BULLISH
    if bear > bull and bear > 0:
        return STANCE_BEARISH
    if bull > 0 or bear > 0:
        return STANCE_NEUTRAL
    return STANCE_UNKNOWN


def _fact_check_reasons(
    matrix: SymbolConsensusMatrix,
    has_opinion_only_consensus: bool,
    stale_ratio: float,
    kb015_verification_count: int,
) -> Tuple[List[str], str]:
    """生成 needs_fact_check 触发原因与优先级。

    返回 ``(reasons, priority)``。priority: low / medium / high。
    """
    reasons: List[str] = []
    priority = "low"

    # 1. 任一维度分歧超过阈值 → 疑似观点分裂，需事实对照。
    split_dims = [
        d for d in matrix.dimensions.values()
        if d.disagreement_score >= DISAGREEMENT_THRESHOLD
    ]
    if split_dims:
        dim_labels = "、".join(
            _DIMENSION_LABELS.get(d.dimension, d.dimension) for d in split_dims
        )
        reasons.append(
            f"{dim_labels}维度分歧较高（≥{DISAGREEMENT_THRESHOLD:.2f}），"
            "需 HY-003/HY-005 做事实对照判定。"
        )
        priority = "medium"

    # 2. 弱来源共识：仅观点类来源（broker/media/user_note/unknown）且 bullish 共识。
    if has_opinion_only_consensus and matrix.dominant_stance == STANCE_BULLISH:
        bull_total = sum(
            d.bullish_count for d in matrix.dimensions.values()
        )
        if bull_total >= WEAK_SOURCE_BULLISH_THRESHOLD:
            reasons.append(
                f"看多共识仅基于券商/媒体观点（{bull_total} 条），"
                "缺乏公告/财报原文支撑，需事实反证。"
            )
            if priority == "low":
                priority = "medium"

    # 3. 过期页主导：stale_ratio ≥ 50%。
    #    覆盖两类场景：in-window 内 stale 占比高，或全部为 out-of-window stale。
    #    stale_ratio 在调用前已做 total>0 防护（total=0 时 stale_ratio=0.0）。
    if stale_ratio >= STALE_DOMINANCE_RATIO:
        reasons.append(
            f"过期研报占比 {stale_ratio:.0%}（≥{STALE_DOMINANCE_RATIO:.0%}），"
            "观点可能基于旧数据，需刷新事实。"
        )
        if priority == "low":
            priority = "medium"

    # 4. KB-015 verification_needs > 0：观点冒充事实 / 缺来源。
    if kb015_verification_count > 0:
        reasons.append(
            f"KB-015 标记 {kb015_verification_count} 处待验证项"
            "（观点冒充事实 / 缺来源），需事实核验。"
        )
        if priority == "low":
            priority = "medium"

    # 5. 高分歧 + 弱来源共识同时出现 → 高优先级。
    if split_dims and has_opinion_only_consensus:
        priority = "high"

    return reasons, priority


def _render_summary(matrix: SymbolConsensusMatrix) -> str:
    """渲染一句话中性摘要（含负面信息，无强动作词）。"""
    parts: List[str] = []
    stance_label = {
        STANCE_BULLISH: "偏看多",
        STANCE_BEARISH: "偏看空",
        STANCE_NEUTRAL: "观点中性/分裂",
        STANCE_UNKNOWN: "观点不明",
    }.get(matrix.dominant_stance, matrix.dominant_stance)
    parts.append(
        f"近 {matrix.window_months} 月有效研报 "
        f"{matrix.attention_count_effective} 篇，共识倾向 {stance_label}，"
        f"consensus={matrix.consensus_score:.2f} / "
        f"disagreement={matrix.disagreement_score:.2f}"
    )
    # 负面信息：重复 / 过期 / 低置信。
    negatives: List[str] = []
    if matrix.report_count_duplicate:
        negatives.append(f"重复报告 {matrix.report_count_duplicate}")
    if matrix.stale_count:
        negatives.append(f"过期 {matrix.stale_count}")
    if matrix.low_confidence_count:
        negatives.append(f"低置信 {matrix.low_confidence_count}")
    if negatives:
        parts.append("负面：" + "、".join(negatives))
    if matrix.needs_fact_check:
        parts.append(
            f"需事实反证（{matrix.fact_check_priority}）："
            + "；".join(matrix.fact_check_reasons[:2])
        )
    return "；".join(parts) + "。"


# ── 单 symbol 矩阵构建 ────────────────────────────────────────────────


def _build_symbol_matrix(
    symbol_key: str,
    bare_code: str,
    name: str,
    pages: List[ResearchFactOpinionPage],
    *,
    window_months: int,
    today: date,
) -> SymbolConsensusMatrix:
    """从 KB-015 pages 构建单 symbol 的共识 / 分歧矩阵。"""
    matrix = SymbolConsensusMatrix(
        symbol_key=symbol_key,
        bare_code=bare_code,
        name=name,
        window_months=window_months,
    )
    matrix.report_count_total = len(pages)

    # 1. 时间窗口过滤。
    #    **过期页绕过窗口**（对应任务"对过期观点做去重/衰减"——decay not delete）：
    #    stale_risk=高 或 valid_until 过期的页即使超出 3/6/12 月窗口，
    #    仍纳入矩阵并标 stale_count，触发 needs_fact_check（建议刷新事实）。
    in_window_pages: List[ResearchFactOpinionPage] = []
    stale_out_of_window: List[ResearchFactOpinionPage] = []
    for p in pages:
        if _within_window(p.report_date, today, window_months):
            in_window_pages.append(p)
        elif p.stale_status == STALE_STALE:
            stale_out_of_window.append(p)
    all_matrix_pages = in_window_pages + stale_out_of_window
    matrix.report_count_in_window = len(in_window_pages)

    # 2. 为每页构建 ReportStance（含四维立场）。
    reports: List[ReportStance] = []
    for p in all_matrix_pages:
        dims: Dict[str, DimensionStance] = {}
        for dim in DIMENSIONS:
            dim_claims = _page_dimension_claims(p, dim)
            dims[dim] = _detect_dimension_stance(dim, dim_claims)
        overall = _detect_overall_stance(dims)
        reports.append(ReportStance(
            rel_path=p.rel_path,
            title=p.title,
            symbol=p.symbol,
            name=p.name,
            source_quality_tier=p.source_quality_tier,
            report_date=p.report_date,
            stale_status=p.stale_status,
            confidence=p.confidence,
            institution=_extract_institution(p),
            overall_stance=overall,
            dimensions=dims,
        ))

    # 3. 机构 / 标题去重。
    _mark_duplicates(reports)

    # 4. 聚合维度分歧。
    dim_results: Dict[str, DimensionDisagreement] = {}
    for dim in DIMENSIONS:
        dim_results[dim] = _dimension_disagreement(dim, reports)
    matrix.dimensions = dim_results

    # 5. 共识 / 分歧分数。
    non_dup_reports = [r for r in reports if not r.is_duplicate]
    if dim_results:
        valid_disagreements = [
            d.disagreement_score for d in dim_results.values()
        ]
        avg_disagreement = sum(valid_disagreements) / len(valid_disagreements)
        matrix.disagreement_score = sum(valid_disagreements)
        matrix.consensus_score = max(0.0, 1.0 - avg_disagreement)
    matrix.dominant_stance = _symbol_dominant_stance(dim_results)

    # 6. 统计字段。
    #    stale_count 包含 in-window stale + out-of-window stale（过期绕过窗口）。
    matrix.report_count_duplicate = sum(1 for r in reports if r.is_duplicate)
    matrix.stale_count = sum(
        1 for r in non_dup_reports if r.stale_status == STALE_STALE
    )
    matrix.fresh_count = sum(
        1 for r in non_dup_reports if r.stale_status == STALE_FRESH
    )
    matrix.low_confidence_count = sum(
        1 for r in non_dup_reports if r.stale_status == STALE_LOW_CONFIDENCE
    )
    # attention_count_effective = 非重复 fresh + 部分 low_confidence
    # （low_confidence 按 0.5 权重近似，向下取整）。
    # 注意：out-of-window stale 不计入有效关注度（已过期）。
    matrix.attention_count_effective = (
        matrix.fresh_count + matrix.low_confidence_count // 2
    )

    # 7. needs_fact_check 判定。
    has_opinion_only = all(
        r.source_quality_tier in OPINION_ONLY_TIERS for r in non_dup_reports
    ) and len(non_dup_reports) > 0
    # stale_ratio：stale / total_matrix_pages（含 out-of-window stale）。
    total_for_ratio = matrix.report_count_in_window + len(stale_out_of_window)
    stale_ratio = (
        matrix.stale_count / total_for_ratio if total_for_ratio > 0 else 0.0
    )
    kb015_verification = sum(
        len(p.verification_needs) for p in all_matrix_pages
    )
    reasons, priority = _fact_check_reasons(
        matrix, has_opinion_only, stale_ratio, kb015_verification
    )
    matrix.needs_fact_check = bool(reasons)
    matrix.fact_check_reasons = reasons[:_MAX_REASONS]
    matrix.fact_check_priority = priority

    # 8. 报告与摘要。
    matrix.reports = reports[:_MAX_MATRIX_PAGES]
    matrix.summary = _render_summary(matrix)

    return matrix


# ── 主入口 ────────────────────────────────────────────────────────────


def build_research_consensus_matrix(
    knowledge_root: str,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    today: Optional[date] = None,
) -> ResearchConsensusMatrixResult:
    """构建研报共识 / 分歧矩阵。

    参数:
        knowledge_root: 知识库根目录绝对路径。
        symbol: A 股代码或代码+后缀（如 ``603296`` / ``603296.SH``）。
            ``None`` 时不限 symbol（全库索引模式，扫描所有 investment 页）。
        name: 公司简称（与页面 symbols 简称或 title 子串匹配）。
        window_months: 时间窗口（3/6/12），默认 12。
        today: 用于时间窗口与 stale 判定的基准日期（测试注入）；
            ``None`` 用 ``date.today()``。

    返回:
        :class:`ResearchConsensusMatrixResult`。单页解析失败不抛异常，记入 errors。
        知识库不存在时返回 status=FAILED。
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
    result = ResearchConsensusMatrixResult(
        knowledge_root=str(root),
        window_months=window_months,
        as_of_date=today.strftime("%Y-%m-%d"),
        query=query_desc,
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
    # symbol_key → list of (rel_path, abs_path, frontmatter)
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

        # 命中判定。
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

        # 为每个命中 symbol 建组（多 symbol 页同时进多组）。
        for entry in page_symbols:
            code_norm, bare, sname = _split_symbol_entry(entry)
            if not code_norm and not sname:
                continue
            skey = code_norm or sname
            # symbol 精确查询时只保留目标 symbol 的组。
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

    # ── 2. 按 symbol 分组构建 KB-015 index + matrix ──
    matrices: List[SymbolConsensusMatrix] = []
    for skey, info in grouped.items():
        # 用 KB-015 做 per-symbol claim 抽取（只读，不重复实现）。
        try:
            kb015_result = build_research_fact_opinion_index(
                knowledge_root,
                symbol=skey,
                name=info.get("name") or None,
                today=today,
                max_pages=_MAX_MATRIX_PAGES,
            )
        except Exception as exc:  # pragma: no cover
            result.errors.append(f"{skey}: KB-015 索引失败 {exc!r}")
            continue

        if not kb015_result.pages:
            continue
        result.errors.extend(kb015_result.errors or [])

        try:
            matrix = _build_symbol_matrix(
                skey,
                info.get("bare_code", ""),
                info.get("name", ""),
                kb015_result.pages,
                window_months=window_months,
                today=today,
            )
        except Exception as exc:  # pragma: no cover
            result.errors.append(f"{skey}: 矩阵构建失败 {exc!r}")
            continue

        matrices.append(matrix)

    # 排序：needs_fact_check=True 优先；其次 consensus_score 降序；最后 symbol_key。
    matrices.sort(
        key=lambda m: (
            0 if m.needs_fact_check else 1,
            -m.consensus_score,
            m.symbol_key,
        )
    )
    result.symbols = matrices
    result.needs_fact_check_symbols = [
        m.symbol_key for m in matrices if m.needs_fact_check
    ]

    # 状态机。
    if not matrices:
        result.status = STATUS_NORMAL_NO_DATA
    elif all(m.report_count_total == 0 for m in matrices):
        result.status = STATUS_NORMAL_NO_DATA
    else:
        # 任一 matrix 有 fresh 页 → HAS_DATA；否则按 stale/low 聚合。
        any_fresh = any(m.fresh_count > 0 for m in matrices)
        all_stale = all(
            m.fresh_count == 0 and m.low_confidence_count == 0
            for m in matrices if m.report_count_in_window > 0
        )
        if any_fresh:
            result.status = STATUS_HAS_DATA
        elif all_stale:
            result.status = STATUS_STALE
        else:
            result.status = STATUS_LOW_CONFIDENCE

    return result


# ── 单 symbol 查询便利 ────────────────────────────────────────────────


def lookup_research_consensus_matrix(
    knowledge_root: str,
    symbol: Optional[str],
    *,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    today: Optional[date] = None,
) -> Optional[SymbolConsensusMatrix]:
    """单 symbol 查询共识 / 分歧矩阵。

    无命中返回 ``None``；永远不会因单页解析失败而抛异常。
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return None
    result = build_research_consensus_matrix(
        knowledge_root, symbol=symbol,
        window_months=window_months, today=today,
    )
    for m in result.symbols:
        if _symbol_equivalent(symbol, m.symbol_key, m.bare_code):
            return m
    return None


_SUFFIX_RE = re.compile(r"\.(SH|SZ|BJ|HK|US|SS)$", re.IGNORECASE)


def _symbol_equivalent(query: str, sym_key: str, bare_code: str) -> bool:
    """宽松等价判定（与 KB-008 口径一致）。

    bare_code 仅在 sym_key 不是代码形态（如 sym_key 是公司简称）时兜底使用；
    若 sym_key 本身已是代码（如 ``600200.SH``），则 bare_code 不可信
    （可能来自不一致的调用方输入），避免误匹配。
    """
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
    # bare_code 兜底：仅当 sym_key 不是代码形态时才使用。
    if s_bare and s_bare.isdigit():
        return False
    if bare_code and q_bare and q_bare == bare_code.upper():
        return True
    return False


# ── TA 可消费扁平摘要 ────────────────────────────────────────────────


def matrix_to_ta_consumable_summary(
    matrix: Optional[SymbolConsensusMatrix],
) -> Dict[str, Any]:
    """把单 symbol 矩阵转为 TA/TradeFlow 可复用的扁平字典。

    无命中（``matrix is None``）时返回空结构（NORMAL_NO_DATA）。
    """
    if matrix is None:
        return {
            "has_hit": False,
            "consensus_score": 0.0,
            "disagreement_score": 0.0,
            "attention_count_effective": 0,
            "dominant_stance": STANCE_UNKNOWN,
            "needs_fact_check": False,
            "fact_check_priority": "low",
            "fact_check_reasons": [],
            "dimensions_brief": {},
            "consensus_summary": "",
        }

    dim_brief: Dict[str, Any] = {}
    for dim in DIMENSIONS:
        d = matrix.dimensions.get(dim)
        if d is None:
            dim_brief[dim] = {
                "disagreement_score": 0.0,
                "dominant_stance": STANCE_UNKNOWN,
            }
        else:
            dim_brief[dim] = {
                "disagreement_score": round(d.disagreement_score, 3),
                "dominant_stance": d.dominant_stance,
                "bullish_count": d.bullish_count,
                "bearish_count": d.bearish_count,
            }
    return {
        "has_hit": True,
        "consensus_score": round(matrix.consensus_score, 3),
        "disagreement_score": round(matrix.disagreement_score, 3),
        "attention_count_effective": matrix.attention_count_effective,
        "dominant_stance": matrix.dominant_stance,
        "needs_fact_check": matrix.needs_fact_check,
        "fact_check_priority": matrix.fact_check_priority,
        "fact_check_reasons": list(matrix.fact_check_reasons),
        "dimensions_brief": dim_brief,
        "consensus_summary": matrix.summary,
    }


def matrix_to_summary_dict(
    matrix: Optional[SymbolConsensusMatrix],
) -> Dict[str, Any]:
    """[KB-008/KB-016] 把单 symbol 矩阵转为前端/UI 可展示的扁平字典。

    字段名与 :func:`attention_to_summary` 风格一致，便于前端复用渲染。
    """
    if matrix is None:
        return {
            "research_consensus_has_hit": False,
            "research_consensus_score": 0.0,
            "research_disagreement_score": 0.0,
            "research_attention_effective_count": 0,
            "research_consensus_stance": STANCE_UNKNOWN,
            "research_consensus_needs_fact_check": False,
            "research_consensus_priority": "low",
            "research_consensus_reasons": [],
            "research_consensus_summary": "",
        }
    return {
        "research_consensus_has_hit": True,
        "research_consensus_score": round(matrix.consensus_score, 3),
        "research_disagreement_score": round(matrix.disagreement_score, 3),
        "research_attention_effective_count": matrix.attention_count_effective,
        "research_consensus_stance": matrix.dominant_stance,
        "research_consensus_needs_fact_check": matrix.needs_fact_check,
        "research_consensus_priority": matrix.fact_check_priority,
        "research_consensus_reasons": list(matrix.fact_check_reasons),
        "research_consensus_summary": matrix.summary,
    }


# ── 报告渲染 ──────────────────────────────────────────────────────────


_STATUS_LABELS = {
    STATUS_HAS_DATA: "已生成研报共识/分歧矩阵",
    STATUS_NORMAL_NO_DATA: "无研报命中（窗口内）",
    STATUS_FAILED: "研报共识/分歧矩阵构建失败",
    STATUS_STALE: "研报全部已过期（仅供参考）",
    STATUS_LOW_CONFIDENCE: "研报全部低置信（仅供参考）",
}

_STANCE_LABELS = {
    STANCE_BULLISH: "偏看多",
    STANCE_BEARISH: "偏看空",
    STANCE_NEUTRAL: "中性/分裂",
    STANCE_UNKNOWN: "不明",
}


def render_research_consensus_matrix_report(
    result: ResearchConsensusMatrixResult,
    *,
    top: int = 20,
) -> str:
    """渲染完整的研报共识 / 分歧矩阵 Markdown 报告。

    只展示摘要和路径，不展示长正文。状态为 NORMAL_NO_DATA / FAILED 时返回
    最小化报告（不抛异常）。**不含买卖建议或强动作词**。
    """
    lines: List[str] = []
    lines.append(f"# 研报共识/分歧矩阵报告 — {TASK_CODE}")
    lines.append("")
    lines.append(f"- 知识库：`{result.knowledge_root}`")
    lines.append(f"- 查询：`{result.query or '全库'}`")
    lines.append(f"- 时间窗口：近 {result.window_months} 月")
    lines.append(f"- 基准日期：{result.as_of_date}")
    status_label = _STATUS_LABELS.get(result.status, result.status)
    lines.append(f"- 状态：{result.status}（{status_label}）")
    lines.append(f"- 命中标的数：{len(result.symbols)}")
    lines.append(
        f"- 需事实反证候选：{len(result.needs_fact_check_symbols)} 个"
    )
    if result.errors:
        lines.append(f"- 错误：{'; '.join(result.errors[:5])}")
    lines.append("")
    lines.append(
        "> 本矩阵聚合同股多份研报的一致性 / 分歧，"
        "**不构成买卖建议**；多研报高关注只提高研究优先级，"
        "不绕过 TradeFlow/TA 强动作门禁。"
        "同机构重复覆盖已去重；过期观点已衰减为弱证据。"
    )
    lines.append("")

    if result.status in (STATUS_NORMAL_NO_DATA, STATUS_FAILED):
        lines.append("（无研报命中或构建失败）")
        return "\n".join(lines)

    # 概览表。
    top_syms = result.symbols[:top]
    if top_syms:
        lines.append("## 概览")
        lines.append("")
        lines.append(
            "| 标的 | 有效研报 | consensus | disagreement | 倾向 | "
            "需反证 | 优先级 |"
        )
        lines.append("|------|----------|-----------|--------------|------|"
            "--------|--------|")
        for m in top_syms:
            stance = _STANCE_LABELS.get(m.dominant_stance, m.dominant_stance)
            nfc = "是" if m.needs_fact_check else "—"
            lines.append(
                f"| `{m.symbol_key}` {m.name} | "
                f"{m.attention_count_effective} | "
                f"{m.consensus_score:.2f} | {m.disagreement_score:.2f} | "
                f"{stance} | {nfc} | {m.fact_check_priority} |"
            )
        lines.append("")

    # 详细矩阵。
    for idx, m in enumerate(top_syms[:10], 1):
        lines.append(f"## {idx}. `{m.symbol_key}` {m.name or '未命名'}")
        lines.append("")
        lines.append(
            f"- 有效研报：**{m.attention_count_effective}** ｜ "
            f"窗口内 {m.report_count_in_window}（重复 {m.report_count_duplicate}）｜ "
            f"fresh {m.fresh_count} / stale {m.stale_count} / "
            f"low_conf {m.low_confidence_count}"
        )
        lines.append(
            f"- consensus_score：**{m.consensus_score:.2f}** ｜ "
            f"disagreement_score：**{m.disagreement_score:.2f}** ｜ "
            f"dominant_stance：**"
            f"{_STANCE_LABELS.get(m.dominant_stance, m.dominant_stance)}**"
        )
        if m.needs_fact_check:
            lines.append(
                f"- ⚠️ **needs_fact_check=True**（优先级 {m.fact_check_priority}）："
            )
            for r in m.fact_check_reasons:
                lines.append(f"  - {r}")
        lines.append(f"- 摘要：{m.summary}")
        lines.append("")

        # 四维矩阵。
        lines.append("### 四维分歧矩阵")
        lines.append("")
        lines.append("| 维度 | 看多 | 看空 | 中性 | 不明 | 分歧 | 主导 |")
        lines.append("|------|------|------|------|------|------|------|")
        for dim in DIMENSIONS:
            d = m.dimensions.get(dim)
            if d is None:
                lines.append(
                    f"| {_DIMENSION_LABELS[dim]} | - | - | - | - | - | - |"
                )
                continue
            lines.append(
                f"| {_DIMENSION_LABELS[dim]} | "
                f"{d.bullish_count} | {d.bearish_count} | "
                f"{d.neutral_count} | {d.unknown_count} | "
                f"{d.disagreement_score:.2f} | "
                f"{_STANCE_LABELS.get(d.dominant_stance, d.dominant_stance)} |"
            )
        lines.append("")

        # 报告列表。
        if m.reports:
            lines.append("### 报告列表")
            lines.append("")
            for r in m.reports:
                dup_tag = " [重复]" if r.is_duplicate else ""
                if r.duplicate_reason:
                    dup_tag += f" ← {r.duplicate_reason}"
                flags = []
                if r.stale_status == STALE_STALE:
                    flags.append("STALE")
                if r.stale_status == STALE_LOW_CONFIDENCE:
                    flags.append("LOW_CONF")
                flag_text = f" [{', '.join(flags)}]" if flags else ""
                stance_label = _STANCE_LABELS.get(
                    r.overall_stance, r.overall_stance
                )
                lines.append(
                    f"- `{r.rel_path}` ({r.source_quality_tier})"
                    f"{flag_text}{dup_tag} → {stance_label}"
                )
            lines.append("")

    # 计分口径。
    lines.append("## 计分口径")
    lines.append("")
    lines.append("```")
    lines.append("# 单维分歧 disagreement = 1 - max(bull, bear) / total")
    lines.append("# consensus_score = 1 - mean(dim_disagreement)")
    lines.append("# disagreement_score = sum(dim_disagreement)")
    lines.append(
        f"# needs_fact_check 触发：分歧≥{DISAGREEMENT_THRESHOLD} / "
        "弱来源共识 / 过期主导 / KB-015 验证缺口"
    )
    lines.append("```")
    lines.append("")
    lines.append(
        f"_由 `tradingagents.dataflows.research_consensus_matrix` 只读生成；"
        f"任务编号 {TASK_CODE}。_"
    )
    lines.append("")
    return "\n".join(lines)


# ── 便利函数 ──────────────────────────────────────────────────────────


def suggest_report_output_path(
    docs_dir: str = "docs/knowledge_reports",
) -> str:
    """建议的报告输出路径：``docs/knowledge_reports/research_consensus_matrix-YYYY-MM-DD.md``。"""
    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"research_consensus_matrix-{today}.md")


def has_forbidden_action_words(text: str) -> bool:
    """检查文本是否含强动作词（防回归辅助）。"""
    for word in _FORBIDDEN_ACTION_WORDS:
        if word in (text or ""):
            return True
    return False


__all__ = [
    "VENDOR",
    "TASK_CODE",
    "STANCE_BULLISH",
    "STANCE_BEARISH",
    "STANCE_NEUTRAL",
    "STANCE_UNKNOWN",
    "ALL_STANCES",
    "DIM_PERFORMANCE",
    "DIM_SUPPLY_CHAIN_ROLE",
    "DIM_RISK",
    "DIM_VALUATION",
    "DIMENSIONS",
    "WINDOW_3M",
    "WINDOW_6M",
    "WINDOW_12M",
    "DEFAULT_WINDOW_MONTHS",
    "DimensionStance",
    "ReportStance",
    "DimensionDisagreement",
    "SymbolConsensusMatrix",
    "ResearchConsensusMatrixResult",
    "split_institution_helper",
    "build_research_consensus_matrix",
    "lookup_research_consensus_matrix",
    "matrix_to_ta_consumable_summary",
    "matrix_to_summary_dict",
    "render_research_consensus_matrix_report",
    "suggest_report_output_path",
    "has_forbidden_action_words",
]
