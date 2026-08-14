from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
import pytest

from tradingagents.dataflows.financial_periods import (
    FY_YTD,
    SINGLE_QUARTER,
    derive_single_quarters,
    normalize_financial_markdown,
)
from tradingagents.dataflows.instrument_identity import (
    build_instrument_identity,
    extract_profile_from_fundamentals,
)
from tradingagents.dataflows.providers.cn_tushare_provider import (
    CnTushareProvider,
    _normalize_ts_code,
)
from tradingagents.dataflows.source_catalog import get_primary_source
from tradingagents.dataflows.interface import (
    _last_hit_vendor,
    _record_hit_vendor,
    clear_current_thread_hit_vendor,
    get_current_thread_hit_vendor,
)
from tradingagents.graph.data_collector import DataCollector, make_cache_key


def _query_from(frames: dict[str, pd.DataFrame]):
    def query(endpoint: str, **_kwargs):
        return frames.get(endpoint, pd.DataFrame()).copy()

    return query


def test_symbol_normalization_covers_supported_a_share_boards():
    assert _normalize_ts_code("603629") == "603629.SH"
    assert _normalize_ts_code("002409.sz") == "002409.SZ"
    assert _normalize_ts_code("430047") == "430047.BJ"
    with pytest.raises(ValueError):
        _normalize_ts_code("AAPL")
    with pytest.raises(ValueError, match="conflicts"):
        _normalize_ts_code("603629.SZ")
    with pytest.raises(ValueError):
        _normalize_ts_code("prefix603629.SH")


def test_provider_requires_token_or_injected_query(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = CnTushareProvider(token="")
    assert provider.configured is False
    with pytest.raises(NotImplementedError, match="TUSHARE_TOKEN"):
        provider._pro()


def test_registry_only_enables_tushare_when_token_is_present(monkeypatch):
    from tradingagents.dataflows.providers.registry import build_default_registry

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert "cn_tushare" not in build_default_registry().list_names()

    monkeypatch.setenv("TUSHARE_TOKEN", "configured-for-test")
    assert "cn_tushare" in build_default_registry().list_names()


def test_ohlcv_converts_source_units_and_uses_latest_factor_by_date():
    daily = pd.DataFrame(
        [
            {
                "trade_date": "20260810",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "pre_close": 10.0,
                "vol": 12.0,
                "amount": 34.0,
            },
            {
                "trade_date": "20260808",
                "open": 5.0,
                "high": 5.5,
                "low": 4.5,
                "close": 5.0,
                "pre_close": 5.0,
                "vol": 7.0,
                "amount": 8.0,
            },
        ]
    )
    # Deliberately return the oldest factor first. The latest date, not API row
    # order, must anchor the qfq calculation.
    factors = pd.DataFrame(
        [
            {"trade_date": "20260808", "adj_factor": 1.0},
            {"trade_date": "20260810", "adj_factor": 2.0},
        ]
    )
    provider = CnTushareProvider(
        query_fn=_query_from({"daily": daily, "adj_factor": factors})
    )

    payload = provider.get_stock_data("603629.SH", "2026-08-01", "2026-08-10")

    assert "volume_unit=shares amount_unit=CNY" in payload
    rows = pd.read_csv(pd.io.common.StringIO(payload.split("\n\n", 1)[1]))
    rows["Date"] = rows["Date"].astype(str)
    old = rows.loc[rows["Date"] == "2026-08-08"].iloc[0]
    latest = rows.loc[rows["Date"] == "2026-08-10"].iloc[0]
    assert old["Close"] == 2.5
    assert latest["Close"] == 10.5
    assert old["Volume"] == 700
    assert old["Amount"] == 8000


def test_ohlcv_without_adjustment_factors_falls_back_instead_of_returning_raw_prices():
    daily = pd.DataFrame(
        [{"trade_date": "20260810", "open": 10, "high": 11, "low": 9, "close": 10}]
    )
    provider = CnTushareProvider(query_fn=_query_from({"daily": daily}))

    with pytest.raises(NotImplementedError, match="adjustment factors unavailable"):
        provider.get_stock_data("603629.SH", "2026-08-10", "2026-08-10")


def test_ohlcv_with_partial_adjustment_factors_falls_back():
    daily = pd.DataFrame(
        [
            {"trade_date": "20260809", "close": 9},
            {"trade_date": "20260810", "close": 10},
        ]
    )
    factors = pd.DataFrame([{"trade_date": "20260810", "adj_factor": 2}])
    provider = CnTushareProvider(
        query_fn=_query_from({"daily": daily, "adj_factor": factors})
    )

    with pytest.raises(NotImplementedError, match="adjustment factors incomplete"):
        provider.get_stock_data("603629.SH", "2026-08-09", "2026-08-10")


def test_financial_statement_filters_future_filing_and_prefers_latest_update():
    income = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "ann_date": "20250420",
                "f_ann_date": "20250420",
                "end_date": "20250331",
                "report_type": "1",
                "comp_type": "1",
                "total_revenue": 700,
                "n_income_attr_p": 70,
                "update_flag": "0",
            },
            {
                "ts_code": "603629.SH",
                "ann_date": "20250422",
                "f_ann_date": "20250422",
                "end_date": "20250331",
                "report_type": "1",
                "comp_type": "1",
                "total_revenue": 710,
                "n_income_attr_p": 71,
                "update_flag": "1",
            },
            {
                "ts_code": "603629.SH",
                "ann_date": "20250830",
                "f_ann_date": "20250830",
                "end_date": "20250630",
                "report_type": "1",
                "comp_type": "1",
                "total_revenue": 1500,
                "n_income_attr_p": 150,
                "update_flag": "1",
            },
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"income": income}))

    payload = provider.get_income_statement("603629.SH", curr_date="2025-07-01")

    assert "2025-04-22" in payload
    assert "2025-08-30" not in payload
    table = pd.read_csv(
        pd.io.common.StringIO(payload), sep="|", skiprows=4, engine="python"
    )
    numeric_text = " ".join(table.astype(str).to_numpy().ravel())
    assert "710" in numeric_text
    assert "700" not in numeric_text


