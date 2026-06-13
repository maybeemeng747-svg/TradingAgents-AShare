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
    action_tier: str = "scan"  # [TF-UX-004] action_tier_scorer
    trade_priority_score: float = 0.0  # [TF-UX-004] action_tier_scorer
    action_tier_reason: str = ""  # [TF-UX-004] action_tier_scorer
    counter_evidence: List[dict] = Field(default_factory=list)  # [H-009] mandate_counter_evidence_calibration
    overheat_flags: List[str] = Field(default_factory=list)  # [H-009]
    downgrade_reasons: List[str] = Field(default_factory=list)  # [H-009]
    what_would_change_mind: List[str] = Field(default_factory=list)  # [H-009]
    topic_lifecycle_state: str = ""  # [H-010] mandate_topic_lifecycle
    topic_lifecycle_reason: str = ""  # [H-010] mandate_topic_lifecycle
    topic_last_signal_date: str = ""  # [H-010] mandate_topic_lifecycle
    topic_signal_count: int = 0  # [H-010] mandate_topic_lifecycle
    contradiction_level: str = ""  # [H-011] candidate_contradiction_explainer
    contradiction_items: List[dict] = Field(default_factory=list)  # [H-011]
    blocking_evidence_gaps: List[str] = Field(default_factory=list)  # [H-011]
    next_verification_steps: List[str] = Field(default_factory=list)  # [H-011]
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
    main_candidates: List[TradeFlowCandidateItem] = Field(default_factory=list)
    observation_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    filtered_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    pool_counts: Dict[str, Any] = Field(default_factory=dict)
    pool_gate_summary: str = ""
    summary_agg: TradeFlowSummary = Field(default_factory=TradeFlowSummary)
    main_summary_agg: TradeFlowSummary = Field(default_factory=TradeFlowSummary)
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
    observe_auto_run: bool = False  # [TF-OBS-002] observe_auto_run
    last_observed_at: str = ""      # [TF-OBS-002]
    observe_reason: str = ""        # [TF-OBS-002]
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
    plan_date: str = ""  # [TF-REVIEW-002] review_date_mapping
    effective_trade_date: str = ""  # [TF-REVIEW-002] review_date_mapping
    data_status: str = "OK"  # [TF-REVIEW-002] review_date_mapping
    data_status_message: str = ""  # [TF-REVIEW-002] review_date_mapping
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
    evidence_coverage_audit_available: bool = False  # [DATA-007] evidence_coverage_audit
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


# [TF-UX-001] tiered candidates
class TradeFlowTieredCandidatesResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    actionable: List[TradeFlowCandidateItem] = Field(default_factory=list)
    watch: List[TradeFlowCandidateItem] = Field(default_factory=list)
    scan: List[TradeFlowCandidateItem] = Field(default_factory=list)
    main_candidates: List[TradeFlowCandidateItem] = Field(default_factory=list)
    observation_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    filtered_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    pool_counts: Dict[str, Any] = Field(default_factory=dict)
    pool_gate_summary: str = ""
    actionable_count: int = 0
    watch_count: int = 0
    scan_count: int = 0
    summary_agg: TradeFlowSummary = Field(default_factory=TradeFlowSummary)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TF-UX-003] post_market_review
class TradeFlowReviewGenerateResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    message: str = ""
    data_status: str = ""  # [TF-REVIEW-002] review_date_mapping
    data_status_message: str = ""  # [TF-REVIEW-002] review_date_mapping
    review: Optional[Dict[str, Any]] = None


# [DATA-007] evidence_coverage_audit
class EvidenceCredibilityItem(BaseModel):
    symbol: str = ""
    candidate_type: str = ""
    evidence_coverage: float = 0.0
    evidence_quality_level: str = "UNKNOWN"
    tier_allowed: str = ""
    tier_restricted: bool = False
    need_deep_ta_allowed: bool = True
    credibility_level: str = "UNKNOWN"
    credibility_reasons: List[str] = Field(default_factory=list)


class TradeFlowEvidenceAuditResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    total_candidates: int = 0
    audits_count: int = 0
    credibilities_count: int = 0
    credibilities: List[EvidenceCredibilityItem] = Field(default_factory=list)
    summary_markdown: str = ""
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [UI-009] candidate_ta_plan_draft
# [TF-UI-011] candidate_research_entry — profile metadata added
class TradeFlowResearchPlanResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    symbol: str = ""
    candidate_type: str = ""
    research_queue: str = ""
    research_intent: str = ""
    horizon: str = ""
    analysis_intent: str = ""
    position_context: str = ""
    runtime_profile: str = ""
    profile_label: str = ""  # [TF-UI-011]
    expected_latency: str = ""  # [TF-UI-011]
    llm_allowed: bool = True  # [TF-UI-011]
    requires_confirmation: bool = False  # [TF-UI-011]
    cost_risk: str = ""  # [TF-UI-011]
    enabled_modules: List[str] = Field(default_factory=list)
    required_evidence: List[str] = Field(default_factory=list)
    existing_evidence_coverage: float = 0.0
    can_generate: bool = True
    block_reason: str = ""
    plan_markdown: str = ""
    route_reason: str = ""


