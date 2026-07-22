# [M-010] feishu_webhook_notification
"""飞书 Incoming Webhook 机器人消息推送 service.

通过飞书群机器人 webhook 发送通知卡片。与 ``feishu_export_service``
（云文档导出）不同，本模块用于即时消息推送。

环境变量::

    FEISHU_WEBHOOK_URL     — 飞书群机器人 webhook 地址（必填）
    FEISHU_WEBHOOK_ENABLED — 是否启用（默认 true）

设计契约：

- 不读取 / 打印 webhook secret（URL 中的 sign 参数只做格式校验）。
- 不写数据库，不调 LLM，不改 prompts。
- 失败返回结构化 ``{"success": False, "error": ...}``，不抛异常。
- 卡片消息优先（富文本），纯文本兜底。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import base64
import json
import logging
import os
import time
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_FEISHU_WEBHOOK_HOST = "open.feishu.cn"
_FEISHU_WEBHOOK_PATH = "/open-apis/bot/v2/hook/"


# ── Env helpers ──────────────────────────────────────────────────────────────

def is_feishu_webhook_enabled() -> bool:
    """Check if feishu webhook is configured and enabled."""
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        return False
    enabled = os.getenv("FEISHU_WEBHOOK_ENABLED", "true").strip().lower()
    return enabled in ("1", "true", "yes", "on")


def get_feishu_webhook_url() -> str | None:
    """Return configured webhook URL (plaintext, never logged)."""
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    return url or None


# ── URL validation ───────────────────────────────────────────────────────────

def normalize_webhook_url(webhook_url: str) -> str:
    """Validate and normalize a Feishu webhook URL.

    Accepts:
    - Full URL: ``https://open.feishu.cn/open-apis/bot/v2/hook/{token}``
    - Token only: ``{token}`` → expanded to full URL

    Raises ``ValueError`` on invalid input.
    """
    normalized = str(webhook_url or "").strip()
    if not normalized:
        raise ValueError("飞书 Webhook 不能为空")

    # Token-only input
    if not normalized.startswith(("http://", "https://")):
        if not all(c.isalnum() or c in "-_" for c in normalized):
            raise ValueError("飞书 Webhook token 格式不正确")
        return f"https://{_FEISHU_WEBHOOK_HOST}{_FEISHU_WEBHOOK_PATH}{normalized}"

    parsed = urlparse(normalized)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("飞书 Webhook 必须使用 HTTPS")
    if parsed.scheme == "http":
        # Upgrade to https
        normalized = "https" + normalized[4:]
        parsed = urlparse(normalized)
    if parsed.netloc != _FEISHU_WEBHOOK_HOST:
        raise ValueError("仅支持飞书官方 Webhook 地址 (open.feishu.cn)")
    if not parsed.path.startswith(_FEISHU_WEBHOOK_PATH):
        raise ValueError("飞书 Webhook 路径格式不正确")
    token = parsed.path[len(_FEISHU_WEBHOOK_PATH):].strip("/")
    if not token or not all(c.isalnum() or c in "-_" for c in token):
        raise ValueError("飞书 Webhook token 格式不正确")
    return normalized


def mask_webhook_url(webhook_url: str | None) -> str | None:
    """Mask webhook URL for safe logging (hides the token)."""
    normalized = str(webhook_url or "").strip()
    if not normalized:
        return None
    if not normalized.startswith(("http://", "https://")):
        token = normalized
        if len(token) > 8:
            return f"{token[:4]}{'*' * max(4, len(token) - 8)}{token[-4:]}"
        return "******"
    parsed = urlparse(normalized)
    path = parsed.path
    token_start = path.rfind("/")
    if token_start > 0:
        token = path[token_start + 1:]
        if len(token) > 8:
            masked = f"{token[:4]}{'*' * max(4, len(token) - 8)}{token[-4:]}"
        else:
            masked = "******"
        return f"{parsed.scheme}://{parsed.netloc}{path[:token_start]}/{masked}"
    return f"{parsed.scheme}://{parsed.netloc}/******"


# ── Sign support ─────────────────────────────────────────────────────────────

def _compute_sign(secret: str, timestamp: int) -> str:
    """Compute Feishu webhook signature (when sign secret is configured)."""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


# ── Message builders ─────────────────────────────────────────────────────────

def build_card_message(
    title: str,
    content_lines: list[str],
    *,
    color: str = "blue",
) -> dict:
    """Build a Feishu interactive card message.

    Colors: blue (default), green, red, orange, grey.
    """
    elements = []
    content = "\n".join(content_lines)
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": content},
    })
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title[:128]},
                "template": color,
            },
            "elements": elements,
        },
    }


def build_text_message(content: str) -> dict:
    """Build a plain text message (fallback)."""
    return {
        "msg_type": "text",
        "content": {"text": content[:4000]},
    }


def build_report_card(
    symbol: str,
    trade_date: str,
    action_label: str | None = None,
    direction: str | None = None,
    execution_action: str | None = None,
    confidence: int | None = None,
    summary: str | None = None,
    risk_items: list[dict] | None = None,
    target_price: float | None = None,
    stop_loss_price: float | None = None,
) -> dict:
    """Build a report notification card from ReportDB fields."""
    title = f"TradingAgents 分析完成 | {symbol}"
    lines = [f"**交易日**：{trade_date}"]

    if action_label:
        lines.append(f"**动作**：{action_label}")
    if direction:
        lines.append(f"**方向**：{direction}")
    if execution_action:
        lines.append(f"**动作码**：{execution_action}")
    if confidence is not None:
        lines.append(f"**置信度**：{confidence}%")

    if target_price or stop_loss_price:
        price_parts = []
        if target_price:
            price_parts.append(f"目标 {target_price:.2f}")
        if stop_loss_price:
            price_parts.append(f"止损 {stop_loss_price:.2f}")
        lines.append(f"**价位**：{' / '.join(price_parts)}")

    if summary:
        clipped = summary[:500]
        lines.append("")
        lines.append(f"**摘要**：{clipped}")

    if risk_items:
        lines.append("")
        lines.append("**风险提示**：")
        for item in risk_items[:3]:
            level = item.get("level", "medium")
            name = item.get("name", "")
            desc = item.get("description", "")
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(level, "⚪")
            lines.append(f"- {icon} {name}：{desc}" if desc else f"- {icon} {name}")

    color = "red" if direction and "看空" in direction else "blue"
    if direction and "看多" in direction:
        color = "green"

    return build_card_message(title, lines, color=color)


def build_draft_card(
    title: str,
    drafts: list[dict],
    *,
    channel_label: str = "通知",
) -> dict:
    """Build a card from notification draft items (M-010 confirmation flow)."""
    lines = []
    for i, draft in enumerate(drafts[:10], 1):
        symbol = draft.get("symbol", "")
        name = draft.get("name", "")
        event_type = draft.get("event_type", "")
        reason = draft.get("reason", "")
        priority = draft.get("priority", "P2")
        as_of = draft.get("as_of", "")

        label = f"{symbol} {name}".strip() or event_type
        lines.append(f"**{i}. [{priority}] {label}**")
        if reason:
            lines.append(f"   {reason[:200]}")
        if as_of:
            lines.append(f"   _{as_of}_")
        lines.append("")

    if len(drafts) > 10:
        lines.append(f"... 共 {len(drafts)} 条")

    color = "orange" if channel_label == "盘中提醒" else "blue"
    return build_card_message(f"{title} | {channel_label}", lines, color=color)


def build_test_message() -> dict:
    """Build a test/warmup message."""
    return build_text_message(
        "TradingAgents Webhook Warmup\n这是一条飞书机器人测试消息。"
    )


# ── Send ─────────────────────────────────────────────────────────────────────

def send_message(payload: dict, webhook_url: str) -> bool:
    """Send a message via Feishu webhook. Returns True on success."""
    url = normalize_webhook_url(webhook_url)
    response = requests.post(
        url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json;charset=utf-8"},
        timeout=10,
    )
    response.raise_for_status()
    try:
        body = response.json()
    except Exception:
        logger.warning(
            "[feishu-wh] non-JSON response body=%s",
            str(getattr(response, "text", ""))[:240],
        )
        return False
    code = body.get("code", -1)
    msg = body.get("msg", "")
    if int(code) == 0:
        return True
    logger.warning("[feishu-wh] API error code=%s msg=%s", code, msg)
    return False


async def send_with_retry(payload: dict, webhook_url: str, max_retries: int = 2) -> bool:
    """Send message with retry (async-friendly). Follows bark/wecom pattern."""
    for attempt in range(max_retries + 1):
        try:
            ok = await asyncio.to_thread(send_message, payload, webhook_url)
            if ok:
                return True
        except Exception as exc:
            logger.warning("[feishu-wh] attempt %d failed: %s", attempt + 1, exc)
        if attempt < max_retries:
            await asyncio.sleep(15)
    return False


async def send_report_notification(
    symbol: str,
    trade_date: str,
    webhook_url: str,
    *,
    action_label: str | None = None,
    direction: str | None = None,
    execution_action: str | None = None,
    confidence: int | None = None,
    summary: str | None = None,
    risk_items: list[dict] | None = None,
    target_price: float | None = None,
    stop_loss_price: float | None = None,
) -> bool:
    """High-level: build card and send report notification with retry."""
    payload = build_report_card(
        symbol=symbol,
        trade_date=trade_date,
        action_label=action_label,
        direction=direction,
        execution_action=execution_action,
        confidence=confidence,
        summary=summary,
        risk_items=risk_items,
        target_price=target_price,
        stop_loss_price=stop_loss_price,
    )
    return await send_with_retry(payload, webhook_url)


async def send_draft_notification(
    title: str,
    drafts: list[dict],
    webhook_url: str,
    *,
    channel_label: str = "通知",
) -> bool:
    """High-level: build card from drafts and send with retry."""
    payload = build_draft_card(title, drafts, channel_label=channel_label)
    return await send_with_retry(payload, webhook_url)


async def send_warmup(webhook_url: str) -> bool:
    """Send a test message to verify webhook connectivity."""
    payload = build_test_message()
    return await send_with_retry(payload, webhook_url, max_retries=0)


__all__ = [
    "is_feishu_webhook_enabled",
    "get_feishu_webhook_url",
    "normalize_webhook_url",
    "mask_webhook_url",
    "build_card_message",
    "build_text_message",
    "build_report_card",
    "build_draft_card",
    "build_test_message",
    "send_message",
    "send_with_retry",
    "send_report_notification",
    "send_draft_notification",
    "send_warmup",
]
