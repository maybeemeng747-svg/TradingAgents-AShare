import asyncio
import json
import inspect
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from api.main import (
    AnalyzeRequest,
    _ensure_query_and_user_intent,
    _extract_request_user_context,
    _resolve_has_position,
)
from api.ta_profile import TAProfile, recommend_profile
from tradingagents.graph.intent_parser import _infer_position_context
from tradingagents.agents.utils.context_utils import (
    build_prompt_context_block,
    extract_price_snapshot,
)


def _raw_evidence():
    return {
        "stock_data": {
            "status": "HAS_DATA",
            "raw": (
                "# completed bars only\n"
                "Date,Open,High,Low,Close,Volume\n"
                "2026-07-27,160,170,158,169.35,100\n"
                "2026-07-28,160,175,158,166.43,120\n"
            ),
        },
        "realtime_quote": {
            "status": "HAS_DATA",
            "fetched_at": "2026-07-29T14:21:00+08:00",
            "raw": json.dumps(
                {
                    "002409.SZ": {
                        "price": 154.6,
                        "open": 160.0,
                        "high": 162.8,
                        "low": 149.79,
                        "previous_close": 166.43,
                        "change_pct": -7.1081,
                        "turnover_rate": 14.73,
                        "volume_ratio": 1.19,
                        "quote_time": "2026-07-29 14:20:03",
                        "source": "sina+tencent",
                    }
                },
                ensure_ascii=False,
            ),
        },
    }


