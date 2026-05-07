from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse

import requests

if TYPE_CHECKING:
    from api.database import ReportDB

logger = logging.getLogger(__name__)
_BARK_OFFICIAL_HOST = "api.day.app"
_BARK_DEFAULT_GROUP = "TradingAgents"


def _clip_text(text: str | None, limit: int = 720) -> str:
    if not text:
        return ""
    compact = " ".join(str(text).split()).strip()
    return compact[:limit]


def normalize_bark_url(value: str) -> str:
    """Normalize a Bark device key or endpoint URL to a HTTPS endpoint."""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Bark 地址不能为空")

    if not normalized.startswith(("http://", "https://")):
        if not all(char.isalnum() or char in "-_" for char in normalized):
            raise ValueError("Bark 设备 Key 格式不正确")
        return f"https://{_BARK_OFFICIAL_HOST}/{normalized}"

    parsed = urlparse(normalized)
    if parsed.scheme != "https":
        raise ValueError("Bark 地址必须使用 HTTPS")
    if not parsed.netloc:
        raise ValueError("Bark 地址格式不正确")
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ValueError("Bark 地址缺少设备 Key")
    if parsed.params or parsed.fragment:
        raise ValueError("Bark 地址格式不正确")
    key = path_parts[0]
    if not all(char.isalnum() or char in "-_" for char in key):
        raise ValueError("Bark 设备 Key 格式不正确")
    return f"https://{parsed.netloc}/{key}"


def build_report_payload(report: "ReportDB") -> dict:
    title = f"TradingAgents {report.symbol} 定时分析"
    lines = [
        f"交易日：{report.trade_date}",
    ]
    if getattr(report, "decision", None):
        lines.append(f"决策：{report.decision}")
    if getattr(report, "direction", None):
        lines.append(f"方向：{report.direction}")
    if getattr(report, "confidence", None) is not None:
        lines.append(f"置信度：{report.confidence}%")
    if getattr(report, "target_price", None) is not None:
        lines.append(f"目标价：{report.target_price}")
    if getattr(report, "stop_loss_price", None) is not None:
        lines.append(f"止损价：{report.stop_loss_price}")

    summary = (
        _clip_text(getattr(report, "final_trade_decision", None), 700)
        or _clip_text(getattr(report, "trader_investment_plan", None), 700)
        or _clip_text(getattr(report, "investment_plan", None), 700)
    )
    if summary:
        lines.append("")
        lines.append(summary)
    return {
        "title": title[:128],
        "body": "\n".join(lines)[:1800],
        "group": _BARK_DEFAULT_GROUP,
    }


def build_test_payload(content: str | None = None) -> dict:
    body = _clip_text(content, 500) or "这是一条 TradingAgents Bark 推送测试消息。"
    return {
        "title": "TradingAgents Bark 测试",
        "body": body,
        "group": _BARK_DEFAULT_GROUP,
    }


def send_message(payload: dict, bark_url: str) -> bool:
    url = normalize_bark_url(bark_url)
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    try:
        body = response.json()
    except Exception:
        logger.warning("[bark] non-JSON response body=%s", _clip_text(getattr(response, "text", None), 240))
        return False

    code = body.get("code")
    if code is None:
        return True
    try:
        return int(code) == 200
    except (TypeError, ValueError):
        return False


async def send_report_message_with_retry(report: "ReportDB", bark_url: str) -> bool:
    payload = build_report_payload(report)
    try:
        ok = await asyncio.to_thread(send_message, payload, bark_url)
        if ok:
            logger.info("[bark] sent OK for %s", report.symbol)
            return True
    except Exception as exc:
        logger.warning("[bark] first send failed for %s: %s", report.symbol, exc)

    await asyncio.sleep(15)
    try:
        ok = await asyncio.to_thread(send_message, payload, bark_url)
        if ok:
            logger.info("[bark] retry sent OK for %s", report.symbol)
            return True
    except Exception as exc:
        logger.error("[bark] retry failed for %s: %s", report.symbol, exc)
    return False


def mask_bark_url(bark_url: str | None) -> str | None:
    normalized = str(bark_url or "").strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.scheme:
        key = normalized
        return f"{key[:4]}{'*' * max(6, len(key) - 8)}{key[-4:]}" if len(key) > 8 else "******"
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        return f"{parsed.scheme}://{parsed.netloc}/******"
    key = path_parts[0]
    masked_key = f"{key[:4]}{'*' * max(6, len(key) - 8)}{key[-4:]}" if len(key) > 8 else "******"
    suffix = "/".join(quote(part) for part in path_parts[1:])
    suffix_part = f"/{suffix}" if suffix else ""
    return f"{parsed.scheme}://{parsed.netloc}/{masked_key}{suffix_part}"
