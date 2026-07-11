# [HY-007] controller_half_year_briefing
"""Tests for investment-controller 半年报 briefing payload 与去噪规则 (HY-007).

覆盖（对应 docs/TASKS.md HY-007 验收方式）：
  - **bucket 10 ``half_year_facts``**：持仓 / 观察仓 / 候选池三类 origin 都能
    被扫描；fixture 覆盖（HY-003 query + HY-005 thesis check + HY-006 score
    全链路）。
  - **briefing payload 三类提醒**：fact_update / rebuttal_alert /
    needs_ta_review 分类与 priority 正确；contradicted + priority_reminder 才
    进 P1（intraday_push），其余 P2 daily_digest。
  - **去噪规则**：同一 symbol 同一事实 24 小时内去重；不同事实 / 超过 24h /
    不同 reminder_type 不去重。
  - **缺数据只记录不推送**：FAILED/NO_DATA 不进 items（避免"没有半年报"刷屏）；
    知识库 disabled/skipped 时 bucket 退化为稳定空结构。
  - **payload 不含长原文与强动作词**：所有文本字段已 pre-clip；引擎自检
    ``scan_forbidden_words`` 与 ``assert_no_strong_action_verbs`` 通过。
  - **stable empty structure**：无 knowledge_root / 无 symbols / 全部查询失败
    时 bucket 返回 ``data_status=skipped/failed/missing``，永不抛异常。
  - **不写 DB / 不调 LLM**：纯函数，复用 HY-003 fixture 知识库。
  - **controller_hints.half_year_alerts lane** 与 bucket 10 items 联动正确。
  - **与 IC-TA-004 三场景兼容**：pre_market / post_market 输出
    half_year_reminders；intraday 保持空（盘中不调度新 TA 的语义不变）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from api.services.investment_controller_context import (
    _build_half_year_alert_lane,
    _build_half_year_symbol_universe,
)
from tradingagents.tradeflow.controller_briefing_payload import (
    ALLOWED_HALF_YEAR_REMINDER_TYPES,
    HALF_YEAR_DEDUP_WINDOW_SECONDS,
    HALF_YEAR_REMINDER_FACT_UPDATE,
    HALF_YEAR_REMINDER_NEEDS_TA_REVIEW,
    HALF_YEAR_REMINDER_REBUTTAL_ALERT,
    NOTIFY_LEVEL_DAILY_DIGEST,
    NOTIFY_LEVEL_INTRADAY_PUSH,
    HalfYearReminderDeduplicator,
    apply_half_year_dedup,
    build_half_year_reminders,
    build_intraday_payload,
    build_post_market_payload,
    build_pre_market_payload,
)
from tradingagents.tradeflow.notification_draft import (
    CHANNEL_DAILY_DIGEST,
    CHANNEL_INTRADAY_PUSH,
)

# ── helpers / fixtures ────────────────────────────────────────────────

_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "立即买入", "立即卖出",
    "全仓", "满仓", "清仓", "止损", "建仓", "强烈推荐",
    "BUY", "SELL", "strong buy", "strong sell",
)


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, (
            f"text contains forbidden word {forbidden!r}: {text!r}"
        )


def _hy_item(
    *,
    symbol: str = "300750.SZ",
    name: str = "宁德时代",
    origin: str = "holdings",
    facts_status: str = "HAS_FACTS",
    latest_period: str = "2025H1",
    data_status: str = "fresh",
    has_conflict: bool = False,
    has_stale: bool = False,
    half_year_score: float = 0.0,
    fact_summary_text: str = "2025H1 储能营收同比 -20.0%",
    thesis_check_status: str = "",
    thesis_inline: str = "",
    needs_tree_work_review: bool = False,
    needs_research_review: bool = False,
    priority_reminder: bool = False,
    downgrade_reasons: list[str] | None = None,
    risk_preview: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "origin": origin,
        "facts_status": facts_status,
        "latest_period": latest_period,
        "latest_disclosure_date": "2026-08-28",
        "data_status": data_status,
        "has_conflict": has_conflict,
        "has_stale": has_stale,
        "half_year_score": half_year_score,
        "fact_summary_text": fact_summary_text,
        "thesis_check_status": thesis_check_status,
        "thesis_inline": thesis_inline,
        "needs_tree_work_review": needs_tree_work_review,
        "needs_research_review": needs_research_review,
        "priority_reminder": priority_reminder,
        "downgrade_reasons": downgrade_reasons or [],
        "risk_preview": risk_preview or [],
        "source": "half_year_facts_provider",
        "as_of": "2026-07-12 09:30:00",
    }


def _hy_bucket(items: list[dict[str, Any]], *, data_status: str = "fresh") -> dict[str, Any]:
    return {
        "source": "half_year_facts_context",
        "as_of": "2026-07-12 09:30:00",
        "data_status": data_status,
        "knowledge_root": "",
        "symbol_count": len(items),
        "scanned_symbol_count": max(len(items), 1),
        "fresh_fact_count": sum(1 for i in items if i.get("data_status") == "fresh"),
        "contradicted_count": sum(
            1 for i in items if i.get("thesis_check_status") == "contradicted"
        ),
        "weakened_count": sum(
            1 for i in items if i.get("thesis_check_status") == "weakened"
        ),
        "needs_review_count": sum(
            1 for i in items
            if i.get("needs_tree_work_review") or i.get("needs_research_review")
        ),
        "items": items,
        "errors": [],
        "read_only": True,
    }


def _ctx_with_half_year(
    items: list[dict[str, Any]],
    *,
    holdings_items: list[dict[str, Any]] | None = None,
    observation_items: list[dict[str, Any]] | None = None,
    candidates_items: list[dict[str, Any]] | None = None,
    bucket_data_status: str = "fresh",
) -> dict[str, Any]:
    """构造一个最小可用的 IC context，包含 bucket 10 items."""
    return {
        "schema_version": "1.0",
        "as_of": "2026-07-12 09:30:00",
        "previous_trade_date": "2026-07-11",
        "is_trading_day": True,
        "generated_by": "investment_controller_context",
        "read_only": True,
        "holdings": {
            "source": "imported_portfolio",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "fresh",
            "count": len(holdings_items or []),
            "items": holdings_items or [],
        },
        "observation_warehouse": {
            "source": "observation_warehouse",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "fresh",
            "count": len(observation_items or []),
            "items": observation_items or [],
        },
        "tradeflow_candidates": {
            "source": "tradeflow_candidates",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "fresh",
            "count": len(candidates_items or []),
            "items": candidates_items or [],
        },
        "latest_ta_reports": {
            "source": "ta_report",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "fresh",
            "count": 0,
            "items": [],
        },
        "data_health": {
            "source": "tradeflow_data_health",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "fresh",
            "tradeflow_db_available": True,
            "sources": [],
        },
        "pending_ta_required": {
            "source": "pending_ta_required",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "fresh",
            "count": 0,
            "items": [],
        },
        "mandate_daily_report": {
            "source": "mandate_daily_report",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "missing",
            "rising_topics": [],
            "cooling_topics": [],
            "main_candidates": [],
            "observation_candidates": [],
            "evidence_gaps": [],
        },
        "recent_report_data_blockers": {
            "source": "recent_report_data_blockers",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "fresh",
            "scanned_report_count": 0,
            "affected_symbols": [],
            "summary_level": "ok",
            "field_counts": {},
            "total_severe_blockers": 0,
        },
        "local_knowledge_hits": {
            "source": "local_knowledge_context",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "missing",
            "items": [],
        },
        "half_year_facts": _hy_bucket(items, data_status=bucket_data_status),
        "controller_hints": {
            "source": "controller_hints",
            "as_of": "2026-07-12 09:30:00",
            "data_status": "fresh",
            "needs_ta": [],
            "daily_report_only": [],
            "suppress_push_data_insufficient": [],
            "research_review": [],
            "half_year_alerts": _build_half_year_alert_lane(
                _hy_bucket(items), "2026-07-12 09:30:00"
            ),
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


# ════════════════════════════════════════════════════════════════════
# 1. build_half_year_reminders — 分类与 priority
# ════════════════════════════════════════════════════════════════════


class TestBuildHalfYearReminders:
    """build_half_year_reminders 三类分类与 notify_level."""

    def test_empty_bucket_returns_empty(self):
        ctx = _ctx_with_half_year([])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        assert out == []

    def test_fact_update_classification(self):
        # 有 fresh facts，无反证 / 无 needs_review → fact_update / P2 / daily
        item = _hy_item(
            symbol="000977.SZ",
            thesis_check_status="",
            fact_summary_text="2025H1 营收 150.2亿",
        )
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        assert len(out) == 1
        r = out[0]
        assert r["reminder_type"] == HALF_YEAR_REMINDER_FACT_UPDATE
        assert r["priority"] == "P2"
        assert r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST
        assert r["record_only"] is False
        assert r["symbol"] == "000977.SZ"
        assert "2025H1" in r["reason"]
        _assert_no_strong_action_words(r["reason"])

    def test_rebuttal_alert_contradicted_with_priority_reminder_is_p1(self):
        # contradicted + priority_reminder → P1 intraday_push
        item = _hy_item(
            symbol="300750.SZ",
            thesis_check_status="contradicted",
            priority_reminder=True,
            thesis_inline="[HY-005] 2 条观点被事实打脸（优先提醒）。",
            half_year_score=-3.0,
        )
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        assert len(out) == 1
        r = out[0]
        assert r["reminder_type"] == HALF_YEAR_REMINDER_REBUTTAL_ALERT
        assert r["priority"] == "P1"
        assert r["notify_level"] == NOTIFY_LEVEL_INTRADAY_PUSH
        assert r["record_only"] is False
        _assert_no_strong_action_words(r["reason"])

    def test_rebuttal_alert_contradicted_without_priority_is_p2(self):
        # contradicted 但无 priority_reminder → 仍然 P2 daily_digest（只日报）
        item = _hy_item(
            symbol="300750.SZ",
            thesis_check_status="contradicted",
            priority_reminder=False,
            thesis_inline="[HY-005] 1 条观点被事实打脸。",
            half_year_score=-2.0,
        )
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        r = out[0]
        assert r["reminder_type"] == HALF_YEAR_REMINDER_REBUTTAL_ALERT
        assert r["priority"] == "P2"
        assert r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST

    def test_rebuttal_alert_weakened_is_p2(self):
        # weakened → P2 daily_digest（仅 contradicted + priority 才进 P1）
        item = _hy_item(
            symbol="002415.SZ",
            thesis_check_status="weakened",
            priority_reminder=True,
            thesis_inline="[HY-005] 1 条被削弱（优先提醒）。",
            half_year_score=-1.0,
        )
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        r = out[0]
        assert r["reminder_type"] == HALF_YEAR_REMINDER_REBUTTAL_ALERT
        assert r["priority"] == "P2"
        assert r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST

    def test_needs_ta_review_classification(self):
        # needs_tree_work_review 但 thesis_check_status 为 supported →
        # 走 needs_ta_review（rebuttal 优先级最高，但 supported 不触发 rebuttal；
        # needs_review 是第二优先级）
        item = _hy_item(
            symbol="000977.SZ",
            thesis_check_status="supported",
            needs_tree_work_review=True,
            half_year_score=1.0,
        )
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        r = out[0]
        assert r["reminder_type"] == HALF_YEAR_REMINDER_NEEDS_TA_REVIEW
        assert r["priority"] == "P2"
        assert r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST

    def test_needs_research_review_classification(self):
        item = _hy_item(
            symbol="000977.SZ",
            thesis_check_status="",
            needs_research_review=True,
            half_year_score=0.0,
            fact_summary_text="无半年报事实（mandate 候选）",
        )
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        r = out[0]
        assert r["reminder_type"] == HALF_YEAR_REMINDER_NEEDS_TA_REVIEW
        assert r["priority"] == "P2"
        assert r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST

    def test_rebuttal_takes_priority_over_needs_review(self):
        # 同时 contradicted + needs_review → 走 rebuttal_alert
        item = _hy_item(
            symbol="300750.SZ",
            thesis_check_status="contradicted",
            priority_reminder=True,
            needs_tree_work_review=True,
            needs_research_review=True,
        )
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        assert len(out) == 1
        assert out[0]["reminder_type"] == HALF_YEAR_REMINDER_REBUTTAL_ALERT
        assert out[0]["priority"] == "P1"

    def test_missing_data_status_record_only(self):
        # data_status=missing → record_only=True（只记录，不推送）
        item = _hy_item(
            symbol="000977.SZ",
            data_status="missing",
            facts_status="NO_DATA",
            needs_research_review=True,
            thesis_check_status="",
        )
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        r = out[0]
        assert r["record_only"] is True
        # record_only + P2 → daily_digest
        assert r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST

    def test_dedup_key_includes_period_and_thesis_status(self):
        # 同 symbol 不同 period 的 key 不同；不同 thesis_status 的 key 不同
        item_a = _hy_item(symbol="300750.SZ", latest_period="2025H1",
                          thesis_check_status="contradicted",
                          priority_reminder=True, half_year_score=-3.0)
        item_b = _hy_item(symbol="300750.SZ", latest_period="2024H1",
                          thesis_check_status="contradicted",
                          priority_reminder=True, half_year_score=-3.0)
        item_c = _hy_item(symbol="300750.SZ", latest_period="2025H1",
                          thesis_check_status="weakened",
                          half_year_score=-1.0)
        ctx = _ctx_with_half_year([item_a, item_b, item_c])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        keys = {r["dedup_key"] for r in out}
        assert len(keys) == 3, f"dedup keys should be unique: {keys}"

    def test_reason_no_long_text(self):
        # reason 必须 <=200 字符（briefing-friendly）
        long_summary = "x" * 500
        item = _hy_item(fact_summary_text=long_summary)
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        assert len(out[0]["reason"]) <= 200

    def test_reminder_schema_fields(self):
        item = _hy_item()
        ctx = _ctx_with_half_year([item])
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        r = out[0]
        for key in (
            "reminder_type", "symbol", "name", "origin", "reason",
            "priority", "notify_level", "record_only", "facts_status",
            "thesis_check_status", "latest_period", "half_year_score",
            "dedup_key", "source", "as_of",
        ):
            assert key in r, f"missing key {key!r} in reminder"

    def test_reminder_types_in_allowed_set(self):
        items = [
            _hy_item(
                symbol="A.SZ", thesis_check_status="contradicted",
                priority_reminder=True, half_year_score=-3.0,
                thesis_inline="[HY-005] 打脸（优先提醒）",
            ),
            _hy_item(
                symbol="B.SZ", needs_research_review=True, half_year_score=0.0,
                thesis_check_status="", fact_summary_text="mandate 候选无半年报",
            ),
            _hy_item(
                symbol="C.SZ", thesis_check_status="", half_year_score=1.0,
                fact_summary_text="2025H1 营收 +30%",
            ),
        ]
        ctx = _ctx_with_half_year(items)
        out = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        assert len(out) == 3
        for r in out:
            assert r["reminder_type"] in ALLOWED_HALF_YEAR_REMINDER_TYPES


# ════════════════════════════════════════════════════════════════════
# 2. HalfYearReminderDeduplicator — 24h 去噪
# ════════════════════════════════════════════════════════════════════


class TestHalfYearDeduplicator:
    """24h dedup rule (HY-007 执行约束 3)."""

    def test_window_is_24_hours(self):
        assert HALF_YEAR_DEDUP_WINDOW_SECONDS == 24 * 60 * 60

    def test_same_key_within_24h_suppressed(self):
        d = HalfYearReminderDeduplicator()
        now = datetime(2026, 7, 12, 9, 30, 0)
        reminder = {"dedup_key": "300750.SZ|half_year_rebuttal_alert|2025H1|contradicted|strong_negative"}
        ok1, _, reason1 = d.should_emit(reminder, now)
        assert ok1 is True
        assert reason1 == ""
        d.mark_emitted(reminder, now)
        # 1 hour later — same key → suppressed
        ok2, _, reason2 = d.should_emit(reminder, now + timedelta(hours=1))
        assert ok2 is False
        assert "已提醒过" in reason2
        assert "24 小时" in reason2

    def test_after_24h_emits_again(self):
        d = HalfYearReminderDeduplicator()
        now = datetime(2026, 7, 12, 9, 30, 0)
        reminder = {"dedup_key": "300750.SZ|half_year_fact_update|2025H1||positive"}
        d.mark_emitted(reminder, now)
        # 24h + 1 minute later → emit again
        ok, _, reason = d.should_emit(reminder, now + timedelta(hours=24, minutes=1))
        assert ok is True
        assert reason == ""

    def test_different_period_same_symbol_emits(self):
        # 同 symbol 但不同 period（事实变了）→ 视为新事件
        d = HalfYearReminderDeduplicator()
        now = datetime(2026, 7, 12, 9, 30, 0)
        r1 = {"dedup_key": "300750.SZ|half_year_fact_update|2025H1||positive"}
        r2 = {"dedup_key": "300750.SZ|half_year_fact_update|2024H1||positive"}
        d.mark_emitted(r1, now)
        ok, _, _ = d.should_emit(r2, now)
        assert ok is True

    def test_different_thesis_status_emits(self):
        # 同 symbol + 同 period 但 thesis_status 从 weakened 变 contradicted → emit
        d = HalfYearReminderDeduplicator()
        now = datetime(2026, 7, 12, 9, 30, 0)
        r1 = {"dedup_key": "300750.SZ|half_year_rebuttal_alert|2025H1|weakened|negative"}
        r2 = {"dedup_key": "300750.SZ|half_year_rebuttal_alert|2025H1|contradicted|strong_negative"}
        d.mark_emitted(r1, now)
        ok, _, _ = d.should_emit(r2, now)
        assert ok is True

    def test_different_reminder_type_emits(self):
        # 同 symbol + 同 period + 同 thesis_status 但 reminder_type 不同 → emit
        d = HalfYearReminderDeduplicator()
        now = datetime(2026, 7, 12, 9, 30, 0)
        r1 = {"dedup_key": "300750.SZ|half_year_fact_update|2025H1||positive"}
        r2 = {"dedup_key": "300750.SZ|half_year_needs_ta_review|2025H1||positive"}
        d.mark_emitted(r1, now)
        ok, _, _ = d.should_emit(r2, now)
        assert ok is True

    def test_apply_half_year_dedup_partitions_correctly(self):
        d = HalfYearReminderDeduplicator()
        now = datetime(2026, 7, 12, 9, 30, 0)
        reminders = [
            {"dedup_key": "A|half_year_fact_update|2025H1||positive", "symbol": "A"},
            {"dedup_key": "A|half_year_fact_update|2025H1||positive", "symbol": "A"},  # dup
            {"dedup_key": "B|half_year_rebuttal_alert|2025H1|contradicted|strong_negative", "symbol": "B"},
        ]
        out = apply_half_year_dedup(reminders, d, now=now)
        assert len(out["emitted"]) == 2
        assert len(out["deduplicated"]) == 1
        assert "dedup_reason" in out["deduplicated"][0]
        assert out["deduplicated"][0]["symbol"] == "A"

    def test_reset_clears_state(self):
        d = HalfYearReminderDeduplicator()
        now = datetime(2026, 7, 12, 9, 30, 0)
        r = {"dedup_key": "A|half_year_fact_update|2025H1||positive"}
        d.mark_emitted(r, now)
        d.reset()
        ok, _, _ = d.should_emit(r, now)
        assert ok is True

    def test_dedup_key_fallback_when_missing(self):
        # 没有 dedup_key 字段时，按 symbol + reminder_type 兜底
        d = HalfYearReminderDeduplicator()
        reminder = {"symbol": "000977.SZ", "reminder_type": "half_year_fact_update"}
        key = d.dedup_key(reminder)
        assert key == "000977.SZ|half_year_fact_update"


# ════════════════════════════════════════════════════════════════════
# 3. 三场景 briefing payload
# ════════════════════════════════════════════════════════════════════


class TestScenePayloads:
    """pre_market / post_market 输出 half_year_reminders；intraday 保持空."""

    def test_pre_market_includes_half_year_reminders(self):
        items = [
            _hy_item(symbol="300750.SZ", thesis_check_status="contradicted",
                     priority_reminder=True, half_year_score=-3.0),
            _hy_item(symbol="000977.SZ", thesis_check_status="",
                     half_year_score=1.0, fact_summary_text="2025H1 营收 +30%"),
        ]
        ctx = _ctx_with_half_year(items)
        payload = build_pre_market_payload(ctx, as_of="2026-07-12 09:30:00")
        assert "half_year_reminders" in payload
        reminders = payload["half_year_reminders"]
        assert len(reminders) == 2
        types = {r["reminder_type"] for r in reminders}
        assert HALF_YEAR_REMINDER_REBUTTAL_ALERT in types
        assert HALF_YEAR_REMINDER_FACT_UPDATE in types
        # notify_level_summary 包含 half_year 计数
        notify = payload["notify_level"]
        push = notify["intraday_push_count"]
        digest = notify["daily_digest_count"]
        # 1 P1 (contradicted+priority) + 1 P2 (fact_update)
        assert push >= 1
        assert digest >= 1

    def test_post_market_includes_half_year_reminders(self):
        items = [
            _hy_item(symbol="300750.SZ", needs_research_review=True,
                     half_year_score=0.0, thesis_check_status="",
                     fact_summary_text="无半年报，需 TA 复核"),
        ]
        ctx = _ctx_with_half_year(items)
        payload = build_post_market_payload(ctx, as_of="2026-07-12 16:00:00")
        assert len(payload["half_year_reminders"]) == 1
        assert payload["half_year_reminders"][0]["reminder_type"] == HALF_YEAR_REMINDER_NEEDS_TA_REVIEW

    def test_intraday_keeps_half_year_reminders_empty(self):
        # 盘中不调度新事项（含半年报提醒），保持 IC-TA-004 的契约
        items = [_hy_item(symbol="300750.SZ")]
        ctx = _ctx_with_half_year(items)
        payload = build_intraday_payload(ctx, as_of="2026-07-12 10:30:00")
        assert payload["half_year_reminders"] == []

    def test_forbidden_word_scan_passes(self):
        items = [
            _hy_item(symbol="300750.SZ", thesis_check_status="contradicted",
                     priority_reminder=True, half_year_score=-3.0,
                     thesis_inline="[HY-005] 2 条观点被事实打脸（优先提醒）。"),
        ]
        ctx = _ctx_with_half_year(items)
        for builder, as_of in [
            (build_pre_market_payload, "2026-07-12 09:30:00"),
            (build_post_market_payload, "2026-07-12 16:00:00"),
        ]:
            payload = builder(ctx, as_of=as_of)
            scan = payload["forbidden_word_scan"]
            assert scan["found"] is False, (
                f"forbidden words in {builder.__name__}: {scan.get('hits')}"
            )

    def test_markdown_preview_includes_half_year_section(self):
        items = [
            _hy_item(symbol="300750.SZ", thesis_check_status="contradicted",
                     priority_reminder=True, half_year_score=-3.0,
                     thesis_inline="[HY-005] 2 条观点被事实打脸。"),
        ]
        ctx = _ctx_with_half_year(items)
        payload = build_pre_market_payload(ctx, as_of="2026-07-12 09:30:00")
        md = payload["markdown_preview"]
        assert "半年报提醒" in md
        assert "300750.SZ" in md
        assert "反证提醒" in md

    def test_markdown_preview_omits_section_when_empty(self):
        ctx = _ctx_with_half_year([])
        payload = build_pre_market_payload(ctx, as_of="2026-07-12 09:30:00")
        md = payload["markdown_preview"]
        # 无提醒时不渲染独立区块（避免"无半年报提醒"刷屏）
        assert "半年报提醒" not in md

    def test_summary_headline_includes_half_year_count(self):
        items = [_hy_item(symbol="A.SZ"), _hy_item(symbol="B.SZ")]
        ctx = _ctx_with_half_year(items)
        payload = build_pre_market_payload(ctx, as_of="2026-07-12 09:30:00")
        assert "半年报提醒" in payload["summary"]


# ════════════════════════════════════════════════════════════════════
# 4. controller_hints.half_year_alerts lane
# ════════════════════════════════════════════════════════════════════


class TestControllerHintsLane:
    """controller_hints.half_year_alerts 与 bucket 10 items 联动."""

    def test_empty_bucket_yields_empty_lane(self):
        alerts = _build_half_year_alert_lane(_hy_bucket([]), "2026-07-12 09:30:00")
        assert alerts == []

    def test_rebuttal_alert_kind(self):
        bucket = _hy_bucket([
            _hy_item(symbol="300750.SZ", thesis_check_status="contradicted",
                     priority_reminder=True, half_year_score=-3.0,
                     thesis_inline="[HY-005] 反证提醒"),
        ])
        alerts = _build_half_year_alert_lane(bucket, "2026-07-12 09:30:00")
        assert len(alerts) == 1
        assert alerts[0]["kind"] == "rebuttal_alert"
        assert alerts[0]["symbol"] == "300750.SZ"
        _assert_no_strong_action_words(alerts[0]["reason"])
        _assert_no_strong_action_words(alerts[0]["suggested_next_step"])

    def test_needs_ta_review_kind(self):
        bucket = _hy_bucket([
            _hy_item(symbol="A.SZ", needs_research_review=True, thesis_check_status=""),
        ])
        alerts = _build_half_year_alert_lane(bucket, "2026-07-12 09:30:00")
        assert alerts[0]["kind"] == "needs_ta_review"

    def test_fact_update_kind(self):
        bucket = _hy_bucket([
            _hy_item(symbol="A.SZ", thesis_check_status="", half_year_score=1.0),
        ])
        alerts = _build_half_year_alert_lane(bucket, "2026-07-12 09:30:00")
        assert alerts[0]["kind"] == "fact_update"


# ════════════════════════════════════════════════════════════════════
# 5. _build_half_year_symbol_universe — origin priority & cap
# ════════════════════════════════════════════════════════════════════


class TestSymbolUniverse:
    """Symbol universe construction (origin priority + caps)."""

    def test_holdings_take_priority_over_observation(self):
        holdings = {"items": [{"symbol": "A.SZ"}, {"symbol": "B.SZ"}]}
        observation = {"items": [{"symbol": "A.SZ"}, {"symbol": "C.SZ"}]}
        candidates = {"items": []}
        mandate = {"data_status": "missing"}
        symbols, origins = _build_half_year_symbol_universe(
            holdings=holdings, observation=observation,
            candidates=candidates, mandate_report=mandate,
        )
        assert "A.SZ" in symbols
        assert origins["A.SZ"] == "holdings"  # 优先归 holdings
        assert origins["B.SZ"] == "holdings"
        assert origins["C.SZ"] == "observation_warehouse"

    def test_cap_enforced(self):
        # 超过 _HALF_YEAR_PER_ORIGIN_CAP * 4 不会越界
        many = [{"symbol": f"H{i:03d}.SZ"} for i in range(30)]
        holdings = {"items": many}
        observation = {"items": []}
        candidates = {"items": []}
        mandate = {"data_status": "missing"}
        symbols, _ = _build_half_year_symbol_universe(
            holdings=holdings, observation=observation,
            candidates=candidates, mandate_report=mandate,
        )
        # cap = _HALF_YEAR_SYMBOL_CAP (10), 单 origin cap = 6
        assert len(symbols) <= 10
        assert len(symbols) <= 6  # holdings 单 origin cap = 6


# ════════════════════════════════════════════════════════════════════
# 6. _collect_half_year_facts — 端到端集成（fixture KB）
# ════════════════════════════════════════════════════════════════════


class TestCollectHalfYearFactsFixture:
    """真实 HY-003 fixture KB → bucket 10 → briefing reminders 全链路."""

    def test_disabled_env_yields_skipped_bucket(self, monkeypatch):
        from api.services import investment_controller_context as icc
        monkeypatch.setenv("KNOWLEDGE_CONTEXT_DISABLED", "1")
        bucket = icc._collect_half_year_facts(
            "2026-07-12 09:30:00",
            holdings={"items": [{"symbol": "300750.SZ", "name": "宁德时代"}]},
            observation={"items": []},
            candidates={"items": []},
            mandate_report={"data_status": "missing"},
            notes=[],
        )
        assert bucket["data_status"] == "skipped"
        assert bucket["items"] == []
        assert bucket["symbol_count"] == 0

    def test_no_symbols_yields_skipped_bucket(self):
        from api.services import investment_controller_context as icc
        bucket = icc._collect_half_year_facts(
            "2026-07-12 09:30:00",
            holdings={"items": []},
            observation={"items": []},
            candidates={"items": []},
            mandate_report={"data_status": "missing"},
            notes=[],
        )
        assert bucket["data_status"] == "skipped"
        assert bucket["items"] == []

    def test_full_chain_with_fixture_kb(self, tmp_path: Path):
        """端到端：fixture KB → bucket 10 → briefing 三类提醒.

        使用 half_year_fixtures 的 weakened_old_opinion 样本（宁德时代，
        HY-005 反证基线）+ qualified 样本（浪潮信息，事实支持）。
        """
        from tests.half_year_fixtures import build_half_year_fixture_kb
        from api.services import investment_controller_context as icc
        from api.services.local_knowledge_context_service import (
            resolve_knowledge_root,
        )

        kb_root = str(build_half_year_fixture_kb(
            tmp_path, include=["weakened_old_opinion", "qualified"],
        ))

        holdings = {
            "items": [{"symbol": "300750.SZ", "name": "宁德时代"}],
        }
        observation = {
            "items": [{"symbol": "000977.SZ", "name": "浪潮信息"}],
        }
        # 用 monkey knowledge_root 注入：通过临时 env 变量
        import os
        old_root_env = os.environ.get("KNOWLEDGE_ROOT")
        os.environ["KNOWLEDGE_ROOT"] = kb_root
        try:
            # 验证 root 解析正确
            assert resolve_knowledge_root() == kb_root
            notes: list[str] = []
            bucket = icc._collect_half_year_facts(
                "2026-07-12 09:30:00",
                holdings=holdings,
                observation=observation,
                candidates={"items": []},
                mandate_report={"data_status": "missing"},
                notes=notes,
            )
        finally:
            if old_root_env is None:
                os.environ.pop("KNOWLEDGE_ROOT", None)
            else:
                os.environ["KNOWLEDGE_ROOT"] = old_root_env

        # 至少有一项（两个 fixture 都有 fresh facts）。
        assert bucket["data_status"] in ("fresh", "stale", "missing"), (
            f"unexpected data_status {bucket['data_status']}; notes={notes}; "
            f"errors={bucket.get('errors')}"
        )
        symbols_in_items = {it["symbol"] for it in bucket["items"]}
        # 宁德时代在 fixture 中有 contradicted/weakened thesis（储能 -20% YoY
        # vs 旧券商"爆发式增长"观点）；浪潮信息为 golden sample（事实支持）。
        assert "300750.SZ" in symbols_in_items or "000977.SZ" in symbols_in_items, (
            f"expected at least one fixture symbol in items; got {symbols_in_items}; "
            f"notes={notes}; errors={bucket.get('errors')}"
        )

        # 生成 briefing reminders，验证三类至少出现一类
        ctx = _ctx_with_half_year(
            bucket["items"],
            holdings_items=holdings["items"],
            observation_items=observation["items"],
            bucket_data_status=bucket["data_status"],
        )
        reminders = build_half_year_reminders(ctx, "2026-07-12 09:30:00")
        types = {r["reminder_type"] for r in reminders}
        assert types.issubset(set(ALLOWED_HALF_YEAR_REMINDER_TYPES))
        # 至少有一项提醒
        assert len(reminders) >= 1
        # 无强动作词
        for r in reminders:
            _assert_no_strong_action_words(r["reason"])
            _assert_no_strong_action_words(r.get("thesis_inline", ""))

    def test_no_strong_action_verbs_in_full_context(self, tmp_path: Path):
        """对 assert_no_strong_action_verbs 的回归 — bucket 10 字段全通过."""
        from api.services.investment_controller_context import (
            assert_no_strong_action_verbs,
        )
        items = [
            _hy_item(symbol="A.SZ", thesis_check_status="contradicted",
                     priority_reminder=True, half_year_score=-3.0,
                     thesis_inline="[HY-005] 反证提醒（优先提醒）",
                     downgrade_reasons=["半年报事实打脸投资逻辑（营收）"],
                     risk_preview=["储能增速不及预期"]),
        ]
        payload = _ctx_with_half_year(items)
        # 不抛异常即通过
        assert_no_strong_action_verbs(payload)


# ════════════════════════════════════════════════════════════════════
# 7. 数据契约稳定性 / 边界
# ════════════════════════════════════════════════════════════════════


class TestBucketStability:
    """bucket 10 在异常路径下的稳定空结构."""

    def test_empty_bucket_shape(self):
        from api.services.investment_controller_context import (
            _empty_half_year_bucket,
        )
        bucket = _empty_half_year_bucket("2026-07-12 09:30:00", "skipped")
        for key in (
            "source", "as_of", "data_status", "knowledge_root",
            "symbol_count", "fresh_fact_count", "contradicted_count",
            "weakened_count", "needs_review_count", "items", "errors",
            "read_only",
        ):
            assert key in bucket
        assert bucket["items"] == []
        assert bucket["symbol_count"] == 0
        assert bucket["read_only"] is True

    def test_no_data_does_not_become_noise(self):
        # 纯 NO_DATA/FAILED item 不会进 items（避免"没有半年报"刷屏）
        from api.services.investment_controller_context import (
            _collect_half_year_facts,
        )
        # 故意传一个没有 fixture 的 root（KB 为空）
        bucket = _collect_half_year_facts(
            "2026-07-12 09:30:00",
            holdings={"items": [{"symbol": "999999.SZ", "name": "虚空公司"}]},
            observation={"items": []},
            candidates={"items": []},
            mandate_report={"data_status": "missing"},
            notes=[],
        )
        # 没有任何 fresh facts / rebuttal / needs_review → items 为空
        assert bucket["items"] == []
        # data_status 为 skipped（默认 root 不可用）/ missing（扫过但无命中）
        assert bucket["data_status"] in ("skipped", "missing", "failed")


# ════════════════════════════════════════════════════════════════════
# 8. 联动：bucket 10 + briefing + denoise 全链路
# ════════════════════════════════════════════════════════════════════


class TestEndToEndPipeline:
    """bucket 10 → briefing reminders → 24h dedup 全链路."""

    def test_pre_market_payload_then_dedup(self):
        items = [
            _hy_item(symbol="300750.SZ", thesis_check_status="contradicted",
                     priority_reminder=True, half_year_score=-3.0,
                     thesis_inline="[HY-005] 2 条观点被事实打脸（优先提醒）。"),
            _hy_item(symbol="000977.SZ", thesis_check_status="",
                     half_year_score=1.0, fact_summary_text="2025H1 营收 +30%"),
            _hy_item(symbol="002415.SZ", needs_research_review=True,
                     half_year_score=0.0, thesis_check_status="",
                     fact_summary_text="mandate 候选无半年报"),
        ]
        ctx = _ctx_with_half_year(items)
        payload = build_pre_market_payload(ctx, as_of="2026-07-12 09:30:00")
        reminders = payload["half_year_reminders"]
        assert len(reminders) == 3

        # 第一次去噪：全部通过
        d = HalfYearReminderDeduplicator()
        out1 = apply_half_year_dedup(reminders, d, now=datetime(2026, 7, 12, 9, 30, 0))
        assert len(out1["emitted"]) == 3
        assert len(out1["deduplicated"]) == 0

        # 第二次（同一天）：全部被去噪
        out2 = apply_half_year_dedup(reminders, d, now=datetime(2026, 7, 12, 16, 0, 0))
        assert len(out2["emitted"]) == 0
        assert len(out2["deduplicated"]) == 3

        # 次日（>24h）：全部重新放行
        out3 = apply_half_year_dedup(reminders, d, now=datetime(2026, 7, 13, 10, 0, 0))
        assert len(out3["emitted"]) == 3

    def test_priority_distribution_for_intraday_push(self):
        # 验收：P2/P3 只进日报；只有 contradicted + priority_reminder 才进 intraday_push
        items = [
            # 1 项 P1（contradicted + priority_reminder）
            _hy_item(symbol="P1.SZ", thesis_check_status="contradicted",
                     priority_reminder=True, half_year_score=-3.0),
            # 3 项 P2（weakened / contradicted 无 priority / fact_update / needs_review）
            _hy_item(symbol="W.SZ", thesis_check_status="weakened",
                     priority_reminder=True, half_year_score=-1.0),
            _hy_item(symbol="C.SZ", thesis_check_status="contradicted",
                     priority_reminder=False, half_year_score=-2.0),
            _hy_item(symbol="F.SZ", thesis_check_status="", half_year_score=1.0),
            _hy_item(symbol="N.SZ", needs_research_review=True,
                     thesis_check_status="", half_year_score=0.0),
        ]
        ctx = _ctx_with_half_year(items)
        payload = build_pre_market_payload(ctx, as_of="2026-07-12 09:30:00")
        reminders = payload["half_year_reminders"]
        intraday = [r for r in reminders if r["notify_level"] == NOTIFY_LEVEL_INTRADAY_PUSH]
        daily = [r for r in reminders if r["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST]
        # 只有 P1.SZ 进 intraday_push
        assert len(intraday) == 1
        assert intraday[0]["symbol"] == "P1.SZ"
        # 其余全部进 daily_digest
        assert len(daily) == 4
