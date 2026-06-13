# [H-012] mandate_topic_registry — comprehensive tests
import pytest

from tradingagents.tradeflow.topic_registry import (
    POLICY_LEVEL_CENTRAL,
    POLICY_LEVEL_MINISTRY,
    POLICY_LEVEL_LOCAL,
    POLICY_LEVEL_INDUSTRY,
    POLICY_LEVEL_MEDIA,
    POLICY_LEVEL_UNKNOWN,
    TOPIC_STATUS_BREWING,
    TOPIC_STATUS_FERMENTING,
    TOPIC_STATUS_CONFIRMING,
    TOPIC_STATUS_DELIVERING,
    TOPIC_STATUS_RECEDING,
    TOPIC_STATUS_UNKNOWN,
    _TOPIC_STATUS_LABELS,
    _LEFT_SIDE_STATUSES,
    _CONFIRM_STATUSES,
    _OBSERVE_ONLY_STATUSES,
    _POLICY_LEVEL_WEIGHTS,
    _lifecycle_to_status,
    ChainSegment,
    TopicDefinition,
    TopicRegistryEntry,
    TopicRegistry,
    TopicWatchlistSymbol,
    TopicWatchlistEntry,
    TopicWatchlistResult,
    get_default_topic_definitions,
    get_default_topic_registry,
    get_topic_status_label,
    is_topic_left_side,
    is_topic_observe_only,
    match_topic_from_text,
    match_topic,
    suggest_topic_watchlist_note,
    build_topic_watchlist,
)


class TestPolicyLevels:
    def test_six_levels(self):
        levels = {
            POLICY_LEVEL_CENTRAL, POLICY_LEVEL_MINISTRY,
            POLICY_LEVEL_LOCAL, POLICY_LEVEL_INDUSTRY,
            POLICY_LEVEL_MEDIA, POLICY_LEVEL_UNKNOWN,
        }
        assert len(levels) == 6

    def test_weights_ordered(self):
        assert _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_CENTRAL] > _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_MINISTRY]
        assert _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_MINISTRY] > _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_LOCAL]
        assert _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_LOCAL] > _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_INDUSTRY]
        assert _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_INDUSTRY] > _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_MEDIA]
        assert _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_MEDIA] > _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_UNKNOWN]

    def test_unknown_weight_zero(self):
        assert _POLICY_LEVEL_WEIGHTS[POLICY_LEVEL_UNKNOWN] == 0


class TestTopicStatus:
    def test_six_statuses(self):
        statuses = {
            TOPIC_STATUS_BREWING, TOPIC_STATUS_FERMENTING,
            TOPIC_STATUS_CONFIRMING, TOPIC_STATUS_DELIVERING,
            TOPIC_STATUS_RECEDING, TOPIC_STATUS_UNKNOWN,
        }
        assert len(statuses) == 6

    def test_labels(self):
        assert _TOPIC_STATUS_LABELS[TOPIC_STATUS_BREWING] == "酝酿"
        assert _TOPIC_STATUS_LABELS[TOPIC_STATUS_FERMENTING] == "发酵"
        assert _TOPIC_STATUS_LABELS[TOPIC_STATUS_CONFIRMING] == "确认"
        assert _TOPIC_STATUS_LABELS[TOPIC_STATUS_DELIVERING] == "兑现"
        assert _TOPIC_STATUS_LABELS[TOPIC_STATUS_RECEDING] == "退潮"
        assert _TOPIC_STATUS_LABELS[TOPIC_STATUS_UNKNOWN] == "未知"

    def test_left_side_statuses(self):
        assert TOPIC_STATUS_BREWING in _LEFT_SIDE_STATUSES
        assert TOPIC_STATUS_FERMENTING in _LEFT_SIDE_STATUSES
        assert TOPIC_STATUS_CONFIRMING not in _LEFT_SIDE_STATUSES
        assert TOPIC_STATUS_RECEDING not in _LEFT_SIDE_STATUSES

    def test_observe_only_statuses(self):
        assert TOPIC_STATUS_RECEDING in _OBSERVE_ONLY_STATUSES
        assert TOPIC_STATUS_BREWING not in _OBSERVE_ONLY_STATUSES

    def test_confirm_statuses(self):
        assert TOPIC_STATUS_CONFIRMING in _CONFIRM_STATUSES
        assert TOPIC_STATUS_DELIVERING in _CONFIRM_STATUSES


