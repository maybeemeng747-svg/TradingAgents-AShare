# [DATA-003] mandate_event_normalization
"""Tests for mandate_event_normalizer — unified event-to-MandateSignal conversion.

Covers:
- Multi-source ingestion (EventItem, raw dict, mixed)
- Source-level classification: research reports → MEDIA, company announcements → COMPANY_NOTICE
- Research reports never become CENTRAL/STATE_COUNCIL
- Company announcements cannot auto-promote to policy level
- Title deduplication across multiple sources (keep highest authority, merge raw_refs)
- Topic tagging from H-002 vocabulary
- Direction inference from event type and keywords
- Batch statistics and grouping
- Integration with existing mandate_signal and event_source modules
"""

import pytest
from dataclasses import dataclass, field

from tradingagents.tradeflow.mandate_event_normalizer import (
    NormalizedEventBatch,
    _classify_source_level_enhanced,
    _infer_direction,
    _merge_duplicate_signals,
    _raw_event_to_mandate_signal,
    _title_dedup_key,
    _topic_tags_from_title,
    is_company_announcement,
    is_policy_document,
    is_research_report,
    normalize_event_items_by_symbol,
    normalize_event_source_result,
    normalize_events,
    normalize_raw_dicts,
)
from tradingagents.tradeflow.mandate_signal import (
    MandateEventType,
    MandateSignal,
    SourceLevel,
    event_item_to_mandate_signal,
)
from tradingagents.tradeflow.event_source import EventItem, EventSourceResult


# ---------------------------------------------------------------------------
# Helper: mock event-like objects
# ---------------------------------------------------------------------------

class TestTitleDedupKey:
    def test_basic(self):
        assert _title_dedup_key("国务院关于低空经济的指导意见") == "国务院关于低空经济的指导意见"

    def test_whitespace_stripped(self):
        assert _title_dedup_key("  国务院 关于 低空经济  ") == "国务院关于低空经济"

    def test_long_title_truncated(self):
        key = _title_dedup_key("A" * 100)
        assert len(key) == 60

    def test_case_insensitive(self):
        assert _title_dedup_key("ABC") == _title_dedup_key("abc")


class TestClassifySourceLevelEnhanced:
    def test_research_report_is_media(self):
        sl = _classify_source_level_enhanced(
            "中金公司：算力行业深度研报", "中金公司", "rating"
        )
        assert sl == SourceLevel.MEDIA

    def test_research_org_in_source(self):
        sl = _classify_source_level_enhanced(
            "低空经济行业分析", "华泰证券研究所", ""
        )
        assert sl == SourceLevel.MEDIA

    def test_rating_is_media(self):
        sl = _classify_source_level_enhanced(
            "买入评级", "cninfo", "rating"
        )
        assert sl == SourceLevel.MEDIA

    def test_buyback_is_company_notice(self):
        sl = _classify_source_level_enhanced(
            "回购计划公告", "eastmoney", "buyback"
        )
        assert sl == SourceLevel.COMPANY_NOTICE

    def test_notice_with_policy_keywords(self):
        sl = _classify_source_level_enhanced(
            "工信部关于机器人产业发展的指导意见", "eastmoney", "notice"
        )
        assert sl == SourceLevel.MINISTRY

    def test_notice_without_policy_keywords(self):
        sl = _classify_source_level_enhanced(
            "关于董事会决议的公告", "巨潮资讯", "notice"
        )
        assert sl == SourceLevel.COMPANY_NOTICE

    def test_state_council(self):
        sl = _classify_source_level_enhanced(
            "国务院关于印发新质生产力发展纲要的通知", "xinhua", ""
        )
        assert sl == SourceLevel.STATE_COUNCIL

    def test_central_party(self):
        sl = _classify_source_level_enhanced(
            "中共中央关于全面深化改革的决定", "xinhua", ""
        )
        assert sl == SourceLevel.CENTRAL

    def test_fallback_to_classify(self):
        sl = _classify_source_level_enhanced(
            "某公司中标大项目", "某新闻", ""
        )
        assert sl in list(SourceLevel)


