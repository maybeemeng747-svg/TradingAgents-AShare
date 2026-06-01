# [H-008] mandate_watchlist_note
"""Tests for watchlist note summary generation."""

import pytest

from tradingagents.tradeflow.mandate_watchlist_note import (
    WatchlistNoteResult,
    generate_watchlist_note,
    generate_watchlist_note_from_candidate,
    _compute_benefit_score,
    _compute_consensus_score,
    _compute_evidence_gap,
    _format_window,
    _sanitize_note,
    _WINDOW_MAP,
    _FORBIDDEN_NOTE_WORDS,
)
from tradingagents.tradeflow.schemas import Candidate


# ── WatchlistNoteResult ──


class TestWatchlistNoteResult:
    def test_default_values(self):
        r = WatchlistNoteResult()
        assert r.topic == ""
        assert r.benefit_score == 0.0
        assert r.consensus_score == 0.0
        assert r.expected_window == ""
        assert r.evidence_gap == []
        assert r.note_summary == ""
        assert r.has_evidence is False
        assert r.suggested_note == ""

    def test_custom_values(self):
        r = WatchlistNoteResult(
            topic="半导体",
            benefit_score=55.3,
            consensus_score=72.0,
            expected_window="中线",
            evidence_gap=["资金验证", "技术确认"],
            note_summary="半导体｜利好55.3｜共识72｜窗口中线｜缺口:资金验证/技术确认",
            has_evidence=True,
            suggested_note="半导体｜利好55.3｜共识72｜窗口中线｜缺口:资金验证/技术确认",
        )
        assert r.topic == "半导体"
        assert r.benefit_score == 55.3
        assert r.consensus_score == 72.0

    def test_to_dict(self):
        r = WatchlistNoteResult(
            topic="低空经济",
            benefit_score=33.3,
            consensus_score=44.4,
            expected_window="观察",
            evidence_gap=["政策证据"],
            note_summary="test",
            has_evidence=True,
            suggested_note="test",
        )
        d = r.to_dict()
        assert d["topic"] == "低空经济"
        assert d["benefit_score"] == 33.3
        assert d["consensus_score"] == 44.4
        assert d["expected_window"] == "观察"
        assert d["evidence_gap"] == ["政策证据"]
        assert d["has_evidence"] is True


# ── _compute_benefit_score ──


class TestComputeBenefitScore:
    def test_all_zero(self):
        assert _compute_benefit_score() == 0.0

    def test_mandate_only(self):
        s = _compute_benefit_score(mandate_score_component=80.0)
        assert s > 0
        assert s <= 100.0

    def test_beneficiary_only(self):
        s = _compute_benefit_score(beneficiary_score_component=60.0)
        assert s > 0

    def test_ambush_only(self):
        s = _compute_benefit_score(ambush_score=50.0)
        assert s > 0

    def test_all_combined(self):
        s = _compute_benefit_score(
            mandate_score_component=80.0,
            beneficiary_score_component=70.0,
            ambush_score=60.0,
        )
        assert s > 50.0

    def test_capped_at_100(self):
        s = _compute_benefit_score(
            mandate_score_component=100.0,
            beneficiary_score_component=100.0,
            ambush_score=100.0,
        )
        assert s <= 100.0

    def test_no_negative(self):
        s = _compute_benefit_score(mandate_score_component=-10.0)
        assert s >= 0.0


# ── _compute_consensus_score ──


class TestComputeConsensusScore:
    def test_all_zero(self):
        assert _compute_consensus_score() == 0.0

    def test_mandate_only(self):
        s = _compute_consensus_score(mandate_score_component=60.0)
        assert s > 0

    def test_narrative_only(self):
        s = _compute_consensus_score(narrative_score=40.0)
        assert s > 0

    def test_composite_only(self):
        s = _compute_consensus_score(composite_score=50.0)
        assert s > 0

    def test_resonance_only(self):
        s = _compute_consensus_score(resonance_count=3)
        assert s == 55.0

    def test_resonance_2(self):
        s = _compute_consensus_score(resonance_count=2)
        assert s == 40.0

    def test_resonance_1(self):
        s = _compute_consensus_score(resonance_count=1)
        assert s == 25.0

    def test_positive_category_boost(self):
        s1 = _compute_consensus_score(mandate_score_component=50.0, positive_category_count=2)
        s2 = _compute_consensus_score(mandate_score_component=50.0, positive_category_count=0)
        assert s1 > s2

    def test_resonance_boost(self):
        s1 = _compute_consensus_score(mandate_score_component=50.0, resonance_count=3)
        s2 = _compute_consensus_score(mandate_score_component=50.0, resonance_count=0)
        assert s1 > s2

    def test_capped_at_100(self):
        s = _compute_consensus_score(
            mandate_score_component=100.0,
            narrative_score=100.0,
            composite_score=100.0,
            positive_category_count=5,
            resonance_count=5,
        )
        assert s <= 100.0


