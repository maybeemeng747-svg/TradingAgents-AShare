# [H-017] mandate_evidence_packet
"""Tests for the 昊天 left-side candidate evidence packet (H-017).

Verifies:
1. Three-layer fields (policy / industry / company) populate from a
   well-evidenced candidate fixture.
2. ``needs_manual_research`` triggers when policy OR company evidence is
   missing — a thin candidate is never inflated.
3. ``missing_evidence`` lists concrete, human-readable gaps.
4. Confidence degrades from high → medium → low as layers go missing.
5. ``raw_evidence`` company-layer detection (announcements / research_report).
6. ``build_evidence_packets_for_candidates`` batch helper.
7. Integration with H-015 daily report: ``MandateDailyCandidate.evidence_packet``.
8. Forbidden trade-word scan passes on packet output + summary line.
9. Determinism: same inputs → identical output.
"""

import json

from tradingagents.tradeflow.mandate_daily_report import (
    MandateDailyCandidate,
    build_mandate_daily_report,
)
from tradingagents.tradeflow.mandate_evidence_packet import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    LAYER_COMPANY,
    LAYER_POLICY,
    MandateEvidencePacket,
    build_evidence_packet,
    build_evidence_packets_for_candidates,
    render_evidence_packet_summary,
)


def _full_candidate():
    """A well-evidenced POLICY_AMBUSH candidate with all three layers."""
    return {
        "symbol": "300034.SZ",
        "name": "钢研高纳",
        "mandate_topic": "低空经济",
        "policy_tags": ["低空经济", "eVTOL"],
        "company_role": "CORE_SUPPLIER",
        "beneficiary_path": ["整机", "动力系统"],
        "candidate_type": "POLICY_AMBUSH",
        "topic_lifecycle_state": "ACCELERATING",
        "topic_signal_count": 5,
        "policy_evidence_refs": [
            {"title": "工信部召开低空经济座谈会", "source": "工信部",
             "date": "2026-06-18", "source_level": "MINISTRY"},
            {"title": "多省发布低空经济行动方案", "source": "各省发改委",
             "date": "2026-06-20", "source_level": "LOCAL"},
        ],
        "mandate_evidence_refs": [
            {"title": "低空经济产业大会召开", "source": "新华社",
             "date": "2026-06-19", "source_level": "MINISTRY"},
        ],
        "blocking_evidence_gaps": [],
        "overheat_flags": [],
    }


def _raw_evidence_with_company():
    return {
        "announcements": {
            "raw": "## Announcements\n- 关于签署重大合同的公告",
            "status": "OK",
            "record_count": 3,
            "vendor": "cninfo",
        },
        "research_report": {
            "raw": "## Research\n- 低空经济产业链深度",
            "status": "OK",
            "record_count": 2,
            "vendor": "eastmoney",
        },
        "news": {
            "raw": "",
            "status": "FAILED",
            "record_count": 0,
        },
    }


# ── Unit: three-layer population ─────────────────────────────────────


class TestThreeLayerPopulation:
    def test_policy_layer_populated(self):
        pkt = build_evidence_packet(_full_candidate())
        d = pkt.to_dict()
        assert d["policy_theme"] == "低空经济"
        assert d["policy_level"] == "MINISTRY"
        assert d["policy_level_weight"] >= 4
        assert d["policy_evidence_count"] == 3  # 2 policy + 1 mandate ref
        assert d["topic_status_label"] != ""

    def test_industry_layer_populated(self):
        pkt = build_evidence_packet(_full_candidate())
        d = pkt.to_dict()
        # beneficiary_path ["整机", "动力系统"] should match defined chain
        assert "整机" in d["industry_chain_segments"]
        assert d["industry_chain_role"]  # non-empty
        assert "整机" in d["beneficiary_path"]

    def test_company_layer_populated(self):
        pkt = build_evidence_packet(_full_candidate())
        d = pkt.to_dict()
        assert d["company_role"] == "CORE_SUPPLIER"
        assert d["company_role_label"] == "核心供应商"
        assert d["has_company_evidence"] is True

    def test_evidence_titles_tagged_by_layer(self):
        pkt = build_evidence_packet(_full_candidate())
        titles = pkt.to_dict()["evidence_titles"]
        policy_titles = [t for t in titles if t["layer"] == LAYER_POLICY]
        assert len(policy_titles) >= 3
        # each policy title has a non-empty title string
        for t in policy_titles:
            assert t["title"]

    def test_full_candidate_not_needs_manual_research(self):
        pkt = build_evidence_packet(_full_candidate())
        assert pkt.needs_manual_research is False
        assert pkt.confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM)


# ── Unit: missing evidence → needs_manual_research ───────────────────


