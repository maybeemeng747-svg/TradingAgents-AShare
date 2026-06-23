# [TRACK-005] post_market_tracking_review
"""Tests for TRACK-005: 盘后复盘摘要与次日计划写回跟踪看板.

Covers:
- Pure engine: build_post_market_tracking_review across all scenarios.
- Empty state: no holdings, no observation, no candidates → NO_DATA with clear message.
- Non-trading day: NON_TRADING_DAY status; plan effective next trading day.
- Holdings review: large drop / broke key level / deep loss / near key level /
  no analysis / normal monitoring.
- Observation review: in_entry_zone / near_entry / invalidated / missed_entry /
  ta_required / watching / data_missing.
- Candidate pool review: triggered / eliminated / entered_observation / queued.
- Tomorrow focus aggregation & priority sorting.
- Forbidden strong words guard: no 立即买入/重仓/清仓/满仓/梭哈 anywhere.
- data_status transitions: OK / NON_TRADING_DAY / NO_DATA / PARTIAL_DATA.
- Service-level _build_review_summary defensive wrapper.
"""

from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.post_market_tracking_review import (
    build_post_market_tracking_review,
    review_summary_has_forbidden_words,
    DATA_STATUS_NON_TRADING_DAY,
    DATA_STATUS_NO_DATA,
    DATA_STATUS_OK,
    DATA_STATUS_PARTIAL_DATA,
    TAG_CANDIDATE_ELIMINATED,
    TAG_CANDIDATE_TRIGGERED,
    TAG_CONTINUE_MONITORING,
    TAG_MARK_INVALIDATED,
    TAG_NEEDS_ATTENTION,
    TAG_RERUN_TA,
    TAG_REVIEW_TA,
    TAG_WAIT_TRIGGER,
    TAG_WATCH_KEY_LEVEL,
)
from tradingagents.tradeflow.observation_state_engine import FORBIDDEN_STRONG_WORDS


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Data status: empty / non-trading day
# ---------------------------------------------------------------------------


class TestDataStatusEmptyAndNonTradingDay:
    def test_all_empty_on_trading_day_returns_no_data(self):
        r = build_post_market_tracking_review(
            holdings=[],
            observation_items=[],
            tradeflow_review=None,
            is_trading_day=True,
            review_date="2026-06-23",
            as_of=_now_str(),
        )
        assert r["data_status"] == DATA_STATUS_NO_DATA
        # "空 Review 时能解释为何无数据"
        assert r["data_status_message"]
        assert "无" in r["data_status_message"] or "空" in r["data_status_message"]
        assert r["holdings_review"] == []
        assert r["observation_review"] == []
        assert r["candidate_pool_review"] == []
        assert r["tomorrow_focus"] == []
        assert r["has_tradeflow_review"] is False
        assert r["tradeflow_review_status"] == "skipped"

    def test_non_trading_day_returns_non_trading_day_status(self):
        """验收：非交易日生成的计划可在下一交易日复盘."""
        r = build_post_market_tracking_review(
            holdings=[],
            observation_items=[],
            tradeflow_review=None,
            is_trading_day=False,
            review_date="2026-06-21",
            as_of=_now_str(),
        )
        assert r["data_status"] == DATA_STATUS_NON_TRADING_DAY
        assert "非交易日" in r["data_status_message"]
        assert "下一交易日" in r["data_status_message"]

    def test_non_trading_day_with_holdings_still_builds_review(self):
        """Non-trading day should still surface holdings/observation review
        (using last-known data) but mark data_status=NON_TRADING_DAY."""
        holdings = [
            {"symbol": "600000.SH", "name": "浦发银行", "live_price": 10.0,
             "price_change_pct": None, "average_cost": 11.0, "floating_pnl_pct": -9.0,
             "analysis": None}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings,
            observation_items=[],
            tradeflow_review=None,
            is_trading_day=False,
            review_date="2026-06-21",
            as_of=_now_str(),
        )
        assert r["data_status"] == DATA_STATUS_NON_TRADING_DAY
        assert len(r["holdings_review"]) == 1

    def test_no_data_tradeflow_review_yields_no_candidates(self):
        """空 Review (tradeflow returns no_data) → candidate_pool empty + has_tradeflow_review False."""
        r = build_post_market_tracking_review(
            holdings=[],
            observation_items=[],
            tradeflow_review={"status": "no_data", "data_status": "NO_CANDIDATES"},
            is_trading_day=True,
            review_date="2026-06-23",
            as_of=_now_str(),
        )
        assert r["has_tradeflow_review"] is False
        assert r["tradeflow_review_status"] == "no_data"
        assert r["candidate_pool_review"] == []
        # Still NO_DATA because all sources empty
        assert r["data_status"] == DATA_STATUS_NO_DATA

    def test_partial_data_when_holdings_have_no_quotes_on_trading_day(self):
        holdings = [
            {"symbol": "A", "name": "A", "live_price": None,
             "price_change_pct": None, "analysis": None}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings,
            observation_items=[],
            tradeflow_review=None,
            is_trading_day=True,
            review_date="2026-06-23",
            as_of=_now_str(),
        )
        assert r["data_status"] == DATA_STATUS_PARTIAL_DATA


