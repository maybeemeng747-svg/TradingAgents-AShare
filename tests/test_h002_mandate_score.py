# [H-002] mandate_policy_continuity
"""Tests for mandate_score module — policy continuity and source authority scoring."""

import pytest

from tradingagents.tradeflow.mandate_signal import MandateSignal, SourceLevel
from tradingagents.tradeflow.mandate_score import (
    MandateScoreResult,
    compute_mandate_score,
    compute_mandate_scores_by_topic,
    match_topics,
    _compute_source_authority_score,
    _compute_policy_continuity_score,
    _compute_topic_heat_delta,
    _deduplicate_signals,
    _collect_evidence_refs,
    _SOURCE_AUTHORITY_WEIGHTS,
    _MAX_SAME_SOURCE_COUNT,
    _SINGLE_DAY_PENALTY,
    _MAX_MANDATE_SCORE,
    _MAX_AUTHORITY_SCORE,
    _MAX_CONTINUITY_SCORE,
    _TOPIC_KEYWORDS_V0,
)


# ── Topic matching ──


class TestMatchTopics:
    def test_empty_text(self):
        assert match_topics("") == []

    def test_none_equivalent(self):
        assert match_topics("") == []

    def test_low_altitude_economy(self):
        topics = match_topics("国务院关于促进低空经济发展的意见")
        assert "低空经济" in topics

    def test_robot(self):
        topics = match_topics("工信部发布人形机器人产业发展规划")
        assert "机器人" in topics

    def test_compute_power(self):
        topics = match_topics("算力基础设施建设行动计划发布")
        assert "算力" in topics

    def test_semiconductor(self):
        topics = match_topics("国产半导体芯片自主可控迎来突破")
        assert "半导体" in topics
        assert "国产替代" in topics

    def test_military(self):
        topics = match_topics("国防军工行业深度报告")
        assert "军工" in topics

    def test_soe_reform(self):
        topics = match_topics("国资委推进央企混改")
        assert "国企改革" in topics

    def test_m_and_a(self):
        topics = match_topics("某公司发布重大资产重组方案")
        assert "并购重组" in topics

    def test_ai_application(self):
        topics = match_topics("生成式AI大模型应用加速")
        assert "AI应用" in topics

    def test_data_element(self):
        topics = match_topics("数据要素市场化配置改革方案")
        assert "数据要素" in topics

    def test_overseas(self):
        topics = match_topics("企业出海国际化布局加速")
        assert "出海" in topics

    def test_multiple_topics(self):
        topics = match_topics("低空经济机器人算力融合发展规划")
        assert "低空经济" in topics
        assert "机器人" in topics
        assert "算力" in topics

    def test_no_match(self):
        topics = match_topics("某公司发布年度报告")
        assert topics == []

    def test_zhongtegu(self):
        topics = match_topics("中特估央企估值重塑")
        assert "中特估" in topics

    def test_new_productive_forces(self):
        topics = match_topics("发展新质生产力推动高质量发展")
        assert "新质生产力" in topics

    def test_keyword_priority_longer_first(self):
        topics = match_topics("人工智能")
        assert "AI应用" in topics


class TestTopicKeywordsV0:
    def test_thirteen_topics(self):
        assert len(_TOPIC_KEYWORDS_V0) == 13

    def test_all_topics_have_keywords(self):
        for topic, keywords in _TOPIC_KEYWORDS_V0.items():
            assert len(keywords) > 0, f"{topic} has no keywords"

    def test_topics_covered(self):
        expected = {
            "新质生产力", "低空经济", "机器人", "算力", "军工",
            "半导体", "国产替代", "并购重组", "国企改革", "出海",
            "中特估", "AI应用", "数据要素",
        }
        assert set(_TOPIC_KEYWORDS_V0.keys()) == expected


# ── Source authority weights ──


