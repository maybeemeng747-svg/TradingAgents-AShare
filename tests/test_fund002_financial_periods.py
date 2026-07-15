from tradingagents.dataflows.financial_periods import (
    FIELD_MISSING,
    POINT_IN_TIME,
    SINGLE_QUARTER,
    derive_single_quarters,
    normalize_financial_records,
    period_scope_for_date,
    render_financial_period_context,
)


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
