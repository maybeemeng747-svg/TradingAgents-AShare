# [H-013] mandate_topic_heatmap
"""Topic Heatmap — observability layer over the H-012 topic registry.

Turns the static topic registry into observable "topic heat / lifecycle" data
so the user can see whether policy emphasis is *persisting*, rather than only
looking at single-stock technical patterns.

What this module provides
-------------------------
1. Per-topic heat curve over a date window: daily signal count, candidate
   count, policy level, evidence count, overheat flags, lifecycle state.
2. Windowed summaries: signals/candidates/evidence in the last 7 / 20 / 60
   days, plus a heat-trend direction (rising / stable / cooling).
3. Topic state-change detection: 酝酿 → 发酵 → 确认 → 兑现 → 退潮
   transitions surfaced as human-readable change labels.
4. Counter-evidence & evidence-gap aggregation per topic.
5. Candidate ↔ topic interlinking (every heatmap entry carries its symbols).

Design constraints (from the task)
----------------------------------
- No LLM calls.
- Topic heat never equals a trade action — it only affects ranking
  *explanation* and watchlist ordering, never the final buy/sell decision.
- Does not overwrite user watchlist notes.

Usage (library)
---------------
    from tradingagents.tradeflow.topic_heatmap import (
        build_topic_heatmap,
        render_topic_heatmap_markdown,
        save_topic_heatmap_report,
    )
    report = build_topic_heatmap(candidate_history)
    print(render_topic_heatmap_markdown(report))
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .topic_registry import (
    POLICY_LEVEL_CENTRAL,
    POLICY_LEVEL_MINISTRY,
    POLICY_LEVEL_LOCAL,
    POLICY_LEVEL_INDUSTRY,
    POLICY_LEVEL_MEDIA,
    POLICY_LEVEL_UNKNOWN,
    TOPIC_STATUS_BREWING,
    TOPIC_STATUS_FERMENTING,
    TOPIC_STATUS_CONFIRMING,
    TOPIC_STATUS_DELIVERING,
    TOPIC_STATUS_RECEDING,
    TOPIC_STATUS_UNKNOWN,
    _POLICY_LEVEL_WEIGHTS,
    _TOPIC_STATUS_LABELS,
    _LEFT_SIDE_STATUSES,
    _CONFIRM_STATUSES,
    _OBSERVE_ONLY_STATUSES,
    match_topic,
    get_default_topic_definitions,
)


# ── Heat trend direction ─────────────────────────────────────────────

HEAT_RISING = "RISING"        # 升温 — recent window hotter than prior
HEAT_STABLE = "STABLE"        # 平稳 — roughly equal
HEAT_COOLING = "COOLING"      # 降温 — recent window cooler than prior
HEAT_UNKNOWN = "UNKNOWN"

_HEAT_TREND_LABELS: dict[str, str] = {
    HEAT_RISING: "升温",
    HEAT_STABLE: "平稳",
    HEAT_COOLING: "降温",
    HEAT_UNKNOWN: "未知",
}

# Sensitivity: recent/previous ratio above this ⇒ RISING, below inverse ⇒ COOLING
_RISING_RATIO = 1.25
_COOLING_RATIO = 0.75

# Windows (days)
WINDOW_SHORT = 7
WINDOW_MID = 20
WINDOW_LONG = 60

_WINDOWS: tuple[int, int, int] = (WINDOW_SHORT, WINDOW_MID, WINDOW_LONG)

_WINDOW_LABELS: dict[int, str] = {
    WINDOW_SHORT: "近7天",
    WINDOW_MID: "近20天",
    WINDOW_LONG: "近60天",
}


# ── State-change detection ───────────────────────────────────────────

# ordered lifecycle progression for transition magnitude
_STATUS_ORDER: dict[str, int] = {
    TOPIC_STATUS_UNKNOWN: 0,
    TOPIC_STATUS_BREWING: 1,
    TOPIC_STATUS_FERMENTING: 2,
    TOPIC_STATUS_CONFIRMING: 3,
    TOPIC_STATUS_DELIVERING: 4,
    TOPIC_STATUS_RECEDING: 5,
}


def _state_change_label(prev_status: str, curr_status: str) -> str:
    """Human-readable label for a status transition.

    A forward move (brewing → fermenting …) is "升温/发酵/确认/兑现".
    A move into RECEDING is "退潮". No change ⇒ "持平".
    """
    prev = prev_status or TOPIC_STATUS_UNKNOWN
    curr = curr_status or TOPIC_STATUS_UNKNOWN
    if prev == curr:
        return "持平"

    curr_label = _TOPIC_STATUS_LABELS.get(curr, curr)
    prev_label = _TOPIC_STATUS_LABELS.get(prev, prev)

    # moving into receding is always a cooling signal
    if curr == TOPIC_STATUS_RECEDING and prev != TOPIC_STATUS_RECEDING:
        return f"退潮（{prev_label}→退潮）"

    # receding → anything else is a revival
    if prev == TOPIC_STATUS_RECEDING:
        return f"回暖（退潮→{curr_label}）"

    prev_order = _STATUS_ORDER.get(prev, 0)
    curr_order = _STATUS_ORDER.get(curr, 0)

    if curr_order > prev_order:
        # forward progression
        if curr == TOPIC_STATUS_FERMENTING:
            return f"升温发酵（{prev_label}→发酵）"
        if curr == TOPIC_STATUS_CONFIRMING:
            return f"确认升温（{prev_label}→确认）"
        if curr == TOPIC_STATUS_DELIVERING:
            return f"政策兑现（{prev_label}→兑现）"
        if curr == TOPIC_STATUS_BREWING:
            return f"进入酝酿（{prev_label}→酝酿）"
        return f"状态上升（{prev_label}→{curr_label}）"

    # backward within non-receding range
    return f"状态回落（{prev_label}→{curr_label}）"


def is_state_change_positive(prev_status: str, curr_status: str) -> bool:
    """A status change that indicates the topic is gaining heat (not receding)."""
    prev = prev_status or TOPIC_STATUS_UNKNOWN
    curr = curr_status or TOPIC_STATUS_UNKNOWN
    if curr == TOPIC_STATUS_RECEDING and prev != TOPIC_STATUS_RECEDING:
        return False
    if prev == TOPIC_STATUS_RECEDING:
        return True
    return _STATUS_ORDER.get(curr, 0) > _STATUS_ORDER.get(prev, 0)


# ── Data models ──────────────────────────────────────────────────────

@dataclass
class TopicHeatPoint:
    """One day of heat data for a topic."""
    date: str = ""
    signal_count: int = 0
    candidate_count: int = 0
    unique_candidates: int = 0
    symbols: List[str] = field(default_factory=list)  # [H-013A] mandate_topic_heatmap_fix
    policy_level: str = POLICY_LEVEL_UNKNOWN
    policy_level_weight: int = 0
    evidence_count: int = 0
    overheat_flags: List[str] = field(default_factory=list)
    lifecycle_state: str = ""
    topic_status: str = TOPIC_STATUS_UNKNOWN
    topic_status_label: str = "未知"
    heat: float = 0.0

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "signal_count": self.signal_count,
            "candidate_count": self.candidate_count,
            "unique_candidates": self.unique_candidates,
            "symbols": list(self.symbols),  # [H-013A] mandate_topic_heatmap_fix
            "policy_level": self.policy_level,
            "policy_level_weight": self.policy_level_weight,
            "evidence_count": self.evidence_count,
            "overheat_flags": self.overheat_flags,
            "lifecycle_state": self.lifecycle_state,
            "topic_status": self.topic_status,
            "topic_status_label": self.topic_status_label,
            "heat": round(self.heat, 2),
        }


@dataclass
class WindowStats:
    """Aggregated stats for one time window (7 / 20 / 60 days)."""
    window_days: int = 0
    signal_count: int = 0
    candidate_count: int = 0
    unique_candidates: int = 0
    evidence_count: int = 0
    active_days: int = 0
    max_policy_level: str = POLICY_LEVEL_UNKNOWN
    max_policy_level_weight: int = 0

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "window_label": _WINDOW_LABELS.get(self.window_days, f"近{self.window_days}天"),
            "signal_count": self.signal_count,
            "candidate_count": self.candidate_count,
            "unique_candidates": self.unique_candidates,
            "evidence_count": self.evidence_count,
            "active_days": self.active_days,
            "max_policy_level": self.max_policy_level,
            "max_policy_level_weight": self.max_policy_level_weight,
        }


@dataclass
class TopicHeatmapEntry:
    """One topic's full heatmap entry: curve + windowed stats + evidence."""
    topic: str = ""
    description: str = ""
    topic_status: str = TOPIC_STATUS_UNKNOWN
    topic_status_label: str = "未知"
    policy_level: str = POLICY_LEVEL_UNKNOWN
    policy_level_weight: int = 0
    is_left_side: bool = False
    is_observe_only: bool = False
    is_confirmed: bool = False
    heat_curve: List[TopicHeatPoint] = field(default_factory=list)
    windows: Dict[int, WindowStats] = field(default_factory=dict)
    heat_trend: str = HEAT_UNKNOWN
    heat_trend_label: str = "未知"
    state_change_label: str = "持平"
    state_change_positive: bool = False
    latest_date: str = ""
    latest_signal_count: int = 0
    peak_date: str = ""
    peak_heat: float = 0.0
    evidence_links: List[Dict[str, Any]] = field(default_factory=list)
    evidence_summary: str = ""
    counter_evidence_gaps: List[str] = field(default_factory=list)
    overheat_flags: List[str] = field(default_factory=list)
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    candidate_symbols: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "description": self.description,
            "topic_status": self.topic_status,
            "topic_status_label": self.topic_status_label,
            "policy_level": self.policy_level,
            "policy_level_weight": self.policy_level_weight,
            "is_left_side": self.is_left_side,
            "is_observe_only": self.is_observe_only,
            "is_confirmed": self.is_confirmed,
            "heat_curve": [p.to_dict() for p in self.heat_curve],
            "windows": {str(k): v.to_dict() for k, v in self.windows.items()},
            "heat_trend": self.heat_trend,
            "heat_trend_label": self.heat_trend_label,
            "state_change_label": self.state_change_label,
            "state_change_positive": self.state_change_positive,
            "latest_date": self.latest_date,
            "latest_signal_count": self.latest_signal_count,
            "peak_date": self.peak_date,
            "peak_heat": round(self.peak_heat, 2),
            "evidence_links": self.evidence_links,
            "evidence_summary": self.evidence_summary,
            "counter_evidence_gaps": self.counter_evidence_gaps,
            "overheat_flags": self.overheat_flags,
            "candidates": self.candidates,
            "candidate_symbols": self.candidate_symbols,
        }


