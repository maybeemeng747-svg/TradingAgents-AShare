// Agent Types
export type AgentStatus = 'pending' | 'in_progress' | 'completed' | 'error' | 'skipped'

export interface Agent {
    id: string
    name: string
    team: string
    status: AgentStatus
    description?: string
    startedAt?: number
    finishedAt?: number
}

export interface AgentTeam {
    name: string
    agents: Agent[]
}

// Analysis Types
export interface InstrumentContext {
    symbol: string
    security_name: string
    market_country: string
    exchange: string
    currency: string
    asset_type: string
}

export interface MarketContext {
    trade_date: string
    timezone: string
    market_country: string
    exchange: string
    market_session: string
    market_is_open: boolean
    analysis_mode: string
    data_as_of: string
    session_note: string
}

export interface UserContext {
    objective?: string
    risk_profile?: string
    investment_horizon?: string
    cash_available?: number
    current_position?: number
    current_position_pct?: number
    average_cost?: number
    max_loss_pct?: number
    constraints?: string[]
    user_notes?: string
}

export interface WorkflowContext {
    context_version: string
    request_source: string
    selected_analysts: string[]
}

export interface GameTheorySignals {
    board?: string
    players?: string[]
    player_states?: Record<string, string>
    likely_actions?: Record<string, string[]>
    dominant_strategy?: string
    fragile_equilibrium?: string
    counter_consensus_signal?: string
    confidence?: number
}

export interface RiskFeedbackState {
    retry_count: number
    max_retries: number
    revision_required: boolean
    latest_risk_verdict: string
    hard_constraints: string[]
    soft_constraints: string[]
    execution_preconditions: string[]
    de_risk_triggers: string[]
    revision_reason: string
}

export interface AnalysisRequest {
    symbol: string
    trade_date: string
    selected_analysts: string[]
    objective?: string
    risk_profile?: string
    investment_horizon?: string
    cash_available?: number
    current_position?: number
    current_position_pct?: number
    average_cost?: number
    max_loss_pct?: number
    constraints?: string[]
    user_notes?: string
    config_overrides?: Record<string, unknown>
    dry_run?: boolean
}

export interface AnalysisResponse {
    job_id: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    created_at: string
}

export interface JobStatus {
    job_id: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    created_at: string
    started_at?: string
    finished_at?: string
    symbol: string
    trade_date: string
    error?: string
    waiting_ahead_count?: number | null
    scheduled_running_count?: number | null
    scheduled_concurrency_limit?: number | null
}

// SSE Event Types
export type SSEEventType =
    | 'job.created'
    | 'job.running'
    | 'job.completed'
    | 'job.failed'
    | 'agent.status'
    | 'agent.message'
    | 'agent.tool_call'
    | 'agent.report'
    | 'agent.report.chunk'
    | 'agent.snapshot'
    | 'agent.milestone'
    | 'agent.writing'
    | 'agent.activity'
    | 'agent.activity_complete'
    | 'agent.token'
    | 'agent.debate'
    | 'agent.debate.token'

export interface SSEEvent {
    event: SSEEventType
    data: Record<string, unknown>
    timestamp: string
}

export interface AgentStatusEvent {
    agent: string
    status: AgentStatus
    previous_status?: AgentStatus
}

export interface AgentMessageEvent {
    agent: string | null
    message_type: string | null
    content: string
}

export interface AgentToolCallEvent {
    agent: string | null
    tool_call: {
        name: string
        args: Record<string, unknown>
    }
}

export interface AgentReportEvent {
    section: string
    content: string
}

export interface ReportChunkEvent {
    section: string
    chunk: string
    index: number
    is_complete: boolean
}

export interface AgentMilestoneEvent {
    stage: string
    title: string
    summary: string
    timestamp: string
}

export interface AgentToolCallDisplayEvent {
    agent: string
    tool: string
    description: string
}

export interface AgentWritingEvent {
    agent: string
    report: string
    report_name: string
    status: 'writing' | 'completed'
}

export interface AgentTokenEvent {
    agent: string
    report: string
    token: string
    horizon?: string
}

export interface AgentActivityEvent {
    agent: string
    type: 'data_fetch' | 'data_analysis' | 'writing' | 'thinking'
    details: string
    tools?: string[]
    is_update?: boolean
}

