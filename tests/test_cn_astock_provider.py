"""Tests for CnAstockProvider — [N-002] cn_astock_fallback."""

import json
import os
import sys
from io import StringIO
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.providers.cn_astock_provider import (
    CnAstockProvider,
    _extract_code,
    _get_prefix,
)
from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
from tradingagents.dataflows.providers.registry import build_default_registry


class TestSymbolNormalization:
    def test_plain_code(self):
        assert _extract_code("002138") == "002138"

    def test_prefix_sz(self):
        assert _extract_code("SZ002138") == "002138"

    def test_suffix_sz(self):
        assert _extract_code("002138.SZ") == "002138"

    def test_lowercase_sh_prefix(self):
        assert _extract_code("sh600519") == "600519"

    def test_mixed_case(self):
        assert _extract_code("Sh600519") == "600519"

    def test_whitespace_stripped(self):
        assert _extract_code("  002138  ") == "002138"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            _extract_code("INVALID")

    def test_get_prefix_sh(self):
        assert _get_prefix("510300") == "sh"
        assert _get_prefix("588000") == "sh"
        assert _get_prefix("600519") == "sh"
        assert _get_prefix("900001") == "sh"

    def test_get_prefix_sz(self):
        assert _get_prefix("000001") == "sz"
        assert _get_prefix("002138") == "sz"
        assert _get_prefix("300001") == "sz"

    def test_get_prefix_bj(self):
        assert _get_prefix("430047") == "bj"
        assert _get_prefix("800001") == "bj"
        assert _get_prefix("920045") == "bj"

    def test_akshare_prefixes_support_920_series_bj_symbols(self):
        provider = CnAkshareProvider()

        assert provider._sina_symbol("920045.BJ") == "bj920045"
        assert provider._xq_symbol("920045.BJ") == "BJ920045"


class TestRegistryRegistration:
    def test_registered_in_default_registry(self):
        registry = build_default_registry()
        assert "cn_astock" in registry.list_names()

    def test_provider_name(self):
        provider = CnAstockProvider()
        assert provider.name == "cn_astock"

    def test_registry_returns_correct_provider(self):
        registry = build_default_registry()
        provider = registry.get("cn_astock")
        assert provider is not None
        assert isinstance(provider, CnAstockProvider)


class TestGetStockDataCSV:
    def _make_mock_df(self, n=3):
        return pd.DataFrame({
            "Date": pd.date_range("2026-01-02", periods=n, freq="B"),
            "Open": [10.0 + i for i in range(n)],
            "High": [10.5 + i for i in range(n)],
            "Low": [9.5 + i for i in range(n)],
            "Close": [10.2 + i for i in range(n)],
            "Volume": [1000000 + i * 100000 for i in range(n)],
        })

    def test_csv_has_required_columns(self):
        provider = CnAstockProvider()
        with patch.object(provider, "_fetch_hist_df", return_value=self._make_mock_df()):
            csv_str = provider.get_stock_data("002138", "2026-01-01", "2026-01-10")

        for col in ["Date", "Open", "High", "Low", "Close", "Volume"]:
            assert col in csv_str
        assert "# Stock data for 002138" in csv_str

    def test_csv_parseable_as_dataframe(self):
        provider = CnAstockProvider()
        with patch.object(provider, "_fetch_hist_df", return_value=self._make_mock_df()):
            csv_str = provider.get_stock_data("002138", "2026-01-01", "2026-01-10")

        df = pd.read_csv(StringIO(csv_str), comment="#")
        assert {"Date", "Open", "High", "Low", "Close", "Volume"}.issubset(df.columns)
        assert len(df) == 3

    def test_empty_data_returns_message(self):
        provider = CnAstockProvider()
        with patch.object(provider, "_fetch_hist_df", return_value=pd.DataFrame()):
            result = provider.get_stock_data("002138", "2026-01-01", "2026-01-10")
        assert "No data found" in result

    def test_fetch_failure_returns_error_string(self):
        provider = CnAstockProvider()
        with patch.object(
            provider, "_fetch_hist_df",
            side_effect=NotImplementedError("cn_astock failed: timeout"),
        ):
            result = provider.get_stock_data("002138", "2026-01-01", "2026-01-10")
        assert "cn_astock failed" in result
        assert isinstance(result, str)


