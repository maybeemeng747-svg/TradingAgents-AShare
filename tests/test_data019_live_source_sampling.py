# [DATA-019] live_source_sampling tests
"""Tests for DATA-019: key data source live sampling health daily report.

Tests cover:
  - Sample universe construction (large/small/candidate/watchlist)
  - 6-state classification (HAS_DATA/NORMAL_NO_DATA/STALE/FAILED/RATE_LIMITED/UNIT_UNVERIFIED)
  - Fixture scenarios: AKShare fail → cn_astock fallback success, LHB normal no-data
  - Env gating (TA_LIVE_DATA_SMOKE)
  - Report rendering (no secrets)
  - Report file I/O (YYYY-MM-DD-live-smoke.md)
  - Per-data-type summary
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.live_source_sampling import (
    SampleStock,
    SampleResult,
    LiveSamplingReport,
    DEFAULT_SAMPLE_UNIVERSE,
    _MAX_SYMBOLS,
    _MIN_ENDPOINT_INTERVAL,
    _LIVE_ENV,
    _DATA_TYPE_LABELS,
    _make_data_type_definitions,
    _resolve_args,
    _detect_unit_from_text,
    _count_data_lines,
    _extract_as_of,
    classify_sample_result,
    _build_sample_diagnosis,
    _sample_single,
    _compute_summary,
    build_sample_universe,
    run_live_sampling,
    render_live_sampling_report,
    save_live_sampling_report,
    find_latest_live_sampling_report,
    build_sampling_section_for_nightly_report,
    is_live_sampling_enabled,
    ALL_SAMPLE_RESPONSES,
)
from tradingagents.dataflows.source_freshness_report import SourceFreshnessStatus


# ── Test SampleStock ──────────────────────────────────────────────────


class TestSampleStock:
    def test_basic_construction(self):
        s = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap", category_cn="大票")
        assert s.symbol == "600519.SH"
        assert s.name == "贵州茅台"
        assert s.category == "large_cap"

    def test_to_dict(self):
        s = SampleStock(symbol="000001.SZ", name="平安银行", category="watchlist", category_cn="自选")
        d = s.to_dict()
        assert d["symbol"] == "000001.SZ"
        assert d["category"] == "watchlist"
        assert d["category_cn"] == "自选"


class TestSampleResult:
    def test_traffic_light(self):
        r = SampleResult(status=SourceFreshnessStatus.HAS_DATA)
        assert r.traffic_light == "green"
        r2 = SampleResult(status=SourceFreshnessStatus.FAILED)
        assert r2.traffic_light == "red"

    def test_label_cn(self):
        r = SampleResult(status=SourceFreshnessStatus.HAS_DATA)
        assert r.label_cn == "正常"
        r2 = SampleResult(status=SourceFreshnessStatus.RATE_LIMITED)
        assert r2.label_cn == "限流"

    def test_to_dict(self):
        r = SampleResult(
            data_type="fund_flow",
            symbol="600519.SH",
            status=SourceFreshnessStatus.HAS_DATA,
            latency_ms=150.0,
            error="x" * 300,
        )
        d = r.to_dict()
        assert d["data_type"] == "fund_flow"
        assert d["status"] == "HAS_DATA"
        assert len(d["error"]) <= 200  # truncated


# ── Test default sample universe ──────────────────────────────────────


class TestSampleUniverse:
    def test_default_has_four_categories(self):
        universe = DEFAULT_SAMPLE_UNIVERSE
        assert len(universe) == 4
        categories = [s["category"] for s in universe]
        assert "large_cap" in categories
        assert "small_mid_cap" in categories
        assert "recent_candidate" in categories
        assert "watchlist" in categories

    def test_build_sample_universe_defaults(self):
        stocks = build_sample_universe()
        assert len(stocks) == 4
        assert all(isinstance(s, SampleStock) for s in stocks)
        assert stocks[0].category == "large_cap"
        assert stocks[3].category == "watchlist"

    def test_build_sample_universe_custom(self):
        custom = [
            {"symbol": "601318.SH", "name": "中国平安", "category": "large_cap", "category_cn": "大票"},
            {"symbol": "300750.SZ", "name": "宁德时代", "category": "small_mid_cap", "category_cn": "中小票"},
        ]
        stocks = build_sample_universe(custom)
        assert len(stocks) == 2
        assert stocks[0].symbol == "601318.SH"

    def test_build_sample_universe_capped(self):
        custom = [{"symbol": f"{i:06d}.SH", "name": f"S{i}", "category": "x", "category_cn": "x"} for i in range(10)]
        stocks = build_sample_universe(custom)
        assert len(stocks) <= _MAX_SYMBOLS

    def test_each_stock_has_name_and_symbol(self):
        stocks = build_sample_universe()
        for s in stocks:
            assert s.symbol
            assert s.name
            assert "." in s.symbol  # e.g. 600519.SH


# ── Test data type definitions ────────────────────────────────────────


class TestDataTypeDefinitions:
    def test_covers_key_data_types(self):
        defs = _make_data_type_definitions()
        dt_names = [d["data_type"] for d in defs]
        # Task requires: quote, fund_flow, lhb, notice, rating, buyback, report
        for required in ["quote", "fund_flow", "lhb", "notice", "rating", "buyback", "report"]:
            assert required in dt_names, f"Missing data_type: {required}"

    def test_each_def_has_method_and_args(self):
        defs = _make_data_type_definitions()
        for d in defs:
            assert "method" in d
            assert "args_template" in d
            assert "kwargs" in d
            assert "expected_unit" in d

    def test_lhb_has_force_true(self):
        defs = _make_data_type_definitions()
        lhb_def = [d for d in defs if d["data_type"] == "lhb"][0]
        assert lhb_def["kwargs"].get("force") is True

    def test_fund_flow_has_special_note(self):
        defs = _make_data_type_definitions()
        ff_def = [d for d in defs if d["data_type"] == "fund_flow"][0]
        assert "fallback" in ff_def["special_note"]

    def test_lhb_has_special_note(self):
        defs = _make_data_type_definitions()
        lhb_def = [d for d in defs if d["data_type"] == "lhb"][0]
        assert "NORMAL_NO_DATA" in lhb_def["special_note"]


# ── Test arg resolution ───────────────────────────────────────────────


class TestResolveArgs:
    def test_string_args(self):
        result = _resolve_args(("{symbol}", "{date}"), "600519.SH", "2026-06-18")
        assert result == ("600519.SH", "2026-06-18")

    def test_list_args(self):
        result = _resolve_args((["{symbol}"],), "600519.SH", "2026-06-18")
        assert result == (["600519.SH"],)

    def test_no_placeholders(self):
        result = _resolve_args(("static",), "600519.SH", "2026-06-18")
        assert result == ("static",)


# ── Test classification ───────────────────────────────────────────────


class TestClassifySampleResult:
    def test_has_data(self):
        status = classify_sample_result("real data here with enough length", data_type="fund_flow")
        assert status in (SourceFreshnessStatus.HAS_DATA, SourceFreshnessStatus.UNIT_UNVERIFIED)

    def test_lhb_normal_no_data(self):
        text = "600519.SH [G-007] LHB_NORMAL_NO_DATA: 非异动日属正常"
        status = classify_sample_result(text, data_type="lhb")
        assert status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_buyback_normal_no_data(self):
        text = "002415.SZ [DATA-013] BUYBACK_NORMAL_NO_DATA: 该股无回购计划"
        status = classify_sample_result(text, data_type="buyback")
        assert status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_failed_string(self):
        text = "个股资金流向数据获取失败：ProxyError"
        status = classify_sample_result(text, data_type="fund_flow")
        assert status == SourceFreshnessStatus.FAILED

    def test_rate_limited(self):
        status = classify_sample_result(
            "", error="HTTPError 429: Too Many Requests", data_type="quote"
        )
        assert status == SourceFreshnessStatus.RATE_LIMITED

    def test_stale(self):
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        status = classify_sample_result(
            "data present", as_of=old_date, unit_verified=True, data_type="fund_flow"
        )
        assert status == SourceFreshnessStatus.STALE

    def test_unit_unverified(self):
        status = classify_sample_result(
            "data present with content",
            unit_verified=False,
            data_type="fund_flow",
        )
        # Without as_of, and unit_verified=False → UNIT_UNVERIFIED
        assert status == SourceFreshnessStatus.UNIT_UNVERIFIED


# ── Test unit detection ───────────────────────────────────────────────


class TestUnitDetection:
    def test_detect_wanyuan(self):
        text = "资金流向（单位：万元）："
        unit, verified = _detect_unit_from_text(text)
        assert unit == "万元"
        assert verified is True

    def test_detect_yuan(self):
        text = "单位：元"
        unit, verified = _detect_unit_from_text(text)
        assert unit == "元"

    def test_no_unit(self):
        text = "some random data"
        unit, verified = _detect_unit_from_text(text)
        assert unit == ""
        assert verified is False

    def test_empty_text(self):
        unit, verified = _detect_unit_from_text("")
        assert unit == ""


class TestCountDataLines:
    def test_count(self):
        text = "header\n2026-06-17 | data1\n2026-06-16 | data2"
        count = _count_data_lines(text)
        assert count >= 1

    def test_empty(self):
        assert _count_data_lines("") == 0


class TestExtractAsOf:
    def test_extract(self):
        assert _extract_as_of("data from 2026-06-17 here") == "2026-06-17"

    def test_no_date(self):
        assert _extract_as_of("no date here") == ""

    def test_empty(self):
        assert _extract_as_of("") == ""


# ── Test _sample_single with mock fetch_fn ────────────────────────────


def _make_mock_fetch_fn(response_map):
    """Create a mock fetch_fn that returns canned responses by (method, symbol)."""

    def fetch_fn(method, args, kwargs):
        symbol = ""
        for a in args:
            if isinstance(a, str) and "." in a:
                symbol = a
                break
            elif isinstance(a, list):
                for item in a:
                    if isinstance(item, str) and "." in item:
                        symbol = item
                        break
        key = (method, symbol)
        if key in response_map:
            resp = response_map[key]
            if isinstance(resp, Exception):
                raise resp
            return resp
        return "default response"

    return fetch_fn


class TestSampleSingle:
    def test_fund_flow_ok(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")
        dt_def = [d for d in _make_data_type_definitions() if d["data_type"] == "fund_flow"][0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_individual_fund_flow", "600519.SH"): ALL_SAMPLE_RESPONSES["fund_flow_ok"]
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.status in (SourceFreshnessStatus.HAS_DATA, SourceFreshnessStatus.UNIT_UNVERIFIED)
        assert result.data_type == "fund_flow"
        assert result.latency_ms >= 0

    def test_fund_flow_akshare_fail(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")
        dt_def = [d for d in _make_data_type_definitions() if d["data_type"] == "fund_flow"][0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_individual_fund_flow", "600519.SH"): ALL_SAMPLE_RESPONSES["fund_flow_akshare_fail"]
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.status == SourceFreshnessStatus.FAILED

    def test_lhb_normal_no_data(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")
        dt_def = [d for d in _make_data_type_definitions() if d["data_type"] == "lhb"][0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_lhb_detail", "600519.SH"): ALL_SAMPLE_RESPONSES["lhb_normal_no_data"]
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_lhb_has_data(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")
        dt_def = [d for d in _make_data_type_definitions() if d["data_type"] == "lhb"][0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_lhb_detail", "600519.SH"): ALL_SAMPLE_RESPONSES["lhb_has_data"]
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.status in (SourceFreshnessStatus.HAS_DATA, SourceFreshnessStatus.UNIT_UNVERIFIED)

    def test_exception_handling(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")
        dt_def = _make_data_type_definitions()[0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_realtime_quotes", "600519.SH"): ConnectionError("timeout")
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.status == SourceFreshnessStatus.FAILED
        assert "ConnectionError" in result.error

    def test_rate_limited_exception(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")
        dt_def = _make_data_type_definitions()[0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_realtime_quotes", "600519.SH"): Exception("HTTPError 429: Too Many Requests")
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.status == SourceFreshnessStatus.RATE_LIMITED

    def test_buyback_normal_no_data(self):
        stock = SampleStock(symbol="002415.SZ", name="海康威视", category="small_mid_cap")
        dt_def = [d for d in _make_data_type_definitions() if d["data_type"] == "buyback"][0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_buybacks", "002415.SZ"): ALL_SAMPLE_RESPONSES["buyback_normal_no_data"]
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_null_response(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")
        dt_def = _make_data_type_definitions()[0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_realtime_quotes", "600519.SH"): None
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.status == SourceFreshnessStatus.FAILED

    def test_diagnosis_built(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")
        dt_def = [d for d in _make_data_type_definitions() if d["data_type"] == "fund_flow"][0]
        fetch_fn = _make_mock_fetch_fn({
            ("get_individual_fund_flow", "600519.SH"): ALL_SAMPLE_RESPONSES["fund_flow_ok"]
        })
        result = _sample_single(fetch_fn, dt_def, stock, "2026-06-18")
        assert result.diagnosis
        assert "资金流" in result.diagnosis


# ── Test run_live_sampling (env gated) ────────────────────────────────


class TestRunLiveSamplingGated:
    def test_env_gated_returns_skipped(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_LIVE_ENV, None)
            report = run_live_sampling()
            assert report.env_gated is True
            assert len(report.results) > 0
            assert all(r.status == "SKIPPED" for r in report.results)

    def test_env_gated_has_correct_sample_count(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_LIVE_ENV, None)
            report = run_live_sampling()
            # 4 stocks × 7 data types = 28 results
            assert len(report.results) == 4 * 7

    def test_env_gated_summary(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_LIVE_ENV, None)
            report = run_live_sampling()
            assert report.summary["skipped_count"] == 28
            assert report.summary["green_count"] == 0

    # [DATA-019A] live_source_sampling_skip_status
    def test_env_gated_skipped_only_not_all_green(self):
        """Env-gated run produces skipped-only results → NOT all_green."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_LIVE_ENV, None)
            report = run_live_sampling()
            assert report.summary["skipped_only"] is True
            assert report.summary["all_green"] is False
            assert report.summary["overall_status"] == "skipped"


