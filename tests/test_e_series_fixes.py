"""Tests for E-series fixes (E-001 through E-005)."""

import pytest
from tradingagents.graph.signal_processing import _extract_decision_keyword, _execution_layer_overrides_hold
from tradingagents.agents.utils.trade_setup import _first_price
from tradingagents.agents.utils.readiness_score import (
    EvidenceStatus,
    calculate_evidence_coverage,
    get_strong_action_gate,
)


# ── E-001: Execution layer overrides VERDICT ──────────────────────


class TestE001ExecutionLayerOverrides:
    """P0: Strong Action Gate not passed → final signal must be HOLD."""

    def test_strong_action_gate_failed_returns_hold(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看多\"} -->\n"
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：未通过\n"
            "- 降级原因：source_coverage=60% < 70%\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"

    def test_strong_action_gate_passed_allows_verdict(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看多\"} -->\n"
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：通过\n"
        )
        assert _extract_decision_keyword(text) == "BUY"

    def test_system_action_wait_returns_hold(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"BUY\"} -->\n"
            "### 执行质检\n"
            "- 系统动作：等待触发\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"

    def test_system_action_manual_review_returns_hold(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看空\"} -->\n"
            "### 执行质检\n"
            "- 系统动作：等待人工复核\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"

    def test_c001_auto_convert_returns_hold(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"SELL\"} -->\n"
            "⚠️ [C-001] 未持仓状态，已将减仓/清仓建议自动转换为观望（WAIT）。\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"

    def test_no_override_normal_verdict(self):
        text = "<!-- VERDICT: {\"direction\": \"中性\"} -->\n一些中性内容"
        assert _extract_decision_keyword(text) == "HOLD"

    def test_execution_layer_overrides_helper_gate_failed(self):
        text = "- Strong Action Gate：未通过\n- 降级原因：source_coverage=60%"
        assert _execution_layer_overrides_hold(text) is True

    def test_execution_layer_overrides_helper_gate_passed(self):
        text = "- Strong Action Gate：通过"
        assert _execution_layer_overrides_hold(text) is False


# ── E-002: Markdown bold price labels ──────────────────────────────


class TestE002MarkdownBoldPrice:
    """P1: Stop-loss price regex handles **止损价**：165.00 format."""

    def test_plain_label(self):
        assert _first_price("止损价：165.00", ("止损价",)) == 165.0

    def test_bold_label_cn_colon(self):
        assert _first_price("**止损价**：165.00", ("止损价",)) == 165.0

    def test_bold_label_en_colon(self):
        assert _first_price("**止损价**: 165.00", ("止损价",)) == 165.0

    def test_bold_label_with_spaces(self):
        assert _first_price("**止损价** ： 165.50", ("止损价",)) == 165.5

    def test_multiple_labels_bold(self):
        text = "触发位：160.00\n**止损价**：165.00"
        assert _first_price(text, ("止损价",)) == 165.0

    def test_no_price_returns_none(self):
        assert _first_price("**止损价**：无", ("止损价",)) is None


# ── E-004: NOT_AVAILABLE status ────────────────────────────────────


class TestE004NotAvailable:
    """P3: Missing fields marked as not_available, excluded from coverage."""

    def test_not_available_excluded_from_coverage(self):
        """NOT_AVAILABLE fields should not drag down coverage."""
        coverage = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.HAS_DATA,
            turnover_rate=EvidenceStatus.NOT_AVAILABLE,  # excluded
            volume_ratio=EvidenceStatus.HAS_DATA,
            individual_fund_flow=EvidenceStatus.HAS_DATA,
            lhb_status=EvidenceStatus.NORMAL_NO_DATA,
            margin_trading=EvidenceStatus.NOT_AVAILABLE,  # excluded
            announcements=EvidenceStatus.HAS_DATA,
        )
        # 6 counted (excluding 2 NOT_AVAILABLE), all 6 are valid
        assert coverage == 100

    def test_not_available_reduces_denominator(self):
        """With NOT_AVAILABLE, denominator shrinks but valid items still count."""
        coverage = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.HAS_DATA,
            volume=EvidenceStatus.FIELD_MISSING,
            turnover_rate=EvidenceStatus.NOT_AVAILABLE,
            volume_ratio=EvidenceStatus.HAS_DATA,
            individual_fund_flow=EvidenceStatus.HAS_DATA,
            lhb_status=EvidenceStatus.NORMAL_NO_DATA,
            margin_trading=EvidenceStatus.NOT_AVAILABLE,
            announcements=EvidenceStatus.HAS_DATA,
        )
        # 6 counted, 5 valid (volume is FIELD_MISSING)
        assert coverage == 83  # 5/6 ≈ 83%

    def test_all_not_available_returns_zero(self):
        """Edge case: all fields are NOT_AVAILABLE → 0% (0/0 = 0)."""
        coverage = calculate_evidence_coverage(
            ohlcv_5d=EvidenceStatus.NOT_AVAILABLE,
            volume=EvidenceStatus.NOT_AVAILABLE,
            turnover_rate=EvidenceStatus.NOT_AVAILABLE,
            volume_ratio=EvidenceStatus.NOT_AVAILABLE,
            individual_fund_flow=EvidenceStatus.NOT_AVAILABLE,
            lhb_status=EvidenceStatus.NOT_AVAILABLE,
            margin_trading=EvidenceStatus.NOT_AVAILABLE,
            announcements=EvidenceStatus.NOT_AVAILABLE,
        )
        assert coverage == 0

    def test_not_available_constant(self):
        assert EvidenceStatus.NOT_AVAILABLE == "not_available"


# ── E-001: Strong Action Gate with HOLD enforcement ────────────────


class TestE001StrongActionGateHOLD:
    """Verify Strong Action Gate results in HOLD when not passed."""

    def test_gate_not_passed_with_low_coverage(self):
        gate = get_strong_action_gate(
            source_coverage=60,
            evidence_coverage=80,
        )
        assert gate["passed"] is False
        assert any("source_coverage" in f for f in gate["failures"])

    def test_gate_passed_with_good_coverage(self):
        gate = get_strong_action_gate(
            source_coverage=80,
            evidence_coverage=80,
        )
        assert gate["passed"] is True
        assert gate["failures"] == []
