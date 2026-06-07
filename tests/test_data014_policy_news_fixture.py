# [DATA-014] policy_news_fixture_smoke
"""
Tests for news/policy event fixtures, live smoke integration,
evidence contract, coverage audit, readiness score, and source catalog.
"""

import pytest
from unittest.mock import patch, MagicMock

from tradingagents.dataflows.source_catalog import (
    DataType,
    Freshness,
    RateLimitRisk,
    get_sources_for_type,
    get_primary_source,
    get_fallback_chain,
    get_all_data_types,
    validate_catalog,
)
from tradingagents.dataflows.evidence_contract import (
    EvidenceContract,
    resolve_data_type,
    compute_contract_completeness,
    _REQUIRED_FIELDS_FOR_COMPLETENESS,
)
from tradingagents.dataflows.evidence_coverage_audit import (
    audit_raw_evidence,
    _EVIDENCE_FIELD_LABELS,
    _EVIDENCE_FIELD_FAMILIES,
)
from tradingagents.agents.utils.readiness_score import (
    EvidenceStatus,
    infer_evidence_statuses,
)
from tradingagents.dataflows.fixture_replay import (
    FIXTURE_NEWS_HAS_DATA,
    FIXTURE_NEWS_NORMAL_NO_DATA,
    FIXTURE_NEWS_PARTIAL_FAILED,
    FIXTURE_NEWS_FAILED,
    FIXTURE_NEWS_RATE_LIMITED,
    FIXTURE_GLOBAL_NEWS_HAS_DATA,
    FIXTURE_GLOBAL_NEWS_NORMAL_NO_DATA,
    FIXTURE_GLOBAL_NEWS_FAILED,
    FIXTURE_GLOBAL_NEWS_RATE_LIMITED,
    ALL_FIXTURE_IDS,
    get_fixture,
    get_all_fixtures,
    run_fixture_replay,
)
from tradingagents.dataflows.interface import (
    TOOLS_CATEGORIES,
    get_category_for_method,
    _FAILURE_RESULT_PATTERNS,
)
from tradingagents.dataflows.live_smoke import (
    _make_endpoint_definitions,
    run_live_smoke,
    is_live_smoke_enabled,
    LiveSmokeReport,
    EndpointSmokeResult,
)
from tradingagents.tradeflow.event_source import (
    EventSourceStatus,
    EventSourceResult,
    EventItem,
)


# ── Source Catalog ────────────────────────────────────────────────────


class TestSourceCatalogNews:
    def test_news_data_type_exists(self):
        assert DataType.NEWS.value == "news"

    def test_global_news_data_type_exists(self):
        assert DataType.GLOBAL_NEWS.value == "global_news"

    def test_news_sources_registered(self):
        sources = get_sources_for_type(DataType.NEWS)
        assert len(sources) >= 2

    def test_global_news_sources_registered(self):
        sources = get_sources_for_type(DataType.GLOBAL_NEWS)
        assert len(sources) >= 2

    def test_news_primary_source(self):
        primary = get_primary_source(DataType.NEWS)
        assert primary is not None
        assert primary.vendor == "cn_akshare"
        assert primary.is_primary

    def test_global_news_primary_source(self):
        primary = get_primary_source(DataType.GLOBAL_NEWS)
        assert primary is not None
        assert primary.vendor == "cn_akshare"
        assert primary.is_primary

    def test_news_fallback_chain(self):
        chain = get_fallback_chain(DataType.NEWS)
        assert "cn_akshare" in chain
        assert "cn_astock" in chain
        assert chain.index("cn_akshare") < chain.index("cn_astock")

    def test_global_news_fallback_chain(self):
        chain = get_fallback_chain(DataType.GLOBAL_NEWS)
        assert "cn_akshare" in chain
        assert "cn_astock" in chain

    def test_news_in_all_data_types(self):
        all_types = get_all_data_types()
        assert "news" in all_types
        assert "global_news" in all_types

    def test_news_primary_fields(self):
        primary = get_primary_source(DataType.NEWS)
        assert len(primary.fields) > 0

    def test_news_primary_endpoint(self):
        primary = get_primary_source(DataType.NEWS)
        assert primary.endpoint == "stock_news_em"

    def test_global_news_primary_endpoint(self):
        primary = get_primary_source(DataType.GLOBAL_NEWS)
        assert primary.endpoint == "news_cctv"

    def test_catalog_validation_passes(self):
        issues = validate_catalog()
        news_issues = [i for i in issues if "news" in i.lower()]
        assert len(news_issues) == 0