class TestSourceAuthorityWeights:
    def test_hierarchy(self):
        order = [
            SourceLevel.CENTRAL,
            SourceLevel.STATE_COUNCIL,
            SourceLevel.MINISTRY,
            SourceLevel.EXCHANGE,
            SourceLevel.SOE_GROUP,
            SourceLevel.LOCAL_GOV,
            SourceLevel.COMPANY_NOTICE,
            SourceLevel.MEDIA,
        ]
        for i in range(len(order) - 1):
            assert _SOURCE_AUTHORITY_WEIGHTS[order[i]] > _SOURCE_AUTHORITY_WEIGHTS[order[i + 1]]

    def test_central_highest(self):
        assert _SOURCE_AUTHORITY_WEIGHTS[SourceLevel.CENTRAL] == 1.0

    def test_media_lowest(self):
        assert _SOURCE_AUTHORITY_WEIGHTS[SourceLevel.MEDIA] == 0.15

    def test_all_levels_have_weight(self):
        for sl in SourceLevel:
            assert sl in _SOURCE_AUTHORITY_WEIGHTS
            assert _SOURCE_AUTHORITY_WEIGHTS[sl] > 0


# ── Deduplication ──


class TestDeduplicateSignals:
    def test_no_duplicates(self):
        sigs = [
            MandateSignal(title="A", source="s1", date="2026-06-01"),
            MandateSignal(title="B", source="s2", date="2026-06-01"),
        ]
        result = _deduplicate_signals(sigs)
        assert len(result) == 2

    def test_exact_duplicates_removed(self):
        sigs = [
            MandateSignal(title="A", source="s1", date="2026-06-01"),
            MandateSignal(title="A", source="s1", date="2026-06-01"),
            MandateSignal(title="A", source="s1", date="2026-06-01"),
        ]
        result = _deduplicate_signals(sigs)
        assert len(result) == 1

    def test_same_title_different_source_kept(self):
        sigs = [
            MandateSignal(title="A", source="s1", date="2026-06-01"),
            MandateSignal(title="A", source="s2", date="2026-06-01"),
        ]
        result = _deduplicate_signals(sigs)
        assert len(result) == 2

    def test_same_title_different_date_kept(self):
        sigs = [
            MandateSignal(title="A", source="s1", date="2026-06-01"),
            MandateSignal(title="A", source="s1", date="2026-06-02"),
        ]
        result = _deduplicate_signals(sigs)
        assert len(result) == 2

    def test_empty_list(self):
        assert _deduplicate_signals([]) == []

    def test_whitespace_handling(self):
        sigs = [
            MandateSignal(title="  A  ", source="  s1  ", date="  2026-06-01  "),
            MandateSignal(title="A", source="s1", date="2026-06-01"),
        ]
        result = _deduplicate_signals(sigs)
        assert len(result) == 1


# ── Evidence refs collection ──


class TestCollectEvidenceRefs:
    def test_basic_ref(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="国务院关于促进低空经济发展的意见",
                source="gov.cn",
                date="2026-06-01",
                source_level="STATE_COUNCIL",
                confidence=0.8,
                evidence_url="http://example.com",
            ),
        ]
        refs = _collect_evidence_refs(sigs)
        assert len(refs) == 1
        assert refs[0]["title"] == "国务院关于促进低空经济发展的意见"
        assert refs[0]["source_level"] == "STATE_COUNCIL"
        assert refs[0]["evidence_url"] == "http://example.com"

    def test_duplicate_titles_deduped(self):
        sigs = [
            MandateSignal(title="A", source="s1", date="2026-06-01", confidence=0.5),
            MandateSignal(title="A", source="s1", date="2026-06-01", confidence=0.5),
        ]
        refs = _collect_evidence_refs(sigs)
        assert len(refs) == 1

    def test_empty_signals(self):
        assert _collect_evidence_refs([]) == []

    def test_raw_refs_included(self):
        sigs = [
            MandateSignal(
                title="A", source="s1", date="2026-06-01",
                raw_refs=[{"original_title": "A"}],
            ),
        ]
        refs = _collect_evidence_refs(sigs)
        assert refs[0]["raw_refs"] == [{"original_title": "A"}]


# ── Source authority score computation ──


