# [UI-001] tradeflow_api
# [PERF-001] runtime_tier_contract
"""Pydantic response models for TradeFlow read-only API endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# [PERF-001] runtime_tier_contract
class RuntimeTierMeta(BaseModel):
    runtime_tier: str = "FAST_RADAR"
    expected_latency: str = "5-30s"
    llm_allowed: bool = False
    requires_confirmation: bool = False
    cost_risk: str = "none"
    tier_label: str = "快速筛选"
    tier_description: str = ""


# [PERF-002] lightweight_ta_profiles
class RuntimeProfileMeta(BaseModel):
    runtime_profile: str = ""
    profile_label: str = ""
    tier: str = ""
    expected_latency: str = ""
    enabled_analysts: List[str] = Field(default_factory=list)
    enabled_risk_modules: List[str] = Field(default_factory=list)
    enabled_managers: List[str] = Field(default_factory=list)
    description: str = ""


class TradeFlowCandidateItem(BaseModel):
    symbol: str
    name: str = ""
    tier: str = ""
    composite_score: float = 0.0
    score: float = 0.0
    strategy_tags: List[str] = Field(default_factory=list)
    primary_strategy: str = ""
    trigger_price: Optional[float] = None
    support_price: Optional[float] = None
    invalid_price: Optional[float] = None
    need_deep_ta: bool = False
    action: str = "OBSERVE"
    reason: str = ""
    observe_state: str = "WAITING"
    observe_trigger_count: int = 0
    observe_first_trigger_time: str = ""
    tradeflow_data_completeness: float = 0.0
    missing_data_fields: List[str] = Field(default_factory=list)
    data_completeness: float = 0.0
    missing_evidence: List[str] = Field(default_factory=list)
    game_balance: str = ""
    bull_case: str = ""
    bear_case: str = ""
    policy_case: str = ""
    fund_flow_case: str = ""
    why_deep_ta: str = ""
    why_not_deep_ta: str = ""
    risk_flags: List[str] = Field(default_factory=list)
    policy_tags: List[str] = Field(default_factory=list)
    version_score: float = 0.0
    narrative_score: float = 0.0
    fund_flow_anomaly_score: float = 0.0
    fund_flow_anomaly_tags: List[str] = Field(default_factory=list)
    fund_flow_unit_verified: bool = False
    evidence_gate_applied: bool = False
    deep_ta_status: str = ""
    deep_ta_dispatch_reason: str = ""
    deep_ta_model: str = ""
    source: str = "manual"
    universe_sources: List[str] = Field(default_factory=list)
    resonance_count: int = 0
    ta_budget_priority: int = 0
    tier_reason: str = ""
    missing_evidence_for_upgrade: List[str] = Field(default_factory=list)
    candidate_type: str = ""  # [H-005] mandate_radar_ui
    mandate_score: float = 0.0  # [H-005] mandate_radar_ui
    ambush_score: float = 0.0  # [H-005] mandate_radar_ui
    mandate_topic: str = ""  # [H-005] mandate_radar_ui
    company_role: str = ""  # [H-005] mandate_radar_ui
    beneficiary_path: List[str] = Field(default_factory=list)  # [H-005] mandate_radar_ui
    candidate_type_reason: str = ""  # [H-005] mandate_radar_ui
    deep_ta_route: str = ""  # [H-005] mandate_radar_ui
    research_queue: str = ""  # [H-007] mandate_ta_queue_router
    research_intent: str = ""  # [H-007] mandate_ta_queue_router
    research_route_reason: str = ""  # [H-007] mandate_ta_queue_router
    watchlist_note: str = ""  # [H-008] mandate_watchlist_note
    watchlist_note_suggested: str = ""  # [H-008] mandate_watchlist_note
    watchlist_topic: str = ""  # [H-008] mandate_watchlist_note
    watchlist_benefit_score: float = 0.0  # [H-008] mandate_watchlist_note
    watchlist_consensus_score: float = 0.0  # [H-008] mandate_watchlist_note
    watchlist_evidence_gap: List[str] = Field(default_factory=list)  # [H-008] mandate_watchlist_note
    created_at: str = ""
    updated_at: str = ""


class TradeFlowCandidateDetail(TradeFlowCandidateItem):
    evidence: Dict[str, Any] = Field(default_factory=dict)
    policy_evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    narrative_evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    narrative_reasons: List[str] = Field(default_factory=list)
    risk_evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    risk_reasons: List[str] = Field(default_factory=list)
    game_balance_refs: List[Dict[str, Any]] = Field(default_factory=list)
    fund_flow_anomaly_refs: List[Dict[str, Any]] = Field(default_factory=list)
    fund_flow_individual_summary: str = ""
    fund_flow_board_summary: str = ""
    signal_category_hits: List[str] = Field(default_factory=list)
    positive_category_count: int = 0
    what_to_upgrade: List[str] = Field(default_factory=list)
    deep_ta_report_path: str = ""
    deep_ta_dispatch_time: str = ""
    deep_ta_position_context: str = ""
    ambush_reasons: List[str] = Field(default_factory=list)  # [H-005] mandate_radar_ui
    ambush_evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)  # [H-005] mandate_radar_ui
    mandate_evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)  # [H-005] mandate_radar_ui


class TradeFlowSummary(BaseModel):
    total_candidates: int = 0
    tier_a_count: int = 0
    tier_b_count: int = 0
    tier_c_count: int = 0
    need_deep_ta_count: int = 0
    avg_completeness: float = 0.0


class TradeFlowDailyPlanResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    mode: str = "pre_market"
    summary: str = ""
    candidates: List[TradeFlowCandidateItem] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    summary_agg: TradeFlowSummary = Field(default_factory=TradeFlowSummary)
    created_at: str = ""
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)  # [PERF-001]


class TradeFlowCandidatesResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    candidates: List[TradeFlowCandidateItem] = Field(default_factory=list)
    summary_agg: TradeFlowSummary = Field(default_factory=TradeFlowSummary)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)  # [PERF-001]


class TradeFlowCandidateDetailResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    candidate: Optional[TradeFlowCandidateDetail] = None
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)  # [PERF-001]


class TradeFlowObserveItem(BaseModel):
    symbol: str
    name: str = ""
    observe_state: str = "WAITING"
    trigger_price: Optional[float] = None
    invalid_price: Optional[float] = None
    observe_trigger_count: int = 0
    observe_first_trigger_time: str = ""
    tier: str = ""
    composite_score: float = 0.0
    current_price: Optional[float] = None
    trigger_reason: str = ""
    strategy_tags: List[str] = Field(default_factory=list)


class TradeFlowObserveResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    observe_items: List[TradeFlowObserveItem] = Field(default_factory=list)
    triggered_count: int = 0
    invalidated_count: int = 0
    waiting_count: int = 0
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)  # [PERF-001]


class TradeFlowTAQueueItem(BaseModel):
    symbol: str
    name: str = ""
    tier: str = ""
    ta_budget_priority: int = 0
    need_deep_ta: bool = False
    deep_ta_status: str = ""
    deep_ta_dispatch_reason: str = ""
    deep_ta_model: str = ""
    why_deep_ta: str = ""
    why_not_deep_ta: str = ""
    composite_score: float = 0.0
    tradeflow_data_completeness: float = 0.0
    evidence_gate_applied: bool = False


class TradeFlowTAQueueResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    queue: List[TradeFlowTAQueueItem] = Field(default_factory=list)
    total_in_queue: int = 0
    dispatched_count: int = 0
    blocked_count: int = 0
    pending_count: int = 0
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)  # [PERF-001]


class TradeFlowReviewItem(BaseModel):
    symbol: str
    name: str = ""
    plan_action: str = "OBSERVE"
    keep_observing: bool = True
    reason: str = ""
    tier: str = ""
    composite_score: float = 0.0
    strategy_tags: List[str] = Field(default_factory=list)
    trigger_price: Optional[float] = None
    invalid_price: Optional[float] = None
    observe_state: str = "WAITING"


class TradeFlowReviewResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    reviewed_at: str = ""
    results: List[TradeFlowReviewItem] = Field(default_factory=list)
    summary_agg: TradeFlowSummary = Field(default_factory=TradeFlowSummary)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)  # [PERF-001]


class DataHealthSource(BaseModel):
    name: str
    available: bool = False
    status: str = "NOT_QUERIED"
    fallback_vendor: str = ""
    last_updated: Optional[str] = None
    record_count: int = 0
    error: str = ""


class TradeFlowDataHealthResponse(BaseModel):
    status: str = "ok"
    tradeflow_db_available: bool = False
    tradeflow_db_path: str = ""
    sources: List[DataHealthSource] = Field(default_factory=list)
    latest_plan_date: Optional[str] = None
    latest_candidates_date: Optional[str] = None
    total_candidates_today: int = 0
    total_signals_today: int = 0
    latest_observe_check_time: Optional[str] = None
    latest_signal_time: Optional[str] = None
    evidence_contract_available: bool = False  # [DATA-004] raw_evidence_contract
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)  # [PERF-001]


# [UI-007] tradeflow_filtered_trace
class TradeFlowFilteredItem(BaseModel):
    symbol: str
    name: str = ""
    source: str = ""
    reason: str = ""
    run_id: str = ""
    created_at: str = ""


class TradeFlowFilteredResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    filtered: List[TradeFlowFilteredItem] = Field(default_factory=list)
    filter_breakdown: Dict[str, int] = Field(default_factory=dict)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)  # [PERF-001]
