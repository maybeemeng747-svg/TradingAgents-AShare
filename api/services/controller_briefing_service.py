# [IC-TA-003] controller_briefing_dry_run
"""investment-controller 盘前 / 盘后 briefing service（dry-run）.

在 IC-TA-002 只读上下文包之上，调用纯引擎
``tradingagents.tradeflow.controller_briefing`` 产出盘前 / 盘后 briefing payload。
**dry-run**：不调用 LLM、不发送飞书、不写文件、不写数据库；只读取 IC context。

investment-controller（OpenClaw / cron）可调用本服务获得：

- ``build_pre_market_briefing_dry_run``：盘前流程提示 + TA 调度建议。
- ``build_post_market_briefing_dry_run``：盘后今日表现 + 报告数据缺口 + 次日 TA 候选。
- ``run_briefing_fixtures``：跑内置 fixture，供联调 / 文档 / 测试使用。

设计契约见 ``docs/TASKS.md`` IC-TA-003 与
``tradingagents/tradeflow/controller_briefing.py`` 模块文档。
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
from tradingagents.tradeflow.controller_briefing import (
    BRIEFING_POST_MARKET,
    BRIEFING_PRE_MARKET,
    BRIEFING_SCHEMA_VERSION,
    BRIEFING_SOURCE,
    POST_MARKET_FIXTURE_CONTEXT,
    PRE_MARKET_FIXTURE_CONTEXT,
    build_post_market_briefing,
    build_pre_market_briefing,
    dry_run_all_fixtures,
)

logger = logging.getLogger(__name__)


def build_pre_market_briefing_dry_run(
    db: Session,
    user_id: str,
    *,
    tf_db_path: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """盘前 briefing dry-run（读取真实 IC context，不写、不发、不调 LLM）."""
    as_of = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    context = _safe_get_context(db, user_id, tf_db_path, as_of)
    briefing = build_pre_market_briefing(context, as_of=as_of)
    briefing["runtime_tier_meta"] = _tradeflow_meta("controller_briefing_dry_run")
    return briefing


def build_post_market_briefing_dry_run(
    db: Session,
    user_id: str,
    *,
    tf_db_path: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """盘后 briefing dry-run（读取真实 IC context，不写、不发、不调 LLM）."""
    as_of = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    context = _safe_get_context(db, user_id, tf_db_path, as_of)
    briefing = build_post_market_briefing(context, as_of=as_of)
    briefing["runtime_tier_meta"] = _tradeflow_meta("controller_briefing_dry_run")
    return briefing


def run_briefing_fixtures(
    *,
    kind: Literal["pre", "post", "both"] = "both",
    as_of: str | None = None,
) -> dict[str, Any]:
    """跑内置 fixture 的 dry-run（不读 DB、不联网、不写文件）.

    供测试 / investment-controller 联调 / 文档生成使用。``kind=both`` 时返回
    与 ``dry_run_all_fixtures`` 等价的汇总；``pre`` / ``post`` 单独返回对应 briefing。
    """
    if kind == "both":
        report = dry_run_all_fixtures(as_of=as_of)
        report["runtime_tier_meta"] = _tradeflow_meta("controller_briefing_dry_run")
        return report
    if kind == "pre":
        briefing = build_pre_market_briefing(
            PRE_MARKET_FIXTURE_CONTEXT, as_of=as_of
        )
        briefing["runtime_tier_meta"] = _tradeflow_meta("controller_briefing_dry_run")
        return briefing
    if kind == "post":
        briefing = build_post_market_briefing(
            POST_MARKET_FIXTURE_CONTEXT, as_of=as_of
        )
        briefing["runtime_tier_meta"] = _tradeflow_meta("controller_briefing_dry_run")
        return briefing
    raise ValueError(f"unknown kind: {kind!r}")


def _safe_get_context(
    db: Session, user_id: str, tf_db_path: str, as_of: str
) -> dict[str, Any]:
    """读取 IC context，永不抛异常（返回最小稳定结构）."""
    try:
        return get_investment_controller_context(db, user_id, tf_db_path=tf_db_path)
    except Exception as exc:
        logger.warning("[ic-ta-003] IC context build failed: %s", exc)
        return {
            "schema_version": "1.0",
            "as_of": as_of,
            "previous_trade_date": "",
            "is_trading_day": None,
            "generated_by": "investment_controller_context",
            "read_only": True,
        }


__all__ = [
    "BRIEFING_POST_MARKET",
    "BRIEFING_PRE_MARKET",
    "BRIEFING_SCHEMA_VERSION",
    "BRIEFING_SOURCE",
    "build_post_market_briefing_dry_run",
    "build_pre_market_briefing_dry_run",
    "run_briefing_fixtures",
]
