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
    technical_score: float = 0.0
    policy_score: float = 0.0
    fund_flow_score: float = 0.0
    event_score: float = 0.0
    ranking_reasons: List[str] = Field(default_factory=list)
    weakness_reasons: List[str] = Field(default_factory=list)
    risk_penalty_score: float = 0.0
    data_quality_score: float = 0.0
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
    research_attention_score: float = 0.0  # [KB-008] research_attention_integration
    knowledge_theme_count: int = 0  # [KB-008] research_attention_integration
    research_attention_summary: str = ""  # [KB-008] research_attention_integration
    research_attention_detail: Dict[str, Any] = Field(default_factory=dict)  # [KB-008]
    research_attention_effective_score: float = 0.0  # [KB-009] research_attention_decay
    research_attention_overheat_penalty: float = 0.0  # [KB-009] research_attention_decay
    local_knowledge_score: float = 0.0  # [KB-004] tradeflow_knowledge_score
    knowledge_hit_count: int = 0  # [KB-004] tradeflow_knowledge_score
    local_knowledge_summary: str = ""  # [KB-004] tradeflow_knowledge_score
    local_knowledge_detail: Dict[str, Any] = Field(default_factory=dict)  # [KB-004]
    needs_tree_work_research: bool = False  # [KB-004] tradeflow_knowledge_score
    knowledge_influence_explain: List[str] = Field(default_factory=list)  # [TF-KB-001] knowledge_score_calibration
    knowledge_influence_detail: Dict[str, Any] = Field(default_factory=dict)  # [TF-KB-001] knowledge_score_calibration
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
    concentration_summary: Dict[str, Any] = Field(default_factory=dict)  # [H-014] mandate_concentration_gate
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
    paper_status: str = ""  # [TF-OBS-003] observe_paper_sync
    # [TF-OBS-004] observe_refresh_alert_queue — structured trigger explanation
    trigger_distance_pct: Optional[float] = None  # signed % distance from trigger price
    near_trigger: bool = False  # within near-trigger band but not yet triggered
    trigger_explain: Dict[str, Any] = Field(default_factory=dict)  # why triggered / why not / how far off


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
    # [TF-OBS-004] observe_refresh_alert_queue — refresh metadata + grouped counts
    refresh_interval_seconds: int = 180
    is_market_hours: bool = False
    is_trading_day: bool = False
    near_trigger_count: int = 0
    pending_count: int = 0
    # [TF-OBS-005] observe_date_semantics — explicit plan/effective/observe dates
    plan_date: str = ""
    effective_trade_date: str = ""
    observe_date: str = ""
    non_trading_day_plan: bool = False  # plan generated on a non-trading day
    next_trading_day_hint: str = ""     # human-readable cross-date hint
    is_view_trading_day: bool = True    # whether the queried date is a trading day


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
    candidate_type: str = ""  # [TF-REVIEW-003] strategy_attribution_review
    hit_type: str = ""  # [TF-REVIEW-003] strategy_attribution_review
    tomorrow_focus: str = ""  # [TF-REVIEW-003] strategy_attribution_review
    downgrade_reason: str = ""  # [TF-REVIEW-003] strategy_attribution_review
    evidence_needed: List[str] = Field(default_factory=list)  # [TF-REVIEW-003]
    # [TF-REVIEW-005] review_observe_paper_attribution — observe signal + paper ledger join
    paper_status: str = ""
    signal_state: str = ""
    signal_current_price: Optional[float] = None
    signal_trigger_reason: str = ""
    signal_time: str = ""
    has_signal_for_date: bool = False
    review_bucket: str = ""  # not_triggered/triggered_pending/confirmed/invalidated/data_missing
    review_bucket_label: str = ""  # 未触发/触发待确认/已确认/已失效/缺数据
    review_bucket_reason: str = ""


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
    # [TF-REVIEW-004] review_empty_diagnostics
    empty_reason: str = ""
    empty_reason_message: str = ""
    suggested_action: str = ""
    available_plan_dates: List[str] = Field(default_factory=list)
    latest_plan_date: str = ""
    has_observe_signals: bool = False
    review_date: str = ""
    today_review_focus: Dict[str, Any] = Field(default_factory=dict)  # [TF-REVIEW-005]
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
    concentration_summary: Dict[str, Any] = Field(default_factory=dict)  # [H-014] mandate_concentration_gate
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TF-UX-003] post_market_review
class TradeFlowReviewGenerateResponse(BaseModel):
    status: str = "ok"
    trade_date: str = ""
    message: str = ""
    data_status: str = ""  # [TF-REVIEW-002] review_date_mapping
    data_status_message: str = ""  # [TF-REVIEW-002] review_date_mapping
    review: Optional[Dict[str, Any]] = None
    # [TF-REVIEW-004] review_empty_diagnostics
    empty_reason: str = ""
    empty_reason_message: str = ""
    suggested_action: str = ""
    available_plan_dates: List[str] = Field(default_factory=list)
    latest_plan_date: str = ""
    has_observe_signals: bool = False
    plan_date: str = ""
    effective_trade_date: str = ""
    review_date: str = ""


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
    observation_count: int = 0  # [TF-RISK-001] paper_risk_budget
    invested: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    risk_exposure: Dict[str, Any] = Field(default_factory=dict)  # [TF-RISK-001] paper_risk_budget


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
    data_quality_score: Optional[float] = None  # [TF-RISK-001] paper_risk_budget


