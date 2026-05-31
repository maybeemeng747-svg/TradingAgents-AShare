"""Parse broker position screenshots into structured position data.

Uses the generic VLM service (vlm_service.py) for image recognition.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from api.services.vlm_service import call_vlm

logger = logging.getLogger(__name__)

POSITION_PROMPT = """你是一个股票截图解析助手。用户会上传券商 App 的截图（可能是自选股列表、持仓页面、或其他包含股票信息的页面）。
请从图片中提取所有能识别到的 A 股股票信息，返回 JSON 数组，每个元素包含：
- symbol: 股票代码（6位数字，如 "600519"）
- name: 股票名称
- current_position: 持仓数量（股），如果图中没有则为 null
- average_cost: 成本价（元），如果图中没有则为 null
- market_value: 持仓市值（元），如果图中没有则为 null

注意：流通市值不是持仓市值，如果只看到流通市值请忽略该字段。
只返回 JSON 数组，不要有其他文字。如果图片中没有任何股票信息，返回空数组 []。

请解析这张截图中的股票信息。"""

# [VLM-001] watchlist_table_parser
WATCHLIST_TABLE_PROMPT = """你是一个投资分析表格解析助手。用户会上传一张投资分析/自选候选表格的截图，包含以下列：
股票代码、股票名称、核心业务、所属板块、利好度、市场共识度。

请从图片中提取所有股票信息，返回 JSON 数组，每个元素包含：
- symbol: 股票代码（6位纯数字，去掉后缀，如 "002371"）
- name: 股票名称（如 "北方华创"）
- business: 核心业务（如 "半导体刻蚀、沉积等关键设备"）
- sector: 所属板块（如 "半导体设备"）
- bullish_score: 利好度，浮点数（如 9.3）
- consensus: 市场共识度，整数（如 92）

注意：
1. 股票代码只保留6位数字，去掉 .SZ/.SH 等后缀。
2. 如果是 ETF，也要提取，代码同样只保留6位数字。
3. 利好度和共识度如果缺失则为 null。
4. 只返回 JSON 数组，不要有其他文字。如果图片中没有任何股票信息，返回空数组 []。

请解析这张表格截图中的股票信息。"""


def parse_position_image(
    image_bytes: bytes,
    content_type: str,
) -> list[dict[str, Any]]:
    """Parse a broker screenshot and return extracted positions."""
    raw = call_vlm(image_bytes, POSITION_PROMPT, content_type)
    return _parse_response(raw)


# [VLM-001] watchlist_table_parser
def parse_watchlist_table_image(
    image_bytes: bytes,
    content_type: str,
) -> list[dict[str, Any]]:
    """Parse a watchlist candidate table screenshot and return structured data with notes."""
    raw = call_vlm(image_bytes, WATCHLIST_TABLE_PROMPT, content_type)
    return _parse_watchlist_response(raw)


def _parse_response(raw: str) -> list[dict[str, Any]]:
    """Extract JSON array from VLM response, tolerating markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("[vlm-parser] Failed to parse VLM response as JSON: %s", text[:200])
        return []

    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            continue
        result.append({
            "symbol": symbol,
            "name": item.get("name"),
            "current_position": _to_float(item.get("current_position")),
            "average_cost": _to_float(item.get("average_cost")),
            "market_value": _to_float(item.get("market_value")),
        })
    return result


# [VLM-001] watchlist_table_parser
def _parse_watchlist_response(raw: str) -> list[dict[str, Any]]:
    """Extract JSON array from VLM watchlist table response and build notes."""
    text = _strip_markdown_fences(raw)
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("[vlm-parser] Failed to parse watchlist VLM response as JSON: %s", text[:200])
        return []

    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip()
        symbol = _extract_6digit_code(symbol)
        if not symbol:
            continue
        name = item.get("name")
        business = item.get("business")
        sector = item.get("sector")
        bullish_score = _to_float(item.get("bullish_score"))
        consensus = _to_int(item.get("consensus"))
        notes = _build_watchlist_notes(sector, business, bullish_score, consensus)

        result.append({
            "symbol": symbol,
            "name": name,
            "business": business,
            "sector": sector,
            "bullish_score": bullish_score,
            "consensus": consensus,
            "notes": notes,
        })
    return result


# [VLM-001] watchlist_table_parser
def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return text


# [VLM-001] watchlist_table_parser
def _extract_6digit_code(symbol: str) -> str:
    digits = re.sub(r"\D", "", symbol)
    return digits[:6] if len(digits) >= 6 else digits


# [VLM-001] watchlist_table_parser
def _build_watchlist_notes(
    sector: str | None,
    business: str | None,
    bullish_score: float | None,
    consensus: int | None,
) -> str:
    parts = []
    if sector:
        parts.append(sector)
    if business:
        parts.append(business[:20])
    if bullish_score is not None:
        parts.append(f"利好{bullish_score:g}")
    if consensus is not None:
        parts.append(f"共识{consensus}")
    return "｜".join(parts)


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# [VLM-001] watchlist_table_parser
def _to_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None
