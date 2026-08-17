# [B-002] openclaw_callback
"""OpenClaw 定时任务完成回调 service.

定时分析完成后，构建结构化 payload 并 POST 到 OpenClaw 回调端点，
由主控 AI 决定是否推送到飞书 / 企业微信等下游通道。

设计契约：
- ``OPENCLAW_CALLBACK_URL``  环境变量：OpenClaw 接收 webhook 的地址。
- ``OPENCLAW_CALLBACK_ENABLED`` 环境变量：是否启用回调（默认 false）。
- ``readiness_score`` 恒出现在 payload 中（v1.1.0 起）：
  - 有真实来源时 ``{"status": "available", "data_completeness": int,
    "confidence": "高|中|低", "source": "readiness_score|final_trade_decision"}``；
  - 无来源时 ``{"status": "not_available"}``，不以空值或缺键冒充。
  - fail-closed：仅当 data_completeness 为 0-100 整数且 confidence ∈ {高,中,低}
    时才发布 available；截断/越界/类型错误的候选值一律视为无效，不降级发布。
    无 offset 的 legacy 文本仅在匹配系统确定性结构（``[C-008] 执行就绪度评分``
    标题 + 字段行）时采信，正文行内提及的 [C-008] 值不发布。
- 不读取 / 打印任何 API key / token（与 notification_draft_service 一致）。
- 失败时静默降级，不阻塞主流程。
- 不写数据库、不调 LLM、不改 prompts。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

CALLBACK_SCHEMA_VERSION = "1.1.0"
CALLBACK_SOURCE = "tradingagents-scheduler"

# Retry config
_MAX_RETRIES = 2
_RETRY_DELAY_SECONDS = 10
_REQUEST_TIMEOUT_SECONDS = 15


def _pick(*values):
    """Return first non-null value, allowing explicit empty-string fallback."""
    for value in values:
        if value is None:
            continue
        return value
    return None


def _extract(source: Any, key: str, default: Any = None) -> Any:
    """Extract field from dict-like/ORM-like source."""
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    if hasattr(source, key):
        return getattr(source, key, default)
    return default


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


# [B-002-R2] readiness 真源：risk_manager 把 [C-008] 执行就绪度评分块追加到
# final_trade_decision 文本并随 ReportDB 持久化；结构化 dict 无生产写入方，
# 仅作为前向兼容输入。文本块是当前唯一真实持久化来源。
# [B-002-R2-fix] 权威块定位：risk_manager 同时把 metadata.system_diagnostics_offset
# （系统追加诊断块的起始位置）持久化到 result_data.metadata。解析必须优先限定
# 在该可信尾部；模型正文提及的 [C-008] 完整度/置信度不得发布为 available。
_C008_HEADING_PATTERN = re.compile(r"\[C-008\][ \t]*执行就绪度评分")
# Field lines are deterministic bullet rows in format_readiness_score output
# (tradingagents/agents/utils/readiness_score.py); line-anchoring rejects
# inline prose mentions such as "参考 [C-008] 数据完整度：99% 置信度：高".
_READINESS_COMPLETENESS_PATTERN = re.compile(
    r"(?m)^-[ \t]*数据完整度[：:][ \t]*(\d{1,3})[ \t]*%[ \t]*$"
)
_READINESS_CONFIDENCE_PATTERN = re.compile(
    r"(?m)^-[ \t]*置信度[：:][ \t]*([高中低])[ \t]*$"
)
_READINESS_CONFIDENCE_VALUES = frozenset({"高", "中", "低"})

# Sentinel distinguishing "metadata.system_diagnostics_offset was never
# persisted" (legacy reports → last-[C-008] fallback) from "the key is
# present but its value is invalid" (incl. explicit null → fail closed).
_OFFSET_ABSENT = object()


def _is_valid_completeness(value: Any) -> bool:
    """v1.1 contract: data_completeness must be an int in [0, 100]."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100


def _is_valid_confidence(value: Any) -> bool:
    """v1.1 contract: confidence must be one of 高/中/低."""
    return isinstance(value, str) and value in _READINESS_CONFIDENCE_VALUES


def _validated_readiness_values(mapping: Any) -> dict | None:
    """Return a publishable readiness values dict, or None if invalid.

    ``status: available`` requires BOTH a valid ``data_completeness`` and a
    valid ``confidence``; partial / wrongly-typed / out-of-range candidates
    fail closed (caller then emits ``not_available`` or tries the next
    source).
    """
    if not isinstance(mapping, dict):
        return None
    dc = mapping.get("data_completeness")
    conf = mapping.get("confidence")
    if _is_valid_completeness(dc) and _is_valid_confidence(conf):
        return {"data_completeness": dc, "confidence": conf}
    return None


def _extract_system_diagnostics_offset(*sources: Any) -> Any:
    """Return the persisted ``metadata.system_diagnostics_offset`` if present.

    Scans dict-like sources (``result_data`` / ``report_obj["result_data"]``)
    for ``metadata.system_diagnostics_offset``. Returns the raw value whenever
    any source carries the key — including an explicit ``None`` or another
    invalid value — so the parser can fail closed on "present but
    inconsistent". Returns the ``_OFFSET_ABSENT`` sentinel only when no source
    carries the key at all (legacy reports without the metadata).
    """
    for source in sources:
        if not isinstance(source, dict):
            continue
        metadata = source.get("metadata")
        if isinstance(metadata, dict) and "system_diagnostics_offset" in metadata:
            return metadata["system_diagnostics_offset"]
    return _OFFSET_ABSENT


