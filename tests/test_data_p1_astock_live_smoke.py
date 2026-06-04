"""Tests for DATA-P1-ASTOCK-LIVE-SMOKE: cn_astock/Eastmoney live smoke and rate-limit verification.

All live network calls are gated behind ``TA_LIVE_DATA_SMOKE=1``.
Without it, tests verify the data models, endpoint definitions, report
rendering, rate limiting logic, and mock-based smoke runs.
"""

import json
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.live_smoke import (
    EndpointSmokeResult,
    LiveSmokeReport,
    _STATUS_OK,
    _STATUS_FAILED,
    _STATUS_SKIPPED,
    _STATUS_NORMAL_NO_DATA,
    _count_data_lines,
    _detect_unit,
    _make_endpoint_definitions,
    _resolve_args,
    _compute_summary,
    run_live_smoke,
    render_live_smoke_report,
    save_live_smoke_report,
    is_live_smoke_enabled,
    DEFAULT_LIVE_SMOKE_SYMBOLS,
    _LIVE_ENV,
    _MAX_SYMBOLS,
    _MIN_ENDPOINT_INTERVAL,
)


FUND_FLOW_OK_TEXT = (
    "600519.SH 近20日主力资金净流向（Eastmoney push2his，单位：万元）：\n"
    "日期 | 主力净流入 | 小单净流入 | 中单净流入 | 大单净流入 | 超大单净流入\n"
    "2026-06-03 | 1234.56 | -500.00 | 200.00 | 600.00 | 434.56\n"
    "2026-06-02 | -800.00 | 300.00 | -100.00 | -400.00 | -300.00\n"
)

LHB_NORMAL_NO_DATA_TEXT = (
    "600519.SH [G-007] LHB_NORMAL_NO_DATA: 在 2026-06-03 无龙虎榜数据（非异动日属正常）。"
)

LHB_HAS_DATA_TEXT = (
    "600519.SH [G-007] LHB_HAS_DATA: 龙虎榜明细（2026-06-03，Eastmoney datacenter）：\n"
    "- 涨幅偏离 | 净买 1234.5万 | 买入 2000.0万 | 卖出 765.5万\n"
)

REALTIME_QUOTE_OK_TEXT = json.dumps({
    "600519.SH": {
        "price": 1800.0,
        "open": 1790.0,
        "high": 1810.0,
        "low": 1785.0,
        "previous_close": 1788.0,
        "change": 12.0,
        "change_pct": 0.67,
        "volume": 12345678,
        "amount": 22222222222,
        "turnover_rate": 0.98,
        "volume_ratio": 1.23,
        "limit_up": 1966.8,
        "limit_down": 1609.2,
        "market_cap": 22600,
        "pe_ttm": 28.5,
        "pe_static": 27.8,
        "pb": 10.2,
        "source": "tencent",
    }
}, ensure_ascii=False)

ANNOUNCEMENTS_OK_TEXT = (
    "## Announcements for 600519.SH (3 total)\n\n"
    "- 2026-06-01 | 定期报告 | 贵州茅台2025年年度报告\n"
    "  Link: https://www.cninfo.com.cn/new/disclosure/detail?annoId=xxx\n"
    "- 2026-05-30 | 股东大会 | 关于召开2025年年度股东大会的通知\n"
)

FUND_FLOW_FAILURE_TEXT = "个股资金流向数据获取失败（Eastmoney push2his）：ProxyError: connection refused"


def _make_result(**overrides) -> EndpointSmokeResult:
    defaults = dict(
        endpoint="cn_astock/push2his_fund_flow",
        vendor="cn_astock",
        symbol="600519.SH",
        status=_STATUS_OK,
        latency_ms=150.0,
        field_count=2,
        unit="万元",
        unit_verified=True,
        error="",
        has_data=True,
        sample_snippet="sample",
        rate_limit_ok=True,
    )
    defaults.update(overrides)
    return EndpointSmokeResult(**defaults)


# ── Data models ──────────────────────────────────────────────────────────

