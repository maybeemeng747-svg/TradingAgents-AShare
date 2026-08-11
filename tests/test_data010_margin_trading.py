# [DATA-010] margin_trading_raw_evidence
"""
融资融券数据源注册与 raw_evidence 接入测试。

覆盖：
  1. source_catalog 中 margin_trading 数据类型注册与查询
  2. evidence_contract 中 margin_trading 映射与完整性计算
  3. evidence_coverage_audit 中 margin_trading 标签
  4. readiness_score 中 margin_trading 从 raw_evidence 读取
  5. fixture_replay 中新增 margin_trading fixture
  6. provider route 中 get_margin_trading 方法
  7. 验收标准
"""

import pytest
from unittest.mock import patch, MagicMock

from tradingagents.dataflows.source_catalog import (
    DataType,
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
    build_data_source_summary,
)
from tradingagents.dataflows.evidence_coverage_audit import (
    _EVIDENCE_FIELD_LABELS,
    _EVIDENCE_FIELD_FAMILIES,
    audit_raw_evidence,
)
from tradingagents.dataflows.fixture_replay import (
    ALL_FIXTURE_IDS,
    FIXTURE_MARGIN_HAS_DATA,
    FIXTURE_MARGIN_FAILED,
    FIXTURE_MARGIN_NOT_QUERIED,
    get_fixture,
    run_fixture_replay,
    get_all_fixtures,
)
from tradingagents.dataflows.interface import (
    TOOLS_CATEGORIES,
    get_category_for_method,
    route_to_vendor,
    get_last_hit_vendor,
)
from tradingagents.agents.utils.readiness_score import (
    EvidenceStatus,
    infer_evidence_statuses,
    calculate_evidence_coverage,
)


# ── Source Catalog ──────────────────────────────────────────────────────


class TestSourceCatalogMarginTrading:
    def test_margin_trading_data_type_exists(self):
        assert DataType.MARGIN_TRADING.value == "margin_trading"

    def test_margin_trading_sources_registered(self):
        sources = get_sources_for_type(DataType.MARGIN_TRADING)
        assert len(sources) >= 2

    def test_margin_trading_has_primary(self):
        primary = get_primary_source(DataType.MARGIN_TRADING)
        assert primary is not None
        assert primary.vendor == "cn_tushare"
        assert primary.is_primary is True

    def test_margin_trading_fallback_chain(self):
        chain = get_fallback_chain(DataType.MARGIN_TRADING)
        assert "cn_tushare" in chain
        assert "cn_akshare" in chain
        assert "cn_astock" in chain
        assert chain.index("cn_tushare") < chain.index("cn_akshare")
        assert chain.index("cn_akshare") < chain.index("cn_astock")

    def test_margin_trading_in_all_data_types(self):
        types = get_all_data_types()
        assert "margin_trading" in types

    def test_margin_trading_primary_has_fields(self):
        primary = get_primary_source(DataType.MARGIN_TRADING)
        assert len(primary.fields) > 0
        assert primary.unit != ""

    def test_margin_trading_astock_source(self):
        sources = get_sources_for_type(DataType.MARGIN_TRADING)
        astock = [s for s in sources if s.vendor == "cn_astock"]
        assert len(astock) >= 1
        assert "RPT_RZRQ_LSHJ" in astock[0].endpoint

    def test_catalog_validation_passes(self):
        issues = validate_catalog()
        margin_issues = [i for i in issues if "margin_trading" in i]
        assert len(margin_issues) == 0


# ── Evidence Contract ──────────────────────────────────────────────────


