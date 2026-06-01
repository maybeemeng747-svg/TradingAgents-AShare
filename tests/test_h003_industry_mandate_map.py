# [H-003] mandate_beneficiary_map
"""Tests for industry_mandate_map module — industry chain mapping and company role classification."""

import pytest

from tradingagents.tradeflow.mandate_signal import MandateSignal, SourceLevel
from tradingagents.tradeflow.industry_mandate_map import (
    CompanyRole,
    IndustryChainLink,
    BeneficiaryPathResult,
    _INDUSTRY_CHAIN_MAP,
    _ROLE_KEYWORD_PATTERNS,
    _WEAK_ROLE_INDICATORS,
    get_industry_chain,
    get_all_topics,
    classify_company_role,
    compute_beneficiary_path,
    compute_beneficiary_paths_for_signals,
)


# ── CompanyRole enum ──


class TestCompanyRoleEnum:
    def test_all_values(self):
        expected = {
            "LEADER", "CORE_SUPPLIER", "INFRA_PROVIDER",
            "APPLICATION_SCENE", "PERIPHERAL", "CONCEPT_ONLY", "UNKNOWN",
        }
        assert {e.value for e in CompanyRole} == expected

    def test_seven_roles(self):
        assert len(CompanyRole) == 7

    def test_from_string(self):
        assert CompanyRole("LEADER") is CompanyRole.LEADER

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            CompanyRole("INVALID")


# ── IndustryChainLink ──


class TestIndustryChainLink:
    def test_default(self):
        link = IndustryChainLink(segment="test", keywords=["a"])
        assert link.segment == "test"
        assert link.keywords == ["a"]
        assert link.roles == []

    def test_to_dict(self):
        link = IndustryChainLink(segment="整机", keywords=["eVTOL"], roles=["LEADER"])
        d = link.to_dict()
        assert d["segment"] == "整机"
        assert d["keywords"] == ["eVTOL"]
        assert d["roles"] == ["LEADER"]


# ── Industry chain map structure ──


class TestIndustryChainMap:
    def test_topics_covered(self):
        expected = [
            "低空经济", "机器人", "算力", "军工", "半导体",
            "AI应用", "新质生产力", "数据要素", "国产替代",
            "并购重组", "国企改革", "出海", "中特估",
        ]
        for t in expected:
            assert t in _INDUSTRY_CHAIN_MAP, f"missing topic: {t}"

    def test_thirteen_topics(self):
        assert len(_INDUSTRY_CHAIN_MAP) == 13

    def test_low_altitude_economy_chain(self):
        chain = get_industry_chain("低空经济")
        segments = [link.segment for link in chain]
        assert "整机" in segments
        assert "空管" in segments
        assert "运营" in segments

    def test_robot_chain(self):
        chain = get_industry_chain("机器人")
        segments = [link.segment for link in chain]
        assert "减速器" in segments
        assert "伺服" in segments
        assert "控制器" in segments
        assert "本体" in segments

    def test_compute_chain(self):
        chain = get_industry_chain("算力")
        segments = [link.segment for link in chain]
        assert "芯片" in segments
        assert "服务器" in segments
        assert "液冷" in segments
        assert "IDC" in segments
        assert "光模块" in segments

    def test_military_chain(self):
        chain = get_industry_chain("军工")
        segments = [link.segment for link in chain]
        assert "航空" in segments
        assert "导弹" in segments

    def test_semiconductor_chain(self):
        chain = get_industry_chain("半导体")
        segments = [link.segment for link in chain]
        assert "设计" in segments
        assert "制造" in segments
        assert "封测" in segments

    def test_each_link_has_keywords(self):
        for topic, links in _INDUSTRY_CHAIN_MAP.items():
            for link in links:
                assert link.keywords, f"empty keywords for {topic}/{link.segment}"
                assert isinstance(link.keywords, list)

    def test_each_link_has_roles(self):
        for topic, links in _INDUSTRY_CHAIN_MAP.items():
            for link in links:
                assert link.roles, f"empty roles for {topic}/{link.segment}"

    def test_unknown_topic_returns_empty(self):
        assert get_industry_chain("量子计算") == []

    def test_all_roles_are_valid(self):
        for topic, links in _INDUSTRY_CHAIN_MAP.items():
            for link in links:
                for role_str in link.roles:
                    CompanyRole(role_str)


