# TradeFlow 小资金试跑回归清单 (V-009)

**文档版本**: v1.0
**生成时间**: 2026-06-24
**对应任务**: V-009（配套 `docs/tradeflow_trial_user_guide.md`）
**用途**: 每次发布前 / 回归时按本清单逐项核对，每一步标注对应的 API 路由、前端字段、期望结果与自动化测试。

> 本清单全部步骤均为**模拟 / 观察**，**不是实盘交易建议**。所有动作均需用户人工确认。

---

## 使用方法

1. 按顺序执行 Step 1 → Step 9。
2. 每一步在「结果」列打勾（✓ / ✗）。
3. 任何一步失败即停止，按「失败排查」列定位。
4. 全部通过后，把本清单归档到 `docs/task_runs/<TASK_ID>-<TIMESTAMP>/`。
5. 自动化 smoke 测试位于 `tests/test_v009_trial_guide_smoke.py`，可单独运行：
   ```bash
   pytest tests/test_v009_trial_guide_smoke.py -q
   ```

---

## 回归清单

### Step 1 — 候选池生成

| 项 | 内容 |
|----|------|
| 前端入口 | `/tradeflow` → 候选池 tab → 生成候选池 |
| API | `POST /v1/tradeflow/discovery` |
| 请求体 | `{date, symbols:[], top_n:20, include_watchlist:true, use_event_source:true, save_candidates:true}` |
| 期望结果 | 返回候选数组长度 ≥ 1 |
| 字段校验 | 每条候选 `symbol / name / candidate_type` 非空；`name ≠ symbol`；`candidate_type` ∈ {POLICY_AMBUSH, POLICY_CONFIRM, TECH_TRADE, EVENT_WATCH, UNCLASSIFIED_DATA_GAP} |
| 日期语义 | 周末生成时 `effective_trade_date ≠ plan_date`，且为下一交易日 |
| 失败排查 | `GET /v1/tradeflow/data-health`；检查 watchlist 是否为空 |
| 自动化测试 | `tests/test_v008_paper_trial_acceptance.py::test_step1_generate_candidates` |
| 结果 | ☐ ✓ ☐ ✗ |

### Step 2 — 分层压缩（主候选）

| 项 | 内容 |
|----|------|
| 前端入口 | 候选池 tab → 主候选区 |
| API | `GET /v1/tradeflow/candidates/tiered?date=YYYY-MM-DD` |
| 期望结果 | `actionable` 长度 ≤ 3；`watch` 长度 ≤ 8；存在 `main_candidates` |
| 字段校验 | 主候选含 `action_tier ∈ {A, B, C}` 与 `precision_dimensions` |
| 池计数 | `pool_counts` 含 `haotian / policy / tech / event / gap` 分项 |
| 失败排查 | 检查 `pool_gate_summary`；候选数过少时检查 `concentration_summary` |
| 自动化测试 | `tests/test_v008_paper_trial_acceptance.py::test_step2_tiered_ranking` |
| 结果 | ☐ ✓ ☐ ✗ |

### Step 3 — 风险预算（模拟账本配置）

| 项 | 内容 |
|----|------|
| 前端入口 | 模拟账本 tab → 配置区 |
| API | `GET /v1/tradeflow/paper-ledger` |
| 期望结果 | 返回 `config` 与 `summary.risk_exposure` |
| 字段校验 | config 含 `principal=5000 / per_ticket_max=1500 / per_ticket_min=500 / daily_new_max=3 / max_concurrent_tracking=5 / require_trigger_price=true / require_invalid_price=true / min_data_quality_score=40.0` |
| 风险敞口 | `risk_exposure` 含 `invested / remaining / utilization`；`utilization ∈ [0, 100]` |
| 失败排查 | 检查 schema migration；首次启动应写默认配置 |
| 自动化测试 | `tests/test_tf_risk001_paper_risk_budget.py` |
| 结果 | ☐ ✓ ☐ ✗ |

