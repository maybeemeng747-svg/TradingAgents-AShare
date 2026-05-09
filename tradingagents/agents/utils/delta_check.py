# [C-005] delta_check
"""同股票结论翻转检测"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional


DELTA_LOG_DIR = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    "portfolio", "analysis", "delta_log"
)


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


def check_delta(stock_code: str, new_conclusion: str, new_data_sources: list[str]) -> Optional[dict]:
    """
    检查结论是否翻转。
    返回 None 表示无翻转，否则返回翻转信息 dict。
    触发条件：结论翻转 且 72 小时内没有新增数据源。
    """
    last = load_last_conclusion(stock_code)
    if last is None:
        return None

    last_ts = datetime.fromisoformat(last["timestamp"])
    now = datetime.now()
    hours_since = (now - last_ts).total_seconds() / 3600

    if hours_since > 72:
        return None

    last_conclusion = last["conclusion"]
    last_sources = set(last.get("data_sources", []))
    new_sources = set(new_data_sources)

    # 判断结论是否翻转（简单方向判断）
    bullish_keywords = ["看多", "偏多", "买入", "BUY", "ENTER"]
    bearish_keywords = ["看空", "偏空", "卖出", "SELL", "EXIT", "回避"]

    def _direction(text):
        text_lower = text.lower()
        has_bull = any(kw in text_lower for kw in bullish_keywords)
        has_bear = any(kw in text_lower for kw in bearish_keywords)
        if has_bull and not has_bear:
            return "bullish"
        if has_bear and not has_bull:
            return "bearish"
        return "neutral"

    last_dir = _direction(last_conclusion)
    new_dir = _direction(new_conclusion)

    if last_dir == new_dir or last_dir == "neutral" or new_dir == "neutral":
        return None

    # 结论翻转了，检查是否有新增数据源
    has_new_data = len(new_sources - last_sources) > 0

    return {
        "stock_code": stock_code,
        "last_conclusion": last_conclusion,
        "new_conclusion": new_conclusion,
        "last_timestamp": last["timestamp"],
        "new_timestamp": now.isoformat(),
        "has_new_data": has_new_data,
        "new_data_added": list(new_sources - last_sources),
        "possible_noise": not has_new_data,
    }


def format_delta_warning(delta_info: dict) -> str:
    """格式化翻转警告信息"""
    if delta_info is None:
        return ""

    warning = (
        f"\n\n⚠️ [C-005] 同股票结论翻转警告\n"
        f"股票：{delta_info['stock_code']}\n"
        f"上一版结论：{delta_info['last_conclusion'][:200]}\n"
        f"本版结论：{delta_info['new_conclusion'][:200]}\n"
        f"变化时间：{delta_info['last_timestamp']} → {delta_info['new_timestamp']}\n"
    )

    if delta_info["has_new_data"]:
        warning += f"新增数据源：{', '.join(delta_info['new_data_added'])}\n"
        warning += "结论：有新数据支撑，翻转合理。\n"
    else:
        warning += "新增数据源：无\n"
        warning += "⚠️ 可能是模型噪音，建议人工复核。\n"

    return warning