@dataclass
class TopicHeatmapReport:
    """Full heatmap report across all topics."""
    as_of: str = ""
    window_days: int = WINDOW_LONG
    topics: List[TopicHeatmapEntry] = field(default_factory=list)
    total_topics: int = 0
    active_topics: int = 0
    rising_topics: int = 0
    cooling_topics: int = 0
    left_side_topics: int = 0
    observe_only_topics: int = 0
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "window_days": self.window_days,
            "topics": [t.to_dict() for t in self.topics],
            "total_topics": self.total_topics,
            "active_topics": self.active_topics,
            "rising_topics": self.rising_topics,
            "cooling_topics": self.cooling_topics,
            "left_side_topics": self.left_side_topics,
            "observe_only_topics": self.observe_only_topics,
            "summary": self.summary,
        }


# ── Helpers ──────────────────────────────────────────────────────────

def _today_str(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def _candidate_date(cand: dict) -> str:
    """Resolve the effective date of a candidate (effective_trade_date > trade_date)."""
    return (
        cand.get("effective_trade_date")
        or cand.get("trade_date")
        or cand.get("plan_date")
        or ""
    )


def _resolve_topic(cand: dict, definitions: Optional[list] = None) -> str:
    """Resolve the topic for a candidate, matching against the registry."""
    defs = definitions if definitions is not None else get_default_topic_definitions()
    topic = match_topic(
        mandate_topic=cand.get("mandate_topic", ""),
        policy_tags=cand.get("policy_tags"),
        name=cand.get("name", ""),
        definitions=defs,
    )
    if not topic:
        topic = cand.get("mandate_topic", "")
    return topic


def _max_policy_level(levels: List[str]) -> str:
    """Pick the highest-weight policy level from a list."""
    best = POLICY_LEVEL_UNKNOWN
    best_w = -1
    for lvl in levels:
        w = _POLICY_LEVEL_WEIGHTS.get(lvl, 0)
        if w > best_w:
            best = lvl
            best_w = w
    return best


# ── Heat curve construction ──────────────────────────────────────────

def _compute_heat(point: TopicHeatPoint) -> float:
    """Compute a 0-100 scalar heat for a daily point.

    Weighting (deliberately simple & rule-based, no LLM):
      - candidate count:  up to 30 (3 per candidate, cap 30)
      - evidence count:   up to 25 (2.5 per evidence, cap 25)
      - policy level:     up to 30 (weight * 6, capped)
      - signal momentum:  up to 15 (signal_count * 3, cap 15)
    """
    cand_part = min(point.candidate_count * 3.0, 30.0)
    ev_part = min(point.evidence_count * 2.5, 25.0)
    lvl_part = min(point.policy_level_weight * 6.0, 30.0)
    sig_part = min(point.signal_count * 3.0, 15.0)
    return round(cand_part + ev_part + lvl_part + sig_part, 2)


def _build_daily_point(
    date: str,
    topic_candidates: List[dict],
) -> TopicHeatPoint:
    """Build one TopicHeatPoint from all candidates of a topic on a date."""
    symbols: set[str] = set()
    evidence_count = 0
    policy_levels: List[str] = []
    overheat_flags: List[str] = []
    lifecycle_states: List[str] = []
    statuses: List[str] = []

    for cand in topic_candidates:
        sym = cand.get("symbol", "")
        if sym:
            symbols.add(sym)
        evidence_count += len(cand.get("policy_evidence_refs") or cand.get("mandate_evidence_refs") or [])
        flags = cand.get("overheat_flags") or []
        overheat_flags.extend(flags)
        lc = cand.get("topic_lifecycle_state", "")
        if lc:
            lifecycle_states.append(lc)
        policy_refs = cand.get("policy_evidence_refs") or []
        for ref in policy_refs:
            sl = ref.get("source_level", "")
            if sl in ("CENTRAL", "STATE_COUNCIL"):
                policy_levels.append(POLICY_LEVEL_CENTRAL)
            elif sl in ("MINISTRY",):
                policy_levels.append(POLICY_LEVEL_MINISTRY)
            elif sl in ("LOCAL",):
                policy_levels.append(POLICY_LEVEL_LOCAL)
        pl = cand.get("policy_level", "")
        if pl:
            policy_levels.append(pl)

    max_signal = 0
    for cand in topic_candidates:
        sc = cand.get("topic_signal_count", 0) or 0
        if sc > max_signal:
            max_signal = sc

    policy_level = _max_policy_level(policy_levels) if policy_levels else POLICY_LEVEL_UNKNOWN
    lifecycle_state = lifecycle_states[-1] if lifecycle_states else ""

    # derive topic status from lifecycle via H-012 mapping
    from .topic_registry import _lifecycle_to_status
    topic_status = _lifecycle_to_status(lifecycle_state) if lifecycle_state else TOPIC_STATUS_UNKNOWN
    topic_status_label = _TOPIC_STATUS_LABELS.get(topic_status, "未知")

    # de-dup overheat flags preserving order
    seen_flags: set[str] = set()
    unique_flags: List[str] = []
    for f in overheat_flags:
        if f and f not in seen_flags:
            unique_flags.append(f)
            seen_flags.add(f)

    point = TopicHeatPoint(
        date=date,
        signal_count=max_signal,
        candidate_count=len(topic_candidates),
        unique_candidates=len(symbols),
        symbols=sorted(symbols),  # [H-013A] mandate_topic_heatmap_fix — carry symbols for window dedup
        policy_level=policy_level,
        policy_level_weight=_POLICY_LEVEL_WEIGHTS.get(policy_level, 0),
        evidence_count=evidence_count,
        overheat_flags=unique_flags,
        lifecycle_state=lifecycle_state,
        topic_status=topic_status,
        topic_status_label=topic_status_label,
    )
    point.heat = _compute_heat(point)
    return point


def _compute_window_stats(
    curve: List[TopicHeatPoint],
    window_days: int,
    as_of: str,
) -> WindowStats:
    """Aggregate stats for the trailing `window_days` ending at `as_of`."""
    try:
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    except (ValueError, TypeError):
        return WindowStats(window_days=window_days)

    cutoff = as_of_dt - timedelta(days=window_days)
    signals = 0
    candidates = 0
    evidence = 0
    active_days = 0
    symbols: set[str] = set()
    levels: List[str] = []

    for p in curve:
        try:
            p_dt = datetime.strptime(p.date, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if cutoff <= p_dt <= as_of_dt:
            signals += p.signal_count
            candidates += p.candidate_count
            evidence += p.evidence_count
            if p.candidate_count > 0:
                active_days += 1
            # [H-013A] mandate_topic_heatmap_fix — populate the symbols set
            # (previously created but never filled, so unique_candidates was always 0)
            symbols.update(p.symbols)
            levels.append(p.policy_level)

    return WindowStats(
        window_days=window_days,
        signal_count=signals,
        candidate_count=candidates,
        unique_candidates=len(symbols),
        evidence_count=evidence,
        active_days=active_days,
        max_policy_level=_max_policy_level(levels) if levels else POLICY_LEVEL_UNKNOWN,
        max_policy_level_weight=_POLICY_LEVEL_WEIGHTS.get(
            _max_policy_level(levels) if levels else POLICY_LEVEL_UNKNOWN, 0
        ),
    )


def _detect_heat_trend(curve: List[TopicHeatPoint], as_of: str) -> str:
    """Compare recent-window heat to prior-window heat.

    recent   = trailing 7 days (incl. as_of)
    prior    = the 7 days *before* the recent window (8..14 days ago)
    """
    if len(curve) < 1:
        return HEAT_UNKNOWN
    try:
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    except (ValueError, TypeError):
        return HEAT_UNKNOWN

    recent_start = as_of_dt - timedelta(days=7)
    prior_start = as_of_dt - timedelta(days=14)
    prior_end = as_of_dt - timedelta(days=8)

    recent_heat = 0.0
    prior_heat = 0.0
    for p in curve:
        try:
            p_dt = datetime.strptime(p.date, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if recent_start <= p_dt <= as_of_dt:
            recent_heat += p.heat
        if prior_start <= p_dt <= prior_end:
            prior_heat += p.heat

    if prior_heat <= 0 and recent_heat <= 0:
        return HEAT_UNKNOWN
    if prior_heat <= 0:
        return HEAT_RISING if recent_heat > 0 else HEAT_UNKNOWN

    ratio = recent_heat / prior_heat
    if ratio >= _RISING_RATIO:
        return HEAT_RISING
    if ratio <= _COOLING_RATIO:
        return HEAT_COOLING
    return HEAT_STABLE


def _detect_state_change(curve: List[TopicHeatPoint]) -> tuple[str, str, str]:
    """Detect state change between the two most recent active points.

    Returns (prev_status, curr_status, change_label).
    """
    active = [p for p in curve if p.candidate_count > 0 or p.signal_count > 0]
    if not active:
        return (TOPIC_STATUS_UNKNOWN, TOPIC_STATUS_UNKNOWN, "持平")
    if len(active) == 1:
        curr = active[-1].topic_status
        return (TOPIC_STATUS_UNKNOWN, curr, "新进入")

    prev = active[-2].topic_status
    curr = active[-1].topic_status
    return (prev, curr, _state_change_label(prev, curr))


# ── Candidate aggregation per topic ──────────────────────────────────

def _aggregate_candidates(candidates: List[dict]) -> List[dict]:
    """Dedup & rank candidates within a topic, keeping display fields."""
    by_symbol: Dict[str, dict] = {}
    for cand in candidates:
        sym = cand.get("symbol", "")
        if not sym:
            continue
        existing = by_symbol.get(sym)
        score = cand.get("mandate_score_component") or cand.get("composite_score") or 0.0
        if existing is None or score > existing.get("_score", 0.0):
            by_symbol[sym] = {
                "symbol": sym,
                "name": cand.get("name", ""),
                "company_role": cand.get("company_role", ""),
                "beneficiary_path": cand.get("beneficiary_path", []),
                "mandate_score": score,
                "tier": cand.get("tier", ""),
                "candidate_type": cand.get("candidate_type", ""),
                "latest_date": _candidate_date(cand),
                "_score": score,
            }

    ranked = sorted(by_symbol.values(), key=lambda c: c.get("_score", 0.0), reverse=True)
    for r in ranked:
        r.pop("_score", None)
    return ranked


def _aggregate_evidence(candidates: List[dict]) -> tuple[List[dict], str]:
    """Collect deduplicated evidence links + a short summary string."""
    seen: List[dict] = []
    seen_keys: set[str] = set()
    titles: List[str] = []
    for cand in candidates:
        refs = cand.get("policy_evidence_refs") or cand.get("mandate_evidence_refs") or []
        for ref in refs:
            title = ref.get("title", "")
            source = ref.get("source", "")
            key = (title, source)
            if not title or key in seen_keys:
                continue
            seen_keys.add(key)
            seen.append({
                "title": title,
                "source": source,
                "date": ref.get("date", ""),
                "url": ref.get("url", ""),
                "source_level": ref.get("source_level", ""),
            })
            if len(titles) < 5:
                titles.append(title)
    summary = "; ".join(titles[:3]) if titles else ""
    return seen[:20], summary


def _aggregate_evidence_gaps(candidates: List[dict]) -> List[str]:
    """Collect unique counter-evidence gaps across the topic's candidates."""
    gaps: List[str] = []
    seen: set[str] = set()
    for cand in candidates:
        for g in (cand.get("blocking_evidence_gaps") or cand.get("watchlist_evidence_gap") or []):
            if g and g not in seen:
                gaps.append(g)
                seen.add(g)
            if len(gaps) >= 8:
                break
    return gaps


def _aggregate_overheat_flags(curve: List[TopicHeatPoint]) -> List[str]:
    flags: List[str] = []
    seen: set[str] = set()
    for p in curve:
        for f in p.overheat_flags:
            if f and f not in seen:
                flags.append(f)
                seen.add(f)
    return flags


# ── Main builder ─────────────────────────────────────────────────────

def build_topic_heatmap(
    candidates: List[dict],
    *,
    as_of: str = "",
    window_days: int = WINDOW_LONG,
    definitions: Optional[list] = None,
) -> TopicHeatmapReport:
    """Build the full topic heatmap report from candidate history.

    Each candidate dict should carry fields like:
      symbol, name, mandate_topic, policy_tags, company_role,
      beneficiary_path, mandate_score_component, composite_score, tier,
      candidate_type, effective_trade_date/trade_date/plan_date,
      policy_evidence_refs/mandate_evidence_refs, overheat_flags,
      topic_lifecycle_state, topic_signal_count, topic_last_signal_date,
      policy_level, blocking_evidence_gaps, watchlist_evidence_gap.

    Args:
        candidates: list of candidate dicts across one or more dates.
        as_of: the report's "today" (YYYY-MM-DD). Defaults to today.
        window_days: max look-back window (default 60).
        definitions: optional topic definitions for matching.
    """
    defs = definitions if definitions is not None else get_default_topic_definitions()
    if not as_of:
        # infer from the latest candidate date, else today
        all_dates = [d for d in (_candidate_date(c) for c in candidates) if d]
        as_of = max(all_dates) if all_dates else _today_str()

    # group candidates by topic
    by_topic: Dict[str, List[dict]] = {}
    for cand in candidates:
        topic = _resolve_topic(cand, defs)
        if not topic:
            continue
        by_topic.setdefault(topic, []).append(cand)

    # include pre-defined topics that have no candidates yet (so dashboard
    # shows the full universe even when quiet)
    def_descriptions: Dict[str, str] = {d.topic: d.description for d in defs}
    for d in defs:
        if d.topic not in by_topic:
            by_topic[d.topic] = []

    entries: List[TopicHeatmapEntry] = []

    for topic, topic_cands in by_topic.items():
        # group by date → daily points
        by_date: Dict[str, List[dict]] = {}
        for cand in topic_cands:
            d = _candidate_date(cand)
            if not d:
                continue
            by_date.setdefault(d, []).append(cand)

        curve: List[TopicHeatPoint] = []
        for date in sorted(by_date.keys()):
            point = _build_daily_point(date, by_date[date])
            curve.append(point)

        # filter curve to the look-back window
        try:
            as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
            window_start = as_of_dt - timedelta(days=window_days)
            curve = [
                p for p in curve
                if _safe_parse_date(p.date) is None or _safe_parse_date(p.date) >= window_start
            ]
        except (ValueError, TypeError):
            pass

        # windowed stats
        windows: Dict[int, WindowStats] = {}
        for w in _WINDOWS:
            windows[w] = _compute_window_stats(curve, w, as_of)

        heat_trend = _detect_heat_trend(curve, as_of)

        prev_status, curr_status, change_label = _detect_state_change(curve)
        state_positive = is_state_change_positive(prev_status, curr_status)

        evidence_links, evidence_summary = _aggregate_evidence(topic_cands)
        counter_gaps = _aggregate_evidence_gaps(topic_cands)
        agg_candidates = _aggregate_candidates(topic_cands)
        agg_flags = _aggregate_overheat_flags(curve)

        latest_date = curve[-1].date if curve else ""
        latest_signal = curve[-1].signal_count if curve else 0

        peak_date = ""
        peak_heat = 0.0
        for p in curve:
            if p.heat >= peak_heat:
                peak_heat = p.heat
                peak_date = p.date

        # latest policy level / status
        latest_point = curve[-1] if curve else None
        topic_status = latest_point.topic_status if latest_point else TOPIC_STATUS_UNKNOWN
        topic_status_label = _TOPIC_STATUS_LABELS.get(topic_status, "未知")
        policy_level = latest_point.policy_level if latest_point else POLICY_LEVEL_UNKNOWN
        policy_level_weight = latest_point.policy_level_weight if latest_point else 0

        entries.append(TopicHeatmapEntry(
            topic=topic,
            description=def_descriptions.get(topic, ""),
            topic_status=topic_status,
            topic_status_label=topic_status_label,
            policy_level=policy_level,
            policy_level_weight=policy_level_weight,
            is_left_side=topic_status in _LEFT_SIDE_STATUSES,
            is_observe_only=topic_status in _OBSERVE_ONLY_STATUSES,
            is_confirmed=topic_status in _CONFIRM_STATUSES,
            heat_curve=curve,
            windows=windows,
            heat_trend=heat_trend,
            heat_trend_label=_HEAT_TREND_LABELS.get(heat_trend, "未知"),
            state_change_label=change_label,
            state_change_positive=state_positive,
            latest_date=latest_date,
            latest_signal_count=latest_signal,
            peak_date=peak_date,
            peak_heat=peak_heat,
            evidence_links=evidence_links,
            evidence_summary=evidence_summary,
            counter_evidence_gaps=counter_gaps,
            overheat_flags=agg_flags,
            candidates=agg_candidates,
            candidate_symbols=[c["symbol"] for c in agg_candidates],
        ))

    # sort: active (has candidates) first, then by latest signal count,
    # then by policy level weight, then by peak heat
    def _sort_key(e: TopicHeatmapEntry) -> tuple:
        return (
            0 if e.candidates else 1,
            -e.latest_signal_count,
            -e.policy_level_weight,
            -e.peak_heat,
        )
    entries.sort(key=_sort_key)

    active = sum(1 for e in entries if e.candidates)
    rising = sum(1 for e in entries if e.heat_trend == HEAT_RISING)
    cooling = sum(1 for e in entries if e.heat_trend == HEAT_COOLING)
    left_side = sum(1 for e in entries if e.is_left_side)
    observe_only = sum(1 for e in entries if e.is_observe_only)

    summary = {
        "as_of": as_of,
        "total_topics": len(entries),
        "active_topics": active,
        "rising_topics": rising,
        "cooling_topics": cooling,
        "left_side_topics": left_side,
        "observe_only_topics": observe_only,
        "windows": [w for w in _WINDOWS],
    }

    return TopicHeatmapReport(
        as_of=as_of,
        window_days=window_days,
        topics=entries,
        total_topics=len(entries),
        active_topics=active,
        rising_topics=rising,
        cooling_topics=cooling,
        left_side_topics=left_side,
        observe_only_topics=observe_only,
        summary=summary,
    )


def _safe_parse_date(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ── Markdown rendering ───────────────────────────────────────────────

def render_topic_heatmap_markdown(report: TopicHeatmapReport) -> str:
    """Render the heatmap report as Markdown."""
    lines: List[str] = []
    lines.append("# 昊天主题热度曲线与政策证据看板")
    lines.append("")
    lines.append(f"> 日期: {report.as_of} | 回看窗口: 近{report.window_days}天 | "
                 f"主题总数: {report.total_topics} | 活跃: {report.active_topics} | "
                 f"升温: {report.rising_topics} | 降温: {report.cooling_topics}")
    lines.append("")

    if not report.topics:
        lines.append("_暂无主题数据。_")
        return "\n".join(lines)

    # ── Overview table ──
    lines.append("## 主题概览")
    lines.append("")
    lines.append("| 主题 | 状态 | 级别 | 热度趋势 | 状态变化 | 候选数 | 证据数 | 反证缺口 |")
    lines.append("|------|------|------|----------|----------|--------|--------|----------|")
    for e in report.topics:
        w7 = e.windows.get(WINDOW_SHORT)
        lines.append(
            f"| {e.topic} | {e.topic_status_label} | {_policy_level_short(e.policy_level)} | "
            f"{e.heat_trend_label} | {e.state_change_label} | "
            f"{w7.candidate_count if w7 else 0} | "
            f"{w7.evidence_count if w7 else 0} | "
            f"{len(e.counter_evidence_gaps)} |"
        )
    lines.append("")

    # ── Per-topic detail ──
    lines.append("## 主题热度详情")
    lines.append("")
    for e in report.topics:
        lines.append(f"### {e.topic}")
        if e.description:
            lines.append(f"_{e.description}_")
        lines.append("")
        lines.append(f"- **状态**: {e.topic_status_label}"
                     f"{'（左侧埋伏）' if e.is_left_side else ''}"
                     f"{'（仅观察）' if e.is_observe_only else ''}")
        lines.append(f"- **政策级别**: {_policy_level_short(e.policy_level)} (权重 {e.policy_level_weight})")
        lines.append(f"- **热度趋势**: {e.heat_trend_label}")
        lines.append(f"- **状态变化**: {e.state_change_label}"
                     + (" ↑" if e.state_change_positive else ""))
        if e.latest_date:
            lines.append(f"- **最新信号日**: {e.latest_date}（信号数 {e.latest_signal_count}）")
        if e.peak_date:
            lines.append(f"- **热度峰值**: {e.peak_date}（heat {e.peak_heat}）")

        # windowed stats
        lines.append("- **窗口统计**:")
        for w in _WINDOWS:
            ws = e.windows.get(w)
            if ws:
                wl = _WINDOW_LABELS.get(w, f"近{w}天")
                lines.append(f"  - {wl}: 信号 {ws.signal_count} | 候选 {ws.candidate_count} "
                             f"(去重 {ws.unique_candidates}) | 证据 {ws.evidence_count} | "
                             f"活跃 {ws.active_days} 天 | 最高级别 {_policy_level_short(ws.max_policy_level)}")

        # heat curve sparkline (text)
        if e.heat_curve:
            lines.append("- **热度曲线**:")
            lines.append("  | 日期 | 候选 | 证据 | 级别 | heat | 状态 |")
            lines.append("  |------|------|------|------|-------|------|")
            for p in e.heat_curve[-10:]:  # last 10 points
                bar = _heat_bar(p.heat)
                lines.append(f"  | {p.date} | {p.candidate_count} | {p.evidence_count} | "
                             f"{_policy_level_short(p.policy_level)} | {bar} {p.heat:.0f} | "
                             f"{p.topic_status_label} |")

        # evidence
        if e.evidence_links:
            lines.append("- **政策证据**:")
            for ref in e.evidence_links[:5]:
                src = ref.get("source", "")
                dt = ref.get("date", "")
                lines.append(f"  - [{dt}] {ref.get('title', '')} ({src})")

        # counter-evidence gaps
        if e.counter_evidence_gaps:
            lines.append(f"- **反证缺口**: {'、'.join(e.counter_evidence_gaps)}")

        # overheat flags
        if e.overheat_flags:
            lines.append(f"- **过热标记**: {'、'.join(e.overheat_flags)}")

        # candidates
        if e.candidates:
            lines.append("- **关联候选**:")
            lines.append("  | 代码 | 名称 | 角色 | 昊天分 | tier |")
            lines.append("  |------|------|------|--------|------|")
            for c in e.candidates[:5]:
                lines.append(f"  | {c.get('symbol', '')} | {c.get('name', '')} | "
                             f"{c.get('company_role', '')} | "
                             f"{c.get('mandate_score', 0):.0f} | {c.get('tier', '')} |")
        else:
            lines.append("- _暂无关联候选_")
        lines.append("")

    # ── Constraints reminder ──
    lines.append("---")
    lines.append("")
    lines.append("> 主题热度仅影响排序解释，不直接改变最终交易动作。")
    return "\n".join(lines)


def _policy_level_short(level: str) -> str:
    return {
        POLICY_LEVEL_CENTRAL: "中央",
        POLICY_LEVEL_MINISTRY: "部委",
        POLICY_LEVEL_LOCAL: "地方",
        POLICY_LEVEL_INDUSTRY: "行业",
        POLICY_LEVEL_MEDIA: "媒体",
        POLICY_LEVEL_UNKNOWN: "未知",
    }.get(level, level or "未知")


def _heat_bar(heat: float) -> str:
    """Render a small text bar for the heat value."""
    n = max(0, min(10, int(heat / 10)))
    return "█" * n + "░" * (10 - n)


# ── File I/O ─────────────────────────────────────────────────────────

_DEFAULT_REPORT_DIR = "docs/topic_heatmap_reports"


def save_topic_heatmap_report(
    report: TopicHeatmapReport,
    output_dir: str = _DEFAULT_REPORT_DIR,
    filename: Optional[str] = None,
) -> str:
    """Save the heatmap report as Markdown. Returns the file path."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    if filename is None:
        filename = f"topic-heatmap-{report.as_of}.md"

    filepath = os.path.join(output_dir, filename)
    content = render_topic_heatmap_markdown(report)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def find_latest_topic_heatmap_report(
    reports_dir: str = _DEFAULT_REPORT_DIR,
) -> Optional[str]:
    """Find the latest topic heatmap report file."""
    if not os.path.isdir(reports_dir):
        return None

    files = []
    for f in os.listdir(reports_dir):
        if not f.startswith("topic-heatmap-") or not f.endswith(".md"):
            continue
        date_part = f.replace("topic-heatmap-", "").replace(".md", "")[:10]
        files.append((date_part, os.path.join(reports_dir, f)))

    if not files:
        return None

    files.sort(key=lambda x: x[0], reverse=True)
    return files[0][1]


# ── Nightly report integration ───────────────────────────────────────

def build_heatmap_section_for_nightly_report(
    candidates: Optional[List[dict]] = None,
    reports_dir: str = _DEFAULT_REPORT_DIR,
) -> str:
    """Build a heatmap section for the nightly auto-dev report.

    If candidate history is provided, generates a fresh report.
    Otherwise, tries to reuse the latest saved report.
    """
    if candidates is not None:
        report = build_topic_heatmap(candidates)
        return _render_compact_section(report)

    report_path = find_latest_topic_heatmap_report(reports_dir)
    if report_path:
        with open(report_path, encoding="utf-8") as f:
            return f.read()

    report = build_topic_heatmap([])
    return _render_compact_section(report)


def _render_compact_section(report: TopicHeatmapReport) -> str:
    lines: List[str] = []
    lines.append("## 昊天主题热度")
    lines.append("")
    lines.append(f"- **日期**: {report.as_of}")
    lines.append(f"- **活跃主题**: {report.active_topics}/{report.total_topics}")
    lines.append(f"- **升温**: {report.rising_topics} | **降温**: {report.cooling_topics}")
    lines.append("")
    if report.topics:
        lines.append("| 主题 | 状态 | 趋势 | 变化 | 候选(7d) |")
        lines.append("|------|------|------|------|----------|")
        for e in report.topics:
            w7 = e.windows.get(WINDOW_SHORT)
            lines.append(
                f"| {e.topic} | {e.topic_status_label} | {e.heat_trend_label} | "
                f"{e.state_change_label} | {w7.candidate_count if w7 else 0} |"
            )
    return "\n".join(lines)


# ── Sample fixtures (低空经济 / 算力 / 半导体设备) ──────────────────
# Multi-date candidate histories so the heatmap, windows and trend logic
# are exercised without touching the live DB or any LLM.

HEATMAP_SAMPLE_CANDIDATES: List[dict] = [
    # ── 低空经济 — rising, fermenting ──
    {
        "symbol": "300034.SZ", "name": "钢研高纳",
        "mandate_topic": "低空经济", "policy_tags": ["低空经济", "eVTOL"],
        "company_role": "动力系统", "beneficiary_path": ["整机", "动力"],
        "mandate_score_component": 68.0, "composite_score": 70.0, "tier": "A",
        "candidate_type": "POLICY_AMBUSH",
        "effective_trade_date": "2026-05-20",
        "policy_evidence_refs": [
            {"title": "工信部召开低空经济座谈会", "source": "工信部",
             "date": "2026-05-18", "source_level": "MINISTRY"},
        ],
        "overheat_flags": [],
        "topic_lifecycle_state": "EMERGING", "topic_signal_count": 2,
        "topic_last_signal_date": "2026-05-18", "policy_level": "MINISTRY",
    },
    {
        "symbol": "300034.SZ", "name": "钢研高纳",
        "mandate_topic": "低空经济", "policy_tags": ["低空经济"],
        "company_role": "动力系统", "beneficiary_path": ["整机", "动力"],
        "mandate_score_component": 72.0, "composite_score": 74.0, "tier": "A",
        "candidate_type": "POLICY_AMBUSH",
        "effective_trade_date": "2026-06-10",
        "policy_evidence_refs": [
            {"title": "多省发布低空经济行动方案", "source": "各省发改委",
             "date": "2026-06-08", "source_level": "LOCAL"},
            {"title": "低空经济产业大会召开", "source": "新华社",
             "date": "2026-06-09", "source_level": "MINISTRY"},
        ],
        "overheat_flags": [],
        "topic_lifecycle_state": "ACCELERATING", "topic_signal_count": 5,
        "topic_last_signal_date": "2026-06-09", "policy_level": "MINISTRY",
    },
    {
        "symbol": "002097.SZ", "name": "众合科技",
        "mandate_topic": "低空经济", "policy_tags": ["低空空域"],
        "company_role": "空管基础设施", "beneficiary_path": ["空管", "通信"],
        "mandate_score_component": 55.0, "composite_score": 58.0, "tier": "B",
        "candidate_type": "POLICY_CONFIRM",
        "effective_trade_date": "2026-06-10",
        "policy_evidence_refs": [
            {"title": "低空空域管理改革试点", "source": "民航局",
             "date": "2026-06-07", "source_level": "MINISTRY"},
        ],
        "overheat_flags": [],
        "topic_lifecycle_state": "ACCELERATING", "topic_signal_count": 5,
        "topic_last_signal_date": "2026-06-09", "policy_level": "MINISTRY",
    },
    # ── 算力 — confirming, stable ──
    {
        "symbol": "300308.SZ", "name": "中际旭创",
        "mandate_topic": "算力", "policy_tags": ["算力", "光模块"],
        "company_role": "光模块", "beneficiary_path": ["光模块", "光互联"],
        "mandate_score_component": 75.0, "composite_score": 78.0, "tier": "A",
        "candidate_type": "POLICY_CONFIRM",
        "effective_trade_date": "2026-05-25",
        "policy_evidence_refs": [
            {"title": "东数西算工程进展", "source": "发改委",
             "date": "2026-05-20", "source_level": "CENTRAL"},
        ],
        "overheat_flags": [],
        "topic_lifecycle_state": "CONFIRMING", "topic_signal_count": 8,
        "topic_last_signal_date": "2026-05-20", "policy_level": "MINISTRY",
    },
    {
        "symbol": "300308.SZ", "name": "中际旭创",
        "mandate_topic": "算力", "policy_tags": ["算力", "智算中心"],
        "company_role": "光模块", "beneficiary_path": ["光模块", "光互联"],
        "mandate_score_component": 78.0, "composite_score": 80.0, "tier": "A",
        "candidate_type": "POLICY_CONFIRM",
        "effective_trade_date": "2026-06-12",
        "policy_evidence_refs": [
            {"title": "智算中心建设指南发布", "source": "工信部",
             "date": "2026-06-10", "source_level": "MINISTRY"},
        ],
        "overheat_flags": ["crowded_consensus_risk"],
        "topic_lifecycle_state": "CONFIRMING", "topic_signal_count": 10,
        "topic_last_signal_date": "2026-06-10", "policy_level": "MINISTRY",
    },
    # ── 半导体设备 — emerging, rising ──
    {
        "symbol": "688012.SH", "name": "中微公司",
        "mandate_topic": "半导体设备", "policy_tags": ["半导体设备", "刻蚀"],
        "company_role": "刻蚀设备", "beneficiary_path": ["刻蚀设备", "核心工艺设备"],
        "mandate_score_component": 65.0, "composite_score": 67.0, "tier": "B",
        "candidate_type": "POLICY_AMBUSH",
        "effective_trade_date": "2026-06-11",
        "policy_evidence_refs": [
            {"title": "大基金三期重点支持半导体设备", "source": "大基金",
             "date": "2026-06-05", "source_level": "MINISTRY"},
        ],
        "overheat_flags": [],
        "topic_lifecycle_state": "EMERGING", "topic_signal_count": 3,
        "topic_last_signal_date": "2026-06-05", "policy_level": "MINISTRY",
    },
]


def get_heatmap_sample_candidates() -> List[dict]:
    """Return a copy of the sample candidate history for fixtures."""
    import copy
    return copy.deepcopy(HEATMAP_SAMPLE_CANDIDATES)
