"""Tests for DATA-P0-603629: TA A股关键数据源补强与假可用修复.

Covers:
1. Fund flow Eastmoney push2his fallback in cn_astock_provider
2. LHB decoupled from fund flow - force query on anomaly conditions
3. Realtime quote enrichment (turnover_rate, volume_ratio, limit_up/down)
4. Announcement integration in data_collector and raw_evidence
5. Adjustment/复权口径 annotation in K-line data
6. Data source availability uses raw_evidence status (not text existence)
7. Evidence coverage-based confidence downgrade
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from tradingagents.agents.utils.readiness_score import (
    assess_confidence,
    ConfidenceLevel,
    calculate_evidence_coverage,
    EvidenceStatus,
)
from tradingagents.graph.data_collector import _should_force_lhb, DataCollector


class TestFundFlowFallback:
    """Test cn_astock fund flow fallback via Eastmoney push2his."""

    def test_individual_fund_flow_success(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "klines": [
                    "2026-05-30,12345,5432,3210,8765,3580",
                    "2026-05-29,-8000,-2000,1500,-6000,-3500",
                ]
            }
        }
        with patch("requests.get", return_value=mock_response):
            result = provider.get_individual_fund_flow("603629.SH")
        assert "主力资金净流向" in result
        assert "12345" in result
        assert "Eastmoney push2his" in result

    def test_individual_fund_flow_failure(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("requests.get", side_effect=Exception("ConnectionError")):
            result = provider.get_individual_fund_flow("603629.SH")
        assert "获取失败" in result

    def test_individual_fund_flow_empty(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"klines": []}}
        with patch("requests.get", return_value=mock_response):
            result = provider.get_individual_fund_flow("603629.SH")
        assert "暂不可用" in result

    def test_board_fund_flow_success(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "diff": [
                    {"f12": "BK0428", "f14": "电子元件", "f3": "2.5", "f62": "12345678"},
                ]
            }
        }
        with patch("requests.get", return_value=mock_response):
            result = provider.get_board_fund_flow()
        assert "电子元件" in result
        assert "Eastmoney push2" in result

    def test_board_fund_flow_failure(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("requests.get", side_effect=Exception("ConnectionError")):
            result = provider.get_board_fund_flow()
        assert "获取失败" in result


class TestLHBFallback:
    """Test cn_astock LHB fallback via Eastmoney datacenter."""

    def test_lhb_not_queried_when_force_false(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        result = provider.get_lhb_detail("603629.SH", "2026-05-30", force=False)
        assert "LHB_NOT_QUERIED" in result

    def test_lhb_has_data_when_force_true(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_data = [
            {
                "EXPLANATION": "涨幅偏离",
                "BILLBOARD_NET_AMT": 50000000,
                "BUY_AMOUNT": 30000000,
                "SELL_AMOUNT": 20000000,
                "BUY_BROKER_NAME": "机构专用",
                "SELL_BROKER_NAME": "中信证券",
            }
        ]
        with patch(
            "tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter",
            return_value=mock_data,
        ):
            result = provider.get_lhb_detail("603629.SH", "2026-05-30", force=True)
        assert "LHB_HAS_DATA" in result
        assert "涨幅偏离" in result
        assert "Eastmoney datacenter" in result

    def test_lhb_normal_no_data(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch(
            "tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter",
            return_value=[],
        ):
            result = provider.get_lhb_detail("603629.SH", "2026-05-30", force=True)
        assert "LHB_NORMAL_NO_DATA" in result

    def test_lhb_failed(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch(
            "tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter",
            side_effect=Exception("timeout"),
        ):
            result = provider.get_lhb_detail("603629.SH", "2026-05-30", force=True)
        assert "LHB_FAILED" in result


class TestLHBForceConditions:
    """Test LHB force query conditions decoupled from fund flow."""

    def test_force_on_news_mentions_lhb(self):
        assert _should_force_lhb("某股登上龙虎榜", "") is True

    def test_force_on_severe_anomaly(self):
        assert _should_force_lhb("该股严重异常波动", "") is True

    def test_force_on_consecutive_limit_up(self):
        assert _should_force_lhb("连续3涨停", "") is True

    def test_force_on_consecutive_limit_up_chinese(self):
        assert _should_force_lhb("连续三涨停", "") is True

    def test_force_on_one_char_limit(self):
        assert _should_force_lhb("一字涨停", "") is True

    def test_force_on_deviation(self):
        assert _should_force_lhb("涨跌幅偏离", "") is True

    def test_no_force_on_normal_news(self):
        assert _should_force_lhb("公司发布季报", "") is False

    def test_no_force_on_empty(self):
        assert _should_force_lhb("", "") is False

    def test_force_on_volume_ratio_keyword(self):
        assert _should_force_lhb("量比超过5", "") is True


class TestRealtimeQuoteEnrichment:
    """Test that cn_astock realtime quotes include turnover_rate, volume_ratio, etc."""

    def test_realtime_includes_turnover_rate(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()

        # Tencent format: ~ separated fields, need >= 53 fields
        fields = [""] * 53
        fields[0] = "market"  # prefix
        fields[1] = "某股"  # name
        fields[3] = "10.50"  # price
        fields[4] = "10.00"  # last_close
        fields[5] = "10.20"  # open
        fields[31] = "0.50"  # change_amt
        fields[32] = "5.00"  # change_pct
        fields[33] = "10.80"  # high
        fields[34] = "9.90"  # low
        fields[37] = "35000"  # amount_wan
        fields[38] = "2.5"  # turnover_pct
        fields[39] = "20"  # pe_ttm
        fields[43] = "5.0"  # amplitude_pct
        fields[44] = "50.0"  # mcap_yi
        fields[45] = "45.0"  # float_mcap_yi
        fields[46] = "3.5"  # pb
        fields[47] = "11.00"  # limit_up
        fields[48] = "9.00"  # limit_down
        fields[49] = "1.8"  # vol_ratio
        fields[52] = "25.0"  # pe_static
        tencent_data = f'v_sh603629="{ "~".join(fields) }";'

        with patch.object(provider, "_tencent_quote") as mock_quote:
            mock_quote.return_value = {
                "603629": {
                    "name": "某股", "price": 10.50, "last_close": 10.00,
                    "open": 10.20, "change_amt": 0.50, "change_pct": 5.0,
                    "high": 10.80, "low": 9.90, "amount_wan": 35000,
                    "turnover_pct": 2.5, "pe_ttm": 20, "amplitude_pct": 5.0,
                    "mcap_yi": 50.0, "float_mcap_yi": 45.0, "pb": 3.5,
                    "limit_up": 11.0, "limit_down": 9.0, "vol_ratio": 1.8,
                    "pe_static": 25.0,
                }
            }
            result_json = provider.get_realtime_quotes(["603629.SH"])
        data = json.loads(result_json)
        assert "603629.SH" in data
        q = data["603629.SH"]
        assert "turnover_rate" in q
        assert "volume_ratio" in q
        assert "limit_up" in q
        assert "limit_down" in q
        assert "pe_ttm" in q
        assert "pb" in q
        assert "market_cap" in q
        assert q["turnover_rate"] == 2.5
        assert q["volume_ratio"] == 1.8


class TestAdjustmentAnnotation:
    """Test that K-line data includes adjustment type annotation."""

    def test_em_kline_includes_adjustment(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "klines": [
                    "2026-05-30,10.00,10.50,10.80,9.90,50000",
                ]
            }
        }
        with patch("requests.get", return_value=mock_response):
            df = provider._fetch_kline_em("603629", "2026-05-01", "2026-05-30")
        assert not df.empty
        assert df.attrs.get("_adjustment") == "前复权"

    def test_stock_data_csv_includes_adjustment_header(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "klines": [
                    "2026-05-30,10.00,10.50,10.80,9.90,50000",
                ]
            }
        }
        with patch("requests.get", return_value=mock_response):
            csv_str = provider.get_stock_data("603629.SH", "2026-05-01", "2026-05-30")
        assert "adjustment=" in csv_str
        assert "[DATA-P0-603629]" in csv_str

    def test_akshare_stock_data_includes_adjustment(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        import pandas as pd
        provider = CnAkshareProvider()
        df = pd.DataFrame({
            "Date": ["2026-05-30"],
            "Open": [10.0], "High": [10.5], "Low": [9.9], "Close": [10.3],
            "Volume": [50000],
        })
        csv_str = provider._format_ak_hist(df, "603629.SH", "2026-05-01", "2026-05-30")
        assert "adjustment=" in csv_str
        assert "前复权" in csv_str


class TestAnnouncementIntegration:
    """Test announcement integration in data_collector and raw_evidence."""

    def test_announcements_in_data_source_keys(self):
        dc = DataCollector()
        pool = {
            "stock_data": "# data\nDate,Open\n2026-05-30,10",
            "news": "some news",
            "global_news": "global",
            "fund_flow_board": "board flow",
            "fund_flow_individual": "individual flow",
            "lhb": "lhb data",
            "fundamentals": "fundamentals",
            "balance_sheet": "bs",
            "cashflow": "cf",
            "income_statement": "is",
            "insider_transactions": "insider",
            "zt_pool": "zt",
            "hot_stocks": "hot",
            "indicators": {},
            "vpa_indicators": "vpa",
            "announcements": "- 2026-05-30 | 风险提示 | 关于股票交易异常波动的公告",
        }
        with patch.object(dc, "get", return_value=pool):
            evidence = dc.build_raw_evidence("603629.SH", "2026-05-30")
        assert "announcements" in evidence
        assert evidence["announcements"]["status"] == "HAS_DATA"

    def test_announcements_tool_registered(self):
        from tradingagents.dataflows.interface import TOOLS_CATEGORIES
        assert "get_announcements" in TOOLS_CATEGORIES["cn_market_data"]["tools"]


class TestDataSourceAvailabilityFix:
    """Test that data source availability uses raw_evidence status."""

    def test_fund_flow_failed_shows_failed_not_ok(self):
        dc = DataCollector()
        pool = {
            "stock_data": "# data\nDate,Open\n2026-05-30,10",
            "news": "news", "global_news": "global",
            "fund_flow_board": "board", "fund_flow_individual": "数据获取失败：ConnectionError",
            "lhb": "lhb", "fundamentals": "f", "balance_sheet": "b",
            "cashflow": "c", "income_statement": "i", "insider_transactions": "it",
            "zt_pool": "z", "hot_stocks": "h", "indicators": {}, "vpa_indicators": "v",
            "announcements": "",
        }
        with patch.object(dc, "get", return_value=pool):
            evidence = dc.build_raw_evidence("603629.SH", "2026-05-30")
        assert evidence["fund_flow_individual"]["status"] == "FAILED"

    def test_fund_flow_ok_shows_has_data(self):
        dc = DataCollector()
        pool = {
            "stock_data": "# data\nDate,Open\n2026-05-30,10",
            "news": "news", "global_news": "global",
            "fund_flow_board": "board",
            "fund_flow_individual": "603629.SH 近20日主力资金净流向：\n日期 | 主力 | 50000",
            "lhb": "lhb", "fundamentals": "f", "balance_sheet": "b",
            "cashflow": "c", "income_statement": "i", "insider_transactions": "it",
            "zt_pool": "z", "hot_stocks": "h", "indicators": {}, "vpa_indicators": "v",
            "announcements": "",
        }
        with patch.object(dc, "get", return_value=pool):
            evidence = dc.build_raw_evidence("603629.SH", "2026-05-30")
        assert evidence["fund_flow_individual"]["status"] == "HAS_DATA"


class TestEvidenceCoverageConfidenceDowngrade:
    """Test that low evidence_coverage prevents HIGH confidence."""

    def test_high_coverage_allows_high_confidence(self):
        result = assess_confidence(80, evidence_coverage=80)
        assert result == ConfidenceLevel.HIGH

    def test_low_evidence_coverage_caps_confidence(self):
        result = assess_confidence(90, evidence_coverage=42)
        assert result != ConfidenceLevel.HIGH

    def test_low_evidence_coverage_medium(self):
        result = assess_confidence(80, evidence_coverage=42)
        assert result == ConfidenceLevel.MEDIUM

    def test_very_low_all_caps_low(self):
        result = assess_confidence(30, evidence_coverage=30)
        assert result == ConfidenceLevel.LOW

    def test_no_evidence_coverage_no_cap(self):
        result = assess_confidence(80)
        assert result == ConfidenceLevel.HIGH

    def test_exactly_70_evidence_allows_high(self):
        result = assess_confidence(80, evidence_coverage=70)
        assert result == ConfidenceLevel.HIGH

    def test_69_evidence_caps_medium(self):
        result = assess_confidence(90, evidence_coverage=69)
        assert result != ConfidenceLevel.HIGH


class TestVendorTracking:
    """Test that vendor tracking works for fund flow and LHB methods."""

    def test_fund_flow_vendor_tracked_in_raw_evidence(self):
        dc = DataCollector()
        pool = {
            "stock_data": "# data\nDate,Open\n2026-05-30,10",
            "news": "news", "global_news": "global",
            "fund_flow_board": "board", "fund_flow_individual": "fund flow data",
            "lhb": "lhb", "fundamentals": "f", "balance_sheet": "b",
            "cashflow": "c", "income_statement": "i", "insider_transactions": "it",
            "zt_pool": "z", "hot_stocks": "h", "indicators": {}, "vpa_indicators": "v",
            "announcements": "",
        }
        with patch.object(dc, "get", return_value=pool), \
             patch("tradingagents.graph.data_collector.get_last_hit_vendor", return_value="cn_astock"):
            evidence = dc.build_raw_evidence("603629.SH", "2026-05-30")
        assert evidence["fund_flow_individual"]["vendor"] == "cn_astock"

    def test_lhb_vendor_tracked(self):
        dc = DataCollector()
        pool = {
            "stock_data": "# data\nDate,Open\n2026-05-30,10",
            "news": "news", "global_news": "global",
            "fund_flow_board": "board", "fund_flow_individual": "fund flow",
            "lhb": "LHB_HAS_DATA", "fundamentals": "f", "balance_sheet": "b",
            "cashflow": "c", "income_statement": "i", "insider_transactions": "it",
            "zt_pool": "z", "hot_stocks": "h", "indicators": {}, "vpa_indicators": "v",
            "announcements": "",
        }
        with patch.object(dc, "get", return_value=pool), \
             patch("tradingagents.graph.data_collector.get_last_hit_vendor", return_value="cn_astock"):
            evidence = dc.build_raw_evidence("603629.SH", "2026-05-30")
        assert evidence["lhb"]["vendor"] == "cn_astock"


class TestRawEvidenceAdjustmentField:
    """Test adjustment field in raw_evidence."""

    def test_adjustment_in_stock_data_evidence(self):
        dc = DataCollector()
        csv_with_adj = (
            "# Stock data for 603629.SH from 2026-05-01 to 2026-05-30\n"
            "# [DATA-P0-603629] adjustment=前复权\n"
            "Date,Open,High,Low,Close,Volume\n"
            "2026-05-30,10.0,10.5,9.9,10.3,50000\n"
        )
        pool = {
            "stock_data": csv_with_adj,
            "news": "news", "global_news": "global",
            "fund_flow_board": "board", "fund_flow_individual": "fund",
            "lhb": "lhb", "fundamentals": "f", "balance_sheet": "b",
            "cashflow": "c", "income_statement": "i", "insider_transactions": "it",
            "zt_pool": "z", "hot_stocks": "h", "indicators": {}, "vpa_indicators": "v",
            "announcements": "",
        }
        with patch.object(dc, "get", return_value=pool):
            evidence = dc.build_raw_evidence("603629.SH", "2026-05-30")
        assert evidence["stock_data"].get("adjustment") == "前复权"


class TestHealthCheckCnMarketData:
    """Test health check now includes cn_market_data endpoints."""

    def test_default_endpoints_include_fund_flow(self):
        from tradingagents.dataflows.health_check import _default_endpoints
        endpoints = _default_endpoints()
        methods = [e["method"] for e in endpoints]
        assert "get_individual_fund_flow" in methods

    def test_default_endpoints_include_lhb(self):
        from tradingagents.dataflows.health_check import _default_endpoints
        endpoints = _default_endpoints()
        methods = [e["method"] for e in endpoints]
        assert "get_lhb_detail" in methods

    def test_default_endpoints_include_announcements(self):
        from tradingagents.dataflows.health_check import _default_endpoints
        endpoints = _default_endpoints()
        methods = [e["method"] for e in endpoints]
        assert "get_announcements" in methods


class TestLHBForceReasonTracking:
    """Test that LHB force reason is tracked in raw_evidence."""

    def test_lhb_force_reason_fund_flow_anomaly(self):
        dc = DataCollector()
        pool = {
            "stock_data": "# data\nDate,Open\n2026-05-30,10",
            "news": "news", "global_news": "global",
            "fund_flow_board": "board",
            "fund_flow_individual": "603629 近20日主力资金净流向：\n主力 80000万",
            "lhb": "LHB_HAS_DATA", "fundamentals": "f", "balance_sheet": "b",
            "cashflow": "c", "income_statement": "i", "insider_transactions": "it",
            "zt_pool": "z", "hot_stocks": "h", "indicators": {}, "vpa_indicators": "v",
            "announcements": "",
            "_lhb_query_mode": "forced",
            "_lhb_force_reason": "fund_flow_anomaly",
        }
        with patch.object(dc, "get", return_value=pool):
            evidence = dc.build_raw_evidence("603629.SH", "2026-05-30")
        assert evidence["lhb"]["force_reason"] == "fund_flow_anomaly"

    def test_lhb_force_reason_anomaly_condition(self):
        dc = DataCollector()
        pool = {
            "stock_data": "# data\nDate,Open\n2026-05-30,10",
            "news": "该股登上龙虎榜", "global_news": "global",
            "fund_flow_board": "board", "fund_flow_individual": "fund",
            "lhb": "LHB_HAS_DATA", "fundamentals": "f", "balance_sheet": "b",
            "cashflow": "c", "income_statement": "i", "insider_transactions": "it",
            "zt_pool": "z", "hot_stocks": "h", "indicators": {}, "vpa_indicators": "v",
            "announcements": "",
            "_lhb_query_mode": "forced",
            "_lhb_force_reason": "anomaly_condition",
        }
        with patch.object(dc, "get", return_value=pool):
            evidence = dc.build_raw_evidence("603629.SH", "2026-05-30")
        assert evidence["lhb"]["force_reason"] == "anomaly_condition"
