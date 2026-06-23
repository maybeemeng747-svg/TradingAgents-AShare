# [IC-TA-001] investment_controller_context
"""Read-only context pack for the investment-controller.

Aggregates six stable buckets that the investment-controller (the external
pre/open/post-market scheduler) needs as input:

    1. holdings snapshot          - real imported portfolio positions
    2. observation warehouse      - symbols awaiting entry confirmation
    3. TradeFlow latest candidates- latest candidate pool summary
    4. latest TA report summary   - most recent completed TA report per symbol
    5. data health summary        - TradeFlow DB freshness + source availability
    6. pending TA required items  - observation items + candidates flagged for TA

Design contract (see docs/TASKS.md IC-TA-001):

    * READ-ONLY. No state writes, no TA/LLM triggers, no notifications.
    * Every bucket entry carries ``source`` + ``as_of``.
    * Each bucket declares a ``data_status`` drawn from the fixed set
      ``fresh / stale / missing / failed / skipped``.
    * Stable empty structure on no data — never raises on missing DB / tables.
    * No strong final action verb synthesised here — only structured facts
      and soft state (report decisions / observation statuses pass through as
      factual context, but this service emits no buy/sell recommendation).
    * No API key / token / secret is ever surfaced.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from api.runtime_tier import tradeflow_meta as _tradeflow_meta  # [PERF-001]
from api.services.tracking_board_service import (
    _fetch_live_quotes,
    _list_imported_position_rows,
    _select_reports_for_symbols,
    _serialize_report_summary,
    _to_float,
)
from tradingagents.dataflows.trade_calendar import (
    cn_today_str,
    is_cn_trading_day,
    previous_cn_trading_day,
)

logger = logging.getLogger(__name__)

CONTEXT_SCHEMA_VERSION = "1.0"
CONTEXT_SOURCE = "investment_controller_context"

# Canonical data_status vocabulary (see docs/TASKS.md IC-TA-001).
DATA_STATUS_FRESH = "fresh"
DATA_STATUS_STALE = "stale"
DATA_STATUS_MISSING = "missing"
DATA_STATUS_FAILED = "failed"
DATA_STATUS_SKIPPED = "skipped"
ALLOWED_DATA_STATUSES = frozenset({
    DATA_STATUS_FRESH,
    DATA_STATUS_STALE,
    DATA_STATUS_MISSING,
    DATA_STATUS_FAILED,
    DATA_STATUS_SKIPPED,
})

# Strong action verbs the synthesised context must NEVER emit as its own
# recommendation. Note: TA report.decision fields may legitimately carry these
# as structured facts (they describe the report, not the controller's order).
_STRONG_ACTION_PATTERNS = ("立即买入", "立即卖出", "满仓", "清仓", "全仓")


def get_investment_controller_context(
    db: Session,
    user_id: str,
    *,
    tf_db_path: str = "",
) -> dict[str, Any]:
    """Build the read-only context pack for the investment-controller.

    Args:
        db: SQLAlchemy session bound to ``tradingagents.db`` (read-only use).
        user_id: User whose holdings + reports should be surfaced.
        tf_db_path: Optional override for the tradeflow SQLite path (tests).

    Returns:
        A dict with the stable schema described in the module docstring.
        Every bucket is always present; buckets with no data return an empty
        structure with ``data_status`` set appropriately (never raises).
    """
    now = datetime.now()
    as_of = now.strftime("%Y-%m-%d %H:%M:%S")
    today = cn_today_str()
    is_trading = is_cn_trading_day(today)
    previous_trade_date = previous_cn_trading_day(today)

    notes: list[str] = []

    # --- Bucket 1: holdings snapshot ---
    holdings = _collect_holdings_snapshot(db, user_id, previous_trade_date, as_of, is_trading)

    # --- Bucket 2: observation warehouse ---
    observation = _collect_observation_warehouse(as_of, tf_db_path, notes)

    # --- Bucket 3: TradeFlow latest candidates ---
    candidates = _collect_tradeflow_candidates(as_of, tf_db_path, notes)

    # --- Bucket 4: latest TA report summary ---
    # Union of symbols from holdings + observation (pending TA items are
    # surfaced separately in bucket 6 to avoid double-counting).
    report_symbols = _union_symbols(holdings["items"], observation["items"])
    ta_reports = _collect_latest_ta_reports(
        db, user_id, report_symbols, previous_trade_date, as_of, notes
    )

    # --- Bucket 5: data health summary ---
    data_health = _collect_data_health(as_of, tf_db_path, notes)

    # --- Bucket 6: pending TA required items ---
    pending_ta = _collect_pending_ta_required(
        as_of, tf_db_path, candidates.get("trade_date"), notes
    )

    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "as_of": as_of,
        "previous_trade_date": previous_trade_date,
        "is_trading_day": is_trading,
        "generated_by": CONTEXT_SOURCE,
        "read_only": True,
        "holdings": holdings,
        "observation_warehouse": observation,
        "tradeflow_candidates": candidates,
        "latest_ta_reports": ta_reports,
        "data_health": data_health,
        "pending_ta_required": pending_ta,
        "notes": notes,
        "runtime_tier_meta": _tradeflow_meta("investment_controller_context"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Bucket collectors — each returns a dict with: source, as_of, data_status,
# plus bucket-specific fields. Failures degrade to data_status=failed with an
# empty items list; never raises.
# ──────────────────────────────────────────────────────────────────────────────

def _collect_holdings_snapshot(
    db: Session,
    user_id: str,
    previous_trade_date: str,
    as_of: str,
    is_trading_day: bool,
) -> dict[str, Any]:
    """Real imported portfolio positions with live quotes + latest TA report."""
    try:
        rows = _list_imported_position_rows(db, user_id)
    except Exception as exc:
        logger.warning("[ic-ta-001] holdings query failed: %s", exc)
        return _empty_bucket("holdings_snapshot", as_of, DATA_STATUS_FAILED)

    if not rows:
        return _empty_bucket("holdings_snapshot", as_of, DATA_STATUS_MISSING)

    symbols = [row.symbol for row in rows]
    try:
        quotes = _fetch_live_quotes(symbols)
    except Exception as exc:
        logger.warning("[ic-ta-001] holdings live quote fetch failed: %s", exc)
        quotes = {}

    try:
        reports = _select_reports_for_symbols(db, user_id, symbols, previous_trade_date)
    except Exception as exc:
        logger.warning("[ic-ta-001] holdings report lookup failed: %s", exc)
        reports = {}

    items: list[dict[str, Any]] = []
    quotes_with_price = 0
    for row in rows:
        quote = quotes.get(row.symbol, {})
        live_price = _to_float(quote.get("price"))
        if live_price is not None:
            quotes_with_price += 1
        current_position = _to_float(row.current_position)
        average_cost = _to_float(row.average_cost)
        live_market_value = (
            round(live_price * current_position, 2)
            if live_price is not None and current_position is not None
            else _to_float(row.market_value)
        )
        floating_pnl = (
            round((live_price - average_cost) * current_position, 2)
            if live_price is not None and average_cost is not None and current_position is not None
            else None
        )
        floating_pnl_pct = (
            round(((live_price - average_cost) / average_cost) * 100, 2)
            if live_price is not None and average_cost not in (None, 0)
            else None
        )
        report_summary = _serialize_report_summary(reports.get(row.symbol), previous_trade_date)
        items.append({
            "symbol": row.symbol,
            "name": row.security_name or row.symbol,
            "current_position": current_position,
            "available_position": _to_float(row.available_position),
            "average_cost": average_cost,
            "market_value": _to_float(row.market_value),
            "live_market_value": live_market_value,
            "floating_pnl": floating_pnl,
            "floating_pnl_pct": floating_pnl_pct,
            "live_price": live_price,
            "price_change_pct": _to_float(quote.get("change_pct")),
            "quote_time": quote.get("quote_time"),
            "quote_source": quote.get("source"),
            "latest_report": report_summary,
            "source": "imported_portfolio",
            "as_of": as_of,
        })

    # Derive data_status: failed quote fetch on a trading day = stale; non-trading
    # day with no quotes = skipped; otherwise fresh.
    if quotes_with_price == 0:
        data_status = DATA_STATUS_SKIPPED if not is_trading_day else DATA_STATUS_STALE
    else:
        data_status = DATA_STATUS_FRESH

    return {
        "source": "imported_portfolio",
        "as_of": as_of,
        "data_status": data_status,
        "count": len(items),
        "items": items,
    }


def _collect_observation_warehouse(
    as_of: str, tf_db_path: str, notes: list[str]
) -> dict[str, Any]:
    """Active observation warehouse items (excludes ``removed``)."""
    try:
        from api.services.tradeflow_service import get_observation_items
        result = get_observation_items(status=None, include_removed=False, tf_db_path=tf_db_path)
    except Exception as exc:
        logger.warning("[ic-ta-001] observation warehouse query failed: %s", exc)
        return _empty_bucket("observation_warehouse", as_of, DATA_STATUS_FAILED)

    status = result.get("status") if isinstance(result, dict) else "failed"
    items = result.get("items", []) if isinstance(result, dict) else []
    summary = result.get("summary", {}) if isinstance(result, dict) else {}

    if status == "no_data":
        notes.append("observation_warehouse: tradeflow DB unavailable")
        return _empty_bucket("observation_warehouse", as_of, DATA_STATUS_MISSING)

    # Stamp each item with source + as_of (fact, not controller recommendation).
    stamped: list[dict[str, Any]] = []
    for item in items:
        entry = dict(item)
        entry["source"] = "observation_warehouse"
        entry["as_of"] = as_of
        stamped.append(entry)

    data_status = DATA_STATUS_FRESH if stamped else DATA_STATUS_MISSING
    return {
        "source": "observation_warehouse",
        "as_of": as_of,
        "data_status": data_status,
        "count": len(stamped),
        "summary": summary,
        "items": stamped,
    }


def _collect_tradeflow_candidates(
    as_of: str, tf_db_path: str, notes: list[str]
) -> dict[str, Any]:
    """Latest TradeFlow candidate pool (most recent plan_date)."""
    try:
        from api.services.tradeflow_service import get_data_health, get_candidates
        health = get_data_health(tf_db_path=tf_db_path)
    except Exception as exc:
        logger.warning("[ic-ta-001] tradeflow data-health lookup failed: %s", exc)
        return _empty_bucket("tradeflow_candidates", as_of, DATA_STATUS_FAILED)

    trade_date = (
        health.get("latest_effective_trade_date")
        or health.get("latest_candidates_date")
        or health.get("latest_plan_date")
    )

    if not trade_date:
        if not health.get("tradeflow_db_available", False):
            notes.append("tradeflow_candidates: tradeflow DB unavailable")
            return _empty_bucket("tradeflow_candidates", as_of, DATA_STATUS_MISSING)
        notes.append("tradeflow_candidates: no plan_date recorded yet")
        return _empty_bucket("tradeflow_candidates", as_of, DATA_STATUS_MISSING)

    try:
        cand = get_candidates(trade_date, tf_db_path=tf_db_path)
    except Exception as exc:
        logger.warning("[ic-ta-001] tradeflow candidates query failed: %s", exc)
        return _empty_bucket("tradeflow_candidates", as_of, DATA_STATUS_FAILED)

    candidates = cand.get("candidates", []) if isinstance(cand, dict) else []
    pool_counts = cand.get("pool_counts", {}) if isinstance(cand, dict) else {}
    summary_agg = cand.get("summary_agg", {}) if isinstance(cand, dict) else {}

    # Slim each candidate to the soft-state fields the controller needs.
    slim: list[dict[str, Any]] = []
    for c in candidates:
        slim.append({
            "symbol": c.get("symbol", ""),
            "name": c.get("name", ""),
            "candidate_type": c.get("candidate_type", ""),
            "tier": c.get("tier", ""),
            "composite_score": c.get("composite_score"),
            "primary_strategy": c.get("primary_strategy", ""),
            "strategy_tags": c.get("strategy_tags", []),
            "trigger_price": c.get("trigger_price"),
            "support_price": c.get("support_price"),
            "invalid_price": c.get("invalid_price"),
            "need_deep_ta": bool(c.get("need_deep_ta", False)),
            "deep_ta_status": c.get("deep_ta_status", ""),
            "action_tier": c.get("action_tier", ""),
            "reason": c.get("reason", ""),
            "source": "tradeflow_candidates",
            "as_of": as_of,
        })

    data_status = DATA_STATUS_FRESH if slim else DATA_STATUS_MISSING
    return {
        "source": "tradeflow_candidates",
        "as_of": as_of,
        "trade_date": trade_date,
        "data_status": data_status,
        "count": len(slim),
        "pool_counts": pool_counts,
        "summary_agg": summary_agg,
        "items": slim,
    }


def _collect_latest_ta_reports(
    db: Session,
    user_id: str,
    symbols: list[str],
    previous_trade_date: str,
    as_of: str,
    notes: list[str],
) -> dict[str, Any]:
    """Latest completed TA report per symbol (structured facts only)."""
    if not symbols:
        return _empty_bucket("latest_ta_reports", as_of, DATA_STATUS_MISSING)

    try:
        reports = _select_reports_for_symbols(db, user_id, symbols, previous_trade_date)
    except Exception as exc:
        logger.warning("[ic-ta-001] TA report lookup failed: %s", exc)
        return _empty_bucket("latest_ta_reports", as_of, DATA_STATUS_FAILED)

    items: list[dict[str, Any]] = []
    for symbol, report in reports.items():
        summary = _serialize_report_summary(report, previous_trade_date)
        if summary is None:
            continue
        entry = dict(summary)
        entry["symbol"] = symbol
        entry["source"] = "ta_report"
        entry["as_of"] = as_of
        items.append(entry)

    data_status = DATA_STATUS_FRESH if items else DATA_STATUS_MISSING
    return {
        "source": "ta_report",
        "as_of": as_of,
        "data_status": data_status,
        "count": len(items),
        "items": items,
    }


def _collect_data_health(
    as_of: str, tf_db_path: str, notes: list[str]
) -> dict[str, Any]:
    """TradeFlow DB health summary (sources, freshness, observe signals)."""
    try:
        from api.services.tradeflow_service import get_data_health
        health = get_data_health(tf_db_path=tf_db_path)
    except Exception as exc:
        logger.warning("[ic-ta-001] data health query failed: %s", exc)
        return _empty_bucket("data_health", as_of, DATA_STATUS_FAILED)

    db_available = bool(health.get("tradeflow_db_available", False))
    if not db_available:
        notes.append("data_health: tradeflow DB unavailable")

    sources = health.get("sources", []) if isinstance(health, dict) else []
    has_failure = any(
        str(src.get("status", "")).upper() in {"FAILED", "ERROR"} for src in sources
    )
    has_data = bool(
        health.get("latest_plan_date")
        or health.get("latest_candidates_date")
        or health.get("total_candidates_today", 0)
    )

    if has_failure:
        data_status = DATA_STATUS_FAILED
    elif not has_data:
        data_status = DATA_STATUS_MISSING if not db_available else DATA_STATUS_MISSING
    else:
        data_status = DATA_STATUS_FRESH

    return {
        "source": "tradeflow_data_health",
        "as_of": as_of,
        "data_status": data_status,
        "tradeflow_db_available": db_available,
        "latest_plan_date": health.get("latest_plan_date"),
        "latest_candidates_date": health.get("latest_candidates_date"),
        "latest_effective_trade_date": health.get("latest_effective_trade_date"),
        "latest_observe_date": health.get("latest_observe_date"),
        "total_candidates_today": health.get("total_candidates_today", 0),
        "total_signals_today": health.get("total_signals_today", 0),
        "latest_observe_check_time": health.get("latest_observe_check_time"),
        "latest_signal_time": health.get("latest_signal_time"),
        "sources": sources,
    }


def _collect_pending_ta_required(
    as_of: str,
    tf_db_path: str,
    candidates_trade_date: str | None,
    notes: list[str],
) -> dict[str, Any]:
    """Symbols flagged as needing deep TA confirmation.

    Sources:
        - observation items with status=ta_required
        - tradeflow candidates with need_deep_ta=True (if a trade_date is known)
    """
    observation_pending: list[dict[str, Any]] = []
    candidate_pending: list[dict[str, Any]] = []

    # Observation items with ta_required status
    try:
        from api.services.tradeflow_service import get_observation_items
        obs_result = get_observation_items(
            status="ta_required", include_removed=False, tf_db_path=tf_db_path
        )
        if isinstance(obs_result, dict):
            for item in obs_result.get("items", []):
                observation_pending.append({
                    "symbol": item.get("symbol", ""),
                    "name": item.get("name", ""),
                    "horizon": item.get("horizon", ""),
                    "reason": item.get("reason", ""),
                    "origin": "observation_warehouse",
                    "source": "observation_warehouse",
                    "as_of": as_of,
                })
    except Exception as exc:
        logger.warning("[ic-ta-001] pending ta_required observation query failed: %s", exc)

    # Candidates flagged need_deep_ta
    if candidates_trade_date:
        try:
            from api.services.tradeflow_service import get_candidates
            cand = get_candidates(
                candidates_trade_date, need_deep_ta=True, tf_db_path=tf_db_path
            )
            if isinstance(cand, dict):
                for c in cand.get("candidates", []):
                    candidate_pending.append({
                        "symbol": c.get("symbol", ""),
                        "name": c.get("name", ""),
                        "candidate_type": c.get("candidate_type", ""),
                        "composite_score": c.get("composite_score"),
                        "deep_ta_status": c.get("deep_ta_status", ""),
                        "reason": c.get("reason", ""),
                        "origin": "tradeflow_candidates",
                        "source": "tradeflow_candidates",
                        "as_of": as_of,
                    })
        except Exception as exc:
            logger.warning("[ic-ta-001] pending need_deep_ta candidate query failed: %s", exc)

    items = observation_pending + candidate_pending
    if not items:
        notes.append("pending_ta_required: no items flagged")
        data_status = DATA_STATUS_MISSING
    else:
        data_status = DATA_STATUS_FRESH

    return {
        "source": "pending_ta_required",
        "as_of": as_of,
        "data_status": data_status,
        "count": len(items),
        "items": items,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _empty_bucket(source: str, as_of: str, data_status: str) -> dict[str, Any]:
    """Stable empty bucket shape used for all no-data / failed fallbacks."""
    if data_status not in ALLOWED_DATA_STATUSES:
        data_status = DATA_STATUS_MISSING
    return {
        "source": source,
        "as_of": as_of,
        "data_status": data_status,
        "count": 0,
        "items": [],
    }


def _union_symbols(*buckets: list[dict[str, Any]]) -> list[str]:
    """Union of symbols across buckets, preserving first-seen order."""
    seen: dict[str, None] = {}
    for bucket in buckets:
        for item in bucket:
            symbol = item.get("symbol")
            if symbol and symbol not in seen:
                seen[symbol] = None
    return list(seen.keys())


def assert_no_strong_action_verbs(payload: dict[str, Any]) -> None:
    """Test helper: assert no synthesised strong action verb leaked into payload.

    Only checks the controller-synthesised fields (notes, data_status, source
    labels). TA report.decision fields are structured facts and intentionally
    allowed to pass through.
    """
    text = str(payload.get("notes", "")) + " " + str(payload.get("generated_by", ""))
    for pattern in _STRONG_ACTION_PATTERNS:
        if pattern in text:
            raise AssertionError(f"strong action verb leaked into context: {pattern!r}")


__all__ = [
    "ALLOWED_DATA_STATUSES",
    "CONTEXT_SCHEMA_VERSION",
    "CONTEXT_SOURCE",
    "assert_no_strong_action_verbs",
    "get_investment_controller_context",
]
