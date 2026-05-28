"""Tests for TradeFlow event_source module."""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from tradingagents.tradeflow.event_source import (
    EventItem,
    fetch_notice_events,
    fetch_buyback_events,
    fetch_rating_events,
    fetch_daily_events,
    fetch_events_for_symbol,
    _normalize_symbol,
    _notice_direction,
    _rating_direction,
    _date_str,
)


# ── Unit tests: direction mapping ──


class TestDirectionMapping:
    def test_notice_direction_bearish(self):
        assert _notice_direction("风险提示") == "bearish"

    def test_notice_direction_bullish(self):
        for t in ["重大事项", "资产重组", "融资公告", "持股变动"]:
            assert _notice_direction(t) == "bullish", f"expected bullish for {t}"

    def test_notice_direction_neutral(self):
        for t in ["财务报告", "信息变更", "其他"]:
            assert _notice_direction(t) == "neutral", f"expected neutral for {t}"

    def test_rating_direction_bullish(self):
        for kw in ["买入", "增持", "推荐", "强烈推荐", "优于大势"]:
            assert _rating_direction(kw) == "bullish", f"expected bullish for {kw}"

    def test_rating_direction_bearish(self):
        for kw in ["卖出", "减持", "回避"]:
            assert _rating_direction(kw) == "bearish", f"expected bearish for {kw}"

    def test_rating_direction_neutral(self):
        for kw in ["中性", "持有", "观望", "同步大市"]:
            assert _rating_direction(kw) == "neutral", f"expected neutral for {kw}"


# ── Unit tests: helpers ──


class TestHelpers:
    def test_normalize_symbol_plain(self):
        assert _normalize_symbol("002138") == "002138"

    def test_normalize_symbol_with_suffix(self):
        assert _normalize_symbol("002138.SZ") == "002138"

    def test_date_str_8digit(self):
        assert _date_str("20260528") == "2026-05-28"

    def test_date_str_already_formatted(self):
        assert _date_str("2026-05-28") == "2026-05-28"


# ── Mocked integration tests ──


def _mock_notice_df():
    return pd.DataFrame({
        "代码": ["002138", "600519", "000001"],
        "名称": ["顺络电子", "贵州茅台", "平安银行"],
        "公告标题": [
            "顺络电子:关于回购公司股份的进展公告",
            "贵州茅台:2025年度财务报告",
            "平安银行:关于风险提示的公告",
        ],
        "公告类型": ["重大事项", "财务报告", "风险提示"],
        "公告日期": ["2026-05-28", "2026-05-28", "2026-05-28"],
        "网址": ["http://a", "http://b", "http://c"],
    })


def _mock_buyback_df():
    return pd.DataFrame({
        "股票代码": ["002138", "600000"],
        "股票简称": ["顺络电子", "浦发银行"],
        "最新价": [30.0, 8.0],
        "计划回购价格区间": ["25-35", "7-9"],
        "计划回购数量区间-下限": ["100万", "200万"],
        "计划回购数量区间-上限": ["200万", "400万"],
        "最新公告日期": ["2026-05-20", "2026-01-01"],
    })


def _mock_rating_df():
    return pd.DataFrame({
        "证券代码": ["002138", "600519"],
        "证券简称": ["顺络电子", "贵州茅台"],
        "发布日期": ["2026-05-28", "2026-05-28"],
        "研究机构简称": ["中信证券", "国泰君安"],
        "研究员名称": ["张三", "李四"],
        "投资评级": ["买入", "减持"],
        "是否首次评级": ["否", "否"],
        "评级变化": ["维持", "下调"],
    })


class TestFetchNoticeEvents:
    @patch("tradingagents.tradeflow.event_source._bypass_proxy")
    def test_fetch_notice_events(self, mock_bp):
        with patch("akshare.stock_notice_report", return_value=_mock_notice_df(), create=True):
            with patch.dict(sys.modules, {"akshare": MagicMock(stock_notice_report=lambda **kw: _mock_notice_df())}):
                import akshare as ak
                with patch.object(ak, "stock_notice_report", return_value=_mock_notice_df()):
                    items = fetch_notice_events("20260528")
        mock_bp.assert_called_once()
        assert len(items) == 3
        assert items[0].symbol == "002138"
        assert items[0].event_type == "notice"
        assert items[0].direction == "bullish"
        assert items[2].direction == "bearish"

    @patch("tradingagents.tradeflow.event_source._bypass_proxy")
    def test_fetch_notice_events_empty(self, mock_bp):
        with patch("akshare.stock_notice_report", return_value=pd.DataFrame(), create=True):
            import akshare as ak
            with patch.object(ak, "stock_notice_report", return_value=pd.DataFrame()):
                items = fetch_notice_events("20260528")
        assert items == []

    @patch("tradingagents.tradeflow.event_source._bypass_proxy", side_effect=Exception("net error"))
    def test_fetch_notice_events_exception(self, mock_bp):
        items = fetch_notice_events("20260528")
        assert items == []