class TestComputeSourceAuthorityScore:
    def _make_signal(self, source_level: str, title: str = "test", confidence: float = 0.8) -> MandateSignal:
        return MandateSignal(
            symbol="600519.SH",
            title=title,
            source="test",
            date="2026-06-01",
            source_level=source_level,
            confidence=confidence,
        )

    def test_single_central_signal(self):
        sigs = [self._make_signal("CENTRAL")]
        score, reasons = _compute_source_authority_score(sigs)
        assert score > 0
        assert any("CENTRAL" in r for r in reasons)

    def test_single_media_signal(self):
        sigs = [self._make_signal("MEDIA")]
        score, _ = _compute_source_authority_score(sigs)
        assert score > 0

    def test_central_higher_than_media(self):
        central = [self._make_signal("CENTRAL")]
        media = [self._make_signal("MEDIA")]
        c_score, _ = _compute_source_authority_score(central)
        m_score, _ = _compute_source_authority_score(media)
        assert c_score > m_score

    def test_empty_signals(self):
        score, reasons = _compute_source_authority_score([])
        assert score == 0.0

    def test_multiple_levels_higher_than_single(self):
        multi = [
            self._make_signal("STATE_COUNCIL"),
            self._make_signal("MINISTRY"),
            self._make_signal("LOCAL_GOV"),
        ]
        single = [self._make_signal("STATE_COUNCIL")]
        m_score, _ = _compute_source_authority_score(multi)
        s_score, _ = _compute_source_authority_score(single)
        assert m_score >= s_score

    def test_same_source_diminishing(self):
        base = self._make_signal("MINISTRY")
        three_same = [base] * 3
        three_unique = [
            self._make_signal("MINISTRY", title="t1"),
            self._make_signal("STATE_COUNCIL", title="t2"),
            self._make_signal("LOCAL_GOV", title="t3"),
        ]
        score_same, _ = _compute_source_authority_score(three_same)
        score_unique, _ = _compute_source_authority_score(three_unique)
        assert score_unique > score_same


# ── Policy continuity score computation ──


class TestComputePolicyContinuityScore:
    def _make_signal(self, date: str = "2026-06-01", source: str = "gov.cn",
                     source_level: str = "MINISTRY", event_type: str = "") -> MandateSignal:
        return MandateSignal(
            symbol="600519.SH",
            title="test",
            source=source,
            date=date,
            source_level=source_level,
            event_type=event_type,
        )

    def test_single_day_penalized(self):
        sigs = [self._make_signal(date="2026-06-01")]
        score, reasons = _compute_policy_continuity_score(sigs, "算力")
        assert "single_day_isolated_signal" in reasons
        assert score < 50

    def test_multi_day_higher(self):
        multi_day = [
            self._make_signal(date="2026-06-01"),
            self._make_signal(date="2026-06-02"),
            self._make_signal(date="2026-06-03"),
        ]
        single_day = [self._make_signal(date="2026-06-01")]
        m_score, _ = _compute_policy_continuity_score(multi_day, "算力")
        s_score, _ = _compute_policy_continuity_score(single_day, "算力")
        assert m_score > s_score

    def test_multi_source_bonus(self):
        multi_src = [
            self._make_signal(source="gov.cn"),
            self._make_signal(source="xinhua.net"),
            self._make_signal(source="people.cn"),
        ]
        single_src = [
            self._make_signal(source="gov.cn"),
            self._make_signal(source="gov.cn"),
        ]
        m_score, m_reasons = _compute_policy_continuity_score(multi_src, "算力")
        s_score, s_reasons = _compute_policy_continuity_score(single_src, "算力")
        assert any("multi_source" in r for r in m_reasons)
        assert any("single_source" in r for r in s_reasons)
        assert m_score >= s_score

    def test_policy_document_bonus(self):
        with_policy = [self._make_signal(event_type="POLICY_DOCUMENT")]
        without = [self._make_signal(event_type="BUYBACK_RATING")]
        wp_score, wp_reasons = _compute_policy_continuity_score(with_policy, "算力")
        wo_score, _ = _compute_policy_continuity_score(without, "算力")
        assert any("has_policy_or_meeting_signal" in r for r in wp_reasons)
        assert wp_score > wo_score

    def test_meeting_signal_bonus(self):
        with_meeting = [self._make_signal(event_type="MEETING_SIGNAL")]
        without = [self._make_signal(event_type="BUYBACK_RATING")]
        wm_score, _ = _compute_policy_continuity_score(with_meeting, "算力")
        wo_score, _ = _compute_policy_continuity_score(without, "算力")
        assert wm_score > wo_score

    def test_empty_signals(self):
        score, reasons = _compute_policy_continuity_score([], "")
        assert score == 0.0

    def test_multi_level_bonus(self):
        multi_level = [
            self._make_signal(source_level="STATE_COUNCIL"),
            self._make_signal(source_level="MINISTRY"),
            self._make_signal(source_level="LOCAL_GOV"),
        ]
        score, reasons = _compute_policy_continuity_score(multi_level, "低空经济")
        assert any("multi_level" in r for r in reasons)

    def test_score_capped_at_max(self):
        many_signals = []
        for day in range(1, 31):
            for src in range(1, 11):
                many_signals.append(self._make_signal(
                    date=f"2026-05-{day:02d}",
                    source=f"source_{src}",
                    source_level="MINISTRY",
                ))
        score, _ = _compute_policy_continuity_score(many_signals, "算力")
        assert score <= _MAX_CONTINUITY_SCORE