# ---------------------------------------------------------------------------
# Holdings review
# ---------------------------------------------------------------------------


class TestHoldingsReview:
    def test_large_drop_marks_needs_attention(self):
        holdings = [
            {"symbol": "600000.SH", "name": "浦发银行", "live_price": 9.5,
             "price_change_pct": -4.0, "average_cost": 10.0, "floating_pnl_pct": -5.0,
             "analysis": {"low_price": 9.0, "action_label": "HOLD"}}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=[], tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        h = r["holdings_review"][0]
        assert h["needs_attention"] is True
        assert h["tomorrow_focus_tag"] == TAG_NEEDS_ATTENTION
        assert h["deviated_from_ta_plan"] is True
        assert "浦发银行" in h["review_note"]

    def test_broke_key_level_marks_needs_attention(self):
        holdings = [
            {"symbol": "600000.SH", "name": "浦发银行", "live_price": 8.8,
             "price_change_pct": -2.0, "average_cost": 10.0, "floating_pnl_pct": -12.0,
             "analysis": {"low_price": 9.0}}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=[], tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        h = r["holdings_review"][0]
        assert h["broke_key_level"] is True
        assert h["needs_attention"] is True

    def test_deep_loss_without_broke_level(self):
        holdings = [
            {"symbol": "A", "name": "A", "live_price": 9.0,
             "price_change_pct": -0.5, "average_cost": 11.0, "floating_pnl_pct": -18.0,
             "analysis": {"low_price": 7.0}}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=[], tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        h = r["holdings_review"][0]
        assert h["needs_attention"] is True
        assert h["deviated_from_ta_plan"] is True
        assert "偏离" in h["review_note"]

    def test_near_key_level(self):
        holdings = [
            {"symbol": "A", "name": "A", "live_price": 9.85,
             "price_change_pct": -0.5, "average_cost": 10.0, "floating_pnl_pct": -1.5,
             "analysis": {"low_price": 9.7}}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=[], tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        h = r["holdings_review"][0]
        assert h["tomorrow_focus_tag"] == TAG_WATCH_KEY_LEVEL
        assert h["needs_attention"] is False
        assert "关键位" in h["review_note"]

    def test_no_analysis_yields_review_ta_tag(self):
        holdings = [
            {"symbol": "A", "name": "A", "live_price": 10.0,
             "price_change_pct": 0.5, "analysis": None}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=[], tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        h = r["holdings_review"][0]
        assert h["has_analysis"] is False
        assert h["tomorrow_focus_tag"] == TAG_REVIEW_TA

    def test_normal_monitoring(self):
        holdings = [
            {"symbol": "A", "name": "A", "live_price": 11.0,
             "price_change_pct": 1.2, "average_cost": 10.0, "floating_pnl_pct": 10.0,
             "analysis": {"low_price": 9.0}}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=[], tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        h = r["holdings_review"][0]
        assert h["tomorrow_focus_tag"] == TAG_CONTINUE_MONITORING
        assert h["needs_attention"] is False


# ---------------------------------------------------------------------------
# Observation review (reuses TRACK-004 state engine)
# ---------------------------------------------------------------------------


class TestObservationReview:
    def test_in_entry_zone(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.0,
                "live_price": 11.0}]
        r = build_post_market_tracking_review(
            holdings=[], observation_items=obs, tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        o = r["observation_review"][0]
        assert o["near_entry"] is True
        assert o["invalidated"] is False
        assert o["tomorrow_focus_tag"] == TAG_WAIT_TRIGGER

    def test_invalidated(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.5,
                "live_price": 9.0}]
        r = build_post_market_tracking_review(
            holdings=[], observation_items=obs, tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        o = r["observation_review"][0]
        assert o["invalidated"] is True
        assert o["tomorrow_focus_tag"] == TAG_MARK_INVALIDATED

    def test_ta_required_yields_rerun_ta(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "ta_required",
                "entry_low": 0.0, "entry_high": 0.0, "invalid_price": 0.0,
                "live_price": 15.0}]
        r = build_post_market_tracking_review(
            holdings=[], observation_items=obs, tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        o = r["observation_review"][0]
        assert o["needs_re_ta"] is True
        assert o["tomorrow_focus_tag"] == TAG_RERUN_TA

    def test_missed_entry_yields_rerun_ta(self):
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 10.5, "invalid_price": 9.0,
                "live_price": 12.0}]  # > entry_high * 1.05
        r = build_post_market_tracking_review(
            holdings=[], observation_items=obs, tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        o = r["observation_review"][0]
        assert o["missed_entry"] is True
        assert o["needs_re_ta"] is True

    def test_data_missing_on_trading_day(self):
        """数据缺失时不误判为可入场（硬约束）."""
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.0,
                "live_price": None}]
        r = build_post_market_tracking_review(
            holdings=[], observation_items=obs, tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        o = r["observation_review"][0]
        assert o["state"] == "data_missing"
        assert o["near_entry"] is False
        assert o["invalidated"] is False


# ---------------------------------------------------------------------------
# Candidate pool review (TF-REVIEW-003 integration)
# ---------------------------------------------------------------------------


class TestCandidatePoolReview:
    def _tf_review(self, results):
        return {
            "status": "ok",
            "trade_date": "2026-06-23",
            "results": results,
            "data_status": "OK",
            "plan_date": "2026-06-23",
            "effective_trade_date": "2026-06-23",
        }

    def test_triggered_candidate(self):
        review = self._tf_review([
            {"symbol": "600519.SH", "name": "贵州茅台", "observe_state": "TRIGGERED",
             "plan_action": "OBSERVE", "candidate_type": "POLICY_AMBUSH",
             "tomorrow_focus": "已触发且命中，关注次日是否站稳触发价",
             "hit_type": "policy_hit", "downgrade_reason": "",
             "evidence_needed": []}
        ])
        r = build_post_market_tracking_review(
            holdings=[], observation_items=[], tradeflow_review=review,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        assert r["has_tradeflow_review"] is True
        c = r["candidate_pool_review"][0]
        assert c["triggered"] is True
        assert c["eliminated"] is False
        assert c["tomorrow_focus_tag"] == TAG_CANDIDATE_TRIGGERED
        assert "已触发" in c["tomorrow_focus"]

    def test_eliminated_candidate_via_plan_action(self):
        review = self._tf_review([
            {"symbol": "A", "name": "A", "observe_state": "WAITING",
             "plan_action": "REMOVE_FROM_WATCH", "candidate_type": "",
             "tomorrow_focus": "", "downgrade_reason": "信号偏弱"}
        ])
        r = build_post_market_tracking_review(
            holdings=[], observation_items=[], tradeflow_review=review,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        c = r["candidate_pool_review"][0]
        assert c["eliminated"] is True
        assert c["tomorrow_focus_tag"] == TAG_CANDIDATE_ELIMINATED

    def test_eliminated_candidate_via_invalidated_state(self):
        review = self._tf_review([
            {"symbol": "A", "name": "A", "observe_state": "INVALIDATED",
             "plan_action": "OBSERVE", "candidate_type": ""}
        ])
        r = build_post_market_tracking_review(
            holdings=[], observation_items=[], tradeflow_review=review,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        assert r["candidate_pool_review"][0]["eliminated"] is True

    def test_entered_observation_cross_reference(self):
        review = self._tf_review([
            {"symbol": "000001.SZ", "name": "平安银行", "observe_state": "WAITING",
             "plan_action": "OBSERVE", "candidate_type": ""}
        ])
        obs = [{"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
                "entry_low": 10.0, "entry_high": 12.0, "live_price": 11.0}]
        r = build_post_market_tracking_review(
            holdings=[], observation_items=obs, tradeflow_review=review,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        c = r["candidate_pool_review"][0]
        assert c["entered_observation"] is True

    def test_candidate_not_in_observation(self):
        review = self._tf_review([
            {"symbol": "999999.SH", "name": "X", "observe_state": "WAITING",
             "plan_action": "OBSERVE", "candidate_type": ""}
        ])
        r = build_post_market_tracking_review(
            holdings=[], observation_items=[], tradeflow_review=review,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        assert r["candidate_pool_review"][0]["entered_observation"] is False

    def test_empty_results_treated_as_no_data(self):
        review = {"status": "ok", "results": [], "data_status": "OK"}
        r = build_post_market_tracking_review(
            holdings=[], observation_items=[], tradeflow_review=review,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        assert r["has_tradeflow_review"] is False
        assert r["tradeflow_review_status"] == "no_data"


# ---------------------------------------------------------------------------
# Tomorrow focus aggregation
# ---------------------------------------------------------------------------


class TestTomorrowFocusAggregation:
    def test_priority_sorting(self):
        holdings = [
            # P0: large drop
            {"symbol": "RISK", "name": "RISK", "live_price": 9.0,
             "price_change_pct": -5.0, "analysis": {"low_price": 8.0}},
            # P2: no analysis
            {"symbol": "NO_TA", "name": "NO_TA", "live_price": 10.0,
             "price_change_pct": 0.0, "analysis": None},
        ]
        obs = [
            # P0: in entry zone
            {"symbol": "IN", "name": "IN", "status": "watching",
             "entry_low": 10.0, "entry_high": 12.0, "live_price": 11.0},
            # P2: invalidated
            {"symbol": "INV", "name": "INV", "status": "watching",
             "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.5,
             "live_price": 9.0},
        ]
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=obs, tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        priorities = [t["priority"] for t in r["tomorrow_focus"]]
        assert priorities == sorted(priorities)
        # All entries carry source + as_of
        for t in r["tomorrow_focus"]:
            assert t["source"] == "post_market_tracking_review"
            assert t["as_of"]
            assert t["reason"]
            assert t["suggested_next_step"]

    def test_empty_when_no_signal(self):
        holdings = [
            {"symbol": "A", "name": "A", "live_price": 11.0,
             "price_change_pct": 0.5, "average_cost": 10.0,
             "floating_pnl_pct": 10.0, "analysis": {"low_price": 9.0}}
        ]
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=[], tradeflow_review=None,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        # Normal monitoring holding does NOT produce a tomorrow_focus entry
        assert r["tomorrow_focus"] == []


# ---------------------------------------------------------------------------
# Forbidden words guard (acceptance: 输出不含强买卖词)
# ---------------------------------------------------------------------------


class TestForbiddenWordsGuard:
    def _full_review(self):
        holdings = [
            {"symbol": "600000.SH", "name": "浦发银行", "live_price": 9.0,
             "price_change_pct": -5.0, "average_cost": 10.0,
             "floating_pnl_pct": -10.0, "analysis": {"low_price": 9.5}},
            {"symbol": "NO_TA", "name": "NO_TA", "live_price": 10.0,
             "price_change_pct": 0.0, "analysis": None},
        ]
        obs = [
            {"symbol": "000001.SZ", "name": "平安银行", "status": "watching",
             "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.0,
             "live_price": 11.0},
            {"symbol": "000002.SZ", "name": "万科A", "status": "watching",
             "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.5,
             "live_price": 9.0},
        ]
        tf = {
            "status": "ok",
            "results": [
                {"symbol": "600519.SH", "name": "贵州茅台", "observe_state": "TRIGGERED",
                 "plan_action": "OBSERVE", "candidate_type": "POLICY_AMBUSH",
                 "tomorrow_focus": "已触发且命中，关注次日是否站稳触发价",
                 "downgrade_reason": "", "evidence_needed": []},
                {"symbol": "999999.SH", "name": "X", "observe_state": "INVALIDATED",
                 "plan_action": "REMOVE_FROM_WATCH", "candidate_type": "",
                 "tomorrow_focus": "已失效，建议移出观察或降低权重",
                 "downgrade_reason": "信号偏弱"},
            ],
        }
        return build_post_market_tracking_review(
            holdings=holdings, observation_items=obs, tradeflow_review=tf,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )

    def test_no_forbidden_strong_words_in_summary(self):
        r = self._full_review()
        hits = review_summary_has_forbidden_words(r)
        assert hits == [], f"forbidden words found: {hits}"

    def test_forbidden_detector_catches_injected_word(self):
        r = self._full_review()
        r["holdings_review"][0]["review_note"] = "建议立即买入"  # inject
        hits = review_summary_has_forbidden_words(r)
        assert "立即买入" in hits

    def test_all_forbidden_words_listed_in_engine(self):
        # Ensure the guard list is non-empty and stable
        assert "立即买入" in FORBIDDEN_STRONG_WORDS
        assert "重仓" in FORBIDDEN_STRONG_WORDS
        assert "清仓" in FORBIDDEN_STRONG_WORDS
        assert "满仓" in FORBIDDEN_STRONG_WORDS
        assert "梭哈" in FORBIDDEN_STRONG_WORDS


# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------


class TestSummaryCounts:
    def test_counts_reflect_inputs(self):
        holdings = [
            {"symbol": "A", "name": "A", "live_price": 9.0,
             "price_change_pct": -5.0, "analysis": {"low_price": 9.5}},  # risk + has_analysis
            {"symbol": "B", "name": "B", "live_price": 10.0,
             "price_change_pct": 0.0, "analysis": None},  # no analysis
        ]
        obs = [
            {"symbol": "IN", "name": "IN", "status": "watching",
             "entry_low": 10.0, "entry_high": 12.0, "live_price": 11.0},  # near_entry
            {"symbol": "INV", "name": "INV", "status": "watching",
             "entry_low": 10.0, "entry_high": 12.0, "invalid_price": 9.5,
             "live_price": 9.0},  # invalidated
        ]
        tf = {
            "status": "ok",
            "results": [
                {"symbol": "T", "name": "T", "observe_state": "TRIGGERED",
                 "plan_action": "OBSERVE"},
                {"symbol": "E", "name": "E", "observe_state": "INVALIDATED",
                 "plan_action": "REMOVE_FROM_WATCH"},
            ],
        }
        r = build_post_market_tracking_review(
            holdings=holdings, observation_items=obs, tradeflow_review=tf,
            is_trading_day=True, review_date="2026-06-23", as_of=_now_str(),
        )
        c = r["summary_counts"]
        assert c["holdings_total"] == 2
        assert c["holdings_with_analysis"] == 1
        assert c["holdings_risk"] == 1
        assert c["holdings_no_analysis"] == 1
        assert c["observation_total"] == 2
        assert c["observation_near_entry"] == 1
        assert c["observation_invalidated"] == 1
        assert c["candidates_total"] == 2
        assert c["candidates_triggered"] == 1
        assert c["candidates_eliminated"] == 1


# ---------------------------------------------------------------------------
# Service-level defensive wrapper
# ---------------------------------------------------------------------------


class TestServiceBuildReviewSummary:
    """Tests for api.services.tracking_board_service._build_review_summary."""

    def test_returns_stable_summary_on_empty_inputs(self):
        from api.services.tracking_board_service import _build_review_summary
        from datetime import datetime
        r = _build_review_summary(
            holdings=[], observation_items=[],
            previous_trade_date="2026-06-22",
            is_trading_day=True, now=datetime.now(),
        )
        assert r["review_date"] == "2026-06-22"
        assert r["data_status"] in (
            DATA_STATUS_NO_DATA, DATA_STATUS_OK, DATA_STATUS_PARTIAL_DATA,
            DATA_STATUS_NON_TRADING_DAY,
        )
        assert "summary_counts" in r
        assert r["summary_counts"]["holdings_total"] == 0

    def test_tradeflow_fetch_failure_degrades_gracefully(self, monkeypatch):
        """If get_review raises, summary still builds with has_tradeflow_review=False."""
        from api.services import tracking_board_service as svc
        from datetime import datetime

        def _boom(_date):
            raise RuntimeError("db locked")

        monkeypatch.setattr(
            "api.services.tradeflow_service.get_review", _boom, raising=False
        )
        r = svc._build_review_summary(
            holdings=[], observation_items=[],
            previous_trade_date="2026-06-22",
            is_trading_day=True, now=datetime.now(),
        )
        assert r["has_tradeflow_review"] is False
        assert r["tradeflow_review_status"] in ("skipped", "no_data")

    def test_engine_failure_returns_skipped_payload(self, monkeypatch):
        """If the pure engine raises, wrapper returns a stable NO_DATA payload."""
        from api.services import tracking_board_service as svc
        from datetime import datetime

        def _boom(**kwargs):
            raise RuntimeError("engine broken")

        # Patch the name as imported in tracking_board_service (not the source module)
        monkeypatch.setattr(
            "api.services.tracking_board_service.build_post_market_tracking_review",
            _boom,
        )
        r = svc._build_review_summary(
            holdings=[{"symbol": "A"}], observation_items=[],
            previous_trade_date="2026-06-22",
            is_trading_day=True, now=datetime.now(),
        )
        assert r["data_status"] == DATA_STATUS_NO_DATA
        assert r["summary_counts"]["holdings_total"] == 1
        assert r["tomorrow_focus"] == []

    def test_non_trading_day_path_through_service(self):
        from api.services.tracking_board_service import _build_review_summary
        from datetime import datetime
        r = _build_review_summary(
            holdings=[], observation_items=[],
            previous_trade_date="2026-06-21",
            is_trading_day=False, now=datetime.now(),
        )
        assert r["data_status"] == DATA_STATUS_NON_TRADING_DAY
        assert "非交易日" in r["data_status_message"]