class TestLifecycleToStatus:
    def test_emerging_to_brewing(self):
        assert _lifecycle_to_status("EMERGING") == TOPIC_STATUS_BREWING

    def test_accelerating_to_fermenting(self):
        assert _lifecycle_to_status("ACCELERATING") == TOPIC_STATUS_FERMENTING

    def test_confirming_to_confirming(self):
        assert _lifecycle_to_status("CONFIRMING") == TOPIC_STATUS_CONFIRMING

    def test_crowded_to_receding(self):
        assert _lifecycle_to_status("CROWDED") == TOPIC_STATUS_RECEDING

    def test_fading_to_receding(self):
        assert _lifecycle_to_status("FADING") == TOPIC_STATUS_RECEDING

    def test_unknown_to_unknown(self):
        assert _lifecycle_to_status("UNKNOWN") == TOPIC_STATUS_UNKNOWN

    def test_empty_to_unknown(self):
        assert _lifecycle_to_status("") == TOPIC_STATUS_UNKNOWN

    def test_invalid_to_unknown(self):
        assert _lifecycle_to_status("GARBAGE") == TOPIC_STATUS_UNKNOWN


class TestChainSegment:
    def test_defaults(self):
        seg = ChainSegment()
        assert seg.name == ""
        assert seg.role == ""
        assert seg.example_symbols == []

    def test_to_dict(self):
        seg = ChainSegment(name="整机制造", role="核心整机", example_symbols=["600000.SH"])
        d = seg.to_dict()
        assert d["name"] == "整机制造"
        assert d["role"] == "核心整机"
        assert d["example_symbols"] == ["600000.SH"]


class TestTopicDefinition:
    def test_defaults(self):
        d = TopicDefinition(topic="测试")
        assert d.topic == "测试"
        assert d.aliases == []
        assert d.policy_level == POLICY_LEVEL_UNKNOWN
        assert d.keywords == []
        assert d.chain_segments == []
        assert d.description == ""

    def test_to_dict(self):
        d = TopicDefinition(
            topic="低空经济",
            aliases=["eVTOL"],
            policy_level=POLICY_LEVEL_MINISTRY,
            keywords=["低空经济"],
            chain_segments=[ChainSegment(name="整机", role="核心")],
            description="test",
        )
        d_dict = d.to_dict()
        assert d_dict["topic"] == "低空经济"
        assert d_dict["aliases"] == ["eVTOL"]
        assert d_dict["policy_level"] == POLICY_LEVEL_MINISTRY
        assert d_dict["keywords"] == ["低空经济"]
        assert len(d_dict["chain_segments"]) == 1
        assert d_dict["description"] == "test"


class TestTopicRegistryEntry:
    def test_defaults(self):
        entry = TopicRegistryEntry(topic="算力")
        assert entry.topic == "算力"
        assert entry.topic_status == TOPIC_STATUS_UNKNOWN
        assert entry.topic_status_label == "未知"
        assert entry.lifecycle_state == "UNKNOWN"
        assert entry.policy_level == POLICY_LEVEL_UNKNOWN
        assert entry.last_signal_date == ""
        assert entry.signal_count == 0
        assert entry.evidence_links == []
        assert entry.evidence_summary == ""
        assert entry.chain_segments == []
        assert not entry.is_left_side
        assert not entry.is_observe_only
        assert not entry.is_confirmed
        assert entry.matched_candidates == []

    def test_to_dict(self):
        entry = TopicRegistryEntry(
            topic="低空经济",
            topic_status=TOPIC_STATUS_FERMENTING,
            lifecycle_state="ACCELERATING",
            policy_level=POLICY_LEVEL_MINISTRY,
            signal_count=5,
            is_left_side=True,
        )
        d = entry.to_dict()
        assert d["topic"] == "低空经济"
        assert d["topic_status"] == TOPIC_STATUS_FERMENTING
        assert d["policy_level"] == POLICY_LEVEL_MINISTRY
        assert d["signal_count"] == 5
        assert d["is_left_side"] is True


