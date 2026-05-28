"""Tests for N-003: raw_evidence vendor reflects actual routed provider."""
import pytest
from unittest.mock import patch, MagicMock

from tradingagents.dataflows.interface import (
    route_to_vendor,
    get_last_hit_vendor,
    _last_hit_vendor,
)
from tradingagents.graph.data_collector import DataCollector


def _make_pool(**overrides):
    pool = {
        "stock_data": "# Stock data for 600584.SH\nDate,Open,High,Low,Close,Volume\n2026-05-25,80.0,81.0,79.5,80.17,150000",
        "news": "Some news data",
        "global_news": "Global news content",
        "fund_flow_board": "Board fund flow data",
        "fund_flow_individual": "Individual fund flow data",
        "lhb": "LHB data",
        "fundamentals": "Fundamentals data",
        "balance_sheet": "Balance sheet data",
        "cashflow": "Cash flow data",
        "income_statement": "Income statement data",
        "insider_transactions": "Insider data",
        "zt_pool": "ZT pool data",
        "hot_stocks": "Hot stocks data",
        "indicators": {"rsi": 65.0},
        "vpa_indicators": "VPA data",
    }
    pool.update(overrides)
    return pool


class TestGetLastHitVendor:

    def setup_method(self):
        _last_hit_vendor.clear()

    def test_returns_empty_when_no_hits(self):
        assert get_last_hit_vendor("get_stock_data") == ""

    def test_returns_vendor_after_hit(self):
        _last_hit_vendor["get_stock_data"] = "cn_akshare"
        assert get_last_hit_vendor("get_stock_data") == "cn_akshare"

    def test_returns_cn_astock_after_hit(self):
        _last_hit_vendor["get_stock_data"] = "cn_astock"
        assert get_last_hit_vendor("get_stock_data") == "cn_astock"


class TestRawEvidenceVendorTracking:

    def setup_method(self):
        _last_hit_vendor.clear()

    def test_stock_data_vendor_cn_akshare(self):
        """When cn_akshare succeeds, vendor is cn_akshare."""
        _last_hit_vendor["get_stock_data"] = "cn_akshare"
        collector = DataCollector()
        pool = _make_pool()
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert re_data["stock_data"]["vendor"] == "cn_akshare"

    def test_stock_data_vendor_cn_astock_fallback(self):
        """When cn_akshare fails and cn_astock succeeds, vendor is cn_astock."""
        _last_hit_vendor["get_stock_data"] = "cn_astock"
        collector = DataCollector()
        pool = _make_pool()
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert re_data["stock_data"]["vendor"] == "cn_astock"

    def test_stock_data_vendor_default_when_no_tracking(self):
        """When no vendor tracking info, falls back to akshare default."""
        _last_hit_vendor.pop("get_stock_data", None)
        collector = DataCollector()
        pool = _make_pool()
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert re_data["stock_data"]["vendor"] == "akshare"

    def test_realtime_patched_overrides_tracked_vendor(self):
        """G-005 realtime patch vendor takes precedence over tracked vendor."""
        _last_hit_vendor["get_stock_data"] = "cn_akshare"
        collector = DataCollector()
        patched_csv = (
            "# Stock data for 600584.SH\n"
            "# [G-005] is_realtime_patched=True, source=sina, quote_time=2026-05-26 15:00:00\n"
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-26,85.0,89.0,84.5,88.19,50000\n"
        )
        pool = _make_pool(stock_data=patched_csv)
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert re_data["stock_data"]["vendor"] == "sina"
        assert re_data["stock_data"]["is_realtime_patched"] is True

    def test_non_stock_data_keys_keep_default_vendor(self):
        """Keys other than stock_data still get default akshare vendor."""
        _last_hit_vendor["get_stock_data"] = "cn_astock"
        collector = DataCollector()
        pool = _make_pool()
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert re_data["stock_data"]["vendor"] == "cn_astock"
        assert re_data["news"]["vendor"] == "akshare"
        assert re_data["fundamentals"]["vendor"] == "akshare"

    def test_failed_stock_data_no_vendor_override(self):
        """Failed stock_data uses default vendor when no tracking available."""
        _last_hit_vendor.pop("get_stock_data", None)
        collector = DataCollector()
        pool = _make_pool(stock_data="数据获取失败：timeout")
        with patch("tradingagents.graph.data_collector._fetch_all", return_value=pool):
            collector.collect("600584.SH", "2026-05-26")

        re_data = collector.build_raw_evidence("600584.SH", "2026-05-26")
        assert re_data["stock_data"]["vendor"] == "akshare"
        assert re_data["stock_data"]["status"] == "FAILED"