def test_financial_statement_prefers_adjusted_consolidated_revision():
    income = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "ann_date": "20260420",
                "f_ann_date": "20260420",
                "end_date": "20251231",
                "report_type": "1",
                "total_revenue": 100,
                "n_income_attr_p": 10,
                "update_flag": "1",
            },
            {
                "ts_code": "603629.SH",
                "ann_date": "20260425",
                "f_ann_date": "20260425",
                "end_date": "20251231",
                "report_type": "4",
                "total_revenue": 120,
                "n_income_attr_p": 12,
                "update_flag": "1",
            },
            {
                "ts_code": "603629.SH",
                "ann_date": "20260426",
                "f_ann_date": "20260426",
                "end_date": "20251231",
                "report_type": "2",
                "total_revenue": 999,
                "n_income_attr_p": 99,
                "update_flag": "1",
            },
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"income": income}))

    payload = provider.get_income_statement("603629.SH", curr_date="2026-05-01")

    assert "120" in payload
    assert "100" not in payload
    assert "999" not in payload


def test_tushare_financials_keep_fy_cumulative_and_derive_q4_only_from_q3():
    income = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "ann_date": "20251030",
                "f_ann_date": "20251030",
                "end_date": "20250930",
                "report_type": "1",
                "comp_type": "1",
                "total_revenue": 24.62342,
                "n_income_attr_p": 2.0,
                "update_flag": "1",
            },
            {
                "ts_code": "603629.SH",
                "ann_date": "20260420",
                "f_ann_date": "20260420",
                "end_date": "20251231",
                "report_type": "1",
                "comp_type": "1",
                "total_revenue": 33.074,
                "n_income_attr_p": 3.0,
                "update_flag": "1",
            },
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"income": income}))
    payload = provider.get_income_statement("603629.SH", curr_date="2026-05-01")
    facts = normalize_financial_markdown(
        payload, statement_type="income_statement", source="cn_tushare"
    )

    fy = next(
        fact
        for fact in facts
        if fact.metric == "revenue" and fact.report_date == "2025-12-31"
    )
    q4 = next(
        fact
        for fact in derive_single_quarters(facts)
        if fact.metric == "revenue" and fact.report_date == "2025-12-31"
    )
    assert fy.period_scope == FY_YTD
    assert fy.value == 33.074
    assert q4.period_scope == SINGLE_QUARTER
    assert q4.value == pytest.approx(8.45058, abs=0.00005)


