// [TF-UX-001] small_cap_trial_workbench
// Pure helpers used by the TradeFlow focus workspace. Extracted into a standalone
// module so smoke tests can verify the default small-cap trial view without
// having to mount React components or hit the backend.
//
// The backend candidate scoring is intentionally untouched — these helpers only
// reshape what the frontend shows by default and compute the risk-budget summary
// that surfaces in the "5000 元试跑" prompt area.

import type {
    TradeFlowCandidateItem,
    PaperLedgerResponse,
    PaperLedgerSummary,
    PaperTradeItem,
} from '@/types'

// [TF-UX-001] small_cap_trial_workbench — group keys for the default focus view.
// The default workspace shows three actionable groups plus a collapsed weak
// observation pool, so users never face a flat list of candidates.
export type FocusGroupKey = 'pending_confirm' | 'near_trigger' | 'main' | 'invalidated'

// The `groups` array in FocusGroupResult only contains the three keys rendered
// in the default main view (invalidated has its own collapsed section).
export type MainGroupKey = 'pending_confirm' | 'near_trigger' | 'main'

export interface FocusGroup {
    key: MainGroupKey
    label: string
    hint: string
    items: TradeFlowCandidateItem[]
}

export interface FocusGroupResult {
    pending_confirm: TradeFlowCandidateItem[]
    near_trigger: TradeFlowCandidateItem[]
    main: TradeFlowCandidateItem[]
    invalidated: TradeFlowCandidateItem[]
    groups: FocusGroup[]
    /** Main view = pending_confirm + near_trigger + main (i.e. actionable candidates). */
    main_view_count: number
    /** Hard cap for the default main view — backend may keep main_candidates loose. */
    main_view_cap: number
    /** True when the main view exceeds the cap and would need trimming. */
    main_view_truncated: boolean
}

export const DEFAULT_MAIN_VIEW_CAP = 5

// [TF-UX-001] small_cap_trial_workbench — split main candidates into the three
// groups required by the task spec. The backend already filters
// `main_candidates`, so this function only classifies by observe state.
//
//  - pending_confirm: observe_state=TRIGGERED, waiting for the user to add to the
//    paper ledger or skip.
//  - near_trigger: observe_state=WAITING but the candidate has already fired at
//    least once (observe_trigger_count>0), or tier A with a trigger price — i.e.
//    close enough that the user should watch it this session.
//  - main: the rest of the actionable main candidates that are still waiting for
//    a trigger.
//  - invalidated: EXPIRED or INVALIDATED — kept visible so the user understands
//    why a previously main candidate is no longer actionable, but never in the
//    default main view.
export function groupMainCandidates(
    mainCandidates: TradeFlowCandidateItem[] | null | undefined,
    cap: number = DEFAULT_MAIN_VIEW_CAP,
): FocusGroupResult {
    const items = mainCandidates ?? []
    const pending_confirm: TradeFlowCandidateItem[] = []
    const near_trigger: TradeFlowCandidateItem[] = []
    const main: TradeFlowCandidateItem[] = []
    const invalidated: TradeFlowCandidateItem[] = []

    for (const c of items) {
        const state = (c.observe_state || '').toUpperCase()
        if (state === 'TRIGGERED') {
            pending_confirm.push(c)
        } else if (state === 'INVALIDATED' || state === 'EXPIRED') {
            invalidated.push(c)
        } else if ((c.observe_trigger_count ?? 0) > 0) {
            near_trigger.push(c)
        } else if (state === 'WAITING' && (c.tier || '').toUpperCase() === 'A' && c.trigger_price != null) {
            near_trigger.push(c)
        } else {
            main.push(c)
        }
    }

    // Stable ordering inside each group: composite_score desc, then symbol asc.
    const byScoreDesc = (a: TradeFlowCandidateItem, b: TradeFlowCandidateItem) => {
        const sa = a.composite_score ?? 0
        const sb = b.composite_score ?? 0
        if (sa !== sb) return sb - sa
        return a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : 0
    }
    pending_confirm.sort(byScoreDesc)
    near_trigger.sort(byScoreDesc)
    main.sort(byScoreDesc)
    invalidated.sort(byScoreDesc)

    const groups: FocusGroup[] = [
        {
            key: 'pending_confirm',
            label: '待确认',
            hint: '已触发，等待人工确认是否加入试跑',
            items: pending_confirm,
        },
        {
            key: 'near_trigger',
            label: '接近触发',
            hint: '已有点火信号或 A 级带触发价，建议盘中重点盯',
            items: near_trigger,
        },
        {
            key: 'main',
            label: '主候选',
            hint: '今日优先研究，等待触发',
            items: main,
        },
    ]

    const main_view_count = pending_confirm.length + near_trigger.length + main.length
    const main_view_truncated = main_view_count > cap

    return {
        pending_confirm,
        near_trigger,
        main,
        invalidated,
        groups,
        main_view_count,
        main_view_cap: cap,
        main_view_truncated,
    }
}

