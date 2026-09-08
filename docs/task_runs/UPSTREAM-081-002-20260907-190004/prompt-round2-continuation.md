# Task: UPSTREAM-081-002 continuation — 完成工作区中已存在的半成品实现（round1 超时续跑）

Read task "UPSTREAM-081-002" from docs/TASKS.md and read
docs/task_runs/UPSTREAM-081-002-20260907-190004/（round1 运行记录与超时说明）.

## 背景
- Round1 OpenCode 在 1800s 超时被杀（NEEDS_HUMAN），但工作区已留下**实质性半成品**（git diff 约 451 行）：
  - api/job_store_redis.py、api/main.py、api/services/report_service.py（后端软/硬超时与恢复）
  - frontend/src/hooks/useSSE.ts、stores/analysisStore.ts、components/ChatCopilotPanel.tsx、types/index.ts
  - 新增 frontend/src/utils/jobLifecycle.ts + jobLifecycle.test.ts（未跟踪）
- 本次任务是**续跑收尾**：在既有未提交 diff 之上继续，不要回滚/重写已有实现，不要动无关文件。

## 任务目标（照抄任务卡）
软超时=「继续后台运行」的非终态事件 + 独立硬超时 fail-closed；前端断线/刷新后可恢复同一 job，禁止重复提交昂贵分析；软超时、硬超时、真实失败、完成四态可区分；复用本地 `_job_timeout_for_config`、持久化报告、SSE 和 job store；不得把历史 failed(timeout) 无条件重写为 running。

## 续跑步骤
1. 先全面盘点现状：git diff 逐文件读懂 round1 已实现的 parts；对照任务卡列「已实现/半成品/缺失」清单（写入 implementation 记录）。
2. 补齐缺失与半成品部分，重点核对验收项：
   - 软超时后 job 仍运行且最终**只产生一次** completed/failed（防双写）
   - 硬超时 fail-closed；历史 failed(timeout) 不被重写为 running
   - 刷新/断线恢复不重复创建 job；Redis 与内存 store 语义一致
   - 前端展示「后台继续」而非失败（jobLifecycle 生命周期工具与 SSE/store 接线完整性）
3. 测试收尾：
   - 后端：`pytest tests/test_upstream081_timeout_recovery.py -q --tb=short`（若 round1 未建此文件，按验收方式命名补齐；复用 conftest 隔离 DATABASE_URL）
   - 回归：`pytest tests/test_api_smoke.py -q --tb=short`
   - 前端：`cd frontend && npm run build` 零错误；jobLifecycle.test.ts 用 vitest 跑（如配置了）
4. 文档：docs/DEVLOG.md 追加 round2（continuation）条目；docs/task_runs/UPSTREAM-081-002-20260907-190004/ 下追加 continuation 记录（已实现/补齐/测试结果）。

## Constraints（不变）
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No full-market scan or stock deep TA
- No push / PR / merge
- **Do NOT git commit** — 修复候选等待独立 Codex review
- Do not update docs/TASKS.md 状态（主控管理）
- 不改默认模型、分析 Agent 图、scheduler 时间、强动作门禁、根目录 .env.example

## Output when done
- 「已实现/本次补齐/测试证据」三段式总结
- 测试结果（贴 pytest 与 npm run build 汇总行）
- 遗留缺口（如有，如实列出）
