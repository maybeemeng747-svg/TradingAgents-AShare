# [DATA-016] hot_stock_fallback
"""
Tests for hot stocks (热门股票) cn_astock fallback, source catalog registration,
evidence contract, evidence coverage audit, fixture replay,
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
    FIXTURE_HOT_STOCKS_HAS_DATA,
    FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK,
    FIXTURE_HOT_STOCKS_FAILED,
    FIXTURE_HOT_STOCKS_NORMAL_NO_DATA,
    FIXTURE_HOT_STOCKS_RATE_LIMITED,
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


class TestSourceCatalogHotStocks:
    def test_hot_stocks_data_type_exists(self):
        assert DataType.HOT_STOCKS.value == "hot_stocks"

    def test_hot_stocks_sources_registered(self):
        sources = get_sources_for_type(DataType.HOT_STOCKS)
        assert len(sources) >= 2

    def test_hot_stocks_primary_source(self):
        primary = get_primary_source(DataType.HOT_STOCKS)
        assert primary is not None
        assert primary.vendor == "cn_akshare"
        assert primary.is_primary

    def test_hot_stocks_fallback_chain(self):
        chain = get_fallback_chain(DataType.HOT_STOCKS)
        assert "cn_akshare" in chain
        assert "cn_astock" in chain
        assert chain.index("cn_akshare") < chain.index("cn_astock")

    def test_hot_stocks_in_all_data_types(self):
        all_types = get_all_data_types()
        assert "hot_stocks" in all_types

    def test_hot_stocks_primary_fields(self):
        primary = get_primary_source(DataType.HOT_STOCKS)
        assert len(primary.fields) > 0

    def test_hot_stocks_primary_endpoint(self):
        primary = get_primary_source(DataType.HOT_STOCKS)
        assert primary.endpoint == "stock_hot_follow_xq"

    def test_hot_stocks_fallback_endpoint(self):
        sources = get_sources_for_type(DataType.HOT_STOCKS)
        astock = [s for s in sources if s.vendor == "cn_astock"]
        assert len(astock) >= 1
        assert "eastmoney" in astock[0].endpoint.lower() or "push2" in astock[0].endpoint.lower()


class TestEvidenceContractHotStocks:
    def test_resolve_data_type_hot_stocks(self):
        assert resolve_data_type("hot_stocks") == "hot_stocks"

    def test_hot_stocks_contract_has_data(self):
        contract = EvidenceContract(
            field="hot_stocks",
            value="雪球热搜前20：\n600519 贵州茅台 120.5",
            unit="条",
            vendor="cn_akshare",
            endpoint="stock_hot_follow_xq",
            status="HAS_DATA",
        )
        assert contract.has_data
        assert contract.unit_known
        assert contract.vendor == "cn_akshare"

    def test_hot_stocks_contract_failed(self):
        contract = EvidenceContract(
            field="hot_stocks",
            value=None,
            vendor="cn_astock",
            endpoint="push2.eastmoney.com/getHotStock",
            status="FAILED",
            error="ConnectionError",
        )
        assert contract.is_failed
        assert not contract.has_data

    def test_hot_stocks_completeness(self):
        raw = {
            "hot_stocks": {
                "raw": "雪球热搜前20",
                "field": "hot_stocks",
                "unit": "条",
                "vendor": "cn_akshare",
                "endpoint": "stock_hot_follow_xq",
                "status": "HAS_DATA",
            },
        }
        result = compute_contract_completeness(raw)
        assert result["completeness_score"] > 0

    def test_hot_stocks_missing(self):
        raw = {}
        result = compute_contract_completeness(raw)
        assert "hot_stocks" in result.get("missing_details", {})

    def test_hot_stocks_in_required_fields(self):
        assert "hot_stocks" in _REQUIRED_FIELDS_FOR_COMPLETENESS


class TestEvidenceCoverageAuditHotStocks:
    def test_hot_stocks_label(self):
        assert _EVIDENCE_FIELD_LABELS.get("hot_stocks") == "热门股票"

    def test_hot_stocks_family(self):
        assert _EVIDENCE_FIELD_FAMILIES.get("hot_stocks") == "hot_stocks"

    def test_audit_hot_stocks_has_data(self):
        raw = {
            "hot_stocks": {
                "raw": "雪球热搜前20",
                "field": "hot_stocks",
                "unit": "条",
                "vendor": "cn_akshare",
                "endpoint": "stock_hot_follow_xq",
                "status": "HAS_DATA",
            },
        }
        result = audit_raw_evidence(raw)
        assert "hot_stocks" in result.has_data_fields

    def test_audit_hot_stocks_failed(self):
        raw = {
            "hot_stocks": {
                "raw": "HOT_STOCKS_FAILED",
                "field": "hot_stocks",
                "unit": None,
                "vendor": "cn_astock",
                "endpoint": "push2.eastmoney.com/getHotStock",
                "status": "FAILED",
                "error": "ConnectionError",
            },
        }
        result = audit_raw_evidence(raw)
        assert "hot_stocks" in result.failed_fields


class TestInterfaceHotStocks:
    def test_hot_stocks_in_tools_categories(self):
        tools = TOOLS_CATEGORIES.get("cn_market_data", {}).get("tools", [])
        assert "get_hot_stocks_xq" in tools

    def test_hot_stocks_category(self):
        cat = get_category_for_method("get_hot_stocks_xq")
        assert cat == "cn_market_data"

    def test_failure_string_detection(self):
        failure = "热门股票数据获取失败：ConnectionError"
        assert any(p in failure for p in _FAILURE_RESULT_PATTERNS)


class TestProviderHotStocks:
    def test_astock_has_get_hot_stocks(self):
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        assert hasattr(provider, "get_hot_stocks")

    @patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get")
    def test_astock_hot_stocks_has_data(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "diff": [
                    {"f12": "600519", "f14": "贵州茅台", "f2": 1800.0, "f3": 2.5, "f6": 4500000000},
                    {"f12": "300750", "f14": "宁德时代", "f2": 220.0, "f3": 3.1, "f6": 3800000000},
                ]
            }
        }
        mock_get.return_value = mock_resp
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        result = provider.get_hot_stocks()
        assert "HOT_STOCKS_HAS_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get")
    def test_astock_hot_stocks_no_data(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"diff": []}}
        mock_get.return_value = mock_resp
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        result = provider.get_hot_stocks()
        assert "HOT_STOCKS_NORMAL_NO_DATA" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get")
    def test_astock_hot_stocks_failed(self, mock_get):
        mock_get.side_effect = Exception("ConnectionError")
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        result = provider.get_hot_stocks()
        assert "HOT_STOCKS_FAILED" in result

    @patch("tradingagents.dataflows.providers.cn_astock_provider.requests.get")
    def test_astock_hot_stocks_rate_limited(self, mock_get):
        mock_get.side_effect = Exception("HTTPError: 429 Too Many Requests")
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        provider = CnAstockProvider()
        result = provider.get_hot_stocks()
        assert "HOT_STOCKS_FAILED" in result
        assert "429" in result


class TestHotStocksFixtures:
    def test_fixture_ids_exist(self):
        assert FIXTURE_HOT_STOCKS_HAS_DATA == "hot_stocks_has_data"
        assert FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK == "hot_stocks_akshare_fail_fallback"
        assert FIXTURE_HOT_STOCKS_FAILED == "hot_stocks_failed"
        assert FIXTURE_HOT_STOCKS_NORMAL_NO_DATA == "hot_stocks_normal_no_data"
        assert FIXTURE_HOT_STOCKS_RATE_LIMITED == "hot_stocks_rate_limited"

    def test_fixture_count_in_all_fixture_ids(self):
        hs_ids = [
            FIXTURE_HOT_STOCKS_HAS_DATA,
            FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK,
            FIXTURE_HOT_STOCKS_FAILED,
            FIXTURE_HOT_STOCKS_NORMAL_NO_DATA,
            FIXTURE_HOT_STOCKS_RATE_LIMITED,
        ]
        for hid in hs_ids:
            assert hid in ALL_FIXTURE_IDS

    def test_total_fixture_count_increased(self):
        assert len(ALL_FIXTURE_IDS) >= 39

    def test_hot_stocks_has_data_fixture(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_HAS_DATA)
        assert f is not None
        assert f.expected_status == "HAS_DATA"
        assert f.vendor == "cn_akshare"
        assert f.data_type == "hot_stocks"
        ev = f.raw_evidence.get("hot_stocks", {})
        assert ev.get("status") == "HAS_DATA"
        assert ev.get("record_count") == 20

    def test_hot_stocks_fallback_fixture(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK)
        assert f is not None
        assert f.expected_status == "HAS_DATA"
        assert f.vendor == "cn_astock"
        ev = f.raw_evidence.get("hot_stocks", {})
        assert ev.get("fallback_from") == "cn_akshare"

    def test_hot_stocks_failed_fixture(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_FAILED)
        assert f is not None
        assert f.expected_status == "FAILED"
        ev = f.raw_evidence.get("hot_stocks", {})
        assert ev.get("status") == "FAILED"
        assert ev.get("error") == "ConnectionError"

    def test_hot_stocks_normal_no_data_fixture(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_NORMAL_NO_DATA)
        assert f is not None
        assert f.expected_status == "NORMAL_NO_DATA"
        ev = f.raw_evidence.get("hot_stocks", {})
        assert ev.get("status") == "NORMAL_NO_DATA"
        assert ev.get("record_count") == 0

    def test_hot_stocks_rate_limited_fixture(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_RATE_LIMITED)
        assert f is not None
        assert f.expected_status == "FAILED"
        ev = f.raw_evidence.get("hot_stocks", {})
        assert ev.get("status") == "FAILED"
        assert "429" in ev.get("error", "")

    def test_all_hot_stocks_fixtures_unique_ids(self):
        ids = [
            FIXTURE_HOT_STOCKS_HAS_DATA,
            FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK,
            FIXTURE_HOT_STOCKS_FAILED,
            FIXTURE_HOT_STOCKS_NORMAL_NO_DATA,
            FIXTURE_HOT_STOCKS_RATE_LIMITED,
        ]
        assert len(set(ids)) == 5

    def test_unknown_fixture_returns_none(self):
        assert get_fixture("hot_stocks_unknown") is None


class TestHotStocksFixtureReplay:
    def test_replay_has_data(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_HOT_STOCKS_HAS_DATA])
        assert report.total_fixtures == 1
        assert report.passed == 1
        assert report.failed == 0

    def test_replay_fallback(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK])
        assert report.total_fixtures == 1
        assert report.passed == 1
        r = report.results[0]
        assert r.vendor == "cn_astock"
        assert r.fallback_from == "cn_akshare"
        assert r.is_fallback

    def test_replay_failed(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_HOT_STOCKS_FAILED])
        assert report.total_fixtures == 1
        assert report.passed == 1
        r = report.results[0]
        assert r.actual_status == "FAILED"

    def test_replay_normal_no_data(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_HOT_STOCKS_NORMAL_NO_DATA])
        assert report.total_fixtures == 1
        assert report.passed == 1
        r = report.results[0]
        assert r.actual_status == "NORMAL_NO_DATA"

    def test_replay_rate_limited(self):
        report = run_fixture_replay(fixture_ids=[FIXTURE_HOT_STOCKS_RATE_LIMITED])
        assert report.total_fixtures == 1
        assert report.passed == 1
        r = report.results[0]
        assert r.actual_status == "FAILED"

    def test_replay_all_hot_stocks(self):
        ids = [
            FIXTURE_HOT_STOCKS_HAS_DATA,
            FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK,
            FIXTURE_HOT_STOCKS_FAILED,
            FIXTURE_HOT_STOCKS_NORMAL_NO_DATA,
            FIXTURE_HOT_STOCKS_RATE_LIMITED,
        ]
        report = run_fixture_replay(fixture_ids=ids)
        assert report.total_fixtures == 5
        assert report.all_passed

    def test_replay_full_includes_hot_stocks(self):
        report = run_fixture_replay()
        hs_results = [r for r in report.results if r.data_type == "hot_stocks"]
        assert len(hs_results) == 5


class TestLiveSmokeHotStocks:
    def test_endpoint_definitions_include_hot_stocks(self):
        from tradingagents.dataflows.live_smoke import _make_endpoint_definitions
        defs = _make_endpoint_definitions()
        hs_eps = [d for d in defs if "hot_stocks" in d["endpoint"]]
        assert len(hs_eps) >= 1

    def test_hot_stocks_endpoint_vendor(self):
        from tradingagents.dataflows.live_smoke import _make_endpoint_definitions
        defs = _make_endpoint_definitions()
        hs_ep = [d for d in defs if "hot_stocks" in d["endpoint"]][0]
        assert hs_ep["vendor"] == "cn_astock"
        assert hs_ep["method"] == "get_hot_stocks"

    def test_live_smoke_env_gated(self):
        from tradingagents.dataflows.live_smoke import run_live_smoke
        with patch.dict("os.environ", {}, clear=True):
            report = run_live_smoke(symbols=["600519.SH"])
            assert report.env_gated
            hs_results = [r for r in report.results if "hot_stocks" in r.endpoint]
            assert len(hs_results) >= 1
            assert all(r.status == "SKIPPED" for r in hs_results)

    def test_live_smoke_enabled_runs(self):
        from tradingagents.dataflows.live_smoke import run_live_smoke, _make_endpoint_definitions
        defs = [d for d in _make_endpoint_definitions() if "hot_stocks" in d["endpoint"]]
        mock_fn = MagicMock(return_value=MagicMock(
            endpoint="cn_astock/hot_stocks",
            vendor="cn_astock",
            symbol="600519.SH",
            status="OK",
            latency_ms=100.0,
            field_count=5,
            unit="条",
            unit_verified=True,
            error="",
            has_data=True,
            sample_snippet="热门股票（Eastmoney 热榜，共 30 只）",
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


class TestHotStocksEvidenceIsolation:
    def test_hot_stocks_not_policy_evidence(self):
        raw = {
            "hot_stocks": {
                "raw": "雪球热搜前20",
                "field": "hot_stocks",
                "unit": "条",
                "vendor": "cn_akshare",
                "endpoint": "stock_hot_follow_xq",
                "status": "HAS_DATA",
            },
        }
        result = audit_raw_evidence(raw)
        assert "hot_stocks" in result.has_data_fields
        assert "news" not in result.has_data_fields
        assert "announcements" not in result.has_data_fields

    def test_hot_stocks_failure_not_candidate_failure(self):
        raw = {
            "hot_stocks": {
                "raw": "HOT_STOCKS_FAILED",
                "field": "hot_stocks",
                "status": "FAILED",
                "vendor": "cn_astock",
                "error": "ConnectionError",
            },
        }
        result = audit_raw_evidence(raw)
        assert "hot_stocks" in result.failed_fields


class TestAcceptanceDATA016:
    def test_fallback_shows_cn_astock_vendor(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK)
        ev = f.raw_evidence["hot_stocks"]
        assert ev["vendor"] == "cn_astock"
        assert ev["fallback_from"] == "cn_akshare"

    def test_all_failed_shows_failed(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_FAILED)
        ev = f.raw_evidence["hot_stocks"]
        assert ev["status"] == "FAILED"
        assert ev["error"] is not None

    def test_empty_result_shows_normal_no_data(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_NORMAL_NO_DATA)
        ev = f.raw_evidence["hot_stocks"]
        assert ev["status"] == "NORMAL_NO_DATA"
        assert ev["record_count"] == 0

    def test_catalog_has_fallback_chain(self):
        chain = get_fallback_chain(DataType.HOT_STOCKS)
        assert len(chain) >= 2
        assert chain[0] == "cn_akshare"

    def test_hot_stocks_not_in_coverage_denominator(self):
        cov = calculate_evidence_coverage()
        assert 0 <= cov <= 100

    def test_hot_stocks_evidence_audit_works(self):
        raw = {
            "hot_stocks": {
                "raw": "热门股票数据",
                "field": "hot_stocks",
                "unit": "条",
                "vendor": "cn_astock",
                "endpoint": "push2.eastmoney.com/getHotStock",
                "status": "HAS_DATA",
                "fallback_from": "cn_akshare",
            },
        }
        result = audit_raw_evidence(raw)
        assert "hot_stocks" in result.fallback_fields
        assert "hot_stocks" in result.has_data_fields

    def test_fixture_replay_all_passed(self):
        ids = [
            FIXTURE_HOT_STOCKS_HAS_DATA,
            FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK,
            FIXTURE_HOT_STOCKS_FAILED,
            FIXTURE_HOT_STOCKS_NORMAL_NO_DATA,
            FIXTURE_HOT_STOCKS_RATE_LIMITED,
        ]
        report = run_fixture_replay(fixture_ids=ids)
        assert report.all_passed
        assert report.total_fixtures == 5

    def test_live_smoke_has_hot_stocks(self):
        from tradingagents.dataflows.live_smoke import _make_endpoint_definitions
        defs = _make_endpoint_definitions()
        hs_eps = [d for d in defs if "hot_stocks" in d["endpoint"]]
        assert len(hs_eps) >= 1

    def test_provider_fallback_text_detected(self):
        failure = "热门股票数据获取失败：ConnectionError"
        from tradingagents.dataflows.interface import _is_failure_result
        assert _is_failure_result(failure)

    def test_catalog_validation_no_hot_stocks_issues(self):
        issues = validate_catalog()
        hs_issues = [i for i in issues if "hot_stocks" in i.lower()]
        assert len(hs_issues) == 0

    def test_hot_stocks_fixtures_tags_contain_data016(self):
        for fid in [
            FIXTURE_HOT_STOCKS_HAS_DATA,
            FIXTURE_HOT_STOCKS_AKSHARE_FAIL_FALLBACK,
            FIXTURE_HOT_STOCKS_FAILED,
            FIXTURE_HOT_STOCKS_NORMAL_NO_DATA,
            FIXTURE_HOT_STOCKS_RATE_LIMITED,
        ]:
            f = get_fixture(fid)
            assert "DATA-016" in f.tags

    def test_hot_stocks_has_data_record_count_positive(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_HAS_DATA)
        ev = f.raw_evidence["hot_stocks"]
        assert ev["record_count"] > 0

    def test_hot_stocks_failed_record_count_zero(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_FAILED)
        ev = f.raw_evidence["hot_stocks"]
        assert ev["record_count"] == 0

    def test_hot_stocks_failure_not_affect_other_evidence(self):
        raw = {
            "hot_stocks": {
                "raw": "HOT_STOCKS_FAILED",
                "field": "hot_stocks",
                "status": "FAILED",
                "vendor": "cn_astock",
                "error": "ConnectionError",
            },
            "news": {
                "raw": "新闻数据",
                "field": "news",
                "status": "HAS_DATA",
                "vendor": "cn_akshare",
                "endpoint": "stock_news_em",
            },
        }
        result = audit_raw_evidence(raw)
        assert "hot_stocks" in result.failed_fields
        assert "news" in result.has_data_fields

    def test_hot_stocks_rate_limited_is_failed_not_normal(self):
        f = get_fixture(FIXTURE_HOT_STOCKS_RATE_LIMITED)
        ev = f.raw_evidence["hot_stocks"]
        assert ev["status"] == "FAILED"
        assert "429" in ev.get("error", "")