# ── _compute_evidence_gap ──


class TestComputeEvidenceGap:
    def test_all_present(self):
        gaps = _compute_evidence_gap(
            has_policy_tags=True,
            has_beneficiary_path=True,
            has_company_role=True,
            has_fund_flow=True,
            has_narrative=True,
            has_tech_signal=True,
            data_completeness=0.9,
        )
        assert gaps == []

    def test_no_policy(self):
        gaps = _compute_evidence_gap(has_policy_tags=False)
        assert "政策证据" in gaps

    def test_no_beneficiary(self):
        gaps = _compute_evidence_gap(has_beneficiary_path=False)
        assert "受益路径" in gaps

    def test_no_company_role(self):
        gaps = _compute_evidence_gap(has_company_role=False)
        assert "公司定位" in gaps

    def test_no_fund_flow(self):
        gaps = _compute_evidence_gap(has_fund_flow=False)
        assert "资金验证" in gaps

    def test_no_narrative(self):
        gaps = _compute_evidence_gap(has_narrative=False)
        assert "叙事质量" in gaps

    def test_no_tech(self):
        gaps = _compute_evidence_gap(has_tech_signal=False)
        assert "技术确认" in gaps

    def test_low_completeness(self):
        gaps = _compute_evidence_gap(
            has_policy_tags=True,
            has_beneficiary_path=True,
            has_company_role=True,
            has_fund_flow=True,
            has_narrative=True,
            has_tech_signal=True,
            data_completeness=0.3,
        )
        assert "数据完整度低" in gaps

    def test_missing_evidence_appended(self):
        gaps = _compute_evidence_gap(missing_evidence=["订单数据", "龙虎榜"])
        assert len(gaps) >= 2

    def test_max_6_gaps(self):
        gaps = _compute_evidence_gap(
            has_policy_tags=False,
            has_beneficiary_path=False,
            has_company_role=False,
            has_fund_flow=False,
            has_narrative=False,
            has_tech_signal=False,
            data_completeness=0.1,
            missing_evidence=["x1", "x2"],
        )
        assert len(gaps) <= 6

    def test_default_no_args(self):
        gaps = _compute_evidence_gap()
        assert len(gaps) > 0


# ── _format_window ──


class TestFormatWindow:
    def test_default(self):
        assert _format_window() == "观察"

    def test_policy_ambush(self):
        assert _format_window("POLICY_AMBUSH") == "中线"

    def test_policy_confirm(self):
        assert _format_window("POLICY_CONFIRM") == "短线"

    def test_tech_trade(self):
        assert _format_window("TECH_TRADE") == "短线"

    def test_overheated(self):
        assert _format_window("OVERHEATED_AVOID") == "规避"

    def test_custom_window(self):
        assert _format_window("POLICY_AMBUSH", "1月") == "1月"

    def test_unknown_type(self):
        assert _format_window("UNKNOWN") == "观察"


# ── _sanitize_note ──


class TestSanitizeNote:
    def test_clean_text(self):
        assert _sanitize_note("半导体｜利好50") == "半导体｜利好50"

    def test_buy_word(self):
        assert "买入" not in _sanitize_note("看好买入")
        assert "***" in _sanitize_note("看好买入")

    def test_sell_word(self):
        assert "卖出" not in _sanitize_note("建议卖出")

    def test_multiple_forbidden(self):
        text = _sanitize_note("买入卖出清仓满仓")
        for w in _FORBIDDEN_NOTE_WORDS:
            assert w not in text


# ── generate_watchlist_note ──


