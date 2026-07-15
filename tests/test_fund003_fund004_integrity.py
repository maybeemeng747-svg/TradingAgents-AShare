from tradingagents.agents.utils.financial_validator import check_financial_anomalies
from tradingagents.graph.data_collector import DataCollector
from tradingagents.agents.utils.fundamental_integrity import (
    ACCOUNTING_POLICY_UNKNOWN,
    CAUSE_UNSUPPORTED,
    IDENTITY_UNVERIFIED,
    PERIOD_SCOPE_INVALID,
    build_official_explanation_context,
    evaluate_fundamental_integrity,
    extract_financial_anomaly_inputs,
)


def _identity(allowed=True):
    return {
        "symbol": "603629.SH",
        "security_name": "利通电子" if allowed else None,
        "commercial_analysis_allowed": allowed,
    }


def _facts():
    return [
        {"metric": "revenue", "report_date": "2025-12-31", "value": 100.0, "status": "HAS_DATA"},
        {"metric": "operating_cost", "report_date": "2025-12-31", "value": 60.0, "status": "HAS_DATA"},
        {"metric": "net_profit", "report_date": "2025-12-31", "value": 10.0, "status": "HAS_DATA"},
        {"metric": "operating_cashflow", "report_date": "2025-12-31", "value": -1.0, "status": "HAS_DATA"},
        {"metric": "total_assets", "report_date": "2025-12-31", "value": 100.0, "status": "HAS_DATA"},
        {"metric": "total_liabilities", "report_date": "2025-12-31", "value": 75.0, "status": "HAS_DATA"},
    ]


def test_missing_identity_rejects_603629_style_business_guess():
    integrity = evaluate_fundamental_integrity(
        identity=_identity(False),
        period_facts=_facts(),
        explanation_context={"status": "unexplained"},
        report_text="公司主营化工品生产与销售，原材料成本下降。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert IDENTITY_UNVERIFIED in codes
    assert CAUSE_UNSUPPORTED in codes
    assert integrity["status"] == "NEEDS_REVIEW"


def test_missing_periods_are_blocked_not_treated_as_completed():
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=[], explanation_context={}, report_text=""
    )
    assert integrity["blockers"][0]["code"] == PERIOD_SCOPE_INVALID


def test_no_announcements_is_normal_absence_not_official_explanation():
    context = build_official_explanation_context(
        announcements="No announcements found for 603629.SH", half_year_facts=None
    )
    assert context["status"] == "unexplained"
    assert context["entries"] == []
    assert DataCollector._infer_source_status("No announcements found for 603629.SH") == "NORMAL_NO_DATA"


def test_official_contract_liability_explanation_supports_policy_claim():
    context = build_official_explanation_context(
        announcements="公司说明经营现金流增加主要由于算力业务预收货款增加及合同负债上升。",
        half_year_facts=None,
    )
    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context=context,
        report_text="现金流由预收款和合同负债变化驱动。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED not in codes
    assert ACCOUNTING_POLICY_UNKNOWN not in codes


def test_accounting_claim_without_official_evidence_is_blocked():
    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context={"status": "unexplained", "accounting_policy_terms": []},
        report_text="公司采用净额法确认设备经销收入。",
    )
    assert ACCOUNTING_POLICY_UNKNOWN in {item["code"] for item in integrity["blockers"]}


def test_c006_receives_real_financial_values():
    inputs = extract_financial_anomaly_inputs(_facts())
    assert inputs["gross_margin"] == 40.0
    assert inputs["debt_ratio"] == 75.0
    anomaly = check_financial_anomalies("603629.SH", **inputs)
    assert "cashflow_profit_divergence" in anomaly["anomalies"]
    assert "high_debt_ratio" in anomaly["anomalies"]


def test_c006_converts_yuan_cashflow_to_yi_before_thresholds():
    facts = [{
        "metric": "investing_cashflow", "report_date": "2025-12-31",
        "value": 100000.0, "unit": "元", "status": "HAS_DATA",
    }]
    inputs = extract_financial_anomaly_inputs(facts)
    assert inputs["total_invest_cashflow"] == 0.001
    assert check_financial_anomalies("603629.SH", **inputs)["has_anomaly"] is False


def test_verified_chemical_industry_statement_is_not_a_causal_claim():
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(), explanation_context={"status": "unexplained"},
        report_text="公司所属化工行业，主营产品为基础化工品。",
    )
    assert CAUSE_UNSUPPORTED not in {item["code"] for item in integrity["blockers"]}
