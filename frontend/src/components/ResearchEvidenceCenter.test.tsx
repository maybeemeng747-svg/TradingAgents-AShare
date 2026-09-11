// @vitest-environment jsdom
// [UI-014-R1-fix] 组件级回归：请求生命周期与 loading 状态解耦。
// 审核复现路径：真实网络延迟（200ms）下响应在 cleanup 之后到达，
// 旧实现把响应当"过期"丢弃 → 永远停在"加载中"。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StrictMode } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import ResearchEvidenceCenter from '@/components/ResearchEvidenceCenter'
import { api } from '@/services/api'
import type { ResearchEvidenceResponse } from '@/types'

function deferred() {
    let resolve!: (v: ResearchEvidenceResponse) => void
    let reject!: (e: unknown) => void
    const promise = new Promise<ResearchEvidenceResponse>((res, rej) => {
        resolve = res
        reject = rej
    })
    return { promise, resolve, reject }
}

function makeResponse(symbol: string): ResearchEvidenceResponse {
    return {
        source: 'research_evidence',
        task: 'KB-020',
        symbol,
        as_of: '2026-09-11 12:00:00',
        data_status: 'fresh',
        vendor: 'tree_work_wiki',
        endpoint: 'wiki/investment',
        knowledge_root: '/tmp/knowledge',
        query: { symbol, window_months: 12 },
        source_freshness: {},
        gaps: [],
        errors: [],
        read_only: true,
        consensus: {
            bucket: 'consensus',
            status: 'HAS_DATA',
            task: 'KB-016',
            has_hit: true,
            data_status: 'fresh',
            errors: [],
            summary: {
                has_hit: true,
                consensus_score: 0.7,
                disagreement_score: 0.2,
                attention_count_effective: 3,
                dominant_stance: '偏多',
                needs_fact_check: false,
                dimensions_brief: {},
                consensus_summary: `${symbol} 共识摘要`,
            },
        },
        citation_audit: {
            bucket: 'citation_audit',
            status: 'NORMAL_NO_DATA',
            task: 'KB-017',
            has_hit: false,
            data_status: 'missing',
            errors: [],
            summary: { counts: {}, audit_summary: [] },
        },
        thesis_timeline: {
            bucket: 'thesis_timeline',
            status: 'NORMAL_NO_DATA',
            task: 'KB-018',
            has_hit: false,
            data_status: 'missing',
            errors: [],
            summary: { theses_brief: [] },
        },
        half_year_facts: {
            bucket: 'half_year_facts',
            status: 'NORMAL_NO_DATA',
            task: 'HY-003',
            has_hit: false,
            data_status: 'missing',
            errors: [],
            summary: {},
        },
        research_score_snapshot: {
            bucket: 'research_score_snapshot',
            status: 'NORMAL_NO_DATA',
            task: 'SCORE-001',
            has_hit: false,
            data_status: 'missing',
            errors: [],
            summary: { snapshot: null },
        },
    }
}

function mockApi(impl: (symbol: string) => Promise<ResearchEvidenceResponse>) {
    return vi.fn(impl) as unknown as typeof api.getResearchEvidence
}

afterEach(cleanup)

describe('[UI-014-R1-fix] 研报证据中心请求生命周期（组件级）', () => {
    it('延迟响应（审核 200ms 复现路径）不再卡在加载中', async () => {
        const d = deferred()
        const calls: string[] = []
        api.getResearchEvidence = mockApi((symbol) => {
            calls.push(symbol)
            return d.promise
        })
        render(
            <StrictMode>
                <ResearchEvidenceCenter symbol="600519.SH" />
            </StrictMode>,
        )
        // 展开 → 进入加载
        fireEvent.click(screen.getByRole('button', { name: /研报证据中心/ }))
        expect(await screen.findByText('加载研报证据...')).toBeTruthy()
        // 模拟 200ms 后响应到达（在无任何依赖变化的情形下）
        d.resolve(makeResponse('600519.SH'))
        await waitFor(() => {
            expect(screen.queryByText('加载研报证据...')).toBeNull()
        })
        // 已加载内容出现：不再是 loading
        expect(await screen.findByText('600519.SH 共识摘要')).toBeTruthy()
        expect(calls).toEqual(['600519.SH'])
    })

    it('A→B 切换：A 的慢响应被丢弃，B 正常展示', async () => {
        const pending = new Map<string, ReturnType<typeof deferred>>()
        api.getResearchEvidence = mockApi((symbol) => {
            const d = deferred()
            pending.set(symbol, d)
            return d.promise
        })
        const { rerender } = render(
            <StrictMode>
                <ResearchEvidenceCenter symbol="600519.SH" />
            </StrictMode>,
        )
        fireEvent.click(screen.getByRole('button', { name: /研报证据中心/ }))
        await screen.findByText('加载研报证据...')

        // 切换到 B：B 的新请求发出
        rerender(
            <StrictMode>
                <ResearchEvidenceCenter symbol="000858.SZ" />
            </StrictMode>,
        )
        await waitFor(() => {
            expect(pending.has('000858.SZ')).toBe(true)
        })
        // B 先返回
        pending.get('000858.SZ')!.resolve(makeResponse('000858.SZ'))
        expect(await screen.findByText('000858.SZ 共识摘要')).toBeTruthy()

        // A 的慢响应迟到：不得污染 B
        pending.get('600519.SH')!.resolve(makeResponse('600519.SH'))
        await new Promise((r) => setTimeout(r, 20))
        expect(screen.queryByText('600519.SH 共识摘要')).toBeNull()
        expect(screen.getByText('000858.SZ 共识摘要')).toBeTruthy()
    })

    it('失败稳定展示错误不自动重试；手动重试受上限约束', async () => {
        const calls: string[] = []
        api.getResearchEvidence = mockApi((symbol) => {
            calls.push(symbol)
            return Promise.reject(new Error('网络错误'))
        })
        render(
            <StrictMode>
                <ResearchEvidenceCenter symbol="600519.SH" />
            </StrictMode>,
        )
        fireEvent.click(screen.getByRole('button', { name: /研报证据中心/ }))
        expect(await screen.findByText('网络错误')).toBeTruthy()
        expect(calls.length).toBeGreaterThanOrEqual(1)
        const afterFailure = calls.length
        // 不自动重试：静置后调用数不变
        await new Promise((r) => setTimeout(r, 30))
        expect(calls.length).toBe(afterFailure)

        // 手动重试至上限（首次 + 2 次重试 = 3 次尝试）
        while (screen.queryByText('重新加载')) {
            fireEvent.click(screen.getByText('重新加载'))
            await screen.findByText('网络错误')
        }
        // 上限后按钮消失，出现上限提示，且不再发起新请求
        expect(screen.queryByText('重新加载')).toBeNull()
        expect(screen.getByText(/已达到重试上限/)).toBeTruthy()
        const finalCalls = calls.length
        await new Promise((r) => setTimeout(r, 30))
        expect(calls.length).toBe(finalCalls)
    })
})
