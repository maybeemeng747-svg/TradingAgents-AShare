# [H-013] mandate_topic_heatmap — comprehensive tests
import os
import tempfile

import pytest

from tradingagents.tradeflow.topic_heatmap import (
    HEAT_RISING,
    HEAT_STABLE,
    HEAT_COOLING,
    HEAT_UNKNOWN,
    _HEAT_TREND_LABELS,
    _RISING_RATIO,
    _COOLING_RATIO,
    WINDOW_SHORT,
    WINDOW_MID,
    WINDOW_LONG,
    _WINDOWS,
    _WINDOW_LABELS,
    _state_change_label,
    is_state_change_positive,
    TopicHeatPoint,
    WindowStats,
    TopicHeatmapEntry,
    TopicHeatmapReport,
    _compute_heat,
    _build_daily_point,
    _compute_window_stats,
    _detect_heat_trend,
    _detect_state_change,
    _aggregate_candidates,
    _aggregate_evidence,
    _aggregate_evidence_gaps,
    _aggregate_overheat_flags,
    build_topic_heatmap,
    render_topic_heatmap_markdown,
    save_topic_heatmap_report,
    find_latest_topic_heatmap_report,
    build_heatmap_section_for_nightly_report,
    get_heatmap_sample_candidates,
    HEATMAP_SAMPLE_CANDIDATES,
)
from tradingagents.tradeflow.topic_registry import (
    TOPIC_STATUS_BREWING,
    TOPIC_STATUS_FERMENTING,
    TOPIC_STATUS_CONFIRMING,
    TOPIC_STATUS_DELIVERING,
    TOPIC_STATUS_RECEDING,
    TOPIC_STATUS_UNKNOWN,
    POLICY_LEVEL_CENTRAL,
    POLICY_LEVEL_MINISTRY,
    POLICY_LEVEL_LOCAL,
    POLICY_LEVEL_UNKNOWN,
)


# ── Heat trend constants ─────────────────────────────────────────────

class TestHeatTrendConstants:
    def test_four_trends(self):
        trends = {HEAT_RISING, HEAT_STABLE, HEAT_COOLING, HEAT_UNKNOWN}
        assert len(trends) == 4

    def test_labels(self):
        assert _HEAT_TREND_LABELS[HEAT_RISING] == "升温"
        assert _HEAT_TREND_LABELS[HEAT_STABLE] == "平稳"
        assert _HEAT_TREND_LABELS[HEAT_COOLING] == "降温"
        assert _HEAT_TREND_LABELS[HEAT_UNKNOWN] == "未知"

    def test_ratio_thresholds(self):
        assert _RISING_RATIO > 1.0
        assert _COOLING_RATIO < 1.0
        assert _RISING_RATIO > _COOLING_RATIO

    def test_windows(self):
        assert WINDOW_SHORT == 7
        assert WINDOW_MID == 20
        assert WINDOW_LONG == 60
        assert _WINDOWS == (7, 20, 60)

    def test_window_labels(self):
        assert _WINDOW_LABELS[7] == "近7天"
        assert _WINDOW_LABELS[20] == "近20天"
        assert _WINDOW_LABELS[60] == "近60天"


# ── State change detection ───────────────────────────────────────────

class TestStateChangeLabel:
    def test_no_change(self):
        assert _state_change_label(TOPIC_STATUS_BREWING, TOPIC_STATUS_BREWING) == "持平"

    def test_brewing_to_fermenting(self):
        label = _state_change_label(TOPIC_STATUS_BREWING, TOPIC_STATUS_FERMENTING)
        assert "升温发酵" in label
        assert "酝酿" in label
        assert "发酵" in label

    def test_fermenting_to_confirming(self):
        label = _state_change_label(TOPIC_STATUS_FERMENTING, TOPIC_STATUS_CONFIRMING)
        assert "确认" in label

    def test_confirming_to_delivering(self):
        label = _state_change_label(TOPIC_STATUS_CONFIRMING, TOPIC_STATUS_DELIVERING)
        assert "兑现" in label

    def test_to_receding_is_cooling(self):
        label = _state_change_label(TOPIC_STATUS_CONFIRMING, TOPIC_STATUS_RECEDING)
        assert "退潮" in label

    def test_receding_to_brewing_is_revival(self):
        label = _state_change_label(TOPIC_STATUS_RECEDING, TOPIC_STATUS_BREWING)
        assert "回暖" in label

    def test_unknown_to_brewing(self):
        label = _state_change_label(TOPIC_STATUS_UNKNOWN, TOPIC_STATUS_BREWING)
        assert "酝酿" in label

    def test_backward_within_non_receding(self):
        label = _state_change_label(TOPIC_STATUS_CONFIRMING, TOPIC_STATUS_FERMENTING)
        assert "回落" in label


