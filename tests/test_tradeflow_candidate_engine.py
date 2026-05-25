"""Tests for TradeFlow candidate engine — filtering, evaluation, persistence."""

import sys
import os
import tempfile
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import (
    init_db, filter_symbol, run_strategies, evaluate_symbol,
    save_candidate, _calc_liquidity,
)
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal


def _make_vcp_df():
    """VCP-like data for testing."""
    np.random.seed(123)
    n = 120
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    prices = [20.0]
    for i in range(1, 80):
        prices.append(prices[-1] * (1 + 0.004 + np.random.normal(0, 0.015)))
    base = prices[-1]
    for i in range(40):
        amp = 0.03 * (1 - i / 40)
        prices.append(base + amp * base * np.sin(i * 0.3) + np.random.normal(0, 0.005) * base)
    prices = np.array(prices[:n])
    volumes = np.concatenate([
        np.random.uniform(8e6, 15e6, 80),
        np.linspace(10e6, 3e6, 40),
    ])
    return pd.DataFrame({
        "Date": dates,
        "Open": prices, "High": prices * 1.01, "Low": prices * 0.99,
        "Close": prices, "Volume": volumes,
    })


class TestFiltering:
    def test_st_symbol_filter(self):
        """ST filter — P0 always returns False (no name lookup)."""
        df = _make_vcp_df()
        filtered, reasons = filter_symbol("TEST.SZ", df)
        assert filtered is False

    def test_low_liquidity_filter(self):
        """Low amount data should be filtered."""
        n = 50
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=n, freq="B"),
            "Open": np.full(n, 20.0),
            "High": np.full(n, 20.5),
            "Low": np.full(n, 19.5),
            "Close": np.full(n, 20.0),
            "Volume": np.full(n, 100_000),
        })
        filtered, reasons = filter_symbol("TEST.SZ", df)
        assert filtered is True
        assert "流动性差" in reasons[0]
        assert "avg_amount" in reasons[0]
        assert "threshold" in reasons[0]

    def test_missing_data_filter(self):
        filtered, reasons = filter_symbol("TEST.SZ", None)
        assert filtered is True
        assert "数据缺失" in reasons

    def test_short_data_filter(self):
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=30, freq="B"),
            "Open": np.full(30, 20.0),
            "High": np.full(30, 20.5),
            "Low": np.full(30, 19.5),
            "Close": np.full(30, 20.0),
            "Volume": np.full(30, 10e6),
        })
        filtered, reasons = filter_symbol("TEST.SZ", df)
        assert filtered is True
        assert any("数据不足" in r for r in reasons)


class TestEvaluation:
    def test_evaluate_with_data(self):
        df = _make_vcp_df()
        c, reason = evaluate_symbol("TEST.SZ", name="测试", df=df)
        if c is not None:
            assert c.symbol == "TEST.SZ"
            assert len(c.strategy_tags) > 0
            assert reason == ""
        else:
            assert reason != ""

    def test_evaluate_no_data(self):
        c, reason = evaluate_symbol("NONEXISTENT.SZ")
        assert c is None
        assert "数据缺失" in reason

    def test_evaluate_with_events(self):
        df = _make_vcp_df()
        c, reason = evaluate_symbol(
            "TEST.SZ",
            name="测试",
            df=df,
            news_texts=["公司发布业绩预告，利润增长100%"],
        )
        if c:
            assert "EVENT_CATALYST" in c.strategy_tags
            assert c.primary_strategy != ""
            assert reason == ""

    def test_evaluate_without_events(self):
        df = _make_vcp_df()
        c, reason = evaluate_symbol(
            "TEST.SZ",
            name="测试",
            df=df,
            news_texts=None,
            event_overrides=None,
        )
        if c:
            assert "EVENT_CATALYST" not in c.strategy_tags


