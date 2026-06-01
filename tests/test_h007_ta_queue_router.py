# [H-007] mandate_ta_queue_router
"""Tests for H-007: 昊天候选到 TA 中线研究队列分流."""

from __future__ import annotations

import pytest

from tradingagents.tradeflow.mandate_ta_queue_router import (
    ResearchQueue,
    ResearchIntent,
    QueueRouteResult,
    QueueStatistics,
    RESEARCH_QUEUE_LABELS,
    RESEARCH_INTENT_LABELS,
    route_to_research_queue,
    compute_queue_statistics,
    render_queue_report,
)
from tradingagents.tradeflow.ambush_score import CandidateType


# ── Enum Tests ──


class TestResearchQueueEnum:
    def test_all_values(self):
        assert {q.value for q in ResearchQueue} == {
            "MIDLINE_POLICY", "TA_CONFIRM", "SHORT_TERM_TRADE", "WATCH_ONLY", "REJECTED"
        }

    def test_count(self):
        assert len(ResearchQueue) == 5

    def test_string_construction(self):
        assert ResearchQueue("MIDLINE_POLICY") == ResearchQueue.MIDLINE_POLICY

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            ResearchQueue("INVALID")


class TestResearchIntentEnum:
    def test_all_values(self):
        assert {i.value for i in ResearchIntent} == {
            "policy_validation", "trend_confirmation", "risk_review"
        }

    def test_count(self):
        assert len(ResearchIntent) == 3

    def test_string_construction(self):
        assert ResearchIntent("policy_validation") == ResearchIntent.POLICY_VALIDATION

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            ResearchIntent("invalid")


class TestLabels:
    def test_all_queues_have_labels(self):
        for q in ResearchQueue:
            assert q in RESEARCH_QUEUE_LABELS
            assert len(RESEARCH_QUEUE_LABELS[q]) > 0

    def test_all_intents_have_labels(self):
        for i in ResearchIntent:
            assert i in RESEARCH_INTENT_LABELS
            assert len(RESEARCH_INTENT_LABELS[i]) > 0


# ── QueueRouteResult Tests ──


class TestQueueRouteResult:
    def test_default_values(self):
        r = QueueRouteResult()
        assert r.research_queue == ResearchQueue.WATCH_ONLY.value
        assert r.research_intent == ResearchIntent.RISK_REVIEW.value
        assert r.route_reason == ""
        assert r.queue_priority == 4

    def test_custom_values(self):
        r = QueueRouteResult(
            research_queue=ResearchQueue.MIDLINE_POLICY.value,
            research_intent=ResearchIntent.POLICY_VALIDATION.value,
            route_reason="test",
            queue_priority=1,
        )
        assert r.research_queue == "MIDLINE_POLICY"
        assert r.research_intent == "policy_validation"
        assert r.route_reason == "test"
        assert r.queue_priority == 1

    def test_to_dict(self):
        r = QueueRouteResult(
            research_queue="MIDLINE_POLICY",
            research_intent="policy_validation",
            route_reason="reason",
            queue_priority=1,
        )
        d = r.to_dict()
        assert d["research_queue"] == "MIDLINE_POLICY"
        assert d["research_intent"] == "policy_validation"
        assert d["route_reason"] == "reason"
        assert d["queue_priority"] == 1


# ── QueueStatistics Tests ──


class TestQueueStatistics:
    def test_default_values(self):
        s = QueueStatistics()
        assert s.total == 0
        assert s.by_queue == {}
        assert s.by_intent == {}
        assert s.midline_policy_symbols == []
        assert s.ta_confirm_symbols == []
        assert s.short_term_symbols == []
        assert s.watch_only_symbols == []
        assert s.rejected_symbols == []

    def test_to_dict(self):
        s = QueueStatistics(
            total=3,
            by_queue={"MIDLINE_POLICY": 1, "TA_CONFIRM": 2},
            by_intent={"policy_validation": 1, "trend_confirmation": 2},
            midline_policy_symbols=["AAA"],
            ta_confirm_symbols=["BBB", "CCC"],
            short_term_symbols=[],
            watch_only_symbols=[],
            rejected_symbols=[],
        )
        d = s.to_dict()
        assert d["total"] == 3
        assert d["by_queue"]["MIDLINE_POLICY"] == 1
        assert d["midline_policy_symbols"] == ["AAA"]


# ── Core Routing Tests ──


