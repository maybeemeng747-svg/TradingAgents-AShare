# TradeFlow 5000 元小资金试跑 5 日操作手册 v3 (V-012)

**文档版本**: v3.0
**生成时间**: 2026-07-01
**对应任务**: V-012（5 日纸面交易/模拟观察回放验收 + 人工操作手册 v3）
**配套验收报告**: `docs/tradeflow_trial_5day_replay_acceptance.md`
**配套回放测试**: `tests/test_v012_paper_trial_5day_replay.py`
**适用对象**: 准备拿约 ¥5000 试跑一周（5 个交易日）TradeFlow 模拟流程的用户
**模拟本金默认值**: ¥5000

> 本手册在 v1（V-009 单日手册）基础上，补充「一周 5 日盘前/盘中/盘后逐日指引」与
> 「风险预算与失败退出条件」两节，回答用户「这一周每天到底看什么、点什么、复盘什么」。

---

## 合规与免责声明（请先阅读）

- 本手册描述的全部动作均为**模拟 / 观察**，**不是实盘交易建议**，**不连接真实交易通道**。
- 模拟账户仅供学习研究，**不构成任何投资建议**，**不输出收益承诺**。
- 所有触发 / 模拟进场 / 模拟出场均需用户**人工确认**，系统不会自动下单。
- 观察池（observation 状态）候选**不能**直接 confirm 模拟进场，避免隐性自动交易。
- 系统只输出**软状态**与结构化事实，使用 `OBSERVE / WAIT_TRIGGER / NEED_DEEP_TA / REMOVE_FROM_WATCH`
  这类中性动作标签，不输出强买卖词。

---

## 0. 前置准备

| 项 | 要求 |
|----|------|
| 后端 | `uvicorn api.main:app`（或项目惯用启动命令）已启动 |
| 前端 | `npm run dev`（位于 `frontend/`）已启动，能打开 `/tradeflow` 页面 |
| 数据库 | 使用本地 TradeFlow SQLite（如 `tradeflow.db`），**禁止写生产 `tradingagents.db`** |
| LLM | 试跑链路**不调用 LLM**，无需 API Key |
| 行情 | 盘中观察支持 mock 行情或真实行情；首次试跑建议用 fixture，避免限流 |

**建议起点**：周末 / 节假日 / 盘后生成候选池，系统会自动把 `effective_trade_date` 推到下一个
A 股交易日。这样可以在非交易日完成「研究 + 计划」，交易日只做观察和确认。

---

## 1. 风险预算与失败退出条件（V-012 核心约束）

> 全周试跑必须遵守以下预算与退出纪律。任何一条被触发，对应候选只能观察、不能模拟进场。

### 1.1 单票与仓位预算（默认值，来自 `_DEFAULT_RISK_BUDGET`）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `principal` | ¥5000 | 模拟本金 |
| `per_ticket_max` | ¥1500 | 单票金额上限（超过会被截断） |
| `per_ticket_min` | ¥500 | 单票金额下限（低于会被抬升） |
| `daily_new_max` | 3 | 每日新增候选上限 |
| `max_concurrent_tracking` | 5 | 并发跟踪上限 |
| `require_trigger_price` | true | 必须有触发价 |
| `require_invalid_price` | true | 必须有失效价 |
| `min_data_quality_score` | 40.0 | 数据质量门禁（低于硬拒绝） |

修改配置（仅影响本地模拟账本，不影响真实资金）：
```http
POST /v1/tradeflow/paper-ledger/config
{"risk_budget": {"principal": 8000, "per_ticket_max": 2000}}
```

### 1.2 三类拒绝 / 降级

| 类型 | 触发条件 | 结果 |
|------|----------|------|
| **硬拒绝** | 缺触发价 / 缺失效价 / `data_quality_score < 40` | 候选**不进**账本，返回 `status="rejected"` + `rule` |
| **金额截断** | `planned_amount > per_ticket_max` | 截断到 `per_ticket_max`，仍可模拟进场 |
| **软降级** | 超并发上限 / 超每日新增 / 超剩余预算 | 降级为 `observation`，**不能** confirm 模拟进场 |

### 1.3 失败退出条件（全周止损纪律）

| 条件 | 阈值 | 动作 |
|------|------|------|
| **单票失效退出** | 价格 ≤ `invalid_price` | `observe_state=INVALIDATED`，候选标记 invalidated，不再模拟进场 |
| **整体失败退出** | 全周 realized P&L ≤ 本金 × (−10%) = **−¥500** | 停止本周新增模拟进场，只做观察与复盘 |
| **数据缺口退出** | 报告命中 severe `data_blockers` | 进入 `suppress_push_data_insufficient`，不作强结论推送 |