class TestRunLiveSamplingMocked:
    def test_mocked_live_run(self):
        """Simulate a full live run with mocked fetch_fn."""
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")

        def mock_fetch(method, args, kwargs):
            if method == "get_realtime_quotes":
                return ALL_SAMPLE_RESPONSES["quote_ok"]
            elif method == "get_individual_fund_flow":
                return ALL_SAMPLE_RESPONSES["fund_flow_ok"]
            elif method == "get_lhb_detail":
                return ALL_SAMPLE_RESPONSES["lhb_normal_no_data"]
            elif method == "get_announcements":
                return ALL_SAMPLE_RESPONSES["notice_ok"]
            elif method == "get_ratings":
                return ALL_SAMPLE_RESPONSES["rating_ok"]
            elif method == "get_buybacks":
                return ALL_SAMPLE_RESPONSES["buyback_normal_no_data"]
            elif method == "get_research_report":
                return ALL_SAMPLE_RESPONSES["report_ok"]
            return "unknown"

        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_sampling(
                samples=[stock],
                fetch_fn=mock_fetch,
                date_str="2026-06-18",
            )
            assert report.env_gated is False
            assert len(report.results) == 7  # 1 stock × 7 data types

            # Check statuses
            statuses = {r.data_type: r.status for r in report.results}
            assert statuses["lhb"] == SourceFreshnessStatus.NORMAL_NO_DATA
            assert statuses["buyback"] == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_akshare_fail_astock_fallback_scenario(self):
        """Fixture: AKShare fails, cn_astock fallback succeeds for fund_flow."""
        stock = SampleStock(symbol="603629.SH", name="苏利股份", category="recent_candidate")

        def mock_fetch(method, args, kwargs):
            if method == "get_individual_fund_flow":
                return ALL_SAMPLE_RESPONSES["fund_flow_astock_fallback"]
            return ALL_SAMPLE_RESPONSES["quote_ok"]

        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_sampling(
                samples=[stock],
                fetch_fn=mock_fetch,
                date_str="2026-06-18",
            )
            fund_flow_result = [r for r in report.results if r.data_type == "fund_flow"][0]
            assert fund_flow_result.status in (
                SourceFreshnessStatus.HAS_DATA,
                SourceFreshnessStatus.UNIT_UNVERIFIED,
            )

    def test_all_failed_scenario(self):
        stock = SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap")

        def mock_fetch(method, args, kwargs):
            raise ConnectionError("all endpoints down")

        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_live_sampling(
                samples=[stock],
                fetch_fn=mock_fetch,
                date_str="2026-06-18",
            )
            assert all(r.status == SourceFreshnessStatus.FAILED for r in report.results)
            assert report.summary["red_count"] == 7
            assert report.summary["has_failures"] is True


