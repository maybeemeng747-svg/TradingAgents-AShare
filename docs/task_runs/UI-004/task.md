# UI-004: 盘中 Observe 与 TA 队列只读面板

## 目标
在 TradeFlow 页面增加"盘中观察"和"TA 队列"Tab，展示候选状态、触发价/失效价、TA 调度状态和成本门禁原因。

## 约束
- 第一版只读，不自动触发 TA
- 不调用 DeepSeek，不绕过 TA 预算门禁
- 代码标注：`// [UI-004] tradeflow_observe_queue`

## 实现

### 1. 修改 TradeFlow.tsx
在候选池表格上方增加 Tab 切换：
- Tab 1: 候选池（已有）
- Tab 2: 盘中观察
- Tab 3: TA 队列

### 2. 盘中观察 Tab
- 调用 getTradeFlowObserve(date)
- 表格列：Symbol | Name | 状态 | 触发价 | 失效价 | 当前价 | 触发距离 | 触发次数 | 首次触发时间
- 状态颜色：WAITING=灰, TRIGGERED=绿, EXPIRED=黄, INVALIDATED=红
- 无实时价格时显示"实时行情不可用"

### 3. TA 队列 Tab
- 调用 getTradeFlowTaQueue(date)
- 表格列：Symbol | Name | Tier | 优先级 | TA状态 | 调度原因 | 模型 | 完整度
- TA状态颜色：pending=灰, running=蓝, completed=绿, failed=红, skipped=黄
- 显示今日 TA 调度数量、上限、剩余额度

### 4. 验证
```bash
cd frontend && npm run build
```

## 文件变更
- `frontend/src/pages/TradeFlow.tsx`（修改）
