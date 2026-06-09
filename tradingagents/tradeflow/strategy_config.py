# [M-004] strategy_config
"""Centralized strategy weights and thresholds for TradeFlow.

All strategy scoring, filtering, gating, and tier classification thresholds
are defined here.  Modules should accept an optional ``StrategyConfig`` and
fall back to ``DEFAULT_STRATEGY_CONFIG`` when none is provided.

Design constraints:
- Default values are conservative — no strong buy/sell words.
- LLM must NOT dynamically change weights.
- Config version is stamped into daily plan metadata for audit.
- Missing config falls back to ``DEFAULT_STRATEGY_CONFIG``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


STRATEGY_CONFIG_VERSION = "v1.0"


@dataclass
class StrategyConfig:
    """Centralized weights and thresholds for TradeFlow strategies.

    Groups:
    - Liquidity: amount threshold for filtering low-liquidity stocks.
    - VCP: Volatility Contraction Pattern scoring thresholds.
    - Pullback: Pullback-to-Support scoring thresholds.
    - Event: Event Catalyst scoring thresholds.
    - Policy: Policy Version signal thresholds.
    - Narrative: Narrative Quality scoring thresholds.
    - Risk: Underwater Risk Flag thresholds.
    - FundFlow: Fund Flow Anomaly detection thresholds.
    - GameBalance: Game Balance assessment thresholds.
    - Gate: Selection Priority Gate thresholds.
    - Tier: Tier Budget classification thresholds.
    - Evidence: Evidence Gate completeness thresholds.
    """

    config_version: str = STRATEGY_CONFIG_VERSION

    # ── Liquidity ──
    amount_threshold: float = 30_000_000.0

    # ── VCP (Volatility Contraction Pattern) ──
    vcp_min_data_len: int = 40
    vcp_range_ratio_max: float = 0.8
    vcp_vol_ratio_max: float = 0.9
    vcp_price_position_min: float = 0.5
    vcp_ma20_break_pct: float = 0.97
    vcp_score_max: float = 80.0
    vcp_need_deep_ta_score: float = 55.0
    vcp_need_deep_ta_position: float = 0.8

    # ── Pullback Support ──
    pullback_min_data_len: int = 60
    pullback_min_pct: float = -0.03
    pullback_max_pct: float = -0.20
    pullback_vol_ratio_max: float = 1.0
    pullback_ma_distance_max: float = 0.03
    pullback_ma_above_max: float = 1.02
    pullback_prior_low_distance_max: float = 0.05
    pullback_score_max: float = 80.0
    pullback_need_deep_ta_score: float = 60.0
    pullback_need_deep_ta_support_dist: float = 0.01

    # ── Event Catalyst ──
    event_score_max: float = 80.0
    event_need_deep_ta_score: float = 60.0

    # ── Policy Version Signal ──
    policy_max_bonus: float = 30.0
    policy_industry_weight_factor: float = 0.3
    policy_need_deep_ta_score: float = 15.0

    # ── Narrative Quality ──
    narrative_max_score: float = 40.0
    narrative_need_deep_ta_score: float = 20.0

    # ── Underwater Risk Flags ──
    risk_max_penalty: float = 30.0
    risk_lockup_score_factor: float = 0.8
    risk_many_flags_threshold: int = 3
    risk_heavy_penalty_threshold: float = -15.0
    risk_high_severity_flags: tuple = ("INQUIRY_RISK", "FINANCIAL_QUALITY_RISK")

    # ── Fund Flow Anomaly ──
    fund_flow_max_bonus: float = 25.0
    fund_flow_anomaly_threshold_wan: float = 50000.0
    fund_flow_consecutive_min_days: int = 3
    fund_flow_high_proportion_threshold: float = 5.0
    fund_flow_need_deep_ta_score: float = 10.0
    fund_flow_lookback_default: int = 5

    # ── Game Balance ──
    game_balance_risk_penalty_threshold: float = -10.0
    game_balance_many_risks_threshold: int = 3
    game_balance_heavy_penalty_threshold: float = -15.0

    # ── Selection Priority Gate ──
    gate_min_positive_categories: int = 2
    gate_max_composite_score: float = 200.0
    gate_completeness_bonus_factor: float = 5.0
    gate_completeness_min: float = 0.5
    gate_rank_a_min_categories: int = 3
    gate_rank_a_min_composite: float = 60.0
    gate_rank_a_min_completeness: float = 0.7
    gate_rank_b_min_categories: int = 2
    gate_rank_b_min_composite: float = 30.0

    # ── Tier Budget ──
    tier_a_hard_cap: int = 5
    tier_a_budget: int = 100
    tier_b_budget: int = 30
    tier_c_budget: int = 0
    tier_a_min_completeness: float = 0.6
    tier_b_min_completeness: float = 0.3
    tier_a_min_composite_for_2cat: float = 50.0

    # ── Evidence Gate ──
    evidence_total_dimensions: int = 10
    evidence_completeness_for_a_tier: float = 0.6
    evidence_completeness_for_deep_ta: float = 0.5

    # ── Intraday Observe [M-005] ──
    observe_max_daily_triggers: int = 3
    observe_trigger_breach_pct: float = 0.0
    observe_invalidate_breach_pct: float = 0.0

    # ── Intraday Observe Scheduler [T-004] ──
    observe_interval_minutes: int = 30
    observe_volume_anomaly_ratio: float = 2.0
    observe_market_open_hour: int = 9
    observe_market_open_minute: int = 30
    observe_market_close_hour: int = 15
    observe_market_close_minute: int = 0

    # ── Candidate Pool Gate [TF-QUALITY-001] ──
    pool_main_max: int = 5
    pool_tech_max: int = 3
    pool_haotian_max: int = 3
    pool_min_positive_categories_for_main: int = 2
    pool_min_composite_for_single_strong: float = 60.0
    pool_min_completeness_for_single_strong: float = 0.6

    # ── Deep TA Dispatch Gate [M-006] ──
    deep_ta_daily_limit: int = 3
    deep_ta_default_model: str = ""
    deep_ta_blocked_models: tuple = ("deepseek",)
    deep_ta_max_retries: int = 1
    deep_ta_min_composite_score: float = 40.0
    deep_ta_min_completeness: float = 0.5
    deep_ta_require_observe_triggered: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["risk_high_severity_flags"] = list(self.risk_high_severity_flags)
        return d


DEFAULT_STRATEGY_CONFIG = StrategyConfig()