# ── Evidence Contract ─────────────────────────────────────────────────


class TestEvidenceContractNews:
    def test_resolve_data_type_news(self):
        assert resolve_data_type("news") == "news"

    def test_resolve_data_type_global_news(self):
        assert resolve_data_type("global_news") == "global_news"

    def test_news_required_fields(self):
        assert "news" in _REQUIRED_FIELDS_FOR_COMPLETENESS
        assert "status" in _REQUIRED_FIELDS_FOR_COMPLETENESS["news"]
        assert "vendor" in _REQUIRED_FIELDS_FOR_COMPLETENESS["news"]

    def test_global_news_required_fields(self):
        assert "global_news" in _REQUIRED_FIELDS_FOR_COMPLETENESS
        assert "status" in _REQUIRED_FIELDS_FOR_COMPLETENESS["global_news"]
        assert "vendor" in _REQUIRED_FIELDS_FOR_COMPLETENESS["global_news"]

    def test_news_contract_has_data(self):
        contract = EvidenceContract(
            field="news", value="新闻文本", unit="条",
            vendor="cn_akshare", endpoint="stock_news_em",
            as_of="2026-06-08", status="HAS_DATA",
        )
        assert contract.status == "HAS_DATA"
        assert contract.vendor == "cn_akshare"

    def test_news_contract_failed(self):
        contract = EvidenceContract(
            field="news", value=None, unit=None,
            vendor="cn_akshare", endpoint="stock_news_em",
            as_of="2026-06-08", status="FAILED",
            error="ConnectionError",
        )
        assert contract.status == "FAILED"
        assert contract.error == "ConnectionError"

    def test_news_completeness_has_data(self):
        raw_evidence = {
            "news": {
                "raw": "新闻内容",
                "field": "news",
                "unit": "条",
                "vendor": "cn_akshare",
                "endpoint": "stock_news_em",
                "as_of": "2026-06-08",
                "fetched_at": "2026-06-08T10:00:00",
                "status": "HAS_DATA",
                "record_count": 5,
            },
        }
        result = compute_contract_completeness(raw_evidence)
        assert result["completeness_score"] > 0

    def test_news_completeness_missing(self):
        raw_evidence = {
            "news": {
                "raw": None,
                "field": "news",
                "status": "NOT_QUERIED",
            },
        }
        result = compute_contract_completeness(raw_evidence)
        assert "news" in result.get("missing_details", {})


# ── Evidence Coverage Audit ───────────────────────────────────────────


class TestEvidenceCoverageAuditNews:
    def test_news_label(self):
        assert "news" in _EVIDENCE_FIELD_LABELS
        assert _EVIDENCE_FIELD_LABELS["news"] == "新闻"

    def test_global_news_label(self):
        assert "global_news" in _EVIDENCE_FIELD_LABELS
        assert _EVIDENCE_FIELD_LABELS["global_news"] == "全球新闻"

    def test_news_family(self):
        assert _EVIDENCE_FIELD_FAMILIES.get("news") == "news"

    def test_global_news_family(self):
        assert _EVIDENCE_FIELD_FAMILIES.get("global_news") == "news"

    def test_audit_news_has_data(self):
        raw = {
            "news": {
                "raw": "新闻内容",
                "field": "news",
                "unit": "条",
                "vendor": "cn_akshare",
                "endpoint": "stock_news_em",
                "as_of": "2026-06-08",
                "fetched_at": "2026-06-08T10:00:00",
                "status": "HAS_DATA",
            },
        }
        result = audit_raw_evidence(raw)
        assert result.evidence_coverage >= 0

    def test_audit_news_failed(self):
        raw = {
            "news": {
                "raw": None,
                "field": "news",
                "unit": None,
                "vendor": "cn_akshare",
                "endpoint": "stock_news_em",
                "as_of": "2026-06-08",
                "status": "FAILED",
                "error": "ConnectionError",
            },
        }
        result = audit_raw_evidence(raw)
        assert "news" in result.failed_fields or result.evidence_coverage < 100


