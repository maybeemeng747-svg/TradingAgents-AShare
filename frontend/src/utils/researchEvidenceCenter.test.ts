// [UI-014-R1] research_evidence_center — KB-020 真实响应 fixture 驱动的测试
//
// fixture 字段对齐后端真实产出：
//   api/services/research_evidence_service.py
//   ├ matrix_to_ta_consumable_summary      (KB-016 consensus)
//   ├ audit_to_ta_consumable_summary       (KB-017 citation_audit)
//   ├ timeline_to_ta_consumable_summary    (KB-018 thesis_timeline)
//   ├ half_year_facts slim pages           (HY-003)
//   └ snapshot_to_api_dict                 (SCORE-001B)
import { describe, expect, it } from 'vitest'

import type {
    ResearchEvidenceBucket,
    ResearchEvidenceResponse,
} from '@/types'
import {
    buildConsensusViewModel,
    buildCitationAuditViewModel,
    buildThesisTimelineViewModel,
    buildHalfYearFactsViewModel,
    buildScoreSnapshotViewModel,
    initialEvidenceCenterState,
    reduceEvidenceCenter,
    shouldFetchEvidence,
    EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL,
    type EvidenceCenterState,
} from '@/utils/researchEvidenceCenter'

// ── 真实 schema fixtures ────────────────────────────────────────────────────

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
        source_freshness: {},
        gaps: [],
        errors: [],
        read_only: true,
        consensus: makeBucket({
            bucket: 'consensus',
            task: 'KB-016',
            summary: {
                has_hit: true,
                consensus_score: 0.72,
                disagreement_score: 0.18,
                attention_count_effective: 5,
                dominant_stance: '偏多',
                needs_fact_check: true,
                fact_check_priority: 'medium',
                fact_check_reasons: ['2 条待验证声明'],
                dimensions_brief: {
                    performance: { disagreement_score: 0.1, dominant_stance: '偏多', bullish_count: 4, bearish_count: 1 },
                    valuation: { disagreement_score: 0.3, dominant_stance: '中性' },
                },
                consensus_summary: '近 12 个月主流研报整体偏多。',
            },
        }),
        citation_audit: makeBucket({
            bucket: 'citation_audit',
            task: 'KB-017',
            summary: {
                task: 'KB-017',
                citation_audit_status: 'SUPPORTED_WITH_GAPS',
                counts: { supported: 3, weakened: 1, contradicted: 0, pending: 2, insufficient_data: 1 },
                total_claim_count: 7,
                checked_claim_count: 5,
                weak_source_override_blocked_count: 1,
                needs_tree_work_review: true,
                fact_period: '2025H1',
                fact_source_tier: 'strong',
                fact_data_status: 'fresh',
                audit_summary: ['营收声明与半年报一致', '毛利率声明证据不足'],
            },
        }),
        thesis_timeline: makeBucket({
            bucket: 'thesis_timeline',
            task: 'KB-018',
            summary: {
                has_hit: true,
                consensus_drift_score: 0.12,
                total_versions: 9,
                effective_versions: 6,
                reversed_versions: 1,
                weakened_versions: 1,
                reinforced_versions: 2,
                stale_versions: 1,
                pending_fact_check_versions: 1,
                weak_source_overlay_count: 0,
                theses_brief: [
                    {
                        thesis_key: 'premium-liquor-demand',
                        theme: 'consumption_upgrade',
                        theme_label: '消费升级',
                        base_direction: '看多',
                        current_direction: '看多',
                        consensus_drift_score: 0.05,
                        version_count_effective: 4,
                        reversed_count: 0,
                        weakened_count: 1,
                        reinforced_count: 2,
                        stale_count: 0,
                        pending_fact_check_count: 0,
                        weak_source_overlay_count: 0,
                        first_version_path: 'wiki/investment/a.md',
                        latest_version_path: 'wiki/investment/b.md',
                        first_report_date: '2026-01-10',
                        latest_report_date: '2026-07-01',
                    },
                ],
                timeline_summary: '核心逻辑保持看多，期间一次减弱后重新强化。',
            },
        }),
        half_year_facts: makeBucket({
            bucket: 'half_year_facts',
            task: 'HY-003',
            // [UI-014-R1] latest_period / latest_disclosure_date 在 bucket 层
            latest_period: '2025H1',
            latest_disclosure_date: '2026-08-29',
            summary: {
                pages: [
                    {
                        rel_path: 'wiki/investment/600519-2025H1.md',
                        title: '贵州茅台600519-2025H1半年报',
                        financial_period: '2025H1',
                        disclosure_date: '2026-08-29',
                        source_type: ['exchange_filing', 'fact_table'],
                        data_status: 'fresh',
                        metric_count: 4,
                        metric_keys: ['revenue', 'net_profit', 'gross_margin', 'operating_cash_flow'],
                    },
                ],
                summary_lines: ['[2025H1] 营收 435.8亿'],
                risks: ['上游供应风险'],
            },
        }),
        research_score_snapshot: makeBucket({
            bucket: 'research_score_snapshot',
            task: 'SCORE-001',
            summary: {
                status: 'HAS_DATA',
                snapshot_id: 'snap-600519-20260723',
                schema_version: '1.2',
                rubric_id: 'zcode-research-rubric',
                rubric_version: '1',
                degradation_reasons: [],
                validation_warnings: [],
                snapshot: {
                    snapshot_id: 'snap-600519-20260723',
                    symbol: '600519.SH',
                    name: '贵州茅台',
                    as_of: '2026-07-23',
                    status: 'FRESH',
                    scores: { research_evidence_confidence: 0.8, thesis_quality: 0.65 },
                    theses_summary: [
                        {
                            thesis_id: 't1',
                            topic: '高端白酒需求',
                            direction: '看多',
                            status: 'active',
                            core_hypothesis: '批价企稳支撑全年业绩。',
                        },
                    ],
                    evidence_refs_summary: [],
                    missing_evidence: ['缺少 Q2 现金流交叉验证'],
                    upgrade_conditions: [],
                    downgrade_conditions: [],
                    invalidation_conditions: [],
                    score_change_summary: { previous_snapshot_id: 'snap-prev', reasons: ['新增半年报事实'] },
                    warnings: [],
                },
            },
        }),
    }
}

