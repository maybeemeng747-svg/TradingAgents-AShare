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
from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]


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


def _build_plan_entry(candidate: Candidate, event_items: Optional[list] = None) -> dict:  # [T-006] event_source_discovery
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

    entry = {
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
        "tradeflow_data_completeness": candidate.tradeflow_data_completeness,  # [S-008] tradeflow_evidence_gate
        "missing_data_fields": candidate.missing_data_fields,  # [S-008]
        "what_to_upgrade": candidate.what_to_upgrade,  # [S-008]
        "evidence_gate_applied": candidate.evidence_gate_applied,  # [S-008]
        "universe_sources": candidate.universe_sources,  # [M-003] tradeflow_universe_manager
        "observe_state": candidate.observe_state,  # [M-005] intraday_observe_state
        "observe_trigger_count": candidate.observe_trigger_count,  # [M-005]
        "observe_first_trigger_time": candidate.observe_first_trigger_time,  # [M-005]
        "deep_ta_status": candidate.deep_ta_status,  # [M-006] gated_deep_ta_dispatch
        "deep_ta_dispatch_reason": candidate.deep_ta_dispatch_reason,  # [M-006]
        "deep_ta_model": candidate.deep_ta_model,  # [M-006]
        "deep_ta_report_path": candidate.deep_ta_report_path,  # [M-006]
        "deep_ta_dispatch_time": candidate.deep_ta_dispatch_time,  # [M-006]
        "deep_ta_position_context": candidate.deep_ta_position_context,  # [M-006]
        "plan_date": candidate.plan_date,  # [TF-DATE-001] tradeflow_date_semantics
        "effective_trade_date": candidate.effective_trade_date,  # [TF-DATE-001]
        "observe_date": candidate.observe_date,  # [TF-DATE-001]
        "beneficiary_path": candidate.beneficiary_path,  # [H-003] mandate_beneficiary_map
        "company_role": candidate.company_role,  # [H-003]
        "mandate_topic": candidate.mandate_topic,  # [H-003]
        "mandate_evidence_refs": candidate.mandate_evidence_refs,  # [H-003]
        "candidate_type": candidate.candidate_type,  # [H-004] mandate_ambush_score
        "ambush_score": candidate.ambush_score,  # [H-004]
        "mandate_score_component": candidate.mandate_score_component,  # [H-004]
        "beneficiary_score_component": candidate.beneficiary_score_component,  # [H-004]
        "pricing_gap_score": candidate.pricing_gap_score,  # [H-004]
        "overheat_penalty": candidate.overheat_penalty,  # [H-004]
        "candidate_type_reason": candidate.candidate_type_reason,  # [H-004]
        "deep_ta_route": candidate.deep_ta_route,  # [H-004]
        "deep_ta_route_reason": candidate.deep_ta_route_reason,  # [H-004]
        "ambush_reasons": candidate.ambush_reasons,  # [H-004]
        "ambush_evidence_refs": candidate.ambush_evidence_refs,  # [H-004]
        "research_queue": candidate.research_queue,  # [H-007] mandate_ta_queue_router
        "research_intent": candidate.research_intent,  # [H-007] mandate_ta_queue_router
        "research_route_reason": candidate.research_route_reason,  # [H-007] mandate_ta_queue_router
    }

    # [T-006] event_source_discovery — add event metadata to plan entry
    if event_items:
        entry["event_titles"] = [it.title for it in event_items if it.title]
        entry["event_types"] = sorted({it.event_type for it in event_items if it.event_type})
        entry["event_sources"] = sorted({it.source for it in event_items if it.source})
        entry["event_directions"] = sorted({it.direction for it in event_items if it.direction})
        entry["event_count"] = len(event_items)
        entry["event_details"] = [
            {
                "title": it.title,
                "event_type": it.event_type,
                "direction": it.direction,
                "source": it.source,
                "date": it.date,
            }
            for it in event_items
        ]

    return entry


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
    cfg: Optional[StrategyConfig] = None,  # [M-004]
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
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    # [TF-DATE-001] tradeflow_date_semantics — resolve date semantics
    from .date_semantics import resolve_effective_trade_date, resolve_observe_date
    eff_trade_date = resolve_effective_trade_date(trade_date)
    obs_date = resolve_observe_date(eff_trade_date)

    # [N-001] event_source_plan_integration — fetch events if enabled
    # [T-006] event_source_discovery — use detailed fetch for richer metadata
    events_map: dict[str, list[str]] = {}
    event_items_by_symbol: dict[str, list] = {}
    event_source_status = "NOT_QUERIED"
    event_source_error = ""
    event_source_failed: list[str] = []  # [T-007]
    event_source_per_source: dict[str, str] = {}  # [T-007]

    if use_event_source:
        from .event_source import fetch_daily_events_detailed, EventSourceStatus
        trade_date_compact = trade_date.replace("-", "")
        event_result = fetch_daily_events_detailed(trade_date_compact)
        events_map = event_result.events_map
        event_items_by_symbol = event_result.items_by_symbol
        event_source_status = event_result.status.value
        event_source_error = event_result.error_message
        event_source_failed = event_result.failed_sources  # [T-007]
        event_source_per_source = event_result.source_statuses  # [T-007]

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
        # [T-006] event_source_discovery — also generate event_overrides from EventItem data
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

            # [T-006] event_source_discovery — generate event_overrides from EventItem data
            sym_event_overrides = list(filtered_overrides) if filtered_overrides else []
            if sym in event_items_by_symbol:
                for ev_item in event_items_by_symbol[sym]:
                    sym_event_overrides.append({
                        "symbol": sym,
                        "title": ev_item.title,
                        "event_type": ev_item.event_type,
                        "direction": ev_item.direction,
                        "source": ev_item.source,
                        "date": ev_item.date,
                    })
            sym_event_overrides_final = sym_event_overrides if sym_event_overrides else None

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
                event_overrides=sym_event_overrides_final,  # [T-006]
                fund_flow_individual=sym_ff_individual,  # [T-003]
                fund_flow_board=sym_ff_board,  # [T-003]
                cfg=cfg,  # [M-004]
            )
            if c is not None:
                if item.get("universe_sources"):
                    c.universe_sources = item["universe_sources"]  # [M-003]
                # [T-006] event_source_discovery — attach event items for plan entry
                if sym in event_items_by_symbol:
                    c._event_items = event_items_by_symbol[sym]  # type: ignore[attr-defined]
                candidates.append(c)

    # Build plan entries
    plan_entries = []
    for c in candidates:
        ev_items = getattr(c, "_event_items", None)  # [T-006]
        entry = _build_plan_entry(c, event_items=ev_items)
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
    budget_allocation = allocate_tier_budget(tier_results, cfg=cfg)  # [M-004]
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
            "strategy_config_version": cfg.config_version,  # [M-004]
            "event_source": {  # [T-006] event_source_discovery
                "status": event_source_status,
                "error_message": event_source_error,
                "event_count": sum(len(v) for v in event_items_by_symbol.values()),
                "symbols_count": len(event_items_by_symbol),
                "failed_sources": event_source_failed,  # [T-007]
                "source_statuses": event_source_per_source,  # [T-007]
            },
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

    # [S-008] tradeflow_evidence_gate — add evidence gate summary to metadata
    gate_blocked_count = sum(1 for e in plan_entries if e.get("evidence_gate_applied"))
    gate_deep_ta_blocked = sum(1 for e in plan_entries if e.get("evidence_gate_applied") and not e.get("need_deep_ta"))
    plan.metadata["evidence_gate"] = {
        "gate_blocked_count": gate_blocked_count,
        "deep_ta_blocked_count": gate_deep_ta_blocked,
        "avg_completeness": round(sum(e.get("tradeflow_data_completeness", 0) for e in plan_entries) / max(len(plan_entries), 1), 3),
    }

    # Validate
    issues = plan.validate()
    if issues:
        plan.metadata["validation_issues"] = issues

    # Optionally persist candidates  # [G-004] tradeflow_candidate_date
    if save_candidates and tf_db_path:
        for c in candidates:  # [TF-DATE-001] tradeflow_date_semantics
            if c.trade_date != trade_date:
                c.trade_date = trade_date
            c.plan_date = trade_date
            c.effective_trade_date = eff_trade_date
            c.observe_date = obs_date
            save_candidate(c, tf_db_path)

    # [TF-DATE-001] tradeflow_date_semantics — set plan date fields
    plan.plan_date = trade_date
    plan.effective_trade_date = eff_trade_date
    plan.observe_date = obs_date

    return plan


def save_plan(plan: DailyPlan, db_path: str) -> int:
    """Save daily plan to DB."""
    conn = sqlite3.connect(db_path)
    row = plan.to_db_row()
    try:
        conn.execute(
            "INSERT INTO tradeflow_daily_plans "
            "(trade_date, mode, summary, candidates_json, metadata_json, created_at, "
            "plan_date, effective_trade_date, observe_date) "  # [TF-DATE-001]
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, mode) DO UPDATE SET "
            "summary=excluded.summary, candidates_json=excluded.candidates_json, "
            "metadata_json=excluded.metadata_json, "
            "plan_date=excluded.plan_date, "  # [TF-DATE-001]
            "effective_trade_date=excluded.effective_trade_date, "
            "observe_date=excluded.observe_date",
            (row["trade_date"], row["mode"], row["summary"],
             row["candidates_json"], row["metadata_json"], row["created_at"],
             row["plan_date"], row["effective_trade_date"], row["observe_date"]),  # [TF-DATE-001]
        )
        conn.commit()
        row_id = conn.execute(
            "SELECT id FROM tradeflow_daily_plans WHERE trade_date=? AND mode=?",
            (row["trade_date"], row["mode"]),
        ).fetchone()[0]
        return row_id
    finally:
        conn.close()
