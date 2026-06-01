# [DATA-001] source_catalog tests

import pytest

from tradingagents.dataflows.source_catalog import (
    DataType,
    Freshness,
    RateLimitRisk,
    SourceCapability,
    get_all_data_types,
    get_catalog_summary,
    get_fallback_chain,
    get_primary_source,
    get_sources_for_type,
    get_vendor_capabilities,
    validate_catalog,
    _SOURCE_CATALOG,
)


class TestDataTypeEnum:
    def test_all_values(self):
        expected = {
            "quote", "ohlcv", "fund_flow", "board_fund_flow", "lhb",
            "margin_trading", "notice", "report", "rating", "news",
            "global_news", "financials", "insider", "hot_stocks",
            "zt_pool", "realtime_quotes",
        }
        assert {dt.value for dt in DataType} == expected

    def test_string_construct(self):
        assert DataType("quote") == DataType.QUOTE
        assert DataType("fund_flow") == DataType.FUND_FLOW

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            DataType("nonexistent")


class TestFreshnessEnum:
    def test_all_values(self):
        expected = {"realtime", "intraday", "daily", "delayed", "stale", "unknown"}
        assert {f.value for f in Freshness} == expected


class TestRateLimitRiskEnum:
    def test_all_values(self):
        expected = {"low", "medium", "high", "unknown"}
        assert {r.value for r in RateLimitRisk} == expected


class TestSourceCapability:
    def test_defaults(self):
        cap = SourceCapability(
            vendor="test", endpoint="ep", data_type=DataType.QUOTE,
        )
        assert cap.fields == []
        assert cap.unit == ""
        assert cap.freshness == Freshness.UNKNOWN
        assert cap.rate_limit_risk == RateLimitRisk.UNKNOWN
        assert cap.fallback_priority == 99
        assert cap.known_gaps == []
        assert cap.is_primary is False
        assert cap.notes == ""

    def test_to_dict(self):
        cap = SourceCapability(
            vendor="cn_akshare",
            endpoint="stock_zh_a_hist",
            data_type=DataType.OHLCV,
            fields=["Date", "Close"],
            unit="元",
            freshness=Freshness.DAILY,
            rate_limit_risk=RateLimitRisk.MEDIUM,
            fallback_priority=1,
            is_primary=True,
        )
        d = cap.to_dict()
        assert d["vendor"] == "cn_akshare"
        assert d["endpoint"] == "stock_zh_a_hist"
        assert d["data_type"] == "ohlcv"
        assert d["fields"] == ["Date", "Close"]
        assert d["unit"] == "元"
        assert d["freshness"] == "daily"
        assert d["rate_limit_risk"] == "medium"
        assert d["fallback_priority"] == 1
        assert d["is_primary"] is True
        assert d["known_gaps"] == []
        assert d["notes"] == ""

    def test_can_be_primary_all_good(self):
        cap = SourceCapability(
            vendor="v", endpoint="e", data_type=DataType.QUOTE,
            fields=["price"], unit="元", freshness=Freshness.REALTIME,
        )
        assert cap.can_be_primary is True

    def test_can_be_primary_no_fields(self):
        cap = SourceCapability(
            vendor="v", endpoint="e", data_type=DataType.QUOTE,
            fields=[], unit="元", freshness=Freshness.REALTIME,
        )
        assert cap.can_be_primary is False

    def test_can_be_primary_no_unit_for_quote(self):
        cap = SourceCapability(
            vendor="v", endpoint="e", data_type=DataType.QUOTE,
            fields=["price"], unit="", freshness=Freshness.REALTIME,
        )
        assert cap.can_be_primary is False

    def test_can_be_primary_no_unit_for_fund_flow(self):
        cap = SourceCapability(
            vendor="v", endpoint="e", data_type=DataType.FUND_FLOW,
            fields=["flow"], unit="", freshness=Freshness.DAILY,
        )
        assert cap.can_be_primary is False

    def test_can_be_primary_no_unit_for_non_financial(self):
        cap = SourceCapability(
            vendor="v", endpoint="e", data_type=DataType.NEWS,
            fields=["title"], unit="", freshness=Freshness.DAILY,
        )
        assert cap.can_be_primary is True

    def test_can_be_primary_unknown_freshness(self):
        cap = SourceCapability(
            vendor="v", endpoint="e", data_type=DataType.QUOTE,
            fields=["price"], unit="元", freshness=Freshness.UNKNOWN,
        )
        assert cap.can_be_primary is False

    def test_can_be_primary_stale_freshness(self):
        cap = SourceCapability(
            vendor="v", endpoint="e", data_type=DataType.QUOTE,
            fields=["price"], unit="元", freshness=Freshness.STALE,
        )
        assert cap.can_be_primary is False

    def test_can_be_primary_board_fund_flow_no_unit(self):
        cap = SourceCapability(
            vendor="v", endpoint="e", data_type=DataType.BOARD_FUND_FLOW,
            fields=["板块"], unit="", freshness=Freshness.DAILY,
        )
        assert cap.can_be_primary is False


