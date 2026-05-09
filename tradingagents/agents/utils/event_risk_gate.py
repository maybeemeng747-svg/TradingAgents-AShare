# [C-007] event_risk_gate
"""重大事件风控门禁"""

import logging
from datetime import datetime, timedelta
from typing import Optional

_logger = logging.getLogger(__name__)

# 需要触发风控优先模式的事件类型
RISK_EVENTS = {
    "large_unlock": "大比例解禁（解禁比例 > 流通股本 5%）",
    "major_ma": "重大并购/重组",
    "high_leverage": "高杠杆（资产负债率 > 80%）",
    "suspension": "停复牌",
    "earnings_crash": "业绩暴雷（净利润下降 > 50%）",
}


def check_event_risk(
    stock_code: str,
    unlock_ratio: Optional[float] = None,
    debt_ratio: Optional[float] = None,
    net_profit_change: Optional[float] = None,
    is_suspended: bool = False,
    has_ma_event: bool = False,
) -> dict:
    """
    检查是否存在重大风险事件。

    返回:
        {
            "has_risk": bool,
            "risk_events": list[str],
            "risk_details": dict,
            "block_open": bool,  # 是否阻止开仓
        }
    """
    risk_events = []
    risk_details = {}

    # 检查解禁
    if unlock_ratio is not None and unlock_ratio > 0.05:
        risk_events.append("large_unlock")
        risk_details["large_unlock"] = f"解禁比例 {unlock_ratio:.1%} > 5%"

    # 检查高杠杆
    if debt_ratio is not None and debt_ratio > 0.80:
        risk_events.append("high_leverage")
        risk_details["high_leverage"] = f"资产负债率 {debt_ratio:.1%} > 80%"

    # 检查业绩暴雷
    if net_profit_change is not None and net_profit_change < -0.50:
        risk_events.append("earnings_crash")
        risk_details["earnings_crash"] = f"净利润下降 {abs(net_profit_change):.1%} > 50%"

    # 检查停复牌
    if is_suspended:
        risk_events.append("suspension")
        risk_details["suspension"] = "当前处于停牌状态"

    # 检查重大并购
    if has_ma_event:
        risk_events.append("major_ma")
        risk_details["major_ma"] = "存在重大并购/重组事件"

    has_risk = len(risk_events) > 0
    # 有风险事件时阻止开仓
    block_open = has_risk

    if has_risk:
        _logger.warning(
            "[C-007] event_risk_gate: %s 检测到风险事件: %s",
            stock_code,
            ", ".join(risk_events)
        )

    return {
        "has_risk": has_risk,
        "risk_events": risk_events,
        "risk_details": risk_details,
        "block_open": block_open,
    }


def format_event_risk_warning(risk_info: dict) -> str:
    """格式化事件风险警告"""
    if not risk_info["has_risk"]:
        return ""

    warning = "\n\n🚨 [C-007] 事件风控警告\n"

    for event_type in risk_info["risk_events"]:
        desc = RISK_EVENTS.get(event_type, event_type)
        detail = risk_info["risk_details"].get(event_type, "")
        warning += f"- {desc}\n"
        if detail:
            warning += f"  详情：{detail}\n"

    if risk_info["block_open"]:
        warning += "\n⚠️ 风控优先模式：不建议开仓，等待事件落地。\n"
        warning += "如需特批开仓，需主控 AI 审批。\n"

    return warning