class TestRouteToResearchQueue:
    def test_empty_candidate_type(self):
        result = route_to_research_queue(candidate_type="")
        assert result.research_queue == "WATCH_ONLY"
        assert result.research_intent == "risk_review"
        assert "无候选类型" in result.route_reason

    def test_policy_ambush_basic(self):
        result = route_to_research_queue(
            candidate_type="POLICY_AMBUSH",
            ambush_score=55.0,
            mandate_score_component=50.0,
        )
        assert result.research_queue == "MIDLINE_POLICY"
        assert result.research_intent == "policy_validation"
        assert result.queue_priority == 1

    def test_policy_ambush_with_beneficiary_path(self):
        result = route_to_research_queue(
            candidate_type="POLICY_AMBUSH",
            ambush_score=65.0,
            mandate_score_component=55.0,
            beneficiary_score_component=60.0,
            has_beneficiary_path=True,
        )
        assert result.research_queue == "MIDLINE_POLICY"
        assert "受益路径明确" in result.route_reason
        assert "mandate=55" in result.route_reason

    def test_policy_ambush_policy_only(self):
        result = route_to_research_queue(
            candidate_type="POLICY_AMBUSH",
            ambush_score=40.0,
            mandate_score_component=35.0,
            has_policy=True,
        )
        assert result.research_queue == "MIDLINE_POLICY"
        assert "待验证受益路径" in result.route_reason

    def test_policy_confirm_basic(self):
        result = route_to_research_queue(
            candidate_type="POLICY_CONFIRM",
            mandate_score_component=50.0,
        )
        assert result.research_queue == "TA_CONFIRM"
        assert result.research_intent == "trend_confirmation"
        assert result.queue_priority == 2

    def test_policy_confirm_with_beneficiary(self):
        result = route_to_research_queue(
            candidate_type="POLICY_CONFIRM",
            mandate_score_component=55.0,
            has_beneficiary_path=True,
        )
        assert result.research_queue == "TA_CONFIRM"
        assert "受益路径+技术确认" in result.route_reason

    def test_policy_confirm_without_beneficiary(self):
        result = route_to_research_queue(
            candidate_type="POLICY_CONFIRM",
            mandate_score_component=45.0,
            has_beneficiary_path=False,
        )
        assert result.research_queue == "TA_CONFIRM"
        assert "需趋势确认" in result.route_reason

    def test_tech_trade(self):
        result = route_to_research_queue(candidate_type="TECH_TRADE")
        assert result.research_queue == "SHORT_TERM_TRADE"
        assert result.research_intent == "trend_confirmation"
        assert result.queue_priority == 3
        assert "短线/做T" in result.route_reason

    def test_event_watch_low_score(self):
        result = route_to_research_queue(
            candidate_type="EVENT_WATCH",
            ambush_score=10.0,
        )
        assert result.research_queue == "WATCH_ONLY"
        assert result.research_intent == "risk_review"
        assert "信号不足" in result.route_reason

    def test_event_watch_high_score_with_policy(self):
        result = route_to_research_queue(
            candidate_type="EVENT_WATCH",
            ambush_score=35.0,
            mandate_score_component=25.0,
            has_policy=True,
        )
        assert result.research_queue == "WATCH_ONLY"
        assert result.research_intent == "policy_validation"
        assert "有政策信号" in result.route_reason

    def test_pseudo_policy_basic(self):
        result = route_to_research_queue(candidate_type="PSEUDO_POLICY")
        assert result.research_queue == "WATCH_ONLY"
        assert result.research_intent == "risk_review"
        assert "伪政策" in result.route_reason or "蹭概念" in result.route_reason

    def test_pseudo_policy_high_risk_rejected(self):
        result = route_to_research_queue(
            candidate_type="PSEUDO_POLICY",
            risk_flags=["INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"],
        )
        assert result.research_queue == "REJECTED"
        assert "高风险" in result.route_reason

    def test_pseudo_policy_fragile_rejected(self):
        result = route_to_research_queue(
            candidate_type="PSEUDO_POLICY",
            game_balance="fragile",
        )
        assert result.research_queue == "REJECTED"
        assert "fragile" in result.route_reason

    def test_overheated_avoid(self):
        result = route_to_research_queue(
            candidate_type="OVERHEATED_AVOID",
            overheat_penalty=45.0,
        )
        assert result.research_queue == "REJECTED"
        assert result.research_intent == "risk_review"
        assert result.queue_priority == 5
        assert "过热规避" in result.route_reason

    def test_overheated_avoid_with_risk_flags(self):
        result = route_to_research_queue(
            candidate_type="OVERHEATED_AVOID",
            overheat_penalty=50.0,
            risk_flags=["LHB_OVERHEAT_RISK", "MARGIN_CROWDING_RISK"],
        )
        assert result.research_queue == "REJECTED"
        assert "LHB_OVERHEAT_RISK" in result.route_reason

    def test_unknown_type(self):
        result = route_to_research_queue(candidate_type="UNKNOWN_TYPE")
        assert result.research_queue == "WATCH_ONLY"
        assert "未知类型" in result.route_reason


