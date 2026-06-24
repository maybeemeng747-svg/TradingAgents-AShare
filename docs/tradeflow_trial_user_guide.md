# TradeFlow 小资金试跑用户操作手册 (V-009)

**文档版本**: v1.0
**生成时间**: 2026-06-24
**对应任务**: V-009（基于 V-008 验收链路）
**适用对象**: 第一次使用 TradeFlow 模拟试跑流程的用户
**模拟本金默认值**: ¥5000

---

## 合规与免责声明（请先阅读）

- 本手册描述的全部动作均为**模拟 / 观察**，**不是实盘交易建议**。
- 模拟账户仅供学习研究，**不构成任何投资建议**，**不连接真实交易通道**。
- 所有触发 / 模拟进场 / 模拟出场动作均需用户**人工确认**，系统不会自动下单。
- 观察池（observation 状态）候选**不能**直接 confirm 模拟进场，避免隐性自动交易。
- 系统只输出**软状态**与结构化事实，使用 `OBSERVE / WAIT_TRIGGER / NEED_DEEP_TA / REMOVE_FROM_WATCH` 这类中性动作标签，不输出强动作词。

---

## 0. 前置准备

| 项 | 要求 |
|----|------|
| 后端 | `uvicorn api.main:app`（或项目惯用启动命令）已启动 |
| 前端 | `npm run dev`（位于 `frontend/`）已启动，能打开 `/tradeflow` 页面 |
| 数据库 | 使用本地 TradeFlow SQLite（如 `tradeflow.db`），**禁止写生产 `tradingagents.db`** |
| LLM | 试跑链路**不调用 LLM**，无需 API Key |
| 行情 | 盘中观察支持 mock 行情或真实行情；首次试跑建议用 fixture，避免限流 |

**建议试跑日期选择**：
- 周末 / 节假日 / 盘后生成候选池 → 系统会自动把 `effective_trade_date` 推到下一个 A 股交易日。
- 这样可以安全地在非交易日完成"研究 + 计划"，等到交易日再观察。

---

## 1. 第一步：生成今日候选池

### 入口
- **前端**: 打开 `/tradeflow`，确认当前 tab 是「候选池」。
- **API**: `POST /v1/tradeflow/discovery`

### 操作（前端）
1. 在候选池工具栏选择「生成候选池」按钮。
2. 选择日期（默认当天；周末生成会自动推到下一交易日生效）。
3. 等待生成完成（属于 `FAST_RADAR` 层级，预计耗时数秒到数十秒）。

### 操作（API）
```http
POST /v1/tradeflow/discovery
Content-Type: application/json

{
  "date": "2026-06-13",
  "symbols": [],
  "top_n": 20,
  "include_holdings": false,
  "include_watchlist": true,
  "use_event_source": true,
  "save_candidates": true
}
```

### 期望结果
- 候选池表格出现候选，**名称列显示中文名**（如「拓普集团」），**不是代码**（如「601689.SH」）。
- 每只候选有 `candidate_type`（`POLICY_AMBUSH / POLICY_CONFIRM / TECH_TRADE / EVENT_WATCH / UNCLASSIFIED_DATA_GAP`），**不为空字符串**。
- 顶部显示「候选池日期（plan_date）」与「生效交易日（effective_trade_date）」。若两者不同，会有「该候选池由 X 日生成，将在 Y 日观察」提示。
- 顶部池计数显示「昊天左侧 / 政策确认 / 短线技术 / 事件观察 / 证据缺口」各池候选数。

### 常见问题
| 现象 | 处理 |
|------|------|
| 名称仍显示代码 | 触发 TF-P0-001 名称回填，检查后端日志 |
| `candidate_type` 为空 | 触发 TF-P0-002 池拆分逻辑，候选应至少标记为 `UNCLASSIFIED_DATA_GAP` |
| 生成报错 | 看 `GET /v1/tradeflow/data-health` 数据源健康状态 |

---

## 2. 第二步：查看主候选（分层压缩）

### 入口
- **前端**: `/tradeflow` 候选池 tab，主候选区。
- **API**: `GET /v1/tradeflow/candidates/tiered?date=YYYY-MM-DD`

### 操作（前端）
1. 候选池 tab 默认按 action_tier 排序：
   - **actionable（A 档）**：Top 3，建议优先观察。
   - **watch（B 档）**：Top 8，待触发。
   - **scan（C 档）**：其余，仅供扫描。
2. 使用顶部筛选切换池子：
   - 「昊天左侧」：政策主题候选。
   - 「短线技术」：VCP / 回踩支撑候选。
   - 「证据缺口」：数据不足候选。
3. 点击候选行可打开候选详情抽屉。

### 操作（API）
```http
GET /v1/tradeflow/candidates/tiered?date=2026-06-13
```

