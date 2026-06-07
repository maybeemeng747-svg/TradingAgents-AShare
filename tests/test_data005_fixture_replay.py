# [DATA-005] data_source_replay
"""
Tests for data source fixture replay and failure/rate-limit replay system.

Covers:
  - 7 fixture types: normal quote, stale daily, realtime success/failure,
    fund flow unit anomaly, LHB no trigger, announcement failure
  - Replay runner: single fixture, all fixtures, selective fixtures
  - Replay result validation: status matching, fallback detection, completeness
  - Rate limiting / timeout not treated as "no data"
  - Report rendering and file output
  - Acceptance criteria per TASKS.md
"""

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import patch

import pytest

from tradingagents.dataflows.fixture_replay import (
    ALL_FIXTURE_IDS,
    FIXTURE_ANNOUNCEMENT_FAILURE,
    FIXTURE_FUND_FLOW_UNIT_ANOMALY,
    FIXTURE_LHB_NO_TRIGGER,
    FIXTURE_NORMAL_QUOTE,
    FIXTURE_REALTIME_FAILURE,
    FIXTURE_REALTIME_SUCCESS,
    FIXTURE_STALE_DAILY,
    FixtureEntry,
    ReplayReport,
    ReplayResult,
    get_all_fixtures,
    get_fixture,
    render_replay_report,
    run_fixture_replay,
    run_replay_and_save,
    save_replay_report,
    _classify_failure_type,
    _replay_single_fixture,
)
from tradingagents.dataflows.evidence_contract import (
    EvidenceContract,
    compute_contract_completeness,
)


# ── Fixture Builders ──────────────────────────────────────────────────


