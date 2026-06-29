# [TRACK-002] tracking_board_v2_groups
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, load_only

from api.database import ImportedPortfolioPositionDB, ReportDB
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.trade_calendar import cn_today_str, is_cn_trading_day, previous_cn_trading_day
# [TRACK-004] observation_state_engine
from tradingagents.tradeflow.observation_state_engine import (
    STATE_IN_ENTRY_ZONE,
    STATE_NEAR_ENTRY,
    STATE_MISSED_ENTRY,
    STATE_TA_REQUIRED,
    evaluate_observation_state,
    result_to_guidance,
    should_emit_guidance,
)
# [TRACK-005] post_market_tracking_review
from tradingagents.tradeflow.post_market_tracking_review import (
    build_post_market_tracking_review,
    sanitize_review_summary_text,  # [TRACK-010] review_encoding_regression
)


REFRESH_INTERVAL_SECONDS = 20
logger = logging.getLogger(__name__)


def get_tracking_board(db: Session, user_id: str) -> dict[str, Any]:
    previous_trade_date = previous_cn_trading_day(cn_today_str())
    rows = _list_imported_position_rows(db, user_id)
    symbols = [row.symbol for row in rows]
    quotes = _fetch_live_quotes(symbols)
    reports = _select_reports_for_symbols(db, user_id, symbols, previous_trade_date)

    items: list[dict[str, Any]] = []
    for row in rows:
        quote = quotes.get(row.symbol, {})
        live_price = _to_float(quote.get("price"))
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

        items.append(
            {
                "symbol": row.symbol,
                "name": row.security_name or row.symbol,
                "current_position": _to_float(row.current_position),
                "available_position": _to_float(row.available_position),
                "average_cost": average_cost,
                "market_value": _to_float(row.market_value),
                "current_position_pct": _to_float(row.current_position_pct),
                "live_market_value": live_market_value,
                "floating_pnl": floating_pnl,
                "floating_pnl_pct": floating_pnl_pct,
                "live_price": live_price,
                "day_open": _to_float(quote.get("open")),
                "price_change": _to_float(quote.get("change")),
                "price_change_pct": _to_float(quote.get("change_pct")),
                "day_high": _to_float(quote.get("high")),
                "day_low": _to_float(quote.get("low")),
                "previous_close": _to_float(quote.get("previous_close")),
                "volume": _to_float(quote.get("volume")),
                "amount": _to_float(quote.get("amount")),
                "quote_time": quote.get("quote_time"),
                "quote_source": quote.get("source"),
                "last_imported_at": row.last_imported_at.isoformat() if row.last_imported_at else None,
                "analysis": _serialize_report_summary(reports.get(row.symbol), previous_trade_date),
            }
        )

    return {
        "previous_trade_date": previous_trade_date,
        "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
        "items": items,
    }


def _list_imported_position_rows(db: Session, user_id: str) -> list[ImportedPortfolioPositionDB]:
    """Return all imported positions for a user regardless of source."""
    return (
        db.query(ImportedPortfolioPositionDB)
        .filter(ImportedPortfolioPositionDB.user_id == user_id)
        .order_by(
            ImportedPortfolioPositionDB.market_value.desc(),
            ImportedPortfolioPositionDB.current_position.desc(),
            ImportedPortfolioPositionDB.symbol,
        )
        .all()
    )


def _select_reports_for_symbols(
    db: Session,
    user_id: str,
    symbols: list[str],
    previous_trade_date: str,
) -> dict[str, ReportDB]:
    if not symbols:
        return {}

    rows = (
        db.query(ReportDB)
        .options(load_only(
            ReportDB.id,
            ReportDB.symbol,
            ReportDB.trade_date,
            ReportDB.decision,
            ReportDB.direction,
            ReportDB.research_direction,
            ReportDB.execution_action,
            ReportDB.action_label,
            ReportDB.target_price,
            ReportDB.stop_loss_price,
            ReportDB.trader_investment_plan,
            ReportDB.final_trade_decision,
            ReportDB.created_at,
        ))
        .filter(
            ReportDB.user_id == user_id,
            ReportDB.symbol.in_(symbols),
            ReportDB.status == "completed",
        )
        .order_by(ReportDB.trade_date.desc(), ReportDB.created_at.desc())
        .all()
    )

    latest_any: dict[str, ReportDB] = {}

    for row in rows:
        if row.symbol not in latest_any:
            latest_any[row.symbol] = row

    selected: dict[str, ReportDB] = {}
    for symbol in symbols:
        report = latest_any.get(symbol)
        if report:
            selected[symbol] = report
    return selected