export interface AgentActivityCompleteEvent {
    agent: string
    type: string
}

export interface AgentSnapshotEvent {
    agents: Array<{
        team: string
        agent: string
        status: AgentStatus
    }>
}

// Streaming Report State
export interface StreamingSectionState {
    buffer: string
    displayed: string
    isTyping: boolean
    isComplete: boolean
}

export interface MilestoneMessage {
    id: string
    stage: string
    title: string
    summary: string
    timestamp: string
}

// Report Types
export interface AnalysisReport {
    symbol: string
    trade_date: string
    decision?: string
    direction?: string
    confidence?: number | null
    target_price?: number | null
    stop_loss_price?: number | null
    instrument_context?: InstrumentContext
    market_context?: MarketContext
    user_context?: UserContext
    workflow_context?: WorkflowContext
    market_report?: string
    sentiment_report?: string
    news_report?: string
    fundamentals_report?: string
    macro_report?: string
    smart_money_report?: string
    volume_price_report?: string
    game_theory_report?: string
    game_theory_signals?: GameTheorySignals
    investment_plan?: string
    trader_investment_plan?: string
    risk_feedback_state?: RiskFeedbackState
    final_trade_decision?: string
}

// UI Types
export interface LogEntry {
    id: string
    timestamp: string
    type: 'system' | 'agent' | 'tool' | 'data' | 'error'
    content: string
    agent?: string
}

export interface StockInfo {
    symbol: string
    name: string
    price: number
    change: number
    changePercent: number
}

export interface KlineCandle {
    date: string
    open: number
    high: number
    low: number
    close: number
    volume?: number | null
    amount?: number | null
    change?: number | null
    change_percent?: number | null
    turnover_rate?: number | null
}

export interface KlineResponse {
    symbol: string
    start_date: string
    end_date: string
    candles: KlineCandle[]
}

// Structured extraction types
export interface RiskItem {
    name: string
    level: 'high' | 'medium' | 'low'
    description?: string
}

export interface KeyMetric {
    name: string
    value: string
    status: 'good' | 'neutral' | 'bad'
}

// Report Types (from database)
export interface Report {
    id: string
    user_id?: string
    symbol: string
    name?: string
    trade_date: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    error?: string
    decision?: string
    direction?: string
    confidence?: number
    target_price?: number
    stop_loss_price?: number
    risk_items?: RiskItem[]
    key_metrics?: KeyMetric[]
    created_at?: string
    updated_at?: string
    waiting_ahead_count?: number | null
    scheduled_running_count?: number | null
    scheduled_concurrency_limit?: number | null
}

export interface ReportDetail extends Report {
    market_report?: string
    sentiment_report?: string
    news_report?: string
    fundamentals_report?: string
    macro_report?: string
    smart_money_report?: string
    volume_price_report?: string
    game_theory_report?: string
    investment_plan?: string
    trader_investment_plan?: string
    final_trade_decision?: string
    result_data?: AnalysisReport
}

export interface ReportListResponse {
    total: number
    reports: Report[]
}

export interface AnnouncementItem {
    title: string
    detail: string
}

export interface Announcement {
    id: string
    tag?: string
    title: string
    summary?: string
    published_at: string
    items: AnnouncementItem[]
    cta_label?: string
    cta_path?: string
}

export interface LatestAnnouncementResponse {
    announcement: Announcement | null
}

// Watchlist & Scheduled Analysis
export interface WatchlistItem {
    id: string
    symbol: string
    name: string
    sort_order: number
    notes: string | null
    created_at: string
    has_scheduled: boolean
}

export interface WatchlistBatchResult {
    input: string
    symbol?: string
    name?: string
    status: 'added' | 'duplicate' | 'invalid' | 'failed'
    message: string
    item?: WatchlistItem
}

export interface WatchlistBatchResponse {
    message: string
    summary: {
        total: number
        added: number
        duplicate: number
        failed: number
    }
    results: WatchlistBatchResult[]
}

export interface ScheduledAnalysis {
    id: string
    symbol: string
    name: string
    horizon: string
    trigger_time: string
    is_active: boolean
    last_run_date: string | null
    last_run_status: string | null
    last_report_id: string | null
    consecutive_failures: number
    created_at: string
    has_imported_context?: boolean
    imported_current_position?: number | null
    imported_average_cost?: number | null
    imported_trade_points_count?: number
}

