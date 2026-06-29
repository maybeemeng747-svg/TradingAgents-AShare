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
# [H-014] mandate_concentration_gate
from .mandate_concentration_gate import (
    STRENGTH_WEAK,
    build_concentration_summary,
    compute_topic_metrics_map,
    downgrade_reason_for_cap,
    downgrade_reason_for_weak,
    resolve_topic_for_entry,
)


@dataclass
class PoolGateResult:
    main_candidates: list[dict] = field(default_factory=list)
    observation_candidates: list[dict] = field(default_factory=list)
    filtered_candidates: list[dict] = field(default_factory=list)
    pool_counts: dict = field(default_factory=dict)
    gate_summary: str = ""
    # [TF-QUALITY-004] live_pool_calibration — calibration audit trail
    calibration_summary: dict = field(default_factory=dict)
    # [H-014] mandate_concentration_gate — theme concentration audit trail
    concentration_summary: dict = field(default_factory=dict)
    # [TF-QUALITY-005] candidate_pool_strength_tiers — strength tier split of main
    primary_candidates: list[dict] = field(default_factory=list)
    secondary_candidates: list[dict] = field(default_factory=list)
    strength_tier_summary: dict = field(default_factory=dict)


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


# [TF-QUALITY-005] candidate_pool_strength_tiers
TIER_PRIMARY = "primary"
TIER_SECONDARY = "secondary"


# [TF-QUALITY-005] candidate_pool_strength_tiers
def _haotian_support_dim_count(entry: dict) -> int:
    """Count cross-signal support dimensions for a 昊天 candidate.

    Mirrors the support-dim logic in :func:`_apply_live_calibration` so the
    tier classifier reasons about the same evidence set.
    """
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
    return support_dims