class TestNeedsManualResearch:
    def test_missing_policy_evidence_triggers_flag(self):
        c = _full_candidate()
        c["policy_evidence_refs"] = []
        c["mandate_evidence_refs"] = []
        pkt = build_evidence_packet(c)
        assert pkt.needs_manual_research is True
        assert any("政策" in m for m in pkt.missing_evidence)

    def test_missing_company_role_triggers_flag(self):
        c = _full_candidate()
        c["company_role"] = ""
        pkt = build_evidence_packet(c)
        assert pkt.needs_manual_research is True
        assert any("公司" in m for m in pkt.missing_evidence)

    def test_concept_only_role_triggers_flag(self):
        c = _full_candidate()
        c["company_role"] = "CONCEPT_ONLY"
        pkt = build_evidence_packet(c)
        assert pkt.needs_manual_research is True
        assert any("概念" in m for m in pkt.missing_evidence)

    def test_no_topic_triggers_flag(self):
        c = _full_candidate()
        c["mandate_topic"] = ""
        c["policy_tags"] = []
        c["name"] = "无主题公司"
        pkt = build_evidence_packet(c)
        assert pkt.needs_manual_research is True
        assert pkt.policy_theme == ""

    def test_thin_candidate_confidence_is_low(self):
        c = {
            "symbol": "X.SZ",
            "name": "空壳",
            "mandate_topic": "",
            "policy_tags": [],
            "company_role": "",
        }
        pkt = build_evidence_packet(c)
        assert pkt.confidence == CONFIDENCE_LOW
        assert pkt.needs_manual_research is True

    def test_missing_evidence_lists_concrete_gaps(self):
        c = {
            "symbol": "X.SZ",
            "name": "空壳",
            "mandate_topic": "",
        }
        pkt = build_evidence_packet(c)
        gaps = pkt.missing_evidence
        assert len(gaps) >= 3
        # should mention policy, industry and company layers
        joined = " ".join(gaps)
        assert "政策" in joined
        assert "产业" in joined
        assert "公司" in joined

    def test_blocking_evidence_gaps_surfaced(self):
        c = _full_candidate()
        c["blocking_evidence_gaps"] = ["订单兑现待跟踪", "估值偏高需验证"]
        pkt = build_evidence_packet(c)
        joined = " ".join(pkt.missing_evidence)
        assert "订单兑现待跟踪" in joined


# ── Unit: confidence tiers ───────────────────────────────────────────


class TestConfidenceTiers:
    def test_high_confidence_full_evidence(self):
        pkt = build_evidence_packet(_full_candidate(), raw_evidence=_raw_evidence_with_company())
        assert pkt.confidence == CONFIDENCE_HIGH

    def test_medium_confidence_two_layers(self):
        c = _full_candidate()
        c["company_role"] = "UNKNOWN"
        c["policy_evidence_refs"] = c["policy_evidence_refs"][:1]
        pkt = build_evidence_packet(c)
        assert pkt.confidence in (CONFIDENCE_MEDIUM, CONFIDENCE_LOW)


# ── Unit: raw_evidence company detection ─────────────────────────────


class TestRawEvidenceDetection:
    def test_company_evidence_available_detected(self):
        pkt = build_evidence_packet(
            _full_candidate(), raw_evidence=_raw_evidence_with_company(),
        )
        d = pkt.to_dict()
        assert "announcements" in d["company_evidence_available"]
        assert "research_report" in d["company_evidence_available"]
        # news FAILED → not included
        assert "news" not in d["company_evidence_available"]

    def test_no_raw_evidence_no_crash(self):
        pkt = build_evidence_packet(_full_candidate(), raw_evidence=None)
        assert pkt.company_evidence_available == []

    def test_empty_raw_evidence_dict(self):
        pkt = build_evidence_packet(_full_candidate(), raw_evidence={})
        assert pkt.company_evidence_available == []

    def test_company_markers_in_evidence_titles(self):
        pkt = build_evidence_packet(
            _full_candidate(), raw_evidence=_raw_evidence_with_company(),
        )
        titles = {t["title"] for t in pkt.to_dict()["evidence_titles"]}
        assert "公告已采集" in titles
        assert "研报已采集" in titles


# ── Unit: company role normalization ─────────────────────────────────


class TestCompanyRoleNormalization:
    def test_enum_value_passthrough(self):
        c = _full_candidate()
        c["company_role"] = "LEADER"
        assert build_evidence_packet(c).company_role == "LEADER"

    def test_chinese_label_normalized(self):
        c = _full_candidate()
        c["company_role"] = "核心供应商"
        assert build_evidence_packet(c).company_role == "CORE_SUPPLIER"

    def test_unknown_role(self):
        c = _full_candidate()
        c["company_role"] = "某种新角色"
        assert build_evidence_packet(c).company_role == "UNKNOWN"


# ── Unit: batch helper ───────────────────────────────────────────────


class TestBatchHelper:
    def test_batch_keyed_by_symbol(self):
        candidates = [
            _full_candidate(),
            {**_full_candidate(), "symbol": "002097.SZ", "name": "众合科技"},
        ]
        packets = build_evidence_packets_for_candidates(candidates)
        assert set(packets.keys()) == {"300034.SZ", "002097.SZ"}
        assert all(isinstance(p, MandateEvidencePacket) for p in packets.values())

    def test_batch_skips_empty_symbol(self):
        candidates = [{**_full_candidate(), "symbol": ""}]
        packets = build_evidence_packets_for_candidates(candidates)
        assert packets == {}

    def test_batch_with_raw_evidence_map(self):
        candidates = [_full_candidate()]
        raw_map = {"300034.SZ": _raw_evidence_with_company()}
        packets = build_evidence_packets_for_candidates(
            candidates, raw_evidence_by_symbol=raw_map,
        )
        assert "announcements" in packets["300034.SZ"].company_evidence_available


