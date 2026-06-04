"""[DATA-P0-FUND-ROUTE] Tests for fund flow fallback truth and unit conversion.

Covers:
1. route_to_vendor failure-string detection → fallback to next vendor
2. get_last_hit_vendor records the actual successful vendor after fallback
3. cn_astock fund flow unit conversion: 元 → 万元
4. _verify_unit detects 万元 annotation from cn_astock output
5. Both vendors fail → RuntimeError
6. Acceptance criteria from TASKS.md
"""
import pytest
from unittest.mock import patch, MagicMock

from tradingagents.dataflows.interface import (
    route_to_vendor,
    get_last_hit_vendor,
    _last_hit_vendor,
    _is_failure_result,
    _FAILURE_RESULT_PATTERNS,
)
from tradingagents.tradeflow.fund_flow_anomaly import _verify_unit


# ── _is_failure_result ──────────────────────────────────────────

class TestIsFailureResult:

    @pytest.mark.parametrize("pattern_text", [
        "个股资金流向数据获取失败：ProxyError",
        "个股资金流向数据获取失败：ConnectionError: max retries",
        "数据获取失败：Max retries exceeded with url",
        "Unable to connect to proxy",
        "近期主力资金流向数据暂不可用。",
        "TimeoutError: timed out",
        "ConnectTimeout: connection timed out",
        "ReadTimeout: read timed out",
    ])
    def test_detects_failure_patterns(self, pattern_text):
        assert _is_failure_result(pattern_text) is True

    @pytest.mark.parametrize("ok_text", [
        "603629.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n2026-06-01 | 1234.56",
        "603629.SH 近20日主力资金净流向：\n日期 主力净流入",
        "板块资金流向排名（共15个板块，前10名）：\n排名 板块代码",
        "some normal data string with no errors",
    ])
    def test_normal_data_not_flagged(self, ok_text):
        assert _is_failure_result(ok_text) is False

    def test_non_string_not_flagged(self):
        assert _is_failure_result(None) is False
        assert _is_failure_result(42) is False
        assert _is_failure_result({"key": "value"}) is False

    def test_empty_string_not_flagged(self):
        assert _is_failure_result("") is False


# ── route_to_vendor fallback chain ──────────────────────────────

class TestRouteToVendorFallback:

    def setup_method(self):
        _last_hit_vendor.clear()

    def _make_mock_provider(self, return_value=None, side_effect=None):
        provider = MagicMock()
        provider.is_placeholder = False
        provider.get_individual_fund_flow = MagicMock(
            return_value=return_value, side_effect=side_effect
        )
        return provider

    def test_akshare_failure_string_triggers_astock_fallback(self):
        """When cn_akshare returns failure string, route continues to cn_astock."""
        astock_result = "603629.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\ndata"
        mock_akshare = self._make_mock_provider(
            return_value="个股资金流向数据获取失败：ProxyError"
        )
        mock_astock = self._make_mock_provider(return_value=astock_result)

        mock_reg = MagicMock()
        mock_reg.get = lambda name: {
            "cn_akshare": mock_akshare,
            "cn_astock": mock_astock,
        }.get(name)
        mock_reg.list_names = MagicMock(return_value=["cn_akshare", "cn_astock"])

        with patch("tradingagents.dataflows.interface._registry", mock_reg):
            with patch("tradingagents.dataflows.interface.get_vendor", return_value="cn_akshare"):
                result = route_to_vendor("get_individual_fund_flow", "603629.SH")

        assert result == astock_result
        assert get_last_hit_vendor("get_individual_fund_flow") == "cn_astock"

    def test_both_vendors_fail_raises_error(self):
        """When both vendors return failure strings, RuntimeError is raised."""
        mock_akshare = self._make_mock_provider(
            return_value="个股资金流向数据获取失败：ConnectionError"
        )
        mock_astock = self._make_mock_provider(
            return_value="个股资金流向数据获取失败（Eastmoney push2his）：TimeoutError: timeout"
        )

        mock_reg = MagicMock()
        mock_reg.get = lambda name: {
            "cn_akshare": mock_akshare,
            "cn_astock": mock_astock,
        }.get(name)
        mock_reg.list_names = MagicMock(return_value=["cn_akshare", "cn_astock"])

        with patch("tradingagents.dataflows.interface._registry", mock_reg):
            with patch("tradingagents.dataflows.interface.get_vendor", return_value="cn_akshare"):
                with pytest.raises(RuntimeError, match="No available vendor"):
                    route_to_vendor("get_individual_fund_flow", "603629.SH")

    def test_first_vendor_succeeds_no_fallback(self):
        """When first vendor returns valid data, no fallback occurs."""
        akshare_result = "603629.SH 近20日主力资金净流向：\ndata here"
        mock_akshare = self._make_mock_provider(return_value=akshare_result)
        mock_astock = self._make_mock_provider(return_value="should not be called")

        mock_reg = MagicMock()
        mock_reg.get = lambda name: {
            "cn_akshare": mock_akshare,
            "cn_astock": mock_astock,
        }.get(name)
        mock_reg.list_names = MagicMock(return_value=["cn_akshare", "cn_astock"])

        with patch("tradingagents.dataflows.interface._registry", mock_reg):
            with patch("tradingagents.dataflows.interface.get_vendor", return_value="cn_akshare"):
                result = route_to_vendor("get_individual_fund_flow", "603629.SH")

        assert result == akshare_result
        assert get_last_hit_vendor("get_individual_fund_flow") == "cn_akshare"
        mock_astock.get_individual_fund_flow.assert_not_called()

    def test_exception_triggers_fallback(self):
        """When provider raises exception, fallback continues."""
        astock_result = "603629.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\ndata"
        mock_akshare = self._make_mock_provider(side_effect=Exception("network error"))
        mock_astock = self._make_mock_provider(return_value=astock_result)

        mock_reg = MagicMock()
        mock_reg.get = lambda name: {
            "cn_akshare": mock_akshare,
            "cn_astock": mock_astock,
        }.get(name)
        mock_reg.list_names = MagicMock(return_value=["cn_akshare", "cn_astock"])

        with patch("tradingagents.dataflows.interface._registry", mock_reg):
            with patch("tradingagents.dataflows.interface.get_vendor", return_value="cn_akshare"):
                result = route_to_vendor("get_individual_fund_flow", "603629.SH")

        assert result == astock_result
        assert get_last_hit_vendor("get_individual_fund_flow") == "cn_astock"

    def test_last_hit_vendor_records_actual_vendor_after_fallback(self):
        """get_last_hit_vendor must return the real vendor that succeeded."""
        astock_result = "603629.SH data here with enough length to be data"
        mock_akshare = self._make_mock_provider(
            return_value="数据获取失败：ProxyError"
        )
        mock_astock = self._make_mock_provider(return_value=astock_result)

        mock_reg = MagicMock()
        mock_reg.get = lambda name: {
            "cn_akshare": mock_akshare,
            "cn_astock": mock_astock,
        }.get(name)
        mock_reg.list_names = MagicMock(return_value=["cn_akshare", "cn_astock"])

        with patch("tradingagents.dataflows.interface._registry", mock_reg):
            with patch("tradingagents.dataflows.interface.get_vendor", return_value="cn_akshare"):
                route_to_vendor("get_individual_fund_flow", "603629.SH")

        assert get_last_hit_vendor("get_individual_fund_flow") == "cn_astock"