# ── get_all_topics ──


class TestGetAllTopics:
    def test_returns_sorted(self):
        topics = get_all_topics()
        assert topics == sorted(topics)

    def test_includes_key_topics(self):
        topics = get_all_topics()
        assert "低空经济" in topics
        assert "机器人" in topics
        assert "算力" in topics


# ── classify_company_role ──


class TestClassifyCompanyRole:
    def test_leader_keyword(self):
        assert classify_company_role("该公司是低空经济龙头企业") == CompanyRole.LEADER

    def test_core_supplier_keyword(self):
        assert classify_company_role("公司是机器人核心供应商") == CompanyRole.CORE_SUPPLIER

    def test_infra_provider_keyword(self):
        assert classify_company_role("算力基础设施提供商") == CompanyRole.INFRA_PROVIDER

    def test_application_scene_keyword(self):
        assert classify_company_role("机器人应用场景落地") == CompanyRole.APPLICATION_SCENE

    def test_concept_only_weak_word(self):
        assert classify_company_role("公司涉足低空经济领域") == CompanyRole.CONCEPT_ONLY

    def test_concept_only_layout(self):
        assert classify_company_role("公司布局AI应用") == CompanyRole.CONCEPT_ONLY

    def test_unknown_no_match(self):
        assert classify_company_role("公司发布季度报告") == CompanyRole.UNKNOWN

    def test_empty_string(self):
        assert classify_company_role("") == CompanyRole.UNKNOWN

    def test_with_tags(self):
        assert classify_company_role("", tags=["核心供应商"]) == CompanyRole.CORE_SUPPLIER

    def test_title_and_tags_combined(self):
        role = classify_company_role("低空经济", tags=["龙头企业"])
        assert role == CompanyRole.LEADER

    def test_priority_order(self):
        assert classify_company_role("龙头企业核心供应商") == CompanyRole.CORE_SUPPLIER

    def test_leader_wins_over_generic(self):
        assert classify_company_role("公司是行业领军企业") == CompanyRole.LEADER

    def test_concept_only_attention(self):
        assert classify_company_role("公司关注机器人发展") == CompanyRole.CONCEPT_ONLY


# ── compute_beneficiary_path ──


def _make_signal(
    title: str = "",
    symbol: str = "",
    source_level: str = "MINISTRY",
    confidence: float = 0.7,
    topic: str = "",
    industry_tags: list = None,
    evidence_text: str = "",
) -> MandateSignal:
    return MandateSignal(
        symbol=symbol,
        topic=topic,
        title=title,
        source="test_source",
        source_level=source_level,
        date="2026-06-01",
        evidence_text=evidence_text or title,
        confidence=confidence,
        industry_tags=industry_tags or [],
    )


