# TradeFlow 试用闭环端到端验收报告 (V-007)

**生成时间**: 2026-06-18 03:03:35
**计划日期 (plan_date)**: 2026-06-13 (周六，非交易日)
**生效交易日 (effective_trade_date)**: 2026-06-15 (周一，交易日)
**候选数**: 5 只 (2 主候选 + 1 弱技术 + 1 事件观察 + 1 证据缺口)

## 总体结果

| 指标 | 值 |
|------|------|
| 步骤总数 | 9 |
| 通过 | 9/9 |
| 失败 | 0 |

## 链路步骤

### [PASS] generate_5_candidates

### [PASS] identify_2_main

### [PASS] date_semantics

### [PASS] observe_1_triggered

### [PASS] paper_ledger_add

### [PASS] paper_ledger_buy_sell

### [PASS] post_market_review

### [PASS] field_integrity

### [PASS] no_forbidden_words

## 候选清单

| Symbol | 名称 | 类型 | Tier | 池 | 触发价 | 失效价 |
|--------|------|------|------|------|--------|--------|
| 300034.SZ | 钢研高纳 | POLICY_AMBUSH | A | 昊天左侧 | 35.00 | 30.50 |
| 601689.SH | 拓普集团 | TECH_TRADE | B | 短线技术 | 42.50 | 38.50 |
| 600585.SH | 海螺水泥 | TECH_TRADE | C | 过滤 | 25.00 | 22.50 |
| 002230.SZ | 科大讯飞 | EVENT_WATCH | B | 观察 | - | - |
| 600711.SH | 香江控股 | UNCLASSIFIED_DATA_GAP | C | 过滤 | - | - |

## 试用闭环说明

### 为什么入池
- **钢研高纳 (300034.SZ)**: 低空经济政策连续催化，公司是高温合金核心供应商，
  受益路径明确（航空发动机叶片/高温合金），数据完整度 82%，政策分 80.0。
  通过昊天精度门禁 4/4 维度（政策主题/受益路径/反证不过热/证据覆盖）。
- **拓普集团 (601689.SH)**: VCP 形态突破 + 资金流入确认，
  综合分 62.0，数据完整度 65%。通过技术精度门禁（形态/触发价/数据质量/资金）。

### 什么时候触发
- 盘中观察使用 mock 行情：钢研高纳价格 36.00 突破触发价 35.00（+2.86%），
  观察状态从 WAITING 变为 TRIGGERED，信号写入 tradeflow_signals。
- 拓普集团价格 41.00 未达触发价 42.50，继续 WAITING。

### 触发后如何记录
- update_paper_observe_state 将观察状态同步到模拟账本。
- add_paper_candidate 将钢研高纳加入模拟账本，计划金额 1000 元。
- confirm_paper_action(buy, 35.5) 扣除现金，持仓状态变为 open。
- confirm_paper_action(sell, 38.0) 计算盈亏，持仓状态变为 closed，实现盈利。

### 盘后结果如何
- generate_review 生成盘后复盘报告，覆盖 5 只候选。
- 报告包含策略统计（VCP/POLICY_VERSION 命中率）、日期映射（plan_date → effective_trade_date）。
- get_paper_review 显示模拟账本复盘：1 笔已平仓，实现盈利。

## 约束验证

- [x] 不调用 LLM
- [x] 不写生产数据库 (tradingagents.db)
- [x] 不接真实交易（使用 mock 行情和模拟账本）
- [x] 无强买卖建议（无禁止词）

## 验收结论

全部 9 个步骤通过，TradeFlow 试用闭环端到端验收合格。
