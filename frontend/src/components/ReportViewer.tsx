import { FileText, Download, ChevronDown, ChevronRight, Loader2, MousePointerClick, BookOpen } from 'lucide-react'
import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useAnalysisStore } from '@/stores/analysisStore'
import type { ReportDetail } from '@/types'
import { sanitizeReportMarkdown, selectReportTextForDiagnostics, splitReportSystemDiagnostics } from '@/utils/reportText'
import { deriveKnowledgeEvidenceCard } from '@/utils/knowledgeContract'
import ResearchEvidenceCenter from '@/components/ResearchEvidenceCenter'

const REPORT_SECTIONS = [
    { key: 'market_report', title: '市场分析报告', team: '分析团队' },
    { key: 'sentiment_report', title: '舆情分析报告', team: '分析团队' },
    { key: 'news_report', title: '新闻分析报告', team: '分析团队' },
    { key: 'fundamentals_report', title: '基本面分析报告', team: '分析团队' },
    { key: 'macro_report', title: '宏观板块报告', team: '分析团队' },
    { key: 'smart_money_report', title: '主力资金报告', team: '分析团队' },
    { key: 'volume_price_report', title: '量价分析报告', team: '分析团队' },
    { key: 'investment_plan', title: '研究团队决策', team: '研究团队' },
    { key: 'trader_investment_plan', title: '交易团队计划', team: '交易团队' },
    { key: 'final_trade_decision', title: '最终交易决策', team: '组合管理' },
]

const REPORT_DISCLAIMER =
    '> 免责声明：以上内容由模型基于公开数据、历史信息与预设规则自动生成，仅供研究参考，不构成任何投资建议、收益承诺或实际交易指令。'

const MD_COMPONENTS = {
    table: ({ children }: { children?: React.ReactNode }) => (
        <table className="w-full border-collapse border border-slate-300 dark:border-slate-600 my-4">{children}</table>
    ),
    thead: ({ children }: { children?: React.ReactNode }) => (
        <thead className="bg-slate-100 dark:bg-slate-700">{children}</thead>
    ),
    th: ({ children }: { children?: React.ReactNode }) => (
        <th className="border border-slate-300 dark:border-slate-600 px-3 py-2 text-left font-semibold text-slate-700 dark:text-slate-300">{children}</th>
    ),
    td: ({ children }: { children?: React.ReactNode }) => (
        <td className="border border-slate-300 dark:border-slate-600 px-3 py-2 text-slate-600 dark:text-slate-400">{children}</td>
    ),
    tr: ({ children }: { children?: React.ReactNode }) => (
        <tr className="even:bg-slate-50 dark:even:bg-slate-800/50">{children}</tr>
    ),
}

interface ReportViewerProps {
    /** 传入后进入历史报告模式，不读取 store */
    reportData?: ReportDetail
    /** 当前选中章节（实时模式：点哪个智能体就显示哪个） */
    activeSection?: string
}

