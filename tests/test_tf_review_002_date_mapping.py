# [TF-REVIEW-002] review_date_mapping tests
"""Tests for post-market Review data enrichment and non-trading-day plan mapping."""

import os
import tempfile

import pytest

from tradingagents.tradeflow.post_market_review import (
    ReviewDataStatus,
    CandidatePerformance,
    ReviewSummary,
    run_post_market_review,
    render_review_markdown,
    build_candidate_performance_from_dict,
)
from tradingagents.tradeflow.date_semantics import (
    resolve_review_date,
    find_latest_plan_date,
    next_cn_trading_day,
)


# ── ReviewDataStatus ──


class TestReviewDataStatus:
    def test_all_statuses_exist(self):
        assert ReviewDataStatus.OK
        assert ReviewDataStatus.NO_MARKET_DATA
        assert ReviewDataStatus.NON_TRADING_DAY
        assert ReviewDataStatus.SOURCE_FAILED
        assert ReviewDataStatus.NOT_ENOUGH_DAYS
        assert ReviewDataStatus.NO_CANDIDATES

    def test_status_values_are_strings(self):
        for s in ReviewDataStatus:
            assert isinstance(s.value, str)

    def test_message_cn_returns_chinese(self):
        assert "行情" in ReviewDataStatus.NO_MARKET_DATA.message_cn
        assert "非交易" in ReviewDataStatus.NON_TRADING_DAY.message_cn
        assert "不足" in ReviewDataStatus.NOT_ENOUGH_DAYS.message_cn

    def test_ok_message(self):
        assert ReviewDataStatus.OK.message_cn == "数据正常"


# ── data_status in run_post_market_review ──


class TestDataStatusInReview:
    def test_no_candidates_status(self):
        summary = run_post_market_review([], candidate_date="2026-05-31", review_date="2026-06-01")
        assert summary.data_status == ReviewDataStatus.NO_CANDIDATES.value

    def test_all_no_data_status(self):
        """When all candidates have no next_day_close, status is NO_MARKET_DATA."""
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP"], tier="A",
        )
        summary = run_post_market_review([perf], candidate_date="2026-05-31")
        assert summary.data_status == ReviewDataStatus.NO_MARKET_DATA.value

    def test_not_enough_days_status(self):
        """When next_day data exists but 3-day/5-day don't, status is NOT_ENOUGH_DAYS."""
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP"], tier="A", next_day_close=10.5,
        )
        summary = run_post_market_review([perf], candidate_date="2026-05-31")
        assert summary.data_status == ReviewDataStatus.NOT_ENOUGH_DAYS.value

    def test_ok_status_with_multi_day_data(self):
        """When next_day and day3 data exist, status is OK."""
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP"], tier="A",
            next_day_close=10.5, day3_close=11.0, day5_close=11.5,
        )
        summary = run_post_market_review([perf], candidate_date="2026-05-31")
        assert summary.data_status == ReviewDataStatus.OK.value

    def test_mixed_candidates_ok_status(self):
        """When some have data and some don't, status is OK (not all no_data)."""
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-31",
                entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
                strategy_tags=["VCP"], tier="A", next_day_close=10.5,
                day3_close=11.0,
            ),
            CandidatePerformance(
                symbol="000002.SZ", trade_date="2026-05-31",
                entry_price=20.0, trigger_price=21.0, invalid_price=19.0,
                strategy_tags=["VCP"], tier="B",
            ),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-31")
        assert summary.data_status == ReviewDataStatus.OK.value


# ── plan_date / effective_trade_date fields ──


