# [DATA-012] rating_raw_evidence
# [DATA-012A] rating_data_collector_wiring
"""
Tests for analyst ratings data source registration, provider routing,
raw_evidence contract, evidence coverage audit, readiness score, fixtures,
and data collector wiring.
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


FIXTURE_RATINGS_HAS_DATA = "ratings_has_data"
FIXTURE_RATINGS_FAILED = "ratings_failed"
FIXTURE_RATINGS_NOT_QUERIED = "ratings_not_queried"


# ── Source Catalog ──────────────────────────────────────────────────────


class TestSourceCatalogRatings:
    def test_data_type_rating_exists(self):
        from tradingagents.dataflows.source_catalog import DataType
        assert DataType.RATING.value == "rating"

    def test_rating_sources_registered(self):
        from tradingagents.dataflows.source_catalog import get_sources_for_type
        sources = get_sources_for_type("rating")
        assert len(sources) >= 2

    def test_rating_primary_source(self):
        from tradingagents.dataflows.source_catalog import get_primary_source
        primary = get_primary_source("rating")
        assert primary is not None
        assert primary.vendor == "cn_akshare"
        assert primary.is_primary is True

    def test_rating_fallback_chain(self):
        from tradingagents.dataflows.source_catalog import get_sources_for_type
        sources = get_sources_for_type("rating")
        vendors = [s.vendor for s in sources]
        assert "cn_akshare" in vendors
        assert "cn_astock" in vendors

    def test_rating_primary_has_fields(self):
        from tradingagents.dataflows.source_catalog import get_primary_source
        primary = get_primary_source("rating")
        assert primary.fields is not None
        assert len(primary.fields) > 0

    def test_rating_astock_source(self):
        from tradingagents.dataflows.source_catalog import get_sources_for_type
        sources = get_sources_for_type("rating")
        astock = [s for s in sources if s.vendor == "cn_astock"]
        assert len(astock) >= 1
        assert astock[0].endpoint != ""

    def test_rating_all_data_types_includes_rating(self):
        from tradingagents.dataflows.source_catalog import DataType
        values = [d.value for d in DataType]
        assert "rating" in values

    def test_rating_source_catalog_validation(self):
        from tradingagents.dataflows.source_catalog import get_sources_for_type
        sources = get_sources_for_type("rating")
        for s in sources:
            assert s.vendor != ""
            assert s.data_type == "rating"
            assert s.fallback_priority > 0


# ── Evidence Contract ───────────────────────────────────────────────────


class TestEvidenceContractRatings:
    def test_resolve_data_type_ratings(self):
        from tradingagents.dataflows.evidence_contract import resolve_data_type
        assert resolve_data_type("ratings") == "rating"

    def test_ratings_has_data_contract(self):
        from tradingagents.dataflows.evidence_contract import EvidenceContract
        contract = EvidenceContract(
            field="ratings",
            value="600519.SH [DATA-012] RATINGS_HAS_DATA: ...",
            vendor="cn_astock",
            endpoint="reportapi.eastmoney.com/report/list",
            status="HAS_DATA",
        )
        assert contract.status == "HAS_DATA"
        assert contract.vendor == "cn_astock"

    def test_ratings_failed_contract(self):
        from tradingagents.dataflows.evidence_contract import EvidenceContract
        contract = EvidenceContract(
            field="ratings",
            value="600519.SH [DATA-012] RATINGS_FAILED: ConnectionError",
            vendor="cn_akshare",
            status="FAILED",
            error="ConnectionError",
        )
        assert contract.status == "FAILED"

    def test_ratings_completeness_check(self):
        from tradingagents.dataflows.evidence_contract import (
            EvidenceContract,
            compute_contract_completeness,
        )
        raw_ev = {
            "ratings": EvidenceContract(
                field="ratings",
                value="data",
                vendor="cn_astock",
                endpoint="reportapi.eastmoney.com/report/list",
                status="HAS_DATA",
            ).to_dict(),
        }
        result = compute_contract_completeness(raw_ev)
        assert result["completeness_score"] > 0

    def test_ratings_missing_vendor(self):
        from tradingagents.dataflows.evidence_contract import (
            EvidenceContract,
            compute_contract_completeness,
        )
        raw_ev = {
            "ratings": EvidenceContract(
                field="ratings",
                value="data",
                vendor="",
                status="HAS_DATA",
            ).to_dict(),
        }
        result = compute_contract_completeness(raw_ev)
        assert "vendor" in result["missing_details"].get("ratings", [])

    def test_ratings_failed_status_completeness(self):
        from tradingagents.dataflows.evidence_contract import (
            EvidenceContract,
            compute_contract_completeness,
        )
        raw_ev = {
            "ratings": EvidenceContract(
                field="ratings",
                value="failed",
                vendor="cn_astock",
                status="FAILED",
            ).to_dict(),
        }
        result = compute_contract_completeness(raw_ev)
        assert result["missing_details"].get("ratings") is not None

    def test_ratings_data_source_summary(self):
        from tradingagents.dataflows.evidence_contract import EvidenceContract, build_data_source_summary
        raw_ev = {
            "ratings": EvidenceContract(
                field="ratings",
                value="data",
                vendor="cn_astock",
                status="HAS_DATA",
                unit="条",
            ).to_dict(),
        }
        summary = build_data_source_summary(raw_ev)
        fields = [s["field"] for s in summary]
        assert "ratings" in fields


# ── Evidence Coverage Audit ─────────────────────────────────────────────


class TestEvidenceCoverageAuditRatings:
    def test_ratings_label(self):
        from tradingagents.dataflows.evidence_coverage_audit import _EVIDENCE_FIELD_LABELS
        assert "ratings" in _EVIDENCE_FIELD_LABELS
        assert _EVIDENCE_FIELD_LABELS["ratings"] == "分析师评级"

    def test_ratings_family(self):
        from tradingagents.dataflows.evidence_coverage_audit import _EVIDENCE_FIELD_FAMILIES
        assert "ratings" in _EVIDENCE_FIELD_FAMILIES
        assert _EVIDENCE_FIELD_FAMILIES["ratings"] == "rating"

    def test_audit_ratings_has_data(self):
        from tradingagents.dataflows.evidence_coverage_audit import audit_raw_evidence
        raw = {
            "ratings": {
                "raw": "600519.SH [DATA-012] RATINGS_HAS_DATA: ...",
                "vendor": "cn_astock",
                "status": "HAS_DATA",
            },
        }
        result = audit_raw_evidence(raw)
        assert "ratings" in result.has_data_fields

    def test_audit_ratings_failed(self):
        from tradingagents.dataflows.evidence_coverage_audit import audit_raw_evidence
        raw = {
            "ratings": {
                "raw": "600519.SH [DATA-012] RATINGS_FAILED: ConnectionError",
                "vendor": "cn_astock",
                "status": "FAILED",
                "error": "ConnectionError",
            },
        }
        result = audit_raw_evidence(raw)
        assert "ratings" in result.failed_fields


# ── Readiness Score ─────────────────────────────────────────────────────


class TestReadinessScoreRatings:
    def test_ratings_default_not_queried(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        result = infer_evidence_statuses({})
        assert result["ratings"] == "not_queried"

    def test_ratings_has_data_structured(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": {"status": "HAS_DATA", "raw": "data"}}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "has_data"

    def test_ratings_failed_structured(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": {"status": "FAILED", "raw": "failed"}}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "query_failed"

    def test_ratings_normal_no_data_structured(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": {"status": "NORMAL_NO_DATA", "raw": ""}}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "normal_no_data"

    def test_ratings_has_data_text(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": "600519.SH [DATA-012] RATINGS_HAS_DATA: ..."}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "has_data"

    def test_ratings_failed_text(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": "600519.SH [DATA-012] RATINGS_FAILED: error"}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "query_failed"

    def test_ratings_normal_no_data_text(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": "600519.SH [DATA-012] RATINGS_NORMAL_NO_DATA: 无分析师评级数据"}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "normal_no_data"

    def test_ratings_long_text_has_data(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": "600519.SH " + "x" * 100}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "has_data"

    def test_ratings_coverage_includes_ratings(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage
        coverage = calculate_evidence_coverage(
            ohlcv_5d="has_data",
            volume="has_data",
            turnover_rate="has_data",
            volume_ratio="has_data",
            individual_fund_flow="has_data",
            lhb_status="normal_no_data",
            margin_trading="has_data",
            announcements="has_data",
            research_report="has_data",
            ratings="has_data",
        )
        assert coverage > 70

    def test_ratings_not_queried_excluded(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage
        coverage_all_has = calculate_evidence_coverage(
            ohlcv_5d="has_data",
            volume="has_data",
            turnover_rate="has_data",
            volume_ratio="has_data",
            individual_fund_flow="has_data",
            lhb_status="has_data",
            margin_trading="has_data",
            announcements="has_data",
            research_report="has_data",
            ratings="not_queried",
        )
        coverage_all_has_with_ratings = calculate_evidence_coverage(
            ohlcv_5d="has_data",
            volume="has_data",
            turnover_rate="has_data",
            volume_ratio="has_data",
            individual_fund_flow="has_data",
            lhb_status="has_data",
            margin_trading="has_data",
            announcements="has_data",
            research_report="has_data",
            ratings="has_data",
        )
        assert coverage_all_has_with_ratings > coverage_all_has

    def test_ratings_failure_text_detected(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": "600519.SH [DATA-012] RATINGS_FAILED: Timeout"}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "query_failed"

    def test_ratings_empty_string_not_queried(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": {"status": "NOT_QUERIED", "raw": None}}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "not_queried"


# ── Interface ────────────────────────────────────────────────────────────


class TestInterfaceRatings:
    def test_get_ratings_in_tools_categories(self):
        from tradingagents.dataflows.interface import TOOLS_CATEGORIES
        tools = TOOLS_CATEGORIES["cn_market_data"]["tools"]
        assert "get_ratings" in tools

    def test_get_ratings_category(self):
        from tradingagents.dataflows.interface import get_category_for_method
        assert get_category_for_method("get_ratings") == "cn_market_data"

    def test_akshare_provider_has_method(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        assert hasattr(CnAkshareProvider, "get_ratings")

    def test_astock_provider_has_method(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        assert hasattr(CnAstockProvider, "get_ratings")


# ── Fixtures ─────────────────────────────────────────────────────────────


class TestRatingsFixtures:
    def test_ratings_fixture_ids_in_all(self):
        from tradingagents.dataflows.fixture_replay import ALL_FIXTURE_IDS
        assert FIXTURE_RATINGS_HAS_DATA in ALL_FIXTURE_IDS
        assert FIXTURE_RATINGS_FAILED in ALL_FIXTURE_IDS
        assert FIXTURE_RATINGS_NOT_QUERIED in ALL_FIXTURE_IDS

    def test_total_fixture_count(self):
        from tradingagents.dataflows.fixture_replay import ALL_FIXTURE_IDS
        assert len(ALL_FIXTURE_IDS) >= 22

    def test_ratings_has_data_fixture(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture(FIXTURE_RATINGS_HAS_DATA)
        assert fixture is not None
        assert fixture.data_type == "rating"
        assert fixture.expected_status == "HAS_DATA"
        assert fixture.vendor == "cn_astock"
        ev = fixture.raw_evidence["ratings"]
        assert ev["status"] == "HAS_DATA"
        assert "RATINGS_HAS_DATA" in ev["raw"]

    def test_ratings_failed_fixture(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture(FIXTURE_RATINGS_FAILED)
        assert fixture is not None
        assert fixture.expected_status == "FAILED"
        ev = fixture.raw_evidence["ratings"]
        assert ev["status"] == "FAILED"
        assert ev["error"] == "ConnectionError"

    def test_ratings_not_queried_fixture(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture(FIXTURE_RATINGS_NOT_QUERIED)
        assert fixture is not None
        assert fixture.expected_status == "NOT_QUERIED"
        ev = fixture.raw_evidence["ratings"]
        assert ev["status"] == "NOT_QUERIED"
        assert ev["raw"] is None

    def test_ratings_fixture_vendor(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        has_data = get_fixture(FIXTURE_RATINGS_HAS_DATA)
        assert has_data.vendor == "cn_astock"

    def test_ratings_fixture_error(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        failed = get_fixture(FIXTURE_RATINGS_FAILED)
        assert failed.raw_evidence["ratings"]["error"] == "ConnectionError"

    def test_ratings_fixture_no_vendor_not_queried(self):
        from tradingagents.dataflows.fixture_replay import get_fixture
        not_queried = get_fixture(FIXTURE_RATINGS_NOT_QUERIED)
        assert not_queried.raw_evidence["ratings"]["vendor"] == ""

    def test_all_ratings_fixtures_have_field(self):
        from tradingagents.dataflows.fixture_replay import ALL_FIXTURE_IDS, get_fixture
        for fid in ALL_FIXTURE_IDS:
            if fid.startswith("ratings_"):
                fixture = get_fixture(fid)
                assert "ratings" in fixture.raw_evidence

    def test_ratings_fixtures_evidence_contract_roundtrip(self):
        from tradingagents.dataflows.evidence_contract import EvidenceContract
        from tradingagents.dataflows.fixture_replay import get_fixture
        fixture = get_fixture(FIXTURE_RATINGS_HAS_DATA)
        ev = fixture.raw_evidence["ratings"]
        contract = EvidenceContract.from_dict(ev)
        assert contract.field == "ratings"
        assert contract.status == "HAS_DATA"


# ── Fixture Replay ──────────────────────────────────────────────────────


class TestRatingsFixtureReplay:
    def test_replay_ratings_has_data(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay([FIXTURE_RATINGS_HAS_DATA])
        assert report.total_fixtures >= 1
        assert report.all_passed is True
        result = report.results[0]
        assert result.passed is True
        assert result.actual_status == "HAS_DATA"

    def test_replay_ratings_failed(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay([FIXTURE_RATINGS_FAILED])
        result = report.results[0]
        assert result.passed is True
        assert result.actual_status == "FAILED"

    def test_replay_ratings_not_queried(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay([FIXTURE_RATINGS_NOT_QUERIED])
        result = report.results[0]
        assert result.passed is True
        assert result.actual_status == "NOT_QUERIED"

    def test_replay_all_ratings(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay([
            FIXTURE_RATINGS_HAS_DATA,
            FIXTURE_RATINGS_FAILED,
            FIXTURE_RATINGS_NOT_QUERIED,
        ])
        assert report.total_fixtures == 3
        assert report.all_passed is True

    def test_full_replay_includes_ratings(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay()
        rating_results = [r for r in report.results if r.data_type == "rating"]
        assert len(rating_results) == 3


# ── Provider Tests ──────────────────────────────────────────────────────


class TestProviderRatings:
    def test_akshare_get_ratings_method(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        assert callable(getattr(provider, "get_ratings", None))

    def test_astock_get_ratings_method(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        assert callable(getattr(provider, "get_ratings", None))

    @patch("tradingagents.dataflows.providers.cn_akshare_provider.AKSHARE_CALL_LOCK")
    def test_akshare_ratings_failure(self, mock_lock):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        with patch.object(provider, "_ak") as mock_ak:
            mock_ak.side_effect = Exception("ConnectionError")
            result = provider.get_ratings("600519.SH")
            assert "RATINGS_FAILED" in result
            assert "[DATA-012]" in result

    def test_astock_ratings_failure(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.Session") as mock_session:
            mock_session.return_value.get.side_effect = Exception("ConnectionError")
            result = provider.get_ratings("600519.SH")
            assert "RATINGS_FAILED" in result
            assert "[DATA-012]" in result

    def test_astock_ratings_no_data(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.Session") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": []}
            mock_session.return_value.get.return_value = mock_resp
            result = provider.get_ratings("600519.SH")
            assert "RATINGS_NORMAL_NO_DATA" in result
            assert "[DATA-012]" in result

    def test_astock_ratings_has_data(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.Session") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "data": [
                    {
                        "publishDate": "2026-06-06",
                        "orgSName": "东方证券",
                        "emRatingName": "买入",
                        "lastEmRatingName": "增持",
                        "indvAimPriceT": "2100",
                        "title": "贵州茅台点评",
                    }
                ],
                "TotalPage": 1,
            }
            mock_session.return_value.get.return_value = mock_resp
            result = provider.get_ratings("600519.SH")
            assert "RATINGS_HAS_DATA" in result
            assert "[DATA-012]" in result
            assert "买入" in result
            assert "增持" in result

    @patch("tradingagents.dataflows.providers.cn_akshare_provider.AKSHARE_CALL_LOCK")
    def test_akshare_ratings_attribute_error(self, mock_lock):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        with patch.object(provider, "_ak") as mock_ak:
            mock_ak.return_value = MagicMock(
                spec=[]
            )
            result = provider.get_ratings("600519.SH")
            assert "RATINGS_NORMAL_NO_DATA" in result or "RATINGS_FAILED" in result

    @patch("tradingagents.dataflows.providers.cn_akshare_provider.AKSHARE_CALL_LOCK")
    def test_akshare_ratings_has_data(self, mock_lock):
        import pandas as pd
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        df = pd.DataFrame({
            "股票代码": ["600519"],
            "股票名称": ["贵州茅台"],
            "最新评级": ["买入"],
            "评级机构": ["东方证券"],
            "分析师": ["张三"],
            "目标价": [2100],
            "评级日期": ["2026-06-06"],
        })
        with patch.object(provider, "_ak") as mock_ak:
            mock_ak.return_value.stock_institute_recommend_detail.return_value = df
            result = provider.get_ratings("600519.SH")
            assert "RATINGS_HAS_DATA" in result
            assert "买入" in result


# ── Data Collector Wiring ────────────────────────────────────────────────


class TestDataCollectorRatingsWiring:
    def test_get_ratings_importable_from_agent_utils(self):
        from tradingagents.agents.utils.agent_utils import get_ratings
        assert hasattr(get_ratings, "invoke")

    def test_get_ratings_in_game_theory_tools(self):
        from tradingagents.agents.utils.game_theory_tools import get_ratings
        assert hasattr(get_ratings, "invoke")

    def test_ratings_in_evidence_key_to_data_type(self):
        from tradingagents.graph.data_collector import _EVIDENCE_KEY_TO_DATA_TYPE
        assert _EVIDENCE_KEY_TO_DATA_TYPE.get("ratings") == "rating"

    def test_ratings_in_build_raw_evidence_keys(self):
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        pool = {
            "ratings": "600519.SH [DATA-012] RATINGS_HAS_DATA: 买入 东方证券",
        }
        dc._cache["600519.SH_2026-06-06"] = pool
        evidence = dc.build_raw_evidence("600519.SH", "2026-06-06")
        assert "ratings" in evidence
        assert evidence["ratings"]["status"] == "HAS_DATA"
        assert evidence["ratings"]["field"] == "ratings"

    def test_ratings_normal_no_data_inferred(self):
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        pool = {
            "ratings": "600519.SH [DATA-012] RATINGS_NORMAL_NO_DATA: 无分析师评级数据",
        }
        dc._cache["600519.SH_2026-06-06"] = pool
        evidence = dc.build_raw_evidence("600519.SH", "2026-06-06")
        assert evidence["ratings"]["status"] == "NORMAL_NO_DATA"

    def test_ratings_failed_inferred(self):
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        pool = {
            "ratings": "600519.SH [DATA-012] RATINGS_FAILED: ConnectionError",
        }
        dc._cache["600519.SH_2026-06-06"] = pool
        evidence = dc.build_raw_evidence("600519.SH", "2026-06-06")
        assert evidence["ratings"]["status"] == "FAILED"
        assert evidence["ratings"]["error"] is not None

    def test_ratings_not_queried_when_key_absent(self):
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        pool = {
            "stock_data": "some data",
        }
        dc._cache["600519.SH_2026-06-06"] = pool
        evidence = dc.build_raw_evidence("600519.SH", "2026-06-06")
        assert "ratings" in evidence
        assert evidence["ratings"]["status"] == "NOT_QUERIED"

    def test_ratings_vendor_resolved(self):
        from unittest.mock import patch
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        pool = {
            "ratings": "600519.SH [DATA-012] RATINGS_HAS_DATA: 买入",
        }
        dc._cache["600519.SH_2026-06-06"] = pool
        with patch("tradingagents.graph.data_collector.get_last_hit_vendor") as mock_vendor:
            mock_vendor.return_value = "cn_astock"
            evidence = dc.build_raw_evidence("600519.SH", "2026-06-06")
            assert evidence["ratings"]["vendor"] == "cn_astock"
            mock_vendor.assert_called_with("get_ratings")

    def test_ratings_evidence_has_required_fields(self):
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        pool = {
            "ratings": "600519.SH [DATA-012] RATINGS_HAS_DATA: 买入 东方证券",
        }
        dc._cache["600519.SH_2026-06-06"] = pool
        evidence = dc.build_raw_evidence("600519.SH", "2026-06-06")
        entry = evidence["ratings"]
        required = ["raw", "field", "status", "vendor", "endpoint", "as_of",
                     "fetched_at", "record_count", "unit", "error", "fallback_from",
                     "source_url", "is_realtime_patched"]
        for field_name in required:
            assert field_name in entry, f"Missing field: {field_name}"

    def test_ratings_evidence_endpoint_resolved(self):
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        pool = {
            "ratings": "600519.SH [DATA-012] RATINGS_HAS_DATA: 买入",
        }
        dc._cache["600519.SH_2026-06-06"] = pool
        evidence = dc.build_raw_evidence("600519.SH", "2026-06-06")
        assert evidence["ratings"]["endpoint"] != ""

    def test_ratings_coverage_four_states(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        states_map = {
            "HAS_DATA": {"ratings": {"status": "HAS_DATA", "raw": "data"}},
            "FAILED": {"ratings": {"status": "FAILED", "raw": "error"}},
            "NORMAL_NO_DATA": {"ratings": {"status": "NORMAL_NO_DATA", "raw": ""}},
        }
        expected = {
            "HAS_DATA": "has_data",
            "FAILED": "query_failed",
            "NORMAL_NO_DATA": "normal_no_data",
        }
        for status, raw_ev in states_map.items():
            result = infer_evidence_statuses({}, raw_evidence=raw_ev)
            assert result["ratings"] == expected[status]
        result = infer_evidence_statuses({})
        assert result["ratings"] == "not_queried"


# ── Acceptance ──────────────────────────────────────────────────────────


class TestAcceptanceDATA012:
    def test_source_catalog_has_primary(self):
        from tradingagents.dataflows.source_catalog import get_primary_source
        primary = get_primary_source("rating")
        assert primary is not None
        assert primary.vendor == "cn_akshare"
        assert primary.is_primary is True

    def test_evidence_contract_complete(self):
        from tradingagents.dataflows.evidence_contract import (
            EvidenceContract,
            resolve_data_type,
        )
        assert resolve_data_type("ratings") == "rating"
        contract = EvidenceContract(
            field="ratings",
            value="data",
            vendor="cn_astock",
            endpoint="reportapi.eastmoney.com/report/list",
            status="HAS_DATA",
        )
        assert contract.status == "HAS_DATA"
        assert contract.has_data

    def test_ratings_has_data_status(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": {"status": "HAS_DATA", "raw": "data"}}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "has_data"

    def test_ratings_failed_status(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": {"status": "FAILED", "raw": "error"}}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "query_failed"

    def test_ratings_normal_no_data_status(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        raw_evidence = {"ratings": {"status": "NORMAL_NO_DATA", "raw": ""}}
        result = infer_evidence_statuses({}, raw_evidence=raw_evidence)
        assert result["ratings"] == "normal_no_data"

    def test_ratings_not_queried_status(self):
        from tradingagents.agents.utils.readiness_score import infer_evidence_statuses
        result = infer_evidence_statuses({})
        assert result["ratings"] == "not_queried"

    def test_ratings_not_inflating_coverage(self):
        from tradingagents.agents.utils.readiness_score import calculate_evidence_coverage
        coverage_without = calculate_evidence_coverage(
            ohlcv_5d="has_data", volume="has_data",
            turnover_rate="has_data", volume_ratio="has_data",
            individual_fund_flow="has_data", lhb_status="normal_no_data",
            margin_trading="has_data", announcements="has_data",
            research_report="has_data", ratings="not_queried",
        )
        coverage_with = calculate_evidence_coverage(
            ohlcv_5d="has_data", volume="has_data",
            turnover_rate="has_data", volume_ratio="has_data",
            individual_fund_flow="has_data", lhb_status="normal_no_data",
            margin_trading="has_data", announcements="has_data",
            research_report="has_data", ratings="has_data",
        )
        assert coverage_with > coverage_without

    def test_ratings_fixtures_pass(self):
        from tradingagents.dataflows.fixture_replay import run_fixture_replay
        report = run_fixture_replay([
            FIXTURE_RATINGS_HAS_DATA,
            FIXTURE_RATINGS_FAILED,
            FIXTURE_RATINGS_NOT_QUERIED,
        ])
        assert report.all_passed is True

    def test_ratings_provider_tags(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        ak_provider = CnAkshareProvider()
        em_provider = CnAstockProvider()
        with patch.object(ak_provider, "_ak", side_effect=Exception("test")):
            result = ak_provider.get_ratings("600519.SH")
            assert "[DATA-012]" in result
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.Session") as mock_session:
            mock_session.return_value.get.side_effect = Exception("test")
            result = em_provider.get_ratings("600519.SH")
            assert "[DATA-012]" in result

    def test_ratings_no_strong_action_words(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.Session") as mock_session:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "data": [
                    {
                        "publishDate": "2026-06-06",
                        "orgSName": "东方证券",
                        "emRatingName": "买入",
                        "lastEmRatingName": "增持",
                        "indvAimPriceT": "2100",
                        "title": "test",
                    }
                ],
                "TotalPage": 1,
            }
            mock_session.return_value.get.return_value = mock_resp
            result = provider.get_ratings("600519.SH")
            for word in ["建议买入", "强烈推荐", "立即买入"]:
                assert word not in result

    def test_data012a_collector_wiring_complete(self):
        from tradingagents.graph.data_collector import DataCollector, _EVIDENCE_KEY_TO_DATA_TYPE
        assert _EVIDENCE_KEY_TO_DATA_TYPE.get("ratings") == "rating"
        dc = DataCollector()
        pool = {
            "ratings": "600519.SH [DATA-012] RATINGS_HAS_DATA: 买入 东方证券 目标价2100",
        }
        dc._cache["600519.SH_2026-06-06"] = pool
        evidence = dc.build_raw_evidence("600519.SH", "2026-06-06")
        assert "ratings" in evidence
        assert evidence["ratings"]["status"] == "HAS_DATA"
        assert evidence["ratings"]["vendor"] != ""
        assert evidence["ratings"]["endpoint"] != ""
        assert evidence["ratings"]["fetched_at"] != ""

    def test_infer_source_status_ratings_normal_no_data_with_bukyong(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "RATINGS_NORMAL_NO_DATA: AKShare 评级接口不可用。"
        )
        assert status == "NORMAL_NO_DATA"

    def test_infer_source_status_ratings_failed_with_huoqushibai(self):
        from tradingagents.graph.data_collector import DataCollector
        status = DataCollector._infer_source_status(
            "RATINGS_FAILED: 评级数据获取失败，ConnectionError"
        )
        assert status == "FAILED"

    def test_data012a_four_states_from_collector(self):
        from tradingagents.graph.data_collector import DataCollector
        dc = DataCollector()
        test_cases = [
            ("600519.SH [DATA-012] RATINGS_HAS_DATA: data", "HAS_DATA"),
            ("600519.SH [DATA-012] RATINGS_NORMAL_NO_DATA: 无分析师评级", "NORMAL_NO_DATA"),
            ("600519.SH [DATA-012] RATINGS_FAILED: ConnectionError", "FAILED"),
        ]
        for idx, (raw_val, expected_status) in enumerate(test_cases):
            pool = {"ratings": raw_val}
            dc._cache[f"600519.SH_2026-06-{idx+1:02d}"] = pool
            evidence = dc.build_raw_evidence("600519.SH", f"2026-06-{idx+1:02d}")
            assert evidence["ratings"]["status"] == expected_status, \
                f"Expected {expected_status} for raw={raw_val[:50]}, got {evidence['ratings']['status']}"
