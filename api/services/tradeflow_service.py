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

        return {
            "status": "ok",
            "trade_date": trade_date,
            "candidates": items,
            "summary_agg": _compute_summary(items),
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


def get_observe(trade_date: str, tf_db_path: str = "") -> dict:
    conn = _connect(tf_db_path)
    _fast_meta = _tradeflow_meta("tradeflow_observe")  # [PERF-001]
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}  # [PERF-001]

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


def get_review(trade_date: str, tf_db_path: str = "") -> dict:
    _fast_meta = _tradeflow_meta("tradeflow_review")  # [PERF-001]
    plan_data = get_daily_plan(trade_date, tf_db_path)
    if plan_data.get("status") == "no_data":
        return {"status": "no_data", "trade_date": trade_date, "runtime_tier_meta": _fast_meta}  # [PERF-001]

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

    return {
        "status": "ok",
        "trade_date": trade_date,
        "reviewed_at": datetime.now().isoformat(),
        "results": results,
        "summary_agg": _compute_summary(candidates),
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

        actionable = [it for it in all_items if it.get("action_tier") == "actionable"][:3]
        watch = [it for it in all_items if it.get("action_tier") == "watch"][:8]
        scan = [it for it in all_items if it.get("action_tier") == "scan"]

        return {
            "status": "ok",
            "trade_date": trade_date,
            "actionable": actionable,
            "watch": watch,
            "scan": scan,
            "actionable_count": len([it for it in all_items if it.get("action_tier") == "actionable"]),
            "watch_count": len([it for it in all_items if it.get("action_tier") == "watch"]),
            "scan_count": len(scan),
            "summary_agg": _compute_summary(all_items),
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


# [TF-UX-003] post_market_review
def generate_review(trade_date: str, tf_db_path: str = "") -> dict:
    """Generate post-market review for trade_date using post_market_review module."""
    from tradingagents.tradeflow.candidate_engine import init_db
    from tradingagents.tradeflow.post_market_review import (
        build_candidate_performance_from_dict,
        run_post_market_review,
        save_review_report,
    )

    tf_db = tf_db_path or _get_tradeflow_db_path()
    init_db(tf_db)

    candidates_data = get_candidates(trade_date, tf_db_path=tf_db)
    if candidates_data.get("status") == "no_data":
        return {"status": "no_data", "trade_date": trade_date, "message": "无候选数据"}

    candidates = candidates_data.get("candidates", [])
    if not candidates:
        return {"status": "no_data", "trade_date": trade_date, "message": "候选列表为空"}

    performances = []
    for c in candidates:
        perf = build_candidate_performance_from_dict(c)
        performances.append(perf)

    summary = run_post_market_review(performances, candidate_date=trade_date)

    try:
        save_review_report(summary)
    except Exception:
        pass

    return {
        "status": "ok",
        "trade_date": trade_date,
        "review": {
            "review_date": summary.review_date,
            "candidate_date": summary.candidate_date,
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
        return result
    finally:
        conn.close()
