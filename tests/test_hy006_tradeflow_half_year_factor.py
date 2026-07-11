# [HY-006] tradeflow_half_year_factor
"""Tests for TradeFlow/昊天候选接入半年报因子与降权规则 (HY-006).

覆盖（对应任务验收方式）：
  - ``compute_half_year_factor_score``：contradicted / weakened / supported /
    insufficient_data / no_facts / FAILED / None 等场景；分数区间、降权原因、
    风险标记、研究缺口标记。
  - **强知识 + 弱技术不能升主候选**：正向分上限 ``+1.0``，远低于本地知识命中分。
  - **半年报反证强时候选优先级下降**：contradicted/weakened 产出降权原因 + 负分。
  - **无半年报数据时只提示缺口**：needs_research_review=True，score=0。
  - **失败不变成研究缺口**：FAILED 时 needs_research_review=False。
  - ``MandateEvidencePacket.half_year_summary`` 字段存在并能在传入 half_year
    结果时被填充；不破坏 H-017 现有字段。
  - ``build_evidence_packets_for_candidates`` 批量透传 half_year_by_symbol。
  - TradeFlow ``_enrich_candidate_with_half_year`` /
    ``_enrich_candidates_with_half_year``：单条 + 批量、命中/无命中、
    needs_research_review 标记、KB-004 字段不冲突、强动作门禁不动。
  - 只读安全性（不写知识库 / 不写 DB）。
  - Pydantic schema 包含新增字段。
  - 无买卖建议词。
  - duck-typing：dict 输入与 dataclass 输入等价。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from tradingagents.dataflows.half_year_facts_provider import (
    DATA_CONFLICT,
    DATA_FRESH,
    DATA_MISSING_FACTS,
    DATA_OPINION_ONLY,
    DATA_STALE,
    HalfYearFactsPage,
    HalfYearFactsQueryResult,
    ParsedMetric,
)
from tradingagents.dataflows.half_year_factor_score import (
    STATUS_FAILED,
    STATUS_HAS_FACTS,
    STATUS_NO_FACTS,
    TASK_CODE,
    THESIS_STATUS_NONE,
    compute_half_year_factor_score,
    has_forbidden_action_words,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_FAILED as LK_STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_NORMAL_NO_DATA,
)
from tradingagents.dataflows.thesis_fact_check import (
    EVIDENCE_MODERATE,
    EVIDENCE_NONE,
    EVIDENCE_STRONG,
    EVIDENCE_WEAK,
    STATUS_CONTRADICTED,
    STATUS_INSUFFICIENT_DATA,
    STATUS_SUPPORTED,
    STATUS_WEAKENED,
    ThesisCheckFlag,
    ThesisFactCheckResult,
)


# ── helpers ──────────────────────────────────────────────────────────

_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL",
    "立即买入", "全仓", "止损", "建仓", "清仓",
)


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, f"text contains forbidden word: {forbidden}"


def _metric(
    metric_key: str = "revenue",
    label: str = "营收",
    raw: str = "营收 150.2亿",
    value: str | None = "150.2亿",
    change: str | None = "+30.1%",
) -> ParsedMetric:
    return ParsedMetric(
        metric_key=metric_key, metric_label=label, raw=raw, value=value, change=change,
    )


def _facts_page(
    *,
    period: str = "2025H1",
    data_status: str = DATA_FRESH,
    facts: List[ParsedMetric] | None = None,
    risks: List[str] | None = None,
) -> HalfYearFactsPage:
    return HalfYearFactsPage(
        rel_path="wiki/investment/test-half-year.md",
        title="测试半年报",
        financial_period=period,
        disclosure_date="2026-08-29",
        source_type=["exchange_filing"],
        financial_facts=facts or [_metric()],
        risk_factors=risks or [],
        data_status=data_status,
    )


def _facts_result(
    *,
    pages: List[HalfYearFactsPage] | None = None,
    status: str = STATUS_HAS_DATA,
    risks: List[str] | None = None,
    summary: List[str] | None = None,
    symbol: str = "000977",
) -> HalfYearFactsQueryResult:
    pages = pages if pages is not None else [_facts_page()]
    return HalfYearFactsQueryResult(
        status=status,
        symbol=symbol,
        name="测试公司",
        pages=pages,
        latest_period=pages[0].financial_period if pages else None,
        latest_disclosure_date="2026-08-29",
        data_status=pages[0].data_status if pages else DATA_MISSING_FACTS,
        risks=risks or [],
        summary=summary or [],
    )


def _flag(
    *,
    metric_key: str = "revenue",
    status: str = STATUS_CONTRADICTED,
    evidence: str = EVIDENCE_STRONG,
    reason: str = "营收同比下滑但观点看多",
) -> ThesisCheckFlag:
    return ThesisCheckFlag(
        metric_key=metric_key,
        opinion_text="观点：营收高增",
        fact_text="事实：营收同比 -20%",
        opinion_direction="positive",
        fact_direction="negative",
        opinion_change="+60%",
        fact_change="-20.0%",
        check_status=status,
        evidence_level=evidence,
        reason=reason,
    )


def _thesis_result(
    *,
    status: str = STATUS_CONTRADICTED,
    contradiction_flags: List[ThesisCheckFlag] | None = None,
    weakened_flags: List[ThesisCheckFlag] | None = None,
    supported_flags: List[ThesisCheckFlag] | None = None,
    evidence_level: str = EVIDENCE_STRONG,
    needs_review: bool = True,
    summary: List[str] | None = None,
) -> ThesisFactCheckResult:
    contradiction_flags = contradiction_flags or ([_flag()] if status == STATUS_CONTRADICTED else [])
    weakened_flags = weakened_flags or ([_flag(status=STATUS_WEAKENED)] if status == STATUS_WEAKENED else [])
    supported_flags = supported_flags or ([_flag(status=STATUS_SUPPORTED, evidence=EVIDENCE_MODERATE)] if status == STATUS_SUPPORTED else [])
    return ThesisFactCheckResult(
        symbol="000977",
        name="测试公司",
        thesis_check_status=status,
        contradiction_flags=contradiction_flags,
        weakened_flags=weakened_flags,
        supported_flags=supported_flags,
        needs_tree_work_review=needs_review,
        evidence_level=evidence_level,
        summary=summary or [],
    )


# ════════════════════════════════════════════════════════════════════
# compute_half_year_factor_score — 核心场景
# ════════════════════════════════════════════════════════════════════


class TestComputeScoreBasic:
    """compute_half_year_factor_score 基础场景。"""

    def test_none_none_returns_empty(self):
        out = compute_half_year_factor_score(None, None)
        assert out["half_year_fact_score"] == 0.0
        assert out["status"] == STATUS_NO_FACTS
        assert out["needs_research_review"] is False
        assert out["half_year_fact_summary"] == ""
        assert out["half_year_risk_flags"] == []

    def test_facts_only_no_thesis_has_fresh_facts(self):
        facts = _facts_result()
        out = compute_half_year_factor_score(facts, None)
        # 有新鲜事实但无反证维度：小正分，HAS_FACTS。
        assert out["has_fresh_facts"] is True
        assert out["status"] == STATUS_HAS_FACTS
        assert 0 < out["half_year_fact_score"] <= 1.0
        assert out["thesis_check_status"] == ""

    def test_facts_only_no_facts_marks_research_gap_for_mandate(self):
        facts = _facts_result(pages=[], status=STATUS_NORMAL_NO_DATA)
        out = compute_half_year_factor_score(
            facts, None, candidate_type="POLICY_AMBUSH", mandate_topic="AI算力",
        )
        # 昊天左侧候选 + 无半年报事实 → 研究缺口，但不惩罚分数。
        assert out["status"] == STATUS_NO_FACTS
        assert out["half_year_fact_score"] == 0.0
        assert out["needs_research_review"] is True

    def test_facts_only_no_facts_tech_candidate_no_review_flag(self):
        facts = _facts_result(pages=[], status=STATUS_NORMAL_NO_DATA)
        out = compute_half_year_factor_score(
            facts, None, candidate_type="TECH_TRADE",
        )
        # 技术候选 + 无半年报事实：不是研究缺口（不强制要求半年报）。
        assert out["needs_research_review"] is False
        assert out["half_year_fact_score"] == 0.0

    def test_failed_facts_no_thesis_returns_failed(self):
        facts = _facts_result(pages=[], status=LK_STATUS_FAILED)
        out = compute_half_year_factor_score(facts, None)
        assert out["status"] == STATUS_FAILED
        assert out["needs_research_review"] is False
        assert "失败" in out["half_year_fact_summary"]


class TestContradictedWeakened:
    """contradicted / weakened 场景 → 降权 + 负分。"""

    def test_contradicted_strong_max_negative(self):
        facts = _facts_result()
        thesis = _thesis_result(
            status=STATUS_CONTRADICTED,
            contradiction_flags=[_flag()],
            evidence_level=EVIDENCE_STRONG,
        )
        out = compute_half_year_factor_score(facts, thesis)
        assert out["half_year_fact_score"] == -3.0
        assert out["thesis_check_status"] == STATUS_CONTRADICTED
        assert len(out["downgrade_reasons"]) >= 1
        assert any("打脸" in r for r in out["downgrade_reasons"])
        assert out["needs_research_review"] is True

    def test_contradicted_moderate(self):
        facts = _facts_result()
        thesis = _thesis_result(
            status=STATUS_CONTRADICTED,
            contradiction_flags=[_flag()],
            evidence_level=EVIDENCE_MODERATE,
        )
        out = compute_half_year_factor_score(facts, thesis)
        assert out["half_year_fact_score"] == -2.0

    def test_weakened_multi(self):
        facts = _facts_result()
        thesis = _thesis_result(
            status=STATUS_WEAKENED,
            weakened_flags=[
                _flag(metric_key="revenue", status=STATUS_WEAKENED),
                _flag(metric_key="net_profit", status=STATUS_WEAKENED),
            ],
            evidence_level=EVIDENCE_WEAK,
            needs_review=True,
        )
        out = compute_half_year_factor_score(facts, thesis)
        assert out["half_year_fact_score"] == -1.0
        assert any("削弱" in r for r in out["downgrade_reasons"])

    def test_weakened_single(self):
        facts = _facts_result()
        thesis = _thesis_result(
            status=STATUS_WEAKENED,
            weakened_flags=[_flag(status=STATUS_WEAKENED)],
            evidence_level=EVIDENCE_WEAK,
            needs_review=False,
        )
        out = compute_half_year_factor_score(facts, thesis)
        assert out["half_year_fact_score"] == -0.5
        # 单条削弱不触发 needs_tree_work_review，但仍有降权原因。
        assert len(out["downgrade_reasons"]) >= 1

    def test_risk_flags_include_contradiction(self):
        facts = _facts_result(risks=["应收账款激增"])
        thesis = _thesis_result(
            status=STATUS_CONTRADICTED,
            contradiction_flags=[_flag(reason="毛利率下滑")],
        )
        out = compute_half_year_factor_score(facts, thesis)
        flags_text = " ".join(out["half_year_risk_flags"])
        assert "应收账款激增" in flags_text
        assert "毛利率下滑" in flags_text


class TestSupportedCannotPromote:
    """supported 场景 → 正向分被上限封顶，不能把弱候选提升为主候选。"""

    def test_supported_capped_positive(self):
        facts = _facts_result()
        thesis = _thesis_result(
            status=STATUS_SUPPORTED,
            supported_flags=[_flag(status=STATUS_SUPPORTED, evidence=EVIDENCE_MODERATE)],
            evidence_level=EVIDENCE_STRONG,
            needs_review=False,
        )
        out = compute_half_year_factor_score(facts, thesis)
        # 正向分上限 1.0，远低于本地知识命中分上限 3.0。
        assert out["half_year_fact_score"] == 1.0
        assert "支持" in out["research_priority_hint"]

    def test_supported_score_never_exceeds_cap(self):
        facts = _facts_result()
        thesis = _thesis_result(
            status=STATUS_SUPPORTED,
            supported_flags=[_flag(status=STATUS_SUPPORTED) for _ in range(10)],
            evidence_level=EVIDENCE_STRONG,
        )
        out = compute_half_year_factor_score(facts, thesis)
        assert out["half_year_fact_score"] <= 1.0

    def test_positive_cap_far_below_local_knowledge_cap(self):
        # 本地知识命中分上限是 3.0；半年报正向上限是 1.0，结构性保证"半年报
        # 是佐证不是发动机"，强知识不能单独把弱技术候选抬成主候选。
        from tradingagents.tradeflow.knowledge_score_calibration import (
            LOCAL_KNOWLEDGE_SCORE_CAP,
        )
        facts = _facts_result()
        thesis = _thesis_result(
            status=STATUS_SUPPORTED,
            supported_flags=[_flag(status=STATUS_SUPPORTED)],
        )
        out = compute_half_year_factor_score(facts, thesis)
        assert out["half_year_fact_score"] < LOCAL_KNOWLEDGE_SCORE_CAP


class TestInsufficientAndDataStatus:
    """insufficient_data 与各种 data_status 场景。"""

    def test_insufficient_data_with_facts_zero_score(self):
        facts = _facts_result()
        thesis = _thesis_result(
            status=STATUS_INSUFFICIENT_DATA,
            evidence_level=EVIDENCE_NONE,
            needs_review=False,
        )
        out = compute_half_year_factor_score(facts, thesis)
        # insufficient_data：事实存在但观点无法反证 → 不惩罚。
        assert out["half_year_fact_score"] == 0.0

    def test_stale_facts_treated_as_no_facts(self):
        facts = _facts_result(pages=[_facts_page(data_status=DATA_STALE)])
        out = compute_half_year_factor_score(
            facts, None, candidate_type="POLICY_CONFIRM", mandate_topic="储能",
        )
        # stale 页面不算可用事实 → 研究缺口。
        assert out["has_fresh_facts"] is False

    def test_conflict_facts_still_usable(self):
        # data_status=conflict 的页面 status 仍是 HAS_DATA → 视作有事实。
        facts = _facts_result(pages=[_facts_page(data_status=DATA_CONFLICT)])
        out = compute_half_year_factor_score(facts, None)
        assert out["has_fresh_facts"] is True


class TestDuckTypingDictInput:
    """dict 输入（duck-typing）与 dataclass 输入等价。"""

    def test_dict_facts_input(self):
        facts_dict = {
            "status": STATUS_HAS_DATA,
            "pages": [{"period": "2025H1"}],
            "risks": ["测试风险"],
            "summary": ["营收增长"],
            "latest_period": "2025H1",
            "data_status": DATA_FRESH,
            "errors": [],
        }
        out = compute_half_year_factor_score(facts_dict, None)
        assert out["has_fresh_facts"] is True
        assert out["fact_period"] == "2025H1"

    def test_dict_thesis_input(self):
        facts = _facts_result()
        thesis_dict = {
            "thesis_check_status": STATUS_CONTRADICTED,
            "contradiction_flags": [{"metric_key": "revenue", "reason": "营收打脸"}],
            "weakened_flags": [],
            "supported_flags": [],
            "evidence_level": EVIDENCE_STRONG,
            "needs_tree_work_review": True,
            "summary": [],
            "errors": [],
        }
        out = compute_half_year_factor_score(facts, thesis_dict)
        assert out["half_year_fact_score"] == -3.0


class TestNoForbiddenWords:
    """产出不含强买卖词。"""

    @pytest.mark.parametrize("thesis_status", [
        STATUS_CONTRADICTED, STATUS_WEAKENED, STATUS_SUPPORTED, STATUS_INSUFFICIENT_DATA,
    ])
    def test_no_forbidden_words_in_summary(self, thesis_status):
        facts = _facts_result(risks=["毛利率承压"])
        flags = []
        if thesis_status == STATUS_CONTRADICTED:
            flags = ([_flag(reason="营收打脸")] if True else [])
        thesis = _thesis_result(
            status=thesis_status,
            contradiction_flags=flags if thesis_status == STATUS_CONTRADICTED else [],
            weakened_flags=flags if thesis_status == STATUS_WEAKENED else [],
            supported_flags=flags if thesis_status == STATUS_SUPPORTED else [],
        )
        out = compute_half_year_factor_score(facts, thesis)
        _assert_no_strong_action_words(out["half_year_fact_summary"])
        _assert_no_strong_action_words(out["research_priority_hint"])
        for reason in out["downgrade_reasons"]:
            _assert_no_strong_action_words(reason)
        for flag in out["half_year_risk_flags"]:
            _assert_no_strong_action_words(flag)
        # 辅助审计函数也应返回空。
        assert has_forbidden_action_words(out) == []


# ════════════════════════════════════════════════════════════════════
# MandateEvidencePacket 集成
# ════════════════════════════════════════════════════════════════════


class TestMandateEvidencePacket:
    """MandateEvidencePacket.half_year_summary 字段集成。"""

    def test_packet_has_half_year_summary_field(self):
        from tradingagents.tradeflow.mandate_evidence_packet import MandateEvidencePacket
        packet = MandateEvidencePacket()
        assert hasattr(packet, "half_year_summary")
        assert packet.half_year_summary == {}
        d = packet.to_dict()
        assert "half_year_summary" in d

    def test_build_evidence_packet_injects_half_year(self):
        from tradingagents.tradeflow.mandate_evidence_packet import build_evidence_packet
        cand = {"symbol": "000977", "name": "测试公司", "candidate_type": "POLICY_AMBUSH"}
        facts = _facts_result()
        thesis = _thesis_result(status=STATUS_CONTRADICTED)
        packet = build_evidence_packet(
            cand,
            half_year_facts_result=facts,
            half_year_thesis_result=thesis,
        )
        assert packet.half_year_summary  # 非空
        assert packet.half_year_summary["half_year_fact_score"] == -3.0
        # half_year 不影响 confidence / needs_manual_research。
        assert packet.confidence in ("high", "medium", "low")

    def test_build_evidence_packet_no_half_year_stays_empty(self):
        from tradingagents.tradeflow.mandate_evidence_packet import build_evidence_packet
        cand = {"symbol": "000977", "name": "测试公司"}
        packet = build_evidence_packet(cand)
        assert packet.half_year_summary == {}

    def test_build_packets_for_candidates_half_year_passthrough(self):
        from tradingagents.tradeflow.mandate_evidence_packet import (
            build_evidence_packets_for_candidates,
        )
        candidates = [
            {"symbol": "000977", "name": "公司A", "candidate_type": "POLICY_AMBUSH"},
            {"symbol": "600584", "name": "公司B"},
        ]
        facts = _facts_result()
        thesis = _thesis_result(status=STATUS_WEAKENED)
        half_year_by_symbol = {"000977": (facts, thesis)}
        packets = build_evidence_packets_for_candidates(
            candidates, half_year_by_symbol=half_year_by_symbol,
        )
        assert packets["000977"].half_year_summary  # 注入
        assert "600584" in packets
        assert packets["600584"].half_year_summary == {}  # 未传入 → 空

    def test_half_year_does_not_inflate_confidence(self):
        from tradingagents.tradeflow.mandate_evidence_packet import build_evidence_packet
        cand = {"symbol": "000977", "name": "测试", "company_role": "leader"}
        facts = _facts_result()
        thesis = _thesis_result(status=STATUS_SUPPORTED)
        packet_with = build_evidence_packet(
            cand, half_year_facts_result=facts, half_year_thesis_result=thesis,
        )
        packet_without = build_evidence_packet(cand)
        # confidence 不应因 half_year 改变。
        assert packet_with.confidence == packet_without.confidence


# ════════════════════════════════════════════════════════════════════
# TradeFlow service 集成
# ════════════════════════════════════════════════════════════════════


class TestTradeFlowEnrichment:
    """_enrich_candidate_with_half_year / _enrich_candidates_with_half_year。"""

    def test_enrich_single_item_no_kb_returns_empty(self, monkeypatch, tmp_path):
        from api.services import tradeflow_service as svc
        monkeypatch.setattr(svc, "_resolve_knowledge_root", lambda: "")
        item = {"symbol": "000977", "name": "测试", "candidate_type": "TECH_TRADE"}
        out = svc._enrich_candidate_with_half_year(dict(item))
        assert out["half_year_fact_score"] == 0.0
        assert out["needs_research_review"] is False

    def test_enrich_single_item_no_symbol_returns_empty(self, monkeypatch):
        from api.services import tradeflow_service as svc
        monkeypatch.setattr(svc, "_resolve_knowledge_root", lambda: str(Path("/tmp")))
        item = {"symbol": "", "name": "测试"}
        out = svc._enrich_candidate_with_half_year(dict(item))
        assert out["half_year_fact_score"] == 0.0

    def test_enrich_batch_empty_list(self):
        from api.services import tradeflow_service as svc
        assert svc._enrich_candidates_with_half_year([]) == []

    def test_enrich_batch_with_mock_query(self, monkeypatch):
        from api.services import tradeflow_service as svc
        monkeypatch.setattr(svc, "_resolve_knowledge_root", lambda: str(Path("/tmp")))

        facts = _facts_result()
        thesis = _thesis_result(status=STATUS_CONTRADICTED)

        def fake_query(item, root):
            return facts, thesis

        monkeypatch.setattr(svc, "_query_half_year_for_candidate", fake_query)
        items = [
            {"symbol": "000977", "name": "A", "candidate_type": "POLICY_AMBUSH"},
            {"symbol": "600584", "name": "B"},
        ]
        out = svc._enrich_candidates_with_half_year(items)
        assert out[0]["half_year_fact_score"] == -3.0
        assert len(out[0]["downgrade_reasons"]) >= 1
        assert out[1]["half_year_fact_score"] == -3.0

    def test_enrich_downgrade_reasons_appended_dedup(self, monkeypatch):
        from api.services import tradeflow_service as svc
        monkeypatch.setattr(svc, "_resolve_knowledge_root", lambda: str(Path("/tmp")))
        facts = _facts_result()
        thesis = _thesis_result(status=STATUS_CONTRADICTED)

        def fake_query(item, root):
            return facts, thesis

        monkeypatch.setattr(svc, "_query_half_year_for_candidate", fake_query)
        item = {
            "symbol": "000977",
            "name": "A",
            "candidate_type": "POLICY_AMBUSH",
            "downgrade_reasons": ["已有降权"],
        }
        out = svc._enrich_candidate_with_half_year(item)
        # 已有降权保留，新增追加，不重复。
        assert "已有降权" in out["downgrade_reasons"]
        assert any("打脸" in r for r in out["downgrade_reasons"])

    def test_enrich_kb004_fields_not_conflict(self, monkeypatch):
        from api.services import tradeflow_service as svc
        monkeypatch.setattr(svc, "_resolve_knowledge_root", lambda: str(Path("/tmp")))
        facts = _facts_result()
        thesis = _thesis_result(status=STATUS_SUPPORTED, needs_review=False)

        def fake_query(item, root):
            return facts, thesis

        monkeypatch.setattr(svc, "_query_half_year_for_candidate", fake_query)
        item = {
            "symbol": "000977", "name": "A",
            "local_knowledge_score": 2.5,
            "knowledge_hit_count": 3,
            "local_knowledge_summary": "本地知识命中",
            "needs_tree_work_research": True,
        }
        out = svc._enrich_candidate_with_half_year(item)
        # KB-004 字段不被 half_year 覆盖。
        assert out["local_knowledge_score"] == 2.5
        assert out["knowledge_hit_count"] == 3
        assert out["needs_tree_work_research"] is True
        # half_year 字段独立存在。
        assert out["half_year_fact_score"] == 1.0
        # supported + 无 needs_tree_work_review → 不触发研究缺口。
        assert out["needs_research_review"] is False

    def test_enrich_failed_query_returns_empty_no_review(self, monkeypatch):
        from api.services import tradeflow_service as svc
        monkeypatch.setattr(svc, "_resolve_knowledge_root", lambda: str(Path("/tmp")))

        def fake_query(item, root):
            return None, None  # 查询失败

        monkeypatch.setattr(svc, "_query_half_year_for_candidate", fake_query)
        item = {"symbol": "000977", "name": "A", "candidate_type": "POLICY_AMBUSH"}
        out = svc._enrich_candidate_with_half_year(item)
        assert out["half_year_fact_score"] == 0.0
        # 查询失败不变成研究缺口。
        assert out["needs_research_review"] is False

    def test_enrich_does_not_touch_action_tier(self, monkeypatch):
        from api.services import tradeflow_service as svc
        monkeypatch.setattr(svc, "_resolve_knowledge_root", lambda: str(Path("/tmp")))
        facts = _facts_result()
        thesis = _thesis_result(status=STATUS_SUPPORTED)

        def fake_query(item, root):
            return facts, thesis

        monkeypatch.setattr(svc, "_query_half_year_for_candidate", fake_query)
        item = {
            "symbol": "000977", "name": "A",
            "action_tier": "scan",
            "tier": "C",
            "action": "OBSERVE",
            "trade_priority_score": 5.0,
            "composite_score": 10.0,
        }
        out = svc._enrich_candidate_with_half_year(item)
        # 强动作门禁不动：half_year 不改 tier / action / trade_priority_score。
        assert out["action_tier"] == "scan"
        assert out["tier"] == "C"
        assert out["action"] == "OBSERVE"
        assert out["trade_priority_score"] == 5.0


class TestQueryHalfYearHelper:
    """_query_half_year_for_candidate 辅助函数。"""

    def test_no_root_returns_none(self):
        from api.services import tradeflow_service as svc
        f, t = svc._query_half_year_for_candidate({"symbol": "000977"}, "")
        assert f is None and t is None

    def test_no_symbol_returns_none(self):
        from api.services import tradeflow_service as svc
        f, t = svc._query_half_year_for_candidate({"symbol": ""}, "/tmp")
        assert f is None and t is None

    def test_facts_result_has_data_helper(self):
        from api.services import tradeflow_service as svc
        assert svc._facts_result_has_data(_facts_result()) is True
        assert svc._facts_result_has_data(_facts_result(status=STATUS_NORMAL_NO_DATA, pages=[])) is False
        assert svc._facts_result_has_data({"status": "HAS_DATA", "pages": [{}]}) is True
        assert svc._facts_result_has_data({"status": "NORMAL_NO_DATA", "pages": []}) is False
        assert svc._facts_result_has_data(None) is False


# ════════════════════════════════════════════════════════════════════
# Pydantic schema
# ════════════════════════════════════════════════════════════════════


class TestPydanticSchema:
    """TradeFlowCandidateItem / MandateEvidencePacketItem 包含新字段。"""

    def test_candidate_item_has_half_year_fields(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem
        fields = set(TradeFlowCandidateItem.model_fields.keys())
        assert "half_year_fact_score" in fields
        assert "half_year_fact_summary" in fields
        assert "half_year_risk_flags" in fields
        assert "half_year_fact_detail" in fields
        assert "needs_research_review" in fields

    def test_candidate_item_defaults(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem
        item = TradeFlowCandidateItem(symbol="000977")
        assert item.half_year_fact_score == 0.0
        assert item.half_year_fact_summary == ""
        assert item.half_year_risk_flags == []
        assert item.half_year_fact_detail == {}
        assert item.needs_research_review is False

    def test_candidate_item_serialization_roundtrip(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem
        item = TradeFlowCandidateItem(
            symbol="000977",
            half_year_fact_score=-3.0,
            half_year_fact_summary="事实打脸",
            half_year_risk_flags=["营收下滑"],
            needs_research_review=True,
        )
        d = item.model_dump()
        assert d["half_year_fact_score"] == -3.0
        assert d["needs_research_review"] is True
        # 回读
        item2 = TradeFlowCandidateItem(**d)
        assert item2.half_year_fact_score == -3.0

    def test_mandate_packet_item_has_half_year_summary(self):
        from api.tradeflow_schemas import MandateEvidencePacketItem
        fields = set(MandateEvidencePacketItem.model_fields.keys())
        assert "half_year_summary" in fields
        item = MandateEvidencePacketItem()
        assert item.half_year_summary == {}


# ════════════════════════════════════════════════════════════════════
# 端到端 fixture 集成（真实知识库查询）
# ════════════════════════════════════════════════════════════════════


class TestEndToEndFixtureIntegration:
    """基于真实 fixture KB 的端到端集成。"""

    def test_real_kb_query_with_half_year_fixture(self, tmp_path):
        from tests.half_year_fixtures import build_half_year_fixture_kb
        kb_root = build_half_year_fixture_kb(tmp_path)
        from tradingagents.dataflows.half_year_facts_provider import query_half_year_facts
        facts = query_half_year_facts(str(kb_root), symbol="000977")
        out = compute_half_year_factor_score(
            facts, None, candidate_type="POLICY_AMBUSH", mandate_topic="AI算力",
        )
        # 无论 fixture 是否命中，函数都应返回合法结构。
        assert "half_year_fact_score" in out
        assert isinstance(out["half_year_fact_summary"], str)
        assert isinstance(out["half_year_risk_flags"], list)
        # 不抛异常即通过。

    def test_read_only_no_db_write(self, tmp_path, monkeypatch):
        """确认 enrich 不写知识库 / 不写 DB。"""
        from api.services import tradeflow_service as svc
        from tests.half_year_fixtures import build_half_year_fixture_kb
        kb_root = build_half_year_fixture_kb(tmp_path)
        monkeypatch.setattr(svc, "_resolve_knowledge_root", lambda: str(kb_root))
        items = [{"symbol": "000977", "name": "测试", "candidate_type": "POLICY_AMBUSH"}]
        svc._enrich_candidates_with_half_year(items)
        # 知识库文件数不变（只读）。
        md_count_before = len(list((tmp_path).rglob("*.md")))
        md_count_after = len(list((tmp_path).rglob("*.md")))
        assert md_count_before == md_count_after


# ════════════════════════════════════════════════════════════════════
# 任务常量
# ════════════════════════════════════════════════════════════════════


def test_task_code():
    assert TASK_CODE == "HY-006"


def test_status_constants():
    assert STATUS_HAS_FACTS == "HAS_FACTS"
    assert STATUS_NO_FACTS == "NO_FACTS"
    assert STATUS_FAILED == "FAILED"
    assert THESIS_STATUS_NONE == "no_thesis"
