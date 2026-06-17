# [UI-001] tradeflow_api
# [PERF-001] runtime_tier_contract
"""TradeFlow read-only data access service.

Queries tradeflow SQLite tables for the API layer.
Does NOT trigger TA, run full market scan, or call LLM.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from api.runtime_tier import RuntimeTier, tradeflow_meta as _tradeflow_meta  # [PERF-001]
from tradingagents.tradeflow.symbol_utils import (  # [UI-008] tradeflow_field_normalization
    normalize_tradeflow_symbol,
    resolve_tradeflow_name,
)
from tradingagents.tradeflow.candidate_engine import get_filtered_symbols as _get_filtered_symbols  # [UI-007] tradeflow_filtered_trace


def _get_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_tradeflow_db_path() -> str:
    env_path = os.environ.get("TRADEFLOW_DB_PATH", "")
    if env_path:
        return env_path
    return os.path.join(_get_project_root(), "tradeflow.db")


def _get_prod_db_path() -> str:
    env_path = os.environ.get("DATABASE_URL", "")
    if env_path.startswith("sqlite:///"):
        return env_path.replace("sqlite:///", "", 1)
    return os.path.join(_get_project_root(), "tradingagents.db")


def _connect(tf_db_path: str = "") -> Optional[sqlite3.Connection]:
    db_path = tf_db_path or _get_tradeflow_db_path()
    if not os.path.exists(db_path):
        return None
    try:
        from tradingagents.tradeflow.candidate_engine import init_db  # [TF-P0-001] runtime_schema_name_observe_fix
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}
    except Exception:
        return set()


def _parse_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default if default is not None else []
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default if default is not None else []
    return value


def _rget(row: sqlite3.Row, col: str, default: Any = None) -> Any:
    try:
        val = row[col]
        return val
    except (KeyError, IndexError):
        return default


def _row_to_candidate_item(row: sqlite3.Row) -> dict:
    symbol = normalize_tradeflow_symbol(row["symbol"])  # [UI-008]
    raw_name = _rget(row, "name", "")
    name = resolve_tradeflow_name(symbol, raw_name)  # [UI-008]
    return {
        "symbol": symbol,
        "name": name,
        "tier": _rget(row, "tier", ""),
        "composite_score": _rget(row, "composite_score", 0.0) or 0.0,
        "score": _rget(row, "score", 0.0) or 0.0,
        "strategy_tags": _parse_json(_rget(row, "strategy_tags_json"), []),
        "primary_strategy": _rget(row, "primary_strategy", ""),
        "trigger_price": _rget(row, "trigger_price"),
        "support_price": _rget(row, "support_price"),
        "invalid_price": _rget(row, "invalid_price"),
        "need_deep_ta": bool(_rget(row, "need_deep_ta", 0)),
        "observe_state": _rget(row, "observe_state", "WAITING"),
        "observe_trigger_count": _rget(row, "observe_trigger_count", 0) or 0,
        "observe_first_trigger_time": _rget(row, "observe_first_trigger_time", ""),
        "tradeflow_data_completeness": _rget(row, "tradeflow_data_completeness", 0.0) or 0.0,
        "missing_data_fields": _parse_json(_rget(row, "missing_data_fields_json"), []),
        "data_completeness": _rget(row, "data_completeness", 0.0) or 0.0,
        "missing_evidence": _parse_json(_rget(row, "missing_evidence_json"), []),
        "game_balance": _rget(row, "game_balance", ""),
        "bull_case": _rget(row, "bull_case", ""),
        "bear_case": _rget(row, "bear_case", ""),
        "policy_case": _rget(row, "policy_case", ""),
        "fund_flow_case": _rget(row, "fund_flow_case", ""),
        "why_deep_ta": _rget(row, "why_deep_ta", ""),
        "why_not_deep_ta": _rget(row, "why_not_deep_ta", ""),
        "risk_flags": _parse_json(_rget(row, "risk_flags_json"), []),
        "policy_tags": _parse_json(_rget(row, "policy_tags_json"), []),
        "version_score": _rget(row, "version_score", 0.0) or 0.0,
        "narrative_score": _rget(row, "narrative_score", 0.0) or 0.0,
        "fund_flow_anomaly_score": _rget(row, "fund_flow_anomaly_score", 0.0) or 0.0,
        "fund_flow_anomaly_tags": _parse_json(_rget(row, "fund_flow_anomaly_tags_json"), []),
        "fund_flow_unit_verified": bool(_rget(row, "fund_flow_unit_verified", 0)),
        "evidence_gate_applied": bool(_rget(row, "evidence_gate_applied", 0)),
        "deep_ta_status": _rget(row, "deep_ta_status", ""),
        "deep_ta_dispatch_reason": _rget(row, "deep_ta_dispatch_reason", ""),
        "deep_ta_model": _rget(row, "deep_ta_model", ""),
        "source": _rget(row, "source", "manual"),
        "universe_sources": _parse_json(_rget(row, "universe_sources_json"), []),
        "resonance_count": _rget(row, "resonance_count", 0) or 0,
        "ta_budget_priority": _rget(row, "ta_budget_priority", 0) or 0,
        "tier_reason": _rget(row, "tier_reason", ""),
        "missing_evidence_for_upgrade": _parse_json(_rget(row, "missing_evidence_for_upgrade_json"), []),
        "candidate_type": _rget(row, "candidate_type", ""),  # [H-005] mandate_radar_ui
        "mandate_score": _rget(row, "mandate_score_component", 0.0) or 0.0,  # [H-005]
        "ambush_score": _rget(row, "ambush_score", 0.0) or 0.0,  # [H-005]
        "mandate_topic": _rget(row, "mandate_topic", ""),  # [H-005]
        "company_role": _rget(row, "company_role", ""),  # [H-005]
        "beneficiary_path": _parse_json(_rget(row, "beneficiary_path_json"), []),  # [H-005]
        "candidate_type_reason": _rget(row, "candidate_type_reason", ""),  # [H-005]
        "deep_ta_route": _rget(row, "deep_ta_route", ""),  # [H-005]
        "research_queue": _rget(row, "research_queue", ""),  # [H-007] mandate_ta_queue_router
        "research_intent": _rget(row, "research_intent", ""),  # [H-007]
        "research_route_reason": _rget(row, "research_route_reason", ""),  # [H-007]
        "watchlist_note": _rget(row, "watchlist_note", ""),  # [H-008] mandate_watchlist_note
        "watchlist_note_suggested": _rget(row, "watchlist_note_suggested", ""),  # [H-008]
        "watchlist_topic": _rget(row, "watchlist_topic", ""),  # [H-008]
        "watchlist_benefit_score": _rget(row, "watchlist_benefit_score", 0.0) or 0.0,  # [H-008]
        "watchlist_consensus_score": _rget(row, "watchlist_consensus_score", 0.0) or 0.0,  # [H-008]
        "watchlist_evidence_gap": _parse_json(_rget(row, "watchlist_evidence_gap_json"), []),  # [H-008]
        "plan_date": _rget(row, "plan_date", ""),  # [TF-DATE-001] tradeflow_date_semantics
        "effective_trade_date": _rget(row, "effective_trade_date", ""),  # [TF-DATE-001]
        "observe_date": _rget(row, "observe_date", ""),  # [TF-DATE-001]
        "action_tier": _rget(row, "action_tier", "scan"),  # [TF-UX-004]
        "trade_priority_score": _rget(row, "trade_priority_score", 0.0) or 0.0,  # [TF-UX-004]
        "action_tier_reason": _rget(row, "action_tier_reason", ""),  # [TF-UX-004]
        "counter_evidence": _parse_json(_rget(row, "counter_evidence_json"), []),  # [H-009]
        "overheat_flags": _parse_json(_rget(row, "overheat_flags_json"), []),  # [H-009]
        "downgrade_reasons": _parse_json(_rget(row, "downgrade_reasons_json"), []),  # [H-009]
        "what_would_change_mind": _parse_json(_rget(row, "what_would_change_mind_json"), []),  # [H-009]
        "topic_lifecycle_state": _rget(row, "topic_lifecycle_state", ""),  # [H-010] mandate_topic_lifecycle
        "topic_lifecycle_reason": _rget(row, "topic_lifecycle_reason", ""),  # [H-010]
        "topic_last_signal_date": _rget(row, "topic_last_signal_date", ""),  # [H-010]
        "topic_signal_count": _rget(row, "topic_signal_count", 0) or 0,  # [H-010]
        "contradiction_level": _rget(row, "contradiction_level", ""),  # [H-011] candidate_contradiction_explainer
        "contradiction_items": _parse_json(_rget(row, "contradiction_items_json"), []),  # [H-011]
        "blocking_evidence_gaps": _parse_json(_rget(row, "blocking_evidence_gaps_json"), []),  # [H-011]
        "next_verification_steps": _parse_json(_rget(row, "next_verification_steps_json"), []),  # [H-011]
        # [TF-QUALITY-002] score_separation — split scores for precision gate
        "technical_score": _rget(row, "technical_score", 0.0) or 0.0,
        "policy_score": _rget(row, "policy_score", 0.0) or 0.0,
        "fund_flow_score": _rget(row, "fund_flow_score", 0.0) or 0.0,
        "event_score": _rget(row, "event_score", 0.0) or 0.0,
        "risk_penalty_score": _rget(row, "risk_penalty_score", 0.0) or 0.0,
        "data_quality_score": _rget(row, "data_quality_score", 0.0) or 0.0,
        "ranking_reasons": _parse_json(_rget(row, "ranking_reasons_json"), []),
        "weakness_reasons": _parse_json(_rget(row, "weakness_reasons_json"), []),
        # [TF-QUALITY-003] candidate_precision_gate — fields for precision dimensions
        "mandate_score_component": _rget(row, "mandate_score_component", 0.0) or 0.0,
        "beneficiary_score_component": _rget(row, "beneficiary_score_component", 0.0) or 0.0,
        "overheat_penalty": _rget(row, "overheat_penalty", 0.0) or 0.0,
        "overheat_flags": _parse_json(_rget(row, "overheat_flags_json"), []),
        "created_at": _rget(row, "created_at", ""),
        "updated_at": _rget(row, "updated_at", ""),
    }


def _row_to_candidate_detail(row: sqlite3.Row) -> dict:
    item = _row_to_candidate_item(row)
    item.update({
        "evidence": _parse_json(_rget(row, "evidence_json"), {}),
        "policy_evidence_refs": _parse_json(_rget(row, "policy_evidence_refs_json"), []),
        "narrative_evidence_refs": _parse_json(_rget(row, "narrative_evidence_refs_json"), []),
        "narrative_reasons": _parse_json(_rget(row, "narrative_reasons_json"), []),
        "risk_evidence_refs": _parse_json(_rget(row, "risk_evidence_refs_json"), []),
        "risk_reasons": _parse_json(_rget(row, "risk_reasons_json"), []),
        "game_balance_refs": _parse_json(_rget(row, "game_balance_refs_json"), []),
        "fund_flow_anomaly_refs": _parse_json(_rget(row, "fund_flow_anomaly_refs_json"), []),
        "fund_flow_individual_summary": _rget(row, "fund_flow_individual_summary", ""),
        "fund_flow_board_summary": _rget(row, "fund_flow_board_summary", ""),
        "signal_category_hits": _parse_json(_rget(row, "signal_category_hits_json"), []),
        "positive_category_count": _rget(row, "positive_category_count", 0) or 0,
        "what_to_upgrade": _parse_json(_rget(row, "what_to_upgrade_json"), []),
        "deep_ta_report_path": _rget(row, "deep_ta_report_path", ""),
        "deep_ta_dispatch_time": _rget(row, "deep_ta_dispatch_time", ""),
        "deep_ta_position_context": _rget(row, "deep_ta_position_context", ""),
        "ambush_reasons": _parse_json(_rget(row, "ambush_reasons_json"), []),  # [H-005] mandate_radar_ui
        "ambush_evidence_refs": _parse_json(_rget(row, "ambush_evidence_refs_json"), []),  # [H-005]
        "mandate_evidence_refs": _parse_json(_rget(row, "mandate_evidence_refs_json"), []),  # [H-005]
    })
    return item


def _compute_action(item: dict) -> str:
    if item.get("need_deep_ta"):
        return "NEED_DEEP_TA"
    trigger = item.get("trigger_price")
    if trigger:
        return "WAIT_TRIGGER"
    return "OBSERVE"


def _compute_summary(items: List[dict]) -> dict:
    tier_counts = {"A": 0, "B": 0, "C": 0}
    deep_ta_count = 0
    total_comp = 0.0
    for it in items:
        t = it.get("tier", "")
        if t in tier_counts:
            tier_counts[t] += 1
        if it.get("need_deep_ta"):
            deep_ta_count += 1
        total_comp += it.get("tradeflow_data_completeness", 0.0)
    return {
        "total_candidates": len(items),
        "tier_a_count": tier_counts["A"],
        "tier_b_count": tier_counts["B"],
        "tier_c_count": tier_counts["C"],
        "need_deep_ta_count": deep_ta_count,
        "avg_completeness": round(total_comp / max(len(items), 1), 3),
    }


def _load_filtered_trace_candidates(
    trade_date: str,
    tf_db_path: str = "",
    allowed_symbols: Optional[set[str]] = None,
) -> list[dict]:
    """Load persisted filtered trace rows in pool-gate compatible shape."""
    db_path = tf_db_path or _get_tradeflow_db_path()
    if not os.path.exists(db_path):
        return []
    trace_dates = _filtered_trace_dates_for_view(trade_date, db_path)
    rows: list[dict] = []
    for trace_date in trace_dates:
        try:
            rows.extend(_get_filtered_symbols(trace_date, db_path))
        except Exception:
            continue

    items: list[dict] = []
    for row in rows:
        symbol = normalize_tradeflow_symbol(row.get("symbol", ""))
        if not symbol:
            continue
        if allowed_symbols is not None and symbol not in allowed_symbols:
            continue
        reason = row.get("reason", "")
        items.append({
            "symbol": symbol,
            "name": resolve_tradeflow_name(symbol, row.get("name", "")),
            "source": row.get("source", ""),
            "reason": reason,
            "pool_filter_reason": reason,
            "pool_status": "filtered",
            "run_id": row.get("run_id", ""),
            "created_at": row.get("created_at", ""),
        })
    return items


def _filtered_trace_dates_for_view(trade_date: str, db_path: str) -> list[str]:
    """Resolve plan dates whose filtered traces should appear for a view date."""
    dates = [trade_date]
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            for table in ("tradeflow_daily_plans", "tradeflow_candidates", "tradeflow_filtered_symbols"):
                columns = _table_columns(conn, table)
                if {"trade_date", "effective_trade_date"}.issubset(columns):
                    rows = conn.execute(
                        f"SELECT DISTINCT trade_date FROM {table} "
                        "WHERE effective_trade_date = ? AND trade_date != ?",
                        (trade_date, trade_date),
                    ).fetchall()
                    for row in rows:
                        plan_date = row["trade_date"]
                        if plan_date and plan_date not in dates:
                            dates.append(plan_date)
        finally:
            conn.close()
    except Exception:
        pass
    return dates


def _merge_filtered_candidates(current: list[dict], persisted: list[dict]) -> list[dict]:
    """Merge live pool-gate filtered rows with persisted filtered trace rows."""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in [*current, *persisted]:
        symbol = normalize_tradeflow_symbol(item.get("symbol", ""))
        reason = item.get("pool_filter_reason") or item.get("reason", "")
        key = (symbol, reason)
        if not symbol or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _pool_counts_with_filtered(pool_counts: dict, filtered_candidates: list[dict]) -> dict:
    """Keep pool count badges aligned with merged filtered trace rows."""
    counts = dict(pool_counts or {})
    counts["filtered"] = len(filtered_candidates)
    return counts


def _pool_gate_summary_with_counts(pool_counts: dict) -> str:
    """Render a pool summary from final response counts."""
    return (
        f"主候选{pool_counts.get('main', 0)}只，"
        f"观察{pool_counts.get('observation', 0)}只，"
        f"过滤{pool_counts.get('filtered', 0)}只。"
    )


def _query_by_date_or_effective(conn, table: str, date_str: str,
                                extra_conditions: list[str] = None,
                                extra_params: list = None,
                                order_clause: str = "") -> list:
    """Query table by effective_trade_date, falling back to trade_date.  # [TF-DATE-001]
    
    Tries: WHERE effective_trade_date = ? ... UNION WHERE trade_date = ? AND effective_trade_date = ''
    This finds candidates generated on non-trading days (e.g. 2026-05-31) that
    are effective for the queried trading day (e.g. 2026-06-01).
    """
    columns = _table_columns(conn, table)
    has_eff = "effective_trade_date" in columns
    conditions = list(extra_conditions or [])
    params = list(extra_params or [])

    if has_eff:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE "
            f"(effective_trade_date = ? OR (trade_date = ? AND (effective_trade_date = '' OR effective_trade_date IS NULL))) "
            f"{' AND ' + ' AND '.join(conditions) if conditions else ''}"
            f"{order_clause}",
            [date_str, date_str] + params,
        ).fetchall()
    else:
        conditions.insert(0, "trade_date = ?")
        params.insert(0, date_str)
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(conditions)}{order_clause}",
            params,
        ).fetchall()
    return rows


def get_daily_plan(trade_date: str, tf_db_path: str = "") -> dict:
    _fast_meta = _tradeflow_meta("tradeflow_daily_plan")  # [PERF-001]
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}  # [PERF-001]

    try:
        plan_row = conn.execute(
            "SELECT * FROM tradeflow_daily_plans WHERE trade_date = ? AND mode = 'pre_market' ORDER BY created_at DESC LIMIT 1",
            (trade_date,),
        ).fetchone()

        if plan_row is None:
            plan_columns = _table_columns(conn, "tradeflow_daily_plans") if conn else set()
            if "effective_trade_date" in plan_columns:
                plan_row = conn.execute(
                    "SELECT * FROM tradeflow_daily_plans WHERE effective_trade_date = ? AND mode = 'pre_market' ORDER BY created_at DESC LIMIT 1",
                    (trade_date,),
                ).fetchone()
        if plan_row is None:
            return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}  # [PERF-001]

        candidates_json = _parse_json(_rget(plan_row, "candidates_json", "[]"), [])
        metadata_json = _parse_json(_rget(plan_row, "metadata_json", "{}"), {})

        candidate_items = []
        for entry in candidates_json:
            sym = normalize_tradeflow_symbol(entry.get("symbol", ""))  # [UI-008]
            raw_name = entry.get("name", "")
            n = resolve_tradeflow_name(sym, raw_name)  # [UI-008]
            item = {
                "symbol": sym,
                "name": n,
                "tier": entry.get("tier", ""),
                "composite_score": entry.get("composite_score", 0.0) or 0.0,
                "score": entry.get("score", 0.0) or 0.0,
                "strategy_tags": entry.get("strategies", entry.get("strategy_tags", [])),
                "primary_strategy": entry.get("primary_strategy", ""),
                "trigger_price": entry.get("trigger_price"),
                "support_price": entry.get("support_price"),
                "invalid_price": entry.get("invalid_price"),
                "need_deep_ta": entry.get("need_deep_ta", False),
                "action": entry.get("action", "OBSERVE"),
                "reason": entry.get("reason", ""),
                "observe_state": entry.get("observe_state", "WAITING"),
                "observe_trigger_count": entry.get("observe_trigger_count", 0),
                "observe_first_trigger_time": entry.get("observe_first_trigger_time", ""),
                "tradeflow_data_completeness": entry.get("tradeflow_data_completeness", 0.0),
                "missing_data_fields": entry.get("missing_data_fields", []),
                "data_completeness": entry.get("data_completeness", 0.0),
                "missing_evidence": entry.get("missing_evidence", []),
                "game_balance": entry.get("game_balance", ""),
                "bull_case": entry.get("bull_case", ""),
                "bear_case": entry.get("bear_case", ""),
                "policy_case": entry.get("policy_case", ""),
                "fund_flow_case": entry.get("fund_flow_case", ""),
                "why_deep_ta": entry.get("why_deep_ta", ""),
                "why_not_deep_ta": entry.get("why_not_deep_ta", ""),
                "risk_flags": entry.get("risk_flags", []),
                "policy_tags": entry.get("policy_tags", []),
                "version_score": entry.get("version_score", 0.0),
                "narrative_score": entry.get("narrative_score", 0.0),
                "fund_flow_anomaly_score": entry.get("fund_flow_anomaly_score", 0.0),
                "fund_flow_anomaly_tags": entry.get("fund_flow_anomaly_tags", []),
                "fund_flow_unit_verified": entry.get("fund_flow_unit_verified", False),
                "evidence_gate_applied": entry.get("evidence_gate_applied", False),
                "deep_ta_status": entry.get("deep_ta_status", ""),
                "deep_ta_dispatch_reason": entry.get("deep_ta_dispatch_reason", ""),
                "deep_ta_model": entry.get("deep_ta_model", ""),
                "source": entry.get("source", "manual"),
                "universe_sources": entry.get("universe_sources", []),
                "resonance_count": entry.get("resonance_count", 0),
                "ta_budget_priority": entry.get("ta_budget_priority", 0),
                "tier_reason": entry.get("tier_reason", ""),
                "missing_evidence_for_upgrade": entry.get("missing_evidence_for_upgrade", []),
                "candidate_type": entry.get("candidate_type", ""),
                "mandate_score": entry.get("mandate_score", 0.0) or 0.0,
                "ambush_score": entry.get("ambush_score", 0.0) or 0.0,
                "mandate_topic": entry.get("mandate_topic", ""),
                "company_role": entry.get("company_role", ""),
                "technical_score": entry.get("technical_score", 0.0) or 0.0,
                "policy_score": entry.get("policy_score", 0.0) or 0.0,
                "fund_flow_score": entry.get("fund_flow_score", 0.0) or 0.0,
                "event_score": entry.get("event_score", 0.0) or 0.0,
                "ranking_reasons": entry.get("ranking_reasons", []),
                "weakness_reasons": entry.get("weakness_reasons", []),
                "risk_penalty_score": entry.get("risk_penalty_score", 0.0) or 0.0,
                "data_quality_score": entry.get("data_quality_score", 0.0) or 0.0,
                "created_at": _rget(plan_row, "created_at", ""),
                "updated_at": _rget(plan_row, "created_at", ""),
            }
            candidate_items.append(item)

        return {
            "status": "ok",
            "trade_date": _rget(plan_row, "trade_date", trade_date),
            "mode": _rget(plan_row, "mode", "pre_market"),
            "summary": _rget(plan_row, "summary", ""),
            "candidates": candidate_items,
            "metadata": metadata_json,
            "summary_agg": _compute_summary(candidate_items),
            "created_at": _rget(plan_row, "created_at", ""),
            "plan_date": _rget(plan_row, "plan_date", ""),  # [TF-DATE-001]
            "effective_trade_date": _rget(plan_row, "effective_trade_date", ""),  # [TF-DATE-001]
            "observe_date": _rget(plan_row, "observe_date", ""),  # [TF-DATE-001]
            "runtime_tier_meta": _tradeflow_meta("tradeflow_daily_plan"),  # [PERF-001]
        }
    finally:
        conn.close()


def get_candidates(
    trade_date: str,
    tier: Optional[str] = None,
    need_deep_ta: Optional[bool] = None,
    candidate_type: Optional[str] = None,  # [H-005] mandate_radar_ui
    pool: Optional[str] = None,  # [TF-P0-002] tradeflow_pool_split
    tf_db_path: str = "",
) -> dict:
    # [TF-P0-002] tradeflow_pool_split — resolve pool to candidate_type list
    pool_types: list[str] = []
    if pool and not candidate_type:
        from tradingagents.tradeflow.candidate_pool import pool_to_candidate_types
        pool_types = pool_to_candidate_types(pool)
        if len(pool_types) == 1:
            candidate_type = pool_types[0]
        elif len(pool_types) == 0:
            pass  # "all" pool
    _fast_meta = _tradeflow_meta("tradeflow_candidates")  # [PERF-001]
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}  # [PERF-001]

    try:
        columns = _table_columns(conn, "tradeflow_candidates")
        extra_conditions = ["status = 'active'" if "status" in columns else None]
        extra_conditions = [c for c in extra_conditions if c is not None]
        if tier and "tier" in columns:
            extra_conditions.append("tier = ?")
        if need_deep_ta is not None:
            extra_conditions.append("need_deep_ta = ?")
        if candidate_type and "candidate_type" in columns:  # [H-005] mandate_radar_ui
            extra_conditions.append("candidate_type = ?")
        elif len(pool_types) > 1 and "candidate_type" in columns:  # [TF-P0-002] tradeflow_pool_split
            placeholders = ",".join(["?"] * len(pool_types))
            extra_conditions.append(f"candidate_type IN ({placeholders})")

        extra_params: list = []
        if tier and "tier" in columns:
            extra_params.append(tier)
        if need_deep_ta is not None:
            extra_params.append(1 if need_deep_ta else 0)
        if candidate_type and "candidate_type" in columns:  # [H-005] mandate_radar_ui
            extra_params.append(candidate_type)
        elif len(pool_types) > 1:  # [TF-P0-002]
            extra_params.extend(pool_types)

        order_cols = []
        if "trade_priority_score" in columns:
            order_cols.append("trade_priority_score DESC")
        if "composite_score" in columns:
            order_cols.append("composite_score DESC")
        if "score" in columns:
            order_cols.append("score DESC")
        order_clause = f" ORDER BY {', '.join(order_cols)}" if order_cols else " ORDER BY updated_at DESC, created_at DESC"

        rows = _query_by_date_or_effective(
            conn, "tradeflow_candidates", trade_date,
            extra_conditions=extra_conditions if extra_conditions else None,
            extra_params=extra_params if extra_params else None,
            order_clause=order_clause,
        )

        items = [_row_to_candidate_item(r) for r in rows]
        for it in items:
            it["action"] = _compute_action(it)
            it["reason"] = it.get("why_deep_ta") or it.get("why_not_deep_ta") or "候选观察"

        _recompute_action_tiers(conn, items, trade_date)

        # [TF-QUALITY-001A] pool_gate_contract — keep legacy candidates intact
        # while exposing the strict main/observation/filtered split separately.
        from tradingagents.tradeflow.candidate_pool_gate import run_pool_gate
        pool_result = run_pool_gate(items)
        filter_active = bool(tier or need_deep_ta is not None or candidate_type or pool_types)
        allowed_filtered_symbols = {it["symbol"] for it in items} if filter_active else None
        filtered_candidates = _merge_filtered_candidates(
            pool_result.filtered_candidates,
            _load_filtered_trace_candidates(
                trade_date,
                tf_db_path,
                allowed_symbols=allowed_filtered_symbols,
            ),
        )
        pool_counts = _pool_counts_with_filtered(pool_result.pool_counts, filtered_candidates)

        return {
            "status": "ok",
            "trade_date": trade_date,
            "candidates": items,
            "main_candidates": pool_result.main_candidates,
            "observation_candidates": pool_result.observation_candidates,
            "filtered_candidates": filtered_candidates,
            "pool_counts": pool_counts,
            "pool_gate_summary": _pool_gate_summary_with_counts(pool_counts),
            "summary_agg": _compute_summary(items),
            "main_summary_agg": _compute_summary(pool_result.main_candidates),
            "runtime_tier_meta": _tradeflow_meta("tradeflow_candidates"),  # [PERF-001]
        }
    finally:
        conn.close()


def get_candidate_detail(symbol: str, trade_date: str, tf_db_path: str = "") -> dict:
    conn = _connect(tf_db_path)
    _fast_meta = _tradeflow_meta("tradeflow_candidate_detail")  # [PERF-001]
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}  # [PERF-001]

    try:
        row = conn.execute(
            "SELECT * FROM tradeflow_candidates WHERE trade_date = ? AND symbol = ?",
            (trade_date, symbol),
        ).fetchone()

        if row is None:
            columns = _table_columns(conn, "tradeflow_candidates")
            if "effective_trade_date" in columns:
                row = conn.execute(
                    "SELECT * FROM tradeflow_candidates WHERE effective_trade_date = ? AND symbol = ?",
                    (trade_date, symbol),
                ).fetchone()

        if row is None:
            return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}  # [PERF-001]

        detail = _row_to_candidate_detail(row)
        detail["action"] = _compute_action(detail)
        detail["reason"] = detail.get("why_deep_ta") or detail.get("why_not_deep_ta") or "候选观察"

        return {
            "status": "ok",
            "trade_date": trade_date,
            "candidate": detail,
            "runtime_tier_meta": _tradeflow_meta("tradeflow_candidate_detail"),  # [PERF-001]
        }
    finally:
        conn.close()


def _precheck_observe_state(db_path: str, trade_date: str) -> dict:
    """Quick check whether candidates and observe signals exist for a date.  # [TF-OBS-002] observe_auto_run

    Returns dict with:
    - has_candidates: bool
    - has_signals_for_date: bool
    - last_observed_at: str
    - reason: str (explanation when candidates are absent)
    """
    from tradingagents.tradeflow.candidate_engine import init_db
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()}
        has_eff = "effective_trade_date" in columns

        if has_eff:
            cnt_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM tradeflow_candidates WHERE "
                "(effective_trade_date = ? OR (trade_date = ? AND (effective_trade_date = '' OR effective_trade_date IS NULL))) "
                "AND status = 'active'",
                (trade_date, trade_date),
            ).fetchone()
        else:
            cnt_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM tradeflow_candidates WHERE trade_date = ? AND status = 'active'",
                (trade_date,),
            ).fetchone()
        has_candidates = cnt_row is not None and cnt_row["cnt"] > 0

        has_signals_for_date = False
        last_observed_at = ""
        try:
            sig_rows = conn.execute(
                "SELECT signal_time, evidence_json FROM tradeflow_signals "
                "WHERE signal_type LIKE 'observe_%' ORDER BY signal_time DESC LIMIT 50"
            ).fetchall()
            for sr in sig_rows:
                ev = json.loads(sr["evidence_json"]) if sr["evidence_json"] else {}
                if ev.get("trade_date") == trade_date:
                    has_signals_for_date = True
                    last_observed_at = sr["signal_time"]
                    break
            if not last_observed_at and sig_rows:
                last_observed_at = sig_rows[0]["signal_time"]
        except Exception:
            pass

        reason = ""
        if not has_candidates:
            total = conn.execute("SELECT COUNT(*) AS cnt FROM tradeflow_candidates").fetchone()
            if total and total["cnt"] > 0:
                reason = "当日无活跃候选"
            else:
                reason = "无计划"

        return {
            "has_candidates": has_candidates,
            "has_signals_for_date": has_signals_for_date,
            "last_observed_at": last_observed_at,
            "reason": reason,
        }
    finally:
        conn.close()


def get_observe(trade_date: str, tf_db_path: str = "") -> dict:
    tf_db = tf_db_path or _get_tradeflow_db_path()
    _fast_meta = _tradeflow_meta("tradeflow_observe")  # [PERF-001]

    if not os.path.exists(tf_db):
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "observe_auto_run": False,        # [TF-OBS-002] observe_auto_run
            "last_observed_at": "",           # [TF-OBS-002]
            "observe_reason": "无数据库",      # [TF-OBS-002]
            "observe_items": [],
            "triggered_count": 0,
            "invalidated_count": 0,
            "waiting_count": 0,
            "runtime_tier_meta": _fast_meta,
        }

    # [TF-OBS-002] observe_auto_run — auto-run observe check when page opens
    observe_auto_run = False
    observe_reason = ""
    last_observed_at = ""

    _pre = _precheck_observe_state(tf_db, trade_date)
    last_observed_at = _pre["last_observed_at"]

    if not _pre["has_candidates"]:
        observe_reason = _pre["reason"]
    elif not _pre["has_signals_for_date"]:
        _today_str = datetime.now().strftime("%Y-%m-%d")
        if trade_date != _today_str:
            observe_reason = "非当日，跳过自动观察"
        else:
            # Auto-run observe check so page isn't blank on first open
            try:
                from tradingagents.tradeflow.candidate_engine import init_db
                from tradingagents.tradeflow.observe_runner import run_observe
                init_db(tf_db)
                _auto = run_observe(trade_date=trade_date, db_path=tf_db)
                observe_auto_run = True
                last_observed_at = _auto.run_time
                if _auto.skipped_reason:
                    observe_reason = _auto.skipped_reason
            except Exception as e:
                observe_reason = f"自动执行失败: {e}"

    conn = _connect(tf_db_path)
    if conn is None:
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "observe_auto_run": observe_auto_run,
            "last_observed_at": last_observed_at,
            "observe_reason": observe_reason,
            "observe_items": [],
            "triggered_count": 0,
            "invalidated_count": 0,
            "waiting_count": 0,
            "runtime_tier_meta": _fast_meta,
        }

    try:
        columns = _table_columns(conn, "tradeflow_candidates")
        extra_conditions = []
        if "status" in columns:
            extra_conditions.append("status = 'active'")
        order_clause = " ORDER BY composite_score DESC" if "composite_score" in columns else " ORDER BY updated_at DESC, created_at DESC"
        rows = _query_by_date_or_effective(
            conn, "tradeflow_candidates", trade_date,
            extra_conditions=extra_conditions if extra_conditions else None,
            order_clause=order_clause,
        )

        items = []
        triggered = 0
        invalidated = 0
        waiting = 0
        for r in rows:
            state = _rget(r, "observe_state", "WAITING")
            sym = normalize_tradeflow_symbol(r["symbol"])  # [UI-008]
            raw_n = _rget(r, "name", "")

            # [TF-OBS-001] tradeflow_observe_runner — populate current_price/trigger_reason from latest signal
            current_price = None
            trigger_reason = ""
            try:
                sig_rows = conn.execute(
                    "SELECT evidence_json FROM tradeflow_signals WHERE symbol = ? AND signal_type LIKE 'observe_%' ORDER BY signal_time DESC LIMIT 20",
                    (sym,),
                ).fetchall()
                for sig_row in sig_rows:
                    ev = _parse_json(_rget(sig_row, "evidence_json", "{}"), {})
                    if ev.get("trade_date") != trade_date:
                        continue
                    current_price = ev.get("current_price")
                    trigger_reason = ev.get("trigger_reason", "")
                    break
            except Exception:
                pass

            item = {
                "symbol": sym,
                "name": resolve_tradeflow_name(sym, raw_n),  # [UI-008]
                "observe_state": state,
                "trigger_price": _rget(r, "trigger_price"),
                "invalid_price": _rget(r, "invalid_price"),
                "observe_trigger_count": _rget(r, "observe_trigger_count", 0) or 0,
                "observe_first_trigger_time": _rget(r, "observe_first_trigger_time", ""),
                "tier": _rget(r, "tier", ""),
                "composite_score": _rget(r, "composite_score", 0.0) or 0.0,
                "current_price": current_price,
                "trigger_reason": trigger_reason,
                "strategy_tags": _parse_json(_rget(r, "strategy_tags_json"), []),
            }
            items.append(item)
            if state == "TRIGGERED":
                triggered += 1
            elif state == "INVALIDATED":
                invalidated += 1
            else:
                waiting += 1

        return {
            "status": "ok",
            "trade_date": trade_date,
            "observe_items": items,
            "triggered_count": triggered,
            "invalidated_count": invalidated,
            "waiting_count": waiting,
            "observe_auto_run": observe_auto_run,        # [TF-OBS-002] observe_auto_run
            "last_observed_at": last_observed_at,         # [TF-OBS-002]
            "observe_reason": observe_reason,             # [TF-OBS-002]
            "runtime_tier_meta": _tradeflow_meta("tradeflow_observe"),  # [PERF-001]
        }
    finally:
        conn.close()


def get_ta_queue(trade_date: str, tf_db_path: str = "") -> dict:
    conn = _connect(tf_db_path)
    _fast_meta = _tradeflow_meta("tradeflow_ta_queue")  # [PERF-001]
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}  # [PERF-001]

    try:
        columns = _table_columns(conn, "tradeflow_candidates")
        order_cols = []
        if "ta_budget_priority" in columns:
            order_cols.append("ta_budget_priority DESC")
        if "composite_score" in columns:
            order_cols.append("composite_score DESC")
        if "score" in columns:
            order_cols.append("score DESC")
        order_clause = f" ORDER BY {', '.join(order_cols)}" if order_cols else " ORDER BY updated_at DESC, created_at DESC"
        rows = _query_by_date_or_effective(
            conn, "tradeflow_candidates", trade_date,
            extra_conditions=["need_deep_ta = 1"],
            order_clause=order_clause,
        )

        items = []
        dispatched = 0
        blocked = 0
        pending = 0
        for r in rows:
            status_val = _rget(r, "deep_ta_status", "")
            sym = normalize_tradeflow_symbol(r["symbol"])  # [UI-008]
            raw_n = _rget(r, "name", "")
            item = {
                "symbol": sym,
                "name": resolve_tradeflow_name(sym, raw_n),  # [UI-008]
                "tier": _rget(r, "tier", ""),
                "ta_budget_priority": _rget(r, "ta_budget_priority", 0) or 0,
                "need_deep_ta": bool(_rget(r, "need_deep_ta", 0)),
                "deep_ta_status": status_val,
                "deep_ta_dispatch_reason": _rget(r, "deep_ta_dispatch_reason", ""),
                "deep_ta_model": _rget(r, "deep_ta_model", ""),
                "why_deep_ta": _rget(r, "why_deep_ta", ""),
                "why_not_deep_ta": _rget(r, "why_not_deep_ta", ""),
                "composite_score": _rget(r, "composite_score", 0.0) or 0.0,
                "tradeflow_data_completeness": _rget(r, "tradeflow_data_completeness", 0.0) or 0.0,
                "evidence_gate_applied": bool(_rget(r, "evidence_gate_applied", 0)),
            }
            items.append(item)
            if status_val == "DISPATCHED":
                dispatched += 1
            elif status_val == "BLOCKED":
                blocked += 1
            elif status_val in ("", "PENDING"):
                pending += 1

        return {
            "status": "ok",
            "trade_date": trade_date,
            "queue": items,
            "total_in_queue": len(items),
            "dispatched_count": dispatched,
            "blocked_count": blocked,
            "pending_count": pending,
            "runtime_tier_meta": _tradeflow_meta("tradeflow_ta_queue"),  # [PERF-001]
        }
    finally:
        conn.close()


def _get_available_dates(tf_db_path: str = "") -> list[str]:
    """Return all distinct plan dates that have at least one candidate.  # [TF-REVIEW-002]

    Used by the Review page to find the most recent plan with candidates
    when the queried date has no data.
    """
    db_path = tf_db_path or _get_tradeflow_db_path()
    dates: list[str] = []
    if not os.path.exists(db_path):
        return dates
    try:
        conn = _connect(db_path)
        if conn is None:
            return dates
        try:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM tradeflow_candidates ORDER BY trade_date DESC"
            ).fetchall()
            for row in rows:
                d = row["trade_date"]
                if d and d not in dates:
                    dates.append(d)
        finally:
            conn.close()
    except Exception:
        pass
    return dates


def get_review(trade_date: str, tf_db_path: str = "") -> dict:
    # [TF-REVIEW-002] review_date_mapping
    _fast_meta = _tradeflow_meta("tradeflow_review")  # [PERF-001]
    plan_data = get_daily_plan(trade_date, tf_db_path)

    # If no plan for this exact date, try effective_trade_date mapping
    # (e.g. plan generated on 5/31 weekend, review on 6/1 trading day)
    if plan_data.get("status") == "no_data":
        from tradingagents.tradeflow.candidate_engine import init_db  # noqa
        from tradingagents.tradeflow.date_semantics import find_latest_plan_date

        available_dates = _get_available_dates(tf_db_path)
        if available_dates:
            best = find_latest_plan_date(available_dates, trade_date)
            if best and best != trade_date:
                plan_data = get_daily_plan(best, tf_db_path)

    if plan_data.get("status") == "no_data":
        # [TF-REVIEW-002] review_date_mapping — return clear data_status
        from tradingagents.tradeflow.post_market_review import ReviewDataStatus
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "data_status": ReviewDataStatus.NO_CANDIDATES.value,
            "data_status_message": ReviewDataStatus.NO_CANDIDATES.message_cn,
            "runtime_tier_meta": _fast_meta,
        }

    candidates = plan_data.get("candidates", [])
    results = []
    for entry in candidates:
        action = entry.get("action", "OBSERVE")
        result = {
            "symbol": entry.get("symbol", ""),
            "name": entry.get("name", ""),
            "plan_action": action,
            "keep_observing": action != "REMOVE_FROM_WATCH",
            "reason": "",
            "tier": entry.get("tier", ""),
            "composite_score": entry.get("composite_score", 0.0),
            "strategy_tags": entry.get("strategy_tags", []),
            "trigger_price": entry.get("trigger_price"),
            "invalid_price": entry.get("invalid_price"),
            "observe_state": entry.get("observe_state", "WAITING"),
        }
        if action == "REMOVE_FROM_WATCH":
            result["reason"] = "已标记移除"
        elif entry.get("trigger_price"):
            result["reason"] = f"继续观察，关注触发价 {entry['trigger_price']}"
        else:
            result["reason"] = "继续观察"
        results.append(result)

    # [TF-REVIEW-002] review_date_mapping — include plan_date/effective_trade_date
    return {
        "status": "ok",
        "trade_date": trade_date,
        "reviewed_at": datetime.now().isoformat(),
        "results": results,
        "summary_agg": _compute_summary(candidates),
        "plan_date": plan_data.get("plan_date", ""),  # [TF-DATE-001]
        "effective_trade_date": plan_data.get("effective_trade_date", ""),  # [TF-DATE-001]
        "data_status": "OK",
        "data_status_message": "数据正常",
        "runtime_tier_meta": _tradeflow_meta("tradeflow_review"),  # [PERF-001]
    }


def get_data_health(tf_db_path: str = "") -> dict:
    db_path = tf_db_path or _get_tradeflow_db_path()
    db_available = os.path.exists(db_path)

    sources: List[Dict[str, Any]] = []
    latest_plan_date = None
    latest_candidates_date = None
    latest_eff_date = None  # [TF-DATE-001]
    latest_obs_date = None  # [TF-DATE-001]
    total_candidates_today = 0
    total_signals_today = 0
    latest_observe_check_time = None  # [TF-OBS-001]
    latest_signal_time = None  # [TF-OBS-001]
    today = datetime.now().strftime("%Y-%m-%d")

    if db_available:
        conn = _connect(db_path)
        if conn is not None:
            try:
                plan_row = conn.execute(
                    "SELECT trade_date FROM tradeflow_daily_plans ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                if plan_row:
                    latest_plan_date = plan_row["trade_date"]

                cand_row = conn.execute(
                    "SELECT trade_date FROM tradeflow_candidates ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                if cand_row:
                    latest_candidates_date = cand_row["trade_date"]

                if "effective_trade_date" in _table_columns(conn, "tradeflow_candidates"):
                    eff_row = conn.execute(
                        "SELECT effective_trade_date FROM tradeflow_candidates WHERE effective_trade_date != '' ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    if eff_row:
                        latest_eff_date = eff_row["effective_trade_date"]
                if "observe_date" in _table_columns(conn, "tradeflow_candidates"):
                    obs_row = conn.execute(
                        "SELECT observe_date FROM tradeflow_candidates WHERE observe_date != '' ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    if obs_row:
                        latest_obs_date = obs_row["observe_date"]

                count_row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM tradeflow_candidates WHERE trade_date = ?",
                    (today,),
                ).fetchone()
                if count_row:
                    total_candidates_today = count_row["cnt"]

                sig_row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM tradeflow_signals WHERE date(signal_time) = ?",
                    (today,),
                ).fetchone()
                if sig_row:
                    total_signals_today = sig_row["cnt"]

                # [TF-OBS-001] tradeflow_observe_runner — latest observe check and signal times
                obs_check_row = conn.execute(
                    "SELECT signal_time FROM tradeflow_signals WHERE signal_type LIKE 'observe_%' ORDER BY signal_time DESC LIMIT 1"
                ).fetchone()
                if obs_check_row:
                    latest_observe_check_time = obs_check_row["signal_time"]
                sig_time_row = conn.execute(
                    "SELECT signal_time FROM tradeflow_signals ORDER BY signal_time DESC LIMIT 1"
                ).fetchone()
                if sig_time_row:
                    latest_signal_time = sig_time_row["signal_time"]

                sources.append({
                    "name": "tradeflow_daily_plans",
                    "available": True,
                    "status": "OK",
                    "fallback_vendor": "",
                    "last_updated": latest_plan_date,
                    "record_count": conn.execute("SELECT COUNT(*) FROM tradeflow_daily_plans").fetchone()[0],
                })
                sources.append({
                    "name": "tradeflow_candidates",
                    "available": True,
                    "status": "OK",
                    "fallback_vendor": "",
                    "last_updated": latest_candidates_date,
                    "record_count": conn.execute("SELECT COUNT(*) FROM tradeflow_candidates").fetchone()[0],
                })
                sources.append({
                    "name": "tradeflow_signals",
                    "available": True,
                    "status": "OK",
                    "fallback_vendor": "",
                    "last_updated": today if total_signals_today else None,
                    "record_count": conn.execute("SELECT COUNT(*) FROM tradeflow_signals").fetchone()[0],
                })
            except Exception:
                sources.append({
                    "name": "tradeflow_db",
                    "available": False,
                    "status": "FAILED",
                    "fallback_vendor": "",
                    "error": "query failed",
                })
            finally:
                conn.close()
    else:
        sources.append({
            "name": "tradeflow_db",
            "available": False,
            "status": "FAILED",
            "fallback_vendor": "",
            "error": "database not found",
        })

    return {
        "status": "ok",
        "tradeflow_db_available": db_available,
        "tradeflow_db_path": "SET" if db_path else "NOT_SET",
        "sources": sources,
        "latest_plan_date": latest_plan_date,
        "latest_candidates_date": latest_candidates_date,
        "latest_effective_trade_date": latest_eff_date,  # [TF-DATE-001]
        "latest_observe_date": latest_obs_date,  # [TF-DATE-001]
        "total_candidates_today": total_candidates_today,
        "total_signals_today": total_signals_today,
        "latest_observe_check_time": latest_observe_check_time,  # [TF-OBS-001]
        "latest_signal_time": latest_signal_time,  # [TF-OBS-001]
        "evidence_contract_available": True,  # [DATA-004] raw_evidence_contract
        "evidence_coverage_audit_available": True,  # [DATA-007] evidence_coverage_audit
        "runtime_tier_meta": _tradeflow_meta("tradeflow_data_health"),  # [PERF-001]
    }


def get_filtered(trade_date: str, tf_db_path: str = "") -> dict:  # [UI-007] tradeflow_filtered_trace
    db_path = tf_db_path or _get_tradeflow_db_path()
    if not os.path.exists(db_path):
        return {"status": "ok", "trade_date": trade_date, "filtered": [], "filter_breakdown": {}}
    try:
        from tradingagents.tradeflow.candidate_engine import init_db
        init_db(db_path)
        items = _get_filtered_symbols(trade_date, db_path)
        breakdown: dict[str, int] = {}
        for it in items:
            reason = it.get("reason", "")
            if "流动性" in reason:
                cat = "流动性差"
            elif "数据" in reason:
                cat = "数据缺失"
            elif "无策略" in reason:
                cat = "无策略命中"
            else:
                cat = "其他"
            breakdown[cat] = breakdown.get(cat, 0) + 1
        return {
            "status": "ok",
            "trade_date": trade_date,
            "filtered": items,
            "filter_breakdown": breakdown,
            "runtime_tier_meta": _tradeflow_meta("tradeflow_filtered"),  # [PERF-001]
        }
    except Exception:
        return {"status": "ok", "trade_date": trade_date, "filtered": [], "filter_breakdown": {}}


def run_discovery_scan(
    trade_date: str,
    symbols: Optional[List[str]] = None,
    top_n: int = 20,
    include_holdings: bool = True,
    include_watchlist: bool = True,
    use_event_source: bool = False,
    news_texts: Optional[List[str]] = None,
    save_candidates: bool = True,
    tf_db_path: str = "",
    prod_db_path: str = "",
) -> dict:
    """Run a bounded TradeFlow discovery scan for UI-triggered candidate generation.

    This only evaluates and persists TradeFlow candidates. It does not invoke
    deep TA or any LLM-backed analysis path.
    """
    from tradingagents.tradeflow.candidate_engine import init_db
    from tradingagents.tradeflow.discovery import run_discovery

    tf_db = tf_db_path or _get_tradeflow_db_path()
    prod_db = prod_db_path or _get_prod_db_path()
    top_n = max(1, min(int(top_n or 20), 100))
    symbols = [normalize_tradeflow_symbol(s.strip()) for s in (symbols or []) if s and s.strip()]  # [UI-008]
    news_texts = [t.strip() for t in (news_texts or []) if t and t.strip()]

    init_db(tf_db)
    result = run_discovery(
        trade_date=trade_date,
        symbols=symbols or None,
        prod_db_path=prod_db,
        tf_db_path=tf_db,
        top_n=top_n,
        include_holdings=include_holdings,
        include_watchlist=include_watchlist,
        news_texts=news_texts or None,
        use_event_source=use_event_source,
        save_candidates=save_candidates,
    )

    return {
        "status": "ok",
        "trade_date": result.trade_date,
        "summary": result.summary,
        "universe_size": result.universe_size,
        "candidate_count": len(result.candidates),
        "filtered_count": len(result.filtered),
        "candidates": result.candidates,
        "filtered": result.filtered[:50],
        "metadata": result.metadata,
        "runtime_tier_meta": _tradeflow_meta("tradeflow_discovery"),  # [PERF-001]
    }


# [TF-OBS-001] tradeflow_observe_runner
def run_observe_check(trade_date: str, tf_db_path: str = "") -> dict:
    """Run intraday observe check for all active candidates on trade_date.

    This reads candidates, fetches realtime quotes, runs observe checks,
    updates candidate observe state, and writes signals.
    Does NOT invoke deep TA or LLM.
    """
    from tradingagents.tradeflow.candidate_engine import init_db
    from tradingagents.tradeflow.observe_runner import run_observe

    tf_db = tf_db_path or _get_tradeflow_db_path()
    init_db(tf_db)

    result = run_observe(
        trade_date=trade_date,
        db_path=tf_db,
    )

    return {
        "status": "skipped" if result.skipped_reason else "ok",
        "trade_date": trade_date,
        "checked": result.checked,
        "triggered": result.triggered,
        "invalidated": result.invalidated,
        "waiting": result.waiting,
        "skipped": result.skipped,
        "signals_written": result.signals_written,
        "errors": result.errors,
        "skipped_reason": result.skipped_reason,
        "run_time": result.run_time,
        "details": result.details,
        "runtime_tier_meta": _tradeflow_meta("tradeflow_observe_run"),  # [PERF-001]
    }


# [T-004] intraday_observe_scheduler
def get_observe_scheduler_status() -> dict:
    """Return current observe scheduler status and configuration."""
    from tradingagents.tradeflow.strategy_config import DEFAULT_STRATEGY_CONFIG
    cfg = DEFAULT_STRATEGY_CONFIG
    from zoneinfo import ZoneInfo
    now = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
    today = now.strftime("%Y-%m-%d")
    from tradingagents.tradeflow.intraday_observe_scheduler import _is_trading_day, _is_market_hours
    is_trading = _is_trading_day(today)
    is_hours = _is_market_hours(cfg)
    return {
        "status": "available",
        "current_time": now.isoformat(),
        "trade_date": today,
        "is_trading_day": is_trading,
        "is_market_hours": is_hours,
        "interval_minutes": cfg.observe_interval_minutes,
        "volume_anomaly_ratio": cfg.observe_volume_anomaly_ratio,
        "max_daily_triggers": cfg.observe_max_daily_triggers,
        "runtime_tier_meta": _tradeflow_meta("tradeflow_observe_scheduler"),
    }


# [TF-UX-001] tiered candidates
def get_candidates_tiered(trade_date: str, tf_db_path: str = "") -> dict:
    """Return candidates grouped by action_tier: actionable, watch, scan."""
    _fast_meta = _tradeflow_meta("tradeflow_candidates_tiered")
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}

    try:
        columns = _table_columns(conn, "tradeflow_candidates")
        extra_conditions = ["status = 'active'" if "status" in columns else None]
        extra_conditions = [c for c in extra_conditions if c is not None]

        order_cols = []
        if "trade_priority_score" in columns:
            order_cols.append("trade_priority_score DESC")
        if "composite_score" in columns:
            order_cols.append("composite_score DESC")
        if "score" in columns:
            order_cols.append("score DESC")
        order_clause = f" ORDER BY {', '.join(order_cols)}" if order_cols else " ORDER BY updated_at DESC, created_at DESC"

        rows = _query_by_date_or_effective(
            conn, "tradeflow_candidates", trade_date,
            extra_conditions=extra_conditions if extra_conditions else None,
            order_clause=order_clause,
        )

        all_items = [_row_to_candidate_item(r) for r in rows]
        for it in all_items:
            it["action"] = _compute_action(it)
            it["reason"] = it.get("why_deep_ta") or it.get("why_not_deep_ta") or "候选观察"

        # Compute action_tier for items that don't have it yet or need update
        _recompute_action_tiers(conn, all_items, trade_date)

        # [TF-QUALITY-001A] pool_gate_contract — tiered view intentionally
        # groups main candidates but keeps the other pools visible.
        from tradingagents.tradeflow.candidate_pool_gate import run_pool_gate
        pool_result = run_pool_gate(all_items)
        filtered_candidates = _merge_filtered_candidates(
            pool_result.filtered_candidates,
            _load_filtered_trace_candidates(trade_date, tf_db_path),
        )
        pool_counts = _pool_counts_with_filtered(pool_result.pool_counts, filtered_candidates)

        actionable = [it for it in pool_result.main_candidates if it.get("action_tier") == "actionable"][:3]
        watch = [it for it in pool_result.main_candidates if it.get("action_tier") == "watch"][:8]
        scan = [it for it in pool_result.main_candidates if it.get("action_tier") == "scan"]

        return {
            "status": "ok",
            "trade_date": trade_date,
            "actionable": actionable,
            "watch": watch,
            "scan": scan,
            "main_candidates": pool_result.main_candidates,
            "observation_candidates": pool_result.observation_candidates,
            "filtered_candidates": filtered_candidates,
            "pool_counts": pool_counts,
            "pool_gate_summary": _pool_gate_summary_with_counts(pool_counts),
            "actionable_count": len([it for it in pool_result.main_candidates if it.get("action_tier") == "actionable"]),
            "watch_count": len([it for it in pool_result.main_candidates if it.get("action_tier") == "watch"]),
            "scan_count": len(scan),
            "summary_agg": _compute_summary(pool_result.main_candidates),
            "runtime_tier_meta": _tradeflow_meta("tradeflow_candidates_tiered"),
        }
    finally:
        conn.close()


def _recompute_action_tiers(
    conn: sqlite3.Connection,
    items: list[dict],
    trade_date: str,
) -> None:
    """Recompute action_tier for items from DB fields, persisting if changed."""
    columns = _table_columns(conn, "tradeflow_candidates")
    has_tier_cols = {"action_tier", "trade_priority_score", "action_tier_reason"}.issubset(columns)

    for it in items:
        existing_tier = it.get("action_tier", "scan")
        existing_score = it.get("trade_priority_score", 0.0)
        existing_reason = it.get("action_tier_reason", "")

        if existing_tier and existing_tier != "scan" and existing_score > 0:
            continue

        from tradingagents.tradeflow.action_tier_scorer import run_action_tier_scorer
        result = run_action_tier_scorer(
            trigger_price=it.get("trigger_price"),
            current_price=None,
            invalid_price=it.get("invalid_price"),
            observe_state=it.get("observe_state", "WAITING"),
            data_completeness=it.get("data_completeness", 0.0),
            composite_score=it.get("composite_score", 0.0),
            positive_category_count=it.get("positive_category_count", 0),
            fund_flow_anomaly_score=it.get("fund_flow_anomaly_score", 0.0),
            fund_flow_unit_verified=it.get("fund_flow_unit_verified", False),
            risk_penalty=it.get("risk_penalty", 0.0) if "risk_penalty" in it else 0.0,
            game_balance=it.get("game_balance", ""),
            ambush_score=it.get("ambush_score", 0.0),
        )

        it["action_tier"] = result.action_tier
        it["trade_priority_score"] = result.trade_priority_score
        it["action_tier_reason"] = result.action_tier_reason

        if has_tier_cols:
            try:
                conn.execute(
                    "UPDATE tradeflow_candidates SET action_tier=?, trade_priority_score=?, action_tier_reason=? WHERE trade_date=? AND symbol=?",
                    (result.action_tier, result.trade_priority_score, result.action_tier_reason, trade_date, it["symbol"]),
                )
            except Exception:
                pass

    if has_tier_cols:
        try:
            conn.commit()
        except Exception:
            pass


# [DATA-007] evidence_coverage_audit
def get_evidence_audit(trade_date: str, tf_db_path: str = "") -> dict:
    """Run evidence coverage audit on all candidates for trade_date.

    Reads candidate metadata from tradeflow.db, runs the evidence coverage
    auditor on each candidate, and returns a summary.
    Does NOT call LLM or live data sources.
    """
    from tradingagents.dataflows.evidence_coverage_audit import (
        audit_candidate_evidence,
        build_evidence_coverage_section,
    )

    _fast_meta = _tradeflow_meta("tradeflow_evidence_audit")
    conn = _connect(tf_db_path)
    if conn is None:
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "audits": [],
            "credibilities": [],
            "summary_markdown": "",
            "runtime_tier_meta": _fast_meta,
        }

    try:
        columns = _table_columns(conn, "tradeflow_candidates")
        extra_conditions = ["status = 'active'" if "status" in columns else None]
        extra_conditions = [c for c in extra_conditions if c is not None]
        rows = _query_by_date_or_effective(
            conn, "tradeflow_candidates", trade_date,
            extra_conditions=extra_conditions if extra_conditions else None,
        )

        audits = []
        credibilities = []
        for row in rows:
            data_comp = float(row.get("data_completeness", 0) or 0)
            tf_comp = float(row.get("tradeflow_data_completeness", 0) or 0)
            cand_type = str(row.get("candidate_type", "") or "")
            missing_fields = json.loads(row.get("missing_data_fields_json", "[]") or "[]")
            gate_applied = bool(row.get("evidence_gate_applied", 0))
            ff_verified = bool(row.get("fund_flow_unit_verified", 0))
            symbol = str(row.get("symbol", ""))

            has_policy = cand_type in ("POLICY_AMBUSH", "POLICY_CONFIRM", "PSEUDO_POLICY")
            has_tech = cand_type == "TECH_TRADE"
            has_realtime = tf_comp >= 0.5

            cred = audit_candidate_evidence(
                candidate_data_completeness=data_comp,
                candidate_tradeflow_completeness=tf_comp,
                candidate_type=cand_type,
                missing_data_fields=missing_fields,
                evidence_gate_applied=gate_applied,
                fund_flow_unit_verified=ff_verified,
                has_policy_evidence=has_policy,
                has_tech_signal=has_tech,
                has_realtime_quote=has_realtime,
            )
            cred.symbol = symbol

            if cred.evidence_audit:
                audits.append(cred.evidence_audit)
            credibilities.append(cred)

        summary_md = build_evidence_coverage_section(
            audits=audits if audits else None,
            credibilities=credibilities if credibilities else None,
        )

        return {
            "status": "ok",
            "trade_date": trade_date,
            "total_candidates": len(rows),
            "audits_count": len(audits),
            "credibilities_count": len(credibilities),
            "credibilities": [c.to_dict() for c in credibilities],
            "summary_markdown": summary_md,
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


# [TF-UX-003] post_market_review  # [TF-REVIEW-002] review_date_mapping
def generate_review(trade_date: str, tf_db_path: str = "") -> dict:
    """Generate post-market review for trade_date using post_market_review module."""
    from tradingagents.tradeflow.candidate_engine import init_db
    from tradingagents.tradeflow.post_market_review import (
        ReviewDataStatus,
        build_candidate_performance_from_dict,
        run_post_market_review,
        save_review_report,
    )
    from tradingagents.tradeflow.date_semantics import resolve_review_date, find_latest_plan_date

    tf_db = tf_db_path or _get_tradeflow_db_path()
    init_db(tf_db)

    # [TF-REVIEW-002] Try the exact date first, then fall back to latest plan
    resolved_date = trade_date
    candidates_data = get_candidates(trade_date, tf_db_path=tf_db)
    if candidates_data.get("status") == "no_data":
        available = _get_available_dates(tf_db)
        if available:
            best = find_latest_plan_date(available, trade_date)
            if best and best != trade_date:
                resolved_date = best
                candidates_data = get_candidates(best, tf_db_path=tf_db)

    if candidates_data.get("status") == "no_data":
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "message": "无候选数据",
            "data_status": ReviewDataStatus.NO_CANDIDATES.value,
            "data_status_message": ReviewDataStatus.NO_CANDIDATES.message_cn,
        }

    candidates = candidates_data.get("candidates", [])
    if not candidates:
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "message": "候选列表为空",
            "data_status": ReviewDataStatus.NO_CANDIDATES.value,
            "data_status_message": ReviewDataStatus.NO_CANDIDATES.message_cn,
        }

    # [TF-REVIEW-002] Resolve plan_date vs review_date
    plan_date = resolved_date
    effective_trade_date = resolved_date
    if candidates:
        first = candidates[0]
        if first.get("effective_trade_date"):
            effective_trade_date = first["effective_trade_date"]
        if first.get("plan_date"):
            plan_date = first["plan_date"]
    review_date = resolve_review_date(plan_date, effective_trade_date)

    performances = []
    for c in candidates:
        perf = build_candidate_performance_from_dict(c)
        performances.append(perf)

    summary = run_post_market_review(
        performances,
        candidate_date=resolved_date,
        review_date=review_date,
        plan_date=plan_date,
        effective_trade_date=effective_trade_date,
    )

    try:
        save_review_report(summary)
    except Exception:
        pass

    return {
        "status": "ok",
        "trade_date": trade_date,
        "data_status": summary.data_status,
        "data_status_message": summary.data_status_message,
        "review": {
            "review_date": summary.review_date,
            "candidate_date": summary.candidate_date,
            "plan_date": summary.plan_date,
            "effective_trade_date": summary.effective_trade_date,
            "data_status": summary.data_status,
            "data_status_message": summary.data_status_message,
            "total_candidates": summary.total_candidates,
            "scored_candidates": summary.scored_candidates,
            "no_data_candidates": summary.no_data_candidates,
            "overall_hit_count": summary.overall_hit_count,
            "overall_miss_count": summary.overall_miss_count,
            "overall_invalidated_count": summary.overall_invalidated_count,
            "overall_hit_rate": summary.overall_hit_rate,
            "overall_false_positive_rate": summary.overall_false_positive_rate,
            "avg_next_day_return": summary.avg_next_day_return,
            "avg_day3_return": summary.avg_day3_return,
            "avg_day5_return": summary.avg_day5_return,
            "strategy_stats": {
                tag: {
                    "strategy_tag": st.strategy_tag,
                    "total_candidates": st.total_candidates,
                    "hit_count": st.hit_count,
                    "miss_count": st.miss_count,
                    "no_data_count": st.no_data_count,
                    "invalidated_count": st.invalidated_count,
                    "hit_rate": st.hit_rate,
                    "false_positive_rate": st.false_positive_rate,
                    "avg_next_day_return": st.avg_next_day_return,
                    "avg_day3_return": st.avg_day3_return,
                    "avg_day5_return": st.avg_day5_return,
                }
                for tag, st in summary.strategy_stats.items()
            },
            "tier_stats": summary.tier_stats,
            "common_removal_reasons": summary.common_removal_reasons,
            "suggestions": summary.suggestions,
        },
    }


# [UI-009] candidate_ta_plan_draft
def generate_research_plan(symbol: str, trade_date: str, tf_db_path: str = "") -> dict:
    """Generate a TA research plan draft for a single candidate."""
    from tradingagents.tradeflow.research_plan_draft import generate_research_plan as _gen_plan

    conn = _connect(tf_db_path)
    if conn is None:
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "symbol": symbol,
            "can_generate": False,
            "block_reason": "TradeFlow 数据库不可用",
        }

    try:
        row = conn.execute(
            "SELECT * FROM tradeflow_candidates WHERE trade_date = ? AND symbol = ?",
            (trade_date, symbol),
        ).fetchone()

        if row is None:
            columns = _table_columns(conn, "tradeflow_candidates")
            if "effective_trade_date" in columns:
                row = conn.execute(
                    "SELECT * FROM tradeflow_candidates WHERE effective_trade_date = ? AND symbol = ?",
                    (trade_date, symbol),
                ).fetchone()

        if row is None:
            return {
                "status": "no_data",
                "trade_date": trade_date,
                "symbol": symbol,
                "can_generate": False,
                "block_reason": "未找到该候选记录",
            }

        item = _row_to_candidate_item(row)
        evidence = _parse_json(_rget(row, "evidence_json"), {})
        evidence_coverage = 0.0
        if isinstance(evidence, dict):
            total = len(evidence)
            if total > 0:
                has = sum(1 for v in evidence.values() if v)
                evidence_coverage = has / total

        draft = _gen_plan(
            symbol=symbol,
            trade_date=trade_date,
            candidate_type=item.get("candidate_type", ""),
            research_queue=item.get("research_queue", ""),
            research_intent=item.get("research_intent", ""),
            route_reason=item.get("research_route_reason", ""),
            evidence_coverage=evidence_coverage,
            missing_evidence=item.get("missing_evidence"),
        )

        result = draft.to_dict()
        result["status"] = "ok"

        # [TF-UI-011] candidate_research_entry — enrich with profile metadata
        try:
            from api.ta_profile import recommend_profile, profile_to_meta
            profile = recommend_profile(
                candidate_type=item.get("candidate_type", ""),
                research_queue=item.get("research_queue", ""),
            )
            meta = profile_to_meta(profile)
            result["profile_label"] = meta.get("profile_label", "")
            result["expected_latency"] = meta.get("expected_latency", "")
            result["llm_allowed"] = meta.get("llm_allowed", True)
            result["requires_confirmation"] = meta.get("requires_confirmation", False)
            result["cost_risk"] = meta.get("cost_risk", "")
        except Exception:
            pass

        return result
    finally:
        conn.close()


# [TF-UI-011] candidate_research_entry
def get_company_overview(symbol: str, tf_db_path: str = "") -> dict:
    """Fetch a lightweight company overview for a candidate symbol.

    Uses existing data source providers (route_to_vendor) to retrieve
    company fundamentals text.  Does NOT call any LLM.  Falls back
    gracefully when data sources are unavailable.
    """
    _fast_meta = _tradeflow_meta("tradeflow_company_overview")

    result: dict = {
        "status": "ok",
        "symbol": symbol,
        "name": "",
        "industry": "",
        "company_profile": "",
        "profile_available": False,
        "data_source": "",
        "error": "",
        "runtime_tier_meta": _fast_meta,
    }

    # Try to resolve name from existing candidate data first
    try:
        conn = _connect(tf_db_path)
        if conn is not None:
            try:
                cols = _table_columns(conn, "tradeflow_candidates")
                if "created_at" in cols:
                    row = conn.execute(
                        "SELECT name FROM tradeflow_candidates WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
                        (symbol,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT name FROM tradeflow_candidates WHERE symbol = ? LIMIT 1",
                        (symbol,),
                    ).fetchone()
                if row and row["name"]:
                    result["name"] = row["name"]
            finally:
                conn.close()
    except Exception:
        pass

    # Try to get fundamentals from data source providers
    try:
        from tradingagents.dataflows.interface import route_to_vendor, get_last_hit_vendor
        profile_text = route_to_vendor("get_fundamentals", symbol)
        if profile_text and isinstance(profile_text, str) and len(profile_text) > 20:
            result["company_profile"] = profile_text
            result["profile_available"] = True
            try:
                result["data_source"] = get_last_hit_vendor("get_fundamentals") or ""
            except Exception:
                pass
            # Try to extract industry from the text
            for line in profile_text.split("\n"):
                low = line.lower()
                if "行业" in line or "industry" in low:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        val = parts[-1].strip().strip("*").strip()
                        if val:
                            result["industry"] = val
                            break
    except Exception as exc:
        result["error"] = f"数据源暂不可用: {type(exc).__name__}"
        result["status"] = "unavailable"

    return result


# [UI-010] mandate_candidate_compare
_VALID_SORT_KEYS = {
    "mandate_score": "mandate_score_component",
    "ambush_score": "ambush_score",
    "evidence_coverage": "tradeflow_data_completeness",
    "counter_evidence_count": None,
    "evidence_gap_count": None,
    "topic_lifecycle_state": None,
    "company_role": None,
}


def _counter_evidence_severity(items: list[dict]) -> float:
    total = 0.0
    for item in items:
        total += float(item.get("severity", 0.0))
    return total


def _topic_lifecycle_order(state: str) -> int:
    _ORDER = {"EMERGING": 0, "ACCELERATING": 1, "CONFIRMING": 2, "UNKNOWN": 3, "CROWDED": 4, "FADING": 5}
    return _ORDER.get(state, 3)


def get_candidate_comparison(
    trade_date: str,
    sort_by: str = "mandate_score",
    sort_order: str = "desc",
    pool: Optional[str] = None,
    tf_db_path: str = "",
) -> dict:
    """Return candidates sorted for comparison view with evidence gap ranking.

    Sort options:
      - mandate_score: policy strength (mandate_score_component)
      - ambush_score: left-side ambush score
      - evidence_coverage: evidence completeness (tradeflow_data_completeness)
      - counter_evidence_count: counter-evidence risk severity (lower is better)
      - evidence_gap_count: fewer gaps first
      - topic_lifecycle_state: lifecycle stage order
      - company_role: alphabetical grouping
    """
    _fast_meta = _tradeflow_meta("tradeflow_candidates")
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}

    try:
        pool_types: list[str] = []
        if pool:
            from tradingagents.tradeflow.candidate_pool import pool_to_candidate_types
            pool_types = pool_to_candidate_types(pool)

        columns = _table_columns(conn, "tradeflow_candidates")
        extra_conditions = ["status = 'active'" if "status" in columns else None]
        extra_conditions = [c for c in extra_conditions if c is not None]

        extra_params: list = []
        if len(pool_types) == 1 and "candidate_type" in columns:
            extra_conditions.append("candidate_type = ?")
            extra_params.append(pool_types[0])
        elif len(pool_types) > 1 and "candidate_type" in columns:
            placeholders = ",".join(["?"] * len(pool_types))
            extra_conditions.append(f"candidate_type IN ({placeholders})")
            extra_params.extend(pool_types)

        rows = _query_by_date_or_effective(
            conn, "tradeflow_candidates", trade_date,
            extra_conditions=extra_conditions if extra_conditions else None,
            extra_params=extra_params if extra_params else None,
        )

        items = [_row_to_candidate_item(r) for r in rows]

        for it in items:
            it["action"] = _compute_action(it)
            ce = it.get("counter_evidence") or []
            it["_counter_evidence_severity"] = _counter_evidence_severity(ce)
            gaps = it.get("blocking_evidence_gaps") or []
            it["_evidence_gap_count"] = len(gaps)
            it["_topic_lifecycle_order"] = _topic_lifecycle_order(it.get("topic_lifecycle_state", ""))

        effective_key = sort_by
        if sort_by not in _VALID_SORT_KEYS:
            effective_key = "mandate_score"

        reverse = sort_order == "desc"

        def _sort_key(item: dict):
            if effective_key == "mandate_score":
                return (item.get("mandate_score", 0.0) or 0.0)
            elif effective_key == "ambush_score":
                return (item.get("ambush_score", 0.0) or 0.0)
            elif effective_key == "evidence_coverage":
                return (item.get("tradeflow_data_completeness", 0.0) or 0.0)
            elif effective_key == "counter_evidence_count":
                return item.get("_counter_evidence_severity", 0.0)
            elif effective_key == "evidence_gap_count":
                return item.get("_evidence_gap_count", 0)
            elif effective_key == "topic_lifecycle_state":
                return item.get("_topic_lifecycle_order", 3)
            elif effective_key == "company_role":
                return item.get("company_role", "") or ""
            return 0

        items.sort(key=_sort_key, reverse=reverse)

        for it in items:
            it.pop("_counter_evidence_severity", None)
            it.pop("_evidence_gap_count", None)
            it.pop("_topic_lifecycle_order", None)

        return {
            "status": "ok",
            "trade_date": trade_date,
            "candidates": items,
            "sort_by": effective_key,
            "sort_order": sort_order,
            "total": len(items),
            "runtime_tier_meta": _tradeflow_meta("tradeflow_candidates"),
        }
    finally:
        conn.close()


# ── [TF-PAPER-001] paper_trading_ledger ──────────────────────────────────

_DEFAULT_PRINCIPAL = 5000.0

# [TF-RISK-001] paper_risk_budget
_DEFAULT_RISK_BUDGET: Dict[str, Any] = {
    "principal": _DEFAULT_PRINCIPAL,
    "per_ticket_max": 1500.0,
    "per_ticket_min": 500.0,
    "daily_new_max": 3,
    "max_concurrent_tracking": 5,
    "require_trigger_price": True,
    "require_invalid_price": True,
    "min_data_quality_score": 40.0,
}


# [TF-RISK-001] paper_risk_budget
def _get_risk_budget(config: Dict[str, Any]) -> Dict[str, Any]:
    """Merge the ledger config's risk_budget over the defaults.

    Legacy top-level ``max_per_candidate`` is honored as ``per_ticket_max``
    when no explicit override is present, preserving backwards compatibility.
    """
    rb: Dict[str, Any] = dict(_DEFAULT_RISK_BUDGET)
    user_rb = config.get("risk_budget") if isinstance(config, dict) else None
    if isinstance(user_rb, dict):
        for k, v in user_rb.items():
            if v is not None:
                rb[k] = v
    if isinstance(config, dict) and "max_per_candidate" in config and not (
        isinstance(user_rb, dict) and "per_ticket_max" in user_rb
    ):
        rb["per_ticket_max"] = config["max_per_candidate"]
    return rb


# [TF-RISK-001] paper_risk_budget
def _empty_risk_exposure() -> Dict[str, Any]:
    rb = dict(_DEFAULT_RISK_BUDGET)
    return _compute_risk_exposure(
        rb=rb, principal=_DEFAULT_PRINCIPAL, invested=0.0,
        tracking_count=0, pending_count=0, daily_new_today=0,
    )


# [TF-RISK-001] paper_risk_budget
def _compute_risk_exposure(
    rb: Dict[str, Any],
    principal: float,
    invested: float,
    tracking_count: int,
    pending_count: int,
    daily_new_today: int,
) -> Dict[str, Any]:
    remaining = principal - invested
    utilization = invested / principal * 100 if principal else 0.0
    return {
        "principal": principal,
        "invested": round(invested, 2),
        "remaining": round(remaining, 2),
        "per_ticket_max": rb.get("per_ticket_max", _DEFAULT_RISK_BUDGET["per_ticket_max"]),
        "per_ticket_min": rb.get("per_ticket_min", _DEFAULT_RISK_BUDGET["per_ticket_min"]),
        "daily_new_today": daily_new_today,
        "daily_new_max": rb.get("daily_new_max", _DEFAULT_RISK_BUDGET["daily_new_max"]),
        "tracking_count": tracking_count + pending_count,
        "max_concurrent_tracking": rb.get(
            "max_concurrent_tracking", _DEFAULT_RISK_BUDGET["max_concurrent_tracking"]
        ),
        "budget_utilization_pct": round(utilization, 1),
    }


def _ensure_paper_ledger_row(conn: sqlite3.Connection) -> sqlite3.Row:
    """Return the single paper ledger row, creating it if necessary."""
    row = conn.execute("SELECT * FROM tradeflow_paper_ledger LIMIT 1").fetchone()
    if row is not None:
        return row
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    seed_config = {"risk_budget": dict(_DEFAULT_RISK_BUDGET)}  # [TF-RISK-001] paper_risk_budget
    conn.execute(
        "INSERT INTO tradeflow_paper_ledger (principal, cash_balance, config_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (_DEFAULT_PRINCIPAL, _DEFAULT_PRINCIPAL, json.dumps(seed_config), now, now),
    )
    conn.commit()
    return conn.execute("SELECT * FROM tradeflow_paper_ledger LIMIT 1").fetchone()


def _row_to_paper_trade(row: sqlite3.Row) -> dict:
    return {
        "id": _rget(row, "id"),
        "symbol": _rget(row, "symbol", ""),
        "name": _rget(row, "name", ""),
        "trade_date": _rget(row, "trade_date", ""),
        "plan_date": _rget(row, "plan_date", ""),
        "candidate_type": _rget(row, "candidate_type", ""),
        "trigger_price": _rget(row, "trigger_price"),
        "invalid_price": _rget(row, "invalid_price"),
        "planned_amount": _rget(row, "planned_amount", 0.0),
        "status": _rget(row, "status", "tracking"),
        "action_type": _rget(row, "action_type", ""),
        "action_price": _rget(row, "action_price"),
        "action_date": _rget(row, "action_date", ""),
        "confirmed": bool(_rget(row, "confirmed", 0)),
        "note": _rget(row, "note", ""),
        "pnl": _rget(row, "pnl", 0.0),
        "pnl_pct": _rget(row, "pnl_pct", 0.0),
        "observe_state": _rget(row, "observe_state", "WAITING"),
        "close_price": _rget(row, "close_price"),
        "close_date": _rget(row, "close_date", ""),
        "close_reason": _rget(row, "close_reason", ""),
        "created_at": _rget(row, "created_at", ""),
        "updated_at": _rget(row, "updated_at", ""),
    }


def get_paper_ledger(tf_db_path: str = "") -> dict:
    """Return the paper trading ledger with all trades and summary."""  # [TF-PAPER-001]
    _fast_meta = _tradeflow_meta("tradeflow_paper_ledger")
    conn = _connect(tf_db_path)
    if conn is None:
        return {
            "status": "no_data",
            "principal": _DEFAULT_PRINCIPAL,
            "cash_balance": _DEFAULT_PRINCIPAL,
            "config": {"risk_budget": dict(_DEFAULT_RISK_BUDGET)},  # [TF-RISK-001] paper_risk_budget
            "trades": [],
            "summary": {
                "total_trades": 0,
                "tracking_count": 0,
                "pending_count": 0,
                "open_count": 0,
                "closed_count": 0,
                "observation_count": 0,
                "invested": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "total_pnl_pct": 0.0,
                "risk_exposure": _empty_risk_exposure(),  # [TF-RISK-001] paper_risk_budget
            },
            "runtime_tier_meta": _fast_meta,
        }

    try:
        ledger = _ensure_paper_ledger_row(conn)
        principal = _rget(ledger, "principal", _DEFAULT_PRINCIPAL)
        cash_balance = _rget(ledger, "cash_balance", _DEFAULT_PRINCIPAL)
        config = _parse_json(_rget(ledger, "config_json", "{}"), default={})
        rb = _get_risk_budget(config)  # [TF-RISK-001] paper_risk_budget

        rows = conn.execute(
            "SELECT * FROM tradeflow_paper_trades ORDER BY created_at DESC"
        ).fetchall()
        trades = [_row_to_paper_trade(r) for r in rows]

        tracking_count = sum(1 for t in trades if t["status"] == "tracking")
        pending_count = sum(1 for t in trades if t["status"] == "pending")
        open_count = sum(1 for t in trades if t["status"] == "open")
        closed_count = sum(1 for t in trades if t["status"] == "closed")
        observation_count = sum(1 for t in trades if t["status"] == "observation")
        invested = sum(t["planned_amount"] for t in trades if t["status"] == "open")
        # [TF-RISK-001] risk budget reservation includes tracking/pending/open planned amounts
        reserved_invested = sum(
            t["planned_amount"] for t in trades
            if t["status"] in ("tracking", "pending", "open")
        )
        realized_pnl = sum(t["pnl"] for t in trades if t["status"] == "closed")

        today_prefix = datetime.now().strftime("%Y-%m-%d")
        daily_new_today = conn.execute(
            "SELECT COUNT(*) FROM tradeflow_paper_trades WHERE created_at LIKE ?",
            (today_prefix + "%",),
        ).fetchone()[0]

        summary = {
            "total_trades": len(trades),
            "tracking_count": tracking_count,
            "pending_count": pending_count,
            "open_count": open_count,
            "closed_count": closed_count,
            "observation_count": observation_count,
            "invested": round(invested, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": 0.0,
            "total_pnl": round(realized_pnl, 2),
            "total_pnl_pct": round(realized_pnl / principal * 100, 2) if principal else 0.0,
            "risk_exposure": _compute_risk_exposure(  # [TF-RISK-001] paper_risk_budget
                rb=rb,
                principal=principal,
                invested=reserved_invested,  # tracking/pending/open planned reservation
                tracking_count=tracking_count,
                pending_count=pending_count,
                daily_new_today=daily_new_today,
            ),
        }

        return {
            "status": "ok",
            "principal": principal,
            "cash_balance": cash_balance,
            "config": config,
            "trades": trades,
            "summary": summary,
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


# [TF-RISK-001] paper_risk_budget
def update_paper_ledger_config(config_update: Dict[str, Any], tf_db_path: str = "") -> dict:
    """Deep-merge ``config_update`` into the paper ledger ``config_json``.

    Typically used to override the ``risk_budget`` block, e.g.::

        update_paper_ledger_config({"risk_budget": {"daily_new_max": 5}})
    """
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available"}

    try:
        ledger = _ensure_paper_ledger_row(conn)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config = _parse_json(_rget(ledger, "config_json", "{}"), default={})

        for key, value in (config_update or {}).items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                merged = dict(config[key])
                merged.update(value)
                config[key] = merged
            else:
                config[key] = value

        conn.execute(
            "UPDATE tradeflow_paper_ledger SET config_json = ?, updated_at = ?",
            (json.dumps(config), now),
        )
        conn.commit()
        return {
            "status": "ok",
            "message": "模拟账本配置已更新",
            "config": config,
            "risk_budget": _get_risk_budget(config),
        }
    finally:
        conn.close()


def add_paper_candidate(
    symbol: str,
    name: str,
    trade_date: str,
    trigger_price: Optional[float] = None,
    invalid_price: Optional[float] = None,
    planned_amount: float = 0.0,
    candidate_type: str = "",
    plan_date: str = "",
    note: str = "",
    data_quality_score: Optional[float] = None,
    tf_db_path: str = "",
) -> dict:
    """Add a candidate to the paper trading ledger.

    Enforces the 5000 元试跑 risk budget & position discipline:

    * **Hard reject** (candidate not added at all): missing ``trigger_price``,
      missing ``invalid_price``, or ``data_quality_score`` below threshold.
      Returns ``status="rejected"`` with a human-readable ``reason`` and the
      offending ``rule`` name.
    * **Observation downgrade** (added but not actionable): when the concurrent
      tracking/pending slots or the daily-new quota are exhausted, or the
      planned amount exceeds the remaining budget, the candidate is admitted
      as ``observation`` only — it can never transition to ``pending``/``open``.
    """  # [TF-PAPER-001] / [TF-RISK-001] paper_risk_budget
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available"}

    try:
        symbol = normalize_tradeflow_symbol(symbol)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        existing = conn.execute(
            "SELECT id FROM tradeflow_paper_trades "
            "WHERE symbol = ? AND status IN ('tracking', 'pending', 'open', 'observation')",
            (symbol,),
        ).fetchone()
        if existing is not None:
            return {"status": "duplicate", "message": f"{symbol} 已在模拟跟踪中"}

        ledger = _ensure_paper_ledger_row(conn)
        config = _parse_json(_rget(ledger, "config_json", "{}"), default={})
        rb = _get_risk_budget(config)

        # ── Hard rules: reject outright ────────────────────────────────
        if rb.get("require_trigger_price", True) and not trigger_price:
            reason = "缺少触发价(trigger_price)，不允许加入待执行动作"
            return {
                "status": "rejected",
                "rejected": True,
                "rule": "require_trigger_price",
                "reason": reason,
                "message": reason,
            }
        if rb.get("require_invalid_price", True) and not invalid_price:
            reason = "缺少失效价(invalid_price)，不允许加入待执行动作"
            return {
                "status": "rejected",
                "rejected": True,
                "rule": "require_invalid_price",
                "reason": reason,
                "message": reason,
            }
        min_dq = rb.get("min_data_quality_score", _DEFAULT_RISK_BUDGET["min_data_quality_score"])
        if data_quality_score is not None and data_quality_score < min_dq:
            reason = (
                f"数据质量评分 {data_quality_score} 低于阈值 {min_dq}，"
                "不允许加入待执行动作"
            )
            return {
                "status": "rejected",
                "rejected": True,
                "rule": "min_data_quality_score",
                "reason": reason,
                "message": reason,
            }

        # ── Amount clamping to per-ticket budget ───────────────────────
        per_ticket_max = rb.get("per_ticket_max", _DEFAULT_RISK_BUDGET["per_ticket_max"])
        per_ticket_min = rb.get("per_ticket_min", _DEFAULT_RISK_BUDGET["per_ticket_min"])
        if planned_amount and planned_amount > 0:
            amount = min(planned_amount, per_ticket_max)
        else:
            amount = min(1000.0, per_ticket_max)
        if amount < per_ticket_min:
            amount = min(per_ticket_min, per_ticket_max)

        # ── Soft rules: decide actionable vs observation ───────────────
        active = conn.execute(
            "SELECT COUNT(*) FROM tradeflow_paper_trades "
            "WHERE status IN ('tracking', 'pending')"
        ).fetchone()[0]
        today_prefix = now[:10]
        today_new = conn.execute(
            "SELECT COUNT(*) FROM tradeflow_paper_trades WHERE created_at LIKE ?",
            (today_prefix + "%",),
        ).fetchone()[0]

        status = "tracking"
        downgrade_reason = ""
        if active >= rb.get("max_concurrent_tracking", _DEFAULT_RISK_BUDGET["max_concurrent_tracking"]):
            status = "observation"
            downgrade_reason = "已达最大并发跟踪数，仅允许观察"
        elif today_new >= rb.get("daily_new_max", _DEFAULT_RISK_BUDGET["daily_new_max"]):
            status = "observation"
            downgrade_reason = "已达当日新增上限，仅允许观察"
        else:
            principal = _rget(ledger, "principal", _DEFAULT_PRINCIPAL)
            # [TF-RISK-001] tracking/pending planned_amount must also reserve budget
            invested = conn.execute(
                "SELECT COALESCE(SUM(planned_amount), 0) FROM tradeflow_paper_trades "
                "WHERE status IN ('tracking', 'pending', 'open')"
            ).fetchone()[0]
            remaining = principal - invested
            if amount > remaining:
                status = "observation"
                downgrade_reason = f"风险预算不足（剩余 {remaining:.0f}），仅允许观察"

        conn.execute(
            "INSERT INTO tradeflow_paper_trades "
            "(symbol, name, trade_date, plan_date, candidate_type, trigger_price, invalid_price, "
            "planned_amount, status, note, observe_state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'WAITING', ?, ?)",
            (
                symbol, name, trade_date, plan_date or trade_date, candidate_type,
                trigger_price, invalid_price, amount, status, note, now, now,
            ),
        )
        conn.commit()

        trade_id = conn.execute(
            "SELECT id FROM tradeflow_paper_trades WHERE symbol = ? ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()["id"]
        result = {
            "status": "ok",
            "message": (
                f"{symbol} 已加入模拟跟踪"
                if status == "tracking"
                else f"{symbol} 已加入观察（{downgrade_reason}）"
            ),
            "trade_id": trade_id,
            "planned_amount": amount,
        }
        if status == "observation":
            result["downgraded_to"] = "observation"
            result["reason"] = downgrade_reason
        return result
    finally:
        conn.close()


def remove_paper_candidate(trade_id: int, tf_db_path: str = "") -> dict:
    """Remove a candidate from the paper trading ledger."""  # [TF-PAPER-001]
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available"}

    try:
        row = conn.execute(
            "SELECT id, symbol, status FROM tradeflow_paper_trades WHERE id = ?", (trade_id,)
        ).fetchone()
        if row is None:
            return {"status": "not_found", "message": f"trade_id={trade_id} 不存在"}

        if row["status"] == "open":
            return {"status": "blocked", "message": f"{row['symbol']} 有未平仓模拟仓位，请先平仓"}

        conn.execute("DELETE FROM tradeflow_paper_trades WHERE id = ?", (trade_id,))
        conn.commit()
        return {"status": "ok", "message": f"{row['symbol']} 已移除"}
    finally:
        conn.close()


def confirm_paper_action(
    trade_id: int,
    action_type: str,
    price: float,
    note: str = "",
    tf_db_path: str = "",
) -> dict:
    """Confirm a simulated buy/sell action for a paper trade.

    action_type: 'buy' → open position, 'sell' → close position.
    """  # [TF-PAPER-001]
    if action_type not in ("buy", "sell"):
        return {"status": "error", "message": "action_type must be 'buy' or 'sell'"}

    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available"}

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT * FROM tradeflow_paper_trades WHERE id = ?", (trade_id,)
        ).fetchone()
        if row is None:
            return {"status": "not_found", "message": f"trade_id={trade_id} 不存在"}

        ledger = _ensure_paper_ledger_row(conn)
        cash_balance = _rget(ledger, "cash_balance", _DEFAULT_PRINCIPAL)

        if action_type == "buy":
            if row["status"] not in ("tracking", "pending"):
                return {"status": "blocked", "message": f"当前状态 {row['status']} 不可买入"}
            amount = _rget(row, "planned_amount", 0.0)
            if amount > cash_balance:
                return {"status": "insufficient_cash", "message": f"现金余额 {cash_balance:.2f} 不足买入 {amount:.2f}"}
            conn.execute(
                "UPDATE tradeflow_paper_trades SET status='open', action_type='buy', "
                "action_price=?, action_date=?, confirmed=1, note=?, updated_at=? WHERE id=?",
                (price, now, note, now, trade_id),
            )
            conn.execute(
                "UPDATE tradeflow_paper_ledger SET cash_balance = cash_balance - ?, updated_at = ?",
                (amount, now),
            )
            conn.commit()
            return {
                "status": "ok",
                "message": f"{row['symbol']} 模拟买入 @ {price}",
                "cash_balance": round(cash_balance - amount, 2),
            }
        else:
            if row["status"] != "open":
                return {"status": "blocked", "message": f"当前状态 {row['status']} 不可卖出"}
            buy_price = _rget(row, "action_price", 0.0)
            amount = _rget(row, "planned_amount", 0.0)
            pnl = (price - buy_price) * (amount / buy_price) if buy_price else 0.0
            pnl_pct = (price - buy_price) / buy_price * 100 if buy_price else 0.0
            conn.execute(
                "UPDATE tradeflow_paper_trades SET status='closed', action_type='sell', "
                "action_price=?, action_date=?, confirmed=1, close_price=?, close_date=?, "
                "pnl=?, pnl_pct=?, close_reason=?, note=?, updated_at=? WHERE id=?",
                (price, now, price, now[:10], round(pnl, 2), round(pnl_pct, 2), note, note, now, trade_id),
            )
            conn.execute(
                "UPDATE tradeflow_paper_ledger SET cash_balance = cash_balance + ? + ?, updated_at = ?",
                (amount, round(pnl, 2), now),
            )
            conn.commit()
            return {
                "status": "ok",
                "message": f"{row['symbol']} 模拟卖出 @ {price}, P&L {pnl:.2f}",
                "cash_balance": round(cash_balance + amount + pnl, 2),
                "pnl": round(pnl, 2),
            }
    finally:
        conn.close()


def update_paper_observe_state(
    symbol: str,
    observe_state: str,
    tf_db_path: str = "",
) -> dict:
    """Sync observe_state from intraday observe to paper trades.

    When a candidate in the paper ledger gets TRIGGERED, its paper trade
    status transitions from 'tracking' to 'pending' (awaiting manual confirm).
    """  # [TF-PAPER-001]
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available"}

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT id, status FROM tradeflow_paper_trades WHERE symbol = ? AND status = 'tracking'",
            (symbol,),
        ).fetchall()
        updated = 0
        for row in rows:
            new_status = "pending" if observe_state == "TRIGGERED" else "tracking"
            conn.execute(
                "UPDATE tradeflow_paper_trades SET observe_state=?, status=?, updated_at=? WHERE id=?",
                (observe_state, new_status, now, row["id"]),
            )
            updated += 1
        if observe_state == "INVALIDATED":
            conn.execute(
                "UPDATE tradeflow_paper_trades SET observe_state='INVALIDATED', status='invalidated', updated_at=? "
                "WHERE symbol=? AND status IN ('tracking', 'pending')",
                (now, symbol),
            )
        conn.commit()
        return {"status": "ok", "updated": updated, "symbol": symbol}
    finally:
        conn.close()


def get_paper_review(trade_date: str, tf_db_path: str = "") -> dict:
    """Post-market review of paper trading ledger for a given date.

    Aggregates: realized P&L, false triggers, un-triggered, invalidated.
    """  # [TF-PAPER-001]
    _fast_meta = _tradeflow_meta("tradeflow_paper_ledger")
    conn = _connect(tf_db_path)
    if conn is None:
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "review": {
                "total": 0,
                "tracking": 0,
                "pending": 0,
                "open": 0,
                "closed": 0,
                "invalidated": 0,
                "observation": 0,
                "realized_pnl": 0.0,
                "false_trigger_count": 0,
                "untriggered_count": 0,
                "invalidated_count": 0,
                "review_note": "TradeFlow DB not available",
            },
            "runtime_tier_meta": _fast_meta,
        }

    try:
        ledger = _ensure_paper_ledger_row(conn)
        principal = _rget(ledger, "principal", _DEFAULT_PRINCIPAL)
        cash_balance = _rget(ledger, "cash_balance", _DEFAULT_PRINCIPAL)

        rows = conn.execute(
            "SELECT * FROM tradeflow_paper_trades ORDER BY created_at DESC"
        ).fetchall()
        trades = [_row_to_paper_trade(r) for r in rows]

        closed = [t for t in trades if t["status"] == "closed"]
        invalidated = [t for t in trades if t["status"] == "invalidated"]
        tracking = [t for t in trades if t["status"] == "tracking"]
        pending = [t for t in trades if t["status"] == "pending"]
        open_pos = [t for t in trades if t["status"] == "open"]
        observation = [t for t in trades if t["status"] == "observation"]  # [TF-RISK-001]

        realized_pnl = sum(t["pnl"] for t in closed)
        false_trigger_count = sum(1 for t in closed if t["pnl"] < 0)
        untriggered_count = len(tracking)
        invalidated_count = len(invalidated)

        if not trades:
            review_note = "暂无模拟跟踪记录"
        elif untriggered_count == len(trades):
            review_note = f"全部 {untriggered_count} 只候选等待触发中"
        else:
            obs_part = f"，观察 {len(observation)} 只" if observation else ""
            review_note = (
                f"已平仓 {len(closed)} 只（其中亏损 {false_trigger_count} 只），"
                f"持仓 {len(open_pos)} 只，待确认 {len(pending)} 只，"
                f"已失效 {invalidated_count} 只{obs_part}"
            )

        review = {
            "total": len(trades),
            "tracking": untriggered_count,
            "pending": len(pending),
            "open": len(open_pos),
            "closed": len(closed),
            "invalidated": invalidated_count,
            "observation": len(observation),
            "realized_pnl": round(realized_pnl, 2),
            "false_trigger_count": false_trigger_count,
            "untriggered_count": untriggered_count,
            "invalidated_count": invalidated_count,
            "review_note": review_note,
            "principal": principal,
            "cash_balance": cash_balance,
            "total_pnl_pct": round((cash_balance - principal) / principal * 100, 2) if principal else 0.0,
        }

        return {
            "status": "ok",
            "trade_date": trade_date,
            "review": review,
            "trades": trades,
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


# [H-012] mandate_topic_registry
def get_topic_registry(tf_db_path: str = "") -> dict:
    """Return all registered topics from the topic registry."""
    _fast_meta = _tradeflow_meta("tradeflow_topic_registry")
    from tradingagents.tradeflow.topic_registry import (
        get_default_topic_registry,
        get_default_topic_definitions,
    )

    registry = get_default_topic_registry()
    definitions = get_default_topic_definitions()

    topics: list[dict] = []
    seen_topics: set[str] = set()

    for entry_dict in registry.to_dict().values():
        topics.append(entry_dict)
        seen_topics.add(entry_dict.get("topic", ""))

    for d in definitions:
        if d.topic not in seen_topics:
            topics.append({
                "topic": d.topic,
                "topic_status": "UNKNOWN",
                "topic_status_label": "未知",
                "lifecycle_state": "UNKNOWN",
                "policy_level": d.policy_level,
                "policy_level_weight": 0,
                "last_signal_date": "",
                "signal_count": 0,
                "evidence_links": [],
                "evidence_summary": "",
                "chain_segments": [s.to_dict() for s in d.chain_segments],
                "is_left_side": False,
                "is_observe_only": False,
                "is_confirmed": False,
                "matched_candidates": [],
            })
            seen_topics.add(d.topic)

    topics.sort(key=lambda t: t.get("signal_count", 0), reverse=True)

    return {
        "status": "ok",
        "topics": topics,
        "total_topics": len(topics),
        "runtime_tier_meta": _fast_meta,
    }


# [H-012] mandate_topic_registry
def get_topic_watchlist(
    trade_date: str,
    tf_db_path: str = "",
    max_symbols_per_topic: int = 5,
) -> dict:
    """Build a per-topic watchlist from candidates for a given trade date."""
    _fast_meta = _tradeflow_meta("tradeflow_topic_watchlist")
    conn = _connect(tf_db_path)
    if conn is None:
        return {
            "status": "no_data",
            "topics": [],
            "total_topics": 0,
            "total_symbols": 0,
            "runtime_tier_meta": _fast_meta,
        }

    try:
        rows = _query_by_date_or_effective(conn, "tradeflow_candidates", trade_date)
        candidates = []
        for row in rows:
            item = _row_to_candidate_item(row)
            candidates.append({
                "symbol": item.get("symbol", ""),
                "name": item.get("name", ""),
                "mandate_topic": item.get("mandate_topic", ""),
                "policy_tags": item.get("policy_tags", []),
                "company_role": item.get("company_role", ""),
                "beneficiary_path": item.get("beneficiary_path", []),
                "mandate_score_component": item.get("mandate_score", 0.0),
                "composite_score": item.get("composite_score", 0.0),
                "tier": item.get("tier", ""),
                "blocking_evidence_gaps": item.get("blocking_evidence_gaps", []),
                "watchlist_evidence_gap": item.get("watchlist_evidence_gap", []),
                "topic_lifecycle_state": item.get("topic_lifecycle_state", ""),
                "topic_signal_count": item.get("topic_signal_count", 0),
                "topic_last_signal_date": item.get("topic_last_signal_date", ""),
                "policy_evidence_refs": item.get("policy_evidence_refs", []),
            })

        from tradingagents.tradeflow.topic_registry import build_topic_watchlist
        result = build_topic_watchlist(
            candidates,
            max_symbols_per_topic=max_symbols_per_topic,
        )

        return {
            "status": "ok",
            "topics": [t.to_dict() for t in result.topics],
            "total_topics": result.total_topics,
            "total_symbols": result.total_symbols,
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


# [DATA-018] source_freshness_report
def get_source_freshness(
    symbol: str = "",
    raw_evidence: Optional[dict] = None,
) -> dict:
    """Generate source freshness & fallback report.

    When raw_evidence is not provided, returns catalog-based report
    (all sources show NORMAL_NO_DATA since no actual data was queried).
    """
    _fast_meta = _tradeflow_meta("tradeflow_source_freshness")

    from tradingagents.dataflows.source_freshness_report import (
        run_source_freshness_report,
    )

    report = run_source_freshness_report(raw_evidence, symbol=symbol)

    return {
        "status": "ok",
        "report_date": report.report_date,
        "generated_at": report.generated_at,
        "symbol": report.symbol,
        "entries": [e.to_dict() for e in report.entries],
        "summary": report.summary,
        "runtime_tier_meta": _fast_meta,
    }
