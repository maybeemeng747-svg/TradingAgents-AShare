# [TF-QUALITY-002] score_separation
"""Score Separation — splits composite score into dimension sub-scores and computes
ranking_reasons / weakness_reasons for explainable sorting.

Responsibilities:
1. Derive technical_score, policy_score, fund_flow_score, event_score,
   risk_penalty_score, data_quality_score from existing candidate fields.
2. Produce ranking_reasons (why this candidate ranks high) and
   weakness_reasons (what holds it back).
3. Normalize scores across a batch so that differences are visible
   (avoid all candidates clustering at 45-55).

Design constraints:
- No LLM calls.
- No new data fetching — reuses fields already computed by candidate_engine.
- Deterministic: same inputs always produce same output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG


@dataclass
class ScoreSeparationResult:
    technical_score: float = 0.0
    policy_score: float = 0.0
    fund_flow_score: float = 0.0
    event_score: float = 0.0
    risk_penalty_score: float = 0.0
    data_quality_score: float = 0.0
    ranking_reasons: list[str] = field(default_factory=list)
    weakness_reasons: list[str] = field(default_factory=list)


def compute_split_scores(
    score: float = 0.0,
    strategy_tags: Optional[list[str]] = None,
    version_score: float = 0.0,
    policy_tags: Optional[list[str]] = None,
    narrative_score: float = 0.0,
    fund_flow_anomaly_score: float = 0.0,
    fund_flow_anomaly_tags: Optional[list[str]] = None,
    fund_flow_unit_verified: bool = False,
    risk_penalty: float = 0.0,
    risk_flags: Optional[list[str]] = None,
    data_completeness: float = 0.0,
    game_balance: str = "",
    resonance_count: int = 0,
    positive_category_count: int = 0,
    composite_score: float = 0.0,
    trigger_price: Optional[float] = None,
    cfg: Optional[StrategyConfig] = None,
) -> ScoreSeparationResult:
    """Compute dimension-level sub-scores from existing candidate fields.

    Sub-score derivation rules:
    - technical_score: VCP/PULLBACK_SUPPORT base score (the original signal score)
    - policy_score: version_score + mandate-related bonus
    - fund_flow_score: fund_flow_anomaly_score (only if unit verified)
    - event_score: narrative_score (event/narrative quality)
    - risk_penalty_score: absolute value of risk_penalty (always >= 0)
    - data_quality_score: 0-100 based on data_completeness ratio
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    tags = set(strategy_tags or [])
    ff_tags = set(fund_flow_anomaly_tags or [])
    p_tags = list(policy_tags or [])
    r_flags = list(risk_flags or [])

    has_tech = "VCP" in tags or "PULLBACK_SUPPORT" in tags
    has_policy = version_score > 0 or len(p_tags) > 0
    has_narrative = narrative_score > 0
    positive_ff = bool(ff_tags - {"NET_OUTFLOW_DOMINANT"}) and fund_flow_anomaly_score > 0

    technical_score = score if has_tech else 0.0
    policy_score = float(version_score)
    if policy_score > 0 and len(p_tags) >= 2:
        policy_score += 5.0

    fund_flow_score = fund_flow_anomaly_score if (positive_ff and fund_flow_unit_verified) else 0.0
    event_score = float(narrative_score)
    risk_penalty_score = abs(risk_penalty)
    data_quality_score = round(data_completeness * 100.0, 1)

    ranking_reasons: list[str] = []
    weakness_reasons: list[str] = []

    if has_tech and technical_score >= 30:
        tech_name = "VCP" if "VCP" in tags else "回踩支撑"
        ranking_reasons.append(f"{tech_name}形态({technical_score:.0f}分)")
    if has_policy:
        ranking_reasons.append(f"政策催化(+{policy_score:.0f}分)")
    if positive_ff and fund_flow_unit_verified:
        ranking_reasons.append(f"资金共振(+{fund_flow_score:.0f}分)")
    if has_narrative:
        ranking_reasons.append(f"叙事质量(+{event_score:.0f}分)")
    if resonance_count >= 2:
        ranking_reasons.append(f"{resonance_count}维共振")
    if game_balance == "favorable":
        ranking_reasons.append("博弈有利")
    if positive_category_count >= 3:
        ranking_reasons.append(f"{positive_category_count}类正向信号")
    if trigger_price is not None and trigger_price > 0:
        ranking_reasons.append("有明确触发价")

    if not has_tech:
        weakness_reasons.append("无技术形态信号")
    if not has_policy:
        weakness_reasons.append("无政策催化")
    if not positive_ff:
        weakness_reasons.append("无资金共振")
    if not has_narrative:
        weakness_reasons.append("无叙事质量")
    if risk_penalty_score > 0:
        weakness_reasons.append(f"风险扣分({risk_penalty_score:.0f})")
    if r_flags:
        weakness_reasons.append(f"风险标签({len(r_flags)})")
    if game_balance in ("fragile", "crowded"):
        weakness_reasons.append(f"博弈{game_balance}")
    if data_completeness < 0.5:
        weakness_reasons.append(f"数据完整度低({data_completeness:.0%})")
    if composite_score < 30:
        weakness_reasons.append("综合分偏低")
    if positive_category_count < 2:
        weakness_reasons.append(f"信号维度不足({positive_category_count}类)")

    ranking_reasons = ranking_reasons[:5]
    weakness_reasons = weakness_reasons[:5]

    return ScoreSeparationResult(
        technical_score=round(technical_score, 2),
        policy_score=round(policy_score, 2),
        fund_flow_score=round(fund_flow_score, 2),
        event_score=round(event_score, 2),
        risk_penalty_score=round(risk_penalty_score, 2),
        data_quality_score=round(data_quality_score, 1),
        ranking_reasons=ranking_reasons,
        weakness_reasons=weakness_reasons,
    )


def normalize_scores_batch(
    entries: list[dict],
    score_key: str = "composite_score",
    min_spread: float = 20.0,
    target_min: float = 0.0,
    target_max: float = 100.0,
) -> list[dict]:
    """Normalize a score field across a batch so scores spread apart.

    When all scores cluster in a narrow band (e.g. 45-55), applies min-max
    normalization to stretch them to [target_min, target_max] with a minimum
    spread of min_spread points.

    The original score is preserved in ``{score_key}_raw`` and the normalized
    value overwrites ``{score_key}`` only when normalization was applied.
    """
    if not entries:
        return entries

    scores = []
    for e in entries:
        s = e.get(score_key, 0.0) or 0.0
        scores.append(s)

    raw_min = min(scores)
    raw_max = max(scores)
    raw_range = raw_max - raw_min

    if raw_range < min_spread and raw_range > 0:
        for i, e in enumerate(entries):
            original = scores[i]
            e[f"{score_key}_raw"] = original
            normalized = target_min + (original - raw_min) / raw_range * (target_max - target_min)
            e[score_key] = round(normalized, 2)
    elif raw_range == 0 and len(entries) > 1:
        for i, e in enumerate(entries):
            e[f"{score_key}_raw"] = scores[i]
            e[score_key] = round((target_min + target_max) / 2.0, 2)

    return entries
