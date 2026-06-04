# Fix task: PERF-004

Previous implementation failed tests. Please fix:

## Test output (last 30 lines)
```
ERROR: file or directory not found: tests/test_scheduler*.py


no tests ran in 0.00s
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
