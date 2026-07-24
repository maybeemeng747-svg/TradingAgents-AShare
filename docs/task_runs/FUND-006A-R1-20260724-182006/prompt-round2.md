# Fix task: FUND-006A-R1

Codex review found correctness issues, please fix every finding:

## Final Codex Review
```
STATUS=FINDINGS
--- FINAL CODEX REVIEW ---
The patch improves several hollow tests, but one of the targeted grouping tests still does not assert the period-scope behavior it claims to protect. This leaves a known regression class able to pass the suite.

Review comment:

- [P2] Assert period scope isolation in grouping test — /Users/maybee/TradingAgents-AShare/tests/test_fund006a_provider_replay.py:287-288
  For the cumulative/single-quarter regression this test is meant to cover, these assertions still pass if `extract_financial_anomaly_inputs` drops `period_scope` from its grouping key but keeps units separate: the margin remains non-null, and the added different-unit revenue-only fact does not change it. Please assert the expected margin/group from a specific `period_scope` or add a same-date/same-unit cross-scope contaminating fact so the test actually fails when FY_YTD and SINGLE_QUARTER rows are collapsed.
The patch improves several hollow tests, but one of the targeted grouping tests still does not assert the period-scope behavior it claims to protect. This leaves a known regression class able to pass the suite.

Review comment:

- [P2] Assert period scope isolation in grouping test — /Users/maybee/TradingAgents-AShare/tests/test_fund006a_provider_replay.py:287-288
  For the cumulative/single-quarter regression this test is meant to cover, these assertions still pass if `extract_financial_anomaly_inputs` drops `period_scope` from its grouping key but keeps units separate: the margin remains non-null, and the added different-unit revenue-only fact does not change it. Please assert the expected margin/group from a specific `period_scope` or add a same-date/same-unit cross-scope contaminating fact so the test actually fails when FY_YTD and SINGLE_QUARTER rows are collapsed.
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