# [TF-QUALITY-005] candidate_pool_strength_tiers
def _classify_strength_tier(
    entry: dict,
    cfg: StrategyConfig,
) -> tuple[str, str]:
    """Classify a main candidate into ``primary`` or ``secondary`` tier.

    Returns ``(tier, reason)``:
    - ``(TIER_PRIMARY, "")`` — high conviction, all confirmation dimensions hit.
    - ``(TIER_SECONDARY, reason)`` — passes main pool gate but lacks the extra
      confirmation required to be a top-priority name. ``reason`` is a
      human-readable Chinese string explaining which downgrade rule fired.

    Downgrade rules (any match → secondary). Rules are evaluated in order and
    the first matching rule wins so the reason stays specific:

    1. 数据不足 — data completeness below the secondary floor.
    2. TECH 弱缩量无资金确认 — 量能 or 资金 missing in precision dimensions.
    3. TECH 反弹但趋势未修复 — only 形态 + 触发价 hit, no volume/capital
       confirmation AND composite below primary threshold. This is the
       "半山腰抄底" pattern: pattern-only rebound without trend repair.
    4. TECH 共振维度偏少 — resonance count below primary minimum.
    5. TECH 综合分偏低 — composite below primary minimum (after other rules
       did not fire, this catches generally weak scores).
    6. POLICY 弱主题 — H-014 concentration strength is "weak".
    7. POLICY 证据单一 — only one cross-signal support dimension.
    8. POLICY 共振维度偏少 — resonance count below primary minimum.
    9. 兜底 — composite below primary minimum.
    """
    candidate_type = entry.get("candidate_type", "")
    composite = _safe_float(entry.get("composite_score"))
    resonance = int(entry.get("precision_resonance_count", 0) or 0)
    dims = entry.get("precision_dimensions", {}) or {}
    data_comp = (
        _safe_float(entry.get("tradeflow_data_completeness"))
        or _safe_float(entry.get("data_completeness"))
    )

    # Rule 1: 数据不足 — applies to all candidate types.
    if data_comp < cfg.tier_secondary_data_completeness_min:
        return (
            TIER_SECONDARY,
            f"数据不足(完整度{data_comp:.0%}<{cfg.tier_secondary_data_completeness_min:.0%})",
        )

    if candidate_type in _TECH_TYPES:
        # Rule 2: 弱缩量无资金确认.
        has_volume = bool(dims.get("量能", False))
        has_capital = bool(dims.get("资金", False))
        if cfg.tier_primary_require_volume_and_capital:
            if not has_volume or not has_capital:
                missing = []
                if not has_volume:
                    missing.append("量能")
                if not has_capital:
                    missing.append("资金")
                return (
                    TIER_SECONDARY,
                    f"弱缩量无资金确认(缺{'/'.join(missing)})",
                )

        # Rule 3: 反弹但趋势未修复 — pattern + trigger only, no confirmation.
        has_pattern = bool(dims.get("形态", False))
        has_trigger = bool(dims.get("触发/失效价", False))
        if (
            has_pattern
            and has_trigger
            and not has_volume
            and not has_capital
            and composite < cfg.tier_primary_min_composite
        ):
            return (
                TIER_SECONDARY,
                "反弹但趋势未修复(仅形态+触发价，无量能/资金确认)",
            )

        # Rule 4: 共振维度偏少.
        if resonance < cfg.tier_primary_min_resonance:
            return (
                TIER_SECONDARY,
                f"共振维度偏少({resonance}<{cfg.tier_primary_min_resonance})",
            )

        # Rule 5: 综合分偏低.
        if composite < cfg.tier_primary_min_composite:
            return (
                TIER_SECONDARY,
                f"综合分偏低({composite:.0f}<{cfg.tier_primary_min_composite:.0f})",
            )

        return TIER_PRIMARY, ""

    if candidate_type in _HAOTIAN_TYPES:
        # Rule 6: 弱主题 — H-014 already downgraded topic; never primary.
        strength = entry.get("concentration_strength", "")
        if strength == STRENGTH_WEAK:
            return (
                TIER_SECONDARY,
                f"弱主题({entry.get('concentration_topic', '') or '未分类'})",
            )

        # Rule 7: 证据单一.
        support_dims = _haotian_support_dim_count(entry)
        if support_dims < cfg.tier_primary_haotian_min_support_dims:
            return (
                TIER_SECONDARY,
                f"证据单一(仅{support_dims}类支撑维度，"
                f"需≥{cfg.tier_primary_haotian_min_support_dims})",
            )

        # Rule 8: 共振维度偏少.
        if resonance < cfg.tier_primary_min_resonance:
            return (
                TIER_SECONDARY,
                f"共振维度偏少({resonance}<{cfg.tier_primary_min_resonance})",
            )

        # Rule 9: 综合分偏低.
        if composite < cfg.tier_primary_min_composite:
            return (
                TIER_SECONDARY,
                f"综合分偏低({composite:.0f}<{cfg.tier_primary_min_composite:.0f})",
            )

        return TIER_PRIMARY, ""

    # Other candidate types (EVENT_WATCH / UNCLASSIFIED): primary by composite.
    if composite < cfg.tier_primary_min_composite:
        return (
            TIER_SECONDARY,
            f"综合分偏低({composite:.0f}<{cfg.tier_primary_min_composite:.0f})",
        )
    if resonance > 0 and resonance < cfg.tier_primary_min_resonance:
        return (
            TIER_SECONDARY,
            f"共振维度偏少({resonance}<{cfg.tier_primary_min_resonance})",
        )

    return TIER_PRIMARY, ""