# ── Test summary computation ──────────────────────────────────────────


class TestComputeSummary:
    def test_all_green(self):
        results = [
            SampleResult(data_type="quote", status=SourceFreshnessStatus.HAS_DATA),
            SampleResult(data_type="fund_flow", status=SourceFreshnessStatus.NORMAL_NO_DATA),
        ]
        summary = _compute_summary(results)
        assert summary["green_count"] == 2
        assert summary["red_count"] == 0
        assert summary["all_green"] is True

    def test_has_failures(self):
        results = [
            SampleResult(data_type="quote", status=SourceFreshnessStatus.HAS_DATA),
            SampleResult(data_type="fund_flow", status=SourceFreshnessStatus.FAILED),
        ]
        summary = _compute_summary(results)
        assert summary["has_failures"] is True
        assert summary["red_count"] == 1

    def test_skipped(self):
        results = [
            SampleResult(data_type="quote", status="SKIPPED"),
            SampleResult(data_type="fund_flow", status="SKIPPED"),
        ]
        summary = _compute_summary(results)
        assert summary["skipped_count"] == 2

    # [DATA-019A] live_source_sampling_skip_status
    def test_skipped_only_is_not_all_green(self):
        """When every check is SKIPPED, the report must NOT claim ALL GREEN."""
        results = [
            SampleResult(data_type="quote", status="SKIPPED"),
            SampleResult(data_type="fund_flow", status="SKIPPED"),
            SampleResult(data_type="lhb", status="SKIPPED"),
        ]
        summary = _compute_summary(results)
        assert summary["skipped_count"] == 3
        assert summary["skipped_only"] is True
        # the key regression: all_green must be False even though red/yellow are 0
        assert summary["all_green"] is False
        assert summary["overall_status"] == "skipped"
        assert summary["green_count"] == 0
        assert summary["red_count"] == 0
        assert summary["yellow_count"] == 0
        assert summary["has_failures"] is False
        assert summary["has_warnings"] is False

    # [DATA-019A] live_source_sampling_skip_status
    def test_mixed_skipped_and_pass_is_all_green(self):
        """Skipped mixed with green is OK — at least one real check ran."""
        results = [
            SampleResult(data_type="quote", status=SourceFreshnessStatus.HAS_DATA),
            SampleResult(data_type="fund_flow", status="SKIPPED"),
        ]
        summary = _compute_summary(results)
        assert summary["skipped_only"] is False
        assert summary["all_green"] is True
        assert summary["overall_status"] == "all_green"

    # [DATA-019A] live_source_sampling_skip_status
    def test_empty_results_not_skipped_only(self):
        """Empty results (degenerate) should not be flagged skipped_only."""
        summary = _compute_summary([])
        assert summary["skipped_only"] is False

    def test_by_data_type(self):
        results = [
            SampleResult(data_type="quote", data_type_label="实时行情", status=SourceFreshnessStatus.HAS_DATA),
            SampleResult(data_type="quote", data_type_label="实时行情", status=SourceFreshnessStatus.FAILED),
            SampleResult(data_type="fund_flow", data_type_label="个股资金流", status=SourceFreshnessStatus.HAS_DATA),
        ]
        summary = _compute_summary(results)
        by_dt = summary["by_data_type"]
        assert "quote" in by_dt
        assert by_dt["quote"]["green"] == 1
        assert by_dt["quote"]["red"] == 1
        assert by_dt["fund_flow"]["green"] == 1

    def test_fallback_triggered(self):
        results = [
            SampleResult(data_type="fund_flow", status=SourceFreshnessStatus.HAS_DATA, is_fallback=True),
        ]
        summary = _compute_summary(results)
        assert summary["fallback_triggered_count"] == 1