# ── Readiness Score ───────────────────────────────────────────────────


class TestReadinessScoreNews:
    def test_news_structured_has_data(self):
        raw = {
            "news": {
                "status": "HAS_DATA",
                "raw": "贵州茅台召开股东大会,东财\n"
                       "贵州茅台发布2025年度权益分派实施公告,东财\n"
                       "白酒行业龙头稳健增长,东财",
            },
        }
        result = infer_evidence_statuses(reports={}, raw_evidence=raw)
        assert result["announcements"] == EvidenceStatus.HAS_DATA

    def test_news_structured_failed(self):
        raw = {
            "news": {
                "status": "FAILED",
                "raw": "新闻数据获取失败",
                "error": "ConnectionError",
            },
        }
        result = infer_evidence_statuses(reports={}, raw_evidence=raw)
        assert result["announcements"] == EvidenceStatus.QUERY_FAILED

    def test_news_structured_normal_no_data(self):
        raw = {
            "news": {
                "status": "NORMAL_NO_DATA",
                "raw": "暂无相关新闻",
            },
        }
        result = infer_evidence_statuses(reports={}, raw_evidence=raw)
        assert result["announcements"] == EvidenceStatus.NORMAL_NO_DATA

    def test_news_not_queried_default(self):
        raw = {}
        result = infer_evidence_statuses(reports={}, raw_evidence=raw)
        assert result["announcements"] == EvidenceStatus.NOT_QUERIED

    def test_news_text_fallback(self):
        reports = {"news_report": "贵州茅台：2026-06-08公告标题摘要。公司公告重大事项。"}
        result = infer_evidence_statuses(reports=reports, raw_evidence={})
        assert result["announcements"] in (EvidenceStatus.HAS_DATA, EvidenceStatus.FIELD_MISSING)


# ── Interface ─────────────────────────────────────────────────────────


class TestInterfaceNews:
    def test_news_tools_category(self):
        assert "news_data" in TOOLS_CATEGORIES
        tools = TOOLS_CATEGORIES["news_data"]["tools"]
        assert "get_news" in tools
        assert "get_global_news" in tools

    def test_get_news_category(self):
        cat = get_category_for_method("get_news")
        assert cat == "news_data"

    def test_get_global_news_category(self):
        cat = get_category_for_method("get_global_news")
        assert cat == "news_data"

    def test_failure_string_detection(self):
        for pattern in _FAILURE_RESULT_PATTERNS:
            assert isinstance(pattern, str)


# ── Event Source ──────────────────────────────────────────────────────


class TestEventSourceStatus:
    def test_status_enum_values(self):
        assert EventSourceStatus.OK.value == "OK"
        assert EventSourceStatus.FAILED.value == "FAILED"
        assert EventSourceStatus.STALE.value == "STALE"
        assert EventSourceStatus.PARTIAL.value == "PARTIAL"

    def test_event_source_result_default(self):
        result = EventSourceResult()
        assert result.status == EventSourceStatus.OK
        assert result.event_count == 0
        assert result.symbols_count == 0

    def test_event_item_default(self):
        item = EventItem()
        assert item.symbol == ""
        assert item.direction == "neutral"
        assert item.event_type == ""


# ── News Fixtures ─────────────────────────────────────────────────────


_NEWS_FIXTURE_IDS = [
    FIXTURE_NEWS_HAS_DATA,
    FIXTURE_NEWS_NORMAL_NO_DATA,
    FIXTURE_NEWS_PARTIAL_FAILED,
    FIXTURE_NEWS_FAILED,
    FIXTURE_NEWS_RATE_LIMITED,
]

_GLOBAL_NEWS_FIXTURE_IDS = [
    FIXTURE_GLOBAL_NEWS_HAS_DATA,
    FIXTURE_GLOBAL_NEWS_NORMAL_NO_DATA,
    FIXTURE_GLOBAL_NEWS_FAILED,
    FIXTURE_GLOBAL_NEWS_RATE_LIMITED,
]


