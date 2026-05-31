import { useEffect, useState } from 'react'
import { X, Loader2, CheckCircle2, XCircle, AlertTriangle, Shield, BarChart3, FileCheck, Lightbulb } from 'lucide-react'
import { api } from '@/services/api'
import type { TradeFlowCandidateItem, TradeFlowCandidateDetail } from '@/types'

const ALL_STRATEGIES = [
    'VCP',
    'PULLBACK_SUPPORT',
    'EVENT_CATALYST',
    'POLICY_VERSION',
    'NARRATIVE_QUALITY',
    'FUND_FLOW_ANOMALY',
]

function gameBalanceColor(balance: string): string {
    switch (balance) {
        case 'favorable': return 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/30 border-emerald-200 dark:border-emerald-800'
        case 'neutral': return 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/30 border-amber-200 dark:border-amber-800'
        case 'crowded': return 'text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/30 border-orange-200 dark:border-orange-800'
        case 'fragile': return 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-800'
        default: return 'text-slate-600 dark:text-slate-400 bg-slate-50 dark:bg-slate-800 border-slate-200 dark:border-slate-700'
    }
}

function gameBalanceLabel(balance: string): string {
    switch (balance) {
        case 'favorable': return '有利'
        case 'neutral': return '中性'
        case 'crowded': return '拥挤'
        case 'fragile': return '脆弱'
        default: return balance || '未知'
    }
}

function SectionTitle({ icon: Icon, title }: { icon: React.ElementType; title: string }) {
    return (
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
            <Icon className="h-4 w-4" />
            {title}
        </div>
    )
}

function ProgressBar({ value, label }: { value: number; label?: string }) {
    const pct = Math.min(100, Math.round(value * 100))
    const color = pct >= 80 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-red-500'
    return (
        <div>
            {label && <div className="mb-1 text-xs text-slate-500 dark:text-slate-400">{label}</div>}
            <div className="flex items-center gap-2">
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                    <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
                </div>
                <span className="text-xs tabular-nums text-slate-600 dark:text-slate-400">{pct}%</span>
            </div>
        </div>
    )
}

interface TradeFlowCandidateDrawerProps {
    candidate: TradeFlowCandidateItem
    tradeDate: string
    open: boolean
    onClose: () => void
}