class TestEndpointSmokeResult:
    def test_to_dict_roundtrip(self):
        r = _make_result()
        d = r.to_dict()
        assert d["endpoint"] == "cn_astock/push2his_fund_flow"
        assert d["vendor"] == "cn_astock"
        assert d["status"] == _STATUS_OK
        assert d["latency_ms"] == 150.0
        assert d["field_count"] == 2
        assert d["unit"] == "万元"
        assert d["unit_verified"] is True
        assert d["has_data"] is True
        assert d["rate_limit_ok"] is True

    def test_to_dict_failed(self):
        r = _make_result(status=_STATUS_FAILED, error="TimeoutError: timed out after 15s")
        d = r.to_dict()
        assert d["status"] == _STATUS_FAILED
        assert "TimeoutError" in d["error"]

    def test_to_dict_truncates_long_error(self):
        r = _make_result(error="X" * 300)
        d = r.to_dict()
        assert len(d["error"]) <= 200

    def test_to_dict_truncates_long_snippet(self):
        r = _make_result(sample_snippet="A" * 200)
        d = r.to_dict()
        assert len(d["sample_snippet"]) <= 120

    def test_default_status_is_not_run(self):
        r = EndpointSmokeResult(endpoint="x", vendor="y", symbol="z")
        assert r.status == "NOT_RUN"


class TestLiveSmokeReport:
    def test_to_dict_roundtrip(self):
        r = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={"total": 1, "OK": 1, "FAILED": 0},
            env_gated=False,
        )
        d = r.to_dict()
        assert d["date"] == "2026-06-04"
        assert len(d["results"]) == 1
        assert d["env_gated"] is False

    def test_empty_report(self):
        r = LiveSmokeReport(run_at="2026-06-04 12:00:00", date="2026-06-04")
        d = r.to_dict()
        assert d["results"] == []
        assert d["symbols"] == []


# ── Unit detection ───────────────────────────────────────────────────────

class TestDetectUnit:
    def test_wan_yuan_colon(self):
        assert _detect_unit("单位：万元") == "万元"

    def test_wan_yuan_english_colon(self):
        assert _detect_unit("单位: 万元") == "万元"

    def test_yuan(self):
        assert _detect_unit("单位：元") == "元"

    def test_no_unit(self):
        assert _detect_unit("some random text") == ""

    def test_embedded_in_context(self):
        assert _detect_unit(FUND_FLOW_OK_TEXT) == "万元"


class TestCountDataLines:
    def test_normal_csv(self):
        assert _count_data_lines(FUND_FLOW_OK_TEXT) == 3

    def test_empty_string(self):
        assert _count_data_lines("") == 0

    def test_header_only(self):
        assert _count_data_lines("日期 | 主力\n") == 0

    def test_strips_hashes(self):
        text = "## Header\n# comment\ndata line 1\ndata line 2\ndata line 3\n"
        assert _count_data_lines(text) == 2


# ── Endpoint definitions ────────────────────────────────────────────────

class TestEndpointDefinitions:
    def test_has_four_endpoints(self):
        eps = _make_endpoint_definitions()
        assert len(eps) == 4

    def test_covers_fund_flow(self):
        eps = _make_endpoint_definitions()
        methods = [e["method"] for e in eps]
        assert "get_individual_fund_flow" in methods

    def test_covers_lhb(self):
        eps = _make_endpoint_definitions()
        methods = [e["method"] for e in eps]
        assert "get_lhb_detail" in methods

    def test_covers_realtime_quote(self):
        eps = _make_endpoint_definitions()
        methods = [e["method"] for e in eps]
        assert "get_realtime_quotes" in methods

    def test_covers_announcements(self):
        eps = _make_endpoint_definitions()
        methods = [e["method"] for e in eps]
        assert "get_announcements" in methods

    def test_all_have_expected_unit(self):
        for ep in _make_endpoint_definitions():
            assert "expected_unit" in ep

    def test_all_have_args_template(self):
        for ep in _make_endpoint_definitions():
            assert "args_template" in ep
            assert isinstance(ep["args_template"], tuple)

    def test_lhb_has_force_true(self):
        eps = _make_endpoint_definitions()
        lhb = [e for e in eps if e["method"] == "get_lhb_detail"][0]
        assert lhb["kwargs"].get("force") is True


# ── Arg resolution ──────────────────────────────────────────────────────

class TestResolveArgs:
    def test_symbol_substitution(self):
        result = _resolve_args(("{symbol}",), "600519.SH", "2026-06-04")
        assert result == ("600519.SH",)

    def test_date_substitution(self):
        result = _resolve_args(("{symbol}", "{date}"), "600519.SH", "2026-06-04")
        assert result == ("600519.SH", "2026-06-04")

    def test_list_substitution(self):
        result = _resolve_args((["{symbol}"],), "600519.SH", "2026-06-04")
        assert result == (["600519.SH"],)

    def test_mixed_args(self):
        result = _resolve_args(("{symbol}", "{date}"), "603629.SH", "2026-06-03")
        assert result == ("603629.SH", "2026-06-03")


