# [TRACK-005] post_market_tracking_review
"""盘后复盘摘要与次日计划写回跟踪看板 — 规则版.

为持仓 + 观察仓 + TradeFlow 候选池生成结构化的盘后复盘摘要，回答三个问题：
1. 今天是否触发计划？（持仓破位 / 观察仓进入买点 / 候选触发）
2. 明天是否继续看？（tomorrow_focus 聚合清单）
3. 是否需要 TA？（needs_re_ta / review_ta 标记）

设计原则
--------
1. **纯函数**：不访问数据库、不联网、不调用 LLM、不写状态。输入是已被
   ``tracking_board_service`` 拉取并组装好的 holdings / observation_items /
   tradeflow_review dict，输出一个 ``review_summary`` dict，便于单测与回放。
2. **安全红线**：
   - 永不输出 "立即买入 / 重仓 / 清仓 / 满仓 / 梭哈" 等强动作词；本引擎只
     描述事实状态并给出"软建议"（继续观察 / 复核 TA / 关注关键位）。
   - 数据缺失时只能标记 ``data_missing`` / ``needs_review``，绝不把缺数据
     的标的误判为可入场（硬约束，与 TRACK-004 状态引擎一致）。
3. **非交易日友好**：非交易日仍生成复盘，但 ``data_status=NON_TRADING_DAY``
   并明确提示"非交易日生成的计划在下一交易日复盘时生效"。
4. **空数据可解释**：当持仓/观察仓/候选池全部为空时，返回
   ``data_status=NO_DATA`` 并给出"为何无数据"的解释文案，不返回 None。
5. **可追溯**：每个复盘条目都带 ``reason`` 与使用的 ``data_fields``，每个
   tomorrow_focus 条目都带 ``source`` 与 ``as_of``。

依赖说明
--------
- 复用 ``observation_state_engine.evaluate_observation_state`` 计算观察仓派生
  状态，避免重复实现价格区间/失效价逻辑（TRACK-004 已稳定）。
- 复用 ``observation_state_engine.FORBIDDEN_STRONG_WORDS`` 做输出安全校验。
- 不导入 tradeflow_service 或 tracking_board_service，避免循环依赖。
"""

from __future__ import annotations

from typing import Any

from .observation_state_engine import (
    STATE_INVALIDATED,
    STATE_IN_ENTRY_ZONE,
    STATE_MISSED_ENTRY,
    STATE_NEAR_ENTRY,
    STATE_TA_REQUIRED,
    FORBIDDEN_STRONG_WORDS,
    evaluate_observation_state,
)

# [TRACK-005] post_market_tracking_review
# ── data_status 枚举（字符串常量） ──────────────────────────────────────
DATA_STATUS_OK = "OK"
DATA_STATUS_NON_TRADING_DAY = "NON_TRADING_DAY"
DATA_STATUS_NO_DATA = "NO_DATA"
DATA_STATUS_PARTIAL_DATA = "PARTIAL_DATA"

_DATA_STATUS_MESSAGES = {
    DATA_STATUS_OK: "盘后复盘已生成",
    DATA_STATUS_NON_TRADING_DAY: "非交易日，无行情更新；计划在下一交易日复盘时生效",
    DATA_STATUS_NO_DATA: "暂无持仓、观察仓与候选池数据，复盘为空",
    DATA_STATUS_PARTIAL_DATA: "部分数据缺失（行情或候选池未补齐），复盘基于已有数据",
}

