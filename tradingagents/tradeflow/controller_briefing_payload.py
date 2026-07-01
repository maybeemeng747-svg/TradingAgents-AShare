# [IC-TA-004] controller_briefing_payload
"""investment-controller 飞书 briefing payload 纯引擎（dry-run）.

IC-TA-003 已为盘前 / 盘后产出 ``briefing_type`` 形态的 briefing（``ta_to_schedule`` /
``daily_report_only`` / ``needs_manual_confirmation`` 三条分流）。IC-TA-004 把
investment-controller 固化为 **TA 调度官 / 播报官**：它给飞书 / 总控一份**统一的
briefing payload**，覆盖盘前 / 盘中 / 盘后三场景，每场景只做"调度 + 摘要"，
**不越权下最终交易结论**。

本引擎定义 IC-TA-004 的统一 payload schema::

    {
        "scene":            "pre_market" | "intraday" | "post_market",
        "summary":          "一句话摘要（无强买卖词）",
        "ta_requests":      [待调度 TA，每条带 reason_call_ta + notify_level],
        "watch_items":      [仅观察 / 仅日报，每条带 reason_skip_ta + notify_level],
        "data_warnings":    [数据缺口 / 行情风险 / 失效告警，每条带 notify_level],
        "notify_level":     {intraday_push / daily_digest / suppressed 计数},
    }

设计契约（见 docs/TASKS.md IC-TA-004）
--------------------------------------
* **纯函数**：不访问数据库、不联网、不读环境变量、不发送飞书、不写文件。
  输入是 IC-TA-001 / IC-TA-002 的 ``get_investment_controller_context`` 返回 dict
  （或等价 fixture）。
* **dry-run**：永远只产出 payload + markdown 预览，由 investment-controller /
  OpenClaw 决定是否真正发送。
* **不调用 LLM**：所有理由由规则模板生成。
* **不强动作词**：复用 ``controller_briefing.FORBIDDEN_STRONG_WORDS`` 与
  ``scan_forbidden_words`` 自检。
* **接入通知去噪规则**（NOTIFY-001/002/003）：每条 ta_request / watch_item /
  data_warning 都带 ``notify_level``，P2/P3 → ``daily_digest``，只有 P0/P1 非
  record_only → ``intraday_push``。三场景的 briefing 本身**不**调用
  ``NotificationDeduplicator``（去噪由 notify dry-run 通道负责），只做**通道
  分类**，让 investment-controller 知道"这条如果发，走盘中还是日报"。
* **三场景分工**（剃刀定律）：
    - ``pre_market``：TA 调度（今天调哪些 TA）+ 昊天主题重心 + 数据健康。
    - ``intraday``：盘中观察（哪些标的接近触发 / 已触发 / 失效）+ 持仓风险告警。
      **盘中不调度新 TA**（那是盘前 / 盘后的职责），只 watch + warn。
    - ``post_market``：今日表现 + 报告数据缺口 + 次日 TA 候选。
* **稳定空结构**：缺数据 / 缺 bucket 时返回空数组 + 明确 summary，不抛异常。
* 每条条目带 ``source`` + ``as_of``，与 IC-TA-001 数据契约一致。

复用关系（不重复造轮子）：
    - IC-TA-003 的 ``_route_ta_to_schedule`` / ``_route_daily_report_only`` /
      ``_route_needs_manual_confirmation`` 仍然是路由事实来源，本引擎把它们
      reshape 成 IC-TA-004 的 schema 并补 ``notify_level``。
    - NOTIFY 的 ``classify_delivery_channel`` 决定 notify_level。
    - TRACK-004 的 ``evaluate_observation_state`` 派生盘中观察状态。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from tradingagents.tradeflow import controller_briefing as _ic_ta003
from tradingagents.tradeflow.controller_briefing import (
    FORBIDDEN_STRONG_WORDS,
    POST_MARKET_FIXTURE_CONTEXT,
    PRE_MARKET_FIXTURE_CONTEXT,
    PROFILE_FULL_TA,
    scan_forbidden_words,
)
from tradingagents.tradeflow.notification_draft import (
    CHANNEL_DAILY_DIGEST,
    CHANNEL_INTRADAY_PUSH,
    classify_delivery_channel,
)
from tradingagents.tradeflow.observation_state_engine import (
    STATE_DATA_MISSING,
    STATE_IN_ENTRY_ZONE,
    STATE_INVALIDATED,
    STATE_MISSED_ENTRY,
    STATE_NEAR_ENTRY,
    STATE_NEEDS_REVIEW,
    STATE_TA_REQUIRED,
    STATE_WATCHING,
    evaluate_observation_state,
)

# [IC-TA-004] controller_briefing_payload
# ── Schema / 常量 ────────────────────────────────────────────────────────────

PAYLOAD_SCHEMA_VERSION = "1.0"
PAYLOAD_SOURCE = "controller_briefing_payload"

# 三场景（scene）：盘前调度 / 盘中观察 / 盘后复盘
SCENE_PRE_MARKET = "pre_market"
SCENE_INTRADAY = "intraday"
SCENE_POST_MARKET = "post_market"
ALLOWED_SCENES = (SCENE_PRE_MARKET, SCENE_INTRADAY, SCENE_POST_MARKET)

# notify_level 取值（与 NOTIFY 通道对齐）
NOTIFY_LEVEL_INTRADAY_PUSH = CHANNEL_INTRADAY_PUSH   # 盘中主动提醒（仅 P0/P1 非 record_only）
NOTIFY_LEVEL_DAILY_DIGEST = CHANNEL_DAILY_DIGEST     # 夜间日报（P2/P3 + record_only）

# data_warning 类型
WARNING_REPORT_DATA_GAP = "report_data_gap"
WARNING_HOLDINGS_RISK_LARGE = "holdings_risk_large_drop"
WARNING_HOLDINGS_RISK_MODERATE = "holdings_risk_moderate_drop"
WARNING_OBSERVATION_INVALIDATED = "observation_invalidated"
WARNING_DATA_SOURCE_FAILURE = "data_source_failure"
WARNING_DATA_STALE = "data_stale"


# ──────────────────────────────────────────────────────────────────────────────
# 公共 helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_now(as_of: str | None) -> str:
    if as_of:
        return as_of
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _bucket(context: dict[str, Any], key: str) -> dict[str, Any]:
    bucket = context.get(key)
    return bucket if isinstance(bucket, dict) else {}


def _items(bucket: dict[str, Any]) -> list[dict[str, Any]]:
    raw = bucket.get("items")
    return [it for it in (raw or []) if isinstance(it, dict)]


def _notify_level_for(*, priority: str, record_only: bool = False) -> str:
    """复用 NOTIFY 通道分类决定 notify_level（P0/P1 非记录 → 盘中，其余 → 日报）."""
    draft = {"priority": priority, "record_only": bool(record_only)}
    return classify_delivery_channel(draft)


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# 统一 payload 组装
# ──────────────────────────────────────────────────────────────────────────────

def _build_ta_requests(context: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
    """从 IC-TA-003 路由出待调度 TA，reshape 成 IC-TA-004 schema + notify_level.

    盘前 / 盘后场景使用；盘中场景不调度新 TA（返回空）。
    """
    ta_list = _ic_ta003._route_ta_to_schedule(context, as_of)
    out: list[dict[str, Any]] = []
    for it in ta_list:
        priority = it.get("priority", "P2")
        record_only = it.get("requires_confirmation") and priority not in ("P0", "P1")
        out.append({
            "symbol": it.get("symbol", ""),
            "name": it.get("name", ""),
            "origin": it.get("origin", ""),
            "candidate_type": it.get("candidate_type", ""),
            "priority": priority,
            "reason_call_ta": it.get("reason_call_ta", ""),
            "suggested_profile": it.get("suggested_profile", PROFILE_FULL_TA),
            "requires_confirmation": it.get("requires_confirmation", True),
            "notify_level": _notify_level_for(priority=priority, record_only=record_only),
            "source": it.get("source", "controller_hints"),
            "as_of": as_of,
        })
    return out


def _build_watch_items(context: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
    """从 IC-TA-003 daily_report_only 路由出仅观察项，补 notify_level."""
    daily_list = _ic_ta003._route_daily_report_only(context, as_of)
    out: list[dict[str, Any]] = []
    for it in daily_list:
        out.append({
            "symbol": it.get("symbol", ""),
            "name": it.get("name", ""),
            "origin": it.get("origin", ""),
            "reason_skip_ta": it.get("reason_skip_ta", ""),
            "notify_level": NOTIFY_LEVEL_DAILY_DIGEST,  # 仅日报项天然进日报
            "source": it.get("source", "controller_hints"),
            "as_of": as_of,
        })
    return out


def _build_data_warnings(context: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
    """数据缺口 + 数据健康告警 + 持仓风险，统一成 data_warnings 列表.

    每条带 warning_type / reason / notify_level。
    """
    warnings: list[dict[str, Any]] = []

    # 1. 报告数据缺口（需人工复核）—— from IC-TA-003 needs_manual_confirmation
    manual_list = _ic_ta003._route_needs_manual_confirmation(context, as_of)
    for it in manual_list:
        warnings.append({
            "symbol": it.get("symbol", ""),
            "name": it.get("name", ""),
            "warning_type": WARNING_REPORT_DATA_GAP,
            "reason": it.get("reason", "最近报告存在关键数据缺口，作为强结论前需人工复核"),
            "fields": it.get("fields", []) or [],
            "priority": "P2",
            "notify_level": NOTIFY_LEVEL_DAILY_DIGEST,
            "source": it.get("source", "controller_hints"),
            "as_of": as_of,
        })

    # 2. 数据源失败 / stale（系统级，symbol 为空）
    warnings.extend(_build_data_health_warnings(context, as_of))

    return warnings


def _build_data_health_warnings(
    context: dict[str, Any], as_of: str
) -> list[dict[str, Any]]:
    """数据源失败 / stale 告警（系统级，只进日报 record_only）."""
    health = _bucket(context, "data_health")
    data_status = str(health.get("data_status", "missing"))
    sources = health.get("sources", []) or []
    failed_sources = [
        s for s in sources
        if str(s.get("status", "")).upper() in {"FAILED", "ERROR"}
    ]
    out: list[dict[str, Any]] = []
    if failed_sources or data_status == "failed":
        names = ", ".join(
            str(s.get("name", s.get("source", "?"))) for s in failed_sources[:5]
        ) or "未知源"
        out.append({
            "symbol": "",
            "name": "SYSTEM",
            "warning_type": WARNING_DATA_SOURCE_FAILURE,
            "reason": f"以下数据源状态失败：{names}。候选池与 TA 报告可能基于降级数据。",
            "fields": [],
            "priority": "P2",
            "notify_level": NOTIFY_LEVEL_DAILY_DIGEST,
            "source": "tradeflow_data_health",
            "as_of": as_of,
        })
    elif data_status == "stale" and context.get("is_trading_day"):
        out.append({
            "symbol": "",
            "name": "SYSTEM",
            "warning_type": WARNING_DATA_STALE,
            "reason": "交易日实时行情降级为 stale，盘中提醒可能基于延迟数据。",
            "fields": [],
            "priority": "P2",
            "notify_level": NOTIFY_LEVEL_DAILY_DIGEST,
            "source": "tradeflow_data_health",
            "as_of": as_of,
        })
    return out


def _build_holdings_risk_warnings(
    context: dict[str, Any], as_of: str
) -> list[dict[str, Any]]:
    """持仓盘中风险告警（大跌 P0 / 中跌 P1）。用于盘中场景."""
    holdings = _bucket(context, "holdings")
    out: list[dict[str, Any]] = []
    for h in _items(holdings):
        symbol = str(h.get("symbol", "")).strip()
        name = str(h.get("name", symbol))
        change_pct = _to_float(h.get("price_change_pct"))
        live_price = _to_float(h.get("live_price"))
        record_only = live_price == 0.0
        if change_pct != 0.0 and change_pct <= -3.0:
            out.append({
                "symbol": symbol,
                "name": name,
                "warning_type": WARNING_HOLDINGS_RISK_LARGE,
                "reason": f"持仓 {symbol} ({name}) 当前跌幅 {change_pct:.2f}%，超过 -3.0% 阈值，需关注风险。",
                "fields": [],
                "priority": "P0",
                "notify_level": _notify_level_for(priority="P0", record_only=record_only),
                "source": "holdings_snapshot",
                "as_of": as_of,
            })
        elif change_pct != 0.0 and change_pct <= -1.5:
            out.append({
                "symbol": symbol,
                "name": name,
                "warning_type": WARNING_HOLDINGS_RISK_MODERATE,
                "reason": f"持仓 {symbol} ({name}) 当前跌幅 {change_pct:.2f}%，超过 -1.5% 关注线。",
                "fields": [],
                "priority": "P1",
                "notify_level": _notify_level_for(priority="P1", record_only=record_only),
                "source": "holdings_snapshot",
                "as_of": as_of,
            })
    return out


def _notify_level_summary(
    ta_requests: list[dict[str, Any]],
    watch_items: list[dict[str, Any]],
    data_warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """汇总 notify_level 计数（不应用去噪，只做通道分类统计）."""
    push = digest = 0
    for bucket in (ta_requests, watch_items, data_warnings):
        for it in bucket:
            lvl = it.get("notify_level")
            if lvl == NOTIFY_LEVEL_INTRADAY_PUSH:
                push += 1
            else:
                digest += 1
    return {
        NOTIFY_LEVEL_INTRADAY_PUSH: push,
        NOTIFY_LEVEL_DAILY_DIGEST: digest,
        "intraday_push_count": push,
        "daily_digest_count": digest,
        "dedup_applied": False,  # briefing 本身不去噪；去噪在 notify dry-run 通道
        "note": "briefing 只做通道分类，去噪由 notify dry-run 通道负责",
    }


def _assemble_payload(
    *,
    scene: str,
    context: dict[str, Any],
    as_of: str,
    summary: str,
    ta_requests: list[dict[str, Any]],
    watch_items: list[dict[str, Any]],
    data_warnings: list[dict[str, Any]],
    scene_extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装统一 payload（含 notify_level_summary / markdown_preview / 禁用词自检）."""
    notify_summary = _notify_level_summary(ta_requests, watch_items, data_warnings)
    payload: dict[str, Any] = {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "scene": scene,
        "as_of": as_of,
        "generated_by": PAYLOAD_SOURCE,
        "read_only": True,
        "dry_run": True,
        "summary": summary,
        "previous_trade_date": context.get("previous_trade_date", ""),
        "is_trading_day": context.get("is_trading_day"),
        "ta_requests": ta_requests,
        "watch_items": watch_items,
        "data_warnings": data_warnings,
        "notify_level": notify_summary,
        "scene_extras": scene_extras or {},
        "notes": _build_scene_notes(scene, ta_requests, watch_items, data_warnings),
    }
    payload["markdown_preview"] = render_briefing_markdown(payload)
    payload["forbidden_word_scan"] = scan_forbidden_words(payload)
    return payload


