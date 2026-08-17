# Fix task: B-002-R2

Codex review found correctness issues, please fix every finding:

## Final Codex Review
```
STATUS=FINDINGS
--- FINAL CODEX REVIEW ---
The ORM mapping fix works and the focused tests pass, but readiness parsing can still publish model-authored values instead of the authoritative system block. The run archive also references the wrong test artifact.

Full review comments:

- [P2] Parse the authoritative C-008 block, not the first mention — /Users/maybee/TradingAgents-AShare/api/services/openclaw_callback_service.py:93-96
  When the model-generated portion of `final_trade_decision` mentions `[C-008]` before the system-appended block, these searches return the model's completeness and confidence rather than the authoritative values appended later. Since production already persists `metadata.system_diagnostics_offset`, restrict parsing to that trusted tail—or at least select the last complete C-008 heading—so fabricated readiness is not published as `available`.

- [P3] Point the archive at the callback test run it claims — /Users/maybee/TradingAgents-AShare/docs/task_runs/B-002-R2-20260817-200225/implementation.md:39-40
  Once this run archive is checked in, reviewers following this reference will not find the claimed 53 callback tests: `tests-round1.txt` contains only the API smoke/runtime-tier command and its 157 passes. Preserve the callback test output in a separate artifact or correct this reference so the recorded B-002-R2 evidence is auditable.
The ORM mapping fix works and the focused tests pass, but readiness parsing can still publish model-authored values instead of the authoritative system block. The run archive also references the wrong test artifact.

Full review comments:

- [P2] Parse the authoritative C-008 block, not the first mention — /Users/maybee/TradingAgents-AShare/api/services/openclaw_callback_service.py:93-96
  When the model-generated portion of `final_trade_decision` mentions `[C-008]` before the system-appended block, these searches return the model's completeness and confidence rather than the authoritative values appended later. Since production already persists `metadata.system_diagnostics_offset`, restrict parsing to that trusted tail—or at least select the last complete C-008 heading—so fabricated readiness is not published as `available`.

- [P3] Point the archive at the callback test run it claims — /Users/maybee/TradingAgents-AShare/docs/task_runs/B-002-R2-20260817-200225/implementation.md:39-40
  Once this run archive is checked in, reviewers following this reference will not find the claimed 53 callback tests: `tests-round1.txt` contains only the API smoke/runtime-tier command and its 157 passes. Preserve the callback test output in a separate artifact or correct this reference so the recorded B-002-R2 evidence is auditable.
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