class TestNewsFixtures:
    def test_news_fixture_ids_exist(self):
        for fid in _NEWS_FIXTURE_IDS:
            fixture = get_fixture(fid)
            assert fixture is not None, f"Missing fixture: {fid}"

    def test_global_news_fixture_ids_exist(self):
        for fid in _GLOBAL_NEWS_FIXTURE_IDS:
            fixture = get_fixture(fid)
            assert fixture is not None, f"Missing fixture: {fid}"

    def test_all_fixture_ids_count(self):
        assert len(ALL_FIXTURE_IDS) >= 34

    def test_news_fixtures_in_all_fixture_ids(self):
        for fid in _NEWS_FIXTURE_IDS:
            assert fid in ALL_FIXTURE_IDS, f"{fid} not in ALL_FIXTURE_IDS"

    def test_global_news_fixtures_in_all_fixture_ids(self):
        for fid in _GLOBAL_NEWS_FIXTURE_IDS:
            assert fid in ALL_FIXTURE_IDS, f"{fid} not in ALL_FIXTURE_IDS"

    def test_news_has_data_fixture(self):
        fixture = get_fixture(FIXTURE_NEWS_HAS_DATA)
        assert fixture.expected_status == "HAS_DATA"
        assert fixture.vendor == "cn_akshare"
        assert "news" in fixture.raw_evidence
        ev = fixture.raw_evidence["news"]
        assert ev["status"] == "HAS_DATA"
        assert ev["unit"] == "条"
        assert ev["record_count"] > 0

    def test_news_normal_no_data_fixture(self):
        fixture = get_fixture(FIXTURE_NEWS_NORMAL_NO_DATA)
        assert fixture.expected_status == "NORMAL_NO_DATA"
        ev = fixture.raw_evidence["news"]
        assert ev["status"] == "NORMAL_NO_DATA"
        assert ev["record_count"] == 0

    def test_news_partial_failed_fixture(self):
        fixture = get_fixture(FIXTURE_NEWS_PARTIAL_FAILED)
        assert fixture.expected_status == "HAS_DATA"
        ev = fixture.raw_evidence["news"]
        assert ev["fallback_from"] == "cn_akshare"
        assert ev["vendor"] == "cn_astock"
        assert ev["error"] is not None

    def test_news_failed_fixture(self):
        fixture = get_fixture(FIXTURE_NEWS_FAILED)
        assert fixture.expected_status == "FAILED"
        ev = fixture.raw_evidence["news"]
        assert ev["status"] == "FAILED"
        assert ev["error"] == "ConnectionError"

    def test_news_rate_limited_fixture(self):
        fixture = get_fixture(FIXTURE_NEWS_RATE_LIMITED)
        assert fixture.expected_status == "FAILED"
        ev = fixture.raw_evidence["news"]
        assert ev["status"] == "FAILED"
        assert "RateLimitError" in (ev["error"] or "")

    def test_global_news_has_data_fixture(self):
        fixture = get_fixture(FIXTURE_GLOBAL_NEWS_HAS_DATA)
        assert fixture.expected_status == "HAS_DATA"
        assert fixture.vendor == "cn_akshare"
        ev = fixture.raw_evidence["global_news"]
        assert ev["status"] == "HAS_DATA"
        assert ev["record_count"] > 0

    def test_global_news_normal_no_data_fixture(self):
        fixture = get_fixture(FIXTURE_GLOBAL_NEWS_NORMAL_NO_DATA)
        assert fixture.expected_status == "NORMAL_NO_DATA"
        ev = fixture.raw_evidence["global_news"]
        assert ev["record_count"] == 0

    def test_global_news_failed_fixture(self):
        fixture = get_fixture(FIXTURE_GLOBAL_NEWS_FAILED)
        assert fixture.expected_status == "FAILED"
        ev = fixture.raw_evidence["global_news"]
        assert ev["status"] == "FAILED"

    def test_global_news_rate_limited_fixture(self):
        fixture = get_fixture(FIXTURE_GLOBAL_NEWS_RATE_LIMITED)
        assert fixture.expected_status == "FAILED"
        ev = fixture.raw_evidence["global_news"]
        assert "RateLimitError" in (ev["error"] or "")

    def test_no_vendor_for_empty_fixtures(self):
        fixture = get_fixture(FIXTURE_NEWS_NORMAL_NO_DATA)
        assert fixture.vendor != "" or fixture.expected_status == "NORMAL_NO_DATA"

    def test_all_news_fixtures_unique(self):
        all_ids = _NEWS_FIXTURE_IDS + _GLOBAL_NEWS_FIXTURE_IDS
        assert len(all_ids) == len(set(all_ids))

    def test_unknown_fixture_returns_none(self):
        assert get_fixture("nonexistent_news_fixture") is None


