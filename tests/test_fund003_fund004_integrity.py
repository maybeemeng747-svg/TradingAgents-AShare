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
        identity=_identity(False), period_facts=_facts(),
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
        identity=_identity(), period_facts=_facts(), explanation_context=context,
        report_text="现金流由预收款和合同负债变化驱动。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED not in codes
    assert ACCOUNTING_POLICY_UNKNOWN not in codes


def test_accounting_claim_without_official_evidence_is_blocked():
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(),
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
    facts = [{"metric": "investing_cashflow", "report_date": "2025-12-31",
              "value": 100000.0, "unit": "元", "status": "HAS_DATA"}]
    inputs = extract_financial_anomaly_inputs(facts)
    assert inputs["total_invest_cashflow"] == 0.001
    assert check_financial_anomalies("603629.SH", **inputs)["has_anomaly"] is False


def test_verified_chemical_industry_statement_is_not_a_causal_claim():
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(),
        explanation_context={"status": "unexplained"},
        report_text="公司所属化工行业，主营产品为基础化工品。",
    )
    assert CAUSE_UNSUPPORTED not in {item["code"] for item in integrity["blockers"]}


# ── FUND-003A adversarial tests ──────────────────────────────────────────────

def _contract_liability_context():
    return build_official_explanation_context(
        announcements="公司合同负债增加，主要系预收客户货款增加所致。",
        half_year_facts=None,
    )


def test_contract_liability_announcement_cannot_validate_cause_claims():
    ctx = _contract_liability_context()
    assert ctx["entries"]
    assert ctx["cause_terms"] == []
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(), explanation_context=ctx,
        report_text="公司业绩增长主要受益于化工旺季，原材料下降推动毛利率提升。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED in codes


def test_contract_liability_announcement_cannot_validate_net_method():
    ctx = _contract_liability_context()
    assert "净额法" not in ctx["accounting_policy_terms"]
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(), explanation_context=ctx,
        report_text="公司采用净额法确认收入。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert ACCOUNTING_POLICY_UNKNOWN in codes


def test_correct_cashflow_driven_by_advance_receipts_passes():
    ctx = build_official_explanation_context(
        announcements="公司说明经营现金流增加主要由于算力业务预收货款增加及合同负债上升。",
        half_year_facts=None,
    )
    assert ctx["entries"]
    assert "预收款" in ctx["accounting_policy_terms"]
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(), explanation_context=ctx,
        report_text="经营现金流由预收款和合同负债变化驱动。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED not in codes
    assert ACCOUNTING_POLICY_UNKNOWN not in codes
    assert integrity["is_valid"] is True
    claims = integrity.get("claims") or []
    for c in claims:
        if c["claim_id"].startswith("ACCT"):
            assert c["status"] == "officially_explained"
            assert c["evidence_ids"]


def test_bind_claims_returns_unexplained_when_no_evidence():
    claims = bind_claims("原材料下降导致毛利率提升。", {"entries": []})
    cause = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause) == 1
    assert cause[0]["metric"] == "原材料下降"
    assert cause[0]["status"] == "unexplained"
    assert cause[0]["evidence_ids"] == []


def test_bind_claims_returns_evidence_conflict_for_mismatched_terms():
    ctx = _contract_liability_context()
    claims = bind_claims("原材料下降导致毛利率提升。", ctx)
    cause = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause) == 1
    assert cause[0]["metric"] == "原材料下降"
    assert cause[0]["status"] == "evidence_conflict"


def test_bind_claims_returns_officially_explained_for_matching_terms():
    ctx = build_official_explanation_context(
        announcements="公司毛利率提升，主要由于原材料采购成本下降。", half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    cause = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause) == 1
    assert cause[0]["metric"] == "原材料下降"
    assert cause[0]["status"] == "officially_explained"
    assert cause[0]["evidence_ids"]


def test_explanation_context_has_data_status_field():
    ctx = build_official_explanation_context(announcements="合同负债增加。", half_year_facts=None)
    assert ctx["data_status"] == "HAS_DATA"
    ctx2 = build_official_explanation_context(
        announcements="No announcements found for 603629.SH", half_year_facts=None
    )
    assert ctx2["data_status"] == "NORMAL_NO_DATA"


