// [M-009-R1] 只读观察池数据服务
// 观察池必须读取完整观察状态集合：不得继承候选页的 tier / need_deep_ta /
// candidate_type / pool 服务端筛选，也不得套用候选页的 observeFilter 客户端
// 过滤。本模块只做两件事：声明"完整集合"的提取口径，以及提供快速切换
// tab 时的过期响应守卫（先发出的请求后返回不得覆盖新数据）。

import type {
    TradeFlowCandidateItem,
    TradeFlowCandidatesResponse,
} from '@/types'

export type { TradeFlowCandidateItem, TradeFlowCandidatesResponse }

/**
 * [M-009-R1] 提取观察池完整候选集合。
 *
 * 与候选页不同：不做 observe_state 过滤（INVALIDATED/EXPIRED 的展示过滤
 * 由观察池视图自行决定），不读取任何候选页筛选条件。
 */
export function extractWatchPoolCandidates(
    res: TradeFlowCandidatesResponse,
): TradeFlowCandidateItem[] {
    if (res.status !== 'ok') {
        return []
    }
    return res.candidates
}

/**
 * [M-009-R1] 单调请求序号守卫：快速切换 tab/日期时，先发出但后返回的
 * 请求不得覆盖新请求的数据。
 */
export interface LatestRequestGuard {
    next: () => number
    isCurrent: (id: number) => boolean
}

export function createLatestRequestGuard(): LatestRequestGuard {
    let seq = 0
    return {
        next: () => {
            seq += 1
            return seq
        },
        isCurrent: (id: number) => id === seq,
    }
}