// [TF-UX-001] small_cap_trial_workbench — risk budget summary for the "5000 元
// 试跑" prompt area. All textual hints avoid strong buy/sell wording per the
// task constraint.
export interface TrialBudgetView {
    principal: number
    cash_balance: number
    invested: number
    remaining: number
    /** Per-ticket budget — single name occupancy if the user adds it to the trial. */
    per_ticket_budget: number
    /** Max occupancy = max concurrent tracking tickets * per-ticket budget. */
    max_occupancy: number
    /** Already occupied tracking count. */
    tracking_count: number
    /** Max concurrent tracking tickets allowed by config. */
    max_concurrent_tracking: number
    /** Pending paper actions awaiting user confirmation. */
    pending_confirmation_count: number
    /** Budget utilization 0..1. */
    utilization_pct: number
    /** True when the trial is at capacity (cannot add more tickets). */
    at_capacity: boolean
    /** Default single-ticket occupancy ratio 0..1 (per_ticket / principal). */
    per_ticket_occupancy_pct: number
    /** Whether the ledger is missing — used to render an "enable trial" empty state. */
    ledger_missing: boolean
    /** Soft hint shown in the prompt area — no strong buy/sell wording. */
    primary_hint: string
}

export const DEFAULT_TRIAL_PRINCIPAL = 5000

