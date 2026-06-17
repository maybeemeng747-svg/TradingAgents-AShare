# [TF-REVIEW-003] strategy_attribution_review tests
"""Tests for post-market Review strategy hit attribution and next-day feedback.

Covers:
1. HitAttribution enum (5 values + labels).
2. classify_hit_attribution — technical/policy/fund_flow/data_issue/risk_hit.
3. compute_next_day_feedback — tomorrow_focus / downgrade_reason / evidence_needed.
4. CandidatePerformance.compute_attribution integration.
5. StrategyStats attributions tracking.
6. compute_candidate_type_stats aggregation.
7. compute_attribution_stats aggregation.
8. run_post_market_review with attribution + next_day_feedback.
9. Fixture scenario: ≥1 hit, ≥1 invalidated, ≥1 not-triggered (waiting).
10. Service-layer get_review returns hit_type/tomorrow_focus/downgrade_reason/evidence_needed.
11. Markdown render includes 命中归因 / 次日反馈 sections.
"""

import tempfile

import pytest

from tradingagents.tradeflow.post_market_review import (
    HitAttribution,
    CandidatePerformance,
    StrategyStats,
    ReviewSummary,
    ReviewDataStatus,
    classify_hit_attribution,
    compute_next_day_feedback,
    compute_strategy_stats,
    compute_tier_stats,
    compute_candidate_type_stats,
    compute_attribution_stats,
    run_post_market_review,
    render_review_markdown,
    build_candidate_performance_from_dict,
)
from tradingagents.tradeflow.schemas import Candidate


# ── helpers ──


def _make_perf(
    symbol="000001.SZ",
    trigger_price=10.0,
    invalid_price=9.0,
    next_day_close=None,
    observe_state="WAITING",
    candidate_type="",
    strategy_tags=None,
    risk_flags=None,
    split_scores=None,
    missing_evidence=None,
    trade_date="2026-05-29",
) -> CandidatePerformance:
    perf = CandidatePerformance(
        symbol=symbol,
        trade_date=trade_date,
        entry_price=float(trigger_price or 0),
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        strategy_tags=strategy_tags or ["VCP"],
        observe_state=observe_state,
        risk_flags=risk_flags or [],
        candidate_type=candidate_type,
        split_scores=split_scores or {},
        evidence_needed=list(missing_evidence or []),
    )
    perf.next_day_close = next_day_close
    perf.compute_returns()
    perf.compute_attribution()
    return perf


# ── HitAttribution ──


class TestHitAttribution:
    def test_five_values(self):
        assert HitAttribution.TECHNICAL_HIT.value == "technical_hit"
        assert HitAttribution.POLICY_HIT.value == "policy_hit"
        assert HitAttribution.FUND_FLOW_HIT.value == "fund_flow_hit"
        assert HitAttribution.DATA_ISSUE.value == "data_issue"
        assert HitAttribution.RISK_HIT.value == "risk_hit"

    def test_all_values_count(self):
        assert len(HitAttribution.all_values()) == 5

    def test_label_cn(self):
        assert HitAttribution.TECHNICAL_HIT.label_cn == "技术命中"
        assert HitAttribution.POLICY_HIT.label_cn == "政策命中"
        assert HitAttribution.FUND_FLOW_HIT.label_cn == "资金流命中"
        assert HitAttribution.DATA_ISSUE.label_cn == "数据不足"
        assert HitAttribution.RISK_HIT.label_cn == "风险触发"


# ── classify_hit_attribution ──


