"""Regression tests for selectively absorbed upstream v0.8.1 fixes."""

from __future__ import annotations

import pandas as pd
import requests
import time

from tradingagents.dataflows.network_timeout import install_default_network_timeout
from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
from tradingagents.dataflows import interface as dataflow_interface
from tradingagents.dataflows.interface import _is_failure_result, route_to_vendor
from tradingagents.dataflows.source_catalog import DataType, get_sources_for_type
from tradingagents.graph import data_collector as data_collector_module
from tradingagents.graph.data_collector import DataCollector
from tradingagents.graph.signal_processing import (
    WAIT_REASON_ACTION_NOT_APPLICABLE,
    WAIT_REASON_NO_TRIGGER,
    WAIT_REASON_RISK_FIRST,
    _classify_research_direction,
    _extract_decision_keyword,
    _extract_decision_semantics,
)


class _FakeAkshare:
    def __init__(self) -> None:
        self.realtime_kwargs: dict = {}
        self.board_symbol: str | None = None
        self.lhb_kwargs: dict = {}
        self.lhb_error: Exception | None = None

    def stock_individual_spot_xq(self, **kwargs):
        self.realtime_kwargs = kwargs
        return pd.DataFrame(
            {
                "item": ["时间", "今开", "最高", "最低", "现价", "成交量"],
                "value": ["2026-07-27", 10.0, 10.5, 9.8, 10.2, 1000],
            }
        )

    def stock_fund_flow_industry(self, *, symbol: str):
        self.board_symbol = symbol
        return pd.DataFrame(
            {
                "行业": ["半导体", "化工"],
                "净额": ["1.2亿", "9000万"],
            }
        )

    def stock_lhb_detail_em(self, **kwargs):
        self.lhb_kwargs = kwargs
        if self.lhb_error is not None:
            raise self.lhb_error
        return pd.DataFrame(
            {
                "代码": ["603629", "600519"],
                "名称": ["利通电子", "贵州茅台"],
                "净买额": [100, 200],
            }
        )

    def stock_main_stock_holder(self, *, stock: str):
        raise RuntimeError("primary unavailable")


def _provider_with(fake_akshare: _FakeAkshare) -> CnAkshareProvider:
    provider = CnAkshareProvider()
    provider._ak = lambda: fake_akshare
    return provider


def test_xueqiu_realtime_forwards_configured_token(monkeypatch):
    fake = _FakeAkshare()
    monkeypatch.setenv("XQ_A_TOKEN", "test-token")

    result = _provider_with(fake)._fetch_realtime_row_unlocked("603629.SH")

    assert not result.empty
    assert fake.realtime_kwargs == {"symbol": "SH603629", "token": "test-token"}


def test_board_fund_flow_uses_installed_akshare_api():
    fake = _FakeAkshare()

    result = _provider_with(fake).get_board_fund_flow()

    assert fake.board_symbol == "即时"
    assert "半导体" in result
    assert "fallback" not in result
    assert result.index("半导体") < result.index("化工")


def test_board_fund_flow_catalog_matches_current_akshare_contract():
    primary = next(
        source
        for source in get_sources_for_type(DataType.BOARD_FUND_FLOW)
        if source.vendor == "cn_akshare" and source.is_primary
    )

    assert primary.endpoint == "stock_fund_flow_industry"
    assert primary.unit == "亿元"
    assert primary.fields == ["行业", "行业指数", "流入资金", "流出资金", "净额"]


def test_lhb_fetches_market_by_compact_date_and_filters_symbol():
    fake = _FakeAkshare()

    result = _provider_with(fake).get_lhb_detail(
        "603629.SH",
        "2026-07-27",
        force=True,
    )

    assert fake.lhb_kwargs == {
        "start_date": "20260727",
        "end_date": "20260727",
    }
    assert "LHB_HAS_DATA" in result
    assert "利通电子" in result
    assert "贵州茅台" not in result


def test_lhb_non_matching_symbol_is_normal_no_data():
    fake = _FakeAkshare()

    result = _provider_with(fake).get_lhb_detail(
        "002138.SZ",
        "2026-07-27",
        force=True,
    )

    assert "LHB_NORMAL_NO_DATA" in result


