// [UI-005] tradeflow_review_page
// [H-005] mandate_radar_ui
// [TA-UI-001] analysis_console_horizon_intent
// [TF-P0-002] tradeflow_pool_split
// [TF-UX-001] tiered candidates
// [TF-UX-002] auto refresh + A-stock colors
// [TF-UX-003] post_market_review
// [TF-UX-004] trade_priority_score
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Target, Loader2, AlertCircle, Calendar, Filter, Eye, RefreshCw, ListOrdered, ClipboardList, BarChart3, Activity, Search, FilterX, ChevronDown, ChevronRight, Zap, Clock, GitCompare } from 'lucide-react'
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
    TradeFlowTieredCandidatesResponse,
} from '@/types'
import TradeFlowCandidateDrawer from '@/components/TradeFlowCandidateDrawer'

type TabKey = 'candidates' | 'observe' | 'ta-queue' | 'review' | 'filtered' | 'data-health' | 'compare'

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

function observeStateLabel(state: string): { text: string; cls: string } {
    switch (state) {
        case 'TRIGGERED': return { text: '已触发', cls: 'text-emerald-600 dark:text-emerald-400' }
        case 'EXPIRED': return { text: '已过期', cls: 'text-amber-600 dark:text-amber-400' }
        case 'INVALIDATED': return { text: '已失效', cls: 'text-red-500 dark:text-red-400' }
        default: return { text: '等待中', cls: 'text-slate-500 dark:text-slate-400' }
    }
}

