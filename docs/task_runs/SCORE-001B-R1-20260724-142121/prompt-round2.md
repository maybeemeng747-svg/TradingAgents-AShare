# Fix task: SCORE-001B-R1

Codex review found correctness issues, please fix every finding:

## Final Codex Review
```
STATUS=FINDINGS
--- FINAL CODEX REVIEW ---
The patch still permits future research snapshots on normal TradeFlow historical candidate details and leaves some emitted snapshot text fields unsanitized. These directly undermine the SCORE-001B-R1 fixes for historical timing and action-word leakage.

Full review comments:

- [P1] Read TradeFlow rows' actual dates before falling back — /Users/maybee/TradingAgents-AShare/api/services/tradeflow_service.py:919-920
  For candidate details loaded from `tradeflow_candidates`, `_row_to_candidate_item()` exposes `trade_date` / `effective_trade_date` / `plan_date`, not `date` or `discovered_at`, so this resolver falls through to `datetime.now()` for normal historical rows. In `/tradeflow` detail requests for an older trade date, the query can still select a snapshot whose `as_of` is after the candidate date, reintroducing the future-data leak this patch is meant to close.

- [P1] Sanitize every emitted snapshot text field — /Users/maybee/TradingAgents-AShare/tradingagents/dataflows/research_score_snapshot.py:1252-1252
  If ZCode sends a strong action phrase in a score-change reason or thesis topic, this serializer still returns it unchanged: `score_change_summary.reasons` is built before the new cleaning block and `topic` is copied directly. Candidate detail/report consumers can therefore still see phrases like `建议买入` even though the new contract says snapshot text must not leak action words; apply the stripping to all free-text fields emitted here.
The patch still permits future research snapshots on normal TradeFlow historical candidate details and leaves some emitted snapshot text fields unsanitized. These directly undermine the SCORE-001B-R1 fixes for historical timing and action-word leakage.

Full review comments:

- [P1] Read TradeFlow rows' actual dates before falling back — /Users/maybee/TradingAgents-AShare/api/services/tradeflow_service.py:919-920
  For candidate details loaded from `tradeflow_candidates`, `_row_to_candidate_item()` exposes `trade_date` / `effective_trade_date` / `plan_date`, not `date` or `discovered_at`, so this resolver falls through to `datetime.now()` for normal historical rows. In `/tradeflow` detail requests for an older trade date, the query can still select a snapshot whose `as_of` is after the candidate date, reintroducing the future-data leak this patch is meant to close.

- [P1] Sanitize every emitted snapshot text field — /Users/maybee/TradingAgents-AShare/tradingagents/dataflows/research_score_snapshot.py:1252-1252
  If ZCode sends a strong action phrase in a score-change reason or thesis topic, this serializer still returns it unchanged: `score_change_summary.reasons` is built before the new cleaning block and `topic` is copied directly. Candidate detail/report consumers can therefore still see phrases like `建议买入` even though the new contract says snapshot text must not leak action words; apply the stripping to all free-text fields emitted here.
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