def test_lhb_type_error_fails_closed():
    fake = _FakeAkshare()
    fake.lhb_error = TypeError("'NoneType' object is not subscriptable")

    result = _provider_with(fake).get_lhb_detail(
        "603629.SH",
        "2026-07-27",
        force=True,
    )

    assert "LHB_FAILED" in result
    assert "TypeError" in result


def test_lhb_failed_marker_triggers_provider_fallback():
    assert _is_failure_result(
        "603629.SH [G-007] LHB_FAILED: 龙虎榜返回缺少证券代码列"
    )


def test_lhb_final_structured_failure_survives_exhausted_fallbacks(monkeypatch):
    structured_failure = (
        "603629.SH [G-007] LHB_FAILED: 龙虎榜返回缺少证券代码列"
    )

    class _StructuredFailureProvider:
        def get_lhb_detail(self, *args, **kwargs):
            return structured_failure

    class _ExceptionProvider:
        def get_lhb_detail(self, *args, **kwargs):
            raise RuntimeError("secondary provider unavailable")

    class _Registry:
        providers = {
            "structured": _StructuredFailureProvider(),
            "exception": _ExceptionProvider(),
        }

        def get(self, name):
            return self.providers.get(name)

        def list_names(self):
            return list(self.providers)

    monkeypatch.setattr(dataflow_interface, "_registry", _Registry())
    monkeypatch.setattr(
        dataflow_interface,
        "get_vendor",
        lambda category, method=None: "structured",
    )
    monkeypatch.setattr(
        dataflow_interface,
        "_resolve_vendor_chain",
        lambda method, configured_vendor: ["structured", "exception"],
    )

    result = route_to_vendor(
        "get_lhb_detail",
        "603629.SH",
        "2026-07-27",
        force=True,
    )

    assert result == structured_failure


