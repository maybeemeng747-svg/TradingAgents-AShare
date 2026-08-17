# Task: TA-TUSHARE-2000-001A-R1: 权限矩阵补修与真实重跑（P0）

Read task "TA-TUSHARE-2000-001A-R1" from docs/TASKS.md and implement as described.

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No full-market scan or stock deep TA
- No push / PR / merge
- **Do NOT git commit** -- outer script handles commits
- Do not update task status or release downstream tasks in docs/TASKS.md;
  the outer script owns all task state transitions after review
- Update docs/DEVLOG.md after changes

## Output when done
- Which files were changed
- Key logic summary
- Test results (passed/failed count)
