# [DATA-018] source_freshness_report tests
"""Tests for source freshness & fallback visualization daily report."""

import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from tradingagents.dataflows.source_freshness_report import (
    SourceFreshnessStatus,
    SourceFreshnessEntry,
    SourceFreshnessReport,
    classify_source_status,
    run_source_freshness_report,
    render_source_freshness_report,
    save_source_freshness_report,
    find_latest_freshness_report,
    build_freshness_section_for_nightly_report,
    _is_stale_date,
    _detect_rate_limited,
    _detect_failed,
    _detect_normal_no_data,
    _build_summary,
    _inspect_entry,
    _catalog_only_entry,
    _build_diagnosis,
    SAMPLE_ALL_HEALTHY,
    SAMPLE_AKSHARE_FAIL_FALLBACK,
    SAMPLE_RATE_LIMITED,
    SAMPLE_STALE_DATA,
    SAMPLE_UNIT_UNVERIFIED,
    SAMPLE_INTERFACE_FAILED,
    ALL_FRESHNESS_SAMPLES,
)
from tradingagents.dataflows.source_catalog import (
    DataType,
    get_all_data_types,
    get_primary_source,
    get_fallback_chain,
)


# ── Test SourceFreshnessStatus ────────────────────────────────────────

class TestSourceFreshnessStatus:
    def test_all_six_statuses(self):
        assert len(SourceFreshnessStatus.ALL) == 6

    def test_has_data(self):
        assert SourceFreshnessStatus.HAS_DATA == "HAS_DATA"

    def test_normal_no_data(self):
        assert SourceFreshnessStatus.NORMAL_NO_DATA == "NORMAL_NO_DATA"

    def test_stale(self):
        assert SourceFreshnessStatus.STALE == "STALE"

    def test_failed(self):
        assert SourceFreshnessStatus.FAILED == "FAILED"

    def test_rate_limited(self):
        assert SourceFreshnessStatus.RATE_LIMITED == "RATE_LIMITED"

    def test_unit_unverified(self):
        assert SourceFreshnessStatus.UNIT_UNVERIFIED == "UNIT_UNVERIFIED"

    def test_traffic_light_mapping(self):
        assert SourceFreshnessStatus.TRAFFIC_LIGHT[SourceFreshnessStatus.HAS_DATA] == "green"
        assert SourceFreshnessStatus.TRAFFIC_LIGHT[SourceFreshnessStatus.NORMAL_NO_DATA] == "green"
        assert SourceFreshnessStatus.TRAFFIC_LIGHT[SourceFreshnessStatus.STALE] == "yellow"
        assert SourceFreshnessStatus.TRAFFIC_LIGHT[SourceFreshnessStatus.FAILED] == "red"
        assert SourceFreshnessStatus.TRAFFIC_LIGHT[SourceFreshnessStatus.RATE_LIMITED] == "red"
        assert SourceFreshnessStatus.TRAFFIC_LIGHT[SourceFreshnessStatus.UNIT_UNVERIFIED] == "yellow"

    def test_label_cn(self):
        assert SourceFreshnessStatus.LABEL_CN[SourceFreshnessStatus.HAS_DATA] == "正常"
        assert SourceFreshnessStatus.LABEL_CN[SourceFreshnessStatus.RATE_LIMITED] == "限流"
        assert SourceFreshnessStatus.LABEL_CN[SourceFreshnessStatus.UNIT_UNVERIFIED] == "单位未校验"

    def test_all_statuses_have_traffic_light(self):
        for s in SourceFreshnessStatus.ALL:
            assert s in SourceFreshnessStatus.TRAFFIC_LIGHT

    def test_all_statuses_have_label_cn(self):
        for s in SourceFreshnessStatus.ALL:
            assert s in SourceFreshnessStatus.LABEL_CN


# ── Test detection helpers ────────────────────────────────────────────