# ── Acceptance Tests ──


class TestAcceptanceH007:
    def test_policy_ambush_goes_to_midline(self):
        result = route_to_research_queue(
            candidate_type="POLICY_AMBUSH",
            ambush_score=60.0,
            mandate_score_component=50.0,
            beneficiary_score_component=55.0,
            has_beneficiary_path=True,
        )
        assert result.research_queue == "MIDLINE_POLICY"
        assert result.research_intent == "policy_validation"

    def test_policy_confirm_goes_to_ta_confirm(self):
        result = route_to_research_queue(
            candidate_type="POLICY_CONFIRM",
            mandate_score_component=55.0,
        )
        assert result.research_queue == "TA_CONFIRM"
        assert result.research_intent == "trend_confirmation"

    def test_tech_trade_goes_to_short_term(self):
        result = route_to_research_queue(candidate_type="TECH_TRADE")
        assert result.research_queue == "SHORT_TERM_TRADE"

    def test_overheated_goes_to_rejected(self):
        result = route_to_research_queue(
            candidate_type="OVERHEATED_AVOID",
            overheat_penalty=40.0,
        )
        assert result.research_queue == "REJECTED"

    def test_pseudo_policy_high_risk_rejected(self):
        result = route_to_research_queue(
            candidate_type="PSEUDO_POLICY",
            risk_flags=["INQUIRY_RISK"],
        )
        assert result.research_queue == "REJECTED"

    def test_no_strong_trade_words(self):
        forbidden = {"买入", "卖出", "清仓", "满仓", "梭哈", "重仓"}
        for ct in ["POLICY_AMBUSH", "POLICY_CONFIRM", "TECH_TRADE",
                    "EVENT_WATCH", "PSEUDO_POLICY", "OVERHEATED_AVOID"]:
            result = route_to_research_queue(candidate_type=ct)
            for word in forbidden:
                assert word not in result.route_reason, (
                    f"Forbidden word '{word}' found in route_reason for {ct}: {result.route_reason}"
                )

    def test_routing_stable(self):
        for _ in range(5):
            r1 = route_to_research_queue(
                candidate_type="POLICY_AMBUSH",
                ambush_score=50.0,
                mandate_score_component=45.0,
            )
            assert r1.research_queue == "MIDLINE_POLICY"

    def test_position_context_not_overridden(self):
        result = route_to_research_queue(
            candidate_type="POLICY_CONFIRM",
            mandate_score_component=50.0,
        )
        assert "position" not in result.route_reason.lower()
        assert "持仓" not in result.route_reason

    def test_analysis_intent_not_conflicting(self):
        result = route_to_research_queue(
            candidate_type="POLICY_AMBUSH",
            ambush_score=55.0,
            mandate_score_component=45.0,
        )
        assert result.research_intent == "policy_validation"
        assert result.research_intent not in ("buy", "sell", "hold")


# ── Queue Statistics Tests ──


class TestComputeQueueStatistics:
    def test_empty(self):
        stats = compute_queue_statistics([])
        assert stats.total == 0
        assert stats.by_queue == {}
        assert stats.by_intent == {}

    def test_single_midline(self):
        routes = [{"symbol": "AAA", "research_queue": "MIDLINE_POLICY", "research_intent": "policy_validation"}]
        stats = compute_queue_statistics(routes)
        assert stats.total == 1
        assert stats.by_queue["MIDLINE_POLICY"] == 1
        assert stats.midline_policy_symbols == ["AAA"]

    def test_mixed_queues(self):
        routes = [
            {"symbol": "AAA", "research_queue": "MIDLINE_POLICY", "research_intent": "policy_validation"},
            {"symbol": "BBB", "research_queue": "TA_CONFIRM", "research_intent": "trend_confirmation"},
            {"symbol": "CCC", "research_queue": "SHORT_TERM_TRADE", "research_intent": "trend_confirmation"},
            {"symbol": "DDD", "research_queue": "WATCH_ONLY", "research_intent": "risk_review"},
            {"symbol": "EEE", "research_queue": "REJECTED", "research_intent": "risk_review"},
        ]
        stats = compute_queue_statistics(routes)
        assert stats.total == 5
        assert stats.by_queue["MIDLINE_POLICY"] == 1
        assert stats.by_queue["TA_CONFIRM"] == 1
        assert stats.by_queue["SHORT_TERM_TRADE"] == 1
        assert stats.by_queue["WATCH_ONLY"] == 1
        assert stats.by_queue["REJECTED"] == 1
        assert len(stats.by_intent) == 3

    def test_by_intent_counts(self):
        routes = [
            {"symbol": "A", "research_queue": "MIDLINE_POLICY", "research_intent": "policy_validation"},
            {"symbol": "B", "research_queue": "TA_CONFIRM", "research_intent": "trend_confirmation"},
            {"symbol": "C", "research_queue": "SHORT_TERM_TRADE", "research_intent": "trend_confirmation"},
        ]
        stats = compute_queue_statistics(routes)
        assert stats.by_intent["policy_validation"] == 1
        assert stats.by_intent["trend_confirmation"] == 2

    def test_missing_fields_default(self):
        routes = [{"symbol": "X"}]
        stats = compute_queue_statistics(routes)
        assert stats.total == 1
        assert stats.by_queue.get("WATCH_ONLY", 0) == 1

    def test_no_symbol_excluded_from_lists(self):
        routes = [{"research_queue": "MIDLINE_POLICY", "research_intent": "policy_validation"}]
        stats = compute_queue_statistics(routes)
        assert stats.midline_policy_symbols == []


