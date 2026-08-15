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


# ══════════════════════════════════════════════════════════════════════════════
# P0-1: 持仓状态统一 — 否定词 + current_position=0 清空持仓数据
# ══════════════════════════════════════════════════════════════════════════════


class TestP01PositionStateUnification:
    """P0-1: 否定词或 current_position=0 必须完全清空持仓数据。"""

    def test_negation_keyword_clears_all(self):
        """否定词(未持仓)时 shares/avg_cost/position_pct 全部为 None。"""
        ctx = _infer_position_context("未持仓，想看看", {
            "current_position": 100,
            "average_cost": 10,
            "current_position_pct": 20,
        })
        assert ctx["has_position"] is False
        assert ctx["shares"] is None
        assert ctx["avg_cost"] is None
        assert ctx["position_pct"] is None

    def test_empty_position_keyword_clears_all(self):
        """空仓 关键词清空所有持仓数据。"""
        ctx = _infer_position_context("空仓状态", {
            "current_position": 500,
            "average_cost": 25.0,
            "current_position_pct": 10,
        })
        assert ctx["has_position"] is False
        assert ctx["shares"] is None
        assert ctx["avg_cost"] is None
        assert ctx["position_pct"] is None

    def test_current_position_zero_is_explicit_no_position(self):
        """current_position=0 + avg_cost=10 + position_pct=20 → 完全清空。"""
        ctx = _infer_position_context("看看002353", {
            "current_position": 0,
            "average_cost": 10,
            "current_position_pct": 20,
        })
        assert ctx["has_position"] is False
        assert ctx["shares"] is None
        assert ctx["avg_cost"] is None
        assert ctx["position_pct"] is None

    def test_current_position_zero_prevents_position_pct_override(self):
        """current_position=0 阻止 position_pct 把状态污染为持仓。"""
        ctx = _infer_position_context("分析一下", {
            "current_position": 0,
            "current_position_pct": 30,
        })
        assert ctx["has_position"] is False
        assert ctx["position_pct"] is None

    def test_no_negation_no_zero_position_normal(self):
        """正常持仓数据不受影响。"""
        ctx = _infer_position_context("继续拿着", {
            "current_position": 1000,
            "average_cost": 15.5,
            "current_position_pct": 10,
        })
        assert ctx["has_position"] is True
        assert ctx["shares"] == 1000
        assert ctx["avg_cost"] == 15.5
        assert ctx["position_pct"] == 10

    def test_negation_keyword_variants(self):
        """各种否定词变体都正确清空。"""
        for keyword in ["未持仓", "空仓", "还没买", "没有持仓", "无持仓", "不持有", "未持有"]:
            ctx = _infer_position_context(f"{keyword}，想看看", {
                "current_position": 200,
                "average_cost": 8.0,
            })
            assert ctx["has_position"] is False, f"keyword={keyword} should be no position"
            assert ctx["shares"] is None, f"keyword={keyword} shares should be None"
            assert ctx["avg_cost"] is None, f"keyword={keyword} avg_cost should be None"


# ══════════════════════════════════════════════════════════════════════════════
# P0-2: sanitizer 修复 — 短句替换 + 保留字段名
# ══════════════════════════════════════════════════════════════════════════════