class TestDefaultTopicDefinitions:
    def test_has_low_altitude(self):
        defs = get_default_topic_definitions()
        topics = [d.topic for d in defs]
        assert "低空经济" in topics

    def test_has_compute(self):
        defs = get_default_topic_definitions()
        topics = [d.topic for d in defs]
        assert "算力" in topics

    def test_has_semiconductor(self):
        defs = get_default_topic_definitions()
        topics = [d.topic for d in defs]
        assert "半导体设备" in topics

    def test_has_robot(self):
        defs = get_default_topic_definitions()
        topics = [d.topic for d in defs]
        assert "机器人" in topics

    def test_has_new_energy(self):
        defs = get_default_topic_definitions()
        topics = [d.topic for d in defs]
        assert "新能源" in topics

    def test_min_definition_count(self):
        defs = get_default_topic_definitions()
        assert len(defs) >= 6

    def test_each_has_keywords(self):
        defs = get_default_topic_definitions()
        for d in defs:
            assert len(d.keywords) > 0, f"Topic {d.topic} has no keywords"

    def test_each_has_chain_segments(self):
        defs = get_default_topic_definitions()
        for d in defs:
            assert len(d.chain_segments) > 0, f"Topic {d.topic} has no chain segments"

    def test_each_has_policy_level(self):
        defs = get_default_topic_definitions()
        for d in defs:
            assert d.policy_level != POLICY_LEVEL_UNKNOWN, f"Topic {d.topic} has unknown policy level"

    def test_low_altitude_keywords(self):
        defs = get_default_topic_definitions()
        la_def = next(d for d in defs if d.topic == "低空经济")
        assert "低空经济" in la_def.keywords
        assert "eVTOL" in la_def.keywords

    def test_compute_keywords(self):
        defs = get_default_topic_definitions()
        comp_def = next(d for d in defs if d.topic == "算力")
        assert "算力" in comp_def.keywords
        assert "光模块" in comp_def.keywords

    def test_semiconductor_keywords(self):
        defs = get_default_topic_definitions()
        semi_def = next(d for d in defs if d.topic == "半导体设备")
        assert "半导体设备" in semi_def.keywords
        assert "刻蚀" in semi_def.keywords


class TestMatchTopicFromText:
    def test_match_low_altitude(self):
        assert match_topic_from_text("低空经济政策发布") == "低空经济"

    def test_match_compute(self):
        assert match_topic_from_text("算力中心建设加速") == "算力"

    def test_match_semiconductor(self):
        assert match_topic_from_text("刻蚀设备国产化") == "半导体设备"

    def test_match_robot(self):
        assert match_topic_from_text("人形机器人量产") == "机器人"

    def test_match_new_energy(self):
        assert match_topic_from_text("光伏产业链景气") == "新能源"

    def test_no_match(self):
        assert match_topic_from_text("某公司公告") == ""

    def test_empty_text(self):
        assert match_topic_from_text("") == ""

    def test_match_by_alias(self):
        assert match_topic_from_text("eVTOL concept stocks") == "低空经济"

    def test_match_keyword_in_longer_text(self):
        assert match_topic_from_text("国务院发布低空空域管理改革意见") == "低空经济"


class TestMatchTopic:
    def test_explicit_topic(self):
        result = match_topic(mandate_topic="算力")
        assert result == "算力"

    def test_from_policy_tags(self):
        result = match_topic(policy_tags=["低空经济政策"])
        assert result == "低空经济"

    def test_from_name(self):
        result = match_topic(name="某低空经济概念股")
        assert result == "低空经济"

    def test_priority_mandate_topic(self):
        result = match_topic(mandate_topic="算力", policy_tags=["低空经济政策"])
        assert result == "算力"

    def test_unmatched_returns_mandate_topic(self):
        result = match_topic(mandate_topic="自定义主题X")
        assert result == "自定义主题X"

    def test_empty_all(self):
        assert match_topic() == ""

    def test_keyword_fallback_for_unregistered_topic(self):
        result = match_topic(mandate_topic="低空经济试点城市")
        assert result == "低空经济"


