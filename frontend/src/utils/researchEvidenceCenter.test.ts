// [UI-014] research_evidence_center
import { describe, expect, it } from 'vitest'

import type {
    ResearchEvidenceResponse,
    ResearchEvidenceDataStatus,
    ResearchEvidenceBucket,
} from '@/types'
import { sanitizeRelativePath } from '@/utils/knowledgeContract'

// ── Fixtures ────────────────────────────────────────────────────────────────

function makeBucket(
    overrides: Partial<ResearchEvidenceBucket> & { summary: Record<string, unknown> },
): ResearchEvidenceBucket {
    return {
        bucket: 'test',
        status: 'HAS_DATA',
        task: 'TEST',
        has_hit: true,
        data_status: 'fresh',
        errors: [],
        ...overrides,
    }
}

/** S1: Complete — all 5 buckets have fresh data */
function makeCompleteFixture(): ResearchEvidenceResponse {
    return {
        source: 'research_evidence',
        task: 'KB-020',
        symbol: '600519.SH',
        as_of: '2026-07-23 12:00:00',
        data_status: 'fresh',
        vendor: 'tree_work_wiki',
        endpoint: 'wiki/investment',
        knowledge_root: '/tmp/knowledge',
        query: { symbol: '600519.SH', window_months: 12 },
        source_freshness: {
            consensus: { data_status: 'fresh', has_hit: true, task: 'KB-016' },
            citation_audit: { data_status: 'fresh', has_hit: true, task: 'KB-017' },
            thesis_timeline: { data_status: 'fresh', has_hit: true, task: 'KB-018' },
            half_year_facts: { data_status: 'fresh', has_hit: true, task: 'HY-003' },
            research_score_snapshot: { data_status: 'fresh', has_hit: true, task: 'SCORE-001B' },
        },
        consensus: makeBucket({
            bucket: 'consensus',
            task: 'KB-016',
            summary: {
                effective_report_count: 5,
                institution_dedup_count: 3,
                consensus_direction: '偏多',
                disagreement_count: 1,
                reports: [
                    { institution: '中信证券', direction: '偏多', date: '2026-07-01', summary: '看好消费升级', source_path: 'raw/research/zhongxin.md' },
                    { institution: '国泰君安', direction: '中性', date: '2026-06-15', summary: '估值合理', source_path: 'raw/research/guotai.md' },
                ],
            },
        }),
        citation_audit: makeBucket({
            bucket: 'citation_audit',
            task: 'KB-017',
            summary: {
                total_claims: 8,
                supported_count: 5,
                contradicted_count: 1,
                pending_count: 2,
                items: [
                    { claim: '营收增长10%', status: 'supported', source_path: 'wiki/investment/600519.md', detail: '半年报确认' },
                    { claim: '毛利率提升', status: 'contradicted', source_path: 'raw/research/note.md', detail: '实际下降2pp' },
                    { claim: '新产能投产', status: 'pending' },
                ],
            },
        }),
        thesis_timeline: makeBucket({
            bucket: 'thesis_timeline',
            task: 'KB-018',
            summary: {
                total_versions: 3,
                drift_direction: '偏多→中性',
                timeline: [
                    { date: '2026-01', thesis: '看好增长', direction: '偏多', source_path: 'raw/research/v1.md', status: 'superseded' },
                    { date: '2026-04', thesis: '增速放缓', direction: '中性', source_path: 'raw/research/v2.md', status: 'current' },
                ],
            },
        }),
        half_year_facts: makeBucket({
            bucket: 'half_year_facts',
            task: 'HY-003',
            summary: {
                latest_period: '2025H1',
                latest_disclosure_date: '2025-08-30',
                pages: [
                    { rel_path: 'wiki/investment/600519.md', title: '贵州茅台', financial_period: '2025H1', data_status: 'ok', metric_count: 5, metric_keys: ['revenue', 'net_profit'] },
                ],
                summary_lines: ['营收同比+10%', '净利润同比+8%'],
                risks: ['消费税改革风险'],
            },
        }),
        research_score_snapshot: makeBucket({
            bucket: 'research_score_snapshot',
            task: 'SCORE-001B',
            summary: {
                status: 'HAS_DATA',
                scores: { entry_timing: 72.5, portfolio_fit: 65.0 },
                theses_summary: '3篇研报支持偏多观点',
                missing_evidence: ['最新季度数据'],
            },
        }),
        gaps: [],
        errors: [],
        read_only: true,
    }
}

