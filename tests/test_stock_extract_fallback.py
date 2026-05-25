import asyncio
from unittest.mock import MagicMock, patch

from api.main import _ai_extract_symbol_and_date, _ai_extract_symbol_and_date_streaming


def test_stock_extract_falls_back_to_explicit_code_when_llm_fails():
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        side_effect=RuntimeError("quota exhausted"),
    ):
        symbol, trade_date, horizons, *_ = _ai_extract_symbol_and_date(
            "分析 300845.SZ 今日走势",
            {"llm_provider": "zhipu", "quick_think_llm": "glm-coding-plan"},
        )

    assert symbol == "300845.SZ"
    assert trade_date is not None
    assert horizons == ["short"]


def test_stock_extract_does_not_return_unresolved_chinese_name_when_code_exists():
    llm = MagicMock()
    llm.invoke.return_value = '{"stock_name":"天孚通讯","date":"2026-05-24","horizons":["short"],"focus_areas":[],"specific_questions":[],"user_context":{}}'
    client = MagicMock()
    client.get_llm.return_value = llm

    with patch("tradingagents.llm_clients.factory.create_llm_client", return_value=client), \
         patch("api.main._search_cn_stock_by_name", return_value=None), \
         patch("api.main._load_cn_stock_map", return_value={}):
        symbol, trade_date, *_ = _ai_extract_symbol_and_date(
            "分析 300845.SZ 今日走势",
            {"llm_provider": "zhipu", "quick_think_llm": "glm-coding-plan"},
        )

    assert symbol == "300845.SZ"
    assert trade_date == "2026-05-24"


def test_streaming_stock_extract_falls_back_to_explicit_code_when_llm_fails():
    async def scenario():
        with patch(
            "tradingagents.llm_clients.factory.create_llm_client",
            side_effect=RuntimeError("quota exhausted"),
        ):
            return await _ai_extract_symbol_and_date_streaming(
                "分析 002837.SZ 今日走势",
                {"llm_provider": "zhipu", "quick_think_llm": "glm-coding-plan"},
                "job-test",
            )

    symbol, trade_date, horizons, *_ = asyncio.run(scenario())
    assert symbol == "002837.SZ"
    assert trade_date is not None
    assert horizons == ["short"]