class TestTopicRegistry:
    def setup_method(self):
        self.registry = TopicRegistry()

    def test_empty_registry(self):
        assert self.registry.all_entries() == {}

    def test_get_or_create(self):
        entry = self.registry.get_or_create("算力")
        assert entry.topic == "算力"
        assert entry.policy_level == POLICY_LEVEL_MINISTRY  # from default definition

    def test_get_or_create_idempotent(self):
        e1 = self.registry.get_or_create("算力")
        e2 = self.registry.get_or_create("算力")
        assert e1 is e2

    def test_get_existing(self):
        self.registry.get_or_create("算力")
        entry = self.registry.get("算力")
        assert entry is not None
        assert entry.topic == "算力"

    def test_get_nonexistent(self):
        assert self.registry.get("不存在") is None

    def test_register_candidate_basic(self):
        entry = self.registry.register_candidate_topic(
            topic="低空经济",
            mandate_topic="低空经济",
            candidate_symbol="600000.SH",
            signal_count=3,
            last_signal_date="2026-06-10",
        )
        assert entry.topic == "低空经济"
        assert entry.signal_count == 3
        assert entry.last_signal_date == "2026-06-10"
        assert "600000.SH" in entry.matched_candidates

    def test_register_updates_signal_count_max(self):
        self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="000001.SZ", signal_count=3,
        )
        entry = self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="000002.SZ", signal_count=5,
        )
        assert entry.signal_count == 5

    def test_register_does_not_decrease_signal_count(self):
        self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="000001.SZ", signal_count=5,
        )
        entry = self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="000002.SZ", signal_count=2,
        )
        assert entry.signal_count == 5

    def test_register_updates_last_signal_date(self):
        self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="A", last_signal_date="2026-06-01",
        )
        entry = self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="B", last_signal_date="2026-06-10",
        )
        assert entry.last_signal_date == "2026-06-10"

    def test_register_accumulates_candidates(self):
        self.registry.register_candidate_topic(topic="算力", candidate_symbol="A")
        self.registry.register_candidate_topic(topic="算力", candidate_symbol="B")
        entry = self.registry.get("算力")
        assert set(entry.matched_candidates) == {"A", "B"}

    def test_register_no_duplicate_candidate(self):
        self.registry.register_candidate_topic(topic="算力", candidate_symbol="A")
        self.registry.register_candidate_topic(topic="算力", candidate_symbol="A")
        entry = self.registry.get("算力")
        assert entry.matched_candidates.count("A") == 1

    def test_register_with_evidence_refs(self):
        refs = [
            {"title": "低空经济政策", "source": "国务院", "date": "2026-06-01", "url": ""},
            {"title": "eVTOL产业规划", "source": "工信部", "date": "2026-06-05", "url": ""},
        ]
        entry = self.registry.register_candidate_topic(
            topic="低空经济", candidate_symbol="A", evidence_refs=refs,
        )
        assert len(entry.evidence_links) == 2
        assert entry.evidence_summary != ""

    def test_register_deduplicates_evidence(self):
        ref = {"title": "政策A", "source": "国务院", "date": "2026-06-01", "url": ""}
        self.registry.register_candidate_topic(topic="低空经济", candidate_symbol="A", evidence_refs=[ref])
        entry = self.registry.register_candidate_topic(topic="低空经济", candidate_symbol="B", evidence_refs=[ref])
        assert len(entry.evidence_links) == 1

    def test_register_evidence_link_cap(self):
        refs = [{"title": f"政策{i}", "source": "国务院", "date": "2026-06-01", "url": ""} for i in range(25)]
        entry = self.registry.register_candidate_topic(topic="低空经济", candidate_symbol="A", evidence_refs=refs)
        assert len(entry.evidence_links) <= 20

    def test_register_policy_level_upgrade(self):
        self.registry.register_candidate_topic(
            topic="低空经济", candidate_symbol="A", policy_level=POLICY_LEVEL_LOCAL,
        )
        entry = self.registry.register_candidate_topic(
            topic="低空经济", candidate_symbol="B", policy_level=POLICY_LEVEL_CENTRAL,
        )
        assert entry.policy_level == POLICY_LEVEL_CENTRAL

    def test_register_policy_level_no_downgrade(self):
        self.registry.register_candidate_topic(
            topic="低空经济", candidate_symbol="A", policy_level=POLICY_LEVEL_CENTRAL,
        )
        entry = self.registry.register_candidate_topic(
            topic="低空经济", candidate_symbol="B", policy_level=POLICY_LEVEL_LOCAL,
        )
        assert entry.policy_level == POLICY_LEVEL_CENTRAL

    def test_register_lifecycle_to_status(self):
        entry = self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="A", lifecycle_state="ACCELERATING",
        )
        assert entry.topic_status == TOPIC_STATUS_FERMENTING
        assert entry.is_left_side is True

    def test_register_observe_only_status(self):
        entry = self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="A", lifecycle_state="CROWDED",
        )
        assert entry.topic_status == TOPIC_STATUS_RECEDING
        assert entry.is_observe_only is True

    def test_register_confirmed_status(self):
        entry = self.registry.register_candidate_topic(
            topic="算力", candidate_symbol="A", lifecycle_state="CONFIRMING",
        )
        assert entry.topic_status == TOPIC_STATUS_CONFIRMING
        assert entry.is_confirmed is True

    def test_chain_segments_from_definition(self):
        entry = self.registry.register_candidate_topic(topic="算力", candidate_symbol="A")
        assert len(entry.chain_segments) > 0

    def test_clear(self):
        self.registry.register_candidate_topic(topic="算力", candidate_symbol="A")
        self.registry.clear()
        assert self.registry.all_entries() == {}

    def test_to_dict(self):
        self.registry.register_candidate_topic(topic="算力", candidate_symbol="A")
        d = self.registry.to_dict()
        assert "算力" in d

    def test_all_definitions(self):
        defs = self.registry.all_definitions()
        assert "低空经济" in defs
        assert "算力" in defs

    def test_get_definition(self):
        d = self.registry.get_definition("算力")
        assert d is not None
        assert d.topic == "算力"

    def test_get_definition_nonexistent(self):
        assert self.registry.get_definition("不存在") is None

    def test_match_by_keyword_via_mandate_topic(self):
        entry = self.registry.register_candidate_topic(
            mandate_topic="低空经济试点",
            candidate_symbol="A",
        )
        assert entry.topic == "低空经济"

    def test_match_by_policy_tags(self):
        entry = self.registry.register_candidate_topic(
            mandate_topic="",
            policy_tags=["低空经济概念"],
            candidate_symbol="A",
        )
        assert entry.topic == "低空经济"

    def test_empty_topic_noop(self):
        entry = self.registry.register_candidate_topic(topic="", candidate_symbol="A")
        assert entry.topic == ""

    def test_update_count_increments(self):
        self.registry.register_candidate_topic(topic="算力", candidate_symbol="A")
        self.registry.register_candidate_topic(topic="算力", candidate_symbol="B")
        entry = self.registry.get("算力")
        assert entry.update_count == 2


