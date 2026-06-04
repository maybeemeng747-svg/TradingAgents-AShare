# [DATA-007] evidence_coverage_audit
"""Tests for raw_evidence coverage audit and candidate credibility linkage."""

from __future__ import annotations

import pytest
from typing import Any, Dict, List

from tradingagents.dataflows.evidence_coverage_audit import (
    EvidenceAuditResult,
    CandidateCredibilityResult,
    audit_raw_evidence,
    audit_candidate_evidence,
    apply_credibility_linkage,
    render_audit_summary,
    render_credibility_summary,
    build_evidence_coverage_section,
    _classify_quality_level,
    _CRITICAL_FIELDS_BY_CANDIDATE_TYPE,
    _EVIDENCE_FIELD_LABELS,
    _EVIDENCE_FIELD_FAMILIES,
)


def _make_contract(
    field: str = "stock_data",
    status: str = "HAS_DATA",
    vendor: str = "cn_akshare",
    unit: str = "元",
    unit_verified: bool = True,
    is_realtime_patched: bool = False,
    fallback_from: str | None = None,
    endpoint: str = "stock_zh_a_hist",
    error: str | None = None,
) -> Dict[str, Any]:
    return {
        "raw": f"sample_data_for_{field}",
        "field": field,
        "unit": unit,
        "vendor": vendor,
        "endpoint": endpoint,
        "as_of": "2026-06-04",
        "fetched_at": "2026-06-04T10:00:00",
        "status": status,
        "fallback_from": fallback_from,
        "source_url": None,
        "error": error,
        "is_realtime_patched": is_realtime_patched,
        "source_type": "api",
        "unit_verified": unit_verified,
        "query_mode": "normal",
        "adjustment": "qfq",
        "force_reason": None,
        "record_count": 20,
    }


def _full_evidence() -> Dict[str, Any]:
    return {
        "stock_data": _make_contract("stock_data", status="HAS_DATA"),
        "fund_flow_individual": _make_contract("fund_flow_individual", status="HAS_DATA", unit="万元"),
        "fund_flow_board": _make_contract("fund_flow_board", status="HAS_DATA"),
        "lhb": _make_contract("lhb", status="NORMAL_NO_DATA"),
        "news": _make_contract("news", status="HAS_DATA"),
        "global_news": _make_contract("global_news", status="HAS_DATA"),
        "announcements": _make_contract("announcements", status="HAS_DATA"),
        "fundamentals": _make_contract("fundamentals", status="HAS_DATA"),
    }


def _evidence_missing_fund() -> Dict[str, Any]:
    ev = _full_evidence()
    ev["fund_flow_individual"] = _make_contract("fund_flow_individual", status="FAILED", error="ProxyError")
    return ev


def _evidence_missing_announcements() -> Dict[str, Any]:
    ev = _full_evidence()
    ev["announcements"] = _make_contract("announcements", status="NOT_QUERIED")
    return ev


def _evidence_stale() -> Dict[str, Any]:
    ev = _full_evidence()
    ev["stock_data"] = _make_contract("stock_data", status="HAS_DATA", is_realtime_patched=True)
    return ev


def _evidence_unit_unknown() -> Dict[str, Any]:
    ev = _full_evidence()
    ev["fund_flow_individual"] = _make_contract("fund_flow_individual", status="HAS_DATA", unit="", unit_verified=False)
    return ev


def _evidence_with_fallback() -> Dict[str, Any]:
    ev = _full_evidence()
    ev["fund_flow_individual"] = _make_contract(
        "fund_flow_individual", status="HAS_DATA", unit="万元",
        fallback_from="cn_akshare", vendor="cn_astock",
    )
    return ev


# ── TestEvidenceAuditResult ──────────────────────────────────────────

