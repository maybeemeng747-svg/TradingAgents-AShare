// [KB-011] knowledge_contract_ui
//
// Helpers that classify a candidate's / observation item's KB payload so the
// UI can simultaneously render 加分项 (credits) AND 降权项 (penalties), per
// the KB-011 acceptance criterion:
//
//   "前端展示必须同时显示加分项与降权项：过期、低置信、同源重复、过热惩罚。
//    只有低置信/过期命中时，前端不显示为强正面。"
//
// All helpers are pure functions over plain dicts so they can be unit-tested
// without React. They must be defensive against missing / malformed payloads
// (KB-008/KB-009 may legitimately produce empty structures when there is no
// hit — that is NORMAL_NO_DATA, not an error).

export interface KnowledgeDetail {
    readonly [key: string]: unknown
}

export interface ResearchAttentionBreakdown {
    /** True when the symbol has at least one non-zero credit OR penalty. */
    hasAnySignal: boolean
    /** True when at least one fresh, high-quality mention exists. */
    hasCredit: boolean
    /** True when stale / low-confidence / dedup / overheat dominates. */
    isWeakOrPenalized: boolean
    /** Numeric scores mirrored from detail for convenience. */
    baseScore: number
    effectiveScore: number
    overheatPenalty: number
    dedupPenalty: number
    /** Human-readable credit lines, e.g. "高质量研报 2 篇 (+2.0)". */
    credits: string[]
    /** Human-readable penalty lines, e.g. "过期命中 1 篇 (-0.7)". */
    penalties: string[]
    /** Short headline suitable for an empty-state chip. */
    emptyHeadline: string
}

/**
 * Parse a possibly-missing numeric field from a dict.
 * Defensive: returns 0 for null/undefined/NaN/non-finite values so the UI
 * never renders NaN. Strings that look like numbers are coerced (mirrors
 * Pydantic's lenient coercion of backend float fields).
 */
function asFiniteNumber(value: unknown): number {
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string') {
        const n = Number(value)
        if (Number.isFinite(n)) return n
    }
    return 0
}

function asNumberArray(value: unknown): number[] {
    if (!Array.isArray(value)) return []
    return value.map(asFiniteNumber).filter(n => n > 0)
}

function asStringArray(value: unknown): string[] {
    if (!Array.isArray(value)) return []
    return value.map(v => (typeof v === 'string' ? v : String(v ?? ''))).filter(Boolean)
}

/**
 * Classify a research_attention_detail payload into credits and penalties.
 *
 * The detail dict shape comes from KB-008 + KB-009 (see
 * ``research_attention_decay.decay_to_summary`` and
 * ``research_attention.attention_to_summary``). It may contain any subset of:
 *
 *   - research_attention_score, research_attention_effective_score,
 *     research_attention_overheat_penalty, research_attention_dedup_penalty,
 *     research_attention_base_score, research_attention_time_decay_factor
 *   - mention_count, fresh_mention_count, stale_mention_count,
 *     deprecated_mention_count, high_quality_mention_count
 *   - research_attention_decay_explain: string[] (human-readable lines,
 *     already prefixed with +/- by the backend)
 *   - research_attention_warnings: string[]
 *   - research_attention_overheat_flags: string[]
 *
 * Any missing field is treated as "no signal in that dimension".
 */
