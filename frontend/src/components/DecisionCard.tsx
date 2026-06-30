import { useState } from 'react'
import { TrendingUp, TrendingDown, Target, Shield, ChevronDown, ChevronUp, Info } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AnalysisReport } from '@/types'
import { WAIT_REASON_LABELS } from '@/types'
import { sanitizeReportMarkdown } from '@/utils/reportText'

interface DecisionCardProps {
    symbol: string
    name?: string
    decision?: 'buy' | 'sell' | 'hold' | 'add' | 'reduce' | 'watch'
    direction?: string
    confidence?: number
    targetPrice?: number
    targetChange?: number
    stopLoss?: number
    stopLossChange?: number
    reasoning?: string
    riskLevel?: 'low' | 'medium' | 'high'
    report?: AnalysisReport
    actionLabel?: string
    researchDirection?: string
    executionAction?: string
    // [REPORT-UX-003] wait_reason_codes — explain WHY the action is WAIT.
    waitReasonCodes?: string[]
    waitReasonLabels?: Record<string, string>
}

const decisionConfig: Record<string, { label: string; color: string; icon: typeof TrendingUp }> = {
    buy: { label: '买入', color: 'bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 border-red-200 dark:border-red-500/30', icon: TrendingUp },
    sell: { label: '卖出', color: 'bg-green-100 dark:bg-green-500/20 text-green-700 dark:text-green-400 border-green-200 dark:border-green-500/30', icon: TrendingDown },
    hold: { label: '持有', color: 'bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-400 border-blue-200 dark:border-blue-500/30', icon: Shield },
    add: { label: '增持', color: 'bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 border-red-200 dark:border-red-500/30', icon: TrendingUp },
    reduce: { label: '减持', color: 'bg-orange-100 dark:bg-orange-500/20 text-orange-700 dark:text-orange-400 border-orange-200 dark:border-orange-500/30', icon: TrendingDown },
    watch: { label: '观望', color: 'bg-slate-100 dark:bg-slate-700/50 text-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-600', icon: Info },
}

