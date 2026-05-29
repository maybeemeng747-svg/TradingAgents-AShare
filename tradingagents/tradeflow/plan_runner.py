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
from .false_positive_audit import build_audit_report, AuditReport  # [S-006] candidate_false_positive_audit
from .tier_budget import classify_candidate_tier, allocate_tier_budget, render_tier_budget_summary  # [S-007] candidate_tier_budget


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
        "narrative_score": candidate.narrative_score,  # [S-002] narrative_quality_score
        "narrative_reasons": candidate.narrative_reasons,  # [S-002]
        "narrative_evidence_refs": candidate.narrative_evidence_refs,  # [S-002]
        "risk_penalty": candidate.risk_penalty,  # [S-003] underwater_risk_flags
        "risk_evidence_refs": candidate.risk_evidence_refs,  # [S-003]
        "risk_reasons": candidate.risk_reasons,  # [S-003]
        "game_balance": candidate.game_balance,  # [S-004] candidate_game_balance
        "bull_case": candidate.bull_case,  # [S-004]
        "bear_case": candidate.bear_case,  # [S-004]
        "policy_case": candidate.policy_case,  # [S-004]
        "fund_flow_case": candidate.fund_flow_case,  # [S-004]
        "resonance_count": candidate.resonance_count,  # [S-004]
        "game_balance_refs": candidate.game_balance_refs,  # [S-004]
        "fund_flow_anomaly_score": candidate.fund_flow_anomaly_score,  # [T-003] fund_flow_anomaly_pool
        "fund_flow_anomaly_tags": candidate.fund_flow_anomaly_tags,  # [T-003]
        "fund_flow_anomaly_refs": candidate.fund_flow_anomaly_refs,  # [T-003]
        "fund_flow_unit_verified": candidate.fund_flow_unit_verified,  # [T-003]
        "fund_flow_individual_summary": candidate.fund_flow_individual_summary,  # [T-003]
        "fund_flow_board_summary": candidate.fund_flow_board_summary,  # [T-003]
        "composite_score": candidate.composite_score,  # [S-005] selection_priority_gate
        "signal_category_hits": candidate.signal_category_hits,  # [S-005]
        "positive_category_count": candidate.positive_category_count,  # [S-005]
        "data_completeness": candidate.data_completeness,  # [S-005]
        "missing_evidence": candidate.missing_evidence,  # [S-005]
        "why_deep_ta": candidate.why_deep_ta,  # [S-005]
        "why_not_deep_ta": candidate.why_not_deep_ta,  # [S-005]
        "priority_rank": candidate.priority_rank,  # [S-005]
        "tier": candidate.tier,  # [S-007] candidate_tier_budget
        "ta_budget_priority": candidate.ta_budget_priority,  # [S-007]
        "tier_reason": candidate.tier_reason,  # [S-007]
        "missing_evidence_for_upgrade": candidate.missing_evidence_for_upgrade,  # [S-007]
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
    fund_flow_map: Optional[dict[str, dict]] = None,  # [T-003] fund_flow_anomaly_pool
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
        fund_flow_map: [T-003] Optional dict mapping symbol → {"individual": str, "board": str}.

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

            # [T-003] fund_flow_anomaly_pool — get per-symbol fund flow data
            sym_ff = fund_flow_map.get(sym, {}) if fund_flow_map else {}
            sym_ff_individual = sym_ff.get("individual") if sym_ff else None
            sym_ff_board = sym_ff.get("board") if sym_ff else None

            c, reason = evaluate_symbol(
                symbol=sym,
                name=item.get("name", ""),
                source=item.get("source", "manual"),
                trade_date=trade_date,
                news_texts=sym_news or news_texts,
                event_overrides=filtered_overrides,
                fund_flow_individual=sym_ff_individual,  # [T-003]
                fund_flow_board=sym_ff_board,  # [T-003]
            )
            if c is not None:
                candidates.append(c)

    # Build plan entries
    plan_entries = []
    for c in candidates:
        entry = _build_plan_entry(c)
        plan_entries.append(entry)

    # Sort by composite_score descending (S-005 priority gate), fallback to score
    plan_entries.sort(key=lambda x: (x.get("composite_score", 0) or 0, x.get("score", 0)), reverse=True)

    # [S-007] candidate_tier_budget — sort by tier (A first, then B, then C)
    tier_order = {"A": 0, "B": 1, "C": 2, "": 3}
    plan_entries.sort(key=lambda x: (tier_order.get(x.get("tier", ""), 3), -(x.get("composite_score", 0) or 0)))

    # [S-007] candidate_tier_budget — enforce A-tier cap and compute budget summary
    from .tier_budget import TierBudgetResult
    tier_results = []
    for e in plan_entries:
        tier_results.append(TierBudgetResult(
            tier=e.get("tier", "C"),
            ta_budget_priority=e.get("ta_budget_priority", 0),
            tier_reason=e.get("tier_reason", ""),
            missing_evidence_for_upgrade=e.get("missing_evidence_for_upgrade", []),
            why_not_deep_ta=e.get("why_not_deep_ta", ""),
        ))
    budget_allocation = allocate_tier_budget(tier_results)
    # Update entries if any were demoted by the cap
    for i, tr in enumerate(tier_results):
        if i < len(plan_entries):
            plan_entries[i]["tier"] = tr.tier
            plan_entries[i]["ta_budget_priority"] = tr.ta_budget_priority
            plan_entries[i]["tier_reason"] = tr.tier_reason

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

    # [S-006] candidate_false_positive_audit — generate audit report
    audit = build_audit_report(
        candidates=candidates,
        trade_date=trade_date,
    )
    plan.metadata["audit_summary"] = {
        "total_candidates": audit.summary.total_candidates if audit.summary else 0,
        "by_category": audit.summary.by_category if audit.summary else {},
        "common_evidence_gaps": audit.summary.common_evidence_gaps[:5] if audit.summary else [],
        "common_risk_demotions": audit.summary.common_risk_demotions[:5] if audit.summary else [],
    }

    # [S-007] candidate_tier_budget — add budget allocation to metadata
    plan.metadata["tier_budget"] = {
        "tier_a_count": budget_allocation.tier_a_count,
        "tier_b_count": budget_allocation.tier_b_count,
        "tier_c_count": budget_allocation.tier_c_count,
        "total_budget": budget_allocation.total_budget,
        "a_tier_cap_applied": budget_allocation.a_tier_cap_applied,
        "demoted_count": len(budget_allocation.demoted_symbols),
    }
    plan.metadata["tier_budget_summary"] = render_tier_budget_summary(budget_allocation)

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
