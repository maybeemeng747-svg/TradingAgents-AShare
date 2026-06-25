# [HK-001] hk_market_boundary
"""Tests for Hong Kong stock boundary and lightweight quote-only mode.

Ensures HKEX tickers (e.g. 0700.HK, 9988.HK) are recognised as market HK /
currency HKD, that A-share-only data gates (fund flow / LHB / margin trading /
ratings) are skipped for HK, and that A-share classification does not regress.
"""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from tradingagents.agents.utils.context_utils import (
    build_market_context,
    infer_instrument_context,
    is_hk_symbol,
)
from tradingagents.agents.utils.readiness_score import (
    EvidenceStatus,
    build_data_blockers,
    infer_evidence_statuses,
)
from tradingagents.graph import data_collector as dc


# ── infer_instrument_context ──────────────────────────────────────────────


class TestHKInstrumentContext:
    def test_hk_symbol_classified_as_hk(self):
        ctx = infer_instrument_context("0700.HK")
        assert ctx["market_country"] == "HK"
        assert ctx["currency"] == "HKD"
        assert ctx["exchange"] == "HKEX"

    def test_hk_symbol_9988(self):
        ctx = infer_instrument_context("9988.HK")
        assert ctx["market_country"] == "HK"
        assert ctx["currency"] == "HKD"

    def test_hk_symbol_five_digit(self):
        ctx = infer_instrument_context("00700.HK")
        assert ctx["market_country"] == "HK"

    def test_hk_not_classified_as_us(self):
        # [HK-001] acceptance: 0700.HK must not be tagged US/USD.
        ctx = infer_instrument_context("0700.HK")
        assert ctx["market_country"] != "US"
        assert ctx["currency"] != "USD"

    def test_hk_not_classified_as_cn(self):
        ctx = infer_instrument_context("0700.HK")
        assert ctx["market_country"] != "CN"
        assert ctx["currency"] != "CNY"

    def test_lowercase_hk_suffix_normalised(self):
        ctx = infer_instrument_context("0700.hk")
        assert ctx["market_country"] == "HK"

    def test_is_hk_symbol_helper(self):
        assert is_hk_symbol("0700.HK") is True
        assert is_hk_symbol("9988.HK") is True
        assert is_hk_symbol("0700.hk") is True
        assert is_hk_symbol("000001.SH") is False
        assert is_hk_symbol("AAPL") is False
        assert is_hk_symbol("") is False


# ── A-share / US non-regression ───────────────────────────────────────────


class TestNonRegression:
    def test_cn_symbol_unchanged(self):
        ctx = infer_instrument_context("000001.SH")
        assert ctx["market_country"] == "CN"
        assert ctx["currency"] == "CNY"

    def test_us_symbol_unchanged(self):
        ctx = infer_instrument_context("AAPL")
        assert ctx["market_country"] == "US"
        assert ctx["currency"] == "USD"

    def test_cn_symbol_not_hk(self):
        assert is_hk_symbol("600519.SH") is False
        assert is_hk_symbol("300750.SZ") is False


# ── build_market_context ──────────────────────────────────────────────────