export function classifyResearchAttention(detail: KnowledgeDetail | null | undefined): ResearchAttentionBreakdown {
    const d = detail ?? {}
    const baseScore = asFiniteNumber(d.research_attention_base_score ?? d.research_attention_score)
    const effectiveScore = asFiniteNumber(d.research_attention_effective_score ?? d.research_attention_score)
    const overheatPenalty = asFiniteNumber(d.research_attention_overheat_penalty)
    const dedupPenalty = asFiniteNumber(d.research_attention_dedup_penalty)

    const fresh = asFiniteNumber(d.fresh_mention_count)
    const highQuality = asFiniteNumber(d.high_quality_mention_count)
    const stale = asFiniteNumber(d.stale_mention_count)
    const deprecated = asFiniteNumber(d.deprecated_mention_count)
    const mentionCount = asFiniteNumber(d.mention_count)
    const themeCount = asFiniteNumber(d.knowledge_theme_count ?? d.theme_count)

    const decayExplain = asStringArray(d.research_attention_decay_explain)
    const warnings = asStringArray(d.research_attention_warnings)
    const overheatFlags = asStringArray(d.research_attention_overheat_flags)

    const credits: string[] = []
    const penalties: string[] = []

    if (highQuality > 0) credits.push(`高质量研报 ${highQuality} 篇`)
    if (fresh > 0) credits.push(`近期研报 ${fresh} 篇`)
    if (themeCount > 0) credits.push(`主题交叉 ${themeCount} 个`)
    // Note: base_score is the weighted sum of ALL mentions (fresh + stale +
    // deprecated). It cannot by itself signal "strong positive" because a
    // base_score of 2.5 made up entirely of stale mentions is NOT a credit.
    // Only render the base_score line as a credit when fresh/high-quality
    // mentions exist to back it.

    if (stale > 0) penalties.push(`过期命中 ${stale} 篇`)
    if (deprecated > 0) penalties.push(`低置信命中 ${deprecated} 篇`)
    if (dedupPenalty > 0) penalties.push(`同源去重 -${dedupPenalty.toFixed(2)}`)
    if (overheatPenalty > 0) penalties.push(`过热惩罚 -${overheatPenalty.toFixed(2)}`)
    for (const flag of overheatFlags) penalties.push(`过热信号: ${flag}`)
    for (const w of warnings) penalties.push(w)
    // Backend-prepared explain lines (already include +/- signs). Only add
    // them if credits/penalties didn't already cover the same ground.
    if (credits.length === 0 && penalties.length === 0) {
        for (const line of decayExplain) {
            if (line.startsWith('-') || line.includes('惩罚') || line.includes('过期') || line.includes('低置信')) {
                penalties.push(line)
            } else if (line.startsWith('+') || line.includes('加分') || line.includes('高质量')) {
                credits.push(line)
            }
        }
    }

    // hasCredit requires genuine fresh/high-quality signal — base_score alone
    // does NOT qualify (KB-011 weak rule).
    const hasCredit = highQuality > 0 || fresh > 0
    const hasPenalty = stale > 0 || deprecated > 0 || dedupPenalty > 0 || overheatPenalty > 0 || overheatFlags.length > 0
    const hasAnySignal = hasCredit || hasPenalty || mentionCount > 0 || effectiveScore > 0

    // KB-011 rule: only-stale / only-low-confidence hits must NOT display as
    // strong positive even if base_score > 0. Weak = no fresh + no high-quality
    // AND (stale OR deprecated OR penalty).
    const isWeakOrPenalized =
        !hasCredit && (stale > 0 || deprecated > 0 || overheatPenalty > 0 || dedupPenalty > 0 || overheatFlags.length > 0)

    return {
        hasAnySignal,
        hasCredit,
        isWeakOrPenalized,
        baseScore,
        effectiveScore,
        overheatPenalty,
        dedupPenalty,
        credits,
        penalties,
        emptyHeadline: '暂无研报关注度',
    }
}

/**
 * Classify a local_knowledge_detail payload. KB-004 detail contains:
 *   - status: HAS_DATA | NORMAL_NO_DATA | FAILED | STALE | LOW_CONFIDENCE
 *   - confidence: string label (e.g. "高/中/低")
 *   - matched_pages_brief: array of { title, summary, is_stale, is_low_confidence, ... }
 *   - knowledge_hit_count, fresh_hit_count, stale_hit_count, low_confidence_hit_count
 *
 * For TradeFlow candidates ``local_knowledge_summary`` is a STRING; for
 * reports it is a DICT. This helper accepts the detail dict only.
 */
export interface LocalKnowledgeBreakdown {
    hasAnySignal: boolean
    isWeakOrPenalized: boolean
    status: string
    confidence: string
    hitCount: number
    freshCount: number
    staleCount: number
    lowConfidenceCount: number
    risks: string[]
    errors: string[]
    emptyHeadline: string
}