def test_price_snapshot_keeps_completed_bar_and_realtime_quote_separate():
    snapshot = extract_price_snapshot(
        _raw_evidence(),
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 14, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["completed_bar"]["date"] == "2026-07-28"
    assert snapshot["completed_bar"]["close"] == 166.43
    assert snapshot["realtime_quote"]["price"] == 154.6
    assert snapshot["realtime_quote"]["quote_time"] == "2026-07-29 14:20:03"
    assert snapshot["realtime_quote"]["same_trade_date"] is True


def test_price_snapshot_rejects_undated_completed_bar():
    raw = {
        "stock_data": {
            "status": "HAS_DATA",
            "raw": "Open,High,Low,Close,Volume\n160,170,158,169.35,100\n",
        }
    }

    snapshot = extract_price_snapshot(raw, "002409.SZ", "2026-07-29")

    assert snapshot["completed_bar"] is None


def test_historical_price_snapshot_rejects_future_completed_bar():
    raw = {
        "stock_data": {
            "status": "HAS_DATA",
            "raw": (
                "Date,Open,High,Low,Close,Volume\n"
                "2026-07-28,160,170,158,169.35,100\n"
                "2026-07-30,170,180,168,178.00,120\n"
            ),
        }
    }

    snapshot = extract_price_snapshot(raw, "002409.SZ", "2026-07-29")

    assert snapshot["completed_bar"]["date"] == "2026-07-28"
    assert snapshot["completed_bar"]["close"] == 169.35


def test_prompt_context_labels_intraday_quote_as_not_close():
    with patch(
        "tradingagents.agents.utils.context_utils.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(
            2026, 7, 29, 14, 21, tzinfo=ZoneInfo("Asia/Shanghai")
        )
        mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
        block = build_prompt_context_block(
            {
                "company_of_interest": "002409.SZ",
                "trade_date": "2026-07-29",
                "instrument_context": {"symbol": "002409.SZ"},
                "market_context": {},
                "user_context": {},
                "metadata": {"raw_evidence": _raw_evidence()},
            },
            "analyst",
        )

    assert "最近已完成日线：2026-07-28，收盘价 166.43" in block
    assert "实时行情快照：2026-07-29 14:20:03，最新价 154.60" in block
    assert "实时行情快照是盘中状态，不是当日收盘价" in block


def test_stale_realtime_quote_is_labeled_non_executable():
    block = build_prompt_context_block(
        {
            "company_of_interest": "002409.SZ",
            "trade_date": "2026-07-30",
            "instrument_context": {"symbol": "002409.SZ"},
            "market_context": {},
            "user_context": {},
            "metadata": {"raw_evidence": _raw_evidence()},
        },
        "analyst",
    )

    assert "只能作为过期参考" in block
    assert "不得作为当前执行价格" in block


def test_old_same_day_quote_is_non_executable():
    raw = _raw_evidence()
    raw["realtime_quote"]["fetched_at"] = "2026-07-29T14:40:00+08:00"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 14, 40, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"]["quote_age_seconds"] == 1197
    assert snapshot["realtime_quote"]["consumption_age_seconds"] == 1197
    assert snapshot["realtime_quote"]["same_trade_date"] is False


def test_final_risk_gate_refreshes_stale_intraday_quote_once():
    from tradingagents.agents.managers.risk_manager import (
        _refresh_intraday_quote_if_stale,
    )

    raw = _raw_evidence()
    refreshed_payload = json.dumps(
        {
            "002409.SZ": {
                "price": 155.2,
                "quote_time": "2026-07-29 14:39:50",
                "source": "sina+tencent",
            }
        },
        ensure_ascii=False,
    )
    consumed_at = datetime(
        2026, 7, 29, 14, 40, tzinfo=ZoneInfo("Asia/Shanghai")
    )

    with patch(
        "tradingagents.agents.managers.risk_manager.get_realtime_quotes"
    ) as quote_tool:
        quote_tool.invoke.return_value = refreshed_payload
        refreshed = _refresh_intraday_quote_if_stale(
            raw,
            "002409.SZ",
            "2026-07-29",
            now=consumed_at,
        )

    quote_tool.invoke.assert_called_once_with({"symbols": ["002409.SZ"]})
    assert refreshed is not raw
    assert refreshed["realtime_quote"]["refresh_stage"] == "final_risk_gate"
    snapshot = extract_price_snapshot(
        refreshed,
        "002409.SZ",
        "2026-07-29",
        now=consumed_at,
    )
    assert snapshot["realtime_quote"]["price"] == 155.2
    assert snapshot["realtime_quote"]["same_trade_date"] is True


def test_final_risk_gate_does_not_refresh_a_fresh_quote():
    from tradingagents.agents.managers.risk_manager import (
        _refresh_intraday_quote_if_stale,
    )

    raw = _raw_evidence()
    consumed_at = datetime(
        2026, 7, 29, 14, 21, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    with patch(
        "tradingagents.agents.managers.risk_manager.get_realtime_quotes"
    ) as quote_tool:
        result = _refresh_intraday_quote_if_stale(
            raw,
            "002409.SZ",
            "2026-07-29",
            now=consumed_at,
        )

    assert result is raw
    quote_tool.invoke.assert_not_called()


def test_same_day_closing_quote_remains_usable_after_freshness_window():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    quote["002409.SZ"]["quote_time"] = "2026-07-29 15:00:03"
    raw["realtime_quote"]["raw"] = json.dumps(quote, ensure_ascii=False)
    raw["realtime_quote"]["fetched_at"] = "2026-07-29T15:20:00+08:00"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"]["consumption_age_seconds"] == 10797
    assert snapshot["realtime_quote"]["same_trade_date"] is True


def test_pre_close_quote_is_not_promoted_to_closing_snapshot():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    quote["002409.SZ"]["quote_time"] = "2026-07-29 14:45:00"
    raw["realtime_quote"]["raw"] = json.dumps(quote, ensure_ascii=False)
    raw["realtime_quote"]["fetched_at"] = "2026-07-29T14:45:30+08:00"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"]["is_closing_snapshot"] is False
    assert snapshot["realtime_quote"]["same_trade_date"] is False


def test_utc_docker_fetch_timestamp_preserves_fresh_quote():
    raw = _raw_evidence()
    raw["realtime_quote"]["fetched_at"] = "2026-07-29T06:21:00+00:00"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 14, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"]["quote_age_seconds"] == 57
    assert snapshot["realtime_quote"]["same_trade_date"] is True


def test_cn_lunch_break_keeps_last_morning_quote_valid():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    quote["002409.SZ"]["quote_time"] = "2026-07-29 11:30:00"
    raw["realtime_quote"]["raw"] = json.dumps(quote, ensure_ascii=False)
    raw["realtime_quote"]["fetched_at"] = "2026-07-29T11:31:00+08:00"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"]["consumption_age_seconds"] == 3600
    assert snapshot["realtime_quote"]["same_trade_date"] is True


def test_cn_lunch_break_accepts_quote_fetched_later_during_pause():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    quote["002409.SZ"]["quote_time"] = "2026-07-29 11:30:00"
    raw["realtime_quote"]["raw"] = json.dumps(quote, ensure_ascii=False)
    raw["realtime_quote"]["fetched_at"] = "2026-07-29T12:30:00+08:00"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"]["quote_age_seconds"] == 3600
    assert snapshot["realtime_quote"]["is_pause_snapshot"] is True
    assert snapshot["realtime_quote"]["same_trade_date"] is True


def test_cn_lunch_break_rejects_stale_pre_pause_quote():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    quote["002409.SZ"]["quote_time"] = "2026-07-29 11:15:00"
    raw["realtime_quote"]["raw"] = json.dumps(quote, ensure_ascii=False)
    raw["realtime_quote"]["fetched_at"] = "2026-07-29T12:30:00+08:00"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"]["is_pause_snapshot"] is False
    assert snapshot["realtime_quote"]["same_trade_date"] is False


def test_post_close_snapshot_is_not_labeled_as_intraday():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    quote["002409.SZ"]["quote_time"] = "2026-07-29 15:00:03"
    raw["realtime_quote"]["raw"] = json.dumps(quote, ensure_ascii=False)
    raw["realtime_quote"]["fetched_at"] = "2026-07-29T15:01:00+08:00"

    with patch(
        "tradingagents.agents.utils.context_utils.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = datetime(
            2026, 7, 29, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")
        )
        mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
        mock_datetime.combine.side_effect = datetime.combine
        block = build_prompt_context_block(
            {
                "company_of_interest": "002409.SZ",
                "trade_date": "2026-07-29",
                "instrument_context": {"symbol": "002409.SZ"},
                "market_context": {},
                "user_context": {},
                "metadata": {"raw_evidence": raw},
            },
            "analyst",
        )

    assert "收盘执行参考" in block
    assert "实时行情快照是盘中状态" not in block


def test_same_day_daily_row_is_excluded_while_market_is_open():
    raw = _raw_evidence()
    raw["stock_data"]["raw"] += "2026-07-29,160,162.8,149.79,154.6,90\n"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 14, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["completed_bar"]["date"] == "2026-07-28"
    assert snapshot["completed_bar"]["close"] == 166.43
    assert snapshot["realtime_quote"]["price"] == 154.6


def test_forward_analysis_excludes_current_forming_daily_row():
    raw = _raw_evidence()
    raw["stock_data"]["raw"] += "2026-07-29,160,162.8,149.79,154.6,90\n"
    raw["stock_data"]["current_day_status"] = "PARTIAL_INTRADAY"

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-30",
        now=datetime(2026, 7, 29, 14, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["completed_bar"]["date"] == "2026-07-28"
    assert snapshot["completed_bar"]["close"] == 166.43


def test_previous_trade_day_bar_is_final_during_next_morning():
    snapshot = extract_price_snapshot(
        _raw_evidence(),
        "002409.SZ",
        "2026-07-28",
        now=datetime(2026, 7, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["completed_bar"]["date"] == "2026-07-28"
    assert snapshot["completed_bar"]["close"] == 166.43


def test_hk_same_day_bar_uses_hk_session_not_cn_session():
    raw = {
        "stock_data": {
            "status": "HAS_DATA",
            "raw": (
                "Date,Open,High,Low,Close,Volume\n"
                "2026-07-28,500,510,495,505,100\n"
                "2026-07-29,506,512,500,508,80\n"
            ),
        }
    }
    now = datetime(2026, 7, 29, 15, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))

    snapshot = extract_price_snapshot(raw, "0700.HK", "2026-07-29", now=now)

    assert snapshot["completed_bar"]["date"] == "2026-07-28"
    assert snapshot["completed_bar"]["close"] == 505


def test_timestamp_less_collector_quote_stays_non_executable():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    quote["002409.SZ"].pop("quote_time")
    raw["realtime_quote"]["raw"] = json.dumps(quote, ensure_ascii=False)
    raw["realtime_quote"]["as_of"] = "2026-07-29"

    snapshot = extract_price_snapshot(raw, "002409.SZ", "2026-07-29")
    block = build_prompt_context_block(
        {
            "company_of_interest": "002409.SZ",
            "trade_date": "2026-07-29",
            "instrument_context": {"symbol": "002409.SZ"},
            "market_context": {},
            "user_context": {},
            "metadata": {"raw_evidence": raw},
        },
        "analyst",
    )

    assert snapshot["realtime_quote"]["same_trade_date"] is False
    assert snapshot["realtime_quote"]["date_inferred_from_collection"] is False
    assert "只能作为过期参考" in block


def test_timestamp_less_quote_without_matching_evidence_date_is_stale():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    quote["002409.SZ"].pop("quote_time")
    raw["realtime_quote"]["raw"] = json.dumps(quote, ensure_ascii=False)
    raw["realtime_quote"]["as_of"] = "2026-07-28"

    snapshot = extract_price_snapshot(raw, "002409.SZ", "2026-07-29")

    assert snapshot["realtime_quote"]["same_trade_date"] is False
    assert snapshot["realtime_quote"]["date_inferred_from_collection"] is False


def test_invalid_realtime_prices_are_rejected():
    for invalid_price in (0, -1, float("nan"), float("inf")):
        raw = _raw_evidence()
        quote = json.loads(raw["realtime_quote"]["raw"])
        quote["002409.SZ"]["price"] = invalid_price
        raw["realtime_quote"]["raw"] = json.dumps(quote)

        snapshot = extract_price_snapshot(raw, "002409.SZ", "2026-07-29")

        assert snapshot["realtime_quote"] is None


def test_single_quote_for_another_symbol_is_rejected():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    raw["realtime_quote"]["raw"] = json.dumps(
        {"600519.SH": next(iter(quote.values()))},
        ensure_ascii=False,
    )

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 14, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"] is None


def test_safely_normalized_cn_quote_key_is_accepted():
    raw = _raw_evidence()
    quote = json.loads(raw["realtime_quote"]["raw"])
    raw["realtime_quote"]["raw"] = json.dumps(
        {"sz002409": next(iter(quote.values()))},
        ensure_ascii=False,
    )

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 14, 21, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["realtime_quote"]["price"] == 154.6


def test_missing_query_preserves_explicit_entry_intent_and_no_position():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query=None,
        horizons=["short"],
        current_position=0,
        user_intent={
            "ticker": "002409.SZ",
            "horizons": ["short"],
            "analysis_intent": "entry",
            "position_context": {"has_position": False},
            "user_context": {"current_position": 0},
        },
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.query == "分析002409.SZ的短线机会"
    assert request.user_intent["analysis_intent"] == "entry"
    assert request.user_intent["position_context"] == {"has_position": False}
    assert request.user_intent["user_context"] == {"current_position": 0}


def test_existing_chat_query_gets_missing_intent_fields_without_replacement():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="帮我判断是否适合建仓",
        horizons=["short"],
        objective="建仓",
        current_position=0,
        user_intent={
            "ticker": "002409.SZ",
            "horizons": ["short"],
            "raw_query": "帮我判断是否适合建仓",
        },
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.query == "帮我判断是否适合建仓"
    assert request.user_intent["analysis_intent"] == "entry"
    assert request.user_intent["position_context"]["has_position"] is False


def test_missing_query_derives_entry_intent_from_request_objective():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query=None,
        objective="建仓",
        current_position=0,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.user_intent["analysis_intent"] == "entry"
    assert request.user_intent["position_context"]["has_position"] is False

    profile = recommend_profile(
        analysis_intent=request.user_intent["analysis_intent"],
        has_position=False,
    )
    assert profile == TAProfile.MIDLINE_POLICY_LIGHT


def test_missing_query_preserves_specific_position_intent_when_position_is_unknown():
    for objective, expected_intent in (
        ("持仓复盘", "holding"),
        ("加仓判断", "add"),
        ("减仓判断", "reduce"),
        ("减仓止损", "reduce"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=None,
            objective=objective,
            current_position=None,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == expected_intent
        assert recommend_profile(
            analysis_intent=request.user_intent["analysis_intent"],
            has_position=None,
        ) == TAProfile.POSITION_RISK_LIGHT


def test_missing_query_preserves_specific_intent_for_explicit_position():
    for objective, expected_intent in (
        ("加仓判断", "add"),
        ("减仓判断", "reduce"),
        ("清仓止损", "stop_loss"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=None,
            objective=objective,
            current_position=100,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == expected_intent
        assert request.user_intent["position_context"]["has_position"] is True


def test_explicit_zero_position_does_not_become_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query=None,
        objective="持仓复盘",
        current_position=0,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.user_intent["analysis_intent"] == "watch"
    assert request.user_intent["position_context"]["has_position"] is False


def test_no_position_wording_takes_priority_over_holding_substring():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query=None,
        objective="无持仓，准备建仓",
        current_position=None,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.user_intent["analysis_intent"] == "entry"
    assert request.user_intent["position_context"]["has_position"] is False
    assert _resolve_has_position(request.user_intent, request) is False
    assert recommend_profile(
        analysis_intent=request.user_intent["analysis_intent"],
        has_position=_resolve_has_position(request.user_intent, request),
    ) == TAProfile.MIDLINE_POLICY_LIGHT


def test_natural_no_position_phrases_create_explicit_flat_context():
    for objective in (
        "目前没有任何持仓，准备建仓",
        "没有任何实际持仓，只想观察",
        "当前持仓为0，准备建仓",
        "持仓=0，等待机会",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=None,
            objective=objective,
            current_position=None,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        expected_intent = "entry" if "建仓" in objective else "watch"
        assert request.user_intent["analysis_intent"] == expected_intent
        assert request.user_intent["position_context"]["has_position"] is False
        assert _resolve_has_position(request.user_intent, request) is False
        assert request.user_intent["user_context"]["current_position"] == 0


def test_reduce_slash_stop_loss_ui_objective_remains_reduce():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="分析持仓风险",
        objective="减仓/止损",
        current_position=100,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.user_intent["analysis_intent"] == "reduce"


def test_negated_position_actions_do_not_override_holding_intent():
    for objective in (
        "无需止损，继续持仓",
        "不考虑减仓，持仓观察",
        "暂不加仓，继续持有",
        "不减仓，继续持有",
        "不加仓，继续持有",
        "不止损，继续持有",
        "减仓不合适，继续持有",
        "加仓并不合适，继续持有",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            objective=objective,
            current_position=100,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == "holding"


def test_separated_negations_do_not_become_buy_intents():
    for query in (
        "不要在这里买入，只观察",
        "不建议现在加仓，继续持有",
        "暂不考虑在当前位置建仓，先看看",
        "不买入，只观察",
        "买入不是当前选择，只观察",
        "建仓不建议，先看看",
        "不建议立即买入，继续观察",
        "不要贸然建仓，先观察",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=0,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == "watch"
        assert request.user_intent["position_context"]["has_position"] is False


def test_adverbial_negations_preserve_holding_intent():
    for query in (
        "当前不宜马上加仓，继续持有",
        "暂不考虑立即清仓，继续持有",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == "holding"


def test_contrast_after_negation_can_still_express_later_action():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="不建议现在加仓，但是可以继续持有",
        current_position=100,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.user_intent["analysis_intent"] == "holding"


def test_no_position_wording_in_query_materializes_flat_context():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我现在空仓，看看是否适合入场",
        current_position=None,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 0
    assert request.user_intent["analysis_intent"] == "entry"
    assert request.user_intent["position_context"]["has_position"] is False
    assert request.user_intent["user_context"]["current_position"] == 0


def test_positive_position_wording_does_not_become_flat():
    for query in (
        "我有持仓，但没有持仓成本记录，请帮我减仓",
        "我目前有持仓，需要止损",
        "本人现在持有实际持仓，请检查风险",
        "我不是空仓，请检查止损",
        "并非无持仓，需要减仓",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=None,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.current_position is None
        assert request.user_intent["position_context"]["has_position"] is True
        assert _resolve_has_position(request.user_intent, request) is True


def test_missing_position_cost_alone_does_not_assert_flat_position():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="没有持仓成本记录，请检查风险",
        current_position=None,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position is None
    assert request.user_intent["position_context"] is None
    assert _resolve_has_position(request.user_intent, request) is None


def test_current_query_action_overrides_saved_objective():
    for objective, query in (
        ("减仓", "不考虑减仓，继续持有"),
        ("止损", "无需止损，继续持有"),
        ("加仓", "暂不加仓，继续持有"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            objective=objective,
            query=query,
            current_position=100,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == "holding"


def test_current_watch_query_overrides_saved_action_objective():
    for objective, query in (
        ("加仓", "先观察，不执行交易"),
        ("减仓", "暂时观望，只做研究"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            objective=objective,
            query=query,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == "watch"


def test_latest_no_position_statement_overrides_historical_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="之前有持仓，现在没有持仓，想重新建仓",
        current_position=None,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 0
    assert request.user_intent["analysis_intent"] == "entry"
    assert request.user_intent["position_context"]["has_position"] is False


def test_current_no_position_statement_overrides_saved_numeric_position():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我现在不持仓，只想观察",
        current_position=100,
        current_position_pct=55,
        average_cost=160,
        user_intent={
            "analysis_intent": "holding",
            "position_context": {
                "has_position": True,
                "shares": 100,
                "position_pct": 55,
                "avg_cost": 160,
            },
            "user_context": {
                "current_position": 100,
                "current_position_pct": 55,
                "average_cost": 160,
            },
        },
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 0
    assert request.current_position_pct is None
    assert request.average_cost is None
    assert request.user_intent["analysis_intent"] == "watch"
    assert request.user_intent["position_context"]["has_position"] is False
    assert request.user_intent["user_context"]["current_position"] == 0
    assert "current_position_pct" not in request.user_intent["user_context"]
    assert "average_cost" not in request.user_intent["user_context"]


def test_third_party_flat_statement_does_not_override_user_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="北向资金目前无持仓该股，我该减仓吗",
        current_position=100,
        current_position_pct=35,
        average_cost=160,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 100
    assert request.current_position_pct == 35
    assert request.average_cost == 160
    assert request.user_intent["analysis_intent"] == "reduce"
    assert request.user_intent["position_context"]["has_position"] is True


def test_attributed_third_party_flat_statement_does_not_override_user_holding():
    for query in (
        "北向资金数据显示目前无持仓该股，我该减仓吗",
        "机构报告称目前无持仓该股，我该减仓吗",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=35,
            average_cost=160,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.current_position == 100
        assert request.user_intent["analysis_intent"] == "reduce"
        assert request.user_intent["position_context"]["has_position"] is True


def test_second_normalization_preserves_position_extracted_by_parser():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我当前持仓100股，是否加仓",
        user_intent={
            "ticker": "002409.SZ",
            "analysis_intent": "add",
            "position_context": {"has_position": True, "shares": 100},
            "user_context": {"current_position": 100},
        },
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 100
    assert request.user_intent["analysis_intent"] == "add"
    assert request.user_intent["position_context"]["shares"] == 100
    assert request.user_intent["user_context"]["current_position"] == 100


def test_zero_position_clears_contradictory_cost_and_position_pct():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="分析短线机会",
        current_position=0,
        current_position_pct=35,
        average_cost=160,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 0
    assert request.current_position_pct is None
    assert request.average_cost is None
    assert _extract_request_user_context(request) == {"current_position": 0}
    assert request.user_intent["position_context"]["has_position"] is False


def test_analyze_route_normalizes_direct_query_before_profile_selection():
    source = inspect.getsource(__import__("api.main", fromlist=["analyze"]).analyze)

    normalize_index = source.index("_ensure_query_and_user_intent(")
    profile_index = source.index("recommend_profile(")

    assert normalize_index < profile_index


def test_direct_query_hint_does_not_hide_full_intent_parser():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="分析002409.SZ，重点判断半年报和建仓时机",
        user_intent=None,
    )

    hint = _ensure_query_and_user_intent(
        request,
        "002409.SZ",
        materialize_missing_intent=False,
    )

    assert hint == "entry"
    assert request.user_intent is None


def test_direct_query_profile_hint_matches_parser_rules():
    for query, expected in (
        ("600519.SH能不能买？", "entry"),
        ("有没有买点", "entry"),
        ("我被套了怎么办", "holding"),
        ("继续拿着不动吗", "holding"),
        ("我想卖出 AAPL", "reduce"),
        ("是否应该全部卖出离场", "stop_loss"),
    ):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            user_intent=None,
        )

        hint = _ensure_query_and_user_intent(
            request,
            "600519.SH",
            materialize_missing_intent=False,
        )

        assert hint == expected
        assert request.user_intent is None


def test_flat_position_recommendation_does_not_erase_saved_holding():
    for query in (
        "是否建议空仓观望？",
        "从风控角度建议空仓等待还是继续持有？",
        "应该空仓等待还是减仓？",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=35,
            average_cost=160,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")
        parser_context = _infer_position_context(
            query,
            {
                "current_position": 100,
                "current_position_pct": 35,
                "average_cost": 160,
            },
        )

        assert request.current_position == 100
        assert request.current_position_pct == 35
        assert request.average_cost == 160
        assert request.user_intent["position_context"]["has_position"] is True
        assert parser_context["has_position"] is True


def test_explicit_current_flat_position_still_clears_stale_holding():
    for query in (
        "我现在空仓",
        "目前空仓",
        "空仓",
        "我现在不持有，只想观察",
        "我已不持有，看看机会",
        "当前未持有，先观察",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=35,
            average_cost=160,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.current_position == 0
        assert request.current_position_pct is None
        assert request.average_cost is None
        assert request.user_intent["position_context"]["has_position"] is False


def test_opinion_wording_does_not_clear_saved_holding():
    for query, expected_intent in (
        ("我不持有乐观观点，想减仓", "reduce"),
        ("我不持有悲观观点，继续持有", "holding"),
        ("我不持有任何偏见，分析一下", "holding"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=35,
            average_cost=160,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")
        parser_context = _infer_position_context(
            query,
            {
                "current_position": 100,
                "current_position_pct": 35,
                "average_cost": 160,
            },
        )

        assert request.current_position == 100
        assert request.current_position_pct == 35
        assert request.average_cost == 160
        assert request.user_intent["analysis_intent"] == expected_intent
        assert request.user_intent["position_context"]["has_position"] is True
        assert parser_context["has_position"] is True


def test_question_shaped_flat_wording_does_not_clear_saved_holding():
    for query in (
        "我已经空仓了吗？",
        "我现在空仓了么？",
        "请问我有没有持仓",
        "有没有实际持仓？",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=30,
            average_cost=160,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.current_position == 100
        assert request.current_position_pct == 30
        assert request.average_cost == 160
        assert request.user_intent["position_context"]["has_position"] is True


def test_flat_position_recommendation_does_not_erase_real_holding():
    for query in (
        "建议不持仓，等待机会",
        "应该不持仓，继续观察",
        "推荐现在不持仓，规避风险",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=35,
            average_cost=160,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.current_position == 100
        assert request.current_position_pct == 35
        assert request.average_cost == 160
        assert request.user_intent["position_context"]["has_position"] is True


def test_direct_query_symbol_is_resolved_before_job_creation():
    from api.main import _extract_symbol_and_date

    symbol, trade_date = _extract_symbol_and_date(
        "请分析002409.SZ在2026-07-29的中线机会"
    )

    assert symbol == "002409.SZ"
    assert trade_date == "2026-07-29"


def test_direct_query_symbol_resolution_preserves_exchange_and_us_tickers():
    from api.main import _extract_symbol_and_date

    cases = (
        ("分析0700.HK", "0700.HK"),
        ("分析430047.BJ", "430047.BJ"),
        ("分析BRK.B", "BRK.B"),
        ("分析AAPL", "AAPL"),
    )
    for query, expected in cases:
        symbol, _ = _extract_symbol_and_date(query)
        assert symbol == expected


def test_overlong_hk_symbol_is_not_truncated_or_redirected():
    from api.main import _extract_symbol_and_date, _normalize_symbol

    assert _normalize_symbol("123456.HK") == "123456.HK"
    assert _extract_symbol_and_date("分析123456.HK")[0] is None


def test_local_market_labels_do_not_create_phantom_us_symbols(monkeypatch):
    from api import main

    monkeypatch.setattr(main, "_load_cn_stock_map", lambda: {"贵州茅台": "600519.SH"})

    cases = (
        ("分析A股600519", "600519.SH"),
        ("分析ETF 510300", "510300.SH"),
        ("分析SH 600519", "600519.SH"),
        ("分析BJ 430047", "430047.BJ"),
        ("分析A股贵州茅台", "600519.SH"),
    )
    for query, expected in cases:
        assert main._resolve_query_symbol_and_date(query)[0] == expected

    # A remains a valid standalone US ticker when it is not a market label.
    assert main._extract_symbol_and_date("分析 A")[0] == "A"


def test_exchange_suffix_can_be_followed_by_chinese_text():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("分析0700.HK近期走势")[0] == "0700.HK"
    assert _extract_symbol_and_date("分析9988.HK的机会")[0] == "9988.HK"


def test_amounts_and_ambiguous_multi_symbol_queries_are_not_silently_selected():
    from api.main import _extract_symbol_and_date, _query_contains_instrument_code

    for query in (
        "我有100000元，分析贵州茅台",
        "用600000元分析贵州茅台",
        "预算100000元，分析雅克科技",
        "持有100000股，分析贵州茅台",
        "比较600519和002409",
        "比较600519.SH和002409.SZ",
        "分析600519.SH和002409",
        "compare 600519.SH and AAPL",
        "比较0700.HK和AAPL",
        "分析600519和BRK.B",
        "分析 AAPL、MSFT",
        "AAPL/MSFT",
    ):
        assert _extract_symbol_and_date(query)[0] is None

    assert _extract_symbol_and_date("分析600519股票")[0] == "600519.SH"
    assert _extract_symbol_and_date("看看002409")[0] == "002409.SZ"
    assert _query_contains_instrument_code("我有100000元，分析贵州茅台") is False
    assert _query_contains_instrument_code("比较600519和雅克科技") is True


def test_cash_amounts_are_not_counted_as_second_instruments():
    from api.main import _query_has_multiple_explicit_instruments

    for query in (
        "我有100000现金，分析600519",
        "现金100000元，分析600519",
        "预算100000，分析600519",
    ):
        assert _query_has_multiple_explicit_instruments(query) is False


def test_english_command_words_are_not_treated_as_us_tickers():
    from api.main import (
        _extract_symbol_and_date,
        _query_has_multiple_explicit_instruments,
    )

    assert _extract_symbol_and_date("BUY AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("Please analyze AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("Should I buy TSLA?")[0] == "TSLA"
    assert _extract_symbol_and_date("Analyze BRK.B")[0] == "BRK.B"
    assert _extract_symbol_and_date("分析 Apple 的机会")[0] is None
    assert _extract_symbol_and_date("Analyze AAPL and TSLA")[0] is None
    assert _extract_symbol_and_date("Compare AAPL and MSFT")[0] is None
    assert _extract_symbol_and_date("Analyze AAPL, MSFT")[0] is None
    assert _extract_symbol_and_date("analysis of AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("analyze stock AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("please check the stock AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("Analyze AAPL on 2026-08-01") == (
        "AAPL",
        "2026-08-01",
    )
    assert _extract_symbol_and_date("Analyze AAPL for me")[0] == "AAPL"
    assert _extract_symbol_and_date(
        "I bought AAPL on 2026-08-01, analyze it today"
    )[0] == "AAPL"
    assert _extract_symbol_and_date("I want to buy 100 shares of AAPL")[0] == "AAPL"
    assert _query_has_multiple_explicit_instruments(
        "Compare AAPL with its industry"
    ) is False


def test_english_actions_override_stale_preintent_before_profile_selection():
    cases = (
        ("Should I buy AAPL?", 0, "entry", TAProfile.MIDLINE_POLICY_LIGHT),
        ("Should I sell AAPL?", 100, "reduce", TAProfile.POSITION_RISK_LIGHT),
        ("I want to add to AAPL", 100, "add", TAProfile.POSITION_RISK_LIGHT),
        ("Should I hold AAPL?", 100, "holding", TAProfile.POSITION_RISK_LIGHT),
    )
    for query, current_position, expected_intent, expected_profile in cases:
        request = AnalyzeRequest(
            symbol="AAPL",
            query=query,
            current_position=current_position,
            user_intent={
                "ticker": "AAPL",
                "horizons": ["short"],
                "analysis_intent": "watch",
                "position_context": {
                    "has_position": current_position > 0,
                    "shares": current_position or None,
                },
                "user_context": {"current_position": current_position},
            },
        )

        hint = _ensure_query_and_user_intent(request, request.symbol)

        assert hint == expected_intent
        assert request.user_intent["analysis_intent"] == expected_intent
        assert recommend_profile(
            analysis_intent=expected_intent,
            has_position=current_position > 0,
        ) == expected_profile


def test_lowercase_action_tickers_are_resolved_and_multi_target_fails_closed():
    from api.main import (
        _extract_symbol_and_date,
        _query_has_multiple_explicit_instruments,
    )

    assert _extract_symbol_and_date("Should I buy msft?")[0] == "MSFT"
    assert _extract_symbol_and_date("What do you think about aapl?")[0] == "AAPL"
    assert _query_has_multiple_explicit_instruments(
        "I hold aapl, I want to buy msft"
    ) is True
    assert _query_has_multiple_explicit_instruments(
        "Should I sell aapl and buy msft?"
    ) is True
    for query in (
        "analyze AAPL and buy",
        "analyze AAPL and hold",
        "analyze AAPL and watch",
        "check AAPL and wait",
        "I want to buy AAPL",
        "I plan to buy AAPL",
        "I need help with AAPL",
        "what is wrong with AAPL",
    ):
        assert _query_has_multiple_explicit_instruments(query) is False
    assert _query_has_multiple_explicit_instruments("analyze aapl and msft") is True
    assert _query_has_multiple_explicit_instruments(
        "please analyze aapl or msft"
    ) is True
    assert _query_has_multiple_explicit_instruments("Compare AAPL to MSFT") is True
    assert _query_has_multiple_explicit_instruments("Compare AAPL with MSFT") is True
    assert _query_has_multiple_explicit_instruments(
        "I hold AAPL but want to buy MSFT"
    ) is True
    assert _query_has_multiple_explicit_instruments(
        "我持有AAPL但想买MSFT"
    ) is True
    assert _query_has_multiple_explicit_instruments(
        "Compare AAPL with its industry"
    ) is False


def test_pure_english_multi_instrument_check_skips_cn_name_map():
    import api.main as main_module

    with (
        patch.object(
            main_module,
            "_extract_cn_symbols_from_query",
            side_effect=AssertionError("pure English query must not resolve CN names"),
        ),
        patch.object(
            main_module,
            "_load_cn_stock_map",
            side_effect=AssertionError("pure English query must not load CN name map"),
        ),
    ):
        assert main_module._query_has_multiple_explicit_instruments(
            "Compare AAPL with its industry"
        ) is False
        assert main_module._query_has_multiple_explicit_instruments(
            "Compare AAPL and MSFT"
        ) is True


def test_explicit_symbol_resolution_does_not_cold_load_cn_stock_map():
    import api.main as main_module

    with patch.object(
        main_module,
        "_load_cn_stock_map",
        side_effect=AssertionError("explicit symbols must not cold-load CN names"),
    ):
        assert main_module._resolve_query_symbol_and_date(
            "分析600519的风险"
        )[0] == "600519.SH"
        assert main_module._query_has_multiple_explicit_instruments(
            "分析600519的估值和风险"
        ) is False
        assert main_module._resolve_query_symbol_and_date(
            "分析AAPL的风险和收益"
        )[0] == "AAPL"


def test_chinese_command_with_us_ticker_resolves_cn_names_fail_closed():
    import api.main as main_module

    with patch.object(
        main_module,
        "_load_cn_stock_map",
        return_value={"贵州茅台": "600519.SH"},
    ):
        assert main_module._query_has_multiple_explicit_instruments(
            "分析 AAPL"
        ) is False
        assert main_module._query_has_multiple_explicit_instruments(
            "比较 AAPL 和 贵州茅台"
        ) is True


def test_mixed_bare_cn_and_us_tickers_are_rejected_as_multi_instrument():
    import api.main as main_module

    for query in (
        "分析600519和BRK.B",
        "比较600519和AAPL",
        "比较AAPL和600519",
    ):
        assert main_module._query_has_multiple_explicit_instruments(query) is True


def test_unique_uppercase_us_ticker_is_accepted_in_ordinary_prose():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("I own AAPL, should I hold?")[0] == "AAPL"
    assert _extract_symbol_and_date("What do you think about AAPL?")[0] == "AAPL"
    assert _extract_symbol_and_date("请分析一下AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("I own AAPL and MSFT")[0] is None
    assert _extract_symbol_and_date("TODAY ANALYZE AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("NOW CHECK AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("HELP ME ANALYZE AAPL")[0] == "AAPL"


def test_non_ticker_acronyms_do_not_conflict_with_an_explicit_cn_symbol():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("分析600519的ESG表现")[0] == "600519.SH"
    assert _extract_symbol_and_date("分析600519的CEO变更")[0] == "600519.SH"


def test_chinese_category_acronyms_are_not_routed_as_us_tickers():
    from api.main import _extract_symbol_and_date

    for query in (
        "AI概念股有哪些",
        "分析AI概念股机会",
        "新能源ETF能买吗",
        "ST股票风险",
        "CPU行业机会",
        "GPU算力板块怎么看",
    ):
        assert _extract_symbol_and_date(query)[0] is None

    assert _extract_symbol_and_date("ticker AI")[0] == "AI"


def test_cn_exchange_suffix_must_match_the_code_segment():
    from api.main import (
        _has_consistent_cn_exchange,
        _is_valid_analysis_symbol,
        _normalize_analysis_symbol,
    )

    for symbol in (
        "600519.SH", "002409.SZ", "510300.SH", "920045.BJ",
        "000001.SH", "000300.SH", "000688.SH",
    ):
        assert _has_consistent_cn_exchange(symbol) is True
        assert _is_valid_analysis_symbol(symbol) is True

    for symbol in ("600519.BJ", "600519.SZ", "002409.SH", "920045.SH"):
        assert _has_consistent_cn_exchange(symbol) is False
        assert _is_valid_analysis_symbol(symbol) is False

    for symbol in ("RDS-A", "BRK-B", "A1"):
        assert _normalize_analysis_symbol(symbol) == symbol
        assert _is_valid_analysis_symbol(symbol) is True

    assert _normalize_analysis_symbol("BTC-USD") == "BTC-USD"
    assert _is_valid_analysis_symbol("BTC-USD") is False


def test_financial_indicators_are_not_treated_as_us_tickers():
    from api.main import _extract_symbol_and_date

    for query in (
        "分析贵州茅台的PE和ROE",
        "分析雅克科技的RSI和MACD",
        "请看这家公司的EPS、ROA和ATR",
        "PE怎么看",
        "RSI是否超买",
        "MACD有没有金叉",
    ):
        assert _extract_symbol_and_date(query)[0] is None


def test_us_ticker_at_query_start_remains_supported():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("AAPL怎么样")[0] == "AAPL"
    assert _extract_symbol_and_date("BRK.B近期怎么看")[0] == "BRK.B"
    assert _extract_symbol_and_date("分析RDS-A")[0] == "RDS-A"
    assert _extract_symbol_and_date("AAPL is rising, should I buy?")[0] == "AAPL"
    assert _extract_symbol_and_date("AAPL looks attractive")[0] == "AAPL"
    assert _extract_symbol_and_date("AAPL analysis")[0] == "AAPL"
    assert _extract_symbol_and_date("AAPL stock analysis")[0] == "AAPL"
    assert _extract_symbol_and_date("BRK.B stock analysis")[0] == "BRK.B"


def test_malformed_short_exchange_codes_are_not_redirected_to_suffix_tickers():
    from api.main import _extract_symbol_and_date, _normalize_analysis_symbol

    for symbol in ("5.SH", "123.SZ", "12.BJ"):
        assert _extract_symbol_and_date(f"分析{symbol}")[0] is None
        assert _normalize_analysis_symbol(symbol) == symbol


def test_unknown_numeric_exchange_suffix_is_not_reinterpreted_as_bare_a_share():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("分析600519.XX")[0] is None


def test_explicit_context_allows_wordlike_us_tickers():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("AN")[0] == "AN"
    assert _extract_symbol_and_date("Analyze AN")[0] == "AN"
    assert _extract_symbol_and_date("Analyze FOR")[0] == "FOR"
    assert _extract_symbol_and_date("Analyze AI")[0] == "AI"
    assert _extract_symbol_and_date("analyze aapl")[0] == "AAPL"
    assert _extract_symbol_and_date("aapl analysis")[0] == "AAPL"
    assert _extract_symbol_and_date("Analyze for revenue growth")[0] is None


def test_st_prefixed_a_share_name_is_not_ambiguous_with_us_ticker(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {"ST华微": "600360.SH"},
    )

    assert main_module._resolve_query_symbol_and_date("分析ST华微的机会")[0] == "600360.SH"


def test_latin_prefixed_a_share_name_is_not_ambiguous_with_us_ticker(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {"TCL科技": "000100.SZ"},
    )

    assert main_module._resolve_query_symbol_and_date("分析TCL科技")[0] == "000100.SZ"
    assert main_module._resolve_query_symbol_and_date("TCL科技值得买吗")[0] == "000100.SZ"


def test_cross_market_comparison_is_rejected_as_multi_instrument(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {
            "贵州茅台": "600519.SH",
            "TCL科技": "000100.SZ",
        },
    )

    assert main_module._query_has_multiple_explicit_instruments(
        "比较 AAPL 和 贵州茅台"
    ) is True
    assert main_module._query_has_multiple_explicit_instruments(
        "比较 贵州茅台 和 AAPL"
    ) is True
    assert main_module._query_has_multiple_explicit_instruments(
        "分析 TCL科技"
    ) is False
    assert main_module._query_has_multiple_explicit_instruments(
        "分析贵州茅台的PE和ROE"
    ) is False
    assert main_module._query_has_multiple_explicit_instruments(
        "分析贵州茅台，看看PE"
    ) is False
    assert main_module._query_has_multiple_explicit_instruments(
        "I own AAPL, should I hold?"
    ) is False


def test_ss_alias_is_canonicalized_before_analysis_validation():
    import api.main as main_module

    assert main_module._normalize_analysis_symbol("600519.SS") == "600519.SH"
    assert main_module._normalize_analysis_symbol("600519.ss") == "600519.SH"


def test_completed_purchase_with_aspect_marker_establishes_holding():
    request = AnalyzeRequest(
        symbol="510050.SH",
        query="我已经买入了100股，后续怎么看",
    )

    _ensure_query_and_user_intent(request, request.symbol)

    assert request.current_position is None
    assert request.user_intent["position_context"]["has_position"] is True
    assert request.user_intent["analysis_intent"] == "holding"


def test_flat_not_bought_variants_do_not_route_to_entry():
    for query in (
        "我还没买入，只想观察",
        "我还未买入，只想观察",
        "我尚未买入，只想观察",
    ):
        request = AnalyzeRequest(symbol="510050.SH", query=query)

        _ensure_query_and_user_intent(request, request.symbol)

        assert request.current_position == 0
        assert request.user_intent["analysis_intent"] == "watch"


def test_fund_object_buy_question_routes_to_entry():
    request = AnalyzeRequest(symbol="510050.SH", query="这只基金能不能买")

    _ensure_query_and_user_intent(request, request.symbol)

    assert request.user_intent["analysis_intent"] == "entry"


def test_supported_us_class_tickers_receive_us_market_context():
    from tradingagents.agents.utils.context_utils import infer_instrument_context

    for symbol in ("RDS-A", "BRK-B", "A1"):
        context = infer_instrument_context(symbol)
        assert context["market_country"] == "US"
        assert context["currency"] == "USD"


def test_negated_action_modifiers_do_not_select_execution_intent():
    for query, current_position, expected in (
        ("不是要建仓，只是看看", 0, "watch"),
        ("我并不想加仓，继续持有", 100, "holding"),
        ("我不是要止损，只想持有", 100, "holding"),
        ("并非建议买入，只是观察", 0, "watch"),
    ):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=current_position,
        )

        _ensure_query_and_user_intent(request, "600519.SH")

        assert request.user_intent["analysis_intent"] == expected


def test_company_name_query_requires_one_unique_local_symbol(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {
            "贵州茅台": "600519.SH",
            "雅克科技": "002409.SZ",
        },
    )

    assert main_module._extract_cn_symbol_from_query("分析贵州茅台的中线机会") == "600519.SH"
    assert main_module._extract_cn_symbol_from_query("贵州茅台能否买") == "600519.SH"
    assert main_module._extract_cn_symbol_from_query("贵州茅台可以买吗") == "600519.SH"
    assert main_module._extract_cn_symbol_from_query("比较贵州茅台和雅克科技") is None
    assert main_module._extract_cn_symbol_from_query("分析一家未知公司") is None


def test_company_name_resolution_accepts_common_financial_questions(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {"贵州茅台": "600519.SH"},
    )

    for query in (
        "贵州茅台业绩如何",
        "贵州茅台财报怎么样",
        "贵州茅台最近如何",
        "贵州茅台今天跌了多少",
    ):
        assert main_module._resolve_query_symbol_and_date(query)[0] == "600519.SH"


def test_generic_cn_analysis_request_does_not_cold_load_name_map(monkeypatch):
    import api.main as main_module

    def fail_if_loaded():
        raise AssertionError("generic analysis instruction must not load stock map")

    monkeypatch.setattr(main_module, "_load_cn_stock_map", fail_if_loaded)
    monkeypatch.setattr(main_module, "_cn_stock_map", None)

    assert main_module._resolve_query_symbol_and_date(
        "帮我分析一下是否值得买"
    )[0] is None
    assert main_module._query_plausibly_contains_cn_company_name(
        "分析贵州茅台的中线机会"
    ) is True


def test_multi_ticker_detector_handles_lowercase_and_sector_acronyms(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "_load_cn_stock_map", lambda: {})

    for query in (
        "compare aapl and msft valuations",
        "比较 aapl 和 msft 的估值",
        "aapl vs msft which is better",
    ):
        assert main_module._query_has_multiple_explicit_instruments(query) is True

    for query in (
        "分析AAPL与AI行业的关系",
        "比较AAPL和ETF板块走势",
        "AAPL与GPU产业链相关性",
        "分析600519与AI概念的关系",
    ):
        assert main_module._query_has_multiple_explicit_instruments(query) is False


def test_symbol_extraction_handles_buy_phrases_without_promoting_risk_labels(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "_load_cn_stock_map", lambda: {})

    for query in ("我想买 AAPL", "想买AAPL", "能不能买 AAPL"):
        assert main_module._resolve_query_symbol_and_date(query)[0] == "AAPL"

    assert main_module._resolve_query_symbol_and_date(
        "My risk profile is HIGH"
    )[0] is None
    assert main_module._resolve_query_symbol_and_date(
        "Is AAPL overvalued?"
    )[0] == "AAPL"


def test_flat_position_is_not_overridden_by_position_advice_question():
    for query in ("当前仓位建议是多少", "目前仓位上限是多少"):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=0,
        )

        _ensure_query_and_user_intent(request, "600519.SH")

        assert request.current_position == 0
        assert request.user_intent["position_context"]["has_position"] is False
        assert request.user_intent["analysis_intent"] != "holding"


def test_proposed_trade_size_does_not_assert_an_existing_position():
    for query in (
        "建议仓位为20%，现在可以买入吗",
        "目标仓位为20%，现在可以买入吗",
        "计划仓位为20%，现在可以买入吗",
        "我希望仓位为20%，现在可以买入吗",
        "最大仓位20%，现在可以买入吗",
        "仓位上限20%，现在可以买入吗",
        "单票仓位控制在20%，现在可以买入吗",
        "风险预算仓位20%，现在可以买入吗",
        "我买入100股可以吗",
    ):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=0,
        )

        _ensure_query_and_user_intent(
            request,
            "600519.SH",
            numeric_position_was_explicit=False,
        )

        assert request.user_intent["position_context"]["has_position"] is False
        assert request.user_intent["analysis_intent"] == "entry"


def test_entry_stop_loss_parameters_do_not_become_exit_requests():
    for query in (
        "我想建仓，止损位设为10元",
        "准备买入，止损条件怎么设",
        "未持仓，买入后止损价设多少",
    ):
        request = AnalyzeRequest(symbol="600519.SH", query=query)

        _ensure_query_and_user_intent(request, "600519.SH")

        assert request.user_intent["analysis_intent"] == "entry"
        position_context = request.user_intent.get("position_context") or {}
        assert position_context.get("has_position") is not True


def test_company_name_resolution_prefers_maximal_nested_name(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {
            "半导体ETF": "512480.SH",
            "半导体ETF联接": "012969.SZ",
        },
    )

    assert main_module._extract_cn_symbol_from_query("分析半导体ETF联接") is None
    assert main_module._extract_cn_symbol_from_query(
        "比较半导体ETF和半导体ETF联接"
    ) is None


def test_position_alternative_preserves_authoritative_request_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="现在空仓还是继续持有？",
        current_position=100,
        current_position_pct=35,
        average_cost=160,
    )

    _ensure_query_and_user_intent(request, request.symbol)

    assert request.current_position == 100
    assert request.user_intent["analysis_intent"] == "holding"
    assert request.user_intent["position_context"] == {
        "has_position": True,
        "avg_cost": 160.0,
        "shares": 100.0,
        "position_pct": 35.0,
        "holding_days": None,
    }


def test_single_code_with_topic_clause_does_not_cold_load_name_map(monkeypatch):
    import api.main as main_module

    def fail_if_loaded():
        raise AssertionError("single-code topic clause must not cold-load stock map")

    monkeypatch.setattr(main_module, "_load_cn_stock_map", fail_if_loaded)
    monkeypatch.setattr(main_module, "_cn_stock_map", None)

    assert main_module._query_has_multiple_explicit_instruments(
        "分析600519.SH，重点看估值"
    ) is False


def test_explicit_company_name_code_pair_fails_closed_on_mismatch(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {"五粮液": "000858.SZ", "贵州茅台": "600519.SH"},
    )
    monkeypatch.setattr(main_module, "_cn_stock_map", None)

    symbol, _ = main_module._resolve_query_symbol_and_date(
        "分析五粮液(600519.SH)"
    )
    assert symbol is None


def test_unparenthesized_company_name_code_pair_cold_loads_and_fails_closed(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {"五粮液": "000858.SZ", "贵州茅台": "600519.SH"},
    )
    monkeypatch.setattr(main_module, "_cn_stock_map", None)

    symbol, _ = main_module._resolve_query_symbol_and_date(
        "分析贵州茅台 000858.SZ"
    )
    assert symbol is None


def test_query_with_code_and_different_company_name_is_ambiguous(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {
            "贵州茅台": "600519.SH",
            "雅克科技": "002409.SZ",
        },
    )

    assert main_module._resolve_query_symbol_and_date(
        "比较600519和雅克科技"
    )[0] is None
    assert main_module._resolve_query_symbol_and_date(
        "分析600519贵州茅台"
    )[0] == "600519.SH"


def test_company_name_resolution_accepts_exchange_traded_fund(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {"芯片ETF": "159995.SZ"},
    )

    assert main_module._extract_cn_symbol_from_query("分析芯片ETF") == "159995.SZ"


def test_company_name_resolution_does_not_match_ordinary_phrase(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {"值得买": "300785.SZ"},
    )

    assert main_module._extract_cn_symbol_from_query("这只股票值得买吗？") is None
    assert main_module._extract_cn_symbol_from_query("分析值得买的基本面") == "300785.SZ"


def test_market_today_uses_instrument_timezone():
    from tradingagents.agents.utils.context_utils import market_today_str

    shanghai_morning = datetime(
        2026, 8, 4, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )

    assert market_today_str("002409.SZ", now=shanghai_morning) == "2026-08-04"
    assert market_today_str("0700.HK", now=shanghai_morning) == "2026-08-04"
    assert market_today_str("AAPL", now=shanghai_morning) == "2026-08-03"


def test_stock_extract_keeps_omitted_date_empty_for_market_local_default():
    import api.main as main_module

    class _Response:
        content = json.dumps(
            {
                "stock_name": "AAPL",
                "date": None,
                "horizons": ["short"],
                "focus_areas": [],
                "specific_questions": [],
                "user_context": {},
            }
        )

    class _LLM:
        model_name = "test"

        def invoke(self, _prompt):
            return _Response()

    class _Client:
        def get_llm(self):
            return _LLM()

    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=_Client(),
    ):
        symbol, trade_date, *_ = main_module._ai_extract_symbol_and_date(
            "分析 AAPL 的短线机会", {}
        )

    assert symbol == "AAPL"
    assert trade_date is None


def test_stock_extract_preserves_explicit_regex_date_when_llm_omits_it():
    import api.main as main_module

    class _Response:
        content = json.dumps(
            {
                "stock_name": "AAPL",
                "date": None,
                "horizons": ["short"],
                "focus_areas": [],
                "specific_questions": [],
                "user_context": {},
            }
        )

    class _LLM:
        model_name = "test"

        def invoke(self, _prompt):
            return _Response()

    class _Client:
        def get_llm(self):
            return _LLM()

    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=_Client(),
    ):
        symbol, trade_date, *_ = main_module._ai_extract_symbol_and_date(
            "AAPL 2026-02-28", {}
        )

    assert symbol == "AAPL"
    assert trade_date == "2026-02-28"


def test_streaming_stock_extract_preserves_explicit_regex_date_when_llm_omits_it(
    monkeypatch,
):
    import api.main as main_module

    class _Chunk:
        content = json.dumps(
            {
                "stock_name": "AAPL",
                "date": None,
                "horizons": ["short"],
                "focus_areas": [],
                "specific_questions": [],
                "user_context": {},
            }
        )

    class _LLM:
        model_name = "test"

        async def astream(self, _prompt):
            yield _Chunk()

    class _Client:
        def get_llm(self):
            return _LLM()

    monkeypatch.setattr(main_module, "_emit_job_event", lambda *_args, **_kwargs: None)
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=_Client(),
    ):
        symbol, trade_date, *_ = asyncio.run(
            main_module._ai_extract_symbol_and_date_streaming(
                "AAPL 2026-02-28", {}, "test-job"
            )
        )

    assert symbol == "AAPL"
    assert trade_date == "2026-02-28"


def test_stock_extract_preserves_hyphenated_class_ticker():
    import api.main as main_module

    class _Response:
        content = json.dumps(
            {
                "stock_name": "BRK-B",
                "date": None,
                "horizons": ["short"],
                "focus_areas": [],
                "specific_questions": [],
                "user_context": {},
            }
        )

    class _LLM:
        model_name = "test"

        def invoke(self, _prompt):
            return _Response()

    class _Client:
        def get_llm(self):
            return _LLM()

    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=_Client(),
    ):
        symbol, *_ = main_module._ai_extract_symbol_and_date(
            "分析 BRK-B 的短线机会", {}
        )

    assert symbol == "BRK-B"


def test_explicit_trade_date_wins_over_query_date(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "market_today_str", lambda _symbol: "2026-08-03")

    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2024-01-15",
        parsed_query_date="2023-01-01",
        trade_date_was_explicit=True,
    ) == "2024-01-15"
    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-04",
        parsed_query_date="2023-01-01",
        trade_date_was_explicit=False,
    ) == "2023-01-01"
    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-04",
        parsed_query_date=None,
        trade_date_was_explicit=False,
    ) == "2026-08-03"


def test_transaction_date_does_not_replace_current_analysis_date(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "market_today_str", lambda _symbol: "2026-08-10")

    assert main_module._resolve_analysis_trade_date(
        symbol="600519.SH",
        current_trade_date="2026-08-10",
        parsed_query_date="2024-01-15",
        trade_date_was_explicit=False,
        query_text="我在2024-01-15买入600519.SH，现在该减仓吗？",
    ) == "2026-08-10"
    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-10",
        parsed_query_date="2024-01-15",
        trade_date_was_explicit=False,
        query_text="请分析 AAPL 在 2024-01-15 的表现",
    ) == "2024-01-15"
    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-10",
        parsed_query_date="2024-01-15",
        trade_date_was_explicit=False,
        query_text="我在2024-01-15买入AAPL，今天分析持仓风险",
    ) == "2026-08-10"
    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-10",
        parsed_query_date="2024-01-15",
        trade_date_was_explicit=False,
        query_text="分析 AAPL 2024-01-15",
    ) == "2024-01-15"
    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-10",
        parsed_query_date="2026-08-01",
        trade_date_was_explicit=False,
        query_text="AAPL 2026-08-01",
    ) == "2026-08-01"


def test_dated_completed_purchase_routes_as_holding():
    for query in (
        "我昨天买入了100股AAPL，现在怎么办",
        "昨天我买入了100股AAPL，现在怎么办",
        "上周三我买入100股AAPL，现在怎么办",
        "我上周买了100股AAPL，现在怎么办",
    ):
        request = AnalyzeRequest(symbol="AAPL", query=query)

        intent = _ensure_query_and_user_intent(request, "AAPL")

        assert intent == "holding"
        assert request.user_intent["analysis_intent"] == "holding"
        assert request.user_intent["position_context"]["has_position"] is True


def test_relative_and_localized_analysis_dates_are_preserved(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "market_today_str", lambda _symbol: "2026-08-10")

    for query, expected in (
        ("分析 AAPL 昨天走势", "2026-08-09"),
        ("分析 AAPL 截至2026年8月8日的走势", "2026-08-08"),
        ("分析 AAPL 在2026/8/8的表现", "2026-08-08"),
    ):
        assert main_module._resolve_analysis_trade_date(
            symbol="AAPL",
            current_trade_date="2026-08-10",
            parsed_query_date="2026-08-08",
            trade_date_was_explicit=False,
            query_text=query,
        ) == expected

    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-10",
        parsed_query_date="2026-08-08",
        trade_date_was_explicit=False,
        query_text="我昨天买入 AAPL，现在分析持仓风险",
    ) == "2026-08-10"

    for query in (
        "分析600519.SH，2025-06-30发布的半年报怎么看",
        "分析600519.SH，公司在2025-06-30发布公告",
        "分析600519在2024-06-01发布公告后的走势",
        "分析600519 2024-06-01财报发布后的表现",
    ):
        assert main_module._resolve_analysis_trade_date(
            symbol="600519.SH",
            current_trade_date="2026-08-10",
            parsed_query_date="2025-06-30",
            trade_date_was_explicit=False,
            query_text=query,
        ) == "2026-08-10"


def test_explicit_position_percentage_is_authoritative_and_propagated():
    flat_request = AnalyzeRequest(
        symbol="002409.SZ",
        query="分析当前持仓",
        current_position_pct=0,
    )
    _ensure_query_and_user_intent(flat_request, flat_request.symbol)

    assert flat_request.user_intent["position_context"]["has_position"] is False
    assert flat_request.user_intent["analysis_intent"] == "watch"

    holding_request = AnalyzeRequest(
        symbol="002409.SZ",
        query="分析当前持仓",
        current_position_pct=20,
    )
    _ensure_query_and_user_intent(holding_request, holding_request.symbol)

    assert holding_request.user_intent["position_context"]["has_position"] is True
    assert holding_request.user_intent["position_context"]["position_pct"] == 20
    assert holding_request.user_intent["user_context"]["current_position_pct"] == 20
    assert holding_request.user_intent["analysis_intent"] == "holding"


def test_analysis_topics_are_not_treated_as_second_tickers():
    from api.main import _query_has_multiple_explicit_instruments

    for query in (
        "analyze AAPL, earnings",
        "analyze AAPL and outlook",
        "analyze AAPL, revenue",
        "analyze AAPL and risk",
        "Compare value and momentum for AAPL",
        "Analyze technical and fundamental signals for AAPL",
    ):
        assert _query_has_multiple_explicit_instruments(query) is False

    assert _query_has_multiple_explicit_instruments("analyze AAPL and MSFT") is True


def test_direct_holding_and_valuation_questions_accept_unique_ticker():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("Should I hold AAPL?")[0] == "AAPL"
    assert _extract_symbol_and_date("Is AAPL overvalued?")[0] == "AAPL"
    assert _extract_symbol_and_date("What is the PE of AAPL?")[0] == "AAPL"
    assert _extract_symbol_and_date("Research MSFT")[0] == "MSFT"
    assert _extract_symbol_and_date("Analyze risk for AAPL")[0] == "AAPL"


def test_analysis_symbol_normalizer_rejects_malformed_explicit_values():
    from api.main import _is_valid_analysis_symbol, _normalize_analysis_symbol

    for raw in ("1234567", "12345678", "0000012", "600519x", "BRK-B."):
        normalized = _normalize_analysis_symbol(raw)
        assert _is_valid_analysis_symbol(normalized) is False

    assert _normalize_analysis_symbol("600519") == "600519.SH"
    assert _normalize_analysis_symbol("SH600519") == "600519.SH"
    assert _normalize_analysis_symbol("SZ000001") == "000001.SZ"
    assert _normalize_analysis_symbol("BJ920001") == "920001.BJ"
    assert _normalize_analysis_symbol("SH000001") == "000001.SH"


def test_direct_analysis_symbol_resolves_only_exact_cn_company_name(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(
        main_module,
        "_load_cn_stock_map",
        lambda: {
            "贵州茅台": "600519.SH",
            "TCL科技": "000100.SZ",
        },
    )

    assert main_module._resolve_analysis_symbol("贵州茅台") == "600519.SH"
    assert main_module._resolve_analysis_symbol("TCL科技") == "000100.SZ"
    assert main_module._resolve_analysis_symbol("茅台") == "茅台"


def test_direct_analyze_preflight_rejects_lowercase_multi_ticker_query():
    from api.main import analyze

    source = inspect.getsource(analyze)
    multi_gate = source.index("_query_has_multiple_explicit_instruments")
    symbol_resolution = source.index("_resolve_query_symbol_and_date")

    assert multi_gate < symbol_resolution


def test_query_date_is_parsed_even_when_symbol_field_is_supplied():
    from api.main import _extract_symbol_and_date

    symbol, query_date = _extract_symbol_and_date(
        "请分析 AAPL 在 2023-01-01 的表现"
    )

    assert symbol == "AAPL"
    assert query_date == "2023-01-01"


def test_920_series_is_normalized_as_beijing_exchange():
    from api.main import _is_supported_cn_analysis_symbol, _normalize_symbol
    from tradingagents.agents.utils.context_utils import build_market_context

    assert _normalize_symbol("920045") == "920045.BJ"
    assert _is_supported_cn_analysis_symbol("920045.BJ") is True
    assert build_market_context("920045", "2026-08-10")["exchange"] == "BJ"


def test_holder_buy_request_routes_to_add_intent():
    for query in ("我已经持仓100股，还能买吗", "我有持仓，想买一些"):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
        )

        _ensure_query_and_user_intent(request, request.symbol)

        assert request.user_intent["analysis_intent"] == "add"


def test_adjectival_buy_negations_remain_watch_intent():
    for query in (
        "这里不适合买入，只想观察",
        "当前不可买入，只想观察",
        "现阶段不支持买入，继续观望",
    ):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=0,
        )

        _ensure_query_and_user_intent(request, request.symbol)

        assert request.user_intent["analysis_intent"] == "watch"


def test_short_us_tickers_are_allowed_in_explicit_symbol_context():
    from api.main import _extract_symbol_and_date

    cases = (
        ("Analyze ON", "ON"),
        ("Should I buy IT?", "IT"),
        ("Analyze NOW", "NOW"),
        ("分析 A", "A"),
        ("查看 CAN", "CAN"),
        ("ON", "ON"),
    )
    for query, expected in cases:
        assert _extract_symbol_and_date(query)[0] == expected

    assert _extract_symbol_and_date("CAN you analyze this stock?")[0] is None


def test_lowercase_us_tickers_are_normalized_and_comparisons_fail_closed():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("aapl")[0] == "AAPL"
    assert _extract_symbol_and_date("analyze brk.b")[0] == "BRK.B"
    assert _extract_symbol_and_date("msft vs aapl")[0] is None
    assert _extract_symbol_and_date("r is rising")[0] is None


def test_chinese_us_ticker_context_collects_every_mentioned_symbol():
    from api.main import _extract_symbol_and_date

    assert _extract_symbol_and_date("比较AAPL和MSFT")[0] is None
    assert _extract_symbol_and_date("我持有AAPL")[0] == "AAPL"
    assert _extract_symbol_and_date("买入AAPL")[0] == "AAPL"


def test_common_buy_wording_routes_to_entry_profile():
    from api.main import AnalyzeRequest, _ensure_query_and_user_intent
    from api.ta_profile import TAProfile, recommend_profile

    for query in (
        "我想买600519",
        "600519能买吗",
        "是否应该买入600519",
        "600519值得买吗",
        "600519什么时候买",
        "600519买多少",
    ):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=0,
        )
        intent = _ensure_query_and_user_intent(
            request,
            request.symbol,
            materialize_missing_intent=False,
        )

        assert intent == "entry"
        assert recommend_profile(
            analysis_intent=intent,
            has_position=False,
        ) == TAProfile.MIDLINE_POLICY_LIGHT


def test_empty_raw_evidence_cannot_be_replaced_by_report_price():
    from tradingagents.agents.managers.risk_manager import _build_valuation_check

    result = _build_valuation_check(
        {},
        "600519.SH",
        "2026-08-04",
        "建议在当前价150元附近买入。",
        "最新价150元。",
    )

    assert result["price_unavailable"] is True


def test_beijing_exchange_uses_cn_market_finality():
    from tradingagents.agents.utils.context_utils import (
        infer_instrument_context,
        is_daily_bar_final,
    )

    now = datetime(2026, 8, 4, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    context = infer_instrument_context("430047.BJ")

    assert context["market_country"] == "CN"
    assert context["exchange"] == "BJ"
    assert is_daily_bar_final("430047.BJ", "2026-08-04", now=now) is False


def test_explicit_sell_phrases_override_saved_holding_intent():
    for query, expected in (
        ("是否应该全部卖出离场", "stop_loss"),
        ("全部卖出", "stop_loss"),
        ("部分卖出离场", "reduce"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            user_intent={
                "analysis_intent": "holding",
                "position_context": {"has_position": True, "shares": 100},
                "user_context": {"current_position": 100},
            },
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == expected


def test_copular_flat_position_overrides_stale_saved_holding():
    for query in (
        "我现在是空仓，看看能不能买",
        "我目前为空仓，准备建仓",
        "我当前仓位为零，想入场",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=30,
            average_cost=160,
            user_intent={
                "analysis_intent": "holding",
                "position_context": {"has_position": True, "shares": 100},
                "user_context": {"current_position": 100},
            },
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.current_position == 0
        assert request.current_position_pct is None
        assert request.average_cost is None
        assert request.user_intent["position_context"]["has_position"] is False
        assert request.user_intent["analysis_intent"] == "entry"


def test_not_bought_query_overrides_stale_saved_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我还没买，想看看能不能买",
        current_position=100,
        current_position_pct=30,
        average_cost=160,
        user_intent={
            "analysis_intent": "holding",
            "position_context": {"has_position": True, "shares": 100},
            "user_context": {"current_position": 100},
        },
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 0
    assert request.current_position_pct is None
    assert request.average_cost is None
    assert request.user_intent["position_context"]["has_position"] is False
    assert request.user_intent["analysis_intent"] == "entry"


def test_explicit_holding_query_overrides_imported_zero_position():
    from api.main import _apply_user_context_to_request

    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我有持仓，请帮我减仓",
    )
    _apply_user_context_to_request(request, {"current_position": 0})

    _ensure_query_and_user_intent(
        request,
        "002409.SZ",
        numeric_position_was_explicit=False,
    )

    assert request.current_position is None
    assert request.user_intent["position_context"]["has_position"] is True
    assert request.user_intent["analysis_intent"] == "reduce"


def test_explicit_zero_position_pct_clears_imported_holding_tuple():
    from api.main import _apply_user_context_to_request

    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="分析当前风险",
        current_position_pct=0,
    )
    explicit_context = _extract_request_user_context(request)
    _apply_user_context_to_request(
        request,
        {
            "current_position": 100,
            "current_position_pct": 0,
            "average_cost": 160,
        },
    )

    _ensure_query_and_user_intent(
        request,
        request.symbol,
        numeric_position_was_explicit="current_position" in explicit_context,
    )

    assert request.current_position == 0
    assert request.current_position_pct is None
    assert request.average_cost is None
    assert request.user_intent["position_context"]["has_position"] is False
    assert request.user_intent["user_context"]["current_position"] == 0
    assert request.user_intent["analysis_intent"] == "watch"


def test_explicit_flat_objective_clears_imported_holding_tuple():
    from api.main import _apply_user_context_to_request

    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="分析当前风险",
        objective="无持仓，准备建仓",
    )
    explicit_context = _extract_request_user_context(request)
    _apply_user_context_to_request(
        request,
        {
            "objective": "无持仓，准备建仓",
            "current_position": 100,
            "current_position_pct": 20,
            "average_cost": 160,
        },
    )

    _ensure_query_and_user_intent(
        request,
        request.symbol,
        numeric_position_was_explicit="current_position" in explicit_context,
    )

    assert request.current_position == 0
    assert request.current_position_pct is None
    assert request.average_cost is None
    assert request.user_intent["position_context"]["has_position"] is False
    assert request.user_intent["user_context"]["current_position"] == 0
    assert request.user_intent["analysis_intent"] == "entry"


def test_explicit_objective_survives_third_party_or_negated_query_actions():
    for query, objective, expected in (
        ("机构已经买入600519，帮我分析", "减仓", "reduce"),
        ("主力是否买入600519", "止损", "stop_loss"),
        ("我不打算买入，只分析风险", "加仓", "add"),
    ):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            objective=objective,
            current_position=100,
        )

        _ensure_query_and_user_intent(request, request.symbol)

        assert request.user_intent["analysis_intent"] == expected


def test_natural_holding_declarations_route_buy_questions_to_add():
    for query in (
        "我已经持仓100股，还能买吗",
        "持仓100股，想买一些",
    ):
        request = AnalyzeRequest(symbol="002409.SZ", query=query)

        intent = _ensure_query_and_user_intent(
            request,
            request.symbol,
            materialize_missing_intent=False,
        )

        assert intent == "add"
        assert request.user_intent is None


def test_completed_purchase_with_share_count_routes_as_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我已经买入100股，后续怎么看",
    )

    intent = _ensure_query_and_user_intent(
        request,
        request.symbol,
        materialize_missing_intent=False,
    )

    assert intent == "holding"
    assert request.current_position is None


def test_completed_purchase_without_share_unit_routes_as_holding():
    for query in (
        "我已经买入600519，后续怎么看",
        "我已经买入这只股票，后续怎么看",
        "我刚买了贵州茅台，后续怎么看",
        "我已买入AAPL，后续怎么看",
    ):
        request = AnalyzeRequest(symbol="002409.SZ", query=query)

        intent = _ensure_query_and_user_intent(
            request,
            request.symbol,
            materialize_missing_intent=False,
        )

        assert intent == "holding", query


def test_completed_purchase_still_allows_explicit_follow_up_add_request():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我已经买入600519，现在是否加仓",
    )

    intent = _ensure_query_and_user_intent(
        request,
        request.symbol,
        materialize_missing_intent=False,
    )

    assert intent == "add"


def test_rejecting_additional_purchase_does_not_clear_existing_holding():
    for query in (
        "我持有100股，但没有买入更多的计划",
        "我当前持仓100股，暂时没有买入额外计划",
    ):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=100,
        )

        intent = _ensure_query_and_user_intent(request, request.symbol)

        assert intent == "holding"
        assert request.current_position == 100
        assert request.user_intent["position_context"]["has_position"] is True


def test_no_holding_contrast_overrides_stale_imported_position_for_entry():
    request = AnalyzeRequest(
        symbol="600519.SH",
        query="我不持有但想买600519",
        current_position=100,
    )

    intent = _ensure_query_and_user_intent(request, request.symbol)

    assert intent == "entry"
    assert request.current_position == 0
    assert request.user_intent["position_context"]["has_position"] is False


def test_completed_sale_history_does_not_request_another_reduction():
    request = AnalyzeRequest(
        symbol="600519.SH",
        query="我昨天卖出100股，现在仍持有200股",
        current_position=200,
    )

    intent = _ensure_query_and_user_intent(request, request.symbol)

    assert intent == "holding"
    assert request.current_position == 200


def test_attributed_buy_ratings_do_not_become_user_entry_requests():
    for query in (
        "券商给出买入评级，分析600519",
        "分析师给了买入建议，分析600519",
        "研报维持买入评级，分析600519",
    ):
        request = AnalyzeRequest(symbol="600519.SH", query=query)

        intent = _ensure_query_and_user_intent(request, request.symbol)

        assert intent == "watch"


def test_third_party_completed_purchase_does_not_create_user_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="机构已经买入600519，我先观察",
    )

    intent = _ensure_query_and_user_intent(
        request,
        request.symbol,
        materialize_missing_intent=False,
    )

    assert intent == "watch"


def test_third_party_actions_do_not_create_user_holding():
    for query in (
        "大股东减仓了吗，我只想观察",
        "机构建议加仓，我只想观察",
    ):
        request = AnalyzeRequest(symbol="002409.SZ", query=query)

        _ensure_query_and_user_intent(request, request.symbol)

        assert request.current_position is None
        position_context = request.user_intent.get("position_context") or {}
        assert position_context.get("has_position") is not True
        assert request.user_intent["analysis_intent"] == "watch"


def test_positive_position_pct_without_shares_routes_as_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="分析当前持仓",
        current_position_pct=20,
    )

    intent = _ensure_query_and_user_intent(
        request,
        request.symbol,
        materialize_missing_intent=False,
    )

    assert intent == "holding"
    assert request.current_position is None
    assert request.current_position_pct == 20


def test_preparsed_percentage_only_holding_is_propagated_to_position_context():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="现在是30%仓位，想加仓",
        user_intent={
            "ticker": "002409.SZ",
            "user_context": {"current_position_pct": 30.0},
        },
    )

    intent = _ensure_query_and_user_intent(request, request.symbol)

    assert intent == "add"
    assert request.current_position is None
    assert request.current_position_pct == 30.0
    assert request.user_intent["position_context"] == {
        "has_position": True,
        "avg_cost": None,
        "shares": None,
        "position_pct": 30.0,
        "holding_days": None,
    }
    assert _resolve_has_position(request.user_intent, request) is True


def test_preparsed_positive_pct_overrides_stale_zero_share_context():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我现在半仓持有，帮我分析风险",
        user_intent={
            "ticker": "002409.SZ",
            "user_context": {
                "current_position": 0,
                "current_position_pct": 50.0,
            },
            "position_context": {
                "has_position": False,
                "position_status_explicit": False,
                "shares": 0,
                "position_pct": 50.0,
            },
        },
    )

    intent = _ensure_query_and_user_intent(request, request.symbol)

    assert intent == "holding"
    assert request.current_position is None
    assert request.current_position_pct == 50.0
    assert request.user_intent["position_context"]["has_position"] is True
    assert _resolve_has_position(request.user_intent, request) is True


def test_explicit_positive_position_pct_overrides_imported_zero_shares():
    from api.main import _apply_user_context_to_request

    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="请分析当前持仓风险",
        current_position_pct=20,
    )
    explicit_context = _extract_request_user_context(request)
    _apply_user_context_to_request(
        request,
        {
            "current_position": 0,
            "current_position_pct": 20,
        },
    )

    intent = _ensure_query_and_user_intent(
        request,
        request.symbol,
        materialize_missing_intent=False,
        numeric_position_was_explicit="current_position" in explicit_context,
        position_pct_was_explicit="current_position_pct" in explicit_context,
    )

    assert intent == "holding"
    assert request.current_position is None
    assert request.current_position_pct == 20
    assert _resolve_has_position(request.user_intent, request) is True