# ── 派生标签（用于 review 条目的 tomorrow_focus_tag 字段） ─────────────
# 软建议标签，绝不包含强买卖词。
TAG_CONTINUE_MONITORING = "continue_monitoring"      # 持仓：正常监控
TAG_WATCH_KEY_LEVEL = "watch_key_level"              # 持仓：关注关键位
TAG_REVIEW_TA = "review_ta"                          # 持仓/观察仓：需要 TA 复核
TAG_NEEDS_ATTENTION = "needs_attention"              # 持仓：已破位/大跌，需关注
TAG_KEEP_WATCHING = "keep_watching"                  # 观察仓：继续观察
TAG_WAIT_TRIGGER = "wait_trigger"                    # 观察仓：等待触发信号
TAG_MARK_INVALIDATED = "mark_invalidated"            # 观察仓：建议标记失效
TAG_RERUN_TA = "rerun_ta"                            # 观察仓：建议重新 TA
TAG_CANDIDATE_TRIGGERED = "candidate_triggered"      # 候选池：已触发
TAG_CANDIDATE_ELIMINATED = "candidate_eliminated"    # 候选池：已淘汰
TAG_CANDIDATE_QUEUED = "candidate_queued"            # 候选池：仍在队列

# ── 阈值 ────────────────────────────────────────────────────────────────
HOLDINGS_LARGE_DROP_PCT = -3.0     # 持仓日跌幅 <= -3% 视为大跌
HOLDINGS_MODERATE_DROP_PCT = -1.5  # 持仓日跌幅 <= -1.5% 视为温和下跌
HOLDINGS_DEEP_LOSS_PCT = -10.0     # 浮盈亏 <= -10% 视为深套（偏离 TA 计划）
KEY_LEVEL_PROXIMITY_PCT = 0.03     # 现价距关键位 3% 以内视为"接近关键位"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        return v
    except (TypeError, ValueError):
        return None


def _contains_forbidden_word(text: str) -> str | None:
    """Return the forbidden word if found, else None."""
    if not text:
        return None
    for word in FORBIDDEN_STRONG_WORDS:
        if word in text:
            return word
    return None


def _data_status_message(status: str) -> str:
    return _DATA_STATUS_MESSAGES.get(status, status)