def test_company_identity_comes_from_structured_profile_not_llm_guess():
    basic = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "symbol": "603629",
                "name": "利通电子",
                "area": "江苏",
                "industry": "元器件",
                "market": "主板",
                "exchange": "SSE",
                "list_status": "L",
                "list_date": "20181224",
            }
        ]
    )
    company = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "exchange": "SSE",
                "province": "江苏",
                "city": "无锡",
                "main_business": "精密金属结构件及算力相关业务",
                "business_scope": "电子元器件、通信设备及算力服务",
                "introduction": "江苏利通电子股份有限公司",
            }
        ]
    )
    provider = CnTushareProvider(
        query_fn=_query_from({"stock_basic": basic, "stock_company": company})
    )

    payload = provider.get_fundamentals(
        "603629.SH", curr_date=datetime.now().strftime("%Y-%m-%d")
    )

    assert "利通电子" in payload
    assert "元器件" in payload
    assert "算力相关业务" in payload
    assert "化工" not in payload
    profile = extract_profile_from_fundamentals(payload)
    identity = build_instrument_identity(
        "603629.SH", profile, source="cn_tushare_company_profile"
    )
    assert profile == {
        "symbol": "603629.SH",
        "security_name": "利通电子",
        "industry": "元器件",
        "main_business": "精密金属结构件及算力相关业务",
    }
    assert identity.status == "HAS_DATA"
    assert identity.commercial_analysis_allowed is True


def test_company_identity_failure_triggers_provider_fallback():
    basic = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "symbol": "603629",
                "name": "利通电子",
                "industry": "元器件",
                "market": "主板",
                "exchange": "SSE",
                "list_status": "L",
                "list_date": "20181224",
            }
        ]
    )

    def query(endpoint: str, **_kwargs):
        if endpoint == "stock_basic":
            return basic
        if endpoint == "stock_company":
            raise RuntimeError("temporary permission error")
        return pd.DataFrame()

    provider = CnTushareProvider(query_fn=query)
    with pytest.raises(RuntimeError, match="temporary permission error"):
        provider.get_fundamentals(
            "603629.SH", curr_date=datetime.now().strftime("%Y-%m-%d")
        )


def test_current_company_profile_is_rejected_for_historical_replay():
    basic = pd.DataFrame(
        [{"ts_code": "603629.SH", "exchange": "SSE", "list_date": "20181224"}]
    )
    company = pd.DataFrame([{"ts_code": "603629.SH", "exchange": "SSE"}])
    provider = CnTushareProvider(
        query_fn=_query_from({"stock_basic": basic, "stock_company": company})
    )

    with pytest.raises(NotImplementedError, match="cannot prove historical company identity"):
        provider.get_fundamentals("603629.SH", curr_date="2025-12-31")


def test_company_profile_is_rejected_before_listing_date():
    basic = pd.DataFrame(
        [{"ts_code": "603629.SH", "exchange": "SSE", "list_date": "20181224"}]
    )
    company = pd.DataFrame([{"ts_code": "603629.SH", "exchange": "SSE"}])
    provider = CnTushareProvider(
        query_fn=_query_from({"stock_basic": basic, "stock_company": company})
    )

    with pytest.raises(NotImplementedError, match="unavailable before listing"):
        provider.get_fundamentals("603629.SH", curr_date="2018-01-01")


def test_optional_fundamental_failure_is_exposed_as_partial_data():
    basic = pd.DataFrame(
        [{"ts_code": "603629.SH", "exchange": "SSE", "list_date": "20181224"}]
    )
    company = pd.DataFrame([{"ts_code": "603629.SH", "exchange": "SSE"}])

    def query(endpoint: str, **_kwargs):
        if endpoint == "stock_basic":
            return basic
        if endpoint == "stock_company":
            return company
        if endpoint == "daily_basic":
            raise RuntimeError("permission denied")
        return pd.DataFrame()

    provider = CnTushareProvider(query_fn=query)
    payload = provider.get_fundamentals(
        "603629.SH", curr_date=datetime.now().strftime("%Y-%m-%d")
    )

    assert "data_quality=PARTIAL" in payload
    assert "optional_endpoints=daily_basic" in payload
    assert "permission denied" not in payload


def test_query_sanitizes_token_before_exception_escapes(monkeypatch):
    secret = "query-path-sensitive-token"
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    def fail(_endpoint: str, **_kwargs):
        raise RuntimeError(f"request URL contains {secret}")

    provider = CnTushareProvider(token=secret, query_fn=fail)
    with pytest.raises(RuntimeError) as caught:
        provider.get_stock_data("603629.SH", "2026-08-01", "2026-08-10")

    assert secret not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)


def test_statement_rejects_non_cumulative_report_types():
    income = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "ann_date": "20260420",
                "f_ann_date": "20260420",
                "end_date": "20251231",
                "report_type": "2",
                "total_revenue": 999,
                "n_income_attr_p": 99,
            }
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"income": income}))

    with pytest.raises(NotImplementedError, match="returned no data"):
        provider.get_income_statement("603629.SH", curr_date="2026-05-01")


