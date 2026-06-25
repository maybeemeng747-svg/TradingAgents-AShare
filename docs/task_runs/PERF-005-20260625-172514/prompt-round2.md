# Fix task: PERF-005

Previous implementation failed tests. Please fix:

## Test output (last 30 lines)
```
........................................................................ [ 85%]
........................................................................ [ 86%]
........................................................................ [ 87%]
........................................................................ [ 88%]
........................................................................ [ 90%]
........................................................................ [ 91%]
........................................................................ [ 92%]
........................................................................ [ 93%]
........................................................................ [ 94%]
........................................................................ [ 95%]
........................................................................ [ 96%]
........................................................................ [ 97%]
........................................................................ [ 98%]
........................................................................ [ 99%]
..............                                                           [100%]
=================================== FAILURES ===================================
______________________ TestSampleSingle.test_fund_flow_ok ______________________
tests/test_data019_live_source_sampling.py:323: in test_fund_flow_ok
    assert result.status in (SourceFreshnessStatus.HAS_DATA, SourceFreshnessStatus.UNIT_UNVERIFIED)
E   AssertionError: assert 'STALE' in ('HAS_DATA', 'UNIT_UNVERIFIED')
E    +  where 'STALE' = SampleResult(data_type='fund_flow', data_type_label='个股资金流', symbol='600519.SH', symbol_name='贵州茅台', category='large_c...gnosis='个股资金流数据过期（as_of=2026-06-17）（注意：主力资金一般应可取；失败需 fallback 到 cn_astock/Eastmoney push2his）', rate_limit_risk='high').status
_____ TestRunLiveSamplingMocked.test_akshare_fail_astock_fallback_scenario _____
tests/test_data019_live_source_sampling.py:491: in test_akshare_fail_astock_fallback_scenario
    assert fund_flow_result.status in (
E   AssertionError: assert 'STALE' in ('HAS_DATA', 'UNIT_UNVERIFIED')
E    +  where 'STALE' = SampleResult(data_type='fund_flow', data_type_label='个股资金流', symbol='603629.SH', symbol_name='苏利股份', category='recent_...gnosis='个股资金流数据过期（as_of=2026-06-17）（注意：主力资金一般应可取；失败需 fallback 到 cn_astock/Eastmoney push2his）', rate_limit_risk='high').status
=========================== short test summary info ============================
FAILED tests/test_data019_live_source_sampling.py::TestSampleSingle::test_fund_flow_ok
FAILED tests/test_data019_live_source_sampling.py::TestRunLiveSamplingMocked::test_akshare_fail_astock_fallback_scenario
2 failed, 6619 passed, 17 skipped in 213.85s (0:03:33)
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
