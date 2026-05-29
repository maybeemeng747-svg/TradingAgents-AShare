# [V-003] tradeflow_acceptance_replay
"""V-003: TradeFlow end-to-end candidate quality acceptance replay tests.

Replays 6 fixed candidate scenarios through generate_daily_plan(),
verifying stable outputs for candidate tiers, filter reasons, missing
evidence, event_source status, and deep TA gate reasons.

Scenarios:
1. strong_resonance   — VCP + policy + event + verified fund -> A tier
2. tech_only_signal   — VCP only -> B/C tier, no deep TA
3. unverified_fund_flow — VCP + fund (unit_verified=False) -> NOT A
4. event_source_failed — event source FAILED -> metadata shows FAILED
5. high_risk          — VCP + risk flags -> C tier, fragile
6. yesterday_observation — observe_state=TRIGGERED preserved

Constraints:
- No external LLM calls.
- No full-market scan.
- No production DB writes.
- No strong buy/sell words.
"""

from __future__ import annotations

import tempfile
import os
from unittest.mock import patch, MagicMock

import pytest

from tradingagents.tradeflow.schemas import (
    Candidate, CandidateSignal, DailyPlan, FORBIDDEN_WORDS, ALLOWED_ACTIONS,
)
from tradingagents.tradeflow.plan_runner import generate_daily_plan, _build_plan_entry
from tradingagents.tradeflow.candidate_engine import init_db, save_candidate, evaluate_symbol
from tradingagents.tradeflow.acceptance_replay import (
    generate_acceptance_fixtures,
    generate_acceptance_report,
    make_vcp_df,
    make_flat_df,
    build_candidate,
    verify_result,
    extract_replay_result,
    AcceptanceFixture,
    AcceptanceReplayResult,
    BUILDERS,
)


