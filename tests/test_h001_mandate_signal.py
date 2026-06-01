# [H-001] mandate_signal_model
"""Tests for mandate_signal module — 昊天雷达 v0 data model and signal classification."""

import pytest

from tradingagents.tradeflow.mandate_signal import (
    MandateSignal,
    MandateEventType,
    SourceLevel,
    classify_source_level,
    classify_event_type,
    compute_confidence,
    event_item_to_mandate_signal,
    convert_event_items,
    validate_mandate_signal,
    _SOURCE_LEVEL_WEIGHT,
    _MAX_CONFIDENCE_LOW_EVIDENCE,
)
from tradingagents.tradeflow.event_source import EventItem


# ── SourceLevel enum ──


class TestSourceLevelEnum:
    def test_all_values(self):
        expected = {
            "CENTRAL", "STATE_COUNCIL", "MINISTRY", "LOCAL_GOV",
            "EXCHANGE", "SOE_GROUP", "COMPANY_NOTICE", "MEDIA",
        }
        assert {e.value for e in SourceLevel} == expected

    def test_eight_levels(self):
        assert len(SourceLevel) == 8

    def test_from_string(self):
        assert SourceLevel("CENTRAL") is SourceLevel.CENTRAL

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            SourceLevel("INVALID")


# ── MandateEventType enum ──


class TestMandateEventTypeEnum:
    def test_all_values(self):
        expected = {
            "POLICY_DOCUMENT", "MEETING_SIGNAL", "INDUSTRY_PLAN",
            "SUBSIDY_SUPPORT", "PROCUREMENT_ORDER", "LICENSE_APPROVAL",
            "M_AND_A_RESTRUCTURING", "SOE_REFORM", "BUYBACK_RATING",
        }
        assert {e.value for e in MandateEventType} == expected

    def test_nine_types(self):
        assert len(MandateEventType) == 9


# ── Source level classification ──


class TestClassifySourceLevel:
    def test_central_dangzhongyang(self):
        assert classify_source_level("中共中央关于若干重大问题的决定") == SourceLevel.CENTRAL

    def test_state_council_guowuyuan(self):
        assert classify_source_level("国务院关于加强低空经济的意见") == SourceLevel.STATE_COUNCIL

    def test_state_council_guoban(self):
        assert classify_source_level("国办发[2026]1号文件") == SourceLevel.STATE_COUNCIL

    def test_ministry_gongxinbu(self):
        assert classify_source_level("工信部印发机器人产业发展规划") == SourceLevel.MINISTRY

    def test_ministry_zhengjianhui(self):
        assert classify_source_level("证监会发布上市公司并购重组规则") == SourceLevel.MINISTRY

    def test_ministry_fagaiwei(self):
        assert classify_source_level("发改委关于算力基础设施建设的通知") == SourceLevel.MINISTRY

    def test_exchange_sse(self):
        assert classify_source_level("上交所关于股票异常波动的监管函") == SourceLevel.EXCHANGE

    def test_soe_group_guozijian(self):
        assert classify_source_level("国资委推动央企深化改革") == SourceLevel.SOE_GROUP

    def test_local_gov_province(self):
        assert classify_source_level("广东省人民政府关于促进低空经济发展的措施") == SourceLevel.LOCAL_GOV

    def test_local_gov_city(self):
        assert classify_source_level("深圳市政府发布低空经济行动计划") == SourceLevel.LOCAL_GOV

    def test_company_notice(self):
        assert classify_source_level("某某公司：董事会关于重大事项的公告") == SourceLevel.COMPANY_NOTICE

    def test_media_dongcai(self):
        assert classify_source_level("东财：机器人板块掀涨停潮") == SourceLevel.MEDIA

    def test_media_cailianshe(self):
        assert classify_source_level("财联社报道低空经济政策") == SourceLevel.MEDIA

    def test_unknown_defaults_to_media(self):
        assert classify_source_level("某公司发布新产品") == SourceLevel.MEDIA

    def test_empty_title_and_source(self):
        assert classify_source_level("", "") == SourceLevel.MEDIA

    def test_source_param_used(self):
        assert classify_source_level("", "巨潮资讯") == SourceLevel.COMPANY_NOTICE

    def test_priority_central_over_media(self):
        assert classify_source_level("党中央召开重要会议，媒体报道") == SourceLevel.CENTRAL

    def test_priority_state_council_over_local(self):
        assert classify_source_level("国务院和广东省联合发布政策") == SourceLevel.STATE_COUNCIL


