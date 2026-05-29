"""TradeFlow universe — manages the watch universe from multiple sources.  # [M-003] tradeflow_universe_manager

P0 sources:
1. Current holdings (imported_portfolio_positions)
2. Watchlist items
3. Manual symbol specification
4. Previously observed candidates from yesterday's review
5. Event/catalyst triggered stocks
6. Fund flow anomaly triggered stocks
7. Manual industry pool

M-003 introduces UniverseManager which:
- Defines a UniverseSource enum for all source types
- Tracks per-symbol: sources list, entry reasons, timestamps, filter reasons
- Deduplicates symbols while preserving ALL source provenance
- Never triggers full-market scan when no sources are provided
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class UniverseSource(Enum):
    WATCHLIST = "watchlist"
    HOLDING = "holding"
    MANUAL = "manual"
    EVENT = "event_catalyst"  # [M-011] universe_compat_fix — preserve old public source label
    EVENT_SOURCE = "event_source"
    FUND_FLOW = "fund_flow_pool"
    INDUSTRY = "industry_pool"
    YESTERDAY_OBSERVE = "yesterday_observe"


@dataclass
class SourceRecord:
    source: UniverseSource
    reason: str = ""
    timestamp: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class UniverseEntry:
    symbol: str
    name: str = ""
    sources: list[SourceRecord] = field(default_factory=list)
    filter_reason: str = ""

    @property
    def primary_source(self) -> str:
        if self.sources:
            return self.sources[0].source.value
        return ""

    @property
    def source_values(self) -> list[str]:
        return sorted({sr.source.value for sr in self.sources})

    def to_dict(self) -> dict:
        result = {  # [M-011] universe_compat_fix — include extra in serialization
            "symbol": self.symbol,
            "name": self.name,
            "source": self.primary_source,
            "universe_sources": self.source_values,
            "universe_source_records": [],
            "filter_reason": self.filter_reason,
        }
        for sr in self.sources:
            rec = {
                "source": sr.source.value,
                "reason": sr.reason,
                "timestamp": sr.timestamp,
            }
            if sr.extra:
                rec["extra"] = sr.extra
            result["universe_source_records"].append(rec)
        return result


class UniverseManager:
    """Centralized manager for TradeFlow candidate universe.  # [M-003] tradeflow_universe_manager

    Collects symbols from multiple sources, deduplicates by symbol
    while preserving all source provenance, and never falls back to
    full-market scan.

    Usage:
        mgr = UniverseManager()
        mgr.add_from_watchlist(db_path)
        mgr.add_from_holdings(db_path)
        mgr.add_manual(["600519.SH"])
        mgr.add_from_events(event_map)
        universe = mgr.get_universe()  # list[dict]
    """

    def __init__(self):
        self._entries: dict[str, UniverseEntry] = {}

    def add_symbol(
        self,
        symbol: str,
        source: UniverseSource,
        name: str = "",
        reason: str = "",
        extra: Optional[dict] = None,
    ) -> UniverseEntry:
        symbol = symbol.strip()
        if not symbol:
            return self._entries.get("", UniverseEntry(symbol=""))

        entry = self._entries.get(symbol)
        if entry is None:
            entry = UniverseEntry(symbol=symbol, name=name)
            self._entries[symbol] = entry
        if name and not entry.name:
            entry.name = name

        existing_sources = {sr.source for sr in entry.sources}
        if source not in existing_sources:
            entry.sources.append(SourceRecord(
                source=source,
                reason=reason,
                extra=extra or {},
            ))

        return entry

    def remove_symbol(self, symbol: str, reason: str = "") -> None:
        entry = self._entries.get(symbol.strip())
        if entry is not None:
            entry.filter_reason = reason

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def symbols(self) -> list[str]:
        return list(self._entries.keys())

    def get_entry(self, symbol: str) -> Optional[UniverseEntry]:
        return self._entries.get(symbol.strip())

    def get_universe(self) -> list[dict]:
        return [entry.to_dict() for entry in self._entries.values()]

    def get_active_universe(self) -> list[dict]:
        return [
            entry.to_dict()
            for entry in self._entries.values()
            if not entry.filter_reason
        ]

    def get_source_summary(self) -> dict[str, int]:
        summary: dict[str, int] = {}
        for entry in self._entries.values():
            for sr in entry.sources:
                key = sr.source.value
                summary[key] = summary.get(key, 0) + 1
        return summary

    def add_from_holdings(self, db_path: str) -> int:
        count = 0
        for item in get_holdings(db_path):
            self.add_symbol(
                symbol=item["symbol"],
                source=UniverseSource.HOLDING,
                name=item.get("name", ""),
                reason="持仓股",
            )
            count += 1
        return count

    def add_from_watchlist(self, db_path: str) -> int:
        count = 0
        for item in get_watchlist(db_path):
            self.add_symbol(
                symbol=item["symbol"],
                source=UniverseSource.WATCHLIST,
                name=item.get("name", ""),
                reason="自选股",
            )
            count += 1
        return count

    def add_from_yesterday(self, tf_db_path: str, trade_date: Optional[str] = None) -> int:
        count = 0
        for item in get_yesterday_observations(tf_db_path, trade_date):
            extra = {}
            if item.get("strategy_tags"):
                extra["strategy_tags"] = item["strategy_tags"]
            if item.get("trigger_price"):
                extra["trigger_price"] = item["trigger_price"]
            if item.get("invalid_price"):
                extra["invalid_price"] = item["invalid_price"]
            self.add_symbol(
                symbol=item["symbol"],
                source=UniverseSource.YESTERDAY_OBSERVE,
                name=item.get("name", ""),
                reason="昨日候选继续观察",
                extra=extra,
            )
            count += 1
        return count

    def add_manual(
        self,
        symbols: list[str],
        reason: str = "手动指定",
    ) -> int:
        count = 0
        for sym in symbols:
            sym = sym.strip()
            if sym:
                self.add_symbol(
                    symbol=sym,
                    source=UniverseSource.MANUAL,
                    reason=reason,
                )
                count += 1
        return count

    def add_from_events(
        self,
        event_overrides: Optional[list[dict]] = None,
        event_symbols: Optional[dict[str, list[str]]] = None,
    ) -> int:
        count = 0

        if event_overrides:
            for ev in event_overrides:
                sym = ev.get("symbol", "").strip()
                if sym:
                    self.add_symbol(
                        symbol=sym,
                        source=UniverseSource.EVENT,
                        name=ev.get("name", ""),
                        reason=f"事件催化: {ev.get('title', '') or ev.get('event_type', '')}",
                    )
                    count += 1

        if event_symbols:
            for sym, titles in event_symbols.items():
                sym = sym.strip()
                if sym:
                    title_preview = "; ".join(titles[:3]) if titles else ""
                    self.add_symbol(
                        symbol=sym,
                        source=UniverseSource.EVENT_SOURCE,
                        reason=f"事件源: {title_preview}" if title_preview else "事件源",
                    )
                    count += 1

        return count

    def add_from_fund_flow(
        self,
        symbols: list[str],
        reason: str = "资金异动",
    ) -> int:
        count = 0
        for sym in symbols:
            sym = sym.strip()
            if sym:
                self.add_symbol(
                    symbol=sym,
                    source=UniverseSource.FUND_FLOW,
                    reason=reason,
                )
                count += 1
        return count

    def add_from_industry(
        self,
        symbols: list[str],
        industry_name: str = "",
        reason: str = "行业池",
    ) -> int:
        count = 0
        reason_text = f"{reason}: {industry_name}" if industry_name else reason
        for sym in symbols:
            sym = sym.strip()
            if sym:
                self.add_symbol(
                    symbol=sym,
                    source=UniverseSource.INDUSTRY,
                    reason=reason_text,
                )
                count += 1
        return count


def get_holdings(db_path: str) -> list[dict]:
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
    event_symbols: Optional[dict[str, list[str]]] = None,
    industry_symbols: Optional[list[str]] = None,
    fund_flow_symbols: Optional[list[str]] = None,
) -> list[dict]:
    """Build the full universe of symbols to evaluate.

    Returns a deduplicated list of dicts with keys:
    - symbol, name, source (primary), universe_sources (all sources),
      universe_source_records (per-source detail), filter_reason.

    Backward compatible: callers that only read symbol/name/source still work.
    """
    mgr = UniverseManager()

    if include_holdings and prod_db_path:
        mgr.add_from_holdings(prod_db_path)

    if include_watchlist and prod_db_path:
        mgr.add_from_watchlist(prod_db_path)

    if symbols:
        mgr.add_manual(symbols)

    if include_yesterday and tf_db_path:
        mgr.add_from_yesterday(tf_db_path)

    if event_overrides or event_symbols:
        mgr.add_from_events(event_overrides, event_symbols)

    if industry_symbols:
        mgr.add_from_industry(industry_symbols)

    if fund_flow_symbols:
        mgr.add_from_fund_flow(fund_flow_symbols)

    return mgr.get_universe()