function observeStateBg(state: string): string {
    switch (state) {
        case 'TRIGGERED': return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
        case 'EXPIRED': return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
        case 'INVALIDATED': return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
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

// [TF-UX-002] A-stock color: red=up/triggered, green=down/invalidated
function priceDistanceColor(currentPrice: number | null, triggerPrice: number | null, invalidPrice: number | null): { text: string; cls: string; label: string } {
    if (!triggerPrice || !currentPrice) {
        return { text: '-', cls: 'text-slate-400', label: '' }
    }
    const distPct = ((currentPrice - triggerPrice) / triggerPrice * 100)
    if (currentPrice >= triggerPrice) {
        return { text: `已触发 / +${distPct.toFixed(2)}%`, cls: 'text-red-600 dark:text-red-400 font-medium', label: '已触发' }
    }
    if (invalidPrice && currentPrice <= invalidPrice) {
        const belowPct = ((invalidPrice - currentPrice) / invalidPrice * 100)
        return { text: `已失效 / -${belowPct.toFixed(2)}%`, cls: 'text-emerald-600 dark:text-emerald-400', label: '已失效' }
    }
    const toTrigger = ((triggerPrice - currentPrice) / triggerPrice * 100)
    return { text: `还差 ${toTrigger.toFixed(2)}%`, cls: 'text-blue-600 dark:text-blue-400', label: '等待中' }
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

function ObserveTable({ items, onRun, running, runResult, lastCheckTime }: { items: TradeFlowObserveItem[]; onRun: () => void; running: boolean; runResult: TradeFlowObserveRunResponse | null; lastCheckTime: string | null }) {
    const [showResult, setShowResult] = useState(false)
    return (
        <div>
            <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-700">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-300">盘中观察</span>
                {lastCheckTime && (
                    <span className="inline-flex items-center gap-1 text-xs text-slate-400">
                        <Clock className="h-3 w-3" />
                        最后检查: {lastCheckTime}
                    </span>
                )}
                <button
                    onClick={() => { onRun(); setShowResult(true) }}
                    disabled={running}
                    className="ml-auto inline-flex items-center gap-1.5 rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                    {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                    {running ? '执行中...' : '执行观察'}
                </button>
            </div>
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
            {items.length === 0 && !runResult ? (
                <div className="py-20 text-center text-sm text-slate-400">
                    <div className="mb-3">暂无盘中观察数据</div>
                    <div className="text-xs">点击"执行观察"按钮手动触发盘中检查</div>
                </div>
            ) : items.length === 0 ? null : (
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                <thead>
                    <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                        <th className="px-4 py-2.5 font-medium">代码</th>
                        <th className="px-4 py-2.5 font-medium">名称</th>
                        <th className="px-4 py-2.5 font-medium">状态</th>
                        <th className="px-4 py-2.5 font-medium">触发价</th>
                        <th className="px-4 py-2.5 font-medium">失效价</th>
                        <th className="px-4 py-2.5 font-medium">当前价</th>
                        <th className="px-4 py-2.5 font-medium">触发距离</th>
                        <th className="px-4 py-2.5 font-medium">触发次数</th>
                        <th className="px-4 py-2.5 font-medium">首次触发时间</th>
                        <th className="px-4 py-2.5 font-medium">触发原因</th>
                    </tr>
                </thead>
                <tbody>
                    {items.map(item => {
                        const st = observeStateLabel(item.observe_state)
                        const distInfo = priceDistanceColor(item.current_price, item.trigger_price, item.invalid_price)
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
                                <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{fmtPrice(item.trigger_price)}</td>
                                <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{fmtPrice(item.invalid_price)}</td>
                                <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">
                                    {item.current_price != null ? item.current_price.toFixed(2) : <span className="text-xs text-slate-400">实时行情不可用</span>}
                                </td>
                                <td className="px-4 py-2.5 tabular-nums">
                                    <span className={distInfo.cls}>{distInfo.text}</span>
                                </td>
                                <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{item.observe_trigger_count}</td>
                                <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">{item.observe_first_trigger_time || '-'}</td>
                                <td className="max-w-[200px] truncate px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400" title={item.trigger_reason}>{item.trigger_reason || '-'}</td>
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
    { key: 'compare', label: '候选对比', icon: GitCompare },
    { key: 'observe', label: '盘中观察', icon: Eye },
    { key: 'ta-queue', label: 'TA 队列', icon: ListOrdered },
    { key: 'review', label: '盘后 Review', icon: BarChart3 },
    { key: 'filtered', label: '被过滤', icon: FilterX },
    { key: 'data-health', label: '数据健康', icon: Activity },
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

    const [observeData, setObserveData] = useState<TradeFlowObserveResponse | null>(null)
    const [observeRunLoading, setObserveRunLoading] = useState(false)  // [TF-OBS-001]
    const [observeRunResult, setObserveRunResult] = useState<TradeFlowObserveRunResponse | null>(null)  // [TF-OBS-001]
    const [taQueueData, setTaQueueData] = useState<TradeFlowTAQueueResponse | null>(null)
    const [reviewData, setReviewData] = useState<TradeFlowReviewResponse | null>(null)
    const [filteredData, setFilteredData] = useState<TradeFlowFilteredResponse | null>(null)  // [UI-007] tradeflow_filtered_trace

    // [TF-UX-001] tiered candidates state
    const [tieredData, setTieredData] = useState<TradeFlowTieredCandidatesResponse | null>(null)
    const [showScan, setShowScan] = useState(false)
    const [viewMode, setViewMode] = useState<'tiered' | 'table'>('tiered')

    // [TF-UX-002] auto-refresh state
    const [lastObserveCheckTime, setLastObserveCheckTime] = useState<string | null>(null)
    const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null)

    // [TF-UX-003] review generation
    const [reviewGenerating, setReviewGenerating] = useState(false)

    // [UI-010] mandate_candidate_compare
    const [compareData, setCompareData] = useState<TradeFlowCandidateItem[]>([])
    const [compareSortBy, setCompareSortBy] = useState<CompareSortKey>('mandate_score')

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
    const handleGenerateReview = useCallback(async () => {
        setReviewGenerating(true)
        try {
            await api.generateTradeFlowReview(tradeDate)
            await fetchReview(tradeDate)
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '生成复盘失败')
        } finally {
            setReviewGenerating(false)
        }
    }, [tradeDate, fetchReview])

    const fetchData = useCallback(async (date: string) => {
        if (activeTab === 'candidates') {
            await fetchCandidates(date)
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
        }
    }, [activeTab, fetchCandidates, fetchCompare, fetchObserve, fetchTaQueue, fetchReview, fetchDataHealth, fetchFiltered])

    useEffect(() => {
        void fetchData(tradeDate)
    }, [tradeDate, fetchData])

    // [TF-UX-002] auto-refresh for observe tab
    useEffect(() => {
        if (autoRefreshRef.current) {
            clearInterval(autoRefreshRef.current)
            autoRefreshRef.current = null
        }
        if (activeTab === 'observe') {
            const interval = isInMarketHours() ? 3 * 60 * 1000 : 5 * 60 * 1000
            autoRefreshRef.current = setInterval(() => {
                void fetchObserve(tradeDate)
            }, interval)
        }
        return () => {
            if (autoRefreshRef.current) {
                clearInterval(autoRefreshRef.current)
            }
        }
    }, [activeTab, tradeDate, fetchObserve, isInMarketHours])

    const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setTradeDate(e.target.value)
    }

    const handleRowClick = (c: TradeFlowCandidateItem) => {
        setSelectedCandidate(c)
        setDrawerOpen(true)
    }

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

    // [TF-UX-001] tiered candidate card renderer
    const renderTieredCandidateCard = (c: TradeFlowCandidateItem) => {
        const at = actionTierBadge(c.action_tier)
        const ct = candidateTypeLabel(c.candidate_type)
        return (
            <div
                key={c.symbol}
                className="cursor-pointer rounded-lg border border-slate-100 p-3 transition-colors hover:border-blue-200 hover:bg-blue-50/30 dark:border-slate-700 dark:hover:border-blue-800 dark:hover:bg-blue-900/10"
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
                    <div className="py-20 text-center text-sm text-slate-400">
                        <Target className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                        {tradeDate} 暂无候选数据
                        <div className="mt-2 text-xs text-slate-400">尚未生成候选池，或接口/数据源暂时不可用</div>
                    </div>
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
                                return (
                                    <tr
                                        key={c.symbol}
                                        className="cursor-pointer border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
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
            return <ObserveTable items={observeData?.observe_items ?? []} onRun={handleRunObserve} running={observeRunLoading} runResult={observeRunResult} lastCheckTime={lastObserveCheckTime} />
        }

        if (activeTab === 'ta-queue') {
            if (!taQueueData) {
                return <div className="py-20 text-center text-sm text-slate-400">暂无 TA 队列数据</div>
            }
            return <TAQueueTable items={taQueueData.queue} meta={taQueueData} />
        }

        if (activeTab === 'review') {
            if (!reviewData || reviewData.status === 'no_data') {
                return (
                    <div className="py-20 text-center text-sm text-slate-400">
                        <BarChart3 className="mx-auto mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
                        <div className="mb-4">尚未生成盘后复盘</div>
                        <button
                            onClick={() => void handleGenerateReview()}
                            disabled={reviewGenerating}
                            className="inline-flex items-center gap-1.5 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                        >
                            {reviewGenerating ? <Loader2 className="h-4 w-4 animate-spin" /> : <BarChart3 className="h-4 w-4" />}
                            {reviewGenerating ? '生成中...' : '生成今日复盘'}
                        </button>
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
            return <DataHealthPanel data={dataHealth} />
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
                <div className="card p-4">
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
                                onClick={() => setActiveTab(tab.key)}
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