### Step 4 — 加入模拟账本

| 项 | 内容 |
|----|------|
| 前端入口 | 候选详情抽屉 → 加入模拟账本 |
| API | `POST /v1/tradeflow/paper-ledger/add` |
| 请求体 | `{symbol, name, trade_date, trigger_price, invalid_price, planned_amount, candidate_type, plan_date, data_quality_score}` |
| 期望结果 | 成功时返回 `accepted=true / trade_id`；状态 `tracking` |
| 硬拒绝 | 缺失 `trigger_price / invalid_price` 或 `data_quality_score<40` 时 `rejected=true`，含 `rule/reason` |
| 软降级 | 超出 `max_concurrent_tracking / daily_new_max / remaining` 时 `downgraded_to=observation` |
| 字段校验 | `PaperActionResponse` 含 `accepted/rejected/rule/reason/downgraded_to/trade_id` |
| 失败排查 | 看 `rule` 字段；检查是否超预算 |
| 自动化测试 | `tests/test_tf_paper001_paper_ledger.py`、`tests/test_tf_risk001_paper_risk_budget.py` |
| 结果 | ☐ ✓ ☐ ✗ |

### Step 5 — 盘中观察触发

| 项 | 内容 |
|----|------|
| 前端入口 | 盘中观察 tab → 执行一次观察 |
| API | `POST /v1/tradeflow/observe/run?date=YYYY-MM-DD` |
| 期望结果（触发） | 价格突破触发价 → `observe_state: WAITING → TRIGGERED`；写入 `tradeflow_signals` |
| 期望结果（失效） | 跌破失效价 → `observe_state: INVALIDATED` |
| 期望结果（非交易日） | 返回 `SKIP_NON_TRADING_DAY`，**不**拉实时行情 |
| 信号字段 | `tradeflow_signals` 含 `current_price / quote_time / source / trigger_reason` |
| 联动账本 | 触发后 paper trade 状态 `tracking → pending` |
| 字段校验 | observe 列表每条含 `observe_state / observe_trigger_count / observe_first_trigger_time` |
| 失败排查 | 是否交易日；是否有实时行情；触发价是否合理 |
| 自动化测试 | `tests/test_tf_obs_001_observe_runner.py`、`tests/test_tf_obs_003_observe_paper_sync.py` |
| 结果 | ☐ ✓ ☐ ✗ |

### Step 6 — 人工确认模拟动作

| 项 | 内容 |
|----|------|
| 前端入口 | 模拟账本 tab → 待确认候选 → 确认 |
| API（进场） | `POST /v1/tradeflow/paper-ledger/confirm` body `{trade_id, action_type:"buy", price, note}` |
| API（出场） | `POST /v1/tradeflow/paper-ledger/confirm` body `{trade_id, action_type:"sell", price, note}` |
| 期望结果（进场） | 状态 `open`；返回最新 `cash_balance` |
| 期望结果（出场） | 状态 `closed`；计算 `realized_pnl` |
| 安全门禁 | observation 状态候选调用 confirm buy **必须被拒绝** |
| 字段校验 | 返回 `cash_balance / pnl / status` |
| 失败排查 | 检查 `trade_id`；检查候选状态是否 pending |
| 自动化测试 | `tests/test_tf_paper001_paper_ledger.py` |
| 结果 | ☐ ✓ ☐ ✗ |

### Step 7 — 盘后 Review 归因