class TestFetchBuybackEvents:
    @patch("tradingagents.tradeflow.event_source._bypass_proxy")
    def test_fetch_buyback_filters_old(self, mock_bp):
        import akshare as ak
        with patch.object(ak, "stock_repurchase_em", return_value=_mock_buyback_df()):
            items = fetch_buyback_events()
        mock_bp.assert_called_once()
        assert len(items) == 1
        assert items[0].symbol == "002138"
        assert items[0].direction == "bullish"
        assert items[0].event_type == "buyback"

    @patch("tradingagents.tradeflow.event_source._bypass_proxy")
    def test_fetch_buyback_empty(self, mock_bp):
        import akshare as ak
        with patch.object(ak, "stock_repurchase_em", return_value=pd.DataFrame()):
            items = fetch_buyback_events()
        assert items == []


class TestFetchRatingEvents:
    @patch("tradingagents.tradeflow.event_source._bypass_proxy")
    def test_fetch_rating_events(self, mock_bp):
        import akshare as ak
        with patch.object(ak, "stock_rank_forecast_cninfo", return_value=_mock_rating_df()):
            items = fetch_rating_events("20260528")
        mock_bp.assert_called_once()
        assert len(items) == 2
        assert items[0].direction == "bullish"
        assert items[1].direction == "bearish"
        assert items[0].event_type == "rating"

    @patch("tradingagents.tradeflow.event_source._bypass_proxy")
    def test_fetch_rating_empty(self, mock_bp):
        import akshare as ak
        with patch.object(ak, "stock_rank_forecast_cninfo", return_value=pd.DataFrame()):
            items = fetch_rating_events("20260528")
        assert items == []


class TestFetchDailyEvents:
    @patch("tradingagents.tradeflow.event_source.fetch_rating_events", return_value=[])
    @patch("tradingagents.tradeflow.event_source.fetch_buyback_events", return_value=[])
    @patch("tradingagents.tradeflow.event_source.fetch_notice_events")
    def test_returns_dict_of_lists(self, mock_notice, mock_buyback, mock_rating):
        mock_notice.return_value = [
            EventItem(symbol="002138", name="顺络电子", event_type="notice",
                      title="回购进展公告", direction="bullish"),
            EventItem(symbol="002138", name="顺络电子", event_type="notice",
                      title="季度报告", direction="neutral"),
            EventItem(symbol="600519", name="贵州茅台", event_type="notice",
                      title="年报披露", direction="neutral"),
        ]
        result = fetch_daily_events("20260528")
        assert isinstance(result, dict)
        assert "002138" in result
        assert len(result["002138"]) == 2
        assert "600519" in result
        assert len(result["600519"]) == 1

    @patch("tradingagents.tradeflow.event_source.fetch_rating_events", return_value=[])
    @patch("tradingagents.tradeflow.event_source.fetch_buyback_events", return_value=[])
    @patch("tradingagents.tradeflow.event_source.fetch_notice_events", return_value=[])
    def test_empty_result(self, mock_notice, mock_buyback, mock_rating):
        result = fetch_daily_events("20260528")
        assert result == {}


class TestFetchEventsForSymbol:
    @patch("tradingagents.tradeflow.event_source.fetch_daily_events")
    def test_found(self, mock_daily):
        mock_daily.return_value = {"002138": ["回购进展", "季报"]}
        result = fetch_events_for_symbol("002138", "20260528")
        assert result == ["回购进展", "季报"]

    @patch("tradingagents.tradeflow.event_source.fetch_daily_events")
    def test_not_found(self, mock_daily):
        mock_daily.return_value = {"600519": ["年报"]}
        result = fetch_events_for_symbol("002138", "20260528")
        assert result == []

    @patch("tradingagents.tradeflow.event_source.fetch_daily_events")
    def test_strips_suffix(self, mock_daily):
        mock_daily.return_value = {"002138": ["回购进展"]}
        result = fetch_events_for_symbol("002138.SZ", "20260528")
        assert result == ["回购进展"]


# ── Integration with event_catalyst ──


class TestEventCatalystIntegration:
    def test_events_compatible_with_score_event_catalyst(self):
        from tradingagents.tradeflow.strategies.event_catalyst import score_event_catalyst

        news_texts = [
            "顺络电子:关于回购公司股份的进展公告",
            "顺络电子:2025年度业绩预告，净利润同比增长50%",
        ]
        signal = score_event_catalyst("002138", news_texts=news_texts)
        assert signal is not None
        assert signal.strategy_tag == "EVENT_CATALYST"
        assert signal.score > 0

    def test_empty_news_returns_none(self):
        from tradingagents.tradeflow.strategies.event_catalyst import score_event_catalyst

        signal = score_event_catalyst("002138", news_texts=["普通公告无关键词"])
        assert signal is None
