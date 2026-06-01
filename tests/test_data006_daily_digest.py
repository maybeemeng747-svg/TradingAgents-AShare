# [DATA-006] data_source_report_daily
"""
Tests for data source quality report daily digest integration.

Covers:
  - DailyDigest / DataTypeHealth data models
  - build_daily_digest: from ReplayReport, from file, NOT_RUN
  - Status classification: OK / PARTIAL / FAILED / NOT_RUN
  - Impact assessment: TradeFlow / TA readiness
  - Failure type aggregation and warnings
  - Report file parsing (read_replay_report_from_file)
  - find_latest_replay_report
  - Markdown rendering
  - Integration with summarize_auto_dev_runs.py
  - Acceptance criteria per TASKS.md
  - Edge cases
"""

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import patch

import pytest

from tradingagents.dataflows.data_source_daily_digest import (
    DailyDigest,
    DataTypeHealth,
    build_daily_digest,
    build_digest_section_for_nightly_report,
    find_latest_replay_report,
    read_replay_report_from_file,
    render_daily_digest,
    run_digest_and_append_to_report,
    _classify_status,
    _assess_overall_impact,
    _status_mark,
    _impact_mark,
)
from tradingagents.dataflows.fixture_replay import (
    ALL_FIXTURE_IDS,
    FixtureEntry,
    ReplayReport,
    ReplayResult,
    run_fixture_replay,
    render_replay_report,
    save_replay_report,
)


# ── DataTypeHealth ───────────────────────────────────────────────────


class TestDataTypeHealth:
    def test_defaults(self):
        h = DataTypeHealth(data_type="ohlcv", label="行情/OHLCV")
        assert h.status == "NOT_RUN"
        assert h.total_fixtures == 0
        assert h.passed == 0
        assert h.failed == 0
        assert h.failure_samples == []
        assert h.completeness_avg == 0.0
        assert h.has_fallback is False
        assert h.fallback_samples == []
        assert h.impact_tradeflow == "unknown"
        assert h.impact_ta_readiness == "unknown"

    def test_to_dict(self):
        h = DataTypeHealth(
            data_type="fund_flow",
            label="资金流",
            status="PARTIAL",
            total_fixtures=2,
            passed=1,
            failed=1,
            completeness_avg=75.3,
            impact_tradeflow="high",
            impact_ta_readiness="high",
        )
        d = h.to_dict()
        assert d["data_type"] == "fund_flow"
        assert d["status"] == "PARTIAL"
        assert d["completeness_avg"] == 75.3
        assert d["impact_tradeflow"] == "high"


# ── DailyDigest ──────────────────────────────────────────────────────


class TestDailyDigest:
    def test_defaults(self):
        d = DailyDigest(date="2026-06-02", generated_at="2026-06-02 12:00:00")
        assert d.replay_status == "NOT_RUN"
        assert d.replay_report_available is False
        assert d.all_passed is True
        assert d.data_type_healths == []
        assert d.failure_types == {}
        assert d.warnings == []

    def test_to_dict(self):
        d = DailyDigest(
            date="2026-06-02",
            generated_at="2026-06-02 12:00:00",
            replay_status="COMPLETED",
            total_fixtures=7,
            total_passed=7,
            total_failed=0,
            all_passed=True,
        )
        dd = d.to_dict()
        assert dd["date"] == "2026-06-02"
        assert dd["replay_status"] == "COMPLETED"
        assert dd["total_fixtures"] == 7
        assert dd["data_type_healths"] == []


# ── Status Classification ────────────────────────────────────────────


class TestClassifyStatus:
    def test_all_passed(self):
        assert _classify_status(5, 5) == "OK"

    def test_all_failed(self):
        assert _classify_status(0, 5) == "FAILED"

    def test_partial(self):
        assert _classify_status(3, 5) == "PARTIAL"

    def test_zero_total(self):
        assert _classify_status(0, 0) == "NOT_RUN"

    def test_single_passed(self):
        assert _classify_status(1, 1) == "OK"

    def test_single_failed(self):
        assert _classify_status(0, 1) == "FAILED"


# ── Impact Assessment ────────────────────────────────────────────────


