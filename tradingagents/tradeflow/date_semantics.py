"""TradeFlow date semantics — resolve plan_date, effective_trade_date, observe_date.

# [TF-DATE-001] tradeflow_date_semantics

Solves the problem where `trade_date` was simultaneously used to mean:
- candidate pool generation date (can be weekend/holiday)
- plan effective trading date (must be a valid A-share trading day)
- observation date (which trading day to observe during)

Now each semantic has its own field:
- plan_date: when the candidate pool was generated (any calendar date)
- effective_trade_date: which trading day this plan targets for observation
- observe_date: actual observation date (first version: same as effective_trade_date)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from tradingagents.dataflows.trade_calendar import is_cn_trading_day, now_cn, cn_market_phase


def _parse_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def next_cn_trading_day(date_str: str) -> str:
    d = _parse_date(date_str)
    cur = d + timedelta(days=1)
    max_days = 30
    for _ in range(max_days):
        if is_cn_trading_day(cur.strftime("%Y-%m-%d")):
            return cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


def resolve_effective_trade_date(plan_date: str, now: Optional[datetime] = None) -> str:
    if now is None:
        now = now_cn()

    plan_d = _parse_date(plan_date)
    is_trading = is_cn_trading_day(plan_date)

    if not is_trading:
        return next_cn_trading_day(plan_date)

    now_date_str = now.strftime("%Y-%m-%d") if now.tzinfo else now.date().strftime("%Y-%m-%d")

    if plan_date != now_date_str:
        return next_cn_trading_day(plan_date)

    phase = cn_market_phase(now)
    if phase in ("pre_open", "in_session", "lunch_break"):
        return plan_date
    return next_cn_trading_day(plan_date)


def resolve_plan_date(now: Optional[datetime] = None) -> str:
    if now is None:
        now = now_cn()
    return now.strftime("%Y-%m-%d") if now.tzinfo else now.date().strftime("%Y-%m-%d")


def resolve_observe_date(effective_trade_date: str) -> str:
    return effective_trade_date


# [TF-REVIEW-002] review_date_mapping
def resolve_review_date(plan_date: str, effective_trade_date: str = "") -> str:
    """Resolve which trading day a plan should be reviewed on.

    If the plan was generated on a non-trading day (e.g. 2026-05-31 Sunday),
    the review happens on the effective_trade_date (e.g. 2026-06-01 Monday).

    Args:
        plan_date: The date the candidate pool was generated.
        effective_trade_date: The resolved effective trading day (from resolve_effective_trade_date).
            If empty, computed from plan_date.

    Returns:
        The trading day the review should target.
    """
    if effective_trade_date:
        return effective_trade_date
    if is_cn_trading_day(plan_date):
        return plan_date
    return next_cn_trading_day(plan_date)


def find_latest_plan_date(dates: list[str], target_date: str = "") -> str:
    """Find the most recent plan date that should be reviewed.

    Used by the Review page to default-select the most recent plan with candidates.
    If target_date is given, prefers the exact match; otherwise returns the max date.

    Args:
        dates: list of plan_date strings available in the DB.
        target_date: the date the user is viewing (optional).

    Returns:
        The best plan_date to show, or "" if no dates available.
    """
    if not dates:
        return ""
    if target_date in dates:
        return target_date
    return max(dates)