def test_negated_buy_plan_does_not_clear_real_position():
    for query in ("我持有100股，但没有买入计划", "我持有100股，没有买入信号"):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=20,
            average_cost=150,
        )

        intent = _ensure_query_and_user_intent(request, request.symbol)

        assert intent == "holding"
        assert request.current_position == 100
        assert request.current_position_pct == 20
        assert request.average_cost == 150


def test_us_exchange_suffix_and_class_tickers_remain_supported():
    import api.main as main_module
    from tradingagents.agents.utils.context_utils import infer_instrument_context

    for symbol in ("AAPL.US", "DELL.US", "BRK-B"):
        assert main_module._is_valid_analysis_symbol(symbol)
        assert infer_instrument_context(symbol)["market_country"] == "US"

    assert main_module._extract_symbol_and_date("Analyze BRK-B.")[0] == "BRK-B"
    assert main_module._normalize_analysis_symbol("BRK-B.") == "BRK-B."
    assert not main_module._is_valid_analysis_symbol("BRK-B.")


def test_third_party_flat_position_does_not_clear_real_holding():
    for query in (
        "基金经理目前无持仓该股，我该减仓吗",
        "分析师称目前无持仓该股，我该减仓吗",
        "研报显示目前无持仓该股，我该减仓吗",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=20,
            average_cost=150,
        )

        intent = _ensure_query_and_user_intent(request, request.symbol)

        assert intent == "reduce"
        assert request.current_position == 100
        assert request.current_position_pct == 20
        assert request.average_cost == 150