class TestDetectionHelpers:
    def test_is_stale_date_old(self):
        old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        assert _is_stale_date(old) is True

    def test_is_stale_date_recent(self):
        today = datetime.now().strftime("%Y-%m-%d")
        assert _is_stale_date(today) is False

    def test_is_stale_date_empty(self):
        assert _is_stale_date("") is False

    def test_is_stale_date_invalid(self):
        assert _is_stale_date("invalid") is False

    def test_is_stale_date_boundary(self):
        boundary = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
        assert _is_stale_date(boundary) is True

    def test_is_stale_date_within_boundary(self):
        within = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        assert _is_stale_date(within) is False

    def test_detect_rate_limited_429(self):
        assert _detect_rate_limited("HTTPError 429: Too Many Requests") is True

    def test_detect_rate_limited_chinese(self):
        assert _detect_rate_limited("请求过于频繁") is True

    def test_detect_rate_limited_rate_limit(self):
        assert _detect_rate_limited("rate limit exceeded") is True

    def test_detect_rate_limited_no_match(self):
        assert _detect_rate_limited("ConnectionError") is False

    def test_detect_rate_limited_empty(self):
        assert _detect_rate_limited("") is False

    def test_detect_failed_proxy(self):
        assert _detect_failed("ProxyError: Cannot connect") is True

    def test_detect_failed_connection(self):
        assert _detect_failed("ConnectionError") is True

    def test_detect_failed_timeout(self):
        assert _detect_failed("TimeoutError") is True

    def test_detect_failed_no_match(self):
        assert _detect_failed("正常数据") is False

    def test_detect_normal_no_data_pattern(self):
        assert _detect_normal_no_data("NORMAL_NO_DATA") is True

    def test_detect_normal_no_data_chinese(self):
        assert _detect_normal_no_data("无数据") is True

    def test_detect_normal_no_data_no_match(self):
        assert _detect_normal_no_data("有数据") is False


# ── Test classify_source_status ───────────────────────────────────────

class TestClassifySourceStatus:
    def test_rate_limited_priority(self):
        """RATE_LIMITED should be detected before FAILED."""
        result = classify_source_status(
            status="FAILED",
            error="HTTPError 429: Too Many Requests",
        )
        assert result == SourceFreshnessStatus.RATE_LIMITED

    def test_failed_status(self):
        result = classify_source_status(status="FAILED", error="ProxyError")
        assert result == SourceFreshnessStatus.FAILED

    def test_failed_no_status(self):
        result = classify_source_status(raw_value="获取失败：ProxyError")
        assert result == SourceFreshnessStatus.FAILED

    def test_not_queried_is_normal_no_data(self):
        result = classify_source_status(status="NOT_QUERIED")
        assert result == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_normal_no_data_explicit(self):
        result = classify_source_status(status="NORMAL_NO_DATA")
        assert result == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_has_data_with_verified_unit(self):
        today = datetime.now().strftime("%Y-%m-%d")
        result = classify_source_status(
            status="HAS_DATA",
            as_of=today,
            unit_verified=True,
        )
        assert result == SourceFreshnessStatus.HAS_DATA

    def test_stale_date(self):
        old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        result = classify_source_status(
            status="HAS_DATA",
            as_of=old,
            unit_verified=True,
        )
        assert result == SourceFreshnessStatus.STALE

    def test_unit_unverified(self):
        today = datetime.now().strftime("%Y-%m-%d")
        result = classify_source_status(
            status="HAS_DATA",
            as_of=today,
            unit_verified=False,
        )
        assert result == SourceFreshnessStatus.UNIT_UNVERIFIED

    def test_empty_value_no_status(self):
        result = classify_source_status(raw_value="", status="")
        assert result == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_none_value(self):
        result = classify_source_status(raw_value=None, status="")
        assert result == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_rate_limited_over_failed_pattern(self):
        """Even with failure patterns, rate-limited error takes priority."""
        result = classify_source_status(
            error="429: 请求过于频繁",
        )
        assert result == SourceFreshnessStatus.RATE_LIMITED


# ── Test SourceFreshnessEntry ─────────────────────────────────────────

class TestSourceFreshnessEntry:
    def test_traffic_light_property_green(self):
        e = SourceFreshnessEntry(status=SourceFreshnessStatus.HAS_DATA)
        assert e.traffic_light == "green"

    def test_traffic_light_property_red(self):
        e = SourceFreshnessEntry(status=SourceFreshnessStatus.FAILED)
        assert e.traffic_light == "red"

    def test_traffic_light_property_yellow(self):
        e = SourceFreshnessEntry(status=SourceFreshnessStatus.STALE)
        assert e.traffic_light == "yellow"

    def test_label_cn_property(self):
        e = SourceFreshnessEntry(status=SourceFreshnessStatus.HAS_DATA)
        assert e.label_cn == "正常"

    def test_to_dict(self):
        e = SourceFreshnessEntry(
            data_type="fund_flow",
            label="个股资金流",
            status=SourceFreshnessStatus.HAS_DATA,
            primary_vendor="cn_akshare",
            unit="万元",
            unit_verified=True,
        )
        d = e.to_dict()
        assert d["data_type"] == "fund_flow"
        assert d["status"] == "HAS_DATA"
        assert d["traffic_light"] == "green"
        assert d["status_label_cn"] == "正常"


# ── Test _inspect_entry ───────────────────────────────────────────────

