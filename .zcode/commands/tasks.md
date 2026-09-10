---
description: 查看或连续执行 TradingAgents-AShare 的 ready 任务
argument-hint: 留空查看；run 连续开发；TASK-ID 执行指定 ready 任务
---

# /tasks - TradingAgents-AShare 任务入口

先确认当前工作区根目录是 `/Users/maybee/TradingAgents-AShare`，并读取：

```text
.zcode/rules/tradingagents-development.md
AGENTS.md
docs/project-overview.md
docs/TASKS.md
docs/DECISIONS.md
docs/DEVLOG.md
```

必须先运行：

```bash
git status --short --branch
```

若工作区存在来源不明的修改，输出 `NEEDS_HUMAN` 并停止。工作区干净时再运行：

```bash
.venv/bin/python scripts/task_dependency_resolver.py dry-run --tasks-file docs/TASKS.md
```

## 无参数

- 只读汇总总任务、done、ready、blocked-auto、blocked-human 和 unknown 数量。
- 列出实际可领取任务及依赖。
- 没有 ready 时输出 `NO_READY_TASKS`，不得写代码或自行释放任务。

## 参数为 TASK-ID

- 仅当该任务当前为 `ready` 且依赖满足时执行。
- 严格按项目规则完成实现、测试、运行档案和独立 Codex review。
- 非 ready、依赖不满足或存在状态冲突时停止，不擅自改状态。

## 参数为 run

- 进入连续模式，按 P0 到 P3、同优先级按文档顺序领取。
- 一次只处理一个任务并完成测试与 Codex review 闭环。
- PASS 后选择性提交并重新读取任务池；失败、UNKNOWN、超时、额度或配置异常立即停止。
- 不自动 push，不调用 live LLM，不写生产数据库，不修改知识库或其他项目。

用户参数：
$ARGUMENTS
