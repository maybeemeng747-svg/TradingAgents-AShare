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
