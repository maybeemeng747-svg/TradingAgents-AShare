# TradingAgents-AShare Z Code 开发规则

## 工作区与真源

本规则仅在工作区根目录为 `/Users/maybee/TradingAgents-AShare` 时适用。

任务状态唯一真源：`docs/TASKS.md`。

开始任何任务前必须按顺序执行：

1. `git status --short --branch`
2. 若存在来源不明的修改，立即停止并输出 `NEEDS_HUMAN`；不要读取任务、测试、暂存、提交或清理现场。
3. 工作区干净时，依次读取：
   - `AGENTS.md`
   - `docs/project-overview.md`
   - `docs/TASKS.md`
   - `docs/DECISIONS.md`
   - `docs/DEVLOG.md`
4. 运行只读任务检查：
   - `.venv/bin/python scripts/task_dependency_resolver.py dry-run --tasks-file docs/TASKS.md`

没有 `ready` 任务时只汇报 `NO_READY_TASKS`，不得修改代码、任务状态或生成新任务。

## 单任务执行

只处理任务卡明确允许的路径；任务卡未说明时采用最小修改范围。禁止顺手重构。

1. 记录 task id、任务标题、允许/禁止路径、依赖和验收命令。
2. 将本任务状态改为 `in_progress`，创建 `docs/task_runs/<TASK_ID>-<TIMESTAMP>/task.md`。
3. 使用第一性原理确认最小必要行为，再实现代码。
4. 运行任务指定测试、相关回归和 `git diff --check`。
5. 更新 `docs/DEVLOG.md` 和运行档案，记录真实命令、退出码、失败与未知项。
6. 运行独立审查：`codex review --uncommitted`。不得把 Z Code 自审替代 Codex review。
7. 发现 P0/P1/P2 correctness finding 时修复并重新测试、重新 review，最多两轮；仍不通过则停止为 `NEEDS_HUMAN`。
8. review UNKNOWN、超时、额度、认证或配置失败时，不提交、不标 done、不释放下游。
9. 仅当测试通过且 review 无 P0/P1/P2 correctness finding，才允许选择性暂存本任务文件、提交、标记 done，并运行依赖释放检查。

## 连续模式

用户明确要求“连续开发”或执行 `/tasks run` 后：

- 每次只完成一个任务的完整闭环，再重新检查工作区和任务池。
- 当前任务 PASS 后才进入下一项；FAIL/UNKNOWN/NEEDS_HUMAN 立即停止整批。
- 不设置固定任务数量；由用户给定时间预算或可领取队列决定。
- 不自动调用 live LLM、真实股票深度分析或收费数据接口。
- 不自动 push。

## 安全边界

- 不读取、打印或提交 `.env`、API token、cookie、凭据文件。
- 不修改或提交 `tradingagents.db*`、`tradeflow.db*`、`eval_results/`、`logs/`。
- 不修改 `tradingagents/prompts/`，除非任务卡和用户明确批准。
- 不用 fixture 冒充真实数据，不把失败、未查询、权限不足或字段缺失写成数值 0。
- 不覆盖用户已有修改，不使用 destructive git 命令。
