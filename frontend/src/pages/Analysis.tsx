import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import AgentCollaboration from '@/components/AgentCollaboration'
import DebateDrawer from '@/components/DebateDrawer'
import ReportViewer from '@/components/ReportViewer'
import ChatCopilotPanel from '@/components/ChatCopilotPanel'
import KlinePanel from '@/components/KlinePanel'
import DecisionCard from '@/components/DecisionCard'
import RiskRadar from '@/components/RiskRadar'
import KeyMetrics from '@/components/KeyMetrics'
import { useAnalysisStore } from '@/stores/analysisStore'
import { api } from '@/services/api'
import type { FullTACostPreview } from '@/types'
import { AlertTriangle, X, Eye, Loader2 } from 'lucide-react'

// [TA-UI-001] analysis_console_horizon_intent
const HORIZON_OPTIONS = [
    { value: 'short' as const, label: '短线' },
    { value: 'medium' as const, label: '中线' },
]

const INTENT_OPTIONS = [
    { value: 'watch', label: '观察' },
    { value: 'entry', label: '入场研究' },
    { value: 'holding', label: '持仓复盘' },
    { value: 'add', label: '加仓判断' },
    { value: 'reduce', label: '减仓止损' },
]

const POSITION_OPTIONS = [
    { value: false, label: '未持仓' },
    { value: true, label: '已持仓' },
]

function mapDecision(decision?: string): 'buy' | 'sell' | 'hold' | 'add' | 'reduce' | 'watch' | undefined {
    if (!decision) return undefined
    const d = decision.toUpperCase()
    if (d.includes('SELL') || d.includes('卖出')) return 'sell'
    if (d.includes('REDUCE') || d.includes('减持')) return 'reduce'
    if (d.includes('WAIT') || d.includes('WATCH') || d.includes('观望') || d.includes('观察')) return 'watch'
    if (d.includes('HOLD') || d.includes('持有')) return 'hold'
    if (d.includes('ADD') || d.includes('增持')) return 'add'
    if (d.includes('BUY') || d.includes('买入')) return 'buy'
    return undefined
}

function extractConfidence(text?: string): number | undefined {
    if (!text) return undefined
    const m = text.match(/置信度[:：]\s*(\d+)%/i) ?? text.match(/confidence[:：]\s*(\d+)%/i)
    if (m) {
        const v = parseInt(m[1])
        return v >= 0 && v <= 100 ? v : undefined
    }
    return undefined
}

function extractPrice(text: string | undefined, type: 'target' | 'stop'): number | undefined {
    if (!text) return undefined
    const patterns = type === 'target'
        ? [/目标价[:：]\s*[¥$]?\s*([\d.]+)/, /目标价格[:：]\s*[¥$]?\s*([\d.]+)/, /target[:：]\s*[¥$]?\s*([\d.]+)/i]
        : [/止损价[:：]\s*[¥$]?\s*([\d.]+)/, /止损价格[:：]\s*[¥$]?\s*([\d.]+)/, /stop[-\s_]?loss[:：]\s*[¥$]?\s*([\d.]+)/i]
    for (const p of patterns) {
        const m = text.match(p)
        if (m) return parseFloat(m[1])
    }
    return undefined
}

