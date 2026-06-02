# [UI-001] tradeflow_api
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
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date}

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
            return {"status": "no_data", "trade_date": trade_date}

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
        }
    finally:
        conn.close()


def get_candidates(
    trade_date: str,
    tier: Optional[str] = None,
    need_deep_ta: Optional[bool] = None,
    candidate_type: Optional[str] = None,  # [H-005] mandate_radar_ui
    tf_db_path: str = "",
) -> dict:
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date}

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

        extra_params: list = []
        if tier and "tier" in columns:
            extra_params.append(tier)
        if need_deep_ta is not None:
            extra_params.append(1 if need_deep_ta else 0)
        if candidate_type and "candidate_type" in columns:  # [H-005] mandate_radar_ui
            extra_params.append(candidate_type)

        order_cols = []
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

        return {
            "status": "ok",
            "trade_date": trade_date,
            "candidates": items,
            "summary_agg": _compute_summary(items),
        }
    finally:
        conn.close()


def get_candidate_detail(symbol: str, trade_date: str, tf_db_path: str = "") -> dict:
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date}

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
            return {"status": "no_data", "trade_date": trade_date}

        detail = _row_to_candidate_detail(row)
        detail["action"] = _compute_action(detail)
        detail["reason"] = detail.get("why_deep_ta") or detail.get("why_not_deep_ta") or "候选观察"

        return {
            "status": "ok",
            "trade_date": trade_date,
            "candidate": detail,
        }
    finally:
        conn.close()


def get_observe(trade_date: str, tf_db_path: str = "") -> dict:
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date}

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
        }
    finally:
        conn.close()


def get_ta_queue(trade_date: str, tf_db_path: str = "") -> dict:
    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "trade_date": trade_date}

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
        }
    finally:
        conn.close()


def get_review(trade_date: str, tf_db_path: str = "") -> dict:
    plan_data = get_daily_plan(trade_date, tf_db_path)
    if plan_data.get("status") == "no_data":
        return {"status": "no_data", "trade_date": trade_date}

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
    }