export interface ScheduledBatchUpdateResponse {
    items: ScheduledAnalysis[]
}

export interface ScheduledBatchDeleteResponse {
    deleted_ids: string[]
    missing_ids: string[]
}

export interface ScheduledBatchTriggerJob {
    item_id: string
    job_id: string
    symbol: string
    name: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    created_at: string
    current_position?: number | null
    average_cost?: number | null
}

export interface ScheduledBatchTriggerResponse {
    summary: {
        total: number
        with_position_context: number
    }
    jobs: ScheduledBatchTriggerJob[]
}

export interface StockSearchResult {
    symbol: string
    name: string
}

export interface ImportedPortfolioPosition {
    symbol: string
    name: string
    current_position?: number | null
    available_position?: number | null
    average_cost?: number | null
    market_value?: number | null
    current_position_pct?: number | null
    trade_points_count: number
    latest_trade_at?: string | null
    latest_trade_action?: string | null
    last_imported_at?: string | null
    recent_trade_points?: Array<Record<string, unknown>>
}

export interface ImportedScheduledSyncSummary {
    created: string[]
    existing: string[]
    skipped_limit: string[]
}

export interface PortfolioImportState {
    auto_apply_scheduled: boolean
    last_synced_at?: string | null
    last_error?: string | null
    summary: {
        positions: number
    }
    scheduled_sync?: ImportedScheduledSyncSummary
    positions: ImportedPortfolioPosition[]
}

export interface PortfolioPositionInput {
    symbol: string
    name?: string
    current_position?: number | null
    available_position?: number | null
    average_cost?: number | null
    market_value?: number | null
    current_position_pct?: number | null
}

export interface PortfolioOverviewResponse {
    watchlist: WatchlistItem[]
    scheduled: ScheduledAnalysis[]
    latest_reports: Report[]
    portfolio_import: PortfolioImportState | null
}

export interface TrackingBoardAnalysis {
    report_id: string
    trade_date: string
    is_previous_trade_day: boolean
    decision?: string | null
    direction?: string | null
    high_price?: number | null
    low_price?: number | null
    trader_advice_summary?: string | null
    trader_investment_plan?: string | null
    final_trade_decision?: string | null
}

export interface TrackingBoardItem {
    symbol: string
    name: string
    current_position?: number | null
    available_position?: number | null
    average_cost?: number | null
    market_value?: number | null
    current_position_pct?: number | null
    live_market_value?: number | null
    floating_pnl?: number | null
    floating_pnl_pct?: number | null
    live_price?: number | null
    day_open?: number | null
    price_change?: number | null
    price_change_pct?: number | null
    day_high?: number | null
    day_low?: number | null
    previous_close?: number | null
    volume?: number | null
    amount?: number | null
    quote_time?: string | null
    quote_source?: string | null
    last_imported_at?: string | null
    analysis?: TrackingBoardAnalysis | null
}

export interface TrackingBoardResponse {
    previous_trade_date: string
    refresh_interval_seconds: number
    items: TrackingBoardItem[]
}

// Runtime config
export interface RuntimeConfig {
    llm_provider: string
    deep_think_llm: string
    quick_think_llm: string
    backend_url: string
    max_debate_rounds: number
    max_risk_discuss_rounds: number
    has_api_key?: boolean
    has_wecom_webhook?: boolean
    wecom_webhook_display?: string | null
    has_bark_url?: boolean
    bark_url_display?: string | null
    server_fallback_enabled?: boolean
    email_report_enabled?: boolean
    wecom_report_enabled?: boolean
    bark_report_enabled?: boolean
    default_analysts?: string[]
    current_api_key_scope?: string | null
    api_key_scopes?: string[]
}

export interface RuntimeConfigUpdateResponse {
    message: string
    applied: RuntimeConfigUpdate
    has_api_key: boolean
    current: RuntimeConfig
    warmup?: RuntimeConfigWarmup
}

