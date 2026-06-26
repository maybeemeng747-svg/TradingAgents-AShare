# [TF-OBS-001] tradeflow_observe_runner
"""TradeFlow Observe Runner — executes intraday observe checks for active candidates.

Responsibilities:
1. Read candidates with effective_trade_date = today and status = 'active'.
2. Fetch realtime price/volume/amount for each candidate symbol.
3. Call run_observe_check() for each candidate.
4. Update tradeflow_candidates observe_state/trigger_count/first_trigger_time.
5. Write tradeflow_signals with each check snapshot.
6. Skip on non-trading days with explicit skip reason.

Design constraints:
- Low-frequency checks (default 30 min or manual trigger).
- Does NOT auto-invoke deep TA or LLM.
- Does NOT auto-push notifications.
- Non-trading days return explicit skip status.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from .intraday_observe import (
    ObserveState,
    ObserveSnapshot,
    ObserveTracker,
    run_observe_check,
)
from .schemas import Candidate, Signal
from .candidate_engine import init_db, save_candidate
from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG

_MAX_OBSERVE_FALLBACK_DAYS = 5


@dataclass
class ObserveRunResult:
    checked: int = 0
    triggered: int = 0
    invalidated: int = 0
    waiting: int = 0
    skipped: int = 0
    signals_written: int = 0
    errors: list[str] = field(default_factory=list)
    skipped_reason: str = ""
    run_time: str = ""
    details: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.run_time:
            self.run_time = datetime.now().isoformat()


def _is_trading_day(date_str: str) -> bool:
    try:
        from tradingagents.dataflows.trade_calendar import is_cn_trading_day
        return is_cn_trading_day(date_str)
    except Exception:
        from datetime import datetime as _dt
        d = _dt.strptime(date_str, "%Y-%m-%d")
        return d.weekday() < 5


def _fetch_realtime_quotes(symbols: list[str]) -> dict[str, dict]:
    """Fetch realtime quotes for a list of symbols.

    Returns dict keyed by symbol with keys:
    current_price, current_volume, current_amount, quote_time, source.
    """
    if not symbols:
        return {}

    try:
        from tradingagents.dataflows.interface import route_to_vendor
        raw_json = route_to_vendor("get_realtime_quotes", symbols)
        if not raw_json:
            return {}
        quotes = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    except Exception:
        return {}

    result: dict[str, dict] = {}
    for sym, q in quotes.items():
        if not isinstance(q, dict):
            continue
        price = q.get("price")
        if price is not None:
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
        result[sym] = {
            "current_price": price,
            "current_volume": q.get("volume"),
            "current_amount": q.get("amount"),
            "quote_time": q.get("quote_time", ""),
            "source": q.get("source", ""),
        }
    return result


def _load_active_candidates(db_path: str, trade_date: str) -> list[Candidate]:
    """Load active candidates for a given effective_trade_date from DB."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()}
        has_eff = "effective_trade_date" in columns

        if has_eff:
            rows = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE "
                "(effective_trade_date = ? OR (trade_date = ? AND (effective_trade_date = '' OR effective_trade_date IS NULL))) "
                "AND status = 'active'",
                (trade_date, trade_date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE trade_date = ? AND status = 'active'",
                (trade_date,),
            ).fetchall()

        if not rows:
            # [TF-QUALITY-001A] pool_gate_contract — if an observe date has no
            # exact candidates, fall back to the latest active plan in this DB.
            # This preserves weekend/next-trading-day workflows and keeps tests
            # with isolated temp DB candidates from depending on today's date.
            date_expr = "COALESCE(NULLIF(effective_trade_date, ''), trade_date)"
            current_records = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM tradeflow_candidates "
                f"WHERE {date_expr} = ?",
                (trade_date,),
            ).fetchone()
            if current_records and current_records["cnt"] > 0:
                return []

            latest = conn.execute(
                f"SELECT {date_expr} AS active_date FROM tradeflow_candidates "
                f"WHERE status = 'active' AND {date_expr} IS NOT NULL AND {date_expr} != '' "
                f"AND {date_expr} <= ? "
                f"AND julianday(?) - julianday({date_expr}) <= ? "
                f"ORDER BY active_date DESC LIMIT 1",
                (trade_date, trade_date, _MAX_OBSERVE_FALLBACK_DAYS),
            ).fetchone()
            if latest and latest["active_date"]:
                rows = conn.execute(
                    f"SELECT * FROM tradeflow_candidates WHERE status = 'active' AND {date_expr} = ? "
                    f"ORDER BY updated_at DESC, created_at DESC",
                    (latest["active_date"],),
                ).fetchall()

        candidates = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            candidates.append(Candidate.from_db_row(d))
        return candidates
    finally:
        conn.close()


