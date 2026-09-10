// [UI-014-R1] research_evidence_center — KB-020 真实 schema 视图模型 + 请求状态机
//
// 本模块是纯函数层，供 ResearchEvidenceCenter 组件消费：
//  1. 视图模型构建器：输入对齐 api/services/research_evidence_service.py
//     及其上游真实产出的 summary dict（matrix_to_ta_consumable_summary /
//     audit_to_ta_consumable_summary / timeline_to_ta_consumable_summary /
//     snapshot_to_api_dict），输出可直接渲染的 chips/列表/警告。
//     缺字段一律缺失展示，不得编造数值。
//  2. 请求状态机：显式 phase（idle/loading/loaded/error）。
//     - symbol 变化即清空旧数据（旧 symbol 的响应按归属丢弃）；
//     - 失败进入稳定 error 展示，绝不自动重试；
//     - 手动重试受每 symbol 总尝试次数上限约束；
//     - 卸载（unmount）后到达的响应一律丢弃。

import type {
    ResearchEvidenceBucket,
    ResearchEvidenceResponse,
} from '@/types'

// ── 通用工具 ────────────────────────────────────────────────────────────────

export interface EvidenceChip {
    label: string
    value: string
    tone: 'default' | 'positive' | 'warning' | 'negative' | 'info'
}

