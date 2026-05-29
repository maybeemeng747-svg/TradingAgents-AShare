# [S-008] tradeflow_evidence_gate
"""TradeFlow Evidence Completeness Gate.

Responsibilities:
1. Compute ``tradeflow_data_completeness`` as a fine-grained completeness ratio
   over critical evidence dimensions, beyond the boolean flags already used
   in S-005's ``_compute_data_completeness``.
2. Emit ``missing_data_fields`` that explicitly list which evidence categories
   are absent or invalid.
3. Block A-tier classification and ``need_deep_ta=True`` when completeness
   falls below configurable thresholds.
4. Provide ``what_to_upgrade`` — actionable hints showing "what evidence is
   needed to promote this candidate".

Evidence dimensions (10 total):
  - ohlcv:          Price OHLCV data available and sufficient length.
  - liquidity:      Liquidity above threshold (avg_amount >= 3000万).
  - event_source:   At least one event / news text for the symbol.
  - fund_flow_unit: Fund flow data with unit verification.
  - risk_labels:    Risk assessment performed with evidence-bound labels.
  - tech_signal:    At least one technical signal (VCP / Pullback).
  - policy_signal:  Policy version signal detected with text evidence.
  - narrative_signal: Narrative quality score > 0.
  - fund_signal:    Fund flow anomaly detected (positive tags).
  - game_assessment: Game balance assessed (non-empty verdict).

Gate thresholds:
  - COMPLETENESS_FOR_A_TIER = 0.6  (same as S-007 tier A threshold)
  - COMPLETENESS_FOR_DEEP_TA = 0.5 (same as S-005 gate threshold)

Design constraints:
  - Does NOT modify TA deep report readiness logic (only TradeFlow candidate layer).
  - Data absence can only downgrade or flag gaps — never fabricate data.
  - No external LLM calls.
  - No strong buy/sell words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]


_STRONG_BUY_SELL_WORDS = {
    "立即买入", "重仓买入", "立即清仓", "满仓", "梭哈",
    "强力买入", "强烈推荐", "必买", "抄底", "追涨", "杀跌",
}

TOTAL_EVIDENCE_DIMENSIONS = 10

COMPLETENESS_FOR_A_TIER = 0.6
COMPLETENESS_FOR_DEEP_TA = 0.5

EVIDENCE_DIMENSION_NAMES = {
    "ohlcv": "行情OHLCV数据",
    "liquidity": "流动性达标",
    "event_source": "事件/新闻来源",
    "fund_flow_unit": "资金流单位校验",
    "risk_labels": "风险标签证据",
    "tech_signal": "技术形态信号",
    "policy_signal": "政策版本信号",
    "narrative_signal": "叙事质量信号",
    "fund_signal": "资金异动信号",
    "game_assessment": "博弈平衡评估",
}


@dataclass
class EvidenceGateResult:
    tradeflow_data_completeness: float = 0.0
    missing_data_fields: list[str] = field(default_factory=list)
    present_data_fields: list[str] = field(default_factory=list)
    gate_completeness: float = 0.0
    can_enter_a_tier: bool = False
    can_trigger_deep_ta: bool = False
    what_to_upgrade: list[str] = field(default_factory=list)
    evidence_gate_refs: list[dict] = field(default_factory=list)


def _sanitize(text: str) -> str:
    for w in _STRONG_BUY_SELL_WORDS:
        if w in text:
            text = text.replace(w, "***")
    return text


def compute_evidence_completeness(
    has_ohlcv: bool = False,
    has_liquidity: bool = False,
    has_event_source: bool = False,
    has_fund_flow_unit: bool = False,
    has_risk_labels: bool = False,
    has_tech_signal: bool = False,
    has_policy_signal: bool = False,
    has_narrative_signal: bool = False,
    has_fund_signal: bool = False,
    has_game_assessment: bool = False,
    cfg: Optional[StrategyConfig] = None,  # [M-004]
) -> EvidenceGateResult:
    """Compute evidence completeness for a TradeFlow candidate.

    Each dimension is a boolean indicating whether that piece of evidence
    is present and valid.  The result contains:
    - ``tradeflow_data_completeness``: ratio of present dimensions (0.0 to 1.0).
    - ``missing_data_fields``: human-readable labels for absent dimensions.
    - ``present_data_fields``: human-readable labels for present dimensions.
    - ``gate_completeness``: same as ``tradeflow_data_completeness`` (alias for downstream).
    - ``can_enter_a_tier``: True iff completeness >= COMPLETENESS_FOR_A_TIER.
    - ``can_trigger_deep_ta``: True iff completeness >= COMPLETENESS_FOR_DEEP_TA.
    - ``what_to_upgrade``: actionable hints for missing critical evidence.
    - ``evidence_gate_refs``: structured refs for audit trail.

    Args:
        has_ohlcv: Whether valid OHLCV price data exists (non-empty, >= 40 bars).
        has_liquidity: Whether liquidity meets threshold (avg_amount >= 3000万).
        has_event_source: Whether at least one event/news text is bound to this symbol.
        has_fund_flow_unit: Whether fund flow data has unit verification.
        has_risk_labels: Whether risk assessment was performed with evidence-bound labels.
        has_tech_signal: Whether at least one technical signal (VCP/Pullback) was detected.
        has_policy_signal: Whether a policy version signal was detected with text evidence.
        has_narrative_signal: Whether narrative quality score > 0.
        has_fund_signal: Whether a positive fund flow anomaly was detected.
        has_game_assessment: Whether game balance assessment produced a non-empty verdict.

    Returns:
        EvidenceGateResult with completeness metrics and gate verdicts.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    dimensions = {
        "ohlcv": has_ohlcv,
        "liquidity": has_liquidity,
        "event_source": has_event_source,
        "fund_flow_unit": has_fund_flow_unit,
        "risk_labels": has_risk_labels,
        "tech_signal": has_tech_signal,
        "policy_signal": has_policy_signal,
        "narrative_signal": has_narrative_signal,
        "fund_signal": has_fund_signal,
        "game_assessment": has_game_assessment,
    }

    present_keys = [k for k, v in dimensions.items() if v]
    missing_keys = [k for k, v in dimensions.items() if not v]

    present_count = len(present_keys)
    completeness = round(present_count / cfg.evidence_total_dimensions, 3) if cfg.evidence_total_dimensions > 0 else 0.0  # [M-004]

    missing_fields = [EVIDENCE_DIMENSION_NAMES[k] for k in missing_keys]
    present_fields = [EVIDENCE_DIMENSION_NAMES[k] for k in present_keys]

    can_enter_a = completeness >= cfg.evidence_completeness_for_a_tier  # [M-004]
    can_deep_ta = completeness >= cfg.evidence_completeness_for_deep_ta

    what_to_upgrade: list[str] = []

    critical_dims = {"ohlcv", "liquidity", "risk_labels"}
    critical_missing = [k for k in missing_keys if k in critical_dims]
    if critical_missing:
        labels = [EVIDENCE_DIMENSION_NAMES[k] for k in critical_missing]
        what_to_upgrade.append(f"关键证据缺失: {'; '.join(labels)}")

    signal_dims = {"tech_signal", "policy_signal", "narrative_signal", "fund_signal"}
    signal_missing = [k for k in missing_keys if k in signal_dims]
    if len(signal_missing) >= 3:
        labels = [EVIDENCE_DIMENSION_NAMES[k] for k in signal_missing]
        what_to_upgrade.append(f"信号维度不足: {'; '.join(labels)}")

    if not can_enter_a:
        needed = max(1, int(cfg.evidence_total_dimensions * cfg.evidence_completeness_for_a_tier) - present_count)  # [M-004]
        if needed > 0:
            what_to_upgrade.append(f"完整度需≥{cfg.evidence_completeness_for_a_tier:.0%}(当前{completeness:.0%}，还需{needed}项)")

    if not has_fund_flow_unit and not has_event_source:
        what_to_upgrade.append("需补充资金单位校验或事件来源中至少一项")

    refs: list[dict] = [
        {"field": "tradeflow_data_completeness", "value": completeness,
         "present_count": present_count, "total": cfg.evidence_total_dimensions},  # [M-004]
    ]
    if not can_enter_a:
        refs.append({"field": "evidence_gate_blocked", "value": "completeness_below_a_tier",
                      "threshold": cfg.evidence_completeness_for_a_tier, "actual": completeness})
    if not can_deep_ta:
        refs.append({"field": "evidence_gate_blocked", "value": "completeness_below_deep_ta",
                      "threshold": cfg.evidence_completeness_for_deep_ta, "actual": completeness})
    for k in missing_keys:
        refs.append({"field": "missing_dimension", "value": k,
                      "label": EVIDENCE_DIMENSION_NAMES[k]})

    what_to_upgrade = [_sanitize(h) for h in what_to_upgrade]

    return EvidenceGateResult(
        tradeflow_data_completeness=completeness,
        missing_data_fields=missing_fields,
        present_data_fields=present_fields,
        gate_completeness=completeness,
        can_enter_a_tier=can_enter_a,
        can_trigger_deep_ta=can_deep_ta,
        what_to_upgrade=what_to_upgrade,
        evidence_gate_refs=refs,
    )


