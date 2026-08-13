from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from scripts import export_financial_fact_bundle as exporter_module
from scripts.export_financial_fact_bundle import (
    _available_default_provider_names,
    _is_verified_export,
    _persist_export_bundle,
    _publish_verified_bundle,
    _write_json_immutable,
)
from tradingagents.dataflows.financial_fact_bundle import (
    DEFAULT_PROVIDER_NAMES,
    CONFLICT,
    HAS_DATA,
    NORMAL_NO_DATA,
    QUERY_FAILED,
    SINGLE_SOURCE,
    VERIFIED_CROSS_SOURCE,
    _identity_source_ids,
    _fact_conflict_counts,
    _partition_fact_conflicts,
    build_financial_fact_bundle,
)


def test_default_providers_include_independent_identity_source():
    assert DEFAULT_PROVIDER_NAMES == (
        "cn_tushare",
        "cn_astock",
        "cn_eastmoney_financial",
        "cn_cninfo_identity",
    )


def test_tokenless_default_skips_unregistered_optional_tushare():
    registry = Mock()
    registry.get.side_effect = lambda name: (
        None if name == "cn_tushare" else object()
    )

    assert _available_default_provider_names(registry) == (
        "cn_astock",
        "cn_eastmoney_financial",
        "cn_cninfo_identity",
    )


def test_configured_default_includes_tushare():
    registry = Mock()
    registry.get.return_value = object()

    assert _available_default_provider_names(registry) == DEFAULT_PROVIDER_NAMES


def test_list_configured_providers_cli_is_read_only(monkeypatch, capsys):
    registry = Mock()
    registry.get.side_effect = lambda name: (
        None if name == "cn_tushare" else object()
    )
    monkeypatch.setattr(exporter_module, "build_default_registry", lambda: registry)
    monkeypatch.setattr(
        sys,
        "argv",
        ["export_financial_fact_bundle.py", "--list-configured-providers"],
    )

    assert exporter_module.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "providers": [
            "cn_astock",
            "cn_eastmoney_financial",
            "cn_cninfo_identity",
        ]
    }


def test_astock_cninfo_fallback_is_not_relabelled_as_eastmoney():
    provider = Mock(name="provider")
    provider.name = "cn_astock"
    provider.identity_source_id = None
    fundamentals = """\
### Company Profile (巨潮资讯)
| item | value |
|---|---|
| 股票简称 | 亨通光电 |
| 股票代码 | 600487 |
"""

    assert _identity_source_ids(provider, fundamentals) == ("cninfo",)


def test_astock_uses_only_primary_identity_lineage_when_both_profiles_exist():
    provider = Mock(name="provider")
    provider.name = "cn_astock"
    provider.identity_source_id = None
    fundamentals = """\
### Company Profile (东财)
- **股票简称**: 亨通光电
- **股票代码**: 600487

### Company Profile (巨潮资讯)
- **股票简称**: 亨通光电
- **股票代码**: 600487
"""

    assert _identity_source_ids(provider, fundamentals) == ("eastmoney",)


def test_astock_ignores_profile_header_without_usable_identity():
    provider = Mock(name="provider")
    provider.name = "cn_astock"
    provider.identity_source_id = None
    fundamentals = """\
### Company Profile (东财)
- **总市值(元)**: 1000000000

### Company Profile (巨潮资讯)
- **股票简称**: 亨通光电
- **股票代码**: 600487
"""

    assert _identity_source_ids(provider, fundamentals) == ("cninfo",)


def test_astock_identity_fields_come_from_selected_primary_section():
    eastmoney = """\
### Company Profile (东财)
- **代码**: 600487
- **名称**: 错误简称

### Company Profile (巨潮资讯)
- **股票代码**: 600487
- **股票简称**: 亨通光电
"""
    standalone_cninfo = """\
### Company Profile (巨潮资讯)
| item | value |
|---|---|
| 股票代码 | 600487 |
| 股票简称 | 亨通光电 |
"""
    astock = FakeProvider("cn_astock", profile=eastmoney)
    astock.identity_source_id = None
    result = _bundle(
        astock,
        FakeProvider(
            "cn_cninfo_identity",
            profile=standalone_cninfo,
            identity_source_id="cninfo",
        ),
    )

    assert result["status"] == CONFLICT
    assert result["identity"]["status"] == CONFLICT


def test_identity_only_query_failure_is_not_normal_no_data():
    provider = FakeProvider(
        "cn_cninfo_identity",
        statement_sentinel="No income statement data from identity-only provider",
        identity_source_id="cninfo",
    )
    provider.get_fundamentals = Mock(side_effect=ConnectionError("down"))

    result = _bundle(provider)

    assert result["status"] == QUERY_FAILED
    assert result["providers"][0]["status"] == QUERY_FAILED