function readNumber(v: unknown): number | null {
    return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function readString(v: unknown): string | null {
    return typeof v === 'string' && v.trim() !== '' ? v : null
}

function readBool(v: unknown): boolean | null {
    return typeof v === 'boolean' ? v : null
}

function readStringArray(v: unknown): string[] {
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

function formatNum(n: number): string {
    return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

function chip(label: string, value: string, tone: EvidenceChip['tone'] = 'default'): EvidenceChip {
    return { label, value, tone }
}

// ── Consensus（KB-016 真实 schema）──────────────────────────────────────────

const DIMENSION_LABELS: Record<string, string> = {
    performance: '业绩预测',
    supply_chain_role: '产业链角色',
    risk: '风险判断',
    valuation: '估值假设',
}

export interface ConsensusDimensionRow {
    key: string
    label: string
    dominantStance: string | null
    bullishCount: number | null
    bearishCount: number | null
    disagreementScore: number | null
}

export interface ConsensusViewModel {
    chips: EvidenceChip[]
    dimensions: ConsensusDimensionRow[]
    summaryText: string | null
    factCheckWarning: string | null
}

export function buildConsensusViewModel(summary: Record<string, unknown>): ConsensusViewModel {
    const chips: EvidenceChip[] = []
    const consensusScore = readNumber(summary.consensus_score)
    if (consensusScore != null) chips.push(chip('共识分', formatNum(consensusScore), 'info'))
    const disagreementScore = readNumber(summary.disagreement_score)
    if (disagreementScore != null) {
        chips.push(chip('分歧分', formatNum(disagreementScore), disagreementScore > 0.5 ? 'warning' : 'default'))
    }
    const attention = readNumber(summary.attention_count_effective)
    if (attention != null) chips.push(chip('有效关注', formatNum(attention)))
    const dominant = readString(summary.dominant_stance)
    if (dominant) chips.push(chip('主导立场', dominant))

    const dimensions: ConsensusDimensionRow[] = []
    const brief = summary.dimensions_brief
    if (brief && typeof brief === 'object') {
        for (const [key, raw] of Object.entries(brief as Record<string, unknown>)) {
            if (!raw || typeof raw !== 'object') continue
            const d = raw as Record<string, unknown>
            dimensions.push({
                key,
                label: DIMENSION_LABELS[key] ?? key,
                dominantStance: readString(d.dominant_stance),
                bullishCount: readNumber(d.bullish_count),
                bearishCount: readNumber(d.bearish_count),
                disagreementScore: readNumber(d.disagreement_score),
            })
        }
    }

    const needsFactCheck = readBool(summary.needs_fact_check) === true
    let factCheckWarning: string | null = null
    if (needsFactCheck) {
        const priority = readString(summary.fact_check_priority) ?? 'low'
        const reasons = readStringArray(summary.fact_check_reasons)
        factCheckWarning = `需事实核查（优先级 ${priority}）${reasons.length > 0 ? '：' + reasons.join('；') : ''}`
    }

    return {
        chips,
        dimensions,
        summaryText: readString(summary.consensus_summary),
        factCheckWarning,
    }
}

// ── Citation Audit（KB-017 真实 schema）─────────────────────────────────────

const AUDIT_COUNT_LABELS: Array<{ key: string; label: string; tone: EvidenceChip['tone'] }> = [
    { key: 'supported', label: '已证实', tone: 'positive' },
    { key: 'weakened', label: '被削弱', tone: 'warning' },
    { key: 'contradicted', label: '有矛盾', tone: 'negative' },
    { key: 'pending', label: '待验证', tone: 'warning' },
    { key: 'insufficient_data', label: '证据不足', tone: 'default' },
]

export interface CitationAuditViewModel {
    chips: EvidenceChip[]
    statusLine: string | null
    auditLines: string[]
    needsReviewWarning: string | null
}

export function buildCitationAuditViewModel(summary: Record<string, unknown>): CitationAuditViewModel {
    const chips: EvidenceChip[] = []
    const totalClaims = readNumber(summary.total_claim_count)
    if (totalClaims != null) chips.push(chip('总声明', formatNum(totalClaims)))
    const checked = readNumber(summary.checked_claim_count)
    if (checked != null) chips.push(chip('已核查', formatNum(checked)))

    const counts = summary.counts
    if (counts && typeof counts === 'object') {
        for (const { key, label, tone } of AUDIT_COUNT_LABELS) {
            const v = readNumber((counts as Record<string, unknown>)[key])
            if (v != null && v > 0) chips.push(chip(label, formatNum(v), tone))
        }
    }
    const blocked = readNumber(summary.weak_source_override_blocked_count)
    if (blocked != null && blocked > 0) chips.push(chip('弱源拦截', formatNum(blocked), 'warning'))

    const status = readString(summary.citation_audit_status)
    const factPeriod = readString(summary.fact_period)
    const factTier = readString(summary.fact_source_tier)
    const statusParts: string[] = []
    if (status) statusParts.push(`审计状态 ${status}`)
    if (factPeriod) statusParts.push(`事实期 ${factPeriod}`)
    if (factTier) statusParts.push(`事实源等级 ${factTier}`)

    const needsReview = readBool(summary.needs_tree_work_review) === true

    return {
        chips,
        statusLine: statusParts.length > 0 ? statusParts.join(' · ') : null,
        auditLines: readStringArray(summary.audit_summary),
        needsReviewWarning: needsReview ? '审计结论需 Tree Work 人工复核' : null,
    }
}

// ── Thesis Timeline（KB-018 真实 schema）────────────────────────────────────

export interface ThesisBriefRow {
    key: string
    themeLabel: string | null
    baseDirection: string | null
    currentDirection: string | null
    effectiveVersions: number | null
    reversedCount: number | null
    weakenedCount: number | null
    reinforcedCount: number | null
    latestReportDate: string | null
    latestVersionPath: string | null
}

export interface ThesisTimelineViewModel {
    chips: EvidenceChip[]
    theses: ThesisBriefRow[]
    summaryText: string | null
}

export function buildThesisTimelineViewModel(summary: Record<string, unknown>): ThesisTimelineViewModel {
    const chips: EvidenceChip[] = []
    const total = readNumber(summary.total_versions)
    if (total != null) chips.push(chip('总版本', formatNum(total)))
    const effective = readNumber(summary.effective_versions)
    if (effective != null) chips.push(chip('有效版本', formatNum(effective)))
    const reversed = readNumber(summary.reversed_versions)
    if (reversed != null && reversed > 0) chips.push(chip('反转', formatNum(reversed), 'negative'))
    const weakened = readNumber(summary.weakened_versions)
    if (weakened != null && weakened > 0) chips.push(chip('减弱', formatNum(weakened), 'warning'))
    const reinforced = readNumber(summary.reinforced_versions)
    if (reinforced != null && reinforced > 0) chips.push(chip('强化', formatNum(reinforced), 'positive'))
    const drift = readNumber(summary.consensus_drift_score)
    if (drift != null) chips.push(chip('共识漂移', formatNum(drift)))

    const theses: ThesisBriefRow[] = []
    const briefs = summary.theses_brief
    if (Array.isArray(briefs)) {
        for (const raw of briefs) {
            if (!raw || typeof raw !== 'object') continue
            const t = raw as Record<string, unknown>
            theses.push({
                key: readString(t.thesis_key) ?? '',
                themeLabel: readString(t.theme_label),
                baseDirection: readString(t.base_direction),
                currentDirection: readString(t.current_direction),
                effectiveVersions: readNumber(t.version_count_effective),
                reversedCount: readNumber(t.reversed_count),
                weakenedCount: readNumber(t.weakened_count),
                reinforcedCount: readNumber(t.reinforced_count),
                latestReportDate: readString(t.latest_report_date),
                latestVersionPath: readString(t.latest_version_path),
            })
        }
    }

    return { chips, theses, summaryText: readString(summary.timeline_summary) }
}

// ── Half-Year Facts（HY-003 真实 schema；latest_* 在 bucket 层）─────────────

export interface HalfYearFactsViewModel {
    latestPeriod: string | null
    latestDisclosure: string | null
    pages: Array<{
        relPath: string | null
        title: string | null
        financialPeriod: string | null
        disclosureDate: string | null
        dataStatus: string | null
        metricCount: number | null
        metricKeys: string[]
    }>
    summaryLines: string[]
    risks: string[]
}

export function buildHalfYearFactsViewModel(bucket: ResearchEvidenceBucket): HalfYearFactsViewModel {
    const summary = bucket.summary
    const rawPages = Array.isArray(summary.pages) ? summary.pages : []
    const pages = rawPages
        .filter((p): p is Record<string, unknown> => !!p && typeof p === 'object')
        .map(p => ({
            relPath: readString(p.rel_path),
            title: readString(p.title),
            financialPeriod: readString(p.financial_period),
            disclosureDate: readString(p.disclosure_date),
            dataStatus: readString(p.data_status),
            metricCount: readNumber(p.metric_count),
            metricKeys: readStringArray(p.metric_keys),
        }))
    return {
        // [UI-014-R1] latest_period / latest_disclosure_date 在 bucket 层
        latestPeriod: readString((bucket as unknown as Record<string, unknown>).latest_period),
        latestDisclosure: readString((bucket as unknown as Record<string, unknown>).latest_disclosure_date),
        pages,
        summaryLines: readStringArray(summary.summary_lines),
        risks: readStringArray(summary.risks),
    }
}

// ── Research Score Snapshot（SCORE-001B 真实 schema）────────────────────────

export interface ScoreSnapshotViewModel {
    status: string | null
    hasSnapshot: boolean
    scoreChips: EvidenceChip[]
    theses: Array<{ topic: string | null; direction: string | null; status: string | null; coreHypothesis: string | null }>
    missingEvidence: string[]
    scoreChangeReasons: string[]
    warnings: string[]
}

export function buildScoreSnapshotViewModel(summary: Record<string, unknown>): ScoreSnapshotViewModel {
    const status = readString(summary.status)
    const snap = summary.snapshot && typeof summary.snapshot === 'object'
        ? summary.snapshot as Record<string, unknown>
        : null
    if (!snap) {
        return {
            status,
            hasSnapshot: false,
            scoreChips: [],
            theses: [],
            missingEvidence: [],
            scoreChangeReasons: [],
            warnings: [],
        }
    }

    const scoreChips: EvidenceChip[] = []
    const scores = snap.scores && typeof snap.scores === 'object'
        ? snap.scores as Record<string, unknown>
        : {}
    const rec = readNumber(scores.research_evidence_confidence)
    if (rec != null) scoreChips.push(chip('证据可信度', formatNum(rec), 'info'))
    const tq = readNumber(scores.thesis_quality)
    if (tq != null) scoreChips.push(chip('逻辑质量', formatNum(tq), 'info'))

    const theses = Array.isArray(snap.theses_summary)
        ? snap.theses_summary
            .filter((t): t is Record<string, unknown> => !!t && typeof t === 'object')
            .map(t => ({
                topic: readString(t.topic),
                direction: readString(t.direction),
                status: readString(t.status),
                coreHypothesis: readString(t.core_hypothesis),
            }))
        : []

    const sc = snap.score_change_summary && typeof snap.score_change_summary === 'object'
        ? snap.score_change_summary as Record<string, unknown>
        : null
    const scoreChangeReasons = sc ? readStringArray(sc.reasons) : []

    return {
        status,
        hasSnapshot: true,
        scoreChips,
        theses,
        missingEvidence: readStringArray(snap.missing_evidence),
        scoreChangeReasons,
        warnings: readStringArray(snap.warnings),
    }
}

// ── 请求状态机 ──────────────────────────────────────────────────────────────

export type EvidenceLoadPhase = 'idle' | 'loading' | 'loaded' | 'error'

export interface EvidenceCenterState {
    /** 当前数据归属的 symbol；symbol 变化即重置 */
    symbol: string
    phase: EvidenceLoadPhase
    data: ResearchEvidenceResponse | null
    error: string | null
    /** 当前 symbol 已发起的请求次数（含首次；重试上限依据） */
    attempts: number
    /** 卸载后冻结，后续事件一律丢弃 */
    unmounted: boolean
}

/** 每 symbol 最大尝试次数：1 次自动 + 2 次手动重试 */
export const EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL = 3

export type EvidenceCenterEvent =
    | { type: 'symbol_changed'; symbol: string }
    | { type: 'load_started' }
    | { type: 'load_succeeded'; symbol: string; response: ResearchEvidenceResponse }
    | { type: 'load_failed'; symbol: string; message: string }
    | { type: 'retry_clicked' }
    | { type: 'unmounted' }

export function initialEvidenceCenterState(symbol: string): EvidenceCenterState {
    return { symbol, phase: 'idle', data: null, error: null, attempts: 0, unmounted: false }
}

export function reduceEvidenceCenter(
    state: EvidenceCenterState,
    event: EvidenceCenterEvent,
): EvidenceCenterState {
    if (state.unmounted) return state
    switch (event.type) {
        case 'unmounted':
            return { ...state, unmounted: true }
        case 'symbol_changed':
            if (event.symbol === state.symbol) return state
            // [UI-014-R1] symbol 变化清空旧数据：旧 symbol 的 data/error
            // 不得展示在新 symbol 名下
            return initialEvidenceCenterState(event.symbol)
        case 'load_started':
            if (state.phase !== 'idle') return state
            return { ...state, phase: 'loading', error: null, attempts: state.attempts + 1 }
        case 'load_succeeded':
            // 慢响应归属校验：仅当前 symbol 且仍在 loading 中才采纳
            if (event.symbol !== state.symbol || state.phase !== 'loading') return state
            return { ...state, phase: 'loaded', data: event.response, error: null }
        case 'load_failed':
            if (event.symbol !== state.symbol || state.phase !== 'loading') return state
            // 稳定进入 error，不自动重试
            return { ...state, phase: 'error', error: event.message }
        case 'retry_clicked':
            if (state.phase !== 'error') return state
            if (state.attempts >= EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL) return state
            return { ...state, phase: 'idle', error: null }
        default:
            return state
    }
}

/** 是否应当发起请求：展开 + idle + 未超重试上限 */
export function shouldFetchEvidence(state: EvidenceCenterState, expanded: boolean): boolean {
    return expanded
        && state.phase === 'idle'
        && state.attempts < EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL
}