# ── Topic heat delta ──


class TestComputeTopicHeatDelta:
    def _make_signal(self, source_level: str = "STATE_COUNCIL", confidence: float = 0.8) -> MandateSignal:
        return MandateSignal(
            symbol="600519.SH",
            title="test",
            source="gov.cn",
            date="2026-06-01",
            source_level=source_level,
            confidence=confidence,
        )

    def test_no_current_signals(self):
        delta = _compute_topic_heat_delta([])
        assert delta == 0.0

    def test_current_no_historical(self):
        current = [self._make_signal()]
        delta = _compute_topic_heat_delta(current)
        assert delta > 0

    def test_current_higher_than_historical(self):
        current = [self._make_signal(source_level="STATE_COUNCIL", confidence=0.9)]
        historical = [self._make_signal(source_level="MEDIA", confidence=0.3)]
        delta = _compute_topic_heat_delta(current, historical)
        assert delta > 0

    def test_heat_capped_at_100(self):
        current = [self._make_signal(confidence=1.0)] * 20
        delta = _compute_topic_heat_delta(current)
        assert delta <= 100.0


# ── MandateScoreResult ──


class TestMandateScoreResult:
    def test_default_values(self):
        result = MandateScoreResult()
        assert result.mandate_score == 0.0
        assert result.policy_continuity_score == 0.0
        assert result.source_authority_score == 0.0
        assert result.topic_heat_delta == 0.0
        assert result.mandate_reasons == []
        assert result.mandate_evidence_refs == []
        assert result.is_noise is False

    def test_to_dict(self):
        result = MandateScoreResult(
            mandate_score=75.5,
            topic="低空经济",
            signal_count=5,
            is_noise=False,
        )
        d = result.to_dict()
        assert d["mandate_score"] == 75.5
        assert d["topic"] == "低空经济"
        assert d["signal_count"] == 5
        assert d["is_noise"] is False

    def test_to_dict_rounds_scores(self):
        result = MandateScoreResult(
            mandate_score=75.5678,
            source_authority_score=88.1234,
        )
        d = result.to_dict()
        assert d["mandate_score"] == 75.57
        assert d["source_authority_score"] == 88.12


# ── Main compute_mandate_score ──


