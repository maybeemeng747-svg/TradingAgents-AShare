# [TF-REVIEW-005] review_observe_paper_attribution tests
"""Tests for post-market Review attribution joining observe signals + paper ledger.

Covers:
1. ReviewBucket enum (5 values + Chinese labels + worth_review flag).
2. classify_review_bucket — risk-first precedence across paper_status /
   observe_state / signal_state / has_signal_for_date (pending / open /
   closed / invalidated / triggered / waiting / expired / data-missing).
3. review_bucket_reason — non-empty Chinese explanations, no buy/sell words.
4. compute_today_review_focus — only worth_review buckets included, risk-first
   ordering, bucket_counts aggregation.
5. CandidatePerformance.compute_review_bucket + build_candidate_performance_from_dict
   carrying paper/signal fields.
6. run_post_market_review produces today_review_focus.
7. render_review_markdown includes the "今天实际值得复盘的票" section.
8. Service-layer get_review — fixtures with pending/open/invalidated paper
   trades + observe signals; verifies per-candidate review_bucket and the
   today_review_focus summary.
9. generate_review — persisted report carries observe+paper attribution.
10. Forbidden-word scan — no strong buy/sell wording in any output.
11. Fixture document fragment generation — replayable sample doc.
"""

import json
import sqlite3

import pytest

from tradingagents.tradeflow.post_market_review import (
    ReviewBucket,
    classify_review_bucket,
    review_bucket_reason,
    compute_today_review_focus,
    CandidatePerformance,
    build_candidate_performance_from_dict,
    run_post_market_review,
    render_review_markdown,
)
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.schemas import Candidate, FORBIDDEN_WORDS


# ── helpers ──


def _insert_signal(
    db_path,
    symbol,
    trade_date,
    *,
    signal_type="observe_check",
    observe_state="WAITING",
    current_price=10.0,
    trigger_reason="",
    signal_time=None,
):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tradeflow_signals "
        "(signal_time, symbol, signal_type, signal_level, source, evidence_json, "
        "action_hint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            signal_time or f"{trade_date}T10:30:00",
            symbol,
            signal_type,
            "info",
            "observe_runner",
            json.dumps(
                {
                    "trade_date": trade_date,
                    "observe_state": observe_state,
                    "current_price": current_price,
                    "trigger_reason": trigger_reason,
                }
            ),
            "OBSERVE",
            "new",
            signal_time or f"{trade_date}T10:30:00",
        ),
    )
    conn.commit()
    conn.close()


def _insert_paper_trade(
    db_path,
    symbol,
    trade_date,
    *,
    status="tracking",
    name="",
    trigger_price=10.0,
    invalid_price=9.0,
    planned_amount=1000.0,
    observe_state="WAITING",
):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tradeflow_paper_trades "
        "(symbol, name, trade_date, plan_date, candidate_type, trigger_price, "
        "invalid_price, planned_amount, status, action_type, action_price, "
        "action_date, confirmed, note, pnl, pnl_pct, observe_state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            symbol,
            name,
            trade_date,
            trade_date,
            "TECH_TRADE",
            trigger_price,
            invalid_price,
            planned_amount,
            status,
            "",
            None,
            "",
            1 if status in ("open", "closed") else 0,
            "",
            0.0,
            0.0,
            observe_state,
            f"{trade_date}T09:00:00",
            f"{trade_date}T09:00:00",
        ),
    )
    conn.commit()
    conn.close()


def _make_perf(
    symbol="A",
    *,
    observe_state="WAITING",
    paper_status="",
    signal_state="",
    has_signal_for_date=False,
    candidate_type="TECH_TRADE",
    **kwargs,
):
    perf = CandidatePerformance(
        symbol=symbol,
        trade_date="2026-06-02",
        entry_price=float(kwargs.get("trigger_price", 10.0) or 0),
        trigger_price=kwargs.get("trigger_price", 10.0),
        invalid_price=kwargs.get("invalid_price", 9.0),
        observe_state=observe_state,
        candidate_type=candidate_type,
        paper_status=paper_status,
        signal_state=signal_state,
        has_signal_for_date=has_signal_for_date,
    )
    perf.compute_attribution()
    perf.compute_review_bucket()
    return perf