class TestEvidenceContractMarginTrading:
    def test_resolve_data_type_margin_trading(self):
        assert resolve_data_type("margin_trading") == "margin_trading"

    def test_resolve_data_type_unknown(self):
        assert resolve_data_type("nonexistent_key") == ""

    def test_margin_trading_contract_has_data(self):
        contract = EvidenceContract(
            field="margin_trading",
            value="融资余额 185200万",
            unit="万元",
            vendor="cn_astock",
            endpoint="datacenter-web.eastmoney.com/RPT_RZRQ_LSHJ",
            as_of="2026-06-07",
            status="HAS_DATA",
            unit_verified=True,
            record_count=10,
        )
        assert contract.has_data is True
        assert contract.is_failed is False
        assert contract.unit_known is True

    def test_margin_trading_contract_failed(self):
        contract = EvidenceContract(
            field="margin_trading",
            status="FAILED",
            error="ConnectionError",
        )
        assert contract.has_data is False
        assert contract.is_failed is True

    def test_margin_trading_completeness_calculation(self):
        raw_evidence = {
            "margin_trading": {
                "raw": "融资融券数据",
                "field": "margin_trading",
                "unit": "万元",
                "vendor": "cn_astock",
                "endpoint": "datacenter-web.eastmoney.com/RPT_RZRQ_LSHJ",
                "as_of": "2026-06-07",
                "fetched_at": "2026-06-07T10:00:00",
                "status": "HAS_DATA",
                "fallback_from": None,
                "source_url": None,
                "error": None,
                "is_realtime_patched": False,
                "unit_verified": True,
                "record_count": 10,
            }
        }
        result = compute_contract_completeness(raw_evidence)
        assert "margin_trading" not in result["missing_details"]

    def test_margin_trading_missing_all(self):
        result = compute_contract_completeness({"margin_trading": None})
        assert result["missing_details"].get("margin_trading") is not None

    def test_margin_trading_failed_completeness(self):
        raw_evidence = {
            "margin_trading": {
                "raw": None,
                "field": "margin_trading",
                "status": "FAILED",
                "error": "ConnectionError",
                "vendor": "",
            }
        }
        result = compute_contract_completeness(raw_evidence)
        assert result["completeness_score"] < 100

    def test_build_data_source_summary_includes_margin(self):
        raw_evidence = {
            "margin_trading": {
                "raw": "data",
                "field": "margin_trading",
                "unit": "万元",
                "vendor": "cn_astock",
                "endpoint": "RPT_RZRQ_LSHJ",
                "status": "HAS_DATA",
            }
        }
        summary = build_data_source_summary(raw_evidence)
        margin_items = [s for s in summary if s["field"] == "margin_trading"]
        assert len(margin_items) == 1
        assert margin_items[0]["vendor"] == "cn_astock"
        assert margin_items[0]["status"] == "HAS_DATA"


# ── Evidence Coverage Audit ────────────────────────────────────────────


class TestEvidenceCoverageAuditMarginTrading:
    def test_margin_trading_label_exists(self):
        assert "margin_trading" in _EVIDENCE_FIELD_LABELS
        assert _EVIDENCE_FIELD_LABELS["margin_trading"] == "融资融券"

    def test_margin_trading_family_exists(self):
        assert "margin_trading" in _EVIDENCE_FIELD_FAMILIES
        assert _EVIDENCE_FIELD_FAMILIES["margin_trading"] == "margin_trading"

    def test_audit_raw_evidence_margin_has_data(self):
        raw_evidence = {
            "margin_trading": {
                "raw": "600519 [DATA-010] MARGIN_HAS_DATA",
                "field": "margin_trading",
                "unit": "万元",
                "vendor": "cn_astock",
                "endpoint": "RPT_RZRQ_LSHJ",
                "as_of": "2026-06-07",
                "status": "HAS_DATA",
                "unit_verified": True,
                "record_count": 10,
            }
        }
        result = audit_raw_evidence(raw_evidence)
        assert "margin_trading" in result.has_data_fields

    def test_audit_raw_evidence_margin_failed(self):
        raw_evidence = {
            "margin_trading": {
                "raw": "MARGIN_FAILED: ConnectionError",
                "field": "margin_trading",
                "status": "FAILED",
                "error": "ConnectionError",
            }
        }
        result = audit_raw_evidence(raw_evidence)
        assert "margin_trading" in result.failed_fields


# ── Readiness Score ────────────────────────────────────────────────────