class TestComputeBeneficiaryPath:
    def test_empty_signals(self):
        result = compute_beneficiary_path("低空经济", [])
        assert result.company_role == CompanyRole.UNKNOWN.value
        assert result.beneficiary_path == []
        assert "no_signals" in result.path_reasons

    def test_empty_topic(self):
        sig = _make_signal(title="某公司公告", symbol="000001.SZ")
        result = compute_beneficiary_path("", [sig])
        assert result.company_role == CompanyRole.UNKNOWN.value
        assert "no_topic" in result.path_reasons

    def test_low_altitude_core_supplier(self):
        sig = _make_signal(
            title="某公司获eVTOL整机核心零部件订单",
            symbol="300001.SZ",
            topic="低空经济",
            industry_tags=["eVTOL", "飞行器"],
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="300001.SZ")
        assert len(result.beneficiary_path) > 0
        assert result.mandate_topic == "低空经济"
        assert result.has_company_evidence is True
        assert result.path_confidence > 0.0
        assert len(result.mandate_evidence_refs) > 0

    def test_robot_core_supplier(self):
        sig = _make_signal(
            title="某公司谐波减速器出货量行业第一",
            symbol="002001.SZ",
            topic="机器人",
            industry_tags=["减速器"],
        )
        result = compute_beneficiary_path("机器人", [sig], symbol="002001.SZ")
        assert "减速器" in result.beneficiary_path
        assert result.has_company_evidence is True

    def test_compute_power_chain(self):
        sig = _make_signal(
            title="国产AI芯片算力突破",
            symbol="688001.SH",
            topic="算力",
            industry_tags=["AI芯片", "GPU"],
        )
        result = compute_beneficiary_path("算力", [sig], symbol="688001.SH")
        assert "芯片" in result.beneficiary_path
        assert result.has_company_evidence is True

    def test_concept_only_weak_word(self):
        sig = _make_signal(
            title="公司涉足低空经济布局",
            symbol="000002.SZ",
            source_level="COMPANY_NOTICE",
            confidence=0.4,
            topic="低空经济",
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="000002.SZ")
        assert result.company_role == CompanyRole.CONCEPT_ONLY.value

    def test_concept_only_exploring(self):
        sig = _make_signal(
            title="公司探索机器人领域",
            symbol="000003.SZ",
            source_level="COMPANY_NOTICE",
            confidence=0.3,
            topic="机器人",
        )
        result = compute_beneficiary_path("机器人", [sig], symbol="000003.SZ")
        assert result.company_role in (
            CompanyRole.CONCEPT_ONLY.value,
            CompanyRole.UNKNOWN.value,
        )

    def test_no_company_evidence_concept_only(self):
        sig = _make_signal(
            title="国务院发布低空经济发展规划",
            symbol="",
            source_level="STATE_COUNCIL",
            confidence=0.9,
            topic="低空经济",
        )
        result = compute_beneficiary_path("低空经济", [sig])
        assert result.has_company_evidence is False
        assert result.company_role in (
            CompanyRole.CONCEPT_ONLY.value,
            CompanyRole.UNKNOWN.value,
        )
        assert "no_company_evidence" in " ".join(result.path_reasons)

    def test_media_only_low_confidence(self):
        sig = _make_signal(
            title="某媒体报道低空经济概念股",
            symbol="000004.SZ",
            source_level="MEDIA",
            confidence=0.15,
            topic="低空经济",
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="000004.SZ")
        assert result.path_confidence <= 0.5

    def test_evidence_refs_populated(self):
        sig = _make_signal(
            title="某公司获飞行器整机订单",
            symbol="300002.SZ",
            topic="低空经济",
            evidence_text="整机订单",
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="300002.SZ")
        assert len(result.mandate_evidence_refs) >= 1
        ref = result.mandate_evidence_refs[0]
        assert "title" in ref
        assert ref["title"] == sig.title

    def test_multiple_segments_matched(self):
        sigs = [
            _make_signal(
                title="某公司eVTOL整机+空管系统双布局",
                symbol="300003.SZ",
                topic="低空经济",
                industry_tags=["eVTOL", "空管"],
            ),
        ]
        result = compute_beneficiary_path("低空经济", sigs, symbol="300003.SZ")
        assert len(result.beneficiary_path) >= 1

    def test_path_confidence_capped(self):
        sigs = [
            _make_signal(
                title=f"信号{i}",
                symbol="300004.SZ",
                topic="算力",
                industry_tags=["AI芯片", "GPU", "液冷", "服务器"],
                confidence=0.9,
            )
            for i in range(5)
        ]
        result = compute_beneficiary_path("算力", sigs, symbol="300004.SZ")
        assert result.path_confidence <= 1.0

    def test_no_company_evidence_caps_confidence(self):
        sig = _make_signal(
            title="国务院算力规划发布",
            symbol="",
            source_level="STATE_COUNCIL",
            confidence=0.9,
            topic="算力",
        )
        result = compute_beneficiary_path("算力", [sig])
        assert result.path_confidence <= 0.4

    def test_unknown_topic(self):
        sig = _make_signal(title="量子计算突破", symbol="000005.SZ")
        result = compute_beneficiary_path("量子计算", [sig], symbol="000005.SZ")
        assert result.beneficiary_path == []
        assert result.chain_segments_matched == []

    def test_result_to_dict(self):
        result = compute_beneficiary_path("低空经济", [])
        d = result.to_dict()
        assert "beneficiary_path" in d
        assert "company_role" in d
        assert "mandate_topic" in d
        assert "path_confidence" in d
        assert isinstance(d["path_confidence"], float)

    def test_beneficiary_path_result_defaults(self):
        r = BeneficiaryPathResult()
        assert r.beneficiary_path == []
        assert r.company_role == CompanyRole.UNKNOWN.value
        assert r.mandate_topic == ""
        assert r.path_confidence == 0.0

    def test_beneficiary_path_result_to_dict(self):
        r = BeneficiaryPathResult(
            beneficiary_path=["整机"],
            company_role="LEADER",
            mandate_topic="低空经济",
            path_confidence=0.85,
        )
        d = r.to_dict()
        assert d["beneficiary_path"] == ["整机"]
        assert d["company_role"] == "LEADER"
        assert d["path_confidence"] == 0.85


