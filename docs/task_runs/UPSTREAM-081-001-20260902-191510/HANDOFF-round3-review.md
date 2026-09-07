# HANDOFF — UPSTREAM-081-001 round3 review 续跑（2026-09-05 18:00）

你是自动开发循环的续跑会话。目标：完成 UPSTREAM-081-001 的 Codex round3 独立 review，并按结果提交或打回。本手册自包含，按序执行。

项目目录：/Users/maybee/TradingAgents-AShare（分支 local/tradingagents-custom）

## 当前状态（14:26 固化）

- fix round2 已完成：round2 review（docs/reviews/UPSTREAM-081-001-20260903-round2.txt）的 3×P1+2×P2 全部修复。变更全部在工作区**未提交**：
  - M api/main.py、M tradingagents/graph/data_collector.py、M docs/DEVLOG.md、M docs/TASKS.md
  - ?? tests/test_upstream081_runtime_resilience.py、?? docs/reviews/UPSTREAM-081-001-20260902-round1.txt、?? docs/reviews/UPSTREAM-081-001-20260903-round2.txt、?? docs/task_runs/UPSTREAM-081-001-20260902-191510/
- 独立复验已过：`pytest tests/test_upstream081_runtime_resilience.py tests/test_api_smoke.py -q` → 131 passed；py_compile 通过
- round3 review 已尝试 2 次失败：attempt1 默认模型 gpt-6-astra 超出 CLI 0.144.1 能力；attempt2 `--model gpt-5.5` 触发 OpenAI usage limit（额度 17:50 重置）
- docs/TASKS.md：队列行（约 59 行）与详情状态行（约 375 行）均为 in_progress（等待本次续跑收口）

## 执行步骤（严格按序）

1. 加锁：`echo "round3-review-continuation $(date +%s)" > /Users/maybee/.openclaw/workspace/.auto_dev.lock`
2. `cd /Users/maybee/TradingAgents-AShare && git status --short` 核对与上述固化状态一致；若出现意外的新修改（非本任务文件），停止并按"FAIL 处置"第 3-4 步收口汇报。
3. 跑 review（约 10-30 分钟，**后台执行，耐心轮询到结束，不要提前中断**）：
   ```
   cd /Users/maybee/TradingAgents-AShare && codex exec --sandbox read-only --model gpt-5.5 "$(cat docs/task_runs/UPSTREAM-081-001-20260902-191510/review-prompt-round3.md)" > docs/task_runs/UPSTREAM-081-001-20260902-191510/codex-review-round3-raw.txt 2>&1
   ```
   - 若仍报 usage limit：把输出归档为 codex-review-round3-raw-attempt3-quota.log，改 `--model gpt-5.6-terra` 再试**一次**；再失败 → 按"仍失败处置"。
4. 写 review 档案 docs/reviews/UPSTREAM-081-001-20260905-round3.txt，格式对齐 round1/round2：
   ```
   Task: UPSTREAM-081-001 - 线程池饱和、股票识别与错误语义选择性吸收（P1）
   Date: 2026-09-05_HH:MM
   Codex exit code: <code>
   Round: 3 (fix round2 后复审)
   ---
   <codex 最终结论文本>
   ```

## PASS 处置（结论: PASS，无 P0/P1/P2 correctness finding）

1. 复跑验收（必须全绿）：`cd /Users/maybee/TradingAgents-AShare && source .venv/bin/activate && python -m py_compile api/main.py tradingagents/graph/data_collector.py && pytest tests/test_upstream081_runtime_resilience.py tests/test_api_smoke.py -q --tb=short`
2. 提交1（代码+测试+docs 档案，**不含 TASKS.md**）：
   ```
   git add api/main.py tradingagents/graph/data_collector.py tests/test_upstream081_runtime_resilience.py docs/DEVLOG.md docs/reviews/UPSTREAM-081-001-20260902-round1.txt docs/reviews/UPSTREAM-081-001-20260903-round2.txt docs/reviews/UPSTREAM-081-001-20260905-round3.txt docs/task_runs/UPSTREAM-081-001-20260902-191510
   git commit -m "fix(api): close round2 P1/P2 findings for runtime resilience absorption [UPSTREAM-081-001]"
   ```
3. 更新 docs/TASKS.md（只改两处状态行）：
   - 队列行：``29. `UPSTREAM-081-001`：线程池饱和、股票识别与错误语义选择性吸收（P1，done — `<提交1哈希>`）。``
   - 详情行：`- **状态**：done — <提交1哈希>（round2 fix + round3 review PASS 收口，见 docs/task_runs/UPSTREAM-081-001-20260902-191510）`
   - UPSTREAM-081-002 队列行（约 60 行）状态从 blocked-auto 改为 ready（其 depends_on=081-001，auto_release=true；详情行不动）
4. 提交2：`git add docs/TASKS.md && git commit -m "docs(tasks): mark UPSTREAM-081-001 done at <提交1哈希>"`
5. 更新 /Users/maybee/.openclaw/workspace/logs/auto_dev/summary.md 为最终结果
6. 删锁：`rm /Users/maybee/.openclaw/workspace/.auto_dev.lock`
7. 汇报：review 结论、两个提交哈希、测试结果、081-002 是否释放

## FAIL 处置（有 P0/P1/P2 correctness finding）

1. **不提交**，变更保持工作区未提交
2. docs/TASKS.md：队列行 → `（P1，blocked — round3 review 有 findings，待 fix round3）`；详情行 → `- **状态**：blocked — round3 review findings, see docs/reviews/UPSTREAM-081-001-20260905-round3.txt`
3. 更新 logs/auto_dev/summary.md；删锁
4. 汇报 findings 清单（每条一行：级别 file:line 摘要）。**不要**自动再派 fix round——等下一次循环或人工决策

## 仍失败处置（Codex 额度/服务仍不可用）

- 不提交；docs/TASKS.md 详情行 → `- **状态**：blocked — NEEDS_HUMAN（Codex 额度未恢复，round3 review 待重试）`；队列行保持 in_progress
- 更新 logs/auto_dev/summary.md；删锁；如实汇报
- 只允许本次 + 一次改模型重试；**不得**无限重试、**不得**绕过 review 直接提交

## 红线

- 禁止 push / PR / merge；禁止改 tradingagents/prompts/、生产库、无关 provider；禁止绕过 review 提交
- docs/TASKS.md 只按上述指定的状态行修改
- 测试只允许 pytest（conftest 已隔离生产库）；禁止任何 ad-hoc 脚本直连 tradingagents.db
