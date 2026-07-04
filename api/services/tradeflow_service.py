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
from tradingagents.tradeflow.strategy_config import DEFAULT_STRATEGY_CONFIG  # [TF-OBS-004] observe_refresh_alert_queue


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


def _connect(
    tf_db_path: str = "",
    *,
    read_only: bool = False,
) -> Optional[sqlite3.Connection]:
    db_path = tf_db_path or _get_tradeflow_db_path()
    if not os.path.exists(db_path):
        return None
    try:
        if read_only:
            # [H-016] mandate_daily_cli — genuinely read-only path: never call
            # init_db() (which issues CREATE/ALTER TABLE) and open the DB in
            # SQLite URI read-only mode so the report CLI / dry-run can never
            # mutate tradeflow.db even if the on-disk schema is stale.
            import urllib.request

            uri = "file:" + urllib.request.pathname2url(os.path.abspath(db_path)) + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            return conn
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


# [KB-008] research_attention_integration
def _enrich_candidate_with_research_attention(item: dict) -> dict:
    """把单 symbol 研报关注度（多研报重复提及因子）注入候选 item。

    只读、不调用 LLM、不写 DB；只作为研究优先级/解释信息，**不改变**强动作门禁
    与候选 tier。无命中时填充空结构（``research_attention_score=0``、
    ``research_attention_summary=""``），不影响 TA 主流程（NORMAL_NO_DATA 语义）。

    负面信息（stale / deprecated / 主题拥挤）通过 ``research_attention_detail``
    与 ``research_attention_summary`` 一并透出，前端必须能同时看到。
    """
    symbol = item.get("symbol") or ""
    if not symbol:
        item.setdefault("research_attention_score", 0.0)
        item.setdefault("knowledge_theme_count", 0)
        item.setdefault("research_attention_summary", "")
        item.setdefault("research_attention_detail", {})
        item.setdefault("research_attention_effective_score", 0.0)
        item.setdefault("research_attention_overheat_penalty", 0.0)
        return item

    try:
        from tradingagents.dataflows.local_knowledge_audit import (
            default_knowledge_root as _lk_default_root,
        )
        from tradingagents.dataflows.research_attention import (
            attention_to_summary as _kb008_to_summary,
            lookup_research_attention as _kb008_lookup,
        )
    except Exception:
        # 依赖不可用时退化为空结构，绝不阻塞候选读取主链路。
        item.setdefault("research_attention_score", 0.0)
        item.setdefault("knowledge_theme_count", 0)
        item.setdefault("research_attention_summary", "")
        item.setdefault("research_attention_detail", {})
        item.setdefault("research_attention_effective_score", 0.0)
        item.setdefault("research_attention_overheat_penalty", 0.0)
        return item

    try:
        sym_attention = _kb008_lookup(_lk_default_root(), symbol)
    except Exception:
        sym_attention = None
    summary = _kb008_to_summary(sym_attention)
    item["research_attention_score"] = summary["research_attention_score"]
    item["knowledge_theme_count"] = summary["knowledge_theme_count"]
    item["research_attention_summary"] = summary["research_attention_summary"]
    item["research_attention_detail"] = summary
    # [KB-009] research_attention_decay — 叠加候选过热惩罚（不改交易动作）。
    _apply_kb009_overheat_to_item(item, sym_attention)
    return item


# [KB-009] research_attention_decay
def _apply_kb009_overheat_to_item(item: dict, sym_attention: Any) -> None:
    """在 KB-008 symbol 级去重/时效衰减之上叠加候选过热惩罚。

    从候选 item 读取 ``overheat_flags`` / ``short_term_gain_pct``，配合 symbol
    的 ``theme_count`` 计算过热惩罚，覆写 ``research_attention_effective_score``
    与相关 explain/warnings。只降低研究优先级，**不改变**强动作门禁与候选 tier；
    依赖不可用时静默退化为已由 KB-008 写入的 symbol 级 effective score。
    """
    try:
        from tradingagents.dataflows.research_attention_decay import (
            apply_overheat_penalty as _kb009_apply_overheat,
            compute_symbol_decay as _kb009_compute_decay,
        )
    except Exception:
        return

    try:
        overheat_flags = item.get("overheat_flags") or []
        short_term_gain = item.get("short_term_gain_pct")
        if sym_attention is None:
            # 无命中：effective = 0，但仍透出 overheat_flags 便于前端展示负面信号。
            item["research_attention_effective_score"] = 0.0
            item["research_attention_overheat_penalty"] = 0.0
            return
        theme_count = sym_attention.theme_count
        decay = _kb009_compute_decay(sym_attention)
        decay = _kb009_apply_overheat(
            decay,
            overheat_flags=overheat_flags,
            short_term_gain_pct=short_term_gain,
            theme_count=theme_count,
        )
        item["research_attention_effective_score"] = round(decay.effective_score, 2)
        item["research_attention_overheat_penalty"] = round(decay.overheat_penalty, 2)
        # 把过热明细合并回 detail，前端一处即可看到完整 explain。
        detail = item.get("research_attention_detail")
        if isinstance(detail, dict):
            detail["research_attention_effective_score"] = round(
                decay.effective_score, 2
            )
            detail["research_attention_overheat_penalty"] = round(
                decay.overheat_penalty, 2
            )
            detail["research_attention_decay_explain"] = list(decay.explain)
            detail["research_attention_warnings"] = list(decay.warnings)
            detail["research_attention_overheat_flags"] = list(decay.overheat_flags)
    except Exception:
        return


def _enrich_candidates_with_research_attention(items: List[dict]) -> List[dict]:
    """批量注入研究关注度（[KB-008]）。

    共享一次全库倒排索引扫描结果，避免每个候选都重新扫一遍 wiki。当知识库
    不可读或 symbol 解析失败时，逐项降级为空结构。
    """
    if not items:
        return items
    try:
        from tradingagents.dataflows.local_knowledge_audit import (
            default_knowledge_root as _lk_default_root,
        )
        from tradingagents.dataflows.research_attention import (
            attention_to_summary as _kb008_to_summary,
            compute_research_attention as _kb008_compute,
            _symbols_equivalent as _kb008_equiv,
        )
    except Exception:
        for it in items:
            it.setdefault("research_attention_score", 0.0)
            it.setdefault("knowledge_theme_count", 0)
            it.setdefault("research_attention_summary", "")
            it.setdefault("research_attention_detail", {})
            it.setdefault("research_attention_effective_score", 0.0)
            it.setdefault("research_attention_overheat_penalty", 0.0)
        return items

    # 共享一次扫描结果；按 symbol_key 建索引加速候选批量查询。
    by_key: Dict[str, Any] = {}
    try:
        result = _kb008_compute(_lk_default_root())
        for sym in result.symbols:
            by_key[sym.symbol_key.upper()] = sym
            if sym.bare_code:
                by_key.setdefault(sym.bare_code.upper(), sym)
    except Exception:
        pass

    for it in items:
        symbol = (it.get("symbol") or "").strip().upper()
        if not symbol:
            it.setdefault("research_attention_score", 0.0)
            it.setdefault("knowledge_theme_count", 0)
            it.setdefault("research_attention_summary", "")
            it.setdefault("research_attention_detail", {})
            it.setdefault("research_attention_effective_score", 0.0)
            it.setdefault("research_attention_overheat_penalty", 0.0)
            continue
        # 优先精确匹配 symbol_key；找不到再做等价比较。
        sym_attention = by_key.get(symbol)
        if sym_attention is None:
            for key, sym in by_key.items():
                if _kb008_equiv(symbol, key, sym.bare_code):
                    sym_attention = sym
                    break
        summary = _kb008_to_summary(sym_attention)
        it["research_attention_score"] = summary["research_attention_score"]
        it["knowledge_theme_count"] = summary["knowledge_theme_count"]
        it["research_attention_summary"] = summary["research_attention_summary"]
        it["research_attention_detail"] = summary
        # [KB-009] research_attention_decay — 批量也叠加候选过热惩罚。
        _apply_kb009_overheat_to_item(it, sym_attention)
    return items


# [KB-004] tradeflow_knowledge_score
def _resolve_knowledge_root() -> str:
    """解析当前生效的本地知识库根目录（环境变量优先）。

    与 KB-008 共享 ``default_knowledge_root`` 的解析口径，保证 TA 报告 /
    TradeFlow 候选 / 研报关注度使用同一份 wiki 索引。
    """
    try:
        from tradingagents.dataflows.local_knowledge_audit import (
            default_knowledge_root as _lk_default_root,
        )
        return _lk_default_root()
    except Exception:
        return ""


# [KB-004] tradeflow_knowledge_score
def _query_local_knowledge_for_candidate(
    item: dict,
    knowledge_root: str,
):
    """按候选 symbol / name / mandate_topic 只读查询本地知识。

    查询异常返回 ``FAILED`` 结构而不是 ``None``，避免把基础设施故障误判为
    Tree Work 待补研报。命中维度优先级：symbol > name > themes(mandate_topic)。
    当三者都为空时返回 ``None``。
    """
    if not knowledge_root:
        return None
    symbol = (item.get("symbol") or "").strip() or None
    name = (item.get("name") or "").strip() or None
    topic = (item.get("mandate_topic") or "").strip() or None
    themes = [topic] if topic else None
    if not any([symbol, name, themes]):
        return None
    try:
        from tradingagents.dataflows.local_knowledge_provider import (
            query_local_knowledge as _kb004_query,
        )
        return _kb004_query(
            knowledge_root,
            symbol=symbol,
            name=name,
            themes=themes,
        )
    except Exception as exc:
        try:
            from tradingagents.dataflows.local_knowledge_provider import (
                LocalKnowledgeQueryResult,
                STATUS_FAILED,
            )

            return LocalKnowledgeQueryResult(
                status=STATUS_FAILED,
                knowledge_root=knowledge_root,
                query={
                    "symbol": symbol or "",
                    "name": name or "",
                    "themes": themes or [],
                },
                errors=[f"query_local_knowledge failed: {type(exc).__name__}: {exc}"],
            )
        except Exception:
            return None


# [KB-004] tradeflow_knowledge_score
def _apply_local_knowledge_to_item(
    item: dict,
    knowledge_result,
) -> dict:
    """把单次本地知识查询结果注入候选 item。

    只读、不调用 LLM、不写 DB；``local_knowledge_score`` 仅作为研究优先级 /
    解释信息，**不改变** tier / action / 强动作门禁。无命中时填充空结构
    （score=0、hit_count=0），符合 NORMAL_NO_DATA 语义。过期/低置信命中页
    不贡献分数但仍透出，便于前端提示用户更新知识库。
    """
    try:
        from tradingagents.dataflows.local_knowledge_provider import (
            compute_local_knowledge_score as _kb004_score,
            needs_tree_work_research as _kb004_needs,
        )
    except Exception:
        item.setdefault("local_knowledge_score", 0.0)
        item.setdefault("knowledge_hit_count", 0)
        item.setdefault("local_knowledge_summary", "")
        item.setdefault("local_knowledge_detail", {})
        item.setdefault("needs_tree_work_research", False)
        return item

    score_dict = _kb004_score(knowledge_result)
    # [KB-004] tradeflow_knowledge_score — FAILED means infrastructure/config
    # failure, not a Tree Work research gap. Do not turn a bad knowledge_root
    # into a false backlog signal.
    needs_flag = False
    if score_dict.get("status") != "FAILED":
        needs_flag = _kb004_needs(
            candidate_type=item.get("candidate_type", ""),
            mandate_topic=item.get("mandate_topic", ""),
            topic_status=item.get("topic_lifecycle_state") or item.get("topic_status", ""),
            mandate_score=float(item.get("mandate_score") or item.get("mandate_score_component") or 0.0),
            knowledge_summary=score_dict,
        )
    item["local_knowledge_score"] = score_dict["local_knowledge_score"]
    item["knowledge_hit_count"] = score_dict["knowledge_hit_count"]
    item["local_knowledge_summary"] = score_dict["local_knowledge_summary"]
    item["local_knowledge_detail"] = score_dict
    item["needs_tree_work_research"] = bool(needs_flag)
    return item


# [KB-004] tradeflow_knowledge_score
def _enrich_candidate_with_local_knowledge(item: dict) -> dict:
    """单条候选注入本地知识命中分（[KB-004]）。

    失败 / 无 knowledge_root 时静默退化为空结构，绝不阻塞候选读取主链路。
    """
    symbol = item.get("symbol") or ""
    if not symbol:
        return _apply_local_knowledge_to_item(item, None)
    knowledge_root = _resolve_knowledge_root()
    if not knowledge_root:
        return _apply_local_knowledge_to_item(item, None)
    knowledge_result = _query_local_knowledge_for_candidate(item, knowledge_root)
    return _apply_local_knowledge_to_item(item, knowledge_result)


