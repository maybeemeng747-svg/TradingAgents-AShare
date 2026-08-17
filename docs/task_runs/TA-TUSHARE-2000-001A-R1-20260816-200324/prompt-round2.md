# Fix task: TA-TUSHARE-2000-001A-R1

Codex review found correctness issues, please fix every finding:

## Final Codex Review
```
STATUS=FINDINGS
--- FINAL CODEX REVIEW ---
The patch improves the audit flow but still permits probing with an unintended inherited token and can miss serialized credential headers. It also leaves downstream exporters pinned to the superseded matrix.

Full review comments:

- [P1] Reject inherited tokens unless --no-dotenv is explicit — /Users/maybee/TradingAgents-AShare/scripts/audit_tushare_capability.py:100-101
  When an explicitly selected `.env` is missing or lacks `TUSHARE_TOKEN` while the process environment contains one, this branch proceeds with the inherited token even though `--no-dotenv` was not requested. A typo such as `--dotenv wrong.env` can therefore probe the wrong account, contrary to the task's `.env`-only credential contract; inherited fallback should be restricted to the explicit test-isolation path.

- [P1] Detect serialized credential headers before writing artifacts — /Users/maybee/TradingAgents-AShare/scripts/audit_tushare_capability.py:165-169
  If a probe exception contains serialized headers such as `{"Cookie": "short-secret"}`, `sanitize_error_text` leaves that value intact and this scan misses it because the quote appears between `cookie` and `:`. `write_outputs` then reports clean and writes the credential, defeating the new fail-closed guarantee; detect quoted/dict header forms as well as plain `Cookie:` and `Authorization:`.

- [P2] Promote the rerun matrix to downstream defaults — /Users/maybee/TradingAgents-AShare/docs/task_runs/TA-TUSHARE-2000-001A-R1-RERUN-20260816-202237/run-receipt.md:25-25
  For default evidence/governance exports, both exporter scripts still load `TA-TUSHARE-2000-001A-20260816-041709`, so this newly designated truth source is never consumed and generated packs continue recording the old, unauditable `matrix_ref`. Update those defaults or provide a stable pointer to this corrected matrix.
The patch improves the audit flow but still permits probing with an unintended inherited token and can miss serialized credential headers. It also leaves downstream exporters pinned to the superseded matrix.

Full review comments:

- [P1] Reject inherited tokens unless --no-dotenv is explicit — /Users/maybee/TradingAgents-AShare/scripts/audit_tushare_capability.py:100-101
  When an explicitly selected `.env` is missing or lacks `TUSHARE_TOKEN` while the process environment contains one, this branch proceeds with the inherited token even though `--no-dotenv` was not requested. A typo such as `--dotenv wrong.env` can therefore probe the wrong account, contrary to the task's `.env`-only credential contract; inherited fallback should be restricted to the explicit test-isolation path.

- [P1] Detect serialized credential headers before writing artifacts — /Users/maybee/TradingAgents-AShare/scripts/audit_tushare_capability.py:165-169
  If a probe exception contains serialized headers such as `{"Cookie": "short-secret"}`, `sanitize_error_text` leaves that value intact and this scan misses it because the quote appears between `cookie` and `:`. `write_outputs` then reports clean and writes the credential, defeating the new fail-closed guarantee; detect quoted/dict header forms as well as plain `Cookie:` and `Authorization:`.

- [P2] Promote the rerun matrix to downstream defaults — /Users/maybee/TradingAgents-AShare/docs/task_runs/TA-TUSHARE-2000-001A-R1-RERUN-20260816-202237/run-receipt.md:25-25
  For default evidence/governance exports, both exporter scripts still load `TA-TUSHARE-2000-001A-20260816-041709`, so this newly designated truth source is never consumed and generated packs continue recording the old, unauditable `matrix_ref`. Update those defaults or provide a stable pointer to this corrected matrix.
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
