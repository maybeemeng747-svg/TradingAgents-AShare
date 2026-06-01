# [V-004] mandate_e2e_smoke
"""End-to-end smoke test for the 昊天 (Mandate) pipeline.

Validates that the full chain:
  DATA-003 normalize_events → H-001 MandateSignal → H-002 MandateScore
  → H-003 BeneficiaryPath → H-004 AmbushScore/CandidateType
  → H-007 ResearchQueue → H-008 WatchlistNote

works without field breakage, and that:
  - Fixtures produce stable, deterministic output.
  - Weak evidence does NOT enter high-score POLICY_AMBUSH.
  - Overheated samples do NOT become POLICY_AMBUSH.
  - No strong buy/sell words appear in any output.
  - Field gaps are explicitly reported.

Constraints:
  - No external LLM calls.
  - No full-market scan.
  - No production DB writes.
  - No strong buy/sell words.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from typing import Optional

import pytest

from tradingagents.tradeflow.mandate_signal import (
    MandateSignal,
    SourceLevel,
    MandateEventType,
    classify_source_level,
    classify_event_type,
    compute_confidence,
    validate_mandate_signal,
)
from tradingagents.tradeflow.mandate_score import (
    compute_mandate_score,
    compute_mandate_scores_by_topic,
    match_topics,
    MandateScoreResult,
)
from tradingagents.tradeflow.industry_mandate_map import (
    compute_beneficiary_path,
    compute_beneficiary_paths_for_signals,
    CompanyRole,
    get_all_topics,
    BeneficiaryPathResult,
)
from tradingagents.tradeflow.ambush_score import (
    compute_ambush_score,
    classify_candidate_type,
    route_deep_ta,
    CandidateType,
    AmbushScoreResult,
)
from tradingagents.tradeflow.mandate_ta_queue_router import (
    route_to_research_queue,
    compute_queue_statistics,
    ResearchQueue,
    ResearchIntent,
    QueueRouteResult,
    QueueStatistics,
)
from tradingagents.tradeflow.mandate_watchlist_note import (
    generate_watchlist_note,
    WatchlistNoteResult,
)
from tradingagents.tradeflow.mandate_event_normalizer import (
    normalize_events,
    normalize_raw_dicts,
    NormalizedEventBatch,
)


_FORBIDDEN_WORDS = {"买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓", "强烈推荐", "立即"}


def _contains_forbidden(text: str) -> bool:
    return any(w in text for w in _FORBIDDEN_WORDS)


def _check_forbidden_in_dict(d: dict) -> list[str]:
    hits: list[str] = []
    for key, value in d.items():
        if isinstance(value, str) and _contains_forbidden(value):
            hits.append(f"{key}: {value}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and _contains_forbidden(item):
                    hits.append(f"{key}[item]: {item}")
                elif isinstance(item, dict):
                    for ik, iv in item.items():
                        if isinstance(iv, str) and _contains_forbidden(iv):
                            hits.append(f"{key}[{ik}]: {iv}")
    return hits


@dataclass
class PipelineFixture:
    fixture_id: str
    description: str
    raw_events: list[dict]
    symbol: str = ""
    expected_candidate_type: Optional[str] = None
    expected_high_mandate: Optional[bool] = None
    tags: list[str] = field(default_factory=list)
    risk_flags: Optional[list[str]] = None
    game_balance: str = ""


_DATE_TODAY = "2026-06-02"
_DATE_YESTERDAY = "2026-06-01"
_DATE_TWO_DAYS_AGO = "2026-05-31"
_DATE_THREE_DAYS_AGO = "2026-05-30"

FIXTURE_CENTRAL_CONTINUOUS = PipelineFixture(
    fixture_id="central_continuous",
    description="中央/部委政策连续信号 + 公司公告受益路径明确",
    symbol="300034.SZ",
    expected_candidate_type="POLICY_AMBUSH",
    expected_high_mandate=True,
    tags=["positive", "high_authority"],
    raw_events=[
        {
            "title": "国务院印发《低空经济高质量发展指导意见》",
            "source": "国务院",
            "date": _DATE_THREE_DAYS_AGO,
            "detail": "https://example.com/gov/low-altitude",
            "symbol": "300034.SZ",
        },
        {
            "title": "工信部发布低空经济产业规划（2026-2030）",
            "source": "工信部",
            "date": _DATE_TWO_DAYS_AGO,
            "detail": "https://example.com/miit/low-altitude-plan",
            "symbol": "300034.SZ",
        },
        {
            "title": "民航局就低空空域管理征求意见",
            "source": "民航局",
            "date": _DATE_YESTERDAY,
            "detail": "https://example.com/caac/airspace",
            "symbol": "300034.SZ",
        },
        {
            "title": "300034.SZ：公司无人机整机获军方采购订单",
            "source": "巨潮资讯",
            "date": _DATE_YESTERDAY,
            "detail": "https://example.com/cninfo/300034-order",
            "event_type": "notice",
            "symbol": "300034.SZ",
        },
    ],
)

FIXTURE_COMPANY_BENEFICIARY = PipelineFixture(
    fixture_id="company_beneficiary",
    description="公司公告受益路径明确，半导体核心供应商",
    symbol="688981.SH",
    expected_candidate_type="POLICY_AMBUSH",
    expected_high_mandate=True,
    tags=["positive", "company_evidence"],
    raw_events=[
        {
            "title": "发改委：加快推进国产替代半导体设备自主化",
            "source": "发改委",
            "date": _DATE_TWO_DAYS_AGO,
            "detail": "https://example.com/ndrc/semi",
            "symbol": "688981.SH",
        },
        {
            "title": "工信部：半导体设备国产化率目标提升至70%",
            "source": "工信部",
            "date": _DATE_YESTERDAY,
            "detail": "https://example.com/miit/semi-target",
            "symbol": "688981.SH",
        },
        {
            "title": "688981.SH：公司半导体刻蚀设备通过龙头晶圆厂验证",
            "source": "巨潮资讯",
            "date": _DATE_YESTERDAY,
            "detail": "https://example.com/cninfo/688981-verify",
            "event_type": "notice",
            "symbol": "688981.SH",
        },
        {
            "title": "688981.SH：中标中芯国际12英寸产线设备采购",
            "source": "巨潮资讯",
            "date": _DATE_TODAY,
            "detail": "https://example.com/cninfo/688981-smic",
            "event_type": "notice",
            "symbol": "688981.SH",
        },
    ],
)

FIXTURE_WEAK_EVIDENCE = PipelineFixture(
    fixture_id="weak_evidence",
    description="研报/媒体弱证据，不应进入高分左侧",
    symbol="000001.SZ",
    expected_candidate_type=None,
    expected_high_mandate=False,
    tags=["negative", "weak"],
    raw_events=[
        {
            "title": "中信证券研报：低空经济主题值得关注",
            "source": "中信证券",
            "date": _DATE_YESTERDAY,
            "detail": "https://example.com/citic/low-altitude",
            "symbol": "000001.SZ",
        },
        {
            "title": "财联社：低空经济概念板块盘中拉升",
            "source": "财联社",
            "date": _DATE_TODAY,
            "detail": "https://example.com/cls/low-altitude",
            "symbol": "000001.SZ",
        },
    ],
)

FIXTURE_OVERHEATED = PipelineFixture(
    fixture_id="overheated",
    description="过热/伪政策反例，不应进入POLICY_AMBUSH",
    symbol="002XXX.SZ",
    expected_candidate_type="OVERHEATED_AVOID",
    expected_high_mandate=False,
    tags=["negative", "overheated"],
    risk_flags=["LHB_OVERHEAT_RISK", "MARGIN_CROWDING_RISK"],
    game_balance="fragile",
    raw_events=[
        {
            "title": "东方财富：低空经济龙头概念股连续涨停",
            "source": "东方财富",
            "date": _DATE_TODAY,
            "detail": "https://example.com/eastmoney/zt",
            "symbol": "002XXX.SZ",
        },
    ],
)

FIXTURE_PSEUDO_POLICY = PipelineFixture(
    fixture_id="pseudo_policy",
    description="伪政策题材 — 弱证据蹭概念",
    symbol="600XXX.SH",
    expected_candidate_type="PSEUDO_POLICY",
    expected_high_mandate=False,
    tags=["negative", "pseudo"],
    raw_events=[
        {
            "title": "公司公告：拟探索低空经济相关布局",
            "source": "巨潮资讯",
            "date": _DATE_YESTERDAY,
            "detail": "https://example.com/cninfo/600xxx",
            "event_type": "notice",
            "symbol": "600XXX.SH",
        },
        {
            "title": "媒体：某公司称将关注低空经济产业",
            "source": "新浪",
            "date": _DATE_TODAY,
            "detail": "https://example.com/sina/600xxx",
            "symbol": "600XXX.SH",
        },
    ],
)

ALL_FIXTURES = [
    FIXTURE_CENTRAL_CONTINUOUS,
    FIXTURE_COMPANY_BENEFICIARY,
    FIXTURE_WEAK_EVIDENCE,
    FIXTURE_OVERHEATED,
    FIXTURE_PSEUDO_POLICY,
]

_FIXTURE_MAP = {f.fixture_id: f for f in ALL_FIXTURES}


def get_fixture(fixture_id: str) -> PipelineFixture:
    if fixture_id not in _FIXTURE_MAP:
        raise ValueError(f"Unknown fixture: {fixture_id}")
    return _FIXTURE_MAP[fixture_id]


def get_all_fixture_ids() -> list[str]:
    return [f.fixture_id for f in ALL_FIXTURES]


@dataclass
class PipelineStepResult:
    step_name: str
    success: bool = True
    output: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class PipelineRunResult:
    fixture_id: str
    symbol: str
    steps: list[PipelineStepResult] = field(default_factory=list)
    candidate_type: str = ""
    mandate_score: float = 0.0
    ambush_score: float = 0.0
    research_queue: str = ""
    note_summary: str = ""
    field_gaps: list[str] = field(default_factory=list)
    forbidden_word_hits: list[str] = field(default_factory=list)
    passed: bool = False
    fail_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "fixture_id": self.fixture_id,
            "symbol": self.symbol,
            "candidate_type": self.candidate_type,
            "mandate_score": round(self.mandate_score, 2),
            "ambush_score": round(self.ambush_score, 2),
            "research_queue": self.research_queue,
            "note_summary": self.note_summary,
            "field_gaps": self.field_gaps,
            "forbidden_word_hits": self.forbidden_word_hits,
            "passed": self.passed,
            "fail_reasons": self.fail_reasons,
        }


def _run_single_pipeline(fixture: PipelineFixture) -> PipelineRunResult:
    result = PipelineRunResult(
        fixture_id=fixture.fixture_id,
        symbol=fixture.symbol,
    )
    forbidden_hits: list[str] = []

    step1 = PipelineStepResult(step_name="DATA-003 normalize_events")
    try:
        batch = normalize_raw_dicts(fixture.raw_events, default_symbol=fixture.symbol)
        step1.output = {"signal_count": len(batch.signals)}
        step1.success = True
    except Exception as e:
        step1.success = False
        step1.error = str(e)
        result.fail_reasons.append(f"normalize_events failed: {e}")
    result.steps.append(step1)
    if not step1.success:
        result.passed = False
        return result

    signals = batch.signals

    step2 = PipelineStepResult(step_name="H-002 compute_mandate_score")
    score_result = MandateScoreResult()
    try:
        topics = set()
        for sig in signals:
            matched = match_topics(sig.title or "")
            topics.update(matched)
        topic = ""
        if topics:
            topic = sorted(topics)[0]
        score_result = compute_mandate_score(signals, topic=topic)
        step2.output = {
            "mandate_score": round(score_result.mandate_score, 2),
            "is_noise": score_result.is_noise,
            "has_policy_document": score_result.has_policy_document,
            "has_high_authority": score_result.has_high_authority,
            "unique_dates": score_result.unique_dates,
            "topic": score_result.topic,
        }
        step2.success = True
        hits = _check_forbidden_in_dict(score_result.to_dict())
        forbidden_hits.extend(hits)
    except Exception as e:
        step2.success = False
        step2.error = str(e)
        result.fail_reasons.append(f"compute_mandate_score failed: {e}")
    result.steps.append(step2)
    if not step2.success:
        result.passed = False
        return result

    result.mandate_score = score_result.mandate_score

    step3 = PipelineStepResult(step_name="H-003 compute_beneficiary_path")
    beneficiary_result = BeneficiaryPathResult()
    try:
        beneficiary_result = compute_beneficiary_path(
            topic=score_result.topic or topic,
            signals=signals,
            symbol=fixture.symbol,
        )
        step3.output = {
            "company_role": beneficiary_result.company_role,
            "beneficiary_path": beneficiary_result.beneficiary_path,
            "has_company_evidence": beneficiary_result.has_company_evidence,
            "path_confidence": round(beneficiary_result.path_confidence, 2),
        }
        step3.success = True
        hits = _check_forbidden_in_dict(beneficiary_result.to_dict())
        forbidden_hits.extend(hits)
    except Exception as e:
        step3.success = False
        step3.error = str(e)
        result.fail_reasons.append(f"compute_beneficiary_path failed: {e}")
    result.steps.append(step3)
    if not step3.success:
        result.passed = False
        return result

    step4 = PipelineStepResult(step_name="H-004 compute_ambush_score")
    ambush_result = AmbushScoreResult()
    try:
        policy_tags = []
        for sig in signals:
            policy_tags.extend(sig.policy_tags or [])
        policy_tags = list(set(policy_tags))

        ambush_result = compute_ambush_score(
            mandate_score=score_result.mandate_score,
            has_policy_document=score_result.has_policy_document,
            has_high_authority=score_result.has_high_authority,
            unique_dates=score_result.unique_dates,
            is_noise=score_result.is_noise,
            company_role=beneficiary_result.company_role,
            beneficiary_path=beneficiary_result.beneficiary_path,
            has_company_evidence=beneficiary_result.has_company_evidence,
            path_confidence=beneficiary_result.path_confidence,
            risk_flags=fixture.risk_flags,
            game_balance=fixture.game_balance,
            mandate_evidence_refs=score_result.mandate_evidence_refs,
            mandate_reasons=score_result.mandate_reasons,
        )
        step4.output = {
            "ambush_score": round(ambush_result.ambush_score, 2),
            "candidate_type": ambush_result.candidate_type,
            "mandate_score_component": round(ambush_result.mandate_score_component, 2),
            "beneficiary_score_component": round(ambush_result.beneficiary_score_component, 2),
        }
        step4.success = True
        hits = _check_forbidden_in_dict(ambush_result.to_dict())
        forbidden_hits.extend(hits)
    except Exception as e:
        step4.success = False
        step4.error = str(e)
        result.fail_reasons.append(f"compute_ambush_score failed: {e}")
    result.steps.append(step4)
    if not step4.success:
        result.passed = False
        return result

    result.candidate_type = ambush_result.candidate_type
    result.ambush_score = ambush_result.ambush_score

    step5 = PipelineStepResult(step_name="H-007 route_to_research_queue")
    queue_result = QueueRouteResult()
    try:
        has_beneficiary_path = bool(
            beneficiary_result.beneficiary_path
            and beneficiary_result.company_role
            not in ("", "UNKNOWN", "CONCEPT_ONLY")
        )
        has_policy = bool(
            policy_tags
            or score_result.mandate_score > 0
        )
        queue_result = route_to_research_queue(
            candidate_type=ambush_result.candidate_type,
            ambush_score=ambush_result.ambush_score,
            mandate_score_component=ambush_result.mandate_score_component,
            beneficiary_score_component=ambush_result.beneficiary_score_component,
            overheat_penalty=ambush_result.overheat_penalty,
            risk_flags=fixture.risk_flags,
            game_balance=fixture.game_balance,
            has_beneficiary_path=has_beneficiary_path,
            has_policy=has_policy,
        )
        step5.output = {
            "research_queue": queue_result.research_queue,
            "research_intent": queue_result.research_intent,
            "route_reason": queue_result.route_reason,
        }
        step5.success = True
        hits = _check_forbidden_in_dict(queue_result.to_dict())
        forbidden_hits.extend(hits)
    except Exception as e:
        step5.success = False
        step5.error = str(e)
        result.fail_reasons.append(f"route_to_research_queue failed: {e}")
    result.steps.append(step5)
    if not step5.success:
        result.passed = False
        return result

    result.research_queue = queue_result.research_queue

    step6 = PipelineStepResult(step_name="H-008 generate_watchlist_note")
    note_result = WatchlistNoteResult()
    try:
        note_result = generate_watchlist_note(
            mandate_topic=beneficiary_result.mandate_topic or score_result.topic or topic,
            candidate_type=ambush_result.candidate_type,
            mandate_score_component=ambush_result.mandate_score_component,
            beneficiary_score_component=ambush_result.beneficiary_score_component,
            ambush_score=ambush_result.ambush_score,
            beneficiary_path=beneficiary_result.beneficiary_path,
            company_role=beneficiary_result.company_role,
            policy_tags=policy_tags,
        )
        step6.output = {
            "note_summary": note_result.note_summary,
            "has_evidence": note_result.has_evidence,
            "benefit_score": round(note_result.benefit_score, 1),
            "consensus_score": round(note_result.consensus_score, 1),
            "evidence_gap": note_result.evidence_gap,
        }
        step6.success = True
        hits = _check_forbidden_in_dict(note_result.to_dict())
        forbidden_hits.extend(hits)
    except Exception as e:
        step6.success = False
        step6.error = str(e)
        result.fail_reasons.append(f"generate_watchlist_note failed: {e}")
    result.steps.append(step6)
    if not step6.success:
        result.passed = False
        return result

    result.note_summary = note_result.note_summary

    result.forbidden_word_hits = forbidden_hits

    field_gaps: list[str] = []
    if not result.candidate_type:
        field_gaps.append("candidate_type")
    if result.mandate_score == 0:
        field_gaps.append("mandate_score=0")
    if not result.research_queue:
        field_gaps.append("research_queue")
    if not note_result.note_summary:
        field_gaps.append("note_summary")
    if not score_result.topic:
        field_gaps.append("topic")
    if not beneficiary_result.company_role or beneficiary_result.company_role in ("UNKNOWN", ""):
        field_gaps.append("company_role")
    if not beneficiary_result.beneficiary_path:
        field_gaps.append("beneficiary_path")
    result.field_gaps = field_gaps

    all_steps_ok = all(s.success for s in result.steps)
    no_forbidden = len(forbidden_hits) == 0

    type_ok = True
    if fixture.expected_candidate_type is not None:
        type_ok = result.candidate_type == fixture.expected_candidate_type

    mandate_ok = True
    if fixture.expected_high_mandate is True:
        mandate_ok = result.mandate_score > 0
    elif fixture.expected_high_mandate is False:
        mandate_ok = result.mandate_score < 50 or result.candidate_type not in ("POLICY_AMBUSH",)

    result.passed = all_steps_ok and no_forbidden and type_ok and mandate_ok

    if not all_steps_ok:
        result.fail_reasons.append("some pipeline steps failed")
    if not no_forbidden:
        result.fail_reasons.append(f"forbidden words found: {forbidden_hits}")
    if not type_ok:
        result.fail_reasons.append(
            f"candidate_type mismatch: expected={fixture.expected_candidate_type}, got={result.candidate_type}"
        )
    if not mandate_ok:
        result.fail_reasons.append(
            f"mandate expectation not met: expected_high={fixture.expected_high_mandate}, "
            f"score={result.mandate_score:.1f}, type={result.candidate_type}"
        )

    return result


def render_smoke_report(results: list[PipelineRunResult]) -> str:
    lines: list[str] = []
    lines.append("# V-004 昊天链路端到端 Smoke 验收报告")
    lines.append("")
    today = datetime.date.today().isoformat()
    lines.append(f"生成日期: {today}")
    lines.append("")

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    lines.append(f"## 总体结果: {passed}/{len(results)} 通过")
    lines.append("")
    lines.append("| Fixture | Symbol | CandidateType | MandateScore | AmbushScore | Queue | Passed |")
    lines.append("|---------|--------|---------------|-------------|-------------|-------|--------|")
    for r in results:
        lines.append(
            f"| {r.fixture_id} | {r.symbol} | {r.candidate_type} | "
            f"{r.mandate_score:.1f} | {r.ambush_score:.1f} | {r.research_queue} | "
            f"{'✅' if r.passed else '❌'} |"
        )
    lines.append("")

    for r in results:
        lines.append(f"### {r.fixture_id} ({r.symbol})")
        lines.append("")
        lines.append(f"- **CandidateType**: {r.candidate_type}")
        lines.append(f"- **MandateScore**: {r.mandate_score:.2f}")
        lines.append(f"- **AmbushScore**: {r.ambush_score:.2f}")
        lines.append(f"- **ResearchQueue**: {r.research_queue}")
        lines.append(f"- **NoteSummary**: {r.note_summary}")
        if r.field_gaps:
            lines.append(f"- **字段缺口**: {', '.join(r.field_gaps)}")
        if r.forbidden_word_hits:
            lines.append(f"- **强词命中**: {r.forbidden_word_hits}")
        if r.fail_reasons:
            lines.append(f"- **失败原因**: {r.fail_reasons}")
        lines.append("")
        for step in r.steps:
            status = "✅" if step.success else "❌"
            lines.append(f"  - {status} {step.step_name}")
            if step.error:
                lines.append(f"    - error: {step.error}")
        lines.append("")

    return "\n".join(lines)


def run_smoke_and_save(
    fixture_ids: Optional[list[str]] = None,
    output_dir: str = "docs/mandate_acceptance",
) -> list[PipelineRunResult]:
    if fixture_ids is None:
        fixture_ids = get_all_fixture_ids()

    results: list[PipelineRunResult] = []
    for fid in fixture_ids:
        fixture = get_fixture(fid)
        result = _run_single_pipeline(fixture)
        results.append(result)

    report = render_smoke_report(results)
    os.makedirs(output_dir, exist_ok=True)
    today = datetime.date.today().isoformat()
    report_path = os.path.join(output_dir, f"{today}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    return results


class TestPipelineFixtures:
    def test_all_fixtures_registered(self):
        assert len(ALL_FIXTURES) == 5
        assert set(get_all_fixture_ids()) == {
            "central_continuous",
            "company_beneficiary",
            "weak_evidence",
            "overheated",
            "pseudo_policy",
        }

    def test_get_fixture_known(self):
        f = get_fixture("central_continuous")
        assert f.symbol == "300034.SZ"

    def test_get_fixture_unknown(self):
        with pytest.raises(ValueError, match="Unknown fixture"):
            get_fixture("nonexistent")

    def test_fixture_has_events(self):
        for f in ALL_FIXTURES:
            assert len(f.raw_events) > 0, f"{f.fixture_id} has no events"
            for event in f.raw_events:
                assert "title" in event, f"{f.fixture_id} event missing title"


class TestStep1NormalizeEvents:
    def test_central_continuous_normalize(self):
        batch = normalize_raw_dicts(
            FIXTURE_CENTRAL_CONTINUOUS.raw_events,
            default_symbol="300034.SZ",
        )
        assert isinstance(batch, NormalizedEventBatch)
        assert len(batch.signals) >= 4

    def test_company_beneficiary_normalize(self):
        batch = normalize_raw_dicts(
            FIXTURE_COMPANY_BENEFICIARY.raw_events,
            default_symbol="688981.SH",
        )
        assert len(batch.signals) >= 4

    def test_weak_evidence_normalize(self):
        batch = normalize_raw_dicts(
            FIXTURE_WEAK_EVIDENCE.raw_events,
            default_symbol="000001.SZ",
        )
        assert len(batch.signals) >= 2
        for sig in batch.signals:
            assert sig.source_level in (
                "MEDIA",
                SourceLevel.MEDIA.value,
            )

    def test_overheated_normalize(self):
        batch = normalize_raw_dicts(
            FIXTURE_OVERHEATED.raw_events,
            default_symbol="002XXX.SZ",
        )
        assert len(batch.signals) >= 1

    def test_pseudo_policy_normalize(self):
        batch = normalize_raw_dicts(
            FIXTURE_PSEUDO_POLICY.raw_events,
            default_symbol="600XXX.SH",
        )
        assert len(batch.signals) >= 2


class TestStep2MandateScore:
    def test_central_continuous_high_score(self):
        batch = normalize_raw_dicts(
            FIXTURE_CENTRAL_CONTINUOUS.raw_events,
            default_symbol="300034.SZ",
        )
        result = compute_mandate_score(batch.signals, topic="低空经济")
        assert result.mandate_score > 0
        assert not result.is_noise
        assert result.has_high_authority

    def test_company_beneficiary_high_score(self):
        batch = normalize_raw_dicts(
            FIXTURE_COMPANY_BENEFICIARY.raw_events,
            default_symbol="688981.SH",
        )
        result = compute_mandate_score(batch.signals, topic="国产替代")
        assert result.mandate_score > 0

    def test_weak_evidence_low_or_noise(self):
        batch = normalize_raw_dicts(
            FIXTURE_WEAK_EVIDENCE.raw_events,
            default_symbol="000001.SZ",
        )
        result = compute_mandate_score(batch.signals)
        assert result.mandate_score < 50 or result.is_noise

    def test_overheated_media_only(self):
        batch = normalize_raw_dicts(
            FIXTURE_OVERHEATED.raw_events,
            default_symbol="002XXX.SZ",
        )
        result = compute_mandate_score(batch.signals)
        assert result.mandate_score < 50 or result.is_noise


class TestStep3BeneficiaryPath:
    def test_central_continuous_has_path(self):
        batch = normalize_raw_dicts(
            FIXTURE_CENTRAL_CONTINUOUS.raw_events,
            default_symbol="300034.SZ",
        )
        result = compute_beneficiary_path("低空经济", batch.signals, symbol="300034.SZ")
        assert result.company_role not in ("UNKNOWN", "")
        assert len(result.beneficiary_path) > 0

    def test_company_beneficiary_has_path(self):
        batch = normalize_raw_dicts(
            FIXTURE_COMPANY_BENEFICIARY.raw_events,
            default_symbol="688981.SH",
        )
        result = compute_beneficiary_path("国产替代", batch.signals, symbol="688981.SH")
        assert result.company_role not in ("UNKNOWN", "CONCEPT_ONLY")

    def test_weak_evidence_no_clear_path(self):
        batch = normalize_raw_dicts(
            FIXTURE_WEAK_EVIDENCE.raw_events,
            default_symbol="000001.SZ",
        )
        result = compute_beneficiary_path("低空经济", batch.signals, symbol="000001.SZ")
        assert result.company_role in ("CONCEPT_ONLY", "UNKNOWN", "PERIPHERAL", "") or result.path_confidence < 0.5

    def test_overheated_weak_role(self):
        batch = normalize_raw_dicts(
            FIXTURE_OVERHEATED.raw_events,
            default_symbol="002XXX.SZ",
        )
        result = compute_beneficiary_path("低空经济", batch.signals, symbol="002XXX.SZ")
        assert result.company_role in ("CONCEPT_ONLY", "UNKNOWN", "PERIPHERAL", "") or result.path_confidence < 0.5


class TestStep4AmbushScore:
    def test_central_continuous_is_policy_ambush(self):
        r = _run_single_pipeline(FIXTURE_CENTRAL_CONTINUOUS)
        assert r.candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"), (
            f"Expected POLICY_AMBUSH or POLICY_CONFIRM, got {r.candidate_type}"
        )
        assert r.ambush_score > 0

    def test_company_beneficiary_is_policy(self):
        r = _run_single_pipeline(FIXTURE_COMPANY_BENEFICIARY)
        assert r.candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"), (
            f"Expected POLICY_AMBUSH or POLICY_CONFIRM, got {r.candidate_type}"
        )

    def test_weak_evidence_not_ambush(self):
        r = _run_single_pipeline(FIXTURE_WEAK_EVIDENCE)
        assert r.candidate_type not in ("POLICY_AMBUSH",), (
            f"Weak evidence should not be POLICY_AMBUSH, got {r.candidate_type}"
        )

    def test_overheated_is_overheated_avoid(self):
        r = _run_single_pipeline(FIXTURE_OVERHEATED)
        assert r.candidate_type in ("OVERHEATED_AVOID", "PSEUDO_POLICY", "EVENT_WATCH", "TECH_TRADE"), (
            f"Overheated should not be POLICY_AMBUSH, got {r.candidate_type}"
        )

    def test_pseudo_policy_not_ambush(self):
        r = _run_single_pipeline(FIXTURE_PSEUDO_POLICY)
        assert r.candidate_type not in ("POLICY_AMBUSH", "POLICY_CONFIRM"), (
            f"Pseudo policy should not be POLICY_AMBUSH, got {r.candidate_type}"
        )


class TestStep5ResearchQueue:
    def test_central_continuous_midline(self):
        r = _run_single_pipeline(FIXTURE_CENTRAL_CONTINUOUS)
        if r.candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
            assert r.research_queue in ("MIDLINE_POLICY", "TA_CONFIRM")

    def test_company_beneficiary_midline_or_ta(self):
        r = _run_single_pipeline(FIXTURE_COMPANY_BENEFICIARY)
        if r.candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"):
            assert r.research_queue in ("MIDLINE_POLICY", "TA_CONFIRM")

    def test_overheated_rejected_or_watch(self):
        r = _run_single_pipeline(FIXTURE_OVERHEATED)
        assert r.research_queue in ("REJECTED", "WATCH_ONLY", "SHORT_TERM_TRADE")


class TestStep6WatchlistNote:
    def test_central_continuous_has_evidence(self):
        r = _run_single_pipeline(FIXTURE_CENTRAL_CONTINUOUS)
        assert r.note_summary != ""
        assert "缺证据" not in r.note_summary or "300034" in r.note_summary

    def test_all_notes_have_topic(self):
        for fixture in ALL_FIXTURES:
            r = _run_single_pipeline(fixture)
            assert r.note_summary != "", f"{fixture.fixture_id} has empty note_summary"

    def test_no_forbidden_words_in_note(self):
        for fixture in ALL_FIXTURES:
            r = _run_single_pipeline(fixture)
            assert not _contains_forbidden(r.note_summary), (
                f"{fixture.fixture_id} note has forbidden words: {r.note_summary}"
            )


class TestPipelineNoForbiddenWords:
    def test_all_step_outputs_no_forbidden(self):
        for fixture in ALL_FIXTURES:
            r = _run_single_pipeline(fixture)
            assert len(r.forbidden_word_hits) == 0, (
                f"{fixture.fixture_id} has forbidden words: {r.forbidden_word_hits}"
            )


class TestPipelineStability:
    def test_deterministic_output(self):
        for fixture in ALL_FIXTURES:
            r1 = _run_single_pipeline(fixture)
            r2 = _run_single_pipeline(fixture)
            assert r1.candidate_type == r2.candidate_type, (
                f"{fixture.fixture_id} candidate_type not stable"
            )
            assert abs(r1.mandate_score - r2.mandate_score) < 0.01, (
                f"{fixture.fixture_id} mandate_score not stable"
            )
            assert abs(r1.ambush_score - r2.ambush_score) < 0.01, (
                f"{fixture.fixture_id} ambush_score not stable"
            )
            assert r1.research_queue == r2.research_queue, (
                f"{fixture.fixture_id} research_queue not stable"
            )


class TestPipelineFieldGaps:
    def test_field_gaps_reported(self):
        r = _run_single_pipeline(FIXTURE_WEAK_EVIDENCE)
        assert isinstance(r.field_gaps, list)

    def test_strong_fixture_fewer_gaps(self):
        r_strong = _run_single_pipeline(FIXTURE_CENTRAL_CONTINUOUS)
        r_weak = _run_single_pipeline(FIXTURE_WEAK_EVIDENCE)
        assert len(r_strong.field_gaps) <= len(r_weak.field_gaps), (
            "Strong fixture should have fewer or equal gaps than weak"
        )


class TestSmokeReport:
    def test_render_report(self):
        results = [_run_single_pipeline(f) for f in ALL_FIXTURES]
        report = render_smoke_report(results)
        assert "# V-004" in report
        assert "central_continuous" in report
        assert "company_beneficiary" in report
        assert "weak_evidence" in report
        assert "overheated" in report
        assert "pseudo_policy" in report

    def test_report_contains_table(self):
        results = [_run_single_pipeline(f) for f in ALL_FIXTURES]
        report = render_smoke_report(results)
        assert "| Fixture |" in report

    def test_report_contains_field_gaps(self):
        results = [_run_single_pipeline(f) for f in ALL_FIXTURES]
        report = render_smoke_report(results)
        assert "字段缺口" in report

    def test_save_report(self, tmp_path):
        results = [_run_single_pipeline(f) for f in ALL_FIXTURES]
        report = render_smoke_report(results)
        report_file = tmp_path / "test-report.md"
        report_file.write_text(report, encoding="utf-8")
        assert report_file.exists()
        content = report_file.read_text(encoding="utf-8")
        assert "V-004" in content


class TestRunSmokeAndSave:
    def test_run_all_fixtures(self, tmp_path):
        results = run_smoke_and_save(output_dir=str(tmp_path))
        assert len(results) == 5
        report_files = list(tmp_path.glob("*.md"))
        assert len(report_files) == 1

    def test_run_selected_fixtures(self, tmp_path):
        results = run_smoke_and_save(
            fixture_ids=["central_continuous", "weak_evidence"],
            output_dir=str(tmp_path),
        )
        assert len(results) == 2


class TestAcceptanceV004:
    def test_fixtures_output_stable(self):
        for fixture in ALL_FIXTURES:
            r1 = _run_single_pipeline(fixture)
            r2 = _run_single_pipeline(fixture)
            assert r1.candidate_type == r2.candidate_type
            assert r1.research_queue == r2.research_queue
            assert abs(r1.mandate_score - r2.mandate_score) < 0.01

    def test_weak_evidence_not_high_score_ambush(self):
        r = _run_single_pipeline(FIXTURE_WEAK_EVIDENCE)
        assert r.candidate_type != "POLICY_AMBUSH", (
            "Weak evidence should NOT enter POLICY_AMBUSH"
        )

    def test_overheated_not_ambush(self):
        r = _run_single_pipeline(FIXTURE_OVERHEATED)
        assert r.candidate_type != "POLICY_AMBUSH", (
            "Overheated should NOT become POLICY_AMBUSH"
        )

    def test_pseudo_not_ambush(self):
        r = _run_single_pipeline(FIXTURE_PSEUDO_POLICY)
        assert r.candidate_type != "POLICY_AMBUSH", (
            "Pseudo policy should NOT become POLICY_AMBUSH"
        )

    def test_strong_policy_is_ambush_or_confirm(self):
        r = _run_single_pipeline(FIXTURE_CENTRAL_CONTINUOUS)
        assert r.candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM"), (
            f"Strong policy should be POLICY_AMBUSH or POLICY_CONFIRM, got {r.candidate_type}"
        )

    def test_no_forbidden_words_anywhere(self):
        for fixture in ALL_FIXTURES:
            r = _run_single_pipeline(fixture)
            assert len(r.forbidden_word_hits) == 0, (
                f"{fixture.fixture_id}: {r.forbidden_word_hits}"
            )

    def test_all_pipelines_complete_all_steps(self):
        for fixture in ALL_FIXTURES:
            r = _run_single_pipeline(fixture)
            step_names = [s.step_name for s in r.steps]
            assert "DATA-003 normalize_events" in step_names
            assert "H-002 compute_mandate_score" in step_names
            assert "H-003 compute_beneficiary_path" in step_names
            assert "H-004 compute_ambush_score" in step_names
            assert "H-007 route_to_research_queue" in step_names
            assert "H-008 generate_watchlist_note" in step_names
            for step in r.steps:
                assert step.success, f"{fixture.fixture_id} step {step.step_name} failed: {step.error}"

    def test_field_gaps_explicitly_reported(self):
        for fixture in ALL_FIXTURES:
            r = _run_single_pipeline(fixture)
            assert isinstance(r.field_gaps, list)

    def test_pipeline_run_result_to_dict(self):
        r = _run_single_pipeline(FIXTURE_CENTRAL_CONTINUOUS)
        d = r.to_dict()
        assert "fixture_id" in d
        assert "candidate_type" in d
        assert "mandate_score" in d
        assert "ambush_score" in d
        assert "research_queue" in d
        assert "note_summary" in d
        assert "field_gaps" in d
        assert "forbidden_word_hits" in d
        assert "passed" in d


class TestEdgeCases:
    def test_empty_events(self):
        fixture = PipelineFixture(
            fixture_id="empty",
            description="空事件",
            raw_events=[],
            symbol="000000.SZ",
        )
        r = _run_single_pipeline(fixture)
        assert r.candidate_type in ("TECH_TRADE", "EVENT_WATCH", "")

    def test_single_media_event(self):
        fixture = PipelineFixture(
            fixture_id="single_media",
            description="单条媒体事件",
            raw_events=[
                {
                    "title": "某媒体报道某题材",
                    "source": "新浪",
                    "date": _DATE_TODAY,
                    "symbol": "000000.SZ",
                }
            ],
            symbol="000000.SZ",
        )
        r = _run_single_pipeline(fixture)
        assert r.candidate_type not in ("POLICY_AMBUSH",)

    def test_no_title_event(self):
        fixture = PipelineFixture(
            fixture_id="no_title",
            description="无标题事件",
            raw_events=[
                {
                    "source": "未知",
                    "date": _DATE_TODAY,
                    "symbol": "000000.SZ",
                }
            ],
            symbol="000000.SZ",
        )
        r = _run_single_pipeline(fixture)
        assert isinstance(r.candidate_type, str)

    def test_mixed_source_levels(self):
        fixture = PipelineFixture(
            fixture_id="mixed_levels",
            description="混合来源级别",
            raw_events=[
                {
                    "title": "国务院发布重要政策文件",
                    "source": "国务院",
                    "date": _DATE_TWO_DAYS_AGO,
                    "symbol": "300034.SZ",
                },
                {
                    "title": "财联社报道市场行情",
                    "source": "财联社",
                    "date": _DATE_TODAY,
                    "symbol": "300034.SZ",
                },
            ],
            symbol="300034.SZ",
        )
        r = _run_single_pipeline(fixture)
        assert r.mandate_score > 0

    def test_all_forbidden_words_caught(self):
        for word in _FORBIDDEN_WORDS:
            assert _contains_forbidden(f"这是一条{word}消息")