class TestGetSourcesForType:
    def test_ohlcv_sources(self):
        sources = get_sources_for_type(DataType.OHLCV)
        assert len(sources) >= 5
        vendors = [s.vendor for s in sources]
        assert "cn_akshare" in vendors
        assert "cn_astock" in vendors
        assert "cn_baostock" in vendors

    def test_ohlcv_string_input(self):
        sources = get_sources_for_type("ohlcv")
        assert len(sources) >= 5

    def test_fund_flow_sources(self):
        sources = get_sources_for_type("fund_flow")
        assert len(sources) >= 2
        vendors = [s.vendor for s in sources]
        assert "cn_akshare" in vendors
        assert "cn_astock" in vendors

    def test_lhb_sources(self):
        sources = get_sources_for_type("lhb")
        assert len(sources) >= 2

    def test_notice_sources(self):
        sources = get_sources_for_type("notice")
        assert len(sources) >= 1

    def test_realtime_quotes_sources(self):
        sources = get_sources_for_type("realtime_quotes")
        assert len(sources) >= 2
        vendors = [s.vendor for s in sources]
        assert "cn_akshare" in vendors
        assert "cn_astock" in vendors

    def test_news_sources(self):
        sources = get_sources_for_type("news")
        assert len(sources) >= 2

    def test_global_news_sources(self):
        sources = get_sources_for_type("global_news")
        assert len(sources) >= 2

    def test_financials_sources(self):
        sources = get_sources_for_type("financials")
        assert len(sources) >= 3

    def test_report_sources(self):
        sources = get_sources_for_type("report")
        assert len(sources) >= 1

    def test_insider_sources(self):
        sources = get_sources_for_type("insider")
        assert len(sources) >= 2

    def test_zt_pool_sources(self):
        sources = get_sources_for_type("zt_pool")
        assert len(sources) >= 1

    def test_hot_stocks_sources(self):
        sources = get_sources_for_type("hot_stocks")
        assert len(sources) >= 1

    def test_invalid_type_returns_empty(self):
        assert get_sources_for_type("nonexistent") == []

    def test_sorted_by_fallback_priority(self):
        sources = get_sources_for_type(DataType.OHLCV)
        priorities = [s.fallback_priority for s in sources]
        assert priorities == sorted(priorities)

    def test_board_fund_flow_sources(self):
        sources = get_sources_for_type("board_fund_flow")
        assert len(sources) >= 2


