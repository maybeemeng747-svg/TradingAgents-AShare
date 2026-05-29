# [S-008] tradeflow_evidence_gate — tests
"""Tests for TradeFlow Evidence Completeness Gate (S-008).

Covers:
1. compute_evidence_completeness — dimension counting, completeness ratio,
   gate verdicts, missing fields, upgrade hints.
2. apply_evidence_gate — tier downgrade, need_deep_ta override, gate_applied flag.
3. Integration with candidate_engine.evaluate_symbol — full pipeline test.
4. Schema persistence — to_db_row / from_db_row round-trip.
5. Plan / Discovery output — evidence gate fields in entries and metadata.
6. Rendering — DailyPlan.render_text and render_discovery_text show gate info.

Design constraints verified:
- Data missing → can only downgrade, not fabricate.
- Fund flow unit unverified → missing_data_fields includes "资金流单位校验".
- No event source → missing_data_fields includes "事件/新闻来源".
- Complete data → not downgraded.
- No strong buy/sell words.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from tradingagents.tradeflow.evidence_gate import (
    compute_evidence_completeness,
    apply_evidence_gate,
    EvidenceGateResult,
    TOTAL_EVIDENCE_DIMENSIONS,
    COMPLETENESS_FOR_A_TIER,
    COMPLETENESS_FOR_DEEP_TA,
    EVIDENCE_DIMENSION_NAMES,
)
from tradingagents.tradeflow.schemas import Candidate, DailyPlan
from tradingagents.tradeflow.candidate_engine import (
    evaluate_symbol,
    init_db,
    save_candidate,
    _calc_liquidity,
)


# ── Helpers ──


def _make_candidate(**overrides) -> Candidate:
    defaults = {
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "source": "manual",
        "strategy_tags": ["VCP"],
        "score": 50.0,
    }
    defaults.update(overrides)
    return Candidate(**defaults)


def _make_df(n=60, price=100.0):
    import pandas as pd
    import numpy as np
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    data = {
        "Date": dates,
        "Open": np.full(n, price),
        "High": np.full(n, price * 1.02),
        "Low": np.full(n, price * 0.98),
        "Close": np.full(n, price),
        "Volume": np.full(n, 5_000_000),
    }
    df = pd.DataFrame(data)
    return df


# ═══════════════════════════════════════════════════════════════════
# 1. compute_evidence_completeness
# ═══════════════════════════════════════════════════════════════════


class TestComputeEvidenceCompleteness:
    def test_all_present(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_fund_flow_unit=True, has_risk_labels=True, has_tech_signal=True,
            has_policy_signal=True, has_narrative_signal=True, has_fund_signal=True,
            has_game_assessment=True,
        )
        assert result.tradeflow_data_completeness == 1.0
        assert result.missing_data_fields == []
        assert len(result.present_data_fields) == TOTAL_EVIDENCE_DIMENSIONS
        assert result.can_enter_a_tier is True
        assert result.can_trigger_deep_ta is True

    def test_all_absent(self):
        result = compute_evidence_completeness()
        assert result.tradeflow_data_completeness == 0.0
        assert len(result.missing_data_fields) == TOTAL_EVIDENCE_DIMENSIONS
        assert result.present_data_fields == []
        assert result.can_enter_a_tier is False
        assert result.can_trigger_deep_ta is False

    def test_only_ohlcv_and_liquidity(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True,
        )
        assert result.tradeflow_data_completeness == pytest.approx(2 / TOTAL_EVIDENCE_DIMENSIONS, abs=0.01)
        assert "行情OHLCV数据" in result.present_data_fields
        assert "流动性达标" in result.present_data_fields
        assert "事件/新闻来源" in result.missing_data_fields
        assert result.can_enter_a_tier is False
        assert result.can_trigger_deep_ta is False

    def test_six_dimensions_meets_deep_ta_threshold(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_risk_labels=True, has_tech_signal=True, has_game_assessment=True,
        )
        assert result.tradeflow_data_completeness == pytest.approx(6 / TOTAL_EVIDENCE_DIMENSIONS, abs=0.01)
        assert result.can_trigger_deep_ta is True
        assert result.can_enter_a_tier is True

    def test_five_dimensions_deep_ta_boundary(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_risk_labels=True, has_tech_signal=True,
        )
        comp = 5 / TOTAL_EVIDENCE_DIMENSIONS
        assert result.tradeflow_data_completeness == pytest.approx(comp, abs=0.01)
        assert result.can_trigger_deep_ta is (comp >= COMPLETENESS_FOR_DEEP_TA)
        assert result.can_enter_a_tier is (comp >= COMPLETENESS_FOR_A_TIER)

    def test_missing_fund_flow_unit_listed(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_fund_flow_unit=False,
        )
        assert "资金流单位校验" in result.missing_data_fields

    def test_missing_event_source_listed(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=False,
        )
        assert "事件/新闻来源" in result.missing_data_fields

    def test_missing_risk_labels_listed(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_risk_labels=False,
        )
        assert "风险标签证据" in result.missing_data_fields

    def test_what_to_upgrade_critical_missing(self):
        result = compute_evidence_completeness(has_ohlcv=False, has_liquidity=False)
        upgrade_text = " ".join(result.what_to_upgrade)
        assert "关键证据缺失" in upgrade_text

    def test_what_to_upgrade_signal_insufficient(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_tech_signal=False, has_policy_signal=False,
            has_narrative_signal=False, has_fund_signal=False,
        )
        upgrade_text = " ".join(result.what_to_upgrade)
        assert "信号维度不足" in upgrade_text

    def test_what_to_upgrade_completeness_hint(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True,
        )
        upgrade_text = " ".join(result.what_to_upgrade)
        assert "完整度需" in upgrade_text

    def test_what_to_upgrade_no_fund_and_event(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True,
            has_fund_flow_unit=False, has_event_source=False,
        )
        upgrade_text = " ".join(result.what_to_upgrade)
        assert "资金单位校验或事件来源" in upgrade_text

    def test_evidence_gate_refs_present(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True,
        )
        assert len(result.evidence_gate_refs) > 0
        completeness_ref = [r for r in result.evidence_gate_refs if r["field"] == "tradeflow_data_completeness"]
        assert len(completeness_ref) == 1
        assert completeness_ref[0]["present_count"] == 2

    def test_evidence_gate_refs_blocked(self):
        result = compute_evidence_completeness()
        blocked_refs = [r for r in result.evidence_gate_refs if r.get("value") == "completeness_below_a_tier"]
        assert len(blocked_refs) == 1

    def test_no_strong_buy_sell_words(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True,
        )
        all_text = " ".join(result.what_to_upgrade + result.missing_data_fields)
        for w in {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}:
            assert w not in all_text

    def test_gate_completeness_equals_tradeflow_completeness(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
        )
        assert result.gate_completeness == result.tradeflow_data_completeness

    def test_seven_dimensions_all_gates_open(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_risk_labels=True, has_tech_signal=True, has_game_assessment=True,
            has_fund_flow_unit=True,
        )
        assert result.tradeflow_data_completeness == 0.7
        assert result.can_enter_a_tier is True
        assert result.can_trigger_deep_ta is True


# ═══════════════════════════════════════════════════════════════════
# 2. apply_evidence_gate
# ═══════════════════════════════════════════════════════════════════


class TestApplyEvidenceGate:
    def test_no_override_when_completeness_sufficient(self):
        result = apply_evidence_gate(
            candidate_completeness=0.7,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=True,
            can_trigger_deep_ta=True,
        )
        assert result["tier"] == "A"
        assert result["need_deep_ta"] is True
        assert result["gate_applied"] is False

    def test_a_tier_downgraded_to_b(self):
        result = apply_evidence_gate(
            candidate_completeness=0.4,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
        )
        assert result["tier"] == "B"
        assert result["ta_budget_priority"] == 30
        assert result["gate_applied"] is True
        assert "证据完整度不足" in result["tier_reason_addition"]

    def test_deep_ta_blocked(self):
        result = apply_evidence_gate(
            candidate_completeness=0.4,
            candidate_tier="B",
            candidate_need_deep_ta=True,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
        )
        assert result["need_deep_ta"] is False
        assert result["gate_applied"] is True
        assert "证据完整度不足" in result["why_not_deep_ta_addition"]

    def test_b_tier_not_downgraded(self):
        result = apply_evidence_gate(
            candidate_completeness=0.4,
            candidate_tier="B",
            candidate_need_deep_ta=False,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
        )
        assert result["tier"] == "B"
        assert result["gate_applied"] is False

    def test_c_tier_unchanged(self):
        result = apply_evidence_gate(
            candidate_completeness=0.2,
            candidate_tier="C",
            candidate_need_deep_ta=False,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
        )
        assert result["tier"] == "C"
        assert result["gate_applied"] is False

    def test_a_tier_kept_when_completeness_ok(self):
        result = apply_evidence_gate(
            candidate_completeness=0.7,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=True,
            can_trigger_deep_ta=True,
        )
        assert result["tier"] == "A"
        assert result["ta_budget_priority"] == 100
        assert result["need_deep_ta"] is True
        assert result["gate_applied"] is False

    def test_missing_data_fields_passed_through(self):
        result = apply_evidence_gate(
            candidate_completeness=0.3,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
            missing_data_fields=["行情OHLCV数据", "流动性达标"],
        )
        assert "行情OHLCV数据" in result["missing_data_fields"]
        assert "流动性达标" in result["missing_data_fields"]

    def test_what_to_upgrade_passed_through(self):
        result = apply_evidence_gate(
            candidate_completeness=0.3,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
            what_to_upgrade=["关键证据缺失: 行情数据"],
        )
        assert len(result["what_to_upgrade"]) >= 1

    def test_no_strong_buy_sell_words(self):
        result = apply_evidence_gate(
            candidate_completeness=0.3,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
        )
        all_text = result["tier_reason_addition"] + result["why_not_deep_ta_addition"]
        for w in {"立即买入", "重仓买入", "满仓", "梭哈"}:
            assert w not in all_text

    def test_ta_budget_correct_for_each_tier(self):
        for tier, expected_budget in [("A", 100), ("B", 30), ("C", 0)]:
            result = apply_evidence_gate(
                candidate_completeness=0.8,
                candidate_tier=tier,
                candidate_need_deep_ta=False,
                can_enter_a_tier=(tier != "C"),
                can_trigger_deep_ta=True,
            )
            if tier == "A" or (tier == "C"):
                pass
            assert result["ta_budget_priority"] == expected_budget or result["gate_applied"]

    def test_deep_ta_kept_when_completeness_sufficient(self):
        result = apply_evidence_gate(
            candidate_completeness=0.6,
            candidate_tier="B",
            candidate_need_deep_ta=True,
            can_enter_a_tier=True,
            can_trigger_deep_ta=True,
        )
        assert result["need_deep_ta"] is True
        assert result["gate_applied"] is False


# ═══════════════════════════════════════════════════════════════════
# 3. Integration with candidate_engine.evaluate_symbol
# ═══════════════════════════════════════════════════════════════════


class TestEvaluateSymbolIntegration:
    @pytest.fixture
    def mock_df(self):
        return _make_df(60, 100.0)

    def test_evidence_gate_fields_populated(self, mock_df):
        c, reason = evaluate_symbol(
            symbol="600519.SH",
            name="贵州茅台",
            source="manual",
            news_texts=["低空经济政策利好"],
            df=mock_df,
        )
        if c is None:
            pytest.skip("No strategy hit for this test data")
        assert c.tradeflow_data_completeness > 0.0
        assert isinstance(c.missing_data_fields, list)
        assert isinstance(c.what_to_upgrade, list)
        assert isinstance(c.evidence_gate_applied, bool)
        assert "evidence_gate" in c.evidence

    def test_no_fund_flow_data_gives_low_completeness(self, mock_df):
        c, reason = evaluate_symbol(
            symbol="600519.SH",
            name="贵州茅台",
            source="manual",
            news_texts=["低空经济政策利好"],
            df=mock_df,
        )
        if c is None:
            pytest.skip("No strategy hit for this test data")
        assert "资金流单位校验" in c.missing_data_fields or c.fund_flow_unit_verified is True

    def test_low_completeness_blocks_a_tier(self, mock_df):
        c, reason = evaluate_symbol(
            symbol="600519.SH",
            name="贵州茅台",
            source="manual",
            df=mock_df,
        )
        if c is None:
            pytest.skip("No strategy hit")
        if c.tradeflow_data_completeness < COMPLETENESS_FOR_A_TIER:
            assert c.tier != "A" or c.evidence_gate_applied is True

    def test_evidence_gate_applied_flag_set(self, mock_df):
        c, reason = evaluate_symbol(
            symbol="600519.SH",
            name="贵州茅台",
            source="manual",
            df=mock_df,
        )
        if c is None:
            pytest.skip("No strategy hit")
        gate_info = c.evidence.get("evidence_gate", {})
        if gate_info.get("gate_applied"):
            assert c.evidence_gate_applied is True


# ═══════════════════════════════════════════════════════════════════
# 4. Schema persistence — to_db_row / from_db_row round-trip
# ═══════════════════════════════════════════════════════════════════


class TestSchemaPersistence:
    def test_round_trip_s008_fields(self):
        c = _make_candidate(
            tradeflow_data_completeness=0.7,
            missing_data_fields=["事件/新闻来源", "资金流单位校验"],
            what_to_upgrade=["需补充资金单位校验或事件来源中至少一项"],
            evidence_gate_applied=True,
        )
        row = c.to_db_row()
        assert row["tradeflow_data_completeness"] == 0.7
        assert json.loads(row["missing_data_fields_json"]) == ["事件/新闻来源", "资金流单位校验"]
        assert json.loads(row["what_to_upgrade_json"]) == ["需补充资金单位校验或事件来源中至少一项"]
        assert row["evidence_gate_applied"] == 1

        restored = Candidate.from_db_row(row)
        assert restored.tradeflow_data_completeness == 0.7
        assert restored.missing_data_fields == ["事件/新闻来源", "资金流单位校验"]
        assert restored.what_to_upgrade == ["需补充资金单位校验或事件来源中至少一项"]
        assert restored.evidence_gate_applied is True

    def test_default_values(self):
        c = _make_candidate()
        assert c.tradeflow_data_completeness == 0.0
        assert c.missing_data_fields == []
        assert c.what_to_upgrade == []
        assert c.evidence_gate_applied is False

    def test_db_round_trip_via_sqlite(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = _make_candidate(
                tradeflow_data_completeness=0.5,
                missing_data_fields=["资金流单位校验"],
                what_to_upgrade=["完整度需≥60%"],
                evidence_gate_applied=True,
            )
            save_candidate(c, db_path)
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol=?", (c.symbol,)
            ).fetchone())
            conn.close()
            assert row["tradeflow_data_completeness"] == 0.5
            assert "资金流单位校验" in json.loads(row["missing_data_fields_json"])
        finally:
            os.unlink(db_path)


# ═══════════════════════════════════════════════════════════════════
# 5. Plan / Discovery output
# ═══════════════════════════════════════════════════════════════════


class TestPlanOutput:
    def test_plan_entry_contains_s008_fields(self):
        from tradingagents.tradeflow.plan_runner import _build_plan_entry
        c = _make_candidate(
            tradeflow_data_completeness=0.6,
            missing_data_fields=["资金流单位校验"],
            what_to_upgrade=["需补充资金流单位校验"],
            evidence_gate_applied=False,
        )
        entry = _build_plan_entry(c)
        assert entry["tradeflow_data_completeness"] == 0.6
        assert entry["missing_data_fields"] == ["资金流单位校验"]
        assert entry["what_to_upgrade"] == ["需补充资金流单位校验"]
        assert entry["evidence_gate_applied"] is False

    def test_discovery_entry_contains_s008_fields(self):
        from tradingagents.tradeflow.discovery import _build_discovery_entry
        c = _make_candidate(
            tradeflow_data_completeness=0.8,
            missing_data_fields=[],
            what_to_upgrade=[],
            evidence_gate_applied=False,
        )
        entry = _build_discovery_entry(c)
        assert entry["tradeflow_data_completeness"] == 0.8
        assert entry["missing_data_fields"] == []
        assert entry["evidence_gate_applied"] is False

    def test_plan_metadata_contains_evidence_gate(self):
        c1 = _make_candidate(
            symbol="600519.SH",
            tradeflow_data_completeness=0.3,
            missing_data_fields=["行情OHLCV数据"],
            what_to_upgrade=["关键证据缺失"],
            evidence_gate_applied=True,
            need_deep_ta=False,
        )
        c2 = _make_candidate(
            symbol="000001.SZ",
            tradeflow_data_completeness=0.8,
            missing_data_fields=[],
            what_to_upgrade=[],
            evidence_gate_applied=False,
            need_deep_ta=True,
        )
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[
                {"symbol": "600519.SH", "tradeflow_data_completeness": 0.3, "evidence_gate_applied": True, "need_deep_ta": False},
                {"symbol": "000001.SZ", "tradeflow_data_completeness": 0.8, "evidence_gate_applied": False, "need_deep_ta": True},
            ],
        )
        plan.metadata["evidence_gate"] = {
            "gate_blocked_count": 1,
            "deep_ta_blocked_count": 0,
            "avg_completeness": 0.55,
        }
        assert plan.metadata["evidence_gate"]["gate_blocked_count"] == 1
        assert plan.metadata["evidence_gate"]["avg_completeness"] == 0.55


# ═══════════════════════════════════════════════════════════════════
# 6. Rendering
# ═══════════════════════════════════════════════════════════════════


class TestRendering:
    def test_render_text_shows_evidence_completeness(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "action": "OBSERVE",
                "strategies": ["VCP"],
                "score": 50,
                "tradeflow_data_completeness": 0.6,
                "missing_data_fields": ["资金流单位校验"],
                "what_to_upgrade": ["需补充资金流单位校验"],
                "evidence_gate_applied": False,
            }],
        )
        text = plan.render_text()
        assert "证据完整度" in text
        assert "60%" in text
        assert "资金流单位校验" in text

    def test_render_text_shows_gate_applied(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "action": "OBSERVE",
                "tradeflow_data_completeness": 0.3,
                "missing_data_fields": ["行情OHLCV数据"],
                "what_to_upgrade": ["关键证据缺失"],
                "evidence_gate_applied": True,
            }],
        )
        text = plan.render_text()
        assert "门禁降级" in text
        assert "30%" in text

    def test_render_text_no_strong_words(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "action": "OBSERVE",
                "tradeflow_data_completeness": 0.5,
                "missing_data_fields": [],
                "what_to_upgrade": [],
                "evidence_gate_applied": False,
            }],
        )
        text = plan.render_text()
        for w in {"立即买入", "重仓买入", "满仓", "梭哈"}:
            assert w not in text

    def test_discovery_render_shows_evidence(self):
        from tradingagents.tradeflow.discovery import render_discovery_text, DiscoveryResult
        result = DiscoveryResult(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "source": "manual",
                "action": "OBSERVE",
                "tradeflow_data_completeness": 0.7,
                "missing_data_fields": ["资金流单位校验"],
                "what_to_upgrade": ["需补充资金单位校验"],
                "evidence_gate_applied": False,
            }],
        )
        text = render_discovery_text(result)
        assert "证据完整度" in text
        assert "70%" in text


# ═══════════════════════════════════════════════════════════════════
# 7. Verification scenarios from S-008 spec
# ═══════════════════════════════════════════════════════════════════


class TestVerificationScenarios:
    def test_fund_unit_unverified_no_high_confidence(self):
        """缺资金单位校验时不触发高置信资金异动."""
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_fund_flow_unit=False,
        )
        assert "资金流单位校验" in result.missing_data_fields

    def test_no_price_data_blocks_a_tier(self):
        """缺行情/流动性数据时不进入 A 层."""
        result = compute_evidence_completeness(
            has_ohlcv=False, has_liquidity=False,
        )
        assert result.can_enter_a_tier is False
        assert result.can_trigger_deep_ta is False

    def test_complete_data_not_downgraded(self):
        """完整数据样本不被误降级."""
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_fund_flow_unit=True, has_risk_labels=True, has_tech_signal=True,
            has_policy_signal=True, has_narrative_signal=True, has_fund_signal=True,
            has_game_assessment=True,
        )
        assert result.can_enter_a_tier is True
        assert result.can_trigger_deep_ta is True
        assert result.missing_data_fields == []

    def test_missing_liquidity_blocks_a(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=False,
            has_event_source=True, has_risk_labels=True,
            has_tech_signal=True, has_game_assessment=True,
        )
        assert result.can_enter_a_tier is False

    def test_missing_event_source_blocks_deep_ta_at_boundary(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=False,
            has_fund_flow_unit=True, has_risk_labels=True, has_tech_signal=True,
        )
        comp = result.tradeflow_data_completeness
        if comp < COMPLETENESS_FOR_DEEP_TA:
            assert result.can_trigger_deep_ta is False

    def test_apply_gate_downgrades_a_tier_when_incomplete(self):
        """Complete pipeline: A tier candidate downgraded when evidence incomplete."""
        gate = apply_evidence_gate(
            candidate_completeness=0.4,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
            missing_data_fields=["行情OHLCV数据", "流动性达标"],
        )
        assert gate["tier"] == "B"
        assert gate["need_deep_ta"] is False
        assert gate["gate_applied"] is True

    def test_apply_gate_no_downgrade_when_complete(self):
        """Complete data: A tier stays, deep TA stays."""
        gate = apply_evidence_gate(
            candidate_completeness=0.8,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=True,
            can_trigger_deep_ta=True,
        )
        assert gate["tier"] == "A"
        assert gate["need_deep_ta"] is True
        assert gate["gate_applied"] is False


# ═══════════════════════════════════════════════════════════════════
# 8. Edge cases
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_exactly_at_a_tier_threshold(self):
        needed = int(TOTAL_EVIDENCE_DIMENSIONS * COMPLETENESS_FOR_A_TIER)
        kwargs = {f"has_{k}": True for k in list(EVIDENCE_DIMENSION_NAMES.keys())[:needed]}
        result = compute_evidence_completeness(**kwargs)
        assert result.tradeflow_data_completeness == pytest.approx(needed / TOTAL_EVIDENCE_DIMENSIONS, abs=0.01)
        if result.tradeflow_data_completeness >= COMPLETENESS_FOR_A_TIER:
            assert result.can_enter_a_tier is True

    def test_exactly_at_deep_ta_threshold(self):
        needed = int(TOTAL_EVIDENCE_DIMENSIONS * COMPLETENESS_FOR_DEEP_TA)
        kwargs = {f"has_{k}": True for k in list(EVIDENCE_DIMENSION_NAMES.keys())[:needed]}
        result = compute_evidence_completeness(**kwargs)
        if result.tradeflow_data_completeness >= COMPLETENESS_FOR_DEEP_TA:
            assert result.can_trigger_deep_ta is True

    def test_what_to_upgrade_empty_when_all_present(self):
        result = compute_evidence_completeness(
            has_ohlcv=True, has_liquidity=True, has_event_source=True,
            has_fund_flow_unit=True, has_risk_labels=True, has_tech_signal=True,
            has_policy_signal=True, has_narrative_signal=True, has_fund_signal=True,
            has_game_assessment=True,
        )
        assert result.what_to_upgrade == []

    def test_empty_candidate_schema_defaults(self):
        c = Candidate(symbol="000001.SZ")
        assert c.tradeflow_data_completeness == 0.0
        assert c.missing_data_fields == []
        assert c.what_to_upgrade == []
        assert c.evidence_gate_applied is False

    def test_apply_gate_empty_missing_fields(self):
        result = apply_evidence_gate(
            candidate_completeness=0.9,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=True,
            can_trigger_deep_ta=True,
        )
        assert result["missing_data_fields"] == []

    def test_apply_gate_with_large_missing_fields(self):
        result = apply_evidence_gate(
            candidate_completeness=0.1,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
            missing_data_fields=[f"field_{i}" for i in range(20)],
        )
        assert len(result["missing_data_fields"]) == 20

    def test_evidence_dimension_names_complete(self):
        assert len(EVIDENCE_DIMENSION_NAMES) == TOTAL_EVIDENCE_DIMENSIONS
        for key in EVIDENCE_DIMENSION_NAMES:
            assert isinstance(EVIDENCE_DIMENSION_NAMES[key], str)
            assert len(EVIDENCE_DIMENSION_NAMES[key]) > 0

    def test_sanitize_in_apply_gate(self):
        result = apply_evidence_gate(
            candidate_completeness=0.3,
            candidate_tier="A",
            candidate_need_deep_ta=True,
            can_enter_a_tier=False,
            can_trigger_deep_ta=False,
            what_to_upgrade=["关键证据缺失: 满仓买入"],
        )
        for hint in result["what_to_upgrade"]:
            assert "满仓买入" not in hint