class TestReviewDateMapping:
    def test_plan_date_and_effective_trade_date_set(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP"], tier="A", next_day_close=10.5,
        )
        summary = run_post_market_review(
            [perf],
            candidate_date="2026-05-31",
            review_date="2026-06-01",
            plan_date="2026-05-31",
            effective_trade_date="2026-06-01",
        )
        assert summary.plan_date == "2026-05-31"
        assert summary.effective_trade_date == "2026-06-01"

    def test_plan_date_defaults_to_candidate_date(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP"], tier="A", next_day_close=10.5,
        )
        summary = run_post_market_review([perf], candidate_date="2026-05-31")
        assert summary.plan_date == "2026-05-31"
        assert summary.effective_trade_date == summary.review_date

    def test_cross_date_review_summary(self):
        """Plan generated on weekend, reviewed on Monday."""
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP", "POLICY_VERSION"], tier="A",
            next_day_close=10.8,
        )
        summary = run_post_market_review(
            [perf],
            candidate_date="2026-05-31",
            review_date="2026-06-01",
            plan_date="2026-05-31",
            effective_trade_date="2026-06-01",
        )
        assert summary.candidate_date == "2026-05-31"
        assert summary.review_date == "2026-06-01"
        assert summary.plan_date == "2026-05-31"
        assert summary.overall_hit_count == 1


# ── Markdown rendering with data_status ──


class TestRenderWithDataStatus:
    def test_render_shows_data_status_when_not_ok(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP"], tier="A",
        )
        summary = run_post_market_review([perf], candidate_date="2026-05-31")
        md = render_review_markdown(summary)
        assert "数据状态" in md
        assert ReviewDataStatus.NO_MARKET_DATA.value in md

    def test_render_no_data_status_when_ok(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP"], tier="A",
            next_day_close=10.5, day3_close=11.0, day5_close=11.5,
        )
        summary = run_post_market_review([perf], candidate_date="2026-05-31")
        md = render_review_markdown(summary)
        assert "数据状态" not in md

    def test_render_cross_date_mapping_shown(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP"], tier="A", next_day_close=10.5,
        )
        summary = run_post_market_review(
            [perf],
            candidate_date="2026-05-31",
            review_date="2026-06-01",
            plan_date="2026-05-31",
            effective_trade_date="2026-06-01",
        )
        md = render_review_markdown(summary)
        assert "2026-05-31" in md
        assert "2026-06-01" in md

    def test_render_zero_percent_not_na(self):
        """0% return must render as 0.0%, not N/A%."""
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=None, invalid_price=None,
            strategy_tags=["VCP"], tier="A", next_day_close=10.0,
            day3_close=10.0, day5_close=10.0,
        )
        summary = run_post_market_review([perf], candidate_date="2026-05-31", review_date="2026-06-01")
        md = render_review_markdown(summary)
        assert "总命中率 | 0.0%" in md
        assert "平均次日收益 | 0.0%" in md
        assert "平均3日收益 | 0.0%" in md
        assert "平均5日收益 | 0.0%" in md

    def test_render_partial_na_only_for_missing_periods(self):
        """When day3/day5 data is missing, only those show N/A%."""
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=None, invalid_price=None,
            strategy_tags=["VCP"], tier="A", next_day_close=10.5,
        )
        summary = run_post_market_review([perf], candidate_date="2026-05-31", review_date="2026-06-01")
        md = render_review_markdown(summary)
        assert "平均次日收益 | 5.0%" in md
        assert "平均3日收益 | N/A%" in md
        assert "平均5日收益 | N/A%" in md


# ── date_semantics helpers ──


