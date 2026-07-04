// [KB-011] knowledge_contract_ui
import { describe, expect, it } from 'vitest'

import {
    classifyResearchAttention,
    classifyLocalKnowledge,
    isKnowledgeStrongPositive,
    asFiniteNumber,
    asStringArray,
} from '@/utils/knowledgeContract'

describe('[KB-011] asFiniteNumber — defensive numeric parsing', () => {
    it('returns 0 for null / undefined / NaN / non-finite', () => {
        expect(asFiniteNumber(null)).toBe(0)
        expect(asFiniteNumber(undefined)).toBe(0)
        expect(asFiniteNumber(NaN)).toBe(0)
        expect(asFiniteNumber(Infinity)).toBe(0)
        expect(asFiniteNumber({})).toBe(0)
        expect(asFiniteNumber('not a number')).toBe(0)
    })
    it('passes through finite numbers', () => {
        expect(asFiniteNumber(3.14)).toBe(3.14)
        expect(asFiniteNumber(0)).toBe(0)
        expect(asFiniteNumber(-2.5)).toBe(-2.5)
    })
    it('coerces numeric strings', () => {
        expect(asFiniteNumber('4.5')).toBe(4.5)
        expect(asFiniteNumber('0')).toBe(0)
    })
})

describe('[KB-011] asStringArray — defensive array parsing', () => {
    it('returns [] for non-arrays', () => {
        expect(asStringArray(null)).toEqual([])
        expect(asStringArray(undefined)).toEqual([])
        expect(asStringArray('hello')).toEqual([])
        expect(asStringArray({ a: 1 })).toEqual([])
    })
    it('drops falsy / empty entries', () => {
        expect(asStringArray(['a', '', null, undefined, 'b'])).toEqual(['a', 'b'])
    })
    it('stringifies non-string entries', () => {
        expect(asStringArray([1, 2, 3])).toEqual(['1', '2', '3'])
    })
})

describe('[KB-011] classifyResearchAttention — empty / no-hit payload', () => {
    it('returns NORMAL_NO_DATA semantics for null', () => {
        const r = classifyResearchAttention(null)
        expect(r.hasAnySignal).toBe(false)
        expect(r.hasCredit).toBe(false)
        expect(r.isWeakOrPenalized).toBe(false)
        expect(r.credits).toEqual([])
        expect(r.penalties).toEqual([])
        expect(r.emptyHeadline).toBe('暂无研报关注度')
    })
    it('returns NORMAL_NO_DATA semantics for {}', () => {
        const r = classifyResearchAttention({})
        expect(r.hasAnySignal).toBe(false)
    })
})

describe('[KB-011] classifyResearchAttention — strong positive', () => {
    it('classifies fresh + high-quality mentions as credit', () => {
        const r = classifyResearchAttention({
            research_attention_score: 4.5,
            research_attention_base_score: 4.5,
            research_attention_effective_score: 4.0,
            fresh_mention_count: 2,
            high_quality_mention_count: 1,
            mention_count: 3,
            knowledge_theme_count: 5,
        })
        expect(r.hasCredit).toBe(true)
        expect(r.isWeakOrPenalized).toBe(false)
        expect(r.credits.length).toBeGreaterThan(0)
        expect(r.penalties).toEqual([])
    })
    it('exposes effective score and overheat penalty', () => {
        const r = classifyResearchAttention({
            research_attention_base_score: 5.0,
            research_attention_effective_score: 3.0,
            research_attention_overheat_penalty: 2.0,
        })
        expect(r.baseScore).toBe(5.0)
        expect(r.effectiveScore).toBe(3.0)
        expect(r.overheatPenalty).toBe(2.0)
        expect(r.penalties).toContain('过热惩罚 -2.00')
    })
})

describe('[KB-011] classifyResearchAttention — penalty-only (KB-011 weak rule)', () => {
    it('marks stale-only hit as weak, NOT strong positive', () => {
        const r = classifyResearchAttention({
            research_attention_score: 2.5,
            research_attention_base_score: 2.5,
            stale_mention_count: 2,
            fresh_mention_count: 0,
            high_quality_mention_count: 0,
        })
        expect(r.hasCredit).toBe(false)
        expect(r.isWeakOrPenalized).toBe(true)
        expect(r.penalties).toContain('过期命中 2 篇')
    })
    it('marks low-confidence-only hit as weak', () => {
        const r = classifyResearchAttention({
            research_attention_score: 1.5,
            deprecated_mention_count: 3,
            fresh_mention_count: 0,
            high_quality_mention_count: 0,
        })
        expect(r.isWeakOrPenalized).toBe(true)
        expect(r.penalties).toContain('低置信命中 3 篇')
    })
    it('marks dedup-only hit as weak', () => {
        const r = classifyResearchAttention({
            research_attention_dedup_penalty: 0.4,
        })
        expect(r.isWeakOrPenalized).toBe(true)
        expect(r.penalties).toContain('同源去重 -0.40')
    })
    it('marks overheat-flags-only as weak', () => {
        const r = classifyResearchAttention({
            research_attention_overheat_flags: ['SHORT_TERM_GAIN', 'THEME_CROWDING'],
        })
        expect(r.isWeakOrPenalized).toBe(true)
        expect(r.penalties.filter(p => p.startsWith('过热信号:'))).toHaveLength(2)
    })
})

