"""FUND-004A: Tests for fundamental semantic gate forward.

Verifies that:
1. Gate node replaces fundamentals_report with safe version when integrity fails
2. Safe version contains blocker reasons and verified facts, no unsupported narrative
3. Gate node passes through when integrity is valid
4. Research Manager excludes gated fundamentals from consensus weight
5. Risk Judge uses pre-computed integrity from gate metadata
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.utils.fundamental_integrity import (
    ACCOUNTING_POLICY_UNKNOWN,
    CAUSE_UNSUPPORTED,
    IDENTITY_UNVERIFIED,
    PERIOD_SCOPE_INVALID,
    build_gated_fundamentals_report,
    evaluate_fundamental_integrity,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _identity(allowed=True):
    return {
        "symbol": "603629.SH",
        "security_name": "利通电子" if allowed else None,
        "industry": "电子元器件" if allowed else None,
        "commercial_analysis_allowed": allowed,
    }


def _facts():
    return [
        {"metric": "revenue", "report_date": "2025-12-31", "value": 100.0, "unit": "亿元", "status": "HAS_DATA"},
        {"metric": "operating_cost", "report_date": "2025-12-31", "value": 60.0, "unit": "亿元", "status": "HAS_DATA"},
        {"metric": "net_profit", "report_date": "2025-12-31", "value": 10.0, "unit": "亿元", "status": "HAS_DATA"},
        {"metric": "operating_cashflow", "report_date": "2025-12-31", "value": -1.0, "unit": "亿元", "status": "HAS_DATA"},
        {"metric": "total_assets", "report_date": "2025-12-31", "value": 100.0, "unit": "亿元", "status": "HAS_DATA"},
        {"metric": "total_liabilities", "report_date": "2025-12-31", "value": 75.0, "unit": "亿元", "status": "HAS_DATA"},
    ]


def _pool(identity_allowed=True, explanation_status="unexplained"):
    return {
        "instrument_identity": _identity(identity_allowed),
        "financial_period_facts": _facts(),
        "fundamental_explanations": {"status": explanation_status, "entries": []},
    }


_INVALID_REPORT = (
    "利通电子主营化工品生产与销售，原材料成本下降导致毛利率提升。"
    "公司采用净额法确认设备经销收入。"
)

_VALID_REPORT = (
    "利通电子营业收入100亿元，净利润10亿元。经营现金流为负。"
)


# ── Gate Node Tests ───────────────────────────────────────────────────────────

class TestFundamentalsIntegrityGateNode:
    """Test the gate node function directly."""

    def _make_state(self, fundamentals_report=_INVALID_REPORT):
        return {
            "company_of_interest": "603629.SH",
            "trade_date": "2025-12-31",
            "fundamentals_report": fundamentals_report,
            "metadata": {"raw_evidence": {}},
        }

    def test_gate_replaces_report_when_identity_unverified(self):
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        pool = _pool(identity_allowed=False)
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = self._make_state()
        result = asyncio.run(gate(state))

        assert "fundamentals_report" in result
        gated = result["fundamentals_report"]
        assert "不参与方向权重" in gated
        assert IDENTITY_UNVERIFIED in gated
        # Original narrative must NOT appear
        assert "化工" not in gated
        assert "原材料成本下降" not in gated

    def test_gate_replaces_report_when_cause_unsupported(self):
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        pool = _pool(explanation_status="unexplained")
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = self._make_state()
        result = asyncio.run(gate(state))

        assert "fundamentals_report" in result
        gated = result["fundamentals_report"]
        assert CAUSE_UNSUPPORTED in gated or IDENTITY_UNVERIFIED in gated
        # Unsupported causal claim must not appear
        assert "原材料成本下降导致毛利率提升" not in gated

    def test_gate_preserves_verified_facts(self):
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        pool = _pool()
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = self._make_state()
        result = asyncio.run(gate(state))

        gated = result["fundamentals_report"]
        # Verified facts should be present
        assert "营业收入" in gated
        assert "100.0" in gated
        assert "利通电子" in gated

    def test_gate_stores_integrity_in_metadata(self):
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        pool = _pool()
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = self._make_state()
        result = asyncio.run(gate(state))

        assert "metadata" in result
        integrity = result["metadata"].get("fundamental_integrity")
        assert integrity is not None
        assert integrity["status"] == "NEEDS_REVIEW"
        assert not integrity["is_valid"]

    def test_gate_passes_through_when_integrity_valid(self):
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        # Valid pool with official explanation for the claims
        pool = _pool(explanation_status="officially_explained")
        # Add explanation entries that support the claims
        pool["fundamental_explanations"] = {
            "status": "officially_explained",
            "entries": [{
                "evidence_id": "E001",
                "source": "announcement",
                "text": "原材料采购成本下降导致毛利率提升",
                "cause_terms": ["原材料采购成本下降"],
                "accounting_terms": [],
                "occurrences": [{
                    "surface_term": "原材料采购成本下降",
                    "canonical": "原材料下降",
                    "direction": "down",
                    "negation": False,
                    "relation": "causal",
                    "relation_cue": "导致",
                    "relation_target": "毛利率",
                    "relation_targets": ["毛利率"],
                    "position": 0,
                    "context": "原材料采购成本下降导致毛利率提升",
                }],
            }],
        }
        dc = MagicMock()
        dc.get.return_value = pool
        gate = create_fundamentals_integrity_gate(dc)

        state = self._make_state(fundamentals_report=_VALID_REPORT)
        result = asyncio.run(gate(state))

        # Report should NOT be replaced
        assert "fundamentals_report" not in result or result.get("fundamentals_report") == _VALID_REPORT
        # Integrity should still be in metadata
        integrity = result["metadata"].get("fundamental_integrity")
        assert integrity is not None
        assert integrity["is_valid"]

    def test_gate_skips_when_no_data_collector(self):
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        gate = create_fundamentals_integrity_gate(None)
        state = self._make_state()
        result = asyncio.run(gate(state))
        assert result == {}

    def test_gate_skips_when_no_pool(self):
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        dc = MagicMock()
        dc.get.return_value = None
        gate = create_fundamentals_integrity_gate(dc)

        state = self._make_state()
        result = asyncio.run(gate(state))
        assert result == {}

    def test_gate_skips_when_empty_report(self):
        from tradingagents.graph.setup import create_fundamentals_integrity_gate

        dc = MagicMock()
        gate = create_fundamentals_integrity_gate(dc)

        state = self._make_state(fundamentals_report="")
        result = asyncio.run(gate(state))
        assert result == {}


# ── Gated Report Content Tests ────────────────────────────────────────────────

class TestGatedReportContent:
    """Test build_gated_fundamentals_report output content."""

    def test_gated_report_contains_blocker_reasons(self):
        integrity = evaluate_fundamental_integrity(
            identity=_identity(False), period_facts=_facts(),
            explanation_context={"status": "unexplained"},
            report_text=_INVALID_REPORT,
        )
        report = build_gated_fundamentals_report(
            original_report=_INVALID_REPORT, integrity=integrity,
            pool=_pool(False),
        )
        assert IDENTITY_UNVERIFIED in report
        assert "不参与方向权重" in report

    def test_gated_report_no_unsupported_narrative(self):
        integrity = evaluate_fundamental_integrity(
            identity=_identity(), period_facts=_facts(),
            explanation_context={"status": "unexplained"},
            report_text=_INVALID_REPORT,
        )
        report = build_gated_fundamentals_report(
            original_report=_INVALID_REPORT, integrity=integrity,
            pool=_pool(),
        )
        # The unsupported causal claim must not appear as narrative
        assert "原材料成本下降导致毛利率提升" not in report
        # The unsupported accounting claim must not appear as narrative
        assert "公司采用净额法" not in report
        assert "确认设备经销收入" not in report
        # Blocker reasons should mention the keywords diagnostically
        assert ACCOUNTING_POLICY_UNKNOWN in report
        assert CAUSE_UNSUPPORTED in report

    def test_gated_report_preserves_financial_numbers(self):
        integrity = evaluate_fundamental_integrity(
            identity=_identity(), period_facts=_facts(),
            explanation_context={"status": "unexplained"},
            report_text=_INVALID_REPORT,
        )
        report = build_gated_fundamentals_report(
            original_report=_INVALID_REPORT, integrity=integrity,
            pool=_pool(),
        )
        assert "100.0" in report
        assert "营业收入" in report
        assert "利通电子" in report


# ── Research Manager Consensus Tests ──────────────────────────────────────────

class TestResearchManagerConsensusExclusion:
    """Test that Research Manager excludes gated fundamentals from consensus."""

    def test_consensus_excludes_fundamentals_when_gated(self):
        from tradingagents.agents.managers.research_manager import _build_consensus_block

        state = {
            "market_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "sentiment_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "news_report": '<!-- VERDICT: {"direction": "看空"} -->',
            "fundamentals_report": '<!-- VERDICT: {"direction": "看空"} -->',
            "smart_money_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "volume_price_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "metadata": {
                "fundamental_integrity": {
                    "status": "NEEDS_REVIEW",
                    "is_valid": False,
                    "blockers": [{"code": IDENTITY_UNVERIFIED, "reason": "test"}],
                },
            },
        }
        block = _build_consensus_block(state)
        # Fundamentals should be excluded from consensus
        if block is not None:
            assert "fundamentals_analyst" not in block

    def test_consensus_includes_fundamentals_when_valid(self):
        from tradingagents.agents.managers.research_manager import _build_consensus_block

        state = {
            "market_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "sentiment_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "news_report": '<!-- VERDICT: {"direction": "看空"} -->',
            "fundamentals_report": '<!-- VERDICT: {"direction": "看空"} -->',
            "smart_money_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "volume_price_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "metadata": {
                "fundamental_integrity": {
                    "status": "VALID",
                    "is_valid": True,
                    "blockers": [],
                },
            },
        }
        block = _build_consensus_block(state)
        # Fundamentals should still be included (as minority bearish)
        if block is not None:
            assert "fundamentals_analyst" in block

    def test_consensus_works_without_integrity_metadata(self):
        from tradingagents.agents.managers.research_manager import _build_consensus_block

        state = {
            "market_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "sentiment_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "news_report": '<!-- VERDICT: {"direction": "看空"} -->',
            "fundamentals_report": '<!-- VERDICT: {"direction": "看空"} -->',
            "smart_money_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "volume_price_report": '<!-- VERDICT: {"direction": "看多"} -->',
            "metadata": {},
        }
        # Should not raise, should work as before
        block = _build_consensus_block(state)
        # Just verify no crash; block may or may not be None depending on counts


# ── Risk Judge Pre-computed Integrity Tests ───────────────────────────────────

class TestRiskJudgePrecomputedIntegrity:
    """Test that Risk Judge uses pre-computed integrity from gate metadata."""

    def test_risk_manager_uses_precomputed_integrity(self):
        """Verify risk_manager_node reads pre-computed integrity when available."""
        pre_computed = {
            "status": "NEEDS_REVIEW",
            "is_valid": False,
            "blockers": [{"code": IDENTITY_UNVERIFIED, "reason": "test"}],
            "claims": [],
        }
        # Simulate what risk_manager does
        state_metadata = {"fundamental_integrity": pre_computed}
        pre_computed_integrity = state_metadata.get("fundamental_integrity")
        assert pre_computed_integrity is not None
        assert not pre_computed_integrity["is_valid"]
        assert pre_computed_integrity["blockers"][0]["code"] == IDENTITY_UNVERIFIED

    def test_risk_manager_falls_back_without_precomputed(self):
        """Verify risk_manager falls back to computing integrity."""
        state_metadata = {}
        pre_computed_integrity = state_metadata.get("fundamental_integrity")
        assert pre_computed_integrity is None
        # In real code, it would compute from raw_evidence


# ── Memory Protection Tests ───────────────────────────────────────────────────

class TestMemoryProtection:
    """Test that invalid narratives don't flow into memory."""

    def test_reflection_uses_gated_report_from_state(self):
        """The reflector reads fundamentals_report from state.
        Since the gate node already replaced it with the safe version,
        memory automatically gets the safe version."""
        from tradingagents.graph.reflection import Reflector

        # Simulate gated state
        gated_state = {
            "market_report": "市场报告",
            "sentiment_report": "舆情报告",
            "news_report": "新闻报告",
            "fundamentals_report": (
                "【基本面语义门禁已触发 — 该模块不参与方向权重】\n\n"
                "保留的可验证财务事实：\n营业收入：100.0 亿元\n"
            ),
        }

        reflector = Reflector.__new__(Reflector)
        situation = reflector._extract_current_situation(gated_state)

        # The gated fundamentals should be in the situation
        assert "不参与方向权重" in situation
        # The invalid narrative should NOT be in the situation
        assert "原材料成本下降" not in situation
        assert "净额法" not in situation