def test_position_value_question_does_not_create_a_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="这只股票有持仓价值吗？",
        current_position=0,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 0
    assert request.user_intent["position_context"]["has_position"] is False
    assert _resolve_has_position(request.user_intent, request) is False


def test_possessive_position_risk_keeps_reduce_intent_and_holding_context():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="评估我的持仓风险，要不要减仓",
    )

    intent = _ensure_query_and_user_intent(request, request.symbol)

    assert intent == "reduce"
    assert request.user_intent["analysis_intent"] == "reduce"
    assert request.user_intent["position_context"]["has_position"] is True
    assert _resolve_has_position(request.user_intent, request) is True


def test_negative_position_value_question_does_not_erase_real_holding():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="没有持仓价值了吗？",
        current_position=100,
        current_position_pct=20,
        average_cost=100,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 100
    assert request.current_position_pct == 20
    assert request.average_cost == 100
    assert request.user_intent["position_context"]["has_position"] is True
    assert _resolve_has_position(request.user_intent, request) is True


def test_future_flat_goal_does_not_erase_current_holding_or_exit_intent():
    for query, expected_intent in (
        ("为了不持仓过夜，我该如何减仓", "reduce"),
        ("明天不持仓，今天如何卖出", "reduce"),
        ("目标是不持仓过夜，帮我清仓", "stop_loss"),
        ("希望不再持仓，怎么清仓", "stop_loss"),
        ("目标是明天清仓后持仓为0", "stop_loss"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=100,
            current_position_pct=20,
            average_cost=150,
        )

        intent = _ensure_query_and_user_intent(request, request.symbol)

        assert intent == expected_intent
        assert request.current_position == 100
        assert request.current_position_pct == 20
        assert request.average_cost == 150
        assert request.user_intent["position_context"]["has_position"] is True


def test_present_flat_assertion_wins_after_separate_future_clause():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="明天再做计划，但现在持仓为0",
        current_position=100,
        average_cost=150,
    )

    _ensure_query_and_user_intent(request, request.symbol)

    assert request.current_position == 0
    assert request.average_cost is None
    assert request.user_intent["position_context"]["has_position"] is False


