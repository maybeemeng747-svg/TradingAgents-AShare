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

    claims = integrity.get("claims") or []
    acct_claims = [c for c in claims if c["claim_id"].startswith("ACCT")]
    for c in acct_claims:
        assert c["status"] == "officially_explained"
        assert c["evidence_ids"], f"claim {c['metric']} must have evidence IDs"


def test_bind_claims_returns_unexplained_when_no_evidence():
    """bind_claims: no entries → all claims are 'unexplained'."""
    claims = bind_claims("原材料下降导致毛利率提升。", {"entries": []})
    cause_claims = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause_claims) == 1
    assert cause_claims[0]["metric"] == "原材料下降"
    assert cause_claims[0]["status"] == "unexplained"
    assert cause_claims[0]["evidence_ids"] == []


def test_bind_claims_returns_evidence_conflict_for_mismatched_terms():
    """bind_claims: evidence has '合同负债' but report claims '原材料下降'
    → 'evidence_conflict'."""
    ctx = _contract_liability_context()
    claims = bind_claims("原材料下降导致毛利率提升。", ctx)
    cause_claims = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause_claims) == 1
    assert cause_claims[0]["metric"] == "原材料下降"
    assert cause_claims[0]["status"] == "evidence_conflict"


def test_bind_claims_returns_officially_explained_for_matching_terms():
    """bind_claims: evidence has '原材料下降' synonym and report claims '原材料下降'
    → 'officially_explained'."""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    claims = bind_claims("原材料下降推动毛利率提升。", ctx)
    cause_claims = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause_claims) == 1
    assert cause_claims[0]["metric"] == "原材料下降"
    assert cause_claims[0]["status"] == "officially_explained"
    assert cause_claims[0]["evidence_ids"]


def test_explanation_context_has_data_status_field():
    ctx_with = build_official_explanation_context(
        announcements="合同负债增加。", half_year_facts=None
    )
    assert ctx_with["data_status"] == "HAS_DATA"

    ctx_without = build_official_explanation_context(
        announcements="No announcements found for 603629.SH", half_year_facts=None
    )
    assert ctx_without["data_status"] == "NORMAL_NO_DATA"


def test_entries_have_evidence_id_and_term_details():
    """Each entry carries evidence_id and occurrences list."""
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
    assert "occurrences" in entry
    assert isinstance(entry["occurrences"], list)


# ── FUND-003A adversarial tests (补修) ───────────────────────────────────────


def test_reverse_raw_material_cause_is_caught():
    """[P1] 公告含"原材料成本上涨"，报告写"原材料下降推动毛利率"
    → 必须命中 CAUSE_UNSUPPORTED（反向证据）。"""
    ctx = build_official_explanation_context(
        announcements="公司公告显示原材料成本上涨，对毛利率造成压力。",
        half_year_facts=None,
    )
    assert ctx["entries"], "should have entries"
    entry = ctx["entries"][0]
    td = entry.get("term_details") or {}
    assert "原材料上涨" in td, f"term_details should have 原材料上涨, got {list(td.keys())}"
    assert td["原材料上涨"]["direction"] == "up"

    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context=ctx,
        report_text="原材料下降推动毛利率提升。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED in codes, (
        "reverse direction: evidence says '上涨' but claim says '下降' must be caught"
    )


def test_negation_net_method_is_caught():
    """[P1] 公告含"未采用净额法"，报告写"采用净额法"
    → 必须命中 ACCOUNTING_POLICY_UNKNOWN（否定证据）。"""
    ctx = build_official_explanation_context(
        announcements="公司说明本年度未采用净额法确认收入，仍采用总额法。",
        half_year_facts=None,
    )
    assert ctx["entries"], "should have entries"
    entry = ctx["entries"][0]
    td = entry.get("term_details") or {}
    assert "净额法" in td, f"term_details should have 净额法, got {list(td.keys())}"
    assert td["净额法"]["negation"] is True

    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context=ctx,
        report_text="公司采用净额法确认设备经销收入。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert ACCOUNTING_POLICY_UNKNOWN in codes, (
        "negation: evidence says '未采用净额法' but claim says '采用净额法' must be caught"
    )