class TestClassifyHitAttribution:
    def test_data_issue_when_no_data(self):
        perf = _make_perf(next_day_close=None)
        assert classify_hit_attribution(perf) == "data_issue"

    def test_risk_hit_when_invalidated_with_risk_flags(self):
        perf = _make_perf(
            next_day_close=8.5,  # below invalid_price 9.0
            risk_flags=["high_volatility"],
        )
        assert perf.invalidated is True
        assert classify_hit_attribution(perf) == "risk_hit"

    def test_policy_hit_for_policy_ambush_candidate_type(self):
        perf = _make_perf(
            next_day_close=10.5,
            candidate_type="POLICY_AMBUSH",
            split_scores={"technical_score": 80, "policy_score": 30, "fund_flow_score": 10},
        )
        assert classify_hit_attribution(perf) == "policy_hit"

    def test_policy_hit_when_policy_score_dominates(self):
        perf = _make_perf(
            next_day_close=10.5,
            candidate_type="TECH_TRADE",
            split_scores={"technical_score": 30, "policy_score": 80, "fund_flow_score": 10},
        )
        assert classify_hit_attribution(perf) == "policy_hit"

    def test_fund_flow_hit_when_fund_dominates(self):
        perf = _make_perf(
            next_day_close=10.5,
            split_scores={"technical_score": 20, "policy_score": 10, "fund_flow_score": 80},
        )
        assert classify_hit_attribution(perf) == "fund_flow_hit"

    def test_technical_hit_default(self):
        perf = _make_perf(
            next_day_close=10.5,
            split_scores={"technical_score": 80, "policy_score": 20, "fund_flow_score": 10},
        )
        assert classify_hit_attribution(perf) == "technical_hit"

    def test_fallback_to_strategy_tags_when_no_scores(self):
        perf = _make_perf(
            next_day_close=10.5,
            strategy_tags=["POLICY_VERSION"],
            split_scores={},
        )
        assert classify_hit_attribution(perf) == "policy_hit"

    def test_fallback_fund_flow_tag(self):
        perf = _make_perf(
            next_day_close=10.5,
            strategy_tags=["FUND_FLOW_ANOMALY"],
            split_scores={},
        )
        assert classify_hit_attribution(perf) == "fund_flow_hit"


# ── compute_next_day_feedback ──


class TestComputeNextDayFeedback:
    def test_triggered_hit(self):
        perf = _make_perf(observe_state="TRIGGERED", next_day_close=10.5, trigger_price=10.0)
        perf.hit = True
        tf, dr, en = compute_next_day_feedback(perf)
        assert "站稳" in tf
        assert dr == ""

    def test_triggered_miss(self):
        perf = _make_perf(observe_state="TRIGGERED", next_day_close=10.1, trigger_price=10.5)
        perf.hit = False
        tf, dr, en = compute_next_day_feedback(perf)
        assert "假突破" in tf

    def test_invalidated(self):
        perf = _make_perf(observe_state="INVALIDATED", next_day_close=8.5, invalid_price=9.0)
        tf, dr, en = compute_next_day_feedback(perf)
        assert "失效" in tf
        assert "失效价" in dr

    def test_expired(self):
        perf = _make_perf(observe_state="EXPIRED", next_day_close=None)
        tf, dr, en = compute_next_day_feedback(perf)
        assert "观察期" in tf or "未触发" in tf

    def test_waiting(self):
        perf = _make_perf(observe_state="WAITING", next_day_close=None)
        tf, dr, en = compute_next_day_feedback(perf)
        assert "等待" in tf

    def test_evidence_needed_passed_through(self):
        perf = _make_perf(missing_evidence=["fund_flow", "lhb_detail"])
        _, _, en = compute_next_day_feedback(perf)
        assert "fund_flow" in en
        assert "lhb_detail" in en


# ── compute_attribution integration on CandidatePerformance ──


class TestCandidatePerformanceAttribution:
    def test_compute_attribution_sets_hit_type(self):
        perf = _make_perf(
            next_day_close=10.6,
            trigger_price=10.5,
            split_scores={"technical_score": 80, "policy_score": 20, "fund_flow_score": 10},
        )
        assert perf.hit_type == "technical_hit"

    def test_compute_attribution_sets_tomorrow_focus(self):
        perf = _make_perf(observe_state="TRIGGERED", next_day_close=10.5)
        assert perf.tomorrow_focus != ""

    def test_compute_attribution_sets_downgrade_reason(self):
        perf = _make_perf(observe_state="INVALIDATED", next_day_close=8.5, invalid_price=9.0)
        assert perf.downgrade_reason != ""

    def test_compute_attribution_sets_evidence_needed(self):
        perf = _make_perf(missing_evidence=["news", "fund_flow"])
        assert perf.evidence_needed == ["news", "fund_flow"]