def test_insider_news_fallback_uses_analysis_date():
    fake = _FakeAkshare()
    provider = _provider_with(fake)
    captured: dict[str, str] = {}

    def fake_news(symbol: str, start_date: str, end_date: str) -> str:
        captured.update(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
        return "fallback news"

    provider.get_news = fake_news

    result = provider.get_insider_transactions("603629.SH", "2026-07-14")

    assert captured == {
        "symbol": "603629.SH",
        "start_date": "2026-06-30",
        "end_date": "2026-07-14",
    }
    assert "fallback news" in result


def test_negated_buy_phrase_is_not_buy():
    assert (
        _extract_decision_keyword("最终建议：不建议买入，维持观望")
        == "HOLD"
    )


def test_long_negated_buy_phrase_is_not_buy():
    text = "最终建议：不建议在当前价格直接追高买入，继续观望"

    assert _extract_decision_keyword(text) == "HOLD"
    assert _classify_research_direction(text) == "中性"


def test_very_long_negated_buy_clause_is_not_buy():
    text = (
        "最终建议：不建议在公司三季报披露、资金面改善以及行业催化"
        "兑现之前直接追高买入，继续观望"
    )

    assert _extract_decision_keyword(text) == "HOLD"
    assert _classify_research_direction(text) == "中性"


def test_negated_entry_scope_survives_chinese_list_conditions():
    for text in (
        "最终建议：不建议在业绩披露、资金改善前建仓",
        "最终建议：不建议在业绩披露、资金改善前条件建仓",
        "最终建议：禁止在业绩披露、资金改善前买入",
    ):
        assert _extract_decision_keyword(text) in {"HOLD", "UNKNOWN"}
        assert _classify_research_direction(text) in {None, "中性"}


def test_negated_buy_only_wording_maps_to_hold():
    assert _extract_decision_keyword("最终建议：不建议买入") == "HOLD"
    assert _extract_decision_keyword("最终建议：暂不建仓") == "HOLD"


def test_negated_sell_only_wording_maps_to_hold():
    assert _extract_decision_keyword("最终建议：不建议卖出") == "HOLD"
    assert _extract_decision_keyword("最终建议：不宜减持") == "HOLD"


def test_explicit_sell_action_overrides_bullish_context():
    text = "最终建议：长期逻辑看多，但当前建议减持"

    assert _extract_decision_keyword(text) == "SELL"
    assert _classify_research_direction(text) == "偏多"


def test_structured_semantics_preserve_bullish_research_and_reduce_position():
    text = "最终建议：长期逻辑看多，但当前建议减持"

    result = _extract_decision_semantics(text, has_position=True)

    assert result.research_direction == "偏多"
    assert result.execution_action == "REDUCE"
    assert result.action_label == "条件减仓"
    assert result.decision == "SELL"


def test_structured_semantics_distinguish_exit_and_no_position():
    exit_result = _extract_decision_semantics(
        "最终建议：长期逻辑看多，但当前建议清仓",
        has_position=True,
    )
    no_position_result = _extract_decision_semantics(
        "最终建议：长期逻辑看多，但当前建议减持",
        has_position=False,
    )

    assert exit_result.research_direction == "偏多"
    assert exit_result.execution_action == "EXIT"
    assert exit_result.action_label == "条件清仓"
    assert exit_result.decision == "SELL"
    assert no_position_result.research_direction == "偏多"
    assert no_position_result.execution_action == "WAIT"
    assert no_position_result.action_label == "观望"
    assert no_position_result.decision == "HOLD"
    assert WAIT_REASON_ACTION_NOT_APPLICABLE in no_position_result.wait_reason_codes
    assert WAIT_REASON_NO_TRIGGER not in no_position_result.wait_reason_codes


def test_explicit_empty_position_instruction_cannot_be_inverted_by_bullish_context():
    text = "最终建议：长期逻辑看多，但当前建议空仓"

    held = _extract_decision_semantics(text, has_position=True)
    no_position = _extract_decision_semantics(text, has_position=False)

    assert held.research_direction == "偏多"
    assert held.execution_action == "EXIT"
    assert held.action_label == "条件清仓"
    assert held.decision == "SELL"
    assert no_position.research_direction == "偏多"
    assert no_position.execution_action == "WAIT"
    assert no_position.action_label == "观望"
    assert no_position.decision == "HOLD"
    assert WAIT_REASON_ACTION_NOT_APPLICABLE in no_position.wait_reason_codes
    assert WAIT_REASON_NO_TRIGGER not in no_position.wait_reason_codes


def test_reduce_synonyms_keep_sell_direction_and_risk_first_semantics():
    for recommendation in ("减仓", "继续减仓", "维持减仓"):
        held = _extract_decision_semantics(
            f"最终建议：{recommendation}",
            has_position=True,
        )
        no_position = _extract_decision_semantics(
            f"最终建议：{recommendation}",
            has_position=False,
        )

        assert held.research_direction == "偏空"
        assert held.execution_action == "REDUCE"
        assert held.decision == "SELL"
        assert no_position.research_direction == "偏空"
        assert no_position.execution_action == "WAIT"
        assert no_position.action_label == "回避"
        assert no_position.decision == "HOLD"
        assert "RISK_FIRST" in no_position.wait_reason_codes
        assert WAIT_REASON_ACTION_NOT_APPLICABLE in no_position.wait_reason_codes


def test_explicit_sell_action_overrides_bullish_verdict_in_legacy_path():
    text = (
        '<!-- VERDICT: {"direction": "偏多"} -->\n'
        "最终建议：长期逻辑看多，但当前建议减持"
    )

    assert _extract_decision_keyword(text, has_position=True) == "SELL"
    assert _extract_decision_keyword(text, has_position=False) == "HOLD"


def test_stop_loss_exit_stays_consistent_across_decision_paths():
    text = (
        '<!-- VERDICT: {"direction": "偏多"} -->\n'
        "最终建议：长期逻辑看多，但当前建议止损离场"
    )

    assert _extract_decision_keyword(text, has_position=True) == "SELL"
    result = _extract_decision_semantics(text, has_position=True)
    assert result.research_direction == "偏多"
    assert result.execution_action == "EXIT"
    assert result.decision == "SELL"


def test_explicit_sell_with_unknown_position_keeps_legacy_sell_decision():
    for text in (
        "最终建议：清仓",
        "最终建议：减持",
        "最终建议：长期逻辑看多，但当前建议清仓",
    ):
        result = _extract_decision_semantics(text)
        assert result.execution_action == "WAIT"
        assert result.decision == "SELL"


def test_negated_buy_overrides_bullish_verdict_in_legacy_path():
    text = (
        '<!-- VERDICT: {"direction": "偏多"} -->\n'
        "最终建议：当前价格偏高，不建议买入"
    )

    assert _extract_decision_keyword(text, has_position=True) == "HOLD"
    assert _extract_decision_keyword(text, has_position=False) == "HOLD"


def test_negated_buy_overrides_bullish_verdict_in_structured_semantics():
    text = (
        '<!-- VERDICT: {"direction": "偏多"} -->\n'
        "最终建议：当前价格偏高，不建议买入"
    )

    result = _extract_decision_semantics(
        text,
        has_position=False,
        trigger_price=10.0,
    )

    assert result.research_direction == "偏多"
    assert result.execution_action == "WAIT"
    assert result.action_label == "观望"
    assert result.decision == "HOLD"


def test_negated_sell_overrides_bearish_verdict_in_structured_semantics():
    for recommendation in ("不宜减持", "不建议卖出"):
        result = _extract_decision_semantics(
            '<!-- VERDICT: {"direction": "偏空"} -->\n'
            f"最终建议：{recommendation}",
            has_position=True,
        )

        assert result.research_direction == "偏空"
        assert result.execution_action == "HOLD"
        assert result.action_label == "持有"
        assert result.decision == "HOLD"


def test_contrastive_clause_preserves_bullish_research_direction():
    text = "最终建议：不建议买入但仍看多"

    assert _extract_decision_keyword(text) == "HOLD"
    assert _classify_research_direction(text) == "偏多"
    assert _classify_research_direction("回避短期风险不过谨慎看多") == "偏多"


def test_later_direction_does_not_override_final_no_buy_advice():
    text = (
        "最终建议：当前价格偏高，不建议买入\n"
        "后续方向：回调后条件建仓需要验证"
    )

    no_position = _extract_decision_semantics(text, has_position=False)
    has_position = _extract_decision_semantics(text, has_position=True)

    assert no_position.research_direction == "偏多"
    assert no_position.execution_action == "WAIT"
    assert no_position.action_label == "观望"
    assert no_position.decision == "HOLD"
    assert has_position.execution_action == "HOLD"
    assert has_position.action_label == "持有"
    assert has_position.decision == "HOLD"


def test_explicit_avoidance_overrides_bullish_risk_caveat():
    text = "最终建议：看多风险较大，建议回避"

    no_position = _extract_decision_semantics(text, has_position=False)
    has_position = _extract_decision_semantics(text, has_position=True)

    assert _extract_decision_keyword(text, has_position=True) == "SELL"
    assert no_position.research_direction == "偏空"
    assert no_position.execution_action == "WAIT"
    assert no_position.action_label == "回避"
    assert no_position.decision == "SELL"
    assert WAIT_REASON_RISK_FIRST in no_position.wait_reason_codes
    assert WAIT_REASON_NO_TRIGGER not in no_position.wait_reason_codes
    assert has_position.execution_action == "REDUCE"
    assert has_position.decision == "SELL"


def test_avoiding_short_term_risk_does_not_erase_bullish_direction():
    text = "最终建议：回避短期风险、谨慎看多"

    assert _extract_decision_keyword(text) == "BUY"
    assert _extract_decision_semantics(text, has_position=False).research_direction == "偏多"


def test_rejected_bullish_thesis_does_not_override_avoidance():
    for text in (
        "最终建议：看多观点不成立，建议回避",
        "最终建议：看多逻辑证伪，建议回避",
        "最终建议：看多条件不成立，回避",
    ):
        assert _extract_decision_keyword(text) == "SELL"
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "偏空"
        assert result.execution_action == "WAIT"
        assert result.decision == "SELL"


def test_rejected_bullish_thesis_can_resolve_to_neutral():
    text = "最终建议：偏多逻辑已失效，继续观望"

    assert _extract_decision_keyword(text) == "HOLD"
    result = _extract_decision_semantics(text, has_position=False)
    assert result.research_direction == "中性"
    assert result.execution_action == "WAIT"
    assert result.decision == "HOLD"


def test_conditional_entry_overrides_earlier_anti_chase_caveat():
    text = "最终建议：不建议追高买入，回调后条件建仓"

    assert _extract_decision_keyword(text) == "BUY"
    assert _classify_research_direction(text) == "偏多"


def test_negated_build_position_with_avoidance_is_sell():
    assert (
        _extract_decision_keyword("核心定性：风险偏高，回避追高，暂不建仓")
        == "SELL"
    )


def test_bullish_direction_survives_unrelated_risk_caveat():
    text = "方向：看多，谨慎看多为主，但需回避短期回调风险"
    assert _extract_decision_keyword(text) == "BUY"
    assert _classify_research_direction(text) == "偏多"


def test_chinese_list_comma_breaks_avoidance_scope():
    assert _extract_decision_keyword("最终建议：回避短期风险、谨慎看多") == "BUY"
    assert (
        _extract_decision_keyword("最终建议：回避追高、回调后条件建仓")
        == "BUY"
    )
    assert (
        _classify_research_direction("最终建议：回避追高、回调后条件建仓")
        == "偏多"
    )
    for prefix in ("禁止追高", "切勿追高"):
        text = f"最终建议：{prefix}、回调后条件建仓"
        assert _extract_decision_keyword(text) == "BUY"
        assert _classify_research_direction(text) == "偏多"


def test_sell_pressure_is_not_an_exit_instruction():
    text = "最终建议：卖出压力减轻，谨慎看多"

    assert _extract_decision_keyword(text) == "BUY"
    result = _extract_decision_semantics(text, has_position=True)
    assert result.research_direction == "偏多"
    assert result.execution_action == "HOLD"
    assert result.decision == "BUY"


def test_ascii_action_substrings_do_not_override_structured_verdict():
    for recommendation in (
        "buyback公告利好，继续观望",
        "sell-side rating 下调，但继续观望",
    ):
        text = (
            '<!-- VERDICT: {"direction": "中性"} -->\n'
            f"最终建议：{recommendation}"
        )

        assert _extract_decision_keyword(text, has_position=True) == "HOLD"
        result = _extract_decision_semantics(text, has_position=False)
        assert result.research_direction == "中性"
        assert result.execution_action == "WAIT"
        assert result.decision == "HOLD"


def test_standalone_ascii_action_tokens_remain_executable():
    for token, expected in (("BUY", "BUY"), ("SELL", "SELL")):
        text = (
            '<!-- VERDICT: {"direction": "中性"} -->\n'
            f"最终建议：{token}"
        )
        assert _extract_decision_keyword(text, has_position=True) == expected


def test_buy_price_and_pressure_are_not_entry_instructions():
    for text in (
        "最终建议：当前买入价格偏高，建议观望",
        "最终建议：买入压力不足，继续观望",
        "最终建议：不建议买入，但买入价格需低于10元",
    ):
        assert _extract_decision_keyword(text) == "HOLD"
        assert _extract_decision_semantics(text).research_direction == "中性"


def test_buy_action_allows_short_timing_context():
    assert _extract_decision_keyword("最终建议：可以在回调后买入") == "BUY"
    assert _extract_decision_keyword("最终建议：应该买入") == "BUY"
    assert _extract_decision_keyword("最终建议：反弹后减持") == "SELL"


def test_sector_names_with_bare_negation_characters_remain_bullish():
    assert _extract_decision_keyword("方向：非银金融板块看多") == "BUY"
    assert _extract_decision_keyword("方向：不锈钢板块偏多") == "BUY"


def test_collect_lock_wait_is_bounded(monkeypatch):
    collector = DataCollector()
    key = "603629.SH_2026-07-27"
    lock = collector._get_key_lock(key)
    lock.acquire()
    monkeypatch.setattr(data_collector_module, "FETCH_LOCK_TIMEOUT", 0.02)

    try:
        started = time.monotonic()
        try:
            collector.collect("603629.SH", "2026-07-27")
        except TimeoutError as exc:
            assert "数据抓取锁超时" in str(exc)
        else:
            raise AssertionError("collect() should fail closed on a stuck key lock")
        assert time.monotonic() - started < 0.15
    finally:
        lock.release()


def test_requests_default_timeout_is_enforced_and_explicit_timeout_wins(
    monkeypatch,
):
    captured: list[float | tuple | None] = []

    def fake_send(self, request, **kwargs):
        captured.append(kwargs.get("timeout"))
        return object()

    monkeypatch.setattr(requests.sessions.Session, "send", fake_send)
    install_default_network_timeout(12.5)

    session = requests.Session()
    session.get("https://example.invalid/default")
    session.get("https://example.invalid/explicit", timeout=3)

    assert captured == [12.5, 3]
