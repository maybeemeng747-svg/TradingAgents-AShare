# [NOTIFY-002] notification_mandate_data_blockers
"""Tests for NOTIFY-002: 飞书/通知草稿接入昊天日报与数据缺口摘要.

验收点（docs/TASKS.md NOTIFY-002）覆盖：
- dry-run payload 包含 ``mandate_daily_digest`` 与 ``data_blocker_digest`` 顶层字段。
- mandate 摘要包含升温主题、主候选、证据缺口。
- data_blocker 摘要包含最近报告中失败最多的数据源和影响。
- 去噪：两个摘要草稿只进 ``daily_digest``（P2），不进 ``intraday_push``；
  数据缺口草稿 ``record_only=True``；普通正常无数据不生成草稿。
- dry-run markdown 预览包含摘要区块。
- 禁用词（强买卖词）扫描通过。
- 不真实发送（dry_run 恒 True，webhook_configured 恒 None）。
- 空状态稳定，不报错。
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db
from tradingagents.tradeflow.notification_draft import (
    CHANNEL_DAILY_DIGEST,
    CHANNEL_INTRADAY_PUSH,
    EVENT_DATA_BLOCKER_DIGEST,
    EVENT_MANDATE_DAILY_DIGEST,
    FORBIDDEN_STRONG_WORDS,
    NOTIFY_SCHEMA_VERSION,
    PRIORITY_P2,
    assert_no_forbidden_words,
    build_data_blocker_digest,
    build_mandate_daily_digest,
    build_notification_drafts_from_context,
    classify_delivery_channel,
    render_drafts_markdown,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────────────

AS_OF = "2026-06-26 09:00:00"
NOW = datetime(2026, 6, 26, 9, 0, 0)


def _ctx(**overrides):
    """Minimal IC-TA-001/002-like context."""
    base = {
        "as_of": AS_OF,
        "is_trading_day": True,
        "holdings": {"data_status": "fresh", "items": []},
        "observation_warehouse": {"data_status": "fresh", "items": []},
        "tradeflow_candidates": {"data_status": "fresh", "items": []},
        "latest_ta_reports": {"data_status": "missing", "items": []},
        "data_health": {"data_status": "fresh", "sources": []},
        "pending_ta_required": {"data_status": "missing", "items": []},
        "mandate_daily_report": {
            "source": "mandate_daily_report",
            "as_of": AS_OF,
            "report_as_of": "2026-06-26",
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
            "as_of": AS_OF,
            "data_status": "missing",
            "scanned_report_count": 0,
            "affected_report_count": 0,
            "total_severe_blockers": 0,
            "summary_level": "ok",
            "field_counts": {},
            "affected_symbols": [],
        },
    }
    base.update(overrides)
    return base


def _mandate_bucket(**overrides):
    """A fresh mandate_daily_report bucket with rising topics + candidates."""
    base = {
        "source": "mandate_daily_report",
        "as_of": AS_OF,
        "report_as_of": "2026-06-26",
        "data_status": "fresh",
        "rising_topic_count": 2,
        "cooling_topic_count": 1,
        "main_candidate_count": 2,
        "observation_candidate_count": 3,
        "evidence_gap_count": 2,
        "rising_topics": [
            {"topic": "国产算力", "status_label": "升温",
             "heat_trend_label": "加速升温", "candidate_count": 4},
            {"topic": "低空经济", "status_label": "升温",
             "heat_trend_label": "持续", "candidate_count": 2},
        ],
        "cooling_topics": [
            {"topic": "锂矿", "status_label": "降温", "heat_trend_label": "回落"},
        ],
        "main_candidates": [
            {"symbol": "002230.SZ", "name": "科大讯飞", "topic": "国产算力",
             "candidate_type": "POLICY_AMBUSH", "mandate_score": 82},
            {"symbol": "688111.SH", "name": "金山办公", "topic": "国产算力",
             "candidate_type": "POLICY_CONFIRM", "mandate_score": 75},
        ],
        "observation_candidates": [
            {"symbol": "300033.SZ", "name": "同花顺", "topic": "国产算力",
             "mandate_score": 60},
        ],
        "evidence_gaps": ["国产算力缺主力资金佐证", "低空经济缺政策原文"],
    }
    base.update(overrides)
    return base


def _blockers_bucket(**overrides):
    """A fresh recent_report_data_blockers bucket with severe gaps."""
    base = {
        "source": "recent_report_data_blockers",
        "as_of": AS_OF,
        "data_status": "fresh",
        "scanned_report_count": 5,
        "affected_report_count": 2,
        "total_severe_blockers": 4,
        "summary_level": "warning",
        "field_counts": {
            "individual_fund_flow": 3,
            "announcements": 2,
            "lhb_status": 1,
        },
        "affected_symbols": [
            {"symbol": "603629.SH", "report_id": "r1", "trade_date": "2026-06-25",
             "severe_count": 2, "fields": ["individual_fund_flow", "announcements"],
             "action_label": "数据不足观察", "research_direction": "偏空"},
            {"symbol": "600000.SH", "report_id": "r2", "trade_date": "2026-06-25",
             "severe_count": 2, "fields": ["individual_fund_flow", "lhb_status"],
             "action_label": "数据不足观察", "research_direction": ""},
        ],
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────────
# build_mandate_daily_digest
# ──────────────────────────────────────────────────────────────────────────────

class TestMandateDigest:

    def test_fresh_bucket_produces_available_digest(self):
        digest = build_mandate_daily_digest(_mandate_bucket())
        assert digest["available"] is True
        assert digest["rising_topic_count"] == 2
        assert digest["main_candidate_count"] == 2
        assert digest["evidence_gap_count"] == 2
        assert len(digest["top_rising_topics"]) == 2
        assert len(digest["top_main_candidates"]) == 2
        assert len(digest["evidence_gaps"]) == 2
        assert "升温主题" in digest["summary_text"]

    def test_summary_text_includes_evidence_gap_count(self):
        digest = build_mandate_daily_digest(_mandate_bucket())
        assert "证据缺口" in digest["summary_text"]

    def test_missing_bucket_returns_empty(self):
        digest = build_mandate_daily_digest({"data_status": "missing"})
        assert digest["available"] is False
        assert digest["top_rising_topics"] == []
        assert digest["summary_text"] == ""

    def test_failed_bucket_returns_empty(self):
        digest = build_mandate_daily_digest({"data_status": "failed"})
        assert digest["available"] is False

    def test_none_bucket_returns_empty(self):
        digest = build_mandate_daily_digest(None)
        assert digest["available"] is False

    def test_digest_caps_topics_and_candidates(self):
        many_topics = [{"topic": f"T{i}", "status_label": "升温",
                        "heat_trend_label": "", "candidate_count": i} for i in range(20)]
        many_cands = [{"symbol": f"{600000+i}.SH", "name": f"N{i}",
                       "topic": "x", "candidate_type": "", "mandate_score": i}
                      for i in range(20)]
        digest = build_mandate_daily_digest(_mandate_bucket(
            rising_topics=many_topics,
            main_candidates=many_cands,
            rising_topic_count=20,
            main_candidate_count=20,
        ))
        assert len(digest["top_rising_topics"]) <= 3
        assert len(digest["top_main_candidates"]) <= 3


# ──────────────────────────────────────────────────────────────────────────────
# build_data_blocker_digest
# ──────────────────────────────────────────────────────────────────────────────

class TestDataBlockerDigest:

    def test_fresh_bucket_with_blockers_produces_digest(self):
        digest = build_data_blocker_digest(_blockers_bucket())
        assert digest["available"] is True
        assert digest["has_blockers"] is True
        assert digest["scanned_report_count"] == 5
        assert digest["affected_report_count"] == 2
        assert digest["total_severe_blockers"] == 4
        assert digest["summary_level"] == "warning"
        assert len(digest["top_failed_fields"]) == 3
        # field counts sorted desc: individual_fund_flow(3) > announcements(2) > lhb_status(1)
        assert digest["top_failed_fields"][0]["field"] == "individual_fund_flow"
        assert digest["top_failed_fields"][0]["count"] == 3
        assert len(digest["affected_symbols"]) == 2
        assert "数据缺口" in digest["summary_text"]

    def test_clean_scanned_reports_no_blockers(self):
        digest = build_data_blocker_digest(_blockers_bucket(
            affected_symbols=[],
            affected_report_count=0,
            total_severe_blockers=0,
            field_counts={},
            summary_level="ok",
        ))
        assert digest["available"] is True
        assert digest["has_blockers"] is False
        assert digest["top_failed_fields"] == []
        # Scanned clean -> summary notes the scan, no blocker wording.
        assert "扫描" in digest["summary_text"]

    def test_missing_bucket_returns_empty(self):
        digest = build_data_blocker_digest({"data_status": "missing"})
        assert digest["available"] is False
        assert digest["has_blockers"] is False

    def test_failed_bucket_returns_empty(self):
        digest = build_data_blocker_digest({"data_status": "failed"})
        assert digest["available"] is False

    def test_none_bucket_returns_empty(self):
        digest = build_data_blocker_digest(None)
        assert digest["available"] is False

    def test_failed_fields_sorted_desc_and_capped(self):
        many_fields = {f"field_{i}": 10 - i for i in range(10)}
        digest = build_data_blocker_digest(_blockers_bucket(field_counts=many_fields))
        assert len(digest["top_failed_fields"]) <= 5
        # Highest count first.
        assert digest["top_failed_fields"][0]["count"] >= digest["top_failed_fields"][1]["count"]


# ──────────────────────────────────────────────────────────────────────────────
# Draft generation: mandate + data_blocker drafts wired into context
# ──────────────────────────────────────────────────────────────────────────────

class TestDigestDraftsFromContext:

    def test_mandate_digest_draft_generated_when_fresh(self):
        ctx = _ctx(mandate_daily_report=_mandate_bucket())
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        mandate_drafts = [d for d in drafts if d["event_type"] == EVENT_MANDATE_DAILY_DIGEST]
        assert len(mandate_drafts) == 1
        d = mandate_drafts[0]
        assert d["priority"] == PRIORITY_P2
        assert d["source"] == "mandate_daily_report"
        assert d["record_only"] is False
        assert "升温主题" in d["reason"]
        # P2 -> daily_digest channel, never intraday.
        assert classify_delivery_channel(d) == CHANNEL_DAILY_DIGEST

    def test_mandate_digest_no_draft_when_missing(self):
        ctx = _ctx(mandate_daily_report={"data_status": "missing"})
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        mandate_drafts = [d for d in drafts if d["event_type"] == EVENT_MANDATE_DAILY_DIGEST]
        assert mandate_drafts == []

    def test_data_blocker_digest_draft_generated_when_has_blockers(self):
        ctx = _ctx(recent_report_data_blockers=_blockers_bucket())
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        blocker_drafts = [d for d in drafts if d["event_type"] == EVENT_DATA_BLOCKER_DIGEST]
        assert len(blocker_drafts) == 1
        d = blocker_drafts[0]
        assert d["priority"] == PRIORITY_P2
        assert d["record_only"] is True  # informational, not intraday actionable
        assert d["source"] == "recent_report_data_blockers"
        assert "individual_fund_flow" in d["reason"]
        assert classify_delivery_channel(d) == CHANNEL_DAILY_DIGEST

    def test_data_blocker_no_draft_when_clean(self):
        """普通正常无数据 / 扫描干净不生成草稿（去噪规则 3）."""
        ctx = _ctx(recent_report_data_blockers=_blockers_bucket(
            affected_symbols=[],
            affected_report_count=0,
            total_severe_blockers=0,
            field_counts={},
            summary_level="ok",
        ))
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        blocker_drafts = [d for d in drafts if d["event_type"] == EVENT_DATA_BLOCKER_DIGEST]
        assert blocker_drafts == []

    def test_data_blocker_no_draft_when_missing(self):
        ctx = _ctx(recent_report_data_blockers={"data_status": "missing"})
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        blocker_drafts = [d for d in drafts if d["event_type"] == EVENT_DATA_BLOCKER_DIGEST]
        assert blocker_drafts == []

    def test_both_digests_present_too(self):
        ctx = _ctx(
            mandate_daily_report=_mandate_bucket(),
            recent_report_data_blockers=_blockers_bucket(),
        )
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        event_types = {d["event_type"] for d in drafts}
        assert EVENT_MANDATE_DAILY_DIGEST in event_types
        assert EVENT_DATA_BLOCKER_DIGEST in event_types

    def test_digest_drafts_never_enter_intraday_push(self):
        """去噪规则：P2/P3 + 摘要只进日报，不盘中推送."""
        ctx = _ctx(
            mandate_daily_report=_mandate_bucket(),
            recent_report_data_blockers=_blockers_bucket(),
        )
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        for d in drafts:
            if d["event_type"] in (EVENT_MANDATE_DAILY_DIGEST, EVENT_DATA_BLOCKER_DIGEST):
                assert classify_delivery_channel(d) == CHANNEL_DAILY_DIGEST

    def test_generated_digest_drafts_have_no_forbidden_words(self):
        ctx = _ctx(
            mandate_daily_report=_mandate_bucket(),
            recent_report_data_blockers=_blockers_bucket(),
        )
        drafts = build_notification_drafts_from_context(ctx, now=NOW)
        assert_no_forbidden_words(drafts)  # should not raise


# ──────────────────────────────────────────────────────────────────────────────
# Markdown preview rendering with digest sections
# ──────────────────────────────────────────────────────────────────────────────

class TestMarkdownDigestSections:

    def test_markdown_renders_mandate_digest_section(self):
        digest = build_mandate_daily_digest(_mandate_bucket())
        md = render_drafts_markdown(
            intraday_push=[], daily_digest=[], recorded_only=[],
            deduplicated=[], as_of=AS_OF,
            mandate_digest=digest,
        )
        assert "昊天日报摘要" in md
        assert "mandate_daily_digest" in md
        assert "国产算力" in md
        assert "升温主题" in md
        assert "主候选" in md
        assert "证据缺口" in md

    def test_markdown_renders_data_blocker_digest_section(self):
        digest = build_data_blocker_digest(_blockers_bucket())
        md = render_drafts_markdown(
            intraday_push=[], daily_digest=[], recorded_only=[],
            deduplicated=[], as_of=AS_OF,
            data_blocker_digest=digest,
        )
        assert "数据缺口摘要" in md
        assert "data_blocker_digest" in md
        assert "individual_fund_flow" in md
        assert "失败最多字段" in md
        assert "受影响标的" in md

    def test_markdown_skips_mandate_section_when_unavailable(self):
        digest = build_mandate_daily_digest({"data_status": "missing"})
        md = render_drafts_markdown(
            intraday_push=[], daily_digest=[], recorded_only=[],
            deduplicated=[], as_of=AS_OF,
            mandate_digest=digest,
        )
        assert "昊天日报摘要" not in md

    def test_markdown_skips_data_blocker_section_when_clean(self):
        """扫描干净（has_blockers=False）不渲染独立区块（避免无缺口刷屏）."""
        digest = build_data_blocker_digest(_blockers_bucket(
            affected_symbols=[],
            affected_report_count=0,
            total_severe_blockers=0,
            field_counts={},
            summary_level="ok",
        ))
        md = render_drafts_markdown(
            intraday_push=[], daily_digest=[], recorded_only=[],
            deduplicated=[], as_of=AS_OF,
            data_blocker_digest=digest,
        )
        assert "数据缺口摘要" not in md


# ──────────────────────────────────────────────────────────────────────────────
# Service layer end-to-end (dry-run payload)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_sqla_session():
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
    db_path = str(tmp_path / "test_notify002_tradeflow.db")
    init_db(db_path)
    return db_path


class TestServiceDryRunDigests:

    def test_payload_contains_digest_top_level_keys(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        # Force mandate report to no_data so empty-state path is isolated from
        # any saved mandate report shipped with the repo.
        monkeypatch.setattr(
            "api.services.tradeflow_service.get_mandate_daily_report",
            lambda **kw: {"status": "no_data"},
        )
        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        assert "mandate_daily_digest" in payload
        assert "data_blocker_digest" in payload
        # Stable empty structure even with no mandate report / no reports.
        assert payload["mandate_daily_digest"]["available"] is False
        assert payload["data_blocker_digest"]["available"] is False

    def test_payload_markdown_preview_contains_digest_section_when_fresh(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        # Inject a fresh mandate bucket by patching the IC-TA-002 collector.
        from api.services import investment_controller_context as ic_ctx

        def _fake_mandate(as_of, notes):  # noqa: ARG001
            return _mandate_bucket()

        monkeypatch.setattr(ic_ctx, "_collect_mandate_daily_report", _fake_mandate)

        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        digest = payload["mandate_daily_digest"]
        assert digest["available"] is True
        assert digest["rising_topic_count"] == 2
        assert "昊天日报摘要" in payload["markdown_preview"]
        assert "国产算力" in payload["markdown_preview"]

    def test_payload_data_blocker_digest_when_reports_have_gaps(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        # Seed a completed report with severe blockers.
        from api.database import ReportDB
        blockers = [
            {"key": "individual_fund_flow", "label": "individual_fund_flow",
             "status": "query_failed", "status_label": "query_failed",
             "severity": "high", "reason": "timeout", "impact": "demo"},
            {"key": "announcements", "label": "announcements",
             "status": "query_failed", "status_label": "query_failed",
             "severity": "high", "reason": "unavailable", "impact": "demo"},
        ]
        tmp_sqla_session.add(ReportDB(
            id=uuid.uuid4().hex, user_id="ghost_user", symbol="603629.SH",
            trade_date="2026-06-25", status="completed", decision="HOLD",
            direction="", research_direction="偏空", execution_action="WAIT",
            action_label="数据不足观察", target_price=None, stop_loss_price=None,
            confidence="", risk_items="[]", key_metrics="{}", analyst_traces="{}",
            trader_investment_plan="", final_trade_decision="",
            result_data={
                "market_report": "demo",
                "data_blockers": blockers,
                "data_blocker_summary": {"level": "warning", "message": "demo",
                                         "counts": {"query_failed": 2}, "total": 2},
            },
        ))
        tmp_sqla_session.commit()

        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})

        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        digest = payload["data_blocker_digest"]
        assert digest["available"] is True
        assert digest["has_blockers"] is True
        assert digest["affected_report_count"] == 1
        assert digest["total_severe_blockers"] == 2
        # The data blocker digest draft must land in recorded_only (record_only=True).
        assert any(
            d["event_type"] == EVENT_DATA_BLOCKER_DIGEST
            for d in payload["recorded_only"]
        )
        assert "数据缺口摘要" in payload["markdown_preview"]

    def test_payload_context_data_status_includes_new_buckets(
        self, tmp_sqla_session, tmp_tf_db
    ):
        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        cds = payload["context_data_status"]
        assert "mandate_daily_report" in cds
        assert "recent_report_data_blockers" in cds

    def test_payload_no_forbidden_words_across_all_channels(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        from api.services import investment_controller_context as ic_ctx

        monkeypatch.setattr(
            ic_ctx, "_collect_mandate_daily_report",
            lambda as_of, notes: _mandate_bucket(),  # noqa: ARG005
        )
        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        from api.database import ReportDB
        tmp_sqla_session.add(ReportDB(
            id=uuid.uuid4().hex, user_id="ghost_user", symbol="603629.SH",
            trade_date="2026-06-25", status="completed", decision="HOLD",
            direction="", research_direction="", execution_action="",
            action_label="数据不足观察", target_price=None, stop_loss_price=None,
            confidence="", risk_items="[]", key_metrics="{}", analyst_traces="{}",
            trader_investment_plan="", final_trade_decision="",
            result_data={
                "market_report": "demo",
                "data_blockers": [
                    {"key": "individual_fund_flow", "label": "x",
                     "status": "query_failed", "status_label": "query_failed",
                     "severity": "high", "reason": "timeout", "impact": "demo"},
                ],
                "data_blocker_summary": {"level": "warning"},
            },
        ))
        tmp_sqla_session.commit()

        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        all_drafts = (
            payload["intraday_push"] + payload["daily_digest"]
            + payload["recorded_only"] + payload["deduplicated"]
        )
        assert_no_forbidden_words(all_drafts)  # should not raise

    def test_dry_run_and_webhook_configured_unaffected(
        self, tmp_sqla_session, tmp_tf_db
    ):
        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        payload = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        assert payload["schema_version"] == NOTIFY_SCHEMA_VERSION
        assert payload["dry_run"] is True
        assert payload["webhook_configured"] is None

    def test_service_dedup_applies_to_digest_drafts(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        """Digest drafts are deduped like any other draft (same event within window)."""
        from api.services import investment_controller_context as ic_ctx
        monkeypatch.setattr(
            ic_ctx, "_collect_mandate_daily_report",
            lambda as_of, notes: _mandate_bucket(),  # noqa: ARG005
        )
        from api.services import notification_draft_service
        notification_draft_service.reset_dedup_state()
        p1 = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db, now=NOW,
        )
        first_total = p1["summary_counts"]["total_drafts"]
        assert first_total >= 1
        # second call within window -> mandate digest draft deduped.
        p2 = notification_draft_service.build_notification_dry_run(
            tmp_sqla_session, "ghost_user", tf_db_path=tmp_tf_db,
            now=NOW + timedelta(minutes=5),
        )
        assert p2["summary_counts"]["deduplicated"] == first_total
        assert p2["summary_counts"]["total_drafts"] == first_total


# ──────────────────────────────────────────────────────────────────────────────
# Forbidden-words sanity for the NOTIFY-002 vocabulary
# ──────────────────────────────────────────────────────────────────────────────

class TestForbiddenWordsVocabulary:
    def test_forbidden_list_still_complete(self):
        for word in ("立即买入", "清仓", "满仓", "梭哈"):
            assert word in FORBIDDEN_STRONG_WORDS
