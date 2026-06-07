# Fix task: DATA-012

Previous OpenCode run failed (exit code 143). Please fix:

## OpenCode log (last 30 lines)
```
[0m
> build · glm-5.1
[0m
[0m→ [0mRead docs/TASKS.md
[0m→ [0mRead docs/DEVLOG.md
[0m→ [0mRead docs/project-overview.md
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