function makePartialFailureFixture(): ResearchEvidenceResponse {
    const base = makeCompleteFixture()
    return {
        ...base,
        data_status: 'stale',
        gaps: ['citation_audit: bucket failed — see errors'],
        consensus: makeBucket({
            bucket: 'consensus',
            task: 'KB-016',
            data_status: 'failed',
            summary: {},
            errors: ['consensus: RuntimeError: knowledge root missing'],
        }),
    }
}

function makeEmptyFixture(): ResearchEvidenceResponse {
    const base = makeCompleteFixture()
    const emptyBucket = (bucket: string, task: string): ResearchEvidenceBucket => ({
        bucket,
        status: 'NORMAL_NO_DATA',
        task,
        has_hit: false,
        data_status: 'missing',
        errors: [],
        summary: {},
    })
    return {
        ...base,
        data_status: 'missing',
        consensus: emptyBucket('consensus', 'KB-016'),
        citation_audit: emptyBucket('citation_audit', 'KB-017'),
        thesis_timeline: emptyBucket('thesis_timeline', 'KB-018'),
        half_year_facts: emptyBucket('half_year_facts', 'HY-003'),
        research_score_snapshot: emptyBucket('research_score_snapshot', 'SCORE-001'),
    }
}

// ── 视图模型：真实 schema 完整 fixture ──────────────────────────────────────

describe('[UI-014-R1] buildConsensusViewModel — KB-016 真实 schema', () => {
    const summary = makeCompleteFixture().consensus.summary

    it('读取真实字段（consensus_score/dominant_stance/attention_count_effective）', () => {
        const vm = buildConsensusViewModel(summary)
        const labels = vm.chips.map(c => c.label)
        expect(labels).toContain('共识分')
        expect(labels).toContain('分歧分')
        expect(labels).toContain('有效关注')
        expect(labels).toContain('主导立场')
        expect(vm.chips.find(c => c.label === '有效关注')?.value).toBe('5')
    })

    it('dimensions_brief 渲染维度（中文标签与多空计数；缺失值不编造）', () => {
        const vm = buildConsensusViewModel(summary)
        expect(vm.dimensions.map(d => d.label)).toContain('业绩预测')
        const perf = vm.dimensions.find(d => d.key === 'performance')
        expect(perf?.bullishCount).toBe(4)
        expect(perf?.bearishCount).toBe(1)
        expect(perf?.dominantStance).toBe('偏多')
        const valuation = vm.dimensions.find(d => d.key === 'valuation')
        expect(valuation?.bullishCount).toBeNull()
    })

    it('needs_fact_check 生成警告并携带原因', () => {
        const vm = buildConsensusViewModel(summary)
        expect(vm.factCheckWarning).toContain('事实核查')
        expect(vm.factCheckWarning).toContain('2 条待验证声明')
    })

    it('consensus_summary 文本透传；部分失败 bucket 不编造数据', () => {
        expect(buildConsensusViewModel(summary).summaryText).toContain('整体偏多')
        const vm = buildConsensusViewModel(makePartialFailureFixture().consensus.summary)
        expect(vm.chips).toEqual([])
        expect(vm.dimensions).toEqual([])
        expect(vm.summaryText).toBeNull()
        expect(vm.factCheckWarning).toBeNull()
    })
})