def _serialize_report_summary(report: ReportDB | None, previous_trade_date: str) -> dict[str, Any] | None:
    if report is None:
        return None

    return {
        "report_id": report.id,
        "trade_date": report.trade_date,
        "is_previous_trade_day": report.trade_date == previous_trade_date,
        "decision": report.decision,
        "direction": report.direction,
        "research_direction": report.research_direction,
        "execution_action": report.execution_action,
        "action_label": report.action_label,
        "high_price": _to_float(report.target_price),
        "low_price": _to_float(report.stop_loss_price),
        "trader_advice_summary": _summarize_trader_advice(
            report.trader_investment_plan,
            fallback_text=report.final_trade_decision,
        ),
        "trader_investment_plan": report.trader_investment_plan,
        "final_trade_decision": report.final_trade_decision,
    }


def _summarize_trader_advice(text: str | None, fallback_text: str | None = None) -> str | None:
    for source in (text, fallback_text):
        if not source:
            continue

        for pattern in (
            r"最终交易建议[:：]\s*([^\n]+)",
            r"结论[:：]\s*([^\n]+)",
            r"建议动作[:：]\s*([^\n]+)",
            r"方向[:：]\s*([^\n]+)",
        ):
            match = re.search(pattern, source, re.IGNORECASE)
            if match:
                return _clip_summary(match.group(1))

        lines = [
            _clip_summary(line.strip(" -*\t"))
            for line in _strip_markdown(source).splitlines()
            if line.strip()
        ]
        for line in lines:
            if len(line) >= 6 and not re.match(r"^[一二三四五六七八九十0-9]+[、.)：:]?$", line):
                return line
    return None


def _strip_markdown(text: str) -> str:
    cleaned = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    cleaned = cleaned.replace("\r", "\n")
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"\*\*|__", "", cleaned)
    cleaned = re.sub(r"^\s*#+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", cleaned)
    return cleaned


def _clip_summary(text: str | None) -> str | None:
    if text is None:
        return None
    compact = re.sub(r"\s+", " ", text).strip(" ，,;；。")
    if not compact:
        return None
    return compact[:96]


def _fetch_live_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    try:
        result_json = route_to_vendor("get_realtime_quotes", symbols)
        return json.loads(result_json)
    except Exception as exc:
        logger.warning("[tracking-board] realtime quote fetch failed: %s", exc)
        return {}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except Exception:
        return None


# [TRACK-002] tracking_board_v2_groups
def get_tracking_board_v2(db: Session, user_id: str) -> dict[str, Any]:
    """Tracking board v2: holdings + observation items + today guidance + alerts.

    Returns:
        - holdings: list of position items with live quotes and TA report
        - observation_items: list of active observation warehouse items
        - today_guidance: aggregated guidance items with source/priority/reason
        - alerts: high-priority items needing attention
        - review_summary: post-market review summary (TRACK-005, rule-based)
        - data_freshness: freshness metadata for all data sources used
    """
    now = datetime.now()
    previous_trade_date = previous_cn_trading_day(cn_today_str())
    is_trading = is_cn_trading_day(cn_today_str())

    # --- Holdings ---
    holdings = _build_holdings_v2(db, user_id, previous_trade_date)

    # --- Observation items ---
    obs_items = _fetch_observation_items()
    obs_symbols = [item["symbol"] for item in obs_items]
    obs_quotes = _fetch_live_quotes(obs_symbols)
    observation_items = _enrich_observation_items(obs_items, obs_quotes)

    # --- Today guidance aggregation ---
    guidance = _aggregate_today_guidance(
        holdings, observation_items, obs_quotes, is_trading, now
    )

    # --- Alerts (P0 items) ---
    alerts = [g for g in guidance if g.get("priority") == "P0"]

    # --- Data freshness ---
    data_freshness = _build_data_freshness(holdings, observation_items, obs_quotes, is_trading, now)

    # --- Post-market review summary (TRACK-005) ---
    review_summary = _build_review_summary(
        holdings, observation_items, previous_trade_date, is_trading, now
    )

    return {
        "previous_trade_date": previous_trade_date,
        "is_trading_day": is_trading,
        "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        "holdings": holdings,
        "observation_items": observation_items,
        "today_guidance": guidance,
        "alerts": alerts,
        "review_summary": review_summary,
        "data_freshness": data_freshness,
    }


# [TRACK-002] tracking_board_v2_groups
def _build_holdings_v2(
    db: Session, user_id: str, previous_trade_date: str
) -> list[dict[str, Any]]:
    """Build holdings list with live quotes and TA report for v2."""
    rows = _list_imported_position_rows(db, user_id)
    if not rows:
        return []

    symbols = [row.symbol for row in rows]
    quotes = _fetch_live_quotes(symbols)
    reports = _select_reports_for_symbols(db, user_id, symbols, previous_trade_date)

    items: list[dict[str, Any]] = []
    for row in rows:
        quote = quotes.get(row.symbol, {})
        live_price = _to_float(quote.get("price"))
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

        items.append(
            {
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
                "analysis": _serialize_report_summary(reports.get(row.symbol), previous_trade_date),
            }
        )
    return items


