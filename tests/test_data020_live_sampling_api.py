# [DATA-020] live_sampling_health_ui tests
"""Tests for DATA-020: data source live sampling health daily report UI/API.

Tests cover:
  - JSON sidecar save/load (additive to DATA-019 Markdown)
  - find_latest_live_sampling_json (latest selection)
  - load_latest_live_sampling_report (graceful no_data, structured dict)
  - get_live_sampling_report service (no_data envelope, report envelope, runtime tier)
  - Pydantic schema validation (LiveSamplingResponse / Summary / Result)
  - Layered status contract: SKIPPED must never look healthy, FAILED != NORMAL_NO_DATA
  - Backward compatibility: Markdown still saved alongside JSON
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.live_source_sampling import (
    SampleStock,
    SampleResult,
    LiveSamplingReport,
    save_live_sampling_report,
    find_latest_live_sampling_report,
    find_latest_live_sampling_json,
    load_latest_live_sampling_report,
    _compute_summary,
)
from tradingagents.dataflows.source_freshness_report import SourceFreshnessStatus


# ── Helpers ────────────────────────────────────────────────────────────


def _make_result(
    data_type: str,
    label: str,
    symbol: str,
    status: str,
    *,
    is_fallback: bool = False,
    actual_vendor: str = "",
    primary_vendor: str = "",
    error: str = "",
    record_count: int = 0,
) -> SampleResult:
    return SampleResult(
        data_type=data_type,
        data_type_label=label,
        symbol=symbol,
        symbol_name=symbol,
        status=status,
        is_fallback=is_fallback,
        actual_vendor=actual_vendor,
        primary_vendor=primary_vendor,
        error=error,
        record_count=record_count,
    )


def _make_mixed_report(date_str: str = "2026-06-24") -> LiveSamplingReport:
    """A report that exercises all layered states:
    skipped, failed, fallback, normal_no_data, has_data.
    """
    report = LiveSamplingReport(
        report_date=date_str,
        generated_at=f"{date_str} 10:00:00",
        env_gated=False,
    )
    report.samples = [
        SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap", category_cn="大票"),
    ]
    report.results = [
        # healthy
        _make_result("quote", "实时行情", "600519.SH", SourceFreshnessStatus.HAS_DATA,
                     actual_vendor="tencent", primary_vendor="tencent", record_count=1),
        # fallback success
        _make_result("fund_flow", "个股资金流", "600519.SH", SourceFreshnessStatus.HAS_DATA,
                     is_fallback=True, actual_vendor="cn_astock", primary_vendor="cn_akshare",
                     record_count=20),
        # normal no data (grey, NOT a fault)
        _make_result("lhb", "龙虎榜", "600519.SH", SourceFreshnessStatus.NORMAL_NO_DATA,
                     primary_vendor="cn_astock"),
        # failed (red fault)
        _make_result("notice", "公告", "600519.SH", SourceFreshnessStatus.FAILED,
                     primary_vendor="cn_astock", error="ConnectionError"),
        # rate limited (red fault)
        _make_result("rating", "评级", "600519.SH", SourceFreshnessStatus.RATE_LIMITED,
                     primary_vendor="cn_astock", error="429 Too Many Requests"),
    ]
    report.summary = _compute_summary(report.results)
    return report


def _make_skipped_only_report(date_str: str = "2026-06-24") -> LiveSamplingReport:
    """A report where every check is SKIPPED (env not enabled)."""
    report = LiveSamplingReport(
        report_date=date_str,
        generated_at=f"{date_str} 10:00:00",
        env_gated=True,
    )
    report.samples = [
        SampleStock(symbol="600519.SH", name="贵州茅台", category="large_cap", category_cn="大票"),
    ]
    report.results = [
        _make_result("quote", "实时行情", "600519.SH", "SKIPPED"),
        _make_result("fund_flow", "个股资金流", "600519.SH", "SKIPPED"),
    ]
    report.summary = _compute_summary(report.results)
    return report


# ── JSON sidecar I/O ──────────────────────────────────────────────────


class TestJsonSidecar:
    def test_save_writes_markdown_and_json(self):
        report = _make_mixed_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = save_live_sampling_report(report, output_dir=tmpdir)
            assert os.path.exists(md_path)
            assert md_path.endswith("-live-smoke.md")
            # JSON sidecar must exist alongside
            json_path = md_path[:-3] + ".json"
            assert os.path.exists(json_path), "JSON sidecar should be written"

    def test_json_sidecar_is_structured_dict(self):
        report = _make_mixed_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_live_sampling_report(report, output_dir=tmpdir)
            json_path = find_latest_live_sampling_json(tmpdir)
            assert json_path is not None
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict)
            assert data["report_date"] == "2026-06-24"
            assert "results" in data
            assert "summary" in data
            assert len(data["results"]) == 5

    def test_custom_filename_still_writes_json(self):
        report = LiveSamplingReport(report_date="2026-06-24")
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = save_live_sampling_report(
                report, output_dir=tmpdir, filename="custom.md"
            )
            assert md_path.endswith("custom.md")
            json_path = os.path.join(tmpdir, "custom.json")
            assert os.path.exists(json_path)

    def test_markdown_content_unchanged(self):
        """DATA-020 JSON sidecar must not break DATA-019 Markdown consumers."""
        report = _make_mixed_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = save_live_sampling_report(report, output_dir=tmpdir)
            with open(md_path, encoding="utf-8") as f:
                content = f.read()
            assert "[DATA-019]" in content
            assert "Sampling Results" in content


# ── find_latest_live_sampling_json ────────────────────────────────────


class TestFindLatestJson:
    def test_finds_latest_by_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for date in ["2026-06-15", "2026-06-24", "2026-06-10"]:
                path = os.path.join(tmpdir, f"{date}-live-smoke.json")
                with open(path, "w") as f:
                    json.dump({"report_date": date}, f)
            latest = find_latest_live_sampling_json(tmpdir)
            assert latest is not None
            assert "2026-06-24-live-smoke.json" in latest

    def test_returns_none_when_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert find_latest_live_sampling_json(tmpdir) is None

    def test_ignores_markdown_and_other_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "2026-06-24-live-smoke.md"), "w") as f:
                f.write("# stub")
            with open(os.path.join(tmpdir, "freshness-2026-06-24.json"), "w") as f:
                json.dump({}, f)
            assert find_latest_live_sampling_json(tmpdir) is None

    def test_missing_dir_returns_none(self):
        assert find_latest_live_sampling_json("/nonexistent/path/xyz") is None


# ── load_latest_live_sampling_report ──────────────────────────────────


class TestLoadLatestReport:
    def test_load_returns_structured_dict(self):
        report = _make_mixed_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_live_sampling_report(report, output_dir=tmpdir)
            data = load_latest_live_sampling_report(tmpdir)
        assert data is not None
        assert data["report_date"] == "2026-06-24"
        assert data["env_gated"] is False
        assert len(data["results"]) == 5
        assert data["summary"]["total_checks"] == 5

    def test_load_returns_none_when_no_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert load_latest_live_sampling_report(tmpdir) is None

    def test_load_returns_none_on_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2026-06-24-live-smoke.json")
            with open(path, "w") as f:
                f.write("{not valid json")
            # Must NOT raise — graceful None
            assert load_latest_live_sampling_report(tmpdir) is None

    def test_load_never_raises(self):
        # Missing dir
        assert load_latest_live_sampling_report("/nonexistent/xyz") is None


# ── Service function ──────────────────────────────────────────────────


class TestGetLiveSamplingReportService:
    def test_no_data_envelope(self):
        from api.services.tradeflow_service import get_live_sampling_report

        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_live_sampling_report(reports_dir=tmpdir)
        assert result["status"] == "no_data"
        assert result["has_report"] is False
        assert result["results"] == []
        assert result["summary"]["overall_status"] == "no_data"
        assert result["summary"]["red_count"] == 0

    def test_no_data_runtime_tier_is_fast(self):
        from api.services.tradeflow_service import get_live_sampling_report

        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_live_sampling_report(reports_dir=tmpdir)
        meta = result["runtime_tier_meta"]
        assert meta["runtime_tier"] == "FAST_RADAR"

    def test_report_envelope_mixed_statuses(self):
        from api.services.tradeflow_service import get_live_sampling_report

        report = _make_mixed_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_live_sampling_report(report, output_dir=tmpdir)
            result = get_live_sampling_report(reports_dir=tmpdir)
        assert result["status"] == "ok"
        assert result["has_report"] is True
        assert result["report_date"] == "2026-06-24"
        assert len(result["results"]) == 5

        summary = result["summary"]
        # One failed + one rate_limited => red_count == 2
        assert summary["red_count"] == 2
        # One fallback triggered
        assert summary["fallback_triggered_count"] == 1
        assert summary["overall_status"] == "has_failures"

    def test_report_envelope_skipped_only(self):
        """skipped-only report must surface as 'skipped', NOT all_green."""
        from api.services.tradeflow_service import get_live_sampling_report

        report = _make_skipped_only_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_live_sampling_report(report, output_dir=tmpdir)
            result = get_live_sampling_report(reports_dir=tmpdir)
        assert result["status"] == "ok"
        assert result["has_report"] is True
        summary = result["summary"]
        assert summary["skipped_only"] is True
        assert summary["skipped_count"] == 2
        assert summary["all_green"] is False
        assert summary["overall_status"] == "skipped"

    def test_results_carry_status_label_cn(self):
        from api.services.tradeflow_service import get_live_sampling_report

        report = _make_mixed_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_live_sampling_report(report, output_dir=tmpdir)
            result = get_live_sampling_report(reports_dir=tmpdir)
        for r in result["results"]:
            assert "status_label_cn" in r
            assert "traffic_light" in r

    def test_each_status_layer_present_in_mixed_report(self):
        """The mixed report must contain skipped-free, but distinct failed /
        normal_no_data / has_data / fallback layers so the UI can render them
        separately."""
        from api.services.tradeflow_service import get_live_sampling_report

        report = _make_mixed_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_live_sampling_report(report, output_dir=tmpdir)
            result = get_live_sampling_report(reports_dir=tmpdir)
        statuses = {r["status"] for r in result["results"]}
        assert SourceFreshnessStatus.HAS_DATA in statuses
        assert SourceFreshnessStatus.NORMAL_NO_DATA in statuses
        assert SourceFreshnessStatus.FAILED in statuses
        assert SourceFreshnessStatus.RATE_LIMITED in statuses
        # At least one fallback
        assert any(r["is_fallback"] for r in result["results"])


# ── Schema validation ─────────────────────────────────────────────────


class TestSchemaValidation:
    def test_live_sampling_result_item_accepts_skipped(self):
        from api.tradeflow_schemas import LiveSamplingResultItem

        item = LiveSamplingResultItem(status="SKIPPED", traffic_light="red")
        assert item.status == "SKIPPED"

    def test_live_sampling_summary_skipped_only(self):
        from api.tradeflow_schemas import LiveSamplingSummary

        s = LiveSamplingSummary(skipped_only=True, skipped_count=4, all_green=False)
        assert s.skipped_only is True
        assert s.all_green is False

    def test_live_sampling_response_no_data(self):
        from api.tradeflow_schemas import LiveSamplingResponse

        resp = LiveSamplingResponse(status="no_data", has_report=False)
        assert resp.has_report is False
        assert resp.results == []
        assert resp.summary.overall_status == "no_data"

    def test_live_sampling_response_with_results(self):
        from api.tradeflow_schemas import (
            LiveSamplingResponse,
            LiveSamplingResultItem,
            LiveSamplingSummary,
        )

        resp = LiveSamplingResponse(
            status="ok",
            has_report=True,
            report_date="2026-06-24",
            results=[
                LiveSamplingResultItem(
                    data_type="fund_flow",
                    status="FAILED",
                    traffic_light="red",
                    is_fallback=False,
                ),
            ],
            summary=LiveSamplingSummary(
                total_checks=1, red_count=1, overall_status="has_failures"
            ),
        )
        assert resp.has_report is True
        assert resp.results[0].status == "FAILED"
        assert resp.summary.red_count == 1

    def test_runtime_tier_meta_defaults_fast(self):
        from api.tradeflow_schemas import LiveSamplingResponse

        resp = LiveSamplingResponse()
        assert resp.runtime_tier_meta.runtime_tier == "FAST_RADAR"


# ── Layered status contract ───────────────────────────────────────────


class TestLayeredStatusContract:
    """The core DATA-020 invariant: skipped, failed, normal_no_data and
    fallback must be distinguishable — skipped must never look healthy."""

    def test_skipped_is_not_green_in_summary(self):
        report = _make_skipped_only_report()
        assert report.summary["skipped_only"] is True
        assert report.summary["all_green"] is False
        assert report.summary["overall_status"] == "skipped"

    def test_failed_distinct_from_normal_no_data(self):
        report = _make_mixed_report()
        statuses = [r.status for r in report.results]
        assert SourceFreshnessStatus.FAILED in statuses
        assert SourceFreshnessStatus.NORMAL_NO_DATA in statuses
        # summary red_count counts failed+rate_limited only
        assert report.summary["red_count"] == 2

    def test_fallback_count_is_one(self):
        report = _make_mixed_report()
        assert report.summary["fallback_triggered_count"] == 1

    def test_traffic_lights_differ_across_layers(self):
        report = _make_mixed_report()
        lights = {r.status: r.traffic_light for r in report.results}
        assert lights[SourceFreshnessStatus.HAS_DATA] == "green"
        assert lights[SourceFreshnessStatus.FAILED] == "red"
        assert lights[SourceFreshnessStatus.RATE_LIMITED] == "red"
        assert lights[SourceFreshnessStatus.NORMAL_NO_DATA] == "red" or \
            lights[SourceFreshnessStatus.NORMAL_NO_DATA] == "green"
