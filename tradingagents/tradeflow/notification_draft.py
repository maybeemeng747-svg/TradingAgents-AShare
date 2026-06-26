# [TRACK-NOTIFY-001] notification_payload_dry_run
# [NOTIFY-002] notification_mandate_data_blockers
"""
飞书 / 总控官通知草稿 payload 与去噪规则（纯引擎）.

TA 侧生成给 investment-controller / 飞书使用的通知草稿。第一阶段只产出
``dry-run`` payload —— 不读取 / 打印 webhook，不真实发送飞书，只生成本地
markdown / json 预览，交给 OpenClaw / investment-controller 决定是否发。

NOTIFY-002 在 TRACK-NOTIFY-001 之上接入 H-015 昊天日报与 DATA-021 数据缺口，
让用户早上 / 盘后能看到主题重心与数据风险，而不是只看到候选数量：两个结构化
摘要（``mandate_daily_digest`` / ``data_blocker_digest``）既作为顶层 payload
字段供 investment-controller 直接读取，也各自生成一条 P2 日报草稿进入
``daily_digest`` 通道（不进入 ``intraday_push``）。

设计原则
--------
1. 纯函数：不访问数据库、不联网、不读环境变量（webhook 等敏感配置由调用方
   的 service 层负责，本引擎只接收结构化上下文 dict）。
2. 安全红线：
   - 永不输出"立即买入 / 重仓 / 清仓 / 满仓 / 梭哈"等强动作词；
     ``suggested_next_step`` 只描述软状态（关注 / 复核 / 记录 / 等 TA）。
   - 数据不足（missing / failed / data_missing）的草稿标记 ``record_only=True``，
     只进日报记录，绝不进入盘中主动提醒队列。
3. 去噪规则：
   - 同一标的同一事件 (``symbol`` + ``event_type``) 在 ``DEDUP_WINDOW_SECONDS``
     （默认 30 分钟）内不重复；重复项进入 ``deduplicated`` 列表并标注 reason。
   - 只有 P0 / P1 且非 record_only 的草稿才能进入 ``intraday_push`` 盘中提醒队列；
     P2 / P3 一律只进 ``daily_digest`` 日报。
4. 输入契约：接收 IC-TA-001 的 investment-controller context dict（六个 bucket），
   每条草稿都带 ``source`` + ``as_of``，与 IC-TA-001 的数据契约保持一致。

模块不持久化去噪状态；``NotificationDeduplicator`` 是内存态，由 service 层
持有跨调用复用，测试可注入新实例保证确定性。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

# 复用 TRACK-004 的强动作禁用词，并补充通知场景常见的强词
from tradingagents.tradeflow.observation_state_engine import (
    FORBIDDEN_STRONG_WORDS as _OBS_FORBIDDEN_WORDS,
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

# [TRACK-NOTIFY-001] notification_payload_dry_run
# ── Schema / 常量 ────────────────────────────────────────────────────────────

NOTIFY_SCHEMA_VERSION = "1.0"
NOTIFY_SOURCE = "notification_draft_engine"

# 优先级（与 today_guidance 对齐）
PRIORITY_P0 = "P0"
PRIORITY_P1 = "P1"
PRIORITY_P2 = "P2"
PRIORITY_P3 = "P3"
ALLOWED_PRIORITIES = (PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3)

# 投递通道
CHANNEL_INTRADAY_PUSH = "intraday_push"   # 盘中主动提醒队列（仅 P0/P1）
CHANNEL_DAILY_DIGEST = "daily_digest"     # 夜间日报（P2/P3 + record_only）

# 去噪窗口：同一标的同一事件 30 分钟内不重复
DEDUP_WINDOW_SECONDS = 30 * 60

# 数据状态：与 IC-TA-001 对齐，这些状态的草稿只能 record_only
_RECORD_ONLY_DATA_STATUSES = frozenset({"missing", "failed"})

# 强动作禁用词（引擎输出不得出现）
FORBIDDEN_STRONG_WORDS = tuple(dict.fromkeys(
    _OBS_FORBIDDEN_WORDS + ("立即卖出", "全仓", "必涨", "必跌", "无脑买", "加杠杆")
))

# 持仓风险阈值（与 tracking_board_service._aggregate_today_guidance 一致）
HOLDINGS_LARGE_DROP_THRESHOLD = -3.0
HOLDINGS_MODERATE_DROP_THRESHOLD = -1.5

# 事件类型常量
EVENT_HOLDINGS_RISK_LARGE = "holdings_risk_large_drop"
EVENT_HOLDINGS_RISK_MODERATE = "holdings_risk_moderate_drop"
EVENT_HOLDINGS_NO_ANALYSIS = "holdings_no_analysis"
EVENT_OBSERVATION_IN_ENTRY_ZONE = "observation_in_entry_zone"
EVENT_OBSERVATION_INVALIDATED = "observation_invalidated"
EVENT_OBSERVATION_NEAR_ENTRY = "observation_near_entry"
EVENT_OBSERVATION_TA_REQUIRED = "observation_ta_required"
EVENT_OBSERVATION_MISSED_ENTRY = "observation_missed_entry"
EVENT_OBSERVATION_DATA_MISSING = "observation_data_missing"
EVENT_OBSERVATION_NEEDS_REVIEW = "observation_needs_review"
EVENT_OBSERVATION_WATCHING = "observation_watching"
EVENT_PENDING_TA_REQUIRED = "pending_ta_required"
EVENT_DATA_SOURCE_FAILURE = "data_source_failure"
EVENT_DATA_STALE = "data_stale"
# [NOTIFY-002] notification_mandate_data_blockers
EVENT_MANDATE_DAILY_DIGEST = "mandate_daily_digest"
EVENT_DATA_BLOCKER_DIGEST = "data_blocker_digest"


# ── 草稿构造 ──────────────────────────────────────────────────────────────────

def _draft(
    *,
    event_type: str,
    priority: str,
    symbol: str,
    name: str,
    title: str,
    reason: str,
    source: str,
    as_of: str,
    suggested_next_step: str,
    record_only: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造一条标准通知草稿 dict（schema 见 TRACK-NOTIFY-001 实现要点 1）."""
    draft = {
        "event_type": event_type,
        "priority": priority,
        "symbol": symbol,
        "name": name or symbol,
        "title": title,
        "reason": reason,
        "source": source,
        "as_of": as_of,
        "suggested_next_step": suggested_next_step,
        "record_only": bool(record_only),
    }
    if extra:
        draft["extra"] = dict(extra)
    return draft