# ── Event type classification ──


class TestClassifyEventType:
    def test_policy_document_guideline(self):
        assert classify_event_type("算力基础设施发展指导意见") == MandateEventType.POLICY_DOCUMENT

    def test_policy_document_action_plan(self):
        assert classify_event_type("机器人产业三年行动计划") == MandateEventType.POLICY_DOCUMENT

    def test_meeting_signal(self):
        assert classify_event_type("国务院常务会议研究低空经济") == MandateEventType.MEETING_SIGNAL

    def test_industry_plan(self):
        assert classify_event_type("低空经济产业政策发布") == MandateEventType.INDUSTRY_PLAN

    def test_subsidy_support(self):
        assert classify_event_type("财政部发布算力补贴政策") == MandateEventType.SUBSIDY_SUPPORT

    def test_procurement_order(self):
        assert classify_event_type("某公司中标智慧城市采购项目") == MandateEventType.PROCUREMENT_ORDER

    def test_license_approval(self):
        assert classify_event_type("某公司获得低空飞行许可证") == MandateEventType.LICENSE_APPROVAL

    def test_m_and_a_restructuring(self):
        assert classify_event_type("某公司发布重大资产重组方案") == MandateEventType.M_AND_A_RESTRUCTURING

    def test_soe_reform(self):
        assert classify_event_type("国资委推进央企混改") == MandateEventType.SOE_REFORM

    def test_buyback_rating(self):
        assert classify_event_type("某公司公告回购计划") == MandateEventType.BUYBACK_RATING

    def test_empty_defaults_to_policy_document(self):
        assert classify_event_type("") == MandateEventType.POLICY_DOCUMENT

    def test_no_match_defaults_to_policy_document(self):
        assert classify_event_type("某公司发布新产品") == MandateEventType.POLICY_DOCUMENT


# ── Confidence computation ──


class TestComputeConfidence:
    def test_high_authority_full_evidence(self):
        conf = compute_confidence(
            SourceLevel.STATE_COUNCIL,
            has_title=True, has_date=True, has_source=True, has_evidence_text=True,
        )
        assert conf > 0.5
        assert conf <= 1.0

    def test_low_authority_full_evidence(self):
        conf = compute_confidence(
            SourceLevel.MEDIA,
            has_title=True, has_date=True, has_source=True, has_evidence_text=True,
        )
        assert conf < compute_confidence(
            SourceLevel.STATE_COUNCIL,
            has_title=True, has_date=True, has_source=True, has_evidence_text=True,
        )

    def test_missing_title_caps_confidence(self):
        conf = compute_confidence(
            SourceLevel.STATE_COUNCIL,
            has_title=False, has_date=True, has_source=True,
        )
        assert conf <= _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_missing_date_caps_confidence(self):
        conf = compute_confidence(
            SourceLevel.MINISTRY,
            has_title=True, has_date=False, has_source=True,
        )
        assert conf <= _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_missing_source_caps_confidence(self):
        conf = compute_confidence(
            SourceLevel.CENTRAL,
            has_title=True, has_date=True, has_source=False,
        )
        assert conf <= _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_all_missing_zero(self):
        conf = compute_confidence(
            SourceLevel.MEDIA,
            has_title=False, has_date=False, has_source=False,
        )
        assert conf == 0.0

    def test_clamped_between_0_and_1(self):
        conf = compute_confidence(
            SourceLevel.CENTRAL,
            has_title=True, has_date=True, has_source=True, has_evidence_text=True,
        )
        assert 0.0 <= conf <= 1.0

    def test_authority_hierarchy(self):
        levels = [SourceLevel.CENTRAL, SourceLevel.STATE_COUNCIL, SourceLevel.MINISTRY,
                   SourceLevel.LOCAL_GOV, SourceLevel.COMPANY_NOTICE, SourceLevel.MEDIA]
        confs = [
            compute_confidence(l, has_title=True, has_date=True, has_source=True)
            for l in levels
        ]
        for i in range(len(confs) - 1):
            assert confs[i] >= confs[i + 1]