# [KB-004] tradeflow_knowledge_score
def _enrich_candidates_with_local_knowledge(items: List[dict]) -> List[dict]:
    """批量注入本地知识命中分（[KB-004]）。

    与 KB-008 不同，本地知识查询是按 (symbol, name, themes) 多维度匹配，
    无法像 KB-008 那样共享一次扫描结果（KB-003 的 query 接口本身就是按
    查询条件过滤）。因此批量版逐项调用 ``query_local_knowledge``；对于
    TradeFlow 候选池规模（通常 < 20 只）这是可接受的开销。失败项降级为
    空结构，绝不阻塞候选读取主链路。
    """
    if not items:
        return items
    knowledge_root = _resolve_knowledge_root()
    for it in items:
        if not knowledge_root or not (it.get("symbol") or ""):
            _apply_local_knowledge_to_item(it, None)
            continue
        knowledge_result = _query_local_knowledge_for_candidate(it, knowledge_root)
        _apply_local_knowledge_to_item(it, knowledge_result)
    return items


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

        # [KB-008] research_attention_integration — 注入研报关注度字段。
        # 只读、不调用 LLM、共享一次全库扫描；不改变 tier / action 门禁。
        candidate_items = _enrich_candidates_with_research_attention(candidate_items)

        # [KB-004] tradeflow_knowledge_score — 注入本地知识命中分与证据摘要。
        # 只读、不调用 LLM、不改变 tier / action 门禁；过期/低置信命中不加分。
        candidate_items = _enrich_candidates_with_local_knowledge(candidate_items)

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

        # [KB-008] research_attention_integration — 注入研报关注度字段。
        # 只读、不调用 LLM、共享一次全库扫描；不改变 tier / action 门禁。
        items = _enrich_candidates_with_research_attention(items)

        # [KB-004] tradeflow_knowledge_score — 注入本地知识命中分与证据摘要。
        # 只读、不调用 LLM、不改变 tier / action 门禁；过期/低置信命中不加分。
        items = _enrich_candidates_with_local_knowledge(items)

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
            "concentration_summary": pool_result.concentration_summary,  # [H-014] mandate_concentration_gate
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

        # [KB-008] research_attention_integration — 注入研报关注度字段。
        # 只读、不调用 LLM、不改变 tier / action 门禁；无命中返回空结构。
        detail = _enrich_candidate_with_research_attention(detail)

        # [KB-004] tradeflow_knowledge_score — 候选详情也注入本地知识命中分与
        # 证据摘要（命中页路径 / 更新时间 / 风险提示），便于前端 drawer 展示。
        detail = _enrich_candidate_with_local_knowledge(detail)

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


def _observe_market_status() -> dict:
    """Return is_trading_day / is_market_hours for observe refresh metadata.  # [TF-OBS-004] observe_refresh_alert_queue

    Defensive: never raises — falls back to False on any import error so the
    observe endpoint stays available even when scheduler helpers are unavailable.
    """
    try:
        from tradingagents.tradeflow.strategy_config import DEFAULT_STRATEGY_CONFIG
        from zoneinfo import ZoneInfo
        now = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
        today = now.strftime("%Y-%m-%d")
        from tradingagents.tradeflow.intraday_observe_scheduler import _is_trading_day, _is_market_hours
        return {
            "is_trading_day": _is_trading_day(today),
            "is_market_hours": _is_market_hours(DEFAULT_STRATEGY_CONFIG),
        }
    except Exception:
        return {"is_trading_day": False, "is_market_hours": False}


def _build_trigger_explain(observe_state: str, current_price, trigger_price, invalid_price,
                           near_band_pct: float) -> dict:
    """Build structured "why triggered / why not / how far off" explanation.  # [TF-OBS-004] observe_refresh_alert_queue

    Returns dict with:
    - category: triggered / near_trigger / invalidated / waiting / no_data
    - why_triggered: reason string when triggered
    - why_not: reason string when not triggered
    - how_far_off: human-readable distance to trigger
    - breach_pct: signed % distance from trigger price (positive = above trigger)
    """
    result = {
        "category": "waiting",
        "why_triggered": "",
        "why_not": "",
        "how_far_off": "",
        "breach_pct": None,
    }
    if observe_state == "INVALIDATED" or (invalid_price and current_price is not None and current_price <= invalid_price):
        result["category"] = "invalidated"
        below = ""
        if invalid_price and current_price is not None:
            below_pct = (invalid_price - current_price) / invalid_price * 100
            result["breach_pct"] = (current_price - trigger_price) / trigger_price * 100 if trigger_price else None
            below = f"（跌破失效价 {invalid_price:.2f}，偏离 {below_pct:.2f}%）"
        result["why_not"] = f"价格已跌破失效价{below}，观察失效"
        return result
    if trigger_price is None or current_price is None or trigger_price <= 0:
        result["category"] = "no_data"
        result["why_not"] = "缺少触发价或实时行情，无法判断"
        return result

    breach_pct = (current_price - trigger_price) / trigger_price * 100
    result["breach_pct"] = breach_pct

    if current_price >= trigger_price:
        result["category"] = "triggered"
        result["why_triggered"] = f"当前价 {current_price:.2f} 达到触发价 {trigger_price:.2f}（超出 {breach_pct:.2f}%）"
        result["how_far_off"] = f"已超出触发价 {breach_pct:.2f}%"
        return result

    to_trigger_pct = (trigger_price - current_price) / trigger_price * 100
    result["how_far_off"] = f"还差 {to_trigger_pct:.2f}% 触发"
    # near-trigger band: within (1 - near_band_pct) * trigger_price and trigger_price (inclusive, with epsilon)
    if to_trigger_pct <= near_band_pct * 100 + 1e-9:
        result["category"] = "near_trigger"
        result["why_not"] = f"当前价 {current_price:.2f} 接近触发价 {trigger_price:.2f}（还差 {to_trigger_pct:.2f}%）"
    else:
        result["category"] = "waiting"
        result["why_not"] = f"当前价 {current_price:.2f} 距触发价 {trigger_price:.2f} 仍差 {to_trigger_pct:.2f}%"
    return result


def get_observe(trade_date: str, tf_db_path: str = "") -> dict:
    tf_db = tf_db_path or _get_tradeflow_db_path()
    _fast_meta = _tradeflow_meta("tradeflow_observe")  # [PERF-001]
    # [TF-OBS-005] observe_date_semantics — cross-date plan hint
    _sem = _resolve_observe_date_semantics(tf_db, trade_date)

    if not os.path.exists(tf_db):
        _mkt = _observe_market_status()  # [TF-OBS-004]
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
            "refresh_interval_seconds": DEFAULT_STRATEGY_CONFIG.observe_interval_minutes * 60,  # [TF-OBS-004]
            "is_market_hours": _mkt["is_market_hours"],   # [TF-OBS-004]
            "is_trading_day": _mkt["is_trading_day"],     # [TF-OBS-004]
            "near_trigger_count": 0,   # [TF-OBS-004]
            "pending_count": 0,        # [TF-OBS-004]
            "plan_date": _sem["plan_date"],                          # [TF-OBS-005]
            "effective_trade_date": _sem["effective_trade_date"],    # [TF-OBS-005]
            "observe_date": _sem["observe_date"],                    # [TF-OBS-005]
            "non_trading_day_plan": _sem["non_trading_day_plan"],    # [TF-OBS-005]
            "next_trading_day_hint": _sem["next_trading_day_hint"],  # [TF-OBS-005]
            "is_view_trading_day": _sem["is_view_trading_day"],      # [TF-OBS-005]
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
        # [TF-OBS-005] observe_date_semantics — when viewing a non-trading-day plan
        # date, the precheck reports "当日无活跃候选" because candidates are mapped to
        # the next trading day. Replace that false negative with a cross-date hint so
        # the user understands the plan is queued for the next trading day.
        if _sem["non_trading_day_plan"] and _sem["next_trading_day_hint"]:
            observe_reason = _sem["next_trading_day_hint"]
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
                # [TF-OBS-003] observe_paper_sync — sync ledger on page-open auto-run
                _sync_paper_from_observe(_auto.details, tf_db)
                observe_auto_run = True
                last_observed_at = _auto.run_time
                if _auto.skipped_reason:
                    observe_reason = _auto.skipped_reason
            except Exception as e:
                observe_reason = f"自动执行失败: {e}"

    conn = _connect(tf_db_path)
    if conn is None:
        _mkt = _observe_market_status()  # [TF-OBS-004]
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
            "refresh_interval_seconds": DEFAULT_STRATEGY_CONFIG.observe_interval_minutes * 60,  # [TF-OBS-004]
            "is_market_hours": _mkt["is_market_hours"],   # [TF-OBS-004]
            "is_trading_day": _mkt["is_trading_day"],     # [TF-OBS-004]
            "near_trigger_count": 0,   # [TF-OBS-004]
            "pending_count": 0,        # [TF-OBS-004]
            "plan_date": _sem["plan_date"],                          # [TF-OBS-005]
            "effective_trade_date": _sem["effective_trade_date"],    # [TF-OBS-005]
            "observe_date": _sem["observe_date"],                    # [TF-OBS-005]
            "non_trading_day_plan": _sem["non_trading_day_plan"],    # [TF-OBS-005]
            "next_trading_day_hint": _sem["next_trading_day_hint"],  # [TF-OBS-005]
            "is_view_trading_day": _sem["is_view_trading_day"],      # [TF-OBS-005]
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
        near_trigger_total = 0   # [TF-OBS-004] observe_refresh_alert_queue
        pending_total = 0        # [TF-OBS-004]
        _near_band = DEFAULT_STRATEGY_CONFIG.observe_near_trigger_band_pct  # [TF-OBS-004]

        # [TF-OBS-003] observe_paper_sync — map symbol → paper ledger status
        paper_status_map: dict[str, str] = {}
        try:
            pt_rows = conn.execute(
                "SELECT symbol, status FROM tradeflow_paper_trades ORDER BY updated_at DESC"
            ).fetchall()
            for pt in pt_rows:
                _psym = normalize_tradeflow_symbol(pt["symbol"])
                if _psym not in paper_status_map:
                    paper_status_map[_psym] = pt["status"]
        except Exception:
            pass

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

            _tp = _rget(r, "trigger_price")
            _ip = _rget(r, "invalid_price")
            # [TF-OBS-004] observe_refresh_alert_queue — structured trigger explanation
            _explain = _build_trigger_explain(state, current_price, _tp, _ip, _near_band)
            _dist_pct = _explain.get("breach_pct")
            _near = _explain["category"] == "near_trigger"

            _paper = paper_status_map.get(sym, "")  # [TF-OBS-003] observe_paper_sync

            item = {
                "symbol": sym,
                "name": resolve_tradeflow_name(sym, raw_n),  # [UI-008]
                "observe_state": state,
                "trigger_price": _tp,
                "invalid_price": _ip,
                "observe_trigger_count": _rget(r, "observe_trigger_count", 0) or 0,
                "observe_first_trigger_time": _rget(r, "observe_first_trigger_time", ""),
                "tier": _rget(r, "tier", ""),
                "composite_score": _rget(r, "composite_score", 0.0) or 0.0,
                "current_price": current_price,
                "trigger_reason": trigger_reason,
                "strategy_tags": _parse_json(_rget(r, "strategy_tags_json"), []),
                "paper_status": _paper,  # [TF-OBS-003] observe_paper_sync
                "trigger_distance_pct": _dist_pct,  # [TF-OBS-004]
                "near_trigger": _near,               # [TF-OBS-004]
                "trigger_explain": _explain,         # [TF-OBS-004]
            }
            items.append(item)
            if state == "TRIGGERED":
                triggered += 1
            elif state == "INVALIDATED":
                invalidated += 1
            else:
                waiting += 1
            if _near:
                near_trigger_total += 1
            if _paper == "pending":
                pending_total += 1

        _mkt = _observe_market_status()  # [TF-OBS-004]
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
            "refresh_interval_seconds": DEFAULT_STRATEGY_CONFIG.observe_interval_minutes * 60,  # [TF-OBS-004]
            "is_market_hours": _mkt["is_market_hours"],   # [TF-OBS-004]
            "is_trading_day": _mkt["is_trading_day"],     # [TF-OBS-004]
            "near_trigger_count": near_trigger_total,     # [TF-OBS-004]
            "pending_count": pending_total,               # [TF-OBS-004]
            "plan_date": _sem["plan_date"],                          # [TF-OBS-005]
            "effective_trade_date": _sem["effective_trade_date"],    # [TF-OBS-005]
            "observe_date": _sem["observe_date"],                    # [TF-OBS-005]
            "non_trading_day_plan": _sem["non_trading_day_plan"],    # [TF-OBS-005]
            "next_trading_day_hint": _sem["next_trading_day_hint"],  # [TF-OBS-005]
            "is_view_trading_day": _sem["is_view_trading_day"],      # [TF-OBS-005]
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


# [TF-REVIEW-004] review_empty_diagnostics
def _has_observe_signals_for_date(db_path: str, trade_date: str) -> bool:
    """Return True if tradeflow_signals has any observe_* signal on trade_date."""
    if not os.path.exists(db_path) or not trade_date:
        return False
    try:
        conn = _connect(db_path)
        if conn is None:
            return False
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM tradeflow_signals "
                "WHERE signal_type LIKE 'observe_%' AND date(signal_time) = ?",
                (trade_date,),
            ).fetchone()
            return bool(row and row["cnt"] > 0)
        finally:
            conn.close()
    except Exception:
        return False


