"""P0/P1 Final Acceptance Tests — Two Scenarios.

Scenario 1: Already holding 002353.SZ (100 shares @ cost 33.0)
  - HOLD must stay HOLD (not mapped to WAIT)
  - Stop-loss is required (do_not_trade_if must flag missing stop-loss)

Scenario 2: Not holding, conditional entry signal
  - Buy Level can be 2 (conditional entry) when entry conditions met
  - Conditional entry without stop-loss must be blocked by quality check
"""

import pytest
from tradingagents.graph.signal_processing import (
    SignalProcessor,
    _extract_decision_keyword,
    _execution_layer_overrides_hold,
)
from tradingagents.agents.utils.trade_setup import (
    build_trade_quality_check,
    format_trade_quality_check,
)
from tradingagents.agents.utils.readiness_score import (
    calculate_buy_level,
    format_execution_block,
    get_position_status,
)


# ═══════════════════════════════════════════════════════════════════════
# Scenario 1: Already holding 002353.SZ
# ═══════════════════════════════════════════════════════════════════════


class TestScenario1HasPosition:
    """Verify behavior when user already holds 002353.SZ (100 shares @ 33.0)."""

    # 1a. HOLD stays HOLD, never becomes WAIT
    def test_hold_stays_hold_with_position(self):
        """When has_position=True, signal extraction returns HOLD, not WAIT."""
        text = (
            "<!-- VERDICT: {\"direction\": \"中性\"} -->\n"
            "### 风控委员会最终裁决\n"
            "建议持有等待，不建议加仓。\n"
            "止损价：31.50元\n"
            "目标价：35.00元"
        )
        result = _extract_decision_keyword(text, has_position=True)
        assert result == "HOLD", f"Expected HOLD for has_position=True, got {result}"

    def test_execution_layer_does_not_override_hold_with_position(self):
        """When has_position=True, execution layer should NOT force non-actionable state
        unless there's a real gate failure."""
        # Normal text without gate failures
        text = (
            "### 执行等级与证据门禁\n"
            "- Strong Action Gate：通过\n"
            "- 系统动作：可执行但需核对盘面\n"
            "建议持有，止损价31.50元。"
        )
        result = _execution_layer_overrides_hold(text, has_position=True)
        assert result is False, "Should not override HOLD when position exists and gates pass"

    def test_reduce_signal_with_position(self):
        """REDUCE/EXIT is valid for has_position=True."""
        text = (
            "<!-- VERDICT: {\"direction\": \"看空\"} -->\n"
            "建议减仓至半仓，止损价：31.00元"
        )
        result = _extract_decision_keyword(text, has_position=True)
        assert result == "SELL", f"Expected SELL for reduce, got {result}"

    # 1b. Stop-loss is required when holding
    def test_stop_loss_required_when_holding(self):
        """When current_position > 0, missing stop-loss must be flagged."""
        check = build_trade_quality_check(
            investment_plan="建议持有等待反弹。",
            trader_plan="继续持有，不加仓不减仓。",
            final_decision="持仓观望，等待技术面企稳。",
            user_context={"current_position": 100, "average_cost": 33.0},
        )
        assert check["stop_loss_price"] is None, "No stop-loss in the text"
        assert any("无法从报告中解析出明确止损价" in item for item in check["do_not_trade_if"]), \
            f"Should flag missing stop-loss for held position, got: {check['do_not_trade_if']}"

    def test_stop_loss_found_when_holding(self):
        """When stop-loss is present, no error flag for held position."""
        check = build_trade_quality_check(
            investment_plan="建议持有等待反弹。",
            trader_plan="继续持有，止损价：31.50元。",
            final_decision="持仓观望，止损位31.50元。",
            user_context={"current_position": 100, "average_cost": 33.0},
        )
        assert check["stop_loss_price"] == 31.5, f"Should extract stop-loss 31.5, got {check['stop_loss_price']}"
        assert not any("无法从报告中解析出明确止损价" in item for item in check["do_not_trade_if"]), \
            f"Should NOT flag when stop-loss exists, got: {check['do_not_trade_if']}"

    # 1c. HOLD semantics preserved for has_position=True in process_signal
    def test_process_signal_hold_with_position(self):
        """process_signal returns HOLD (not WAIT) when has_position=True."""
        sp = SignalProcessor.__new__(SignalProcessor)  # skip __init__ (no LLM needed for rule path)
        text = "建议持有，不追高，止损31.50元。"
        result = sp.process_signal(text, has_position=True)
        assert result == "HOLD", f"Expected HOLD, got {result}"