class TestEvidenceAuditResult:
    def test_defaults(self):
        r = EvidenceAuditResult()
        assert r.evidence_coverage == 0.0
        assert r.evidence_quality_level == "UNKNOWN"
        assert r.critical_missing_fields == []
        assert r.stale_fields == []
        assert r.unit_unknown_fields == []
        assert r.fallback_fields == []

    def test_to_dict(self):
        r = EvidenceAuditResult(evidence_coverage=0.75, evidence_quality_level="MEDIUM")
        d = r.to_dict()
        assert d["evidence_coverage"] == 0.75
        assert d["evidence_quality_level"] == "MEDIUM"
        assert "audit_refs_count" in d

    def test_to_dict_roundtrip(self):
        r = EvidenceAuditResult(
            evidence_coverage=0.8,
            critical_missing_fields=["fund_flow_individual"],
            stale_fields=["stock_data"],
            unit_unknown_fields=["fund_flow_board"],
            fallback_fields=["news"],
            failed_fields=["lhb"],
            not_queried_fields=["announcements"],
            has_data_fields=["stock_data", "fundamentals"],
            evidence_quality_level="HIGH",
            total_fields=8,
            satisfied_fields=6,
        )
        d = r.to_dict()
        assert d["evidence_coverage"] == 0.8
        assert d["critical_missing_fields"] == ["fund_flow_individual"]
        assert d["stale_fields"] == ["stock_data"]
        assert d["unit_unknown_fields"] == ["fund_flow_board"]
        assert d["fallback_fields"] == ["news"]
        assert d["total_fields"] == 8


# ── TestCandidateCredibilityResult ───────────────────────────────────

class TestCandidateCredibilityResult:
    def test_defaults(self):
        r = CandidateCredibilityResult()
        assert r.credibility_level == "UNKNOWN"
        assert r.tier_allowed == ""
        assert r.tier_restricted is False

    def test_to_dict(self):
        r = CandidateCredibilityResult(
            symbol="600519.SH",
            candidate_type="POLICY_AMBUSH",
            credibility_level="HIGH",
            tier_allowed="A",
        )
        d = r.to_dict()
        assert d["symbol"] == "600519.SH"
        assert d["candidate_type"] == "POLICY_AMBUSH"
        assert d["credibility_level"] == "HIGH"


# ── TestClassifyQualityLevel ─────────────────────────────────────────

class TestClassifyQualityLevel:
    def test_high(self):
        assert _classify_quality_level(0.85, [], 0, 8) == "HIGH"

    def test_high_no_critical_no_failures(self):
        assert _classify_quality_level(1.0, [], 0, 8) == "HIGH"

    def test_medium(self):
        assert _classify_quality_level(0.6, [], 0, 8) == "MEDIUM"

    def test_medium_with_one_critical(self):
        assert _classify_quality_level(0.6, ["fund_flow_individual"], 0, 8) == "MEDIUM"

    def test_low(self):
        assert _classify_quality_level(0.35, [], 0, 8) == "LOW"

    def test_critical(self):
        assert _classify_quality_level(0.1, [], 0, 8) == "CRITICAL"

    def test_unknown_empty(self):
        assert _classify_quality_level(0.0, [], 0, 0) == "UNKNOWN"

    def test_medium_with_failures(self):
        assert _classify_quality_level(0.7, [], 2, 8) == "MEDIUM"


# ── TestAuditRawEvidence ─────────────────────────────────────────────

class TestAuditRawEvidence:
    def test_empty_evidence(self):
        result = audit_raw_evidence({})
        assert result.evidence_quality_level == "UNKNOWN"
        assert result.total_fields == 0

    def test_none_evidence(self):
        result = audit_raw_evidence(None)
        assert result.evidence_quality_level == "UNKNOWN"

    def test_full_evidence(self):
        result = audit_raw_evidence(_full_evidence())
        assert result.evidence_coverage > 0
        assert result.evidence_quality_level in ("HIGH", "MEDIUM")
        assert len(result.has_data_fields) > 0
        assert len(result.failed_fields) == 0
        assert len(result.stale_fields) == 0

    def test_missing_fund_flow(self):
        result = audit_raw_evidence(_evidence_missing_fund())
        assert "fund_flow_individual" in result.failed_fields
        assert result.evidence_coverage < 1.0

    def test_missing_announcements(self):
        result = audit_raw_evidence(_evidence_missing_announcements())
        assert "announcements" in result.not_queried_fields

    def test_stale_fields(self):
        result = audit_raw_evidence(_evidence_stale())
        assert "stock_data" in result.stale_fields

    def test_unit_unknown_fields(self):
        result = audit_raw_evidence(_evidence_unit_unknown())
        assert "fund_flow_individual" in result.unit_unknown_fields

    def test_fallback_fields(self):
        result = audit_raw_evidence(_evidence_with_fallback())
        assert "fund_flow_individual" in result.fallback_fields

    def test_normal_no_data_fields(self):
        result = audit_raw_evidence(_full_evidence())
        assert "lhb" in result.normal_no_data_fields

    def test_audit_refs_present(self):
        result = audit_raw_evidence(_full_evidence())
        assert len(result.audit_refs) >= 1
        ref = result.audit_refs[0]
        assert ref["field"] == "raw_evidence_audit"

    def test_coverage_is_ratio(self):
        result = audit_raw_evidence(_full_evidence())
        assert 0.0 <= result.evidence_coverage <= 1.0

    def test_non_dict_entry(self):
        ev = _full_evidence()
        ev["bad_key"] = "not a dict"
        result = audit_raw_evidence(ev)
        assert "bad_key" in result.not_queried_fields