# ── Test report rendering ─────────────────────────────────────────────


class TestRenderReport:
    def test_basic_render(self):
        report = LiveSamplingReport(
            report_date="2026-06-18",
            generated_at="2026-06-18 10:00:00",
            samples=build_sample_universe(),
        )
        report.results = [
            SampleResult(
                data_type="fund_flow",
                data_type_label="个股资金流",
                symbol="600519.SH",
                symbol_name="贵州茅台",
                status=SourceFreshnessStatus.HAS_DATA,
            )
        ]
        report.summary = _compute_summary(report.results)
        md = render_live_sampling_report(report)
        assert "Live Source Sampling" in md
        assert "2026-06-18" in md
        assert "fund_flow" in md or "个股资金流" in md
        assert "[DATA-019]" in md

    def test_env_gated_render(self):
        report = LiveSamplingReport(
            report_date="2026-06-18",
            generated_at="2026-06-18 10:00:00",
            env_gated=True,
        )
        md = render_live_sampling_report(report)
        assert "Env gated" in md

    # [DATA-019A] live_source_sampling_skip_status
    def test_skipped_only_render_not_all_green(self):
        """skipped-only report must not render ALL GREEN."""
        report = LiveSamplingReport(
            report_date="2026-06-18",
            generated_at="2026-06-18 10:00:00",
            env_gated=True,
            samples=build_sample_universe(),
        )
        report.results = [
            SampleResult(
                data_type="quote",
                data_type_label="实时行情",
                symbol="600519.SH",
                status="SKIPPED",
            ),
            SampleResult(
                data_type="fund_flow",
                data_type_label="个股资金流",
                symbol="600519.SH",
                status="SKIPPED",
            ),
        ]
        report.summary = _compute_summary(report.results)
        assert report.summary["skipped_only"] is True
        md = render_live_sampling_report(report)
        # must NOT contain ALL GREEN
        assert "ALL GREEN" not in md
        # must explicitly say skipped / live smoke not enabled
        assert "未执行实盘抽样" in md or "live smoke" in md
        assert report.summary["overall_status"] == "skipped"

    def test_no_secrets_in_report(self):
        """Report must not contain API keys or secrets."""
        report = LiveSamplingReport(
            report_date="2026-06-18",
            generated_at="2026-06-18 10:00:00",
            samples=build_sample_universe(),
        )
        report.results = [
            SampleResult(
                data_type="quote",
                data_type_label="实时行情",
                symbol="600519.SH",
                status=SourceFreshnessStatus.HAS_DATA,
            )
        ]
        report.summary = _compute_summary(report.results)
        md = render_live_sampling_report(report)
        # Check no common secret patterns
        secret_patterns = ["api_key", "apikey", "secret", "password", "token=", "bearer "]
        md_lower = md.lower()
        for pattern in secret_patterns:
            assert pattern not in md_lower, f"Found secret pattern '{pattern}' in report"

    def test_special_notes_section(self):
        report = LiveSamplingReport(report_date="2026-06-18")
        report.results = []
        report.summary = _compute_summary([])
        md = render_live_sampling_report(report)
        assert "Fund Flow" in md or "主力资金" in md
        assert "LHB" in md or "龙虎榜" in md
        assert "NORMAL_NO_DATA" in md

    def test_issues_section_for_failures(self):
        report = LiveSamplingReport(
            report_date="2026-06-18",
            samples=[SampleStock(symbol="600519.SH", name="贵州茅台")],
        )
        report.results = [
            SampleResult(
                data_type="fund_flow",
                data_type_label="个股资金流",
                symbol="600519.SH",
                symbol_name="贵州茅台",
                status=SourceFreshnessStatus.FAILED,
                error="ConnectionError",
            )
        ]
        report.summary = _compute_summary(report.results)
        md = render_live_sampling_report(report)
        assert "Issues" in md or "RED" in md


