// [UI-005] tradeflow_review_page
// [H-005] mandate_radar_ui
// [TA-UI-001] analysis_console_horizon_intent
// [TF-P0-002] tradeflow_pool_split
// [TF-UX-001] tiered candidates
// [TF-UX-002] auto refresh + A-stock colors
// [TF-UX-003] post_market_review
// [TF-UX-004] trade_priority_score
// [UI-012] tradeflow_focus_workspace
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Target, Loader2, AlertCircle, Calendar, Filter, Eye, RefreshCw, ListOrdered, ClipboardList, BarChart3, Activity, Search, FilterX, ChevronDown, ChevronRight, Zap, Clock, GitCompare, Wallet, TrendingUp, ShieldAlert, Lightbulb, Eye as EyeIcon, Flame, Crosshair } from 'lucide-react'
import { api } from '@/services/api'
import {
    RUNTIME_TIER_LABELS,
} from '@/types'
import type {
    TradeFlowCandidateItem,
    TradeFlowSummary,
    TradeFlowDataHealthResponse,
    DataHealthSource,
    DataHealthStatus,
    TradeFlowObserveItem,
    TradeFlowObserveResponse,
    TradeFlowObserveRunResponse,
    TradeFlowTAQueueItem,
    TradeFlowTAQueueResponse,
    TradeFlowReviewResponse,
    TradeFlowFilteredResponse,
    TradeFlowFilteredItem,
    TradeFlowTieredCandidatesResponse,
    PaperLedgerResponse,
    SourceFreshnessResponse,
    SourceFreshnessEntry,
    LiveSamplingResponse,
    LiveSamplingResult,
    TopicHeatmapResponse,
    TopicHeatmapEntry,
    MandateDailyReportResponse,
} from '@/types'
import TradeFlowCandidateDrawer from '@/components/TradeFlowCandidateDrawer'
// [TF-UX-001] small_cap_trial_workbench — focus workspace helpers (grouping, risk budget)
import {
    groupMainCandidates,
    computeTrialBudgetView,
    computeCandidateRiskView,
    pickWhySelected,
    pickWhyNotMain,
    DEFAULT_MAIN_VIEW_CAP,
} from '@/utils/tradeflowFocus'
// [TF-OBS-006] observe_refresh_visual_semantics — A-share red/green pure helpers
import {
    computeObserveVisual,
    formatLastRefreshTime,
    shouldShowNonMarketBanner,
    OBSERVE_LEGEND,
} from '@/utils/observeVisuals'

type TabKey = 'candidates' | 'watch-pool' | 'observe' | 'ta-queue' | 'review' | 'filtered' | 'data-health' | 'compare' | 'paper-ledger' | 'topic-heatmap'

function todayStr(): string {
    return new Date().toISOString().slice(0, 10)
}

function tierBadgeClass(tier: string): string {
    switch (tier) {
        case 'A': return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
        case 'B': return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
        case 'C': return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
        default: return 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300'
    }
}

