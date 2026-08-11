"""[G-008] Valuation sanity check tests.

Covers:
1. check_valuation_mismatch — price deviation detection
2. risk_manager raw_evidence wiring — reads from state.metadata.raw_evidence
3. Old-price pollution blocking — stale valuation prices flagged
"""
import json
import pytest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tradingagents.agents.utils.readiness_score import check_valuation_mismatch
from tradingagents.agents.managers.risk_manager import (
    _completed_bar_can_supply_execution_price,
)


class TestValuationMismatch:

    def test_no_mismatch_when_no_valuation_price(self):
        result = check_valuation_mismatch(25.50, "普通基本面分析，无估值段落。")
        assert result["mismatch"] is False
        assert result["valuation_price"] is None

    def test_no_mismatch_when_close_prices(self):
        text = "假设股价约25.30元，对应PE为20倍，估值合理。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is False
        assert result["deviation_pct"] < 1

    def test_mismatch_when_old_price_pollution(self):
        text = "假设约15.00元，PE仅12倍，严重低估，建议买入。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is True
        assert result["valuation_price"] == 15.0
        assert result["deviation_pct"] > 20
        assert "估值口径错配" in result["note"]

    def test_missing_current_price_blocks_even_without_valuation_phrase(self):
        result = check_valuation_mismatch(
            None,
            "建议立即买入，目标价186元。",
        )
        assert result["mismatch"] is False
        assert result["price_unavailable"] is True
        assert "强动作必须降级" in result["note"]

    def test_missing_current_price_blocks_with_valuation_phrase(self):
        text = "假设股价约25.30元"
        result = check_valuation_mismatch(None, text)
        assert result["mismatch"] is False
        assert result["price_unavailable"] is True
        assert "强动作必须降级" in result["note"]

    def test_no_mismatch_when_empty_report(self):
        result = check_valuation_mismatch(25.50, "")
        assert result["mismatch"] is False

    def test_mismatch_with_estimated_price_pattern(self):
        text = "以约15.80元计算，公司市值约为50亿。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is True

    def test_no_mismatch_within_20pct_threshold(self):
        text = "假设约24.00元，估值合理。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is False

    def test_mismatch_exactly_at_20pct_boundary(self):
        text = "假设约20.40元"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is True
        assert result["deviation_pct"] == pytest.approx(20.0, abs=0.5)

    def test_valuation_section_price_extracted(self):
        text = "估值段：当前股价约12.00元，明显低于历史估值中枢30元。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is True
        assert result["valuation_price"] == 12.0