# [TF-PAPER-001] paper_trading_ledger
class PaperActionResponse(BaseModel):
    status: str = "ok"
    message: str = ""
    trade_id: Optional[int] = None
    planned_amount: Optional[float] = None
    cash_balance: Optional[float] = None
    pnl: Optional[float] = None
    # [TF-RISK-001] paper_risk_budget
    rejected: Optional[bool] = None
    rule: Optional[str] = None
    reason: Optional[str] = None
    downgraded_to: Optional[str] = None


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


# [DATA-018] source_freshness_report

class SourceFreshnessEntryItem(BaseModel):
    data_type: str = ""
    label: str = ""
    status: str = "FAILED"
    status_label_cn: str = "未知"
    traffic_light: str = "red"
    primary_vendor: str = ""
    fallback_vendor: str = ""
    fallback_chain: List[str] = Field(default_factory=list)
    actual_vendor: str = ""
    endpoint: str = ""
    as_of: str = ""
    latest_data_date: str = ""
    unit: str = ""
    unit_verified: bool = False
    is_fallback: bool = False
    record_count: int = 0
    error: str = ""
    rate_limit_risk: str = ""
    diagnosis: str = ""
    freshness_catalog: str = ""


class SourceFreshnessSummary(BaseModel):
    total_sources: int = 0
    green_count: int = 0
    yellow_count: int = 0
    red_count: int = 0
    fallback_triggered_count: int = 0
    all_green: bool = False
    has_failures: bool = False
    has_warnings: bool = False
    overall_status: str = "has_failures"
    status_counts: Dict[str, int] = Field(default_factory=dict)
    high_rate_limit_risk_sources: List[str] = Field(default_factory=list)


class SourceFreshnessResponse(BaseModel):
    status: str = "ok"
    report_date: str = ""
    generated_at: str = ""
    symbol: str = ""
    entries: List[SourceFreshnessEntryItem] = Field(default_factory=list)
    summary: SourceFreshnessSummary = Field(default_factory=SourceFreshnessSummary)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [DATA-020] live_sampling_health_ui
#
# Layered status classification for the live-sampling health daily report.
# The frontend MUST distinguish:
#   - skipped          → "未启用实盘抽样" (grey, never green)
#   - failed/rate_limited → red fault
#   - normal_no_data   → grey "正常无数据"
#   - has_data/stale/unit_unverified → green/yellow as usual
class LiveSamplingSampleItem(BaseModel):
    symbol: str = ""
    name: str = ""
    category: str = ""
    category_cn: str = ""


