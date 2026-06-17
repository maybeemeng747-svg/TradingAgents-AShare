# [M-007] post_market_review tests
"""Tests for post-market review and strategy hit rate analysis."""

import os
import tempfile
from datetime import datetime

import pytest

from tradingagents.tradeflow.schemas import Candidate, CandidateSignal
from tradingagents.tradeflow.post_market_review import (
    CandidatePerformance,
    StrategyStats,
    ReviewSummary,
    build_candidate_performance,
    build_candidate_performance_from_dict,
    compute_strategy_stats,
    compute_tier_stats,
    generate_suggestions,
    run_post_market_review,
    render_review_markdown,
    save_review_report,
)


def _make_candidate(
    symbol: str = "000001.SZ",
    strategy_tags: list = None,
    trigger_price: float = 10.0,
    invalid_price: float = 9.0,
    tier: str = "A",
    need_deep_ta: bool = True,
    composite_score: float = 70.0,
    game_balance: str = "favorable",
    risk_flags: list = None,
    observe_state: str = "TRIGGERED",
    trade_date: str = "2026-05-29",
) -> Candidate:
    c = Candidate(
        symbol=symbol,
        name="测试股",
        trade_date=trade_date,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        tier=tier,
        need_deep_ta=need_deep_ta,
        composite_score=composite_score,
        game_balance=game_balance,
        risk_flags=risk_flags or [],
        observe_state=observe_state,
        strategy_tags=strategy_tags or ["VCP"],
        score=60.0,
    )
    return c


# ── CandidatePerformance ──


class TestCandidatePerformance:
    def test_compute_returns_next_day(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            next_day_close=10.3,
        )
        perf.compute_returns()
        assert perf.next_day_return_pct == 3.0

    def test_compute_returns_day3(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            day3_close=9.5,
        )
        perf.compute_returns()
        assert perf.day3_return_pct == -5.0

    def test_compute_returns_day5(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            day5_close=11.0,
        )
        perf.compute_returns()
        assert perf.day5_return_pct == 10.0

    def test_hit_when_return_exceeds_trigger_distance(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            next_day_close=10.6,
        )
        perf.compute_returns()
        assert perf.hit is True

    def test_miss_when_return_below_trigger_distance(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
            next_day_close=10.2,
        )
        perf.compute_returns()
        assert perf.hit is False

    def test_hit_positive_return_no_trigger(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=None, invalid_price=None,
            next_day_close=10.5,
        )
        perf.compute_returns()
        assert perf.hit is True

    def test_miss_negative_return_no_trigger(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=None, invalid_price=None,
            next_day_close=9.5,
        )
        perf.compute_returns()
        assert perf.hit is False

    def test_invalidated_when_below_invalid_price(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.5,
            next_day_close=9.3,
        )
        perf.compute_returns()
        assert perf.invalidated is True

    def test_not_invalidated_when_above_invalid_price(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.5,
            next_day_close=9.8,
        )
        perf.compute_returns()
        assert perf.invalidated is False

    def test_no_hit_without_next_day_data(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
        )
        perf.compute_returns()
        assert perf.hit is None

    def test_zero_entry_price_no_crash(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=0.0, trigger_price=None, invalid_price=None,
            next_day_close=10.5,
        )
        perf.compute_returns()
        assert perf.next_day_return_pct is None


# ── StrategyStats ──


class TestStrategyStats:
    def test_compute_rates_basic(self):
        st = StrategyStats(strategy_tag="VCP", hit_count=7, miss_count=3, total_candidates=10)
        st.compute_rates()
        assert st.hit_rate == 70.0
        assert st.false_positive_rate == 30.0

    def test_compute_rates_zero(self):
        st = StrategyStats(strategy_tag="VCP", hit_count=0, miss_count=0, total_candidates=5)
        st.compute_rates()
        assert st.hit_rate is None
        assert st.false_positive_rate is None

    def test_compute_rates_all_hits(self):
        st = StrategyStats(strategy_tag="VCP", hit_count=5, miss_count=0, total_candidates=5)
        st.compute_rates()
        assert st.hit_rate == 100.0
        assert st.false_positive_rate == 0.0


# ── build_candidate_performance ──


