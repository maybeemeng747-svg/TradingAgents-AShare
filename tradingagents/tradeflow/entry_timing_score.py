# [SCORE-002] entry_timing_adapter
"""Entry Timing Score Card — reuses existing TradeFlow candidate fields to produce
an independent ``entry_timing`` 0-100 score with five sub-dimensions.

Sub-dimensions:
1. valuation_position   — 估值预期位置 (pricing gap, support proximity)
2. crowding             — 拥挤度 (game balance, resonance, category breadth)
3. fund_price_confirm   — 资金价格确认 (fund flow anomaly, unit verification)
4. catalyst_effectiveness — 催化有效性 (event score, ambush score, contradictions)
5. downside_odds        — 下行赔率 (risk penalty, invalid price safety margin)

Design constraints:
- No LLM calls, no network, no prompts modification.
- Purely deterministic mapping from existing candidate fields.
- Same inputs always produce the same output (stable/deterministic).
- Research snapshot scores are background evidence only and never
  inflate entry_timing.
- Missing data must lower confidence, never produce a neutral perfect score.

[SCORE-002-R1] entry_timing API contract & risk deduction persistence:
- entry_timing_card is exposed as a structured response model
  (api.tradeflow_schemas.EntryTimingCard), not an opaque Dict[str, Any].
- every deduction applied to the composite entry_timing is persisted as a
  RiskDeductionRecord(source_field / raw_score / applied_deduction) on the
  result, so downstream/DB readback can reconstruct the penalty path.
- entry_timing_pre_flags captures the score before timing risk flag penalty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TimingDimension:
    """Single sub-dimension of the entry timing score card."""
    score: float = 0.0
    weight: float = 0.0
    status: str = "missing"
    source_fields: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)


# [SCORE-002-R1] risk_deduction_persistence
@dataclass
class RiskDeductionRecord:
    """Structured record of a single risk deduction applied to entry_timing.

    Persists the audit trail so downstream consumers (and DB readback) can
    reconstruct exactly why the composite score was lowered, instead of only
    seeing the final ``risk_penalty`` scalar.

    Fields:
        source_field: the input field that triggered this deduction
                      (e.g. "risk_penalty", "timing_risk_flags", "contradiction_level").
        raw_score:    the raw value observed on that source field (sign preserved
                      for risk_penalty; flag count for timing_risk_flags).
        applied_deduction: the points actually subtracted from entry_timing (>= 0).
    """
    source_field: str = ""
    raw_score: float = 0.0
    applied_deduction: float = 0.0


@dataclass
class EntryTimingResult:
    """Full entry timing score card."""
    entry_timing: float = 0.0
    data_status: str = "missing"
    valuation_position: TimingDimension = field(default_factory=lambda: TimingDimension(weight=0.25))
    crowding: TimingDimension = field(default_factory=lambda: TimingDimension(weight=0.20))
    fund_price_confirm: TimingDimension = field(default_factory=lambda: TimingDimension(weight=0.20))
    catalyst_effectiveness: TimingDimension = field(default_factory=lambda: TimingDimension(weight=0.25))
    downside_odds: TimingDimension = field(default_factory=lambda: TimingDimension(weight=0.10))
    timing_risk_flags: list[str] = field(default_factory=list)
    # [SCORE-002-R1] risk_deduction_persistence — structured audit trail of
    # every deduction applied to the composite entry_timing. Empty when no
    # deductions were applied. Allows downstream/DB readback to reconstruct
    # the penalty path instead of only seeing the final scalar.
    risk_deductions: list[RiskDeductionRecord] = field(default_factory=list)
    # entry_timing before any timing_risk_flags deduction was applied, so the
    # flag penalty is separately attributable.
    entry_timing_pre_flags: Optional[float] = None


# ---------------------------------------------------------------------------
# Timing risk flag constants
# ---------------------------------------------------------------------------

_TIMING_RISK_FLAG_KEYWORDS: tuple[str, ...] = (
    "第一次大跌",
    "跌停",
    "跌停未打开",
    "爆量破位",
    "板块退潮",
    "limit_down",
    "first_big_drop",
    "volume_breakdown",
    "sector_retreat",
)


# ---------------------------------------------------------------------------
# Dimension scorers (each returns 0.0-100.0)
# ---------------------------------------------------------------------------

def _score_valuation_position(
    pricing_gap_score: float = 0.0,
    current_price: Optional[float] = None,
    support_price: Optional[float] = None,
) -> tuple[float, list[str], list[str], list[str]]:
    """估值预期位置: pricing gap + proximity to support.

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_gap = pricing_gap_score > 0
    has_support = support_price is not None and support_price > 0 and current_price is not None and current_price > 0

    if not has_gap and not has_support:
        missing_fields.extend(["pricing_gap_score", "support_price"])
        return 0.0, source_fields, reasons, missing_fields

    components: list[float] = []

    if has_gap:
        source_fields.append("pricing_gap_score")
        # pricing_gap_score is typically 0-50+, normalize to 0-100
        gap_norm = min(100.0, pricing_gap_score * 2.0)
        components.append(gap_norm)
        reasons.append(f"估值预期缺口 {pricing_gap_score:.0f}")

    if has_support:
        source_fields.extend(["current_price", "support_price"])
        # Distance above support: closer = better entry opportunity
        margin_pct = (current_price - support_price) / support_price * 100.0
        if margin_pct <= 0:
            # Below support — already broken, bad signal
            support_score = 0.0
            reasons.append(f"价格已破支撑({margin_pct:+.1f}%)")
        elif margin_pct <= 5:
            # Very close to support — ideal entry zone
            support_score = 80.0 + (5.0 - margin_pct) * 4.0
            reasons.append(f"接近支撑位({margin_pct:+.1f}%)")
        elif margin_pct <= 15:
            # Moderate distance
            support_score = 50.0 + (15.0 - margin_pct) * 3.0
            reasons.append(f"距支撑位{margin_pct:.1f}%")
        else:
            # Far from support
            support_score = max(10.0, 50.0 - margin_pct * 0.5)
            reasons.append(f"远离支撑位({margin_pct:.1f}%)")
        components.append(support_score)
    else:
        missing_fields.append("support_price")

    score = sum(components) / len(components) if components else 0.0
    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


