// [TF-UX-001] small_cap_trial_workbench
import { describe, expect, it } from 'vitest'

import type {
    TradeFlowCandidateItem,
    PaperLedgerResponse,
    PaperTradeItem,
} from '@/types'

import {
    groupMainCandidates,
    computeTrialBudgetView,
    computeCandidateRiskView,
    countPendingConfirmations,
    pickWhySelected,
    pickWhyNotMain,
    buildFixtureCandidate,
    DEFAULT_MAIN_VIEW_CAP,
    DEFAULT_TRIAL_PRINCIPAL,
} from '@/utils/tradeflowFocus'

// [TF-UX-001] small_cap_trial_workbench — 10-candidate fixture used to verify
// the default main view does not exceed 5 visible items (acceptance criterion).
// 5 actionable (pending+near+main) + 5 invalidated/expired = 10 total.
function buildTenCandidateFixture(): TradeFlowCandidateItem[] {
    return [
        // 2 pending confirm (triggered, awaiting user action)
        buildFixtureCandidate({ symbol: '600001.SH', name: '已触发甲', observe_state: 'TRIGGERED', composite_score: 88, tier: 'A' }),
        buildFixtureCandidate({ symbol: '600002.SH', name: '已触发乙', observe_state: 'TRIGGERED', composite_score: 82, tier: 'A' }),
        // 2 near trigger (already fired once, back to waiting OR tier A w/ trigger price)
        buildFixtureCandidate({ symbol: '600003.SH', name: '近触发甲', observe_state: 'WAITING', observe_trigger_count: 1, composite_score: 78, tier: 'B' }),
        buildFixtureCandidate({ symbol: '600004.SH', name: '近触发乙', observe_state: 'WAITING', observe_trigger_count: 0, tier: 'A', trigger_price: 10.5, composite_score: 75 }),
        // 1 plain main (waiting, no triggers yet)
        buildFixtureCandidate({ symbol: '600005.SH', name: '主候选甲', observe_state: 'WAITING', composite_score: 70, tier: 'B' }),
        // 5 invalidated/expired — must NOT appear in default main view
        buildFixtureCandidate({ symbol: '600006.SH', name: '已失效甲', observe_state: 'INVALIDATED', composite_score: 40 }),
        buildFixtureCandidate({ symbol: '600007.SH', name: '已失效乙', observe_state: 'INVALIDATED', composite_score: 39 }),
        buildFixtureCandidate({ symbol: '600008.SH', name: '已过期甲', observe_state: 'EXPIRED', composite_score: 38 }),
        buildFixtureCandidate({ symbol: '600009.SH', name: '已过期乙', observe_state: 'EXPIRED', composite_score: 37 }),
        buildFixtureCandidate({ symbol: '600010.SH', name: '已过期丙', observe_state: 'EXPIRED', composite_score: 36 }),
    ]
}


describe('[TF-UX-001] groupMainCandidates', () => {
    it('splits 10 candidates into the four focus groups with correct counts', () => {
        const all = buildTenCandidateFixture()
        const result = groupMainCandidates(all)

        expect(result.pending_confirm).toHaveLength(2)
        expect(result.near_trigger).toHaveLength(2)
        expect(result.main).toHaveLength(1)
        expect(result.invalidated).toHaveLength(5)
    })

    it('keeps the default main view (pending+near+main) at or below 5 of 10 candidates', () => {
        const all = buildTenCandidateFixture()
        const result = groupMainCandidates(all)

        // Acceptance criterion: "10 只候选 fixture 中默认主视图不超过 5 只"
        expect(result.main_view_count).toBeLessThanOrEqual(DEFAULT_MAIN_VIEW_CAP)
        expect(result.main_view_count).toBe(5)
        expect(result.main_view_truncated).toBe(false)
    })

    it('flags truncation when the main view exceeds the configured cap', () => {
        const all = buildTenCandidateFixture()
        // Add 2 more triggered candidates → pending_confirm becomes 4, total main view = 7
        all.push(buildFixtureCandidate({ symbol: '600011.SH', observe_state: 'TRIGGERED' }))
        all.push(buildFixtureCandidate({ symbol: '600012.SH', observe_state: 'TRIGGERED' }))

        const result = groupMainCandidates(all, 5)
        expect(result.main_view_count).toBe(7)
        expect(result.main_view_cap).toBe(5)
        expect(result.main_view_truncated).toBe(true)
    })

    it('excludes invalidated and expired candidates from the default main view', () => {
        const result = groupMainCandidates([
            buildFixtureCandidate({ symbol: 'A', observe_state: 'INVALIDATED' }),
            buildFixtureCandidate({ symbol: 'B', observe_state: 'EXPIRED' }),
            buildFixtureCandidate({ symbol: 'C', observe_state: 'WAITING' }),
        ])
        expect(result.main_view_count).toBe(1)
        expect(result.invalidated).toHaveLength(2)
    })

    it('never throws on null/undefined input', () => {
        const r1 = groupMainCandidates(null)
        const r2 = groupMainCandidates(undefined)
        expect(r1.main_view_count).toBe(0)
        expect(r2.main_view_count).toBe(0)
    })

    it('exposes a stable group ordering (pending → near → main) for the prompt area', () => {
        const result = groupMainCandidates(buildTenCandidateFixture())
        expect(result.groups.map(g => g.key)).toEqual(['pending_confirm', 'near_trigger', 'main'])
    })
})