class TestTopicStatusHelpers:
    def test_get_label(self):
        assert get_topic_status_label(TOPIC_STATUS_BREWING) == "酝酿"
        assert get_topic_status_label(TOPIC_STATUS_UNKNOWN) == "未知"

    def test_is_left_side(self):
        assert is_topic_left_side(TOPIC_STATUS_BREWING) is True
        assert is_topic_left_side(TOPIC_STATUS_FERMENTING) is True
        assert is_topic_left_side(TOPIC_STATUS_RECEDING) is False

    def test_is_observe_only(self):
        assert is_topic_observe_only(TOPIC_STATUS_RECEDING) is True
        assert is_topic_observe_only(TOPIC_STATUS_BREWING) is False


class TestSuggestTopicWatchlistNote:
    def test_empty_topic(self):
        assert suggest_topic_watchlist_note(topic="") == ""

    def test_basic_note(self):
        note = suggest_topic_watchlist_note(
            topic="算力",
            topic_status=TOPIC_STATUS_FERMENTING,
            policy_level=POLICY_LEVEL_MINISTRY,
            symbol_count=3,
        )
        assert "算力" in note
        assert "发酵" in note
        assert "部委" in note
        assert "核心3只" in note

    def test_no_candidates(self):
        note = suggest_topic_watchlist_note(topic="机器人", symbol_count=0)
        assert "暂无候选" in note

    def test_with_evidence_gaps(self):
        note = suggest_topic_watchlist_note(
            topic="低空经济",
            evidence_gaps=["资金验证", "技术确认"],
        )
        assert "缺口:资金验证/技术确认" in note

    def test_with_top_symbol_and_role(self):
        note = suggest_topic_watchlist_note(
            topic="算力",
            top_symbol="000001.SZ",
            top_role="AI芯片",
        )
        assert "标杆:000001.SZ(AI芯片)" in note

    def test_no_buy_sell_words(self):
        for word in ["买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓"]:
            note = suggest_topic_watchlist_note(topic="算力")
            assert word not in note

    def test_central_level_label(self):
        note = suggest_topic_watchlist_note(topic="国产替代", policy_level=POLICY_LEVEL_CENTRAL)
        assert "中央" in note

    def test_unknown_level_label(self):
        note = suggest_topic_watchlist_note(topic="测试", policy_level=POLICY_LEVEL_UNKNOWN)
        assert "未知级别" in note


