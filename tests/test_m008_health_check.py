"""Tests for M-008: data source health check and fallback observability.

All tests mock the actual provider calls so that no live network requests
are made.  Live smoke tests are exercised separately via
``python scripts/run_health_check.py --dry-run`` when the user explicitly
opts in.
"""

import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.health_check import (
    EndpointCheck,
    HealthReport,
    STATUS_OK,
    STATUS_FAILED,
    STATUS_STALE,
    STATUS_NOT_QUERIED,
    DEFAULT_SAMPLE_SYMBOLS,
    _default_endpoints,
    _probe_endpoint,
    run_health_check,
    render_report,
    save_report,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _make_check(
    method="get_stock_data",
    category="core_stock_apis",
    symbol="600519.SH",
    status=STATUS_OK,
    vendor="cn_akshare",
    fallback_from="",
    latency_ms=100.0,
    error="",
    chain=None,
    record_count=50,
    is_stale=False,
    stale_reason="",
) -> EndpointCheck:
    return EndpointCheck(
        method=method,
        category=category,
        sample_symbol=symbol,
        status=status,
        vendor=vendor,
        fallback_from=fallback_from,
        latency_ms=latency_ms,
        error=error,
        chain=chain or ["cn_akshare", "cn_baostock", "yfinance"],
        record_count=record_count,
        is_stale=is_stale,
        stale_reason=stale_reason,
    )


def _mock_probe(**overrides):
    """Return a probe_fn that produces a predictable EndpointCheck."""

    def probe_fn(method, args, kwargs, symbol):
        defaults = dict(
            method=method,
            category="core_stock_apis",
            sample_symbol=symbol,
            status=STATUS_OK,
            vendor="cn_akshare",
            fallback_from="",
            latency_ms=50.0,
            error="",
            chain=["cn_akshare", "cn_baostock", "yfinance"],
            record_count=30,
        )
        defaults.update(overrides)
        return EndpointCheck(**defaults)

    return probe_fn


# ── EndpointCheck dataclass ────────────────────────────────────────────

class TestEndpointCheck:
    def test_to_dict_roundtrip(self):
        c = _make_check()
        d = c.to_dict()
        assert d["method"] == "get_stock_data"
        assert d["status"] == STATUS_OK
        assert d["vendor"] == "cn_akshare"
        assert d["latency_ms"] == 100.0
        assert d["record_count"] == 50

    def test_to_dict_failed(self):
        c = _make_check(status=STATUS_FAILED, error="TimeoutError: timed out")
        d = c.to_dict()
        assert d["status"] == STATUS_FAILED
        assert "TimeoutError" in d["error"]

    def test_to_dict_stale(self):
        c = _make_check(status=STATUS_STALE, is_stale=True, stale_reason="empty_response")
        d = c.to_dict()
        assert d["status"] == STATUS_STALE
        assert d["is_stale"] is True
        assert d["stale_reason"] == "empty_response"

    def test_default_status_is_not_queried(self):
        c = EndpointCheck(method="get_news", category="news_data", sample_symbol="000001.SZ")
        assert c.status == STATUS_NOT_QUERIED

    def test_chain_default_empty(self):
        c = EndpointCheck(method="x", category="y", sample_symbol="z")
        assert c.chain == []


# ── HealthReport dataclass ─────────────────────────────────────────────

class TestHealthReport:
    def test_to_dict_roundtrip(self):
        report = HealthReport(
            run_at="2026-05-29 14:00:00",
            date="2026-05-29",
            checks=[_make_check(), _make_check(method="get_news", category="news_data")],
            summary={"OK": 2, "FAILED": 0, "STALE": 0, "NOT_QUERIED": 0, "fallback_count": 0, "total": 2},
        )
        d = report.to_dict()
        assert d["date"] == "2026-05-29"
        assert len(d["checks"]) == 2
        assert d["summary"]["OK"] == 2

    def test_empty_report(self):
        report = HealthReport(run_at="2026-05-29 14:00:00", date="2026-05-29")
        assert report.to_dict()["checks"] == []
        assert report.to_dict()["summary"] == {}


# ── _default_endpoints ─────────────────────────────────────────────────

class TestDefaultEndpoints:
    def test_has_expected_methods(self):
        eps = _default_endpoints()
        methods = [e["method"] for e in eps]
        assert "get_stock_data" in methods
        assert "get_indicators" in methods
        assert "get_fundamentals" in methods
        assert "get_news" in methods
        assert "get_realtime_quotes" in methods

    def test_each_endpoint_has_args(self):
        for ep in _default_endpoints():
            assert "method" in ep
            assert "args" in ep
            assert isinstance(ep["args"], tuple)

    def test_symbol_placeholder(self):
        for ep in _default_endpoints():
            has_placeholder = any(a == "_SYMBOL_" for a in ep["args"])
            if ep["method"] == "get_realtime_quotes":
                assert any(
                    isinstance(a, list) and "_SYMBOL_" in a for a in ep["args"]
                )
            elif ep["method"] not in ("get_global_news",):
                assert has_placeholder or any(
                    "_SYMBOL_" in str(v) for v in ep.get("kwargs", {}).values()
                )


# ── _probe_endpoint (with mocked route_to_vendor) ──────────────────────

class TestProbeEndpoint:
    def test_ok_response(self):
        with patch("tradingagents.dataflows.health_check.route_to_vendor") as mock_route:
            mock_route.return_value = "date,open,high,low,close,volume\n2026-05-29,1,2,3,4,5\n2026-05-28,6,7,8,9,10\n"
            with patch("tradingagents.dataflows.health_check._resolve_vendor_chain", return_value=["cn_akshare", "cn_baostock"]):
                with patch("tradingagents.dataflows.health_check.get_vendor", return_value="cn_akshare"):
                    check = _probe_endpoint("get_stock_data", ("600519.SH", "20260429", "20260529"), {}, "600519.SH")
        assert check.status == STATUS_OK
        assert check.sample_symbol == "600519.SH"
        assert check.latency_ms > 0

    def test_failed_response(self):
        with patch("tradingagents.dataflows.health_check.route_to_vendor") as mock_route:
            mock_route.side_effect = RuntimeError("No available vendor for method 'get_stock_data'")
            with patch("tradingagents.dataflows.health_check._resolve_vendor_chain", return_value=["cn_akshare"]):
                with patch("tradingagents.dataflows.health_check.get_vendor", return_value="cn_akshare"):
                    check = _probe_endpoint("get_stock_data", ("600519.SH", "20260429", "20260529"), {}, "600519.SH")
        assert check.status == STATUS_FAILED
        assert "RuntimeError" in check.error

    def test_stale_empty_response(self):
        with patch("tradingagents.dataflows.health_check.route_to_vendor") as mock_route:
            mock_route.return_value = ""
            with patch("tradingagents.dataflows.health_check._resolve_vendor_chain", return_value=["cn_akshare"]):
                with patch("tradingagents.dataflows.health_check.get_vendor", return_value="cn_akshare"):
                    check = _probe_endpoint("get_stock_data", ("600519.SH", "20260429", "20260529"), {}, "600519.SH")
        assert check.status == STATUS_STALE
        assert check.is_stale is True

    def test_stale_minimal_response(self):
        with patch("tradingagents.dataflows.health_check.route_to_vendor") as mock_route:
            mock_route.return_value = "short"
            with patch("tradingagents.dataflows.health_check._resolve_vendor_chain", return_value=["cn_akshare"]):
                with patch("tradingagents.dataflows.health_check.get_vendor", return_value="cn_akshare"):
                    check = _probe_endpoint("get_stock_data", ("600519.SH", "20260429", "20260529"), {}, "600519.SH")
        assert check.status == STATUS_STALE

    def test_none_response_is_stale(self):
        with patch("tradingagents.dataflows.health_check.route_to_vendor") as mock_route:
            mock_route.return_value = None
            with patch("tradingagents.dataflows.health_check._resolve_vendor_chain", return_value=["cn_akshare"]):
                with patch("tradingagents.dataflows.health_check.get_vendor", return_value="cn_akshare"):
                    check = _probe_endpoint("get_stock_data", ("600519.SH", "20260429", "20260529"), {}, "600519.SH")
        assert check.status == STATUS_STALE


# ── run_health_check ───────────────────────────────────────────────────

class TestRunHealthCheck:
    def test_all_ok(self):
        report = run_health_check(
            symbols=["600519.SH"],
            probe_fn=_mock_probe(status=STATUS_OK, vendor="cn_akshare"),
        )
        assert report.summary[STATUS_OK] == report.summary["total"]
        assert report.summary[STATUS_FAILED] == 0
        assert report.summary["STALE"] == 0
        assert report.summary["fallback_count"] == 0

    def test_all_failed(self):
        report = run_health_check(
            symbols=["600519.SH"],
            probe_fn=_mock_probe(status=STATUS_FAILED, error="TimeoutError: timed out"),
        )
        assert report.summary[STATUS_FAILED] == report.summary["total"]
        assert report.summary[STATUS_OK] == 0

    def test_mixed_statuses(self):
        statuses = [STATUS_OK, STATUS_FAILED, STATUS_OK, STATUS_STALE, STATUS_OK, STATUS_OK, STATUS_OK, STATUS_OK, STATUS_OK, STATUS_OK, STATUS_OK]
        idx = [0]

        def probe_fn(method, args, kwargs, symbol):
            s = statuses[idx[0] % len(statuses)]
            idx[0] += 1
            return _make_check(status=s, method=method, error="err" if s == STATUS_FAILED else "")

        report = run_health_check(symbols=["600519.SH"], probe_fn=probe_fn)
        assert report.summary[STATUS_OK] == 9
        assert report.summary[STATUS_FAILED] == 1
        assert report.summary[STATUS_STALE] == 1

    def test_fallback_detected(self):
        def probe_fn(method, args, kwargs, symbol):
            return _make_check(
                method=method,
                status=STATUS_OK,
                vendor="cn_baostock",
                chain=["cn_akshare", "cn_baostock", "yfinance"],
            )

        report = run_health_check(symbols=["600519.SH"], probe_fn=probe_fn)
        assert report.summary["fallback_count"] == report.summary["total"]
        for c in report.checks:
            assert c.fallback_from == "cn_akshare"
            assert c.vendor == "cn_baostock"

    def test_no_fallback_when_first_vendor_succeeds(self):
        def probe_fn(method, args, kwargs, symbol):
            return _make_check(
                method=method,
                status=STATUS_OK,
                vendor="cn_akshare",
                chain=["cn_akshare", "cn_baostock"],
            )

        report = run_health_check(symbols=["600519.SH"], probe_fn=probe_fn)
        assert report.summary["fallback_count"] == 0
        for c in report.checks:
            assert c.fallback_from == ""

    def test_uses_first_sample_symbol(self):
        symbols_used = []

        def probe_fn(method, args, kwargs, symbol):
            symbols_used.append(symbol)
            return _make_check(method=method, symbol=symbol)

        run_health_check(symbols=["000001.SZ", "600519.SH"], probe_fn=probe_fn)
        assert all(s == "000001.SZ" for s in symbols_used)

    def test_default_symbols(self):
        assert DEFAULT_SAMPLE_SYMBOLS == ["600519.SH", "000001.SZ"]

    def test_report_has_date(self):
        report = run_health_check(probe_fn=_mock_probe())
        assert report.date == datetime.now().strftime("%Y-%m-%d")

    def test_report_has_run_at(self):
        report = run_health_check(probe_fn=_mock_probe())
        assert datetime.now().strftime("%Y-%m-%d") in report.run_at


# ── render_report ──────────────────────────────────────────────────────

class TestRenderReport:
    def test_contains_summary_table(self):
        report = run_health_check(probe_fn=_mock_probe())
        md = render_report(report)
        assert "## Summary" in md
        assert "| OK |" in md

    def test_contains_endpoint_details_table(self):
        report = run_health_check(probe_fn=_mock_probe())
        md = render_report(report)
        assert "## Endpoint Details" in md
        assert "get_stock_data" in md

    def test_shows_fallback_section_when_present(self):
        def probe_fn(method, args, kwargs, symbol):
            return _make_check(
                method=method,
                vendor="cn_baostock",
                chain=["cn_akshare", "cn_baostock"],
            )

        report = run_health_check(probe_fn=probe_fn)
        md = render_report(report)
        assert "## Fallback Events" in md
        assert "cn_akshare → cn_baostock" in md

    def test_no_fallback_section_when_absent(self):
        report = run_health_check(probe_fn=_mock_probe(vendor="cn_akshare"))
        md = render_report(report)
        assert "## Fallback Events" not in md

    def test_shows_failed_section(self):
        report = run_health_check(
            probe_fn=_mock_probe(status=STATUS_FAILED, error="TimeoutError: timed out after 30s"),
        )
        md = render_report(report)
        assert "## Failed Endpoints" in md
        assert "TimeoutError" in md

    def test_no_failed_section_when_all_ok(self):
        report = run_health_check(probe_fn=_mock_probe(status=STATUS_OK))
        md = render_report(report)
        assert "## Failed Endpoints" not in md

    def test_shows_stale_section(self):
        report = run_health_check(
            probe_fn=_mock_probe(status=STATUS_STALE, is_stale=True, stale_reason="empty_response"),
        )
        md = render_report(report)
        assert "## Stale Responses" in md

    def test_no_stale_section_when_all_ok(self):
        report = run_health_check(probe_fn=_mock_probe(status=STATUS_OK))
        md = render_report(report)
        assert "## Stale Responses" not in md

    def test_error_truncated_in_table(self):
        long_error = "X" * 100
        c = _make_check(status=STATUS_FAILED, error=long_error)
        report = HealthReport(
            run_at="2026-05-29 14:00:00",
            date="2026-05-29",
            checks=[c],
            summary={"OK": 0, "FAILED": 1, "STALE": 0, "NOT_QUERIED": 0, "fallback_count": 0, "total": 1},
        )
        md = render_report(report)
        assert "..." in md

    def test_m008_marker_in_footer(self):
        report = run_health_check(probe_fn=_mock_probe())
        md = render_report(report)
        assert "[M-008] data_source_health" in md


# ── save_report ────────────────────────────────────────────────────────

class TestSaveReport:
    def test_creates_directory_and_file(self):
        report = run_health_check(probe_fn=_mock_probe())
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = os.path.join(tmpdir, "health_output")
            path = save_report(report, output_dir=out_dir)
            assert os.path.isfile(path)
            assert report.date in path

    def test_file_content_is_markdown(self):
        report = run_health_check(probe_fn=_mock_probe())
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_report(report, output_dir=tmpdir)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert content.startswith("# Data Source Health Report")

    def test_overwrites_existing_file(self):
        report1 = run_health_check(probe_fn=_mock_probe(status=STATUS_OK))
        report2 = run_health_check(probe_fn=_mock_probe(status=STATUS_FAILED, error="test"))
        with tempfile.TemporaryDirectory() as tmpdir:
            path1 = save_report(report1, output_dir=tmpdir)
            path2 = save_report(report2, output_dir=tmpdir)
            assert path1 == path2
            with open(path2, encoding="utf-8") as f:
                content = f.read()
            assert "FAILED" in content


# ── Fallback observability integration ─────────────────────────────────

class TestFallbackObservability:
    def test_primary_fails_fallback_succeeds(self):
        """Mock: cn_akshare fails, cn_baostock succeeds → report shows fallback."""

        def probe_fn(method, args, kwargs, symbol):
            return _make_check(
                method=method,
                status=STATUS_OK,
                vendor="cn_baostock",
                chain=["cn_akshare", "cn_baostock", "yfinance"],
            )

        report = run_health_check(symbols=["600519.SH"], probe_fn=probe_fn)
        assert report.summary["fallback_count"] == report.summary["total"]
        for c in report.checks:
            assert c.fallback_from == "cn_akshare"
            assert c.vendor == "cn_baostock"

    def test_primary_fails_all_fail(self):
        """Mock: all vendors fail → status FAILED, no fallback."""

        def probe_fn(method, args, kwargs, symbol):
            return _make_check(
                method=method,
                status=STATUS_FAILED,
                vendor="",
                error="RuntimeError: No available vendor",
                chain=["cn_akshare", "cn_baostock", "yfinance"],
            )

        report = run_health_check(symbols=["600519.SH"], probe_fn=probe_fn)
        assert report.summary[STATUS_FAILED] == report.summary["total"]
        assert report.summary["fallback_count"] == 0

    def test_yfinance_as_last_resort_fallback(self):
        """Mock: cn_akshare + cn_baostock fail, yfinance succeeds."""

        def probe_fn(method, args, kwargs, symbol):
            return _make_check(
                method=method,
                status=STATUS_OK,
                vendor="yfinance",
                chain=["cn_akshare", "cn_baostock", "yfinance"],
            )

        report = run_health_check(symbols=["600519.SH"], probe_fn=probe_fn)
        assert report.summary["fallback_count"] == report.summary["total"]
        for c in report.checks:
            assert c.fallback_from == "cn_akshare"
            assert c.vendor == "yfinance"

    def test_partial_fallback_only_some_endpoints(self):
        """Some endpoints use fallback, others use primary directly."""
        call_count = [0]

        def probe_fn(method, args, kwargs, symbol):
            call_count[0] += 1
            if method in ("get_stock_data", "get_indicators"):
                return _make_check(
                    method=method,
                    vendor="cn_baostock",
                    chain=["cn_akshare", "cn_baostock"],
                )
            return _make_check(
                method=method,
                vendor="cn_akshare",
                chain=["cn_akshare", "cn_baostock"],
            )

        report = run_health_check(symbols=["600519.SH"], probe_fn=probe_fn)
        assert report.summary["fallback_count"] == 2
        fallback_methods = [c.method for c in report.checks if c.fallback_from]
        assert "get_stock_data" in fallback_methods
        assert "get_indicators" in fallback_methods

    def test_fallback_from_recorded_in_report_markdown(self):
        def probe_fn(method, args, kwargs, symbol):
            return _make_check(
                method=method,
                vendor="cn_astock",
                chain=["cn_akshare", "cn_astock"],
            )

        report = run_health_check(probe_fn=probe_fn)
        md = render_report(report)
        assert "cn_akshare → cn_astock" in md


# ── Status constants ───────────────────────────────────────────────────

class TestStatusConstants:
    def test_values(self):
        assert STATUS_OK == "OK"
        assert STATUS_FAILED == "FAILED"
        assert STATUS_STALE == "STALE"
        assert STATUS_NOT_QUERIED == "NOT_QUERIED"

    def test_endpoint_check_all_statuses(self):
        for status in (STATUS_OK, STATUS_FAILED, STATUS_STALE, STATUS_NOT_QUERIED):
            c = _make_check(status=status)
            assert c.status == status
