# [DATA-011] research_report_raw_evidence
"""
Tests for research report provider route and raw_evidence integration.

Covers:
  - source_catalog: DataType.REPORT sources registered
  - evidence_contract: research_report key mapping and completeness
  - evidence_coverage_audit: label and family registration
  - readiness_score: research_report status inference and coverage
  - interface: get_research_report in TOOLS_CATEGORIES
  - fixtures: report fixture IDs and replay
  - providers: akshare/astock get_research_report methods
  - acceptance: end-to-end acceptance criteria
"""

import pytest


# ── Source Catalog ──────────────────────────────────────────────────

class TestSourceCatalogResearchReport:

    def test_report_data_type_exists(self):
        from tradingagents.dataflows.source_catalog import DataType
        assert DataType.REPORT.value == "report"

    def test_report_sources_registered(self):
        from tradingagents.dataflows.source_catalog import get_sources_for_type, DataType
        sources = get_sources_for_type(DataType.REPORT)
        assert len(sources) >= 2

    def test_report_primary_source(self):
        from tradingagents.dataflows.source_catalog import get_primary_source, DataType
        primary = get_primary_source(DataType.REPORT)
        assert primary is not None
        assert primary.vendor == "cn_akshare"

    def test_report_fallback_chain(self):
        from tradingagents.dataflows.source_catalog import get_fallback_chain, DataType
        chain = get_fallback_chain(DataType.REPORT)
        assert "cn_akshare" in chain
        assert "cn_astock" in chain

    def test_report_akshare_fields(self):
        from tradingagents.dataflows.source_catalog import get_sources_for_type, DataType
        sources = get_sources_for_type(DataType.REPORT)
        akshare_src = [s for s in sources if s.vendor == "cn_akshare"]
        assert len(akshare_src) >= 1
        assert len(akshare_src[0].fields) > 0

    def test_report_astock_fields(self):
        from tradingagents.dataflows.source_catalog import get_sources_for_type, DataType
        sources = get_sources_for_type(DataType.REPORT)
        astock_src = [s for s in sources if s.vendor == "cn_astock"]
        assert len(astock_src) >= 1
        assert "publishDate" in astock_src[0].fields

    def test_report_all_data_types_includes_report(self):
        from tradingagents.dataflows.source_catalog import get_all_data_types
        assert "report" in get_all_data_types()

    def test_catalog_validation_report(self):
        from tradingagents.dataflows.source_catalog import validate_catalog
        issues = validate_catalog()
        report_issues = [i for i in issues if "report" in i.lower()]
        assert len(report_issues) == 0, f"Report catalog issues: {report_issues}"


# ── Evidence Contract ──────────────────────────────────────────────