export function computeTrialBudgetView(
    ledger: PaperLedgerResponse | null | undefined,
): TrialBudgetView {
    if (!ledger) {
        return {
            principal: DEFAULT_TRIAL_PRINCIPAL,
            cash_balance: DEFAULT_TRIAL_PRINCIPAL,
            invested: 0,
            remaining: DEFAULT_TRIAL_PRINCIPAL,
            per_ticket_budget: 0,
            max_occupancy: 0,
            tracking_count: 0,
            max_concurrent_tracking: 0,
            pending_confirmation_count: 0,
            utilization_pct: 0,
            at_capacity: false,
            per_ticket_occupancy_pct: 0,
            ledger_missing: true,
            primary_hint: '尚未开启 5000 元试跑，可在「模拟账本」开启后回到主工作台查看风险预算占用。',
        }
    }

    const summary: PaperLedgerSummary = ledger.summary ?? {
        total_trades: 0,
        tracking_count: 0,
        pending_count: 0,
        open_count: 0,
        closed_count: 0,
        observation_count: 0,
        invested: 0,
        realized_pnl: 0,
        unrealized_pnl: 0,
        total_pnl: 0,
        total_pnl_pct: 0,
        risk_exposure: {
            principal: ledger.principal ?? DEFAULT_TRIAL_PRINCIPAL,
            invested: 0,
            remaining: ledger.principal ?? DEFAULT_TRIAL_PRINCIPAL,
            per_ticket_max: 0,
            per_ticket_min: 0,
            daily_new_today: 0,
            daily_new_max: 0,
            tracking_count: 0,
            max_concurrent_tracking: 0,
            budget_utilization_pct: 0,
        },
    }
    const re = summary.risk_exposure
    const principal = re.principal || ledger.principal || DEFAULT_TRIAL_PRINCIPAL
    const per_ticket_budget = re.per_ticket_max || 0
    const max_concurrent_tracking = re.max_concurrent_tracking || 0
    const max_occupancy = per_ticket_budget * max_concurrent_tracking
    const utilization_pct = re.budget_utilization_pct ?? 0
    const tracking_count = re.tracking_count ?? summary.tracking_count ?? 0
    const pending_confirmation_count = countPendingConfirmations(ledger.trades)
    const at_capacity = tracking_count >= max_concurrent_tracking && max_concurrent_tracking > 0
    const per_ticket_occupancy_pct = principal > 0 ? per_ticket_budget / principal : 0

    let primary_hint: string
    if (pending_confirmation_count > 0) {
        primary_hint = `有 ${pending_confirmation_count} 个候选已触发待确认，先确认后再考虑新增。`
    } else if (at_capacity) {
        primary_hint = `试跑占用已达上限（${tracking_count}/${max_concurrent_tracking} 只），需先释放名额才能新增。`
    } else if (tracking_count === 0) {
        primary_hint = `今日试跑额度空闲：单票预算 ¥${per_ticket_budget.toFixed(0)}，最多同时跟踪 ${max_concurrent_tracking} 只。`
    } else {
        const slotsLeft = Math.max(0, max_concurrent_tracking - tracking_count)
        primary_hint = `已跟踪 ${tracking_count}/${max_concurrent_tracking} 只，剩余 ${slotsLeft} 个名额，单票预算 ¥${per_ticket_budget.toFixed(0)}。`
    }

    return {
        principal,
        cash_balance: ledger.cash_balance ?? principal,
        invested: re.invested ?? summary.invested ?? 0,
        remaining: re.remaining ?? principal - (re.invested ?? 0),
        per_ticket_budget,
        max_occupancy,
        tracking_count,
        max_concurrent_tracking,
        pending_confirmation_count,
        utilization_pct,
        at_capacity,
        per_ticket_occupancy_pct,
        ledger_missing: false,
        primary_hint,
    }
}

// [TF-UX-001] small_cap_trial_workbench — pending confirmation = triggered paper
// entries the user has not confirmed yet. These surface as "待确认动作" in the
// prompt area without strong buy/sell wording.
export function countPendingConfirmations(trades: PaperTradeItem[] | null | undefined): number {
    if (!trades || trades.length === 0) return 0
    return trades.filter(t => {
        const st = (t.status || '').toLowerCase()
        return (!t.confirmed) && (st === 'pending' || st === 'waiting' || st === 'observation')
    }).length
}

// [TF-UX-001] small_cap_trial_workbench — projected risk occupancy for a single
// candidate card. If the candidate is already in the paper ledger we return its
// actual planned_amount; otherwise we return what would be occupied if the user
// added it at the default per-ticket budget.
export interface CandidateRiskView {
    /** Symbol of the matching paper trade, if any. */
    matched_trade_id: number | null
    /** Actual or projected amount that would be occupied. */
    projected_amount: number
    /** Ratio of projected_amount / principal, 0..1. */
    occupancy_pct: number
    /** True when the candidate is already tracked in the paper ledger. */
    already_tracked: boolean
    /** True when the candidate has an unconfirmed pending action. */
    has_pending_action: boolean
    /** True when adding this candidate would exceed the concurrent tracking cap. */
    would_exceed_cap: boolean
    /** Short, soft wording (no buy/sell) explaining the occupancy. */
    hint: string
}