describe('[UI-014-R1] buildCitationAuditViewModel — KB-017 真实 schema', () => {
    const summary = makeCompleteFixture().citation_audit.summary

    it('读取 total_claim_count/checked_claim_count（非臆造的 total_claims）', () => {
        const vm = buildCitationAuditViewModel(summary)
        expect(vm.chips.find(c => c.label === '总声明')?.value).toBe('7')
        expect(vm.chips.find(c => c.label === '已核查')?.value).toBe('5')
    })

    it('counts 字典渲染各状态（仅 >0 项；弱源拦截计入）', () => {
        const vm = buildCitationAuditViewModel(summary)
        const labels = vm.chips.map(c => c.label)
        expect(labels).toContain('已证实')
        expect(labels).toContain('被削弱')
        expect(labels).toContain('待验证')
        expect(labels).not.toContain('有矛盾')
        expect(labels).toContain('弱源拦截')
    })

    it('audit_summary 行、审计状态行与 Tree Work 复核警告', () => {
        const vm = buildCitationAuditViewModel(summary)
        expect(vm.auditLines).toHaveLength(2)
        expect(vm.needsReviewWarning).toContain('Tree Work')
        expect(vm.statusLine).toContain('SUPPORTED_WITH_GAPS')
    })
})

describe('[UI-014-R1] buildThesisTimelineViewModel — KB-018 真实 schema', () => {
    it('读取版本统计与 theses_brief（非臆造的 timeline[]）', () => {
        const vm = buildThesisTimelineViewModel(makeCompleteFixture().thesis_timeline.summary)
        const labels = vm.chips.map(c => c.label)
        expect(labels).toContain('总版本')
        expect(labels).toContain('反转')
        expect(labels).toContain('共识漂移')
        expect(vm.theses).toHaveLength(1)
        expect(vm.theses[0].themeLabel).toBe('消费升级')
        expect(vm.theses[0].currentDirection).toBe('看多')
        expect(vm.theses[0].latestVersionPath).toBe('wiki/investment/b.md')
        expect(vm.summaryText).toContain('重新强化')
    })
})

describe('[UI-014-R1] buildHalfYearFactsViewModel — HY-003 真实 schema', () => {
    it('latest_period/latest_disclosure_date 从 bucket 层读取', () => {
        const vm = buildHalfYearFactsViewModel(makeCompleteFixture().half_year_facts)
        expect(vm.latestPeriod).toBe('2025H1')
        expect(vm.latestDisclosure).toBe('2026-08-29')
        expect(vm.pages).toHaveLength(1)
        expect(vm.pages[0].metricKeys).toContain('operating_cash_flow')
    })
})

describe('[UI-014-R1] buildScoreSnapshotViewModel — SCORE-001B 真实 schema', () => {
    it('scores/theses 嵌套在 snapshot 内（非 summary 顶层）', () => {
        const vm = buildScoreSnapshotViewModel(makeCompleteFixture().research_score_snapshot.summary)
        expect(vm.hasSnapshot).toBe(true)
        expect(vm.scoreChips.map(c => c.label)).toEqual(['证据可信度', '逻辑质量'])
        expect(vm.scoreChips[0].value).toBe('0.80')
        expect(vm.theses[0].topic).toBe('高端白酒需求')
        expect(vm.missingEvidence).toContain('缺少 Q2 现金流交叉验证')
        expect(vm.scoreChangeReasons).toContain('新增半年报事实')
    })

    it('snapshot=null 显示暂无正式快照且不编造', () => {
        const vm = buildScoreSnapshotViewModel({ status: 'NORMAL_NO_DATA', snapshot: null })
        expect(vm.hasSnapshot).toBe(false)
        expect(vm.scoreChips).toEqual([])
        expect(vm.theses).toEqual([])
    })
})

// ── 请求状态机 ──────────────────────────────────────────────────────────────

function loadedAState(): EvidenceCenterState {
    let s = initialEvidenceCenterState('600519.SH')
    s = reduceEvidenceCenter(s, { type: 'load_started' })
    s = reduceEvidenceCenter(s, {
        type: 'load_succeeded',
        symbol: '600519.SH',
        response: makeCompleteFixture(),
    })
    return s
}

