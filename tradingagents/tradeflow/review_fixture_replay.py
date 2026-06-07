# [T-005] review_fixture_replay
"""TradeFlow Post-Market Review Fixture Replay — replay review scenarios without live market data.

Scenarios:
1. multi_strategy_hit: multiple strategies hit, next_day > trigger → HIT
2. single_miss: VCP only, next_day < trigger → MISS
3. invalidated_break: price breaks invalid_price → INVALIDATED, removal reason
4. expired_no_trigger: observe_state=EXPIRED, removal reason
5. mixed_tier_review: A/B/C tier candidates with varied outcomes
6. no_data_stale: no next_day_close available → no_data, can't score
7. cross_date_review: candidates from non-trading day, reviewed on next trading day

Design constraints:
- No live market data, no TA/LLM calls.
- Uses post_market_review.run_post_market_review() with fixture CandidatePerformance objects.
- Validates: hit/miss/invalidated, removal reasons, strategy stats, tier stats, suggestions.
- Each fixture provides candidate data and expected outcomes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from .post_market_review import (
    CandidatePerformance,
    ReviewSummary,
    run_post_market_review,
    render_review_markdown,
    save_review_report,
)
from .schemas import FORBIDDEN_WORDS

logger = logging.getLogger(__name__)


ALL_FIXTURE_IDS = [
    "multi_strategy_hit",
    "single_miss",
    "invalidated_break",
    "expired_no_trigger",
    "mixed_tier_review",
    "no_data_stale",
    "cross_date_review",
]


@dataclass
class ReviewFixtureResult:
    fixture_id: str
    candidates_count: int = 0
    scored_candidates: int = 0
    no_data_candidates: int = 0
    hit_count: int = 0
    miss_count: int = 0
    invalidated_count: int = 0
    removal_reasons: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    strategy_stats_keys: list[str] = field(default_factory=list)
    tier_stats_keys: list[str] = field(default_factory=list)
    overall_hit_rate: Optional[float] = None
    overall_false_positive_rate: Optional[float] = None
    avg_next_day_return: Optional[float] = None
    review_date: str = ""
    candidate_date: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "fixture_id": self.fixture_id,
            "candidates_count": self.candidates_count,
            "scored_candidates": self.scored_candidates,
            "no_data_candidates": self.no_data_candidates,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "invalidated_count": self.invalidated_count,
            "removal_reasons": self.removal_reasons,
            "suggestions": self.suggestions,
            "strategy_stats_keys": self.strategy_stats_keys,
            "tier_stats_keys": self.tier_stats_keys,
            "overall_hit_rate": self.overall_hit_rate,
            "overall_false_positive_rate": self.overall_false_positive_rate,
            "avg_next_day_return": self.avg_next_day_return,
            "review_date": self.review_date,
            "candidate_date": self.candidate_date,
            "error": self.error,
        }


def _build_multi_strategy_hit() -> dict:
    return {
        "fixture_id": "multi_strategy_hit",
        "candidate_date": "2026-06-02",
        "review_date": "2026-06-03",
        "performances": [
            CandidatePerformance(
                symbol="000001.SZ",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["VCP", "POLICY_VERSION"],
                tier="A",
                next_day_close=10.8,
                day3_close=11.0,
                day5_close=11.2,
            ),
            CandidatePerformance(
                symbol="600519.SH",
                trade_date="2026-06-02",
                entry_price=100.0,
                trigger_price=103.0,
                invalid_price=95.0,
                strategy_tags=["VCP", "FUND_FLOW_ANOMALY"],
                tier="A",
                next_day_close=105.0,
                day3_close=107.0,
                day5_close=106.0,
            ),
        ],
        "expected": {
            "hit_count": 2,
            "miss_count": 0,
            "invalidated_count": 0,
            "no_data_count": 0,
            "strategy_keys": {"VCP", "POLICY_VERSION", "FUND_FLOW_ANOMALY"},
            "tier_keys": {"A"},
        },
    }


def _build_single_miss() -> dict:
    return {
        "fixture_id": "single_miss",
        "candidate_date": "2026-06-02",
        "review_date": "2026-06-03",
        "performances": [
            CandidatePerformance(
                symbol="601689.SH",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["VCP"],
                tier="B",
                next_day_close=9.8,
                day3_close=9.5,
            ),
        ],
        "expected": {
            "hit_count": 0,
            "miss_count": 1,
            "invalidated_count": 0,
            "no_data_count": 0,
            "strategy_keys": {"VCP"},
            "tier_keys": {"B"},
        },
    }


def _build_invalidated_break() -> dict:
    return {
        "fixture_id": "invalidated_break",
        "candidate_date": "2026-06-02",
        "review_date": "2026-06-03",
        "performances": [
            CandidatePerformance(
                symbol="002600.SZ",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["PULLBACK_SUPPORT"],
                tier="B",
                next_day_close=8.5,
                observe_state="INVALIDATED",
            ),
        ],
        "expected": {
            "hit_count": 0,
            "miss_count": 1,
            "invalidated_count": 1,
            "no_data_count": 0,
            "has_removal_reason": True,
            "removal_keyword": "跌破失效价",
            "strategy_keys": {"PULLBACK_SUPPORT"},
            "tier_keys": {"B"},
        },
    }


def _build_expired_no_trigger() -> dict:
    return {
        "fixture_id": "expired_no_trigger",
        "candidate_date": "2026-06-02",
        "review_date": "2026-06-03",
        "performances": [
            CandidatePerformance(
                symbol="300750.SZ",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["EVENT_CATALYST"],
                tier="C",
                next_day_close=10.1,
                observe_state="EXPIRED",
            ),
        ],
        "expected": {
            "hit_count": 0,
            "miss_count": 1,
            "invalidated_count": 0,
            "no_data_count": 0,
            "has_removal_reason": True,
            "removal_keyword": "观察到期未触发",
            "strategy_keys": {"EVENT_CATALYST"},
            "tier_keys": {"C"},
        },
    }


def _build_mixed_tier_review() -> dict:
    return {
        "fixture_id": "mixed_tier_review",
        "candidate_date": "2026-06-02",
        "review_date": "2026-06-03",
        "performances": [
            CandidatePerformance(
                symbol="000001.SZ",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["VCP"],
                tier="A",
                next_day_close=11.0,
            ),
            CandidatePerformance(
                symbol="600519.SH",
                trade_date="2026-06-02",
                entry_price=100.0,
                trigger_price=103.0,
                invalid_price=95.0,
                strategy_tags=["VCP", "FUND_FLOW_ANOMALY"],
                tier="A",
                next_day_close=99.0,
            ),
            CandidatePerformance(
                symbol="601689.SH",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["PULLBACK_SUPPORT"],
                tier="B",
                next_day_close=9.5,
            ),
            CandidatePerformance(
                symbol="002600.SZ",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["NARRATIVE_QUALITY"],
                tier="C",
                next_day_close=8.5,
                observe_state="INVALIDATED",
            ),
            CandidatePerformance(
                symbol="300750.SZ",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["POLICY_VERSION"],
                tier="B",
            ),
        ],
        "expected": {
            "hit_count": 1,
            "miss_count": 3,
            "invalidated_count": 1,
            "no_data_count": 1,
            "strategy_keys": {"VCP", "FUND_FLOW_ANOMALY", "PULLBACK_SUPPORT", "NARRATIVE_QUALITY", "POLICY_VERSION"},
            "tier_keys": {"A", "B", "C"},
            "has_removal_reason": True,
        },
    }


def _build_no_data_stale() -> dict:
    return {
        "fixture_id": "no_data_stale",
        "candidate_date": "2026-06-02",
        "review_date": "2026-06-03",
        "performances": [
            CandidatePerformance(
                symbol="000001.SZ",
                trade_date="2026-06-02",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["VCP"],
                tier="A",
            ),
            CandidatePerformance(
                symbol="600519.SH",
                trade_date="2026-06-02",
                entry_price=100.0,
                trigger_price=103.0,
                invalid_price=95.0,
                strategy_tags=["PULLBACK_SUPPORT"],
                tier="B",
            ),
        ],
        "expected": {
            "hit_count": 0,
            "miss_count": 0,
            "invalidated_count": 0,
            "no_data_count": 2,
            "strategy_keys": {"VCP", "PULLBACK_SUPPORT"},
            "tier_keys": {"A", "B"},
        },
    }


def _build_cross_date_review() -> dict:
    return {
        "fixture_id": "cross_date_review",
        "candidate_date": "2026-05-31",
        "review_date": "2026-06-01",
        "performances": [
            CandidatePerformance(
                symbol="000001.SZ",
                trade_date="2026-05-31",
                entry_price=10.0,
                trigger_price=10.5,
                invalid_price=9.0,
                strategy_tags=["VCP", "POLICY_VERSION"],
                tier="A",
                next_day_close=10.8,
            ),
        ],
        "expected": {
            "hit_count": 1,
            "miss_count": 0,
            "invalidated_count": 0,
            "no_data_count": 0,
            "strategy_keys": {"VCP", "POLICY_VERSION"},
            "tier_keys": {"A"},
        },
    }


_FIXTURE_BUILDERS = {
    "multi_strategy_hit": _build_multi_strategy_hit,
    "single_miss": _build_single_miss,
    "invalidated_break": _build_invalidated_break,
    "expired_no_trigger": _build_expired_no_trigger,
    "mixed_tier_review": _build_mixed_tier_review,
    "no_data_stale": _build_no_data_stale,
    "cross_date_review": _build_cross_date_review,
}


def get_fixture(fixture_id: str) -> Optional[dict]:
    builder = _FIXTURE_BUILDERS.get(fixture_id)
    if builder is None:
        return None
    return builder()


def replay_fixture(fixture_id: str) -> ReviewFixtureResult:
    fixture = get_fixture(fixture_id)
    if fixture is None:
        return ReviewFixtureResult(
            fixture_id=fixture_id,
            error=f"Unknown fixture: {fixture_id}",
        )

    candidate_date = fixture["candidate_date"]
    review_date = fixture["review_date"]
    performances = fixture["performances"]

    try:
        for p in performances:
            p.compute_returns()

        summary = run_post_market_review(
            performances,
            candidate_date=candidate_date,
            review_date=review_date,
        )

        return _summary_to_result(fixture_id, summary)
    except Exception as exc:
        logger.error("replay_fixture %s failed: %s", fixture_id, exc)
        return ReviewFixtureResult(
            fixture_id=fixture_id,
            candidate_date=candidate_date,
            review_date=review_date,
            error=str(exc),
        )


def _summary_to_result(fixture_id: str, summary: ReviewSummary) -> ReviewFixtureResult:
    return ReviewFixtureResult(
        fixture_id=fixture_id,
        candidates_count=summary.total_candidates,
        scored_candidates=summary.scored_candidates,
        no_data_candidates=summary.no_data_candidates,
        hit_count=summary.overall_hit_count,
        miss_count=summary.overall_miss_count,
        invalidated_count=summary.overall_invalidated_count,
        removal_reasons=summary.common_removal_reasons,
        suggestions=summary.suggestions,
        strategy_stats_keys=sorted(summary.strategy_stats.keys()),
        tier_stats_keys=sorted(summary.tier_stats.keys()),
        overall_hit_rate=summary.overall_hit_rate,
        overall_false_positive_rate=summary.overall_false_positive_rate,
        avg_next_day_return=summary.avg_next_day_return,
        review_date=summary.review_date,
        candidate_date=summary.candidate_date,
    )


def replay_all() -> list[ReviewFixtureResult]:
    results = []
    for fid in ALL_FIXTURE_IDS:
        results.append(replay_fixture(fid))
    return results


def validate_fixture_expectations(results: list[ReviewFixtureResult]) -> list[str]:
    issues = []
    for result in results:
        fixture = get_fixture(result.fixture_id)
        if fixture is None:
            issues.append(f"{result.fixture_id}: unknown fixture")
            continue
        if result.error:
            issues.append(f"{result.fixture_id}: replay error: {result.error}")
            continue

        expected = fixture["expected"]
        if result.hit_count != expected.get("hit_count", 0):
            issues.append(
                f"{result.fixture_id}: expected hit_count={expected.get('hit_count', 0)}, got {result.hit_count}"
            )
        if result.miss_count != expected.get("miss_count", 0):
            issues.append(
                f"{result.fixture_id}: expected miss_count={expected.get('miss_count', 0)}, got {result.miss_count}"
            )
        if result.invalidated_count != expected.get("invalidated_count", 0):
            issues.append(
                f"{result.fixture_id}: expected invalidated_count={expected.get('invalidated_count', 0)}, got {result.invalidated_count}"
            )
        if result.no_data_candidates != expected.get("no_data_count", 0):
            issues.append(
                f"{result.fixture_id}: expected no_data_count={expected.get('no_data_count', 0)}, got {result.no_data_candidates}"
            )

        expected_strategies = expected.get("strategy_keys", set())
        actual_strategies = set(result.strategy_stats_keys)
        if expected_strategies and actual_strategies != expected_strategies:
            issues.append(
                f"{result.fixture_id}: expected strategy_keys={sorted(expected_strategies)}, got {sorted(actual_strategies)}"
            )

        expected_tiers = expected.get("tier_keys", set())
        actual_tiers = set(result.tier_stats_keys)
        if expected_tiers and actual_tiers != expected_tiers:
            issues.append(
                f"{result.fixture_id}: expected tier_keys={sorted(expected_tiers)}, got {sorted(actual_tiers)}"
            )

        if expected.get("has_removal_reason"):
            if not result.removal_reasons:
                issues.append(f"{result.fixture_id}: expected removal reasons but got none")
            keyword = expected.get("removal_keyword", "")
            if keyword and not any(keyword in r for r in result.removal_reasons):
                issues.append(f"{result.fixture_id}: expected removal keyword '{keyword}' not found")
    return issues


def validate_no_forbidden_words(results: list[ReviewFixtureResult]) -> list[str]:
    issues = []
    for result in results:
        if result.error:
            continue
        fixture = get_fixture(result.fixture_id)
        if fixture is None:
            continue

        performances = fixture["performances"]
        for p in performances:
            p.compute_returns()
        summary = run_post_market_review(
            performances,
            candidate_date=fixture["candidate_date"],
            review_date=fixture["review_date"],
        )
        markdown = render_review_markdown(summary)
        for word in FORBIDDEN_WORDS:
            if word in markdown:
                issues.append(f"{result.fixture_id}: forbidden word '{word}' in review markdown")
    return issues