export interface RuntimeConfigUpdate {
    llm_provider?: string
    deep_think_llm?: string
    quick_think_llm?: string
    backend_url?: string
    max_debate_rounds?: number
    max_risk_discuss_rounds?: number
    api_key?: string
    wecom_webhook_url?: string
    bark_url?: string
    clear_api_key?: boolean
    clear_wecom_webhook?: boolean
    clear_bark_url?: boolean
    email_report_enabled?: boolean
    wecom_report_enabled?: boolean
    bark_report_enabled?: boolean
    default_analysts?: string[]
    warmup?: boolean
    force_warmup?: boolean
}

export interface RuntimeWarmupRequest extends RuntimeConfigUpdate {
    prompt?: string
}

export interface RuntimeConfigWarmup {
    requested: boolean
    triggered: boolean
    status: 'scheduled' | 'skipped' | 'disabled'
    message: string
    models?: string[]
}

export interface RuntimeWarmupResult {
    model: string
    targets: string[]
    content?: string | null
    error?: string | null
}

export interface RuntimeWarmupResponse {
    prompt: string
    results: RuntimeWarmupResult[]
}

export interface WecomWarmupRequest {
    wecom_webhook_url?: string
    content?: string
}

export interface WecomWarmupResponse {
    sent: boolean
    message: string
    webhook_display?: string | null
}

export interface BarkWarmupRequest {
    bark_url?: string
    content?: string
}

export interface BarkWarmupResponse {
    sent: boolean
    message: string
    bark_url_display?: string | null
}

export interface AuthUser {
    id: string
    email: string
    created_at?: string
    last_login_at?: string
}

export interface AuthVerifyResponse {
    access_token: string
    token_type: string
    user: AuthUser
}

export interface UserToken {
    id: string
    name: string
    token?: string
    token_hint?: string
    last_used_at?: string
    created_at: string
}

export interface UserTokenCreateRequest {
    name: string
}

// Feedback types
export interface FeedbackItem {
    id: string
    user_email: string
    subject: string
    content: string
    admin_reply?: string | null
    replied_at?: string | null
    is_read: boolean
    created_at?: string
    updated_at?: string
}

export interface FeedbackListResponse {
    total: number
    feedbacks: FeedbackItem[]
}

export interface FeedbackUnreadResponse {
    unread_count: number
}

// Debate message (for battle view)
export interface DebateMessage {
    debate: 'research' | 'risk'
    agent: string
    round: number        // -1 = verdict
    content: string
    isVerdict?: boolean
    horizon?: string
}

// TradeFlow Types
export interface TradeFlowCandidateItem {
    symbol: string
    name: string
    tier: string
    composite_score: number
    score: number
    strategy_tags: string[]
    primary_strategy: string
    trigger_price: number | null
    support_price: number | null
    invalid_price: number | null
    need_deep_ta: boolean
    action: string
    reason: string
    observe_state: string
    observe_trigger_count: number
    observe_first_trigger_time: string
    tradeflow_data_completeness: number
    missing_data_fields: string[]
    data_completeness: number
    missing_evidence: string[]
    game_balance: string
    bull_case: string
    bear_case: string
    policy_case: string
    fund_flow_case: string
    why_deep_ta: string
    why_not_deep_ta: string
    risk_flags: string[]
    policy_tags: string[]
    version_score: number
    narrative_score: number
    fund_flow_anomaly_score: number
    fund_flow_anomaly_tags: string[]
    fund_flow_unit_verified: boolean
    evidence_gate_applied: boolean
    deep_ta_status: string
    deep_ta_dispatch_reason: string
    deep_ta_model: string
    source: string
    universe_sources: string[]
    resonance_count: number
    ta_budget_priority: number
    tier_reason: string
    missing_evidence_for_upgrade: string[]
    created_at: string
    updated_at: string
}

export interface TradeFlowCandidateDetail extends TradeFlowCandidateItem {
    evidence: Record<string, unknown>
    policy_evidence_refs: Array<Record<string, unknown>>
    narrative_evidence_refs: Array<Record<string, unknown>>
    narrative_reasons: string[]
    risk_evidence_refs: Array<Record<string, unknown>>
    risk_reasons: string[]
    game_balance_refs: Array<Record<string, unknown>>
    fund_flow_anomaly_refs: Array<Record<string, unknown>>
    fund_flow_individual_summary: string
    fund_flow_board_summary: string
    signal_category_hits: string[]
    positive_category_count: number
    what_to_upgrade: string[]
    deep_ta_report_path: string
    deep_ta_dispatch_time: string
    deep_ta_position_context: string
}

