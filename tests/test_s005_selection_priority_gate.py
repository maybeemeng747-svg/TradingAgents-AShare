# [S-005] selection_priority_gate — tests
"""Tests for the selection priority gate module.

Covers:
- Composite score computation: policy + narrative + tech + fund + risk + completeness
- Signal category detection: policy / narrative / tech / fund
- need_deep_ta gate: requires >= 2 positive categories, no major risks
- Priority rank: A / B / C tiers
- Data completeness: 8 fields, missing_evidence output
- why_deep_ta / why_not_deep_ta explanations
- Evidence refs trace back to input signals
- No strong buy/sell words in output
- Integration with Candidate / evaluate_symbol / plan_runner / discovery
- DB persistence round-trip for S-005 fields
- Sorting by composite_score in plans
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from tradingagents.tradeflow.selection_priority_gate import (
    run_selection_priority_gate,
    SelectionPriorityResult,
    _compute_data_completeness,
    _sanitize,
    MIN_POSITIVE_CATEGORIES_FOR_DEEP_TA,
    MAX_COMPOSITE_SCORE,
)
from tradingagents.tradeflow.schemas import (
    Candidate,
    CandidateSignal,
    DailyPlan,
    STRATEGY_VCP,
    STRATEGY_PULLBACK,
    STRATEGY_EVENT,
    STRATEGY_POLICY_VERSION,
    STRATEGY_NARRATIVE,
    STRATEGY_FUND_FLOW,
    FORBIDDEN_WORDS,
)
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate


# ═══════════════════════════════════════════════════════════════
# Unit tests: run_selection_priority_gate()
# ═══════════════════════════════════════════════════════════════

class TestCompositeScore:
    def test_empty_inputs_near_zero(self):
        r = run_selection_priority_gate(has_price_data=False)
        assert r.composite_score == 0.0

    def test_tech_only(self):
        r = run_selection_priority_gate(score=45.0, strategy_tags=["VCP"])
        assert r.composite_score >= 45.0

    def test_policy_only(self):
        r = run_selection_priority_gate(version_score=20.0, policy_tags=["低空经济"])
        assert r.composite_score >= 20.0

    def test_narrative_only(self):
        r = run_selection_priority_gate(narrative_score=25.0)
        assert r.composite_score >= 25.0

    def test_fund_flow_only(self):
        r = run_selection_priority_gate(
            fund_flow_anomaly_score=15.0,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
        )
        assert r.composite_score >= 15.0

    def test_all_positive_signals_summed(self):
        r = run_selection_priority_gate(
            score=40.0,
            version_score=20.0,
            narrative_score=25.0,
            fund_flow_anomaly_score=15.0,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            strategy_tags=["VCP"],
            policy_tags=["算力"],
            has_event_data=True,
            has_fund_flow_data=True,
            has_risk_assessment=True,
        )
        assert r.composite_score >= 100.0

    def test_risk_penalty_reduces_score(self):
        r_no_risk = run_selection_priority_gate(
            score=50.0, strategy_tags=["VCP"],
            has_event_data=True, has_fund_flow_data=True,
        )
        r_with_risk = run_selection_priority_gate(
            score=50.0, strategy_tags=["VCP"],
            risk_penalty=-20.0,
            has_event_data=True, has_fund_flow_data=True,
        )
        assert r_with_risk.composite_score < r_no_risk.composite_score

    def test_composite_score_capped_at_max(self):
        r = run_selection_priority_gate(
            score=200.0, version_score=50.0, narrative_score=50.0,
            fund_flow_anomaly_score=50.0,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            strategy_tags=["VCP"],
            has_event_data=True, has_fund_flow_data=True,
            has_risk_assessment=True,
        )
        assert r.composite_score <= MAX_COMPOSITE_SCORE

    def test_composite_score_non_negative(self):
        r = run_selection_priority_gate(
            score=5.0, risk_penalty=-50.0,
        )
        assert r.composite_score >= 0.0


class TestSignalCategories:
    def test_no_categories(self):
        r = run_selection_priority_gate()
        assert r.positive_category_count == 0
        assert r.signal_category_hits == []

    def test_policy_category(self):
        r = run_selection_priority_gate(policy_tags=["低空经济"], version_score=10.0)
        assert "policy" in r.signal_category_hits
        assert r.positive_category_count >= 1

    def test_narrative_category_from_score(self):
        r = run_selection_priority_gate(narrative_score=20.0)
        assert "narrative" in r.signal_category_hits

    def test_narrative_category_from_event_tag(self):
        r = run_selection_priority_gate(strategy_tags=["EVENT_CATALYST"])
        assert "narrative" in r.signal_category_hits

    def test_tech_category_vcp(self):
        r = run_selection_priority_gate(strategy_tags=["VCP"])
        assert "tech" in r.signal_category_hits

    def test_tech_category_pullback(self):
        r = run_selection_priority_gate(strategy_tags=["PULLBACK_SUPPORT"])
        assert "tech" in r.signal_category_hits

    def test_fund_category(self):
        r = run_selection_priority_gate(
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            fund_flow_anomaly_score=10.0,
        )
        assert "fund" in r.signal_category_hits

    def test_fund_category_excludes_net_outflow(self):
        r = run_selection_priority_gate(
            fund_flow_anomaly_tags=["NET_OUTFLOW_DOMINANT"],
            fund_flow_anomaly_score=10.0,
        )
        assert "fund" not in r.signal_category_hits

    def test_all_four_categories(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP", "EVENT_CATALYST"],
            policy_tags=["算力"],
            version_score=15.0,
            narrative_score=20.0,
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            fund_flow_anomaly_score=10.0,
        )
        assert set(r.signal_category_hits) == {"policy", "narrative", "tech", "fund"}
        assert r.positive_category_count == 4


class TestNeedDeepTaGate:
    def test_single_category_does_not_pass(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"], score=80.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert not r.gate_passed
        assert not r.why_deep_ta
        assert r.why_not_deep_ta != ""

    def test_two_categories_pass(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.gate_passed
        assert r.why_deep_ta != ""

    def test_high_risk_blocks_gate(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            risk_flags=["INQUIRY_RISK"],
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert not r.gate_passed
        assert "INQUIRY_RISK" in r.why_not_deep_ta or "高风险" in r.why_not_deep_ta

    def test_financial_quality_risk_blocks(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            risk_flags=["FINANCIAL_QUALITY_RISK"],
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert not r.gate_passed

    def test_many_risks_blocks_gate(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            risk_flags=["LOCKUP_RISK", "REDUCE_HOLDING_RISK", "MARGIN_CROWDING_RISK"],
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert not r.gate_passed

    def test_heavy_penalty_blocks_gate(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            risk_penalty=-20.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert not r.gate_passed

    def test_fragile_game_balance_blocks(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            game_balance="fragile",
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert not r.gate_passed

    def test_crowded_game_balance_blocks(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            game_balance="crowded",
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert not r.gate_passed

    def test_low_data_completeness_blocks_gate(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            has_price_data=False,
            has_event_data=False,
            has_fund_flow_data=False,
            has_risk_assessment=False,
        )
        assert not r.gate_passed

    def test_favorable_game_balance_allows_gate(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            game_balance="favorable",
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.gate_passed

    def test_neutral_game_balance_allows_gate(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            game_balance="neutral",
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.gate_passed


class TestPriorityRank:
    def test_tech_only_gets_c(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"], score=50.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.priority_rank == "C"

    def test_two_categories_moderate_score_gets_b(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=20.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.priority_rank in {"B", "A"}

    def test_three_categories_high_score_gets_a(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP", "EVENT_CATALYST"],
            score=60.0,
            policy_tags=["算力"],
            version_score=25.0,
            narrative_score=30.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.priority_rank == "A"

    def test_high_risk_forces_c(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP", "EVENT_CATALYST"],
            score=80.0,
            policy_tags=["算力"],
            version_score=25.0,
            risk_flags=["INQUIRY_RISK"],
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.priority_rank == "C"

    def test_many_risks_forces_c(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP", "EVENT_CATALYST"],
            score=80.0,
            policy_tags=["算力"],
            version_score=25.0,
            risk_flags=["LOCKUP_RISK", "REDUCE_HOLDING_RISK", "MARGIN_CROWDING_RISK"],
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.priority_rank == "C"


class TestDataCompleteness:
    def test_all_fields_present(self):
        completeness, missing = _compute_data_completeness(
            has_price_data=True,
            has_event_data=True,
            has_fund_flow_data=True,
            has_fund_flow_unit_verified=True,
            has_technical_signal=True,
            has_policy_signal=True,
            has_narrative_signal=True,
            has_risk_assessment=True,
        )
        assert completeness == 1.0
        assert missing == []

    def test_no_fields_present(self):
        completeness, missing = _compute_data_completeness()
        assert completeness == 0.0
        assert len(missing) == 8

    def test_partial_completeness(self):
        completeness, missing = _compute_data_completeness(
            has_price_data=True,
            has_event_data=True,
            has_technical_signal=True,
        )
        assert completeness == 3 / 8
        assert len(missing) == 5

    def test_missing_evidence_descriptions(self):
        _, missing = _compute_data_completeness()
        assert "行情数据" in missing
        assert "事件/新闻数据" in missing
        assert "资金流数据" in missing
        assert "资金单位校验" in missing
        assert "技术形态信号" in missing
        assert "政策版本信号" in missing
        assert "叙事质量信号" in missing
        assert "风险评估" in missing


class TestExplanations:
    def test_why_deep_ta_contains_category_info(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.gate_passed
        assert "2类信号共振" in r.why_deep_ta

    def test_why_not_deep_ta_lists_blockers(self):
        r = run_selection_priority_gate()
        assert r.why_not_deep_ta != ""
        assert "0类正向信号" in r.why_not_deep_ta

    def test_why_not_deep_ta_multiple_blockers(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            risk_flags=["INQUIRY_RISK", "FINANCIAL_QUALITY_RISK", "LOCKUP_RISK"],
            game_balance="fragile",
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert not r.gate_passed
        assert "高风险" in r.why_not_deep_ta
        assert "风险标签过多" in r.why_not_deep_ta

    def test_missing_evidence_list(self):
        r = run_selection_priority_gate()
        assert len(r.missing_evidence) > 0

    def test_gate_passed_candidate_has_no_why_not(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=40.0,
            policy_tags=["低空经济"],
            version_score=15.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r.gate_passed
        assert r.why_deep_ta != ""
        assert r.why_not_deep_ta == ""


class TestNoForbiddenWords:
    @pytest.mark.parametrize("word", FORBIDDEN_WORDS)
    def test_no_forbidden_words_in_why_deep_ta(self, word):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=50.0,
            policy_tags=["低空经济"],
            version_score=20.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert word not in r.why_deep_ta
        assert word not in r.why_not_deep_ta

    def test_sanitize_replaces_forbidden(self):
        text = "建议立即买入，满仓梭哈"
        sanitized = _sanitize(text)
        assert "立即买入" not in sanitized
        assert "满仓" not in sanitized
        assert "梭哈" not in sanitized


class TestGateRefs:
    def test_refs_include_signal_categories(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            policy_tags=["低空经济"],
            version_score=15.0,
        )
        fields = [ref["field"] for ref in r.gate_refs]
        assert "signal_category" in fields

    def test_refs_include_gate_result(self):
        r = run_selection_priority_gate(
            strategy_tags=["VCP"],
            policy_tags=["低空经济"],
            version_score=15.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        fields = [ref["field"] for ref in r.gate_refs]
        assert "gate_passed" in fields or "gate_blocked" in fields

    def test_refs_include_blocker_detail(self):
        r = run_selection_priority_gate(
            risk_flags=["INQUIRY_RISK"],
            strategy_tags=["VCP"],
            policy_tags=["低空经济"],
            version_score=15.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        blocker_refs = [ref for ref in r.gate_refs if ref.get("value") == "high_risk_flags"]
        assert len(blocker_refs) > 0


# ═══════════════════════════════════════════════════════════════
# Integration tests: Candidate + evaluate_symbol
# ═══════════════════════════════════════════════════════════════

class TestCandidateIntegration:
    def test_candidate_has_s005_fields(self):
        c = Candidate(symbol="000001.SZ")
        assert hasattr(c, "composite_score")
        assert hasattr(c, "signal_category_hits")
        assert hasattr(c, "positive_category_count")
        assert hasattr(c, "data_completeness")
        assert hasattr(c, "missing_evidence")
        assert hasattr(c, "why_deep_ta")
        assert hasattr(c, "why_not_deep_ta")
        assert hasattr(c, "priority_rank")

    def test_candidate_defaults(self):
        c = Candidate(symbol="000001.SZ")
        assert c.composite_score == 0.0
        assert c.signal_category_hits == []
        assert c.positive_category_count == 0
        assert c.data_completeness == 0.0
        assert c.missing_evidence == []
        assert c.why_deep_ta == ""
        assert c.why_not_deep_ta == ""
        assert c.priority_rank == ""

    def test_evaluate_symbol_sets_composite_score(self):
        from unittest.mock import patch, MagicMock
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(10, 15, 120),
            "High": np.random.uniform(15, 20, 120),
            "Low": np.random.uniform(5, 10, 120),
            "Close": np.linspace(10, 20, 120) + np.random.normal(0, 0.5, 120),
            "Volume": np.random.uniform(1e7, 1e8, 120),
            "Amount": np.random.uniform(5e7, 5e8, 120),
        })

        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            from tradingagents.tradeflow.candidate_engine import evaluate_symbol
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="测试",
                source="manual",
                trade_date="2026-05-29",
                news_texts=["回购进展公告", "低空经济政策支持"],
            )
        if c is not None:
            assert c.composite_score > 0

    def test_evaluate_symbol_sets_priority_rank(self):
        from unittest.mock import patch
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(10, 15, 120),
            "High": np.random.uniform(15, 20, 120),
            "Low": np.random.uniform(5, 10, 120),
            "Close": np.linspace(10, 20, 120) + np.random.normal(0, 0.5, 120),
            "Volume": np.random.uniform(1e7, 1e8, 120),
            "Amount": np.random.uniform(5e7, 5e8, 120),
        })

        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            from tradingagents.tradeflow.candidate_engine import evaluate_symbol
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="测试",
                source="manual",
                trade_date="2026-05-29",
            )
        if c is not None:
            assert c.priority_rank in {"A", "B", "C", ""}


class TestDBPersistence:
    def test_save_and_load_s005_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tradeflow.db")
            init_db(db_path)

            c = Candidate(
                symbol="000001.SZ",
                name="测试",
                source="manual",
                trade_date="2026-05-29",
                strategy_tags=["VCP", "POLICY_VERSION"],
                score=45.0,
                policy_tags=["低空经济"],
                version_score=20.0,
                narrative_score=25.0,
                risk_penalty=-5.0,
                composite_score=85.0,
                signal_category_hits=["policy", "narrative", "tech"],
                positive_category_count=3,
                data_completeness=0.875,
                missing_evidence=["资金单位校验"],
                why_deep_ta="3类信号共振(政策版本(低空经济) + 叙事质量(25分) + 技术形态(VCP))，风险可控，值得深度分析",
                why_not_deep_ta="",
                priority_rank="A",
                need_deep_ta=True,
            )
            c.evidence = {"selection_priority_gate": {"test": True}}
            c.primary_strategy = "VCP"

            row_id = save_candidate(c, db_path)
            assert row_id > 0

            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE trade_date=? AND symbol=?",
                ("2026-05-29", "000001.SZ"),
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["composite_score"] == 85.0
            assert row["positive_category_count"] == 3
            assert row["data_completeness"] == 0.875
            assert row["why_deep_ta"] != ""
            assert row["priority_rank"] == "A"
            assert json.loads(row["signal_category_hits_json"]) == ["policy", "narrative", "tech"]
            assert json.loads(row["missing_evidence_json"]) == ["资金单位校验"]

    def test_db_round_trip_from_db_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tradeflow.db")
            init_db(db_path)

            c = Candidate(
                symbol="600519.SH",
                name="贵州茅台",
                source="watchlist",
                trade_date="2026-05-29",
                composite_score=120.5,
                signal_category_hits=["policy", "tech", "fund"],
                positive_category_count=3,
                data_completeness=1.0,
                missing_evidence=[],
                why_deep_ta="3类信号共振",
                why_not_deep_ta="",
                priority_rank="A",
                need_deep_ta=True,
            )
            c.primary_strategy = "VCP"
            c.evidence = {"selection_priority_gate": {"test": True}}

            save_candidate(c, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE trade_date=? AND symbol=?",
                ("2026-05-29", "600519.SH"),
            ).fetchone())
            conn.close()

            c2 = Candidate.from_db_row(row)
            assert c2.composite_score == 120.5
            assert c2.signal_category_hits == ["policy", "tech", "fund"]
            assert c2.positive_category_count == 3
            assert c2.data_completeness == 1.0
            assert c2.missing_evidence == []
            assert "3类信号共振" in c2.why_deep_ta
            assert c2.priority_rank == "A"


class TestPlanRunnerIntegration:
    def test_plan_entries_sorted_by_composite_score(self):
        from tradingagents.tradeflow.plan_runner import _build_plan_entry

        c1 = Candidate(
            symbol="000001.SZ", trade_date="2026-05-29",
            strategy_tags=["VCP"], score=30.0,
            composite_score=30.0, priority_rank="C",
        )
        c1.primary_strategy = "VCP"
        c1.need_deep_ta = False

        c2 = Candidate(
            symbol="600519.SH", trade_date="2026-05-29",
            strategy_tags=["VCP", "POLICY_VERSION"], score=50.0,
            policy_tags=["算力"], version_score=20.0,
            composite_score=75.0, priority_rank="A",
        )
        c2.primary_strategy = "VCP"
        c2.need_deep_ta = True

        entries = [_build_plan_entry(c1), _build_plan_entry(c2)]
        entries.sort(key=lambda x: (x.get("composite_score", 0), x.get("score", 0)), reverse=True)

        assert entries[0]["symbol"] == "600519.SH"
        assert entries[0]["composite_score"] == 75.0
        assert entries[1]["symbol"] == "000001.SZ"
        assert entries[1]["composite_score"] == 30.0

    def test_plan_entry_has_s005_fields(self):
        from tradingagents.tradeflow.plan_runner import _build_plan_entry

        c = Candidate(
            symbol="000001.SZ", trade_date="2026-05-29",
            composite_score=85.0,
            signal_category_hits=["policy", "tech"],
            positive_category_count=2,
            data_completeness=0.75,
            missing_evidence=["资金单位校验", "叙事质量信号"],
            why_deep_ta="2类信号共振",
            why_not_deep_ta="",
            priority_rank="A",
            need_deep_ta=True,
        )
        c.primary_strategy = "VCP"

        entry = _build_plan_entry(c)
        assert entry["composite_score"] == 85.0
        assert entry["signal_category_hits"] == ["policy", "tech"]
        assert entry["positive_category_count"] == 2
        assert entry["data_completeness"] == 0.75
        assert entry["missing_evidence"] == ["资金单位校验", "叙事质量信号"]
        assert entry["why_deep_ta"] == "2类信号共振"
        assert entry["priority_rank"] == "A"


class TestDiscoveryIntegration:
    def test_discovery_entry_has_s005_fields(self):
        from tradingagents.tradeflow.discovery import _build_discovery_entry

        c = Candidate(
            symbol="000001.SZ", trade_date="2026-05-29",
            composite_score=85.0,
            signal_category_hits=["policy", "tech", "fund"],
            positive_category_count=3,
            data_completeness=1.0,
            missing_evidence=[],
            why_deep_ta="3类信号共振",
            why_not_deep_ta="",
            priority_rank="A",
            need_deep_ta=True,
        )

        entry = _build_discovery_entry(c)
        assert entry["composite_score"] == 85.0
        assert entry["signal_category_hits"] == ["policy", "tech", "fund"]
        assert entry["positive_category_count"] == 3
        assert entry["data_completeness"] == 1.0
        assert entry["missing_evidence"] == []
        assert entry["why_deep_ta"] == "3类信号共振"
        assert entry["priority_rank"] == "A"

    def test_discovery_candidates_sorted_by_composite_score(self):
        from tradingagents.tradeflow.discovery import _build_discovery_entry

        c1 = Candidate(
            symbol="000001.SZ", trade_date="2026-05-29",
            score=30.0, composite_score=30.0, priority_rank="C",
        )
        c2 = Candidate(
            symbol="600519.SH", trade_date="2026-05-29",
            score=50.0, composite_score=75.0, priority_rank="A",
        )
        c3 = Candidate(
            symbol="000002.SZ", trade_date="2026-05-29",
            score=40.0, composite_score=50.0, priority_rank="B",
        )

        candidates = [c1, c2, c3]
        candidates.sort(key=lambda c: (c.composite_score, c.score), reverse=True)

        assert candidates[0].symbol == "600519.SH"
        assert candidates[1].symbol == "000002.SZ"
        assert candidates[2].symbol == "000001.SZ"


class TestRenderText:
    def test_daily_plan_render_shows_priority(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            summary="共1只候选",
            candidates=[{
                "symbol": "000001.SZ",
                "name": "测试",
                "action": "NEED_DEEP_TA",
                "score": 50.0,
                "strategies": ["VCP", "POLICY_VERSION"],
                "need_deep_ta": True,
                "risk_flags": [],
                "priority_rank": "A",
                "composite_score": 85.0,
                "data_completeness": 0.875,
                "why_deep_ta": "3类信号共振",
                "why_not_deep_ta": "",
                "missing_evidence": ["资金单位校验"],
            }],
        )
        text = plan.render_text()
        assert "优先级" in text
        assert "A" in text
        assert "综合分" in text
        assert "值得深挖" in text
        assert "完整度" in text

    def test_daily_plan_render_shows_why_not(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            summary="共1只候选",
            candidates=[{
                "symbol": "000001.SZ",
                "name": "测试",
                "action": "OBSERVE",
                "score": 30.0,
                "strategies": ["VCP"],
                "need_deep_ta": False,
                "risk_flags": [],
                "priority_rank": "C",
                "composite_score": 30.0,
                "data_completeness": 0.375,
                "why_deep_ta": "",
                "why_not_deep_ta": "仅1类正向信号(需≥2)",
                "missing_evidence": ["事件/新闻数据", "资金流数据"],
            }],
        )
        text = plan.render_text()
        assert "暂不深挖" in text
        assert "缺少证据" in text

    def test_render_discovery_text_shows_priority(self):
        from tradingagents.tradeflow.discovery import render_discovery_text, DiscoveryResult

        result = DiscoveryResult(
            trade_date="2026-05-29",
            universe_size=5,
            candidates=[{
                "symbol": "000001.SZ",
                "name": "测试",
                "source": "manual",
                "action": "NEED_DEEP_TA",
                "score": 50.0,
                "strategies": ["VCP"],
                "need_deep_ta": True,
                "priority_rank": "A",
                "composite_score": 85.0,
                "data_completeness": 0.875,
                "why_deep_ta": "2类信号共振",
                "why_not_deep_ta": "",
                "missing_evidence": [],
            }],
            summary="共5只，1只候选",
        )
        text = render_discovery_text(result)
        assert "优先级" in text
        assert "值得深挖" in text


class TestSortingBehavior:
    def test_tech_only_ranks_lower_than_resonance(self):
        r_tech = run_selection_priority_gate(
            strategy_tags=["VCP"], score=50.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        r_resonance = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=50.0,
            policy_tags=["低空经济"],
            version_score=20.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r_resonance.composite_score > r_tech.composite_score

    def test_risk_heavy_demotes_ranking(self):
        r_no_risk = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=50.0,
            policy_tags=["低空经济"],
            version_score=20.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        r_with_risk = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=50.0,
            policy_tags=["低空经济"],
            version_score=20.0,
            risk_flags=["LOCKUP_RISK", "REDUCE_HOLDING_RISK", "MARGIN_CROWDING_RISK"],
            risk_penalty=-15.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        assert r_with_risk.composite_score < r_no_risk.composite_score

    def test_data_incomplete_demotes_ranking(self):
        r_full = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=50.0,
            policy_tags=["低空经济"],
            version_score=20.0,
            has_event_data=True, has_fund_flow_data=True, has_risk_assessment=True,
        )
        r_sparse = run_selection_priority_gate(
            strategy_tags=["VCP"],
            score=50.0,
            policy_tags=["低空经济"],
            version_score=20.0,
            has_event_data=False, has_fund_flow_data=False, has_risk_assessment=False,
        )
        assert r_sparse.composite_score < r_full.composite_score


class TestEdgeCases:
    def test_none_inputs(self):
        r = run_selection_priority_gate(
            strategy_tags=None,
            policy_tags=None,
            risk_flags=None,
            fund_flow_anomaly_tags=None,
        )
        assert r.composite_score >= 0.0

    def test_empty_lists(self):
        r = run_selection_priority_gate(
            strategy_tags=[],
            policy_tags=[],
            risk_flags=[],
            fund_flow_anomaly_tags=[],
        )
        assert r.positive_category_count == 0

    def test_min_categories_constant(self):
        assert MIN_POSITIVE_CATEGORIES_FOR_DEEP_TA == 2

    def test_max_composite_score_constant(self):
        assert MAX_COMPOSITE_SCORE == 200.0

    def test_negative_score_with_large_penalty(self):
        r = run_selection_priority_gate(
            score=5.0, risk_penalty=-100.0,
        )
        assert r.composite_score == 0.0

    def test_fund_flow_score_zero_excludes_fund_category(self):
        r = run_selection_priority_gate(
            fund_flow_anomaly_tags=["CONSECUTIVE_INFLOW"],
            fund_flow_anomaly_score=0.0,
        )
        assert "fund" not in r.signal_category_hits