def test_entries_have_evidence_id_and_occurrences():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，合同负债增加。", half_year_facts=None,
    )
    assert len(ctx["entries"]) >= 1
    entry = ctx["entries"][0]
    assert "evidence_id" in entry
    assert entry["evidence_id"].startswith("E")
    assert isinstance(entry.get("cause_terms"), list)
    assert isinstance(entry.get("accounting_terms"), list)
    assert "occurrences" in entry
    assert isinstance(entry["occurrences"], list)


# ── FUND-003A 补修 adversarial tests ───────────────────────────────────────

def test_reverse_raw_material_cause_is_caught():
    ctx = build_official_explanation_context(
        announcements="公司公告显示原材料成本上涨，对毛利率造成压力。",
        half_year_facts=None,
    )
    assert ctx["entries"]
    occs = ctx["entries"][0].get("occurrences") or []
    cause = [o for o in occs if o["canonical"] == "原材料上涨"]
    assert cause
    assert cause[0]["direction"] == "up"
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(), explanation_context=ctx,
        report_text="原材料下降推动毛利率提升。",
    )
    assert CAUSE_UNSUPPORTED in {item["code"] for item in integrity["blockers"]}


def test_negation_net_method_is_caught():
    ctx = build_official_explanation_context(
        announcements="公司说明本年度未采用净额法确认收入，仍采用总额法。",
        half_year_facts=None,
    )
    assert ctx["entries"]
    occs = ctx["entries"][0].get("occurrences") or []
    net = [o for o in occs if o["canonical"] == "净额法" and o["negation"]]
    assert net
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(), explanation_context=ctx,
        report_text="公司采用净额法确认设备经销收入。",
    )
    assert ACCOUNTING_POLICY_UNKNOWN in {item["code"] for item in integrity["blockers"]}


def test_downstream_negation_does_not_negate_accounting_method():
    ctx = build_official_explanation_context(
        announcements="公司采用净额法确认收入。", half_year_facts=None,
    )
    claims = bind_claims("公司采用净额法不影响现金流。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "净额法"
    assert claims[0]["status"] == "officially_explained"


def test_reverse_contract_liability_is_caught():
    ctx = build_official_explanation_context(
        announcements="公司合同负债较上期增加30%，主要系预收客户货款增加所致。",
        half_year_facts=None,
    )
    assert ctx["entries"]
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(), explanation_context=ctx,
        report_text="合同负债下降表明收入质量改善。",
    )
    assert ACCOUNTING_POLICY_UNKNOWN in {item["code"] for item in integrity["blockers"]}


def test_synonym_advance_receipts_passes():
    ctx = build_official_explanation_context(
        announcements="公司经营现金流增加主要由于预收货款增加。", half_year_facts=None,
    )
    assert ctx["entries"]
    assert "预收款" in ctx["accounting_policy_terms"]
    integrity = evaluate_fundamental_integrity(
        identity=_identity(), period_facts=_facts(), explanation_context=ctx,
        report_text="经营现金流由预收款驱动。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED not in codes
    assert ACCOUNTING_POLICY_UNKNOWN not in codes
    assert integrity["is_valid"] is True
    claims = integrity.get("claims") or []
    acct = [c for c in claims if c["metric"] == "预收款"]
    assert acct
    assert acct[0]["evidence_ids"]


def test_claim_id_stability():
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本下降。", half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升。"
    c1 = bind_claims(report, ctx)
    c2 = bind_claims(report, ctx)
    cause1 = [c for c in c1 if c["claim_id"].startswith("CAUSE")]
    cause2 = [c for c in c2 if c["claim_id"].startswith("CAUSE")]
    assert len(cause1) == len(cause2) == 1
    assert cause1[0]["claim_id"] == cause2[0]["claim_id"]
    assert cause1[0]["claim_id"].startswith("CAUSE-")


def test_long_evidence_context_preserves_keyword():
    padding = "这是无关的填充文字。" * 50
    long_text = f"{padding}公司公告显示原材料成本下降，主要系采购策略优化所致。"
    assert len(long_text) > 500
    ctx = build_official_explanation_context(announcements=long_text, half_year_facts=None)
    assert ctx["entries"]
    assert "原材料" in ctx["entries"][0]["text"]


# ── FUND-003A-B: per-term binding adversarial tests ────────────────────────

def test_neutral_first_mention_plus_later_positive():
    ctx = build_official_explanation_context(
        announcements="公司公告显示毛利率提升，主要由于原材料采购成本下降。", half_year_facts=None,
    )
    report = "原材料方面保持关注。后文原材料下降推动毛利率提升。"
    claims = bind_claims(report, ctx)
    cause = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause) == 1
    assert cause[0]["metric"] == "原材料下降"
    assert cause[0]["status"] == "officially_explained"


