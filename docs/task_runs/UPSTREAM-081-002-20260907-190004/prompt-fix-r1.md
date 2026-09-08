# Task: UPSTREAM-081-002 fix — 修复 r1 Codex review 的 1×P1 + 1×P2

Read task "UPSTREAM-081-002" from docs/TASKS.md and read
docs/task_runs/UPSTREAM-081-002-20260907-190004/implementation-round2.md（实现盘点）与
docs/reviews/UPSTREAM-081-002-20260907-r1.txt（r1 review 档案）.

## 背景
- 候选实现已在**未提交**工作区（round1 超时半成品 + round2 续跑收尾的累积 diff）。
- r1 独立 review 结论 FAIL：候选八项验收中七项通过，但 1×P1（Redis 终态重放缺失）+ 1×P2（测试盲区）。
- 本次在既有未提交 diff 之上修复，不要回滚/重写已有实现，不要动无关文件。

## 待修复 findings

1. **[P1] api/job_store_redis.py:169 — Redis SSE 订阅错过终态事件后不重放终态**
   现状：`subscribe` 轮询发现 `completed/failed` 后直接 `break`，不产出终态事件；内存 store（api/job_store.py:260-264）在超时轮询发现终态会补发 `job.completed/job.failed`。结果：走 Redis 的 `/v1/jobs/{job_id}/events`（api/main.py:4895）订阅端只收到 `job.ready` 就结束，SSE 消费方永远等不到终态。
   修复方向：Redis 路径在结束前按内存 store 同样语义补发终态事件（`job.completed` 或 `job.failed`，payload 与内存版一致：含 status/result/error 等既有字段）；保持其余事件顺序与去重语义不变；不引入对 Redis 数据结构的额外写入。

2. **[P2] Redis 订阅终态重放/恢复语义无专项测试**
   现状：test_job_store_redis.py 整模块依赖 Redis 可用（本机未运行即 skip），新增超时测试只覆盖 bool 序列化。
   修复方向（最小健壮，取舍写进 implementation 记录）：把「错过终态→补发哪个事件」的决策抽成可不依赖活 Redis 的纯函数/薄层并加单测；如 Redis 本机可用（`redis-cli ping` 通过）再加一条订阅重放集成测试（带 skip 守卫），不得引入新第三方依赖（如 fakeredis）除非确有必要并说明。

## 验证
- `pytest tests/test_upstream081_timeout_recovery.py tests/test_api_smoke.py tests/test_upstream081_runtime_resilience.py -q --tb=short`
- `pytest tests/test_job_store_redis.py -q --tb=short`（Redis 可用则须全过；不可用则如实记录 skip）
- 前端本轮未改动则无需重跑 vitest/build；若意外触碰需重跑并在输出中说明

## Constraints（不变）
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push / PR / merge
- **Do NOT git commit** — 等待独立 review
- Do not update docs/TASKS.md 状态（主控管理）
- 不改默认模型、分析 Agent 图、scheduler 时间、强动作门禁、.env.example
- docs/DEVLOG.md 追加 fix 条目；docs/task_runs/UPSTREAM-081-002-20260907-190004/implementation-round2.md 追加修复说明与取舍

## Output when done
- P1 修复的代码位置与事件序列说明（修复前后订阅端收到的事件对比）
- P2 测试方案与取舍
- 测试结果（贴 pytest 汇总行；Redis 相关如实标注 skipped/passed）