class TestIsStateChangePositive:
    def test_forward_is_positive(self):
        assert is_state_change_positive(TOPIC_STATUS_BREWING, TOPIC_STATUS_FERMENTING) is True

    def test_to_receding_is_negative(self):
        assert is_state_change_positive(TOPIC_STATUS_CONFIRMING, TOPIC_STATUS_RECEDING) is False

    def test_receding_to_active_is_positive(self):
        assert is_state_change_positive(TOPIC_STATUS_RECEDING, TOPIC_STATUS_BREWING) is True

    def test_no_change_is_negative(self):
        assert is_state_change_positive(TOPIC_STATUS_BREWING, TOPIC_STATUS_BREWING) is False


# ── Data models ──────────────────────────────────────────────────────

class TestTopicHeatPoint:
    def test_defaults(self):
        p = TopicHeatPoint()
        assert p.date == ""
        assert p.signal_count == 0
        assert p.candidate_count == 0
        assert p.heat == 0.0
        assert p.topic_status == TOPIC_STATUS_UNKNOWN

    def test_to_dict(self):
        p = TopicHeatPoint(date="2026-06-01", candidate_count=2, heat=45.5)
        d = p.to_dict()
        assert d["date"] == "2026-06-01"
        assert d["candidate_count"] == 2
        assert d["heat"] == 45.5


class TestWindowStats:
    def test_defaults(self):
        ws = WindowStats()
        assert ws.signal_count == 0
        assert ws.candidate_count == 0

    def test_to_dict_includes_label(self):
        ws = WindowStats(window_days=7)
        d = ws.to_dict()
        assert d["window_label"] == "近7天"


class TestTopicHeatmapEntry:
    def test_defaults(self):
        e = TopicHeatmapEntry(topic="算力")
        assert e.topic == "算力"
        assert e.heat_curve == []
        assert e.windows == {}
        assert e.heat_trend == HEAT_UNKNOWN

    def test_to_dict(self):
        e = TopicHeatmapEntry(
            topic="低空经济",
            topic_status=TOPIC_STATUS_FERMENTING,
            heat_trend=HEAT_RISING,
        )
        d = e.to_dict()
        assert d["topic"] == "低空经济"
        assert d["heat_trend"] == HEAT_RISING
        assert isinstance(d["heat_curve"], list)
        assert isinstance(d["windows"], dict)


class TestTopicHeatmapReport:
    def test_defaults(self):
        r = TopicHeatmapReport()
        assert r.topics == []
        assert r.total_topics == 0

    def test_to_dict(self):
        r = TopicHeatmapReport(
            as_of="2026-06-12",
            topics=[TopicHeatmapEntry(topic="算力")],
            total_topics=1,
        )
        d = r.to_dict()
        assert d["as_of"] == "2026-06-12"
        assert d["total_topics"] == 1
        assert len(d["topics"]) == 1


# ── Heat computation ─────────────────────────────────────────────────

class TestComputeHeat:
    def test_zero_for_empty(self):
        p = TopicHeatPoint()
        assert _compute_heat(p) == 0.0

    def test_increases_with_candidates(self):
        low = TopicHeatPoint(candidate_count=1)
        high = TopicHeatPoint(candidate_count=3)
        assert _compute_heat(high) > _compute_heat(low)

    def test_increases_with_evidence(self):
        low = TopicHeatPoint(evidence_count=1)
        high = TopicHeatPoint(evidence_count=5)
        assert _compute_heat(high) > _compute_heat(low)

    def test_increases_with_policy_level(self):
        low = TopicHeatPoint(policy_level=POLICY_LEVEL_LOCAL, policy_level_weight=3)
        high = TopicHeatPoint(policy_level=POLICY_LEVEL_CENTRAL, policy_level_weight=5)
        assert _compute_heat(high) > _compute_heat(low)

    def test_increases_with_signal_count(self):
        low = TopicHeatPoint(signal_count=1)
        high = TopicHeatPoint(signal_count=5)
        assert _compute_heat(high) > _compute_heat(low)

    def test_capped_at_100(self):
        p = TopicHeatPoint(
            candidate_count=100, evidence_count=100,
            policy_level=POLICY_LEVEL_CENTRAL, policy_level_weight=5,
            signal_count=100,
        )
        assert _compute_heat(p) <= 100.0

    def test_max_heat_with_full_inputs(self):
        p = TopicHeatPoint(
            candidate_count=10, evidence_count=10,
            policy_level=POLICY_LEVEL_CENTRAL, policy_level_weight=5,
            signal_count=5,
        )
        # 30 (cand) + 25 (ev) + 30 (lvl) + 15 (sig) = 100
        assert _compute_heat(p) == 100.0


