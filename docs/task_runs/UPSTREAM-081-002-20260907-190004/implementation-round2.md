# UPSTREAM-081-002 round2 continuation 实现记录

- Date: 2026-09-07 20:10
- Runner: OpenCode（主控派发，round1 超时续跑）
- Base: round1 未提交工作区（451 行 diff），HEAD feb9c05
- Constraint: 未 commit；未动 prompts/生产 DB/默认模型/门禁/scheduler 时间/.env.example；未改 docs/TASKS.md 状态

## 一、round1 已实现盘点（保留，未回滚）

### 后端
| 文件 | 内容 | 状态 |
|---|---|---|
| `api/main.py` | `_JOB_HARD_TIMEOUT`（TA_JOB_HARD_TIMEOUT，默认 7200s，0=禁用）；`_run_job` 重写为软/硬双门：软超时只写 `running + overtime=True` + 发 `job.overtime` 非终态事件，不 cancel 内层；硬超时 cancel 内层协程后标 failed + `mark_report_failed` + 发 `job.failed`（带 `hard_timeout_seconds`）；`JobStatusResponse` 增加 `overtime/overtime_at`；`_run_job_inner` 各终态写入统一清 `overtime`；`_save_report_or_raise`：报告持久化失败抛回失败边界（不再日志吞掉后伪装 completed）；`analyze/chat_completions` 建任务时初始化 overtime 字段 | 完整 |
| `api/job_store_redis.py` | `_serialize_value` 对 bool 做 JSON 编码（修复 `str(True)` 往返，保证 Redis 与内存 store 布尔语义一致） | 完整 |
| `api/services/report_service.py` | `create_report` 成功收口时清除陈旧 `error`（旧看门狗误标 failed 的报告不再残留失败态） | 完整 |

### 前端
| 文件 | 内容 | 状态 |
|---|---|---|
| `utils/jobLifecycle.ts`（新增） | 事件→运行态纯函数映射；`classifyRecoveredJobStatus` 仅对历史软超时文案（`任务超时（超过 N 秒）`）按 running 继续等待，硬超时/真实失败保持 failed；恢复轮询上限 2h | 完整 |
| `utils/jobLifecycle.test.ts`（新增） | 14 用例覆盖四态/legacy 边界/非超时错误不误判 | 半成品：缺 `vitest` 显式 import（本项目未启用 globals），collection 即失败 → round2 修复 |
| `hooks/useSSE.ts` | 订阅 `job.overtime`；生命周期统一由 `getJobLifecycleUpdate` 维护；流结束≠任务结束 | 完整 |
| `stores/analysisStore.ts` | `analysisOvertimeNotice` 状态；persist merge 保留 running 中的 `currentJobId` 供刷新恢复 | 完整 |
| `components/ChatCopilotPanel.tsx` | 刷新挂载恢复 + 断线回查同一 job（3s 间隔、可中止、2h 上限、404/记录失效不自动重提交）；`isAnalyzing`（含加班/回查）阻止重复提交；软超时琥珀色横幅；`job.ready/created` 均写入 `currentJobId` | 完整 |
| `types/index.ts` | `JobStatus.overtime/overtime_at`、`SSEEventType` 增加 `job.overtime` | 完整 |

### round1 缺失
- `tests/test_upstream081_timeout_recovery.py` 不存在（验收命令的主测试文件）。

## 二、round2 本次补齐

1. **新增 `tests/test_upstream081_timeout_recovery.py`（11 tests）**，逐条对照验收项：
   - `test_soft_timeout_keeps_job_running_then_completes_once`：软超时后 job 保持运行，最终只产生一次 `job.completed`、零 `job.failed`、终态是最后一个事件（防双写）；`mark_report_failed` 未被调用。
   - `test_soft_timeout_marks_job_running_with_overtime`：加班时刻 job 状态快照 = `running + overtime=True + error=None`，事件带 `soft_timeout_seconds/后台仍在继续/请勿重复提交`。
   - `test_hard_timeout_cancels_and_fails_closed`：内层协程确实被 cancel（不可能再写终态）；`failed + 硬性运行上限` 文案与旧软超时可区分；`mark_report_failed` 恰一次；`job.overtime` 先于硬超时出现。
   - `test_hard_timeout_equal_to_soft_skips_overtime_event` / `test_soft_timeout_disabled_hard_backstop_still_closes`：软=硬与软禁用（0）边界不发误导性加班事件，硬兜底独立 fail-closed。
   - `test_inner_failure_marks_failed_with_error`：内层在自身 try 之外抛错 → 外层标 failed（真实失败态可区分）。
   - `test_save_report_or_raise_never_fakes_completion`：持久化失败抛 RuntimeError，不伪装 completed。
   - `test_job_status_exposes_overtime_fields` / `test_legacy_failed_timeout_is_never_rewritten_to_running`：`get_job_status` 暴露 overtime 字段；历史 `failed(timeout)` 记录读路径逐字段只读、不被重写为 running。
   - `test_redis_bool_serialization_matches_memory_store`：序列化层 bool 往返与内存 store 一致（不依赖 Redis 连接，本机 Redis 未运行也生效）。
   - `test_create_report_clears_stale_failure_on_completion`：真实 DB（conftest 隔离 DATABASE_URL）验证完成收口清除陈旧 failed 痕迹。
