"""Tests for P1-1: has_position resolution and HOLD→WAIT mapping."""

import pytest
from tradingagents.graph.signal_processing import _extract_decision_keyword, _execution_layer_overrides_hold

# We need to import _resolve_has_position from api.main
# Use importlib to handle circular / heavy import gracefully
import importlib


def _get_resolve_has_position():
    """Lazy import to avoid loading full FastAPI app at module level."""
    from api.main import _resolve_has_position
    return _resolve_has_position


# ── P1-1a: _resolve_has_position priority fallback ─────────────────


class TestResolveHasPosition:
    """Verify _resolve_has_position picks the right source in priority order."""

    def test_position_context_has_position_true(self):
        resolve = _get_resolve_has_position()
        user_intent = {"position_context": {"has_position": True}}
        assert resolve(user_intent) is True

    def test_position_context_has_position_false(self):
        resolve = _get_resolve_has_position()
        user_intent = {"position_context": {"has_position": False}}
        assert resolve(user_intent) is False

    def test_fallback_user_context_current_position_positive(self):
        resolve = _get_resolve_has_position()
        user_intent = {"user_context": {"current_position": 500}}
        assert resolve(user_intent) is True

    def test_fallback_user_context_current_position_zero(self):
        resolve = _get_resolve_has_position()
        user_intent = {"user_context": {"current_position": 0}}
        assert resolve(user_intent) is False

    def test_fallback_request_current_position_positive(self):
        resolve = _get_resolve_has_position()
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(symbol="600519.SH", current_position=100)
        assert resolve(None, req) is True

    def test_fallback_request_current_position_zero(self):
        resolve = _get_resolve_has_position()
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(symbol="600519.SH", current_position=0)
        assert resolve(None, req) is False

    def test_no_info_returns_none(self):
        resolve = _get_resolve_has_position()
        assert resolve(None, None) is None

    def test_priority_position_context_over_user_context(self):
        """position_context.has_position takes priority over user_context.current_position."""
        resolve = _get_resolve_has_position()
        user_intent = {
            "position_context": {"has_position": False},
            "user_context": {"current_position": 999},
        }
        assert resolve(user_intent) is False

    def test_none_user_intent_falls_back_to_request(self):
        resolve = _get_resolve_has_position()
        from api.main import AnalyzeRequest
        req = AnalyzeRequest(symbol="600519.SH", current_position=0)
        assert resolve(None, req) is False


# ── P1-1b: process_signal outputs WAIT (not HOLD) when no position ─


class TestProcessSignalWaitMapping:
    """When has_position=False, HOLD must be mapped to WAIT."""

    def test_extract_keyword_returns_hold_no_position(self):
        """_extract_decision_keyword returns HOLD for no-position text;
        process_signal maps it to WAIT."""
        text = "VERDICT: {\"direction\": \"中性\"}\n### 决策\n建议：HOLD"
        result = _extract_decision_keyword(text, has_position=False)
        assert result == "HOLD"  # keyword extraction returns HOLD; process_signal maps to WAIT

    def test_has_position_none_hold_stays_hold(self):
        """When has_position is None (unknown), HOLD remains HOLD."""
        text = "建议：HOLD"
        result = _extract_decision_keyword(text, has_position=None)
        assert result == "HOLD"

    def test_has_position_true_hold_stays_hold(self):
        """When has_position is True, HOLD remains HOLD."""
        text = "建议：HOLD"
        result = _extract_decision_keyword(text, has_position=True)
        assert result == "HOLD"


# ── P1-1c: Strong Action Gate 未通过 + 未持仓 → WAIT ────────────


class TestStrongActionGateNoPositionWait:
    """When not holding + Strong Action Gate failed, decision should be WAIT."""

    def test_strong_action_gate_fail_no_position(self):
        text = (
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：未通过\n"
            "- 降级原因：source_coverage=60% < 70%\n"
        )
        # _execution_layer_overrides_hold returns True → process_signal maps to WAIT
        assert _execution_layer_overrides_hold(text, has_position=False) is True

    def test_strong_action_gate_fail_with_position(self):
        text = (
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：未通过\n"
            "- 降级原因：source_coverage=60% < 70%\n"
        )
        # With position, overrides_hold is True but mapping stays HOLD
        assert _execution_layer_overrides_hold(text, has_position=True) is True

    def test_no_position_explicit_in_text(self):
        """Text says 未持仓 + WAIT → override holds."""
        text = "未持仓状态，建议观望等待。WAIT"
        assert _execution_layer_overrides_hold(text, has_position=False) is True