# ── Test file I/O ─────────────────────────────────────────────────────


class TestFileIO:
    def test_save_report(self):
        report = LiveSamplingReport(
            report_date="2026-06-18",
            generated_at="2026-06-18 10:00:00",
        )
        report.results = [
            SampleResult(data_type="quote", data_type_label="实时行情", symbol="600519.SH", status=SourceFreshnessStatus.HAS_DATA)
        ]
        report.summary = _compute_summary(report.results)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_live_sampling_report(report, output_dir=tmpdir)
            assert os.path.exists(path)
            assert "2026-06-18-live-smoke.md" in path
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "[DATA-019]" in content

    def test_save_custom_filename(self):
        report = LiveSamplingReport(report_date="2026-06-18")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_live_sampling_report(report, output_dir=tmpdir, filename="custom.md")
            assert "custom.md" in path

    def test_find_latest_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create reports for different dates
            for date in ["2026-06-15", "2026-06-18", "2026-06-10"]:
                path = os.path.join(tmpdir, f"{date}-live-smoke.md")
                with open(path, "w") as f:
                    f.write("# stub")

            latest = find_latest_live_sampling_report(tmpdir)
            assert latest is not None
            assert "2026-06-18-live-smoke.md" in latest

    def test_find_latest_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert find_latest_live_sampling_report(tmpdir) is None

    def test_find_latest_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Non-matching file
            with open(os.path.join(tmpdir, "freshness-2026-06-18.md"), "w") as f:
                f.write("# stub")
            with open(os.path.join(tmpdir, "live-smoke-2026-06-18.md"), "w") as f:
                f.write("# stub")
            assert find_latest_live_sampling_report(tmpdir) is None


