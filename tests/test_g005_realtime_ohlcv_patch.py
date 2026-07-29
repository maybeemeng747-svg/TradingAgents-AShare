"""Tests for G-005: realtime OHLCV patch for missing intraday data."""
import json
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import datetime


def _make_hist_df(dates, closes, volumes=None):
    rows = []
    for i, d in enumerate(dates):
        rows.append({
            "Date": pd.to_datetime(d),
            "Open": closes[i] - 0.1,
            "High": closes[i] + 0.2,
            "Low": closes[i] - 0.3,
            "Close": closes[i],
            "Volume": (volumes[i] if volumes else 100000 + i * 1000),
        })
    return pd.DataFrame(rows)


class TestG005RealtimeOHLCVPatch:
    """G-005: realtime OHLCV row patching via get_realtime_quotes/Sina."""

    def test_patch_applied_when_hist_missing_today(self):
        """When historical OHLCV lacks today's row, realtime quote is appended."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(
            ["2026-05-20", "2026-05-21", "2026-05-22", "2026-05-25"],
            [80.0, 81.0, 79.5, 80.17],
        )

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=True), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_market_phase", return_value="post_close"), \
             patch.object(provider, "_fetch_quotes_sina", return_value=json.dumps({
                 "600584.SH": {
                     "price": 88.19, "open": 85.0, "high": 89.0, "low": 84.5,
                     "previous_close": 80.17, "change": 8.02, "change_pct": 10.0,
                     "volume": 50000, "amount": 4400000,
                     "quote_time": "2026-05-26 15:00:00", "source": "sina",
                 }
             })):
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-26", assume_locked=True
            )

        assert len(result) == 5
        last_date = pd.to_datetime(result.iloc[-1]["Date"]).normalize()
        assert last_date == pd.to_datetime("2026-05-26")
        assert abs(result.iloc[-1]["Close"] - 88.19) < 0.01

        attrs = getattr(result, "attrs", {})
        assert attrs.get("_realtime_patched") is True
        patch_info = attrs.get("_realtime_patch_info", {})
        assert patch_info.get("is_realtime_patched") is True
        assert patch_info.get("source") == "sina"
        assert patch_info.get("quote_time") is not None

    def test_no_duplicate_when_hist_already_has_today(self):
        """When historical OHLCV already contains today, no duplicate is added."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(
            ["2026-05-25", "2026-05-26"],
            [80.17, 88.19],
        )

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=True), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_market_phase", return_value="post_close"):
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-26", assume_locked=True
            )

        assert len(result) == 2
        assert not getattr(result, "attrs", {}).get("_realtime_patched", False)

    def test_no_patch_for_historical_dates(self):
        """Historical/backtest dates should never be patched with realtime data."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(
            ["2026-05-20", "2026-05-21"],
            [80.0, 81.0],
        )

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"):
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-22", assume_locked=True
            )

        assert len(result) == 2
        assert not getattr(result, "attrs", {}).get("_realtime_patched", False)

    def test_failure_status_on_realtime_error(self):
        """When realtime quote fails, status is marked FAILED (not silent)."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(
            ["2026-05-25"],
            [80.17],
        )

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=True), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_market_phase", return_value="post_close"), \
             patch.object(provider, "_fetch_quotes_sina", return_value="{}"), \
             patch.object(provider, "_ak", side_effect=Exception("EM failed")):
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-26", assume_locked=True
            )

        assert len(result) == 1
        attrs = getattr(result, "attrs", {})
        assert attrs.get("_realtime_patch_status") in ("FAILED", "STALE")

    def test_eastmoney_fallback_reuses_existing_provider_lock(self):
        """The post-close fallback must not re-enter AKSHARE_CALL_LOCK."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        em_df = pd.DataFrame(
            [{
                "代码": "600584",
                "最新价": 88.19,
                "今开": 85.0,
                "最高": 89.0,
                "最低": 84.5,
                "成交量": 50000,
                "成交额": 4400000,
            }]
        )
        fake_ak = MagicMock()
        fake_ak.stock_zh_a_spot_em.return_value = em_df

        with patch.object(provider, "_fetch_quotes_sina", return_value="{}"), \
             patch.object(provider, "_ak", return_value=fake_ak), \
             patch(
                 "tradingagents.dataflows.providers.cn_akshare_provider.AKSHARE_CALL_LOCK"
             ) as lock:
            result, info = provider._fetch_realtime_ohlcv_from_quotes(
                "600584.SH",
                assume_locked=True,
            )

        lock.__enter__.assert_not_called()
        assert not result.empty
        assert info["status"] == "HAS_DATA"

    def test_format_header_includes_patch_info(self):
        """_format_ak_hist header includes realtime patch metadata."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        df = _make_hist_df(["2026-05-25"], [80.17])
        df.attrs["_realtime_patched"] = True
        df.attrs["_realtime_patch_info"] = {
            "source": "sina", "quote_time": "2026-05-26 15:00:00",
            "is_realtime_patched": True, "status": "HAS_DATA",
        }

        output = provider._format_ak_hist(df, "600584.SH", "2026-05-20", "2026-05-26")
        assert "is_realtime_patched=True" in output
        assert "source=sina" in output

    def test_no_patch_on_pre_open_phase(self):
        """No patching during pre_open phase (market hasn't opened yet)."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(["2026-05-25"], [80.17])

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=True), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_market_phase", return_value="pre_open"):
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-26", assume_locked=True
            )

        assert len(result) == 1
        assert not getattr(result, "attrs", {}).get("_realtime_patched", False)

    @pytest.mark.parametrize("phase", ["in_session", "lunch_break"])
    def test_no_daily_bar_patch_before_market_close(self, phase):
        """Partial intraday quotes must not become completed daily candles."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(["2026-05-25"], [80.17])

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=True), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_market_phase", return_value=phase), \
             patch.object(provider, "_fetch_realtime_ohlcv_from_quotes") as fetch_quote:
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-26", assume_locked=True
            )

        assert len(result) == 1
        assert result.attrs["_current_day_status"] == "PARTIAL_INTRADAY"
        assert result.attrs["_realtime_patch_status"] == "SKIPPED_NOT_FINAL"
        fetch_quote.assert_not_called()

    def test_provider_partial_today_row_is_removed_before_close(self):
        """Even a historical endpoint's current-day row is incomplete intraday."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(
            ["2026-05-25", "2026-05-26"],
            [80.17, 79.0],
            volumes=[150000, 5000],
        )

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=True), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_market_phase", return_value="in_session"):
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-26", assume_locked=True
            )

        assert len(result) == 1
        assert result.iloc[-1]["Date"] == pd.Timestamp("2026-05-25")
        assert result.attrs["_current_day_status"] == "PARTIAL_INTRADAY"

    def test_format_header_marks_partial_intraday_exclusion(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        df = _make_hist_df(["2026-05-25"], [80.17])
        df.attrs["_current_day_status"] = "PARTIAL_INTRADAY"

        output = provider._format_ak_hist(
            df, "600584.SH", "2026-05-20", "2026-05-26"
        )

        assert "current_day_status=PARTIAL_INTRADAY" in output
        assert "action=excluded_from_daily_ohlcv" in output

    def test_format_empty_intraday_range_preserves_partial_status(self):
        """A today-only query must explain why its sole partial bar was removed."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        df = _make_hist_df(["2026-05-26"], [79.0], volumes=[5000])

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=True), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_market_phase", return_value="in_session"):
            completed = provider._maybe_append_realtime_row(
                "600584.SH", df, "2026-05-26", assume_locked=True
            )

        assert completed.empty
        output = provider._format_ak_hist(
            completed, "600584.SH", "2026-05-26", "2026-05-26"
        )
        assert "current_day_status=PARTIAL_INTRADAY" in output
        assert "action=excluded_from_daily_ohlcv" in output
        assert "No data found for symbol '600584.SH'" in output

    def test_volume_unit_normalization(self):
        """Sina returns volume in shares; if ratio to hist median > 100, divide by 100."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(
            ["2026-05-25"],
            [80.17],
            volumes=[150000],
        )

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-26"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=True), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_market_phase", return_value="post_close"), \
             patch.object(provider, "_fetch_quotes_sina", return_value=json.dumps({
                 "600584.SH": {
                     "price": 88.19, "open": 85.0, "high": 89.0, "low": 84.5,
                     "previous_close": 80.17, "change": 8.02, "change_pct": 10.0,
                     "volume": 50000000, "amount": 4400000000,
                     "quote_time": "2026-05-26 15:00:00", "source": "sina",
                 }
             })):
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-26", assume_locked=True
            )

        assert len(result) == 2
        today_vol = result.iloc[-1]["Volume"]
        hist_median = hist_df["Volume"].median()
        assert today_vol < hist_median * 1000

    def test_no_patch_on_non_trading_day(self):
        """No patching on non-trading days (weekends/holidays)."""
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        hist_df = _make_hist_df(["2026-05-22"], [80.0])

        with patch("tradingagents.dataflows.providers.cn_akshare_provider.cn_today_str", return_value="2026-05-24"), \
             patch("tradingagents.dataflows.providers.cn_akshare_provider.is_cn_trading_day", return_value=False):
            result = provider._maybe_append_realtime_row(
                "600584.SH", hist_df, "2026-05-24", assume_locked=True
            )

        assert len(result) == 1
