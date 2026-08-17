# B-002-R2 Implementation Result

- Task: B-002-R2 - OpenClaw 回调 ORM 字段与 readiness 真源修复（P1）
- Implemented at: 2026-08-17
- Status: IMPLEMENTED (awaiting Codex review / outer-loop state transition)

## Files changed

- `scheduler/main.py` — `_load_report_payload` only reads real `ReportDB`
  columns; removed `horizon` / `analysis_summary` / `opinion` reads
  (AttributeError source) and the always-None `readiness_score` getattr.
- `api/services/openclaw_callback_service.py` — readiness now parsed from the
  persisted `[C-008]` block in `final_trade_decision` (structured dict kept as
  forward-compatible priority); `readiness_score` always present in payload
  with explicit `available` / `not_available` status; schema version 1.1.0.
  [R2 fix] Parsing is restricted to the trusted system tail via persisted
  `metadata.system_diagnostics_offset`; a model-authored `[C-008]` mention can
  no longer be published as `available`.
- `tests/test_b002_openclaw_callback.py` — updated contract assertions, added
  `TestReadinessScoreSources` (10 tests), `TestSchedulerCallbackOrmAlignment`
  (3 tests: schema-key alignment vs `sqlalchemy.inspect(ReportDB).columns`,
  enabled-callback full-payload integration, missing-report-row path),
  `TestReadinessTrustedOffset` (12 tests for the trusted-tail / offset
  semantics incl. explicit-null fail-closed), and
  `TestReadinessAvailableValidation` (6 tests: available requires a fully
  valid completeness + confidence).
- `docs/DEVLOG.md` — entry added.

## Key logic

- P1: `ReportDB` has no `horizon/analysis_summary/opinion/readiness_score`
  columns (verified via AttributeError probe on a real instance). The loader
  now maps only real columns with direct attribute access; `horizon` continues
  to flow from the scheduled task argument.
- P2: real persisted readiness source = `[C-008] 执行就绪度评分` text block
  appended by risk_manager into `final_trade_decision` (ReportDB column and
  result_data key). [R2 fix] The parser first reads the persisted
  `result_data.metadata.system_diagnostics_offset` (the same trusted field
  report_service and the frontend already consume) and restricts parsing to
  that system-appended tail: a `[C-008]` mention in the model-authored body —
  even one carrying completeness/confidence values — is never published. An
  offset that is present but invalid (explicit null / non-int / out of
  range) yields `not_available`. Legacy reports without the metadata fall
  back to the LAST `[C-008]` heading (the system block is appended after
  model text); a malformed last block does not leak earlier model-authored
  values. Completeness outside 0-100 is dropped; text without the marker is
  not trusted.
  [R2-round2/3 fixes] (a) an explicit `system_diagnostics_offset: null` is
  present-but-invalid and fails closed (no legacy fallback), distinguished
  from a truly absent key via an `_OFFSET_ABSENT` sentinel; (b) `available`
  is only assigned when BOTH `data_completeness` (int 0-100) and
  `confidence` (高/中/低) validate — truncated / out-of-range / wrongly-typed
  candidates (text or structured snapshot) yield `not_available`; (c) the
  parser only accepts the deterministic system-block structure
  (`[C-008] 执行就绪度评分` heading + bullet field lines), so a bare inline
  `[C-008]` prose mention with valid-looking values is never published.
- Contract: `payload["readiness_score"]` is always present —
  `{"status": "available", "data_completeness": int, "confidence": "高|中|低",
  "source": "readiness_score"|"final_trade_decision"}` or
  `{"status": "not_available"}`. No empty-value or partial faking.

## Test evidence

- `tests/test_b002_openclaw_callback.py`: 73 passed (63 after round2; +2
  explicit-null fail-closed / absent-key tests, +1 out-of-range
  not_available rewrite, +6 `TestReadinessAvailableValidation`, +2 inline
  prose rejection) — see `tests-b002-callback-round3.txt`.
  (Round2's 63-test output: `tests-b002-callback-round2.txt`; round1's
  53-test run output was not preserved — `tests-round1.txt` only contains
  the API smoke / runtime-tier command below.)
- Scheduler-related regression (`test_scheduled_queue.py`,
  `test_data026_db_hygiene.py`, `test_perf004_full_ta_cost_gate.py`,
  `test_portfolio_import.py`, `test_watchlist_scheduled.py`): 114 passed —
  see `tests-scheduler-regression-round3.txt`.
- `pytest tests/test_api_smoke.py tests/test_runtime_tier_contract.py`:
  157 passed — see `tests-round3.txt`.
- `py_compile` on all changed Python files and `git diff --check`: clean.

## Constraints honored

- No writes to prod `tradingagents.db` (tests use isolated temp/in-memory DB
  via conftest + StaticPool).
- No changes to `tradingagents/prompts/`, OpenClaw-side config, or non-callback
  paths. No live LLM calls. No commit/push (outer script owns commits).
