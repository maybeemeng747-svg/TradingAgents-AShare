// [PERF-001] runtime_tier_contract
export interface RuntimeTierMeta {
    runtime_tier: string
    expected_latency: string
    llm_allowed: boolean
    requires_confirmation: boolean
    cost_risk: string
    tier_label: string
    tier_description: string
}

export const RUNTIME_TIER_LABELS: Record<string, string> = {
    FAST_RADAR: '快速筛选',
    LIGHT_RESEARCH: '轻量研究',
    FULL_TA: '完整 TA',
}

export const RUNTIME_TIER_LATENCY: Record<string, string> = {
    FAST_RADAR: '5-30s',
    LIGHT_RESEARCH: '1-3min',
    FULL_TA: '10-20min',
}

// [PERF-002] lightweight_ta_profiles
export interface RuntimeProfileMeta {
    runtime_profile: string
    profile_label: string
    tier: string
    expected_latency: string
    enabled_analysts: string[]
    enabled_risk_modules: string[]
    enabled_managers: string[]
    description: string
}

export const RUNTIME_PROFILE_LABELS: Record<string, string> = {
    MIDLINE_POLICY_LIGHT: '中线政策轻量',
    SHORT_TECH_LIGHT: '短线技术轻量',
    POSITION_RISK_LIGHT: '持仓风控轻量',
    FULL_TA: '完整 TA',
}

export const RUNTIME_PROFILE_LATENCY: Record<string, string> = {
    MIDLINE_POLICY_LIGHT: '1-3min',
    SHORT_TECH_LIGHT: '1-2min',
    POSITION_RISK_LIGHT: '1-2min',
    FULL_TA: '10-20min',
}

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
    query?: string
    horizons?: string[]
    user_intent?: Record<string, unknown>
    runtime_tier?: string  // [PERF-001]
    confirmed_full_ta?: boolean  // [PERF-001]
    runtime_profile?: string  // [PERF-002]
}

export interface AnalysisResponse {
    job_id: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    created_at: string
    runtime_tier?: string  // [PERF-001]
    runtime_tier_label?: string  // [PERF-001]
    expected_latency?: string  // [PERF-001]
    runtime_profile?: string  // [PERF-002]
    runtime_profile_label?: string  // [PERF-002]
    enabled_modules?: string[]  // [PERF-002]
}

// [PERF-004] full_ta_cost_gate
export interface FullTACostPreview {
    runtime_tier: string
    tier_label: string
    expected_latency: string
    llm_allowed: boolean
    cost_risk: string
    requires_confirmation: boolean
    llm_provider: string
    llm_model: string
    base_url_display: string
    enabled_modules: string[]
    estimated_llm_calls: number
    description: string
}

export interface ScheduledCostMeta {
    is_full_ta: boolean
    runtime_tier: string
    tier_label: string
    created_by: string
    trigger_frequency: string
    last_run_llm_summary: string
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
    research_direction?: string
    execution_action?: string
    action_label?: string
    data_blockers?: DataBlocker[]
    data_blocker_summary?: DataBlockerSummary
    // [REPORT-UX-003] wait_reason_codes
    wait_reason_codes?: string[]
    wait_reason_labels?: Record<string, string>
}

// [REPORT-UX-003] wait_reason_codes — explainable codes for WAIT actions.
export const WAIT_REASON_LABELS: Record<string, string> = {
    DATA_MISSING: '关键数据缺口',
    GATE_BLOCKED: '门禁未通过',
    CONFLICT: '结论冲突',
    NO_TRIGGER: '等待触发价',
    RISK_FIRST: '风险优先',
    NORMAL_NO_DATA: '数据正常·暂无触发',
}

// [DATA-021] report_data_blockers
export interface DataBlocker {
    key: string
    label: string
    status: 'normal_no_data' | 'query_failed' | 'not_queried' | 'field_missing' | 'skipped'
    status_label: string
    severity: 'info' | 'low' | 'medium' | 'high'
    reason: string
    impact: string
}