class TestBuildCandidatePerformance:
    def test_basic_build(self):
        c = _make_candidate()
        perf = build_candidate_performance(c, next_day_close=10.5, day3_close=11.0, day5_close=11.5)
        assert perf.symbol == "000001.SZ"
        assert perf.entry_price == 10.0
        assert perf.trigger_price == 10.0
        assert perf.tier == "A"
        assert perf.next_day_return_pct == 5.0
        assert perf.day3_return_pct == 10.0
        assert perf.day5_return_pct == 15.0

    def test_no_price_data(self):
        c = _make_candidate()
        perf = build_candidate_performance(c)
        assert perf.next_day_return_pct is None
        assert perf.hit is None

    def test_preserves_strategy_tags(self):
        c = _make_candidate(strategy_tags=["VCP", "EVENT_CATALYST"])
        perf = build_candidate_performance(c, next_day_close=10.5)
        assert "VCP" in perf.strategy_tags
        assert "EVENT_CATALYST" in perf.strategy_tags

    def test_preserves_risk_flags(self):
        c = _make_candidate(risk_flags=["INQUIRY_RISK", "LOCKUP_RISK"])
        perf = build_candidate_performance(c)
        assert "INQUIRY_RISK" in perf.risk_flags
        assert "LOCKUP_RISK" in perf.risk_flags


class TestBuildCandidatePerformanceFromDict:
    def test_basic_dict(self):
        entry = {
            "symbol": "600519.SH",
            "trade_date": "2026-05-29",
            "trigger_price": 1800.0,
            "invalid_price": 1750.0,
            "strategies": ["VCP", "POLICY_VERSION"],
            "tier": "B",
            "need_deep_ta": False,
            "observe_state": "WAITING",
            "composite_score": 45.0,
            "game_balance": "neutral",
            "risk_flags": [],
        }
        perf = build_candidate_performance_from_dict(entry, next_day_close=1820.0)
        assert perf.symbol == "600519.SH"
        assert perf.entry_price == 1800.0
        assert perf.next_day_return_pct == pytest.approx(1.11, abs=0.01)

    def test_dict_with_strategy_tags_key(self):
        entry = {
            "symbol": "000002.SZ",
            "trigger_price": 15.0,
            "strategy_tags": ["PULLBACK_SUPPORT"],
        }
        perf = build_candidate_performance_from_dict(entry, next_day_close=14.0)
        assert perf.strategy_tags == ["PULLBACK_SUPPORT"]

    def test_dict_no_trigger(self):
        entry = {"symbol": "000003.SZ"}
        perf = build_candidate_performance_from_dict(entry)
        assert perf.entry_price == 0.0
        assert perf.trigger_price is None


# ── compute_strategy_stats ──


class TestComputeStrategyStats:
    def test_single_strategy(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=10.5, invalid_price=9.0,
                strategy_tags=["VCP"], next_day_close=10.6,
            ),
            CandidatePerformance(
                symbol="000002.SZ", trade_date="2026-05-29",
                entry_price=20.0, trigger_price=21.0, invalid_price=19.0,
                strategy_tags=["VCP"], next_day_close=20.5,
            ),
        ]
        for p in perfs:
            p.compute_returns()
        stats = compute_strategy_stats(perfs)
        assert "VCP" in stats
        assert stats["VCP"].total_candidates == 2
        assert stats["VCP"].hit_count == 1
        assert stats["VCP"].miss_count == 1
        assert stats["VCP"].hit_rate == 50.0

    def test_multiple_strategies(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP", "EVENT_CATALYST"], next_day_close=10.5,
            ),
            CandidatePerformance(
                symbol="000002.SZ", trade_date="2026-05-29",
                entry_price=20.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=19.5,
            ),
            CandidatePerformance(
                symbol="000003.SZ", trade_date="2026-05-29",
                entry_price=30.0, trigger_price=None, invalid_price=None,
                strategy_tags=["EVENT_CATALYST"], next_day_close=31.0,
            ),
        ]
        for p in perfs:
            p.compute_returns()
        stats = compute_strategy_stats(perfs)
        assert stats["VCP"].total_candidates == 2
        assert stats["EVENT_CATALYST"].total_candidates == 2

    def test_no_strategy_tag(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=[], next_day_close=10.5,
            ),
        ]
        perfs[0].compute_returns()
        stats = compute_strategy_stats(perfs)
        assert "NO_STRATEGY" in stats

    def test_avg_returns(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=11.0,
            ),
            CandidatePerformance(
                symbol="000002.SZ", trade_date="2026-05-29",
                entry_price=20.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=22.0,
            ),
        ]
        for p in perfs:
            p.compute_returns()
        stats = compute_strategy_stats(perfs)
        assert stats["VCP"].avg_next_day_return == 10.0