# ── MandateSignal dataclass ──


class TestMandateSignal:
    def test_default_values(self):
        sig = MandateSignal()
        assert sig.symbol == ""
        assert sig.confidence == 0.0
        assert sig.direction == "neutral"
        assert sig.policy_tags == []
        assert sig.industry_tags == []
        assert sig.raw_refs == []

    def test_auto_clamp_confidence_high_with_evidence(self):
        sig = MandateSignal(
            confidence=1.5,
            title="test", source="s", date="2026-06-01",
        )
        assert sig.confidence == 1.0

    def test_auto_clamp_confidence_high_without_evidence(self):
        sig = MandateSignal(confidence=1.5)
        assert sig.confidence <= _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_auto_clamp_confidence_negative(self):
        sig = MandateSignal(confidence=-0.5)
        assert sig.confidence == 0.0

    def test_low_confidence_without_minimum_evidence(self):
        sig = MandateSignal(
            symbol="600519.SH",
            confidence=0.9,
            source_level="STATE_COUNCIL",
        )
        assert sig.confidence <= _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_high_confidence_with_full_evidence(self):
        sig = MandateSignal(
            symbol="600519.SH",
            title="国务院关于促进低空经济发展的意见",
            source="gov.cn",
            date="2026-06-01",
            source_level="STATE_COUNCIL",
            confidence=0.8,
        )
        assert sig.confidence > _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_source_level_from_enum(self):
        sig = MandateSignal(source_level=SourceLevel.CENTRAL)
        assert sig.source_level == "CENTRAL"

    def test_event_type_from_enum(self):
        sig = MandateSignal(event_type=MandateEventType.MEETING_SIGNAL)
        assert sig.event_type == "MEETING_SIGNAL"

    def test_source_level_enum_property(self):
        sig = MandateSignal(source_level="CENTRAL")
        assert sig.source_level_enum is SourceLevel.CENTRAL

    def test_source_level_enum_property_invalid(self):
        sig = MandateSignal(source_level="INVALID")
        assert sig.source_level_enum is None

    def test_event_type_enum_property(self):
        sig = MandateSignal(event_type="POLICY_DOCUMENT")
        assert sig.event_type_enum is MandateEventType.POLICY_DOCUMENT

    def test_event_type_enum_property_invalid(self):
        sig = MandateSignal(event_type="INVALID")
        assert sig.event_type_enum is None

    def test_source_level_weight_central(self):
        sig = MandateSignal(source_level="CENTRAL")
        assert sig.source_level_weight == _SOURCE_LEVEL_WEIGHT[SourceLevel.CENTRAL]

    def test_source_level_weight_invalid(self):
        sig = MandateSignal(source_level="INVALID")
        assert sig.source_level_weight == 0.0

    def test_is_high_authority(self):
        assert MandateSignal(source_level="CENTRAL").is_high_authority() is True
        assert MandateSignal(source_level="MINISTRY").is_high_authority() is True
        assert MandateSignal(source_level="MEDIA").is_high_authority() is False
        assert MandateSignal(source_level="LOCAL_GOV").is_high_authority() is False

    def test_to_dict(self):
        sig = MandateSignal(
            symbol="600519.SH",
            title="test title",
            source="eastmoney",
            date="2026-06-01",
            source_level="CENTRAL",
            confidence=0.5,
            policy_tags=["算力"],
        )
        d = sig.to_dict()
        assert d["symbol"] == "600519.SH"
        assert d["title"] == "test title"
        assert d["source_level"] == "CENTRAL"
        assert d["confidence"] == 0.5
        assert d["policy_tags"] == ["算力"]

    def test_to_dict_round_trip(self):
        sig = MandateSignal(
            symbol="000001.SZ",
            title="test",
            source="eastmoney",
            date="2026-06-01",
            source_level="MINISTRY",
            event_type="MEETING_SIGNAL",
            confidence=0.6,
            raw_refs=[{"k": "v"}],
        )
        d = sig.to_dict()
        assert d["raw_refs"] == [{"k": "v"}]


