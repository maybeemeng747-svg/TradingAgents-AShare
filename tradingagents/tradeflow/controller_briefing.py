# [IC-TA-003] controller_briefing_dry_run
"""investment-controller 盘前 / 盘后 briefing 纯引擎（dry-run）.

IC-TA-002 把昊天日报 / 报告数据缺口接进 ``investment_controller_context`` 之后，
investment-controller 需要一份**流程提示 + 调度建议**，而不是另一份 TA 结论。
本引擎把 IC-TA-002 的只读上下文包路由成两条 briefing：

1. ``build_pre_market_briefing`` — 盘前：把待 TA / 仅日报 / 需人工确认 三条
   分流落地，每条都带**为什么要调 / 为什么不要调 TA** 的理由。
2. ``build_post_market_briefing`` — 盘后：今日持仓表现 + 报告数据缺口 + 次日
   TA 候选，回答"今天发生了什么、数据缺什么、明天要不要调 TA"。

设计契约（见 docs/TASKS.md IC-TA-003）
--------------------------------------
* **纯函数**：不访问数据库、不联网、不读环境变量、不发送飞书、不写文件。
  输入是 IC-TA-001 / IC-TA-002 的 ``get_investment_controller_context`` 返回的
  dict（或等价 fixture）；输出是稳定的 briefing dict。
* **不调用 LLM**：所有理由由规则模板生成。
* **不强动作词**：引擎输出永不出现 ``FORBIDDEN_STRONG_WORDS`` 中的强动作词；
  ``scan_forbidden_words`` 提供自检。
* **稳定空结构**：缺数据 / 缺 bucket 时返回空数组 + 明确 note，不抛异常。
* 每条条目带 ``source`` + ``as_of``，与 IC-TA-001 数据契约一致。

引擎不持久化任何状态；``PRE_MARKET_FIXTURE_CONTEXT`` /
``POST_MARKET_FIXTURE_CONTEXT`` 是模块内置的 dry-run fixture，供测试与
investment-controller 联调使用，不代表真实行情。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

# 复用 observation_state_engine 的强动作禁用词，保持全链路一致
from tradingagents.tradeflow.observation_state_engine import (
    FORBIDDEN_STRONG_WORDS as _OBS_FORBIDDEN_WORDS,
)

# [IC-TA-003] controller_briefing_dry_run
# ── Schema / 常量 ────────────────────────────────────────────────────────────

BRIEFING_SCHEMA_VERSION = "1.0"
BRIEFING_SOURCE = "controller_briefing_engine"

BRIEFING_PRE_MARKET = "pre_market"
BRIEFING_POST_MARKET = "post_market"
ALLOWED_BRIEFING_TYPES = (BRIEFING_PRE_MARKET, BRIEFING_POST_MARKET)

# 强动作禁用词（引擎输出不得出现）。在 observation_state_engine 基础上补齐
# briefing 场景常见的强词，去重保序。
FORBIDDEN_STRONG_WORDS: tuple[str, ...] = tuple(dict.fromkeys(
    _OBS_FORBIDDEN_WORDS + ("立即卖出", "全仓", "必涨", "必跌", "无脑买", "加杠杆")
))

# 轻量 TA Profile（与 PERF-002 对齐，仅供 suggested_profile 字段引用）
PROFILE_MIDLINE_POLICY_LIGHT = "MIDLINE_POLICY_LIGHT"
PROFILE_SHORT_TECH_LIGHT = "SHORT_TECH_LIGHT"
PROFILE_POSITION_RISK_LIGHT = "POSITION_RISK_LIGHT"
PROFILE_FULL_TA = "FULL_TA"

# candidate_type → 建议 profile 路由
_CANDIDATE_TYPE_PROFILE = {
    "POLICY_AMBUSH": PROFILE_MIDLINE_POLICY_LIGHT,
    "POLICY_CONFIRM": PROFILE_MIDLINE_POLICY_LIGHT,
    "EVENT_WATCH": PROFILE_MIDLINE_POLICY_LIGHT,
    "TECH_TRADE": PROFILE_SHORT_TECH_LIGHT,
    "PSEUDO_POLICY": PROFILE_SHORT_TECH_LIGHT,
    "OVERHEATED_AVOID": PROFILE_POSITION_RISK_LIGHT,
}

# observation status → 是否进 TA 队列的软判定
_OBS_STATUS_TA_TRIGGER = frozenset({"ta_required", "in_entry_zone", "near_entry"})
_OBS_STATUS_DAILY_ONLY = frozenset({"watching", "missed_entry"})


# ──────────────────────────────────────────────────────────────────────────────
# 公共路由 helpers
# ──────────────────────────────────────────────────────────────────────────────

def scan_forbidden_words(payload: dict[str, Any]) -> dict[str, Any]:
    """自检：扫描 briefing payload 是否出现强动作禁用词.

    只扫描引擎合成的文本字段（headline / reason / note /
    reason_call_ta / reason_skip_ta），TA 报告原始 decision 等结构化事实字段
    不属于引擎输出，不在扫描范围（与 IC-TA-001 ``assert_no_strong_action_verbs``
    的边界一致）。

    Returns:
        ``{"found": bool, "hits": [{"path": str, "word": str}], "scanned_words": tuple}``
    """
    hits: list[dict[str, str]] = []

    def _walk(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                # 跳过非引擎合成的原始事实字段（TA decision 等）。
                if k in {"decision", "direction", "execution_action", "final_trade_decision"}:
                    continue
                _walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk(v, f"{path}[{i}]")
        elif isinstance(obj, str):
            for word in FORBIDDEN_STRONG_WORDS:
                if word in obj:
                    hits.append({"path": path, "word": word})

    _walk(payload, "root")
    return {
        "found": bool(hits),
        "hits": hits,
        "scanned_words": FORBIDDEN_STRONG_WORDS,
    }


def _safe_now(as_of: str | None) -> str:
    if as_of:
        return as_of
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _bucket(context: dict[str, Any], key: str) -> dict[str, Any]:
    """防御性取 bucket：缺失时返回空 dict，不抛异常。"""
    bucket = context.get(key)
    return bucket if isinstance(bucket, dict) else {}


def _items(bucket: dict[str, Any]) -> list[dict[str, Any]]:
    raw = bucket.get("items")
    return [it for it in (raw or []) if isinstance(it, dict)]


def _suggest_profile(*, candidate_type: str = "", origin: str = "") -> str:
    """根据 candidate_type / origin 推荐轻量 TA profile.

    无法明确路由时返回 ``FULL_TA``（需人工确认，符合 PERF-004 门禁）。
    """
    if candidate_type and candidate_type in _CANDIDATE_TYPE_PROFILE:
        return _CANDIDATE_TYPE_PROFILE[candidate_type]
    if origin == "observation_warehouse":
        # 持仓/观察仓复盘默认走持仓风控 profile
        return PROFILE_POSITION_RISK_LIGHT
    return PROFILE_FULL_TA


def _route_ta_to_schedule(
    context: dict[str, Any], as_of: str
) -> list[dict[str, Any]]:
    """从 controller_hints.needs_ta + pending_ta_required 路由出待 TA 队列.

    每条带 ``reason_call_ta``（**为什么调 TA**）。
    """
    hints = _bucket(context, "controller_hints")
    pending = _bucket(context, "pending_ta_required")
    # candidate_type 索引：从 tradeflow_candidates 里取，帮助推荐 profile
    cand_types: dict[str, str] = {}
    for c in _items(_bucket(context, "tradeflow_candidates")):
        sym = c.get("symbol", "")
        if sym:
            cand_types[sym] = c.get("candidate_type", "") or ""

    # pending_ta_required 提供更完整的 origin/reason
    pending_by_symbol: dict[str, dict[str, Any]] = {}
    for it in _items(pending):
        sym = it.get("symbol", "")
        if sym:
            pending_by_symbol[sym] = it

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    # 先吃 controller_hints.needs_ta（已经做过去重）
    for item in hints.get("needs_ta", []) or []:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        origin = item.get("origin", "") or item.get("source", "")
        cand_type = cand_types.get(sym, "")
        # 若 origin 是观察仓但其实是候选池来的，用 candidate_type 兜底
        if not cand_type and origin == "tradeflow_candidates":
            cand_type = "TECH_TRADE"
        profile = _suggest_profile(candidate_type=cand_type, origin=origin)
        base_reason = (
            pending_by_symbol.get(sym, {}).get("reason")
            or item.get("reason")
            or ""
        )
        reason_call_ta = _compose_call_ta_reason(origin, cand_type, base_reason)
        out.append({
            "symbol": sym,
            "name": item.get("name", ""),
            "origin": origin,
            "candidate_type": cand_type,
            "priority": _priority_for_ta(origin, cand_type),
            "reason_call_ta": reason_call_ta,
            "suggested_profile": profile,
            "requires_confirmation": profile == PROFILE_FULL_TA,
            "source": item.get("source", "controller_hints"),
            "as_of": as_of,
        })
    return out


def _compose_call_ta_reason(origin: str, candidate_type: str, base_reason: str) -> str:
    """合成"为什么要调 TA"的理由（规则模板，无强动作词）."""
    why = "建议进入 TA 队列："
    if origin == "observation_warehouse":
        why += "观察仓已标记需要 TA 确认"
    elif origin == "tradeflow_candidates":
        why += "候选池标记需要深度 TA"
    else:
        why += "存在待确认事项"
    if candidate_type:
        type_label = {
            "POLICY_AMBUSH": "政策左侧埋伏",
            "POLICY_CONFIRM": "政策右侧确认",
            "TECH_TRADE": "技术交易",
            "EVENT_WATCH": "事件观察",
        }.get(candidate_type, candidate_type)
        why += f"（{type_label}）"
    if base_reason:
        why += f"；依据：{base_reason}"
    return why


def _priority_for_ta(origin: str, candidate_type: str) -> str:
    """待 TA 项的优先级（与 today_guidance P0-P3 对齐）."""
    if candidate_type in {"POLICY_AMBUSH", "POLICY_CONFIRM"}:
        return "P1"
    if origin == "observation_warehouse":
        return "P1"
    return "P2"


def _route_daily_report_only(
    context: dict[str, Any], as_of: str
) -> list[dict[str, Any]]:
    """从 controller_hints.daily_report_only 路由出仅日报项.

    每条带 ``reason_skip_ta``（**为什么不调 TA**）。
    """
    hints = _bucket(context, "controller_hints")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in hints.get("daily_report_only", []) or []:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        origin = item.get("origin", "") or item.get("source", "")
        out.append({
            "symbol": sym,
            "name": item.get("name", ""),
            "origin": origin,
            "reason_skip_ta": _compose_skip_ta_reason(origin, item.get("reason", "")),
            "source": item.get("source", "controller_hints"),
            "as_of": as_of,
        })
    return out


def _compose_skip_ta_reason(origin: str, base_reason: str) -> str:
    """合成"为什么不调 TA"的理由（规则模板，无强动作词）."""
    if origin == "mandate_observation":
        why = "昊天主题观察候选，仅进日报；待主题升温或进入触发区再升级"
    elif origin == "observation_warehouse":
        why = "被动观察状态，价格未进入触发区，本轮仅记录"
    else:
        why = "软状态记录，暂不触发 TA"
    if base_reason:
        why += f"；依据：{base_reason}"
    return why


def _route_needs_manual_confirmation(
    context: dict[str, Any], as_of: str
) -> list[dict[str, Any]]:
    """从 controller_hints.suppress_push_data_insufficient 路由出需人工确认项."""
    hints = _bucket(context, "controller_hints")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in hints.get("suppress_push_data_insufficient", []) or []:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append({
            "symbol": sym,
            "name": item.get("name", ""),
            "origin": item.get("origin", "recent_report_data_blockers"),
            "reason": item.get("reason", "") or (
                "最近报告存在关键数据缺口，作为强结论前需人工复核"
            ),
            "fields": item.get("fields", []) or [],
            "source": item.get("source", "controller_hints"),
            "as_of": as_of,
        })
    return out


def _headline(pre: bool, *, ta_n: int, daily_n: int, manual_n: int,
              extra: str = "") -> str:
    kind = "盘前" if pre else "盘后"
    parts = [f"{ta_n} 项待 TA", f"{daily_n} 项仅日报", f"{manual_n} 项需人工确认"]
    head = f"{kind} briefing：" + "，".join(parts)
    if extra:
        head += f"；{extra}"
    return head


# ──────────────────────────────────────────────────────────────────────────────
# 盘前 briefing
# ──────────────────────────────────────────────────────────────────────────────

def build_pre_market_briefing(
    context: dict[str, Any], *, as_of: str | None = None
) -> dict[str, Any]:
    """构建盘前 briefing（流程提示 + 调度建议）.

    输入：IC-TA-001 / IC-TA-002 的 ``get_investment_controller_context`` 返回 dict
    （或等价 fixture）。输出稳定 briefing dict，包含：

    - ``ta_to_schedule``：待调度 TA，每条带 ``reason_call_ta``。
    - ``daily_report_only``：只进日报项，每条带 ``reason_skip_ta``。
    - ``needs_manual_confirmation``：数据缺口需人工复核项。
    - ``mandate_digest`` / ``data_health_note``：盘前背景摘要。
    - ``forbidden_word_scan``：禁用词自检结果。
    """
    ts = _safe_now(as_of)
    ta_list = _route_ta_to_schedule(context, ts)
    daily_list = _route_daily_report_only(context, ts)
    manual_list = _route_needs_manual_confirmation(context, ts)

    mandate = _bucket(context, "mandate_daily_report")
    health = _bucket(context, "data_health")

    mandate_digest = _build_mandate_digest(mandate)
    data_health_note = _build_data_health_note(health, pre_market=True)

    headline = _headline(
        pre=True,
        ta_n=len(ta_list),
        daily_n=len(daily_list),
        manual_n=len(manual_list),
        extra=_mandate_headline_extra(mandate_digest),
    )

    payload: dict[str, Any] = {
        "schema_version": BRIEFING_SCHEMA_VERSION,
        "briefing_type": BRIEFING_PRE_MARKET,
        "as_of": ts,
        "generated_by": BRIEFING_SOURCE,
        "read_only": True,
        "headline": headline,
        "previous_trade_date": context.get("previous_trade_date", ""),
        "is_trading_day": context.get("is_trading_day"),
        "ta_to_schedule": ta_list,
        "daily_report_only": daily_list,
        "needs_manual_confirmation": manual_list,
        "mandate_digest": mandate_digest,
        "data_health_note": data_health_note,
        "notes": _build_notes(context, ta_list, daily_list, manual_list, pre_market=True),
    }
    payload["forbidden_word_scan"] = scan_forbidden_words(payload)
    return payload


# ──────────────────────────────────────────────────────────────────────────────
# 盘后 briefing
# ──────────────────────────────────────────────────────────────────────────────

def build_post_market_briefing(
    context: dict[str, Any], *, as_of: str | None = None
) -> dict[str, Any]:
    """构建盘后 briefing（今日表现 + 数据缺口 + 次日 TA 候选）.

    输入同盘前。盘后额外回答：
    - 今天持仓表现如何（涨/跌/无行情计数）。
    - 最近报告有哪些数据缺口（影响次日是否值得再调 TA）。
    - 明天哪些标的进入 TA 队列 / 仅日报 / 需人工确认。
    """
    ts = _safe_now(as_of)
    ta_list = _route_ta_to_schedule(context, ts)
    daily_list = _route_daily_report_only(context, ts)
    manual_list = _route_needs_manual_confirmation(context, ts)

    today_perf = _build_today_performance(_bucket(context, "holdings"))
    report_gaps = _build_report_gaps_summary(_bucket(context, "recent_report_data_blockers"))

    headline = _headline(
        pre=False,
        ta_n=len(ta_list),
        daily_n=len(daily_list),
        manual_n=len(manual_list),
        extra=_post_headline_extra(today_perf, report_gaps),
    )

    payload: dict[str, Any] = {
        "schema_version": BRIEFING_SCHEMA_VERSION,
        "briefing_type": BRIEFING_POST_MARKET,
        "as_of": ts,
        "generated_by": BRIEFING_SOURCE,
        "read_only": True,
        "headline": headline,
        "previous_trade_date": context.get("previous_trade_date", ""),
        "is_trading_day": context.get("is_trading_day"),
        "today_performance": today_perf,
        "report_data_gaps": report_gaps,
        # 次日 TA 候选复用同一套路由（why call TA）。
        "tomorrow_ta_candidates": ta_list,
        "daily_report_only": daily_list,
        "needs_manual_confirmation": manual_list,
        "notes": _build_notes(context, ta_list, daily_list, manual_list, pre_market=False),
    }
    payload["forbidden_word_scan"] = scan_forbidden_words(payload)
    return payload


# ──────────────────────────────────────────────────────────────────────────────
# 摘要 helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_mandate_digest(mandate: dict[str, Any]) -> dict[str, Any]:
    """昊天日报摘要（盘前用，只读事实）。"""
    return {
        "data_status": mandate.get("data_status", "missing"),
        "report_as_of": mandate.get("report_as_of", ""),
        "rising_topic_count": mandate.get("rising_topic_count", 0),
        "cooling_topic_count": mandate.get("cooling_topic_count", 0),
        "main_candidate_count": mandate.get("main_candidate_count", 0),
        "observation_candidate_count": mandate.get("observation_candidate_count", 0),
        "evidence_gap_count": mandate.get("evidence_gap_count", 0),
        "top_rising_topics": [
            t.get("topic", "") for t in (mandate.get("rising_topics") or [])[:3]
            if isinstance(t, dict) and t.get("topic")
        ],
    }


def _mandate_headline_extra(digest: dict[str, Any]) -> str:
    rising = digest.get("rising_topic_count", 0)
    main = digest.get("main_candidate_count", 0)
    if rising or main:
        return f"昊天升温主题 {rising} 个，主候选 {main} 只"
    return ""


def _build_data_health_note(health: dict[str, Any], *, pre_market: bool) -> str:
    db_ok = bool(health.get("tradeflow_db_available", False))
    plan = health.get("latest_plan_date") or ""
    eff = health.get("latest_effective_trade_date") or ""
    status = health.get("data_status", "missing")
    when = "盘前" if pre_market else "盘后"
    if not db_ok:
        return f"{when} briefing：TradeFlow DB 不可用（data_status={status}）"
    if not plan:
        return f"{when} briefing：TradeFlow DB 可用，暂无候选池记录"
    eff_part = f"，生效交易日 {eff}" if eff and eff != plan else ""
    return f"{when} briefing：TradeFlow DB 可用，最新候选池 {plan}{eff_part}（data_status={status}）"


def _build_today_performance(holdings: dict[str, Any]) -> dict[str, Any]:
    """今日持仓表现（盘后用）。基于 holdings bucket 的 live_price / floating_pnl_pct。"""
    items = _items(holdings)
    gain = loss = flat = no_quote = 0
    for it in items:
        price = it.get("live_price")
        pnl_pct = it.get("floating_pnl_pct")
        if price is None:
            no_quote += 1
            continue
        if pnl_pct is None:
            flat += 1
        elif pnl_pct > 0:
            gain += 1
        elif pnl_pct < 0:
            loss += 1
        else:
            flat += 1
    return {
        "holdings_count": len(items),
        "gain_count": gain,
        "loss_count": loss,
        "flat_count": flat,
        "no_quote_count": no_quote,
        "data_status": holdings.get("data_status", "missing"),
        "note": _perf_note(gain, loss, flat, no_quote, len(items)),
    }


def _perf_note(gain: int, loss: int, flat: int, no_quote: int, total: int) -> str:
    if total == 0:
        return "无持仓，盘后无需复盘持仓表现"
    parts = []
    if gain:
        parts.append(f"{gain} 只上涨")
    if loss:
        parts.append(f"{loss} 只下跌")
    if flat:
        parts.append(f"{flat} 只持平")
    if no_quote:
        parts.append(f"{no_quote} 只无行情")
    if not parts:
        return "持仓数据完整，无异常表现"
    return "今日持仓表现：" + "，".join(parts)


def _build_report_gaps_summary(blockers: dict[str, Any]) -> dict[str, Any]:
    """报告数据缺口摘要（盘后用）。"""
    return {
        "data_status": blockers.get("data_status", "missing"),
        "scanned_report_count": blockers.get("scanned_report_count", 0),
        "affected_report_count": blockers.get("affected_report_count", 0),
        "total_severe_blockers": blockers.get("total_severe_blockers", 0),
        "summary_level": blockers.get("summary_level", "ok"),
        "field_counts": blockers.get("field_counts", {}) or {},
    }


def _post_headline_extra(today_perf: dict[str, Any], gaps: dict[str, Any]) -> str:
    extra_parts: list[str] = []
    total = today_perf.get("holdings_count", 0)
    if total:
        extra_parts.append(
            f"持仓 {total} 只"
            f"（{today_perf.get('gain_count', 0)} 涨 {today_perf.get('loss_count', 0)} 跌）"
        )
    severe = gaps.get("total_severe_blockers", 0)
    if severe:
        extra_parts.append(f"报告缺口 {severe} 项")
    return "；".join(extra_parts)


def _build_notes(
    context: dict[str, Any],
    ta_list: list[dict[str, Any]],
    daily_list: list[dict[str, Any]],
    manual_list: list[dict[str, Any]],
    *,
    pre_market: bool,
) -> list[str]:
    notes: list[str] = []
    kind = "盘前" if pre_market else "盘后"
    if not ta_list and not daily_list and not manual_list:
        notes.append(f"{kind} briefing：暂无待调度项，investment-controller 本轮可保持轻量")
    if not ta_list:
        notes.append(f"{kind} briefing：无标的进入 TA 队列，避免无谓调用模型")
    # 数据缺口提示
    blockers = _bucket(context, "recent_report_data_blockers")
    if blockers.get("affected_report_count", 0):
        notes.append(
            f"{kind} briefing：{blockers.get('affected_report_count')} 份报告存在"
            f"关键数据缺口，相关标的需人工复核后再决定是否升级 TA"
        )
    return notes


# ──────────────────────────────────────────────────────────────────────────────
# dry-run fixture 入口
# ──────────────────────────────────────────────────────────────────────────────

def dry_run_all_fixtures(*, as_of: str | None = None) -> dict[str, Any]:
    """跑两条内置 fixture，返回 dry-run 汇总（不写文件、不联网）.

    供测试 / investment-controller 联调 / 文档生成使用。返回结构::

        {
            "pre_market": <pre_market briefing>,
            "post_market": <post_market briefing>,
            "summary": {
                "forbidden_word_scan_passed": bool,
                "pre_ta_count": int,
                "post_ta_count": int,
                ...
            },
        }
    """
    pre = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT, as_of=as_of)
    post = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT, as_of=as_of)
    pre_scan = pre.get("forbidden_word_scan", {})
    post_scan = post.get("forbidden_word_scan", {})
    return {
        "pre_market": pre,
        "post_market": post,
        "summary": {
            "forbidden_word_scan_passed": (
                not pre_scan.get("found", False) and not post_scan.get("found", False)
            ),
            "pre_ta_count": len(pre.get("ta_to_schedule", [])),
            "pre_daily_count": len(pre.get("daily_report_only", [])),
            "pre_manual_count": len(pre.get("needs_manual_confirmation", [])),
            "post_ta_count": len(post.get("tomorrow_ta_candidates", [])),
            "post_daily_count": len(post.get("daily_report_only", [])),
            "post_manual_count": len(post.get("needs_manual_confirmation", [])),
            "schema_version": BRIEFING_SCHEMA_VERSION,
            "generated_by": BRIEFING_SOURCE,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# 内置 dry-run fixture（不代表真实行情，仅供联调 / 测试 / 文档）
# ──────────────────────────────────────────────────────────────────────────────
# 形状与 api.services.investment_controller_context.get_investment_controller_context
# 返回值保持一致：每个 bucket 带 source / as_of / data_status / items。

_FIXTURE_AS_OF = "2026-06-26 09:10:00"
_FIXTURE_PREV_TRADE = "2026-06-25"
_FIXTURE_TF_DATE = "2026-06-25"


def _empty_ic_bucket(source: str, data_status: str) -> dict[str, Any]:
    return {
        "source": source,
        "as_of": _FIXTURE_AS_OF,
        "data_status": data_status,
        "count": 0,
        "items": [],
    }


PRE_MARKET_FIXTURE_CONTEXT: dict[str, Any] = {
    "schema_version": "1.0",
    "as_of": _FIXTURE_AS_OF,
    "previous_trade_date": _FIXTURE_PREV_TRADE,
    "is_trading_day": True,
    "generated_by": "investment_controller_context",
    "read_only": True,
    # Bucket 1: holdings — 一只持仓（有报告）
    "holdings": {
        "source": "imported_portfolio",
        "as_of": _FIXTURE_AS_OF,
        "data_status": "skipped",
        "count": 1,
        "items": [
            {
                "symbol": "600584.SH",
                "name": "华天科技",
                "current_position": 200.0,
                "average_cost": 11.20,
                "live_price": None,
                "floating_pnl": None,
                "floating_pnl_pct": None,
                "latest_report": {
                    "report_id": "r-600584",
                    "decision": "HOLD",
                    "action_label": "持有",
                    "research_direction": "中性",
                    "trade_date": _FIXTURE_PREV_TRADE,
                },
                "source": "imported_portfolio",
                "as_of": _FIXTURE_AS_OF,
            }
        ],
    },
    # Bucket 2: observation warehouse — 一只接近买点 + 一只被动观察
    "observation_warehouse": {
        "source": "observation_warehouse",
        "as_of": _FIXTURE_AS_OF,
        "data_status": "fresh",
        "count": 2,
        "summary": {"by_status": {"ta_required": 1, "watching": 1}},
        "items": [
            {
                "symbol": "601689.SH",
                "name": "拓普集团",
                "status": "ta_required",
                "horizon": "mid",
                "entry_low": 29.0,
                "entry_high": 30.5,
                "trigger_price": 30.0,
                "invalid_price": 28.0,
                "reason": "政策利好接近买点，需 TA 确认入场区间",
                "source": "observation_warehouse",
                "as_of": _FIXTURE_AS_OF,
            },
            {
                "symbol": "002353.SZ",
                "name": "杰瑞股份",
                "status": "watching",
                "horizon": "mid",
                "entry_low": 34.0,
                "entry_high": 35.5,
                "trigger_price": 35.0,
                "invalid_price": 33.0,
                "reason": "能源主题观察，价格未到触发区",
                "source": "observation_warehouse",
                "as_of": _FIXTURE_AS_OF,
            },
        ],
    },
    # Bucket 3: tradeflow candidates — 一只政策左侧候选（need_deep_ta）
    "tradeflow_candidates": {
        "source": "tradeflow_candidates",
        "as_of": _FIXTURE_AS_OF,
        "trade_date": _FIXTURE_TF_DATE,
        "data_status": "fresh",
        "count": 1,
        "pool_counts": {"POLICY_AMBUSH": 1},
        "summary_agg": {},
        "items": [
            {
                "symbol": "688256.SH",
                "name": "寒武纪",
                "candidate_type": "POLICY_AMBUSH",
                "tier": "A",
                "composite_score": 68.0,
                "primary_strategy": "POLICY_AMBUSH",
                "strategy_tags": ["半导体自主可控"],
                "trigger_price": 240.0,
                "support_price": 225.0,
                "invalid_price": 215.0,
                "need_deep_ta": True,
                "deep_ta_status": "pending",
                "action_tier": "P1",
                "reason": "半导体政策升温，左侧埋伏候选",
                "source": "tradeflow_candidates",
                "as_of": _FIXTURE_AS_OF,
            }
        ],
    },
    # Bucket 4: latest TA reports
    "latest_ta_reports": {
        "source": "ta_report",
        "as_of": _FIXTURE_AS_OF,
        "data_status": "fresh",
        "count": 1,
        "items": [
            {
                "symbol": "600584.SH",
                "decision": "HOLD",
                "action_label": "持有",
                "research_direction": "中性",
                "source": "ta_report",
                "as_of": _FIXTURE_AS_OF,
            }
        ],
    },
    # Bucket 5: data health
    "data_health": {
        "source": "tradeflow_data_health",
        "as_of": _FIXTURE_AS_OF,
        "data_status": "fresh",
        "tradeflow_db_available": True,
        "latest_plan_date": _FIXTURE_TF_DATE,
        "latest_candidates_date": _FIXTURE_TF_DATE,
        "latest_effective_trade_date": _FIXTURE_TF_DATE,
        "latest_observe_date": _FIXTURE_TF_DATE,
        "total_candidates_today": 1,
        "total_signals_today": 0,
        "sources": [],
    },
    # Bucket 6: pending TA required — 观察仓 ta_required + 候选 need_deep_ta
    "pending_ta_required": {
        "source": "pending_ta_required",
        "as_of": _FIXTURE_AS_OF,
        "data_status": "fresh",
        "count": 2,
        "items": [
            {
                "symbol": "601689.SH",
                "name": "拓普集团",
                "horizon": "mid",
                "reason": "政策利好接近买点，需 TA 确认入场区间",
                "origin": "observation_warehouse",
                "source": "observation_warehouse",
                "as_of": _FIXTURE_AS_OF,
            },
            {
                "symbol": "688256.SH",
                "name": "寒武纪",
                "candidate_type": "POLICY_AMBUSH",
                "composite_score": 68.0,
                "deep_ta_status": "pending",
                "reason": "半导体政策升温，左侧埋伏候选",
                "origin": "tradeflow_candidates",
                "source": "tradeflow_candidates",
                "as_of": _FIXTURE_AS_OF,
            },
        ],
    },
    # Bucket 7: mandate daily report
    "mandate_daily_report": {
        "source": "mandate_daily_report",
        "as_of": _FIXTURE_AS_OF,
        "report_as_of": _FIXTURE_TF_DATE,
        "data_status": "fresh",
        "rising_topic_count": 2,
        "cooling_topic_count": 1,
        "main_candidate_count": 1,
        "observation_candidate_count": 1,
        "evidence_gap_count": 0,
        "rising_topics": [
            {"topic": "半导体自主可控", "status_label": "升温",
             "heat_trend_label": "上行", "candidate_count": 1},
            {"topic": "汽车零部件", "status_label": "升温",
             "heat_trend_label": "走平", "candidate_count": 1},
        ],
        "cooling_topics": [
            {"topic": "房地产", "status_label": "降温", "heat_trend_label": "下行"},
        ],
        "main_candidates": [
            {"symbol": "688256.SH", "name": "寒武纪", "topic": "半导体自主可控",
             "candidate_type": "POLICY_AMBUSH", "mandate_score": 68.0,
             "entry_reason": "政策升温左侧"},
        ],
        "observation_candidates": [
            {"symbol": "002353.SZ", "name": "杰瑞股份", "topic": "能源",
             "mandate_score": 52.0},
        ],
        "evidence_gaps": [],
    },
    # Bucket 8: recent report data blockers — 干净
    "recent_report_data_blockers": {
        "source": "recent_report_data_blockers",
        "as_of": _FIXTURE_AS_OF,
        "data_status": "fresh",
        "scanned_report_count": 1,
        "affected_report_count": 0,
        "total_severe_blockers": 0,
        "summary_level": "ok",
        "field_counts": {},
        "affected_symbols": [],
    },
    # controller_hints（与 IC-TA-002 形状一致）
    "controller_hints": {
        "source": "controller_hints",
        "as_of": _FIXTURE_AS_OF,
        "data_status": "fresh",
        "needs_ta": [
            {
                "symbol": "601689.SH",
                "name": "拓普集团",
                "origin": "observation_warehouse",
                "reason": "政策利好接近买点，需 TA 确认入场区间",
                "suggested_next_step": "consider_light_or_full_ta",
                "source": "pending_ta_required",
                "as_of": _FIXTURE_AS_OF,
            },
            {
                "symbol": "688256.SH",
                "name": "寒武纪",
                "origin": "tradeflow_candidates",
                "reason": "半导体政策升温，左侧埋伏候选",
                "suggested_next_step": "consider_light_or_full_ta",
                "source": "pending_ta_required",
                "as_of": _FIXTURE_AS_OF,
            },
        ],
        "daily_report_only": [
            {
                "symbol": "002353.SZ",
                "name": "杰瑞股份",
                "origin": "observation_warehouse",
                "reason": "被动观察，daily digest only",
                "suggested_next_step": "daily_report_only",
                "source": "observation_warehouse",
                "as_of": _FIXTURE_AS_OF,
            },
        ],
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


# 盘后 fixture：持仓有表现 + 报告数据缺口 + 次日 TA 候选
POST_MARKET_FIXTURE_CONTEXT: dict[str, Any] = {
    "schema_version": "1.0",
    "as_of": "2026-06-26 15:30:00",
    "previous_trade_date": _FIXTURE_TF_DATE,
    "is_trading_day": True,
    "generated_by": "investment_controller_context",
    "read_only": True,
    # Bucket 1: holdings — 两只持仓（1 涨 1 跌）
    "holdings": {
        "source": "imported_portfolio",
        "as_of": "2026-06-26 15:30:00",
        "data_status": "fresh",
        "count": 2,
        "items": [
            {
                "symbol": "600584.SH",
                "name": "华天科技",
                "current_position": 200.0,
                "average_cost": 11.20,
                "live_price": 11.58,
                "floating_pnl": 76.0,
                "floating_pnl_pct": 3.39,
                "latest_report": {
                    "report_id": "r-600584",
                    "decision": "HOLD",
                    "action_label": "持有",
                    "research_direction": "中性",
                    "trade_date": _FIXTURE_TF_DATE,
                },
                "source": "imported_portfolio",
                "as_of": "2026-06-26 15:30:00",
            },
            {
                "symbol": "603629.SH",
                "name": "苏利股份",
                "current_position": 100.0,
                "average_cost": 14.80,
                "live_price": 14.21,
                "floating_pnl": -59.0,
                "floating_pnl_pct": -3.99,
                "latest_report": {
                    "report_id": "r-603629",
                    "decision": "数据不足观察",
                    "action_label": "数据不足观察",
                    "research_direction": "偏空",
                    "trade_date": _FIXTURE_TF_DATE,
                },
                "source": "imported_portfolio",
                "as_of": "2026-06-26 15:30:00",
            },
        ],
    },
    # Bucket 2: observation — 一只失效 + 一只仍观察
    "observation_warehouse": {
        "source": "observation_warehouse",
        "as_of": "2026-06-26 15:30:00",
        "data_status": "fresh",
        "count": 2,
        "summary": {"by_status": {"watching": 1, "invalidated": 1}},
        "items": [
            {
                "symbol": "002353.SZ",
                "name": "杰瑞股份",
                "status": "watching",
                "horizon": "mid",
                "entry_low": 34.0,
                "entry_high": 35.5,
                "trigger_price": 35.0,
                "invalid_price": 33.0,
                "reason": "能源主题观察，价格未到触发区",
                "source": "observation_warehouse",
                "as_of": "2026-06-26 15:30:00",
            },
        ],
    },
    # Bucket 3: tradeflow candidates
    "tradeflow_candidates": {
        "source": "tradeflow_candidates",
        "as_of": "2026-06-26 15:30:00",
        "trade_date": _FIXTURE_TF_DATE,
        "data_status": "fresh",
        "count": 1,
        "pool_counts": {"POLICY_AMBUSH": 1},
        "summary_agg": {},
        "items": [
            {
                "symbol": "688256.SH",
                "name": "寒武纪",
                "candidate_type": "POLICY_AMBUSH",
                "tier": "A",
                "composite_score": 68.0,
                "primary_strategy": "POLICY_AMBUSH",
                "strategy_tags": ["半导体自主可控"],
                "trigger_price": 240.0,
                "support_price": 225.0,
                "invalid_price": 215.0,
                "need_deep_ta": True,
                "deep_ta_status": "pending",
                "action_tier": "P1",
                "reason": "半导体政策升温，左侧埋伏候选",
                "source": "tradeflow_candidates",
                "as_of": "2026-06-26 15:30:00",
            }
        ],
    },
    # Bucket 4: latest TA reports
    "latest_ta_reports": {
        "source": "ta_report",
        "as_of": "2026-06-26 15:30:00",
        "data_status": "fresh",
        "count": 2,
        "items": [
            {
                "symbol": "600584.SH",
                "decision": "HOLD",
                "action_label": "持有",
                "research_direction": "中性",
                "source": "ta_report",
                "as_of": "2026-06-26 15:30:00",
            },
            {
                "symbol": "603629.SH",
                "decision": "数据不足观察",
                "action_label": "数据不足观察",
                "research_direction": "偏空",
                "source": "ta_report",
                "as_of": "2026-06-26 15:30:00",
            },
        ],
    },
    # Bucket 5: data health
    "data_health": {
        "source": "tradeflow_data_health",
        "as_of": "2026-06-26 15:30:00",
        "data_status": "fresh",
        "tradeflow_db_available": True,
        "latest_plan_date": _FIXTURE_TF_DATE,
        "latest_candidates_date": _FIXTURE_TF_DATE,
        "latest_effective_trade_date": _FIXTURE_TF_DATE,
        "latest_observe_date": _FIXTURE_TF_DATE,
        "total_candidates_today": 1,
        "total_signals_today": 1,
        "sources": [],
    },
    # Bucket 6: pending TA required — 候选 need_deep_ta
    "pending_ta_required": {
        "source": "pending_ta_required",
        "as_of": "2026-06-26 15:30:00",
        "data_status": "fresh",
        "count": 1,
        "items": [
            {
                "symbol": "688256.SH",
                "name": "寒武纪",
                "candidate_type": "POLICY_AMBUSH",
                "composite_score": 68.0,
                "deep_ta_status": "pending",
                "reason": "半导体政策升温，左侧埋伏候选",
                "origin": "tradeflow_candidates",
                "source": "tradeflow_candidates",
                "as_of": "2026-06-26 15:30:00",
            },
        ],
    },
    # Bucket 7: mandate daily report（盘后复用同一份）
    "mandate_daily_report": {
        "source": "mandate_daily_report",
        "as_of": "2026-06-26 15:30:00",
        "report_as_of": _FIXTURE_TF_DATE,
        "data_status": "fresh",
        "rising_topic_count": 2,
        "cooling_topic_count": 1,
        "main_candidate_count": 1,
        "observation_candidate_count": 1,
        "evidence_gap_count": 0,
        "rising_topics": [
            {"topic": "半导体自主可控", "status_label": "升温",
             "heat_trend_label": "上行", "candidate_count": 1},
        ],
        "cooling_topics": [],
        "main_candidates": [
            {"symbol": "688256.SH", "name": "寒武纪", "topic": "半导体自主可控",
             "candidate_type": "POLICY_AMBUSH", "mandate_score": 68.0,
             "entry_reason": "政策升温左侧"},
        ],
        "observation_candidates": [],
        "evidence_gaps": [],
    },
    # Bucket 8: recent report data blockers — 603629 有缺口
    "recent_report_data_blockers": {
        "source": "recent_report_data_blockers",
        "as_of": "2026-06-26 15:30:00",
        "data_status": "fresh",
        "scanned_report_count": 2,
        "affected_report_count": 1,
        "total_severe_blockers": 2,
        "summary_level": "warning",
        "field_counts": {"individual_fund_flow": 1, "announcements": 1},
        "affected_symbols": [
            {
                "symbol": "603629.SH",
                "report_id": "r-603629",
                "trade_date": _FIXTURE_TF_DATE,
                "severe_count": 2,
                "fields": ["individual_fund_flow", "announcements"],
                "action_label": "数据不足观察",
                "research_direction": "偏空",
            }
        ],
    },
    # controller_hints
    "controller_hints": {
        "source": "controller_hints",
        "as_of": "2026-06-26 15:30:00",
        "data_status": "fresh",
        "needs_ta": [
            {
                "symbol": "688256.SH",
                "name": "寒武纪",
                "origin": "tradeflow_candidates",
                "reason": "半导体政策升温，左侧埋伏候选",
                "suggested_next_step": "consider_light_or_full_ta",
                "source": "pending_ta_required",
                "as_of": "2026-06-26 15:30:00",
            },
        ],
        "daily_report_only": [
            {
                "symbol": "002353.SZ",
                "name": "杰瑞股份",
                "origin": "observation_warehouse",
                "reason": "被动观察，daily digest only",
                "suggested_next_step": "daily_report_only",
                "source": "observation_warehouse",
                "as_of": "2026-06-26 15:30:00",
            },
        ],
        "suppress_push_data_insufficient": [
            {
                "symbol": "603629.SH",
                "name": "苏利股份",
                "origin": "recent_report_data_blockers",
                "reason": "最近报告存在 2 项关键数据缺口（individual_fund_flow, announcements），不作为强结论推送",
                "fields": ["individual_fund_flow", "announcements"],
                "suggested_next_step": "suppress_push_data_insufficient",
                "source": "recent_report_data_blockers",
                "as_of": "2026-06-26 15:30:00",
            },
        ],
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
    "ALLOWED_BRIEFING_TYPES",
    "BRIEFING_POST_MARKET",
    "BRIEFING_PRE_MARKET",
    "BRIEFING_SCHEMA_VERSION",
    "BRIEFING_SOURCE",
    "FORBIDDEN_STRONG_WORDS",
    "POST_MARKET_FIXTURE_CONTEXT",
    "PRE_MARKET_FIXTURE_CONTEXT",
    "PROFILE_FULL_TA",
    "PROFILE_MIDLINE_POLICY_LIGHT",
    "PROFILE_POSITION_RISK_LIGHT",
    "PROFILE_SHORT_TECH_LIGHT",
    "build_post_market_briefing",
    "build_pre_market_briefing",
    "dry_run_all_fixtures",
    "scan_forbidden_words",
]


def _self_check() -> None:
    """模块导入时自检 fixture 可序列化、可构建、无禁用词（仅 debug 用）."""
    report = dry_run_all_fixtures()
    assert report["summary"]["forbidden_word_scan_passed"], "fixture 含禁用词"
    json.dumps(report)


# 调试期可手动执行：``python -c "from tradingagents.tradeflow.controller_briefing import _self_check; _self_check()"``