class TestNightlySection:
    def test_build_section_with_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = LiveSamplingReport(report_date="2026-06-18")
            save_live_sampling_report(report, output_dir=tmpdir)
            section = build_sampling_section_for_nightly_report(reports_dir=tmpdir)
            assert "[DATA-019]" in section

    def test_build_section_without_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            section = build_sampling_section_for_nightly_report(reports_dir=tmpdir)
            assert "未生成" in section or "[DATA-019]" in section


# ── Test env gate helper ──────────────────────────────────────────────


class TestEnvGate:
    def test_is_enabled_false_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_LIVE_ENV, None)
            assert is_live_sampling_enabled() is False

    def test_is_enabled_true(self):
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            assert is_live_sampling_enabled() is True


# ── Test fixture responses ────────────────────────────────────────────


class TestSampleResponses:
    def test_all_responses_have_keys(self):
        required = [
            "quote_ok",
            "fund_flow_ok",
            "fund_flow_akshare_fail",
            "fund_flow_astock_fallback",
            "lhb_normal_no_data",
            "lhb_has_data",
            "lhb_failed",
            "notice_ok",
            "rating_ok",
            "buyback_normal_no_data",
            "buyback_has_data",
            "report_ok",
            "rate_limited",
        ]
        for key in required:
            assert key in ALL_SAMPLE_RESPONSES, f"Missing fixture: {key}"

    def test_fund_flow_ok_has_wanyuan(self):
        assert "万元" in ALL_SAMPLE_RESPONSES["fund_flow_ok"]

    def test_lhb_normal_no_data_marker(self):
        assert "LHB_NORMAL_NO_DATA" in ALL_SAMPLE_RESPONSES["lhb_normal_no_data"]

    def test_akshare_fail_has_error_pattern(self):
        text = ALL_SAMPLE_RESPONSES["fund_flow_akshare_fail"]
        assert "ProxyError" in text or "获取失败" in text