class TestEvidenceContractResearchReport:

    def test_resolve_data_type(self):
        from tradingagents.dataflows.evidence_contract import resolve_data_type
        assert resolve_data_type("research_report") == "report"

    def test_research_report_has_data_contract(self):
        from tradingagents.dataflows.evidence_contract import EvidenceContract
        contract = EvidenceContract(
            field="research_report",
            value="REPORT_HAS_DATA: ...",
            vendor="cn_astock",
            endpoint="reportapi.eastmoney.com/report/list",
            status="HAS_DATA",
            record_count=5,
        )
        assert contract.has_data is True
        assert contract.is_failed is False

    def test_research_report_failed_contract(self):
        from tradingagents.dataflows.evidence_contract import EvidenceContract
        contract = EvidenceContract(
            field="research_report",
            value=None,
            vendor="cn_astock",
            status="FAILED",
            error="ConnectionError",
        )
        assert contract.has_data is False
        assert contract.is_failed is True

    def test_completeness_includes_research_report(self):
        from tradingagents.dataflows.evidence_contract import compute_contract_completeness
        raw_evidence = {
            "research_report": {
                "raw": "REPORT_HAS_DATA",
                "field": "research_report",
                "status": "HAS_DATA",
                "vendor": "cn_astock",
                "unit": "条",
            },
        }
        result = compute_contract_completeness(raw_evidence)
        assert result["total_checks"] >= 2

    def test_completeness_missing_research_report(self):
        from tradingagents.dataflows.evidence_contract import compute_contract_completeness, _REQUIRED_FIELDS_FOR_COMPLETENESS
        assert "research_report" in _REQUIRED_FIELDS_FOR_COMPLETENESS

    def test_completeness_failed_research_report(self):
        from tradingagents.dataflows.evidence_contract import compute_contract_completeness
        raw_evidence = {
            "research_report": {
                "raw": None,
                "field": "research_report",
                "status": "FAILED",
                "vendor": "",
                "unit": None,
                "error": "ConnectionError",
            },
        }
        result = compute_contract_completeness(raw_evidence)
        assert result["completeness_score"] < 100

    def test_data_source_summary_research_report(self):
        from tradingagents.dataflows.evidence_contract import build_data_source_summary
        raw_evidence = {
            "research_report": {
                "raw": "REPORT_HAS_DATA",
                "field": "research_report",
                "status": "HAS_DATA",
                "vendor": "cn_astock",
                "endpoint": "reportapi.eastmoney.com/report/list",
                "unit": "条",
                "unit_verified": None,
                "is_fallback": False,
                "fallback_from": None,
                "is_realtime_patched": False,
                "as_of": "2026-06-08",
                "record_count": 5,
                "error": None,
            },
        }
        summary = build_data_source_summary(raw_evidence)
        report_entries = [s for s in summary if s["field"] == "research_report"]
        assert len(report_entries) == 1
        assert report_entries[0]["vendor"] == "cn_astock"
        assert report_entries[0]["status"] == "HAS_DATA"


# ── Evidence Coverage Audit ────────────────────────────────────────

class TestEvidenceCoverageAuditResearchReport:

    def test_label_exists(self):
        from tradingagents.dataflows.evidence_coverage_audit import _EVIDENCE_FIELD_LABELS
        assert "research_report" in _EVIDENCE_FIELD_LABELS
        assert _EVIDENCE_FIELD_LABELS["research_report"] == "研报/评级"

    def test_family_exists(self):
        from tradingagents.dataflows.evidence_coverage_audit import _EVIDENCE_FIELD_FAMILIES
        assert "research_report" in _EVIDENCE_FIELD_FAMILIES
        assert _EVIDENCE_FIELD_FAMILIES["research_report"] == "report"

    def test_audit_research_report_has_data(self):
        from tradingagents.dataflows.evidence_coverage_audit import audit_raw_evidence
        raw_evidence = {
            "research_report": {
                "raw": "REPORT_HAS_DATA",
                "field": "research_report",
                "status": "HAS_DATA",
                "vendor": "cn_astock",
                "endpoint": "reportapi.eastmoney.com/report/list",
                "unit": "条",
                "unit_verified": None,
                "is_realtime_patched": False,
                "fallback_from": None,
                "source_url": None,
                "error": None,
                "record_count": 5,
                "as_of": "2026-06-08",
                "fetched_at": "2026-06-08T12:00:00",
            },
        }
        result = audit_raw_evidence(raw_evidence)
        assert "research_report" in result.has_data_fields

    def test_audit_research_report_failed(self):
        from tradingagents.dataflows.evidence_coverage_audit import audit_raw_evidence
        raw_evidence = {
            "research_report": {
                "raw": None,
                "field": "research_report",
                "status": "FAILED",
                "vendor": "cn_astock",
                "endpoint": "reportapi.eastmoney.com/report/list",
                "unit": None,
                "unit_verified": None,
                "is_realtime_patched": False,
                "fallback_from": None,
                "source_url": None,
                "error": "ConnectionError",
                "record_count": 0,
                "as_of": "2026-06-08",
                "fetched_at": "2026-06-08T12:00:00",
            },
        }
        result = audit_raw_evidence(raw_evidence)
        assert "research_report" in result.failed_fields


# ── Readiness Score ────────────────────────────────────────────────