export default function ReportViewer({ reportData, activeSection }: ReportViewerProps = {}) {
    const { report, streamingSections, isAnalyzing } = useAnalysisStore()
    const [expandedSections, setExpandedSections] = useState<string[]>([])
    const isHistorical = !!reportData

    const getRawSectionContent = (key: string): string => {
        if (isHistorical) {
            return (reportData?.[key as keyof ReportDetail] as string | undefined) || ''
        }
        const s = streamingSections[key]
        const completed = (report?.[key as keyof typeof report] as string | undefined) || ''
        if (key === 'final_trade_decision') {
            const rawOffset = report?.metadata?.system_diagnostics_offset
            return selectReportTextForDiagnostics(
                s?.displayed,
                completed,
                typeof rawOffset === 'number' ? rawOffset : undefined,
            )
        }
        return s?.displayed || completed
    }

    const getSectionParts = (key: string) => {
        const content = getRawSectionContent(key)
        const metadata = isHistorical ? reportData?.result_data?.metadata : report?.metadata
        const rawOffset = metadata?.system_diagnostics_offset
        const trustedOffset = typeof rawOffset === 'number' ? rawOffset : undefined
        return key === 'final_trade_decision'
            ? splitReportSystemDiagnostics(content, trustedOffset)
            : { main: sanitizeReportMarkdown(content), diagnostics: '' }
    }

    const getSectionState = (key: string) => {
        if (isHistorical) return { isStreaming: false, isComplete: true }
        const s = streamingSections[key]
        return {
            isStreaming: s?.isTyping || false,
            isComplete: s?.isComplete || !!(report?.[key as keyof typeof report]),
        }
    }

    const hasAnyContent = isHistorical
        ? REPORT_SECTIONS.some(s => !!reportData?.[s.key as keyof ReportDetail])
        : Object.keys(streamingSections).length > 0 || (report && Object.values(report).some(v => typeof v === 'string' && v.length > 0))

    // ── Historical mode: auto-expand first 2 sections with content ────────────
    useEffect(() => {
        if (!isHistorical) return
        const withContent = REPORT_SECTIONS
            .filter(s => !!reportData?.[s.key as keyof ReportDetail])
            .map(s => s.key)
        setExpandedSections(withContent.slice(0, 2))
    }, [isHistorical, reportData])

    // Historical mode: accordion toggle
    const toggleSection = (key: string) =>
        setExpandedSections(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])

    const handleExport = () => {
        const source = isHistorical ? reportData : report
        if (!source) return
        const text = REPORT_SECTIONS
            .filter(s => source[s.key as keyof typeof source])
            .map(s => `## ${s.title}\n\n${source[s.key as keyof typeof source]}`)
            .join('\n\n---\n\n') + `\n\n---\n\n${REPORT_DISCLAIMER}\n`
        const blob = new Blob([text], { type: 'text/markdown' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `analysis-${isHistorical ? reportData?.symbol : report?.symbol || 'report'}.md`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
    }

    // ── Historical mode: full accordion ──────────────────────────────────────
    if (isHistorical) {
        if (!hasAnyContent) {
            return (
                <div className="flex items-center justify-center py-12">
                    <div className="text-center">
                        <FileText className="w-12 h-12 text-slate-300 dark:text-slate-600 mx-auto mb-4" />
                        <p className="text-slate-500 dark:text-slate-400">暂无分析报告</p>
                    </div>
                </div>
            )
        }
        return (
            <div className="space-y-2">
                <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                        <FileText className="w-5 h-5 text-blue-500" />
                        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">分析报告</h2>
                    </div>
                    <button onClick={handleExport} className="btn-secondary flex items-center gap-2 text-sm py-1.5 px-3">
                        <Download className="w-4 h-4" />
                        导出
                    </button>
                </div>
                <div className="space-y-3">
                    {REPORT_SECTIONS.map((section) => {
                        const { main: content, diagnostics } = getSectionParts(section.key)
                        if (!content && !diagnostics) return null
                        const isExpanded = expandedSections.includes(section.key)
                        return (
                            <div key={section.key} className="border border-slate-200 dark:border-slate-700 rounded-2xl overflow-hidden bg-white dark:bg-slate-900/40">
                                <button
                                    onClick={() => toggleSection(section.key)}
                                    className="w-full flex items-center justify-between p-4 bg-slate-50/90 dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                                >
                                    <div className="flex items-center gap-2">
                                        {isExpanded ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                                        <span className="font-medium text-slate-900 dark:text-slate-100">{section.title}</span>
                                        <span className="text-xs text-slate-500 dark:text-slate-400">{section.team}</span>
                                    </div>
                                    <span className="text-xs text-green-500">✓</span>
                                </button>
                                {isExpanded && (
                                    <div className="p-5 bg-white dark:bg-slate-800/30">
                                        <div className="prose dark:prose-invert prose-sm md:prose-base max-w-none">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{content}</ReactMarkdown>
                                        </div>
                                        {diagnostics && (
                                            <details className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-700">
                                                <summary className="cursor-pointer text-sm font-medium text-slate-600 dark:text-slate-300">
                                                    系统审计详情
                                                </summary>
                                                <div className="prose dark:prose-invert prose-sm max-w-none mt-3 text-slate-600 dark:text-slate-400">
                                                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{diagnostics}</ReactMarkdown>
                                                </div>
                                            </details>
                                        )}
                                    </div>
                                )}
                            </div>
                        )
                    })}
                    {/* [REPORT-UX-006] knowledge_evidence_card — lightweight
                        evidence-health strip derived purely from the
                        local_knowledge_summary + half_year_facts_summary
                        already attached to the report. It answers "用了哪些
                        本地资料 / 是否过期冲突 / 还缺什么" without re-parsing
                        the markdown body and WITHOUT altering
                        decision/execution_action/action_label. Hidden for
                        truly legacy reports (no KB fields at all). */}
                    {(() => {
                        const card = deriveKnowledgeEvidenceCard({
                            localKnowledgeSummary: reportData?.local_knowledge_summary,
                            halfYearFactsSummary: reportData?.half_year_facts_summary,
                            halfYearFactsStatus: reportData?.half_year_facts_status,
                        })
                        if (!card.hasAnySignal) return null
                        const freshnessLabel: Record<typeof card.freshness, string> = {
                            fresh: '证据可用',
                            stale: '证据过期',
                            conflict: '事实冲突',
                            disabled: '知识库禁用',
                            no_data: '暂无命中',
                        }
                        const freshnessClass: Record<typeof card.freshness, string> = {
                            fresh: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300',
                            stale: 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300',
                            conflict: 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300',
                            disabled: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
                            no_data: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
                        }
                        return (
                            <div className="border border-sky-200/70 dark:border-sky-500/20 rounded-2xl overflow-hidden bg-sky-50/30 dark:bg-sky-900/10">
                                <div className="flex items-center gap-2 px-4 py-2.5 bg-sky-50/80 dark:bg-sky-900/20">
                                    <BookOpen className="w-4 h-4 text-sky-500" />
                                    <span className="font-medium text-slate-900 dark:text-slate-100">知识证据来源</span>
                                    <span className="text-xs text-slate-500 dark:text-slate-400">仅作研究背景，不影响买卖动作</span>
                                    <span className={`ml-auto inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium ${freshnessClass[card.freshness]}`}>
                                        {freshnessLabel[card.freshness]}
                                    </span>
                                </div>
                                <div className="p-4 bg-white dark:bg-slate-800/30 space-y-2">
                                    <div className="flex flex-wrap gap-2">
                                        {card.localKnowledgeHitCount > 0 && (
                                            <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                                                本地知识命中 {card.localKnowledgeHitCount} 条
                                            </span>
                                        )}
                                        {card.halfYearHitCount > 0 && (
                                            <span className="inline-flex items-center rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">
                                                半年报 {card.halfYearHitCount} 页
                                            </span>
                                        )}
                                        {card.halfYearPeriod && (
                                            <span className="inline-flex items-center rounded bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-600 dark:bg-sky-900/30 dark:text-sky-300">
                                                报告期 {card.halfYearPeriod}
                                            </span>
                                        )}
                                        {card.bestSourceTier && (
                                            <span className="inline-flex items-center rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-300">
                                                来源等级 {card.bestSourceTier}
                                            </span>
                                        )}
                                        {card.latestDate && (
                                            <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                                                最近更新 {card.latestDate}
                                            </span>
                                        )}
                                    </div>
                                    {card.sourcePaths.length > 0 && (
                                        <div className="flex flex-wrap gap-1">
                                            {card.sourcePaths.slice(0, 5).map((p) => (
                                                <code key={p} className="inline-block max-w-full truncate rounded bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-500 dark:bg-slate-800/60 dark:text-slate-400" title={p}>
                                                    {p}
                                                </code>
                                            ))}
                                        </div>
                                    )}
                                    <div className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${
                                        card.gapCode === 'none'
                                            ? 'border-slate-100 bg-slate-50/70 text-slate-600 dark:border-slate-700 dark:bg-slate-800/30 dark:text-slate-300'
                                            : 'border-amber-100 bg-amber-50/60 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200'
                                    }`}>
                                        {card.gapExplanation}
                                    </div>
                                </div>
                            </div>
                        )
                    })()}
                    {/* [KB-011] knowledge_contract_ui — 本地知识补充 / 研报关注度
                        block rendered from top-level KB fields. Falls back
                        silently when the report predates KB-003 (then the
                        fields are null and the card is hidden). The block is
                        already a markdown blob produced by
                        ``render_local_knowledge_block`` + KB-008 inline
                        section, so we render it verbatim through ReactMarkdown. */}
                    {(() => {
                        const lkBlock = reportData?.local_knowledge_block
                        const lkSummary = reportData?.local_knowledge_summary
                        const attentionScore = reportData?.research_attention_score
                        const themeCount = reportData?.knowledge_theme_count
                        const attentionSummary = reportData?.research_attention_summary
                        if (!lkBlock && attentionScore == null && !attentionSummary) return null
                        // local_knowledge_summary on reports is a dict; pull a
                        // status safely without assuming any specific key.
                        const lkStatus = lkSummary && typeof lkSummary === 'object' && 'status' in lkSummary
                            ? String((lkSummary as Record<string, unknown>).status || '')
                            : ''
                        const lkMatched = lkSummary && typeof lkSummary === 'object' && 'matched_count' in lkSummary
                            ? Number((lkSummary as Record<string, unknown>).matched_count || 0)
                            : 0
                        return (
                            <div className="border border-indigo-200/70 dark:border-indigo-500/20 rounded-2xl overflow-hidden bg-indigo-50/30 dark:bg-indigo-900/10">
                                <div className="flex items-center gap-2 px-4 py-2.5 bg-indigo-50/80 dark:bg-indigo-900/20">
                                    <BookOpen className="w-4 h-4 text-indigo-500" />
                                    <span className="font-medium text-slate-900 dark:text-slate-100">本地知识补充</span>
                                    <span className="text-xs text-slate-500 dark:text-slate-400">仅作为研究背景，不构成数据完整或买卖依据</span>
                                    {lkStatus === 'NORMAL_NO_DATA' && (
                                        <span className="ml-auto inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                                            暂无本地知识
                                        </span>
                                    )}
                                </div>
                                <div className="p-4 bg-white dark:bg-slate-800/30 space-y-2">
                                    {(attentionScore != null || themeCount != null || attentionSummary) && (
                                        <div className="flex flex-wrap gap-2">
                                            {attentionScore != null && attentionScore > 0 && (
                                                <span className="inline-flex items-center rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-300">
                                                    研报关注度 {Number(attentionScore).toFixed(2)}
                                                </span>
                                            )}
                                            {themeCount != null && themeCount > 0 && (
                                                <span className="inline-flex items-center rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-300">
                                                    主题交叉 {themeCount}
                                                </span>
                                            )}
                                            {lkMatched > 0 && (
                                                <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                                                    本地知识命中 {lkMatched} 条
                                                </span>
                                            )}
                                        </div>
                                    )}
                                    {lkBlock && (
                                        <div className="prose dark:prose-invert prose-sm max-w-none">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{lkBlock}</ReactMarkdown>
                                        </div>
                                    )}
                                    {attentionSummary && (
                                        <div className="rounded-lg border border-slate-100 bg-slate-50/70 p-3 text-xs leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-800/30 dark:text-slate-300">
                                            {attentionSummary}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )
                    })()}
                    {/* [UI-014] research_evidence_center — 研报证据中心与来源下钻
                        Lazy-loads KB-020 aggregation on first expand.
                        Failure does not block report body rendering. */}
                    {reportData?.symbol && (
                        <ResearchEvidenceCenter symbol={reportData.symbol} />
                    )}
                    <div className="rounded-2xl border border-amber-200/80 bg-amber-50/80 px-4 py-3 text-xs leading-6 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{REPORT_DISCLAIMER}</ReactMarkdown>
                    </div>
                </div>
            </div>
        )
    }

    // ── Live mode: single-section viewer ─────────────────────────────────────
    const activeMeta = activeSection ? REPORT_SECTIONS.find(s => s.key === activeSection) : null
    const activeParts = activeSection ? getSectionParts(activeSection) : { main: '', diagnostics: '' }
    const activeContent = activeParts.main
    const { isStreaming: activeStreaming } = activeSection ? getSectionState(activeSection) : { isStreaming: false }

    return (
        <div className="card flex-1 flex flex-col min-h-0 ring-1 ring-slate-200/70 dark:ring-slate-800 shadow-[0_16px_40px_rgba(15,23,42,0.06)] dark:shadow-none">
            {/* Header */}
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                    <FileText className="w-5 h-5 text-blue-500" />
                    <div>
                        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                            {activeMeta ? activeMeta.title : '分析报告'}
                        </h2>
                        {activeMeta ? (
                            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                                {activeMeta.team} · 点击其他智能体切换报告
                            </p>
                        ) : (
                            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                                点击上方智能体卡片查看完整报告
                            </p>
                        )}
                    </div>
                    {isAnalyzing && (
                        <span className="badge-orange animate-pulse">生成中</span>
                    )}
                </div>
                {hasAnyContent && (
                    <button onClick={handleExport} className="btn-secondary flex items-center gap-2 text-sm py-1.5 px-3">
                        <Download className="w-4 h-4" />
                        导出全部
                    </button>
                )}
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto min-h-0">
                {!activeMeta ? (
                    /* Empty state */
                    <div className="flex flex-col items-center justify-center py-16 gap-3">
                        <MousePointerClick className="w-10 h-10 text-slate-300 dark:text-slate-600" />
                        <p className="text-sm font-medium text-slate-400 dark:text-slate-500">
                            点击上方智能体卡片查看报告
                        </p>
                        {isAnalyzing && !hasAnyContent && (
                            <div className="flex items-center gap-1.5 mt-1 text-xs text-slate-400 dark:text-slate-500">
                                <Loader2 className="w-3 h-3 animate-spin" />
                                等待首个章节输出...
                            </div>
                        )}
                    </div>
                ) : (
                    /* Single section content */
                    <div className="space-y-4">
                        <div className="border border-slate-200 dark:border-slate-700 rounded-2xl overflow-hidden bg-white dark:bg-slate-900/40">
                            <div className="p-5 bg-white dark:bg-slate-800/30">
                                {activeContent ? (
                                    <div className="prose dark:prose-invert prose-sm md:prose-base max-w-none">
                                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                            {activeContent}
                                        </ReactMarkdown>
                                        {activeStreaming && (
                                            <span className="inline-block w-2 h-4 bg-blue-500 animate-pulse ml-1" />
                                        )}
                                    </div>
                                ) : (
                                    <div className="flex items-center justify-center py-10 text-slate-400 dark:text-slate-500">
                                        <Loader2 className="w-4 h-4 animate-spin mr-2" />
                                        正在生成报告...
                                    </div>
                                )}
                                {activeParts.diagnostics && (
                                    <details className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-700">
                                        <summary className="cursor-pointer text-sm font-medium text-slate-600 dark:text-slate-300">
                                            系统审计详情
                                        </summary>
                                        <div className="prose dark:prose-invert prose-sm max-w-none mt-3 text-slate-600 dark:text-slate-400">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                {activeParts.diagnostics}
                                            </ReactMarkdown>
                                        </div>
                                    </details>
                                )}
                            </div>
                        </div>

                        {activeContent && (
                            <div className="rounded-2xl border border-amber-200/80 bg-amber-50/80 px-4 py-3 text-xs leading-6 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>{REPORT_DISCLAIMER}</ReactMarkdown>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    )
}