PROFILE = """\
### Company Profile
| 项目 | 内容 |
|---|---|
| 股票简称 | 亨通光电 |
| 股票代码 | 600487 |
| 主营业务 | 光纤光缆及海洋通信 |
| 所属行业 | 通信设备 |
"""
INCOME = """\
## Income Statement
| 报告日 | 营业收入 | 归属于母公司股东的净利润 |
|---|---:|---:|
| 2026-03-31 | 1000000000 | 100000000 |
| 2025-12-31 | 3600000000 | 300000000 |
| 2025-09-30 | 2500000000 | 210000000 |
"""
CASHFLOW = """\
## Cashflow
| 报告日 | 经营活动产生的现金流量净额 |
|---|---:|
| 2026-03-31 | 120000000 |
"""
BALANCE = """\
## Balance Sheet
| 报告日 | 资产总计 | 负债合计 | 存货 |
|---|---:|---:|---:|
| 2026-03-31 | 8000000000 | 4000000000 | 900000000 |
"""


class FakeProvider:
    def __init__(
        self,
        name: str,
        *,
        revenue: str = "1000000000",
        profile: str = PROFILE,
        fail: bool = False,
        source_id: str | None = None,
        identity_source_id: str | None = None,
        profile_fail_only: bool = False,
        statement_sentinel: str | None = None,
    ):
        self.name = name
        self.revenue = revenue
        self.profile = profile
        self.fail = fail
        self.financial_source_id = source_id or name
        self.identity_source_id = identity_source_id or name
        self.profile_fail_only = profile_fail_only
        self.statement_sentinel = statement_sentinel

    def get_fundamentals(self, ticker, curr_date=None):
        if self.fail or self.profile_fail_only:
            raise ConnectionError("offline")
        return self.profile

    def get_income_statement(self, ticker, freq="quarterly", curr_date=None):
        if self.fail:
            raise ConnectionError("offline")
        if self.statement_sentinel:
            return self.statement_sentinel
        return INCOME.replace("1000000000", self.revenue, 1)

    def get_cashflow(self, ticker, freq="quarterly", curr_date=None):
        if self.fail:
            raise ConnectionError("offline")
        if self.statement_sentinel:
            return self.statement_sentinel
        return CASHFLOW

    def get_balance_sheet(self, ticker, freq="quarterly", curr_date=None):
        if self.fail:
            raise ConnectionError("offline")
        if self.statement_sentinel:
            return self.statement_sentinel
        return BALANCE


def _bundle(*providers):
    return build_financial_fact_bundle(
        symbol="600487.SH",
        as_of="2026-07-30",
        providers=providers,
        generated_at="2026-07-30T12:00:00+08:00",
        observed_at="2026-07-30",
    )


def test_two_sources_agree_and_verify_numeric_facts():
    result = _bundle(FakeProvider("one"), FakeProvider("two"))
    assert result["status"] == HAS_DATA
    assert result["identity"]["status"] == VERIFIED_CROSS_SOURCE
    revenue = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue" and fact["report_date"] == "2026-03-31"
    )
    assert revenue["verification_status"] == VERIFIED_CROSS_SOURCE
    assert revenue["providers"] == ["one", "two"]
    assert revenue["disclosure_date"] == "2026-07-30"
    assert revenue["disclosure_date_inferred"] is True


def test_small_rounding_difference_is_tolerated():
    result = _bundle(
        FakeProvider("one"),
        FakeProvider("two", revenue="1001000000"),
    )
    revenue = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue" and fact["report_date"] == "2026-03-31"
    )
    assert revenue["verification_status"] == VERIFIED_CROSS_SOURCE


def test_cross_source_verification_keeps_a_real_source_value():
    result = _bundle(
        FakeProvider("rounded", revenue="1000000000", source_id="sina_finance"),
        FakeProvider(
            "precise",
            revenue="1000000123.45",
            source_id="eastmoney_datacenter",
        ),
    )
    revenue = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue" and fact["report_date"] == "2026-03-31"
    )
    assert revenue["verification_status"] == VERIFIED_CROSS_SOURCE
    assert revenue["value"] == 1000000123.45
    assert revenue["value_source_id"] == "eastmoney_datacenter"


def test_tushare_is_independent_and_has_value_priority():
    result = _bundle(
        FakeProvider(
            "cn_tushare",
            revenue="1000000456.78",
            source_id="tushare_pro",
            identity_source_id="tushare_pro",
        ),
        FakeProvider(
            "cn_eastmoney_financial",
            revenue="1000000123.45",
            source_id="eastmoney_datacenter",
            identity_source_id="eastmoney",
        ),
    )
    revenue = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue" and fact["report_date"] == "2026-03-31"
    )
    assert result["identity"]["status"] == VERIFIED_CROSS_SOURCE
    assert revenue["verification_status"] == VERIFIED_CROSS_SOURCE
    assert revenue["source_ids"] == ["eastmoney_datacenter", "tushare_pro"]
    assert revenue["value"] == 1000000456.78
    assert revenue["value_source_id"] == "tushare_pro"


