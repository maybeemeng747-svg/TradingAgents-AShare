# [T-005] review_fixture_replay tests
"""Tests for TradeFlow P3 post-market review fixture replay."""

import os
import tempfile
from datetime import datetime

import pytest

from tradingagents.tradeflow.review_fixture_replay import (
    ALL_FIXTURE_IDS,
    ReviewFixtureResult,
    get_fixture,
    replay_fixture,
    replay_all,
    validate_fixture_expectations,
    validate_no_forbidden_words,
    _summary_to_result,
)
from tradingagents.tradeflow.post_market_review import (
    CandidatePerformance,
    ReviewSummary,
    StrategyStats,
    run_post_market_review,
    render_review_markdown,
    save_review_report,
    build_candidate_performance_from_dict,
)
from tradingagents.tradeflow.schemas import FORBIDDEN_WORDS


# ── Fixture Existence ──


class TestFixtureExistence:
    @pytest.mark.parametrize("fid", ALL_FIXTURE_IDS)
    def test_fixture_exists(self, fid):
        fixture = get_fixture(fid)
        assert fixture is not None
        assert fixture["fixture_id"] == fid

    @pytest.mark.parametrize("fid", ALL_FIXTURE_IDS)
    def test_fixture_has_performances(self, fid):
        fixture = get_fixture(fid)
        assert "performances" in fixture
        assert len(fixture["performances"]) > 0

    @pytest.mark.parametrize("fid", ALL_FIXTURE_IDS)
    def test_fixture_has_dates(self, fid):
        fixture = get_fixture(fid)
        assert "candidate_date" in fixture
        assert "review_date" in fixture
        assert len(fixture["candidate_date"]) == 10
        assert len(fixture["review_date"]) == 10

    @pytest.mark.parametrize("fid", ALL_FIXTURE_IDS)
    def test_fixture_has_expected(self, fid):
        fixture = get_fixture(fid)
        assert "expected" in fixture
        assert "hit_count" in fixture["expected"]

    def test_all_fixture_ids_count(self):
        assert len(ALL_FIXTURE_IDS) == 7

    def test_unknown_fixture_returns_none(self):
        assert get_fixture("nonexistent") is None

    @pytest.mark.parametrize("fid", ALL_FIXTURE_IDS)
    def test_performance_symbols_unique_per_fixture(self, fid):
        fixture = get_fixture(fid)
        symbols = [p.symbol for p in fixture["performances"]]
        assert len(symbols) == len(set(symbols))


# ── Multi Strategy Hit ──


class TestMultiStrategyHit:
    def test_hit_count(self):
        result = replay_fixture("multi_strategy_hit")
        assert result.hit_count == 2

    def test_miss_count(self):
        result = replay_fixture("multi_strategy_hit")
        assert result.miss_count == 0

    def test_invalidated_count(self):
        result = replay_fixture("multi_strategy_hit")
        assert result.invalidated_count == 0

    def test_strategy_stats(self):
        result = replay_fixture("multi_strategy_hit")
        assert set(result.strategy_stats_keys) == {"VCP", "POLICY_VERSION", "FUND_FLOW_ANOMALY"}

    def test_tier_stats(self):
        result = replay_fixture("multi_strategy_hit")
        assert result.tier_stats_keys == ["A"]

    def test_no_removal_reasons(self):
        result = replay_fixture("multi_strategy_hit")
        assert result.removal_reasons == []

    def test_avg_return(self):
        result = replay_fixture("multi_strategy_hit")
        assert result.avg_next_day_return is not None
        assert result.avg_next_day_return > 0

    def test_hit_rate(self):
        result = replay_fixture("multi_strategy_hit")
        assert result.overall_hit_rate == 100.0

    def test_no_error(self):
        result = replay_fixture("multi_strategy_hit")
        assert result.error == ""


# ── Single Miss ──


class TestSingleMiss:
    def test_hit_count(self):
        result = replay_fixture("single_miss")
        assert result.hit_count == 0

    def test_miss_count(self):
        result = replay_fixture("single_miss")
        assert result.miss_count == 1

    def test_strategy_stats(self):
        result = replay_fixture("single_miss")
        assert result.strategy_stats_keys == ["VCP"]

    def test_tier_stats(self):
        result = replay_fixture("single_miss")
        assert result.tier_stats_keys == ["B"]

    def test_false_positive_rate(self):
        result = replay_fixture("single_miss")
        assert result.overall_false_positive_rate == 100.0

    def test_avg_return_negative(self):
        result = replay_fixture("single_miss")
        assert result.avg_next_day_return is not None
        assert result.avg_next_day_return < 0