# ── Acceptance: core supplier event → clear path ──


class TestH003Acceptance:
    def test_core_supplier_clear_path(self):
        sig = _make_signal(
            title="公司获eVTOL核心零部件订单，金额超10亿",
            symbol="300001.SZ",
            source_level="COMPANY_NOTICE",
            confidence=0.7,
            topic="低空经济",
            industry_tags=["eVTOL", "整机", "零部件"],
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="300001.SZ")
        assert len(result.beneficiary_path) > 0
        assert result.has_company_evidence is True
        assert result.company_role != CompanyRole.UNKNOWN.value

    def test_weak_word_concept_only(self):
        sig = _make_signal(
            title="公司涉足低空经济相关业务",
            symbol="000001.SZ",
            source_level="COMPANY_NOTICE",
            confidence=0.4,
            topic="低空经济",
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="000001.SZ")
        assert result.company_role == CompanyRole.CONCEPT_ONLY.value

    def test_no_company_evidence_not_high_priority(self):
        sig = _make_signal(
            title="国务院发布低空经济发展规划",
            symbol="",
            source_level="STATE_COUNCIL",
            confidence=0.9,
            topic="低空经济",
        )
        result = compute_beneficiary_path("低空经济", [sig])
        assert result.has_company_evidence is False
        assert result.path_confidence <= 0.4
        assert result.company_role in (
            CompanyRole.CONCEPT_ONLY.value,
            CompanyRole.UNKNOWN.value,
        )

    def test_core_supplier_with_segment_match(self):
        sig = _make_signal(
            title="公司谐波减速器出货量增长200%",
            symbol="002001.SZ",
            source_level="COMPANY_NOTICE",
            confidence=0.7,
            topic="机器人",
            industry_tags=["谐波减速器", "减速器"],
        )
        result = compute_beneficiary_path("机器人", [sig], symbol="002001.SZ")
        assert "减速器" in result.beneficiary_path
        assert result.company_role in (
            CompanyRole.CORE_SUPPLIER.value,
            CompanyRole.LEADER.value,
        )

    def test_strong_vs_weak_evidence(self):
        strong = _make_signal(
            title="公司获得eVTOL整机订单",
            symbol="300001.SZ",
            source_level="COMPANY_NOTICE",
            confidence=0.8,
            topic="低空经济",
            industry_tags=["eVTOL"],
        )
        weak = _make_signal(
            title="公司布局低空经济",
            symbol="000001.SZ",
            source_level="MEDIA",
            confidence=0.2,
            topic="低空经济",
        )
        strong_result = compute_beneficiary_path("低空经济", [strong], symbol="300001.SZ")
        weak_result = compute_beneficiary_path("低空经济", [weak], symbol="000001.SZ")
        assert strong_result.path_confidence > weak_result.path_confidence