describe('[UI-014-R1] 证据中心请求状态机', () => {
    it('展开触发加载：idle→loading→loaded，且不重复发起', () => {
        let s = initialEvidenceCenterState('600519.SH')
        expect(shouldFetchEvidence(s, true)).toBe(true)
        s = reduceEvidenceCenter(s, { type: 'load_started' })
        expect(shouldFetchEvidence(s, true)).toBe(false)
        s = reduceEvidenceCenter(s, {
            type: 'load_succeeded',
            symbol: '600519.SH',
            response: makeCompleteFixture(),
        })
        expect(s.phase).toBe('loaded')
        expect(s.data?.symbol).toBe('600519.SH')
        expect(shouldFetchEvidence(s, true)).toBe(false)
    })

    it('symbol 变化清空旧数据（A 已加载 → 切 B 后无数据、重新可加载）', () => {
        const sA = loadedAState()
        const sB = reduceEvidenceCenter(sA, { type: 'symbol_changed', symbol: '000858.SZ' })
        expect(sB.symbol).toBe('000858.SZ')
        expect(sB.data).toBeNull()
        expect(sB.error).toBeNull()
        expect(sB.phase).toBe('idle')
        expect(sB.attempts).toBe(0)
        expect(shouldFetchEvidence(sB, true)).toBe(true)
    })

    it('A→B 切换后 A 的慢响应被丢弃，不污染 B', () => {
        let s = initialEvidenceCenterState('600519.SH')
        s = reduceEvidenceCenter(s, { type: 'load_started' }) // A 请求发出
        s = reduceEvidenceCenter(s, { type: 'symbol_changed', symbol: '000858.SZ' }) // 切 B
        s = reduceEvidenceCenter(s, { type: 'load_started' }) // B 请求发出
        s = reduceEvidenceCenter(s, {
            type: 'load_succeeded',
            symbol: '600519.SH',
            response: makeCompleteFixture(),
        }) // A 的慢响应到达
        expect(s.symbol).toBe('000858.SZ')
        expect(s.data).toBeNull() // A 的数据不得展示在 B 名下
        const bFixture = { ...makeCompleteFixture(), symbol: '000858.SZ' }
        s = reduceEvidenceCenter(s, { type: 'load_succeeded', symbol: '000858.SZ', response: bFixture })
        expect(s.phase).toBe('loaded')
        expect(s.data?.symbol).toBe('000858.SZ')
    })

    it('失败后稳定 error，不自动重试；手动重试受上限约束', () => {
        let s = initialEvidenceCenterState('600519.SH')
        s = reduceEvidenceCenter(s, { type: 'load_started' })
        s = reduceEvidenceCenter(s, { type: 'load_failed', symbol: '600519.SH', message: '网络错误' })
        expect(s.phase).toBe('error')
        expect(s.error).toBe('网络错误')
        // error 相位绝不自动重新发起
        expect(shouldFetchEvidence(s, true)).toBe(false)

        while (s.attempts < EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL) {
            s = reduceEvidenceCenter(s, { type: 'retry_clicked' })
            expect(s.phase).toBe('idle')
            s = reduceEvidenceCenter(s, { type: 'load_started' })
            s = reduceEvidenceCenter(s, { type: 'load_failed', symbol: '600519.SH', message: '网络错误' })
        }
        expect(s.attempts).toBe(EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL)
        s = reduceEvidenceCenter(s, { type: 'retry_clicked' })
        expect(s.phase).toBe('error')
        expect(shouldFetchEvidence(s, true)).toBe(false)
    })

    it('非 loading 相位的迟到失败被丢弃', () => {
        let s = initialEvidenceCenterState('600519.SH')
        s = reduceEvidenceCenter(s, { type: 'load_started' })
        s = reduceEvidenceCenter(s, {
            type: 'load_succeeded',
            symbol: '600519.SH',
            response: makeCompleteFixture(),
        })
        s = reduceEvidenceCenter(s, { type: 'load_failed', symbol: '600519.SH', message: '迟到失败' })
        expect(s.phase).toBe('loaded')
    })

    it('卸载后事件全部丢弃', () => {
        let s = initialEvidenceCenterState('600519.SH')
        s = reduceEvidenceCenter(s, { type: 'load_started' })
        s = reduceEvidenceCenter(s, { type: 'unmounted' })
        s = reduceEvidenceCenter(s, {
            type: 'load_succeeded',
            symbol: '600519.SH',
            response: makeCompleteFixture(),
        })
        expect(s.phase).toBe('loading')
        expect(s.data).toBeNull()
    })
})