class TestInspectEntry:
    def test_dict_entry_has_data(self):
        today = datetime.now().strftime("%Y-%m-%d")
        raw = {
            "raw": "资金流数据...",
            "status": "HAS_DATA",
            "unit": "万元",
            "unit_verified": True,
            "vendor": "cn_akshare",
            "as_of": today,
            "record_count": 20,
        }
        entry = _inspect_entry("fund_flow_individual", raw, "fund_flow")
        assert entry.status == SourceFreshnessStatus.HAS_DATA
        assert entry.actual_vendor == "cn_akshare"
        assert entry.unit == "万元"

    def test_dict_entry_failed(self):
        raw = {
            "raw": "获取失败",
            "status": "FAILED",
            "error": "ProxyError",
        }
        entry = _inspect_entry("fund_flow_individual", raw, "fund_flow")
        assert entry.status == SourceFreshnessStatus.FAILED
        assert "ProxyError" in entry.error

    def test_dict_entry_rate_limited(self):
        raw = {
            "raw": "获取失败",
            "status": "FAILED",
            "error": "429: Too Many Requests",
        }
        entry = _inspect_entry("stock_data", raw, "ohlcv")
        assert entry.status == SourceFreshnessStatus.RATE_LIMITED

    def test_dict_entry_normal_no_data(self):
        raw = {
            "raw": "LHB_NORMAL_NO_DATA",
            "status": "NORMAL_NO_DATA",
            "vendor": "cn_akshare",
        }
        entry = _inspect_entry("lhb", raw, "lhb")
        assert entry.status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_dict_entry_stale(self):
        old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        raw = {
            "raw": "数据...",
            "status": "HAS_DATA",
            "unit": "元/股, 股",
            "unit_verified": True,
            "vendor": "cn_akshare",
            "as_of": old,
        }
        entry = _inspect_entry("stock_data", raw, "ohlcv")
        assert entry.status == SourceFreshnessStatus.STALE

    def test_dict_entry_unit_unverified(self):
        today = datetime.now().strftime("%Y-%m-%d")
        raw = {
            "raw": "数据...",
            "status": "HAS_DATA",
            "unit": "",
            "unit_verified": False,
            "vendor": "cn_astock",
            "as_of": today,
        }
        entry = _inspect_entry("stock_data", raw, "ohlcv")
        assert entry.status == SourceFreshnessStatus.UNIT_UNVERIFIED

    def test_dict_entry_fallback(self):
        today = datetime.now().strftime("%Y-%m-%d")
        raw = {
            "raw": "资金流数据...",
            "status": "HAS_DATA",
            "unit": "万元",
            "unit_verified": True,
            "vendor": "cn_astock",
            "endpoint": "push2his.eastmoney.com/fflow",
            "fallback_from": "cn_akshare",
            "as_of": today,
        }
        entry = _inspect_entry("fund_flow_individual", raw, "fund_flow")
        assert entry.status == SourceFreshnessStatus.HAS_DATA
        assert entry.is_fallback is True
        assert entry.actual_vendor == "cn_astock"

    def test_none_entry(self):
        entry = _inspect_entry("stock_data", None, "ohlcv")
        assert entry.status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_string_entry_has_data(self):
        entry = _inspect_entry("news", "这是一段很长的新闻内容标题超过二十个字符的测试字符串数据", "news")
        assert entry.status == SourceFreshnessStatus.HAS_DATA

    def test_string_entry_failed(self):
        entry = _inspect_entry("stock_data", "数据获取失败：ProxyError", "ohlcv")
        assert entry.status == SourceFreshnessStatus.FAILED

    def test_string_entry_short_normal_no_data(self):
        entry = _inspect_entry("lhb", "空", "lhb")
        assert entry.status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_catalog_info_populated(self):
        raw = {
            "raw": "数据...",
            "status": "HAS_DATA",
            "unit": "万元",
            "unit_verified": True,
            "vendor": "cn_akshare",
            "as_of": datetime.now().strftime("%Y-%m-%d"),
        }
        entry = _inspect_entry("fund_flow_individual", raw, "fund_flow")
        assert entry.primary_vendor != ""
        assert len(entry.fallback_chain) >= 1


# ── Test _catalog_only_entry ──────────────────────────────────────────

class TestCatalogOnlyEntry:
    def test_basic(self):
        entry = _catalog_only_entry("fund_flow")
        assert entry.data_type == "fund_flow"
        assert entry.status == SourceFreshnessStatus.NORMAL_NO_DATA
        assert entry.primary_vendor != ""

    def test_has_fallback_chain(self):
        entry = _catalog_only_entry("ohlcv")
        assert len(entry.fallback_chain) >= 1

    def test_has_label(self):
        entry = _catalog_only_entry("lhb")
        assert entry.label == "龙虎榜"

    def test_diagnosis_present(self):
        entry = _catalog_only_entry("news")
        assert "未查询" in entry.diagnosis