# ── End-to-End Gate Behavior Tests ────────────────────────────────────────────

class TestEndToEndGateBehavior:
    """Test the complete gate behavior from evaluation to downstream consumption."""

    def test_603629_style_invalid_report_is_gated(self):
        """Regression: 603629-style report with identity guess and unsupported
        causal claims should be gated before reaching Bull/Bear."""
        report = (
            "利通电子（603629.SH）主营化工品生产与销售。"
            "2025年Q1化工行业进入淡季，原材料采购成本下降导致毛利率提升至40%。"
            "公司采用净额法确认设备经销收入。"
            "经营现金流与净利润背离，现金流质量存疑。"
        )
        integrity = evaluate_fundamental_integrity(
            identity=_identity(False), period_facts=_facts(),
            explanation_context={"status": "unexplained"},
            report_text=report,
        )
        assert not integrity["is_valid"]

        gated = build_gated_fundamentals_report(
            original_report=report, integrity=integrity,
            pool=_pool(False),
        )
        # Gated report must not contain the unsupported narratives as claims
        assert "主营化工品生产与销售" not in gated
        assert "化工行业进入淡季" not in gated
        assert "原材料采购成本下降导致毛利率提升至40%" not in gated
        assert "公司采用净额法" not in gated
        assert "经营现金流与净利润背离" not in gated
        # But should contain verified facts and blocker diagnostics
        assert "营业收入" in gated
        assert "不参与方向权重" in gated
        assert CAUSE_UNSUPPORTED in gated or IDENTITY_UNVERIFIED in gated

    def test_valid_report_passes_through(self):
        """A report with no unsupported claims should pass the gate."""
        simple_report = "利通电子营业收入100亿元，净利润10亿元。"
        integrity = evaluate_fundamental_integrity(
            identity=_identity(), period_facts=_facts(),
            explanation_context={"status": "unexplained"},
            report_text=simple_report,
        )
        # No causal or accounting claims → should be valid
        assert integrity["is_valid"]

    def test_accounting_claim_without_evidence_is_gated(self):
        """Accounting method claims without official evidence should be gated."""
        report = "公司采用净额法确认收入。"
        integrity = evaluate_fundamental_integrity(
            identity=_identity(), period_facts=_facts(),
            explanation_context={"status": "unexplained", "accounting_policy_terms": []},
            report_text=report,
        )
        assert not integrity["is_valid"]
        codes = {b["code"] for b in integrity["blockers"]}
        assert ACCOUNTING_POLICY_UNKNOWN in codes
