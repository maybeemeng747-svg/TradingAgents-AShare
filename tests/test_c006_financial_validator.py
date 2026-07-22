# [C-006] financial_validator — Phase 2 tests
"""Tests for C-006 financial_data_validator Phase 2 anomaly detection rules.

Phase 1 rules (gross_margin_jump, cashflow_profit_divergence, high_debt_ratio,
debt_ratio_jump, large_invest_cashflow, large_finance_cashflow) are already
covered in test_fund003_fund004_integrity.py.

Phase 2 adds: extreme_roe, roe_jump, margin_squeeze, negative_cashflow_quality,
excessive_cashflow_quality, ar_revenue_divergence, inventory_cost_divergence.
"""

from tradingagents.agents.utils.financial_validator import (
    check_financial_anomalies,
    format_financial_anomaly_warning,
)
from tradingagents.agents.utils.fundamental_integrity import (
    extract_financial_anomaly_inputs,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _base_facts(
    revenue=33074.0,
    cost=19844.0,
    net_profit=2000.0,
    operating_cf=-500.0,
    assets=100000.0,
    liabilities=75000.0,
    report_date="2025-12-31",
    period_scope="FY_YTD",
    unit="万元",
):
    """Minimal fact set for Phase 1 + 2 tests."""
    return [
        {"metric": "revenue", "report_date": report_date, "period_scope": period_scope,
         "value": revenue, "unit": unit, "status": "HAS_DATA"},
        {"metric": "operating_cost", "report_date": report_date, "period_scope": period_scope,
         "value": cost, "unit": unit, "status": "HAS_DATA"},
        {"metric": "net_profit", "report_date": report_date, "period_scope": period_scope,
         "value": net_profit, "unit": unit, "status": "HAS_DATA"},
        {"metric": "operating_cashflow", "report_date": report_date, "period_scope": period_scope,
         "value": operating_cf, "unit": unit, "status": "HAS_DATA"},
        {"metric": "total_assets", "report_date": report_date, "period_scope": "POINT_IN_TIME",
         "value": assets, "unit": unit, "status": "HAS_DATA"},
        {"metric": "total_liabilities", "report_date": report_date, "period_scope": "POINT_IN_TIME",
         "value": liabilities, "unit": unit, "status": "HAS_DATA"},
    ]


# ── Phase 1 backward compatibility ──────────────────────────────────────────


class TestPhase1BackwardCompatibility:
    """Phase 1 rules still work with Phase 2 signature."""

    def test_gross_margin_jump(self):
        r = check_financial_anomalies("X", gross_margin=40.0, gross_margin_prev=15.0)
        assert "gross_margin_jump" in r["anomalies"]

    def test_cashflow_profit_divergence(self):
        r = check_financial_anomalies("X", net_profit=10.0, operating_cashflow=-5.0)
        assert "cashflow_profit_divergence" in r["anomalies"]

    def test_high_debt_ratio(self):
        r = check_financial_anomalies("X", debt_ratio=75.0)
        assert "high_debt_ratio" in r["anomalies"]

    def test_debt_ratio_jump(self):
        r = check_financial_anomalies("X", debt_ratio=60.0, debt_ratio_prev=45.0)
        assert "debt_ratio_jump" in r["anomalies"]

    def test_large_invest_cashflow(self):
        r = check_financial_anomalies("X", total_invest_cashflow=60.0)
        assert "large_invest_cashflow" in r["anomalies"]

    def test_large_finance_cashflow(self):
        r = check_financial_anomalies("X", total_finance_cashflow=-55.0)
        assert "large_finance_cashflow" in r["anomalies"]

    def test_no_anomaly_clean_input(self):
        r = check_financial_anomalies("X", gross_margin=35.0, gross_margin_prev=33.0)
        assert r["has_anomaly"] is False
        assert r["anomalies"] == []

    def test_all_none_returns_no_anomaly(self):
        r = check_financial_anomalies("X")
        assert r["has_anomaly"] is False

    def test_backward_compat_new_params_default_none(self):
        """New Phase 2 params default to None — old callers unaffected."""
        r = check_financial_anomalies(
            "X", gross_margin=40.0, gross_margin_prev=15.0,
            # Phase 2 params not passed
        )
        assert "gross_margin_jump" in r["anomalies"]
        assert "extreme_roe" not in r["anomalies"]


# ── Phase 2: ROE anomaly ────────────────────────────────────────────────────


class TestROEAnomaly:
    def test_extreme_roe_positive(self):
        r = check_financial_anomalies("X", roe=55.0)
        assert "extreme_roe" in r["anomalies"]
        assert "55.0%" in r["anomaly_details"]["extreme_roe"]

    def test_extreme_roe_negative(self):
        r = check_financial_anomalies("X", roe=-60.0)
        assert "extreme_roe" in r["anomalies"]

    def test_roe_within_normal_range(self):
        r = check_financial_anomalies("X", roe=15.0)
        assert "extreme_roe" not in r["anomalies"]

    def test_roe_at_boundary_50(self):
        r = check_financial_anomalies("X", roe=50.0)
        assert "extreme_roe" not in r["anomalies"]

    def test_roe_just_above_boundary(self):
        r = check_financial_anomalies("X", roe=50.1)
        assert "extreme_roe" in r["anomalies"]

    def test_roe_jump(self):
        r = check_financial_anomalies("X", roe=30.0, roe_prev=5.0)
        assert "roe_jump" in r["anomalies"]
        assert "25.0" in r["anomaly_details"]["roe_jump"]

    def test_roe_jump_within_threshold(self):
        r = check_financial_anomalies("X", roe=20.0, roe_prev=5.0)
        assert "roe_jump" not in r["anomalies"]

    def test_roe_jump_negative_to_positive(self):
        r = check_financial_anomalies("X", roe=15.0, roe_prev=-10.0)
        assert "roe_jump" in r["anomalies"]

    def test_roe_none_skips_check(self):
        r = check_financial_anomalies("X", roe=None)
        assert "extreme_roe" not in r["anomalies"]
        assert "roe_jump" not in r["anomalies"]

    def test_roe_prev_none_skips_jump(self):
        r = check_financial_anomalies("X", roe=60.0, roe_prev=None)
        assert "extreme_roe" in r["anomalies"]
        assert "roe_jump" not in r["anomalies"]


# ── Phase 2: Margin squeeze ─────────────────────────────────────────────────


class TestMarginSqueeze:
    def test_margin_squeeze_detected(self):
        r = check_financial_anomalies(
            "X",
            revenue_growth=10.0,
            operating_cost_growth=20.0,
            gross_margin=30.0,
            gross_margin_prev=35.0,
        )
        assert "margin_squeeze" in r["anomalies"]
        assert "10.0%" in r["anomaly_details"]["margin_squeeze"]

    def test_margin_squeeze_cost_excess_too_small(self):
        """cost_excess <= 5 should not trigger."""
        r = check_financial_anomalies(
            "X",
            revenue_growth=10.0,
            operating_cost_growth=14.0,
            gross_margin=32.0,
            gross_margin_prev=35.0,
        )
        assert "margin_squeeze" not in r["anomalies"]

    def test_margin_squeeze_margin_drop_too_small(self):
        """margin_drop <= 3 should not trigger."""
        r = check_financial_anomalies(
            "X",
            revenue_growth=10.0,
            operating_cost_growth=20.0,
            gross_margin=33.0,
            gross_margin_prev=35.0,
        )
        assert "margin_squeeze" not in r["anomalies"]

    def test_margin_squeeze_revenue_declining(self):
        """Revenue declining should not trigger margin squeeze."""
        r = check_financial_anomalies(
            "X",
            revenue_growth=-5.0,
            operating_cost_growth=10.0,
            gross_margin=30.0,
            gross_margin_prev=35.0,
        )
        assert "margin_squeeze" not in r["anomalies"]

    def test_margin_squeeze_margin_improving(self):
        """If margin improves, no squeeze."""
        r = check_financial_anomalies(
            "X",
            revenue_growth=10.0,
            operating_cost_growth=5.0,
            gross_margin=38.0,
            gross_margin_prev=35.0,
        )
        assert "margin_squeeze" not in r["anomalies"]

    def test_margin_squeeze_none_fields_skip(self):
        r = check_financial_anomalies(
            "X",
            revenue_growth=None,
            operating_cost_growth=20.0,
            gross_margin=30.0,
            gross_margin_prev=35.0,
        )
        assert "margin_squeeze" not in r["anomalies"]

    def test_margin_squeeze_exactly_at_thresholds(self):
        """cost_excess=5, margin_drop=3 should NOT trigger (strict >)."""
        r = check_financial_anomalies(
            "X",
            revenue_growth=10.0,
            operating_cost_growth=15.0,
            gross_margin=32.0,
            gross_margin_prev=35.0,
        )
        assert "margin_squeeze" not in r["anomalies"]

    def test_margin_squeeze_just_above_thresholds(self):
        r = check_financial_anomalies(
            "X",
            revenue_growth=10.0,
            operating_cost_growth=15.1,
            gross_margin=31.9,
            gross_margin_prev=35.0,
        )
        assert "margin_squeeze" in r["anomalies"]


# ── Phase 2: Cashflow quality ───────────────────────────────────────────────


class TestCashflowQuality:
    def test_negative_cashflow_quality(self):
        r = check_financial_anomalies("X", net_profit=10.0, operating_cashflow=-5.0)
        assert "negative_cashflow_quality" in r["anomalies"]

    def test_excessive_cashflow_quality(self):
        r = check_financial_anomalies("X", net_profit=2.0, operating_cashflow=15.0)
        assert "excessive_cashflow_quality" in r["anomalies"]

    def test_normal_cashflow_quality(self):
        r = check_financial_anomalies("X", net_profit=10.0, operating_cashflow=12.0)
        assert "negative_cashflow_quality" not in r["anomalies"]
        assert "excessive_cashflow_quality" not in r["anomalies"]

    def test_cashflow_quality_ratio_at_5(self):
        """Ratio exactly 5 should not trigger (strict >)."""
        r = check_financial_anomalies("X", net_profit=2.0, operating_cashflow=10.0)
        assert "excessive_cashflow_quality" not in r["anomalies"]

    def test_cashflow_quality_ratio_just_above_5(self):
        r = check_financial_anomalies("X", net_profit=2.0, operating_cashflow=10.01)
        assert "excessive_cashflow_quality" in r["anomalies"]

    def test_cashflow_quality_zero_profit_skips(self):
        """net_profit=0 -> division skipped."""
        r = check_financial_anomalies("X", net_profit=0.0, operating_cashflow=10.0)
        assert "excessive_cashflow_quality" not in r["anomalies"]

    def test_cashflow_quality_none_skips(self):
        r = check_financial_anomalies("X", net_profit=None, operating_cashflow=None)
        assert "negative_cashflow_quality" not in r["anomalies"]

    def test_cashflow_quality_both_positive_ratio_1(self):
        r = check_financial_anomalies("X", net_profit=10.0, operating_cashflow=10.0)
        assert r["has_anomaly"] is False


# ── Phase 2: AR/Revenue divergence ──────────────────────────────────────────


class TestARRevenueDivergence:
    def test_ar_revenue_divergence(self):
        r = check_financial_anomalies(
            "X", accounts_receivable_growth=50.0, revenue_growth=5.0
        )
        assert "ar_revenue_divergence" in r["anomalies"]
        assert "50.0%" in r["anomaly_details"]["ar_revenue_divergence"]

    def test_ar_growth_below_threshold(self):
        r = check_financial_anomalies(
            "X", accounts_receivable_growth=25.0, revenue_growth=5.0
        )
        assert "ar_revenue_divergence" not in r["anomalies"]

    def test_revenue_growth_above_threshold(self):
        r = check_financial_anomalies(
            "X", accounts_receivable_growth=50.0, revenue_growth=15.0
        )
        assert "ar_revenue_divergence" not in r["anomalies"]

    def test_ar_none_skips(self):
        r = check_financial_anomalies(
            "X", accounts_receivable_growth=None, revenue_growth=5.0
        )
        assert "ar_revenue_divergence" not in r["anomalies"]

    def test_revenue_none_skips(self):
        r = check_financial_anomalies(
            "X", accounts_receivable_growth=50.0, revenue_growth=None
        )
        assert "ar_revenue_divergence" not in r["anomalies"]

    def test_ar_declining_no_divergence(self):
        r = check_financial_anomalies(
            "X", accounts_receivable_growth=-10.0, revenue_growth=5.0
        )
        assert "ar_revenue_divergence" not in r["anomalies"]


# ── Phase 2: Inventory/Cost divergence ──────────────────────────────────────


class TestInventoryCostDivergence:
    def test_inventory_cost_divergence(self):
        r = check_financial_anomalies(
            "X", inventory_growth=40.0, operating_cost_growth=5.0
        )
        assert "inventory_cost_divergence" in r["anomalies"]
        assert "40.0%" in r["anomaly_details"]["inventory_cost_divergence"]

    def test_inventory_growth_below_threshold(self):
        r = check_financial_anomalies(
            "X", inventory_growth=20.0, operating_cost_growth=5.0
        )
        assert "inventory_cost_divergence" not in r["anomalies"]

    def test_cost_growth_above_threshold(self):
        r = check_financial_anomalies(
            "X", inventory_growth=40.0, operating_cost_growth=15.0
        )
        assert "inventory_cost_divergence" not in r["anomalies"]

    def test_inventory_none_skips(self):
        r = check_financial_anomalies(
            "X", inventory_growth=None, operating_cost_growth=5.0
        )
        assert "inventory_cost_divergence" not in r["anomalies"]

    def test_cost_none_skips(self):
        r = check_financial_anomalies(
            "X", inventory_growth=40.0, operating_cost_growth=None
        )
        assert "inventory_cost_divergence" not in r["anomalies"]

    def test_inventory_declining_no_divergence(self):
        r = check_financial_anomalies(
            "X", inventory_growth=-10.0, operating_cost_growth=5.0
        )
        assert "inventory_cost_divergence" not in r["anomalies"]


# ── Phase 2: extract_financial_anomaly_inputs integration ────────────────────


class TestPhase2Extraction:
    """Verify extract_financial_anomaly_inputs produces Phase 2 fields."""

    def _facts_with_equity(self, equity=4000.0, report_date="2025-12-31"):
        return _base_facts(report_date=report_date) + [
            {"metric": "total_equity", "report_date": report_date,
             "period_scope": "POINT_IN_TIME", "value": equity,
             "unit": "万元", "status": "HAS_DATA"},
        ]

    def test_roe_extracted(self):
        facts = self._facts_with_equity(equity=4000.0)
        inputs = extract_financial_anomaly_inputs(facts)
        # net_profit=2000, equity=4000 -> ROE = 50%
        assert inputs["roe"] == 50.0

    def test_roe_none_when_no_equity(self):
        facts = _base_facts()
        inputs = extract_financial_anomaly_inputs(facts)
        assert inputs["roe"] is None

    def test_roe_prev_from_prior_period(self):
        facts = self._facts_with_equity(equity=4000.0)
        # Add prior period
        facts.extend([
            {"metric": "net_profit", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 1000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "total_equity", "report_date": "2025-09-30",
             "period_scope": "POINT_IN_TIME", "value": 5000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        # Prior: net_profit=1000, equity=5000 -> ROE = 20%
        assert inputs["roe_prev"] == 20.0

    def test_revenue_growth_extracted(self):
        facts = _base_facts(revenue=33074.0)
        facts.extend([
            {"metric": "revenue", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 24000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 15000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        expected = (33074.0 - 24000.0) / 24000.0 * 100
        assert abs(inputs["revenue_growth"] - expected) < 0.01

    def test_operating_cost_growth_extracted(self):
        facts = _base_facts(cost=19844.0)
        facts.extend([
            {"metric": "revenue", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 24000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 15000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        expected = (19844.0 - 15000.0) / 15000.0 * 100
        assert abs(inputs["operating_cost_growth"] - expected) < 0.01

    def test_growth_none_when_no_prior(self):
        facts = _base_facts()
        inputs = extract_financial_anomaly_inputs(facts)
        assert inputs["revenue_growth"] is None
        assert inputs["operating_cost_growth"] is None

    def test_accounts_receivable_growth_extracted(self):
        facts = _base_facts()
        facts.extend([
            {"metric": "accounts_receivable", "report_date": "2025-12-31",
             "period_scope": "POINT_IN_TIME", "value": 5000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "accounts_receivable", "report_date": "2025-09-30",
             "period_scope": "POINT_IN_TIME", "value": 3000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        expected = (5000.0 - 3000.0) / 3000.0 * 100
        assert abs(inputs["accounts_receivable_growth"] - expected) < 0.01

    def test_inventory_growth_extracted(self):
        facts = _base_facts()
        facts.extend([
            {"metric": "inventory", "report_date": "2025-12-31",
             "period_scope": "POINT_IN_TIME", "value": 8000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "inventory", "report_date": "2025-09-30",
             "period_scope": "POINT_IN_TIME", "value": 5000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        expected = (8000.0 - 5000.0) / 5000.0 * 100
        assert abs(inputs["inventory_growth"] - expected) < 0.01

    def test_ar_growth_none_when_no_data(self):
        facts = _base_facts()
        inputs = extract_financial_anomaly_inputs(facts)
        assert inputs["accounts_receivable_growth"] is None

    def test_inventory_growth_none_when_no_data(self):
        facts = _base_facts()
        inputs = extract_financial_anomaly_inputs(facts)
        assert inputs["inventory_growth"] is None

    def test_phase2_fields_backward_compat(self):
        """Old Phase 1 callers still get all Phase 1 fields."""
        facts = _base_facts()
        inputs = extract_financial_anomaly_inputs(facts)
        # Phase 1 fields exist
        assert "gross_margin" in inputs
        assert "gross_margin_prev" in inputs
        assert "operating_cashflow" in inputs
        assert "net_profit" in inputs
        assert "debt_ratio" in inputs
        assert "debt_ratio_prev" in inputs
        assert "total_invest_cashflow" in inputs
        assert "total_finance_cashflow" in inputs
        # Phase 2 fields exist (even if None)
        assert "roe" in inputs
        assert "roe_prev" in inputs
        assert "revenue_growth" in inputs
        assert "operating_cost_growth" in inputs
        assert "accounts_receivable_growth" in inputs
        assert "inventory_growth" in inputs


# ── Phase 2: End-to-end via extract + check ─────────────────────────────────


class TestPhase2EndToEnd:
    """Full pipeline: facts -> extract_financial_anomaly_inputs -> check_financial_anomalies."""

    def test_roe_extreme_via_pipeline(self):
        facts = _base_facts(net_profit=6000.0)
        facts.append(
            {"metric": "total_equity", "report_date": "2025-12-31",
             "period_scope": "POINT_IN_TIME", "value": 10000.0,
             "unit": "万元", "status": "HAS_DATA"},
        )
        inputs = extract_financial_anomaly_inputs(facts)
        anomaly = check_financial_anomalies("TEST", **inputs)
        assert "extreme_roe" in anomaly["anomalies"]

    def test_margin_squeeze_via_pipeline(self):
        facts = _base_facts(revenue=40000.0, cost=32000.0)
        # Prior: revenue=30000, cost=20000 -> gross_margin 33.3%
        # Current: revenue=40000, cost=32000 -> gross_margin 20%
        # revenue_growth=33.3%, cost_growth=60%, margin_drop=13.3pp
        facts.extend([
            {"metric": "revenue", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 30000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 20000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        anomaly = check_financial_anomalies("TEST", **inputs)
        assert "margin_squeeze" in anomaly["anomalies"]

    def test_ar_revenue_divergence_via_pipeline(self):
        facts = _base_facts(revenue=33074.0)
        facts.extend([
            {"metric": "accounts_receivable", "report_date": "2025-12-31",
             "period_scope": "POINT_IN_TIME", "value": 8000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "accounts_receivable", "report_date": "2025-09-30",
             "period_scope": "POINT_IN_TIME", "value": 3000.0,
             "unit": "万元", "status": "HAS_DATA"},
            # Prior revenue for growth calc
            {"metric": "revenue", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 32000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 19000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        anomaly = check_financial_anomalies("TEST", **inputs)
        # AR growth = 166.7%, revenue growth = 3.4% -> divergence
        assert "ar_revenue_divergence" in anomaly["anomalies"]

    def test_inventory_cost_divergence_via_pipeline(self):
        facts = _base_facts(cost=19844.0)
        facts.extend([
            {"metric": "inventory", "report_date": "2025-12-31",
             "period_scope": "POINT_IN_TIME", "value": 10000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "inventory", "report_date": "2025-09-30",
             "period_scope": "POINT_IN_TIME", "value": 5000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "revenue", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 32000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 19000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        anomaly = check_financial_anomalies("TEST", **inputs)
        # Inventory growth = 100%, cost growth = 4.4% -> divergence
        assert "inventory_cost_divergence" in anomaly["anomalies"]

    def test_clean_financials_no_anomaly(self):
        facts = _base_facts(revenue=33074.0, cost=19844.0, net_profit=2000.0,
                            operating_cf=500.0, assets=100000.0, liabilities=40000.0)
        # Prior period for stable growth
        facts.extend([
            {"metric": "revenue", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 31000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-09-30",
             "period_scope": "Q3_YTD", "value": 18500.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "total_assets", "report_date": "2025-09-30",
             "period_scope": "POINT_IN_TIME", "value": 95000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "total_liabilities", "report_date": "2025-09-30",
             "period_scope": "POINT_IN_TIME", "value": 38000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "total_equity", "report_date": "2025-12-31",
             "period_scope": "POINT_IN_TIME", "value": 60000.0,
             "unit": "万元", "status": "HAS_DATA"},
            {"metric": "total_equity", "report_date": "2025-09-30",
             "period_scope": "POINT_IN_TIME", "value": 57000.0,
             "unit": "万元", "status": "HAS_DATA"},
        ])
        inputs = extract_financial_anomaly_inputs(facts)
        anomaly = check_financial_anomalies("TEST", **inputs)
        assert anomaly["has_anomaly"] is False

    def test_multiple_anomalies_accumulate(self):
        """Multiple Phase 2 anomalies can be detected simultaneously."""
        r = check_financial_anomalies(
            "X",
            roe=60.0,
            revenue_growth=2.0,
            operating_cost_growth=8.0,
            gross_margin=25.0,
            gross_margin_prev=35.0,
            accounts_receivable_growth=50.0,
            inventory_growth=40.0,
            net_profit=10.0,
            operating_cashflow=-5.0,
        )
        assert "extreme_roe" in r["anomalies"]
        assert "margin_squeeze" in r["anomalies"]
        assert "negative_cashflow_quality" in r["anomalies"]
        assert "ar_revenue_divergence" in r["anomalies"]
        assert "inventory_cost_divergence" in r["anomalies"]
        # Phase 1 still works
        assert "cashflow_profit_divergence" in r["anomalies"]


# ── Format warning ──────────────────────────────────────────────────────────


class TestFormatWarning:
    def test_empty_when_no_anomaly(self):
        r = check_financial_anomalies("X")
        assert format_financial_anomaly_warning(r) == ""

    def test_contains_all_detected_anomalies(self):
        r = check_financial_anomalies("X", roe=60.0)
        warning = format_financial_anomaly_warning(r)
        assert "[C-006]" in warning
        assert "净资产收益率" in warning
        assert "人工复核" in warning

    def test_multiple_anomalies_in_warning(self):
        r = check_financial_anomalies(
            "X", roe=60.0, net_profit=10.0, operating_cashflow=-5.0
        )
        warning = format_financial_anomaly_warning(r)
        assert "净资产收益率" in warning
        assert "经营现金流" in warning


# ── Determinism ─────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_deterministic_output(self):
        """Same input always produces same output."""
        r1 = check_financial_anomalies("X", roe=60.0, gross_margin=30.0, gross_margin_prev=10.0)
        r2 = check_financial_anomalies("X", roe=60.0, gross_margin=30.0, gross_margin_prev=10.0)
        assert r1 == r2

    def test_deterministic_extraction(self):
        facts = _base_facts()
        r1 = extract_financial_anomaly_inputs(facts)
        r2 = extract_financial_anomaly_inputs(facts)
        assert r1 == r2