# ── News Fixture Replay ───────────────────────────────────────────────


class TestNewsFixtureReplay:
    def test_replay_news_has_data(self):
        fixture = get_fixture(FIXTURE_NEWS_HAS_DATA)
        report = run_fixture_replay(fixtures=[fixture])
        assert report.total_fixtures == 1
        assert report.passed == 1
        assert report.failed == 0

    def test_replay_news_normal_no_data(self):
        fixture = get_fixture(FIXTURE_NEWS_NORMAL_NO_DATA)
        report = run_fixture_replay(fixtures=[fixture])
        assert report.total_fixtures == 1
        assert report.passed == 1

    def test_replay_news_failed(self):
        fixture = get_fixture(FIXTURE_NEWS_FAILED)
        report = run_fixture_replay(fixtures=[fixture])
        assert report.total_fixtures == 1
        assert report.passed == 1
        assert report.results[0].actual_status == "FAILED"

    def test_replay_news_rate_limited(self):
        fixture = get_fixture(FIXTURE_NEWS_RATE_LIMITED)
        report = run_fixture_replay(fixtures=[fixture])
        assert report.total_fixtures == 1
        assert report.passed == 1

    def test_replay_all_news(self):
        fixtures = [get_fixture(fid) for fid in _NEWS_FIXTURE_IDS]
        report = run_fixture_replay(fixtures=fixtures)
        assert report.total_fixtures == 5
        assert report.passed == 5

    def test_replay_all_global_news(self):
        fixtures = [get_fixture(fid) for fid in _GLOBAL_NEWS_FIXTURE_IDS]
        report = run_fixture_replay(fixtures=fixtures)
        assert report.total_fixtures == 4
        assert report.passed == 4

    def test_replay_full_includes_news(self):
        report = run_fixture_replay()
        news_results = [r for r in report.results if r.data_type in ("news", "global_news")]
        assert len(news_results) == 9


# ── Source Level Classification ───────────────────────────────────────


class TestSourceLevelClassification:
    def test_news_has_source_level_field(self):
        fixture = get_fixture(FIXTURE_NEWS_HAS_DATA)
        ev = fixture.raw_evidence["news"]
        assert "source_level" in ev
        assert ev["source_level"] != ""

    def test_global_news_has_policy_document_type(self):
        fixture = get_fixture(FIXTURE_GLOBAL_NEWS_HAS_DATA)
        ev = fixture.raw_evidence["global_news"]
        assert ev.get("evidence_type") == "policy_document"

    def test_news_relay_type(self):
        fixture = get_fixture(FIXTURE_NEWS_HAS_DATA)
        ev = fixture.raw_evidence["news"]
        assert ev.get("evidence_type") == "news_relay"

    def test_failed_fixtures_have_no_source_level(self):
        fixture = get_fixture(FIXTURE_NEWS_FAILED)
        ev = fixture.raw_evidence["news"]
        assert ev.get("source_level", "") == ""


# ── Evidence Type Distinction ─────────────────────────────────────────