def test_ui_no_position_token_materializes_flat_context():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query=(
            "[分析上下文] 周期=短线｜意图=建仓/入场研究｜持仓=否\n"
            "分析 002409.SZ"
        ),
        objective="建仓/入场研究",
        current_position=None,
        user_intent={"ticker": "002409.SZ", "horizons": ["short"]},
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 0
    assert request.user_intent["analysis_intent"] == "entry"
    assert request.user_intent["position_context"]["has_position"] is False
    assert request.user_intent["user_context"]["current_position"] == 0


def test_refreshed_request_context_preserves_query_position_override():
    from api.main import _extract_request_user_context, _merge_user_context_payload

    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="我现在不持仓，只想观察",
        current_position=100,
        user_intent={
            "analysis_intent": "holding",
            "position_context": {"has_position": True, "shares": 100},
            "user_context": {"current_position": 100},
        },
    )

    _ensure_query_and_user_intent(request, "002409.SZ")
    refreshed = _extract_request_user_context(request)
    merged = _merge_user_context_payload(
        refreshed,
        request.user_intent["user_context"],
    )

    assert merged["current_position"] == 0


def test_hypothetical_or_question_position_does_not_assert_holding():
    for query in (
        "如果有持仓，跌破支撑是否需要止损？",
        "如果我有持仓，应该怎样处理？",
        "是否有持仓风险？",
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=None,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.current_position is None
        assert request.user_intent["position_context"] is None
        assert _resolve_has_position(request.user_intent, request) is None


def test_parser_default_flat_marker_remains_unknown_downstream():
    user_intent = {
        "position_context": {
            "has_position": False,
            "position_status_explicit": False,
        }
    }

    assert _resolve_has_position(user_intent) is None


def test_non_explicit_parser_flat_marker_is_cleared_before_agent_context():
    request = AnalyzeRequest(
        symbol="AAPL",
        query="Analyze AAPL",
        user_intent={
            "ticker": "AAPL",
            "analysis_intent": "watch",
            "position_context": {
                "has_position": False,
                "position_status_explicit": False,
                "avg_cost": None,
                "shares": None,
                "position_pct": None,
                "holding_days": None,
            },
            "user_context": {},
        },
    )

    _ensure_query_and_user_intent(request, "AAPL")

    assert request.user_intent["position_context"] is None
    assert _resolve_has_position(request.user_intent, request) is None


def test_unknown_position_context_falls_back_to_explicit_request_position():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        current_position=100,
        user_intent={
            "position_context": {
                "has_position": False,
                "position_status_explicit": False,
            }
        },
    )

    assert _resolve_has_position(request.user_intent, request) is True


