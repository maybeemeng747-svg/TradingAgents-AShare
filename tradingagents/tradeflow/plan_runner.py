"""TradeFlow Plan Runner — generates pre-market daily plans.

Reads candidates from tradeflow_candidates, produces a DailyPlan
with only allowed actions: OBSERVE / WAIT_TRIGGER / NEED_DEEP_TA / REMOVE_FROM_WATCH.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

from .schemas import (
    DailyPlan, Candidate, ALLOWED_ACTIONS, FORBIDDEN_WORDS
)
from .candidate_engine import init_db, save_candidate, evaluate_symbol
from .universe import build_universe


def _action_for_candidate(candidate: Candidate) -> str:
    """Determine the action for a candidate based on its signals."""
    if candidate.need_deep_ta:
        return "NEED_DEEP_TA"

    # If score is high and trigger price is close to current price
    evidence = candidate.evidence
    for strategy, ev in evidence.items():
        if isinstance(ev, dict):
            latest = ev.get("latest_close", 0)
            trigger = candidate.trigger_price or 0
            if latest > 0 and trigger > 0:
                distance = (trigger - latest) / latest
                if distance < 0.02:  # within 2% of trigger
                    return "WAIT_TRIGGER"

    return "OBSERVE"


def _build_plan_entry(candidate: Candidate) -> dict:
    """Build a plan entry dict from a candidate."""
    action = _action_for_candidate(candidate)

    # Build reason — no forbidden words
    reasons = []
    for sig in candidate.signals:
        reasons.append(f"[{sig.strategy_tag}] {sig.reason}")
    reason_str = "; ".join(reasons) if reasons else "候选观察"

    # Sanity check: no forbidden words
    for word in FORBIDDEN_WORDS:
        if word in reason_str:
            reason_str = reason_str.replace(word, "***")

    return {
        "symbol": candidate.symbol,
        "name": candidate.name,
        "action": action,
        "primary_strategy": candidate.primary_strategy,
        "strategies": candidate.strategy_tags,
        "score": candidate.score,
        "trigger_price": candidate.trigger_price,
        "support_price": candidate.support_price,
        "invalid_price": candidate.invalid_price,
        "reason": reason_str,
        "need_deep_ta": candidate.need_deep_ta,
        "risk_flags": candidate.risk_flags,
        "policy_tags": candidate.policy_tags,  # [S-001] policy_version_signal
        "version_score": candidate.version_score,  # [S-001]
        "policy_evidence_refs": candidate.policy_evidence_refs,  # [S-001]
    }


def generate_daily_plan(
    trade_date: Optional[str] = None,
    symbols: Optional[list[str]] = None,
    prod_db_path: str = "",
    tf_db_path: str = "",
    candidates: Optional[list[Candidate]] = None,
    news_texts: Optional[list[str]] = None,
    event_overrides: Optional[list[dict]] = None,
    save_candidates: bool = False,
    use_event_source: bool = False,  # [N-001] event_source_plan_integration
) -> DailyPlan:
    """Generate a pre-market daily plan.

    Args:
        trade_date: Trading date (default: today).
        symbols: Specific symbols to evaluate (overrides universe).
        prod_db_path: Path to production tradingagents.db.
        tf_db_path: Path to tradeflow SQLite DB.
        candidates: Pre-evaluated candidates (skip evaluation if provided).
        news_texts: News/announcement texts for event catalyst detection.
        event_overrides: Manual event overrides for event catalyst detection.
        save_candidates: If True, persist evaluated candidates to tf_db_path.
        use_event_source: [N-001] If True, fetch daily events and inject into universe.

    Returns:
        DailyPlan with validated entries.
    """
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    # [N-001] event_source_plan_integration — fetch events if enabled
    events_map: dict[str, list[str]] = {}
    if use_event_source:
        from .event_source import fetch_daily_events
        trade_date_compact = trade_date.replace("-", "")
        events_map = fetch_daily_events(trade_date_compact)

    if candidates is None:
        # Build universe — pass event_symbols so event-discovered
        # symbols enter the pool
        universe = build_universe(
            symbols=symbols,
            prod_db_path=prod_db_path,
            tf_db_path=tf_db_path,
            event_overrides=event_overrides,
            event_symbols=events_map if events_map else None,  # [N-001]
        )

        # Evaluate each symbol — filter event_overrides to only
        # those matching the current symbol (or global overrides without symbol).
        # [N-001] Each symbol gets its own event titles as news_texts.
        candidates = []
        for item in universe:
            sym = item["symbol"]
            if event_overrides:
                filtered_overrides = [
                    ev for ev in event_overrides
                    if ev.get("symbol") is None or ev.get("symbol") == sym
                ]
            else:
                filtered_overrides = None

            # [N-001] event_source_plan_integration
            sym_news = events_map.get(sym) if events_map else None

            c, reason = evaluate_symbol(
                symbol=sym,
                name=item.get("name", ""),
                source=item.get("source", "manual"),
                trade_date=trade_date,
                news_texts=sym_news or news_texts,
                event_overrides=filtered_overrides,
            )
            if c is not None:
                candidates.append(c)

    # Build plan entries
    plan_entries = []
    for c in candidates:
        entry = _build_plan_entry(c)
        plan_entries.append(entry)

    # Sort by score descending
    plan_entries.sort(key=lambda x: x["score"], reverse=True)

    # Build summary
    n_total = len(plan_entries)
    n_deep_ta = sum(1 for e in plan_entries if e["need_deep_ta"])
    n_wait = sum(1 for e in plan_entries if e["action"] == "WAIT_TRIGGER")

    summary_parts = [
        f"共{n_total}只候选",
        f"{n_deep_ta}只需要深度TA",
        f"{n_wait}只接近触发",
    ]
    if symbols:
        summary_parts.append(f"指定标的: {', '.join(symbols)}")

    plan = DailyPlan(
        trade_date=trade_date,
        mode="pre_market",
        summary="，".join(summary_parts) + "。",
        candidates=plan_entries,
        metadata={
            "universe_size": len(plan_entries),
            "generated_at": datetime.now().isoformat(),
        },
    )

    # Validate
    issues = plan.validate()
    if issues:
        plan.metadata["validation_issues"] = issues

    # Optionally persist candidates  # [G-004] tradeflow_candidate_date
    if save_candidates and tf_db_path:
        for c in candidates:
            if c.trade_date != trade_date:
                c.trade_date = trade_date
            save_candidate(c, tf_db_path)

    return plan


def save_plan(plan: DailyPlan, db_path: str) -> int:
    """Save daily plan to DB."""
    conn = sqlite3.connect(db_path)
    row = plan.to_db_row()
    try:
        conn.execute(
            "INSERT INTO tradeflow_daily_plans "
            "(trade_date, mode, summary, candidates_json, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, mode) DO UPDATE SET "
            "summary=excluded.summary, candidates_json=excluded.candidates_json, "
            "metadata_json=excluded.metadata_json",
            (row["trade_date"], row["mode"], row["summary"],
             row["candidates_json"], row["metadata_json"], row["created_at"]),
        )
        conn.commit()
        row_id = conn.execute(
            "SELECT id FROM tradeflow_daily_plans WHERE trade_date=? AND mode=?",
            (row["trade_date"], row["mode"]),
        ).fetchone()[0]
        return row_id
    finally:
        conn.close()