def test_raw_material_decline_plus_contract_liability_increase():
    ctx = build_official_explanation_context(
        announcements="公司合同负债增加，主要系预收客户货款增加所致。",
        half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升，同时合同负债增加表明订单增长。"
    claims = bind_claims(report, ctx)
    cause = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause) == 1
    assert cause[0]["status"] == "evidence_conflict"
    acct = [c for c in claims if c["metric"] == "合同负债"]
    assert len(acct) == 1
    assert acct[0]["status"] == "officially_explained"


def test_same_indicator_opposite_claims_in_report():
    ctx = build_official_explanation_context(
        announcements="公司毛利率提升，主要由于原材料采购成本下降。", half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升，但近期原材料上涨带来成本压力。"
    claims = bind_claims(report, ctx)
    cause = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause) == 2
    assert cause[0]["status"] == "officially_explained"
    assert cause[1]["status"] == "evidence_conflict"


def test_different_causes_sharing_same_indicator():
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本下降，主要系采购策略优化所致。", half_year_facts=None,
    )
    report = "原材料下降主要因为采购策略优化，同时行业周期也导致原材料下降。"
    claims = bind_claims(report, ctx)
    cause = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    industry = [c for c in cause if c["metric"] == "行业周期"]
    assert len(industry) == 1
    assert industry[0]["status"] == "evidence_conflict"


def test_multi_term_long_evidence():
    ctx = build_official_explanation_context(
        announcements="公司毛利率提升主要由于原材料采购成本下降；合同负债增加主要系预收货款增加所致。",
        half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升，同时合同负债和预收款驱动现金流改善。"
    claims = bind_claims(report, ctx)
    cause = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause) == 1
    assert cause[0]["status"] == "officially_explained"
    acct = [c for c in claims if c["claim_id"].startswith("ACCT")]
    assert len(acct) >= 1
    for c in acct:
        assert c["status"] == "officially_explained"


def test_occurrences_have_direction_negation_per_term():
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本下降，未采用净额法确认收入。",
        half_year_facts=None,
    )
    assert ctx["entries"]
    occs = ctx["entries"][0].get("occurrences") or []
    cause = [o for o in occs if o["canonical"] == "原材料下降"]
    assert cause
    assert cause[0]["direction"] == "down"
    assert cause[0]["negation"] is False
    net = [o for o in occs if o["canonical"] == "净额法"]
    assert net
    assert net[0]["negation"] is True


def test_evidence_occurrences_audit_trail():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    occs = ctx["entries"][0].get("occurrences") or []
    assert occs
    occ = [o for o in occs if o["canonical"] == "原材料下降"][0]
    assert occ["direction"] == "down"
    assert occ["negation"] is False
    # In this sentence the material decline is the result of procurement
    # optimization, not itself the cause of another financial result.
    assert occ["relation"] is None
    assert occ["relation_cue"] is None
    assert occ["relation_target"] is None
    assert isinstance(occ["position"], int)
    assert len(occ["context"]) > 0


