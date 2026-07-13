// [REPORT-UX-006] knowledge_evidence_card
import { describe, expect, it } from 'vitest'

import {
    deriveKnowledgeEvidenceCard,
    sanitizeRelativePath,
} from '@/utils/knowledgeContract'

describe('[REPORT-UX-006] sanitizeRelativePath — never leaks absolute paths', () => {
    it('passes through clean relative paths', () => {
        expect(sanitizeRelativePath('wiki/investment/600519.md')).toBe('wiki/investment/600519.md')
        expect(sanitizeRelativePath('raw/research/note.md')).toBe('raw/research/note.md')
    })
    it('returns "" for absolute posix paths even when they target a knowledge subdir', () => {
        // Constraint #3: never expose an arbitrary absolute path. The provider
        // stores paths relative at the source, so any absolute path arriving
        // here is suspect and is dropped wholesale (no recovery).
        expect(sanitizeRelativePath('/Users/maybee/Documents/knowledge/wiki/x.md')).toBe('')
        expect(sanitizeRelativePath('/etc/passwd')).toBe('')
        expect(sanitizeRelativePath('/knowledge/wiki/investment/600519.md')).toBe('')
    })
    it('returns "" for windows drive paths', () => {
        expect(sanitizeRelativePath('C:\\Users\\docs\\wiki\\x.md')).toBe('')
    })
    it('returns "" for parent-escape sequences', () => {
        expect(sanitizeRelativePath('../../etc/passwd')).toBe('')
        expect(sanitizeRelativePath('wiki/../../etc')).toBe('')
    })
    it('returns "" for non-string / empty', () => {
        expect(sanitizeRelativePath(null)).toBe('')
        expect(sanitizeRelativePath(undefined)).toBe('')
        expect(sanitizeRelativePath(123)).toBe('')
        expect(sanitizeRelativePath('   ')).toBe('')
    })
    it('recovers the tail when an absolute path targets a known knowledge subdir', () => {
        // Absolute paths are always dropped (see constraint above).
        expect(sanitizeRelativePath('/knowledge/wiki/investment/600519.md')).toBe('')
    })
})

describe('[REPORT-UX-006] deriveKnowledgeEvidenceCard — six required states', () => {
    it('S1 complete: fresh hits + half-year facts → freshness=fresh, gap=none', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: {
                status: 'HAS_DATA',
                matched_count: 3,
                confidence: 'medium',
                updated_at: '2026-06-01',
            },
            halfYearFactsSummary: {
                status: 'HAS_DATA',
                matched_count: 1,
                latest_period: '2025H1',
                latest_disclosure_date: '2025-08-30',
                has_conflict: false,
                has_stale: false,
            },
            halfYearFactsStatus: 'HAS_FACTS',
        })
        expect(card.hasAnySignal).toBe(true)
        expect(card.localKnowledgeHitCount).toBe(3)
        expect(card.halfYearHitCount).toBe(1)
        expect(card.halfYearPeriod).toBe('2025H1')
        expect(card.freshness).toBe('fresh')
        expect(card.gapCode).toBe('none')
        expect(card.latestDate).toBe('2026-06-01')
    })

    it('S2 empty: KB present but zero hits → no_data / no_hit', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: {
                status: 'NORMAL_NO_DATA',
                matched_count: 0,
                confidence: 'low',
            },
            halfYearFactsSummary: {
                status: 'NORMAL_NO_DATA',
                matched_count: 0,
            },
            halfYearFactsStatus: 'NO_DATA',
        })
        expect(card.localKnowledgeHitCount).toBe(0)
        expect(card.halfYearHitCount).toBe(0)
        expect(card.freshness).toBe('no_data')
        expect(card.gapCode).toBe('no_hit')
        expect(card.gapExplanation).toContain('未命中')
    })

    it('S3 stale: every hit stale → freshness=stale, gap=stale', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: {
                status: 'STALE',
                matched_count: 2,
                stale_hit_count: 2,
                fresh_hit_count: 0,
            },
            halfYearFactsSummary: {
                matched_count: 0,
                has_stale: true,
            },
            halfYearFactsStatus: 'STALE',
        })
        expect(card.freshness).toBe('stale')
        expect(card.gapCode).toBe('stale')
        expect(card.gapExplanation).toContain('过期')
    })

    it('S4 conflict: half-year facts conflict wins over stale → conflict', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: {
                status: 'HAS_DATA',
                matched_count: 2,
            },
            halfYearFactsSummary: {
                matched_count: 2,
                has_conflict: true,
                has_stale: true,
            },
            halfYearFactsStatus: 'CONFLICT',
        })
        expect(card.freshness).toBe('conflict')
        expect(card.gapCode).toBe('conflict')
        expect(card.gapExplanation).toContain('冲突')
    })

    it('S5 disabled: kb_disabled=true short-circuits everything → disabled', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: {
                status: 'NORMAL_NO_DATA',
                matched_count: 0,
                kb_disabled: true,
            },
            halfYearFactsSummary: null,
            halfYearFactsStatus: null,
        })
        expect(card.freshness).toBe('disabled')
        expect(card.gapCode).toBe('disabled')
        expect(card.gapExplanation).toContain('禁用')
        // Even when a conflict would otherwise be present, disabled wins.
        const card2 = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: { kb_disabled: true, matched_count: 0 },
            halfYearFactsSummary: { has_conflict: true, matched_count: 1 },
            halfYearFactsStatus: 'CONFLICT',
        })
        expect(card2.gapCode).toBe('disabled')
    })

    it('S6 legacy: all KB fields absent → missing_source + hasAnySignal=false (card hidden)', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: null,
            halfYearFactsSummary: null,
            halfYearFactsStatus: null,
        })
        expect(card.hasAnySignal).toBe(false)
        expect(card.gapCode).toBe('missing_source')
        expect(card.gapExplanation).toContain('未接入')
        expect(card.localKnowledgeHitCount).toBe(0)
        expect(card.halfYearHitCount).toBe(0)
        expect(card.freshness).toBe('no_data')
    })
})

