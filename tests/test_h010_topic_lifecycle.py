# [H-010] mandate_topic_lifecycle — comprehensive tests
import pytest

from tradingagents.tradeflow.topic_lifecycle import (
    TopicLifecycleState,
    TopicLifecycleEntry,
    TopicLifecycleResult,
    TopicLifecycleRegistry,
    _LIFECYCLE_STATE_LABELS,
    _LEFT_SIDE_STATES,
    _OBSERVE_ONLY_STATES,
    _classify_lifecycle_state,
    _build_lifecycle_reason,
    evaluate_topic_lifecycle,
    apply_lifecycle_to_candidate,
    get_default_registry,
)


class TestTopicLifecycleState:
    def test_six_states(self):
        assert len(TopicLifecycleState) == 6

    def test_values(self):
        expected = {"EMERGING", "ACCELERATING", "CONFIRMING", "CROWDED", "FADING", "UNKNOWN"}
        assert {s.value for s in TopicLifecycleState} == expected

    def test_str_comparison(self):
        assert TopicLifecycleState.EMERGING.value == "EMERGING"
        assert TopicLifecycleState.CROWDED.value == "CROWDED"

    def test_labels(self):
        assert len(_LIFECYCLE_STATE_LABELS) == 6
        assert _LIFECYCLE_STATE_LABELS[TopicLifecycleState.EMERGING] == "新主题萌芽"
        assert _LIFECYCLE_STATE_LABELS[TopicLifecycleState.ACCELERATING] == "升温加速"
        assert _LIFECYCLE_STATE_LABELS[TopicLifecycleState.CONFIRMING] == "兑现确认"
        assert _LIFECYCLE_STATE_LABELS[TopicLifecycleState.CROWDED] == "拥挤过热"
        assert _LIFECYCLE_STATE_LABELS[TopicLifecycleState.FADING] == "退潮衰减"
        assert _LIFECYCLE_STATE_LABELS[TopicLifecycleState.UNKNOWN] == "未知"

    def test_left_side_states(self):
        assert _LEFT_SIDE_STATES == {TopicLifecycleState.EMERGING, TopicLifecycleState.ACCELERATING}

    def test_observe_only_states(self):
        assert _OBSERVE_ONLY_STATES == {TopicLifecycleState.CROWDED, TopicLifecycleState.FADING}


class TestTopicLifecycleEntry:
    def test_defaults(self):
        e = TopicLifecycleEntry(topic="算力")
        assert e.state == TopicLifecycleState.UNKNOWN
        assert e.signal_count == 0
        assert e.unique_dates == 0
        assert e.unique_sources == 0
        assert e.last_signal_date == ""
        assert not e.has_policy_document
        assert not e.has_high_authority
        assert not e.is_noise
        assert e.mandate_score == 0.0
        assert e.heat_delta == 0.0
        assert e.overheat_flags == []
        assert e.update_count == 0

    def test_to_dict(self):
        e = TopicLifecycleEntry(
            topic="低空经济",
            state=TopicLifecycleState.ACCELERATING,
            signal_count=5,
            unique_dates=3,
            unique_sources=2,
            last_signal_date="2026-06-07",
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=60.0,
            update_count=2,
        )
        d = e.to_dict()
        assert d["topic"] == "低空经济"
        assert d["state"] == "ACCELERATING"
        assert d["signal_count"] == 5
        assert d["unique_dates"] == 3
        assert d["mandate_score"] == 60.0
        assert d["update_count"] == 2

    def test_roundtrip(self):
        e = TopicLifecycleEntry(topic="半导体", state=TopicLifecycleState.CONFIRMING, signal_count=8)
        d = e.to_dict()
        assert d["topic"] == "半导体"
        assert d["state"] == "CONFIRMING"