> 验收要求：5 日回放中全周 realized P&L 必须始终 > −¥500，未触发整体失败退出。

---

## 2. 一周 5 日逐日操作指引

> 下表是一份「标准一周剧本」。每天分**盘前 / 盘中 / 盘后**三段，对应 v1 手册里的候选池、
> 盘中观察、盘后 Review 三个 tab。验收回放（`tests/test_v012_paper_trial_5day_replay.py`）
> 完整覆盖这 5 天。

| 交易日 | 盘前（看什么） | 盘中（点什么） | 盘后（复盘什么） |
|--------|----------------|----------------|------------------|
| **Day1 周一** | 周末候选池今日生效；看昊天主候选 + 技术主候选（分层压缩后 ≤ 2 只） | 「执行一次观察」；价格 ≥ 触发价 → TRIGGERED | 触发候选加入模拟账本 → 人工确认模拟进场；生成 Review |
| **Day2 周二** | 昨日未触发候选顺延；查看风险敞口（invested / remaining） | 继续执行观察；第二只候选触发 | 第二笔模拟进场；确认双仓占用 < 100% 预算 |
| **Day3 周三** | 新候选进池，重点看失效价 | 执行观察；价格 ≤ 失效价 → INVALIDATED | **失败退出**：invalidated 候选不再进场；可对盈利仓模拟出场 |
| **Day4 周四** | 数据缺口候选尝试进场（会被门禁拒绝） | 数据不足不触发观察 | 检查硬拒绝原因（`rule`/`reason`）；金额截断案例 |
| **Day5 周五** | 查看本周遗留仓位 | 无观察（review / exit 为主） | 全部盈利仓模拟出场；**全周 Review + 风险预算回顾** |

---

## 3. 逐日详细操作

### Day 1（周一）—— 首次触发与首次模拟进场

**盘前**
1. 打开 `/tradeflow` →「候选池」tab，确认 `plan_date`（周末）与 `effective_trade_date`（今日）。
2. 用 `GET /v1/tradeflow/candidates/tiered?date=今日` 查看分层压缩结果：
   - `actionable / watch` 档为主候选（≤ 配置上限）。
   - 名称显示中文名，`candidate_type` 非空。
3. 确认池门禁：`pool_gate_summary` 显示通过原因，证据缺口候选在 `scan` 档。

**盘中**
1. 切换到「盘中观察」tab，点「执行一次观察」（`POST /v1/tradeflow/observe/run?date=今日`）。
2. 查看 `observe_state`：价格 ≥ 触发价 → `TRIGGERED`；触发信号写入 `tradeflow_signals`。

**盘后**
1. 触发候选点「加入模拟账本」（`POST /v1/tradeflow/paper-ledger/add`），带入触发价/失效价。
2. 在「模拟账本」对 `pending`（待确认）候选点「确认模拟进场」（`action_type=buy`）。
3. 生成今日 Review（`POST /v1/tradeflow/review/generate?date=今日`），查看 `attribution_stats`。

### Day 2（周二）—— 第二笔模拟进场与预算跟踪

**盘前**
1. 查看模拟账本「风险敞口」：`invested` / `remaining` / `budget_utilization_pct`。
2. 昨日未触发候选今日继续观察（`effective_trade_date` 顺延或重新生成）。

**盘中**
1. 执行观察；第二只主候选触发 → TRIGGERED。

**盘后**
1. 第二笔模拟进场（buy）。
2. 确认 `budget_utilization_pct < 100%`；若超并发/每日上限，候选被降级为 `observation`（不能进场）。

### Day 3（周三）—— 失败退出（跌破失效价）

**盘前**
1. 重点看新候选的 `invalid_price`（失效价），这是失败退出的触发线。

**盘中**
1. 执行观察；若价格 ≤ `invalid_price` → `observe_state=INVALIDATED`。

**盘后**
1. **失败退出**：invalidated 候选自动标记，**不再**模拟进场（`_sync_paper_from_observe` 同步）。
2. 可对已盈利的 open 仓位人工确认模拟出场（`action_type=sell`），记录 `realized_pnl`。
3. 检查整体失败退出线：全周 realized P&L 是否 > −¥500。

### Day 4（周四）—— 风险门禁硬拒绝与金额截断

**盘前**
1. 尝试把数据缺口候选（`data_quality_score < 40`）加入账本，预期被硬拒绝。