def test_explicit_zero_position_wins_over_hypothetical_holding_wording():
    request = AnalyzeRequest(
        symbol="002409.SZ",
        query="如果未来我已持仓，应该怎样止损？",
        current_position=0,
    )

    _ensure_query_and_user_intent(request, "002409.SZ")

    assert request.current_position == 0
    assert request.user_intent["analysis_intent"] == "watch"
    assert request.user_intent["position_context"]["has_position"] is False


def test_position_value_and_risk_questions_remain_watch_when_position_unknown():
    for query in ("这只股票有持仓价值吗？", "是否有持仓风险？"):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=None,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.current_position is None
        assert request.user_intent["analysis_intent"] == "watch"
        assert request.user_intent["position_context"] is None


def test_compound_negated_actions_do_not_override_requested_observation():
    for query, current_position, expected in (
        ("不建议卖出止盈，继续持有", 100, "holding"),
        ("无需卖出止盈，继续持有", 100, "holding"),
        ("不建议买入建仓，只观察", 0, "watch"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=current_position,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == expected


def test_coordinated_vetoes_and_third_party_trades_do_not_become_user_actions():
    for query, current_position, expected in (
        ("不建议买入或建仓，只观察", 0, "watch"),
        ("不要加仓和补仓，继续持有", 100, "holding"),
        ("分析北向资金买入情况", 0, "watch"),
        ("大股东减仓了吗", 100, "holding"),
    ):
        request = AnalyzeRequest(
            symbol="002409.SZ",
            query=query,
            current_position=current_position,
        )

        _ensure_query_and_user_intent(request, "002409.SZ")

        assert request.user_intent["analysis_intent"] == expected


def test_modified_third_party_purchases_do_not_become_user_actions():
    for query in (
        "北向资金昨天买入贵州茅台，后市如何",
        "大股东昨日以集中竞价方式买入100万股，怎么看",
        "基金经理在回调时买入该股的逻辑是什么",
        "公司回购买入股份对股价有什么影响",
    ):
        request = AnalyzeRequest(symbol="600519.SH", query=query)

        _ensure_query_and_user_intent(request, "600519.SH")

        assert request.user_intent["analysis_intent"] == "watch"
        position_context = request.user_intent.get("position_context")
        assert position_context is None or position_context["has_position"] is False


def test_analyze_loads_saved_context_before_intent_and_profile_selection():
    from api.main import analyze

    source = inspect.getsource(analyze)

    apply_context = source.index("_apply_user_context_to_request")
    ensure_intent = source.index("_ensure_query_and_user_intent")
    recommend = source.index("recommend_profile(")

    assert apply_context < ensure_intent < recommend
    assert source.count("_ensure_query_and_user_intent(") == 1


def test_trader_and_risk_prompts_include_price_snapshot_summary():
    from tradingagents.agents.managers import risk_manager
    from tradingagents.agents.trader import trader

    assert "context_view['price_snapshot_summary']" in inspect.getsource(
        risk_manager.create_risk_manager
    )
    assert "context_view['price_snapshot_summary']" in inspect.getsource(
        trader.create_trader
    )


def test_agent_context_uses_legacy_root_raw_evidence_for_price_summary():
    from tradingagents.agents.utils.context_utils import build_agent_context_view

    state = {
        "company_of_interest": "002409.SZ",
        "trade_date": "2026-07-29",
        "instrument_context": {"symbol": "002409.SZ"},
        "metadata": {},
        "raw_evidence": {
            "realtime_quote": {
                "status": "HAS_DATA",
                "raw": {
                    "002409.SZ": {
                        "price": 154.6,
                        "quote_time": "2026-07-29 14:20:03",
                    }
                },
            }
        },
    }

    view = build_agent_context_view(state, "risk")

    assert "154.6" in view["price_snapshot_summary"]


def test_separated_multi_instrument_contexts_fail_closed():
    from api.main import _query_has_multiple_explicit_instruments

    for query in (
        "I hold AAPL, I want to buy MSFT",
        "I hold AAPL and want to buy MSFT",
        "我持有AAPL，同时想买MSFT",
        "AAPL涨了，MSFT跌了，怎么办",
    ):
        assert _query_has_multiple_explicit_instruments(query) is True

    assert _query_has_multiple_explicit_instruments(
        "analyze AAPL and outlook"
    ) is False


def test_cn_company_names_across_sentence_boundaries_fail_closed(monkeypatch):
    import api.main as main_module

    stock_map = {"贵州茅台": "600519.SH", "宁德时代": "300750.SZ"}
    monkeypatch.setattr(main_module, "_load_cn_stock_map", lambda: stock_map)

    for query in (
        "我持有贵州茅台。想买宁德时代",
        "贵州茅台涨了；宁德时代跌了，怎么办",
        "我持有贵州茅台！然后关注宁德时代",
    ):
        assert main_module._query_has_multiple_explicit_instruments(query) is True


def test_relative_today_uses_instrument_market_date(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "market_today_str", lambda _symbol: "2026-08-03")

    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-04",
        parsed_query_date="2026-08-04",
        trade_date_was_explicit=False,
        query_text="Analyze AAPL today",
    ) == "2026-08-03"

    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-04",
        parsed_query_date="2026-08-04",
        trade_date_was_explicit=False,
        query_text="分析 AAPL 今日走势",
    ) == "2026-08-03"