class TestGetRealtimeQuotesJSON:
    def _mock_tencent_quote(self):
        return {
            "002138": {
                "name": "顺络电子",
                "price": 35.5,
                "last_close": 35.0,
                "open": 35.2,
                "change_amt": 0.5,
                "change_pct": 1.43,
                "high": 36.0,
                "low": 34.8,
                "amount_wan": 58000.0,
                "turnover_pct": 3.2,
                "pe_ttm": 25.5,
                "amplitude_pct": 3.43,
                "mcap_yi": 280.0,
                "float_mcap_yi": 250.0,
                "pb": 4.5,
                "limit_up": 38.5,
                "limit_down": 31.5,
                "vol_ratio": 1.2,
                "pe_static": 26.0,
                "quote_time": "2026-07-29 14:20:03",
            }
        }

    def test_tencent_payload_timestamp_is_normalized(self):
        fields = [""] * 53
        fields[1] = "顺络电子"
        fields[3] = "35.50"
        fields[4] = "35.00"
        fields[5] = "35.20"
        fields[30] = "20260729142003"
        fields[31] = "0.50"
        fields[32] = "1.43"
        fields[33] = "36.00"
        fields[34] = "34.80"
        fields[37] = "58000"
        fields[38] = "3.2"
        fields[39] = "25.5"
        fields[43] = "3.43"
        fields[44] = "280"
        fields[45] = "250"
        fields[46] = "4.5"
        fields[47] = "38.5"
        fields[48] = "31.5"
        fields[49] = "1.2"
        fields[52] = "26"
        payload = f'v_sz002138="{"~".join(fields)}";'
        response = MagicMock()
        response.read.return_value = payload.encode("gbk")
        opener = MagicMock()
        opener.open.return_value = response

        with patch(
            "tradingagents.dataflows.providers.cn_astock_provider.urllib.request.build_opener",
            return_value=opener,
        ):
            quote = CnAstockProvider()._tencent_quote(["002138"])["002138"]

        assert quote["quote_time"] == "2026-07-29 14:20:03"

    def test_returns_required_fields(self):
        provider = CnAstockProvider()
        with patch.object(provider, "_tencent_quote", return_value=self._mock_tencent_quote()):
            result_json = provider.get_realtime_quotes(["002138"])

        data = json.loads(result_json)
        assert "002138" in data
        q = data["002138"]
        for field in ["price", "open", "high", "low", "previous_close", "amount", "quote_time", "source"]:
            assert field in q, f"Missing field: {field}"

    def test_amount_is_turnover_not_volume(self):
        provider = CnAstockProvider()
        mock = self._mock_tencent_quote()
        mock["002138"]["amount_wan"] = 58000.0
        with patch.object(provider, "_tencent_quote", return_value=mock):
            data = json.loads(provider.get_realtime_quotes(["002138"]))

        amount = data["002138"]["amount"]
        assert amount > 10000, f"amount={amount} suspiciously low — might be volume mislabeled"

    def test_empty_symbols_returns_empty_json(self):
        provider = CnAstockProvider()
        result = provider.get_realtime_quotes([])
        assert json.loads(result) == {}

    def test_http_failure_graceful(self):
        provider = CnAstockProvider()
        with patch.object(provider, "_tencent_quote", side_effect=Exception("timeout")):
            result = provider.get_realtime_quotes(["002138"])
        data = json.loads(result)
        assert data == {}

    def test_multiple_symbols(self):
        provider = CnAstockProvider()
        mock = self._mock_tencent_quote()
        mock["600519"] = {
            "name": "贵州茅台", "price": 1800.0, "last_close": 1790.0,
            "open": 1795.0, "change_amt": 10.0, "change_pct": 0.56,
            "high": 1810.0, "low": 1790.0, "amount_wan": 650000.0,
            "turnover_pct": 0.5, "pe_ttm": 30.0, "amplitude_pct": 1.12,
            "mcap_yi": 22000.0, "float_mcap_yi": 22000.0, "pb": 10.0,
            "limit_up": 1969.0, "limit_down": 1611.0, "vol_ratio": 0.8,
            "pe_static": 31.0,
        }
        with patch.object(provider, "_tencent_quote", return_value=mock):
            data = json.loads(provider.get_realtime_quotes(["002138", "600519"]))

        assert len(data) == 2
        assert "002138" in data
        assert "600519" in data

    def test_symbol_normalization_in_quotes(self):
        provider = CnAstockProvider()
        with patch.object(provider, "_tencent_quote", return_value=self._mock_tencent_quote()):
            data = json.loads(provider.get_realtime_quotes(["002138.SZ"]))

        assert "002138.SZ" in data