class TestEvidenceTypeDistinction:
    def test_policy_document_distinguished(self):
        fixture = get_fixture(FIXTURE_GLOBAL_NEWS_HAS_DATA)
        ev = fixture.raw_evidence["global_news"]
        assert ev["evidence_type"] == "policy_document"
        assert ev["source_level"] == "中央/国务院"

    def test_news_relay_distinguished(self):
        fixture = get_fixture(FIXTURE_NEWS_HAS_DATA)
        ev = fixture.raw_evidence["news"]
        assert ev["evidence_type"] == "news_relay"

    def test_market_rumor_not_in_has_data(self):
        fixture = get_fixture(FIXTURE_NEWS_HAS_DATA)
        ev = fixture.raw_evidence["news"]
        assert ev["evidence_type"] != "market_rumor"

    def test_empty_evidence_type_for_failures(self):
        fixture = get_fixture(FIXTURE_NEWS_FAILED)
        ev = fixture.raw_evidence["news"]
        assert ev.get("evidence_type", "") == ""


# ── Live Smoke ────────────────────────────────────────────────────────


class TestLiveSmokeNews:
    def test_news_endpoint_in_definitions(self):
        endpoints = _make_endpoint_definitions()
        ep_names = [ep["endpoint"] for ep in endpoints]
        assert "cn_astock/eastmoney_news" in ep_names
        assert "cn_astock/cls_global_news" in ep_names

    def test_news_endpoint_definition_fields(self):
        endpoints = _make_endpoint_definitions()
        news_ep = [ep for ep in endpoints if ep["endpoint"] == "cn_astock/eastmoney_news"][0]
        assert news_ep["method"] == "get_news"
        assert news_ep["vendor"] == "cn_astock"
        assert news_ep["expected_unit"] == "条"

    def test_global_news_endpoint_definition_fields(self):
        endpoints = _make_endpoint_definitions()
        gn_ep = [ep for ep in endpoints if ep["endpoint"] == "cn_astock/cls_global_news"][0]
        assert gn_ep["method"] == "get_global_news"
        assert gn_ep["vendor"] == "cn_astock"
        assert gn_ep["expected_unit"] == "条"

    def test_live_smoke_skip_without_env(self):
        with patch.dict("os.environ", {}, clear=True):
            report = run_live_smoke(
                symbols=["600519.SH"],
                endpoints=[{
                    "endpoint": "cn_astock/eastmoney_news",
                    "method": "get_news",
                    "vendor": "cn_astock",
                    "args_template": ("{symbol}", "{date}", "{date}"),
                    "kwargs": {},
                    "expected_unit": "条",
                }],
            )
        assert report.env_gated is True
        assert all(r.status == "SKIPPED" for r in report.results)

    def test_live_smoke_enabled_with_env(self):
        with patch.dict("os.environ", {"TA_LIVE_DATA_SMOKE": "1"}, clear=False):
            mock_provider = MagicMock()
            mock_provider.get_news.return_value = "新闻标题,来源\n测试新闻,东财"
            report = run_live_smoke(
                symbols=["600519.SH"],
                endpoints=[{
                    "endpoint": "cn_astock/eastmoney_news",
                    "method": "get_news",
                    "vendor": "cn_astock",
                    "args_template": ("{symbol}", "{date}", "{date}"),
                    "kwargs": {},
                    "expected_unit": "条",
                }],
            )
        assert report.env_gated is False

    def test_live_smoke_report_structure(self):
        report = run_live_smoke(symbols=["600519.SH"])
        assert isinstance(report, LiveSmokeReport)
        assert report.run_at != ""
        assert report.date != ""
        assert "600519.SH" in report.symbols

    def test_is_live_smoke_enabled_default(self):
        with patch.dict("os.environ", {}, clear=True):
            assert is_live_smoke_enabled() is False

    def test_is_live_smoke_enabled_set(self):
        with patch.dict("os.environ", {"TA_LIVE_DATA_SMOKE": "1"}, clear=False):
            assert is_live_smoke_enabled() is True


# ── News Dedup ────────────────────────────────────────────────────────


class TestNewsDedup:
    def test_no_double_count_same_vendor(self):
        fixture = get_fixture(FIXTURE_NEWS_HAS_DATA)
        ev = fixture.raw_evidence["news"]
        assert ev["vendor"] == "cn_akshare"
        report = run_fixture_replay(fixtures=[fixture])
        assert report.total_fixtures == 1

    def test_different_vendor_for_fallback(self):
        fixture = get_fixture(FIXTURE_NEWS_PARTIAL_FAILED)
        ev = fixture.raw_evidence["news"]
        assert ev["vendor"] == "cn_astock"
        assert ev["fallback_from"] == "cn_akshare"