class LiveSamplingResultItem(BaseModel):
    data_type: str = ""
    data_type_label: str = ""
    symbol: str = ""
    symbol_name: str = ""
    category: str = ""
    status: str = "FAILED"
    status_label_cn: str = "未知"
    traffic_light: str = "red"
    actual_vendor: str = ""
    primary_vendor: str = ""
    fallback_vendor: str = ""
    is_fallback: bool = False
    latency_ms: float = 0.0
    record_count: int = 0
    unit: str = ""
    unit_verified: bool = False
    as_of: str = ""
    error: str = ""
    diagnosis: str = ""
    rate_limit_risk: str = ""


class LiveSamplingSummary(BaseModel):
    total_checks: int = 0
    status_counts: Dict[str, int] = Field(default_factory=dict)
    green_count: int = 0
    yellow_count: int = 0
    red_count: int = 0
    skipped_count: int = 0
    skipped_only: bool = False
    fallback_triggered_count: int = 0
    all_green: bool = False
    has_failures: bool = False
    has_warnings: bool = False
    by_data_type: Dict[str, Any] = Field(default_factory=dict)
    overall_status: str = "no_data"


class LiveSamplingResponse(BaseModel):
    status: str = "ok"
    has_report: bool = False
    report_date: str = ""
    generated_at: str = ""
    env_gated: bool = True
    samples: List[LiveSamplingSampleItem] = Field(default_factory=list)
    results: List[LiveSamplingResultItem] = Field(default_factory=list)
    summary: LiveSamplingSummary = Field(default_factory=LiveSamplingSummary)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [H-013] mandate_topic_heatmap

class TopicHeatPointItem(BaseModel):
    date: str = ""
    signal_count: int = 0
    candidate_count: int = 0
    unique_candidates: int = 0
    policy_level: str = "UNKNOWN"
    policy_level_weight: int = 0
    evidence_count: int = 0
    overheat_flags: List[str] = Field(default_factory=list)
    lifecycle_state: str = ""
    topic_status: str = "UNKNOWN"
    topic_status_label: str = "未知"
    heat: float = 0.0


class TopicHeatWindowItem(BaseModel):
    window_days: int = 0
    window_label: str = ""
    signal_count: int = 0
    candidate_count: int = 0
    unique_candidates: int = 0
    evidence_count: int = 0
    active_days: int = 0
    max_policy_level: str = "UNKNOWN"
    max_policy_level_weight: int = 0


class TopicHeatCandidateItem(BaseModel):
    symbol: str = ""
    name: str = ""
    company_role: str = ""
    beneficiary_path: List[str] = Field(default_factory=list)
    mandate_score: float = 0.0
    tier: str = ""
    candidate_type: str = ""
    latest_date: str = ""


class TopicHeatmapEntryItem(BaseModel):
    topic: str = ""
    description: str = ""
    topic_status: str = "UNKNOWN"
    topic_status_label: str = "未知"
    policy_level: str = "UNKNOWN"
    policy_level_weight: int = 0
    is_left_side: bool = False
    is_observe_only: bool = False
    is_confirmed: bool = False
    heat_curve: List[TopicHeatPointItem] = Field(default_factory=list)
    windows: Dict[str, TopicHeatWindowItem] = Field(default_factory=dict)
    heat_trend: str = "UNKNOWN"
    heat_trend_label: str = "未知"
    state_change_label: str = "持平"
    state_change_positive: bool = False
    latest_date: str = ""
    latest_signal_count: int = 0
    peak_date: str = ""
    peak_heat: float = 0.0
    evidence_links: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_summary: str = ""
    counter_evidence_gaps: List[str] = Field(default_factory=list)
    overheat_flags: List[str] = Field(default_factory=list)
    candidates: List[TopicHeatCandidateItem] = Field(default_factory=list)
    candidate_symbols: List[str] = Field(default_factory=list)