# ── StrategyStats attributions ──


class TestStrategyStatsAttributions:
    def test_attributions_tracked(self):
        perfs = [
            _make_perf(symbol="A", next_day_close=10.5, split_scores={"technical_score": 80}),
            _make_perf(symbol="B", next_day_close=8.5, invalid_price=9.0, risk_flags=["x"]),
            _make_perf(symbol="C", next_day_close=None),
        ]
        stats = compute_strategy_stats(perfs)
        vcp = stats.get("VCP")
        assert vcp is not None
        assert "technical_hit" in vcp.attributions
        assert "risk_hit" in vcp.attributions
        assert "data_issue" in vcp.attributions


# ── compute_candidate_type_stats ──


class TestComputeCandidateTypeStats:
    def test_basic_aggregation(self):
        perfs = [
            _make_perf(symbol="A", candidate_type="POLICY_AMBUSH", next_day_close=10.5),
            _make_perf(symbol="B", candidate_type="TECH_TRADE", next_day_close=8.5, invalid_price=9.0, risk_flags=["x"]),
            _make_perf(symbol="C", candidate_type="TECH_TRADE", next_day_close=None),
        ]
        ct_stats = compute_candidate_type_stats(perfs)
        assert ct_stats["POLICY_AMBUSH"]["total"] == 1
        assert ct_stats["TECH_TRADE"]["total"] == 2

    def test_unclassified_when_empty(self):
        perf = _make_perf(symbol="A", candidate_type="", next_day_close=10.5)
        ct_stats = compute_candidate_type_stats([perf])
        assert "UNCLASSIFIED" in ct_stats


# ── compute_attribution_stats ──


class TestComputeAttributionStats:
    def test_all_categories_present(self):
        perfs = [_make_perf(symbol="A", next_day_close=10.5)]
        attr = compute_attribution_stats(perfs)
        for val in HitAttribution.all_values():
            assert val in attr

    def test_counts(self):
        perfs = [
            _make_perf(symbol="A", next_day_close=10.5, split_scores={"technical_score": 80}),
            _make_perf(symbol="B", next_day_close=8.5, invalid_price=9.0, risk_flags=["x"]),
            _make_perf(symbol="C", next_day_close=None),
        ]
        attr = compute_attribution_stats(perfs)
        assert attr["technical_hit"]["total"] == 1
        assert attr["risk_hit"]["total"] == 1
        assert attr["data_issue"]["total"] == 1
        assert "A" in attr["technical_hit"]["symbols"]
        assert "B" in attr["risk_hit"]["symbols"]

    def test_label_cn_in_stats(self):
        perfs = [_make_perf(symbol="A", next_day_close=10.5)]
        attr = compute_attribution_stats(perfs)
        assert attr["technical_hit"]["label"] == "技术命中"


# ── run_post_market_review with attribution ──


