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
- 每轮开发前先执行 `T-000 自动开发巡检基线`
- 如果没有 `[ready]` 任务，进入 `REQUEST_TASKS` 模式，只生成候选任务草案，不写代码
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
- 不把 `REQUEST_TASKS` 草案自动改成 `ready`

## 无任务时的 REQUEST_TASKS 协议

当 `./scripts/auto_dev_loop.sh --dry-run` 输出没有可开发任务时，OpenClaw 不应空转，也不应自由发明任务后直接开发。它必须进入 `REQUEST_TASKS` 模式：

1. 只读检查 `docs/TASKS.md`、`docs/DEVLOG.md`、`docs/AUTO_DEV_PLAN.md`、最近 10 个 commit 和当前 git status。
2. 输出 1-3 个候选任务草案，格式必须包含：任务编号建议、优先级、背景、修改范围、验收命令、禁止事项。
3. 候选任务状态只能是 `proposed`，不能是 `ready`。
4. 若需要 Codex 参与，可调用一次低成本任务规划请求，例如：

```bash
codex exec -s read-only "请只读检查 TradingAgents-AShare 当前任务池和最近提交，提出 1-3 个 proposed 任务。不要修改文件，不要运行股票分析，不要调用外部数据源。"
```

5. OpenClaw 将候选任务写入日报或 `docs/task_requests/YYYY-MM-DD.md`，等待孟或 Codex 确认后，才允许把其中任务转入 `docs/TASKS.md` 的 `ready` 状态。

`REQUEST_TASKS` 的目标是避免自动开发空转，同时避免没有人确认的需求被自动开发。

## 任务优先级

### P0.5 — 自动开发巡检（固定前置）

```
[ready]    每轮开发前巡检 git 状态、敏感文件、测试健康、token/API 消耗风险
[ready]    每轮开发后复核 diff、测试、DEVLOG、运行产物
[ready]    定时任务/后台进程/token 消耗风险巡检
```

### P1 — 真实事件源（当前优先）

```
[done]    接入 akshare 公告接口（沪深公告、业绩预告、回购、增持） — commit b5131cd
[ready]    N-001: 将 fetch_daily_events 接入 generate_daily_plan / candidate_engine
[ready]    接入东财新闻关键词匹配（利好/利空分类）
[blocked]  飞书 webhook 推送（需孟确认推送格式）
[blocked]  全市场 Discovery（需事件源先跑通）
```

### P1 — Simon 实践吸收（数据源 + A股标签）

```
[ready]    N-002: cn_astock provider 验收与 TradeFlow fallback 接入
[ready]    N-003: cn_astock raw_evidence 溯源接入
[ready]    N-004: 政策/游资/解禁 A股特化标签先入 TradeFlow
[ready]    N-005: 最终执行层 schema 化最小实现
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

### 巡检结果
- Git 状态
- 敏感文件/生产数据库/prompts/logs/eval_results 风险
- token/API/定时任务消耗风险

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