class TestComputeMandateScore:
    def _make_signal(self, source_level: str = "STATE_COUNCIL",
                     date: str = "2026-06-01", source: str = "gov.cn",
                     title: str = "国务院关于促进低空经济发展的意见",
                     event_type: str = "POLICY_DOCUMENT",
                     confidence: float = 0.8,
                     topic: str = "低空经济") -> MandateSignal:
        return MandateSignal(
            symbol="600519.SH",
            title=title,
            source=source,
            date=date,
            source_level=source_level,
            event_type=event_type,
            confidence=confidence,
            topic=topic,
        )

    def test_no_signals(self):
        result = compute_mandate_score([])
        assert result.mandate_score == 0.0
        assert result.is_noise is True
        assert result.signal_count == 0
        assert "no_signals" in result.mandate_reasons

    def test_single_state_council_signal(self):
        sigs = [self._make_signal()]
        result = compute_mandate_score(sigs, topic="低空经济")
        assert result.topic == "低空经济"
        assert result.source_authority_score > 0
        assert result.has_high_authority is True
        assert result.has_policy_document is True
        assert result.signal_count == 1

    def test_multi_source_higher_than_single_company(self):
        multi_source = [
            self._make_signal(source_level="STATE_COUNCIL", source="gov.cn"),
            self._make_signal(source_level="MINISTRY", source="miit.gov.cn"),
            self._make_signal(source_level="LOCAL_GOV", source="sz.gov.cn", date="2026-06-02"),
        ]
        single_company = [
            MandateSignal(
                symbol="600036.SH",
                title="招商银行：董事会关于回购股份的公告",
                source="eastmoney",
                date="2026-06-01",
                source_level="COMPANY_NOTICE",
                event_type="BUYBACK_RATING",
                confidence=0.5,
            ),
        ]
        multi_result = compute_mandate_score(multi_source, topic="低空经济")
        single_result = compute_mandate_score(single_company)
        assert multi_result.mandate_score > single_result.mandate_score
        assert multi_result.has_high_authority is True
        assert single_result.has_high_authority is False

    def test_duplicate_same_source_no_infinite_stacking(self):
        many_duplicates = [
            self._make_signal(title="A", source="gov.cn") for _ in range(20)
        ]
        result_dup = compute_mandate_score(many_duplicates, topic="低空经济")

        few_unique = [
            self._make_signal(title="A", source="gov.cn", date="2026-06-01"),
            self._make_signal(title="B", source="xinhua.net", date="2026-06-02"),
            self._make_signal(title="C", source="people.cn", date="2026-06-03"),
        ]
        result_unique = compute_mandate_score(few_unique, topic="低空经济")
        assert result_dup.signal_count <= 2
        assert result_unique.mandate_score > 0

    def test_media_title_no_policy_low_confidence(self):
        media_only = [
            MandateSignal(
                symbol="600519.SH",
                title="东财：低空经济板块资金流入明显",
                source="eastmoney",
                date="2026-06-01",
                source_level="MEDIA",
                event_type="POLICY_DOCUMENT",
                confidence=0.3,
            ),
        ]
        result = compute_mandate_score(media_only, topic="低空经济")
        assert result.has_high_authority is False
        assert result.mandate_score < 50

    def test_result_has_evidence_refs(self):
        sigs = [
            self._make_signal(title="国务院关于低空经济的意见", source="gov.cn"),
            self._make_signal(title="工信部低空经济规划", source="miit.gov.cn", date="2026-06-02"),
        ]
        result = compute_mandate_score(sigs, topic="低空经济")
        assert len(result.mandate_evidence_refs) >= 1
        for ref in result.mandate_evidence_refs:
            assert "title" in ref
            assert "source" in ref
            assert "source_level" in ref

    def test_single_day_single_source_is_noise(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="某公司发布新产品",
                source="eastmoney",
                date="2026-06-01",
                source_level="MEDIA",
                confidence=0.2,
            ),
        ]
        result = compute_mandate_score(sigs)
        assert result.is_noise is True

    def test_multi_day_not_noise(self):
        sigs = [
            self._make_signal(date="2026-06-01"),
            self._make_signal(date="2026-06-02"),
        ]
        result = compute_mandate_score(sigs, topic="低空经济")
        assert result.is_noise is False

    def test_high_authority_not_noise(self):
        sigs = [self._make_signal(source_level="STATE_COUNCIL")]
        result = compute_mandate_score(sigs, topic="低空经济")
        assert result.has_high_authority is True

    def test_auto_topic_detection(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="国务院关于促进低空经济发展的意见",
                source="gov.cn",
                date="2026-06-01",
                source_level="STATE_COUNCIL",
                confidence=0.8,
            ),
        ]
        result = compute_mandate_score(sigs)
        assert result.topic == "低空经济"

    def test_mandate_score_capped_at_max(self):
        sigs = []
        for i in range(10):
            sigs.append(MandateSignal(
                symbol="600519.SH",
                title=f"国务院关于促进算力发展意见_{i}",
                source=f"gov.cn",
                date=f"2026-06-{i+1:02d}",
                source_level="STATE_COUNCIL",
                event_type="POLICY_DOCUMENT",
                confidence=0.9,
                topic="算力",
            ))
        result = compute_mandate_score(sigs, topic="算力")
        assert result.mandate_score <= _MAX_MANDATE_SCORE

    def test_historical_signals_heat_delta(self):
        current = [
            self._make_signal(confidence=0.9),
            self._make_signal(confidence=0.8, date="2026-06-02"),
        ]
        historical = [
            MandateSignal(
                symbol="600519.SH",
                title="旧闻", source="s", date="2026-05-01",
                source_level="MEDIA", confidence=0.2,
            ),
        ]
        result = compute_mandate_score(current, topic="低空经济", historical_signals=historical)
        assert result.topic_heat_delta >= 0


# ── Multi-topic scoring ──


