"""TradeFlow universe — manages the watch universe from multiple sources.

P0 sources:
1. Current holdings (imported_portfolio_positions)
2. Watchlist items
3. Manual symbol specification
4. Previously observed candidates from yesterday's review
5. Event/catalyst triggered stocks
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional


def get_holdings(db_path: str) -> list[dict]:
    """Load current holdings from imported_portfolio_positions."""
    rows = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT symbol, name, quantity, avg_cost, current_price, market_value "
            "FROM imported_portfolio_positions WHERE quantity > 0"
        )
        for r in cur.fetchall():
            rows.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "source": "holding",
            })
        conn.close()
    except Exception:
        pass
    return rows


def get_watchlist(db_path: str) -> list[dict]:
    """Load watchlist items."""
    rows = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT symbol, name FROM watchlist_items")
        for r in cur.fetchall():
            rows.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "source": "watchlist",
            })
        conn.close()
    except Exception:
        pass
    return rows


def get_yesterday_observations(tf_db_path: str, trade_date: Optional[str] = None) -> list[dict]:
    """Load candidates from yesterday that were kept for observation."""
    if trade_date is None:
        trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = []
    try:
        conn = sqlite3.connect(tf_db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT symbol, name, strategy_tags_json, trigger_price, invalid_price "
            "FROM tradeflow_candidates WHERE trade_date = ? AND status = 'active'",
            (trade_date,),
        )
        for r in cur.fetchall():
            rows.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "source": "yesterday_observe",
                "strategy_tags": r["strategy_tags_json"],
                "trigger_price": r["trigger_price"],
                "invalid_price": r["invalid_price"],
            })
        conn.close()
    except Exception:
        pass
    return rows


def build_universe(
    symbols: Optional[list[str]] = None,
    prod_db_path: str = "",
    tf_db_path: str = "",
    include_holdings: bool = True,
    include_watchlist: bool = True,
    include_yesterday: bool = True,
    event_overrides: Optional[list[dict]] = None,
    event_symbols: Optional[dict[str, list[str]]] = None,  # [N-001] event_source_plan_integration
) -> list[dict]:
    """Build the full universe of symbols to evaluate.

    Returns a deduplicated list of {symbol, name, source} dicts.
    """
    universe: dict[str, dict] = {}

    # 1. Holdings
    if include_holdings and prod_db_path:
        for item in get_holdings(prod_db_path):
            universe.setdefault(item["symbol"], item)

    # 2. Watchlist
    if include_watchlist and prod_db_path:
        for item in get_watchlist(prod_db_path):
            universe.setdefault(item["symbol"], item)

    # 3. Manual symbols
    if symbols:
        for sym in symbols:
            sym = sym.strip()
            if sym and sym not in universe:
                universe[sym] = {"symbol": sym, "name": "", "source": "manual"}

    # 4. Yesterday observations
    if include_yesterday and tf_db_path:
        for item in get_yesterday_observations(tf_db_path):
            universe.setdefault(item["symbol"], item)

    # 5. Event-triggered stocks from overrides
    if event_overrides:
        for ev in event_overrides:
            sym = ev.get("symbol", "").strip()
            if sym and sym not in universe:
                universe[sym] = {
                    "symbol": sym,
                    "name": ev.get("name", ""),
                    "source": "event_catalyst",
                }

    # 6. [N-001] event_source_plan_integration — event-discovered symbols
    if event_symbols:
        for sym, titles in event_symbols.items():
            sym = sym.strip()
            if sym and sym not in universe:
                universe[sym] = {
                    "symbol": sym,
                    "name": "",
                    "source": "event_source",
                }

    return list(universe.values())