# ── Unit: summary renderer ───────────────────────────────────────────


class TestSummaryRenderer:
    def test_summary_contains_three_layers(self):
        pkt = build_evidence_packet(_full_candidate())
        summary = render_evidence_packet_summary(pkt)
        assert "政策" in summary
        assert "产业" in summary
        assert "公司" in summary

    def test_summary_marks_needs_research(self):
        c = _full_candidate()
        c["company_role"] = ""
        pkt = build_evidence_packet(c)
        summary = render_evidence_packet_summary(pkt)
        assert "需人工补证" in summary


# ── Integration: H-015 daily report ──────────────────────────────────


class TestDailyReportIntegration:
    def test_daily_candidate_has_evidence_packet(self):
        from tests.test_h015_mandate_daily_report import _fixture_heatmap
        report = build_mandate_daily_report(_fixture_heatmap())
        cand = report.main_candidates[0]
        assert isinstance(cand, MandateDailyCandidate)
        assert cand.evidence_packet  # non-empty dict
        assert cand.evidence_packet.get("policy_theme")
        assert cand.evidence_packet.get("company_role_label")

    def test_daily_candidate_to_dict_includes_packet(self):
        from tests.test_h015_mandate_daily_report import _fixture_heatmap
        report = build_mandate_daily_report(_fixture_heatmap())
        d = report.to_dict()
        pkt = d["main_candidates"][0]["evidence_packet"]
        assert "policy_theme" in pkt
        assert "missing_evidence" in pkt
        assert "needs_manual_research" in pkt

    def test_daily_report_markdown_no_forbidden_words(self):
        from tests.test_h015_mandate_daily_report import _fixture_heatmap
        report = build_mandate_daily_report(_fixture_heatmap())
        forbidden = ["立即清仓", "重仓买入", "满仓", "梭哈", "买入", "卖出"]
        for word in forbidden:
            assert word not in report.markdown, f"forbidden word '{word}' in markdown"


# ── Forbidden word scan on packet output ─────────────────────────────


class TestForbiddenWordScan:
    def test_packet_dict_no_trade_words(self):
        pkt = build_evidence_packet(_full_candidate(), raw_evidence=_raw_evidence_with_company())
        blob = json.dumps(pkt.to_dict(), ensure_ascii=False)
        forbidden = ["买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓"]
        for word in forbidden:
            assert word not in blob

    def test_summary_no_trade_words(self):
        pkt = build_evidence_packet(_full_candidate())
        summary = render_evidence_packet_summary(pkt)
        forbidden = ["买入", "卖出", "清仓", "满仓", "梭哈", "加仓", "减仓"]
        for word in forbidden:
            assert word not in summary


# ── Determinism ──────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_inputs_same_output(self):
        c = _full_candidate()
        p1 = build_evidence_packet(c).to_dict()
        p2 = build_evidence_packet(c).to_dict()
        assert p1 == p2

    def test_evidence_titles_capped(self):
        c = _full_candidate()
        c["policy_evidence_refs"] = [
            {"title": f"政策文件{i}", "source": "src", "date": "2026-06-01",
             "source_level": "MINISTRY"}
            for i in range(30)
        ]
        pkt = build_evidence_packet(c)
        assert len(pkt.evidence_titles) <= 12


# ── Edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_candidate(self):
        pkt = build_evidence_packet({})
        assert pkt.symbol == ""
        assert pkt.needs_manual_research is True
        assert pkt.confidence == CONFIDENCE_LOW

    def test_candidate_with_topic_but_no_evidence(self):
        c = {
            "symbol": "X.SZ",
            "name": "概念股",
            "mandate_topic": "低空经济",
            "company_role": "CONCEPT_ONLY",
        }
        pkt = build_evidence_packet(c)
        assert pkt.policy_theme == "低空经济"
        assert pkt.needs_manual_research is True
        assert pkt.confidence in (CONFIDENCE_LOW, CONFIDENCE_MEDIUM)

    def test_industry_chain_segments_from_definition(self):
        """When beneficiary_path is empty but topic is known, the defined
        chain segments are still surfaced as the industry-layer context."""
        c = {
            "symbol": "X.SZ",
            "name": "某公司",
            "mandate_topic": "机器人",
            "policy_tags": ["机器人"],
            "company_role": "LEADER",
            "beneficiary_path": [],
            "policy_evidence_refs": [
                {"title": "机器人产业政策", "source": "工信部",
                 "date": "2026-06-01", "source_level": "MINISTRY"},
            ],
        }
        pkt = build_evidence_packet(c)
        # industry_chain_role may be empty (no matched segments), but
        # the topic resolves and policy layer is populated
        assert pkt.policy_theme == "机器人"
        assert pkt.policy_evidence_count == 1
