# [IC-TA-003] controller_briefing_dry_run
"""Tests for IC-TA-003: investment-controller 盘前/盘后 briefing fixture dry-run.

Covers:
- Pure engine ``build_pre_market_briefing`` / ``build_post_market_briefing``
  on both the built-in fixtures and synthetic minimal contexts.
- Briefing payload structure: schema_version / briefing_type / source / as_of.
- Routing lanes: ``ta_to_schedule`` / ``daily_report_only`` /
  ``needs_manual_confirmation`` (post: ``tomorrow_ta_candidates``).
- **"为什么要/不要调 TA"** requirement: every routing entry carries an explicit
  ``reason_call_ta`` / ``reason_skip_ta`` / ``reason`` field.
- Forbidden strong-word scan: ``scan_forbidden_words`` self-check on the whole
  payload; no synthesised strong action verb.
- READ-ONLY / FAST_RADAR contract via the service wrapper + runtime_tier.
- JSON-serialisable; graceful degradation on empty / missing buckets.
- Fixture dry-run entry point ``dry_run_all_fixtures``.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db

from tradingagents.tradeflow.controller_briefing import (
    ALLOWED_BRIEFING_TYPES,
    BRIEFING_POST_MARKET,
    BRIEFING_PRE_MARKET,
    BRIEFING_SCHEMA_VERSION,
    BRIEFING_SOURCE,
    FORBIDDEN_STRONG_WORDS,
    POST_MARKET_FIXTURE_CONTEXT,
    PRE_MARKET_FIXTURE_CONTEXT,
    PROFILE_FULL_TA,
    PROFILE_MIDLINE_POLICY_LIGHT,
    PROFILE_POSITION_RISK_LIGHT,
    PROFILE_SHORT_TECH_LIGHT,
    build_post_market_briefing,
    build_pre_market_briefing,
    dry_run_all_fixtures,
    scan_forbidden_words,
)
from api.runtime_tier import RuntimeTier, tradeflow_endpoint_tier
from api.services.controller_briefing_service import (
    build_post_market_briefing_dry_run,
    build_pre_market_briefing_dry_run,
    run_briefing_fixtures,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

TODAY = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def tmp_tf_db(tmp_path):
    db_path = str(tmp_path / "test_ic_ta003_tradeflow.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_sqla_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api.database import ImportedPortfolioPositionDB, ReportDB

    engine = create_engine("sqlite:///:memory:")
    ImportedPortfolioPositionDB.__table__.create(engine)
    ReportDB.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ──────────────────────────────────────────────────────────────────────────────
# Forbidden-word scan helper
# ──────────────────────────────────────────────────────────────────────────────

class TestScanForbiddenWords:

    def test_clean_payload(self):
        payload = {"headline": "盘前 briefing", "items": [{"reason": "观察"}]}
        result = scan_forbidden_words(payload)
        assert result["found"] is False
        assert result["hits"] == []
        assert "立即买入" in result["scanned_words"]

    def test_detects_strong_word_in_synthesised_field(self):
        payload = {
            "headline": "正常",
            "ta_to_schedule": [
                {"reason_call_ta": "建议立即买入"}  # 注入强词
            ],
        }
        result = scan_forbidden_words(payload)
        assert result["found"] is True
        assert any(h["word"] == "立即买入" for h in result["hits"])

    def test_skips_ta_decision_fact_fields(self):
        # TA 报告原始 decision 是结构化事实，不在引擎合成扫描范围。
        payload = {"items": [{"latest_report": {"decision": "满仓"}}]}
        result = scan_forbidden_words(payload)
        assert result["found"] is False

    def test_scans_nested_lists_and_strings(self):
        payload = {
            "lanes": [
                {"notes": ["正常", "第二条含清仓"]}
            ]
        }
        result = scan_forbidden_words(payload)
        assert result["found"] is True
        assert any(h["word"] == "清仓" for h in result["hits"])

    @pytest.mark.parametrize("word", list(FORBIDDEN_STRONG_WORDS))
    def test_every_forbidden_word_is_detected(self, word):
        payload = {"reason": f"含 {word} 的理由"}
        assert scan_forbidden_words(payload)["found"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Pre-market briefing — structure + fixture routing
# ──────────────────────────────────────────────────────────────────────────────

class TestPreMarketBriefingStructure:

    def test_top_level_metadata(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        assert briefing["schema_version"] == BRIEFING_SCHEMA_VERSION
        assert briefing["briefing_type"] == BRIEFING_PRE_MARKET
        assert briefing["generated_by"] == BRIEFING_SOURCE
        assert briefing["read_only"] is True
        assert briefing["as_of"]
        assert isinstance(briefing["headline"], str) and briefing["headline"]

    def test_all_required_lanes_present(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        for lane in ("ta_to_schedule", "daily_report_only",
                     "needs_manual_confirmation"):
            assert isinstance(briefing[lane], list)
        assert isinstance(briefing["mandate_digest"], dict)
        assert isinstance(briefing["data_health_note"], str)
        assert isinstance(briefing["forbidden_word_scan"], dict)
        assert isinstance(briefing["notes"], list)

    def test_forbidden_word_scan_self_check(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        scan = briefing["forbidden_word_scan"]
        assert scan["found"] is False, f"forbidden words leaked: {scan['hits']}"

    def test_json_serialisable(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        json.dumps(briefing)


class TestPreMarketFixtureRouting:
    """The pre-market fixture carries 2 needs_ta + 1 daily_only + 0 manual."""

    def test_ta_to_schedule_routed(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        ta = briefing["ta_to_schedule"]
        symbols = {item["symbol"] for item in ta}
        assert symbols == {"601689.SH", "688256.SH"}

    def test_each_ta_entry_has_reason_call_ta(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        for item in briefing["ta_to_schedule"]:
            # IC-TA-003 验收：briefing 必须包含"为什么要调 TA"。
            assert item["reason_call_ta"], "missing reason_call_ta"
            assert isinstance(item["reason_call_ta"], str)

    def test_policy_ambush_candidate_routes_to_midline_profile(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        cambricon = next(
            it for it in briefing["ta_to_schedule"] if it["symbol"] == "688256.SH"
        )
        assert cambricon["candidate_type"] == "POLICY_AMBUSH"
        assert cambricon["suggested_profile"] == PROFILE_MIDLINE_POLICY_LIGHT
        assert cambricon["requires_confirmation"] is False  # light profile

    def test_observation_origin_routes_to_position_risk_profile(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        topu = next(
            it for it in briefing["ta_to_schedule"] if it["symbol"] == "601689.SH"
        )
        assert topu["origin"] == "observation_warehouse"
        assert topu["suggested_profile"] == PROFILE_POSITION_RISK_LIGHT

    def test_daily_report_only_routed_with_reason_skip_ta(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        daily = briefing["daily_report_only"]
        assert len(daily) == 1
        assert daily[0]["symbol"] == "002353.SZ"
        # IC-TA-003 验收：briefing 必须包含"为什么不要调 TA"。
        assert daily[0]["reason_skip_ta"]

    def test_manual_confirmation_empty_when_no_data_gaps(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        # Pre-market fixture has clean reports → no manual confirm items.
        assert briefing["needs_manual_confirmation"] == []

    def test_mandate_digest_present(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        digest = briefing["mandate_digest"]
        assert digest["rising_topic_count"] == 2
        assert digest["main_candidate_count"] == 1
        assert "半导体自主可控" in digest["top_rising_topics"]

    def test_data_health_note_mentions_db_available(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        assert "TradeFlow DB 可用" in briefing["data_health_note"]

    def test_every_entry_carries_source_and_as_of(self):
        briefing = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)
        for lane in ("ta_to_schedule", "daily_report_only",
                     "needs_manual_confirmation"):
            for item in briefing[lane]:
                assert item.get("source"), f"{lane} entry missing source"
                assert item.get("as_of"), f"{lane} entry missing as_of"


# ──────────────────────────────────────────────────────────────────────────────
# Post-market briefing — structure + fixture routing
# ──────────────────────────────────────────────────────────────────────────────

class TestPostMarketBriefingStructure:

    def test_top_level_metadata(self):
        briefing = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)
        assert briefing["schema_version"] == BRIEFING_SCHEMA_VERSION
        assert briefing["briefing_type"] == BRIEFING_POST_MARKET
        assert briefing["generated_by"] == BRIEFING_SOURCE
        assert briefing["read_only"] is True

    def test_required_sections_present(self):
        briefing = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)
        for key in ("today_performance", "report_data_gaps",
                    "tomorrow_ta_candidates", "daily_report_only",
                    "needs_manual_confirmation", "forbidden_word_scan"):
            assert key in briefing

    def test_forbidden_word_scan_self_check(self):
        briefing = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)
        assert briefing["forbidden_word_scan"]["found"] is False

    def test_json_serialisable(self):
        json.dumps(build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT))


class TestPostMarketFixtureRouting:
    """Post-market fixture: 1 gain / 1 loss holdings, 1 report with gaps."""

    def test_today_performance_counts(self):
        briefing = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)
        perf = briefing["today_performance"]
        assert perf["holdings_count"] == 2
        assert perf["gain_count"] == 1   # 600584 +3.39%
        assert perf["loss_count"] == 1   # 603629 -3.99%
        assert perf["no_quote_count"] == 0
        assert perf["data_status"] == "fresh"

    def test_report_data_gaps_aggregated(self):
        briefing = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)
        gaps = briefing["report_data_gaps"]
        assert gaps["scanned_report_count"] == 2
        assert gaps["affected_report_count"] == 1
        assert gaps["total_severe_blockers"] == 2
        assert gaps["summary_level"] == "warning"
        assert gaps["field_counts"]["individual_fund_flow"] == 1

    def test_manual_confirmation_routed_from_data_gaps(self):
        briefing = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)
        manual = briefing["needs_manual_confirmation"]
        assert len(manual) == 1
        assert manual[0]["symbol"] == "603629.SH"
        assert "individual_fund_flow" in manual[0]["fields"]
        assert manual[0]["reason"]  # explicit reason present

    def test_tomorrow_ta_candidates_carry_reason_call_ta(self):
        briefing = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)
        ta = briefing["tomorrow_ta_candidates"]
        assert len(ta) == 1
        assert ta[0]["symbol"] == "688256.SH"
        assert ta[0]["reason_call_ta"]

    def test_headline_mentions_holdings_and_gaps(self):
        briefing = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)
        headline = briefing["headline"]
        assert "持仓 2 只" in headline
        assert "报告缺口 2 项" in headline


# ──────────────────────────────────────────────────────────────────────────────
# Empty / degraded context
# ──────────────────────────────────────────────────────────────────────────────

class TestEmptyAndDegradedContext:

    def test_empty_context_does_not_raise(self):
        briefing = build_pre_market_briefing({})
        assert briefing["briefing_type"] == BRIEFING_PRE_MARKET
        assert briefing["ta_to_schedule"] == []
        assert briefing["daily_report_only"] == []
        assert briefing["needs_manual_confirmation"] == []
        assert briefing["forbidden_word_scan"]["found"] is False

    def test_empty_context_post_market(self):
        briefing = build_post_market_briefing({})
        assert briefing["today_performance"]["holdings_count"] == 0
        assert "无持仓" in briefing["today_performance"]["note"]
        assert briefing["report_data_gaps"]["scanned_report_count"] == 0

    def test_missing_controller_hints_degrades_gracefully(self):
        ctx = {"previous_trade_date": TODAY, "is_trading_day": True}
        briefing = build_pre_market_briefing(ctx)
        assert briefing["ta_to_schedule"] == []

    def test_unknown_candidate_type_falls_back_to_full_ta(self):
        ctx = {
            "controller_hints": {
                "needs_ta": [{
                    "symbol": "000001.SZ",
                    "name": "平安银行",
                    "origin": "tradeflow_candidates",
                    "reason": "未知类型",
                    "source": "pending_ta_required",
                    "as_of": TODAY,
                }],
            },
            "tradeflow_candidates": {
                "items": [{"symbol": "000001.SZ", "candidate_type": "UNKNOWN_TYPE"}]
            },
        }
        briefing = build_pre_market_briefing(ctx)
        item = briefing["ta_to_schedule"][0]
        # 未知 candidate_type → FULL_TA，需人工确认（PERF-004 门禁）
        assert item["suggested_profile"] == PROFILE_FULL_TA
        assert item["requires_confirmation"] is True


# ──────────────────────────────────────────────────────────────────────────────
# dry_run_all_fixtures entry point
# ──────────────────────────────────────────────────────────────────────────────

class TestDryRunAllFixtures:

    def test_returns_pre_and_post(self):
        report = dry_run_all_fixtures()
        assert report["pre_market"]["briefing_type"] == BRIEFING_PRE_MARKET
        assert report["post_market"]["briefing_type"] == BRIEFING_POST_MARKET

    def test_summary_counts(self):
        report = dry_run_all_fixtures()
        s = report["summary"]
        assert s["schema_version"] == BRIEFING_SCHEMA_VERSION
        assert s["pre_ta_count"] == 2
        assert s["pre_daily_count"] == 1
        assert s["post_ta_count"] == 1
        assert s["post_manual_count"] == 1

    def test_summary_forbidden_scan_passed(self):
        report = dry_run_all_fixtures()
        assert report["summary"]["forbidden_word_scan_passed"] is True

    def test_json_serialisable(self):
        json.dumps(dry_run_all_fixtures())


# ──────────────────────────────────────────────────────────────────────────────
# Service wrapper — READ-ONLY / FAST_RADAR contract
# ──────────────────────────────────────────────────────────────────────────────

class TestServiceWrapperContract:

    def test_pre_market_service_reads_real_context(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        # No holdings / observation / candidates → empty but stable briefing.
        briefing = build_pre_market_briefing_dry_run(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert briefing["briefing_type"] == BRIEFING_PRE_MARKET
        assert briefing["ta_to_schedule"] == []
        assert briefing["read_only"] is True
        assert briefing["forbidden_word_scan"]["found"] is False

    def test_post_market_service_reads_real_context(
        self, tmp_sqla_session, tmp_tf_db
    ):
        briefing = build_post_market_briefing_dry_run(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert briefing["briefing_type"] == BRIEFING_POST_MARKET
        assert briefing["today_performance"]["holdings_count"] == 0
        # FAST_RADAR runtime tier metadata attached.
        meta = briefing["runtime_tier_meta"]
        assert meta["runtime_tier"] == RuntimeTier.FAST_RADAR.value
        assert meta["llm_allowed"] is False

    def test_service_does_not_write_db(
        self, tmp_sqla_session, tmp_tf_db
    ):
        """READ-ONLY contract: running the dry-run must not create report rows."""
        from api.database import ReportDB
        before = tmp_sqla_session.query(ReportDB).count()
        build_pre_market_briefing_dry_run(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        build_post_market_briefing_dry_run(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        after = tmp_sqla_session.query(ReportDB).count()
        assert before == after, "briefing dry-run wrote to the DB"

    def test_service_resilient_to_context_failure(
        self, tmp_sqla_session, tmp_tf_db, monkeypatch
    ):
        # Force the IC context builder to blow up — service must degrade.
        from api.services import controller_briefing_service as svc
        def _boom(*a, **k):
            raise RuntimeError("context exploded")
        monkeypatch.setattr(svc, "get_investment_controller_context", _boom)
        briefing = build_pre_market_briefing_dry_run(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert briefing["briefing_type"] == BRIEFING_PRE_MARKET
        assert briefing["forbidden_word_scan"]["found"] is False


# ──────────────────────────────────────────────────────────────────────────────
# run_briefing_fixtures helper
# ──────────────────────────────────────────────────────────────────────────────

class TestRunBriefingFixtures:

    def test_both_returns_summary(self):
        report = run_briefing_fixtures(kind="both")
        assert "summary" in report
        assert report["summary"]["forbidden_word_scan_passed"] is True
        assert report["runtime_tier_meta"]["runtime_tier"] == RuntimeTier.FAST_RADAR.value

    def test_pre_only(self):
        briefing = run_briefing_fixtures(kind="pre")
        assert briefing["briefing_type"] == BRIEFING_PRE_MARKET

    def test_post_only(self):
        briefing = run_briefing_fixtures(kind="post")
        assert briefing["briefing_type"] == BRIEFING_POST_MARKET

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            run_briefing_fixtures(kind="nope")  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# Runtime tier registration
# ──────────────────────────────────────────────────────────────────────────────

class TestRuntimeTierRegistration:

    def test_endpoint_registered_as_fast(self):
        assert (
            tradeflow_endpoint_tier("controller_briefing_dry_run")
            == RuntimeTier.FAST_RADAR
        )

    def test_allowed_briefing_types(self):
        assert set(ALLOWED_BRIEFING_TYPES) == {BRIEFING_PRE_MARKET, BRIEFING_POST_MARKET}


# ──────────────────────────────────────────────────────────────────────────────
# Cross-cutting: every fixture briefing passes the strong-word guard
# ──────────────────────────────────────────────────────────────────────────────

class TestFixturesPassStrongWordGuard:

    def test_pre_market_fixture_clean(self):
        scan = build_pre_market_briefing(PRE_MARKET_FIXTURE_CONTEXT)[
            "forbidden_word_scan"
        ]
        assert scan["found"] is False

    def test_post_market_fixture_clean(self):
        scan = build_post_market_briefing(POST_MARKET_FIXTURE_CONTEXT)[
            "forbidden_word_scan"
        ]
        assert scan["found"] is False