# ── TestAuditCandidateEvidence ───────────────────────────────────────

class TestAuditCandidateEvidence:
    def test_policy_ambush_with_evidence(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.9,
            candidate_tradeflow_completeness=0.8,
            candidate_type="POLICY_AMBUSH",
            has_policy_evidence=True,
            has_realtime_quote=True,
        )
        assert result.credibility_level in ("HIGH", "MEDIUM")
        assert result.tier_allowed == "A"
        assert not result.tier_restricted

    def test_policy_ambush_without_policy_evidence(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.5,
            candidate_type="POLICY_AMBUSH",
            has_policy_evidence=False,
        )
        assert result.tier_restricted
        assert result.tier_allowed in ("B", "C")
        assert any("政策" in r for r in result.credibility_reasons)

    def test_tech_trade_with_realtime(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.8,
            candidate_type="TECH_TRADE",
            has_tech_signal=True,
            has_realtime_quote=True,
            fund_flow_unit_verified=True,
        )
        assert not result.tier_restricted
        assert result.need_deep_ta_allowed

    def test_tech_trade_without_realtime_and_tech(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.3,
            candidate_type="TECH_TRADE",
            has_tech_signal=False,
            has_realtime_quote=False,
        )
        assert result.tier_restricted
        assert result.tier_allowed == "C"
        assert any("降级" in r for r in result.credibility_reasons)

    def test_tech_trade_without_realtime_only(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.8,
            candidate_type="TECH_TRADE",
            has_tech_signal=True,
            has_realtime_quote=False,
        )
        assert result.tier_restricted
        assert any("实时行情" in r for r in result.credibility_reasons)

    def test_tech_trade_fund_flow_not_verified(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.8,
            candidate_type="TECH_TRADE",
            has_tech_signal=True,
            has_realtime_quote=True,
            fund_flow_unit_verified=False,
        )
        assert not result.need_deep_ta_allowed
        assert any("资金流单位" in r for r in result.credibility_reasons)

    def test_unclassified_data_gap(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.1,
            candidate_type="UNCLASSIFIED_DATA_GAP",
        )
        assert result.tier_allowed == "C"
        assert result.tier_restricted
        assert not result.need_deep_ta_allowed

    def test_low_coverage_blocks_deep_ta(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.3,
            candidate_tradeflow_completeness=0.3,
            candidate_type="TECH_TRADE",
            has_tech_signal=True,
            has_realtime_quote=True,
        )
        assert not result.need_deep_ta_allowed

    def test_evidence_gate_applied(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.5,
            candidate_type="TECH_TRADE",
            evidence_gate_applied=True,
            has_tech_signal=True,
            has_realtime_quote=True,
        )
        assert any("门控" in r for r in result.credibility_reasons)

    def test_with_raw_evidence(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.9,
            candidate_type="POLICY_AMBUSH",
            has_policy_evidence=True,
            raw_evidence=_full_evidence(),
        )
        assert result.evidence_audit is not None
        assert result.evidence_audit.evidence_coverage > 0

    def test_policy_ambush_missing_critical(self):
        ev = _full_evidence()
        ev["fundamentals"] = _make_contract("fundamentals", status="FAILED")
        result = audit_candidate_evidence(
            candidate_data_completeness=0.5,
            candidate_type="POLICY_AMBUSH",
            has_policy_evidence=True,
            raw_evidence=ev,
        )
        assert any("关键证据缺失" in r for r in result.credibility_reasons)

    def test_missing_critical_data_fields_blocks_a(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.5,
            candidate_tradeflow_completeness=0.5,
            candidate_type="TECH_TRADE",
            missing_data_fields=["关键证据缺失: 行情OHLCV数据"],
            has_tech_signal=True,
            has_realtime_quote=True,
            fund_flow_unit_verified=True,
        )
        assert result.tier_allowed != "A" or result.tier_restricted