def _build_plan_db(tmp_path, trade_date, candidates, *, db_name="tf_review_005.db"):
    """Create an isolated tradeflow DB with a daily plan row for get_review."""
    db_path = str(tmp_path / db_name)
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tradeflow_daily_plans "
        "(trade_date, mode, summary, candidates_json, metadata_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (trade_date, "pre_market", "fixture", json.dumps(candidates), "{}"),
    )
    conn.commit()
    conn.close()
    return db_path


def _save_candidate_row(db_path, symbol, name, trade_date, observe_state="WAITING"):
    """Insert a candidate row into tradeflow_candidates (used by generate_review
    which reads the candidates table, not daily_plans)."""
    cand = Candidate(
        symbol=symbol,
        name=name,
        source="manual",
        trade_date=trade_date,
        trigger_price=10.0,
        invalid_price=9.0,
        status="active",
        observe_state=observe_state,
        candidate_type="TECH_TRADE",
        composite_score=70.0,
        tier="A",
        strategy_tags=["VCP"],
        technical_score=80,
        policy_score=10,
        fund_flow_score=10,
        effective_trade_date=trade_date,
    )
    save_candidate(cand, db_path)


# ── ReviewBucket enum ──


class TestReviewBucketEnum:
    def test_five_values_exist(self):
        assert ReviewBucket.NOT_TRIGGERED
        assert ReviewBucket.TRIGGERED_PENDING
        assert ReviewBucket.CONFIRMED
        assert ReviewBucket.INVALIDATED
        assert ReviewBucket.DATA_MISSING

    def test_distinct_values(self):
        vals = ReviewBucket.all_values()
        assert len(vals) == len(set(vals)) == 5

    def test_labels_are_chinese_and_match_required(self):
        assert ReviewBucket.NOT_TRIGGERED.label_cn == "未触发"
        assert ReviewBucket.TRIGGERED_PENDING.label_cn == "触发待确认"
        assert ReviewBucket.CONFIRMED.label_cn == "已确认"
        assert ReviewBucket.INVALIDATED.label_cn == "已失效"
        assert ReviewBucket.DATA_MISSING.label_cn == "缺数据"

    def test_worth_review_flag(self):
        assert ReviewBucket.TRIGGERED_PENDING.worth_review is True
        assert ReviewBucket.CONFIRMED.worth_review is True
        assert ReviewBucket.INVALIDATED.worth_review is True
        # not-triggered / data-missing are NOT worth a focus review
        assert ReviewBucket.NOT_TRIGGERED.worth_review is False
        assert ReviewBucket.DATA_MISSING.worth_review is False


# ── classify_review_bucket ──


class TestClassifyReviewBucket:
    def test_paper_pending_is_triggered_pending(self):
        b = classify_review_bucket("WAITING", "pending", True)
        assert b is ReviewBucket.TRIGGERED_PENDING

    def test_paper_open_is_confirmed(self):
        b = classify_review_bucket("WAITING", "open", True)
        assert b is ReviewBucket.CONFIRMED

    def test_paper_closed_is_confirmed(self):
        b = classify_review_bucket("WAITING", "closed", True)
        assert b is ReviewBucket.CONFIRMED

    def test_paper_invalidated_is_invalidated(self):
        b = classify_review_bucket("WAITING", "invalidated", True)
        assert b is ReviewBucket.INVALIDATED

    def test_observe_state_invalidated_is_invalidated(self):
        b = classify_review_bucket("INVALIDATED", "", True)
        assert b is ReviewBucket.INVALIDATED

    def test_signal_invalidated_is_invalidated(self):
        b = classify_review_bucket("WAITING", "", True, signal_state="INVALIDATED")
        assert b is ReviewBucket.INVALIDATED

    def test_observe_state_triggered_is_triggered_pending(self):
        b = classify_review_bucket("TRIGGERED", "", True)
        assert b is ReviewBucket.TRIGGERED_PENDING

    def test_signal_triggered_is_triggered_pending(self):
        b = classify_review_bucket("WAITING", "", True, signal_state="TRIGGERED")
        assert b is ReviewBucket.TRIGGERED_PENDING

    def test_waiting_with_signal_is_not_triggered(self):
        b = classify_review_bucket("WAITING", "", True)
        assert b is ReviewBucket.NOT_TRIGGERED

    def test_expired_is_not_triggered(self):
        b = classify_review_bucket("EXPIRED", "", False)
        assert b is ReviewBucket.NOT_TRIGGERED

    def test_no_signal_no_trigger_is_data_missing(self):
        b = classify_review_bucket("WAITING", "", False)
        assert b is ReviewBucket.DATA_MISSING

    def test_invalidated_takes_precedence_over_open(self):
        """Risk-first: if both invalidated signal and open paper exist,
        the candidate is flagged invalidated (needs attention)."""
        b = classify_review_bucket("INVALIDATED", "open", True)
        assert b is ReviewBucket.INVALIDATED

    def test_confirmed_takes_precedence_over_triggered(self):
        """A confirmed (open) position wins over a mere trigger signal."""
        b = classify_review_bucket("TRIGGERED", "open", True)
        assert b is ReviewBucket.CONFIRMED


