# [B-002] openclaw_callback
"""OpenClaw 定时任务完成回调 service.

定时分析完成后，构建结构化 payload 并 POST 到 OpenClaw 回调端点，
由主控 AI 决定是否推送到飞书 / 企业微信等下游通道。

设计契约：
- ``OPENCLAW_CALLBACK_URL``  环境变量：OpenClaw 接收 webhook 的地址。
- ``OPENCLAW_CALLBACK_ENABLED`` 环境变量：是否启用回调（默认 false）。
- 不读取 / 打印任何 API key / token（与 notification_draft_service 一致）。
- 失败时静默降级，不阻塞主流程。
- 不写数据库、不调 LLM、不改 prompts。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

CALLBACK_SCHEMA_VERSION = "1.0.0"
CALLBACK_SOURCE = "tradingagents-scheduler"

# Retry config
_MAX_RETRIES = 2
_RETRY_DELAY_SECONDS = 10
_REQUEST_TIMEOUT_SECONDS = 15


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _clip_text(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    compact = " ".join(str(text).split()).strip()
    return compact[:limit]


def is_openclaw_callback_enabled() -> bool:
    """Check if OpenClaw callback is enabled and URL is configured."""
    url = os.getenv("OPENCLAW_CALLBACK_URL", "").strip()
    enabled = _env_bool("OPENCLAW_CALLBACK_ENABLED", False)
    return enabled and bool(url)


def build_callback_payload(
    report_id: str,
    symbol: str,
    trade_date: str,
    *,
    user_id: str = "",
    horizon: str = "short",
    source: str = "scheduled",
    result_data: dict | None = None,
) -> dict[str, Any]:
    """Build structured callback payload from a completed report.

    Extracts decision/direction/action/confidence/summary from result_data
    into a compact dict for OpenClaw consumption.

    Args:
        report_id: Report/job ID.
        symbol: Stock symbol.
        trade_date: Trade date string (YYYY-MM-DD).
        user_id: User who owns the scheduled task.
        horizon: Analysis horizon (short/medium).
        source: How the analysis was triggered (scheduled/scheduled_manual).
        result_data: Full report result_data dict (from ReportDB.result_data).

    Returns:
        Structured callback payload dict.
    """
    result = result_data or {}

    # Extract semantic fields from result_data (same priority as wecom service)
    action = (
        result.get("action_label")
        or result.get("decision")
        or ""
    )
    direction = (
        result.get("research_direction")
        or result.get("direction")
        or ""
    )
    execution_action = result.get("execution_action") or ""
    confidence = result.get("confidence")

    # Build summary from available text fields
    summary = _clip_text(
        result.get("final_trade_decision")
        or result.get("trader_investment_plan")
        or result.get("investment_plan")
        or ""
    )

    # Extract risk items (compact)
    risk_items = result.get("risk_items") or []
    risk_summary = []
    for item in risk_items[:5]:  # Cap at 5
        if isinstance(item, dict):
            risk_summary.append({
                "name": item.get("name", ""),
                "level": item.get("level", "unknown"),
            })

    # Extract key metrics (compact)
    key_metrics = result.get("key_metrics") or []
    metrics_summary = []
    for metric in key_metrics[:8]:  # Cap at 8
        if isinstance(metric, dict):
            metrics_summary.append({
                "name": metric.get("name", ""),
                "value": metric.get("value", ""),
                "status": metric.get("status", "unknown"),
            })

    # Readiness score
    readiness = result.get("readiness_score") or {}
    readiness_score = None
    if isinstance(readiness, dict):
        dc = readiness.get("data_completeness")
        conf = readiness.get("confidence")
        if dc is not None or conf is not None:
            readiness_score = {
                "data_completeness": dc,
                "confidence": conf,
            }

    now = datetime.now(timezone.utc)

    payload: dict[str, Any] = {
        "schema_version": CALLBACK_SCHEMA_VERSION,
        "source": CALLBACK_SOURCE,
        "event": "scheduled_analysis_completed",
        "timestamp": now.isoformat(),
        "report": {
            "id": report_id,
            "symbol": symbol,
            "trade_date": trade_date,
            "horizon": horizon,
            "source": source,
        },
        "decision": {
            "action": action,
            "direction": direction,
            "execution_action": execution_action,
            "confidence": confidence,
        },
        "summary": summary,
        "risk_items": risk_summary,
        "key_metrics": metrics_summary,
    }

    if readiness_score:
        payload["readiness_score"] = readiness_score

    if user_id:
        payload["user_id"] = user_id

    return payload


def send_callback(payload: dict[str, Any]) -> bool:
    """POST callback payload to OpenClaw webhook URL.

    Args:
        payload: Structured callback payload.

    Returns:
        True if sent successfully, False otherwise.
    """
    url = os.getenv("OPENCLAW_CALLBACK_URL", "").strip()
    if not url:
        logger.debug("[B-002] OPENCLAW_CALLBACK_URL not configured, skipping")
        return False

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TradingAgents-Scheduler/1.0",
    }

    # Optional auth header
    auth_token = os.getenv("OPENCLAW_CALLBACK_TOKEN", "").strip()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    body = json.dumps(payload, ensure_ascii=False)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = requests.post(
                url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            if 200 <= response.status_code < 300:
                logger.info(
                    "[B-002] OpenClaw callback sent OK (status=%d, symbol=%s)",
                    response.status_code,
                    payload.get("report", {}).get("symbol", "?"),
                )
                return True
            logger.warning(
                "[B-002] OpenClaw callback HTTP %d (attempt %d/%d): %s",
                response.status_code,
                attempt + 1,
                _MAX_RETRIES + 1,
                _clip_text(getattr(response, "text", None), 200),
            )
        except requests.RequestException as exc:
            logger.warning(
                "[B-002] OpenClaw callback failed (attempt %d/%d): %s",
                attempt + 1,
                _MAX_RETRIES + 1,
                exc,
            )

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY_SECONDS)

    logger.error(
        "[B-002] OpenClaw callback exhausted retries for symbol=%s",
        payload.get("report", {}).get("symbol", "?"),
    )
    return False


async def send_callback_async(payload: dict[str, Any]) -> bool:
    """Async wrapper around send_callback."""
    return await asyncio.to_thread(send_callback, payload)


def notify_openclaw_on_report_completion(
    report_id: str,
    symbol: str,
    trade_date: str,
    *,
    user_id: str = "",
    horizon: str = "short",
    source: str = "scheduled",
    result_data: dict | None = None,
) -> None:
    """Fire-and-forget OpenClaw notification for a completed scheduled report.

    Called from the scheduler after a scheduled analysis completes.
    Builds the payload and sends it asynchronously (non-blocking).

    Args:
        report_id: Report/job ID.
        symbol: Stock symbol.
        trade_date: Trade date string.
        user_id: User who owns the scheduled task.
        horizon: Analysis horizon.
        source: How the analysis was triggered.
        result_data: Full report result_data dict.
    """
    if not is_openclaw_callback_enabled():
        return

    payload = build_callback_payload(
        report_id=report_id,
        symbol=symbol,
        trade_date=trade_date,
        user_id=user_id,
        horizon=horizon,
        source=source,
        result_data=result_data,
    )

    # Fire-and-forget: caller already in async context, schedule as background task
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_callback_async(payload))
    except RuntimeError:
        # No running loop (e.g. sync context) — try sync send in background thread
        import threading
        thread = threading.Thread(
            target=send_callback,
            args=(payload,),
            daemon=True,
            name=f"openclaw-callback-{symbol}",
        )
        thread.start()


__all__ = [
    "build_callback_payload",
    "send_callback",
    "send_callback_async",
    "is_openclaw_callback_enabled",
    "notify_openclaw_on_report_completion",
    "CALLBACK_SCHEMA_VERSION",
    "CALLBACK_SOURCE",
]