# [TF-QUALITY-005] candidate_pool_strength_tiers
def _apply_strength_tiers(
    main_entries: list[dict],
    cfg: StrategyConfig,
) -> tuple[list[dict], list[dict], dict]:
    """Split main_entries into primary/secondary with readable reasons.

    - Stamps ``strength_tier`` / ``tier_reason`` on each entry (in-place on a
      copy via :func:`dict` so callers retain the original objects).
    - Secondary cap: after the natural classification, only the first
      ``tier_secondary_max_count`` secondaries stay in ``secondary_candidates``;
      overflow is demoted to ``observation_candidates`` by the caller.
    """
    primary: list[dict] = []
    secondary: list[dict] = []
    downgrade_reasons: dict[str, list[str]] = {
        "weak_volume_capital": [],
        "rebound_trend_unrepaired": [],
        "data_insufficient": [],
        "low_resonance": [],
        "low_composite": [],
        "weak_topic": [],
        "single_evidence": [],
    }

    for entry in main_entries:
        tier, reason = _classify_strength_tier(entry, cfg)
        entry_copy = dict(entry)
        entry_copy["strength_tier"] = tier
        entry_copy["tier_reason"] = reason
        if tier == TIER_PRIMARY:
            primary.append(entry_copy)
        else:
            secondary.append(entry_copy)
            if "弱缩量" in reason:
                downgrade_reasons["weak_volume_capital"].append(entry.get("symbol", ""))
            elif "反弹" in reason:
                downgrade_reasons["rebound_trend_unrepaired"].append(entry.get("symbol", ""))
            elif "数据不足" in reason:
                downgrade_reasons["data_insufficient"].append(entry.get("symbol", ""))
            elif "共振维度" in reason:
                downgrade_reasons["low_resonance"].append(entry.get("symbol", ""))
            elif "综合分" in reason:
                downgrade_reasons["low_composite"].append(entry.get("symbol", ""))
            elif "弱主题" in reason:
                downgrade_reasons["weak_topic"].append(entry.get("symbol", ""))
            elif "证据单一" in reason:
                downgrade_reasons["single_evidence"].append(entry.get("symbol", ""))

    summary = {
        "primary_count": len(primary),
        "secondary_count": len(secondary),
        "primary_min_composite": cfg.tier_primary_min_composite,
        "primary_min_resonance": cfg.tier_primary_min_resonance,
        "secondary_max": cfg.tier_secondary_max_count,
        "downgrade_reasons": downgrade_reasons,
        "headline": (
            f"主候选分层：主候选{len(primary)}只，观察候选(次优)"
            f"{len(secondary)}只"
        ),
    }
    return primary, secondary, summary


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

    # [H-014] mandate_concentration_gate — precompute per-topic strength so
    # the loop can apply per-topic admission caps to 昊天 candidates. Topics
    # without metrics (non-haotian / unresolved topic) fall through.
    topic_metrics = compute_topic_metrics_map(entries, cfg) if cfg.concentration_enabled else {}
    topic_admission_count: dict[str, int] = {t: 0 for t in topic_metrics}
    admitted_by_topic: dict[str, list[str]] = {t: [] for t in topic_metrics}

    main: list[dict] = []
    observation: list[dict] = []
    filtered: list[dict] = []

    haotian_count = 0
    tech_count = 0

    # [TF-QUALITY-004] live_pool_calibration — calibration audit trail
    cal_weak_vcp: list[str] = []
    cal_data_insufficient: list[str] = []
    cal_haotian_evidence: list[str] = []
    # [H-014] mandate_concentration_gate — concentration audit trail
    conc_weak_topic: list[str] = []
    conc_topic_cap: list[str] = []

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

        # [H-014] mandate_concentration_gate — per-topic admission cap for
        # 昊天 candidates. Runs before the global haotian_max so the pool
        # reflects "strong theme Top 1-3" instead of a flat pile. Candidates
        # whose topic cannot be resolved skip this gate (safe fallback).
        if is_haotian and topic_metrics:
            topic = resolve_topic_for_entry(entry)
            metrics = topic_metrics.get(topic) if topic else None
            if metrics is not None:
                if metrics.admission_cap <= 0:
                    entry_copy = dict(entry)
                    entry_copy["precision_dimensions"] = precision.dimensions
                    entry_copy["precision_resonance_count"] = precision.resonance_count
                    entry_copy["pool_filter_reason"] = downgrade_reason_for_weak(metrics)
                    entry_copy["concentration_topic"] = topic
                    entry_copy["concentration_strength"] = metrics.strength
                    entry_copy["pool_status"] = "observation"
                    observation.append(entry_copy)
                    conc_weak_topic.append(entry.get("symbol", ""))
                    continue
                rank_in_topic = topic_admission_count.get(topic, 0) + 1
                if rank_in_topic > metrics.admission_cap:
                    entry_copy = dict(entry)
                    entry_copy["precision_dimensions"] = precision.dimensions
                    entry_copy["precision_resonance_count"] = precision.resonance_count
                    entry_copy["pool_filter_reason"] = downgrade_reason_for_cap(metrics, rank_in_topic)
                    entry_copy["concentration_topic"] = topic
                    entry_copy["concentration_strength"] = metrics.strength
                    entry_copy["pool_status"] = "observation"
                    observation.append(entry_copy)
                    conc_topic_cap.append(entry.get("symbol", ""))
                    continue

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
        # [H-014] mandate_concentration_gate — stamp topic/strength on main
        if is_haotian and topic_metrics:
            topic = resolve_topic_for_entry(entry)
            metrics = topic_metrics.get(topic) if topic else None
            if metrics is not None:
                topic_admission_count[topic] = topic_admission_count.get(topic, 0) + 1
                admitted_by_topic.setdefault(topic, []).append(entry.get("symbol", ""))
                entry_copy["concentration_topic"] = topic
                entry_copy["concentration_strength"] = metrics.strength
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

    # [H-014] mandate_concentration_gate — concentration summary
    concentration_summary = build_concentration_summary(
        topic_metrics, admitted_by_topic, cfg
    )
    concentration_summary["weak_topic_downgraded"] = conc_weak_topic
    concentration_summary["per_topic_cap_downgraded"] = conc_topic_cap

    # [TF-QUALITY-005] candidate_pool_strength_tiers — split main into
    # primary/secondary. Secondary overflow (beyond tier_secondary_max_count)
    # is demoted to observation so the secondary list itself stays tight and
    # the user sees a clear "primary few + secondary few + observation rest"
    # convergence. main_candidates keeps the full main set (primary + admitted
    # secondary) so existing consumers do not lose scoring fields.
    primary_candidates, secondary_candidates, strength_tier_summary = _apply_strength_tiers(
        main, cfg
    )
    main_before_tier = len(main)
    secondary_overflow: list[str] = []
    if len(secondary_candidates) > cfg.tier_secondary_max_count:
        overflow = secondary_candidates[cfg.tier_secondary_max_count:]
        for ov in overflow:
            ov["pool_status"] = "observation"
            ov["pool_filter_reason"] = (
                f"[TF-QUALITY-005]次优候选已满"
                f"({cfg.tier_secondary_max_count}/{cfg.tier_secondary_max_count})，"
                f"原因:{ov.get('tier_reason', '')}"
            )
            observation.append(ov)
            secondary_overflow.append(ov.get("symbol", ""))
        secondary_candidates = secondary_candidates[: cfg.tier_secondary_max_count]

    # Rebuild main so it reflects the post-cap primary+secondary set and each
    # entry carries strength_tier / tier_reason metadata. Entries demoted to
    # observation are removed from main.
    main = list(primary_candidates) + list(secondary_candidates)
    strength_tier_summary["main_before_tier"] = main_before_tier
    strength_tier_summary["main_after_tier"] = len(main)
    strength_tier_summary["observation_after_tier"] = len(observation)
    strength_tier_summary["secondary_overflow_to_observation"] = secondary_overflow

    # Refresh pool_counts with tier split so API / frontend can render it.
    # ``main`` is updated post-tier so pool_counts["main"] == len(main_candidates).
    pool_counts["main"] = len(main)
    pool_counts["primary"] = len(primary_candidates)
    pool_counts["secondary"] = len(secondary_candidates)
    pool_counts["secondary_overflow"] = len(secondary_overflow)

    return PoolGateResult(
        main_candidates=main,
        observation_candidates=observation,
        filtered_candidates=filtered,
        pool_counts=pool_counts,
        gate_summary=gate_summary,
        calibration_summary=calibration_summary,
        concentration_summary=concentration_summary,
        primary_candidates=primary_candidates,
        secondary_candidates=secondary_candidates,
        strength_tier_summary=strength_tier_summary,
    )