export default function TradeFlowCandidateDrawer({ candidate, tradeDate, open, onClose }: TradeFlowCandidateDrawerProps) {
    const [detail, setDetail] = useState<TradeFlowCandidateDetail | null>(null)
    const [loadingDetail, setLoadingDetail] = useState(false)

    useEffect(() => {
        if (!open) return
        let cancelled = false
        setLoadingDetail(true)
        setDetail(null)
        api.getTradeFlowCandidateDetail(candidate.symbol, tradeDate)
            .then(res => {
                if (!cancelled && res.candidate) {
                    setDetail(res.candidate)
                }
            })
            .catch(() => {})
            .finally(() => {
                if (!cancelled) setLoadingDetail(false)
            })
        return () => { cancelled = true }
    }, [open, candidate.symbol, tradeDate])

    useEffect(() => {
        if (!open) return
        const handleKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose()
        }
        document.addEventListener('keydown', handleKey)
        return () => document.removeEventListener('keydown', handleKey)
    }, [open, onClose])

    useEffect(() => {
        if (open) {
            document.body.style.overflow = 'hidden'
        } else {
            document.body.style.overflow = ''
        }
        return () => { document.body.style.overflow = '' }
    }, [open])

    const data = detail ?? candidate

    return (
        <>
            {open && (
                <div
                    className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm transition-opacity"
                    onClick={onClose}
                />
            )}
            <div
                className={`fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-slate-200 bg-white shadow-xl transition-transform duration-300 dark:border-slate-700 dark:bg-slate-900 ${
                    open ? 'translate-x-0' : 'translate-x-full'
                }`}
            >
                <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-700">
                    <div>
                        <div className="flex items-center gap-2">
                            <span className="font-mono text-sm font-bold text-slate-900 dark:text-slate-100">{candidate.symbol}</span>
                            <span className="text-sm text-slate-600 dark:text-slate-400">{candidate.name}</span>
                        </div>
                        <div className="mt-1 flex items-center gap-2">
                            <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-bold ${
                                candidate.tier === 'A' ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                                : candidate.tier === 'B' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                                : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                            }`}>{candidate.tier}级</span>
                            <span className="text-xs tabular-nums text-slate-500 dark:text-slate-400">评分 {candidate.composite_score.toFixed(2)}</span>
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
                    >
                        <X className="h-5 w-5" />
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
                    {loadingDetail && (
                        <div className="flex items-center gap-2 text-xs text-slate-400">
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            加载详情...
                        </div>
                    )}

                    <div>
                        <SectionTitle icon={Lightbulb} title="策略命中" />
                        <div className="mt-2 flex flex-wrap gap-1.5">
                            {ALL_STRATEGIES.map(tag => {
                                const hit = data.strategy_tags.includes(tag)
                                return (
                                    <span
                                        key={tag}
                                        className={`inline-block rounded px-2 py-0.5 text-[11px] font-medium ${
                                            hit
                                                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                                                : 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500'
                                        }`}
                                    >
                                        {hit ? '● ' : '○ '}{tag}
                                    </span>
                                )
                            })}
                        </div>
                        {data.strategy_tags.length > 0 && (
                            <div className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                                主策略: <span className="font-medium text-slate-700 dark:text-slate-300">{data.primary_strategy}</span>
                            </div>
                        )}
                    </div>

                    <div>
                        <SectionTitle icon={BarChart3} title="多空博弈" />
                        <div className="mt-2 space-y-2">
                            <div className={`inline-block rounded border px-2.5 py-1 text-xs font-medium ${gameBalanceColor(data.game_balance)}`}>
                                {gameBalanceLabel(data.game_balance)}
                                {data.resonance_count > 0 && <span className="ml-1.5 opacity-75">共振 ×{data.resonance_count}</span>}
                            </div>
                            {data.bull_case && (
                                <div className="rounded-lg bg-emerald-50/50 p-3 dark:bg-emerald-900/10">
                                    <div className="text-[11px] font-medium text-emerald-600 dark:text-emerald-400">多头观点</div>
                                    <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{data.bull_case}</div>
                                </div>
                            )}
                            {data.bear_case && (
                                <div className="rounded-lg bg-red-50/50 p-3 dark:bg-red-900/10">
                                    <div className="text-[11px] font-medium text-red-600 dark:text-red-400">空头观点</div>
                                    <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{data.bear_case}</div>
                                </div>
                            )}
                            {data.policy_case && (
                                <div className="rounded-lg bg-blue-50/50 p-3 dark:bg-blue-900/10">
                                    <div className="text-[11px] font-medium text-blue-600 dark:text-blue-400">政策观点</div>
                                    <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{data.policy_case}</div>
                                </div>
                            )}
                            {data.fund_flow_case && (
                                <div className="rounded-lg bg-purple-50/50 p-3 dark:bg-purple-900/10">
                                    <div className="text-[11px] font-medium text-purple-600 dark:text-purple-400">资金流观点</div>
                                    <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{data.fund_flow_case}</div>
                                </div>
                            )}
                        </div>
                    </div>

                    <div>
                        <SectionTitle icon={FileCheck} title="证据门禁" />
                        <div className="mt-2 space-y-3">
                            <ProgressBar value={data.tradeflow_data_completeness} label="数据完整度" />
                            <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-500 dark:text-slate-400">单位验证:</span>
                                {data.fund_flow_unit_verified ? (
                                    <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                                        <CheckCircle2 className="h-3.5 w-3.5" /> 已验证
                                    </span>
                                ) : (
                                    <span className="flex items-center gap-1 text-xs text-red-500 dark:text-red-400">
                                        <XCircle className="h-3.5 w-3.5" /> 未验证
                                    </span>
                                )}
                            </div>
                            <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-500 dark:text-slate-400">门禁已应用:</span>
                                {data.evidence_gate_applied ? (
                                    <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                                        <CheckCircle2 className="h-3.5 w-3.5" /> 是
                                    </span>
                                ) : (
                                    <span className="flex items-center gap-1 text-xs text-slate-400">
                                        <XCircle className="h-3.5 w-3.5" /> 否
                                    </span>
                                )}
                            </div>
                            {data.missing_data_fields.length > 0 && (
                                <div>
                                    <div className="text-xs text-slate-500 dark:text-slate-400">缺失数据字段:</div>
                                    <div className="mt-1 flex flex-wrap gap-1">
                                        {data.missing_data_fields.map(f => (
                                            <span key={f} className="inline-block rounded bg-red-50 px-1.5 py-0.5 text-[11px] text-red-600 dark:bg-red-900/20 dark:text-red-400">{f}</span>
                                        ))}
                                    </div>
                                </div>
                            )}
                            {data.missing_evidence.length > 0 && (
                                <div>
                                    <div className="text-xs text-slate-500 dark:text-slate-400">缺失证据:</div>
                                    <div className="mt-1 flex flex-wrap gap-1">
                                        {data.missing_evidence.map(e => (
                                            <span key={e} className="inline-block rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-600 dark:bg-amber-900/20 dark:text-amber-400">{e}</span>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>

                    <div>
                        <SectionTitle icon={AlertTriangle} title="TA 建议" />
                        <div className="mt-2 space-y-2">
                            {data.need_deep_ta ? (
                                <div className="rounded-lg border border-purple-200 bg-purple-50/50 p-3 dark:border-purple-800 dark:bg-purple-900/10">
                                    <div className="text-[11px] font-medium text-purple-600 dark:text-purple-400">需要深度 TA</div>
                                    {data.why_deep_ta && (
                                        <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{data.why_deep_ta}</div>
                                    )}
                                </div>
                            ) : (
                                <div className="rounded-lg border border-slate-200 bg-slate-50/50 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                    <div className="text-[11px] font-medium text-slate-500 dark:text-slate-400">暂无需深度 TA</div>
                                    {data.why_not_deep_ta && (
                                        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{data.why_not_deep_ta}</div>
                                    )}
                                </div>
                            )}
                            {detail && detail.what_to_upgrade && detail.what_to_upgrade.length > 0 && (
                                <div>
                                    <div className="text-xs text-slate-500 dark:text-slate-400">升级条件:</div>
                                    <ul className="mt-1 space-y-0.5">
                                        {detail.what_to_upgrade.map((item: string, i: number) => (
                                            <li key={i} className="text-xs text-slate-600 dark:text-slate-400">• {item}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                            {data.missing_evidence_for_upgrade.length > 0 && (
                                <div>
                                    <div className="text-xs text-slate-500 dark:text-slate-400">升级缺失证据:</div>
                                    <div className="mt-1 flex flex-wrap gap-1">
                                        {data.missing_evidence_for_upgrade.map(e => (
                                            <span key={e} className="inline-block rounded bg-orange-50 px-1.5 py-0.5 text-[11px] text-orange-600 dark:bg-orange-900/20 dark:text-orange-400">{e}</span>
                                        ))}
                                    </div>
                                </div>
                            )}
                            {data.deep_ta_status && (
                                <div className="text-xs text-slate-500 dark:text-slate-400">
                                    TA 状态: <span className="text-slate-700 dark:text-slate-300">{data.deep_ta_status}</span>
                                    {data.deep_ta_dispatch_reason && <span className="ml-1 text-slate-400">({data.deep_ta_dispatch_reason})</span>}
                                </div>
                            )}
                        </div>
                    </div>

                    <div>
                        <SectionTitle icon={Shield} title="风险标签" />
                        <div className="mt-2 space-y-2">
                            {data.risk_flags.length > 0 ? (
                                <div className="flex flex-wrap gap-1">
                                    {data.risk_flags.map(flag => (
                                        <span key={flag} className="inline-flex items-center gap-1 rounded bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-600 dark:bg-red-900/20 dark:text-red-400">
                                            <AlertTriangle className="h-3 w-3" />
                                            {flag}
                                        </span>
                                    ))}
                                </div>
                            ) : (
                                <div className="text-xs text-slate-400">无风险标签</div>
                            )}
                            {data.policy_tags.length > 0 && (
                                <div>
                                    <div className="text-xs text-slate-500 dark:text-slate-400">政策标签:</div>
                                    <div className="mt-1 flex flex-wrap gap-1">
                                        {data.policy_tags.map(tag => (
                                            <span key={tag} className="inline-block rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/20 dark:text-blue-400">{tag}</span>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>

                    {data.reason && (
                        <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                            <div className="text-[11px] font-medium text-slate-500 dark:text-slate-400">入池理由</div>
                            <div className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{data.reason}</div>
                        </div>
                    )}

                    {data.tier_reason && (
                        <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                            <div className="text-[11px] font-medium text-slate-500 dark:text-slate-400">定级理由</div>
                            <div className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{data.tier_reason}</div>
                        </div>
                    )}

                    {detail && detail.fund_flow_individual_summary && (
                        <div className="rounded-lg border border-purple-100 bg-purple-50/30 p-3 dark:border-purple-800 dark:bg-purple-900/10">
                            <div className="text-[11px] font-medium text-purple-600 dark:text-purple-400">资金流个股摘要</div>
                            <div className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{detail.fund_flow_individual_summary}</div>
                        </div>
                    )}

                    {detail && detail.fund_flow_board_summary && (
                        <div className="rounded-lg border border-purple-100 bg-purple-50/30 p-3 dark:border-purple-800 dark:bg-purple-900/10">
                            <div className="text-[11px] font-medium text-purple-600 dark:text-purple-400">资金流板块摘要</div>
                            <div className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{detail.fund_flow_board_summary}</div>
                        </div>
                    )}

                    <div className="pb-4" />
                </div>
            </div>
        </>
    )
}
