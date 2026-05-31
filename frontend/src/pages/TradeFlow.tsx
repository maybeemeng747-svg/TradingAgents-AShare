import { useState, useEffect, useCallback } from 'react'
import { Target, Loader2, AlertCircle, Calendar, Filter, Eye, RefreshCw } from 'lucide-react'
import { api } from '@/services/api'
import type { TradeFlowCandidateItem, TradeFlowSummary, TradeFlowDataHealthResponse } from '@/types'
import TradeFlowCandidateDrawer from '@/components/TradeFlowCandidateDrawer'

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
        case 'INVALIDATED': return { text: '已失效', cls: 'text-red-500 dark:text-red-400' }
        default: return { text: '等待中', cls: 'text-slate-500 dark:text-slate-400' }
    }
}

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
                        {dataHealth.sources.map(s => (
                            <span
                                key={s.name}
                                className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${
                                    s.available
                                        ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                                        : 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
                                }`}
                            >
                                <span className={`h-1.5 w-1.5 rounded-full ${s.available ? 'bg-emerald-500' : 'bg-red-500'}`} />
                                {s.name} ({s.record_count})
                            </span>
                        ))}
                    </div>
                </div>
            )}
        </div>
    )
}

export default function TradeFlow() {
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

    const [tierFilter, setTierFilter] = useState<string>('')
    const [deepTaFilter, setDeepTaFilter] = useState<string>('')
    const [observeFilter, setObserveFilter] = useState<string>('')
    const [drawerOpen, setDrawerOpen] = useState(false)
    const [selectedCandidate, setSelectedCandidate] = useState<TradeFlowCandidateItem | null>(null)

    const fetchData = useCallback(async (date: string) => {
        setLoading(true)
        setError(null)
        try {
            const [candidatesRes, healthRes] = await Promise.all([
                api.getTradeFlowCandidates(date, tierFilter || undefined, deepTaFilter === 'yes' ? true : deepTaFilter === 'no' ? false : undefined),
                api.getTradeFlowDataHealth(),
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
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }, [tierFilter, deepTaFilter, observeFilter])

    useEffect(() => {
        void fetchData(tradeDate)
    }, [tradeDate, fetchData])

    const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setTradeDate(e.target.value)
    }

    const handleRowClick = (c: TradeFlowCandidateItem) => {
        setSelectedCandidate(c)
        setDrawerOpen(true)
    }

    const handleDrawerClose = () => {
        setDrawerOpen(false)
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <Target className="h-5 w-5 text-blue-600 dark:text-blue-400" />
                    <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100">TradeFlow 候选池</h1>
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

            <SummaryCards summary={summary} dataHealth={dataHealth} />

            <div className="card">
                <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-700">
                    <div className="flex items-center gap-1.5 text-sm text-slate-500">
                        <Filter className="h-3.5 w-3.5" />
                        筛选
                    </div>
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

                {loading ? (
                    <div className="flex items-center justify-center py-20 text-slate-400">
                        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                        加载中...
                    </div>
                ) : error ? (
                    <div className="flex items-center justify-center py-20 text-red-500">
                        <AlertCircle className="mr-2 h-5 w-5" />
                        {error}
                    </div>
                ) : status === 'no_data' ? (
                    <div className="py-20 text-center text-sm text-slate-400">
                        {tradeDate} 暂无候选数据
                    </div>
                ) : candidates.length === 0 ? (
                    <div className="py-20 text-center text-sm text-slate-400">
                        无匹配候选
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-slate-100 text-left text-xs text-slate-500 dark:border-slate-700">
                                    <th className="px-4 py-2.5 font-medium">代码</th>
                                    <th className="px-4 py-2.5 font-medium">名称</th>
                                    <th className="px-4 py-2.5 font-medium">层级</th>
                                    <th className="px-4 py-2.5 font-medium">评分</th>
                                    <th className="px-4 py-2.5 font-medium">策略标签</th>
                                    <th className="px-4 py-2.5 font-medium">触发价</th>
                                    <th className="px-4 py-2.5 font-medium">失效价</th>
                                    <th className="px-4 py-2.5 font-medium">完整度</th>
                                    <th className="px-4 py-2.5 font-medium">深度TA</th>
                                    <th className="px-4 py-2.5 font-medium">观察状态</th>
                                    <th className="px-4 py-2.5 font-medium">动作</th>
                                </tr>
                            </thead>
                            <tbody>
                                {candidates.map(c => {
                                    const act = actionLabel(c.action)
                                    const obs = observeStateLabel(c.observe_state)
                                    return (
                                        <tr
                                            key={c.symbol}
                                            className="cursor-pointer border-b border-slate-50 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                                            onClick={() => handleRowClick(c)}
                                        >
                                            <td className="px-4 py-2.5 font-mono text-xs font-semibold text-slate-900 dark:text-slate-100">{c.symbol}</td>
                                            <td className="max-w-[120px] truncate px-4 py-2.5 text-slate-700 dark:text-slate-300">{c.name}</td>
                                            <td className="px-4 py-2.5">
                                                <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-bold ${tierBadgeClass(c.tier)}`}>{c.tier || '-'}</span>
                                            </td>
                                            <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{c.composite_score.toFixed(2)}</td>
                                            <td className="max-w-[200px] px-4 py-2.5">
                                                <div className="flex flex-wrap gap-1">
                                                    {c.strategy_tags.slice(0, 3).map(tag => (
                                                        <span key={tag} className="inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 dark:bg-slate-700 dark:text-slate-300">{tag}</span>
                                                    ))}
                                                    {c.strategy_tags.length > 3 && (
                                                        <span className="text-[11px] text-slate-400">+{c.strategy_tags.length - 3}</span>
                                                    )}
                                                </div>
                                            </td>
                                            <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{c.trigger_price != null ? c.trigger_price.toFixed(2) : '-'}</td>
                                            <td className="px-4 py-2.5 tabular-nums text-slate-700 dark:text-slate-300">{c.invalid_price != null ? c.invalid_price.toFixed(2) : '-'}</td>
                                            <td className="px-4 py-2.5">
                                                <div className="flex items-center gap-1.5">
                                                    <div className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                                                        <div
                                                            className="h-full rounded-full bg-blue-500"
                                                            style={{ width: `${Math.min(100, c.tradeflow_data_completeness * 100)}%` }}
                                                        />
                                                    </div>
                                                    <span className="tabular-nums text-xs text-slate-500">{(c.tradeflow_data_completeness * 100).toFixed(0)}%</span>
                                                </div>
                                            </td>
                                            <td className="px-4 py-2.5">
                                                {c.need_deep_ta ? (
                                                    <span className="inline-block rounded bg-purple-100 px-1.5 py-0.5 text-[11px] font-medium text-purple-700 dark:bg-purple-900/40 dark:text-purple-300">是</span>
                                                ) : (
                                                    <span className="text-xs text-slate-400">否</span>
                                                )}
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
                )}
            </div>

            {selectedCandidate && (
                <TradeFlowCandidateDrawer
                    candidate={selectedCandidate}
                    tradeDate={tradeDate}
                    open={drawerOpen}
                    onClose={handleDrawerClose}
                />
            )}
        </div>
    )
}