class TestGetPrimarySource:
    def test_ohlcv_primary(self):
        p = get_primary_source("ohlcv")
        assert p is not None
        assert p.is_primary is True
        assert p.can_be_primary is True

    def test_fund_flow_primary(self):
        p = get_primary_source("fund_flow")
        assert p is not None
        assert p.is_primary is True
        assert p.can_be_primary is True

    def test_realtime_quotes_primary(self):
        p = get_primary_source("realtime_quotes")
        assert p is not None
        assert p.is_primary is True
        assert p.can_be_primary is True

    def test_notice_primary(self):
        p = get_primary_source("notice")
        assert p is not None
        assert p.is_primary is True
        assert p.can_be_primary is True

    def test_news_primary(self):
        p = get_primary_source("news")
        assert p is not None
        assert p.can_be_primary is True

    def test_invalid_type_returns_none(self):
        assert get_primary_source("nonexistent") is None


class TestGetFallbackChain:
    def test_ohlcv_chain(self):
        chain = get_fallback_chain("ohlcv")
        assert len(chain) >= 5
        assert "cn_akshare" in chain
        assert "cn_astock" in chain
        assert "cn_baostock" in chain

    def test_fund_flow_chain(self):
        chain = get_fallback_chain("fund_flow")
        assert len(chain) >= 2

    def test_lhb_chain(self):
        chain = get_fallback_chain("lhb")
        assert len(chain) >= 2

    def test_chain_order(self):
        chain = get_fallback_chain("ohlcv")
        sources = get_sources_for_type("ohlcv")
        expected = [s.vendor for s in sources]
        assert chain == expected


class TestGetAllDataTypes:
    def test_returns_list(self):
        types = get_all_data_types()
        assert isinstance(types, list)
        assert len(types) >= 10

    def test_no_duplicates(self):
        types = get_all_data_types()
        assert len(types) == len(set(types))

    def test_contains_key_types(self):
        types = get_all_data_types()
        assert "ohlcv" in types
        assert "fund_flow" in types
        assert "lhb" in types
        assert "notice" in types
        assert "news" in types
        assert "realtime_quotes" in types


class TestGetCatalogSummary:
    def test_returns_list_of_dicts(self):
        summary = get_catalog_summary()
        assert isinstance(summary, list)
        assert len(summary) >= 10
        for item in summary:
            assert isinstance(item, dict)
            assert "vendor" in item
            assert "endpoint" in item
            assert "data_type" in item
            assert "fields" in item
            assert "unit" in item
            assert "freshness" in item
            assert "rate_limit_risk" in item
            assert "fallback_priority" in item
            assert "is_primary" in item


class TestGetVendorCapabilities:
    def test_cn_akshare(self):
        caps = get_vendor_capabilities("cn_akshare")
        assert len(caps) >= 5
        types = {c.data_type for c in caps}
        assert DataType.OHLCV in types
        assert DataType.FUND_FLOW in types

    def test_cn_astock(self):
        caps = get_vendor_capabilities("cn_astock")
        assert len(caps) >= 5
        types = {c.data_type for c in caps}
        assert DataType.REALTIME_QUOTES in types

    def test_cn_baostock(self):
        caps = get_vendor_capabilities("cn_baostock")
        assert len(caps) >= 1

    def test_unknown_vendor(self):
        assert get_vendor_capabilities("nonexistent_vendor") == []


class TestValidateCatalog:
    def test_no_issues(self):
        issues = validate_catalog()
        assert issues == []

    def test_primary_has_fields_and_unit(self):
        for dt in DataType:
            sources = get_sources_for_type(dt)
            primaries = [s for s in sources if s.is_primary]
            for p in primaries:
                assert p.fields, (
                    f"primary {p.vendor}/{p.endpoint} for {dt.value} has no fields"
                )