export default function DecisionCard({
    symbol,
    name = symbol,
    decision: propDecision,
    direction,
    confidence,
    targetPrice,
    targetChange,
    stopLoss,
    stopLossChange,
    reasoning,
    riskLevel,
    report,
    actionLabel: propActionLabel,
    researchDirection: propResearchDirection,
    executionAction: propExecutionAction,
    waitReasonCodes: propWaitReasonCodes,
    waitReasonLabels: propWaitReasonLabels,
}: DecisionCardProps) {
    const [expanded, setExpanded] = useState(false)

    const actionLabel = propActionLabel || report?.action_label
    const researchDirection = propResearchDirection || report?.research_direction
    const executionAction = propExecutionAction || report?.execution_action
    // [REPORT-UX-003] wait_reason_codes — prefer explicit props, fall back to
    // report.result_data, so both detail page and Analysis page can supply them.
    const waitReasonCodes: string[] = (
        propWaitReasonCodes
        || report?.wait_reason_codes
        || []
    ).filter(Boolean)
    const waitReasonLabels: Record<string, string> = propWaitReasonLabels
        || report?.wait_reason_labels
        || WAIT_REASON_LABELS

    const actionLabelColorMap: Record<string, string> = {
        '等待触发': 'bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 border-red-200 dark:border-red-500/30',
        '条件入场': 'bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 border-red-200 dark:border-red-500/30',
        '持有': 'bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-400 border-blue-200 dark:border-blue-500/30',
        '回避': 'bg-slate-100 dark:bg-slate-700/50 text-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-600',
        '条件减仓': 'bg-orange-100 dark:bg-orange-500/20 text-orange-700 dark:text-orange-400 border-orange-200 dark:border-orange-500/30',
        '清仓': 'bg-orange-100 dark:bg-orange-500/20 text-orange-700 dark:text-orange-400 border-orange-200 dark:border-orange-500/30',
        '数据不足观察': 'bg-slate-100 dark:bg-slate-700/50 text-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-600',
    }

    const researchDirectionColorMap: Record<string, string> = {
        '看多': 'text-red-600 dark:text-red-400',
        '偏多': 'text-red-500 dark:text-red-300',
        '中性': 'text-slate-500 dark:text-slate-400',
        '偏空': 'text-green-500 dark:text-green-300',
        '看空': 'text-green-600 dark:text-green-400',
    }

    const executionActionColorMap: Record<string, string> = {
        'WAIT': 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300',
        'ENTER': 'bg-red-100 dark:bg-red-500/20 text-red-600 dark:text-red-400',
        'HOLD': 'bg-blue-100 dark:bg-blue-500/20 text-blue-600 dark:text-blue-400',
        'REDUCE': 'bg-orange-100 dark:bg-orange-500/20 text-orange-600 dark:text-orange-400',
        'EXIT': 'bg-orange-100 dark:bg-orange-500/20 text-orange-600 dark:text-orange-400',
    }

    // [REPORT-UX-003] wait_reason_codes — per-code chip styling so users can
    // tell a genuine data gap apart from "no trigger yet" or "gate blocked".
    const waitReasonChipClass = (code: string): string => {
        if (code === 'DATA_MISSING') {
            return 'bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/30'
        }
        if (code === 'GATE_BLOCKED') {
            return 'bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/30'
        }
        if (code === 'CONFLICT') {
            return 'bg-orange-50 text-orange-700 ring-orange-200 dark:bg-orange-500/10 dark:text-orange-300 dark:ring-orange-500/30'
        }
        if (code === 'RISK_FIRST') {
            return 'bg-green-50 text-green-700 ring-green-200 dark:bg-green-500/10 dark:text-green-300 dark:ring-green-500/30'
        }
        // NO_TRIGGER / NORMAL_NO_DATA — neutral info tone.
        return 'bg-slate-50 text-slate-600 ring-slate-200 dark:bg-slate-700/40 dark:text-slate-300 dark:ring-slate-600'
    }

    // Only render reason chips for WAIT-family actions; ENTER/HOLD/etc have no
    // wait reason to explain and showing empty chips would be noise.
    const showWaitReasons = executionAction === 'WAIT' && waitReasonCodes.length > 0

    const parseDecision = (text?: string): 'buy' | 'sell' | 'hold' | 'add' | 'reduce' | 'watch' | undefined => {
        if (!text) return propDecision
        const lower = text.toLowerCase()
        if (lower.includes('sell') || lower.includes('卖出')) return 'sell'
        if (lower.includes('reduce') || lower.includes('减持')) return 'reduce'
        if (lower.includes('watch') || lower.includes('观望')) return 'watch'
        if (lower.includes('hold') || lower.includes('持有')) return 'hold'
        if (lower.includes('add') || lower.includes('增持')) return 'add'
        if (lower.includes('buy') || lower.includes('买入')) return 'buy'
        return undefined
    }

    const decision = propDecision || parseDecision(report?.decision || report?.final_trade_decision)
    const config = decision ? (decisionConfig[decision] || decisionConfig.hold) : null
    const DecisionIcon = config?.icon

    const riskLabels: Record<string, string> = { low: '低', medium: '中等', high: '高' }
    const riskColors: Record<string, string> = {
        low: 'text-green-600 dark:text-green-400',
        medium: 'text-yellow-600 dark:text-yellow-400',
        high: 'text-red-600 dark:text-red-400',
    }

    return (
        <div className="card overflow-hidden">
            {/* 头部 */}
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-cyan-400 flex items-center justify-center">
                        <TrendingUp className="w-5 h-5 text-white" />
                    </div>
                    <div>
                        <h3 className="font-semibold text-slate-900 dark:text-slate-100">{name}</h3>
                        <p className="text-sm text-slate-500">{symbol}</p>
                        {researchDirection && (
                            <p className={`text-xs mt-0.5 font-medium ${researchDirectionColorMap[researchDirection] || 'text-slate-400'}`}>
                                {researchDirection}
                            </p>
                        )}
                        {!researchDirection && direction && (
                            <p className="text-xs text-slate-400 mt-0.5">方向：{direction}</p>
                        )}
                    </div>
                </div>
                <div className="flex flex-col items-end gap-1.5">
                    {actionLabel ? (
                        <div className={`px-4 py-2 rounded-full border font-medium text-base ${actionLabelColorMap[actionLabel] || 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 border-slate-200 dark:border-slate-600'}`}>
                            {actionLabel}
                        </div>
                    ) : config && DecisionIcon ? (
                        <div className={`px-4 py-2 rounded-full border font-medium flex items-center gap-1.5 ${config.color}`}>
                            <DecisionIcon className="w-4 h-4" />
                            {config.label}
                        </div>
                    ) : (
                        <div className="px-4 py-2 rounded-full border font-medium text-slate-400 border-slate-200 dark:border-slate-700">
                            等待裁决
                        </div>
                    )}
                    {executionAction && (
                        <span className={`text-[10px] px-2 py-0.5 rounded font-mono ${executionActionColorMap[executionAction] || 'bg-slate-100 dark:bg-slate-700 text-slate-500'}`}>
                            {executionAction}
                        </span>
                    )}
                </div>
            </div>

            {/* [REPORT-UX-003] 观察原因 — decompose "数据不足观察" into explainable chips */}
            {showWaitReasons && (
                <div className="mb-4 rounded-xl bg-slate-50 dark:bg-slate-800/50 ring-1 ring-slate-200 dark:ring-slate-700 p-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
                            观察原因：
                        </span>
                        {waitReasonCodes.map(code => (
                            <span
                                key={code}
                                className={`rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${waitReasonChipClass(code)}`}
                            >
                                {waitReasonLabels[code] || WAIT_REASON_LABELS[code] || code}
                            </span>
                        ))}
                    </div>
                    <p className="mt-1.5 text-[11px] leading-4 text-slate-400 dark:text-slate-500">
                        系统未给出强动作，以上为本次观察的具体原因，可据此判断是否补数据或等待触发。
                    </p>
                </div>
            )}

            {/* 置信度 */}
            {confidence != null && (
                <div className="mb-4">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-sm text-slate-500">置信度</span>
                        <span className="text-sm font-medium text-slate-700 dark:text-slate-300">{confidence}%</span>
                    </div>
                    <div className="h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                        <div
                            className="h-full bg-gradient-to-r from-blue-500 to-cyan-400 rounded-full transition-all duration-1000"
                            style={{ width: `${confidence}%` }}
                        />
                    </div>
                </div>
            )}

            {/* 目标价和止损价 */}
            <div className="grid grid-cols-2 gap-3 mb-4">
                <div className="p-3 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20">
                    <div className="flex items-center gap-1.5 mb-1">
                        <Target className="w-4 h-4 text-red-600 dark:text-red-400" />
                        <span className="text-xs text-slate-500">目标价</span>
                    </div>
                    <p className="text-xl font-bold text-red-600 dark:text-red-400">
                        {targetPrice != null ? `¥${targetPrice}` : '--'}
                    </p>
                    {targetChange != null && (
                        <p className="text-sm text-red-600 dark:text-red-400">
                            {targetChange >= 0 ? '+' : ''}{targetChange.toFixed(1)}%
                        </p>
                    )}
                </div>
                <div className="p-3 rounded-xl bg-green-50 dark:bg-green-500/10 border border-green-100 dark:border-green-500/20">
                    <div className="flex items-center gap-1.5 mb-1">
                        <Shield className="w-4 h-4 text-green-600 dark:text-green-400" />
                        <span className="text-xs text-slate-500">止损价</span>
                    </div>
                    <p className="text-xl font-bold text-green-600 dark:text-green-400">
                        {stopLoss != null ? `¥${stopLoss}` : '--'}
                    </p>
                    {stopLossChange != null && (
                        <p className="text-sm text-green-600 dark:text-green-400">
                            {stopLossChange >= 0 ? '+' : ''}{stopLossChange.toFixed(1)}%
                        </p>
                    )}
                </div>
            </div>

            {/* 展开详情 */}
            {expanded && (reasoning || riskLevel) && (
                <div className="mb-4 p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 space-y-2">
                    {reasoning && (
                        <div>
                            <span className="text-sm text-slate-500">核心逻辑</span>
                            <div className="mt-1 prose prose-sm dark:prose-invert max-w-none text-slate-700 dark:text-slate-300">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                    {sanitizeReportMarkdown(reasoning)}
                                </ReactMarkdown>
                            </div>
                        </div>
                    )}
                    {riskLevel && (
                        <div className="flex justify-between">
                            <span className="text-sm text-slate-500">风险等级</span>
                            <span className={`text-sm font-medium ${riskColors[riskLevel]}`}>{riskLabels[riskLevel]}</span>
                        </div>
                    )}
                </div>
            )}

            {/* 操作按钮 */}
            <div className="flex flex-wrap gap-2">
                {(reasoning || riskLevel) && (
                    <button
                        onClick={() => setExpanded(!expanded)}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
                    >
                        {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                        {expanded ? '收起' : '详情'}
                    </button>
                )}
            </div>
        </div>
    )
}