# ── cn_astock unit conversion ───────────────────────────────────

class TestCnAstockFundFlowUnitConversion:

    def test_yuan_to_wanyuan_conversion(self):
        """Eastmoney push2his returns 元-level values; output must be 万元."""
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider

        provider = CnAstockProvider()

        klines = [
            "2026-06-01,10000000,2000000,3000000,4000000,6000000",
            "2026-05-30,-5000000,-1000000,-1500000,-2000000,-3000000",
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "klines": klines,
            }
        }

        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get", return_value=mock_response):
            with patch("tradingagents.dataflows.providers.cn_astock_provider._rate_limit"):
                result = provider.get_individual_fund_flow("603629.SH")

        assert "单位：万元" in result
        assert "1000.00" in result
        assert "-500.00" in result
        assert "600.00" in result

    def test_unit_verified_detects_wanyuan(self):
        """_verify_unit must return True for cn_astock output with 万元 annotation."""
        text = (
            "603629.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n"
            "日期 | 主力净流入 | 小单净流入\n"
            "2026-06-01 | 1000.00 | 200.00"
        )
        assert _verify_unit(text) is True

    def test_unit_not_verified_for_raw_yuan(self):
        """Before fix, raw 元-level values without 万元 annotation would fail verification."""
        text = (
            "603629.SH 近20日主力资金净流向（Eastmoney push2his）：\n"
            "日期 | 主力净流入 | 小单净流入\n"
            "2026-06-01 | 10000000 | 2000000"
        )
        assert _verify_unit(text) is False

    def test_small_values_converted(self):
        """Small yuan values must still be converted, not confused with 万元."""
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider

        provider = CnAstockProvider()

        klines = [
            "2026-06-01,1000,200,300,400,600",
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "klines": klines,
            }
        }

        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get", return_value=mock_response):
            with patch("tradingagents.dataflows.providers.cn_astock_provider._rate_limit"):
                result = provider.get_individual_fund_flow("603629.SH")

        assert "0.10" in result
        assert "单位：万元" in result

    def test_empty_klines_returns_unavailable(self):
        """Empty klines returns failure string detected by router."""
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider

        provider = CnAstockProvider()

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"klines": []}}

        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get", return_value=mock_response):
            with patch("tradingagents.dataflows.providers.cn_astock_provider._rate_limit"):
                result = provider.get_individual_fund_flow("603629.SH")

        assert _is_failure_result(result) is True
        assert "暂不可用" in result


# ── Integration: fallback → provenance ──────────────────────────

