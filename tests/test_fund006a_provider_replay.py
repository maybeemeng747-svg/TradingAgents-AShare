"""[FUND-006A] Real provider format + full production graph offline replay.

Covers the complete deterministic pipeline from DataCollector sidecars
through the Fundamentals Integrity Gate to Research Manager consensus
exclusion and Risk Judge consumption.  No live LLM is called; all
fixtures use real provider output shapes (desensitized).

Five verification axes:
  1. Identity source — profile extraction from provider markdown
  2. Period formula — cumulative/single-quarter derivation
  3. Claim-evidence binding — causal + accounting term matching
  4. Research weight removal — consensus exclusion when gated
  5. Actual model trace — agent trace annotation fields
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from tradingagents.agents.utils.fundamental_integrity import (
    ACCOUNTING_POLICY_UNKNOWN,
    CAUSE_UNSUPPORTED,
    IDENTITY_UNVERIFIED,
    PERIOD_SCOPE_INVALID,
    build_gated_fundamentals_report,
    build_official_explanation_context,
    evaluate_fundamental_integrity,
    extract_financial_anomaly_inputs,
)
from tradingagents.agents.utils.agent_trace import (
    annotate_agent_traces,
    build_run_model_snapshot,
    _extract_actual_model_from_response,
    _model_for_tier_with_fallback,
)
from tradingagents.dataflows.financial_periods import (
    derive_single_quarters,
    normalize_financial_markdown,
    period_scope_for_date,
    render_financial_period_context,
)
from tradingagents.dataflows.instrument_identity import (
    InstrumentIdentity,
    build_instrument_identity,
    extract_profile_from_fundamentals,
    render_identity_context,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Axis 1: Identity Source — real provider markdown fixtures
# ═══════════════════════════════════════════════════════════════════════════════

# Real AKShare company_profile output shape (table format, desensitized)
_AKSHARE_PROFILE_TABLE = """### Company Profile

| 项目 | 内容 |
|---|---|
| 股票代码 | 603629 |
| 股票简称 | 利通电子 |
| 所属行业 | 计算机设备 |
| 主营业务 | 算力云服务及精密金属结构件 |
| 上市日期 | 2018-01-25 |
| 总股本 | 5.2亿股 |
"""

# Real cn_astock / eastmoney profile shape (bullet-list with **bold** markers)
_EASTMONEY_PROFILE_LIST = """### Company Profile

- **股票代码**：603629
- **股票简称**：利通电子
- **所属行业**：计算机设备
- **主营业务**：算力云服务及精密金属结构件
"""

# Real cninfo profile shape (for cross-source conflict test — different code)
_CNINFO_PROFILE_DIFFERENT_CODE = """### Company Profile

- **股票代码**：603629
- **股票简称**：利通电子
- **所属行业**：计算机设备
- **主营业务**：算力云服务及精密金属结构件

### Company Profile (巨潮资讯)

