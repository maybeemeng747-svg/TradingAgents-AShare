你是独立代码审查者。任务：UPSTREAM-081-002 — 长时分析软/硬超时与断线恢复。仓库：/Users/maybee/TradingAgents-AShare。只读审查。

## 审查对象
工作区未提交候选（运行记录 docs/task_runs/UPSTREAM-081-002-20260907-190004/，含 implementation-round2.md 逐文件盘点表）：
- 修改：api/job_store_redis.py、api/main.py、api/services/report_service.py、frontend/src/hooks/useSSE.ts、stores/analysisStore.ts、components/ChatCopilotPanel.tsx、types/index.ts、docs/DEVLOG.md
- 新增：frontend/src/utils/jobLifecycle.ts + .test.ts、tests/test_upstream081_timeout_recovery.py

**范围豁免**：docs/TASKS.md 状态行变更是主控/流程簿记，不在审查范围，不要因此判 FAIL。

## 任务要求（上游 3e32c89 #204 本地化）
软超时=「继续后台运行」非终态事件 + 独立硬超时 fail-closed；前端断线/刷新可恢复同一 job，禁止重复提交昂贵分析；软超时、硬超时、真实失败、完成**四态可区分**；复用本地 `_job_timeout_for_config`、持久化报告、SSE、job store；不得把历史 `failed(timeout)` 无条件重写为 running。

## 必须逐项核验（引用文件:行）
1. 软超时后 job 仍运行，且最终**只产生一次** completed/failed（找双写/重复终态路径，含 Redis 与内存 store 两实现）
2. 硬超时 fail-closed；历史 failed(timeout) 不被重写为 running；`_job_timeout_for_config` 是否被复用而非绕过
3. 刷新/断线恢复：恢复路径不会重复创建 job；幂等性证据（测试或代码路径）
4. Redis 与内存 store 语义一致（超时/恢复/终态一次性行为对齐；注意 Redis 不可用时 skip 的测试留下的语义盲区）
5. 前端：SSE 断线重连后状态恢复正确；`job.overtime` 错过时靠轮询 `status.overtime` 补偿的接线是否真的闭环；「后台继续」展示而非失败；jobLifecycle 工具与 store 接线一致
6. 四态可区分：软超时/硬超时/真实失败/完成在 API 响应、job store、前端展示三层的区分字段是否贯通
7. 边界与约束：未动 prompts/、生产 DB、scheduler 时间、强动作门禁、.env.example；api/main.py 存量端点兼容；前端 `npm run build` 兼容
8. 新测试质量：test_upstream081_timeout_recovery.py 11 项是否真实断言上述语义（抽 3-4 个看断言），jobLifecycle.test.ts 同理

## 已知设计内行为（不算 finding）
- Redis 未运行时 test_job_store_redis.py 整模块 skip（与基线一致）
- SSE 重连错过 `job.overtime` 时靠轮询 `status.overtime` 补偿

## 允许操作
只读 + 可运行测试（.venv/bin/python -m pytest ...；沙箱临时目录受限时用 -s 并说明）。不得修改文件、不得 commit。

## 输出（中文，stdout）
- 第一行：`结论: PASS` 或 `结论: FAIL`
- 按 1-8 逐条核验（引用文件:行）
- 新发现 findings（P0/P1/P2，引用文件:行；没有写"无"）
- 实际运行的命令与结果