# ── review_bucket_reason ──


class TestReviewBucketReason:
    def test_reason_nonempty_for_each_bucket(self):
        cases = [
            (ReviewBucket.INVALIDATED, {"observe_state": "INVALIDATED"}),
            (ReviewBucket.CONFIRMED, {"paper_status": "open"}),
            (ReviewBucket.TRIGGERED_PENDING, {"paper_status": "pending"}),
            (ReviewBucket.NOT_TRIGGERED, {"has_signal_for_date": True}),
            (ReviewBucket.DATA_MISSING, {}),
        ]
        for bucket, ctx in cases:
            reason = review_bucket_reason(bucket, **ctx)
            assert reason != ""
            assert any("\u4e00" <= ch <= "\u9fff" for ch in reason)

    def test_pending_reason_mentions_paper(self):
        reason = review_bucket_reason(
            ReviewBucket.TRIGGERED_PENDING, paper_status="pending"
        )
        assert "模拟账本" in reason or "确认" in reason

    def test_open_reason_mentions_confirmation(self):
        reason = review_bucket_reason(ReviewBucket.CONFIRMED, paper_status="open")
        assert "确认" in reason

    def test_invalidated_reason_mentions_invalidated(self):
        reason = review_bucket_reason(
            ReviewBucket.INVALIDATED, observe_state="INVALIDATED"
        )
        assert "失效" in reason

    def test_no_forbidden_words_in_reasons(self):
        for bucket in ReviewBucket:
            reason = review_bucket_reason(bucket)
            for word in FORBIDDEN_WORDS:
                assert word not in reason


# ── compute_today_review_focus ──


class TestComputeTodayReviewFocus:
    def test_only_worth_review_buckets_appear(self):
        items = [
            {"symbol": "A", "name": "A名", "review_bucket": "triggered_pending"},
            {"symbol": "B", "name": "B名", "review_bucket": "confirmed"},
            {"symbol": "C", "name": "C名", "review_bucket": "invalidated"},
            {"symbol": "D", "name": "D名", "review_bucket": "not_triggered"},
            {"symbol": "E", "name": "E名", "review_bucket": "data_missing"},
        ]
        focus = compute_today_review_focus(items)
        syms = [f["symbol"] for f in focus["focus_items"]]
        assert set(syms) == {"A", "B", "C"}
        assert focus["focus_count"] == 3

    def test_bucket_counts_cover_all_five(self):
        items = [
            {"symbol": "A", "review_bucket": "triggered_pending"},
            {"symbol": "B", "review_bucket": "invalidated"},
            {"symbol": "B", "review_bucket": "invalidated"},
            {"symbol": "D", "review_bucket": "not_triggered"},
        ]
        focus = compute_today_review_focus(items)
        assert focus["bucket_counts"]["triggered_pending"] == 1
        assert focus["bucket_counts"]["invalidated"] == 2
        assert focus["bucket_counts"]["not_triggered"] == 1
        assert focus["bucket_counts"]["confirmed"] == 0
        assert focus["bucket_counts"]["data_missing"] == 0

    def test_risk_first_ordering(self):
        items = [
            {"symbol": "C", "review_bucket": "confirmed"},
            {"symbol": "A", "review_bucket": "invalidated"},
            {"symbol": "B", "review_bucket": "triggered_pending"},
        ]
        focus = compute_today_review_focus(items)
        order = [f["review_bucket"] for f in focus["focus_items"]]
        assert order == ["invalidated", "triggered_pending", "confirmed"]

    def test_empty_items(self):
        focus = compute_today_review_focus([])
        assert focus["focus_items"] == []
        assert focus["focus_count"] == 0
        assert all(v == 0 for v in focus["bucket_counts"].values())

    def test_unknown_bucket_falls_back_to_data_missing(self):
        items = [{"symbol": "X", "review_bucket": "nonsense"}]
        focus = compute_today_review_focus(items)
        assert focus["bucket_counts"]["data_missing"] == 1
        assert focus["focus_count"] == 0