class TestInferDirection:
    def test_buyback_bullish(self):
        assert _infer_direction("回购计划", "", "buyback") == "bullish"

    def test_rating_buy(self):
        assert _infer_direction("买入评级", "", "rating") == "bullish"

    def test_rating_sell(self):
        assert _infer_direction("卖出评级", "", "rating") == "bearish"

    def test_rating_neutral(self):
        assert _infer_direction("持有评级", "", "rating") == "neutral"

    def test_notice_bearish(self):
        assert _infer_direction("风险提示公告", "", "notice") == "bearish"

    def test_notice_bullish(self):
        assert _infer_direction("回购公告", "", "notice") == "bullish"

    def test_notice_neutral(self):
        assert _infer_direction("董事会决议公告", "", "notice") == "neutral"

    def test_generic_bearish(self):
        assert _infer_direction("公司遭遇减持", "", "") == "bearish"

    def test_generic_bullish(self):
        assert _infer_direction("公司中标重大合同", "", "") == "bullish"

    def test_generic_neutral(self):
        assert _infer_direction("公司召开股东大会", "", "") == "neutral"


class TestTopicTagsFromTitle:
    def test_low_altitude(self):
        tags = _topic_tags_from_title("低空经济产业规划发布")
        assert "低空经济" in tags

    def test_multiple_topics(self):
        tags = _topic_tags_from_title("算力与AI应用融合发展指导意见")
        assert "算力" in tags
        assert "AI应用" in tags

    def test_no_match(self):
        tags = _topic_tags_from_title("公司关于董事会决议的公告")
        assert tags == []

    def test_empty_title(self):
        tags = _topic_tags_from_title("")
        assert tags == []


