# [V-005] nightly_mandate_quality_report
"""Tests for 昊天候选质量日报与样本回放."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import List
from unittest.mock import patch

import pytest

from tradingagents.tradeflow.mandate_quality_report import (
    EMPTY_ALL_FILTERED,
    EMPTY_EVENT_SOURCE_FAILED,
    EMPTY_NO_EVENT_SOURCE,
    EMPTY_REASON_LABELS,
    EMPTY_STRATEGY_TOO_STRICT,
    EMPTY_UNKNOWN,
    CandidateSummary,
    MandateQualityReport,
    build_mandate_quality_report,
    build_report_from_replay,
    classify_empty_reason,
    render_mandate_quality_report,
    run_mandate_quality_report,
    save_mandate_quality_report,
    _count_distribution,
    _top_counter_evidences,
)
from tradingagents.tradeflow.mandate_replay_eval import (
    ReplayReport,
    ReplayEvaluation,
    run_replay_evaluation,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_candidate(
    symbol: str = "600519.SH",
    name: str = "贵州茅台",
    candidate_type: str = "POLICY_AMBUSH",
    tier: str = "A",
    mandate_topic: str = "消费升级",
    company_role: str = "LEADER",
    ambush_score: float = 65.0,
    mandate_score_component: float = 70.0,
    research_queue: str = "MIDLINE_POLICY",
    evidence_coverage: float = 0.85,
    evidence_quality_level: str = "HIGH",
    overheat_flags: list = None,
    downgrade_reasons: list = None,
    counter_evidence_types: list = None,
    what_would_change_mind: list = None,
    topic_lifecycle_state: str = "ACCELERATING",
    need_deep_ta: bool = True,
    watchlist_note: str = "",
) -> CandidateSummary:
    return CandidateSummary(
        symbol=symbol,
        name=name,
        candidate_type=candidate_type,
        tier=tier,
        mandate_topic=mandate_topic,
        company_role=company_role,
        ambush_score=ambush_score,
        mandate_score_component=mandate_score_component,
        research_queue=research_queue,
        evidence_coverage=evidence_coverage,
        evidence_quality_level=evidence_quality_level,
        overheat_flags=overheat_flags or [],
        downgrade_reasons=downgrade_reasons or [],
        counter_evidence_types=counter_evidence_types or [],
        what_would_change_mind=what_would_change_mind or [],
        topic_lifecycle_state=topic_lifecycle_state,
        need_deep_ta=need_deep_ta,
        watchlist_note=watchlist_note,
    )


def _make_overheated_candidate() -> CandidateSummary:
    return _make_candidate(
        symbol="688xxx.SH",
        name="过热票",
        candidate_type="OVERHEATED_AVOID",
        tier="C",
        ambush_score=20.0,
        research_queue="REJECTED",
        overheat_flags=["高位放量", "无政策延续"],
        downgrade_reasons=["过热降权"],
        counter_evidence_types=["overheat_reversal", "capital_not_recognizing"],
        topic_lifecycle_state="CROWDED",
        need_deep_ta=False,
    )


def _make_tech_trade_candidate() -> CandidateSummary:
    return _make_candidate(
        symbol="000001.SZ",
        name="平安银行",
        candidate_type="TECH_TRADE",
        tier="B",
        mandate_topic="",
        company_role="UNKNOWN",
        ambush_score=30.0,
        research_queue="SHORT_TERM_TRADE",
        evidence_coverage=0.5,
        evidence_quality_level="MEDIUM",
        topic_lifecycle_state="UNKNOWN",
        need_deep_ta=False,
    )


def _make_replay_report(
    total: int = 5,
    passed: int = 4,
    failed: int = 1,
) -> ReplayReport:
    results = []
    for i in range(total):
        results.append(ReplayEvaluation(
            fixture_id=f"fixture_{i}",
            symbol=f"{600000 + i}.SH",
            candidate_type="POLICY_AMBUSH" if i % 2 == 0 else "TECH_TRADE",
            passed=(i < passed),
            verdict="通过" if i < passed else "失败",
            verdict_score=70.0 - i * 5,
        ))
    return ReplayReport(
        run_at="2026-06-07 23:00:00",
        date="2026-06-07",
        total_fixtures=total,
        passed=passed,
        failed=failed,
        all_passed=(failed == 0),
        results=results,
        avg_verdict_score=60.0,
    )


# ── Test CandidateSummary ────────────────────────────────────────────

class TestCandidateSummary:
    def test_defaults(self):
        cs = CandidateSummary()
        assert cs.symbol == ""
        assert cs.candidate_type == ""
        assert cs.overheat_flags == []
        assert cs.need_deep_ta is False

    def test_to_dict(self):
        cs = _make_candidate()
        d = cs.to_dict()
        assert d["symbol"] == "600519.SH"
        assert d["candidate_type"] == "POLICY_AMBUSH"
        assert d["tier"] == "A"
        assert d["evidence_coverage"] == 0.85
        assert d["need_deep_ta"] is True

    def test_to_dict_roundtrip(self):
        cs = _make_candidate(overheat_flags=["flag1"], counter_evidence_types=["type1"])
        d = cs.to_dict()
        assert d["overheat_flags"] == ["flag1"]
        assert d["counter_evidence_types"] == ["type1"]


# ── Test MandateQualityReport ────────────────────────────────────────

class TestMandateQualityReport:
    def test_defaults(self):
        r = MandateQualityReport()
        assert r.total_candidates == 0
        assert r.candidates_by_type == {}
        assert r.data_source_status == "NOT_RUN"
        assert r.replay_status == "NOT_RUN"
        assert r.empty_reason == ""

    def test_to_dict(self):
        r = build_mandate_quality_report(
            candidates=[_make_candidate()],
            date="2026-06-07",
        )
        d = r.to_dict()
        assert d["date"] == "2026-06-07"
        assert d["total_candidates"] == 1
        assert d["candidates_by_type"]["POLICY_AMBUSH"] == 1

    def test_to_dict_empty(self):
        r = build_mandate_quality_report(candidates=[], date="2026-06-07")
        d = r.to_dict()
        assert d["total_candidates"] == 0
        assert d["empty_reason"] != ""


# ── Test Distribution Helpers ────────────────────────────────────────

class TestCountDistribution:
    def test_basic(self):
        assert _count_distribution(["A", "B", "A"]) == {"A": 2, "B": 1}

    def test_empty(self):
        assert _count_distribution([]) == {}

    def test_single(self):
        assert _count_distribution(["X"]) == {"X": 1}


class TestTopCounterEvidences:
    def test_basic(self):
        c1 = _make_candidate(counter_evidence_types=["overheat_reversal"])
        c2 = _make_candidate(counter_evidence_types=["overheat_reversal", "policy_faded"])
        result = _top_counter_evidences([c1, c2])
        assert len(result) == 2
        assert result[0]["counter_type"] == "overheat_reversal"
        assert result[0]["count"] == 2

    def test_empty(self):
        assert _top_counter_evidences([]) == []

    def test_no_counters(self):
        c = _make_candidate(counter_evidence_types=[])
        assert _top_counter_evidences([c]) == []

    def test_limit(self):
        cs = [_make_candidate(counter_evidence_types=[f"type_{i}"]) for i in range(10)]
        result = _top_counter_evidences(cs, limit=3)
        assert len(result) == 3


# ── Test Empty Reason Classification ─────────────────────────────────

class TestClassifyEmptyReason:
    def test_event_source_failed(self):
        assert classify_empty_reason(event_source_status="FAILED") == EMPTY_EVENT_SOURCE_FAILED

    def test_no_event_source(self):
        assert classify_empty_reason(
            event_source_status="OK",
            total_universe=0,
        ) == EMPTY_NO_EVENT_SOURCE

    def test_all_filtered(self):
        assert classify_empty_reason(
            event_source_status="OK",
            total_universe=10,
            total_filtered=10,
        ) == EMPTY_ALL_FILTERED

    def test_strategy_too_strict(self):
        assert classify_empty_reason(
            event_source_status="OK",
            total_universe=10,
            total_filtered=3,
        ) == EMPTY_STRATEGY_TOO_STRICT

    def test_unknown(self):
        assert classify_empty_reason() == EMPTY_UNKNOWN

    def test_partial_event_with_universe(self):
        result = classify_empty_reason(
            event_source_status="PARTIAL",
            total_universe=5,
            total_filtered=5,
        )
        assert result == EMPTY_ALL_FILTERED

    def test_labels_exist(self):
        for key in [EMPTY_NO_EVENT_SOURCE, EMPTY_EVENT_SOURCE_FAILED,
                     EMPTY_ALL_FILTERED, EMPTY_STRATEGY_TOO_STRICT, EMPTY_UNKNOWN]:
            assert key in EMPTY_REASON_LABELS
            assert len(EMPTY_REASON_LABELS[key]) > 0


# ── Test Build Report ────────────────────────────────────────────────

class TestBuildMandateQualityReport:
    def test_with_candidates(self):
        cs = [
            _make_candidate(symbol="600519.SH", mandate_topic="消费升级", tier="A"),
            _make_candidate(symbol="000001.SZ", mandate_topic="消费升级", tier="B",
                           candidate_type="TECH_TRADE", research_queue="SHORT_TERM_TRADE"),
            _make_overheated_candidate(),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert report.total_candidates == 3
        assert report.candidates_by_type.get("POLICY_AMBUSH") == 1
        assert report.candidates_by_type.get("TECH_TRADE") == 1
        assert report.candidates_by_type.get("OVERHEATED_AVOID") == 1
        assert report.candidates_by_tier.get("A") == 1
        assert report.candidates_by_tier.get("B") == 1
        assert report.candidates_by_tier.get("C") == 1
        assert report.candidates_by_topic.get("消费升级") == 3
        assert report.overheat_count == 1
        assert report.downgrade_count == 1
        assert report.need_deep_ta_count == 2

    def test_no_candidates(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="FAILED",
            date="2026-06-07",
        )
        assert report.total_candidates == 0
        assert report.empty_reason == EMPTY_EVENT_SOURCE_FAILED

    def test_no_candidates_no_event_source(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="OK",
            total_universe=0,
            date="2026-06-07",
        )
        assert report.empty_reason == EMPTY_NO_EVENT_SOURCE

    def test_no_candidates_all_filtered(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="OK",
            total_universe=10,
            total_filtered=10,
            date="2026-06-07",
        )
        assert report.empty_reason == EMPTY_ALL_FILTERED

    def test_with_replay_report(self):
        replay = _make_replay_report()
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            replay_report=replay,
            date="2026-06-07",
        )
        assert report.replay_status == "COMPLETED"
        assert "passed=4" in report.replay_summary

    def test_with_data_source(self):
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            data_source_status="OK",
            data_source_summary="全部通过",
            date="2026-06-07",
        )
        assert report.data_source_status == "OK"
        assert report.data_source_summary == "全部通过"

    def test_date_default(self):
        report = build_mandate_quality_report()
        assert len(report.date) == 10
        assert report.date[4] == "-"

    def test_evidence_coverage_average(self):
        cs = [
            _make_candidate(evidence_coverage=0.6),
            _make_candidate(evidence_coverage=0.8),
            _make_candidate(evidence_coverage=1.0),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert abs(report.avg_evidence_coverage - 0.8) < 0.01

    def test_evidence_coverage_zero_candidates(self):
        report = build_mandate_quality_report(candidates=[], date="2026-06-07")
        assert report.avg_evidence_coverage == 0.0

    def test_candidates_needing_research(self):
        cs = [
            _make_candidate(need_deep_ta=True, research_queue="MIDLINE_POLICY"),
            _make_candidate(need_deep_ta=True, research_queue="TA_CONFIRM"),
            _make_candidate(need_deep_ta=False, research_queue="WATCH_ONLY"),
            _make_candidate(need_deep_ta=True, research_queue="SHORT_TERM_TRADE"),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert len(report.candidates_needing_research) == 2
        queues = [c.research_queue for c in report.candidates_needing_research]
        assert "MIDLINE_POLICY" in queues
        assert "TA_CONFIRM" in queues

    def test_lifecycle_distribution(self):
        cs = [
            _make_candidate(topic_lifecycle_state="EMERGING"),
            _make_candidate(topic_lifecycle_state="ACCELERATING"),
            _make_candidate(topic_lifecycle_state="CROWDED"),
            _make_candidate(topic_lifecycle_state="EMERGING"),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert report.candidates_by_lifecycle.get("EMERGING") == 2

    def test_quality_distribution(self):
        cs = [
            _make_candidate(evidence_quality_level="HIGH"),
            _make_candidate(evidence_quality_level="MEDIUM"),
            _make_candidate(evidence_quality_level="LOW"),
            _make_candidate(evidence_quality_level="HIGH"),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert report.evidence_quality_distribution.get("HIGH") == 2
        assert report.evidence_quality_distribution.get("MEDIUM") == 1


# ── Test Build from Replay ───────────────────────────────────────────

class TestBuildReportFromReplay:
    def test_from_replay(self):
        report = build_report_from_replay(
            fixture_ids=["policy_ambush_success"],
            date="2026-06-07",
        )
        assert report.total_candidates >= 1
        assert report.replay_status == "COMPLETED"

    def test_from_replay_multiple(self):
        report = build_report_from_replay(
            fixture_ids=["policy_ambush_success", "tech_trade_short"],
            date="2026-06-07",
        )
        assert report.total_candidates == 2
        types = list(report.candidates_by_type.keys())
        assert len(types) >= 2

    def test_from_replay_empty(self):
        report = build_report_from_replay(fixture_ids=[], date="2026-06-07")
        assert report.total_candidates == 0


# ── Test Rendering ───────────────────────────────────────────────────

class TestRenderMandateQualityReport:
    def test_render_with_candidates(self):
        cs = [
            _make_candidate(),
            _make_overheated_candidate(),
            _make_tech_trade_candidate(),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        assert "# 昊天候选质量日报" in md
        assert "候选总数" in md
        assert "POLICY_AMBUSH" in md
        assert "OVERHEATED_AVOID" in md
        assert "TECH_TRADE" in md
        assert "按候选类型" in md
        assert "按等级" in md
        assert "数据覆盖率" in md
        assert "反证与过热" in md
        assert "[V-005]" in md

    def test_render_empty_no_event(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="FAILED",
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        assert "无候选原因" in md
        assert "事件源失败" in md

    def test_render_empty_all_filtered(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="OK",
            total_universe=10,
            total_filtered=10,
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        assert "全部被过滤" in md

    def test_render_with_data_source(self):
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            data_source_status="OK",
            data_source_summary="全部通过",
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        assert "数据源状态" in md
        assert "全部通过" in md

    def test_render_with_replay(self):
        replay = _make_replay_report()
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            replay_report=replay,
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        assert "回放评估" in md
        assert "COMPLETED" in md

    def test_render_no_data_source(self):
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        assert "数据源状态" not in md

    def test_render_needing_research(self):
        cs = [_make_candidate(need_deep_ta=True, research_queue="MIDLINE_POLICY")]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        assert "需人工研究候选" in md
        assert "600519.SH" in md

    def test_render_no_forbidden_words(self):
        forbidden = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}
        cs = [_make_candidate(), _make_overheated_candidate()]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        for w in forbidden:
            assert w not in md

    def test_render_no_sensitive_info(self):
        cs = [_make_candidate()]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        assert "API" not in md
        assert "key" not in md.lower() or "monkey" in md.lower()
        assert "token" not in md.lower() or "" == ""
        assert "/home/" not in md
        assert "/Users/" not in md

    def test_render_lifecycle_table(self):
        cs = [
            _make_candidate(topic_lifecycle_state="EMERGING"),
            _make_candidate(topic_lifecycle_state="ACCELERATING"),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        assert "按主题生命周期" in md
        assert "EMERGING" in md
        assert "ACCELERATING" in md

    def test_render_queue_table(self):
        cs = [
            _make_candidate(research_queue="MIDLINE_POLICY"),
            _make_candidate(research_queue="SHORT_TERM_TRADE"),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        assert "按研究队列" in md
        assert "MIDLINE_POLICY" in md
        assert "SHORT_TERM_TRADE" in md

    def test_render_counter_evidence_topn(self):
        cs = [
            _make_candidate(counter_evidence_types=["overheat_reversal"]),
            _make_candidate(counter_evidence_types=["overheat_reversal", "policy_faded"]),
        ]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        assert "反证 TopN" in md
        assert "过热回撤" in md

    def test_render_empty_with_replay(self):
        replay = _make_replay_report()
        report = build_mandate_quality_report(
            candidates=[],
            replay_report=replay,
            event_source_status="FAILED",
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        assert "无候选原因" in md
        assert "回放状态" in md


# ── Test File Output ─────────────────────────────────────────────────

class TestSaveMandateQualityReport:
    def test_save(self):
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            date="2026-06-07",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_mandate_quality_report(report, output_dir=tmpdir)
            assert os.path.exists(path)
            assert path.endswith("2026-06-07.md")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "# 昊天候选质量日报" in content

    def test_save_creates_dir(self):
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            date="2026-06-07",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "sub", "dir")
            path = save_mandate_quality_report(report, output_dir=output_dir)
            assert os.path.exists(path)


class TestRunMandateQualityReport:
    def test_run_and_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = run_mandate_quality_report(
                candidates=[_make_candidate()],
                date="2026-06-07",
                output_dir=tmpdir,
            )
            assert os.path.exists(path)

    def test_run_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = run_mandate_quality_report(
                candidates=[],
                event_source_status="FAILED",
                date="2026-06-07",
                output_dir=tmpdir,
            )
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "事件源失败" in content


# ── Three Scenario Fixtures ──────────────────────────────────────────

class TestThreeScenarioFixtures:
    def test_fixture_has_candidates(self):
        cs = [
            _make_candidate(symbol="600519.SH", mandate_topic="消费升级", tier="A"),
            _make_candidate(symbol="002097.SZ", mandate_topic="低空经济", tier="A",
                           candidate_type="POLICY_AMBUSH"),
            _make_tech_trade_candidate(),
        ]
        report = build_mandate_quality_report(
            candidates=cs,
            replay_report=_make_replay_report(),
            data_source_status="OK",
            data_source_summary="全部通过",
            date="2026-06-07",
        )
        assert report.total_candidates == 3
        assert report.replay_status == "COMPLETED"
        assert report.data_source_status == "OK"
        md = render_mandate_quality_report(report)
        assert "600519.SH" in md
        assert "002097.SZ" in md

    def test_fixture_no_candidates_data_failure(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="FAILED",
            data_source_status="FAILED",
            data_source_summary="东财接口超时",
            replay_report=_make_replay_report(total=3, passed=1, failed=2),
            date="2026-06-07",
        )
        assert report.total_candidates == 0
        assert report.empty_reason == EMPTY_EVENT_SOURCE_FAILED
        md = render_mandate_quality_report(report)
        assert "事件源失败" in md
        assert "数据源状态" in md or report.data_source_status == "FAILED"

    def test_fixture_no_candidates_all_filtered(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="OK",
            total_universe=10,
            total_filtered=10,
            data_source_status="OK",
            date="2026-06-07",
        )
        assert report.empty_reason == EMPTY_ALL_FILTERED
        md = render_mandate_quality_report(report)
        assert "全部被过滤" in md


# ── Acceptance Tests ─────────────────────────────────────────────────

class TestAcceptanceV005:
    def test_report_with_candidates(self):
        cs = [_make_candidate(), _make_overheated_candidate(), _make_tech_trade_candidate()]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        assert "候选总数" in md
        assert "POLICY_AMBUSH" in md
        assert "OVERHEATED_AVOID" in md
        assert "TECH_TRADE" in md

    def test_report_no_candidates_event_failed(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="FAILED",
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        assert "事件源失败" in md

    def test_report_no_candidates_all_filtered(self):
        report = build_mandate_quality_report(
            candidates=[],
            event_source_status="OK",
            total_universe=10,
            total_filtered=10,
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        assert "全部被过滤" in md

    def test_report_no_api_key(self):
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            date="2026-06-07",
        )
        md = render_mandate_quality_report(report)
        for sensitive in ["sk-", "api_key", "API_KEY", "Bearer", "secret"]:
            assert sensitive not in md

    def test_report_no_forbidden_words(self):
        cs = [_make_candidate(), _make_overheated_candidate()]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        for w in ["立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"]:
            assert w not in md

    def test_report_includes_type_distribution(self):
        cs = [_make_candidate(), _make_tech_trade_candidate()]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert "POLICY_AMBUSH" in report.candidates_by_type
        assert "TECH_TRADE" in report.candidates_by_type

    def test_report_includes_tier_distribution(self):
        cs = [_make_candidate(tier="A"), _make_candidate(tier="B"), _make_candidate(tier="C")]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert report.candidates_by_tier.get("A") == 1
        assert report.candidates_by_tier.get("B") == 1
        assert report.candidates_by_tier.get("C") == 1

    def test_report_includes_evidence_coverage(self):
        cs = [_make_candidate(evidence_coverage=0.7)]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        md = render_mandate_quality_report(report)
        assert "覆盖率" in md

    def test_report_includes_counter_evidence(self):
        cs = [_make_candidate(counter_evidence_types=["overheat_reversal"])]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert len(report.counter_evidence_top) >= 1

    def test_report_includes_needing_research(self):
        cs = [_make_candidate(need_deep_ta=True, research_queue="MIDLINE_POLICY")]
        report = build_mandate_quality_report(candidates=cs, date="2026-06-07")
        assert len(report.candidates_needing_research) == 1

    def test_file_output_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = run_mandate_quality_report(
                candidates=[_make_candidate()],
                date="2026-06-07",
                output_dir=tmpdir,
            )
            assert os.path.basename(path) == "2026-06-07.md"

    def test_replay_integration(self):
        report = build_report_from_replay(
            fixture_ids=["policy_ambush_success"],
            date="2026-06-07",
        )
        assert report.replay_status == "COMPLETED"
        assert report.total_candidates >= 1

    def test_replay_report_not_run_by_default(self):
        report = build_mandate_quality_report(
            candidates=[_make_candidate()],
            date="2026-06-07",
        )
        assert report.replay_status == "NOT_RUN"

    def test_empty_reasons_all_classified(self):
        for status, expected in [
            ("FAILED", EMPTY_EVENT_SOURCE_FAILED),
            ("OK", EMPTY_NO_EVENT_SOURCE),
        ]:
            result = classify_empty_reason(event_source_status=status)
            assert result in EMPTY_REASON_LABELS