class TestTopicLifecycleResult:
    def test_defaults(self):
        r = TopicLifecycleResult()
        assert r.topic_lifecycle_state == "UNKNOWN"
        assert r.topic_lifecycle_reason == ""
        assert r.topic_last_signal_date == ""
        assert r.topic_signal_count == 0
        assert not r.is_left_side_suitable
        assert not r.is_observe_only
        assert r.lifecycle_label == "未知"

    def test_to_dict(self):
        r = TopicLifecycleResult(
            topic_lifecycle_state="EMERGING",
            topic_lifecycle_reason="新主题萌芽",
            topic_signal_count=2,
            is_left_side_suitable=True,
            lifecycle_label="新主题萌芽",
        )
        d = r.to_dict()
        assert d["topic_lifecycle_state"] == "EMERGING"
        assert d["topic_lifecycle_reason"] == "新主题萌芽"
        assert d["topic_signal_count"] == 2
        assert d["is_left_side_suitable"] is True
        assert d["is_observe_only"] is False
        assert "lifecycle_entry" not in d

    def test_to_dict_with_entry(self):
        entry = TopicLifecycleEntry(topic="算力", state=TopicLifecycleState.ACCELERATING)
        r = TopicLifecycleResult(lifecycle_entry=entry)
        d = r.to_dict()
        assert "lifecycle_entry" in d
        assert d["lifecycle_entry"]["topic"] == "算力"


class TestTopicLifecycleRegistry:
    def test_empty(self):
        reg = TopicLifecycleRegistry()
        assert reg.get("不存在") is None
        assert reg.all_entries() == {}

    def test_get_or_create(self):
        reg = TopicLifecycleRegistry()
        e = reg.get_or_create("算力")
        assert e.topic == "算力"
        assert e.state == TopicLifecycleState.UNKNOWN
        e2 = reg.get_or_create("算力")
        assert e2 is e

    def test_update_entry(self):
        reg = TopicLifecycleRegistry()
        entry = reg.update_entry(
            "低空经济",
            signal_count=5,
            unique_dates=3,
            unique_sources=4,
            last_signal_date="2026-06-07",
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=65.0,
        )
        assert entry.topic == "低空经济"
        assert entry.signal_count == 5
        assert entry.update_count == 1

    def test_update_triggers_classify(self):
        reg = TopicLifecycleRegistry()
        entry = reg.update_entry(
            "算力",
            signal_count=8,
            unique_dates=4,
            unique_sources=3,
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=70.0,
        )
        assert entry.state == TopicLifecycleState.CONFIRMING

    def test_all_entries(self):
        reg = TopicLifecycleRegistry()
        reg.update_entry("算力", signal_count=3, unique_dates=2, unique_sources=2, mandate_score=40.0)
        reg.update_entry("低空经济", signal_count=1, unique_dates=1, is_noise=True)
        entries = reg.all_entries()
        assert len(entries) == 2
        assert "算力" in entries
        assert "低空经济" in entries

    def test_to_dict(self):
        reg = TopicLifecycleRegistry()
        reg.update_entry("半导体", signal_count=3, unique_dates=2, unique_sources=2, mandate_score=40.0)
        d = reg.to_dict()
        assert "半导体" in d

    def test_clear(self):
        reg = TopicLifecycleRegistry()
        reg.update_entry("算力", signal_count=3, unique_dates=2, unique_sources=2)
        reg.clear()
        assert reg.get("算力") is None

    def test_overheat_flags_update(self):
        reg = TopicLifecycleRegistry()
        entry = reg.update_entry(
            "机器人",
            signal_count=10,
            unique_dates=5,
            unique_sources=6,
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=80.0,
            overheat_flags=["overheated_price_position", "crowded_consensus_risk"],
        )
        assert entry.state == TopicLifecycleState.CROWDED