class TestBuildTopicWatchlist:
    def test_empty_candidates(self):
        result = build_topic_watchlist([])
        assert result.total_topics == 0
        assert result.total_symbols == 0

    def test_single_topic_single_candidate(self):
        candidates = [
            {
                "symbol": "000001.SZ",
                "name": "测试公司",
                "mandate_topic": "算力",
                "policy_tags": [],
                "company_role": "AI芯片",
                "beneficiary_path": ["芯片设计"],
                "mandate_score_component": 60.0,
                "tier": "B",
                "blocking_evidence_gaps": ["资金验证"],
                "topic_lifecycle_state": "ACCELERATING",
                "topic_signal_count": 3,
            }
        ]
        result = build_topic_watchlist(candidates)
        assert result.total_topics == 1
        assert result.topics[0].topic == "算力"
        assert result.topics[0].topic_status == TOPIC_STATUS_FERMENTING
        assert len(result.topics[0].symbols) == 1
        assert result.topics[0].symbols[0].symbol == "000001.SZ"

    def test_multi_topic(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "算力", "mandate_score_component": 50},
            {"symbol": "B", "mandate_topic": "低空经济", "mandate_score_component": 40},
        ]
        result = build_topic_watchlist(candidates)
        assert result.total_topics == 2
        topics = {t.topic for t in result.topics}
        assert "算力" in topics
        assert "低空经济" in topics

    def test_max_symbols_per_topic(self):
        candidates = [
            {"symbol": f"S{i}", "mandate_topic": "算力", "mandate_score_component": float(100 - i)}
            for i in range(10)
        ]
        result = build_topic_watchlist(candidates, max_symbols_per_topic=3)
        assert len(result.topics[0].symbols) == 3
        assert result.total_symbols == 3

    def test_sorted_by_mandate_score(self):
        candidates = [
            {"symbol": "LOW", "mandate_topic": "算力", "mandate_score_component": 10.0},
            {"symbol": "HIGH", "mandate_topic": "算力", "mandate_score_component": 80.0},
            {"symbol": "MID", "mandate_topic": "算力", "mandate_score_component": 40.0},
        ]
        result = build_topic_watchlist(candidates)
        symbols = [s.symbol for s in result.topics[0].symbols]
        assert symbols[0] == "HIGH"
        assert symbols[1] == "MID"
        assert symbols[2] == "LOW"

    def test_keyword_matching(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "", "policy_tags": ["低空经济概念"], "name": "某公司"},
        ]
        result = build_topic_watchlist(candidates)
        assert result.total_topics == 1
        assert result.topics[0].topic == "低空经济"

    def test_counter_evidence_gaps_aggregated(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "算力", "blocking_evidence_gaps": ["资金验证", "技术确认"]},
            {"symbol": "B", "mandate_topic": "算力", "blocking_evidence_gaps": ["技术确认", "数据完整度低"]},
        ]
        result = build_topic_watchlist(candidates)
        gaps = result.topics[0].counter_evidence_gaps
        assert "资金验证" in gaps
        assert "技术确认" in gaps
        assert "数据完整度低" in gaps

    def test_topic_note_suggestion(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "算力", "mandate_score_component": 50.0, "company_role": "光模块"},
        ]
        result = build_topic_watchlist(candidates)
        note = result.topics[0].note_suggestion
        assert "算力" in note

    def test_left_side_status(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "算力", "topic_lifecycle_state": "EMERGING"},
        ]
        result = build_topic_watchlist(candidates)
        assert result.topics[0].is_left_side is True

    def test_observe_only_status(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "算力", "topic_lifecycle_state": "FADING"},
        ]
        result = build_topic_watchlist(candidates)
        assert result.topics[0].is_observe_only is True
        assert result.topics[0].topic_status == TOPIC_STATUS_RECEDING

    def test_chain_segments_in_watchlist(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "算力"},
        ]
        result = build_topic_watchlist(candidates)
        assert len(result.topics[0].chain_segments) > 0

    def test_candidate_without_topic_skipped(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "", "policy_tags": [], "name": "普通公司"},
        ]
        result = build_topic_watchlist(candidates)
        assert result.total_topics == 0

    def test_custom_mandate_topic(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "量子计算", "mandate_score_component": 30.0},
        ]
        result = build_topic_watchlist(candidates)
        assert result.total_topics == 1
        assert result.topics[0].topic == "量子计算"

    def test_topics_sorted_by_status(self):
        candidates = [
            {"symbol": "F", "mandate_topic": "新能源", "topic_lifecycle_state": "FADING"},
            {"symbol": "E", "mandate_topic": "算力", "topic_lifecycle_state": "EMERGING"},
            {"symbol": "C", "mandate_topic": "低空经济", "topic_lifecycle_state": "CONFIRMING"},
        ]
        result = build_topic_watchlist(candidates)
        statuses = [t.topic_status for t in result.topics]
        assert statuses.index(TOPIC_STATUS_BREWING) < statuses.index(TOPIC_STATUS_CONFIRMING)
        assert statuses.index(TOPIC_STATUS_CONFIRMING) < statuses.index(TOPIC_STATUS_RECEDING)

    def test_symbol_evidence_gaps_from_watchlist_gap(self):
        candidates = [
            {
                "symbol": "A",
                "mandate_topic": "算力",
                "watchlist_evidence_gap": ["政策证据", "资金验证"],
                "mandate_score_component": 50.0,
            },
        ]
        result = build_topic_watchlist(candidates)
        gaps = result.topics[0].symbols[0].evidence_gaps
        assert "政策证据" in gaps
        assert "资金验证" in gaps

    def test_evidence_refs_registered(self):
        candidates = [
            {
                "symbol": "A",
                "mandate_topic": "低空经济",
                "policy_evidence_refs": [
                    {"title": "低空政策", "source": "国务院", "date": "2026-06-01", "source_level": "CENTRAL"},
                ],
            },
        ]
        registry = TopicRegistry()
        result = build_topic_watchlist(candidates, registry=registry)
        entry = registry.get("低空经济")
        assert entry is not None
        assert len(entry.evidence_links) == 1
        assert entry.policy_level == POLICY_LEVEL_CENTRAL