# ── Test diagnosis builder ────────────────────────────────────────────


class TestBuildDiagnosis:
    def test_has_data_diagnosis(self):
        result = SampleResult(
            data_type="fund_flow",
            data_type_label="个股资金流",
            status=SourceFreshnessStatus.HAS_DATA,
            record_count=20,
            actual_vendor="cn_astock",
        )
        dt_def = {"special_note": "主力资金一般应可取"}
        diag = _build_sample_diagnosis(result, dt_def)
        assert "资金流" in diag
        assert "正常" in diag
        assert "注意" in diag

    def test_normal_no_data_diagnosis(self):
        result = SampleResult(
            data_type="lhb",
            data_type_label="龙虎榜",
            status=SourceFreshnessStatus.NORMAL_NO_DATA,
        )
        dt_def = {"special_note": "龙虎榜无触发是 NORMAL_NO_DATA"}
        diag = _build_sample_diagnosis(result, dt_def)
        assert "无数据" in diag or "正常" in diag
        assert "NORMAL_NO_DATA" in diag

    def test_failed_diagnosis(self):
        result = SampleResult(
            data_type="fund_flow",
            data_type_label="个股资金流",
            status=SourceFreshnessStatus.FAILED,
            error="ConnectionError",
        )
        dt_def = {"special_note": ""}
        diag = _build_sample_diagnosis(result, dt_def)
        assert "失败" in diag
        assert "ConnectionError" in diag