class TestGenerateWatchlistNote:
    def test_all_empty(self):
        r = generate_watchlist_note()
        assert r.topic == "未定主题"
        assert r.has_evidence is False
        assert "缺证据" in r.note_summary

    def test_no_evidence_shows_gap(self):
        r = generate_watchlist_note()
        assert r.has_evidence is False
        assert "缺证据" in r.note_summary
        assert r.benefit_score == 0.0

    def test_policy_ambush_full(self):
        r = generate_watchlist_note(
            mandate_topic="半导体设备",
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=70.0,
            beneficiary_score_component=60.0,
            ambush_score=55.0,
            narrative_score=40.0,
            composite_score=50.0,
            positive_category_count=3,
            resonance_count=2,
            beneficiary_path=["芯片", "设备"],
            company_role="LEADER",
            policy_tags=["半导体"],
            strategy_tags=["VCP"],
            data_completeness=0.8,
        )
        assert r.topic == "半导体设备"
        assert r.has_evidence is True
        assert r.benefit_score > 0
        assert r.consensus_score > 0
        assert r.expected_window == "中线"
        assert "半导体设备" in r.note_summary
        assert "利好" in r.note_summary
        assert "共识" in r.note_summary
        assert "中线" in r.note_summary

    def test_tech_trade_no_policy(self):
        r = generate_watchlist_note(
            candidate_type="TECH_TRADE",
            strategy_tags=["VCP"],
            data_completeness=0.7,
        )
        assert r.topic == "技术形态"
        assert r.has_evidence is True
        assert r.expected_window == "短线"

    def test_overheated_avoid(self):
        r = generate_watchlist_note(
            candidate_type="OVERHEATED_AVOID",
        )
        assert r.topic == "过热规避"
        assert r.expected_window == "规避"

    def test_existing_note_preserved(self):
        r = generate_watchlist_note(
            existing_note="用户自定义备注",
            mandate_topic="低空经济",
            policy_tags=["低空经济"],
        )
        assert r.suggested_note == "用户自定义备注"
        assert "用户自定义备注" in r.suggested_note

    def test_no_existing_note_gets_summary(self):
        r = generate_watchlist_note(
            mandate_topic="低空经济",
            policy_tags=["低空经济"],
            mandate_score_component=50.0,
        )
        assert r.suggested_note == r.note_summary

    def test_benefit_score_format(self):
        r = generate_watchlist_note(
            mandate_topic="算力",
            mandate_score_component=80.0,
            beneficiary_score_component=80.0,
            ambush_score=80.0,
            policy_tags=["算力"],
            data_completeness=0.9,
        )
        assert "利好" in r.note_summary

    def test_consensus_in_summary(self):
        r = generate_watchlist_note(
            mandate_topic="机器人",
            mandate_score_component=60.0,
            policy_tags=["机器人"],
            data_completeness=0.9,
        )
        assert "共识" in r.note_summary

    def test_gap_in_summary(self):
        r = generate_watchlist_note(
            mandate_topic="AI应用",
            policy_tags=["AI应用"],
            mandate_score_component=50.0,
        )
        assert "缺口:" in r.note_summary

    def test_no_fake_scores_without_evidence(self):
        r = generate_watchlist_note()
        assert r.benefit_score == 0.0
        assert "缺证据" in r.note_summary

    def test_mandate_topic_from_policy_tags(self):
        r = generate_watchlist_note(policy_tags=["低空经济"])
        assert r.topic == "低空经济"

    def test_window_from_expected_window_override(self):
        r = generate_watchlist_note(
            candidate_type="POLICY_AMBUSH",
            expected_window="3月",
        )
        assert r.expected_window == "3月"


# ── Acceptance H-008 ──