export function classifyLocalKnowledge(detail: KnowledgeDetail | null | undefined): LocalKnowledgeBreakdown {
    const d = detail ?? {}
    const status = typeof d.status === 'string' ? d.status : ''
    const confidence = typeof d.confidence === 'string' ? d.confidence : ''
    const hitCount = asFiniteNumber(d.knowledge_hit_count ?? d.matched_count)
    const freshCount = asFiniteNumber(d.fresh_hit_count)
    const staleCount = asFiniteNumber(d.stale_hit_count)
    const lowConfidenceCount = asFiniteNumber(d.low_confidence_hit_count)

    // Aggregate page-level stale/low-confidence flags when scalar counts missing.
    const pages = Array.isArray(d.matched_pages_brief) ? (d.matched_pages_brief as KnowledgeDetail[]) : []
    let pageStale = 0
    let pageLow = 0
    for (const p of pages) {
        if (p.is_stale) pageStale += 1
        if (p.is_low_confidence || p.is_to_be_supplemented) pageLow += 1
    }
    const staleTotal = staleCount || pageStale
    const lowTotal = lowConfidenceCount || pageLow

    const risks = asStringArray(d.risks)
    const errors = asStringArray(d.errors)

    const isFailed = status === 'FAILED'
    const isWeakOrPenalized =
        !isFailed &&
        hitCount > 0 &&
        (status === 'STALE' || status === 'LOW_CONFIDENCE' || (staleTotal > 0 && freshCount === 0) || (lowTotal > 0 && freshCount === 0))

    return {
        hasAnySignal: hitCount > 0 || status === 'HAS_DATA' || pages.length > 0,
        isWeakOrPenalized,
        status,
        confidence,
        hitCount,
        freshCount,
        staleCount: staleTotal,
        lowConfidenceCount: lowTotal,
        risks,
        errors,
        emptyHeadline: '暂无本地知识',
    }
}

/**
 * KB-011 "weak dominant" gate: should the UI display this candidate's KB
 * section as strong positive? Returns false whenever only low-confidence /
 * stale / penalty signals are present (no fresh + no high-quality).
 */
export function isKnowledgeStrongPositive(
    researchDetail: KnowledgeDetail | null | undefined,
    localDetail: KnowledgeDetail | null | undefined,
): boolean {
    const ra = classifyResearchAttention(researchDetail)
    const lk = classifyLocalKnowledge(localDetail)
    if (ra.isWeakOrPenalized) return false
    if (lk.isWeakOrPenalized) return false
    return ra.hasCredit || lk.hasAnySignal
}

export { asFiniteNumber, asNumberArray, asStringArray }

// ─────────────────────────────────────────────────────────────────────────────
// [REPORT-UX-006] knowledge_evidence_card
//
// Pure helpers that derive a lightweight "知识证据来源卡" from the existing
// ``local_knowledge_summary`` + ``half_year_facts_summary`` dicts already
// attached to a TA report (KB-003/KB-008 + HY-004). The card answers three
// questions for the reader without re-parsing markdown bodies or waiting for
// a new aggregation API:
//
//   1. 用了哪些本地资料？(hit counts, latest date, half-year period)
//   2. 资料是否过期/冲突/禁用？(freshness)
//   3. 还缺什么？(gapCode + gapExplanation)
//
// Hard contract (mirrors REPORT-UX-005):
//   - The card MUST NOT alter decision / execution_action / action_label.
//     It is a pure read-only presentation layer over the summary dicts.
//   - Source paths are sanitized to knowledge-root-relative form before
//     display; absolute paths are never surfaced (constraint #3).
//   - Legacy reports (all KB fields null) collapse to a hideable empty card
//     rather than an error.
// ─────────────────────────────────────────────────────────────────────────────

/** Combined freshness label shown as a badge on the card. */
export type KnowledgeFreshness =
    | 'fresh'
    | 'stale'
    | 'conflict'
    | 'disabled'
    | 'no_data'

/**
 * Gap reason codes — the six states REPORT-UX-006 must distinguish:
 *   - none                : evidence present and healthy
 *   - no_hit              : KB queried but no document matched the symbol
 *   - missing_source      : KB fields entirely absent (legacy report path)
 *   - stale               : every hit is stale / outdated
 *   - conflict            : half-year facts carry a data conflict
 *   - pending_tree_work   : raw research exists but no wiki digested it
 *                           (signaled via is_to_be_supplemented / LOW_CONFIDENCE)
 *   - disabled            : KB disabled by operator (KNOWLEDGE_*_DISABLED)
 */
export type KnowledgeGapCode =
    | 'none'
    | 'no_hit'
    | 'missing_source'
    | 'stale'
    | 'conflict'
    | 'pending_tree_work'
    | 'disabled'

export interface KnowledgeEvidenceCard {
    /** True when any KB field is present (card should render). */
    hasAnySignal: boolean
    /** Total local-knowledge matched document count (0 when no hit). */
    localKnowledgeHitCount: number
    /** Half-year report matched page count (0 when no half-year data). */
    halfYearHitCount: number
    /** Latest half-year financial period, e.g. "2024H1" (null when absent). */
    halfYearPeriod: string | null
    /** Latest half-year disclosure date (null when absent). */
    halfYearDisclosureDate: string | null
    /** Latest updated_at across local-knowledge hits (null when absent). */
    latestDate: string | null
    /** Best source-quality tier observed (KB-014), or null when not surfaced. */
    bestSourceTier: string | null
    /** Distinct source tiers seen (empty when the summary omits tier info). */
    sourceTiers: string[]
    /** Copyable knowledge-root-relative paths (constraint #3). */
    sourcePaths: string[]
    /** Combined freshness badge label. */
    freshness: KnowledgeFreshness
    /** Machine-readable gap reason (one of the six required codes). */
    gapCode: KnowledgeGapCode
    /** Human-readable one-line gap explanation (Chinese). */
    gapExplanation: string
}