- **股票代码**：600001
- **股票简称**：邯郸钢铁
- **所属行业**：钢铁
- **主营业务**：钢铁冶炼
"""


class TestAxis1_IdentitySource:
    """Verify identity extraction from real provider markdown formats."""

    def test_akshare_table_format(self):
        """AKShare table-format profile extracts all identity fields."""
        profile = extract_profile_from_fundamentals(_AKSHARE_PROFILE_TABLE)
        assert profile is not None
        assert profile["security_name"] == "利通电子"
        assert profile["main_business"] == "算力云服务及精密金属结构件"
        assert profile["industry"] == "计算机设备"

    def test_eastmoney_list_format(self):
        """Eastmoney bullet-list format (with **bold** markers) extracts correctly."""
        profile = extract_profile_from_fundamentals(_EASTMONEY_PROFILE_LIST)
        assert profile is not None
        assert profile.get("security_name") == "利通电子"
        assert profile.get("main_business") == "算力云服务及精密金属结构件"

    def test_cninfo_cross_source_conflict(self):
        """Two sections with different 6-digit codes triggers CONFLICT."""
        profile = extract_profile_from_fundamentals(_CNINFO_PROFILE_DIFFERENT_CODE)
        assert profile is not None
        # Cross-source conflict should be detected
        assert profile.get("_cross_source_conflict") == "True"

    def test_identity_contract_has_data(self):
        """Build identity from profile → HAS_DATA, commercial_analysis_allowed."""
        profile = extract_profile_from_fundamentals(_AKSHARE_PROFILE_TABLE)
        identity = build_instrument_identity("603629.SH", profile, source="akshare")
        d = identity.to_dict()
        assert d["status"] == "HAS_DATA"
        assert d["commercial_analysis_allowed"] is True
        assert d["security_name"] == "利通电子"
        assert d["industry"] == "计算机设备"
        assert d["source"] == "akshare"

    def test_identity_missing_profile(self):
        """No profile → MISSING, no analysis."""
        identity = build_instrument_identity("603629.SH", None, source="provider_profile_missing")
        d = identity.to_dict()
        assert d["status"] == "MISSING"
        assert d["commercial_analysis_allowed"] is False
        assert len(d["missing_fields"]) > 0

    def test_identity_partial_profile(self):
        """Partial profile (name only) → PARTIAL, no analysis."""
        identity = build_instrument_identity(
            "603629.SH",
            {"security_name": "利通电子"},
            source="partial",
        )
        d = identity.to_dict()
        assert d["status"] == "PARTIAL"
        assert d["commercial_analysis_allowed"] is False

    def test_identity_cross_source_conflict(self):
        """Two sections with different codes → CONFLICT via _cross_source_conflict flag."""
        profile = extract_profile_from_fundamentals(_CNINFO_PROFILE_DIFFERENT_CODE)
        identity = build_instrument_identity("603629.SH", profile, source="multi_source")
        d = identity.to_dict()
        assert d["status"] == "CONFLICT"
        assert d["commercial_analysis_allowed"] is False

    def test_render_identity_context_injects_constraint(self):
        """render_identity_context adds hard constraint for incomplete identity."""
        identity = build_instrument_identity("603629.SH", None, source="missing")
        ctx = render_identity_context(identity.to_dict())
        assert "禁止推断" in ctx or "缺失" in ctx

    def test_render_identity_context_for_valid(self):
        """Valid identity renders name, industry, business in context."""
        profile = extract_profile_from_fundamentals(_AKSHARE_PROFILE_TABLE)
        identity = build_instrument_identity("603629.SH", profile, source="akshare")
        ctx = render_identity_context(identity.to_dict())
        assert "利通电子" in ctx
        assert "算力云服务" in ctx


# ═══════════════════════════════════════════════════════════════════════════════
# Axis 2: Period Formula — cumulative/single-quarter with mixed data
# ═══════════════════════════════════════════════════════════════════════════════

# Real AKShare income_statement output shape (desensitized, 603629-like)
_INCOME_STATEMENT_RAW = """| 报告日 | 营业总收入 | 营业成本 | 净利润 | 扣非净利润 |
|---|---:|---:|---:|---:|
| 2025-03-31 | 7.04342 | 5.62100 | 0.85200 | 0.81000 |
| 2025-06-30 | 15.16342 | 12.00000 | 1.82000 | 1.75000 |
| 2025-09-30 | 24.62342 | 19.50000 | 2.90000 | 2.78000 |
| 2025-12-31 | 33.07400 | 26.20000 | 3.85000 | 3.70000 |
"""

# Real AKShare cashflow output
_CASHFLOW_RAW = """| 报告日 | 经营活动现金流入 | 经营活动现金流出 | 经营活动产生的现金流量净额 | 投资活动产生的现金流量净额 | 筹资活动产生的现金流量净额 |
|---|---:|---:|---:|---:|---:|
| 2025-03-31 | 8.50000 | 7.90000 | 0.60000 | -0.30000 | -0.20000 |
| 2025-06-30 | 17.20000 | 16.00000 | 1.20000 | -0.50000 | -0.40000 |
| 2025-09-30 | 26.00000 | 24.50000 | 1.50000 | -0.80000 | -0.60000 |
| 2025-12-31 | 35.00000 | 33.50000 | 1.50000 | -1.00000 | -0.80000 |
"""

# Real AKShare balance_sheet (point-in-time, not cumulative)
_BALANCE_SHEET_RAW = """| 报告日 | 总资产 | 总负债 | 所有者权益 |
|---|---:|---:|---:|
| 2025-03-31 | 45.00000 | 30.00000 | 15.00000 |
| 2025-06-30 | 46.50000 | 31.00000 | 15.50000 |
| 2025-09-30 | 48.00000 | 32.00000 | 16.00000 |
| 2025-12-31 | 50.00000 | 33.00000 | 17.00000 |
"""


class TestAxis2_PeriodFormula:
    """Verify financial period normalization and single-quarter derivation."""

    def test_income_cumulative_recognized(self):
        """Income statement dates map to correct cumulative scopes."""
        facts = normalize_financial_markdown(
            _INCOME_STATEMENT_RAW, statement_type="income_statement", source="fixture",
        )
        scopes = {f.report_date: f.period_scope for f in facts if f.metric == "revenue"}
        assert scopes["2025-03-31"] == "Q1_YTD"
        assert scopes["2025-06-30"] == "H1_YTD"
        assert scopes["2025-09-30"] == "Q3_YTD"
        assert scopes["2025-12-31"] == "FY_YTD"

    def test_single_quarter_derivation(self):
        """Q2=H1-Q1, Q3=Q3-H1, Q4=FY-Q3 for revenue."""
        facts = normalize_financial_markdown(
            _INCOME_STATEMENT_RAW, statement_type="income_statement", source="fixture",
        )
        derived = derive_single_quarters(facts)
        by_date = {f.report_date: f for f in derived if f.metric == "revenue" and f.period_scope == "SINGLE_QUARTER"}

        # Q2 = 15.16342 - 7.04342 = 8.12000
        assert by_date["2025-06-30"].value == pytest.approx(8.12000, abs=0.001)
        assert by_date["2025-06-30"].formula == "H1_YTD-Q1_YTD"

        # Q3 = 24.62342 - 15.16342 = 9.46000
        assert by_date["2025-09-30"].value == pytest.approx(9.46000, abs=0.001)
        assert by_date["2025-09-30"].formula == "Q3_YTD-H1_YTD"

        # Q4 = 33.07400 - 24.62342 = 8.45058
        assert by_date["2025-12-31"].value == pytest.approx(8.45058, abs=0.001)
        assert by_date["2025-12-31"].formula == "FY_YTD-Q3_YTD"

    def test_cashflow_scopes(self):
        """Cashflow statement also derives single quarters."""
        facts = normalize_financial_markdown(
            _CASHFLOW_RAW, statement_type="cashflow", source="fixture",
        )
        derived = derive_single_quarters(facts)
        q4_ocf = next(
            f for f in derived
            if f.metric == "operating_cashflow"
            and f.report_date == "2025-12-31"
            and f.period_scope == "SINGLE_QUARTER"
        )
        # Q4 OCF = 1.5 - 1.5 = 0.0
        assert q4_ocf.value == pytest.approx(0.0, abs=0.001)

    def test_balance_sheet_point_in_time(self):
        """Balance sheet dates are POINT_IN_TIME, not cumulative."""
        facts = normalize_financial_markdown(
            _BALANCE_SHEET_RAW, statement_type="balance_sheet", source="fixture",
        )
        for f in facts:
            assert f.period_scope == "POINT_IN_TIME", f"{f.metric} {f.report_date}"

    def test_period_scope_for_date(self):
        """period_scope_for_date maps month-day to correct scope."""
        assert period_scope_for_date("2025-03-31", "income_statement") == "Q1_YTD"
        assert period_scope_for_date("2025-06-30", "income_statement") == "H1_YTD"
        assert period_scope_for_date("2025-09-30", "income_statement") == "Q3_YTD"
        assert period_scope_for_date("2025-12-31", "income_statement") == "FY_YTD"
        assert period_scope_for_date("2025-12-31", "balance_sheet") == "POINT_IN_TIME"

    def test_render_period_context_mentions_scopes(self):
        """render_financial_period_context includes scope labels."""
        facts = normalize_financial_markdown(
            _INCOME_STATEMENT_RAW, statement_type="income_statement", source="fixture",
        )
        all_facts = [f.to_dict() for f in facts] + [f.to_dict() for f in derive_single_quarters(facts)]
        ctx = render_financial_period_context(all_facts)
        assert "Q1_YTD" in ctx or "Q1" in ctx
        assert "SINGLE_QUARTER" in ctx or "单季" in ctx

    def test_mixed_cumulative_and_single_quarter_grouping(self):
        """FUND-004B: extract_financial_anomaly_inputs groups by same-date/same-scope/same-unit."""
        facts = normalize_financial_markdown(
            _INCOME_STATEMENT_RAW, statement_type="income_statement", source="fixture",
        )
        all_facts = facts + derive_single_quarters(facts)
        fact_dicts = [f.to_dict() for f in all_facts]
        inputs = extract_financial_anomaly_inputs(fact_dicts)

        # gross_margin should be computable (revenue and cost from same group)
        assert inputs.get("gross_margin") is not None, "gross_margin should be computed from same-group facts"

        # Cross-unit isolation: adding a different-unit fact should NOT affect the result
        cross_unit_facts = fact_dicts + [
            {"metric": "revenue", "report_date": "2025-12-31", "value": 330740.0, "unit": "万元", "period_scope": "FY_YTD", "status": "HAS_DATA"},
        ]
        inputs_cross = extract_financial_anomaly_inputs(cross_unit_facts)
        # gross_margin should still be computed from the 亿元 group, not corrupted by 万元
        assert inputs_cross.get("gross_margin") is not None
        assert inputs_cross["gross_margin"] == pytest.approx(inputs["gross_margin"], abs=0.01), (
            "cross-unit fact should not corrupt same-unit gross_margin"
        )

        # Cross-scope isolation: same-date/same-unit fact with DIFFERENT period_scope
        # and an obviously wrong revenue would corrupt the margin if period_scope
        # is dropped from the grouping key.
        wrong_revenue = 999999.0
        cross_scope_facts = fact_dicts + [
            {"metric": "revenue", "report_date": "2025-12-31", "value": wrong_revenue, "unit": "元", "period_scope": "SINGLE_QUARTER", "status": "HAS_DATA"},
        ]
        inputs_scope = extract_financial_anomaly_inputs(cross_scope_facts)
        assert inputs_scope.get("gross_margin") is not None
        # FY_YTD margin must NOT be corrupted by the SINGLE_QUARTER contaminant
        assert inputs_scope["gross_margin"] == pytest.approx(inputs["gross_margin"], abs=0.5), (
            "cross-scope contaminating fact must not corrupt FY_YTD gross_margin"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Axis 3: Claim-Evidence Binding
# ═══════════════════════════════════════════════════════════════════════════════

# Real announcement text (desensitized)
_ANNOUNCEMENT_TEXT = """
2025年度业绩快报：报告期内，公司实现营业收入33.074亿元，同比增长15.2%；
实现归属于上市公司股东的净利润3.85亿元，同比增长12.8%。
主要系原材料采购成本下降及算力云服务业务规模扩大所致。
公司采用总额法确认收入。
"""


class TestAxis3_ClaimEvidenceBinding:
    """Verify causal claim-evidence binding with real provider shapes."""

    def test_explanation_context_from_announcement(self):
        """build_official_explanation_context extracts cause/accounting terms."""
        ctx = build_official_explanation_context(
            announcements=_ANNOUNCEMENT_TEXT, half_year_facts=None,
        )
        assert ctx["status"] != "unexplained"
        # Should extract at least one entry
        assert len(ctx.get("entries", [])) > 0

    def test_bind_claims_match_official_evidence(self):
        """Report mentioning '原材料采购成本下降' generates claims matched against announcement."""
        from tradingagents.agents.utils.fundamental_integrity import bind_claims

        ctx = build_official_explanation_context(
            announcements=_ANNOUNCEMENT_TEXT, half_year_facts=None,
        )
        # Report must contain a cause keyword with direction or causal relation
        report = "原材料采购成本下降导致毛利率提升。"
        claims = bind_claims(report, ctx)
        # At least one claim should be generated
        assert len(claims) > 0
        # The claim about 原材料 should be found
        cause_claims = [c for c in claims if "原材料" in str(c.get("metric", ""))]
        assert len(cause_claims) > 0
        # Status is evidence_conflict because the announcement's evidence
        # occurrence has no relation_targets (the announcement text doesn't
        # explicitly mention 毛利率 as a target of the causal relation)
        assert cause_claims[0]["status"] in ("officially_explained", "evidence_conflict")

    def test_unsupported_claim_detected(self):
        """Claim not in announcement → unexplained or conflict."""
        from tradingagents.agents.utils.fundamental_integrity import bind_claims

        ctx = build_official_explanation_context(
            announcements=_ANNOUNCEMENT_TEXT, half_year_facts=None,
        )
        # Report mentions a cause keyword not in the announcement
        report = "产品涨价导致收入增长。"
        claims = bind_claims(report, ctx)
        # "产品涨价" is in _CAUSE_KEYWORDS and "导致" marks it as causal
        # but the announcement has no "产品涨价" evidence → unexplained
        assert len(claims) > 0, "产品涨价 claim should be generated from report"
        for c in claims:
            assert c["status"] != "officially_explained", (
                f"claim {c['metric']} should not be officially explained — "
                f"announcement has no matching evidence"
            )

    def test_accounting_policy_detected(self):
        """Accounting policy term '总额法' detected from announcement."""
        ctx = build_official_explanation_context(
            announcements=_ANNOUNCEMENT_TEXT, half_year_facts=None,
        )
        # The announcement mentions 总额法
        entries = ctx.get("entries", [])
        accounting_entries = [e for e in entries if e.get("accounting_terms")]
        # At least one entry must have accounting_terms and it must include 总额法
        assert len(accounting_entries) > 0, "announcement contains 总额法 but no accounting entries found"
        all_acct_terms = [t for e in accounting_entries for t in e["accounting_terms"]]
        assert "总额法" in all_acct_terms, f"总额法 not found in accounting terms: {all_acct_terms}"

    def test_negation_handling(self):
        """'未采用净额法' is detected as negated — conflicts with report claiming 采用净额法."""
        from tradingagents.agents.utils.fundamental_integrity import bind_claims

        announcement = "公司未采用净额法确认收入，仍采用总额法。"
        ctx = build_official_explanation_context(
            announcements=announcement, half_year_facts=None,
        )
        report = "公司采用净额法确认收入。"
        claims = bind_claims(report, ctx)
        # The report claims "净额法" (non-negated) but evidence says "未采用净额法" (negated)
        # → evidence_conflict (negation mismatch)
        assert len(claims) > 0, "净额法 claim should be generated from report"
        net_claims = [c for c in claims if c.get("metric") == "净额法"]
        assert len(net_claims) > 0, "净额法 claim not found in claims"
        assert net_claims[0]["status"] == "evidence_conflict", (
            f"negated evidence should conflict with non-negated claim, got {net_claims[0]['status']}"
        )

    def test_direction_conflict(self):
        """Evidence '原材料上涨' cannot support claim '原材料下降'."""
        from tradingagents.agents.utils.fundamental_integrity import _directions_conflict

        assert _directions_conflict("up", "down") is True
        assert _directions_conflict("down", "up") is True
        assert _directions_conflict("up", "up") is False
        assert _directions_conflict("down", "down") is False

    def test_synonym_normalization(self):
        """Synonyms like '原材料采购成本下降' → '原材料下降' are normalized."""
        from tradingagents.agents.utils.fundamental_integrity import _SYNONYM_MAP, _CAUSE_SYNONYM_MAP

        # _SYNONYM_MAP maps each synonym to the group's canonical form
        assert _SYNONYM_MAP.get("原材料采购成本下降") == "原材料下降"
        assert _SYNONYM_MAP.get("原材料价格下降") == "原材料下降"
        assert _SYNONYM_MAP.get("采购成本下降") == "原材料下降"
        assert _SYNONYM_MAP.get("原材料成本下降") == "原材料下降"
        assert _SYNONYM_MAP.get("预收货款") == "预收款"
        # _CAUSE_SYNONYM_MAP also maps cause synonyms
        assert _CAUSE_SYNONYM_MAP.get("原材料采购成本下降") == "原材料下降"

    def test_per_occurrence_binding(self):
        """FUND-003A-B: each occurrence of a keyword gets its own binding."""
        from tradingagents.agents.utils.fundamental_integrity import _extract_term_occurrences

        text = "原材料采购成本下降，但原材料价格仍处高位。"
        keywords = ["原材料"]
        occurrences = _extract_term_occurrences(text, keywords)
        # Should find at least 2 occurrences of '原材料'
        assert len(occurrences) >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# Axis 4: Research Weight Removal (consensus exclusion)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_state_with_reports(integrity_valid=True, fundamentals_verdict="看空"):
    """Build a realistic LangGraph state with all analyst reports."""
    integrity = {
        "status": "VALID" if integrity_valid else "NEEDS_REVIEW",
        "is_valid": integrity_valid,
        "blockers": [] if integrity_valid else [{"code": IDENTITY_UNVERIFIED, "reason": "test"}],
        "claims": [],
    }
    return {
        "company_of_interest": "603629.SH",
        "trade_date": "2025-12-31",
        "market_report": f'<!-- VERDICT: {{"direction": "看多"}} -->',
        "sentiment_report": f'<!-- VERDICT: {{"direction": "看多"}} -->',
        "news_report": f'<!-- VERDICT: {{"direction": "偏多"}} -->',
        "fundamentals_report": f'<!-- VERDICT: {{"direction": "{fundamentals_verdict}"}} -->',
        "smart_money_report": f'<!-- VERDICT: {{"direction": "看多"}} -->',
        "volume_price_report": f'<!-- VERDICT: {{"direction": "看多"}} -->',
        "game_theory_report": f'<!-- VERDICT: {{"direction": "中性"}} -->',
        "metadata": {"fundamental_integrity": integrity},
    }


class TestAxis4_ResearchWeightRemoval:
    """Verify consensus exclusion when fundamentals are gated."""

    def test_consensus_excludes_gated_fundamentals(self):
        """FUND-004A: fundamentals excluded from consensus when integrity fails."""
        from tradingagents.agents.managers.research_manager import _build_consensus_block

        state = _make_state_with_reports(integrity_valid=False)
        block = _build_consensus_block(state)
        if block is not None:
            assert "fundamentals_analyst" not in block

    def test_consensus_includes_valid_fundamentals(self):
        """Valid integrity → fundamentals still in consensus (as minority bearish)."""
        from tradingagents.agents.managers.research_manager import _build_consensus_block

        state = _make_state_with_reports(integrity_valid=True)
        block = _build_consensus_block(state)
        # With 5 bullish + 1 bearish (fundamentals) + 1 neutral, fundamentals
        # is the minority. The consensus block should mention it.
        if block is not None:
            # Fundamentals analyst appears as minority or in the block
            assert "fundamentals" in block.lower() or "少数" in block or "minority" in block.lower()

    def test_gate_node_replaces_report(self):
        """Gate node replaces fundamentals_report when integrity fails."""
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        pool = {
            "instrument_identity": {
                "symbol": "603629.SH",
                "security_name": None,
                "industry": None,
                "commercial_analysis_allowed": False,
            },
            "financial_period_facts": [
                {"metric": "revenue", "report_date": "2025-12-31", "value": 33.074, "status": "HAS_DATA"},
            ],
            "fundamental_explanations": {"status": "unexplained", "entries": []},
        }
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = {
            "company_of_interest": "603629.SH",
            "trade_date": "2025-12-31",
            "fundamentals_report": "原材料采购成本下降导致毛利率提升。公司采用净额法确认收入。",
            "metadata": {"raw_evidence": {}},
        }
        result = asyncio.run(gate(state))

        assert "fundamentals_report" in result
        gated = result["fundamentals_report"]
        # Unsupported narratives must not appear as claims
        assert "原材料采购成本下降导致毛利率提升" not in gated
        # Blocker diagnostics must appear
        assert "不参与方向权重" in gated

    def test_gate_stores_integrity_in_metadata(self):
        """Gate stores fundamental_integrity in state metadata."""
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        pool = {
            "instrument_identity": {
                "symbol": "603629.SH",
                "security_name": "利通电子",
                "industry": "计算机设备",
                "commercial_analysis_allowed": True,
            },
            "financial_period_facts": [
                {"metric": "revenue", "report_date": "2025-12-31", "value": 33.074, "status": "HAS_DATA"},
            ],
            "fundamental_explanations": {"status": "unexplained", "entries": []},
        }
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = {
            "company_of_interest": "603629.SH",
            "trade_date": "2025-12-31",
            "fundamentals_report": "公司营业收入33亿元。",
            "metadata": {"raw_evidence": {}},
        }
        result = asyncio.run(gate(state))
        integrity = result.get("metadata", {}).get("fundamental_integrity")
        assert integrity is not None
        assert "is_valid" in integrity
        assert "blockers" in integrity

    def test_full_integrity_eval_with_real_fixtures(self):
        """End-to-end: real provider data → evaluate_fundamental_integrity."""
        identity = build_instrument_identity(
            "603629.SH",
            extract_profile_from_fundamentals(_AKSHARE_PROFILE_TABLE),
            source="akshare",
        )
        facts = normalize_financial_markdown(
            _INCOME_STATEMENT_RAW, statement_type="income_statement", source="fixture",
        )
        all_facts = [f.to_dict() for f in facts] + [f.to_dict() for f in derive_single_quarters(facts)]

        ctx = build_official_explanation_context(
            announcements=_ANNOUNCEMENT_TEXT, half_year_facts=None,
        )

        # Report with unsupported claims
        report = "原材料采购成本下降导致毛利率提升。公司采用净额法确认收入。"
        integrity = evaluate_fundamental_integrity(
            identity=identity.to_dict(),
            period_facts=all_facts,
            explanation_context=ctx,
            report_text=report,
        )
        # Should fail because accounting policy claim is unsupported
        # (announcement says 总额法, not 净额法)
        codes = {b["code"] for b in integrity["blockers"]}
        assert ACCOUNTING_POLICY_UNKNOWN in codes or CAUSE_UNSUPPORTED in codes

    def test_valid_report_passes_integrity(self):
        """Report with only verified facts and no unsupported claims passes."""
        identity = build_instrument_identity(
            "603629.SH",
            extract_profile_from_fundamentals(_AKSHARE_PROFILE_TABLE),
            source="akshare",
        )
        facts = normalize_financial_markdown(
            _INCOME_STATEMENT_RAW, statement_type="income_statement", source="fixture",
        )
        all_facts = [f.to_dict() for f in facts] + [f.to_dict() for f in derive_single_quarters(facts)]

        ctx = build_official_explanation_context(
            announcements=_ANNOUNCEMENT_TEXT, half_year_facts=None,
        )

        # Simple factual report, no unsupported claims
        report = "利通电子营业收入33.074亿元，净利润3.85亿元。"
        integrity = evaluate_fundamental_integrity(
            identity=identity.to_dict(),
            period_facts=all_facts,
            explanation_context=ctx,
            report_text=report,
        )
        assert integrity["is_valid"]


# ═══════════════════════════════════════════════════════════════════════════════
# Axis 5: Actual Model Trace
# ═══════════════════════════════════════════════════════════════════════════════

class TestAxis5_AgentTrace:
    """Verify agent trace annotation with real config shapes."""

    def test_annotate_traces_attaches_routing_facts(self):
        """annotate_agent_traces adds provider/model/tier/digest."""
        config = {
            "llm_provider": "openai",
            "quick_think_llm": "gpt-4o-mini",
            "mid_think_llm": "gpt-4o",
            "deep_think_llm": "o1",
        }
        raw_traces = [
            {"agent": "fundamentals_analyst", "verdict": "看空", "latency_ms": 1500.0},
            {"agent": "market_analyst", "verdict": "看多", "latency_ms": 800.0},
        ]
        raw_evidence = {
            "instrument_identity": {"status": "HAS_DATA", "vendor": "akshare"},
            "financial_period_facts": {"status": "HAS_DATA", "vendor": "fixture"},
        }
        traces = annotate_agent_traces(raw_traces, config=config, raw_evidence=raw_evidence)

        assert len(traces) == 2
        fa = traces[0]
        assert fa["provider"] == "openai"
        assert fa["model"] == "gpt-4o"
        assert fa["model_tier"] == "mid"
        assert fa["requested_model"] == "gpt-4o"
        assert fa["input_contract_digest"].startswith("sha256:")
        assert len(fa["input_statuses"]) > 0

        ma = traces[1]
        assert ma["model"] == "gpt-4o-mini"
        assert ma["model_tier"] == "quick"

    def test_model_tier_fallback(self):
        """When mid_think_llm is empty, falls back to deep then quick."""
        config = {
            "llm_provider": "openai",
            "quick_think_llm": "gpt-4o-mini",
            "deep_think_llm": "o1",
        }
        model, fallback = _model_for_tier_with_fallback(config, "mid")
        assert model == "o1"
        assert fallback == "mid_think_llm"

    def test_run_model_snapshot(self):
        """build_run_model_snapshot captures all tier models."""
        config = {
            "llm_provider": "deepseek",
            "quick_think_llm": "deepseek-chat",
            "mid_think_llm": "deepseek-reasoner",
            "deep_think_llm": "deepseek-reasoner",
            "ultra_think_llm": "deepseek-reasoner",
        }
        snapshot = build_run_model_snapshot(config)
        assert snapshot["provider"] == "deepseek"
        assert snapshot["quick"] == "deepseek-chat"
        assert snapshot["mid"] == "deepseek-reasoner"

    def test_extract_actual_model_from_response(self):
        """_extract_actual_model_from_response extracts from response metadata."""
        chunk = MagicMock()
        chunk.response_metadata = {"model": "gpt-4o-2024-08-06"}
        assert _extract_actual_model_from_response(chunk) == "gpt-4o-2024-08-06"

    def test_extract_actual_model_none_chunk(self):
        """None chunk → None."""
        assert _extract_actual_model_from_response(None) is None

    def test_trace_privacy_no_secrets(self):
        """Traces never contain API keys or base URLs."""
        config = {
            "llm_provider": "openai",
            "quick_think_llm": "gpt-4o-mini",
            "api_key": "sk-secret-123",
            "base_url": "https://api.openai.com/v1",
        }
        traces = annotate_agent_traces(
            [{"agent": "market_analyst"}],
            config=config, raw_evidence={},
        )
        trace_str = str(traces)
        assert "sk-secret-123" not in trace_str
        assert "api.openai.com" not in trace_str

    def test_actual_model_from_response_overrides_default(self):
        """actual_model from LLM response takes precedence over config model."""
        config = {"llm_provider": "openai", "quick_think_llm": "gpt-4o-mini"}
        raw_traces = [{"agent": "market_analyst", "actual_model": "gpt-4o-2024-08-06"}]
        traces = annotate_agent_traces(raw_traces, config=config, raw_evidence={})
        assert traces[0]["actual_model"] == "gpt-4o-2024-08-06"


# ═══════════════════════════════════════════════════════════════════════════════
# Full Pipeline Offline Replay
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullPipelineOfflineReplay:
    """End-to-end offline replay simulating the full production graph path.

    DataCollector → Fundamentals Analyst prompt context →
    Integrity Gate → Bull/Bear → Research Manager → Risk Judge
    """

    def _build_realistic_pool(self, symbol="603629.SH"):
        """Build a DataCollector pool with real provider output shapes."""
        profile = extract_profile_from_fundamentals(_AKSHARE_PROFILE_TABLE)
        identity = build_instrument_identity(symbol, profile, source="akshare")

        income_facts = normalize_financial_markdown(
            _INCOME_STATEMENT_RAW, statement_type="income_statement", source="raw:income_statement",
        )
        cashflow_facts = normalize_financial_markdown(
            _CASHFLOW_RAW, statement_type="cashflow", source="raw:cashflow",
        )
        balance_facts = normalize_financial_markdown(
            _BALANCE_SHEET_RAW, statement_type="balance_sheet", source="raw:balance_sheet",
        )
        all_raw = income_facts + cashflow_facts + balance_facts
        all_derived = derive_single_quarters(income_facts + cashflow_facts)
        all_facts = all_raw + all_derived

        explanations = build_official_explanation_context(
            announcements=_ANNOUNCEMENT_TEXT, half_year_facts=None,
        )

        return {
            "instrument_identity": identity.to_dict(),
            "company_profile": {
                "status": identity.status,
                "raw": profile,
                "vendor": "akshare",
            },
            "financial_period_facts": [f.to_dict() for f in all_facts],
            "fundamental_explanations": explanations,
            "fundamentals": _AKSHARE_PROFILE_TABLE,
            "income_statement": _INCOME_STATEMENT_RAW,
            "cashflow": _CASHFLOW_RAW,
            "balance_sheet": _BALANCE_SHEET_RAW,
        }

    def test_identity_injected_into_analyst_context(self):
        """Identity context is rendered and injected into Fundamentals Analyst prompt."""
        pool = self._build_realistic_pool()
        ctx = render_identity_context(pool["instrument_identity"])
        assert "利通电子" in ctx
        assert "算力云服务" in ctx
        assert "计算机设备" in ctx

    def test_period_context_injected_into_analyst(self):
        """Period context includes scope labels and derived single quarters."""
        pool = self._build_realistic_pool()
        ctx = render_financial_period_context(pool["financial_period_facts"])
        # Should mention cumulative scopes
        assert "Q1" in ctx or "H1" in ctx or "累计" in ctx

    def test_integrity_gate_with_real_pool(self):
        """Gate replaces report when identity is missing."""
        pool = self._build_realistic_pool()
        # Simulate missing identity
        pool["instrument_identity"] = {
            "symbol": "603629.SH",
            "security_name": None,
            "industry": None,
            "main_business": None,
            "commercial_analysis_allowed": False,
            "status": "IDENTITY_MISSING",
            "missing_fields": ["security_name", "industry", "main_business"],
        }

        from tradingagents.graph.setup import create_fundamentals_integrity_gate
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = {
            "company_of_interest": "603629.SH",
            "trade_date": "2025-12-31",
            "fundamentals_report": "利通电子主营化工品，原材料下降导致毛利率提升。",
            "metadata": {"raw_evidence": {}},
        }
        result = asyncio.run(gate(state))
        gated = result["fundamentals_report"]
        # Unsupported narrative must not appear as a claim
        assert "化工" not in gated or "不参与方向权重" in gated
        assert "不参与方向权重" in gated

    def test_risk_judge_consumes_integrity(self):
        """Risk Judge reads pre-computed integrity from gate metadata."""
        pool = self._build_realistic_pool()

        # Run the gate
        from tradingagents.graph.setup import create_fundamentals_integrity_gate
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = {
            "company_of_interest": "603629.SH",
            "trade_date": "2025-12-31",
            "fundamentals_report": "原材料采购成本下降导致毛利率提升。公司采用净额法确认收入。",
            "metadata": {"raw_evidence": {}},
        }
        gate_result = asyncio.run(gate(state))

        # Simulate what Risk Judge sees
        metadata = gate_result.get("metadata", {})
        integrity = metadata.get("fundamental_integrity")
        assert integrity is not None
        assert not integrity["is_valid"]
        assert len(integrity["blockers"]) > 0

    def test_consensus_exclusion_after_gate(self):
        """After gate fires, Research Manager excludes fundamentals from consensus."""
        from tradingagents.agents.managers.research_manager import _build_consensus_block

        # Simulate state after gate replaced fundamentals_report
        state = {
            "market_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "sentiment_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "news_report": '<!-- VERDICT: {"direction": "偏多"} -->',
            "fundamentals_report": "【基本面语义门禁已触发 — 该模块不参与方向权重】\n保留的可验证财务事实：\n营业收入：33.074 亿元",
            "smart_money_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "volume_price_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "game_theory_report": '<!-- VERDICT: {"direction": "中性"} -->',
            "metadata": {
                "fundamental_integrity": {
                    "status": "NEEDS_REVIEW",
                    "is_valid": False,
                    "blockers": [{"code": IDENTITY_UNVERIFIED, "reason": "missing"}],
                },
            },
        }
        block = _build_consensus_block(state)
        if block is not None:
            assert "fundamentals_analyst" not in block

    def test_trace_annotation_on_full_run(self):
        """After full run, traces carry provider/model/tier/digest."""
        config = {
            "llm_provider": "deepseek",
            "quick_think_llm": "deepseek-chat",
            "mid_think_llm": "deepseek-reasoner",
        }
        raw_traces = [
            {"agent": "market_analyst", "verdict": "看多", "latency_ms": 900.0},
            {"agent": "fundamentals_analyst", "verdict": "看空", "latency_ms": 2000.0, "actual_model": "deepseek-reasoner"},
            {"agent": "bull_researcher", "verdict": "看多", "latency_ms": 1500.0},
            {"agent": "bear_researcher", "verdict": "看空", "latency_ms": 1400.0},
            {"agent": "risk_manager", "verdict": "中性", "latency_ms": 1200.0},
        ]
        pool = self._build_realistic_pool()
        raw_evidence = {
            "instrument_identity": {"status": "HAS_DATA", "vendor": "akshare"},
            "financial_period_facts": {"status": "HAS_DATA", "vendor": "fixture"},
        }
        traces = annotate_agent_traces(raw_traces, config=config, raw_evidence=raw_evidence)

        fa_trace = next(t for t in traces if t["agent"] == "fundamentals_analyst")
        assert fa_trace["model_tier"] == "mid"
        assert fa_trace["actual_model"] == "deepseek-reasoner"
        assert fa_trace["input_contract_digest"].startswith("sha256:")

        ma_trace = next(t for t in traces if t["agent"] == "market_analyst")
        assert ma_trace["model_tier"] == "quick"
        assert ma_trace["model"] == "deepseek-chat"

    def test_cross_industry_replay(self):
        """Replay across multiple stocks — identity preserved, not reclassified."""
        stocks = [
            ("603629.SH", "利通电子", "算力云服务及精密金属结构件", "计算机设备"),
            ("600519.SH", "贵州茅台", "白酒生产销售", "食品饮料"),
            ("600000.SH", "浦发银行", "商业银行服务", "银行"),
            ("300750.SZ", "宁德时代", "动力电池", "电力设备"),
        ]
        for symbol, name, business, industry in stocks:
            identity = build_instrument_identity(
                symbol,
                {"symbol": symbol[:6], "security_name": name, "main_business": business, "industry": industry},
            )
            d = identity.to_dict()
            assert d["commercial_analysis_allowed"] is True, f"{symbol} should allow analysis"
            assert d["industry"] == industry, f"{symbol} industry mismatch"

    def test_603629_historical_bug_regression(self):
        """Regression: 603629 historically guessed '化工' — now blocked."""
        integrity = evaluate_fundamental_integrity(
            identity={},
            period_facts=[{"metric": "revenue", "report_date": "2025-12-31", "value": 33.074, "status": "HAS_DATA"}],
            explanation_context={"status": "unexplained"},
            report_text="公司主营化工品生产销售，原材料采购成本下降导致毛利率提升。",
        )
        codes = {b["code"] for b in integrity["blockers"]}
        assert "IDENTITY_UNVERIFIED" in codes

    def test_financial_facts_retained_when_narrative_rejected(self):
        """Rejected narrative is removed while verified facts survive the gate."""
        facts = [{"metric": "revenue", "report_date": "2025-12-31", "value": 33.074, "status": "HAS_DATA"}]
        report_text = "原材料下降导致毛利率提升。"
        integrity = evaluate_fundamental_integrity(
            identity={}, period_facts=facts,
            explanation_context={"status": "unexplained"},
            report_text=report_text,
        )
        assert integrity["status"] == "NEEDS_REVIEW"
        assert len(integrity["claims"]) > 0, "cause keywords in report should generate claims"
        for c in integrity["claims"]:
            assert c["status"] == "unexplained", f"claim {c['metric']} should be unexplained"

        gated_report = build_gated_fundamentals_report(
            original_report=report_text,
            integrity=integrity,
            pool={"financial_period_facts": facts},
        )
        assert "【保留的可验证财务事实】" in gated_report
        assert "营业收入：33.074" in gated_report
        assert "2025-12-31" in gated_report
        assert report_text not in gated_report


# ═══════════════════════════════════════════════════════════════════════════════
# Adversarial / Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdversarialCases:
    """Edge cases and adversarial scenarios."""

    def test_empty_profile_gated(self):
        """Empty profile dict → identity gate blocks."""
        integrity = evaluate_fundamental_integrity(
            identity={}, period_facts=[],
            explanation_context={"status": "unexplained"},
            report_text="公司主营化工。",
        )
        codes = {b["code"] for b in integrity["blockers"]}
        assert "IDENTITY_UNVERIFIED" in codes

    def test_missing_period_facts_handled(self):
        """No financial facts → PERIOD_SCOPE_INVALID blocker added."""
        profile = extract_profile_from_fundamentals(_AKSHARE_PROFILE_TABLE)
        identity = build_instrument_identity("603629.SH", profile, source="akshare")
        integrity = evaluate_fundamental_integrity(
            identity=identity.to_dict(), period_facts=[],
            explanation_context={"status": "unexplained"},
            report_text="公司营业收入增长。",
        )
        # Empty period_facts should trigger PERIOD_SCOPE_INVALID blocker
        codes = {b["code"] for b in integrity["blockers"]}
        assert PERIOD_SCOPE_INVALID in codes, f"expected PERIOD_SCOPE_INVALID in {codes}"

    def test_future_report_date_no_crash(self):
        """Future report date doesn't crash period scope."""
        facts = normalize_financial_markdown(
            "| 报告日 | 营业总收入 |\n|---|---:|\n| 2099-12-31 | 100.0 |\n",
            statement_type="income_statement", source="fixture",
        )
        assert len(facts) > 0

    def test_duplicate_report_dates(self):
        """Duplicate report dates in provider output are handled without crash."""
        raw = """| 报告日 | 营业总收入 |
|---|---:|
| 2025-03-31 | 7.0 |
| 2025-03-31 | 7.0 |
| 2025-06-30 | 15.0 |
"""
        facts = normalize_financial_markdown(raw, statement_type="income_statement", source="fixture")
        # Should not crash; at least some facts are produced
        assert len(facts) >= 2, f"expected at least 2 facts from 3 rows, got {len(facts)}"
        # Duplicate dates should be preserved (last-wins or deduped)
        dates = {f.report_date for f in facts}
        assert "2025-03-31" in dates
        assert "2025-06-30" in dates

    def test_unit_mismatch_warning(self):
        """Different units across facts should be grouped separately."""
        facts = [
            {"metric": "revenue", "report_date": "2025-12-31", "value": 33.074, "unit": "亿元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-12-31", "value": 330740.0, "unit": "万元", "status": "HAS_DATA"},
        ]
        inputs = extract_financial_anomaly_inputs(facts)
        # Different units → revenue and cost are in different groups
        # → _find_best_group({"revenue", "operating_cost"}) returns None
        # → gross_margin must be None (not computed from cross-unit data)
        assert inputs.get("gross_margin") is None, (
            f"gross_margin should be None when units differ, got {inputs.get('gross_margin')}"
        )

    def test_gate_no_crash_on_malformed_state(self):
        """Gate handles malformed state gracefully."""
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        dc = MagicMock()
        dc.get.return_value = None
        gate = create_fundamentals_integrity_gate(dc)

        # Empty state
        result = asyncio.run(gate({}))
        assert result == {}

        # State with empty report
        result = asyncio.run(gate({"fundamentals_report": "", "company_of_interest": "X"}))
        assert result == {}