def test_statement_rejects_metadata_only_rows():
    income = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "ann_date": "20260420",
                "f_ann_date": "20260420",
                "end_date": "20251231",
                "report_type": "1",
                "update_flag": "1",
            }
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"income": income}))

    with pytest.raises(NotImplementedError, match="without accounting metrics"):
        provider.get_income_statement("603629.SH", curr_date="2026-05-01")


def test_statement_markdown_preserves_financial_precision():
    income = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "ann_date": "20260420",
                "f_ann_date": "20260420",
                "end_date": "20251231",
                "report_type": "1",
                "total_revenue": 1000000456.78,
                "n_income_attr_p": 12376602856876.87,
                "update_flag": "1",
            }
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"income": income}))

    payload = provider.get_income_statement("603629.SH", curr_date="2026-05-01")
    facts = normalize_financial_markdown(
        payload, statement_type="income_statement", source="cn_tushare"
    )

    values = {fact.metric: fact.value for fact in facts}
    assert values["revenue"] == 1000000456.78
    assert values["net_profit"] == 12376602856876.87


def test_statement_preserves_valid_zero_valued_accounting_metrics():
    income = pd.DataFrame(
        [
            {
                "ts_code": "603629.SH",
                "ann_date": "20260420",
                "f_ann_date": "20260420",
                "end_date": "20251231",
                "report_type": "1",
                "total_revenue": 0,
                "n_income_attr_p": 0,
                "update_flag": "1",
            }
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"income": income}))

    payload = provider.get_income_statement("603629.SH", curr_date="2026-05-01")

    assert "营业总收入" in payload
    assert "归属于母公司股东的净利润" in payload
    assert re.search(r"\|\s+0\s+\|", payload)


def test_fund_flow_labels_unit_and_individual_scope():
    frame = pd.DataFrame(
        [
            {
                "trade_date": "20260810",
                "net_mf_amount": 123.45,
                "buy_sm_amount": 1,
                "sell_sm_amount": 2,
                "buy_md_amount": 3,
                "sell_md_amount": 4,
                "buy_lg_amount": 5,
                "sell_lg_amount": 6,
                "buy_elg_amount": 7,
                "sell_elg_amount": 8,
            }
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"moneyflow": frame}))

    payload = provider.get_individual_fund_flow("603629.SH")

    assert "FUND_FLOW_HAS_DATA" in payload
    assert "金额单位：万元" in payload
    assert "非板块资金流" in payload
    assert "主力净流入(大单+超大单,万元)" in payload
    assert "-2" in payload
    assert "净流入额(L2总口径,万元)" in payload


def test_fund_flow_requires_components_for_validated_main_force_metric():
    provider = CnTushareProvider(
        query_fn=_query_from(
            {"moneyflow": pd.DataFrame([{"trade_date": "20260810", "net_mf_amount": 1}])}
        )
    )

    with pytest.raises(NotImplementedError, match="large/extra-large"):
        provider.get_individual_fund_flow("603629.SH")


def test_fund_flow_rejects_rows_with_null_required_components():
    frame = pd.DataFrame(
        [
            {
                "trade_date": "20260810",
                "buy_lg_amount": None,
                "sell_lg_amount": 6,
                "buy_elg_amount": 7,
                "sell_elg_amount": 8,
            }
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"moneyflow": frame}))

    with pytest.raises(NotImplementedError, match="no rows with complete"):
        provider.get_individual_fund_flow("603629.SH")


def test_fund_flow_drops_only_incomplete_rows_and_discloses_count():
    frame = pd.DataFrame(
        [
            {
                "trade_date": "20260810",
                "buy_lg_amount": None,
                "sell_lg_amount": 6,
                "buy_elg_amount": 7,
                "sell_elg_amount": 8,
            },
            {
                "trade_date": "20260809",
                "buy_lg_amount": 5,
                "sell_lg_amount": 6,
                "buy_elg_amount": 7,
                "sell_elg_amount": 8,
            },
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"moneyflow": frame}))

    payload = provider.get_individual_fund_flow("603629.SH")

    assert "dropped_incomplete_rows=1" in payload
    assert "20260810" not in payload
    assert "20260809" in payload