# ── Invalidated Break ──


class TestInvalidatedBreak:
    def test_invalidated_count(self):
        result = replay_fixture("invalidated_break")
        assert result.invalidated_count == 1

    def test_miss_count(self):
        result = replay_fixture("invalidated_break")
        assert result.miss_count == 1

    def test_has_removal_reason(self):
        result = replay_fixture("invalidated_break")
        assert len(result.removal_reasons) > 0

    def test_removal_reason_contains_keyword(self):
        result = replay_fixture("invalidated_break")
        assert any("跌破失效价" in r for r in result.removal_reasons)

    def test_strategy_stats(self):
        result = replay_fixture("invalidated_break")
        assert result.strategy_stats_keys == ["PULLBACK_SUPPORT"]

    def test_no_error(self):
        result = replay_fixture("invalidated_break")
        assert result.error == ""


# ── Expired No Trigger ──


class TestExpiredNoTrigger:
    def test_miss_count(self):
        result = replay_fixture("expired_no_trigger")
        assert result.miss_count == 1

    def test_has_removal_reason(self):
        result = replay_fixture("expired_no_trigger")
        assert len(result.removal_reasons) > 0

    def test_removal_reason_contains_keyword(self):
        result = replay_fixture("expired_no_trigger")
        assert any("观察到期未触发" in r for r in result.removal_reasons)

    def test_strategy_stats(self):
        result = replay_fixture("expired_no_trigger")
        assert result.strategy_stats_keys == ["EVENT_CATALYST"]

    def test_tier_stats(self):
        result = replay_fixture("expired_no_trigger")
        assert result.tier_stats_keys == ["C"]


# ── Mixed Tier Review ──


class TestMixedTierReview:
    def test_hit_count(self):
        result = replay_fixture("mixed_tier_review")
        assert result.hit_count == 1

    def test_miss_count(self):
        result = replay_fixture("mixed_tier_review")
        assert result.miss_count == 3

    def test_invalidated_count(self):
        result = replay_fixture("mixed_tier_review")
        assert result.invalidated_count == 1

    def test_no_data_count(self):
        result = replay_fixture("mixed_tier_review")
        assert result.no_data_candidates == 1

    def test_strategy_stats_all(self):
        result = replay_fixture("mixed_tier_review")
        expected = {"VCP", "FUND_FLOW_ANOMALY", "PULLBACK_SUPPORT", "NARRATIVE_QUALITY", "POLICY_VERSION"}
        assert set(result.strategy_stats_keys) == expected

    def test_tier_stats_all(self):
        result = replay_fixture("mixed_tier_review")
        assert set(result.tier_stats_keys) == {"A", "B", "C"}

    def test_has_removal_reasons(self):
        result = replay_fixture("mixed_tier_review")
        assert len(result.removal_reasons) > 0

    def test_candidates_count(self):
        result = replay_fixture("mixed_tier_review")
        assert result.candidates_count == 5

    def test_suggestions_not_empty(self):
        result = replay_fixture("mixed_tier_review")
        assert len(result.suggestions) > 0


# ── No Data Stale ──


class TestNoDataStale:
    def test_no_data_count(self):
        result = replay_fixture("no_data_stale")
        assert result.no_data_candidates == 2

    def test_hit_count_zero(self):
        result = replay_fixture("no_data_stale")
        assert result.hit_count == 0

    def test_miss_count_zero(self):
        result = replay_fixture("no_data_stale")
        assert result.miss_count == 0

    def test_hit_rate_none(self):
        result = replay_fixture("no_data_stale")
        assert result.overall_hit_rate is None

    def test_avg_return_none(self):
        result = replay_fixture("no_data_stale")
        assert result.avg_next_day_return is None

    def test_suggestions_has_no_data_hint(self):
        result = replay_fixture("no_data_stale")
        assert len(result.suggestions) > 0


# ── Cross Date Review ──


class TestCrossDateReview:
    def test_hit_count(self):
        result = replay_fixture("cross_date_review")
        assert result.hit_count == 1

    def test_candidate_date(self):
        result = replay_fixture("cross_date_review")
        assert result.candidate_date == "2026-05-31"

    def test_review_date(self):
        result = replay_fixture("cross_date_review")
        assert result.review_date == "2026-06-01"

    def test_dates_differ(self):
        result = replay_fixture("cross_date_review")
        assert result.candidate_date != result.review_date


