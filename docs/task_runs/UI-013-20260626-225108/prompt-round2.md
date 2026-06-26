# Fix task: UI-013

Previous implementation failed tests. Please fix:

## Test output (last 30 lines)
```
E    +  where 0 = ObserveRunResult(checked=0, triggered=0, invalidated=0, waiting=0, skipped=0, signals_written=0, errors=[], skipped_reason='2026-06-27 非交易日，跳过实时观察', run_time='2026-06-27T00:16:01.617818', details=[]).invalidated
_________ TestObserveToPaperE2E.test_waiting_candidate_stays_tracking __________
tests/test_tf_obs_003_observe_paper_sync.py:193: in test_waiting_candidate_stays_tracking
    assert result.waiting == 1
E   AssertionError: assert 0 == 1
E    +  where 0 = ObserveRunResult(checked=0, triggered=0, invalidated=0, waiting=0, skipped=0, signals_written=0, errors=[], skipped_reason='2026-06-27 非交易日，跳过实时观察', run_time='2026-06-27T00:16:01.649851', details=[]).waiting
____ TestObserveToPaperE2E.test_non_paper_candidate_no_record_after_observe ____
tests/test_tf_obs_003_observe_paper_sync.py:206: in test_non_paper_candidate_no_record_after_observe
    assert result.triggered == 1
E   AssertionError: assert 0 == 1
E    +  where 0 = ObserveRunResult(checked=0, triggered=0, invalidated=0, waiting=0, skipped=0, signals_written=0, errors=[], skipped_reason='2026-06-27 非交易日，跳过实时观察', run_time='2026-06-27T00:16:01.680138', details=[]).triggered
_____________ TestObserveToPaperE2E.test_mixed_paper_and_non_paper _____________
tests/test_tf_obs_003_observe_paper_sync.py:222: in test_mixed_paper_and_non_paper
    assert result.triggered == 2
E   AssertionError: assert 0 == 2
E    +  where 0 = ObserveRunResult(checked=0, triggered=0, invalidated=0, waiting=0, skipped=0, signals_written=0, errors=[], skipped_reason='2026-06-27 非交易日，跳过实时观察', run_time='2026-06-27T00:16:01.712398', details=[]).triggered
=============================== warnings summary ===============================
tests/test_hk001_market_boundary.py::TestHKDataCollectorGating::test_cn_does_not_set_hk_light_mode
tests/test_hk001_market_boundary.py::TestHKDataCollectorGating::test_cn_still_attempts_ashare_fields
  /Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/akshare/stock_fundamental/stock_recommend.py:91: FutureWarning: Passing literal html to 'read_html' is deprecated and will be removed in a future version. To read from a literal string, wrap it in a 'StringIO' object.
    temp_df = pd.read_html(r.text, header=0)[0].iloc[:, :8]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/test_tf_obs_003_observe_paper_sync.py::TestObserveToPaperE2E::test_triggered_candidate_becomes_pending
FAILED tests/test_tf_obs_003_observe_paper_sync.py::TestObserveToPaperE2E::test_invalidated_candidate_becomes_invalidated
FAILED tests/test_tf_obs_003_observe_paper_sync.py::TestObserveToPaperE2E::test_waiting_candidate_stays_tracking
FAILED tests/test_tf_obs_003_observe_paper_sync.py::TestObserveToPaperE2E::test_non_paper_candidate_no_record_after_observe
FAILED tests/test_tf_obs_003_observe_paper_sync.py::TestObserveToPaperE2E::test_mixed_paper_and_non_paper
5 failed, 6999 passed, 17 skipped, 2 warnings in 232.01s (0:03:52)
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