def test_old_conflict_is_audited_without_blocking_recent_window():
    facts = [
        {
            "metric": "operating_cost",
            "report_date": f"202{year}-{month_day}",
            "period_scope": "FY_YTD",
            "is_derived": False,
            "verification_status": VERIFIED_CROSS_SOURCE,
        }
        for year, month_day in (
            (6, "03-31"),
            (5, "12-31"),
            (5, "09-30"),
            (5, "06-30"),
            (5, "03-31"),
            (4, "12-31"),
        )
    ]
    facts.append(
        {
            "metric": "operating_cost",
            "report_date": "2023-12-31",
            "period_scope": "SINGLE_QUARTER",
            "is_derived": True,
            "verification_status": CONFLICT,
        }
    )

    assert _fact_conflict_counts(facts) == (0, 1)
    blocking, historical = _partition_fact_conflicts(facts)
    assert blocking == []
    assert historical == [facts[-1]]


def test_recent_conflict_remains_blocking():
    facts = [
        {
            "metric": "revenue",
            "report_date": "2026-03-31",
            "period_scope": "Q1_YTD",
            "is_derived": False,
            "verification_status": CONFLICT,
        }
    ]

    assert _fact_conflict_counts(facts) == (1, 0)


def test_conflict_window_keeps_all_scopes_from_cutoff_period():
    facts = [
        {
            "metric": "revenue",
            "report_date": report_date,
            "period_scope": "Q1_YTD",
            "is_derived": False,
            "verification_status": VERIFIED_CROSS_SOURCE,
        }
        for report_date in (
            "2026-03-31",
            "2025-12-31",
            "2025-09-30",
            "2025-06-30",
            "2025-03-31",
            "2024-12-31",
        )
    ]
    facts.append(
        {
            "metric": "revenue",
            "report_date": "2024-12-31",
            "period_scope": "SINGLE_QUARTER",
            "is_derived": True,
            "verification_status": CONFLICT,
        }
    )

    assert _fact_conflict_counts(facts) == (1, 0)


def test_material_provider_conflict_fails_closed():
    result = _bundle(
        FakeProvider("one"),
        FakeProvider("two", revenue="1500000000"),
    )
    assert result["status"] == CONFLICT
    revenue = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue" and fact["report_date"] == "2026-03-31"
    )
    assert revenue["verification_status"] == CONFLICT
    assert revenue["value"] is None


def test_single_provider_never_claims_cross_source_verification():
    result = _bundle(FakeProvider("one"))
    assert result["status"] == SINGLE_SOURCE
    assert all(
        fact["verification_status"] == SINGLE_SOURCE for fact in result["facts"]
    )


def test_two_adapters_on_same_underlying_source_are_not_cross_source():
    result = _bundle(
        FakeProvider("adapter-one", source_id="same-origin"),
        FakeProvider("adapter-two", source_id="same-origin"),
    )
    assert result["status"] == SINGLE_SOURCE
    revenue = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue" and fact["report_date"] == "2026-03-31"
    )
    assert revenue["verification_status"] == SINGLE_SOURCE
    assert revenue["source_ids"] == ["same-origin"]
    assert revenue["providers"] == ["adapter-one", "adapter-two"]


def test_same_source_adapter_parse_disagreement_is_a_conflict():
    result = _bundle(
        FakeProvider("adapter-one", source_id="same-origin"),
        FakeProvider(
            "adapter-two", source_id="same-origin", revenue="1500000000"
        ),
    )
    assert result["status"] == CONFLICT
    revenue = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue" and fact["report_date"] == "2026-03-31"
    )
    assert revenue["verification_status"] == CONFLICT
    assert revenue["value"] is None


def test_cross_source_reconciliation_never_uses_same_source_averages():
    result = _bundle(
        FakeProvider("adapter-one", source_id="same-origin"),
        FakeProvider(
            "adapter-two",
            source_id="same-origin",
            revenue="1005000000",
        ),
        FakeProvider(
            "independent",
            source_id="independent-origin",
            revenue="1007500000",
        ),
    )
    revenue = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue" and fact["report_date"] == "2026-03-31"
    )
    assert revenue["verification_status"] == CONFLICT
    assert revenue["value"] is None