2. **`tests/test_job_store_redis.py`** 增加 `test_bool_roundtrip_matches_memory_store`（Redis 可用时运行的 store 级一致性用例）。
3. **修复 `frontend/src/utils/jobLifecycle.test.ts`**：补 `import { describe, expect, it } from 'vitest'` 并改相对导入（round1 版本依赖未启用的 globals，无法运行）。

## 三、测试证据

```
pytest tests/test_upstream081_timeout_recovery.py -q --tb=short
  → 11 passed in 5.47s
pytest tests/test_api_smoke.py -q --tb=short
  → 87 passed in 24.40s
pytest tests/test_upstream081_runtime_resilience.py -q --tb=short   # 依赖任务回归
  → 49 passed in 5.80s
npx vitest run            (frontend)
  → Test Files 11 passed (11) / Tests 157 passed (157)
npm run build             (frontend, tsc + vite)
  → ✓ built in 1.56s（仅既有 chunk-size 警告，非错误）
```

## 四、遗留缺口

- `test_job_store_redis.py` 全模块依赖本机 Redis，当前环境 skip（与 round1 基线一致）；bool 一致性已由序列化层测试实际覆盖，Redis 起来后 store 级用例自动生效。
- 独立 Codex review 未跑（本轮按任务约束不 commit，修复候选等待 review）。
- SSE 重连订阅场景下（Redis pub/sub 无回放）错过的 `job.overtime` 事件靠轮询 `status.overtime` 补偿——已接线，属设计内行为，无代码缺口。

## 五、r1 review 修复（2026-09-08，1×P1 + 1×P2）

### P1：Redis SSE 订阅错过终态事件后不重放终态

- **修复位置**：`api/job_store_redis.py` `subscribe()` 超时分支（约 :173-191）+ 新共享纯函数 `api/job_store.py::terminal_replay_event`（:35）。内存 store `InMemoryJobStore.subscribe` 同步改为调用同一纯函数（行为等价重构，防两处漂移）。
- **语义**：Redis 订阅在轮询超时发现 `status ∈ {completed, failed}` 时，按内存 store 既有 payload 契约补发 `job.completed`（含 `job_id/decision/direction/result/risk_items/key_metrics/confidence/target_price/stop_loss_price`，顶层缺失回退 `result`，列表缺省 `[]`）或 `job.failed`（含 `job_id/error`，error 兜底 `"job failed"`），随后才终止生成器。对 job hash 只读（一次 `HGETALL`，与修复前 `get_job().get("status")` 的 I/O 相同），不向 Redis 写入任何数据，不额外 publish。非终态/快照缺失仍走 ping，事件顺序与去重语义不变。
- **事件序列对比**（`/v1/jobs/{job_id}/events`，api/main.py `_stream_job_events`，订阅端晚于终态接入）：
  - 修复前：`job.ready` → `ping`×N → 流结束（无终态事件、无 `done` marker）——SSE 消费方永远等不到终态。
  - 修复后：`job.ready` → `ping`×N → `job.completed`（或 `job.failed`）→ `done` marker，与内存 store 行为一致。
- **附带的防御性收敛**：`result` 为非 dict 的脏数据不再让 SSE 生成器中途 AttributeError，只丢弃回退字段（`direction/confidence/...` 为 None），`result` 原样透传。内存/Redis 两边经同一函数同时获得该保护；合法数据下 payload 与旧实现逐字节一致（测试断言）。

### P2：终态重放/恢复语义专项测试

- **取舍**：把「错过终态 → 补发哪个事件、payload 是什么」抽成不依赖活 Redis 的纯函数 `terminal_replay_event`（`api/job_store.py`，模块级、零 I/O），单测放 `tests/test_upstream081_timeout_recovery.py`（4 个新用例，本机 Redis 未运行也执行）：completed payload 全字段+回退规则、failed/非终态/脏 result 边界、内存 store 订阅补发与纯函数逐字段一致、Redis 序列化往返后决策与内存视图完全一致。未引入 fakeredis 等新依赖。
- **集成测试**（`tests/test_job_store_redis.py`，带既有模块级 skip 守卫）：改写 `test_subscribe_timeout_ping` 尾断言（旧断言「终态后静默结束」正是 bug 行为）；新增 `test_subscribe_replays_completed_terminal_when_live_event_missed`、`test_subscribe_replays_failed_terminal_when_live_event_missed`——不发布任何 pub/sub 事件，靠超时轮询验证补发终态恰一次、payload 从 Redis hash 还原、生成器在终态收口。本机 Redis 未运行（connection refused），如实 skip（12 skipped），Redis 可用后自动生效。
- **离线补充验证**：以 stub `get_job` 驱动修复后的 Redis 生成器控制流（监听线程失败被既有 except 吞掉、队列空 → 超时路径），确认 `ping → job.completed` 重放与立即 `job.failed` 重放，一次性 sanity，不入库。

### 修复后测试证据（2026-09-08）

```
pytest tests/test_upstream081_timeout_recovery.py -q --tb=short
  → 15 passed in 6.35s（11 原有 + 4 新增纯函数/一致性用例）
pytest tests/test_upstream081_timeout_recovery.py tests/test_api_smoke.py tests/test_upstream081_runtime_resilience.py -q --tb=short
  → 151 passed in 32.97s
pytest tests/test_job_store_redis.py -q --tb=short
  → 12 skipped in 0.07s（本机 Redis 未运行，如实 skip；含 3 个新终态重放用例）
```

- 前端本轮零改动，未重跑 vitest/build（round2 已验证）。