### 期望结果（关键字段）
| 字段 | 含义 | 期望 |
|------|------|------|
| `actionable / watch / scan` | 分层桶 | actionable 长度 ≤ 3 |
| `main_candidates` | 主候选聚合 | 含 A/B 档候选 |
| `pool_counts` | 各池计数 | 昊天 / 政策 / 技术 / 事件 / 缺口分别计数 |
| `pool_gate_summary` | 池门禁结果 | 通过 / 拒绝原因 |
| `concentration_summary` | 主题集中度 | 防止单主题过载 |

### 候选详情关键字段（`GET /v1/tradeflow/candidates/{symbol}`）
- 昊天左侧候选必显：`mandate_score_component / ambush_score / mandate_topic / company_role / watchlist_note_suggested`。
- 短线技术候选必显：`technical_strategy / trigger_price / invalid_price / data_quality_score`，并显示「为什么不是昊天票」。
- 所有候选都有 `precision_dimensions`（精度门禁分项）和 `action_tier`（A/B/C）。

---

## 3. 第三步：查看风险预算（模拟账本）

### 入口
- **前端**: `/tradeflow` →「模拟账本」tab。
- **API**: `GET /v1/tradeflow/paper-ledger`

### 操作（前端）
1. 打开模拟账本 tab。
2. 查看顶部「配置 / 风险敞口」区域。

### 操作（API）
```http
GET /v1/tradeflow/paper-ledger
```

### 期望结果（默认风险预算）
| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `principal` | ¥5000 | 模拟本金 |
| `per_ticket_max` | ¥1500 | 单票金额上限 |
| `per_ticket_min` | ¥500 | 单票金额下限 |
| `daily_new_max` | 3 | 每日新增候选上限 |
| `max_concurrent_tracking` | 5 | 并发跟踪上限 |
| `require_trigger_price` | true | 必须有触发价 |
| `require_invalid_price` | true | 必须有失效价 |
| `min_data_quality_score` | 40.0 | 数据质量门禁 |

### 风险敞口（`summary.risk_exposure`）
- `invested`：已占用金额。
- `remaining`：剩余可用金额。
- `utilization`：占用百分比（0–100）。
- 例：invested ¥1000 / remaining ¥4000 / utilization 20%。

### 修改配置
```http
POST /v1/tradeflow/paper-ledger/config
{"principal": 8000, "per_ticket_max": 2000}
```
（仅修改本地模拟账本配置，不影响真实资金。）

---

## 4. 第四步：把主候选加入模拟账本

### 入口
- **前端**: 候选详情抽屉 →「加入模拟账本」按钮。
- **API**: `POST /v1/tradeflow/paper-ledger/add`

### 操作（前端）
1. 在候选详情抽屉点击「加入模拟账本」。
2. 系统自动带入 `trigger_price / invalid_price / planned_amount / candidate_type`。
3. 风险预算自动校验。

### 操作（API）
```http
POST /v1/tradeflow/paper-ledger/add
{
  "symbol": "300034.SZ",
  "name": "钢研高纳",
  "trade_date": "2026-06-15",
  "trigger_price": 35.00,
  "invalid_price": 30.50,
  "planned_amount": 1000,
  "candidate_type": "POLICY_AMBUSH",
  "plan_date": "2026-06-13",
  "data_quality_score": 82.0
}
```

### 期望结果
- 成功：返回 `trade_id`，状态为 `tracking`，金额可能被截断到 `per_ticket_max`。
- **硬拒绝**（`rejected=true`）：
  - 缺失 `trigger_price` 或 `invalid_price`。
  - `data_quality_score < 40`。
- **软降级**（`downgraded_to="observation"`）：
  - 超出 `max_concurrent_tracking` 或 `daily_new_max`。
  - 超出剩余预算。
  - 降级后的候选**不能** confirm 模拟进场，只能观察。

### 关键字段（`PaperActionResponse`）
- `accepted / rejected / rule / reason / downgraded_to / trade_id`

---

## 5. 第五步：盘中观察

### 入口
- **前端**: `/tradeflow` →「盘中观察」tab。
- **API**:
  - `GET /v1/tradeflow/observe?date=YYYY-MM-DD`（查看）
  - `POST /v1/tradeflow/observe/run?date=YYYY-MM-DD`（手动触发一次扫描）

### 操作（前端）
1. 切换到「盘中观察」tab。
2. 顶部显示「上次刷新时间 / 调度器状态 / 非交易日提示」。
3. 点击「执行一次观察」按钮触发扫描。
4. 每只候选显示 `observe_state`：
   - `WAITING`：未触发。
   - `TRIGGERED`：价格突破触发价。
   - `INVALIDATED`：跌破失效价。
   - `SKIP_NON_TRADING_DAY`：非交易日跳过。
   - `NO_REALTIME_QUOTE`：无实时行情。

