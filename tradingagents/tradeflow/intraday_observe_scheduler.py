# [T-004] intraday_observe_scheduler
"""Periodic intraday observe scheduler for TradeFlow candidates.

Runs during A-share market hours (09:30–15:00) at a configurable interval
(default 30 minutes). Each tick calls `run_observe()` to check all active
candidates against realtime quotes, detect price triggers / invalidations /
volume anomalies, and persist signals.

Design constraints:
- Default silent: only records signals when conditions are met.
- Does NOT auto-push notifications.
- Does NOT auto-invoke deep TA or LLM.
- Daily trigger count capped per symbol (via StrategyConfig).
- Non-trading days are skipped.
- Can be run standalone or embedded in the main scheduler.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .intraday_observe import ObserveState
from .observe_runner import run_observe, ObserveRunResult
from .candidate_engine import init_db
from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class ObserveSchedulerTickResult:
    tick_time: str = ""
    trade_date: str = ""
    status: str = ""
    result: Optional[ObserveRunResult] = None
    error: str = ""

    def __post_init__(self):
        if not self.tick_time:
            self.tick_time = datetime.now().isoformat()


def _is_market_hours(cfg: Optional[StrategyConfig] = None) -> bool:
    """Check if current time is within A-share market hours."""
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    now = datetime.now()
    current_minutes = now.hour * 60 + now.minute

    open_minutes = cfg.observe_market_open_hour * 60 + cfg.observe_market_open_minute
    close_minutes = cfg.observe_market_close_hour * 60 + cfg.observe_market_close_minute

    return open_minutes <= current_minutes < close_minutes


def _is_trading_day(date_str: str) -> bool:
    try:
        from tradingagents.dataflows.trade_calendar import is_cn_trading_day
        return is_cn_trading_day(date_str)
    except Exception:
        from datetime import datetime as _dt
        d = _dt.strptime(date_str, "%Y-%m-%d")
        return d.weekday() < 5


def run_observe_tick(
    trade_date: Optional[str] = None,
    db_path: str = "",
    cfg: Optional[StrategyConfig] = None,
    quote_provider=None,
) -> ObserveSchedulerTickResult:
    """Execute a single observe tick.

    Checks if it's a trading day and market hours, then runs observe for
    all active candidates. Returns tick result with status and run result.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    tick = ObserveSchedulerTickResult(trade_date=trade_date)

    if not _is_trading_day(trade_date):
        tick.status = "skipped_non_trading_day"
        return tick

    if not _is_market_hours(cfg):
        tick.status = "skipped_non_market_hours"
        return tick

    if not db_path:
        tick.status = "skipped_no_db"
        return tick

    try:
        init_db(db_path)
        result = run_observe(
            trade_date=trade_date,
            db_path=db_path,
            cfg=cfg,
            quote_provider=quote_provider,
        )
        tick.result = result
        if result.skipped_reason:
            tick.status = "skipped"
        elif result.errors:
            tick.status = "partial"
        else:
            tick.status = "ok"

        if result.triggered > 0:
            logger.info(
                "[T-004] Observe tick %s: checked=%d triggered=%d invalidated=%d signals=%d",
                trade_date, result.checked, result.triggered, result.invalidated, result.signals_written,
            )
    except Exception as e:
        tick.status = "error"
        tick.error = str(e)
        logger.error("[T-004] Observe tick error: %s", e)

    return tick


def run_observe_session(
    db_path: str = "",
    cfg: Optional[StrategyConfig] = None,
    *,
    max_ticks: int = 20,
    tick_callback=None,
    stop_check=None,
) -> list[ObserveSchedulerTickResult]:
    """Run observe ticks during the current market session.

    Blocks and runs periodic observe checks until market close or stopped.

    Args:
        db_path: Path to tradeflow.db.
        cfg: Strategy config.
        max_ticks: Safety limit on number of ticks.
        tick_callback: Optional callable(tick_result) after each tick.
        stop_check: Optional callable() -> bool to signal early stop.

    Returns:
        List of tick results for the session.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    interval_seconds = cfg.observe_interval_minutes * 60
    results: list[ObserveSchedulerTickResult] = []
    tick_count = 0

    logger.info("[T-004] Observe session starting (interval=%dmin)", cfg.observe_interval_minutes)

    while tick_count < max_ticks:
        if stop_check and stop_check():
            logger.info("[T-004] Observe session stopped by stop_check")
            break

        trade_date = datetime.now().strftime("%Y-%m-%d")

        if not _is_trading_day(trade_date):
            logger.info("[T-004] %s is not a trading day, stopping session", trade_date)
            break

        if not _is_market_hours(cfg):
            logger.info("[T-004] Outside market hours, stopping session")
            break

        tick = run_observe_tick(
            trade_date=trade_date,
            db_path=db_path,
            cfg=cfg,
        )
        results.append(tick)

        if tick_callback:
            tick_callback(tick)

        tick_count += 1
        time.sleep(interval_seconds)

    logger.info("[T-004] Observe session ended: %d ticks", len(results))
    return results