def test_identity_mismatch_blocks_bundle():
    other = PROFILE.replace("亨通光电", "错误公司")
    result = _bundle(FakeProvider("one"), FakeProvider("two", profile=other))
    assert result["status"] == CONFLICT
    assert result["identity"]["status"] == CONFLICT


def test_identity_whitespace_difference_does_not_create_false_conflict():
    spaced = PROFILE.replace("亨通光电", "亨通 光电")
    result = _bundle(FakeProvider("one"), FakeProvider("two", profile=spaced))
    assert result["status"] == HAS_DATA
    assert result["identity"]["status"] == VERIFIED_CROSS_SOURCE


def test_partial_identity_can_confirm_symbol_and_name_across_sources():
    partial = PROFILE.replace("光纤光缆及海洋通信", "")
    result = _bundle(
        FakeProvider("one"),
        FakeProvider("two", profile=partial),
    )
    assert result["identity"]["status"] == VERIFIED_CROSS_SOURCE
    assert result["identity"]["security_name"] == "亨通光电"


def test_two_adapters_on_same_identity_source_are_not_cross_source():
    result = _bundle(
        FakeProvider("adapter-one", identity_source_id="eastmoney"),
        FakeProvider("adapter-two", identity_source_id="eastmoney"),
    )
    assert result["identity"]["status"] == SINGLE_SOURCE
    assert result["identity"]["source_ids"] == ["eastmoney"]


def test_provider_symbol_mismatch_blocks_even_when_other_provider_is_valid():
    wrong_code = PROFILE.replace("600487", "000001")
    result = _bundle(
        FakeProvider("one"),
        FakeProvider("two", profile=wrong_code),
    )
    assert result["status"] == CONFLICT
    assert result["identity"]["status"] == CONFLICT
    assert result["identity"]["reasons"] == ["profile_symbol_mismatch"]
    assert result["providers"][1]["status"] == CONFLICT


def test_embedded_fallback_symbol_mismatch_blocks_verified_export():
    mixed_profile = """### Company Profile (东财)
- **代码**: 600487
- **名称**: 亨通光电
- **行业**: 通信
- **主营业务**: 光通信

### Company Profile (巨潮资讯)
- **股票代码**: 000001
- **身份冲突**: requested=600487, returned=000001
"""
    mixed = FakeProvider(
        "cn_astock",
        profile=mixed_profile,
        source_id="sina_finance",
    )
    mixed.identity_source_id = None
    result = _bundle(
        mixed,
        FakeProvider(
            "independent",
            identity_source_id="independent_identity",
            source_id="independent_financial",
        ),
    )

    assert result["status"] == CONFLICT
    assert result["identity"]["status"] == CONFLICT
    assert result["identity"]["reasons"] == ["profile_sections_symbol_mismatch"]
    assert _is_verified_export(result) is False


def test_all_provider_identity_conflicts_remain_explicit():
    wrong_code = PROFILE.replace("600487", "000001")
    result = _bundle(
        FakeProvider("one", profile=wrong_code),
        FakeProvider("two", profile=wrong_code),
    )
    assert result["status"] == CONFLICT
    assert result["identity"]["status"] == CONFLICT
    assert result["identity"]["providers"] == ["one", "two"]


def test_identity_full_legal_name_and_stock_short_name_are_compatible():
    full_name = PROFILE.replace("亨通光电", "江苏亨通光电股份有限公司")
    result = _bundle(
        FakeProvider("short-name"),
        FakeProvider("legal-name", profile=full_name),
    )
    assert result["status"] == HAS_DATA
    assert result["identity"]["status"] == VERIFIED_CROSS_SOURCE
    assert result["identity"]["security_name"] == "亨通光电"


def test_identity_arbitrary_name_containment_is_a_conflict():
    petroleum = PROFILE.replace("亨通光电", "中国石油")
    petrochemical = PROFILE.replace("亨通光电", "中国石油化工")
    result = _bundle(
        FakeProvider("petroleum", profile=petroleum),
        FakeProvider("petrochemical", profile=petrochemical),
    )
    assert result["status"] == CONFLICT
    assert result["identity"]["status"] == CONFLICT


def test_provider_failure_is_explicit_not_empty_success():
    result = _bundle(FakeProvider("one", fail=True), FakeProvider("two", fail=True))
    assert result["status"] == QUERY_FAILED
    assert all(provider["status"] == QUERY_FAILED for provider in result["providers"])


def test_statement_failures_are_not_labeled_normal_no_data():
    result = _bundle(
        FakeProvider("one", statement_sentinel="Income statement unavailable: down"),
        FakeProvider("two", statement_sentinel="Income statement unavailable: down"),
    )
    assert result["status"] == QUERY_FAILED
    assert all(provider["status"] == QUERY_FAILED for provider in result["providers"])
    assert all(
        statement["status"] == QUERY_FAILED
        for provider in result["providers"]
        for statement in provider["statements"].values()
    )