# ── Test _build_summary ───────────────────────────────────────────────

class TestBuildSummary:
    def test_all_green(self):
        entries = [
            SourceFreshnessEntry(data_type="a", status=SourceFreshnessStatus.HAS_DATA),
            SourceFreshnessEntry(data_type="b", status=SourceFreshnessStatus.NORMAL_NO_DATA),
        ]
        s = _build_summary(entries)
        assert s["total_sources"] == 2
        assert s["green_count"] == 2
        assert s["yellow_count"] == 0
        assert s["red_count"] == 0
        assert s["all_green"] is True

    def test_has_failures(self):
        entries = [
            SourceFreshnessEntry(data_type="a", status=SourceFreshnessStatus.HAS_DATA),
            SourceFreshnessEntry(data_type="b", status=SourceFreshnessStatus.FAILED),
        ]
        s = _build_summary(entries)
        assert s["has_failures"] is True
        assert s["red_count"] == 1
        assert s["all_green"] is False

    def test_has_warnings(self):
        entries = [
            SourceFreshnessEntry(data_type="a", status=SourceFreshnessStatus.HAS_DATA),
            SourceFreshnessEntry(data_type="b", status=SourceFreshnessStatus.STALE),
        ]
        s = _build_summary(entries)
        assert s["has_warnings"] is True
        assert s["yellow_count"] == 1
        assert s["all_green"] is False

    def test_fallback_count(self):
        entries = [
            SourceFreshnessEntry(data_type="a", status=SourceFreshnessStatus.HAS_DATA, is_fallback=True),
            SourceFreshnessEntry(data_type="b", status=SourceFreshnessStatus.HAS_DATA, is_fallback=False),
        ]
        s = _build_summary(entries)
        assert s["fallback_triggered_count"] == 1

    def test_high_risk_sources(self):
        entries = [
            SourceFreshnessEntry(data_type="fund_flow", status=SourceFreshnessStatus.HAS_DATA, rate_limit_risk="high"),
            SourceFreshnessEntry(data_type="news", status=SourceFreshnessStatus.HAS_DATA, rate_limit_risk="medium"),
        ]
        s = _build_summary(entries)
        assert "fund_flow" in s["high_rate_limit_risk_sources"]
        assert "news" not in s["high_rate_limit_risk_sources"]

    def test_status_counts(self):
        entries = [
            SourceFreshnessEntry(data_type="a", status=SourceFreshnessStatus.HAS_DATA),
            SourceFreshnessEntry(data_type="b", status=SourceFreshnessStatus.FAILED),
            SourceFreshnessEntry(data_type="c", status=SourceFreshnessStatus.FAILED),
        ]
        s = _build_summary(entries)
        assert s["status_counts"][SourceFreshnessStatus.HAS_DATA] == 1
        assert s["status_counts"][SourceFreshnessStatus.FAILED] == 2


# ── Test run_source_freshness_report ──────────────────────────────────

class TestRunSourceFreshnessReport:
    def test_empty_raw_evidence(self):
        report = run_source_freshness_report({})
        assert len(report.entries) > 0
        # All entries should be NORMAL_NO_DATA or catalog-only
        for e in report.entries:
            assert e.status in (SourceFreshnessStatus.NORMAL_NO_DATA,)
        assert report.summary["total_sources"] > 0

    def test_with_raw_evidence(self):
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY, symbol="600519.SH")
        assert report.symbol == "600519.SH"
        assert len(report.entries) > 0
        # Should have entries for ohlcv, fund_flow, lhb, news, notice
        data_types = [e.data_type for e in report.entries]
        assert "ohlcv" in data_types
        assert "fund_flow" in data_types
        assert "lhb" in data_types

    def test_all_data_types_covered(self):
        report = run_source_freshness_report({})
        report_types = {e.data_type for e in report.entries}
        catalog_types = set(get_all_data_types())
        # Report should cover all catalog data types
        assert catalog_types.issubset(report_types)

    def test_report_has_date(self):
        report = run_source_freshness_report({})
        assert report.report_date != ""
        assert len(report.report_date) == 10  # YYYY-MM-DD

    def test_report_has_generated_at(self):
        report = run_source_freshness_report({})
        assert report.generated_at != ""

    def test_report_has_summary(self):
        report = run_source_freshness_report({})
        assert "total_sources" in report.summary
        assert "green_count" in report.summary
        assert "red_count" in report.summary

    def test_specific_data_types_filter(self):
        report = run_source_freshness_report({}, data_types=["fund_flow", "lhb"])
        data_types = [e.data_type for e in report.entries]
        assert set(data_types) == {"fund_flow", "lhb"}

    def test_extra_evidence_keys_included(self):
        raw = {"unknown_key": {"status": "HAS_DATA", "vendor": "test"}}
        report = run_source_freshness_report(raw)
        # Should still work even with unknown keys
        assert len(report.entries) > 0


