# AUTO_DEV_PLAN.md — 自动开发路线图

> OpenClaw 按本文档执行，孟按本文档验收。所有变更需经孟确认后才能 push。

## 当前阶段

```
P0  ✅ TradeFlow 候选池最小闭环
P0.5 ✅ 过滤阈值校准、可观测性
P1   → 真实事件源 + 小范围 Discovery
P2   → 盘中 Observe + OpenClaw 自动触发 TA
P3   → 盘后 Review + 策略命中率复盘
```

## 自动开发允许做的事

- 从下方"当前任务池"中挑选标记为 `[ready]` 的任务
- 单个任务控制在 1-2 个 commit 内完成
- 补测试、修 bug、优化过滤逻辑
- 更新 DEVLOG.md（只追加，不重排历史）
- 跑测试验证

## 自动开发禁止做的事

- 不改 `tradingagents/prompts/`
- 不改 TA 多 Agent 核心裁判逻辑（risk_manager/research_manager 的核心流程）
- 不改生产数据库 schema（只能新增 tradeflow 表）
- 不自动推送飞书
- 不自动跑全市场扫描
- 不输出买入/卖出强建议
- 不调用 DeepSeek 大模型做开发
- 不删除现有测试
- 不跨任务做大重构
- 不自由发挥做不在任务池里的功能

## 任务优先级

### P1 — 真实事件源（当前优先）

```
[ready]    接入 akshare 公告接口（沪深公告、业绩预告、回购、增持）
[ready]    接入东财新闻关键词匹配（利好/利空分类）
[blocked]  飞书 webhook 推送（需孟确认推送格式）
[blocked]  全市场 Discovery（需事件源先跑通）
```

### P1 — 小范围 Discovery

```
[ready]    行业池：按申万二级行业扫描（先做 1-2 个行业试点）
[ready]    资金异动池：主力资金净流入 Top N
[blocked]  全市场扫描（需先验证小范围逻辑）
```

### P2 — 盘中 Observe

```
[blocked]  盘中低频触发（每 30 分钟检查一次候选池触发价）
[blocked]  触发后自动调 TA 深度分析
[blocked]  飞书推送触发通知
```

### P3 — 盘后 Review

```
[blocked]  盘后复盘：今日命中率、误报率
[blocked]  策略权重自动调整
[blocked]  周度/月度统计
```

## 验收标准

### TradeFlow 相关

1. 候选是否虚假命中
2. 过滤原因是否准确（流动性差/无策略命中/ST/数据缺失）
3. 事件是否串票（event_overrides 按 symbol 过滤）
4. 触发价/失效价是否被覆盖（primary_strategy 保护）
5. need_deep_ta 是否合理
6. 是否输出强买卖词（禁止）

### TA 深度分析相关

1. 未持仓是否 WAIT（非 HOLD）
2. Buy/Risk Level 是否合理
3. 执行质检区是否落地
4. 强动作门禁是否生效
5. raw_evidence 是否传递
6. 模型冲突是否被正确处理
7. 止损价是否污染未持仓报告

### 通用

1. 有没有遵守 AGENTS.md
2. 有没有误改生产数据库/prompts/logs/eval_results
3. diff 是否聚焦
4. 代码逻辑有没有 bug
5. 测试是否覆盖关键边界
6. 是否真的解决任务，而不是只做表面改动
7. 是否需要补测或回滚

## 日报格式

OpenClaw 每天自动开发后，输出以下格式：

```markdown
## 自动开发日报 YYYY-MM-DD

### 今日任务
- 选了什么任务
- 为什么选这个任务

### 修改清单
- 改了哪些文件
- 每个文件改了什么（一句话）

### 测试结果
- 跑了哪些测试
- 结果（passed/failed）

### Commit
- commit hash
- commit message

### 风险
- 有没有风险
- 需要确认什么

### 建议
- 是否建议 push
- 下一步建议
```

## 固定节奏

```
每天 10:30    OpenClaw 自动开发（从任务池挑 [ready] 任务）
中午/晚上     孟拿日报发 review
review 通过   孟决定 push
review 不通过 孟把修改意见丢回 OpenClaw/OpenCode
```

## 版本

- v1.0 — 2026-05-25 初始版本