class TestFailureDegradation:
    def test_get_stock_data_http_failure(self):
        provider = CnAstockProvider()
        with patch.object(
            provider, "_fetch_hist_df",
            side_effect=NotImplementedError("cn_astock failed: all sources failed"),
        ):
            result = provider.get_stock_data("002138", "2026-01-01", "2026-01-10")
        assert isinstance(result, str)
        assert "cn_astock failed" in result

    def test_get_realtime_quotes_http_failure(self):
        provider = CnAstockProvider()
        with patch.object(provider, "_tencent_quote", side_effect=ConnectionError("network error")):
            result = provider.get_realtime_quotes(["002138"])
        assert isinstance(result, str)
        assert json.loads(result) == {}

    def test_get_fundamentals_http_failure(self):
        provider = CnAstockProvider()
        with patch.object(provider, "_eastmoney_stock_info", side_effect=Exception("error")), \
             patch.object(provider, "_tencent_quote", side_effect=Exception("error")), \
             patch("tradingagents.dataflows.providers.cn_astock_provider._cninfo_profile", return_value={}):
            with pytest.raises(NotImplementedError):
                provider.get_fundamentals("002138")

    def test_get_news_http_failure(self):
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get",
                    side_effect=Exception("network error")):
            result = provider.get_news("002138", "2026-01-01", "2026-01-10")
        assert isinstance(result, str)
        assert "unavailable" in result.lower()


class TestAmountNotVolume:
    def test_amount_much_larger_than_zero(self):
        provider = CnAstockProvider()
        mock = {
            "002138": {
                "name": "顺络电子", "price": 35.5, "last_close": 35.0,
                "open": 35.2, "change_amt": 0.5, "change_pct": 1.43,
                "high": 36.0, "low": 34.8, "amount_wan": 58000.0,
                "turnover_pct": 3.2, "pe_ttm": 25.5, "amplitude_pct": 3.43,
                "mcap_yi": 280.0, "float_mcap_yi": 250.0, "pb": 4.5,
                "limit_up": 38.5, "limit_down": 31.5, "vol_ratio": 1.2,
                "pe_static": 26.0,
            }
        }
        with patch.object(provider, "_tencent_quote", return_value=mock):
            data = json.loads(provider.get_realtime_quotes(["002138"]))

        amount = data["002138"]["amount"]
        assert amount > 10000, "amount seems too low — might be volume mislabeled"


@pytest.mark.skipif(not os.environ.get("LIVE_SMOKE"), reason="live smoke")
class TestLiveSmoke:
    def test_live_get_stock_data(self):
        provider = CnAstockProvider()
        csv = provider.get_stock_data("002138", "2026-01-01", "2026-05-28")
        assert "Date" in csv
        assert "Close" in csv

    def test_live_get_realtime_quotes(self):
        provider = CnAstockProvider()
        result = provider.get_realtime_quotes(["002138"])
        data = json.loads(result)
        assert "002138" in data
        assert data["002138"]["price"] > 0
