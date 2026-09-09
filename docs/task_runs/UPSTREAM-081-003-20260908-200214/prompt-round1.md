# Task: UPSTREAM-081-003: AKShare v0.8.1 差异审计与最小修复（P1）

Read task "UPSTREAM-081-003" from docs/TASKS.md and implement as described.

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