# ── Render Report Tests ──


class TestRenderQueueReport:
    def test_basic_render(self):
        stats = QueueStatistics(
            total=5,
            by_queue={"MIDLINE_POLICY": 2, "TA_CONFIRM": 1, "SHORT_TERM_TRADE": 1, "WATCH_ONLY": 1},
            by_intent={"policy_validation": 2, "trend_confirmation": 2, "risk_review": 1},
            midline_policy_symbols=["AAA", "BBB"],
            ta_confirm_symbols=["CCC"],
            short_term_symbols=["DDD"],
            watch_only_symbols=["EEE"],
            rejected_symbols=[],
        )
        text = render_queue_report(stats)
        assert "总计: 5" in text
        assert "中线政策研究: 2" in text
        assert "TA深度确认: 1" in text
        assert "AAA" in text

    def test_empty_report(self):
        stats = QueueStatistics()
        text = render_queue_report(stats)
        assert "总计: 0" in text

    def test_no_strong_trade_words_in_report(self):
        forbidden = {"买入", "卖出", "清仓", "满仓", "梭哈", "重仓"}
        stats = QueueStatistics(
            total=1,
            by_queue={"REJECTED": 1},
            by_intent={"risk_review": 1},
            rejected_symbols=["AAA"],
        )
        text = render_queue_report(stats)
        for word in forbidden:
            assert word not in text

    def test_all_queues_shown(self):
        stats = QueueStatistics(total=5, by_queue={
            "MIDLINE_POLICY": 1, "TA_CONFIRM": 1,
            "SHORT_TERM_TRADE": 1, "WATCH_ONLY": 1, "REJECTED": 1,
        })
        text = render_queue_report(stats)
        assert "中线政策研究" in text
        assert "TA深度确认" in text
        assert "短线交易" in text
        assert "仅观察" in text
        assert "已拒绝" in text

    def test_all_intents_shown(self):
        stats = QueueStatistics(by_intent={
            "policy_validation": 2, "trend_confirmation": 3, "risk_review": 1,
        })
        text = render_queue_report(stats)
        assert "政策验证" in text
        assert "趋势确认" in text
        assert "风控审查" in text


# ── Integration Tests ──