# ── compute_beneficiary_paths_for_signals ──


class TestComputeBeneficiaryPathsForSignals:
    def test_empty_signals(self):
        result = compute_beneficiary_paths_for_signals([])
        assert result == {}

    def test_single_topic_single_symbol(self):
        sig = _make_signal(
            title="公司eVTOL整机订单",
            symbol="300001.SZ",
            topic="低空经济",
            industry_tags=["eVTOL"],
        )
        result = compute_beneficiary_paths_for_signals([sig])
        assert "低空经济" in result
        assert result["低空经济"].mandate_topic == "低空经济"

    def test_multiple_topics(self):
        sigs = [
            _make_signal(
                title="公司eVTOL订单",
                symbol="300001.SZ",
                topic="低空经济",
                industry_tags=["eVTOL"],
            ),
            _make_signal(
                title="公司谐波减速器出货增长",
                symbol="002001.SZ",
                topic="机器人",
                industry_tags=["减速器"],
            ),
        ]
        result = compute_beneficiary_paths_for_signals(sigs)
        assert "低空经济" in result
        assert "机器人" in result

    def test_auto_topic_detection(self):
        sig = _make_signal(
            title="公司获低空经济飞行器订单",
            symbol="300001.SZ",
            topic="",
            industry_tags=["飞行器"],
        )
        result = compute_beneficiary_paths_for_signals([sig])
        assert len(result) > 0

    def test_multiple_symbols_same_topic(self):
        sigs = [
            _make_signal(
                title="公司A的eVTOL整机订单",
                symbol="300001.SZ",
                topic="低空经济",
                industry_tags=["eVTOL"],
            ),
            _make_signal(
                title="公司B的空管系统中标",
                symbol="002001.SZ",
                topic="低空经济",
                industry_tags=["空管"],
            ),
        ]
        result = compute_beneficiary_paths_for_signals(sigs)
        assert any("低空经济" in key for key in result)


# ── Integration with H-001/H-002 ──