class TestComputeMandateScoresByTopic:
    def _make_signal(self, topic: str = "低空经济", source_level: str = "STATE_COUNCIL",
                     title: str = "国务院关于促进低空经济发展的意见") -> MandateSignal:
        return MandateSignal(
            symbol="600519.SH",
            title=title,
            source="gov.cn",
            date="2026-06-01",
            source_level=source_level,
            confidence=0.8,
            topic=topic,
        )

    def test_separate_topics(self):
        sigs = [
            self._make_signal(topic="低空经济"),
            self._make_signal(topic="算力", title="算力基础设施规划"),
        ]
        results = compute_mandate_scores_by_topic(sigs)
        assert "低空经济" in results
        assert "算力" in results
        assert results["低空经济"].signal_count == 1
        assert results["算力"].signal_count == 1

    def test_auto_topic_from_title(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="国务院关于促进低空经济发展的意见",
                source="gov.cn",
                date="2026-06-01",
                source_level="STATE_COUNCIL",
                confidence=0.8,
            ),
        ]
        results = compute_mandate_scores_by_topic(sigs)
        assert "低空经济" in results

    def test_unassigned_signals(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="某公司发布新产品",
                source="eastmoney",
                date="2026-06-01",
                source_level="MEDIA",
                confidence=0.2,
            ),
        ]
        results = compute_mandate_scores_by_topic(sigs)
        assert "_unassigned" in results

    def test_empty_signals(self):
        results = compute_mandate_scores_by_topic([])
        assert results == {}

    def test_multi_signal_same_topic(self):
        sigs = [
            self._make_signal(topic="机器人", title="工信部机器人规划", source_level="MINISTRY"),
            self._make_signal(topic="机器人", title="机器人产业政策", source_level="STATE_COUNCIL"),
        ]
        results = compute_mandate_scores_by_topic(sigs)
        assert "机器人" in results
        assert results["机器人"].signal_count == 2

    def test_signal_can_match_multiple_topics(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="低空经济机器人融合发展规划",
                source="gov.cn",
                date="2026-06-01",
                source_level="STATE_COUNCIL",
                confidence=0.8,
            ),
        ]
        results = compute_mandate_scores_by_topic(sigs)
        assert "低空经济" in results
        assert "机器人" in results


# ── Validation: acceptance criteria from task ──


class TestH002Acceptance:
    def _make_signal(self, source_level: str = "STATE_COUNCIL",
                     date: str = "2026-06-01", source: str = "gov.cn",
                     title: str = "国务院关于促进低空经济发展的意见",
                     event_type: str = "POLICY_DOCUMENT",
                     confidence: float = 0.8) -> MandateSignal:
        return MandateSignal(
            symbol="600519.SH",
            title=title,
            source=source,
            date=date,
            source_level=source_level,
            event_type=event_type,
            confidence=confidence,
        )

    def test_multi_source_same_topic_higher_than_single_company(self):
        multi_source = [
            self._make_signal(source="gov.cn", source_level="STATE_COUNCIL"),
            self._make_signal(source="miit.gov.cn", source_level="MINISTRY", date="2026-06-02"),
            self._make_signal(source="xinhua.net", source_level="MEDIA", date="2026-06-03"),
        ]
        single_company = [
            self._make_signal(
                source="eastmoney",
                source_level="COMPANY_NOTICE",
                title="某公司：董事会关于回购股份的公告",
                event_type="BUYBACK_RATING",
                confidence=0.4,
            ),
        ]
        multi_result = compute_mandate_score(multi_source, topic="低空经济")
        single_result = compute_mandate_score(single_company)
        assert multi_result.mandate_score > single_result.mandate_score

    def test_same_source_duplicate_no_infinite_stacking(self):
        identical = [
            self._make_signal(title="同一标题同一来源", source="same_source") for _ in range(100)
        ]
        result_many = compute_mandate_score(identical, topic="低空经济")
        result_one = compute_mandate_score([self._make_signal()], topic="低空经济")
        assert result_many.signal_count <= 2
        assert result_many.mandate_score > 0
        assert result_many.mandate_score <= _MAX_MANDATE_SCORE

    def test_media_no_policy_no_high_confidence(self):
        media = [
            MandateSignal(
                symbol="600519.SH",
                title="东财：低空经济板块掀涨停潮",
                source="eastmoney",
                date="2026-06-01",
                source_level="MEDIA",
                confidence=0.15,
            ),
        ]
        result = compute_mandate_score(media, topic="低空经济")
        assert result.has_high_authority is False
        assert result.mandate_score < 40

    def test_result_contains_evidence_refs(self):
        sigs = [
            self._make_signal(
                title="国务院关于促进低空经济发展的意见",
                source="gov.cn",
            ),
        ]
        result = compute_mandate_score(sigs, topic="低空经济")
        assert len(result.mandate_evidence_refs) >= 1
        ref = result.mandate_evidence_refs[0]
        assert "title" in ref
        assert "source" in ref
        assert ref["title"] == "国务院关于促进低空经济发展的意见"

    def test_reasons_explain_why_direction(self):
        strong = [
            self._make_signal(source_level="STATE_COUNCIL", event_type="POLICY_DOCUMENT"),
            self._make_signal(source_level="MINISTRY", date="2026-06-02"),
        ]
        result = compute_mandate_score(strong, topic="低空经济")
        assert any("high_authority" in r for r in result.mandate_reasons)
        assert any("has_policy_document_evidence" in r for r in result.mandate_reasons)
        assert any("continuous" in r for r in result.mandate_reasons)

    def test_reasons_explain_why_noise(self):
        noise = [
            MandateSignal(
                symbol="600519.SH",
                title="某公司发布新产品",
                source="eastmoney",
                date="2026-06-01",
                source_level="MEDIA",
                confidence=0.15,
            ),
        ]
        result = compute_mandate_score(noise)
        assert result.is_noise is True
        assert any("noise" in r for r in result.mandate_reasons)


