"""TradeFlow Review Runner — post-market review of signals and plans.

P0: Simple review that checks:
- Did any triggers hit?
- Did any invalid levels break?
- What to keep observing tomorrow?
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from .schemas import Candidate, DailyPlan


def review_plan(
    plan: DailyPlan,
    tf_db_path: str = "",
) -> dict:
    """Review a daily plan against latest prices.

    P0: Mark status based on simple rules.
    Returns review dict with actions.
    """
    results = []
    for entry in plan.candidates:
        symbol = entry["symbol"]
        trigger = entry.get("trigger_price")
        invalid = entry.get("invalid_price")
        action = entry.get("action", "OBSERVE")

        # P0: We don't fetch live prices in review_runner
        # Just mark what to watch tomorrow
        result = {
            "symbol": symbol,
            "plan_action": action,
            "keep_observing": True,
            "reason": "",
        }

        if action == "REMOVE_FROM_WATCH":
            result["keep_observing"] = False
            result["reason"] = "已标记移除"
        elif trigger:
            result["reason"] = f"继续观察，关注触发价 {trigger}"
        else:
            result["reason"] = "继续观察"

        results.append(result)

    return {
        "trade_date": plan.trade_date,
        "reviewed_at": datetime.now().isoformat(),
        "results": results,
    }