# ── Replay All ──


class TestReplayAll:
    def test_count(self):
        results = replay_all()
        assert len(results) == len(ALL_FIXTURE_IDS)

    def test_no_errors(self):
        results = replay_all()
        for r in results:
            assert r.error == "", f"{r.fixture_id} has error: {r.error}"

    def test_unique_fixture_ids(self):
        results = replay_all()
        ids = [r.fixture_id for r in results]
        assert len(ids) == len(set(ids))

    def test_all_fixtures_present(self):
        results = replay_all()
        ids = {r.fixture_id for r in results}
        assert ids == set(ALL_FIXTURE_IDS)

    def test_all_have_valid_dates(self):
        results = replay_all()
        for r in results:
            assert len(r.review_date) == 10
            assert len(r.candidate_date) == 10


# ── Validation ──


class TestValidation:
    def test_validate_expectations_no_issues(self):
        results = replay_all()
        issues = validate_fixture_expectations(results)
        assert issues == [], f"Validation issues: {issues}"

    def test_validate_no_forbidden_words(self):
        results = replay_all()
        issues = validate_no_forbidden_words(results)
        assert issues == []

    def test_validate_unknown_fixture(self):
        bad_result = ReviewFixtureResult(fixture_id="nonexistent", error="test error")
        issues = validate_fixture_expectations([bad_result])
        assert any("nonexistent" in i for i in issues)

    def test_validate_error_fixture(self):
        bad_result = ReviewFixtureResult(
            fixture_id="multi_strategy_hit",
            hit_count=999,
            miss_count=0,
            invalidated_count=0,
            no_data_candidates=0,
            strategy_stats_keys=[],
            tier_stats_keys=[],
            removal_reasons=[],
        )
        issues = validate_fixture_expectations([bad_result])
        assert len(issues) > 0


# ── ReviewFixtureResult ──


class TestReviewFixtureResult:
    def test_defaults(self):
        result = ReviewFixtureResult(fixture_id="test")
        assert result.candidates_count == 0
        assert result.hit_count == 0
        assert result.miss_count == 0
        assert result.invalidated_count == 0
        assert result.removal_reasons == []
        assert result.suggestions == []
        assert result.error == ""

    def test_to_dict(self):
        result = ReviewFixtureResult(
            fixture_id="test",
            candidates_count=3,
            hit_count=1,
            review_date="2026-06-03",
        )
        d = result.to_dict()
        assert d["fixture_id"] == "test"
        assert d["candidates_count"] == 3
        assert d["hit_count"] == 1
        assert d["review_date"] == "2026-06-03"

    def test_roundtrip(self):
        result = ReviewFixtureResult(
            fixture_id="test",
            candidates_count=5,
            scored_candidates=3,
            hit_count=2,
            miss_count=1,
            invalidated_count=1,
            removal_reasons=["SYM: 价格跌破失效价"],
            suggestions=["策略信号质量正常"],
            strategy_stats_keys=["VCP"],
            tier_stats_keys=["A", "B"],
            overall_hit_rate=66.7,
            overall_false_positive_rate=33.3,
            avg_next_day_return=1.5,
            review_date="2026-06-03",
            candidate_date="2026-06-02",
        )
        d = result.to_dict()
        assert d["removal_reasons"] == ["SYM: 价格跌破失效价"]
        assert d["overall_hit_rate"] == 66.7


# ── Markdown Rendering with Fixtures ──