# [TRACK-005] post_market_tracking_review
def build_post_market_tracking_review(
    *,
    holdings: list[dict[str, Any]],
    observation_items: list[dict[str, Any]],
    tradeflow_review: dict[str, Any] | None = None,
    is_trading_day: bool = True,
    review_date: str = "",
    as_of: str = "",
) -> dict[str, Any]:
    """构建盘后复盘摘要.

    Args:
        holdings: tracking_board v2 的 holdings 列表（每项含 ``symbol/name/
            average_cost/live_price/price_change_pct/floating_pnl_pct/analysis``）。
            ``analysis`` 为 None 或 dict（含 ``low_price/high_price/action_label``）。
        observation_items: 已被 ``_enrich_observation_items`` 注入 ``live_price``
            的观察仓条目（含 ``symbol/name/status/entry_low/entry_high/
            trigger_price/invalid_price``）。
        tradeflow_review: ``tradeflow_service.get_review()`` 的返回值；为 None 或
            ``status="no_data"`` 表示候选池复盘缺失。
        is_trading_day: 当前是否为 A 股交易日。仅影响 ``data_status`` 文案与
            观察仓状态引擎的"无行情"分类（交易日 → data_missing / 非交易日 →
            needs_review）。
        review_date: 复盘日期（YYYY-MM-DD），通常为 ``previous_trade_date``。
        as_of: 复盘生成时间戳字符串。

    Returns:
        复盘 dict，结构见模块 docstring。永远返回非 None dict。
        所有文本字段均不含 ``FORBIDDEN_STRONG_WORDS``。
    """
    # ── 1. 候选池复盘（可空）──────────────────────────────────────────
    candidate_review, tf_has_data, tf_status = _build_candidate_pool_review(
        tradeflow_review, observation_items
    )

    # ── 2. 持仓复盘 ───────────────────────────────────────────────────
    holdings_review = _build_holdings_review(holdings)

    # ── 3. 观察仓复盘 ─────────────────────────────────────────────────
    observation_review = _build_observation_review(
        observation_items, is_trading_day=is_trading_day
    )

    # ── 4. 次日关注聚合 ───────────────────────────────────────────────
    tomorrow_focus = _aggregate_tomorrow_focus(
        holdings_review, observation_review, candidate_review, as_of=as_of
    )

    # ── 5. data_status 判定 ───────────────────────────────────────────
    has_holdings = len(holdings) > 0
    has_observation = len(observation_items) > 0
    has_candidates = len(candidate_review) > 0

    if not is_trading_day:
        data_status = DATA_STATUS_NON_TRADING_DAY
    elif not has_holdings and not has_observation and not has_candidates:
        data_status = DATA_STATUS_NO_DATA
    elif (has_holdings and all(not h.get("live_price") for h in holdings)) or (
        tf_has_data is False and (has_holdings or has_observation)
    ):
        # 交易日但持仓无任何行情，或候选池缺失但仍有持仓/观察仓 → 部分数据
        data_status = DATA_STATUS_PARTIAL_DATA
    else:
        data_status = DATA_STATUS_OK

    # ── 6. 汇总计数 ───────────────────────────────────────────────────
    summary_counts = {
        "holdings_total": len(holdings),
        "holdings_with_analysis": sum(1 for h in holdings_review if h.get("has_analysis")),
        "holdings_risk": sum(1 for h in holdings_review if h.get("needs_attention")),
        "holdings_no_analysis": sum(1 for h in holdings_review if not h.get("has_analysis")),
        "observation_total": len(observation_items),
        "observation_near_entry": sum(1 for o in observation_review if o.get("near_entry")),
        "observation_invalidated": sum(1 for o in observation_review if o.get("invalidated")),
        "observation_needs_re_ta": sum(1 for o in observation_review if o.get("needs_re_ta")),
        "candidates_total": len(candidate_review),
        "candidates_triggered": sum(1 for c in candidate_review if c.get("triggered")),
        "candidates_eliminated": sum(1 for c in candidate_review if c.get("eliminated")),
        "candidates_entered_observation": sum(
            1 for c in candidate_review if c.get("entered_observation")
        ),
    }

    return {
        "review_date": review_date,
        "as_of": as_of,
        "is_trading_day": bool(is_trading_day),
        "data_status": data_status,
        "data_status_message": _data_status_message(data_status),
        "has_tradeflow_review": bool(tf_has_data),
        "tradeflow_review_status": tf_status,
        "holdings_review": holdings_review,
        "observation_review": observation_review,
        "candidate_pool_review": candidate_review,
        "tomorrow_focus": tomorrow_focus,
        "summary_counts": summary_counts,
    }


