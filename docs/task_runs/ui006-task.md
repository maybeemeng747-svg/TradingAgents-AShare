# UI-006: 数据源健康前端面板（P2）

## 目标
把 M-008/T-007 的数据源健康状态展示到 TradeFlow 前端，让用户能判断候选池质量问题来自策略还是数据源。

## 执行边界
1. 不暴露 API key、cookie、token。
2. 不主动发起高频 live 数据探测，只展示后端已有 health/check 结果。
3. 不调用 LLM，不跑全市场扫描，不触发 TA。
4. 不改 tradingagents/prompts/。
5. 不写生产 tradingagents.db。
6. 不 push。

## 实现要点

### 1. 在 TradeFlow 页面增加"数据健康"Tab
- 在现有 Tab 列表（candidates, observe, ta-queue, review）末尾新增 `data-health` Tab
- Tab label: "数据健康"

### 2. 接入 API
- 调用 UI-001 已有的 `GET /v1/tradeflow/data-health`
- 使用 ApiService 中已有的 `getTradeFlowDataHealth()` 方法

### 3. 数据健康面板内容
展示每个数据源的状态卡片/行：
- 数据源名称（行情、实时行情、资金流、龙虎榜、公告/事件源、财报等）
- 状态（OK/PARTIAL/FAILED/STALE/NOT_QUERIED）
- fallback vendor（如有）
- 最近更新时间
- 失败原因（仅 FAILED/STALE 时显示）
- 记录数

### 4. 状态样式
- OK: 绿色
- PARTIAL: 黄色
- FAILED: 红色
- STALE: 橙色
- NOT_QUERIED: 灰色

### 5. 顶部健康摘要
在候选池顶部（summary cards 区域）增加轻量健康摘要：
- ✅ 数据源全部正常
- ⚠️ 部分数据源异常（N 个）
- ❌ 数据源故障（N 个）
- ℹ️ 今日未生成健康检查

### 6. 空状态
没有健康数据时，不报错，显示"未生成健康检查"。

### 7. 不显示强买卖词
动作字段保持 OBSERVE/WAIT_TRIGGER/NEED_DEEP_TA/REMOVE_FROM_WATCH。

## 现有代码参考
- API 端点: api/main.py 的 `/v1/tradeflow/data-health`
- Response model: api/tradeflow_schemas.py 的 `TradeflowDataHealthItem`
- API client: frontend/src/services/api.ts 的 `getTradeFlowDataHealth()`
- 类型: frontend/src/types/index.ts 的 `TradeflowDataHealthItem`
- 页面: frontend/src/pages/TradeFlow.tsx

## 验证
1. cd frontend && npm run build（零错误）
2. mock OK/PARTIAL/FAILED/STALE/NOT_QUERIED 五种状态均正确展示
3. 空数据时显示"未生成健康检查"
4. git status 干净（仅 UI-006 相关文件）

## 完成后输出
- 修改文件列表
- 测试结果
- 是否需要下一轮 UI 验收任务