# ── CandidatePerformance + build_from_dict ──


class TestCandidatePerformanceBucket:
    def test_compute_review_bucket_sets_all_three_fields(self):
        perf = _make_perf("A", observe_state="TRIGGERED", paper_status="pending")
        assert perf.review_bucket == "triggered_pending"
        assert perf.review_bucket_label == "触发待确认"
        assert perf.review_bucket_reason != ""

    def test_build_from_dict_reads_paper_signal_fields(self):
        entry = {
            "symbol": "000001.SZ",
            "trade_date": "2026-06-02",
            "trigger_price": 10.0,
            "invalid_price": 9.0,
            "observe_state": "WAITING",
            "candidate_type": "TECH_TRADE",
            "paper_status": "open",
            "signal_state": "WAITING",
            "has_signal_for_date": True,
        }
        perf = build_candidate_performance_from_dict(entry)
        assert perf.paper_status == "open"
        assert perf.has_signal_for_date is True
        assert perf.review_bucket == "confirmed"

    def test_build_from_dict_defaults_to_data_missing(self):
        entry = {"symbol": "X", "trade_date": "2026-06-02", "trigger_price": 10.0}
        perf = build_candidate_performance_from_dict(entry)
        assert perf.paper_status == ""
        assert perf.has_signal_for_date is False
        assert perf.review_bucket == "data_missing"


# ── run_post_market_review + render ──


class TestRunReviewAndRender:
    def test_summary_has_today_review_focus(self):
        perfs = [
            _make_perf("A", observe_state="TRIGGERED", paper_status="pending"),
            _make_perf("B", observe_state="INVALIDATED"),
            _make_perf("C", observe_state="WAITING", has_signal_for_date=True),
            _make_perf("D", observe_state="WAITING", has_signal_for_date=False),
        ]
        summary = run_post_market_review(
            perfs, candidate_date="2026-06-02", review_date="2026-06-02"
        )
        focus = summary.today_review_focus
        assert focus["focus_count"] == 2  # A + B
        assert focus["bucket_counts"]["triggered_pending"] == 1
        assert focus["bucket_counts"]["invalidated"] == 1
        assert focus["bucket_counts"]["not_triggered"] == 1
        assert focus["bucket_counts"]["data_missing"] == 1

    def test_next_day_feedback_carries_bucket_fields(self):
        perfs = [
            _make_perf("A", observe_state="TRIGGERED", paper_status="pending"),
        ]
        summary = run_post_market_review(
            perfs, candidate_date="2026-06-02", review_date="2026-06-02"
        )
        fb = summary.next_day_feedback[0]
        assert fb["review_bucket"] == "triggered_pending"
        assert fb["review_bucket_label"] == "触发待确认"
        assert fb["paper_status"] == "pending"

    def test_render_markdown_has_focus_section(self):
        perfs = [
            _make_perf("A", observe_state="TRIGGERED", paper_status="pending"),
            _make_perf("B", observe_state="INVALIDATED"),
        ]
        summary = run_post_market_review(
            perfs, candidate_date="2026-06-02", review_date="2026-06-02"
        )
        md = render_review_markdown(summary)
        assert "今天实际值得复盘的票" in md
        assert "触发待确认" in md
        assert "已失效" in md

    def test_render_markdown_empty_focus_message(self):
        perfs = [
            _make_perf("A", observe_state="WAITING", has_signal_for_date=False),
        ]
        summary = run_post_market_review(
            perfs, candidate_date="2026-06-02", review_date="2026-06-02"
        )
        md = render_review_markdown(summary)
        assert "今天实际值得复盘的票" in md
        assert "暂无需重点复盘" in md

    def test_render_markdown_no_forbidden_words(self):
        perfs = [
            _make_perf("A", observe_state="TRIGGERED", paper_status="pending"),
            _make_perf("B", observe_state="INVALIDATED"),
            _make_perf("C", observe_state="WAITING", has_signal_for_date=True),
        ]
        summary = run_post_market_review(
            perfs, candidate_date="2026-06-02", review_date="2026-06-02"
        )
        md = render_review_markdown(summary)
        for word in FORBIDDEN_WORDS:
            assert word not in md