class TestReadinessScoreMarginTrading:
    def test_margin_trading_not_queried_by_default(self):
        statuses = infer_evidence_statuses({})
        assert statuses["margin_trading"] == EvidenceStatus.NOT_QUERIED

    def test_margin_trading_has_data_from_raw_evidence(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": "600519 [DATA-010] MARGIN_HAS_DATA: 融资余额 185200万",
                    "status": "HAS_DATA",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.HAS_DATA

    def test_margin_trading_failed_from_raw_evidence(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": "600519 [DATA-010] MARGIN_FAILED: ConnectionError",
                    "status": "FAILED",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.QUERY_FAILED

    def test_margin_trading_normal_no_data_from_raw_evidence(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": "600519 [DATA-010] MARGIN_NORMAL_NO_DATA: 非融资融券标的",
                    "status": "NORMAL_NO_DATA",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.NORMAL_NO_DATA

    def test_margin_trading_not_queried_from_raw_evidence(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": None,
                    "status": "NOT_QUERIED",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.NOT_QUERIED

    def test_margin_trading_coverage_increases_with_has_data(self):
        base = {
            "ohlcv_5d": EvidenceStatus.HAS_DATA,
            "volume": EvidenceStatus.HAS_DATA,
            "turnover_rate": EvidenceStatus.NOT_AVAILABLE,
            "volume_ratio": EvidenceStatus.HAS_DATA,
            "individual_fund_flow": EvidenceStatus.HAS_DATA,
            "lhb_status": EvidenceStatus.NOT_QUERIED,
            "announcements": EvidenceStatus.HAS_DATA,
        }
        coverage_without = calculate_evidence_coverage(**base, margin_trading=EvidenceStatus.NOT_QUERIED)
        coverage_with = calculate_evidence_coverage(**base, margin_trading=EvidenceStatus.HAS_DATA)
        assert coverage_with > coverage_without

    def test_margin_trading_coverage_not_available_excluded(self):
        coverage = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.NOT_AVAILABLE,
            volume=EvidenceStatus.NOT_AVAILABLE,
            turnover_rate=EvidenceStatus.NOT_AVAILABLE,
            volume_ratio=EvidenceStatus.NOT_AVAILABLE,
            individual_fund_flow=EvidenceStatus.NOT_AVAILABLE,
            lhb_status=EvidenceStatus.NOT_AVAILABLE,
            margin_trading=EvidenceStatus.NOT_AVAILABLE,
            announcements=EvidenceStatus.NOT_AVAILABLE,
        )
        assert coverage == 0

    def test_margin_trading_long_text_has_data(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": "x" * 100,
                    "status": "HAS_DATA",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.HAS_DATA

    def test_margin_trading_unavailable_text(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": "600519 融资融券数据暂不可用。",
                    "status": "NORMAL_NO_DATA",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.NORMAL_NO_DATA


# ── Interface Route ────────────────────────────────────────────────────


class TestInterfaceMarginTrading:
    def test_get_margin_trading_in_tools_categories(self):
        assert "get_margin_trading" in TOOLS_CATEGORIES["cn_market_data"]["tools"]

    def test_category_for_margin_trading(self):
        assert get_category_for_method("get_margin_trading") == "cn_market_data"

    def test_route_to_vendor_margin_trading_has_method(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        assert hasattr(provider, "get_margin_trading")

    def test_route_fallback_detects_failure_string(self):
        from tradingagents.dataflows.interface import _is_failure_result
        assert _is_failure_result("融资融券数据获取失败：ConnectionError") is True
        assert _is_failure_result("600519 [DATA-010] MARGIN_FAILED: permission denied") is True
        assert _is_failure_result("600519 [DATA-010] MARGIN_HAS_DATA") is False


# ── Fixtures ───────────────────────────────────────────────────────────


class TestMarginTradingFixtures:
    def test_margin_fixture_ids_in_all_fixtures(self):
        assert FIXTURE_MARGIN_HAS_DATA in ALL_FIXTURE_IDS
        assert FIXTURE_MARGIN_FAILED in ALL_FIXTURE_IDS
        assert FIXTURE_MARGIN_NOT_QUERIED in ALL_FIXTURE_IDS

    def test_total_fixture_count_increased(self):
        assert len(ALL_FIXTURE_IDS) >= 16

    def test_margin_has_data_fixture_exists(self):
        f = get_fixture(FIXTURE_MARGIN_HAS_DATA)
        assert f is not None
        assert f.data_type == "margin_trading"
        assert f.expected_status == "HAS_DATA"
        assert "margin_trading" in f.raw_evidence

    def test_margin_failed_fixture_exists(self):
        f = get_fixture(FIXTURE_MARGIN_FAILED)
        assert f is not None
        assert f.expected_status == "FAILED"
        assert f.raw_evidence["margin_trading"]["status"] == "FAILED"
        assert f.raw_evidence["margin_trading"]["error"] is not None

    def test_margin_not_queried_fixture_exists(self):
        f = get_fixture(FIXTURE_MARGIN_NOT_QUERIED)
        assert f is not None
        assert f.expected_status == "NOT_QUERIED"
        assert f.raw_evidence["margin_trading"]["vendor"] == ""

    def test_margin_has_data_fixture_vendor(self):
        f = get_fixture(FIXTURE_MARGIN_HAS_DATA)
        contract = EvidenceContract.from_dict(f.raw_evidence["margin_trading"])
        assert contract.vendor == "cn_astock"
        assert contract.unit_verified is True
        assert contract.has_data is True

    def test_margin_has_data_fixture_unit(self):
        f = get_fixture(FIXTURE_MARGIN_HAS_DATA)
        contract = EvidenceContract.from_dict(f.raw_evidence["margin_trading"])
        assert contract.unit == "万元"

    def test_margin_failed_fixture_error(self):
        f = get_fixture(FIXTURE_MARGIN_FAILED)
        contract = EvidenceContract.from_dict(f.raw_evidence["margin_trading"])
        assert contract.error == "ConnectionError"
        assert contract.is_failed is True

    def test_margin_not_queried_fixture_has_no_vendor(self):
        f = get_fixture(FIXTURE_MARGIN_NOT_QUERIED)
        entry = f.raw_evidence["margin_trading"]
        assert entry["vendor"] == ""
        assert entry["endpoint"] == ""

    def test_all_margin_fixtures_in_get_all(self):
        all_fixtures = get_all_fixtures()
        fixture_ids = [f.fixture_id for f in all_fixtures]
        assert FIXTURE_MARGIN_HAS_DATA in fixture_ids
        assert FIXTURE_MARGIN_FAILED in fixture_ids
        assert FIXTURE_MARGIN_NOT_QUERIED in fixture_ids


# ── Fixture Replay ─────────────────────────────────────────────────────


class TestMarginTradingFixtureReplay:
    def test_replay_margin_has_data(self):
        f = get_fixture(FIXTURE_MARGIN_HAS_DATA)
        report = run_fixture_replay([FIXTURE_MARGIN_HAS_DATA])
        assert report.total_fixtures == 1
        result = report.results[0]
        assert result.passed is True
        assert result.actual_status == "HAS_DATA"

    def test_replay_margin_failed(self):
        report = run_fixture_replay([FIXTURE_MARGIN_FAILED])
        result = report.results[0]
        assert result.passed is True
        assert result.actual_status == "FAILED"

    def test_replay_margin_not_queried(self):
        report = run_fixture_replay([FIXTURE_MARGIN_NOT_QUERIED])
        result = report.results[0]
        assert result.passed is True
        assert result.actual_status == "NOT_QUERIED"

    def test_replay_all_margin_fixtures(self):
        margin_ids = [
            FIXTURE_MARGIN_HAS_DATA,
            FIXTURE_MARGIN_FAILED,
            FIXTURE_MARGIN_NOT_QUERIED,
        ]
        report = run_fixture_replay(margin_ids)
        assert report.total_fixtures == 3
        assert report.all_passed is True

    def test_replay_full_includes_margin(self):
        report = run_fixture_replay()
        margin_results = [r for r in report.results if r.data_type == "margin_trading"]
        assert len(margin_results) == 3


# ── Provider Methods ──────────────────────────────────────────────────


class TestProviderMarginTrading:
    def test_akshare_provider_has_method(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        assert hasattr(provider, "get_margin_trading")

    def test_astock_provider_has_method(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        assert hasattr(provider, "get_margin_trading")

    def test_akshare_margin_failed_returns_error(self):
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        with patch.object(provider, "_ak", side_effect=Exception("test error")):
            result = provider.get_margin_trading("600519.SH")
            assert "MARGIN_FAILED" in result

    def test_astock_margin_failed_returns_error(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter",
                    side_effect=Exception("ConnectionError")):
            result = provider.get_margin_trading("600519.SH")
            assert "MARGIN_FAILED" in result

    def test_astock_margin_normal_no_data(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        with patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter",
                    return_value=[]):
            result = provider.get_margin_trading("600519.SH")
            assert "MARGIN_NORMAL_NO_DATA" in result

    def test_astock_margin_has_data(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        mock_data = [
            {
                "TRADE_DATE": "2026-06-07",
                "RZRQ_YE": 1852000000,
                "RZRQ_MRE": 32000000,
                "RQYE": 15000000,
                "RQMRL": 500000,
                "RZRQ_JME": 31000000,
            }
        ]
        with patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter",
                    return_value=mock_data):
            result = provider.get_margin_trading("600519.SH")
            assert "MARGIN_HAS_DATA" in result
            assert "185200.0万" in result


# ── Acceptance ─────────────────────────────────────────────────────────


class TestAcceptanceDATA010:
    def test_01_margin_trading_source_catalog_has_primary(self):
        primary = get_primary_source(DataType.MARGIN_TRADING)
        assert primary is not None
        assert primary.is_primary is True

    def test_02_margin_trading_evidence_contract_complete(self):
        contract = EvidenceContract(
            field="margin_trading",
            value="test",
            unit="万元",
            vendor="cn_astock",
            endpoint="RPT_RZRQ_LSHJ",
            as_of="2026-06-07",
            status="HAS_DATA",
        )
        assert contract.has_data
        assert contract.vendor != ""
        assert contract.unit_known

    def test_03_margin_trading_not_queried_clear(self):
        contract = EvidenceContract(
            field="margin_trading",
            status="NOT_QUERIED",
        )
        assert contract.status == "NOT_QUERIED"
        assert contract.has_data is False

    def test_04_margin_trading_failed_not_mistaken_for_data(self):
        contract = EvidenceContract(
            field="margin_trading",
            status="FAILED",
            error="ConnectionError",
        )
        assert contract.is_failed
        assert not contract.has_data

    def test_05_margin_trading_has_data_in_raw_evidence(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": "600519 [DATA-010] MARGIN_HAS_DATA",
                    "status": "HAS_DATA",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.HAS_DATA

    def test_06_margin_trading_failed_in_raw_evidence(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": "600519 [DATA-010] MARGIN_FAILED",
                    "status": "FAILED",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.QUERY_FAILED

    def test_07_margin_trading_normal_no_data(self):
        statuses = infer_evidence_statuses(
            {},
            raw_evidence={
                "margin_trading": {
                    "raw": "600519 [DATA-010] MARGIN_NORMAL_NO_DATA",
                    "status": "NORMAL_NO_DATA",
                }
            },
        )
        assert statuses["margin_trading"] == EvidenceStatus.NORMAL_NO_DATA

    def test_08_margin_trading_not_queried_default(self):
        statuses = infer_evidence_statuses({})
        assert statuses["margin_trading"] == EvidenceStatus.NOT_QUERIED

    def test_09_margin_trading_coverage_not_inflated_by_missing(self):
        coverage = calculate_evidence_coverage(
            margin_trading=EvidenceStatus.NOT_QUERIED,
        )
        assert coverage == 0

    def test_10_all_margin_fixtures_replay(self):
        margin_ids = [FIXTURE_MARGIN_HAS_DATA, FIXTURE_MARGIN_FAILED, FIXTURE_MARGIN_NOT_QUERIED]
        report = run_fixture_replay(margin_ids)
        assert report.all_passed is True
