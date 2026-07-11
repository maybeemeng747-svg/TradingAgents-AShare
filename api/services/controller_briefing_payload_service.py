# [IC-TA-004] controller_briefing_payload
"""investment-controller 飞书 briefing payload service（dry-run）.

在 IC-TA-001 / IC-TA-002 只读上下文包之上，调用纯引擎
``tradingagents.tradeflow.controller_briefing_payload`` 产出三场景
（盘前 / 盘中 / 盘后）统一 briefing payload。**dry-run**：不调用 LLM、
不发送飞书、不写文件、不写数据库；只读取 IC context。

investment-controller（OpenClaw / cron）可调用本服务获得：

- ``build_briefing_payload_dry_run``：按场景读取真实 IC context 产出 payload。
- ``run_briefing_scene_fixtures``：跑内置 fixture，供联调 / 文档 / 测试使用。

设计契约见 ``docs/TASKS.md`` IC-TA-004 与
``tradingagents/tradeflow/controller_briefing_payload.py`` 模块文档。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from api.runtime_tier import tradeflow_meta as _tradeflow_meta  # [PERF-001]
from api.services.investment_controller_context import (
    get_investment_controller_context,
)
from tradingagents.tradeflow.controller_briefing_payload import (
    ALLOWED_SCENES,
    INTRADAY_FIXTURE_CONTEXT,
    PAYLOAD_SCHEMA_VERSION,
    PAYLOAD_SOURCE,
    POST_MARKET_FIXTURE_CONTEXT,
    PRE_MARKET_FIXTURE_CONTEXT,
    SCENE_INTRADAY,
    SCENE_POST_MARKET,
    SCENE_PRE_MARKET,
    build_intraday_payload,
    build_post_market_payload,
    build_pre_market_payload,
    dry_run_all_scene_fixtures,
)

logger = logging.getLogger(__name__)

_SceneKind = Literal["pre", "intraday", "post", "all"]

_SCENE_BUILDERS = {
    SCENE_PRE_MARKET: build_pre_market_payload,
    SCENE_INTRADAY: build_intraday_payload,
    SCENE_POST_MARKET: build_post_market_payload,
}

_SCENE_FIXTURES = {
    SCENE_PRE_MARKET: PRE_MARKET_FIXTURE_CONTEXT,
    SCENE_INTRADAY: INTRADAY_FIXTURE_CONTEXT,
    SCENE_POST_MARKET: POST_MARKET_FIXTURE_CONTEXT,
}


def build_briefing_payload_dry_run(
    db: Session,
    user_id: str,
    *,
    scene: str,
    tf_db_path: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """按场景产出 briefing payload dry-run（读取真实 IC context，不写、不发、不调 LLM）.

    Args:
        db: SQLAlchemy session（只读，用于 IC-TA-001 上下文）.
        user_id: 用户 id.
        scene: 场景，``pre_market`` / ``intraday`` / ``post_market``.
        tf_db_path: 可选 tradeflow SQLite 路径覆盖（测试用）.
        now: 注入时间（测试用）.

    Returns:
        统一 briefing payload dict（schema 见 IC-TA-004），附加
        ``runtime_tier_meta`` 与 ``context_data_status``。
    """
    if scene not in ALLOWED_SCENES:
        raise ValueError(
            f"unknown scene: {scene!r}; expected one of {ALLOWED_SCENES}"
        )
    as_of = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    context = _safe_get_context(db, user_id, tf_db_path, as_of)
    builder = _SCENE_BUILDERS[scene]
    payload = builder(context, as_of=as_of)
    payload["runtime_tier_meta"] = _tradeflow_meta("controller_briefing_payload")
    payload["context_data_status"] = _context_data_status(context)
    return payload


def run_briefing_scene_fixtures(
    *,
    kind: _SceneKind = "all",
    as_of: str | None = None,
) -> dict[str, Any]:
    """跑内置 fixture 的 dry-run（不读 DB、不联网、不写文件）.

    供测试 / investment-controller 联调 / 文档生成使用。``kind=all`` 时返回
    与 ``dry_run_all_scene_fixtures`` 等价的汇总；``pre`` / ``intraday`` /
    ``post`` 单独返回对应场景 payload。
    """
    meta = _tradeflow_meta("controller_briefing_payload")
    if kind == "all":
        report = dry_run_all_scene_fixtures(as_of=as_of)
        report["runtime_tier_meta"] = meta
        return report
    scene_map = {"pre": SCENE_PRE_MARKET, "intraday": SCENE_INTRADAY, "post": SCENE_POST_MARKET}
    scene = scene_map.get(kind)
    if scene is None:
        raise ValueError(f"unknown kind: {kind!r}; expected pre/intraday/post/all")
    fixture = _SCENE_FIXTURES[scene]
    payload = _SCENE_BUILDERS[scene](fixture, as_of=as_of)
    payload["runtime_tier_meta"] = meta
    return payload


def _safe_get_context(
    db: Session, user_id: str, tf_db_path: str, as_of: str
) -> dict[str, Any]:
    """读取 IC context，永不抛异常（返回最小稳定结构）."""
    try:
        return get_investment_controller_context(db, user_id, tf_db_path=tf_db_path)
    except Exception as exc:
        logger.warning("[ic-ta-004] IC context build failed: %s", exc)
        return {
            "schema_version": "1.0",
            "as_of": as_of,
            "previous_trade_date": "",
            "is_trading_day": None,
            "generated_by": "investment_controller_context",
            "read_only": True,
        }


def _context_data_status(context: dict[str, Any]) -> dict[str, Any]:
    """透传 IC context 的 data_status 摘要（便于 investment-controller 判断）."""
    return {
        "holdings": (_bucket_status(context, "holdings")),
        "observation_warehouse": (_bucket_status(context, "observation_warehouse")),
        "tradeflow_candidates": (_bucket_status(context, "tradeflow_candidates")),
        "data_health": (_bucket_status(context, "data_health")),
        "mandate_daily_report": (_bucket_status(context, "mandate_daily_report")),
        "recent_report_data_blockers": (
            _bucket_status(context, "recent_report_data_blockers")
        ),
        "half_year_facts": (_bucket_status(context, "half_year_facts")),  # [HY-007]
    }


def _bucket_status(context: dict[str, Any], key: str) -> Any:
    bucket = context.get(key)
    if isinstance(bucket, dict):
        return bucket.get("data_status")
    return None


__all__ = [
    "ALLOWED_SCENES",
    "PAYLOAD_SCHEMA_VERSION",
    "PAYLOAD_SOURCE",
    "SCENE_INTRADAY",
    "SCENE_POST_MARKET",
    "SCENE_PRE_MARKET",
    "build_briefing_payload_dry_run",
    "run_briefing_scene_fixtures",
]
