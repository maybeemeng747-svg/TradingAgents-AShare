# Z Code 任务回执

Z Code 直接执行项目任务时，将候选回执写入本目录。正式运行档案仍保存在
`docs/task_runs/<TASK_ID>-<TIMESTAMP>/`，`docs/TASKS.md` 仍是唯一状态真源。

每份回执至少记录 task id、状态、修改文件、测试命令与真实结果、Codex review
结果、遗留风险和 commit。回执不能替代测试、review 或任务状态。
