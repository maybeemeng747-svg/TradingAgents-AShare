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
from .candidate_engine import evaluate_symbol, init_db, filter_symbol
from .universe import build_universe


SOURCE_WATCHLIST = "watchlist"
SOURCE_HOLDING = "holding"
SOURCE_MANUAL = "manual"
SOURCE_INDUSTRY = "industry_pool"
SOURCE_EVENT = "event_source"

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


def _build_discovery_universe(
    symbols: Optional[list[str]] = None,
    industry_symbols: Optional[list[str]] = None,
    prod_db_path: str = "",
    tf_db_path: str = "",
    include_holdings: bool = True,
    include_watchlist: bool = True,
    include_yesterday: bool = False,
    event_symbols: Optional[dict[str, list[str]]] = None,
) -> list[dict]:
    """Build universe for discovery — adds industry pool as a source."""
    universe: dict[str, dict] = {}

    base = build_universe(
        symbols=symbols,
        prod_db_path=prod_db_path,
        tf_db_path=tf_db_path,
        include_holdings=include_holdings,
        include_watchlist=include_watchlist,
        include_yesterday=include_yesterday,
        event_symbols=event_symbols,
    )
    for item in base:
        universe[item["symbol"]] = item

    if industry_symbols:
        for sym in industry_symbols:
            sym = sym.strip()
            if sym and sym not in universe:
                universe[sym] = {"symbol": sym, "name": "", "source": SOURCE_INDUSTRY}

    return list(universe.values())


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

    Returns:
        DiscoveryResult with candidates and filtered symbols.
    """
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    events_map: dict[str, list[str]] = {}
    if use_event_source:
        from .event_source import fetch_daily_events
        trade_date_compact = trade_date.replace("-", "")
        events_map = fetch_daily_events(trade_date_compact)

    universe = _build_discovery_universe(
        symbols=symbols,
        industry_symbols=industry_symbols,
        prod_db_path=prod_db_path,
        tf_db_path=tf_db_path,
        include_holdings=include_holdings,
        include_watchlist=include_watchlist,
        include_yesterday=False,
        event_symbols=events_map if events_map else None,
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

        sym_news = events_map.get(sym) if events_map else None

        c, reason = evaluate_symbol(
            symbol=sym,
            name=sym_name,
            source=sym_source,
            trade_date=trade_date,
            news_texts=sym_news or news_texts,
            event_overrides=filtered_overrides,
        )
        if c is not None:
            candidates.append(c)
        else:
            filtered.append(FilteredSymbol(
                symbol=sym,
                name=sym_name,
                source=sym_source,
                reason=reason,
            ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    top_candidates = candidates[:top_n]

    plan_entries = []
    for c in top_candidates:
        entry = _build_discovery_entry(c)
        plan_entries.append(entry)

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
        },
    )

    if save_candidates and tf_db_path:
        init_db(tf_db_path)
        from .candidate_engine import save_candidate
        for c in top_candidates:
            if c.trade_date != trade_date:
                c.trade_date = trade_date
            save_candidate(c, tf_db_path)

    return result


def _build_discovery_entry(candidate: Candidate) -> dict:
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

    return {
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
    }


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

    return "\n".join(lines)