export default function Analysis() {
    const [searchParams] = useSearchParams()
    const querySymbol = (searchParams.get('symbol') || '').trim().toUpperCase()
    // [TA-UI-001] analysis_console_horizon_intent - URL params from TradeFlow
    const queryHorizon = searchParams.get('horizon')
    const queryIntent = searchParams.get('intent')
    const queryPosition = searchParams.get('position')
    const [activeSymbol, setActiveSymbol] = useState(() => querySymbol || useAnalysisStore.getState().currentSymbol || '000001.SH')
    const [activeSection, setActiveSection] = useState<string | undefined>()
    const [debateDrawer, setDebateDrawer] = useState<'research' | 'risk' | null>(null)
    const [latestPriceFallback, setLatestPriceFallback] = useState<{ target?: number | null; stop?: number | null; symbol?: string } | null>(null)
    const reportRef = useRef<HTMLDivElement | null>(null)
    const [fullTaMode, setFullTaMode] = useState(false)  // [PERF-004]
    const [showCostPreview, setShowCostPreview] = useState(false)  // [PERF-004]
    const [costPreview, setCostPreview] = useState<FullTACostPreview | null>(null)  // [PERF-004]
    const [costPreviewLoading, setCostPreviewLoading] = useState(false)  // [PERF-004]
    // [TRACK-006] add_to_observation — TA report -> observation warehouse
    const [observeMsg, setObserveMsg] = useState<string | null>(null)
    const [observeLoading, setObserveLoading] = useState(false)
    const {
        report,
        currentSymbol,
        setCurrentSymbol,
        jobConfidence,
        jobTargetPrice,
        jobStopLoss,
        riskItems,
        keyMetrics,
        analysisRunState,
        // [TA-UI-001] analysis_console_horizon_intent
        analysisHorizon,
        analysisIntent,
        hasPosition,
        setAnalysisHorizon,
        setAnalysisIntent,
        setHasPosition,
    } = useAnalysisStore()

    const handleShowReport = (section?: string) => {
        setActiveSection(section)
        reportRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }

    const initialChatInput = querySymbol ? `分析 ${querySymbol} 今日走势` : undefined

    useEffect(() => {
        if (querySymbol) setActiveSymbol(querySymbol)
    }, [querySymbol])

    // [TA-UI-001] analysis_console_horizon_intent - apply URL params once on mount
    useEffect(() => {
        if (queryHorizon === 'short' || queryHorizon === 'medium') setAnalysisHorizon(queryHorizon)
        if (queryIntent && INTENT_OPTIONS.some(o => o.value === queryIntent)) setAnalysisIntent(queryIntent)
        if (queryPosition === 'true') setHasPosition(true)
        else if (queryPosition === 'false') setHasPosition(false)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    useEffect(() => {
        if (currentSymbol) {
            setActiveSymbol(currentSymbol)
        }
    }, [currentSymbol])

    const finalDecision = report?.final_trade_decision
    const confidence = jobConfidence ?? extractConfidence(finalDecision)
    const reportMatchesActiveSymbol = !report?.symbol || report.symbol === activeSymbol
    const latestFallbackMatches = latestPriceFallback?.symbol === activeSymbol
    const targetPrice = jobTargetPrice
        ?? (reportMatchesActiveSymbol ? report?.target_price : undefined)
        ?? (latestFallbackMatches ? latestPriceFallback?.target : undefined)
        ?? extractPrice(finalDecision, 'target')
    const stopLoss = jobStopLoss
        ?? (reportMatchesActiveSymbol ? report?.stop_loss_price : undefined)
        ?? (latestFallbackMatches ? latestPriceFallback?.stop : undefined)
        ?? extractPrice(finalDecision, 'stop')

    useEffect(() => {
        let cancelled = false
        const symbol = activeSymbol
        if (!symbol || targetPrice != null || stopLoss != null || analysisRunState === 'running') return
        api.getReports(symbol, 0, 1)
            .then(response => {
                if (cancelled) return
                const latest = response.reports?.[0]
                if (!latest || latest.symbol !== symbol) return
                setLatestPriceFallback({
                    symbol,
                    target: latest.target_price ?? null,
                    stop: latest.stop_loss_price ?? null,
                })
            })
            .catch(() => {
                if (!cancelled) setLatestPriceFallback(null)
            })
        return () => {
            cancelled = true
        }
    }, [activeSymbol, targetPrice, stopLoss, analysisRunState])

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)] gap-4 min-h-[calc(100vh-5rem)]">
                <aside className="h-[70vh] min-h-[520px] lg:h-[calc(100vh-5rem)] lg:sticky lg:top-0 flex flex-col gap-4">
                    {/* [TA-UI-001] analysis_console_horizon_intent */}
                    <div className="card p-3 space-y-3">
                        <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                            <span className="font-medium">分析配置</span>
                        </div>
                        <div className="space-y-2">
                            <div>
                                <div className="text-[11px] text-slate-400 mb-1">周期</div>
                                <div className="flex gap-1">
                                    {HORIZON_OPTIONS.map(opt => (
                                        <button
                                            key={opt.value}
                                            onClick={() => setAnalysisHorizon(opt.value)}
                                            className={`px-3 py-1 text-xs rounded-md border transition-colors ${
                                                analysisHorizon === opt.value
                                                    ? 'bg-blue-500 text-white border-blue-500'
                                                    : 'bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border-slate-200 dark:border-slate-600 hover:border-blue-400'
                                            }`}
                                        >
                                            {opt.label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <div>
                                <div className="text-[11px] text-slate-400 mb-1">意图</div>
                                <div className="flex flex-wrap gap-1">
                                    {INTENT_OPTIONS.map(opt => (
                                        <button
                                            key={opt.value}
                                            onClick={() => setAnalysisIntent(opt.value)}
                                            className={`px-2 py-1 text-[11px] rounded-md border transition-colors ${
                                                analysisIntent === opt.value
                                                    ? 'bg-blue-500 text-white border-blue-500'
                                                    : 'bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border-slate-200 dark:border-slate-600 hover:border-blue-400'
                                            }`}
                                        >
                                            {opt.label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <div>
                                <div className="text-[11px] text-slate-400 mb-1">持仓</div>
                                <div className="flex gap-1">
                                    {POSITION_OPTIONS.map(opt => (
                                        <button
                                            key={String(opt.value)}
                                            onClick={() => setHasPosition(opt.value)}
                                            className={`px-3 py-1 text-xs rounded-md border transition-colors ${
                                                hasPosition === opt.value
                                                    ? 'bg-blue-500 text-white border-blue-500'
                                                    : 'bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border-slate-200 dark:border-slate-600 hover:border-blue-400'
                                            }`}
                                        >
                                            {opt.label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            {/* [PERF-004] full_ta_cost_gate */}
                            <div className="pt-1 border-t border-slate-200 dark:border-slate-600">
                                <button
                                    onClick={async () => {
                                        if (fullTaMode) {
                                            setFullTaMode(false)
                                            return
                                        }
                                        setCostPreviewLoading(true)
                                        setShowCostPreview(true)
                                        try {
                                            const preview = await api.getFullTACostPreview()
                                            setCostPreview(preview)
                                        } catch {
                                            setCostPreview(null)
                                        }
                                        setCostPreviewLoading(false)
                                    }}
                                    className={`w-full px-3 py-1.5 text-xs rounded-md border transition-colors ${
                                        fullTaMode
                                            ? 'bg-amber-500 text-white border-amber-500'
                                            : 'bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-400 border-slate-200 dark:border-slate-600 hover:border-amber-400'
                                    }`}
                                >
                                    {fullTaMode ? '完整 TA 已启用（点击关闭）' : '完整 TA（需确认）'}
                                </button>
                            </div>
                        </div>
                    </div>
                    <div className="min-h-0 flex-1">
                        <ChatCopilotPanel
                            onSymbolDetected={(symbol) => {
                                setActiveSymbol(symbol)
                                setCurrentSymbol(symbol)
                            }}
                            onShowReport={handleShowReport}
                            initialInput={initialChatInput}
                            horizon={analysisHorizon}
                            intent={analysisIntent}
                            hasPosition={hasPosition}
                            fullTaConfirmed={fullTaMode}
                        />
                    </div>
                </aside>

                <div className="min-w-0 space-y-4">
                    <div className="h-[300px] sm:h-[360px]">
                        <KlinePanel
                            symbol={activeSymbol}
                            onSymbolChange={(symbol) => {
                                setActiveSymbol(symbol)
                            }}
                        />
                    </div>

                    <AgentCollaboration onSelectSection={handleShowReport} onOpenDebate={setDebateDrawer} selectedSection={activeSection} />

                    <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                        <DecisionCard
                            symbol={activeSymbol}
                            report={report || undefined}
                            decision={mapDecision(report?.decision)}
                            direction={report?.direction}
                            confidence={confidence}
                            targetPrice={targetPrice}
                            stopLoss={stopLoss}
                            reasoning={finalDecision?.slice(0, 300)}
                            actionLabel={report?.action_label}
                            researchDirection={report?.research_direction}
                            executionAction={report?.execution_action}
                        />
                        <RiskRadar items={riskItems} />
                        <KeyMetrics items={keyMetrics} />
                    </div>

                    {/* [TRACK-006] add_to_observation — TA report -> observation warehouse */}
                    {report && (
                        <div className="flex flex-wrap items-center gap-3">
                            <button
                                onClick={() => {
                                    setObserveLoading(true)
                                    setObserveMsg(null)
                                    api.addTAReportToObservervation(activeSymbol, {
                                        action_label: report.action_label,
                                        research_direction: report.research_direction,
                                        target_price: report.target_price ?? targetPrice ?? null,
                                        stop_loss_price: report.stop_loss_price ?? stopLoss ?? null,
                                        via: 'analysis_page',
                                    })
                                        .then(res => {
                                            if (res.status === 'ok') {
                                                const verb = res.action === 'updated' ? '已更新' : '已加入'
                                                setObserveMsg(`${verb}观察仓 · ${res.symbol || activeSymbol}（来源：TA 报告）`)
                                            } else {
                                                setObserveMsg(res.message || '加入失败，请稍后重试')
                                            }
                                        })
                                        .catch(() => setObserveMsg('加入失败，请稍后重试'))
                                        .finally(() => setObserveLoading(false))
                                }}
                                disabled={observeLoading}
                                className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-900/20 px-4 py-2 text-sm font-medium text-indigo-600 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/40 transition-colors disabled:opacity-50"
                            >
                                {observeLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
                                {observeLoading ? '加入中...' : '加入观察仓'}
                            </button>
                            {observeMsg && (
                                <span className={`text-xs px-3 py-1.5 rounded ${observeMsg.includes('已加入') || observeMsg.includes('已更新') ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400' : 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400'}`}>
                                    {observeMsg}
                                </span>
                            )}
                            <span className="text-[11px] text-slate-400">
                                将当前 TA 报告的动作语义、目标价 / 止损价写入观察仓，可重复点击刷新。
                            </span>
                        </div>
                    )}

                    <div ref={reportRef}>
                        <ReportViewer activeSection={activeSection} />
                    </div>
                </div>
            </div>

            <DebateDrawer debate={debateDrawer} onClose={() => setDebateDrawer(null)} />

            {/* [PERF-004] full_ta_cost_gate - Confirmation Modal */}
            {showCostPreview && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCostPreview(false)}>
                    <div className="bg-white dark:bg-slate-800 rounded-xl shadow-2xl max-w-md w-full mx-4 p-5" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-4">
                            <div className="flex items-center gap-2">
                                <AlertTriangle className="w-5 h-5 text-amber-500" />
                                <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">完整 TA 成本确认</h3>
                            </div>
                            <button onClick={() => setShowCostPreview(false)} className="text-slate-400 hover:text-slate-600">
                                <X className="w-4 h-4" />
                            </button>
                        </div>
                        {costPreviewLoading ? (
                            <div className="text-sm text-slate-500 py-6 text-center">加载成本预估...</div>
                        ) : costPreview ? (
                            <div className="space-y-3 text-sm">
                                <div className="grid grid-cols-2 gap-2">
                                    <div className="text-slate-500">运行层级</div>
                                    <div className="font-medium text-slate-900 dark:text-slate-100">{costPreview.tier_label}</div>
                                    <div className="text-slate-500">预计耗时</div>
                                    <div className="font-medium text-slate-900 dark:text-slate-100">{costPreview.expected_latency}</div>
                                    <div className="text-slate-500">成本风险</div>
                                    <div className="font-medium text-amber-600">{costPreview.cost_risk === 'high' ? '高' : costPreview.cost_risk}</div>
                                    <div className="text-slate-500">模型厂商</div>
                                    <div className="font-medium text-slate-900 dark:text-slate-100">{costPreview.llm_provider || '默认'}</div>
                                    <div className="text-slate-500">模型</div>
                                    <div className="font-medium text-slate-900 dark:text-slate-100 break-all">{costPreview.llm_model || '默认'}</div>
                                    <div className="text-slate-500">预计调用次数</div>
                                    <div className="font-medium text-slate-900 dark:text-slate-100">~{costPreview.estimated_llm_calls} 次</div>
                                </div>
                                <div className="border-t border-slate-200 dark:border-slate-600 pt-3">
                                    <div className="text-slate-500 mb-1">启用模块</div>
                                    <div className="flex flex-wrap gap-1">
                                        {costPreview.enabled_modules.slice(0, 8).map(m => (
                                            <span key={m} className="text-[10px] px-1.5 py-0.5 bg-slate-100 dark:bg-slate-700 rounded text-slate-600 dark:text-slate-300">{m}</span>
                                        ))}
                                        {costPreview.enabled_modules.length > 8 && (
                                            <span className="text-[10px] px-1.5 py-0.5 bg-slate-100 dark:bg-slate-700 rounded text-slate-600 dark:text-slate-300">+{costPreview.enabled_modules.length - 8}</span>
                                        )}
                                    </div>
                                </div>
                                <div className="text-xs text-amber-600 bg-amber-50 dark:bg-amber-900/20 rounded p-2">
                                    完整 TA 将调用 14 个 Agent 进行多空辩论和风控分析，预计耗时 {costPreview.expected_latency}，产生约 {costPreview.estimated_llm_calls} 次 LLM 调用。
                                </div>
                            </div>
                        ) : (
                            <div className="text-sm text-red-500 py-4 text-center">无法获取成本预估，请检查模型配置。</div>
                        )}
                        <div className="flex gap-2 mt-4">
                            <button
                                onClick={() => setShowCostPreview(false)}
                                className="flex-1 px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700"
                            >
                                取消
                            </button>
                            <button
                                onClick={() => {
                                    setFullTaMode(true)
                                    setShowCostPreview(false)
                                }}
                                disabled={!costPreview}
                                className="flex-1 px-3 py-2 text-sm rounded-lg bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                确认启动完整 TA
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