def test_unparseable_nonempty_statement_is_query_failed():
    result = _bundle(
        FakeProvider("one", statement_sentinel="<html>gateway response</html>"),
        FakeProvider("two", statement_sentinel="<html>gateway response</html>"),
    )
    assert result["status"] == QUERY_FAILED
    assert all(
        statement["status"] == QUERY_FAILED
        for provider in result["providers"]
        for statement in provider["statements"].values()
    )


def test_unsupported_non_string_statement_is_query_failed():
    class ChangedContractProvider(FakeProvider):
        def get_income_statement(self, ticker, freq="quarterly", curr_date=None):
            return [{"报告日": "2026-03-31", "营业收入": 10}]

        def get_cashflow(self, ticker, freq="quarterly", curr_date=None):
            return {"data": []}

        def get_balance_sheet(self, ticker, freq="quarterly", curr_date=None):
            return []

    result = _bundle(ChangedContractProvider("changed"))
    assert result["status"] == QUERY_FAILED
    assert all(
        statement["status"] == QUERY_FAILED
        for statement in result["providers"][0]["statements"].values()
    )


def test_none_statement_is_query_failed_not_normal_no_data():
    class NoneProvider(FakeProvider):
        def get_income_statement(self, ticker, freq="quarterly", curr_date=None):
            return None

        def get_cashflow(self, ticker, freq="quarterly", curr_date=None):
            return None

        def get_balance_sheet(self, ticker, freq="quarterly", curr_date=None):
            return None

    result = _bundle(NoneProvider("none"))
    assert result["status"] == QUERY_FAILED
    assert all(
        statement["status"] == QUERY_FAILED
        for statement in result["providers"][0]["statements"].values()
    )


def test_provider_errors_redact_credentials_before_serialization():
    class SecretProvider(FakeProvider):
        def _raise(self, *args, **kwargs):
            raise RuntimeError(
                "GET https://example.test/data?apikey=SECRET"
                "&access_token=TOKEN Authorization: Bearer TOPSECRET"
                " headers={'Authorization': 'Bearer MAPSECRET', "
                "'api_key': 'KEYSECRET'}"
            )

        get_fundamentals = _raise
        get_income_statement = _raise
        get_cashflow = _raise
        get_balance_sheet = _raise

    result = _bundle(SecretProvider("one"), SecretProvider("two"))
    serialized = json.dumps(result)
    assert "SECRET" not in serialized
    assert "TOKEN" not in serialized
    assert "TOPSECRET" not in serialized
    assert "MAPSECRET" not in serialized
    assert "KEYSECRET" not in serialized
    assert serialized.count("[REDACTED]") > 0


def test_provider_specific_empty_statement_markers_are_normal_no_data():
    class EmptyEastmoneyProvider(FakeProvider):
        def get_income_statement(self, ticker, freq="quarterly", curr_date=None):
            return f"No income data found for {ticker}"

        def get_cashflow(self, ticker, freq="quarterly", curr_date=None):
            return f"No cashflow data found for {ticker}"

        def get_balance_sheet(self, ticker, freq="quarterly", curr_date=None):
            return f"No balance data found for {ticker}"

    result = _bundle(EmptyEastmoneyProvider("eastmoney"))
    assert result["status"] == NORMAL_NO_DATA
    assert all(
        statement["status"] == NORMAL_NO_DATA
        for statement in result["providers"][0]["statements"].values()
    )


def test_profile_failure_does_not_discard_healthy_statements():
    result = _bundle(
        FakeProvider("one", profile_fail_only=True),
        FakeProvider("two"),
    )
    assert result["status"] == HAS_DATA
    failed_profile = result["providers"][0]
    assert failed_profile["identity"]["status"] == QUERY_FAILED
    assert failed_profile["facts"]


def test_all_identity_failures_remain_query_failed_when_statements_are_healthy():
    result = _bundle(
        FakeProvider("one", profile_fail_only=True),
        FakeProvider("two", profile_fail_only=True),
    )
    assert result["status"] == HAS_DATA
    assert result["identity"]["status"] == QUERY_FAILED
    assert result["facts"]


def test_q4_is_derived_from_fy_minus_q3():
    result = _bundle(FakeProvider("one"), FakeProvider("two"))
    q4 = next(
        fact
        for fact in result["facts"]
        if fact["metric"] == "revenue"
        and fact["report_date"] == "2025-12-31"
        and fact["is_derived"]
    )
    assert q4["period_scope"] == "SINGLE_QUARTER"
    assert q4["value"] == 1100000000
    assert q4["formula"] == "FY_YTD-Q3_YTD"