# ── Service-layer get_review with pending/open/invalidated fixtures ──


_CANDIDATE_TEMPLATE = {
    "action": "OBSERVE",
    "tier": "A",
    "composite_score": 70.0,
    "strategy_tags": ["VCP"],
    "trigger_price": 10.0,
    "invalid_price": 9.0,
    "candidate_type": "TECH_TRADE",
    "technical_score": 80,
    "policy_score": 10,
    "fund_flow_score": 10,
    "missing_evidence": [],
    "risk_flags": [],
}


def _candidate(symbol, name, observe_state="WAITING"):
    return {
        **_CANDIDATE_TEMPLATE,
        "symbol": symbol,
        "name": name,
        "observe_state": observe_state,
    }


class TestServiceGetReviewAttribution:
    def test_three_bucket_fixtures(self, tmp_path):
        """Acceptance fixture: pending / open / invalidated three categories."""
        from api.services.tradeflow_service import get_review

        trade_date = "2026-06-02"
        candidates = [
            _candidate("000001.SZ", "测试 pending", observe_state="TRIGGERED"),
            _candidate("000002.SZ", "测试 open", observe_state="TRIGGERED"),
            _candidate("000003.SZ", "测试 invalidated", observe_state="INVALIDATED"),
        ]
        db_path = _build_plan_db(tmp_path, trade_date, candidates)

        # paper ledger: pending / open / invalidated
        _insert_paper_trade(db_path, "000001.SZ", trade_date, status="pending", observe_state="TRIGGERED")
        _insert_paper_trade(db_path, "000002.SZ", trade_date, status="open", observe_state="TRIGGERED")
        _insert_paper_trade(db_path, "000003.SZ", trade_date, status="invalidated", observe_state="INVALIDATED")
        # observe signals for the date
        _insert_signal(
            db_path, "000001.SZ", trade_date,
            signal_type="observe_triggered", observe_state="TRIGGERED",
            current_price=10.1, trigger_reason="突破触发价",
        )
        _insert_signal(
            db_path, "000002.SZ", trade_date,
            signal_type="observe_triggered", observe_state="TRIGGERED",
            current_price=10.2,
        )
        _insert_signal(
            db_path, "000003.SZ", trade_date,
            signal_type="observe_invalidated", observe_state="INVALIDATED",
            current_price=8.8, trigger_reason="跌破失效价",
        )

        result = get_review(trade_date, tf_db_path=db_path)
        assert result["status"] == "ok"
        results = {r["symbol"]: r for r in result["results"]}

        # pending -> triggered_pending
        assert results["000001.SZ"]["review_bucket"] == "triggered_pending"
        assert results["000001.SZ"]["review_bucket_label"] == "触发待确认"
        assert results["000001.SZ"]["paper_status"] == "pending"
        assert results["000001.SZ"]["signal_state"] == "TRIGGERED"
        # open -> confirmed
        assert results["000002.SZ"]["review_bucket"] == "confirmed"
        assert results["000002.SZ"]["review_bucket_label"] == "已确认"
        # invalidated -> invalidated
        assert results["000003.SZ"]["review_bucket"] == "invalidated"
        assert results["000003.SZ"]["review_bucket_label"] == "已失效"

        # today_review_focus — all three are worth reviewing
        focus = result["today_review_focus"]
        assert focus["focus_count"] == 3
        focus_syms = [f["symbol"] for f in focus["focus_items"]]
        assert set(focus_syms) == {"000001.SZ", "000002.SZ", "000003.SZ"}
        # risk-first ordering: invalidated first
        assert focus_syms[0] == "000003.SZ"

    def test_not_triggered_and_data_missing_buckets(self, tmp_path):
        from api.services.tradeflow_service import get_review

        trade_date = "2026-06-02"
        candidates = [
            _candidate("000004.SZ", "测试 not_triggered"),
            _candidate("000005.SZ", "测试 data_missing"),
        ]
        db_path = _build_plan_db(tmp_path, trade_date, candidates)
        # Only one observe signal — 000004 got a check (WAITING), 000005 nothing
        _insert_signal(
            db_path, "000004.SZ", trade_date,
            signal_type="observe_check", observe_state="WAITING", current_price=9.8,
        )

        result = get_review(trade_date, tf_db_path=db_path)
        results = {r["symbol"]: r for r in result["results"]}
        assert results["000004.SZ"]["review_bucket"] == "not_triggered"
        assert results["000004.SZ"]["review_bucket_label"] == "未触发"
        assert results["000004.SZ"]["has_signal_for_date"] is True
        assert results["000005.SZ"]["review_bucket"] == "data_missing"
        assert results["000005.SZ"]["review_bucket_label"] == "缺数据"
        assert results["000005.SZ"]["has_signal_for_date"] is False

        # Neither belongs in today_review_focus
        focus = result["today_review_focus"]
        assert focus["focus_count"] == 0
        assert focus["bucket_counts"]["not_triggered"] == 1
        assert focus["bucket_counts"]["data_missing"] == 1

    def test_review_not_blank_or_score_only(self, tmp_path):
        """Acceptance: Review output no longer just candidate scores — it
        explains trigger / confirm / invalidation status."""
        from api.services.tradeflow_service import get_review

        trade_date = "2026-06-02"
        candidates = [_candidate("000001.SZ", "测试", observe_state="TRIGGERED")]
        db_path = _build_plan_db(tmp_path, trade_date, candidates)
        _insert_paper_trade(db_path, "000001.SZ", trade_date, status="pending", observe_state="TRIGGERED")
        _insert_signal(
            db_path, "000001.SZ", trade_date,
            signal_type="observe_triggered", observe_state="TRIGGERED",
            trigger_reason="放量突破",
        )

        result = get_review(trade_date, tf_db_path=db_path)
        r = result["results"][0]
        # The new attribution fields must be populated, not blank
        assert r["review_bucket"] != ""
        assert r["review_bucket_label"] != ""
        assert r["review_bucket_reason"] != ""
        assert r["paper_status"] == "pending"
        assert r["signal_trigger_reason"] == "放量突破"

    def test_get_review_no_forbidden_words(self, tmp_path):
        from api.services.tradeflow_service import get_review

        trade_date = "2026-06-02"
        candidates = [
            _candidate("000001.SZ", "A", observe_state="TRIGGERED"),
            _candidate("000002.SZ", "B", observe_state="INVALIDATED"),
            _candidate("000003.SZ", "C"),
        ]
        db_path = _build_plan_db(tmp_path, trade_date, candidates)
        _insert_paper_trade(db_path, "000001.SZ", trade_date, status="pending", observe_state="TRIGGERED")
        _insert_paper_trade(db_path, "000002.SZ", trade_date, status="invalidated", observe_state="INVALIDATED")
        _insert_signal(db_path, "000001.SZ", trade_date, signal_type="observe_triggered", observe_state="TRIGGERED")
        _insert_signal(db_path, "000002.SZ", trade_date, signal_type="observe_invalidated", observe_state="INVALIDATED")
        _insert_signal(db_path, "000003.SZ", trade_date, signal_type="observe_check", observe_state="WAITING")

        result = get_review(trade_date, tf_db_path=db_path)
        for r in result["results"]:
            for field in ("review_bucket_label", "review_bucket_reason", "reason"):
                text = r.get(field, "")
                for word in FORBIDDEN_WORDS:
                    assert word not in text
        for f in result["today_review_focus"]["focus_items"]:
            for word in FORBIDDEN_WORDS:
                assert word not in f.get("review_bucket_reason", "")

    def test_signal_from_other_date_is_ignored(self, tmp_path):
        """Observe signals from a different trade_date must not leak in."""
        from api.services.tradeflow_service import get_review

        trade_date = "2026-06-02"
        candidates = [_candidate("000001.SZ", "A")]
        db_path = _build_plan_db(tmp_path, trade_date, candidates)
        # Signal from a DIFFERENT date
        _insert_signal(
            db_path, "000001.SZ", "2026-06-01",
            signal_type="observe_triggered", observe_state="TRIGGERED",
        )

        result = get_review(trade_date, tf_db_path=db_path)
        r = result["results"][0]
        # No signal for 2026-06-02 → data_missing (observe never ran today)
        assert r["review_bucket"] == "data_missing"
        assert r["has_signal_for_date"] is False