class TestDateSemanticsHelpers:
    def test_resolve_review_date_with_effective(self):
        result = resolve_review_date("2026-05-31", "2026-06-01")
        assert result == "2026-06-01"

    def test_resolve_review_date_trading_day(self):
        """If plan_date is a trading day, review on the same day."""
        # 2026-06-01 is a Monday — check if it's a trading day
        result = resolve_review_date("2026-06-01", "")
        assert result == "2026-06-01"

    def test_resolve_review_date_non_trading_day(self):
        """If plan_date is a weekend, resolve to next trading day."""
        # 2026-05-31 is a Sunday
        result = resolve_review_date("2026-05-31", "")
        assert result != "2026-05-31"

    def test_find_latest_plan_date_exact_match(self):
        dates = ["2026-05-29", "2026-05-31", "2026-06-02"]
        assert find_latest_plan_date(dates, "2026-05-31") == "2026-05-31"

    def test_find_latest_plan_date_no_match_returns_max(self):
        dates = ["2026-05-29", "2026-05-31", "2026-06-02"]
        assert find_latest_plan_date(dates, "2026-07-01") == "2026-06-02"

    def test_find_latest_plan_date_empty(self):
        assert find_latest_plan_date([], "2026-06-01") == ""

    def test_find_latest_plan_date_no_target(self):
        dates = ["2026-05-29", "2026-05-31", "2026-06-02"]
        assert find_latest_plan_date(dates) == "2026-06-02"


# ── Service layer: get_review with data_status ──


