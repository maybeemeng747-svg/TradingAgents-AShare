// [TRACK-003] tracking_board_v2_frontend
import {
    AlertTriangle,
    Eye,
    FileText,
    Loader2,
    RefreshCw,
    ShieldAlert,
    TrendingUp,
    Wallet,
    Info,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '@/services/api'
import { useAuthStore } from '@/stores/authStore'
import type {
    TrackingBoardItem,
    TrackingBoardV2Response,
    TrackingBoardV2Guidance,
    ObservationItemV2,
    TrackingBoardV2DataFreshness,
} from '@/types'

// ─── helpers ───────────────────────────────────────────────

function fmtPrice(v?: number | null): string {
    if (v == null || !Number.isFinite(v)) return '--'
    return v.toFixed(2)
}

function fmtPct(v?: number | null): string {
    if (v == null || !Number.isFinite(v)) return '--'
    const sign = v >= 0 ? '+' : ''
    return `${sign}${v.toFixed(2)}%`
}

function pctColor(v?: number | null): string {
    if (v == null) return 'text-slate-400'
    return v >= 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'
}

function pctBg(v?: number | null): string {
    if (v == null) return 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500'
    return v >= 0
        ? 'bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-400'
        : 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400'
}

function priorityBadge(p: string): { label: string; cls: string } {
    switch (p) {
        case 'P0': return { label: '紧急', cls: 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300' }
        case 'P1': return { label: '关注', cls: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300' }
        case 'P2': return { label: '留意', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300' }
        default:  return { label: '记录', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400' }
    }
}

function guidanceTypeLabel(t: string): string {
    const m: Record<string, string> = {
        holdings_risk: '持仓风险',
        holdings_no_analysis: '未分析',
        observation_in_entry_zone: '进入买点',
        observation_near_entry: '接近买点',
        observation_invalidated: '已失效',
        observation_ta_required: '需TA确认',
        observation_data_missing: '数据缺失',
    }
    return m[t] || t
}

function obsStatusLabel(s: string): string {
    const m: Record<string, string> = {
        watching: '观察中',
        near_entry: '接近买点',
        in_entry_zone: '买点区间',
        ta_required: '需TA确认',
        entered: '已入场',
        invalidated: '已失效',
        removed: '已移除',
    }
    return m[s] || s
}

function obsStatusCls(s: string): string {
    if (s === 'in_entry_zone') return 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300'
    if (s === 'near_entry') return 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300'
    if (s === 'invalidated') return 'bg-slate-200 text-slate-500 dark:bg-slate-600 dark:text-slate-400 line-through'
    if (s === 'ta_required') return 'bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300'
    if (s === 'entered') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300'
    return 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
}

function fmtMoney(v: number): string {
    const abs = Math.abs(v)
    if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
    if (abs >= 1e4) return `${(v / 1e4).toFixed(2)}万`
    return v.toFixed(2)
}

// ─── main component ────────────────────────────────────────

export default function TrackingBoardV2Panel() {
    const { user } = useAuthStore()
    const navigate = useNavigate()
    const [data, setData] = useState<TrackingBoardV2Response | null>(null)
    const [loading, setLoading] = useState(true)
    const [refreshing, setRefreshing] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [activeTab, setActiveTab] = useState<'holdings' | 'observation' | 'guidance' | 'review'>('holdings')

    const fetchData = useCallback(async (silent: boolean) => {
        if (silent) setRefreshing(true)
        else setLoading(true)
        try {
            const resp = await api.getDashboardTrackingBoardV2()
            setData(resp)
            setError(null)
        } catch (e) {
            setError(e instanceof Error ? e.message : '加载失败')
        } finally {
            setLoading(false)
            setRefreshing(false)
        }
    }, [])

    useEffect(() => {
        if (!user?.id) return
        let cancelled = false
        const load = async () => { if (!cancelled) await fetchData(false) }
        void load()
        const iv = setInterval(() => { if (!cancelled) void fetchData(true) }, (data?.refresh_interval_seconds ?? 20) * 1000)
        return () => { cancelled = true; clearInterval(iv) }
    }, [user?.id, fetchData, data?.refresh_interval_seconds])

    const freshness = data?.data_freshness
    const alertCount = data?.alerts?.length ?? 0

    // Tab config
    const tabs = useMemo(() => [
        { id: 'holdings' as const, label: '持仓', icon: Wallet, count: data?.holdings?.length ?? 0 },
        { id: 'observation' as const, label: '观察仓', icon: Eye, count: data?.observation_items?.length ?? 0 },
        { id: 'guidance' as const, label: '今日指引', icon: ShieldAlert, count: data?.today_guidance?.length ?? 0, alert: alertCount },
        { id: 'review' as const, label: '盘后复盘', icon: FileText, count: data?.review_summary ? 1 : 0 },
    ], [data, alertCount])

    // Holdings stats
    const totalValue = (data?.holdings ?? []).reduce((s, h) => s + (h.live_market_value ?? h.market_value ?? 0), 0)
    const totalPnl = (data?.holdings ?? []).reduce((s, h) => s + (h.floating_pnl ?? 0), 0)

    if (loading && !data) {
        return (
            <div className="flex items-center justify-center py-20 text-slate-500">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                正在加载跟踪看板...
            </div>
        )
    }

    return (
        <div className="space-y-4">
            {/* Header */}
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">跟踪看板 v2</h1>
                    <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
                        <span className="rounded-full bg-slate-100 px-2.5 py-0.5 dark:bg-slate-700/70">
                            交易日：{data?.is_trading_day ? '是' : '否'}
                        </span>
                        <span className="rounded-full bg-slate-100 px-2.5 py-0.5 dark:bg-slate-700/70">
                            上一个交易日：{data?.previous_trade_date || '--'}
                        </span>
                        {freshness && (
                            <span className={`rounded-full px-2.5 py-0.5 ${
                                freshness.status === 'fresh'
                                    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300'
                                    : freshness.status === 'stale'
                                        ? 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300'
                                        : 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
                            }`}>
                                数据：{freshness.status === 'fresh' ? '实时' : freshness.status === 'stale' ? '过期' : '非交易日'}
                            </span>
                        )}
                        <span className="text-slate-400">{data?.as_of}</span>
                        {refreshing && <RefreshCw className="h-3 w-3 animate-spin text-slate-400" />}
                    </div>
                </div>
            </div>

            {/* Stats bar */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatCard label="持仓总市值" value={`¥${fmtMoney(totalValue)}`} />
                <StatCard label="持仓浮盈亏" value={`¥${fmtMoney(totalPnl)}`} tone={totalPnl >= 0 ? 'up' : 'down'} />
                <StatCard label="观察仓" value={`${data?.observation_items?.length ?? 0} 只`} />
                <StatCard label="今日指引" value={`${data?.today_guidance?.length ?? 0} 条`} tone={alertCount > 0 ? 'alert' : undefined} />
            </div>

            {/* Tabs */}
            <div className="flex gap-1 rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        type="button"
                        onClick={() => setActiveTab(tab.id)}
                        className={`relative flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                            activeTab === tab.id
                                ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-slate-100'
                                : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
                        }`}
                    >
                        <tab.icon className="h-4 w-4" />
                        {tab.label}
                        {tab.count > 0 && (
                            <span className={`ml-0.5 inline-flex h-5 min-w-[20px] items-center justify-center rounded-full px-1 text-[10px] font-bold ${
                                tab.alert
                                    ? 'bg-rose-500 text-white'
                                    : activeTab === tab.id
                                        ? 'bg-slate-200 text-slate-700 dark:bg-slate-600 dark:text-slate-200'
                                        : 'bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
                            }`}>
                                {tab.count}
                            </span>
                        )}
                    </button>
                ))}
            </div>

            {/* Error */}
            {error && (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
                    <AlertTriangle className="mr-1.5 inline h-4 w-4" />
                    {error}
                </div>
            )}

            {/* Content */}
            {activeTab === 'holdings' && <HoldingsZone items={data?.holdings ?? []} onAnalyze={s => navigate(`/analysis?symbol=${s}`)} />}
            {activeTab === 'observation' && <ObservationZone items={data?.observation_items ?? []} />}
            {activeTab === 'guidance' && <GuidanceZone guidance={data?.today_guidance ?? []} onAnalyze={s => navigate(`/analysis?symbol=${s}`)} />}
            {activeTab === 'review' && <ReviewZone summary={data?.review_summary} freshness={freshness} />}
        </div>
    )
}

// ─── Stat card ─────────────────────────────────────────────

function StatCard({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' | 'alert' }) {
    const valueCls = tone === 'up' ? 'text-rose-600 dark:text-rose-400'
        : tone === 'down' ? 'text-emerald-600 dark:text-emerald-400'
        : tone === 'alert' ? 'text-rose-600 dark:text-rose-400'
        : 'text-slate-900 dark:text-slate-100'
    return (
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-900">
            <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
            <div className={`mt-1 text-lg font-semibold ${valueCls}`}>{value}</div>
        </div>
    )
}

// ─── Holdings Zone ─────────────────────────────────────────

function HoldingsZone({ items, onAnalyze }: { items: TrackingBoardItem[]; onAnalyze: (s: string) => void }) {
    if (items.length === 0) {
        return <EmptyState icon={Wallet} title="暂无持仓" desc="导入持仓后，这里会展示实时行情和 TA 报告摘要。" />
    }
    return (
        <div className="space-y-2">
            {items.map(item => (
                <HoldingsRow key={item.symbol} item={item} onAnalyze={onAnalyze} />
            ))}
        </div>
    )
}

function HoldingsRow({ item, onAnalyze }: { item: TrackingBoardItem; onAnalyze: (s: string) => void }) {
    const changePct = item.price_change_pct ?? null
    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4 transition-colors hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800/80">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                {/* Left: symbol + price */}
                <div className="flex items-start gap-3">
                    <div>
                        <div className="text-lg font-semibold text-slate-900 dark:text-slate-100">{item.name}</div>
                        <div className="text-sm text-slate-500 dark:text-slate-400">{item.symbol}</div>
                    </div>
                    <div className="ml-4 text-right sm:ml-6">
                        <div className={`text-xl font-bold ${pctColor(changePct)}`}>{fmtPrice(item.live_price)}</div>
                        <span className={`inline-flex rounded-full px-2 py-0.5 text-sm font-semibold ${pctBg(changePct)}`}>
                            {fmtPct(changePct)}
                        </span>
                    </div>
                </div>

                {/* Right: metrics */}
                <div className="flex flex-wrap items-center gap-3 text-sm">
                    <Metric label="持仓" value={item.current_position != null ? `${item.current_position}股` : '--'} />
                    <Metric label="成本" value={fmtPrice(item.average_cost)} />
                    <Metric label="市值" value={item.live_market_value != null ? `¥${fmtMoney(item.live_market_value)}` : '--'} />
                    <Metric label="浮盈" value={item.floating_pnl_pct != null ? fmtPct(item.floating_pnl_pct) : '--'} tone={item.floating_pnl_pct} />
                    {item.analysis && (
                        <button
                            type="button"
                            onClick={() => onAnalyze(item.symbol)}
                            className="inline-flex items-center gap-1 rounded-lg bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-600 hover:bg-blue-100 dark:bg-blue-500/10 dark:text-blue-400 dark:hover:bg-blue-500/20"
                        >
                            <TrendingUp className="h-3 w-3" />
                            {item.analysis.action_label || item.analysis.decision || '查看报告'}
                        </button>
                    )}
                    {!item.analysis && (
                        <span className="inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                            <Info className="h-3 w-3" />
                            未分析
                        </span>
                    )}
                </div>
            </div>
        </div>
    )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: number | null }) {
    return (
        <div className="text-center">
            <div className="text-[11px] text-slate-400">{label}</div>
            <div className={`text-sm font-medium ${tone != null ? pctColor(tone) : 'text-slate-700 dark:text-slate-200'}`}>{value}</div>
        </div>
    )
}

// ─── Observation Zone ──────────────────────────────────────

function ObservationZone({ items }: { items: ObservationItemV2[] }) {
    if (items.length === 0) {
        return <EmptyState icon={Eye} title="观察仓为空" desc="在候选池或分析报告中可以将标的一键加入观察仓。" />
    }
    return (
        <div className="space-y-2">
            {items.map(item => (
                <ObservationRow key={item.id} item={item} />
            ))}
        </div>
    )
}

function ObservationRow({ item }: { item: ObservationItemV2 }) {
    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <div className="flex items-center gap-2">
                        <span className="text-lg font-semibold text-slate-900 dark:text-slate-100">{item.name || item.symbol}</span>
                        <span className="text-sm text-slate-400">{item.symbol}</span>
                        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${obsStatusCls(item.status)}`}>
                            {obsStatusLabel(item.status)}
                        </span>
                    </div>
                    {item.reason && (
                        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{item.reason}</div>
                    )}
                </div>

                <div className="flex flex-wrap items-center gap-3 text-sm">
                    {item.live_price != null && (
                        <>
                            <div className="text-center">
                                <div className="text-[11px] text-slate-400">现价</div>
                                <div className={`font-medium ${pctColor(item.price_change_pct)}`}>{fmtPrice(item.live_price)}</div>
                            </div>
                            {item.price_change_pct != null && (
                                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${pctBg(item.price_change_pct)}`}>
                                    {fmtPct(item.price_change_pct)}
                                </span>
                            )}
                        </>
                    )}
                    {(item.entry_low ?? 0) > 0 && (
                        <div className="text-center">
                            <div className="text-[11px] text-slate-400">买入区间</div>
                            <div className="text-sm font-medium text-slate-700 dark:text-slate-200">
                                {fmtPrice(item.entry_low)} ~ {fmtPrice(item.entry_high)}
                            </div>
                        </div>
                    )}
                    {(item.invalid_price ?? 0) > 0 && (
                        <div className="text-center">
                            <div className="text-[11px] text-slate-400">失效价</div>
                            <div className="text-sm font-medium text-slate-700 dark:text-slate-200">{fmtPrice(item.invalid_price)}</div>
                        </div>
                    )}
                    {item.source && (
                        <div className="text-center">
                            <div className="text-[11px] text-slate-400">来源</div>
                            <div className="text-xs text-slate-500 dark:text-slate-400">{item.source}</div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}

// ─── Guidance Zone ─────────────────────────────────────────

function GuidanceZone({ guidance, onAnalyze }: { guidance: TrackingBoardV2Guidance[]; onAnalyze: (s: string) => void }) {
    const grouped = useMemo(() => {
        const g: Record<string, TrackingBoardV2Guidance[]> = {}
        for (const item of guidance) {
            const p = item.priority
            if (!g[p]) g[p] = []
            g[p].push(item)
        }
        return g
    }, [guidance])

    if (guidance.length === 0) {
        return <EmptyState icon={ShieldAlert} title="今日无指引" desc="持仓和观察仓暂无需要特别关注的事项。" />
    }

    return (
        <div className="space-y-4">
            {(['P0', 'P1', 'P2', 'P3'] as const).map(p => {
                const items = grouped[p]
                if (!items?.length) return null
                const badge = priorityBadge(p)
                return (
                    <div key={p}>
                        <div className="mb-2 flex items-center gap-2">
                            <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${badge.cls}`}>{badge.label} ({p})</span>
                        </div>
                        <div className="space-y-1.5">
                            {items.map((g, i) => (
                                <div key={`${g.symbol}-${g.type}-${i}`} className="rounded-lg border border-slate-200 bg-white px-4 py-2.5 dark:border-slate-700 dark:bg-slate-900">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="flex-1">
                                            <div className="flex items-center gap-2">
                                                <button
                                                    type="button"
                                                    onClick={() => onAnalyze(g.symbol)}
                                                    className="text-sm font-semibold text-slate-900 hover:text-blue-600 dark:text-slate-100 dark:hover:text-blue-400"
                                                >
                                                    {g.name} <span className="text-slate-400">{g.symbol}</span>
                                                </button>
                                                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-700 dark:text-slate-400">
                                                    {guidanceTypeLabel(g.type)}
                                                </span>
                                            </div>
                                            <div className="mt-1 text-sm text-slate-600 dark:text-slate-300">{g.reason}</div>
                                        </div>
                                        <div className="text-[10px] text-slate-400">{g.as_of.split(' ')[1]}</div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                )
            })}
        </div>
    )
}

// ─── Review Zone ───────────────────────────────────────────

function ReviewZone({ summary, freshness }: { summary: any; freshness?: TrackingBoardV2DataFreshness | null }) {
    if (!summary) {
        return (
            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center dark:border-slate-600 dark:bg-slate-800/50">
                <FileText className="mx-auto mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
                <p className="text-slate-500 dark:text-slate-400">盘后复盘尚未生成</p>
                <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                    {freshness?.status === 'non_trading_day'
                        ? '当前为非交易日，收盘后将自动生成复盘摘要。'
                        : '收盘后将自动生成今日复盘摘要，包含持仓表现和观察仓状态回顾。'}
                </p>
            </div>
        )
    }
    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <pre className="text-sm text-slate-700 dark:text-slate-300 whitespace-pre-wrap">{JSON.stringify(summary, null, 2)}</pre>
        </div>
    )
}

// ─── Empty State ───────────────────────────────────────────

function EmptyState({ icon: Icon, title, desc }: { icon: typeof Wallet; title: string; desc: string }) {
    return (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center dark:border-slate-600 dark:bg-slate-800/50">
            <Icon className="mx-auto mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
            <p className="font-medium text-slate-600 dark:text-slate-300">{title}</p>
            <p className="mt-1 text-sm text-slate-400 dark:text-slate-500">{desc}</p>
        </div>
    )
}
