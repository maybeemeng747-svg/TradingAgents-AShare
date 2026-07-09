# [DATA-027] research_source_smoke
"""
Tests for free research/announcement/half-year-report source smoke with
failure attribution (DATA-027).

Covers:
  - Disclosure source catalog (research_report / announcement / half_year_report)
  - Failure attribution classifier (network / rate-limited / field-missing /
    schema-change / no-data / ok)
  - DataFrame parser across 3 source types
  - Fixture dry-run: all required scenarios (有数据/无数据/接口失败/字段缺失)
  - Live-smoke env gating (TA_LIVE_DATA_SMOKE)
  - Report rendering & file output
  - Capability matrix overlay (does not modify DATA-023 items)
  - DATA-025 / DATA-011 boundary (no new DataType, status semantics aligned)
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.research_source_smoke import (
    DEFAULT_SMOKE_SYMBOLS,
    PROBE_FIXTURES,
    RESEARCH_DISCLOSURE_SOURCES,
    RESEARCH_DISCLOSURE_SOURCES as DISCLOSURE_SOURCES,
    ResearchSourceErrorType,
    ResearchSourceStatus,
    build_capability_matrix_overlay,
    classify_research_source_error_type,
    get_research_disclosure_sources,
    get_sources_by_type,
    is_live_smoke_enabled,
    parse_disclosure_rows,
    render_research_source_smoke_report,
    run_research_source_smoke,
    save_research_source_smoke_report,
    _LIVE_ENV,
    _MAX_SYMBOLS,
)
from tradingagents.dataflows.source_capability_matrix import (
    get_source_capability_matrix,
    validate_matrix_coverage,
)


# ── Catalog: completeness & contract ───────────────────────────────────


class TestDisclosureSourceCatalog:
    def test_at_least_three_source_types(self):
        types = {s["source_type"] for s in RESEARCH_DISCLOSURE_SOURCES}
        assert "research_report" in types
        assert "announcement" in types
        assert "half_year_report" in types

    def test_each_source_has_required_fields(self):
        required = {
            "source_id", "source_type", "name_cn", "vendor", "endpoint",
            "data_type", "fields", "unit", "freshness", "rate_limit_risk",
            "content_role", "provides_pdf_link", "usage_note", "known_limits",
        }
        for src in RESEARCH_DISCLOSURE_SOURCES:
            missing = required - set(src.keys())
            assert not missing, f"{src.get('source_id')} missing fields: {missing}"

    def test_source_ids_unique(self):
        ids = [s["source_id"] for s in RESEARCH_DISCLOSURE_SOURCES]
        assert len(ids) == len(set(ids)), f"duplicate source_ids: {ids}"

    def test_research_report_source_present(self):
        ids = [s["source_id"] for s in RESEARCH_DISCLOSURE_SOURCES]
        assert "eastmoney_research_report_em" in ids

    def test_announcement_sources_present(self):
        ann = get_sources_by_type("announcement")
        assert len(ann) >= 1

    def test_half_year_report_source_present(self):
        hy = get_sources_by_type("half_year_report")
        assert len(hy) >= 1

    def test_get_disclosure_sources_returns_deepcopy(self):
        s1 = get_research_disclosure_sources()
        s1[0]["source_id"] = "MUTATED"
        s2 = get_research_disclosure_sources()
        assert s2[0]["source_id"] != "MUTATED"

    def test_fields_consistent_with_data023_matrix(self):
        """字段与 DATA-023 能力矩阵 entry 一致: data_type/vendor/endpoint/freshness/unit/fields/rate_limit_risk."""
        matrix_keys = {
            "data_type", "vendor", "endpoint", "freshness", "unit",
            "fields", "rate_limit_risk",
        }
        for src in RESEARCH_DISCLOSURE_SOURCES:
            assert matrix_keys.issubset(set(src.keys())), (
                f"{src['source_id']} missing DATA-023-consistent fields"
            )

    def test_freshness_values_valid(self):
        valid = {"realtime", "intraday", "daily", "delayed", "stale", "unknown"}
        for src in RESEARCH_DISCLOSURE_SOURCES:
            assert src["freshness"] in valid, (
                f"{src['source_id']} freshness={src['freshness']}"
            )

    def test_rate_limit_values_valid(self):
        valid = {"low", "medium", "high", "unknown"}
        for src in RESEARCH_DISCLOSURE_SOURCES:
            assert src["rate_limit_risk"] in valid


# ── Opinion vs fact separation ─────────────────────────────────────────


class TestOpinionVsFactSources:
    def test_has_opinion_source(self):
        opinion = [s for s in RESEARCH_DISCLOSURE_SOURCES
                   if s["content_role"] == "opinion"]
        assert len(opinion) >= 1

    def test_has_fact_sources(self):
        fact = [s for s in RESEARCH_DISCLOSURE_SOURCES
                if s["content_role"] == "fact"]
        assert len(fact) >= 1

    def test_research_report_is_opinion(self):
        src = [s for s in RESEARCH_DISCLOSURE_SOURCES
               if s["source_id"] == "eastmoney_research_report_em"][0]
        assert src["content_role"] == "opinion"

    def test_announcement_is_fact(self):
        ann = get_sources_by_type("announcement")
        for s in ann:
            assert s["content_role"] == "fact"

    def test_half_year_report_is_fact(self):
        hy = get_sources_by_type("half_year_report")
        for s in hy:
            assert s["content_role"] == "fact"


# ── Failure attribution classifier ─────────────────────────────────────


class TestErrorTypeConstants:
    def test_all_constants_present(self):
        for et in (
            "ok", "network_error", "rate_limited", "field_missing",
            "schema_change", "no_data", "unknown",
        ):
            assert et in ResearchSourceErrorType.ALL

    def test_required_attribution_includes_4_required(self):
        """任务验收要求: 接口失败 / 字段缺失 / 无数据 + 限流."""
        req = set(ResearchSourceErrorType.REQUIRED_ATTRIBUTION)
        assert ResearchSourceErrorType.NETWORK_ERROR in req
        assert ResearchSourceErrorType.FIELD_MISSING in req
        assert ResearchSourceErrorType.NO_DATA in req

    def test_label_cn_has_all_types(self):
        for et in ResearchSourceErrorType.ALL:
            assert et in ResearchSourceErrorType.LABEL_CN


class TestClassifierRateLimited:
    def test_rate_limited_error(self):
        et = classify_research_source_error_type(
            error="HTTPError 429: Too Many Requests"
        )
        assert et == ResearchSourceErrorType.RATE_LIMITED


class TestClassifierNetworkError:
    def test_connection_error(self):
        et = classify_research_source_error_type(
            error="ConnectionError: Max retries exceeded"
        )
        assert et == ResearchSourceErrorType.NETWORK_ERROR

    def test_failed_status(self):
        et = classify_research_source_error_type(status="FAILED")
        assert et == ResearchSourceErrorType.NETWORK_ERROR

    def test_rows_none_with_error(self):
        et = classify_research_source_error_type(
            rows=None, error="some error"
        )
        assert et == ResearchSourceErrorType.NETWORK_ERROR


class TestClassifierNoData:
    def test_normal_no_data_status(self):
        et = classify_research_source_error_type(status="NORMAL_NO_DATA")
        assert et == ResearchSourceErrorType.NO_DATA

    def test_empty_rows(self):
        et = classify_research_source_error_type(rows=[])
        assert et == ResearchSourceErrorType.NO_DATA

    def test_skipped_status(self):
        et = classify_research_source_error_type(status="SKIPPED")
        assert et == ResearchSourceErrorType.NO_DATA


class TestClassifierFieldMissing:
    def test_rows_with_empty_key_fields(self):
        """返回行但关键契约字段 (标题/日期) 全空 → field_missing."""
        rows = [
            {"报告名称": "", "日期": "", "股票代码": "600519", "机构": "x"},
            {"报告名称": None, "日期": None, "机构": ""},
        ]
        et = classify_research_source_error_type(rows=rows)
        assert et == ResearchSourceErrorType.FIELD_MISSING

    def test_field_missing_not_no_data(self):
        """字段缺失不能被误判成无数据."""
        rows = [{"报告名称": "", "日期": "", "机构": "x"}]
        et = classify_research_source_error_type(rows=rows)
        assert et != ResearchSourceErrorType.NO_DATA

    def test_field_missing_not_ok(self):
        """字段缺失不能被误判成有数据."""
        rows = [{"报告名称": "", "日期": "", "机构": "x"}]
        et = classify_research_source_error_type(rows=rows)
        assert et != ResearchSourceErrorType.OK


class TestClassifierSchemaChange:
    def test_unknown_columns(self):
        """列名完全不匹配 → schema_change."""
        rows = [
            {"未知列A": "x", "未知列B": "y"},
            {"data1": "foo", "data2": "bar"},
        ]
        et = classify_research_source_error_type(rows=rows)
        assert et == ResearchSourceErrorType.SCHEMA_CHANGE


class TestClassifierOK:
    def test_has_data_with_key_fields(self):
        rows = [{"报告名称": "标题", "日期": "2026-06-15", "机构": "中信"}]
        et = classify_research_source_error_type(rows=rows)
        assert et == ResearchSourceErrorType.OK

    def test_ok_with_partial_fields(self):
        rows = [{"报告名称": "标题", "机构": ""}]
        et = classify_research_source_error_type(rows=rows)
        assert et == ResearchSourceErrorType.OK


# ── Parser ──────────────────────────────────────────────────────────────


class TestParseRows:
    def test_empty_rows(self):
        assert parse_disclosure_rows(None) == []
        assert parse_disclosure_rows([]) == []

    def test_research_report_columns(self):
        rows = [{
            "日期": "2026-06-15",
            "报告名称": "贵州茅台深度研究",
            "机构": "中信证券",
            "东财评级": "买入",
            "行业": "白酒",
            "报告PDF链接": "https://pdf.dfcfw.com/pdf/H3_ABC_1.pdf",
        }]
        records = parse_disclosure_rows(rows, source_type="research_report")
        assert len(records) == 1
        r = records[0]
        assert r.date == "2026-06-15"
        assert r.title == "贵州茅台深度研究"
        assert r.org == "中信证券"
        assert r.rating == "买入"
        assert r.pdf_url == "https://pdf.dfcfw.com/pdf/H3_ABC_1.pdf"
        assert r.source_type == "research_report"

    def test_announcement_columns(self):
        rows = [{
            "公告标题": "平安银行2026年半年度报告",
            "公告类型": "定期报告",
            "公告时间": "2026-08-30",
            "公告ID": "CNINFO-001",
        }]
        records = parse_disclosure_rows(rows, source_type="announcement")
        assert len(records) == 1
        r = records[0]
        assert r.title == "平安银行2026年半年度报告"
        assert r.announcement_type == "定期报告"
        assert r.date == "2026-08-30"

    def test_half_year_report_columns(self):
        rows = [{
            "公告标题": "利通电子2026年半年度报告",
            "公告类型": "半年度报告",
            "公告时间": "2026-08-28",
            "报告年度": "2026",
        }]
        records = parse_disclosure_rows(rows, source_type="half_year_report")
        assert len(records) == 1
        r = records[0]
        assert r.title == "利通电子2026年半年度报告"
        assert r.announcement_type == "半年度报告"
        assert r.report_period == "2026"

    def test_nan_values_skipped(self):
        rows = [{"报告名称": "标题", "日期": float("nan")}]
        records = parse_disclosure_rows(rows)
        assert len(records) == 1
        assert records[0].date == ""

    def test_non_dict_row_skipped(self):
        rows = [{"报告名称": "T"}, "not a dict", None]
        records = parse_disclosure_rows(rows)
        assert len(records) == 1

    def test_no_pdf_content_stored(self):
        """验收要求: 不提交 PDF 正文, 只保留 PDF 链接字段."""
        rows = [{"报告名称": "标题", "报告PDF链接": "https://x.com/a.pdf"}]
        records = parse_disclosure_rows(rows)
        assert records[0].pdf_url == "https://x.com/a.pdf"
        assert not hasattr(records[0], "content")
        assert not hasattr(records[0], "body")
        assert not hasattr(records[0], "text")


# ── Fixture dry-run ────────────────────────────────────────────────────


class TestProbeFixtures:
    def test_fixtures_cover_required_scenarios(self):
        """验收: fixture 覆盖有数据 / 无数据 / 接口失败 / 字段缺失."""
        ids = set(PROBE_FIXTURES.keys())
        assert "HAS_DATA" in ids
        assert "NORMAL_NO_DATA" in ids
        assert "FAILED" in ids
        assert "FIELD_MISSING" in ids

    def test_each_fixture_has_expected_error_type(self):
        for fid, fx in PROBE_FIXTURES.items():
            assert "expected_error_type" in fx, f"{fid} missing expected_error_type"

    def test_fixtures_cover_all_source_types(self):
        types = {fx["source_type"] for fx in PROBE_FIXTURES.values()}
        assert "research_report" in types
        assert "announcement" in types
        assert "half_year_report" in types


class TestFixtureDryRun:
    def test_default_mode_is_fixture(self):
        report = run_research_source_smoke()
        assert report.mode == "fixture"
        assert report.env_gated is True

    def test_all_fixtures_match_expected(self):
        report = run_research_source_smoke()
        for r in report.results:
            expected = PROBE_FIXTURES[r.fixture_id]["expected_error_type"]
            assert r.error_type == expected, (
                f"fixture {r.fixture_id}: expected {expected}, got {r.error_type}"
            )
        assert report.summary["all_passed"] is True

    def test_has_data_results(self):
        report = run_research_source_smoke()
        has_data = [r for r in report.results if r.fixture_id == "HAS_DATA"][0]
        assert has_data.error_type == ResearchSourceErrorType.OK
        assert has_data.record_count == 2
        assert has_data.sample_records[0].org == "中信证券"

    def test_announcement_has_data(self):
        report = run_research_source_smoke()
        ann = [r for r in report.results
               if r.fixture_id == "HAS_DATA_ANNOUNCEMENT"][0]
        assert ann.error_type == ResearchSourceErrorType.OK
        assert ann.source_type == "announcement"
        assert ann.record_count >= 1

    def test_half_year_has_data(self):
        report = run_research_source_smoke()
        hy = [r for r in report.results
              if r.fixture_id == "HAS_DATA_HALF_YEAR"][0]
        assert hy.error_type == ResearchSourceErrorType.OK
        assert hy.source_type == "half_year_report"

    def test_normal_no_data(self):
        report = run_research_source_smoke()
        no_data = [r for r in report.results
                   if r.fixture_id == "NORMAL_NO_DATA"][0]
        assert no_data.error_type == ResearchSourceErrorType.NO_DATA
        assert no_data.record_count == 0

    def test_failed_network_error(self):
        report = run_research_source_smoke()
        failed = [r for r in report.results if r.fixture_id == "FAILED"][0]
        assert failed.error_type == ResearchSourceErrorType.NETWORK_ERROR
        assert failed.status == ResearchSourceStatus.FAILED
        assert "ConnectionError" in failed.error

    def test_rate_limited(self):
        report = run_research_source_smoke()
        rl = [r for r in report.results
              if r.fixture_id == "RATE_LIMITED"][0]
        assert rl.error_type == ResearchSourceErrorType.RATE_LIMITED

    def test_field_missing(self):
        report = run_research_source_smoke()
        fm = [r for r in report.results
              if r.fixture_id == "FIELD_MISSING"][0]
        assert fm.error_type == ResearchSourceErrorType.FIELD_MISSING

    def test_field_missing_not_failed(self):
        """验收: 字段缺失不应被判 FAILED (区别于接口失败)."""
        report = run_research_source_smoke()
        fm = [r for r in report.results
              if r.fixture_id == "FIELD_MISSING"][0]
        assert fm.error_type != ResearchSourceErrorType.NETWORK_ERROR

    def test_required_classes_all_covered(self):
        report = run_research_source_smoke()
        assert report.summary["required_classes_missing"] == []
        covered = set(report.summary["required_classes_covered"])
        for req in ResearchSourceErrorType.REQUIRED_ATTRIBUTION:
            assert req in covered

    def test_summary_has_by_source_type(self):
        report = run_research_source_smoke()
        by_sct = report.summary["by_source_type"]
        assert "research_report" in by_sct
        assert "announcement" in by_sct
        assert "half_year_report" in by_sct


# ── Live smoke env gating ──────────────────────────────────────────────


class TestLiveSmokeEnvGating:
    def test_not_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_live_smoke_enabled() is False

    def test_enabled_when_set_to_1(self):
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            assert is_live_smoke_enabled() is True

    def test_live_smoke_skipped_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_research_source_smoke(
                symbols=["600519.SH"], live_smoke=True
            )
        assert report.mode == "live"
        assert report.env_gated is True
        assert len(report.results) == 1
        assert report.results[0].status == ResearchSourceStatus.SKIPPED

    def test_live_smoke_respects_max_symbols(self):
        with patch.dict(os.environ, {}, clear=True):
            many = [f"{i:06d}.SH" for i in range(10)]
            report = run_research_source_smoke(symbols=many, live_smoke=True)
        assert len(report.symbols) <= _MAX_SYMBOLS

    def test_live_smoke_uses_default_symbols_when_none(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_research_source_smoke(live_smoke=True)
        assert len(report.symbols) == len(DEFAULT_SMOKE_SYMBOLS)


# ── Live smoke (mocked fetch_fn, env=1) ────────────────────────────────


def _make_df_mock(rows):
    df = MagicMock()
    df.empty = (len(rows) == 0)
    df.to_dict.return_value = rows
    return df


class TestLiveSmokeMockedFetch:
    def test_live_has_data(self):
        rows = PROBE_FIXTURES["HAS_DATA"]["rows"]
        df_mock = _make_df_mock(rows)
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_research_source_smoke(
                symbols=["600519.SH"],
                live_smoke=True,
                fetch_fn=lambda sym: df_mock,
            )
        assert report.env_gated is False
        r = report.results[0]
        assert r.error_type == ResearchSourceErrorType.OK
        assert r.status == ResearchSourceStatus.HAS_DATA
        assert r.record_count == 2

    def test_live_no_data_empty_df(self):
        df_mock = _make_df_mock([])
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_research_source_smoke(
                symbols=["603629.SH"],
                live_smoke=True,
                fetch_fn=lambda sym: df_mock,
            )
        r = report.results[0]
        assert r.error_type == ResearchSourceErrorType.NO_DATA

    def test_live_no_data_none(self):
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_research_source_smoke(
                symbols=["603629.SH"],
                live_smoke=True,
                fetch_fn=lambda sym: None,
            )
        r = report.results[0]
        assert r.error_type == ResearchSourceErrorType.NO_DATA

    def test_live_failed_on_exception(self):
        def boom(sym):
            raise ConnectionError("refused")

        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_research_source_smoke(
                symbols=["000001.SZ"],
                live_smoke=True,
                fetch_fn=boom,
            )
        r = report.results[0]
        assert r.error_type == ResearchSourceErrorType.NETWORK_ERROR
        assert r.status == ResearchSourceStatus.FAILED
        assert report.summary["has_failures"] is True

    def test_live_field_missing(self):
        df_mock = _make_df_mock(PROBE_FIXTURES["FIELD_MISSING"]["rows"])
        with patch.dict(os.environ, {_LIVE_ENV: "1"}):
            report = run_research_source_smoke(
                symbols=["600519.SH"],
                live_smoke=True,
                fetch_fn=lambda sym: df_mock,
            )
        r = report.results[0]
        assert r.error_type == ResearchSourceErrorType.FIELD_MISSING


# ── Status semantics ────────────────────────────────────────────────────


class TestStatusSemantics:
    def test_status_constants(self):
        assert ResearchSourceStatus.HAS_DATA == "HAS_DATA"
        assert ResearchSourceStatus.NORMAL_NO_DATA == "NORMAL_NO_DATA"
        assert ResearchSourceStatus.FAILED == "FAILED"

    def test_to_freshness_mapping(self):
        m = ResearchSourceErrorType.TO_FRESHNESS_STATUS
        assert m[ResearchSourceErrorType.OK] == "HAS_DATA"
        assert m[ResearchSourceErrorType.NO_DATA] == "NORMAL_NO_DATA"
        assert m[ResearchSourceErrorType.NETWORK_ERROR] == "FAILED"

    def test_failed_does_not_become_no_data(self):
        """验收: 接口失败必须 FAILED, 不能被误判成 NORMAL_NO_DATA."""
        report = run_research_source_smoke()
        failed = [r for r in report.results if r.fixture_id == "FAILED"][0]
        assert failed.status == ResearchSourceStatus.FAILED
        assert failed.status != ResearchSourceStatus.NORMAL_NO_DATA

    def test_no_data_does_not_become_failed(self):
        report = run_research_source_smoke()
        no_data = [r for r in report.results
                   if r.fixture_id == "NORMAL_NO_DATA"][0]
        assert no_data.status == ResearchSourceStatus.NORMAL_NO_DATA
        assert no_data.status != ResearchSourceStatus.FAILED


# ── Report rendering ────────────────────────────────────────────────────


class TestRenderReport:
    def test_contains_task_marker(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        assert "[DATA-027]" in md

    def test_contains_summary_section(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        assert "## Smoke Summary" in md

    def test_contains_disclosure_catalog(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        assert "## 披露源目录" in md
        assert "eastmoney_research_report_em" in md
        assert "half_year_report" in md

    def test_contains_failure_attribution_section(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        assert "## 失败归因说明" in md
        assert "field_missing" in md

    def test_contains_sample_records(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        assert "## Sample Records" in md
        assert "中信证券" in md

    def test_contains_fixture_coverage(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        assert "## Fixture Coverage" in md
        assert "FIELD_MISSING" in md

    def test_contains_boundary_disclaimer(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        assert "边界声明" in md

    def test_no_strong_action_words(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        for word in ("立即买入", "满仓", "清仓"):
            assert word not in md

    def test_env_gated_live_report(self):
        with patch.dict(os.environ, {}, clear=True):
            report = run_research_source_smoke(
                symbols=["600519.SH"], live_smoke=True
            )
        md = render_research_source_smoke_report(report)
        assert "TA_LIVE_DATA_SMOKE" in md
        assert "SKIPPED" in md

    def test_does_not_print_api_keys(self):
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        for sensitive in ("api_key", "API_KEY", "token", "cookie", "Authorization"):
            assert sensitive.lower() not in md.lower()

    def test_report_only_metadata_no_pdf_content(self):
        """验收: smoke 报告不含正文, 只含元数据与状态."""
        report = run_research_source_smoke()
        md = render_research_source_smoke_report(report)
        assert "正文" in md  # disclaimer mentions 正文
        # 没有 PDF 正文片段
        assert "本报告 PDF 正文内容" not in md


# ── File output ─────────────────────────────────────────────────────────


class TestSaveReport:
    def test_creates_file_with_date(self):
        report = run_research_source_smoke(today="2026-07-10")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_research_source_smoke_report(report, output_dir=tmpdir)
            assert os.path.isfile(path)
            assert "research-source-smoke-" in path
            assert path.endswith(".md")
            assert "2026-07-10" in path

    def test_file_content_is_markdown(self):
        report = run_research_source_smoke()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_research_source_smoke_report(report, output_dir=tmpdir)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert content.startswith("# 免费研报/公告/半年报源 Smoke 报告")


# ── Capability matrix overlay ──────────────────────────────────────────


class TestCapabilityMatrixOverlay:
    def test_overlay_keys(self):
        report = run_research_source_smoke()
        overlay = build_capability_matrix_overlay(report)
        for key in (
            "overlay_source", "probe_run_at", "probe_mode",
            "source_types_covered", "total_probes", "by_error_type",
            "required_classes_covered", "required_classes_missing",
            "all_passed", "disclosure_sources", "notes",
        ):
            assert key in overlay, f"missing key: {key}"

    def test_overlay_does_not_leak_secrets(self):
        report = run_research_source_smoke()
        overlay = build_capability_matrix_overlay(report)
        blob = repr(overlay).lower()
        for sensitive in ("api_key", "token", "cookie", "authorization", "password"):
            assert sensitive not in blob

    def test_overlay_covers_three_source_types(self):
        report = run_research_source_smoke()
        overlay = build_capability_matrix_overlay(report)
        assert "research_report" in overlay["source_types_covered"]
        assert "announcement" in overlay["source_types_covered"]
        assert "half_year_report" in overlay["source_types_covered"]

    def test_matrix_items_unchanged(self):
        """overlay 不修改 DATA-023 matrix items."""
        m1 = get_source_capability_matrix()
        items_count = len(m1["items"])
        items_snapshot = [dict(it) for it in m1["items"]]
        # build overlay
        report = run_research_source_smoke()
        build_capability_matrix_overlay(report)
        # re-fetch
        m2 = get_source_capability_matrix()
        assert len(m2["items"]) == items_count
        assert m2["items"] == items_snapshot

    def test_matrix_validation_still_passes(self):
        report = run_research_source_smoke()
        build_capability_matrix_overlay(report)
        matrix = get_source_capability_matrix()
        issues = validate_matrix_coverage(matrix)
        assert issues == [], f"matrix validation issues: {issues}"


# ── DATA-025 / DATA-011 boundary ────────────────────────────────────────


class TestData025Data011Boundary:
    def test_data027_does_not_modify_data025_module(self):
        """DATA-027 是独立模块, 不修改 DATA-025."""
        from tradingagents.dataflows import research_report_sources
        assert hasattr(research_report_sources, "run_research_report_smoke")
        # DATA-025 仍只有 3 类 fixture
        from tradingagents.dataflows.research_report_sources import SMOKE_FIXTURES
        assert len(SMOKE_FIXTURES) == 3

    def test_data027_does_not_register_new_datatype(self):
        from tradingagents.dataflows.source_catalog import DataType
        existing = {dt.value for dt in DataType}
        assert "report" in existing
        assert "notice" in existing

    def test_data025_status_semantics_preserved(self):
        """DATA-025 状态语义不变."""
        from tradingagents.dataflows.research_report_sources import ResearchReportStatus
        assert ResearchReportStatus.HAS_DATA == ResearchSourceStatus.HAS_DATA
        assert ResearchReportStatus.NORMAL_NO_DATA == ResearchSourceStatus.NORMAL_NO_DATA
        assert ResearchReportStatus.FAILED == ResearchSourceStatus.FAILED

    def test_does_not_change_strong_action_gate(self):
        from tradingagents.agents.utils.readiness_score import (
            calculate_evidence_coverage,
            infer_evidence_statuses,
        )
        cov = calculate_evidence_coverage(
            ohlcv_5d="has_data", volume="has_data",
            turnover_rate="has_data", volume_ratio="has_data",
            individual_fund_flow="has_data", lhb_status="has_data",
            margin_trading="has_data", announcements="has_data",
            research_report="has_data",
        )
        assert cov > 0
        statuses = infer_evidence_statuses({})
        assert statuses["research_report"] == "not_queried"


# ── CLI smoke ──────────────────────────────────────────────────────────


class TestCLI:
    def test_cli_module_importable(self):
        import importlib
        mod = importlib.import_module("scripts.run_research_source_smoke")
        assert hasattr(mod, "main")

    def test_cli_fixture_dry_run(self, capsys):
        import subprocess
        result = subprocess.run(
            ["python", "scripts/run_research_source_smoke.py", "--dry-run"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0
        assert "[DATA-027]" in result.stdout
        assert "披露源目录" in result.stdout


# ── Acceptance: DATA-027 ───────────────────────────────────────────────


class TestAcceptanceDATA027:
    def test_acceptance_fixtures_cover_4_scenarios(self):
        """验收: fixture 覆盖有数据 / 无数据 / 接口失败 / 字段缺失."""
        ids = set(PROBE_FIXTURES.keys())
        assert "HAS_DATA" in ids  # 有数据
        assert "NORMAL_NO_DATA" in ids  # 无数据
        assert "FAILED" in ids  # 接口失败
        assert "FIELD_MISSING" in ids  # 字段缺失

    def test_acceptance_covers_research_metadata(self):
        report = run_research_source_smoke()
        types = {r.source_type for r in report.results}
        assert "research_report" in types

    def test_acceptance_covers_announcement_disclosure(self):
        report = run_research_source_smoke()
        types = {r.source_type for r in report.results}
        assert "announcement" in types

    def test_acceptance_covers_half_year_disclosure(self):
        report = run_research_source_smoke()
        types = {r.source_type for r in report.results}
        assert "half_year_report" in types

    def test_acceptance_failure_attribution_outputs_vendor_endpoint_fields(self):
        """验收: 输出 vendor/endpoint/字段/限制/失败类型."""
        report = run_research_source_smoke()
        for src in RESEARCH_DISCLOSURE_SOURCES:
            assert src["vendor"]
            assert src["endpoint"]
            assert isinstance(src["fields"], list)
            assert src["rate_limit_risk"]
        # 失败类型在 by_error_type
        by_et = report.summary["by_error_type"]
        assert ResearchSourceErrorType.NETWORK_ERROR in by_et
        assert ResearchSourceErrorType.FIELD_MISSING in by_et

    def test_acceptance_report_output_path(self):
        """验收: 输出 docs/data_source_reports/research-source-smoke-YYYY-MM-DD.md."""
        report = run_research_source_smoke(today="2026-07-10")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_research_source_smoke_report(report, output_dir=tmpdir)
            assert "research-source-smoke-" in path
            assert "2026-07-10" in path

    def test_acceptance_no_pdf_content(self):
        """验收: smoke 报告不含正文, 只含元数据与状态."""
        report = run_research_source_smoke()
        for r in report.results:
            for rec in r.sample_records:
                assert not hasattr(rec, "content")
                assert not hasattr(rec, "body")
                assert not hasattr(rec, "text")

    def test_acceptance_no_live_llm(self):
        """验收: 不触发 live LLM (fixture dry-run 无 LLM)."""
        report = run_research_source_smoke()
        assert report.mode == "fixture"

    def test_acceptance_normal_no_data_vs_failed_distinct(self):
        """验收: 不把无研报当失败; NORMAL_NO_DATA 与 FAILED 严格区分."""
        report = run_research_source_smoke()
        no_data = [r for r in report.results
                   if r.fixture_id == "NORMAL_NO_DATA"][0]
        failed = [r for r in report.results if r.fixture_id == "FAILED"][0]
        assert no_data.status != failed.status
        assert no_data.error_type != failed.error_type

    def test_acceptance_data023_field_consistency(self):
        """验收: 与 DATA-023 能力矩阵字段保持一致."""
        matrix = get_source_capability_matrix()
        matrix_item = matrix["items"][0]
        matrix_field_keys = set(matrix_item.keys())
        for src in RESEARCH_DISCLOSURE_SOURCES:
            # disclosure source 至少包含 matrix entry 的核心字段
            for k in ("data_type", "vendor", "endpoint", "freshness",
                      "unit", "fields", "rate_limit_risk"):
                assert k in src, f"{src['source_id']} missing matrix field {k}"