def test_negated_cause_evidence_cannot_support_positive_claim():
    ctx = build_official_explanation_context(
        announcements="公司未出现原材料成本下降。", half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"
    assert claims[0]["evidence_ids"] == []


def test_causal_binding_requires_the_same_financial_target():
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"
    assert claims[0]["evidence_ids"] == []


def test_later_financial_word_cannot_be_borrowed_as_relation_target():
    ctx = build_official_explanation_context(
        announcements="原材料采购成本下降，主要系采购优化所致，毛利率保持稳定。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"
    assert claims[0]["evidence_ids"] == []


def test_causal_binding_records_the_matched_financial_target():
    ctx = build_official_explanation_context(
        announcements="公司毛利率提升，主要由于原材料采购成本下降。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "officially_explained"
    assert claims[0]["audit_fragment"]["relation_target"] == "毛利率"


def test_unconfirmed_reason_wording_stays_a_supported_direct_fact():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。", half_year_facts=None,
    )
    claims = bind_claims("原材料下降，原因未确认。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "officially_explained"


def test_shared_leading_cue_binds_each_listed_cause():
    ctx = build_official_explanation_context(
        announcements="公司业绩增长主要由于行业周期、集中交付。",
        half_year_facts=None,
    )
    claims = bind_claims("集中交付推动业绩增长。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "集中交付"
    assert claims[0]["status"] == "officially_explained"
    assert claims[0]["audit_fragment"]["relation_target"] == "业绩"


def test_shared_leading_cue_accepts_chinese_comma_cause_list():
    ctx = build_official_explanation_context(
        announcements="公司业绩增长主要由于行业周期，集中交付。",
        half_year_facts=None,
    )
    claims = bind_claims("集中交付推动业绩增长。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "officially_explained"
    assert claims[0]["audit_fragment"]["relation_target"] == "业绩"


def test_shared_trailing_cue_keeps_every_listed_cause_as_a_claim():
    ctx = build_official_explanation_context(
        announcements="公司利润改善主要由于集中交付。", half_year_facts=None,
    )
    claims = bind_claims("行业周期及集中交付推动利润改善。", ctx)
    assert [claim["metric"] for claim in claims] == ["行业周期", "集中交付"]
    assert claims[0]["status"] == "evidence_conflict"
    assert claims[1]["status"] == "officially_explained"


def test_negated_direction_claim_cannot_use_positive_evidence():
    ctx = build_official_explanation_context(
        announcements="原材料采购成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    claims = bind_claims("公司因原材料价格未下降导致毛利率承压。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "原材料下降"
    assert claims[0]["status"] == "evidence_conflict"
    assert claims[0]["evidence_ids"] == []


def test_causal_claim_requires_causal_evidence_not_cooccurrence():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。公司利润增加。", half_year_facts=None,
    )
    claims = bind_claims("原材料下降导致利润增加。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"


def test_unrelated_causal_cue_does_not_upgrade_directed_fact_to_cause():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。", half_year_facts=None,
    )
    claims = bind_claims("原材料下降，同时其他因素导致需求下滑。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "原材料下降"
    assert claims[0]["status"] == "officially_explained"


def test_leading_causal_cue_requires_causal_evidence():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。", half_year_facts=None,
    )
    claims = bind_claims("导致毛利率提升的原因是原材料下降。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"


def test_subject_reason_phrase_requires_causal_evidence():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。", half_year_facts=None,
    )
    claims = bind_claims("原材料下降是毛利率提升的主要原因。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"


def test_subject_reason_phrase_with_matching_target_is_supported():
    ctx = build_official_explanation_context(
        announcements="原材料下降是毛利率提升的主要原因。", half_year_facts=None,
    )
    claims = bind_claims("原材料下降是毛利率提升的主要原因。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "officially_explained"
    assert claims[0]["audit_fragment"]["relation_target"] == "毛利率"


def test_distant_leading_causal_cue_requires_causal_evidence():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。", half_year_facts=None,
    )
    claims = bind_claims("毛利率提升主要由于上游大宗商品原材料价格下降。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "原材料下降"
    assert claims[0]["status"] == "evidence_conflict"


def test_benefit_from_cue_is_a_causal_claim():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。", half_year_facts=None,
    )
    claims = bind_claims("利润改善受益于行业周期。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "行业周期"
    assert claims[0]["status"] == "evidence_conflict"


def test_gain_from_cue_is_a_causal_claim():
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。", half_year_facts=None,
    )
    claims = bind_claims("收入改善得益于产品涨价。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "产品涨价"
    assert claims[0]["status"] == "evidence_conflict"


def test_intrinsic_product_price_increase_is_checked_without_causal_cue():
    claims = bind_claims("公司产品涨价。", {"entries": []})
    assert len(claims) == 1
    assert claims[0]["metric"] == "产品涨价"
    assert claims[0]["status"] == "unexplained"


def test_neutral_mention_cannot_borrow_direction_across_comma():
    claims = bind_claims(
        "原材料方面保持关注，后文原材料下降推动毛利率提升。",
        {"entries": []},
    )
    assert len(claims) == 1
    assert claims[0]["cause_terms"] == ["原材料下降"]


def test_generic_raw_material_claim_is_canonicalized_by_direction():
    ctx = build_official_explanation_context(
        announcements="毛利率提升，主要由于原材料采购成本下降。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料价格下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "原材料下降"
    assert claims[0]["status"] == "officially_explained"


def test_post_cause_result_connectors_bind_the_financial_target():
    for connector in ("因此", "所以"):
        statement = f"原材料下降，{connector}毛利率提升。"
        ctx = build_official_explanation_context(
            announcements=statement,
            half_year_facts=None,
        )
        claims = bind_claims(statement, ctx)
        assert len(claims) == 1
        assert claims[0]["status"] == "officially_explained"
        assert claims[0]["audit_fragment"]["relation_target"] == "毛利率"


def test_revenue_alias_binds_to_the_canonical_revenue_target():
    ctx = build_official_explanation_context(
        announcements="公司营收增长主要由于产品涨价。",
        half_year_facts=None,
    )
    claims = bind_claims("产品涨价推动营收增长。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "officially_explained"
    assert claims[0]["audit_fragment"]["relation_target"] == "营业收入"


def test_one_evidence_sentence_preserves_multiple_financial_targets():
    ctx = build_official_explanation_context(
        announcements="公司营业收入和净利润增长主要由于产品涨价。",
        half_year_facts=None,
    )
    evidence_occurrence = ctx["entries"][0]["occurrences"][0]
    assert evidence_occurrence["relation_targets"] == ["营业收入", "归母净利润"]

    revenue_claim = bind_claims("产品涨价推动营业收入增长。", ctx)
    profit_claim = bind_claims("产品涨价推动净利润增长。", ctx)
    assert revenue_claim[0]["status"] == "officially_explained"
    assert profit_claim[0]["status"] == "officially_explained"


def test_reversed_causal_direction_does_not_validate_the_report_claim():
    ctx = build_official_explanation_context(
        announcements="公司净利润下降导致原材料采购成本上涨。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料上涨导致净利润下降。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"
    assert claims[0]["evidence_ids"] == []


def test_later_negated_direction_does_not_negate_an_earlier_cause():
    ctx = build_official_explanation_context(
        announcements="毛利率提升主要由于原材料下降，行业未增长。",
        half_year_facts=None,
    )
    evidence_occurrence = ctx["entries"][0]["occurrences"][0]
    assert evidence_occurrence["canonical"] == "原材料下降"
    assert evidence_occurrence["negation"] is False
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert claims[0]["status"] == "officially_explained"


def test_backward_target_scan_stays_in_the_cue_fragment():
    ctx = build_official_explanation_context(
        announcements="公司营业收入增长，毛利率提升主要由于原材料采购成本下降。",
        half_year_facts=None,
    )
    evidence_occurrence = ctx["entries"][0]["occurrences"][0]
    assert evidence_occurrence["relation_targets"] == ["毛利率"]

    margin_claim = bind_claims("原材料下降推动毛利率提升。", ctx)
    revenue_claim = bind_claims("原材料下降推动营业收入增长。", ctx)
    assert margin_claim[0]["status"] == "officially_explained"
    assert revenue_claim[0]["status"] == "evidence_conflict"


def test_ascii_comma_also_limits_the_backward_target_scan():
    ctx = build_official_explanation_context(
        announcements="公司营业收入增长, 毛利率提升主要由于原材料采购成本下降。",
        half_year_facts=None,
    )
    evidence_occurrence = ctx["entries"][0]["occurrences"][0]
    assert evidence_occurrence["relation_targets"] == ["毛利率"]

    margin_claim = bind_claims("原材料下降推动毛利率提升。", ctx)
    revenue_claim = bind_claims("原材料下降推动营业收入增长。", ctx)
    assert margin_claim[0]["status"] == "officially_explained"
    assert revenue_claim[0]["status"] == "evidence_conflict"


def test_leading_cause_cue_binds_the_following_result_fragment():
    ctx = build_official_explanation_context(
        announcements="由于原材料采购成本下降，毛利率提升。",
        half_year_facts=None,
    )
    evidence_occurrence = ctx["entries"][0]["occurrences"][0]
    assert evidence_occurrence["relation_targets"] == ["毛利率"]
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert claims[0]["status"] == "officially_explained"


def test_nested_causal_wording_prefers_the_cue_with_a_financial_target():
    statement = "毛利率提升主要由于原材料下降导致成本改善。"
    ctx = build_official_explanation_context(
        announcements=statement,
        half_year_facts=None,
    )
    evidence_occurrence = ctx["entries"][0]["occurrences"][0]
    assert evidence_occurrence["relation_cue"] == "由于"
    assert evidence_occurrence["relation_targets"] == ["毛利率"]

    identical_claim = bind_claims(statement, ctx)
    normalized_claim = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert identical_claim[0]["status"] == "officially_explained"
    assert normalized_claim[0]["status"] == "officially_explained"


def test_financial_target_before_trailing_causal_verb_is_preserved():
    statement = "原材料下降对毛利率提升有推动作用。"
    ctx = build_official_explanation_context(
        announcements=statement,
        half_year_facts=None,
    )
    occurrence = ctx["entries"][0]["occurrences"][0]
    assert occurrence["relation_cue"] == "推动"
    assert occurrence["relation_targets"] == ["毛利率"]

    identical_claim = bind_claims(statement, ctx)
    matching_claim = bind_claims("原材料下降推动毛利率提升。", ctx)
    mismatched_claim = bind_claims("原材料下降推动营业收入增长。", ctx)
    assert identical_claim[0]["status"] == "officially_explained"
    assert matching_claim[0]["status"] == "officially_explained"
    assert mismatched_claim[0]["status"] == "evidence_conflict"


def test_result_before_cause_cleft_requires_causal_evidence():
    direct_ctx = build_official_explanation_context(
        announcements="公司原材料成本下降。",
        half_year_facts=None,
    )
    causal_ctx = build_official_explanation_context(
        announcements="毛利率提升主要由于原材料成本下降。",
        half_year_facts=None,
    )
    report = "导致毛利率提升的是原材料下降。"

    direct_claim = bind_claims(report, direct_ctx)
    causal_claim = bind_claims(report, causal_ctx)
    assert direct_claim[0]["status"] == "evidence_conflict"
    assert direct_claim[0]["evidence_ids"] == []
    assert causal_claim[0]["status"] == "officially_explained"
    assert causal_claim[0]["audit_fragment"]["relation_target"] == "毛利率"


def test_generic_raw_material_phrase_remains_in_a_cause_list():
    ctx = build_official_explanation_context(
        announcements="毛利率提升主要由于原材料价格下降，产品涨价。",
        half_year_facts=None,
    )
    occurrences = ctx["entries"][0]["occurrences"]
    product = next(item for item in occurrences if item["canonical"] == "产品涨价")
    material = next(item for item in occurrences if item["canonical"] == "原材料下降")
    assert product["relation_targets"] == ["毛利率"]
    assert material["relation_targets"] == ["毛利率"]

    product_claim = bind_claims("产品涨价推动毛利率提升。", ctx)
    material_claim = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert product_claim[0]["status"] == "officially_explained"
    assert material_claim[0]["status"] == "officially_explained"


def test_explicit_term_direction_wins_over_a_nearby_result_direction():
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本下降。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降而毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "原材料下降"
    assert claims[0]["status"] == "officially_explained"
    assert claims[0]["audit_fragment"]["direction"] == "down"


def test_conflicting_evidence_for_one_indicator_fails_closed():
    ctx = build_official_explanation_context(
        announcements=(
            "毛利率提升，主要由于原材料采购成本下降。"
            "后续毛利率承压，主要由于原材料采购成本上涨。"
        ),
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"


def test_opposite_direct_fact_also_conflicts_with_causal_support():
    ctx = build_official_explanation_context(
        announcements=(
            "公司原材料采购成本上涨。"
            "毛利率提升，主要由于原材料采购成本下降。"
        ),
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "evidence_conflict"
    assert claims[0]["evidence_ids"] == ["E001"]


def test_conflicting_accounting_directions_fail_closed():
    ctx = build_official_explanation_context(
        announcements="公司合同负债增加。后续合同负债下降。", half_year_facts=None,
    )
    claims = bind_claims("合同负债增加表明订单增长。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "合同负债"
    assert claims[0]["status"] == "evidence_conflict"


def test_post_term_negated_accounting_direction_fails_closed():
    ctx = build_official_explanation_context(
        announcements="公司合同负债增加。", half_year_facts=None,
    )
    claims = bind_claims("合同负债未增加表明订单增长。", ctx)
    assert len(claims) == 1
    assert claims[0]["metric"] == "合同负债"
    assert claims[0]["status"] == "evidence_conflict"
    assert claims[0]["evidence_ids"] == []


def test_interposed_negation_before_direction_fails_closed():
    for evidence in (
        "毛利率提升主要由于原材料价格未出现上涨。",
        "毛利率提升主要由于原材料价格没有明显上涨。",
    ):
        ctx = build_official_explanation_context(
            announcements=evidence,
            half_year_facts=None,
        )
        occurrence = ctx["entries"][0]["occurrences"][0]
        assert occurrence["canonical"] == "原材料上涨"
        assert occurrence["negation"] is True

        claims = bind_claims("原材料上涨推动毛利率提升。", ctx)
        assert len(claims) == 1
        assert claims[0]["status"] == "evidence_conflict"
        assert claims[0]["evidence_ids"] == []


def test_negated_opposite_evidence_does_not_override_supported_claim():
    ctx = build_official_explanation_context(
        announcements=(
            "公司未出现原材料成本上涨。"
            "毛利率提升，主要由于原材料采购成本下降。"
        ),
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    assert len(claims) == 1
    assert claims[0]["status"] == "officially_explained"


def test_supported_claim_keeps_per_occurrence_audit_fragment():
    ctx = build_official_explanation_context(
        announcements="毛利率提升，主要由于原材料采购成本下降。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    claim = claims[0]
    fragment = claim["audit_fragment"]
    assert claim["status"] == "officially_explained"
    assert fragment["evidence_id"] == "E001"
    assert fragment["matched_term"] == "原材料采购成本下降"
    assert fragment["direction"] == "down"
    assert fragment["negation"] is False
    assert fragment["relation"] == "causal"
    assert claim["audit_fragments"] == [fragment]


def test_claim_id_occurrence_distinction():
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本下降。", half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升。后文原材料下降进一步确认趋势。"
    claims = bind_claims(report, ctx)
    down = [c for c in claims if c["metric"] == "原材料下降"]
    assert len(down) == 2
    assert down[0]["claim_id"] != down[1]["claim_id"]
    claims2 = bind_claims(report, ctx)
    down2 = [c for c in claims2 if c["metric"] == "原材料下降"]
    assert down[0]["claim_id"] == down2[0]["claim_id"]
    assert down[1]["claim_id"] == down2[1]["claim_id"]


def test_claim_id_still_deterministic_for_single_occurrence():
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本下降。", half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升。"
    c1 = bind_claims(report, ctx)
    c2 = bind_claims(report, ctx)
    cause1 = [c for c in c1 if c["metric"] == "原材料下降"]
    cause2 = [c for c in c2 if c["metric"] == "原材料下降"]
    assert len(cause1) == 1
    assert cause1[0]["claim_id"] == cause2[0]["claim_id"]
