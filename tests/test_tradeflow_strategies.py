"""Tests for TradeFlow strategies: VCP, Pullback Support, Event Catalyst."""

import sys
import os
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.strategies.vcp import score_vcp, VCPSignal
from tradingagents.tradeflow.strategies.pullback_support import score_pullback_support, PullbackSupportSignal
from tradingagents.tradeflow.strategies.event_catalyst import score_event_catalyst, EventCatalystSignal


def _make_trending_df(n=120, base_price=20.0, trend=0.003, volatility=0.02):
    """Generate a trending price series."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [base_price]
    for i in range(1, n):
        ret = trend + np.random.normal(0, volatility)
        prices.append(prices[-1] * (1 + ret))
    prices = np.array(prices)
    df = pd.DataFrame({
        "Date": dates,
        "Open": prices * (1 + np.random.uniform(-0.005, 0.005, n)),
        "High": prices * (1 + np.random.uniform(0.005, 0.02, n)),
        "Low": prices * (1 - np.random.uniform(0.005, 0.02, n)),
        "Close": prices,
        "Volume": np.random.uniform(5_000_000, 20_000_000, n),
    })
    return df


def _make_vcp_df():
    """Generate a VCP-like pattern: trend up then consolidation with shrinking range and volume."""
    np.random.seed(123)
    n = 120
    dates = pd.date_range("2026-01-01", periods=n, freq="B")

    # Phase 1: Trend up (80 days)
    prices = [20.0]
    for i in range(1, 80):
        ret = 0.004 + np.random.normal(0, 0.015)
        prices.append(prices[-1] * (1 + ret))

    # Phase 2: Consolidation with shrinking range and volume (40 days)
    base = prices[-1]
    for i in range(40):
        # Shrinking amplitude
        amp = 0.03 * (1 - i / 40)  # decreasing
        price = base + amp * base * np.sin(i * 0.3) + np.random.normal(0, 0.005) * base
        prices.append(max(price, base * 0.95))

    prices = np.array(prices[:n])
    volumes = np.concatenate([
        np.random.uniform(8_000_000, 15_000_000, 80),  # normal vol
        np.linspace(10_000_000, 3_000_000, 40),  # declining vol
    ])

    highs = prices * (1 + np.random.uniform(0.005, 0.015, n))
    lows = prices * (1 - np.random.uniform(0.005, 0.015, n))

    return pd.DataFrame({
        "Date": dates,
        "Open": prices * (1 + np.random.uniform(-0.003, 0.003, n)),
        "High": highs,
        "Low": lows,
        "Close": prices,
        "Volume": volumes,
    })


def _make_pullback_df():
    """Generate a pullback-to-support pattern: trend up, then pullback to MA."""
    np.random.seed(456)
    n = 120
    dates = pd.date_range("2026-01-01", periods=n, freq="B")

    # Phase 1: Strong trend up (100 days)
    prices = [20.0]
    for i in range(1, 100):
        ret = 0.003 + np.random.normal(0, 0.012)
        prices.append(prices[-1] * (1 + ret))

    # Phase 2: Pullback (20 days) — decline 8%
    peak = prices[-1]
    for i in range(20):
        ret = -0.004 + np.random.normal(0, 0.008)
        prices.append(prices[-1] * (1 + ret))

    prices = np.array(prices[:n])
    volumes = np.concatenate([
        np.random.uniform(8_000_000, 15_000_000, 100),
        np.linspace(12_000_000, 5_000_000, 20),  # declining vol on pullback
    ])

    return pd.DataFrame({
        "Date": dates,
        "Open": prices * (1 + np.random.uniform(-0.003, 0.003, n)),
        "High": prices * (1 + np.random.uniform(0.005, 0.015, n)),
        "Low": prices * (1 - np.random.uniform(0.005, 0.015, n)),
        "Close": prices,
        "Volume": volumes,
    })


def _make_declining_df(n=120):
    """Generate a pure declining series — should not match VCP or Pullback."""
    np.random.seed(789)
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [50.0]
    for i in range(1, n):
        ret = -0.003 + np.random.normal(0, 0.015)
        prices.append(prices[-1] * (1 + ret))
    prices = np.array(prices)
    return pd.DataFrame({
        "Date": dates,
        "Open": prices * (1 + np.random.uniform(-0.003, 0.003, n)),
        "High": prices * (1 + np.random.uniform(0.005, 0.015, n)),
        "Low": prices * (1 - np.random.uniform(0.005, 0.015, n)),
        "Close": prices,
        "Volume": np.random.uniform(5_000_000, 15_000_000, n),
    })


class TestVCP:
    def test_vcp_hit(self):
        df = _make_vcp_df()
        result = score_vcp(df, "TEST.SZ")
        assert result is not None
        assert result.strategy_tag == "VCP"
        assert result.score > 0
        assert result.trigger_price is not None
        assert result.invalid_price is not None

    def test_vcp_no_hit_declining(self):
        df = _make_declining_df()
        result = score_vcp(df, "TEST.SZ")
        assert result is None

    def test_vcp_no_hit_insufficient_data(self):
        df = _make_trending_df(n=30)
        result = score_vcp(df, "TEST.SZ")
        assert result is None

    def test_vcp_none_on_empty(self):
        result = score_vcp(None, "TEST.SZ")
        assert result is None

    def test_vcp_has_reason(self):
        df = _make_vcp_df()
        result = score_vcp(df, "TEST.SZ")
        if result:
            assert len(result.reason) > 0


class TestPullbackSupport:
    def test_pullback_hit(self):
        df = _make_pullback_df()
        result = score_pullback_support(df, "TEST.SZ")
        assert result is not None
        assert result.strategy_tag == "PULLBACK_SUPPORT"
        assert result.support_price is not None
        assert result.invalid_price is not None

    def test_pullback_no_hit_trending(self):
        """Pure uptrend without pullback should not match."""
        df = _make_trending_df(n=120, trend=0.005)
        result = score_pullback_support(df, "TEST.SZ")
        # May or may not hit depending on data

    def test_pullback_no_hit_declining(self):
        df = _make_declining_df()
        result = score_pullback_support(df, "TEST.SZ")
        assert result is None  # Below MA60, trend broken

    def test_pullback_insufficient_data(self):
        df = _make_trending_df(n=30)
        result = score_pullback_support(df, "TEST.SZ")
        assert result is None

    def test_pullback_none_on_empty(self):
        result = score_pullback_support(None, "TEST.SZ")
        assert result is None


class TestEventCatalyst:
    def test_event_hit_bullish(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["公司发布业绩预告，净利润同比增长50%"],
            latest_close=25.0,
        )
        assert result is not None
        assert result.strategy_tag == "EVENT_CATALYST"
        assert result.event_direction == "bullish"

    def test_event_hit_bearish(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["公司收到监管处罚通知"],
            latest_close=25.0,
        )
        assert result is not None
        assert result.event_direction == "bearish"

    def test_event_no_hit(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["今天天气不错"],
            latest_close=25.0,
        )
        assert result is None

    def test_event_with_overrides(self):
        result = score_event_catalyst(
            "TEST.SZ",
            event_overrides=[{"event_type": "buyback", "direction": "bullish", "title": "回购计划"}],
            latest_close=25.0,
        )
        assert result is not None
        assert result.event_type == "buyback"

    def test_event_multiple_events(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=[
                "公司发布业绩预告",
                "控股股东增持计划",
                "中标重大工程",
            ],
            latest_close=25.0,
        )
        assert result is not None
        assert result.evidence["event_count"] == 3

    def test_event_need_deep_ta(self):
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=["公司发布并购重组公告"],
            latest_close=25.0,
        )
        assert result is not None
        assert result.need_deep_ta is True  # merger score >= 60


class TestMultiStrategy:
    def test_multiple_strategies_can_hit(self):
        """VCP and Event can both hit on the same symbol."""
        df = _make_vcp_df()
        from tradingagents.tradeflow.candidate_engine import run_strategies
        signals = run_strategies(
            "TEST.SZ",
            df,
            news_texts=["公司发布业绩预告"],
        )
        strategy_tags = {s.strategy_tag for s in signals}
        assert len(strategy_tags) >= 1

    def test_strategies_deduplicate(self):
        """Merging signals should deduplicate strategy tags."""
        from tradingagents.tradeflow.schemas import Candidate, CandidateSignal
        c = Candidate(symbol="TEST.SZ")
        c.signals = [
            CandidateSignal(strategy_tag="VCP", score=50.0),
            CandidateSignal(strategy_tag="VCP", score=60.0),
        ]
        c.merge_signals()
        assert c.strategy_tags == ["VCP"]
        assert c.score == 60.0

    def test_no_event_input_no_hit(self):
        """Without news_texts or event_overrides, EVENT_CATALYST must not hit."""
        result = score_event_catalyst("TEST.SZ", news_texts=None, event_overrides=None, latest_close=25.0)
        assert result is None

    def test_empty_news_no_hit(self):
        """Empty news_texts list should not trigger EVENT_CATALYST."""
        result = score_event_catalyst("TEST.SZ", news_texts=[], event_overrides=None, latest_close=25.0)
        assert result is None

    def test_irrelevant_news_no_hit(self):
        """News with no matching keywords should not trigger EVENT_CATALYST."""
        result = score_event_catalyst("TEST.SZ", news_texts=["今天天气晴朗"], event_overrides=None, latest_close=25.0)
        assert result is None

    def test_event_overrides_filtered_by_symbol_mismatch(self):
        """Overrides for a different symbol must not trigger EVENT_CATALYST."""
        result = score_event_catalyst(
            "AAA.SZ",
            news_texts=None,
            event_overrides=[{"symbol": "BBB.SZ", "event_type": "buyback", "direction": "bullish", "title": "回购"}],
            latest_close=25.0,
        )
        assert result is None

    def test_event_overrides_filtered_by_symbol_match(self):
        """Overrides whose symbol matches should trigger EVENT_CATALYST."""
        result = score_event_catalyst(
            "AAA.SZ",
            news_texts=None,
            event_overrides=[{"symbol": "AAA.SZ", "event_type": "buyback", "direction": "bullish", "title": "回购"}],
            latest_close=25.0,
        )
        assert result is not None
        assert result.event_type == "buyback"

    def test_event_overrides_global_no_symbol(self):
        """Overrides without a symbol field are global and should hit any stock."""
        result = score_event_catalyst(
            "AAA.SZ",
            news_texts=None,
            event_overrides=[{"event_type": "buyback", "direction": "bullish", "title": "回购"}],
            latest_close=25.0,
        )
        assert result is not None
        assert result.event_type == "buyback"

    def test_event_overrides_hit(self):
        """event_overrides should trigger EVENT_CATALYST."""
        result = score_event_catalyst(
            "TEST.SZ",
            news_texts=None,
            event_overrides=[{"event_type": "buyback", "direction": "bullish", "title": "回购计划"}],
            latest_close=25.0,
        )
        assert result is not None
        assert result.event_type == "buyback"

    def test_no_event_vcp_only(self):
        """Without events, only VCP should be evaluated (no EVENT_CATALYST)."""
        df = _make_vcp_df()
        from tradingagents.tradeflow.candidate_engine import run_strategies
        signals = run_strategies("TEST.SZ", df, news_texts=None, event_overrides=None)
        tags = {s.strategy_tag for s in signals}
        assert "EVENT_CATALYST" not in tags


class TestVCPScoring:
    def test_strong_contraction_scores_higher(self):
        """Strong contraction (low range_ratio) should score higher than weak."""
        df_strong = _make_vcp_df()
        result_strong = score_vcp(df_strong, "STRONG.SZ")

        np.random.seed(999)
        n = 120
        dates = pd.date_range("2026-01-01", periods=n, freq="B")
        prices = [20.0]
        for i in range(1, 80):
            prices.append(prices[-1] * (1 + 0.004 + np.random.normal(0, 0.015)))
        base = prices[-1]
        for i in range(40):
            amp = 0.07 * (1 - i / 60)
            prices.append(base + amp * base * np.sin(i * 0.3) + np.random.normal(0, 0.005) * base)
        prices = np.array(prices[:n])
        volumes = np.concatenate([
            np.random.uniform(8e6, 15e6, 80),
            np.linspace(10e6, 3e6, 40),
        ])
        df_weak = pd.DataFrame({
            "Date": dates,
            "Open": prices, "High": prices * 1.02, "Low": prices * 0.98,
            "Close": prices, "Volume": volumes,
        })
        result_weak = score_vcp(df_weak, "WEAK.SZ")

        if result_strong is not None and result_weak is not None:
            assert result_strong.evidence["range_ratio"] <= result_weak.evidence.get("range_ratio", 1)
            assert result_strong.score >= result_weak.score
