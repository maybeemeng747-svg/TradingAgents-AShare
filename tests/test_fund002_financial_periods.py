from tradingagents.dataflows.financial_periods import (
    FIELD_MISSING,
    POINT_IN_TIME,
    SINGLE_QUARTER,
    derive_single_quarters,
    normalize_financial_markdown,
    normalize_financial_records,
    period_scope_for_date,
    render_financial_period_context,
)
from unittest.mock import patch


def _income_records():
    return [
        {"报告日": "2025-03-31", "营业总收入": "7.04342"},
        {"报告日": "2025-06-30", "营业总收入": "15.16342"},
        {"报告日": "2025-09-30", "营业总收入": "24.62342"},
        {"报告日": "2025-12-31", "营业总收入": "33.07400"},
        {"报告日": "2026-03-31", "营业总收入": "9.97438"},
    ]


def test_603629_q4_is_fy_minus_q3_not_fy_value():
    facts = normalize_financial_records(
        _income_records(), statement_type="income_statement", source="fixture"
    )
    derived = derive_single_quarters(facts)
    q4 = next(
        item for item in derived
        if item.metric == "revenue" and item.report_date == "2025-12-31"
    )
    assert q4.period_scope == SINGLE_QUARTER
    assert q4.value == 8.45058
    assert q4.value != 33.074
    assert q4.formula == "FY_YTD-Q3_YTD"
    assert q4.is_derived is True


def test_missing_prior_cumulative_value_never_guesses_q4():
    facts = normalize_financial_records(
        [
            {"报告日": "2025-03-31", "营业总收入": "7"},
            {"报告日": "2025-12-31", "营业总收入": "33"},
        ],
        statement_type="income_statement",
        source="fixture",
        observed_at="2026-07-30",
    )
    q4 = next(item for item in derive_single_quarters(facts) if item.report_date == "2025-12-31")
    assert q4.value is None
    assert q4.status == FIELD_MISSING


def test_cashflow_uses_cumulative_scopes_too():
    facts = normalize_financial_records(
        [
            {"报告日": "2025-03-31", "经营活动产生的现金流量净额": "2"},
            {"报告日": "2025-06-30", "经营活动产生的现金流量净额": "5"},
        ],
        statement_type="cashflow",
        source="fixture",
    )
    q2 = next(item for item in derive_single_quarters(facts) if item.report_date == "2025-06-30")
    assert q2.metric == "operating_cashflow"
    assert q2.value == 3.0


def test_disclosure_date_provenance_distinguishes_actual_from_inferred():
    facts = normalize_financial_records(
        [
            {
                "报告日": "2026-03-31",
                "公告日期": "2026-04-25",
                "营业总收入": "10",
            },
            {
                "报告日": "2025-03-31",
                "营业总收入": "8",
            },
        ],
        statement_type="income_statement",
        source="fixture",
        observed_at="2026-07-30",
    )
    by_period = {fact.report_date: fact for fact in facts}
    assert by_period["2026-03-31"].disclosure_date == "2026-04-25"
    assert by_period["2026-03-31"].disclosure_date_inferred is False
    assert by_period["2025-03-31"].disclosure_date == "2026-07-30"
    assert by_period["2025-03-31"].disclosure_date_inferred is True


def test_actual_announcement_date_wins_regardless_of_column_order():
    facts = normalize_financial_records(
        [
            {
                "报告日": "2026-03-31",
                "公告日期": "2026-04-20",
                "实际公告日期": "2026-04-25",
                "营业总收入": "10",
            }
        ],
        statement_type="income_statement",
        source="tushare_pro",
    )
    assert facts[0].disclosure_date == "2026-04-25"
    assert facts[0].disclosure_date_inferred is False


def test_balance_sheet_is_point_in_time_not_cumulative():
    assert period_scope_for_date("2025-12-31", "balance_sheet") == POINT_IN_TIME
    facts = normalize_financial_records(
        [{"报告日": "2025-12-31", "营业总收入": "33"}],
        statement_type="balance_sheet",
        source="fixture",
    )
    assert facts[0].period_scope == POINT_IN_TIME
    assert derive_single_quarters(facts) == []


def test_cross_year_data_does_not_mix_derivation_inputs():
    facts = normalize_financial_records(
        [
            {"报告日": "2024-09-30", "营业总收入": "20"},
            {"报告日": "2025-12-31", "营业总收入": "33"},
        ],
        statement_type="income_statement",
        source="fixture",
    )
    q4 = next(item for item in derive_single_quarters(facts) if item.report_date == "2025-12-31")
    assert q4.value is None
    assert q4.status == FIELD_MISSING