# ── Summary computation ─────────────────────────────────────────────────

class TestComputeSummary:
    def test_all_ok(self):
        results = [
            _make_result(endpoint="ep1", status=_STATUS_OK),
            _make_result(endpoint="ep2", status=_STATUS_OK),
        ]
        s = _compute_summary(results)
        assert s["OK"] == 2
        assert s["FAILED"] == 0
        assert s["all_passed"] is True
        assert s["has_failures"] is False

    def test_has_failures(self):
        results = [
            _make_result(endpoint="ep1", status=_STATUS_OK),
            _make_result(endpoint="ep2", status=_STATUS_FAILED, error="test"),
        ]
        s = _compute_summary(results)
        assert s["OK"] == 1
        assert s["FAILED"] == 1
        assert s["all_passed"] is False
        assert s["has_failures"] is True

    def test_skipped_does_not_affect_all_passed(self):
        results = [
            _make_result(endpoint="ep1", status=_STATUS_SKIPPED),
            _make_result(endpoint="ep2", status=_STATUS_OK),
        ]
        s = _compute_summary(results)
        assert s["all_passed"] is True
        assert s["SKIPPED"] == 1

    def test_normal_no_data_is_not_failure(self):
        results = [
            _make_result(endpoint="ep1", status=_STATUS_NORMAL_NO_DATA),
        ]
        s = _compute_summary(results)
        assert s["all_passed"] is True
        assert s["has_failures"] is False
        assert s["NORMAL_NO_DATA"] == 1

    def test_by_endpoint_grouping(self):
        results = [
            _make_result(endpoint="ep_a", symbol="600519.SH", status=_STATUS_OK, latency_ms=100),
            _make_result(endpoint="ep_a", symbol="000001.SZ", status=_STATUS_OK, latency_ms=200),
            _make_result(endpoint="ep_b", symbol="600519.SH", status=_STATUS_FAILED, error="x"),
        ]
        s = _compute_summary(results)
        assert s["by_endpoint"]["ep_a"]["total"] == 2
        assert s["by_endpoint"]["ep_a"]["OK"] == 2
        assert s["by_endpoint"]["ep_a"]["avg_latency_ms"] == 150.0
        assert s["by_endpoint"]["ep_b"]["FAILED"] == 1

    def test_empty_results(self):
        s = _compute_summary([])
        assert s["total"] == 0
        assert s["all_passed"] is False

    def test_all_skipped(self):
        results = [_make_result(status=_STATUS_SKIPPED)] * 4
        s = _compute_summary(results)
        assert s["all_passed"] is False
        assert s["SKIPPED"] == 4


# ── run_live_smoke (env gated) ──────────────────────────────────────────