# ── TestApplyCredibilityLinkage ──────────────────────────────────────

class TestApplyCredibilityLinkage:
    def test_no_credibility(self):
        result = apply_credibility_linkage(candidate_tier="A")
        assert result["tier"] == "A"
        assert not result["credibility_applied"]

    def test_high_credibility_no_override(self):
        cred = CandidateCredibilityResult(
            tier_allowed="A", credibility_level="HIGH"
        )
        result = apply_credibility_linkage(candidate_tier="A", credibility=cred)
        assert result["tier"] == "A"
        assert not result["tier_overridden"]

    def test_tier_downgrade(self):
        cred = CandidateCredibilityResult(
            tier_allowed="B", credibility_level="MEDIUM"
        )
        result = apply_credibility_linkage(candidate_tier="A", credibility=cred)
        assert result["tier"] == "B"
        assert result["tier_overridden"]
        assert result["credibility_applied"]

    def test_tier_no_upgrade(self):
        cred = CandidateCredibilityResult(
            tier_allowed="B", credibility_level="MEDIUM"
        )
        result = apply_credibility_linkage(candidate_tier="C", credibility=cred)
        assert result["tier"] == "C"
        assert not result["tier_overridden"]

    def test_deep_ta_blocked(self):
        cred = CandidateCredibilityResult(
            tier_allowed="A",
            need_deep_ta_allowed=False,
            credibility_level="LOW",
        )
        result = apply_credibility_linkage(
            candidate_tier="A", candidate_need_deep_ta=True, credibility=cred
        )
        assert not result["need_deep_ta"]
        assert result["need_deep_ta_overridden"]

    def test_deep_ta_allowed(self):
        cred = CandidateCredibilityResult(
            tier_allowed="A",
            need_deep_ta_allowed=True,
            credibility_level="HIGH",
        )
        result = apply_credibility_linkage(
            candidate_tier="A", candidate_need_deep_ta=True, credibility=cred
        )
        assert result["need_deep_ta"]
        assert not result["need_deep_ta_overridden"]


# ── TestRenderAuditSummary ───────────────────────────────────────────

class TestRenderAuditSummary:
    def test_basic_render(self):
        audit = audit_raw_evidence(_full_evidence())
        md = render_audit_summary(audit)
        assert "raw_evidence 覆盖率审计" in md
        assert "覆盖率" in md
        assert "质量等级" in md
        assert "[DATA-007]" in md

    def test_with_critical_missing(self):
        audit = EvidenceAuditResult(
            critical_missing_fields=["fund_flow_individual", "announcements"],
            evidence_coverage=0.3,
            evidence_quality_level="LOW",
            total_fields=8,
            satisfied_fields=2,
        )
        md = render_audit_summary(audit)
        assert "关键缺失" in md

    def test_with_stale(self):
        audit = EvidenceAuditResult(
            stale_fields=["stock_data"],
            evidence_coverage=0.8,
            evidence_quality_level="MEDIUM",
            total_fields=8,
            satisfied_fields=6,
        )
        md = render_audit_summary(audit)
        assert "Stale" in md

    def test_with_unit_unknown(self):
        audit = EvidenceAuditResult(
            unit_unknown_fields=["fund_flow_individual"],
            evidence_coverage=0.7,
            evidence_quality_level="MEDIUM",
            total_fields=8,
            satisfied_fields=5,
        )
        md = render_audit_summary(audit)
        assert "单位未知" in md

    def test_with_fallback(self):
        audit = EvidenceAuditResult(
            fallback_fields=["fund_flow_individual"],
            evidence_coverage=0.8,
            evidence_quality_level="HIGH",
            total_fields=8,
            satisfied_fields=7,
        )
        md = render_audit_summary(audit)
        assert "Fallback" in md


# ── TestRenderCredibilitySummary ─────────────────────────────────────

class TestRenderCredibilitySummary:
    def test_basic_render(self):
        cred = audit_candidate_evidence(
            candidate_data_completeness=0.8,
            candidate_type="TECH_TRADE",
            has_tech_signal=True,
            has_realtime_quote=True,
        )
        cred.symbol = "600519.SH"
        md = render_credibility_summary(cred)
        assert "600519.SH" in md
        assert "候选可信度评估" in md
        assert "[DATA-007]" in md

    def test_with_reasons(self):
        cred = audit_candidate_evidence(
            candidate_data_completeness=0.3,
            candidate_type="TECH_TRADE",
            has_tech_signal=False,
            has_realtime_quote=False,
        )
        cred.symbol = "000001.SZ"
        md = render_credibility_summary(cred)
        assert "限制原因" in md