def test_future_report_period_is_discarded_before_reconciliation():
    class FutureProvider(FakeProvider):
        def get_income_statement(self, ticker, freq="quarterly", curr_date=None):
            return INCOME

    result = build_financial_fact_bundle(
        symbol="600487.SH",
        as_of="2026-02-28",
        providers=[FutureProvider("one"), FutureProvider("two")],
        generated_at="2026-07-30T12:00:00+08:00",
        observed_at="2026-02-28",
    )
    assert not any(
        fact["report_date"] == "2026-03-31" for fact in result["facts"]
    )
    captures = result["providers"]
    assert all(
        capture["statements"]["income_statement"]["status"] == HAS_DATA
        and capture["statements"]["income_statement"]["discarded_after_as_of"] > 0
        for capture in captures
    )


def test_period_end_before_as_of_is_hidden_until_payload_is_observed():
    result = build_financial_fact_bundle(
        symbol="600487.SH",
        as_of="2026-01-15",
        providers=[FakeProvider("one"), FakeProvider("two")],
        generated_at="2026-07-30T12:00:00+08:00",
        observed_at="2026-07-30",
    )
    assert not any(
        fact["report_date"] == "2025-12-31" for fact in result["facts"]
    )
    assert result["facts"] == []
    assert result["status"] == QUERY_FAILED
    assert result["identity"]["status"] == QUERY_FAILED
    assert all(
        provider["identity"]["status"] == QUERY_FAILED
        and "archived immutable fact bundle" in provider["identity"]["error"]
        and all(
            statement["status"] == QUERY_FAILED
            and "archived immutable fact bundle" in statement["error"]
            for statement in provider["statements"].values()
        )
        for provider in result["providers"]
    )


def test_historical_as_of_never_queries_live_provider_payloads():
    class LiveProviderMustNotRun(FakeProvider):
        def get_fundamentals(self, ticker, curr_date=None):
            raise AssertionError("historical capture queried live identity")

        def get_income_statement(self, ticker, freq="quarterly", curr_date=None):
            raise AssertionError("historical capture queried live income statement")

        def get_cashflow(self, ticker, freq="quarterly", curr_date=None):
            raise AssertionError("historical capture queried live cashflow")

        def get_balance_sheet(self, ticker, freq="quarterly", curr_date=None):
            raise AssertionError("historical capture queried live balance sheet")

    result = build_financial_fact_bundle(
        symbol="600487.SH",
        as_of="2026-01-15",
        providers=[LiveProviderMustNotRun("live")],
        generated_at="2026-07-30T12:00:00+08:00",
        observed_at="2026-07-30",
    )
    assert result["status"] == QUERY_FAILED
    assert result["facts"] == []


def test_bundle_rejects_exchange_suffix_mismatch_before_provider_calls():
    provider = FakeProvider("one")
    with pytest.raises(ValueError, match="exchange suffix mismatch"):
        build_financial_fact_bundle(
            symbol="600487.SZ",
            as_of="2026-07-30",
            providers=[provider],
        )


def test_bundle_rejects_unsupported_a_share_prefix_before_provider_calls():
    provider = FakeProvider("one")
    with pytest.raises(ValueError, match="unsupported A-share symbol"):
        build_financial_fact_bundle(
            symbol="900901.SH",
            as_of="2026-07-30",
            providers=[provider],
        )


def test_bundle_canonicalizes_bare_a_share_symbol():
    result = build_financial_fact_bundle(
        symbol="600487",
        as_of="2026-07-30",
        providers=[FakeProvider("one")],
        generated_at="2026-07-30T12:00:00+08:00",
        observed_at="2026-07-30",
    )
    assert result["symbol"] == "600487.SH"


def test_bundle_rejects_future_as_of_for_live_capture():
    with pytest.raises(ValueError, match="future as_of"):
        build_financial_fact_bundle(
            symbol="600487.SH",
            as_of="2030-01-01",
            providers=[FakeProvider("one"), FakeProvider("two")],
            generated_at="2026-07-30T12:00:00+08:00",
            observed_at="2026-07-30",
        )


@pytest.mark.parametrize(
    ("message", "secret_value"),
    [
        ("OPENAI_API_KEY=sk-test-secret", "sk-test-secret"),
        ("XQ_A_TOKEN=xq-secret", "xq-secret"),
        ("client_secret=oauth-secret", "oauth-secret"),
        (
            "https://example.test?refresh_token=refresh-secret",
            "refresh-secret",
        ),
        ("headers={'Cookie': 'sessionid=private'}", "sessionid=private"),
        ("session_id=session-secret", "session-secret"),
    ],
)
def test_bundle_redacts_prefixed_and_bare_credentials_from_provider_errors(
    message, secret_value
):
    provider = FakeProvider("one")
    provider.get_fundamentals = Mock(side_effect=RuntimeError(message))
    result = build_financial_fact_bundle(
        symbol="600487.SH",
        as_of="2026-07-30",
        providers=[provider],
        generated_at="2026-07-30T12:00:00+08:00",
        observed_at="2026-07-30",
    )
    error = result["providers"][0]["error"]
    assert "[REDACTED]" in error
    assert secret_value not in error


