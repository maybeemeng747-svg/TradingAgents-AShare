# Task: UPSTREAM-081-001 fix round 2 — 修复 round2 Codex review 的 3×P1 + 2×P2 findings

Read task "UPSTREAM-081-001" from docs/TASKS.md and read
docs/task_runs/UPSTREAM-081-001-20260902-191510/implementation.md（设计意图与差异表）.

## 背景
- 实现已存在于**未提交**的工作区 diff 中：api/main.py、tradingagents/graph/data_collector.py、tests/test_upstream081_runtime_resilience.py。
- Round 2 独立 Codex review（docs/reviews/UPSTREAM-081-001-20260903-round2.txt）结论：3×P1 + 2×P2，未修复前不提交。
- 本次 fix round 在**既有未提交 diff 之上**继续修复，不要回滚/重写已有实现，不要动无关文件。

## 待修复 findings（全部 5 条，按编号逐一修复）

1. **[P1] data_collector.py ~620-671 — LHB 强制重试未纳入整轮 deadline**
   `_fetch_all` 超时后 `_compute_lhb_force_decision` 触发的 `_safe_with_vendor(get_lhb_detail, force=True)` 在整轮预算外执行；provider 卡死仍会长期持有 per-key 锁与 executor 线程。
   修复方向：LHB force 查询必须在剩余整轮预算内执行（用同一 deadline 计算 remaining budget，预算不足或超时则跳过并写明确的降级状态文本），不得在 `_run_bounded_fetch` 之外无界运行。

2. **[P1] data_collector.py ~508-546 — 超时 worker 无法回收，线程/连接无界累积**
   `cancel_futures` 取消不了运行中的线程，`shutdown(wait=False)` 任其存活；每轮新建 executor 导致线程/连接无界累积。review 建议改用共享的有界隔离机制。
   修复方向：选最小健壮方案。可接受方案之一：模块级共享的有界 executor（固定 worker 上限、daemon 线程、命名线程池）承担所有 fetch 轮次，泄漏的卡死线程受上限约束不再随轮数累积；并在 `/healthz` 饱和探针统计中可观测泄漏/积压。若选其他方案，须保证：线程总数有界、每轮 deadline 语义不变、队列排队者仍能靠 `FETCH_LOCK_TIMEOUT > FETCH_ALL_TIMEOUT` 熬过整轮预算。在 implementation.md 中记录你的取舍。

3. **[P1] api/main.py ~2685-2741 — `Authorization: Bearer <token>` 脱敏不完整**
   key/value 备选只吃到字面 "Authorization: Bearer"，真实 token 与 JWT 原样落库 job.error 并经 SSE 外发。
   修复方向：`_sanitize_analysis_error_text` 必须把 `Authorization: Bearer <token>` 整体（含 token/JWT 部分）替换为脱敏占位；同时覆盖 key=/value= 及其他 header 形态；不泄漏 key/URL/内部堆栈。

4. **[P2] api/main.py ~2736 — 幂等捷径绕过脱敏**
   错误文本已含 "（原始错误：" 时直接返回原始串，未过 URL/凭据/堆栈清洗。
   修复方向：幂等捷径返回前同样走 `_sanitize_analysis_error_text`（幂等语义保持：重复脱敏结果稳定）。

5. **[P2] data_collector.py ~69-75 — 自定义 `TA_DATA_FETCH_TIMEOUT ≥ 360` 违反锁超时余量不变量**
   修复方向：模块加载时从 fetch 预算推导锁期限（如 `FETCH_LOCK_TIMEOUT = max(env_lock, FETCH_ALL_TIMEOUT + margin)`）或对不兼容配置（lock <= fetch budget）直接拒绝启动并给出清晰报错；默认 300/360 行为保持不变。补对应测试。

## 测试要求
- 在 tests/test_upstream081_runtime_resilience.py 中为每条修复补/改测试（#1 预算内执行/超时跳过、#2 线程上限可观测、#3 Bearer/JWT 脱敏、#4 捷径脱敏、#5 配置不变量）。
- 验收命令（必须全绿）：
  `python -m py_compile api/main.py tradingagents/graph/data_collector.py`
  `pytest tests/test_upstream081_runtime_resilience.py tests/test_api_smoke.py -q --tb=short`

## Constraints（与 round1 相同）
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db；禁止任何 ad-hoc 脚本直连生产库——只允许 pytest（conftest 已隔离 DATABASE_URL）
- No full-market scan or stock deep TA
- No push / PR / merge
- **Do NOT git commit** — 外层会话负责提交
- Do not update docs/TASKS.md 的任务状态；外层会话负责状态流转
- Update docs/DEVLOG.md：追加 "fix round 2" 条目（逐条 finding → 修复方式一句话）
- 更新 docs/task_runs/UPSTREAM-081-001-20260902-191510/implementation.md：补记每条 finding 的取舍与方案

## Output when done
- 逐条 finding 的修复摘要（一行一条）
- 修改了哪些文件
- 测试结果（passed/failed 数量）