# ── compute_tier_stats ──


class TestComputeTierStats:
    def test_basic_tiers(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                tier="A", next_day_close=10.5,
            ),
            CandidatePerformance(
                symbol="000002.SZ", trade_date="2026-05-29",
                entry_price=20.0, trigger_price=None, invalid_price=None,
                tier="B", next_day_close=19.5,
            ),
            CandidatePerformance(
                symbol="000003.SZ", trade_date="2026-05-29",
                entry_price=30.0, trigger_price=None, invalid_price=None,
                tier="C", next_day_close=30.0,
            ),
        ]
        for p in perfs:
            p.compute_returns()
        stats = compute_tier_stats(perfs)
        assert stats["A"]["total"] == 1
        assert stats["A"]["hit"] == 1
        assert stats["B"]["total"] == 1
        assert stats["B"]["miss"] == 1
        assert stats["C"]["total"] == 1

    def test_unknown_tier(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                tier="", next_day_close=10.5,
            ),
        ]
        perfs[0].compute_returns()
        stats = compute_tier_stats(perfs)
        assert "unknown" in stats


# ── generate_suggestions ──


class TestGenerateSuggestions:
    def test_low_hit_rate_suggestion(self):
        stats = {
            "VCP": StrategyStats(
                strategy_tag="VCP", total_candidates=5,
                hit_count=1, miss_count=4,
            )
        }
        stats["VCP"].compute_rates()
        suggestions = generate_suggestions(stats, {}, 5)
        assert any("VCP" in s and "命中率偏低" in s for s in suggestions)

    def test_high_false_positive_suggestion(self):
        stats = {
            "VCP": StrategyStats(
                strategy_tag="VCP", total_candidates=5,
                hit_count=1, miss_count=4,
            )
        }
        stats["VCP"].compute_rates()
        suggestions = generate_suggestions(stats, {}, 5)
        assert any("VCP" in s and "误报率偏高" in s for s in suggestions)

    def test_high_invalidation_suggestion(self):
        stats = {
            "VCP": StrategyStats(
                strategy_tag="VCP", total_candidates=5,
                hit_count=2, miss_count=0,
                invalidated_count=3,
            )
        }
        stats["VCP"].compute_rates()
        suggestions = generate_suggestions(stats, {}, 5)
        assert any("失效计数较高" in s for s in suggestions)

    def test_no_data_suggestion(self):
        tier_stats = {"A": {"total": 10, "hit": 0, "miss": 0, "no_data": 8, "invalidated": 0}}
        suggestions = generate_suggestions({}, tier_stats, 10)
        assert any("数据缺失率" in s for s in suggestions)

    def test_zero_candidates_suggestion(self):
        suggestions = generate_suggestions({}, {}, 0)
        assert any("无候选数据" in s for s in suggestions)

    def test_normal_suggestion(self):
        stats = {
            "VCP": StrategyStats(
                strategy_tag="VCP", total_candidates=2,
                hit_count=1, miss_count=1,
            )
        }
        stats["VCP"].compute_rates()
        suggestions = generate_suggestions(stats, {}, 2)
        assert any("暂无调参建议" in s for s in suggestions)

    def test_too_few_candidates_no_suggestion(self):
        stats = {
            "VCP": StrategyStats(
                strategy_tag="VCP", total_candidates=2,
                hit_count=0, miss_count=2,
            )
        }
        stats["VCP"].compute_rates()
        suggestions = generate_suggestions(stats, {}, 2)
        assert not any("命中率偏低" in s for s in suggestions)


# ── run_post_market_review ──


