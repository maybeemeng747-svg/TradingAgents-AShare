# UI-002: 前端 TradeFlow 页面骨架与候选池表格

## 目标
新增 `/tradeflow` 路由和页面，展示 TradeFlow 摘要卡片和候选池表格。

## 约束
- 只做只读展示，不做一键交易
- 不展示"立即买入/重仓/清仓"等强动作词
- 设计风格保持现有后台工具风格：信息密度高、少装饰

## 实现步骤

### 1. 新增 TradeFlow 类型
在 `frontend/src/types/index.ts` 末尾新增 TradeFlow 相关类型（参考 `api/tradeflow_schemas.py`）：
- TradeflowCandidateSummary
- TradeflowCandidateDetail
- TradeflowDailyPlan
- TradeflowObserveItem
- TradeflowTaQueueItem
- TradeflowReviewSummary
- TradeflowDataHealthItem

### 2. 新增 API Client 方法
在 `frontend/src/services/api.ts` 的 ApiService 类中新增方法：
- getTradeFlowDailyPlan(date: string)
- getTradeFlowCandidates(date: string, tier?: string, needDeepTa?: boolean)
- getTradeFlowCandidateDetail(symbol: string, date: string)
- getTradeFlowObserve(date: string)
- getTradeFlowTaQueue(date: string)
- getTradeFlowReview(date: string)
- getTradeFlowDataHealth()

### 3. 新增侧边栏入口
在 `frontend/src/components/sidebarNav.ts` 中新增：
```typescript
{ path: '/tradeflow', icon: Target, label: 'TradeFlow' },
```
(用 lucide-react 的 Target 或 Radar 或 Crosshair 图标)

### 4. 新增 TradeFlow 页面
创建 `frontend/src/pages/TradeFlow.tsx`：

页面结构：
- **顶部摘要栏**：4-6 个小卡片
  - 总候选数、A/B/C 层数量、need_deep_ta 数量、平均完整度、事件源状态
- **候选池表格**：主表格
  - 列：Symbol | Name | Tier | Score | 策略标签 | 触发价 | 失效价 | 完整度 | need_deep_ta | 观察状态 | 动作
  - 策略标签用 Badge/Tag 组件
  - Tier 用颜色区分（A=绿, B=黄, C=红）
  - 支持按 tier、策略标签、need_deep_ta、observe_state 筛选
- **日期选择器**：默认今天，可切换日期
- **loading/empty/error 状态完整**

使用现有的 UI 组件库（shadcn/ui 或项目已有的组件）。

### 5. 注册路由
在 `frontend/src/App.tsx` 的 Routes 中新增：
```tsx
<Route path="/tradeflow" element={<TradeFlow />} />
```

### 6. 验证
```bash
cd frontend && npm run build
cd frontend && npm test (if tests exist)
```

## 文件变更范围
- `frontend/src/types/index.ts`（新增类型）
- `frontend/src/services/api.ts`（新增 API 方法）
- `frontend/src/components/sidebarNav.ts`（新增导航项）
- `frontend/src/pages/TradeFlow.tsx`（新建）
- `frontend/src/App.tsx`（新增路由）