def test_bundle_preserves_provider_notice_date_from_rendered_markdown():
    provider = FakeProvider("one")
    provider.get_income_statement = Mock(
        return_value="\n".join(
            [
                "| 来源公告日期 | 报告日 | 营业总收入 |",
                "|---|---|---|",
                "| 2026-04-25 | 2026-03-31 | 10 |",
            ]
        )
    )
    result = build_financial_fact_bundle(
        symbol="600487.SH",
        as_of="2026-07-30",
        providers=[provider],
        generated_at="2026-07-30T12:00:00+08:00",
        observed_at="2026-07-30",
    )
    revenue = next(
        fact
        for fact in result["providers"][0]["facts"]
        if fact["metric"] == "revenue"
    )
    assert revenue["disclosure_date"] == "2026-04-25"
    assert revenue["disclosure_date_inferred"] is False


@pytest.mark.parametrize(
    ("bundle", "expected"),
    [
        (
            {
                "status": HAS_DATA,
                "identity": {"status": VERIFIED_CROSS_SOURCE},
                "summary": {"verified_cross_source": 1},
            },
            True,
        ),
        (
            {
                "status": HAS_DATA,
                "identity": {"status": SINGLE_SOURCE},
                "summary": {"verified_cross_source": 1},
            },
            False,
        ),
        ({"status": HAS_DATA, "summary": {"verified_cross_source": 0}}, False),
        ({"status": SINGLE_SOURCE, "summary": {"verified_cross_source": 0}}, False),
        (
            {"status": NORMAL_NO_DATA, "summary": {"verified_cross_source": 0}},
            False,
        ),
    ],
)
def test_export_success_requires_verified_cross_source_fact(bundle, expected):
    assert _is_verified_export(bundle) is expected


def test_unverified_export_preserves_audit_bundle_without_claiming_verification(
    tmp_path,
):
    output = tmp_path / "failed-bundle.json"
    bundle = {
        "status": SINGLE_SOURCE,
        "generated_at": "2026-07-31T12:00:00+08:00",
        "summary": {"verified_cross_source": 0},
    }
    assert _publish_verified_bundle(output, bundle) is False
    assert not output.exists()
    audit_output = tmp_path / "failed-bundle.retryable-202607311200000800.json"
    assert json.loads(audit_output.read_text(encoding="utf-8")) == bundle


def test_query_failure_uses_separate_attempt_path_and_keeps_target_retryable(
    tmp_path,
):
    output = tmp_path / "bundle.json"
    failed = {
        "status": QUERY_FAILED,
        "generated_at": "2026-07-31T12:00:00+08:00",
        "summary": {"verified_cross_source": 0},
    }
    verified, actual_output = _persist_export_bundle(output, failed)
    assert verified is False
    assert actual_output != output
    assert actual_output.is_file()
    assert not output.exists()

    recovered = {
        "status": HAS_DATA,
        "identity": {"status": VERIFIED_CROSS_SOURCE},
        "generated_at": "2026-07-31T12:05:00+08:00",
        "summary": {"verified_cross_source": 1},
    }
    verified, actual_output = _persist_export_bundle(output, recovered)
    assert verified is True
    assert actual_output == output
    assert output.is_file()


def test_partial_provider_failure_also_keeps_target_retryable(tmp_path):
    output = tmp_path / "bundle.json"
    partial = {
        "status": SINGLE_SOURCE,
        "generated_at": "2026-07-31T12:00:00+08:00",
        "summary": {"verified_cross_source": 0},
        "providers": [
            {"provider": "healthy", "status": HAS_DATA},
            {"provider": "offline", "status": QUERY_FAILED},
        ],
    }
    verified, actual_output = _persist_export_bundle(output, partial)
    assert verified is False
    assert actual_output != output
    assert actual_output.is_file()
    assert not output.exists()

    recovered = {
        "status": HAS_DATA,
        "identity": {"status": VERIFIED_CROSS_SOURCE},
        "generated_at": "2026-07-31T12:05:00+08:00",
        "summary": {"verified_cross_source": 1},
        "providers": [
            {"provider": "healthy", "status": HAS_DATA},
            {"provider": "recovered", "status": HAS_DATA},
        ],
    }
    verified, actual_output = _persist_export_bundle(output, recovered)
    assert verified is True
    assert actual_output == output


