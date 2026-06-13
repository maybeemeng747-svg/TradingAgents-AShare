# [TF-QUALITY-003] candidate_precision_gate
"""Candidate Precision Gate — compresses the candidate pool from "looks like many"
to "truly worth watching" by requiring multi-dimension resonance.

Responsibilities:
1. Compute per-dimension hits for TECH_TRADE and POLICY candidates based on
   the TF-QUALITY-002 split scores (technical_score / policy_score /
   fund_flow_score / event_score / data_quality_score / risk_penalty_score)
   with graceful fallback to raw fields when split scores are absent.
2. TECH_TRADE main candidates must satisfy >= 2 of:
   {形态, 量能, 资金, 触发/失效价, 数据质量}.
3. POLICY_AMBUSH / POLICY_CONFIRM main candidates must satisfy >= 2 of:
   {政策主题, 受益路径, 反证不过热, 证据覆盖}.
4. Haotian (POLICY) candidates that fail precision are NEVER filtered —
   they go to observation only (short-term non-breakout is not a filter reason).

Design constraints:
- No LLM calls.
- No prompt changes.
- No strong buy/sell words.
- Deterministic: same inputs always produce same output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG


_HAOTIAN_TYPES = {"POLICY_AMBUSH", "POLICY_CONFIRM"}
_TECH_TYPES = {"TECH_TRADE"}


@dataclass
class PrecisionResult:
    """Result of precision gate evaluation for one candidate."""

    candidate_type: str = ""
    dimensions: dict[str, bool] = field(default_factory=dict)
    resonance_count: int = 0
    min_required: int = 2
    qualified: bool = False
    reason: str = ""


def _safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _tech_dimension_hits(entry: dict, cfg: StrategyConfig) -> dict[str, bool]:
    """Compute TECH_TRADE dimension hits.

    Dimensions: 形态(pattern), 量能(volume), 资金(capital),
    触发/失效价(trigger/invalidation), 数据质量(data quality).
    """
    tags = set(entry.get("strategies", []) or entry.get("strategy_tags", []))
    tech_score = _safe_float(entry.get("technical_score"))
    fund_flow_score = _safe_float(entry.get("fund_flow_score"))
    raw_ff_score = _safe_float(entry.get("fund_flow_anomaly_score"))
    ff_verified = bool(entry.get("fund_flow_unit_verified", False))
    ff_tags = entry.get("fund_flow_anomaly_tags", [])
    if not isinstance(ff_tags, list):
        ff_tags = []
    positive_cats = entry.get("positive_category_count", 0) or 0
    resonance = entry.get("resonance_count", 0) or 0
    data_comp = (
        _safe_float(entry.get("tradeflow_data_completeness"))
        or _safe_float(entry.get("data_completeness"))
    )
    dq_score = _safe_float(entry.get("data_quality_score"))
    trigger = entry.get("trigger_price")
    invalid = entry.get("invalid_price")

    has_pattern_tag = "VCP" in tags or "PULLBACK_SUPPORT" in tags

    dims: dict[str, bool] = {}

    # 形态 — split score preferred, fallback to strategy tag
    dims["形态"] = (tech_score > 0) or has_pattern_tag

    # 量能 — broad market participation: multiple signal categories or resonance
    positive_ff_tags = bool(set(ff_tags) - {"NET_OUTFLOW_DOMINANT"})
    dims["量能"] = (
        positive_cats >= 2
        or resonance >= 2
        or positive_ff_tags
    )

    # 资金 — institutional fund flow with verified units
    dims["资金"] = (fund_flow_score > 0) or (raw_ff_score > 0 and ff_verified)

    # 触发/失效价 — both trigger and invalidation price must be set
    dims["触发/失效价"] = trigger is not None and invalid is not None

    # 数据质量 — completeness above threshold
    dq_from_score = dq_score >= cfg.precision_data_quality_threshold * 100
    dq_from_comp = data_comp >= cfg.precision_data_quality_threshold
    dims["数据质量"] = dq_from_score or dq_from_comp

    return dims


def _policy_dimension_hits(entry: dict, cfg: StrategyConfig) -> dict[str, bool]:
    """Compute POLICY (昊天) dimension hits.

    Dimensions: 政策主题(policy theme), 受益路径(benefit path),
    反证不过热(counter-evidence not overheated), 证据覆盖(evidence coverage).
    """
    policy_score = _safe_float(entry.get("policy_score"))
    version_score = _safe_float(entry.get("version_score"))
    mandate_score = (
        _safe_float(entry.get("mandate_score_component"))
        or _safe_float(entry.get("mandate_score"))
    )
    beneficiary_score = _safe_float(entry.get("beneficiary_score_component"))
    policy_tags = entry.get("policy_tags", [])
    mandate_topic = entry.get("mandate_topic", "")
    beneficiary_path = entry.get("beneficiary_path", [])
    if not isinstance(policy_tags, list):
        policy_tags = []
    if not isinstance(beneficiary_path, list):
        beneficiary_path = []
    counter_ev = entry.get("counter_evidence", [])
    if not isinstance(counter_ev, list):
        counter_ev = []
    overheat_penalty = _safe_float(entry.get("overheat_penalty"))
    overheat_flags = entry.get("overheat_flags", [])
    if not isinstance(overheat_flags, list):
        overheat_flags = []
    data_comp = (
        _safe_float(entry.get("tradeflow_data_completeness"))
        or _safe_float(entry.get("data_completeness"))
    )
    dq_score = _safe_float(entry.get("data_quality_score"))

    dims: dict[str, bool] = {}

    # 政策主题 — policy signal present
    dims["政策主题"] = (
        policy_score > 0
        or version_score > 0
        or mandate_score > 0
        or len(policy_tags) > 0
        or bool(mandate_topic)
    )

    # 受益路径 — beneficiary mapping established
    dims["受益路径"] = (
        beneficiary_score > 0
        or len(beneficiary_path) > 0
    )

    # 反证不过热 — no high-severity counter-evidence and overheat within bounds
    has_high_severity = any(
        isinstance(ce, dict) and ce.get("severity") == "high" for ce in counter_ev
    )
    overheat_ok = overheat_penalty <= cfg.precision_overheat_penalty_max
    no_overheat_flags = len(overheat_flags) == 0
    dims["反证不过热"] = (not has_high_severity) and overheat_ok and no_overheat_flags

    # 证据覆盖 — data completeness above threshold
    dq_from_score = dq_score >= cfg.precision_data_quality_threshold * 100
    dq_from_comp = data_comp >= cfg.precision_data_quality_threshold
    dims["证据覆盖"] = dq_from_score or dq_from_comp

    return dims


def compute_precision(
    entry: dict,
    cfg: Optional[StrategyConfig] = None,
) -> PrecisionResult:
    """Evaluate precision gate for a single candidate.

    Returns a PrecisionResult with per-dimension hits, resonance count,
    qualification verdict, and human-readable reason.

    For candidate types outside TECH_TRADE / POLICY (e.g. EVENT_WATCH,
    UNCLASSIFIED), the result is always ``qualified=True`` so the caller
    can fall through to the legacy pool gate logic.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    candidate_type = entry.get("candidate_type", "")

    if candidate_type in _TECH_TYPES:
        dims = _tech_dimension_hits(entry, cfg)
        min_required = cfg.precision_min_tech_dimensions
        type_label = "技术"
    elif candidate_type in _HAOTIAN_TYPES:
        dims = _policy_dimension_hits(entry, cfg)
        min_required = cfg.precision_min_policy_dimensions
        type_label = "昊天"
    else:
        return PrecisionResult(
            candidate_type=candidate_type,
            dimensions={},
            resonance_count=0,
            min_required=0,
            qualified=True,
            reason="",
        )

    resonance = sum(1 for v in dims.values() if v)
    hit_names = [k for k, v in dims.items() if v]
    miss_names = [k for k, v in dims.items() if not v]

    if resonance >= min_required:
        reason = ""
        qualified = True
    else:
        reason = (
            f"{type_label}共振不足({resonance}/{min_required})，"
            f"命中:{'/'.join(hit_names) if hit_names else '无'}，"
            f"缺失:{'/'.join(miss_names)}"
        )
        qualified = False

    return PrecisionResult(
        candidate_type=candidate_type,
        dimensions=dims,
        resonance_count=resonance,
        min_required=min_required,
        qualified=qualified,
        reason=reason,
    )


def precision_qualifies_for_main(
    entry: dict,
    cfg: Optional[StrategyConfig] = None,
) -> tuple[bool, str]:
    """Quick boolean check: does the candidate pass the precision gate for main?

    Returns (qualified, reason).
    """
    result = compute_precision(entry, cfg)
    return result.qualified, result.reason


def enrich_with_precision(
    entries: list[dict],
    cfg: Optional[StrategyConfig] = None,
) -> list[dict]:
    """Stamp each entry with precision dimension metadata (in-place safe).

    Adds ``precision_dimensions``, ``precision_resonance_count``, and
    ``precision_min_required`` keys to each entry dict copy.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    for entry in entries:
        result = compute_precision(entry, cfg)
        entry["precision_dimensions"] = result.dimensions
        entry["precision_resonance_count"] = result.resonance_count
        entry["precision_min_required"] = result.min_required
        if not result.qualified and not entry.get("precision_fail_reason"):
            entry["precision_fail_reason"] = result.reason
    return entries
