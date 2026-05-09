# [C-004] action_enum
"""交易动作枚举定义"""

from enum import Enum


class TradeAction(Enum):
    """交易动作枚举 — 5 个标准动作"""
    WAIT = "WAIT"           # 观望/等条件（含回避，备注说明原因）
    ENTER = "ENTER"         # 建仓
    HOLD = "HOLD"           # 持有
    REDUCE = "REDUCE"       # 减仓/止损/止盈
    EXIT = "EXIT"           # 清仓


# 动作描述
ACTION_DESCRIPTIONS = {
    TradeAction.WAIT: "观望，等待更好的入场条件",
    TradeAction.ENTER: "建仓买入",
    TradeAction.HOLD: "继续持有",
    TradeAction.REDUCE: "减仓或止损",
    TradeAction.EXIT: "清仓离场",
}

# 持仓状态与允许的动作
POSITION_STATE_ACTIONS = {
    False: [TradeAction.WAIT, TradeAction.ENTER],              # 未持仓：只能观望或建仓
    True: [TradeAction.HOLD, TradeAction.REDUCE, TradeAction.EXIT],  # 已持仓：持有、减仓、清仓
}

# 旧动作 → 新动作映射（兼容现有 prompt 输出）
ACTION_MAPPING = {
    "BUY": TradeAction.ENTER,
    "SELL": TradeAction.EXIT,
    "HOLD": TradeAction.HOLD,
    "买入": TradeAction.ENTER,
    "卖出": TradeAction.EXIT,
    "观望": TradeAction.WAIT,
    "减仓": TradeAction.REDUCE,
    "清仓": TradeAction.EXIT,
    "止损": TradeAction.REDUCE,
    "止盈": TradeAction.REDUCE,
}


def validate_action(action_str: str, has_position: bool) -> TradeAction:
    """校验动作是否合法，返回合法的 TradeAction"""
    action_str_upper = action_str.strip().upper()

    # 尝试直接匹配
    for action in TradeAction:
        if action.value == action_str_upper:
            return action

    # 尝试中文匹配
    mapped = ACTION_MAPPING.get(action_str.strip())
    if mapped:
        return mapped

    # 尝试模糊匹配
    for key, action in ACTION_MAPPING.items():
        if key in action_str:
            return action

    # 默认返回 WAIT
    return TradeAction.WAIT