# ── EventItem → MandateSignal conversion ──


class TestEventItemToMandateSignal:
    def test_state_council_event(self):
        item = EventItem(
            symbol="600519.SH",
            name="贵州茅台",
            event_type="notice",
            title="国务院关于促进低空经济发展的意见",
            direction="bullish",
            date="2026-06-01",
            source="gov.cn",
        )
        sig = event_item_to_mandate_signal(item)
        assert sig.symbol == "600519.SH"
        assert sig.source_level == "STATE_COUNCIL"
        assert sig.confidence > _MAX_CONFIDENCE_LOW_EVIDENCE
        assert sig.direction == "bullish"
        assert sig.title == item.title
        assert len(sig.raw_refs) == 1
        assert sig.raw_refs[0]["original_title"] == item.title

    def test_ministry_event(self):
        item = EventItem(
            symbol="002138.SZ",
            name="顺络电子",
            event_type="notice",
            title="工信部发布半导体产业发展规划",
            direction="bullish",
            date="2026-06-01",
            source="miit.gov.cn",
        )
        sig = event_item_to_mandate_signal(item)
        assert sig.source_level == "MINISTRY"
        assert sig.is_high_authority() is True

    def test_local_gov_event(self):
        item = EventItem(
            symbol="300001.SZ",
            name="特锐德",
            event_type="notice",
            title="深圳市政府发布新能源补贴政策",
            direction="bullish",
            date="2026-06-01",
            source="sz.gov.cn",
        )
        sig = event_item_to_mandate_signal(item)
        assert sig.source_level == "LOCAL_GOV"
        assert sig.is_high_authority() is False

    def test_company_notice_event(self):
        item = EventItem(
            symbol="600036.SH",
            name="招商银行",
            event_type="notice",
            title="招商银行：董事会关于回购股份的公告",
            direction="bullish",
            date="2026-06-01",
            source="eastmoney",
        )
        sig = event_item_to_mandate_signal(item)
        assert sig.source_level == "COMPANY_NOTICE"
        assert sig.is_high_authority() is False

    def test_media_event(self):
        item = EventItem(
            symbol="600519.SH",
            name="贵州茅台",
            event_type="notice",
            title="东财：白酒板块资金流入明显",
            direction="neutral",
            date="2026-06-01",
            source="eastmoney",
        )
        sig = event_item_to_mandate_signal(item)
        assert sig.source_level == "MEDIA"
        assert sig.is_high_authority() is False

    def test_preserves_original_data(self):
        item = EventItem(
            symbol="600519.SH",
            name="贵州茅台",
            event_type="buyback",
            title="回购计划公告",
            direction="bullish",
            date="2026-06-01",
            source="eastmoney",
            detail={"url": "http://example.com/123", "amount": "10亿"},
        )
        sig = event_item_to_mandate_signal(item)
        assert sig.evidence_url == "http://example.com/123"
        assert sig.raw_refs[0]["original_event_type"] == "buyback"
        assert sig.raw_refs[0]["original_direction"] == "bullish"
        assert "url" in sig.raw_refs[0]["detail_keys"]
        assert "amount" in sig.raw_refs[0]["detail_keys"]

    def test_empty_title_skipped(self):
        item = EventItem(symbol="600519.SH", title="", source="eastmoney")
        sig = event_item_to_mandate_signal(item)
        assert sig.title == ""

    def test_missing_fields_low_confidence(self):
        item = EventItem(
            symbol="600519.SH",
            title="",
            source="",
            date="",
        )
        sig = event_item_to_mandate_signal(item)
        assert sig.confidence <= _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_direction_override(self):
        item = EventItem(
            symbol="600519.SH",
            title="test",
            direction="bearish",
            source="eastmoney",
            date="2026-06-01",
        )
        sig = event_item_to_mandate_signal(item, direction="bullish")
        assert sig.direction == "bullish"


# ── Symbol isolation ──


