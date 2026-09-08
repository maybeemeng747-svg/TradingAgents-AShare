# Task: UPSTREAM-081-002 micro-fix — 补入 r2 review P2-1 要求的离线测试

Read docs/reviews/UPSTREAM-081-002-20260907-r1.txt 与
docs/task_runs/UPSTREAM-081-002-20260907-190004/codex-review-r2-raw.txt（r2 结论）.

## 背景
r2 review 核验 P1 修复本体通过，唯一实质缺口（P2-1）：你在修复输出中声明跑过「stub get_job 离线驱动 Redis 生成器」的 sanity 用例，但**没有入库**——RedisJobStore.subscribe() 的生成器控制流（ping×N → 终态补发 → done）目前只能靠活 Redis 的 skip 守卫集成测试覆盖。

## 任务（只做这一件事）
把你已验证过的离线 sanity 用例产品化入库：
- 位置建议：tests/test_job_store_redis_offline.py（新文件）或并入 tests/test_upstream081_timeout_recovery.py，你选并说明
- 用 stub/monkeypatch 替身驱动 `RedisJobStore.subscribe()` 生成器：替身 `get_job`（按时间序返回 running→completed，及另一用例直接 failed）+ 替身 pubsub listen（返回空/有限事件），**不依赖活 Redis、不引入新第三方依赖**
- 断言事件序列：非终态时 `ping`，终态时补发 `job.completed`/`job.failed`（payload 与 `terminal_replay_event` 一致），随后 `done`
- 至少覆盖：①错过事件、终态已落库（补发路径）②立即就是终态（重放路径）

## 验证
- `pytest tests/test_job_store_redis_offline.py tests/test_upstream081_timeout_recovery.py -q --tb=short`
- `pytest tests/test_api_smoke.py -q --tb=short`（回归）
- 除新增测试文件外**不得改动任何其他文件**（job_store 源码、前端、docs 均不动；若发现确需改源码才能测，停下来在输出里说明而不是硬改）

## Output when done
- 测试文件路径与用例清单
- pytest 汇总行