describe('[TF-UX-001] computeTrialBudgetView', () => {
    function buildLedger(opts: {
        principal?: number
        per_ticket_max?: number
        max_concurrent_tracking?: number
        invested?: number
        budget_utilization_pct?: number
        tracking_count?: number
        trades?: PaperTradeItem[]
    }): PaperLedgerResponse {
        const principal = opts.principal ?? 5000
        const per_ticket_max = opts.per_ticket_max ?? 1000
        const max_concurrent_tracking = opts.max_concurrent_tracking ?? 5
        const invested = opts.invested ?? 0
        return {
            status: 'ok',
            principal,
            cash_balance: principal - invested,
            config: {},
            trades: opts.trades ?? [],
            summary: {
                total_trades: opts.trades?.length ?? 0,
                tracking_count: opts.tracking_count ?? 0,
                pending_count: 0,
                open_count: 0,
                closed_count: 0,
                observation_count: 0,
                invested,
                realized_pnl: 0,
                unrealized_pnl: 0,
                total_pnl: 0,
                total_pnl_pct: 0,
                risk_exposure: {
                    principal,
                    invested,
                    remaining: principal - invested,
                    per_ticket_max,
                    per_ticket_min: 0,
                    daily_new_today: 0,
                    daily_new_max: max_concurrent_tracking,
                    tracking_count: opts.tracking_count ?? 0,
                    max_concurrent_tracking,
                    budget_utilization_pct: opts.budget_utilization_pct ?? 0,
                },
            },
            runtime_tier_meta: {
                tier: 'fast',
                expected_latency: '<30s',
                llm_allowed: false,
                requires_confirmation: false,
                cost_risk: 'low',
            },
        }
    }

    it('returns an "enable trial" empty state when ledger is missing', () => {
        const view = computeTrialBudgetView(null)
        expect(view.ledger_missing).toBe(true)
        expect(view.principal).toBe(DEFAULT_TRIAL_PRINCIPAL)
        expect(view.per_ticket_budget).toBe(0)
        expect(view.primary_hint).toContain('5000 元试跑')
    })

    it('reports per-ticket budget and max occupancy for the prompt area', () => {
        const view = computeTrialBudgetView(buildLedger({ per_ticket_max: 1000, max_concurrent_tracking: 5 }))
        expect(view.per_ticket_budget).toBe(1000)
        expect(view.max_occupancy).toBe(5000)
        expect(view.per_ticket_occupancy_pct).toBeCloseTo(0.2, 5)
        expect(view.primary_hint).toContain('单票预算')
        expect(view.primary_hint).not.toMatch(/买入|卖出|加仓|减仓/)
    })

    it('flags capacity and surfaces pending confirmations', () => {
        const trades: PaperTradeItem[] = [
            { id: 1, symbol: 'A', confirmed: false, status: 'pending', planned_amount: 1000 } as PaperTradeItem,
            { id: 2, symbol: 'B', confirmed: false, status: 'observation', planned_amount: 1000 } as PaperTradeItem,
            { id: 3, symbol: 'C', confirmed: true, status: 'open', planned_amount: 1000 } as PaperTradeItem,
        ]
        const view = computeTrialBudgetView(buildLedger({ max_concurrent_tracking: 3, tracking_count: 3, trades }))
        expect(view.at_capacity).toBe(true)
        expect(view.pending_confirmation_count).toBe(2)
        // pending confirmations take priority over at-capacity messaging
        expect(view.primary_hint).toContain('待确认')
        expect(view.primary_hint).not.toMatch(/买入|卖出/)
    })

    it('never produces strong buy/sell wording in any state', () => {
        const cases = [
            null,
            buildLedger({}),
            buildLedger({ max_concurrent_tracking: 1, tracking_count: 1 }),
            buildLedger({ per_ticket_max: 0 }),
        ]
        for (const c of cases) {
            const view = computeTrialBudgetView(c)
            expect(view.primary_hint).not.toMatch(/买入|卖出|加仓|减仓|满仓|清仓/)
        }
    })
})