# ═══════════════════════════════════════════════════════════════════════
# Scenario 2: Not holding, conditional entry signal
# ═══════════════════════════════════════════════════════════════════════


class TestScenario2ConditionalEntry:
    """Verify conditional entry behavior when user has no position."""

    # 2a. Buy Level 2 is possible with conditional entry conditions met
    def test_buy_level_2_conditional_entry_conditions_met(self):
        """Buy Level 2 (条件试仓) is achievable when entry conditions are confirmed."""
        result = calculate_buy_level(
            source_coverage=80,
            evidence_coverage=75,
            trend_confirmed=True,
            main_capital_inflow_days=2,
            volume_healthy_expansion=True,
            has_major_negative_announcement=False,
            no_execution_conflict=True,
            no_unresolved_analyst_conflict=True,
            position_status="no_position",
            name_mismatch=False,
            research_bearish=False,
        )
        assert result["level"] >= 2, f"Expected Buy Level >= 2 for conditional entry, got {result}"

    def test_buy_level_1_without_conditions(self):
        """Buy Level stays at 1 when entry conditions are NOT met (no position)."""
        result = calculate_buy_level(
            source_coverage=80,
            evidence_coverage=75,
            trend_confirmed=False,
            main_capital_inflow_days=0,
            position_status="no_position",
        )
        assert result["level"] <= 1, f"Expected Buy Level ≤1 without conditions, got {result}"

    # 2b. Conditional entry MUST have stop-loss
    def test_conditional_entry_without_stop_loss_blocked(self):
        """Conditional entry without stop-loss → quality check must flag it."""
        check = build_trade_quality_check(
            investment_plan="未持仓。若放量突破关键阻力位可考虑试仓。",
            trader_plan="若股价放量突破50.00元，可考虑建仓试仓。等待确认。",
            final_decision="触发条件：放量突破50.00元可建仓。建议关注。",
            user_context={"current_position": 0},
        )
        # execution_mode should be 条件触发
        assert check["execution_mode"] == "条件触发", f"Expected 条件触发, got {check['execution_mode']}"
        # Stop-loss is missing → must be flagged
        assert check["stop_loss_price"] is None
        assert any("无法从报告中解析出明确止损价" in item for item in check["do_not_trade_if"]), \
            f"Conditional entry without stop-loss should be flagged, got: {check['do_not_trade_if']}"

    def test_conditional_entry_with_stop_loss_passes(self):
        """Conditional entry with stop-loss → no block."""
        check = build_trade_quality_check(
            investment_plan="未持仓。若放量突破关键阻力位可考虑试仓。",
            trader_plan="若股价放量突破50.00元，可考虑建仓。止损价：48.00元。",
            final_decision="触发条件：放量突破50.00元可建仓。止损价48.00元。",
            user_context={"current_position": 0},
        )
        assert check["execution_mode"] == "条件触发"
        assert check["stop_loss_price"] == 48.0
        assert not any("无法从报告中解析出明确止损价" in item for item in check["do_not_trade_if"]), \
            f"Should NOT flag when stop-loss exists for conditional entry, got: {check['do_not_trade_if']}"

    # 2c. No position → HOLD maps to WAIT
    def test_hold_becomes_wait_no_position(self):
        """When has_position=False, HOLD from report must become WAIT."""
        text = (
            "<!-- VERDICT: {\"direction\": \"中性\"} -->\n"
            "建议观望等待更好的入场时机。\n"
            "系统动作：等待触发"
        )
        # _execution_layer_overrides_hold returns True for "等待触发"
        assert _execution_layer_overrides_hold(text, has_position=False) is True
        # keyword extraction returns HOLD
        result = _extract_decision_keyword(text, has_position=False)
        assert result == "HOLD"
        # But process_signal maps HOLD → WAIT when has_position=False
        sp = SignalProcessor.__new__(SignalProcessor)
        final = sp.process_signal(text, has_position=False)
        assert final == "WAIT", f"Expected WAIT for no-position HOLD, got {final}"

    # 2d. Execution block shows correct labels for no-position conditional entry
    def test_execution_block_shows_conditional_buy_level_2(self):
        """Execution block for no-position + Buy Level 2 shows conditional candidate."""
        block = format_execution_block(
            source_coverage=80,
            evidence_coverage=75,
            confidence="high",
            opportunity_score=65,
            risk_level=0,
            buy_level=2,
            position_status="no_position",
            analysis_intent="entry",
            position_context={"has_position": False},
            horizon="short",
        )
        assert "Buy Level" in block
        assert "WAIT/观察" in block or "条件候选" in block
        assert "未持仓" in block
