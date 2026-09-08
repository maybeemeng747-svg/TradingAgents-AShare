你是独立代码审查者（round2）。任务：UPSTREAM-081-002 修复轮 — 修 r1 review 的 1×P1 + 1×P2。仓库：/Users/maybee/TradingAgents-AShare。只读审查。

## 背景
r1 review（docs/reviews/UPSTREAM-081-002-20260907-r1.txt）结论 FAIL：
- [P1] api/job_store_redis.py:169 — subscribe 发现 completed/failed 后直接 break 不补发终态事件；内存 store（api/job_store.py:260-264 旧内联逻辑）会补发 → 两 store 语义不一致，Redis 路径 SSE 订阅端收不到终态。
- [P2] Redis 订阅终态重放语义无专项测试（模块级 skip 盲区）。

修复声明：
- 新纯函数 `terminal_replay_event(job_id, job)`（api/job_store.py:35）：从内存 store 内联逻辑抽出，零 I/O，两 store 共用同一决策；内存 subscribe 改为调用它（声明为行为等价重构）。
- api/job_store_redis.py:173-191：超时分支发现终态时按同一纯函数补发 `job.completed/job.failed` 后才 break；job hash 只读一次 HGETALL，零额外写入。
- 测试：新增 4 离线用例（payload 全字段/回退规则/failed 兜底/脏 result 不崩溃/内存订阅与纯函数一致/Redis 序列化往返决策一致）；改写 test_subscribe_timeout_ping 旧尾断言（旧断言在断言 bug 行为）；新增 2 条 Redis 集成用例（skip 守卫）+ stub get_job 离线驱动 Redis 生成器的 sanity 用例。

## 必须逐项核验（引用文件:行）
1. 纯函数抽取是否真为行为等价重构：内存 store 补发语义（事件名、payload 字段、回退规则）修复前后一致；两 store 决策确实共用同一函数（不是复制两份）。
2. Redis 修复正确性：超时分支终态补发的 payload 与内存版逐字段一致；事件顺序（ready→ping×N→终态→done）不回归；HGETALL 只读一次；非终态 ping 行为不变；无对 Redis 的额外写入。
3. 旧测试改写是否合理：旧断言确实在断言 bug 行为（对照 r1 review 引用）；新断言与修复语义一致。
4. P2 测试质量：4 个离线新用例断言真实（抽 2-3 个看）；stub get_job 驱动 Redis 生成器的 sanity 用例是否真的离线可跑（不依赖活 Redis）；Redis 集成用例 skip 守卫正确。
5. 无夹带：除 P1/P2 修复+测试+文档外无其他改动；未动 prompts/、生产 DB、scheduler、门禁、.env.example；api/main.py 本轮不应有新改动（r1 已审过的部分不在本轮范围，但若本轮 diff 触碰需指出）。
6. 范围豁免：docs/TASKS.md 状态行变更为流程簿记，不审。

## 已知事实（不必重复验证）
- 主控本地已实测：151 passed + 12 skipped（Redis 未运行），含新增用例。

## 允许操作
只读；可运行 pytest（临时目录受限用 -s 并说明）。不得修改文件、不得 commit。

## 输出（中文，stdout）
- 第一行：`结论: PASS` 或 `结论: FAIL`
- 逐项核验（引用文件:行）
- 新 findings（P0/P1/P2；没有写"无"）
- 实际运行的命令与结果
