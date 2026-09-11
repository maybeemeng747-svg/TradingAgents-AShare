// [UI-014] research_evidence_center
//
// Lightweight "研报证据中心" component that shows the 5-bucket KB-020
// aggregated evidence for a symbol: consensus, citation audit, thesis timeline,
// half-year facts, and research score snapshot.
//
// Design contracts:
//   - Lazy-loads on first expand; failure must NOT block report body.
//   - No long research report text displayed; only summaries + source paths.
//   - Evidence colors for support/weaken/conflict/pending only (not trade
//     action colors).
//   - Read-only presentation layer; never alters decision/action_label.
//   - [UI-014-R1] 按 KB-020 真实 summary schema 展示（视图模型见
//     utils/researchEvidenceCenter.ts）；symbol 变化即清空旧数据；
//     失败稳定展示错误，不自动重试，手动重试有上限，卸载丢弃迟到响应。

import { useState, useEffect, useCallback, useReducer, useRef } from 'react'
import {
    ChevronDown,
    ChevronRight,
    Loader2,
    AlertCircle,
    BookOpen,
    Scale,
    GitBranch,
    FileCheck,
    Gauge,
    Copy,
    Check,
    RefreshCw,
} from 'lucide-react'
import { api } from '@/services/api'
import type {
    ResearchEvidenceBucket,
    ResearchEvidenceDataStatus,
} from '@/types'
import { sanitizeRelativePath } from '@/utils/knowledgeContract'
import {
    buildConsensusViewModel,
    buildCitationAuditViewModel,
    buildThesisTimelineViewModel,
    buildHalfYearFactsViewModel,
    buildScoreSnapshotViewModel,
    initialEvidenceCenterState,
    reduceEvidenceCenter,
    EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL,
    type EvidenceChip,
} from '@/utils/researchEvidenceCenter'

// ── Helpers ─────────────────────────────────────────────────────────────────

const DATA_STATUS_LABEL: Record<ResearchEvidenceDataStatus, string> = {
    fresh: '数据可用',
    stale: '数据过期',
    missing: '暂无数据',
    failed: '加载失败',
    skipped: '已跳过',
    conflict: '事实冲突',
}

const DATA_STATUS_CLASS: Record<ResearchEvidenceDataStatus, string> = {
    fresh: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300',
    stale: 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300',
    missing: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
    failed: 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300',
    skipped: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
    conflict: 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300',
}