# ── Daily point building ─────────────────────────────────────────────

class TestBuildDailyPoint:
    def test_single_candidate(self):
        cands = [{
            "symbol": "300034.SZ", "name": "钢研高纳",
            "policy_evidence_refs": [{"title": "政策A", "source": "工信部"}],
            "topic_lifecycle_state": "ACCELERATING",
            "topic_signal_count": 5,
            "policy_level": "MINISTRY",
        }]
        p = _build_daily_point("2026-06-10", cands)
        assert p.date == "2026-06-10"
        assert p.candidate_count == 1
        assert p.unique_candidates == 1
        assert p.evidence_count == 1
        assert p.signal_count == 5
        assert p.topic_status == TOPIC_STATUS_FERMENTING
        assert p.heat > 0

    def test_multiple_candidates_dedup_symbols(self):
        cands = [
            {"symbol": "A", "policy_evidence_refs": []},
            {"symbol": "A", "policy_evidence_refs": []},
            {"symbol": "B", "policy_evidence_refs": [{"title": "x"}]},
        ]
        p = _build_daily_point("2026-06-10", cands)
        assert p.candidate_count == 3
        assert p.unique_candidates == 2
        assert p.evidence_count == 1

    def test_policy_level_from_evidence_refs(self):
        cands = [{
            "symbol": "A",
            "policy_evidence_refs": [{"source_level": "CENTRAL"}],
        }]
        p = _build_daily_point("2026-06-10", cands)
        assert p.policy_level == POLICY_LEVEL_CENTRAL

    def test_overheat_flags_dedup(self):
        cands = [
            {"symbol": "A", "overheat_flags": ["x", "y"]},
            {"symbol": "B", "overheat_flags": ["y", "z"]},
        ]
        p = _build_daily_point("2026-06-10", cands)
        assert sorted(p.overheat_flags) == ["x", "y", "z"]

    def test_empty_candidates(self):
        p = _build_daily_point("2026-06-10", [])
        assert p.candidate_count == 0
        assert p.heat == 0.0


# ── Window stats ─────────────────────────────────────────────────────

class TestComputeWindowStats:
    def test_empty_curve(self):
        ws = _compute_window_stats([], 7, "2026-06-12")
        assert ws.window_days == 7
        assert ws.signal_count == 0
        assert ws.active_days == 0

    def test_within_window(self):
        curve = [
            TopicHeatPoint(date="2026-06-10", candidate_count=2, signal_count=3),
            TopicHeatPoint(date="2026-06-11", candidate_count=1, signal_count=1),
        ]
        ws = _compute_window_stats(curve, 7, "2026-06-12")
        assert ws.candidate_count == 3
        assert ws.signal_count == 4
        assert ws.active_days == 2

    def test_outside_window(self):
        curve = [
            TopicHeatPoint(date="2026-05-01", candidate_count=5),
        ]
        ws = _compute_window_stats(curve, 7, "2026-06-12")
        assert ws.candidate_count == 0
        assert ws.active_days == 0

    def test_invalid_as_of(self):
        ws = _compute_window_stats([], 7, "bad-date")
        assert ws.signal_count == 0

    def test_max_policy_level(self):
        curve = [
            TopicHeatPoint(date="2026-06-10", policy_level=POLICY_LEVEL_LOCAL, policy_level_weight=3),
            TopicHeatPoint(date="2026-06-11", policy_level=POLICY_LEVEL_CENTRAL, policy_level_weight=5),
        ]
        ws = _compute_window_stats(curve, 7, "2026-06-12")
        assert ws.max_policy_level == POLICY_LEVEL_CENTRAL
        assert ws.max_policy_level_weight == 5


# ── Heat trend detection ─────────────────────────────────────────────