### 操作（API）
```http
POST /v1/tradeflow/observe/run?date=2026-06-15
```

### 期望结果
- 触发的候选 `observe_state` 从 `WAITING → TRIGGERED`。
- 触发信号写入 `tradeflow_signals` 表，每条信号包含 `current_price / quote_time / source / trigger_reason`。
- **Observe 联动模拟账本**：触发后 paper trade 状态自动从 `tracking → pending`（待确认）。
- 非交易日执行 observe：runner **不**拉实时行情，返回 `SKIP_NON_TRADING_DAY`，前端明确提示。

### 调度器状态
```http
GET /v1/tradeflow/observe/scheduler-status
```
返回：是否启用 / 当前轮询间隔 / 上次执行时间 / 下次执行时间。

---

## 6. 第六步：人工确认模拟动作

### 入口
- **前端**: 模拟账本 tab → 待确认候选 → 「确认」按钮。
- **API**: `POST /v1/tradeflow/paper-ledger/confirm`

### 操作（前端）
1. 模拟账本 tab 找到状态为 `pending`（待确认）的候选。
2. 点击「确认模拟进场」（action_type=buy），填入实际价（默认为触发价）。
3. 后续可点击「确认模拟出场」（action_type=sell）完成一笔模拟交易。

### 操作（API）
```http
# mock entry (action_type=buy)
POST /v1/tradeflow/paper-ledger/confirm
{
  "trade_id": "...",
  "action_type": "buy",
  "price": 35.50,
  "note": "用户人工确认"
}

# mock exit (action_type=sell)
POST /v1/tradeflow/paper-ledger/confirm
{
  "trade_id": "...",
  "action_type": "sell",
  "price": 38.00,
  "note": "用户人工确认"
}
```

### 期望结果
- buy → 状态 `open`，记录模拟进场价。
- sell → 状态 `closed`，计算 `realized_pnl`（实现盈亏）。
- 返回最新 `cash_balance` 与 `pnl`。
- **观察池候选（observation 状态）调用 confirm buy 会被拒绝**，避免自动交易。

---

## 7. 第七步：盘后 Review

### 入口
- **前端**: `/tradeflow` →「盘后 Review」tab。
- **API**:
  - `POST /v1/tradeflow/review/generate?date=YYYY-MM-DD`（生成）
  - `GET /v1/tradeflow/review?date=YYYY-MM-DD`（查询）
  - `GET /v1/tradeflow/paper-ledger/review?date=YYYY-MM-DD`（账本复盘）

### 操作（前端）
1. 切换到「盘后 Review」tab。
2. 点击「生成今日复盘」。
3. 查看策略命中归因 + 次日反馈 + 账本复盘。

### 操作（API）
```http
POST /v1/tradeflow/review/generate?date=2026-06-15
```

### 期望结果
- `attribution_stats`：策略命中归因，分类计数：
  - `technical_hit / policy_hit / fund_flow_hit / data_issue / risk_hit`
- `next_day_feedback`：每只候选的次日反馈：
  - `tomorrow_focus`：明日关注点。
  - `downgrade_reason`：降级原因（如有）。
  - `evidence_needed`：证据缺口。
- 账本复盘（`get_paper_review`）：展示已平仓笔的 buy 价 / sell 价 / realized_pnl。

### 空数据诊断
若 Review 返回空：
- 系统返回 `_diagnose_review_empty_state` 结构化原因，明确是「无候选 / 非交易日 / 数据缺失 / 生成失败」中的哪一类。
- 前端展示「为何无数据」与「一键生成入口」。

---

## 8. 第八步（可选）：加入观察仓 / 跟踪看板

### 8.1 一键加入观察仓
适用于「想买但等位置」的标的。

- **从候选加入**:
  ```http
  POST /v1/tradeflow/candidates/{symbol}/add-to-observation
  {"symbol": "300034.SZ", "trade_date": "2026-06-15", "via": "manual"}
  ```
- **从 TA 报告加入**:
  ```http
  POST /v1/tradeflow/ta-reports/{symbol}/add-to-observation
  ```

系统自动带入：`trigger_price / invalid_price / strategy_tags / score / action_label / research_direction`。
重复 symbol 做 upsert，保留历史来源，**不覆盖用户手动备注**（除非 `force_overwrite_notes=true`）。

### 8.2 跟踪看板（四区）
打开 `/tracking-board`，四个 tab：
| Tab | 内容 |
|-----|------|
| 持仓 | 真实持仓、当日表现、浮盈、关键价位、最新 TA 动作语义 |
| 观察仓 | 观察状态、进场区间、触发价、失效价、来源、理由、优先级 |
| 今日指引 | 按 P0/P1/P2/P3 或「风险/接近买点/需 TA/仅记录」分组 |
| 盘后复盘 | 当日是否触发计划、是否失效、明日是否继续观察 |