function StatusBadge({ status }: { status: ResearchEvidenceDataStatus }) {
    return (
        <span className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium ${DATA_STATUS_CLASS[status]}`}>
            {DATA_STATUS_LABEL[status]}
        </span>
    )
}

function ChipRow({ chips }: { chips: EvidenceChip[] }) {
    if (chips.length === 0) return null
    const toneClass: Record<EvidenceChip['tone'], string> = {
        default: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
        positive: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300',
        warning: 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300',
        negative: 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300',
        info: 'bg-sky-50 text-sky-600 dark:bg-sky-900/30 dark:text-sky-300',
    }
    return (
        <div className="flex flex-wrap gap-2">
            {chips.map((c, i) => (
                <span key={i} className={`inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium ${toneClass[c.tone]}`}>
                    {c.label} {c.value}
                </span>
            ))}
        </div>
    )
}

function CopyablePath({ path }: { path: string }) {
    const [copied, setCopied] = useState(false)
    const handleCopy = useCallback(() => {
        navigator.clipboard.writeText(path).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
        }).catch(() => { /* ignore clipboard errors */ })
    }, [path])

    return (
        <button
            onClick={handleCopy}
            className="inline-flex items-center gap-1 max-w-full truncate rounded bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-500 hover:bg-slate-100 dark:bg-slate-800/60 dark:text-slate-400 dark:hover:bg-slate-700 transition-colors"
            title={path}
        >
            <code className="truncate">{path}</code>
            {copied ? <Check className="w-3 h-3 text-emerald-500 shrink-0" /> : <Copy className="w-3 h-3 shrink-0 opacity-50" />}
        </button>
    )
}

// ── Bucket Section Component ────────────────────────────────────────────────

interface BucketSectionProps {
    title: string
    icon: React.ReactNode
    bucket: ResearchEvidenceBucket
    expanded: boolean
    onToggle: () => void
    children: React.ReactNode
}

function BucketSection({ title, icon, bucket, expanded, onToggle, children }: BucketSectionProps) {
    const hasErrors = bucket.errors.length > 0
    return (
        <div className="border border-slate-200 dark:border-slate-700 rounded-xl overflow-hidden">
            <button
                onClick={onToggle}
                className="w-full flex items-center justify-between px-4 py-3 bg-slate-50/80 dark:bg-slate-800/40 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            >
                <div className="flex items-center gap-2">
                    {expanded ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                    {icon}
                    <span className="font-medium text-sm text-slate-900 dark:text-slate-100">{title}</span>
                    <span className="text-[11px] text-slate-400">{bucket.task}</span>
                </div>
                <div className="flex items-center gap-2">
                    {hasErrors && (
                        <AlertCircle className="w-3.5 h-3.5 text-amber-500" />
                    )}
                    <StatusBadge status={bucket.data_status} />
                </div>
            </button>
            {expanded && (
                <div className="px-4 py-3 bg-white dark:bg-slate-800/20 space-y-3">
                    {hasErrors && (
                        <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                            {bucket.errors.join('; ')}
                        </div>
                    )}
                    {children}
                </div>
            )}
        </div>
    )
}

function EmptyBucketHint() {
    return <p className="text-xs text-slate-400">该分组无结构化摘要数据。</p>
}

// ── Consensus Section ───────────────────────────────────────────────────────

function ConsensusDetail({ summary }: { summary: Record<string, unknown> }) {
    const vm = buildConsensusViewModel(summary)
    const hasAnything = vm.chips.length > 0 || vm.dimensions.length > 0 || vm.summaryText || vm.factCheckWarning
    return (
        <div className="space-y-2">
            {!hasAnything && <EmptyBucketHint />}
            <ChipRow chips={vm.chips} />
            {vm.factCheckWarning && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                    {vm.factCheckWarning}
                </div>
            )}
            {vm.dimensions.length > 0 && (
                <div className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1">
                    {vm.dimensions.map(d => (
                        <div key={d.key} className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-slate-700 dark:text-slate-300">{d.label}</span>
                            {d.dominantStance && <span className="text-slate-500 dark:text-slate-400">{d.dominantStance}</span>}
                            {d.bullishCount != null && <span className="text-emerald-600 dark:text-emerald-400">看多 {d.bullishCount}</span>}
                            {d.bearishCount != null && <span className="text-rose-600 dark:text-rose-400">看空 {d.bearishCount}</span>}
                            {d.disagreementScore != null && <span className="text-slate-400 ml-auto">{d.disagreementScore.toFixed(2)}</span>}
                        </div>
                    ))}
                </div>
            )}
            {vm.summaryText && (
                <p className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{vm.summaryText}</p>
            )}
        </div>
    )
}

// ── Citation Audit Section ──────────────────────────────────────────────────

function CitationAuditDetail({ summary }: { summary: Record<string, unknown> }) {
    const vm = buildCitationAuditViewModel(summary)
    const hasAnything = vm.chips.length > 0 || vm.statusLine || vm.auditLines.length > 0 || vm.needsReviewWarning
    return (
        <div className="space-y-2">
            {!hasAnything && <EmptyBucketHint />}
            <ChipRow chips={vm.chips} />
            {vm.statusLine && (
                <p className="text-[11px] text-slate-500 dark:text-slate-400">{vm.statusLine}</p>
            )}
            {vm.needsReviewWarning && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                    {vm.needsReviewWarning}
                </div>
            )}
            {vm.auditLines.length > 0 && (
                <div className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1">
                    {vm.auditLines.map((line, i) => (
                        <p key={i} className="text-slate-600 dark:text-slate-400 leading-relaxed">{line}</p>
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Thesis Timeline Section ─────────────────────────────────────────────────

function ThesisTimelineDetail({ summary }: { summary: Record<string, unknown> }) {
    const vm = buildThesisTimelineViewModel(summary)
    const hasAnything = vm.chips.length > 0 || vm.theses.length > 0 || vm.summaryText
    return (
        <div className="space-y-2">
            {!hasAnything && <EmptyBucketHint />}
            <ChipRow chips={vm.chips} />
            {vm.theses.length > 0 && (
                <div className="space-y-1.5">
                    {vm.theses.map((t, i) => (
                        <div key={t.key || i} className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1">
                            <div className="flex items-center gap-2 flex-wrap">
                                {t.themeLabel && <span className="font-medium text-slate-700 dark:text-slate-300">{t.themeLabel}</span>}
                                {t.baseDirection && <span className="text-slate-400">{t.baseDirection}</span>}
                                {t.currentDirection && t.currentDirection !== t.baseDirection && (
                                    <span className="text-slate-600 dark:text-slate-300">→ {t.currentDirection}</span>
                                )}
                                {t.effectiveVersions != null && <span className="text-slate-400">{t.effectiveVersions} 个有效版本</span>}
                                {t.latestReportDate && <span className="text-slate-400 ml-auto">{t.latestReportDate}</span>}
                            </div>
                            <div className="flex items-center gap-2 flex-wrap text-[11px]">
                                {t.reversedCount != null && t.reversedCount > 0 && <span className="text-rose-600 dark:text-rose-400">反转 {t.reversedCount}</span>}
                                {t.weakenedCount != null && t.weakenedCount > 0 && <span className="text-amber-600 dark:text-amber-400">减弱 {t.weakenedCount}</span>}
                                {t.reinforcedCount != null && t.reinforcedCount > 0 && <span className="text-emerald-600 dark:text-emerald-400">强化 {t.reinforcedCount}</span>}
                            </div>
                            {t.latestVersionPath && <CopyablePath path={sanitizeRelativePath(t.latestVersionPath)} />}
                        </div>
                    ))}
                </div>
            )}
            {vm.summaryText && (
                <p className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{vm.summaryText}</p>
            )}
        </div>
    )
}

// ── Half-Year Facts Section ─────────────────────────────────────────────────

function HalfYearFactsDetail({ bucket }: { bucket: ResearchEvidenceBucket }) {
    const vm = buildHalfYearFactsViewModel(bucket)
    const hasAnything = vm.latestPeriod || vm.latestDisclosure || vm.pages.length > 0 || vm.summaryLines.length > 0 || vm.risks.length > 0
    return (
        <div className="space-y-2">
            {!hasAnything && <EmptyBucketHint />}
            <div className="flex flex-wrap gap-2">
                {vm.latestPeriod && (
                    <span className="inline-flex items-center rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">
                        报告期 {vm.latestPeriod}
                    </span>
                )}
                {vm.latestDisclosure && (
                    <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                        披露日 {vm.latestDisclosure}
                    </span>
                )}
                {vm.pages.length > 0 && (
                    <span className="inline-flex items-center rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-300">
                        {vm.pages.length} 页
                    </span>
                )}
            </div>
            {vm.summaryLines.length > 0 && (
                <div className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1">
                    {vm.summaryLines.map((line, i) => (
                        <p key={i} className="text-slate-600 dark:text-slate-400 leading-relaxed">{line}</p>
                    ))}
                </div>
            )}
            {vm.risks.length > 0 && (
                <div className="rounded-lg border border-rose-200 bg-rose-50/60 px-3 py-2 text-xs space-y-1 dark:border-rose-500/20 dark:bg-rose-500/10">
                    {vm.risks.map((risk, i) => (
                        <p key={i} className="text-rose-600 dark:text-rose-300 leading-relaxed">{risk}</p>
                    ))}
                </div>
            )}
            {vm.pages.length > 0 && (
                <div className="space-y-1.5">
                    {vm.pages.map((page, i) => (
                        <div key={i} className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs">
                            <div className="flex items-center gap-2">
                                {page.title && <span className="font-medium text-slate-700 dark:text-slate-300">{page.title}</span>}
                                {page.financialPeriod && <span className="text-slate-400">{page.financialPeriod}</span>}
                                {page.dataStatus && (
                                    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                        page.dataStatus === 'conflict'
                                            ? 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300'
                                            : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
                                    }`}>
                                        {page.dataStatus}
                                    </span>
                                )}
                                {page.metricCount != null && page.metricCount > 0 && (
                                    <span className="text-slate-400 ml-auto">{page.metricCount} 指标</span>
                                )}
                            </div>
                            {page.metricKeys.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1">
                                    {page.metricKeys.slice(0, 8).map((k) => (
                                        <span key={k} className="inline-block rounded bg-slate-50 px-1 py-0.5 text-[10px] text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">{k}</span>
                                    ))}
                                </div>
                            )}
                            {page.relPath && <div className="mt-1"><CopyablePath path={sanitizeRelativePath(page.relPath)} /></div>}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Research Score Snapshot Section ──────────────────────────────────────────