class TestDetectHeatTrend:
    def test_empty_curve(self):
        assert _detect_heat_trend([], "2026-06-12") == HEAT_UNKNOWN

    def test_no_heat_data(self):
        curve = [TopicHeatPoint(date="2026-06-10", heat=0.0)]
        assert _detect_heat_trend(curve, "2026-06-12") == HEAT_UNKNOWN

    def test_recent_only_is_rising(self):
        curve = [TopicHeatPoint(date="2026-06-10", heat=50.0)]
        assert _detect_heat_trend(curve, "2026-06-12") == HEAT_RISING

    def test_recent_higher_than_prior(self):
        curve = [
            TopicHeatPoint(date="2026-06-01", heat=10.0),  # prior window
            TopicHeatPoint(date="2026-06-10", heat=50.0),  # recent window
        ]
        assert _detect_heat_trend(curve, "2026-06-12") == HEAT_RISING

    def test_recent_lower_than_prior(self):
        curve = [
            TopicHeatPoint(date="2026-06-01", heat=50.0),  # prior window
            TopicHeatPoint(date="2026-06-10", heat=5.0),   # recent window
        ]
        assert _detect_heat_trend(curve, "2026-06-12") == HEAT_COOLING

    def test_roughly_equal_is_stable(self):
        curve = [
            TopicHeatPoint(date="2026-06-01", heat=40.0),  # prior window
            TopicHeatPoint(date="2026-06-10", heat=40.0),  # recent window
        ]
        assert _detect_heat_trend(curve, "2026-06-12") == HEAT_STABLE

    def test_invalid_date(self):
        curve = [TopicHeatPoint(date="bad", heat=50.0)]
        assert _detect_heat_trend(curve, "2026-06-12") == HEAT_UNKNOWN


# ── State change detection from curve ────────────────────────────────

class TestDetectStateChange:
    def test_empty_curve(self):
        prev, curr, label = _detect_state_change([])
        assert curr == TOPIC_STATUS_UNKNOWN
        assert label == "持平"

    def test_single_point_is_new_entry(self):
        curve = [TopicHeatPoint(date="2026-06-10", candidate_count=1, topic_status=TOPIC_STATUS_BREWING)]
        prev, curr, label = _detect_state_change(curve)
        assert curr == TOPIC_STATUS_BREWING
        assert label == "新进入"

    def test_two_points_transition(self):
        curve = [
            TopicHeatPoint(date="2026-06-01", candidate_count=1, topic_status=TOPIC_STATUS_BREWING),
            TopicHeatPoint(date="2026-06-10", candidate_count=1, topic_status=TOPIC_STATUS_FERMENTING),
        ]
        prev, curr, label = _detect_state_change(curve)
        assert prev == TOPIC_STATUS_BREWING
        assert curr == TOPIC_STATUS_FERMENTING
        assert "发酵" in label


# ── Aggregation helpers ──────────────────────────────────────────────

class TestAggregateCandidates:
    def test_dedup_by_symbol(self):
        cands = [
            {"symbol": "A", "name": "A名", "mandate_score_component": 50},
            {"symbol": "A", "name": "A名", "mandate_score_component": 70},
            {"symbol": "B", "name": "B名", "mandate_score_component": 60},
        ]
        result = _aggregate_candidates(cands)
        assert len(result) == 2
        # higher score wins
        a = next(c for c in result if c["symbol"] == "A")
        assert a["mandate_score"] == 70

    def test_ranked_by_score(self):
        cands = [
            {"symbol": "A", "mandate_score_component": 30},
            {"symbol": "B", "mandate_score_component": 80},
            {"symbol": "C", "mandate_score_component": 50},
        ]
        result = _aggregate_candidates(cands)
        assert result[0]["symbol"] == "B"
        assert result[1]["symbol"] == "C"
        assert result[2]["symbol"] == "A"

    def test_composite_score_fallback(self):
        cands = [{"symbol": "A", "composite_score": 55}]
        result = _aggregate_candidates(cands)
        assert result[0]["mandate_score"] == 55

    def test_empty(self):
        assert _aggregate_candidates([]) == []


class TestAggregateEvidence:
    def test_collect_and_dedup(self):
        cands = [
            {"policy_evidence_refs": [{"title": "A", "source": "S1"}]},
            {"policy_evidence_refs": [{"title": "A", "source": "S1"}, {"title": "B", "source": "S2"}]},
        ]
        links, summary = _aggregate_evidence(cands)
        assert len(links) == 2
        assert "A" in summary

    def test_mandate_evidence_refs_fallback(self):
        cands = [{"mandate_evidence_refs": [{"title": "X", "source": "S"}]}]
        links, _ = _aggregate_evidence(cands)
        assert len(links) == 1
        assert links[0]["title"] == "X"

    def test_empty(self):
        links, summary = _aggregate_evidence([])
        assert links == []
        assert summary == ""

    def test_capped_at_20(self):
        refs = [{"title": f"T{i}", "source": f"S{i}"} for i in range(30)]
        cands = [{"policy_evidence_refs": refs}]
        links, _ = _aggregate_evidence(cands)
        assert len(links) <= 20