describe('[TF-UX-001] computeCandidateRiskView', () => {
    function ledger(): PaperLedgerResponse {
        return {
            status: 'ok',
            principal: 5000,
            cash_balance: 5000,
            config: {},
            trades: [],
            summary: {
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
                    principal: 5000,
                    invested: 0,
                    remaining: 5000,
                    per_ticket_max: 1000,
                    per_ticket_min: 0,
                    daily_new_today: 0,
                    daily_new_max: 5,
                    tracking_count: 0,
                    max_concurrent_tracking: 5,
                    budget_utilization_pct: 0,
                },
            },
            runtime_tier_meta: {
                tier: 'fast',
                expected_latency: '<30s',
                llm_allowed: false,
                requires_confirmation: false,
                cost_risk: 'low',
            },
        }
    }

    it('projects per-ticket budget occupancy when candidate is not tracked yet', () => {
        const budget = computeTrialBudgetView(ledger())
        const view = computeCandidateRiskView(buildFixtureCandidate({ symbol: '600001.SH' }), ledger(), budget)
        expect(view.already_tracked).toBe(false)
        expect(view.projected_amount).toBe(1000)
        expect(view.occupancy_pct).toBeCloseTo(0.2, 5)
        expect(view.hint).toContain('若加入试跑')
        expect(view.hint).not.toMatch(/买入|卖出/)
    })

    it('uses actual planned_amount when candidate is already tracked', () => {
        const lg = ledger()
        lg.trades = [{
            id: 7, symbol: '600001.SH', confirmed: true, status: 'open', planned_amount: 800,
        } as PaperTradeItem]
        const budget = computeTrialBudgetView(lg)
        const view = computeCandidateRiskView(buildFixtureCandidate({ symbol: '600001.SH' }), lg, budget)
        expect(view.already_tracked).toBe(true)
        expect(view.projected_amount).toBe(800)
        expect(view.occupancy_pct).toBeCloseTo(0.16, 5)
    })

    it('flags pending action when matched trade is unconfirmed', () => {
        const lg = ledger()
        lg.trades = [{
            id: 9, symbol: '600001.SH', confirmed: false, status: 'pending', planned_amount: 1000,
        } as PaperTradeItem]
        const budget = computeTrialBudgetView(lg)
        const view = computeCandidateRiskView(buildFixtureCandidate({ symbol: '600001.SH' }), lg, budget)
        expect(view.has_pending_action).toBe(true)
        expect(view.hint).toContain('待确认')
    })

    it('warns when adding would exceed concurrent tracking cap', () => {
        const lg = ledger()
        lg.summary.risk_exposure.max_concurrent_tracking = 2
        lg.summary.risk_exposure.tracking_count = 2
        const budget = computeTrialBudgetView(lg)
        const view = computeCandidateRiskView(buildFixtureCandidate({ symbol: '600999.SH' }), lg, budget)
        expect(view.would_exceed_cap).toBe(true)
        expect(view.hint).toContain('超出试跑上限')
    })
})

describe('[TF-UX-001] reason pickers', () => {
    it('pickWhySelected returns ranking_reasons first, falls back to action_tier_reason', () => {
        expect(pickWhySelected(buildFixtureCandidate({ ranking_reasons: ['政策强', '资金流入'] }))).toEqual(['政策强', '资金流入'])
        expect(pickWhySelected(buildFixtureCandidate({ action_tier_reason: 'tier reason only' }))).toEqual(['tier reason only'])
        expect(pickWhySelected(buildFixtureCandidate({}))).toEqual([])
    })

    it('pickWhyNotMain aggregates weakness/downgrade/tier reasons', () => {
        const c = buildFixtureCandidate({
            weakness_reasons: ['数据完整度低'],
            downgrade_reasons: ['过热'],
            action_tier_reason: 'tier-A only',
            missing_evidence_for_upgrade: ['主力资金', '龙虎榜'],
        })
        const reasons = pickWhyNotMain(c, 5)
        expect(reasons).toContain('数据完整度低')
        expect(reasons).toContain('过热')
        expect(reasons).toContain('tier-A only')
    })

    it('countPendingConfirmations handles empty and mixed cases', () => {
        expect(countPendingConfirmations(null)).toBe(0)
        expect(countPendingConfirmations([])).toBe(0)
        const trades = [
            { confirmed: false, status: 'pending' },
            { confirmed: false, status: 'observation' },
            { confirmed: true, status: 'open' },
            { confirmed: false, status: 'closed' },
        ] as PaperTradeItem[]
        expect(countPendingConfirmations(trades)).toBe(2)
    })
})

describe('[TF-UX-001] fixture sanity', () => {
    it('buildFixtureCandidate returns a valid TradeFlowCandidateItem without casting', () => {
        const c = buildFixtureCandidate({ symbol: '688001.SH' })
        expect(c.symbol).toBe('688001.SH')
        // ensure required fields used by the focus workspace are present
        expect(c.observe_state).toBeDefined()
        expect(c.ranking_reasons).toEqual([])
        expect(c.weakness_reasons).toEqual([])
    })
})
