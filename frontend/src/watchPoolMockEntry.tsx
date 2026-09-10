// [M-009-R1] 离线 mock UI 验收入口：纯 mock 数据渲染真实 WatchPoolTab。
// 不调用任何 API（不触发分析/观察执行接口）。验收截图用开发辅助文件。
import { createRoot } from 'react-dom/client'

import './index.css'
import { WatchPoolTab } from './pages/TradeFlow'
import type { TradeFlowCandidateItem, TradeFlowFilteredItem } from '@/types'

// 模拟"候选页带了 tier=A / pool=main / observeFilter=TRIGGERED 筛选"后
// 切到观察池：观察池仍必须显示完整观察状态集合（含 B/C 层、非主池、
// INVALIDATED/EXPIRED）。
const mockCandidates: TradeFlowCandidateItem[] = [
    {
        symbol: '000977',
        name: '浪潮信息',
        observe_state: 'TRIGGERED',
        composite_score: 88.5,
        strategy_tags: ['AI服务器', '放量突破'],
        need_deep_ta: true,
        ta_budget_priority: 9,
        trigger_price: 45.6,
        invalid_price: 42.1,
    },
    {
        symbol: '002594',
        name: '比亚迪',
        observe_state: 'WAITING',
        composite_score: 82.1,
        strategy_tags: ['新能源'],
        need_deep_ta: false,
        trigger_price: 268.0,
        invalid_price: 252.5,
    },
    {
        symbol: '600519',
        name: '贵州茅台',
        observe_state: 'WAITING',
        composite_score: 79.4,
        strategy_tags: ['消费白马'],
        need_deep_ta: false,
        trigger_price: 1520.0,
        invalid_price: 1450.0,
    },
    {
        symbol: '300750',
        name: '宁德时代',
        observe_state: 'INVALIDATED',
        composite_score: 71.2,
        strategy_tags: ['动力电池'],
        need_deep_ta: false,
        trigger_price: 198.0,
        invalid_price: 205.0,
    },
    {
        symbol: '601127',
        name: '赛力斯',
        observe_state: 'EXPIRED',
        composite_score: 66.8,
        strategy_tags: ['华为链'],
        need_deep_ta: false,
        trigger_price: 96.5,
        invalid_price: 91.0,
    },
] as unknown as TradeFlowCandidateItem[]

const mockFiltered: TradeFlowFilteredItem[] = [
    {
        symbol: '688981',
        name: '中芯国际',
        source: 'haotian',
        reason: '流动性不足',
        run_id: 'mock-run-1',
        created_at: new Date().toISOString(),
    },
    {
        symbol: '000012',
        name: '国A科技',
        source: 'scan',
        reason: '数据不完整',
        run_id: 'mock-run-1',
        created_at: new Date().toISOString(),
    },
]

function MockApp() {
    return (
        <div className="min-h-screen bg-slate-50 p-4 dark:bg-slate-900">
            <div className="mb-3 rounded bg-amber-100 px-3 py-2 text-xs text-amber-800">
                [M-009-R1] 离线 mock：候选页筛选（tier=A / pool=main /
                observeFilter=TRIGGERED）后进入观察池，仍显示完整观察状态集合
                （5 只，含 INVALIDATED / EXPIRED）。
            </div>
            <WatchPoolTab
                candidates={mockCandidates}
                filteredItems={mockFiltered}
                onRowClick={() => {}}
                onNavigateToAnalysis={() => {}}
            />
        </div>
    )
}

createRoot(document.getElementById('root')!).render(<MockApp />)