// [DATA-021] report_data_blockers
export interface DataBlockerSummary {
    level: 'ok' | 'info' | 'caution' | 'warning'
    message: string
    counts: Record<string, number>
    total: number
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
    research_direction?: string | null
    execution_action?: string | null
    action_label?: string | null
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
    data_blockers?: DataBlocker[] | null
    data_blocker_summary?: DataBlockerSummary | null
    // [REPORT-UX-003] wait_reason_codes
    wait_reason_codes?: string[] | null
    wait_reason_labels?: Record<string, string> | null
    // [KB-011] knowledge_contract_ui — top-level KB fields mirrored from
    // result_data so the report viewer can render 本地知识补充 / 研报关注度
    // without digging into result_data. Note: ``local_knowledge_summary`` on
    // reports is a DICT (KB-003/KB-008 merge), unlike the STRING shape on
    // TradeFlow candidates. Both shapes are accepted here.
    local_knowledge_block?: string | null
    local_knowledge_summary?: Record<string, unknown> | null
    research_attention_score?: number | null
    knowledge_theme_count?: number | null
    research_attention_summary?: string | null
    research_attention_block?: string | null
    // [REPORT-UX-006] knowledge_evidence_card — half-year facts fields are
    // mirrored from result_data by the backend (HY-004) but were missing
    // from this interface, so the report viewer could not render them.
    // All optional + nullable so legacy reports keep a compatible empty state.
    half_year_facts_block?: string | null
    half_year_facts_summary?: Record<string, unknown> | null
    half_year_facts_status?: string | null
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
    // [DATA-009] watchlist_notes_persistence — structured note fields
    topic: string | null
    benefit_score: number | null
    consensus_score: number | null
    expected_window: string | null
    evidence_gap: string | null
    watchlist_note_suggested: string | null
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

// [TRACK-009] holdings_import_contract
export interface HoldingsImportFieldDelta {
    before: number | string | null
    after: number | string | null
}

export interface HoldingsImportAddedItem {
    symbol: string
    incoming: Record<string, unknown>
}

export interface HoldingsImportUpdatedItem {
    symbol: string
    delta: Record<string, HoldingsImportFieldDelta>
    before?: Record<string, unknown>
    after?: Record<string, unknown>
}

export interface HoldingsImportRemovedItem {
    symbol: string
    before: Record<string, unknown>
}

export interface HoldingsImportInvalidItem {
    symbol?: string
    reason: string
    fields: string[]
    raw_symbol?: string
    raw?: unknown
}

export interface HoldingsImportWarning {
    symbol: string
    reason: string
    field?: string
}

// [TRACK-009] holdings_import_contract — dry-run 预览差异
export interface HoldingsImportDiff {
    dry_run: true
    source: string
    write_semantics: string
    added: HoldingsImportAddedItem[]
    updated: HoldingsImportUpdatedItem[]
    unchanged: HoldingsImportAddedItem[]
    removed: HoldingsImportRemovedItem[]
    errors: HoldingsImportInvalidItem[]
    warnings: HoldingsImportWarning[]
    added_count: number
    updated_count: number
    unchanged_count: number
    removed_count: number
    error_count: number
    valid_count: number
    invalid_count: number
}

// [TRACK-009] holdings_import_contract — 提交后返回
export interface HoldingsImportResult {
    dry_run: HoldingsImportDiff
    state: PortfolioImportState
}

export interface HoldingsImportEndpointInfo {
    path: string
    method: string
    tier?: string
    purpose: string
    accepts?: string
}

// [TRACK-009] holdings_import_contract — OpenClaw 契约（只读）
export interface HoldingsImportContract {
    schema_version: string
    contract_for: string
    description?: string
    write_endpoints: HoldingsImportEndpointInfo[]
    read_endpoints: HoldingsImportEndpointInfo[]
    field_semantics: Record<string, string>
    write_semantics: string
    invariants?: string[]
    text_formats?: Record<string, string>
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

export interface ObservationItemCreatePayload {
    symbol: string
    name?: string
    status?: string
    entry_low?: number
    entry_high?: number
    trigger_price?: number
    invalid_price?: number
    horizon?: string
    source?: string
    reason?: string
    priority?: number
    notes?: string
}

export interface ObservationActionResponse {
    status: string
    message: string
    item?: ObservationItemV2 | null
    item_id?: number | null
    symbol?: string | null
}

// [TRACK-008] observation_bulk_import_export
export interface ObservationImportResponse {
    status: string
    message: string
    parsed_count: number
    created: string[]
    updated: string[]
    errored: { symbol: string; reason: string }[]
    created_count: number
    updated_count: number
    errored_count: number
}

// [TRACK-008] observation_bulk_import_export
export interface ObservationExportResponse {
    status: string
    csv_text: string
    count: number
}

// [VLM-001] watchlist_table_parser
export interface WatchlistTableItem {
    symbol: string
    name?: string
    business?: string
    sector?: string
    bullish_score?: number | null
    consensus?: number | null
    notes?: string
}

export interface WatchlistTableParseResponse {
    mode: 'watchlist'
    items: WatchlistTableItem[]
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
    research_direction?: string | null
    execution_action?: string | null
    action_label?: string | null
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

// [TRACK-003] tracking_board_v2_frontend
type GuidancePriority = 'P0' | 'P1' | 'P2' | 'P3'
type GuidanceType =
    | 'holdings_risk'
    | 'holdings_no_analysis'
    | 'observation_in_entry_zone'
    | 'observation_near_entry'
    | 'observation_invalidated'
    | 'observation_ta_required'
    | 'observation_data_missing'
    | 'observation_needs_review'   // [TRACK-004] observation_state_engine
    | 'observation_missed_entry'
    | 'observation_watching'

export interface TrackingBoardV2Guidance {
    type: GuidanceType
    priority: GuidancePriority
    symbol: string
    name: string
    reason: string
    source: string
    as_of: string
    state?: string                 // [TRACK-004] observation_state_engine
    data_fields?: Record<string, unknown>
}

export interface TrackingBoardV2DataFreshness {
    status: 'fresh' | 'stale' | 'non_trading_day'
    holdings_total: number
    holdings_with_live_quotes: number
    holdings_with_analysis: number
    observation_total: number
    observation_with_live_quotes: number
    latest_quote_time: string | null
    as_of: string
}

export interface ObservationItemV2 {
    id: number
    symbol: string
    name: string
    status: string
    entry_low?: number | null
    entry_high?: number | null
    trigger_price?: number | null
    invalid_price?: number | null
    horizon?: string | null
    source?: string | null
    reason?: string | null
    priority?: number | null
    notes?: string | null
    live_price?: number | null
    price_change_pct?: number | null
    quote_time?: string | null
    quote_source?: string | null
    created_at?: string | null
    updated_at?: string | null
    // [TRACK-006] add_to_observation — provenance fields surfaced to UI
    strategy_tags?: string[]
    score?: number | null
    action_label?: string | null
    research_direction?: string | null
    source_history?: Array<{
        source: string
        as_of: string
        via: string
        reason: string
    }>
    // [KB-011] knowledge_contract_ui — read-only KB fields populated
    // best-effort by ``_enrich_observation_items_with_knowledge``. All
    // fields default to zero/empty when there is no knowledge hit; never
    // an error.
    research_attention_score?: number
    research_attention_effective_score?: number
    research_attention_overheat_penalty?: number
    research_attention_summary?: string
    research_attention_detail?: Record<string, unknown>
    knowledge_theme_count?: number
    local_knowledge_score?: number
    knowledge_hit_count?: number
    local_knowledge_summary?: string
    local_knowledge_detail?: Record<string, unknown>
    needs_tree_work_research?: boolean
}

export interface TrackingBoardV2Response {
    previous_trade_date: string
    is_trading_day: boolean
    refresh_interval_seconds: number
    as_of: string
    holdings: TrackingBoardItem[]
    observation_items: ObservationItemV2[]
    today_guidance: TrackingBoardV2Guidance[]
    alerts: TrackingBoardV2Guidance[]
    review_summary: any
    data_freshness: TrackingBoardV2DataFreshness
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
    candidate_type: string  // [H-005] mandate_radar_ui
    mandate_score: number  // [H-005]
    ambush_score: number  // [H-005]
    mandate_topic: string  // [H-005]
    company_role: string  // [H-005]
    beneficiary_path: string[]  // [H-005]
    candidate_type_reason: string  // [H-005]
    deep_ta_route: string  // [H-005]
    research_queue: string  // [H-007] mandate_ta_queue_router
    research_intent: string  // [H-007] mandate_ta_queue_router
    research_route_reason: string  // [H-007] mandate_ta_queue_router
    watchlist_note: string  // [H-008] mandate_watchlist_note
    watchlist_note_suggested: string  // [H-008] mandate_watchlist_note
    watchlist_topic: string  // [H-008] mandate_watchlist_note
    watchlist_benefit_score: number  // [H-008] mandate_watchlist_note
    watchlist_consensus_score: number  // [H-008] mandate_watchlist_note
    watchlist_evidence_gap: string[]  // [H-008] mandate_watchlist_note
    action_tier: string  // [TF-UX-004] actionable/watch/scan
    trade_priority_score: number  // [TF-UX-004]
    action_tier_reason: string  // [TF-UX-004]
    // [UI-012] tradeflow_focus_workspace — dimension scores for main candidate cards
    technical_score: number
    policy_score: number
    fund_flow_score: number
    event_score: number
    risk_penalty_score: number
    data_quality_score: number
    ranking_reasons: string[]
    weakness_reasons: string[]
    counter_evidence: Array<Record<string, unknown>>  // [H-009]
    overheat_flags: string[]  // [H-009]
    downgrade_reasons: string[]  // [H-009]
    what_would_change_mind: string[]  // [H-009]
    topic_lifecycle_state: string  // [H-010]
    topic_lifecycle_reason: string  // [H-010]
    topic_last_signal_date: string  // [H-010]
    topic_signal_count: number  // [H-010]
    contradiction_level: string  // [H-011]
    contradiction_items: Array<Record<string, unknown>>  // [H-011]
    blocking_evidence_gaps: string[]  // [H-011]
    next_verification_steps: string[]  // [H-011]
    local_knowledge_score: number  // [KB-004]
    knowledge_hit_count: number  // [KB-004]
    local_knowledge_summary: string  // [KB-004]
    local_knowledge_detail: Record<string, unknown>  // [KB-004]
    needs_tree_work_research: boolean  // [KB-004]
    // [HY-006] tradeflow_half_year_factor — half-year report fact factor fields
    // mirrored from backend ``TradeFlowCandidateItem`` so the drawer can render
    // 半年报事实对照 (报告期 / 支持或削弱 / 风险标记 / 需 Tree Work 复核).
    // Defaults to zero/empty when there is no half-year data (NO_FACTS semantics).
    half_year_fact_score: number  // [HY-006]
    half_year_fact_summary: string  // [HY-006]
    half_year_risk_flags: string[]  // [HY-006]
    half_year_fact_detail: Record<string, unknown>  // [HY-006]
    needs_research_review: boolean  // [HY-006]
    // [KB-011] knowledge_contract_ui — research attention fields mirrored from
    // backend ``TradeFlowCandidateItem`` so the drawer can render 研报关注度
    // (base score + effective score + decay/overheat breakdown). Defaults to
    // zero/empty when there is no hit (NORMAL_NO_DATA semantics).
    research_attention_score: number  // [KB-008]
    research_attention_effective_score: number  // [KB-009]
    research_attention_overheat_penalty: number  // [KB-009]
    research_attention_summary: string  // [KB-008]
    research_attention_detail: Record<string, unknown>  // [KB-008/KB-009]
    knowledge_theme_count: number  // [KB-008]
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
    ambush_reasons: string[]  // [H-005] mandate_radar_ui
    ambush_evidence_refs: Array<Record<string, unknown>>  // [H-005]
    mandate_evidence_refs: Array<Record<string, unknown>>  // [H-005]
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
    runtime_tier_meta: RuntimeTierMeta  // [PERF-001]
}

export interface TradeFlowCandidatesResponse {
    status: string
    trade_date: string
    candidates: TradeFlowCandidateItem[]
    summary_agg: TradeFlowSummary
    runtime_tier_meta: RuntimeTierMeta  // [PERF-001]
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
    runtime_tier_meta: RuntimeTierMeta  // [PERF-001]
}

export interface TriggerExplain {  // [TF-OBS-004] observe_refresh_alert_queue
    category: string  // triggered / near_trigger / invalidated / waiting / no_data
    why_triggered: string
    why_not: string
    how_far_off: string
    breach_pct: number | null
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
    paper_status: string  // [TF-OBS-003] observe_paper_sync
    trigger_distance_pct: number | null  // [TF-OBS-004]
    near_trigger: boolean  // [TF-OBS-004]
    trigger_explain: TriggerExplain  // [TF-OBS-004]
}

export interface TradeFlowObserveResponse {
    status: string
    trade_date: string
    observe_items: TradeFlowObserveItem[]
    triggered_count: number
    invalidated_count: number
    waiting_count: number
    observe_auto_run: boolean   // [TF-OBS-002] observe_auto_run
    last_observed_at: string    // [TF-OBS-002]
    observe_reason: string      // [TF-OBS-002]
    refresh_interval_seconds: number  // [TF-OBS-004]
    is_market_hours: boolean    // [TF-OBS-004]
    is_trading_day: boolean     // [TF-OBS-004]
    near_trigger_count: number  // [TF-OBS-004]
    pending_count: number       // [TF-OBS-004]
    runtime_tier_meta: RuntimeTierMeta  // [PERF-001]
    // [TF-OBS-005] observe_date_semantics — explicit plan/effective/observe dates
    plan_date?: string
    effective_trade_date?: string
    observe_date?: string
    non_trading_day_plan?: boolean
    next_trading_day_hint?: string
    is_view_trading_day?: boolean
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
    runtime_tier_meta: RuntimeTierMeta  // [PERF-001]
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
    candidate_type?: string  // [TF-REVIEW-003] strategy_attribution_review
    hit_type?: string  // [TF-REVIEW-003] strategy_attribution_review
    tomorrow_focus?: string  // [TF-REVIEW-003] strategy_attribution_review
    downgrade_reason?: string  // [TF-REVIEW-003] strategy_attribution_review
    evidence_needed?: string[]  // [TF-REVIEW-003] strategy_attribution_review
}

export interface TradeFlowReviewResponse {
    status: string
    trade_date: string
    reviewed_at: string
    results: TradeFlowReviewItem[]
    summary_agg: TradeFlowSummary
    plan_date?: string  // [TF-REVIEW-002] review_date_mapping
    effective_trade_date?: string  // [TF-REVIEW-002] review_date_mapping
    data_status?: string  // [TF-REVIEW-002] review_date_mapping
    data_status_message?: string  // [TF-REVIEW-002] review_date_mapping
    // [TF-REVIEW-004] review_empty_diagnostics
    empty_reason?: string
    empty_reason_message?: string
    suggested_action?: string
    available_plan_dates?: string[]
    latest_plan_date?: string
    has_observe_signals?: boolean
    review_date?: string
    runtime_tier_meta: RuntimeTierMeta  // [PERF-001]
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
    latest_observe_check_time: string | null
    latest_signal_time: string | null
    evidence_contract_available: boolean
    runtime_tier_meta: RuntimeTierMeta  // [PERF-001]
}

// [DATA-018] source_freshness_report
export type SourceFreshnessStatus =
    | 'HAS_DATA' | 'NORMAL_NO_DATA' | 'STALE'
    | 'FAILED' | 'RATE_LIMITED' | 'UNIT_UNVERIFIED'

export interface SourceFreshnessEntry {
    data_type: string
    label: string
    status: SourceFreshnessStatus
    status_label_cn: string
    traffic_light: 'green' | 'yellow' | 'red'
    primary_vendor: string
    fallback_vendor: string
    fallback_chain: string[]
    actual_vendor: string
    endpoint: string
    as_of: string
    latest_data_date: string
    unit: string
    unit_verified: boolean
    is_fallback: boolean
    record_count: number
    error: string
    rate_limit_risk: string
    diagnosis: string
    freshness_catalog: string
}

export interface SourceFreshnessSummary {
    total_sources: number
    green_count: number
    yellow_count: number
    red_count: number
    fallback_triggered_count: number
    all_green: boolean
    has_failures: boolean
    has_warnings: boolean
    overall_status: string
    status_counts: Record<string, number>
    high_rate_limit_risk_sources: string[]
}

export interface SourceFreshnessResponse {
    status: string
    report_date: string
    generated_at: string
    symbol: string
    entries: SourceFreshnessEntry[]
    summary: SourceFreshnessSummary
    runtime_tier_meta: RuntimeTierMeta
}

// [DATA-020] live_sampling_health_ui
export type LiveSamplingStatus =
    | 'HAS_DATA' | 'NORMAL_NO_DATA' | 'STALE'
    | 'FAILED' | 'RATE_LIMITED' | 'UNIT_UNVERIFIED' | 'SKIPPED'

export interface LiveSamplingSample {
    symbol: string
    name: string
    category: string
    category_cn: string
}

export interface LiveSamplingResult {
    data_type: string
    data_type_label: string
    symbol: string
    symbol_name: string
    category: string
    status: LiveSamplingStatus
    status_label_cn: string
    traffic_light: 'green' | 'yellow' | 'red'
    actual_vendor: string
    primary_vendor: string
    fallback_vendor: string
    is_fallback: boolean
    latency_ms: number
    record_count: number
    unit: string
    unit_verified: boolean
    as_of: string
    error: string
    diagnosis: string
    rate_limit_risk: string
}

export interface LiveSamplingSummary {
    total_checks: number
    status_counts: Record<string, number>
    green_count: number
    yellow_count: number
    red_count: number
    skipped_count: number
    skipped_only: boolean
    fallback_triggered_count: number
    all_green: boolean
    has_failures: boolean
    has_warnings: boolean
    by_data_type: Record<string, any>
    overall_status: string
}

export interface LiveSamplingResponse {
    status: string
    has_report: boolean
    report_date: string
    generated_at: string
    env_gated: boolean
    samples: LiveSamplingSample[]
    results: LiveSamplingResult[]
    summary: LiveSamplingSummary
    runtime_tier_meta: RuntimeTierMeta
}

// [H-013] mandate_topic_heatmap
export interface TopicHeatPoint {
    date: string
    signal_count: number
    candidate_count: number
    unique_candidates: number
    policy_level: string
    policy_level_weight: number
    evidence_count: number
    overheat_flags: string[]
    lifecycle_state: string
    topic_status: string
    topic_status_label: string
    heat: number
}

export interface TopicHeatWindow {
    window_days: number
    window_label: string
    signal_count: number
    candidate_count: number
    unique_candidates: number
    evidence_count: number
    active_days: number
    max_policy_level: string
    max_policy_level_weight: number
}

export interface TopicHeatCandidate {
    symbol: string
    name: string
    company_role: string
    beneficiary_path: string[]
    mandate_score: number
    tier: string
    candidate_type: string
    latest_date: string
}

export interface TopicHeatmapEntry {
    topic: string
    description: string
    topic_status: string
    topic_status_label: string
    policy_level: string
    policy_level_weight: number
    is_left_side: boolean
    is_observe_only: boolean
    is_confirmed: boolean
    heat_curve: TopicHeatPoint[]
    windows: Record<string, TopicHeatWindow>
    heat_trend: string
    heat_trend_label: string
    state_change_label: string
    state_change_positive: boolean
    latest_date: string
    latest_signal_count: number
    peak_date: string
    peak_heat: number
    evidence_links: Array<{ title: string; source: string; date: string; url: string; source_level: string }>
    evidence_summary: string
    counter_evidence_gaps: string[]
    overheat_flags: string[]
    candidates: TopicHeatCandidate[]
    candidate_symbols: string[]
}

export interface TopicHeatmapResponse {
    status: string
    as_of: string
    window_days: number
    topics: TopicHeatmapEntry[]
    total_topics: number
    active_topics: number
    rising_topics: number
    cooling_topics: number
    left_side_topics: number
    observe_only_topics: number
    summary: Record<string, unknown>
    runtime_tier_meta: RuntimeTierMeta
}

// [H-015] mandate_daily_report
export interface MandateDailyCandidate {
    symbol: string
    name: string
    topic: string
    company_role: string
    candidate_type: string
    tier: string
    mandate_score: number
    latest_date: string
    entry_reason: string
    evidence_gaps: string[]
}

export interface MandateDailyReportResponse {
    status: string
    as_of: string
    generated_at: string
    rising_topics: Array<Record<string, unknown>>
    cooling_topics: Array<Record<string, unknown>>
    main_candidates: MandateDailyCandidate[]
    observation_candidates: MandateDailyCandidate[]
    entry_reasons: Array<Record<string, unknown>>
    exit_reasons: Array<Record<string, unknown>>
    evidence_gaps: Array<Record<string, unknown>>
    markdown: string
    source: string
    path?: string
    markdown_path?: string
    runtime_tier_meta: RuntimeTierMeta
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
    runtime_tier_meta: RuntimeTierMeta  // [PERF-001]
}

// [TF-OBS-001] tradeflow_observe_runner
export interface TradeFlowObserveRunResponse {
    status: string
    trade_date: string
    checked: number
    triggered: number
    invalidated: number
    waiting: number
    skipped: number
    signals_written: number
    errors: string[]
    skipped_reason: string
    run_time: string
    details: {
        symbol: string
        observe_state?: string
        current_price?: number
        trigger_reason?: string
        status?: string
        reason?: string
    }[]
    // [TF-OBS-005] observe_date_semantics — cross-date plan provenance
    plan_date?: string
    effective_trade_date?: string
    observe_date?: string
    non_trading_day_plan?: boolean
    next_trading_day_hint?: string
    is_view_trading_day?: boolean
}

// [TF-UX-001] tiered candidates
export interface TradeFlowTieredCandidatesResponse {
    status: string
    trade_date: string
    actionable: TradeFlowCandidateItem[]
    watch: TradeFlowCandidateItem[]
    scan: TradeFlowCandidateItem[]
    actionable_count: number
    watch_count: number
    scan_count: number
    summary_agg: TradeFlowSummary
    // [UI-012] tradeflow_focus_workspace — pool data for focus workspace
    main_candidates?: TradeFlowCandidateItem[]
    observation_candidates?: TradeFlowCandidateItem[]
    filtered_candidates?: Array<{ symbol: string; name: string; source: string; reason: string }>
    pool_counts?: Record<string, number>
    pool_gate_summary?: string
    // [H-014] mandate_concentration_gate — theme concentration payload
    concentration_summary?: {
        enabled?: boolean
        top_theme?: string
        top_theme_status?: string
        top_theme_main_symbols?: string[]
        weak_topics_downgraded?: string[]
        per_topic_cap_downgraded?: string[]
        headline?: string
        per_topic?: Record<string, {
            strength?: string
            strength_reason?: string
            admission_cap?: number
            candidate_count?: number
            admitted_count?: number
            admitted_symbols?: string[]
            lifecycle_state?: string
            topic_status_label?: string
            max_signal_count?: number
            evidence_gap_burden?: number
            symbols?: string[]
        }>
    }
}

// [TF-UX-003] post_market_review
export interface TradeFlowReviewGenerateResponse {
    status: string
    trade_date: string
    message: string
    data_status?: string  // [TF-REVIEW-002] review_date_mapping
    data_status_message?: string  // [TF-REVIEW-002] review_date_mapping
    // [TF-REVIEW-004] review_empty_diagnostics
    empty_reason?: string
    empty_reason_message?: string
    suggested_action?: string
    available_plan_dates?: string[]
    latest_plan_date?: string
    has_observe_signals?: boolean
    plan_date?: string
    effective_trade_date?: string
    review_date?: string
    review: {
        review_date: string
        candidate_date: string
        plan_date?: string  // [TF-REVIEW-002] review_date_mapping
        effective_trade_date?: string  // [TF-REVIEW-002] review_date_mapping
        data_status?: string  // [TF-REVIEW-002] review_date_mapping
        data_status_message?: string  // [TF-REVIEW-002] review_date_mapping
        total_candidates: number
        scored_candidates: number
        no_data_candidates: number
        overall_hit_count: number
        overall_miss_count: number
        overall_invalidated_count: number
        overall_hit_rate: number | null
        overall_false_positive_rate: number | null
        avg_next_day_return: number | null
        avg_day3_return: number | null
        avg_day5_return: number | null
        strategy_stats: Record<string, {
            strategy_tag: string
            total_candidates: number
            hit_count: number
            miss_count: number
            no_data_count: number
            invalidated_count: number
            hit_rate: number | null
            false_positive_rate: number | null
            avg_next_day_return: number | null
            avg_day3_return: number | null  // [TF-REVIEW-002] review_date_mapping
            avg_day5_return: number | null  // [TF-REVIEW-002] review_date_mapping
        }>
        tier_stats: Record<string, { total: number; hit: number; miss: number; no_data: number; invalidated: number }>
        common_removal_reasons: string[]
        suggestions: string[]
    } | null
}

// [UI-009] candidate_ta_plan_draft
// [TF-UI-011] candidate_research_entry — profile metadata added
export interface TradeFlowResearchPlanResponse {
    status: string
    trade_date: string
    symbol: string
    candidate_type: string
    research_queue: string
    research_intent: string
    horizon: string
    analysis_intent: string
    position_context: string
    runtime_profile: string
    profile_label: string  // [TF-UI-011]
    expected_latency: string  // [TF-UI-011]
    llm_allowed: boolean  // [TF-UI-011]
    requires_confirmation: boolean  // [TF-UI-011]
    cost_risk: string  // [TF-UI-011]
    enabled_modules: string[]
    required_evidence: string[]
    existing_evidence_coverage: number
    can_generate: boolean
    block_reason: string
    plan_markdown: string
    route_reason: string
}

// [TF-UI-011] candidate_research_entry
export interface CompanyOverviewResponse {
    status: string
    symbol: string
    name: string
    industry: string
    company_profile: string
    profile_available: boolean
    data_source: string
    error: string
    runtime_tier_meta: RuntimeTierMeta
}

// [UI-010] mandate_candidate_compare
export interface TradeFlowCompareResponse {
    status: string
    trade_date: string
    candidates: TradeFlowCandidateItem[]
    sort_by: string
    sort_order: string
    total: number
    runtime_tier_meta: RuntimeTierMeta
}

// [TF-PAPER-001] paper_trading_ledger
export interface PaperTradeItem {
    id: number
    symbol: string
    name: string
    trade_date: string
    plan_date: string
    candidate_type: string
    trigger_price: number | null
    invalid_price: number | null
    planned_amount: number
    status: string
    action_type: string
    action_price: number | null
    action_date: string
    confirmed: boolean
    note: string
    pnl: number
    pnl_pct: number
    observe_state: string
    close_price: number | null
    close_date: string
    close_reason: string
    created_at: string
    updated_at: string
}

// [TF-PAPER-001] paper_trading_ledger
export interface PaperLedgerSummary {
    total_trades: number
    tracking_count: number
    pending_count: number
    open_count: number
    closed_count: number
    observation_count: number
    invested: number
    realized_pnl: number
    unrealized_pnl: number
    total_pnl: number
    total_pnl_pct: number
    // [TF-RISK-001] paper_risk_budget
    risk_exposure: {
        principal: number
        invested: number
        remaining: number
        per_ticket_max: number
        per_ticket_min: number
        daily_new_today: number
        daily_new_max: number
        tracking_count: number
        max_concurrent_tracking: number
        budget_utilization_pct: number
    }
}

// [TF-PAPER-001] paper_trading_ledger
export interface PaperLedgerResponse {
    status: string
    principal: number
    cash_balance: number
    config: Record<string, unknown>
    trades: PaperTradeItem[]
    summary: PaperLedgerSummary
    runtime_tier_meta: RuntimeTierMeta
}

// [TF-PAPER-001] paper_trading_ledger
export interface PaperActionResponse {
    status: string
    message: string
    trade_id?: number
    planned_amount?: number
    cash_balance?: number
    pnl?: number
}

// [TF-PAPER-001] paper_trading_ledger
export interface PaperReviewResponse {
    status: string
    trade_date: string
    review: {
        total: number
        tracking: number
        pending: number
        open: number
        closed: number
        invalidated: number
        realized_pnl: number
        false_trigger_count: number
        untriggered_count: number
        invalidated_count: number
        review_note: string
        principal: number
        cash_balance: number
        total_pnl_pct: number
    }
    trades: PaperTradeItem[]
    runtime_tier_meta: RuntimeTierMeta
}

// [TRACK-006] add_to_observation — one-click add from candidate / TA report
export interface ObservationAddResponse {
    status: string
    action?: string  // "created" | "updated" | ""
    message: string
    symbol?: string
    item_id?: number
    item?: ObservationItemV2 & {
        last_reviewed_at?: string | null
    }
    runtime_tier_meta?: RuntimeTierMeta
}
