# [KB-004] tradeflow_knowledge_score
"""Tests for TradeFlow 昊天候选接入本地知识命中分与证据摘要 (KB-004).

覆盖：
  - ``compute_local_knowledge_score``：HAS_DATA / NORMAL_NO_DATA / STALE /
    LOW_CONFIDENCE / FAILED / None / 空 matched_pages 等场景；分数计算、
    过期/低置信不加分、summary 含负面信息。
  - ``needs_tree_work_research``：判定规则（昊天候选 + 主题热 + 无 fresh 命中）。
  - ``MandateEvidencePacket.local_knowledge_summary`` 字段存在并能在传入
    ``local_knowledge_result`` 时被填充；不破坏 H-017 现有测试断言。
  - ``build_evidence_packets_for_candidates`` 批量版本透传 local_knowledge。
  - ``build_mandate_daily_report`` 可选 ``knowledge_root`` 注入命中摘要。
  - TradeFlow ``_enrich_candidate_with_local_knowledge`` /
    ``_enrich_candidates_with_local_knowledge``：单条 + 批量、命中/无命中、
    needs_tree_work_research 标记、KB-008 字段不冲突、强动作门禁不动。
  - 只读安全性（不写知识库）。
  - Pydantic schema 包含新增字段。
  - 无买卖建议词。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

# 复用 KB-007 的 fixture 常量（同一套 mini 知识库），保证口径一致。
from tests.test_kb007_research_attention import (
    _COMPANY_PAGE_A,
    _COMPANY_PAGE_B,
    _EXPIRED_PAGE,
    _HK_PAGE,
    _SCORE_TABLE_PAGE,
    _US_TODO_PAGE,
)
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.citation_policy import (
    TIER_MEDIA,
    TIER_ORIGINAL_FILING,
    TIER_USER_NOTE,
)
from tradingagents.dataflows.local_knowledge_provider import (
    LocalKnowledgeMatch,
    LocalKnowledgeQueryResult,
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    compute_local_knowledge_score,
    needs_tree_work_research,
    query_local_knowledge,
)


# ── fixture ─────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE_A)
    _write(inv / "AI算力基础设施-公司评分表.md", _SCORE_TABLE_PAGE)
    _write(inv / "华勤技术-深度研究.md", _COMPANY_PAGE_B)
    _write(inv / "腾讯控股-游戏复苏.md", _HK_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _US_TODO_PAGE)
    _write(inv / "某周期股-已过期.md", _EXPIRED_PAGE)
    return tmp_path


@pytest.fixture()
def empty_kb(tmp_path: Path) -> Path:
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True)
    return tmp_path


def _result(status: str, matches: list[LocalKnowledgeMatch]) -> LocalKnowledgeQueryResult:
    """构造一个 LocalKnowledgeQueryResult 用于单测。

    模拟 ``query_local_knowledge._aggregate_result`` 的行为：从 matched_pages
    聚合 themes / risks / updated_at，使 ``compute_local_knowledge_score`` 能
    拿到与真实查询一致的二级字段。
    """
    themes: list[str] = []
    risks: list[str] = []
    updated_list: list[str] = []
    for m in matches:
        for t in m.themes:
            if t and t not in themes:
                themes.append(t)
        for r in m.risks:
            if r and r not in risks:
                risks.append(r)
        if m.updated_at:
            updated_list.append(m.updated_at)
    return LocalKnowledgeQueryResult(
        status=status,
        matched_pages=matches,
        themes=themes,
        risks=risks,
        updated_at=max(updated_list) if updated_list else None,
        confidence="medium" if matches else "low",
    )


def _match(
    *,
    confidence: str = "medium",
    is_stale: bool = False,
    is_low_confidence: bool = False,
    is_to_be_supplemented: bool = False,
    title: str = "page",
    summary: str = "x",
    risks: list[str] | None = None,
    themes: list[str] | None = None,
    updated_at: str = "2026-06-30",
    rel_path: str = "wiki/investment/x.md",
    source_quality_tier: str = TIER_ORIGINAL_FILING,
    citation_confidence_weight: float = 1.0,
) -> LocalKnowledgeMatch:
    return LocalKnowledgeMatch(
        rel_path=rel_path,
        title=title,
        page_type="company",
        summary=summary,
        risks=risks or [],
        sources=[],
        symbols=["603296.SH 华勤技术"],
        themes=themes or ["AI服务器"],
        tags=[],
        updated_at=updated_at,
        machine_readiness="medium",
        is_stale=is_stale,
        is_low_confidence=is_low_confidence,
        is_to_be_supplemented=is_to_be_supplemented,
        matched_by=["symbol"],
        confidence=confidence,
        source_quality_tier=source_quality_tier,
        citation_confidence_weight=citation_confidence_weight,
    )


# ── 1. compute_local_knowledge_score 基础场景 ────────────────────────


class TestComputeScoreBasic:
    def test_none_returns_empty_structure(self):
        s = compute_local_knowledge_score(None)
        assert s["local_knowledge_score"] == 0.0
        assert s["knowledge_hit_count"] == 0
        assert s["fresh_hit_count"] == 0
        assert s["has_hit"] is False
        assert s["status"] == STATUS_NORMAL_NO_DATA
        assert s["local_knowledge_summary"] == ""

    def test_empty_matched_returns_empty_structure(self):
        result = LocalKnowledgeQueryResult(status=STATUS_NORMAL_NO_DATA)
        s = compute_local_knowledge_score(result)
        assert s["local_knowledge_score"] == 0.0
        assert s["knowledge_hit_count"] == 0
        assert s["has_hit"] is False
        assert s["status"] == STATUS_NORMAL_NO_DATA

    def test_has_data_with_fresh_high_confidence(self):
        result = _result(
            STATUS_HAS_DATA,
            [_match(confidence="high"), _match(confidence="high")],
        )
        s = compute_local_knowledge_score(result)
        # 2 个 fresh high confidence 命中：1.0 + 1.0 = 2.0
        assert s["local_knowledge_score"] == 2.0
        assert s["knowledge_hit_count"] == 2
        assert s["fresh_hit_count"] == 2
        assert s["stale_hit_count"] == 0
        assert s["has_hit"] is True
        assert "本地知识命中 2 条" in s["local_knowledge_summary"]
        assert "命中分 2.00" in s["local_knowledge_summary"]

    def test_score_capped_at_max(self):
        # 5 个 fresh high confidence 命中：理论 5.0，但封顶 3.0。
        matches = [_match(confidence="high") for _ in range(5)]
        result = _result(STATUS_HAS_DATA, matches)
        s = compute_local_knowledge_score(result)
        assert s["local_knowledge_score"] == 3.0
        assert s["knowledge_hit_count"] == 5

    def test_score_weights_by_confidence(self):
        # high=1.0, medium=0.6, low=0.3
        matches = [
            _match(confidence="high"),
            _match(confidence="medium"),
            _match(confidence="low"),
        ]
        result = _result(STATUS_HAS_DATA, matches)
        s = compute_local_knowledge_score(result)
        assert s["local_knowledge_score"] == round(1.0 + 0.6 + 0.3, 2)

    def test_score_rounded_to_two_decimals(self):
        matches = [_match(confidence="medium")]  # 0.6
        result = _result(STATUS_HAS_DATA, matches)
        s = compute_local_knowledge_score(result)
        assert round(s["local_knowledge_score"], 2) == s["local_knowledge_score"]


# ── 2. 过期 / 低置信不加分（核心约束）──────────────────────────────


class TestStaleAndLowConfidenceDoNotBoost:
    def test_stale_pages_score_zero(self):
        m = _match(confidence="high", is_stale=True, title="过期页")
        result = _result(STATUS_STALE, [m])
        s = compute_local_knowledge_score(result)
        # 任务约束：过期知识不得加分。
        assert s["local_knowledge_score"] == 0.0
        assert s["fresh_hit_count"] == 0
        assert s["stale_hit_count"] == 1
        assert s["has_hit"] is False
        # summary 必须提示需更新。
        assert "过期" in s["local_knowledge_summary"]

    def test_low_confidence_pages_score_zero(self):
        m = _match(confidence="high", is_low_confidence=True, title="低置信页")
        result = _result(STATUS_LOW_CONFIDENCE, [m])
        s = compute_local_knowledge_score(result)
        assert s["local_knowledge_score"] == 0.0
        assert s["fresh_hit_count"] == 0
        assert s["low_confidence_hit_count"] == 1
        assert s["has_hit"] is False
        assert "低置信" in s["local_knowledge_summary"]

    def test_to_be_supplemented_pages_score_zero(self):
        m = _match(confidence="high", is_to_be_supplemented=True, title="待补充页")
        result = _result(STATUS_LOW_CONFIDENCE, [m])
        s = compute_local_knowledge_score(result)
        assert s["local_knowledge_score"] == 0.0
        assert s["fresh_hit_count"] == 0
        assert s["low_confidence_hit_count"] == 1

    def test_mixed_fresh_and_stale_only_fresh_counted(self):
        matches = [
            _match(confidence="high"),
            _match(confidence="high", is_stale=True),
            _match(confidence="high", is_low_confidence=True),
        ]
        result = _result(STATUS_HAS_DATA, matches)
        s = compute_local_knowledge_score(result)
        # 只有一个 fresh 命中计入分数。
        assert s["local_knowledge_score"] == 1.0
        assert s["fresh_hit_count"] == 1
        assert s["stale_hit_count"] == 1
        assert s["low_confidence_hit_count"] == 1
        assert s["knowledge_hit_count"] == 3
        assert s["has_hit"] is True
        # summary 同时展示负面信息。
        assert "过期" in s["local_knowledge_summary"]
        assert "低置信" in s["local_knowledge_summary"]

    def test_failed_status_no_summary(self):
        result = LocalKnowledgeQueryResult(status=STATUS_FAILED)
        s = compute_local_knowledge_score(result)
        assert s["local_knowledge_score"] == 0.0
        assert s["has_hit"] is False
        assert "查询失败" in s["local_knowledge_summary"]

    def test_weak_source_pages_do_not_count_as_fresh_hit(self):
        m = _match(
            confidence="high",
            source_quality_tier=TIER_MEDIA,
            citation_confidence_weight=0.3,
        )
        result = _result(STATUS_HAS_DATA, [m])
        s = compute_local_knowledge_score(result)
        assert s["local_knowledge_score"] == 0.0
        assert s["knowledge_hit_count"] == 1
        assert s["fresh_hit_count"] == 0
        assert s["low_confidence_hit_count"] == 1
        assert s["weak_source_hit_count"] == 1
        assert s["has_hit"] is False
        assert s["matched_pages_brief"][0]["is_low_confidence"] is True
        assert s["matched_pages_brief"][0]["is_weak_source"] is True
        assert "弱来源" in s["local_knowledge_summary"]

    def test_mixed_weak_and_low_confidence_summary_reports_both(self):
        weak = _match(
            confidence="high",
            source_quality_tier=TIER_MEDIA,
            citation_confidence_weight=0.3,
        )
        low = _match(
            confidence="low",
            is_low_confidence=True,
            source_quality_tier=TIER_ORIGINAL_FILING,
            citation_confidence_weight=1.0,
        )
        result = _result(STATUS_LOW_CONFIDENCE, [weak, low])

        s = compute_local_knowledge_score(result)

        assert s["knowledge_hit_count"] == 2
        assert s["fresh_hit_count"] == 0
        assert s["low_confidence_hit_count"] == 2
        assert s["weak_source_hit_count"] == 1
        assert "1 条弱来源页" in s["local_knowledge_summary"]
        assert "1 条低置信/待补充页" in s["local_knowledge_summary"]

    def test_weak_only_summary_keeps_tree_work_research_needed(self):
        m = _match(
            confidence="high",
            source_quality_tier=TIER_USER_NOTE,
            citation_confidence_weight=0.2,
        )
        s = compute_local_knowledge_score(_result(STATUS_HAS_DATA, [m]))
        assert s["has_hit"] is False
        assert needs_tree_work_research(
            candidate_type="POLICY_AMBUSH",
            mandate_topic="低空经济",
            mandate_score=5.0,
            knowledge_summary=s,
        ) is True


# ── 3. summary 内容 / 风险 / 主题字段 ───────────────────────────────


class TestSummaryContent:
    def test_summary_contains_themes(self):
        m = _match(confidence="high", themes=["AI服务器", "液冷", "ODM"])
        result = _result(STATUS_HAS_DATA, [m])
        s = compute_local_knowledge_score(result)
        assert "AI服务器" in s["local_knowledge_summary"]

    def test_summary_contains_risks(self):
        m = _match(confidence="high", risks=["客户集中", "需求不及预期"])
        result = _result(STATUS_HAS_DATA, [m])
        s = compute_local_knowledge_score(result)
        assert "风险提示" in s["local_knowledge_summary"]
        assert "客户集中" in s["local_knowledge_summary"]

    def test_matched_pages_brief_structure(self):
        m = _match(confidence="high", title="华勤技术")
        result = _result(STATUS_HAS_DATA, [m])
        s = compute_local_knowledge_score(result)
        assert len(s["matched_pages_brief"]) == 1
        brief = s["matched_pages_brief"][0]
        for key in (
            "rel_path", "title", "page_type", "updated_at",
            "confidence", "is_stale", "is_low_confidence",
            "is_to_be_supplemented", "summary_snippet", "matched_by",
        ):
            assert key in brief
        assert brief["title"] == "华勤技术"

    def test_matched_pages_brief_capped(self):
        matches = [_match(confidence="high", title=f"p{i}") for i in range(10)]
        result = _result(STATUS_HAS_DATA, matches)
        s = compute_local_knowledge_score(result)
        # _MAX_MATCHED_PAGES = 5。
        assert len(s["matched_pages_brief"]) <= 5

    def test_summary_no_strong_action_words(self):
        m = _match(confidence="high")
        result = _result(STATUS_HAS_DATA, [m])
        s = compute_local_knowledge_score(result)
        text = s["local_knowledge_summary"]
        for forbidden in ("买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL"):
            assert forbidden not in text, f"summary contains forbidden word: {forbidden}"

    def test_summary_snippet_truncated(self):
        long_summary = "x" * 500
        m = _match(confidence="high", summary=long_summary)
        result = _result(STATUS_HAS_DATA, [m])
        s = compute_local_knowledge_score(result)
        brief = s["matched_pages_brief"][0]
        # _SUMMARY_MAX_CHARS = 200。
        assert len(brief["summary_snippet"]) <= 200

    def test_partial_query_errors_preserved_with_hits(self):
        m = _match(confidence="high", title="华勤技术")
        result = _result(STATUS_HAS_DATA, [m])
        result.errors.append("partial page parse failed")
        s = compute_local_knowledge_score(result)
        assert s["has_hit"] is True
        assert s["errors"] == ["partial page parse failed"]


# ── 4. needs_tree_work_research ─────────────────────────────────────


class TestNeedsTreeWorkResearch:
    def test_mandate_candidate_no_knowledge_needs_research(self):
        assert needs_tree_work_research(
            candidate_type="POLICY_AMBUSH",
            mandate_topic="低空经济",
            mandate_score=5.0,
            knowledge_summary={"has_hit": False},
        ) is True

    def test_mandate_candidate_with_knowledge_no_flag(self):
        assert needs_tree_work_research(
            candidate_type="POLICY_AMBUSH",
            mandate_topic="低空经济",
            mandate_score=5.0,
            knowledge_summary={"has_hit": True},
        ) is False

    def test_tech_trade_candidate_not_flagged(self):
        # 短线技术候选不在昊天左侧池。
        assert needs_tree_work_research(
            candidate_type="TECH_TRADE",
            mandate_topic="",
            mandate_score=0.0,
            knowledge_summary={"has_hit": False},
        ) is False

    def test_no_topic_no_flag(self):
        assert needs_tree_work_research(
            candidate_type="",
            mandate_topic="",
            mandate_score=5.0,
            knowledge_summary={"has_hit": False},
        ) is False

    def test_cold_topic_no_flag(self):
        # mandate_score=0 且 topic_status 不在 hot 列表中。
        assert needs_tree_work_research(
            candidate_type="POLICY_AMBUSH",
            mandate_topic="低空经济",
            topic_status="RECEDING",
            mandate_score=0.0,
            knowledge_summary={"has_hit": False},
        ) is False

    def test_hot_topic_status_triggers_flag(self):
        # mandate_score=0 但 topic_status=RISING。
        assert needs_tree_work_research(
            candidate_type="POLICY_CONFIRM",
            mandate_topic="机器人",
            topic_status="RISING",
            mandate_score=0.0,
            knowledge_summary={"has_hit": False},
        ) is True

    def test_no_knowledge_summary_dict_treated_as_no_hit(self):
        assert needs_tree_work_research(
            candidate_type="POLICY_AMBUSH",
            mandate_topic="低空经济",
            mandate_score=3.0,
            knowledge_summary=None,
        ) is True

    def test_event_watch_candidate_can_be_flagged(self):
        assert needs_tree_work_research(
            candidate_type="EVENT_WATCH",
            mandate_topic="航天发射",
            mandate_score=2.0,
            knowledge_summary={"has_hit": False},
        ) is True


# ── 5. MandateEvidencePacket.local_knowledge_summary ────────────────


def _full_candidate():
    """复用 H-017 测试的 well-evidenced candidate 结构。"""
    return {
        "symbol": "300034.SZ",
        "name": "钢研高纳",
        "mandate_topic": "低空经济",
        "policy_tags": ["低空经济"],
        "company_role": "CORE_SUPPLIER",
        "beneficiary_path": ["整机", "动力系统"],
        "candidate_type": "POLICY_AMBUSH",
        "topic_lifecycle_state": "ACCELERATING",
        "policy_evidence_refs": [
            {"title": "工信部召开低空经济座谈会", "source": "工信部",
             "date": "2026-06-18", "source_level": "MINISTRY"},
        ],
    }


class TestEvidencePacketLocalKnowledge:
    def test_packet_has_local_knowledge_summary_field(self):
        from tradingagents.tradeflow.mandate_evidence_packet import (
            build_evidence_packet,
        )
        pkt = build_evidence_packet(_full_candidate())
        d = pkt.to_dict()
        assert "local_knowledge_summary" in d
        # 默认为空 dict（未传入 local_knowledge_result）。
        assert d["local_knowledge_summary"] == {}

    def test_packet_populated_when_result_provided(self):
        from tradingagents.tradeflow.mandate_evidence_packet import (
            build_evidence_packet,
        )
        result = _result(
            STATUS_HAS_DATA, [_match(confidence="high", title="华勤技术")],
        )
        pkt = build_evidence_packet(
            _full_candidate(), local_knowledge_result=result,
        )
        d = pkt.to_dict()
        lk = d["local_knowledge_summary"]
        assert lk["has_hit"] is True
        assert lk["local_knowledge_score"] == 1.0
        assert lk["knowledge_hit_count"] == 1

    def test_packet_does_not_inflate_confidence(self):
        """任务约束：本地知识命中不能提升主候选层级。"""
        from tradingagents.tradeflow.mandate_evidence_packet import (
            CONFIDENCE_HIGH,
            CONFIDENCE_MEDIUM,
            build_evidence_packet,
        )
        # 先建一个无知识命中的包，再建一个有知识命中的包，confidence 应一致。
        pkt_no_kb = build_evidence_packet(_full_candidate())
        result = _result(
            STATUS_HAS_DATA, [_match(confidence="high")] * 3,
        )
        pkt_with_kb = build_evidence_packet(
            _full_candidate(), local_knowledge_result=result,
        )
        assert pkt_no_kb.confidence == pkt_with_kb.confidence
        assert pkt_no_kb.needs_manual_research == pkt_with_kb.needs_manual_research
        # 但 local_knowledge_summary 不一样。
        assert pkt_with_kb.local_knowledge_summary["local_knowledge_score"] > 0.0
        assert pkt_no_kb.local_knowledge_summary == {}

    def test_packet_stale_result_does_not_inflate(self):
        from tradingagents.tradeflow.mandate_evidence_packet import (
            build_evidence_packet,
        )
        stale_result = _result(
            STATUS_STALE, [_match(confidence="high", is_stale=True)],
        )
        pkt = build_evidence_packet(
            _full_candidate(), local_knowledge_result=stale_result,
        )
        lk = pkt.local_knowledge_summary
        # stale 不加分。
        assert lk["local_knowledge_score"] == 0.0
        assert lk["has_hit"] is False
        # confidence 仍然不变。
        pkt_no_kb = build_evidence_packet(_full_candidate())
        assert pkt.confidence == pkt_no_kb.confidence

    def test_packet_failed_result_does_not_break(self):
        from tradingagents.tradeflow.mandate_evidence_packet import (
            build_evidence_packet,
        )
        failed = LocalKnowledgeQueryResult(status=STATUS_FAILED)
        pkt = build_evidence_packet(
            _full_candidate(), local_knowledge_result=failed,
        )
        # 失败的查询不应该破坏包构建。
        lk = pkt.local_knowledge_summary
        assert lk["has_hit"] is False
        assert lk["local_knowledge_score"] == 0.0

    def test_packet_none_result_keeps_empty(self):
        from tradingagents.tradeflow.mandate_evidence_packet import (
            build_evidence_packet,
        )
        pkt = build_evidence_packet(
            _full_candidate(), local_knowledge_result=None,
        )
        assert pkt.local_knowledge_summary == {}

    def test_determinism_same_result_same_output(self):
        from tradingagents.tradeflow.mandate_evidence_packet import (
            build_evidence_packet,
        )
        result = _result(STATUS_HAS_DATA, [_match(confidence="high")])
        p1 = build_evidence_packet(
            _full_candidate(), local_knowledge_result=result,
        ).to_dict()
        p2 = build_evidence_packet(
            _full_candidate(), local_knowledge_result=result,
        ).to_dict()
        assert p1 == p2


class TestBatchBuildPacketsWithLocalKnowledge:
    def test_batch_forwards_local_knowledge(self):
        from tradingagents.tradeflow.mandate_evidence_packet import (
            build_evidence_packets_for_candidates,
        )
        candidates = [
            {"symbol": "300034.SZ", "name": "钢研高纳", "mandate_topic": "低空经济"},
            {"symbol": "603296.SH", "name": "华勤技术", "mandate_topic": "AI算力"},
        ]
        kb_by_symbol = {
            "300034.SZ": _result(
                STATUS_HAS_DATA, [_match(confidence="high", title="钢研")],
            ),
            "603296.SH": LocalKnowledgeQueryResult(status=STATUS_NORMAL_NO_DATA),
        }
        packets = build_evidence_packets_for_candidates(
            candidates, local_knowledge_by_symbol=kb_by_symbol,
        )
        assert packets["300034.SZ"].local_knowledge_summary["has_hit"] is True
        # 无命中的 symbol：local_knowledge_summary 是结构化空命中（has_hit=False）。
        assert packets["603296.SH"].local_knowledge_summary["has_hit"] is False
        assert packets["603296.SH"].local_knowledge_summary["local_knowledge_score"] == 0.0


# ── 6. build_mandate_daily_report 集成 ──────────────────────────────


class TestMandateDailyReportKnowledgeInjection:
    def _heatmap_with_candidate(self) -> dict:
        return {
            "as_of": "2026-07-04",
            "topics": [
                {
                    "topic": "AI算力基础设施",
                    "topic_status": "RISING",
                    "heat_trend": "RISING",
                    "is_left_side": True,
                    "candidates": [
                        {
                            "symbol": "603296.SH",
                            "name": "华勤技术",
                            "company_role": "CORE_SUPPLIER",
                            "candidate_type": "POLICY_AMBUSH",
                            "mandate_score": 5.0,
                        },
                    ],
                },
            ],
        }

    def test_report_without_knowledge_root_uses_default_root(
        self, fixture_kb: Path, monkeypatch
    ):
        import tradingagents.dataflows.local_knowledge_audit as audit
        from tradingagents.tradeflow.mandate_daily_report import (
            build_mandate_daily_report,
        )

        monkeypatch.setattr(audit, "default_knowledge_root", lambda: str(fixture_kb))

        report = build_mandate_daily_report(self._heatmap_with_candidate())
        assert report.main_candidates
        ep = report.main_candidates[0].evidence_packet
        lk = ep.get("local_knowledge_summary", {})
        assert lk.get("has_hit") is True
        assert lk.get("local_knowledge_score", 0.0) > 0.0

    def test_report_with_knowledge_root_populates_summary(
        self, fixture_kb: Path
    ):
        from tradingagents.tradeflow.mandate_daily_report import (
            build_mandate_daily_report,
        )
        report = build_mandate_daily_report(
            self._heatmap_with_candidate(),
            knowledge_root=str(fixture_kb),
        )
        ep = report.main_candidates[0].evidence_packet
        lk = ep.get("local_knowledge_summary", {})
        # 华勤 fixture 有 fresh 命中（COMPANY_PAGE_A、COMPANY_PAGE_B）。
        assert lk.get("has_hit") is True
        assert lk.get("local_knowledge_score", 0.0) > 0.0

    def test_report_with_missing_kb_does_not_crash(self, tmp_path: Path):
        from tradingagents.tradeflow.mandate_daily_report import (
            build_mandate_daily_report,
        )
        missing = tmp_path / "does-not-exist"
        report = build_mandate_daily_report(
            self._heatmap_with_candidate(),
            knowledge_root=str(missing),
        )
        # 失败的查询不破坏报告。
        ep = report.main_candidates[0].evidence_packet
        # 要么字段缺失，要么是空 dict / has_hit=False。
        lk = ep.get("local_knowledge_summary", {})
        assert lk.get("has_hit", False) is False


# ── 7. TradeFlow 候选 enrichment ────────────────────────────────────


class TestTradeFlowCandidateEnrichment:
    def test_single_candidate_hit(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {
            "symbol": "603296.SH",
            "name": "华勤技术",
            "candidate_type": "POLICY_AMBUSH",
            "mandate_topic": "AI服务器",
            "mandate_score": 5.0,
        }
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["local_knowledge_score"] > 0.0
        assert result["knowledge_hit_count"] >= 1
        assert "本地知识命中" in result["local_knowledge_summary"]
        assert "research_attention_score" not in result  # KB-008 字段不混入

    def test_single_candidate_bare_code_symbol(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {"symbol": "603296", "name": "华勤技术"}
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["local_knowledge_score"] > 0.0

    def test_single_candidate_no_hit(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {"symbol": "999999.SH", "name": "无此股"}
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["local_knowledge_score"] == 0.0
        assert result["knowledge_hit_count"] == 0
        assert result["local_knowledge_summary"] == ""
        assert result["local_knowledge_detail"]["has_hit"] is False

    def test_single_candidate_empty_symbol(self, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        item = {"symbol": "", "name": "x"}
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["local_knowledge_score"] == 0.0
        assert result["needs_tree_work_research"] is False

    def test_needs_tree_work_research_flag(
        self, fixture_kb: Path, monkeypatch
    ):
        """任务约束：知识缺失但主题热的候选标记 needs_tree_work_research。"""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {
            "symbol": "999999.SH",  # 无知识命中
            "name": "某概念股",
            "candidate_type": "POLICY_AMBUSH",
            "mandate_topic": "低空经济",
            "mandate_score": 4.5,
        }
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["needs_tree_work_research"] is True
        assert result["local_knowledge_score"] == 0.0

    def test_failed_knowledge_lookup_is_not_tree_work_gap(
        self, tmp_path: Path, monkeypatch
    ):
        """Codex review regression: bad knowledge root is infra failure, not backlog."""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(tmp_path / "missing-kb"))
        item = {
            "symbol": "999999.SH",
            "name": "某概念股",
            "candidate_type": "POLICY_AMBUSH",
            "mandate_topic": "低空经济",
            "mandate_score": 4.5,
        }
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["local_knowledge_detail"]["status"] == "FAILED"
        assert result["needs_tree_work_research"] is False

    def test_query_exception_is_not_tree_work_gap(
        self, fixture_kb: Path, monkeypatch
    ):
        """Codex review regression: query exceptions are FAILED, not empty backlog."""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        import tradingagents.dataflows.local_knowledge_provider as provider

        def _boom(*args, **kwargs):
            raise RuntimeError("index locked")

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        monkeypatch.setattr(provider, "query_local_knowledge", _boom)
        item = {
            "symbol": "999999.SH",
            "name": "某概念股",
            "candidate_type": "POLICY_AMBUSH",
            "mandate_topic": "低空经济",
            "mandate_score": 4.5,
        }
        result = _enrich_candidate_with_local_knowledge(item)
        detail = result["local_knowledge_detail"]
        assert detail["status"] == "FAILED"
        assert "index locked" in " ".join(detail["errors"])
        assert result["needs_tree_work_research"] is False

    def test_needs_tree_work_research_not_set_when_has_knowledge(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {
            "symbol": "603296.SH",  # 华勤有命中
            "name": "华勤技术",
            "candidate_type": "POLICY_AMBUSH",
            "mandate_topic": "AI服务器",
            "mandate_score": 5.0,
        }
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["needs_tree_work_research"] is False

    def test_needs_tree_work_research_not_set_for_tech_trade(
        self, fixture_kb: Path, monkeypatch
    ):
        """短线技术候选不在昊天左侧池，即便无知识命中也不打 flag。"""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {
            "symbol": "999999.SH",
            "name": "短线票",
            "candidate_type": "TECH_TRADE",
            "mandate_topic": "",
            "mandate_score": 0.0,
        }
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["needs_tree_work_research"] is False

    def test_does_not_touch_strong_action_gate(
        self, fixture_kb: Path, monkeypatch
    ):
        """任务约束：本地知识命中是解释信息，绝不改变强动作门禁 / tier。"""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {
            "symbol": "603296.SH",
            "name": "华勤技术",
            "tier": "B",
            "need_deep_ta": False,
            "action": "OBSERVE",
            "candidate_type": "POLICY_AMBUSH",
            "mandate_topic": "AI服务器",
            "mandate_score": 5.0,
            "trigger_price": None,
            "invalid_price": 100.0,
        }
        result = _enrich_candidate_with_local_knowledge(item)
        # 强动作门禁字段不动。
        assert result["tier"] == "B"
        assert result["need_deep_ta"] is False
        assert result["action"] == "OBSERVE"
        assert result["invalid_price"] == 100.0

    def test_stale_only_hit_does_not_boost_score(
        self, fixture_kb: Path, monkeypatch
    ):
        """任务约束：仅 stale 命中不会推高分数。"""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        # 浦发银行只出现在 _EXPIRED_PAGE（valid_until=2020-01-01）。
        item = {"symbol": "600000.SH", "name": "浦发银行"}
        result = _enrich_candidate_with_local_knowledge(item)
        assert result["local_knowledge_score"] == 0.0
        assert result["local_knowledge_detail"]["stale_hit_count"] >= 1

    def test_kb008_fields_remain_when_kb004_added(
        self, fixture_kb: Path, monkeypatch
    ):
        """KB-008 与 KB-004 字段共存，互不影响。"""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
            _enrich_candidate_with_research_attention,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {"symbol": "603296.SH", "name": "华勤技术"}
        # KB-008 先注入。
        item = _enrich_candidate_with_research_attention(item)
        assert item["research_attention_score"] > 0.0
        # KB-004 再注入。
        item = _enrich_candidate_with_local_knowledge(item)
        assert item["research_attention_score"] > 0.0  # 仍在
        assert item["local_knowledge_score"] > 0.0


# ── 8. 批量 enrichment ──────────────────────────────────────────────


class TestTradeFlowBatchEnrichment:
    def test_batch_enrichment_mixed_hit_and_no_hit(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        items = [
            {"symbol": "603296.SH", "name": "华勤技术"},
            {"symbol": "999999.SH", "name": "无此股"},
        ]
        result = _enrich_candidates_with_local_knowledge(items)
        hit, miss = result
        assert hit["local_knowledge_score"] > 0.0
        assert miss["local_knowledge_score"] == 0.0
        assert miss["local_knowledge_detail"]["has_hit"] is False

    def test_batch_enrichment_empty_list(self):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_local_knowledge,
        )
        assert _enrich_candidates_with_local_knowledge([]) == []

    def test_batch_enrichment_all_items_have_kb004_fields(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        items = [
            {"symbol": "603296.SH"},
            {"symbol": "000977.SZ"},
            {"symbol": "00700.HK"},
        ]
        result = _enrich_candidates_with_local_knowledge(items)
        for it in result:
            assert "local_knowledge_score" in it
            assert "knowledge_hit_count" in it
            assert "local_knowledge_summary" in it
            assert "local_knowledge_detail" in it
            assert "needs_tree_work_research" in it

    def test_batch_enrichment_degraded_when_kb_missing(
        self, tmp_path: Path, monkeypatch
    ):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_local_knowledge,
        )
        missing = tmp_path / "does-not-exist"
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(missing))
        items = [{"symbol": "603296.SH"}, {"symbol": "000001.SZ"}]
        result = _enrich_candidates_with_local_knowledge(items)
        for it in result:
            assert it["local_knowledge_score"] == 0.0
            assert it["local_knowledge_detail"]["has_hit"] is False
            assert it["needs_tree_work_research"] is False


# ── 9. 只读安全性 ───────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_enrichment_does_not_write_to_knowledge_base(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
            _enrich_candidates_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        files = list((fixture_kb / INVESTMENT_SUBDIR).rglob("*.md"))
        assert files
        before = {p: (p.stat().st_mtime, p.read_bytes()) for p in files}

        _enrich_candidate_with_local_knowledge({"symbol": "603296.SH"})
        _enrich_candidates_with_local_knowledge(
            [
                {"symbol": "603296.SH"},
                {"symbol": "999999.SH"},
                {"symbol": "00700.HK"},
            ]
        )

        for p in files:
            mt, content = before[p]
            assert p.stat().st_mtime == mt, f"mtime changed: {p}"
            assert p.read_bytes() == content, f"content changed: {p}"

    def test_no_new_files_created(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        before = {str(p) for p in fixture_kb.rglob("*")}
        _enrich_candidate_with_local_knowledge({"symbol": "603296.SH"})
        after = {str(p) for p in fixture_kb.rglob("*")}
        assert before == after, "enrichment created new files"


# ── 10. Pydantic schema ─────────────────────────────────────────────


class TestTradeFlowSchemaFields:
    def test_candidate_item_has_kb004_fields(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem

        fields = TradeFlowCandidateItem.model_fields
        assert "local_knowledge_score" in fields
        assert "knowledge_hit_count" in fields
        assert "local_knowledge_summary" in fields
        assert "local_knowledge_detail" in fields
        assert "needs_tree_work_research" in fields

    def test_candidate_item_default_values(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem

        item = TradeFlowCandidateItem(symbol="603296.SH")
        assert item.local_knowledge_score == 0.0
        assert item.knowledge_hit_count == 0
        assert item.local_knowledge_summary == ""
        assert item.local_knowledge_detail == {}
        assert item.needs_tree_work_research is False

    def test_candidate_item_accepts_kb004_payload(
        self, fixture_kb: Path, monkeypatch
    ):
        """端到端：enrichment 后产出的 dict 应能直接构造 Pydantic 模型。"""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )
        from api.tradeflow_schemas import TradeFlowCandidateItem

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = _enrich_candidate_with_local_knowledge({"symbol": "603296.SH"})
        schema_item = TradeFlowCandidateItem(**item)
        assert schema_item.local_knowledge_score > 0.0
        assert schema_item.knowledge_hit_count >= 1

    def test_mandate_evidence_packet_item_has_kb004_field(self):
        from api.tradeflow_schemas import MandateEvidencePacketItem

        fields = MandateEvidencePacketItem.model_fields
        assert "local_knowledge_summary" in fields

    def test_mandate_evidence_packet_item_accepts_kb004_payload(self):
        from api.tradeflow_schemas import MandateEvidencePacketItem

        lk = compute_local_knowledge_score(
            _result(STATUS_HAS_DATA, [_match(confidence="high")])
        )
        item = MandateEvidencePacketItem(local_knowledge_summary=lk)
        assert item.local_knowledge_summary["has_hit"] is True


# ── 11. 真实知识库 smoke（不写、不调 LLM）─────────────────────────────


class TestRealKnowledgeBaseSmoke:
    def test_compute_score_on_real_kb_returns_sensible_structure(self):
        """对真实 ~/Documents/knowledge 跑一次只读 smoke（缺失时跳过）。

        保证 KB-004 在真实环境不会抛异常，且结构字段齐备。
        """
        from tradingagents.dataflows.local_knowledge_audit import (
            default_knowledge_root,
        )
        root = default_knowledge_root()
        if not Path(root).exists():
            pytest.skip(f"knowledge root not present: {root}")
        result = query_local_knowledge(root, symbol="603296")
        s = compute_local_knowledge_score(result)
        # 字段齐备。
        for key in (
            "local_knowledge_score",
            "knowledge_hit_count",
            "fresh_hit_count",
            "stale_hit_count",
            "low_confidence_hit_count",
            "has_hit",
            "status",
            "confidence",
            "themes",
            "risks",
            "matched_pages_brief",
            "local_knowledge_summary",
        ):
            assert key in s
        # 命中分上限。
        assert s["local_knowledge_score"] <= 3.0
        # 命中分与 fresh 命中数一致（fresh=0 时分数为 0）。
        if s["fresh_hit_count"] == 0:
            assert s["local_knowledge_score"] == 0.0
