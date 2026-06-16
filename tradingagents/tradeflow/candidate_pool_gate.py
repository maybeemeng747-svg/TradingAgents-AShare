# [TF-QUALITY-001] candidate_pool_gate
"""Candidate Pool Gate — strict convergence gate for TradeFlow candidate pools.

Responsibilities:
1. Split candidates into main_candidates and observation_candidates.
2. Main candidates must meet "at least two dimension resonance" or
   "single strong signal + data completeness达标".
3. Per-pool size limits: main (Top 5), tech (Top 3), haotian (Top 3).
4. Filtered-out candidates get explicit filter_reason.

Design constraints:
- Does NOT delete or modify existing strategies — only adds convergence gate.
- No LLM calls.
- No strong buy/sell words.
- Deterministic: same inputs always produce same output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG
from .candidate_precision_gate import compute_precision  # [TF-QUALITY-003] candidate_precision_gate


@dataclass
class PoolGateResult:
    main_candidates: list[dict] = field(default_factory=list)
    observation_candidates: list[dict] = field(default_factory=list)
    filtered_candidates: list[dict] = field(default_factory=list)
    pool_counts: dict = field(default_factory=dict)
    gate_summary: str = ""
    # [TF-QUALITY-004] live_pool_calibration — calibration audit trail
    calibration_summary: dict = field(default_factory=dict)


_HAOTIAN_TYPES = {"POLICY_AMBUSH", "POLICY_CONFIRM"}
_TECH_TYPES = {"TECH_TRADE"}


def _check_tech_resonance(entry: dict) -> int:
    """Count how many tech dimensions are valid for TECH_TRADE candidates."""
    hits = 0
    tags = set(entry.get("strategies", []))
    if "VCP" in tags or "PULLBACK_SUPPORT" in tags:
        hits += 1
    ff_score = entry.get("fund_flow_anomaly_score", 0) or 0
    ff_verified = entry.get("fund_flow_unit_verified", False)
    if ff_score > 0 and ff_verified:
        hits += 1
    comp = entry.get("composite_score", 0) or 0
    if comp >= 30:
        hits += 1
    trigger = entry.get("trigger_price")
    invalid = entry.get("invalid_price")
    if trigger and invalid:
        hits += 1
    return hits


def _check_haotian_resonance(entry: dict) -> int:
    """Count how many haotian dimensions are valid for 昊天 candidates."""
    hits = 0
    mandate = entry.get("mandate_score_component", 0) or 0
    if mandate > 0:
        hits += 1
    beneficiary = entry.get("beneficiary_score_component", 0) or 0
    if beneficiary > 0:
        hits += 1
    policy_tags = entry.get("policy_tags", [])
    if policy_tags:
        hits += 1
    counter_ev = entry.get("counter_evidence", [])
    if not counter_ev or all(not ce.get("severity") == "high" for ce in counter_ev):
        hits += 1
    return hits


def _qualify_for_main(entry: dict, cfg: StrategyConfig) -> tuple[bool, str]:
    """Determine if a candidate qualifies for the main pool."""
    positive_cats = entry.get("positive_category_count", 0) or 0
    completeness = entry.get("tradeflow_data_completeness", 0) or entry.get("data_completeness", 0) or 0
    composite = entry.get("composite_score", 0) or 0
    candidate_type = entry.get("candidate_type", "")
    tier = entry.get("tier", "C")

    if tier == "C":
        return False, "C层候选，质量不足"

    if positive_cats >= cfg.pool_min_positive_categories_for_main:
        return True, ""

    if (
        composite >= cfg.pool_min_composite_for_single_strong
        and completeness >= cfg.pool_min_completeness_for_single_strong
    ):
        return True, ""

    if not tier and positive_cats == 0 and completeness == 0:
        return True, ""

    if tier in ("A", "B") and positive_cats == 0 and completeness == 0:
        return True, ""

    if candidate_type in _HAOTIAN_TYPES:
        resonance = _check_haotian_resonance(entry)
        if resonance >= 2:
            return True, ""

    if candidate_type in _TECH_TYPES:
        resonance = _check_tech_resonance(entry)
        if resonance >= 2:
            return True, ""

    reasons = []
    if positive_cats < cfg.pool_min_positive_categories_for_main:
        reasons.append(f"信号类别不足({positive_cats}<{cfg.pool_min_positive_categories_for_main})")
    if composite < cfg.pool_min_composite_for_single_strong:
        reasons.append(f"综合分偏低({composite:.0f}<{cfg.pool_min_composite_for_single_strong:.0f})")
    if completeness < cfg.pool_min_completeness_for_single_strong:
        reasons.append(f"完整度不足({completeness:.0%}<{cfg.pool_min_completeness_for_single_strong:.0%})")

    return False, "；".join(reasons) if reasons else "质量未达主候选门槛"


def _assign_filter_reason(entry: dict, qualified: bool, qual_reason: str) -> str:
    """Generate a filter reason for non-main candidates."""
    if qualified:
        return ""

    reasons = []
    if qual_reason:
        reasons.append(qual_reason)

    completeness = entry.get("tradeflow_data_completeness", 0) or entry.get("data_completeness", 0) or 0
    if completeness < 0.3:
        reasons.append("数据完整度低")
    elif completeness < 0.5:
        reasons.append("数据完整度一般")

    composite = entry.get("composite_score", 0) or 0
    if composite < 20:
        reasons.append("低分候选")

    risk_flags = entry.get("risk_flags", [])
    if risk_flags:
        reasons.append(f"风险标签({len(risk_flags)})")

    return "；".join(reasons) if reasons else "质量未达主候选门槛"


# [TF-QUALITY-004] live_pool_calibration
def _safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


# [TF-QUALITY-004] live_pool_calibration
def _compute_effective_main_cap(
    entries: list[dict],
    cfg: StrategyConfig,
) -> tuple[int, str]:
    """Reduce main cap when top candidates have indistinguishable scores.

    If the spread among the top ``pool_main_max`` composite scores is less
    than ``calibration_score_spread_min``, the main cap is reduced by
    ``calibration_main_cap_reduction`` (floor of 2).  This prevents a
    "flat pile" of candidates that all look the same.

    Only applies when ``pool_main_max >= 4`` so explicitly-tight configs
    are respected.
    """
    if cfg.pool_main_max < 4:
        return cfg.pool_main_max, ""

    if len(entries) <= cfg.pool_main_max:
        return cfg.pool_main_max, ""

    scores = sorted(
        [_safe_float(e.get("composite_score")) for e in entries],
        reverse=True,
    )
    top_scores = scores[: cfg.pool_main_max]
    if len(top_scores) < 2:
        return cfg.pool_main_max, ""

    spread = top_scores[0] - top_scores[-1]
    if spread < cfg.calibration_score_spread_min:
        reduced = max(2, cfg.pool_main_max - cfg.calibration_main_cap_reduction)
        return reduced, (
            f"分项评分差异不足({spread:.1f}<{cfg.calibration_score_spread_min:.0f})，"
            f"主候选上限缩减({cfg.pool_main_max}→{reduced})"
        )

    return cfg.pool_main_max, ""


# [TF-QUALITY-004] live_pool_calibration
def _apply_live_calibration(
    entry: dict,
    precision,
    cfg: StrategyConfig,
) -> tuple[str, str]:
    """Apply per-candidate calibration rules after precision passes.

    Returns ``(action, reason)``:
    - ``("pass", "")`` — candidate survives calibration.
    - ``("observation", reason)`` — downgrade to observation.
    - ``("filtered", reason)`` — downgrade to filtered.

    Rules (evaluated in order):
    1. TECH_TRADE data insufficient: data quality below threshold → filtered.
    2. TECH_TRADE weak VCP: no volume AND no fund-flow confirmation → observation.
    3. POLICY (昊天) evidence insufficient: no policy score OR fewer than
       ``calibration_haotian_min_support_dims`` support dimensions → observation.
    """
    candidate_type = entry.get("candidate_type", "")

    if candidate_type in _TECH_TYPES:
        dims = precision.dimensions if precision else {}

        # Rule 1: data insufficient — below quality threshold
        dq_score = _safe_float(entry.get("data_quality_score"))
        data_comp = (
            _safe_float(entry.get("tradeflow_data_completeness"))
            or _safe_float(entry.get("data_completeness"))
        )
        dq_ok = (
            dq_score >= cfg.calibration_tech_data_quality_min
            or data_comp >= cfg.calibration_tech_data_quality_min / 100.0
        )
        if not dq_ok:
            return "filtered", f"数据不足(质量分{dq_score:.0f})"

        # Rule 2: weak VCP — no 量能 AND no 资金
        has_volume = dims.get("量能", False)
        has_capital = dims.get("资金", False)
        if not has_volume and not has_capital:
            return "observation", "弱VCP(无量能确认/无资金流验证)"

    if candidate_type in _HAOTIAN_TYPES:
        # Rule 3: must have policy_score > 0 AND >= 2 support dimensions
        policy_score = (
            _safe_float(entry.get("policy_score"))
            or _safe_float(entry.get("version_score"))
            or _safe_float(entry.get("mandate_score_component"))
        )
        support_dims = 0
        if _safe_float(entry.get("event_score")) > 0:
            support_dims += 1
        if _safe_float(entry.get("fund_flow_score")) > 0:
            support_dims += 1
        if _safe_float(entry.get("narrative_score")) > 0:
            support_dims += 1
        if (
            _safe_float(entry.get("beneficiary_score_component")) > 0
            or bool(entry.get("beneficiary_path"))
        ):
            support_dims += 1
        if _safe_float(entry.get("mandate_score_component")) > 0:
            support_dims += 1

        if policy_score <= 0:
            return "observation", "昊天左侧无政策主题分"
        if support_dims < cfg.calibration_haotian_min_support_dims:
            return (
                "observation",
                f"昊天证据不足(仅{support_dims}类支撑维度，"
                f"需≥{cfg.calibration_haotian_min_support_dims})",
            )

    return "pass", ""


def run_pool_gate(
    entries: list[dict],
    cfg: Optional[StrategyConfig] = None,
) -> PoolGateResult:
    """Run the candidate pool convergence gate.

    Splits entries into main_candidates (capped), observation_candidates,
    and filtered_candidates with explicit reasons.

    [TF-QUALITY-003] candidate_precision_gate — after the legacy
    ``_qualify_for_main`` check passes, candidates must also satisfy the
    precision gate (multi-dimension resonance). Candidates that pass legacy
    but fail precision go to observation (not filtered). POLICY (昊天)
    candidates that fail precision are NEVER filtered — they can only enter
    observation or haotian main.

    [TF-QUALITY-004] live_pool_calibration — after precision passes, a
    live-calibration layer applies stricter per-candidate rules:
    - TECH weak VCP (no volume AND no fund-flow) → observation.
    - TECH data quality below threshold → filtered.
    - POLICY insufficient evidence (no policy score or < 2 support dims) → observation.
    Additionally, when top candidates have indistinguishable scores
    (spread < ``calibration_score_spread_min``), the main cap is reduced.

    Args:
        entries: Sorted candidate entries (best first).
        cfg: Strategy config (defaults to DEFAULT_STRATEGY_CONFIG).

    Returns:
        PoolGateResult with main/observation/filtered split.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    # [TF-QUALITY-004] live_pool_calibration — dynamic main cap
    effective_main_cap, spread_reason = _compute_effective_main_cap(entries, cfg)

    main: list[dict] = []
    observation: list[dict] = []
    filtered: list[dict] = []

    haotian_count = 0
    tech_count = 0

    # [TF-QUALITY-004] live_pool_calibration — calibration audit trail
    cal_weak_vcp: list[str] = []
    cal_data_insufficient: list[str] = []
    cal_haotian_evidence: list[str] = []

    for entry in entries:
        candidate_type = entry.get("candidate_type", "")
        is_haotian = candidate_type in _HAOTIAN_TYPES
        qualified, qual_reason = _qualify_for_main(entry, cfg)

        if not qualified:
            filter_reason = _assign_filter_reason(entry, qualified, qual_reason)
            entry_copy = dict(entry)
            entry_copy["pool_filter_reason"] = filter_reason
            # [TF-QUALITY-003] Haotian protection: POLICY candidates that fail
            # legacy qualification go to observation, never filtered. They may
            # simply lack short-term breakout, which is not a filter reason.
            if is_haotian:
                entry_copy["pool_status"] = "observation"
                observation.append(entry_copy)
            else:
                entry_copy["pool_status"] = "filtered"
                filtered.append(entry_copy)
            continue

        # [TF-QUALITY-003] candidate_precision_gate — precision resonance check
        precision = compute_precision(entry, cfg)
        if not precision.qualified:
            entry_copy = dict(entry)
            entry_copy["precision_dimensions"] = precision.dimensions
            entry_copy["precision_resonance_count"] = precision.resonance_count
            # POLICY (昊天) candidates NEVER get filtered for precision failure
            pool_reason = precision.reason or "精度门禁未达标"
            entry_copy["pool_filter_reason"] = pool_reason
            entry_copy["pool_status"] = "observation"
            observation.append(entry_copy)
            continue

        # [TF-QUALITY-004] live_pool_calibration — per-candidate calibration
        cal_action, cal_reason = _apply_live_calibration(entry, precision, cfg)
        if cal_action != "pass":
            entry_copy = dict(entry)
            entry_copy["precision_dimensions"] = precision.dimensions
            entry_copy["precision_resonance_count"] = precision.resonance_count
            entry_copy["pool_filter_reason"] = cal_reason
            entry_copy["calibration_rule"] = cal_reason  # [TF-QUALITY-004]
            if cal_action == "filtered" and not is_haotian:
                entry_copy["pool_status"] = "filtered"
                filtered.append(entry_copy)
                cal_data_insufficient.append(entry.get("symbol", ""))
            else:
                entry_copy["pool_status"] = "observation"
                observation.append(entry_copy)
                if "弱VCP" in cal_reason:
                    cal_weak_vcp.append(entry.get("symbol", ""))
                elif "昊天" in cal_reason:
                    cal_haotian_evidence.append(entry.get("symbol", ""))
            continue

        is_tech = candidate_type in _TECH_TYPES

        if is_haotian and haotian_count >= cfg.pool_haotian_max:
            entry_copy = dict(entry)
            entry_copy["pool_filter_reason"] = f"昊天池已满({haotian_count}/{cfg.pool_haotian_max})"
            entry_copy["pool_status"] = "observation"
            observation.append(entry_copy)
            continue

        if is_tech and tech_count >= cfg.pool_tech_max:
            entry_copy = dict(entry)
            entry_copy["pool_filter_reason"] = f"技术池已满({tech_count}/{cfg.pool_tech_max})"
            entry_copy["pool_status"] = "observation"
            observation.append(entry_copy)
            continue

        if len(main) >= effective_main_cap:
            entry_copy = dict(entry)
            cap_label = (
                f"主候选池已满({len(main)}/{effective_main_cap})"
                if effective_main_cap == cfg.pool_main_max
                else f"主候选池已满({len(main)}/{effective_main_cap}，校准缩减)"
            )
            entry_copy["pool_filter_reason"] = cap_label
            entry_copy["pool_status"] = "observation"
            observation.append(entry_copy)
            continue

        entry_copy = dict(entry)
        entry_copy["pool_status"] = "main"
        entry_copy["pool_filter_reason"] = ""
        entry_copy["precision_dimensions"] = precision.dimensions  # [TF-QUALITY-003]
        entry_copy["precision_resonance_count"] = precision.resonance_count  # [TF-QUALITY-003]
        main.append(entry_copy)

        if is_haotian:
            haotian_count += 1
        elif is_tech:
            tech_count += 1

    pool_counts = {
        "main": len(main),
        "observation": len(observation),
        "filtered": len(filtered),
        "haotian_in_main": haotian_count,
        "tech_in_main": tech_count,
        "main_max": cfg.pool_main_max,
        "effective_main_max": effective_main_cap,  # [TF-QUALITY-004]
        "haotian_max": cfg.pool_haotian_max,
        "tech_max": cfg.pool_tech_max,
    }

    summary_parts = [
        f"主候选{len(main)}只",
        f"观察{len(observation)}只",
        f"过滤{len(filtered)}只",
    ]
    gate_summary = "，".join(summary_parts) + "。"

    # [TF-QUALITY-004] live_pool_calibration — calibration summary
    calibration_summary = {
        "weak_vcp_downgraded": cal_weak_vcp,
        "data_insufficient_filtered": cal_data_insufficient,
        "haotian_evidence_insufficient": cal_haotian_evidence,
        "score_spread_reduced": bool(spread_reason),
        "score_spread_reason": spread_reason,
        "effective_main_cap": effective_main_cap,
        "original_main_cap": cfg.pool_main_max,
    }

    return PoolGateResult(
        main_candidates=main,
        observation_candidates=observation,
        filtered_candidates=filtered,
        pool_counts=pool_counts,
        gate_summary=gate_summary,
        calibration_summary=calibration_summary,
    )
