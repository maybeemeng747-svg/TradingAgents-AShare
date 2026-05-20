"""G-001: 三层决策体系 — P6 六个必测场景。

1. 未持仓 entry
2. 已持仓 holding
3. 中线好短线弱（冲突处理）
4. 持仓中线好短线破位（冲突处理）
5. 数据完整度 < 70%（动作限制）
6. 短中线冲突综合
"""
from unittest.mock import MagicMock

from tradingagents.graph.intent_parser import (
    parse_intent,
    _infer_analysis_intent,
    _infer_position_context,
)
from tradingagents.agents.utils.readiness_score import (
    get_allowed_actions,
    resolve_horizon_conflict,
    apply_horizon_conflict_to_actions,
    format_execution_block,
)


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 1: 未持仓 entry — 用户问"短线买点"→ entry + short
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario1NoPositionEntry:
    """未持仓用户问入场 → entry + short，禁止 HOLD/REDUCE/EXIT。"""

    def test_intent_entry_short(self):
        intent, horizon = _infer_analysis_intent("600519短线买点在哪里")
        assert intent == "entry"
        assert horizon == "short"

    def test_position_context_no_position(self):
        ctx = _infer_position_context("600519短线买点在哪里", {})
        assert ctx["has_position"] is False

    def test_actions_no_position_forbid_hold_reduce_exit(self):
        """未持仓时动作映射必须禁止 HOLD/REDUCE/EXIT。"""
        result = get_allowed_actions(data_completeness=80, position_status="no_position")
        assert "HOLD" in result["forbidden"]
        assert "REDUCE" in result["forbidden"]
        assert "EXIT" in result["forbidden"]
        assert "减仓" in result["forbidden"]
        assert "止损" in result["forbidden"]
        assert "清仓" in result["forbidden"]
        # 允许观察和条件入场
        assert "观察" in result["allowed"]

    def test_parse_intent_full_flow(self):
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content='{"ticker":"600519","horizons":["short"],"focus_areas":[],"specific_questions":[]}'
        )
        result = parse_intent("600519短线买点在哪里", mock_llm)
        assert result["analysis_intent"] == "entry"
        assert result["horizons"] == ["short"]
        assert result["position_context"]["has_position"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 2: 已持仓 holding — 用户问"继续拿"→ holding + short
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario2HasPositionHolding:
    """已持仓用户问继续拿 → holding + short，允许持有/减仓/止损。"""

    def test_intent_holding_short(self):
        intent, horizon = _infer_analysis_intent("继续拿着600519")
        assert intent == "holding"
        assert horizon == "short"

    def test_position_context_has_position(self):
        ctx = _infer_position_context("继续拿着600519", {"current_position": 1000})
        assert ctx["has_position"] is True
        assert ctx["shares"] == 1000

    def test_actions_has_position_allow_all_levels(self):
        """已持仓时允许 Level 0-4（持有/止损/减仓/加仓）。"""
        result = get_allowed_actions(data_completeness=80, position_status="has_position")
        # 允许持有和止损
        assert "持有" in result["allowed"]
        assert "止损触发" in result["allowed"]
        # 禁止追涨
        assert "追涨" in result["forbidden"]

    def test_parse_intent_auto_upgrade_watch_to_holding(self):
        """通用查询 + 有持仓 → watch 自动升级为 holding。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content='{"ticker":"600519","horizons":["short"],"focus_areas":[],"specific_questions":[],"user_context":{"current_position":500}}'
        )
        result = parse_intent("分析600519", mock_llm)
        assert result["analysis_intent"] == "holding"
        assert result["position_context"]["has_position"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 3: 中线好短线弱（冲突处理）
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario3MediumBullShortWeak:
    """中线偏多+短线偏弱 → 不追买不清仓，等待短线修复。"""

    def test_conflict_resolution_no_position(self):
        result = resolve_horizon_conflict(
            medium_bullish=True, short_bullish=False, has_position=False
        )
        assert result["conflict_type"] == "medium_bull_short_weak"
        assert result["action"] == "继续观察"
        assert "entry" not in result["allowed_intents"]
        assert "watch" in result["allowed_intents"]
        assert "等待短线修复" in result["note"]

    def test_conflict_resolution_has_position(self):
        result = resolve_horizon_conflict(
            medium_bullish=True, short_bullish=False, has_position=True
        )
        assert result["conflict_type"] == "medium_bull_short_weak"
        assert result["action"] == "持有等待"
        assert "holding" in result["allowed_intents"]
        assert "entry" not in result["allowed_intents"]
        assert "不追买不清仓" in result["note"]

    def test_conflict_restricts_actions(self):
        """冲突结果应进一步限制允许动作。"""
        conflict = resolve_horizon_conflict(
            medium_bullish=True, short_bullish=False, has_position=False
        )
        base = get_allowed_actions(data_completeness=80, position_status="no_position")
        restricted = apply_horizon_conflict_to_actions(conflict, base)
        # 不允许买入/建仓
        assert "确认建仓" not in restricted.get("allowed", [])


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 4: 持仓中线好短线破位（冲突处理）
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario4PositionMediumBullShortBreak:
    """持仓 + 中线好 + 短线破位 → 不追买不清仓，等待短线修复。"""

    def test_intent_is_holding(self):
        intent, _ = _infer_analysis_intent("600519要不要止损")
        assert intent == "stop_loss"

    def test_conflict_has_position_medium_bull_short_weak(self):
        result = resolve_horizon_conflict(
            medium_bullish=True, short_bullish=False, has_position=True
        )
        assert result["action"] == "持有等待"
        assert "holding" in result["allowed_intents"]
        # 允许观察但不鼓励止损
        assert "stop_loss" not in result["allowed_intents"]

    def test_report_shows_three_layers(self):
        """报告输出包含三层决策区。"""
        block = format_execution_block(
            source_coverage=80,
            evidence_coverage=75,
            confidence="中",
            opportunity_score=40,
            risk_level=2,
            buy_level=0,
            risk_level_note="",
            buy_level_note="",
            strong_action_gate={"passed": False, "failures": ["short_bearish"]},
            position_status="has_position",
            analysis_intent="holding",
            position_context={"has_position": True, "avg_cost": 50.5, "shares": 1000},
        )
        assert "三层决策" in block
        assert "中线逻辑层" in block
        assert "短线时点层" in block
        assert "持仓执行层" in block
        assert "持仓处理" in block
        assert "已持仓" in block


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 5: 数据完整度 < 70%（动作限制）
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario5LowDataCompleteness:
    """数据完整度 < 70% → 限制动作，禁止强动作。"""

    def test_no_position_low_data_only_observe(self):
        """未持仓 + 数据 < 50%：只允许观察。"""
        result = get_allowed_actions(data_completeness=40, position_status="no_position")
        assert result["allowed"] == ["观察"]
        assert "买入" in result["forbidden"]

    def test_no_position_medium_data(self):
        """未持仓 + 数据 50-74%：允许观察和条件试仓。"""
        result = get_allowed_actions(data_completeness=60, position_status="no_position")
        assert "观察" in result["allowed"]
        assert "条件试仓" in result["allowed"]
        # 仍然禁止买入
        assert "买入" in result["forbidden"]

    def test_has_position_low_data_no_add(self):
        """已持仓 + 数据 < 50%：禁止买入/加仓/追涨。"""
        result = get_allowed_actions(data_completeness=40, position_status="has_position")
        assert "买入" in result["forbidden"]
        assert "加仓" in result["forbidden"]
        assert "追涨" in result["forbidden"]
        # 允许持有和止损触发
        assert "持有" in result["allowed"]
        assert "止损触发" in result["allowed"]

    def test_has_position_medium_data_no_add(self):
        """已持仓 + 数据 50-74%：禁止加仓。"""
        result = get_allowed_actions(data_completeness=65, position_status="has_position")
        assert "加仓" in result["forbidden"]

    def test_has_position_high_data_allow_add(self):
        """已持仓 + 数据 >= 75%：允许加仓。"""
        result = get_allowed_actions(data_completeness=80, position_status="has_position")
        assert "加仓" in result["allowed"]

    def test_report_shows_low_data_warning(self):
        """报告输出标注数据不足警告。"""
        block = format_execution_block(
            source_coverage=45,
            evidence_coverage=50,
            confidence="低",
            opportunity_score=20,
            risk_level=0,
            buy_level=0,
            strong_action_gate={"passed": False, "failures": ["source_coverage=45% < 70%"]},
            position_status="no_position",
            analysis_intent="entry",
            position_context={"has_position": False},
        )
        assert "数据完整度不足" in block


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 6: 短中线冲突综合
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario6HorizonConflictComprehensive:
    """四种冲突组合全面测试。"""

    def test_both_bullish_no_conflict(self):
        """中线偏多+短线偏强 → 无冲突，可买入。"""
        result = resolve_horizon_conflict(
            medium_bullish=True, short_bullish=True, has_position=False
        )
        assert result["conflict_type"] == "none"
        assert "entry" in result["allowed_intents"]
        assert "add" in result["allowed_intents"]

    def test_both_bullish_has_position_can_add(self):
        """中线偏多+短线偏强+已持仓 → 可加仓。"""
        result = resolve_horizon_conflict(
            medium_bullish=True, short_bullish=True, has_position=True
        )
        assert result["conflict_type"] == "none"
        assert "add" in result["allowed_intents"]

    def test_medium_bear_short_strong_no_position(self):
        """中线偏空+短线偏强+未持仓 → 只允许短打观察，禁止入场。"""
        result = resolve_horizon_conflict(
            medium_bullish=False, short_bullish=True, has_position=False
        )
        assert result["conflict_type"] == "medium_bear_short_strong"
        assert "entry" not in result["allowed_intents"]
        assert result["allowed_intents"] == ["watch"]

    def test_medium_bear_short_strong_has_position(self):
        """中线偏空+短线偏强+已持仓 → 短打观察，禁止加仓。"""
        result = resolve_horizon_conflict(
            medium_bullish=False, short_bullish=True, has_position=True
        )
        assert result["conflict_type"] == "medium_bear_short_strong"
        assert "add" not in result["allowed_intents"]
        assert "entry" not in result["allowed_intents"]
        assert "holding" in result["allowed_intents"]

    def test_both_bearish_no_position(self):
        """中线偏空+短线偏弱+未持仓 → 继续观察。"""
        result = resolve_horizon_conflict(
            medium_bullish=False, short_bullish=False, has_position=False
        )
        assert result["conflict_type"] == "both_bearish"
        assert result["action"] == "继续观察"
        assert "禁止入场" in result["note"]

    def test_both_bearish_has_position(self):
        """中线偏空+短线偏弱+已持仓 → 减仓/止损观察。"""
        result = resolve_horizon_conflict(
            medium_bullish=False, short_bullish=False, has_position=True
        )
        assert result["conflict_type"] == "both_bearish"
        assert "reduce" in result["allowed_intents"]
        assert "stop_loss" in result["allowed_intents"]
        assert "entry" not in result["allowed_intents"]

    def test_apply_conflict_restrictions_combined(self):
        """冲突限制 + 动作映射联合验证。"""
        # 中线偏空+短线偏强+未持仓
        conflict = resolve_horizon_conflict(
            medium_bullish=False, short_bullish=True, has_position=False
        )
        base = get_allowed_actions(data_completeness=80, position_status="no_position")
        restricted = apply_horizon_conflict_to_actions(conflict, base)
        # 确认入场/建仓被限制
        assert "确认建仓" not in restricted.get("allowed", [])
        assert "短打观察" in conflict["action"]
        assert "conflict" in restricted.get("message", "").lower() or "冲突" in restricted.get("message", "")

    def test_three_layer_report_with_conflict(self):
        """冲突场景下报告仍输出三层决策区。"""
        block = format_execution_block(
            source_coverage=75,
            evidence_coverage=70,
            confidence="中",
            opportunity_score=35,
            risk_level=1,
            buy_level=1,
            risk_level_note="",
            buy_level_note="",
            strong_action_gate={"passed": True, "failures": []},
            position_status="has_position",
            analysis_intent="holding",
            position_context={"has_position": True, "avg_cost": 30.0},
        )
        assert "三层决策" in block
        assert "中线逻辑层" in block
        assert "短线时点层" in block
        assert "持仓执行层" in block
        assert "已持仓" in block
        assert "持仓处理" in block
        assert "持仓成本: 30.0" in block


# ══════════════════════════════════════════════════════════════════════════════
# G-001 Production Wiring — 补充测试
# ══════════════════════════════════════════════════════════════════════════════

class TestG001ProductionWiring:
    """G-001 生产链路闭环的补充验证。"""

    def test_medium_horizon_shows_zhongxian(self):
        """medium query 最终报告显示「周期：中线」，不显示「周期：短线」。"""
        block = format_execution_block(
            source_coverage=75,
            evidence_coverage=70,
            confidence="中",
            opportunity_score=50,
            risk_level=1,
            buy_level=1,
            position_status="no_position",
            analysis_intent="watch",
            position_context={"has_position": False},
            horizon="medium",
        )
        assert "周期：中线" in block
        assert "周期：短线" not in block

    def test_holding_intent_shows_chicang_chuli(self):
        """holding query 执行区显示「持仓处理」。"""
        block = format_execution_block(
            source_coverage=80,
            evidence_coverage=75,
            confidence="中",
            opportunity_score=40,
            risk_level=2,
            buy_level=0,
            position_status="has_position",
            analysis_intent="holding",
            position_context={"has_position": True},
            horizon="short",
        )
        assert "持仓处理" in block

    def test_position_context_avg_cost_shown(self):
        """position_context 带 avg_cost 时最终报告显示成本。"""
        block = format_execution_block(
            source_coverage=80,
            evidence_coverage=75,
            confidence="中",
            opportunity_score=40,
            risk_level=2,
            buy_level=0,
            position_status="has_position",
            analysis_intent="holding",
            position_context={"has_position": True, "avg_cost": 25.8},
            horizon="short",
        )
        assert "持仓成本: 25.8" in block

    def test_default_horizon_is_short(self):
        """默认 horizon=short 时显示「周期：短线」。"""
        block = format_execution_block(
            source_coverage=80,
            evidence_coverage=75,
            confidence="中",
            opportunity_score=50,
            risk_level=1,
            buy_level=1,
            position_status="no_position",
        )
        assert "周期：短线" in block

    def test_dual_horizon_conflict_in_block(self):
        """传入 short_bullish + medium_bullish 时冲突裁决接入报告。"""
        block = format_execution_block(
            source_coverage=80,
            evidence_coverage=75,
            confidence="中",
            opportunity_score=50,
            risk_level=1,
            buy_level=1,
            position_status="no_position",
            analysis_intent="watch",
            position_context={"has_position": False},
            horizon="short",
            short_bullish=False,
            medium_bullish=True,
        )
        assert "短中线冲突" in block
        assert "中线偏多但短线偏弱" in block

    def test_single_horizon_no_conflict_output(self):
        """单周期不传冲突信号时不输出冲突裁决。"""
        block = format_execution_block(
            source_coverage=80,
            evidence_coverage=75,
            confidence="中",
            opportunity_score=50,
            risk_level=1,
            buy_level=1,
            position_status="no_position",
            analysis_intent="watch",
            position_context={"has_position": False},
            horizon="short",
        )
        assert "短中线冲突" not in block