class TestRiskManagerRawEvidenceWiring:
    """Verify risk_manager reads raw_evidence from state.metadata.raw_evidence."""

    def test_metadata_path_takes_priority(self):
        import asyncio
        from unittest.mock import patch, MagicMock
        from tradingagents.agents.managers.risk_manager import create_risk_manager

        raw_stock_csv = (
            "date,open,high,low,close,volume\n"
            "2026-05-26,25.00,26.00,24.50,25.50,1000000"
        )
        metadata_raw = {
            "stock_data": {
                "raw": raw_stock_csv,
                "status": "HAS_DATA",
                "vendor": "akshare",
            }
        }
        refreshed_raw = {
            **metadata_raw,
            "realtime_quote": {
                "status": "HAS_DATA",
                "raw": '{"600584.SH":{"price":25.6}}',
                "refresh_stage": "final_risk_gate",
            },
        }

        captured_raw_evidence = {}
        captured_prompt = {}

        original_infer = __import__(
            "tradingagents.agents.utils.readiness_score",
            fromlist=["infer_evidence_statuses"],
        ).infer_evidence_statuses

        def mock_infer(reports, raw_evidence=None):
            captured_raw_evidence["value"] = raw_evidence
            return original_infer(reports, raw_evidence=raw_evidence)

        class _FakeLLM:
            async def astream(self, prompt):
                captured_prompt["value"] = prompt
                yield MagicMock(
                    content=(
                        "建议持有。\n"
                        "<!-- RISK_JUDGE: {"
                        "\"verdict\":\"pass\","
                        "\"hard_constraints\":[],"
                        "\"soft_constraints\":[],"
                        "\"execution_preconditions\":[],"
                        "\"de_risk_triggers\":[],"
                        "\"revision_reason\":\"\""
                        "} -->"
                    )
                )

        class _Memory:
            def get_memories(self, _situation, n_matches=2):
                return []

        state = {
            "company_of_interest": "600584.SH",
            "ticker": "600584.SH",
            "trade_date": "2026-05-26",
            "market_report": "技术面报告",
            "sentiment_report": "情绪面报告",
            "news_report": "新闻报告",
            "fundamentals_report": "基本面报告",
            "smart_money_report": "主力资金报告",
            "volume_price_report": "量价报告",
            "investment_plan": "方案",
            "trader_investment_plan": "交易计划",
            "user_context": {"objective": "短线", "current_position": 0},
            "metadata": {"raw_evidence": metadata_raw},
            "risk_feedback_state": {"retry_count": 0, "max_retries": 1},
            "risk_debate_state": {
                "history": "",
                "aggressive_history": "",
                "conservative_history": "",
                "neutral_history": "",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "count": 0,
                "claims": [],
                "focus_claim_ids": [],
                "open_claim_ids": [],
                "resolved_claim_ids": [],
                "unresolved_claim_ids": [],
                "round_summary": "",
                "round_goal": "",
                "claim_counter": 0,
            },
        }

        with patch(
            "tradingagents.agents.managers.risk_manager.infer_evidence_statuses",
            side_effect=mock_infer,
        ), patch(
            "tradingagents.agents.managers.risk_manager._refresh_intraday_quote_if_stale",
            return_value=refreshed_raw,
        ):
            node = create_risk_manager(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        assert captured_raw_evidence["value"] == refreshed_raw
        assert result["metadata"]["raw_evidence"] == refreshed_raw
        assert "最新价 25.60" in captured_prompt["value"]

    def test_fallback_to_state_root_raw_evidence(self):
        from tradingagents.agents.managers.risk_manager import create_risk_manager
        import asyncio
        from unittest.mock import patch, MagicMock

        raw_stock_csv = (
            "date,open,high,low,close,volume\n"
            "2026-05-26,25.00,26.00,24.50,25.50,1000000"
        )
        root_raw = {"stock_data": raw_stock_csv}
        captured = {}

        original_check = __import__(
            "tradingagents.agents.utils.readiness_score",
            fromlist=["check_valuation_mismatch"],
        ).check_valuation_mismatch

        def mock_check(current_price, report_text):
            captured["price"] = current_price
            return original_check(current_price, report_text)

        class _FakeLLM:
            async def astream(self, _prompt):
                yield MagicMock(
                    content=(
                        "建议持有。假设约25.30元\n"
                        "<!-- RISK_JUDGE: {"
                        "\"verdict\":\"pass\","
                        "\"hard_constraints\":[],"
                        "\"soft_constraints\":[],"
                        "\"execution_preconditions\":[],"
                        "\"de_risk_triggers\":[],"
                        "\"revision_reason\":\"\""
                        "} -->"
                    )
                )

        class _Memory:
            def get_memories(self, _situation, n_matches=2):
                return []

        state = {
            "company_of_interest": "600584.SH",
            "ticker": "600584.SH",
            "trade_date": "2026-05-26",
            "market_report": "报告",
            "sentiment_report": "情绪面报告",
            "news_report": "新闻报告",
            "fundamentals_report": "基本面报告",
            "smart_money_report": "主力报告",
            "volume_price_report": "量价报告",
            "investment_plan": "方案",
            "trader_investment_plan": "交易计划",
            "user_context": {"objective": "短线", "current_position": 0},
            "metadata": {},
            "raw_evidence": root_raw,
            "risk_feedback_state": {"retry_count": 0, "max_retries": 1},
            "risk_debate_state": {
                "history": "",
                "aggressive_history": "",
                "conservative_history": "",
                "neutral_history": "",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "count": 0,
                "claims": [],
                "focus_claim_ids": [],
                "open_claim_ids": [],
                "resolved_claim_ids": [],
                "unresolved_claim_ids": [],
                "round_summary": "",
                "round_goal": "",
                "claim_counter": 0,
            },
        }

        with patch(
            "tradingagents.agents.managers.risk_manager.check_valuation_mismatch",
            side_effect=mock_check,
        ):
            node = create_risk_manager(_FakeLLM(), _Memory())
            result = asyncio.run(node(state))

        assert captured["price"] == 25.50


class TestCompletedBarExecutionFallback:
    def test_current_day_previous_close_is_not_current_execution_price(self):
        now = datetime(2026, 7, 29, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

        allowed = _completed_bar_can_supply_execution_price(
            {"date": "2026-07-28", "close": 166.43},
            "2026-07-29",
            symbol="002409.SZ",
            now=now,
        )

        assert allowed is False

    def test_historical_analysis_can_use_latest_completed_close(self):
        now = datetime(2026, 7, 29, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

        allowed = _completed_bar_can_supply_execution_price(
            {"date": "2026-05-26", "close": 25.50},
            "2026-05-26",
            symbol="002409.SZ",
            now=now,
        )

        assert allowed is True

    def test_current_day_completed_bar_can_be_used(self):
        now = datetime(2026, 7, 29, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

        allowed = _completed_bar_can_supply_execution_price(
            {"date": "2026-07-29", "close": 154.60},
            "2026-07-29",
            symbol="002409.SZ",
            now=now,
        )

        assert allowed is True

    def test_current_day_bar_is_not_execution_price_before_close(self):
        now = datetime(2026, 7, 29, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

        allowed = _completed_bar_can_supply_execution_price(
            {"date": "2026-07-29", "close": 154.60},
            "2026-07-29",
            symbol="002409.SZ",
            now=now,
        )

        assert allowed is False

    def test_us_previous_close_is_not_current_price_during_us_session(self):
        now = datetime(2026, 7, 30, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        allowed = _completed_bar_can_supply_execution_price(
            {"date": "2026-07-28", "close": 210.0},
            "2026-07-29",
            symbol="AAPL",
            now=now,
        )

        assert allowed is False

    def test_us_same_day_close_is_available_after_us_finality_cutoff(self):
        now = datetime(2026, 7, 30, 4, 10, tzinfo=ZoneInfo("Asia/Shanghai"))

        allowed = _completed_bar_can_supply_execution_price(
            {"date": "2026-07-29", "close": 212.0},
            "2026-07-29",
            symbol="AAPL",
            now=now,
        )

        assert allowed is True

    def test_closed_non_trading_day_rejects_previous_completed_close(self):
        now = datetime(2026, 8, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        allowed = _completed_bar_can_supply_execution_price(
            {"date": "2026-07-31", "close": 154.60},
            "2026-08-01",
            symbol="002409.SZ",
            now=now,
        )

        assert allowed is False


def test_final_risk_gate_refreshes_stale_quote_after_same_day_close():
    from tradingagents.agents.managers.risk_manager import (
        _refresh_intraday_quote_if_stale,
    )

    raw = {
        "realtime_quote": {
            "status": "HAS_DATA",
            "fetched_at": "2026-07-29T14:51:00+08:00",
            "raw": json.dumps({
                "002409.SZ": {
                    "price": 154.6,
                    "quote_time": "2026-07-29 14:50:00",
                }
            }),
        }
    }
    closing_payload = json.dumps({
        "002409.SZ": {
            "price": 155.8,
            "quote_time": "2026-07-29 15:00:03",
        }
    })
    now = datetime(2026, 7, 29, 15, 20, tzinfo=ZoneInfo("Asia/Shanghai"))

    with patch(
        "tradingagents.agents.managers.risk_manager.get_realtime_quotes"
    ) as quote_tool:
        quote_tool.invoke.return_value = closing_payload
        refreshed = _refresh_intraday_quote_if_stale(
            raw, "002409.SZ", "2026-07-29", now=now
        )

    quote_tool.invoke.assert_called_once_with({"symbols": ["002409.SZ"]})
    assert refreshed["realtime_quote"]["refresh_stage"] == "final_risk_gate"


def test_missing_valuation_price_does_not_suppress_existing_position_exit():
    from tradingagents.agents.utils.readiness_score import (
        sanitize_forbidden_strong_actions,
    )

    text = "风险条件已触发，建议立即清仓止损。假设股价约25.30元。"
    gate = {
        "passed": False,
        "failures": ["估值基准价不可用(valuation_price_unavailable)"],
    }

    result, changes = sanitize_forbidden_strong_actions(
        text, gate, "has_position", buy_level=0, risk_level=4,
    )

    assert "立即清仓止损" in result
    assert changes == []


class TestOldPricePollution:

    def test_stale_valuation_price_flagged(self):
        text = (
            "基本面分析：公司业绩稳健增长。\n"
            "估值分析：假设约10.00元，对应PE仅8倍，严重低估，建议买入。\n"
            "当前股价25.50元已充分反映基本面。"
        )
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is True
        assert "旧价污染" in result["note"] or "估值口径错配" in result["note"]

    def test_recent_valuation_price_not_flagged(self):
        text = "假设约25.00元，对应PE为20倍，估值合理。"
        result = check_valuation_mismatch(25.50, text)
        assert result["mismatch"] is False