# [TF-REVIEW-004] review_empty_diagnostics
def _review_report_exists(review_date: str, tf_db_path: str = "") -> bool:
    """Return True if a saved review report exists for review_date.

    Prefers the per-DB record in ``tradeflow_review_reports`` so report
    existence stays isolated to the queried tradeflow DB (test isolation).
    Falls back to the project report file *only* when querying the default
    production DB, ensuring injected test DBs never leak global report files.
    """
    if not review_date:
        return False
    db_path = tf_db_path or _get_tradeflow_db_path()
    conn = _connect(db_path)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT 1 FROM tradeflow_review_reports WHERE review_date = ?",
                (review_date,),
            ).fetchone()
            if row:
                return True
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    # Legacy file fallback — only for the default production DB context,
    # never for injected (test) DBs, to keep tests isolated.
    if db_path == _get_tradeflow_db_path():
        report_path = os.path.join(
            _get_project_root(), "docs", "tradeflow_reviews", f"{review_date}.md"
        )
        return os.path.exists(report_path)
    return False


# [TF-OBS-005] observe_date_semantics
def _resolve_observe_date_semantics(tf_db: str, trade_date: str) -> dict:
    """Resolve plan_date / effective_trade_date / observe_date for an observe view.

    Detects cross-date plans: when a candidate pool was generated on a non-trading
    day (e.g. Saturday 2026-05-30) but is effective for the next trading day
    (e.g. Monday 2026-06-01). This lets the observe UI explain "this plan is for
    the next trading day" instead of showing a false "no active candidates" when
    a user opens the observe tab on the non-trading plan date itself.

    Returns dict with:
    - plan_date: plan_date of the cross-date plan covering this view (or "")
    - effective_trade_date: effective trading day (or "")
    - observe_date: same as effective_trade_date (first version)
    - non_trading_day_plan: True when plan_date != effective_trade_date
    - next_trading_day_hint: human-readable hint, "" when not applicable
    - is_view_trading_day: whether the viewed trade_date is itself a trading day
    """
    result = {
        "plan_date": "",
        "effective_trade_date": "",
        "observe_date": "",
        "non_trading_day_plan": False,
        "next_trading_day_hint": "",
        "is_view_trading_day": True,
    }

    try:
        from tradingagents.dataflows.trade_calendar import is_cn_trading_day
        result["is_view_trading_day"] = bool(is_cn_trading_day(trade_date)) if trade_date else True
    except Exception:
        pass

    if not trade_date or not os.path.exists(tf_db):
        return result

    conn = _connect(tf_db)
    if conn is None:
        return result
    try:
        columns = _table_columns(conn, "tradeflow_candidates")
        if "plan_date" not in columns or "effective_trade_date" not in columns:
            return result

        # Case 1: viewed date is itself the plan_date (e.g. user opens observe on
        # the Saturday/Sunday the pool was generated). Look for a cross-date plan.
        row = conn.execute(
            "SELECT plan_date, effective_trade_date FROM tradeflow_candidates "
            "WHERE plan_date = ? AND effective_trade_date != '' "
            "AND effective_trade_date != plan_date "
            "ORDER BY effective_trade_date DESC LIMIT 1",
            (trade_date,),
        ).fetchone()

        if row is None:
            # Case 2: viewed date is the effective_trade_date (e.g. Monday).
            # Report the originating plan_date so the UI can show provenance.
            row = conn.execute(
                "SELECT plan_date, effective_trade_date FROM tradeflow_candidates "
                "WHERE effective_trade_date = ? AND plan_date != '' "
                "AND plan_date != effective_trade_date "
                "ORDER BY plan_date DESC LIMIT 1",
                (trade_date,),
            ).fetchone()

        if row and row["effective_trade_date"]:
            pd = row["plan_date"] or trade_date
            eff = row["effective_trade_date"]
            result["plan_date"] = pd
            result["effective_trade_date"] = eff
            result["observe_date"] = eff
            result["non_trading_day_plan"] = pd != eff
            if pd != eff:
                if not result["is_view_trading_day"] and pd == trade_date:
                    # Viewing the non-trading plan date itself → next-trading-day hint
                    result["next_trading_day_hint"] = (
                        f"该候选池于 {pd}（非交易日）生成，将在 {eff}（下一交易日）观察"
                    )
                else:
                    # Viewing the effective trading day; informational provenance
                    result["next_trading_day_hint"] = (
                        f"该候选池由 {pd} 生成，于 {eff}（交易日）观察"
                    )
    finally:
        conn.close()
    return result


# [TF-REVIEW-004] review_empty_diagnostics
def _diagnose_review_empty_state(trade_date: str, tf_db_path: str = "") -> dict:
    """Diagnose WHY a post-market Review is empty for ``trade_date``.

    Returns a dict with:
      - empty_reason: one of ReviewEmptyReason values ("" if not empty-diagnostic)
      - empty_reason_message: Chinese explanation
      - suggested_action: actionable hint for the user
      - available_plan_dates: dates that have candidates
      - latest_plan_date
      - has_observe_signals: whether observe signals exist for the relevant date
      - plan_date / effective_trade_date / review_date: cross-date mapping
    """
    from tradingagents.tradeflow.post_market_review import ReviewEmptyReason
    from tradingagents.tradeflow.date_semantics import (
        find_latest_plan_date,
        next_cn_trading_day,
    )
    from tradingagents.dataflows.trade_calendar import is_cn_trading_day

    db_path = tf_db_path or _get_tradeflow_db_path()
    available_dates = _get_available_dates(tf_db_path)

    diag: Dict[str, Any] = {
        "empty_reason": "",
        "empty_reason_message": "",
        "suggested_action": "",
        "available_plan_dates": available_dates,
        "latest_plan_date": max(available_dates) if available_dates else "",
        "has_observe_signals": False,
        "plan_date": "",
        "effective_trade_date": "",
        "review_date": "",
    }

    # Case 1: no candidates anywhere → NO_CANDIDATES
    if not available_dates:
        reason = ReviewEmptyReason.NO_CANDIDATES
        diag["empty_reason"] = reason.value
        diag["empty_reason_message"] = reason.message_cn
        diag["suggested_action"] = reason.suggested_action_cn
        return diag

    best = find_latest_plan_date(available_dates, trade_date)
    is_td = is_cn_trading_day(trade_date)
    has_signals = _has_observe_signals_for_date(db_path, trade_date) or (
        _has_observe_signals_for_date(db_path, best) if best else False
    )
    diag["has_observe_signals"] = has_signals

    # Case 2: queried date is a non-trading day → plan maps to next trading day
    # This takes precedence: a weekend plan (e.g. 5/31) should be reviewed on
    # the next trading day (e.g. 6/1), regardless of where candidates are stored.
    if not is_td:
        reason = ReviewEmptyReason.NON_TRADING_DAY_MAPPED
        eff = next_cn_trading_day(trade_date)
        diag["empty_reason"] = reason.value
        diag["empty_reason_message"] = reason.message_cn
        diag["suggested_action"] = reason.suggested_action_cn
        diag["plan_date"] = best or trade_date
        diag["effective_trade_date"] = eff
        diag["review_date"] = eff
        return diag

    # Case 3: candidates exist for this trading day (best == trade_date)
    if best and best == trade_date:
        if not has_signals:
            # Candidates present but observe never ran
            reason = ReviewEmptyReason.NO_OBSERVE
        elif not _review_report_exists(trade_date, db_path):
            reason = ReviewEmptyReason.NOT_GENERATED
        else:
            # Report exists but data still empty → likely market data missing
            reason = ReviewEmptyReason.MARKET_DATA_MISSING
        diag["empty_reason"] = reason.value
        diag["empty_reason_message"] = reason.message_cn
        diag["suggested_action"] = reason.suggested_action_cn
        diag["plan_date"] = best
        diag["effective_trade_date"] = best
        diag["review_date"] = trade_date
        return diag

    # Case 4: candidates exist elsewhere but not for this trading day
    if best and best != trade_date and is_td:
        reason = ReviewEmptyReason.NO_CANDIDATES
        diag["empty_reason"] = reason.value
        diag["empty_reason_message"] = reason.message_cn
        diag["suggested_action"] = reason.suggested_action_cn
        diag["plan_date"] = best
        return diag

    # Fallback: no candidates for this date
    reason = ReviewEmptyReason.NO_CANDIDATES
    diag["empty_reason"] = reason.value
    diag["empty_reason_message"] = reason.message_cn
    diag["suggested_action"] = reason.suggested_action_cn
    return diag


