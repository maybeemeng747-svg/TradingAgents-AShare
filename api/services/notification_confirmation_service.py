# [M-010] feishu_notification_confirmation
"""通知确认队列 service — 人工确认后才真正发送飞书。

第一阶段工作流::

    1. ``generate_pending`` — 从 notification_draft 引擎生成草稿，
       存入 ``notification_logs`` 表，状态为 ``pending_confirmation``。
    2. ``list_pending`` — 用户查看待确认队列。
    3. ``confirm_and_send`` — 用户确认单条，通过飞书 webhook 发送。
    4. ``dismiss`` — 用户拒绝单条，不再发送。

设计契约：

- 不读取 / 打印 webhook URL / token（只存 masked 版本）。
- 不调 LLM，不改 prompts，不写生产 DB（``notification_logs`` 不含交易数据）。
- 失败返回结构化 dict，不抛异常。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import desc
from sqlalchemy.orm import Session

from api.database import NotificationLogDB
from api.services.notification_draft_service import build_notification_dry_run

logger = logging.getLogger(__name__)

# Status constants
STATUS_PENDING = "pending_confirmation"
STATUS_CONFIRMED = "confirmed"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_DISMISSED = "dismissed"

VALID_TRANSITIONS = {
    STATUS_PENDING: {STATUS_CONFIRMED, STATUS_DISMISSED},
    STATUS_CONFIRMED: {STATUS_SENDING, STATUS_DISMISSED},
    STATUS_SENDING: {STATUS_SENT, STATUS_FAILED},
    STATUS_FAILED: {STATUS_CONFIRMED},  # Allow retry
}

CHANNEL_FEISHU = "feishu"


def generate_pending(
    db: Session,
    user_id: str,
    *,
    channel: str = CHANNEL_FEISHU,
    force_refresh: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Generate notification drafts and save as pending confirmation.

    Returns:
        dict with ``generated_count``, ``skipped_count``, ``pending_items``.
    """
    normalized_channel = (channel or "").strip().lower()
    if normalized_channel != CHANNEL_FEISHU:
        raise ValueError(f"不支持的通知渠道: {channel!r}（当前仅支持 feishu）")
    channel = normalized_channel

    now = now or datetime.now()
    dry_run = build_notification_dry_run(
        db, user_id, force_refresh=force_refresh, now=now,
    )

    generated = 0
    skipped = 0

    for channel_key in ("intraday_push", "daily_digest"):
        drafts = dry_run.get(channel_key) or []
        for draft in drafts:
            symbol = draft.get("symbol", "")
            event_type = draft.get("event_type", "")
            # Skip if already pending for same (user, symbol, event_type)
            existing = (
                db.query(NotificationLogDB)
                .filter(
                    NotificationLogDB.user_id == user_id,
                    NotificationLogDB.symbol == symbol,
                    NotificationLogDB.event_type == event_type,
                    NotificationLogDB.status == STATUS_PENDING,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            log_entry = NotificationLogDB(
                id=uuid4().hex,
                user_id=user_id,
                channel=channel,
                event_type=event_type,
                priority=draft.get("priority", "P2"),
                symbol=symbol,
                name=draft.get("name", ""),
                title=draft.get("title", "")[:200],
                reason=draft.get("reason", ""),
                status=STATUS_PENDING,
                payload=draft,
            )
            db.add(log_entry)
            generated += 1

    db.commit()

    pending_items = list_pending(db, user_id)
    return {
        "generated_count": generated,
        "skipped_count": skipped,
        "pending_items": pending_items,
        "dry_run_preview": dry_run.get("markdown_preview", ""),
    }


def list_pending(
    db: Session,
    user_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List pending confirmation notifications for a user."""
    rows = (
        db.query(NotificationLogDB)
        .filter(
            NotificationLogDB.user_id == user_id,
            NotificationLogDB.status == STATUS_PENDING,
        )
        .order_by(
            NotificationLogDB.priority.asc(),
            NotificationLogDB.created_at.desc(),
        )
        .limit(limit)
        .all()
    )
    return [_log_to_dict(row) for row in rows]


def list_log(
    db: Session,
    user_id: str,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List notification history (all statuses)."""
    query = db.query(NotificationLogDB).filter(
        NotificationLogDB.user_id == user_id,
    )
    if status:
        query = query.filter(NotificationLogDB.status == status)
    rows = (
        query.order_by(desc(NotificationLogDB.created_at))
        .limit(limit)
        .all()
    )
    return [_log_to_dict(row) for row in rows]


def confirm_and_send(
    db: Session,
    notification_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Confirm a pending notification and send via feishu webhook.

    Returns:
        dict with ``success``, ``notification``, optional ``error``.
    """
    row = (
        db.query(NotificationLogDB)
        .filter(
            NotificationLogDB.id == notification_id,
            NotificationLogDB.user_id == user_id,
        )
        .first()
    )
    if not row:
        return {"success": False, "error": "通知记录不存在"}
    if row.status != STATUS_PENDING and row.status != STATUS_FAILED:
        return {"success": False, "error": f"当前状态 {row.status} 不允许确认"}

    row.status = STATUS_CONFIRMED
    row.confirmed_at = datetime.now(timezone.utc)
    db.commit()

    # Fire send
    return _do_send(db, row)


def dismiss(
    db: Session,
    notification_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Dismiss a pending notification (won't be sent)."""
    row = (
        db.query(NotificationLogDB)
        .filter(
            NotificationLogDB.id == notification_id,
            NotificationLogDB.user_id == user_id,
        )
        .first()
    )
    if not row:
        return {"success": False, "error": "通知记录不存在"}
    if row.status not in (STATUS_PENDING, STATUS_FAILED):
        return {"success": False, "error": f"当前状态 {row.status} 不允许拒绝"}

    row.status = STATUS_DISMISSED
    db.commit()
    return {"success": True, "notification": _log_to_dict(row)}


def confirm_all_pending(
    db: Session,
    user_id: str,
) -> dict[str, Any]:
    """Confirm and send all pending notifications for a user."""
    rows = (
        db.query(NotificationLogDB)
        .filter(
            NotificationLogDB.user_id == user_id,
            NotificationLogDB.status == STATUS_PENDING,
        )
        .order_by(NotificationLogDB.priority.asc())
        .all()
    )
    results = {"total": len(rows), "sent": 0, "failed": 0, "errors": []}
    for row in rows:
        row.status = STATUS_CONFIRMED
        row.confirmed_at = datetime.now(timezone.utc)
        db.commit()

        send_result = _do_send(db, row)
        if send_result.get("success"):
            results["sent"] += 1
        else:
            results["failed"] += 1
            results["errors"].append({
                "id": row.id,
                "symbol": row.symbol,
                "error": send_result.get("error", "unknown"),
            })
    return results


# ── Internal send ────────────────────────────────────────────────────────────

def _do_send(db: Session, row: NotificationLogDB) -> dict[str, Any]:
    """Send a confirmed notification via feishu webhook (sync)."""
    import json as _json

    from api.services.feishu_webhook_service import (
        get_feishu_webhook_url,
        is_feishu_webhook_enabled,
        mask_webhook_url,
        sanitize_error_text,
        send_message,
        build_draft_card,
    )

    # [M-010-R1] Channel guard: only ``feishu`` may go through the feishu
    # webhook. Drafts generated for any other channel (e.g. bark / wecom /
    # unknown) must NOT silently fall through to feishu sending.
    if (row.channel or "").strip().lower() != CHANNEL_FEISHU:
        row.status = STATUS_FAILED
        row.error = f"不支持的通知渠道: {row.channel!r}（当前仅支持 feishu）"
        db.commit()
        logger.warning(
            "[m-010] blocked non-feishu send for %s (channel=%s)",
            row.id, row.channel,
        )
        return {"success": False, "error": row.error, "notification": _log_to_dict(row)}

    if not is_feishu_webhook_enabled():
        row.status = STATUS_FAILED
        row.error = "飞书 Webhook 未配置或已禁用"
        db.commit()
        return {"success": False, "error": row.error, "notification": _log_to_dict(row)}

    webhook_url = get_feishu_webhook_url()
    if not webhook_url:
        row.status = STATUS_FAILED
        row.error = "飞书 Webhook URL 为空"
        db.commit()
        return {"success": False, "error": row.error, "notification": _log_to_dict(row)}

    row.status = STATUS_SENDING
    row.webhook_url_masked = mask_webhook_url(webhook_url)
    db.commit()

    # Build message payload from stored draft
    payload = row.payload or {}
    channel_label = {"P0": "盘中提醒", "P1": "盘中提醒"}.get(row.priority, "日报摘要")
    card_payload = build_draft_card(
        title=f"TradingAgents | {row.symbol or ''}",
        drafts=[payload],
        channel_label=channel_label,
    )

    try:
        ok = send_message(card_payload, webhook_url)
        if ok:
            row.status = STATUS_SENT
            row.sent_at = datetime.now(timezone.utc)
            row.error = None
            db.commit()
            logger.info("[m-010] sent notification %s for %s", row.id, row.symbol)
            return {"success": True, "notification": _log_to_dict(row)}
        else:
            row.status = STATUS_FAILED
            row.error = "飞书 API 返回失败"
            db.commit()
            return {"success": False, "error": row.error, "notification": _log_to_dict(row)}
    except Exception as exc:
        # [M-010-R1] Scrub the exception text BEFORE persisting / returning —
        # transport exceptions (requests, urllib3) routinely embed the request
        # URL, which for Feishu carries the signing token in the path.
        safe_err = sanitize_error_text(exc)[:500]
        row.status = STATUS_FAILED
        row.error = safe_err
        db.commit()
        logger.warning("[m-010] send failed for %s: %s", row.id, safe_err)
        return {"success": False, "error": safe_err, "notification": _log_to_dict(row)}


def _log_to_dict(row: NotificationLogDB) -> dict[str, Any]:
    """Convert a NotificationLogDB row to a safe dict (no webhook URL)."""
    return {
        "id": row.id,
        "channel": row.channel,
        "event_type": row.event_type,
        "priority": row.priority,
        "symbol": row.symbol,
        "name": row.name,
        "title": row.title,
        "reason": row.reason,
        "status": row.status,
        "webhook_url_masked": row.webhook_url_masked,
        "error": row.error,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


__all__ = [
    "STATUS_PENDING",
    "STATUS_CONFIRMED",
    "STATUS_SENDING",
    "STATUS_SENT",
    "STATUS_FAILED",
    "STATUS_DISMISSED",
    "CHANNEL_FEISHU",
    "generate_pending",
    "list_pending",
    "list_log",
    "confirm_and_send",
    "dismiss",
    "confirm_all_pending",
]