export function computeCandidateRiskView(
    candidate: TradeFlowCandidateItem,
    ledger: PaperLedgerResponse | null | undefined,
    budget: TrialBudgetView,
): CandidateRiskView {
    const matched = ledger?.trades?.find(t => t.symbol === candidate.symbol) ?? null
    const projected_amount = matched?.planned_amount ?? budget.per_ticket_budget
    const occupancy_pct = budget.principal > 0 ? projected_amount / budget.principal : 0
    const already_tracked = !!matched
    const has_pending_action = !!matched && !matched.confirmed && ['pending', 'waiting', 'observation'].includes((matched.status || '').toLowerCase())
    const would_exceed_cap = !already_tracked && budget.max_concurrent_tracking > 0 && budget.tracking_count >= budget.max_concurrent_tracking

    let hint: string
    if (already_tracked) {
        hint = has_pending_action
            ? `已跟踪，待确认动作（占用 ¥${projected_amount.toFixed(0)} / ${(occupancy_pct * 100).toFixed(0)}%）`
            : `已跟踪，占用 ¥${projected_amount.toFixed(0)} / ${(occupancy_pct * 100).toFixed(0)}%`
    } else if (would_exceed_cap) {
        hint = `若加入将超出试跑上限（${budget.max_concurrent_tracking} 只），需先释放名额`
    } else if (budget.ledger_missing) {
        hint = '尚未开启试跑，无风险预算数据'
    } else {
        hint = `若加入试跑，占用 ¥${projected_amount.toFixed(0)} / ${(occupancy_pct * 100).toFixed(0)}%`
    }

    return {
        matched_trade_id: matched?.id ?? null,
        projected_amount,
        occupancy_pct,
        already_tracked,
        has_pending_action,
        would_exceed_cap,
        hint,
    }
}

// [TF-UX-001] small_cap_trial_workbench — pick the "why selected" reasons for a
// main candidate card. Falls back to action_tier_reason when ranking_reasons is
// empty so the card never shows an empty explanations block.
export function pickWhySelected(c: TradeFlowCandidateItem, limit = 2): string[] {
    const reasons = (c.ranking_reasons && c.ranking_reasons.length > 0)
        ? c.ranking_reasons
        : (c.action_tier_reason ? [c.action_tier_reason] : [])
    return reasons.slice(0, limit)
}

// [TF-UX-001] small_cap_trial_workbench — pick the "why not promoted to main"
// reasons. Used by observation pool cards so users understand why a weak
// candidate did not make the cut.
export function pickWhyNotMain(c: TradeFlowCandidateItem, limit = 2): string[] {
    const reasons: string[] = []
    if (c.weakness_reasons && c.weakness_reasons.length > 0) {
        reasons.push(...c.weakness_reasons)
    }
    if (c.downgrade_reasons && c.downgrade_reasons.length > 0) {
        reasons.push(...c.downgrade_reasons)
    }
    if (c.action_tier_reason && !reasons.includes(c.action_tier_reason)) {
        reasons.push(c.action_tier_reason)
    }
    if (c.tier_reason && !reasons.includes(c.tier_reason)) {
        reasons.push(c.tier_reason)
    }
    if (reasons.length === 0 && c.missing_evidence_for_upgrade && c.missing_evidence_for_upgrade.length > 0) {
        reasons.push(`证据缺口: ${c.missing_evidence_for_upgrade.slice(0, 2).join('、')}`)
    }
    return reasons.slice(0, limit)
}

// [TF-UX-001] small_cap_trial_workbench — fixture builder used by smoke tests.
// Keeps the test fixtures in this module so other tests can import the same
// 10-candidate fixture referenced by the acceptance criteria ("10 只候选 fixture
// 中默认主视图不超过 5 只").
export interface FixtureCandidateOptions {
    symbol?: string
    name?: string
    observe_state?: string
    observe_trigger_count?: number
    tier?: string
    trigger_price?: number | null
    invalid_price?: number | null
    composite_score?: number
    score?: number
    trade_priority_score?: number
    candidate_type?: string
    action_tier?: string
    action_tier_reason?: string
    ranking_reasons?: string[]
    weakness_reasons?: string[]
    downgrade_reasons?: string[]
    missing_evidence_for_upgrade?: string[]
}

