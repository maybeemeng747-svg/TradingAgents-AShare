# [TRACK-004] observation_state_engine
"""
观察仓入场区/失效区规则引擎与状态流转.

根据实时价、买入区间 (entry_low/entry_high)、触发价 (trigger_price)、
失效价 (invalid_price) 以及数据新鲜度信号 (live_price / quote_time)，为单个
观察仓标的计算"派生状态"，并产出可直接写入 tracking board v2 ``today_guidance``
的结构化结果。

设计原则
--------
1. 纯函数：不访问数据库、不联网、不写状态。输入一个 observation item dict
   （已经由 ``_enrich_observation_items`` 注入 ``live_price`` 等行情字段），
   输出一个状态结果 dict。便于单测与回放。
2. 安全红线：
   - 永不输出"立即买入 / 重仓 / 清仓"等强动作词；本引擎只描述事实状态。
   - 数据缺失时只能返回 ``data_missing`` / ``needs_review``，绝不能把缺数据
     的标的误判为可入场（硬约束，写在判断链最前面）。
3. 状态优先级（高 -> 低）：
   data_missing / needs_review  >  invalidated  >  in_entry_zone  >
   ta_required(stored)  >  near_entry  >  missed_entry  >  watching
4. 每个结果都带 ``reason`` 与 ``data_fields``（参与判断的数据字段），满足
   TRACK-004 "每个状态必须有 reason 和使用的数据字段" 的要求。

该模块不持久化状态；如需把失效/错失等终态写回观察仓，调用方应使用
``api.services.tradeflow_service.mark_observation_item_status``。
"""

from __future__ import annotations

from typing import Any

# [TRACK-004] observation_state_engine
# ── 派生状态枚举（字符串常量） ────────────────────────────────────────────
STATE_DATA_MISSING = "data_missing"        # 交易日无实时行情，无法判断
STATE_NEEDS_REVIEW = "needs_review"        # 非交易日无行情，需人工复核
STATE_WATCHING = "watching"                 # 持续观察，价格未接近买点
STATE_NEAR_ENTRY = "near_entry"             # 接近买点下沿（5% 内）
STATE_IN_ENTRY_ZONE = "in_entry_zone"       # 价格进入买入区间
STATE_MISSED_ENTRY = "missed_entry"         # 价格已偏离上沿 >5%，本轮窗口可能已过
STATE_INVALIDATED = "invalidated"           # 跌破失效价
STATE_TA_REQUIRED = "ta_required"           # 用户标记需要 TA 深度确认

# 可出现在 today_guidance 的 guidance_type 前缀统一为 observation_*
GUIDANCE_TYPE_DATA_MISSING = "observation_data_missing"
GUIDANCE_TYPE_NEEDS_REVIEW = "observation_needs_review"
GUIDANCE_TYPE_WATCHING = "observation_watching"
GUIDANCE_TYPE_NEAR_ENTRY = "observation_near_entry"
GUIDANCE_TYPE_IN_ENTRY_ZONE = "observation_in_entry_zone"
GUIDANCE_TYPE_MISSED_ENTRY = "observation_missed_entry"
GUIDANCE_TYPE_INVALIDATED = "observation_invalidated"
GUIDANCE_TYPE_TA_REQUIRED = "observation_ta_required"

# 接近买点下沿的容差（entry_low 下方 5% 内算 near_entry）
NEAR_ENTRY_BAND = 0.05
# 偏离买点上沿的阈值（entry_high 上方 5% 外算 missed_entry）
MISSED_ENTRY_BAND = 0.05

# 强动作禁用词，供调用方/测试核验引擎输出不含违规词
FORBIDDEN_STRONG_WORDS = ("立即买入", "重仓买入", "重仓", "立即清仓", "清仓", "满仓", "梭哈")


def _to_float(value: Any) -> float:
    """宽松转 float，None/异常返回 0.0（保持 0 边界语义）。"""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _result(
    base: dict[str, Any],
    state: str,
    priority: str,
    guidance_type: str,
    reason: str,
    used: dict[str, Any],
) -> dict[str, Any]:
    """构造一个状态结果 dict。"""
    return {
        "symbol": base.get("symbol", ""),
        "name": base.get("name", base.get("symbol", "")),
        "state": state,
        "priority": priority,
        "guidance_type": guidance_type,
        "reason": reason,
        # 参与本次判断的数据字段（满足"每个状态必须有使用的数据字段"）
        "data_fields": dict(used),
        # 背景上下文（便于审计/调试，不计入 data_fields）
        "stored_status": base.get("stored_status", "watching"),
        "live_price": base.get("live_price"),
        "entry_low": base.get("entry_low", 0.0),
        "entry_high": base.get("entry_high", 0.0),
        "invalid_price": base.get("invalid_price", 0.0),
        "trigger_price": base.get("trigger_price", 0.0),
        "quote_time": base.get("quote_time"),
        "quote_source": base.get("quote_source"),
        "is_trading_day": bool(base.get("is_trading_day", True)),
    }