class TestSymbolIsolation:
    def test_events_by_symbol_no_cross_contamination(self):
        items = {
            "600519.SH": [
                EventItem(symbol="600519.SH", title="茅台公告", source="eastmoney", date="2026-06-01"),
            ],
            "000001.SZ": [
                EventItem(symbol="000001.SZ", title="平安公告", source="eastmoney", date="2026-06-01"),
            ],
        }
        result = convert_event_items(items)
        assert "600519.SH" in result
        assert "000001.SZ" in result
        assert len(result["600519.SH"]) == 1
        assert len(result["000001.SZ"]) == 1
        assert result["600519.SH"][0].title == "茅台公告"
        assert result["000001.SZ"][0].title == "平安公告"

    def test_empty_symbol_excluded(self):
        items = {
            "": [
                EventItem(symbol="", title="test", source="eastmoney", date="2026-06-01"),
            ],
            "600519.SH": [
                EventItem(symbol="600519.SH", title="valid", source="eastmoney", date="2026-06-01"),
            ],
        }
        result = convert_event_items(items)
        assert "" not in result
        assert "600519.SH" in result

    def test_no_title_items_excluded(self):
        items = {
            "600519.SH": [
                EventItem(symbol="600519.SH", title="", source="eastmoney", date="2026-06-01"),
                EventItem(symbol="600519.SH", title="valid title", source="eastmoney", date="2026-06-01"),
            ],
        }
        result = convert_event_items(items)
        assert len(result["600519.SH"]) == 1
        assert result["600519.SH"][0].title == "valid title"

    def test_empty_input(self):
        result = convert_event_items({})
        assert result == {}


# ── Validation ──


class TestValidateMandateSignal:
    def test_valid_signal_no_issues(self):
        sig = MandateSignal(
            symbol="600519.SH",
            title="test title",
            source="eastmoney",
            date="2026-06-01",
            source_level="MEDIA",
            event_type="POLICY_DOCUMENT",
        )
        issues = validate_mandate_signal(sig)
        assert issues == []

    def test_missing_symbol(self):
        sig = MandateSignal(title="test", source="s", date="d")
        issues = validate_mandate_signal(sig)
        assert any("missing symbol" in i for i in issues)

    def test_missing_title(self):
        sig = MandateSignal(symbol="600519.SH", source="s", date="d")
        issues = validate_mandate_signal(sig)
        assert any("missing title" in i for i in issues)

    def test_missing_source(self):
        sig = MandateSignal(symbol="600519.SH", title="t", date="d")
        issues = validate_mandate_signal(sig)
        assert any("missing source" in i for i in issues)

    def test_missing_date(self):
        sig = MandateSignal(symbol="600519.SH", title="t", source="s")
        issues = validate_mandate_signal(sig)
        assert any("missing date" in i for i in issues)

    def test_missing_source_level(self):
        sig = MandateSignal(
            symbol="600519.SH", title="t", source="s", date="d",
        )
        issues = validate_mandate_signal(sig)
        assert any("missing source_level" in i for i in issues)


# ── Source level weights ──


class TestSourceLevelWeights:
    def test_central_highest(self):
        assert _SOURCE_LEVEL_WEIGHT[SourceLevel.CENTRAL] == 1.0

    def test_media_lowest(self):
        assert _SOURCE_LEVEL_WEIGHT[SourceLevel.MEDIA] == 0.15

    def test_hierarchy_ordering(self):
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
            assert _SOURCE_LEVEL_WEIGHT[order[i]] > _SOURCE_LEVEL_WEIGHT[order[i + 1]]

    def test_all_levels_have_weight(self):
        for level in SourceLevel:
            assert level in _SOURCE_LEVEL_WEIGHT
            assert _SOURCE_LEVEL_WEIGHT[level] > 0


# ── Integration: full pipeline with four event types ──