class TestRunLiveSmokeGated:
    def test_skipped_when_env_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_live_smoke(symbols=["600519.SH"])
        assert report.env_gated is True
        assert all(r.status == _STATUS_SKIPPED for r in report.results)

    def test_skipped_report_has_results(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_live_smoke(symbols=["600519.SH"])
        assert len(report.results) > 0
        assert report.summary.get("SKIPPED", 0) > 0

    def test_respects_max_symbols(self):
        with patch.dict(os.environ, {}, clear=True):
            symbols = ["600519.SH", "000001.SZ", "603629.SH", "300034.SZ"]
            report = run_live_smoke(symbols=symbols)
        assert len(report.symbols) <= _MAX_SYMBOLS

    def test_default_symbols_used(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_live_smoke()
        assert len(report.symbols) > 0


# ── run_live_smoke (mocked live calls) ──────────────────────────────────

class TestRunLiveSmokeMockedLive:
    def _mock_smoke_fn(self, responses):
        call_idx = [0]

        def smoke_fn(provider, ep_def, symbol, date):
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(responses):
                return responses[idx]
            return _make_result(status=_STATUS_OK)

        return smoke_fn

    def test_fund_flow_ok(self):
        ok_result = _make_result(
            endpoint="cn_astock/push2his_fund_flow",
            status=_STATUS_OK,
            has_data=True,
            field_count=2,
            unit="万元",
            unit_verified=True,
            sample_snippet=FUND_FLOW_OK_TEXT[:120],
        )
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_smoke(
                symbols=["600519.SH"],
                smoke_fn=self._mock_smoke_fn([ok_result] * 4),
            )
        assert report.env_gated is False
        fund_results = [r for r in report.results if r.endpoint == "cn_astock/push2his_fund_flow"]
        assert len(fund_results) >= 1
        assert fund_results[0].status == _STATUS_OK

    def test_lhb_normal_no_data(self):
        lhb_result = _make_result(
            endpoint="cn_astock/datacenter_lhb",
            status=_STATUS_NORMAL_NO_DATA,
            has_data=False,
            unit="",
            sample_snippet=LHB_NORMAL_NO_DATA_TEXT[:120],
        )
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_smoke(
                symbols=["600519.SH"],
                smoke_fn=self._mock_smoke_fn([lhb_result] * 4),
            )
        lhb_results = [r for r in report.results if r.endpoint == "cn_astock/datacenter_lhb"]
        assert len(lhb_results) >= 1
        assert lhb_results[0].status == _STATUS_NORMAL_NO_DATA

    def test_fund_flow_failure(self):
        fail_result = _make_result(
            endpoint="cn_astock/push2his_fund_flow",
            status=_STATUS_FAILED,
            error="ProxyError: connection refused",
            has_data=False,
        )
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_smoke(
                symbols=["600519.SH"],
                smoke_fn=self._mock_smoke_fn([fail_result] * 4),
            )
        assert report.summary["has_failures"] is True
        assert report.summary["FAILED"] > 0

    def test_realtime_quote_has_fields(self):
        quote_result = _make_result(
            endpoint="cn_astock/tencent_realtime_quote",
            status=_STATUS_OK,
            has_data=True,
            field_count=15,
            unit="",
            unit_verified=False,
        )
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_smoke(
                symbols=["600519.SH"],
                smoke_fn=self._mock_smoke_fn([quote_result] * 4),
            )
        qt_results = [r for r in report.results if r.endpoint == "cn_astock/tencent_realtime_quote"]
        assert len(qt_results) >= 1
        assert qt_results[0].has_data is True

    def test_announcements_ok(self):
        ann_result = _make_result(
            endpoint="cn_astock/cninfo_announcements",
            status=_STATUS_OK,
            has_data=True,
            field_count=3,
            unit="",
        )
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_smoke(
                symbols=["600519.SH"],
                smoke_fn=self._mock_smoke_fn([ann_result] * 4),
            )
        ann_results = [r for r in report.results if r.endpoint == "cn_astock/cninfo_announcements"]
        assert len(ann_results) >= 1
        assert ann_results[0].status == _STATUS_OK

    def test_multiple_symbols(self):
        results = [
            _make_result(symbol="600519.SH", status=_STATUS_OK),
            _make_result(symbol="000001.SZ", status=_STATUS_OK),
        ] * 4
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_smoke(
                symbols=["600519.SH", "000001.SZ"],
                smoke_fn=self._mock_smoke_fn(results),
            )
        symbols_in_results = set(r.symbol for r in report.results)
        assert "600519.SH" in symbols_in_results
        assert "000001.SZ" in symbols_in_results


# ── Smoke single endpoint (mocked provider) ─────────────────────────────

class TestSmokeSingleEndpoint:
    def test_fund_flow_ok_mock(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_individual_fund_flow.return_value = FUND_FLOW_OK_TEXT
        ep_def = {
            "endpoint": "cn_astock/push2his_fund_flow",
            "method": "get_individual_fund_flow",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_OK
        assert result.has_data is True
        assert result.unit == "万元"
        assert result.unit_verified is True
        assert result.latency_ms > 0

    def test_lhb_no_data_mock(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_lhb_detail.return_value = LHB_NORMAL_NO_DATA_TEXT
        ep_def = {
            "endpoint": "cn_astock/datacenter_lhb",
            "method": "get_lhb_detail",
            "vendor": "cn_astock",
            "args_template": ("{symbol}", "{date}"),
            "kwargs": {"force": True},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_NORMAL_NO_DATA
        assert result.has_data is False

    def test_lhb_has_data_mock(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_lhb_detail.return_value = LHB_HAS_DATA_TEXT
        ep_def = {
            "endpoint": "cn_astock/datacenter_lhb",
            "method": "get_lhb_detail",
            "vendor": "cn_astock",
            "args_template": ("{symbol}", "{date}"),
            "kwargs": {"force": True},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_OK
        assert result.has_data is True

    def test_failure_string_detected(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_individual_fund_flow.return_value = FUND_FLOW_FAILURE_TEXT
        ep_def = {
            "endpoint": "cn_astock/push2his_fund_flow",
            "method": "get_individual_fund_flow",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_FAILED
        assert "failure_string_detected" in result.error

    def test_exception_handled(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_realtime_quotes.side_effect = ConnectionError("refused")
        ep_def = {
            "endpoint": "cn_astock/tencent_realtime_quote",
            "method": "get_realtime_quotes",
            "vendor": "cn_astock",
            "args_template": (["{symbol}"],),
            "kwargs": {},
            "expected_unit": "",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_FAILED
        assert "ConnectionError" in result.error

    def test_empty_response(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_announcements.return_value = "short"
        ep_def = {
            "endpoint": "cn_astock/cninfo_announcements",
            "method": "get_announcements",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_FAILED
        assert "empty_or_minimal" in result.error

    def test_unit_mismatch(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        text = "600519.SH 资金流（单位：元）\n日期 | 金额\n2026-06-03 | 123456"
        provider = MagicMock()
        provider.get_individual_fund_flow.return_value = text
        ep_def = {
            "endpoint": "cn_astock/push2his_fund_flow",
            "method": "get_individual_fund_flow",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_OK
        assert result.unit == "元"
        assert result.unit_verified is False

    def test_rate_limit_checked(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_individual_fund_flow.return_value = FUND_FLOW_OK_TEXT
        ep_def = {
            "endpoint": "cn_astock/push2his_fund_flow",
            "method": "get_individual_fund_flow",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.rate_limit_ok is True


# ── Report rendering ────────────────────────────────────────────────────

class TestRenderLiveSmokeReport:
    def test_contains_summary(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={"total": 1, "OK": 1, "FAILED": 0, "SKIPPED": 0, "NORMAL_NO_DATA": 0},
            env_gated=False,
        )
        md = render_live_smoke_report(report)
        assert "## Summary" in md
        assert "| OK |" in md

    def test_contains_endpoint_details(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={"total": 1, "OK": 1, "FAILED": 0, "SKIPPED": 0, "NORMAL_NO_DATA": 0},
            env_gated=False,
        )
        md = render_live_smoke_report(report)
        assert "## Endpoint Details" in md
        assert "cn_astock/push2his_fund_flow" in md

    def test_shows_env_gated(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            env_gated=True,
        )
        md = render_live_smoke_report(report)
        assert "TA_LIVE_DATA_SMOKE" in md

    def test_shows_failed_section(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result(status=_STATUS_FAILED, error="TimeoutError")],
            summary={"total": 1, "OK": 0, "FAILED": 1, "SKIPPED": 0, "NORMAL_NO_DATA": 0},
            env_gated=False,
        )
        md = render_live_smoke_report(report)
        assert "## Failed Endpoints" in md
        assert "TimeoutError" in md

    def test_no_failed_section_when_all_ok(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={"total": 1, "OK": 1, "FAILED": 0, "SKIPPED": 0, "NORMAL_NO_DATA": 0},
            env_gated=False,
        )
        md = render_live_smoke_report(report)
        assert "## Failed Endpoints" not in md

    def test_per_endpoint_summary(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={
                "total": 1, "OK": 1, "FAILED": 0, "SKIPPED": 0, "NORMAL_NO_DATA": 0,
                "by_endpoint": {
                    "cn_astock/push2his_fund_flow": {
                        "total": 1, "OK": 1, "FAILED": 0, "SKIPPED": 0,
                        "NORMAL_NO_DATA": 0, "avg_latency_ms": 150.0,
                    },
                },
            },
            env_gated=False,
        )
        md = render_live_smoke_report(report)
        assert "## Per-Endpoint Summary" in md
        assert "cn_astock/push2his_fund_flow" in md

    def test_task_marker_in_footer(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
        )
        md = render_live_smoke_report(report)
        assert "[DATA-P1-ASTOCK-LIVE-SMOKE]" in md


# ── File output ──────────────────────────────────────────────────────────

class TestSaveLiveSmokeReport:
    def test_creates_file(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={"total": 1, "OK": 1, "FAILED": 0},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_live_smoke_report(report, output_dir=tmpdir)
            assert os.path.isfile(path)
            assert "live-smoke-2026-06-04" in path

    def test_file_content_is_markdown(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={"total": 1, "OK": 1, "FAILED": 0},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_live_smoke_report(report, output_dir=tmpdir)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert content.startswith("# Live Smoke Report")


# ── Env gating ───────────────────────────────────────────────────────────

class TestEnvGating:
    def test_not_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_live_smoke_enabled() is False

    def test_enabled_when_set(self):
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            assert is_live_smoke_enabled() is True

    def test_not_enabled_when_zero(self):
        with patch.dict(os.environ, {_LIVE_ENV: "0"}):
            assert is_live_smoke_enabled() is False


# ── Rate limiting ────────────────────────────────────────────────────────

class TestRateLimiting:
    def test_min_interval_defined(self):
        assert _MIN_ENDPOINT_INTERVAL >= 0.3

    def test_max_symbols_defined(self):
        assert _MAX_SYMBOLS == 3


# ── Acceptance: DATA-P1-ASTOCK-LIVE-SMOKE ────────────────────────────────

class TestAcceptanceDataP1AstockLiveSmoke:
    def test_live_tests_skip_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_live_smoke(symbols=["600519.SH"])
        assert report.env_gated is True
        for r in report.results:
            assert r.status == _STATUS_SKIPPED

    def test_max_three_symbols(self):
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            many = [f"{i:06d}.SH" for i in range(10)]
            report = run_live_smoke(symbols=many)
        assert len(report.symbols) <= 3

    def test_covers_four_endpoint_types(self):
        eps = _make_endpoint_definitions()
        methods = {e["method"] for e in eps}
        assert "get_individual_fund_flow" in methods
        assert "get_lhb_detail" in methods
        assert "get_realtime_quotes" in methods
        assert "get_announcements" in methods

    def test_endpoint_failure_is_failed_not_normal_no_data(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_individual_fund_flow.return_value = FUND_FLOW_FAILURE_TEXT
        ep_def = {
            "endpoint": "cn_astock/push2his_fund_flow",
            "method": "get_individual_fund_flow",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_FAILED
        assert result.status != _STATUS_NORMAL_NO_DATA

    def test_report_output_path(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={"total": 1, "OK": 1, "FAILED": 0},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_live_smoke_report(report, output_dir=tmpdir)
            assert "live-smoke-" in path
            assert path.endswith(".md")

    def test_unit_verified_for_fund_flow(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_individual_fund_flow.return_value = FUND_FLOW_OK_TEXT
        ep_def = {
            "endpoint": "cn_astock/push2his_fund_flow",
            "method": "get_individual_fund_flow",
            "vendor": "cn_astock",
            "args_template": ("{symbol}",),
            "kwargs": {},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.unit == "万元"
        assert result.unit_verified is True

    def test_lhb_normal_no_data_not_failed(self):
        from tradingagents.dataflows.live_smoke import _smoke_single_endpoint
        provider = MagicMock()
        provider.get_lhb_detail.return_value = LHB_NORMAL_NO_DATA_TEXT
        ep_def = {
            "endpoint": "cn_astock/datacenter_lhb",
            "method": "get_lhb_detail",
            "vendor": "cn_astock",
            "args_template": ("{symbol}", "{date}"),
            "kwargs": {"force": True},
            "expected_unit": "万元",
        }
        result = _smoke_single_endpoint(provider, ep_def, "600519.SH", "2026-06-04")
        assert result.status == _STATUS_NORMAL_NO_DATA
        assert result.status != _STATUS_FAILED

    def test_report_includes_latency(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result(latency_ms=250.3)],
            summary={"total": 1, "OK": 1, "FAILED": 0},
            env_gated=False,
        )
        md = render_live_smoke_report(report)
        assert "250" in md

    def test_no_strong_words_in_report(self):
        report = LiveSmokeReport(
            run_at="2026-06-04 12:00:00",
            date="2026-06-04",
            symbols=["600519.SH"],
            results=[_make_result()],
            summary={"total": 1, "OK": 1, "FAILED": 0},
        )
        md = render_live_smoke_report(report)
        for word in ("买入", "卖出", "清仓", "满仓"):
            assert word not in md
