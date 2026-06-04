# Fix task: DATA-P1-SOURCE-GAP-AUDIT

Previous implementation failed tests. Please fix:

## Test output (last 30 lines)
```
........................................................................ [ 73%]
.................................................ss..................... [ 75%]
............................ssss........................................ [ 77%]
........................................................................ [ 79%]
........................................................................ [ 81%]
........................................................................ [ 83%]
........................................................................ [ 85%]
........................................................................ [ 87%]
........................................................................ [ 89%]
........................................................................ [ 91%]
........................................................................ [ 93%]
........................................................................ [ 95%]
........................................................................ [ 96%]
........................................................................ [ 98%]
........................................                                 [100%]
=================================== FAILURES ===================================
____________ TestFundFlowFallback.test_individual_fund_flow_success ____________
tests/test_data_p0_603629.py:44: in test_individual_fund_flow_success
    assert "12345" in result
E   AssertionError: assert '12345' in '603629.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n日期 | 主力净流入 | 小单净流入 | 中单净流入 | 大单净流入 | 超大单净流入\n2026-05-30 | 1.23 | 0.54 | 0.32 | 0.88 | 0.36\n2026-05-29 | -0.80 | -0.20 | 0.15 | -0.60 | -0.35'
___________ TestAcceptanceH006.test_all_six_candidate_types_covered ____________
tests/test_h006_mandate_replay_eval.py:647: in test_all_six_candidate_types_covered
    assert ct.value in types_covered, f"Missing coverage for {ct.value}"
E   AssertionError: Missing coverage for UNCLASSIFIED_DATA_GAP
E   assert 'UNCLASSIFIED_DATA_GAP' in {'EVENT_WATCH', 'OVERHEATED_AVOID', 'POLICY_AMBUSH', 'POLICY_CONFIRM', 'PSEUDO_POLICY', 'TECH_TRADE'}
E    +  where 'UNCLASSIFIED_DATA_GAP' = <CandidateType.UNCLASSIFIED_DATA_GAP: 'UNCLASSIFIED_DATA_GAP'>.value
=========================== short test summary info ============================
FAILED tests/test_data_p0_603629.py::TestFundFlowFallback::test_individual_fund_flow_success
FAILED tests/test_h006_mandate_replay_eval.py::TestAcceptanceH006::test_all_six_candidate_types_covered
2 failed, 3693 passed, 17 skipped in 75.18s (0:01:15)
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