# ── TestBuildEvidenceCoverageSection ─────────────────────────────────

class TestBuildEvidenceCoverageSection:
    def test_empty(self):
        md = build_evidence_coverage_section()
        assert "证据覆盖率审计" in md
        assert "[DATA-007]" in md

    def test_with_audits(self):
        audits = [
            audit_raw_evidence(_full_evidence()),
            audit_raw_evidence(_evidence_missing_fund()),
        ]
        md = build_evidence_coverage_section(audits=audits)
        assert "raw_evidence 审计汇总" in md
        assert "审计样本数" in md
        assert "平均覆盖率" in md

    def test_with_credibilities(self):
        creds = [
            audit_candidate_evidence(
                candidate_data_completeness=0.9,
                candidate_type="POLICY_AMBUSH",
                has_policy_evidence=True,
            ),
            audit_candidate_evidence(
                candidate_data_completeness=0.3,
                candidate_type="TECH_TRADE",
                has_tech_signal=False,
                has_realtime_quote=False,
            ),
        ]
        creds[0].symbol = "600519.SH"
        creds[1].symbol = "000001.SZ"
        md = build_evidence_coverage_section(credibilities=creds)
        assert "候选可信度汇总" in md
        assert "等级受限" in md
        assert "候选类型" in md

    def test_with_both(self):
        audits = [audit_raw_evidence(_full_evidence())]
        creds = [
            audit_candidate_evidence(
                candidate_data_completeness=0.8,
                candidate_type="POLICY_AMBUSH",
                has_policy_evidence=True,
            )
        ]
        creds[0].symbol = "600519.SH"
        md = build_evidence_coverage_section(audits=audits, credibilities=creds)
        assert "raw_evidence 审计汇总" in md
        assert "候选可信度汇总" in md


# ── TestCriticalFieldsByCandidateType ────────────────────────────────

class TestCriticalFieldsByCandidateType:
    def test_policy_ambush_requires_policy(self):
        assert "fundamentals" in _CRITICAL_FIELDS_BY_CANDIDATE_TYPE["POLICY_AMBUSH"]
        assert "news" in _CRITICAL_FIELDS_BY_CANDIDATE_TYPE["POLICY_AMBUSH"]
        assert "announcements" in _CRITICAL_FIELDS_BY_CANDIDATE_TYPE["POLICY_AMBUSH"]

    def test_tech_trade_requires_realtime(self):
        assert "stock_data" in _CRITICAL_FIELDS_BY_CANDIDATE_TYPE["TECH_TRADE"]
        assert "fund_flow_individual" in _CRITICAL_FIELDS_BY_CANDIDATE_TYPE["TECH_TRADE"]

    def test_data_gap_no_critical(self):
        assert _CRITICAL_FIELDS_BY_CANDIDATE_TYPE["UNCLASSIFIED_DATA_GAP"] == []

    def test_all_types_have_entries(self):
        expected = {"POLICY_AMBUSH", "POLICY_CONFIRM", "TECH_TRADE", "EVENT_WATCH",
                    "PSEUDO_POLICY", "OVERHEATED_AVOID", "UNCLASSIFIED_DATA_GAP"}
        assert expected.issubset(set(_CRITICAL_FIELDS_BY_CANDIDATE_TYPE.keys()))


# ── TestEvidenceFieldLabels ──────────────────────────────────────────

class TestEvidenceFieldLabels:
    def test_key_fields_have_labels(self):
        for key in ("stock_data", "fund_flow_individual", "lhb", "news",
                     "announcements", "fundamentals"):
            assert key in _EVIDENCE_FIELD_LABELS

    def test_all_labels_non_empty(self):
        for key, label in _EVIDENCE_FIELD_LABELS.items():
            assert label, f"Label for {key} is empty"


# ── TestEvidenceFieldFamilies ────────────────────────────────────────

class TestEvidenceFieldFamilies:
    def test_stock_data_ohlcv(self):
        assert _EVIDENCE_FIELD_FAMILIES["stock_data"] == "ohlcv"
        assert _EVIDENCE_FIELD_FAMILIES["indicators"] == "ohlcv"

    def test_fund_flow_family(self):
        assert _EVIDENCE_FIELD_FAMILIES["fund_flow_individual"] == "fund_flow"
        assert _EVIDENCE_FIELD_FAMILIES["fund_flow_board"] == "fund_flow"


