# [S-005] selection_priority_gate
"""Selection Priority Gate — integrates all S-series signals into a unified composite
score, re-ranks candidates, and applies a strict need_deep_ta gate.

Responsibilities:
1. Compute composite_score from policy_version, narrative_quality, tech (VCP/Pullback),
   fund_flow_anomaly, risk_penalty, and data_completeness.
2. Determine whether need_deep_ta should be True (requires >= 2 positive signal categories
   AND no major unexplained risks).
3. Output why_deep_ta / why_not_deep_ta / missing_evidence for Daily Plan display.
4. Provide stable fields for M-003 universe manager and M-004 strategy config.

Design constraints:
- No external LLM calls.
- No strong buy/sell words.
- No full-market scan.
- Single signal alone cannot trigger need_deep_ta=True.
- Risk-heavy or data-incomplete candidates must NOT get need_deep_ta=True.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]


_STRONG_BUY_SELL_WORDS = {
    "立即买入", "重仓买入", "立即清仓", "满仓", "梭哈",
    "强力买入", "强烈推荐", "必买", "抄底", "追涨", "杀跌",
}

_SEVERITY_HIGH_RISK_FLAGS = {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}

MIN_POSITIVE_CATEGORIES_FOR_DEEP_TA = 2

MAX_COMPOSITE_SCORE = 200.0


@dataclass
class SelectionPriorityResult:
    composite_score: float = 0.0
    signal_category_hits: list[str] = field(default_factory=list)
    positive_category_count: int = 0
    data_completeness: float = 0.0
    missing_evidence: list[str] = field(default_factory=list)
    why_deep_ta: str = ""
    why_not_deep_ta: str = ""
    gate_passed: bool = False
    priority_rank: str = ""
    gate_refs: list[dict] = field(default_factory=list)


def _sanitize(text: str) -> str:
    for w in _STRONG_BUY_SELL_WORDS:
        if w in text:
            text = text.replace(w, "***")
    return text


def _compute_data_completeness(
    has_price_data: bool = False,
    has_event_data: bool = False,
    has_fund_flow_data: bool = False,
    has_fund_flow_unit_verified: bool = False,
    has_technical_signal: bool = False,
    has_policy_signal: bool = False,
    has_narrative_signal: bool = False,
    has_risk_assessment: bool = False,
) -> tuple[float, list[str]]:
    total_fields = 8
    present = 0
    missing: list[str] = []

    if has_price_data:
        present += 1
    else:
        missing.append("行情数据")

    if has_event_data:
        present += 1
    else:
        missing.append("事件/新闻数据")

    if has_fund_flow_data:
        present += 1
    else:
        missing.append("资金流数据")

    if has_fund_flow_unit_verified:
        present += 1
    else:
        missing.append("资金单位校验")

    if has_technical_signal:
        present += 1
    else:
        missing.append("技术形态信号")

    if has_policy_signal:
        present += 1
    else:
        missing.append("政策版本信号")

    if has_narrative_signal:
        present += 1
    else:
        missing.append("叙事质量信号")

    if has_risk_assessment:
        present += 1
    else:
        missing.append("风险评估")

    completeness = present / total_fields if total_fields > 0 else 0.0
    return round(completeness, 3), missing


def run_selection_priority_gate(
    score: float = 0.0,
    strategy_tags: Optional[list[str]] = None,
    policy_tags: Optional[list[str]] = None,
    version_score: float = 0.0,
    narrative_score: float = 0.0,
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    fund_flow_unit_verified: bool = False,
    risk_flags: Optional[list[str]] = None,
    risk_penalty: float = 0.0,
    resonance_count: int = 0,
    game_balance: str = "",
    has_price_data: bool = True,
    has_event_data: bool = False,
    has_fund_flow_data: bool = False,
    has_risk_assessment: bool = False,
    cfg: Optional[StrategyConfig] = None,  # [M-004]
    score_already_includes_subscores: bool = True,  # [S-009] selection_gate_fix
) -> SelectionPriorityResult:
    """Run the unified selection priority gate on a candidate.

    Args:
        score: Aggregate tech + event + pullback score from merge_signals().
            WARNING: If called from candidate_engine.evaluate_symbol(), this value
            already includes version_score, narrative_score, fund_flow_anomaly_score,
            and risk_penalty.  Set ``score_already_includes_subscores=True`` (default)
            to avoid double-counting.  [S-009] selection_gate_fix
        strategy_tags: All strategy tags on the candidate.
        policy_tags: Policy version tags (S-001).
        version_score: Policy version score (S-001).
        narrative_score: Narrative quality score (S-002).
        fund_flow_anomaly_score: Fund flow anomaly score (T-003).
        fund_flow_anomaly_tags: Fund flow anomaly tags (T-003).
        fund_flow_unit_verified: Whether fund flow unit was verified (T-003).
        risk_flags: Risk flags (S-003).
        risk_penalty: Risk penalty score (negative, S-003).
        resonance_count: Resonance count across categories (S-004).
        game_balance: Game balance verdict (S-004).
        has_price_data: Whether price data was available.
        has_event_data: Whether event/news data was available.
        has_fund_flow_data: Whether fund flow data was provided.
        has_risk_assessment: Whether risk assessment was performed.
        score_already_includes_subscores: [S-009] If True (default), ``score`` is
            treated as a cumulative total that already includes sub-scores, so the
            composite is ``score + completeness_bonus`` only.  If False, the legacy
            formula ``score + version_score + narrative_score + ...`` is used.

    Returns:
        SelectionPriorityResult with composite score, gate verdict, and explanations.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    tags = set(strategy_tags or [])
    p_tags = list(policy_tags or [])
    r_flags = set(risk_flags or [])
    ff_tags = set(fund_flow_anomaly_tags or [])

    refs: list[dict] = []

    has_policy = version_score > 0 or len(p_tags) > 0
    has_narrative = narrative_score > 0 or "EVENT_CATALYST" in tags or "NARRATIVE_QUALITY" in tags
    has_tech = "VCP" in tags or "PULLBACK_SUPPORT" in tags
    has_fund = bool(ff_tags - {"NET_OUTFLOW_DOMINANT"}) and fund_flow_anomaly_score > 0 and fund_flow_unit_verified  # [S-009] selection_gate_fix

    category_hits: list[str] = []
    if has_policy:
        category_hits.append("policy")
        refs.append({"field": "signal_category", "value": "policy", "source": "version_score", "score": version_score})
    if has_narrative:
        category_hits.append("narrative")
        refs.append({"field": "signal_category", "value": "narrative", "source": "narrative_score", "score": narrative_score})
    if has_tech:
        category_hits.append("tech")
        refs.append({"field": "signal_category", "value": "tech", "source": "strategy_tags", "score": score})
    if has_fund:
        category_hits.append("fund")
        refs.append({"field": "signal_category", "value": "fund", "source": "fund_flow_anomaly", "score": fund_flow_anomaly_score})

    positive_category_count = len(category_hits)

    data_completeness, missing_evidence = _compute_data_completeness(
        has_price_data=has_price_data,
        has_event_data=has_event_data,
        has_fund_flow_data=has_fund_flow_data,
        has_fund_flow_unit_verified=fund_flow_unit_verified,
        has_technical_signal=has_tech,
        has_policy_signal=has_policy,
        has_narrative_signal=has_narrative,
        has_risk_assessment=has_risk_assessment,
    )

    completeness_bonus = round(data_completeness * cfg.gate_completeness_bonus_factor, 2)  # [M-004]

    # [S-009] selection_gate_fix — avoid double-counting sub-scores
    if score_already_includes_subscores:
        composite_score = score + completeness_bonus
    else:
        composite_score = score + version_score + narrative_score + fund_flow_anomaly_score + risk_penalty + completeness_bonus
    composite_score = max(0.0, min(composite_score, cfg.gate_max_composite_score))
    composite_score = round(composite_score, 2)

    has_high_risk = bool(r_flags & set(cfg.risk_high_severity_flags))  # [M-004]
    many_risks = len(r_flags) >= cfg.risk_many_flags_threshold
    heavy_penalty = risk_penalty <= cfg.risk_heavy_penalty_threshold

    fragile_or_crowded = game_balance in {"fragile", "crowded"}
    favorable_or_neutral = game_balance in {"favorable", "neutral"}

    gate_passed = False
    why_deep_ta = ""
    why_not_deep_ta = ""

    can_pass_gate = (
        positive_category_count >= cfg.gate_min_positive_categories  # [M-004]
        and not has_high_risk
        and not many_risks
        and not heavy_penalty
        and not fragile_or_crowded
        and data_completeness >= cfg.gate_completeness_min
    )

    if can_pass_gate:
        gate_passed = True
        reasons = []
        if has_policy:
            reasons.append(f"政策版本({', '.join(p_tags[:2])})")
        if has_narrative:
            reasons.append(f"叙事质量({narrative_score:.0f}分)")
        if has_tech:
            tech_tags = [t for t in ["VCP", "PULLBACK_SUPPORT"] if t in tags]
            reasons.append(f"技术形态({', '.join(tech_tags)})")
        if has_fund:
            reasons.append("资金异动")
        reasons_str = " + ".join(reasons)
        why_deep_ta = f"{positive_category_count}类信号共振({reasons_str})，风险可控，值得深度分析"
        refs.append({"field": "gate_passed", "value": True, "reason": f"{positive_category_count} categories, risk manageable"})
    else:
        blockers: list[str] = []
        if positive_category_count < cfg.gate_min_positive_categories:  # [M-004]
            blockers.append(f"仅{positive_category_count}类正向信号(需≥{cfg.gate_min_positive_categories})")
            refs.append({"field": "gate_blocked", "value": "insufficient_categories", "detail": f"{positive_category_count}/{cfg.gate_min_positive_categories}"})
        if has_high_risk:
            blockers.append(f"高风险标签({', '.join(r_flags & _SEVERITY_HIGH_RISK_FLAGS)})")
            refs.append({"field": "gate_blocked", "value": "high_risk_flags"})
        if many_risks:
            blockers.append(f"风险标签过多({len(r_flags)}个)")
            refs.append({"field": "gate_blocked", "value": "too_many_risks"})
        if heavy_penalty:
            blockers.append(f"风险罚分过重({risk_penalty})")
            refs.append({"field": "gate_blocked", "value": "heavy_penalty"})
        if fragile_or_crowded:
            blockers.append(f"博弈平衡不佳({game_balance})")
            refs.append({"field": "gate_blocked", "value": "bad_game_balance"})
        if data_completeness < cfg.gate_completeness_min:  # [M-004]
            blockers.append(f"数据完整度不足({data_completeness:.0%})")
            refs.append({"field": "gate_blocked", "value": "low_data_completeness"})
        why_not_deep_ta = "；".join(blockers)

    if positive_category_count >= cfg.gate_rank_a_min_categories and composite_score >= cfg.gate_rank_a_min_composite and data_completeness >= cfg.gate_rank_a_min_completeness:  # [M-004]
        priority_rank = "A"
    elif positive_category_count >= cfg.gate_rank_b_min_categories and composite_score >= cfg.gate_rank_b_min_composite:
        priority_rank = "B"
    else:
        priority_rank = "C"

    if has_high_risk or many_risks:
        priority_rank = "C"

    why_deep_ta = _sanitize(why_deep_ta)
    why_not_deep_ta = _sanitize(why_not_deep_ta)

    return SelectionPriorityResult(
        composite_score=composite_score,
        signal_category_hits=category_hits,
        positive_category_count=positive_category_count,
        data_completeness=data_completeness,
        missing_evidence=missing_evidence,
        why_deep_ta=why_deep_ta,
        why_not_deep_ta=why_not_deep_ta,
        gate_passed=gate_passed,
        priority_rank=priority_rank,
        gate_refs=refs,
    )