class TopicHeatmapResponse(BaseModel):
    status: str = "ok"
    as_of: str = ""
    window_days: int = 60
    topics: List[TopicHeatmapEntryItem] = Field(default_factory=list)
    total_topics: int = 0
    active_topics: int = 0
    rising_topics: int = 0
    cooling_topics: int = 0
    left_side_topics: int = 0
    observe_only_topics: int = 0
    summary: Dict[str, Any] = Field(default_factory=dict)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [H-015] mandate_daily_report
# [H-017] mandate_evidence_packet
# [KB-004] tradeflow_knowledge_score
class MandateEvidencePacketItem(BaseModel):
    symbol: str = ""
    name: str = ""
    topic: str = ""
    policy_theme: str = ""
    policy_level: str = ""
    policy_level_weight: int = 0
    topic_status: str = ""
    topic_status_label: str = ""
    policy_evidence_count: int = 0
    industry_chain_role: str = ""
    industry_chain_segments: List[str] = Field(default_factory=list)
    beneficiary_path: List[str] = Field(default_factory=list)
    company_role: str = ""
    company_role_label: str = ""
    raw_company_role: str = ""
    has_company_evidence: bool = False
    company_evidence_available: List[str] = Field(default_factory=list)
    evidence_titles: List[Dict[str, Any]] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    needs_manual_research: bool = False
    confidence: str = ""
    confidence_reason: str = ""
    local_knowledge_summary: Dict[str, Any] = Field(default_factory=dict)  # [KB-004]


class MandateDailyCandidateItem(BaseModel):
    symbol: str = ""
    name: str = ""
    topic: str = ""
    company_role: str = ""
    candidate_type: str = ""
    tier: str = ""
    mandate_score: float = 0.0
    latest_date: str = ""
    entry_reason: str = ""
    evidence_gaps: List[str] = Field(default_factory=list)
    evidence_packet: MandateEvidencePacketItem = Field(default_factory=MandateEvidencePacketItem)  # [H-017]


class MandateDailyReportResponse(BaseModel):
    status: str = "ok"
    as_of: str = ""
    generated_at: str = ""
    rising_topics: List[Dict[str, Any]] = Field(default_factory=list)
    cooling_topics: List[Dict[str, Any]] = Field(default_factory=list)
    main_candidates: List[MandateDailyCandidateItem] = Field(default_factory=list)
    observation_candidates: List[MandateDailyCandidateItem] = Field(default_factory=list)
    entry_reasons: List[Dict[str, Any]] = Field(default_factory=list)
    exit_reasons: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_gaps: List[Dict[str, Any]] = Field(default_factory=list)
    markdown: str = ""
    source: str = "generated"
    path: str = ""
    markdown_path: str = ""
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TRACK-001] observation_warehouse
# [TRACK-006] add_to_observation — provenance fields surfaced to UI
# Note: numeric price fields use float (not Optional[float]) with default 0.0 so
# that boundary values like entry_low=0 are never surfaced as N/A downstream.
class ObservationItemResponse(BaseModel):
    id: int = 0
    symbol: str = ""
    name: str = ""
    status: str = "watching"
    entry_low: float = 0.0
    entry_high: float = 0.0
    trigger_price: float = 0.0
    invalid_price: float = 0.0
    horizon: str = "short"
    source: str = "manual"
    reason: str = ""
    priority: int = 0
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_reviewed_at: str = ""
    # [TRACK-006] add_to_observation — provenance & history fields
    strategy_tags: List[str] = Field(default_factory=list)
    score: float = 0.0
    action_label: str = ""
    research_direction: str = ""
    source_history: List[Dict[str, Any]] = Field(default_factory=list)
    # [KB-011] knowledge_contract_ui — read-only KB fields surfaced on the
    # observation warehouse detail so the frontend can render the same
    # "本地知识 / 研报关注度" block as TradeFlow candidates. Populated
    # best-effort by ``_enrich_observation_items_with_knowledge``; empty
    # defaults equal NORMAL_NO_DATA semantics (never an error).
    research_attention_score: float = 0.0
    research_attention_effective_score: float = 0.0
    research_attention_overheat_penalty: float = 0.0
    research_attention_summary: str = ""
    research_attention_detail: Dict[str, Any] = Field(default_factory=dict)
    knowledge_theme_count: int = 0
    local_knowledge_score: float = 0.0
    knowledge_hit_count: int = 0
    local_knowledge_summary: str = ""
    local_knowledge_detail: Dict[str, Any] = Field(default_factory=dict)
    needs_tree_work_research: bool = False
    knowledge_influence_explain: List[str] = Field(default_factory=list)  # [TF-KB-001] knowledge_score_calibration
    knowledge_influence_detail: Dict[str, Any] = Field(default_factory=dict)  # [TF-KB-001] knowledge_score_calibration
    # [PLAYBOOK-001] lifecycle_contract — optional playbook stage + contract
    # dict. Populated best-effort by upstream services; defaults to None/empty
    # so old observation items are unaffected. Unknown stages are never coerced
    # to "hold" (see playbook_contract.normalize_playbook_stage).
    playbook_stage: Optional[str] = None
    playbook_contract: Dict[str, Any] = Field(default_factory=dict)


