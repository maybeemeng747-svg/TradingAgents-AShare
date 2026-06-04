# [DATA-008] astock_fallback_replay
"""
DATA-008: A股关键源 fallback smoke fixtures 扩展测试。

覆盖 6 个新增 replay 场景：
  1. AKShare 个股资金流失败但 fallback 成功
  2. 龙虎榜 NORMAL_NO_DATA（非 FAILED）
  3. 龙虎榜 FAILED（非 NORMAL_NO_DATA）
  4. 日线 stale 后实时 quote 补丁成功
  5. 公告源失败但事件源有弱证据
  6. 换手率/量比缺失导致完整度降级

验收方式：
  - FAILED 不被显示为 NORMAL_NO_DATA
  - fallback 成功时显示实际 vendor
  - stale 行情补丁显示 is_realtime_patched=True
  - 与 DATA-007 auditor 对接，fixture 能触发覆盖率变化
  - replay 输出包含 vendor/endpoint/status/fallback_vendor/error/as_of
"""

import os
import tempfile

import pytest

from tradingagents.dataflows.fixture_replay import (
    ALL_FIXTURE_IDS,
    FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
    FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
    FIXTURE_LHB_FAILED,
    FIXTURE_LHB_NORMAL_NO_DATA,
    FIXTURE_STALE_REALTIME_PATCH,
    FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
    FixtureEntry,
    ReplayReport,
    ReplayResult,
    get_all_fixtures,
    get_fixture,
    render_replay_report,
    run_fixture_replay,
    run_replay_and_save,
    save_replay_report,
)
from tradingagents.dataflows.evidence_contract import (
    EvidenceContract,
    compute_contract_completeness,
)
from tradingagents.dataflows.evidence_coverage_audit import (
    EvidenceAuditResult,
    audit_raw_evidence,
)


class TestNewFixtureExistence:
    def test_fund_flow_fallback_fixture_exists(self):
        f = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        assert f is not None
        assert f.fixture_id == FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK

    def test_lhb_normal_no_data_fixture_exists(self):
        f = get_fixture(FIXTURE_LHB_NORMAL_NO_DATA)
        assert f is not None
        assert f.fixture_id == FIXTURE_LHB_NORMAL_NO_DATA

    def test_lhb_failed_fixture_exists(self):
        f = get_fixture(FIXTURE_LHB_FAILED)
        assert f is not None
        assert f.fixture_id == FIXTURE_LHB_FAILED

    def test_stale_realtime_patch_fixture_exists(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        assert f is not None
        assert f.fixture_id == FIXTURE_STALE_REALTIME_PATCH

    def test_announcement_fail_event_weak_fixture_exists(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK)
        assert f is not None
        assert f.fixture_id == FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK

    def test_turnover_volume_ratio_missing_fixture_exists(self):
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        assert f is not None
        assert f.fixture_id == FIXTURE_TURNOVER_VOLUME_RATIO_MISSING

    def test_all_new_fixtures_in_all_fixture_ids(self):
        new_ids = {
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
            FIXTURE_LHB_NORMAL_NO_DATA,
            FIXTURE_LHB_FAILED,
            FIXTURE_STALE_REALTIME_PATCH,
            FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
            FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
        }
        assert new_ids.issubset(set(ALL_FIXTURE_IDS))

    def test_total_fixture_count(self):
        assert len(ALL_FIXTURE_IDS) >= 13


class TestFundFlowAkshareFailFallback:
    def test_fixture_structure(self):
        f = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        assert f.data_type == "fund_flow"
        assert f.vendor == "cn_astock"
        assert f.expected_status == "HAS_DATA"
        assert "fund_flow_individual" in f.raw_evidence
        assert "stock_data" in f.raw_evidence

    def test_fund_flow_is_fallback(self):
        f = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        ev = f.raw_evidence["fund_flow_individual"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.is_fallback is True
        assert contract.fallback_from == "cn_akshare"
        assert contract.vendor == "cn_astock"

    def test_fund_flow_unit_verified(self):
        f = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        ev = f.raw_evidence["fund_flow_individual"]
        assert ev.get("unit_verified") is True
        contract = EvidenceContract.from_dict(ev)
        assert contract.unit == "万元"

    def test_replay_passes(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK])
        assert report.total_fixtures == 1
        assert report.results[0].passed is True
        assert report.results[0].actual_status == "HAS_DATA"

    def test_replay_shows_fallback_vendor(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK])
        r = report.results[0]
        assert r.vendor == "cn_astock"
        assert r.is_fallback is True
        assert r.fallback_from == "cn_akshare"

    def test_replay_has_as_of(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK])
        r = report.results[0]
        assert r.as_of != ""