class TestReadinessScoreResearchReport:

    def test_research_report_default_not_queried(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        statuses = infer_evidence_statuses({})
        assert statuses["research_report"] == "not_queried"

    def test_research_report_has_data(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        raw = {"research_report": {"raw": "600519.SH [DATA-011] REPORT_HAS_DATA: ...", "status": "HAS_DATA"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.HAS_DATA

    def test_research_report_failed(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        raw = {"research_report": {"raw": "600519.SH [DATA-011] REPORT_FAILED: ...", "status": "FAILED"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.QUERY_FAILED

    def test_research_report_normal_no_data(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        raw = {"research_report": {"raw": "600519.SH [DATA-011] REPORT_NORMAL_NO_DATA: ...", "status": "NORMAL_NO_DATA"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.NORMAL_NO_DATA

    def test_research_report_from_raw_long_text(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        long_text = "A" * 100
        raw = {"research_report": {"raw": long_text, "status": "HAS_DATA"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.HAS_DATA

    def test_research_report_coverage_increases_with_data(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage
        base = calculate_evidence_coverage(
            ohlcv_5d="has_data", volume="has_data",
            turnover_rate="has_data", volume_ratio="has_data",
            individual_fund_flow="has_data", lhb_status="normal_no_data",
            margin_trading="has_data", announcements="has_data",
            research_report="not_queried",
        )
        with_report = calculate_evidence_coverage(
            ohlcv_5d="has_data", volume="has_data",
            turnover_rate="has_data", volume_ratio="has_data",
            individual_fund_flow="has_data", lhb_status="normal_no_data",
            margin_trading="has_data", announcements="has_data",
            research_report="has_data",
        )
        assert with_report > base

    def test_research_report_not_available_excluded(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage
        cov = calculate_evidence_coverage(research_report="not_available")
        assert cov == 0

    def test_research_report_failed_text(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        raw = {"research_report": {"raw": "600519.SH [DATA-011] REPORT_FAILED: 研报数据获取失败：ConnectionError", "status": "FAILED"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.QUERY_FAILED

    def test_research_report_normal_no_data_empty(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        raw = {"research_report": {"raw": "", "status": "NORMAL_NO_DATA"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.NORMAL_NO_DATA

    def test_research_report_coverage_denominator_increases(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage
        all_has = calculate_evidence_coverage(
            ohlcv_5d="has_data", volume="has_data",
            turnover_rate="has_data", volume_ratio="has_data",
            individual_fund_flow="has_data", lhb_status="has_data",
            margin_trading="has_data", announcements="has_data",
            research_report="has_data", ratings="has_data",
            buybacks="has_data",
        )
        assert all_has == 100


# ── Interface ──────────────────────────────────────────────────────

class TestInterfaceResearchReport:

    def test_get_research_report_in_tools_categories(self):
        from tradingagents.dataflows.interface import TOOLS_CATEGORIES
        assert "get_research_report" in TOOLS_CATEGORIES["cn_market_data"]["tools"]

    def test_get_category_for_method(self):
        from tradingagents.dataflows.interface import get_category_for_method
        assert get_category_for_method("get_research_report") == "cn_market_data"

    def test_provider_has_method(self):
        from tradingagents.dataflows.interface import _registry
        found = False
        for name in _registry.list_names():
            provider = _registry.get(name)
            if hasattr(provider, "get_research_report"):
                found = True
                break
        assert found, "No provider implements get_research_report"

    def test_failure_string_detection(self):
        from tradingagents.dataflows.interface import _is_failure_result
        assert _is_failure_result("600519.SH [DATA-011] REPORT_FAILED: 研报数据获取失败：ConnectionError")
        assert not _is_failure_result("600519.SH [DATA-011] REPORT_HAS_DATA: ...")


# ── Fixtures ───────────────────────────────────────────────────────

class TestResearchReportFixtures:

    def test_fixture_ids_in_all_fixtures(self):
        from tradingagents.dataflows.fixture_replay import ALL_FIXTURE_IDS
        assert "report_has_data" in ALL_FIXTURE_IDS
        assert "report_failed" in ALL_FIXTURE_IDS
        assert "report_not_queried" in ALL_FIXTURE_IDS

    def test_total_fixture_count(self):
        from tradingagents.dataflows.fixture_replay import ALL_FIXTURE_IDS
        assert len(ALL_FIXTURE_IDS) >= 19

    def test_report_has_data_fixture(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture("report_has_data")
        assert fixture is not None
        assert fixture.expected_status == "HAS_DATA"
        assert fixture.vendor == "cn_astock"
        ev = fixture.raw_evidence["research_report"]
        assert ev["status"] == "HAS_DATA"
        assert ev["vendor"] == "cn_astock"

    def test_report_failed_fixture(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture("report_failed")
        assert fixture is not None
        assert fixture.expected_status == "FAILED"
        ev = fixture.raw_evidence["research_report"]
        assert ev["status"] == "FAILED"
        assert ev["error"] == "ConnectionError"

    def test_report_not_queried_fixture(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture("report_not_queried")
        assert fixture is not None
        assert fixture.expected_status == "NOT_QUERIED"
        ev = fixture.raw_evidence["research_report"]
        assert ev["status"] == "NOT_QUERIED"
        assert ev["vendor"] == ""

    def test_report_has_data_unit(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture("report_has_data")
        ev = fixture.raw_evidence["research_report"]
        assert ev["unit"] == "条"

    def test_report_failed_no_vendor(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture("report_failed")
        ev = fixture.raw_evidence["research_report"]
        assert ev["record_count"] == 0

    def test_all_report_fixtures_exist(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        for fid in ("report_has_data", "report_failed", "report_not_queried"):
            assert get_fixture(fid) is not None, f"Fixture {fid} not found"


# ── Fixture Replay ─────────────────────────────────────────────────

class TestResearchReportFixtureReplay:

    def test_replay_report_has_data(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay(["report_has_data"])
        assert report.total_fixtures == 1
        assert report.results[0].passed

    def test_replay_report_failed(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay(["report_failed"])
        assert report.total_fixtures == 1
        assert report.results[0].passed

    def test_replay_report_not_queried(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay(["report_not_queried"])
        assert report.total_fixtures == 1
        assert report.results[0].passed

    def test_full_replay_includes_report(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay, ALL_FIXTURE_IDS
        report = run_fixture_replay()
        report_ids = [r.fixture_id for r in report.results if "report" in r.fixture_id and "margin" not in r.fixture_id]
        assert len(report_ids) >= 3

    def test_replay_all_report_fixtures_pass(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay(["report_has_data", "report_failed", "report_not_queried"])
        assert report.all_passed


# ── Provider Methods ───────────────────────────────────────────────

class TestProviderResearchReport:

    def test_akshare_provider_has_method(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        assert hasattr(CnAkshareProvider, "get_research_report")

    def test_astock_provider_has_method(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        assert hasattr(CnAstockProvider, "get_research_report")

    def test_akshare_failure_returns_report_failed(self):
        from unittest.mock import patch, MagicMock
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        with patch.object(provider, "_ak", side_effect=Exception("test error")):
            result = provider.get_research_report("600519.SH")
            assert "REPORT_FAILED" in result

    def test_akshare_uses_real_research_report_schema(self):
        import pandas as pd
        from unittest.mock import patch
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

        provider = CnAkshareProvider()
        df = pd.DataFrame(
            {
                "日期": ["2026-06-29"],
                "机构": ["华鑫证券"],
                "东财评级": ["买入"],
                "报告名称": ["公司动态研究报告"],
                "报告PDF链接": ["https://example.test/report.pdf"],
            }
        )
        with patch.object(provider, "_ak") as mock_ak:
            mock_ak.return_value.stock_research_report_em.return_value = df
            result = provider.get_research_report("002409.SZ")

        mock_ak.return_value.stock_research_report_em.assert_called_once_with(
            symbol="002409"
        )
        assert "REPORT_HAS_DATA" in result
        assert "华鑫证券" in result
        assert "公司动态研究报告" in result
        assert "https://example.test/report.pdf" in result

    def test_astock_failure_returns_report_failed(self):
        from unittest.mock import patch, MagicMock
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.side_effect = Exception("ConnectionError")
            result = provider.get_research_report("600519.SH")
            assert "REPORT_FAILED" in result

    def test_astock_no_data_returns_normal_no_data(self):
        from unittest.mock import patch, MagicMock
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": [], "TotalPage": 0}
            mock_session.get.return_value = mock_response
            result = provider.get_research_report("600519.SH")
            assert "REPORT_NORMAL_NO_DATA" in result

    def test_astock_has_data(self):
        from unittest.mock import patch, MagicMock
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": [
                    {
                        "publishDate": "2026-06-05",
                        "orgSName": "中信证券",
                        "title": "贵州茅台深度研究",
                        "emRatingName": "买入",
                        "predictThisYearEps": "58.5",
                        "infoCode": "ABC123",
                    }
                ],
                "TotalPage": 1,
            }
            mock_session.get.return_value = mock_response
            result = provider.get_research_report("600519.SH")
            assert "REPORT_HAS_DATA" in result
            assert "中信证券" in result


# ── Acceptance ─────────────────────────────────────────────────────

class TestAcceptanceDATA011:

    def test_acceptance_source_catalog(self):
        from tradingagents.dataflows.source_catalog import get_sources_for_type, DataType
        sources = get_sources_for_type(DataType.REPORT)
        assert len(sources) >= 2
        vendors = [s.vendor for s in sources]
        assert "cn_akshare" in vendors
        assert "cn_astock" in vendors

    def test_acceptance_evidence_contract(self):
        from tradingagents.dataflows.evidence_contract import resolve_data_type, _REQUIRED_FIELDS_FOR_COMPLETENESS
        assert resolve_data_type("research_report") == "report"
        assert "research_report" in _REQUIRED_FIELDS_FOR_COMPLETENESS

    def test_acceptance_has_data_from_raw_evidence(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        raw = {"research_report": {"raw": "REPORT_HAS_DATA: 研报数据", "status": "HAS_DATA"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.HAS_DATA

    def test_acceptance_failed_from_raw_evidence(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        raw = {"research_report": {"raw": "REPORT_FAILED: ConnectionError", "status": "FAILED"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.QUERY_FAILED

    def test_acceptance_normal_no_data_from_raw_evidence(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses, EvidenceStatus
        raw = {"research_report": {"raw": "REPORT_NORMAL_NO_DATA: 无研报", "status": "NORMAL_NO_DATA"}}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["research_report"] == EvidenceStatus.NORMAL_NO_DATA

    def test_acceptance_not_queried_default(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        statuses = infer_evidence_statuses({})
        assert statuses["research_report"] == "not_queried"

    def test_acceptance_coverage_includes_research_report(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage
        cov_without = calculate_evidence_coverage(
            ohlcv_5d="has_data", volume="has_data",
            turnover_rate="has_data", volume_ratio="has_data",
            individual_fund_flow="has_data", lhb_status="has_data",
            margin_trading="has_data", announcements="has_data",
            research_report="not_queried",
        )
        cov_with = calculate_evidence_coverage(
            ohlcv_5d="has_data", volume="has_data",
            turnover_rate="has_data", volume_ratio="has_data",
            individual_fund_flow="has_data", lhb_status="has_data",
            margin_trading="has_data", announcements="has_data",
            research_report="has_data",
        )
        assert cov_with > cov_without

    def test_acceptance_fixture_replay(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay(["report_has_data", "report_failed", "report_not_queried"])
        assert report.all_passed

    def test_acceptance_interface_route_registered(self):
        from tradingagents.dataflows.interface import get_category_for_method
        cat = get_category_for_method("get_research_report")
        assert cat == "cn_market_data"

    def test_acceptance_audit_label(self):
        from tradingagents.dataflows.evidence_coverage_audit import _EVIDENCE_FIELD_LABELS
        label = _EVIDENCE_FIELD_LABELS.get("research_report", "")
        assert "研报" in label

    def test_acceptance_data_collector_key(self):
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        assert hasattr(dc, "build_raw_evidence")
