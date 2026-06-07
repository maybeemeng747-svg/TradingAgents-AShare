# [DATA-015] limit_up_pool_fallback
"""
Tests for ZT pool (涨停池) cn_astock fallback, source catalog registration,
evidence contract, evidence coverage audit, readiness score, fixture replay,
provider, live smoke, and acceptance criteria.
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
    calculate_evidence_coverage,
)
from tradingagents.dataflows.fixture_replay import (
    FIXTURE_ZT_POOL_HAS_DATA,
    FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK,
    FIXTURE_ZT_POOL_FAILED,
    FIXTURE_ZT_POOL_NORMAL_NO_DATA,
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


class TestSourceCatalogZtPool:
    def test_zt_pool_data_type_exists(self):
        assert DataType.ZT_POOL.value == "zt_pool"

    def test_zt_pool_sources_registered(self):
        sources = get_sources_for_type(DataType.ZT_POOL)
        assert len(sources) >= 2

    def test_zt_pool_primary_source(self):
        primary = get_primary_source(DataType.ZT_POOL)
        assert primary is not None
        assert primary.vendor == "cn_akshare"
        assert primary.is_primary

    def test_zt_pool_fallback_chain(self):
        chain = get_fallback_chain(DataType.ZT_POOL)
        assert "cn_akshare" in chain
        assert "cn_astock" in chain
        assert chain.index("cn_akshare") < chain.index("cn_astock")

    def test_zt_pool_in_all_data_types(self):
        all_types = get_all_data_types()
        assert "zt_pool" in all_types

    def test_zt_pool_primary_fields(self):
        primary = get_primary_source(DataType.ZT_POOL)
        assert len(primary.fields) > 0

    def test_zt_pool_primary_endpoint(self):
        primary = get_primary_source(DataType.ZT_POOL)
        assert primary.endpoint == "stock_zt_pool_em"

    def test_zt_pool_fallback_endpoint(self):
        sources = get_sources_for_type(DataType.ZT_POOL)
        astock = [s for s in sources if s.vendor == "cn_astock"]
        assert len(astock) >= 1
        assert "eastmoney" in astock[0].endpoint.lower() or "push2" in astock[0].endpoint.lower()


class TestEvidenceContractZtPool:
    def test_resolve_data_type_zt_pool(self):
        assert resolve_data_type("zt_pool") == "zt_pool"

    def test_zt_pool_contract_has_data(self):
        contract = EvidenceContract(
            field="zt_pool",
            value="2026-06-08 涨停家数：35",
            unit="条",
            vendor="cn_akshare",
            endpoint="stock_zt_pool_em",
            status="HAS_DATA",
        )
        assert contract.has_data
        assert contract.unit_known
        assert contract.vendor == "cn_akshare"

    def test_zt_pool_contract_failed(self):
        contract = EvidenceContract(
            field="zt_pool",
            value=None,
            vendor="cn_astock",
            endpoint="push2his.eastmoney.com/zt",
            status="FAILED",
            error="ConnectionError",
        )
        assert contract.is_failed
        assert not contract.has_data

    def test_zt_pool_completeness(self):
        raw = {
            "zt_pool": {
                "raw": "2026-06-08 涨停家数：35",
                "field": "zt_pool",
                "unit": "条",
                "vendor": "cn_akshare",
                "endpoint": "stock_zt_pool_em",
                "status": "HAS_DATA",
            },
        }
        result = compute_contract_completeness(raw)
        assert result["completeness_score"] > 0

    def test_zt_pool_missing(self):
        raw = {}
        result = compute_contract_completeness(raw)
        assert "zt_pool" in result.get("missing_details", {})

    def test_zt_pool_in_required_fields(self):
        assert "zt_pool" in _REQUIRED_FIELDS_FOR_COMPLETENESS


class TestEvidenceCoverageAuditZtPool:
    def test_zt_pool_label(self):
        assert _EVIDENCE_FIELD_LABELS.get("zt_pool") == "涨停池"

    def test_zt_pool_family(self):
        assert _EVIDENCE_FIELD_FAMILIES.get("zt_pool") == "zt_pool"

    def test_audit_zt_pool_has_data(self):
        raw = {
            "zt_pool": {
                "raw": "2026-06-08 涨停家数：35",
                "field": "zt_pool",
                "unit": "条",
                "vendor": "cn_akshare",
                "endpoint": "stock_zt_pool_em",
                "status": "HAS_DATA",
            },
        }
        result = audit_raw_evidence(raw)
        assert "zt_pool" in result.has_data_fields

    def test_audit_zt_pool_failed(self):
        raw = {
            "zt_pool": {
                "raw": "ZT_POOL_FAILED",
                "field": "zt_pool",
                "unit": None,
                "vendor": "cn_astock",
                "endpoint": "push2his.eastmoney.com/zt",
                "status": "FAILED",
                "error": "ConnectionError",
            },
        }
        result = audit_raw_evidence(raw)
        assert "zt_pool" in result.failed_fields


class TestInterfaceZtPool:
    def test_zt_pool_in_tools_categories(self):
        tools = TOOLS_CATEGORIES.get("cn_market_data", {}).get("tools", [])
        assert "get_zt_pool" in tools

    def test_zt_pool_category(self):
        cat = get_category_for_method("get_zt_pool")
        assert cat == "cn_market_data"

    def test_failure_string_detection(self):
        failure = "涨停板情绪池数据获取失败：ConnectionError"
        assert any(p in failure for p in _FAILURE_RESULT_PATTERNS)


class TestProviderZtPool:
    def test_astock_has_get_zt_pool(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        assert hasattr(provider, "get_zt_pool")

    @patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter")
    def test_astock_zt_pool_has_data(self, mock_dc):
        mock_dc.return_value = [
            {"SECURITY_CODE": "601678", "SECURITY_NAME_ABBR": "拓普集团",
             "EXPLANATION": "涨停", "CHANGE_RATE": 10.0},
        ]
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        result = provider.get_zt_pool("2026-06-08")
        assert "ZT_POOL_HAS_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter")
    def test_astock_zt_pool_no_data(self, mock_dc):
        mock_dc.return_value = []
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": {"pool": []}}
            mock_get.return_value = mock_resp
            from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
            provider = CnAstockProvider()
            result = provider.get_zt_pool("2026-06-08")
            assert "ZT_POOL_NORMAL_NO_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter")
    def test_astock_zt_pool_failed(self, mock_dc):
        mock_dc.side_effect = Exception("ConnectionError")
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        result = provider.get_zt_pool("2026-06-08")
        assert "ZT_POOL_FAILED" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter")
    def test_astock_zt_pool_fallback_has_data(self, mock_dc):
        mock_dc.return_value = []
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "data": {
                    "pool": [
                        {"c": "601678", "n": "拓普集团", "zdp": "10.0", "lbc": "1"},
                        {"c": "300750", "n": "宁德时代", "zdp": "20.0", "lbc": "2"},
                    ]
                }
            }
            mock_get.return_value = mock_resp
            from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
            provider = CnAstockProvider()
            result = provider.get_zt_pool("2026-06-08")
            assert "ZT_POOL_HAS_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider._eastmoney_datacenter")
    def test_astock_zt_pool_push2_fallback_failed(self, mock_dc):
        mock_dc.return_value = []
        with patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get") as mock_get:
            mock_get.side_effect = Exception("ConnectionError")
            from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
            provider = CnAstockProvider()
            result = provider.get_zt_pool("2026-06-08")
            assert "ZT_POOL_FAILED" in result


class TestZtPoolFixtures:
    def test_fixture_ids_exist(self):
        assert FIXTURE_ZT_POOL_HAS_DATA == "zt_pool_has_data"
        assert FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK == "zt_pool_akshare_fail_fallback"
        assert FIXTURE_ZT_POOL_FAILED == "zt_pool_failed"
        assert FIXTURE_ZT_POOL_NORMAL_NO_DATA == "zt_pool_normal_no_data"

    def test_fixture_count_in_all_fixture_ids(self):
        zt_ids = [
            FIXTURE_ZT_POOL_HAS_DATA,
            FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK,
            FIXTURE_ZT_POOL_FAILED,
            FIXTURE_ZT_POOL_NORMAL_NO_DATA,
        ]
        for zid in zt_ids:
            assert zid in ALL_FIXTURE_IDS

    def test_total_fixture_count_increased(self):
        assert len(ALL_FIXTURE_IDS) >= 34

    def test_zt_pool_has_data_fixture(self):
        f = get_fixture(FIXTURE_ZT_POOL_HAS_DATA)
        assert f is not None
        assert f.expected_status == "HAS_DATA"
        assert f.vendor == "cn_akshare"
        assert f.data_type == "zt_pool"
        ev = f.raw_evidence.get("zt_pool", {})
        assert ev.get("status") == "HAS_DATA"
        assert ev.get("record_count") == 35

    def test_zt_pool_fallback_fixture(self):
        f = get_fixture(FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK)
        assert f is not None
        assert f.expected_status == "HAS_DATA"
        assert f.vendor == "cn_astock"
        ev = f.raw_evidence.get("zt_pool", {})
        assert ev.get("fallback_from") == "cn_akshare"

    def test_zt_pool_failed_fixture(self):
        f = get_fixture(FIXTURE_ZT_POOL_FAILED)
        assert f is not None
        assert f.expected_status == "FAILED"
        ev = f.raw_evidence.get("zt_pool", {})
        assert ev.get("status") == "FAILED"
        assert ev.get("error") == "ConnectionError"

    def test_zt_pool_normal_no_data_fixture(self):
        f = get_fixture(FIXTURE_ZT_POOL_NORMAL_NO_DATA)
        assert f is not None
        assert f.expected_status == "NORMAL_NO_DATA"
        ev = f.raw_evidence.get("zt_pool", {})
        assert ev.get("status") == "NORMAL_NO_DATA"
        assert ev.get("record_count") == 0

    def test_all_zt_pool_fixtures_unique_ids(self):
        ids = [
            FIXTURE_ZT_POOL_HAS_DATA,
            FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK,
            FIXTURE_ZT_POOL_FAILED,
            FIXTURE_ZT_POOL_NORMAL_NO_DATA,
        ]
        assert len(set(ids)) == 4

    def test_unknown_fixture_returns_none(self):
        assert get_fixture("zt_pool_unknown") is None


class TestZtPoolFixtureReplay:
    def test_replay_has_data(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_ZT_POOL_HAS_DATA])
        assert report.total_fixtures == 1
        assert report.passed == 1
        assert report.failed == 0

    def test_replay_fallback(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK])
        assert report.total_fixtures == 1
        assert report.passed == 1
        r = report.results[0]
        assert r.vendor == "cn_astock"
        assert r.fallback_from == "cn_akshare"
        assert r.is_fallback

    def test_replay_failed(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_ZT_POOL_FAILED])
        assert report.total_fixtures == 1
        assert report.passed == 1
        r = report.results[0]
        assert r.actual_status == "FAILED"

    def test_replay_normal_no_data(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_ZT_POOL_NORMAL_NO_DATA])
        assert report.total_fixtures == 1
        assert report.passed == 1
        r = report.results[0]
        assert r.actual_status == "NORMAL_NO_DATA"

    def test_replay_all_zt_pool(self):
        ids = [
            FIXTURE_ZT_POOL_HAS_DATA,
            FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK,
            FIXTURE_ZT_POOL_FAILED,
            FIXTURE_ZT_POOL_NORMAL_NO_DATA,
        ]
        report = run_fixture_replay(fixture_ids=ids)
        assert report.total_fixtures == 4
        assert report.all_passed

    def test_replay_full_includes_zt_pool(self):
        report = run_fixture_replay()
        zt_results = [r for r in report.results if r.data_type == "zt_pool"]
        assert len(zt_results) == 4


class TestLiveSmokeZtPool:
    def test_endpoint_definitions_include_zt_pool(self):
        from tradingagents.dataflows.live_smoke import _make_endpoint_definitions
        defs = _make_endpoint_definitions()
        zt_eps = [d for d in defs if "zt_pool" in d["endpoint"]]
        assert len(zt_eps) >= 1

    def test_zt_pool_endpoint_vendor(self):
        from tradingagents.dataflows.live_smoke import _make_endpoint_definitions
        defs = _make_endpoint_definitions()
        zt_ep = [d for d in defs if "zt_pool" in d["endpoint"]][0]
        assert zt_ep["vendor"] == "cn_astock"
        assert zt_ep["method"] == "get_zt_pool"

    def test_live_smoke_env_gated(self):
        from tradingagents.dataflows.live_smoke import run_live_smoke
        with patch.dict("os.environ", {}, clear=True):
            report = run_live_smoke(symbols=["600519.SH"])
            assert report.env_gated
            zt_results = [r for r in report.results if "zt_pool" in r.endpoint]
            assert len(zt_results) >= 1
            assert all(r.status == "SKIPPED" for r in zt_results)

    def test_live_smoke_enabled_runs(self):
        from tradingagents.dataflows.live_smoke import run_live_smoke, _make_endpoint_definitions
        defs = [d for d in _make_endpoint_definitions() if "zt_pool" in d["endpoint"]]
        mock_fn = MagicMock(return_value=MagicMock(
            endpoint="cn_astock/zt_pool",
            vendor="cn_astock",
            symbol="600519.SH",
            status="OK",
            latency_ms=100.0,
            field_count=5,
            unit="条",
            unit_verified=True,
            error="",
            has_data=True,
            sample_snippet="涨停家数：35",
            rate_limit_ok=True,
        ))
        with patch.dict("os.environ", {"TA_LIVE_DATA_SMOKE": "1"}):
            report = run_live_smoke(
                symbols=["600519.SH"],
                endpoints=defs,
                smoke_fn=mock_fn,
            )
            assert not report.env_gated
            assert len(report.results) >= 1


class TestAcceptanceDATA015:
    def test_fallback_shows_cn_astock_vendor(self):
        f = get_fixture(FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK)
        ev = f.raw_evidence["zt_pool"]
        assert ev["vendor"] == "cn_astock"
        assert ev["fallback_from"] == "cn_akshare"

    def test_all_failed_shows_failed(self):
        f = get_fixture(FIXTURE_ZT_POOL_FAILED)
        ev = f.raw_evidence["zt_pool"]
        assert ev["status"] == "FAILED"
        assert ev["error"] is not None

    def test_empty_pool_shows_normal_no_data(self):
        f = get_fixture(FIXTURE_ZT_POOL_NORMAL_NO_DATA)
        ev = f.raw_evidence["zt_pool"]
        assert ev["status"] == "NORMAL_NO_DATA"
        assert ev["record_count"] == 0

    def test_catalog_has_fallback_chain(self):
        chain = get_fallback_chain(DataType.ZT_POOL)
        assert len(chain) >= 2
        assert chain[0] == "cn_akshare"

    def test_zt_pool_not_in_coverage_denominator(self):
        cov = calculate_evidence_coverage()
        assert 0 <= cov <= 100

    def test_zt_pool_evidence_audit_works(self):
        raw = {
            "zt_pool": {
                "raw": "涨停池数据",
                "field": "zt_pool",
                "unit": "条",
                "vendor": "cn_astock",
                "endpoint": "push2his.eastmoney.com/zt",
                "status": "HAS_DATA",
                "fallback_from": "cn_akshare",
            },
        }
        result = audit_raw_evidence(raw)
        assert "zt_pool" in result.fallback_fields
        assert "zt_pool" in result.has_data_fields

    def test_fixture_replay_all_passed(self):
        ids = [
            FIXTURE_ZT_POOL_HAS_DATA,
            FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK,
            FIXTURE_ZT_POOL_FAILED,
            FIXTURE_ZT_POOL_NORMAL_NO_DATA,
        ]
        report = run_fixture_replay(fixture_ids=ids)
        assert report.all_passed
        assert report.total_fixtures == 4

    def test_live_smoke_has_zt_pool(self):
        from tradingagents.dataflows.live_smoke import _make_endpoint_definitions
        defs = _make_endpoint_definitions()
        zt_eps = [d for d in defs if "zt_pool" in d["endpoint"]]
        assert len(zt_eps) >= 1

    def test_provider_fallback_text_detected(self):
        failure = "涨停板情绪池数据获取失败：ConnectionError"
        from tradingagents.dataflows.interface import _is_failure_result
        assert _is_failure_result(failure)

    def test_catalog_validation_no_zt_pool_issues(self):
        issues = validate_catalog()
        zt_issues = [i for i in issues if "zt_pool" in i.lower()]
        assert len(zt_issues) == 0

    def test_zt_pool_fixtures_tags_contain_data015(self):
        for fid in [
            FIXTURE_ZT_POOL_HAS_DATA,
            FIXTURE_ZT_POOL_AKSHARE_FAIL_FALLBACK,
            FIXTURE_ZT_POOL_FAILED,
            FIXTURE_ZT_POOL_NORMAL_NO_DATA,
        ]:
            f = get_fixture(fid)
            assert "DATA-015" in f.tags

    def test_zt_pool_has_data_record_count_positive(self):
        f = get_fixture(FIXTURE_ZT_POOL_HAS_DATA)
        ev = f.raw_evidence["zt_pool"]
        assert ev["record_count"] > 0

    def test_zt_pool_failed_record_count_zero(self):
        f = get_fixture(FIXTURE_ZT_POOL_FAILED)
        ev = f.raw_evidence["zt_pool"]
        assert ev["record_count"] == 0
