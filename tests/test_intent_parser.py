from unittest.mock import MagicMock
from tradingagents.graph.intent_parser import (
    parse_intent, build_horizon_context,
    _infer_analysis_intent, _infer_position_context,
)


def test_parse_intent_returns_defaults():
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(
        content='{"ticker": "600519", "horizons": ["short", "medium"], "focus_areas": [], "specific_questions": []}'
    )
    result = parse_intent("分析600519", mock_llm)
    assert result["ticker"] == "600519"
    assert result["horizons"] == ["short"]
    assert result["focus_areas"] == []
    assert "analysis_intent" in result
    assert "position_context" in result


def test_parse_intent_fallback_on_invalid_json():
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="这不是JSON")
    result = parse_intent("600519", mock_llm, fallback_ticker="600519")
    assert result["ticker"] == "600519"
    assert result["horizons"] == ["short"]
    assert result["focus_areas"] == []
    assert "analysis_intent" in result
    assert "position_context" in result


def test_build_horizon_context_short_contains_label():
    ctx = build_horizon_context("short", ["量价关系"], ["能否突破"])
    assert "短线" in ctx
    assert "量价关系" in ctx
    assert "能否突破" in ctx


def test_build_horizon_context_medium_has_label():
    ctx = build_horizon_context("medium", [], [], agent_type="fundamentals")
    assert "中线" in ctx


def test_build_horizon_context_short_fundamentals_has_downweight_hint():
    ctx = build_horizon_context("short", [], [], agent_type="fundamentals")
    assert "次要" in ctx


# ── G-001 P0: analysis_intent 推断测试 ──

class TestInferAnalysisIntent:
    def test_entry_intent(self):
        intent, horizon = _infer_analysis_intent("600519短线买点在哪里")
        assert intent == "entry"
        assert horizon == "short"

    def test_entry_intent_generic(self):
        intent, horizon = _infer_analysis_intent("能不能买入600519")
        assert intent == "entry"

    def test_holding_intent(self):
        intent, horizon = _infer_analysis_intent("继续拿着600519")
        assert intent == "holding"
        assert horizon == "short"

    def test_add_intent(self):
        intent, horizon = _infer_analysis_intent("想加仓600519")
        assert intent == "add"

    def test_reduce_intent(self):
        intent, horizon = _infer_analysis_intent("考虑减仓600519")
        assert intent == "reduce"

    def test_stop_loss_intent(self):
        intent, horizon = _infer_analysis_intent("600519要不要止损")
        assert intent == "stop_loss"

    def test_watch_intent(self):
        intent, horizon = _infer_analysis_intent("先观察一下600519")
        assert intent == "watch"

    def test_default_watch(self):
        intent, horizon = _infer_analysis_intent("600519怎么样")
        assert intent == "watch"
        assert horizon == "short"

    def test_medium_holding_intent(self):
        intent, horizon = _infer_analysis_intent("中线拿不拿600519")
        assert intent == "holding"
        assert horizon == "medium"

    def test_medium_entry_intent(self):
        intent, horizon = _infer_analysis_intent("想中线建仓600519")
        assert intent == "entry"
        assert horizon == "medium"


class TestInferPositionContext:
    def test_no_position(self):
        ctx = _infer_position_context("分析600519", {})
        assert ctx["has_position"] is False

    def test_has_position_from_user_context(self):
        ctx = _infer_position_context("600519", {"current_position": 1000})
        assert ctx["has_position"] is True
        assert ctx["shares"] == 1000

    def test_position_from_pct(self):
        ctx = _infer_position_context("600519", {"current_position_pct": 30})
        assert ctx["has_position"] is True
        assert ctx["position_pct"] == 30

    def test_inferred_from_keywords(self):
        ctx = _infer_position_context("600519加仓", {})
        assert ctx["has_position"] is True

    def test_avg_cost(self):
        ctx = _infer_position_context("600519", {"average_cost": 50.5})
        assert ctx["avg_cost"] == 50.5


def test_parse_intent_auto_upgrade_watch_to_holding():
    """未持仓 query 但 user_context 有持仓 → watch 升级为 holding"""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(
        content='{"ticker": "600519", "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {"current_position": 500}}'
    )
    result = parse_intent("分析600519", mock_llm)
    assert result["analysis_intent"] == "holding"  # auto-upgraded from watch
    assert result["position_context"]["has_position"] is True


def test_build_horizon_context_includes_intent_and_position():
    """G-001: build_horizon_context 输出交易目的和持仓状态"""
    ctx = build_horizon_context(
        "short", [], [],
        analysis_intent="entry",
        position_context={"has_position": False},
    )
    assert "寻找入场机会" in ctx
    assert "未持仓" in ctx


def test_build_horizon_context_shows_position_details():
    ctx = build_horizon_context(
        "short", [], [],
        analysis_intent="holding",
        position_context={"has_position": True, "avg_cost": 50.5, "shares": 1000, "position_pct": 30},
    )
    assert "已持仓" in ctx
    assert "成本 50.5" in ctx
    assert "持仓 1000" in ctx