# ── Test sample fixtures ──────────────────────────────────────────────

class TestSampleFixtures:
    def test_all_healthy_sample(self):
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY)
        ohlcv = [e for e in report.entries if e.data_type == "ohlcv"]
        assert len(ohlcv) == 1
        assert ohlcv[0].status == SourceFreshnessStatus.HAS_DATA

        fund_flow = [e for e in report.entries if e.data_type == "fund_flow"]
        assert len(fund_flow) == 1
        assert fund_flow[0].status == SourceFreshnessStatus.HAS_DATA

        lhb = [e for e in report.entries if e.data_type == "lhb"]
        assert len(lhb) == 1
        assert lhb[0].status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_akshare_fail_fallback_sample(self):
        report = run_source_freshness_report(SAMPLE_AKSHARE_FAIL_FALLBACK)
        fund_flow = [e for e in report.entries if e.data_type == "fund_flow"]
        assert len(fund_flow) == 1
        assert fund_flow[0].status == SourceFreshnessStatus.HAS_DATA
        assert fund_flow[0].is_fallback is True
        assert fund_flow[0].actual_vendor == "cn_astock"

        lhb = [e for e in report.entries if e.data_type == "lhb"]
        assert lhb[0].status == SourceFreshnessStatus.NORMAL_NO_DATA

    def test_rate_limited_sample(self):
        report = run_source_freshness_report(SAMPLE_RATE_LIMITED)
        ohlcv = [e for e in report.entries if e.data_type == "ohlcv"]
        assert ohlcv[0].status == SourceFreshnessStatus.RATE_LIMITED

        fund_flow = [e for e in report.entries if e.data_type == "fund_flow"]
        assert fund_flow[0].status == SourceFreshnessStatus.RATE_LIMITED

    def test_stale_data_sample(self):
        report = run_source_freshness_report(SAMPLE_STALE_DATA)
        ohlcv = [e for e in report.entries if e.data_type == "ohlcv"]
        assert ohlcv[0].status == SourceFreshnessStatus.STALE

        fund_flow = [e for e in report.entries if e.data_type == "fund_flow"]
        assert fund_flow[0].status == SourceFreshnessStatus.STALE

    def test_unit_unverified_sample(self):
        report = run_source_freshness_report(SAMPLE_UNIT_UNVERIFIED)
        ohlcv = [e for e in report.entries if e.data_type == "ohlcv"]
        assert ohlcv[0].status == SourceFreshnessStatus.UNIT_UNVERIFIED

    def test_interface_failed_sample(self):
        report = run_source_freshness_report(SAMPLE_INTERFACE_FAILED)
        ohlcv = [e for e in report.entries if e.data_type == "ohlcv"]
        assert ohlcv[0].status == SourceFreshnessStatus.FAILED

        fund_flow = [e for e in report.entries if e.data_type == "fund_flow"]
        assert fund_flow[0].status == SourceFreshnessStatus.FAILED

    def test_all_samples_dict(self):
        assert len(ALL_FRESHNESS_SAMPLES) == 6
        assert "ALL_HEALTHY" in ALL_FRESHNESS_SAMPLES
        assert "AKSHARE_FAIL_FALLBACK" in ALL_FRESHNESS_SAMPLES
        assert "RATE_LIMITED" in ALL_FRESHNESS_SAMPLES
        assert "STALE_DATA" in ALL_FRESHNESS_SAMPLES
        assert "UNIT_UNVERIFIED" in ALL_FRESHNESS_SAMPLES
        assert "INTERFACE_FAILED" in ALL_FRESHNESS_SAMPLES


# ── Test render_source_freshness_report ───────────────────────────────

class TestRenderSourceFreshnessReport:
    def test_basic_render(self):
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY, symbol="600519.SH")
        md = render_source_freshness_report(report)
        assert "# A-Share Source Freshness & Fallback Report" in md
        assert "600519.SH" in md
        assert "## Summary" in md
        assert "## Source Status" in md

    def test_render_with_failures(self):
        report = run_source_freshness_report(SAMPLE_INTERFACE_FAILED)
        md = render_source_freshness_report(report)
        assert "## Issues & Warnings" in md

    def test_render_with_fallback(self):
        report = run_source_freshness_report(SAMPLE_AKSHARE_FAIL_FALLBACK)
        md = render_source_freshness_report(report)
        assert "## Fallback Details" in md

    def test_render_contains_data_tag(self):
        report = run_source_freshness_report({})
        md = render_source_freshness_report(report)
        assert "[DATA-018]" in md

    def test_render_contains_traffic_light_status(self):
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY)
        md = render_source_freshness_report(report)
        # Should show status label
        assert "正常" in md

    def test_render_empty_report(self):
        report = SourceFreshnessReport(
            report_date="2026-06-14",
            generated_at="2026-06-14 12:00:00",
        )
        md = render_source_freshness_report(report)
        assert "## Source Status" in md
        assert "## Summary" in md


