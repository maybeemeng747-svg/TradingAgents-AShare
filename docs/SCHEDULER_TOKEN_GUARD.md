# 定时任务 Token 护栏

> 目标：避免“保存持仓/启动后台进程后，系统在用户没意识到的情况下自动消耗模型 token”。

## 当前运行规则

- 定时分析只会在独立进程 `python -m scheduler.main` 运行时触发。
- 每条任务必须满足：`is_active=1`、当天未运行、当前时间已到 `trigger_time`、当天是 A 股交易日。
- `@test.com` 测试用户会被过滤，不参与自动定时任务。
- 持仓导入默认只保存持仓，不再自动创建定时任务；需要自动创建时必须显式传 `auto_apply_scheduled=true`。

## 环境变量

| 变量 | 默认 | 作用 |
|------|------|------|
| `SCHEDULER_ENABLED` | `1` | 总开关。设为 `0` 后 scheduler 进程仍可启动，但不会领取任务。 |
| `SCHEDULER_DRY_RUN` | `0` | 干跑审计。设为 `1` 后只打印“将会运行哪些任务”，不发起分析、不写 `last_run_date`。 |
| `SCHEDULER_RUN_INTRADAY` | `1` | 是否允许盘中自动运行，例如 `14:30`。默认开启，尊重用户主动创建的定时任务。 |
| `SCHEDULER_MAX_TASKS_PER_TICK` | `1` | 每分钟最多领取几个任务，避免同一分钟批量消耗。 |
| `SCHEDULER_ALLOWED_TRIGGER_TIMES` | 空 | 触发时间 allowlist，例如 `20:00,22:00`。留空表示不限制。 |
| `SCHEDULER_CONCURRENCY` | `1` | 并发执行上限。 |

## 快速审计

查看当前活跃定时任务：

```bash
sqlite3 tradingagents.db "SELECT symbol,horizon,trigger_time,is_active,last_run_date,last_run_status FROM scheduled_analyses ORDER BY is_active DESC, trigger_time, symbol;"
```

查看 scheduler 进程是否在跑：

```bash
ps aux | grep -E "python -m scheduler.main|scheduler.main" | grep -v grep
```

临时安全模式启动：

```bash
SCHEDULER_DRY_RUN=1 python -m scheduler.main
```

只允许晚上复盘自动跑：

```bash
SCHEDULER_RUN_INTRADAY=0 SCHEDULER_ALLOWED_TRIGGER_TIMES=20:00,22:00 python -m scheduler.main
```

完全停用自动执行：

```bash
SCHEDULER_ENABLED=0 python -m scheduler.main
```
