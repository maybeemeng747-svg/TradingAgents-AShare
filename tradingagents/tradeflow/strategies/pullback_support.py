"""Pullback to Support / 回踩支撑 strategy.

入池逻辑：
- 中期趋势未破（价格在60日均线上方）
- 回踩 20日/30日/60日线或箱体下沿
- 回调缩量
- 最近出现止跌迹象
- 支撑位明确
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class PullbackSupportSignal:
    strategy_tag: str = "PULLBACK_SUPPORT"
    trigger_price: Optional[float] = None
    support_price: Optional[float] = None
    invalid_price: Optional[float] = None
    score: float = 0.0
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    need_deep_ta: bool = False


def score_pullback_support(df: pd.DataFrame, symbol: str = "") -> Optional[PullbackSupportSignal]:
    """Score a stock for pullback-to-support pattern.

    Args:
        df: DataFrame with Date, Open, High, Low, Close, Volume.
        symbol: Stock symbol for reporting.

    Returns:
        PullbackSupportSignal if pattern detected, None otherwise.
    """
    if df is None or len(df) < 60:
        return None

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)

    latest_close = float(close.iloc[-1])

    # ── 1. Mid-term trend intact: price above 60 MA ──
    ma60 = close.rolling(60).mean()
    if len(ma60.dropna()) == 0:
        return None
    latest_ma60 = float(ma60.dropna().iloc[-1])
    if latest_close < latest_ma60:
        return None  # trend broken

    # ── 2. Recent pullback (price dropped from recent high) ──
    # Look at last 20 days
    recent_20 = df.tail(20)
    high_20 = float(recent_20["High"].max())
    pullback_pct = (latest_close - high_20) / high_20

    # Must have pulled back at least 3% but not more than 15%
    if pullback_pct > -0.03 or pullback_pct < -0.20:
        return None

    # ── 3. Volume declining during pullback ──
    # Compare last 5 days volume to prior 10 days
    vol_last5 = float(df.tail(5)["Volume"].mean())
    vol_prior10 = float(df.iloc[-15:-5]["Volume"].mean())
    if vol_prior10 == 0:
        return None
    vol_ratio = vol_last5 / vol_prior10
    if vol_ratio > 1.0:
        # Volume increasing during pullback — not ideal
        return None

    # ── 4. Support level identification ──
    ma20 = close.rolling(20).mean()
    ma30 = close.rolling(30).mean()

    support_price = None
    support_label = ""

    # Check proximity to MAs
    for label, ma_series in [("MA20", ma20), ("MA30", ma30), ("MA60", ma60)]:
        if len(ma_series.dropna()) > 0:
            ma_val = float(ma_series.dropna().iloc[-1])
            distance_pct = abs(latest_close - ma_val) / latest_close
            if distance_pct < 0.03 and ma_val <= latest_close * 1.02:
                support_price = round(ma_val, 2)
                support_label = label
                break

    # Also check prior low as support
    if support_price is None:
        prior_low = float(df.tail(30)["Low"].min())
        if latest_close >= prior_low and (latest_close - prior_low) / latest_close < 0.05:
            support_price = round(prior_low, 2)
            support_label = "30日低点"

    if support_price is None:
        return None  # No clear support identified

    # ── 5. Signs of stabilization (recent candles) ──
    last3 = df.tail(3)
    # Check if recent candles have small bodies (doji-like) or bullish
    bodies = (last3["Close"].astype(float) - last3["Open"].astype(float)).abs()
    ranges = (last3["High"].astype(float) - last3["Low"].astype(float))
    avg_body_ratio = float((bodies / ranges.replace(0, 1)).mean())

    # At least some sign of stabilization (small bodies or bullish)
    recent_bullish = float((last3["Close"].astype(float) >= last3["Open"].astype(float)).sum())
    stabilizing = avg_body_ratio < 0.6 or recent_bullish >= 2

    if not stabilizing:
        return None

    # ── Compute score ──
    score = 0.0
    # Closer to support = better
    support_distance = (latest_close - support_price) / latest_close
    score += max(0, (1 - support_distance * 20)) * 25
    # Volume declining
    score += min((1 - vol_ratio) * 30, 25)
    # Not too deep pullback
    score += max(0, (1 + pullback_pct) * 20)
    # Stabilization
    score += 15
    score = min(score, 80)

    trigger_price = round(latest_close * 1.02, 2)  # reclaims 2% above current
    invalid_price = round(support_price * 0.97, 2)  # 3% below support

    need_deep_ta = score >= 60 or support_distance < 0.01

    return PullbackSupportSignal(
        trigger_price=trigger_price,
        support_price=support_price,
        invalid_price=invalid_price,
        score=round(score, 1),
        reason=f"趋势内回踩至{support_label}({support_price})附近，缩量企稳，风险位清楚",
        evidence={
            "pullback_pct": round(pullback_pct, 4),
            "vol_ratio": round(vol_ratio, 3),
            "support_level": support_label,
            "support_price": support_price,
            "latest_close": latest_close,
            "ma60": round(latest_ma60, 2),
        },
        risk_flags=[],
        need_deep_ta=need_deep_ta,
    )