# ── Test save/find report ─────────────────────────────────────────────

class TestSaveFindReport:
    def test_save_report(self, tmp_path):
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY)
        path = save_source_freshness_report(report, str(tmp_path))
        assert os.path.exists(path)
        assert path.endswith(".md")

    def test_save_creates_directory(self, tmp_path):
        new_dir = os.path.join(str(tmp_path), "subdir")
        report = run_source_freshness_report({})
        path = save_source_freshness_report(report, new_dir)
        assert os.path.exists(path)
        assert os.path.exists(new_dir)

    def test_save_custom_filename(self, tmp_path):
        report = run_source_freshness_report({})
        path = save_source_freshness_report(report, str(tmp_path), filename="custom.md")
        assert path.endswith("custom.md")

    def test_save_with_symbol(self, tmp_path):
        report = run_source_freshness_report({}, symbol="600519.SH")
        path = save_source_freshness_report(report, str(tmp_path))
        assert "600519.SH" in path

    def test_find_latest_report(self, tmp_path):
        report1 = SourceFreshnessReport(report_date="2026-01-01", generated_at="2026-01-01 10:00:00")
        report2 = SourceFreshnessReport(report_date="2026-06-14", generated_at="2026-06-14 10:00:00")
        save_source_freshness_report(report1, str(tmp_path))
        save_source_freshness_report(report2, str(tmp_path))

        latest = find_latest_freshness_report(str(tmp_path))
        assert latest is not None
        assert "2026-06-14" in latest

    def test_find_latest_no_directory(self):
        assert find_latest_freshness_report("/nonexistent/path") is None

    def test_find_latest_empty_directory(self, tmp_path):
        assert find_latest_freshness_report(str(tmp_path)) is None


# ── Test nightly report integration ───────────────────────────────────

class TestNightlyReportIntegration:
    def test_build_section_with_raw_evidence(self):
        section = build_freshness_section_for_nightly_report(
            raw_evidence=SAMPLE_ALL_HEALTHY,
        )
        assert "数据源新鲜度报告" in section
        assert "[DATA-018]" in section

    def test_build_section_without_data(self):
        section = build_freshness_section_for_nightly_report(
            reports_dir="/nonexistent",
            raw_evidence={},
        )
        assert "数据源新鲜度报告" in section

    def test_build_section_from_saved_report(self, tmp_path):
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY)
        save_source_freshness_report(report, str(tmp_path))
        section = build_freshness_section_for_nightly_report(
            reports_dir=str(tmp_path),
        )
        assert "[DATA-018]" in section

    def test_section_contains_status_summary(self):
        section = build_freshness_section_for_nightly_report(
            raw_evidence=SAMPLE_INTERFACE_FAILED,
        )
        assert "需关注" in section or "ALL GREEN" in section or "HAS" in section


# ── Test acceptance criteria from task ────────────────────────────────