/** S2: Empty — no data at all */
function makeEmptyFixture(): ResearchEvidenceResponse {
    return {
        source: 'research_evidence',
        task: 'KB-020',
        symbol: '999999.SH',
        as_of: '2026-07-23 12:00:00',
        data_status: 'missing',
        vendor: 'tree_work_wiki',
        endpoint: 'wiki/investment',
        knowledge_root: '/tmp/knowledge',
        query: { symbol: '999999.SH', window_months: 12 },
        source_freshness: {},
        consensus: makeBucket({ bucket: 'consensus', task: 'KB-016', status: 'NO_DATA', has_hit: false, data_status: 'missing', summary: {} }),
        citation_audit: makeBucket({ bucket: 'citation_audit', task: 'KB-017', status: 'NO_DATA', has_hit: false, data_status: 'missing', summary: {} }),
        thesis_timeline: makeBucket({ bucket: 'thesis_timeline', task: 'KB-018', status: 'NO_DATA', has_hit: false, data_status: 'missing', summary: {} }),
        half_year_facts: makeBucket({ bucket: 'half_year_facts', task: 'HY-003', status: 'NO_DATA', has_hit: false, data_status: 'missing', summary: {} }),
        research_score_snapshot: makeBucket({ bucket: 'research_score_snapshot', task: 'SCORE-001B', status: 'NO_DATA', has_hit: false, data_status: 'missing', summary: {} }),
        gaps: ['consensus: no data', 'citation_audit: no data', 'thesis_timeline: no data', 'half_year_facts: no data', 'research_score_snapshot: no data'],
        errors: [],
        read_only: true,
    }
}

/** S3: Partial failure — some buckets failed, others have data */
function makePartialFailureFixture(): ResearchEvidenceResponse {
    return {
        source: 'research_evidence',
        task: 'KB-020',
        symbol: '000001.SZ',
        as_of: '2026-07-23 12:00:00',
        data_status: 'stale',
        vendor: 'tree_work_wiki',
        endpoint: 'wiki/investment',
        knowledge_root: '/tmp/knowledge',
        query: { symbol: '000001.SZ', window_months: 12 },
        source_freshness: {
            consensus: { data_status: 'fresh', has_hit: true, task: 'KB-016' },
            citation_audit: { data_status: 'failed', has_hit: false, task: 'KB-017' },
            thesis_timeline: { data_status: 'stale', has_hit: true, task: 'KB-018' },
            half_year_facts: { data_status: 'fresh', has_hit: true, task: 'HY-003' },
            research_score_snapshot: { data_status: 'failed', has_hit: false, task: 'SCORE-001B' },
        },
        consensus: makeBucket({
            bucket: 'consensus',
            task: 'KB-016',
            summary: { effective_report_count: 2, consensus_direction: '偏空' },
        }),
        citation_audit: makeBucket({
            bucket: 'citation_audit',
            task: 'KB-017',
            status: 'NO_DATA',
            has_hit: false,
            data_status: 'failed',
            errors: ['KB-017 查询超时'],
            summary: {},
        }),
        thesis_timeline: makeBucket({
            bucket: 'thesis_timeline',
            task: 'KB-018',
            data_status: 'stale',
            summary: { total_versions: 1 },
        }),
        half_year_facts: makeBucket({
            bucket: 'half_year_facts',
            task: 'HY-003',
            summary: { latest_period: '2024H2', pages: [] },
        }),
        research_score_snapshot: makeBucket({
            bucket: 'research_score_snapshot',
            task: 'SCORE-001B',
            status: 'NO_DATA',
            has_hit: false,
            data_status: 'failed',
            errors: ['SCORE-001B 数据不可用'],
            summary: {},
        }),
        gaps: ['citation_audit: failed', 'research_score_snapshot: failed'],
        errors: ['部分数据源不可用'],
        read_only: true,
    }
}

/** S4: Conflict — half-year facts have data conflicts */
function makeConflictFixture(): ResearchEvidenceResponse {
    return {
        source: 'research_evidence',
        task: 'KB-020',
        symbol: '600036.SH',
        as_of: '2026-07-23 12:00:00',
        data_status: 'fresh',
        vendor: 'tree_work_wiki',
        endpoint: 'wiki/investment',
        knowledge_root: '/tmp/knowledge',
        query: { symbol: '600036.SH', window_months: 12 },
        source_freshness: {
            consensus: { data_status: 'fresh', has_hit: true, task: 'KB-016' },
            citation_audit: { data_status: 'fresh', has_hit: true, task: 'KB-017' },
            thesis_timeline: { data_status: 'fresh', has_hit: true, task: 'KB-018' },
            half_year_facts: { data_status: 'conflict', has_hit: true, task: 'HY-003' },
            research_score_snapshot: { data_status: 'fresh', has_hit: true, task: 'SCORE-001B' },
        },
        consensus: makeBucket({
            bucket: 'consensus',
            task: 'KB-016',
            summary: { effective_report_count: 4, consensus_direction: '偏多', disagreement_count: 2 },
        }),
        citation_audit: makeBucket({
            bucket: 'citation_audit',
            task: 'KB-017',
            summary: { total_claims: 6, supported_count: 3, contradicted_count: 2, pending_count: 1 },
        }),
        thesis_timeline: makeBucket({
            bucket: 'thesis_timeline',
            task: 'KB-018',
            summary: { total_versions: 2 },
        }),
        half_year_facts: makeBucket({
            bucket: 'half_year_facts',
            task: 'HY-003',
            data_status: 'conflict',
            summary: {
                latest_period: '2025H1',
                pages: [
                    { rel_path: 'wiki/investment/600036.md', title: '招商银行', financial_period: '2025H1', data_status: 'conflict', metric_count: 3 },
                ],
                summary_lines: ['营收数据存在两个不同版本'],
                risks: ['同一报告期营收数据不一致，需人工复核'],
            },
        }),
        research_score_snapshot: makeBucket({
            bucket: 'research_score_snapshot',
            task: 'SCORE-001B',
            summary: { status: 'HAS_DATA' },
        }),
        gaps: ['half_year_facts: 2 contradicted'],
        errors: [],
        read_only: true,
    }
}