export interface TradeFlowSummary {
    total_candidates: number
    tier_a_count: number
    tier_b_count: number
    tier_c_count: number
    need_deep_ta_count: number
    avg_completeness: number
}

export interface TradeFlowDailyPlanResponse {
    status: string
    trade_date: string
    mode: string
    summary: string
    candidates: TradeFlowCandidateItem[]
    metadata: Record<string, unknown>
    summary_agg: TradeFlowSummary
    created_at: string
}

export interface TradeFlowCandidatesResponse {
    status: string
    trade_date: string
    candidates: TradeFlowCandidateItem[]
    summary_agg: TradeFlowSummary
}

export interface TradeFlowDiscoveryRequest {
    date: string
    symbols: string[]
    top_n: number
    include_holdings: boolean
    include_watchlist: boolean
    use_event_source: boolean
    news_texts: string[]
    save_candidates: boolean
}

export interface TradeFlowDiscoveryResponse {
    status: string
    trade_date: string
    summary: string
    universe_size: number
    candidate_count: number
    filtered_count: number
    candidates: TradeFlowCandidateItem[]
    filtered: Array<{ symbol: string; name: string; source: string; reason: string }>
    metadata: Record<string, unknown>
}

export interface TradeFlowCandidateDetailResponse {
    status: string
    trade_date: string
    candidate: TradeFlowCandidateDetail | null
}

export interface TradeFlowObserveItem {
    symbol: string
    name: string
    observe_state: string
    trigger_price: number | null
    invalid_price: number | null
    observe_trigger_count: number
    observe_first_trigger_time: string
    tier: string
    composite_score: number
    current_price: number | null
    trigger_reason: string
    strategy_tags: string[]
}

export interface TradeFlowObserveResponse {
    status: string
    trade_date: string
    observe_items: TradeFlowObserveItem[]
    triggered_count: number
    invalidated_count: number
    waiting_count: number
}

export interface TradeFlowTAQueueItem {
    symbol: string
    name: string
    tier: string
    ta_budget_priority: number
    need_deep_ta: boolean
    deep_ta_status: string
    deep_ta_dispatch_reason: string
    deep_ta_model: string
    why_deep_ta: string
    why_not_deep_ta: string
    composite_score: number
    tradeflow_data_completeness: number
    evidence_gate_applied: boolean
}

export interface TradeFlowTAQueueResponse {
    status: string
    trade_date: string
    queue: TradeFlowTAQueueItem[]
    total_in_queue: number
    dispatched_count: number
    blocked_count: number
    pending_count: number
}

export interface TradeFlowReviewItem {
    symbol: string
    name: string
    plan_action: string
    keep_observing: boolean
    reason: string
    tier: string
    composite_score: number
    strategy_tags: string[]
    trigger_price: number | null
    invalid_price: number | null
    observe_state: string
}

export interface TradeFlowReviewResponse {
    status: string
    trade_date: string
    reviewed_at: string
    results: TradeFlowReviewItem[]
    summary_agg: TradeFlowSummary
}

export type DataHealthStatus = 'OK' | 'PARTIAL' | 'FAILED' | 'STALE' | 'NOT_QUERIED'

export interface DataHealthSource {
    name: string
    available: boolean
    last_updated: string | null
    record_count: number
    error: string
    status?: DataHealthStatus
    fallback_vendor?: string | null
}

export interface TradeFlowDataHealthResponse {
    status: string
    tradeflow_db_available: boolean
    tradeflow_db_path: string
    sources: DataHealthSource[]
    latest_plan_date: string | null
    latest_candidates_date: string | null
    total_candidates_today: number
    total_signals_today: number
}

// [UI-007] tradeflow_filtered_trace
export interface TradeFlowFilteredItem {
    symbol: string
    name: string
    source: string
    reason: string
    run_id: string
    created_at: string
}

export interface TradeFlowFilteredResponse {
    status: string
    trade_date: string
    filtered: TradeFlowFilteredItem[]
    filter_breakdown: Record<string, number>
}