| 项 | 内容 |
|----|------|
| 前端入口 | 盘后 Review tab → 生成今日复盘 |
| API（生成） | `POST /v1/tradeflow/review/generate?date=YYYY-MM-DD` |
| API（查询） | `GET /v1/tradeflow/review?date=YYYY-MM-DD` |
| API（账本） | `GET /v1/tradeflow/paper-ledger/review?date=YYYY-MM-DD` |
| 期望结果 | `attribution_stats` 含 `technical_hit / policy_hit / fund_flow_hit / data_issue / risk_hit` |
| 次日反馈 | `next_day_feedback` 每条含 `tomorrow_focus / downgrade_reason / evidence_needed` |
| 账本复盘 | 已平仓笔含 `buy_price / sell_price / realized_pnl` |
| 空数据诊断 | Review 为空时返回结构化 `_diagnose_review_empty_state` 原因 |
| 失败排查 | 检查候选是否当日；检查 observe 是否触发 |
| 自动化测试 | `tests/test_tf_review_003_strategy_attribution.py`、`tests/test_tf_review_004_empty_diagnostics.py` |
| 结果 | ☐ ✓ ☐ ✗ |

### Step 8 — 观察仓与跟踪看板（可选）

| 项 | 内容 |
|----|------|
| 前端入口 | `/tracking-board` 四区 |
| API（v2 看板） | `GET /v1/dashboard/tracking-board/v2` |
| API（加入观察仓） | `POST /v1/tradeflow/candidates/{symbol}/add-to-observation` |
| 期望结果（看板） | 返回 `holdings / observation_items / today_guidance / alerts / review_summary / data_freshness` |
| 期望结果（观察仓） | 重复加入不生成重复记录；用户 notes 不丢失 |
| 状态流转 | 规则引擎输出 `watching / near_entry / in_entry_zone / missed_entry / invalidated / ta_required / data_missing` |
| 字段校验 | 每条 today_guidance 含 `source / as_of / reason`，无来源结论不予展示 |
| 失败排查 | 观察仓状态不流转 → 检查实时价；today_guidance 为空 → 检查持仓与观察仓 |
| 自动化测试 | `tests/test_track002_tracking_board_v2.py`、`tests/test_track004_observation_state_engine.py`、`tests/test_track006_add_to_observation.py` |
| 结果 | ☐ ✓ ☐ ✗ |

### Step 9 — 合规与禁用词校验

| 项 | 内容 |
|----|------|
| 检查范围 | 候选 / tiered / paper_ledger / review / observation 全部响应 |
| 禁用词集合 | 详见下方代码块（FORBIDDEN_WORDS + 项目扩展集合，自动 smoke 会扫到任何一个） |
| 允许动作 | 仅允许 `OBSERVE / WAIT_TRIGGER / NEED_DEEP_TA / REMOVE_FROM_WATCH` |
| 期望结果 | 全部响应文本扫描禁用词命中数 = 0 |
| 失败排查 | 定位命中字段 → 检查生成源（候选模板 / review 模板 / observation 模板） |
| 自动化测试 | `tests/test_v008_paper_trial_acceptance.py::test_no_forbidden_words_in_*`、`tests/test_v009_trial_guide_smoke.py::test_no_forbidden_words_in_user_guide` |
| 结果 | ☐ ✓ ☐ ✗ |

```
FORBIDDEN_WORDS + 项目扩展集合（任一命中即视为违规）：
立即买入 / 重仓买入 / 立即清仓 / 重仓 / 清仓 / 满仓 / 梭哈 /
立即卖出 / 全仓 / 必涨 / 必跌 / 无脑买 / 加杠杆 / 稳赚 / 保本 /
买入 / 卖出 / 加仓 / 减仓 / 抄底 / 逃顶 / 追涨 / 杀跌
```

---

## API 路由存在性回归（防回归）

以下路由必须存在并返回 2xx / 4xx（而非 404 / 500），由 `tests/test_v009_trial_guide_smoke.py::test_referenced_api_routes_exist` 自动校验：