class TestRawEventToMandateSignal:
    def test_dict_event(self):
        raw = {
            "title": "国务院关于低空经济发展的指导意见",
            "symbol": "002138.SZ",
            "source": "xinhua",
            "date": "2026-06-01",
            "event_type": "",
            "url": "https://example.com/doc1",
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert sig.symbol == "002138.SZ"
        assert sig.title == "国务院关于低空经济发展的指导意见"
        assert sig.source_level == SourceLevel.STATE_COUNCIL.value
        assert sig.date == "2026-06-01"
        assert sig.confidence > 0
        assert "低空经济" in sig.policy_tags

    def test_dict_empty_title_skipped(self):
        raw = {"title": "", "symbol": "002138.SZ"}
        assert _raw_event_to_mandate_signal(raw) is None

    def test_dict_nan_title_skipped(self):
        raw = {"title": "nan", "symbol": "002138.SZ"}
        assert _raw_event_to_mandate_signal(raw) is None

    def test_event_item(self):
        item = EventItem(
            symbol="600519.SH",
            title="回购计划公告",
            event_type="buyback",
            direction="bullish",
            date="2026-06-01",
            source="eastmoney",
        )
        sig = _raw_event_to_mandate_signal(item)
        assert sig is not None
        assert sig.symbol == "600519.SH"
        assert sig.source_level == SourceLevel.COMPANY_NOTICE.value
        assert sig.direction == "bullish"

    def test_event_item_empty_title(self):
        item = EventItem(symbol="600519.SH", title="")
        assert _raw_event_to_mandate_signal(item) is None

    def test_research_report_is_media(self):
        raw = {
            "title": "中金公司：算力行业深度研究报告",
            "symbol": "002138.SZ",
            "source": "中金公司",
            "date": "2026-06-01",
            "event_type": "rating",
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert sig.source_level == SourceLevel.MEDIA.value
        assert sig.source_level != SourceLevel.CENTRAL.value
        assert sig.source_level != SourceLevel.STATE_COUNCIL.value

    def test_research_report_cannot_be_central(self):
        raw = {
            "title": "中信证券：国务院新政策利好低空经济板块",
            "symbol": "002138.SZ",
            "source": "中信证券",
            "date": "2026-06-01",
            "event_type": "rating",
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert sig.source_level == SourceLevel.MEDIA.value

    def test_company_announcement_is_company_notice(self):
        raw = {
            "title": "关于召开2026年第一次临时股东大会的通知",
            "symbol": "600519.SH",
            "source": "巨潮资讯",
            "date": "2026-06-01",
            "event_type": "notice",
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert sig.source_level == SourceLevel.COMPANY_NOTICE.value

    def test_company_announcement_cannot_be_policy(self):
        raw = {
            "title": "关于董事会决议的公告",
            "symbol": "600519.SH",
            "source": "巨潮资讯",
            "date": "2026-06-01",
            "event_type": "notice",
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert sig.source_level in (
            SourceLevel.COMPANY_NOTICE.value,
            SourceLevel.MEDIA.value,
        )
        assert sig.source_level != SourceLevel.CENTRAL.value
        assert sig.source_level != SourceLevel.STATE_COUNCIL.value
        assert sig.source_level != SourceLevel.MINISTRY.value

    def test_policy_notice_is_ministry(self):
        raw = {
            "title": "工信部关于机器人产业发展的指导意见",
            "symbol": "",
            "source": "eastmoney",
            "date": "2026-06-01",
            "event_type": "notice",
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert sig.source_level == SourceLevel.MINISTRY.value

    def test_url_from_detail(self):
        raw = {
            "title": "回购公告",
            "symbol": "600519.SH",
            "detail": {"url": "https://cninfo.com/doc/123"},
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert sig.evidence_url == "https://cninfo.com/doc/123"

    def test_topic_tagging(self):
        raw = {
            "title": "算力基础设施建设加速推进",
            "symbol": "002138.SZ",
            "source": "新闻",
            "date": "2026-06-01",
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert "算力" in sig.policy_tags

    def test_confidence_low_for_missing_fields(self):
        raw = {"title": "某事件", "symbol": "600519.SH"}
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert sig.confidence <= 0.3

    def test_raw_refs_preserved(self):
        raw = {
            "title": "国务院关于低空经济的意见",
            "symbol": "",
            "source": "xinhua",
            "date": "2026-06-01",
            "url": "https://example.com",
        }
        sig = _raw_event_to_mandate_signal(raw)
        assert sig is not None
        assert len(sig.raw_refs) == 1
        assert sig.raw_refs[0]["original_source"] == "xinhua"
        assert sig.raw_refs[0]["url"] == "https://example.com"


class TestMergeDuplicateSignals:
    def test_no_duplicates(self):
        signals = [
            MandateSignal(symbol="A", title="标题1", source="s1", source_level="MEDIA", date="2026-06-01"),
            MandateSignal(symbol="A", title="标题2", source="s1", source_level="MEDIA", date="2026-06-01"),
        ]
        merged = _merge_duplicate_signals(signals)
        assert len(merged) == 2

    def test_same_title_same_symbol_deduped(self):
        signals = [
            MandateSignal(symbol="A", title="国务院关于低空经济的意见", source="s1", source_level="MEDIA", date="2026-06-01", confidence=0.3, raw_refs=[{"original_source": "s1", "original_date": "2026-06-01"}]),
            MandateSignal(symbol="A", title="国务院关于低空经济的意见", source="xinhua", source_level="STATE_COUNCIL", date="2026-06-01", confidence=0.8, raw_refs=[{"original_source": "xinhua", "original_date": "2026-06-01"}]),
        ]
        merged = _merge_duplicate_signals(signals)
        assert len(merged) == 1
        assert merged[0].source_level == SourceLevel.STATE_COUNCIL.value
        assert len(merged[0].raw_refs) == 2

    def test_same_title_different_symbol_kept(self):
        signals = [
            MandateSignal(symbol="A", title="回购计划", source="s1", source_level="COMPANY_NOTICE", date="2026-06-01"),
            MandateSignal(symbol="B", title="回购计划", source="s1", source_level="COMPANY_NOTICE", date="2026-06-01"),
        ]
        merged = _merge_duplicate_signals(signals)
        assert len(merged) == 2

    def test_highest_authority_kept(self):
        signals = [
            MandateSignal(symbol="A", title="低空经济政策发布", source="media", source_level="MEDIA", date="2026-06-01", confidence=0.5, raw_refs=[{"original_source": "media"}]),
            MandateSignal(symbol="A", title="低空经济政策发布", source="ministry", source_level="MINISTRY", date="2026-06-01", confidence=0.7, raw_refs=[{"original_source": "ministry"}]),
            MandateSignal(symbol="A", title="低空经济政策发布", source="state_council", source_level="STATE_COUNCIL", date="2026-06-01", confidence=0.9, raw_refs=[{"original_source": "state_council"}]),
        ]
        merged = _merge_duplicate_signals(signals)
        assert len(merged) == 1
        assert merged[0].source_level == SourceLevel.STATE_COUNCIL.value
        assert len(merged[0].raw_refs) == 3

    def test_raw_refs_deduped_by_source_date(self):
        signals = [
            MandateSignal(symbol="A", title="某事件", source="s1", source_level="MEDIA", date="2026-06-01", raw_refs=[{"original_source": "s1", "original_date": "2026-06-01"}, {"original_source": "s1", "original_date": "2026-06-01"}]),
            MandateSignal(symbol="A", title="某事件", source="s2", source_level="COMPANY_NOTICE", date="2026-06-01", raw_refs=[{"original_source": "s2", "original_date": "2026-06-01"}, {"original_source": "s1", "original_date": "2026-06-01"}]),
        ]
        merged = _merge_duplicate_signals(signals)
        assert len(merged) == 1
        sources = [r["original_source"] for r in merged[0].raw_refs]
        assert sources.count("s1") == 1

    def test_policy_tags_merged(self):
        signals = [
            MandateSignal(symbol="A", title="算力与AI应用", source="s1", source_level="MEDIA", date="2026-06-01", policy_tags=["算力"], raw_refs=[{"original_source": "s1", "original_date": "2026-06-01"}]),
            MandateSignal(symbol="A", title="算力与AI应用", source="s2", source_level="COMPANY_NOTICE", date="2026-06-01", policy_tags=["AI应用"], raw_refs=[{"original_source": "s2", "original_date": "2026-06-01"}]),
        ]
        merged = _merge_duplicate_signals(signals)
        assert len(merged) == 1
        assert "算力" in merged[0].policy_tags
        assert "AI应用" in merged[0].policy_tags

    def test_empty_list(self):
        assert _merge_duplicate_signals([]) == []


class TestNormalizeEvents:
    def test_basic_dict_events(self):
        events = [
            {"title": "国务院关于低空经济的意见", "symbol": "002138.SZ", "source": "xinhua", "date": "2026-06-01"},
            {"title": "回购计划", "symbol": "600519.SH", "source": "eastmoney", "date": "2026-06-01", "event_type": "buyback"},
        ]
        batch = normalize_events(events)
        assert batch.total_raw == 2
        assert len(batch.signals) == 2
        assert batch.duplicates_removed == 0

    def test_dedup_counts(self):
        events = [
            {"title": "低空经济政策发布", "symbol": "A", "source": "media", "date": "2026-06-01"},
            {"title": "低空经济政策发布", "symbol": "A", "source": "ministry", "date": "2026-06-01"},
            {"title": "低空经济政策发布", "symbol": "A", "source": "state_council", "date": "2026-06-01"},
        ]
        batch = normalize_events(events)
        assert batch.duplicates_removed == 2
        assert len(batch.signals) == 1

    def test_by_symbol_grouping(self):
        events = [
            {"title": "事件1", "symbol": "A", "source": "s1", "date": "2026-06-01"},
            {"title": "事件2", "symbol": "B", "source": "s1", "date": "2026-06-01"},
            {"title": "事件3", "symbol": "A", "source": "s1", "date": "2026-06-01"},
        ]
        batch = normalize_events(events)
        assert len(batch.by_symbol["A"]) == 2
        assert len(batch.by_symbol["B"]) == 1

    def test_by_source_level(self):
        events = [
            {"title": "国务院关于意见", "symbol": "A", "source": "国务院办公厅", "date": "2026-06-01"},
            {"title": "回购计划", "symbol": "B", "source": "eastmoney", "date": "2026-06-01", "event_type": "buyback"},
        ]
        batch = normalize_events(events)
        assert batch.by_source_level.get("STATE_COUNCIL", 0) == 1
        assert batch.by_source_level.get("COMPANY_NOTICE", 0) == 1

    def test_by_event_type(self):
        events = [
            {"title": "回购计划", "symbol": "A", "source": "eastmoney", "date": "2026-06-01", "event_type": "buyback"},
            {"title": "某政策规划", "symbol": "B", "source": "s1", "date": "2026-06-01"},
        ]
        batch = normalize_events(events)
        assert batch.by_event_type.get("BUYBACK_RATING", 0) == 1

    def test_empty_input(self):
        batch = normalize_events([])
        assert batch.total_raw == 0
        assert len(batch.signals) == 0

    def test_all_empty_titles(self):
        events = [{"title": ""}, {"title": "nan"}]
        batch = normalize_events(events)
        assert len(batch.signals) == 0

    def test_mixed_event_item_and_dict(self):
        items = [
            EventItem(symbol="600519.SH", title="减持公告", event_type="notice", direction="bearish", date="2026-06-01", source="eastmoney"),
            {"title": "买入评级", "symbol": "002138.SZ", "source": "cninfo", "date": "2026-06-01", "event_type": "rating"},
        ]
        batch = normalize_events(items)
        assert len(batch.signals) == 2

    def test_to_dict(self):
        events = [
            {"title": "回购计划", "symbol": "A", "source": "eastmoney", "date": "2026-06-01", "event_type": "buyback"},
        ]
        batch = normalize_events(events)
        d = batch.to_dict()
        assert "total_raw" in d
        assert "total_normalized" in d
        assert "duplicates_removed" in d
        assert "symbols_count" in d


class TestNormalizeRawDicts:
    def test_basic(self):
        events = [
            {"title": "政策发布", "symbol": "A", "source": "国务院", "date": "2026-06-01"},
        ]
        batch = normalize_raw_dicts(events)
        assert len(batch.signals) == 1

    def test_default_symbol(self):
        events = [
            {"title": "政策发布", "source": "国务院", "date": "2026-06-01"},
        ]
        batch = normalize_raw_dicts(events, default_symbol="DEFAULT.SZ")
        assert len(batch.signals) == 1
        assert batch.signals[0].symbol == "DEFAULT.SZ"


class TestNormalizeEventSourceResult:
    def test_basic(self):
        result = EventSourceResult()
        result.items_by_symbol = {
            "600519.SH": [
                EventItem(symbol="600519.SH", title="回购计划", event_type="buyback", direction="bullish", date="2026-06-01", source="eastmoney"),
            ],
        }
        batch = normalize_event_source_result(result)
        assert len(batch.signals) == 1
        assert batch.signals[0].source_level == SourceLevel.COMPANY_NOTICE.value

    def test_empty_result(self):
        result = EventSourceResult()
        batch = normalize_event_source_result(result)
        assert len(batch.signals) == 0


class TestNormalizeEventItemsBySymbol:
    def test_basic(self):
        items_by_symbol = {
            "600519.SH": [
                EventItem(symbol="600519.SH", title="公告1", date="2026-06-01", source="eastmoney"),
                EventItem(symbol="600519.SH", title="公告2", date="2026-06-01", source="eastmoney"),
            ],
            "002138.SZ": [
                EventItem(symbol="002138.SZ", title="公告3", date="2026-06-01", source="cninfo"),
            ],
        }
        batch = normalize_event_items_by_symbol(items_by_symbol)
        assert len(batch.signals) == 3

    def test_empty_input(self):
        batch = normalize_event_items_by_symbol({})
        assert len(batch.signals) == 0


class TestIsResearchReport:
    def test_research_org(self):
        assert is_research_report("行业深度分析", "中信证券") is True

    def test_non_research(self):
        assert is_research_report("国务院公告", "xinhua") is False

    def test_title_contains_research_keyword(self):
        assert is_research_report("中金公司研报：低空经济前景", "") is True


class TestIsCompanyAnnouncement:
    def test_notice_event_type(self):
        assert is_company_announcement("董事会决议公告", "巨潮资讯", "notice") is True

    def test_notice_with_policy_is_not_company(self):
        assert is_company_announcement("工信部关于机器人政策", "eastmoney", "notice") is False

    def test_non_notice_non_policy(self):
        assert is_company_announcement("关于增持公告", "巨潮资讯", "") is True

    def test_non_notice_with_policy(self):
        assert is_company_announcement("工信部发布规划", "eastmoney", "") is False


class TestIsPolicyDocument:
    def test_state_council(self):
        assert is_policy_document("国务院关于意见", "") is True

    def test_ministry(self):
        assert is_policy_document("工信部规划", "") is True

    def test_non_policy(self):
        assert is_policy_document("公司董事会决议", "") is False


class TestAcceptanceData003:
    def test_same_policy_multi_source_no_double_count(self):
        events = [
            {"title": "国务院关于低空经济发展的指导意见", "symbol": "002138.SZ", "source": "xinhua", "date": "2026-06-01"},
            {"title": "国务院关于低空经济发展的指导意见", "symbol": "002138.SZ", "source": "sina", "date": "2026-06-01"},
            {"title": "国务院关于低空经济发展的指导意见", "symbol": "002138.SZ", "source": "cls", "date": "2026-06-01"},
        ]
        batch = normalize_events(events)
        assert len(batch.signals) == 1
        assert batch.signals[0].source_level == SourceLevel.STATE_COUNCIL.value
        assert len(batch.signals[0].raw_refs) == 3

    def test_research_report_not_central_policy(self):
        events = [
            {"title": "中信证券：国务院新政策利好低空经济板块", "symbol": "002138.SZ", "source": "中信证券", "date": "2026-06-01", "event_type": "rating"},
        ]
        batch = normalize_events(events)
        assert len(batch.signals) == 1
        assert batch.signals[0].source_level == SourceLevel.MEDIA.value
        assert batch.signals[0].source_level != SourceLevel.CENTRAL.value
        assert batch.signals[0].source_level != SourceLevel.STATE_COUNCIL.value

    def test_company_notice_cannot_be_policy_level(self):
        events = [
            {"title": "关于召开股东大会的通知", "symbol": "600519.SH", "source": "巨潮资讯", "date": "2026-06-01", "event_type": "notice"},
        ]
        batch = normalize_events(events)
        assert len(batch.signals) == 1
        sl = batch.signals[0].source_level
        assert sl in (SourceLevel.COMPANY_NOTICE.value, SourceLevel.MEDIA.value)
        assert sl not in (SourceLevel.CENTRAL.value, SourceLevel.STATE_COUNCIL.value, SourceLevel.MINISTRY.value)

    def test_policy_notice_is_correct_level(self):
        events = [
            {"title": "工信部关于机器人产业发展的指导意见", "symbol": "", "source": "eastmoney", "date": "2026-06-01", "event_type": "notice"},
        ]
        batch = normalize_events(events)
        assert len(batch.signals) == 1
        assert batch.signals[0].source_level == SourceLevel.MINISTRY.value

    def test_buyback_always_company_notice(self):
        events = [
            {"title": "回购计划公告", "symbol": "600519.SH", "source": "eastmoney", "date": "2026-06-01", "event_type": "buyback"},
        ]
        batch = normalize_events(events)
        assert batch.signals[0].source_level == SourceLevel.COMPANY_NOTICE.value
        assert batch.signals[0].direction == "bullish"

    def test_rating_always_media(self):
        events = [
            {"title": "中金公司买入评级", "symbol": "002138.SZ", "source": "中金公司", "date": "2026-06-01", "event_type": "rating"},
        ]
        batch = normalize_events(events)
        assert batch.signals[0].source_level == SourceLevel.MEDIA.value

    def test_mixed_events_correct_levels(self):
        events = [
            {"title": "国务院关于新质生产力的纲要", "symbol": "", "source": "xinhua", "date": "2026-06-01"},
            {"title": "工信部关于算力基础设施意见", "symbol": "", "source": "eastmoney", "date": "2026-06-01", "event_type": "notice"},
            {"title": "华泰证券：算力行业研报", "symbol": "002138.SZ", "source": "华泰证券", "date": "2026-06-01", "event_type": "rating"},
            {"title": "回购计划", "symbol": "600519.SH", "source": "eastmoney", "date": "2026-06-01", "event_type": "buyback"},
            {"title": "股东大会通知", "symbol": "600519.SH", "source": "巨潮资讯", "date": "2026-06-01", "event_type": "notice"},
        ]
        batch = normalize_events(events)
        assert len(batch.signals) == 5
        levels = {s.source_level for s in batch.signals}
        assert SourceLevel.STATE_COUNCIL.value in levels
        assert SourceLevel.MINISTRY.value in levels
        assert SourceLevel.MEDIA.value in levels
        assert SourceLevel.COMPANY_NOTICE.value in levels

    def test_topic_tags_populated(self):
        events = [
            {"title": "算力基础设施建设加速推进", "symbol": "002138.SZ", "source": "新闻", "date": "2026-06-01"},
            {"title": "低空经济产业规划发布", "symbol": "600519.SH", "source": "新闻", "date": "2026-06-01"},
        ]
        batch = normalize_events(events)
        topics_found = set()
        for sig in batch.signals:
            topics_found.update(sig.policy_tags)
        assert "算力" in topics_found
        assert "低空经济" in topics_found

    def test_direction_correct_for_events(self):
        events = [
            {"title": "风险提示公告", "symbol": "600519.SH", "source": "巨潮资讯", "date": "2026-06-01", "event_type": "notice"},
            {"title": "减持公告", "symbol": "600519.SH", "source": "巨潮资讯", "date": "2026-06-01", "event_type": "notice"},
            {"title": "回购计划公告", "symbol": "600519.SH", "source": "eastmoney", "date": "2026-06-01", "event_type": "buyback"},
            {"title": "买入评级报告", "symbol": "002138.SZ", "source": "cninfo", "date": "2026-06-01", "event_type": "rating"},
        ]
        batch = normalize_events(events)
        by_title = {s.title: s.direction for s in batch.signals}
        assert by_title["风险提示公告"] == "bearish"
        assert by_title["减持公告"] == "bearish"
        assert by_title["回购计划公告"] == "bullish"
        assert by_title["买入评级报告"] == "bullish"


class TestIntegrationWithData003:
    def test_event_source_result_to_mandate_signals(self):
        result = EventSourceResult()
        result.items_by_symbol = {
            "600519.SH": [
                EventItem(symbol="600519.SH", title="回购计划公告", event_type="buyback", direction="bullish", date="2026-06-01", source="eastmoney"),
                EventItem(symbol="600519.SH", title="风险提示公告", event_type="notice", direction="bearish", date="2026-06-01", source="eastmoney"),
            ],
            "002138.SZ": [
                EventItem(symbol="002138.SZ", title="中信证券买入评级", event_type="rating", direction="bullish", date="2026-06-01", source="cninfo"),
            ],
        }
        batch = normalize_event_source_result(result)
        assert batch.total_raw == 3
        assert len(batch.signals) == 3
        assert "600519.SH" in batch.by_symbol
        assert "002138.SZ" in batch.by_symbol

    def test_h001_pipeline_compatible(self):
        batch = normalize_raw_dicts([
            {"title": "国务院关于低空经济的意见", "symbol": "002138.SZ", "source": "xinhua", "date": "2026-06-01"},
        ])
        assert len(batch.signals) == 1
        sig = batch.signals[0]
        assert isinstance(sig, MandateSignal)
        assert sig.is_high_authority()

    def test_batch_statistics_complete(self):
        events = [
            {"title": "国务院关于意见", "symbol": "A", "source": "xinhua", "date": "2026-06-01"},
            {"title": "回购计划", "symbol": "B", "source": "eastmoney", "date": "2026-06-01", "event_type": "buyback"},
            {"title": "华泰证券研报", "symbol": "C", "source": "华泰证券", "date": "2026-06-01", "event_type": "rating"},
        ]
        batch = normalize_events(events)
        d = batch.to_dict()
        assert d["total_raw"] == 3
        assert d["total_normalized"] == 3
        assert d["symbols_count"] == 3
        assert d["by_source_level"]["STATE_COUNCIL"] == 1
        assert d["by_source_level"]["COMPANY_NOTICE"] == 1
        assert d["by_source_level"]["MEDIA"] == 1

    def test_cross_source_dedup_keeps_highest(self):
        events = [
            {"title": "低空经济政策重大突破", "symbol": "002138.SZ", "source": "新浪财经", "date": "2026-06-01"},
            {"title": "低空经济政策重大突破", "symbol": "002138.SZ", "source": "财联社", "date": "2026-06-01"},
            {"title": "低空经济政策重大突破", "symbol": "002138.SZ", "source": "第一财经", "date": "2026-06-01"},
        ]
        batch = normalize_events(events)
        assert len(batch.signals) == 1
        assert len(batch.signals[0].raw_refs) == 3
        assert batch.duplicates_removed == 2

    def test_no_evidence_low_confidence(self):
        events = [
            {"title": "某事件", "symbol": "A"},
        ]
        batch = normalize_events(events)
        assert len(batch.signals) == 1
        assert batch.signals[0].confidence <= 0.3


class TestEdgeCases:
    def test_none_in_list(self):
        events = [None]
        batch = normalize_events(events)
        assert len(batch.signals) == 0

    def test_missing_all_optional_fields(self):
        events = [{"title": "仅有标题"}]
        batch = normalize_events(events)
        assert len(batch.signals) == 1
        assert batch.signals[0].confidence <= 0.3

    def test_very_long_title(self):
        long_title = "A" * 500
        events = [{"title": long_title, "symbol": "A", "source": "s1", "date": "2026-06-01"}]
        batch = normalize_events(events)
        assert len(batch.signals) == 1

    def test_unicode_title(self):
        events = [{"title": "关于🎉低空经济🚀的公告", "symbol": "A", "source": "s1", "date": "2026-06-01"}]
        batch = normalize_events(events)
        assert len(batch.signals) == 1

    def test_duplicate_with_different_whitespace(self):
        events = [
            {"title": "低空经济政策发布", "symbol": "A", "source": "s1", "date": "2026-06-01"},
            {"title": "  低空经济 政策发布 ", "symbol": "A", "source": "s2", "date": "2026-06-01"},
        ]
        batch = normalize_events(events)
        assert len(batch.signals) == 1

    def test_large_batch(self):
        events = []
        for i in range(200):
            events.append({
                "title": f"事件_{i}",
                "symbol": f"SYM{i % 10}",
                "source": f"source_{i % 5}",
                "date": "2026-06-01",
            })
        batch = normalize_events(events)
        assert len(batch.signals) == 200
        assert len(batch.by_symbol) == 10
