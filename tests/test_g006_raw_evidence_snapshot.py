"""Tests for G-006: raw evidence snapshot in report result_data."""
import pytest
from unittest.mock import patch
from tradingagents.graph.data_collector import DataCollector


def _make_pool(**overrides):
    pool = {
        "stock_data": "# Stock data for 600584.SH\nDate,Open,High,Low,Close,Volume\n2026-05-25,80.0,81.0,79.5,80.17,150000",
        "news": "Some news data for testing",
        "global_news": "Global news content",
        "fund_flow_board": "Board fund flow data",
        "fund_flow_individual": "Individual fund flow data",
        "lhb": "LHB data",
        "fundamentals": "Fundamental analysis data",
        "balance_sheet": "Balance sheet data",
        "cashflow": "Cash flow data",
        "income_statement": "Income statement data",
        "insider_transactions": "Insider transaction data",
        "zt_pool": "ZT pool data",
        "hot_stocks": "Hot stocks data",
        "indicators": {"rsi": 65.0, "macd": 0.5},
        "vpa_indicators": "VPA precomputed data",
    }
    pool.update(overrides)
    return pool


class TestG006RawEvidenceSnapshot:

    def test_stock_data_exists_in_raw_evidence(self):
        """result_data.metadata.raw_evidence.stock_data exists after report."""
        collector = DataCollector()
        pool = _make_pool()
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert "stock_data" in re_data
        entry = re_data["stock_data"]
        assert entry["status"] == "HAS_DATA"
        assert entry["raw"] is not None
        assert isinstance(entry["raw"], str)
        assert "80.17" in entry["raw"]

    def test_fund_flow_failed_status(self):
        """Fund flow failure is marked as FAILED."""
        collector = DataCollector()
        pool = _make_pool(fund_flow_individual="数据获取失败：timeout")
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert re_data["fund_flow_individual"]["status"] == "FAILED"
        assert re_data["fund_flow_individual"]["error"] is not None

    def test_lhb_not_queried_status(self):
        """LHB not triggered should show NOT_QUERIED when raw is None."""
        collector = DataCollector()
        pool = _make_pool(lhb=None)
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert re_data["lhb"]["status"] == "NOT_QUERIED"

    def test_all_required_keys_present(self):
        """All data source keys are present in raw_evidence."""
        collector = DataCollector()
        pool = _make_pool()
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        required_keys = [
            "stock_data", "news", "global_news",
            "fund_flow_board", "fund_flow_individual", "lhb",
            "fundamentals", "balance_sheet", "cashflow", "income_statement",
        ]
        for key in required_keys:
            assert key in re_data, f"Missing key: {key}"
            entry = re_data[key]
            assert "status" in entry, f"Missing 'status' in {key}"
            assert "vendor" in entry, f"Missing 'vendor' in {key}"
            assert "as_of" in entry, f"Missing 'as_of' in {key}"
            assert "fetched_at" in entry, f"Missing 'fetched_at' in {key}"
            assert "record_count" in entry, f"Missing 'record_count' in {key}"
            assert "is_realtime_patched" in entry, f"Missing 'is_realtime_patched' in {key}"
            assert "raw" in entry, f"Missing 'raw' in {key}"

    def test_realtime_patched_flag_in_stock_data(self):
        """Stock data with G-005 patch header has is_realtime_patched=True."""
        collector = DataCollector()
        patched_stock_data = (
            "# Stock data for 600584.SH\n"
            "# [G-005] is_realtime_patched=True, source=sina, quote_time=2026-05-26 15:00:00\n"
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-26,85.0,89.0,84.5,88.19,50000\n"
        )
        pool = _make_pool(stock_data=patched_stock_data)
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        sd = re_data["stock_data"]
        assert sd["is_realtime_patched"] is True
        assert sd["vendor"] == "sina"

    def test_dual_horizon_preserves_raw_evidence(self):
        """_build_horizon_result keeps raw_evidence from DataCollector."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        collector = DataCollector()
        pool = _make_pool()
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        class _FakeGraph:
            data_collector = collector

        graph = _FakeGraph()

        final_state = {
            "company_of_interest": "600584.SH",
            "trade_date": "2026-05-26",
            "metadata": {},
            "final_trade_decision": "HOLD",
            "investment_plan": "",
            "trader_investment_plan": "",
            "analyst_traces": [],
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "macro_report": "",
            "smart_money_report": "",
            "volume_price_report": "",
        }

        result = TradingAgentsGraph._build_horizon_result(graph, "short", final_state)
        assert "raw_evidence" in result["metadata"]
        re_data = result["metadata"]["raw_evidence"]
        assert "stock_data" in re_data
        assert re_data["stock_data"]["raw"] is not None

    def test_empty_pool_returns_empty(self):
        """build_raw_evidence returns empty dict when pool not found."""
        collector = DataCollector()
        result = collector.build_raw_evidence("000000.SH", "2026-01-01")
        assert result == {}

    def test_no_api_keys_in_output(self):
        """Ensure no API keys, cookies, or tokens in raw_evidence."""
        collector = DataCollector()
        pool = _make_pool(
            stock_data="# data header\nDate,Close\n2026-05-25,80.17",
        )
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        import json
        serialized = json.dumps(re_data).lower()
        for secret in ["sk-12345", "cookie=abc", "token=xyz", "password", "api_key", "secret_key"]:
            assert secret not in serialized

    def test_infer_evidence_statuses_with_g006_format(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses

        raw_evidence = {
            "stock_data": {
                "raw": "# Stock data for 600584.SH from 2026-01-01 to 2026-05-25\nDate,Close,Volume\n2026-05-25,80.17,150000",
                "status": "HAS_DATA",
                "vendor": "akshare",
            },
            "fund_flow_individual": {
                "raw": "数据获取失败：timeout",
                "status": "FAILED",
            },
            "lhb": {
                "raw": None,
                "status": "NOT_QUERIED",
            },
            "news": {
                "raw": "Important news headline about the stock",
                "status": "HAS_DATA",
            },
        }

        statuses = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert statuses["ohlcv_5d"] == "has_data"
        assert statuses["individual_fund_flow"] == "query_failed"
        assert statuses["lhb_status"] == "not_queried"

    def test_infer_evidence_statuses_backwards_compatible(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses

        raw_evidence_old = {
            "stock_data": "# Stock data for 600584.SH from 2026-01-01 to 2026-05-25\nDate,Close,Volume\n2026-05-25,80.17,150000",
            "fund_flow_individual": "flow data here",
            "lhb": "LHB data",
            "news": "News data content here",
        }

        statuses = infer_evidence_statuses({}, raw_evidence=raw_evidence_old)
        assert statuses["ohlcv_5d"] == "has_data"
        assert statuses["individual_fund_flow"] == "has_data"