# ── Integration: H-001 signals → H-002 scoring ──


class TestH002Integration:
    def test_h001_signals_to_h002_scoring(self):
        from tradingagents.tradeflow.mandate_signal import event_item_to_mandate_signal
        from tradingagents.tradeflow.event_source import EventItem

        items = {
            "600519.SH": [
                EventItem(
                    symbol="600519.SH",
                    title="国务院关于促进低空经济发展的意见",
                    source="gov.cn",
                    date="2026-06-01",
                    direction="bullish",
                ),
                EventItem(
                    symbol="600519.SH",
                    title="工信部发布低空经济产业规划",
                    source="miit.gov.cn",
                    date="2026-06-02",
                    direction="bullish",
                ),
                EventItem(
                    symbol="600519.SH",
                    title="深圳市政府发布低空经济行动计划",
                    source="sz.gov.cn",
                    date="2026-06-03",
                    direction="bullish",
                ),
            ],
        }
        from tradingagents.tradeflow.mandate_signal import convert_event_items
        signals_map = convert_event_items(items)
        signals = signals_map.get("600519.SH", [])
        assert len(signals) == 3

        result = compute_mandate_score(signals, topic="低空经济")
        assert result.mandate_score > 0
        assert result.unique_dates >= 2
        assert result.has_high_authority is True
        assert result.has_policy_document is True
        assert result.is_noise is False

    def test_four_level_source_hierarchy(self):
        signals = [
            MandateSignal(
                symbol="600519.SH",
                title="中共中央关于低空经济的决定",
                source="xinhua",
                date="2026-06-01",
                source_level="CENTRAL",
                confidence=0.9,
            ),
            MandateSignal(
                symbol="600519.SH",
                title="工信部低空经济规划",
                source="miit.gov.cn",
                date="2026-06-02",
                source_level="MINISTRY",
                confidence=0.8,
            ),
            MandateSignal(
                symbol="600519.SH",
                title="深圳市低空经济行动计划",
                source="sz.gov.cn",
                date="2026-06-03",
                source_level="LOCAL_GOV",
                confidence=0.6,
            ),
            MandateSignal(
                symbol="600519.SH",
                title="某公司关于低空经济合作的公告",
                source="cninfo",
                date="2026-06-04",
                source_level="COMPANY_NOTICE",
                confidence=0.4,
            ),
        ]
        result = compute_mandate_score(signals, topic="低空经济")
        assert result.source_authority_score > 0
        assert result.unique_source_levels >= 3
        assert result.has_high_authority is True
        assert result.unique_dates >= 3

    def test_low_confidence_signals_capped(self):
        low_conf = [
            MandateSignal(
                symbol="600519.SH",
                title="",
                source="",
                date="2026-06-01",
                source_level="MEDIA",
                confidence=0.1,
            ),
        ]
        result = compute_mandate_score(low_conf, topic="低空经济")
        assert result.mandate_score < 50

    def test_policy_document_higher_than_buyback(self):
        policy = [
            MandateSignal(
                symbol="600519.SH",
                title="国务院关于算力发展的指导意见",
                source="gov.cn",
                date="2026-06-01",
                source_level="STATE_COUNCIL",
                event_type="POLICY_DOCUMENT",
                confidence=0.9,
            ),
        ]
        buyback = [
            MandateSignal(
                symbol="600519.SH",
                title="某公司公告回购计划",
                source="eastmoney",
                date="2026-06-01",
                source_level="COMPANY_NOTICE",
                event_type="BUYBACK_RATING",
                confidence=0.4,
            ),
        ]
        p_result = compute_mandate_score(policy, topic="算力")
        b_result = compute_mandate_score(buyback)
        assert p_result.mandate_score > b_result.mandate_score
        assert p_result.has_policy_document is True
        assert b_result.has_policy_document is False

    def test_continuity_across_five_days(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title=f"算力政策进展_{i}",
                source=f"source_{i % 3}",
                date=f"2026-06-{i+1:02d}",
                source_level=["STATE_COUNCIL", "MINISTRY", "LOCAL_GOV"][i % 3],
                confidence=0.8,
                topic="算力",
            )
            for i in range(5)
        ]
        result = compute_mandate_score(sigs, topic="算力")
        assert result.unique_dates == 5
        assert result.policy_continuity_score > 0
        assert result.is_noise is False

    def test_no_signals_returns_valid_result(self):
        result = compute_mandate_score([], topic="算力")
        assert result.mandate_score == 0.0
        assert result.topic == "算力"
        assert result.is_noise is True

    def test_dedup_with_historical_signals(self):
        current = [
            MandateSignal(
                symbol="600519.SH",
                title="算力新政策",
                source="gov.cn",
                date="2026-06-05",
                source_level="STATE_COUNCIL",
                confidence=0.9,
            ),
        ]
        historical = [
            MandateSignal(
                symbol="600519.SH",
                title="旧算力新闻",
                source="eastmoney",
                date="2026-05-01",
                source_level="MEDIA",
                confidence=0.2,
            ),
        ]
        result = compute_mandate_score(current, topic="算力", historical_signals=historical)
        assert result.topic_heat_delta > 0

    def test_all_topic_keywords_produce_score(self):
        for topic in _TOPIC_KEYWORDS_V0:
            keywords = _TOPIC_KEYWORDS_V0[topic]
            sig = MandateSignal(
                symbol="600519.SH",
                title=f"国务院关于{keywords[0]}的意见",
                source="gov.cn",
                date="2026-06-01",
                source_level="STATE_COUNCIL",
                confidence=0.8,
            )
            result = compute_mandate_score([sig], topic=topic)
            assert result.topic == topic
            assert result.mandate_score > 0, f"topic '{topic}' got 0 mandate_score"