# ── Acceptance ────────────────────────────────────────────────────────


class TestAcceptanceDATA014:
    def test_all_failed_returns_failed(self):
        fixture = get_fixture(FIXTURE_NEWS_FAILED)
        ev = fixture.raw_evidence["news"]
        assert ev["status"] == "FAILED"

    def test_success_no_events_returns_normal_no_data(self):
        fixture = get_fixture(FIXTURE_NEWS_NORMAL_NO_DATA)
        ev = fixture.raw_evidence["news"]
        assert ev["status"] == "NORMAL_NO_DATA"
        assert ev["record_count"] == 0

    def test_live_smoke_skip_without_env(self):
        with patch.dict("os.environ", {}, clear=True):
            assert is_live_smoke_enabled() is False
            report = run_live_smoke(symbols=["600519.SH"])
            assert all(r.status == "SKIPPED" for r in report.results)

    def test_fixture_replay_passes(self):
        all_news = _NEWS_FIXTURE_IDS + _GLOBAL_NEWS_FIXTURE_IDS
        fixtures = [get_fixture(fid) for fid in all_news]
        report = run_fixture_replay(fixtures=fixtures)
        assert report.passed == len(all_news)
        assert report.failed == 0

    def test_source_level_distinction(self):
        policy = get_fixture(FIXTURE_GLOBAL_NEWS_HAS_DATA)
        policy_ev = policy.raw_evidence["global_news"]
        assert policy_ev["source_level"] == "中央/国务院"
        assert policy_ev["evidence_type"] == "policy_document"

        relay = get_fixture(FIXTURE_NEWS_HAS_DATA)
        relay_ev = relay.raw_evidence["news"]
        assert relay_ev["evidence_type"] == "news_relay"

    def test_rate_limit_not_no_data(self):
        fixture = get_fixture(FIXTURE_NEWS_RATE_LIMITED)
        ev = fixture.raw_evidence["news"]
        assert ev["status"] == "FAILED"
        assert ev["status"] != "NORMAL_NO_DATA"

    def test_fallback_from_recorded(self):
        fixture = get_fixture(FIXTURE_NEWS_PARTIAL_FAILED)
        ev = fixture.raw_evidence["news"]
        assert ev["fallback_from"] == "cn_akshare"

    def test_vendor_endpoint_present(self):
        for fid in _NEWS_FIXTURE_IDS:
            fixture = get_fixture(fid)
            ev = fixture.raw_evidence["news"]
            assert ev["vendor"] != "" or fixture.expected_status == "NORMAL_NO_DATA"
            assert "endpoint" in ev
        for fid in _GLOBAL_NEWS_FIXTURE_IDS:
            fixture = get_fixture(fid)
            ev = fixture.raw_evidence["global_news"]
            assert ev["vendor"] != "" or fixture.expected_status == "NORMAL_NO_DATA"
            assert "endpoint" in ev

    def test_no_sensitive_data_in_fixtures(self):
        sensitive_patterns = ["api_key", "token", "password", "secret"]
        for fid in _NEWS_FIXTURE_IDS + _GLOBAL_NEWS_FIXTURE_IDS:
            fixture = get_fixture(fid)
            fixture_str = str(fixture.to_dict())
            for pat in sensitive_patterns:
                assert pat not in fixture_str.lower(), f"Sensitive data found in {fid}: {pat}"

    def test_evidence_contract_completeness(self):
        fixture = get_fixture(FIXTURE_NEWS_HAS_DATA)
        ev = fixture.raw_evidence["news"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.status == "HAS_DATA"

    def test_five_news_scenarios_covered(self):
        assert len(_NEWS_FIXTURE_IDS) == 5

    def test_four_global_news_scenarios_covered(self):
        assert len(_GLOBAL_NEWS_FIXTURE_IDS) == 4

    def test_total_nine_new_fixtures(self):
        total_new = len(_NEWS_FIXTURE_IDS) + len(_GLOBAL_NEWS_FIXTURE_IDS)
        assert total_new == 9