class TestAcceptanceH008:
    def test_user_notes_preserved(self):
        r = generate_watchlist_note(existing_note="我的笔记", policy_tags=["半导体"])
        assert r.suggested_note == "我的笔记"

    def test_long_fields_compressed(self):
        r = generate_watchlist_note(
            mandate_topic="半导体",
            mandate_score_component=70.0,
            beneficiary_score_component=60.0,
            ambush_score=50.0,
            policy_tags=["半导体"],
            data_completeness=0.9,
        )
        assert len(r.note_summary) < 200

    def test_no_evidence_shows_gap_not_fake(self):
        r = generate_watchlist_note()
        assert r.has_evidence is False
        assert "缺证据" in r.note_summary
        assert r.benefit_score == 0.0

    def test_no_strong_words_in_summary(self):
        for w in _FORBIDDEN_NOTE_WORDS:
            r = generate_watchlist_note(
                mandate_topic=w + "题材",
                mandate_score_component=80.0,
                policy_tags=[w],
                data_completeness=0.9,
            )
            assert w not in r.note_summary

    def test_suggested_note_preserves_user_note(self):
        r = generate_watchlist_note(
            existing_note="长期持有观察",
            mandate_topic="算力",
            policy_tags=["算力"],
            mandate_score_component=50.0,
        )
        assert r.suggested_note == "长期持有观察"

    def test_summary_stable_format(self):
        r = generate_watchlist_note(
            mandate_topic="低空经济",
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=60.0,
            beneficiary_score_component=50.0,
            ambush_score=40.0,
            policy_tags=["低空经济"],
            beneficiary_path=["整机"],
            company_role="LEADER",
            data_completeness=0.8,
        )
        assert "低空经济" in r.note_summary
        assert "｜" in r.note_summary

    def test_watchlist_result_serializable(self):
        r = generate_watchlist_note(mandate_topic="军工", policy_tags=["军工"])
        d = r.to_dict()
        assert isinstance(d, dict)
        assert "topic" in d
        assert "benefit_score" in d
        assert "consensus_score" in d
        assert "note_summary" in d


# ── generate_watchlist_note_from_candidate ──


class TestGenerateWatchlistNoteFromCandidate:
    def test_basic_candidate(self):
        c = Candidate(
            symbol="002138.SZ",
            name="顺络电子",
            mandate_topic="半导体",
            candidate_type="POLICY_AMBUSH",
            mandate_score_component=60.0,
            beneficiary_score_component=50.0,
            ambush_score=40.0,
            policy_tags=["半导体"],
            beneficiary_path=["芯片设计"],
            company_role="LEADER",
            data_completeness=0.8,
        )
        r = generate_watchlist_note_from_candidate(c)
        assert r.topic == "半导体"
        assert r.has_evidence is True
        assert "半导体" in r.note_summary

    def test_empty_candidate(self):
        c = Candidate(symbol="000001.SZ")
        r = generate_watchlist_note_from_candidate(c)
        assert r.topic in ("未定主题", "技术形态", "过热规避")
        assert r.has_evidence is False

    def test_tech_trade_candidate(self):
        c = Candidate(
            symbol="600000.SH",
            candidate_type="TECH_TRADE",
            strategy_tags=["VCP"],
            data_completeness=0.7,
        )
        r = generate_watchlist_note_from_candidate(c)
        assert r.topic == "技术形态"
        assert r.expected_window == "短线"

    def test_candidate_with_narrative(self):
        c = Candidate(
            symbol="300001.SZ",
            mandate_topic="算力",
            narrative_score=50.0,
            composite_score=60.0,
            policy_tags=["算力"],
            data_completeness=0.9,
        )
        r = generate_watchlist_note_from_candidate(c)
        assert r.consensus_score > 0


# ── Integration with Schema ──