class TestFundFlowFallbackProvenance:

    def setup_method(self):
        _last_hit_vendor.clear()

    def test_fallback_produces_has_data_status(self):
        """After AKShare fails and cn_astock succeeds, provenance should show HAS_DATA."""
        from tradingagents.agents.utils.readiness_score import build_fund_flow_provenance

        raw = {
            "fund_flow_individual": {
                "raw": (
                    "603629.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n"
                    "日期 | 主力净流入 | 小单净流入\n"
                    "2026-06-01 | 1234.56 | -567.89"
                ),
                "status": "HAS_DATA",
                "vendor": "cn_astock",
                "fallback_from": "cn_akshare",
                "unit": "万元",
                "unit_verified": True,
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "HAS_DATA"
        assert result["unit_verified"] is True
        assert result["strong_evidence_allowed"] is True

    def test_both_fail_produces_failed_status(self):
        """When both vendors fail, provenance should show FAILED."""
        from tradingagents.agents.utils.readiness_score import build_fund_flow_provenance

        raw = {
            "fund_flow_individual": {
                "raw": "个股资金流向数据获取失败：ConnectionError",
                "status": "FAILED",
                "vendor": "cn_akshare",
                "error": "ConnectionError",
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "FAILED"
        assert result["unit_verified"] is False
        assert result["strong_evidence_allowed"] is False


# ── Acceptance ──────────────────────────────────────────────────

class TestAcceptanceDataP0FundRoute:

    def setup_method(self):
        _last_hit_vendor.clear()

    def test_akshare_proxyerror_astock_fallback_succeeds(self):
        """mock AKShare returns ProxyError string, cn_astock returns valid data,
        final result comes from cn_astock."""
        astock_result = (
            "603629.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n"
            "日期 | 主力净流入 | 小单净流入 | 中单净流入 | 大单净流入 | 超大单净流入\n"
            "2026-06-01 | 1234.56 | -567.89 | 100.00 | 800.00 | 434.56"
        )

        mock_akshare = MagicMock()
        mock_akshare.is_placeholder = False
        mock_akshare.get_individual_fund_flow = MagicMock(
            return_value="个股资金流向数据获取失败：ProxyError"
        )
        mock_astock = MagicMock()
        mock_astock.is_placeholder = False
        mock_astock.get_individual_fund_flow = MagicMock(return_value=astock_result)

        mock_reg = MagicMock()
        mock_reg.get = lambda name: {
            "cn_akshare": mock_akshare,
            "cn_astock": mock_astock,
        }.get(name)
        mock_reg.list_names = MagicMock(return_value=["cn_akshare", "cn_astock"])

        with patch("tradingagents.dataflows.interface._registry", mock_reg):
            with patch("tradingagents.dataflows.interface.get_vendor", return_value="cn_akshare"):
                result = route_to_vendor("get_individual_fund_flow", "603629.SH")

        assert result == astock_result
        assert get_last_hit_vendor("get_individual_fund_flow") == "cn_astock"

    def test_unit_conversion_yuan_to_wanyuan(self):
        """Input: 元-level raw values from Eastmoney; output: 万元 with unit_verified=True."""
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider

        provider = CnAstockProvider()

        klines = ["2026-06-01,50000000,10000000,15000000,20000000,30000000"]

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"klines": klines}}

        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get", return_value=mock_response):
            with patch("tradingagents.dataflows.providers.cn_astock_provider._rate_limit"):
                result = provider.get_individual_fund_flow("603629.SH")

        assert "5000.00" in result
        assert "单位：万元" in result
        assert _verify_unit(result) is True

    def test_failure_string_not_treated_as_success(self):
        """Failure strings must be detected and not treated as successful hits."""
        failure_texts = [
            "个股资金流向数据获取失败：ProxyError",
            "个股资金流向数据获取失败（Eastmoney push2his）：TimeoutError: timeout",
            "603629.SH 近期主力资金流向数据暂不可用。",
        ]
        for text in failure_texts:
            assert _is_failure_result(text) is True, f"Should detect: {text[:50]}"

    def test_both_fail_status_is_failed(self):
        """When all vendors fail, no fake HAS_DATA."""
        from tradingagents.agents.utils.readiness_score import build_fund_flow_provenance

        raw = {
            "fund_flow_individual": {
                "raw": "个股资金流向数据获取失败：ConnectionError",
                "status": "FAILED",
            },
        }
        result = build_fund_flow_provenance(raw, {})
        assert result["individual_status"] == "FAILED"
        assert result["strong_evidence_allowed"] is False

    def test_no_forbidden_words_in_output(self):
        """cn_astock output must not contain strong trading words."""
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider

        provider = CnAstockProvider()

        klines = ["2026-06-01,10000000,2000000,3000000,4000000,6000000"]
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"klines": klines}}

        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get", return_value=mock_response):
            with patch("tradingagents.dataflows.providers.cn_astock_provider._rate_limit"):
                result = provider.get_individual_fund_flow("603629.SH")

        forbidden = ["买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓"]
        for word in forbidden:
            assert word not in result, f"Forbidden word '{word}' found in output"