describe('[KB-011] classifyResearchAttention — mixed credit + penalty', () => {
    it('lists both credits and penalties', () => {
        const r = classifyResearchAttention({
            research_attention_base_score: 3.5,
            research_attention_effective_score: 1.5,
            fresh_mention_count: 1,
            high_quality_mention_count: 1,
            stale_mention_count: 1,
            research_attention_overheat_penalty: 1.0,
        })
        expect(r.hasCredit).toBe(true)
        // Has credit so NOT weak — but still has penalties to display.
        expect(r.isWeakOrPenalized).toBe(false)
        expect(r.credits.length).toBeGreaterThan(0)
        expect(r.penalties.length).toBeGreaterThan(0)
    })
})

describe('[KB-011] classifyLocalKnowledge — empty / no-hit payload', () => {
    it('returns NORMAL_NO_DATA for null', () => {
        const r = classifyLocalKnowledge(null)
        expect(r.hasAnySignal).toBe(false)
        expect(r.isWeakOrPenalized).toBe(false)
        expect(r.emptyHeadline).toBe('暂无本地知识')
    })
    it('handles FAILED without marking weak', () => {
        const r = classifyLocalKnowledge({ status: 'FAILED', errors: ['read failed'] })
        expect(r.status).toBe('FAILED')
        expect(r.isWeakOrPenalized).toBe(false)
        expect(r.errors).toEqual(['read failed'])
    })
})

describe('[KB-011] classifyLocalKnowledge — weak rule', () => {
    it('marks STALE status as weak', () => {
        const r = classifyLocalKnowledge({
            status: 'STALE',
            knowledge_hit_count: 2,
            fresh_hit_count: 0,
            stale_hit_count: 2,
        })
        expect(r.isWeakOrPenalized).toBe(true)
        expect(r.staleCount).toBe(2)
    })
    it('marks LOW_CONFIDENCE status as weak', () => {
        const r = classifyLocalKnowledge({
            status: 'LOW_CONFIDENCE',
            knowledge_hit_count: 3,
            fresh_hit_count: 0,
            low_confidence_hit_count: 3,
        })
        expect(r.isWeakOrPenalized).toBe(true)
    })
    it('aggregates page-level flags when scalar counts missing', () => {
        const r = classifyLocalKnowledge({
            status: 'HAS_DATA',
            knowledge_hit_count: 2,
            matched_pages_brief: [
                { is_stale: true },
                { is_low_confidence: true },
            ],
        })
        expect(r.staleCount).toBe(1)
        expect(r.lowConfidenceCount).toBe(1)
        // fresh_hit_count=0 + stale>0 => weak
        expect(r.isWeakOrPenalized).toBe(true)
    })
    it('does NOT mark HAS_DATA with fresh hits as weak', () => {
        const r = classifyLocalKnowledge({
            status: 'HAS_DATA',
            knowledge_hit_count: 3,
            fresh_hit_count: 2,
            stale_hit_count: 1,
        })
        // fresh > 0 means it is genuine positive coverage even if some pages stale
        expect(r.isWeakOrPenalized).toBe(false)
        expect(r.hasAnySignal).toBe(true)
    })
})

describe('[KB-011] isKnowledgeStrongPositive — KB-011 gate', () => {
    it('returns false when research detail is weak', () => {
        expect(isKnowledgeStrongPositive(
            { research_attention_score: 2.0, stale_mention_count: 2 },
            null,
        )).toBe(false)
    })
    it('returns false when local knowledge detail is weak', () => {
        expect(isKnowledgeStrongPositive(
            null,
            { status: 'STALE', knowledge_hit_count: 2, stale_hit_count: 2 },
        )).toBe(false)
    })
    it('returns true when at least one side has fresh credit', () => {
        expect(isKnowledgeStrongPositive(
            { research_attention_score: 3.0, fresh_mention_count: 1, high_quality_mention_count: 1 },
            null,
        )).toBe(true)
    })
    it('returns false for entirely empty payloads', () => {
        expect(isKnowledgeStrongPositive(null, null)).toBe(false)
        expect(isKnowledgeStrongPositive({}, {})).toBe(false)
    })
})

describe('[KB-011] contract: TradeFlowCandidateItem TS shape parity', () => {
    // Static field-list assertion. TS types are erased at runtime, but we can
    // guarantee the contract by building a fixture candidate via the shared
    // builder and checking that all KB-011 fields are present with the
    // expected default values. This is the same shape the backend
    // TradeFlowCandidateItem Pydantic schema emits when enriched.
    it('buildFixtureCandidate includes all KB-011 research attention defaults', async () => {
        const { buildFixtureCandidate } = await import('@/utils/tradeflowFocus')
        const c = buildFixtureCandidate({ symbol: '600000.SH' })
        const keys: Array<keyof typeof c> = [
            'research_attention_score',
            'research_attention_effective_score',
            'research_attention_overheat_penalty',
            'research_attention_summary',
            'research_attention_detail',
            'knowledge_theme_count',
            'local_knowledge_score',
            'knowledge_hit_count',
            'local_knowledge_summary',
            'local_knowledge_detail',
            'needs_tree_work_research',
        ]
        for (const k of keys) {
            expect(k in c, `missing field ${k}`).toBe(true)
        }
        // Defaults equal NORMAL_NO_DATA semantics.
        expect(c.research_attention_score).toBe(0)
        expect(c.research_attention_effective_score).toBe(0)
        expect(c.research_attention_overheat_penalty).toBe(0)
        expect(c.research_attention_summary).toBe('')
        expect(c.research_attention_detail).toEqual({})
        expect(c.knowledge_theme_count).toBe(0)
        expect(c.needs_tree_work_research).toBe(false)
    })
})
