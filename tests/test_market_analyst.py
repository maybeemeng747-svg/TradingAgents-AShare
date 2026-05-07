import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.graph.data_collector import DataCollector


def _make_state(horizon="short"):
    return {
        "trade_date": "2026-03-12",
        "company_of_interest": "600519",
        "horizon": horizon,
        "user_intent": {
            "raw_query": "test", "ticker": "600519",
            "horizons": ["short", "medium"], "focus_areas": [], "specific_questions": [],
        },
    }


def _stub_pool(horizon="short"):
    days = "14天" if horizon == "short" else "90天"
    return {
        "stock_data": "close,volume\n100,1000",
        "indicators": {k: "50" for k in [
            "close_50_sma","close_200_sma","close_10_ema",
            "rsi","macd","boll","boll_ub","boll_lb","atr","vwma"
        ]},
        "_data_window": days, "_horizon": horizon,
    }


def test_market_analyst_returns_trace():
    mock_llm = MagicMock()

    async def _astream(_messages):
        yield SimpleNamespace(
            content='报告\n<!-- VERDICT: {"direction": "看多", "reason": "趋势向上"} -->'
        )

    mock_llm.astream = _astream
    collector = DataCollector()
    collector._cache["600519_2026-03-12"] = _stub_pool("short")
    node = create_market_analyst(mock_llm, collector)
    result = asyncio.run(node(_make_state("short")))
    assert "market_report" in result
    assert "analyst_traces" in result
    assert len(result["analyst_traces"]) == 1
    assert result["analyst_traces"][0]["agent"] == "market_analyst"
    assert result["analyst_traces"][0]["verdict"] == "看多"
    assert result["analyst_traces"][0]["horizon"] == "short"


def test_market_analyst_uses_short_window_for_medium_request():
    mock_llm = MagicMock()

    async def _astream(_messages):
        yield SimpleNamespace(content="report")

    mock_llm.astream = _astream
    collector = DataCollector()
    collector._cache["600519_2026-03-12"] = _stub_pool("medium")
    node = create_market_analyst(mock_llm, collector)
    result = asyncio.run(node(_make_state("medium")))
    assert result["analyst_traces"][0]["horizon"] == "short"
    assert result["analyst_traces"][0]["data_window"] == "14天"


def test_market_analyst_prompt_includes_user_context_constraints():
    mock_llm = MagicMock()
    captured = {}

    async def _astream(messages):
        captured["messages"] = messages
        yield SimpleNamespace(content="report")

    mock_llm.astream = _astream
    collector = DataCollector()
    collector._cache["600519_2026-03-12"] = _stub_pool("short")
    node = create_market_analyst(mock_llm, collector)
    state = _make_state("short")
    state["user_context"] = {
        "objective": "短线波段",
        "risk_profile": "谨慎",
        "constraints": ["不追高", "只在回调确认后入场"],
        "user_notes": "优先看情绪和预期，不做长期价值投资。",
    }

    asyncio.run(node(state))

    prompt_text = "\n".join(getattr(message, "content", "") for message in captured["messages"])
    assert "【用户上下文】" in prompt_text
    assert "不追高" in prompt_text
    assert "只在回调确认后入场" in prompt_text