class TestH003Integration:
    def test_h001_signal_to_h003_path(self):
        sig = MandateSignal(
            symbol="300001.SZ",
            topic="低空经济",
            title="公司获eVTOL核心零部件订单",
            source="公司公告",
            source_level="COMPANY_NOTICE",
            date="2026-06-01",
            evidence_text="公司获eVTOL核心零部件订单，金额超10亿",
            confidence=0.7,
            industry_tags=["eVTOL"],
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="300001.SZ")
        assert len(result.beneficiary_path) > 0
        assert result.has_company_evidence is True

    def test_h002_topics_covered(self):
        from tradingagents.tradeflow.mandate_score import _TOPIC_KEYWORDS_V0
        for topic in _TOPIC_KEYWORDS_V0:
            chain = get_industry_chain(topic)
            assert len(chain) > 0, f"H-003 missing chain for H-002 topic: {topic}"

    def test_full_pipeline_policy_to_path(self):
        sigs = [
            MandateSignal(
                symbol="300001.SZ",
                topic="低空经济",
                title="国务院发布低空经济发展规划",
                source="国务院",
                source_level="STATE_COUNCIL",
                date="2026-05-20",
                confidence=0.9,
                event_type="POLICY_DOCUMENT",
            ),
            MandateSignal(
                symbol="300001.SZ",
                topic="低空经济",
                title="公司获eVTOL整机订单",
                source="公司公告",
                source_level="COMPANY_NOTICE",
                date="2026-05-25",
                confidence=0.7,
                industry_tags=["eVTOL"],
            ),
            MandateSignal(
                symbol="300001.SZ",
                topic="低空经济",
                title="工信部召开低空经济座谈会",
                source="工信部",
                source_level="MINISTRY",
                date="2026-06-01",
                confidence=0.8,
            ),
        ]
        from tradingagents.tradeflow.mandate_score import compute_mandate_score
        mandate_result = compute_mandate_score(sigs, topic="低空经济")
        path_result = compute_beneficiary_path("低空经济", sigs, symbol="300001.SZ")

        assert mandate_result.mandate_score > 0
        assert mandate_result.has_policy_document is True
        assert len(path_result.beneficiary_path) > 0
        assert path_result.has_company_evidence is True

    def test_candidate_schema_fields(self):
        from tradingagents.tradeflow.schemas import Candidate
        c = Candidate(
            symbol="300001.SZ",
            beneficiary_path=["整机"],
            company_role="CORE_SUPPLIER",
            mandate_topic="低空经济",
            mandate_evidence_refs=[{"title": "test", "confidence": 0.7}],
        )
        assert c.beneficiary_path == ["整机"]
        assert c.company_role == "CORE_SUPPLIER"
        assert c.mandate_topic == "低空经济"
        assert len(c.mandate_evidence_refs) == 1

    def test_candidate_to_db_round_trip(self):
        from tradingagents.tradeflow.schemas import Candidate
        c = Candidate(
            symbol="300001.SZ",
            beneficiary_path=["整机", "材料"],
            company_role="CORE_SUPPLIER",
            mandate_topic="低空经济",
            mandate_evidence_refs=[{"title": "test"}],
        )
        row = c.to_db_row()
        assert "beneficiary_path_json" in row
        assert "company_role" in row
        assert "mandate_topic" in row
        assert "mandate_evidence_refs_json" in row

        restored = Candidate.from_db_row(row)
        assert restored.beneficiary_path == ["整机", "材料"]
        assert restored.company_role == "CORE_SUPPLIER"
        assert restored.mandate_topic == "低空经济"
        assert len(restored.mandate_evidence_refs) == 1


# ── Edge cases ──


class TestEdgeCases:
    def test_zero_confidence(self):
        sig = _make_signal(
            title="test",
            symbol="000001.SZ",
            confidence=0.0,
            topic="低空经济",
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="000001.SZ")
        assert result.path_confidence >= 0.0

    def test_no_source_level(self):
        sig = MandateSignal(
            symbol="000001.SZ",
            title="某公司公告",
            source="test",
            confidence=0.5,
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="000001.SZ")
        assert result is not None

    def test_empty_title(self):
        sig = MandateSignal(
            symbol="000001.SZ",
            title="",
            source="test",
            confidence=0.5,
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="000001.SZ")
        assert result.beneficiary_path == []

    def test_many_signals(self):
        sigs = [
            _make_signal(
                title=f"信号{i}",
                symbol="300001.SZ",
                topic="算力",
                confidence=0.7,
                industry_tags=["AI芯片", "GPU"],
            )
            for i in range(100)
        ]
        result = compute_beneficiary_path("算力", sigs, symbol="300001.SZ")
        assert result.path_confidence <= 1.0
        assert result.mandate_evidence_refs is not None

    def test_all_13_topics_have_chain(self):
        topics = get_all_topics()
        for t in topics:
            chain = get_industry_chain(t)
            assert len(chain) > 0, f"empty chain for topic: {t}"

    def test_semicolon_chain_segments_unique(self):
        sig = _make_signal(
            title="eVTOL整机飞行器eVTOL整机",
            symbol="300001.SZ",
            topic="低空经济",
            industry_tags=["eVTOL", "飞行器"],
        )
        result = compute_beneficiary_path("低空经济", [sig], symbol="300001.SZ")
        assert len(result.beneficiary_path) == len(set(result.beneficiary_path))
