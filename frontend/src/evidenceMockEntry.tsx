// [UI-014-R1] 离线 mock UI 验收入口：KB-020 真实 schema mock 数据渲染
// ResearchEvidenceCenter。不调用任何 API。验收截图用开发辅助文件。
import { createRoot } from 'react-dom/client'

import './index.css'
import ResearchEvidenceCenter from './components/ResearchEvidenceCenter'
import { api } from './services/api'
import type {
    ResearchEvidenceBucket,
    ResearchEvidenceResponse,
} from './types'

// 后端 bucket 为自由 dict（latest_period 等额外键在类型之外），故 overrides
// 放宽为 Record<string, unknown>
function makeBucket(overrides: { summary: Record<string, unknown> } & Record<string, unknown>): ResearchEvidenceBucket {
    return {
        bucket: 'test',
        status: 'HAS_DATA',
        task: 'TEST',
        has_hit: true,
        data_status: 'fresh',
        errors: [],
        ...overrides,
    } as unknown as ResearchEvidenceBucket
}

// 与 researchEvidenceCenter.test.ts 同源的 KB-020 真实 schema fixture
const fixture: ResearchEvidenceResponse = {
    source: 'research_evidence',
    task: 'KB-020',
    symbol: '600519.SH',
    as_of: '2026-07-23 12:00:00',
    data_status: 'fresh',
    vendor: 'tree_work_wiki',
    endpoint: 'wiki/investment',
    knowledge_root: '/Users/maybee/Documents/knowledge',
    query: { symbol: '600519.SH', window_months: 12 },
    source_freshness: {},
    gaps: ['research_score_snapshot: stale — refresh recommended'],
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
            consensus_summary: '近 12 个月主流研报整体偏多，分歧集中在估值假设。',
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
                    latest_version_path: 'wiki/investment/600519-2026-07.md',
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
        data_status: 'stale',
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

// 离线拦截：组件懒加载调用的 api.getResearchEvidence 在 mock 页内
// 直接返回真实 schema fixture，不发任何网络请求。
api.getResearchEvidence = async () => fixture

function MockApp() {
    return (
        <div className="min-h-screen bg-slate-50 p-4 dark:bg-slate-900">
            <div className="mb-3 rounded bg-amber-100 px-3 py-2 text-xs text-amber-800">
                [UI-014-R1] 离线 mock：KB-020 真实 schema fixture 驱动（API 已在
                页面内拦截），展开后渲染 5 个 bucket。
            </div>
            <ResearchEvidenceCenter symbol="600519.SH" />
        </div>
    )
}

createRoot(document.getElementById('root')!).render(<MockApp />)
