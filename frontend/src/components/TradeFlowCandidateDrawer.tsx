// [H-005] mandate_radar_ui
// [TA-UI-001] analysis_console_horizon_intent
import { useEffect, useState } from 'react'
import { X, Loader2, CheckCircle2, XCircle, AlertTriangle, Shield, BarChart3, FileCheck, Lightbulb, ShieldCheck, StickyNote, FlaskConical } from 'lucide-react'
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

function candidateTypeColor(ct: string): string {  // [H-005] mandate_radar_ui
    switch (ct) {
        case 'POLICY_AMBUSH': return 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300'
        case 'POLICY_CONFIRM': return 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
        case 'TECH_TRADE': return 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300'
        case 'EVENT_WATCH': return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
        case 'PSEUDO_POLICY': return 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300'
        case 'OVERHEATED_AVOID': return 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
        case 'UNCLASSIFIED_DATA_GAP': return 'bg-gray-100 text-gray-700 dark:bg-gray-900/40 dark:text-gray-300'  // [TF-P0-002]
        default: return 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'
    }
}

function candidateTypeLabel(ct: string): string {  // [H-005] mandate_radar_ui
    switch (ct) {
        case 'POLICY_AMBUSH': return '昊天左侧'
        case 'POLICY_CONFIRM': return '政策确认'
        case 'TECH_TRADE': return '技术交易'
        case 'EVENT_WATCH': return '事件观察'
        case 'PSEUDO_POLICY': return '伪政策'
        case 'OVERHEATED_AVOID': return '过热规避'
        case 'UNCLASSIFIED_DATA_GAP': return '证据缺口'  // [TF-P0-002]
        default: return ct || '未分类'
    }
}