def apply_evidence_gate(
    candidate_completeness: float = 0.0,
    candidate_tier: str = "",
    candidate_need_deep_ta: bool = False,
    can_enter_a_tier: bool = True,
    can_trigger_deep_ta: bool = True,
    what_to_upgrade: Optional[list[str]] = None,
    missing_data_fields: Optional[list[str]] = None,
    cfg: Optional[StrategyConfig] = None,  # [M-004]
) -> dict:
    """Apply the evidence gate to a candidate's tier and need_deep_ta.

    This function post-processes the candidate after S-005 and S-007 have
    assigned ``tier`` and ``need_deep_ta``.  If the evidence gate says
    completeness is too low, it overrides:

    - tier A → tier B (with reason)
    - need_deep_ta True → False (with reason)

    Returns a dict with potentially overridden fields:
        - ``tier``: str
        - ``ta_budget_priority``: int
        - ``need_deep_ta``: bool
        - ``tier_reason``: str
        - ``why_not_deep_ta``: str
        - ``missing_data_fields``: list[str]
        - ``what_to_upgrade``: list[str]
        - ``gate_applied``: bool
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    missing = list(missing_data_fields or [])
    upgrade = list(what_to_upgrade or [])

    tier = candidate_tier
    need_deep_ta = candidate_need_deep_ta
    gate_applied = False
    tier_reason_additions: list[str] = []
    why_not_additions: list[str] = []

    if tier == "A" and not can_enter_a_tier:
        tier = "B"
        gate_applied = True
        tier_reason_additions.append(
            f"证据完整度不足({candidate_completeness:.0%}，A层需≥{cfg.evidence_completeness_for_a_tier:.0%})"  # [M-004]
        )
        why_not_additions.append(
            f"证据完整度不足({candidate_completeness:.0%})，禁止A层"
        )

    if need_deep_ta and not can_trigger_deep_ta:
        need_deep_ta = False
        gate_applied = True
        why_not_additions.append(
            f"证据完整度不足({candidate_completeness:.0%}，深挖需≥{cfg.evidence_completeness_for_deep_ta:.0%})"  # [M-004]
        )

    ta_budget = {1: 100, 2: 30}.get({"A": 1, "B": 2, "C": 3}.get(tier, 3), 0)

    tier_reason_combined = "；".join(tier_reason_additions) if tier_reason_additions else ""
    why_not_combined = "；".join(why_not_additions) if why_not_additions else ""

    return {
        "tier": tier,
        "ta_budget_priority": ta_budget,
        "need_deep_ta": need_deep_ta,
        "tier_reason_addition": _sanitize(tier_reason_combined),
        "why_not_deep_ta_addition": _sanitize(why_not_combined),
        "missing_data_fields": missing,
        "what_to_upgrade": [_sanitize(h) for h in upgrade],
        "gate_applied": gate_applied,
    }