class TestRunPostMarketReview:
    def _make_perfs(self):
        perfs = []
        for i, (sym, tag, nd_close, tier) in enumerate([
            ("000001.SZ", ["VCP"], 10.5, "A"),
            ("000002.SZ", ["VCP"], 9.5, "A"),
            ("000003.SZ", ["EVENT_CATALYST"], 31.0, "B"),
            ("000004.SZ", ["EVENT_CATALYST"], 28.0, "B"),
            ("000005.SZ", ["VCP", "EVENT_CATALYST"], 20.0, "C"),
        ]):
            p = CandidatePerformance(
                symbol=sym, trade_date="2026-05-29",
                entry_price=10.0 * (i + 1), trigger_price=None, invalid_price=None,
                strategy_tags=tag, tier=tier, next_day_close=nd_close,
            )
            p.compute_returns()
            perfs.append(p)
        return perfs

    def test_basic_review(self):
        perfs = self._make_perfs()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        assert summary.total_candidates == 5
        assert summary.review_date == "2026-05-30"
        assert summary.candidate_date == "2026-05-29"
        assert summary.overall_hit_count == 2
        assert summary.overall_miss_count == 3
        assert summary.overall_hit_rate == 40.0
        assert summary.overall_false_positive_rate == 60.0

    def test_review_with_no_data(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"],
            ),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert summary.no_data_candidates == 1
        assert summary.overall_hit_rate is None

    def test_review_empty_perfs(self):
        summary = run_post_market_review([], candidate_date="2026-05-29")
        assert summary.total_candidates == 0
        assert summary.overall_hit_rate is None

    def test_review_auto_computes_returns(self):
        """run_post_market_review must call compute_returns() internally."""
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], tier="A", next_day_close=10.5,
            ),
            CandidatePerformance(
                symbol="000002.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], tier="A", next_day_close=9.5,
            ),
        ]
        # Do NOT call compute_returns() — run_post_market_review should do it
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert summary.overall_hit_count == 1
        assert summary.overall_miss_count == 1
        assert summary.avg_next_day_return is not None

    def test_review_auto_date(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-28",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=10.5,
            ),
        ]
        perfs[0].compute_returns()
        summary = run_post_market_review(perfs)
        assert summary.candidate_date == "2026-05-28"
        assert summary.review_date == datetime.now().strftime("%Y-%m-%d")

    def test_strategy_stats_in_review(self):
        perfs = self._make_perfs()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert "VCP" in summary.strategy_stats
        assert "EVENT_CATALYST" in summary.strategy_stats

    def test_tier_stats_in_review(self):
        perfs = self._make_perfs()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert "A" in summary.tier_stats
        assert "B" in summary.tier_stats
        assert "C" in summary.tier_stats

    def test_avg_returns(self):
        perfs = self._make_perfs()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert summary.avg_next_day_return is not None

    def test_removal_reasons_invalidated(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=10.5, invalid_price=9.5,
                strategy_tags=["VCP"], next_day_close=9.3,
                invalidated=True,
            ),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert any("跌破失效价" in r for r in summary.common_removal_reasons)

    def test_removal_reasons_expired(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], observe_state="EXPIRED",
            ),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert any("到期" in r for r in summary.common_removal_reasons)


# ── render_review_markdown ──


class TestRenderReviewMarkdown:
    def test_basic_render(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], tier="A", next_day_close=10.5,
            ),
        ]
        perfs[0].compute_returns()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        md = render_review_markdown(summary)
        assert "# 盘后复盘 2026-05-30" in md
        # [TF-REVIEW-003] symbols now appear in the 次日反馈 section
        assert "次日反馈" in md
        assert "VCP" in md
        assert "A层" in md

    def test_render_no_forbidden_words(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=10.5,
            ),
        ]
        perfs[0].compute_returns()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        md = render_review_markdown(summary)
        for word in ["立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"]:
            assert word not in md

    def test_render_empty_summary(self):
        summary = ReviewSummary(review_date="2026-05-30", candidate_date="2026-05-29")
        summary.compute_overall()
        md = render_review_markdown(summary)
        assert "总候选数 | 0" in md
        assert "N/A" in md

    def test_render_zero_percent_not_na(self):
        """0.0% must render as '0.0%', not 'N/A%'. N/A is ok for genuinely missing data."""
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], tier="A", next_day_close=10.0,  # 0% return
            ),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        md = render_review_markdown(summary)
        # 0.0% values must show as 0.0%, not N/A%
        assert "总命中率 | 0.0%" in md
        assert "平均次日收益 | 0.0%" in md
        assert "| 0.0% | 100.0% | 0.0%" in md  # strategy row
        # N/A is acceptable for genuinely missing 3d/5d data
        assert "平均3日收益 | N/A%" in md
        assert "平均5日收益 | N/A%" in md

    def test_render_strategy_table(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP", "EVENT_CATALYST"], tier="A", next_day_close=10.5,
            ),
            CandidatePerformance(
                symbol="000002.SZ", trade_date="2026-05-29",
                entry_price=20.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], tier="B", next_day_close=19.5,
            ),
        ]
        for p in perfs:
            p.compute_returns()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        md = render_review_markdown(summary)
        assert "| VCP |" in md
        assert "| EVENT_CATALYST |" in md


