"""VCP (Volatility Contraction Pattern) / 缩量整理 strategy.

入池逻辑：
- 前期有上涨或趋势基础
- 最近进入横盘/收敛
- 波动区间变窄
- 成交量下降
- 价格没有跌破关键均线
- 距离箱体上沿不远
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ..strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]


@dataclass
class VCPSignal:
    strategy_tag: str = "VCP"
    trigger_price: Optional[float] = None
    support_price: Optional[float] = None
    invalid_price: Optional[float] = None
    score: float = 0.0
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    need_deep_ta: bool = False


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def score_vcp(df: pd.DataFrame, symbol: str = "", cfg: Optional[StrategyConfig] = None) -> Optional[VCPSignal]:  # [M-004]
    """Score a stock for VCP pattern.

    Args:
        df: DataFrame with columns: Date, Open, High, Low, Close, Volume.
            Sorted by date ascending. Should have at least 40 rows.
        symbol: Stock symbol for reporting.
        cfg: StrategyConfig with thresholds. Uses defaults when None.

    Returns:
        VCPSignal if pattern detected, None otherwise.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    if df is None or len(df) < cfg.vcp_min_data_len:
        return None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    latest_close = float(close.iloc[-1])
    latest_vol = float(volume.iloc[-1])

    # ── 1. Trend base: price should be above 60-day MA ──
    ma60 = close.rolling(60).mean()
    if len(ma60.dropna()) == 0:
        return None
    latest_ma60 = float(ma60.dropna().iloc[-1])
    if latest_close < latest_ma60:
        return None

    # ── 2. Recent range contraction ──
    # Compare recent 10-day range vs prior 20-day range
    recent = df.tail(10)
    prior = df.iloc[-30:-10]

    recent_range = float(recent["High"].max() - recent["Low"].min())
    prior_range = float(prior["High"].max() - prior["Low"].min())
    if prior_range == 0:
        return None

    range_ratio = recent_range / prior_range
    if range_ratio > cfg.vcp_range_ratio_max:
        # Not contracting enough
        return None

    # ── 3. Volume declining ──
    recent_avg_vol = float(recent["Volume"].mean())
    prior_avg_vol = float(prior["Volume"].mean())
    if prior_avg_vol == 0:
        return None

    vol_ratio = recent_avg_vol / prior_avg_vol
    if vol_ratio > cfg.vcp_vol_ratio_max:
        # Volume not declining enough
        return None

    # ── 4. Price near top of recent range ──
    recent_high = float(recent["High"].max())
    recent_low = float(recent["Low"].min())
    if recent_high == recent_low:
        return None

    price_position = (latest_close - recent_low) / (recent_high - recent_low)
    if price_position < cfg.vcp_price_position_min:
        # Price closer to bottom than top
        return None

    # ── 5. Not breaking below key MA ──
    ma20 = close.rolling(20).mean()
    if len(ma20.dropna()) > 0 and latest_close < float(ma20.dropna().iloc[-1]) * cfg.vcp_ma20_break_pct:
        return None

    # ── Compute score ──
    score = 0.0
    score += min((1 - range_ratio) * 50, 35)  # more contraction (lower ratio) → higher score
    score += min((1 - vol_ratio) * 30, 25)  # more volume decline = higher
    score += price_position * 20  # closer to top = higher
    score = min(score, cfg.vcp_score_max)

    # trigger = recent high (breakout level)
    trigger_price = round(recent_high, 2)
    support_price = round(recent_low, 2)
    invalid_price = round(float(ma20.dropna().iloc[-1]) * cfg.vcp_ma20_break_pct, 2) if len(ma20.dropna()) > 0 else round(support_price * 0.95, 2)

    # need_deep_ta if score is high (near breakout)
    need_deep_ta = score >= cfg.vcp_need_deep_ta_score or price_position >= cfg.vcp_need_deep_ta_position

    return VCPSignal(
        trigger_price=trigger_price,
        support_price=support_price,
        invalid_price=invalid_price,
        score=round(score, 1),
        reason=f"缩量整理，波动收窄{range_ratio:.0%}，成交量降{vol_ratio:.0%}，等待放量突破确认",
        evidence={
            "range_ratio": round(range_ratio, 3),
            "vol_ratio": round(vol_ratio, 3),
            "price_position": round(price_position, 3),
            "ma60": round(latest_ma60, 2),
            "latest_close": latest_close,
        },
        risk_flags=[],
        need_deep_ta=need_deep_ta,
    )