def _build_scene_notes(
    scene: str,
    ta_requests: list[dict[str, Any]],
    watch_items: list[dict[str, Any]],
    data_warnings: list[dict[str, Any]],
) -> list[str]:
    notes: list[str] = []
    scene_label = {"pre_market": "盘前", "intraday": "盘中", "post_market": "盘后"}[scene]
    if scene == SCENE_INTRADAY:
        notes.append("盘中 briefing：不调度新 TA，只观察与告警；TA 调度由盘前/盘后负责")
    if not ta_requests:
        if scene != SCENE_INTRADAY:
            notes.append(f"{scene_label} briefing：无标的进入 TA 队列，避免无谓调用模型")
    if not watch_items and not data_warnings:
        notes.append(f"{scene_label} briefing：暂无观察项与告警，investment-controller 本轮可保持轻量")
    return notes


# ──────────────────────────────────────────────────────────────────────────────
# 场景 1：盘前 briefing
# ──────────────────────────────────────────────────────────────────────────────

def build_pre_market_payload(
    context: dict[str, Any], *, as_of: str | None = None
) -> dict[str, Any]:
    """盘前 briefing：TA 调度 + 昊天主题重心 + 数据健康.

    盘前是 investment-controller 的主调度窗口：决定今天调哪些 TA、哪些只进日报、
    哪些有数据缺口需人工复核。
    """
    ts = _safe_now(as_of)
    ta_requests = _build_ta_requests(context, ts)
    watch_items = _build_watch_items(context, ts)
    data_warnings = _build_data_warnings(context, ts)

    mandate = _bucket(context, "mandate_daily_report")
    mandate_digest = _ic_ta003._build_mandate_digest(mandate)
    health = _bucket(context, "data_health")
    data_health_note = _ic_ta003._build_data_health_note(health, pre_market=True)

    summary = _headline(
        scene=SCENE_PRE_MARKET,
        ta_n=len(ta_requests),
        watch_n=len(watch_items),
        warn_n=len(data_warnings),
        extra=_ic_ta003._mandate_headline_extra(mandate_digest),
    )

    return _assemble_payload(
        scene=SCENE_PRE_MARKET,
        context=context,
        as_of=ts,
        summary=summary,
        ta_requests=ta_requests,
        watch_items=watch_items,
        data_warnings=data_warnings,
        scene_extras={
            "mandate_digest": mandate_digest,
            "data_health_note": data_health_note,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# 场景 2：盘中 briefing
# ──────────────────────────────────────────────────────────────────────────────

def build_intraday_payload(
    context: dict[str, Any], *, as_of: str | None = None
) -> dict[str, Any]:
    """盘中 briefing：观察仓触发监控 + 持仓风险告警.

    盘中**不调度新 TA**（那是盘前 / 盘后的职责）。盘中只回答：
    - 哪些观察仓标的接近触发 / 已进入买入区 / 已失效（→ watch_items / data_warnings）。
    - 哪些持仓出现盘中风险（大跌 / 中跌 → data_warnings）。
    - 数据源是否健康（→ data_warnings）。
    """
    ts = _safe_now(as_of)
    is_trading = bool(context.get("is_trading_day", True))

    # 盘中不调度新 TA：ta_requests 恒为空
    ta_requests: list[dict[str, Any]] = []

    watch_items = _build_intraday_watch_items(context, ts, is_trading)
    data_warnings: list[dict[str, Any]] = []
    data_warnings.extend(_build_holdings_risk_warnings(context, ts))
    data_warnings.extend(_build_intraday_observation_warnings(context, ts, is_trading))
    data_warnings.extend(_build_data_health_warnings(context, ts))

    summary = _headline(
        scene=SCENE_INTRADAY,
        ta_n=0,
        watch_n=len(watch_items),
        warn_n=len(data_warnings),
        extra="" if is_trading else "非交易日，盘中观察暂停",
    )

    health = _bucket(context, "data_health")
    return _assemble_payload(
        scene=SCENE_INTRADAY,
        context=context,
        as_of=ts,
        summary=summary,
        ta_requests=ta_requests,
        watch_items=watch_items,
        data_warnings=data_warnings,
        scene_extras={
            "is_trading_day": is_trading,
            "data_health_note": _ic_ta003._build_data_health_note(health, pre_market=False),
        },
    )


def _build_intraday_watch_items(
    context: dict[str, Any], as_of: str, is_trading_day: bool
) -> list[dict[str, Any]]:
    """盘中观察项：从观察仓派生状态，near_entry / in_entry_zone / watching 进观察.

    in_entry_zone 是 P0（接近可执行），near_entry 是 P1，watching/missed_entry 是 P2/P3。
    这些项不调度 TA，只提示 investment-controller 盯盘。
    """
    observation = _bucket(context, "observation_warehouse")
    out: list[dict[str, Any]] = []
    for item in _items(observation):
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            continue
        result = evaluate_observation_state(item, is_trading_day=is_trading_day)
        state = result.get("state", STATE_WATCHING)
        reason_text = result.get("reason", "")
        # 状态 → 优先级（与 NOTIFY 观察仓事件对齐）
        if state == STATE_IN_ENTRY_ZONE:
            priority = "P0"
        elif state == STATE_NEAR_ENTRY:
            priority = "P1"
        elif state == STATE_TA_REQUIRED:
            priority = "P1"
        elif state in (STATE_MISSED_ENTRY, STATE_DATA_MISSING):
            priority = "P2"
        else:
            priority = "P3"
        record_only = state in (STATE_DATA_MISSING, STATE_NEEDS_REVIEW)
        out.append({
            "symbol": symbol,
            "name": item.get("name", symbol),
            "origin": "observation_warehouse",
            "reason_skip_ta": (
                f"盘中观察（派生状态={state}）：{reason_text}。"
                "本轮不调度 TA，由 investment-controller 盯盘。"
            ),
            "state": state,
            "priority": priority,
            "notify_level": _notify_level_for(priority=priority, record_only=record_only),
            "source": "observation_warehouse",
            "as_of": as_of,
        })
    return out


def _build_intraday_observation_warnings(
    context: dict[str, Any], as_of: str, is_trading_day: bool
) -> list[dict[str, Any]]:
    """已失效观察仓 → data_warnings（P1，需复盘）."""
    observation = _bucket(context, "observation_warehouse")
    out: list[dict[str, Any]] = []
    for item in _items(observation):
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            continue
        result = evaluate_observation_state(item, is_trading_day=is_trading_day)
        state = result.get("state", STATE_WATCHING)
        if state == STATE_INVALIDATED:
            out.append({
                "symbol": symbol,
                "name": item.get("name", symbol),
                "warning_type": WARNING_OBSERVATION_INVALIDATED,
                "reason": f"观察仓 {symbol} 已跌破失效价：{result.get('reason', '')}",
                "fields": [],
                "priority": "P1",
                "notify_level": _notify_level_for(priority="P1"),
                "source": "observation_warehouse",
                "as_of": as_of,
            })
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 场景 3：盘后 briefing
# ──────────────────────────────────────────────────────────────────────────────

def build_post_market_payload(
    context: dict[str, Any], *, as_of: str | None = None
) -> dict[str, Any]:
    """盘后 briefing：今日表现 + 报告数据缺口 + 次日 TA 候选.

    盘后回答：今天发生了什么、数据缺什么、明天要不要调 TA。
    """
    ts = _safe_now(as_of)
    ta_requests = _build_ta_requests(context, ts)
    watch_items = _build_watch_items(context, ts)
    data_warnings = _build_data_warnings(context, ts)

    today_perf = _ic_ta003._build_today_performance(_bucket(context, "holdings"))
    report_gaps = _ic_ta003._build_report_gaps_summary(
        _bucket(context, "recent_report_data_blockers")
    )

    summary = _headline(
        scene=SCENE_POST_MARKET,
        ta_n=len(ta_requests),
        watch_n=len(watch_items),
        warn_n=len(data_warnings),
        extra=_ic_ta003._post_headline_extra(today_perf, report_gaps),
    )

    return _assemble_payload(
        scene=SCENE_POST_MARKET,
        context=context,
        as_of=ts,
        summary=summary,
        ta_requests=ta_requests,
        watch_items=watch_items,
        data_warnings=data_warnings,
        scene_extras={
            "today_performance": today_perf,
            "report_data_gaps": report_gaps,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# headline helper
# ──────────────────────────────────────────────────────────────────────────────

def _headline(
    *, scene: str, ta_n: int, watch_n: int, warn_n: int, extra: str = ""
) -> str:
    scene_label = {
        SCENE_PRE_MARKET: "盘前",
        SCENE_INTRADAY: "盘中",
        SCENE_POST_MARKET: "盘后",
    }[scene]
    parts: list[str] = []
    if scene != SCENE_INTRADAY:
        parts.append(f"{ta_n} 项待 TA")
    parts.append(f"{watch_n} 项观察")
    parts.append(f"{warn_n} 项告警")
    head = f"{scene_label} briefing：" + "，".join(parts)
    if extra:
        head += f"；{extra}"
    return head


# ──────────────────────────────────────────────────────────────────────────────
# markdown 预览（供飞书卡片后续接入）
# ──────────────────────────────────────────────────────────────────────────────

def render_briefing_markdown(payload: dict[str, Any]) -> str:
    """渲染 briefing markdown 预览（供飞书卡片后续接入，dry-run 不发送）.

    结构：
    - 标题 + scene + summary
    - ta_requests（待调度 TA）
    - watch_items（仅观察）
    - data_warnings（告警）
    - notify_level 汇总
    """
    scene = payload.get("scene", "")
    scene_label = {
        SCENE_PRE_MARKET: "盘前",
        SCENE_INTRADAY: "盘中",
        SCENE_POST_MARKET: "盘后",
    }.get(scene, scene)
    lines: list[str] = [
        f"# {scene_label} Briefing（dry-run）",
        "",
        f"- 场景：`{scene}`",
        f"- 生成时间：{payload.get('as_of', '')}",
        f"- 摘要：{payload.get('summary', '')}",
        f"- 只读：{payload.get('read_only', True)}（investment-controller 不下最终交易结论）",
        "",
    ]

    def _section(title: str, items: list[dict[str, Any]], reason_key: str) -> None:
        if not items:
            lines.append(f"## {title}\n\n_（无）_\n")
            return
        lines.append(f"## {title}\n")
        for it in items:
            sym = it.get("symbol", "—") or "—"
            name = it.get("name", "")
            pri = it.get("priority", "")
            lvl = it.get("notify_level", "")
            lvl_label = "盘中" if lvl == NOTIFY_LEVEL_INTRADAY_PUSH else "日报"
            head = f"- **[{pri}] {sym} {name}**".rstrip()
            if lvl:
                head += f" _（{lvl_label}）_"
            lines.append(head)
            reason = it.get(reason_key) or it.get("reason", "")
            if reason:
                lines.append(f"  - {reason}")
            if it.get("suggested_profile"):
                lines.append(f"  - 建议 profile：{it['suggested_profile']}")
            if it.get("requires_confirmation"):
                lines.append("  - 需人工确认")
        lines.append("")

    _section("待调度 TA（ta_requests）", payload.get("ta_requests", []), "reason_call_ta")
    _section("仅观察（watch_items）", payload.get("watch_items", []), "reason_skip_ta")
    _section("告警（data_warnings）", payload.get("data_warnings", []), "reason")

    notify = payload.get("notify_level", {}) or {}
    lines.append("## notify_level 汇总\n")
    lines.append(f"- 盘中主动提醒：{notify.get('intraday_push_count', 0)} 条")
    lines.append(f"- 夜间日报：{notify.get('daily_digest_count', 0)} 条")
    lines.append(f"- 去噪：{notify.get('dedup_applied', False)}（briefing 只分类，去噪在 notify 通道）")
    lines.append("")

    notes = payload.get("notes", []) or []
    if notes:
        lines.append("## 备注\n")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# dry-run fixture 入口
# ──────────────────────────────────────────────────────────────────────────────

def dry_run_all_scene_fixtures(*, as_of: str | None = None) -> dict[str, Any]:
    """跑三场景内置 fixture，返回 dry-run 汇总（不写文件、不联网）.

    供测试 / investment-controller 联调 / 文档生成使用。返回结构::

        {
            "pre_market": <pre_market payload>,
            "intraday":   <intraday payload>,
            "post_market": <post_market payload>,
            "summary": {
                "forbidden_word_scan_passed": bool,
                "pre_ta_count": int,
                "intraday_watch_count": int,
                "post_ta_count": int,
                ...
            },
        }
    """
    pre = build_pre_market_payload(PRE_MARKET_FIXTURE_CONTEXT, as_of=as_of)
    intraday = build_intraday_payload(INTRADAY_FIXTURE_CONTEXT, as_of=as_of)
    post = build_post_market_payload(POST_MARKET_FIXTURE_CONTEXT, as_of=as_of)

    scans = [
        pre.get("forbidden_word_scan", {}),
        intraday.get("forbidden_word_scan", {}),
        post.get("forbidden_word_scan", {}),
    ]
    return {
        "pre_market": pre,
        "intraday": intraday,
        "post_market": post,
        "summary": {
            "forbidden_word_scan_passed": not any(s.get("found", False) for s in scans),
            "pre_ta_count": len(pre.get("ta_requests", [])),
            "pre_watch_count": len(pre.get("watch_items", [])),
            "pre_warning_count": len(pre.get("data_warnings", [])),
            "intraday_ta_count": len(intraday.get("ta_requests", [])),
            "intraday_watch_count": len(intraday.get("watch_items", [])),
            "intraday_warning_count": len(intraday.get("data_warnings", [])),
            "post_ta_count": len(post.get("ta_requests", [])),
            "post_watch_count": len(post.get("watch_items", [])),
            "post_warning_count": len(post.get("data_warnings", [])),
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "generated_by": PAYLOAD_SOURCE,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# 内置 dry-run fixture（不代表真实行情，仅供联调 / 测试 / 文档）
# ──────────────────────────────────────────────────────────────────────────────
# 盘前 / 盘后 fixture 复用 IC-TA-003 的 PRE_MARKET_FIXTURE_CONTEXT /
# POST_MARKET_FIXTURE_CONTEXT（形状一致）。盘中需要带 live_price 的观察仓 +
# 持仓风险数据，单独构造。

_INTRADAY_AS_OF = "2026-06-26 10:30:00"


def _intraday_observation_item(
    *, symbol: str, name: str, status: str, live_price: float | None,
    entry_low: float, entry_high: float, trigger_price: float,
    invalid_price: float, reason: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "status": status,
        "horizon": "mid",
        "live_price": live_price,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "trigger_price": trigger_price,
        "invalid_price": invalid_price,
        "reason": reason,
        "source": "observation_warehouse",
        "as_of": _INTRADAY_AS_OF,
    }


def _intraday_holding(
    *, symbol: str, name: str, position: float, avg_cost: float,
    live_price: float, price_change_pct: float,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "current_position": position,
        "average_cost": avg_cost,
        "live_price": live_price,
        "price_change_pct": price_change_pct,
        "floating_pnl_pct": price_change_pct,
        "latest_report": {
            "report_id": f"r-{symbol.split('.')[0]}",
            "decision": "HOLD",
            "action_label": "持有",
            "research_direction": "中性",
        },
        "source": "imported_portfolio",
        "as_of": _INTRADAY_AS_OF,
    }


INTRADAY_FIXTURE_CONTEXT: dict[str, Any] = {
    "schema_version": "1.0",
    "as_of": _INTRADAY_AS_OF,
    "previous_trade_date": "2026-06-25",
    "is_trading_day": True,
    "generated_by": "investment_controller_context",
    "read_only": True,
    # 持仓：1 只大跌（P0）+ 1 只平稳
    "holdings": {
        "source": "imported_portfolio",
        "as_of": _INTRADAY_AS_OF,
        "data_status": "fresh",
        "count": 2,
        "items": [
            _intraday_holding(
                symbol="603629.SH", name="苏利股份", position=100, avg_cost=14.80,
                live_price=14.21, price_change_pct=-3.99,
            ),
            _intraday_holding(
                symbol="600584.SH", name="华天科技", position=200, avg_cost=11.20,
                live_price=11.30, price_change_pct=0.89,
            ),
        ],
    },
    # 观察仓：1 只进入买入区（P0）+ 1 只接近买点（P1）+ 1 只持续观察（P3）
    "observation_warehouse": {
        "source": "observation_warehouse",
        "as_of": _INTRADAY_AS_OF,
        "data_status": "fresh",
        "count": 3,
        "summary": {"by_status": {"in_entry_zone": 1, "near_entry": 1, "watching": 1}},
        "items": [
            _intraday_observation_item(
                symbol="601689.SH", name="拓普集团", status="ta_required",
                live_price=30.2, entry_low=29.0, entry_high=30.5,
                trigger_price=30.0, invalid_price=28.0,
                reason="政策利好接近买点，需 TA 确认入场区间",
            ),
            _intraday_observation_item(
                symbol="002353.SZ", name="杰瑞股份", status="watching",
                live_price=33.6, entry_low=34.0, entry_high=35.5,
                trigger_price=35.0, invalid_price=33.0,
                reason="能源主题观察，价格接近买点下沿",
            ),
            _intraday_observation_item(
                symbol="688256.SH", name="寒武纪", status="watching",
                live_price=250.0, entry_low=225.0, entry_high=240.0,
                trigger_price=240.0, invalid_price=215.0,
                reason="半导体左侧观察，价格已偏离区间上沿",
            ),
        ],
    },
    # 候选池（盘中不调度 TA，仅保持上下文完整）
    "tradeflow_candidates": {
        "source": "tradeflow_candidates",
        "as_of": _INTRADAY_AS_OF,
        "trade_date": "2026-06-25",
        "data_status": "fresh",
        "count": 0,
        "pool_counts": {},
        "summary_agg": {},
        "items": [],
    },
    "latest_ta_reports": {
        "source": "ta_report",
        "as_of": _INTRADAY_AS_OF,
        "data_status": "fresh",
        "count": 0,
        "items": [],
    },
    "data_health": {
        "source": "tradeflow_data_health",
        "as_of": _INTRADAY_AS_OF,
        "data_status": "fresh",
        "tradeflow_db_available": True,
        "latest_plan_date": "2026-06-25",
        "latest_candidates_date": "2026-06-25",
        "latest_effective_trade_date": "2026-06-25",
        "latest_observe_date": "2026-06-25",
        "total_candidates_today": 0,
        "total_signals_today": 0,
        "sources": [],
    },
    "pending_ta_required": {
        "source": "pending_ta_required",
        "as_of": _INTRADAY_AS_OF,
        "data_status": "fresh",
        "count": 0,
        "items": [],
    },
    "mandate_daily_report": {
        "source": "mandate_daily_report",
        "as_of": _INTRADAY_AS_OF,
        "report_as_of": "2026-06-25",
        "data_status": "missing",
        "rising_topic_count": 0,
        "cooling_topic_count": 0,
        "main_candidate_count": 0,
        "observation_candidate_count": 0,
        "evidence_gap_count": 0,
        "rising_topics": [],
        "cooling_topics": [],
        "main_candidates": [],
        "observation_candidates": [],
        "evidence_gaps": [],
    },
    "recent_report_data_blockers": {
        "source": "recent_report_data_blockers",
        "as_of": _INTRADAY_AS_OF,
        "data_status": "fresh",
        "scanned_report_count": 0,
        "affected_report_count": 0,
        "total_severe_blockers": 0,
        "summary_level": "ok",
        "field_counts": {},
        "affected_symbols": [],
    },
    # 盘中 controller_hints 为空（盘中不调度 TA）
    "controller_hints": {
        "source": "controller_hints",
        "as_of": _INTRADAY_AS_OF,
        "data_status": "fresh",
        "needs_ta": [],
        "daily_report_only": [],
        "suppress_push_data_insufficient": [],
    },
    "notes": [],
    "runtime_tier_meta": {
        "runtime_tier": "FAST_RADAR",
        "expected_latency": "5-30s",
        "llm_allowed": False,
        "requires_confirmation": False,
        "cost_risk": "none",
    },
}


__all__ = [
    "ALLOWED_SCENES",
    "FORBIDDEN_STRONG_WORDS",
    "INTRADAY_FIXTURE_CONTEXT",
    "NOTIFY_LEVEL_DAILY_DIGEST",
    "NOTIFY_LEVEL_INTRADAY_PUSH",
    "PAYLOAD_SCHEMA_VERSION",
    "PAYLOAD_SOURCE",
    "POST_MARKET_FIXTURE_CONTEXT",
    "PRE_MARKET_FIXTURE_CONTEXT",
    "SCENE_INTRADAY",
    "SCENE_POST_MARKET",
    "SCENE_PRE_MARKET",
    "WARNING_DATA_SOURCE_FAILURE",
    "WARNING_DATA_STALE",
    "WARNING_HOLDINGS_RISK_LARGE",
    "WARNING_HOLDINGS_RISK_MODERATE",
    "WARNING_OBSERVATION_INVALIDATED",
    "WARNING_REPORT_DATA_GAP",
    "build_intraday_payload",
    "build_post_market_payload",
    "build_pre_market_payload",
    "dry_run_all_scene_fixtures",
    "render_briefing_markdown",
]


def _self_check() -> None:
    """模块导入时自检 fixture 可序列化、可构建、无禁用词（仅 debug 用）."""
    report = dry_run_all_scene_fixtures()
    assert report["summary"]["forbidden_word_scan_passed"], "fixture 含禁用词"
    json.dumps(report)