class TestH001FourEventTypes:
    def _make_items(self):
        return {
            "600519.SH": [
                EventItem(
                    symbol="600519.SH",
                    title="国务院关于促进低空经济发展的意见",
                    source="gov.cn",
                    date="2026-06-01",
                    direction="bullish",
                    event_type="notice",
                ),
            ],
            "002138.SZ": [
                EventItem(
                    symbol="002138.SZ",
                    title="工信部发布半导体产业发展规划",
                    source="miit.gov.cn",
                    date="2026-06-01",
                    direction="bullish",
                    event_type="notice",
                ),
            ],
            "300001.SZ": [
                EventItem(
                    symbol="300001.SZ",
                    title="深圳市政府发布新能源补贴政策",
                    source="sz.gov.cn",
                    date="2026-06-01",
                    direction="bullish",
                    event_type="notice",
                ),
            ],
            "600036.SH": [
                EventItem(
                    symbol="600036.SH",
                    title="招商银行：董事会关于回购股份的公告",
                    source="eastmoney",
                    date="2026-06-01",
                    direction="bullish",
                    event_type="buyback",
                ),
            ],
        }

    def test_state_council_signal(self):
        items = self._make_items()
        result = convert_event_items(items)
        sig = result["600519.SH"][0]
        assert sig.source_level == "STATE_COUNCIL"
        assert sig.is_high_authority() is True
        assert sig.confidence > _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_ministry_signal(self):
        items = self._make_items()
        result = convert_event_items(items)
        sig = result["002138.SZ"][0]
        assert sig.source_level == "MINISTRY"
        assert sig.is_high_authority() is True
        assert sig.confidence > _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_local_gov_signal(self):
        items = self._make_items()
        result = convert_event_items(items)
        sig = result["300001.SZ"][0]
        assert sig.source_level == "LOCAL_GOV"
        assert sig.is_high_authority() is False

    def test_company_notice_signal(self):
        items = self._make_items()
        result = convert_event_items(items)
        sig = result["600036.SH"][0]
        assert sig.source_level == "COMPANY_NOTICE"
        assert sig.is_high_authority() is False

    def test_no_cross_contamination(self):
        items = self._make_items()
        result = convert_event_items(items)
        for sym, signals in result.items():
            for s in signals:
                assert s.symbol == sym

    def test_confidence_ordering(self):
        items = self._make_items()
        result = convert_event_items(items)
        state_council_conf = result["600519.SH"][0].confidence
        ministry_conf = result["002138.SZ"][0].confidence
        local_conf = result["300001.SZ"][0].confidence
        company_conf = result["600036.SH"][0].confidence
        assert state_council_conf >= ministry_conf >= local_conf >= company_conf

    def test_all_signals_have_raw_refs(self):
        items = self._make_items()
        result = convert_event_items(items)
        for sym, signals in result.items():
            for s in signals:
                assert len(s.raw_refs) == 1
                assert s.raw_refs[0]["original_title"]

    def test_all_signals_valid(self):
        items = self._make_items()
        result = convert_event_items(items)
        for sym, signals in result.items():
            for s in signals:
                issues = validate_mandate_signal(s)
                critical = [i for i in issues if "missing" in i and "source_level" not in i and "event_type" not in i]
                assert critical == [], f"{sym} has issues: {issues}"


# ── No-evidence signal cannot be high confidence ──


class TestNoEvidenceLowConfidence:
    def test_no_source_no_date_no_title_low_confidence(self):
        sig = MandateSignal(
            symbol="600519.SH",
            source_level="CENTRAL",
            event_type="POLICY_DOCUMENT",
            confidence=0.9,
        )
        assert sig.confidence <= _MAX_CONFIDENCE_LOW_EVIDENCE

    def test_media_with_evidence_still_lower_than_central_with_evidence(self):
        media_conf = compute_confidence(
            SourceLevel.MEDIA,
            has_title=True, has_date=True, has_source=True,
        )
        central_conf = compute_confidence(
            SourceLevel.CENTRAL,
            has_title=True, has_date=True, has_source=True,
        )
        assert media_conf < central_conf

    def test_company_notice_cannot_exceed_state_council(self):
        company_conf = compute_confidence(
            SourceLevel.COMPANY_NOTICE,
            has_title=True, has_date=True, has_source=True,
        )
        state_conf = compute_confidence(
            SourceLevel.STATE_COUNCIL,
            has_title=True, has_date=True, has_source=True,
        )
        assert company_conf < state_conf