# [TRACK-002] tracking_board_v2_groups
def _fetch_observation_items() -> list[dict[str, Any]]:
    """Fetch active (non-removed) observation items from tradeflow DB."""
    try:
        from api.services.tradeflow_service import get_observation_items
        result = get_observation_items(status=None, include_removed=False)
        return result.get("items", []) if isinstance(result, dict) else []
    except Exception as exc:
        logger.warning("[tracking-board-v2] failed to fetch observation items: %s", exc)
        return []


# [TRACK-002] tracking_board_v2_groups
def _enrich_observation_items(
    items: list[dict[str, Any]], quotes: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add live quote data to observation items."""
    enriched = []
    for item in items:
        quote = quotes.get(item.get("symbol", ""), {})
        enriched_item = dict(item)
        enriched_item["live_price"] = _to_float(quote.get("price"))
        enriched_item["price_change_pct"] = _to_float(quote.get("change_pct"))
        enriched_item["quote_time"] = quote.get("quote_time")
        enriched_item["quote_source"] = quote.get("source")
        enriched.append(enriched_item)
    return enriched


# [TRACK-002] tracking_board_v2_groups
def _aggregate_today_guidance(
    holdings: list[dict[str, Any]],
    observation_items: list[dict[str, Any]],
    obs_quotes: dict[str, dict[str, Any]],
    is_trading_day: bool,
    now: datetime,
) -> list[dict[str, Any]]:
    """Aggregate today's guidance from holdings risk and observation entry signals.

    Each guidance has: type, priority (P0-P3), symbol, source, reason, as_of.
    """
    guidance: list[dict[str, Any]] = []
    as_of = now.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Holdings risk: large daily drop without TA analysis
    for h in holdings:
        symbol = h["symbol"]
        analysis = h.get("analysis")
        change_pct = h.get("price_change_pct")

        # No analysis at all
        if analysis is None:
            guidance.append({
                "type": "holdings_no_analysis",
                "priority": "P2",
                "symbol": symbol,
                "name": h.get("name", symbol),
                "reason": f"持仓 {symbol} 无最新 TA 报告，需人工确认",
                "source": "tracking_board_v2",
                "as_of": as_of,
            })
            continue

        # Large daily drop (>3%)
        if change_pct is not None and change_pct <= -3.0:
            guidance.append({
                "type": "holdings_risk",
                "priority": "P0",
                "symbol": symbol,
                "name": h.get("name", symbol),
                "reason": f"持仓 {symbol} 日跌幅 {change_pct:.2f}%，超阈值",
                "source": "tracking_board_v2",
                "as_of": as_of,
            })
        # Moderate drop (>1.5%)
        elif change_pct is not None and change_pct <= -1.5:
            guidance.append({
                "type": "holdings_risk",
                "priority": "P1",
                "symbol": symbol,
                "name": h.get("name", symbol),
                "reason": f"持仓 {symbol} 日跌幅 {change_pct:.2f}%，需关注",
                "source": "tracking_board_v2",
                "as_of": as_of,
            })

    # 2. Observation items: state engine drives near entry / in entry zone /
    #    invalidated / missed entry / ta_required / data_missing / needs_review.
    #    [TRACK-004] observation_state_engine
    for obs in observation_items:
        symbol = obs.get("symbol", "")
        stored_status = obs.get("status", "watching")
        obs_name = obs.get("name", symbol)

        result = evaluate_observation_state(obs, is_trading_day=is_trading_day)
        if should_emit_guidance(result):
            guidance.append(result_to_guidance(result, as_of=as_of))

        # 用户标记 ta_required 且引擎命中价格类状态时，额外补一条 ta_required
        # 提示（与原 TRACK-002 行为保持一致：价格到位 + 仍需 TA 确认）。
        price_states = {STATE_IN_ENTRY_ZONE, STATE_NEAR_ENTRY, STATE_MISSED_ENTRY}
        if stored_status == "ta_required" and result.get("state") in price_states:
            guidance.append({
                "type": "observation_ta_required",
                "priority": "P1",
                "symbol": symbol,
                "name": obs_name,
                "reason": f"观察仓 {symbol} 需要 TA 深度确认",
                "source": "tracking_board_v2",
                "as_of": as_of,
                "state": STATE_TA_REQUIRED,
                "data_fields": {"stored_status": stored_status},
            })

    # Sort by priority
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    guidance.sort(key=lambda g: priority_order.get(g.get("priority", "P3"), 9))

    return guidance


# [TRACK-002] tracking_board_v2_groups
def _build_data_freshness(
    holdings: list[dict[str, Any]],
    observation_items: list[dict[str, Any]],
    obs_quotes: dict[str, dict[str, Any]],
    is_trading_day: bool,
    now: datetime,
) -> dict[str, Any]:
    """Build data freshness summary."""
    as_of = now.strftime("%Y-%m-%d %H:%M:%S")

    # Count holdings with quotes
    h_total = len(holdings)
    h_with_quotes = sum(1 for h in holdings if h.get("live_price") is not None)
    h_with_analysis = sum(1 for h in holdings if h.get("analysis") is not None)

    # Count observation items with quotes
    o_total = len(observation_items)
    o_with_quotes = sum(1 for o in observation_items if o.get("live_price") is not None)

    # Latest quote time
    all_quote_times = []
    for h in holdings:
        if h.get("quote_time"):
            all_quote_times.append(h["quote_time"])
    for o in observation_items:
        if o.get("quote_time"):
            all_quote_times.append(o["quote_time"])

    latest_quote_time = max(all_quote_times) if all_quote_times else None

    status = "fresh"
    if not is_trading_day:
        status = "non_trading_day"
    elif h_total > 0 and h_with_quotes == 0:
        status = "stale"

    return {
        "status": status,
        "holdings_total": h_total,
        "holdings_with_live_quotes": h_with_quotes,
        "holdings_with_analysis": h_with_analysis,
        "observation_total": o_total,
        "observation_with_live_quotes": o_with_quotes,
        "latest_quote_time": latest_quote_time,
        "as_of": as_of,
    }


# [TRACK-005] post_market_tracking_review
def _build_review_summary(
    holdings: list[dict[str, Any]],
    observation_items: list[dict[str, Any]],
    previous_trade_date: str,
    is_trading_day: bool,
    now: datetime,
) -> dict[str, Any]:
    """Build the post-market review summary for the tracking board.

    Defensive wrapper around ``build_post_market_tracking_review``:
    - Fetches the TradeFlow review (TF-REVIEW-003) for ``previous_trade_date``
      in a try/except so any DB failure degrades gracefully to "no candidate
      pool review" instead of breaking the whole tracking board.
    - Never raises; on any unexpected error returns a minimal "skipped" summary
      so the frontend ReviewZone still has a stable payload.
    """
    as_of = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        tradeflow_review = _fetch_tradeflow_review(previous_trade_date)
    except Exception as exc:
        logger.warning("[tracking-board-v2] tradeflow review fetch failed: %s", exc)
        tradeflow_review = None

    try:
        summary = build_post_market_tracking_review(
            holdings=holdings,
            observation_items=observation_items,
            tradeflow_review=tradeflow_review,
            is_trading_day=is_trading_day,
            review_date=previous_trade_date,
            as_of=as_of,
        )
        # [TRACK-010] review_encoding_regression — 在交给 API JSON 序列化
        # 之前，统一清洗可能出现的字面 \uXXXX / \n / BOM / bytes repr，
        # 避免前端"盘后复盘"区域出现乱码。永不抛异常。
        try:
            summary = sanitize_review_summary_text(summary)
        except Exception as exc:
            logger.warning(
                "[tracking-board-v2] review summary sanitize failed: %s", exc
            )
        return summary
    except Exception as exc:
        logger.exception("[tracking-board-v2] review summary build failed: %s", exc)
        return {
            "review_date": previous_trade_date,
            "as_of": as_of,
            "is_trading_day": bool(is_trading_day),
            "data_status": "NO_DATA",
            "data_status_message": "复盘摘要生成失败，请稍后重试或人工复核",
            "has_tradeflow_review": False,
            "tradeflow_review_status": "skipped",
            "holdings_review": [],
            "observation_review": [],
            "candidate_pool_review": [],
            "tomorrow_focus": [],
            "summary_counts": {
                "holdings_total": len(holdings),
                "observation_total": len(observation_items),
                "candidates_total": 0,
            },
        }


# [TRACK-005] post_market_tracking_review
def _fetch_tradeflow_review(trade_date: str) -> dict[str, Any] | None:
    """Fetch TradeFlow post-market review for the given trade date.

    Defensive: returns None on any failure (missing DB, no candidates, etc.).
    """
    try:
        from api.services.tradeflow_service import get_review
        return get_review(trade_date)
    except Exception as exc:
        logger.warning("[tracking-board-v2] get_review(%s) failed: %s", trade_date, exc)
        return None
