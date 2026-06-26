# TradeFlow 小资金试跑 v2 验收报告 (V-010)

**生成时间**: 2026-06-27 01:21:23
**计划日期 (plan_date)**: 2026-06-13 (周六，非交易日)
**生效交易日 (effective_trade_date)**: 2026-06-15 (周一，交易日)
**模拟本金**: ¥5000
**候选数**: 4 只 (1 昊天主候选 + 1 技术主候选 + 1 观察候选 + 1 过滤候选)

## 总体结果

| 指标 | 值 |
|------|------|
| 步骤总数 | 6 |
| 通过 | 6/6 |
| 失败 | 0 |

## 链路步骤

### [PASS] 1_candidate_convergence

### [PASS] 2_observe_trigger

### [PASS] 3_mandate_daily_report

### [PASS] 4_data_blockers_and_board_summary

### [PASS] 5_post_market_review

### [PASS] 6_safety_constraints

## 候选清单

| Symbol | 名称 | 类型 | Tier | 池 | 触发价 | 失效价 |
|--------|------|------|------|------|--------|--------|
| 300034.SZ | 钢研高纳 | POLICY_AMBUSH | A | 昊天主候选 | 35.00 | 30.50 |
| 601689.SH | 拓普集团 | TECH_TRADE | B | 技术主候选 | 42.50 | 38.50 |
| 002230.SZ | 科大讯飞 | EVENT_WATCH | B | 观察候选 | - | - |
| 600711.SH | 香江控股 | UNCLASSIFIED_DATA_GAP | C | 过滤候选（证据缺口） | - | - |

## 试跑五问回答

### 1. 今天看哪几只
- **主候选（2 只）**：钢研高纳 (300034.SZ，昊天左侧)、拓普集团 (601689.SH，短线技术)。
- **观察池（1 只）**：科大讯飞 (002230.SZ) — 事件催化待确认。
- **过滤池（1 只）**：香江控股 (600711.SH) — 数据完整度仅 18%，无可靠信号。
- 候选池经过 tiered ranking 严格收敛，主候选不超过配置上限（2 只）。

### 2. 为什么（评分 + 原因）
- **钢研高纳**：综合分 75.0，分项评分 技术 55.0 / 政策 80.0 / 资金 15.0 / 风控 0.0 / 数据 82.0。
  排前原因：「政策连续性强」「受益路径明确」；扣分原因：「估值偏高」。
  通过昊天精度门禁（政策主题 ✓ / 受益路径 ✓ / 反证不过热 ✓ / 证据覆盖 ✓）。
- **拓普集团**：综合分 62.0，分项评分 技术 58.0 / 政策 0.0 / 资金 8.0 / 风控 0.0 / 数据 65.0。
  排前原因：「VCP 形态确认」「资金流入」；扣分原因：「无政策支撑」。
  通过技术精度门禁（形态 ✓ / 触发价 ✓ / 数据质量 ✓ / 资金 ✓）。
- **过滤原因**：香江控股 `data_quality_score=18`，命中数据质量门禁与证据缺口门禁，直接进过滤池。

### 3. 何时观察
- **盘中观察**（使用 mock 行情，不接真实交易）：
  - 钢研高纳价格 36.00 突破触发价 35.00（+2.86%），observe_state 从 WAITING → TRIGGERED。
  - 拓普集团价格 41.00 未达触发价 42.50，继续 WAITING。
  - 观察候选 (科大讯飞) 与过滤候选 (香江控股) 因无触发价不参与盘中触发。
- 触发信号写入 `tradeflow_signals` 表，证据含 current_price / quote_time / source。

### 4. 数据缺什么（昊天日报 + DATA-021 报告缺口）
- **昊天主题日报 (H-015)**：从候选池 topic heatmap 重建，识别升温主题「低空经济」，
  并输出 `evidence_gaps`（订单兑现等反证缺口）。
- **报告字段级 data_blockers (DATA-021)**：扫描最近 completed 报告，
  香江控股 (600711.SH) 报告命中 2 项 severe blocker：
  - `individual_fund_flow` → `query_failed`（主力资金查询失败）
  - `announcements` → `query_failed`（公告查询失败）
  - `lhb_status` → `normal_no_data`（正常无数据，不计 severe）
- **controller_hints.suppress_push_data_insufficient**：香江控股因严重数据缺口被标记为
  「不作强结论推送」，避免把数据不足的报告当 confident 结论发给用户。

### 5. 盘后怎么复盘（Tracking Board v2 只读摘要 + Review）
- **Tracking Board v2 只读摘要 (IC-TA-002 investment_controller_context)** 一次性聚合：
  - holdings / observation_warehouse / tradeflow_candidates（4 只）/ data_health
  - mandate_daily_report（升温主题、主候选、证据缺口）
  - recent_report_data_blockers（severe 2 项，affected_symbols=[600711.SH]）
  - controller_hints（needs_ta / daily_report_only / suppress_push_data_insufficient 三条软路由）
- **盘后 Review 归因 (TF-REVIEW-003)**：
  - attribution_stats：technical_hit / policy_hit / fund_flow_hit / data_issue / risk_hit。
  - next_day_feedback：4 只候选每个携带 `tomorrow_focus` / `downgrade_reason` / `evidence_needed`。
- 整条链路 `read_only=True`，不会修改任何状态，不发真实通知。

## 链路完整性

1. **候选收敛** → 4 类候选落库，tiered ranking 收敛到 2 只主候选（昊天 + 技术）。
2. **盘中观察** → run_observe + mock quote → observe_state=TRIGGERED + signal 落库。
3. **昊天日报** → topic heatmap 重建，识别升温主题与证据缺口。
4. **报告缺口** → IC-TA-002 上下文聚合 DATA-021 severe blockers 与 controller_hints。
5. **盘后复盘** → generate_review 输出 attribution_stats + next_day_feedback。

## 约束验证

- [x] 不调用 LLM（全部使用 fixture / mock）
- [x] 不写生产数据库 (tradingagents.db / tradeflow.db) — 使用 tmp_path 隔离
- [x] 不接真实交易（mock quote + paper ledger）
- [x] 不发真实通知（monkeypatch 所有 notification sender，counter 保持 0）
- [x] 无强买卖建议（FORBIDDEN_WORDS + 扩展禁词清单均未命中）
- [x] runtime_tier 全程不升级到 FULL_TA（链路 4 个 endpoint 均为 FAST_RADAR）

## 合规确认

- 模拟账户仅供学习研究，**不构成投资建议**，不连接真实交易。
- 所有触发/买卖动作均需用户人工确认，系统不会自动下单。
- 严重数据缺口的标的不会被推送为 confident 结论，避免误导。

## 验收结论

全部 6 个步骤通过，TradeFlow 小资金试跑 v2 验收合格。
用户可以用 ¥5000 模拟本金安全试用「候选收敛→观察→日报→报告缺口→盘后复盘」完整流程。
