# [M-006] gated_deep_ta_dispatch
"""Gated Deep TA Dispatch — decides whether to invoke deep TA analysis
after an intraday observe trigger.

Responsibilities:
1. Check executable conditions before dispatching TA.
2. Enforce daily hard cap on TA dispatch count.
3. Block forbidden models (e.g. DeepSeek) by default; DeepSeek requires the
   explicit authorization switch ``deep_ta_deepseek_authorized`` (see
   [CONFIG-DS-SCHEDULE-R1] in strategy_config).
4. Record dispatch reason, model, duration, report path.
5. Limit retries on failure — no infinite retry loops.
6. Require position_context for all dispatches.

Design constraints:
- Default: do NOT call DeepSeek or any blocked model. DeepSeek is only
  unblocked when the config explicitly sets deep_ta_deepseek_authorized=True.
- Daily TA deep analysis count has a hard upper limit.
- need_deep_ta=False → never dispatch.
- Exceeds daily limit → never dispatch.
- Failures are recorded but not retried beyond max_retries.
- Does NOT actually call any LLM — only produces a dispatch decision
  and audit trail.  The caller (OpenClaw / scheduler) is responsible
  for executing the TA if allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]


class DeepTAStatus(str, Enum):
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    DISPATCHED = "DISPATCHED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class DeepTARecord:
    symbol: str
    status: DeepTAStatus = DeepTAStatus.PENDING
    dispatch_reason: str = ""
    block_reason: str = ""
    model: str = ""
    dispatch_time: str = ""
    duration_sec: float = 0.0
    report_path: str = ""
    retry_count: int = 0
    position_context: str = ""
    composite_score: float = 0.0
    observe_state: str = ""
    completeness: float = 0.0
    trade_date: str = ""

    def __post_init__(self):
        if not self.trade_date:
            self.trade_date = datetime.now().strftime("%Y-%m-%d")


@dataclass
class DeepTADecision:
    allowed: bool = False
    reason: str = ""
    model: str = ""
    record: Optional[DeepTARecord] = None


@dataclass
class DeepTADispatcher:
    trade_date: str = ""
    daily_count: int = 0
    daily_limit: int = 3
    blocked_models: tuple = ("deepseek",)  # [CONFIG-DS-SCHEDULE-R1] default block restored; explicit authorization required to lift
    # [CONFIG-DS-SCHEDULE-R1] DeepSeek explicit authorization is carried on the
    # dispatcher itself and enforced by check_deep_ta_gate, so direct
    # construction (not just from_config) cannot bypass the paid-model gate.
    deepseek_authorized: bool = False
    default_model: str = ""
    max_retries: int = 1
    min_composite_score: float = 40.0
    min_completeness: float = 0.5
    require_observe_triggered: bool = True
    records: dict[str, DeepTARecord] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if not self.trade_date:
            self.trade_date = datetime.now().strftime("%Y-%m-%d")

    @classmethod
    def from_config(cls, cfg: Optional[StrategyConfig] = None) -> "DeepTADispatcher":
        if cfg is None:
            cfg = DEFAULT_STRATEGY_CONFIG
        deepseek_authorized = bool(getattr(cfg, "deep_ta_deepseek_authorized", False))
        blocked_models = tuple(getattr(cfg, "deep_ta_blocked_models", ("deepseek",)))
        # [CONFIG-DS-SCHEDULE-R1] DeepSeek explicit authorization gate:
        # - unauthorized (default): keep "deepseek" blocked even if a config
        #   accidentally dropped it — fail closed, never implicitly unblocked.
        # - authorized: drop only "deepseek"; every other blocked entry stays.
        if deepseek_authorized:
            blocked_models = tuple(
                m for m in blocked_models if str(m).lower() != "deepseek"
            )
        elif "deepseek" not in {str(m).lower() for m in blocked_models}:
            blocked_models = blocked_models + ("deepseek",)
        return cls(
            daily_limit=getattr(cfg, "deep_ta_daily_limit", 3),
            blocked_models=blocked_models,
            deepseek_authorized=deepseek_authorized,
            default_model=getattr(cfg, "deep_ta_default_model", ""),
            max_retries=getattr(cfg, "deep_ta_max_retries", 1),
            min_composite_score=getattr(cfg, "deep_ta_min_composite_score", 40.0),
            min_completeness=getattr(cfg, "deep_ta_min_completeness", 0.5),
            require_observe_triggered=getattr(cfg, "deep_ta_require_observe_triggered", True),
        )


def check_deep_ta_gate(
    dispatcher: DeepTADispatcher,
    symbol: str,
    need_deep_ta: bool,
    observe_state: str,
    composite_score: float = 0.0,
    completeness: float = 0.0,
    position_context: str = "unknown",
    tier: str = "",
    model: str = "",
    retry_count: int = 0,
) -> DeepTADecision:
    """Check all gates before allowing a deep TA dispatch.

    Returns a DeepTADecision with allowed=True only if all gates pass.
    """
    record = DeepTARecord(
        symbol=symbol,
        position_context=position_context,
        composite_score=composite_score,
        observe_state=observe_state,
        completeness=completeness,
        trade_date=dispatcher.trade_date,
    )

    if not need_deep_ta:
        record.status = DeepTAStatus.BLOCKED
        record.block_reason = "need_deep_ta=False"
        return DeepTADecision(allowed=False, reason="need_deep_ta=False", record=record)

    if dispatcher.daily_count >= dispatcher.daily_limit:
        record.status = DeepTAStatus.BLOCKED
        record.block_reason = f"每日次数上限({dispatcher.daily_limit}次)已满"
        return DeepTADecision(allowed=False, reason=record.block_reason, record=record)

    if dispatcher.require_observe_triggered and observe_state != "TRIGGERED":
        record.status = DeepTAStatus.BLOCKED
        record.block_reason = f"盘中观察状态非TRIGGERED(当前:{observe_state})"
        return DeepTADecision(allowed=False, reason=record.block_reason, record=record)

    if composite_score < dispatcher.min_composite_score:
        record.status = DeepTAStatus.BLOCKED
        record.block_reason = f"综合分{composite_score:.1f}低于阈值{dispatcher.min_composite_score:.1f}"
        return DeepTADecision(allowed=False, reason=record.block_reason, record=record)

    if completeness < dispatcher.min_completeness:
        record.status = DeepTAStatus.BLOCKED
        record.block_reason = f"证据完整度{completeness:.0%}低于阈值{dispatcher.min_completeness:.0%}"
        return DeepTADecision(allowed=False, reason=record.block_reason, record=record)

    resolved_model = model or dispatcher.default_model
    if not resolved_model:
        record.status = DeepTAStatus.BLOCKED
        record.block_reason = "未指定模型(default_model也为空)"
        return DeepTADecision(allowed=False, reason=record.block_reason, record=record)

    model_lower = resolved_model.lower()
    # [CONFIG-DS-SCHEDULE-R1] DeepSeek requires explicit authorization carried
    # on the dispatcher itself. Enforced here in the gate — not only in
    # from_config — so direct construction with a cleared blocked_models tuple
    # still cannot bypass the paid-model protection.
    if "deepseek" in model_lower and not dispatcher.deepseek_authorized:
        record.status = DeepTAStatus.BLOCKED
        record.block_reason = (
            f"模型'{resolved_model}'未获DeepSeek显式授权"
            "(deep_ta_deepseek_authorized=False)"
        )
        return DeepTADecision(allowed=False, reason=record.block_reason, record=record)
    for blocked in dispatcher.blocked_models:
        if blocked and blocked.lower() in model_lower:
            record.status = DeepTAStatus.BLOCKED
            record.block_reason = f"模型'{resolved_model}'被默认屏蔽"
            return DeepTADecision(allowed=False, reason=record.block_reason, record=record)

    sym_failures = dispatcher.failure_counts.get(symbol, 0)
    if sym_failures + retry_count > dispatcher.max_retries:
        record.status = DeepTAStatus.BLOCKED
        record.block_reason = f"失败次数({sym_failures + retry_count})超过最大重试({dispatcher.max_retries})"
        return DeepTADecision(allowed=False, reason=record.block_reason, record=record)

    if not position_context or position_context == "unknown":
        record.position_context = "unknown"

    record.status = DeepTAStatus.PENDING
    record.model = resolved_model
    record.retry_count = retry_count

    reason_parts = [
        f"综合分{composite_score:.1f}",
        f"完整度{completeness:.0%}",
        f"观察状态={observe_state}",
        f"持仓={position_context}",
    ]
    if tier:
        reason_parts.append(f"分层={tier}")
    if retry_count > 0:
        reason_parts.append(f"重试#{retry_count}")

    return DeepTADecision(
        allowed=True,
        reason="; ".join(reason_parts),
        model=resolved_model,
        record=record,
    )


def record_deep_ta_dispatch(
    dispatcher: DeepTADispatcher,
    symbol: str,
    status: DeepTAStatus,
    model: str = "",
    duration_sec: float = 0.0,
    report_path: str = "",
    dispatch_reason: str = "",
    position_context: str = "unknown",
) -> DeepTARecord:
    """Record a deep TA dispatch result and update dispatcher state.

    Increments daily count on DISPATCHED and failure count on FAILED.
    """
    now_iso = datetime.now().isoformat()

    existing = dispatcher.records.get(symbol)
    if existing is None:
        record = DeepTARecord(
            symbol=symbol,
            status=status,
            model=model,
            dispatch_time=now_iso,
            duration_sec=duration_sec,
            report_path=report_path,
            dispatch_reason=dispatch_reason,
            position_context=position_context,
            trade_date=dispatcher.trade_date,
        )
    else:
        record = existing
        record.status = status
        if model:
            record.model = model
        if duration_sec > 0:
            record.duration_sec = duration_sec
        if report_path:
            record.report_path = report_path
        if dispatch_reason:
            record.dispatch_reason = dispatch_reason
        if position_context and position_context != "unknown":
            record.position_context = position_context
        record.dispatch_time = now_iso

    dispatcher.records[symbol] = record

    if status == DeepTAStatus.DISPATCHED:
        dispatcher.daily_count += 1
    elif status == DeepTAStatus.FAILED:
        cur = dispatcher.failure_counts.get(symbol, 0)
        dispatcher.failure_counts[symbol] = cur + 1

    return record


def can_retry_deep_ta(
    dispatcher: DeepTADispatcher,
    symbol: str,
    retry_count: int = 0,
) -> bool:
    """Check if a failed deep TA dispatch can be retried."""
    sym_failures = dispatcher.failure_counts.get(symbol, 0)
    return sym_failures + retry_count <= dispatcher.max_retries