class TestClassifyLifecycleState:
    def test_no_topic(self):
        e = TopicLifecycleEntry(topic="")
        assert _classify_lifecycle_state(e) == TopicLifecycleState.UNKNOWN

    def test_zero_signals(self):
        e = TopicLifecycleEntry(topic="算力", signal_count=0)
        assert _classify_lifecycle_state(e) == TopicLifecycleState.UNKNOWN

    def test_noise_single_signal(self):
        e = TopicLifecycleEntry(topic="算力", signal_count=1, unique_dates=1, is_noise=True)
        assert _classify_lifecycle_state(e) == TopicLifecycleState.UNKNOWN

    def test_single_day_no_policy(self):
        e = TopicLifecycleEntry(topic="算力", signal_count=2, unique_dates=1, unique_sources=1)
        assert _classify_lifecycle_state(e) == TopicLifecycleState.EMERGING

    def test_emerging_two_signals_two_dates(self):
        e = TopicLifecycleEntry(topic="算力", signal_count=2, unique_dates=2, unique_sources=1)
        assert _classify_lifecycle_state(e) == TopicLifecycleState.EMERGING

    def test_accelerating_multi_day_has_policy(self):
        e = TopicLifecycleEntry(
            topic="算力", signal_count=5, unique_dates=3, unique_sources=3,
            has_policy_document=True, mandate_score=45.0,
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.ACCELERATING

    def test_accelerating_multi_source_no_policy(self):
        e = TopicLifecycleEntry(
            topic="算力", signal_count=4, unique_dates=2, unique_sources=3,
            has_high_authority=True, mandate_score=35.0,
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.ACCELERATING

    def test_accelerating_multi_day_mandate(self):
        e = TopicLifecycleEntry(
            topic="低空经济", signal_count=3, unique_dates=2, unique_sources=1,
            has_high_authority=False, has_policy_document=False, mandate_score=35.0,
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.ACCELERATING

    def test_confirming_strong(self):
        e = TopicLifecycleEntry(
            topic="半导体", signal_count=10, unique_dates=4, unique_sources=3,
            has_policy_document=True, has_high_authority=True, mandate_score=65.0,
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.CONFIRMING

    def test_confirming_min(self):
        e = TopicLifecycleEntry(
            topic="机器人", signal_count=8, unique_dates=3, unique_sources=2,
            has_policy_document=True, has_high_authority=False, mandate_score=50.0,
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.CONFIRMING

    def test_crowded_overheated(self):
        e = TopicLifecycleEntry(
            topic="AI应用", signal_count=15, unique_dates=5, unique_sources=8,
            has_policy_document=True, has_high_authority=True, mandate_score=80.0,
            overheat_flags=["overheated_price_position"],
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.CROWDED

    def test_crowded_consensus(self):
        e = TopicLifecycleEntry(
            topic="低空经济", signal_count=12, unique_dates=4, unique_sources=6,
            has_policy_document=True, mandate_score=70.0,
            overheat_flags=["crowded_consensus_risk"],
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.CROWDED

    def test_fading_crowded_no_policy_no_high(self):
        e = TopicLifecycleEntry(
            topic="数据要素", signal_count=5, unique_dates=3, unique_sources=2,
            has_policy_document=False, has_high_authority=False, mandate_score=30.0,
            heat_delta=-5.0,
            overheat_flags=["overheated_price_position"],
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.FADING

    def test_fading_negative_delta_no_policy(self):
        e = TopicLifecycleEntry(
            topic="出海", signal_count=3, unique_dates=2, unique_sources=2,
            has_policy_document=False, has_high_authority=False, heat_delta=-3.0,
        )
        assert _classify_lifecycle_state(e) == TopicLifecycleState.FADING

    def test_not_fading_with_policy(self):
        e = TopicLifecycleEntry(
            topic="算力", signal_count=5, unique_dates=3, unique_sources=3,
            has_policy_document=True, heat_delta=-2.0, mandate_score=55.0,
        )
        assert _classify_lifecycle_state(e) != TopicLifecycleState.FADING


class TestBuildLifecycleReason:
    def test_unknown_no_topic(self):
        e = TopicLifecycleEntry(topic="")
        reason = _build_lifecycle_reason(e, TopicLifecycleState.UNKNOWN)
        assert reason == "无政策主题"

    def test_unknown_noise(self):
        e = TopicLifecycleEntry(topic="算力", is_noise=True, signal_count=1)
        reason = _build_lifecycle_reason(e, TopicLifecycleState.UNKNOWN)
        assert "孤立" in reason

    def test_emerging(self):
        e = TopicLifecycleEntry(topic="算力", unique_dates=1)
        reason = _build_lifecycle_reason(e, TopicLifecycleState.EMERGING)
        assert "萌芽" in reason
        assert "1天" in reason

    def test_accelerating(self):
        e = TopicLifecycleEntry(topic="算力", unique_dates=3, has_policy_document=True, has_high_authority=True)
        reason = _build_lifecycle_reason(e, TopicLifecycleState.ACCELERATING)
        assert "加速" in reason
        assert "3天" in reason
        assert "政策文件" in reason
        assert "高权威" in reason

    def test_confirming(self):
        e = TopicLifecycleEntry(topic="算力", unique_dates=4, has_policy_document=True, mandate_score=65.0)
        reason = _build_lifecycle_reason(e, TopicLifecycleState.CONFIRMING)
        assert "确认" in reason
        assert "65" in reason

    def test_crowded(self):
        e = TopicLifecycleEntry(topic="算力", overheat_flags=["overheated_price_position"])
        reason = _build_lifecycle_reason(e, TopicLifecycleState.CROWDED)
        assert "拥挤" in reason
        assert "overheated" in reason

    def test_fading(self):
        e = TopicLifecycleEntry(topic="算力", heat_delta=-5.0, has_policy_document=False, has_high_authority=False)
        reason = _build_lifecycle_reason(e, TopicLifecycleState.FADING)
        assert "退潮" in reason
        assert "热度下降" in reason


class TestEvaluateTopicLifecycle:
    def test_no_topic(self):
        r = evaluate_topic_lifecycle(topic="")
        assert r.topic_lifecycle_state == "UNKNOWN"
        assert "无政策主题" in r.topic_lifecycle_reason
        assert r.topic_signal_count == 0

    def test_single_isolated_event(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="算力",
            signal_count=1,
            unique_dates=1,
            unique_sources=1,
            is_noise=True,
            registry=reg,
        )
        assert r.topic_lifecycle_state in ("UNKNOWN", "EMERGING")
        assert not r.is_left_side_suitable or r.topic_lifecycle_state == "EMERGING"

    def test_multi_day_policy_accelerating(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="低空经济",
            signal_count=5,
            unique_dates=3,
            unique_sources=4,
            last_signal_date="2026-06-07",
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=60.0,
            registry=reg,
        )
        assert r.topic_lifecycle_state in ("ACCELERATING", "CONFIRMING")
        assert r.topic_signal_count == 5
        assert r.topic_last_signal_date == "2026-06-07"

    def test_overheated_crowded(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="AI应用",
            signal_count=15,
            unique_dates=5,
            unique_sources=8,
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=80.0,
            overheat_flags=["overheated_price_position"],
            registry=reg,
        )
        assert r.topic_lifecycle_state == "CROWDED"
        assert r.is_observe_only is True
        assert not r.is_left_side_suitable

    def test_fading_no_new_signals(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="数据要素",
            signal_count=2,
            unique_dates=2,
            unique_sources=1,
            heat_delta=-5.0,
            has_policy_document=False,
            has_high_authority=False,
            registry=reg,
        )
        assert r.topic_lifecycle_state == "FADING"
        assert r.is_observe_only is True

    def test_registry_updated(self):
        reg = TopicLifecycleRegistry()
        evaluate_topic_lifecycle(
            topic="算力",
            signal_count=3,
            unique_dates=2,
            unique_sources=2,
            mandate_score=40.0,
            registry=reg,
        )
        entry = reg.get("算力")
        assert entry is not None
        assert entry.signal_count == 3
        assert entry.update_count == 1

    def test_default_registry(self):
        r = evaluate_topic_lifecycle(
            topic="测试默认注册表",
            signal_count=2,
            unique_dates=1,
            unique_sources=1,
        )
        assert r.topic_lifecycle_state in ("EMERGING", "UNKNOWN", "ACCELERATING")

    def test_left_side_suitable_emerging(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="算力",
            signal_count=2,
            unique_dates=1,
            unique_sources=1,
            registry=reg,
        )
        assert r.topic_lifecycle_state == "EMERGING"
        assert r.is_left_side_suitable is True

    def test_not_left_side_crowded(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="AI应用",
            signal_count=10,
            unique_dates=5,
            unique_sources=6,
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=70.0,
            overheat_flags=["crowded_consensus_risk"],
            registry=reg,
        )
        assert not r.is_left_side_suitable
        assert r.is_observe_only


class TestApplyLifecycleToCandidate:
    def test_no_downgrade_crowded_b_tier(self):
        lc = TopicLifecycleResult(
            topic_lifecycle_state="CROWDED",
            is_observe_only=True,
        )
        tier, reason = apply_lifecycle_to_candidate(lc, "B")
        assert tier == "B"
        assert reason != ""

    def test_downgrade_crowded_a_tier(self):
        lc = TopicLifecycleResult(
            topic_lifecycle_state="CROWDED",
            is_observe_only=True,
        )
        tier, reason = apply_lifecycle_to_candidate(lc, "A")
        assert tier == "B"
        assert "降级" in reason

    def test_no_downgrade_fading_a_tier(self):
        lc = TopicLifecycleResult(
            topic_lifecycle_state="FADING",
            is_observe_only=True,
        )
        tier, reason = apply_lifecycle_to_candidate(lc, "A")
        assert tier == "B"

    def test_no_change_normal(self):
        lc = TopicLifecycleResult(
            topic_lifecycle_state="EMERGING",
            is_observe_only=False,
        )
        tier, reason = apply_lifecycle_to_candidate(lc, "A")
        assert tier == "A"
        assert reason == ""

    def test_no_change_c_tier(self):
        lc = TopicLifecycleResult(
            topic_lifecycle_state="CROWDED",
            is_observe_only=True,
        )
        tier, reason = apply_lifecycle_to_candidate(lc, "C")
        assert tier == "C"
        assert reason == ""  # C tier stays C, no additional downgrade needed

    def test_no_change_empty_tier(self):
        lc = TopicLifecycleResult(
            topic_lifecycle_state="CROWDED",
            is_observe_only=True,
        )
        tier, reason = apply_lifecycle_to_candidate(lc, "")
        assert tier == ""
        assert reason == ""


class TestAcceptanceH010:
    def test_single_isolated_not_high_confidence(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="算力",
            signal_count=1,
            unique_dates=1,
            unique_sources=1,
            is_noise=True,
            registry=reg,
        )
        assert r.topic_lifecycle_state in ("UNKNOWN", "EMERGING")
        if r.topic_lifecycle_state == "UNKNOWN":
            assert not r.is_left_side_suitable

    def test_multi_day_multi_source_accelerating(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="低空经济",
            signal_count=6,
            unique_dates=3,
            unique_sources=4,
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=55.0,
            registry=reg,
        )
        assert r.topic_lifecycle_state in ("ACCELERATING", "CONFIRMING")

    def test_overheated_no_continuation_crowded_fading(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="AI应用",
            signal_count=8,
            unique_dates=3,
            unique_sources=4,
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=70.0,
            overheat_flags=["overheated_price_position", "crowded_consensus_risk"],
            registry=reg,
        )
        assert r.topic_lifecycle_state in ("CROWDED", "FADING")

    def test_emerging_left_side_priority(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="算力",
            signal_count=2,
            unique_dates=1,
            unique_sources=1,
            registry=reg,
        )
        assert r.topic_lifecycle_state == "EMERGING"
        assert r.is_left_side_suitable is True

    def test_crowded_fading_observe_only(self):
        for state_val in ("CROWDED", "FADING"):
            reg = TopicLifecycleRegistry()
            if state_val == "CROWDED":
                r = evaluate_topic_lifecycle(
                    topic=f"test_{state_val}",
                    signal_count=8,
                    unique_dates=4,
                    unique_sources=3,
                    has_policy_document=True,
                    has_high_authority=True,
                    mandate_score=60.0,
                    overheat_flags=["overheated_price_position", "crowded_consensus_risk"],
                    heat_delta=0.0,
                    registry=reg,
                )
            else:
                r = evaluate_topic_lifecycle(
                    topic=f"test_{state_val}",
                    signal_count=3,
                    unique_dates=2,
                    unique_sources=1,
                    has_policy_document=False,
                    has_high_authority=False,
                    mandate_score=10.0,
                    overheat_flags=["overheated_price_position"],
                    heat_delta=-5.0,
                    registry=reg,
                )
            assert r.topic_lifecycle_state == state_val
            assert r.is_observe_only is True
            assert not r.is_left_side_suitable

    def test_output_fields_present(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="算力",
            signal_count=3,
            unique_dates=2,
            unique_sources=2,
            last_signal_date="2026-06-07",
            registry=reg,
        )
        assert r.topic_lifecycle_state
        assert r.topic_lifecycle_reason
        assert r.topic_signal_count > 0
        d = r.to_dict()
        assert "topic_lifecycle_state" in d
        assert "topic_lifecycle_reason" in d
        assert "topic_last_signal_date" in d
        assert "topic_signal_count" in d
        assert "is_left_side_suitable" in d
        assert "is_observe_only" in d

    def test_confirming_strong_signals(self):
        reg = TopicLifecycleRegistry()
        r = evaluate_topic_lifecycle(
            topic="半导体",
            signal_count=10,
            unique_dates=4,
            unique_sources=3,
            last_signal_date="2026-06-07",
            has_policy_document=True,
            has_high_authority=True,
            mandate_score=70.0,
            registry=reg,
        )
        assert r.topic_lifecycle_state == "CONFIRMING"
        assert r.topic_signal_count == 10

    def test_schema_integration(self):
        from tradingagents.tradeflow.schemas import Candidate
        c = Candidate(symbol="600519.SH")
        assert hasattr(c, "topic_lifecycle_state")
        assert hasattr(c, "topic_lifecycle_reason")
        assert hasattr(c, "topic_last_signal_date")
        assert hasattr(c, "topic_signal_count")
        assert c.topic_lifecycle_state == ""
        assert c.topic_signal_count == 0

    def test_schema_db_roundtrip(self):
        from tradingagents.tradeflow.schemas import Candidate
        c = Candidate(
            symbol="600519.SH",
            topic_lifecycle_state="ACCELERATING",
            topic_lifecycle_reason="升温加速; 3天连续信号",
            topic_last_signal_date="2026-06-07",
            topic_signal_count=5,
        )
        row = c.to_db_row()
        assert row["topic_lifecycle_state"] == "ACCELERATING"
        assert row["topic_lifecycle_reason"] == "升温加速; 3天连续信号"
        assert row["topic_last_signal_date"] == "2026-06-07"
        assert row["topic_signal_count"] == 5
        c2 = Candidate.from_db_row(row)
        assert c2.topic_lifecycle_state == "ACCELERATING"
        assert c2.topic_lifecycle_reason == "升温加速; 3天连续信号"
        assert c2.topic_last_signal_date == "2026-06-07"
        assert c2.topic_signal_count == 5

    def test_api_schema_fields(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem
        item = TradeFlowCandidateItem(
            symbol="600519.SH",
            topic_lifecycle_state="EMERGING",
            topic_lifecycle_reason="新主题萌芽",
            topic_last_signal_date="2026-06-07",
            topic_signal_count=2,
        )
        assert item.topic_lifecycle_state == "EMERGING"
        assert item.topic_signal_count == 2

    def test_no_forbidden_words(self):
        forbidden = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}
        reg = TopicLifecycleRegistry()
        for state in TopicLifecycleState:
            e = TopicLifecycleEntry(topic="测试", state=state, unique_dates=2, signal_count=3,
                                    has_policy_document=True, has_high_authority=True, mandate_score=50.0,
                                    overheat_flags=["overheated_price_position"])
            reason = _build_lifecycle_reason(e, state)
            for word in forbidden:
                assert word not in reason, f"Forbidden word '{word}' in reason for {state}"