class TestWatchlistDataclasses:
    def test_topic_watchlist_symbol_to_dict(self):
        s = TopicWatchlistSymbol(
            symbol="000001.SZ",
            name="测试",
            company_role="AI芯片",
            mandate_score=60.0,
            tier="A",
            evidence_gaps=["资金验证"],
        )
        d = s.to_dict()
        assert d["symbol"] == "000001.SZ"
        assert d["mandate_score"] == 60.0
        assert d["evidence_gaps"] == ["资金验证"]

    def test_topic_watchlist_entry_to_dict(self):
        e = TopicWatchlistEntry(
            topic="算力",
            topic_status=TOPIC_STATUS_FERMENTING,
            is_left_side=True,
            symbols=[TopicWatchlistSymbol(symbol="A")],
        )
        d = e.to_dict()
        assert d["topic"] == "算力"
        assert d["is_left_side"] is True
        assert len(d["symbols"]) == 1

    def test_topic_watchlist_result_to_dict(self):
        r = TopicWatchlistResult(
            topics=[TopicWatchlistEntry(topic="算力")],
            total_topics=1,
            total_symbols=1,
        )
        d = r.to_dict()
        assert d["total_topics"] == 1
        assert len(d["topics"]) == 1


class TestDefaultRegistry:
    def test_get_default_registry(self):
        reg = get_default_topic_registry()
        assert reg is not None
        assert isinstance(reg, TopicRegistry)

    def test_default_registry_is_singleton(self):
        reg1 = get_default_topic_registry()
        reg2 = get_default_topic_registry()
        assert reg1 is reg2


class TestNoTradeActions:
    """Verify that topic status changes do NOT produce trade actions."""

    def test_observe_only_does_not_block_candidate(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "算力", "topic_lifecycle_state": "CROWDED"},
        ]
        result = build_topic_watchlist(candidates)
        assert result.topics[0].is_observe_only is True
        assert len(result.topics[0].symbols) == 1

    def test_no_buy_sell_in_notes(self):
        candidates = [
            {"symbol": "A", "mandate_topic": "算力", "topic_lifecycle_state": "CONFIRMING"},
        ]
        result = build_topic_watchlist(candidates)
        for topic in result.topics:
            assert "买入" not in topic.note_suggestion
            assert "卖出" not in topic.note_suggestion
            for sym in topic.symbols:
                assert "买入" not in sym.note_suggestion
                assert "卖出" not in sym.note_suggestion