class TestH007Integration:
    def test_candidate_type_enum_aligned(self):
        for ct in CandidateType:
            result = route_to_research_queue(candidate_type=ct.value)
            assert result.research_queue in {q.value for q in ResearchQueue}

    def test_full_routing_pipeline(self):
        types_and_expected = [
            ("POLICY_AMBUSH", "MIDLINE_POLICY", "policy_validation"),
            ("POLICY_CONFIRM", "TA_CONFIRM", "trend_confirmation"),
            ("TECH_TRADE", "SHORT_TERM_TRADE", "trend_confirmation"),
            ("OVERHEATED_AVOID", "REJECTED", "risk_review"),
        ]
        for ct, queue, intent in types_and_expected:
            result = route_to_research_queue(
                candidate_type=ct,
                ambush_score=50.0,
                mandate_score_component=45.0,
                overheat_penalty=30.0,
            )
            assert result.research_queue == queue, f"{ct} → expected {queue}, got {result.research_queue}"
            assert result.research_intent == intent, f"{ct} → expected {intent}, got {result.research_intent}"

    def test_statistics_from_routing_results(self):
        routes = []
        for ct in ["POLICY_AMBUSH", "POLICY_CONFIRM", "TECH_TRADE", "OVERHEATED_AVOID", "PSEUDO_POLICY"]:
            result = route_to_research_queue(candidate_type=ct, ambush_score=40.0, mandate_score_component=40.0)
            routes.append({
                "symbol": f"SYM_{ct[:3]}",
                "research_queue": result.research_queue,
                "research_intent": result.research_intent,
            })
        stats = compute_queue_statistics(routes)
        assert stats.total == 5
        assert len(stats.by_queue) >= 3

    def test_queue_route_result_roundtrip(self):
        r = QueueRouteResult(
            research_queue="MIDLINE_POLICY",
            research_intent="policy_validation",
            route_reason="test reason",
            queue_priority=1,
        )
        d = r.to_dict()
        r2 = QueueRouteResult(**d)
        assert r2.research_queue == r.research_queue
        assert r2.research_intent == r.research_intent
        assert r2.route_reason == r.route_reason
        assert r2.queue_priority == r.queue_priority

    def test_schema_fields_exist(self):
        from tradingagents.tradeflow.schemas import Candidate
        c = Candidate(symbol="TEST")
        assert hasattr(c, "research_queue")
        assert hasattr(c, "research_intent")
        assert hasattr(c, "research_route_reason")
        assert c.research_queue == ""
        assert c.research_intent == ""
        assert c.research_route_reason == ""

    def test_schema_to_db_row_includes_fields(self):
        from tradingagents.tradeflow.schemas import Candidate
        c = Candidate(symbol="TEST", research_queue="MIDLINE_POLICY", research_intent="policy_validation")
        row = c.to_db_row()
        assert "research_queue" in row
        assert "research_intent" in row
        assert "research_route_reason" in row
        assert row["research_queue"] == "MIDLINE_POLICY"

    def test_schema_from_db_row_reads_fields(self):
        from tradingagents.tradeflow.schemas import Candidate
        row = {
            "symbol": "TEST", "name": "", "source": "manual",
            "strategy_tags_json": "[]", "primary_strategy": "", "score": 0.0,
            "status": "active", "trigger_price": None, "support_price": None,
            "invalid_price": None, "need_deep_ta": 0, "evidence_json": "{}",
            "risk_flags_json": "[]", "trade_date": "2026-06-02",
            "created_at": "", "updated_at": "",
            "research_queue": "TA_CONFIRM",
            "research_intent": "trend_confirmation",
            "research_route_reason": "test",
        }
        c = Candidate.from_db_row(row)
        assert c.research_queue == "TA_CONFIRM"
        assert c.research_intent == "trend_confirmation"
        assert c.research_route_reason == "test"


# ── Edge Cases ──


class TestEdgeCases:
    def test_none_risk_flags(self):
        result = route_to_research_queue(
            candidate_type="PSEUDO_POLICY",
            risk_flags=None,
        )
        assert result.research_queue in ("WATCH_ONLY", "REJECTED")

    def test_empty_string_type(self):
        result = route_to_research_queue(candidate_type="")
        assert result.research_queue == "WATCH_ONLY"

    def test_zero_scores(self):
        result = route_to_research_queue(
            candidate_type="POLICY_AMBUSH",
            ambush_score=0.0,
            mandate_score_component=0.0,
            beneficiary_score_component=0.0,
        )
        assert result.research_queue == "MIDLINE_POLICY"

    def test_very_high_scores(self):
        result = route_to_research_queue(
            candidate_type="POLICY_AMBUSH",
            ambush_score=100.0,
            mandate_score_component=100.0,
            beneficiary_score_component=100.0,
            has_beneficiary_path=True,
        )
        assert result.research_queue == "MIDLINE_POLICY"

    def test_many_risk_flags(self):
        result = route_to_research_queue(
            candidate_type="OVERHEATED_AVOID",
            overheat_penalty=80.0,
            risk_flags=["LHB_OVERHEAT_RISK", "MARGIN_CROWDING_RISK", "LOCKUP_RISK",
                         "INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"],
        )
        assert result.research_queue == "REJECTED"

    def test_queue_priority_ordering(self):
        r1 = route_to_research_queue(candidate_type="POLICY_AMBUSH", ambush_score=50.0, mandate_score_component=50.0)
        r2 = route_to_research_queue(candidate_type="POLICY_CONFIRM", mandate_score_component=50.0)
        r3 = route_to_research_queue(candidate_type="TECH_TRADE")
        r4 = route_to_research_queue(candidate_type="OVERHEATED_AVOID", overheat_penalty=40.0)
        assert r1.queue_priority < r2.queue_priority < r3.queue_priority < r4.queue_priority