def test_nested_statement_failure_keeps_target_retryable(tmp_path):
    output = tmp_path / "bundle.json"
    partial = {
        "status": HAS_DATA,
        "generated_at": "2026-07-31T12:00:00+08:00",
        "summary": {"verified_cross_source": 1},
        "providers": [
            {
                "provider": "partial",
                "status": HAS_DATA,
                "statements": {
                    "income_statement": {"status": HAS_DATA},
                    "cashflow": {"status": QUERY_FAILED},
                },
            }
        ],
    }
    verified, actual_output = _persist_export_bundle(output, partial)
    assert verified is False
    assert actual_output != output
    assert actual_output.is_file()
    assert not output.exists()


def test_repeated_degraded_attempts_with_same_timestamp_are_both_preserved(tmp_path):
    output = tmp_path / "bundle.json"
    first = {
        "status": QUERY_FAILED,
        "generated_at": "2026-07-31T12:00:00+08:00",
        "summary": {"verified_cross_source": 0},
        "attempt": "first",
    }
    second = {**first, "attempt": "second"}

    first_verified, first_path = _persist_export_bundle(output, first)
    second_verified, second_path = _persist_export_bundle(output, second)

    assert first_verified is False
    assert second_verified is False
    assert first_path != second_path
    assert first_path.name == "bundle.retryable-202607311200000800.json"
    assert second_path.name == "bundle.retryable-202607311200000800-01.json"
    assert json.loads(first_path.read_text(encoding="utf-8")) == first
    assert json.loads(second_path.read_text(encoding="utf-8")) == second
    assert not output.exists()


def test_concurrent_degraded_attempts_with_same_timestamp_are_both_preserved(
    tmp_path,
):
    output = tmp_path / "bundle.json"
    barrier = threading.Barrier(2)

    def persist(attempt):
        bundle = {
            "status": QUERY_FAILED,
            "generated_at": "2026-07-31T12:00:00+08:00",
            "summary": {"verified_cross_source": 0},
            "attempt": attempt,
        }
        barrier.wait()
        return _persist_export_bundle(output, bundle)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(persist, ("first", "second")))

    assert all(verified is False for verified, _path in results)
    paths = {path for _verified, path in results}
    assert len(paths) == 2
    assert {path.name for path in paths} == {
        "bundle.retryable-202607311200000800.json",
        "bundle.retryable-202607311200000800-01.json",
    }
    assert {
        json.loads(path.read_text(encoding="utf-8"))["attempt"] for path in paths
    } == {"first", "second"}
    assert not output.exists()


def test_retryable_writer_does_not_loop_on_invalid_parent_path(tmp_path):
    invalid_parent = tmp_path / "not-a-directory"
    invalid_parent.write_text("occupied by a file", encoding="utf-8")
    bundle = {
        "status": QUERY_FAILED,
        "generated_at": "2026-07-31T12:00:00+08:00",
        "summary": {"verified_cross_source": 0},
    }

    with pytest.raises(FileExistsError):
        _persist_export_bundle(invalid_parent / "bundle.json", bundle)


def test_dangling_symlink_occupies_retryable_slot_without_losing_attempt(tmp_path):
    output = tmp_path / "bundle.json"
    occupied = tmp_path / "bundle.retryable-202607311200000800.json"
    occupied.symlink_to(tmp_path / "missing-target")
    bundle = {
        "status": QUERY_FAILED,
        "generated_at": "2026-07-31T12:00:00+08:00",
        "summary": {"verified_cross_source": 0},
    }

    verified, actual_path = _persist_export_bundle(output, bundle)

    assert verified is False
    assert occupied.is_symlink()
    assert actual_path.name == "bundle.retryable-202607311200000800-01.json"
    assert json.loads(actual_path.read_text(encoding="utf-8")) == bundle
    assert not output.exists()


def test_bundle_records_observation_date():
    provider = FakeProvider(
        "cn_astock",
        profile=PROFILE,
    )
    result = build_financial_fact_bundle(
        symbol="600487.SH",
        as_of="2026-07-30",
        providers=[provider],
        generated_at="2026-07-30T12:00:00+08:00",
        observed_at="2026-07-30",
    )
    assert result["observed_at"] == "2026-07-30"


def test_immutable_writer_is_atomic_under_concurrent_creation(tmp_path):
    output = tmp_path / "bundle.json"
    barrier = threading.Barrier(2)

    def write(payload):
        barrier.wait()
        try:
            _write_json_immutable(output, payload)
            return "written"
        except FileExistsError:
            return "exists"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ({"writer": 1}, {"writer": 2})))

    assert sorted(results) == ["exists", "written"]
    assert json.loads(output.read_text(encoding="utf-8")) in (
        {"writer": 1},
        {"writer": 2},
    )