# ── save_review_report ──


class TestSaveReviewReport:
    def test_save_to_file(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=10.5,
            ),
        ]
        perfs[0].compute_returns()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = save_review_report(summary, output_dir=tmpdir)
            assert os.path.exists(filepath)
            assert filepath.endswith("2026-05-30.md")
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            assert "# 盘后复盘 2026-05-30" in content

    def test_save_creates_directory(self):
        summary = ReviewSummary(review_date="2026-05-30", candidate_date="2026-05-29")
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sub", "dir")
            filepath = save_review_report(summary, output_dir=nested)
            assert os.path.exists(filepath)

    def test_save_no_forbidden_words_in_file(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=10.5,
            ),
        ]
        perfs[0].compute_returns()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = save_review_report(summary, output_dir=tmpdir)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            for word in ["立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"]:
                assert word not in content


# ── Integration: from Candidate objects through full pipeline ──


class TestFullPipelineFromCandidates:
    def test_candidate_to_review(self):
        c1 = _make_candidate(
            symbol="000001.SZ", strategy_tags=["VCP", "POLICY_VERSION"],
            trigger_price=10.5, tier="A",
        )
        c2 = _make_candidate(
            symbol="000002.SZ", strategy_tags=["EVENT_CATALYST"],
            trigger_price=20.0, tier="B",
        )
        c3 = _make_candidate(
            symbol="000003.SZ", strategy_tags=["VCP"],
            trigger_price=15.0, tier="C", risk_flags=["INQUIRY_RISK"],
        )

        perf1 = build_candidate_performance(c1, next_day_close=11.0, day3_close=11.5, day5_close=12.0)
        perf2 = build_candidate_performance(c2, next_day_close=19.0, day3_close=18.5, day5_close=18.0)
        perf3 = build_candidate_performance(c3, next_day_close=14.0)

        assert perf1.hit is True
        assert perf2.hit is False
        assert perf3.hit is False

        summary = run_post_market_review(
            [perf1, perf2, perf3],
            candidate_date="2026-05-29",
            review_date="2026-05-30",
        )
        assert summary.total_candidates == 3
        assert summary.overall_hit_count == 1
        assert summary.overall_miss_count == 2
        assert "VCP" in summary.strategy_stats
        assert "POLICY_VERSION" in summary.strategy_stats
        assert "EVENT_CATALYST" in summary.strategy_stats

        md = render_review_markdown(summary)
        assert "VCP" in md
        assert "命中数 | 1" in md

    def test_candidate_to_review_to_file(self):
        c = _make_candidate(strategy_tags=["VCP"], trigger_price=10.5)
        perf = build_candidate_performance(c, next_day_close=11.0, day3_close=11.5, day5_close=12.0)
        summary = run_post_market_review(
            [perf], candidate_date="2026-05-29", review_date="2026-05-30",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = save_review_report(summary, output_dir=tmpdir)
            assert os.path.exists(filepath)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            assert "命中数 | 1" in content

    def test_plan_entry_to_review(self):
        entry = {
            "symbol": "600519.SH",
            "trade_date": "2026-05-29",
            "trigger_price": 1800.0,
            "invalid_price": 1750.0,
            "strategies": ["VCP", "POLICY_VERSION"],
            "tier": "A",
            "need_deep_ta": True,
            "observe_state": "TRIGGERED",
            "composite_score": 75.0,
            "game_balance": "favorable",
            "risk_flags": [],
        }
        perf = build_candidate_performance_from_dict(
            entry, next_day_close=1850.0, day3_close=1820.0, day5_close=1900.0,
        )
        assert perf.hit is True
        assert perf.next_day_return_pct == pytest.approx(2.78, abs=0.01)

        summary = run_post_market_review(
            [perf], candidate_date="2026-05-29", review_date="2026-05-30",
        )
        assert summary.overall_hit_count == 1
        assert summary.overall_miss_count == 0

    def test_multi_day_returns(self):
        c = _make_candidate(trigger_price=10.0)
        perf = build_candidate_performance(c, next_day_close=10.5, day3_close=9.5, day5_close=11.0)
        assert perf.next_day_return_pct == 5.0
        assert perf.day3_return_pct == -5.0
        assert perf.day5_return_pct == 10.0


# ── Edge cases ──


class TestEdgeCases:
    def test_all_no_data(self):
        perfs = [
            CandidatePerformance(
                symbol=f"00000{i}.SZ", trade_date="2026-05-29",
                entry_price=10.0 + i, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"],
            )
            for i in range(3)
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert summary.no_data_candidates == 3
        assert summary.overall_hit_rate is None

    def test_all_hits(self):
        perfs = []
        for i in range(5):
            p = CandidatePerformance(
                symbol=f"00000{i}.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=11.0,
            )
            p.compute_returns()
            perfs.append(p)
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert summary.overall_hit_rate == 100.0
        assert summary.overall_false_positive_rate == 0.0

    def test_all_misses(self):
        perfs = []
        for i in range(5):
            p = CandidatePerformance(
                symbol=f"00000{i}.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=9.0,
            )
            p.compute_returns()
            perfs.append(p)
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        assert summary.overall_hit_rate == 0.0
        assert summary.overall_false_positive_rate == 100.0

    def test_review_with_day3_and_day5_only(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=None, invalid_price=None,
            strategy_tags=["VCP"], day3_close=10.5, day5_close=11.0,
        )
        perf.compute_returns()
        summary = run_post_market_review([perf], candidate_date="2026-05-29")
        assert summary.no_data_candidates == 1
        assert perf.hit is None
        assert perf.day3_return_pct == 5.0
        assert perf.day5_return_pct == 10.0
        assert summary.avg_next_day_return is None
        assert summary.avg_day3_return == 5.0
        assert summary.avg_day5_return == 10.0

    def test_candidate_with_zero_trigger_price(self):
        c = Candidate(symbol="000001.SZ", trigger_price=None, invalid_price=None)
        perf = build_candidate_performance(c, next_day_close=10.5)
        assert perf.entry_price == 0.0
        assert perf.hit is None

    def test_suggestions_dont_auto_adjust_weights(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=9.0,
            )
        ]
        perfs[0].compute_returns()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        for s in summary.suggestions:
            assert "建议" in s or "暂无调参建议" in s
            assert "调整权重" not in s
            assert "修改阈值" not in s

    def test_review_no_investment_advice(self):
        perfs = [
            CandidatePerformance(
                symbol="000001.SZ", trade_date="2026-05-29",
                entry_price=10.0, trigger_price=None, invalid_price=None,
                strategy_tags=["VCP"], next_day_close=10.5,
            ),
        ]
        perfs[0].compute_returns()
        summary = run_post_market_review(perfs, candidate_date="2026-05-29")
        md = render_review_markdown(summary)
        assert "买入" not in md
        assert "卖出" not in md
        assert "清仓" not in md

    def test_compute_returns_called_automatically(self):
        perf = CandidatePerformance(
            symbol="000001.SZ", trade_date="2026-05-29",
            entry_price=10.0, trigger_price=None, invalid_price=None,
            strategy_tags=["VCP"], next_day_close=10.5, day3_close=11.0, day5_close=11.5,
        )
        perf.compute_returns()
        assert perf.next_day_return_pct is not None
        assert perf.day3_return_pct is not None
        assert perf.day5_return_pct is not None