# ── generate_review carries observe+paper attribution ──


class TestGenerateReviewAttribution:
    def test_generate_review_includes_today_focus(self, tmp_path, monkeypatch):
        from api.services.tradeflow_service import generate_review
        import tradingagents.tradeflow.post_market_review as pmr

        trade_date = "2026-06-02"
        db_path = str(tmp_path / "tf_review_005_gen.db")
        init_db(db_path)
        # generate_review reads the tradeflow_candidates table
        _save_candidate_row(db_path, "000001.SZ", "测试 pending", trade_date, observe_state="TRIGGERED")
        _save_candidate_row(db_path, "000002.SZ", "测试 invalidated", trade_date, observe_state="INVALIDATED")
        _insert_paper_trade(db_path, "000001.SZ", trade_date, status="pending", observe_state="TRIGGERED")
        _insert_paper_trade(db_path, "000002.SZ", trade_date, status="invalidated", observe_state="INVALIDATED")
        _insert_signal(db_path, "000001.SZ", trade_date, signal_type="observe_triggered", observe_state="TRIGGERED")
        _insert_signal(db_path, "000002.SZ", trade_date, signal_type="observe_invalidated", observe_state="INVALIDATED")

        # Avoid writing the fixture report into the real docs/tradeflow_reviews/ dir
        monkeypatch.setattr(pmr, "save_review_report", lambda *a, **k: "")

        result = generate_review(trade_date, tf_db_path=db_path)
        assert result["status"] == "ok"
        review = result["review"]
        assert "today_review_focus" in review
        focus = review["today_review_focus"]
        assert focus["focus_count"] == 2

        # next_day_feedback entries carry the bucket fields
        fb_map = {f["symbol"]: f for f in review["next_day_feedback"]}
        assert fb_map["000001.SZ"]["review_bucket"] == "triggered_pending"
        assert fb_map["000001.SZ"]["paper_status"] == "pending"
        assert fb_map["000002.SZ"]["review_bucket"] == "invalidated"