def test_lhb_and_margin_empty_results_are_normal_no_data():
    provider = CnTushareProvider(query_fn=_query_from({}))

    assert "LHB_NOT_QUERIED" in provider.get_lhb_detail(
        "603629.SH", "2026-08-10", force=False
    )
    assert "LHB_NORMAL_NO_DATA" in provider.get_lhb_detail(
        "603629.SH", "2026-08-10", force=True
    )
    assert "MARGIN_NORMAL_NO_DATA" in provider.get_margin_trading("603629.SH")


def test_buyback_queries_by_date_and_filters_mixed_symbols_locally():
    calls: list[dict] = []
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260801",
                "vol": 999,
                "amount": 9999,
            },
            {
                "ts_code": "603629.SH",
                "ann_date": "20260802",
                "vol": 10,
                "amount": 100,
            },
        ]
    )

    def query(endpoint: str, **kwargs):
        calls.append({"endpoint": endpoint, **kwargs})
        return frame.copy()

    payload = CnTushareProvider(query_fn=query).get_buybacks("603629.SH")

    assert calls[0]["endpoint"] == "repurchase"
    assert "ts_code" not in calls[0]
    assert "603629.SH" in payload
    assert "000001.SZ" not in payload
    assert "9999" not in payload


def test_buyback_chunks_all_market_query_before_declaring_no_data():
    calls: list[dict] = []

    def query(endpoint: str, **kwargs):
        calls.append({"endpoint": endpoint, **kwargs})
        if len(calls) == 3:
            return pd.DataFrame(
                [
                    {
                        "ts_code": "603629.SH",
                        "ann_date": kwargs["end_date"],
                        "vol": 10,
                        "amount": 100,
                    }
                ]
            )
        return pd.DataFrame(
            [
                {
                    "ts_code": f"{index:06d}.SZ",
                    "ann_date": kwargs["end_date"],
                }
                for index in range(100)
            ]
        )

    payload = CnTushareProvider(query_fn=query).get_buybacks("603629.SH")

    assert len(calls) > 3
    assert all("ts_code" not in call for call in calls)
    assert "BUYBACK_HAS_DATA" in payload
    assert "603629.SH" in payload


def test_buyback_fails_closed_when_single_day_response_is_still_capped():
    calls: list[dict] = []

    def query(endpoint: str, **kwargs):
        calls.append({"endpoint": endpoint, **kwargs})
        return pd.DataFrame(
            [
                {
                    "ts_code": f"{index:06d}.SZ",
                    "ann_date": kwargs["end_date"],
                }
                for index in range(2000)
            ]
        )

    payload = CnTushareProvider(query_fn=query).get_buybacks("603629.SH")

    assert len(calls) > 1
    assert "BUYBACK_FAILED" in payload
    assert "row limit" in payload
    assert "BUYBACK_NORMAL_NO_DATA" not in payload


def test_buyback_missing_symbol_column_fails_closed():
    frame = pd.DataFrame([{"ann_date": "20260801", "vol": 10, "amount": 100}])
    payload = CnTushareProvider(
        query_fn=_query_from({"repurchase": frame})
    ).get_buybacks("603629.SH")

    assert "BUYBACK_FAILED" in payload
    assert "lacks ts_code" in payload


def test_lhb_converts_tushare_yuan_amounts_to_ten_thousand_yuan():
    frame = pd.DataFrame(
        [
            {
                "trade_date": "20260810",
                "ts_code": "603629.SH",
                "name": "利通电子",
                "amount": 100000000,
                "l_buy": 30000000,
                "l_sell": 10000000,
                "net_amount": 20000000,
            }
        ]
    )
    provider = CnTushareProvider(query_fn=_query_from({"top_list": frame}))

    payload = provider.get_lhb_detail("603629.SH", "2026-08-10", force=True)

    assert "成交额(万元)" in payload
    assert "10000" in payload
    assert "3000" in payload
    assert "2000" in payload


def test_collector_classifies_empty_fund_flow_and_margin_as_normal_no_data():
    assert DataCollector._infer_source_status(
        "603629.SH [DATA-TUSHARE] FUND_FLOW_NORMAL_NO_DATA: 近期无记录"
    ) == "NORMAL_NO_DATA"
    assert DataCollector._infer_source_status(
        "603629.SH [DATA-010] MARGIN_NORMAL_NO_DATA: 近期无记录"
    ) == "NORMAL_NO_DATA"