class TestPrimaryIntegrity:
    def test_primary_cannot_be_stale(self):
        for s in _SOURCE_CATALOG:
            if s.is_primary:
                assert s.freshness != Freshness.STALE, (
                    f"primary {s.vendor}/{s.endpoint} ({s.data_type.value}) is stale"
                )

    def test_primary_must_have_fields(self):
        for s in _SOURCE_CATALOG:
            if s.is_primary:
                assert len(s.fields) > 0, (
                    f"primary {s.vendor}/{s.endpoint} ({s.data_type.value}) has no fields"
                )

    def test_primary_must_have_unit_for_quantitative(self):
        quantitative_types = {
            DataType.QUOTE, DataType.OHLCV, DataType.FUND_FLOW,
            DataType.BOARD_FUND_FLOW, DataType.REALTIME_QUOTES,
        }
        for s in _SOURCE_CATALOG:
            if s.is_primary and s.data_type in quantitative_types:
                assert s.unit, (
                    f"primary {s.vendor}/{s.endpoint} ({s.data_type.value}) "
                    f"lacks unit for quantitative data type"
                )

    def test_each_data_type_has_at_most_one_primary(self):
        seen = {}
        for s in _SOURCE_CATALOG:
            if s.is_primary:
                dt = s.data_type.value
                if dt in seen:
                    pytest.fail(
                        f"data_type '{dt}' has multiple primaries: "
                        f"{seen[dt]} and {s.vendor}/{s.endpoint}"
                    )
                seen[dt] = f"{s.vendor}/{s.endpoint}"


class TestCatalogCompleteness:
    def test_catalog_not_empty(self):
        assert len(_SOURCE_CATALOG) > 0

    def test_all_key_types_have_primary(self):
        key_types = [
            "ohlcv", "fund_flow", "board_fund_flow", "lhb",
            "notice", "report", "news", "global_news",
            "financials", "insider", "zt_pool", "hot_stocks",
            "realtime_quotes",
        ]
        for dt in key_types:
            p = get_primary_source(dt)
            assert p is not None, f"data_type '{dt}' has no primary source"

    def test_all_sources_have_vendor_and_endpoint(self):
        for s in _SOURCE_CATALOG:
            assert s.vendor, f"source missing vendor: {s}"
            assert s.endpoint, f"source missing endpoint: {s}"

    def test_fallback_priorities_unique_per_type(self):
        for dt in DataType:
            sources = get_sources_for_type(dt)
            if not sources:
                continue
            priorities = [s.fallback_priority for s in sources]
            assert len(priorities) == len(set(priorities)), (
                f"duplicate fallback_priority in {dt.value}: {priorities}"
            )


class TestDataSpecificChecks:
    def test_ohlcv_primary_is_cn_akshare(self):
        p = get_primary_source("ohlcv")
        assert p is not None
        assert p.vendor == "cn_akshare"
        assert "Date" in p.fields
        assert "Close" in p.fields

    def test_realtime_primary_is_cn_akshare_sina(self):
        p = get_primary_source("realtime_quotes")
        assert p is not None
        assert p.vendor == "cn_akshare"
        assert "price" in p.fields

    def test_fund_flow_primary_unit(self):
        p = get_primary_source("fund_flow")
        assert p is not None
        assert p.unit == "万元"

    def test_lhb_has_force_requirement_gap(self):
        sources = get_sources_for_type("lhb")
        akshare_sources = [s for s in sources if s.vendor == "cn_akshare"]
        assert any("force" in " ".join(s.known_gaps) or "NOT_QUERIED" in " ".join(s.known_gaps)
                    for s in akshare_sources)

    def test_notice_primary_is_cninfo(self):
        p = get_primary_source("notice")
        assert p is not None
        assert p.vendor == "cn_astock"
        assert "cninfo" in p.endpoint

    def test_report_primary_is_eastmoney(self):
        p = get_primary_source("report")
        assert p is not None
        assert p.vendor == "cn_astock"

    def test_astock_realtime_includes_pe_pb(self):
        caps = get_vendor_capabilities("cn_astock")
        rt_caps = [c for c in caps if c.data_type == DataType.REALTIME_QUOTES]
        assert len(rt_caps) >= 1
        tencent_rt = rt_caps[0]
        assert "pe_ttm" in tencent_rt.fields
        assert "pb" in tencent_rt.fields
        assert "turnover_rate" in tencent_rt.fields
        assert "volume_ratio" in tencent_rt.fields
