# [TRACK-NOTIFY-001] notification_payload_dry_run
"""Tests for TRACK-NOTIFY-001: 飞书/总控官通知草稿 payload 与去噪规则.

验收点（docs/TASKS.md TRACK-NOTIFY-001）覆盖：
- dry-run 生成 payload（schema: priority/symbol/title/reason/source/as_of/suggested_next_step）
- 未配置 webhook 不报错（webhook_configured 恒 None，不读取环境）
- 重复事件被去重（同一标的同一事件 30 分钟内不重复）
- payload 不含强买卖词
- P2/P3 只进日报，不盘中推送；数据不足只记录不推送
- 空 IC context 不报错，稳定空结构
- markdown / json 预览渲染
- service 层 build_notification_dry_run 端到端
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db
from tradingagents.tradeflow.notification_draft import (
    ALLOWED_PRIORITIES,
    CHANNEL_DAILY_DIGEST,
    CHANNEL_INTRADAY_PUSH,
    DEDUP_WINDOW_SECONDS,
    EVENT_HOLDINGS_RISK_LARGE,
    EVENT_OBSERVATION_DATA_MISSING,
    EVENT_OBSERVATION_IN_ENTRY_ZONE,
    EVENT_OBSERVATION_INVALIDATED,
    EVENT_OBSERVATION_NEAR_ENTRY,
    EVENT_PENDING_TA_REQUIRED,
    FORBIDDEN_STRONG_WORDS,
    NOTIFY_SCHEMA_VERSION,
    NotificationDeduplicator,
    PRIORITY_P0,
    PRIORITY_P1,
    PRIORITY_P2,
    PRIORITY_P3,
    apply_dedup,
    assert_no_forbidden_words,
    build_notification_drafts_from_context,
    classify_delivery_channel,
    render_drafts_json,
    render_drafts_markdown,
    split_by_channel,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────────────

AS_OF = "2026-06-23 14:00:00"
NOW = datetime(2026, 6, 23, 14, 0, 0)


def _ctx(**overrides):
    """Minimal IC-TA-001-like context."""
    base = {
        "as_of": AS_OF,
        "is_trading_day": True,
        "holdings": {"data_status": "fresh", "items": []},
        "observation_warehouse": {"data_status": "fresh", "items": []},
        "tradeflow_candidates": {"data_status": "fresh", "items": []},
        "latest_ta_reports": {"data_status": "missing", "items": []},
        "data_health": {"data_status": "fresh", "sources": []},
        "pending_ta_required": {"data_status": "missing", "items": []},
    }
    base.update(overrides)
    return base


def _schema_keys():
    return {
        "event_type", "priority", "symbol", "name", "title", "reason",
        "source", "as_of", "suggested_next_step", "record_only",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Schema & dry-run basics
# ──────────────────────────────────────────────────────────────────────────────

class TestSchemaAndDryRun:
    def test_empty_context_returns_empty_list_no_raise(self):
        drafts = build_notification_drafts_from_context(_ctx(), now=NOW)
        assert drafts == []

    def test_each_draft_has_full_schema(self):
        ctx = _ctx(holdings={"data_status": "fresh", "items": [
            {"symbol": "600000.SH", "name": "浦发", "price_change_pct": -4.0,
             "live_price": 10.0, "latest_report": {"report_id": "r1"}},
        ]})
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        assert len(drafts) == 1
        assert _schema_keys().issubset(drafts[0].keys())
        assert drafts[0]["priority"] in ALLOWED_PRIORITIES
        assert drafts[0]["source"]
        assert drafts[0]["as_of"] == AS_OF

    def test_priority_values_are_canonical(self):
        ctx = _ctx(holdings={"data_status": "fresh", "items": [
            {"symbol": "A.SH", "name": "A", "price_change_pct": -5.0,
             "live_price": 10.0, "latest_report": {"report_id": "r"}},
            {"symbol": "B.SH", "name": "B", "price_change_pct": -2.0,
             "live_price": 10.0, "latest_report": {"report_id": "r"}},
        ]})
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        priorities = {d["priority"] for d in drafts}
        assert PRIORITY_P0 in priorities
        assert PRIORITY_P1 in priorities


# ──────────────────────────────────────────────────────────────────────────────
# 去噪规则：同一标的同一事件 30 分钟内不重复
# ──────────────────────────────────────────────────────────────────────────────

class TestDedupRules:
    def test_first_emit_passes(self):
        dd = NotificationDeduplicator()
        ok, key, reason = dd.should_emit(
            {"symbol": "600000.SH", "event_type": EVENT_HOLDINGS_RISK_LARGE}, NOW
        )
        assert ok is True
        assert reason == ""
        assert key == "600000.SH|holdings_risk_large_drop"

    def test_same_event_within_window_suppressed(self):
        dd = NotificationDeduplicator()
        draft = {"symbol": "600000.SH", "event_type": EVENT_HOLDINGS_RISK_LARGE}
        dd.mark_emitted(draft, NOW)
        ok, _, reason = dd.should_emit(draft, NOW + timedelta(minutes=10))
        assert ok is False
        assert "分钟" in reason

    def test_after_window_re_emitted(self):
        dd = NotificationDeduplicator()
        draft = {"symbol": "600000.SH", "event_type": EVENT_HOLDINGS_RISK_LARGE}
        dd.mark_emitted(draft, NOW)
        boundary = NOW + timedelta(seconds=DEDUP_WINDOW_SECONDS) + timedelta(seconds=1)
        ok, _, _ = dd.should_emit(draft, boundary)
        assert ok is True

    def test_different_event_same_symbol_not_deduped(self):
        dd = NotificationDeduplicator()
        d1 = {"symbol": "000001.SZ", "event_type": EVENT_OBSERVATION_NEAR_ENTRY}
        d2 = {"symbol": "000001.SZ", "event_type": EVENT_PENDING_TA_REQUIRED}
        dd.mark_emitted(d1, NOW)
        ok, _, _ = dd.should_emit(d2, NOW + timedelta(minutes=5))
        assert ok is True

    def test_apply_dedup_splits_emitted_and_deduplicated(self):
        dd = NotificationDeduplicator()
        ctx = _ctx(holdings={"data_status": "fresh", "items": [
            {"symbol": "600000.SH", "name": "浦发", "price_change_pct": -4.0,
             "live_price": 10.0, "latest_report": {"report_id": "r"}},
        ]})
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        # first call -> all emitted
        r1 = apply_dedup(drafts, dd, now=NOW)
        assert len(r1["emitted"]) == len(drafts)
        assert r1["deduplicated"] == []
        # second call within window -> all deduped
        r2 = apply_dedup(drafts, dd, now=NOW + timedelta(minutes=5))
        assert r2["emitted"] == []
        assert len(r2["deduplicated"]) == len(drafts)
        assert all("dedup_reason" in d for d in r2["deduplicated"])

    def test_reset_clears_state(self):
        dd = NotificationDeduplicator()
        draft = {"symbol": "X.SH", "event_type": EVENT_HOLDINGS_RISK_LARGE}
        dd.mark_emitted(draft, NOW)
        dd.reset()
        ok, _, _ = dd.should_emit(draft, NOW + timedelta(minutes=1))
        assert ok is True


# ──────────────────────────────────────────────────────────────────────────────
# 投递通道：P0/P1 盘中；P2/P3 + record_only 日报
# ──────────────────────────────────────────────────────────────────────────────

class TestDeliveryChannel:
    def test_p0_p1_non_record_go_intraday(self):
        assert classify_delivery_channel({"priority": PRIORITY_P0, "record_only": False}) == CHANNEL_INTRADAY_PUSH
        assert classify_delivery_channel({"priority": PRIORITY_P1, "record_only": False}) == CHANNEL_INTRADAY_PUSH

    def test_p2_p3_go_daily_digest(self):
        assert classify_delivery_channel({"priority": PRIORITY_P2, "record_only": False}) == CHANNEL_DAILY_DIGEST
        assert classify_delivery_channel({"priority": PRIORITY_P3, "record_only": False}) == CHANNEL_DAILY_DIGEST

    def test_record_only_always_daily_even_if_p0(self):
        """数据不足只记录，不盘中推送（即使优先级是 P0）."""
        assert classify_delivery_channel({"priority": PRIORITY_P0, "record_only": True}) == CHANNEL_DAILY_DIGEST

    def test_split_by_channel(self):
        drafts = [
            {"priority": PRIORITY_P0, "record_only": False, "symbol": "A"},
            {"priority": PRIORITY_P1, "record_only": False, "symbol": "B"},
            {"priority": PRIORITY_P2, "record_only": False, "symbol": "C"},
            {"priority": PRIORITY_P0, "record_only": True, "symbol": "D"},
        ]
        ch = split_by_channel(drafts)
        assert len(ch[CHANNEL_INTRADAY_PUSH]) == 2
        assert len(ch[CHANNEL_DAILY_DIGEST]) == 2

    def test_data_missing_observation_is_record_only(self):
        """观察仓行情数据缺失 -> record_only，不进 intraday."""
        ctx = _ctx(observation_warehouse={"data_status": "fresh", "items": [
            {"symbol": "000001.SZ", "name": "平安", "entry_low": 10.0,
             "entry_high": 11.0, "live_price": None, "status": "watching"},
        ]})
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        # live_price None + trading day -> data_missing state
        dm = [d for d in drafts if d["event_type"] == EVENT_OBSERVATION_DATA_MISSING]
        assert len(dm) == 1
        assert dm[0]["record_only"] is True
        assert classify_delivery_channel(dm[0]) == CHANNEL_DAILY_DIGEST

    def test_failed_bucket_makes_items_record_only(self):
        """holdings bucket data_status=failed -> 草稿 record_only."""
        ctx = _ctx(holdings={"data_status": "failed", "items": [
            {"symbol": "600000.SH", "name": "浦发", "price_change_pct": -5.0,
             "live_price": 10.0, "latest_report": {"report_id": "r"}},
        ]})
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        risk = [d for d in drafts if d["event_type"] == EVENT_HOLDINGS_RISK_LARGE]
        assert len(risk) == 1
        assert risk[0]["record_only"] is True


# ──────────────────────────────────────────────────────────────────────────────
# 强动作词护栏
# ──────────────────────────────────────────────────────────────────────────────

class TestForbiddenWords:
    def test_generated_drafts_have_no_strong_words(self):
        ctx = _ctx(
            holdings={"data_status": "fresh", "items": [
                {"symbol": "600000.SH", "name": "浦发", "price_change_pct": -5.0,
                 "live_price": 10.0, "latest_report": {}},
            ]},
            observation_warehouse={"data_status": "fresh", "items": [
                {"symbol": "000001.SZ", "name": "平安", "entry_low": 10.0,
                 "entry_high": 11.0, "live_price": 10.5, "status": "watching"},
            ]},
        )
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        # should not raise
        assert_no_forbidden_words(drafts)

    def test_assert_raises_on_strong_word(self):
        bad = [{
            "event_type": "x", "priority": "P0", "symbol": "A",
            "title": "立即买入", "reason": "", "suggested_next_step": "",
        }]
        with pytest.raises(AssertionError):
            assert_no_forbidden_words(bad)

    def test_forbidden_list_includes_core_strong_verbs(self):
        for word in ("立即买入", "清仓", "满仓", "梭哈"):
            assert word in FORBIDDEN_STRONG_WORDS


# ──────────────────────────────────────────────────────────────────────────────
# 预览渲染
# ──────────────────────────────────────────────────────────────────────────────

class TestPreviewRender:
    def test_markdown_has_sections_and_counts(self):
        md = render_drafts_markdown(
            intraday_push=[{"priority": "P0", "title": "T1", "symbol": "A.SH",
                            "name": "A", "reason": "r", "suggested_next_step": "s"}],
            daily_digest=[],
            recorded_only=[{"priority": "P2", "title": "R", "symbol": "B.SH",
                            "name": "B", "reason": "", "suggested_next_step": "",
                            "record_only": True}],
            deduplicated=[{"priority": "P1", "title": "D", "symbol": "C.SH",
                           "name": "C", "dedup_reason": "30 分钟内已发送过"}],
            as_of=AS_OF,
        )
        assert "通知草稿预览" in md
        assert "intraday_push" in md or "盘中主动提醒" in md
        assert "T1" in md
        assert "仅记录" in md
        assert "去重抑制" in md

    def test_markdown_empty_stable(self):
        md = render_drafts_markdown(
            intraday_push=[], daily_digest=[], recorded_only=[],
            deduplicated=[], as_of=AS_OF,
        )
        assert "通知草稿预览" in md
        assert "（无）" in md

    def test_json_preview_has_channel_tags(self):
        out = render_drafts_json(
            intraday_push=[{"priority": "P0", "symbol": "A"}],
            daily_digest=[{"priority": "P2", "symbol": "B"}],
            recorded_only=[{"priority": "P2", "symbol": "C"}],
            deduplicated=[{"priority": "P1", "symbol": "D"}],
        )
        channels = [d["channel"] for d in out]
        assert CHANNEL_INTRADAY_PUSH in channels
        assert CHANNEL_DAILY_DIGEST in channels
        assert "suppressed" in channels


# ──────────────────────────────────────────────────────────────────────────────
# Service 层端到端（dry-run payload）
# ──────────────────────────────────────────────────────────────────────────────

class TestServiceDryRun:
    def test_empty_state_payload_is_stable(self, tmp_sqla_session, tmp_tf_db):
        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        assert payload["schema_version"] == NOTIFY_SCHEMA_VERSION
        assert payload["dry_run"] is True
        # 未配置 webhook 不报错：webhook_configured 恒 None（本服务不读取）
        assert payload["webhook_configured"] is None
        assert payload["intraday_push"] == []
        assert payload["daily_digest"] == []
        assert payload["recorded_only"] == []
        assert payload["summary_counts"]["total_drafts"] == 0
        assert "markdown_preview" in payload
        assert "json_preview" in payload
        # runtime tier = FAST_RADAR（不触发 LLM）
        assert payload["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"

    def test_service_dedups_across_calls(self, tmp_sqla_session, tmp_tf_db):
        from api.services import notification_draft_service
        from api.database import ImportedPortfolioPositionDB
        import uuid

        tmp_sqla_session.add(ImportedPortfolioPositionDB(
            id=uuid.uuid4().hex, user_id="ghost_user", source="test",
            symbol="600000.SH", security_name="浦发银行",
            current_position=100, available_position=100, average_cost=10.0,
            market_value=1000.0, current_position_pct=0.0,
            trade_points_json="[]", trade_points_count=0,
            latest_trade_at=None, latest_trade_action=None,
            last_imported_at=NOW,
        ))
        tmp_sqla_session.commit()

        notification_draft_service.reset_dedup_state()
        p1 = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        # holding has no report -> at least the holdings_no_analysis draft
        assert p1["summary_counts"]["total_drafts"] >= 1
        first_total = p1["summary_counts"]["total_drafts"]
        first_intraday = p1["summary_counts"]["intraday_push"]

        # second call within window -> all deduped
        p2 = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db,
            now=NOW + timedelta(minutes=5),
        )
        assert p2["summary_counts"]["total_drafts"] == first_total
        assert p2["summary_counts"]["deduplicated"] == first_total
        assert p2["summary_counts"]["intraday_push"] == 0

        # force_refresh resets dedup
        p3 = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db,
            force_refresh=True, now=NOW + timedelta(minutes=6),
        )
        assert p3["summary_counts"]["intraday_push"] == first_intraday

    def test_payload_has_no_forbidden_words(self, tmp_sqla_session, tmp_tf_db):
        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        # should not raise across all channels
        all_drafts = (
            payload["intraday_push"] + payload["daily_digest"]
            + payload["recorded_only"] + payload["deduplicated"]
        )
        assert_no_forbidden_words(all_drafts)

    def test_webhook_env_not_required(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        """未配置 webhook 不报错：即使删掉所有相关 env 也正常返回."""
        from api.services import notification_draft_service
        for key in list(os.environ.keys()):
            if "WEBHOOK" in key.upper() or "FEISHU" in key.upper() or "WECOM" in key.upper():
                monkeypatch.delenv(key, raising=False)
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        assert payload["webhook_configured"] is None


# ──────────────────────────────────────────────────────────────────────────────
# 共享 fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_sqla_session():
    """In-memory SQLAlchemy session with holdings + reports tables."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from api.database import Base, ImportedPortfolioPositionDB, ReportDB

    engine = create_engine("sqlite:///:memory:")
    ImportedPortfolioPositionDB.__table__.create(engine)
    ReportDB.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def tmp_tf_db(tmp_path):
    db_path = str(tmp_path / "test_track_notify_tradeflow.db")
    init_db(db_path)
    return db_path