class TestRunPostMarketReviewAttribution:
    def test_summary_has_attribution_fields(self):
        perfs = [
            _make_perf(symbol="A", next_day_close=10.5, split_scores={"technical_score": 80}),
            _make_perf(symbol="B", next_day_close=8.5, invalid_price=9.0, risk_flags=["x"]),
            _make_perf(symbol="C", next_day_close=None),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        assert hasattr(summary, "candidate_type_stats")
        assert hasattr(summary, "attribution_stats")
        assert hasattr(summary, "next_day_feedback")

    def test_next_day_feedback_populated(self):
        perfs = [
            _make_perf(symbol="A", next_day_close=10.5, split_scores={"technical_score": 80}),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        assert len(summary.next_day_feedback) == 1
        fb = summary.next_day_feedback[0]
        assert fb["symbol"] == "A"
        assert fb["hit_type"] == "technical_hit"
        assert fb["tomorrow_focus"]
        assert fb["downgrade_reason"] == ""  # hit, no downgrade

    def test_attribution_stats_in_summary(self):
        perfs = [
            _make_perf(symbol="A", next_day_close=10.5, split_scores={"technical_score": 80}),
            _make_perf(symbol="B", next_day_close=None),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        assert summary.attribution_stats["technical_hit"]["total"] == 1
        assert summary.attribution_stats["data_issue"]["total"] == 1


# ── Fixture scenario: hit / invalidated / not-triggered ──


class TestFixtureScenario:
    """Acceptance: fixture can produce ≥1 hit, ≥1 invalidated, ≥1 not-triggered."""

    def test_three_outcome_types(self):
        perfs = [
            # hit — technical
            _make_perf(
                symbol="000001.SZ",
                trigger_price=10.0,
                invalid_price=9.0,
                next_day_close=10.6,
                observe_state="TRIGGERED",
                split_scores={"technical_score": 80, "policy_score": 20, "fund_flow_score": 10},
            ),
            # invalidated — risk
            _make_perf(
                symbol="000002.SZ",
                trigger_price=10.0,
                invalid_price=9.0,
                next_day_close=8.5,
                observe_state="INVALIDATED",
                risk_flags=["limit_down_risk"],
            ),
            # not triggered — waiting (no data)
            _make_perf(
                symbol="000003.SZ",
                trigger_price=10.0,
                invalid_price=9.0,
                next_day_close=None,
                observe_state="WAITING",
            ),
        ]

        assert perfs[0].hit is True
        assert perfs[1].invalidated is True
        assert perfs[2].hit is None

        # Attribution
        assert perfs[0].hit_type == "technical_hit"
        assert perfs[1].hit_type == "risk_hit"
        assert perfs[2].hit_type == "data_issue"

        # Feedback
        assert "站稳" in perfs[0].tomorrow_focus
        assert "失效" in perfs[1].tomorrow_focus
        assert "等待" in perfs[2].tomorrow_focus

    def test_summary_full_run(self):
        perfs = [
            _make_perf(
                symbol="000001.SZ", next_day_close=10.6, observe_state="TRIGGERED",
                split_scores={"technical_score": 80},
                candidate_type="TECH_TRADE",
            ),
            _make_perf(
                symbol="000002.SZ", next_day_close=8.5, observe_state="INVALIDATED",
                invalid_price=9.0, risk_flags=["limit_down_risk"],
            ),
            _make_perf(
                symbol="000003.SZ", next_day_close=None, observe_state="WAITING",
                candidate_type="POLICY_AMBUSH", missing_evidence=["fund_flow", "lhb"],
            ),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")

        assert summary.overall_hit_count == 1
        assert summary.overall_invalidated_count == 1
        assert summary.no_data_candidates == 1

        # candidate_type_stats
        assert summary.candidate_type_stats["TECH_TRADE"]["hit"] == 1
        assert summary.candidate_type_stats["POLICY_AMBUSH"]["no_data"] == 1

        # attribution_stats
        assert summary.attribution_stats["technical_hit"]["total"] == 1
        assert summary.attribution_stats["risk_hit"]["total"] == 1
        assert summary.attribution_stats["data_issue"]["total"] == 1

        # next_day_feedback — at least the waiting candidate carries evidence_needed
        waiting_fb = [f for f in summary.next_day_feedback if f["symbol"] == "000003.SZ"][0]
        assert "fund_flow" in waiting_fb["evidence_needed"]


# ── Markdown render includes attribution sections ──


class TestRenderMarkdownAttribution:
    def test_markdown_has_attribution_section(self):
        perfs = [
            _make_perf(symbol="A", next_day_close=10.5, split_scores={"technical_score": 80}),
            _make_perf(symbol="B", next_day_close=None),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        md = render_review_markdown(summary)
        assert "命中归因" in md
        assert "次日反馈" in md

    def test_markdown_has_candidate_type_section(self):
        perfs = [
            _make_perf(symbol="A", candidate_type="TECH_TRADE", next_day_close=10.5),
        ]
        summary = run_post_market_review(perfs, candidate_date="2026-05-29", review_date="2026-05-30")
        md = render_review_markdown(summary)
        assert "候选类型表现" in md


# ── build_candidate_performance_from_dict ──


class TestBuildFromDictAttribution:
    def test_dict_has_attribution(self):
        entry = {
            "symbol": "000001.SZ",
            "trade_date": "2026-05-29",
            "trigger_price": 10.0,
            "invalid_price": 9.0,
            "strategy_tags": ["VCP"],
            "candidate_type": "POLICY_AMBUSH",
            "technical_score": 30,
            "policy_score": 80,
            "fund_flow_score": 10,
            "missing_evidence": ["fund_flow"],
        }
        perf = build_candidate_performance_from_dict(entry, next_day_close=10.5)
        assert perf.hit_type == "policy_hit"
        assert perf.candidate_type == "POLICY_AMBUSH"
        assert "fund_flow" in perf.evidence_needed
        assert perf.tomorrow_focus != ""


# ── Service-layer: get_review returns attribution ──


class TestServiceGetReviewAttribution:
    def test_get_review_returns_hit_type(self):
        from api.services.tradeflow_service import get_review
        from tradingagents.tradeflow.candidate_engine import init_db

        with tempfile.TemporaryDirectory() as tmp:
            db_path = f"{tmp}/tradeflow_test.db"
            init_db(db_path)

            # Insert a daily plan with candidates
            import sqlite3
            import json
            conn = sqlite3.connect(db_path)
            candidates_json = json.dumps([
                {
                    "symbol": "000001.SZ",
                    "name": "测试股A",
                    "action": "OBSERVE",
                    "tier": "A",
                    "composite_score": 75.0,
                    "strategy_tags": ["VCP"],
                    "trigger_price": 10.0,
                    "invalid_price": 9.0,
                    "observe_state": "TRIGGERED",
                    "candidate_type": "TECH_TRADE",
                    "technical_score": 80,
                    "policy_score": 20,
                    "fund_flow_score": 10,
                    "missing_evidence": [],
                    "risk_flags": [],
                },
                {
                    "symbol": "000002.SZ",
                    "name": "测试股B",
                    "action": "OBSERVE",
                    "tier": "B",
                    "composite_score": 60.0,
                    "strategy_tags": ["VCP"],
                    "trigger_price": 10.0,
                    "invalid_price": 9.0,
                    "observe_state": "WAITING",
                    "candidate_type": "POLICY_AMBUSH",
                    "technical_score": 20,
                    "policy_score": 80,
                    "fund_flow_score": 10,
                    "missing_evidence": ["fund_flow", "lhb_detail"],
                    "risk_flags": [],
                },
            ])
            conn.execute(
                "INSERT INTO tradeflow_daily_plans (trade_date, mode, summary, candidates_json, metadata_json) "
                "VALUES (?, ?, ?, ?, ?)",
                ("2026-05-29", "pre_market", "test", candidates_json, "{}"),
            )
            conn.commit()
            conn.close()

            result = get_review("2026-05-29", tf_db_path=db_path)
            assert result["status"] == "ok"
            results = result["results"]
            assert len(results) == 2

            # First candidate: TRIGGERED TECH_TRADE -> technical_hit
            r0 = [r for r in results if r["symbol"] == "000001.SZ"][0]
            assert r0["hit_type"] == "technical_hit"
            assert r0["tomorrow_focus"]
            assert "candidate_type" in r0

            # Second candidate: WAITING POLICY_AMBUSH with no returns -> data_issue
            # (candidate_type is noted but outcome can't be evaluated without market data)
            r1 = [r for r in results if r["symbol"] == "000002.SZ"][0]
            assert r1["hit_type"] == "data_issue"
            assert r1["downgrade_reason"] == ""  # waiting, no downgrade
            assert "fund_flow" in r1["evidence_needed"]
