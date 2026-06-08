"""DECISION-001: Final action semantic layering tests.

Covers 6 key scenarios where HOLD was previously overused, now differentiated
into structured research_direction / execution_action / action_label.
"""

import pytest
from tradingagents.graph.signal_processing import (
    _extract_decision_semantics,
    _derive_action_label,
    _extract_decision_keyword,
    _execution_layer_overrides_hold,
)


class TestDecisionSemanticsSixScenarios:
    """6 core scenarios from DECISION-001 spec."""

    def test_no_position_bullish_no_trigger(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏多\"} -->\n"
            "### 建议\n"
            "偏多方向，但当前价格尚未到理想买点，建议等待。\n"
            "系统动作：等待触发\n"
        )
        result = _extract_decision_semantics(text, has_position=False, trigger_price=None)
        assert result.research_direction == "偏多"
        assert result.execution_action == "WAIT"
        assert result.action_label == "等待触发"

    def test_no_position_strong_bullish_with_trigger(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看多\"} -->\n"
            "### 建议\n"
            "看多方向，放量突破50.00元可建仓。\n"
        )
        result = _extract_decision_semantics(text, has_position=False, trigger_price=50.0)
        assert result.research_direction == "看多"
        assert result.execution_action == "ENTER"
        assert result.action_label == "条件入场"

    def test_no_position_bearish(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏空\"} -->\n"
            "### 建议\n"
            "偏空方向，不建议介入。\n"
        )
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "偏空"
        assert result.execution_action == "WAIT"
        assert result.action_label == "回避"

    def test_has_position_neutral(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"中性\"} -->\n"
            "### 建议\n"
            "中性方向，已持仓建议继续持有。\n"
        )
        result = _extract_decision_semantics(text, has_position=True)
        assert result.research_direction == "中性"
        assert result.execution_action == "HOLD"
        assert result.action_label == "持有"

    def test_has_position_risk_rising(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"偏空\"} -->\n"
            "### 建议\n"
            "风险升高，建议减仓至半仓，止损价31.00元。\n"
        )
        result = _extract_decision_semantics(text, has_position=True, trigger_price=31.0)
        assert result.research_direction == "偏空"
        assert result.execution_action == "REDUCE"
        assert result.action_label == "条件减仓"

    def test_data_insufficient(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"中性\"} -->\n"
            "### 建议\n"
            "数据不足，无法给出明确判断，建议继续观察。\n"
        )
        result = _extract_decision_semantics(text, has_position=None)
        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"
        assert result.action_label == "数据不足观察"


class TestDeriveActionLabel:
    """Unit tests for _derive_action_label."""

    def test_wait_bullish_no_trigger(self):
        label = _derive_action_label(False, "偏多", "WAIT")
        assert label == "等待触发"

    def test_enter_with_trigger(self):
        label = _derive_action_label(False, "看多", "ENTER", trigger_price=50.0)
        assert label == "条件入场"

    def test_wait_bearish_no_position(self):
        label = _derive_action_label(False, "偏空", "WAIT")
        assert label == "回避"

    def test_hold_with_position(self):
        label = _derive_action_label(True, "中性", "HOLD")
        assert label == "持有"

    def test_reduce_with_position(self):
        label = _derive_action_label(True, "偏空", "REDUCE")
        assert label == "条件减仓"

    def test_exit_with_position(self):
        label = _derive_action_label(True, "看空", "EXIT")
        assert label == "条件清仓"


class TestExtractDecisionKeywordBackwardCompat:
    """Verify _extract_decision_keyword still works after refactor."""

    def test_verdict_buy(self):
        text = '<!-- VERDICT: {"direction": "看多"} -->\n建议买入'
        assert _extract_decision_keyword(text) == "BUY"

    def test_verdict_sell(self):
        text = '<!-- VERDICT: {"direction": "看空"} -->\n建议卖出'
        assert _extract_decision_keyword(text) == "SELL"

    def test_verdict_hold(self):
        text = '<!-- VERDICT: {"direction": "中性"} -->\n建议持有'
        assert _extract_decision_keyword(text) == "HOLD"

    def test_gate_failed_returns_hold(self):
        text = (
            '<!-- VERDICT: {"direction": "看多"} -->\n'
            "- Strong Action Gate：未通过\n"
        )
        assert _extract_decision_keyword(text) == "HOLD"


class TestExecutionLayerOverridesBackwardCompat:
    """Verify _execution_layer_overrides_hold still returns bool."""

    def test_gate_failed(self):
        text = "- Strong Action Gate：未通过\n"
        assert _execution_layer_overrides_hold(text) is True

    def test_no_override(self):
        text = "<!-- VERDICT: {\"direction\": \"中性\"} -->\n正常分析"
        assert _execution_layer_overrides_hold(text) is False


class TestExtractDecisionSemanticsGateBlocked:
    """When gate blocks, execution_action is WAIT regardless of direction."""

    def test_gate_blocked_bullish_becomes_wait(self):
        text = (
            "<!-- VERDICT: {\"direction\": \"看多\"} -->\n"
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：未通过\n"
        )
        result = _extract_decision_semantics(text, has_position=False, trigger_price=50.0)
        assert result.execution_action == "WAIT"
        assert result.decision == "HOLD"


class TestNegationHandling:
    """Negated buy phrases should produce 中性, not 偏多."""

    def test_negated_buy_in_explicit_pattern(self):
        text = "最终建议：不建议买入，继续观望"
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "中性"

    def test_negated_buy_with_verdict_uses_verdict(self):
        text = '<!-- VERDICT: {"direction": "偏空"} -->\n最终建议：不建议买入'
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "偏空"


class TestResolveReportFieldsSemantics:
    """Verify resolve_report_fields populates semantic fields."""

    def test_semantic_fields_populated(self):
        from api.services.report_service import resolve_report_fields
        result_data = {
            "final_trade_decision": '<!-- VERDICT: {"direction": "偏多"} -->\n偏多方向',
        }
        resolved = resolve_report_fields(result_data=result_data, has_position=False)
        assert resolved["research_direction"] == "偏多"
        assert resolved["execution_action"] == "WAIT"
        assert resolved["action_label"] == "等待触发"

    def test_semantic_fields_none_without_final_trade_decision(self):
        from api.services.report_service import resolve_report_fields
        resolved = resolve_report_fields(result_data={})
        assert resolved["research_direction"] is None
        assert resolved["execution_action"] is None
        assert resolved["action_label"] is None