def test_relative_historical_dates_use_instrument_market_date(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "market_today_str", lambda _symbol: "2026-08-10")

    for query, expected in (
        ("分析 AAPL 昨天走势", "2026-08-09"),
        ("分析 AAPL 前天走势", "2026-08-08"),
        ("分析 AAPL 大前天走势", "2026-08-07"),
        ("Analyze AAPL yesterday", "2026-08-09"),
        ("Analyze AAPL day before yesterday", "2026-08-08"),
    ):
        assert main_module._resolve_analysis_trade_date(
            symbol="AAPL",
            current_trade_date="2026-08-10",
            parsed_query_date=None,
            trade_date_was_explicit=False,
            query_text=query,
        ) == expected


def test_previous_trading_day_phrases_use_instrument_calendar(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "market_today_str", lambda _symbol: "2026-08-10")
    monkeypatch.setattr(
        "tradingagents.dataflows.trade_calendar.previous_cn_trading_day",
        lambda _date: "2026-08-07",
    )

    for query in (
        "分析600519.SH上个交易日走势",
        "分析600519.SH上一交易日走势",
        "分析600519.SH最近一个交易日走势",
    ):
        assert main_module._resolve_analysis_trade_date(
            symbol="600519.SH",
            current_trade_date="2026-08-10",
            parsed_query_date=None,
            trade_date_was_explicit=False,
            query_text=query,
        ) == "2026-08-07"

    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-10",
        parsed_query_date=None,
        trade_date_was_explicit=False,
        query_text="Analyze AAPL as of the previous trading day",
    ) == "2026-08-07"