# [TRACK-001] observation_warehouse
class ObservationItemListResponse(BaseModel):
    status: str = "ok"
    items: List[ObservationItemResponse] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TRACK-001] observation_warehouse
class ObservationItemCreateRequest(BaseModel):
    symbol: str
    name: str = ""
    status: str = "watching"
    entry_low: float = 0.0
    entry_high: float = 0.0
    trigger_price: float = 0.0
    invalid_price: float = 0.0
    horizon: str = "short"
    source: str = "manual"
    reason: str = ""
    priority: int = 0
    notes: str = ""
    # [TRACK-006] add_to_observation — optional provenance fields
    strategy_tags: List[str] = Field(default_factory=list)
    score: float = 0.0
    action_label: str = ""
    research_direction: str = ""
    # [PLAYBOOK-001] lifecycle_contract — persisted in TradeFlow DB.
    playbook_stage: Optional[str] = None
    playbook_contract: Dict[str, Any] = Field(default_factory=dict)


# [TRACK-001] observation_warehouse
class ObservationItemUpdateRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    trigger_price: Optional[float] = None
    invalid_price: Optional[float] = None
    horizon: Optional[str] = None
    source: Optional[str] = None
    reason: Optional[str] = None
    priority: Optional[int] = None
    notes: Optional[str] = None
    touch_last_reviewed: bool = False
    # [TRACK-006] add_to_observation — optional provenance updates
    strategy_tags: Optional[List[str]] = None
    score: Optional[float] = None
    action_label: Optional[str] = None
    research_direction: Optional[str] = None
    # [PLAYBOOK-001] lifecycle_contract — partial update, stored as JSON.
    playbook_stage: Optional[str] = None
    playbook_contract: Optional[Dict[str, Any]] = None


# [TRACK-001] observation_warehouse
class ObservationItemMarkRequest(BaseModel):
    status: str = Field(..., description="目标状态，如 invalidated / removed / entered / near_entry 等")
    note: Optional[str] = None


# [TRACK-001] observation_warehouse
class ObservationBulkUpsertItem(BaseModel):
    symbol: str
    name: str = ""
    status: str = "watching"
    entry_low: float = 0.0
    entry_high: float = 0.0
    trigger_price: float = 0.0
    invalid_price: float = 0.0
    horizon: str = "short"
    source: str = "manual"
    reason: str = ""
    priority: int = 0
    notes: str = ""
    # [TRACK-006] add_to_observation — provenance + notes-protection flag
    strategy_tags: List[str] = Field(default_factory=list)
    score: float = 0.0
    action_label: str = ""
    research_direction: str = ""
    force_overwrite_notes: bool = False
    # [PLAYBOOK-001] lifecycle_contract — bulk upsert persistence.
    playbook_stage: Optional[str] = None
    playbook_contract: Dict[str, Any] = Field(default_factory=dict)