class TestAssessOverallImpact:
    def test_no_healths(self):
        assert _assess_overall_impact([], "impact_tradeflow") == "none"

    def test_all_ok(self):
        healths = [
            DataTypeHealth(data_type="ohlcv", label="行情", status="OK"),
        ]
        assert _assess_overall_impact(healths, "impact_tradeflow") == "none"

    def test_high_impact_failed(self):
        healths = [
            DataTypeHealth(data_type="ohlcv", label="行情", status="FAILED", impact_tradeflow="high"),
        ]
        assert _assess_overall_impact(healths, "impact_tradeflow") == "high"

    def test_medium_partial(self):
        healths = [
            DataTypeHealth(data_type="lhb", label="龙虎榜", status="PARTIAL", impact_tradeflow="medium"),
        ]
        assert _assess_overall_impact(healths, "impact_tradeflow") == "medium"

    def test_low_only_not_counted(self):
        healths = [
            DataTypeHealth(data_type="news", label="新闻", status="FAILED", impact_tradeflow="low"),
        ]
        assert _assess_overall_impact(healths, "impact_tradeflow") == "low"

    def test_high_overrides_medium(self):
        healths = [
            DataTypeHealth(data_type="ohlcv", label="行情", status="FAILED", impact_tradeflow="high"),
            DataTypeHealth(data_type="news", label="新闻", status="PARTIAL", impact_tradeflow="low"),
        ]
        assert _assess_overall_impact(healths, "impact_tradeflow") == "high"


# ── Status / Impact Marks ────────────────────────────────────────────


class TestStatusMark:
    def test_ok(self):
        assert _status_mark("OK") == "OK"

    def test_partial(self):
        assert _status_mark("PARTIAL") == "PARTIAL"

    def test_failed(self):
        assert _status_mark("FAILED") == "FAILED"

    def test_not_run(self):
        assert _status_mark("NOT_RUN") == "NOT_RUN"


class TestImpactMark:
    def test_high(self):
        assert _impact_mark("high") == "HIGH"

    def test_medium(self):
        assert _impact_mark("medium") == "MEDIUM"

    def test_low(self):
        assert _impact_mark("low") == "LOW"

    def test_unknown(self):
        assert _impact_mark("unknown") == "-"


# ── build_daily_digest ───────────────────────────────────────────────