# [TF-REVIEW-005] review_observe_paper_attribution
def _load_review_attribution(
    conn: sqlite3.Connection,
    trade_date: str,
    symbols: Optional[list[str]] = None,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Load observe signals + paper ledger status for the review date.

    Returns ``(signal_map, paper_map)``:

    * ``signal_map[symbol]`` = latest observe signal for ``trade_date`` with
      ``{state, signal_type, current_price, trigger_reason, signal_time,
      has_signal_for_date=True}``.
    * ``paper_map[symbol]`` = latest paper ledger status string
      (tracking/pending/open/closed/invalidated/observation).

    Reads only; never writes. Tolerates missing tables/columns.
    """
    signal_map: dict[str, dict] = {}
    paper_map: dict[str, str] = {}

    # Observe signals — latest per symbol for the date
    try:
        sig_rows = conn.execute(
            "SELECT signal_time, symbol, signal_type, evidence_json "
            "FROM tradeflow_signals "
            "WHERE signal_type LIKE 'observe_%' "
            "ORDER BY signal_time DESC"
        ).fetchall()
    except Exception:
        sig_rows = []
    for sig in sig_rows:
        try:
            sym = normalize_tradeflow_symbol(sig["symbol"])
        except Exception:
            continue
        if sym in signal_map:
            continue  # latest only (rows are DESC by signal_time)
        if symbols is not None and sym not in symbols:
            continue
        ev = _parse_json(_rget(sig, "evidence_json", "{}"), {})
        # Match the review date (observe evidence carries trade_date)
        if ev.get("trade_date") != trade_date:
            continue
        sig_type = _rget(sig, "signal_type", "")
        sig_state = str(ev.get("observe_state", "") or "")
        if not sig_state:
            # Derive from signal_type when evidence lacks observe_state
            if sig_type == "observe_triggered":
                sig_state = "TRIGGERED"
            elif sig_type == "observe_invalidated":
                sig_state = "INVALIDATED"
            elif sig_type == "observe_expired":
                sig_state = "EXPIRED"
            else:
                sig_state = "WAITING"
        signal_map[sym] = {
            "state": sig_state,
            "signal_type": sig_type,
            "current_price": ev.get("current_price"),
            "trigger_reason": ev.get("trigger_reason", "") or "",
            "signal_time": _rget(sig, "signal_time", "") or "",
            "has_signal_for_date": True,
        }

    # Paper ledger status — latest per symbol
    try:
        pt_rows = conn.execute(
            "SELECT symbol, status FROM tradeflow_paper_trades "
            "ORDER BY updated_at DESC, created_at DESC"
        ).fetchall()
    except Exception:
        pt_rows = []
    for pt in pt_rows:
        try:
            sym = normalize_tradeflow_symbol(pt["symbol"])
        except Exception:
            continue
        if sym in paper_map:
            continue
        if symbols is not None and sym not in symbols:
            continue
        paper_map[sym] = _rget(pt, "status", "") or ""

    return signal_map, paper_map


def _enrich_review_result_with_attribution(
    result: dict,
    signal: Optional[dict],
    paper_status: str,
) -> dict:
    """Attach observe+paper attribution fields to a get_review result item.

    Pure dict transformation; no side effects. Tag: [TF-REVIEW-005].
    """
    from tradingagents.tradeflow.post_market_review import (
        classify_review_bucket,
        review_bucket_reason,
    )

    signal = signal or {}
    signal_state = signal.get("state", "") or ""
    has_signal = bool(signal.get("has_signal_for_date", False))
    observe_state = result.get("observe_state", "WAITING")

    bucket = classify_review_bucket(
        observe_state=observe_state,
        paper_status=paper_status,
        has_signal_for_date=has_signal,
        signal_state=signal_state,
    )
    result["paper_status"] = paper_status
    result["signal_state"] = signal_state
    result["signal_current_price"] = signal.get("current_price")
    result["signal_trigger_reason"] = signal.get("trigger_reason", "") or ""
    result["signal_time"] = signal.get("signal_time", "") or ""
    result["has_signal_for_date"] = has_signal
    result["review_bucket"] = bucket.value
    result["review_bucket_label"] = bucket.label_cn
    result["review_bucket_reason"] = review_bucket_reason(
        bucket,
        observe_state=observe_state,
        paper_status=paper_status,
        signal_state=signal_state,
        has_signal_for_date=has_signal,
    )
    return result


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
        # [TF-REVIEW-004] review_empty_diagnostics — diagnose the empty reason
        from tradingagents.tradeflow.post_market_review import ReviewDataStatus
        _diag = _diagnose_review_empty_state(trade_date, tf_db_path)
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "data_status": ReviewDataStatus.NO_CANDIDATES.value,
            "data_status_message": ReviewDataStatus.NO_CANDIDATES.message_cn,
            "runtime_tier_meta": _fast_meta,
            "empty_reason": _diag["empty_reason"],
            "empty_reason_message": _diag["empty_reason_message"],
            "suggested_action": _diag["suggested_action"],
            "available_plan_dates": _diag["available_plan_dates"],
            "latest_plan_date": _diag["latest_plan_date"],
            "has_observe_signals": _diag["has_observe_signals"],
            "plan_date": _diag["plan_date"],
            "effective_trade_date": _diag["effective_trade_date"],
            "review_date": _diag["review_date"],
        }

    candidates = plan_data.get("candidates", [])

    # [TF-REVIEW-005] review_observe_paper_attribution — load observe signals + paper ledger
    _review_symbols = [normalize_tradeflow_symbol(c.get("symbol", "")) for c in candidates]
    _review_symbols = [s for s in _review_symbols if s]
    _signal_map: dict[str, dict] = {}
    _paper_map: dict[str, str] = {}
    _attr_conn = _connect(tf_db_path)
    if _attr_conn is not None:
        try:
            _signal_map, _paper_map = _load_review_attribution(
                _attr_conn, trade_date, symbols=_review_symbols or None
            )
        finally:
            _attr_conn.close()

    results = []
    for entry in candidates:
        action = entry.get("action", "OBSERVE")
        # [TF-REVIEW-003] strategy_attribution_review — derive attribution + feedback
        from tradingagents.tradeflow.post_market_review import (
            HitAttribution,
            classify_hit_attribution,
            compute_next_day_feedback,
            CandidatePerformance,
        )
        _observe_state = entry.get("observe_state", "WAITING")
        _candidate_type = entry.get("candidate_type", "")
        _strategy_tags = entry.get("strategy_tags", [])
        _risk_flags = entry.get("risk_flags", [])
        _missing_evidence = entry.get("missing_evidence", [])
        _stub = CandidatePerformance(
            symbol=entry.get("symbol", ""),
            trade_date=entry.get("trade_date", trade_date),
            entry_price=float(entry.get("trigger_price") or 0),
            trigger_price=entry.get("trigger_price"),
            invalid_price=entry.get("invalid_price"),
            strategy_tags=_strategy_tags,
            observe_state=_observe_state,
            risk_flags=_risk_flags,
            candidate_type=_candidate_type,
            split_scores={
                "technical_score": entry.get("technical_score", 0.0) or 0.0,
                "policy_score": entry.get("policy_score", 0.0) or 0.0,
                "fund_flow_score": entry.get("fund_flow_score", 0.0) or 0.0,
            },
            evidence_needed=list(_missing_evidence),
        )
        _stub.compute_attribution()
        result = {
            "symbol": entry.get("symbol", ""),
            "name": entry.get("name", ""),
            "plan_action": action,
            "keep_observing": action != "REMOVE_FROM_WATCH",
            "reason": "",
            "tier": entry.get("tier", ""),
            "composite_score": entry.get("composite_score", 0.0),
            "strategy_tags": _strategy_tags,
            "trigger_price": entry.get("trigger_price"),
            "invalid_price": entry.get("invalid_price"),
            "observe_state": _observe_state,
            "candidate_type": _candidate_type,  # [TF-REVIEW-003]
            "hit_type": _stub.hit_type,  # [TF-REVIEW-003]
            "tomorrow_focus": _stub.tomorrow_focus,  # [TF-REVIEW-003]
            "downgrade_reason": _stub.downgrade_reason,  # [TF-REVIEW-003]
            "evidence_needed": list(_stub.evidence_needed),  # [TF-REVIEW-003]
        }
        if action == "REMOVE_FROM_WATCH":
            result["reason"] = "已标记移除"
        elif entry.get("trigger_price"):
            result["reason"] = f"继续观察，关注触发价 {entry['trigger_price']}"
        else:
            result["reason"] = "继续观察"
        # [TF-REVIEW-005] review_observe_paper_attribution — enrich with observe+paper
        _sym = normalize_tradeflow_symbol(entry.get("symbol", ""))
        _enrich_review_result_with_attribution(
            result,
            _signal_map.get(_sym),
            _paper_map.get(_sym, ""),
        )
        results.append(result)

    # [TF-REVIEW-005] review_observe_paper_attribution — "今天实际值得复盘的票"
    from tradingagents.tradeflow.post_market_review import compute_today_review_focus
    _today_focus = compute_today_review_focus(results)

    # [TF-REVIEW-002] review_date_mapping — include plan_date/effective_trade_date
    return {
        "status": "ok",
        "trade_date": trade_date,
        "reviewed_at": datetime.now().isoformat(),
        "results": results,
        "summary_agg": _compute_summary(candidates),
        "today_review_focus": _today_focus,  # [TF-REVIEW-005] review_observe_paper_attribution
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

    # [TF-OBS-003] observe_paper_sync — sync triggered/invalidated into paper ledger
    paper_sync = _sync_paper_from_observe(result.details, tf_db)

    # [TF-OBS-005] observe_date_semantics — surface cross-date plan provenance
    _sem = _resolve_observe_date_semantics(tf_db, trade_date)

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
        "paper_synced": paper_sync["synced"],            # [TF-OBS-003] observe_paper_sync
        "paper_pending": paper_sync["pending"],           # [TF-OBS-003]
        "paper_invalidated": paper_sync["invalidated"],   # [TF-OBS-003]
        "plan_date": _sem["plan_date"],                          # [TF-OBS-005]
        "effective_trade_date": _sem["effective_trade_date"],    # [TF-OBS-005]
        "observe_date": _sem["observe_date"],                    # [TF-OBS-005]
        "non_trading_day_plan": _sem["non_trading_day_plan"],    # [TF-OBS-005]
        "next_trading_day_hint": _sem["next_trading_day_hint"],  # [TF-OBS-005]
        "is_view_trading_day": _sem["is_view_trading_day"],      # [TF-OBS-005]
        "runtime_tier_meta": _tradeflow_meta("tradeflow_observe_run"),  # [PERF-001]
    }


# [TF-OBS-003] observe_paper_sync
def _sync_paper_from_observe(details: list, tf_db: str) -> dict:
    """Sync observe results into the paper ledger.

    For each observe detail with a known state, call update_paper_observe_state
    so triggered candidates become 'pending' (awaiting manual confirm) and
    invalidated candidates become 'invalidated'. Candidates not in the paper
    ledger are ignored — no record is created.
    """
    synced = 0
    pending_count = 0
    invalidated_count = 0
    for d in details:
        symbol = d.get("symbol") if isinstance(d, dict) else None
        state = d.get("observe_state") if isinstance(d, dict) else None
        if not symbol or state not in ("TRIGGERED", "INVALIDATED", "WAITING"):
            continue
        try:
            res = update_paper_observe_state(symbol, state, tf_db_path=tf_db)
            if res.get("status") == "ok" and res.get("updated", 0) > 0:
                synced += res["updated"]
                if state == "TRIGGERED":
                    pending_count += 1
                elif state == "INVALIDATED":
                    invalidated_count += 1
        except Exception:
            pass
    return {"synced": synced, "pending": pending_count, "invalidated": invalidated_count}


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

        # [KB-008] research_attention_integration — 注入研报关注度字段。
        # 只读、不调用 LLM、共享一次全库扫描；不改变 tier / action 门禁。
        all_items = _enrich_candidates_with_research_attention(all_items)

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
            "concentration_summary": pool_result.concentration_summary,  # [H-014] mandate_concentration_gate
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
        # [TF-REVIEW-004] review_empty_diagnostics — diagnose the empty reason
        _diag = _diagnose_review_empty_state(trade_date, tf_db_path=tf_db)
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "message": "无候选数据",
            "data_status": ReviewDataStatus.NO_CANDIDATES.value,
            "data_status_message": ReviewDataStatus.NO_CANDIDATES.message_cn,
            "empty_reason": _diag["empty_reason"],
            "empty_reason_message": _diag["empty_reason_message"],
            "suggested_action": _diag["suggested_action"],
            "available_plan_dates": _diag["available_plan_dates"],
            "latest_plan_date": _diag["latest_plan_date"],
            "has_observe_signals": _diag["has_observe_signals"],
            "plan_date": _diag["plan_date"],
            "effective_trade_date": _diag["effective_trade_date"],
            "review_date": _diag["review_date"],
        }

    candidates = candidates_data.get("candidates", [])
    if not candidates:
        # [TF-REVIEW-004] review_empty_diagnostics — diagnose the empty reason
        _diag = _diagnose_review_empty_state(trade_date, tf_db_path=tf_db)
        return {
            "status": "no_data",
            "trade_date": trade_date,
            "message": "候选列表为空",
            "data_status": ReviewDataStatus.NO_CANDIDATES.value,
            "data_status_message": ReviewDataStatus.NO_CANDIDATES.message_cn,
            "empty_reason": _diag["empty_reason"],
            "empty_reason_message": _diag["empty_reason_message"],
            "suggested_action": _diag["suggested_action"],
            "available_plan_dates": _diag["available_plan_dates"],
            "latest_plan_date": _diag["latest_plan_date"],
            "has_observe_signals": _diag["has_observe_signals"],
            "plan_date": _diag["plan_date"],
            "effective_trade_date": _diag["effective_trade_date"],
            "review_date": _diag["review_date"],
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

    # [TF-REVIEW-005] review_observe_paper_attribution — load observe signals + paper
    # ledger and inject into candidate dicts so the persisted report carries the
    # 触发/确认/失效 attribution, not just raw candidate scores.
    _gen_symbols = [normalize_tradeflow_symbol(c.get("symbol", "")) for c in candidates]
    _gen_symbols = [s for s in _gen_symbols if s]
    _gen_signal_map: dict[str, dict] = {}
    _gen_paper_map: dict[str, str] = {}
    _gen_conn = _connect(tf_db)
    if _gen_conn is not None:
        try:
            _gen_signal_map, _gen_paper_map = _load_review_attribution(
                _gen_conn, review_date, symbols=_gen_symbols or None
            )
        finally:
            _gen_conn.close()

    performances = []
    for c in candidates:
        _csym = normalize_tradeflow_symbol(c.get("symbol", ""))
        _csig = _gen_signal_map.get(_csym, {})
        if _csig:
            c["signal_state"] = _csig.get("state", "")
            c["has_signal_for_date"] = True
            c["signal_current_price"] = _csig.get("current_price")
            c["signal_trigger_reason"] = _csig.get("trigger_reason", "")
        if _gen_paper_map.get(_csym):
            c["paper_status"] = _gen_paper_map.get(_csym)
        perf = build_candidate_performance_from_dict(c)
        performances.append(perf)

    summary = run_post_market_review(
        performances,
        candidate_date=resolved_date,
        review_date=review_date,
        plan_date=plan_date,
        effective_trade_date=effective_trade_date,
    )

    report_saved_path = ""
    try:
        report_saved_path = save_review_report(summary)
    except Exception:
        pass

    # [TF-REVIEW-004] track generated report in DB for isolated diagnostics
    try:
        conn = sqlite3.connect(tf_db)
        conn.execute(
            "INSERT OR REPLACE INTO tradeflow_review_reports "
            "(review_date, plan_date, effective_trade_date, generated_at, report_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                review_date,
                plan_date,
                effective_trade_date,
                datetime.now().isoformat(timespec="seconds"),
                report_saved_path or "",
            ),
        )
        conn.commit()
        conn.close()
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
                    "attributions": dict(st.attributions),  # [TF-REVIEW-003]
                }
                for tag, st in summary.strategy_stats.items()
            },
            "tier_stats": summary.tier_stats,
            "common_removal_reasons": summary.common_removal_reasons,
            "suggestions": summary.suggestions,
            # [TF-REVIEW-003] strategy_attribution_review
            "candidate_type_stats": summary.candidate_type_stats,
            "attribution_stats": summary.attribution_stats,
            "next_day_feedback": summary.next_day_feedback,
            # [TF-REVIEW-005] review_observe_paper_attribution
            "today_review_focus": summary.today_review_focus,
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


# [H-013] mandate_topic_heatmap
def get_topic_heatmap(
    as_of: str = "",
    window_days: int = 60,
    tf_db_path: str = "",
    *,
    read_only: bool = False,
) -> dict:
    """Build a topic heatmap from candidate history across the window.

    Queries `tradeflow_candidates` within the trailing `window_days` and
    feeds the rows into the topic heatmap builder.

    When ``read_only`` is True the DB is opened in SQLite read-only mode and
    schema migration (``init_db``) is skipped entirely, so callers like the
    H-016 report CLI / dry-run can never mutate ``tradeflow.db``. A stale or
    empty DB in read-only mode degrades to the ``no_data`` fallback rather
    than raising.
    """
    def _empty_no_data() -> dict:
        from tradingagents.tradeflow.topic_heatmap import build_topic_heatmap
        report = build_topic_heatmap([], as_of=as_of, window_days=window_days)
        return {
            "status": "no_data",
            **report.to_dict(),
            "runtime_tier_meta": _fast_meta,
        }

    _fast_meta = _tradeflow_meta("tradeflow_topic_heatmap")
    conn = _connect(tf_db_path, read_only=read_only)
    if conn is None:
        return _empty_no_data()

    try:
        from datetime import datetime as _dt, timedelta as _td

        cols = _table_columns(conn, "tradeflow_candidates")
        # [H-016] mandate_daily_cli — in read-only mode the table may not exist
        # (or the schema is too stale to query safely). Rather than raising on
        # the SELECT below, fall back to no_data instead of mutating the DB.
        if not cols:
            return _empty_no_data()
        has_eff = "effective_trade_date" in cols

        # [H-013A] mandate_topic_heatmap_fix
        # Use COALESCE(NULLIF(effective_trade_date, ''), trade_date) so old rows
        # that have an empty effective_trade_date but a valid trade_date are not
        # silently dropped from the heatmap.
        if has_eff:
            date_expr = "COALESCE(NULLIF(effective_trade_date, ''), trade_date)"
        else:
            date_expr = "trade_date"

        if not as_of:
            try:
                row = conn.execute(
                    f"SELECT {date_expr} AS d FROM tradeflow_candidates "
                    f"WHERE {date_expr} != '' ORDER BY d DESC LIMIT 1"
                ).fetchone()
                as_of = row["d"] if row else _dt.now().strftime("%Y-%m-%d")
            except Exception:
                as_of = _dt.now().strftime("%Y-%m-%d")

        try:
            as_of_dt = _dt.strptime(as_of, "%Y-%m-%d")
        except ValueError:
            as_of_dt = _dt.now()
        start_date = (as_of_dt - _td(days=window_days)).strftime("%Y-%m-%d")

        # [H-013A] mandate_topic_heatmap_fix — window filter uses the COALESCE expr
        rows = conn.execute(
            f"SELECT * FROM tradeflow_candidates "
            f"WHERE {date_expr} >= ? AND {date_expr} <= ? "
            f"ORDER BY {date_expr} ASC",
            (start_date, as_of),
        ).fetchall()

        candidates: list[dict] = []
        for row in rows:
            item = _row_to_candidate_item(row)
            policy_refs = _parse_json(_rget(row, "policy_evidence_refs_json"), [])
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
                "candidate_type": item.get("candidate_type", ""),
                # [H-013A] mandate_topic_heatmap_fix — legacy rows may have
                # empty effective_trade_date but a valid trade_date. Keep both
                # so build_topic_heatmap() can resolve the candidate date and
                # include the row in heat_curve/window stats.
                "effective_trade_date": item.get("effective_trade_date", "") or _rget(row, "trade_date", ""),
                "trade_date": _rget(row, "trade_date", ""),
                "policy_evidence_refs": policy_refs,
                "overheat_flags": item.get("overheat_flags", []),
                "topic_lifecycle_state": item.get("topic_lifecycle_state", ""),
                "topic_signal_count": item.get("topic_signal_count", 0),
                "topic_last_signal_date": item.get("topic_last_signal_date", ""),
                "blocking_evidence_gaps": item.get("blocking_evidence_gaps", []),
                "watchlist_evidence_gap": item.get("watchlist_evidence_gap", []),
            })

        from tradingagents.tradeflow.topic_heatmap import build_topic_heatmap
        report = build_topic_heatmap(candidates, as_of=as_of, window_days=window_days)

        return {
            "status": "ok",
            **report.to_dict(),
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


# [H-015] mandate_daily_report
def get_mandate_daily_report(
    as_of: str = "",
    window_days: int = 60,
    reports_dir: str = "docs/mandate_daily_reports",
    save_report: bool = False,
) -> dict:
    """Return the latest mandate daily report or generate a read-only preview."""
    _fast_meta = _tradeflow_meta("tradeflow_mandate_daily_report")
    from tradingagents.tradeflow.mandate_daily_report import (
        build_mandate_daily_report,
        load_latest_mandate_daily_report,
        save_mandate_daily_report,
    )

    if not as_of:
        latest = load_latest_mandate_daily_report(reports_dir)
        if latest:
            latest["status"] = "ok"
            latest["runtime_tier_meta"] = _fast_meta
            return latest

    heatmap = get_topic_heatmap(as_of=as_of, window_days=window_days)
    report = build_mandate_daily_report(heatmap, as_of=heatmap.get("as_of") or as_of)
    data = report.to_dict()
    data["status"] = "ok" if heatmap.get("status") != "no_data" else "no_data"
    data["runtime_tier_meta"] = _fast_meta
    if save_report:
        md_path, json_path = save_mandate_daily_report(report, output_dir=reports_dir)
        data["path"] = json_path
        data["markdown_path"] = md_path
        data["source"] = "generated_file"
    return data


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


# [DATA-020] live_sampling_health_ui
def get_live_sampling_report(
    reports_dir: str = "docs/data_source_reports",
) -> dict:
    """Read the latest live-sampling report for the frontend health panel.

    Returns a ``no_data`` envelope when no report exists so the UI can render
    a clean empty state instead of erroring. Never performs live network
    calls — it only reads the JSON sidecar written by
    :func:`save_live_sampling_report`.
    """
    _fast_meta = _tradeflow_meta("tradeflow_live_sampling")

    from tradingagents.dataflows.live_source_sampling import (
        load_latest_live_sampling_report,
    )

    data = load_latest_live_sampling_report(reports_dir)

    if not data:
        return {
            "status": "no_data",
            "has_report": False,
            "report_date": "",
            "generated_at": "",
            "env_gated": True,
            "samples": [],
            "results": [],
            "summary": {
                "total_checks": 0,
                "status_counts": {},
                "green_count": 0,
                "yellow_count": 0,
                "red_count": 0,
                "skipped_count": 0,
                "skipped_only": False,
                "fallback_triggered_count": 0,
                "all_green": False,
                "has_failures": False,
                "has_warnings": False,
                "by_data_type": {},
                "overall_status": "no_data",
            },
            "runtime_tier_meta": _fast_meta,
        }

    summary = data.get("summary") or {}
    if not summary.get("overall_status"):
        summary["overall_status"] = "no_data"

    return {
        "status": "ok",
        "has_report": True,
        "report_date": data.get("report_date", ""),
        "generated_at": data.get("generated_at", ""),
        "env_gated": bool(data.get("env_gated", True)),
        "samples": data.get("samples", []),
        "results": data.get("results", []),
        "summary": summary,
        "runtime_tier_meta": _fast_meta,
    }


# [TRACK-001] observation_warehouse
# ──────────────────────────────────────────────────────────────────────────────
# Observation warehouse: container for symbols the user wants to buy but is
# waiting on price / event / capital confirmation before entry. Lives in
# tradeflow.db so it can never pollute real holdings (ImportedPortfolioPositionDB
# in tradingagents.db). Does NOT trigger TA / LLM and never sends notifications.
# ──────────────────────────────────────────────────────────────────────────────

OBSERVATION_STATUS_WATCHING = "watching"
OBSERVATION_STATUS_NEAR_ENTRY = "near_entry"
OBSERVATION_STATUS_IN_ENTRY_ZONE = "in_entry_zone"
OBSERVATION_STATUS_TA_REQUIRED = "ta_required"
OBSERVATION_STATUS_MISSED_ENTRY = "missed_entry"  # [TRACK-004] observation_state_engine
OBSERVATION_STATUS_ENTERED = "entered"
OBSERVATION_STATUS_INVALIDATED = "invalidated"
OBSERVATION_STATUS_REMOVED = "removed"
ALLOWED_OBSERVATION_STATUSES = frozenset({
    OBSERVATION_STATUS_WATCHING,
    OBSERVATION_STATUS_NEAR_ENTRY,
    OBSERVATION_STATUS_IN_ENTRY_ZONE,
    OBSERVATION_STATUS_TA_REQUIRED,
    OBSERVATION_STATUS_MISSED_ENTRY,
    OBSERVATION_STATUS_ENTERED,
    OBSERVATION_STATUS_INVALIDATED,
    OBSERVATION_STATUS_REMOVED,
})
ALLOWED_OBSERVATION_HORIZONS = frozenset({"intraday", "short", "mid"})
ALLOWED_OBSERVATION_SOURCES = frozenset({
    "manual",
    "tradeflow",
    "ta",
    "investment_controller",
})
# Statuses that are still actively tracked on the board (not terminal).
# missed_entry 仍属活跃：可重新 watching 或被 invalidated，故不计入终态。
_ACTIVE_OBSERVATION_STATUSES = frozenset({
    OBSERVATION_STATUS_WATCHING,
    OBSERVATION_STATUS_NEAR_ENTRY,
    OBSERVATION_STATUS_IN_ENTRY_ZONE,
    OBSERVATION_STATUS_TA_REQUIRED,
    OBSERVATION_STATUS_MISSED_ENTRY,
    OBSERVATION_STATUS_ENTERED,
})


def _empty_observation_item(symbol: str = "", name: str = "") -> dict:
    """Default observation item dict used for no-data fallbacks.

    Note: numeric price fields default to ``0.0`` (NOT ``None``) so that
    boundary values like ``entry_low=0`` never get rendered as N/A downstream.
    """
    return {
        "id": 0,
        "symbol": symbol,
        "name": name,
        "status": OBSERVATION_STATUS_WATCHING,
        "entry_low": 0.0,
        "entry_high": 0.0,
        "trigger_price": 0.0,
        "invalid_price": 0.0,
        "horizon": "short",
        "source": "manual",
        "reason": "",
        "priority": 0,
        "notes": "",
        "created_at": "",
        "updated_at": "",
        "last_reviewed_at": "",
        # [TRACK-006] add_to_observation — provenance & history fields
        "strategy_tags": [],
        "score": 0.0,
        "action_label": "",
        "research_direction": "",
        "source_history": [],
    }


def _parse_observation_json_field(raw: Any, default: Any) -> Any:
    """Best-effort JSON parse for observation provenance columns.

    Older rows (or DBs upgraded in-place) may carry NULL / empty strings;
    never raise — fall back to ``default`` so the API stays stable.
    """
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        import json as _json
        return _json.loads(raw)
    except (ValueError, TypeError):
        return default


def _row_to_observation_item(row: sqlite3.Row) -> dict:
    """Convert a tradeflow_observation_items row to a dict.

    Preserves ``0.0`` price boundary values — never coerces 0 to None.
    """
    symbol = normalize_tradeflow_symbol(_rget(row, "symbol", ""))
    raw_name = _rget(row, "name", "")
    name = resolve_tradeflow_name(symbol, raw_name)

    def _num(key: str) -> float:
        # Read directly from the row so 0.0 is preserved (no `or` fallback).
        try:
            val = row[key]
        except (KeyError, IndexError):
            return 0.0
        if val is None:
            return 0.0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    # [TRACK-006] add_to_observation — read provenance columns defensively
    strategy_tags = _parse_observation_json_field(
        _rget(row, "strategy_tags_json", "[]"), []
    )
    source_history = _parse_observation_json_field(
        _rget(row, "source_history_json", "[]"), []
    )
    score_val = _num("score")

    return {
        "id": _rget(row, "id", 0),
        "symbol": symbol,
        "name": name,
        "status": _rget(row, "status", OBSERVATION_STATUS_WATCHING) or OBSERVATION_STATUS_WATCHING,
        "entry_low": _num("entry_low"),
        "entry_high": _num("entry_high"),
        "trigger_price": _num("trigger_price"),
        "invalid_price": _num("invalid_price"),
        "horizon": _rget(row, "horizon", "short") or "short",
        "source": _rget(row, "source", "manual") or "manual",
        "reason": _rget(row, "reason", "") or "",
        "priority": int(_rget(row, "priority", 0) or 0),
        "notes": _rget(row, "notes", "") or "",
        "created_at": _rget(row, "created_at", "") or "",
        "updated_at": _rget(row, "updated_at", "") or "",
        "last_reviewed_at": _rget(row, "last_reviewed_at", "") or "",
        # [TRACK-006] add_to_observation — provenance & history fields
        "strategy_tags": strategy_tags if isinstance(strategy_tags, list) else [],
        "score": score_val,
        "action_label": _rget(row, "action_label", "") or "",
        "research_direction": _rget(row, "research_direction", "") or "",
        "source_history": source_history if isinstance(source_history, list) else [],
    }


def _observation_summary(items: list[dict]) -> dict:
    """Aggregate counts by status, excluding removed/invalidated from active."""
    summary = {status: 0 for status in ALLOWED_OBSERVATION_STATUSES}
    summary["total"] = len(items)
    summary["active"] = 0
    for item in items:
        status = item.get("status") or OBSERVATION_STATUS_WATCHING
        if status in summary:
            summary[status] += 1
        if status in _ACTIVE_OBSERVATION_STATUSES:
            summary["active"] += 1
    return summary


def _validate_observation_status(status: str) -> str:
    if status not in ALLOWED_OBSERVATION_STATUSES:
        raise ValueError(
            f"非法观察仓状态: {status}，允许值: {sorted(ALLOWED_OBSERVATION_STATUSES)}"
        )
    return status


def _validate_observation_horizon(horizon: str) -> str:
    if horizon not in ALLOWED_OBSERVATION_HORIZONS:
        raise ValueError(
            f"非法 horizon: {horizon}，允许值: {sorted(ALLOWED_OBSERVATION_HORIZONS)}"
        )
    return horizon


def _validate_observation_source(source: str) -> str:
    if source not in ALLOWED_OBSERVATION_SOURCES:
        raise ValueError(
            f"非法 source: {source}，允许值: {sorted(ALLOWED_OBSERVATION_SOURCES)}"
        )
    return source


def _coerce_price(value: Any) -> float:
    """Coerce a price input to float, treating None/blank as 0.0.

    Boundary note: explicit 0 / 0.0 / "0" all stay 0.0 — they are valid
    placeholders meaning "未设定" and must NOT be displayed as N/A.
    """
    if value is None:
        return 0.0
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0.0
        try:
            return float(s)
        except ValueError:
            raise ValueError(f"非法价格数值: {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"非法价格数值: {value!r}")


def get_observation_items(
    status: Optional[str] = None,
    include_removed: bool = False,
    tf_db_path: str = "",
) -> dict:
    """List observation warehouse items.

    ``include_removed=False`` (default) hides items whose status is
    ``removed`` from the default board view. ``invalidated`` items are always
    returned (they are still useful for post-market review).
    """
    _fast_meta = _tradeflow_meta("tradeflow_observation_items")
    if status is not None:
        _validate_observation_status(status)

    conn = _connect(tf_db_path)
    if conn is None:
        return {
            "status": "no_data",
            "items": [],
            "summary": _observation_summary([]),
            "runtime_tier_meta": _fast_meta,
        }

    try:
        query = "SELECT * FROM tradeflow_observation_items"
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        elif not include_removed:
            clauses.append("status != ?")
            params.append(OBSERVATION_STATUS_REMOVED)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY priority DESC, created_at DESC, id DESC"

        rows = conn.execute(query, params).fetchall()
        items = [_row_to_observation_item(r) for r in rows]
        return {
            "status": "ok",
            "items": items,
            "summary": _observation_summary(items),
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


def create_observation_item(
    symbol: str,
    name: str = "",
    status: str = OBSERVATION_STATUS_WATCHING,
    entry_low: Any = 0.0,
    entry_high: Any = 0.0,
    trigger_price: Any = 0.0,
    invalid_price: Any = 0.0,
    horizon: str = "short",
    source: str = "manual",
    reason: str = "",
    priority: Any = 0,
    notes: str = "",
    strategy_tags: list[str] | None = None,  # [TRACK-006] add_to_observation
    score: Any = 0.0,  # [TRACK-006] add_to_observation
    action_label: str = "",  # [TRACK-006] add_to_observation
    research_direction: str = "",  # [TRACK-006] add_to_observation
    source_history: list[dict] | None = None,  # [TRACK-006] add_to_observation
    tf_db_path: str = "",
) -> dict:
    """Create a new observation warehouse item.

    Returns ``status="duplicate"`` if an item with the same (normalized)
    symbol already exists (the caller may want ``update`` instead).
    """
    _fast_meta = _tradeflow_meta("tradeflow_observation_items")
    norm_symbol = normalize_tradeflow_symbol(symbol)
    if not norm_symbol:
        return {"status": "error", "message": "symbol 不能为空", "runtime_tier_meta": _fast_meta}

    _validate_observation_status(status)
    _validate_observation_horizon(horizon)
    _validate_observation_source(source)

    entry_low_f = _coerce_price(entry_low)
    entry_high_f = _coerce_price(entry_high)
    trigger_price_f = _coerce_price(trigger_price)
    invalid_price_f = _coerce_price(invalid_price)
    try:
        priority_i = int(priority)
    except (TypeError, ValueError):
        priority_i = 0
    try:
        score_f = float(score)
    except (TypeError, ValueError):
        score_f = 0.0

    # [TRACK-006] add_to_observation — serialize provenance lists defensively
    import json as _json
    tags_json = _json.dumps(strategy_tags or [], ensure_ascii=False)
    history_json = _json.dumps(source_history or [], ensure_ascii=False)

    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available", "runtime_tier_meta": _fast_meta}

    try:
        existing = conn.execute(
            "SELECT id FROM tradeflow_observation_items WHERE symbol = ?",
            (norm_symbol,),
        ).fetchone()
        if existing is not None:
            return {
                "status": "duplicate",
                "message": f"{norm_symbol} 已在观察仓中，请使用更新接口",
                "symbol": norm_symbol,
                "item_id": existing["id"],
                "runtime_tier_meta": _fast_meta,
            }

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resolved_name = resolve_tradeflow_name(norm_symbol, name)
        conn.execute(
            """
            INSERT INTO tradeflow_observation_items
                (symbol, name, status, entry_low, entry_high, trigger_price,
                 invalid_price, horizon, source, reason, priority, notes,
                 created_at, updated_at, last_reviewed_at,
                 strategy_tags_json, score, action_label, research_direction,
                 source_history_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                norm_symbol, resolved_name, status,
                entry_low_f, entry_high_f, trigger_price_f, invalid_price_f,
                horizon, source, reason, priority_i, notes,
                now, now, now,
                tags_json, score_f, action_label, research_direction,
                history_json,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM tradeflow_observation_items WHERE symbol = ?",
            (norm_symbol,),
        ).fetchone()
        item = _row_to_observation_item(row)
        return {
            "status": "ok",
            "message": f"{norm_symbol} 已加入观察仓",
            "item": item,
            "item_id": item["id"],
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


def update_observation_item(
    item_id: int,
    *,
    name: Optional[str] = None,
    status: Optional[str] = None,
    entry_low: Any = None,
    entry_high: Any = None,
    trigger_price: Any = None,
    invalid_price: Any = None,
    horizon: Optional[str] = None,
    source: Optional[str] = None,
    reason: Optional[str] = None,
    priority: Any = None,
    notes: Optional[str] = None,
    touch_last_reviewed: bool = False,
    # [TRACK-006] add_to_observation — provenance field updates
    strategy_tags: Optional[list[str]] = None,
    score: Any = None,
    action_label: Optional[str] = None,
    research_direction: Optional[str] = None,
    append_source_history: Optional[list[dict]] = None,
    tf_db_path: str = "",
) -> dict:
    """Partially update an observation item. Only provided fields are changed.

    Returns ``status="not_found"`` if ``item_id`` does not exist.

    [TRACK-006] notes semantics: caller-supplied ``notes`` ALWAYS overwrites
    the stored value (preserving the existing TRACK-001 contract). Callers
    that need preserve-user-notes semantics must read first, merge, and pass
    the merged string — or use ``add_candidate_to_observation`` /
    ``add_ta_report_to_observation`` which never clobber user notes.
    ``append_source_history`` ADDS entries to the source_history_json list
    (it never replaces); pass an empty list to no-op.
    """
    _fast_meta = _tradeflow_meta("tradeflow_observation_items")
    if status is not None:
        _validate_observation_status(status)
    if horizon is not None:
        _validate_observation_horizon(horizon)
    if source is not None:
        _validate_observation_source(source)

    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available", "runtime_tier_meta": _fast_meta}

    try:
        row = conn.execute(
            "SELECT * FROM tradeflow_observation_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return {"status": "not_found", "message": f"item_id={item_id} 不存在", "runtime_tier_meta": _fast_meta}

        current = _row_to_observation_item(row)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        updates: dict[str, Any] = {"updated_at": now}
        if name is not None:
            updates["name"] = resolve_tradeflow_name(current["symbol"], name)
        if status is not None:
            updates["status"] = status
        if entry_low is not None:
            updates["entry_low"] = _coerce_price(entry_low)
        if entry_high is not None:
            updates["entry_high"] = _coerce_price(entry_high)
        if trigger_price is not None:
            updates["trigger_price"] = _coerce_price(trigger_price)
        if invalid_price is not None:
            updates["invalid_price"] = _coerce_price(invalid_price)
        if horizon is not None:
            updates["horizon"] = horizon
        if source is not None:
            updates["source"] = source
        if reason is not None:
            updates["reason"] = reason
        if priority is not None:
            try:
                updates["priority"] = int(priority)
            except (TypeError, ValueError):
                updates["priority"] = 0
        if notes is not None:
            updates["notes"] = notes
        if touch_last_reviewed:
            updates["last_reviewed_at"] = now
        # [TRACK-006] add_to_observation — provenance field updates
        if strategy_tags is not None:
            import json as _json
            updates["strategy_tags_json"] = _json.dumps(strategy_tags, ensure_ascii=False)
        if score is not None:
            try:
                updates["score"] = float(score)
            except (TypeError, ValueError):
                updates["score"] = 0.0
        if action_label is not None:
            updates["action_label"] = action_label
        if research_direction is not None:
            updates["research_direction"] = research_direction
        if append_source_history:
            import json as _json
            merged = list(current.get("source_history") or [])
            for entry in append_source_history:
                if isinstance(entry, dict):
                    merged.append(entry)
            updates["source_history_json"] = _json.dumps(merged, ensure_ascii=False)

        set_clause = ", ".join(f"{col} = ?" for col in updates.keys())
        params: list[Any] = list(updates.values()) + [item_id]
        conn.execute(
            f"UPDATE tradeflow_observation_items SET {set_clause} WHERE id = ?",
            params,
        )
        conn.commit()

        new_row = conn.execute(
            "SELECT * FROM tradeflow_observation_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        item = _row_to_observation_item(new_row)
        return {
            "status": "ok",
            "message": f"{item['symbol']} 已更新",
            "item": item,
            "item_id": item_id,
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


def mark_observation_item_status(
    item_id: int,
    status: str,
    note: Optional[str] = None,
    tf_db_path: str = "",
) -> dict:
    """Transition an observation item to ``invalidated`` / ``removed`` / etc.

    Convenience wrapper around ``update_observation_item`` for state-machine
    transitions. When ``note`` is provided it appends to existing notes.
    """
    _validate_observation_status(status)
    update_kwargs: dict[str, Any] = {"status": status, "touch_last_reviewed": True}
    if note is not None:
        update_kwargs["notes"] = note
    return update_observation_item(item_id, tf_db_path=tf_db_path, **update_kwargs)


def bulk_upsert_observation_items(
    items: list[dict[str, Any]],
    tf_db_path: str = "",
) -> dict:
    """Upsert a batch of observation items keyed by normalized symbol.

    Each item must contain at least ``symbol``. Unknown keys are ignored.
    Returns counts of created / updated / unchanged / errored items.

    [TRACK-006] add_to_observation — notes preservation:
    By default, existing user notes are NEVER overwritten on update; only an
    explicit ``force_overwrite_notes=True`` on the entry will replace them.
    This protects manual annotations when investment-controller / scheduler
    upserts the same symbol repeatedly. Empty incoming notes also never
    clobber existing notes.
    """
    _fast_meta = _tradeflow_meta("tradeflow_observation_items")
    if not isinstance(items, list):
        return {"status": "error", "message": "items 必须为列表", "runtime_tier_meta": _fast_meta}

    created: list[int] = []
    updated: list[int] = []
    errored: list[dict[str, Any]] = []

    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available", "runtime_tier_meta": _fast_meta}

    try:
        import json as _json
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for raw in items:
            try:
                symbol = normalize_tradeflow_symbol(raw.get("symbol", ""))
                if not symbol:
                    raise ValueError("symbol 不能为空")
                status = raw.get("status") or OBSERVATION_STATUS_WATCHING
                horizon = raw.get("horizon") or "short"
                source = raw.get("source") or "manual"
                _validate_observation_status(status)
                _validate_observation_horizon(horizon)
                _validate_observation_source(source)

                force_overwrite_notes = bool(raw.get("force_overwrite_notes", False))  # [TRACK-006]
                incoming_notes = raw.get("notes", "") or ""

                payload = {
                    "name": resolve_tradeflow_name(symbol, raw.get("name", "")),
                    "status": status,
                    "entry_low": _coerce_price(raw.get("entry_low", 0.0)),
                    "entry_high": _coerce_price(raw.get("entry_high", 0.0)),
                    "trigger_price": _coerce_price(raw.get("trigger_price", 0.0)),
                    "invalid_price": _coerce_price(raw.get("invalid_price", 0.0)),
                    "horizon": horizon,
                    "source": source,
                    "reason": raw.get("reason", "") or "",
                    "priority": int(raw.get("priority", 0) or 0),
                    "notes": incoming_notes,
                }

                # [TRACK-006] add_to_observation — provenance fields (optional)
                strategy_tags = raw.get("strategy_tags") or []
                if not isinstance(strategy_tags, list):
                    strategy_tags = []
                payload["strategy_tags_json"] = _json.dumps(strategy_tags, ensure_ascii=False)
                try:
                    payload["score"] = float(raw.get("score", 0.0) or 0.0)
                except (TypeError, ValueError):
                    payload["score"] = 0.0
                payload["action_label"] = raw.get("action_label", "") or ""
                payload["research_direction"] = raw.get("research_direction", "") or ""

                existing = conn.execute(
                    "SELECT id, notes FROM tradeflow_observation_items WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO tradeflow_observation_items
                            (symbol, name, status, entry_low, entry_high, trigger_price,
                             invalid_price, horizon, source, reason, priority, notes,
                             created_at, updated_at, last_reviewed_at,
                             strategy_tags_json, score, action_label, research_direction,
                             source_history_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol, payload["name"], payload["status"],
                            payload["entry_low"], payload["entry_high"],
                            payload["trigger_price"], payload["invalid_price"],
                            payload["horizon"], payload["source"], payload["reason"],
                            payload["priority"], payload["notes"], now, now, now,
                            payload["strategy_tags_json"], payload["score"],
                            payload["action_label"], payload["research_direction"],
                            "[]",
                        ),
                    )
                    created.append(symbol)
                else:
                    # [TRACK-006] add_to_observation — preserve user notes
                    # unless caller explicitly forces overwrite AND incoming
                    # notes are non-empty. Existing-empty + incoming-empty
                    # stays empty; existing-non-empty + incoming-empty keeps
                    # the existing value.
                    existing_notes = existing["notes"] or ""
                    if force_overwrite_notes and incoming_notes:
                        final_notes = incoming_notes
                    elif not existing_notes and incoming_notes:
                        # Write into empty slot — this is not "overwriting"
                        # user content, just persisting the new value.
                        final_notes = incoming_notes
                    else:
                        final_notes = existing_notes

                    set_parts = [
                        "name = ?", "status = ?", "entry_low = ?", "entry_high = ?",
                        "trigger_price = ?", "invalid_price = ?", "horizon = ?",
                        "source = ?", "reason = ?", "priority = ?", "notes = ?",
                        "strategy_tags_json = ?", "score = ?",
                        "action_label = ?", "research_direction = ?",
                        "updated_at = ?",
                    ]
                    params = [
                        payload["name"], payload["status"], payload["entry_low"],
                        payload["entry_high"], payload["trigger_price"],
                        payload["invalid_price"], payload["horizon"], payload["source"],
                        payload["reason"], payload["priority"], final_notes,
                        payload["strategy_tags_json"], payload["score"],
                        payload["action_label"], payload["research_direction"], now,
                        existing["id"],
                    ]
                    conn.execute(
                        f"UPDATE tradeflow_observation_items SET {', '.join(set_parts)} WHERE id = ?",
                        params,
                    )
                    updated.append(symbol)
            except Exception as exc:
                errored.append({
                    "symbol": raw.get("symbol", "") if isinstance(raw, dict) else "",
                    "reason": str(exc),
                })

        conn.commit()
        return {
            "status": "ok" if not errored else "partial",
            "created": created,
            "updated": updated,
            "errored": errored,
            "created_count": len(created),
            "updated_count": len(updated),
            "errored_count": len(errored),
            "runtime_tier_meta": _fast_meta,
        }
    finally:
        conn.close()


# [TRACK-006] add_to_observation
# ──────────────────────────────────────────────────────────────────────────────
# One-click add from TradeFlow candidate / TA report to the observation
# warehouse. Both helpers:
#   1. Normalize the symbol (601689 -> 601689.SH etc.).
#   2. Create a fresh item if none exists, otherwise UPDATE — never returns
#      "duplicate" because the user explicitly clicked the add button.
#   3. PRESERVE existing user notes — auto-generated context lands in the
#      structured ``reason`` / ``action_label`` / ``research_direction``
#      fields instead. Only an explicit ``force_overwrite_notes=True`` from
#      the caller may replace user notes.
#   4. Append an entry to ``source_history_json`` recording the previous
#      source so the audit trail is preserved across re-adds.
# Neither helper triggers TA / LLM or sends notifications.
# ──────────────────────────────────────────────────────────────────────────────

# Strong-action words forbidden in any auto-generated reason / message that
# surfaces to the user (mirrors TRACK-001 / TRACK-004 policy).
_OBSERVATION_FORBIDDEN_WORDS = (
    "立即买入", "重仓买入", "立即清仓", "满仓", "梭哈", "全仓",
    "必涨", "必跌", "稳赚", "保本",
)


def _scrub_observation_text(text: str) -> str:
    """Strip forbidden strong-action words from auto-generated text."""
    if not text:
        return ""
    cleaned = text
    for word in _OBSERVATION_FORBIDDEN_WORDS:
        cleaned = cleaned.replace(word, "**")
    return cleaned


def _append_source_history_entry(
    current_history: list[dict],
    *,
    source: str,
    as_of: str,
    via: str,
    reason: str,
) -> list[dict]:
    """Append a provenance entry to source_history_json."""
    new_entry = {
        "source": source,
        "as_of": as_of,
        "via": via,
        "reason": _scrub_observation_text(reason or "")[:240],
    }
    return list(current_history or []) + [new_entry]


def _resolve_observation_horizon_from_candidate(candidate: dict) -> str:
    """Derive a valid observation horizon from a candidate dict.

    POLICY_AMBUSH / POLICY_CONFIRM candidates are typically mid-line; others
    default to short. Anything that fails validation falls back to ``short``.
    """
    candidate_type = (candidate.get("candidate_type") or "").upper()
    raw_horizon = "mid" if candidate_type in {"POLICY_AMBUSH", "POLICY_CONFIRM"} else "short"
    try:
        _validate_observation_horizon(raw_horizon)
        return raw_horizon
    except ValueError:
        return "short"


# [TRACK-006] add_to_observation
def add_candidate_to_observation(
    candidate: dict[str, Any],
    *,
    via: str = "candidate_drawer",
    force_overwrite_notes: bool = False,
    extra_notes: str = "",
    tf_db_path: str = "",
) -> dict:
    """Add (or refresh) an observation item from a TradeFlow candidate dict.

    Auto-populates: strategy_tags, trigger_price, invalid_price, score,
    why_selected (-> reason), entry_low/entry_high (from support_price),
    horizon (from candidate_type), source="tradeflow".

    Behaviour on duplicate symbol:
      - Item is UPDATED, never rejected.
      - ``source`` is flipped to ``tradeflow`` and the previous source is
        pushed onto ``source_history_json``.
      - Existing user notes are PRESERVED unless ``force_overwrite_notes`` is
        True AND ``extra_notes`` is non-empty.
      - If ``extra_notes`` is non-empty and existing notes are empty, the new
        notes are written (no overwrite risk).
    """
    _fast_meta = _tradeflow_meta("tradeflow_observation_items")
    raw_symbol = candidate.get("symbol", "") if isinstance(candidate, dict) else ""
    norm_symbol = normalize_tradeflow_symbol(str(raw_symbol))
    if not norm_symbol:
        return {"status": "error", "message": "candidate.symbol 不能为空", "runtime_tier_meta": _fast_meta}

    name = candidate.get("name", "") or ""
    trigger_price = candidate.get("trigger_price") or 0.0
    invalid_price = candidate.get("invalid_price") or 0.0
    support_price = candidate.get("support_price") or 0.0
    composite_score = candidate.get("composite_score")
    if composite_score is None or composite_score == 0:
        composite_score = candidate.get("mandate_score") or candidate.get("score") or 0.0
    strategy_tags = candidate.get("strategy_tags") or []
    if not isinstance(strategy_tags, list):
        strategy_tags = []
    why_selected = candidate.get("reason") or candidate.get("tier_reason") or candidate.get("why_deep_ta") or ""
    horizon = _resolve_observation_horizon_from_candidate(candidate)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    reason_text = _scrub_observation_text(
        f"TradeFlow 候选 · 评分 {float(composite_score or 0):.2f}"
        + (f" · {why_selected}" if why_selected else "")
    )

    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available", "runtime_tier_meta": _fast_meta}

    import json as _json
    try:
        existing_row = conn.execute(
            "SELECT * FROM tradeflow_observation_items WHERE symbol = ?",
            (norm_symbol,),
        ).fetchone()
        if existing_row is None:
            create_result = create_observation_item(
                symbol=norm_symbol,
                name=name,
                entry_low=float(support_price or 0.0),
                entry_high=0.0,
                trigger_price=float(trigger_price or 0.0),
                invalid_price=float(invalid_price or 0.0),
                horizon=horizon,
                source="tradeflow",
                reason=reason_text,
                priority=int(candidate.get("ta_budget_priority") or 0),
                notes=extra_notes or "",
                strategy_tags=strategy_tags,
                score=float(composite_score or 0.0),
                tf_db_path=tf_db_path,
            )
            if create_result.get("status") not in {"ok", "duplicate"}:
                return create_result
            # Tag the create result so callers can tell create vs update apart.
            create_result["action"] = "created"
            return create_result

        current = _row_to_observation_item(existing_row)
        existing_notes = current.get("notes", "") or ""
        if force_overwrite_notes and extra_notes:
            final_notes = extra_notes
        elif not existing_notes and extra_notes:
            final_notes = extra_notes
        else:
            final_notes = existing_notes

        # [TRACK-006] add_to_observation — only pass the NEW history entry;
        # update_observation_item handles the append-merge internally.
        new_history_entry = {
            "source": current.get("source") or "manual",
            "as_of": now,
            "via": via,
            "reason": _scrub_observation_text(
                f"切前来源 · {(current.get('reason') or '')[:120]}"
            ),
        }

        update_result = update_observation_item(
            current["id"],
            name=name,
            entry_low=float(support_price or 0.0),
            trigger_price=float(trigger_price or 0.0),
            invalid_price=float(invalid_price or 0.0),
            horizon=horizon,
            source="tradeflow",
            reason=reason_text,
            priority=int(candidate.get("ta_budget_priority") or 0),
            notes=final_notes,
            strategy_tags=strategy_tags,
            score=float(composite_score or 0.0),
            append_source_history=[new_history_entry],
            tf_db_path=tf_db_path,
        )
        if update_result.get("status") != "ok":
            return update_result
        update_result["action"] = "updated"
        return update_result
    finally:
        conn.close()


# [TRACK-006] add_to_observation
def add_ta_report_to_observation(
    report: dict[str, Any],
    *,
    via: str = "analysis_page",
    force_overwrite_notes: bool = False,
    extra_notes: str = "",
    tf_db_path: str = "",
) -> dict:
    """Add (or refresh) an observation item from a TA report dict.

    Auto-populates: action_label, research_direction, key support/stop/target
    (entry_low=stop_loss, entry_high=target_price, trigger_price=target_price,
    invalid_price=stop_loss_price), source="ta".

    Behaviour on duplicate symbol mirrors ``add_candidate_to_observation``:
    UPDATE never reject, source flipped to ``ta``, previous source pushed
    onto history, user notes preserved unless explicitly overwritten.
    """
    _fast_meta = _tradeflow_meta("tradeflow_observation_items")
    raw_symbol = report.get("symbol", "") if isinstance(report, dict) else ""
    norm_symbol = normalize_tradeflow_symbol(str(raw_symbol))
    if not norm_symbol:
        return {"status": "error", "message": "report.symbol 不能为空", "runtime_tier_meta": _fast_meta}

    name = report.get("name", "") or ""
    action_label = report.get("action_label", "") or report.get("execution_action", "") or ""
    research_direction = report.get("research_direction", "") or report.get("direction", "") or ""
    target_price = report.get("target_price") or 0.0
    stop_loss_price = report.get("stop_loss_price") or 0.0

    # Map TA report's key prices onto observation schema:
    #   entry_low   = stop_loss_price  (lower bound of the entry zone)
    #   entry_high  = target_price     (upper bound / first target)
    #   trigger     = target_price     (price that confirms the thesis)
    #   invalid     = stop_loss_price  (price that invalidates the thesis)
    entry_low = float(stop_loss_price or 0.0)
    entry_high = float(target_price or 0.0)
    trigger_price = float(target_price or 0.0)
    invalid_price = float(stop_loss_price or 0.0)

    horizon = "mid"
    try:
        _validate_observation_horizon(horizon)
    except ValueError:
        horizon = "short"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    reason_text = _scrub_observation_text(
        f"TA 报告 · 方向 {research_direction or '未给'} · 动作 {action_label or '未给'}"
        + (f" · 目标 {entry_high:.2f} / 止损 {entry_low:.2f}" if (entry_high or entry_low) else "")
    )

    conn = _connect(tf_db_path)
    if conn is None:
        return {"status": "no_data", "message": "TradeFlow DB not available", "runtime_tier_meta": _fast_meta}

    try:
        existing_row = conn.execute(
            "SELECT * FROM tradeflow_observation_items WHERE symbol = ?",
            (norm_symbol,),
        ).fetchone()
        if existing_row is None:
            create_result = create_observation_item(
                symbol=norm_symbol,
                name=name,
                entry_low=entry_low,
                entry_high=entry_high,
                trigger_price=trigger_price,
                invalid_price=invalid_price,
                horizon=horizon,
                source="ta",
                reason=reason_text,
                notes=extra_notes or "",
                action_label=action_label,
                research_direction=research_direction,
                tf_db_path=tf_db_path,
            )
            if create_result.get("status") not in {"ok", "duplicate"}:
                return create_result
            create_result["action"] = "created"
            return create_result

        current = _row_to_observation_item(existing_row)
        existing_notes = current.get("notes", "") or ""
        if force_overwrite_notes and extra_notes:
            final_notes = extra_notes
        elif not existing_notes and extra_notes:
            final_notes = extra_notes
        else:
            final_notes = existing_notes

        # [TRACK-006] add_to_observation — only pass the NEW history entry.
        new_history_entry = {
            "source": current.get("source") or "manual",
            "as_of": now,
            "via": via,
            "reason": _scrub_observation_text(
                f"切前来源 · {(current.get('reason') or '')[:120]}"
            ),
        }

        update_result = update_observation_item(
            current["id"],
            name=name,
            entry_low=entry_low,
            entry_high=entry_high,
            trigger_price=trigger_price,
            invalid_price=invalid_price,
            horizon=horizon,
            source="ta",
            reason=reason_text,
            notes=final_notes,
            action_label=action_label,
            research_direction=research_direction,
            append_source_history=[new_history_entry],
            tf_db_path=tf_db_path,
        )
        if update_result.get("status") != "ok":
            return update_result
        update_result["action"] = "updated"
        return update_result
    finally:
        conn.close()


# [TRACK-008] observation_bulk_import_export
# ──────────────────────────────────────────────────────────────────────────────
# CSV / text bulk import & export for the observation warehouse.
#   - Import parses a CSV (or simple whitespace-separated text) blob into
#     observation item dicts, dedups within the blob by normalized symbol
#     (merging notes via append/keep), then forwards to the existing
#     ``bulk_upsert_observation_items`` write path so all TRACK-001 / TRACK-006
#     contracts (UNIQUE(symbol), notes preservation, price boundary 0.0,
#     forbidden-word scrub, enum validation) are honored.
#   - Export flattens every observation item to a CSV row using the same
#     header aliases import accepts, so export→import round-trips.
#   - Neither path triggers TA / LLM or writes to tradingagents.db.
# ──────────────────────────────────────────────────────────────────────────────

# Canonical CSV header + accepted aliases (case-insensitive, whitespace-trimmed).
# Chinese headers first so exported CSVs are readable by the target user.
_OBSERVATION_CSV_COLUMNS: list[tuple[str, ...]] = [
    ("symbol",     ("代码", "symbol", "code", "股票代码", "标的")),
    ("name",       ("名称", "name", "股票名称", "股票名")),
    ("status",     ("状态", "status")),
    ("entry_low",  ("入场下沿", "entry_low", "买入下限")),
    ("entry_high", ("入场上沿", "entry_high", "买入上限")),
    ("trigger_price", ("触发价", "trigger_price", "触发价格")),
    ("invalid_price", ("失效价", "invalid_price", "止损价")),
    ("horizon",    ("周期", "horizon", "持仓周期")),
    ("source",     ("来源", "source")),
    ("reason",     ("理由", "reason", "观察理由")),
    ("notes",      ("备注", "notes", "说明")),
    ("priority",   ("优先级", "priority")),
    ("strategy_tags", ("主题", "tags", "标签", "strategy_tags")),
]
# Combined "entry zone" column (e.g. "29.0-30.5") — split into entry_low/high.
_OBSERVATION_ENTRY_ZONE_ALIASES = ("入场区", "入场区间", "entry_zone", "entry_range")

# Chinese display headers used when exporting (order matters).
_OBSERVATION_EXPORT_HEADERS = [
    "代码", "名称", "状态", "入场下沿", "入场上沿", "触发价", "失效价",
    "周期", "来源", "主题", "优先级", "理由", "备注",
]


def _observation_csv_alias_map() -> dict[str, str]:
    """Build a lower-cased alias → canonical field map."""
    amap: dict[str, str] = {}
    for canon, aliases in _OBSERVATION_CSV_COLUMNS:
        for a in aliases:
            amap[a.strip().lower()] = canon
    for a in _OBSERVATION_ENTRY_ZONE_ALIASES:
        amap[a.strip().lower()] = "entry_zone"
    return amap


def _split_observation_entry_zone(raw: str) -> tuple[float, float]:
    """Split a combined entry-zone string like '29.0-30.5' / '29~30.5' into
    (low, high). Returns (0.0, 0.0) if the string is blank or unparseable."""
    if not raw:
        return 0.0, 0.0
    s = raw.strip()
    if not s:
        return 0.0, 0.0
    for sep in ("-", "—", "~", "至", "到", ","):
        if sep in s:
            parts = s.split(sep)
            if len(parts) == 2:
                try:
                    lo = float(parts[0].strip())
                    hi = float(parts[1].strip())
                    return lo, hi
                except ValueError:
                    continue
    # Single number: treat as both low and high (a point entry).
    try:
        v = float(s)
        return v, v
    except ValueError:
        return 0.0, 0.0


def _split_tags(raw: str) -> list[str]:
    """Split a tag-cell string into a list, on comma / pipe / semicolon."""
    if not raw:
        return []
    s = raw.strip()
    if not s:
        return []
    out: list[str] = []
    for chunk in s.replace(";", ",").replace("|", ",").replace("、", ",").split(","):
        c = chunk.strip()
        if c:
            out.append(c)
    return out


def _looks_like_observation_csv(text: str) -> bool:
    """Heuristic: does this blob look like a CSV table (has a recognized
    header or contains commas in the first non-empty line)?"""
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        first = s
        break
    else:
        return False
    if "," not in first and "\t" not in first:
        return False
    header_tokens = {t.strip().lower() for t in first.replace("\t", ",").split(",")}
    amap = _observation_csv_alias_map()
    hits = sum(1 for t in header_tokens if t in amap)
    return hits >= 2


def parse_observation_csv(text: str) -> list[dict[str, Any]]:
    """Parse a CSV / tab-separated / whitespace text blob into observation
    item dicts.

    Recognition:
      - If the first non-empty line contains ≥2 recognized headers (or has a
        comma/tab structure with a header row) → parse as CSV with headers.
      - Otherwise → parse as simple whitespace-separated lines
        ``<symbol> [name...]`` (quick-paste watchlist).

    Within the blob, rows sharing the same normalized symbol are merged:
      - notes appended (duplicate note fragments deduped),
      - strategy_tags unioned,
      - last non-empty value wins for scalar fields.
    """
    import csv as _csv
    import io as _io

    if not text or not text.strip():
        return []

    # Strip a leading UTF-8 BOM so exported CSVs re-import cleanly.
    if text.startswith("\ufeff"):
        text = text[1:]

    raw_rows: list[dict[str, str]] = []

    if _looks_like_observation_csv(text):
        # Normalize tabs to commas so DictReader handles TSV too.
        normalized = "\n".join(
            ln.replace("\t", ",") if not ln.lstrip().startswith("#") else ""
            for ln in text.splitlines()
        )
        reader = _csv.DictReader(_io.StringIO(normalized))
        amap = _observation_csv_alias_map()
        for row in reader:
            mapped: dict[str, str] = {}
            for raw_key, val in row.items():
                if raw_key is None:
                    continue
                canon = amap.get(raw_key.strip().lower())
                if canon and canon not in mapped:
                    mapped[canon] = (val or "").strip()
                elif canon:
                    # Keep first non-empty occurrence.
                    if not mapped[canon]:
                        mapped[canon] = (val or "").strip()
            if mapped:
                raw_rows.append(mapped)
    else:
        # Simple whitespace-separated watchlist: "<symbol> [name...]"
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            sym = parts[0]
            nm = " ".join(parts[1:]) if len(parts) > 1 else ""
            raw_rows.append({"symbol": sym, "name": nm})

    # Materialize each row, expanding entry_zone if present.
    items: list[dict[str, Any]] = []
    for r in raw_rows:
        symbol = (r.get("symbol") or "").strip()
        if not symbol:
            continue
        entry: dict[str, Any] = {"symbol": symbol, "name": (r.get("name") or "").strip()}
        zone = r.get("entry_zone")
        if zone:
            lo, hi = _split_observation_entry_zone(zone)
            # Explicit entry_low / entry_high columns override the zone cell.
            entry["entry_low"] = lo
            entry["entry_high"] = hi
        for canon in (
            "status", "trigger_price", "invalid_price", "horizon",
            "source", "reason", "notes", "priority", "entry_low", "entry_high",
            "name",
        ):
            if canon in r and r[canon] != "":
                entry[canon] = r[canon]
        if "strategy_tags" in r and r["strategy_tags"]:
            entry["strategy_tags"] = _split_tags(r["strategy_tags"])
        items.append(entry)

    # Dedup within blob by normalized symbol (notes append, tags union).
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for it in items:
        norm = normalize_tradeflow_symbol(it.get("symbol", ""))
        if not norm:
            continue
        if norm not in merged:
            it["symbol"] = norm
            merged[norm] = it
            order.append(norm)
        else:
            base = merged[norm]
            for k, v in it.items():
                if k == "symbol":
                    continue
                if k == "notes":
                    existing = base.get("notes", "")
                    incoming = v or ""
                    if incoming and incoming not in existing:
                        base["notes"] = (existing + " | " + incoming).strip(" |") if existing else incoming
                elif k == "strategy_tags":
                    existing_tags = base.get("strategy_tags", [])
                    if isinstance(existing_tags, list) and isinstance(v, list):
                        for t in v:
                            if t not in existing_tags:
                                existing_tags.append(t)
                        base["strategy_tags"] = existing_tags
                else:
                    if v not in (None, "", 0, 0.0) or k not in base:
                        base[k] = v
    return [merged[s] for s in order]


def import_observation_csv(
    csv_text: str,
    force_overwrite_notes: bool = False,
    tf_db_path: str = "",
) -> dict:
    """Parse ``csv_text`` and bulk-upsert the resulting items.

    Honors all TRACK-001 / TRACK-006 contracts via the shared
    ``bulk_upsert_observation_items`` write path. Returns a dict with the
    upsert result plus CSV-level parse stats.
    """
    _fast_meta = _tradeflow_meta("tradeflow_observation_import")
    if not isinstance(csv_text, str):
        return {
            "status": "error",
            "message": "csv_text 必须为字符串",
            "runtime_tier_meta": _fast_meta,
        }

    try:
        parsed = parse_observation_csv(csv_text)
    except Exception as exc:
        return {
            "status": "error",
            "message": f"CSV 解析失败: {exc}",
            "parsed_count": 0,
            "runtime_tier_meta": _fast_meta,
        }

    if not parsed:
        return {
            "status": "ok",
            "message": "无可导入条目",
            "parsed_count": 0,
            "created": [],
            "updated": [],
            "errored": [],
            "created_count": 0,
            "updated_count": 0,
            "errored_count": 0,
            "runtime_tier_meta": _fast_meta,
        }

    for it in parsed:
        it["force_overwrite_notes"] = bool(force_overwrite_notes)
        # Scrub forbidden strong-action words from imported reason text so
        # CSV imports honor the same safety policy as TRACK-006 helpers.
        if it.get("reason"):
            it["reason"] = _scrub_observation_text(it["reason"])

    result = bulk_upsert_observation_items(parsed, tf_db_path=tf_db_path)
    result["parsed_count"] = len(parsed)
    result["runtime_tier_meta"] = _fast_meta
    return result


def export_observation_csv(
    include_removed: bool = True,
    tf_db_path: str = "",
) -> dict:
    """Export all observation items as a CSV string.

    Uses the Chinese display header set so the file is directly readable by
    the user; re-importing the exported file round-trips because the headers
    are recognized aliases.
    """
    import csv as _csv
    import io as _io

    _fast_meta = _tradeflow_meta("tradeflow_observation_export")
    listing = get_observation_items(include_removed=include_removed, tf_db_path=tf_db_path)
    items = listing.get("items", []) if isinstance(listing, dict) else []

    def _fmt_price(v: Any) -> str:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return "0"
        # Trim trailing zeros but keep 0 as "0" (boundary value preserved).
        if f == 0:
            return "0"
        return (f"{f:.4f}").rstrip("0").rstrip(".")

    buf = _io.StringIO()
    # Write UTF-8 BOM so Excel opens Chinese headers correctly.
    buf.write("\ufeff")
    writer = _csv.writer(buf)
    writer.writerow(_OBSERVATION_EXPORT_HEADERS)
    for it in items:
        tags = it.get("strategy_tags") or []
        tags_str = ",".join(tags) if isinstance(tags, list) else ""
        writer.writerow([
            it.get("symbol", ""),
            it.get("name", ""),
            it.get("status", "watching"),
            _fmt_price(it.get("entry_low", 0.0)),
            _fmt_price(it.get("entry_high", 0.0)),
            _fmt_price(it.get("trigger_price", 0.0)),
            _fmt_price(it.get("invalid_price", 0.0)),
            it.get("horizon", "short"),
            it.get("source", "manual"),
            tags_str,
            it.get("priority", 0),
            it.get("reason", ""),
            it.get("notes", ""),
        ])

    return {
        "status": "ok",
        "csv_text": buf.getvalue(),
        "count": len(items),
        "runtime_tier_meta": _fast_meta,
    }