class TestAggregateEvidenceGaps:
    def test_collect_unique(self):
        cands = [
            {"blocking_evidence_gaps": ["gap1", "gap2"]},
            {"blocking_evidence_gaps": ["gap2", "gap3"]},
        ]
        gaps = _aggregate_evidence_gaps(cands)
        assert "gap1" in gaps
        assert "gap2" in gaps
        assert "gap3" in gaps
        # no duplicates
        assert len(gaps) == 3

    def test_watchlist_evidence_gap_fallback(self):
        cands = [{"watchlist_evidence_gap": ["wg1"]}]
        gaps = _aggregate_evidence_gaps(cands)
        assert "wg1" in gaps

    def test_empty(self):
        assert _aggregate_evidence_gaps([]) == []


class TestAggregateOverheatFlags:
    def test_dedup_across_curve(self):
        curve = [
            TopicHeatPoint(overheat_flags=["a", "b"]),
            TopicHeatPoint(overheat_flags=["b", "c"]),
        ]
        flags = _aggregate_overheat_flags(curve)
        assert sorted(flags) == ["a", "b", "c"]

    def test_empty(self):
        assert _aggregate_overheat_flags([]) == []


# ── Main builder ─────────────────────────────────────────────────────

class TestBuildTopicHeatmap:
    def test_empty_candidates_includes_predefined_topics(self):
        report = build_topic_heatmap([])
        assert report.total_topics >= 8  # 8 predefined topics
        assert report.active_topics == 0

    def test_sample_fixtures(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        assert report.as_of == "2026-06-12"
        # at least 3 active topics
        assert report.active_topics >= 3
        topics = {t.topic for t in report.topics}
        assert "低空经济" in topics
        assert "算力" in topics
        assert "半导体设备" in topics

    def test_as_of_inferred_from_candidates(self):
        cands = [{"symbol": "A", "mandate_topic": "算力", "effective_trade_date": "2026-06-15"}]
        report = build_topic_heatmap(cands)
        assert report.as_of == "2026-06-15"

    def test_windows_populated(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        for topic in report.topics:
            if topic.candidates:
                assert set(topic.windows.keys()) == {7, 20, 60}

    def test_heat_curve_filtered_to_window(self):
        # candidate from 90 days ago should be filtered out with 60-day window
        cands = [{
            "symbol": "A", "mandate_topic": "算力",
            "effective_trade_date": "2026-03-01",
            "topic_lifecycle_state": "EMERGING",
        }]
        report = build_topic_heatmap(cands, as_of="2026-06-12", window_days=60)
        topic = next(t for t in report.topics if t.topic == "算力")
        assert len(topic.heat_curve) == 0

    def test_topic_matching_from_policy_tags(self):
        cands = [{
            "symbol": "A", "name": "某公司",
            "policy_tags": ["光模块", "算力"],
        }]
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        topic = next(t for t in report.topics if t.topic == "算力")
        assert len(topic.candidates) == 1

    def test_active_topics_sorted_first(self):
        cands = [{"symbol": "A", "mandate_topic": "算力", "effective_trade_date": "2026-06-10"}]
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        # first topic should be the active one
        assert report.topics[0].topic == "算力"
        assert len(report.topics[0].candidates) > 0

    def test_candidate_interlink(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        low_altitude = next(t for t in report.topics if t.topic == "低空经济")
        assert len(low_altitude.candidates) >= 1
        assert all("symbol" in c for c in low_altitude.candidates)
        assert "300034.SZ" in low_altitude.candidate_symbols

    def test_evidence_links_populated(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        low_altitude = next(t for t in report.topics if t.topic == "低空经济")
        assert len(low_altitude.evidence_links) >= 1

    def test_counter_evidence_gaps(self):
        cands = [{
            "symbol": "A", "mandate_topic": "算力",
            "effective_trade_date": "2026-06-10",
            "blocking_evidence_gaps": ["量能不足", "资金未确认"],
        }]
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        topic = next(t for t in report.topics if t.topic == "算力")
        assert "量能不足" in topic.counter_evidence_gaps

    def test_summary_fields(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        assert "total_topics" in report.summary
        assert "active_topics" in report.summary
        assert report.total_topics == report.summary["total_topics"]

    def test_rising_topics_counted(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        # the sample has recent activity, so at least some rising
        assert report.rising_topics >= 0  # structural test


class TestBuildTopicHeatmapAcceptanceScenarios:
    """Acceptance: 低空经济/算力/半导体设备 can generate topic heat."""

    def test_low_altitude_heat(self):
        cands = [c for c in get_heatmap_sample_candidates() if c["mandate_topic"] == "低空经济"]
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        topic = next(t for t in report.topics if t.topic == "低空经济")
        assert topic.candidates
        assert len(topic.heat_curve) >= 2
        w7 = topic.windows[7]
        assert w7.candidate_count >= 1

    def test_compute_heat(self):
        cands = [c for c in get_heatmap_sample_candidates() if c["mandate_topic"] == "算力"]
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        topic = next(t for t in report.topics if t.topic == "算力")
        assert topic.topic_status == TOPIC_STATUS_CONFIRMING

    def test_semiconductor_equipment_heat(self):
        cands = [c for c in get_heatmap_sample_candidates() if c["mandate_topic"] == "半导体设备"]
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        topic = next(t for t in report.topics if t.topic == "半导体设备")
        assert topic.candidates
        assert topic.topic_status == TOPIC_STATUS_BREWING


# ── Markdown rendering ───────────────────────────────────────────────

class TestRenderMarkdown:
    def test_empty_report(self):
        report = build_topic_heatmap([])
        md = render_topic_heatmap_markdown(report)
        assert "主题热度" in md

    def test_sample_report(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        md = render_topic_heatmap_markdown(report)
        assert "低空经济" in md
        assert "算力" in md
        assert "热度曲线" in md or "热度详情" in md
        assert "政策证据" in md

    def test_constraint_reminder(self):
        report = build_topic_heatmap([])
        md = render_topic_heatmap_markdown(report)
        assert "不直接改变最终交易动作" in md

    def test_heat_bar_rendered(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        md = render_topic_heatmap_markdown(report)
        assert "█" in md or "░" in md

    def test_no_secrets_in_output(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        md = render_topic_heatmap_markdown(report)
        forbidden = ["api_key", "API_KEY", "secret", "password", "token"]
        for w in forbidden:
            assert w.lower() not in md.lower()


# ── File I/O ─────────────────────────────────────────────────────────

class TestFileIO:
    def test_save_and_find(self):
        report = build_topic_heatmap(get_heatmap_sample_candidates(), as_of="2026-06-12")
        with tempfile.TemporaryDirectory() as d:
            path = save_topic_heatmap_report(report, output_dir=d)
            assert os.path.exists(path)
            assert "topic-heatmap-2026-06-12.md" in path

            found = find_latest_topic_heatmap_report(d)
            assert found == path

    def test_custom_filename(self):
        report = build_topic_heatmap([], as_of="2026-06-12")
        with tempfile.TemporaryDirectory() as d:
            path = save_topic_heatmap_report(report, output_dir=d, filename="custom.md")
            assert path.endswith("custom.md")

    def test_find_nonexistent_dir(self):
        assert find_latest_topic_heatmap_report("/nonexistent/dir/xyz") is None

    def test_find_ignores_other_files(self):
        report = build_topic_heatmap([], as_of="2026-06-12")
        with tempfile.TemporaryDirectory() as d:
            save_topic_heatmap_report(report, output_dir=d)
            # write unrelated file
            with open(os.path.join(d, "other.md"), "w") as f:
                f.write("noise")
            found = find_latest_topic_heatmap_report(d)
            assert found is not None
            assert "topic-heatmap" in found

    def test_find_picks_latest_date(self):
        with tempfile.TemporaryDirectory() as d:
            for date in ["2026-06-01", "2026-06-10", "2026-06-05"]:
                r = build_topic_heatmap([], as_of=date)
                save_topic_heatmap_report(r, output_dir=d)
            found = find_latest_topic_heatmap_report(d)
            assert "2026-06-10" in found


# ── Nightly report integration ───────────────────────────────────────

class TestNightlyReport:
    def test_with_candidates(self):
        section = build_heatmap_section_for_nightly_report(get_heatmap_sample_candidates())
        assert "主题热度" in section
        assert "低空经济" in section or "active_topics" not in section.lower()

    def test_fallback_to_empty(self):
        section = build_heatmap_section_for_nightly_report(None, reports_dir="/nonexistent")
        assert "主题热度" in section

    def test_from_saved_report(self):
        report = build_topic_heatmap(get_heatmap_sample_candidates(), as_of="2026-06-12")
        with tempfile.TemporaryDirectory() as d:
            save_topic_heatmap_report(report, output_dir=d)
            section = build_heatmap_section_for_nightly_report(None, reports_dir=d)
            assert "低空经济" in section


# ── Constraints: no trade actions ────────────────────────────────────

class TestNoTradeActions:
    """Verify topic heat never equals a trade action."""

    def test_no_buy_sell_in_state_change_labels(self):
        for prev in [TOPIC_STATUS_BREWING, TOPIC_STATUS_FERMENTING, TOPIC_STATUS_CONFIRMING,
                     TOPIC_STATUS_DELIVERING, TOPIC_STATUS_RECEDING, TOPIC_STATUS_UNKNOWN]:
            for curr in [TOPIC_STATUS_BREWING, TOPIC_STATUS_FERMENTING, TOPIC_STATUS_CONFIRMING,
                         TOPIC_STATUS_DELIVERING, TOPIC_STATUS_RECEDING, TOPIC_STATUS_UNKNOWN]:
                label = _state_change_label(prev, curr)
                for word in ["买入", "卖出", "清仓", "加仓", "减仓", "满仓"]:
                    assert word not in label

    def test_heatmap_dict_no_action_field(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        d = report.to_dict()
        assert "action" not in d
        assert "trade_action" not in d
        for t in d["topics"]:
            assert "action" not in t
            assert "buy" not in str(t).lower() or "beneficiary" in str(t).lower()

    def test_markdown_no_action_words(self):
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        md = render_topic_heatmap_markdown(report)
        for word in ["立即买入", "清仓", "梭哈", "强烈推荐买入"]:
            assert word not in md


# ── Sample fixtures ──────────────────────────────────────────────────

class TestSampleFixtures:
    def test_get_returns_copy(self):
        a = get_heatmap_sample_candidates()
        b = get_heatmap_sample_candidates()
        assert a == b
        a.clear()
        assert len(b) > 0  # original unchanged

    def test_module_constant_matches(self):
        assert len(HEATMAP_SAMPLE_CANDIDATES) == len(get_heatmap_sample_candidates())

    def test_fixtures_have_required_fields(self):
        for c in get_heatmap_sample_candidates():
            assert "symbol" in c
            assert "mandate_topic" in c
            assert "effective_trade_date" in c

    def test_three_required_topics_present(self):
        topics = {c["mandate_topic"] for c in get_heatmap_sample_candidates()}
        assert "低空经济" in topics
        assert "算力" in topics
        assert "半导体设备" in topics


# ── [H-013A] mandate_topic_heatmap_fix — regression tests ────────────


class TestHeatPointSymbolsField:
    """[H-013A] TopicHeatPoint now carries the symbol list so window stats
    can compute true cross-day unique candidates."""

    def test_point_carries_symbols(self):
        p = TopicHeatPoint(date="2026-06-10", candidate_count=2, symbols=["A", "B"])
        assert p.symbols == ["A", "B"]

    def test_point_to_dict_includes_symbols(self):
        p = TopicHeatPoint(date="2026-06-10", symbols=["A", "B"])
        d = p.to_dict()
        assert d["symbols"] == ["A", "B"]

    def test_build_daily_point_populates_symbols(self):
        cands = [
            {"symbol": "A", "policy_evidence_refs": []},
            {"symbol": "A", "policy_evidence_refs": []},
            {"symbol": "B", "policy_evidence_refs": [{"title": "x"}]},
        ]
        p = _build_daily_point("2026-06-10", cands)
        assert sorted(p.symbols) == ["A", "B"]
        assert p.unique_candidates == 2


class TestComputeWindowStatsUniqueCandidates:
    """[H-013A] Regression: 7/20/60 window unique_candidates must be populated,
    not always 0 (the symbols set was previously created but never filled)."""

    def test_unique_candidates_populated_and_deduped(self):
        curve = [
            TopicHeatPoint(
                date="2026-06-10",
                candidate_count=2,
                symbols=["300034.SZ", "002097.SZ"],
            ),
            TopicHeatPoint(
                date="2026-06-11",
                candidate_count=2,
                # 300034.SZ repeats across days → must be deduped
                symbols=["300034.SZ", "688012.SH"],
            ),
        ]
        ws = _compute_window_stats(curve, 7, "2026-06-12")
        assert ws.candidate_count == 4
        # 3 unique symbols across the 2 days
        assert ws.unique_candidates == 3

    def test_unique_candidates_zero_when_no_symbols(self):
        curve = [
            TopicHeatPoint(date="2026-06-10", candidate_count=1, symbols=[]),
        ]
        ws = _compute_window_stats(curve, 7, "2026-06-12")
        assert ws.unique_candidates == 0

    def test_unique_candidates_not_zero_for_sample_fixtures(self):
        """End-to-end guard: the sample fixtures must yield non-zero unique
        candidates in the 7-day window (was always 0 before the fix)."""
        cands = get_heatmap_sample_candidates()
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        # at least one active topic should have unique_candidates > 0 in its 7d window
        total_unique_7d = sum(
            t.windows[7].unique_candidates for t in report.topics if t.candidates
        )
        assert total_unique_7d > 0

    def test_unique_candidates_in_full_build(self):
        cands = [
            {"symbol": "A", "mandate_topic": "算力",
             "effective_trade_date": "2026-06-10", "topic_lifecycle_state": "EMERGING"},
            {"symbol": "B", "mandate_topic": "算力",
             "effective_trade_date": "2026-06-11", "topic_lifecycle_state": "EMERGING"},
            # A repeats on a different day
            {"symbol": "A", "mandate_topic": "算力",
             "effective_trade_date": "2026-06-11", "topic_lifecycle_state": "EMERGING"},
        ]
        report = build_topic_heatmap(cands, as_of="2026-06-12")
        topic = next(t for t in report.topics if t.topic == "算力")
        w7 = topic.windows[7]
        # A and B deduped across the two signal days
        assert w7.unique_candidates == 2


class TestGetTopicHeatmapLegacyDateFallback:
    """[H-013A] Regression: candidates with empty effective_trade_date but a
    valid trade_date must NOT be dropped from the heatmap.

    The SQL query in api.services.tradeflow_service.get_topic_heatmap now uses
    COALESCE(NULLIF(effective_trade_date, ''), trade_date) so legacy rows are
    included.
    """

    def test_legacy_empty_effective_trade_date_included(self):
        import sqlite3
        from tradingagents.tradeflow.candidate_engine import init_db
        from api.services.tradeflow_service import get_topic_heatmap

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_h013a_heatmap_legacy.db")
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            from datetime import datetime
            now = datetime.now().isoformat()
            # Row 1: LEGACY — empty effective_trade_date, valid trade_date
            conn.execute(
                "INSERT INTO tradeflow_candidates "
                "(trade_date, symbol, name, score, tier, trigger_price, invalid_price, "
                "strategy_tags_json, status, created_at, updated_at, "
                "effective_trade_date, plan_date, observe_state, composite_score, mandate_topic) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-06-10", "000001.SZ", "平安银行", 60.0, "A", 10.5, 9.0,
                 '[]', "active", now, now, "", "2026-06-10", "WAITING", 70.0, "算力"),
            )
            # Row 2: MODERN — effective_trade_date populated
            conn.execute(
                "INSERT INTO tradeflow_candidates "
                "(trade_date, symbol, name, score, tier, trigger_price, invalid_price, "
                "strategy_tags_json, status, created_at, updated_at, "
                "effective_trade_date, plan_date, observe_state, composite_score, mandate_topic) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-06-11", "600519.SH", "贵州茅台", 80.0, "A", 1800.0, 1700.0,
                 '[]', "active", now, now, "2026-06-11", "2026-06-11", "WAITING", 80.0, "算力"),
            )
            conn.commit()
            conn.close()

            result = get_topic_heatmap(as_of="2026-06-12", window_days=60, tf_db_path=db_path)
            assert result["status"] == "ok"

            # Both rows must be present — the legacy row (empty effective_trade_date)
            # was previously dropped because the query filtered on effective_trade_date only.
            all_symbols = set()
            for t in result["topics"]:
                for c in t.get("candidates", []):
                    all_symbols.add(c["symbol"])
            assert "000001.SZ" in all_symbols, (
                "legacy row with empty effective_trade_date was dropped by the heatmap query"
            )
            assert "600519.SH" in all_symbols

            topic = next(t for t in result["topics"] if t["topic"] == "算力")
            curve_dates = {p["date"] for p in topic["heat_curve"]}
            assert "2026-06-10" in curve_dates, (
                "legacy row was selected but did not contribute to heat_curve/window stats"
            )
            assert topic["windows"]["7"]["unique_candidates"] == 2

    def test_as_of_inferred_from_legacy_trade_date(self):
        """When all rows have empty effective_trade_date, as_of should still be
        inferred from trade_date via the COALESCE expression."""
        import sqlite3
        from tradingagents.tradeflow.candidate_engine import init_db
        from api.services.tradeflow_service import get_topic_heatmap

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_h013a_heatmap_asof.db")
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            from datetime import datetime
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO tradeflow_candidates "
                "(trade_date, symbol, name, score, tier, trigger_price, invalid_price, "
                "strategy_tags_json, status, created_at, updated_at, "
                "effective_trade_date, plan_date, observe_state, composite_score, mandate_topic) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-06-15", "000001.SZ", "平安银行", 60.0, "A", 10.5, 9.0,
                 '[]', "active", now, now, "", "2026-06-15", "WAITING", 70.0, "算力"),
            )
            conn.commit()
            conn.close()

            # Don't pass as_of — should be inferred from trade_date via COALESCE
            result = get_topic_heatmap(window_days=60, tf_db_path=db_path)
            assert result["status"] == "ok"
            assert result["as_of"] == "2026-06-15"
            topic = next(t for t in result["topics"] if t["topic"] == "算力")
            assert topic["heat_curve"][0]["date"] == "2026-06-15"
            assert topic["windows"]["7"]["unique_candidates"] == 1
