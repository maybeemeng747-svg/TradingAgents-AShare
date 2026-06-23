# [TRACK-NOTIFY-001] notification_payload_dry_run
"""通知草稿 dry-run service（TA 侧 -> investment-controller / 飞书）.

在 IC-TA-001 只读上下文包之上，合成通知草稿 payload 并套上去噪器，产出
本地 markdown / json 预览。**第一阶段只做 dry-run**：

- 不读取 / 打印任何 webhook URL / token（``webhook_configured`` 恒为 ``None``，
  代表"本服务不读取该配置"，调用方/investment-controller 自行判断是否发送）。
- 不真实发送飞书 / 企业微信。
- P2 / P3 与数据不足的草稿只进 ``daily_digest``，不进入 ``intraday_push``。

去噪器（``NotificationDeduplicator``）以模块级单例持有，跨调用复用 30 分钟
窗口；提供 ``reset_dedup_state`` 供测试与人工强制重发使用。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from api.runtime_tier import tradeflow_meta as _tradeflow_meta  # [PERF-001]
from api.services.investment_controller_context import get_investment_controller_context
from tradingagents.tradeflow.notification_draft import (
    CHANNEL_DAILY_DIGEST,
    CHANNEL_INTRADAY_PUSH,
    NOTIFY_SCHEMA_VERSION,
    NOTIFY_SOURCE,
    NotificationDeduplicator,
    apply_dedup,
    build_notification_drafts_from_context,
    classify_delivery_channel,
    render_drafts_json,
    render_drafts_markdown,
    split_by_channel,
)

logger = logging.getLogger(__name__)

# 模块级去噪器：30 分钟窗口内同 (symbol, event_type) 不重复。
# dry-run 场景 + GIL 下足够；如需多进程共享可后续替换为带 TTL 的共享存储。
_DEDUPLICATOR = NotificationDeduplicator()


def reset_dedup_state() -> None:
    """清除去噪器状态（测试 / 人工强制重发使用）."""
    _DEDUPLICATOR.reset()


def build_notification_dry_run(
    db: Session,
    user_id: str,
    *,
    tf_db_path: str = "",
    force_refresh: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """生成飞书 / 总控官通知草稿 dry-run payload.

    Args:
        db: SQLAlchemy session（只读，用于 IC-TA-001 上下文）.
        user_id: 用户 id.
        tf_db_path: 可选 tradeflow SQLite 路径覆盖（测试用）.
        force_refresh: True 时先清空去噪器，强制重新生成全部草稿.
        now: 注入时间（测试用）.

    Returns:
        dry-run payload dict，包含::

            schema_version / as_of / dry_run / webhook_configured
            intraday_push   # P0/P1 非记录草稿（盘中主动提醒队列）
            daily_digest    # P2/P3 草稿
            recorded_only   # 数据不足只记录的草稿
            deduplicated    # 被去噪抑制的草稿（含 dedup_reason）
            summary_counts
            markdown_preview
            json_preview
            runtime_tier_meta

    永不因 webhook 未配置或数据缺失而抛异常（验收：未配置 webhook 不报错）。
    """
    now = now or datetime.now()
    as_of = now.strftime("%Y-%m-%d %H:%M:%S")
    if force_refresh:
        reset_dedup_state()

    # 1. 取 IC-TA-001 只读上下文（永不抛异常：返回稳定空结构）
    try:
        context = get_investment_controller_context(db, user_id, tf_db_path=tf_db_path)
    except Exception as exc:
        logger.warning("[track-notify-001] IC context build failed: %s", exc)
        context = {"as_of": as_of, "is_trading_day": True}

    # 2. 合成草稿（纯函数，已做强动作词自检）
    drafts = build_notification_drafts_from_context(context, as_of=as_of, now=now)

    # 3. 去噪
    dedup_result = apply_dedup(drafts, _DEDUPLICATOR, now=now)
    emitted = dedup_result["emitted"]
    deduplicated = dedup_result["deduplicated"]

    # 4. 分通道
    channels = split_by_channel(emitted)
    intraday_push = channels[CHANNEL_INTRADAY_PUSH]
    daily_all = channels[CHANNEL_DAILY_DIGEST]
    # daily_digest 里再拆出 record_only（数据不足只记录）单独展示
    recorded_only = [d for d in daily_all if d.get("record_only")]
    daily_digest = [d for d in daily_all if not d.get("record_only")]

    # 5. 预览渲染
    markdown_preview = render_drafts_markdown(
        intraday_push=intraday_push,
        daily_digest=daily_digest,
        recorded_only=recorded_only,
        deduplicated=deduplicated,
        as_of=as_of,
    )
    json_preview = render_drafts_json(
        intraday_push=intraday_push,
        daily_digest=daily_digest,
        recorded_only=recorded_only,
        deduplicated=deduplicated,
    )

    return {
        "schema_version": NOTIFY_SCHEMA_VERSION,
        "as_of": as_of,
        "generated_by": NOTIFY_SOURCE,
        "dry_run": True,  # 第一阶段恒为 True
        "webhook_configured": None,  # 本服务不读取 webhook 配置
        "intraday_push": intraday_push,
        "daily_digest": daily_digest,
        "recorded_only": recorded_only,
        "deduplicated": deduplicated,
        "summary_counts": {
            "total_drafts": len(drafts),
            "intraday_push": len(intraday_push),
            "daily_digest": len(daily_digest),
            "recorded_only": len(recorded_only),
            "deduplicated": len(deduplicated),
        },
        "markdown_preview": markdown_preview,
        "json_preview": json_preview,
        "runtime_tier_meta": _tradeflow_meta("notification_draft_dry_run"),
        # 透传 IC context 的 data_status 摘要，便于 investment-controller 判断
        "context_data_status": {
            "holdings": (context.get("holdings") or {}).get("data_status"),
            "observation_warehouse": (context.get("observation_warehouse") or {}).get("data_status"),
            "tradeflow_candidates": (context.get("tradeflow_candidates") or {}).get("data_status"),
            "data_health": (context.get("data_health") or {}).get("data_status"),
        },
    }


__all__ = [
    "build_notification_dry_run",
    "reset_dedup_state",
]