### 观察仓状态流转（规则引擎，不调用 LLM）
| 状态 | 触发条件 |
|------|----------|
| `watching` | 默认观察 |
| `near_entry` | 价格接近进场区间 |
| `in_entry_zone` | 价格进入进场区间 |
| `missed_entry` | 错过进场区间 |
| `invalidated` | 跌破失效价 |
| `ta_required` | 需 TA 深度确认 |
| `data_missing` | 数据缺失，**不**误判为可入场 |

---

## 9. 常见问题排查

| 现象 | 排查路径 |
|------|----------|
| 候选池为空 | 1) 检查 `GET /v1/tradeflow/data-health` 数据源状态；2) 确认 watchlist 非空；3) 换日期重试 |
| 候选名称是代码 | TF-P0-001 名称回填失败，查看后端日志的 `resolve_tradeflow_name` |
| observe 一直 WAITING | 1) 是否交易日；2) 是否有实时行情；3) 触发价是否合理 |
| paper add 被拒 | 检查返回的 `rule / reason`：缺失价格 / 数据质量低 / 超预算 |
| Review 为空 | 看 `_diagnose_review_empty_state` 字段确认原因 |
| 前端字段缺失 | 检查 `npm run build` 是否通过；字段 normalization 在 UI-008 |

---

## 10. 安全边界速记

- **不调用 LLM**：试跑链路全程 fixture / mock / 规则引擎。
- **不写生产数据库**：所有试跑使用本地 `tradeflow.db`，不写 `tradingagents.db`。
- **不接真实交易**：所有买卖均为模拟，需人工确认。
- **不自动下单**：观察池候选无法 confirm buy。
- **不输出强买卖词**：仅使用 `OBSERVE / WAIT_TRIGGER / NEED_DEEP_TA / REMOVE_FROM_WATCH`。
- **不跑全市场扫描**：候选池基于 watchlist + 事件源。
- **不在非交易日拉实时行情**：observe runner 自动 skip。

---

## 11. API 路由速查表

下表列出本手册涉及的全部 API 路由，方便快速定位（自动化 smoke 测试 `tests/test_v009_trial_guide_smoke.py` 会强制校验每条都仍注册）。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/tradeflow/discovery` | 生成候选池 |
| GET | `/v1/tradeflow/daily-plan` | 当日计划（候选 + 元数据） |
| GET | `/v1/tradeflow/candidates` | 候选列表 |
| GET | `/v1/tradeflow/candidates/{symbol}` | 候选详情 |
| GET | `/v1/tradeflow/candidates/tiered` | 分层压缩 |
| GET | `/v1/tradeflow/observe` | 盘中观察列表 |
| POST | `/v1/tradeflow/observe/run` | 手动触发一次观察扫描 |
| GET | `/v1/tradeflow/observe/scheduler-status` | 观察调度器状态 |
| GET | `/v1/tradeflow/ta-queue` | TA 深度分析队列 |
| GET | `/v1/tradeflow/review` | Review 查询 |
| POST | `/v1/tradeflow/review/generate` | 生成盘后 Review |
| GET | `/v1/tradeflow/paper-ledger` | 模拟账本 / 风险敞口 |
| POST | `/v1/tradeflow/paper-ledger/add` | 加入模拟账本（含风险预算校验） |
| POST | `/v1/tradeflow/paper-ledger/remove` | 移除候选 |
| POST | `/v1/tradeflow/paper-ledger/confirm` | 人工确认模拟动作 |
| GET | `/v1/tradeflow/paper-ledger/review` | 账本复盘 |
| GET | `/v1/tradeflow/observation-items` | 观察仓列表 |
| POST | `/v1/tradeflow/observation-items` | 创建观察项 |
| PATCH | `/v1/tradeflow/observation-items/{item_id}` | 更新观察项 |
| POST | `/v1/tradeflow/observation-items/{item_id}/mark` | 标记状态（invalidated/entered/...） |
| POST | `/v1/tradeflow/candidates/{symbol}/add-to-observation` | 候选一键加入观察仓 |
| GET | `/v1/dashboard/tracking-board/v2` | 跟踪看板 v2 分组聚合 |
| GET | `/v1/tradeflow/data-health` | 数据源健康 |

---

## 12. 参考文档

- 验收报告: `docs/tradeflow_trial_acceptance_v2.md`（V-008）
- 回归清单: `docs/tradeflow_trial_regression_checklist.md`（V-009 配套）
- 跟踪看板手册: `docs/tracking-board-v1-acceptance.md` 第七节
- 任务定义: `docs/TASKS.md` V-009 / V-008 / TF-PAPER-001 / TF-RISK-001
