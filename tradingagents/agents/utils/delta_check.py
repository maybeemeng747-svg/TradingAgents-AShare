# [C-005] delta_check
"""同股票结论翻转检测

功能：
- 检测同一股票在 72 小时窗口内结论方向翻转
- 翻转时输出对比信息：上一版结论、本版结论、变化方向、新增证据、是否可能噪音
- 支持使用 TradeAction 枚举进行方向判断
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from tradingagents.agents.utils.trade_actions import TradeAction

_logger = logging.getLogger(__name__)

DELTA_LOG_DIR = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    "portfolio", "analysis", "delta_log"
)

# ── 方向关键词映射 ──
_BULLISH_KEYWORDS = [
    "看多", "偏多", "买入", "建仓", "加仓", "积极",
    "BUY", "ENTER", "BULLISH", "LONG",
]
_BEARISH_KEYWORDS = [
    "看空", "偏空", "卖出", "清仓", "减仓", "回避", "止损",
    "SELL", "EXIT", "BEARISH", "SHORT", "REDUCE",
]


def _ensure_delta_log_dir():
    """确保 delta_log 目录存在"""
    os.makedirs(DELTA_LOG_DIR, exist_ok=True)


def _get_delta_log_path(stock_code: str) -> str:
    """获取某只股票的 delta_log 文件路径"""
    return os.path.join(DELTA_LOG_DIR, f"{stock_code}.json")


def load_last_conclusion(stock_code: str) -> Optional[dict]:
    """加载上一次的结论"""
    path = _get_delta_log_path(stock_code)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, IOError):
        return None


def save_conclusion(stock_code: str, conclusion: str, confidence: str, data_sources: list[str]):
    """保存本次结论"""
    _ensure_delta_log_dir()
    path = _get_delta_log_path(stock_code)
    record = {
        "stock_code": stock_code,
        "conclusion": conclusion,
        "confidence": confidence,
        "data_sources": data_sources,
        "timestamp": datetime.now().isoformat(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def _extract_direction(text: str) -> str:
    """从结论文本中提取方向判断。

    使用 TradeAction 枚举进行标准化判断，同时保留中文关键词兼容。

    Returns:
        "bullish" / "bearish" / "neutral"
    """
    if not text:
        return "neutral"

    text_upper = text.upper()

    # 使用 TradeAction 枚举判断
    has_enter = TradeAction.ENTER.value in text_upper
    has_reduce = TradeAction.REDUCE.value in text_upper
    has_exit = TradeAction.EXIT.value in text_upper
    has_wait = TradeAction.WAIT.value in text_upper

    # 中文关键词判断
    has_bull = any(kw in text for kw in _BULLISH_KEYWORDS) or has_enter
    has_bear = any(kw in text for kw in _BEARISH_KEYWORDS) or has_reduce or has_exit

    # 如果同时有看多和看空信号，根据强度判断
    if has_bull and has_bear:
        # 看看是否有更强的信号
        strong_bull = any(kw in text for kw in ["强烈", "积极", "建议买入", "建议建仓"])
        strong_bear = any(kw in text for kw in ["止损", "清仓", "建议卖出", "建议减仓"])
        if strong_bear:
            return "bearish"
        if strong_bull:
            return "bullish"
        return "neutral"

    if has_bull:
        return "bullish"
    if has_bear:
        return "bearish"
    return "neutral"


def _extract_action_summary(text: str) -> str:
    """从结论文本中提取动作摘要。

    Returns:
        动作描述字符串，如 "ENTER (建仓)" / "HOLD (持有)" / "混合" / "无明确动作"
    """
    if not text:
        return "无明确动作"

    text_upper = text.upper()

    # 优先检查 TradeAction 枚举
    for action in TradeAction:
        if action.value in text_upper:
            from tradingagents.agents.utils.trade_actions import ACTION_DESCRIPTIONS
            return f"{action.value} ({ACTION_DESCRIPTIONS.get(action, '')})"

    # 中文关键词匹配
    action_mapping = {
        "买入": "ENTER", "建仓": "ENTER", "加仓": "ENTER",
        "卖出": "EXIT", "清仓": "EXIT",
        "减仓": "REDUCE", "止损": "REDUCE", "止盈": "REDUCE",
        "持有": "HOLD", "观望": "WAIT",
    }

    found_actions = set()
    for cn_keyword, action in action_mapping.items():
        if cn_keyword in text:
            found_actions.add(action)

    if len(found_actions) == 1:
        action = found_actions.pop()
        from tradingagents.agents.utils.trade_actions import ACTION_DESCRIPTIONS, TradeAction as TA
        ta = TA(action)
        return f"{action} ({ACTION_DESCRIPTIONS.get(ta, '')})"
    elif len(found_actions) > 1:
        return "混合"

    return "无明确动作"


def check_delta(stock_code: str, new_conclusion: str, new_data_sources: list[str]) -> Optional[dict]:
    """
    检查结论是否翻转。

    返回 None 表示无翻转，否则返回翻转信息 dict。

    触发条件：结论翻转 且 72 小时内。

    翻转判断逻辑：
    - 提取上一次和本次结论的方向（bullish/bearish/neutral）
    - neutral 不触发翻转检测
    - 方向相同不触发
    - 方向相反触发翻转
    """
    last = load_last_conclusion(stock_code)
    if last is None:
        return None

    last_ts = datetime.fromisoformat(last["timestamp"])
    now = datetime.now()
    hours_since = (now - last_ts).total_seconds() / 3600

    # 72 小时窗口
    if hours_since > 72:
        return None

    last_conclusion = last["conclusion"]
    last_sources = set(last.get("data_sources", []))
    new_sources = set(new_data_sources)

    last_dir = _extract_direction(last_conclusion)
    new_dir = _extract_direction(new_conclusion)

    # neutral 不触发翻转检测
    if last_dir == "neutral" or new_dir == "neutral":
        return None

    # 方向相同不触发
    if last_dir == new_dir:
        return None

    # 结论翻转了，检查是否有新增数据源
    has_new_data = len(new_sources - last_sources) > 0
    new_data_added = list(new_sources - last_sources)

    # 提取动作摘要
    last_action = _extract_action_summary(last_conclusion)
    new_action = _extract_action_summary(new_conclusion)

    return {
        "stock_code": stock_code,
        "last_conclusion": last_conclusion,
        "new_conclusion": new_conclusion,
        "last_direction": last_dir,
        "new_direction": new_dir,
        "last_action": last_action,
        "new_action": new_action,
        "last_timestamp": last["timestamp"],
        "new_timestamp": now.isoformat(),
        "hours_since_last": round(hours_since, 1),
        "has_new_data": has_new_data,
        "new_data_added": new_data_added,
        "possible_noise": not has_new_data,
    }


def format_delta_warning(delta_info: dict) -> str:
    """格式化翻转警告信息。

    输出内容：
    - 股票代码
    - 方向变化（如 看多 → 看空）
    - 动作变化（如 ENTER → EXIT）
    - 上一版结论摘要
    - 本版结论摘要
    - 变化时间
    - 新增证据
    - 是否可能噪音
    """
    if delta_info is None:
        return ""

    # 方向中文映射
    dir_cn = {
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
    }

    last_dir_cn = dir_cn.get(delta_info["last_direction"], delta_info["last_direction"])
    new_dir_cn = dir_cn.get(delta_info["new_direction"], delta_info["new_direction"])

    # 截取结论摘要（避免过长）
    last_summary = delta_info["last_conclusion"][:300]
    new_summary = delta_info["new_conclusion"][:300]

    warning = (
        f"\n\n⚠️ [C-005] 同股票结论翻转警告\n"
        f"股票：{delta_info['stock_code']}\n"
        f"方向变化：{last_dir_cn} → {new_dir_cn}\n"
        f"动作变化：{delta_info['last_action']} → {delta_info['new_action']}\n"
        f"上一版结论：{last_summary}\n"
        f"本版结论：{new_summary}\n"
        f"变化时间：{delta_info['last_timestamp']} → {delta_info['new_timestamp']}\n"
        f"距上次：{delta_info['hours_since_last']} 小时\n"
    )

    if delta_info["has_new_data"]:
        warning += f"新增数据源：{', '.join(delta_info['new_data_added'])}\n"
        warning += "结论：有新数据支撑，翻转可能合理。\n"
    else:
        warning += "新增数据源：无\n"
        warning += "⚠️ 可能是模型噪音，建议人工复核。\n"

    return warning