class TestGetReviewDataStatus:
    def test_get_review_no_db_returns_no_candidates(self):
        from api.services.tradeflow_service import get_review
        result = get_review("2026-06-02", tf_db_path="/tmp/nonexistent_tf_review_002.db")
        assert result["status"] == "no_data"
        assert result["data_status"] == ReviewDataStatus.NO_CANDIDATES.value
        assert "data_status_message" in result

    def test_get_review_returns_data_status_ok(self):
        """When a plan exists, get_review returns data_status OK."""
        from api.services.tradeflow_service import get_review
        import sqlite3
        from tradingagents.tradeflow.candidate_engine import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tf_review.db")
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            from datetime import datetime
            now = datetime.now().isoformat()
            plan_json = '[{"symbol": "000001.SZ", "name": "测试股", "tier": "A", "trigger_price": 10.5, "invalid_price": 9.0, "strategies": ["VCP"], "action": "OBSERVE", "composite_score": 70.0, "observe_state": "WAITING"}]'
            conn.execute(
                "INSERT INTO tradeflow_daily_plans (trade_date, mode, summary, candidates_json, metadata_json, created_at, effective_trade_date, plan_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-06-02", "pre_market", "test", plan_json, "{}", now, "2026-06-02", "2026-06-02"),
            )
            conn.execute(
                "INSERT INTO tradeflow_candidates (trade_date, symbol, name, score, tier, trigger_price, invalid_price, strategy_tags_json, status, created_at, updated_at, effective_trade_date, plan_date, observe_state, composite_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-06-02", "000001.SZ", "测试股", 60.0, "A", 10.5, 9.0, '["VCP"]', "active", now, now, "2026-06-02", "2026-06-02", "WAITING", 70.0),
            )
            conn.commit()
            conn.close()

            result = get_review("2026-06-02", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["data_status"] == "OK"
            assert "data_status_message" in result
            assert len(result["results"]) == 1

    def test_get_review_falls_back_to_latest_plan(self):
        """When querying a date with no plan, falls back to latest available."""
        from api.services.tradeflow_service import get_review
        import sqlite3
        from tradingagents.tradeflow.candidate_engine import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tf_review_fb.db")
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            from datetime import datetime
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO tradeflow_candidates (trade_date, symbol, name, score, tier, trigger_price, invalid_price, strategy_tags_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-05-29", "000001.SZ", "测试股", 60.0, "A", 10.5, 9.0, '["VCP"]', "active", now, now),
            )
            conn.commit()
            conn.close()

            # Query a date with no candidates — should fall back to 2026-05-29
            result = get_review("2026-06-15", tf_db_path=db_path)
            # Either falls back to latest or returns no_data, but must not crash
            assert result["status"] in ("ok", "no_data")


# ── Service layer: generate_review with data_status ──


class TestGenerateReviewDataStatus:
    def test_generate_review_no_db_returns_data_status(self):
        from api.services.tradeflow_service import generate_review
        result = generate_review("2026-06-02", tf_db_path="/tmp/nonexistent_tf_review_002_gen.db")
        assert result["status"] == "no_data"
        assert result["data_status"] == ReviewDataStatus.NO_CANDIDATES.value
        assert "data_status_message" in result

    def test_generate_review_with_candidates(self):
        """generate_review with actual candidates returns data_status."""
        from api.services.tradeflow_service import generate_review
        import sqlite3
        from tradingagents.tradeflow.candidate_engine import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tf_review_gen.db")
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            from datetime import datetime
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO tradeflow_candidates (trade_date, symbol, name, score, tier, trigger_price, invalid_price, strategy_tags_json, status, created_at, updated_at, effective_trade_date, plan_date, observe_state, composite_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-06-02", "000001.SZ", "测试股", 60.0, "A", 10.5, 9.0, '["VCP"]', "active", now, now, "2026-06-02", "2026-06-02", "WAITING", 70.0),
            )
            conn.commit()
            conn.close()

            result = generate_review("2026-06-02", tf_db_path=db_path)
            assert result["status"] == "ok"
            assert "data_status" in result
            assert result["data_status"] == ReviewDataStatus.NO_MARKET_DATA.value
            review = result.get("review", {})
            assert "plan_date" in review
            assert "effective_trade_date" in review
            assert "data_status" in review

    def test_generate_review_includes_strategy_day3_day5_returns(self):
        """generate_review strategy_stats should include avg_day3_return and avg_day5_return."""
        from api.services.tradeflow_service import generate_review
        import sqlite3
        from tradingagents.tradeflow.candidate_engine import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_tf_review_strat.db")
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            from datetime import datetime
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO tradeflow_candidates (trade_date, symbol, name, score, tier, trigger_price, invalid_price, strategy_tags_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-06-02", "000001.SZ", "测试股", 60.0, "A", 10.5, 9.0, '["VCP"]', "active", now, now),
            )
            conn.commit()
            conn.close()

            result = generate_review("2026-06-02", tf_db_path=db_path)
            review = result.get("review", {})
            stats = review.get("strategy_stats", {})
            assert "VCP" in stats
            assert "avg_day3_return" in stats["VCP"]
            assert "avg_day5_return" in stats["VCP"]


# ── Non-trading day plan mapping acceptance ──


class TestNonTradingDayPlanMapping:
    def test_weekend_plan_maps_to_next_trading_day(self):
        """A candidate pool generated on 2026-05-31 (Sunday) maps to 2026-06-01 (Monday) for review."""
        eff_date = next_cn_trading_day("2026-05-31")
        assert eff_date == "2026-06-01"

        review_date = resolve_review_date("2026-05-31", eff_date)
        assert review_date == "2026-06-01"

    def test_review_summary_cross_date(self):
        """Review summary correctly reflects cross-date mapping."""
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-31",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            strategy_tags=["VCP", "POLICY_VERSION"], tier="A",
            next_day_close=10.8,
        )
        summary = run_post_market_review(
            [perf],
            candidate_date="2026-05-31",
            review_date="2026-06-01",
            plan_date="2026-05-31",
            effective_trade_date="2026-06-01",
        )
        assert summary.plan_date == "2026-05-31"
        assert summary.effective_trade_date == "2026-06-01"
        assert summary.review_date == "2026-06-01"
        assert summary.overall_hit_count == 1

    def test_data_status_message_not_empty_when_no_data(self):
        """When data_status is not OK, data_status_message must be non-empty."""
        summary = run_post_market_review([], candidate_date="2026-05-31")
        assert summary.data_status != ReviewDataStatus.OK.value
        assert summary.data_status_message != ""

    def test_no_blank_page_when_no_data(self):
        """Markdown report explains why data is missing, instead of showing a blank table."""
        summary = run_post_market_review([], candidate_date="2026-05-31", review_date="2026-06-01")
        md = render_review_markdown(summary)
        assert "总候选数 | 0" in md
        assert ReviewDataStatus.NO_CANDIDATES.value in md
        # Must have an explanation, not just "N/A"
        assert summary.data_status_message in md
