# 修复任务: M-003

之前的实现测试未通过。请修复以下问题：

## 测试输出（最后 30 行）
```
........................................................................ [ 66%]
........................................................................ [ 71%]
....................................................ss.................. [ 76%]
...............................ssss..................................F.. [ 80%]
........................................................................ [ 85%]
....................FF.................................................. [ 90%]
........................................................................ [ 95%]
........................................................................ [100%]
=================================== FAILURES ===================================
____________ TestBuildDiscoveryUniverse.test_industry_source_label _____________
tests/test_t002_discovery.py:73: in test_industry_source_label
    assert ind[0]["source"] == SOURCE_INDUSTRY
E   AssertionError: assert 'industry' == 'industry_pool'
E     
E     - industry_pool
E     ?         -----
E     + industry
_________ TestDiscoveryIntegration.test_fund_flow_symbols_in_universe __________
tests/test_t003_fund_flow_anomaly.py:661: in test_fund_flow_symbols_in_universe
    assert "600519.SH" in symbols
E   AssertionError: assert '600519.SH' in []
______ TestDiscoveryIntegration.test_fund_flow_symbols_dedup_with_manual _______
tests/test_t003_fund_flow_anomaly.py:676: in test_fund_flow_symbols_dedup_with_manual
    assert "000001.SZ" in symbols
E   AssertionError: assert '000001.SZ' in ['600519.SH']
=========================== short test summary info ============================
FAILED tests/test_t002_discovery.py::TestBuildDiscoveryUniverse::test_industry_source_label
FAILED tests/test_t003_fund_flow_anomaly.py::TestDiscoveryIntegration::test_fund_flow_symbols_in_universe
FAILED tests/test_t003_fund_flow_anomaly.py::TestDiscoveryIntegration::test_fund_flow_symbols_dedup_with_manual
3 failed, 1492 passed, 17 skipped in 52.18s
```

## 约束
- 不改 tradingagents/prompts/
- 不写入生产 tradingagents.db
- 不 push
- **不要 git commit**
- 修复后更新 docs/DEVLOG.md