def test_reverse_contract_liability_is_caught():
    """[P1] 公告含"合同负债增加"，报告写"合同负债下降"
    → 必须命中 ACCOUNTING_POLICY_UNKNOWN（反向证据）。"""
    ctx = build_official_explanation_context(
        announcements="公司合同负债较上期增加30%，主要系预收客户货款增加所致。",
        half_year_facts=None,
    )
    assert ctx["entries"], "should have entries"

    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context=ctx,
        report_text="合同负债下降表明收入质量改善。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert ACCOUNTING_POLICY_UNKNOWN in codes, (
        "reverse: evidence says '增加' but claim says '下降' must be caught"
    )


def test_synonym_advance_receipts_passes():
    """[P2] 公告含"预收货款增加"，报告写"预收款驱动现金流"
    → 必须通过，保留 evidence ID（同义词）。"""
    ctx = build_official_explanation_context(
        announcements="公司经营现金流增加主要由于预收货款增加。",
        half_year_facts=None,
    )
    assert ctx["entries"], "should have entries"
    assert "预收款" in ctx["accounting_policy_terms"]

    integrity = evaluate_fundamental_integrity(
        identity=_identity(),
        period_facts=_facts(),
        explanation_context=ctx,
        report_text="经营现金流由预收款驱动。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert CAUSE_UNSUPPORTED not in codes
    assert ACCOUNTING_POLICY_UNKNOWN not in codes
    assert integrity["is_valid"] is True

    claims = integrity.get("claims") or []
    acct_claims = [c for c in claims if c["metric"] == "预收款"]
    assert acct_claims, "should have a 预收款 claim"
    assert acct_claims[0]["evidence_ids"], "evidence IDs must be preserved"


def test_claim_id_stability():
    """[P2] 同一输入运行两次，claim_id 必须完全一致。"""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升。"

    claims1 = bind_claims(report, ctx)
    claims2 = bind_claims(report, ctx)

    cause1 = [c for c in claims1 if c["claim_id"].startswith("CAUSE")]
    cause2 = [c for c in claims2 if c["claim_id"].startswith("CAUSE")]
    assert len(cause1) == len(cause2) == 1
    assert cause1[0]["claim_id"] == cause2[0]["claim_id"], (
        f"claim_id must be deterministic: {cause1[0]['claim_id']} != {cause2[0]['claim_id']}"
    )
    assert cause1[0]["claim_id"].startswith("CAUSE-")
    assert len(cause1[0]["claim_id"]) == len("CAUSE-xxxxxxxx")


def test_long_evidence_context_preserves_keyword():
    """[P2] 关键词在文本后半段，evidence_id 的证据片段必须包含该关键词。"""
    padding = "这是无关的填充文字。" * 50
    long_announcement = f"{padding}公司公告显示原材料成本下降，主要系采购策略优化所致。"
    assert len(long_announcement) > 500

    ctx = build_official_explanation_context(
        announcements=long_announcement,
        half_year_facts=None,
    )
    assert ctx["entries"], "should have entries"

    entry = ctx["entries"][0]
    assert "原材料" in entry["text"], (
        f"evidence text must contain the matched keyword, got: ...{entry['text'][-100:]}"
    )


# ── FUND-003A-B: per-term binding adversarial tests ────────────────────────


def test_neutral_first_mention_plus_later_positive():
    """Adversarial: 报告首次提到"原材料"是中性描述，后文写"原材料下降推动毛利率"
    → 两个 occurrence 都应匹配到证据（方向不冲突）。"""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    report = "原材料方面保持关注。后文原材料下降推动毛利率提升。"
    claims = bind_claims(report, ctx)
    cause_claims = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    # "原材料" at pos 0 (neutral) + "原材料下降" at later pos → 2 claims
    assert len(cause_claims) == 2
    assert cause_claims[0]["status"] == "officially_explained"
    assert cause_claims[1]["status"] == "officially_explained"


def test_raw_material_decline_plus_contract_liability_increase():
    """Adversarial: 报告同时写"原材料下降"和"合同负债增加"，
    公告只有"合同负债增加" → 原材料 claim 必须 evidence_conflict，
    合同负债 claim 必须 officially_explained。"""
    ctx = build_official_explanation_context(
        announcements="公司合同负债增加，主要系预收客户货款增加所致。",
        half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升，同时合同负债增加表明订单增长。"
    claims = bind_claims(report, ctx)
    cause_claims = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause_claims) == 1
    assert cause_claims[0]["status"] == "evidence_conflict"
    acct_claims = [c for c in claims if c["metric"] == "合同负债"]
    assert len(acct_claims) == 1
    assert acct_claims[0]["status"] == "officially_explained"


def test_same_indicator_opposite_claims_in_report():
    """Adversarial: 报告写"原材料下降"和"原材料上涨"（矛盾声明），
    公告含"原材料成本下降" → 第一个 occurrence 应通过，第二个应被拒。"""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升，但近期原材料上涨带来成本压力。"
    claims = bind_claims(report, ctx)
    cause_claims = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause_claims) == 2
    assert cause_claims[0]["status"] == "officially_explained"
    assert cause_claims[1]["status"] == "evidence_conflict"