class TestAcceptanceCriteria:
    """Fixture-based testing: AKShare fail, cn_astock fallback success,
    LHB normal no-data, rate-limited, stale, unit-unverified."""

    def test_akshare_fail_cn_astock_fallback_success(self):
        """Fixture: AKShare fails, cn_astock fallback succeeds."""
        report = run_source_freshness_report(SAMPLE_AKSHARE_FAIL_FALLBACK)
        fund_flow = [e for e in report.entries if e.data_type == "fund_flow"][0]
        assert fund_flow.status == SourceFreshnessStatus.HAS_DATA
        assert fund_flow.actual_vendor == "cn_astock"
        assert fund_flow.is_fallback is True
        assert "cn_astock" in fund_flow.fallback_chain

    def test_lhb_normal_no_data(self):
        """Fixture: LHB queried but no data (normal for non-anomaly days)."""
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY)
        lhb = [e for e in report.entries if e.data_type == "lhb"][0]
        assert lhb.status == SourceFreshnessStatus.NORMAL_NO_DATA
        # Normal no-data should be green, not red
        assert lhb.traffic_light == "green"

    def test_rate_limited_detection(self):
        """Rate-limited (429) should be classified distinctly from FAILED."""
        report = run_source_freshness_report(SAMPLE_RATE_LIMITED)
        for e in report.entries:
            if e.data_type in ("ohlcv", "fund_flow"):
                assert e.status == SourceFreshnessStatus.RATE_LIMITED
                assert e.traffic_light == "red"

    def test_no_plaintext_secrets_in_report(self):
        """Report must not contain any API keys or secrets."""
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY)
        md = render_source_freshness_report(report)
        # Should not contain common key patterns
        assert "api_key" not in md.lower()
        assert "bearer " not in md.lower()
        assert "secret" not in md.lower()

    def test_report_has_all_six_status_labels(self):
        """All 6 status labels should be possible in the report."""
        # Create entries with each status
        for status in SourceFreshnessStatus.ALL:
            entry = SourceFreshnessEntry(status=status, data_type="test")
            assert entry.label_cn in SourceFreshnessStatus.LABEL_CN.values()

    def test_data_health_api_response_format(self):
        """The report's to_dict should produce API-compatible output."""
        report = run_source_freshness_report(SAMPLE_ALL_HEALTHY)
        d = report.to_dict()
        assert "entries" in d
        assert "summary" in d
        assert "report_date" in d
        assert "generated_at" in d
        for e in d["entries"]:
            assert "status" in e
            assert "traffic_light" in e
            assert "status_label_cn" in e
            assert "primary_vendor" in e
            assert "fallback_chain" in e

    def test_freshness_covers_all_key_sources(self):
        """Report should cover all key data sources from source_catalog."""
        report = run_source_freshness_report({})
        report_types = {e.data_type for e in report.entries}
        # Check at least these key sources
        expected = {
            "ohlcv", "realtime_quotes", "fund_flow", "board_fund_flow",
            "lhb", "margin_trading", "notice", "report", "rating",
            "news", "global_news", "financials", "insider",
            "zt_pool", "hot_stocks", "buyback",
        }
        missing = expected - report_types
        assert not missing, f"Missing data types: {missing}"


# ── Test _build_diagnosis ─────────────────────────────────────────────

class TestBuildDiagnosis:
    def test_has_data_diagnosis(self):
        entry = SourceFreshnessEntry(
            label="个股资金流",
            status=SourceFreshnessStatus.HAS_DATA,
            actual_vendor="cn_akshare",
            record_count=20,
        )
        diag = _build_diagnosis(entry)
        assert "正常" in diag
        assert "cn_akshare" in diag

    def test_failed_diagnosis(self):
        entry = SourceFreshnessEntry(
            label="行情/OHLCV",
            status=SourceFreshnessStatus.FAILED,
            error="ProxyError",
        )
        diag = _build_diagnosis(entry)
        assert "失败" in diag
        assert "ProxyError" in diag

    def test_rate_limited_diagnosis(self):
        entry = SourceFreshnessEntry(
            label="个股资金流",
            status=SourceFreshnessStatus.RATE_LIMITED,
            rate_limit_risk="high",
        )
        diag = _build_diagnosis(entry)
        assert "限流" in diag

    def test_stale_diagnosis(self):
        entry = SourceFreshnessEntry(
            label="行情/OHLCV",
            status=SourceFreshnessStatus.STALE,
            as_of="2026-01-01",
        )
        diag = _build_diagnosis(entry)
        assert "过期" in diag
        assert "2026-01-01" in diag

    def test_unit_unverified_diagnosis(self):
        entry = SourceFreshnessEntry(
            label="行情/OHLCV",
            status=SourceFreshnessStatus.UNIT_UNVERIFIED,
            unit="",
        )
        diag = _build_diagnosis(entry)
        assert "单位未校验" in diag

    def test_normal_no_data_diagnosis(self):
        entry = SourceFreshnessEntry(
            label="龙虎榜",
            status=SourceFreshnessStatus.NORMAL_NO_DATA,
        )
        diag = _build_diagnosis(entry)
        assert "无数据" in diag

    def test_fallback_diagnosis(self):
        entry = SourceFreshnessEntry(
            label="个股资金流",
            status=SourceFreshnessStatus.HAS_DATA,
            actual_vendor="cn_astock",
            is_fallback=True,
            fallback_chain=["cn_akshare", "cn_astock"],
            record_count=20,
        )
        diag = _build_diagnosis(entry)
        assert "fallback" in diag


# ── Test source catalog integration ───────────────────────────────────