| Method | Path | 说明 |
|--------|------|------|
| POST | `/v1/tradeflow/discovery` | 候选生成 |
| GET | `/v1/tradeflow/daily-plan` | 当日计划 |
| GET | `/v1/tradeflow/candidates` | 候选列表 |
| GET | `/v1/tradeflow/candidates/{symbol}` | 候选详情 |
| GET | `/v1/tradeflow/candidates/tiered` | 分层压缩 |
| GET | `/v1/tradeflow/observe` | 盘中观察 |
| POST | `/v1/tradeflow/observe/run` | 触发观察 |
| GET | `/v1/tradeflow/observe/scheduler-status` | 调度器状态 |
| GET | `/v1/tradeflow/ta-queue` | TA 队列 |
| GET | `/v1/tradeflow/review` | Review 查询 |
| POST | `/v1/tradeflow/review/generate` | Review 生成 |
| GET | `/v1/tradeflow/paper-ledger` | 模拟账本 |
| POST | `/v1/tradeflow/paper-ledger/add` | 加入账本 |
| POST | `/v1/tradeflow/paper-ledger/remove` | 移除候选 |
| POST | `/v1/tradeflow/paper-ledger/confirm` | 确认动作 |
| GET | `/v1/tradeflow/paper-ledger/review` | 账本复盘 |
| GET | `/v1/tradeflow/observation-items` | 观察仓列表 |
| POST | `/v1/tradeflow/observation-items` | 创建观察项 |
| PATCH | `/v1/tradeflow/observation-items/{item_id}` | 更新观察项 |
| POST | `/v1/tradeflow/observation-items/{item_id}/mark` | 标记状态 |
| POST | `/v1/tradeflow/candidates/{symbol}/add-to-observation` | 候选加入观察仓 |
| GET | `/v1/dashboard/tracking-board/v2` | 跟踪看板 v2 |
| GET | `/v1/tradeflow/data-health` | 数据健康 |

---

## 核心字段存在性回归

由 `tests/test_v009_trial_guide_smoke.py::test_referenced_core_fields_exist` 自动校验。下列字段必须能在 schema / 默认配置 / 服务返回中找到：

- 候选: `symbol, name, candidate_type, action_tier, trigger_price, invalid_price, precision_dimensions, mandate_topic, ambush_score, data_quality_score`
- 分层: `actionable, watch, scan, main_candidates, pool_counts, pool_gate_summary, concentration_summary`
- 模拟账本配置: `principal, per_ticket_max, per_ticket_min, daily_new_max, max_concurrent_tracking, require_trigger_price, require_invalid_price, min_data_quality_score`
- 风险敞口: `invested, remaining, utilization`
- Observe: `observe_state, observe_trigger_count, observe_first_trigger_time`
- Review: `attribution_stats, next_day_feedback, tomorrow_focus, downgrade_reason, evidence_needed`
- 观察仓: `entry_low, entry_high, trigger_price, invalid_price, horizon, source, reason, priority, notes, last_reviewed_at`
- 跟踪看板 v2: `holdings, observation_items, today_guidance, alerts, review_summary, data_freshness`

---

## 安全边界回归

每轮回归必须确认以下边界均成立：

- [ ] 试跑链路不调用 LLM（无 LLM provider 配置或 mock 注入）。
- [ ] 试跑链路不写生产 `tradingagents.db`（使用 tmp_path 或本地 tradeflow.db）。
- [ ] 试跑链路不接真实交易（mock 行情 + paper ledger）。
- [ ] observation 状态候选无法 confirm buy。
- [ ] 非交易日 observe runner 不拉实时行情。
- [ ] 全部响应文本不含禁用词。

---

## 通过判定

- Step 1–7 + Step 9 全部 ✓ → **回归通过**。
- Step 8（观察仓 / 跟踪看板）为可选附加项，失败不阻塞主链路。
- 任何 API 路由存在性 / 核心字段存在性测试失败 → **回归失败**。

## 失败归档

失败时在 `docs/task_runs/<TASK_ID>-<TIMESTAMP>/regression_failure.md` 记录：
- 失败步骤。
- 失败的 API / 字段。
- 复现命令。
- 相关 commit。
