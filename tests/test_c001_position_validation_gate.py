"""C-001: Tests for position_validation_gate.

Verifies that:
1. No-position forbids reduce/exit/stop-loss keywords and passes through allowed actions
2. Has-position forbids enter/buy keywords and passes through allowed actions
3. Unknown position (no user_context) does not constrain actions
4. Field name safeguards (止损价/止损位) are not treated as violations
5. System blocks (⚠️/📊 markers) are excluded from detection
6. Multiple violations are all detected
7. format_position_validation_warning produces correct text
8. Integration with research_manager and risk_manager nodes
9. [C-001-R1] HOLD followed by 等待/观察 text does not bypass the no-position conversion
10. [C-001-R1] unified position context (explicit + inferred) drives the gate;
    missing data is never guessed as a holding
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.utils.position_validation_gate import (
    validate_position_actions,
    format_position_validation_warning,
    resolve_unified_has_position,
    _detect_violations,
    _NO_POSITION_FORBIDDEN_PATTERNS,
    _HAS_POSITION_FORBIDDEN_PATTERNS,
)


# ── validate_position_actions: core logic ─────────────────────────────────────


class TestNoPosition:
    """未持仓状态：禁止减仓/清仓/止损/止盈/卖出类动作。"""

    def test_no_position_with_reduce_keyword_detected(self):
        text = "建议减仓，当前风险较大。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False
        assert result["position_status"] == "no_position"
        assert len(result["violations"]) >= 1
        assert any(v["action"] == "减仓" for v in result["violations"])

    def test_no_position_with_exit_keyword_detected(self):
        text = "建议清仓离场，止损出局。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False
        assert result["position_status"] == "no_position"
        actions = [v["action"] for v in result["violations"]]
        assert "清仓" in actions or "止损" in actions

    def test_no_position_with_sell_english_detected(self):
        text = "Recommend SELL position."
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False
        assert any("SELL" in v["action"] or "EXIT" in v["action"] for v in result["violations"])

    def test_no_position_with_hold_detected(self):
        text = "HOLD position indefinitely."
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False
        assert any(v["action"] == "HOLD" for v in result["violations"])

    def test_no_position_with_allowed_actions_passes(self):
        text = "建议观望，等待入场条件成熟后 ENTER。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True
        assert result["position_status"] == "no_position"
        assert result["violations"] == []

    def test_no_position_allowed_actions_list(self):
        result = validate_position_actions("", {"current_position": 0})
        assert "WAIT" in result["allowed_actions"]
        assert "ENTER" in result["allowed_actions"]
        assert "HOLD" not in result["allowed_actions"]
        assert "REDUCE" not in result["allowed_actions"]
        assert "EXIT" not in result["allowed_actions"]

    def test_no_position_with_stop_loss_keyword(self):
        text = "触发止损离场。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False

    def test_no_position_with_trigger_stop_loss(self):
        text = "触发止损。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False

    def test_no_position_with_gradual_reduce(self):
        text = "逐步减仓以控制风险。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False


class TestHasPosition:
    """已持仓状态：禁止建仓/买入类动作。"""

    def test_has_position_with_buy_keyword_detected(self):
        text = "建议买入建仓。"
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is False
        assert result["position_status"] == "has_position"
        assert any("买入" in v["action"] or "建仓" in v["action"] for v in result["violations"])

    def test_has_position_with_enter_english_detected(self):
        text = "Recommend ENTER new position."
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is False
        assert any("ENTER" in v["action"] or "BUY" in v["action"] for v in result["violations"])

    def test_has_position_with_allowed_actions_passes(self):
        text = "建议持有，继续观察。条件成熟时可减仓。"
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is True
        assert result["position_status"] == "has_position"

    def test_has_position_allowed_actions_list(self):
        result = validate_position_actions("", {"current_position": 100})
        assert "HOLD" in result["allowed_actions"]
        assert "REDUCE" in result["allowed_actions"]
        assert "EXIT" in result["allowed_actions"]
        assert "WAIT" not in result["allowed_actions"]
        assert "ENTER" not in result["allowed_actions"]

    def test_has_position_with_reduce_passes(self):
        text = "建议减仓，止损价设在25元。"
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is True

    def test_has_position_with_exit_passes(self):
        text = "建议清仓离场。"
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is True

    def test_has_position_with_buy_english(self):
        text = "BUY signal confirmed."
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is False

    def test_has_position_with_active_build(self):
        text = "积极建仓，当前价位适合入场。"
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is False


class TestUnknownPosition:
    """持仓未知状态：不约束动作。"""

    def test_unknown_position_no_constraints(self):
        text = "建议减仓清仓，止损卖出。"
        result = validate_position_actions(text, None)
        assert result["passed"] is True
        assert result["position_status"] == "unknown"
        assert result["violations"] == []

    def test_unknown_position_empty_user_context(self):
        text = "建议买入建仓。"
        result = validate_position_actions(text, {})
        assert result["passed"] is True
        assert result["position_status"] == "unknown"

    def test_unknown_position_none_current_position(self):
        text = "建议清仓止损。"
        result = validate_position_actions(text, {"current_position": None})
        assert result["passed"] is True
        assert result["position_status"] == "unknown"

    def test_unknown_position_allowed_actions_empty(self):
        result = validate_position_actions("", None)
        assert result["allowed_actions"] == []
        assert result["forbidden_actions"] == []


# ── Field name safeguard ──────────────────────────────────────────────────────


class TestFieldNameSafeguard:
    """止损价/止损位/止损条件/止损线等字段名不应触发违规。"""

    def test_stop_loss_price_not_violation(self):
        text = "止损价设在25.00元，止损位在24.50元。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True

    def test_stop_loss_condition_not_violation(self):
        text = "止损条件：跌破24元。止损红线：23.50元。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True

    def test_stop_loss_line_not_violation(self):
        text = "止损线设在24.80元。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True

    def test_bare_stop_loss_is_violation(self):
        text = "建议止损。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False

    def test_stop_loss_exit_is_violation(self):
        text = "止损离场。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False


# ── System block exclusion ────────────────────────────────────────────────────


class TestSystemBlockExclusion:
    """系统追加区块中的关键词不应触发违规。"""

    def test_c001_marker_excluded(self):
        text = "建议观望。\n\n⚠️ [C-001] 未持仓状态，已将减仓/清仓建议自动转换为观望（WAIT）。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True

    def test_d002_marker_excluded(self):
        text = "建议持有。\n\n⚠️ [D-002] 强动作门禁未通过。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True

    def test_data_source_marker_excluded(self):
        text = "建议观望。\n\n📊 数据源可用性：正常。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True

    def test_report_quality_marker_excluded(self):
        text = "建议观望。\n\n--- 报告质量评分 ---\n得分：85"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True


# ── Multiple violations ───────────────────────────────────────────────────────


class TestMultipleViolations:
    """多个违规同时出现时全部检测。"""

    def test_multiple_no_position_violations(self):
        text = "建议减仓，同时止损清仓。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False
        assert len(result["violations"]) >= 2

    def test_multiple_has_position_violations(self):
        text = "建议买入建仓，可以考虑入场。"
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is False
        assert len(result["violations"]) >= 2


# ── format_position_validation_warning ────────────────────────────────────────


class TestFormatWarning:
    """格式化警告文本。"""

    def test_no_warning_when_passed(self):
        result = {"passed": True, "position_status": "no_position", "violations": []}
        assert format_position_validation_warning(result) == ""

    def test_no_position_warning_text(self):
        result = {
            "passed": False,
            "position_status": "no_position",
            "violations": [
                {"action": "减仓", "matched_text": "减仓", "position_status": "no_position", "offset": 0},
                {"action": "止损", "matched_text": "止损", "position_status": "no_position", "offset": 5},
            ],
        }
        warning = format_position_validation_warning(result)
        assert "[C-001]" in warning
        assert "未持仓" in warning
        assert "减仓" in warning
        assert "止损" in warning

    def test_has_position_warning_text(self):
        result = {
            "passed": False,
            "position_status": "has_position",
            "violations": [
                {"action": "买入", "matched_text": "买入", "position_status": "has_position", "offset": 0},
            ],
        }
        warning = format_position_validation_warning(result)
        assert "[C-001]" in warning
        assert "已持仓" in warning
        assert "买入" in warning

    def test_unknown_position_no_warning(self):
        result = {"passed": False, "position_status": "unknown", "violations": []}
        # unknown with violations shouldn't happen, but if it does, no specific message
        warning = format_position_validation_warning(result)
        assert warning == ""


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """边界条件。"""

    def test_empty_text(self):
        result = validate_position_actions("", {"current_position": 0})
        assert result["passed"] is True

    def test_none_text_handled(self):
        result = validate_position_actions(None, {"current_position": 0})
        assert result["passed"] is True

    def test_has_position_zero_shares_treated_as_no_position(self):
        result = validate_position_actions("", {"current_position": 0})
        assert result["position_status"] == "no_position"
        assert result["has_position"] is False

    def test_has_position_negative_shares_treated_as_no_position(self):
        result = validate_position_actions("", {"current_position": -10})
        assert result["position_status"] == "no_position"
        assert result["has_position"] is False

    def test_has_position_fractional_shares(self):
        result = validate_position_actions("建议持有", {"current_position": 0.5})
        assert result["position_status"] == "has_position"
        assert result["has_position"] is True
        assert result["passed"] is True

    def test_chinese_action_phrases(self):
        text = "可以止损，应该减仓，需要清仓。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False
        assert len(result["violations"]) >= 3

    def test_mixed_chinese_english(self):
        text = "建议 SELL 减仓。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False


# ── [C-001-R1] HOLD+等待 绕过封堵 ─────────────────────────────────────────────


class TestHoldWaitBypassBlocked:
    """正文出现"等待/观察/条件"不得让错误 HOLD 绕过未持仓转换。"""

    def test_no_position_hold_with_wait_text_flagged(self):
        text = "建议 HOLD，等待回调后再考虑。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False
        assert any(v["action"] == "HOLD" for v in result["violations"])

    def test_no_position_hold_with_observe_text_flagged(self):
        text = "HOLD 观察一段时间，等待更明确的信号。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is False
        assert any(v["action"] == "HOLD" for v in result["violations"])

    def test_no_position_hold_warning_mentions_wait_downgrade(self):
        text = "建议 HOLD，等待回调。"
        result = validate_position_actions(text, {"current_position": 0})
        warning = format_position_validation_warning(result)
        assert "WAIT" in warning
        assert "HOLD" in warning

    def test_no_position_non_hold_wait_text_still_passes(self):
        # 纯观望文本（无 HOLD）不是违规，等待语义合法存在于 WAIT
        text = "建议观望，等待入场条件成熟。"
        result = validate_position_actions(text, {"current_position": 0})
        assert result["passed"] is True

    def test_has_position_hold_with_wait_text_preserved(self):
        # 已持仓 HOLD 保留：HOLD 模式只存在于未持仓禁止列表
        text = "建议 HOLD，等待回调后再考虑。"
        result = validate_position_actions(text, {"current_position": 100})
        assert result["passed"] is True
        assert result["position_status"] == "has_position"


# ── [C-001-R1] 显式+推断统一持仓上下文 ────────────────────────────────────────


class TestUnifiedPositionContext:
    """门禁必须消费意图解析的统一持仓上下文；缺数据不得猜成有持仓。"""

    def test_explicit_flat_via_position_context_flags_hold(self):
        # 文本否定空仓但无数字字段：门禁仍须判 no_position
        result = validate_position_actions(
            "建议 HOLD，等待回调。",
            {},
            position_context={"has_position": False, "position_status_explicit": True},
        )
        assert result["position_status"] == "no_position"
        assert result["has_position"] is False
        assert result["passed"] is False

    def test_explicit_flat_via_position_context_flags_reduce(self):
        result = validate_position_actions(
            "建议减仓止损。",
            {},
            position_context={"has_position": False, "position_status_explicit": True},
        )
        assert result["passed"] is False

    def test_inferred_holding_flags_buy(self):
        result = validate_position_actions(
            "建议买入建仓。",
            {},
            position_context={"has_position": True, "position_status_explicit": True},
        )
        assert result["position_status"] == "has_position"
        assert result["passed"] is False

    def test_inferred_holding_allows_hold_and_reduce(self):
        held = {"has_position": True, "position_status_explicit": True}
        assert validate_position_actions("建议 HOLD。", {}, position_context=held)["passed"] is True
        assert validate_position_actions("建议减仓。", {}, position_context=held)["passed"] is True

    def test_unknown_position_with_legacy_default_not_guessed(self):
        # intent 解析无证据时返回 has_position=False + explicit=False 的
        # legacy 默认；门禁不得把它当成真实空仓去约束动作
        result = validate_position_actions(
            "建议买入建仓。",
            {},
            position_context={"has_position": False, "position_status_explicit": False},
        )
        assert result["position_status"] == "unknown"
        assert result["has_position"] is None
        assert result["passed"] is True

    def test_missing_data_not_guessed_as_holding(self):
        result = validate_position_actions(
            "建议 HOLD，等待回调。",
            None,
            position_context=None,
        )
        assert result["position_status"] == "unknown"
        assert result["has_position"] is None
        assert result["passed"] is True

    def test_legacy_position_context_falls_back_to_numeric(self):
        legacy = {"has_position": False, "position_status_explicit": False}
        assert (
            resolve_unified_has_position({"current_position": 100}, legacy) is True
        )
        assert (
            resolve_unified_has_position({"current_position": 0}, legacy) is False
        )

    def test_numeric_explicit_beats_missing_position_context(self):
        assert resolve_unified_has_position({"current_position": 0}, None) is False
        assert resolve_unified_has_position({"current_position_pct": 5}, {}) is True

    def test_unknown_position_uses_pct_fallback(self):
        result = validate_position_actions(
            "建议 HOLD。",
            {"current_position_pct": 0},
            position_context=None,
        )
        assert result["position_status"] == "no_position"


class TestIntakeContractParity:
    """入口结果一致：门禁解析与 api.main._resolve_has_position 契约对齐。"""

    CASES = [
        # (user_context, position_context)
        ({}, {"has_position": False, "position_status_explicit": True}),
        ({}, {"has_position": True, "position_status_explicit": True}),
        ({}, {"has_position": False, "position_status_explicit": False}),
        ({}, {"has_position": None, "position_status_explicit": False}),
        ({"current_position": 0}, None),
        ({"current_position": 100}, None),
        ({"current_position_pct": 5}, {}),
        # 冲突输入：统一上下文优先于数字字段（对齐入口契约第 1 优先级）
        ({"current_position": 100}, {"has_position": False, "position_status_explicit": True}),
        ({}, {}),
        (None, None),
    ]

    def test_gate_resolution_matches_intake(self):
        from api.main import _resolve_has_position

        for user_context, position_context in self.CASES:
            expected = _resolve_has_position(
                {"user_context": user_context, "position_context": position_context},
                None,
            )
            assert resolve_unified_has_position(user_context, position_context) == expected, (
                f"mismatch for user_context={user_context!r}, "
                f"position_context={position_context!r}"
            )


# ── Integration: research_manager / risk_manager ──────────────────────────────


class TestIntegrationWithManagers:
    """集成测试：research_manager 和 risk_manager 使用门禁。"""

    def test_research_manager_uses_gate(self):
        """验证 research_manager 导入并调用了 gate。"""
        from tradingagents.agents.managers import research_manager
        import inspect
        source = inspect.getsource(research_manager)
        assert "validate_position_actions" in source or "position_validation_gate" in source

    def test_risk_manager_uses_gate(self):
        """验证 risk_manager 导入并调用了 gate。"""
        from tradingagents.agents.managers import risk_manager
        import inspect
        source = inspect.getsource(risk_manager)
        assert "validate_position_actions" in source or "position_validation_gate" in source

    def test_signal_processing_still_detects_c001(self):
        """验证 signal_processing 的 _has_gate_failure 仍能识别 [C-001] 标记。"""
        from tradingagents.graph.signal_processing import _has_gate_failure
        text = "建议观望。\n\n⚠️ [C-001] 未持仓状态，以下持仓动作不适用：减仓。已自动降级为观察（WAIT）。"
        assert _has_gate_failure(text) is True

    def test_managers_pass_position_context_to_gate(self):
        """[C-001-R1] 两个调用点必须传入统一持仓上下文。"""
        from tradingagents.agents.managers import research_manager, risk_manager
        import inspect

        research_src = inspect.getsource(research_manager)
        risk_src = inspect.getsource(risk_manager)
        assert 'position_context=state.get("position_context")' in research_src
        assert 'position_context=state.get("position_context")' in risk_src