class TestSourceCatalogIntegration:
    def test_primary_vendor_populated(self):
        report = run_source_freshness_report({})
        for e in report.entries:
            if e.data_type in ("ohlcv", "fund_flow", "lhb"):
                assert e.primary_vendor != ""

    def test_fallback_chain_populated(self):
        report = run_source_freshness_report({})
        for e in report.entries:
            if e.data_type in ("ohlcv", "fund_flow", "lhb"):
                assert len(e.fallback_chain) >= 1

    def test_unit_from_catalog(self):
        report = run_source_freshness_report({})
        fund_flow = [e for e in report.entries if e.data_type == "fund_flow"][0]
        assert fund_flow.unit != ""

    def test_rate_limit_risk_from_catalog(self):
        report = run_source_freshness_report({})
        fund_flow = [e for e in report.entries if e.data_type == "fund_flow"][0]
        assert fund_flow.rate_limit_risk != ""


# ── Test API service function ─────────────────────────────────────────

class TestAPIServiceFunction:
    def test_get_source_freshness_basic(self):
        from api.services.tradeflow_service import get_source_freshness
        result = get_source_freshness()
        assert result["status"] == "ok"
        assert "entries" in result
        assert "summary" in result
        assert "runtime_tier_meta" in result

    def test_get_source_freshness_with_symbol(self):
        from api.services.tradeflow_service import get_source_freshness
        result = get_source_freshness(symbol="600519.SH")
        assert result["symbol"] == "600519.SH"

    def test_get_source_freshness_with_evidence(self):
        from api.services.tradeflow_service import get_source_freshness
        result = get_source_freshness(raw_evidence=SAMPLE_ALL_HEALTHY)
        assert len(result["entries"]) > 0

    def test_get_source_freshness_runtime_tier(self):
        from api.services.tradeflow_service import get_source_freshness
        result = get_source_freshness()
        meta = result["runtime_tier_meta"]
        assert meta["runtime_tier"] == "FAST_RADAR"

    def test_entries_have_traffic_light(self):
        from api.services.tradeflow_service import get_source_freshness
        result = get_source_freshness(raw_evidence=SAMPLE_ALL_HEALTHY)
        for e in result["entries"]:
            assert "traffic_light" in e
            assert e["traffic_light"] in ("green", "yellow", "red")

    def test_entries_have_status_label_cn(self):
        from api.services.tradeflow_service import get_source_freshness
        result = get_source_freshness(raw_evidence=SAMPLE_ALL_HEALTHY)
        for e in result["entries"]:
            assert "status_label_cn" in e


# ── Test schema validation ────────────────────────────────────────────

class TestSchemaValidation:
    def test_source_freshness_entry_item_schema(self):
        from api.tradeflow_schemas import SourceFreshnessEntryItem
        item = SourceFreshnessEntryItem(
            data_type="fund_flow",
            label="个股资金流",
            status="HAS_DATA",
            status_label_cn="正常",
            traffic_light="green",
        )
        assert item.data_type == "fund_flow"
        assert item.status == "HAS_DATA"

    def test_source_freshness_summary_schema(self):
        from api.tradeflow_schemas import SourceFreshnessSummary
        summary = SourceFreshnessSummary(
            total_sources=10,
            green_count=8,
            yellow_count=1,
            red_count=1,
        )
        assert summary.total_sources == 10
        assert summary.all_green is False

    def test_source_freshness_response_schema(self):
        from api.tradeflow_schemas import SourceFreshnessResponse
        resp = SourceFreshnessResponse(
            status="ok",
            report_date="2026-06-14",
        )
        assert resp.status == "ok"
        assert resp.report_date == "2026-06-14"

    def test_response_with_full_data(self):
        from api.tradeflow_schemas import SourceFreshnessResponse, SourceFreshnessEntryItem
        entry = SourceFreshnessEntryItem(
            data_type="fund_flow",
            status="HAS_DATA",
            traffic_light="green",
        )
        resp = SourceFreshnessResponse(
            entries=[entry],
        )
        assert len(resp.entries) == 1
        assert resp.entries[0].traffic_light == "green"


# ── Test SourceFreshnessReport to_dict ────────────────────────────────

class TestSourceFreshnessReportToDict:
    def test_basic_to_dict(self):
        report = SourceFreshnessReport(
            report_date="2026-06-14",
            generated_at="2026-06-14 12:00:00",
            symbol="600519.SH",
        )
        d = report.to_dict()
        assert d["report_date"] == "2026-06-14"
        assert d["symbol"] == "600519.SH"
        assert d["entries"] == []
        assert d["summary"] == {}

    def test_to_dict_with_entries(self):
        entry = SourceFreshnessEntry(data_type="fund_flow", status=SourceFreshnessStatus.HAS_DATA)
        report = SourceFreshnessReport(entries=[entry])
        d = report.to_dict()
        assert len(d["entries"]) == 1
        assert d["entries"][0]["data_type"] == "fund_flow"