# [TRACK-001] observation_warehouse
class ObservationBulkUpsertRequest(BaseModel):
    items: List[ObservationBulkUpsertItem] = Field(default_factory=list)


# [TRACK-001] observation_warehouse
class ObservationActionResponse(BaseModel):
    status: str = "ok"
    message: str = ""
    item: Optional[ObservationItemResponse] = None
    item_id: Optional[int] = None
    symbol: Optional[str] = None
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TRACK-001] observation_warehouse
class ObservationBulkUpsertResponse(BaseModel):
    status: str = "ok"
    created: List[str] = Field(default_factory=list)
    updated: List[str] = Field(default_factory=list)
    errored: List[Dict[str, Any]] = Field(default_factory=list)
    created_count: int = 0
    updated_count: int = 0
    errored_count: int = 0
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TRACK-006] add_to_observation — one-click add from candidate / TA report
class ObservationAddFromCandidateRequest(BaseModel):
    """Push a TradeFlow candidate into the observation warehouse.

    The endpoint accepts a slim payload (``symbol`` + ``trade_date``) and
    resolves the rest from the persisted candidate row, so the frontend does
    not need to forward the whole candidate dict. An optional ``extra_notes``
    field lets the user annotate the new item; existing user notes are never
    clobbered unless ``force_overwrite_notes`` is true.
    """
    symbol: str
    trade_date: str = ""
    via: str = "candidate_drawer"
    extra_notes: str = ""
    force_overwrite_notes: bool = False


# [TRACK-006] add_to_observation
class ObservationAddFromTAReportRequest(BaseModel):
    """Push a TA report into the observation warehouse.

    Accepts either:
      - ``symbol`` + ``report_id`` (preferred — backend re-reads the stored
        report), or
      - ``symbol`` + inline ``action_label`` / ``research_direction`` /
        ``target_price`` / ``stop_loss_price`` (fallback when the report is
        not yet persisted, e.g. live preview).
    """
    symbol: str
    report_id: Optional[str] = None
    name: str = ""
    action_label: str = ""
    research_direction: str = ""
    target_price: float = 0.0
    stop_loss_price: float = 0.0
    via: str = "analysis_page"
    extra_notes: str = ""
    force_overwrite_notes: bool = False


# [TRACK-006] add_to_observation
class ObservationAddResponse(BaseModel):
    status: str = "ok"
    action: str = ""  # "created" | "updated" | ""
    message: str = ""
    symbol: Optional[str] = None
    item: Optional[ObservationItemResponse] = None
    item_id: Optional[int] = None
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TRACK-008] observation_bulk_import_export
class ObservationImportRequest(BaseModel):
    """Bulk import observation items from a CSV / text blob.

    The backend parses the blob (Chinese or English headers, or simple
    whitespace-separated ``<code> [name]`` lines), dedups within the blob by
    normalized symbol, then upserts. Existing user notes are preserved unless
    ``force_overwrite_notes`` is true AND incoming notes are non-empty.
    """
    csv_text: str = Field(..., description="CSV / 制表符 / 空格分隔的观察仓条目文本")
    force_overwrite_notes: bool = False


# [TRACK-008] observation_bulk_import_export
class ObservationImportResponse(BaseModel):
    status: str = "ok"
    message: str = ""
    parsed_count: int = 0
    created: List[str] = Field(default_factory=list)
    updated: List[str] = Field(default_factory=list)
    errored: List[Dict[str, Any]] = Field(default_factory=list)
    created_count: int = 0
    updated_count: int = 0
    errored_count: int = 0
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)


# [TRACK-008] observation_bulk_import_export
class ObservationExportResponse(BaseModel):
    status: str = "ok"
    csv_text: str = ""
    count: int = 0
    runtime_tier_meta: RuntimeTierMeta = Field(default_factory=RuntimeTierMeta)
