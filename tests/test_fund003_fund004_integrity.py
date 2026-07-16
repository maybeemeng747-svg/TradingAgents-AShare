from tradingagents.agents.utils.financial_validator import check_financial_anomalies
from tradingagents.graph.data_collector import DataCollector
from tradingagents.agents.utils.fundamental_integrity import (
    ACCOUNTING_POLICY_UNKNOWN,
    CAUSE_UNSUPPORTED,
    IDENTITY_UNVERIFIED,
    PERIOD_SCOPE_INVALID,
    bind_claims,
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
    assert context["data_status"] == "NORMAL_NO_DATA"
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


# ── FUND-003A adversarial tests ──────────────────────────────────────────────


def _contract_liability_context():
    """Evidence: only '合同负债' — no cause terms."""
    return build_official_explanation_context(
        announcements="公司合同负债增加，主要系预收客户货款增加所致。",
        half_year_facts=None,
    )


def test_contract_liability_announcement_cannot_validate_cause_claims():
    """Adversarial: 公告仅含"合同负债增加"，报告写"化工旺季/原材料下降"
    必须命中 CAUSE_UNSUPPORTED。"""
    ctx = _contract_liability_context()
    assert ctx["entries"], "contract liability should produce entries"
    assert ctx["cause_terms"] == [], "contract liability has no cause terms"

    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context=ctx,
        report_text="公司业绩增长主要受益于化工旺季，原材料下降推动毛利率提升。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED in codes, (
        "contract liability announcement cannot validate raw-material/seasonal claims"
    )


def test_contract_liability_announcement_cannot_validate_net_method():
    """Adversarial: 公告仅含"合同负债"，报告写"采用净额法"
    必须命中 ACCOUNTING_POLICY_UNKNOWN。"""
    ctx = _contract_liability_context()
    assert "净额法" not in ctx["accounting_policy_terms"]

    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context=ctx,
        report_text="公司采用净额法确认收入。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert ACCOUNTING_POLICY_UNKNOWN in codes, (
        "contract liability alone cannot validate net-method accounting claim"
    )


def test_correct_cashflow_driven_by_advance_receipts_passes():
    """Correct claim: "预收款/合同负债推动经营现金流" passes with evidence IDs."""
    ctx = build_official_explanation_context(
        announcements="公司说明经营现金流增加主要由于算力业务预收货款增加及合同负债上升。",
        half_year_facts=None,
    )
    assert ctx["entries"]
    assert "预收款" in ctx["accounting_policy_terms"]
    assert "合同负债" in ctx["accounting_policy_terms"]

    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context=ctx,
        report_text="经营现金流由预收款和合同负债变化驱动。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED not in codes
    assert ACCOUNTING_POLICY_UNKNOWN not in codes
    assert integrity["is_valid"] is True

    # Verify evidence IDs are preserved in claims
    claims = integrity.get("claims") or []
    acct_claims = [c for c in claims if c["claim_id"].startswith("ACCT")]
    for c in acct_claims:
        assert c["status"] == "officially_explained"
        assert c["evidence_ids"], f"claim {c['metric']} must have evidence IDs"


def test_bind_claims_returns_unexplained_when_no_evidence():
    """bind_claims: no entries → all claims are 'unexplained'."""
    claims = bind_claims("原材料下降导致毛利率提升。", {"entries": []})
    assert len(claims) == 1
    assert claims[0]["metric"] == "原材料"
    assert claims[0]["status"] == "unexplained"
    assert claims[0]["evidence_ids"] == []


def test_bind_claims_returns_evidence_conflict_for_mismatched_terms():
    """bind_claims: evidence has '合同负债' but report claims '原材料下降'
    → 'evidence_conflict' (evidence exists, but for different claim)."""
    ctx = _contract_liability_context()
    claims = bind_claims("原材料下降导致毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "原材料"
    assert claims[0]["status"] == "evidence_conflict"


def test_bind_claims_returns_officially_explained_for_matching_terms():
    """bind_claims: evidence has '原材料' and report claims '原材料下降'
    → 'officially_explained'."""
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本同比下降。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "原材料"
    assert claims[0]["status"] == "officially_explained"
    assert claims[0]["evidence_ids"]


def test_explanation_context_has_data_status_field():
    """build_official_explanation_context returns 'data_status' for data pipeline."""
    ctx_with = build_official_explanation_context(
        announcements="合同负债增加。", half_year_facts=None
    )
    assert ctx_with["data_status"] == "HAS_DATA"

    ctx_without = build_official_explanation_context(
        announcements="No announcements found for 603629.SH", half_year_facts=None
    )
    assert ctx_without["data_status"] == "NORMAL_NO_DATA"


def test_entries_have_evidence_id_and_term_fields():
    """Each entry carries evidence_id, cause_terms, accounting_terms."""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，合同负债增加。",
        half_year_facts=None,
    )
    assert len(ctx["entries"]) >= 1
    entry = ctx["entries"][0]
    assert "evidence_id" in entry
    assert entry["evidence_id"].startswith("E")
    assert isinstance(entry.get("cause_terms"), list)
    assert isinstance(entry.get("accounting_terms"), list)