class TestPersistence:
    def test_init_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            import sqlite3
            conn = sqlite3.connect(db_path)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            conn.close()
            assert "tradeflow_candidates" in tables
            assert "tradeflow_signals" in tables
            assert "tradeflow_daily_plans" in tables
        finally:
            os.unlink(db_path)

    def test_save_and_read_candidate(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="002353.SZ",
                name="杰恩设计",
                source="manual",
                strategy_tags=["VCP"],
                score=55.0,
                trigger_price=100.0,
                invalid_price=90.0,
                trade_date="2026-05-25",
            )
            row_id = save_candidate(c, db_path)
            assert row_id > 0

            # Read back
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='002353.SZ'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row["symbol"] == "002353.SZ"
            assert row["score"] == 55.0
        finally:
            os.unlink(db_path)

    def test_upsert_candidate(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c1 = Candidate(symbol="002353.SZ", name="测试", strategy_tags=["VCP"],
                           score=50.0, trade_date="2026-05-25")
            save_candidate(c1, db_path)
            c2 = Candidate(symbol="002353.SZ", name="测试", strategy_tags=["VCP", "PULLBACK_SUPPORT"],
                           score=65.0, trade_date="2026-05-25")
            save_candidate(c2, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM tradeflow_candidates WHERE symbol='002353.SZ' AND trade_date='2026-05-25'"
            ).fetchone()[0]
            conn.close()
            assert count == 1  # Upserted, not duplicated
        finally:
            os.unlink(db_path)

    def test_need_deep_ta_persisted(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(symbol="TEST.SZ", strategy_tags=["VCP"],
                          need_deep_ta=True, trade_date="2026-05-25",
                          primary_strategy="VCP")
            save_candidate(c, db_path)

            import sqlite3
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT need_deep_ta, primary_strategy FROM tradeflow_candidates WHERE symbol='TEST.SZ'"
            ).fetchone()
            conn.close()
            assert row[0] == 1
            assert row[1] == "VCP"
        finally:
            os.unlink(db_path)

    def test_primary_strategy_migrated(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            import sqlite3
            conn = sqlite3.connect(db_path)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()]
            conn.close()
            assert "primary_strategy" in cols
        finally:
            os.unlink(db_path)


class TestFilterReasons:
    def test_filter_missing_data_returns_reason(self):
        c, reason = evaluate_symbol("NONEXISTENT.SZ")
        assert c is None
        assert "数据缺失" in reason

    def test_filter_low_liquidity_returns_reason(self):
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=60, freq="B"),
            "Open": np.full(60, 20.0),
            "High": np.full(60, 20.5),
            "Low": np.full(60, 19.5),
            "Close": np.full(60, 20.0),
            "Volume": np.full(60, 100_000),
        })
        c, reason = evaluate_symbol("TEST.SZ", df=df)
        assert c is None
        assert "流动性差" in reason

    def test_no_strategy_hit_returns_reason(self):
        np.random.seed(42)
        n = 120
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=n, freq="B"),
            "Open": np.full(n, 20.0),
            "High": np.full(n, 20.5),
            "Low": np.full(n, 19.5),
            "Close": np.full(n, 20.0),
            "Volume": np.full(n, 10e6),
        })
        c, reason = evaluate_symbol("FLAT.SZ", df=df)
        assert c is None
        assert "无策略命中" in reason


class TestLiquidityCalibration:
    def test_high_price_low_volume_not_filtered(self):
        """High-price stock with low volume but high amount should pass.

        Price=100, Volume=500k -> Amount=50M > 30M threshold.
        """
        n = 50
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=n, freq="B"),
            "Open": np.full(n, 100.0),
            "High": np.full(n, 101.0),
            "Low": np.full(n, 99.0),
            "Close": np.full(n, 100.0),
            "Volume": np.full(n, 500_000),
        })
        is_low, avg_amount, avg_volume, threshold = _calc_liquidity(df)
        assert is_low is False
        assert avg_amount == pytest.approx(50_000_000)
        assert avg_volume == pytest.approx(500_000)

    def test_low_price_high_volume_filtered(self):
        """Low-price stock with high volume but low amount should be filtered.

        Price=2, Volume=1M -> Amount=2M < 30M threshold.
        """
        n = 50
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=n, freq="B"),
            "Open": np.full(n, 2.0),
            "High": np.full(n, 2.1),
            "Low": np.full(n, 1.9),
            "Close": np.full(n, 2.0),
            "Volume": np.full(n, 1_000_000),
        })
        is_low, avg_amount, avg_volume, threshold = _calc_liquidity(df)
        assert is_low is True
        assert avg_amount == pytest.approx(2_000_000)

    def test_amount_column_used_directly(self):
        """When Amount column exists, use it instead of Close*Volume."""
        n = 50
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=n, freq="B"),
            "Open": np.full(n, 10.0),
            "High": np.full(n, 10.5),
            "Low": np.full(n, 9.5),
            "Close": np.full(n, 10.0),
            "Volume": np.full(n, 1_000_000),
            "Amount": np.full(n, 50_000_000),
        })
        is_low, avg_amount, avg_volume, threshold = _calc_liquidity(df)
        assert is_low is False
        assert avg_amount == pytest.approx(50_000_000)

    def test_estimated_amount_when_no_amount_column(self):
        """When no Amount column, estimate via Close*Volume."""
        n = 50
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=n, freq="B"),
            "Open": np.full(n, 30.0),
            "High": np.full(n, 30.5),
            "Low": np.full(n, 29.5),
            "Close": np.full(n, 30.0),
            "Volume": np.full(n, 2_000_000),
        })
        is_low, avg_amount, avg_volume, threshold = _calc_liquidity(df)
        assert is_low is False
        assert avg_amount == pytest.approx(60_000_000)

    def test_filter_reason_shows_details(self):
        """Filter reason includes avg_amount, avg_volume, threshold."""
        n = 50
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=n, freq="B"),
            "Open": np.full(n, 5.0),
            "High": np.full(n, 5.1),
            "Low": np.full(n, 4.9),
            "Close": np.full(n, 5.0),
            "Volume": np.full(n, 500_000),
        })
        filtered, reasons = filter_symbol("TEST.SZ", df)
        assert filtered is True
        reason = reasons[0]
        assert "avg_amount" in reason
        assert "avg_volume" in reason
        assert "threshold" in reason

    def test_short_data_is_low_liquidity(self):
        """Less than 5 rows counts as low liquidity."""
        n = 4
        df = pd.DataFrame({
            "Date": pd.date_range("2026-01-01", periods=n, freq="B"),
            "Open": np.full(n, 10.0),
            "High": np.full(n, 10.5),
            "Low": np.full(n, 9.5),
            "Close": np.full(n, 10.0),
            "Volume": np.full(n, 10_000_000),
        })
        is_low, avg_amount, avg_volume, threshold = _calc_liquidity(df)
        assert is_low is True
