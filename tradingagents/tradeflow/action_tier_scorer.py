# [TF-UX-004] action_tier_scorer
"""Trade Priority Score & Action Tier Scorer for TradeFlow.

Responsibilities:
1. Compute trade_priority_score (0.0-1.0) from 7 factors.
2. Assign action_tier: actionable / watch / scan.
3. Generate action_tier_reason explaining why.

Design constraints:
- No LLM calls.
- Purely rule-based scoring from candidate fields.
- Deterministic: same inputs always produce same output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ActionTierResult:
    trade_priority_score: float = 0.0
    action_tier: str = "scan"
    action_tier_reason: str = ""


_TRIGGER_DISTANCE_MAX_PCT = 5.0
_TRIGGER_CLOSE_PCT = 1.5


def compute_trade_priority_score(
    trigger_price: Optional[float] = None,
    current_price: Optional[float] = None,
    invalid_price: Optional[float] = None,
    observe_state: str = "WAITING",
    data_completeness: float = 0.0,
    composite_score: float = 0.0,
    positive_category_count: int = 0,
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_unit_verified: bool = False,
    risk_penalty: float = 0.0,
    game_balance: str = "",
    ambush_score: float = 0.0,
) -> float:
    """Compute trade_priority_score in [0.0, 1.0].

    Factors (each 0.0-1.0, weighted):
    1. trigger_proximity  (0.20) — how close current_price is to trigger_price
    2. triggered_bonus    (0.15) — already triggered
    3. data_completeness  (0.15) — evidence completeness
    4. signal_strength    (0.20) — composite_score + positive_category_count
    5. liquidity_signal   (0.10) — fund flow anomaly
    6. invalid_distance   (0.10) — distance from invalid_price (safety margin)
    7. single_signal_penalty (0.10) — penalize single-signal candidates
    """
    scores = []

    # 1. trigger_proximity (0.20)
    if trigger_price and trigger_price > 0 and current_price and current_price > 0:
        dist_pct = abs(current_price - trigger_price) / trigger_price * 100
        prox = max(0.0, 1.0 - dist_pct / _TRIGGER_DISTANCE_MAX_PCT)
    elif trigger_price and trigger_price > 0:
        prox = 0.3
    else:
        prox = 0.5
    scores.append(("trigger_proximity", prox, 0.20))

    # 2. triggered_bonus (0.15)
    triggered = 1.0 if observe_state == "TRIGGERED" else 0.0
    scores.append(("triggered_bonus", triggered, 0.15))

    # 3. data_completeness (0.15)
    scores.append(("data_completeness", min(1.0, max(0.0, data_completeness)), 0.15))

    # 4. signal_strength (0.20)
    composite_norm = min(1.0, max(0.0, composite_score / 100.0)) if composite_score > 0 else 0.0
    cat_norm = min(1.0, positive_category_count / 4.0) if positive_category_count > 0 else 0.0
    signal_str = composite_norm * 0.6 + cat_norm * 0.4
    scores.append(("signal_strength", signal_str, 0.20))

    # 5. liquidity_signal (0.10)
    if fund_flow_anomaly_score > 0 and fund_flow_unit_verified:
        liq = min(1.0, fund_flow_anomaly_score / 15.0)
    elif fund_flow_anomaly_score > 0:
        liq = min(1.0, fund_flow_anomaly_score / 20.0) * 0.5
    else:
        liq = 0.0
    scores.append(("liquidity_signal", liq, 0.10))

    # 6. invalid_distance (0.10) — safety margin above invalid_price
    if invalid_price and current_price and current_price > 0 and invalid_price > 0:
        margin_pct = (current_price - invalid_price) / current_price * 100
        inv_dist = min(1.0, max(0.0, margin_pct / 10.0))
    elif invalid_price and invalid_price > 0:
        inv_dist = 0.2
    else:
        inv_dist = 0.5
    scores.append(("invalid_distance", inv_dist, 0.10))

    # 7. single_signal_penalty (0.10) — penalize if only 1 signal category
    if positive_category_count >= 3:
        cat_quality = 1.0
    elif positive_category_count == 2:
        cat_quality = 0.7
    elif positive_category_count == 1:
        cat_quality = 0.2
    else:
        cat_quality = 0.0
    scores.append(("signal_category_quality", cat_quality, 0.10))

    weighted_sum = sum(score * weight for _, score, weight in scores)
    total_weight = sum(weight for _, _, weight in scores)

    raw = weighted_sum / total_weight if total_weight > 0 else 0.0

    # Adjustments
    if risk_penalty < -15.0:
        raw *= 0.6
    elif risk_penalty < -5.0:
        raw *= 0.85

    if game_balance in ("fragile", "crowded"):
        raw *= 0.7
    elif game_balance in ("neutral",):
        raw *= 0.95

    if ambush_score > 0:
        raw = min(1.0, raw + ambush_score / 100.0 * 0.1)

    return round(max(0.0, min(1.0, raw)), 4)


def compute_trigger_distance_pct(
    trigger_price: Optional[float],
    current_price: Optional[float],
) -> Optional[float]:
    """Return distance in pct: positive means current is below trigger (still waiting)."""
    if not trigger_price or trigger_price <= 0 or not current_price or current_price <= 0:
        return None
    return round((trigger_price - current_price) / trigger_price * 100, 2)


def assign_action_tier(
    trade_priority_score: float,
    observe_state: str,
    trigger_price: Optional[float],
    current_price: Optional[float],
    data_completeness: float,
    positive_category_count: int,
    invalid_price: Optional[float] = None,
) -> ActionTierResult:
    """Assign action_tier based on score + hard rules.

    Rules:
    - actionable: score >= 0.7 AND (within 1.5% of trigger OR triggered) AND completeness >= 0.5 AND categories >= 2
    - watch: score >= 0.4 AND completeness >= 0.3 AND not actionable
    - scan: rest
    """
    reasons: list[str] = []

    # Check proximity
    close_to_trigger = False
    if observe_state == "TRIGGERED":
        close_to_trigger = True
    elif trigger_price and trigger_price > 0 and current_price and current_price > 0:
        dist_pct = abs(current_price - trigger_price) / trigger_price * 100
        if dist_pct <= _TRIGGER_CLOSE_PCT:
            close_to_trigger = True

    # Check invalidated
    is_invalidated = observe_state == "INVALIDATED"

    # actionable check
    actionable_score_ok = trade_priority_score >= 0.7
    actionable_proximity_ok = close_to_trigger
    actionable_completeness_ok = data_completeness >= 0.5
    actionable_categories_ok = positive_category_count >= 2

    if is_invalidated:
        tier = "scan"
        reasons.append("已失效，移入扫描池")
    elif (
        actionable_score_ok
        and actionable_proximity_ok
        and actionable_completeness_ok
        and actionable_categories_ok
    ):
        tier = "actionable"
        if observe_state == "TRIGGERED":
            reasons.append("已触发且评分达标")
        else:
            reasons.append("接近触发价且评分达标")
    elif trade_priority_score >= 0.4 and data_completeness >= 0.3:
        tier = "watch"
        blockers = []
        if not actionable_score_ok:
            blockers.append(f"优先评分不足({trade_priority_score:.2f}<0.7)")
        if not actionable_proximity_ok:
            blockers.append("距触发价较远")
        if not actionable_completeness_ok:
            blockers.append(f"完整度不足({data_completeness:.0%}<50%)")
        if not actionable_categories_ok:
            blockers.append(f"信号类别不足({positive_category_count}<2)")
        reasons.append("观察: " + "; ".join(blockers))
    else:
        tier = "scan"
        scan_reasons = []
        if trade_priority_score < 0.4:
            scan_reasons.append(f"优先评分低({trade_priority_score:.2f})")
        if data_completeness < 0.3:
            scan_reasons.append(f"完整度低({data_completeness:.0%})")
        if is_invalidated:
            scan_reasons.append("已失效")
        reasons.append("扫描: " + "; ".join(scan_reasons))

    return ActionTierResult(
        trade_priority_score=trade_priority_score,
        action_tier=tier,
        action_tier_reason="; ".join(reasons),
    )


def run_action_tier_scorer(
    trigger_price: Optional[float] = None,
    current_price: Optional[float] = None,
    invalid_price: Optional[float] = None,
    observe_state: str = "WAITING",
    data_completeness: float = 0.0,
    composite_score: float = 0.0,
    positive_category_count: int = 0,
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_unit_verified: bool = False,
    risk_penalty: float = 0.0,
    game_balance: str = "",
    ambush_score: float = 0.0,
) -> ActionTierResult:
    """Full pipeline: compute score then assign tier."""
    score = compute_trade_priority_score(
        trigger_price=trigger_price,
        current_price=current_price,
        invalid_price=invalid_price,
        observe_state=observe_state,
        data_completeness=data_completeness,
        composite_score=composite_score,
        positive_category_count=positive_category_count,
        fund_flow_anomaly_score=fund_flow_anomaly_score,
        fund_flow_unit_verified=fund_flow_unit_verified,
        risk_penalty=risk_penalty,
        game_balance=game_balance,
        ambush_score=ambush_score,
    )
    return assign_action_tier(
        trade_priority_score=score,
        observe_state=observe_state,
        trigger_price=trigger_price,
        current_price=current_price,
        data_completeness=data_completeness,
        positive_category_count=positive_category_count,
        invalid_price=invalid_price,
    )