// ── Tests ───────────────────────────────────────────────────────────────────

describe('[UI-014] research_evidence_center — fixture contracts', () => {
    it('S1 complete: all buckets have data, gaps empty, errors empty', () => {
        const f = makeCompleteFixture()
        expect(f.data_status).toBe('fresh')
        expect(f.consensus.has_hit).toBe(true)
        expect(f.citation_audit.has_hit).toBe(true)
        expect(f.thesis_timeline.has_hit).toBe(true)
        expect(f.half_year_facts.has_hit).toBe(true)
        expect(f.research_score_snapshot.has_hit).toBe(true)
        expect(f.gaps).toHaveLength(0)
        expect(f.errors).toHaveLength(0)
    })

    it('S2 empty: data_status=missing, all buckets NO_DATA, gaps populated', () => {
        const f = makeEmptyFixture()
        expect(f.data_status).toBe('missing')
        expect(f.consensus.status).toBe('NO_DATA')
        expect(f.citation_audit.status).toBe('NO_DATA')
        expect(f.thesis_timeline.status).toBe('NO_DATA')
        expect(f.half_year_facts.status).toBe('NO_DATA')
        expect(f.research_score_snapshot.status).toBe('NO_DATA')
        expect(f.gaps.length).toBeGreaterThan(0)
    })

    it('S3 partial failure: some buckets failed, others have data', () => {
        const f = makePartialFailureFixture()
        expect(f.data_status).toBe('stale')
        expect(f.consensus.has_hit).toBe(true)
        expect(f.citation_audit.has_hit).toBe(false)
        expect(f.citation_audit.errors.length).toBeGreaterThan(0)
        expect(f.research_score_snapshot.has_hit).toBe(false)
        expect(f.research_score_snapshot.errors.length).toBeGreaterThan(0)
        expect(f.gaps.some((g) => g.includes('failed'))).toBe(true)
    })

    it('S4 conflict: half_year_facts has conflict status', () => {
        const f = makeConflictFixture()
        expect(f.half_year_facts.data_status).toBe('conflict')
        expect(f.gaps.some((g) => g.includes('contradicted'))).toBe(true)
        expect(f.half_year_facts.summary.risks?.some((r) => r.includes('不一致'))).toBe(true)
    })
})

describe('[UI-014] research_evidence_center — data status labels', () => {
    const labels: Record<ResearchEvidenceDataStatus, string> = {
        fresh: '数据可用',
        stale: '数据过期',
        missing: '暂无数据',
        failed: '加载失败',
        skipped: '已跳过',
    }

    it('all five statuses have labels', () => {
        for (const [status, label] of Object.entries(labels)) {
            expect(label).toBeTruthy()
            expect(typeof label).toBe('string')
        }
    })
})

describe('[UI-014] research_evidence_center — source path sanitization integration', () => {
    it('relative paths pass through for display', () => {
        expect(sanitizeRelativePath('wiki/investment/600519.md')).toBe('wiki/investment/600519.md')
        expect(sanitizeRelativePath('raw/research/note.md')).toBe('raw/research/note.md')
    })

    it('absolute paths are rejected (not displayed in evidence center)', () => {
        expect(sanitizeRelativePath('/Users/maybee/Documents/knowledge/wiki/x.md')).toBe('')
        expect(sanitizeRelativePath('/etc/passwd')).toBe('')
    })

    it('parent-escape sequences are rejected', () => {
        expect(sanitizeRelativePath('../../etc/passwd')).toBe('')
    })

    it('non-string / empty returns ""', () => {
        expect(sanitizeRelativePath(null)).toBe('')
        expect(sanitizeRelativePath(undefined)).toBe('')
        expect(sanitizeRelativePath('')).toBe('')
    })
})