# [TRACK-004] observation_state_engine
def evaluate_observation_state(
    item: dict[str, Any],
    *,
    is_trading_day: bool = True,
) -> dict[str, Any]:
    """评估单个观察仓标的的派生状态.

    Args:
        item: observation item dict，需含 ``symbol``，可选 ``name/status/live_price/
            entry_low/entry_high/trigger_price/invalid_price/quote_time/quote_source``。
            ``live_price`` 为 ``None`` 表示无实时行情。
        is_trading_day: 当前是否为 A 股交易日。仅影响无行情时的状态归类
            (交易日 -> data_missing / 非交易日 -> needs_review)。

    Returns:
        状态结果 dict，键见 :func:`_result`。永远返回 dict，不返回 None。
    """
    symbol = (item.get("symbol") or "").strip() or "(unknown)"
    name = item.get("name") or symbol
    stored_status = item.get("status") or "watching"
    live_price = item.get("live_price")  # None | float
    entry_low = _to_float(item.get("entry_low", 0.0))
    entry_high = _to_float(item.get("entry_high", 0.0))
    invalid_price = _to_float(item.get("invalid_price", 0.0))
    trigger_price = _to_float(item.get("trigger_price", 0.0))
    quote_time = item.get("quote_time")
    quote_source = item.get("quote_source")

    base = {
        "symbol": symbol,
        "name": name,
        "stored_status": stored_status,
        "live_price": live_price,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "invalid_price": invalid_price,
        "trigger_price": trigger_price,
        "quote_time": quote_time,
        "quote_source": quote_source,
        "is_trading_day": is_trading_day,
    }

    # 1. 数据缺失 —— 硬约束：无行情时绝不能误判为可入场
    if live_price is None:
        if is_trading_day:
            return _result(
                base,
                STATE_DATA_MISSING,
                "P3",
                GUIDANCE_TYPE_DATA_MISSING,
                f"观察仓 {symbol} 无实时行情，无法判断入场区间",
                used={"live_price": None, "is_trading_day": True},
            )
        return _result(
            base,
            STATE_NEEDS_REVIEW,
            "P3",
            GUIDANCE_TYPE_NEEDS_REVIEW,
            f"观察仓 {symbol} 非交易日无实时行情，需盘后人工复核",
            used={"live_price": None, "is_trading_day": False},
        )

    has_zone = entry_low > 0 or entry_high > 0

    # 2. 失效 —— 现价已跌破失效价
    if invalid_price > 0 and live_price <= invalid_price:
        return _result(
            base,
            STATE_INVALIDATED,
            "P1",
            GUIDANCE_TYPE_INVALIDATED,
            f"观察仓 {symbol} 现价 {live_price} 已跌破失效价 {invalid_price}",
            used={"live_price": live_price, "invalid_price": invalid_price},
        )

    # 3. 进入买入区间
    if has_zone:
        in_zone = True
        if entry_low > 0 and live_price < entry_low:
            in_zone = False
        if entry_high > 0 and live_price > entry_high:
            in_zone = False

        if in_zone:
            return _result(
                base,
                STATE_IN_ENTRY_ZONE,
                "P0",
                GUIDANCE_TYPE_IN_ENTRY_ZONE,
                f"观察仓 {symbol} 现价 {live_price} 进入买入区间 [{entry_low}, {entry_high}]",
                used={
                    "live_price": live_price,
                    "entry_low": entry_low,
                    "entry_high": entry_high,
                },
            )

        # 4. 错失入场 —— 现价高于买入区上沿超过 5%
        if entry_high > 0 and live_price > entry_high * (1 + MISSED_ENTRY_BAND):
            return _result(
                base,
                STATE_MISSED_ENTRY,
                "P2",
                GUIDANCE_TYPE_MISSED_ENTRY,
                f"观察仓 {symbol} 现价 {live_price} 已高于买入区上沿 {entry_high} 超过 5%，本轮入场窗口可能已过",
                used={"live_price": live_price, "entry_high": entry_high},
            )

        # 5. 接近买点 —— 现价落在买点下沿下方 5% 内
        if entry_low > 0 and entry_low * (1 - NEAR_ENTRY_BAND) <= live_price < entry_low:
            return _result(
                base,
                STATE_NEAR_ENTRY,
                "P1",
                GUIDANCE_TYPE_NEAR_ENTRY,
                f"观察仓 {symbol} 现价 {live_price} 接近买点下沿 {entry_low}",
                used={"live_price": live_price, "entry_low": entry_low},
            )

    # 6. 用户标记需要 TA 深度确认
    if stored_status == "ta_required":
        return _result(
            base,
            STATE_TA_REQUIRED,
            "P1",
            GUIDANCE_TYPE_TA_REQUIRED,
            f"观察仓 {symbol} 需要 TA 深度确认",
            used={"stored_status": stored_status},
        )

    # 7. 默认：持续观察
    suffix = f"，现价 {live_price} 暂未接近买入区间" if has_zone else "，暂未设定买入区间"
    return _result(
        base,
        STATE_WATCHING,
        "P3",
        GUIDANCE_TYPE_WATCHING,
        f"观察仓 {symbol} 持续观察中{suffix}",
        used={"live_price": live_price, "has_zone": has_zone},
    )


# [TRACK-004] observation_state_engine
def result_to_guidance(result: dict[str, Any], *, as_of: str, source: str = "tracking_board_v2") -> dict[str, Any]:
    """把引擎结果 dict 转成 today_guidance 的一条记录."""
    return {
        "type": result.get("guidance_type", "observation_watching"),
        "priority": result.get("priority", "P3"),
        "symbol": result.get("symbol", ""),
        "name": result.get("name", ""),
        "reason": result.get("reason", ""),
        "source": source,
        "as_of": as_of,
        "state": result.get("state", "watching"),
        "data_fields": dict(result.get("data_fields", {})),
    }


# [TRACK-004] observation_state_engine
def should_emit_guidance(result: dict[str, Any]) -> bool:
    """决定引擎结果是否应当写入 today_guidance.

    所有非 watching 状态都应当发出；watching 仅在已设定买入区间时发出
    （让看板能展示"仍在观察、价格未到"的完整视图），未设定区间的纯 watching
    不发出，避免噪声。
    """
    state = result.get("state")
    if state != STATE_WATCHING:
        return True
    # watching：仅当 data_fields.has_zone 为真时才发出
    used = result.get("data_fields", {}) or {}
    return bool(used.get("has_zone", False))