# [TRACK-005] post_market_tracking_review
def _build_holdings_review(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """持仓复盘：涨跌 / 是否跌破关键位 / 是否偏离 TA 计划."""
    reviews: list[dict[str, Any]] = []
    for h in holdings:
        symbol = h.get("symbol", "")
        name = h.get("name", symbol)
        live_price = _to_float(h.get("live_price"))
        change_pct = _to_float(h.get("price_change_pct"))
        average_cost = _to_float(h.get("average_cost"))
        floating_pnl_pct = _to_float(h.get("floating_pnl_pct"))
        analysis = h.get("analysis") or {}
        has_analysis = bool(analysis)

        key_level = _to_float(analysis.get("low_price")) if has_analysis else None
        action_label = analysis.get("action_label") or analysis.get("decision") or ""

        # 是否跌破关键位（TA 止损/低位）
        broke_key_level = False
        if key_level is not None and key_level > 0 and live_price is not None:
            broke_key_level = live_price <= key_level

        # 是否偏离 TA 计划：大跌 / 破位 / 深套
        large_drop = change_pct is not None and change_pct <= HOLDINGS_LARGE_DROP_PCT
        deep_loss = floating_pnl_pct is not None and floating_pnl_pct <= HOLDINGS_DEEP_LOSS_PCT
        deviated = bool(large_drop or broke_key_level or deep_loss)

        # 接近关键位（未跌破但在 3% 以内）
        near_key_level = False
        if (
            key_level is not None
            and key_level > 0
            and live_price is not None
            and not broke_key_level
        ):
            distance_pct = (live_price - key_level) / key_level
            near_key_level = distance_pct <= KEY_LEVEL_PROXIMITY_PCT

        # 软建议标签
        if not has_analysis:
            tag = TAG_REVIEW_TA
            note = f"持仓 {name} 暂无最新 TA 报告，建议盘后补一次轻量复盘"
        elif broke_key_level or large_drop:
            tag = TAG_NEEDS_ATTENTION
            change_str = f"{change_pct:.2f}%" if change_pct is not None else "未知"
            level_str = f"，已跌破关键位 {key_level:.2f}" if broke_key_level and key_level else ""
            note = f"持仓 {name} 日跌幅 {change_str}{level_str}，需要关注"
        elif near_key_level:
            tag = TAG_WATCH_KEY_LEVEL
            note = f"持仓 {name} 现价接近关键位 {key_level:.2f}，关注是否企稳"
        elif deep_loss:
            tag = TAG_NEEDS_ATTENTION
            note = f"持仓 {name} 浮亏 {floating_pnl_pct:.2f}%，偏离 TA 计划"
        else:
            tag = TAG_CONTINUE_MONITORING
            change_str = f"{change_pct:+.2f}%" if change_pct is not None else "持平"
            note = f"持仓 {name} 日涨跌 {change_str}，未偏离计划"

        # 安全校验：若 reason 误含禁用词则降级为通用文案
        if _contains_forbidden_word(note):
            note = f"持仓 {name} 状态已更新，请查看详情"

        reviews.append({
            "symbol": symbol,
            "name": name,
            "live_price": live_price,
            "daily_change_pct": change_pct,
            "floating_pnl_pct": floating_pnl_pct,
            "average_cost": average_cost,
            "has_analysis": has_analysis,
            "key_level": key_level,
            "broke_key_level": broke_key_level,
            "deviated_from_ta_plan": deviated,
            "needs_attention": bool(broke_key_level or large_drop or deep_loss),
            "action_label": action_label,
            "tomorrow_focus_tag": tag,
            "review_note": note,
        })
    return reviews


# [TRACK-005] post_market_tracking_review
def _build_observation_review(
    observation_items: list[dict[str, Any]],
    *,
    is_trading_day: bool = True,
) -> list[dict[str, Any]]:
    """观察仓复盘：是否接近买点 / 是否失效 / 是否需要重新 TA."""
    reviews: list[dict[str, Any]] = []
    for obs in observation_items:
        symbol = obs.get("symbol", "")
        name = obs.get("name", symbol)
        stored_status = obs.get("status") or "watching"

        # 复用 TRACK-004 状态引擎做派生状态判断（不重复实现价格区间逻辑）
        result = evaluate_observation_state(obs, is_trading_day=is_trading_day)
        state = result.get("state", "watching")
        reason = result.get("reason", "")

        near_entry = state in (STATE_NEAR_ENTRY, STATE_IN_ENTRY_ZONE)
        invalidated = state == STATE_INVALIDATED
        missed_entry = state == STATE_MISSED_ENTRY
        needs_re_ta = (
            stored_status == "ta_required"
            or state == STATE_TA_REQUIRED
            or missed_entry
        )

        # 软建议标签
        if invalidated:
            tag = TAG_MARK_INVALIDATED
        elif near_entry:
            tag = TAG_WAIT_TRIGGER
        elif needs_re_ta:
            tag = TAG_RERUN_TA
        else:
            tag = TAG_KEEP_WATCHING

        # 安全校验
        if _contains_forbidden_word(reason):
            reason = f"观察仓 {symbol} 状态已更新，请查看详情"

        reviews.append({
            "symbol": symbol,
            "name": name,
            "stored_status": stored_status,
            "state": state,
            "priority": result.get("priority", "P3"),
            "near_entry": near_entry,
            "invalidated": invalidated,
            "missed_entry": missed_entry,
            "needs_re_ta": needs_re_ta,
            "live_price": _to_float(obs.get("live_price")),
            "entry_low": _to_float(obs.get("entry_low")),
            "entry_high": _to_float(obs.get("entry_high")),
            "invalid_price": _to_float(obs.get("invalid_price")),
            "trigger_price": _to_float(obs.get("trigger_price")),
            "tomorrow_focus_tag": tag,
            "review_note": reason,
        })
    return reviews


# [TRACK-005] post_market_tracking_review
def _build_candidate_pool_review(
    tradeflow_review: dict[str, Any] | None,
    observation_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, str]:
    """候选池复盘：是否触发 / 是否淘汰 / 是否进入观察仓.

    Returns:
        (reviews, has_data, status_str)
        - has_data: True 表示 tradeflow review 有效；False 表示缺失/空。
        - status_str: "ok" | "no_data" | "skipped"
    """
    if not tradeflow_review:
        return [], False, "skipped"

    if tradeflow_review.get("status") == "no_data":
        return [], False, "no_data"

    results = tradeflow_review.get("results") or []
    if not results:
        return [], False, "no_data"

    obs_symbols = {
        (o.get("symbol") or "").strip() for o in observation_items if o.get("symbol")
    }

    reviews: list[dict[str, Any]] = []
    for entry in results:
        symbol = entry.get("symbol", "")
        name = entry.get("name", symbol)
        observe_state = entry.get("observe_state", "WAITING") or "WAITING"
        plan_action = entry.get("plan_action", "OBSERVE") or "OBSERVE"

        triggered = observe_state == "TRIGGERED"
        eliminated = (
            plan_action == "REMOVE_FROM_WATCH"
            or observe_state in ("INVALIDATED", "EXPIRED")
        )
        entered_observation = bool(symbol) and symbol in obs_symbols

        # 透传 TF-REVIEW-003 的 tomorrow_focus 文案（已通过其自身安全校验）
        tf_focus = entry.get("tomorrow_focus") or ""

        if eliminated:
            tag = TAG_CANDIDATE_ELIMINATED
        elif triggered:
            tag = TAG_CANDIDATE_TRIGGERED
        else:
            tag = TAG_CANDIDATE_QUEUED

        reviews.append({
            "symbol": symbol,
            "name": name,
            "candidate_type": entry.get("candidate_type", ""),
            "observe_state": observe_state,
            "plan_action": plan_action,
            "triggered": triggered,
            "eliminated": eliminated,
            "entered_observation": entered_observation,
            "hit_type": entry.get("hit_type", ""),
            "downgrade_reason": entry.get("downgrade_reason", ""),
            "evidence_needed": list(entry.get("evidence_needed") or []),
            "tomorrow_focus": tf_focus,
            "tomorrow_focus_tag": tag,
        })
    return reviews, True, "ok"


# [TRACK-005] post_market_tracking_review
def _aggregate_tomorrow_focus(
    holdings_review: list[dict[str, Any]],
    observation_review: list[dict[str, Any]],
    candidate_review: list[dict[str, Any]],
    *,
    as_of: str = "",
) -> list[dict[str, Any]]:
    """聚合次日关注清单（按优先级排序）.

    每条：{priority, category, symbol, name, reason, suggested_next_step, source, as_of}
    - priority: P0/P1/P2/P3
    - category: holding_risk / observation_entry / observation_invalidated /
                candidate_triggered / candidate_eliminated / holding_no_analysis /
                observation_re_ta / candidate_queued
    - suggested_next_step: 软建议文案，不含强买卖词
    """
    items: list[dict[str, Any]] = []
    source = "post_market_tracking_review"

    # 1. 持仓风险（破位/大跌）→ P0；接近关键位 → P1；无 TA → P2
    for h in holdings_review:
        tag = h.get("tomorrow_focus_tag")
        symbol = h.get("symbol", "")
        name = h.get("name", symbol)
        if h.get("needs_attention"):
            items.append({
                "priority": "P0",
                "category": "holding_risk",
                "symbol": symbol,
                "name": name,
                "reason": h.get("review_note", ""),
                "suggested_next_step": "盘后复核 TA 计划与关键位，关注次日是否企稳",
                "source": source,
                "as_of": as_of,
            })
        elif tag == TAG_WATCH_KEY_LEVEL:
            items.append({
                "priority": "P1",
                "category": "holding_near_key_level",
                "symbol": symbol,
                "name": name,
                "reason": h.get("review_note", ""),
                "suggested_next_step": "关注关键位得失，必要时人工复核",
                "source": source,
                "as_of": as_of,
            })
        elif tag == TAG_REVIEW_TA:
            items.append({
                "priority": "P2",
                "category": "holding_no_analysis",
                "symbol": symbol,
                "name": name,
                "reason": h.get("review_note", ""),
                "suggested_next_step": "建议补一次轻量 TA 复核",
                "source": source,
                "as_of": as_of,
            })

    # 2. 观察仓：进入买点 → P0；接近买点 → P1；失效 → P2；需重新 TA → P2
    for o in observation_review:
        symbol = o.get("symbol", "")
        name = o.get("name", symbol)
        state = o.get("state", "")
        if state == STATE_IN_ENTRY_ZONE:
            items.append({
                "priority": "P0",
                "category": "observation_entry",
                "symbol": symbol,
                "name": name,
                "reason": o.get("review_note", ""),
                "suggested_next_step": "确认是否已记录入场意图，等待人工确认",
                "source": source,
                "as_of": as_of,
            })
        elif state == STATE_NEAR_ENTRY:
            items.append({
                "priority": "P1",
                "category": "observation_near_entry",
                "symbol": symbol,
                "name": name,
                "reason": o.get("review_note", ""),
                "suggested_next_step": "关注下一交易日是否进入买入区间",
                "source": source,
                "as_of": as_of,
            })
        elif o.get("invalidated"):
            items.append({
                "priority": "P2",
                "category": "observation_invalidated",
                "symbol": symbol,
                "name": name,
                "reason": o.get("review_note", ""),
                "suggested_next_step": "建议标记失效并降低权重",
                "source": source,
                "as_of": as_of,
            })
        elif o.get("needs_re_ta"):
            items.append({
                "priority": "P2",
                "category": "observation_re_ta",
                "symbol": symbol,
                "name": name,
                "reason": o.get("review_note", ""),
                "suggested_next_step": "建议重新 TA 复核后再决定是否继续观察",
                "source": source,
                "as_of": as_of,
            })

    # 3. 候选池：已触发 → P1；已淘汰 → P2；进入观察仓 → P2（确认联动）
    for c in candidate_review:
        symbol = c.get("symbol", "")
        name = c.get("name", symbol)
        if c.get("triggered"):
            items.append({
                "priority": "P1",
                "category": "candidate_triggered",
                "symbol": symbol,
                "name": name,
                "reason": c.get("tomorrow_focus") or "候选已触发",
                "suggested_next_step": "关注次日是否站稳触发价，确认是否加入观察仓",
                "source": source,
                "as_of": as_of,
            })
        elif c.get("eliminated"):
            items.append({
                "priority": "P2",
                "category": "candidate_eliminated",
                "symbol": symbol,
                "name": name,
                "reason": c.get("tomorrow_focus") or c.get("downgrade_reason") or "候选已淘汰",
                "suggested_next_step": "建议从候选池移除或降低权重",
                "source": source,
                "as_of": as_of,
            })
        elif c.get("entered_observation"):
            items.append({
                "priority": "P2",
                "category": "candidate_entered_observation",
                "symbol": symbol,
                "name": name,
                "reason": "候选已进入观察仓，继续跟踪",
                "suggested_next_step": "在观察仓中持续跟踪其触发与失效",
                "source": source,
                "as_of": as_of,
            })

    # 按优先级排序（P0 < P1 < P2 < P3）
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    items.sort(key=lambda g: priority_order.get(g.get("priority", "P3"), 9))
    return items


# [TRACK-005] post_market_tracking_review
def review_summary_has_forbidden_words(summary: dict[str, Any]) -> list[str]:
    """扫描复盘摘要中所有文本字段，返回命中的禁用词列表（供测试核验）."""
    hits: list[str] = []

    def _scan(obj: Any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                _scan(v)
        elif isinstance(obj, list):
            for v in obj:
                _scan(v)
        elif isinstance(obj, str):
            word = _contains_forbidden_word(obj)
            if word:
                hits.append(word)

    _scan(summary)
    return hits


# [TRACK-010] review_encoding_regression
# ── 编码/转义清洗 ────────────────────────────────────────────────────────
#
# 用户反馈跟踪看板"盘后复盘"区域出现乱码。常见根因有三类：
# 1. SQLite 文本列里写入了 ASCII 转义后的 JSON（例如 ``"\u4e2d\u6587"``），
#    之后被原样回填到 review_summary 字符串字段，前端就会看到字面的
#    ``\u4e2d\u6587`` 而不是中文。
# 2. 字段中残留 BOM（``\ufeff``）、不可见控制符、或 ``repr()`` 形态的
#    ``b'...'`` bytes 文本。
# 3. 复盘文案里夹带了 markdown 表格 / 多行换行，被前端渲染成"一团乱"
#    （视觉上的乱码）。
#
# 这些问题不应该在引擎里逐字段打补丁（引擎只负责语义），所以在
# ``tracking_board_service._build_review_summary`` 把引擎结果交给 API
# 之前，统一调用 ``sanitize_review_summary_text`` 做一次防御性清洗。
#
# 设计原则
# --------
# 1. **永不抛异常**：任何字段清洗失败都退回原值，不让编码问题拖垮整个
#    看板。
# 2. **只清洗文本字段**：数字/布尔/None 保持原值；列表/字典递归处理。
# 3. **可单测**：每个分支都有独立 fixture（见
#    ``tests/test_track010_review_encoding_regression.py``）。

import re as _re

# 形如 "\u4e2d\u6587" 的字面 ASCII 转义序列（注意：这里匹配的是字面
# 反斜杠+u+4 位十六分），用于把被 json.dumps(ensure_ascii=True) 二次
# 转义过的中文还原回 UTF-8。
_LITERAL_UNICODE_ESCAPE = _re.compile(r"\\u([0-9a-fA-F]{4})")

# 形如 "\n" / "\t" / "\r" 的字面转义（反斜杠+n 等单字符），出现在普通
# 字符串里时通常意味着上游做过二次 json.dumps；还原成真实控制符。
_LITERAL_CTRL_ESCAPE = _re.compile(r"\\([ntr])")

# bytes repr 形态：b'...' 或 b"..."，多见于把 bytes 直接 str() 后塞进
# 字段；尝试以 UTF-8 解码内容，失败则保持原值。
_BYTES_REPR = _re.compile(r"""^b(['"])(.*)\1$""", _re.DOTALL)

# BOM 字符（zero width no-break space）——出现在文本开头会把首字符推到
# 后面，看起来像乱码。
_BOM_CHAR = "\ufeff"

# 其他不可见控制符（C0 控制符除 \n \t 外）
_INVISIBLE_CTRL = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _decode_literal_unicode(s: str) -> str:
    """``"\\u4e2d\\u6587"`` → ``"中文"``.

    若字符串里同时含真实中文和字面转义，只还原字面转义部分。
    """
    def _sub(m: "_re.Match[str]") -> str:
        try:
            return chr(int(m.group(1), 16))
        except (ValueError, OverflowError):
            return m.group(0)
    return _LITERAL_UNICODE_ESCAPE.sub(_sub, s)


def _decode_literal_ctrl(s: str) -> str:
    """``"行1\\n行2"`` → ``"行1\\n行2"`` (真实换行).

    仅处理字面反斜杠+n/t/r，不影响已经是真实换行的字符串。
    """
    mapping = {"n": "\n", "t": "\t", "r": "\r"}
    return _LITERAL_CTRL_ESCAPE.sub(lambda m: mapping.get(m.group(1), m.group(0)), s)


def _decode_bytes_repr(s: str) -> str:
    """``"b'\\xe8\\xb4\\xb5'"`` → ``"贵"``.

    Handles two cases:
    1. ``b'...'`` repr form — parse the ``\\xHH`` escapes into raw bytes,
       then UTF-8 decode (fall back to latin-1 / replace on failure).
    2. Other strings are returned unchanged.
    """
    m = _BYTES_REPR.match(s)
    if not m:
        return s
    inner = m.group(2)
    # Parse \xHH escapes into actual bytes. Also handle \uXXXX for completeness.
    byte_buf = bytearray()
    i = 0
    saw_escape = False
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 3 < len(inner) and inner[i + 1] in ("x", "X"):
            hex_chunk = inner[i + 2 : i + 4]
            try:
                byte_buf.append(int(hex_chunk, 16))
                i += 4
                saw_escape = True
                continue
            except ValueError:
                pass
        if ch == "\\" and i + 5 < len(inner) and inner[i + 1] in ("u", "U"):
            hex_chunk = inner[i + 2 : i + 6]
            try:
                byte_buf.extend(chr(int(hex_chunk, 16)).encode("utf-8"))
                i += 6
                saw_escape = True
                continue
            except ValueError:
                pass
        # Plain ASCII byte (also part of UTF-8 for codepoints < 128).
        byte_buf.extend(ch.encode("utf-8"))
        i += 1
    if not saw_escape:
        # No escapes consumed — don't risk re-encoding a normal "b'foo'" string.
        return s
    try:
        return byte_buf.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return byte_buf.decode("utf-8", errors="replace")
        except Exception:
            return s


def _sanitize_text(value: str) -> str:
    """对一个字符串做 UTF-8/转义清洗，返回稳定 UTF-8 文本。"""
    if not isinstance(value, str):
        return value  # type: ignore[return-value]
    # 1) bytes repr → utf-8 text
    cleaned = _decode_bytes_repr(value)
    # 2) 还原字面 \uXXXX
    cleaned = _decode_literal_unicode(cleaned)
    # 3) 还原字面 \n \t \r
    cleaned = _decode_literal_ctrl(cleaned)
    # 4) 去 BOM
    if _BOM_CHAR in cleaned:
        cleaned = cleaned.replace(_BOM_CHAR, "")
    # 5) 去除其它不可见控制符（保留 \n \t）
    cleaned = _INVISIBLE_CTRL.sub("", cleaned)
    return cleaned


# [TRACK-010] review_encoding_regression
def sanitize_review_summary_text(node: Any) -> Any:
    """递归清洗 review_summary（或任意 dict/list 结构）中的字符串字段.

    - bytes → UTF-8 解码失败则保留 repr。
    - 字面 ``\\uXXXX`` / ``\\n`` / ``\\t`` / ``\\r`` → 还原成真实字符。
    - BOM 与不可见控制符剔除。
    - 数字 / 布尔 / None 不变。
    - 永不抛异常：单字段清洗失败回退为原值。
    """
    if node is None:
        return None
    if isinstance(node, str):
        try:
            return _sanitize_text(node)
        except Exception:
            return node
    if isinstance(node, bytes):
        try:
            return node.decode("utf-8")
        except Exception:
            try:
                return node.decode("utf-8", errors="replace")
            except Exception:
                return node
    if isinstance(node, dict):
        return {k: sanitize_review_summary_text(v) for k, v in node.items()}
    if isinstance(node, list):
        return [sanitize_review_summary_text(v) for v in node]
    if isinstance(node, tuple):
        return tuple(sanitize_review_summary_text(v) for v in node)
    return node