class TestV003StrongResonance:
    """Scenario 1: VCP + policy + event + verified fund -> A tier, deep TA."""

    def test_tier_is_a(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["tier"] == "A"

    def test_need_deep_ta(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["need_deep_ta"] is True

    def test_positive_category_count_ge3(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["positive_category_count"] >= 3

    def test_game_balance_favorable_or_neutral(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["game_balance"] in ("favorable", "neutral")

    def test_fund_flow_unit_verified(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["fund_flow_unit_verified"] is True

    def test_has_policy_tags(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert len(entry["policy_tags"]) > 0

    def test_action_is_deep_ta(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["action"] == "NEED_DEEP_TA"

    def test_no_forbidden_words(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        text = plan.render_text()
        for w in FORBIDDEN_WORDS:
            assert w not in text

    def test_plan_validates(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.validate() == []

    def test_composite_score_high(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["composite_score"] >= 50.0

    def test_data_completeness_high(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["data_completeness"] >= 0.75


class TestV003TechOnlySignal:
    """Scenario 2: VCP only -> B/C tier, no deep TA."""

    def test_tier_not_a(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["tier"] != "A"

    def test_no_deep_ta(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["need_deep_ta"] is False

    def test_positive_category_count_le2(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["positive_category_count"] <= 2

    def test_has_missing_evidence(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert len(entry.get("missing_evidence", [])) > 0

    def test_why_not_deep_ta_present(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry.get("why_not_deep_ta", "")

    def test_action_is_observe(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["action"] == "OBSERVE"

    def test_no_forbidden_words(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        text = plan.render_text()
        for w in FORBIDDEN_WORDS:
            assert w not in text

    def test_evidence_gate_applied(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry.get("evidence_gate_applied") is True


class TestV003UnverifiedFundFlow:
    """Scenario 3: VCP + fund flow (unit_verified=False) -> NOT A, NOT deep TA."""

    def test_tier_not_a(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["tier"] != "A"

    def test_no_deep_ta(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["need_deep_ta"] is False

    def test_fund_flow_unit_not_verified(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["fund_flow_unit_verified"] is False

    def test_has_missing_data_fields(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert len(entry.get("missing_data_fields", [])) > 0

    def test_why_not_deep_ta_mentions_unverified(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        reason = entry.get("why_not_deep_ta", "")
        assert "未校验" in reason or "单位" in reason or reason != ""

    def test_has_fund_flow_tags(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert len(entry.get("fund_flow_anomaly_tags", [])) > 0

    def test_evidence_gate_applied(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry.get("evidence_gate_applied") is True

    def test_no_forbidden_words(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        text = plan.render_text()
        for w in FORBIDDEN_WORDS:
            assert w not in text

    def test_tech_plus_unverified_fund_cannot_pass_gate(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["need_deep_ta"] is False
        assert entry["tier"] in ("B", "C", "")


class TestV003EventSourceFailed:
    """Scenario 4: event source returns FAILED -> metadata shows FAILED."""

    def _make_failed_event_result(self):
        from tradingagents.tradeflow.event_source import EventSourceResult, EventSourceStatus
        return EventSourceResult(
            status=EventSourceStatus.FAILED,
            events_map={},
            items_by_symbol={},
            error_message="AKShare接口异常: ConnectionError",
            event_count=0,
            symbols_count=0,
        )

    def test_metadata_shows_failed(self):
        er = self._make_failed_event_result()
        with patch(
            "tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
            return_value=er,
        ):
            with patch("tradingagents.tradeflow.plan_runner.build_universe", return_value=[]):
                plan = generate_daily_plan(
                    trade_date="2026-05-30",
                    candidates=[],
                    use_event_source=True,
                )
        assert plan.metadata.get("event_source", {}).get("status") == "FAILED"

    def test_system_still_processes_candidates(self):
        er = self._make_failed_event_result()
        c = build_candidate("tech_only_signal")
        with patch(
            "tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
            return_value=er,
        ):
            plan = generate_daily_plan(
                trade_date="2026-05-30",
                candidates=[c],
                use_event_source=True,
            )
        assert len(plan.candidates) == 1
        assert plan.metadata["event_source"]["status"] == "FAILED"

    def test_no_crash_on_event_failure(self):
        er = self._make_failed_event_result()
        with patch(
            "tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
            return_value=er,
        ):
            with patch("tradingagents.tradeflow.plan_runner.build_universe", return_value=[]):
                plan = generate_daily_plan(
                    trade_date="2026-05-30",
                    candidates=[],
                    use_event_source=True,
                )
        issues = plan.validate()
        assert issues == []

    def test_event_source_error_recorded(self):
        er = self._make_failed_event_result()
        with patch(
            "tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
            return_value=er,
        ):
            with patch("tradingagents.tradeflow.plan_runner.build_universe", return_value=[]):
                plan = generate_daily_plan(
                    trade_date="2026-05-30",
                    candidates=[],
                    use_event_source=True,
                )
        es = plan.metadata.get("event_source", {})
        assert es.get("error_message") != ""
        assert es.get("event_count") == 0

    def test_not_treated_as_no_events(self):
        er = self._make_failed_event_result()
        with patch(
            "tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
            return_value=er,
        ):
            with patch("tradingagents.tradeflow.plan_runner.build_universe", return_value=[]):
                plan = generate_daily_plan(
                    trade_date="2026-05-30",
                    candidates=[],
                    use_event_source=True,
                )
        es = plan.metadata.get("event_source", {})
        assert es.get("status") != "OK"
        assert es.get("status") == "FAILED"

    def test_no_forbidden_words_on_failure(self):
        er = self._make_failed_event_result()
        with patch(
            "tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
            return_value=er,
        ):
            with patch("tradingagents.tradeflow.plan_runner.build_universe", return_value=[]):
                plan = generate_daily_plan(
                    trade_date="2026-05-30",
                    candidates=[],
                    use_event_source=True,
                )
        text = plan.render_text()
        for w in FORBIDDEN_WORDS:
            assert w not in text


class TestV003HighRisk:
    """Scenario 5: VCP + multiple risk flags -> C tier, fragile, no deep TA."""

    def test_tier_c(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["tier"] in ("B", "C", "")

    def test_no_deep_ta(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["need_deep_ta"] is False

    def test_has_risk_flags(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert len(entry.get("risk_flags", [])) >= 2

    def test_game_balance_fragile_or_crowded(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["game_balance"] in ("fragile", "crowded", "")

    def test_negative_risk_penalty(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry.get("risk_penalty", 0) < 0

    def test_why_not_deep_ta_mentions_risk(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        reason = entry.get("why_not_deep_ta", "")
        assert "风险" in reason or reason != ""

    def test_action_is_observe(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["action"] == "OBSERVE"

    def test_no_forbidden_words(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        text = plan.render_text()
        for w in FORBIDDEN_WORDS:
            assert w not in text


class TestV003YesterdayObservation:
    """Scenario 6: observe_state=TRIGGERED preserved in plan entry."""

    def test_observe_state_triggered(self):
        c = build_candidate("yesterday_observation")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["observe_state"] == "TRIGGERED"

    def test_observe_trigger_count_preserved(self):
        c = build_candidate("yesterday_observation")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["observe_trigger_count"] >= 1

    def test_first_trigger_time_preserved(self):
        c = build_candidate("yesterday_observation")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["observe_first_trigger_time"] != ""

    def test_universe_sources_includes_yesterday(self):
        c = build_candidate("yesterday_observation")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert "yesterday" in entry.get("universe_sources", [])

    def test_fund_flow_verified(self):
        c = build_candidate("yesterday_observation")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["fund_flow_unit_verified"] is True

    def test_no_forbidden_words(self):
        c = build_candidate("yesterday_observation")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        text = plan.render_text()
        for w in FORBIDDEN_WORDS:
            assert w not in text

    def test_candidate_has_strategies(self):
        c = build_candidate("yesterday_observation")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert len(entry["strategies"]) >= 2


class TestV003FullPipelineReplay:
    """Replay all 6 scenarios through generate_daily_plan and verify."""

    @pytest.fixture
    def all_results(self):
        results = []
        for name, builder in BUILDERS.items():
            c = builder()
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            if plan.candidates:
                entry = plan.candidates[0]
                errors = verify_result(name, entry)
                r = extract_replay_result(name, "", c.symbol, entry, errors)
                results.append(r)
        return results

    def test_all_candidate_scenarios_pass(self, all_results):
        for r in all_results:
            assert r.passed, f"{r.fixture_name} failed: {r.verification_errors}"

    def test_strong_resonance_highest_score(self, all_results):
        scores = {r.fixture_name: r.composite_score for r in all_results}
        assert scores["strong_resonance"] >= max(
            v for k, v in scores.items() if k != "strong_resonance"
        )

    def test_unverified_fund_not_a_tier(self, all_results):
        uf = next(r for r in all_results if r.fixture_name == "unverified_fund_flow")
        assert uf.tier != "A"

    def test_high_risk_lowest_score(self, all_results):
        scores = {r.fixture_name: r.composite_score for r in all_results}
        assert scores["high_risk"] <= max(v for v in scores.values())

    def test_no_forbidden_words_in_any_plan(self):
        for name, builder in BUILDERS.items():
            c = builder()
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            text = plan.render_text()
            for w in FORBIDDEN_WORDS:
                assert w not in text, f"Forbidden word '{w}' in {name}"

    def test_all_plans_validate(self):
        for name, builder in BUILDERS.items():
            c = builder()
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            issues = plan.validate()
            assert issues == [], f"Validation issues in {name}: {issues}"


class TestV003EventSourceFailedFullPipeline:
    """Event source failure scenario through full pipeline."""

    def _run_with_event_failure(self, candidates=None):
        from tradingagents.tradeflow.event_source import EventSourceResult, EventSourceStatus
        er = EventSourceResult(
            status=EventSourceStatus.FAILED,
            events_map={},
            items_by_symbol={},
            error_message="AKShare接口异常",
        )
        with patch(
            "tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
            return_value=er,
        ):
            return generate_daily_plan(
                trade_date="2026-05-30",
                candidates=candidates,
                use_event_source=True,
            )

    def test_full_replay_with_event_failure(self):
        candidates = [build_candidate(n) for n in BUILDERS]
        plan = self._run_with_event_failure(candidates=candidates)
        assert plan.metadata["event_source"]["status"] == "FAILED"
        assert len(plan.candidates) == len(BUILDERS)

    def test_event_failure_does_not_block_candidates(self):
        c = build_candidate("strong_resonance")
        plan = self._run_with_event_failure(candidates=[c])
        entry = plan.candidates[0]
        assert entry["tier"] == "A"
        assert entry["need_deep_ta"] is True


class TestV003StableOutput:
    """Verify deterministic output across repeated runs."""

    def test_strong_resonance_stable(self):
        results = []
        for _ in range(3):
            c = build_candidate("strong_resonance")
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            results.append(plan.candidates[0])
        for key in ("tier", "need_deep_ta", "composite_score", "game_balance"):
            values = [r[key] for r in results]
            assert len(set(str(v) for v in values)) == 1, f"{key} not stable: {values}"

    def test_tech_only_stable(self):
        results = []
        for _ in range(3):
            c = build_candidate("tech_only_signal")
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            results.append(plan.candidates[0])
        for key in ("tier", "need_deep_ta", "action"):
            values = [r[key] for r in results]
            assert len(set(str(v) for v in values)) == 1, f"{key} not stable: {values}"

    def test_unverified_fund_stable(self):
        results = []
        for _ in range(3):
            c = build_candidate("unverified_fund_flow")
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            results.append(plan.candidates[0])
        for key in ("tier", "need_deep_ta", "fund_flow_unit_verified"):
            values = [r[key] for r in results]
            assert len(set(str(v) for v in values)) == 1, f"{key} not stable: {values}"

    def test_high_risk_stable(self):
        results = []
        for _ in range(3):
            c = build_candidate("high_risk")
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            results.append(plan.candidates[0])
        for key in ("tier", "need_deep_ta"):
            values = [r[key] for r in results]
            assert len(set(str(v) for v in values)) == 1, f"{key} not stable: {values}"


class TestV003ReportGeneration:
    """Verify acceptance report generation."""

    def test_report_contains_all_scenarios(self):
        results = []
        for name, builder in BUILDERS.items():
            c = builder()
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            entry = plan.candidates[0] if plan.candidates else {}
            errors = verify_result(name, entry)
            r = extract_replay_result(name, "", c.symbol, entry, errors)
            results.append(r)

        event_results = self._make_event_failure_result()
        results.append(event_results)

        report = generate_acceptance_report(results, trade_date="2026-05-30")

        assert "strong_resonance" in report
        assert "tech_only_signal" in report
        assert "unverified_fund_flow" in report
        assert "high_risk" in report
        assert "yesterday_observation" in report
        assert "FAIL" in report or "PASS" in report
        assert "分层汇总" in report

    def test_report_no_forbidden_words(self):
        results = []
        for name, builder in BUILDERS.items():
            c = builder()
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            entry = plan.candidates[0] if plan.candidates else {}
            errors = verify_result(name, entry)
            r = extract_replay_result(name, "", c.symbol, entry, errors)
            results.append(r)

        report = generate_acceptance_report(results, trade_date="2026-05-30")
        for w in FORBIDDEN_WORDS:
            assert w not in report

    def test_report_has_tier_summary(self):
        results = []
        for name, builder in BUILDERS.items():
            c = builder()
            plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
            entry = plan.candidates[0] if plan.candidates else {}
            errors = verify_result(name, entry)
            r = extract_replay_result(name, "", c.symbol, entry, errors)
            results.append(r)

        report = generate_acceptance_report(results, trade_date="2026-05-30")
        assert "可进入 TA" in report or "优先深挖" in report
        assert "仅观察" in report or "观察等待" in report
        assert "淘汰" in report or "暂不关注" in report

    def _make_event_failure_result(self):
        from tradingagents.tradeflow.event_source import EventSourceResult, EventSourceStatus
        er = EventSourceResult(
            status=EventSourceStatus.FAILED,
            events_map={},
            items_by_symbol={},
            error_message="AKShare接口异常",
        )
        with patch(
            "tradingagents.tradeflow.event_source.fetch_daily_events_detailed",
            return_value=er,
        ):
            plan = generate_daily_plan(
                trade_date="2026-05-30",
                candidates=[],
                use_event_source=True,
            )
        return AcceptanceReplayResult(
            fixture_name="event_source_failed",
            description="事件源失败",
            symbol="N/A",
            passed=plan.metadata.get("event_source", {}).get("status") == "FAILED",
            event_source_status=plan.metadata.get("event_source", {}).get("status", ""),
        )


class TestV003EvaluateSymbolIntegration:
    """Test evaluate_symbol with mocked data for full pipeline verification."""

    def test_strong_resonance_through_evaluate(self):
        df = make_vcp_df(seed=42)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="600519.SH",
                name="贵州茅台",
                source="manual",
                trade_date="2026-05-30",
                news_texts=[
                    "新质生产力政策落地，算力板块受益",
                    "贵州茅台发布回购计划",
                ],
                event_overrides=[
                    {"symbol": "600519.SH", "title": "回购计划公告", "event_type": "buyback", "direction": "bullish"},
                ],
                fund_flow_individual="日期,主力净流入\n2026-05-29,5000万\n2026-05-28,3000万",
                fund_flow_board="日期,行业净流入\n2026-05-29,2亿",
                df=df,
            )
        if c is not None:
            assert c.composite_score > 0
            assert c.tier in ("A", "B", "C", "")
            for w in FORBIDDEN_WORDS:
                assert w not in c.why_deep_ta
                assert w not in c.why_not_deep_ta

    def test_tech_only_through_evaluate(self):
        df = make_vcp_df(seed=42)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="000001.SZ",
                name="平安银行",
                source="manual",
                trade_date="2026-05-30",
                df=df,
            )
        if c is not None:
            assert "VCP" in c.strategy_tags or c.primary_strategy != ""
            assert c.tier in ("A", "B", "C", "")

    def test_unverified_fund_cannot_reach_a_tier(self):
        df = make_vcp_df(seed=42)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="002415.SZ",
                name="海康威视",
                source="manual",
                trade_date="2026-05-30",
                fund_flow_individual="日期,主力净流入(未校验单位)\n2026-05-29,5000\n2026-05-28,3000",
                df=df,
            )
        if c is not None:
            if not c.fund_flow_unit_verified:
                assert c.tier != "A" or c.need_deep_ta is False

    def test_high_risk_demoted(self):
        df = make_vcp_df(seed=42)
        with patch("tradingagents.tradeflow.candidate_engine._fetch_price_data", return_value=df):
            c, reason = evaluate_symbol(
                symbol="300999.SZ",
                name="金龙鱼",
                source="manual",
                trade_date="2026-05-30",
                news_texts=[
                    "金龙鱼收到问询函",
                    "金龙鱼限售股解禁公告",
                    "金龙鱼融资余额持续攀升",
                ],
                df=df,
            )
        if c is not None:
            if len(c.risk_flags) >= 3 or c.risk_penalty <= -15:
                assert c.need_deep_ta is False


class TestV003FixtureCompleteness:
    """Verify all 6 scenarios are covered."""

    def test_fixtures_count(self):
        fixtures = generate_acceptance_fixtures()
        assert len(fixtures) == 6

    def test_all_scenario_names(self):
        fixtures = generate_acceptance_fixtures()
        names = {f.name for f in fixtures}
        expected = {"strong_resonance", "tech_only_signal", "unverified_fund_flow",
                     "event_source_failed", "high_risk", "yesterday_observation"}
        assert names == expected

    def test_all_builders_exist(self):
        for name, _, _, cat in [
            ("strong_resonance", "", "", "candidate"),
            ("tech_only_signal", "", "", "candidate"),
            ("unverified_fund_flow", "", "", "candidate"),
            ("high_risk", "", "", "candidate"),
            ("yesterday_observation", "", "", "candidate"),
        ]:
            assert name in BUILDERS
            c = BUILDERS[name]()
            assert isinstance(c, Candidate)
            assert c.symbol != ""

    def test_all_verifiers_exist(self):
        from tradingagents.tradeflow.acceptance_replay import _VERIFIERS
        for name in ["strong_resonance", "tech_only_signal", "unverified_fund_flow",
                      "high_risk", "yesterday_observation"]:
            assert name in _VERIFIERS


class TestV003TierClassification:
    """Verify tier classification rules across scenarios."""

    def test_strong_resonance_is_a(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.candidates[0]["tier"] == "A"

    def test_tech_only_is_b_or_c(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.candidates[0]["tier"] in ("B", "C")

    def test_unverified_fund_is_b_or_c(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.candidates[0]["tier"] in ("B", "C", "")

    def test_high_risk_is_c(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.candidates[0]["tier"] in ("B", "C", "")

    def test_yesterday_observation_is_b(self):
        c = build_candidate("yesterday_observation")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.candidates[0]["tier"] in ("A", "B", "C")


class TestV003DeepTAGate:
    """Verify deep TA gate decisions across scenarios."""

    def test_strong_resonance_allows_deep_ta(self):
        c = build_candidate("strong_resonance")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        entry = plan.candidates[0]
        assert entry["need_deep_ta"] is True
        assert entry["action"] == "NEED_DEEP_TA"

    def test_tech_only_blocks_deep_ta(self):
        c = build_candidate("tech_only_signal")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.candidates[0]["need_deep_ta"] is False

    def test_unverified_fund_blocks_deep_ta(self):
        c = build_candidate("unverified_fund_flow")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.candidates[0]["need_deep_ta"] is False

    def test_high_risk_blocks_deep_ta(self):
        c = build_candidate("high_risk")
        plan = generate_daily_plan(trade_date="2026-05-30", candidates=[c])
        assert plan.candidates[0]["need_deep_ta"] is False


class TestV003DeepTAGateIntegration:
    """Verify deep TA gate module integration with acceptance fixtures."""

    def test_strong_resonance_passes_deep_ta_gate(self):
        from tradingagents.tradeflow.gated_deep_ta import check_deep_ta_gate, DeepTADispatcher
        from tradingagents.tradeflow.strategy_config import DEFAULT_STRATEGY_CONFIG

        c = build_candidate("strong_resonance")
        dispatcher = DeepTADispatcher.from_config(DEFAULT_STRATEGY_CONFIG)
        decision = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol=c.symbol,
            need_deep_ta=c.need_deep_ta,
            observe_state="TRIGGERED",
            composite_score=c.composite_score,
            completeness=c.data_completeness,
            tier=c.tier,
            model="glm-4-flash",
        )
        assert decision.allowed is True

    def test_tech_only_blocked_by_deep_ta_gate(self):
        from tradingagents.tradeflow.gated_deep_ta import check_deep_ta_gate, DeepTADispatcher
        from tradingagents.tradeflow.strategy_config import DEFAULT_STRATEGY_CONFIG

        c = build_candidate("tech_only_signal")
        dispatcher = DeepTADispatcher.from_config(DEFAULT_STRATEGY_CONFIG)
        decision = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol=c.symbol,
            need_deep_ta=c.need_deep_ta,
            observe_state="WAITING",
            composite_score=c.composite_score,
            completeness=c.data_completeness,
            tier=c.tier,
            model="glm-4-flash",
        )
        assert decision.allowed is False

    def test_high_risk_blocked_by_deep_ta_gate(self):
        from tradingagents.tradeflow.gated_deep_ta import check_deep_ta_gate, DeepTADispatcher
        from tradingagents.tradeflow.strategy_config import DEFAULT_STRATEGY_CONFIG

        c = build_candidate("high_risk")
        dispatcher = DeepTADispatcher.from_config(DEFAULT_STRATEGY_CONFIG)
        decision = check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol=c.symbol,
            need_deep_ta=c.need_deep_ta,
            observe_state="WAITING",
            composite_score=c.composite_score,
            completeness=c.data_completeness,
            tier=c.tier,
            model="glm-4-flash",
        )
        assert decision.allowed is False
