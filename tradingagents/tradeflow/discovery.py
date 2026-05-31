"""TradeFlow Discovery — small-scope candidate discovery.  # [T-002] discovery

Runs the full evaluation pipeline on a bounded set of symbols
(watchlist / holdings / manual list / industry pilot) and returns
TopN candidates with filter reasons, without invoking TA deep analysis.

Design constraints:
- No full-market scan; only explicitly specified symbol pools.
- No TA deep analysis auto-invocation; only marks need_deep_ta.
- No strong buy/sell words in output.
- Filter reasons must distinguish: liquidity / data missing / no strategy hit.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from .schemas import Candidate, DailyPlan, ALLOWED_ACTIONS, FORBIDDEN_WORDS
from .candidate_engine import evaluate_symbol, init_db, filter_symbol  # [UI-007]
from .candidate_engine import save_filtered_symbols as _save_filtered_symbols  # [UI-007] tradeflow_filtered_trace
from .universe import build_universe
from .false_positive_audit import build_audit_report, render_audit_report, AuditReport  # [S-006] candidate_false_positive_audit
from .tier_budget import allocate_tier_budget, render_tier_budget_summary  # [S-007] candidate_tier_budget
from .symbol_utils import normalize_tradeflow_symbol, symbol_bare_code  # [UI-008] tradeflow_field_normalization


SOURCE_WATCHLIST = "watchlist"
SOURCE_HOLDING = "holding"
SOURCE_MANUAL = "manual"
SOURCE_INDUSTRY = "industry_pool"
SOURCE_EVENT = "event_source"

SOURCE_FUND_FLOW = "fund_flow_pool"  # [T-003] fund_flow_anomaly_pool

TOPN_DEFAULT = 20


@dataclass
class FilteredSymbol:
    symbol: str
    name: str
    source: str
    reason: str


@dataclass
class DiscoveryResult:
    trade_date: str
    universe_size: int = 0
    candidates: list[dict] = field(default_factory=list)
    filtered: list[dict] = field(default_factory=list)
    summary: str = ""
    metadata: dict = field(default_factory=dict)
    audit_report: Optional[AuditReport] = None  # [S-006] candidate_false_positive_audit


def _build_discovery_universe(
    symbols: Optional[list[str]] = None,
    industry_symbols: Optional[list[str]] = None,
    prod_db_path: str = "",
    tf_db_path: str = "",
    include_holdings: bool = True,
    include_watchlist: bool = True,
    include_yesterday: bool = False,
    event_symbols: Optional[dict[str, list[str]]] = None,
    fund_flow_symbols: Optional[list[str]] = None,  # [T-003] fund_flow_anomaly_pool
) -> list[dict]:
    """Build universe for discovery — uses UniverseManager with all sources."""
    universe = build_universe(
        symbols=symbols,
        prod_db_path=prod_db_path,
        tf_db_path=tf_db_path,
        include_holdings=include_holdings,
        include_watchlist=include_watchlist,
        include_yesterday=include_yesterday,
        event_symbols=event_symbols,
        industry_symbols=industry_symbols,
        fund_flow_symbols=fund_flow_symbols,
    )

    if fund_flow_symbols:
        existing = {item["symbol"] for item in universe}
        for sym in fund_flow_symbols:
            sym = normalize_tradeflow_symbol(sym.strip())  # [UI-008]
            if sym and sym not in existing:
                universe.append({
                    "symbol": sym,
                    "name": "",
                    "source": SOURCE_FUND_FLOW,
                    "universe_sources": [SOURCE_FUND_FLOW],
                    "universe_source_records": [],
                    "filter_reason": "",
                })
                existing.add(sym)

    return universe


def run_discovery(
    trade_date: Optional[str] = None,
    symbols: Optional[list[str]] = None,
    industry_symbols: Optional[list[str]] = None,
    prod_db_path: str = "",
    tf_db_path: str = "",
    top_n: int = TOPN_DEFAULT,
    include_holdings: bool = True,
    include_watchlist: bool = True,
    event_overrides: Optional[list[dict]] = None,
    news_texts: Optional[list[str]] = None,
    use_event_source: bool = False,
    save_candidates: bool = False,
    fund_flow_map: Optional[dict[str, dict]] = None,  # [T-003] fund_flow_anomaly_pool
) -> DiscoveryResult:
    """Run small-scope discovery and return TopN candidates.

    Args:
        trade_date: Trading date (default: today).
        symbols: Manual symbol list.
        industry_symbols: Industry pilot symbol list.
        prod_db_path: Path to production DB.
        tf_db_path: Path to tradeflow DB.
        top_n: Maximum candidates to return.
        include_holdings: Include holdings from prod DB.
        include_watchlist: Include watchlist from prod DB.
        event_overrides: Manual event overrides.
        news_texts: News texts.
        use_event_source: Fetch daily events for event pool.
        save_candidates: Persist candidates to DB.
        fund_flow_map: [T-003] Optional dict mapping symbol → {"individual": str, "board": str}.

    Returns:
        DiscoveryResult with candidates and filtered symbols.
    """
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    events_map: dict[str, list[str]] = {}
    event_items_by_symbol: dict[str, list] = {}  # [T-006] event_source_discovery
    event_source_status = "NOT_QUERIED"  # [T-006]
    event_source_error = ""  # [T-006]
    event_source_failed: list[str] = []  # [T-007]
    event_source_per_source: dict[str, str] = {}  # [T-007]

    if use_event_source:
        from .event_source import fetch_daily_events_detailed, EventSourceStatus  # [T-006]
        trade_date_compact = trade_date.replace("-", "")
        event_result = fetch_daily_events_detailed(trade_date_compact)  # [T-006]
        events_map = event_result.events_map
        event_items_by_symbol = event_result.items_by_symbol  # [T-006]
        event_source_status = event_result.status.value  # [T-006]
        event_source_error = event_result.error_message  # [T-006]
        event_source_failed = event_result.failed_sources  # [T-007]
        event_source_per_source = event_result.source_statuses  # [T-007]

    universe = _build_discovery_universe(
        symbols=symbols,
        industry_symbols=industry_symbols,
        prod_db_path=prod_db_path,
        tf_db_path=tf_db_path,
        include_holdings=include_holdings,
        include_watchlist=include_watchlist,
        include_yesterday=False,
        event_symbols=events_map if events_map else None,
        fund_flow_symbols=list(fund_flow_map.keys()) if fund_flow_map else None,  # [T-003]
    )

    candidates: list[Candidate] = []
    filtered: list[FilteredSymbol] = []

    for item in universe:
        sym = item["symbol"]
        sym_name = item.get("name", "")
        sym_source = item.get("source", "manual")

        if event_overrides:
            filtered_overrides = [
                ev for ev in event_overrides
                if ev.get("symbol") is None or ev.get("symbol") == sym
            ]
        else:
            filtered_overrides = None

        # [T-006] event_source_discovery — generate event_overrides from EventItem data
        sym_event_overrides = list(filtered_overrides) if filtered_overrides else []
        sym_bare = symbol_bare_code(sym)  # [UI-008] event map uses bare codes
        if sym_bare in event_items_by_symbol:
            for ev_item in event_items_by_symbol[sym_bare]:
                sym_event_overrides.append({
                    "symbol": sym,
                    "title": ev_item.title,
                    "event_type": ev_item.event_type,
                    "direction": ev_item.direction,
                    "source": ev_item.source,
                    "date": ev_item.date,
                })
        sym_event_overrides_final = sym_event_overrides if sym_event_overrides else None

        sym_news = events_map.get(sym_bare) if events_map else None  # [UI-008] bare code lookup

        # [T-003] fund_flow_anomaly_pool — get per-symbol fund flow data
        sym_ff = fund_flow_map.get(sym, {}) if fund_flow_map else {}
        sym_ff_individual = sym_ff.get("individual") if sym_ff else None
        sym_ff_board = sym_ff.get("board") if sym_ff else None

        c, reason = evaluate_symbol(
            symbol=sym,
            name=sym_name,
            source=sym_source,
            trade_date=trade_date,
            news_texts=sym_news or news_texts,
            event_overrides=sym_event_overrides_final,  # [T-006]
            fund_flow_individual=sym_ff_individual,  # [T-003]
            fund_flow_board=sym_ff_board,  # [T-003]
        )
        if c is not None:
            if item.get("universe_sources"):
                c.universe_sources = item["universe_sources"]  # [M-003]
            # [T-006] event_source_discovery — attach event items for discovery entry
            if sym_bare in event_items_by_symbol:
                c._event_items = event_items_by_symbol[sym_bare]  # type: ignore[attr-defined]
            candidates.append(c)
        else:
            filtered.append(FilteredSymbol(
                symbol=sym,
                name=sym_name,
                source=sym_source,
                reason=reason,
            ))

    candidates.sort(key=lambda c: (c.composite_score, c.score), reverse=True)
    top_candidates = candidates[:top_n]

    plan_entries = []
    for c in top_candidates:
        ev_items = getattr(c, "_event_items", None)  # [T-006]
        entry = _build_discovery_entry(c, event_items=ev_items)
        plan_entries.append(entry)

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
    for i, tr in enumerate(tier_results):
        if i < len(plan_entries):
            plan_entries[i]["tier"] = tr.tier
            plan_entries[i]["ta_budget_priority"] = tr.ta_budget_priority
            plan_entries[i]["tier_reason"] = tr.tier_reason

    n_total = len(plan_entries)
    n_deep_ta = sum(1 for e in plan_entries if e.get("need_deep_ta"))
    n_filtered = len(filtered)
    n_universe = len(universe)

    filter_breakdown = _classify_filter_reasons(filtered)

    summary_parts = [
        f"共扫描{n_universe}只",
        f"{n_total}只候选(Top{top_n})",
        f"{n_deep_ta}只需要深度TA",
        f"{n_filtered}只被过滤",
    ]
    if filter_breakdown:
        for cat, cnt in filter_breakdown.items():
            summary_parts.append(f"{cat}{cnt}只")

    result = DiscoveryResult(
        trade_date=trade_date,
        universe_size=n_universe,
        candidates=plan_entries,
        filtered=[asdict(f) for f in filtered],
        summary="，".join(summary_parts) + "。",
        metadata={
            "top_n": top_n,
            "generated_at": datetime.now().isoformat(),
            "filter_breakdown": filter_breakdown,
            "sources": list({item.get("source", "manual") for item in universe}),
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

    if save_candidates and tf_db_path:
        init_db(tf_db_path)
        from .candidate_engine import save_candidate
        for c in top_candidates:
            if c.trade_date != trade_date:
                c.trade_date = trade_date
            save_candidate(c, tf_db_path)

    # [UI-007] tradeflow_filtered_trace — persist filtered symbols
    run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    if tf_db_path and filtered:
        init_db(tf_db_path)
        _save_filtered_symbols(
            filtered=[asdict(f) for f in filtered],
            trade_date=trade_date,
            run_id=run_id,
            db_path=tf_db_path,
        )
    elif tf_db_path and not filtered:
        init_db(tf_db_path)
        import sqlite3
        conn = sqlite3.connect(tf_db_path)
        try:
            conn.execute(
                "DELETE FROM tradeflow_filtered_symbols WHERE trade_date = ?",
                (trade_date,),
            )
            conn.commit()
        finally:
            conn.close()

    # [S-006] candidate_false_positive_audit — generate audit report
    audit = build_audit_report(
        candidates=top_candidates,
        filtered_symbols=[asdict(f) for f in filtered],
        trade_date=trade_date,
    )
    result.audit_report = audit
    result.metadata["audit_summary"] = {
        "total_candidates": audit.summary.total_candidates if audit.summary else 0,
        "total_filtered": audit.summary.total_filtered if audit.summary else 0,
        "by_category": audit.summary.by_category if audit.summary else {},
        "common_evidence_gaps": audit.summary.common_evidence_gaps[:5] if audit.summary else [],
    }

    # [S-007] candidate_tier_budget — add budget allocation to metadata
    result.metadata["tier_budget"] = {
        "tier_a_count": budget_allocation.tier_a_count,
        "tier_b_count": budget_allocation.tier_b_count,
        "tier_c_count": budget_allocation.tier_c_count,
        "total_budget": budget_allocation.total_budget,
        "a_tier_cap_applied": budget_allocation.a_tier_cap_applied,
        "demoted_count": len(budget_allocation.demoted_symbols),
    }
    result.metadata["tier_budget_summary"] = render_tier_budget_summary(budget_allocation)

    # [S-008] tradeflow_evidence_gate — add evidence gate summary to metadata
    gate_blocked_count = sum(1 for e in plan_entries if e.get("evidence_gate_applied"))
    gate_deep_ta_blocked = sum(1 for e in plan_entries if e.get("evidence_gate_applied") and not e.get("need_deep_ta"))
    result.metadata["evidence_gate"] = {
        "gate_blocked_count": gate_blocked_count,
        "deep_ta_blocked_count": gate_deep_ta_blocked,
        "avg_completeness": round(sum(e.get("tradeflow_data_completeness", 0) for e in plan_entries) / max(len(plan_entries), 1), 3),
    }

    return result


def _build_discovery_entry(candidate: Candidate, event_items: Optional[list] = None) -> dict:  # [T-006] event_source_discovery
    """Build a discovery plan entry from a candidate."""
    if candidate.need_deep_ta:
        action = "NEED_DEEP_TA"
    else:
        action = "OBSERVE"

    reasons = []
    for sig in candidate.signals:
        reasons.append(f"[{sig.strategy_tag}] {sig.reason}")
    reason_str = "; ".join(reasons) if reasons else "候选观察"

    for word in FORBIDDEN_WORDS:
        if word in reason_str:
            reason_str = reason_str.replace(word, "***")

    entry = {
        "symbol": candidate.symbol,
        "name": candidate.name,
        "source": candidate.source,
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
        "policy_tags": candidate.policy_tags,
        "version_score": candidate.version_score,
        "narrative_score": candidate.narrative_score,
        "narrative_reasons": candidate.narrative_reasons,
        "risk_penalty": candidate.risk_penalty,
        "risk_reasons": candidate.risk_reasons,
        "game_balance": candidate.game_balance,
        "bull_case": candidate.bull_case,
        "bear_case": candidate.bear_case,
        "policy_case": candidate.policy_case,
        "fund_flow_case": candidate.fund_flow_case,
        "resonance_count": candidate.resonance_count,
        "fund_flow_anomaly_score": candidate.fund_flow_anomaly_score,  # [T-003]
        "fund_flow_anomaly_tags": candidate.fund_flow_anomaly_tags,  # [T-003]
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
    }

    # [T-006] event_source_discovery — add event metadata to discovery entry
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


def _classify_filter_reasons(filtered: list[FilteredSymbol]) -> dict[str, int]:
    """Classify filter reasons into categories."""
    categories: dict[str, int] = {}
    for f in filtered:
        reason = f.reason
        if "流动性" in reason:
            cat = "流动性差"
        elif "数据" in reason:
            cat = "数据缺失"
        elif "无策略" in reason:
            cat = "无策略命中"
        else:
            cat = "其他"
        categories[cat] = categories.get(cat, 0) + 1
    return categories


def render_discovery_text(result: DiscoveryResult) -> str:
    """Render a human-readable discovery report."""
    lines = [
        f"🔍 Discovery 扫描报告 {result.trade_date}",
        "=" * 50,
        "",
        f"📋 摘要: {result.summary}",
        "",
    ]

    if result.metadata.get("sources"):
        lines.append(f"📂 来源: {', '.join(result.metadata['sources'])}")
        lines.append("")

    if not result.candidates:
        lines.append("（无候选）")
    for i, c in enumerate(result.candidates, 1):
        sym = c.get("symbol", "?")
        name = c.get("name", "")
        action = c.get("action", "OBSERVE")
        strategies = c.get("strategies", [])
        reason = c.get("reason", "")
        score = c.get("score", 0)
        trigger = c.get("trigger_price")
        invalid = c.get("invalid_price")
        deep_ta = c.get("need_deep_ta", False)
        src = c.get("source", "")

        lines.append(f"--- 候选 {i}: {sym} {name} (来源: {src}) ---")
        lines.append(f"  动作: {action} | 得分: {score}")
        if strategies:
            lines.append(f"  策略: {', '.join(strategies)}")
        if reason:
            lines.append(f"  原因: {reason}")
        if trigger:
            lines.append(f"  触发价: {trigger}")
        if invalid:
            lines.append(f"  失效价: {invalid}")
        if deep_ta:
            lines.append(f"  ⚠️ 需要深度 TA 分析")

        risk_flags = c.get("risk_flags", [])
        if risk_flags:
            lines.append(f"  ⚠️ 风险: {', '.join(risk_flags)}")

        game_balance = c.get("game_balance", "")
        if game_balance:
            lines.append(f"  博弈平衡: {game_balance}")

        # [T-003] fund_flow_anomaly_pool
        ff_tags = c.get("fund_flow_anomaly_tags", [])
        ff_score = c.get("fund_flow_anomaly_score", 0)
        ff_ind = c.get("fund_flow_individual_summary", "")
        ff_board = c.get("fund_flow_board_summary", "")
        ff_verified = c.get("fund_flow_unit_verified", False)
        if ff_tags:
            unit_mark = "✓" if ff_verified else "⚠未校验"
            lines.append(f"  资金异动: {', '.join(ff_tags)} (+{ff_score}分) [{unit_mark}]")
            if ff_ind:
                lines.append(f"    个股: {ff_ind}")
            if ff_board:
                lines.append(f"    板块: {ff_board}")

        # [S-005] selection_priority_gate — display priority gate results
        priority_rank = c.get("priority_rank", "")
        composite_score_val = c.get("composite_score", 0)
        why_deep = c.get("why_deep_ta", "")
        why_not_deep = c.get("why_not_deep_ta", "")
        missing_ev = c.get("missing_evidence", [])
        data_comp = c.get("data_completeness", 0)
        if priority_rank:
            rank_emoji = {"A": "🟢", "B": "🟡", "C": "🔴"}
            lines.append(f"  优先级: {rank_emoji.get(priority_rank, '')} {priority_rank}层 | 综合分: {composite_score_val:.1f} | 完整度: {data_comp:.0%}")
            if why_deep:
                lines.append(f"    值得深挖: {why_deep}")
            if why_not_deep:
                lines.append(f"    暂不深挖: {why_not_deep}")
            if missing_ev:
                lines.append(f"    缺少证据: {', '.join(missing_ev[:5])}")

        # [S-007] candidate_tier_budget — display tier and budget allocation
        tier = c.get("tier", "")
        ta_budget = c.get("ta_budget_priority", 0)
        tier_reason_text = c.get("tier_reason", "")
        missing_upgrade = c.get("missing_evidence_for_upgrade", [])
        if tier:
            tier_label = {"A": "优先深挖", "B": "观察等待", "C": "暂不关注"}.get(tier, "")
            lines.append(f"  分层: {tier}层({tier_label}) | TA预算: {ta_budget} tokens")
            if tier_reason_text:
                lines.append(f"    分层原因: {tier_reason_text}")
            if missing_upgrade:
                lines.append(f"    升级所需: {'; '.join(missing_upgrade[:4])}")

        # [S-008] tradeflow_evidence_gate — display evidence completeness
        tf_comp = c.get("tradeflow_data_completeness", 0)
        missing_df = c.get("missing_data_fields", [])
        what_up = c.get("what_to_upgrade", [])
        gate_applied = c.get("evidence_gate_applied", False)
        if tf_comp > 0 or missing_df or gate_applied:
            gate_mark = "⚠门禁降级" if gate_applied else ""
            lines.append(f"  证据完整度: {tf_comp:.0%} {gate_mark}")
            if missing_df:
                lines.append(f"    缺失字段: {', '.join(missing_df[:5])}")
            if what_up:
                lines.append(f"    升级所需: {'; '.join(what_up[:4])}")

        lines.append("")

    if result.filtered:
        lines.append(f"--- 被过滤 ({len(result.filtered)}只) ---")
        breakdown = result.metadata.get("filter_breakdown", {})
        if breakdown:
            parts = [f"{cat}: {cnt}只" for cat, cnt in breakdown.items()]
            lines.append(f"  {' | '.join(parts)}")
        lines.append("")
        for f in result.filtered[:20]:
            lines.append(f"  {f['symbol']} {f.get('name', '')}: {f['reason']}")
        if len(result.filtered) > 20:
            lines.append(f"  ... 共{len(result.filtered)}只，仅显示前20只")

    # [S-006] candidate_false_positive_audit — evidence gap summary
    if result.audit_report and result.audit_report.summary:
        s = result.audit_report.summary
        if s.common_evidence_gaps:
            lines.append("")
            lines.append("--- 证据缺口摘要 ---")
            for item in s.common_evidence_gaps[:5]:
                lines.append(f"  - {item['gap']} ({item['count']}只)")
        if s.by_fp_type:
            lines.append("")
            lines.append("--- 正误判分布 ---")
            for t, cnt in sorted(s.by_fp_type.items()):
                lines.append(f"  - {t}: {cnt}只")

    # [S-007] candidate_tier_budget — tier budget summary
    tier_budget_summary = result.metadata.get("tier_budget_summary", "")
    if tier_budget_summary:
        lines.append("")
        lines.append(tier_budget_summary)

    return "\n".join(lines)
