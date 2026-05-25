"""Event Catalyst / 事件催化 strategy.

入池逻辑：
- 近期有公告、业绩预告、季报、回购、增持、中标、订单、并购等事件
- 事件被分类为利好/利空/不确定
- 需要市场价格确认

P0 简化版：基于简单规则检测（不调用 LLM），仅匹配关键词模式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EventCatalystSignal:
    strategy_tag: str = "EVENT_CATALYST"
    trigger_price: Optional[float] = None
    support_price: Optional[float] = None
    invalid_price: Optional[float] = None
    score: float = 0.0
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    need_deep_ta: bool = False
    event_type: str = ""
    event_direction: str = "neutral"  # bullish / bearish / neutral
    verification_condition: str = ""


# Event keyword patterns for P0
EVENT_PATTERNS = [
    ("earnings_preview", "业绩预告", "bullish", 60),
    ("quarterly_report", "季报披露", "neutral", 50),
    ("buyback", "回购", "bullish", 55),
    ("insider_buy", "增持", "bullish", 55),
    ("contract", "中标|大单|签约", "bullish", 50),
    ("merger", "并购|重组", "bullish", 65),
    ("dividend", "分红|派息", "bullish", 40),
    ("delist_warning", r"退市风险|\*ST", "bearish", 80),
    ("regulation", "处罚|监管警示", "bearish", 60),
]


def score_event_catalyst(
    symbol: str,
    news_texts: Optional[list[str]] = None,
    event_overrides: Optional[list[dict]] = None,
    latest_close: float = 0.0,
) -> Optional[EventCatalystSignal]:
    """Score a stock for event-driven catalyst.

    P0: Uses keyword matching on provided text. Does NOT call any LLM.

    Args:
        symbol: Stock symbol.
        news_texts: List of news/announcement text snippets.
        event_overrides: List of {"event_type", "direction", "title"} dicts
                        for manual event specification.
        latest_close: Latest close price for trigger/invalid calculation.

    Returns:
        EventCatalystSignal if catalyst detected, None otherwise.
    """
    import re

    detected_events = []

    # Check overrides first — only use overrides whose symbol matches,
    # or overrides without a symbol field (global manual events).
    if event_overrides:
        for ev in event_overrides:
            ev_symbol = ev.get("symbol")
            if ev_symbol is not None and ev_symbol != symbol:
                continue
            detected_events.append({
                "type": ev.get("event_type", "custom"),
                "title": ev.get("title", ""),
                "direction": ev.get("direction", "neutral"),
                "score": ev.get("score", 50),
            })

    # Check news texts with keyword patterns
    if news_texts:
        for text in news_texts:
            if not text:
                continue
            for event_type, pattern, direction, base_score in EVENT_PATTERNS:
                if re.search(pattern, text):
                    detected_events.append({
                        "type": event_type,
                        "title": text[:80],
                        "direction": direction,
                        "score": base_score,
                    })

    if not detected_events:
        return None

    # Aggregate: take the highest-impact event
    best = max(detected_events, key=lambda x: x["score"])
    all_types = list({e["type"] for e in detected_events})
    has_bearish = any(e["direction"] == "bearish" for e in detected_events)
    has_bullish = any(e["direction"] == "bullish" for e in detected_events)

    direction = best["direction"]
    if has_bearish and has_bullish:
        direction = "mixed"

    score = min(best["score"], 80)

    risk_flags = []
    if has_bearish:
        risk_flags.append("含利空事件")

    # Price-based triggers (simplified for P0)
    trigger_price = round(latest_close * 1.03, 2) if latest_close > 0 else None
    invalid_price = round(latest_close * 0.95, 2) if latest_close > 0 else None

    need_deep_ta = score >= 60 or direction in ("mixed", "neutral")

    verification = ""
    if direction == "bullish":
        verification = "需放量确认突破"
    elif direction == "bearish":
        verification = "需观察是否止跌企稳"
    else:
        verification = "需价格与成交量验证方向"

    return EventCatalystSignal(
        trigger_price=trigger_price,
        support_price=None,
        invalid_price=invalid_price,
        score=score,
        reason=f"有事件催化({best['type']})，但需价格与成交量验证",
        evidence={
            "events": detected_events,
            "event_count": len(detected_events),
            "best_event": best["type"],
        },
        risk_flags=risk_flags,
        need_deep_ta=need_deep_ta,
        event_type=best["type"],
        event_direction=direction,
        verification_condition=verification,
    )