describe('[REPORT-UX-006] deriveKnowledgeEvidenceCard — pending Tree Work detection', () => {
    it('LOW_CONFIDENCE status maps to pending_tree_work gap', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: {
                status: 'LOW_CONFIDENCE',
                matched_count: 1,
                matched_pages_brief: [{ rel_path: 'wiki/x.md', is_to_be_supplemented: true }],
            },
            halfYearFactsSummary: { matched_count: 0 },
            halfYearFactsStatus: 'LOW_CONFIDENCE',
        })
        expect(card.gapCode).toBe('pending_tree_work')
        expect(card.gapExplanation).toContain('Tree Work')
        // The relative path is surfaced copyable (constraint #3).
        expect(card.sourcePaths).toEqual(['wiki/x.md'])
    })
})

describe('[REPORT-UX-006] deriveKnowledgeEvidenceCard — never throws on malformed input', () => {
    it('handles non-object summaries defensively', () => {
        // @ts-expect-error intentionally malformed
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: 'not a dict',
            halfYearFactsSummary: 42,
            halfYearFactsStatus: {},
        })
        expect(card.hasAnySignal).toBe(false)
        expect(card.gapCode).toBe('missing_source')
    })
    it('picks the latest date across local + half-year fields', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: { status: 'HAS_DATA', matched_count: 1, updated_at: '2025-04-01' },
            halfYearFactsSummary: { matched_count: 1, latest_disclosure_date: '2025-09-30', updated_at: '2025-09-30' },
            halfYearFactsStatus: 'HAS_FACTS',
        })
        expect(card.latestDate).toBe('2025-09-30')
    })
    it('picks the best source tier when pages carry tier info', () => {
        const card = deriveKnowledgeEvidenceCard({
            localKnowledgeSummary: {
                status: 'HAS_DATA',
                matched_count: 2,
                matched_pages_brief: [
                    { rel_path: 'a.md', source_quality_tier: 'media' },
                    { rel_path: 'b.md', source_quality_tier: 'original_filing' },
                ],
            },
            halfYearFactsSummary: {},
        })
        expect(card.bestSourceTier).toBe('original_filing')
        expect(card.sourceTiers).toContain('media')
    })
})