def test_different_causes_sharing_same_indicator():
    """Adversarial: 公告含"原材料下降"，报告写两个"原材料下降" → 都应通过。"""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    report = "原材料下降主要因为采购策略优化，同时行业周期也导致原材料下降。"
    claims = bind_claims(report, ctx)
    cause_claims = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    # "原材料下降" appears twice → 2 claims
    down_claims = [c for c in cause_claims if c["metric"] == "原材料下降"]
    assert len(down_claims) == 2
    for c in down_claims:
        assert c["status"] == "officially_explained"
    # "行业周期" also appears → 1 more claim
    cycle_claims = [c for c in cause_claims if c["metric"] == "行业周期"]
    assert len(cycle_claims) == 1


def test_multi_term_long_evidence():
    """Adversarial: 公告含多个术语（原材料+合同负债+预收款），
    报告写多个声明 → 每个声明独立匹配正确的证据。"""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，合同负债增加主要系预收货款增加所致。",
        half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升，同时合同负债和预收款驱动现金流改善。"
    claims = bind_claims(report, ctx)
    cause_claims = [c for c in claims if c["claim_id"].startswith("CAUSE")]
    assert len(cause_claims) == 1
    assert cause_claims[0]["status"] == "officially_explained"
    acct_claims = [c for c in claims if c["claim_id"].startswith("ACCT")]
    assert len(acct_claims) >= 1
    for c in acct_claims:
        assert c["status"] == "officially_explained"


def test_occurrences_have_direction_negation_per_term():
    """Each entry's term_details must have direction, negation per term."""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，未采用净额法确认收入。",
        half_year_facts=None,
    )
    assert ctx["entries"], "should have entries"
    entry = ctx["entries"][0]
    assert "term_details" in entry
    td = entry["term_details"]
    assert "原材料下降" in td
    assert td["原材料下降"]["direction"] == "down"
    assert td["原材料下降"]["negation"] is False
    assert "净额法" in td
    assert td["净额法"]["negation"] is True


def test_evidence_term_details_audit_trail():
    """Each evidence entry's occurrences provide audit trail."""
    ctx = build_official_explanation_context(
        announcements="公司原材料成本下降，主要系采购策略优化所致。",
        half_year_facts=None,
    )
    entry = ctx["entries"][0]
    occs = entry.get("occurrences") or []
    assert occs, "should have occurrences"
    cause_occs = [o for o in occs if o["canonical"] == "原材料下降"]
    assert cause_occs
    assert cause_occs[0]["direction"] == "down"
    assert cause_occs[0]["negation"] is False


def test_claim_id_occurrence_distinction():
    """Multiple occurrences of the same keyword produce distinct claim_ids."""
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本同比下降。",
        half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升。后文原材料下降进一步确认趋势。"
    claims = bind_claims(report, ctx)
    cause_claims = [c for c in claims if c["metric"] == "原材料下降"]
    assert len(cause_claims) == 2
    assert cause_claims[0]["claim_id"] != cause_claims[1]["claim_id"]

    claims2 = bind_claims(report, ctx)
    cause_claims2 = [c for c in claims2 if c["metric"] == "原材料下降"]
    assert cause_claims[0]["claim_id"] == cause_claims2[0]["claim_id"]
    assert cause_claims[1]["claim_id"] == cause_claims2[1]["claim_id"]


def test_claim_id_still_deterministic_for_single_occurrence():
    """Single occurrence still produces deterministic claim_id."""
    ctx = build_official_explanation_context(
        announcements="公司原材料采购成本同比下降。",
        half_year_facts=None,
    )
    report = "原材料下降推动毛利率提升。"
    claims1 = bind_claims(report, ctx)
    claims2 = bind_claims(report, ctx)
    cause1 = [c for c in claims1 if c["metric"] == "原材料下降"]
    cause2 = [c for c in claims2 if c["metric"] == "原材料下降"]
    assert len(cause1) == 1
    assert cause1[0]["claim_id"] == cause2[0]["claim_id"]