def _score_crowding(
    game_balance: str = "",
    resonance_count: int = 0,
    positive_category_count: int = 0,
    overheat_penalty: float = 0.0,
) -> tuple[float, list[str], list[str], list[str]]:
    """拥挤度: less crowded = higher score.

    game_balance: favorable/neutral/crowded/fragile
    resonance_count: number of perspective resonances (0-4)
    positive_category_count: how many signal categories are positive
    overheat_penalty: negative penalty from overheat detection

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_balance = bool(game_balance)
    has_resonance = resonance_count > 0
    has_categories = positive_category_count > 0
    has_overheat = overheat_penalty < 0

    if not has_balance and not has_resonance and not has_categories:
        missing_fields.extend(["game_balance", "resonance_count", "positive_category_count"])
        return 0.0, source_fields, reasons, missing_fields

    components: list[float] = []

    # Game balance component (0-100)
    if has_balance:
        source_fields.append("game_balance")
        balance_map = {
            "favorable": 90.0,
            "neutral": 55.0,
            "crowded": 25.0,
            "fragile": 10.0,
        }
        bal_score = balance_map.get(game_balance, 40.0)
        components.append(bal_score)
        reasons.append(f"多空格局={game_balance}")

    # Resonance component (0-100)
    if has_resonance:
        source_fields.append("resonance_count")
        res_score = min(100.0, resonance_count * 30.0)
        components.append(res_score)
        reasons.append(f"共振视角数={resonance_count}")
    else:
        missing_fields.append("resonance_count")

    # Category breadth component (0-100)
    if has_categories:
        source_fields.append("positive_category_count")
        cat_score = min(100.0, positive_category_count * 30.0)
        components.append(cat_score)
        reasons.append(f"正向类别数={positive_category_count}")
    else:
        missing_fields.append("positive_category_count")

    # Overheat penalty reduces score
    overheat_adj = 0.0
    if has_overheat:
        source_fields.append("overheat_penalty")
        overheat_adj = min(50.0, abs(overheat_penalty))
        reasons.append(f"过热惩罚-{overheat_adj:.0f}")

    base = sum(components) / len(components) if components else 0.0
    score = max(0.0, base - overheat_adj)

    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


def _score_fund_price_confirm(
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_unit_verified: bool = False,
    technical_score: float = 0.0,
) -> tuple[float, list[str], list[str], list[str]]:
    """资金价格确认: fund flow confirmation + technical backing.

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_ff = fund_flow_anomaly_score > 0
    has_tech = technical_score > 0

    if not has_ff and not has_tech:
        missing_fields.extend(["fund_flow_anomaly_score", "technical_score"])
        return 0.0, source_fields, reasons, missing_fields

    components: list[float] = []

    # Fund flow component (0-100)
    if has_ff:
        source_fields.append("fund_flow_anomaly_score")
        if fund_flow_unit_verified:
            source_fields.append("fund_flow_unit_verified")
            # Verified + high score = strong confirmation
            ff_score = min(100.0, fund_flow_anomaly_score * 5.0)
            reasons.append(f"资金共振({fund_flow_anomaly_score:.0f},单位已校验)")
        else:
            # Unverified: reduce confidence significantly
            ff_score = min(50.0, fund_flow_anomaly_score * 2.5)
            reasons.append(f"资金信号({fund_flow_anomaly_score:.0f},单位未校验)")
        components.append(ff_score)

    # Technical backing (0-100)
    if has_tech:
        source_fields.append("technical_score")
        tech_norm = min(100.0, technical_score)
        components.append(tech_norm)
        reasons.append(f"技术分={technical_score:.0f}")
    else:
        missing_fields.append("technical_score")

    score = sum(components) / len(components) if components else 0.0

    # Hard rule: unverified unit cannot produce high confirmation
    if has_ff and not fund_flow_unit_verified and score > 50.0:
        score = 50.0
        reasons.append("资金单位未校验,置信度上限50")

    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