def _clip_reason(text: Any, limit: int = 240) -> str:
    if text is None:
        return ""
    compact = " ".join(str(text).split()).strip()
    return compact[:limit]


# ── 去噪器 ────────────────────────────────────────────────────────────────────

class NotificationDeduplicator:
    """内存态去噪器：按 (symbol, event_type) 在窗口内去重.

    线程安全性由调用方保证（service 层默认单例 + GIL 下足够 dry-run 场景使用）。
    """

    def __init__(self, window_seconds: int = DEDUP_WINDOW_SECONDS) -> None:
        self._window = int(window_seconds)
        # key -> last emitted datetime
        self._seen: dict[str, datetime] = {}

    def dedup_key(self, draft: dict[str, Any]) -> str:
        """同一标的同一事件视为重复（symbol 为空时按 event_type 聚合系统级事件）."""
        symbol = str(draft.get("symbol", "")).strip()
        event_type = str(draft.get("event_type", "")).strip()
        return f"{symbol}|{event_type}"

    def should_emit(
        self, draft: dict[str, Any], now: datetime
    ) -> tuple[bool, str, str]:
        """返回 (是否放行, dedup_key, 抑制原因).

        抑制原因: ``""`` 表示放行；否则是描述（例如"30 分钟内已发送过"）。
        """
        key = self.dedup_key(draft)
        last = self._seen.get(key)
        if last is None:
            return True, key, ""
        elapsed = (now - last).total_seconds()
        if elapsed < self._window:
            return False, key, (
                f"同一事件 {elapsed/60:.1f} 分钟内已发送过（窗口 {self._window/60:.0f} 分钟）"
            )
        return True, key, ""

    def mark_emitted(self, draft: dict[str, Any], now: datetime) -> None:
        self._seen[self.dedup_key(draft)] = now

    def reset(self) -> None:
        self._seen.clear()


# ── 通道分类 ──────────────────────────────────────────────────────────────────

def classify_delivery_channel(draft: dict[str, Any]) -> str:
    """P0/P1 且非 record_only -> intraday_push；其余 -> daily_digest.

    验收约束：P2/P3 只进日报，不盘中推送；数据不足只记录不推送。
    """
    if draft.get("record_only"):
        return CHANNEL_DAILY_DIGEST
    priority = draft.get("priority", PRIORITY_P3)
    if priority in (PRIORITY_P0, PRIORITY_P1):
        return CHANNEL_INTRADAY_PUSH
    return CHANNEL_DAILY_DIGEST


# ── 草稿生成：从 IC-TA-001 context 六个 bucket 合成 ───────────────────────────