def _save_signal(signal: Signal, db_path: str) -> int:
    """Persist a signal to tradeflow_signals table."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        row = signal.to_db_row()
        cur = conn.execute(
            "INSERT INTO tradeflow_signals "
            "(signal_time, symbol, signal_type, signal_level, source, evidence_json, action_hint, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["signal_time"],
                row["symbol"],
                row["signal_type"],
                row["signal_level"],
                row["source"],
                row["evidence_json"],
                row["action_hint"],
                row["status"],
                signal.created_at,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _build_signal_from_snapshot(
    snapshot: ObserveSnapshot,
    trade_date: str,
    quote_source: str = "",
) -> Signal:
    """Convert an ObserveSnapshot into a Signal for persistence."""
    state = snapshot.observe_state
    if state == ObserveState.TRIGGERED:
        signal_type = "observe_triggered"
        signal_level = "warning"
    elif state == ObserveState.INVALIDATED:
        signal_type = "observe_invalidated"
        signal_level = "error"
    elif state == ObserveState.EXPIRED:
        signal_type = "observe_expired"
        signal_level = "info"
    else:
        signal_type = "observe_check"
        signal_level = "info"

    evidence = {
        "trade_date": trade_date,
        "check_time": snapshot.check_time,
        "observe_state": state.value if isinstance(state, ObserveState) else str(state),
        "trigger_price": snapshot.trigger_price,
        "invalid_price": snapshot.invalid_price,
        "current_price": snapshot.current_price,
        "current_volume": snapshot.current_volume,
        "current_amount": snapshot.current_amount,
        "trigger_reason": snapshot.trigger_reason,
        "daily_trigger_count": snapshot.daily_trigger_count,
        "price_evidence": snapshot.price_evidence,
        "quote_source": quote_source,
        "volume_anomaly": getattr(snapshot, "volume_anomaly", False),
        "volume_ratio": getattr(snapshot, "volume_ratio", None),
    }

    return Signal(
        symbol=snapshot.symbol,
        signal_type=signal_type,
        signal_level=signal_level,
        source=f"observe_runner",
        evidence=evidence,
        action_hint="OBSERVE",
        status="new",
    )


def run_observe(
    trade_date: str,
    db_path: str,
    cfg: Optional[StrategyConfig] = None,
    quote_provider=None,
) -> ObserveRunResult:
    """Run observe check for all active candidates on a given trade_date.

    Args:
        trade_date: The effective trade date (YYYY-MM-DD).
        db_path: Path to tradeflow.db.
        cfg: Strategy config (defaults to DEFAULT_STRATEGY_CONFIG).
        quote_provider: Optional callable(symbols) -> dict for testing.
                        When provided, overrides the default quote fetching.

    Returns:
        ObserveRunResult with check counts and details.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    result = ObserveRunResult()

    if quote_provider is None and not _is_trading_day(trade_date):
        result.skipped_reason = f"{trade_date} 非交易日，跳过实时观察"
        return result

    candidates = _load_active_candidates(db_path, trade_date)
    if not candidates:
        result.skipped_reason = f"{trade_date} 无活跃候选"
        return result

    symbols = [c.symbol for c in candidates]

    if quote_provider is not None:
        quotes = quote_provider(symbols)
    else:
        quotes = _fetch_realtime_quotes(symbols)

    for cand in candidates:
        q = quotes.get(cand.symbol, {})
        current_price = q.get("current_price")
        current_volume = q.get("current_volume")
        current_amount = q.get("current_amount")
        quote_source = q.get("source", "")

        if current_price is None:
            result.skipped += 1
            result.details.append({
                "symbol": cand.symbol,
                "status": "skipped_no_price",
                "reason": "实时行情不可用",
            })
            continue

        tracker = ObserveTracker(
            symbol=cand.symbol,
            state=ObserveState(cand.observe_state) if cand.observe_state else ObserveState.WAITING,
            trigger_price=cand.trigger_price,
            invalid_price=cand.invalid_price,
            daily_trigger_count=cand.observe_trigger_count,
            trade_date=trade_date,
            first_trigger_time=cand.observe_first_trigger_time or None,
        )

        snapshot = run_observe_check(
            tracker=tracker,
            current_price=current_price,
            current_volume=current_volume,
            current_amount=current_amount,
            avg_volume=q.get("avg_volume"),
            cfg=cfg,
        )

        cand.observe_state = tracker.state.value
        cand.observe_trigger_count = tracker.daily_trigger_count
        cand.observe_first_trigger_time = tracker.first_trigger_time or ""

        try:
            save_candidate(cand, db_path)
        except Exception as e:
            result.errors.append(f"{cand.symbol}: save failed: {e}")
            continue

        signal = _build_signal_from_snapshot(snapshot, trade_date, quote_source)
        try:
            _save_signal(signal, db_path)
            result.signals_written += 1
        except Exception as e:
            result.errors.append(f"{cand.symbol}: signal save failed: {e}")

        result.checked += 1
        state = snapshot.observe_state
        if state == ObserveState.TRIGGERED:
            result.triggered += 1
        elif state == ObserveState.INVALIDATED:
            result.invalidated += 1
        else:
            result.waiting += 1

        result.details.append({
            "symbol": cand.symbol,
            "observe_state": state.value if isinstance(state, ObserveState) else str(state),
            "current_price": current_price,
            "trigger_reason": snapshot.trigger_reason,
            "volume_anomaly": getattr(snapshot, "volume_anomaly", False),
            "volume_ratio": getattr(snapshot, "volume_ratio", None),
        })

    return result