const DISABLED_HEADLINE = '本地知识库已被运维禁用，未参与本报告'
const NO_HIT_HEADLINE = '未命中该标的的本地研报/半年报资料'
const MISSING_SOURCE_HEADLINE = '本报告未接入本地知识证据'
const STALE_HEADLINE = '本地知识命中已过期，请优先参考最新公告/财报'
const CONFLICT_HEADLINE = '半年报事实存在冲突，需人工复核后再用于决策'
const PENDING_TREE_WORK_HEADLINE = '已有原始研报但尚未被 Tree Work 消化为 wiki，证据不完整'
const OK_HEADLINE = '本地知识证据已纳入研究背景（不影响买卖动作）'

const TIER_RANK: Record<string, number> = {
    original_filing: 5,
    official_notice: 4,
    broker_research: 3,
    media: 2,
    user_note: 1,
    unknown: 0,
}

/**
 * Reduce a knowledge-root-relative path for display.
 *
 * Constraint #3: "来源路径仅显示知识根目录内相对路径，可复制但不直接暴露任意
 * 绝对路径". The provider already stores paths relative to the knowledge root
 * at the source, so any absolute path arriving here is suspect and is dropped
 * entirely — we deliberately do NOT attempt to "recover" a tail from an
 * absolute path, because that could leak an arbitrary filesystem path chosen
 * by an attacker. Parent-escape sequences are dropped for the same reason.
 */
export function sanitizeRelativePath(value: unknown): string {
    if (typeof value !== 'string') return ''
    const p = value.trim()
    if (!p) return ''
    // Reject anything that looks absolute (posix / windows drive letter).
    if (p.startsWith('/') || p.startsWith('\\')) return ''
    if (/^[a-zA-Z]:[\\/]/.test(p)) return ''
    // Reject parent-escape sequences that would leave the knowledge root.
    if (p.startsWith('..')) return ''
    if (p.includes('\\..\\') || p.includes('/../')) return ''
    return p
}

/** Coerce a possibly-malformed summary into a plain dict (never throws). */
function asRecord(value: unknown): KnowledgeDetail {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
        return value as KnowledgeDetail
    }
    return {}
}

function pickLatestDate(...candidates: Array<unknown>): string | null {
    let best: string | null = null
    for (const c of candidates) {
        if (typeof c === 'string' && c.trim() && /\d{4}/.test(c)) {
            const v = c.trim()
            if (best === null || v > best) best = v
        }
    }
    return best
}

/**
 * Derive the knowledge evidence card from a report's top-level KB fields.
 *
 * Inputs are the four optional summary objects already attached to a TA
 * report response (see Report interface). All inputs may be null/undefined
 * for legacy reports; the function returns a stable empty card in that case.
 *
 * The function is total — it never throws — so the report viewer can call it
 * unconditionally without wrapping in try/catch (slow query / missing KB must
 * not block the report body).
 */
