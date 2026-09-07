# Task: UPSTREAM-081-001 fix round 3 — 修复 round3 Codex review 的 2×P1 findings

Read task "UPSTREAM-081-001" from docs/TASKS.md and read
docs/task_runs/UPSTREAM-081-001-20260902-191510/implementation.md（设计意图与历轮修复记录）.

## 背景
- 实现存在于**未提交**的工作区 diff 中（round1+round2 的累积）：tradingagents/graph/data_collector.py、api/main.py、tests/test_upstream081_runtime_resilience.py。
- Round 3 独立 Codex review（docs/reviews/UPSTREAM-081-001-20260905-round3.txt）结论 FAIL：round2 五项修复均已核验实现，但存在 2×P1（见下），未修复前不提交。
- 本次 fix round 在**既有未提交 diff 之上**继续修复，不要回滚/重写已有实现，不要动无关文件。

## 待修复 findings（全部 2 条，逐一修复）

1. **[P1] data_collector.py:126,155-158,669-684 — `_BoundedFetchPool` 队列无界、超时不取消**
   现状：池用 `queue.SimpleQueue()`，`submit()` 无容量上限、无拒绝/降级路径；`_run_bounded_fetch()` 整轮超时后不对 `not_done` futures 调 `cancel()`。pool 被卡死任务占满时，后续每轮仍无限入队；本轮"超时跳过"的任务在旧 worker 释放后仍会继续执行——违反 whole-round deadline 语义，过期 provider probe 在分析结束后继续运行，压力随轮次无限堆积。`abandoned_total` 只计数不设防。

   修复方向（最小健壮方案，在 implementation.md 记录取舍）：
   - 队列必须有界：固定容量上限，`submit()` 在饱和时走明确拒绝/降级路径（不静默），计数入既有 healthz 可观测指标（如 abandoned/rejected 类 gauge）。
   - 整轮超时路径必须对未启动 futures 调 `cancel()`；保证 cancel 成功的 queued 任务之后**不会**被执行。对已启动但卡死的任务维持 round2 方案的上限约束（共享有界 executor、daemon 线程），不引入新的无界资源。
   - 语义验收：一轮结束（含超时）后，本轮任何 provider 调用不得再执行；后续轮次的 submit 遇饱和返回明确降级状态文本，不伪装成功。

2. **[P1] data_collector.py:718-735 — `_apply_forced_lhb()` 超时强制查询不取消**
   现状：对超时的强制 LHB future 不取消；pool 饱和时返回 `forced_timeout`，但 queued 的 force=True 查询之后仍可能被 worker 执行，等价于 LHB force retry 逃出整轮 deadline。round2 的"bounded by remaining round budget"只约束了等待时间，没有约束实际执行。

   修复方向：超时路径对该 future 显式 `cancel()`；只有确认已运行且在剩余预算内的才允许等待（沿用 round2 的 remaining budget 逻辑）；取消失败或已启动超时的，写明确降级状态文本。语义验收：`forced_timeout` 返回后，本轮不得再有 LHB force 查询执行。

## 测试补充（round3 NOTE，必须做）

- tests/test_upstream081_runtime_resilience.py 新增覆盖：**"pool 饱和时超时 future 仍留在队列并稍后执行"回归场景**——修复后该场景必须断言超时后 queued 任务不再执行。
- 现有 pool 测试（184-195 行）期待 queued work 最终全部执行，与新语义冲突，按新 deadline 语义修正。
- 为 finding 2 补断言：`forced_timeout` 后 force 查询不再执行。

## 验证命令
- `python -m py_compile tradingagents/graph/data_collector.py`
- `pytest tests/test_upstream081_runtime_resilience.py -q --tb=short`
- `pytest tests/test_api_smoke.py -q --tb=short`（回归）

## Constraints（与 round1/round2 相同）
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db（跑 pytest 用 conftest 隔离 DATABASE_URL）
- No full-market scan or stock deep TA
- No push / PR / merge
- **Do NOT git commit** — 修复候选等待独立 Codex review
- Do not update docs/TASKS.md（状态由主控管理）
- Update docs/task_runs/UPSTREAM-081-001-20260902-191510/implementation.md：新增 "fix round 3" 小节，说明每个 finding 的修复方式与取舍
- Update docs/DEVLOG.md：追加 "fix round 3" 条目（每条 finding 如何修复与验证）

## Output when done
- 每个 finding 的修复说明（对应到代码位置与关键行）
- 新增/修改的测试清单（含上述回归场景）
- 测试结果（passed/failed 数量，贴 pytest 汇总行）
