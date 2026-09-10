// [M-009-R1] watch_pool_independent_source — 只读观察池数据服务测试
import { describe, expect, it } from 'vitest'

import {
    createLatestRequestGuard,
    extractWatchPoolCandidates,
} from '@/services/watchPoolService'
import type { TradeFlowCandidatesResponse } from '@/types'

function makeResponse(
    status: string,
    symbols: Array<{ symbol: string; observe_state: string }>,
): TradeFlowCandidatesResponse {
    return {
        status,
        candidates: symbols.map(s => ({
            symbol: s.symbol,
            name: `测试${s.symbol}`,
            observe_state: s.observe_state,
            strategy_tags: [],
            need_deep_ta: false,
        })) as unknown as TradeFlowCandidatesResponse['candidates'],
    } as unknown as TradeFlowCandidatesResponse
}

describe('[M-009-R1] extractWatchPoolCandidates — 完整观察状态集合', () => {
    it('返回全部候选，不做 observe_state 过滤（含 INVALIDATED/EXPIRED）', () => {
        const res = makeResponse('ok', [
            { symbol: '000001', observe_state: 'WAITING' },
            { symbol: '000002', observe_state: 'TRIGGERED' },
            { symbol: '000003', observe_state: 'INVALIDATED' },
            { symbol: '000004', observe_state: 'EXPIRED' },
        ])
        const items = extractWatchPoolCandidates(res)
        expect(items).toHaveLength(4)
        expect(items.map(i => i.symbol)).toEqual(['000001', '000002', '000003', '000004'])
    })

    it('非 ok 状态返回空集合（空态由视图渲染）', () => {
        expect(extractWatchPoolCandidates(makeResponse('no_data', []))).toEqual([])
        expect(extractWatchPoolCandidates(makeResponse('error', [
            { symbol: '000001', observe_state: 'WAITING' },
        ]))).toEqual([])
    })

    it('契约：观察池提取不接收任何候选页筛选参数', () => {
        // 提取函数签名只有 response——tier/deep_ta/candidate_type/pool/
        // observeFilter 在类型层面就无法进入观察池数据路径
        expect(extractWatchPoolCandidates.length).toBe(1)
    })
})

describe('[M-009-R1] createLatestRequestGuard — 快速切换过期响应守卫', () => {
    it('只有最新序号是当前请求', () => {
        const guard = createLatestRequestGuard()
        const first = guard.next()
        expect(guard.isCurrent(first)).toBe(true)
        const second = guard.next()
        expect(guard.isCurrent(second)).toBe(true)
        expect(guard.isCurrent(first)).toBe(false)
    })

    it('慢响应（旧序号）不得覆盖新响应', () => {
        const guard = createLatestRequestGuard()
        const slow = guard.next()
        const fast = guard.next()
        // 模拟快速切换：旧请求 slow 后返回
        expect(guard.isCurrent(slow)).toBe(false)
        expect(guard.isCurrent(fast)).toBe(true)
    })
})
