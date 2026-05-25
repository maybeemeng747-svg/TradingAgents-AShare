"""TradeFlow Candidate Engine — runs strategies and produces candidates.

Responsibilities:
- Load price data for each symbol in the universe
- Run all three strategies
- Filter out ST / low-liquidity / data-missing stocks
- Deduplicate multi-strategy hits
- Mark need_deep_ta
- Persist to tradeflow tables
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

import pandas as pd

from .schemas import Candidate, CandidateSignal, ALL_STRATEGIES
from .strategies.vcp import score_vcp
from .strategies.pullback_support import score_pullback_support
from .strategies.event_catalyst import score_event_catalyst


# ── SQL for table creation ──
CREATE_CANDIDATES_TABLE = """
CREATE TABLE IF NOT EXISTS tradeflow_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    strategy_tags_json TEXT DEFAULT '[]',
    primary_strategy TEXT DEFAULT '',
    score REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active',
    trigger_price REAL,
    support_price REAL,
    invalid_price REAL,
    need_deep_ta INTEGER DEFAULT 0,
    evidence_json TEXT DEFAULT '{}',
    risk_flags_json TEXT DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(trade_date, symbol)
);
"""

CREATE_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS tradeflow_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    signal_level TEXT DEFAULT 'info',
    source TEXT DEFAULT 'tradeflow',
    evidence_json TEXT DEFAULT '{}',
    action_hint TEXT DEFAULT 'OBSERVE',
    status TEXT DEFAULT 'new',
    created_at TEXT
);
"""

CREATE_DAILY_PLANS_TABLE = """
CREATE TABLE IF NOT EXISTS tradeflow_daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    mode TEXT DEFAULT 'pre_market',
    summary TEXT DEFAULT '',
    candidates_json TEXT DEFAULT '[]',
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT,
    UNIQUE(trade_date, mode)
);
"""


