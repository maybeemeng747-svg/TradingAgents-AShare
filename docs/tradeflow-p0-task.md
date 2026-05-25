# TradeFlow P0 任务包

## 目标

在 `TradingAgents-AShare` 现有 TA 深度分析系统之上，新增一层 **TradeFlow 交易工作流模块**。

定位：
- TradeFlow 负责发现候选、生成盘前计划、记录信号
- TA 负责单票深度分析与最终裁判
- OpenClaw 负责调度执行和报告整理

第一版不输出强交易建议，只输出：
- 今日观察池
- 入池原因
- 关键触发价
- 失败条件
- 是否需要深度 TA

---

## P0 范围

### 1. 新增 TradeFlow 模块目录

```
tradingagents/tradeflow/__init__.py
tradingagents/tradeflow/schemas.py
tradingagents/tradeflow/universe.py
tradingagents/tradeflow/candidate_engine.py
tradingagents/tradeflow/plan_runner.py
tradingagents/tradeflow/review_runner.py
tradingagents/tradeflow/strategies/__init__.py
tradingagents/tradeflow/strategies/vcp.py
tradingagents/tradeflow/strategies/pullback_support.py
tradingagents/tradeflow/strategies/event_catalyst.py
```

P0 不做盘中 Observe，不做飞书推送。

### 2. 候选池来源

P0 先用这几类：
1. 当前持仓股
2. 自选股 / watchlist
3. 用户手动指定股票
4. 昨日 Review 留下的观察股
5. 公告 / 财报 / 事件触发股

不要求全市场扫描（放 P1）。

### 3. P0 策略桶（3个）

#### A. VCP / 缩量整理

入池逻辑：
- 前期有上涨或趋势基础
- 最近进入横盘/收敛
- 波动区间变窄
- 成交量下降
- 价格没有跌破关键均线
- 距离箱体上沿不远

输出字段：
```
strategy_tag: VCP
trigger_price: 箱体上沿 / 近期压力位
invalid_price: 箱体下沿 / 关键均线
reason: 缩量整理，等待放量突破确认
```

#### B. 回踩支撑

入池逻辑：
- 中期趋势未破
- 回踩 20日/30日/60日线或箱体下沿
- 回调缩量
- 最近出现止跌迹象
- 支撑位明确

输出字段：
```
strategy_tag: PULLBACK_SUPPORT
trigger_price: 重新站回关键位
support_price: 当前支撑位
invalid_price: 跌破支撑位
reason: 趋势内回踩，风险位清楚
```

#### C. 财报/事件催化

入池逻辑：
- 近期有公告、业绩预告、季报、回购、增持、中标、订单、并购等事件
- 事件被分类为利好/利空/不确定
- 需要市场价格确认

输出字段：
```
strategy_tag: EVENT_CATALYST
event_type
event_direction
verification_condition
risk_flags
reason: 有事件催化，但需价格与成交量验证
```

### 4. 结构化表（新增 SQLite 表，不改老业务表）

#### 表1：tradeflow_candidates
```
id, trade_date, symbol, name, source, strategy_tags_json, score,
status, trigger_price, support_price, invalid_price, need_deep_ta,
evidence_json, risk_flags_json, created_at, updated_at
```

#### 表2：tradeflow_signals
```
id, signal_time, symbol, signal_type, signal_level, source,
evidence_json, action_hint, status, created_at
```

#### 表3：tradeflow_daily_plans
```
id, trade_date, mode, summary, candidates_json, metadata_json, created_at
```

**注意：测试必须使用测试 DB，不能污染生产 tradingagents.db。**

### 5. 盘前 Plan 输出格式

盘前 Plan 必须包含：
1. 今日持仓风险
2. 今日重点观察候选
3. 每只股票的入池策略
4. 原始证据摘要
5. 关键触发价
6. 失败条件
7. 是否需要深度 TA
8. 禁止追高/暂不处理名单

动作只允许：OBSERVE / WAIT_TRIGGER / NEED_DEEP_TA / REMOVE_FROM_WATCH

禁止输出：立即买入 / 重仓买入 / 立即清仓

### 6. 和 TA 的连接方式

P0 只打标 `need_deep_ta = true/false`，不直接调用完整 TA。

触发条件：
- VCP 接近突破
- 回踩支撑出现止跌
- 事件催化需要深度解释
- 多个策略同时命中
- 持仓股出现风险

P1 再做：need_deep_ta=True 时由 OpenClaw 调度 TA 跑完整分析。

### 7. CLI

```bash
python scripts/run_tradeflow_plan.py --date 2026-05-25
python scripts/run_tradeflow_plan.py --symbols 002353.SZ,603256.SH
```

P0 有 CLI 就够，API 放 P1。

### 8. 测试要求

新增测试文件：
```
tests/test_tradeflow_schemas.py
tests/test_tradeflow_candidate_engine.py
tests/test_tradeflow_strategies.py
tests/test_tradeflow_plan_runner.py
```

必须覆盖：
- VCP 命中 / 不命中
- 回踩支撑命中 / 跌破失效
- 事件催化命中
- ST / 流动性差 / 数据缺失被过滤
- 多策略命中时合并去重
- need_deep_ta 标记正确
- 盘前 Plan 不输出强买卖词

完成后运行：
```bash
source .venv/bin/activate && pytest tests/test_tradeflow_*.py -q
```

### 9. 禁止事项

- 不要改 tradingagents/prompts/
- 不要改 TA 多 Agent 核心裁判逻辑
- 不要自动推送飞书
- 不要自动跑全市场
- 不要输出买入/卖出强建议
- 不要调用 DeepSeek 大模型
- 不要污染生产数据库

### 10. 交付标准

完成后输出：
1. 修改文件列表
2. 新增表结构说明
3. 候选池策略规则说明
4. 测试结果
5. 用 002353.SZ、603256.SH 跑一次样例
6. 生成一份盘前 Plan 示例
7. 是否有 need_deep_ta=true 的候选
8. docs/DEVLOG.md 更新