class TestMarkdownRendering:
    def test_render_multi_strategy(self):
        fixture = get_fixture("multi_strategy_hit")
        for p in fixture["performances"]:
            p.compute_returns()
        summary = run_post_market_review(
            fixture["performances"],
            candidate_date=fixture["candidate_date"],
            review_date=fixture["review_date"],
        )
        md = render_review_markdown(summary)
        assert "盘后复盘" in md
        assert "2026-06-03" in md
        assert "VCP" in md
        assert "A层" in md

    def test_render_mixed_tier(self):
        fixture = get_fixture("mixed_tier_review")
        for p in fixture["performances"]:
            p.compute_returns()
        summary = run_post_market_review(
            fixture["performances"],
            candidate_date=fixture["candidate_date"],
            review_date=fixture["review_date"],
        )
        md = render_review_markdown(summary)
        assert "A层" in md
        assert "B层" in md
        assert "C层" in md
        assert "调参建议" in md

    def test_render_invalidated_has_removal(self):
        fixture = get_fixture("invalidated_break")
        for p in fixture["performances"]:
            p.compute_returns()
        summary = run_post_market_review(
            fixture["performances"],
            candidate_date=fixture["candidate_date"],
            review_date=fixture["review_date"],
        )
        md = render_review_markdown(summary)
        assert "移除理由" in md
        assert "跌破失效价" in md

    def test_no_forbidden_words_in_markdown(self):
        results = replay_all()
        for result in results:
            if result.error:
                continue
            fixture = get_fixture(result.fixture_id)
            for p in fixture["performances"]:
                p.compute_returns()
            summary = run_post_market_review(
                fixture["performances"],
                candidate_date=fixture["candidate_date"],
                review_date=fixture["review_date"],
            )
            md = render_review_markdown(summary)
            for word in FORBIDDEN_WORDS:
                assert word not in md, f"Forbidden word '{word}' found in {result.fixture_id}"


# ── Save Report ──


