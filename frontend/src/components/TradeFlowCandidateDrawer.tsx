// [H-005] mandate_radar_ui
// [TA-UI-001] analysis_console_horizon_intent
// [TF-UI-011] candidate_research_entry
// [TRACK-006] add_to_observation
// [KB-011] knowledge_contract_ui
import { useEffect, useState } from 'react'
import { X, Loader2, CheckCircle2, XCircle, AlertTriangle, Shield, BarChart3, FileCheck, Lightbulb, ShieldCheck, StickyNote, FlaskConical, FileText, Building2, CandlestickChart, Clock, Cpu, Wallet, Eye, BookOpen } from 'lucide-react'
import { api } from '@/services/api'
import type { TradeFlowCandidateItem, TradeFlowCandidateDetail, TradeFlowResearchPlanResponse, CompanyOverviewResponse } from '@/types'
import MiniKline from './MiniKline'
import { classifyResearchAttention, classifyLocalKnowledge, isKnowledgeStrongPositive } from '@/utils/knowledgeContract'

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
    const [researchPlan, setResearchPlan] = useState<TradeFlowResearchPlanResponse | null>(null)  // [UI-009]
    const [loadingPlan, setLoadingPlan] = useState(false)  // [UI-009]
    const [companyOverview, setCompanyOverview] = useState<CompanyOverviewResponse | null>(null)  // [TF-UI-011]
    const [loadingOverview, setLoadingOverview] = useState(false)  // [TF-UI-011]
    const [showKline, setShowKline] = useState(false)  // [TF-UI-011]
    const [paperMsg, setPaperMsg] = useState<string | null>(null)  // [TF-PAPER-001]
    const [paperLoading, setPaperLoading] = useState(false)  // [TF-PAPER-001]
    // [TRACK-006] add_to_observation — drawer-level state for one-click add
    const [observeMsg, setObserveMsg] = useState<string | null>(null)
    const [observeLoading, setObserveLoading] = useState(false)

    useEffect(() => {
        if (!open) return
        let cancelled = false
        setLoadingDetail(true)
        setDetail(null)
        setResearchPlan(null)
        setCompanyOverview(null)  // [TF-UI-011]
        setShowKline(false)  // [TF-UI-011]
        setObserveMsg(null)  // [TRACK-006] reset on drawer reopen
        setObserveLoading(false)  // [TRACK-006]
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
        // [TF-UI-011] candidate_research_entry — lazy load company overview
        setLoadingOverview(true)
        api.getCompanyOverview(candidate.symbol, tradeDate)
            .then(res => { if (!cancelled) setCompanyOverview(res) })
            .catch(() => {})
            .finally(() => { if (!cancelled) setLoadingOverview(false) })
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
    const localKnowledgeDetail = (data.local_knowledge_detail || {}) as Record<string, unknown>
    const localKnowledgeStatus = String(localKnowledgeDetail.status || '')
    const localKnowledgeConfidence = String(localKnowledgeDetail.confidence || '')
    const localKnowledgeUpdatedAt = String(localKnowledgeDetail.updated_at || '')
    const localKnowledgeMatchedPages = Array.isArray(localKnowledgeDetail.matched_pages_brief)
        ? localKnowledgeDetail.matched_pages_brief as Array<Record<string, unknown>>
        : []
    const localKnowledgeRisks = Array.isArray(localKnowledgeDetail.risks)
        ? localKnowledgeDetail.risks.map(String).filter(Boolean)
        : []
    const localKnowledgeErrors = Array.isArray(localKnowledgeDetail.errors)
        ? localKnowledgeDetail.errors.map(String).filter(Boolean)
        : []
    const showLocalKnowledge = Boolean(
        (data.local_knowledge_score || 0) > 0 ||
        (data.knowledge_hit_count || 0) > 0 ||
        data.local_knowledge_summary ||
        data.needs_tree_work_research ||
        localKnowledgeStatus
    )
    // [HY-006] tradeflow_half_year_factor — half-year report fact factor derived
    // values for the drawer block. Mirrors the KB-004 local knowledge pattern:
    // show the block only when there is a real signal (score != 0, summary,
    // risk flags, needs_research_review, or a fact period). Empty payload stays
    // hidden so NORMAL_NO_DATA candidates don't render an empty half-year panel.
    const halfYearDetail = (data.half_year_fact_detail || {}) as Record<string, unknown>
    const halfYearStatus = String(halfYearDetail.status || '')
    const halfYearThesisStatus = String(halfYearDetail.thesis_check_status || '')
    const halfYearPeriod = String(halfYearDetail.fact_period || '')
    const halfYearDataStatus = String(halfYearDetail.fact_data_status || '')
    const halfYearRiskFlags = Array.isArray(data.half_year_risk_flags)
        ? data.half_year_risk_flags.map(String).filter(Boolean)
        : []
    const halfYearDowngradeReasons = Array.isArray(halfYearDetail.downgrade_reasons)
        ? (halfYearDetail.downgrade_reasons as unknown[]).map(String).filter(Boolean)
        : []
    const halfYearResearchHint = String(halfYearDetail.research_priority_hint || '')
    const halfYearErrors = Array.isArray(halfYearDetail.errors)
        ? (halfYearDetail.errors as unknown[]).map(String).filter(Boolean)
        : []
    const showHalfYear = Boolean(
        (data.half_year_fact_score || 0) !== 0 ||
        data.half_year_fact_summary ||
        halfYearRiskFlags.length > 0 ||
        data.needs_research_review ||
        halfYearStatus === 'HAS_FACTS' ||
        halfYearPeriod
    )
    // [KB-011] knowledge_contract_ui — research attention breakdown + strong
    // positive gate. When the payload is empty, the helper returns
    // hasAnySignal=false so we render the NORMAL_NO_DATA empty state instead
    // of pretending there is positive coverage.
    const researchDetail = (data.research_attention_detail || {}) as Record<string, unknown>
    const researchBreakdown = classifyResearchAttention(researchDetail)
    const localBreakdown = classifyLocalKnowledge(localKnowledgeDetail)
    const knowledgeStrongPositive = isKnowledgeStrongPositive(researchDetail, localKnowledgeDetail)
    const showResearchAttention = Boolean(
        researchBreakdown.hasAnySignal ||
        (data.research_attention_score || 0) > 0 ||
        (data.research_attention_effective_score || 0) > 0 ||
        (data.knowledge_theme_count || 0) > 0 ||
        data.research_attention_summary
    )

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

                    {/* [TF-UI-011] candidate_research_entry — Company Overview */}
                    <div>
                        <SectionTitle icon={Building2} title="公司概览" />
                        <div className="mt-2 space-y-2">
                            {loadingOverview && (
                                <div className="flex items-center gap-2 text-xs text-slate-400">
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    加载公司信息...
                                </div>
                            )}
                            {!loadingOverview && companyOverview && companyOverview.profile_available && (
                                <>
                                    {companyOverview.industry && (
                                        <div className="flex items-center gap-2">
                                            <span className="text-xs text-slate-500 dark:text-slate-400">所属行业:</span>
                                            <span className="inline-block rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
                                                {companyOverview.industry}
                                            </span>
                                        </div>
                                    )}
                                    <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                        <div className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
                                            公司简介 {companyOverview.data_source && <span className="text-slate-400">({companyOverview.data_source})</span>}
                                        </div>
                                        <div className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400 max-h-32 overflow-y-auto whitespace-pre-wrap">
                                            {companyOverview.company_profile}
                                        </div>
                                    </div>
                                </>
                            )}
                            {!loadingOverview && companyOverview && !companyOverview.profile_available && (
                                <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-3 dark:border-slate-700 dark:bg-slate-800/30">
                                    <div className="text-xs text-slate-400">
                                        {companyOverview.error || '公司概览暂不可用，可能是数据源未返回'}
                                    </div>
                                </div>
                            )}
                            {!loadingOverview && !companyOverview && (
                                <div className="text-xs text-slate-400">公司信息获取失败</div>
                            )}
                        </div>
                    </div>

                    {/* [TF-UI-011] candidate_research_entry — K-line entry */}
                    <div>
                        <div className="flex items-center justify-between">
                            <SectionTitle icon={CandlestickChart} title="K 线走势" />
                            <button
                                onClick={() => setShowKline(!showKline)}
                                className="flex items-center gap-1 rounded-md bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700 transition-colors"
                            >
                                <CandlestickChart className="h-3.5 w-3.5" />
                                {showKline ? '收起 K 线' : '查看 K 线'}
                            </button>
                        </div>
                        {showKline && (
                            <div className="mt-2 rounded-lg border border-slate-100 bg-white p-2 dark:border-slate-700 dark:bg-slate-800/30">
                                <MiniKline symbol={candidate.symbol} height={200} />
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

                    {showLocalKnowledge && (  // [KB-004] tradeflow_knowledge_score
                        <div>
                            <SectionTitle icon={FileText} title="本地知识" />
                            <div className="mt-2 space-y-2">
                                <div className="flex flex-wrap gap-2">
                                    <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                                        命中 {data.knowledge_hit_count || 0} 条
                                    </span>
                                    <span className="inline-flex items-center rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-300">
                                        知识分 {(data.local_knowledge_score || 0).toFixed(2)}
                                    </span>
                                    {localKnowledgeStatus && (
                                        <span className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium ${
                                            localKnowledgeStatus === 'FAILED'
                                                ? 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300'
                                                : localKnowledgeStatus === 'HAS_DATA' && !localBreakdown.isWeakOrPenalized
                                                ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300'
                                                : 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300'
                                        }`}>
                                            {localKnowledgeStatus}
                                        </span>
                                    )}
                                    {localBreakdown.isWeakOrPenalized && (
                                        <span className="inline-flex items-center rounded bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                                            仅过期/低置信，非强正面
                                        </span>
                                    )}
                                    {data.needs_tree_work_research && (
                                        <span className="inline-flex items-center rounded bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-600 dark:bg-violet-900/30 dark:text-violet-300">
                                            需 Tree Work 补研报
                                        </span>
                                    )}
                                </div>
                                {(localKnowledgeUpdatedAt || localKnowledgeConfidence) && (
                                    <div className="flex flex-wrap gap-3 text-[11px] text-slate-500 dark:text-slate-400">
                                        {localKnowledgeUpdatedAt && <span>更新时间: {localKnowledgeUpdatedAt}</span>}
                                        {localKnowledgeConfidence && <span>置信度: {localKnowledgeConfidence}</span>}
                                    </div>
                                )}
                                {data.local_knowledge_summary && (
                                    <div className="rounded-lg border border-slate-100 bg-slate-50/70 p-3 text-xs leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-800/30 dark:text-slate-300">
                                        {data.local_knowledge_summary}
                                    </div>
                                )}
                                {localKnowledgeMatchedPages.length > 0 && (
                                    <div className="space-y-1.5">
                                        <div className="text-[11px] font-medium text-slate-500 dark:text-slate-400">命中页面</div>
                                        {localKnowledgeMatchedPages.slice(0, 5).map((page, i) => {
                                            const title = String(page.title || page.rel_path || `页面 ${i + 1}`)
                                            const relPath = String(page.rel_path || '')
                                            const summary = String(page.summary_snippet || page.summary || '')
                                            return (
                                                <div key={`${title}-${i}`} className="rounded-lg border border-slate-100 bg-white p-2 dark:border-slate-700 dark:bg-slate-800/30">
                                                    <div className="truncate text-xs font-medium text-slate-700 dark:text-slate-200" title={title}>{title}</div>
                                                    {relPath && <div className="mt-0.5 truncate text-[11px] text-slate-400" title={relPath}>{relPath}</div>}
                                                    {summary && <div className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{summary}</div>}
                                                </div>
                                            )
                                        })}
                                    </div>
                                )}
                                {localKnowledgeRisks.length > 0 && (
                                    <div className="rounded-lg border border-orange-100 bg-orange-50/50 p-3 dark:border-orange-800 dark:bg-orange-900/10">
                                        <div className="text-[11px] font-medium text-orange-600 dark:text-orange-400">知识库风险提示</div>
                                        <div className="mt-1 flex flex-wrap gap-1">
                                            {localKnowledgeRisks.slice(0, 5).map((risk, i) => (
                                                <span key={`${risk}-${i}`} className="inline-block max-w-full truncate rounded bg-orange-100 px-1.5 py-0.5 text-[11px] text-orange-700 dark:bg-orange-900/30 dark:text-orange-300" title={risk}>
                                                    {risk}
                                                </span>
                                            ))}
                                        </div>
                                    </div>
                                )}
                                {localKnowledgeErrors.length > 0 && (
                                    <div className="rounded-lg border border-red-100 bg-red-50/50 p-3 dark:border-red-800 dark:bg-red-900/10">
                                        <div className="text-[11px] font-medium text-red-600 dark:text-red-400">知识库查询异常</div>
                                        <div className="mt-1 space-y-1">
                                            {localKnowledgeErrors.slice(0, 3).map((err, i) => (
                                                <div key={`${err}-${i}`} className="break-words text-[11px] text-red-500 dark:text-red-300">{err}</div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {showHalfYear && (  // [HY-006] tradeflow_half_year_factor
                        <div>
                            <SectionTitle icon={FileText} title="半年报事实" />
                            <div className="mt-2 space-y-2">
                                <div className="flex flex-wrap gap-2">
                                    <span className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium ${
                                        (data.half_year_fact_score || 0) < 0
                                            ? 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300'
                                            : (data.half_year_fact_score || 0) > 0
                                            ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300'
                                            : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
                                    }`}>
                                        因子分 {(data.half_year_fact_score || 0).toFixed(2)}
                                    </span>
                                    {halfYearPeriod && (
                                        <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                                            报告期 {halfYearPeriod}
                                        </span>
                                    )}
                                    {halfYearThesisStatus && (
                                        <span className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium ${
                                            halfYearThesisStatus === 'contradicted'
                                                ? 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                                                : halfYearThesisStatus === 'weakened'
                                                ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                                                : halfYearThesisStatus === 'supported'
                                                ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300'
                                                : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
                                        }`}>
                                            {halfYearThesisStatus === 'contradicted' ? '事实打脸'
                                                : halfYearThesisStatus === 'weakened' ? '事实削弱'
                                                : halfYearThesisStatus === 'supported' ? '事实支持'
                                                : halfYearThesisStatus === 'insufficient_data' ? '数据不足'
                                                : halfYearThesisStatus}
                                        </span>
                                    )}
                                    {data.needs_research_review && (
                                        <span className="inline-flex items-center rounded bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-600 dark:bg-violet-900/30 dark:text-violet-300">
                                            需 Tree Work 复核
                                        </span>
                                    )}
                                </div>
                                {halfYearDataStatus && (
                                    <div className="text-[11px] text-slate-500 dark:text-slate-400">数据状态: {halfYearDataStatus}</div>
                                )}
                                {data.half_year_fact_summary && (
                                    <div className="rounded-lg border border-slate-100 bg-slate-50/70 p-3 text-xs leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-800/30 dark:text-slate-300">
                                        {data.half_year_fact_summary}
                                    </div>
                                )}
                                {halfYearResearchHint && (
                                    <div className="text-[11px] text-slate-500 dark:text-slate-400">{halfYearResearchHint}</div>
                                )}
                                {halfYearDowngradeReasons.length > 0 && (
                                    <div className="rounded-lg border border-amber-100 bg-amber-50/50 p-3 dark:border-amber-800 dark:bg-amber-900/10">
                                        <div className="text-[11px] font-medium text-amber-600 dark:text-amber-400">降权原因</div>
                                        <div className="mt-1 space-y-1">
                                            {halfYearDowngradeReasons.map((reason, i) => (
                                                <div key={`${reason}-${i}`} className="text-[11px] text-amber-700 dark:text-amber-300">{reason}</div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                                {halfYearRiskFlags.length > 0 && (
                                    <div className="rounded-lg border border-orange-100 bg-orange-50/50 p-3 dark:border-orange-800 dark:bg-orange-900/10">
                                        <div className="text-[11px] font-medium text-orange-600 dark:text-orange-400">风险标记</div>
                                        <div className="mt-1 flex flex-wrap gap-1">
                                            {halfYearRiskFlags.slice(0, 6).map((flag, i) => (
                                                <span key={`${flag}-${i}`} className="inline-block max-w-full truncate rounded bg-orange-100 px-1.5 py-0.5 text-[11px] text-orange-700 dark:bg-orange-900/30 dark:text-orange-300" title={flag}>
                                                    {flag}
                                                </span>
                                            ))}
                                        </div>
                                    </div>
                                )}
                                {halfYearErrors.length > 0 && (
                                    <div className="rounded-lg border border-red-100 bg-red-50/50 p-3 dark:border-red-800 dark:bg-red-900/10">
                                        <div className="text-[11px] font-medium text-red-600 dark:text-red-400">查询异常</div>
                                        <div className="mt-1 space-y-1">
                                            {halfYearErrors.slice(0, 3).map((err, i) => (
                                                <div key={`${err}-${i}`} className="break-words text-[11px] text-red-500 dark:text-red-300">{err}</div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {showResearchAttention && (  // [KB-011] knowledge_contract_ui
                        <div>
                            <SectionTitle icon={BookOpen} title="研报关注度" />
                            <div className="mt-2 space-y-2">
                                <div className="flex flex-wrap gap-2">
                                    <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                                        基础分 {(researchBreakdown.baseScore || data.research_attention_score || 0).toFixed(2)}
                                    </span>
                                    {researchBreakdown.effectiveScore > 0 && (
                                        <span className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium ${
                                            researchBreakdown.isWeakOrPenalized
                                                ? 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300'
                                                : 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300'
                                        }`}>
                                            有效分 {researchBreakdown.effectiveScore.toFixed(2)}
                                        </span>
                                    )}
                                    {(data.knowledge_theme_count || 0) > 0 && (
                                        <span className="inline-flex items-center rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-300">
                                            主题交叉 {data.knowledge_theme_count}
                                        </span>
                                    )}
                                    {researchBreakdown.isWeakOrPenalized && (
                                        <span className="inline-flex items-center rounded bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                                            仅过期/低置信，非强正面
                                        </span>
                                    )}
                                    {knowledgeStrongPositive && (
                                        <span className="inline-flex items-center rounded bg-emerald-100 px-2 py-0.5 text-[11px] font-bold text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                                            强正面
                                        </span>
                                    )}
                                </div>
                                {(researchBreakdown.credits.length > 0 || researchBreakdown.penalties.length > 0) && (
                                    <div className="grid gap-2 sm:grid-cols-2">
                                        {researchBreakdown.credits.length > 0 && (
                                            <div className="rounded-lg border border-emerald-100 bg-emerald-50/40 p-2 dark:border-emerald-800 dark:bg-emerald-900/10">
                                                <div className="text-[11px] font-medium text-emerald-600 dark:text-emerald-400">加分项</div>
                                                <ul className="mt-1 space-y-0.5">
                                                    {researchBreakdown.credits.map((c, i) => (
                                                        <li key={`credit-${i}`} className="text-[11px] leading-relaxed text-emerald-700 dark:text-emerald-300">+ {c}</li>
                                                    ))}
                                                </ul>
                                            </div>
                                        )}
                                        {researchBreakdown.penalties.length > 0 && (
                                            <div className="rounded-lg border border-orange-100 bg-orange-50/40 p-2 dark:border-orange-800 dark:bg-orange-900/10">
                                                <div className="text-[11px] font-medium text-orange-600 dark:text-orange-400">降权项</div>
                                                <ul className="mt-1 space-y-0.5">
                                                    {researchBreakdown.penalties.map((p, i) => (
                                                        <li key={`penalty-${i}`} className="text-[11px] leading-relaxed text-orange-700 dark:text-orange-300">- {p}</li>
                                                    ))}
                                                </ul>
                                            </div>
                                        )}
                                    </div>
                                )}
                                {data.research_attention_summary && (
                                    <div className="rounded-lg border border-slate-100 bg-slate-50/70 p-3 text-xs leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-800/30 dark:text-slate-300">
                                        {data.research_attention_summary}
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

                    {/* [UI-009] candidate_ta_plan_draft — Research plan section */}
                    <div>
                        <div className="flex items-center justify-between">
                            <SectionTitle icon={FileText} title="研究预案" />
                            {!researchPlan && !loadingPlan && (
                                <button
                                    onClick={() => {
                                        setLoadingPlan(true)
                                        api.generateResearchPlan(candidate.symbol, tradeDate)
                                            .then(res => { setResearchPlan(res) })
                                            .catch(() => {})
                                            .finally(() => { setLoadingPlan(false) })
                                    }}
                                    className="flex items-center gap-1 rounded-md bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-600 hover:bg-indigo-100 dark:bg-indigo-900/30 dark:text-indigo-400 dark:hover:bg-indigo-900/50 transition-colors"
                                >
                                    <FileText className="h-3.5 w-3.5" />
                                    {data.candidate_type === 'TECH_TRADE' ? '生成短线确认预案' : '生成中线研究预案'}
                                </button>
                            )}
                        </div>
                        <div className="mt-2 space-y-2">
                            {loadingPlan && (
                                <div className="flex items-center gap-2 text-xs text-slate-400">
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    生成研究预案...
                                </div>
                            )}
                            {researchPlan && researchPlan.status !== 'no_data' && (
                                <>
                                    {researchPlan.can_generate ? (
                                        <div className="rounded-lg border border-indigo-200 bg-indigo-50/50 p-3 dark:border-indigo-800 dark:bg-indigo-900/10">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span className="text-[11px] font-medium text-indigo-600 dark:text-indigo-400">
                                                    {researchPlan.profile_label || (researchPlan.horizon === 'medium' ? '中线研究预案' : '短线确认预案')}
                                                </span>
                                                <span className="text-[10px] text-slate-400">|</span>
                                                <span className="text-[11px] text-slate-500 dark:text-slate-400">
                                                    队列: {researchPlan.research_queue || '未分配'}
                                                </span>
                                                <span className="text-[10px] text-slate-400">|</span>
                                                <span className="text-[11px] text-slate-500 dark:text-slate-400">
                                                    Profile: {researchPlan.runtime_profile}
                                                </span>
                                            </div>
                                            {/* [TF-UI-011] candidate_research_entry — runtime metadata */}
                                            <div className="mt-1 flex items-center gap-3 flex-wrap text-[10px]">
                                                {researchPlan.expected_latency && (
                                                    <span className="flex items-center gap-1 text-slate-500 dark:text-slate-400">
                                                        <Clock className="h-3 w-3" />
                                                        预计耗时 {researchPlan.expected_latency}
                                                    </span>
                                                )}
                                                <span className="flex items-center gap-1 text-slate-500 dark:text-slate-400">
                                                    <Cpu className="h-3 w-3" />
                                                    {researchPlan.llm_allowed ? '调用模型' : '不调用模型'}
                                                </span>
                                                {researchPlan.cost_risk && researchPlan.cost_risk !== 'none' && (
                                                    <span className={`flex items-center gap-1 ${
                                                        researchPlan.cost_risk === 'high' ? 'text-red-500' :
                                                        researchPlan.cost_risk === 'medium' ? 'text-amber-500' :
                                                        'text-emerald-500'
                                                    }`}>
                                                        成本风险: {researchPlan.cost_risk}
                                                    </span>
                                                )}
                                            </div>
                                            {researchPlan.requires_confirmation && (
                                                <div className="mt-1 rounded bg-amber-50 dark:bg-amber-900/20 px-2 py-1 text-[10px] text-amber-600 dark:text-amber-400">
                                                    ⚠ 需要 Explorer 确认后才能启动
                                                </div>
                                            )}
                                            <div className="mt-1 flex flex-wrap gap-1">
                                                {researchPlan.enabled_modules.map(m => (
                                                    <span key={m} className="inline-block rounded bg-blue-50 px-1.5 py-0.5 text-[10px] text-blue-600 dark:bg-blue-900/20 dark:text-blue-400">{m}</span>
                                                ))}
                                            </div>
                                            {researchPlan.route_reason && (
                                                <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{researchPlan.route_reason}</div>
                                            )}
                                            <div className="mt-2 rounded bg-white/60 dark:bg-slate-800/40 p-2 text-[11px] text-slate-600 dark:text-slate-400 whitespace-pre-wrap max-h-40 overflow-y-auto">
                                                {researchPlan.plan_markdown}
                                            </div>
                                            <div className="mt-1 text-[10px] text-slate-400">
                                                预案已生成，需人工确认后才会启动 TA 分析
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="rounded-lg border border-amber-200 bg-amber-50/50 p-3 dark:border-amber-800 dark:bg-amber-900/10">
                                            <div className="text-[11px] font-medium text-amber-600 dark:text-amber-400">无法生成研究预案</div>
                                            <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{researchPlan.block_reason}</div>
                                        </div>
                                    )}
                                </>
                            )}
                            {researchPlan && researchPlan.status === 'no_data' && (
                                <div className="text-xs text-slate-400">未找到候选记录</div>
                            )}
                        </div>
                    </div>

                    <div className="pb-4" />

                    {/* [TF-PAPER-001] paper_trading_ledger — Add to paper tracking */}
                    <div className="sticky bottom-0 bg-white dark:bg-slate-900 pt-3 pb-2 border-t border-slate-100 dark:border-slate-700 space-y-2">
                        {/* [TRACK-006] add_to_observation — one-click add to observation warehouse */}
                        {observeMsg && (
                            <div className={`text-xs text-center px-3 py-1.5 rounded ${observeMsg.includes('已加入') || observeMsg.includes('已更新') ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400' : 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400'}`}>
                                {observeMsg}
                            </div>
                        )}
                        <button
                            onClick={() => {
                                setObserveLoading(true)
                                setObserveMsg(null)
                                api.addCandidateToObservervation(candidate.symbol, {
                                    trade_date: tradeDate,
                                    via: 'candidate_drawer',
                                })
                                    .then(res => {
                                        if (res.status === 'ok') {
                                            const verb = res.action === 'updated' ? '已更新' : '已加入'
                                            setObserveMsg(`${verb}观察仓 · ${res.symbol || candidate.symbol}`)
                                        } else {
                                            setObserveMsg(res.message || '加入失败，请稍后重试')
                                        }
                                    })
                                    .catch(() => setObserveMsg('加入失败，请稍后重试'))
                                    .finally(() => setObserveLoading(false))
                            }}
                            disabled={observeLoading}
                            className="w-full flex items-center justify-center gap-2 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-900/20 px-4 py-2 text-sm font-medium text-indigo-600 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/40 transition-colors disabled:opacity-50"
                        >
                            <Eye className="h-4 w-4" />
                            {observeLoading ? '加入中...' : '加入观察仓'}
                        </button>

                        {paperMsg && (
                            <div className={`text-xs text-center px-3 py-1.5 rounded ${paperMsg.includes('已加入') ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400' : 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400'}`}>
                                {paperMsg}
                            </div>
                        )}
                        {/* [TF-PAPER-001] paper_trading_ledger — Add to paper tracking button */}
                        <button
                            onClick={() => {
                                setPaperLoading(true)
                                setPaperMsg(null)
                                api.addPaperCandidate({
                                    symbol: candidate.symbol,
                                    name: candidate.name,
                                    trade_date: tradeDate,
                                    trigger_price: candidate.trigger_price,
                                    invalid_price: candidate.invalid_price,
                                    planned_amount: 1000,
                                    candidate_type: candidate.candidate_type,
                                    data_quality_score: candidate.data_quality_score,  // [TF-RISK-001]
                                })
                                    .then(res => setPaperMsg(res.message))
                                    .catch(() => setPaperMsg('加入失败，请稍后重试'))
                                    .finally(() => setPaperLoading(false))
                            }}
                            disabled={paperLoading}
                            className="w-full flex items-center justify-center gap-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-slate-50 dark:bg-slate-800 px-4 py-2 text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors disabled:opacity-50"
                        >
                            <Wallet className="h-4 w-4" />
                            {paperLoading ? '添加中...' : '加入模拟跟踪'}
                        </button>

                        {/* [TA-UI-001] analysis_console_horizon_intent - Jump to analysis */}
                        {onNavigateToAnalysis && (
                            <div>
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
            </div>
        </>
    )
}