# ── Edge cases ──


class TestEdgeCases:
    def test_all_signals_zero_confidence(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="test",
                source="s",
                date="2026-06-01",
                source_level="CENTRAL",
                confidence=0.0,
            ),
        ]
        result = compute_mandate_score(sigs, topic="算力")
        assert result.mandate_score == 0.0

    def test_signals_without_source_level(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="test",
                source="s",
                date="2026-06-01",
                confidence=0.5,
            ),
        ]
        result = compute_mandate_score(sigs)
        assert result.mandate_score >= 0

    def test_signals_with_empty_strings(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title="  ",
                source="  ",
                date="  ",
                confidence=0.0,
            ),
        ]
        result = compute_mandate_score(sigs)
        assert result.mandate_score >= 0

    def test_large_number_of_signals(self):
        sigs = [
            MandateSignal(
                symbol="600519.SH",
                title=f"算力政策_{i}",
                source=f"source_{i % 5}",
                date=f"2026-06-{(i % 10) + 1:02d}",
                source_level=["CENTRAL", "MINISTRY", "LOCAL_GOV", "COMPANY_NOTICE", "MEDIA"][i % 5],
                confidence=0.8,
                topic="算力",
            )
            for i in range(100)
        ]
        result = compute_mandate_score(sigs, topic="算力")
        assert result.mandate_score > 0
        assert result.mandate_score <= _MAX_MANDATE_SCORE