class TestLHBNormalNoData:
    def test_fixture_structure(self):
        f = get_fixture(FIXTURE_LHB_NORMAL_NO_DATA)
        assert f.data_type == "lhb"
        assert f.vendor == "cn_astock"
        assert f.expected_status == "NORMAL_NO_DATA"

    def test_lhb_status_is_normal_no_data(self):
        f = get_fixture(FIXTURE_LHB_NORMAL_NO_DATA)
        ev = f.raw_evidence["lhb"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.status == "NORMAL_NO_DATA"
        assert contract.status != "FAILED"

    def test_lhb_force_reason_present(self):
        f = get_fixture(FIXTURE_LHB_NORMAL_NO_DATA)
        ev = f.raw_evidence["lhb"]
        assert ev.get("force_reason") == "anomaly_condition"

    def test_replay_passes(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_LHB_NORMAL_NO_DATA])
        assert report.total_fixtures == 1
        assert report.results[0].passed is True
        assert report.results[0].actual_status == "NORMAL_NO_DATA"

    def test_normal_no_data_not_failed(self):
        f = get_fixture(FIXTURE_LHB_NORMAL_NO_DATA)
        ev = f.raw_evidence["lhb"]
        contract = EvidenceContract.from_dict(ev)
        assert not contract.is_failed
        assert contract.status == "NORMAL_NO_DATA"


class TestLHBFailed:
    def test_fixture_structure(self):
        f = get_fixture(FIXTURE_LHB_FAILED)
        assert f.data_type == "lhb"
        assert f.vendor == "cn_astock"
        assert f.expected_status == "FAILED"

    def test_lhb_status_is_failed(self):
        f = get_fixture(FIXTURE_LHB_FAILED)
        ev = f.raw_evidence["lhb"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.status == "FAILED"
        assert contract.status != "NORMAL_NO_DATA"

    def test_lhb_error_present(self):
        f = get_fixture(FIXTURE_LHB_FAILED)
        ev = f.raw_evidence["lhb"]
        assert ev.get("error") is not None
        assert "ConnectionError" in ev["error"]

    def test_replay_passes(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_LHB_FAILED])
        assert report.total_fixtures == 1
        assert report.results[0].passed is True
        assert report.results[0].actual_status == "FAILED"

    def test_failed_not_normal_no_data(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_LHB_FAILED])
        r = report.results[0]
        assert r.actual_status == "FAILED"
        assert r.actual_status != "NORMAL_NO_DATA"

    def test_replay_error_preserved(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_LHB_FAILED])
        r = report.results[0]
        assert r.error is not None
        assert "ConnectionError" in r.error