class TestP02SanitizerFix:
    """P0-2: sanitizer 使用短句替换，保留止损价/止损位等字段名。"""

    def test_sanitize_uses_short_replacement(self):
        """替换文本是短句而不是长文本。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions
        text = "建议止损离场，风险较大"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=1,
            risk_level=1,
        )
        # 不应包含长文本
        assert "未持仓-相关持仓动作不适用" not in result
        # 应包含短句
        assert "该持仓动作不适用，保持观察" in result

    def test_sanitize_stop_loss_price_preserved(self):
        """止损价/止损位/止损条件/止损红线/止损线 不被替换。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions
        text = "当前止损价为15元，止损位在14.5元，止损条件为跌破14元，止损红线13元，止损线12.5元"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=1,
            risk_level=1,
        )
        assert "止损价" in result
        assert "止损位" in result
        assert "止损条件" in result
        assert "止损红线" in result
        assert "止损线" in result

    def test_sanitize_zh_sun_li_chang(self):
        """止损离场 被完整替换。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions
        text = "建议止损离场"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=1,
            risk_level=1,
        )
        assert "止损离场" not in result
        assert "该持仓动作不适用，保持观察" in result

    def test_sanitize_trigger_stop_loss(self):
        """触发止损 被替换。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions
        text = "如果跌破15元则触发止损"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=1,
            risk_level=1,
        )
        assert "触发止损" not in result

    def test_sanitize_reduce_observe(self):
        """减仓观察 被替换。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions
        text = "减仓观察为主"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=1,
            risk_level=1,
        )
        assert "减仓观察" not in result

    def test_sanitize_no_half_pollution(self):
        """不允许留下半截污染如 'xxx离场' 'xxx价'。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions
        text = "建议立即清仓离场出局"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=1,
            risk_level=1,
        )
        # 不应残留半截
        assert "清仓" not in result or "该持仓动作不适用" in result

    def test_has_position_low_risk_level_downgrades_exit(self):
        """有持仓也不能绕过 Risk Level 对退出动作的授权。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions
        text = "建议止损离场"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="has_position",
            buy_level=1,
            risk_level=1,
        )
        assert "建议止损离场" not in result
        assert "等待触发条件，暂不执行强清仓" in result
        assert changes

    def test_no_position_preserves_explicit_future_holding_scenarios(self):
        """右侧/左侧持仓预案不应被改写成半截污染文本。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        text = (
            "- 右侧持仓：若收盘跌破149.50，止损离场。\n"
            "- 左侧持仓：若跌破144.25减半仓；若跌破143.00止损。\n"
            "- 任何持仓若出现新增减持，立即降险或离场。"
        )
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert result == text
        assert changes == []
        assert "该持仓动作不适用" not in result

    def test_no_position_sanitizes_current_action_after_future_holding_plan(self):
        """未来持仓预案不能掩盖同一行后续的当前清仓指令。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        for text, invalid_action in (
            ("- 右侧持仓：若收盘跌破149.50止损离场；当前操作：清仓。", "当前操作：清仓"),
            ("- 右侧持仓：若跌破149.50止损，结论：清仓。", "结论：清仓"),
            ("- 右侧持仓：若跌破149.50止损，下一步：卖出。", "下一步：卖出"),
            ("- 左侧持仓：若跌破144.25减半仓，执行方案：止损。", "执行方案：止损"),
            ("- 右侧持仓：若跌破149.50止损，但建议立即清仓。", "建议立即清仓"),
            ("- 右侧持仓：若跌破149.50止损，不过应该卖出。", "应该卖出"),
            ("- 左侧持仓：若跌破144.25减半仓，同时立刻清仓。", "立刻清仓"),
            ("- 右侧持仓：若跌破149.50止损，建议清仓。", "建议清仓"),
        ):
            result, changes = sanitize_forbidden_strong_actions(
                text,
                gate={"passed": True, "failures": []},
                position_status="no_position",
                buy_level=0,
                risk_level=1,
            )

            assert "跌破" in result
            assert invalid_action not in result
            assert "该持仓动作不适用，保持观察" in result
            assert changes

    def test_no_position_preserves_conditional_continuation_but_sanitizes_bare_exit(self):
        """分号后的条件预案保留，裸清仓指令仍须降级。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        text = "- 左侧持仓：若跌破144.25减半仓；若跌破143.00止损；建议清仓。"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert "左侧持仓：若跌破144.25减半仓；若跌破143.00止损" in result
        assert "建议清仓" not in result
        assert "该持仓动作不适用，保持观察" in result
        assert changes

    def test_no_position_does_not_protect_unconditional_or_current_position_exit(self):
        """只有带真实触发条件的未来预案可以绕过未持仓清洗。"""
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        for text in (
            "- 右侧持仓：立即清仓。",
            "- 左侧持仓：当前持有1000股，建议清仓。",
            "> 右侧持仓：执行清仓。",
            "- 右侧持仓：股价偏高，立即清仓。",
            "- 右侧持仓：目前股价已跌破149.50，建议清仓。",
            "- 右侧持仓：股价跌破149.50，建议清仓。",
            "- 右侧持仓：收盘跌破149.50，建议清仓。",
            "- 左侧持仓：收盘价为149.50，建议卖出。",
            "- 右侧持仓：立即清仓，若后续跌破149.50则继续观察。",
            "- 右侧持仓：建议清仓，后续若反弹再评估。",
            "- 左侧持仓：卖出，之后跌破143.00止损。",
            "- 右侧持仓：先清仓，若跌破149.50止损。",
            "- 右侧持仓：若反弹则观察但立即清仓。",
            "- 右侧持仓：若跌破149则观察但建议立即清仓。",
            "- 右侧持仓：若跌破149则观察同时立即清仓。",
            "- 右侧持仓：若反弹则观望，立即清仓。",
            "- 右侧持仓：若反弹则观察，应该卖出。",
            "- 右侧持仓：若反弹则加仓，立即清仓。",
            "- 右侧持仓：若反弹则持有，立即清仓。",
            "- 右侧持仓：若反弹则买入，立即清仓。",
            "- 右侧持仓：若反弹则增持，建议清仓。",
            "- 右侧持仓：若跌破149，风险上升，立即清仓。",
            "- 右侧持仓：若反弹则暂不入场，立即清仓。",
            "- 右侧持仓：如果消息转好则继续研究，建议清仓。",
            "- 右侧持仓：若跌破149则发出预警，当前清仓。",
            "- 右侧持仓：若跌破149，用户当前清仓。",
            "- 右侧持仓：若跌破149，最终清仓。",
            "- 右侧持仓：若跌破149，明日卖出。",
            "- 右侧持仓：若跌破149，转为减仓。",
            "- 右侧持仓：若跌破149，只止盈。",
        ):
            result, changes = sanitize_forbidden_strong_actions(
                text,
                gate={"passed": True, "failures": []},
                position_status="no_position",
                buy_level=0,
                risk_level=1,
            )
            assert text.rstrip("。") not in result
            assert "该持仓动作不适用，保持观察" in result
            assert changes

    def test_no_position_preserves_markdown_conditional_future_plan(self):
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        text = "- **右侧持仓**：若收盘跌破149.50，止损离场。"
        result, changes = sanitize_forbidden_strong_actions(
            text,
            gate={"passed": True, "failures": []},
            position_status="no_position",
            buy_level=0,
            risk_level=1,
        )

        assert result == text
        assert changes == []

    def test_no_position_preserves_explicit_future_position_plan(self):
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        for text in (
            "- 右侧持仓：未来持仓后计划减仓。",
            "- 左侧持仓：买入后建议卖出。",
            "- 右侧持仓：若未来建仓后考虑止盈。",
            "- 右侧持仓：触发止损线则清仓。",
            "- 右侧持仓：跌破149后减仓并止损离场。",
            "- 右侧持仓：若跌破149，建议减仓或清仓。",
        ):
            result, changes = sanitize_forbidden_strong_actions(
                text,
                gate={"passed": True, "failures": []},
                position_status="no_position",
                buy_level=0,
                risk_level=1,
            )

            assert result == text
            assert changes == []

    def test_no_position_preserves_multistage_future_position_plan(self):
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        for text in (
            "- 右侧持仓：若跌破149，先减仓，若继续跌破148则清仓。",
            "- 左侧持仓：若跌破144.25，减半仓，若跌破143.00，止损离场。",
            "- 右侧持仓：若跌破149，建议减仓，并在跌破148后清仓。",
            "- 右侧持仓：若跌破149，先减仓，随后清仓。",
            "- 左侧持仓：若跌破144.25，先减半仓，继而止损离场。",
            "- 右侧持仓：若跌破149，先减仓，接着清仓。",
            "- 右侧持仓：若跌破149，先减仓，届时清仓。",
            "- 左侧持仓：若跌破144.25，先减半仓，之后止损离场。",
            "- 右侧持仓：若跌破149，先减仓，必要时清仓。",
            "- 右侧持仓：若跌破149，先减仓，若持续走弱则清仓。",
            "- 右侧持仓：若基本面恶化则立即清仓。",
            "- 左侧持仓：若公告出现重大利空，建议减仓。",
            "- 右侧持仓：若趋势转弱则止损离场。",
        ):
            result, changes = sanitize_forbidden_strong_actions(
                text,
                gate={"passed": True, "failures": []},
                position_status="no_position",
                buy_level=0,
                risk_level=1,
            )

            assert result == text
            assert changes == []

    def test_no_position_does_not_protect_already_triggered_holding_condition(self):
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        for text in (
            "- 右侧持仓：若目前股价已跌破149.50，建议清仓。",
            "- 右侧持仓：如果当前已经失守支撑位，应立即止损离场。",
            "- 左侧持仓：假如今日收盘已跌破144.25，立即减仓。",
            "- 任何持仓若目前已跌破止损线，立即清仓。",
            "- 任何持仓若当前风险过高，立即降险或离场。",
            "- 右侧持仓：昨日跌破149后立即清仓。",
            "- 左侧持仓：刚跌破144.25后立即减仓。",
        ):
            result, changes = sanitize_forbidden_strong_actions(
                text,
                gate={"passed": True, "failures": []},
                position_status="no_position",
                buy_level=0,
                risk_level=1,
            )

            assert text.rstrip("。") not in result
            assert "该持仓动作不适用，保持观察" in result
            assert changes

    def test_no_position_preserves_analytical_exit_terminology(self):
        from tradingagents.agents.utils.readiness_score import sanitize_forbidden_strong_actions

        for text in (
            "历史回测显示止损离场机制有效。",
            "止损离场风险仍需评估。",
            "该模型的止盈退出信号尚未出现。",
            "止损并离场的历史概率为20%。",
        ):
            result, changes = sanitize_forbidden_strong_actions(
                text,
                gate={"passed": True, "failures": []},
                position_status="no_position",
                buy_level=0,
                risk_level=1,
            )

            assert result == text
            assert changes == []
