# [IC-TA-001] investment_controller_context
# [IC-TA-002] controller_context_tradeflow_report
# [KB-006] local_knowledge_context_api
# [HY-007] controller_half_year_briefing
"""Read-only context pack for the investment-controller.

Aggregates the stable buckets that the investment-controller (the external
pre/open/post-market scheduler) needs as input:

IC-TA-001 (six core buckets):
    1. holdings snapshot          - real imported portfolio positions
    2. observation warehouse      - symbols awaiting entry confirmation
    3. TradeFlow latest candidates- latest candidate pool summary
    4. latest TA report summary   - most recent completed TA report per symbol
    5. data health summary        - TradeFlow DB freshness + source availability
    6. pending TA required items  - observation items + candidates flagged for TA

IC-TA-002 (mandate radar + report data gaps):
    7. mandate daily report       - H-015 昊天主题日报 digest (rising/cooling
                                    topics, main/observation candidates,
                                    evidence gaps). Read-only, no network call.
    8. recent report data blockers- DATA-021 field-level query_failed /
                                    field_missing aggregation across the user's
                                    most recent TA reports.
    + controller_hints            - soft scheduling hints derived from the
                                    buckets above: which symbols need TA, which
                                    only enter the daily report, and which
                                    should not be pushed because data is
                                    insufficient.

KB-006 (local knowledge hits):
    9. local_knowledge_hits       - Tree Work ``wiki/investment`` background
                                    digest per symbol + cross-symbol theme
                                    query. Read-only, no full page body, no
                                    LLM. Only the ``wiki/investment`` partition
                                    is visible; supports disable via
                                    ``KNOWLEDGE_CONTEXT_DISABLED`` env. Used as
                                    a research-priority hint only — never
                                    changes the strong-action gate.

HY-007 (half-year facts briefing):
    10. half_year_facts           - Per-symbol half-year report (半年报) fact
                                    digest built on HY-003/005/006. Surfaces
                                    fresh-fact updates, thesis-rebuttal
                                    alerts and needs-TA-review flags to the
                                    briefing payload. Read-only, no LLM, no
                                    long original text. Disabled alongside
                                    local knowledge via
                                    ``KNOWLEDGE_CONTEXT_DISABLED``.

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

    # --- Bucket 7 (IC-TA-002): mandate daily report ---
    mandate_report = _collect_mandate_daily_report(as_of, notes)

    # --- Bucket 8 (IC-TA-002): recent report data blockers ---
    report_blockers = _collect_recent_report_data_blockers(
        db, user_id, as_of, notes
    )

    # --- Bucket 9 (KB-006): local knowledge hits ---
    local_knowledge_hits = _collect_local_knowledge_hits(
        as_of,
        holdings=holdings,
        observation=observation,
        candidates=candidates,
        mandate_report=mandate_report,
        notes=notes,
    )

    # --- Bucket 10 (HY-007): half-year facts briefing digest ---
    half_year_facts = _collect_half_year_facts(
        as_of,
        holdings=holdings,
        observation=observation,
        candidates=candidates,
        mandate_report=mandate_report,
        notes=notes,
    )

    # --- Controller hints (IC-TA-002): soft scheduling hints ---
    controller_hints = _build_controller_hints(
        as_of,
        observation=observation,
        pending_ta=pending_ta,
        mandate_report=mandate_report,
        report_blockers=report_blockers,
        local_knowledge_hits=local_knowledge_hits,  # [KB-006]
        half_year_facts=half_year_facts,  # [HY-007]
        notes=notes,
    )

    # --- [B-004] holdings_sync_status ---
    holdings_sync_status = _collect_holdings_sync_status(db, user_id, as_of, notes)

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
        "mandate_daily_report": mandate_report,  # [IC-TA-002]
        "recent_report_data_blockers": report_blockers,  # [IC-TA-002]
        "local_knowledge_hits": local_knowledge_hits,  # [KB-006]
        "half_year_facts": half_year_facts,  # [HY-007]
        "holdings_sync_status": holdings_sync_status,  # [B-004]
        "controller_hints": controller_hints,  # [IC-TA-002]
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
# Bucket 7 (IC-TA-002): mandate daily report
# ──────────────────────────────────────────────────────────────────────────────

def _empty_mandate_bucket(as_of: str, data_status: str) -> dict[str, Any]:
    """Stable empty shape for the mandate_daily_report bucket."""
    return {
        "source": "mandate_daily_report",
        "as_of": as_of,
        "report_as_of": "",
        "data_status": data_status,
        "rising_topic_count": 0,
        "cooling_topic_count": 0,
        "main_candidate_count": 0,
        "observation_candidate_count": 0,
        "evidence_gap_count": 0,
        "rising_topics": [],
        "cooling_topics": [],
        "main_candidates": [],
        "observation_candidates": [],
        "evidence_gaps": [],
    }


def _collect_mandate_daily_report(
    as_of: str, notes: list[str]
) -> dict[str, Any]:
    """H-015 mandate daily report digest (read-only, no network call).

    Delegates to ``tradeflow_service.get_mandate_daily_report`` which either
    reads the latest saved report JSON from disk or rebuilds it from the
    topic heatmap (heatmap only reads the tradeflow SQLite). Never writes,
    never hits the network.
    """
    try:
        from api.services.tradeflow_service import get_mandate_daily_report
        report = get_mandate_daily_report(as_of="", save_report=False)
    except Exception as exc:
        logger.warning("[ic-ta-002] mandate daily report lookup failed: %s", exc)
        return _empty_mandate_bucket(as_of, DATA_STATUS_FAILED)

    if not isinstance(report, dict) or report.get("status") == "no_data":
        notes.append("mandate_daily_report: no mandate report available yet")
        return _empty_mandate_bucket(as_of, DATA_STATUS_MISSING)

    rising = report.get("rising_topics", []) or []
    cooling = report.get("cooling_topics", []) or []
    main_candidates = report.get("main_candidates", []) or []
    observation_candidates = report.get("observation_candidates", []) or []
    evidence_gaps = report.get("evidence_gaps", []) or []

    digest = {
        "source": "mandate_daily_report",
        "as_of": as_of,
        "report_as_of": report.get("as_of") or "",
        "data_status": DATA_STATUS_FRESH,
        "rising_topic_count": len(rising),
        "cooling_topic_count": len(cooling),
        "main_candidate_count": len(main_candidates),
        "observation_candidate_count": len(observation_candidates),
        "evidence_gap_count": len(evidence_gaps),
        # Slimmed topic/candidate digests (facts only, no trade verbs).
        "rising_topics": [
            {
                "topic": t.get("topic", ""),
                "status_label": t.get("status_label", ""),
                "heat_trend_label": t.get("heat_trend_label", ""),
                "candidate_count": t.get("candidate_count", 0),
            }
            for t in rising[:5]
        ],
        "cooling_topics": [
            {
                "topic": t.get("topic", ""),
                "status_label": t.get("status_label", ""),
                "heat_trend_label": t.get("heat_trend_label", ""),
            }
            for t in cooling[:5]
        ],
        "main_candidates": [
            {
                "symbol": c.get("symbol", ""),
                "name": c.get("name", ""),
                "topic": c.get("topic", ""),
                "candidate_type": c.get("candidate_type", ""),
                "mandate_score": c.get("mandate_score"),
                "entry_reason": c.get("entry_reason", ""),
            }
            for c in main_candidates[:8]
        ],
        "observation_candidates": [
            {
                "symbol": c.get("symbol", ""),
                "name": c.get("name", ""),
                "topic": c.get("topic", ""),
                "mandate_score": c.get("mandate_score"),
            }
            for c in observation_candidates[:12]
        ],
        "evidence_gaps": list(evidence_gaps[:12]),
    }
    return digest


# ──────────────────────────────────────────────────────────────────────────────
# Bucket 8 (IC-TA-002): recent report data blockers
# ──────────────────────────────────────────────────────────────────────────────

# How many recent completed reports to scan for data blockers. Kept small so
# the controller context stays inside the FAST_RADAR latency budget.
_RECENT_REPORT_SCAN_LIMIT = 10
# Blocker statuses that constitute a real data gap (severe). Everything else
# (normal_no_data / skipped / not_queried) is informational, not a blocker.
_SEVERE_BLOCKER_STATUSES = frozenset({"query_failed", "field_missing"})


def _empty_blockers_bucket(as_of: str, data_status: str) -> dict[str, Any]:
    """Stable empty shape for the recent_report_data_blockers bucket."""
    return {
        "source": "recent_report_data_blockers",
        "as_of": as_of,
        "data_status": data_status,
        "scanned_report_count": 0,
        "affected_report_count": 0,
        "total_severe_blockers": 0,
        "summary_level": "ok",
        "field_counts": {},
        "affected_symbols": [],
    }


def _collect_recent_report_data_blockers(
    db: Session,
    user_id: str,
    as_of: str,
    notes: list[str],
) -> dict[str, Any]:
    """DATA-021 aggregation: query_failed / field_missing across recent reports.

    Scans the user's most recent completed reports and aggregates the severe
    field-level data blockers. Read-only: never writes, derives blockers from
    stored ``result_data`` (re-running ``attach_report_data_blockers`` when the
    report predates DATA-021).
    """
    try:
        from api.database import ReportDB
        from sqlalchemy.orm import load_only
        rows = (
            db.query(ReportDB)
            .options(load_only(
                ReportDB.id,
                ReportDB.symbol,
                ReportDB.trade_date,
                ReportDB.status,
                ReportDB.action_label,
                ReportDB.research_direction,
                ReportDB.created_at,
                ReportDB.result_data,
            ))
            .filter(
                ReportDB.user_id == user_id,
                ReportDB.status == "completed",
            )
            .order_by(ReportDB.created_at.desc())
            .limit(_RECENT_REPORT_SCAN_LIMIT)
            .all()
        )
    except Exception as exc:
        logger.warning("[ic-ta-002] recent report scan failed: %s", exc)
        return _empty_blockers_bucket(as_of, DATA_STATUS_FAILED)

    if not rows:
        notes.append("recent_report_data_blockers: no completed reports yet")
        return _empty_blockers_bucket(as_of, DATA_STATUS_MISSING)

    from api.services.report_service import attach_report_data_blockers

    affected_symbols: list[dict[str, Any]] = []
    field_counts: dict[str, int] = {}
    total_severe = 0
    scanned = 0

    for row in rows:
        scanned += 1
        result_data = getattr(row, "result_data", None)
        if not isinstance(result_data, dict):
            continue
        # Use stored blockers when present (post-DATA-021), otherwise derive
        # a read-only enriched copy without mutating the ORM row.
        blockers = result_data.get("data_blockers")
        if not isinstance(blockers, list):
            enriched = attach_report_data_blockers(dict(result_data))
            blockers = enriched.get("data_blockers") if isinstance(enriched, dict) else []

        severe_here = [
            b for b in (blockers or [])
            if isinstance(b, dict) and b.get("status") in _SEVERE_BLOCKER_STATUSES
        ]
        if not severe_here:
            continue

        total_severe += len(severe_here)
        field_keys = sorted({str(b.get("key", "")) for b in severe_here if b.get("key")})
        for key in field_keys:
            field_counts[key] = field_counts.get(key, 0) + 1
        affected_symbols.append({
            "symbol": row.symbol,
            "report_id": row.id,
            "trade_date": row.trade_date,
            "severe_count": len(severe_here),
            "fields": field_keys,
            "action_label": row.action_label or "",
            "research_direction": row.research_direction or "",
        })

    summary_level = "warning" if affected_symbols else "ok"

    data_status = DATA_STATUS_FRESH if affected_symbols else (
        DATA_STATUS_FRESH if scanned else DATA_STATUS_MISSING
    )
    # When reports exist but none carry severe blockers, the bucket is still
    # "fresh" (we successfully confirmed there are no data gaps) — surface a
    # note so the controller can tell "scanned & clean" apart from "no data".
    if not affected_symbols:
        notes.append(
            f"recent_report_data_blockers: scanned {scanned} report(s), no severe gaps"
        )

    return {
        "source": "recent_report_data_blockers",
        "as_of": as_of,
        "data_status": data_status,
        "scanned_report_count": scanned,
        "affected_report_count": len(affected_symbols),
        "total_severe_blockers": total_severe,
        "summary_level": summary_level,
        "field_counts": field_counts,
        "affected_symbols": affected_symbols,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Bucket 9 (KB-006): local knowledge hits
# ──────────────────────────────────────────────────────────────────────────────

# Cap how many symbols we run a per-symbol local-knowledge lookup for, to keep
# the IC context inside the FAST_RADAR latency budget. Holdings + observation
# + mandate main candidates are typically well under this cap.
_LOCAL_KNOWLEDGE_SYMBOL_CAP = 12
# Mandate topics to include in the cross-symbol theme query (top-N by score).
_LOCAL_KNOWLEDGE_THEME_CAP = 5


def _empty_local_knowledge_bucket(as_of: str, data_status: str) -> dict[str, Any]:
    """Stable empty shape for the local_knowledge_hits bucket."""
    return {
        "source": "local_knowledge_context",
        "as_of": as_of,
        "data_status": data_status,
        "knowledge_root": "",
        "symbol_count": 0,
        "theme_count": 0,
        "fresh_symbol_count": 0,
        "stale_symbol_count": 0,
        "items": [],
        "theme_query": None,
        "errors": [],
        "read_only": True,
    }


def _collect_local_knowledge_hits(
    as_of: str,
    *,
    holdings: dict[str, Any],
    observation: dict[str, Any],
    candidates: dict[str, Any],
    mandate_report: dict[str, Any],
    notes: list[str],
) -> dict[str, Any]:
    """[KB-006] Tree Work ``wiki/investment`` background digest for the IC.

    Per-symbol KB-003 lookups are run for the union of:
      - holdings items (real positions)
      - observation items (active watchlist)
      - TradeFlow main candidates (top by composite_score)
      - mandate main candidates (H-015 昊天主题日报)

    Plus one cross-symbol theme query built from the mandate report's rising
    topics. All results are slimmed to the controller-safe fields (no full
    page body, only summary/path/updated_at/confidence/risks).

    READ-ONLY: never writes, never calls LLM, never raises. Degrades to
    ``data_status=skipped`` when disabled via env, ``failed`` on lookup
    exception, ``missing`` when nothing was scanned.
    """
    try:
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits as _collect,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("[kb-006] local_knowledge_context_service import failed: %s", exc)
        return _empty_local_knowledge_bucket(as_of, DATA_STATUS_FAILED)

    # Build the symbol union (dedupe, preserve first-seen order, cap to keep
    # the bucket inside FAST_RADAR).
    seen: set[str] = set()
    symbols: list[str] = []

    def _add_symbol(symbol: Any) -> None:
        if len(symbols) >= _LOCAL_KNOWLEDGE_SYMBOL_CAP:
            return
        sym = str(symbol or "").strip()
        if not sym or sym in seen:
            return
        seen.add(sym)
        symbols.append(sym)

    for item in holdings.get("items", []) or []:
        _add_symbol(item.get("symbol"))
    for item in observation.get("items", []) or []:
        _add_symbol(item.get("symbol"))
    for item in (candidates.get("items", []) or [])[:6]:
        _add_symbol(item.get("symbol"))
    if mandate_report.get("data_status") == DATA_STATUS_FRESH:
        for item in mandate_report.get("main_candidates", []) or []:
            _add_symbol(item.get("symbol"))

    # Theme query: rising + cooling topics from the mandate daily report.
    themes: list[str] = []
    if mandate_report.get("data_status") == DATA_STATUS_FRESH:
        for topic in mandate_report.get("rising_topics", []) or []:
            t = str(topic.get("topic") or "").strip()
            if t and t not in themes:
                themes.append(t)
        for topic in mandate_report.get("cooling_topics", []) or []:
            t = str(topic.get("topic") or "").strip()
            if t and t not in themes:
                themes.append(t)
        themes = themes[:_LOCAL_KNOWLEDGE_THEME_CAP]

    try:
        bucket = _collect(
            symbols=symbols,
            themes=themes or None,
            as_of=as_of,
            notes=notes,
            max_symbols=_LOCAL_KNOWLEDGE_SYMBOL_CAP,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[kb-006] collect_local_knowledge_hits failed: %s", exc)
        return _empty_local_knowledge_bucket(as_of, DATA_STATUS_FAILED)

    if not symbols and not themes:
        # No underlying data → treat as skipped (not missing) so the controller
        # can tell "no positions/candidates yet" apart from "knowledge base
        # unreachable".
        if bucket.get("data_status") == DATA_STATUS_MISSING:
            bucket["data_status"] = DATA_STATUS_SKIPPED
            notes.append("local_knowledge_hits: no symbols/themes to query")
    return bucket


# ──────────────────────────────────────────────────────────────────────────────
# Bucket 10 (HY-007): half-year facts briefing digest
# ──────────────────────────────────────────────────────────────────────────────

# [HY-007] controller_half_year_briefing
#
# 把 HY-003 半年报事实表、HY-005 观点-事实反证、HY-006 因子分聚合到一个
# IC-friendly bucket，让 investment-controller 的盘前/盘后 briefing 能看到
# "哪只票有半年报更新、哪只票的投资逻辑被事实打脸、哪只票需要 TA 复核"。
#
# 设计契约（对齐 docs/TASKS.md HY-007）：
#   - 只读：不写 DB / 不调 LLM / 不访问外网 / 不抛异常。
#   - 不输出长原文：所有文本字段来自上游 pre-clipped 字段（HY-003 raw 120、
#     HY-006 summary 200、HY-005 inline 单行）。
#   - 缺数据只记录不推送：FAILED/无 knowledge_root 时退化为稳定空结构
#     （data_status=skipped/failed），不让"无半年报"刷屏。
#   - 不下交易结论：bucket 只承载事实摘要与软提醒，不强买卖词；与动作语义
#     门禁解耦。
#   - 与 KB-006 共享 disable 开关：KNOWLEDGE_CONTEXT_DISABLED=1 同时关掉本地
#     知识与半年报 bucket，避免半套知识链路。

# 控制符号数量上限，保持 FAST_RADAR 时延预算（HY-003 按符号扫描文件）。
_HALF_YEAR_SYMBOL_CAP = 10
# 单个 origin 类别最多贡献的 symbol 数（避免某一类淹没其他类）。
_HALF_YEAR_PER_ORIGIN_CAP = 6
# 每个符号透出的 risk_preview / downgrade_reasons 上限。
_HALF_YEAR_RISK_PREVIEW_LIMIT = 3
_HALF_YEAR_DOWNGRADE_PREVIEW_LIMIT = 3
# fact_summary_text / thesis_inline 裁剪上限（防御性，正常已 pre-clipped）。
_HALF_YEAR_TEXT_CLIP = 200


def _empty_half_year_bucket(as_of: str, data_status: str) -> dict[str, Any]:
    """Stable empty shape for the half_year_facts bucket."""
    return {
        "source": "half_year_facts_context",
        "as_of": as_of,
        "data_status": data_status,
        "knowledge_root": "",
        "symbol_count": 0,
        "fresh_fact_count": 0,
        "contradicted_count": 0,
        "weakened_count": 0,
        "needs_review_count": 0,
        "items": [],
        "errors": [],
        "read_only": True,
    }


def _half_year_status_token(status: Any, data_status: Any) -> str:
    """把 HY-003 (status, data_status) 映射为简短 filter token.

    与 report_service._half_year_status_to_short 同口径，但本模块避免在
    热路径里 import report_service（service 层互相依赖会让测试难以隔离）。
    NO_DATA 表示"知识库扫过但没命中半年报页"（gap），与 FAILED（基础设施
    故障）严格区分。
    """
    s = str(status or "")
    ds = str(data_status or "")
    if s in ("FAILED", "failed"):
        return "FAILED"
    if s in ("NORMAL_NO_DATA", "normal_no_data"):
        return "NO_DATA"
    if s in ("STALE", "stale"):
        return "STALE"
    if s in ("LOW_CONFIDENCE", "low_confidence"):
        return "LOW_CONFIDENCE"
    if ds == "conflict" or ds == "DATA_CONFLICT":
        return "CONFLICT"
    if s in ("HAS_DATA", "has_data"):
        return "HAS_FACTS"
    return "NO_DATA"


def _half_year_item_origin(symbol: str, origins: dict[str, str]) -> str:
    """Resolve which origin bucket first surfaced a symbol."""
    return origins.get(symbol, "unknown")


def _build_half_year_symbol_universe(
    *,
    holdings: dict[str, Any],
    observation: dict[str, Any],
    candidates: dict[str, Any],
    mandate_report: dict[str, Any],
) -> tuple[list[str], dict[str, str]]:
    """Build the (symbol, origin) universe for the half-year lookup.

    Priority: holdings > observation > candidates > mandate. Caps total at
    ``_HALF_YEAR_SYMBOL_CAP`` and per-origin at
    ``_HALF_YEAR_PER_ORIGIN_CAP`` to stay inside FAST_RADAR.
    """
    seen: set[str] = set()
    symbols: list[str] = []
    origins: dict[str, str] = {}

    def _add(symbol: Any, origin: str) -> None:
        if len(symbols) >= _HALF_YEAR_SYMBOL_CAP:
            return
        # 每个 origin 最多贡献固定数量，避免某类淹没其他。
        origin_count = sum(1 for s in symbols if origins.get(s) == origin)
        if origin_count >= _HALF_YEAR_PER_ORIGIN_CAP:
            return
        sym = str(symbol or "").strip()
        if not sym or sym in seen:
            return
        seen.add(sym)
        symbols.append(sym)
        origins[sym] = origin

    for item in holdings.get("items", []) or []:
        _add(item.get("symbol"), "holdings")
    for item in observation.get("items", []) or []:
        _add(item.get("symbol"), "observation_warehouse")
    for item in (candidates.get("items", []) or [])[:6]:
        _add(item.get("symbol"), "tradeflow_candidates")
    if mandate_report.get("data_status") == DATA_STATUS_FRESH:
        for item in mandate_report.get("main_candidates", []) or []:
            _add(item.get("symbol"), "mandate_daily_report")
    return symbols, origins


def _collect_half_year_facts(
    as_of: str,
    *,
    holdings: dict[str, Any],
    observation: dict[str, Any],
    candidates: dict[str, Any],
    mandate_report: dict[str, Any],
    notes: list[str],
) -> dict[str, Any]:
    """[HY-007] Per-symbol half-year fact + rebuttal + needs-review digest.

    For each symbol in holdings ∪ observation ∪ candidates ∪ mandate main
    candidates (capped), runs a read-only HY-003 query; when fresh facts are
    available, additionally runs HY-005 thesis check and HY-006 factor score.
    The bucket degrades to ``data_status=skipped`` when the knowledge context
    is disabled, ``failed`` on lookup exception, ``missing`` when no symbol
    has any half-year data.
    """
    try:
        from api.services.local_knowledge_context_service import (
            is_local_knowledge_disabled,
            resolve_knowledge_root,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("[hy-007] local_knowledge_context_service import failed: %s", exc)
        return _empty_half_year_bucket(as_of, DATA_STATUS_FAILED)

    if is_local_knowledge_disabled():
        notes.append("half_year_facts: disabled by config")
        bucket = _empty_half_year_bucket(as_of, DATA_STATUS_SKIPPED)
        bucket["errors"] = ["half_year_facts disabled by config"]
        return bucket

    knowledge_root = resolve_knowledge_root() or ""
    if not knowledge_root:
        notes.append("half_year_facts: knowledge_root empty, skipped")
        return _empty_half_year_bucket(as_of, DATA_STATUS_SKIPPED)

    symbols, origins = _build_half_year_symbol_universe(
        holdings=holdings,
        observation=observation,
        candidates=candidates,
        mandate_report=mandate_report,
    )
    if not symbols:
        notes.append("half_year_facts: no symbols to query")
        bucket = _empty_half_year_bucket(as_of, DATA_STATUS_SKIPPED)
        bucket["errors"] = ["no symbols to query"]
        return bucket

    try:
        from tradingagents.dataflows.half_year_facts_provider import (
            query_half_year_facts as _hy003_query,
        )
        from tradingagents.dataflows.half_year_factor_score import (
            compute_half_year_factor_score as _hy006_score,
        )
        from tradingagents.dataflows.research_fact_opinion_index import (
            build_research_fact_opinion_index as _kb015_build,
        )
        from tradingagents.dataflows.thesis_fact_check import (
            check_thesis_against_facts as _hy005_check,
            render_thesis_check_inline as _hy005_inline,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("[hy-007] HY-003/005/006 import failed: %s", exc)
        return _empty_half_year_bucket(as_of, DATA_STATUS_FAILED)

    # 持仓/观察仓优先于候选池：把 symbol 与 name 都记下，便于 HY-003 命中。
    name_lookup: dict[str, str] = {}
    for pool in (holdings, observation, candidates):
        for item in pool.get("items", []) or []:
            sym = str(item.get("symbol") or "").strip()
            nm = str(item.get("name") or "").strip()
            if sym and nm and sym not in name_lookup:
                name_lookup[sym] = nm
    if mandate_report.get("data_status") == DATA_STATUS_FRESH:
        for item in mandate_report.get("main_candidates", []) or []:
            sym = str(item.get("symbol") or "").strip()
            nm = str(item.get("name") or "").strip()
            if sym and nm and sym not in name_lookup:
                name_lookup[sym] = nm

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    fresh_count = 0
    contradicted_count = 0
    weakened_count = 0
    needs_review_count = 0

    for symbol in symbols:
        name = name_lookup.get(symbol, "")
        try:
            facts_result = _hy003_query(
                knowledge_root, symbol=symbol, name=name or None
            )
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{symbol}: HY-003 query failed: {type(exc).__name__}")
            continue

        # 把 (status, data_status) 映射成 IC-friendly token。
        facts_status = _half_year_status_token(
            getattr(facts_result, "status", None),
            getattr(facts_result, "data_status", None),
        )
        fact_pages = getattr(facts_result, "pages", None) or []
        has_conflict = facts_status == "CONFLICT" or any(
            str(
                page.get("data_status")
                if isinstance(page, dict)
                else getattr(page, "data_status", "")
            ).lower() == "conflict"
            for page in fact_pages
        )
        # HY-003 aggregate status prefers any fresh page over conflict pages.
        # For briefing safety, a mixed fresh/conflict result must still enter
        # the review lane and must not be used for thesis comparison.
        if has_conflict:
            facts_status = "CONFLICT"

        thesis_result = None
        # 只在事实可用时跑 HY-005（与 HY-006 _enrich 一致），避免无谓的
        # KB-015 索引构建。FAILED/NO_DATA/STALE 都不触发反证。
        if facts_result is not None and facts_status == "HAS_FACTS":
            try:
                opinion_index = _kb015_build(
                    knowledge_root, symbol=symbol, name=name or None
                )
                thesis_result = _hy005_check(
                    opinion_index, facts_result,
                    symbol=symbol, name=name or "",
                )
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(
                    f"{symbol}: HY-005 thesis check failed: {type(exc).__name__}"
                )
                thesis_result = None

        try:
            score_dict = _hy006_score(facts_result, thesis_result)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{symbol}: HY-006 score failed: {type(exc).__name__}")
            continue

        thesis_status = str(score_dict.get("thesis_check_status") or "")
        thesis_inline = ""
        if thesis_result is not None:
            try:
                thesis_inline = _hy005_inline(thesis_result) or ""
            except Exception:  # pragma: no cover - defensive
                thesis_inline = ""

        # 只把"有事实"或"有反证提醒"或"需要 TA 复核"的标的纳入 items；
        # 纯 NO_DATA/FAILED 不进 items（避免"没有半年报"变成噪音）。
        has_fresh = bool(score_dict.get("has_fresh_facts")) and not has_conflict
        needs_tree_review = bool(getattr(thesis_result, "needs_tree_work_review", False))
        priority_reminder = bool(getattr(thesis_result, "priority_reminder", False))
        # Conflicting facts are usable for surfacing the discrepancy, but are
        # not a fresh fact update. Force them into the review lane so the
        # briefing cannot present one arbitrary value as confirmed evidence.
        needs_research_review = bool(score_dict.get("needs_research_review")) or has_conflict
        is_rebuttal = thesis_status in ("contradicted", "weakened")
        is_needs_review = needs_tree_review or needs_research_review
        if not (has_fresh or is_rebuttal or is_needs_review):
            continue

        if has_fresh:
            fresh_count += 1
        if thesis_status == "contradicted":
            contradicted_count += 1
        elif thesis_status == "weakened":
            weakened_count += 1
        if is_needs_review:
            needs_review_count += 1

        risk_preview = [
            str(r)[:80]
            for r in (score_dict.get("half_year_risk_flags") or [])
            if r
        ][:_HALF_YEAR_RISK_PREVIEW_LIMIT]
        downgrade_preview = [
            str(r)[:_HALF_YEAR_DOWNGRADE_PREVIEW_LIMIT * 20]
            for r in (score_dict.get("downgrade_reasons") or [])
            if r
        ][:_HALF_YEAR_DOWNGRADE_PREVIEW_LIMIT]
        # 防御性裁剪（上游已 pre-clip，但 hybrid 输入可能超出）。
        fact_summary_text = str(score_dict.get("half_year_fact_summary") or "")
        half_year_score = float(score_dict.get("half_year_fact_score") or 0.0)
        if has_conflict:
            conflict_note = "半年报事实存在冲突，需 Tree Work 复核"
            fact_summary_text = (
                f"{conflict_note}；{fact_summary_text}" if fact_summary_text
                else conflict_note
            )
            half_year_score = min(half_year_score, 0.0)
            if conflict_note not in risk_preview:
                risk_preview = [conflict_note, *risk_preview][:_HALF_YEAR_RISK_PREVIEW_LIMIT]
            if conflict_note not in downgrade_preview:
                downgrade_preview = [conflict_note, *downgrade_preview][:_HALF_YEAR_DOWNGRADE_PREVIEW_LIMIT]
        fact_summary_text = fact_summary_text[:_HALF_YEAR_TEXT_CLIP]
        thesis_inline = thesis_inline[:_HALF_YEAR_TEXT_CLIP]

        items.append({
            "symbol": symbol,
            "name": name,
            "origin": _half_year_item_origin(symbol, origins),
            "facts_status": facts_status,
            "latest_period": score_dict.get("fact_period") or "",
            "latest_disclosure_date": getattr(facts_result, "latest_disclosure_date", None) or "",
            "data_status": (
                DATA_STATUS_FRESH if has_fresh
                else DATA_STATUS_STALE if facts_status in ("STALE", "CONFLICT", "LOW_CONFIDENCE")
                else DATA_STATUS_MISSING
            ),
            "has_conflict": has_conflict,
            "has_stale": facts_status in ("STALE", "LOW_CONFIDENCE"),
            "half_year_score": half_year_score,
            "fact_summary_text": fact_summary_text,
            "thesis_check_status": thesis_status,
            "thesis_inline": thesis_inline,
            "needs_tree_work_review": needs_tree_review,
            "needs_research_review": needs_research_review,
            "priority_reminder": priority_reminder,
            "downgrade_reasons": downgrade_preview,
            "risk_preview": risk_preview,
            "source": "half_year_facts_provider",
            "as_of": as_of,
        })

    # Derive overall data_status:
    #   FRESH if any symbol has fresh facts or rebuttal.
    #   STALE if items exist but none fresh.
    #   MISSING if no items and no errors.
    #   FAILED if errors but no items.
    if items:
        data_status = DATA_STATUS_FRESH if fresh_count or contradicted_count else DATA_STATUS_STALE
    elif errors:
        data_status = DATA_STATUS_FAILED
    else:
        data_status = DATA_STATUS_MISSING
        notes.append("half_year_facts: no symbol has fresh facts / rebuttal / needs-review")

    if not items:
        notes.append(
            f"half_year_facts: scanned {len(symbols)} symbols, 0 surfaced "
            "(no fresh facts / rebuttal / needs-review)"
        )

    return {
        "source": "half_year_facts_context",
        "as_of": as_of,
        "data_status": data_status,
        "knowledge_root": "",  # 不暴露完整路径，与 KB-006 一致
        "symbol_count": len(items),
        "scanned_symbol_count": len(symbols),
        "fresh_fact_count": fresh_count,
        "contradicted_count": contradicted_count,
        "weakened_count": weakened_count,
        "needs_review_count": needs_review_count,
        "items": items,
        "errors": errors,
        "read_only": True,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Controller hints (IC-TA-002): soft scheduling guidance
# ──────────────────────────────────────────────────────────────────────────────

# Observation statuses that only justify a daily-report entry, not an active
# push or a TA trigger.
_DAILY_REPORT_ONLY_OBS_STATUSES = frozenset({"watching"})


def _build_controller_hints(
    as_of: str,
    *,
    observation: dict[str, Any],
    pending_ta: dict[str, Any],
    mandate_report: dict[str, Any],
    report_blockers: dict[str, Any],
    local_knowledge_hits: dict[str, Any] | None = None,  # [KB-006]
    half_year_facts: dict[str, Any] | None = None,  # [HY-007]
    notes: list[str],
) -> dict[str, Any]:
    """Derive soft scheduling hints from the already-collected buckets.

    Three lanes (facts only, no trade verbs):
        - ``needs_ta``: observation/candidate symbols flagged for deep TA.
        - ``daily_report_only``: soft items that only enter the daily digest
          (passive observation + mandate observation candidates).
        - ``suppress_push_data_insufficient``: symbols whose latest report
          carried severe data blockers — the controller should not push them
          as confident conclusions.

    The hints never carry a strong buy/sell verb; they only route attention.
    """
    needs_ta: list[dict[str, Any]] = []
    seen_ta: set[str] = set()
    for item in pending_ta.get("items", []):
        symbol = item.get("symbol", "")
        if not symbol or symbol in seen_ta:
            continue
        seen_ta.add(symbol)
        needs_ta.append({
            "symbol": symbol,
            "name": item.get("name", ""),
            "origin": item.get("origin", ""),
            "reason": item.get("reason", ""),
            "suggested_next_step": "consider_light_or_full_ta",
            "source": "pending_ta_required",
            "as_of": as_of,
        })

    daily_only: list[dict[str, Any]] = []
    seen_daily: set[str] = set()
    for item in observation.get("items", []):
        symbol = item.get("symbol", "")
        status = item.get("status", "")
        if not symbol or symbol in seen_daily:
            continue
        if status in _DAILY_REPORT_ONLY_OBS_STATUSES:
            seen_daily.add(symbol)
            daily_only.append({
                "symbol": symbol,
                "name": item.get("name", ""),
                "origin": "observation_warehouse",
                "reason": item.get("reason", "") or "passive observation, daily digest only",
                "suggested_next_step": "daily_report_only",
                "source": "observation_warehouse",
                "as_of": as_of,
            })

    # Mandate radar observation candidates are daily-digest by design.
    mandate_ok = mandate_report.get("data_status") == DATA_STATUS_FRESH
    if mandate_ok:
        for cand in mandate_report.get("observation_candidates", []):
            symbol = cand.get("symbol", "")
            if not symbol or symbol in seen_daily:
                continue
            seen_daily.add(symbol)
            daily_only.append({
                "symbol": symbol,
                "name": cand.get("name", ""),
                "origin": "mandate_observation",
                "reason": f"主题：{cand.get('topic', '') or '未知'}，观察候选",
                "suggested_next_step": "daily_report_only",
                "source": "mandate_daily_report",
                "as_of": as_of,
            })

    suppress_push: list[dict[str, Any]] = []
    seen_suppress: set[str] = set()
    for entry in report_blockers.get("affected_symbols", []):
        symbol = entry.get("symbol", "")
        if not symbol or symbol in seen_suppress:
            continue
        seen_suppress.add(symbol)
        suppress_push.append({
            "symbol": symbol,
            "name": "",
            "origin": "recent_report_data_blockers",
            "reason": (
                f"最近报告存在 {entry.get('severe_count', 0)} 项关键数据缺口"
                f"（{', '.join(entry.get('fields', [])[:3])}），不作为强结论推送"
            ),
            "fields": entry.get("fields", []),
            "suggested_next_step": "suppress_push_data_insufficient",
            "source": "recent_report_data_blockers",
            "as_of": as_of,
        })

    # [KB-006] local-knowledge-driven research-review lane.
    # Symbols whose local knowledge is stale / low-confidence / outdated get a
    # soft "needs research review" hint. Symbols with fresh hits get a
    # background-supplement note. Neither changes the strong-action gate.
    research_review: list[dict[str, Any]] = []
    seen_review: set[str] = set()
    if isinstance(local_knowledge_hits, dict):
        for entry in local_knowledge_hits.get("items", []) or []:
            symbol = entry.get("symbol", "")
            if not symbol or symbol in seen_review:
                continue
            stale = int(entry.get("stale_hit_count") or 0)
            low = int(entry.get("low_confidence_hit_count") or 0)
            fresh = int(entry.get("fresh_hit_count") or 0)
            total = int(entry.get("hit_count") or 0)
            if total == 0:
                continue
            seen_review.add(symbol)
            if fresh > 0:
                research_review.append({
                    "symbol": symbol,
                    "name": "",
                    "origin": "local_knowledge_hits",
                    "reason": (
                        f"本地知识命中 {total} 条（fresh {fresh}），可作为背景补充"
                    ),
                    "suggested_next_step": "background_supplement",
                    "source": "local_knowledge_context",
                    "as_of": as_of,
                })
            else:
                research_review.append({
                    "symbol": symbol,
                    "name": "",
                    "origin": "local_knowledge_hits",
                    "reason": (
                        f"本地知识命中 {total} 条但均为过期/低置信"
                        f"（stale {stale}、low {low}），待研究补强"
                    ),
                    "suggested_next_step": "needs_research_review",
                    "source": "local_knowledge_context",
                    "as_of": as_of,
                })

    has_any = bool(needs_ta or daily_only or suppress_push or research_review)
    if not has_any:
        notes.append("controller_hints: no routing hints derived")

    # [HY-007] half-year alerts lane — 与持仓/观察仓优先级联动。
    # 只承载事实摘要 + 软提醒（fact_update / rebuttal_alert / needs_ta_review），
    # 不下交易结论，不强买卖词。
    half_year_alerts: list[dict[str, Any]] = []
    if isinstance(half_year_facts, dict):
        half_year_alerts = _build_half_year_alert_lane(half_year_facts, as_of)
        if half_year_alerts:
            has_any = True

    return {
        "source": "controller_hints",
        "as_of": as_of,
        "data_status": DATA_STATUS_FRESH if has_any else DATA_STATUS_MISSING,
        "needs_ta": needs_ta,
        "daily_report_only": daily_only,
        "suppress_push_data_insufficient": suppress_push,
        "research_review": research_review,  # [KB-006]
        "half_year_alerts": half_year_alerts,  # [HY-007]
    }


def _build_half_year_alert_lane(
    half_year_bucket: dict[str, Any], as_of: str
) -> list[dict[str, Any]]:
    """[HY-007] 把 bucket 10 items 分类成三条软提醒 lane（不下交易结论）.

    分类规则（与 briefing payload 的 reminder 分类一致）：
      - ``rebuttal_alert``：thesis_check_status ∈ {contradicted, weakened}。
      - ``needs_ta_review``：needs_tree_work_review 或 needs_research_review。
      - ``fact_update``：有 fresh facts 且不属上面两类。
    每条只带事实摘要（无强买卖词），用于 controller_hints 联动。
    """
    alerts: list[dict[str, Any]] = []
    for item in half_year_bucket.get("items", []) or []:
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            continue
        thesis_status = str(item.get("thesis_check_status") or "")
        needs_review = bool(
            item.get("needs_tree_work_review") or item.get("needs_research_review")
        )
        if thesis_status in ("contradicted", "weakened"):
            kind = "rebuttal_alert"
            reason = item.get("thesis_inline") or item.get("fact_summary_text") or (
                f"半年报反证提醒：thesis_check_status={thesis_status}"
            )
            next_step = "建议回 Tree Work 复核，必要时安排 TA 重评"
        elif needs_review:
            kind = "needs_ta_review"
            reason = item.get("fact_summary_text") or "半年报提示需要 TA 复核"
            next_step = "安排轻量/完整 TA 复核，结合半年报事实重判"
        else:
            kind = "fact_update"
            reason = item.get("fact_summary_text") or "半年报事实更新"
            next_step = "盘后日报展示，结合持仓/观察仓优先级联动"
        alerts.append({
            "symbol": symbol,
            "name": item.get("name", ""),
            "origin": item.get("origin", "half_year_facts"),
            "kind": kind,
            "reason": str(reason)[:240],
            "suggested_next_step": next_step,
            "facts_status": item.get("facts_status", ""),
            "thesis_check_status": thesis_status,
            "latest_period": item.get("latest_period", ""),
            "half_year_score": item.get("half_year_score", 0.0),
            "source": "half_year_facts_context",
            "as_of": as_of,
        })
    return alerts


def _collect_holdings_sync_status(
    db: Session,
    user_id: str,
    as_of: str,
    notes: list[str],
) -> dict[str, Any]:
    """[B-004] Lightweight sync status for the external holdings file.

    Read-only — does not trigger sync, only reports whether the external
    file exists and how it differs from TA holdings.
    """
    try:
        from api.services import holdings_sync_service
        status = holdings_sync_service.get_sync_status(db, user_id)
    except Exception as exc:
        logger.warning("[b-004] holdings sync status check failed: %s", exc)
        return {
            "source": "holdings_sync",
            "as_of": as_of,
            "data_status": DATA_STATUS_FAILED,
            "file_exists": False,
            "ta_count": 0,
            "external_count": 0,
        }

    file_exists = status.get("file_exists", False)
    file_error = status.get("file_error")
    diff = status.get("diff", {})

    if file_error and file_error != "file_not_found":
        data_status = DATA_STATUS_FAILED
    elif not file_exists:
        data_status = DATA_STATUS_MISSING
    else:
        ta_count = status.get("ta_count", 0)
        ext_count = status.get("external_count", 0)
        if ta_count == 0 and ext_count == 0:
            data_status = DATA_STATUS_MISSING
        else:
            data_status = DATA_STATUS_FRESH

    return {
        "source": "holdings_sync",
        "as_of": as_of,
        "data_status": data_status,
        "file_path": status.get("file_path", ""),
        "file_exists": file_exists,
        "file_error": file_error,
        "ta_count": status.get("ta_count", 0),
        "external_count": status.get("external_count", 0),
        "ta_only_count": diff.get("ta_only_count", 0),
        "external_only_count": diff.get("external_only_count", 0),
        "both_changed_count": diff.get("both_changed_count", 0),
        "identical_count": diff.get("identical_count", 0),
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

    Only checks the controller-synthesised fields (notes, generated_by,
    controller_hints reasons, mandate/blocker digests). TA report.decision
    fields are structured facts and intentionally allowed to pass through.
    """
    chunks: list[str] = [
        str(payload.get("notes", "")),
        str(payload.get("generated_by", "")),
    ]
    # Controller-synthesised hint reasons (IC-TA-002 + KB-006 + HY-007) — never carry
    # trade verbs.
    hints = payload.get("controller_hints") or {}
    if isinstance(hints, dict):
        for lane in (
            "needs_ta",
            "daily_report_only",
            "suppress_push_data_insufficient",
            "research_review",  # [KB-006]
            "half_year_alerts",  # [HY-007]
        ):
            for item in hints.get(lane, []) or []:
                if isinstance(item, dict):
                    chunks.append(str(item.get("reason", "")))
                    chunks.append(str(item.get("suggested_next_step", "")))
    # [KB-006] local_knowledge_hits synthesised text (summary_lines / errors /
    # score summaries) must also stay free of trade verbs.
    local_hits = payload.get("local_knowledge_hits") or {}
    if isinstance(local_hits, dict):
        for line in local_hits.get("errors") or []:
            chunks.append(str(line))
        theme_q = local_hits.get("theme_query") or {}
        if isinstance(theme_q, dict):
            for line in theme_q.get("summary_lines") or []:
                chunks.append(str(line))
        for item in local_hits.get("items") or []:
            if isinstance(item, dict):
                score = item.get("score") or {}
                if isinstance(score, dict):
                    chunks.append(str(score.get("local_knowledge_summary") or ""))
    # [HY-007] half_year_facts synthesised text (fact_summary_text /
    # thesis_inline / downgrade_reasons / risk_preview) must stay free of
    # trade verbs.
    half_year = payload.get("half_year_facts") or {}
    if isinstance(half_year, dict):
        for line in half_year.get("errors") or []:
            chunks.append(str(line))
        for item in half_year.get("items") or []:
            if isinstance(item, dict):
                chunks.append(str(item.get("fact_summary_text") or ""))
                chunks.append(str(item.get("thesis_inline") or ""))
                for reason in item.get("downgrade_reasons") or []:
                    chunks.append(str(reason))
                for risk in item.get("risk_preview") or []:
                    chunks.append(str(risk))
    for pattern in _STRONG_ACTION_PATTERNS:
        for chunk in chunks:
            if pattern in chunk:
                raise AssertionError(f"strong action verb leaked into context: {pattern!r}")


__all__ = [
    "ALLOWED_DATA_STATUSES",
    "CONTEXT_SCHEMA_VERSION",
    "CONTEXT_SOURCE",
    "assert_no_strong_action_verbs",
    "get_investment_controller_context",
]