class TestBuildDailyDigest:
    def test_not_run_when_no_report(self):
        digest = build_daily_digest(report=None)
        assert digest.replay_status == "NOT_RUN"
        assert digest.replay_report_available is False
        assert digest.all_passed is False
        assert len(digest.warnings) > 0
        assert "NOT_RUN" in digest.warnings[0] or "replay" in digest.warnings[0].lower()

    def test_from_replay_report_all_pass(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        assert digest.replay_status == "COMPLETED"
        assert digest.replay_report_available is True
        assert digest.total_fixtures == 7
        assert digest.total_passed == 7
        assert digest.total_failed == 0
        assert digest.all_passed is True
        assert len(digest.data_type_healths) > 0

    def test_from_replay_report_with_failures(self):
        fail_fixture = FixtureEntry(
            fixture_id="fail_ohlcv",
            description="Should fail",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "stock_data": {
                    "status": "FAILED",
                    "error": "ConnectionError: timeout",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        assert digest.all_passed is False
        assert digest.total_failed == 1
        assert len(digest.data_type_healths) == 1
        assert digest.data_type_healths[0].status == "FAILED"

    def test_data_type_healths_aggregated(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        dtypes = {h.data_type for h in digest.data_type_healths}
        assert "ohlcv" in dtypes or "fund_flow" in dtypes

    def test_completeness_avg_calculated(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        for h in digest.data_type_healths:
            assert h.completeness_avg >= 0

    def test_impact_fields_set(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        for h in digest.data_type_healths:
            assert h.impact_tradeflow in ("high", "medium", "low", "unknown")
            assert h.impact_ta_readiness in ("high", "medium", "low", "unknown")

    def test_overall_impact_all_pass(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        assert digest.overall_tradeflow_impact == "none"
        assert digest.overall_ta_impact == "none"

    def test_overall_impact_with_failure(self):
        fail_fixture = FixtureEntry(
            fixture_id="fail_fund",
            description="Fund flow failure",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "status": "FAILED",
                    "error": "ConnectionError",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        assert digest.overall_tradeflow_impact == "high"
        assert digest.overall_ta_impact == "high"

    def test_warnings_on_high_impact_failure(self):
        fail_fixture = FixtureEntry(
            fixture_id="fail_fund",
            description="Fund flow failure",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "status": "FAILED",
                    "error": "ConnectionError",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        assert len(digest.warnings) > 0
        warning_text = " ".join(digest.warnings)
        assert "TradeFlow" in warning_text or "TA" in warning_text

    def test_failure_types_propagated(self):
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
                    "error": "ConnectionError: timeout",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail1])
        digest = build_daily_digest(report=report)
        assert isinstance(digest.failure_types, dict)


# ── File Reading ─────────────────────────────────────────────────────


class TestReadReplayReportFromFile:
    def test_nonexistent_file(self):
        result = read_replay_report_from_file("/nonexistent/path.md")
        assert result is None

    def test_valid_report_file(self):
        report = run_fixture_replay()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_replay_report(report, output_dir=tmpdir)
            loaded = read_replay_report_from_file(path)
            assert loaded is not None
            assert loaded.date == report.date
            assert loaded.total_fixtures == report.total_fixtures
            assert loaded.passed == report.passed
            assert loaded.failed == report.failed

    def test_invalid_content_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.md")
            with open(path, "w") as f:
                f.write("not a valid report\n")
            result = read_replay_report_from_file(path)
            assert result is not None
            assert result.total_fixtures == 0


class TestFindLatestReplayReport:
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert find_latest_replay_report(tmpdir) is None

    def test_finds_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for date in ["2026-06-01", "2026-06-02", "2026-06-03"]:
                path = os.path.join(tmpdir, f"{date}.md")
                with open(path, "w") as f:
                    f.write(f"# Report {date}\n")
            result = find_latest_replay_report(tmpdir)
            assert result is not None
            assert "2026-06-03" in result

    def test_target_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2026-06-02.md")
            with open(path, "w") as f:
                f.write("# Report\n")
            result = find_latest_replay_report(tmpdir, target_date="2026-06-02")
            assert result is not None
            assert "2026-06-02" in result

    def test_target_date_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2026-06-01.md")
            with open(path, "w") as f:
                f.write("# Report\n")
            result = find_latest_replay_report(tmpdir, target_date="2026-06-02")
            assert result is None

    def test_nonexistent_dir(self):
        assert find_latest_replay_report("/nonexistent/dir") is None

    def test_ignores_non_date_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "readme.md")
            with open(path, "w") as f:
                f.write("# Not a report\n")
            assert find_latest_replay_report(tmpdir) is None


# ── Markdown Rendering ───────────────────────────────────────────────


class TestRenderDailyDigest:
    def test_render_not_run(self):
        digest = build_daily_digest(report=None)
        md = render_daily_digest(digest)
        assert "NOT_RUN" in md
        assert "DATA-006" in md
        assert "replay" in md.lower()

    def test_render_all_pass(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        md = render_daily_digest(digest)
        assert "OK" in md
        assert "按数据类型" in md
        assert "7 passed" in md or f"{report.passed} passed" in md

    def test_render_with_failures(self):
        fail_fixture = FixtureEntry(
            fixture_id="fail_test",
            description="Fail",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "status": "FAILED",
                    "error": "ConnectionError",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        md = render_daily_digest(digest)
        assert "HAS ISSUES" in md
        assert "失败" in md or "FAILED" in md

    def test_render_includes_data_type_table(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        md = render_daily_digest(digest)
        assert "影响TradeFlow" in md
        assert "影响TA" in md

    def test_render_failure_types_section(self):
        fail_fixture = FixtureEntry(
            fixture_id="fail_ft",
            description="Fail for type",
            data_type="ohlcv",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "stock_data": {
                    "status": "FAILED",
                    "error": "ConnectionError: timeout",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        md = render_daily_digest(digest)
        assert "失败类型" in md

    def test_render_warnings_section(self):
        fail_fixture = FixtureEntry(
            fixture_id="fail_warn",
            description="Fail with warning",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "status": "FAILED",
                    "error": "ConnectionError",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        md = render_daily_digest(digest)
        assert "警告" in md or "TradeFlow" in md

    def test_render_report_path(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report, report_path="docs/data_source_reports/2026-06-02.md")
        md = render_daily_digest(digest)
        assert "docs/data_source_reports/2026-06-02.md" in md


# ── build_digest_section_for_nightly_report ──────────────────────────


class TestBuildDigestSectionForNightlyReport:
    def test_with_no_reports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            section = build_digest_section_for_nightly_report(
                reports_dir=tmpdir, target_date="2026-06-02",
            )
            assert "NOT_RUN" in section

    def test_with_existing_report(self):
        report = run_fixture_replay()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_replay_report(report, output_dir=tmpdir)
            section = build_digest_section_for_nightly_report(
                reports_dir=tmpdir, target_date=report.date,
            )
            assert "数据源健康日报" in section
            assert "COMPLETED" in section or "OK" in section


# ── run_digest_and_append_to_report ──────────────────────────────────


class TestRunDigestAndAppendToReport:
    def test_appends_to_existing_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = os.path.join(tmpdir, "nightly.md")
            with open(report_path, "w") as f:
                f.write("# Nightly Report\n\nSome content\n")
            result = run_digest_and_append_to_report(
                nightly_report_path=report_path,
                reports_dir=os.path.join(tmpdir, "nonexistent"),
            )
            assert result is True
            with open(report_path) as f:
                content = f.read()
            assert "数据源健康日报" in content

    def test_does_not_double_append(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = os.path.join(tmpdir, "nightly.md")
            with open(report_path, "w") as f:
                f.write("# Nightly Report\n\nSome content\n")
            run_digest_and_append_to_report(
                nightly_report_path=report_path,
                reports_dir=os.path.join(tmpdir, "nonexistent"),
            )
            result = run_digest_and_append_to_report(
                nightly_report_path=report_path,
                reports_dir=os.path.join(tmpdir, "nonexistent"),
            )
            assert result is False

    def test_nonexistent_report_path(self):
        result = run_digest_and_append_to_report(
            nightly_report_path="/nonexistent/path.md",
        )
        assert result is False


# ── Acceptance: DATA-006 ─────────────────────────────────────────────


class TestAcceptanceData006:
    """DATA-006 验收标准。"""

    def test_fixture_replay_report_in_nightly_digest(self):
        """构造一个 replay 报告 fixture，夜间日报能展示数据源健康摘要。"""
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        md = render_daily_digest(digest)
        assert "数据源健康日报" in md
        assert "按数据类型" in md
        assert len(digest.data_type_healths) > 0

    def test_replay_missing_shows_not_run(self):
        """replay 缺失时显示 NOT_RUN。"""
        digest = build_daily_digest(report=None)
        assert digest.replay_status == "NOT_RUN"
        md = render_daily_digest(digest)
        assert "NOT_RUN" in md

    def test_no_sensitive_data_leaked(self):
        """不泄露敏感环境变量。"""
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        md = render_daily_digest(digest)
        assert "api_key" not in md.lower()
        assert "apikey" not in md.lower()
        assert "secret" not in md.lower()
        assert "cookie" not in md.lower()
        assert "token" not in md.lower() or "record_count" in md.lower()

    def test_data_type_aggregation_complete(self):
        """按数据类型聚合完整：每类有 OK/PARTIAL/FAILED。"""
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        for h in digest.data_type_healths:
            assert h.status in ("OK", "PARTIAL", "FAILED", "NOT_RUN")
            assert h.total_fixtures > 0
            assert h.passed + h.failed == h.total_fixtures

    def test_failure_samples_captured(self):
        """失败样本被捕获。"""
        fail_fixture = FixtureEntry(
            fixture_id="fail_sample",
            description="Fail with details",
            data_type="notice",
            vendor="cn_astock",
            endpoint="cninfo",
            expected_status="HAS_DATA",
            raw_evidence={
                "announcements": {
                    "status": "FAILED",
                    "error": "ConnectionError: cninfo timeout",
                    "vendor": "cn_astock",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        assert len(digest.data_type_healths) == 1
        h = digest.data_type_healths[0]
        assert h.status == "FAILED"
        assert len(h.failure_samples) > 0
        assert "fail_sample" in h.failure_samples[0]

    def test_tradeflow_impact_assessed(self):
        """TradeFlow 影响被评估。"""
        fail_fixture = FixtureEntry(
            fixture_id="fail_ohlcv",
            description="OHLCV failure",
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
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        assert digest.overall_tradeflow_impact == "high"

    def test_ta_readiness_impact_assessed(self):
        """TA readiness 影响被评估。"""
        fail_fixture = FixtureEntry(
            fixture_id="fail_financials",
            description="Financials failure",
            data_type="financials",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "fundamentals": {
                    "status": "FAILED",
                    "error": "KeyError",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[fail_fixture])
        digest = build_daily_digest(report=report)
        assert digest.overall_ta_impact == "high"

    def test_digest_serializable(self):
        """日报可序列化。"""
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        d = digest.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert "data_type_healths" in json_str
        assert "replay_status" in json_str

    def test_nightly_report_integration(self):
        """夜间日报可集成数据源健康摘要。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            report = run_fixture_replay()
            save_replay_report(report, output_dir=tmpdir)
            section = build_digest_section_for_nightly_report(
                reports_dir=tmpdir, target_date=report.date,
            )
            assert "数据源健康日报" in section
            assert "COMPLETED" in section or "OK" in section

    def test_partial_status_shown(self):
        """PARTIAL 状态正确展示。"""
        ok = FixtureEntry(
            fixture_id="ok_fund",
            description="OK fund flow",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "status": "HAS_DATA",
                    "vendor": "cn_akshare",
                    "unit": "万元",
                    "unit_verified": True,
                },
            },
        )
        fail = FixtureEntry(
            fixture_id="fail_fund",
            description="Fail fund flow",
            data_type="fund_flow",
            vendor="cn_akshare",
            endpoint="test",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "status": "FAILED",
                    "error": "ConnectionError",
                    "vendor": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[ok, fail])
        digest = build_daily_digest(report=report)
        fund_health = [h for h in digest.data_type_healths if h.data_type == "fund_flow"]
        assert len(fund_health) == 1
        assert fund_health[0].status == "PARTIAL"
        assert fund_health[0].passed == 1
        assert fund_health[0].failed == 1


# ── Edge Cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_replay_report(self):
        report = run_fixture_replay(fixture_ids=[])
        digest = build_daily_digest(report=report)
        assert digest.replay_status == "COMPLETED"
        assert digest.total_fixtures == 0
        assert digest.data_type_healths == []
        assert digest.all_passed is True

    def test_all_failures_same_type(self):
        fixtures = []
        for i in range(3):
            fixtures.append(FixtureEntry(
                fixture_id=f"fail_{i}",
                description=f"Fail {i}",
                data_type="ohlcv",
                vendor="cn_akshare",
                endpoint="test",
                expected_status="HAS_DATA",
                raw_evidence={
                    "stock_data": {
                        "status": "FAILED",
                        "error": f"Error {i}",
                        "vendor": "cn_akshare",
                    },
                },
            ))
        report = run_fixture_replay(fixtures=fixtures)
        digest = build_daily_digest(report=report)
        assert len(digest.data_type_healths) == 1
        assert digest.data_type_healths[0].total_fixtures == 3
        assert digest.data_type_healths[0].failed == 3

    def test_fallback_detected_in_digest(self):
        f = FixtureEntry(
            fixture_id="fallback_test",
            description="Fallback fixture",
            data_type="fund_flow",
            vendor="cn_astock",
            endpoint="push2his",
            expected_status="HAS_DATA",
            raw_evidence={
                "fund_flow_individual": {
                    "status": "HAS_DATA",
                    "vendor": "cn_astock",
                    "unit": "元",
                    "unit_verified": False,
                    "fallback_from": "cn_akshare",
                },
            },
        )
        report = run_fixture_replay(fixtures=[f])
        digest = build_daily_digest(report=report)
        assert len(digest.data_type_healths) == 1
        h = digest.data_type_healths[0]
        assert h.has_fallback is True

    def test_digest_with_none_report_path(self):
        digest = build_daily_digest(report=None, report_path=None)
        assert digest.replay_report_path is None

    def test_render_empty_healths(self):
        report = run_fixture_replay(fixture_ids=[])
        digest = build_daily_digest(report=report)
        md = render_daily_digest(digest)
        assert "数据源健康日报" in md

    def test_many_failure_samples_truncated(self):
        fixtures = []
        for i in range(10):
            fixtures.append(FixtureEntry(
                fixture_id=f"fail_{i}",
                description=f"Fail {i}",
                data_type="ohlcv",
                vendor="cn_akshare",
                endpoint="test",
                expected_status="HAS_DATA",
                raw_evidence={
                    "stock_data": {
                        "status": "FAILED",
                        "error": f"ConnectionError {i}",
                        "vendor": "cn_akshare",
                    },
                },
            ))
        report = run_fixture_replay(fixtures=fixtures)
        digest = build_daily_digest(report=report)
        h = digest.data_type_healths[0]
        assert len(h.failure_samples) <= 5

    def test_digest_date_matches(self):
        digest = build_daily_digest(report=None)
        today = datetime.now().strftime("%Y-%m-%d")
        assert digest.date == today

    def test_to_dict_roundtrip(self):
        report = run_fixture_replay()
        digest = build_daily_digest(report=report)
        d = digest.to_dict()
        assert isinstance(d, dict)
        assert isinstance(d["data_type_healths"], list)
        for h in d["data_type_healths"]:
            assert "status" in h
            assert "data_type" in h

    def test_report_path_stored_in_digest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = run_fixture_replay()
            path = save_replay_report(report, output_dir=tmpdir)
            digest = build_daily_digest(report_path=path)
            assert digest.replay_report_path == path
