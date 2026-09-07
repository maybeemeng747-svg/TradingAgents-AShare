# UPSTREAM-081-001 Implementation — 线程池饱和、股票识别与错误语义选择性吸收

- Date: 2026-09-02
- Status: implemented; review round2 found 3xP1+2xP2 (fix round 2 done); review round3 found 2xP1 (fix round 3 done 2026-09-07), NOT committed, pending review round 4
- Upstream reference: `89754c5` (#202), v0.8.1
- Local baseline HEAD: `7e52176`

## 差异表（上游新增 / 本地已有 / 本地更强）

| # | 上游 89754c5 | 本地现状（实现前） | 决策 |
|---|---|---|---|
| A1 | `socket.setdefaulttimeout(60)`（`TA_SOCKET_DEFAULT_TIMEOUT`） | **本地更强**：`install_default_network_timeout` 同时覆盖 raw socket 与未显式传 timeout 的 `requests.Session.send` | 不动 |
| A2 | asyncio 默认 executor 64 workers + `_default_executor` 全局 | 已有（`ASYNCIO_DEFAULT_EXECUTOR_WORKERS` 可配） | 不动 |
| A3 | `/healthz` 线程池饱和探针（no-op 5s 排不上队→503+队列统计） | 已有 | 仅把 5s 改为 `_HEALTHZ_PROBE_TIMEOUT`（env `TA_HEALTHZ_PROBE_TIMEOUT`，默认 5 不变），测试可控 |
| A4 | `collect()` per-key 锁 `acquire(timeout=cap+60)` | 已有（`FETCH_LOCK_TIMEOUT=360` + 既有测试） | 不动 |
| A5 | `_fetch_all` 整轮硬上限（`TA_DATA_FETCH_TIMEOUT=300`）：`futures_wait` 超时、超时源跳过、`shutdown(wait=False, cancel_futures=True)` | **缺失**：provider calls join 无整轮上限（2026-07-27 曾有意不移植，但该决策使首个 collector 持锁路径无上界） | **补**（`_run_bounded_fetch`） |
| B1 | LLM 失败/空→`fast_symbol` regex 兜底 | 已有（两条路径均已接） | 不动 |
| B2 | regex 覆盖带后缀代码（300845.SZ 等） | **本地更强**（显式/裸码/HK/美股 + 金额排除 + 上下文） | 不动 |
| B3 | `_normalize_symbol` fallback 收紧（6 位数字或 2+ 英文） | **本地更强**（`_RESOLVABLE_SYMBOL_RE` + `_is_valid_analysis_symbol`） | 不动 |
| B4 | LLM 失败且 regex 无果→原文查本地股票名单（`_search_cn_stock_by_name(text)`，多候选取最短名） | **缺失**：直接返回"无法识别" | **补且加强**：`_resolve_cn_name_from_text_cached` 只读 warm cache（冷缓存不触发远程）、复用 fail-closed 单标的名解析（歧义→None），比上游最短名猜测更安全 |
| C1 | `_humanize_analysis_error`：402/429/401/5xx/审查/下线/连接错误→中文提示+建议 | **缺失**：`job.failed`/job.error 原样透传 `RuntimeError: ...` | **补** |
| C2 | 作用于任务失败落库 + SSE job.failed 两个出口 | **缺失** | **补**（`_run_job_inner` except + chat 流式 `_extract_and_run` except） |
| C3 | 原始错误截断 200 字符后拼入（可能带 URL/key） | — | **补且加强**：`_sanitize_analysis_error_text` 先脱敏（URL 含无 scheme 域名、sk-/Bearer/键值/32+ 不透明 token、堆栈续行仅保留首行、截断 200）再拼入 |

## 修改文件

1. `tradingagents/graph/data_collector.py`
   - `FETCH_ALL_TIMEOUT = float(env TA_DATA_FETCH_TIMEOUT, 300)`
   - 新增 `_run_bounded_fetch(tasks)`：并行抓取 + 整轮 `futures_wait` 硬上限；超时源写入含"数据获取失败"的明文（`_infer_source_status`→FAILED，不伪装成功）；`shutdown(wait=False, cancel_futures=True)` 遗弃卡死 worker（socket 默认超时兜底其生命周期）。取代 2026-07-27 的"绝不遗弃"决策——该决策留下首个 collector 持锁无上界的缺口；`FETCH_LOCK_TIMEOUT(360) > FETCH_ALL_TIMEOUT(300)` 保持排队者能熬过整轮预算。
   - `_fetch_all` 改调 `_run_bounded_fetch`。
2. `api/main.py`
   - `_HEALTHZ_PROBE_TIMEOUT`（默认 5s）；`/healthz` 探针超时改用该常量。
   - `_resolve_cn_name_from_text_cached`（warm-cache + fail-closed），接入 `_ai_extract_symbol_and_date` 与 `_ai_extract_symbol_and_date_streaming` 的 `not llm_name` 分支：fast_symbol 优先，其次本地名单兜底，兜底失败保持 (None, None) 明确错误。
   - `_ANALYSIS_ERROR_HINTS` / `_sanitize_analysis_error_text` / `_humanize_analysis_error`；接线 `_run_job_inner` 失败路径与 chat 流式 `job.failed`。job store 的 `traceback` 字段保持内部真源不对外（`/v1/jobs/{id}` 只暴露 `error`）。
3. `tests/test_upstream081_runtime_resilience.py`（新增，28 项）

## 明确不吸收 / 不修改

- 不整笔 cherry-pick：本地意图解析、Tushare 契约、门禁链全部不动。
- 上游 B4 的"多候选取最短名"不采纳（误报风险），改用本地 fail-closed 解析器。
- prompts/、模型默认厂商、生产数据库内容、非相关 provider 未动。

## 事故记录（已恢复）

实现中的一次 ad-hoc 调试脚本（非 pytest，未走 conftest 隔离）向生产
`tradingagents.db`（WAL 模式）写入了一个测试用户 `dbg081@test.com`。
发现后已做单行外科删除（先验证无子行引用，再删除并复核 count=0）。
pytest 运行本身均被 conftest 的 DATABASE_URL 隔离，未触生产库。
WAL 中 insert+delete 的页净效果为零。

## Test evidence

- `tests-round1.txt`：验收命令
  `pytest tests/test_upstream081_runtime_resilience.py tests/test_api_smoke.py -q --tb=short`
  → **115 passed**（28 新增 + 87 smoke）。
- `tests-regression-round1.txt`：相关回归
  （upstream_v081_absorption / data_collector / intent_parser / raw_evidence_vendor /
  hk001 / g006 / lhb_fund_decouple / v001_600584 / data004 / runtime_tier_contract）
  → **385 passed**。
- `py_compile api/main.py tradingagents/graph/data_collector.py` 通过。
- 约束自查：未改 prompts；未 commit/push；docs/TASKS.md 状态未动（外层脚本所有）。

## Fix round 2（2026-09-05）— round2 review 3×P1 + 2×P2 逐条取舍

前提：在 round1 未提交 diff 之上继续修，未回滚/重写既有实现，未动无关文件。

### R2-1 [P1] LHB 强制重试未纳入整轮 deadline（data_collector.py）

**取舍**：不给 `_fetch_all` 再开第二个等待窗口，而是让 force 查询消费
round1 已建立的同一 `round_deadline`（`time.monotonic() + FETCH_ALL_TIMEOUT`，
`_fetch_all` 计算一次并传入 `_run_bounded_fetch` 与 force 路径）。

- 新增 `_apply_forced_lhb(results, provider_hits, ticker, trade_date, deadline, force_reason)`
  取代原先 `_run_bounded_fetch` 返回后的裸 `_safe_with_vendor(get_lhb_detail, force=True)`：
  - 剩余预算 `remaining = deadline - now` ≤ 0 → **不提交**，`results["lhb"]`
    写"数据获取失败：lhb 强制查询因整轮预算已用尽未执行（>{FETCH_ALL_TIMEOUT}s）…"，
    `_lhb_query_mode="forced_skipped_budget"`；
  - remaining > 0 → 提交到共享 fetch pool，`futures_wait(timeout=remaining)`；
    超时 → 遗弃（`mark_abandoned(1)`）+ "数据获取失败：lhb 强制查询超时…"，
    `_lhb_query_mode="forced_timeout"`；`_infer_source_status` 对两种降级文本
    均判 FAILED（含"获取失败"），不伪装成功；
  - 成功 → 原语义不变（`"forced"` + `_lhb_force_reason`）。
- 新增 `_lhb_query_mode` 枚举值 `forced_skipped_budget` / `forced_timeout`：
  消费方（`build_raw_evidence` entry、`readiness_score._resolve_lhb`）对该值
  无硬编码分支，状态以文本 FAILED 为准，语义兼容。

### R2-2 [P1] 超时 worker 无法回收、线程无界累积（data_collector.py + api/main.py）

**取舍**：采用 review 可接受方案之一——**模块级共享有界 daemon 线程池**承担
所有 fetch 轮次（含 LHB force），而非进程外隔离或进程重启。

- 为什么不用 `ThreadPoolExecutor`+initializer 设 daemon：Python 3.12 在线程
  已启动后设置 `daemon` 直接 RuntimeError（已实测），该 hack 不可行；改用
  ~60 行自实现 `_BoundedFetchPool`：`queue.SimpleQueue` + N 个命名 daemon
  线程（`ta-data-fetch-{i}`）+ `concurrent.futures.Future`，无私有 API 依赖。
- 上限：`TA_DATA_FETCH_POOL_WORKERS`（默认 16）。单轮并发与 round1 的
  `min(10, len(tasks))` 同量级（一轮 ≤19 任务）；卡死线程泄漏上限 = 进程级
  worker 数，不再每轮 +1/超时源 无界累积。多轮并发下排队任务靠整轮 deadline
  兜底（deadline 内未跑完即明确 FAILED），等待同一 key 的排队 collector 仍由
  `FETCH_LOCK_TIMEOUT > FETCH_ALL_TIMEOUT` 不变量保护。
- 遗弃语义不变：`_run_bounded_fetch` 超时不 join 遗弃 worker，只
  `mark_abandoned(n)`；其线程存活到 socket 默认超时兜底结束，daemon 不阻
  进程退出。
- 可观测：`get_fetch_pool_stats()`（max_workers/threads/active/queued/
  abandoned_total）挂到 `/healthz` payload 的 `fetch_pool` 键（api/main.py
  顶层导入，无副作用；stats 在池未创建前报告配置上限+零计数）。

### R2-3 [P1] `Authorization: Bearer <token>` 脱敏不完整（api/main.py）

**取舍**：扩展 `_ERROR_CREDENTIAL_RE` 而非新增长链前置替换——保持单次
`re.sub` 扫描，靠备选顺序（左优先）保证"Authorization 头整体"优先于
key/value 规则（旧规则 `[keyword][seps]\S+` 只吃到字面 "Bearer"）。

- 新增三支备选：
  1. `\bauthorization\b["'=:\s]*(?:bearer|basic|token)?["'=:\s]*[^\s"',;)]+`
     —— 头名 + 可选 scheme + 凭据整体（凭据字符类含 `.`，JWT 点分不截断；
     停在空白/引号/逗号/分号/右括号，兼容 JSON `"Authorization": "Bearer …"`
     与 `authorization=Bearer …` 键值形态）；
  2. `\bbearer\s+[A-Za-z0-9._~+/=-]{8,}` —— 无头名的裸 Bearer 凭据；
  3. `\beyJ…\.[…]\.[…]+` —— 独立 JWT 三段兜底。
- 原 `sk-` / 关键字键值 / 32+ 不透明 token 规则保留（`authorization` 从
  关键字备选中移入新首支，行为对"Authorization Failed"类文案的既有遮蔽
  保持一致；hint 检测始终基于 raw，不受影响）。

### R2-4 [P2] 幂等捷径绕过脱敏（api/main.py）

**取舍**：按 review 建议字面修复——捷径命中"（原始错误："时返回
`_sanitize_analysis_error_text(raw)`（首行 + URL/凭据清洗 + 200 截断）。
重复脱敏对已清洗文本是 no-op（占位符不匹配任何规则），截断点收敛，
`f(f(x)) == f(x)` 幂等语义保持（测试锚定单行与多行两形态）。

### R2-5 [P2] 自定义 `TA_DATA_FETCH_TIMEOUT ≥ 360` 违反锁余量不变量（data_collector.py）

**取舍**：review 给了两条路（推导 / 拒绝启动），选**推导**——运维改
fetch 预算是合理操作，因连带参数没跟上而拒绝启动过于激进；推导后不变量
在任何 env 组合下恒成立。

- `FETCH_LOCK_TIMEOUT = max(env TA_DATA_FETCH_LOCK_TIMEOUT, FETCH_ALL_TIMEOUT + 60)`；
  margin 常量 `FETCH_LOCK_TIMEOUT_MARGIN=60`（与默认 360-300 差值一致）。
- 默认 300/360 行为不变（max(360, 360)=360）；显式更大的 lock 仍被尊重；
  env 被推导覆盖时打 WARNING 说明推导结果。
- 模块导入期生效，测试用 `importlib.reload` 锚定三组合（默认不变 / 360
  推导 420 / 显式 700 尊重）。

### Fix round 2 修改文件

1. `tradingagents/graph/data_collector.py`：锁超时推导；`_BoundedFetchPool` +
   `get_fetch_pool`/`get_fetch_pool_stats`/`mark_abandoned`；`_run_bounded_fetch`
   改共享池+deadline 参数；新增 `_apply_forced_lhb`；`_fetch_all` 接线
   `round_deadline`。
2. `api/main.py`：`_ERROR_CREDENTIAL_RE` 三支新备选；幂等捷径走
   `_sanitize_analysis_error_text`；`/healthz` 增 `fetch_pool` gauges。
3. `tests/test_upstream081_runtime_resilience.py`：28 → 44 项（新增共享池/
   LHB 预算/配置不变量/脱敏加强，加强默认余量断言）。

### Fix round 2 Test evidence

- 验收命令 `pytest tests/test_upstream081_runtime_resilience.py
  tests/test_api_smoke.py -q --tb=short` → **131 passed**。
- 相关回归（data_collector / data_p1_lhb_fund_decouple / g006 / g007 /
  upstream_v081_absorption / data004 / intent_parser / raw_evidence_vendor）
  → **289 passed**；（data017 / data022 / hk001 / runtime_tier_contract /
  v001_600584）→ **244 passed**。
- `py_compile api/main.py tradingagents/graph/data_collector.py` 通过。
- 约束自查：未改 prompts；未写生产库（仅 pytest，conftest 隔离）；未
  commit/push；docs/TASKS.md 状态未动。

## Fix round 3（2026-09-07）— round3 review 2×P1 逐条取舍

前提：在 round1+round2 未提交 diff 之上继续修，未回滚/重写既有实现，
未动无关文件。round2 的五项修复全部保留。

### R3-1 [P1] `_BoundedFetchPool` 队列无界、超时不取消（data_collector.py）

**取舍**：队列从"无界 SimpleQueue"改为**有界 `queue.Queue(maxsize)`**，
饱和走**显式拒绝 + 降级文本**而不是静默入队；整轮超时路径对
`not_done` futures 逐一 `cancel()`，按 cancel 结果分流记账。不引入新的
无界资源，已启动卡死任务维持 round2 的共享有界池 + daemon + socket
默认超时上限。

- 新增 `FETCH_POOL_MAX_QUEUED`（env `TA_DATA_FETCH_POOL_MAX_QUEUED`，
  默认 64 ≈ 3 个整轮任务的积压上限）；`_BoundedFetchPool(max_workers,
  max_queued=None)`，`submit()` 用 `put_nowait`，`queue.Full` → 计入
  `rejected_total` gauge 并抛 `_FetchPoolSaturated`（调用方必须降级）。
- `_run_bounded_fetch`：
  - 提交循环 try/except `_FetchPoolSaturated` → 该 key 写
    "数据获取失败：… 数据拉取被拒（线程池饱和）…"（`_infer_source_status`
    → FAILED），不静默、不伪装成功，继续提交其余任务；
  - `futures_wait` 超时后对每个 `not_done` future 调 `future.cancel()`：
    cancel 成功 = 仍在排队 → **保证永不执行**（worker 出队后
    `set_running_or_notify_cancel()` 返回 False 直接跳过，这是
    `concurrent.futures.Future` 的状态机保证）→ 计入新增
    `cancelled_total` gauge；cancel 失败 = 已在 worker 上运行 → 维持
    round2 语义 `mark_abandoned(n)`（上限 = 进程级 worker 数 + socket
    默认超时兜底）。
- 取舍说明：为什么不再用"只计数不设防"——review 指出被卡死任务占满池后，
  后续每轮"超时跳过"的任务会在旧 worker 释放后继续执行，压力随轮次无限
  堆积且过期 probe 在分析结束后仍在跑；只有 cancel（排队任务）+ 拒绝
  （饱和时的新任务）能把"一轮结束（含超时）后本轮 provider 调用不再执行"
  变成状态机保证而非君子协定。已在运行的 provider 调用无法撤回，保留
  round2 的有界遗弃约束是 review 明确接受的上限方案。
- 可观测：`stats()` / `get_fetch_pool_stats()` 新增 `max_queued`、
  `cancelled_total`、`rejected_total`（零计数路径同步），随 round2 的
  `fetch_pool` 键自动暴露到 `/healthz`。

### R3-2 [P1] `_apply_forced_lhb()` 超时强制查询不取消（data_collector.py）

**取舍**：沿用 round2 的"remaining budget 内才等待"逻辑，但在
`futures_wait` 超时后**先 cancel 再记账**，让 `forced_timeout` 成为
"本轮强制查询彻底终止"的硬语义而不是"等待放弃"。

- 超时路径：`future.cancel()` 成功（= 排队中未启动）→ `mark_cancelled(1)`
  + degraded 文本，该 force=True 查询保证永不执行；cancel 失败（= 已启动
  运行）→ 维持 `mark_abandoned(1)` + degraded 文本（有界遗弃）。两种情况
  均写 "数据获取失败：lhb 强制查询超时…"（FAILED），`_lhb_query_mode` 保持
  `"forced_timeout"`（消费方无硬编码分支，语义兼容）。
- 新增池饱和路径：`submit` 抛 `_FetchPoolSaturated` → 不入队，写
  "数据获取失败：lhb 强制查询被拒（线程池饱和）…"，新增枚举值
  `_lhb_query_mode="forced_rejected_saturated"`；`build_raw_evidence` /
  `readiness_score` 对 query_mode 值无分支（透传），文本 FAILED 判定不变。
- 语义验收：`forced_timeout` 返回后本轮不得再有 LHB force 查询执行——
  排队的被 cancel，已运行的有界遗弃，饱和的根本没入队。

### Fix round 3 测试补充（round3 NOTE 逐项落地）

1. **回归场景（review 点名）**：`test_round_timeout_cancels_queued_work_so_it_never_runs`
   —— 1-worker 池被卡死任务占满，queued 任务随整轮超时"跳过"；修复后断言
   gate 释放 + 宽限期后该任务**从未执行**（`executed == []`），且
   `cancelled_total==1`、`abandoned_total==1`（只计已运行者）。
2. **修正与新语义冲突的旧 pool 测试**（原 184-195 行，期待 queued work
   最终全部执行）：`test_pool_is_bounded_named_and_daemonized` 改为对
   queued futures 先 `cancel()`，断言 workers 释放后它们不执行。
3. **池饱和拒绝**：`test_submit_rejects_when_queue_is_full`（`_FetchPoolSaturated`
   + `rejected_total` 计数）、`test_bounded_fetch_degrades_explicitly_when_pool_saturated`
   （`_run_bounded_fetch` 降级文本 FAILED、`hits` 无 vendor、任务不执行）。
4. **Finding 2 断言**：
   - `test_forced_lhb_queued_future_is_cancelled_and_never_executes` ——
     池饱和时 force 查询排队，`forced_timeout` 后 force 调用次数为 0
     （修复前会事后执行）；
   - `test_forced_lhb_times_out_inside_remaining_budget` 增加调用记录断言
     `len(force_calls) == 1`（仅超时前已启动的那一次，之后不再执行）；
   - `test_fetch_all_keeps_forced_lhb_inside_round_deadline` 整轮集成增加
     `force_calls == 1` / `calls == 2` 精确断言（round3 NOTE 指其原先只验
     及时返回）；
   - `test_forced_lhb_rejected_when_pool_saturated` —— 饱和降级
     `forced_rejected_saturated`，force 调用 0 次。
5. `test_healthz_reports_fetch_pool_gauges` 增补 `max_queued`/
   `cancelled_total`/`rejected_total` 键断言。

### Fix round 3 修改文件

1. `tradingagents/graph/data_collector.py`：`FETCH_POOL_MAX_QUEUED`；
   `_FetchPoolSaturated`；`_BoundedFetchPool`（有界队列 + 拒绝 +
   `mark_cancelled` + 新 gauges）；`_run_bounded_fetch`（饱和降级 +
   超时 cancel 分流记账）；`_apply_forced_lhb`（饱和降级 + 超时 cancel）。
2. `tests/test_upstream081_runtime_resilience.py`：44 → 49 项（新增 5、
   加强 3，按新 deadline 语义修正 1 项旧断言）。
3. `api/main.py`：无代码改动（`fetch_pool` gauges 经
   `get_fetch_pool_stats()` 透传新键）。

### Fix round 3 Test evidence

- `python -m py_compile tradingagents/graph/data_collector.py api/main.py`
  通过。
- 验收命令 `pytest tests/test_upstream081_runtime_resilience.py -q
  --tb=short` → **49 passed**；`pytest tests/test_api_smoke.py -q
  --tb=short` → **87 passed**。
- 相关回归（data_collector / LHB decouple / G-006 / G-007 / v081
  absorption / DATA-004 / DATA-017 / DATA-022）→ **324 passed**。
- 约束自查：未改 prompts；未写生产库（仅 pytest，conftest 隔离）；未
  commit/push；docs/TASKS.md 状态未动。