# [TF-UI-011] candidate_research_entry
class CompanyOverviewResponse(BaseModel):
    status: str = "ok"
    symbol: str = ""
    name: str = ""
    industry: str = ""
    company_profile: str = ""
    profile_available: bool = False
    data_source: str = ""
    error: str = ""
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [UI-010] mandate_candidate_compare
class TradeFlowCompareResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    candidates: List[TradeFlowCandidateItem] = Field(default_factory=list)
    sort_by: str = "mandate_score"
    sort_order: str = "desc"
    total: int = 0
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TF-PAPER-001] paper_trading_ledger
class PaperTradeItem(BaseModel):
    id: int = 0
    symbol: str = ""
    name: str = ""
    trade_date: str = ""
    plan_date: str = ""
    candidate_type: str = ""
    trigger_price: Optional[float] = None
    invalid_price: Optional[float] = None
    planned_amount: float = 0.0
    status: str = "tracking"
    action_type: str = ""
    action_price: Optional[float] = None
    action_date: str = ""
    confirmed: bool = False
    note: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    observe_state: str = "WAITING"
    close_price: Optional[float] = None
    close_date: str = ""
    close_reason: str = ""
    created_at: str = ""
    updated_at: str = ""


# [TF-PAPER-001] paper_trading_ledger
class PaperLedgerSummary(BaseModel):
    total_trades: int = 0
    tracking_count: int = 0
    pending_count: int = 0
    open_count: int = 0
    closed_count: int = 0
    invested: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0


# [TF-PAPER-001] paper_trading_ledger
class PaperLedgerResponse(BaseModel):
    status: str = "ok"
    principal: float = 5000.0
    cash_balance: float = 5000.0
    config: Dict[str, Any] = Field(default_factory=dict)
    trades: List[PaperTradeItem] = Field(default_factory=list)
    summary: PaperLedgerSummary = Field(default_factory=PaperLedgerSummary)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TF-PAPER-001] paper_trading_ledger
class PaperActionRequest(BaseModel):
    trade_id: int
    action_type: str = Field(..., description="buy 或 sell")
    price: float
    note: str = ""


# [TF-PAPER-001] paper_trading_ledger
class PaperAddCandidateRequest(BaseModel):
    symbol: str
    name: str = ""
    trade_date: str
    trigger_price: Optional[float] = None
    invalid_price: Optional[float] = None
    planned_amount: float = 0.0
    candidate_type: str = ""
    plan_date: str = ""
    note: str = ""


# [TF-PAPER-001] paper_trading_ledger
class PaperActionResponse(BaseModel):
    status: str = "ok"
    message: str = ""
    trade_id: Optional[int] = None
    planned_amount: Optional[float] = None
    cash_balance: Optional[float] = None
    pnl: Optional[float] = None


# [TF-PAPER-001] paper_trading_ledger
class PaperReviewResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    review: Dict[str, Any] = Field(default_factory=dict)
    trades: List[PaperTradeItem] = Field(default_factory=list)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [H-012] mandate_topic_registry
class TopicRegistryItem(BaseModel):
    topic: str = ""
    topic_status: str = "UNKNOWN"
    topic_status_label: str = "未知"
    lifecycle_state: str = "UNKNOWN"
    policy_level: str = "UNKNOWN"
    policy_level_weight: int = 0
    last_signal_date: str = ""
    signal_count: int = 0
    evidence_links: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_summary: str = ""
    chain_segments: List[Dict[str, Any]] = Field(default_factory=list)
    is_left_side: bool = False
    is_observe_only: bool = False
    is_confirmed: bool = False
    matched_candidates: List[str] = Field(default_factory=list)


class TopicRegistryResponse(BaseModel):
    status: str = "ok"
    topics: List[TopicRegistryItem] = Field(default_factory=list)
    total_topics: int = 0
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [H-012] mandate_topic_registry
class TopicWatchlistSymbolItem(BaseModel):
    symbol: str = ""
    name: str = ""
    company_role: str = ""
    beneficiary_path: List[str] = Field(default_factory=list)
    mandate_score: float = 0.0
    tier: str = ""
    evidence_gaps: List[str] = Field(default_factory=list)
    note_suggestion: str = ""


class TopicWatchlistTopicItem(BaseModel):
    topic: str = ""
    topic_status: str = "UNKNOWN"
    topic_status_label: str = "未知"
    policy_level: str = "UNKNOWN"
    is_left_side: bool = False
    is_observe_only: bool = False
    symbols: List[TopicWatchlistSymbolItem] = Field(default_factory=list)
    chain_segments: List[Dict[str, Any]] = Field(default_factory=list)
    counter_evidence_gaps: List[str] = Field(default_factory=list)
    note_suggestion: str = ""


class TopicWatchlistResponse(BaseModel):
    status: str = "ok"
    topics: List[TopicWatchlistTopicItem] = Field(default_factory=list)
    total_topics: int = 0
    total_symbols: int = 0
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)