function ScoreSnapshotDetail({ summary }: { summary: Record<string, unknown> }) {
    const vm = buildScoreSnapshotViewModel(summary)
    return (
        <div className="space-y-2">
            <ChipRow chips={vm.status ? [{ label: '状态', value: vm.status, tone: 'default' as const }] : []} />
            {!vm.hasSnapshot && (
                <p className="text-xs text-slate-400">暂无正式研究评分快照。</p>
            )}
            {vm.hasSnapshot && (
                <>
                    <ChipRow chips={vm.scoreChips} />
                    {vm.theses.length > 0 && (
                        <div className="space-y-1.5">
                            {vm.theses.map((t, i) => (
                                <div key={i} className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        {t.topic && <span className="font-medium text-slate-700 dark:text-slate-300">{t.topic}</span>}
                                        {t.direction && <span className="text-slate-500 dark:text-slate-400">{t.direction}</span>}
                                        {t.status && <span className="text-slate-400 ml-auto">{t.status}</span>}
                                    </div>
                                    {t.coreHypothesis && (
                                        <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{t.coreHypothesis}</p>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                    {vm.missingEvidence.length > 0 && (
                        <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs space-y-1 dark:border-amber-500/20 dark:bg-amber-500/10">
                            <span className="font-medium text-amber-700 dark:text-amber-200">缺失证据：</span>
                            {vm.missingEvidence.map((item, i) => (
                                <p key={i} className="text-amber-600 dark:text-amber-300">{item}</p>
                            ))}
                        </div>
                    )}
                    {vm.scoreChangeReasons.length > 0 && (
                        <div className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs text-slate-600 dark:text-slate-400">
                            <span className="font-medium">评分变化：</span>
                            {vm.scoreChangeReasons.join('；')}
                        </div>
                    )}
                    {vm.warnings.length > 0 && (
                        <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                            {vm.warnings.join('；')}
                        </div>
                    )}
                </>
            )}
        </div>
    )
}

// ── Main Component ──────────────────────────────────────────────────────────

interface ResearchEvidenceCenterProps {
    symbol: string
    className?: string
}

export default function ResearchEvidenceCenter({ symbol, className }: ResearchEvidenceCenterProps) {
    const [expanded, setExpanded] = useState(false)
    const [state, dispatch] = useReducer(
        reduceEvidenceCenter,
        symbol,
        initialEvidenceCenterState,
    )
    const [expandedBuckets, setExpandedBuckets] = useState<Set<string>>(new Set())

    // [UI-014-R1-fix] 请求生命周期与 loading 状态解耦：
    // fetch effect 的依赖不含 phase/attempts（它们由请求自身改变），
    // 重触发只经由 expanded / fetchEpoch / symbol。在飞请求用 ticket ref
    // 标记，cleanup 只取消"当前这一发"，迟到响应按归属丢弃。
    const [fetchEpoch, setFetchEpoch] = useState(0)
    const stateRef = useRef(state)
    stateRef.current = state
    const inflightRef = useRef<{ symbol: string; cancelled: boolean } | null>(null)

    // symbol 变化：清空旧数据并提升 epoch 触发新请求
    useEffect(() => {
        dispatch({ type: 'symbol_changed', symbol })
        setFetchEpoch((e) => e + 1)
    }, [symbol])

    useEffect(() => {
        if (!expanded) return
        // 在飞请求尚未返回：不重复发起（StrictMode 双调用自愈）
        if (inflightRef.current) return
        const current = stateRef.current
        if (current.phase !== 'idle') return
        if (current.symbol !== symbol) return
        const ticket = { symbol, cancelled: false }
        inflightRef.current = ticket
        dispatch({ type: 'load_started' })
        api.getResearchEvidence(symbol)
            .then((res) => {
                if (ticket.cancelled || inflightRef.current !== ticket) return
                inflightRef.current = null
                dispatch({ type: 'load_succeeded', symbol, response: res })
            })
            .catch((err) => {
                if (ticket.cancelled || inflightRef.current !== ticket) return
                inflightRef.current = null
                dispatch({
                    type: 'load_failed',
                    symbol,
                    message: err instanceof Error ? err.message : '加载研报证据失败',
                })
            })
        return () => {
            // 仅当被取消的是当前在飞请求（重触发/卸载/折叠）时回收
            if (inflightRef.current === ticket) {
                ticket.cancelled = true
                inflightRef.current = null
                dispatch({ type: 'load_cancelled' })
            }
        }
    }, [expanded, fetchEpoch, symbol])

    // [UI-014-R1-fix] 真实卸载安全由在飞 ticket 的 cleanup 取消保证；
    // 不派发 unmounted 冻结——StrictMode 的"模拟卸载"清理无法与真实卸载
    // 区分，冻结会让组件在开发模式下永久停机（React 18 对已卸载组件的
    // dispatch 是安全的 no-op）。

    const toggleBucket = useCallback((bucketId: string) => {
        setExpandedBuckets((prev) => {
            const next = new Set(prev)
            if (next.has(bucketId)) next.delete(bucketId)
            else next.add(bucketId)
            return next
        })
    }, [])

    // Compute overall evidence status for the header badge
    const overallStatus = state.data?.data_status ?? null
    const hasGaps = state.data && state.data.gaps.length > 0
    const hasConflicts = state.data && (
        state.data.half_year_facts.data_status === 'conflict' ||
        state.data.gaps.some((g) => g.includes('conflict'))
    )
    const retryExhausted = state.attempts >= EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL

    // Auto-expand first bucket with data
    useEffect(() => {
        if (!state.data) return
        const firstWithData = ['consensus', 'citation_audit', 'thesis_timeline', 'half_year_facts', 'research_score_snapshot']
            .find((k) => {
                const bucket = state.data![k as keyof NonNullable<typeof state.data>] as ResearchEvidenceBucket | undefined
                return bucket?.has_hit
            })
        if (firstWithData) setExpandedBuckets(new Set([firstWithData]))
    }, [state.data])

    return (
        <div className={`border border-teal-200/70 dark:border-teal-500/20 rounded-2xl overflow-hidden bg-teal-50/20 dark:bg-teal-900/10 ${className ?? ''}`}>
            {/* Header — always visible */}
            <button
                onClick={() => setExpanded(!expanded)}
                className="w-full flex items-center justify-between px-4 py-3 bg-teal-50/60 dark:bg-teal-900/15 hover:bg-teal-50 dark:hover:bg-teal-900/25 transition-colors"
            >
                <div className="flex items-center gap-2">
                    {expanded ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                    <BookOpen className="w-4 h-4 text-teal-500" />
                    <span className="font-medium text-sm text-slate-900 dark:text-slate-100">研报证据中心</span>
                    <span className="text-xs text-slate-500 dark:text-slate-400">同股研报共识 · 分歧 · 来源下钻</span>
                </div>
                <div className="flex items-center gap-2">
                    {overallStatus && <StatusBadge status={overallStatus} />}
                    {hasConflicts && (
                        <span className="inline-flex items-center rounded bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600 dark:bg-rose-900/30 dark:text-rose-300">
                            存在冲突
                        </span>
                    )}
                    {hasGaps && !hasConflicts && (
                        <span className="inline-flex items-center rounded bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:bg-amber-900/30 dark:text-amber-300">
                            有缺口
                        </span>
                    )}
                </div>
            </button>

            {/* Body — lazy-loaded */}
            {expanded && (
                <div className="px-4 py-3 bg-white dark:bg-slate-800/20 space-y-3">
                    {/* Loading state */}
                    {state.phase === 'loading' && (
                        <div className="flex items-center justify-center py-8 gap-2 text-slate-400">
                            <Loader2 className="w-4 h-4 animate-spin" />
                            <span className="text-sm">加载研报证据...</span>
                        </div>
                    )}

                    {/* [UI-014-R1] Error state — 稳定展示，仅手动重试且受上限约束 */}
                    {state.phase === 'error' && (
                        <div className="flex flex-col items-center justify-center py-8 gap-2 text-rose-500">
                            <div className="flex items-center gap-2">
                                <AlertCircle className="w-4 h-4" />
                                <span className="text-sm">{state.error ?? '加载研报证据失败'}</span>
                            </div>
                            {!retryExhausted ? (
                                <button
                                    onClick={() => {
                                        dispatch({ type: 'retry_clicked' })
                                        setFetchEpoch((e) => e + 1)
                                    }}
                                    className="inline-flex items-center gap-1 rounded border border-rose-200 px-3 py-1 text-xs font-medium text-rose-600 hover:bg-rose-50 dark:border-rose-500/30 dark:hover:bg-rose-500/10 transition-colors"
                                >
                                    <RefreshCw className="w-3 h-3" />
                                    重新加载
                                </button>
                            ) : (
                                <span className="text-[11px] text-slate-400">已达到重试上限（{EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL} 次），请稍后再试</span>
                            )}
                        </div>
                    )}

                    {/* Empty state */}
                    {state.data && state.phase === 'loaded' && state.data.data_status === 'missing' && (
                        <div className="flex items-center justify-center py-8 gap-2 text-slate-400">
                            <BookOpen className="w-4 h-4" />
                            <span className="text-sm">暂无该标的的研报证据数据</span>
                        </div>
                    )}

                    {/* Content */}
                    {state.data && state.phase === 'loaded' && state.data.data_status !== 'missing' && (
                        <>
                            {/* Gaps summary */}
                            {state.data.gaps.length > 0 && (
                                <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                                    <span className="font-medium">缺口：</span>
                                    {state.data.gaps.join(' · ')}
                                </div>
                            )}

                            {/* Errors summary */}
                            {state.data.errors.length > 0 && (
                                <div className="rounded-lg border border-rose-200 bg-rose-50/60 px-3 py-2 text-xs text-rose-600 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-200">
                                    {state.data.errors.join(' · ')}
                                </div>
                            )}

                            {/* 5 Bucket Sections */}
                            <BucketSection
                                title="研报共识与分歧"
                                icon={<Scale className="w-4 h-4 text-blue-500" />}
                                bucket={state.data.consensus}
                                expanded={expandedBuckets.has('consensus')}
                                onToggle={() => toggleBucket('consensus')}
                            >
                                <ConsensusDetail summary={state.data.consensus.summary} />
                            </BucketSection>

                            <BucketSection
                                title="观点 vs 事实审计"
                                icon={<FileCheck className="w-4 h-4 text-emerald-500" />}
                                bucket={state.data.citation_audit}
                                expanded={expandedBuckets.has('citation_audit')}
                                onToggle={() => toggleBucket('citation_audit')}
                            >
                                <CitationAuditDetail summary={state.data.citation_audit.summary} />
                            </BucketSection>

                            <BucketSection
                                title="观点演化时间线"
                                icon={<GitBranch className="w-4 h-4 text-violet-500" />}
                                bucket={state.data.thesis_timeline}
                                expanded={expandedBuckets.has('thesis_timeline')}
                                onToggle={() => toggleBucket('thesis_timeline')}
                            >
                                <ThesisTimelineDetail summary={state.data.thesis_timeline.summary} />
                            </BucketSection>

                            <BucketSection
                                title="半年报事实"
                                icon={<BookOpen className="w-4 h-4 text-amber-500" />}
                                bucket={state.data.half_year_facts}
                                expanded={expandedBuckets.has('half_year_facts')}
                                onToggle={() => toggleBucket('half_year_facts')}
                            >
                                <HalfYearFactsDetail bucket={state.data.half_year_facts} />
                            </BucketSection>

                            <BucketSection
                                title="研究评分快照"
                                icon={<Gauge className="w-4 h-4 text-indigo-500" />}
                                bucket={state.data.research_score_snapshot}
                                expanded={expandedBuckets.has('research_score_snapshot')}
                                onToggle={() => toggleBucket('research_score_snapshot')}
                            >
                                <ScoreSnapshotDetail summary={state.data.research_score_snapshot.summary} />
                            </BucketSection>

                            {/* Source freshness digest */}
                            <div className="pt-2 border-t border-slate-100 dark:border-slate-700">
                                <p className="text-[11px] text-slate-400 dark:text-slate-500">
                                    数据来源: {state.data.vendor} · 查询窗口 {state.data.query.window_months} 个月 · 更新于 {state.data.as_of}
                                </p>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    )
}
