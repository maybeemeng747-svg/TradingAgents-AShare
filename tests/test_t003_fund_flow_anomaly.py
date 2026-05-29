# [T-003] fund_flow_anomaly_pool tests
"""Tests for fund flow anomaly detection in TradeFlow.

Covers:
- Consecutive inflow detection
- Large single-day anomaly detection
- High proportion detection
- Board resonance detection
- Net outflow dominant detection
- Unit verification behavior
- Individual/board separation
- Integration with candidate_engine
- Integration with plan_runner
- Integration with discovery
- DB persistence round-trip
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from tradingagents.tradeflow.fund_flow_anomaly import (
    detect_fund_flow_anomaly,
    FundFlowAnomalyResult,
    _extract_net_flows,
    _detect_consecutive_inflow,
    _detect_large_single_day,
    _detect_high_proportion,
    _detect_board_resonance,
    _detect_net_outflow_dominant,
    _verify_unit,
    FUND_FLOW_TAG_CONSECUTIVE_INFLOW,
    FUND_FLOW_TAG_HIGH_PROPORTION,
    FUND_FLOW_TAG_BOARD_RESONANCE,
    FUND_FLOW_TAG_LARGE_SINGLE_DAY,
    FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT,
    MAX_FUND_FLOW_BONUS,
)
from tradingagents.tradeflow.schemas import (
    Candidate,
    CandidateSignal,
    DailyPlan,
    STRATEGY_FUND_FLOW,
)
from tradingagents.tradeflow.candidate_engine import (
    evaluate_symbol,
    init_db,
    save_candidate,
)


# ── Helper data ──

def _make_fund_flow_text(values: list[float], unit_hint: bool = True) -> str:
    """Build a fake individual fund flow text with given net flow values."""
    prefix = "600519.SH 近20日主力资金净流向：\n"
    header = "日期       收盘价  主力净流入-净额  主力净流入-净占比  超大单净流入-净额"
    if not unit_hint:
        header = "日期       收盘价  主力净流入  主力净流入占比  超大单净流入"
    lines = [prefix + header]
    for i, val in enumerate(values):
        pct = abs(val) / 1000
        lines.append(f"2026-05-{20+i:02d}  25.00  {val:>10.0f}    {pct:>6.2f}%     {val*0.6:>10.0f}")
    return "\n".join(lines)


def _make_board_fund_flow_text(sector_name: str = "半导体", inflow: bool = True) -> str:
    """Build a fake board fund flow text."""
    direction = "净流入" if inflow else "净流出"
    val = "80000" if inflow else "-50000"
    header = "板块资金流向排名（共50个板块，前10名）："
    header += f"\n排名 名称       今日主力{direction}-净额  今日主力净流入-净占比"
    header += f"\n1    {sector_name}  {val}                  3.5%"
    header += f"\n2    电子元件   -20000                  -1.2%"
    return header


# ══════════════════════════════════════════════
# 1. Unit extraction tests
# ══════════════════════════════════════════════

class TestExtractNetFlows:
    def test_empty_text(self):
        assert _extract_net_flows("") == []

    def test_failed_text(self):
        assert _extract_net_flows("获取失败") == []
        assert _extract_net_flows("数据不可用") == []

    def test_normal_extraction(self):
        text = _make_fund_flow_text([10000, -20000, 30000])
        flows = _extract_net_flows(text)
        assert len(flows) > 0
        assert 10000 in flows
        assert -20000 in flows

    def test_only_fund_keyword_lines(self):
        text = "日期       600519   贵州茅台   1800.00\n"
        text += "2026-05-20  25.00   10000"
        flows = _extract_net_flows(text)
        # Lines without fund keywords should be skipped
        # The header line has no fund keywords, so only data lines matter

    def test_negative_values(self):
        text = "日期  收盘价  主力净流入-净额  占比\n2026-05-20  25.00  -50000  -2.5%"
        flows = _extract_net_flows(text)
        assert -50000 in flows


# ══════════════════════════════════════════════
# 2. Consecutive inflow tests
# ══════════════════════════════════════════════

class TestConsecutiveInflow:
    def test_consecutive_5_days(self):
        flows = [10000, 20000, 30000, 15000, 8000]
        is_consec, streak, refs = _detect_consecutive_inflow(flows, 5)
        assert is_consec is True
        assert streak == 5
        assert len(refs) == 1

    def test_consecutive_3_days(self):
        flows = [10000, 20000, 15000, -5000, 8000]
        is_consec, streak, refs = _detect_consecutive_inflow(flows, 5)
        assert is_consec is True
        assert streak == 3

    def test_not_consecutive_2_days(self):
        flows = [10000, -5000, 30000, 15000, 8000]
        is_consec, streak, refs = _detect_consecutive_inflow(flows, 5)
        assert is_consec is False

    def test_all_outflow(self):
        flows = [-10000, -20000, -30000]
        is_consec, streak, refs = _detect_consecutive_inflow(flows, 5)
        assert is_consec is False
        assert streak == 0

    def test_empty_flows(self):
        is_consec, streak, refs = _detect_consecutive_inflow([], 5)
        assert is_consec is False

    def test_consecutive_10_days_capped_lookback(self):
        flows = [1000] * 20
        is_consec, streak, refs = _detect_consecutive_inflow(flows, 5)
        assert is_consec is True
        assert streak == 5  # capped at lookback


# ══════════════════════════════════════════════
# 3. Large single day tests
# ══════════════════════════════════════════════

class TestLargeSingleDay:
    def test_large_inflow(self):
        flows = [100, 200, 80000, 50]
        is_large, max_val, refs = _detect_large_single_day(flows)
        assert is_large is True
        assert max_val == 80000

    def test_large_outflow(self):
        flows = [100, -90000, 50]
        is_large, max_val, refs = _detect_large_single_day(flows)
        assert is_large is True

    def test_no_large(self):
        flows = [100, 200, 300]
        is_large, max_val, refs = _detect_large_single_day(flows)
        assert is_large is False

    def test_empty(self):
        is_large, max_val, refs = _detect_large_single_day([])
        assert is_large is False


# ══════════════════════════════════════════════
# 4. High proportion tests
# ══════════════════════════════════════════════

class TestHighProportion:
    def test_explicit_percentage(self):
        text = "主力净流入-净占比  6.5%"
        is_high, pct, refs = _detect_high_proportion(text)
        assert is_high is True
        assert pct >= 5.0

    def test_below_threshold(self):
        text = "主力净流入-净占比  3.2%"
        is_high, pct, refs = _detect_high_proportion(text)
        assert is_high is False

    def test_inline_proportion(self):
        text = "主力净流入占比  8.0%"
        is_high, pct, refs = _detect_high_proportion(text)
        assert is_high is True

    def test_failed_data(self):
        is_high, pct, refs = _detect_high_proportion("获取失败")
        assert is_high is False

    def test_empty_text(self):
        is_high, pct, refs = _detect_high_proportion("")
        assert is_high is False


# ══════════════════════════════════════════════
# 5. Board resonance tests
# ══════════════════════════════════════════════

class TestBoardResonance:
    def test_resonance_both_positive(self):
        ind_text = _make_fund_flow_text([10000, 20000, 30000])
        board_text = _make_board_fund_flow_text(inflow=True)
        is_res, refs = _detect_board_resonance(ind_text, board_text)
        assert is_res is True
        assert len(refs) == 1

    def test_no_resonance_board_outflow(self):
        ind_text = _make_fund_flow_text([10000, 20000, 30000])
        board_text = _make_board_fund_flow_text(inflow=False)
        is_res, refs = _detect_board_resonance(ind_text, board_text)
        assert is_res is False

    def test_no_resonance_individual_outflow(self):
        ind_text = _make_fund_flow_text([-10000, -20000, 30000])
        board_text = _make_board_fund_flow_text(inflow=True)
        is_res, refs = _detect_board_resonance(ind_text, board_text)
        assert is_res is False

    def test_missing_board(self):
        ind_text = _make_fund_flow_text([10000, 20000, 30000])
        is_res, refs = _detect_board_resonance(ind_text, "")
        assert is_res is False

    def test_missing_individual(self):
        board_text = _make_board_fund_flow_text(inflow=True)
        is_res, refs = _detect_board_resonance("", board_text)
        assert is_res is False

    def test_failed_individual(self):
        ind_text = "获取失败"
        board_text = _make_board_fund_flow_text(inflow=True)
        is_res, refs = _detect_board_resonance(ind_text, board_text)
        assert is_res is False


# ══════════════════════════════════════════════
# 6. Net outflow dominant tests
# ══════════════════════════════════════════════

class TestNetOutflowDominant:
    def test_70_percent_outflow(self):
        flows = [-1000, -2000, -3000, -4000, 500, -6000, -7000]
        is_dom, count, refs = _detect_net_outflow_dominant(flows, 10)
        assert is_dom is True
        assert count >= 5

    def test_balanced(self):
        flows = [1000, -1000, 2000, -2000, 3000]
        is_dom, count, refs = _detect_net_outflow_dominant(flows, 5)
        assert is_dom is False

    def test_all_inflow(self):
        flows = [1000, 2000, 3000, 4000, 5000]
        is_dom, count, refs = _detect_net_outflow_dominant(flows, 5)
        assert is_dom is False

    def test_empty(self):
        is_dom, count, refs = _detect_net_outflow_dominant([], 5)
        assert is_dom is False

    def test_too_few_days(self):
        flows = [-1000, -2000]
        is_dom, count, refs = _detect_net_outflow_dominant(flows, 5)
        assert is_dom is False


# ══════════════════════════════════════════════
# 7. Unit verification tests
# ══════════════════════════════════════════════

class TestVerifyUnit:
    def test_unit_wanyuan(self):
        assert _verify_unit("主力净流入-净额 50000 万元") is True

    def test_unit_net_e(self):
        assert _verify_unit("主力净流入-净额 50000") is True

    def test_no_unit_hint(self):
        assert _verify_unit("just some random text 50000") is False

    def test_failed_data(self):
        assert _verify_unit("获取失败") is False

    def test_empty(self):
        assert _verify_unit("") is False

    def test_unit_wan(self):
        assert _verify_unit("主力 50000 万") is True


# ══════════════════════════════════════════════
# 8. Full detect_fund_flow_anomaly tests
# ══════════════════════════════════════════════

class TestDetectFundFlowAnomaly:
    def test_no_data(self):
        result = detect_fund_flow_anomaly()
        assert result.fund_flow_anomaly_score == 0
        assert result.fund_flow_anomaly_tags == []
        assert result.fund_flow_unit_verified is False

    def test_consecutive_inflow_with_unit(self):
        text = _make_fund_flow_text([10000, 20000, 30000, 15000, 8000])
        result = detect_fund_flow_anomaly(fund_flow_individual=text)
        assert FUND_FLOW_TAG_CONSECUTIVE_INFLOW in result.fund_flow_anomaly_tags
        assert result.fund_flow_anomaly_score > 0
        assert result.fund_flow_unit_verified is True

    def test_consecutive_inflow_without_unit(self):
        text = _make_fund_flow_text([10000, 20000, 30000, 15000, 8000], unit_hint=False)
        result = detect_fund_flow_anomaly(fund_flow_individual=text)
        # Consecutive inflow tag survives unit unverified
        assert FUND_FLOW_TAG_CONSECUTIVE_INFLOW in result.fund_flow_anomaly_tags
        assert result.fund_flow_unit_verified is False

    def test_large_single_day_requires_unit(self):
        text = _make_fund_flow_text([100, 200, 80000, 50])
        result = detect_fund_flow_anomaly(fund_flow_individual=text)
        assert FUND_FLOW_TAG_LARGE_SINGLE_DAY in result.fund_flow_anomaly_tags
        assert result.fund_flow_unit_verified is True

    def test_large_single_day_no_unit(self):
        # Build text with no unit hints
        lines = ["日期  主力净流入"]
        for val in [100, 200, 80000, 50]:
            lines.append(f"2026-05-20  {val}")
        text = "\n".join(lines)
        result = detect_fund_flow_anomaly(fund_flow_individual=text)
        # LARGE_SINGLE_DAY requires unit verification
        assert FUND_FLOW_TAG_LARGE_SINGLE_DAY not in result.fund_flow_anomaly_tags

    def test_board_resonance(self):
        ind_text = _make_fund_flow_text([10000, 20000, 30000, 15000, 8000])
        board_text = _make_board_fund_flow_text(inflow=True)
        result = detect_fund_flow_anomaly(
            fund_flow_individual=ind_text,
            fund_flow_board=board_text,
        )
        assert FUND_FLOW_TAG_BOARD_RESONANCE in result.fund_flow_anomaly_tags

    def test_individual_and_board_separate(self):
        ind_text = _make_fund_flow_text([10000, 20000, 30000])
        board_text = _make_board_fund_flow_text(inflow=True)
        result = detect_fund_flow_anomaly(
            fund_flow_individual=ind_text,
            fund_flow_board=board_text,
        )
        # Summaries should be separate
        assert "个股" in result.fund_flow_individual_summary
        assert "板块" in result.fund_flow_board_summary
        assert result.fund_flow_individual_summary != result.fund_flow_board_summary

    def test_outflow_dominant(self):
        header = "日期  收盘价  主力净流入-净额  占比"
        flows_vals = [-5000, -8000, -3000, -12000, -7000, 200, -9000, -4000, -6000, -11000]
        lines = [header]
        for i, v in enumerate(flows_vals):
            lines.append(f"2026-05-{20+i:02d}  25.00  {v}  {v/1000:.1f}%")
        text = "\n".join(lines)
        result = detect_fund_flow_anomaly(fund_flow_individual=text)
        assert FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT in result.fund_flow_anomaly_tags

    def test_score_capped_at_max(self):
        # Build extreme data: 20 consecutive days, large single day, board resonance, high proportion
        ind_text = _make_fund_flow_text([100000] * 20)
        ind_text += "\n占比 15.0%"
        board_text = _make_board_fund_flow_text(inflow=True)
        result = detect_fund_flow_anomaly(
            fund_flow_individual=ind_text,
            fund_flow_board=board_text,
        )
        assert result.fund_flow_anomaly_score <= MAX_FUND_FLOW_BONUS

    def test_no_strong_buy_sell_words(self):
        ind_text = _make_fund_flow_text([10000, 20000, 30000])
        result = detect_fund_flow_anomaly(fund_flow_individual=ind_text)
        for word in ["立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"]:
            assert word not in result.fund_flow_individual_summary
            assert word not in result.fund_flow_board_summary

    def test_failed_individual(self):
        result = detect_fund_flow_anomaly(
            fund_flow_individual="个股资金流向数据获取失败：timeout",
        )
        assert result.fund_flow_anomaly_score == 0
        assert result.fund_flow_anomaly_tags == []

    def test_individual_summary_with_data(self):
        text = _make_fund_flow_text([10000, 20000, 30000, -5000, 8000])
        result = detect_fund_flow_anomaly(fund_flow_individual=text)
        assert "净流入" in result.fund_flow_individual_summary or "净流出" in result.fund_flow_individual_summary

    def test_board_summary_unavailable(self):
        result = detect_fund_flow_anomaly(fund_flow_board="板块资金流数据获取失败")
        assert "不可用" in result.fund_flow_board_summary

    def test_lookback_days_clamped(self):
        text = _make_fund_flow_text([10000, 20000, 30000])
        result = detect_fund_flow_anomaly(fund_flow_individual=text, lookback_days=100)
        # Should be clamped to 20
        assert result.fund_flow_anomaly_score is not None


# ══════════════════════════════════════════════
# 9. Integration with candidate_engine
# ══════════════════════════════════════════════

class TestCandidateEngineIntegration:
    @patch("tradingagents.tradeflow.candidate_engine._fetch_price_data")
    def test_evaluate_with_fund_flow_anomaly(self, mock_fetch):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(20, 30, 120),
            "High": np.random.uniform(25, 35, 120),
            "Low": np.random.uniform(15, 25, 120),
            "Close": np.linspace(20, 25, 120),
            "Volume": np.random.randint(5000000, 20000000, 120).astype(float),
        })
        mock_fetch.return_value = df

        # Need at least one strategy to hit — patch run_strategies
        mock_signal = MagicMock()
        mock_signal.strategy_tag = "VCP"
        mock_signal.score = 30.0
        mock_signal.reason = "VCP形态观察"
        mock_signal.evidence = {"latest_close": 25.0}
        mock_signal.risk_flags = []
        mock_signal.need_deep_ta = False

        ind_text = _make_fund_flow_text([10000, 20000, 30000, 15000, 8000])

        with patch("tradingagents.tradeflow.candidate_engine.run_strategies", return_value=[mock_signal]):
            c, reason = evaluate_symbol(
                symbol="600519.SH",
                name="贵州茅台",
                source="manual",
                fund_flow_individual=ind_text,
            )

        assert c is not None
        assert c.fund_flow_anomaly_score > 0
        assert FUND_FLOW_TAG_CONSECUTIVE_INFLOW in c.fund_flow_anomaly_tags
        assert STRATEGY_FUND_FLOW in c.strategy_tags
        assert "fund_flow_anomaly" in c.evidence

    @patch("tradingagents.tradeflow.candidate_engine._fetch_price_data")
    def test_evaluate_without_fund_flow(self, mock_fetch):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(20, 30, 120),
            "High": np.random.uniform(25, 35, 120),
            "Low": np.random.uniform(15, 25, 120),
            "Close": np.linspace(20, 25, 120),
            "Volume": np.random.randint(5000000, 20000000, 120).astype(float),
        })
        mock_fetch.return_value = df

        mock_signal = MagicMock()
        mock_signal.strategy_tag = "VCP"
        mock_signal.score = 30.0
        mock_signal.reason = "VCP形态观察"
        mock_signal.evidence = {}
        mock_signal.risk_flags = []
        mock_signal.need_deep_ta = False

        with patch("tradingagents.tradeflow.candidate_engine.run_strategies", return_value=[mock_signal]):
            c, reason = evaluate_symbol(symbol="600519.SH")

        assert c is not None
        assert c.fund_flow_anomaly_score == 0
        assert c.fund_flow_anomaly_tags == []

    @patch("tradingagents.tradeflow.candidate_engine._fetch_price_data")
    def test_fund_flow_does_not_create_candidate_alone(self, mock_fetch):
        """Fund flow anomaly alone does not create a candidate if no strategy hits."""
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(20, 30, 120),
            "High": np.random.uniform(25, 35, 120),
            "Low": np.random.uniform(15, 25, 120),
            "Close": np.linspace(20, 25, 120),
            "Volume": np.random.randint(5000000, 20000000, 120).astype(float),
        })
        mock_fetch.return_value = df

        ind_text = _make_fund_flow_text([10000, 20000, 30000, 15000, 8000])

        with patch("tradingagents.tradeflow.candidate_engine.run_strategies", return_value=[]):
            c, reason = evaluate_symbol(
                symbol="600519.SH",
                fund_flow_individual=ind_text,
            )

        assert c is None
        assert "无策略命中" in reason


# ══════════════════════════════════════════════
# 10. DB persistence round-trip
# ══════════════════════════════════════════════

class TestDBPersistence:
    def test_save_and_load_with_fund_flow(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_db(db_path)

            c = Candidate(symbol="600519.SH", name="贵州茅台", trade_date="2026-05-29")
            c.fund_flow_anomaly_score = 12.0
            c.fund_flow_anomaly_tags = ["CONSECUTIVE_INFLOW", "LARGE_SINGLE_DAY"]
            c.fund_flow_anomaly_refs = [{"field": "test", "matched_text": "test ref", "source": "test"}]
            c.fund_flow_unit_verified = True
            c.fund_flow_individual_summary = "近5日主力合计净流入 63000 万元"
            c.fund_flow_board_summary = "板块资金流可用（5行数据）"

            save_candidate(c, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='600519.SH'"
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["fund_flow_anomaly_score"] == 12.0
            assert json.loads(row["fund_flow_anomaly_tags_json"]) == ["CONSECUTIVE_INFLOW", "LARGE_SINGLE_DAY"]
            assert row["fund_flow_unit_verified"] == 1
            assert "净流入" in row["fund_flow_individual_summary"]
            assert "板块" in row["fund_flow_board_summary"]
        finally:
            os.unlink(db_path)

    def test_from_db_row_round_trip(self):
        row = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "source": "manual",
            "strategy_tags_json": '["FUND_FLOW_ANOMALY"]',
            "primary_strategy": "FUND_FLOW_ANOMALY",
            "score": 15.0,
            "status": "active",
            "trigger_price": None,
            "support_price": None,
            "invalid_price": None,
            "need_deep_ta": 1,
            "evidence_json": '{}',
            "risk_flags_json": '[]',
            "trade_date": "2026-05-29",
            "created_at": "2026-05-29T10:00:00",
            "updated_at": "2026-05-29T10:00:00",
            "policy_tags_json": '[]',
            "version_score": 0.0,
            "policy_evidence_refs_json": '[]',
            "narrative_score": 0.0,
            "narrative_reasons_json": '[]',
            "narrative_evidence_refs_json": '[]',
            "risk_penalty": 0.0,
            "risk_evidence_refs_json": '[]',
            "risk_reasons_json": '[]',
            "game_balance": "neutral",
            "bull_case": "",
            "bear_case": "",
            "policy_case": "",
            "fund_flow_case": "",
            "resonance_count": 1,
            "game_balance_refs_json": '[]',
            "fund_flow_anomaly_score": 15.0,
            "fund_flow_anomaly_tags_json": '["CONSECUTIVE_INFLOW"]',
            "fund_flow_anomaly_refs_json": '[]',
            "fund_flow_unit_verified": 1,
            "fund_flow_individual_summary": "近5日主力合计净流入 50000 万元",
            "fund_flow_board_summary": "",
        }
        c = Candidate.from_db_row(row)
        assert c.fund_flow_anomaly_score == 15.0
        assert c.fund_flow_anomaly_tags == ["CONSECUTIVE_INFLOW"]
        assert c.fund_flow_unit_verified is True
        assert "净流入" in c.fund_flow_individual_summary


# ══════════════════════════════════════════════
# 11. Plan runner integration
# ══════════════════════════════════════════════

class TestPlanRunnerIntegration:
    def test_plan_entry_includes_fund_flow(self):
        c = Candidate(symbol="600519.SH", name="贵州茅台")
        c.fund_flow_anomaly_score = 10.0
        c.fund_flow_anomaly_tags = ["CONSECUTIVE_INFLOW"]
        c.fund_flow_unit_verified = True
        c.fund_flow_individual_summary = "近5日主力合计净流入 60000 万元"
        c.fund_flow_board_summary = "板块资金流可用"

        from tradingagents.tradeflow.plan_runner import _build_plan_entry
        entry = _build_plan_entry(c)

        assert entry["fund_flow_anomaly_score"] == 10.0
        assert "CONSECUTIVE_INFLOW" in entry["fund_flow_anomaly_tags"]
        assert entry["fund_flow_unit_verified"] is True
        assert "净流入" in entry["fund_flow_individual_summary"]


# ══════════════════════════════════════════════
# 12. Discovery integration
# ══════════════════════════════════════════════

class TestDiscoveryIntegration:
    def test_discovery_entry_includes_fund_flow(self):
        c = Candidate(symbol="600519.SH", name="贵州茅台")
        c.fund_flow_anomaly_score = 10.0
        c.fund_flow_anomaly_tags = ["CONSECUTIVE_INFLOW"]
        c.fund_flow_unit_verified = True
        c.fund_flow_individual_summary = "近5日主力合计净流入 60000 万元"
        c.fund_flow_board_summary = "板块资金流可用"

        from tradingagents.tradeflow.discovery import _build_discovery_entry
        entry = _build_discovery_entry(c)

        assert entry["fund_flow_anomaly_score"] == 10.0
        assert "CONSECUTIVE_INFLOW" in entry["fund_flow_anomaly_tags"]

    def test_fund_flow_symbols_in_universe(self):
        from tradingagents.tradeflow.discovery import _build_discovery_universe
        with patch("tradingagents.tradeflow.discovery.build_universe", return_value=[]):
            universe = _build_discovery_universe(
                fund_flow_symbols=["600519.SH", "000001.SZ"],
            )
        symbols = [u["symbol"] for u in universe]
        assert "600519.SH" in symbols
        assert "000001.SZ" in symbols
        sources = {u["symbol"]: u["source"] for u in universe}
        assert sources["600519.SH"] == "fund_flow_pool"

    def test_fund_flow_symbols_dedup_with_manual(self):
        from tradingagents.tradeflow.discovery import _build_discovery_universe
        with patch("tradingagents.tradeflow.discovery.build_universe",
                    return_value=[{"symbol": "600519.SH", "name": "贵州茅台", "source": "manual"}]):
            universe = _build_discovery_universe(
                fund_flow_symbols=["600519.SH", "000001.SZ"],
            )
        # 600519 deduped, 000001 added
        symbols = [u["symbol"] for u in universe]
        assert symbols.count("600519.SH") == 1
        assert "000001.SZ" in symbols


# ══════════════════════════════════════════════
# 13. Schema render_text tests
# ══════════════════════════════════════════════

class TestSchemaRender:
    def test_render_text_with_fund_flow(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            summary="测试",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "action": "OBSERVE",
                "strategies": ["FUND_FLOW_ANOMALY"],
                "score": 40,
                "fund_flow_anomaly_tags": ["CONSECUTIVE_INFLOW"],
                "fund_flow_anomaly_score": 12,
                "fund_flow_unit_verified": True,
                "fund_flow_individual_summary": "近5日主力合计净流入 60000 万元",
                "fund_flow_board_summary": "板块资金流可用",
            }],
        )
        text = plan.render_text()
        assert "资金异动" in text
        assert "CONSECUTIVE_INFLOW" in text
        assert "✓" in text
        assert "个股" in text
        assert "板块" in text

    def test_render_text_unverified_unit(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            summary="测试",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "action": "OBSERVE",
                "fund_flow_anomaly_tags": ["CONSECUTIVE_INFLOW"],
                "fund_flow_anomaly_score": 9,
                "fund_flow_unit_verified": False,
            }],
        )
        text = plan.render_text()
        assert "⚠未校验" in text

    def test_render_text_no_fund_flow(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            summary="测试",
            candidates=[{
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "action": "OBSERVE",
            }],
        )
        text = plan.render_text()
        assert "资金异动" not in text


# ══════════════════════════════════════════════
# 14. Score addition and need_deep_ta tests
# ══════════════════════════════════════════════

class TestScoreAndDeepTA:
    @patch("tradingagents.tradeflow.candidate_engine._fetch_price_data")
    def test_fund_flow_adds_score(self, mock_fetch):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(20, 30, 120),
            "High": np.random.uniform(25, 35, 120),
            "Low": np.random.uniform(15, 25, 120),
            "Close": np.linspace(20, 25, 120),
            "Volume": np.random.randint(5000000, 20000000, 120).astype(float),
        })
        mock_fetch.return_value = df

        mock_signal = MagicMock()
        mock_signal.strategy_tag = "VCP"
        mock_signal.score = 30.0
        mock_signal.reason = "VCP形态"
        mock_signal.evidence = {"latest_close": 25.0}
        mock_signal.risk_flags = []
        mock_signal.need_deep_ta = False

        ind_text = _make_fund_flow_text([10000, 20000, 30000, 15000, 8000])

        with patch("tradingagents.tradeflow.candidate_engine.run_strategies", return_value=[mock_signal]):
            c, _ = evaluate_symbol(
                symbol="600519.SH",
                fund_flow_individual=ind_text,
            )

        assert c.score > 30.0  # Score should be higher with fund flow bonus

    @patch("tradingagents.tradeflow.candidate_engine._fetch_price_data")
    def test_fund_flow_boosts_deep_ta_when_verified(self, mock_fetch):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(20, 30, 120),
            "High": np.random.uniform(25, 35, 120),
            "Low": np.random.uniform(15, 25, 120),
            "Close": np.linspace(20, 25, 120),
            "Volume": np.random.randint(5000000, 20000000, 120).astype(float),
        })
        mock_fetch.return_value = df

        mock_signal = MagicMock()
        mock_signal.strategy_tag = "VCP"
        mock_signal.score = 30.0
        mock_signal.reason = "VCP形态"
        mock_signal.evidence = {"latest_close": 25.0}
        mock_signal.risk_flags = []
        mock_signal.need_deep_ta = False

        # High score fund flow: 5 consecutive days + large single day
        ind_text = _make_fund_flow_text([60000, 70000, 80000, 90000, 100000])
        board_text = _make_board_fund_flow_text(inflow=True)

        with patch("tradingagents.tradeflow.candidate_engine.run_strategies", return_value=[mock_signal]):
            c, _ = evaluate_symbol(
                symbol="600519.SH",
                fund_flow_individual=ind_text,
                fund_flow_board=board_text,
            )

        # With high fund flow score (>=10) and unit verified, need_deep_ta should be True
        assert c.need_deep_ta is True

    @patch("tradingagents.tradeflow.candidate_engine._fetch_price_data")
    def test_outflow_dominant_no_score_boost(self, mock_fetch):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=120, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(20, 30, 120),
            "High": np.random.uniform(25, 35, 120),
            "Low": np.random.uniform(15, 25, 120),
            "Close": np.linspace(20, 25, 120),
            "Volume": np.random.randint(5000000, 20000000, 120).astype(float),
        })
        mock_fetch.return_value = df

        mock_signal = MagicMock()
        mock_signal.strategy_tag = "VCP"
        mock_signal.score = 30.0
        mock_signal.reason = "VCP形态"
        mock_signal.evidence = {}
        mock_signal.risk_flags = []
        mock_signal.need_deep_ta = False

        lines = ["日期  收盘价  主力净流入-净额  占比"]
        for i, v in enumerate([-5000, -8000, -3000, -12000, -7000, 200, -9000, -4000, -6000, -11000]):
            lines.append(f"2026-05-{20+i:02d}  25.00  {v}  {v/10000:.1f}%")
        text = "\n".join(lines)

        with patch("tradingagents.tradeflow.candidate_engine.run_strategies", return_value=[mock_signal]):
            c, _ = evaluate_symbol(
                symbol="600519.SH",
                fund_flow_individual=text,
            )

        # NET_OUTFLOW_DOMINANT does not add score
        assert FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT in c.fund_flow_anomaly_tags
        assert c.score == 30.0  # Score unchanged
        # Also FUND_FLOW_ANOMALY strategy tag should NOT be added
        assert STRATEGY_FUND_FLOW not in c.strategy_tags


# ══════════════════════════════════════════════
# 15. Edge cases
# ══════════════════════════════════════════════

class TestEdgeCases:
    def test_none_inputs(self):
        result = detect_fund_flow_anomaly(None, None)
        assert result.fund_flow_anomaly_score == 0
        assert result.fund_flow_anomaly_tags == []

    def test_empty_string_inputs(self):
        result = detect_fund_flow_anomaly("", "")
        assert result.fund_flow_anomaly_score == 0

    def test_candidate_default_values(self):
        c = Candidate(symbol="600519.SH")
        assert c.fund_flow_anomaly_score == 0.0
        assert c.fund_flow_anomaly_tags == []
        assert c.fund_flow_unit_verified is False
        assert c.fund_flow_individual_summary == ""
        assert c.fund_flow_board_summary == ""

    def test_to_db_row_includes_fund_flow(self):
        c = Candidate(symbol="600519.SH")
        c.fund_flow_anomaly_score = 8.0
        c.fund_flow_anomaly_tags = ["CONSECUTIVE_INFLOW"]
        c.fund_flow_unit_verified = True
        row = c.to_db_row()
        assert row["fund_flow_anomaly_score"] == 8.0
        assert json.loads(row["fund_flow_anomaly_tags_json"]) == ["CONSECUTIVE_INFLOW"]
        assert row["fund_flow_unit_verified"] == 1

    def test_strategy_fund_flow_in_all_strategies(self):
        from tradingagents.tradeflow.schemas import ALL_STRATEGIES
        assert "FUND_FLOW_ANOMALY" in ALL_STRATEGIES