export function buildFixtureCandidate(opts: FixtureCandidateOptions = {}): TradeFlowCandidateItem {
    const symbol = opts.symbol ?? '000001.SZ'
    return {
        symbol,
        name: opts.name ?? `fixture-${symbol}`,
        tier: opts.tier ?? 'B',
        composite_score: opts.composite_score ?? 50,
        score: opts.score ?? 50,
        strategy_tags: [],
        primary_strategy: 'PULLBACK_SUPPORT',
        trigger_price: opts.trigger_price ?? null,
        support_price: null,
        invalid_price: opts.invalid_price ?? null,
        need_deep_ta: false,
        action: 'WAIT_TRIGGER',
        reason: '',
        observe_state: opts.observe_state ?? 'WAITING',
        observe_trigger_count: opts.observe_trigger_count ?? 0,
        observe_first_trigger_time: '',
        tradeflow_data_completeness: 0.6,
        missing_data_fields: [],
        data_completeness: 0.6,
        missing_evidence: [],
        game_balance: '',
        bull_case: '',
        bear_case: '',
        policy_case: '',
        fund_flow_case: '',
        why_deep_ta: '',
        why_not_deep_ta: '',
        risk_flags: [],
        policy_tags: [],
        version_score: 0,
        narrative_score: 0,
        fund_flow_anomaly_score: 0,
        fund_flow_anomaly_tags: [],
        fund_flow_unit_verified: true,
        evidence_gate_applied: false,
        deep_ta_status: '',
        deep_ta_dispatch_reason: '',
        deep_ta_model: '',
        source: 'fixture',
        universe_sources: [],
        resonance_count: 0,
        ta_budget_priority: 0,
        tier_reason: '',
        missing_evidence_for_upgrade: opts.missing_evidence_for_upgrade ?? [],
        candidate_type: opts.candidate_type ?? 'TECH_TRADE',
        mandate_score: 0,
        ambush_score: 0,
        mandate_topic: '',
        company_role: '',
        beneficiary_path: [],
        candidate_type_reason: '',
        deep_ta_route: '',
        research_queue: '',
        research_intent: '',
        research_route_reason: '',
        watchlist_note: '',
        watchlist_note_suggested: '',
        watchlist_topic: '',
        watchlist_benefit_score: 0,
        watchlist_consensus_score: 0,
        watchlist_evidence_gap: [],
        action_tier: opts.action_tier ?? 'watch',
        trade_priority_score: opts.trade_priority_score ?? 30,
        action_tier_reason: opts.action_tier_reason ?? '',
        technical_score: 0,
        policy_score: 0,
        fund_flow_score: 0,
        event_score: 0,
        risk_penalty_score: 0,
        data_quality_score: 0,
        ranking_reasons: opts.ranking_reasons ?? [],
        weakness_reasons: opts.weakness_reasons ?? [],
        counter_evidence: [],
        overheat_flags: [],
        downgrade_reasons: opts.downgrade_reasons ?? [],
        what_would_change_mind: [],
        topic_lifecycle_state: '',
        topic_lifecycle_reason: '',
        topic_last_signal_date: '',
        topic_signal_count: 0,
        contradiction_level: '',
        contradiction_items: [],
        blocking_evidence_gaps: [],
        next_verification_steps: [],
        local_knowledge_score: 0,
        knowledge_hit_count: 0,
        local_knowledge_summary: '',
        local_knowledge_detail: {},
        needs_tree_work_research: false,
        // [KB-011] knowledge_contract_ui — research attention defaults
        // (NORMAL_NO_DATA semantics: zero/empty, never an error).
        research_attention_score: 0,
        research_attention_effective_score: 0,
        research_attention_overheat_penalty: 0,
        research_attention_summary: '',
        research_attention_detail: {},
        knowledge_theme_count: 0,
        created_at: '',
        updated_at: '',
    }
}