class TestGetFixture:
    def test_get_normal_quote(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        assert f is not None
        assert f.fixture_id == FIXTURE_NORMAL_QUOTE
        assert f.expected_status == "HAS_DATA"
        assert "stock_data" in f.raw_evidence

    def test_get_stale_daily(self):
        f = get_fixture(FIXTURE_STALE_DAILY)
        assert f is not None
        assert f.fixture_id == FIXTURE_STALE_DAILY
        assert "stale" in f.tags

    def test_get_realtime_success(self):
        f = get_fixture(FIXTURE_REALTIME_SUCCESS)
        assert f is not None
        assert f.fixture_id == FIXTURE_REALTIME_SUCCESS
        assert "realtime_patch" in f.tags

    def test_get_realtime_failure(self):
        f = get_fixture(FIXTURE_REALTIME_FAILURE)
        assert f is not None
        assert f.fixture_id == FIXTURE_REALTIME_FAILURE
        assert "realtime_failure" in f.tags

    def test_get_fund_flow_unit_anomaly(self):
        f = get_fixture(FIXTURE_FUND_FLOW_UNIT_ANOMALY)
        assert f is not None
        assert f.fixture_id == FIXTURE_FUND_FLOW_UNIT_ANOMALY
        assert "unit_anomaly" in f.tags

    def test_get_lhb_no_trigger(self):
        f = get_fixture(FIXTURE_LHB_NO_TRIGGER)
        assert f is not None
        assert f.fixture_id == FIXTURE_LHB_NO_TRIGGER
        assert f.expected_status == "NOT_QUERIED"

    def test_get_announcement_failure(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAILURE)
        assert f is not None
        assert f.fixture_id == FIXTURE_ANNOUNCEMENT_FAILURE
        assert f.expected_status == "FAILED"

    def test_get_unknown_fixture(self):
        assert get_fixture("nonexistent") is None

    def test_all_fixture_ids_count(self):
        assert len(ALL_FIXTURE_IDS) >= 7

    def test_get_all_fixtures(self):
        fixtures = get_all_fixtures()
        assert len(fixtures) == len(ALL_FIXTURE_IDS)
        ids = {f.fixture_id for f in fixtures}
        assert ids == set(ALL_FIXTURE_IDS)


class TestFixtureEntryStructure:
    def test_fixture_has_required_fields(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        assert f.fixture_id
        assert f.description
        assert f.data_type
        assert f.vendor
        assert f.endpoint
        assert f.expected_status
        assert isinstance(f.raw_evidence, dict)
        assert isinstance(f.tags, list)

    def test_fixture_to_dict(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        d = f.to_dict()
        assert d["fixture_id"] == FIXTURE_NORMAL_QUOTE
        assert "raw_evidence" in d
        assert isinstance(d["tags"], list)

    def test_normal_quote_evidence_has_stock_data(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        sd = f.raw_evidence["stock_data"]
        assert sd["status"] == "HAS_DATA"
        assert sd["vendor"] == "cn_akshare"
        assert sd["unit"] == "元/股, 股"
        assert sd["unit_verified"] is True

    def test_normal_quote_evidence_has_fund_flow(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        ff = f.raw_evidence["fund_flow_individual"]
        assert ff["status"] == "HAS_DATA"
        assert ff["unit"] == "万元"
        assert ff["unit_verified"] is True

    def test_normal_quote_evidence_has_lhb(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        lhb = f.raw_evidence["lhb"]
        assert lhb["status"] == "NORMAL_NO_DATA"

    def test_stale_daily_no_today_data(self):
        f = get_fixture(FIXTURE_STALE_DAILY)
        sd = f.raw_evidence["stock_data"]
        assert sd["is_realtime_patched"] is False
        assert sd["record_count"] < 30

    def test_realtime_success_has_patch(self):
        f = get_fixture(FIXTURE_REALTIME_SUCCESS)
        sd = f.raw_evidence["stock_data"]
        assert sd["is_realtime_patched"] is True
        assert "current_price" in sd.get("patch_fields", [])

    def test_fund_flow_unit_anomaly_fallback(self):
        f = get_fixture(FIXTURE_FUND_FLOW_UNIT_ANOMALY)
        ff = f.raw_evidence["fund_flow_individual"]
        assert ff["unit_verified"] is False
        assert ff["fallback_from"] == "cn_akshare"
        assert ff["unit"] == "元"
        assert ff["vendor"] == "cn_astock"

    def test_announcement_failure_has_error(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAILURE)
        ann = f.raw_evidence["announcements"]
        assert ann["status"] == "FAILED"
        assert "ConnectionError" in ann["error"]

    def test_lhb_no_trigger_not_queried(self):
        f = get_fixture(FIXTURE_LHB_NO_TRIGGER)
        lhb = f.raw_evidence["lhb"]
        assert lhb["status"] == "NOT_QUERIED"
        assert lhb["force_reason"] is None


# ── Replay Result ─────────────────────────────────────────────────────


class TestReplayResult:
    def test_replay_result_defaults(self):
        r = ReplayResult(
            fixture_id="test",
            fixture_description="test desc",
            data_type="ohlcv",
            expected_status="HAS_DATA",
            actual_status="HAS_DATA",
            passed=True,
            vendor="cn_akshare",
            endpoint="stock_zh_a_hist",
            is_fallback=False,
            fallback_from=None,
            error=None,
            completeness_score=100,
            missing_details={},
        )
        assert r.passed is True
        assert r.tags == []

    def test_replay_result_to_dict(self):
        r = ReplayResult(
            fixture_id="test",
            fixture_description="desc",
            data_type="ohlcv",
            expected_status="HAS_DATA",
            actual_status="FAILED",
            passed=False,
            vendor="cn_akshare",
            endpoint="stock_zh_a_hist",
            is_fallback=True,
            fallback_from="cn_astock",
            error="ConnectionError",
            completeness_score=50,
            missing_details={"stock_data": ["status"]},
            tags=["test"],
        )
        d = r.to_dict()
        assert d["fixture_id"] == "test"
        assert d["passed"] is False
        assert d["is_fallback"] is True
        assert d["fallback_from"] == "cn_astock"
        assert d["completeness_score"] == 50


class TestReplayReport:
    def test_replay_report_defaults(self):
        report = ReplayReport(
            run_at="2026-06-02 12:00:00",
            date="2026-06-02",
        )
        assert report.all_passed is True
        assert report.total_fixtures == 0
        assert report.results == []

    def test_replay_report_to_dict(self):
        report = ReplayReport(
            run_at="2026-06-02 12:00:00",
            date="2026-06-02",
            total_fixtures=1,
            passed=1,
            failed=0,
        )
        d = report.to_dict()
        assert d["total_fixtures"] == 1
        assert d["all_passed"] is True
        assert "results" in d


# ── Replay Runner ─────────────────────────────────────────────────────


class TestReplaySingleFixture:
    def test_replay_normal_quote(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        result = _replay_single_fixture(f)
        assert result.passed is True
        assert result.fixture_id == FIXTURE_NORMAL_QUOTE
        assert result.actual_status == "HAS_DATA"

    def test_replay_stale_daily(self):
        f = get_fixture(FIXTURE_STALE_DAILY)
        result = _replay_single_fixture(f)
        assert result.fixture_id == FIXTURE_STALE_DAILY
        assert result.actual_status == "HAS_DATA"

    def test_replay_realtime_success(self):
        f = get_fixture(FIXTURE_REALTIME_SUCCESS)
        result = _replay_single_fixture(f)
        assert result.passed is True
        assert result.actual_status == "HAS_DATA"

    def test_replay_realtime_failure(self):
        f = get_fixture(FIXTURE_REALTIME_FAILURE)
        result = _replay_single_fixture(f)
        assert result.fixture_id == FIXTURE_REALTIME_FAILURE
        assert result.actual_status == "STALE"

    def test_replay_fund_flow_unit_anomaly(self):
        f = get_fixture(FIXTURE_FUND_FLOW_UNIT_ANOMALY)
        result = _replay_single_fixture(f)
        assert result.passed is True
        assert result.actual_status == "HAS_DATA"
        assert result.is_fallback is True

    def test_replay_lhb_no_trigger(self):
        f = get_fixture(FIXTURE_LHB_NO_TRIGGER)
        result = _replay_single_fixture(f)
        assert result.passed is True
        assert result.actual_status == "NOT_QUERIED"

    def test_replay_announcement_failure(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAILURE)
        result = _replay_single_fixture(f)
        assert result.passed is True
        assert result.actual_status == "FAILED"
        assert result.error is not None
        assert "ConnectionError" in result.error


class TestRunFixtureReplay:
    def test_run_all_fixtures(self):
        report = run_fixture_replay()
        assert report.total_fixtures == len(ALL_FIXTURE_IDS)
        assert report.passed + report.failed == report.total_fixtures

    def test_run_all_pass(self):
        report = run_fixture_replay()
        assert report.all_passed is True
        assert report.failed == 0

    def test_run_selective_fixtures(self):
        report = run_fixture_replay(
            fixture_ids=[FIXTURE_NORMAL_QUOTE, FIXTURE_LHB_NO_TRIGGER]
        )
        assert report.total_fixtures == 2

    def test_run_custom_fixtures(self):
        custom = FixtureEntry(
            fixture_id="custom_test",
            description="Custom test fixture",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test_endpoint",
            expected_status="HAS_DATA",
            raw_evidence={
                "stock_data": {
                    "raw": "data",
                    "status": "HAS_DATA",
                    "vendor": "cn_akshare",
                    "unit": "元/股",
                    "unit_verified": True,
                },
            },
        )
        report = run_fixture_replay(fixtures=[custom])
        assert report.total_fixtures == 1
        assert report.results[0].fixture_id == "custom_test"

    def test_report_has_date_and_time(self):
        report = run_fixture_replay()
        assert report.date
        assert report.run_at

    def test_report_by_data_type(self):
        report = run_fixture_replay()
        assert isinstance(report.by_data_type, dict)
        assert len(report.by_data_type) > 0

    def test_report_by_status(self):
        report = run_fixture_replay()
        assert isinstance(report.by_status, dict)

    def test_empty_fixture_ids(self):
        report = run_fixture_replay(fixture_ids=[])
        assert report.total_fixtures == 0
        assert report.all_passed is True


# ── Failure Classification ────────────────────────────────────────────


class TestClassifyFailureType:
    def test_connection_error(self):
        r = ReplayResult(
            fixture_id="test", fixture_description="", data_type="ohlcv",
            expected_status="HAS_DATA", actual_status="FAILED", passed=False,
            vendor="cn_akshare", endpoint="test", is_fallback=False,
            fallback_from=None, error="ConnectionError: timeout", completeness_score=0,
            missing_details={},
        )
        assert _classify_failure_type(r) == "connection_or_timeout"

    def test_timeout_error(self):
        r = ReplayResult(
            fixture_id="test", fixture_description="", data_type="ohlcv",
            expected_status="HAS_DATA", actual_status="FAILED", passed=False,
            vendor="cn_akshare", endpoint="test", is_fallback=False,
            fallback_from=None, error="TimeoutError: read timeout", completeness_score=0,
            missing_details={},
        )
        assert _classify_failure_type(r) == "connection_or_timeout"

    def test_rate_limit(self):
        r = ReplayResult(
            fixture_id="test", fixture_description="", data_type="ohlcv",
            expected_status="HAS_DATA", actual_status="FAILED", passed=False,
            vendor="cn_akshare", endpoint="test", is_fallback=False,
            fallback_from=None, error="RateLimitError: too many requests", completeness_score=0,
            missing_details={},
        )
        assert _classify_failure_type(r) == "rate_limited"

    def test_api_error(self):
        r = ReplayResult(
            fixture_id="test", fixture_description="", data_type="ohlcv",
            expected_status="HAS_DATA", actual_status="FAILED", passed=False,
            vendor="cn_akshare", endpoint="test", is_fallback=False,
            fallback_from=None, error="KeyError: 'data'", completeness_score=0,
            missing_details={},
        )
        assert _classify_failure_type(r) == "api_error"

    def test_stale_data(self):
        r = ReplayResult(
            fixture_id="test", fixture_description="", data_type="ohlcv",
            expected_status="HAS_DATA", actual_status="STALE", passed=False,
            vendor="cn_akshare", endpoint="test", is_fallback=False,
            fallback_from=None, error=None, completeness_score=50,
            missing_details={},
        )
        assert _classify_failure_type(r) == "stale_data"

    def test_unexpected_not_queried(self):
        r = ReplayResult(
            fixture_id="test", fixture_description="", data_type="lhb",
            expected_status="HAS_DATA", actual_status="NOT_QUERIED", passed=False,
            vendor="cn_akshare", endpoint="test", is_fallback=False,
            fallback_from=None, error=None, completeness_score=0,
            missing_details={},
        )
        assert _classify_failure_type(r) == "unexpected_not_queried"

    def test_status_mismatch(self):
        r = ReplayResult(
            fixture_id="test", fixture_description="", data_type="ohlcv",
            expected_status="HAS_DATA", actual_status="NORMAL_NO_DATA", passed=False,
            vendor="cn_akshare", endpoint="test", is_fallback=False,
            fallback_from=None, error=None, completeness_score=80,
            missing_details={},
        )
        assert _classify_failure_type(r) == "status_mismatch"


# ── Key Acceptance: Rate Limit / Timeout Not Treated as "No Data" ────


class TestRateLimitNotNoData:
    """限流/超时不应该被当作'无数据'来处理。"""

    def test_rate_limit_fixture_is_failed_not_no_data(self):
        rate_limit_fixture = FixtureEntry(
            fixture_id="rate_limit_test",
            description="Rate limited fixture",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="stock_individual_fund_flow",
            expected_status="FAILED",
            raw_evidence={
                "fund_flow_individual": {
                    "raw": None,
                    "status": "FAILED",
                    "vendor": "cn_akshare",
                    "endpoint": "stock_individual_fund_flow",
                    "error": "RateLimitError: API rate limit exceeded, retry after 60s",
                    "unit": "万元",
                    "unit_verified": True,
                },
            },
            tags=["rate_limited"],
        )
        result = _replay_single_fixture(rate_limit_fixture)
        assert result.actual_status == "FAILED"
        assert result.actual_status != "NORMAL_NO_DATA"
        assert "RateLimitError" in (result.error or "")

    def test_timeout_fixture_is_failed_not_no_data(self):
        timeout_fixture = FixtureEntry(
            fixture_id="timeout_test",
            description="Timeout fixture",
            data_type="notice",
            vendor="cn_astock",
            endpoint="cninfo.com.cn/hisAnnouncement",
            expected_status="FAILED",
            raw_evidence={
                "announcements": {
                    "raw": None,
                    "status": "FAILED",
                    "vendor": "cn_astock",
                    "endpoint": "cninfo.com.cn/hisAnnouncement",
                    "error": "TimeoutError: HTTPSConnectionPool read timeout=15s",
                    "unit": "",
                },
            },
            tags=["timeout"],
        )
        result = _replay_single_fixture(timeout_fixture)
        assert result.actual_status == "FAILED"
        assert result.actual_status != "NORMAL_NO_DATA"
        assert "TimeoutError" in (result.error or "")

    def test_connection_error_is_failed_not_no_data(self):
        conn_fixture = FixtureEntry(
            fixture_id="connection_error_test",
            description="Connection error fixture",
            data_type="notice",
            vendor="cn_astock",
            endpoint="cninfo.com.cn/hisAnnouncement",
            expected_status="FAILED",
            raw_evidence={
                "announcements": {
                    "raw": None,
                    "status": "FAILED",
                    "vendor": "cn_astock",
                    "endpoint": "cninfo.com.cn/hisAnnouncement",
                    "error": "ConnectionError: Max retries exceeded",
                    "unit": "",
                },
            },
            tags=["connection_error"],
        )
        result = _replay_single_fixture(conn_fixture)
        assert result.actual_status == "FAILED"
        assert result.actual_status != "NORMAL_NO_DATA"


class TestLHBNotQueriedVsFailed:
    """LHB NOT_QUERIED 不应该被混淆为 FAILED 或 NORMAL_NO_DATA。"""

    def test_not_queried_is_not_failed(self):
        f = get_fixture(FIXTURE_LHB_NO_TRIGGER)
        result = _replay_single_fixture(f)
        assert result.actual_status == "NOT_QUERIED"
        assert result.actual_status != "FAILED"
        assert result.passed is True

    def test_not_queried_vs_normal_no_data(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        lhb = f.raw_evidence["lhb"]
        assert lhb["status"] == "NORMAL_NO_DATA"
        g = get_fixture(FIXTURE_LHB_NO_TRIGGER)
        lhb2 = g.raw_evidence["lhb"]
        assert lhb2["status"] == "NOT_QUERIED"
        assert lhb["status"] != lhb2["status"]


class TestFallbackDetection:
    """Fallback 应该被正确检测并记录。"""

    def test_fund_flow_fallback_detected(self):
        f = get_fixture(FIXTURE_FUND_FLOW_UNIT_ANOMALY)
        result = _replay_single_fixture(f)
        assert result.is_fallback is True
        assert result.fallback_from == "cn_akshare"

    def test_normal_quote_no_fallback(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        result = _replay_single_fixture(f)
        assert result.is_fallback is False
        assert result.fallback_from is None


class TestCompleteness:
    """Completeness score should reflect evidence quality."""

    def test_normal_quote_high_completeness(self):
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        result = _replay_single_fixture(f)
        assert result.completeness_score >= 35

    def test_announcement_failure_low_completeness(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAILURE)
        result = _replay_single_fixture(f)
        assert result.completeness_score < 100

    def test_fund_flow_unit_anomaly_lower_completeness(self):
        f = get_fixture(FIXTURE_FUND_FLOW_UNIT_ANOMALY)
        result = _replay_single_fixture(f)
        assert result.completeness_score < 100


# ── Source Conflict ───────────────────────────────────────────────────


class TestSourceConflict:
    """来源冲突场景——同一字段多 vendor 不同状态。"""

    def test_conflicting_vendors_in_evidence(self):
        conflict_fixture = FixtureEntry(
            fixture_id="source_conflict",
            description="Two vendors disagree on fund flow status",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="stock_individual_fund_flow",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "raw": "data from akshare",
                    "status": "HAS_DATA",
                    "vendor": "cn_akshare",
                    "endpoint": "stock_individual_fund_flow",
                    "unit": "万元",
                    "unit_verified": True,
                },
            },
            tags=["source_conflict"],
        )
        result = _replay_single_fixture(conflict_fixture)
        assert result.passed is True

    def test_fallback_vendor_disagreement(self):
        f = get_fixture(FIXTURE_FUND_FLOW_UNIT_ANOMALY)
        ff = f.raw_evidence["fund_flow_individual"]
        assert ff["vendor"] == "cn_astock"
        assert ff["fallback_from"] == "cn_akshare"
        assert ff["vendor"] != ff["fallback_from"]


# ── Report Rendering ──────────────────────────────────────────────────


class TestRenderReplayReport:
    def test_render_all_passed(self):
        report = run_fixture_replay()
        md = render_replay_report(report)
        assert "# Data Source Fixture Replay Report" in md
        assert "ALL PASSED" in md
        assert f"| Total fixtures | {len(ALL_FIXTURE_IDS)} |" in md

    def test_render_has_fixture_table(self):
        report = run_fixture_replay()
        md = render_replay_report(report)
        assert "normal_quote" in md
        assert "stale_daily" in md
        assert "realtime_success" in md
        assert "realtime_failure" in md
        assert "fund_flow_unit_anomaly" in md
        assert "lhb_no_trigger" in md
        assert "announcement_failure" in md

    def test_render_with_failures(self):
        fail_fixture = FixtureEntry(
            fixture_id="fail_test",
            description="Should fail",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "stock_data": {
                    "raw": None,
                    "status": "FAILED",
                    "vendor": "cn_akshare",
                    "error": "ConnectionError: timeout",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        md = render_replay_report(report)
        assert "HAS FAILURES" in md
        assert "Failed Fixtures Detail" in md

    def test_render_no_failure_section_when_all_pass(self):
        report = run_fixture_replay()
        md = render_replay_report(report)
        assert "Failed Fixtures Detail" not in md

    def test_render_by_data_type(self):
        report = run_fixture_replay()
        md = render_replay_report(report)
        assert "By Data Type" in md


class TestSaveReplayReport:
    def test_save_creates_file(self):
        report = run_fixture_replay()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_replay_report(report, output_dir=tmpdir)
            assert os.path.exists(path)
            assert path.endswith(".md")
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "Data Source Fixture Replay Report" in content

    def test_save_filename_is_date(self):
        report = run_fixture_replay()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_replay_report(report, output_dir=tmpdir)
            basename = os.path.basename(path)
            assert basename.startswith(report.date)
            assert basename.endswith(".md")

    def test_run_replay_and_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = run_replay_and_save(output_dir=tmpdir)
            assert os.path.exists(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "Data Source Fixture Replay Report" in content


# ── Acceptance: Fixture Outputs Stable ────────────────────────────────


class TestAcceptanceData005:
    """DATA-005 验收标准。"""

    def test_all_fixture_outputs_stable(self):
        """所有 fixture 输出稳定。"""
        report = run_fixture_replay()
        assert report.all_passed is True
        assert report.total_fixtures == len(ALL_FIXTURE_IDS)

    def test_rate_limit_not_no_data(self):
        """限流/超时不会被当作'无数据'。"""
        report = run_fixture_replay(fixture_ids=[
            FIXTURE_ANNOUNCEMENT_FAILURE,
        ])
        assert report.total_fixtures == 1
        r = report.results[0]
        assert r.actual_status == "FAILED"
        assert r.actual_status != "NORMAL_NO_DATA"
        assert r.error is not None

    def test_lhb_not_queried_not_failed(self):
        """LHB NOT_QUERIED 不被当作 FAILED。"""
        report = run_fixture_replay(fixture_ids=[
            FIXTURE_LHB_NO_TRIGGER,
        ])
        assert report.results[0].actual_status == "NOT_QUERIED"
        assert report.results[0].passed is True

    def test_fixture_replay_callable_by_script(self):
        """run_fixture_replay() 可以被 scripts/auto_dev_loop.sh 调用。"""
        report = run_fixture_replay()
        assert isinstance(report, ReplayReport)
        assert report.total_fixtures > 0

    def test_fixture_replay_callable_with_save(self):
        """run_replay_and_save() 生成报告文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = run_replay_and_save(output_dir=tmpdir)
            assert os.path.exists(path)

    def test_fund_flow_unit_anomaly_detected(self):
        """资金流单位异常被正确检测。"""
        f = get_fixture(FIXTURE_FUND_FLOW_UNIT_ANOMALY)
        ff = f.raw_evidence["fund_flow_individual"]
        assert ff["unit_verified"] is False
        assert ff["unit"] != "万元"

    def test_realtime_failure_marked_stale(self):
        """实时行情失败标记为 stale 而非无数据。"""
        report = run_fixture_replay(fixture_ids=[
            FIXTURE_REALTIME_FAILURE,
        ])
        r = report.results[0]
        assert r.actual_status == "STALE"

    def test_normal_quote_completeness_high(self):
        """正常行情 fixture completeness 高。"""
        f = get_fixture(FIXTURE_NORMAL_QUOTE)
        result = _replay_single_fixture(f)
        assert result.completeness_score >= 35

    def test_no_cookie_or_key_in_fixtures(self):
        """Fixture 不包含 cookie/key。"""
        fixtures = get_all_fixtures()
        for f in fixtures:
            raw = str(f.raw_evidence)
            assert "cookie" not in raw.lower()
            assert "api_key" not in raw.lower()
            assert "apikey" not in raw.lower()
            assert "secret" not in raw.lower()
            assert "token" not in raw.lower() or "record_count" in raw.lower()

    def test_replay_report_serializable(self):
        """报告可序列化为 dict/JSON。"""
        report = run_fixture_replay()
        d = report.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert "fixture_id" in json_str

    def test_stale_daily_needs_realtime_patch(self):
        """日线 stale fixture 标记需要 realtime patch。"""
        f = get_fixture(FIXTURE_STALE_DAILY)
        assert "needs_realtime_patch" in f.tags or "stale" in f.tags

    def test_announcement_failure_error_preserved(self):
        """公告源失败时错误信息完整保留。"""
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAILURE)
        ann = f.raw_evidence["announcements"]
        assert ann["error"]
        assert "ConnectionError" in ann["error"]

    def test_run_fixture_replay_empty_returns_ok(self):
        """空 fixture 列表不报错。"""
        report = run_fixture_replay(fixture_ids=[])
        assert report.total_fixtures == 0
        assert report.all_passed is True


# ── Edge Cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_fixture_with_empty_raw_evidence(self):
        fixture = FixtureEntry(
            fixture_id="empty_ev",
            description="Empty evidence",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={},
        )
        result = _replay_single_fixture(fixture)
        assert result.fixture_id == "empty_ev"
        assert result.completeness_score == 0

    def test_fixture_with_non_dict_evidence(self):
        fixture = FixtureEntry(
            fixture_id="non_dict_ev",
            description="Non-dict evidence",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={"stock_data": "not a dict"},
        )
        result = _replay_single_fixture(fixture)
        assert result.fixture_id == "non_dict_ev"

    def test_fixture_with_none_values(self):
        fixture = FixtureEntry(
            fixture_id="none_vals",
            description="None values",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "stock_data": {
                    "raw": None,
                    "status": "NOT_QUERIED",
                    "vendor": "",
                },
            },
        )
        result = _replay_single_fixture(fixture)
        assert result.completeness_score < 100

    def test_multiple_failures_tracked(self):
        fail1 = FixtureEntry(
            fixture_id="fail1",
            description="Fail 1",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "stock_data": {
                    "status": "FAILED",
                    "error": "ConnectionError",
                    "vendor": "cn_akshare",
                },
            },
        )
        fail2 = FixtureEntry(
            fixture_id="fail2",
            description="Fail 2",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "status": "FAILED",
                    "error": "RateLimitError",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail1, fail2])
        assert report.failed == 2
        assert report.all_passed is False
        assert len(report.failure_types) >= 1

    def test_report_with_only_failures_renders(self):
        fail = FixtureEntry(
            fixture_id="only_fail",
            description="Only failure",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "stock_data": {
                    "status": "FAILED",
                    "error": "error",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail])
        md = render_replay_report(report)
        assert "HAS FAILURES" in md
        assert "Failed Fixtures Detail" in md
        assert "only_fail" in md

    def test_fixture_id_not_in_builders(self):
        """Unknown fixture_id returns None from get_fixture."""
        assert get_fixture("nonexistent_fixture_xyz") is None

    def test_all_fixtures_have_unique_ids(self):
        fixtures = get_all_fixtures()
        ids = [f.fixture_id for f in fixtures]
        assert len(ids) == len(set(ids))