class TestH008SchemaIntegration:
    def test_candidate_has_watchlist_fields(self):
        c = Candidate(symbol="000001.SZ")
        assert hasattr(c, "watchlist_note")
        assert hasattr(c, "watchlist_note_suggested")
        assert hasattr(c, "watchlist_topic")
        assert hasattr(c, "watchlist_benefit_score")
        assert hasattr(c, "watchlist_consensus_score")
        assert hasattr(c, "watchlist_evidence_gap")
        assert c.watchlist_note == ""
        assert c.watchlist_note_suggested == ""
        assert c.watchlist_topic == ""
        assert c.watchlist_benefit_score == 0.0
        assert c.watchlist_consensus_score == 0.0
        assert c.watchlist_evidence_gap == []

    def test_candidate_to_db_row_has_fields(self):
        c = Candidate(symbol="000001.SZ")
        c.watchlist_note = "用户备注"
        c.watchlist_note_suggested = "建议备注"
        c.watchlist_topic = "半导体"
        c.watchlist_benefit_score = 55.0
        c.watchlist_consensus_score = 72.0
        c.watchlist_evidence_gap = ["资金验证"]
        row = c.to_db_row()
        assert row["watchlist_note"] == "用户备注"
        assert row["watchlist_note_suggested"] == "建议备注"
        assert row["watchlist_topic"] == "半导体"
        assert row["watchlist_benefit_score"] == 55.0
        assert row["watchlist_consensus_score"] == 72.0
        assert "资金验证" in row["watchlist_evidence_gap_json"]

    def test_candidate_from_db_row_has_fields(self):
        import json
        row = {
            "symbol": "000001.SZ",
            "watchlist_note": "test note",
            "watchlist_note_suggested": "suggested",
            "watchlist_topic": "AI",
            "watchlist_benefit_score": 33.0,
            "watchlist_consensus_score": 44.0,
            "watchlist_evidence_gap_json": json.dumps(["政策证据"]),
        }
        c = Candidate.from_db_row(row)
        assert c.watchlist_note == "test note"
        assert c.watchlist_note_suggested == "suggested"
        assert c.watchlist_topic == "AI"
        assert c.watchlist_benefit_score == 33.0
        assert c.watchlist_consensus_score == 44.0
        assert c.watchlist_evidence_gap == ["政策证据"]

    def test_db_row_roundtrip(self):
        c = Candidate(
            symbol="002138.SZ",
            watchlist_note="用户备注",
            watchlist_note_suggested="半导体｜利好50.0｜共识60｜窗口中线",
            watchlist_topic="半导体",
            watchlist_benefit_score=50.0,
            watchlist_consensus_score=60.0,
            watchlist_evidence_gap=["资金验证", "技术确认"],
        )
        row = c.to_db_row()
        c2 = Candidate.from_db_row(row)
        assert c2.watchlist_note == c.watchlist_note
        assert c2.watchlist_note_suggested == c.watchlist_note_suggested
        assert c2.watchlist_topic == c.watchlist_topic
        assert c2.watchlist_benefit_score == c.watchlist_benefit_score
        assert c2.watchlist_consensus_score == c.watchlist_consensus_score
        assert c2.watchlist_evidence_gap == c.watchlist_evidence_gap


# ── Edge Cases ──


class TestEdgeCases:
    def test_none_beneficiary_path(self):
        r = generate_watchlist_note(beneficiary_path=None)
        assert r.has_evidence is False

    def test_empty_policy_tags(self):
        r = generate_watchlist_note(policy_tags=[])
        assert r.has_evidence is False or r.topic in ("技术形态", "未定主题")

    def test_unknown_company_role(self):
        r = generate_watchlist_note(company_role="UNKNOWN")
        assert r.has_evidence is False

    def test_zero_scores_with_evidence(self):
        r = generate_watchlist_note(
            policy_tags=["半导体"],
            strategy_tags=["VCP"],
        )
        assert r.has_evidence is True
        assert r.benefit_score == 0.0

    def test_high_scores_all_evidence(self):
        r = generate_watchlist_note(
            mandate_topic="半导体",
            mandate_score_component=90.0,
            beneficiary_score_component=80.0,
            ambush_score=70.0,
            narrative_score=60.0,
            composite_score=70.0,
            positive_category_count=4,
            resonance_count=3,
            policy_tags=["半导体"],
            beneficiary_path=["芯片", "设备"],
            company_role="LEADER",
            fund_flow_anomaly_tags=["NET_INFLOW_DOMINANT"],
            strategy_tags=["VCP", "POLICY_VERSION"],
            data_completeness=0.95,
        )
        assert r.benefit_score > 50
        assert r.consensus_score > 50
        assert len(r.evidence_gap) == 0

    def test_only_risk_no_evidence(self):
        r = generate_watchlist_note(
            candidate_type="OVERHEATED_AVOID",
        )
        assert r.topic == "过热规避"
        assert r.expected_window == "规避"

    def test_empty_string_existing_note(self):
        r = generate_watchlist_note(
            existing_note="",
            mandate_topic="半导体",
            policy_tags=["半导体"],
            mandate_score_component=50.0,
        )
        assert r.suggested_note == r.note_summary

    def test_whitespace_existing_note(self):
        r = generate_watchlist_note(existing_note="   ")
        assert r.suggested_note == r.note_summary

    def test_benefit_score_rounding(self):
        s = _compute_benefit_score(
            mandate_score_component=33.33,
            beneficiary_score_component=66.66,
        )
        assert isinstance(s, float)
        assert s == round(s, 1)