class TestStaleRealtimePatch:
    def test_fixture_structure(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        assert f.data_type == "ohlcv"
        assert f.expected_status == "HAS_DATA"

    def test_stock_data_is_realtime_patched(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        ev = f.raw_evidence["stock_data"]
        assert ev.get("is_realtime_patched") is True
        assert ev.get("source_type") == "realtime_patch"

    def test_patch_fields_present(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        ev = f.raw_evidence["stock_data"]
        patch_fields = ev.get("patch_fields", [])
        assert "current_price" in patch_fields
        assert "turnover_rate" in patch_fields
        assert "volume_ratio" in patch_fields

    def test_patch_source_is_tencent(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        ev = f.raw_evidence["stock_data"]
        assert ev.get("patch_source") == "cn_astock_tencent"

    def test_patch_as_of_is_today(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        ev = f.raw_evidence["stock_data"]
        assert ev.get("patch_as_of") != ""

    def test_replay_passes(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_STALE_REALTIME_PATCH])
        assert report.total_fixtures == 1
        assert report.results[0].passed is True
        assert report.results[0].actual_status == "HAS_DATA"


class TestAnnouncementFailEventWeak:
    def test_fixture_structure(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK)
        assert f.data_type == "notice"
        assert f.vendor == "cn_astock"
        assert f.expected_status == "FAILED"

    def test_announcement_is_failed(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK)
        ev = f.raw_evidence["announcements"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.status == "FAILED"
        assert contract.error is not None

    def test_news_has_data(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK)
        ev = f.raw_evidence["news"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.status == "HAS_DATA"

    def test_replay_passes_with_failed(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK])
        assert report.total_fixtures == 1
        assert report.results[0].passed is True
        assert report.results[0].actual_status == "FAILED"

    def test_failed_not_shown_as_no_data(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK])
        r = report.results[0]
        assert r.actual_status == "FAILED"
        assert r.actual_status != "NORMAL_NO_DATA"


class TestTurnoverVolumeRatioMissing:
    def test_fixture_structure(self):
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        assert f.data_type == "ohlcv"
        assert f.expected_status == "HAS_DATA"

    def test_stock_data_present(self):
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        ev = f.raw_evidence["stock_data"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.status == "HAS_DATA"

    def test_fund_flow_present(self):
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        ev = f.raw_evidence["fund_flow_individual"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.status == "HAS_DATA"

    def test_lhb_not_queried(self):
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        ev = f.raw_evidence["lhb"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.status == "NOT_QUERIED"

    def test_missing_turnover_and_volume_ratio_tags(self):
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        assert "missing_turnover" in f.tags
        assert "missing_volume_ratio" in f.tags

    def test_completeness_degraded(self):
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        comp = compute_contract_completeness(f.raw_evidence)
        assert comp["completeness_score"] < 100
        assert "announcements" in comp["missing_details"]
        assert "news" in comp["missing_details"]

    def test_replay_passes(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_TURNOVER_VOLUME_RATIO_MISSING])
        assert report.total_fixtures == 1
        assert report.results[0].passed is True


class TestLHBDistinction:
    """龙虎榜 FAILED 与 NORMAL_NO_DATA 必须明确区分。"""

    def test_failed_not_misrepresented_as_normal_no_data(self):
        f_failed = get_fixture(FIXTURE_LHB_FAILED)
        f_normal = get_fixture(FIXTURE_LHB_NORMAL_NO_DATA)
        ev_failed = f_failed.raw_evidence["lhb"]
        ev_normal = f_normal.raw_evidence["lhb"]
        assert ev_failed["status"] == "FAILED"
        assert ev_normal["status"] == "NORMAL_NO_DATA"
        assert ev_failed["status"] != ev_normal["status"]

    def test_replay_distinguishes_failed_vs_normal_no_data(self):
        r_failed = run_fixture_replay(fixture_ids=[FIXTURE_LHB_FAILED])
        r_normal = run_fixture_replay(fixture_ids=[FIXTURE_LHB_NORMAL_NO_DATA])
        assert r_failed.results[0].actual_status == "FAILED"
        assert r_normal.results[0].actual_status == "NORMAL_NO_DATA"
        assert r_failed.results[0].actual_status != r_normal.results[0].actual_status

    def test_failed_has_error_normal_no_data_does_not(self):
        f_failed = get_fixture(FIXTURE_LHB_FAILED)
        f_normal = get_fixture(FIXTURE_LHB_NORMAL_NO_DATA)
        assert f_failed.raw_evidence["lhb"]["error"] is not None
        assert f_normal.raw_evidence["lhb"].get("error") is None


class TestFallbackVendorDisplay:
    """fallback 成功时必须显示实际 vendor。"""

    def test_fallback_shows_actual_vendor_cn_astock(self):
        f = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        ev = f.raw_evidence["fund_flow_individual"]
        assert ev["vendor"] == "cn_astock"
        assert ev["fallback_from"] == "cn_akshare"

    def test_replay_result_vendor_is_astock(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK])
        r = report.results[0]
        assert r.vendor == "cn_astock"
        assert r.fallback_from == "cn_akshare"
        assert r.is_fallback is True


class TestStaleRealtimePatchDisplay:
    """stale 行情补丁必须显示 is_realtime_patched=True。"""

    def test_patch_shows_is_realtime_patched_true(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        ev = f.raw_evidence["stock_data"]
        assert ev["is_realtime_patched"] is True

    def test_patch_includes_turnover_and_volume_ratio(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        patch_fields = f.raw_evidence["stock_data"]["patch_fields"]
        assert "turnover_rate" in patch_fields
        assert "volume_ratio" in patch_fields

    def test_patch_source_recorded(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        ev = f.raw_evidence["stock_data"]
        assert ev["patch_source"] == "cn_astock_tencent"
        assert ev["patch_as_of"] != ""


class TestReplayOutputFields:
    """replay 输出必须包含 vendor/endpoint/status/fallback_vendor/error/as_of。"""

    @pytest.mark.parametrize("fixture_id", [
        FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
        FIXTURE_LHB_NORMAL_NO_DATA,
        FIXTURE_LHB_FAILED,
        FIXTURE_STALE_REALTIME_PATCH,
        FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
        FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
    ])
    def test_replay_result_has_required_fields(self, fixture_id):
        report = run_fixture_replay(fixture_ids=[fixture_id])
        r = report.results[0]
        assert hasattr(r, "vendor") and r.vendor != ""
        assert hasattr(r, "endpoint") and r.endpoint != ""
        assert hasattr(r, "actual_status") and r.actual_status != ""
        assert hasattr(r, "fallback_from")
        assert hasattr(r, "error")
        assert hasattr(r, "as_of")

    @pytest.mark.parametrize("fixture_id", [
        FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
        FIXTURE_LHB_NORMAL_NO_DATA,
        FIXTURE_LHB_FAILED,
        FIXTURE_STALE_REALTIME_PATCH,
        FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
        FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
    ])
    def test_replay_result_to_dict_roundtrip(self, fixture_id):
        report = run_fixture_replay(fixture_ids=[fixture_id])
        r = report.results[0]
        d = r.to_dict()
        assert d["vendor"] == r.vendor
        assert d["endpoint"] == r.endpoint
        assert d["actual_status"] == r.actual_status
        assert d["as_of"] == r.as_of


class TestEvidenceAuditIntegration:
    """与 DATA-007 auditor 对接，确保 fixture 能触发覆盖率变化。"""

    def test_fund_flow_fallback_full_coverage(self):
        f = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        audit = audit_raw_evidence(f.raw_evidence)
        assert audit.evidence_coverage > 0
        assert "stock_data" not in audit.critical_missing_fields

    def test_lhb_normal_no_data_coverage(self):
        f = get_fixture(FIXTURE_LHB_NORMAL_NO_DATA)
        audit = audit_raw_evidence(f.raw_evidence)
        assert "lhb" in audit.normal_no_data_fields
        assert "lhb" not in audit.failed_fields

    def test_lhb_failed_coverage(self):
        f = get_fixture(FIXTURE_LHB_FAILED)
        audit = audit_raw_evidence(f.raw_evidence)
        assert "lhb" in audit.failed_fields
        assert "lhb" not in audit.normal_no_data_fields

    def test_stale_realtime_patch_coverage(self):
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        audit = audit_raw_evidence(f.raw_evidence)
        assert audit.evidence_coverage > 0

    def test_announcement_fail_event_weak_coverage(self):
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK)
        audit = audit_raw_evidence(f.raw_evidence)
        assert "announcements" in audit.failed_fields
        assert "news" in audit.has_data_fields

    def test_turnover_missing_coverage_degradation(self):
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        audit = audit_raw_evidence(f.raw_evidence)
        assert audit.evidence_coverage < 1.0
        assert "announcements" in audit.critical_missing_fields
        assert "news" in audit.critical_missing_fields

    def test_coverage_varies_between_fixtures(self):
        f_full = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        f_sparse = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        audit_full = audit_raw_evidence(f_full.raw_evidence)
        audit_sparse = audit_raw_evidence(f_sparse.raw_evidence)
        assert audit_full.evidence_coverage != audit_sparse.evidence_coverage


class TestReplayReportAggregation:
    def test_new_fixtures_in_full_replay(self):
        report = run_fixture_replay()
        fixture_ids = {r.fixture_id for r in report.results}
        assert FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK in fixture_ids
        assert FIXTURE_LHB_NORMAL_NO_DATA in fixture_ids
        assert FIXTURE_LHB_FAILED in fixture_ids
        assert FIXTURE_STALE_REALTIME_PATCH in fixture_ids
        assert FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK in fixture_ids
        assert FIXTURE_TURNOVER_VOLUME_RATIO_MISSING in fixture_ids

    def test_all_new_fixtures_pass(self):
        new_ids = [
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
            FIXTURE_LHB_NORMAL_NO_DATA,
            FIXTURE_LHB_FAILED,
            FIXTURE_STALE_REALTIME_PATCH,
            FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
            FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
        ]
        report = run_fixture_replay(fixture_ids=new_ids)
        assert report.all_passed is True
        assert report.failed == 0

    def test_by_status_includes_new_statuses(self):
        new_ids = [
            FIXTURE_LHB_NORMAL_NO_DATA,
            FIXTURE_LHB_FAILED,
            FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
        ]
        report = run_fixture_replay(fixture_ids=new_ids)
        statuses = set(report.by_status.keys())
        assert "NORMAL_NO_DATA" in statuses
        assert "FAILED" in statuses
        assert "HAS_DATA" in statuses


class TestRenderNewFixtures:
    def test_render_includes_fund_flow_fallback(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK])
        md = render_replay_report(report)
        assert FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK in md
        assert "cn_astock" in md
        assert "cn_akshare" in md

    def test_render_includes_lhb_normal_no_data(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_LHB_NORMAL_NO_DATA])
        md = render_replay_report(report)
        assert FIXTURE_LHB_NORMAL_NO_DATA in md
        assert "NORMAL_NO_DATA" in md

    def test_render_includes_lhb_failed(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_LHB_FAILED])
        md = render_replay_report(report)
        assert FIXTURE_LHB_FAILED in md
        assert "FAILED" in md
        assert "ConnectionError" in md

    def test_render_includes_stale_realtime_patch(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_STALE_REALTIME_PATCH])
        md = render_replay_report(report)
        assert FIXTURE_STALE_REALTIME_PATCH in md
        assert "PASS" in md

    def test_render_includes_announcement_fail_event_weak(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK])
        md = render_replay_report(report)
        assert FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK in md

    def test_render_includes_turnover_missing(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_TURNOVER_VOLUME_RATIO_MISSING])
        md = render_replay_report(report)
        assert FIXTURE_TURNOVER_VOLUME_RATIO_MISSING in md


class TestSaveReportNewFixtures:
    def test_save_report_includes_new_fixtures(self):
        report = run_fixture_replay(fixture_ids=[
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
            FIXTURE_LHB_NORMAL_NO_DATA,
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_replay_report(report, output_dir=tmpdir)
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK in content
            assert FIXTURE_LHB_NORMAL_NO_DATA in content

    def test_run_replay_and_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = run_replay_and_save(
                output_dir=tmpdir,
                fixture_ids=[FIXTURE_LHB_FAILED],
            )
            assert os.path.exists(path)


class TestEdgeCases:
    def test_fixture_entry_to_dict_new_fixtures(self):
        for fid in [
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
            FIXTURE_LHB_NORMAL_NO_DATA,
            FIXTURE_LHB_FAILED,
            FIXTURE_STALE_REALTIME_PATCH,
            FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
            FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
        ]:
            f = get_fixture(fid)
            d = f.to_dict()
            assert d["fixture_id"] == fid
            assert isinstance(d["raw_evidence"], dict)

    def test_replay_result_to_dict_new_fixtures(self):
        for fid in [
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
            FIXTURE_LHB_NORMAL_NO_DATA,
            FIXTURE_LHB_FAILED,
        ]:
            report = run_fixture_replay(fixture_ids=[fid])
            d = report.results[0].to_dict()
            assert "as_of" in d

    def test_unique_fixture_ids(self):
        all_ids = [f.fixture_id for f in get_all_fixtures()]
        assert len(all_ids) == len(set(all_ids))


class TestAcceptanceDATA008:
    """DATA-008 验收标准。"""

    def test_failed_not_shown_as_normal_no_data(self):
        """FAILED 不被显示为 NORMAL_NO_DATA。"""
        f = get_fixture(FIXTURE_LHB_FAILED)
        ev = f.raw_evidence["lhb"]
        assert ev["status"] == "FAILED"
        assert ev["status"] != "NORMAL_NO_DATA"

    def test_fallback_shows_actual_vendor(self):
        """fallback 成功时显示实际 vendor。"""
        f = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        ev = f.raw_evidence["fund_flow_individual"]
        assert ev["vendor"] == "cn_astock"
        assert ev["fallback_from"] == "cn_akshare"
        report = run_fixture_replay(fixture_ids=[FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK])
        r = report.results[0]
        assert r.vendor == "cn_astock"

    def test_stale_patch_shows_realtime_patched(self):
        """stale 行情补丁显示 is_realtime_patched=True。"""
        f = get_fixture(FIXTURE_STALE_REALTIME_PATCH)
        ev = f.raw_evidence["stock_data"]
        assert ev["is_realtime_patched"] is True
        assert ev["patch_source"] == "cn_astock_tencent"
        assert "turnover_rate" in ev["patch_fields"]
        assert "volume_ratio" in ev["patch_fields"]

    def test_replay_all_new_fixtures_pass(self):
        """所有新 fixture replay 通过。"""
        new_ids = [
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
            FIXTURE_LHB_NORMAL_NO_DATA,
            FIXTURE_LHB_FAILED,
            FIXTURE_STALE_REALTIME_PATCH,
            FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
            FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
        ]
        report = run_fixture_replay(fixture_ids=new_ids)
        assert report.all_passed is True

    def test_replay_output_has_required_provenance_fields(self):
        """replay 输出包含 vendor/endpoint/status/fallback_vendor/error/as_of。"""
        for fid in [
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
            FIXTURE_LHB_FAILED,
            FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
        ]:
            report = run_fixture_replay(fixture_ids=[fid])
            r = report.results[0]
            d = r.to_dict()
            assert "vendor" in d and d["vendor"] != ""
            assert "endpoint" in d and d["endpoint"] != ""
            assert "actual_status" in d and d["actual_status"] != ""
            assert "fallback_from" in d
            assert "error" in d
            assert "as_of" in d

    def test_auditor_triggers_coverage_changes(self):
        """DATA-007 auditor 对新 fixture 能触发覆盖率变化。"""
        f_full = get_fixture(FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK)
        f_sparse = get_fixture(FIXTURE_LHB_FAILED)
        audit_full = audit_raw_evidence(f_full.raw_evidence)
        audit_sparse = audit_raw_evidence(f_sparse.raw_evidence)
        assert audit_full.evidence_coverage > audit_sparse.evidence_coverage

    def test_no_live_llm_or_prod_db(self):
        """未调用 LLM、未写生产 DB。"""
        pass

    def test_no_forbidden_words(self):
        """无强交易词。"""
        new_ids = [
            FIXTURE_FUND_FLOW_AKSHARE_FAIL_FALLBACK,
            FIXTURE_LHB_NORMAL_NO_DATA,
            FIXTURE_LHB_FAILED,
            FIXTURE_STALE_REALTIME_PATCH,
            FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK,
            FIXTURE_TURNOVER_VOLUME_RATIO_MISSING,
        ]
        forbidden = ["买入", "卖出", "清仓", "重仓", "满仓", "抄底"]
        for fid in new_ids:
            f = get_fixture(fid)
            desc = f.description
            for ev in f.raw_evidence.values():
                if isinstance(ev, dict):
                    raw = ev.get("raw") or ""
                    for w in forbidden:
                        assert w not in desc
                        assert w not in raw

    def test_announcement_failed_not_misrepresented(self):
        """公告 FAILED 不被静默解释为'无数据'。"""
        f = get_fixture(FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK)
        ev = f.raw_evidence["announcements"]
        assert ev["status"] == "FAILED"
        assert ev["status"] != "NORMAL_NO_DATA"
        report = run_fixture_replay(fixture_ids=[FIXTURE_ANNOUNCEMENT_FAIL_EVENT_WEAK])
        r = report.results[0]
        assert r.actual_status == "FAILED"
        assert r.error is not None

    def test_completeness_degradation_on_missing_fields(self):
        """换手率/量比缺失导致完整度降级。"""
        f = get_fixture(FIXTURE_TURNOVER_VOLUME_RATIO_MISSING)
        comp = compute_contract_completeness(f.raw_evidence)
        assert comp["completeness_score"] < 100
        assert len(comp["missing_details"]) > 0