# ── Fixture document fragment generation ──


class TestFixtureDocumentFragment:
    def test_generate_replayable_fixture_doc(self, tmp_path):
        """Produce a fixture document fragment covering pending/open/invalidated
        three categories — replayable and free of strong buy/sell words."""
        perfs = [
            _make_perf("000001.SZ", observe_state="TRIGGERED", paper_status="pending"),
            _make_perf("000002.SZ", observe_state="TRIGGERED", paper_status="open"),
            _make_perf("000003.SZ", observe_state="INVALIDATED", paper_status="invalidated"),
            _make_perf("000004.SZ", observe_state="WAITING", has_signal_for_date=True),
        ]
        summary = run_post_market_review(
            perfs, candidate_date="2026-06-02", review_date="2026-06-02"
        )
        md = render_review_markdown(summary)

        # All three required fixture categories must be represented
        assert "触发待确认" in md
        assert "已确认" in md
        assert "已失效" in md
        # Plus the supporting categories
        assert "未触发" in md

        # The "今天实际值得复盘的票" section must list the three worth-review tickers
        assert "今天实际值得复盘的票" in md
        for sym in ("000001.SZ", "000002.SZ", "000003.SZ"):
            assert sym in md

        # Forbidden-word scan over the whole fragment
        for word in FORBIDDEN_WORDS:
            assert word not in md

        # Replayable: write to a file and re-read
        out_path = tmp_path / "tf_review_005_fixture.md"
        out_path.write_text(md, encoding="utf-8")
        reread = out_path.read_text(encoding="utf-8")
        assert "今天实际值得复盘的票" in reread
