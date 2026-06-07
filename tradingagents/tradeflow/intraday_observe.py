# [M-005] intraday_observe_state
"""Intraday Observe State Machine for TradeFlow candidates.

Responsibilities:
1. Define observe states: WAITING / TRIGGERED / INVALIDATED / EXPIRED.
2. Check current price/volume against trigger_price and invalid_price from
   pre-market candidates.
3. Record raw price-volume evidence and trigger reason for each check.
4. Enforce a per-symbol daily trigger cap to prevent noise.
5. [T-004] Detect abnormal volume spikes (volume anomaly).

Design constraints:
- Low-frequency checks (every 30 minutes or less).
- Does NOT auto-push to Feishu.
- Does NOT auto-invoke deep TA.
- Each check saves raw price-volume evidence and trigger reason.
- Single-day trigger count capped per symbol.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG

logger = logging.getLogger(__name__)


class ObserveState(str, Enum):
    WAITING = "WAITING"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass
class ObserveSnapshot:
    symbol: str
    observe_state: ObserveState
    check_time: str = ""
    current_price: Optional[float] = None
    current_volume: Optional[float] = None
    current_amount: Optional[float] = None
    trigger_price: Optional[float] = None
    invalid_price: Optional[float] = None
    trigger_reason: str = ""
    price_evidence: dict = field(default_factory=dict)
    daily_trigger_count: int = 0
    volume_anomaly: bool = False
    volume_ratio: Optional[float] = None

    def __post_init__(self):
        if not self.check_time:
            self.check_time = datetime.now().isoformat()


@dataclass
class ObserveTracker:
    symbol: str
    state: ObserveState = ObserveState.WAITING
    trigger_price: Optional[float] = None
    invalid_price: Optional[float] = None
    daily_trigger_count: int = 0
    check_history: list[ObserveSnapshot] = field(default_factory=list)
    trade_date: str = ""
    first_trigger_time: Optional[str] = None
    first_invalidation_time: Optional[str] = None

    def __post_init__(self):
        if not self.trade_date:
            self.trade_date = datetime.now().strftime("%Y-%m-%d")


def detect_volume_anomaly(
    current_volume: Optional[float],
    avg_volume: Optional[float] = None,
    cfg: Optional[StrategyConfig] = None,
) -> tuple[bool, Optional[float]]:
    """[T-004] Detect if current volume is abnormally high.

    Args:
        current_volume: Current session volume.
        avg_volume: Average volume baseline (e.g. 20-day avg). If None, no anomaly check.
        cfg: Strategy config for threshold.

    Returns:
        (is_anomaly, volume_ratio) where volume_ratio = current / avg.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG
    threshold: float = getattr(cfg, "observe_volume_anomaly_ratio", 2.0)

    if current_volume is None or avg_volume is None or avg_volume <= 0:
        return False, None

    ratio = current_volume / avg_volume
    return ratio >= threshold, round(ratio, 2)


def run_observe_check(
    tracker: ObserveTracker,
    current_price: float,
    current_volume: Optional[float] = None,
    current_amount: Optional[float] = None,
    avg_volume: Optional[float] = None,
    cfg: Optional[StrategyConfig] = None,
) -> ObserveSnapshot:
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    max_daily_triggers: int = getattr(cfg, "observe_max_daily_triggers", 3)

    if tracker.state in (ObserveState.TRIGGERED, ObserveState.INVALIDATED, ObserveState.EXPIRED):
        return _make_terminal_snapshot(tracker, current_price, current_volume, current_amount)

    trigger_price = tracker.trigger_price
    invalid_price = tracker.invalid_price
    reason = ""
    new_state = ObserveState.WAITING
    price_evidence: dict = {}

    vol_anomaly, vol_ratio = detect_volume_anomaly(current_volume, avg_volume, cfg)

    if invalid_price is not None and current_price <= invalid_price:
        new_state = ObserveState.INVALIDATED
        reason = f"价格{current_price:.2f}跌破失效价{invalid_price:.2f}"
        price_evidence = {
            "current_price": current_price,
            "invalid_price": invalid_price,
            "breach_pct": round((invalid_price - current_price) / invalid_price * 100, 2) if invalid_price else 0,
            "current_volume": current_volume,
            "current_amount": current_amount,
        }
        tracker.state = new_state
        tracker.first_invalidation_time = tracker.first_invalidation_time or datetime.now().isoformat()
    elif trigger_price is not None and current_price >= trigger_price:
        if tracker.daily_trigger_count < max_daily_triggers:
            new_state = ObserveState.TRIGGERED
            tracker.daily_trigger_count += 1
            rise_pct = (current_price - trigger_price) / trigger_price * 100 if trigger_price else 0
            reason = f"价格{current_price:.2f}突破触发价{trigger_price:.2f}(+{rise_pct:.2f}%)"
            if vol_anomaly:
                reason += f" 放量{vol_ratio:.1f}倍"
            price_evidence = {
                "current_price": current_price,
                "trigger_price": trigger_price,
                "rise_pct": round(rise_pct, 2),
                "current_volume": current_volume,
                "current_amount": current_amount,
            }
            tracker.state = new_state
            tracker.first_trigger_time = tracker.first_trigger_time or datetime.now().isoformat()
        else:
            reason = f"已达到单日触发上限({max_daily_triggers}次)，价格{current_price:.2f}虽突破触发价{trigger_price:.2f}但不再触发"
    elif vol_anomaly:
        reason = f"放量异常: 成交量{vol_ratio:.1f}倍于均量"

    snapshot = ObserveSnapshot(
        symbol=tracker.symbol,
        observe_state=new_state,
        current_price=current_price,
        current_volume=current_volume,
        current_amount=current_amount,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        trigger_reason=reason,
        price_evidence=price_evidence,
        daily_trigger_count=tracker.daily_trigger_count,
        volume_anomaly=vol_anomaly,
        volume_ratio=vol_ratio,
    )
    tracker.check_history.append(snapshot)
    return snapshot


def expire_tracker(tracker: ObserveTracker) -> ObserveSnapshot:
    if tracker.state not in (ObserveState.TRIGGERED, ObserveState.INVALIDATED):
        tracker.state = ObserveState.EXPIRED
    snapshot = ObserveSnapshot(
        symbol=tracker.symbol,
        observe_state=tracker.state,
        trigger_reason="收盘到期",
        daily_trigger_count=tracker.daily_trigger_count,
    )
    tracker.check_history.append(snapshot)
    return snapshot


def _make_terminal_snapshot(
    tracker: ObserveTracker,
    current_price: float,
    current_volume: Optional[float],
    current_amount: Optional[float],
) -> ObserveSnapshot:
    reason = ""
    if tracker.state == ObserveState.TRIGGERED:
        reason = "已触发，等待盘后处理"
    elif tracker.state == ObserveState.INVALIDATED:
        reason = "已失效"
    elif tracker.state == ObserveState.EXPIRED:
        reason = "已到期"

    snapshot = ObserveSnapshot(
        symbol=tracker.symbol,
        observe_state=tracker.state,
        current_price=current_price,
        current_volume=current_volume,
        current_amount=current_amount,
        trigger_price=tracker.trigger_price,
        invalid_price=tracker.invalid_price,
        trigger_reason=reason,
        daily_trigger_count=tracker.daily_trigger_count,
    )
    tracker.check_history.append(snapshot)
    return snapshot