**盘中**
1. 数据不足候选不参与观察触发（无可靠信号）。

**盘后**
1. 检查拒绝原因：`status="rejected"`、`rule="require_trigger_price"` 或 `"min_data_quality_score"`。
2. 验证金额截断：`planned_amount=9999` → 返回 `planned_amount=1500`（`per_ticket_max`）。
3. 把被拒绝候选加入**观察仓**（不进模拟账本），等待数据补齐。

### Day 5（周五）—— 全周复盘与风险预算回顾

**盘前**
1. 查看本周遗留的 open / tracking 仓位。

**盘中**
1. 以 review / exit 为主，通常不再新增观察。

**盘后**
1. 对剩余盈利仓全部模拟出场（sell）。
2. **全周 Review**：对 5 个交易日分别 `generate_review`，汇总 `attribution_stats`。
3. **风险预算回顾**（对照第 1 节）：
   - 每日新增 ≤ 3？并发 ≤ 5？单票 ≤ 1500？
   - 全周 realized P&L > −¥500（未触发整体失败退出）？
   - 无遗留 open 仓位？
4. 确认全程无强买卖词、`runtime_tier` 未升级到 FULL_TA。

---

## 4. 试跑四问速查（V-012 验收口径）

| 问题 | 回答来源 |
|------|----------|
| **今天看哪几只？** | `get_candidates_tiered` → `main_candidates`（actionable/watch 档） |
| **为什么是这几只？** | `precision_dimensions` + `ranking_reasons` + `weakness_reasons` + 池门禁 |
| **何时触发 / 何时退出？** | 盘中 observe：≥ 触发价 TRIGGERED；≤ 失效价 INVALIDATED；全周 P&L ≤ −10% 整体退出 |
| **盘后如何处理？** | `generate_review` 归因 + 次日反馈；模拟账本确认进/出场；风险预算回顾 |

---

## 5. 安全边界速记

- **不调用 LLM**：试跑链路全程 fixture / mock / 规则引擎。
- **不写生产数据库**：所有试跑使用本地 `tradeflow.db`，不写 `tradingagents.db`。
- **不接真实交易**：所有进/出场均为模拟，需人工确认。
- **不自动下单**：观察池候选无法 confirm buy。
- **不输出强买卖词**：仅使用 `OBSERVE / WAIT_TRIGGER / NEED_DEEP_TA / REMOVE_FROM_WATCH`。
- **不跑全市场扫描**：候选池基于 watchlist + 事件源。
- **不在非交易日拉实时行情**：observe runner 自动 skip。
- **不放宽失败退出线**：−10% 整体止损纪律由验收测试守护。

---

## 6. API 路由速查表

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/tradeflow/discovery` | 生成候选池 |
| GET | `/v1/tradeflow/candidates` | 候选列表 |
| GET | `/v1/tradeflow/candidates/tiered` | 分层压缩（主候选） |
| GET | `/v1/tradeflow/observe` | 盘中观察列表 |
| POST | `/v1/tradeflow/observe/run` | 手动触发一次观察扫描 |
| POST | `/v1/tradeflow/review/generate` | 生成盘后 Review |
| GET | `/v1/tradeflow/review` | Review 查询 |
| GET | `/v1/tradeflow/paper-ledger` | 模拟账本 / 风险敞口 |
| POST | `/v1/tradeflow/paper-ledger/config` | 修改风险预算配置 |
| POST | `/v1/tradeflow/paper-ledger/add` | 加入模拟账本（含风险预算校验） |
| POST | `/v1/tradeflow/paper-ledger/confirm` | 人工确认模拟动作（buy/sell） |
| GET | `/v1/tradeflow/paper-ledger/review` | 账本复盘 |
| GET | `/v1/dashboard/tracking-board/v2` | 跟踪看板 v2 分组聚合 |
| GET | `/v1/tradeflow/data-health` | 数据源健康 |

---

## 7. 参考文档

- 5 日回放验收报告：`docs/tradeflow_trial_5day_replay_acceptance.md`（V-012）
- 单日验收报告 v2：`docs/tradeflow_trial_acceptance_v3.md`（V-010）
- 单日操作手册 v1：`docs/tradeflow_trial_user_guide.md`（V-009）
- 回归清单：`docs/tradeflow_trial_regression_checklist.md`（V-009 配套）
- 任务定义：`docs/TASKS.md` V-012 / V-010 / V-009 / V-008 / TF-PAPER-001 / TF-RISK-001
