# Fix task: H-002

Previous implementation failed tests. Please fix:

## Test output (last 30 lines)
```
ss...................................................................... [ 74%]
...............................F........................................ [ 77%]
........................................................................ [ 80%]
........................................................................ [ 83%]
........................................................................ [ 86%]
........................................................................ [ 89%]
........................................................................ [ 93%]
........................................................................ [ 96%]
........................................................................ [ 99%]
..................                                                       [100%]
=================================== FAILURES ===================================
___________ test_runner_contains_claim_lock_and_safe_exit_expansion ____________
tests/test_auto_dev_loop_static.py:79: in test_runner_contains_claim_lock_and_safe_exit_expansion
    assert "update_task_status \"in_progress — claimed $RUN_ID\"" in script_text
E   assert 'update_task_status "in_progress — claimed $RUN_ID"' in '#!/usr/bin/env bash\n# AUTO-002: Auto dev loop v1.3 reliability patch\n# Usage: ./scripts/auto_dev_loop.sh [--dry-run...LED_TASKS))"\necho "========================================"\n\nexit $([ $FAILED_TASKS -eq 0 ] && echo 0 || echo 1)\n'
____________________ TestRunHealthCheck.test_mixed_statuses ____________________
tests/test_m008_health_check.py:254: in test_mixed_statuses
    assert report.summary[STATUS_OK] == 6
E   assert 8 == 6
___________ TestRunDiscoveryWithEvents.test_event_source_integration ___________
tests/test_t002_discovery.py:376: in test_event_source_integration
    assert len(result.candidates) >= 1
E   AssertionError: assert 0 >= 1
E    +  where 0 = len([])
E    +    where [] = DiscoveryResult(trade_date='2026-05-29', universe_size=1, candidates=[], filtered=[{'symbol': '002138.SZ', 'name': '顺络...={'无策略命中': 1}, by_strategy={}, by_priority_rank={}, by_fp_type={}, common_evidence_gaps=[], common_risk_demotions=[]))).candidates
=========================== short test summary info ============================
FAILED tests/test_auto_dev_loop_static.py::test_runner_contains_claim_lock_and_safe_exit_expansion
FAILED tests/test_m008_health_check.py::TestRunHealthCheck::test_mixed_statuses
FAILED tests/test_t002_discovery.py::TestRunDiscoveryWithEvents::test_event_source_integration
3 failed, 2302 passed, 17 skipped in 75.03s (0:01:15)
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