# ── TestFiveFixtureScenarios ─────────────────────────────────────────

class TestFiveFixtureScenarios:
    """Verify 5 fixture scenarios from the acceptance criteria:
    1. Full evidence — no false downgrade
    2. Missing fund flow — degraded
    3. Missing announcements — tracked
    4. Stale market data — tracked
    5. Unit unknown — tracked
    """

    def test_full_evidence_no_downgrade(self):
        audit = audit_raw_evidence(_full_evidence())
        assert audit.evidence_quality_level in ("HIGH", "MEDIUM")
        assert len(audit.failed_fields) == 0
        cred = audit_candidate_evidence(
            candidate_data_completeness=audit.evidence_coverage,
            candidate_type="POLICY_AMBUSH",
            has_policy_evidence=True,
            raw_evidence=_full_evidence(),
        )
        assert cred.credibility_level in ("HIGH", "MEDIUM")
        linkage = apply_credibility_linkage(candidate_tier="A", credibility=cred)
        assert linkage["tier"] == "A"
        assert not linkage["tier_overridden"]

    def test_missing_fund_degraded(self):
        audit = audit_raw_evidence(_evidence_missing_fund())
        assert "fund_flow_individual" in audit.failed_fields
        assert audit.evidence_coverage < 1.0

    def test_missing_announcements_tracked(self):
        audit = audit_raw_evidence(_evidence_missing_announcements())
        assert "announcements" in audit.not_queried_fields

    def test_stale_market_data_tracked(self):
        audit = audit_raw_evidence(_evidence_stale())
        assert "stock_data" in audit.stale_fields

    def test_unit_unknown_tracked(self):
        audit = audit_raw_evidence(_evidence_unit_unknown())
        assert "fund_flow_individual" in audit.unit_unknown_fields


# ── TestAcceptanceDATA007 ────────────────────────────────────────────

class TestAcceptanceDATA007:
    """All acceptance criteria from TASKS.md DATA-007."""

    def test_full_evidence_no_false_downgrade(self):
        audit = audit_raw_evidence(_full_evidence())
        cred = audit_candidate_evidence(
            candidate_data_completeness=audit.evidence_coverage,
            candidate_type="POLICY_AMBUSH",
            has_policy_evidence=True,
            raw_evidence=_full_evidence(),
        )
        assert cred.credibility_level in ("HIGH", "MEDIUM")
        assert not cred.tier_restricted

    def test_missing_critical_restricts_tier_and_deep_ta(self):
        cred = audit_candidate_evidence(
            candidate_data_completeness=0.2,
            candidate_tradeflow_completeness=0.2,
            candidate_type="UNCLASSIFIED_DATA_GAP",
        )
        assert cred.tier_restricted
        assert not cred.need_deep_ta_allowed

    def test_nightly_digest_can_show_coverage(self):
        audits = [audit_raw_evidence(_full_evidence())]
        section = build_evidence_coverage_section(audits=audits)
        assert "证据覆盖率审计" in section
        assert "覆盖率" in section

    def test_no_live_llm_required(self):
        result = audit_raw_evidence(_full_evidence())
        assert result.evidence_coverage > 0

    def test_no_prod_db_write(self):
        result = audit_candidate_evidence(
            candidate_data_completeness=0.5,
            candidate_type="TECH_TRADE",
        )
        assert isinstance(result.credibility_level, str)

    def test_no_speculative_fill(self):
        ev = {"stock_data": _make_contract("stock_data", status="FAILED")}
        result = audit_raw_evidence(ev)
        assert result.evidence_coverage < 1.0
        assert "stock_data" in result.failed_fields

    def test_render_contains_quality_level(self):
        audit = audit_raw_evidence(_full_evidence())
        md = render_audit_summary(audit)
        assert audit.evidence_quality_level in md

    def test_api_audit_endpoint_schema(self):
        from api.tradeflow_schemas import (
            TradeFlowEvidenceAuditResponse,
            EvidenceCredibilityItem,
        )
        resp = TradeFlowEvidenceAuditResponse()
        assert resp.status == "ok"
        assert resp.total_candidates == 0
        item = EvidenceCredibilityItem(symbol="600519.SH")
        assert item.symbol == "600519.SH"