class TestSaveReport:
    def test_save_report(self):
        result = replay_fixture("mixed_tier_review")
        fixture = get_fixture("mixed_tier_review")
        for p in fixture["performances"]:
            p.compute_returns()
        summary = run_post_market_review(
            fixture["performances"],
            candidate_date=fixture["candidate_date"],
            review_date="2099-01-01",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_review_report(summary, output_dir=tmpdir)
            assert os.path.exists(path)
            assert "2099-01-01" in path
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "盘后复盘" in content

    def test_save_creates_directory(self):
        fixture = get_fixture("single_miss")
        for p in fixture["performances"]:
            p.compute_returns()
        summary = run_post_market_review(
            fixture["performances"],
            candidate_date=fixture["candidate_date"],
            review_date="2099-02-01",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "nested", "dir")
            path = save_review_report(summary, output_dir=subdir)
            assert os.path.exists(path)


# ── API Integration ──


class TestAPIIntegration:
    def test_generate_review_no_db(self):
        from api.services.tradeflow_service import generate_review
        result = generate_review("2026-06-02", tf_db_path="/tmp/nonexistent_t005_test.db")
        assert result["status"] in ("no_data", "ok")

    def test_get_review_no_db(self):
        from api.services.tradeflow_service import get_review
        result = get_review("2026-06-02", tf_db_path="/tmp/nonexistent_t005_test.db")
        assert result["status"] in ("no_data", "ok")

    def test_generate_review_schema_fields(self):
        from api.services.tradeflow_service import generate_review
        result = generate_review("2026-06-02", tf_db_path="/tmp/nonexistent_t005_test.db")
        assert "trade_date" in result
        assert "status" in result


# ── Removal Reasons Quality ──


class TestRemovalReasonsQuality:
    def test_invalidated_candidate_has_reason(self):
        perf = CandidatePerformance(
            symbol="000001.SZ",
            trade_date="2026-06-02",
            entry_price=10.0,
            trigger_price=10.5,
            invalid_price=9.0,
            strategy_tags=["VCP"],
            tier="A",
            next_day_close=8.5,
            observe_state="INVALIDATED",
        )
        perf.compute_returns()
        summary = run_post_market_review([perf], candidate_date="2026-06-02")
        assert len(summary.common_removal_reasons) > 0
        assert any("跌破失效价" in r for r in summary.common_removal_reasons)

    def test_expired_candidate_has_reason(self):
        perf = CandidatePerformance(
            symbol="000001.SZ",
            trade_date="2026-06-02",
            entry_price=10.0,
            trigger_price=10.5,
            invalid_price=9.0,
            strategy_tags=["VCP"],
            tier="B",
            next_day_close=10.1,
            observe_state="EXPIRED",
        )
        perf.compute_returns()
        summary = run_post_market_review([perf], candidate_date="2026-06-02")
        assert len(summary.common_removal_reasons) > 0
        assert any("观察到期未触发" in r for r in summary.common_removal_reasons)

    def test_normal_hit_no_removal(self):
        perf = CandidatePerformance(
            symbol="000001.SZ",
            trade_date="2026-06-02",
            entry_price=10.0,
            trigger_price=10.5,
            invalid_price=9.0,
            strategy_tags=["VCP"],
            tier="A",
            next_day_close=11.0,
        )
        perf.compute_returns()
        summary = run_post_market_review([perf], candidate_date="2026-06-02")
        assert summary.common_removal_reasons == []

    def test_multiple_invalidated(self):
        perfs = []
        for sym in ["000001.SZ", "600519.SH", "601689.SH"]:
            p = CandidatePerformance(
                symbol=sym,
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["VCP"],
                tier="B",
                next_day_close=8.0,
                observe_state="INVALIDATED",
            )
            p.compute_returns()
            perfs.append(p)
        summary = run_post_market_review(perfs, candidate_date="2026-06-02")
        assert len(summary.common_removal_reasons) == 3
        assert all("跌破失效价" in r for r in summary.common_removal_reasons)


# ── Strategy Stats Deep Dive ──


class TestStrategyStatsDeep:
    def test_vcp_hit_rate_multi(self):
        fixture = get_fixture("mixed_tier_review")
        for p in fixture["performances"]:
            p.compute_returns()
        summary = run_post_market_review(
            fixture["performances"],
            candidate_date=fixture["candidate_date"],
            review_date=fixture["review_date"],
        )
        vcp = summary.strategy_stats.get("VCP")
        assert vcp is not None
        assert vcp.total_candidates == 2
        assert vcp.hit_count == 1
        assert vcp.miss_count == 1

    def test_fund_flow_anomaly_stats(self):
        fixture = get_fixture("mixed_tier_review")
        for p in fixture["performances"]:
            p.compute_returns()
        summary = run_post_market_review(
            fixture["performances"],
            candidate_date=fixture["candidate_date"],
            review_date=fixture["review_date"],
        )
        ffa = summary.strategy_stats.get("FUND_FLOW_ANOMALY")
        assert ffa is not None
        assert ffa.total_candidates == 1
        assert ffa.miss_count == 1

    def test_no_strategy_default(self):
        perf = CandidatePerformance(
            symbol="000001.SZ",
            trade_date="2026-06-02",
            entry_price=10.0,
            trigger_price=10.5,
            invalid_price=9.0,
            strategy_tags=[],
            tier="C",
            next_day_close=11.0,
        )
        perf.compute_returns()
        summary = run_post_market_review([perf], candidate_date="2026-06-02")
        assert "NO_STRATEGY" in summary.strategy_stats


# ── Acceptance T-005 ──


class TestAcceptanceT005:
    def test_daily_review_table_output(self):
        results = replay_all()
        for result in results:
            assert result.candidates_count >= 0
            assert result.scored_candidates + result.no_data_candidates == result.candidates_count

    def test_invalidated_candidates_have_removal_reason(self):
        result = replay_fixture("invalidated_break")
        assert result.invalidated_count > 0
        assert len(result.removal_reasons) > 0
        assert any("跌破失效价" in r for r in result.removal_reasons)

    def test_expired_candidates_have_removal_reason(self):
        result = replay_fixture("expired_no_trigger")
        assert len(result.removal_reasons) > 0
        assert any("观察到期未触发" in r for r in result.removal_reasons)

    def test_review_markdown_complete(self):
        fixture = get_fixture("mixed_tier_review")
        for p in fixture["performances"]:
            p.compute_returns()
        summary = run_post_market_review(
            fixture["performances"],
            candidate_date=fixture["candidate_date"],
            review_date=fixture["review_date"],
        )
        md = render_review_markdown(summary)
        assert "总体概览" in md
        assert "策略命中率" in md
        assert "分层统计" in md
        assert "调参建议" in md

    def test_all_fixtures_replay_without_error(self):
        results = replay_all()
        for result in results:
            assert result.error == "", f"{result.fixture_id} failed: {result.error}"

    def test_validation_passes(self):
        results = replay_all()
        issues = validate_fixture_expectations(results)
        assert issues == [], f"Validation failed: {issues}"

    def test_no_forbidden_words(self):
        issues = validate_no_forbidden_words(replay_all())
        assert issues == []

    def test_removal_reasons_explain_why(self):
        result = replay_fixture("invalidated_break")
        for reason in result.removal_reasons:
            assert len(reason) > 10
            assert ":" in reason or "：" in reason or "跌破" in reason

    def test_cross_date_review_works(self):
        result = replay_fixture("cross_date_review")
        assert result.candidate_date == "2026-05-31"
        assert result.review_date == "2026-06-01"
        assert result.hit_count == 1

    def test_no_data_still_produces_report(self):
        result = replay_fixture("no_data_stale")
        assert result.candidates_count > 0
        assert result.no_data_candidates == result.candidates_count
        assert len(result.suggestions) > 0