function actionLabel(action: string): { text: string; cls: string } {
    switch (action) {
        case 'NEED_DEEP_TA': return { text: '需深度TA', cls: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300' }
        case 'WAIT_TRIGGER': return { text: '等触发', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' }
        default: return { text: '观察', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300' }
    }
}

// [TF-OBS-002] observe_auto_run — A-share colors: red=up/triggered, green=down/invalidated, gray=waiting
function observeStateLabel(state: string): { text: string; cls: string } {
    switch (state) {
        case 'TRIGGERED': return { text: '已触发', cls: 'text-red-600 dark:text-red-400' }
        case 'EXPIRED': return { text: '已过期', cls: 'text-amber-600 dark:text-amber-400' }
        case 'INVALIDATED': return { text: '已失效', cls: 'text-emerald-600 dark:text-emerald-400' }
        default: return { text: '等待中', cls: 'text-slate-500 dark:text-slate-400' }
    }
}

// [TF-OBS-002] observe_auto_run — A-share colors: red=up/triggered, green=down/invalidated
function observeStateBg(state: string): string {
    switch (state) {
        case 'TRIGGERED': return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
        case 'EXPIRED': return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
        case 'INVALIDATED': return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
        default: return 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
    }
}

function taStatusLabel(status: string): { text: string; cls: string } {
    switch (status) {
        case 'running': return { text: '运行中', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' }
        case 'completed': return { text: '已完成', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
        case 'failed': return { text: '失败', cls: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' }
        case 'skipped': return { text: '已跳过', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' }
        default: return { text: '待处理', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300' }
    }
}

function fmtPrice(v: number | null): string {
    return v != null ? v.toFixed(2) : '-'
}

function candidateTypeLabel(ct: string): { text: string; cls: string } {  // [H-005] mandate_radar_ui
    switch (ct) {
        case 'POLICY_AMBUSH': return { text: '昊天左侧', cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300' }
        case 'POLICY_CONFIRM': return { text: '政策确认', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' }
        case 'TECH_TRADE': return { text: '技术交易', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300' }
        case 'EVENT_WATCH': return { text: '事件观察', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' }
        case 'PSEUDO_POLICY': return { text: '伪政策', cls: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300' }
        case 'OVERHEATED_AVOID': return { text: '过热规避', cls: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' }
        case 'UNCLASSIFIED_DATA_GAP': return { text: '证据缺口', cls: 'bg-gray-100 text-gray-700 dark:bg-gray-900/40 dark:text-gray-300' }  // [TF-P0-002]
        default: return { text: ct || '未分类', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' }
    }
}

// [TF-UX-004] action tier badge
function actionTierBadge(tier: string): { text: string; cls: string } {
    switch (tier) {
        case 'actionable': return { text: '今日可操作', cls: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' }
        case 'watch': return { text: '重点观察', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' }
        case 'scan': return { text: '扫描', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' }
        default: return { text: tier || '未分类', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' }
    }
}

// [TF-OBS-006] observe_refresh_visual_semantics
// A-share colors are now sourced from @/utils/observeVisuals so the semantics
// (red=near trigger/strong, green/gray=weak) are unit-tested and consistent
// with the legend. This thin wrapper keeps the legacy call sites working.
function priceDistanceColor(
    currentPrice: number | null,
    triggerPrice: number | null,
    invalidPrice: number | null,
    nearTrigger?: boolean,
    triggerDistancePct?: number | null,
): { text: string; cls: string; label: string } {
    return computeObserveVisual({ currentPrice, triggerPrice, invalidPrice, nearTrigger, triggerDistancePct })
}

// [TF-P0-002] tradeflow_pool_split — pool tabs
type PoolKey = 'all' | 'haotian' | 'policy' | 'tech' | 'event' | 'gap'

const POOL_TABS: { key: PoolKey; label: string }[] = [
    { key: 'all', label: '全部' },
    { key: 'haotian', label: '昊天左侧' },
    { key: 'policy', label: '政策确认' },
    { key: 'tech', label: '短线技术' },
    { key: 'event', label: '事件观察' },
    { key: 'gap', label: '证据缺口' },
]

interface SummaryCardsProps {
    summary: TradeFlowSummary
    dataHealth: TradeFlowDataHealthResponse | null
}

function SummaryCards({ summary, dataHealth }: SummaryCardsProps) {
    const cards = [
        { label: '总候选', value: summary.total_candidates, color: 'text-slate-900 dark:text-slate-100' },
        { label: 'A级', value: summary.tier_a_count, color: 'text-emerald-600 dark:text-emerald-400' },
        { label: 'B级', value: summary.tier_b_count, color: 'text-amber-600 dark:text-amber-400' },
        { label: 'C级', value: summary.tier_c_count, color: 'text-red-600 dark:text-red-400' },
        { label: '需深度TA', value: summary.need_deep_ta_count, color: 'text-purple-600 dark:text-purple-400' },
        { label: '平均完整度', value: `${(summary.avg_completeness * 100).toFixed(1)}%`, color: 'text-blue-600 dark:text-blue-400' },
    ]

    return (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {cards.map(c => (
                <div key={c.label} className="card p-4">
                    <div className="text-xs text-slate-500 dark:text-slate-400">{c.label}</div>
                    <div className={`mt-1 text-2xl font-semibold tabular-nums ${c.color}`}>{c.value}</div>
                </div>
            ))}
            {dataHealth && (
                <div className="card p-4 sm:col-span-3 lg:col-span-6">
                    <div className="text-xs text-slate-500 dark:text-slate-400">数据源状态</div>
                    <div className="mt-1 flex flex-wrap gap-2">
                        {dataHealth.sources.map(s => {
                            const derived = deriveHealthStatus(s)
                            const badge = healthStatusBadge(derived)
                            return (
                                <span
                                    key={s.name}
                                    className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${badge.cls}`}
                                >
                                    <span className={`h-1.5 w-1.5 rounded-full ${badge.dot}`} />
                                    {s.name} ({s.record_count})
                                </span>
                            )
                        })}
                    </div>
                </div>
            )}
        </div>
    )
}

function CompletenessBar({ value }: { value: number }) {
    return (
        <div className="flex items-center gap-1.5">
            <div className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                <div
                    className="h-full rounded-full bg-blue-500"
                    style={{ width: `${Math.min(100, value * 100)}%` }}
                />
            </div>
            <span className="tabular-nums text-xs text-slate-500">{(value * 100).toFixed(0)}%</span>
        </div>
    )
}

// [TF-OBS-002] observe_auto_run
// [TF-OBS-004] observe_refresh_alert_queue — grouping + countdown
function ObserveRow({ item }: { item: TradeFlowObserveItem }) {
    const st = observeStateLabel(item.observe_state)
    const distInfo = priceDistanceColor(item.current_price, item.trigger_price, item.invalid_price, item.near_trigger, item.trigger_distance_pct)
    const explain = item.trigger_explain
    return (
        <tr
            key={item.symbol}
            className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
        >
            <td className="px-4 py-2.5 font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{item.symbol}</td>
            <td className="max-w-[120px] truncate px-4 py-2.5 text-slate-700 dark:text-slate-300">{item.name || '--'}</td>
            <td className="px-4 py-2.5">
                <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${observeStateBg(item.observe_state)}`}>
                    {item.observe_state === 'WAITING' && <Eye className="h-3 w-3" />}
                    {st.text}
                </span>
            </td>
            <td className="px-4 py-2.5">
                {(() => {
                    const ps = item.paper_status || ''
                    if (ps === 'pending') return <span className="inline-flex items-center gap-1 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">待确认</span>
                    if (ps === 'tracking') return <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-700 dark:text-slate-300">已加入试跑</span>
                    if (ps === 'open') return <span className="inline-flex items-center gap-1 rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-300">持仓中</span>
                    if (ps === 'closed') return <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-500 dark:bg-slate-700 dark:text-slate-400">已平仓</span>
                    if (ps === 'invalidated') return <span className="inline-flex items-center gap-1 rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">已失效</span>
                    if (ps === 'observation') return <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-400 dark:bg-slate-700 dark:text-slate-500">观察降级</span>
                    return <span className="text-xs text-slate-300 dark:text-slate-600">—</span>
                })()}
            </td>
            <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{fmtPrice(item.trigger_price)}</td>
            <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{fmtPrice(item.invalid_price)}</td>
            <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">
                {item.current_price != null ? item.current_price.toFixed(2) : <span className="text-xs text-slate-400">实时行情不可用</span>}
            </td>
            <td className="px-4 py-2.5 tabular-nums">
                <span className={distInfo.cls} title={explain?.why_triggered || explain?.why_not || ''}>{distInfo.text}</span>
            </td>
            <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{item.observe_trigger_count}</td>
            <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">{item.observe_first_trigger_time || '-'}</td>
            <td className="max-w-[200px] truncate px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400" title={item.trigger_reason || explain?.why_triggered || explain?.why_not || ''}>
                {explain?.how_far_off || item.trigger_reason || '-'}
            </td>
        </tr>
    )
}

function ObserveGroup({ title, subtitle, items, accent }: { title: string; subtitle?: string; items: TradeFlowObserveItem[]; accent: string }) {
    if (items.length === 0) return null
    return (
        <div className="border-b border-slate-100 last:border-0 dark:border-slate-800">
            <div className="flex items-center gap-2 bg-slate-50/60 px-4 py-2 dark:bg-slate-800/40">
                <span className={`inline-block h-2 w-2 rounded-full ${accent}`} />
                <span className="text-xs font-semibold text-slate-700 dark:text-slate-200">{title}</span>
                <span className="text-xs text-slate-400">{items.length} 条</span>
                {subtitle && <span className="text-[11px] text-slate-400">{subtitle}</span>}
            </div>
            <table className="w-full text-sm">
                <tbody>
                    {items.map(item => <ObserveRow key={item.symbol} item={item} />)}
                </tbody>
            </table>
        </div>
    )
}

function ObserveTable({ items, onRun, running, runResult, lastCheckTime, observeReason, observeAutoRun, lastObservedAt,
    refreshIntervalSeconds, isMarketHours, isTradingDay, nearTriggerCount, pendingCount, nextRefreshIn, autoRefreshActive,
    planDate, effectiveTradeDate, nonTradingDayPlan, nextTradingDayHint,
    onGoToCandidates, onGoToObservationWarehouse }: {  // [UI-013] tradeflow_empty_state_cta
    items: TradeFlowObserveItem[]
    onRun: () => void
    running: boolean
    runResult: TradeFlowObserveRunResponse | null
    lastCheckTime: string | null
    observeReason: string
    observeAutoRun: boolean
    lastObservedAt: string
    refreshIntervalSeconds: number  // [TF-OBS-004]
    isMarketHours: boolean          // [TF-OBS-004]
    isTradingDay: boolean           // [TF-OBS-004]
    nearTriggerCount: number        // [TF-OBS-004]
    pendingCount: number            // [TF-OBS-004]
    nextRefreshIn: number | null    // [TF-OBS-004] seconds until next auto-refresh, null = paused
    autoRefreshActive: boolean      // [TF-OBS-004]
    planDate?: string                       // [TF-OBS-005] observe_date_semantics
    effectiveTradeDate?: string             // [TF-OBS-005]
    nonTradingDayPlan?: boolean             // [TF-OBS-005]
    nextTradingDayHint?: string             // [TF-OBS-005]
    onGoToCandidates?: () => void           // [UI-013] tradeflow_empty_state_cta
    onGoToObservationWarehouse?: () => void // [UI-013] tradeflow_empty_state_cta
}) {
    const [showResult, setShowResult] = useState(false)
    const effectiveLastTime = lastObservedAt || lastCheckTime

    // [TF-OBS-004] observe_refresh_alert_queue — group items: triggered (pending-first) / near-trigger / invalidated+other
    const triggeredItems = items.filter(it => it.observe_state === 'TRIGGERED' || it.trigger_explain?.category === 'triggered')
        .sort((a, b) => {
            // pending-confirmation first, then by distance desc
            const ap = a.paper_status === 'pending' ? 0 : 1
            const bp = b.paper_status === 'pending' ? 0 : 1
            if (ap !== bp) return ap - bp
            return (b.trigger_distance_pct ?? -999) - (a.trigger_distance_pct ?? -999)
        })
    const nearItems = items.filter(it => it.near_trigger && !triggeredItems.includes(it))
        .sort((a, b) => (b.trigger_distance_pct ?? -999) - (a.trigger_distance_pct ?? -999))
    const otherItems = items.filter(it => !triggeredItems.includes(it) && !nearItems.includes(it))

    const countdownText = (() => {
        if (!autoRefreshActive || nextRefreshIn == null) return null
        const mm = Math.floor(nextRefreshIn / 60)
        const ss = nextRefreshIn % 60
        return mm > 0 ? `${mm}:${ss.toString().padStart(2, '0')}` : `${ss}s`
    })()

    return (
        <div>
            <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-700">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-300">盘中观察</span>
                {/* [TF-OBS-006] observe_refresh_visual_semantics — prominent last-refresh time */}
                {effectiveLastTime ? (
                    <span className="inline-flex items-center gap-1 rounded bg-slate-50 px-1.5 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300" title={effectiveLastTime}>
                        <Clock className="h-3 w-3" />
                        最近刷新: {formatLastRefreshTime(effectiveLastTime) || effectiveLastTime}
                    </span>
                ) : (
                    <span className="inline-flex items-center gap-1 text-xs text-slate-400">
                        <Clock className="h-3 w-3" />
                        尚未刷新
                    </span>
                )}
                {observeAutoRun && (
                    <span className="inline-flex items-center gap-1 rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-300">
                        自动执行
                    </span>
                )}
                {/* [TF-OBS-004] observe_refresh_alert_queue — auto-refresh status + countdown */}
                {autoRefreshActive && countdownText ? (
                    <span className="inline-flex items-center gap-1 rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">
                        <RefreshCw className="h-3 w-3" />
                        自动刷新 · {Math.round(refreshIntervalSeconds / 60)}分钟 · 下次 {countdownText}
                    </span>
                ) : autoRefreshActive ? (
                    <span className="inline-flex items-center gap-1 rounded bg-slate-50 px-1.5 py-0.5 text-[11px] font-medium text-slate-500 dark:bg-slate-700 dark:text-slate-400">
                        自动刷新中
                    </span>
                ) : (
                    <span className="inline-flex items-center gap-1 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-600 dark:bg-amber-900/30 dark:text-amber-300" title={!isTradingDay ? '非交易日' : !isMarketHours ? '非交易时段' : '已暂停'}>
                        已暂停 · {!isTradingDay ? '非交易日' : !isMarketHours ? '非交易时段' : '已暂停'}
                    </span>
                )}
                {/* [TF-OBS-006] observe_refresh_visual_semantics — one-click refresh entry */}
                <button
                    onClick={() => { onRun(); setShowResult(true) }}
                    disabled={running}
                    className="ml-auto inline-flex items-center gap-1.5 rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                    title="立即拉取一次实时行情并检查触发状态（不自动下单）"
                >
                    {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                    {running ? '执行中...' : '一键刷新观察'}
                </button>
            </div>
            {/* [TF-OBS-006] observe_refresh_visual_semantics — A-share color legend
                (red = approaching trigger / strong, green = approaching invalid / weak) */}
            {items.length > 0 && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-slate-100 px-4 py-2 dark:border-slate-700">
                    <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">红涨绿跌图例:</span>
                    {OBSERVE_LEGEND.map(e => (
                        <span key={e.tone} className="inline-flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400" title={e.desc}>
                            <span className={`inline-block h-2 w-2 rounded-full ${e.dotCls}`} />
                            {e.label}
                        </span>
                    ))}
                </div>
            )}
            {/* [TF-OBS-004] observe_refresh_alert_queue — grouped summary */}
            {/* [TF-OBS-006] observe_refresh_visual_semantics — near-trigger uses red (strong) per A-share legend */}
            {items.length > 0 && (
                <div className="flex flex-wrap items-center gap-4 border-b border-slate-100 px-4 py-2 dark:border-slate-700">
                    <span className="text-xs text-slate-500">待确认: <span className="font-medium text-amber-600 dark:text-amber-400">{pendingCount}</span></span>
                    <span className="text-xs text-slate-500">已触发: <span className="font-medium text-red-600 dark:text-red-400">{triggeredItems.length}</span></span>
                    <span className="text-xs text-slate-500">接近触发: <span className="font-medium text-red-500 dark:text-red-400">{nearTriggerCount}</span></span>
                    <span className="text-xs text-slate-500">已失效: <span className="font-medium text-emerald-600 dark:text-emerald-400">{items.filter(i => i.observe_state === 'INVALIDATED').length}</span></span>
                </div>
            )}
            {/* [TF-OBS-005] observe_date_semantics — non-trading-day plan → next trading day hint */}
            {nonTradingDayPlan && nextTradingDayHint && (
                <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-300">
                    <Calendar className="mt-0.5 h-4 w-4 shrink-0" />
                    <div>
                        <div>{nextTradingDayHint}</div>
                        {planDate && effectiveTradeDate && (
                            <div className="mt-1 text-xs opacity-80">
                                候选池日期 <span className="font-medium">{planDate}</span>
                                {' → '}生效交易日 <span className="font-medium">{effectiveTradeDate}</span>
                            </div>
                        )}
                    </div>
                </div>
            )}
            {/* [TF-OBS-006] observe_refresh_visual_semantics — non-market-hours banner.
                Outside trading sessions users can still review the last observe snapshot;
                no live trigger checks happen, so we surface that explicitly. */}
            {shouldShowNonMarketBanner(isTradingDay, isMarketHours) && (
                <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300">
                    <Clock className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                    <div>
                        <div>
                            {!isTradingDay ? '当前为非交易日' : '当前为非交易时段'}
                            {items.length > 0 ? '，可查看上次观察结果，等待交易时段自动刷新。' : '，等待交易时段开始后自动执行观察。'}
                        </div>
                        <div className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">
                            交易时段：周一至周五 09:30-11:30 / 13:00-15:00（法定节假日除外）
                        </div>
                    </div>
                </div>
            )}
            {observeReason && items.length === 0 && !runResult && (
                <div className="mx-4 mt-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
                    {observeReason}
                </div>
            )}
            {showResult && runResult && (
                <div className={`mx-4 mt-3 rounded-lg border px-4 py-3 text-sm ${
                    runResult.status === 'skipped'
                        ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300'
                        : runResult.errors.length > 0
                        ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300'
                        : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300'
                }`}>
                    {runResult.status === 'skipped' ? (
                        <span>跳过: {runResult.skipped_reason}</span>
                    ) : (
                        <div className="flex flex-wrap gap-4">
                            <span>检查: {runResult.checked}</span>
                            <span className="text-red-600 dark:text-red-400">触发: {runResult.triggered}</span>
                            <span className="text-emerald-600 dark:text-emerald-400">失效: {runResult.invalidated}</span>
                            <span>等待: {runResult.waiting}</span>
                            <span className="text-slate-500">无行情: {runResult.skipped}</span>
                            <span>信号: {runResult.signals_written}</span>
                        </div>
                    )}
                    {runResult.errors.length > 0 && (
                        <div className="mt-2 text-xs">{runResult.errors.join('; ')}</div>
                    )}
                    <div className="mt-1 text-xs opacity-70">执行时间: {runResult.run_time}</div>
                </div>
            )}
            {items.length === 0 && !runResult && !observeReason ? (
                <div className="p-4">
                    <EmptyStateCTA
                        icon={Eye}
                        title="暂无盘中观察数据"
                        desc={isTradingDay
                            ? '盘中观察对象来自当日生效候选池，开盘后将自动执行。'
                            : '当前为非交易日，观察将在下一个交易日开盘后自动执行。'}
                        hints={[
                            '先生成候选池，候选会自动进入当日观察',
                            '或手动将标的加入观察仓（跟踪看板）',
                            '也可点击右上角「一键刷新观察」立即触发一次检查',
                        ]}
                        actions={[
                            ...(onGoToCandidates ? [{ label: '前往生成候选池', onClick: onGoToCandidates, primary: true }] : []),
                            ...(onGoToObservationWarehouse ? [{ label: '前往观察仓', onClick: onGoToObservationWarehouse }] : []),
                        ]}
                    />
                </div>
            ) : items.length === 0 && !runResult ? null : (
            <div className="overflow-x-auto">
                {/* [TF-OBS-004] observe_refresh_alert_queue — grouped sections, pending-first */}
                {/* [TF-OBS-006] observe_refresh_visual_semantics — near-trigger accent aligned to red (strong) */}
                <ObserveGroup title="已触发（待确认优先）" subtitle="已进入模拟账本待确认队列，需人工确认，不自动下单" items={triggeredItems} accent="bg-red-500" />
                <ObserveGroup title="接近触发" subtitle="价格接近触发价，重点关注" items={nearItems} accent="bg-red-400" />
                <ObserveGroup title="已失效 / 等待中" items={otherItems} accent="bg-slate-400" />
                {items.length > 0 && triggeredItems.length === 0 && nearItems.length === 0 && otherItems.length === 0 && (
                    <table className="w-full text-sm">
                        <tbody>{items.map(item => <ObserveRow key={item.symbol} item={item} />)}</tbody>
                    </table>
                )}
            </div>
            )}
        </div>
    )
}

function TAQueueTable({ items, meta }: { items: TradeFlowTAQueueItem[]; meta: TradeFlowTAQueueResponse }) {
    return (
        <div>
            <div className="flex flex-wrap items-center gap-4 border-b border-slate-100 px-4 py-2.5 dark:border-slate-700">
                <span className="text-xs text-slate-500">队列: <span className="font-medium text-slate-700 dark:text-slate-300">{meta.total_in_queue}</span></span>
                <span className="text-xs text-slate-500">已调度: <span className="font-medium text-blue-600 dark:text-blue-400">{meta.dispatched_count}</span></span>
                <span className="text-xs text-slate-500">阻塞: <span className="font-medium text-red-600 dark:text-red-400">{meta.blocked_count}</span></span>
                <span className="text-xs text-slate-500">待处理: <span className="font-medium text-slate-700 dark:text-slate-300">{meta.pending_count}</span></span>
            </div>
            {items.length === 0 ? (
                <div className="py-20 text-center text-sm text-slate-400">暂无 TA 队列数据</div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                <th className="px-4 py-2.5 font-medium">代码</th>
                                <th className="px-4 py-2.5 font-medium">名称</th>
                                <th className="px-4 py-2.5 font-medium">Tier</th>
                                <th className="px-4 py-2.5 font-medium">优先级</th>
                                <th className="px-4 py-2.5 font-medium">TA状态</th>
                                <th className="px-4 py-2.5 font-medium">调度原因</th>
                                <th className="px-4 py-2.5 font-medium">模型</th>
                                <th className="px-4 py-2.5 font-medium">完整度</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(item => {
                                const st = taStatusLabel(item.deep_ta_status)
                                return (
                                    <tr
                                        key={item.symbol}
                                        className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                    >
                                        <td className="px-4 py-2.5 font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{item.symbol}</td>
                                        <td className="max-w-[120px] truncate px-4 py-2.5 text-slate-700 dark:text-slate-300">{item.name || '--'}</td>
                                        <td className="px-4 py-2.5">
                                            <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-bold ${tierBadgeClass(item.tier)}`}>{item.tier || '-'}</span>
                                        </td>
                                        <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{item.ta_budget_priority}</td>
                                        <td className="px-4 py-2.5">
                                            <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${st.cls}`}>{st.text}</span>
                                        </td>
                                        <td className="max-w-[200px] truncate px-4 py-2.5 text-xs text-slate-600 dark:text-slate-400" title={item.deep_ta_dispatch_reason}>
                                            {item.deep_ta_dispatch_reason || '-'}
                                        </td>
                                        <td className="px-4 py-2.5 text-xs text-slate-600 dark:text-slate-400">{item.deep_ta_model || '-'}</td>
                                        <td className="px-4 py-2.5">
                                            <CompletenessBar value={item.tradeflow_data_completeness} />
                                        </td>
                                    </tr>
                                )
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}

const TABS: { key: TabKey; label: string; icon: typeof Target }[] = [
    { key: 'candidates', label: '候选池', icon: Target },
    { key: 'watch-pool', label: '观察池', icon: Crosshair },
    { key: 'compare', label: '候选对比', icon: GitCompare },
    { key: 'observe', label: '盘中观察', icon: Eye },
    { key: 'ta-queue', label: 'TA 队列', icon: ListOrdered },
    { key: 'review', label: '盘后 Review', icon: BarChart3 },
    { key: 'filtered', label: '被过滤', icon: FilterX },
    { key: 'data-health', label: '数据健康', icon: Activity },
    { key: 'topic-heatmap', label: '主题热度', icon: Flame },
    { key: 'paper-ledger', label: '模拟账本', icon: Wallet },
]

function fmtPct(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return '0.0%'
    return `${v.toFixed(1)}%`
}

interface StrategyRow {
    strategy: string
    total: number
    hit: number
    miss: number
    noData: number
    invalidated: number
    hitRate: number
    missRate: number
}

interface TierRow {
    tier: string
    total: number
    hit: number
    miss: number
    noData: number
    invalidated: number
}

// [TF-REVIEW-003] strategy_attribution_review
const HIT_TYPE_LABELS: Record<string, string> = {
    technical_hit: '技术命中',
    policy_hit: '政策命中',
    fund_flow_hit: '资金流命中',
    data_issue: '数据不足',
    risk_hit: '风险触发',
}

const HIT_TYPE_COLORS: Record<string, string> = {
    technical_hit: 'text-blue-600 dark:text-blue-400',
    policy_hit: 'text-purple-600 dark:text-purple-400',
    fund_flow_hit: 'text-cyan-600 dark:text-cyan-400',
    data_issue: 'text-amber-600 dark:text-amber-400',
    risk_hit: 'text-red-600 dark:text-red-400',
}

function ReviewTab({ data }: { data: TradeFlowReviewResponse }) {
    const items = data.results
    const agg = data.summary_agg

    const total = items.length
    const hitCount = items.filter(i => i.observe_state === 'TRIGGERED').length
    const invalidatedCount = items.filter(i => i.observe_state === 'INVALIDATED').length
    const waitingCount = items.filter(i => i.observe_state === 'WAITING').length
    const removedCount = items.filter(i => i.plan_action === 'REMOVE_FROM_WATCH').length
    const hitRate = total > 0 ? (hitCount / total) * 100 : 0
    const invalidatedRate = total > 0 ? (invalidatedCount / total) * 100 : 0

    const tierStats = useMemo<TierRow[]>(() => {
        const map = new Map<string, { hit: number; miss: number; noData: number; invalidated: number; total: number }>()
        for (const item of items) {
            const tier = item.tier || '-'
            const row = map.get(tier) || { hit: 0, miss: 0, noData: 0, invalidated: 0, total: 0 }
            row.total++
            if (item.observe_state === 'TRIGGERED') row.hit++
            else if (item.observe_state === 'INVALIDATED') row.invalidated++
            else if (item.plan_action === 'REMOVE_FROM_WATCH') row.miss++
            else row.noData++
            map.set(tier, row)
        }
        const order = ['A', 'B', 'C', '-']
        return order.filter(t => map.has(t)).map(tier => ({ tier, ...map.get(tier)! }))
    }, [items])

    const strategyStats = useMemo<StrategyRow[]>(() => {
        const map = new Map<string, { hit: number; miss: number; noData: number; invalidated: number; total: number }>()
        for (const item of items) {
            const tags = item.strategy_tags.length > 0 ? item.strategy_tags : ['未分类']
            for (const tag of tags) {
                const row = map.get(tag) || { hit: 0, miss: 0, noData: 0, invalidated: 0, total: 0 }
                row.total++
                if (item.observe_state === 'TRIGGERED') row.hit++
                else if (item.observe_state === 'INVALIDATED') row.invalidated++
                else if (item.plan_action === 'REMOVE_FROM_WATCH') row.miss++
                else row.noData++
                map.set(tag, row)
            }
        }
        const rows: StrategyRow[] = []
        for (const [strategy, row] of map) {
            rows.push({
                strategy,
                total: row.total,
                hit: row.hit,
                miss: row.miss,
                noData: row.noData,
                invalidated: row.invalidated,
                hitRate: row.total > 0 ? (row.hit / row.total) * 100 : 0,
                missRate: row.total > 0 ? (row.miss / row.total) * 100 : 0,
            })
        }
        rows.sort((a, b) => b.hitRate - a.hitRate)
        return rows
    }, [items])

    const removalReasons = useMemo(() => {
        return items.filter(i => i.plan_action === 'REMOVE_FROM_WATCH' && i.reason)
    }, [items])

    // [TF-REVIEW-003] strategy_attribution_review — attribution breakdown
    const attributionStats = useMemo(() => {
        const map = new Map<string, number>()
        for (const item of items) {
            const ht = item.hit_type || 'data_issue'
            map.set(ht, (map.get(ht) || 0) + 1)
        }
        return Array.from(map.entries())
            .map(([type, count]) => ({ type, label: HIT_TYPE_LABELS[type] || type, count }))
            .sort((a, b) => b.count - a.count)
    }, [items])

    // [TF-REVIEW-003] strategy_attribution_review — next-day feedback items
    const nextDayFeedback = useMemo(() => {
        return items.filter(i =>
            i.hit_type || i.tomorrow_focus || i.downgrade_reason || (i.evidence_needed && i.evidence_needed.length > 0)
        )
    }, [items])

    const suggestions = useMemo(() => {
        const list: string[] = []
        if (invalidatedRate > 50) list.push('失效占比偏高，建议收紧触发条件或优化失效价设置')
        if (hitRate < 20 && total > 5) list.push('命中率偏低，建议检查策略筛选阈值')
        const lowComp = agg.avg_completeness < 0.5
        if (lowComp) list.push('平均完整度较低，建议补充数据源以提升候选质量')
        const highRemove = removedCount > total * 0.3
        if (highRemove) list.push('移除比例较高，建议优化候选池准入条件')
        if (waitingCount === total && total > 0) list.push('所有候选均处于等待状态，尚未触发')
        if (list.length === 0) list.push('当前策略运行正常，继续保持')
        return list
    }, [invalidatedRate, hitRate, total, agg, removedCount, waitingCount])

    return (
        <div className="space-y-6 p-4">
            <div className="text-xs text-slate-400">复盘时间: {data.reviewed_at || '-'}</div>

            {/* [TF-REVIEW-002] review_date_mapping — show data_status and cross-date mapping */}
            {data.data_status && data.data_status !== 'OK' && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
                    <span className="font-medium">数据状态: {data.data_status}</span>
                    {data.data_status_message && <span className="ml-2">{data.data_status_message}</span>}
                </div>
            )}
            {data.plan_date && data.plan_date !== data.trade_date && (
                <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-xs text-blue-700 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-300">
                    该候选池由 {data.plan_date} 生成
                    {data.effective_trade_date && `，生效交易日: ${data.effective_trade_date}`}
                </div>
            )}

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {[
                    { label: '总候选数', value: total, color: 'text-slate-900 dark:text-slate-100' },
                    { label: '命中(TRIGGERED)', value: hitCount, color: 'text-emerald-600 dark:text-emerald-400' },
                    { label: '失效(INVALIDATED)', value: invalidatedCount, color: 'text-red-600 dark:text-red-400' },
                    { label: '命中率', value: fmtPct(hitRate), color: 'text-blue-600 dark:text-blue-400' },
                    { label: '失效占比', value: fmtPct(invalidatedRate), color: 'text-amber-600 dark:text-amber-400' },
                ].map(c => (
                    <div key={c.label} className="card p-4">
                        <div className="text-xs text-slate-500 dark:text-slate-400">{c.label}</div>
                        <div className={`mt-1 text-2xl font-semibold tabular-nums ${c.color}`}>{c.value}</div>
                    </div>
                ))}
                {[
                    { label: 'A级', value: agg.tier_a_count, color: 'text-emerald-600 dark:text-emerald-400' },
                    { label: 'B级', value: agg.tier_b_count, color: 'text-amber-600 dark:text-amber-400' },
                    { label: 'C级', value: agg.tier_c_count, color: 'text-red-600 dark:text-red-400' },
                    { label: '需深度TA', value: agg.need_deep_ta_count, color: 'text-purple-600 dark:text-purple-400' },
                    { label: '平均完整度', value: fmtPct(agg.avg_completeness * 100), color: 'text-blue-600 dark:text-blue-400' },
                ].map(c => (
                    <div key={c.label} className="card p-4">
                        <div className="text-xs text-slate-500 dark:text-slate-400">{c.label}</div>
                        <div className={`mt-1 text-2xl font-semibold tabular-nums ${c.color}`}>{c.value}</div>
                    </div>
                ))}
            </div>

            {strategyStats.length > 0 && (
                <div className="card overflow-x-auto">
                    <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300">策略命中率</div>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                <th className="px-4 py-2.5 font-medium">策略</th>
                                <th className="px-4 py-2.5 font-medium">总数</th>
                                <th className="px-4 py-2.5 font-medium">命中</th>
                                <th className="px-4 py-2.5 font-medium">误报</th>
                                <th className="px-4 py-2.5 font-medium">无数据</th>
                                <th className="px-4 py-2.5 font-medium">失效</th>
                                <th className="px-4 py-2.5 font-medium">命中率</th>
                                <th className="px-4 py-2.5 font-medium">误报率</th>
                            </tr>
                        </thead>
                        <tbody>
                            {strategyStats.map(row => (
                                <tr key={row.strategy} className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
                                    <td className="px-4 py-2.5 font-medium text-slate-700 dark:text-slate-300">{row.strategy}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{row.total}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-emerald-600 dark:text-emerald-400">{row.hit}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-amber-600 dark:text-amber-400">{row.miss}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-500">{row.noData}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-red-500">{row.invalidated}</td>
                                    <td className="px-4 py-2.5 tabular-nums font-medium">{fmtPct(row.hitRate)}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-amber-600 dark:text-amber-400">{fmtPct(row.missRate)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {tierStats.length > 0 && (
                <div className="card overflow-x-auto">
                    <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300">分层统计</div>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                <th className="px-4 py-2.5 font-medium">层级</th>
                                <th className="px-4 py-2.5 font-medium">总数</th>
                                <th className="px-4 py-2.5 font-medium">命中</th>
                                <th className="px-4 py-2.5 font-medium">误报</th>
                                <th className="px-4 py-2.5 font-medium">无数据</th>
                                <th className="px-4 py-2.5 font-medium">失效</th>
                            </tr>
                        </thead>
                        <tbody>
                            {tierStats.map(row => (
                                <tr key={row.tier} className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
                                    <td className="px-4 py-2.5">
                                        <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-bold ${tierBadgeClass(row.tier)}`}>{row.tier}</span>
                                    </td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{row.total}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-emerald-600 dark:text-emerald-400">{row.hit}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-amber-600 dark:text-amber-400">{row.miss}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-500">{row.noData}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-red-500">{row.invalidated}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* [TF-REVIEW-003] strategy_attribution_review — hit attribution breakdown */}
            {attributionStats.length > 0 && (
                <div className="card overflow-x-auto">
                    <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300">命中归因</div>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                <th className="px-4 py-2.5 font-medium">归因</th>
                                <th className="px-4 py-2.5 font-medium">数量</th>
                                <th className="px-4 py-2.5 font-medium">占比</th>
                            </tr>
                        </thead>
                        <tbody>
                            {attributionStats.map(row => (
                                <tr key={row.type} className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
                                    <td className={`px-4 py-2.5 font-medium ${HIT_TYPE_COLORS[row.type] || 'text-slate-700 dark:text-slate-300'}`}>{row.label}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{row.count}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-500">{fmtPct(total > 0 ? (row.count / total) * 100 : 0)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* [TF-REVIEW-003] strategy_attribution_review — next-day feedback */}
            {nextDayFeedback.length > 0 && (
                <div className="card">
                    <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300">次日反馈</div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                    <th className="px-4 py-2.5 font-medium">代码</th>
                                    <th className="px-4 py-2.5 font-medium">归因</th>
                                    <th className="px-4 py-2.5 font-medium">明日关注</th>
                                    <th className="px-4 py-2.5 font-medium">降级原因</th>
                                    <th className="px-4 py-2.5 font-medium">需要补证据</th>
                                </tr>
                            </thead>
                            <tbody>
                                {nextDayFeedback.map(item => (
                                    <tr key={item.symbol} className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
                                        <td className="px-4 py-2.5">
                                            <span className="font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{item.symbol}</span>
                                            <span className="ml-1.5 max-w-[100px] truncate align-middle text-xs text-slate-500 dark:text-slate-400">{item.name || '--'}</span>
                                        </td>
                                        <td className={`px-4 py-2.5 text-xs font-medium ${HIT_TYPE_COLORS[item.hit_type || ''] || 'text-slate-500'}`}>
                                            {HIT_TYPE_LABELS[item.hit_type || ''] || item.hit_type || '-'}
                                        </td>
                                        <td className="px-4 py-2.5 text-slate-600 dark:text-slate-400">{item.tomorrow_focus || '-'}</td>
                                        <td className="px-4 py-2.5 text-amber-600 dark:text-amber-400">{item.downgrade_reason || '-'}</td>
                                        <td className="px-4 py-2.5 text-slate-500">
                                            {item.evidence_needed && item.evidence_needed.length > 0
                                                ? item.evidence_needed.join('、')
                                                : '-'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {removalReasons.length > 0 && (
                <div className="card">
                    <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300">移除理由</div>
                    <ul className="divide-y divide-slate-50 dark:divide-slate-800">
                        {removalReasons.map(item => (
                            <li key={item.symbol} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                                <span className="font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{item.symbol}</span>
                                <span className="max-w-[120px] truncate text-slate-700 dark:text-slate-300">{item.name || '--'}</span>
                                <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-bold ${tierBadgeClass(item.tier)}`}>{item.tier || '-'}</span>
                                <span className="text-slate-500 dark:text-slate-400">{item.reason}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            <div className="card">
                <div className="border-b border-slate-100 px-4 py-3 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-300">调参建议</div>
                <ul className="divide-y divide-slate-50 dark:divide-slate-800">
                    {suggestions.map((s, i) => (
                        <li key={i} className="flex items-start gap-2 px-4 py-2.5 text-sm text-slate-600 dark:text-slate-400">
                            <span className="mt-0.5 inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full bg-blue-500" />
                            {s}
                        </li>
                    ))}
                </ul>
            </div>
        </div>
    )
}

// [UI-007] tradeflow_filtered_trace
function FilteredTab({ data, candidateCount }: { data: TradeFlowFilteredResponse; candidateCount: number }) {
    const [reasonFilter, setReasonFilter] = useState('')
    const filtered = data.filtered
    const breakdown = data.filter_breakdown
    const totalScanned = filtered.length + candidateCount

    const filteredItems = reasonFilter
        ? filtered.filter(f => {
            const reason = f.reason
            if (reasonFilter === '流动性差') return reason.includes('流动性')
            if (reasonFilter === '数据缺失') return reason.includes('数据')
            if (reasonFilter === '无策略命中') return reason.includes('无策略')
            return !reason.includes('流动性') && !reason.includes('数据') && !reason.includes('无策略')
        })
        : filtered

    const isEmpty = candidateCount === 0 && filtered.length === 0

    return (
        <div className="space-y-4 p-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="card p-4">
                    <div className="text-xs text-slate-500 dark:text-slate-400">总扫描数</div>
                    <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">{totalScanned}</div>
                </div>
                <div className="card p-4">
                    <div className="text-xs text-slate-500 dark:text-slate-400">入池数</div>
                    <div className="mt-1 text-2xl font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">{candidateCount}</div>
                </div>
                <div className="card p-4">
                    <div className="text-xs text-slate-500 dark:text-slate-400">过滤数</div>
                    <div className="mt-1 text-2xl font-semibold tabular-nums text-red-600 dark:text-red-400">{filtered.length}</div>
                </div>
                <div className="card p-4">
                    <div className="text-xs text-slate-500 dark:text-slate-400">入池率</div>
                    <div className="mt-1 text-2xl font-semibold tabular-nums text-blue-600 dark:text-blue-400">
                        {totalScanned > 0 ? ((candidateCount / totalScanned) * 100).toFixed(1) + '%' : '-'}
                    </div>
                </div>
            </div>

            {Object.keys(breakdown).length > 0 && (
                <div className="card p-4">
                    <div className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-300">过滤分类</div>
                    <div className="flex flex-wrap gap-3">
                        {Object.entries(breakdown).map(([cat, cnt]) => (
                            <span
                                key={cat}
                                className={`inline-flex items-center gap-1 rounded px-2.5 py-1 text-sm font-medium ${
                                    cat === '流动性差'
                                        ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                                        : cat === '数据缺失'
                                        ? 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                                        : cat === '无策略命中'
                                        ? 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300'
                                        : 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300'
                                }`}
                            >
                                {cat}: {cnt}只
                            </span>
                        ))}
                    </div>
                </div>
            )}

            {isEmpty && (
                <div className="py-12 text-center text-sm text-slate-400">
                    <FilterX className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                    尚未生成候选池，暂无过滤记录
                    <div className="mt-2 text-xs text-slate-400">daily plan 是计划记录，不等同于候选股票数量</div>
                </div>
            )}

            {!isEmpty && candidateCount === 0 && filtered.length > 0 && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
                    已扫描但全部被过滤，无入池候选
                </div>
            )}

            {filtered.length > 0 && (
                <div className="card">
                    <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-700">
                        <span className="text-sm font-medium text-slate-700 dark:text-slate-300">被过滤股票</span>
                        <select
                            value={reasonFilter}
                            onChange={e => setReasonFilter(e.target.value)}
                            className="rounded border border-slate-200 bg-white px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                        >
                            <option value="">全部原因</option>
                            <option value="流动性差">流动性差</option>
                            <option value="数据缺失">数据缺失</option>
                            <option value="无策略命中">无策略命中</option>
                            <option value="其他">其他</option>
                        </select>
                        <span className="ml-auto text-xs text-slate-400">{filteredItems.length} 条记录</span>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                    <th className="px-4 py-2.5 font-medium">代码</th>
                                    <th className="px-4 py-2.5 font-medium">名称</th>
                                    <th className="px-4 py-2.5 font-medium">来源</th>
                                    <th className="px-4 py-2.5 font-medium">过滤原因</th>
                                    <th className="px-4 py-2.5 font-medium">扫描时间</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filteredItems.map(item => {
                                    const reason = item.reason
                                    let reasonCls = 'text-slate-600 dark:text-slate-400'
                                    if (reason.includes('流动性')) reasonCls = 'text-amber-600 dark:text-amber-400'
                                    else if (reason.includes('数据')) reasonCls = 'text-red-500 dark:text-red-400'
                                    else if (reason.includes('无策略')) reasonCls = 'text-slate-500 dark:text-slate-400'
                                    return (
                                        <tr
                                            key={`${item.symbol}-${item.reason}`}
                                            className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                        >
                                            <td className="px-4 py-2.5 font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{item.symbol}</td>
                                            <td className="max-w-[120px] truncate px-4 py-2.5 text-slate-700 dark:text-slate-300">{item.name || '--'}</td>
                                            <td className="px-4 py-2.5">
                                                <span className="inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 dark:bg-slate-700 dark:text-slate-300">{item.source || '-'}</span>
                                            </td>
                                            <td className={`max-w-[300px] truncate px-4 py-2.5 text-xs ${reasonCls}`} title={item.reason}>
                                                {item.reason || '-'}
                                            </td>
                                            <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">{item.created_at || '-'}</td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            <div className="text-xs text-slate-400">
                daily plan 是计划记录，不等同于候选股票数量。被过滤记录来自最近一次 Discovery 扫描。
            </div>
        </div>
    )
}

function deriveHealthStatus(s: DataHealthSource): DataHealthStatus {
    if (s.status) return s.status
    if (s.available && !s.error) return 'OK'
    if (s.available && s.error) return 'PARTIAL'
    if (!s.available && s.error) return 'FAILED'
    if (!s.available && !s.last_updated) return 'NOT_QUERIED'
    return 'STALE'
}

function healthStatusBadge(status: DataHealthStatus): { text: string; cls: string; dot: string } {
    switch (status) {
        case 'OK': return { text: '正常', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300', dot: 'bg-emerald-500' }
        case 'PARTIAL': return { text: '部分可用', cls: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300', dot: 'bg-yellow-500' }
        case 'FAILED': return { text: '故障', cls: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300', dot: 'bg-red-500' }
        case 'STALE': return { text: '过期', cls: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300', dot: 'bg-orange-500' }
        case 'NOT_QUERIED': return { text: '未查询', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400', dot: 'bg-slate-400' }
        default: return { text: '未知', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400', dot: 'bg-slate-400' }
    }
}

// [PERF-001] runtime_tier_contract
function RuntimeTierBadge({ tier, latency }: { tier: string; latency: string }) {
    const label = (RUNTIME_TIER_LABELS as Record<string, string>)[tier] || tier
    const cls = tier === 'FAST_RADAR'
        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
        : tier === 'LIGHT_RESEARCH'
        ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
        : 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300'
    return (
        <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium ${cls}`}>
            {label}
            <span className="text-[10px] opacity-70">~{latency}</span>
        </span>
    )
}

// [UI-013] tradeflow_empty_state_cta
// Unified empty state with soft next-step CTAs. Wording is intentionally neutral —
// no 买/卖/加仓/减仓 terms, no marketing language. Empty states must never block
// the main flow: they only suggest where to go next.
interface EmptyStateAction {
    label: string
    onClick: () => void
    primary?: boolean
    disabled?: boolean
}

function EmptyStateCTA({
    icon: Icon,
    title,
    desc,
    hints,
    actions,
}: {
    icon: typeof Target
    title: string
    desc?: string
    hints?: string[]
    actions?: EmptyStateAction[]
}) {
    return (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50/60 px-6 py-10 text-center dark:border-slate-600 dark:bg-slate-800/40">
            <Icon className="mx-auto mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
            <p className="font-medium text-slate-600 dark:text-slate-300">{title}</p>
            {desc && <p className="mt-1 text-sm text-slate-400 dark:text-slate-500">{desc}</p>}
            {hints && hints.length > 0 && (
                <div className="mt-3 space-y-1 text-xs text-slate-400 dark:text-slate-500">
                    {hints.map((h, i) => (
                        <div key={i} className="flex items-start justify-center gap-1.5">
                            <span className="mt-1 inline-block h-1 w-1 flex-shrink-0 rounded-full bg-slate-300 dark:bg-slate-600" />
                            <span>{h}</span>
                        </div>
                    ))}
                </div>
            )}
            {actions && actions.length > 0 && (
                <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                    {actions.map((a, i) => (
                        <button
                            key={i}
                            onClick={a.onClick}
                            disabled={a.disabled}
                            className={`inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                                a.primary
                                    ? 'bg-blue-600 text-white hover:bg-blue-700'
                                    : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
                            }`}
                        >
                            {a.label}
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}

// [TF-UX-001] small_cap_trial_workbench — focus group visual cues
function focusGroupDotClass(key: 'pending_confirm' | 'near_trigger' | 'main'): string {
    switch (key) {
        case 'pending_confirm': return 'bg-red-500'
        case 'near_trigger': return 'bg-amber-500'
        case 'main': return 'bg-indigo-500'
    }
}

function focusGroupTitleClass(key: 'pending_confirm' | 'near_trigger' | 'main'): string {
    switch (key) {
        case 'pending_confirm': return 'text-red-700 dark:text-red-300'
        case 'near_trigger': return 'text-amber-700 dark:text-amber-300'
        case 'main': return 'text-indigo-700 dark:text-indigo-300'
    }
}

// [TF-UX-001] small_cap_trial_workbench — "5000 元试跑" prompt area
// Shows single-ticket budget, max occupancy, and pending confirmation actions.
// Wording is intentionally soft — no 买/卖/加仓/减仓 terms.
function TrialBudgetPrompt({ budget }: { budget: import('@/utils/tradeflowFocus').TrialBudgetView }) {
    return (
        <div className="rounded-lg border border-purple-200 bg-purple-50/70 px-3 py-2.5 text-xs dark:border-purple-800/60 dark:bg-purple-900/15">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <div className="flex items-center gap-1 font-bold text-purple-700 dark:text-purple-300">
                    <Wallet className="h-3.5 w-3.5" />
                    5000 元试跑
                </div>
                <span className="text-slate-400">|</span>
                <div className="flex items-center gap-1 text-slate-600 dark:text-slate-300">
                    <span className="text-slate-400">本金</span>
                    <span className="font-semibold tabular-nums">¥{budget.principal.toFixed(0)}</span>
                </div>
                <div className="flex items-center gap-1 text-slate-600 dark:text-slate-300">
                    <span className="text-slate-400">单票预算</span>
                    <span className="font-semibold tabular-nums">
                        {budget.per_ticket_budget > 0 ? `¥${budget.per_ticket_budget.toFixed(0)}` : '—'}
                    </span>
                </div>
                <div className="flex items-center gap-1 text-slate-600 dark:text-slate-300">
                    <span className="text-slate-400">最大占用</span>
                    <span className="font-semibold tabular-nums">
                        {budget.max_occupancy > 0 ? `¥${budget.max_occupancy.toFixed(0)}` : '—'}
                        <span className="ml-0.5 text-[10px] text-slate-400">
                            （{budget.tracking_count}/{budget.max_concurrent_tracking || 0} 只）
                        </span>
                    </span>
                </div>
                {budget.pending_confirmation_count > 0 && (
                    <div className="flex items-center gap-1 rounded bg-red-100 px-1.5 py-0.5 font-medium text-red-700 dark:bg-red-900/40 dark:text-red-300">
                        <Clock className="h-3 w-3" />
                        待确认动作 {budget.pending_confirmation_count}
                    </div>
                )}
                {budget.at_capacity && budget.pending_confirmation_count === 0 && (
                    <div className="flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                        <ShieldAlert className="h-3 w-3" />
                        占用已满
                    </div>
                )}
            </div>
            <div className="mt-1.5 flex items-start gap-1 text-[11px] text-purple-700/80 dark:text-purple-200/70">
                <Lightbulb className="mt-0.5 h-3 w-3 flex-shrink-0" />
                <span>{budget.primary_hint}</span>
            </div>
        </div>
    )
}

// [TF-UX-001] small_cap_trial_workbench — collapsed invalidated/expired pool
// so previously main candidates that have been demoted are still visible
// without crowding the default main view.
function InvalidatedPool({
    items,
    onPick,
    highlightSymbol,
}: {
    items: TradeFlowCandidateItem[]
    onPick: (c: TradeFlowCandidateItem) => void
    highlightSymbol: string | null
}) {
    const [open, setOpen] = useState(false)
    if (items.length === 0) return null
    return (
        <div>
            <button
                onClick={() => setOpen(!open)}
                className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
            >
                {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                <ShieldAlert className="h-4 w-4 text-slate-400" />
                已失效 / 已过期（{items.length} 只）
                <span className="text-xs text-slate-400">— 不进入今日默认主视图</span>
            </button>
            {open && (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {items.map(c => {
                        const ct = candidateTypeLabel(c.candidate_type)
                        const obs = observeStateLabel(c.observe_state)
                        const isHighlighted = !!highlightSymbol && c.symbol === highlightSymbol
                        const highlightCls = isHighlighted
                            ? 'ring-2 ring-indigo-400 bg-indigo-50/60 dark:bg-indigo-900/20'
                            : ''
                        return (
                            <div
                                key={c.symbol}
                                data-highlight-symbol={isHighlighted ? c.symbol : undefined}
                                className={`cursor-pointer rounded border border-slate-100 p-2 opacity-70 transition-colors hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800/50 ${highlightCls}`}
                                onClick={() => onPick(c)}
                            >
                                <div className="flex items-center gap-1.5">
                                    <span className="font-mono text-[11px] font-bold text-slate-600 dark:text-slate-300">{c.symbol}</span>
                                    <span className="max-w-[60px] truncate text-xs text-slate-500 dark:text-slate-400">{c.name || '--'}</span>
                                    <span className={`ml-auto rounded px-1 py-0.5 text-[9px] font-medium ${ct.cls}`}>{ct.text}</span>
                                </div>
                                <div className="mt-1 flex items-center gap-1.5 text-[10px] text-slate-400">
                                    <span className="tabular-nums">综合: {(c.composite_score || 0).toFixed(1)}</span>
                                    <span className={`font-medium ${obs.cls}`}>{obs.text}</span>
                                </div>
                            </div>
                        )
                    })}
                </div>
            )}
        </div>
    )
}

function DataHealthPanel({ data }: { data: TradeFlowDataHealthResponse | null }) {
    if (!data || data.sources.length === 0) {
        return (
            <div className="py-20 text-center text-sm text-slate-400">
                <Activity className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                未生成健康检查
            </div>
        )
    }

    const sourcesWithStatus = data.sources.map(s => ({
        ...s,
        derivedStatus: deriveHealthStatus(s),
    }))

    const okCount = sourcesWithStatus.filter(s => s.derivedStatus === 'OK').length
    const failedCount = sourcesWithStatus.filter(s => s.derivedStatus === 'FAILED').length
    const degradedCount = sourcesWithStatus.filter(s => s.derivedStatus !== 'OK' && s.derivedStatus !== 'NOT_QUERIED' && s.derivedStatus !== 'FAILED').length

    let bannerIcon: string
    let bannerText: string
    let bannerCls: string
    if (failedCount > 0) {
        bannerIcon = '\u274C'
        bannerText = `数据源故障（${failedCount} 个）`
        bannerCls = 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
    } else if (degradedCount > 0) {
        bannerIcon = '\u26A0\uFE0F'
        bannerText = `部分数据源异常（${degradedCount} 个）`
        bannerCls = 'bg-yellow-50 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300'
    } else if (okCount === sourcesWithStatus.length) {
        bannerIcon = '\u2705'
        bannerText = '数据源全部正常'
        bannerCls = 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
    } else {
        bannerIcon = '\u2139\uFE0F'
        bannerText = '今日未生成健康检查'
        bannerCls = 'bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
    }

    return (
        <div className="space-y-4 p-4">
            <div className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium ${bannerCls}`}>
                <span>{bannerIcon}</span>
                {bannerText}
                {data.runtime_tier_meta && (  // [PERF-001]
                    <span className="ml-auto">
                        <RuntimeTierBadge tier={data.runtime_tier_meta.runtime_tier} latency={data.runtime_tier_meta.expected_latency} />
                    </span>
                )}
            </div>

            {data.latest_plan_date && (
                <div className="flex flex-wrap gap-3 text-xs text-slate-500">
                    {data.latest_plan_date && <span>最新计划: <span className="font-medium text-slate-700 dark:text-slate-300">{data.latest_plan_date}</span></span>}
                    {data.latest_candidates_date && <span>最新候选: <span className="font-medium text-slate-700 dark:text-slate-300">{data.latest_candidates_date}</span></span>}
                    {data.total_candidates_today > 0 && <span>今日候选: <span className="font-medium text-slate-700 dark:text-slate-300">{data.total_candidates_today}</span></span>}
                    {data.total_signals_today > 0 && <span>今日信号: <span className="font-medium text-slate-700 dark:text-slate-300">{data.total_signals_today}</span></span>}
                    {data.latest_observe_check_time && <span>最新观察: <span className="font-medium text-slate-700 dark:text-slate-300">{data.latest_observe_check_time}</span></span>}
                    {data.latest_signal_time && <span>最新信号时间: <span className="font-medium text-slate-700 dark:text-slate-300">{data.latest_signal_time}</span></span>}
                </div>
            )}

            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                            <th className="px-4 py-2.5 font-medium">数据源</th>
                            <th className="px-4 py-2.5 font-medium">状态</th>
                            <th className="px-4 py-2.5 font-medium">记录数</th>
                            <th className="px-4 py-2.5 font-medium">备用源</th>
                            <th className="px-4 py-2.5 font-medium">最近更新</th>
                            <th className="px-4 py-2.5 font-medium">错误信息</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sourcesWithStatus.map(s => {
                            const badge = healthStatusBadge(s.derivedStatus)
                            return (
                                <tr
                                    key={s.name}
                                    className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                >
                                    <td className="px-4 py-2.5 font-medium text-slate-700 dark:text-slate-300">{s.name}</td>
                                    <td className="px-4 py-2.5">
                                        <span className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
                                            <span className={`h-1.5 w-1.5 rounded-full ${badge.dot}`} />
                                            {badge.text}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{s.record_count}</td>
                                    <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">{s.fallback_vendor || '-'}</td>
                                    <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">{s.last_updated || '-'}</td>
                                    <td className="max-w-[300px] truncate px-4 py-2.5 text-xs text-red-500" title={s.error}>
                                        {s.error || <span className="text-slate-400">-</span>}
                                    </td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>

            <div className="text-xs text-slate-400">
                数据库: {data.tradeflow_db_available ? '可用' : '不可用'}
                {data.tradeflow_db_path && <span className="ml-2">({data.tradeflow_db_path})</span>}
            </div>
        </div>
    )
}

// [DATA-018] source_freshness_report
function freshnessTrafficLight(entry: SourceFreshnessEntry): { text: string; cls: string; dot: string } {
    const light = entry.traffic_light
    if (light === 'green') {
        return { text: entry.status_label_cn || '正常', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300', dot: 'bg-emerald-500' }
    }
    if (light === 'yellow') {
        return { text: entry.status_label_cn || '警告', cls: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300', dot: 'bg-yellow-500' }
    }
    return { text: entry.status_label_cn || '故障', cls: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300', dot: 'bg-red-500' }
}

function SourceFreshnessPanel({ data }: { data: SourceFreshnessResponse | null }) {
    if (!data || data.entries.length === 0) {
        return (
            <div className="py-8 text-center text-sm text-slate-400">
                <Activity className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                数据源新鲜度报告未生成
            </div>
        )
    }

    const s = data.summary
    const hasFailures = s.red_count > 0
    const hasWarnings = s.yellow_count > 0

    let bannerText: string
    let bannerCls: string
    let bannerIcon: string
    if (hasFailures) {
        bannerIcon = '\u274C'
        bannerText = `${s.red_count} 个数据源故障`
        bannerCls = 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
    } else if (hasWarnings) {
        bannerIcon = '\u26A0\uFE0F'
        bannerText = `${s.yellow_count} 个数据源有警告`
        bannerCls = 'bg-yellow-50 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300'
    } else {
        bannerIcon = '\u2705'
        bannerText = '全部数据源正常'
        bannerCls = 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
    }

    return (
        <div className="space-y-4 p-4">
            {/* Banner */}
            <div className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium ${bannerCls}`}>
                <span>{bannerIcon}</span>
                {bannerText}
                {data.runtime_tier_meta && (
                    <span className="ml-auto">
                        <RuntimeTierBadge tier={data.runtime_tier_meta.runtime_tier} latency={data.runtime_tier_meta.expected_latency} />
                    </span>
                )}
            </div>

            {/* Summary stats */}
            <div className="flex flex-wrap gap-3 text-xs">
                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                    正常 {s.green_count}
                </span>
                <span className="rounded-full bg-yellow-100 px-2.5 py-1 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300">
                    警告 {s.yellow_count}
                </span>
                <span className="rounded-full bg-red-100 px-2.5 py-1 text-red-700 dark:bg-red-900/40 dark:text-red-300">
                    故障 {s.red_count}
                </span>
                {s.fallback_triggered_count > 0 && (
                    <span className="rounded-full bg-blue-100 px-2.5 py-1 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                        Fallback {s.fallback_triggered_count}
                    </span>
                )}
                {data.report_date && <span className="text-slate-500">日期: {data.report_date}</span>}
            </div>

            {/* Source table */}
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                            <th className="px-4 py-2.5 font-medium">数据源</th>
                            <th className="px-4 py-2.5 font-medium">状态</th>
                            <th className="px-4 py-2.5 font-medium">主源</th>
                            <th className="px-4 py-2.5 font-medium">实际源</th>
                            <th className="px-4 py-2.5 font-medium">Fallback</th>
                            <th className="px-4 py-2.5 font-medium">最新日期</th>
                            <th className="px-4 py-2.5 font-medium">记录数</th>
                            <th className="px-4 py-2.5 font-medium">限流风险</th>
                            <th className="px-4 py-2.5 font-medium">诊断</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.entries.map((e) => {
                            const badge = freshnessTrafficLight(e)
                            return (
                                <tr
                                    key={e.data_type}
                                    className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                >
                                    <td className="px-4 py-2.5 font-medium text-slate-700 dark:text-slate-300">{e.label}</td>
                                    <td className="px-4 py-2.5">
                                        <span className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
                                            <span className={`h-1.5 w-1.5 rounded-full ${badge.dot}`} />
                                            {badge.text}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-slate-600 dark:text-slate-400">{e.primary_vendor || '-'}</td>
                                    <td className="px-4 py-2.5 text-xs text-slate-600 dark:text-slate-400">
                                        {e.actual_vendor || '-'}
                                        {e.is_fallback && <span className="ml-1 text-blue-500">fallback</span>}
                                    </td>
                                    <td className="max-w-[160px] truncate px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400" title={e.fallback_chain.join(' -> ')}>
                                        {e.fallback_chain.length > 1 ? e.fallback_chain.join(' -> ') : '-'}
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">{e.as_of || e.latest_data_date || '-'}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{e.record_count}</td>
                                    <td className="px-4 py-2.5">
                                        {e.rate_limit_risk === 'high' ? (
                                            <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-600 dark:bg-red-900/40 dark:text-red-300">高</span>
                                        ) : e.rate_limit_risk === 'medium' ? (
                                            <span className="rounded bg-yellow-100 px-1.5 py-0.5 text-xs text-yellow-600 dark:bg-yellow-900/40 dark:text-yellow-300">中</span>
                                        ) : e.rate_limit_risk === 'low' ? (
                                            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-600 dark:bg-emerald-900/40 dark:text-emerald-300">低</span>
                                        ) : (
                                            <span className="text-xs text-slate-400">-</span>
                                        )}
                                    </td>
                                    <td className="max-w-[240px] truncate px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400" title={e.diagnosis}>
                                        {e.diagnosis || '-'}
                                    </td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

// [DATA-020] live_sampling_health_ui
//
// Layered status display for the live-sampling health daily report.
// Crucially distinguishes:
//   - SKIPPED          → grey "未启用实盘抽样" (never green)
//   - FAILED/RATE_LIMITED → red fault, with impacted-report-field hint
//   - NORMAL_NO_DATA   → grey "正常无数据"
//   - HAS_DATA         → green
//   - fallback entries → blue accent
function liveSamplingStatusBadge(r: LiveSamplingResult): { text: string; cls: string; dot: string } {
    // Skipped must never look healthy — it means live smoke was NOT executed.
    if (r.status === 'SKIPPED') {
        return {
            text: '未启用实盘抽样',
            cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400',
            dot: 'bg-slate-400',
        }
    }
    // Red faults
    if (r.status === 'FAILED' || r.status === 'RATE_LIMITED') {
        return {
            text: r.status_label_cn || '故障',
            cls: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
            dot: 'bg-red-500',
        }
    }
    // Grey "normal no data" — queried successfully, stock just has no such data
    if (r.status === 'NORMAL_NO_DATA') {
        return {
            text: r.status_label_cn || '正常无数据',
            cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400',
            dot: 'bg-slate-400',
        }
    }
    // Yellow warnings
    if (r.status === 'STALE' || r.status === 'UNIT_UNVERIFIED') {
        return {
            text: r.status_label_cn || '警告',
            cls: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300',
            dot: 'bg-yellow-500',
        }
    }
    // Green healthy
    return {
        text: r.status_label_cn || '正常',
        cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
        dot: 'bg-emerald-500',
    }
}

// [DATA-020] live_sampling_health_ui
// Short hint about which TA report fields a data-type failure impacts.
const LIVE_SAMPLING_FIELD_IMPACT: Record<string, string> = {
    quote: '影响：实时行情、技术分析、盘中观察触发',
    fund_flow: '影响：主力资金、资金面判断',
    lhb: '影响：龙虎榜、游资动向',
    notice: '影响：公告/监管事件、风险提示',
    rating: '影响：机构评级',
    buyback: '影响：回购数据',
    report: '影响：机构研报、研报观点',
}

function LiveSamplingPanel({ data }: { data: LiveSamplingResponse | null }) {
    // No report at all — clean empty state, never an error.
    if (!data || !data.has_report) {
        return (
            <div className="py-8 text-center text-sm text-slate-400">
                <Activity className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                实盘抽样报告未生成
                <div className="mt-2 text-xs">
                    运行 <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">TA_LIVE_DATA_SMOKE=1 python scripts/run_live_sampling.py</code> 生成
                </div>
            </div>
        )
    }

    const s = data.summary
    const skippedOnly = !!s.skipped_only

    // Banner reflects layered status — skipped-only is NEVER green.
    let bannerIcon: string
    let bannerText: string
    let bannerCls: string
    if (skippedOnly) {
        bannerIcon = '\u23F8\uFE0F'
        bannerText = '实盘抽样未启用（全部检查为 SKIPPED，不可视为健康）'
        bannerCls = 'bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
    } else if (s.red_count > 0) {
        bannerIcon = '\u274C'
        bannerText = `实盘抽样发现 ${s.red_count} 个故障`
        bannerCls = 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
    } else if (s.yellow_count > 0) {
        bannerIcon = '\u26A0\uFE0F'
        bannerText = `实盘抽样有 ${s.yellow_count} 个警告`
        bannerCls = 'bg-yellow-50 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300'
    } else {
        bannerIcon = '\u2705'
        bannerText = '实盘抽样全部通过'
        bannerCls = 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
    }

    return (
        <div className="space-y-4 p-4">
            {/* Banner */}
            <div className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium ${bannerCls}`}>
                <span>{bannerIcon}</span>
                {bannerText}
                {data.runtime_tier_meta && (
                    <span className="ml-auto">
                        <RuntimeTierBadge tier={data.runtime_tier_meta.runtime_tier} latency={data.runtime_tier_meta.expected_latency} />
                    </span>
                )}
            </div>

            {/* Summary stats — skipped and fallback are first-class layers */}
            <div className="flex flex-wrap gap-3 text-xs">
                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                    正常 {s.green_count}
                </span>
                <span className="rounded-full bg-yellow-100 px-2.5 py-1 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300">
                    警告 {s.yellow_count}
                </span>
                <span className="rounded-full bg-red-100 px-2.5 py-1 text-red-700 dark:bg-red-900/40 dark:text-red-300">
                    故障 {s.red_count}
                </span>
                <span className="rounded-full bg-slate-200 px-2.5 py-1 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                    跳过 {s.skipped_count}
                </span>
                {s.fallback_triggered_count > 0 && (
                    <span className="rounded-full bg-blue-100 px-2.5 py-1 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                        Fallback {s.fallback_triggered_count}
                    </span>
                )}
                {data.report_date && <span className="text-slate-500">抽样日期: {data.report_date}</span>}
                {data.env_gated && <span className="text-slate-400">（未开启 live smoke）</span>}
            </div>

            {/* Per-data-type summary */}
            {Object.keys(s.by_data_type).length > 0 && (
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                <th className="px-4 py-2.5 font-medium">数据类型</th>
                                <th className="px-4 py-2.5 font-medium">总数</th>
                                <th className="px-4 py-2.5 font-medium">正常</th>
                                <th className="px-4 py-2.5 font-medium">警告</th>
                                <th className="px-4 py-2.5 font-medium">故障</th>
                                <th className="px-4 py-2.5 font-medium">跳过</th>
                            </tr>
                        </thead>
                        <tbody>
                            {Object.entries(s.by_data_type).map(([dt, stat]: [string, any]) => (
                                <tr key={dt} className="border-b border-slate-50 dark:border-slate-800">
                                    <td className="px-4 py-2.5 font-medium text-slate-700 dark:text-slate-300">{stat.label || dt}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-600 dark:text-slate-300">{stat.total}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-emerald-600 dark:text-emerald-400">{stat.green}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-yellow-600 dark:text-yellow-400">{stat.yellow}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-red-600 dark:text-red-400">{stat.red}</td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-500 dark:text-slate-400">{stat.skipped}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Detailed results — layered by status */}
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                            <th className="px-4 py-2.5 font-medium">数据类型</th>
                            <th className="px-4 py-2.5 font-medium">股票</th>
                            <th className="px-4 py-2.5 font-medium">状态</th>
                            <th className="px-4 py-2.5 font-medium">实际源</th>
                            <th className="px-4 py-2.5 font-medium">Fallback</th>
                            <th className="px-4 py-2.5 font-medium">记录数</th>
                            <th className="px-4 py-2.5 font-medium">影响</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.results.map((r, idx) => {
                            const badge = liveSamplingStatusBadge(r)
                            const impactHint = (r.status === 'FAILED' || r.status === 'RATE_LIMITED')
                                ? LIVE_SAMPLING_FIELD_IMPACT[r.data_type] || ''
                                : ''
                            return (
                                <tr
                                    key={`${r.data_type}-${r.symbol}-${idx}`}
                                    className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                >
                                    <td className="px-4 py-2.5 font-medium text-slate-700 dark:text-slate-300">{r.data_type_label || r.data_type}</td>
                                    <td className="px-4 py-2.5 text-xs text-slate-600 dark:text-slate-400">
                                        <div className="font-mono">{r.symbol}</div>
                                        {r.symbol_name && <div className="text-slate-400">{r.symbol_name}</div>}
                                    </td>
                                    <td className="px-4 py-2.5">
                                        <span className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
                                            <span className={`h-1.5 w-1.5 rounded-full ${badge.dot}`} />
                                            {badge.text}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-slate-600 dark:text-slate-400">
                                        {r.actual_vendor || r.primary_vendor || '-'}
                                        {r.is_fallback && <span className="ml-1 text-blue-500">fallback</span>}
                                    </td>
                                    <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">
                                        {r.is_fallback ? `${r.primary_vendor || '-'} → ${r.actual_vendor || '-'}` : '-'}
                                    </td>
                                    <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{r.record_count}</td>
                                    <td className="max-w-[260px] px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400" title={r.diagnosis || impactHint}>
                                        {impactHint || r.diagnosis || '-'}
                                    </td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

// [H-013] mandate_topic_heatmap
function heatTrendBadge(trend: string): { text: string; cls: string } {
    switch (trend) {
        case 'RISING': return { text: '升温', cls: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' }
        case 'COOLING': return { text: '降温', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
        case 'STABLE': return { text: '平稳', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300' }
        default: return { text: '未知', cls: 'bg-slate-100 text-slate-400 dark:bg-slate-700 dark:text-slate-500' }
    }
}

function topicStatusBadge(status: string): { text: string; cls: string } {
    switch (status) {
        case 'BREWING': return { text: '酝酿', cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300' }
        case 'FERMENTING': return { text: '发酵', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' }
        case 'CONFIRMING': return { text: '确认', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
        case 'DELIVERING': return { text: '兑现', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' }
        case 'RECEDING': return { text: '退潮', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' }
        default: return { text: '未知', cls: 'bg-slate-100 text-slate-400 dark:bg-slate-700 dark:text-slate-500' }
    }
}

function heatBar(heat: number): string {
    const n = Math.max(0, Math.min(10, Math.round(heat / 10)))
    return '█'.repeat(n) + '░'.repeat(10 - n)
}

function TopicHeatmapPanel({
    data,
    onSelectSymbol,
    onGenerateCandidates,  // [UI-013] tradeflow_empty_state_cta
}: {
    data: TopicHeatmapResponse | null
    onSelectSymbol?: (symbol: string) => void
    onGenerateCandidates?: () => void
}) {
    if (!data || data.status === 'no_data') {
        return (
            <EmptyStateCTA
                icon={Flame}
                title="暂无主题热度数据"
                desc="主题热度由候选池汇总而来，生成候选池后将自动展示。"
                hints={[
                    '热度图只做排序解释，不改变最终动作',
                    '若已有候选池，可尝试刷新当前 Tab',
                ]}
                actions={onGenerateCandidates ? [
                    { label: '前往生成候选池', onClick: onGenerateCandidates, primary: true },
                ] : undefined}
            />
        )
    }

    const activeTopics = data.topics.filter(t => t.candidates.length > 0 || t.heat_curve.length > 0)

    return (
        <div className="space-y-4">
            {/* summary banner */}
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-800/50">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-300">日期 {data.as_of || '-'}</span>
                <span className="text-xs text-slate-400">|</span>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                    主题 <b className="text-slate-700 dark:text-slate-300">{data.total_topics}</b>
                </span>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                    活跃 <b className="text-emerald-600 dark:text-emerald-400">{data.active_topics}</b>
                </span>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                    升温 <b className="text-red-600 dark:text-red-400">{data.rising_topics}</b>
                </span>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                    降温 <b className="text-emerald-600 dark:text-emerald-400">{data.cooling_topics}</b>
                </span>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                    左侧 <b className="text-indigo-600 dark:text-indigo-400">{data.left_side_topics}</b>
                </span>
                {data.runtime_tier_meta && (
                    <span className="ml-auto text-xs text-slate-400">主题热度仅影响排序解释，不改变最终动作</span>
                )}
            </div>

            {/* topic cards */}
            {activeTopics.length === 0 ? (
                <div className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-700 dark:bg-slate-800/50">
                    <p className="text-sm text-slate-500 dark:text-slate-400">当前窗口暂无活跃主题信号。</p>
                </div>
            ) : (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    {activeTopics.map((topic) => (
                        <TopicHeatmapCard key={topic.topic} topic={topic} onSelectSymbol={onSelectSymbol} />
                    ))}
                </div>
            )}
        </div>
    )
}

// [H-015] mandate_daily_report
function MandateDailyReportPanel({
    data,
    onGenerateCandidates,  // [UI-013] tradeflow_empty_state_cta
}: {
    data: MandateDailyReportResponse | null
    onGenerateCandidates?: () => void
}) {
    if (!data || data.status === 'no_data') {
        return (
            <EmptyStateCTA
                icon={Lightbulb}
                title="暂无昊天主题日报"
                desc="生成候选池后可自动汇总主题重心、升温/降温主题与主候选解释。"
                hints={[
                    '日报只做雷达摘要，不含具体买卖建议',
                ]}
                actions={onGenerateCandidates ? [
                    { label: '前往生成候选池', onClick: onGenerateCandidates, primary: true },
                ] : undefined}
            />
        )
    }
    const topicName = (item: Record<string, unknown>) => String(item.topic || '')
    const topicDesc = (item: Record<string, unknown>) => {
        const trend = String(item.heat_trend_label || '未知')
        const status = String(item.status_label || '未知')
        const change = String(item.state_change_label || '持平')
        return `${trend} / ${status} / ${change}`
    }

    return (
        <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-4 dark:border-indigo-500/20 dark:bg-indigo-500/5">
            <div className="flex flex-wrap items-center gap-3">
                <div>
                    <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">昊天主题日报</h3>
                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                        {data.as_of || '-'} · 升温 {data.rising_topics.length} · 降温 {data.cooling_topics.length} · 主候选 {data.main_candidates.length} · 观察候选 {data.observation_candidates.length}
                    </p>
                </div>
                <span className="ml-auto rounded-full bg-white/80 px-2 py-1 text-xs text-slate-500 ring-1 ring-slate-200 dark:bg-slate-900/30 dark:ring-slate-700">
                    只做雷达摘要
                </span>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
                <div className="rounded-lg bg-white/80 p-3 ring-1 ring-slate-200 dark:bg-slate-900/30 dark:ring-slate-700">
                    <div className="text-xs font-medium text-red-600 dark:text-red-300">升温主题</div>
                    <div className="mt-2 space-y-1">
                        {data.rising_topics.slice(0, 4).map((item, idx) => (
                            <div key={`${topicName(item)}-${idx}`} className="text-sm text-slate-700 dark:text-slate-300">
                                <span className="font-medium">{topicName(item) || '未知主题'}</span>
                                <span className="ml-2 text-xs text-slate-500">{topicDesc(item)}</span>
                            </div>
                        ))}
                        {!data.rising_topics.length && <p className="text-xs text-slate-400">暂无升温主题。</p>}
                    </div>
                </div>
                <div className="rounded-lg bg-white/80 p-3 ring-1 ring-slate-200 dark:bg-slate-900/30 dark:ring-slate-700">
                    <div className="text-xs font-medium text-emerald-600 dark:text-emerald-300">降温主题</div>
                    <div className="mt-2 space-y-1">
                        {data.cooling_topics.slice(0, 4).map((item, idx) => (
                            <div key={`${topicName(item)}-${idx}`} className="text-sm text-slate-700 dark:text-slate-300">
                                <span className="font-medium">{topicName(item) || '未知主题'}</span>
                                <span className="ml-2 text-xs text-slate-500">{topicDesc(item)}</span>
                            </div>
                        ))}
                        {!data.cooling_topics.length && <p className="text-xs text-slate-400">暂无降温主题。</p>}
                    </div>
                </div>
            </div>

            {!!data.main_candidates.length && (
                <div className="mt-3 rounded-lg bg-white/80 p-3 ring-1 ring-slate-200 dark:bg-slate-900/30 dark:ring-slate-700">
                    <div className="text-xs font-medium text-slate-600 dark:text-slate-300">主候选解释</div>
                    <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
                        {data.main_candidates.slice(0, 6).map(c => (
                            <div key={`${c.symbol}-${c.topic}`} className="rounded-md bg-slate-50 p-2 dark:bg-slate-800/60">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{c.name || c.symbol}</span>
                                    <span className="text-xs text-indigo-600 dark:text-indigo-300">{c.topic}</span>
                                </div>
                                <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{c.entry_reason}</p>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {!!data.evidence_gaps.length && (
                <div className="mt-3 rounded-lg bg-white/80 p-3 ring-1 ring-slate-200 dark:bg-slate-900/30 dark:ring-slate-700">
                    <div className="text-xs font-medium text-amber-600 dark:text-amber-300">证据缺口</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                        {data.evidence_gaps.slice(0, 8).map((item, idx) => (
                            <span key={idx} className="rounded-full bg-amber-50 px-2 py-1 text-xs text-amber-700 ring-1 ring-amber-100 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/20">
                                {String(item.topic || '未知主题')}：{Array.isArray(item.gaps) ? item.gaps.slice(0, 2).join('、') : ''}
                            </span>
                        ))}
                    </div>
                </div>
            )}
        </div>
    )
}

function TopicHeatmapCard({
    topic,
    onSelectSymbol,
}: {
    topic: TopicHeatmapEntry
    onSelectSymbol?: (symbol: string) => void
}) {
    const [expanded, setExpanded] = useState(false)
    const statusBd = topicStatusBadge(topic.topic_status)
    const trendBd = heatTrendBadge(topic.heat_trend)
    const w7 = topic.windows['7']
    const w20 = topic.windows['20']
    const w60 = topic.windows['60']

    return (
        <div className={`rounded-xl border bg-white p-4 dark:bg-slate-800/50 ${
            topic.is_left_side ? 'border-l-4 border-l-indigo-400 border-slate-200 dark:border-slate-700'
            : topic.is_observe_only ? 'border-l-4 border-l-slate-400 border-slate-200 dark:border-slate-700'
            : 'border-slate-200 dark:border-slate-700'
        }`}>
            {/* header */}
            <div className="mb-3 flex items-start justify-between">
                <div>
                    <div className="flex items-center gap-2">
                        <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200">{topic.topic}</h4>
                        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${statusBd.cls}`}>{statusBd.text}</span>
                        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${trendBd.cls}`}>{trendBd.text}</span>
                    </div>
                    {topic.description && (
                        <p className="mt-0.5 text-xs text-slate-400">{topic.description}</p>
                    )}
                </div>
                <button
                    onClick={() => setExpanded(!expanded)}
                    className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                >
                    {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    {expanded ? '收起' : '详情'}
                </button>
            </div>

            {/* state change */}
            {topic.state_change_label && topic.state_change_label !== '持平' && (
                <div className="mb-2 flex items-center gap-1 text-xs">
                    <TrendingUp className={`h-3.5 w-3.5 ${topic.state_change_positive ? 'text-red-500' : 'text-emerald-500'}`} />
                    <span className={topic.state_change_positive ? 'text-red-600 dark:text-red-400' : 'text-emerald-600 dark:text-emerald-400'}>
                        {topic.state_change_label}
                    </span>
                </div>
            )}

            {/* windowed stats */}
            <div className="mb-3 grid grid-cols-3 gap-2">
                {[w7, w20, w60].filter(Boolean).map((w) => (
                    <div key={w!.window_days} className="rounded-lg bg-slate-50 px-2 py-1.5 text-center dark:bg-slate-900/40">
                        <div className="text-xs text-slate-400">{w!.window_label}</div>
                        <div className="text-sm font-semibold text-slate-700 dark:text-slate-300">{w!.candidate_count}只</div>
                        <div className="text-xs text-slate-400">证据 {w!.evidence_count}</div>
                    </div>
                ))}
            </div>

            {/* heat curve (mini) */}
            {topic.heat_curve.length > 0 && (
                <div className="mb-3">
                    <div className="mb-1 text-xs text-slate-400">热度曲线（近{topic.heat_curve.length}个信号日）</div>
                    <div className="space-y-0.5 font-mono text-xs">
                        {topic.heat_curve.slice(-6).map((p) => (
                            <div key={p.date} className="flex items-center gap-2">
                                <span className="w-20 text-slate-400">{p.date}</span>
                                <span className="text-amber-500">{heatBar(p.heat)}</span>
                                <span className="w-8 tabular-nums text-slate-500">{p.heat.toFixed(0)}</span>
                                <span className="text-slate-400">{p.candidate_count}只</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* counter evidence gaps */}
            {topic.counter_evidence_gaps.length > 0 && (
                <div className="mb-2 flex flex-wrap items-center gap-1">
                    <ShieldAlert className="h-3.5 w-3.5 text-amber-500" />
                    {topic.counter_evidence_gaps.slice(0, 3).map((g, i) => (
                        <span key={i} className="rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-600 dark:bg-amber-900/30 dark:text-amber-300">{g}</span>
                    ))}
                </div>
            )}

            {/* overheat flags */}
            {topic.overheat_flags.length > 0 && (
                <div className="mb-2 flex flex-wrap items-center gap-1">
                    <AlertCircle className="h-3.5 w-3.5 text-red-500" />
                    {topic.overheat_flags.map((f, i) => (
                        <span key={i} className="rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-600 dark:bg-red-900/30 dark:text-red-300">{f}</span>
                    ))}
                </div>
            )}

            {/* candidates with interlink */}
            {topic.candidates.length > 0 && (
                <div className="border-t border-slate-100 pt-2 dark:border-slate-700">
                    <div className="mb-1.5 text-xs text-slate-400">关联候选</div>
                    <div className="flex flex-wrap gap-1.5">
                        {topic.candidates.slice(0, 5).map((c) => (
                            <button
                                key={c.symbol}
                                onClick={() => onSelectSymbol?.(c.symbol)}
                                className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600 transition-colors hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-600 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-300 dark:hover:border-indigo-700 dark:hover:bg-indigo-900/30 dark:hover:text-indigo-300"
                                title={`${c.name} · ${c.company_role}`}
                            >
                                <span className="font-medium">{c.symbol}</span>
                                <span className="text-slate-400">{c.name}</span>
                                {c.tier && <span className="text-slate-400">·{c.tier}</span>}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* expanded: evidence links */}
            {expanded && topic.evidence_links.length > 0 && (
                <div className="mt-3 border-t border-slate-100 pt-2 dark:border-slate-700">
                    <div className="mb-1.5 text-xs text-slate-400">政策证据</div>
                    <ul className="space-y-1">
                        {topic.evidence_links.slice(0, 5).map((ref, i) => (
                            <li key={i} className="text-xs text-slate-500 dark:text-slate-400">
                                <span className="text-slate-400">[{ref.date}]</span> {ref.title}
                                <span className="ml-1 text-slate-400">({ref.source})</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    )
}

// [UI-010] mandate_candidate_compare
type CompareSortKey = 'mandate_score' | 'ambush_score' | 'evidence_coverage' | 'counter_evidence_count' | 'evidence_gap_count' | 'topic_lifecycle_state' | 'company_role'

const COMPARE_SORT_OPTIONS: { key: CompareSortKey; label: string; descLabel: string }[] = [
    { key: 'mandate_score', label: '政策强度', descLabel: '政策强度 ↓' },
    { key: 'ambush_score', label: '左侧埋伏分', descLabel: '埋伏分 ↓' },
    { key: 'evidence_coverage', label: '证据覆盖率', descLabel: '覆盖率 ↓' },
    { key: 'counter_evidence_count', label: '反证风险', descLabel: '反证风险 ↓' },
    { key: 'evidence_gap_count', label: '验证缺口', descLabel: '缺口最少 ↑' },
    { key: 'topic_lifecycle_state', label: '主题周期', descLabel: '主题周期' },
    { key: 'company_role', label: '公司角色', descLabel: '公司角色' },
]

function companyRoleLabel(role: string): { text: string; cls: string } {
    switch (role) {
        case 'LEADER': return { text: '标杆', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
        case 'CORE_SUPPLIER': return { text: '核心供应', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' }
        case 'INFRA_PROVIDER': return { text: '基础设施', cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300' }
        case 'APPLICATION_SCENE': return { text: '应用场景', cls: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300' }
        case 'PERIPHERAL': return { text: '外围', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300' }
        case 'CONCEPT_ONLY': return { text: '纯概念', cls: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300' }
        default: return { text: role || '--', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' }
    }
}

function topicLifecycleLabel(state: string): { text: string; cls: string } {
    switch (state) {
        case 'EMERGING': return { text: '萌芽', cls: 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300' }
        case 'ACCELERATING': return { text: '升温', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' }
        case 'CONFIRMING': return { text: '兑现', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
        case 'CROWDED': return { text: '拥挤', cls: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300' }
        case 'FADING': return { text: '退潮', cls: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300' }
        default: return { text: state || '未验证', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' }
    }
}

function counterEvidenceSummary(items: { rule?: string; severity?: number; description?: string }[] | undefined): string {
    if (!items || items.length === 0) return '无'
    const triggered = items.filter(i => i.severity && i.severity > 0)
    if (triggered.length === 0) return '无'
    return `${triggered.length} 项`
}

function gapsSummary(gaps: string[] | undefined): string {
    if (!gaps || gaps.length === 0) return '无'
    if (gaps.length <= 2) return gaps.join('、')
    return `${gaps.slice(0, 2).join('、')} +${gaps.length - 2}`
}

function CompareTab({ candidates, sortBy, onSortChange, onRowClick }: {
    candidates: TradeFlowCandidateItem[]
    sortBy: CompareSortKey
    onSortChange: (key: CompareSortKey) => void
    onRowClick: (c: TradeFlowCandidateItem) => void
}) {
    if (candidates.length === 0) {
        return (
            <div className="py-20 text-center text-sm text-slate-400">
                <div className="mb-3">暂无昊天候选数据</div>
                <div className="text-xs">请先在候选池中生成今日候选，或切换到昊天左侧池筛选</div>
            </div>
        )
    }

    return (
        <div className="p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-slate-500 dark:text-slate-400">排序:</span>
                {COMPARE_SORT_OPTIONS.map(opt => (
                    <button
                        key={opt.key}
                        onClick={() => onSortChange(opt.key)}
                        className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
                            sortBy === opt.key
                                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                                : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-400 dark:hover:bg-slate-600'
                        }`}
                    >
                        {sortBy === opt.key ? opt.descLabel : opt.label}
                    </button>
                ))}
            </div>

            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                            <th className="px-3 py-2.5 font-medium">代码</th>
                            <th className="px-3 py-2.5 font-medium">名称</th>
                            <th className="px-3 py-2.5 font-medium">类型</th>
                            <th className="px-3 py-2.5 font-medium">层级</th>
                            <th className="px-3 py-2.5 font-medium">昊天分</th>
                            <th className="px-3 py-2.5 font-medium">埋伏分</th>
                            <th className="px-3 py-2.5 font-medium">政策主题</th>
                            <th className="px-3 py-2.5 font-medium">公司角色</th>
                            <th className="px-3 py-2.5 font-medium">主题周期</th>
                            <th className="px-3 py-2.5 font-medium">覆盖率</th>
                            <th className="px-3 py-2.5 font-medium">反证</th>
                            <th className="px-3 py-2.5 font-medium">证据缺口</th>
                            <th className="px-3 py-2.5 font-medium">验证步骤</th>
                        </tr>
                    </thead>
                    <tbody>
                        {candidates.map(c => {
                            const ctBadge = candidateTypeLabel(c.candidate_type)
                            const roleBadge = companyRoleLabel(c.company_role)
                            const lifecycleBadge = topicLifecycleLabel(c.topic_lifecycle_state)
                            const ce = counterEvidenceSummary(c.counter_evidence as { rule?: string; severity?: number; description?: string }[] | undefined)
                            const gaps = gapsSummary(c.blocking_evidence_gaps)
                            const steps = gapsSummary(c.next_verification_steps)

                            return (
                                <tr
                                    key={c.symbol}
                                    onClick={() => onRowClick(c)}
                                    className="cursor-pointer border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                >
                                    <td className="px-3 py-2.5 font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{c.symbol}</td>
                                    <td className="max-w-[80px] truncate px-3 py-2.5 text-slate-700 dark:text-slate-300">{c.name || '--'}</td>
                                    <td className="px-3 py-2.5">
                                        <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${ctBadge.cls}`}>{ctBadge.text}</span>
                                    </td>
                                    <td className="px-3 py-2.5">
                                        <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${tierBadgeClass(c.tier)}`}>{c.tier || '--'}</span>
                                    </td>
                                    <td className="px-3 py-2.5 tabular-nums text-xs font-medium text-slate-700 dark:text-slate-300">{(c.mandate_score || 0).toFixed(1)}</td>
                                    <td className="px-3 py-2.5 tabular-nums text-xs font-medium text-red-600 dark:text-red-400">{(c.ambush_score || 0).toFixed(1)}</td>
                                    <td className="max-w-[100px] truncate px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400" title={c.mandate_topic}>{c.mandate_topic || '--'}</td>
                                    <td className="px-3 py-2.5">
                                        <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${roleBadge.cls}`}>{roleBadge.text}</span>
                                    </td>
                                    <td className="px-3 py-2.5">
                                        <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${lifecycleBadge.cls}`}>{lifecycleBadge.text}</span>
                                    </td>
                                    <td className="px-3 py-2.5"><CompletenessBar value={c.tradeflow_data_completeness} /></td>
                                    <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">{ce}</td>
                                    <td className="max-w-[120px] truncate px-3 py-2.5 text-xs text-amber-600 dark:text-amber-400" title={(c.blocking_evidence_gaps || []).join('、')}>{gaps}</td>
                                    <td className="max-w-[120px] truncate px-3 py-2.5 text-xs text-blue-600 dark:text-blue-400" title={(c.next_verification_steps || []).join('、')}>{steps}</td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

// [M-009] tradeflow_watch_pool_panel — consolidated watch pool showing candidates with
// strategy tags, trigger/invalid prices, filter reasons, and TA requirements.
type WatchPoolView = 'watching' | 'need-ta' | 'filtered'

function WatchPoolTab({
    candidates,
    filteredItems,
    onRowClick,
    onNavigateToAnalysis,
}: {
    candidates: TradeFlowCandidateItem[]
    filteredItems: TradeFlowFilteredItem[]
    onRowClick: (c: TradeFlowCandidateItem) => void
    onNavigateToAnalysis: (url: string) => void
}) {
    const [view, setView] = useState<WatchPoolView>('watching')
    const [strategyFilter, setStrategyFilter] = useState('')

    const strategies = useMemo(() => {
        const set = new Set<string>()
        for (const c of candidates) {
            for (const t of c.strategy_tags ?? []) {
                if (t) set.add(t)
            }
        }
        return Array.from(set).sort()
    }, [candidates])

    const watching = useMemo(() => {
        let items = candidates.filter(c => c.observe_state !== 'INVALIDATED' && c.observe_state !== 'EXPIRED')
        if (strategyFilter) {
            items = items.filter(c => (c.strategy_tags ?? []).includes(strategyFilter))
        }
        return items.sort((a, b) => (b.composite_score || b.score || 0) - (a.composite_score || a.score || 0))
    }, [candidates, strategyFilter])

    const needTa = useMemo(() =>
        candidates.filter(c => c.need_deep_ta).sort((a, b) => (b.ta_budget_priority || 0) - (a.ta_budget_priority || 0)),
        [candidates],
    )

    const isEmpty = candidates.length === 0 && filteredItems.length === 0

    if (isEmpty) {
        return (
            <div className="py-16 text-center text-sm text-slate-400">
                <Crosshair className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                尚无观察池数据
                <div className="mt-2 text-xs text-slate-400">请先在「候选池」生成今日候选</div>
            </div>
        )
    }

    return (
        <div className="space-y-4 p-4">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                <div className="card p-3">
                    <div className="text-xs text-slate-500 dark:text-slate-400">观察中</div>
                    <div className="mt-1 text-xl font-semibold tabular-nums text-blue-600 dark:text-blue-400">{watching.length}</div>
                </div>
                <div className="card p-3">
                    <div className="text-xs text-slate-500 dark:text-slate-400">已触发</div>
                    <div className="mt-1 text-xl font-semibold tabular-nums text-red-600 dark:text-red-400">
                        {candidates.filter(c => c.observe_state === 'TRIGGERED').length}
                    </div>
                </div>
                <div className="card p-3">
                    <div className="text-xs text-slate-500 dark:text-slate-400">需深度TA</div>
                    <div className="mt-1 text-xl font-semibold tabular-nums text-purple-600 dark:text-purple-400">{needTa.length}</div>
                </div>
                <div className="card p-3">
                    <div className="text-xs text-slate-500 dark:text-slate-400">被过滤</div>
                    <div className="mt-1 text-xl font-semibold tabular-nums text-amber-600 dark:text-amber-400">{filteredItems.length}</div>
                </div>
                <div className="card p-3">
                    <div className="text-xs text-slate-500 dark:text-slate-400">策略标签数</div>
                    <div className="mt-1 text-xl font-semibold tabular-nums text-slate-700 dark:text-slate-200">{strategies.length}</div>
                </div>
            </div>

            {/* View Toggle + Filters */}
            <div className="flex flex-wrap items-center gap-3">
                <div className="flex gap-1">
                    {([
                        { key: 'watching' as WatchPoolView, label: '观察中', count: watching.length },
                        { key: 'need-ta' as WatchPoolView, label: '需深度TA', count: needTa.length },
                        { key: 'filtered' as WatchPoolView, label: '被过滤', count: filteredItems.length },
                    ]).map(tab => (
                        <button
                            key={tab.key}
                            onClick={() => setView(tab.key)}
                            className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                                view === tab.key
                                    ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-400 dark:hover:bg-slate-600'
                            }`}
                        >
                            {tab.label} ({tab.count})
                        </button>
                    ))}
                </div>
                {view === 'watching' && strategies.length > 0 && (
                    <>
                        <span className="text-slate-300 dark:text-slate-600">|</span>
                        <select
                            value={strategyFilter}
                            onChange={e => setStrategyFilter(e.target.value)}
                            className="rounded border border-slate-200 bg-white px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                        >
                            <option value="">全部策略</option>
                            {strategies.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                    </>
                )}
            </div>

            {/* Watching Table */}
            {view === 'watching' && (
                <div className="card">
                    {watching.length === 0 ? (
                        <div className="py-12 text-center text-sm text-slate-400">无观察中候选</div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                        <th className="px-4 py-2.5 font-medium">代码/名称</th>
                                        <th className="px-4 py-2.5 font-medium">候选类型</th>
                                        <th className="px-4 py-2.5 font-medium">策略标签</th>
                                        <th className="px-4 py-2.5 font-medium">触发价</th>
                                        <th className="px-4 py-2.5 font-medium">失效价</th>
                                        <th className="px-4 py-2.5 font-medium">观察状态</th>
                                        <th className="px-4 py-2.5 font-medium">需TA</th>
                                        <th className="px-4 py-2.5 font-medium">综合分</th>
                                        <th className="px-4 py-2.5 font-medium">操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {watching.map(c => {
                                        const ct = candidateTypeLabel(c.candidate_type)
                                        const obs = observeStateLabel(c.observe_state)
                                        const tags = c.strategy_tags ?? []
                                        return (
                                            <tr
                                                key={c.symbol}
                                                className="cursor-pointer border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                                onClick={() => onRowClick(c)}
                                            >
                                                <td className="px-4 py-2.5">
                                                    <div className="font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{c.symbol}</div>
                                                    <div className="max-w-[100px] truncate text-xs text-slate-500 dark:text-slate-400">{c.name || '--'}</div>
                                                </td>
                                                <td className="px-4 py-2.5">
                                                    <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${ct.cls}`}>{ct.text}</span>
                                                </td>
                                                <td className="px-4 py-2.5">
                                                    <div className="flex flex-wrap gap-1">
                                                        {tags.length > 0 ? tags.slice(0, 3).map(t => (
                                                            <span key={t} className="inline-block rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400">{t}</span>
                                                        )) : <span className="text-xs text-slate-400">-</span>}
                                                        {tags.length > 3 && <span className="text-[10px] text-slate-400">+{tags.length - 3}</span>}
                                                    </div>
                                                </td>
                                                <td className="px-4 py-2.5 font-mono text-xs text-red-600 dark:text-red-400">{fmtPrice(c.trigger_price)}</td>
                                                <td className="px-4 py-2.5 font-mono text-xs text-emerald-600 dark:text-emerald-400">{fmtPrice(c.invalid_price)}</td>
                                                <td className="px-4 py-2.5">
                                                    <span className={`text-xs font-medium ${obs.cls}`}>{obs.text}</span>
                                                </td>
                                                <td className="px-4 py-2.5">
                                                    {c.need_deep_ta ? (
                                                        <span className="inline-block rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-medium text-purple-700 dark:bg-purple-900/40 dark:text-purple-300">需要</span>
                                                    ) : (
                                                        <span className="text-xs text-slate-400">-</span>
                                                    )}
                                                </td>
                                                <td className="px-4 py-2.5 font-mono text-xs text-slate-700 dark:text-slate-300">
                                                    {(c.composite_score || c.score || 0).toFixed(1)}
                                                </td>
                                                <td className="px-4 py-2.5">
                                                    <button
                                                        onClick={e => { e.stopPropagation(); onNavigateToAnalysis(`/analysis?symbol=${c.symbol}&horizon=medium&intent=entry`) }}
                                                        className="rounded bg-blue-50 px-2 py-1 text-[10px] font-medium text-blue-600 hover:bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400 dark:hover:bg-blue-900/50"
                                                    >
                                                        轻量TA
                                                    </button>
                                                </td>
                                            </tr>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* Need Deep TA Table */}
            {view === 'need-ta' && (
                <div className="card">
                    {needTa.length === 0 ? (
                        <div className="py-12 text-center text-sm text-slate-400">暂无需要深度TA的候选</div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                        <th className="px-4 py-2.5 font-medium">代码/名称</th>
                                        <th className="px-4 py-2.5 font-medium">候选类型</th>
                                        <th className="px-4 py-2.5 font-medium">触发价</th>
                                        <th className="px-4 py-2.5 font-medium">失效价</th>
                                        <th className="px-4 py-2.5 font-medium">优先级</th>
                                        <th className="px-4 py-2.5 font-medium">深度TA原因</th>
                                        <th className="px-4 py-2.5 font-medium">操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {needTa.map(c => {
                                        const ct = candidateTypeLabel(c.candidate_type)
                                        return (
                                            <tr
                                                key={c.symbol}
                                                className="cursor-pointer border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                                onClick={() => onRowClick(c)}
                                            >
                                                <td className="px-4 py-2.5">
                                                    <div className="font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{c.symbol}</div>
                                                    <div className="max-w-[100px] truncate text-xs text-slate-500 dark:text-slate-400">{c.name || '--'}</div>
                                                </td>
                                                <td className="px-4 py-2.5">
                                                    <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${ct.cls}`}>{ct.text}</span>
                                                </td>
                                                <td className="px-4 py-2.5 font-mono text-xs text-red-600 dark:text-red-400">{fmtPrice(c.trigger_price)}</td>
                                                <td className="px-4 py-2.5 font-mono text-xs text-emerald-600 dark:text-emerald-400">{fmtPrice(c.invalid_price)}</td>
                                                <td className="px-4 py-2.5 font-mono text-xs text-slate-700 dark:text-slate-300">{c.ta_budget_priority || 0}</td>
                                                <td className="max-w-[200px] truncate px-4 py-2.5 text-xs text-slate-600 dark:text-slate-400">{c.why_deep_ta || '-'}</td>
                                                <td className="px-4 py-2.5">
                                                    <button
                                                        onClick={e => { e.stopPropagation(); onNavigateToAnalysis(`/analysis?symbol=${c.symbol}&horizon=medium&intent=entry`) }}
                                                        className="rounded bg-purple-50 px-2 py-1 text-[10px] font-medium text-purple-600 hover:bg-purple-100 dark:bg-purple-900/30 dark:text-purple-400"
                                                    >
                                                        发起TA
                                                    </button>
                                                </td>
                                            </tr>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* Filtered Table */}
            {view === 'filtered' && (
                <div className="card">
                    {filteredItems.length === 0 ? (
                        <div className="py-12 text-center text-sm text-slate-400">暂无被过滤股票</div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                        <th className="px-4 py-2.5 font-medium">代码</th>
                                        <th className="px-4 py-2.5 font-medium">名称</th>
                                        <th className="px-4 py-2.5 font-medium">来源</th>
                                        <th className="px-4 py-2.5 font-medium">过滤原因</th>
                                        <th className="px-4 py-2.5 font-medium">扫描时间</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {filteredItems.map(item => {
                                        const reason = item.reason
                                        let reasonCls = 'text-slate-600 dark:text-slate-400'
                                        if (reason.includes('流动性')) reasonCls = 'text-amber-600 dark:text-amber-400'
                                        else if (reason.includes('数据')) reasonCls = 'text-red-500 dark:text-red-400'
                                        else if (reason.includes('无策略')) reasonCls = 'text-slate-500 dark:text-slate-400'
                                        return (
                                            <tr
                                                key={`${item.symbol}-${item.reason}`}
                                                className="border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                            >
                                                <td className="px-4 py-2.5 font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{item.symbol}</td>
                                                <td className="max-w-[120px] truncate px-4 py-2.5 text-slate-700 dark:text-slate-300">{item.name || '--'}</td>
                                                <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">{item.source || '-'}</td>
                                                <td className={`px-4 py-2.5 text-xs font-medium ${reasonCls}`}>{reason}</td>
                                                <td className="px-4 py-2.5 text-xs text-slate-400">{item.created_at ? new Date(item.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'}</td>
                                            </tr>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}

export default function TradeFlow() {
    const navigate = useNavigate()  // [TA-UI-001] analysis_console_horizon_intent
    const [activeTab, setActiveTab] = useState<TabKey>('candidates')
    const [tradeDate, setTradeDate] = useState(todayStr)
    const [candidates, setCandidates] = useState<TradeFlowCandidateItem[]>([])
    const [summary, setSummary] = useState<TradeFlowSummary>({
        total_candidates: 0, tier_a_count: 0, tier_b_count: 0, tier_c_count: 0,
        need_deep_ta_count: 0, avg_completeness: 0,
    })
    const [dataHealth, setDataHealth] = useState<TradeFlowDataHealthResponse | null>(null)
    const [sourceFreshness, setSourceFreshness] = useState<SourceFreshnessResponse | null>(null)  // [DATA-018]
    const [liveSampling, setLiveSampling] = useState<LiveSamplingResponse | null>(null)  // [DATA-020] live_sampling_health_ui
    const [topicHeatmap, setTopicHeatmap] = useState<TopicHeatmapResponse | null>(null)  // [H-013] mandate_topic_heatmap
    const [mandateDailyReport, setMandateDailyReport] = useState<MandateDailyReportResponse | null>(null)  // [H-015] mandate_daily_report
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [status, setStatus] = useState<string>('')
    const [scanLoading, setScanLoading] = useState(false)
    const [scanSymbols, setScanSymbols] = useState('')
    const [scanTopN, setScanTopN] = useState(20)
    const [scanUseEventSource, setScanUseEventSource] = useState(false)
    const [scanIncludeWatchlist, setScanIncludeWatchlist] = useState(true)
    const [scanIncludeHoldings, setScanIncludeHoldings] = useState(true)
    const [scanMessage, setScanMessage] = useState('')

    const [tierFilter, setTierFilter] = useState<string>('')
    const [deepTaFilter, setDeepTaFilter] = useState<string>('')
    const [observeFilter, setObserveFilter] = useState<string>('')
    const [candidateTypeFilter] = useState<string>('')  // [H-005] mandate_radar_ui — kept for compatibility
    const [poolFilter, setPoolFilter] = useState<PoolKey>('all')  // [TF-P0-002] tradeflow_pool_split
    const [drawerOpen, setDrawerOpen] = useState(false)
    const [selectedCandidate, setSelectedCandidate] = useState<TradeFlowCandidateItem | null>(null)
    // [H-013A] mandate_topic_heatmap_fix — symbol carried over from topic heatmap
    const [highlightSymbol, setHighlightSymbol] = useState<string | null>(null)

    const [observeData, setObserveData] = useState<TradeFlowObserveResponse | null>(null)
    const [observeRunLoading, setObserveRunLoading] = useState(false)  // [TF-OBS-001]
    const [observeRunResult, setObserveRunResult] = useState<TradeFlowObserveRunResponse | null>(null)  // [TF-OBS-001]
    const [taQueueData, setTaQueueData] = useState<TradeFlowTAQueueResponse | null>(null)
    const [reviewData, setReviewData] = useState<TradeFlowReviewResponse | null>(null)
    const [filteredData, setFilteredData] = useState<TradeFlowFilteredResponse | null>(null)  // [UI-007] tradeflow_filtered_trace

    // [TF-UX-001] tiered candidates state
    const [tieredData, setTieredData] = useState<TradeFlowTieredCandidatesResponse | null>(null)
    const [showScan, setShowScan] = useState(false)
    const [viewMode, setViewMode] = useState<'focus' | 'tiered' | 'table'>('focus')  // [UI-012] default to focus workspace

    // [TF-UX-002] auto-refresh state
    // [TF-OBS-004] observe_refresh_alert_queue — countdown + server-driven interval
    const [lastObserveCheckTime, setLastObserveCheckTime] = useState<string | null>(null)
    const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null)
    const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null)
    const [nextRefreshIn, setNextRefreshIn] = useState<number | null>(null)
    const [autoRefreshActive, setAutoRefreshActive] = useState<boolean>(false)
    const [effectiveObserveRefreshSeconds, setEffectiveObserveRefreshSeconds] = useState<number>(180)

    // [TF-UX-003] review generation
    const [reviewGenerating, setReviewGenerating] = useState(false)

    // [UI-010] mandate_candidate_compare
    const [compareData, setCompareData] = useState<TradeFlowCandidateItem[]>([])
    const [compareSortBy, setCompareSortBy] = useState<CompareSortKey>('mandate_score')

    // [TF-PAPER-001] paper_trading_ledger
    const [paperLedger, setPaperLedger] = useState<PaperLedgerResponse | null>(null)
    const [paperActionLoading, setPaperActionLoading] = useState(false)

    // [TF-UX-002] check if market is in session
    const isInMarketHours = useCallback(() => {
        const now = new Date()
        const day = now.getDay()
        if (day === 0 || day === 6) return false
        const h = now.getHours()
        const m = now.getMinutes()
        const t = h * 60 + m
        return (t >= 570 && t <= 690) || (t >= 780 && t <= 900) // 9:30-11:30, 13:00-15:00
    }, [])

    const fetchCandidates = useCallback(async (date: string) => {
        setLoading(true)
        setError(null)
        try {
            const [candidatesRes, healthRes, tieredRes] = await Promise.all([
                api.getTradeFlowCandidates(date, tierFilter || undefined, deepTaFilter === 'yes' ? true : deepTaFilter === 'no' ? false : undefined, candidateTypeFilter || undefined, poolFilter !== 'all' ? poolFilter : undefined),
                api.getTradeFlowDataHealth(),
                api.getTradeFlowCandidatesTiered(date),
            ])
            setStatus(candidatesRes.status)
            if (candidatesRes.status === 'ok') {
                let items = candidatesRes.candidates
                if (observeFilter) {
                    items = items.filter(c => c.observe_state === observeFilter)
                }
                setCandidates(items)
                setSummary(candidatesRes.summary_agg)
            } else {
                setCandidates([])
                setSummary({ total_candidates: 0, tier_a_count: 0, tier_b_count: 0, tier_c_count: 0, need_deep_ta_count: 0, avg_completeness: 0 })
            }
            setDataHealth(healthRes)
            setTieredData(tieredRes)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }, [tierFilter, deepTaFilter, observeFilter, candidateTypeFilter, poolFilter])

    const fetchObserve = useCallback(async (date: string) => {
        setLoading(true)
        setError(null)
        try {
            const res = await api.getTradeFlowObserve(date)
            setObserveData(res)
            // [TF-OBS-002] observe_auto_run — sync last observed time from API
            if (res.last_observed_at) {
                setLastObserveCheckTime(res.last_observed_at)
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }, [])

    // [TF-OBS-001] tradeflow_observe_runner
    const handleRunObserve = useCallback(async () => {
        setObserveRunLoading(true)
        setObserveRunResult(null)
        try {
            const res = await api.runTradeFlowObserve(tradeDate)
            setObserveRunResult(res)
            setLastObserveCheckTime(new Date().toISOString())
            await fetchObserve(tradeDate)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '执行观察失败')
        } finally {
            setObserveRunLoading(false)
        }
    }, [tradeDate, fetchObserve])

    const fetchTaQueue = useCallback(async (date: string) => {
        setLoading(true)
        setError(null)
        try {
            const res = await api.getTradeFlowTaQueue(date)
            setTaQueueData(res)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }, [])

    const fetchReview = useCallback(async (date: string) => {
        setLoading(true)
        setError(null)
        try {
            const res = await api.getTradeFlowReview(date)
            setReviewData(res)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }, [])

    // [UI-010] mandate_candidate_compare
    const fetchCompare = useCallback(async (date: string) => {
        setLoading(true)
        setError(null)
        try {
            const res = await api.getTradeFlowCandidatesCompare(date, compareSortBy, 'desc', 'haotian')
            if (res.status === 'ok') {
                setCompareData(res.candidates)
            } else {
                setCompareData([])
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }, [compareSortBy])

    const fetchDataHealth = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const res = await api.getTradeFlowDataHealth()
            setDataHealth(res)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }, [])

    // [DATA-018] source_freshness_report
    const fetchSourceFreshness = useCallback(async () => {
        try {
            const res = await api.getSourceFreshness()
            setSourceFreshness(res)
        } catch {
            // silent fail — freshness is supplementary
        }
    }, [])

    // [DATA-020] live_sampling_health_ui
    const fetchLiveSampling = useCallback(async () => {
        try {
            const res = await api.getLiveSamplingReport()
            setLiveSampling(res)
        } catch {
            // silent fail — live sampling is supplementary
        }
    }, [])

    // [H-013] mandate_topic_heatmap
    const fetchTopicHeatmap = useCallback(async () => {
        try {
            const res = await api.getTopicHeatmap()
            setTopicHeatmap(res)
        } catch {
            // silent fail — heatmap is supplementary
        }
    }, [])

    // [H-015] mandate_daily_report
    const fetchMandateDailyReport = useCallback(async () => {
        try {
            const res = await api.getMandateDailyReport()
            setMandateDailyReport(res)
        } catch {
            // silent fail — mandate daily report is supplementary
        }
    }, [])

    // [UI-007] tradeflow_filtered_trace
    const fetchFiltered = useCallback(async (date: string) => {
        setLoading(true)
        setError(null)
        try {
            const res = await api.getTradeFlowFiltered(date)
            setFilteredData(res)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }, [])

    // [TF-UX-003] generate review
    // [TF-REVIEW-004] review_empty_diagnostics — surface failure reason
    const [reviewGenerateError, setReviewGenerateError] = useState<string>('')
    const handleGenerateReview = useCallback(async () => {
        setReviewGenerating(true)
        setReviewGenerateError('')
        try {
            const res = await api.generateTradeFlowReview(tradeDate)
            if (res.status === 'no_data') {
                // [TF-REVIEW-004] explain why generation could not proceed
                const reason = res.empty_reason_message || res.data_status_message || res.message || '生成复盘失败'
                setReviewGenerateError(reason)
            }
            await fetchReview(tradeDate)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '生成复盘失败')
        } finally {
            setReviewGenerating(false)
        }
    }, [tradeDate, fetchReview])

    // [TF-PAPER-001] paper_trading_ledger
    const fetchPaperLedger = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const res = await api.getPaperLedger()
            setPaperLedger(res)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载模拟账本失败')
        } finally {
            setLoading(false)
        }
    }, [])

    // [TF-UX-001] small_cap_trial_workbench — keep risk budget fresh while users
    // are on the focus workspace. Non-blocking: failures only clear the ledger
    // so the prompt area falls back to the "ledger missing" empty state.
    const refreshPaperLedgerForFocus = useCallback(async () => {
        try {
            const res = await api.getPaperLedger()
            setPaperLedger(res)
        } catch {
            // non-fatal — focus workspace still renders without budget data
        }
    }, [])

    // [TF-PAPER-001] paper_trading_ledger
    const handlePaperAction = useCallback(async (tradeId: number, actionType: string, price: number, note?: string) => {
        setPaperActionLoading(true)
        try {
            await api.confirmPaperAction(tradeId, actionType, price, note)
            await fetchPaperLedger()
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '操作失败')
        } finally {
            setPaperActionLoading(false)
        }
    }, [fetchPaperLedger])

    // [TF-PAPER-001] paper_trading_ledger
    const handlePaperRemove = useCallback(async (tradeId: number) => {
        setPaperActionLoading(true)
        try {
            await api.removePaperCandidate(tradeId)
            await fetchPaperLedger()
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '移除失败')
        } finally {
            setPaperActionLoading(false)
        }
    }, [fetchPaperLedger])

    const fetchData = useCallback(async (date: string) => {
        if (activeTab === 'candidates') {
            await fetchCandidates(date)
        } else if (activeTab === 'watch-pool') {
            // [M-009] tradeflow_watch_pool_panel — load both candidates and filtered data
            await fetchCandidates(date)
            await fetchFiltered(date)
        } else if (activeTab === 'compare') {
            await fetchCompare(date)
        } else if (activeTab === 'observe') {
            await fetchObserve(date)
        } else if (activeTab === 'ta-queue') {
            await fetchTaQueue(date)
        } else if (activeTab === 'review') {
            await fetchReview(date)
        } else if (activeTab === 'filtered') {
            await fetchFiltered(date)
        } else if (activeTab === 'data-health') {
            await fetchDataHealth()
            await fetchSourceFreshness()  // [DATA-018]
            await fetchLiveSampling()  // [DATA-020] live_sampling_health_ui
        } else if (activeTab === 'topic-heatmap') {  // [H-013] mandate_topic_heatmap
            await fetchTopicHeatmap()
            await fetchMandateDailyReport()
        } else if (activeTab === 'paper-ledger') {
            await fetchPaperLedger()
        }
    }, [activeTab, fetchCandidates, fetchCompare, fetchObserve, fetchTaQueue, fetchReview, fetchDataHealth, fetchSourceFreshness, fetchLiveSampling, fetchTopicHeatmap, fetchMandateDailyReport, fetchFiltered, fetchPaperLedger])

    useEffect(() => {
        void fetchData(tradeDate)
    }, [tradeDate, fetchData])

    // [TF-UX-001] small_cap_trial_workbench — preload the paper ledger so the
    // "5000 元试跑" prompt area in the focus workspace has risk budget data
    // without requiring users to navigate to the 模拟账本 tab first.
    useEffect(() => {
        if (activeTab === 'candidates') {
            void refreshPaperLedgerForFocus()
        }
    }, [activeTab, refreshPaperLedgerForFocus])

    // [TF-UX-002] auto-refresh for observe tab
    // [TF-OBS-004] observe_refresh_alert_queue — server-driven interval + 1s countdown ticker
    useEffect(() => {
        if (autoRefreshRef.current) {
            clearInterval(autoRefreshRef.current)
            autoRefreshRef.current = null
        }
        if (countdownRef.current) {
            clearInterval(countdownRef.current)
            countdownRef.current = null
        }
        // Respect speed budget: keep conservative intervals, do NOT add high-frequency polling.
        const inMarket = isInMarketHours()
        // Prefer server-authoritative interval when available; cap to existing budget (3min in-market / 5min off).
        const serverIntervalMs = (observeData?.refresh_interval_seconds ?? 0) * 1000
        const interval = inMarket
            ? Math.min(serverIntervalMs > 0 ? serverIntervalMs : 3 * 60 * 1000, 3 * 60 * 1000)
            : 5 * 60 * 1000
        const active = inMarket && activeTab === 'observe'
        setEffectiveObserveRefreshSeconds(Math.round(interval / 1000))
        setAutoRefreshActive(active)
        if (active) {
            setNextRefreshIn(Math.round(interval / 1000))
            autoRefreshRef.current = setInterval(() => {
                void fetchObserve(tradeDate)
                setNextRefreshIn(Math.round(interval / 1000))
            }, interval)
            countdownRef.current = setInterval(() => {
                setNextRefreshIn(prev => (prev == null ? null : Math.max(0, prev - 1)))
            }, 1000)
        } else {
            setNextRefreshIn(null)
        }
        return () => {
            if (autoRefreshRef.current) {
                clearInterval(autoRefreshRef.current)
            }
            if (countdownRef.current) {
                clearInterval(countdownRef.current)
            }
        }
    }, [activeTab, tradeDate, fetchObserve, isInMarketHours, observeData?.refresh_interval_seconds])

    // [H-013A] mandate_topic_heatmap_fix — scroll the carried symbol into view
    // (and clear it) once the candidates list has rendered it.
    useEffect(() => {
        if (!highlightSymbol) return
        if (activeTab !== 'candidates') return
        const el = document.querySelector(`[data-highlight-symbol="${highlightSymbol}"]`) as HTMLElement | null
        if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }
    }, [highlightSymbol, activeTab, candidates, tieredData])

    const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setTradeDate(e.target.value)
    }

    const handleRowClick = (c: TradeFlowCandidateItem) => {
        setSelectedCandidate(c)
        setDrawerOpen(true)
    }

    // [H-013A] mandate_topic_heatmap_fix — interlink: topic → candidates tab,
    // carrying the clicked symbol so the candidates list can highlight/expand it.
    const handleTopicSymbolClick = useCallback((symbol: string) => {
        setHighlightSymbol(symbol || null)
        setActiveTab('candidates')
    }, [])

    // [UI-013] tradeflow_empty_state_cta — soft next-step CTAs shared across empty states.
    // Wording stays neutral (no 买/卖/加仓/减仓). These only navigate, never trigger scans.
    const goToCandidatesTab = useCallback(() => {
        setActiveTab('candidates')
    }, [])
    const goToDataHealthTab = useCallback(() => {
        setActiveTab('data-health')
    }, [])
    const goToObservationWarehouse = useCallback(() => {
        navigate('/tracking-board')
    }, [navigate])
    const goToScanPanel = useCallback(() => {
        setActiveTab('candidates')
        // The scan panel is rendered above the tab content on the candidates tab.
        // Defer the scroll so the panel is mounted before we look it up.
        window.setTimeout(() => {
            const el = document.getElementById('tradeflow-scan-panel')
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' })
            }
        }, 60)
    }, [])

    const parseSymbolInput = (value: string) => (
        value
            .split(/[\s,，;；]+/)
            .map(s => s.trim().toUpperCase())
            .filter(Boolean)
    )

    const handleRunDiscovery = async () => {
        setScanLoading(true)
        setError(null)
        setScanMessage('')
        try {
            const res = await api.runTradeFlowDiscovery({
                date: tradeDate,
                symbols: parseSymbolInput(scanSymbols),
                top_n: scanTopN,
                include_holdings: scanIncludeHoldings,
                include_watchlist: scanIncludeWatchlist,
                use_event_source: scanUseEventSource,
                news_texts: [],
                save_candidates: true,
            })
            setScanMessage(`${res.summary} 已保存候选 ${res.candidate_count} 只，过滤 ${res.filtered_count} 只。`)
            setActiveTab('candidates')
            await fetchCandidates(tradeDate)
            await fetchFiltered(tradeDate)  // [UI-007] tradeflow_filtered_trace
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '筛选失败')
        } finally {
            setScanLoading(false)
        }
    }

    const handleDrawerClose = () => {
        setDrawerOpen(false)
    }

    // [UI-012] tradeflow_focus_workspace — dimension score chip
    const renderScoreChip = (label: string, value: number, highlight: boolean) => {
        const v = value || 0
        const cls = highlight
            ? 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
            : v > 0
            ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
            : 'bg-slate-50 text-slate-400 dark:bg-slate-800 dark:text-slate-500'
        return (
            <span className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums ${cls}`}>
                {label}: {v.toFixed(1)}
            </span>
        )
    }

    // [UI-012] tradeflow_focus_workspace — main candidate card with scores, reasons, next steps
    // [TF-UX-001] small_cap_trial_workbench — adds 综合分 + 风险预算占用 lines so each
    // card surfaces the candidate type, composite score, core trigger price, risk occupancy,
    // why selected and why-not-main reasons in a single glance.
    const renderMainCandidateCard = (c: TradeFlowCandidateItem, budget?: import('@/utils/tradeflowFocus').TrialBudgetView) => {
        const ct = candidateTypeLabel(c.candidate_type)
        const obs = observeStateLabel(c.observe_state)
        const isHaotian = c.candidate_type === 'POLICY_AMBUSH' || c.candidate_type === 'POLICY_CONFIRM'
        const poolBorderCls = isHaotian
            ? 'border-l-indigo-400 dark:border-l-indigo-600'
            : 'border-l-slate-300 dark:border-l-slate-600'
        // [H-013A] mandate_topic_heatmap_fix — highlight symbol carried from topic heatmap
        const isHighlighted = !!highlightSymbol && c.symbol === highlightSymbol
        const highlightCls = isHighlighted
            ? 'ring-2 ring-indigo-400 bg-indigo-50/60 dark:bg-indigo-900/20'
            : ''
        // [TF-UX-001] small_cap_trial_workbench — risk occupancy for this candidate
        const riskView = budget ? computeCandidateRiskView(c, paperLedger, budget) : null
        const whySelected = pickWhySelected(c, 2)

        return (
            <div
                key={c.symbol}
                data-highlight-symbol={isHighlighted ? c.symbol : undefined}
                className={`cursor-pointer rounded-lg border border-slate-100 border-l-4 ${poolBorderCls} p-3 transition-colors hover:border-blue-200 hover:bg-blue-50/30 dark:border-slate-700 dark:hover:border-blue-800 dark:hover:bg-blue-900/10 ${highlightCls}`}
                onClick={() => handleRowClick(c)}
            >
                {/* Row 1: symbol + name + type + tier + composite score */}
                <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-slate-900 dark:text-slate-100">{c.symbol}</span>
                    <span className="max-w-[80px] truncate text-sm font-medium text-slate-700 dark:text-slate-300">{c.name || '--'}</span>
                    <span className={`inline-block rounded px-1 py-0.5 text-[10px] font-medium ${ct.cls}`}>{ct.text}</span>
                    <span className={`inline-block rounded px-1 py-0.5 text-[10px] font-bold ${tierBadgeClass(c.tier)}`}>{c.tier || '-'}</span>
                    <span className="ml-auto flex items-baseline gap-1.5">
                        <span className="text-[9px] uppercase tracking-wide text-slate-400">综合</span>
                        <span className="text-sm font-bold tabular-nums text-slate-900 dark:text-slate-100">
                            {(c.composite_score ?? 0).toFixed(1)}
                        </span>
                        <span className="text-[9px] uppercase tracking-wide text-slate-400">优先</span>
                        <span className="text-xs font-semibold tabular-nums text-slate-600 dark:text-slate-300">
                            {(c.trade_priority_score ?? 0).toFixed(1)}
                        </span>
                    </span>
                </div>

                {/* Row 2: dimension scores */}
                <div className="mt-1.5 flex flex-wrap gap-1">
                    {renderScoreChip('形态', c.technical_score, false)}
                    {renderScoreChip('政策', c.policy_score, isHaotian)}
                    {renderScoreChip('资金', c.fund_flow_score, false)}
                    {renderScoreChip('事件', c.event_score, false)}
                    {renderScoreChip('风控', c.risk_penalty_score, c.risk_penalty_score > 20)}
                    {renderScoreChip('数据', c.data_quality_score, false)}
                </div>

                {/* Row 3: why selected (top 2) */}
                <div className="mt-1.5 space-y-0.5">
                    {whySelected.length > 0 ? (
                        whySelected.map((r, i) => (
                            <div key={i} className="flex items-start gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                                <TrendingUp className="mt-0.5 h-2.5 w-2.5 flex-shrink-0" />
                                <span className="truncate">{r}</span>
                            </div>
                        ))
                    ) : (
                        <div className="text-[11px] text-slate-400">暂无入选原因</div>
                    )}
                </div>

                {/* Row 4: core trigger price + risk budget occupancy + observe state */}
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
                    {c.trigger_price != null && (
                        <span className="rounded bg-red-50 px-1.5 py-0.5 font-medium text-red-700 dark:bg-red-900/30 dark:text-red-300">
                            核心触发 {fmtPrice(c.trigger_price)}
                        </span>
                    )}
                    {c.invalid_price != null && (
                        <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
                            失效 {fmtPrice(c.invalid_price)}
                        </span>
                    )}
                    {riskView && (
                        <span
                            className={`rounded px-1.5 py-0.5 ${
                                riskView.would_exceed_cap
                                    ? 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
                                    : riskView.already_tracked
                                    ? 'bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300'
                                    : 'bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
                            }`}
                            title={riskView.hint}
                        >
                            风险占用 ¥{riskView.projected_amount.toFixed(0)} / {(riskView.occupancy_pct * 100).toFixed(0)}%
                        </span>
                    )}
                    <span className={`flex items-center gap-0.5 font-medium ${obs.cls}`}>
                        {c.observe_state === 'WAITING' && <EyeIcon className="h-2.5 w-2.5" />}
                        {obs.text}
                    </span>
                    <span className="ml-auto">
                        <CompletenessBar value={c.tradeflow_data_completeness} />
                    </span>
                </div>

                {/* Row 5: Next Step buttons */}
                <div className="mt-2 flex items-center gap-1.5">
                    <button
                        onClick={(e) => { e.stopPropagation(); setActiveTab('observe') }}
                        className="inline-flex items-center gap-0.5 rounded border border-slate-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700"
                    >
                        <Eye className="h-2.5 w-2.5" />
                        观察
                    </button>
                    <button
                        onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/analysis?symbol=${c.symbol}&horizon=${isHaotian ? 'medium' : 'short'}&intent=${isHaotian ? 'entry' : 'trend_confirm'}`)
                        }}
                        className="inline-flex items-center gap-0.5 rounded border border-blue-200 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 hover:bg-blue-50 dark:border-blue-700 dark:text-blue-300 dark:hover:bg-blue-900/30"
                    >
                        <Lightbulb className="h-2.5 w-2.5" />
                        轻量 TA
                    </button>
                    <button
                        onClick={async (e) => {
                            e.stopPropagation()
                            try {
                                await api.addPaperCandidate({
                                    symbol: c.symbol,
                                    name: c.name,
                                    trade_date: tradeDate,
                                    trigger_price: c.trigger_price,
                                    invalid_price: c.invalid_price,
                                    candidate_type: c.candidate_type,
                                    data_quality_score: c.data_quality_score,  // [TF-RISK-001]
                                })
                                setActiveTab('paper-ledger')
                            } catch {
                                // ignore — user can retry from drawer
                            }
                        }}
                        className="inline-flex items-center gap-0.5 rounded border border-purple-200 px-1.5 py-0.5 text-[10px] font-medium text-purple-600 hover:bg-purple-50 dark:border-purple-700 dark:text-purple-300 dark:hover:bg-purple-900/30"
                    >
                        <Wallet className="h-2.5 w-2.5" />
                        模拟跟踪
                    </button>
                </div>
            </div>
        )
    }

    // [UI-012] tradeflow_focus_workspace — observation pool collapsible section
    const [showObservationPool, setShowObservationPool] = useState(false)

    const renderObservationPool = (items: TradeFlowCandidateItem[]) => {
        if (items.length === 0) return null
        return (
            <div>
                <button
                    onClick={() => setShowObservationPool(!showObservationPool)}
                    className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
                >
                    {showObservationPool ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    <Eye className="h-4 w-4 text-amber-500" />
                    观察池（{items.length} 只）
                    <span className="text-xs text-slate-400">— 未达主候选精度但值得跟踪</span>
                </button>
                {showObservationPool && (
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                        {items.map(c => {
                            const ct = candidateTypeLabel(c.candidate_type)
                            // [H-013A] mandate_topic_heatmap_fix
                            const isHighlighted = !!highlightSymbol && c.symbol === highlightSymbol
                            const highlightCls = isHighlighted
                                ? 'ring-2 ring-indigo-400 bg-indigo-50/60 dark:bg-indigo-900/20'
                                : ''
                            // [TF-UX-001] small_cap_trial_workbench — explicit "why not main" reasons
                            const whyNot = pickWhyNotMain(c, 2)
                            return (
                                <div
                                    key={c.symbol}
                                    data-highlight-symbol={isHighlighted ? c.symbol : undefined}
                                    className={`cursor-pointer rounded border border-slate-100 p-2 transition-colors hover:border-amber-200 hover:bg-amber-50/30 dark:border-slate-700 dark:hover:border-amber-800 dark:hover:bg-amber-900/10 ${highlightCls}`}
                                    onClick={() => handleRowClick(c)}
                                >
                                    <div className="flex items-center gap-1.5">
                                        <span className="font-mono text-[11px] font-bold text-slate-700 dark:text-slate-300">{c.symbol}</span>
                                        <span className="max-w-[60px] truncate text-xs text-slate-600 dark:text-slate-400">{c.name || '--'}</span>
                                        <span className={`ml-auto rounded px-1 py-0.5 text-[9px] font-medium ${ct.cls}`}>{ct.text}</span>
                                    </div>
                                    <div className="mt-1 flex items-center gap-1.5 text-[10px] text-slate-400">
                                        <span className="tabular-nums">优先分: {(c.trade_priority_score || 0).toFixed(1)}</span>
                                        <span className="tabular-nums">综合: {(c.composite_score || 0).toFixed(1)}</span>
                                        <span className="tabular-nums">完整度: {((c.tradeflow_data_completeness || 0) * 100).toFixed(0)}%</span>
                                    </div>
                                    {whyNot.length > 0 ? (
                                        <div className="mt-0.5 space-y-0.5">
                                            {whyNot.map((r, i) => (
                                                <div key={i} className="flex items-start gap-0.5 text-[10px] text-amber-600 dark:text-amber-400">
                                                    <ShieldAlert className="mt-0.5 h-2 w-2 flex-shrink-0" />
                                                    <span className="truncate" title={`为什么未入选主候选：${r}`}>未入选主候选：{r}</span>
                                                </div>
                                            ))}
                                        </div>
                                    ) : c.action_tier_reason ? (
                                        <div className="mt-0.5 truncate text-[10px] text-amber-500" title={c.action_tier_reason}>{c.action_tier_reason}</div>
                                    ) : null}
                                </div>
                            )
                        })}
                    </div>
                )}
            </div>
        )
    }

    // [TF-UX-001] tiered candidate card renderer
    const renderTieredCandidateCard = (c: TradeFlowCandidateItem) => {
        const at = actionTierBadge(c.action_tier)
        const ct = candidateTypeLabel(c.candidate_type)
        // [H-013A] mandate_topic_heatmap_fix — highlight symbol carried from topic heatmap
        const isHighlighted = !!highlightSymbol && c.symbol === highlightSymbol
        const highlightCls = isHighlighted
            ? 'ring-2 ring-indigo-400 bg-indigo-50/60 dark:bg-indigo-900/20'
            : ''
        return (
            <div
                key={c.symbol}
                data-highlight-symbol={isHighlighted ? c.symbol : undefined}
                className={`cursor-pointer rounded-lg border border-slate-100 p-3 transition-colors hover:border-blue-200 hover:bg-blue-50/30 dark:border-slate-700 dark:hover:border-blue-800 dark:hover:bg-blue-900/10 ${highlightCls}`}
                onClick={() => handleRowClick(c)}
            >
                <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-slate-900 dark:text-slate-100">{c.symbol}</span>
                    <span className="max-w-[80px] truncate text-sm text-slate-700 dark:text-slate-300">{c.name || '--'}</span>
                    <span className={`ml-auto rounded px-1.5 py-0.5 text-[10px] font-medium ${at.cls}`}>{at.text}</span>
                </div>
                <div className="mt-1.5 flex items-center gap-2 text-xs">
                    <span className={`inline-block rounded px-1 py-0.5 text-[10px] font-bold ${tierBadgeClass(c.tier)}`}>{c.tier || '-'}</span>
                    <span className={`inline-block rounded px-1 py-0.5 text-[10px] font-medium ${ct.cls}`}>{ct.text}</span>
                    <span className="tabular-nums text-slate-500">优先分: <span className="font-medium text-slate-700 dark:text-slate-300">{c.trade_priority_score.toFixed(2)}</span></span>
                    <span className="tabular-nums text-slate-500">综合: <span className="font-medium text-slate-700 dark:text-slate-300">{c.composite_score.toFixed(1)}</span></span>
                </div>
                {c.action_tier_reason && (
                    <div className="mt-1 text-[11px] text-slate-400" title={c.action_tier_reason}>{c.action_tier_reason}</div>
                )}
                <div className="mt-1.5 flex flex-wrap gap-1">
                    {c.strategy_tags.slice(0, 2).map(tag => (
                        <span key={tag} className="inline-block rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-500 dark:bg-slate-700 dark:text-slate-400">{tag}</span>
                    ))}
                    {c.strategy_tags.length > 2 && <span className="text-[10px] text-slate-400">+{c.strategy_tags.length - 2}</span>}
                    <span className="ml-auto">
                        <CompletenessBar value={c.tradeflow_data_completeness} />
                    </span>
                </div>
            </div>
        )
    }

    const renderContent = () => {
        if (loading) {
            return (
                <div className="flex items-center justify-center py-20 text-slate-400">
                    <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                    加载中...
                </div>
            )
        }
        if (error) {
            return (
                <div className="flex items-center justify-center py-20 text-red-500">
                    <AlertCircle className="mr-2 h-5 w-5" />
                    {error}
                </div>
            )
        }

        if (activeTab === 'candidates') {
            if (status === 'no_data') {
                return (
                    <EmptyStateCTA
                        icon={Target}
                        title={`${tradeDate} 暂无候选数据`}
                        desc="尚未生成候选池，或接口/数据源暂时不可用"
                        hints={[
                            '使用上方「生成候选池」扫描自选股 / 持仓 / 事件源',
                            '候选池为空时，盘中观察、盘后 Review、主题热度均无法汇总',
                            '可将关注标的先加入观察仓（跟踪看板）再做轻量研究',
                        ]}
                        actions={[
                            { label: '查看上方生成候选池', onClick: goToScanPanel, primary: true },
                            { label: '前往观察仓', onClick: goToObservationWarehouse },
                            { label: '查看数据健康', onClick: goToDataHealthTab },
                        ]}
                    />
                )
            }
            if (candidates.length === 0) {
                const hasMandateFilter = candidateTypeFilter && ['POLICY_AMBUSH', 'POLICY_CONFIRM', 'EVENT_WATCH'].includes(candidateTypeFilter)
                const hasPoolFilter = poolFilter !== 'all'
                return (
                    <div className="py-20 text-center text-sm text-slate-400">
                        <Target className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                        无匹配候选
                        {(hasMandateFilter || hasPoolFilter) && (
                            <div className="mt-2 space-y-1 text-xs text-slate-400">
                                <div>可能原因：</div>
                                <div>1. 政策信号源不足，尚未生成昊天候选</div>
                                <div>2. 公司受益路径不足，无法确认政策受益标的</div>
                                <div>3. 当前政策候选均已过热，被标记为过热规避</div>
                            </div>
                        )}
                    </div>
                )
            }

            // [UI-012] tradeflow_focus_workspace — default focus view
            // [TF-UX-001] small_cap_trial_workbench — three groups (pending_confirm / near_trigger / main)
            // replace the previous haotian/tech split so users always see a small,
            // actionable default main view, with the weak observation pool collapsed.
            if (viewMode === 'focus' && tieredData && tieredData.status === 'ok') {
                const mainItems = tieredData.main_candidates ?? [
                    ...tieredData.actionable,
                    ...tieredData.watch,
                    ...tieredData.scan,
                ]
                const observationItems = tieredData.observation_candidates ?? []
                const poolSummary = tieredData.pool_gate_summary ?? ''
                const poolCounts = tieredData.pool_counts ?? {}
                // [H-014] mandate_concentration_gate — theme concentration headline
                const concentration = tieredData.concentration_summary

                // [TF-UX-001] small_cap_trial_workbench — split into the three required groups.
                const focusGroups = groupMainCandidates(mainItems, DEFAULT_MAIN_VIEW_CAP)
                // [TF-UX-001] small_cap_trial_workbench — risk budget for the 5000 元试跑 prompt area.
                const trialBudget = computeTrialBudgetView(paperLedger)

                if (mainItems.length === 0) {
                    const hasObservation = observationItems.length > 0
                    const hasFiltered = (poolCounts['filtered'] ?? 0) > 0
                    return (
                        <div className="space-y-4 p-4">
                            <TrialBudgetPrompt budget={trialBudget} />
                            <div className="py-12 text-center text-sm text-slate-400">
                                <Target className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                                <div className="font-medium text-slate-500 dark:text-slate-400">今日无主候选</div>
                                <div className="mt-2 text-xs">
                                    {poolSummary || '候选池尚未生成或全部未达主候选精度'}
                                </div>
                                {hasObservation && (
                                    <div className="mt-1 text-xs text-amber-500">
                                        有 {observationItems.length} 只观察候选，可展开下方观察池查看
                                    </div>
                                )}
                                {!hasObservation && hasFiltered && (
                                    <div className="mt-1 text-xs text-slate-400">
                                        今日扫描的股票全部被过滤，可在「被过滤」tab 查看原因
                                    </div>
                                )}
                                {!hasObservation && !hasFiltered && (
                                    <div className="mt-1 text-xs text-slate-400">
                                        请先生成候选池，或检查数据源是否正常
                                    </div>
                                )}
                            </div>
                            {renderObservationPool(observationItems)}
                        </div>
                    )
                }

                return (
                    <div className="space-y-4 p-4">
                        {/* Pool gate summary banner */}
                        {poolSummary && (
                            <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-300">
                                {poolSummary}
                            </div>
                        )}

                        {/* [H-014] mandate_concentration_gate — 昊天主题集中度 headline */}
                        {concentration && concentration.enabled && concentration.headline && (
                            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300">
                                <span className="font-semibold">昊天主题集中度｜</span>
                                {concentration.headline}
                                {concentration.top_theme_main_symbols && concentration.top_theme_main_symbols.length > 0 && (
                                    <span className="ml-1 text-emerald-600 dark:text-emerald-400">
                                        （主候选已压缩至强主题 Top {concentration.top_theme_main_symbols.length}）
                                    </span>
                                )}
                            </div>
                        )}

                        {/* [TF-UX-001] 5000 元试跑 prompt area */}
                        <TrialBudgetPrompt budget={trialBudget} />

                        {/* Three actionable groups — pending_confirm / near_trigger / main */}
                        {focusGroups.groups.map(g => (
                            <div key={g.key}>
                                <div className="mb-2 flex items-center gap-2">
                                    <span className={`inline-block h-2 w-2 rounded-full ${focusGroupDotClass(g.key)}`} />
                                    <span className={`text-sm font-bold ${focusGroupTitleClass(g.key)}`}>{g.label}</span>
                                    <span className="text-xs text-slate-400">（{g.hint}）</span>
                                    <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-700 dark:text-slate-300">
                                        {g.items.length} 只
                                    </span>
                                </div>
                                {g.items.length === 0 ? (
                                    <div className="rounded border border-dashed border-slate-200 px-3 py-2 text-xs text-slate-400 dark:border-slate-700">
                                        暂无
                                    </div>
                                ) : (
                                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                        {g.items.map(c => renderMainCandidateCard(c, trialBudget))}
                                    </div>
                                )}
                            </div>
                        ))}

                        {/* [TF-UX-001] Invalidated / expired — collapsed separate section so the
                            user understands why a previously main candidate is no longer actionable. */}
                        {focusGroups.invalidated.length > 0 && (
                            <InvalidatedPool items={focusGroups.invalidated} onPick={handleRowClick} highlightSymbol={highlightSymbol} />
                        )}

                        {/* Observation pool — collapsed by default */}
                        {renderObservationPool(observationItems)}
                    </div>
                )
            }

            // [TF-UX-001] tiered view mode
            if (viewMode === 'tiered' && tieredData && tieredData.status === 'ok') {
                const actionableItems = tieredData.actionable
                const watchItems = tieredData.watch
                const scanItems = tieredData.scan

                return (
                    <div className="space-y-4 p-4">
                        {actionableItems.length > 0 && (
                            <div>
                                <div className="mb-2 flex items-center gap-2">
                                    <Zap className="h-4 w-4 text-red-500" />
                                    <span className="text-sm font-bold text-red-700 dark:text-red-300">今日可操作</span>
                                    <span className="text-xs text-slate-400">（最多 3 只）</span>
                                </div>
                                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                    {actionableItems.map(renderTieredCandidateCard)}
                                </div>
                            </div>
                        )}

                        {watchItems.length > 0 && (
                            <div>
                                <div className="mb-2 flex items-center gap-2">
                                    <Eye className="h-4 w-4 text-amber-500" />
                                    <span className="text-sm font-bold text-amber-700 dark:text-amber-300">重点观察</span>
                                    <span className="text-xs text-slate-400">（最多 8 只）</span>
                                </div>
                                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                                    {watchItems.map(renderTieredCandidateCard)}
                                </div>
                            </div>
                        )}

                        {scanItems.length > 0 && (
                            <div>
                                <button
                                    onClick={() => setShowScan(!showScan)}
                                    className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
                                >
                                    {showScan ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                                    <Target className="h-4 w-4" />
                                    全部扫描结果（{tieredData.scan_count} 只）
                                </button>
                                {showScan && (
                                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                                        {scanItems.map(renderTieredCandidateCard)}
                                    </div>
                                )}
                            </div>
                        )}

                        {actionableItems.length === 0 && watchItems.length === 0 && scanItems.length === 0 && (
                            <div className="py-12 text-center text-sm text-slate-400">暂无分级数据</div>
                        )}
                    </div>
                )
            }

            // [TF-UX-001] table view mode (fallback)
            return (
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                <th className="px-4 py-2.5 font-medium">代码</th>
                                <th className="px-4 py-2.5 font-medium">名称</th>
                                <th className="px-4 py-2.5 font-medium">操作层级</th>
                                <th className="px-4 py-2.5 font-medium">候选类型</th>
                                <th className="px-4 py-2.5 font-medium">层级</th>
                                <th className="px-4 py-2.5 font-medium">优先分</th>
                                <th className="px-4 py-2.5 font-medium">综合分</th>
                                <th className="px-4 py-2.5 font-medium">策略标签</th>
                                <th className="px-4 py-2.5 font-medium">完整度</th>
                                <th className="px-4 py-2.5 font-medium">观察状态</th>
                                <th className="px-4 py-2.5 font-medium">动作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {candidates.map(c => {
                                const act = actionLabel(c.action)
                                const obs = observeStateLabel(c.observe_state)
                                const ct = candidateTypeLabel(c.candidate_type)
                                const at = actionTierBadge(c.action_tier)
                                // [H-013A] mandate_topic_heatmap_fix
                                const isHighlighted = !!highlightSymbol && c.symbol === highlightSymbol
                                const highlightCls = isHighlighted
                                    ? 'ring-2 ring-inset ring-indigo-400 bg-indigo-50/60 dark:bg-indigo-900/20'
                                    : ''
                                return (
                                    <tr
                                        key={c.symbol}
                                        data-highlight-symbol={isHighlighted ? c.symbol : undefined}
                                        className={`cursor-pointer border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 ${highlightCls}`}
                                        onClick={() => handleRowClick(c)}
                                    >
                                        <td className="px-4 py-2.5 font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{c.symbol}</td>
                                        <td className="max-w-[100px] truncate px-4 py-2.5 text-slate-700 dark:text-slate-300">{c.name || '--'}</td>
                                        <td className="px-4 py-2.5">
                                            <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${at.cls}`}>{at.text}</span>
                                        </td>
                                        <td className="px-4 py-2.5">
                                            <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${ct.cls}`}>{ct.text}</span>
                                        </td>
                                        <td className="px-4 py-2.5">
                                            <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-bold ${tierBadgeClass(c.tier)}`}>{c.tier || '-'}</span>
                                        </td>
                                        <td className="px-4 py-2.5 tabular-nums text-xs font-medium text-red-600 dark:text-red-400">{c.trade_priority_score.toFixed(2)}</td>
                                        <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{c.composite_score.toFixed(2)}</td>
                                        <td className="max-w-[160px] px-4 py-2.5">
                                            <div className="flex flex-wrap gap-1">
                                                {c.strategy_tags.slice(0, 3).map(tag => (
                                                    <span key={tag} className="inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 dark:bg-slate-700 dark:text-slate-300">{tag}</span>
                                                ))}
                                                {c.strategy_tags.length > 3 && (
                                                    <span className="text-[11px] text-slate-400">+{c.strategy_tags.length - 3}</span>
                                                )}
                                            </div>
                                        </td>
                                        <td className="px-4 py-2.5">
                                            <CompletenessBar value={c.tradeflow_data_completeness} />
                                        </td>
                                        <td className="px-4 py-2.5">
                                            <span className={`flex items-center gap-1 text-xs font-medium ${obs.cls}`}>
                                                {c.observe_state === 'WAITING' && <Eye className="h-3 w-3" />}
                                                {obs.text}
                                            </span>
                                        </td>
                                        <td className="px-4 py-2.5">
                                            <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${act.cls}`}>{act.text}</span>
                                        </td>
                                    </tr>
                                )
                            })}
                        </tbody>
                    </table>
                </div>
            )
        }

        // [M-009] tradeflow_watch_pool_panel
        if (activeTab === 'watch-pool') {
            return (
                <WatchPoolTab
                    candidates={candidates}
                    filteredItems={filteredData?.filtered ?? []}
                    onRowClick={handleRowClick}
                    onNavigateToAnalysis={(url) => navigate(url)}
                />
            )
        }

        if (activeTab === 'compare') {
            return (
                <CompareTab
                    candidates={compareData}
                    sortBy={compareSortBy}
                    onSortChange={(key) => {
                        setCompareSortBy(key)
                        api.getTradeFlowCandidatesCompare(tradeDate, key, 'desc', 'haotian').then(res => {
                            if (res.status === 'ok') setCompareData(res.candidates)
                        }).catch(() => {})
                    }}
                    onRowClick={handleRowClick}
                />
            )
        }

        if (activeTab === 'observe') {
            return <ObserveTable
                items={observeData?.observe_items ?? []}
                onRun={handleRunObserve}
                running={observeRunLoading}
                runResult={observeRunResult}
                lastCheckTime={lastObserveCheckTime}
                observeReason={observeData?.observe_reason ?? ''}
                observeAutoRun={observeData?.observe_auto_run ?? false}
                lastObservedAt={observeData?.last_observed_at ?? ''}
                refreshIntervalSeconds={effectiveObserveRefreshSeconds}
                isMarketHours={observeData?.is_market_hours ?? false}
                isTradingDay={observeData?.is_trading_day ?? false}
                nearTriggerCount={observeData?.near_trigger_count ?? 0}
                pendingCount={observeData?.pending_count ?? 0}
                nextRefreshIn={nextRefreshIn}
                autoRefreshActive={autoRefreshActive}
                planDate={observeData?.plan_date}                                   // [TF-OBS-005]
                effectiveTradeDate={observeData?.effective_trade_date}             // [TF-OBS-005]
                nonTradingDayPlan={observeData?.non_trading_day_plan}             // [TF-OBS-005]
                nextTradingDayHint={observeData?.next_trading_day_hint}           // [TF-OBS-005]
                onGoToCandidates={goToCandidatesTab}                                // [UI-013] tradeflow_empty_state_cta
                onGoToObservationWarehouse={goToObservationWarehouse}              // [UI-013] tradeflow_empty_state_cta
            />
        }

        if (activeTab === 'ta-queue') {
            if (!taQueueData) {
                return <div className="py-20 text-center text-sm text-slate-400">暂无 TA 队列数据</div>
            }
            return <TAQueueTable items={taQueueData.queue} meta={taQueueData} />
        }

        if (activeTab === 'review') {
            if (!reviewData || reviewData.status === 'no_data') {
                // [TF-REVIEW-004] review_empty_diagnostics — show specific reason + cross-date mapping
                // [UI-013] tradeflow_empty_state_cta — unified CTA + non-trading-day mapping wording
                const emptyReason = reviewData?.empty_reason ?? ''
                const emptyMsg = reviewData?.empty_reason_message ?? '尚未生成盘后复盘'
                const suggested = reviewData?.suggested_action ?? ''
                const hasPlanDates = (reviewData?.available_plan_dates?.length ?? 0) > 0
                const showMapping = !!(reviewData?.plan_date && reviewData.plan_date !== reviewData.trade_date)
                const reviewHints: string[] = []
                if (!hasPlanDates) {
                    reviewHints.push('盘后 Review 依赖候选池，请先在「候选池」生成今日候选')
                }
                if (showMapping) {
                    reviewHints.push('非交易日（周末/节假日）生成的候选池会在下一个交易日复盘')
                }
                reviewHints.push('若当日为非交易日，可切换日期或等待下一交易日自动汇总')
                return (
                    <div className="space-y-4 p-4">
                        <EmptyStateCTA
                            icon={BarChart3}
                            title={emptyMsg}
                            desc={emptyReason ? `原因：${emptyReason}` : undefined}
                            hints={reviewHints.length > 0 ? reviewHints : undefined}
                            actions={[
                                { label: reviewGenerating ? '生成中...' : '一键生成盘后复盘', onClick: () => void handleGenerateReview(), primary: true, disabled: reviewGenerating },
                                ...(!hasPlanDates ? [{ label: '前往生成候选池', onClick: goToCandidatesTab }] : []),
                            ]}
                        />
                        {suggested && (
                            <div className="text-center text-xs text-amber-600 dark:text-amber-400">建议：{suggested}</div>
                        )}
                        {showMapping && (
                            <div className="text-center">
                                <span className="inline-block rounded bg-blue-50 px-3 py-1.5 text-xs text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">
                                    候选池生成日 {reviewData?.plan_date}
                                    {reviewData?.effective_trade_date && `，生效交易日：${reviewData.effective_trade_date}`}
                                    {reviewData?.review_date && `，复盘日：${reviewData.review_date}`}
                                </span>
                            </div>
                        )}
                        {hasPlanDates && (
                            <div className="card px-4 py-3 text-xs text-slate-500 dark:text-slate-400">
                                <span className="font-medium text-slate-600 dark:text-slate-300">已有候选池日期：</span>
                                <span className="ml-1.5 font-mono">{reviewData?.available_plan_dates?.slice(0, 8).join('、') || '-'}</span>
                                {(reviewData?.available_plan_dates?.length ?? 0) > 8 && (
                                    <span className="ml-1">等 {reviewData?.available_plan_dates?.length} 个</span>
                                )}
                            </div>
                        )}
                        {reviewGenerateError && (
                            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-xs text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
                                {reviewGenerateError}
                            </div>
                        )}
                    </div>
                )
            }
            return <ReviewTab data={reviewData} />
        }

        if (activeTab === 'filtered') {
            if (!filteredData) {
                return <div className="py-20 text-center text-sm text-slate-400">加载中...</div>
            }
            return <FilteredTab data={filteredData} candidateCount={candidates.length} />
        }

        if (activeTab === 'data-health') {
            return (
                <div className="space-y-6">
                    <DataHealthPanel data={dataHealth} />
                    {/* [DATA-018] source_freshness_report */}
                    <div>
                        <h3 className="mb-2 px-4 text-sm font-semibold text-slate-700 dark:text-slate-300">
                            数据源新鲜度与 Fallback
                        </h3>
                        <SourceFreshnessPanel data={sourceFreshness} />
                    </div>
                    {/* [DATA-020] live_sampling_health_ui */}
                    <div>
                        <h3 className="mb-2 px-4 text-sm font-semibold text-slate-700 dark:text-slate-300">
                            实盘抽样健康日报
                        </h3>
                        <LiveSamplingPanel data={liveSampling} />
                    </div>
                </div>
            )
        }

        // [H-013] mandate_topic_heatmap
        if (activeTab === 'topic-heatmap') {
            return (
                <div className="space-y-4">
                    <MandateDailyReportPanel
                        data={mandateDailyReport}
                        onGenerateCandidates={goToCandidatesTab}  // [UI-013] tradeflow_empty_state_cta
                    />
                    <TopicHeatmapPanel
                        data={topicHeatmap}
                        onSelectSymbol={handleTopicSymbolClick}
                        onGenerateCandidates={goToCandidatesTab}  // [UI-013] tradeflow_empty_state_cta
                    />
                </div>
            )
        }

        // [TF-PAPER-001] paper_trading_ledger
        if (activeTab === 'paper-ledger') {
            if (!paperLedger) {
                return <div className="py-20 text-center text-sm text-slate-400">加载中...</div>
            }
            const s = paperLedger.summary
            const pnlColor = s.total_pnl >= 0 ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400'
            // [TF-RISK-001] paper_risk_budget
            const re = s.risk_exposure
            const utilColor = (re?.budget_utilization_pct ?? 0) >= 80
                ? 'text-amber-600 dark:text-amber-400'
                : 'text-slate-700 dark:text-slate-200'
            return (
                <div className="space-y-4 p-4">
                    {/* Summary Cards */}
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                        <div className="card p-3">
                            <div className="text-xs text-slate-400">本金</div>
                            <div className="mt-1 text-lg font-bold text-slate-700 dark:text-slate-200">¥{paperLedger.principal.toFixed(0)}</div>
                        </div>
                        <div className="card p-3">
                            <div className="text-xs text-slate-400">现金余额</div>
                            <div className="mt-1 text-lg font-bold text-slate-700 dark:text-slate-200">¥{paperLedger.cash_balance.toFixed(2)}</div>
                        </div>
                        <div className="card p-3">
                            <div className="text-xs text-slate-400">累计盈亏</div>
                            <div className={`mt-1 text-lg font-bold ${pnlColor}`}>
                                {s.total_pnl >= 0 ? '+' : ''}{s.total_pnl.toFixed(2)} ({s.total_pnl_pct.toFixed(2)}%)
                            </div>
                        </div>
                        <div className="card p-3">
                            <div className="text-xs text-slate-400">跟踪/持仓/已平仓</div>
                            <div className="mt-1 text-lg font-bold text-slate-700 dark:text-slate-200">
                                {s.tracking_count} / {s.open_count} / {s.closed_count}
                            </div>
                        </div>
                    </div>

                    {/* [TF-RISK-001] paper_risk_budget — risk budget panel */}
                    {re && (
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <div className="card p-3">
                                <div className="text-xs text-slate-400">剩余额度 / 单票上限</div>
                                <div className="mt-1 text-sm font-bold text-slate-700 dark:text-slate-200">
                                    ¥{re.remaining.toFixed(0)} <span className="text-xs font-normal text-slate-400">/ ¥{re.per_ticket_max.toFixed(0)}</span>
                                </div>
                            </div>
                            <div className="card p-3">
                                <div className="text-xs text-slate-400">风险占用</div>
                                <div className={`mt-1 text-sm font-bold ${utilColor}`}>
                                    ¥{re.invested.toFixed(0)} ({re.budget_utilization_pct.toFixed(1)}%)
                                </div>
                            </div>
                            <div className="card p-3">
                                <div className="text-xs text-slate-400">今日新增 / 上限</div>
                                <div className="mt-1 text-sm font-bold text-slate-700 dark:text-slate-200">
                                    {re.daily_new_today} / {re.daily_new_max}
                                </div>
                            </div>
                            <div className="card p-3">
                                <div className="text-xs text-slate-400">并发跟踪 / 上限</div>
                                <div className="mt-1 text-sm font-bold text-slate-700 dark:text-slate-200">
                                    {re.tracking_count} / {re.max_concurrent_tracking}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Trades Table */}
                    {paperLedger.trades.length === 0 ? (
                        <div className="py-12 text-center text-sm text-slate-400">
                            <Wallet className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                            暂无模拟跟踪记录
                            <div className="mt-2 text-xs">从候选详情点击「加入模拟跟踪」开始</div>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b border-slate-200 text-xs text-slate-400 dark:border-slate-700">
                                        <th className="px-2 py-2 text-left">代码</th>
                                        <th className="px-2 py-2 text-left">名称</th>
                                        <th className="px-2 py-2 text-right">触发价</th>
                                        <th className="px-2 py-2 text-right">失效价</th>
                                        <th className="px-2 py-2 text-right">计划金额</th>
                                        <th className="px-2 py-2 text-center">状态</th>
                                        <th className="px-2 py-2 text-right">买入价</th>
                                        <th className="px-2 py-2 text-right">盈亏</th>
                                        <th className="px-2 py-2 text-center">操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {paperLedger.trades.map((t) => {
                                        const statusMap: Record<string, { text: string; cls: string }> = {
                                            tracking: { text: '跟踪中', cls: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300' },
                                            pending: { text: '待确认', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' },
                                            open: { text: '持仓中', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' },
                                            closed: { text: '已平仓', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' },
                                            invalidated: { text: '已失效', cls: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300' },
                                            observation: { text: '仅观察', cls: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300' },
                                        }
                                        const st = statusMap[t.status] || statusMap.tracking
                                        const tradePnlColor = t.pnl >= 0 ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400'
                                        return (
                                            <tr key={t.id} className="border-b border-slate-100 dark:border-slate-800">
                                                <td className="px-2 py-2 font-mono text-xs">{t.symbol}</td>
                                                <td className="px-2 py-2">{t.name || '-'}</td>
                                                <td className="px-2 py-2 text-right tabular-nums">{t.trigger_price?.toFixed(2) || '-'}</td>
                                                <td className="px-2 py-2 text-right tabular-nums">{t.invalid_price?.toFixed(2) || '-'}</td>
                                                <td className="px-2 py-2 text-right tabular-nums">¥{t.planned_amount.toFixed(0)}</td>
                                                <td className="px-2 py-2 text-center"><span className={`rounded px-2 py-0.5 text-xs ${st.cls}`}>{st.text}</span></td>
                                                <td className="px-2 py-2 text-right tabular-nums">{t.action_price?.toFixed(2) || '-'}</td>
                                                <td className={`px-2 py-2 text-right tabular-nums ${tradePnlColor}`}>
                                                    {t.status === 'closed' ? `${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)} (${t.pnl_pct.toFixed(2)}%)` : '-'}
                                                </td>
                                                <td className="px-2 py-2 text-center">
                                                    <div className="flex items-center justify-center gap-1">
                                                        {(t.status === 'tracking' || t.status === 'pending') && (
                                                            <button
                                                                disabled={paperActionLoading}
                                                                onClick={() => {
                                                                    const priceStr = window.prompt(`确认模拟买入 ${t.symbol}，输入买入价格：`, t.trigger_price?.toString() || '')
                                                                    if (priceStr) {
                                                                        const price = parseFloat(priceStr)
                                                                        if (price > 0) void handlePaperAction(t.id, 'buy', price)
                                                                    }
                                                                }}
                                                                className="rounded bg-red-500 px-2 py-0.5 text-xs text-white hover:bg-red-600 disabled:opacity-50"
                                                            >
                                                                买入
                                                            </button>
                                                        )}
                                                        {t.status === 'open' && (
                                                            <button
                                                                disabled={paperActionLoading}
                                                                onClick={() => {
                                                                    const priceStr = window.prompt(`确认模拟卖出 ${t.symbol}，输入卖出价格：`, '')
                                                                    if (priceStr) {
                                                                        const price = parseFloat(priceStr)
                                                                        if (price > 0) void handlePaperAction(t.id, 'sell', price)
                                                                    }
                                                                }}
                                                                className="rounded bg-green-600 px-2 py-0.5 text-xs text-white hover:bg-green-700 disabled:opacity-50"
                                                            >
                                                                卖出
                                                            </button>
                                                        )}
                                                        {(t.status === 'tracking' || t.status === 'pending') && (
                                                            <button
                                                                disabled={paperActionLoading}
                                                                onClick={() => void handlePaperRemove(t.id)}
                                                                className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700 disabled:opacity-50"
                                                            >
                                                                移除
                                                            </button>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}

                    <div className="text-xs text-slate-400">
                        模拟账户仅供学习研究，不构成投资建议，不连接真实交易。
                    </div>
                </div>
            )
        }

        return null
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <ClipboardList className="h-5 w-5 text-blue-600 dark:text-blue-400" />
                    <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100">TradeFlow</h1>
                    <RuntimeTierBadge tier="FAST_RADAR" latency="5-30s" />
                </div>
                <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800">
                        <Calendar className="h-4 w-4 text-slate-400" />
                        <input
                            type="date"
                            value={tradeDate}
                            onChange={handleDateChange}
                            className="w-28 bg-transparent text-sm text-slate-700 outline-none dark:text-slate-300"
                        />
                    </div>
                    <button
                        onClick={() => void fetchData(tradeDate)}
                        className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                    >
                        <RefreshCw className="h-3.5 w-3.5" />
                        刷新
                    </button>
                </div>
            </div>

            {activeTab === 'candidates' && (
                <SummaryCards summary={summary} dataHealth={dataHealth} />
            )}

            {activeTab === 'candidates' && (
                <div id="tradeflow-scan-panel" className="card p-4">  {/* [UI-013] tradeflow_empty_state_cta — scroll target for empty-state CTA */}
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
                        <div className="min-w-0 flex-1">
                            <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">股票池</label>
                            <input
                                value={scanSymbols}
                                onChange={e => setScanSymbols(e.target.value)}
                                placeholder="可空；或输入 002353.SZ, 603256.SH"
                                className="w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                            />
                        </div>
                        <div>
                            <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">TopN</label>
                            <input
                                type="number"
                                min={1}
                                max={100}
                                value={scanTopN}
                                onChange={e => setScanTopN(Math.max(1, Math.min(100, Number(e.target.value) || 20)))}
                                className="w-20 rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                            />
                        </div>
                        <label className="flex items-center gap-2 rounded border border-slate-200 px-3 py-2 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300">
                            <input type="checkbox" checked={scanIncludeWatchlist} onChange={e => setScanIncludeWatchlist(e.target.checked)} />
                            自选股
                        </label>
                        <label className="flex items-center gap-2 rounded border border-slate-200 px-3 py-2 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300">
                            <input type="checkbox" checked={scanIncludeHoldings} onChange={e => setScanIncludeHoldings(e.target.checked)} />
                            持仓
                        </label>
                        <label className="flex items-center gap-2 rounded border border-slate-200 px-3 py-2 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300">
                            <input type="checkbox" checked={scanUseEventSource} onChange={e => setScanUseEventSource(e.target.checked)} />
                            事件源
                        </label>
                        <button
                            onClick={() => void handleRunDiscovery()}
                            disabled={scanLoading}
                            className="inline-flex items-center justify-center gap-1.5 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                            {scanLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                            生成候选池
                        </button>
                    </div>
                    {scanMessage && <div className="mt-3 text-xs text-emerald-600 dark:text-emerald-400">{scanMessage}</div>}
                </div>
            )}

            <div className="card">
                <div className="flex items-center gap-1 border-b border-slate-100 px-4 pt-3 dark:border-slate-700">
                    {TABS.map(tab => {
                        const Icon = tab.icon
                        const isActive = activeTab === tab.key
                        return (
                            <button
                                key={tab.key}
                                onClick={() => {
                                    // [H-013A] mandate_topic_heatmap_fix — clear carried symbol when leaving candidates
                                    if (activeTab === 'candidates' && tab.key !== 'candidates') {
                                        setHighlightSymbol(null)
                                    }
                                    setActiveTab(tab.key)
                                }}
                                className={`flex items-center gap-1.5 rounded-t px-3 py-2 text-sm font-medium transition-colors ${
                                    isActive
                                        ? 'border-b-2 border-blue-600 text-blue-600 dark:border-blue-400 dark:text-blue-400'
                                        : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
                                }`}
                            >
                                <Icon className="h-4 w-4" />
                                {tab.label}
                            </button>
                        )
                    })}
                </div>

                {activeTab === 'candidates' && (
                    <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-700">
                        <div className="flex flex-wrap items-center gap-3">
                            <div className="flex items-center gap-1.5 text-sm text-slate-500">
                                <Filter className="h-3.5 w-3.5" />
                                视图
                            </div>
                            <div className="flex gap-1">
                                <button
                                    onClick={() => setViewMode('focus')}
                                    className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                                        viewMode === 'focus'
                                            ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300'
                                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-400 dark:hover:bg-slate-600'
                                    }`}
                                >
                                    主候选工作台
                                </button>
                                <button
                                    onClick={() => setViewMode('tiered')}
                                    className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                                        viewMode === 'tiered'
                                            ? 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-400 dark:hover:bg-slate-600'
                                    }`}
                                >
                                    分级视图
                                </button>
                                <button
                                    onClick={() => setViewMode('table')}
                                    className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                                        viewMode === 'table'
                                            ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-400 dark:hover:bg-slate-600'
                                    }`}
                                >
                                    表格视图
                                </button>
                            </div>
                            <span className="mx-2 text-slate-300 dark:text-slate-600">|</span>
                            <div className="flex gap-1">
                                {POOL_TABS.map(tab => (
                                    <button
                                        key={tab.key}
                                        onClick={() => setPoolFilter(tab.key)}
                                        className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                                            poolFilter === tab.key
                                                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                                                : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-400 dark:hover:bg-slate-600'
                                        }`}
                                    >
                                        {tab.label}
                                    </button>
                                ))}
                            </div>
                            <span className="mx-2 text-slate-300 dark:text-slate-600">|</span>
                            <select
                                value={tierFilter}
                                onChange={e => setTierFilter(e.target.value)}
                                className="rounded border border-slate-200 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                            >
                                <option value="">全部层级</option>
                                <option value="A">A级</option>
                                <option value="B">B级</option>
                                <option value="C">C级</option>
                            </select>
                            <select
                                value={deepTaFilter}
                                onChange={e => setDeepTaFilter(e.target.value)}
                                className="rounded border border-slate-200 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                            >
                                <option value="">全部TA状态</option>
                                <option value="yes">需深度TA</option>
                                <option value="no">无需深度TA</option>
                            </select>
                            <select
                                value={observeFilter}
                                onChange={e => setObserveFilter(e.target.value)}
                                className="rounded border border-slate-200 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                            >
                                <option value="">全部观察状态</option>
                                <option value="WAITING">等待中</option>
                                <option value="TRIGGERED">已触发</option>
                                <option value="INVALIDATED">已失效</option>
                            </select>
                            <span className="ml-auto text-xs text-slate-400">{candidates.length} 条记录</span>
                        </div>
                    </div>
                )}

                {activeTab === 'observe' && observeData && (
                    <div className="flex flex-wrap items-center gap-4 border-b border-slate-100 px-4 py-2.5 dark:border-slate-700">
                        <span className="text-xs text-slate-500">已触发: <span className="font-medium text-red-600 dark:text-red-400">{observeData.triggered_count}</span></span>
                        <span className="text-xs text-slate-500">等待中: <span className="font-medium text-slate-600 dark:text-slate-300">{observeData.waiting_count}</span></span>
                        <span className="text-xs text-slate-500">已失效: <span className="font-medium text-emerald-600 dark:text-emerald-400">{observeData.invalidated_count}</span></span>
                        <span className="ml-auto text-xs text-slate-400">{observeData.observe_items.length} 条记录</span>
                    </div>
                )}

                {renderContent()}
            </div>

            {selectedCandidate && (
                <TradeFlowCandidateDrawer
                    candidate={selectedCandidate}
                    tradeDate={tradeDate}
                    open={drawerOpen}
                    onClose={handleDrawerClose}
                    onNavigateToAnalysis={(url) => navigate(url)}  // [TA-UI-001] analysis_console_horizon_intent
                />
            )}
        </div>
    )
}