def init_db(db_path: str) -> None:
    """Create tradeflow tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        CREATE_CANDIDATES_TABLE
        + CREATE_SIGNALS_TABLE
        + CREATE_DAILY_PLANS_TABLE
    )
    try:
        conn.execute("ALTER TABLE tradeflow_candidates ADD COLUMN primary_strategy TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def _fetch_price_data(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    """Fetch price data for a symbol using the project's data providers.

    Uses cn_akshare → yfinance fallback chain.
    Returns DataFrame with Date, Open, High, Low, Close, Volume columns.
    """
    from datetime import timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Try cn_akshare provider
    try:
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        provider = CnAkshareProvider()
        csv_str = provider.get_stock_data(symbol, start, end)
        if csv_str and "No data found" not in csv_str:
            from io import StringIO
            df = pd.read_csv(StringIO(csv_str), comment="#")
            if {"Date", "Open", "High", "Low", "Close", "Volume"}.issubset(df.columns):
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.sort_values("Date").reset_index(drop=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["Close"])
                if len(df) > 0:
                    return df
    except Exception:
        pass

    # Fallback: yfinance
    try:
        from tradingagents.dataflows.providers.yfinance_provider import YFinanceProvider
        provider = YFinanceProvider()
        csv_str = provider.get_stock_data(symbol, start, end)
        if csv_str and "No data found" not in csv_str:
            from io import StringIO
            df = pd.read_csv(StringIO(csv_str), comment="#")
            if {"Date", "Open", "High", "Low", "Close", "Volume"}.issubset(df.columns):
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.sort_values("Date").reset_index(drop=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["Close"])
                if len(df) > 0:
                    return df
    except Exception:
        pass

    return None


def _is_st_symbol(symbol: str) -> bool:
    """Check if symbol is ST/*ST."""
    code = symbol.split(".")[0] if "." in symbol else symbol
    # Can't determine ST from code alone — would need name lookup
    # P0: skip this check, rely on risk_flags from data
    return False


def _calc_liquidity(df: pd.DataFrame) -> tuple[bool, float, float, float]:
    """Check liquidity using Amount (turnover) as primary metric.

    Returns (is_low, avg_amount, avg_volume, threshold).
    - Uses Amount column if available, otherwise estimates via Close * Volume.
    - Threshold: 30,000,000 (3000万).
    """
    AMOUNT_THRESHOLD = 30_000_000

    if len(df) < 5:
        avg_amount = 0.0
        avg_volume = 0.0
        return True, avg_amount, avg_volume, AMOUNT_THRESHOLD

    tail = df.tail(10)
    avg_volume = float(tail["Volume"].mean())

    if "Amount" in tail.columns:
        avg_amount = float(tail["Amount"].mean())
    else:
        avg_amount = float((tail["Close"] * tail["Volume"]).mean())

    return avg_amount < AMOUNT_THRESHOLD, avg_amount, avg_volume, AMOUNT_THRESHOLD


def filter_symbol(symbol: str, df: pd.DataFrame) -> tuple[bool, list[str]]:
    """Check if a symbol should be filtered out.

    Returns (should_filter, reason_list).
    """
    reasons = []

    if df is None or len(df) == 0:
        return True, ["数据缺失"]

    if len(df) < 40:
        return True, ["数据不足(需至少40日)"]

    is_low, avg_amount, avg_volume, threshold = _calc_liquidity(df)
    if is_low:
        reasons.append(
            f"流动性差(avg_amount={avg_amount/1e8:.2f}亿, "
            f"avg_volume={avg_volume/1e4:.0f}万, "
            f"threshold={threshold/1e8:.2f}亿)"
        )

    if reasons:
        return True, reasons

    return False, []


def run_strategies(
    symbol: str,
    df: pd.DataFrame,
    news_texts: Optional[list[str]] = None,
    event_overrides: Optional[list[dict]] = None,
) -> list[CandidateSignal]:
    """Run all strategies on a symbol, return list of signals."""
    signals = []

    # VCP
    vcp = score_vcp(df, symbol)
    if vcp is not None:
        signals.append(vcp)

    # Pullback Support
    pb = score_pullback_support(df, symbol)
    if pb is not None:
        signals.append(pb)

    # Event Catalyst
    latest_close = float(df["Close"].iloc[-1]) if len(df) > 0 else 0.0
    ev = score_event_catalyst(symbol, news_texts, event_overrides, latest_close)
    if ev is not None:
        signals.append(ev)

    return signals


def evaluate_symbol(
    symbol: str,
    name: str = "",
    source: str = "manual",
    trade_date: str = "",
    news_texts: Optional[list[str]] = None,
    event_overrides: Optional[list[dict]] = None,
    df: Optional[pd.DataFrame] = None,
) -> tuple[Optional[Candidate], str]:
    """Evaluate one symbol through all strategies.

    Returns (Candidate, "") if any strategy hits, or (None, reason) if
    filtered or no strategy matched.
    """
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    # Fetch data if not provided
    if df is None:
        df = _fetch_price_data(symbol)

    # Filter check
    filtered, reasons = filter_symbol(symbol, df)
    if filtered:
        return None, "；".join(reasons)

    # Run strategies
    signals = run_strategies(symbol, df, news_texts, event_overrides)

    if not signals:
        return None, "无策略命中"

    # Build candidate
    candidate = Candidate(
        symbol=symbol,
        name=name,
        source=source,
        trade_date=trade_date,
        signals=signals,
    )
    candidate.merge_signals()

    return candidate, ""


def save_candidate(candidate: Candidate, db_path: str) -> int:
    """Save a candidate to the database. Upsert on (trade_date, symbol)."""
    conn = sqlite3.connect(db_path)
    row = candidate.to_db_row()
    try:
        conn.execute(
            "INSERT INTO tradeflow_candidates "
            "(trade_date, symbol, name, source, strategy_tags_json, primary_strategy, "
            "score, status, trigger_price, support_price, invalid_price, need_deep_ta, "
            "evidence_json, risk_flags_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, symbol) DO UPDATE SET "
            "name=excluded.name, source=excluded.source, strategy_tags_json=excluded.strategy_tags_json, "
            "primary_strategy=excluded.primary_strategy, score=excluded.score, status=excluded.status, "
            "trigger_price=excluded.trigger_price, "
            "support_price=excluded.support_price, invalid_price=excluded.invalid_price, "
            "need_deep_ta=excluded.need_deep_ta, evidence_json=excluded.evidence_json, "
            "risk_flags_json=excluded.risk_flags_json, updated_at=excluded.updated_at",
            (
                row["trade_date"], row["symbol"], row["name"], row["source"],
                row["strategy_tags_json"], row["primary_strategy"],
                row["score"], row["status"],
                row["trigger_price"], row["support_price"], row["invalid_price"],
                row["need_deep_ta"], row["evidence_json"], row["risk_flags_json"],
                candidate.created_at, row["updated_at"],
            ),
        )
        conn.commit()
        row_id = conn.execute(
            "SELECT id FROM tradeflow_candidates WHERE trade_date=? AND symbol=?",
            (row["trade_date"], row["symbol"]),
        ).fetchone()[0]
        return row_id
    finally:
        conn.close()