def build_notification_drafts_from_context(
    context: dict[str, Any],
    *,
    as_of: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """从 investment-controller context（IC-TA-001）合成通知草稿列表.

    纯函数：不读 DB、不联网。输入是 ``get_investment_controller_context`` 的
    返回值（或等价 dict）。输出为未去噪的草稿列表（调用方负责套去噪器）。

    每条草稿 schema: event_type / priority / symbol / name / title / reason /
    source / as_of / suggested_next_step / record_only。
    """
    now = now or datetime.now()
    as_of = as_of or context.get("as_of") or now.strftime("%Y-%m-%d %H:%M:%S")
    drafts: list[dict[str, Any]] = []

    holdings = context.get("holdings") or {}
    observation = context.get("observation_warehouse") or {}
    candidates = context.get("tradeflow_candidates") or {}
    data_health = context.get("data_health") or {}
    pending_ta = context.get("pending_ta_required") or {}

    is_trading = bool(context.get("is_trading_day", True))

    drafts.extend(_build_holdings_drafts(holdings, is_trading, as_of))
    drafts.extend(_build_observation_drafts(observation, is_trading, as_of))
    drafts.extend(_build_candidate_drafts(candidates, as_of))
    drafts.extend(_build_data_health_drafts(data_health, is_trading, as_of))
    drafts.extend(_build_pending_ta_drafts(pending_ta, as_of))
    # [NOTIFY-002] 昊天日报 + 数据缺口摘要草稿（P2 日报通道）
    mandate_bucket = context.get("mandate_daily_report") or {}
    blockers_bucket = context.get("recent_report_data_blockers") or {}
    mandate_digest = build_mandate_daily_digest(mandate_bucket)
    blocker_digest = build_data_blocker_digest(blockers_bucket)
    mandate_draft = _build_mandate_digest_draft(mandate_digest, as_of)
    if mandate_draft is not None:
        drafts.append(mandate_draft)
    blocker_draft = _build_data_blocker_digest_draft(blocker_digest, as_of)
    if blocker_draft is not None:
        drafts.append(blocker_draft)

    # 统一裁剪 reason，并做强动作词自检（防御性）
    cleaned: list[dict[str, Any]] = []
    for d in drafts:
        d = dict(d)
        d["reason"] = _clip_reason(d.get("reason"))
        d["title"] = _clip_reason(d.get("title"), 80)
        cleaned.append(d)
    assert_no_forbidden_words(cleaned)
    return cleaned


def _build_holdings_drafts(
    holdings_bucket: dict[str, Any], is_trading_day: bool, as_of: str
) -> list[dict[str, Any]]:
    """持仓风险草稿：大跌 / 中跌 / 无 TA 报告。数据缺失时只记录."""
    drafts: list[dict[str, Any]] = []
    data_status = str(holdings_bucket.get("data_status", "missing"))
    bucket_record_only = data_status in _RECORD_ONLY_DATA_STATUSES

    for h in holdings_bucket.get("items", []) or []:
        symbol = str(h.get("symbol", "")).strip()
        name = str(h.get("name", symbol))
        change_pct = _to_float(h.get("price_change_pct"))
        live_price = _to_float(h.get("live_price"))
        # 数据不足：行情缺失，本标的只能记录
        item_record_only = bucket_record_only or live_price == 0.0

        if change_pct != 0.0 and change_pct <= HOLDINGS_LARGE_DROP_THRESHOLD:
            drafts.append(_draft(
                event_type=EVENT_HOLDINGS_RISK_LARGE,
                priority=PRIORITY_P0,
                symbol=symbol,
                name=name,
                title=f"持仓 {name} 日跌幅 {change_pct:.2f}%",
                reason=(
                    f"持仓 {symbol} ({name}) 当前跌幅 {change_pct:.2f}%，"
                    f"超过 {HOLDINGS_LARGE_DROP_THRESHOLD:.1f}% 阈值，需立即关注风险。"
                ),
                source="holdings_snapshot",
                as_of=as_of,
                suggested_next_step="人工复核持仓风险，结合最新 TA 报告与止损纪律评估是否减仓。",
                record_only=item_record_only,
                extra={"price_change_pct": change_pct, "live_price": live_price or None},
            ))
        elif change_pct != 0.0 and change_pct <= HOLDINGS_MODERATE_DROP_THRESHOLD:
            drafts.append(_draft(
                event_type=EVENT_HOLDINGS_RISK_MODERATE,
                priority=PRIORITY_P1,
                symbol=symbol,
                name=name,
                title=f"持仓 {name} 日跌幅 {change_pct:.2f}%",
                reason=(
                    f"持仓 {symbol} ({name}) 当前跌幅 {change_pct:.2f}%，"
                    f"超过 {HOLDINGS_MODERATE_DROP_THRESHOLD:.1f}% 关注线。"
                ),
                source="holdings_snapshot",
                as_of=as_of,
                suggested_next_step="关注盘中走势与板块联动，必要时人工复核。",
                record_only=item_record_only,
                extra={"price_change_pct": change_pct, "live_price": live_price or None},
            ))

        # 持仓无最新 TA 报告（analysis 为空）
        report = h.get("latest_report") or {}
        if not report or not report.get("report_id"):
            drafts.append(_draft(
                event_type=EVENT_HOLDINGS_NO_ANALYSIS,
                priority=PRIORITY_P2,
                symbol=symbol,
                name=name,
                title=f"持仓 {name} 缺少最新 TA 报告",
                reason=f"持仓 {symbol} ({name}) 暂无最新 TA 报告，无法自动评估风险敞口。",
                source="holdings_snapshot",
                as_of=as_of,
                suggested_next_step="安排一次持仓复盘 TA（建议 POSITION_RISK_LIGHT profile）。",
                record_only=False,
            ))
    return drafts


def _build_observation_drafts(
    observation_bucket: dict[str, Any], is_trading_day: bool, as_of: str
) -> list[dict[str, Any]]:
    """观察仓草稿：复用 TRACK-004 状态引擎派生事件.

    data_missing / needs_review 只记录不推送（record_only=True）。
    """
    drafts: list[dict[str, Any]] = []
    data_status = str(observation_bucket.get("data_status", "missing"))
    bucket_record_only = data_status in _RECORD_ONLY_DATA_STATUSES

    for item in observation_bucket.get("items", []) or []:
        symbol = str(item.get("symbol", "")).strip()
        name = str(item.get("name", symbol))
        stored_status = str(item.get("status", "watching"))

        # 复用状态引擎（纯函数）
        result = evaluate_observation_state(item, is_trading_day=is_trading_day)
        state = result.get("state", STATE_WATCHING)
        reason_text = _clip_reason(result.get("reason"), 200)

        event_type, priority, title, next_step = _OBSERVATION_EVENT_MAP.get(
            state, _OBSERVATION_EVENT_DEFAULT
        )

        # data_missing / needs_review：数据不足只记录
        record_only = bucket_record_only or state in (STATE_DATA_MISSING, STATE_NEEDS_REVIEW)

        drafts.append(_draft(
            event_type=event_type,
            priority=priority,
            symbol=symbol,
            name=name,
            title=title.format(name=name),
            reason=f"观察仓 {symbol} ({name})：{reason_text}（派生状态={state}）",
            source="observation_warehouse",
            as_of=as_of,
            suggested_next_step=next_step,
            record_only=record_only,
            extra={
                "state": state,
                "stored_status": stored_status,
                "entry_low": item.get("entry_low"),
                "entry_high": item.get("entry_high"),
                "trigger_price": item.get("trigger_price"),
                "invalid_price": item.get("invalid_price"),
                "live_price": item.get("live_price"),
            },
        ))
    return drafts


# 观察仓派生状态 -> (event_type, priority, title, suggested_next_step)
_OBSERVATION_EVENT_MAP: dict[str, tuple[str, str, str, str]] = {
    STATE_IN_ENTRY_ZONE: (
        EVENT_OBSERVATION_IN_ENTRY_ZONE,
        PRIORITY_P0,
        "观察仓 {name} 进入买入区间",
        "人工确认是否在计划仓位内执行，结合 TA 报告与资金纪律。",
    ),
    STATE_INVALIDATED: (
        EVENT_OBSERVATION_INVALIDATED,
        PRIORITY_P1,
        "观察仓 {name} 已跌破失效价",
        "标记失效并复盘原因，必要时从观察仓移除或重新规划。",
    ),
    STATE_NEAR_ENTRY: (
        EVENT_OBSERVATION_NEAR_ENTRY,
        PRIORITY_P1,
        "观察仓 {name} 接近买点",
        "关注盘中触发条件，准备好触发后的确认流程。",
    ),
    STATE_TA_REQUIRED: (
        EVENT_OBSERVATION_TA_REQUIRED,
        PRIORITY_P1,
        "观察仓 {name} 需要 TA 深度确认",
        "安排轻量/完整 TA 复核，确认前不要进入实盘。",
    ),
    STATE_MISSED_ENTRY: (
        EVENT_OBSERVATION_MISSED_ENTRY,
        PRIORITY_P2,
        "观察仓 {name} 可能已错过买点",
        "复盘是否等待下一个回踩窗口或从观察仓移除。",
    ),
    STATE_DATA_MISSING: (
        EVENT_OBSERVATION_DATA_MISSING,
        PRIORITY_P2,
        "观察仓 {name} 行情数据缺失",
        "等待行情恢复后重新评估，不进入盘中提醒队列。",
    ),
    STATE_NEEDS_REVIEW: (
        EVENT_OBSERVATION_NEEDS_REVIEW,
        PRIORITY_P3,
        "观察仓 {name} 需人工复核",
        "非交易日或无行情，下一交易日开盘前人工复核。",
    ),
    STATE_WATCHING: (
        EVENT_OBSERVATION_WATCHING,
        PRIORITY_P3,
        "观察仓 {name} 持续观察中",
        "继续观察，价格未接近买点。",
    ),
}
_OBSERVATION_EVENT_DEFAULT: tuple[str, str, str, str] = (
    EVENT_OBSERVATION_WATCHING,
    PRIORITY_P3,
    "观察仓 {name} 持续观察中",
    "继续观察。",
)


def _build_candidate_drafts(
    candidates_bucket: dict[str, Any], as_of: str
) -> list[dict[str, Any]]:
    """候选池草稿：需要深度 TA 的候选进日报队列（不盘中推送）."""
    drafts: list[dict[str, Any]] = []
    data_status = str(candidates_bucket.get("data_status", "missing"))
    bucket_record_only = data_status in _RECORD_ONLY_DATA_STATUSES

    for c in candidates_bucket.get("items", []) or []:
        if not c.get("need_deep_ta"):
            continue
        symbol = str(c.get("symbol", "")).strip()
        name = str(c.get("name", symbol))
        score = c.get("composite_score")
        drafts.append(_draft(
            event_type=EVENT_PENDING_TA_REQUIRED,
            priority=PRIORITY_P2,
            symbol=symbol,
            name=name,
            title=f"候选 {name} 等待深度 TA 确认",
            reason=(
                f"TradeFlow 候选 {symbol} ({name})"
                + (f" 评分 {score}" if score is not None else "")
                + " 标记为需要深度 TA，尚未进入实盘。"
            ),
            source="tradeflow_candidates",
            as_of=as_of,
            suggested_next_step="加入 TA 研究队列，由用户确认后跑轻量/完整 TA。",
            record_only=bucket_record_only,
            extra={"composite_score": score, "candidate_type": c.get("candidate_type")},
        ))
    return drafts


def _build_data_health_drafts(
    data_health_bucket: dict[str, Any], is_trading_day: bool, as_of: str
) -> list[dict[str, Any]]:
    """数据健康草稿：交易日源失败 / 行情 stale 只记录进日报（系统级，symbol 为空）."""
    drafts: list[dict[str, Any]] = []
    data_status = str(data_health_bucket.get("data_status", "missing"))
    sources = data_health_bucket.get("sources", []) or []

    failed_sources = [
        s for s in sources
        if str(s.get("status", "")).upper() in {"FAILED", "ERROR"}
    ]
    if failed_sources or data_status == "failed":
        names = ", ".join(str(s.get("name", s.get("source", "?"))) for s in failed_sources[:5])
        drafts.append(_draft(
            event_type=EVENT_DATA_SOURCE_FAILURE,
            priority=PRIORITY_P2,
            symbol="",
            name="SYSTEM",
            title="数据源失败告警",
            reason=(
                f"以下数据源状态失败：{names or '未知源'}。"
                "候选池与 TA 报告可能基于降级数据，结论需人工复核。"
            ),
            source="tradeflow_data_health",
            as_of=as_of,
            suggested_next_step="检查数据源 fallback 链路，必要时重跑受影响的候选池/TA。",
            record_only=True,
            extra={"failed_count": len(failed_sources)},
        ))
    elif data_status == "stale" and is_trading_day:
        drafts.append(_draft(
            event_type=EVENT_DATA_STALE,
            priority=PRIORITY_P2,
            symbol="",
            name="SYSTEM",
            title="实时行情 freshness 降级",
            reason="交易日实时行情降级为 stale，盘中提醒可能基于延迟数据。",
            source="tradeflow_data_health",
            as_of=as_of,
            suggested_next_step="等待行情恢复或人工确认数据可用后再触发盘中提醒。",
            record_only=True,
        ))
    return drafts


def _build_pending_ta_drafts(
    pending_bucket: dict[str, Any], as_of: str
) -> list[dict[str, Any]]:
    """pending_ta_required bucket：每个待 TA 标的进日报（去重靠 symbol 与 candidate 草稿区分）.

    为避免与候选池 pending_ta 草稿重复，本 bucket 只输出 origin=observation_warehouse
    的条目（候选侧已在 _build_candidate_drafts 处理）。
    """
    drafts: list[dict[str, Any]] = []
    for item in pending_bucket.get("items", []) or []:
        origin = str(item.get("origin", ""))
        if origin != "observation_warehouse":
            continue
        symbol = str(item.get("symbol", "")).strip()
        name = str(item.get("name", symbol))
        drafts.append(_draft(
            event_type=EVENT_PENDING_TA_REQUIRED,
            priority=PRIORITY_P2,
            symbol=symbol,
            name=name,
            title=f"观察仓 {name} 等待 TA 确认",
            reason=f"观察仓 {symbol} ({name}) 标记为需要 TA 深度确认。",
            source="pending_ta_required",
            as_of=as_of,
            suggested_next_step="安排 TA 复核，确认前不进入实盘。",
            record_only=False,
            extra={"origin": origin},
        ))
    return drafts


# ── NOTIFY-002: 昊天日报 / 数据缺口摘要 ──────────────────────────────────────
# [NOTIFY-002] notification_mandate_data_blockers
#
# TRACK-NOTIFY-001 的草稿都是"单标的单事件"。NOTIFY-002 把 IC-TA-002 新增的
# 两个顶层 bucket（``mandate_daily_report`` / ``recent_report_data_blockers``）
# 压缩成两条 P2 日报草稿，同时把结构化摘要作为顶层字段返回给 service 层。
#
# 去噪语义：两个摘要都是"早上/盘后看的全局摘要"，按设计只进 ``daily_digest``
# （P2），绝不进入 ``intraday_push`` 盘中主动提醒队列。数据缺口草稿额外标记
# ``record_only=True``：缺口本身是事实陈述，不构成可操作提醒。

# 摘要里最多保留的主题/候选/字段条数（避免日报过长）
_MANDATE_DIGEST_TOPIC_LIMIT = 3
_MANDATE_DIGEST_CANDIDATE_LIMIT = 3
_MANDATE_DIGEST_GAP_LIMIT = 5
_BLOCKER_DIGEST_FIELD_LIMIT = 5
_BLOCKER_DIGEST_SYMBOL_LIMIT = 5


def build_mandate_daily_digest(
    mandate_bucket: dict[str, Any] | None,
) -> dict[str, Any]:
    """从 IC-TA-002 ``mandate_daily_report`` bucket 压缩出日报摘要.

    返回稳定结构（即使 bucket 缺失也返回 ``available=False`` 的空壳）::

        {
            "available": bool,            # mandate report 是否 fresh
            "report_as_of": str,
            "rising_topic_count": int,
            "cooling_topic_count": int,
            "main_candidate_count": int,
            "observation_candidate_count": int,
            "evidence_gap_count": int,
            "top_rising_topics": [...],   # [{topic, status_label, heat_trend_label, candidate_count}]
            "top_main_candidates": [...], # [{symbol, name, topic, candidate_type, mandate_score}]
            "evidence_gaps": [...],       # 字符串列表
            "summary_text": str,          # 一句话摘要（不含强买卖词）
        }
    """
    empty = {
        "available": False,
        "report_as_of": "",
        "rising_topic_count": 0,
        "cooling_topic_count": 0,
        "main_candidate_count": 0,
        "observation_candidate_count": 0,
        "evidence_gap_count": 0,
        "top_rising_topics": [],
        "top_main_candidates": [],
        "evidence_gaps": [],
        "summary_text": "",
    }
    if not isinstance(mandate_bucket, dict):
        return empty
    # 只有 fresh 才视为可用；missing/failed/skipped 都返回空壳（避免把"无日报"
    # 误报成"昊天平静"）。
    if str(mandate_bucket.get("data_status", "missing")) != "fresh":
        return empty

    rising = mandate_bucket.get("rising_topics", []) or []
    main_candidates = mandate_bucket.get("main_candidates", []) or []
    gaps = mandate_bucket.get("evidence_gaps", []) or []

    top_rising = [
        {
            "topic": t.get("topic", ""),
            "status_label": t.get("status_label", ""),
            "heat_trend_label": t.get("heat_trend_label", ""),
            "candidate_count": t.get("candidate_count", 0),
        }
        for t in rising[:_MANDATE_DIGEST_TOPIC_LIMIT]
        if isinstance(t, dict)
    ]
    top_candidates = [
        {
            "symbol": c.get("symbol", ""),
            "name": c.get("name", ""),
            "topic": c.get("topic", ""),
            "candidate_type": c.get("candidate_type", ""),
            "mandate_score": c.get("mandate_score"),
        }
        for c in main_candidates[:_MANDATE_DIGEST_CANDIDATE_LIMIT]
        if isinstance(c, dict)
    ]
    gap_list = [str(g) for g in gaps[:_MANDATE_DIGEST_GAP_LIMIT] if g]

    rising_count = int(mandate_bucket.get("rising_topic_count", len(rising)) or 0)
    main_count = int(mandate_bucket.get("main_candidate_count", len(main_candidates)) or 0)
    gap_count = int(mandate_bucket.get("evidence_gap_count", len(gaps)) or 0)

    if rising_count or main_count:
        summary = (
            f"昊天日报：{rising_count} 个升温主题，{main_count} 个主候选"
            + (f"，{gap_count} 项证据缺口" if gap_count else "")
        )
    else:
        summary = "昊天日报：暂无升温主题与主候选"

    return {
        "available": True,
        "report_as_of": str(mandate_bucket.get("report_as_of", "")),
        "rising_topic_count": rising_count,
        "cooling_topic_count": int(mandate_bucket.get("cooling_topic_count", 0) or 0),
        "main_candidate_count": main_count,
        "observation_candidate_count": int(
            mandate_bucket.get("observation_candidate_count", 0) or 0
        ),
        "evidence_gap_count": gap_count,
        "top_rising_topics": top_rising,
        "top_main_candidates": top_candidates,
        "evidence_gaps": gap_list,
        "summary_text": summary,
    }


def build_data_blocker_digest(
    blockers_bucket: dict[str, Any] | None,
) -> dict[str, Any]:
    """从 IC-TA-002 ``recent_report_data_blockers`` bucket 压缩出数据缺口摘要.

    返回稳定结构（即使 bucket 缺失也返回 ``available=False`` 的空壳）::

        {
            "available": bool,            # 是否成功扫描过报告
            "has_blockers": bool,         # 是否存在 severe 缺口
            "scanned_report_count": int,
            "affected_report_count": int,
            "total_severe_blockers": int,
            "summary_level": str,         # ok / warning
            "top_failed_fields": [...],   # [{field, count}] 按次数倒序
            "affected_symbols": [...],    # [{symbol, severe_count, fields}]
            "summary_text": str,
        }
    """
    empty = {
        "available": False,
        "has_blockers": False,
        "scanned_report_count": 0,
        "affected_report_count": 0,
        "total_severe_blockers": 0,
        "summary_level": "ok",
        "top_failed_fields": [],
        "affected_symbols": [],
        "summary_text": "",
    }
    if not isinstance(blockers_bucket, dict):
        return empty
    # failed/missing 时不算"成功扫描"，只返回空壳，避免把"没报告"误报成"无缺口"。
    status = str(blockers_bucket.get("data_status", "missing"))
    if status not in ("fresh", "stale", "skipped"):
        return empty

    field_counts = blockers_bucket.get("field_counts", {}) or {}
    affected = blockers_bucket.get("affected_symbols", []) or []
    total_severe = int(blockers_bucket.get("total_severe_blockers", 0) or 0)
    scanned = int(blockers_bucket.get("scanned_report_count", 0) or 0)
    affected_count = int(blockers_bucket.get("affected_report_count", len(affected)) or 0)
    summary_level = str(blockers_bucket.get("summary_level", "ok") or "ok")

    # 按失败次数倒序取 top N 字段
    sorted_fields = sorted(
        (
            {"field": str(k), "count": int(v or 0)}
            for k, v in field_counts.items()
            if isinstance(v, (int, float)) and v
        ),
        key=lambda x: x["count"],
        reverse=True,
    )[:_BLOCKER_DIGEST_FIELD_LIMIT]

    slim_affected = [
        {
            "symbol": a.get("symbol", ""),
            "severe_count": int(a.get("severe_count", 0) or 0),
            "fields": list(a.get("fields", []) or [])[:3],
        }
        for a in affected[:_BLOCKER_DIGEST_SYMBOL_LIMIT]
        if isinstance(a, dict)
    ]

    has_blockers = bool(affected) and total_severe > 0
    if has_blockers:
        top_field_names = ", ".join(f["field"] for f in sorted_fields[:3]) or "未知字段"
        summary = (
            f"最近报告数据缺口：{affected_count} 份报告受影响，"
            f"共 {total_severe} 项严重缺口（{top_field_names}）"
        )
    elif scanned:
        summary = f"最近报告数据缺口：扫描 {scanned} 份报告，未发现严重缺口"
    else:
        summary = ""

    return {
        "available": True,
        "has_blockers": has_blockers,
        "scanned_report_count": scanned,
        "affected_report_count": affected_count,
        "total_severe_blockers": total_severe,
        "summary_level": summary_level,
        "top_failed_fields": sorted_fields,
        "affected_symbols": slim_affected,
        "summary_text": summary,
    }


def _build_mandate_digest_draft(
    digest: dict[str, Any], as_of: str
) -> dict[str, Any] | None:
    """昊天日报摘要 -> 一条 P2 日报草稿（系统级，symbol 为空）.

    摘要可用（``available=True``）才生成草稿；否则返回 None。该草稿不进入
    盘中主动提醒队列（classify_delivery_channel 对 P2 返回 daily_digest）。
    """
    if not digest.get("available"):
        return None
    rising = digest.get("top_rising_topics", [])
    candidates = digest.get("top_main_candidates", [])
    gap_count = digest.get("evidence_gap_count", 0)

    reason_parts: list[str] = []
    if rising:
        topic_names = "、".join(t.get("topic", "") for t in rising if t.get("topic"))
        reason_parts.append(f"升温主题：{topic_names}")
    if candidates:
        cand_names = "、".join(
            f"{c.get('name', c.get('symbol', ''))}（{c.get('topic', '')}）"
            for c in candidates
        )
        reason_parts.append(f"主候选：{cand_names}")
    if gap_count:
        reason_parts.append(f"{gap_count} 项证据缺口待补")
    reason = "；".join(reason_parts) if reason_parts else digest.get("summary_text", "")

    return _draft(
        event_type=EVENT_MANDATE_DAILY_DIGEST,
        priority=PRIORITY_P2,
        symbol="",
        name="MANDATE",
        title=digest.get("summary_text", "昊天日报摘要") or "昊天日报摘要",
        reason=reason,
        source="mandate_daily_report",
        as_of=as_of,
        suggested_next_step="早上盘前结合主题重心与主候选安排 TA 研究队列。",
        record_only=False,
        extra={
            "report_as_of": digest.get("report_as_of", ""),
            "rising_topic_count": digest.get("rising_topic_count", 0),
            "main_candidate_count": digest.get("main_candidate_count", 0),
            "evidence_gap_count": gap_count,
        },
    )


def _build_data_blocker_digest_draft(
    digest: dict[str, Any], as_of: str
) -> dict[str, Any] | None:
    """数据缺口摘要 -> 一条 P2 record_only 日报草稿（系统级）.

    只在确实存在 severe 缺口（``has_blockers=True``）时生成草稿；扫描干净
    （``available=True`` 但 ``has_blockers=False``）不生成草稿，避免"无缺口"
    刷屏。``record_only=True``：缺口是事实陈述，不进入盘中主动提醒队列。
    """
    if not digest.get("available") or not digest.get("has_blockers"):
        return None
    fields = digest.get("top_failed_fields", [])
    field_text = "、".join(f"{f['field']}×{f['count']}" for f in fields) or "未知字段"
    affected = digest.get("affected_symbols", [])
    affected_text = "、".join(
        a.get("symbol", "") for a in affected if a.get("symbol")
    )

    return _draft(
        event_type=EVENT_DATA_BLOCKER_DIGEST,
        priority=PRIORITY_P2,
        symbol="",
        name="DATA",
        title="最近报告数据缺口摘要",
        reason=(
            f"{digest.get('summary_text', '')} 失败最多：{field_text}。"
            + (f"受影响标的：{affected_text}。" if affected_text else "")
            + "相关报告结论需人工复核，不作强结论推送。"
        ),
        source="recent_report_data_blockers",
        as_of=as_of,
        suggested_next_step="检查数据源 fallback 链路，必要时重跑受影响标的的 TA。",
        record_only=True,
        extra={
            "scanned_report_count": digest.get("scanned_report_count", 0),
            "affected_report_count": digest.get("affected_report_count", 0),
            "total_severe_blockers": digest.get("total_severe_blockers", 0),
            "summary_level": digest.get("summary_level", "ok"),
        },
    )


# ── 去噪应用 + 分通道 ─────────────────────────────────────────────────────────

def apply_dedup(
    drafts: Iterable[dict[str, Any]],
    deduplicator: NotificationDeduplicator,
    *,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """对草稿列表套去噪器，返回 emitted / deduplicated 两组."""
    now = now or datetime.now()
    emitted: list[dict[str, Any]] = []
    deduplicated: list[dict[str, Any]] = []
    for draft in drafts:
        ok, key, reason = deduplicator.should_emit(draft, now)
        if ok:
            deduplicator.mark_emitted(draft, now)
            emitted.append(draft)
        else:
            entry = dict(draft)
            entry["dedup_key"] = key
            entry["dedup_reason"] = reason
            deduplicated.append(entry)
    return {"emitted": emitted, "deduplicated": deduplicated}


def split_by_channel(
    drafts: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """按投递通道分组：intraday_push（P0/P1 非 record_only）/ daily_digest."""
    intraday: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    for d in drafts:
        if classify_delivery_channel(d) == CHANNEL_INTRADAY_PUSH:
            intraday.append(d)
        else:
            daily.append(d)
    return {CHANNEL_INTRADAY_PUSH: intraday, CHANNEL_DAILY_DIGEST: daily}


# [NOTIFY-002] notification_mandate_data_blockers
# ── 摘要区块渲染辅助 ──────────────────────────────────────────────────────────

def _render_mandate_digest_section(
    lines: list[str], digest: dict[str, Any]
) -> None:
    """渲染昊天日报摘要区块（升温主题 / 主候选 / 证据缺口）."""
    if not digest.get("available"):
        return
    lines.append("## 昊天日报摘要（mandate_daily_digest）\n")
    summary = digest.get("summary_text", "")
    if summary:
        lines.append(f"> {summary}\n")
    rising = digest.get("top_rising_topics", []) or []
    if rising:
        lines.append("- **升温主题**：")
        for t in rising:
            label = t.get("heat_trend_label") or t.get("status_label") or ""
            cnt = t.get("candidate_count", 0)
            tail = f"（{label}，{cnt} 候选）" if label else (f"（{cnt} 候选）" if cnt else "")
            lines.append(f"  - {t.get('topic', '')}{tail}")
    candidates = digest.get("top_main_candidates", []) or []
    if candidates:
        lines.append("- **主候选**：")
        for c in candidates:
            sym = c.get("symbol", "")
            name = c.get("name", "") or sym
            topic = c.get("topic", "")
            score = c.get("mandate_score")
            score_text = f" 昊天分 {score}" if score is not None else ""
            lines.append(f"  - `{sym}` {name}（{topic}）{score_text}".rstrip())
    gaps = digest.get("evidence_gaps", []) or []
    if gaps:
        lines.append(f"- **证据缺口**（{digest.get('evidence_gap_count', 0)}）：")
        for g in gaps:
            lines.append(f"  - {g}")
    lines.append("")


def _render_data_blocker_digest_section(
    lines: list[str], digest: dict[str, Any]
) -> None:
    """渲染数据缺口摘要区块（失败字段 / 受影响标的）."""
    if not digest.get("available"):
        return
    # 扫描干净时不渲染独立区块（避免"无缺口"刷屏），只在摘要已有时展示。
    if not digest.get("has_blockers"):
        return
    lines.append("## 数据缺口摘要（data_blocker_digest）\n")
    summary = digest.get("summary_text", "")
    if summary:
        lines.append(f"> {summary}\n")
    fields = digest.get("top_failed_fields", []) or []
    if fields:
        lines.append("- **失败最多字段**：")
        for f in fields:
            lines.append(f"  - {f.get('field', '')}：{f.get('count', 0)} 次")
    affected = digest.get("affected_symbols", []) or []
    if affected:
        lines.append("- **受影响标的**：")
        for a in affected:
            sym = a.get("symbol", "")
            cnt = a.get("severe_count", 0)
            flds = "、".join(a.get("fields", []) or [])
            tail = f"（{flds}）" if flds else ""
            lines.append(f"  - `{sym}` {cnt} 项缺口{tail}")
    lines.append("")


# ── 预览渲染 ──────────────────────────────────────────────────────────────────

def render_drafts_markdown(
    *,
    intraday_push: list[dict[str, Any]],
    daily_digest: list[dict[str, Any]],
    recorded_only: list[dict[str, Any]],
    deduplicated: list[dict[str, Any]],
    as_of: str,
    mandate_digest: dict[str, Any] | None = None,  # [NOTIFY-002]
    data_blocker_digest: dict[str, Any] | None = None,  # [NOTIFY-002]
) -> str:
    """渲染本地 markdown 预览，供 OpenClaw / investment-controller 决定是否发.

    NOTIFY-002: 当传入 ``mandate_digest`` / ``data_blocker_digest`` 时，在日报
    区之前渲染两个独立的摘要区块（升温主题 / 主候选 / 证据缺口 / 失败字段），
    让用户一眼看到主题重心和数据风险，而不是只看到候选数量。
    """
    lines: list[str] = [
        "# 通知草稿预览（dry-run）",
        "",
        f"- 生成时间：{as_of}",
        f"- 盘中主动提醒（P0/P1）：{len(intraday_push)} 条",
        f"- 日报（P2/P3 + 记录）：{len(daily_digest)} 条",
        f"- 仅记录（数据不足）：{len(recorded_only)} 条",
        f"- 去重抑制：{len(deduplicated)} 条",
        "",
    ]

    # [NOTIFY-002] 两个全局摘要区块（放在日报之前，突出主题重心与数据风险）
    if mandate_digest is not None:
        _render_mandate_digest_section(lines, mandate_digest)
    if data_blocker_digest is not None:
        _render_data_blocker_digest_section(lines, data_blocker_digest)

    def _section(title: str, items: list[dict[str, Any]]) -> None:
        if not items:
            lines.append(f"## {title}\n\n_（无）_\n")
            return
        lines.append(f"## {title}\n")
        for d in items:
            sym = d.get("symbol", "—")
            name = d.get("name", "")
            pri = d.get("priority", "")
            lines.append(
                f"- **[{pri}] {d.get('title', '')}** — `{sym}` {name}".rstrip()
            )
            if d.get("reason"):
                lines.append(f"  - 原因：{d['reason']}")
            if d.get("suggested_next_step"):
                lines.append(f"  - 建议下一步：{d['suggested_next_step']}")
            if d.get("record_only"):
                lines.append("  - _仅记录（数据不足，不盘中推送）_")
            if d.get("dedup_reason"):
                lines.append(f"  - 去重抑制：{d['dedup_reason']}")
        lines.append("")

    _section("盘中主动提醒队列（intraday_push）", intraday_push)
    _section("夜间日报（daily_digest）", daily_digest)
    if recorded_only:
        _section("仅记录（record_only）", recorded_only)
    if deduplicated:
        _section("去重抑制（deduplicated）", deduplicated)

    return "\n".join(lines)


def render_drafts_json(
    *,
    intraday_push: list[dict[str, Any]],
    daily_digest: list[dict[str, Any]],
    recorded_only: list[dict[str, Any]],
    deduplicated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """渲染 json 预览：返回所有草稿的扁平列表（带 channel 标注）."""
    out: list[dict[str, Any]] = []
    for d in intraday_push:
        e = dict(d); e["channel"] = CHANNEL_INTRADAY_PUSH; out.append(e)
    for d in daily_digest:
        e = dict(d); e["channel"] = CHANNEL_DAILY_DIGEST; out.append(e)
    for d in recorded_only:
        e = dict(d); e["channel"] = CHANNEL_DAILY_DIGEST; e["record_only"] = True; out.append(e)
    for d in deduplicated:
        e = dict(d); e["channel"] = "suppressed"; out.append(e)
    return out


# ── 安全自检 / 辅助 ───────────────────────────────────────────────────────────

def assert_no_forbidden_words(drafts: Iterable[dict[str, Any]]) -> None:
    """测试辅助：断言草稿集合不含强动作词（title/reason/suggested_next_step）."""
    for d in drafts:
        blob = " ".join([
            str(d.get("title", "")),
            str(d.get("reason", "")),
            str(d.get("suggested_next_step", "")),
        ])
        for word in FORBIDDEN_STRONG_WORDS:
            if word in blob:
                raise AssertionError(
                    f"通知草稿含强动作词 {word!r}：{d.get('event_type')} {d.get('symbol')}"
                )


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def to_json_str(payload: dict[str, Any]) -> str:
    """稳定序列化（ensure_ascii=False，便于中文预览）."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=False, default=str)


__all__ = [
    "ALLOWED_PRIORITIES",
    "CHANNEL_DAILY_DIGEST",
    "CHANNEL_INTRADAY_PUSH",
    "DEDUP_WINDOW_SECONDS",
    "EVENT_DATA_SOURCE_FAILURE",
    "EVENT_DATA_STALE",
    "EVENT_DATA_BLOCKER_DIGEST",  # [NOTIFY-002]
    "EVENT_MANDATE_DAILY_DIGEST",  # [NOTIFY-002]
    "EVENT_HOLDINGS_NO_ANALYSIS",
    "EVENT_HOLDINGS_RISK_LARGE",
    "EVENT_HOLDINGS_RISK_MODERATE",
    "EVENT_OBSERVATION_DATA_MISSING",
    "EVENT_OBSERVATION_IN_ENTRY_ZONE",
    "EVENT_OBSERVATION_INVALIDATED",
    "EVENT_OBSERVATION_MISSED_ENTRY",
    "EVENT_OBSERVATION_NEAR_ENTRY",
    "EVENT_OBSERVATION_NEEDS_REVIEW",
    "EVENT_OBSERVATION_TA_REQUIRED",
    "EVENT_OBSERVATION_WATCHING",
    "EVENT_PENDING_TA_REQUIRED",
    "FORBIDDEN_STRONG_WORDS",
    "NOTIFY_SCHEMA_VERSION",
    "NOTIFY_SOURCE",
    "PRIORITY_P0",
    "PRIORITY_P1",
    "PRIORITY_P2",
    "PRIORITY_P3",
    "NotificationDeduplicator",
    "apply_dedup",
    "assert_no_forbidden_words",
    "build_data_blocker_digest",  # [NOTIFY-002]
    "build_mandate_daily_digest",  # [NOTIFY-002]
    "build_notification_drafts_from_context",
    "classify_delivery_channel",
    "render_drafts_json",
    "render_drafts_markdown",
    "split_by_channel",
    "to_json_str",
]