class TestHKMarketContext:
    def test_hk_market_context_uses_hk_timezone(self):
        now = datetime(2026, 6, 25, 14, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        mc = build_market_context("0700.HK", "2026-06-25", now=now)
        assert mc["market_country"] == "HK"
        assert mc["timezone"] == "Asia/Hong_Kong"
        assert mc["exchange"] == "HKEX"

    def test_hk_market_context_session_intraday(self):
        now = datetime(2026, 6, 25, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        mc = build_market_context("0700.HK", "2026-06-25", now=now)
        assert mc["market_session"] == "in_session"
        assert mc["market_is_open"] is True

    def test_hk_market_context_lunch_break(self):
        now = datetime(2026, 6, 25, 12, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        mc = build_market_context("0700.HK", "2026-06-25", now=now)
        assert mc["market_session"] == "lunch_break"

    def test_hk_market_context_weekend(self):
        # 2026-06-27 is a Saturday
        now = datetime(2026, 6, 27, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        mc = build_market_context("0700.HK", "2026-06-27", now=now)
        assert mc["market_session"] == "closed"
        assert "非交易日" in mc["session_note"]


# ── DataCollector gating ──────────────────────────────────────────────────


def _stub_fetch(*_args, **_kwargs):
    return "stub"


@pytest.fixture
def patched_collector():
    """Patch network-backed fetchers so _fetch_all runs offline."""
    targets = [
        "get_stock_data", "get_news", "get_global_news",
        "get_insider_transactions", "get_fundamentals", "get_balance_sheet",
        "get_cashflow", "get_income_statement",
    ]
    with patch.multiple(dc, **{name: _stub_fetch for name in targets}):
        yield


class TestHKDataCollectorGating:
    def test_hk_skips_ashare_only_fields(self, patched_collector):
        results = dc._fetch_all("0700.HK", "2026-06-24")
        # HK light-mode flag set
        assert results.get("_hk_light_mode") is True
        assert results.get("_lhb_query_mode") == "skipped_hk"
        # A-share-only fields are explicit NOT_AVAILABLE structured entries
        for key in (
            "fund_flow_board", "fund_flow_individual", "lhb",
            "zt_pool", "hot_stocks", "announcements",
            "margin_trading", "ratings",
        ):
            assert key in results
            entry = results[key]
            assert isinstance(entry, dict)
            assert entry["status"] == "NOT_AVAILABLE"
            assert "HK market not supported" in entry["reason"]

    def test_hk_keeps_yfinance_supported_fields(self, patched_collector):
        results = dc._fetch_all("0700.HK", "2026-06-24")
        # yfinance-backed universal fields remain (value is the stub string)
        for key in ("stock_data", "news", "global_news", "insider_transactions"):
            assert key in results

    def test_cn_does_not_set_hk_light_mode(self, patched_collector):
        results = dc._fetch_all("000001.SH", "2026-06-24")
        assert "_hk_light_mode" not in results
        assert results.get("_lhb_query_mode") != "skipped_hk"

    def test_cn_still_attempts_ashare_fields(self, patched_collector):
        results = dc._fetch_all("600519.SH", "2026-06-24")
        # CN path must still schedule A-share-only fields (not NOT_AVAILABLE markers)
        assert isinstance(results["fund_flow_individual"], str)
        assert results["fund_flow_individual"] != ""
        assert isinstance(results["lhb"], str)
        assert isinstance(results["margin_trading"], str)


class TestInferSourceStatusHonorsExplicit:
    def test_not_available_dict_honored(self):
        assert dc.DataCollector._infer_source_status(
            {"status": "NOT_AVAILABLE", "raw": ""}
        ) == "NOT_AVAILABLE"

    def test_skipped_dict_honored(self):
        assert dc.DataCollector._infer_source_status(
            {"status": "SKIPPED", "raw": ""}
        ) == "SKIPPED"

    def test_plain_dict_legacy_unchanged(self):
        # dicts without an explicit status keep legacy HAS_DATA behaviour
        assert dc.DataCollector._infer_source_status({"foo": "bar"}) == "HAS_DATA"


# ── Readiness flow: HK skipped fields are low-severity ────────────────────


class TestHKReadinessFlow:
    def test_hk_skipped_fields_are_low_severity(self, patched_collector):
        collector = dc.DataCollector()
        collector.collect("0700.HK", "2026-06-24")
        raw_ev = collector.build_raw_evidence("0700.HK", "2026-06-24")

        statuses = infer_evidence_statuses({}, raw_evidence=raw_ev)
        # A-share-only gates are SKIPPED, not NOT_QUERIED / QUERY_FAILED
        assert statuses["individual_fund_flow"] == EvidenceStatus.SKIPPED
        assert statuses["lhb_status"] == EvidenceStatus.SKIPPED
        assert statuses["margin_trading"] == EvidenceStatus.SKIPPED
        assert statuses["ratings"] == EvidenceStatus.SKIPPED

        blockers = build_data_blockers({}, raw_evidence=raw_ev)
        by_key = {b["key"]: b for b in blockers}
        for key in ("individual_fund_flow", "lhb_status", "margin_trading", "ratings"):
            assert key in by_key
            assert by_key[key]["severity"] == "low"
            assert by_key[key]["status"] == "skipped"