def test_financial_raw_evidence_uses_actual_routed_vendor():
    collector = DataCollector()
    key = make_cache_key("603629.SH", "2026-08-10")
    collector._cache[key] = {
        "fundamentals": "## Fundamentals\n<!-- source=tushare endpoints=stock_basic,stock_company -->\n| item | value |\n|---|---|\n| 股票代码 | 603629.SH |",
        "balance_sheet": "## Balance Sheet\n<!-- source=tushare endpoint=balancesheet -->\n| 报告日 | 资产总计 |\n|---|---:|\n| 2025-12-31 | 1 |",
        "cashflow": "## Cashflow\n<!-- source=tushare endpoint=cashflow -->\n| 报告日 | 经营现金流 |\n|---|---:|\n| 2025-12-31 | 1 |",
        "income_statement": "## Income\n<!-- source=tushare endpoint=income -->\n| 报告日 | 营业收入 |\n|---|---:|\n| 2025-12-31 | 1 |",
        "company_profile": {
            "raw": {"symbol": "603629.SH"},
            "vendor": "cn_tushare",
            "endpoint": "stock_basic + stock_company",
        },
        "instrument_identity": {"status": "HAS_DATA"},
        "_provider_hits": {
            "fundamentals": "cn_tushare",
            "balance_sheet": "cn_tushare",
            "cashflow": "cn_tushare",
            "income_statement": "cn_tushare",
        },
    }
    _last_hit_vendor.update(
        {
            "get_fundamentals": "cn_tushare",
            "get_balance_sheet": "cn_tushare",
            "get_cashflow": "cn_tushare",
            "get_income_statement": "cn_tushare",
        }
    )
    try:
        evidence = collector.build_raw_evidence("603629.SH", "2026-08-10")
    finally:
        _last_hit_vendor.clear()

    for key_name in ("fundamentals", "balance_sheet", "cashflow", "income_statement", "company_profile"):
        assert evidence[key_name]["vendor"] == "cn_tushare"
    assert evidence["fundamentals"]["endpoint"] == "stock_basic + stock_company"
    assert evidence["company_profile"]["endpoint"] == "stock_basic + stock_company"
    assert evidence["balance_sheet"]["endpoint"] == "balancesheet"
    assert evidence["cashflow"]["endpoint"] == "cashflow"
    assert evidence["income_statement"]["endpoint"] == "income"


def test_thread_local_vendor_provenance_is_isolated_between_workers():
    barrier = threading.Barrier(2)

    def worker(vendor: str) -> str:
        clear_current_thread_hit_vendor("get_fundamentals")
        _record_hit_vendor("get_fundamentals", vendor)
        barrier.wait(timeout=2)
        return get_current_thread_hit_vendor("get_fundamentals")

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(worker, ("cn_tushare", "cn_akshare")))

    assert values == ["cn_tushare", "cn_akshare"]


def test_provider_errors_redact_token(monkeypatch):
    secret = "sensitive-tushare-token"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)

    def fail(_endpoint: str, **_kwargs):
        raise RuntimeError(f"upstream rejected {secret}")

    provider = CnTushareProvider(token=secret, query_fn=fail)
    payload = provider.get_lhb_detail("603629.SH", "2026-08-10", force=True)

    assert secret not in payload
    assert "[REDACTED]" in payload


def test_constructor_token_is_redacted_without_matching_environment(monkeypatch):
    secret = "constructor-only-sensitive-token"
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    def fail(_endpoint: str, **_kwargs):
        raise RuntimeError(f"upstream rejected {secret}")

    provider = CnTushareProvider(token=secret, query_fn=fail)
    payload = provider.get_margin_trading("603629.SH")

    assert secret not in payload
    assert "[REDACTED]" in payload
    assert "MARGIN_FAILED" in payload


def test_ohlcv_header_exposes_adjustment_to_raw_evidence_parser():
    daily = pd.DataFrame(
        [{"trade_date": "20260810", "open": 10, "high": 11, "low": 9, "close": 10}]
    )
    factors = pd.DataFrame([{"trade_date": "20260810", "adj_factor": 2}])
    provider = CnTushareProvider(
        query_fn=_query_from({"daily": daily, "adj_factor": factors})
    )

    payload = provider.get_stock_data("603629.SH", "2026-08-10", "2026-08-10")

    assert "# [DATA-P0-603629] adjustment=qfq_as_of_end_date" in payload


@pytest.mark.parametrize("data_type", ["financials", "fund_flow", "lhb", "margin_trading"])
def test_tushare_is_primary_for_verified_structured_data(data_type):
    source = get_primary_source(data_type)
    assert source is not None
    assert source.vendor == "cn_tushare"


def test_token_is_never_committed_as_a_project_file():
    assert not os.path.exists("TUSHARE_TOKEN")