def _parse_readiness_from_text(
    text: Any, trusted_offset: Any = _OFFSET_ABSENT
) -> dict | None:
    """Parse the authoritative persisted [C-008] readiness block from report text.

    ``trusted_offset`` is the persisted ``metadata.system_diagnostics_offset``:

    - valid int within ``[0, len(text))`` — parse only that system-appended
      tail; a ``[C-008]`` mention authored by the model before the offset is
      never trusted, even when it carries completeness/confidence values;
    - present but invalid (explicit null / non-int / out of range) — the
      persisted text is inconsistent with the offset, so nothing is trusted
      (fail closed, no legacy fallback);
    - absent (legacy reports without the metadata) — fall back to the last
      ``[C-008]`` heading, because the system block is appended after any
      model-authored text. The fallback only accepts the deterministic
      system-block structure (``[C-008] 执行就绪度评分`` heading followed by
      bullet field lines), so a bare inline ``[C-008]`` mention carrying
      valid-looking inline values is never trusted.

    Returns a partial ``{"data_completeness": int, "confidence": str}`` dict
    (either key may be missing when the block is truncated or out of range)
    or None when no block/values are found; the caller validates completeness
    AND confidence before publishing ``status: available``.
    """
    if not isinstance(text, str) or not text:
        return None
    source = text
    if trusted_offset is not _OFFSET_ABSENT:
        if isinstance(trusted_offset, bool) or not isinstance(trusted_offset, int):
            return None
        if not 0 <= trusted_offset < len(text):
            return None
        source = text[trusted_offset:]
    marker = None
    for marker in _C008_HEADING_PATTERN.finditer(source):
        pass  # keep the last occurrence = system-appended block
    if marker is None:
        return None
    section = source[marker.end():]
    parsed: dict[str, Any] = {}
    completeness = _READINESS_COMPLETENESS_PATTERN.search(section)
    if completeness:
        value = int(completeness.group(1))
        if 0 <= value <= 100:
            parsed["data_completeness"] = value
    confidence = _READINESS_CONFIDENCE_PATTERN.search(section)
    if confidence:
        parsed["confidence"] = confidence.group(1)
    return parsed or None


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
    report_obj: dict | None = None,
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
    report_data = report_obj or {}
    result = result_data or {}

    # Extract semantic fields from result_data (same priority as wecom service)
    action = _pick(
        _extract(report_data, "action_label"),
        _extract(result, "action_label"),
        _extract(report_data, "decision"),
        _extract(result, "decision"),
        "",
    )
    direction = _pick(
        _extract(report_data, "research_direction"),
        _extract(result, "research_direction"),
        _extract(report_data, "direction"),
        _extract(result, "direction"),
        "",
    )
    execution_action = _pick(
        _extract(report_data, "execution_action"),
        _extract(result, "execution_action"),
        "",
    )
    confidence = _pick(_extract(report_data, "confidence"), _extract(result, "confidence"))

    # Build summary from available text fields
    summary = _clip_text(
        _pick(
            _extract(report_data, "final_trade_decision"),
            _extract(result, "final_trade_decision"),
            _extract(report_data, "trader_investment_plan"),
            _extract(result, "trader_investment_plan"),
            _extract(report_data, "investment_plan"),
            _extract(result, "investment_plan"),
            "",
        )
    )

    risk_items = _pick(
        _extract(report_data, "risk_items"),
        _extract(result, "risk_items"),
        [],
    )
    key_metrics = _pick(
        _extract(report_data, "key_metrics"),
        _extract(result, "key_metrics"),
        [],
    )

    risk_summary = []
    for item in risk_items[:5]:  # Cap at 5
        if isinstance(item, dict):
            risk_summary.append({
                "name": item.get("name", ""),
                "level": item.get("level", "unknown"),
            })

    metrics_summary = []
    for metric in key_metrics[:8]:  # Cap at 8
        if isinstance(metric, dict):
            metrics_summary.append({
                "name": metric.get("name", ""),
                "value": metric.get("value", ""),
                "status": metric.get("status", "unknown"),
            })

    # [B-002-R2] readiness_score：优先结构化字段（前向兼容），否则从已持久化
    # 的 [C-008] 文本块解析；仅在候选值通过 v1.1 契约校验（completeness 为
    # 0-100 整数且 confidence ∈ {高,中,低}）时发布 available，否则尝试下一
    # 来源；全部无效/缺失时输出明确 not_available 状态（fail-closed）。
    readiness_raw = _pick(
        _extract(report_data, "readiness_score"),
        _extract(result, "readiness_score"),
    )
    readiness_values: dict | None = None
    readiness_source: str | None = None
    if isinstance(readiness_raw, dict):
        values = _validated_readiness_values(readiness_raw)
        if values is not None:
            readiness_values = values
            readiness_source = "readiness_score"
    if readiness_values is None:
        # [B-002-R2-fix] restrict text parsing to the trusted system tail
        # persisted in metadata.system_diagnostics_offset (report_service and
        # the frontend already consume the same field).
        trusted_offset = _extract_system_diagnostics_offset(
            _extract(report_data, "result_data"),
            result,
        )
        for text in (
            _extract(report_data, "final_trade_decision"),
            _extract(result, "final_trade_decision"),
        ):
            parsed = _parse_readiness_from_text(text, trusted_offset=trusted_offset)
            values = _validated_readiness_values(parsed)
            if values is not None:
                readiness_values = values
                readiness_source = "final_trade_decision"
                break
    if readiness_values is not None:
        readiness_score: dict[str, Any] = {
            "status": "available",
            "data_completeness": readiness_values["data_completeness"],
            "confidence": readiness_values["confidence"],
            "source": readiness_source,
        }
    else:
        readiness_score = {"status": "not_available"}

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
    report_obj: dict | None = None,
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
        report_obj: Full report object as fallback/overrides for key fields.
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
        report_obj=report_obj,
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