describe('[UI-014] research_evidence_center — consensus bucket structure', () => {
    it('complete fixture has reports array with source paths', () => {
        const f = makeCompleteFixture()
        const reports = f.consensus.summary.reports ?? []
        expect(reports).toHaveLength(2)
        expect(reports[0].institution).toBe('中信证券')
        expect(reports[0].source_path).toBeTruthy()
    })

    it('empty fixture has empty summary', () => {
        const f = makeEmptyFixture()
        expect(f.consensus.summary.effective_report_count).toBeUndefined()
        expect(f.consensus.summary.reports).toBeUndefined()
    })
})

describe('[UI-014] research_evidence_center — citation audit bucket structure', () => {
    it('complete fixture has supported/contradicted/pending counts', () => {
        const f = makeCompleteFixture()
        const s = f.citation_audit.summary
        expect(s.total_claims).toBe(8)
        expect(s.supported_count).toBe(5)
        expect(s.contradicted_count).toBe(1)
        expect(s.pending_count).toBe(2)
    })

    it('partial failure bucket carries errors', () => {
        const f = makePartialFailureFixture()
        expect(f.citation_audit.errors).toContain('KB-017 查询超时')
        expect(f.citation_audit.has_hit).toBe(false)
    })
})

describe('[UI-014] research_evidence_center — thesis timeline bucket structure', () => {
    it('complete fixture has timeline entries with drift direction', () => {
        const f = makeCompleteFixture()
        const s = f.thesis_timeline.summary
        expect(s.total_versions).toBe(3)
        expect(s.drift_direction).toBe('偏多→中性')
        expect(s.timeline).toHaveLength(2)
    })
})

describe('[UI-014] research_evidence_center — half-year facts bucket structure', () => {
    it('complete fixture has pages, summary_lines, risks', () => {
        const f = makeCompleteFixture()
        const s = f.half_year_facts.summary
        expect(s.latest_period).toBe('2025H1')
        expect(s.pages).toHaveLength(1)
        expect(s.summary_lines).toHaveLength(2)
        expect(s.risks).toHaveLength(1)
    })

    it('conflict fixture has conflict data_status and risk message', () => {
        const f = makeConflictFixture()
        expect(f.half_year_facts.data_status).toBe('conflict')
        expect(f.half_year_facts.summary.risks?.[0]).toContain('不一致')
    })
})

describe('[UI-014] research_evidence_center — score snapshot bucket structure', () => {
    it('complete fixture has scores and theses_summary', () => {
        const f = makeCompleteFixture()
        const s = f.research_score_snapshot.summary
        expect(s.status).toBe('HAS_DATA')
        expect(s.scores).toBeTruthy()
        expect(s.theses_summary).toContain('偏多')
        expect(s.missing_evidence).toContain('最新季度数据')
    })

    it('partial failure bucket carries errors', () => {
        const f = makePartialFailureFixture()
        expect(f.research_score_snapshot.errors).toContain('SCORE-001B 数据不可用')
    })
})

describe('[UI-014] research_evidence_center — source_freshness digest', () => {
    it('complete fixture has freshness for all 5 buckets', () => {
        const f = makeCompleteFixture()
        const keys = Object.keys(f.source_freshness)
        expect(keys).toContain('consensus')
        expect(keys).toContain('citation_audit')
        expect(keys).toContain('thesis_timeline')
        expect(keys).toContain('half_year_facts')
        expect(keys).toContain('research_score_snapshot')
    })

    it('each freshness entry has data_status and has_hit', () => {
        const f = makeCompleteFixture()
        for (const [_key, val] of Object.entries(f.source_freshness)) {
            expect(typeof val.data_status).toBe('string')
            expect(typeof val.has_hit).toBe('boolean')
            expect(typeof val.task).toBe('string')
        }
    })
})

describe('[UI-014] research_evidence_center — JSON serialization', () => {
    it('all 4 fixtures are JSON-serializable', () => {
        const fixtures = [makeCompleteFixture(), makeEmptyFixture(), makePartialFailureFixture(), makeConflictFixture()]
        for (const f of fixtures) {
            const json = JSON.stringify(f)
            expect(json).toBeTruthy()
            const parsed = JSON.parse(json) as ResearchEvidenceResponse
            expect(parsed.symbol).toBe(f.symbol)
            expect(parsed.data_status).toBe(f.data_status)
        }
    })
})

describe('[UI-014] research_evidence_center — read_only contract', () => {
    it('all fixtures have read_only=true', () => {
        const fixtures = [makeCompleteFixture(), makeEmptyFixture(), makePartialFailureFixture(), makeConflictFixture()]
        for (const f of fixtures) {
            expect(f.read_only).toBe(true)
        }
    })
})