def test_kline_preserves_extended_us_tickers(monkeypatch):
    import api.main as main_module

    captured_symbols = []

    def _route(_method, symbol, _start, _end):
        captured_symbols.append(symbol)
        return "Date,Open,High,Low,Close,Volume\n2026-08-08,1,2,0.5,1.5,100"

    monkeypatch.setattr(main_module, "route_to_vendor", _route)
    monkeypatch.setattr(main_module, "set_config", lambda _config: None)
    monkeypatch.setattr(main_module, "_build_runtime_config", lambda _overrides: {})

    for symbol in ("BRK-B", "ABC1", "LONGTICK"):
        response = main_module.get_kline(
            symbol=symbol,
            start_date="2026-08-01",
            end_date="2026-08-08",
        )
        assert response.symbol == symbol

    assert captured_symbols == ["BRK-B", "ABC1", "LONGTICK"]


def test_scoped_analysis_date_wins_over_transaction_date(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "market_today_str", lambda _symbol: "2026-08-10")

    assert main_module._resolve_analysis_trade_date(
        symbol="AAPL",
        current_trade_date="2026-08-10",
        parsed_query_date="2024-01-01",
        trade_date_was_explicit=False,
        query_text="I bought AAPL on 2024-01-01; analyze as of 2025-01-01",
    ) == "2025-01-01"


def test_sell_all_word_orders_route_to_full_exit():
    for query in ("卖出全部持仓", "卖掉全部持仓", "全部持仓卖出"):
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=100,
        )

        assert _ensure_query_and_user_intent(request, request.symbol) == "stop_loss"
        assert request.user_intent["analysis_intent"] == "stop_loss"


def test_explicit_partial_daily_row_stays_non_executable_after_close():
    raw = {
        "stock_data": {
            "status": "HAS_DATA",
            "current_day_status": "PARTIAL_INTRADAY",
            "raw": (
                "Date,Open,High,Low,Close,Volume\n"
                "2026-07-28,10,11,9,10,100\n"
                "2026-07-29,10,12,9,11,200\n"
            ),
        }
    }

    snapshot = extract_price_snapshot(
        raw,
        "002409.SZ",
        "2026-07-29",
        now=datetime(2026, 7, 29, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot["completed_bar"]["date"] == "2026-07-28"
    assert snapshot["completed_bar"]["close"] == 10.0


def test_parenthesized_name_code_pairs_are_cross_checked(monkeypatch):
    import api.main as main_module

    stock_map = {"贵州茅台": "600519.SH", "雅克科技": "002409.SZ"}
    monkeypatch.setattr(main_module, "_load_cn_stock_map", lambda: stock_map)
    monkeypatch.setattr(main_module, "_get_cn_stock_map_cached_only", lambda: stock_map)

    assert main_module._resolve_query_symbol_and_date("贵州茅台（002409）")[0] is None
    assert main_module._resolve_query_symbol_and_date("贵州茅台（600519）")[0] == "600519.SH"


def test_bare_entry_plan_stop_values_do_not_become_exit_requests():
    for query in (
        "建仓计划：30元买入，止损25元",
        "600519入场止损设25元",
        "600519止损25元能买吗",
    ):
        request = AnalyzeRequest(symbol="600519.SH", query=query)

        assert _ensure_query_and_user_intent(request, request.symbol) == "entry"
        assert request.user_intent["analysis_intent"] == "entry"
        context = _infer_position_context(query, {})
        assert context["has_position"] is False
        assert context["position_status_explicit"] is False


def test_completed_full_exit_overrides_stale_imported_position():
    from tradingagents.graph.intent_parser import _infer_analysis_intent

    for query in (
        "我刚刚卖出全部持仓，后市怎么看",
        "我已清仓",
        "昨天全部卖出离场了",
    ):
        context = _infer_position_context(
            query,
            {"current_position": 100, "average_cost": 30},
        )
        assert context["has_position"] is False
        assert context["position_status_explicit"] is True
        assert context["shares"] is None
        assert context["avg_cost"] is None
        assert _infer_analysis_intent(query) == ("watch", "short")

        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=100,
            average_cost=30,
        )
        assert _ensure_query_and_user_intent(request, request.symbol) == "watch"
        assert request.current_position == 0
        assert request.average_cost is None
        assert request.user_intent["position_context"]["has_position"] is False


def test_completed_full_exit_followed_by_entry_question_stays_flat():
    from tradingagents.graph.intent_parser import _infer_analysis_intent

    query = "昨天卖出全部持仓，今天能买吗"
    context = _infer_position_context(query, {"current_position": 100})
    assert context["has_position"] is False
    assert context["position_status_explicit"] is True
    assert _infer_analysis_intent(query) == ("entry", "short")

    request = AnalyzeRequest(
        symbol="600519.SH",
        query=query,
        current_position=100,
        average_cost=30,
    )
    assert _ensure_query_and_user_intent(request, request.symbol) == "entry"
    assert request.current_position == 0
    assert request.average_cost is None


def test_english_completed_full_exit_overrides_stale_imported_position():
    from tradingagents.graph.intent_parser import _infer_analysis_intent

    for query in (
        "I sold all my shares yesterday. Should I buy AAPL?",
        "I sold AAPL yesterday and no longer hold it. Should I buy again?",
        "I already sold all AAPL. Should I re-enter?",
    ):
        request = AnalyzeRequest(
            symbol="AAPL",
            query=query,
            current_position=100,
            average_cost=190,
        )

        assert _ensure_query_and_user_intent(request, request.symbol) == "entry"
        assert request.current_position == 0
        assert request.average_cost is None
        assert request.user_intent["position_context"]["has_position"] is False
        assert _infer_analysis_intent(query) == ("entry", "short")


def test_current_day_buy_questions_do_not_create_a_position():
    for query in ("今天买点在哪", "今天买多少合适", "今天能买吗"):
        context = _infer_position_context(query, {})
        assert context["has_position"] is False
        assert context["position_status_explicit"] is False

    completed = _infer_position_context("今天已买入100股", {})
    assert completed["has_position"] is True
    assert completed["position_status_explicit"] is True


def test_final_position_resolution_keeps_percentage_only_holding():
    import api.main as main_module

    request = AnalyzeRequest(
        symbol="600519.SH",
        query="持仓风险如何",
        current_position_pct=35,
    )
    final_state = {
        "user_context": {"current_position_pct": 35},
        "position_context": {
            "has_position": True,
            "position_status_explicit": True,
        },
    }

    assert main_module._resolve_final_has_position(final_state, request) is True


def test_unsuccessful_trades_and_later_holdings_preserve_current_position():
    cases = (
        ("昨天买入失败，今天能买吗", None, "entry", False),
        ("昨天买入委托未成交，今天再买吗", None, "entry", False),
        ("昨天清仓失败，现在仍持有100股", 100, "holding", True),
        ("昨天清仓，今天又买入100股", None, "holding", True),
    )
    for query, current_position, expected_intent, has_position in cases:
        request = AnalyzeRequest(
            symbol="600519.SH",
            query=query,
            current_position=current_position,
        )

        intent = _ensure_query_and_user_intent(request, request.symbol)

        assert intent == expected_intent
        position_context = request.user_intent["position_context"]
        if has_position:
            assert position_context["has_position"] is True
        else:
            assert position_context is None or position_context["has_position"] is False
        if current_position:
            assert request.current_position == current_position


def test_failed_full_exit_keeps_position_risk_routing():
    query = "我昨天全部卖出未成交，现在怎么办"
    request = AnalyzeRequest(
        symbol="600519.SH",
        query=query,
        current_position=100,
        average_cost=30,
    )

    assert _ensure_query_and_user_intent(request, request.symbol) == "holding"
    assert request.current_position == 100
    assert request.average_cost == 30
    assert request.user_intent["position_context"]["has_position"] is True


def test_labeled_six_digit_balances_do_not_override_analysis_symbol(monkeypatch):
    import api.main as main_module

    stock_map = {"贵州茅台": "600519.SH"}
    monkeypatch.setattr(main_module, "_load_cn_stock_map", lambda: stock_map)
    monkeypatch.setattr(
        main_module,
        "_get_cn_stock_map_cached_only",
        lambda: stock_map,
    )

    for query in (
        "可用资金是100000，分析贵州茅台",
        "预算为100000，分析贵州茅台",
        "本金约100000，分析贵州茅台",
        "当前持仓100000，分析600519.SH",
    ):
        assert main_module._resolve_query_symbol_and_date(query)[0] == "600519.SH"
        assert main_module._query_has_multiple_explicit_instruments(query) is False


def test_english_six_digit_amounts_are_not_promoted_to_a_share_symbols():
    import api.main as main_module

    cases = {
        "Analyze AAPL with a $100000 budget": "AAPL",
        "I own 100000 shares of AAPL": "AAPL",
        "cash 100000, buy AAPL": "AAPL",
    }
    for query, expected in cases.items():
        assert main_module._query_contains_instrument_code(query) is False
        assert main_module._query_has_multiple_explicit_instruments(query) is False
        assert main_module._resolve_query_symbol_and_date(query)[0] == expected


def test_english_command_words_do_not_become_ticker_candidates():
    import api.main as main_module

    cases = {
        "analyze AAPL and give risk": "AAPL",
        "I bought AAPL, analyze today": "AAPL",
    }
    for query, expected in cases.items():
        assert main_module._query_has_multiple_explicit_instruments(query) is False
        assert main_module._resolve_query_symbol_and_date(query)[0] == expected


def test_ticker_date_shorthand_resolves_symbol_and_date():
    import api.main as main_module

    assert main_module._extract_symbol_and_date("AAPL 2026-08-01") == (
        "AAPL",
        "2026-08-01",
    )


def test_insider_actions_do_not_create_user_intent_or_position():
    from tradingagents.graph.intent_parser import _infer_analysis_intent

    for query in (
        "高管减仓会影响股价吗",
        "董事长刚刚减仓，分析影响",
        "员工持股计划减仓风险",
        "董监高买入信号如何",
    ):
        assert _infer_analysis_intent(query) == ("watch", "short")
        context = _infer_position_context(query, {})
        assert context["has_position"] is False
        assert context["position_status_explicit"] is False


def test_comparison_nouns_are_not_promoted_to_tickers():
    import api.main as main_module

    for query in (
        "compare AAPL with peers",
        "compare AAPL with sector",
        "compare AAPL with industry",
        "compare AAPL with benchmark",
    ):
        assert main_module._query_has_multiple_explicit_instruments(query) is False


def test_invalid_localized_analysis_dates_remain_rejectable(monkeypatch):
    import api.main as main_module

    monkeypatch.setattr(main_module, "market_today_str", lambda _symbol: "2026-08-11")
    for query in (
        "分析600519截至2026年2月30日",
        "分析600519截至2026/02/30",
        "分析600519截至2026-02-30",
    ):
        _, parsed_date = main_module._resolve_query_symbol_and_date(query)
        resolved = main_module._resolve_analysis_trade_date(
            symbol="600519.SH",
            current_trade_date="2026-08-11",
            parsed_query_date=parsed_date,
            trade_date_was_explicit=False,
            query_text=query,
        )
        assert main_module._is_valid_analysis_trade_date(resolved) is False


def test_prefixed_six_digit_cash_amount_does_not_block_company_name_resolution(monkeypatch):
    import api.main as main_module

    stock_map = {"贵州茅台": "600519.SH"}
    monkeypatch.setattr(main_module, "_load_cn_stock_map", lambda: stock_map)
    monkeypatch.setattr(main_module, "_get_cn_stock_map_cached_only", lambda: stock_map)

    for query in (
        "分析贵州茅台，预算100000",
        "分析贵州茅台，现金100000",
        "分析贵州茅台，可用资金100000",
    ):
        assert main_module._query_contains_instrument_code(query) is False
        assert main_module._resolve_query_symbol_and_date(query)[0] == "600519.SH"


def test_six_digit_amounts_are_not_name_code_pairs():
    import api.main as main_module

    for query in (
        "预算100000元",
        "持仓100000股",
        "可用资金100000，分析风险",
    ):
        assert main_module._query_contains_instrument_code(query) is False
        assert main_module._query_has_explicit_cn_name_code_pair(query) is False
