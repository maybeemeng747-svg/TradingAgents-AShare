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
                        />
                        <RiskRadar items={riskItems} />
                        <KeyMetrics items={keyMetrics} />
                    </div>

                    <div ref={reportRef}>
                        <ReportViewer activeSection={activeSection} />
                    </div>
                </div>
            </div>

            <DebateDrawer debate={debateDrawer} onClose={() => setDebateDrawer(null)} />
        </div>
    )
}