function companyRoleLabel(role: string): string {  // [H-005] mandate_radar_ui
    switch (role) {
        case 'LEADER': return '龙头'
        case 'CORE_SUPPLIER': return '核心供应商'
        case 'INFRA_PROVIDER': return '基础设施'
        case 'APPLICATION_SCENE': return '应用场景'
        case 'PERIPHERAL': return '外围'
        case 'CONCEPT_ONLY': return '纯概念'
        default: return role || '未知'
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
    onNavigateToAnalysis?: (url: string) => void
}

export default function TradeFlowCandidateDrawer({ candidate, tradeDate, open, onClose, onNavigateToAnalysis }: TradeFlowCandidateDrawerProps) {
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
                            <span className="text-sm text-slate-600 dark:text-slate-400">{candidate.name || '--'}</span>
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

                    {data.candidate_type && (  // [H-005] mandate_radar_ui
                        <div>
                            <SectionTitle icon={ShieldCheck} title="政策逻辑" />
                            <div className="mt-2 space-y-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-xs text-slate-500 dark:text-slate-400">候选类型:</span>
                                    <span className={`inline-block rounded px-2 py-0.5 text-[11px] font-medium ${candidateTypeColor(data.candidate_type)}`}>
                                        {candidateTypeLabel(data.candidate_type)}
                                    </span>
                                </div>
                                {data.mandate_topic && (
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs text-slate-500 dark:text-slate-400">政策主题:</span>
                                        <span className="text-xs font-medium text-blue-600 dark:text-blue-400">{data.mandate_topic}</span>
                                    </div>
                                )}
                                {data.company_role && (
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs text-slate-500 dark:text-slate-400">公司角色:</span>
                                        <span className="text-xs font-medium text-slate-700 dark:text-slate-300">{companyRoleLabel(data.company_role)}</span>
                                    </div>
                                )}
                                {data.beneficiary_path && data.beneficiary_path.length > 0 && (
                                    <div>
                                        <span className="text-xs text-slate-500 dark:text-slate-400">受益路径:</span>
                                        <div className="mt-1 flex flex-wrap gap-1">
                                            {data.beneficiary_path.map((p, i) => (
                                                <span key={i} className="inline-block rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] text-indigo-600 dark:bg-indigo-900/20 dark:text-indigo-400">{p}</span>
                                            ))}
                                        </div>
                                    </div>
                                )}
                                <div className="flex flex-wrap gap-3">
                                    {data.mandate_score > 0 && (
                                        <div className="flex items-center gap-1.5">
                                            <span className="text-xs text-slate-500 dark:text-slate-400">昊天分:</span>
                                            <span className="text-xs font-medium tabular-nums text-indigo-600 dark:text-indigo-400">{data.mandate_score.toFixed(1)}</span>
                                        </div>
                                    )}
                                    {data.ambush_score > 0 && (
                                        <div className="flex items-center gap-1.5">
                                            <span className="text-xs text-slate-500 dark:text-slate-400">埋伏分:</span>
                                            <span className="text-xs font-medium tabular-nums text-violet-600 dark:text-violet-400">{data.ambush_score.toFixed(1)}</span>
                                        </div>
                                    )}
                                </div>
                                {data.candidate_type_reason && (
                                    <div className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-3 dark:border-indigo-800 dark:bg-indigo-900/10">
                                        <div className="text-[11px] font-medium text-indigo-600 dark:text-indigo-400">分类理由</div>
                                        <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{data.candidate_type_reason}</div>
                                    </div>
                                )}
                                {detail && detail.mandate_evidence_refs && detail.mandate_evidence_refs.length > 0 && (
                                    <div>
                                        <span className="text-xs text-slate-500 dark:text-slate-400">政策证据引用 ({detail.mandate_evidence_refs.length})</span>
                                        <div className="mt-1 space-y-1">
                                            {detail.mandate_evidence_refs.slice(0, 5).map((ref, i) => (
                                                <div key={i} className="text-[11px] text-slate-500 dark:text-slate-400 truncate" title={JSON.stringify(ref)}>
                                                    {String(ref.title || ref.source || `证据 ${i + 1}`)}
                                                </div>
                                            ))}
                                            {detail.mandate_evidence_refs.length > 5 && (
                                                <div className="text-[11px] text-slate-400">+{detail.mandate_evidence_refs.length - 5} 条</div>
                                            )}
                                        </div>
                                    </div>
                                )}
                                {data.candidate_type === 'TECH_TRADE' && (
                                    <div className="rounded-lg border border-slate-200 bg-slate-50/50 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                        <div className="text-[11px] font-medium text-slate-500 dark:text-slate-400">为什么不是政策候选</div>
                                        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">该候选无足够政策连续性或公司受益路径证据，被分类为纯技术交易标的</div>
                                    </div>
                                )}
                                {data.candidate_type === 'UNCLASSIFIED_DATA_GAP' && (  // [TF-P0-002]
                                    <div className="rounded-lg border border-gray-200 bg-gray-50/50 p-3 dark:border-gray-700 dark:bg-gray-800/30">
                                        <div className="text-[11px] font-medium text-gray-500 dark:text-gray-400">为什么无法分类</div>
                                        <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                            该候选数据完整度过低，无法判断是否有政策/事件/受益路径证据。
                                            需要补充数据后再重新分类。
                                        </div>
                                        {data.missing_data_fields.length > 0 && (
                                            <div className="mt-2 flex flex-wrap gap-1">
                                                {data.missing_data_fields.slice(0, 5).map(f => (
                                                    <span key={f} className="inline-block rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600 dark:bg-gray-700 dark:text-gray-400">{f}</span>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}
                                {data.candidate_type === 'POLICY_AMBUSH' && (
                                    <div className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-3 dark:border-indigo-800 dark:bg-indigo-900/10">
                                        <div className="text-[11px] font-medium text-indigo-600 dark:text-indigo-400">缺什么验证</div>
                                        <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">
                                            {data.trigger_price != null ? '等待技术触发确认入场时机' : '等待技术面出现合理入场位置'}
                                            {data.need_deep_ta ? '；已建议深度TA验证' : '；建议等待更多证据'}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

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

                    {data.watchlist_note_suggested && (
                        <div>
                            <SectionTitle icon={StickyNote} title="自选备注" />
                            <div className="mt-2 space-y-2">
                                <div className="rounded-lg border border-teal-100 bg-teal-50/50 p-3 dark:border-teal-800 dark:bg-teal-900/10">
                                    <div className="text-xs font-medium text-teal-700 dark:text-teal-400">{data.watchlist_note_suggested}</div>
                                </div>
                                {data.watchlist_note && data.watchlist_note !== data.watchlist_note_suggested && (
                                    <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                        <div className="text-[11px] font-medium text-slate-500 dark:text-slate-400">用户备注</div>
                                        <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{data.watchlist_note}</div>
                                    </div>
                                )}
                                <div className="flex items-center gap-3 text-[11px] text-slate-500 dark:text-slate-400">
                                    {data.watchlist_topic && <span>主题: {data.watchlist_topic}</span>}
                                    {data.watchlist_benefit_score > 0 && <span>利好: {data.watchlist_benefit_score.toFixed(1)}</span>}
                                    {data.watchlist_consensus_score > 0 && <span>共识: {data.watchlist_consensus_score.toFixed(0)}</span>}
                                </div>
                                {data.watchlist_evidence_gap.length > 0 && (
                                    <div className="flex flex-wrap gap-1">
                                        {data.watchlist_evidence_gap.map(g => (
                                            <span key={g} className="inline-block rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-600 dark:bg-amber-900/20 dark:text-amber-400">{g}</span>
                                        ))}
                                    </div>
                                )}
                            </div>
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

                    {/* [TA-UI-001] analysis_console_horizon_intent - Jump to analysis */}
                    {onNavigateToAnalysis && (
                        <div className="sticky bottom-0 bg-white dark:bg-slate-900 pt-3 pb-2 border-t border-slate-100 dark:border-slate-700">
                            <button
                                onClick={() => {
                                    const ct = data.candidate_type
                                    const horizon = ct === 'POLICY_AMBUSH' || ct === 'POLICY_CONFIRM' ? 'medium' : 'short'
                                    const intent = ct === 'POLICY_AMBUSH' || ct === 'POLICY_CONFIRM' ? 'entry' : 'watch'
                                    const pos = 'false'
                                    const url = `/analysis?symbol=${encodeURIComponent(candidate.symbol)}&horizon=${horizon}&intent=${intent}&position=${pos}`
                                    onNavigateToAnalysis(url)
                                }}
                                className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-500 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-600 transition-colors"
                            >
                                <FlaskConical className="h-4 w-4" />
                                智能分析
                            </button>
                            <div className="mt-1 text-center text-[10px] text-slate-400">
                                {(data.candidate_type === 'POLICY_AMBUSH' || data.candidate_type === 'POLICY_CONFIRM')
                                    ? '将默认以中线｜入场研究打开'
                                    : '将默认以短线｜观察打开'
                                }
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </>
    )
}
