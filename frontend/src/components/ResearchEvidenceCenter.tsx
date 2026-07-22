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

import { useState, useEffect, useCallback } from 'react'
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
} from 'lucide-react'
import { api } from '@/services/api'
import type {
    ResearchEvidenceResponse,
    ResearchEvidenceBucket,
    ResearchEvidenceDataStatus,
} from '@/types'
import { sanitizeRelativePath } from '@/utils/knowledgeContract'

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

// ── Consensus Section ───────────────────────────────────────────────────────

function ConsensusDetail({ summary }: { summary: Record<string, unknown> }) {
    const reports = Array.isArray(summary.reports) ? summary.reports as Array<Record<string, unknown>> : []
    const effectiveCount = typeof summary.effective_report_count === 'number' ? summary.effective_report_count : null
    const dedupCount = typeof summary.institution_dedup_count === 'number' ? summary.institution_dedup_count : null
    const consensusDir = typeof summary.consensus_direction === 'string' ? summary.consensus_direction : null
    const disagreeCount = typeof summary.disagreement_count === 'number' ? summary.disagreement_count : null
    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
                {effectiveCount != null && (
                    <span className="inline-flex items-center rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-300">
                        有效研报 {effectiveCount} 篇
                    </span>
                )}
                {dedupCount != null && (
                    <span className="inline-flex items-center rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-300">
                        机构去重 {dedupCount} 家
                    </span>
                )}
                {consensusDir && (
                    <span className="inline-flex items-center rounded bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-600 dark:bg-sky-900/30 dark:text-sky-300">
                        共识方向 {consensusDir}
                    </span>
                )}
                {disagreeCount != null && disagreeCount > 0 && (
                    <span className="inline-flex items-center rounded bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600 dark:bg-rose-900/30 dark:text-rose-300">
                        分歧 {disagreeCount} 项
                    </span>
                )}
            </div>
            {reports.length > 0 && (
                <div className="space-y-1.5">
                    {reports.map((r, i) => (
                        <div key={i} className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1">
                            <div className="flex items-center gap-2">
                                {typeof r.institution === 'string' && <span className="font-medium text-slate-700 dark:text-slate-300">{r.institution}</span>}
                                {typeof r.direction === 'string' && <span className="text-slate-500 dark:text-slate-400">{r.direction}</span>}
                                {typeof r.date === 'string' && <span className="text-slate-400 ml-auto">{r.date}</span>}
                            </div>
                            {typeof r.summary === 'string' && <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{r.summary.slice(0, 200)}</p>}
                            {typeof r.source_path === 'string' && <CopyablePath path={sanitizeRelativePath(r.source_path)} />}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Citation Audit Section ──────────────────────────────────────────────────

const CITATION_STATUS_CLASS: Record<string, string> = {
    supported: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300',
    contradicted: 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300',
    pending: 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300',
}

function CitationAuditDetail({ summary }: { summary: Record<string, unknown> }) {
    const items = Array.isArray(summary.items) ? summary.items as Array<Record<string, unknown>> : []
    const totalClaims = typeof summary.total_claims === 'number' ? summary.total_claims : null
    const supportedCount = typeof summary.supported_count === 'number' ? summary.supported_count : null
    const contradictedCount = typeof summary.contradicted_count === 'number' ? summary.contradicted_count : null
    const pendingCount = typeof summary.pending_count === 'number' ? summary.pending_count : null
    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
                {totalClaims != null && (
                    <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        总声明 {totalClaims} 项
                    </span>
                )}
                {supportedCount != null && supportedCount > 0 && (
                    <span className="inline-flex items-center rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">
                        已证实 {supportedCount}
                    </span>
                )}
                {contradictedCount != null && contradictedCount > 0 && (
                    <span className="inline-flex items-center rounded bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600 dark:bg-rose-900/30 dark:text-rose-300">
                        有矛盾 {contradictedCount}
                    </span>
                )}
                {pendingCount != null && pendingCount > 0 && (
                    <span className="inline-flex items-center rounded bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:bg-amber-900/30 dark:text-amber-300">
                        待验证 {pendingCount}
                    </span>
                )}
            </div>
            {items.length > 0 && (
                <div className="space-y-1.5">
                    {items.map((item, i) => (
                        <div key={i} className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1">
                            <div className="flex items-center gap-2">
                                {typeof item.claim === 'string' && <span className="font-medium text-slate-700 dark:text-slate-300">{item.claim.slice(0, 100)}</span>}
                                {typeof item.status === 'string' && (
                                    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${CITATION_STATUS_CLASS[item.status] || 'bg-slate-100 text-slate-500'}`}>
                                        {item.status}
                                    </span>
                                )}
                            </div>
                            {typeof item.detail === 'string' && <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{item.detail.slice(0, 200)}</p>}
                            {typeof item.source_path === 'string' && <CopyablePath path={sanitizeRelativePath(item.source_path)} />}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Thesis Timeline Section ─────────────────────────────────────────────────

function ThesisTimelineDetail({ summary }: { summary: Record<string, unknown> }) {
    const timeline = Array.isArray(summary.timeline) ? summary.timeline as Array<Record<string, unknown>> : []
    const totalVersions = typeof summary.total_versions === 'number' ? summary.total_versions : null
    const driftDirection = typeof summary.drift_direction === 'string' ? summary.drift_direction : null
    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
                {totalVersions != null && (
                    <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        共 {totalVersions} 个版本
                    </span>
                )}
                {driftDirection && (
                    <span className="inline-flex items-center rounded bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-600 dark:bg-sky-900/30 dark:text-sky-300">
                        漂移方向 {driftDirection}
                    </span>
                )}
            </div>
            {timeline.length > 0 && (
                <div className="relative pl-4 space-y-2">
                    <div className="absolute left-[7px] top-2 bottom-2 w-px bg-slate-200 dark:bg-slate-700" />
                    {timeline.map((entry, i) => (
                        <div key={i} className="relative flex gap-3">
                            <div className="absolute left-[-12px] top-1.5 w-2.5 h-2.5 rounded-full bg-white dark:bg-slate-800 border-2 border-sky-400" />
                            <div className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1 flex-1">
                                <div className="flex items-center gap-2">
                                    {typeof entry.date === 'string' && <span className="text-slate-400">{entry.date}</span>}
                                    {typeof entry.direction === 'string' && <span className="text-slate-500 dark:text-slate-400">{entry.direction}</span>}
                                    {typeof entry.status === 'string' && (
                                        <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                                            {entry.status}
                                        </span>
                                    )}
                                </div>
                                {typeof entry.thesis === 'string' && <p className="text-slate-600 dark:text-slate-400 leading-relaxed">{entry.thesis.slice(0, 200)}</p>}
                                {typeof entry.source_path === 'string' && <CopyablePath path={sanitizeRelativePath(entry.source_path)} />}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Half-Year Facts Section ─────────────────────────────────────────────────

function HalfYearFactsDetail({ summary }: { summary: Record<string, unknown> }) {
    const pages = Array.isArray(summary.pages) ? summary.pages as Array<Record<string, unknown>> : []
    const summaryLines = Array.isArray(summary.summary_lines) ? summary.summary_lines as string[] : []
    const risks = Array.isArray(summary.risks) ? summary.risks as string[] : []
    const latestPeriod = typeof summary.latest_period === 'string' ? summary.latest_period : null
    const latestDisclosure = typeof summary.latest_disclosure_date === 'string' ? summary.latest_disclosure_date : null
    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
                {latestPeriod && (
                    <span className="inline-flex items-center rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">
                        报告期 {latestPeriod}
                    </span>
                )}
                {latestDisclosure && (
                    <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                        披露日 {latestDisclosure}
                    </span>
                )}
                {pages.length > 0 && (
                    <span className="inline-flex items-center rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-900/30 dark:text-blue-300">
                        {pages.length} 页
                    </span>
                )}
            </div>
            {summaryLines.length > 0 && (
                <div className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs space-y-1">
                    {summaryLines.map((line, i) => (
                        <p key={i} className="text-slate-600 dark:text-slate-400 leading-relaxed">{line}</p>
                    ))}
                </div>
            )}
            {risks.length > 0 && (
                <div className="rounded-lg border border-rose-200 bg-rose-50/60 px-3 py-2 text-xs space-y-1 dark:border-rose-500/20 dark:bg-rose-500/10">
                    {risks.map((risk, i) => (
                        <p key={i} className="text-rose-600 dark:text-rose-300 leading-relaxed">{risk}</p>
                    ))}
                </div>
            )}
            {pages.length > 0 && (
                <div className="space-y-1.5">
                    {pages.map((page, i) => (
                        <div key={i} className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs">
                            <div className="flex items-center gap-2">
                                {typeof page.title === 'string' && <span className="font-medium text-slate-700 dark:text-slate-300">{page.title}</span>}
                                {typeof page.financial_period === 'string' && <span className="text-slate-400">{page.financial_period}</span>}
                                {typeof page.data_status === 'string' && (
                                    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                        page.data_status === 'conflict'
                                            ? 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300'
                                            : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
                                    }`}>
                                        {page.data_status}
                                    </span>
                                )}
                                {typeof page.metric_count === 'number' && page.metric_count > 0 && (
                                    <span className="text-slate-400 ml-auto">{page.metric_count} 指标</span>
                                )}
                            </div>
                            {Array.isArray(page.metric_keys) && page.metric_keys.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1">
                                    {(page.metric_keys as string[]).slice(0, 8).map((k) => (
                                        <span key={k} className="inline-block rounded bg-slate-50 px-1 py-0.5 text-[10px] text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">{k}</span>
                                    ))}
                                </div>
                            )}
                            {typeof page.rel_path === 'string' && <div className="mt-1"><CopyablePath path={sanitizeRelativePath(page.rel_path)} /></div>}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

// ── Research Score Snapshot Section ──────────────────────────────────────────

function ScoreSnapshotDetail({ summary }: { summary: Record<string, unknown> }) {
    const status = typeof summary.status === 'string' ? summary.status : null
    const thesesSummary = typeof summary.theses_summary === 'string' ? summary.theses_summary : null
    const missingEvidence = Array.isArray(summary.missing_evidence) ? summary.missing_evidence as string[] : []
    const scores = summary.scores && typeof summary.scores === 'object' ? summary.scores as Record<string, unknown> : {}
    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
                {status && (
                    <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        状态 {status}
                    </span>
                )}
            </div>
            {thesesSummary && (
                <div className="rounded-lg border border-slate-100 dark:border-slate-700 px-3 py-2 text-xs text-slate-600 dark:text-slate-400 leading-relaxed">
                    {thesesSummary}
                </div>
            )}
            {missingEvidence.length > 0 && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs space-y-1 dark:border-amber-500/20 dark:bg-amber-500/10">
                    <span className="font-medium text-amber-700 dark:text-amber-200">缺失证据：</span>
                    {missingEvidence.map((item, i) => (
                        <p key={i} className="text-amber-600 dark:text-amber-300">{item}</p>
                    ))}
                </div>
            )}
            {Object.keys(scores).length > 0 && (
                <div className="flex flex-wrap gap-2">
                    {Object.entries(scores).map(([k, v]) => (
                        <span key={k} className="inline-flex items-center rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-300">
                            {k}: {typeof v === 'number' ? v.toFixed(1) : String(v)}
                        </span>
                    ))}
                </div>
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
    const [loading, setLoading] = useState(false)
    const [data, setData] = useState<ResearchEvidenceResponse | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [expandedBuckets, setExpandedBuckets] = useState<Set<string>>(new Set())

    // Lazy-load on first expand
    useEffect(() => {
        if (!expanded || data || loading) return
        let cancelled = false
        setLoading(true)
        setError(null)
        api.getResearchEvidence(symbol)
            .then((res) => {
                if (cancelled) return
                setData(res)
                // Auto-expand first bucket with data
                const firstWithData = ['consensus', 'citation_audit', 'thesis_timeline', 'half_year_facts', 'research_score_snapshot']
                    .find((k) => {
                        const bucket = res[k as keyof ResearchEvidenceResponse] as ResearchEvidenceBucket | undefined
                        return bucket?.has_hit
                    })
                if (firstWithData) setExpandedBuckets(new Set([firstWithData]))
            })
            .catch((err) => {
                if (cancelled) return
                setError(err instanceof Error ? err.message : '加载研报证据失败')
            })
            .finally(() => {
                if (!cancelled) setLoading(false)
            })
        return () => { cancelled = true }
    }, [expanded, data, loading, symbol])

    const toggleBucket = useCallback((bucketId: string) => {
        setExpandedBuckets((prev) => {
            const next = new Set(prev)
            if (next.has(bucketId)) next.delete(bucketId)
            else next.add(bucketId)
            return next
        })
    }, [])

    // Compute overall evidence status for the header badge
    const overallStatus = data?.data_status ?? null
    const hasGaps = data && data.gaps.length > 0
    const hasConflicts = data && (
        data.half_year_facts.data_status === 'conflict' ||
        data.gaps.some((g) => g.includes('conflict'))
    )

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
                    {loading && (
                        <div className="flex items-center justify-center py-8 gap-2 text-slate-400">
                            <Loader2 className="w-4 h-4 animate-spin" />
                            <span className="text-sm">加载研报证据...</span>
                        </div>
                    )}

                    {/* Error state */}
                    {error && !loading && (
                        <div className="flex items-center justify-center py-8 gap-2 text-rose-500">
                            <AlertCircle className="w-4 h-4" />
                            <span className="text-sm">{error}</span>
                        </div>
                    )}

                    {/* Empty state */}
                    {data && !loading && data.data_status === 'missing' && (
                        <div className="flex items-center justify-center py-8 gap-2 text-slate-400">
                            <BookOpen className="w-4 h-4" />
                            <span className="text-sm">暂无该标的的研报证据数据</span>
                        </div>
                    )}

                    {/* Content */}
                    {data && !loading && data.data_status !== 'missing' && (
                        <>
                            {/* Gaps summary */}
                            {data.gaps.length > 0 && (
                                <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                                    <span className="font-medium">缺口：</span>
                                    {data.gaps.join(' · ')}
                                </div>
                            )}

                            {/* Errors summary */}
                            {data.errors.length > 0 && (
                                <div className="rounded-lg border border-rose-200 bg-rose-50/60 px-3 py-2 text-xs text-rose-600 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-200">
                                    {data.errors.join(' · ')}
                                </div>
                            )}

                            {/* 5 Bucket Sections */}
                            <BucketSection
                                title="研报共识与分歧"
                                icon={<Scale className="w-4 h-4 text-blue-500" />}
                                bucket={data.consensus}
                                expanded={expandedBuckets.has('consensus')}
                                onToggle={() => toggleBucket('consensus')}
                            >
                                <ConsensusDetail summary={data.consensus.summary} />
                            </BucketSection>

                            <BucketSection
                                title="观点 vs 事实审计"
                                icon={<FileCheck className="w-4 h-4 text-emerald-500" />}
                                bucket={data.citation_audit}
                                expanded={expandedBuckets.has('citation_audit')}
                                onToggle={() => toggleBucket('citation_audit')}
                            >
                                <CitationAuditDetail summary={data.citation_audit.summary} />
                            </BucketSection>

                            <BucketSection
                                title="观点演化时间线"
                                icon={<GitBranch className="w-4 h-4 text-violet-500" />}
                                bucket={data.thesis_timeline}
                                expanded={expandedBuckets.has('thesis_timeline')}
                                onToggle={() => toggleBucket('thesis_timeline')}
                            >
                                <ThesisTimelineDetail summary={data.thesis_timeline.summary} />
                            </BucketSection>

                            <BucketSection
                                title="半年报事实"
                                icon={<BookOpen className="w-4 h-4 text-amber-500" />}
                                bucket={data.half_year_facts}
                                expanded={expandedBuckets.has('half_year_facts')}
                                onToggle={() => toggleBucket('half_year_facts')}
                            >
                                <HalfYearFactsDetail summary={data.half_year_facts.summary} />
                            </BucketSection>

                            <BucketSection
                                title="研究评分快照"
                                icon={<Gauge className="w-4 h-4 text-indigo-500" />}
                                bucket={data.research_score_snapshot}
                                expanded={expandedBuckets.has('research_score_snapshot')}
                                onToggle={() => toggleBucket('research_score_snapshot')}
                            >
                                <ScoreSnapshotDetail summary={data.research_score_snapshot.summary} />
                            </BucketSection>

                            {/* Source freshness digest */}
                            <div className="pt-2 border-t border-slate-100 dark:border-slate-700">
                                <p className="text-[11px] text-slate-400 dark:text-slate-500">
                                    数据来源: {data.vendor} · 查询窗口 {data.query.window_months} 个月 · 更新于 {data.as_of}
                                </p>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    )
}
