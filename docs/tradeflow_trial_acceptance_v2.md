# TradeFlow 小资金试跑前整体验收报告 (V-008)

**生成时间**: 2026-06-26 20:57:10
**计划日期 (plan_date)**: 2026-06-13 (周六，非交易日)
**生效交易日 (effective_trade_date)**: 2026-06-15 (周一，交易日)
**模拟本金**: ¥5000
**候选数**: 5 只 (2 主候选 + 1 弱技术 + 1 事件观察 + 1 证据缺口)

## 总体结果

| 指标 | 值 |
|------|------|
| 步骤总数 | 9 |
| 通过 | 9/9 |
| 失败 | 0 |

## 链路步骤

### [PASS] 1_generate_candidates

### [PASS] 2_tiered_ranking

### [PASS] 3_paper_risk_budget

### [PASS] 4_observe_trigger

### [PASS] 5_observe_paper_sync

### [PASS] 6_confirm_action

### [PASS] 7_review_attribution

### [PASS] 8_api_response_model

### [PASS] 9_no_forbidden_words

## 候选清单

| Symbol | 名称 | 类型 | Tier | 池 | 触发价 | 失效价 |
|--------|------|------|------|------|--------|--------|
| 300034.SZ | 钢研高纳 | POLICY_AMBUSH | A | 昊天左侧（主候选） | 35.00 | 30.50 |
| 601689.SH | 拓普集团 | TECH_TRADE | B | 短线技术（主候选） | 42.50 | 38.50 |
| 600585.SH | 海螺水泥 | TECH_TRADE | C | 过滤 | 25.00 | 22.50 |
| 002230.SZ | 科大讯飞 | EVENT_WATCH | B | 观察 | - | - |
| 600711.SH | 香江控股 | UNCLASSIFIED_DATA_GAP | C | 过滤 | - | - |

## 试跑五问回答

### 1. 今天看哪几只
- **主候选（2 只）**：钢研高纳 (300034.SZ)、拓普集团 (601689.SH)。
- **观察池（1 只）**：科大讯飞 (002230.SZ) — 事件催化待确认。
- **过滤池（2 只）**：海螺水泥 (600585.SH)、香江控股 (600711.SH) — 数据不足/技术信号弱。

### 2. 为什么（评分 + 原因）
- **钢研高纳**：综合分 75.0，分项评分 技术 55.0 / 政策 80.0 / 资金 15.0 / 风控 0.0 / 数据 82.0。
  排前原因：「政策连续性强」「受益路径明确」；扣分原因：「估值偏高」。
  通过昊天精度门禁（政策主题 ✓ / 受益路径 ✓ / 反证不过热 ✓ / 证据覆盖 ✓）。
- **拓普集团**：综合分 62.0，分项评分 技术 58.0 / 政策 0.0 / 资金 8.0 / 风控 0.0 / 数据 65.0。
  排前原因：「VCP 形态确认」「资金流入」；扣分原因：「无政策支撑」。
  通过技术精度门禁（形态 ✓ / 触发价 ✓ / 数据质量 ✓ / 资金 ✓）。

### 3. 何时触发
- **盘中观察**（使用 mock 行情，不接真实交易）：
  - 钢研高纳价格 36.00 突破触发价 35.00（+2.86%），observe_state 从 WAITING → TRIGGERED。
  - 拓普集团价格 41.00 未达触发价 42.50，继续 WAITING。
- 触发信号写入 `tradeflow_signals` 表。
- 通过 `_sync_paper_from_observe` 自动联动到模拟账本，状态从 tracking → pending（待确认）。

### 4. 风险额度
- **本金**：¥5000（默认）。
- **单票上限**：¥1500；**单票下限**：¥500。
- **每日新增上限**：3 只；**并发跟踪上限**：5 只。
- **数据质量门禁**：data_quality_score < 40 直接拒绝。
- **触发价/失效价门禁**：缺失则硬拒绝。
- **当前占用**（验收 fixture）：钢研高纳 ¥1000 → invested ¥1000 / remaining ¥4000 / utilization 20%。
- 观察池候选（observation 状态）不能 confirm buy，避免自动交易。

### 5. 盘后表现
- `generate_review` 生成盘后复盘报告，覆盖 5 只候选。
- **策略命中归因**（attribution_stats）：technical_hit / policy_hit / fund_flow_hit / data_issue / risk_hit。
- **次日反馈**（next_day_feedback）：每个候选携带 `tomorrow_focus`、`downgrade_reason`、`evidence_needed`。
- `get_paper_review` 显示模拟账本复盘：1 笔已平仓（buy 35.5 → sell 38.0），实现盈利。

## 链路完整性

1. **候选生成** → `tradeflow_candidates` 表写入 5 只候选（含 plan_date / effective_trade_date）。
2. **候选压缩** → tiered ranking 识别 2 只主候选，携带 precision_dimensions + action_tier。
3. **加入模拟账本** → add_paper_candidate + risk_budget 校验（金额截断 / 硬拒绝）。
4. **盘中触发** → run_observe + mock quote → observe_state = TRIGGERED。
5. **Observe 联动** → _sync_paper_from_observe → 状态 pending（待确认）。
6. **人工确认** → confirm_paper_action(buy) → confirm_paper_action(sell) → closed + realized_pnl。
7. **盘后 Review 归因** → generate_review 输出 attribution_stats + next_day_feedback。

## 约束验证

- [x] 不调用 LLM（全部使用 fixture / mock）
- [x] 不写生产数据库 (tradingagents.db) — 使用 tmp_path 隔离
- [x] 不接真实交易（mock quote + paper ledger）
- [x] 无强买卖建议（FORBIDDEN_WORDS + 额外禁止词清单均未命中）

## 合规确认

- 模拟账户仅供学习研究，**不构成投资建议**，不连接真实交易。
- 所有触发/买卖动作均需用户人工确认，系统不会自动下单。
- 观察池候选不能成为 pending/open，避免隐性自动交易。

## 验收结论

全部 9 个步骤通过，TradeFlow 小资金试跑整体验收合格。
用户可以用 ¥5000 模拟本金安全试用完整流程。