def _score_catalyst_effectiveness(
    event_score: float = 0.0,
    ambush_score: float = 0.0,
    narrative_score: float = 0.0,
    contradiction_level: str = "",
) -> tuple[float, list[str], list[str], list[str]]:
    """催化有效性: event/narrative quality + mandate ambush, penalized by contradictions.

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_event = event_score > 0
    has_ambush = ambush_score > 0
    has_narrative = narrative_score > 0

    if not has_event and not has_ambush and not has_narrative:
        missing_fields.extend(["event_score", "ambush_score", "narrative_score"])
        return 0.0, source_fields, reasons, missing_fields

    components: list[float] = []

    if has_event:
        source_fields.append("event_score")
        components.append(min(100.0, event_score))
        reasons.append(f"事件分={event_score:.0f}")

    if has_ambush:
        source_fields.append("ambush_score")
        components.append(min(100.0, ambush_score))
        reasons.append(f"埋伏分={ambush_score:.0f}")

    if has_narrative:
        source_fields.append("narrative_score")
        components.append(min(100.0, narrative_score))
        reasons.append(f"叙事分={narrative_score:.0f}")

    base = sum(components) / len(components) if components else 0.0

    # Contradiction penalty
    contradiction_penalty = 0.0
    if contradiction_level:
        source_fields.append("contradiction_level")
        penalty_map = {
            "high": 50.0,
            "medium": 25.0,
            "low": 10.0,
            "none": 0.0,
        }
        contradiction_penalty = penalty_map.get(contradiction_level, 20.0)
        if contradiction_penalty > 0:
            reasons.append(f"矛盾级别={contradiction_level},扣{contradiction_penalty:.0f}")

    score = max(0.0, base - contradiction_penalty)
    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


def _score_downside_odds(
    risk_penalty: float = 0.0,
    invalid_price: Optional[float] = None,
    current_price: Optional[float] = None,
    risk_flags: Optional[list[str]] = None,
) -> tuple[float, list[str], list[str], list[str]]:
    """下行赔率: low risk + good safety margin above invalid price = high score.

    Returns (score, source_fields, reasons, missing_fields).
    """
    source_fields: list[str] = []
    reasons: list[str] = []
    missing_fields: list[str] = []

    has_risk = risk_penalty != 0.0
    has_margin = invalid_price is not None and invalid_price > 0 and current_price is not None and current_price > 0
    has_flags = bool(risk_flags)

    if not has_risk and not has_margin:
        missing_fields.extend(["risk_penalty", "invalid_price"])
        return 0.0, source_fields, reasons, missing_fields

    components: list[float] = []

    # Risk penalty component (0-100, higher = less risk)
    if has_risk:
        source_fields.append("risk_penalty")
        # risk_penalty is negative (worse = more negative); invert to 0-100
        risk_score = max(0.0, 100.0 - abs(risk_penalty) * 2.0)
        components.append(risk_score)
        reasons.append(f"风险惩罚={risk_penalty:.0f}")
    else:
        missing_fields.append("risk_penalty")

    # Safety margin above invalid price (0-100)
    if has_margin:
        source_fields.extend(["invalid_price", "current_price"])
        margin_pct = (current_price - invalid_price) / current_price * 100.0
        if margin_pct <= 0:
            margin_score = 0.0
            reasons.append(f"价格已破止损({margin_pct:+.1f}%)")
        elif margin_pct <= 3:
            margin_score = 20.0
            reasons.append(f"逼近止损({margin_pct:+.1f}%)")
        elif margin_pct <= 8:
            margin_score = 50.0
            reasons.append(f"止损距离{margin_pct:.1f}%")
        elif margin_pct <= 15:
            margin_score = 70.0
            reasons.append(f"止损距离{margin_pct:.1f}%")
        else:
            margin_score = 85.0
            reasons.append(f"止损充裕({margin_pct:.1f}%)")
        components.append(margin_score)
    else:
        missing_fields.append("invalid_price")

    # Risk flags penalty
    if has_flags:
        source_fields.append("risk_flags")
        # Count timing-specific risk flags
        timing_flags = [f for f in risk_flags if any(kw in f for kw in _TIMING_RISK_FLAG_KEYWORDS)]
        if timing_flags:
            flag_penalty = min(40.0, len(timing_flags) * 15.0)
            reasons.append(f"时机风险标志: {', '.join(timing_flags[:3])}")
            base = sum(components) / len(components) if components else 50.0
            score = max(0.0, base - flag_penalty)
            return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields

    score = sum(components) / len(components) if components else 0.0
    return round(min(100.0, max(0.0, score)), 1), source_fields, reasons, missing_fields


# ---------------------------------------------------------------------------
# Timing risk flags detection
# ---------------------------------------------------------------------------

def detect_timing_risk_flags(
    risk_flags: Optional[list[str]] = None,
    overheat_flags: Optional[list[str]] = None,
) -> list[str]:
    """Extract timing-specific risk flags from candidate flags lists.

    Timing risk flags are: 第一次大跌, 跌停未打开, 爆量破位, 板块退潮.
    """
    flags: list[str] = []
    all_flags = list(risk_flags or []) + list(overheat_flags or [])
    for flag in all_flags:
        if any(kw in flag for kw in _TIMING_RISK_FLAG_KEYWORDS):
            if flag not in flags:
                flags.append(flag)
    return flags


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_entry_timing(
    *,
    pricing_gap_score: float = 0.0,
    current_price: Optional[float] = None,
    support_price: Optional[float] = None,
    game_balance: str = "",
    resonance_count: int = 0,
    positive_category_count: int = 0,
    overheat_penalty: float = 0.0,
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_unit_verified: bool = False,
    technical_score: float = 0.0,
    event_score: float = 0.0,
    ambush_score: float = 0.0,
    narrative_score: float = 0.0,
    contradiction_level: str = "",
    risk_penalty: float = 0.0,
    invalid_price: Optional[float] = None,
    risk_flags: Optional[list[str]] = None,
    overheat_flags: Optional[list[str]] = None,
) -> EntryTimingResult:
    """Compute entry_timing score card from existing TradeFlow candidate fields.

    Purely deterministic: same inputs always produce the same output.
    No LLM, no network, no prompts modification.

    Args:
        Pricing gap score (0-50+), current/support/invalid prices,
        game balance state, resonance/category counts, overheat penalty,
        fund flow anomaly score + unit verification, technical/event/
        ambush/narrative scores, contradiction level, risk penalty,
        risk/overheat flags.

    Returns:
        EntryTimingResult with entry_timing 0-100 and five sub-dimensions,
        each carrying status/source_fields/reasons/missing_fields.
    """
    # 1. Valuation position
    val_score, val_src, val_reasons, val_missing = _score_valuation_position(
        pricing_gap_score=pricing_gap_score,
        current_price=current_price,
        support_price=support_price,
    )

    # 2. Crowding
    crow_score, crow_src, crow_reasons, crow_missing = _score_crowding(
        game_balance=game_balance,
        resonance_count=resonance_count,
        positive_category_count=positive_category_count,
        overheat_penalty=overheat_penalty,
    )

    # 3. Fund-price confirmation
    fund_score, fund_src, fund_reasons, fund_missing = _score_fund_price_confirm(
        fund_flow_anomaly_score=fund_flow_anomaly_score,
        fund_flow_unit_verified=fund_flow_unit_verified,
        technical_score=technical_score,
    )

    # 4. Catalyst effectiveness
    cat_score, cat_src, cat_reasons, cat_missing = _score_catalyst_effectiveness(
        event_score=event_score,
        ambush_score=ambush_score,
        narrative_score=narrative_score,
        contradiction_level=contradiction_level,
    )

    # 5. Downside odds
    odds_score, odds_src, odds_reasons, odds_missing = _score_downside_odds(
        risk_penalty=risk_penalty,
        invalid_price=invalid_price,
        current_price=current_price,
        risk_flags=risk_flags,
    )

    # Timing risk flags
    timing_flags = detect_timing_risk_flags(risk_flags=risk_flags, overheat_flags=overheat_flags)

    # Build dimension results
    def _dim_status(has_data: bool, missing: list[str]) -> str:
        if has_data:
            return "ok"
        return "missing"

    valuation_dim = TimingDimension(
        score=val_score, weight=0.25,
        status=_dim_status(bool(val_src), val_missing),
        source_fields=val_src, reasons=val_reasons, missing_fields=val_missing,
    )
    crowding_dim = TimingDimension(
        score=crow_score, weight=0.20,
        status=_dim_status(bool(crow_src), crow_missing),
        source_fields=crow_src, reasons=crow_reasons, missing_fields=crow_missing,
    )
    fund_dim = TimingDimension(
        score=fund_score, weight=0.20,
        status=_dim_status(bool(fund_src), fund_missing),
        source_fields=fund_src, reasons=fund_reasons, missing_fields=fund_missing,
    )
    catalyst_dim = TimingDimension(
        score=cat_score, weight=0.25,
        status=_dim_status(bool(cat_src), cat_missing),
        source_fields=cat_src, reasons=cat_reasons, missing_fields=cat_missing,
    )
    downside_dim = TimingDimension(
        score=odds_score, weight=0.10,
        status=_dim_status(bool(odds_src), odds_missing),
        source_fields=odds_src, reasons=odds_reasons, missing_fields=odds_missing,
    )

    # Weighted composite
    dimensions = [valuation_dim, crowding_dim, fund_dim, catalyst_dim, downside_dim]
    total_weight = sum(d.weight for d in dimensions)
    available_weight = sum(d.weight for d in dimensions if d.status == "ok")

    if available_weight <= 0:
        # All dimensions missing
        entry_timing = 0.0
        data_status = "missing"
    else:
        weighted_sum = sum(d.score * d.weight for d in dimensions if d.status == "ok")
        entry_timing = round(weighted_sum / available_weight, 1)
        if available_weight < total_weight:
            data_status = "partial"
        else:
            data_status = "ok"

    # [SCORE-002-R1] risk_deduction_persistence — record the timing risk flag
    # deduction as a structured record so it survives serialization/DB round-trip.
    # entry_timing_pre_flags captures the score before flag penalty so the flag
    # contribution is separately attributable.
    entry_timing_pre_flags = round(min(100.0, max(0.0, entry_timing)), 1)
    risk_deductions: list[RiskDeductionRecord] = []

    # Timing risk flags reduce overall score
    if timing_flags:
        flag_penalty = min(30.0, len(timing_flags) * 10.0)
        pre = entry_timing
        entry_timing = round(max(0.0, entry_timing - flag_penalty), 1)
        actual_flag_deduction = round(pre - entry_timing, 1)
        if actual_flag_deduction > 0:
            risk_deductions.append(RiskDeductionRecord(
                source_field="timing_risk_flags",
                raw_score=float(len(timing_flags)),
                applied_deduction=actual_flag_deduction,
            ))

    entry_timing = round(min(100.0, max(0.0, entry_timing)), 1)

    # [SCORE-002-R1] Record within-dimension risk deductions that lowered the
    # weighted composite. Each deduction is attributable to entry_timing via
    # its dimension weight, so the recorded applied_deduction is the points
    # actually removed from the (pre-flag) composite entry_timing.
    _contradiction_penalty_map = {"high": 50.0, "medium": 25.0, "low": 10.0, "none": 0.0}
    if contradiction_level and contradiction_level in _contradiction_penalty_map:
        cp = _contradiction_penalty_map[contradiction_level]
        if cp > 0:
            # catalyst_effectiveness weight only contributes when its dimension
            # status is "ok"; otherwise the penalty had no path into the composite.
            if catalyst_dim.status == "ok" and available_weight > 0:
                baseline_catalyst_score, *_ = _score_catalyst_effectiveness(
                    event_score=event_score,
                    ambush_score=ambush_score,
                    narrative_score=narrative_score,
                    contradiction_level="",
                )
                actual_dimension_loss = round(
                    max(0.0, baseline_catalyst_score - catalyst_dim.score),
                    1,
                )
                applied = round(
                    actual_dimension_loss * (catalyst_dim.weight / available_weight),
                    1,
                )
                if applied > 0:
                    risk_deductions.append(RiskDeductionRecord(
                        source_field="contradiction_level",
                        raw_score=cp,
                        applied_deduction=applied,
                    ))

    # risk_penalty / invalid_price deductions flow through the downside_odds
    # dimension. Compare the actual dimension score with and without the risk
    # penalty while keeping all other inputs identical. Averaging a moderate
    # risk component with a weak stop margin can otherwise improve the
    # dimension, in which case no deduction should be claimed.
    if risk_penalty != 0.0 and downside_dim.status == "ok" and available_weight > 0:
        baseline_downside_score, *_ = _score_downside_odds(
            risk_penalty=0.0,
            invalid_price=invalid_price,
            current_price=current_price,
            risk_flags=risk_flags,
        )
        actual_dimension_loss = round(
            max(0.0, baseline_downside_score - downside_dim.score),
            1,
        )
        if actual_dimension_loss > 0:
            applied = round(
                actual_dimension_loss * (downside_dim.weight / available_weight),
                1,
            )
            if applied > 0:
                risk_deductions.append(RiskDeductionRecord(
                    source_field="risk_penalty",
                    raw_score=float(risk_penalty),
                    applied_deduction=applied,
                ))

    return EntryTimingResult(
        entry_timing=entry_timing,
        data_status=data_status,
        valuation_position=valuation_dim,
        crowding=crowding_dim,
        fund_price_confirm=fund_dim,
        catalyst_effectiveness=catalyst_dim,
        downside_odds=downside_dim,
        timing_risk_flags=timing_flags,
        risk_deductions=risk_deductions,
        entry_timing_pre_flags=entry_timing_pre_flags if timing_flags else None,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _dim_to_dict(dim: TimingDimension) -> dict:
    return {
        "score": dim.score,
        "weight": dim.weight,
        "status": dim.status,
        "source_fields": dim.source_fields,
        "reasons": dim.reasons,
        "missing_fields": dim.missing_fields,
    }


# [SCORE-002-R1] risk_deduction_persistence
def _risk_deduction_to_dict(rec: RiskDeductionRecord) -> dict:
    return {
        "source_field": rec.source_field,
        "raw_score": rec.raw_score,
        "applied_deduction": rec.applied_deduction,
    }


def entry_timing_to_dict(result: EntryTimingResult) -> dict:
    """Serialize EntryTimingResult to a JSON-safe dict."""
    return {
        "entry_timing": result.entry_timing,
        "data_status": result.data_status,
        "valuation_position": _dim_to_dict(result.valuation_position),
        "crowding": _dim_to_dict(result.crowding),
        "fund_price_confirm": _dim_to_dict(result.fund_price_confirm),
        "catalyst_effectiveness": _dim_to_dict(result.catalyst_effectiveness),
        "downside_odds": _dim_to_dict(result.downside_odds),
        "timing_risk_flags": result.timing_risk_flags,
        # [SCORE-002-R1] risk_deduction_persistence
        "risk_deductions": [_risk_deduction_to_dict(r) for r in result.risk_deductions],
        "entry_timing_pre_flags": result.entry_timing_pre_flags,
    }