def test_render_context_accepts_collector_dict_sidecars():
    facts = normalize_financial_records(
        [{"报告日": "2025-03-31", "营业总收入": "7"}],
        statement_type="income_statement",
        source="fixture",
    )
    text = render_financial_period_context([fact.to_dict() for fact in facts])
    assert "revenue 2025-03-31 [Q1_YTD]" in text


def test_scientific_notation_keeps_exponent_and_unit_scale():
    facts = normalize_financial_records(
        [
            {
                "报告日": "2026-03-31",
                "营业总收入": "1.97276e+09",
                "归属于母公司股东的净利润": "2.98935e+08",
            }
        ],
        statement_type="income_statement",
        source="fixture",
    )
    by_metric = {fact.metric: fact.value for fact in facts}
    assert by_metric["revenue"] == 1_972_760_000.0
    assert by_metric["net_profit"] == 298_935_000.0


def test_parent_company_profit_alias_wins_over_plain_net_profit_column_order():
    facts = normalize_financial_records(
        [
            {
                "报告日": "2026-03-31",
                "净利润": 298_935_474.3,
                "归属于母公司所有者的净利润": 266_687_960.67,
            }
        ],
        statement_type="income_statement",
        source="fixture",
    )
    net_profit = next(fact for fact in facts if fact.metric == "net_profit")
    assert net_profit.value == 266_687_960.67


def test_balance_sheet_normalizes_equity_receivables_and_inventory():
    facts = normalize_financial_records(
        [
            {
                "报告日": "2026-03-31",
                "资产总计": "1.7654e+10",
                "负债合计": "6.3021e+09",
                "所有者权益合计": "1.14519e+10",
                "归属于母公司股东权益合计": "1.13519e+10",
                "应收账款": "2.4e+09",
                "存货": "1.1e+09",
            }
        ],
        statement_type="balance_sheet",
        source="fixture",
    )
    by_metric = {fact.metric: fact.value for fact in facts}
    assert by_metric["total_assets"] == 17_654_000_000.0
    assert by_metric["total_equity"] == 11_451_900_000.0
    assert by_metric["parent_equity"] == 11_351_900_000.0
    assert by_metric["accounts_receivable"] == 2_400_000_000.0
    assert by_metric["inventory"] == 1_100_000_000.0


def test_balance_sheet_preserves_fixed_assets():
    facts = normalize_financial_records(
        [
            {
                "报告日": "2026-03-31",
                "公告日期": "2026-04-25",
                "固定资产": "12376602856.87",
            }
        ],
        statement_type="balance_sheet",
        source="fixture",
        observed_at="2026-07-31",
    )
    fixed_assets = next(fact for fact in facts if fact.metric == "fixed_assets")
    assert fixed_assets.value == 12376602856.87
    assert fixed_assets.period_scope == POINT_IN_TIME


def test_balance_sheet_accepts_legacy_net_fixed_assets_label():
    facts = normalize_financial_records(
        [
            {
                "报告日": "2026-03-31",
                "公告日期": "2026-04-25",
                "固定资产净额": "12376602856.87",
            }
        ],
        statement_type="balance_sheet",
        source="fixture",
        observed_at="2026-07-31",
    )
    fixed_assets = next(fact for fact in facts if fact.metric == "fixed_assets")
    assert fixed_assets.value == 12376602856.87
    assert fixed_assets.period_scope == POINT_IN_TIME


def test_balance_sheet_accepts_tushare_fixed_assets_total_label():
    facts = normalize_financial_records(
        [
            {
                "报告日": "2026-03-31",
                "实际公告日期": "2026-04-25",
                "固定资产合计": "12376602856.87",
            }
        ],
        statement_type="balance_sheet",
        source="tushare_pro",
    )
    fixed_assets = next(fact for fact in facts if fact.metric == "fixed_assets")
    assert fixed_assets.value == 12376602856.87
    assert fixed_assets.disclosure_date == "2026-04-25"


def test_astock_income_fallback_preserves_report_date_for_normalization():
    from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider

    provider = CnAstockProvider()
    records = [
        {
            "report_date": "20260331",
            "营业总收入": "1.97276e+09",
            "营业收入": "1.97276e+09",
            "营业总成本": "1.5e+09",
            "营业成本": "1.4e+09",
            "归属于母公司所有者的净利润": "2.6668796067e+08",
        }
    ]
    with patch.object(provider, "_sina_financial_report", return_value=records):
        markdown = provider.get_income_statement("002409.SZ")

    facts = normalize_financial_markdown(
        markdown,
        statement_type="income_statement",
        source="cn_astock",
    )
    by_metric = {fact.metric: fact for fact in facts}
    assert by_metric["revenue"].report_date == "2026-03-31"
    assert abs(by_metric["net_profit"].value - 266_687_960.67) < 100
