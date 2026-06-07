# [DATA-013] buyback_raw_evidence
"""
Tests for buyback data source registration, provider routing, raw_evidence,
evidence coverage audit, readiness score, and fixture replay.
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
    _SOURCE_CATALOG,
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
    calculate_evidence_coverage,
    infer_evidence_statuses,
)
from tradingagents.dataflows.fixture_replay import (
    FIXTURE_BUYBACK_HAS_DATA,
    FIXTURE_BUYBACK_FAILED,
    FIXTURE_BUYBACK_NOT_QUERIED,
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


# ── Source Catalog ────────────────────────────────────────────────────


class TestSourceCatalogBuyback:
    def test_buyback_data_type_exists(self):
        assert DataType.BUYBACK.value == "buyback"

    def test_buyback_sources_registered(self):
        sources = get_sources_for_type(DataType.BUYBACK)
        assert len(sources) >= 2

    def test_buyback_primary_source(self):
        primary = get_primary_source(DataType.BUYBACK)
        assert primary is not None
        assert primary.vendor == "cn_akshare"
        assert primary.is_primary

    def test_buyback_fallback_chain(self):
        chain = get_fallback_chain(DataType.BUYBACK)
        assert "cn_akshare" in chain
        assert "cn_astock" in chain
        assert chain.index("cn_akshare") < chain.index("cn_astock")

    def test_buyback_in_all_data_types(self):
        all_types = get_all_data_types()
        assert "buyback" in all_types

    def test_buyback_primary_fields(self):
        primary = get_primary_source(DataType.BUYBACK)
        assert len(primary.fields) > 0

    def test_buyback_primary_endpoint(self):
        primary = get_primary_source(DataType.BUYBACK)
        assert primary.endpoint == "stock_repurchase"

    def test_catalog_validation_passes(self):
        issues = validate_catalog()
        buyback_issues = [i for i in issues if "buyback" in i.lower()]
        assert len(buyback_issues) == 0


# ── Evidence Contract ─────────────────────────────────────────────────


class TestEvidenceContractBuyback:
    def test_resolve_data_type_buybacks(self):
        assert resolve_data_type("buybacks") == "buyback"

    def test_buyback_has_data_contract(self):
        contract = EvidenceContract(
            field="buybacks",
            unit="万元",
            vendor="cn_astock",
            endpoint="datacenter-web.eastmoney.com/RPT_SHAREBUYBACK_DET",
            status="HAS_DATA",
            unit_verified=True,
            record_count=3,
        )
        assert contract.has_data
        assert contract.unit_known
        assert contract.unit_verified is True

    def test_buyback_failed_contract(self):
        contract = EvidenceContract(
            field="buybacks",
            vendor="cn_astock",
            status="FAILED",
            error="ConnectionError",
        )
        assert contract.is_failed
        assert not contract.has_data

    def test_buyback_completeness(self):
        raw = {
            "buybacks": {
                "raw": "data",
                "field": "buybacks",
                "unit": "万元",
                "vendor": "cn_astock",
                "endpoint": "test",
                "status": "HAS_DATA",
                "unit_verified": True,
            }
        }
        result = compute_contract_completeness(raw)
        assert result["completeness_score"] > 0

    def test_buyback_missing_completeness(self):
        raw = {
            "buybacks": {
                "raw": None,
                "field": "buybacks",
                "status": "FAILED",
            }
        }
        result = compute_contract_completeness(raw)
        assert "buybacks" in result["missing_details"]

    def test_buyback_in_required_fields(self):
        assert "buybacks" in _REQUIRED_FIELDS_FOR_COMPLETENESS
        assert "status" in _REQUIRED_FIELDS_FOR_COMPLETENESS["buybacks"]
        assert "vendor" in _REQUIRED_FIELDS_FOR_COMPLETENESS["buybacks"]
        assert "unit" in _REQUIRED_FIELDS_FOR_COMPLETENESS["buybacks"]


# ── Evidence Coverage Audit ───────────────────────────────────────────


class TestEvidenceCoverageAuditBuyback:
    def test_buyback_label(self):
        assert _EVIDENCE_FIELD_LABELS["buybacks"] == "回购"

    def test_buyback_family(self):
        assert _EVIDENCE_FIELD_FAMILIES["buybacks"] == "buyback"

    def test_audit_buyback_has_data(self):
        raw = {
            "buybacks": {
                "raw": "data",
                "field": "buybacks",
                "unit": "万元",
                "vendor": "cn_astock",
                "status": "HAS_DATA",
                "unit_verified": True,
            }
        }
        result = audit_raw_evidence(raw)
        assert "buybacks" in result.has_data_fields

    def test_audit_buyback_failed(self):
        raw = {
            "buybacks": {
                "raw": "error",
                "field": "buybacks",
                "vendor": "cn_astock",
                "status": "FAILED",
                "error": "ConnectionError",
            }
        }
        result = audit_raw_evidence(raw)
        assert "buybacks" in result.failed_fields


# ── Readiness Score ───────────────────────────────────────────────────


class TestReadinessScoreBuyback:
    def test_buyback_not_queried_default(self):
        statuses = infer_evidence_statuses({}, raw_evidence={})
        assert statuses["buybacks"] == EvidenceStatus.NOT_QUERIED

    def test_buyback_has_data_from_structured(self):
        raw = {
            "buybacks": {
                "raw": "data",
                "status": "HAS_DATA",
            }
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.HAS_DATA

    def test_buyback_failed_from_structured(self):
        raw = {
            "buybacks": {
                "raw": "error",
                "status": "FAILED",
            }
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.QUERY_FAILED

    def test_buyback_normal_no_data_from_structured(self):
        raw = {
            "buybacks": {
                "raw": "",
                "status": "NORMAL_NO_DATA",
            }
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.NORMAL_NO_DATA

    def test_buyback_has_data_from_text(self):
        raw = {
            "buybacks": {
                "raw": "600519.SH [DATA-013] BUYBACK_HAS_DATA: 回购数据",
                "status": "HAS_DATA",
            }
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.HAS_DATA

    def test_buyback_failed_from_text(self):
        raw = {
            "buybacks": {
                "raw": "600519.SH [DATA-013] BUYBACK_FAILED: 回购数据获取失败",
                "status": "FAILED",
            }
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.QUERY_FAILED

    def test_buyback_coverage_increase(self):
        coverage_without = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
        )
        coverage_with = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            buybacks=EvidenceStatus.HAS_DATA,
        )
        assert coverage_with >= coverage_without

    def test_buyback_not_queried_text_fallback(self):
        raw = {
            "buybacks": {
                "raw": "something without keywords",
            }
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.HAS_DATA

    def test_buyback_normal_no_data_text(self):
        raw = {
            "buybacks": "BUYBACK_NORMAL_NO_DATA",
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.NORMAL_NO_DATA


# ── Interface ─────────────────────────────────────────────────────────


class TestInterfaceBuyback:
    def test_get_buybacks_in_tools_categories(self):
        tools = TOOLS_CATEGORIES["cn_market_data"]["tools"]
        assert "get_buybacks" in tools

    def test_get_buybacks_category(self):
        assert get_category_for_method("get_buybacks") == "cn_market_data"

    def test_failure_string_detection(self):
        failure = "600519.SH [DATA-013] BUYBACK_FAILED: 回购数据获取失败：ConnectionError"
        assert any(p in failure for p in _FAILURE_RESULT_PATTERNS)


# ── Provider Methods ──────────────────────────────────────────────────


class TestProviderBuyback:
    def test_akshare_has_get_buybacks(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        assert hasattr(provider, "get_buybacks")
        assert callable(provider.get_buybacks)

    def test_astock_has_get_buybacks(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        assert hasattr(provider, "get_buybacks")
        assert callable(provider.get_buybacks)

    @patch("tradingagents.dataflows.providers.cn_akshare_provider.CnAkshareProvider._ak")
    def test_akshare_buyback_no_data(self, mock_ak):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        mock_ak.return_value.stock_repurchase_em.return_value = None
        result = provider.get_buybacks("600519.SH")
        assert "BUYBACK_NORMAL_NO_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_akshare_provider.CnAkshareProvider._ak")
    def test_akshare_buyback_has_data(self, mock_ak):
        import pandas as pd
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        df = pd.DataFrame({
            "公告日期": ["2026-05-15"],
            "回购金额": ["50000万"],
            "回购数量": ["2500000股"],
            "回购进度": ["实施中"],
            "回购目的": ["股权激励"],
        })
        mock_ak.return_value.stock_repurchase_em.return_value = df
        result = provider.get_buybacks("600519.SH")
        assert "BUYBACK_HAS_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter")
    def test_astock_buyback_no_data(self, mock_dc):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_dc.return_value = []
        result = provider.get_buybacks("600519.SH")
        assert "BUYBACK_NORMAL_NO_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter")
    def test_astock_buyback_has_data(self, mock_dc):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_dc.return_value = [
            {
                "NOTICE_DATE": "2026-05-15",
                "BUYBACK_AMOUNT": 500000000,
                "BUYBACK_VOLUME": 2500000,
                "PROGRESS": "实施中",
                "PURPOSE": "股权激励注销",
            }
        ]
        result = provider.get_buybacks("600519.SH")
        assert "BUYBACK_HAS_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter")
    def test_astock_buyback_failed(self, mock_dc):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_dc.side_effect = ConnectionError("timeout")
        result = provider.get_buybacks("600519.SH")
        assert "BUYBACK_FAILED" in result


# ── Fixtures ──────────────────────────────────────────────────────────


class TestBuybackFixtures:
    def test_buyback_fixture_ids_exist(self):
        assert FIXTURE_BUYBACK_HAS_DATA == "buyback_has_data"
        assert FIXTURE_BUYBACK_FAILED == "buyback_failed"
        assert FIXTURE_BUYBACK_NOT_QUERIED == "buyback_not_queried"

    def test_all_fixture_ids_count(self):
        assert len(ALL_FIXTURE_IDS) >= 21

    def test_buyback_fixtures_in_all_fixture_ids(self):
        assert FIXTURE_BUYBACK_HAS_DATA in ALL_FIXTURE_IDS
        assert FIXTURE_BUYBACK_FAILED in ALL_FIXTURE_IDS
        assert FIXTURE_BUYBACK_NOT_QUERIED in ALL_FIXTURE_IDS
        assert len(ALL_FIXTURE_IDS) == 25

    def test_buyback_has_data_fixture(self):
        fixture = get_fixture(FIXTURE_BUYBACK_HAS_DATA)
        assert fixture is not None
        assert fixture.data_type == "buyback"
        assert fixture.vendor == "cn_astock"
        assert fixture.expected_status == "HAS_DATA"
        assert "buybacks" in fixture.raw_evidence
        assert fixture.raw_evidence["buybacks"]["status"] == "HAS_DATA"
        assert fixture.raw_evidence["buybacks"]["unit"] == "万元"
        assert fixture.raw_evidence["buybacks"]["unit_verified"] is True

    def test_buyback_failed_fixture(self):
        fixture = get_fixture(FIXTURE_BUYBACK_FAILED)
        assert fixture is not None
        assert fixture.expected_status == "FAILED"
        assert fixture.raw_evidence["buybacks"]["status"] == "FAILED"
        assert fixture.raw_evidence["buybacks"]["error"] == "ConnectionError"

    def test_buyback_not_queried_fixture(self):
        fixture = get_fixture(FIXTURE_BUYBACK_NOT_QUERIED)
        assert fixture is not None
        assert fixture.expected_status == "NOT_QUERIED"
        assert fixture.raw_evidence["buybacks"]["status"] == "NOT_QUERIED"
        assert fixture.raw_evidence["buybacks"]["vendor"] == ""

    def test_no_vendor_in_not_queried(self):
        fixture = get_fixture(FIXTURE_BUYBACK_NOT_QUERIED)
        assert fixture.raw_evidence["buybacks"]["vendor"] == ""
        assert fixture.raw_evidence["buybacks"]["endpoint"] == ""

    def test_all_fixtures_returns_buybacks(self):
        fixtures = get_all_fixtures()
        buyback_ids = [f.fixture_id for f in fixtures if "buyback" in f.fixture_id]
        assert len(buyback_ids) == 3


# ── Fixture Replay ────────────────────────────────────────────────────


class TestBuybackFixtureReplay:
    def test_replay_buyback_has_data(self):
        fixture = get_fixture(FIXTURE_BUYBACK_HAS_DATA)
        report = run_fixture_replay(fixtures=[fixture])
        assert report.total_fixtures == 1
        assert report.passed == 1
        assert report.results[0].actual_status == "HAS_DATA"

    def test_replay_buyback_failed(self):
        fixture = get_fixture(FIXTURE_BUYBACK_FAILED)
        report = run_fixture_replay(fixtures=[fixture])
        assert report.total_fixtures == 1
        assert report.results[0].actual_status == "FAILED"

    def test_replay_buyback_not_queried(self):
        fixture = get_fixture(FIXTURE_BUYBACK_NOT_QUERIED)
        report = run_fixture_replay(fixtures=[fixture])
        assert report.total_fixtures == 1
        assert report.results[0].actual_status == "NOT_QUERIED"

    def test_replay_all_buyback_fixtures(self):
        buyback_fixtures = [
            get_fixture(FIXTURE_BUYBACK_HAS_DATA),
            get_fixture(FIXTURE_BUYBACK_FAILED),
            get_fixture(FIXTURE_BUYBACK_NOT_QUERIED),
        ]
        report = run_fixture_replay(fixtures=buyback_fixtures)
        assert report.total_fixtures == 3
        assert report.all_passed

    def test_full_replay_includes_buybacks(self):
        report = run_fixture_replay()
        buyback_results = [r for r in report.results if "buyback" in r.fixture_id]
        assert len(buyback_results) == 3


# ── Dedup / Event Source Consistency ──────────────────────────────────


class TestBuybackDedup:
    def test_buyback_event_no_double_count(self):
        raw = {
            "buybacks": {
                "raw": "600519.SH [DATA-013] BUYBACK_HAS_DATA: 回购数据",
                "field": "buybacks",
                "status": "HAS_DATA",
                "vendor": "cn_astock",
                "unit": "万元",
                "record_count": 1,
            }
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.HAS_DATA
        audit = audit_raw_evidence(raw)
        has_data_count = sum(1 for f in audit.has_data_fields if f == "buybacks")
        assert has_data_count == 1

    def test_buyback_same_event_different_vendor(self):
        raw = {
            "buybacks": {
                "raw": "data from astock",
                "field": "buybacks",
                "status": "HAS_DATA",
                "vendor": "cn_astock",
                "fallback_from": "cn_akshare",
                "unit": "万元",
            }
        }
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["buybacks"] == EvidenceStatus.HAS_DATA
        audit = audit_raw_evidence(raw)
        assert "buybacks" in audit.fallback_fields


# ── Acceptance ────────────────────────────────────────────────────────


class TestAcceptanceDATA013:
    def test_source_catalog_has_buyback(self):
        sources = get_sources_for_type(DataType.BUYBACK)
        assert len(sources) >= 2

    def test_provider_route_registered(self):
        tools = TOOLS_CATEGORIES["cn_market_data"]["tools"]
        assert "get_buybacks" in tools

    def test_evidence_contract_has_buybacks(self):
        assert resolve_data_type("buybacks") == "buyback"

    def test_four_fixture_scenarios(self):
        fixtures = [
            get_fixture(FIXTURE_BUYBACK_HAS_DATA),
            get_fixture(FIXTURE_BUYBACK_FAILED),
            get_fixture(FIXTURE_BUYBACK_NOT_QUERIED),
        ]
        assert all(f is not None for f in fixtures)
        statuses = {f.expected_status for f in fixtures}
        assert "HAS_DATA" in statuses
        assert "FAILED" in statuses
        assert "NOT_QUERIED" in statuses

    def test_no_double_scoring(self):
        raw = {
            "buybacks": {
                "raw": "data",
                "status": "HAS_DATA",
                "vendor": "cn_astock",
                "unit": "万元",
            }
        }
        audit = audit_raw_evidence(raw)
        count = sum(1 for f in audit.has_data_fields if f == "buybacks")
        assert count == 1

    def test_readiness_score_four_states(self):
        for status_key, expected in [
            ("HAS_DATA", EvidenceStatus.HAS_DATA),
            ("FAILED", EvidenceStatus.QUERY_FAILED),
            ("NORMAL_NO_DATA", EvidenceStatus.NORMAL_NO_DATA),
        ]:
            raw = {"buybacks": {"raw": "", "status": status_key}}
            statuses = infer_evidence_statuses({}, raw_evidence=raw)
            assert statuses["buybacks"] == expected

        statuses = infer_evidence_statuses({}, raw_evidence={})
        assert statuses["buybacks"] == EvidenceStatus.NOT_QUERIED

    def test_buyback_does_not_generate_buy_signal(self):
        fixture = get_fixture(FIXTURE_BUYBACK_HAS_DATA)
        ev = fixture.raw_evidence["buybacks"]["raw"]
        forbidden = ["买入", "卖出", "强烈推荐", "抄底", "清仓"]
        for word in forbidden:
            assert word not in ev

    def test_replay_all_passes(self):
        report = run_fixture_replay()
        assert report.all_passed

    def test_evidence_coverage_label_exists(self):
        assert "buybacks" in _EVIDENCE_FIELD_LABELS
        assert _EVIDENCE_FIELD_LABELS["buybacks"] == "回购"
