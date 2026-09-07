你是独立代码审查者（round 4）。任务：UPSTREAM-081-001。仓库：/Users/maybee/TradingAgents-AShare。只读审查。

审查对象：当前**未提交**的工作区 diff（`git diff`，快照存于 docs/task_runs/UPSTREAM-081-001-20260902-191510/diff-round3-fix.txt），涉及：
- tradingagents/graph/data_collector.py
- api/main.py
- tests/test_upstream081_runtime_resilience.py
- docs/DEVLOG.md、docs/task_runs/.../implementation.md（文档）

## 背景
round3 review（docs/reviews/UPSTREAM-081-001-20260905-round3.txt）结论 FAIL，2×P1：
1. `_BoundedFetchPool` 用无界 SimpleQueue，submit 无容量上限/拒绝路径；`_run_bounded_fetch()` 超时后不对 not_done futures 调 cancel() → 饱和时过期任务在旧 worker 释放后继续执行，违反 whole-round deadline。
2. `_apply_forced_lhb()` 超时不取消 queued force=True future → LHB force retry 逃出整轮 deadline。

round3 修复声明：队列改 `queue.Queue(maxsize=FETCH_POOL_MAX_QUEUED=64)`（env 可调）；submit 饱和抛 `_FetchPoolSaturated` + `rejected_total` 计数 + 明确降级文本；整轮超时对 not_done 逐一 `cancel()`（`cancelled_total`）；forced LHB 超时先 cancel 再记账、饱和直接拒绝（`forced_rejected_saturated`）；测试 44→49。

## 必须逐项核验
- R3-1：队列确有界；饱和路径返回 FAILED/降级文本、不可能被当作成功；cancel() 真正阻止执行（worker 出队前检查 cancelled 状态）；无新无界资源；相关 gauge 在 /healthz 可观测（键名与 api/main.py 一致）。
- R3-2：`forced_timeout` 返回后本轮 force 查询执行次数必须为 0；cancel 与记账顺序正确；饱和降级文本明确。
- 回归测试是否真实覆盖 review 点名场景（池饱和→超时任务不得事后执行）；旧 pool 测试（原184-195行）语义修正是否到位。
- 有界队列引入的新风险：put 阻塞/worker cancel 交互死锁、wakeup 丢失、竞态、gauge 键不匹配。
- 范围控制：除 2×P1+测试+文档外无夹带改动；未动 prompts/；未提交。

## 允许的操作
只读 + 可运行测试（pytest 用 conftest 隔离 DATABASE_URL，不得写生产 tradingagents.db）。不要 commit、不要修改任何文件。

## 输出（中文，stdout）
- 第一行：`结论: PASS` 或 `结论: FAIL`
- R3-1 / R3-2 逐条核验（引用文件:行号）
- 新发现 findings（P0/P1/P2 分级，引用文件:行号；没有则写"无"）
- 实际运行的测试命令与结果汇总