export function deriveKnowledgeEvidenceCard(params: {
    localKnowledgeSummary?: Record<string, unknown> | null
    halfYearFactsSummary?: Record<string, unknown> | null
    halfYearFactsStatus?: string | null
}): KnowledgeEvidenceCard {
    const lk = asRecord(params.localKnowledgeSummary)
    const hy = asRecord(params.halfYearFactsSummary)
    const hyStatus = typeof params.halfYearFactsStatus === 'string' ? params.halfYearFactsStatus : ''

    const kbDisabled = lk.kb_disabled === true
    const lkStatus = typeof lk.status === 'string' ? lk.status : ''
    const lkHitCount = asFiniteNumber(lk.matched_count ?? lk.knowledge_hit_count)
    const hyHitCount = asFiniteNumber(hy.matched_count)
    const hyPeriod = typeof hy.latest_period === 'string' && hy.latest_period ? hy.latest_period : null
    const hyDisclosure = typeof hy.latest_disclosure_date === 'string' && hy.latest_disclosure_date ? hy.latest_disclosure_date : null

    const latestDate = pickLatestDate(lk.updated_at, hy.updated_at, hyDisclosure)

    // Source tiers / paths are only present when the backend chose to surface
    // them (currently the report summaries do not, but the helper stays
    // forward-compatible). matched_pages_brief is the KB-004 shape.
    const pagesLk = Array.isArray(lk.matched_pages_brief) ? (lk.matched_pages_brief as KnowledgeDetail[]) : []
    const pagesHy = Array.isArray(hy.pages) ? (hy.pages as KnowledgeDetail[]) : []
    const tierSet = new Set<string>()
    const pathSet = new Set<string>()
    let hasToBeSupplemented = false
    for (const p of pagesLk) {
        if (typeof p.source_quality_tier === 'string' && p.source_quality_tier) tierSet.add(p.source_quality_tier)
        if (p.is_to_be_supplemented) hasToBeSupplemented = true
        const rel = sanitizeRelativePath(p.rel_path ?? p.path)
        if (rel) pathSet.add(rel)
    }
    for (const p of pagesHy) {
        const rel = sanitizeRelativePath(p.rel_path ?? p.path)
        if (rel) pathSet.add(rel)
    }
    // Also honor an explicit source_paths list if the summary carries one.
    for (const sp of asStringArray(lk.source_paths)) {
        const rel = sanitizeRelativePath(sp)
        if (rel) pathSet.add(rel)
    }
    const sourceTiers = Array.from(tierSet)
    const bestSourceTier = sourceTiers.length
        ? sourceTiers.reduce<string | null>((best, t) => {
            if (best === null) return t
            return (TIER_RANK[t] ?? -1) > (TIER_RANK[best] ?? -1) ? t : best
        }, null)
        : null

    const hasConflict = hy.has_conflict === true || hyStatus === 'CONFLICT'
    const hasStale =
        lkStatus === 'STALE' ||
        hy.has_stale === true ||
        hyStatus === 'STALE' ||
        (asFiniteNumber(lk.stale_hit_count ?? lk.stale_mention_count) > 0 && asFiniteNumber(lk.fresh_hit_count) === 0)
    const isLowConfidence = lkStatus === 'LOW_CONFIDENCE' || hyStatus === 'LOW_CONFIDENCE'

    // ── Freshness badge (priority: disabled > conflict > stale > no_data > fresh)
    let freshness: KnowledgeFreshness = 'fresh'
    if (kbDisabled) freshness = 'disabled'
    else if (hasConflict) freshness = 'conflict'
    else if (hasStale) freshness = 'stale'
    else if (lkHitCount === 0 && hyHitCount === 0) freshness = 'no_data'

    // ── Gap code (priority order matters — most actionable first)
    const hasAnyLkField =
        lkStatus !== '' || 'matched_count' in lk || 'updated_at' in lk || 'kb_disabled' in lk
    let gapCode: KnowledgeGapCode
    let gapExplanation: string
    if (kbDisabled) {
        gapCode = 'disabled'
        gapExplanation = DISABLED_HEADLINE
    } else if (!hasAnyLkField && hyStatus === '' && !('matched_count' in hy)) {
        // Truly legacy report — none of the KB fields were ever attached.
        gapCode = 'missing_source'
        gapExplanation = MISSING_SOURCE_HEADLINE
    } else if (hasConflict) {
        gapCode = 'conflict'
        gapExplanation = CONFLICT_HEADLINE
    } else if (hasStale) {
        gapCode = 'stale'
        gapExplanation = STALE_HEADLINE
    } else if (hasToBeSupplemented || isLowConfidence) {
        // LOW_CONFIDENCE / is_to_be_supplemented signal that raw research
        // exists but the wiki page is a stub — pending Tree Work digestion.
        gapCode = 'pending_tree_work'
        gapExplanation = PENDING_TREE_WORK_HEADLINE
    } else if (lkHitCount === 0 && hyHitCount === 0) {
        gapCode = 'no_hit'
        gapExplanation = NO_HIT_HEADLINE
    } else {
        gapCode = 'none'
        gapExplanation = OK_HEADLINE
    }

    const hasAnySignal =
        hasAnyLkField ||
        hyStatus !== '' ||
        'matched_count' in hy ||
        kbDisabled

    return {
        hasAnySignal,
        localKnowledgeHitCount: lkHitCount,
        halfYearHitCount: hyHitCount,
        halfYearPeriod: hyPeriod,
        halfYearDisclosureDate: hyDisclosure,
        latestDate,
        bestSourceTier,
        sourceTiers,
        sourcePaths: Array.from(pathSet),
        freshness,
        gapCode,
        gapExplanation,
    }
}
