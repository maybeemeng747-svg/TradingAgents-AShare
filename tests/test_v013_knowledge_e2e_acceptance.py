# [V-013] knowledge_e2e_acceptance
"""V-013 端到端验收：Tree Work → TA → TradeFlow → investment-controller 知识链路.

任务要求（docs/TASKS.md V-013）:
    端到端验收本地知识链路：Tree Work wiki 被 TA raw_evidence 读取，报告展示补充
    区块，TradeFlow 候选显示知识分，investment-controller context 只读引用。

验收重点（docs/TASKS.md line 4723）:
    1. 字段完整
    2. 来源可追溯
    3. 权限只读
    4. 无强动作越权

验收报告必须回答的四个问题（docs/TASKS.md line 4731）:
    Q1: 命中哪些知识？
    Q2: 是否过期？
    Q3: 如何影响研究优先级？
    Q4: 是否改变交易动作？

执行约束:
    - fixture / dry-run，不调用 live LLM
    - 不写生产 tradingagents.db
    - 不改 tradingagents/prompts/

链路覆盖:
    Stage A (KB-003): query_local_knowledge → LocalKnowledgeQueryResult
    Stage B (KB-003 wiring): build_raw_evidence_entry → raw_evidence["local_knowledge"]
    Stage C (KB-003 + KB-008): attach_report_local_knowledge → 报告顶层字段
                                （只加 local_knowledge_block / *_summary，不动 decision/gate）
    Stage D (KB-004 + TF-KB-001): _enrich_candidate_with_local_knowledge → 候选字段
                                  （不改 tier / action / 强动作门禁）
    Stage E (KB-006): collect_local_knowledge_hits + _build_controller_hints → IC context
                      local_knowledge_hits 桶 + research_review lane
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest

# 复用 KB-007 同款 fixture 知识库（与 KB-004/KB-008/KB-011/KB-012/REPORT-UX-004 保持一致）
from tests.test_kb007_research_attention import (
    _COMPANY_PAGE_A,
    _COMPANY_PAGE_B,
    _EXPIRED_PAGE,
    _FUND_PAGE,
    _HK_PAGE,
    _NO_SYMBOLS_PAGE,
    _SCORE_TABLE_PAGE,
    _UNLISTED_PAGE,
    _US_TODO_PAGE,
    fixture_kb as _kb007_fixture_kb,
)
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    VENDOR,
    LocalKnowledgeQueryResult,
    build_raw_evidence_entry,
    compute_local_knowledge_score,
    needs_tree_work_research,
    query_failed_entry,
    query_local_knowledge,
    render_local_knowledge_block,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants & helpers
# ─────────────────────────────────────────────────────────────────────────────

# [V-013] 扩展版强动作词集合（对齐 IC-TA-001 / V-009 / V-012 / TRACK-006 巡检口径）
FORBIDDEN_STRONG_VERBS: tuple[str, ...] = (
    "立即买入",
    "立即卖出",
    "立即清仓",
    "满仓",
    "清仓",
    "全仓",
    "重仓买入",
    "梭哈",
    "强烈推荐",
)

# [V-013] 强动作门禁字段（任何 KB attach 都不能改写这些键）
STRONG_GATE_FIELDS: tuple[str, ...] = (
    "decision",
    "direction",
    "execution_action",
    "action_label",
    "confidence",
    "target_price",
    "stop_loss_price",
    "final_trade_decision",
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _assert_no_strong_verbs(*texts: Any, where: str = "") -> None:
    """扫描任意合成文本，禁止强动作词越权。"""
    for text in texts:
        if not text:
            continue
        hay = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
        for verb in FORBIDDEN_STRONG_VERBS:
            assert verb not in hay, (
                f"[V-013] 强动作词 '{verb}' 出现在 {where}: {hay[:200]}"
            )


# 私人 inbox 页（必须在 wiki/investment 之外，KB-006 partition isolation 回归用）
_PRIVATE_INBOX_PAGE = """---
title: 私人笔记-绝不能透出
created: 2026-06-01
updated: 2026-06-01
sources:
  - "私人备忘"
tags: [私人, 备忘]
symbols: ["603296.SH 华勤技术"]
themes: [私人主题]
report_type: 私人
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 私人备忘

立即买入华勤技术，满仓梭哈，全仓干，强烈推荐。

## 风险提示

- 无
"""


@pytest.fixture()
def fixture_kb_v013(tmp_path: Path) -> Path:
    """V-013 mini Tree Work 知识库（在 KB-007 同款 fixture 之上加私人 inbox 页）."""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE_A)
    _write(inv / "AI算力基础设施-公司评分表.md", _SCORE_TABLE_PAGE)
    _write(inv / "华勤技术-深度研究.md", _COMPANY_PAGE_B)
    _write(inv / "腾讯控股-游戏复苏.md", _HK_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _US_TODO_PAGE)
    _write(inv / "沪深300ETF-指数跟踪.md", _FUND_PAGE)
    _write(inv / "某私募主体-调研纪要.md", _UNLISTED_PAGE)
    _write(inv / "某周期股-已过期.md", _EXPIRED_PAGE)
    _write(inv / "行业综述-无标的.md", _NO_SYMBOLS_PAGE)
    # 私人 inbox 页（在 wiki/investment 之外，必须被 partition isolation 屏蔽）
    inbox = tmp_path / "inbox"
    _write(inbox / "私人笔记.md", _PRIVATE_INBOX_PAGE)
    return tmp_path


@pytest.fixture()
def fixture_kb(fixture_kb_v013: Path) -> Path:
    """对齐 KB-007 fixture 命名（便于跨任务复用同一份 fixture）."""
    return fixture_kb_v013


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: KB-003 query_local_knowledge — 知识库可被读取且状态分类正确
# ─────────────────────────────────────────────────────────────────────────────


class TestStageA_KB003_Query:
    """Q1/Q2: 命中哪些知识？是否过期？"""

    def test_fresh_hit_returns_has_data(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296.SH")
        assert result.status == STATUS_HAS_DATA
        assert len(result.matched_pages) >= 1
        assert result.vendor == VENDOR
        # Q1: 命中页必须可追溯（rel_path / title / updated_at / confidence）
        for m in result.matched_pages:
            assert m.rel_path
            assert m.title
            assert m.updated_at  # 来源时间戳必须存在
            assert m.confidence in ("high", "medium", "low")

    def test_stale_hit_returns_stale_status(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="600000.SH")
        assert result.status == STATUS_STALE
        assert any(m.is_stale for m in result.matched_pages)

    def test_low_confidence_hit_returns_low_status(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="DELL.US")
        assert result.status == STATUS_LOW_CONFIDENCE
        assert any(m.is_low_confidence or m.is_to_be_supplemented
                   for m in result.matched_pages)

    def test_no_hit_returns_normal_no_data(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="999999.SH")
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.matched_pages == []

    def test_failed_when_knowledge_root_missing(self, tmp_path: Path):
        missing = tmp_path / "does-not-exist"
        result = query_local_knowledge(str(missing), symbol="603296.SH")
        assert result.status in (STATUS_FAILED, STATUS_NORMAL_NO_DATA)


# ─────────────────────────────────────────────────────────────────────────────
# Stage B: KB-003 build_raw_evidence_entry — raw_evidence["local_knowledge"]
# 契约字段完整、来源可追溯
# ─────────────────────────────────────────────────────────────────────────────


class TestStageB_RawEvidenceContract:
    """来源可追溯：raw_evidence.local_knowledge 条目必须带齐 vendor / endpoint /
    source_type / as_of / fetched_at / record_count。"""

    def test_raw_evidence_entry_contract_fields(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296.SH")
        entry = build_raw_evidence_entry(result, "2026-07-05", "2026-07-05T04:00:00")
        assert entry["field"] == "local_knowledge"
        assert entry["vendor"] == VENDOR
        assert entry["endpoint"] == "wiki/investment"
        assert entry["source_type"] == "tree_work_wiki"
        assert entry["as_of"] == "2026-07-05"
        assert entry["fetched_at"] == "2026-07-05T04:00:00"
        assert entry["record_count"] == len(result.matched_pages)
        # raw payload 必须可往返序列化（report_service 用 from_dict 反序列化）
        roundtrip = LocalKnowledgeQueryResult.from_dict(entry["raw"])
        assert roundtrip.status == result.status
        assert len(roundtrip.matched_pages) == len(result.matched_pages)

    def test_failed_entry_does_not_carry_raw(self):
        entry = query_failed_entry("2026-07-05", "2026-07-05T04:00:00", "boom")
        assert entry["status"] == STATUS_FAILED
        assert entry["raw"] is None
        assert entry["vendor"] == VENDOR
        assert entry["source_type"] == "tree_work_wiki"

    def test_raw_evidence_entry_no_strong_verbs(self, fixture_kb: Path):
        """合成字段不得包含任何强动作词（防御性扫描整个 entry JSON）."""
        result = query_local_knowledge(str(fixture_kb), symbol="603296.SH")
        entry = build_raw_evidence_entry(result, "2026-07-05", "2026-07-05T04:00:00")
        _assert_no_strong_verbs(entry, where="raw_evidence entry")


# ─────────────────────────────────────────────────────────────────────────────
# Stage C: KB-003 + KB-008 attach_report_local_knowledge — 报告顶层补充区块
# 强动作门禁字段不被改写
# ─────────────────────────────────────────────────────────────────────────────


def _make_baseline_report(symbol: str = "603296.SH") -> Dict[str, Any]:
    """构造一份带强动作门禁字段的基线报告（模拟 TA pipeline 产出）。"""
    return {
        "symbol": symbol,
        "decision": "WAIT",
        "direction": "neutral",
        "execution_action": "WAIT",
        "action_label": "数据不足观察",
        "confidence": 0.4,
        "target_price": None,
        "stop_loss_price": None,
        "final_trade_decision": "Decision: WAIT\nBuy Level: 1\nRisk Level: 2",
        "metadata": {
            "raw_evidence": {
                "local_knowledge": build_raw_evidence_entry(
                    query_local_knowledge(_resolve_test_root(), symbol=symbol),
                    "2026-07-05",
                    "2026-07-05T04:00:00",
                ),
            }
        },
    }


def _resolve_test_root() -> str:
    """临时占位：测试运行时由 monkeypatch 注入 AUTO_DEV_KNOWLEDGE_ROOT。

    实际默认值不影响测试结果——baseline report 在 fixture 内通过
    monkeypatch.setenv 注入临时知识库根后再构造。
    """
    from tradingagents.dataflows.local_knowledge_audit import default_knowledge_root
    return default_knowledge_root()


class TestStageC_ReportAttach:
    """Q3/Q4: attach 后 KB block 出现，但 decision / gate 不动。"""

    def test_attach_adds_local_knowledge_block(self, fixture_kb: Path, monkeypatch):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.report_service import attach_report_local_knowledge

        report = _make_baseline_report("603296.SH")
        out = attach_report_local_knowledge(report, symbol="603296.SH")
        assert isinstance(out, dict)
        # 本地知识补充区块必须存在
        assert out.get("local_knowledge_block")
        assert isinstance(out.get("local_knowledge_summary"), dict)
        assert out["local_knowledge_summary"]["status"] == STATUS_HAS_DATA
        assert out["local_knowledge_summary"]["matched_count"] >= 1

    def test_attach_does_not_touch_strong_gate(self, fixture_kb: Path, monkeypatch):
        """Q4 核心回归：KB attach 不改任何强动作门禁字段。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.report_service import attach_report_local_knowledge

        report = _make_baseline_report("603296.SH")
        baseline_snapshot = {k: report.get(k) for k in STRONG_GATE_FIELDS}
        out = attach_report_local_knowledge(report, symbol="603296.SH")
        for field in STRONG_GATE_FIELDS:
            assert out.get(field) == baseline_snapshot[field], (
                f"[V-013] KB attach 改写了强动作门禁字段 {field}: "
                f"{baseline_snapshot[field]!r} → {out.get(field)!r}"
            )

    def test_attach_no_strong_verbs_in_synthesised_text(
        self, fixture_kb: Path, monkeypatch
    ):
        """合成的 local_knowledge_block / summary 不得包含强动作词。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.report_service import attach_report_local_knowledge

        report = _make_baseline_report("603296.SH")
        out = attach_report_local_knowledge(report, symbol="603296.SH")
        _assert_no_strong_verbs(
            out.get("local_knowledge_block"),
            out.get("local_knowledge_summary"),
            out.get("research_attention_block"),
            out.get("research_attention_summary"),
            where="attach_report_local_knowledge output",
        )

    def test_attach_is_additive_only(self, fixture_kb: Path, monkeypatch):
        """KB-003/KB-008 是纯加性：不修改任何既有键（除新增 KB 字段外）。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.report_service import attach_report_local_knowledge

        report = _make_baseline_report("603296.SH")
        report["existing_marker"] = "preserve-me"
        report["nested"] = {"k": "v"}
        out = attach_report_local_knowledge(report, symbol="603296.SH")
        assert out["existing_marker"] == "preserve-me"
        assert out["nested"] == {"k": "v"}

    def test_attach_failed_knowledge_does_not_break_report(
        self, tmp_path: Path, monkeypatch
    ):
        """知识库不可达时，attach 必须静默失败，返回原 report。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(tmp_path / "no-such-dir"))
        from api.services.report_service import attach_report_local_knowledge

        report = {"symbol": "603296.SH", "decision": "WAIT"}
        out = attach_report_local_knowledge(report, symbol="603296.SH")
        # 既不抛异常，也不破坏原有字段
        assert out["decision"] == "WAIT"


# ─────────────────────────────────────────────────────────────────────────────
# Stage D: KB-004 + TF-KB-001 — TradeFlow 候选显示知识分但不变 tier / action
# ─────────────────────────────────────────────────────────────────────────────


def _make_candidate(symbol: str = "603296.SH", **overrides: Any) -> Dict[str, Any]:
    base = {
        "symbol": symbol,
        "name": "华勤技术" if "603296" in symbol else symbol,
        "candidate_type": "POLICY_AMBUSH",
        "mandate_topic": "AI服务器",
        "mandate_score": 5.0,
        "topic_lifecycle_state": "rising",
        "tier": "B",
        "action": "OBSERVE",
        "tradeflow_data_completeness": 0.7,
    }
    base.update(overrides)
    return base


class TestStageD_TradeFlowEnrichment:
    """Q1/Q3: 候选展示知识分 + 研究优先级提示；Q4: 不变 tier / action。"""

    def test_candidate_carries_local_knowledge_summary(self, fixture_kb, monkeypatch):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        item = _make_candidate("603296.SH")
        out = _enrich_candidate_with_local_knowledge(item)
        # 候选必须显示知识分和摘要
        assert out["local_knowledge_score"] > 0.0
        assert out["knowledge_hit_count"] >= 1
        assert "本地知识命中" in out["local_knowledge_summary"]
        # local_knowledge_detail 必须能回答 Q1（命中哪些知识）
        detail = out["local_knowledge_detail"]
        assert detail["has_hit"] is True
        assert detail["knowledge_hit_count"] >= 1
        assert detail["matched_pages_brief"]  # 命中页摘要
        for brief in detail["matched_pages_brief"]:
            assert brief["rel_path"]
            assert brief["title"]

    def test_candidate_summary_no_strong_verbs(self, fixture_kb, monkeypatch):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        out = _enrich_candidate_with_local_knowledge(_make_candidate("603296.SH"))
        _assert_no_strong_verbs(
            out["local_knowledge_summary"],
            out["local_knowledge_detail"],
            where="TradeFlow candidate enrichment",
        )

    def test_stale_hit_does_not_inflate_score(self, fixture_kb, monkeypatch):
        """Q2/Q3: stale 命中不抬升研究优先级。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        out = _enrich_candidate_with_local_knowledge(
            _make_candidate("600000.SH", mandate_topic="银行")
        )
        # 有命中但全为 stale → score = 0
        assert out["local_knowledge_score"] == 0.0
        assert out["knowledge_hit_count"] >= 1  # 有命中页
        # summary 必须明确告诉用户"过期"
        assert "过期" in out["local_knowledge_summary"] or "stale" in out[
            "local_knowledge_summary"
        ].lower() or "低置信" in out["local_knowledge_summary"]

    def test_enrichment_does_not_change_tier_or_action(
        self, fixture_kb, monkeypatch
    ):
        """Q4 核心回归：KB enrichment 不改 tier / action（TF-KB-001 不变式）."""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        item = _make_candidate("603296.SH")
        item["tier"] = "C"
        item["action"] = "OBSERVE"
        out = _enrich_candidate_with_local_knowledge(item)
        assert out["tier"] == "C"
        assert out["action"] == "OBSERVE"

    def test_needs_tree_work_research_flag_for_knowledge_gap(
        self, fixture_kb, monkeypatch
    ):
        """Q3 反向：知识缺失但主题热的候选标记 needs_tree_work_research。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        out = _enrich_candidate_with_local_knowledge(
            _make_candidate(
                "999999.SH",
                name="某概念股",
                candidate_type="POLICY_AMBUSH",
                mandate_topic="低空经济",
                mandate_score=4.5,
            )
        )
        assert out["needs_tree_work_research"] is True
        assert out["local_knowledge_score"] == 0.0

    def test_tf_kb001_knowledge_does_not_promote_tier(self, fixture_kb, monkeypatch):
        """TF-KB-001 不变式：极端知识分不会把 tier 推高（adversarial）。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from tradingagents.tradeflow.knowledge_score_calibration import (
            build_calibration_fixtures,
            verify_no_knowledge_promotion,
        )

        # 该函数对一组合成极端 knowledge 分数执行对抗性扫描
        report = verify_no_knowledge_promotion(build_calibration_fixtures())
        assert report["all_passed"] is True
        assert report["scorer_signature_clean"] is True
        assert report["violations"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Stage E: KB-006 — investment-controller context local_knowledge_hits 桶
# ─────────────────────────────────────────────────────────────────────────────


class TestStageE_InvestmentControllerContext:
    """Q1/Q2/Q3: IC context 一次性引用本地知识命中 + 来源可追溯 + 不含长原文。"""

    def test_search_local_knowledge_returns_slim_payload(
        self, fixture_kb, monkeypatch
    ):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.local_knowledge_context_service import (
            search_local_knowledge,
        )

        payload = search_local_knowledge(
            symbol="603296.SH", knowledge_root=str(fixture_kb)
        )
        assert payload["vendor"] == VENDOR
        assert payload["endpoint"] == "wiki/investment"
        assert payload["read_only"] is True
        assert payload["hit_count"] >= 1
        # 每个 hit 必须有 rel_path（来源可追溯），但绝无 page body
        for hit in payload["hits"]:
            assert hit.get("rel_path")
            assert hit.get("title")
            # summary_snippet ≤ 200 字符（KB-006 契约）
            snippet = hit.get("summary_snippet") or ""
            assert len(snippet) <= 200
            # 不允许出现整页正文
            assert "body" not in hit
            assert "content" not in hit

    def test_search_no_strong_verbs(self, fixture_kb, monkeypatch):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.local_knowledge_context_service import (
            assert_no_strong_action_verbs,
            search_local_knowledge,
        )

        payload = search_local_knowledge(
            symbol="603296.SH", knowledge_root=str(fixture_kb)
        )
        # KB-006 内置断言
        assert_no_strong_action_verbs(payload)
        # V-013 扩展词集
        _assert_no_strong_verbs(payload, where="search_local_knowledge payload")

    def test_collect_local_knowledge_hits_for_ic_context(
        self, fixture_kb, monkeypatch
    ):
        """IC context local_knowledge_hits 桶字段完整且可被 controller 一次读取。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
        )

        bucket = collect_local_knowledge_hits(
            symbols=["603296.SH", "999999.SH", "600000.SH"],
            themes=["AI服务器"],
            knowledge_root=str(fixture_kb),
            as_of="2026-07-05 04:00:00",
        )
        assert bucket["source"] == "local_knowledge_context"
        assert bucket["read_only"] is True
        assert bucket["as_of"] == "2026-07-05 04:00:00"
        assert bucket["data_status"] in ("fresh", "stale", "missing", "failed", "skipped")
        assert isinstance(bucket["items"], list)
        # Q1: 每个 item 必须可回答"命中哪些知识"
        for item in bucket["items"]:
            assert item["symbol"]
            assert "hit_count" in item
            assert "fresh_hit_count" in item
            assert "data_status" in item
            # top_hits 上限 3 条（避免长原文 / 控制 token）
            assert len(item.get("top_hits", [])) <= 3
        # 603296 必须有 fresh hit
        huaqin = next((i for i in bucket["items"] if i["symbol"] == "603296.SH"), None)
        assert huaqin is not None
        assert huaqin["has_fresh_hit"] is True
        assert huaqin["hit_count"] >= 1
        # 600000（浦发）应该是 stale
        pufa = next((i for i in bucket["items"] if i["symbol"] == "600000.SH"), None)
        if pufa is not None and pufa["hit_count"] > 0:
            assert pufa["has_fresh_hit"] is False

    def test_collect_hits_no_full_body_text(self, fixture_kb, monkeypatch):
        """Q1 防御性：IC 桶里没有任何整页正文 / 私人 inbox 内容。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
        )

        bucket = collect_local_knowledge_hits(
            symbols=["603296.SH"],
            knowledge_root=str(fixture_kb),
        )
        blob = json.dumps(bucket, ensure_ascii=False)
        # 私人 inbox 页中的强动作词绝不能透出
        assert "立即买入华勤技术" not in blob
        assert "私人备忘" not in blob
        assert "私人笔记" not in blob

    def test_controller_hints_research_review_lane_from_local_hits(
        self, fixture_kb, monkeypatch
    ):
        """Q3: local_knowledge_hits 驱动 controller_hints.research_review lane。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.investment_controller_context import _build_controller_hints
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
        )

        bucket = collect_local_knowledge_hits(
            symbols=["603296.SH", "600000.SH"],
            knowledge_root=str(fixture_kb),
            as_of="2026-07-05 04:00:00",
        )
        hints = _build_controller_hints(
            "2026-07-05 04:00:00",
            observation={"items": []},
            pending_ta={"items": []},
            mandate_report={"data_status": "missing"},
            report_blockers={"items": []},
            local_knowledge_hits=bucket,
            notes=[],
        )
        review = hints["research_review"]
        # 603296 fresh → background_supplement
        huaqin_hint = next(
            (r for r in review if r["symbol"] == "603296.SH"), None
        )
        assert huaqin_hint is not None
        assert huaqin_hint["origin"] == "local_knowledge_hits"
        assert huaqin_hint["suggested_next_step"] == "background_supplement"
        # 600000 stale → needs_research_review
        pufa_hint = next(
            (r for r in review if r["symbol"] == "600000.SH"), None
        )
        if pufa_hint is not None:
            assert pufa_hint["suggested_next_step"] == "needs_research_review"
        # lane 文本不得有强动作词
        _assert_no_strong_verbs(review, where="controller_hints.research_review")

    def test_controller_hints_do_not_change_action_gate(self, fixture_kb, monkeypatch):
        """Q4: controller_hints 只是路由建议，不输出任何强动作 / 不改 action_label。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.investment_controller_context import _build_controller_hints
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
        )

        bucket = collect_local_knowledge_hits(
            symbols=["603296.SH"],
            knowledge_root=str(fixture_kb),
        )
        hints = _build_controller_hints(
            "2026-07-05 04:00:00",
            observation={"items": []},
            pending_ta={"items": []},
            mandate_report={"data_status": "missing"},
            report_blockers={"items": []},
            local_knowledge_hits=bucket,
            notes=[],
        )
        # hints 不允许出现任何强动作门禁字段
        for field in ("decision", "execution_action", "action_label", "target_price"):
            assert field not in hints
        # suggested_next_step 仅允许中性词
        allowed_steps = {
            "consider_light_or_full_ta",
            "background_supplement",
            "needs_research_review",
            "daily_digest_only",
            "suppress_push_data_insufficient",
        }
        for lane in ("needs_ta", "daily_report_only", "suppress_push_data_insufficient", "research_review"):
            for entry in hints.get(lane, []) or []:
                assert entry["suggested_next_step"] in allowed_steps


# ─────────────────────────────────────────────────────────────────────────────
# Stage F (端到端整链路): 一只标的从 wiki → raw_evidence → report → TF → IC
# ─────────────────────────────────────────────────────────────────────────────


class TestStageF_EndToEndChain:
    """完整链路：一条 fixture symbol 从 wiki 一路透到 IC context，
    沿途每一段字段对齐同一份命中数据。"""

    def test_full_chain_huaqin_closes_consistently(self, fixture_kb, monkeypatch):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
        )
        from api.services.report_service import attach_report_local_knowledge
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        SYMBOL = "603296.SH"

        # ── Stage A: KB-003 query ──
        result_a = query_local_knowledge(str(fixture_kb), symbol=SYMBOL)
        assert result_a.status == STATUS_HAS_DATA

        # ── Stage B: raw_evidence entry ──
        entry_b = build_raw_evidence_entry(result_a, "2026-07-05", "2026-07-05T04:00:00")
        assert entry_b["record_count"] == len(result_a.matched_pages)

        # ── Stage C: report attach ──
        report_c = {
            "symbol": SYMBOL,
            "decision": "WAIT",
            "action_label": "数据不足观察",
            "metadata": {"raw_evidence": {"local_knowledge": entry_b}},
        }
        report_c = attach_report_local_knowledge(report_c, symbol=SYMBOL)
        assert report_c["local_knowledge_summary"]["status"] == STATUS_HAS_DATA
        # 一致性：report 中的 matched_count 必须与 raw_evidence record_count 对齐
        assert report_c["local_knowledge_summary"]["matched_count"] == entry_b["record_count"]

        # ── Stage D: TradeFlow enrichment ──
        # 注意：TradeFlow 用 (symbol, name, themes) 多维度查询，命中数可能 ≥
        # Stage B 的纯 symbol 查询；只要求"有命中且 score>0"一致即可。
        candidate_d = _enrich_candidate_with_local_knowledge(_make_candidate(SYMBOL))
        assert candidate_d["knowledge_hit_count"] >= 1
        assert candidate_d["local_knowledge_score"] > 0.0

        # ── Stage E: IC context bucket ──
        bucket_e = collect_local_knowledge_hits(
            symbols=[SYMBOL], knowledge_root=str(fixture_kb)
        )
        ic_item = next(i for i in bucket_e["items"] if i["symbol"] == SYMBOL)
        assert ic_item["hit_count"] == entry_b["record_count"]
        assert ic_item["has_fresh_hit"] is True

        # ── Q4 整链路一致性：决策门禁字段在 report 上没被改 ──
        assert report_c["decision"] == "WAIT"
        assert report_c["action_label"] == "数据不足观察"

        # ── 整链路无强动作词 ──
        _assert_no_strong_verbs(
            report_c.get("local_knowledge_block"),
            report_c.get("local_knowledge_summary"),
            candidate_d["local_knowledge_summary"],
            candidate_d["local_knowledge_detail"],
            bucket_e,
            where="full chain output",
        )

    def test_full_chain_stale_hit_never_inflates_priority(
        self, fixture_kb, monkeypatch
    ):
        """Q2/Q3 对抗性：stale 命中沿整链路都不抬升研究优先级。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
        )
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        SYMBOL = "600000.SH"  # 仅在 valid_until=2020-01-01 页面命中

        # TF 候选侧：score 必须为 0
        candidate = _enrich_candidate_with_local_knowledge(
            _make_candidate(SYMBOL, mandate_topic="银行")
        )
        assert candidate["local_knowledge_score"] == 0.0
        assert candidate["knowledge_hit_count"] >= 1  # 有命中但全 stale

        # IC context 侧：fresh_symbol_count 不应计 stale-only 标的
        bucket = collect_local_knowledge_hits(
            symbols=[SYMBOL], knowledge_root=str(fixture_kb)
        )
        ic_item = next(i for i in bucket["items"] if i["symbol"] == SYMBOL)
        assert ic_item["has_fresh_hit"] is False
        if ic_item["hit_count"] > 0:
            # stale-only → 计入 stale_symbol_count，不计入 fresh_symbol_count
            assert bucket["fresh_symbol_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Stage G (约束 / 安全): 只读 / partition isolation / 禁词巡检
# ─────────────────────────────────────────────────────────────────────────────


class TestStageG_ConstraintsAndSafety:
    """V-013 line 4723 验收重点：字段完整 / 来源可追溯 / 权限只读 / 无强动作越权。"""

    def test_search_api_is_read_only(self, fixture_kb, monkeypatch):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.local_knowledge_context_service import search_local_knowledge

        payload = search_local_knowledge(
            symbol="603296.SH", knowledge_root=str(fixture_kb)
        )
        assert payload["read_only"] is True

    def test_knowledge_root_not_mutated(self, fixture_kb, monkeypatch):
        """权限只读：调用所有 KB API 后，知识库文件内容必须逐字不变。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        inv = fixture_kb / INVESTMENT_SUBDIR
        before = {}
        for f in inv.rglob("*.md"):
            before[str(f)] = f.read_text(encoding="utf-8")

        # 触发所有可能写入的代码路径
        query_local_knowledge(str(fixture_kb), symbol="603296.SH")
        compute_local_knowledge_score(
            query_local_knowledge(str(fixture_kb), symbol="603296.SH")
        )
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
        )
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        collect_local_knowledge_hits(
            symbols=["603296.SH"], knowledge_root=str(fixture_kb)
        )
        _enrich_candidate_with_local_knowledge(_make_candidate("603296.SH"))

        for f in inv.rglob("*.md"):
            assert f.read_text(encoding="utf-8") == before[str(f)], (
                f"[V-013] KB API 写入了知识库文件: {f}"
            )

    def test_inbox_partition_isolated_from_wiki_investment(
        self, fixture_kb, monkeypatch
    ):
        """partition isolation：inbox/私人笔记绝不出现在任何 KB 输出里。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
            search_local_knowledge,
        )

        for payload in (
            search_local_knowledge(
                symbol="603296.SH", knowledge_root=str(fixture_kb)
            ),
            collect_local_knowledge_hits(
                symbols=["603296.SH"], knowledge_root=str(fixture_kb)
            ),
        ):
            blob = json.dumps(payload, ensure_ascii=False)
            assert "私人笔记" not in blob
            assert "立即买入华勤技术" not in blob
            assert "私人备忘" not in blob
            # 任何 hit 的 rel_path 必须在 wiki/investment/ 下
            for hit in payload.get("hits", []) if isinstance(payload, dict) else []:
                rp = hit.get("rel_path", "")
                assert "inbox" not in rp.replace("\\", "/").lower(), (
                    f"[V-013] inbox 文件透出：{rp}"
                )

    def test_disabled_env_returns_skipped_not_raises(self, monkeypatch, fixture_kb):
        """KNOWLEDGE_CONTEXT_DISABLED=1 时降级为 skipped，绝不抛异常。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        monkeypatch.setenv("KNOWLEDGE_CONTEXT_DISABLED", "1")
        from api.services.local_knowledge_context_service import (
            collect_local_knowledge_hits,
            search_local_knowledge,
        )

        payload = search_local_knowledge(symbol="603296.SH")
        assert payload["data_status"] == "skipped"
        assert payload["hits"] == []

        bucket = collect_local_knowledge_hits(symbols=["603296.SH"])
        assert bucket["data_status"] == "skipped"
        assert bucket["items"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Stage H: 四问回答（验收报告所需字段全部可回答）
# ─────────────────────────────────────────────────────────────────────────────


class TestStageH_FourQuestionsAnswerable:
    """docs/TASKS.md line 4731 验收方式：报告能回答
    Q1 命中哪些知识 / Q2 是否过期 / Q3 如何影响研究优先级 / Q4 是否改变交易动作。"""

    def test_q1_which_knowledge_hit(self, fixture_kb, monkeypatch):
        """Q1: 命中哪些知识 — 每层输出来源可追溯（rel_path / title / updated_at）。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        out = _enrich_candidate_with_local_knowledge(_make_candidate("603296.SH"))
        detail = out["local_knowledge_detail"]
        # 必须有命中页摘要，每条带 rel_path / title
        assert detail["matched_pages_brief"]
        for brief in detail["matched_pages_brief"]:
            assert all(k in brief for k in ("rel_path", "title", "updated_at"))

    def test_q2_whether_stale(self, fixture_kb, monkeypatch):
        """Q2: 是否过期 — stale / low_confidence 字段在每个 layer 都可读。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        # KB-004 detail
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        stale_candidate = _enrich_candidate_with_local_knowledge(
            _make_candidate("600000.SH", mandate_topic="银行")
        )
        detail = stale_candidate["local_knowledge_detail"]
        assert detail["stale_hit_count"] >= 1
        assert any(b["is_stale"] for b in detail["matched_pages_brief"])

    def test_q3_how_research_priority_affected(self, fixture_kb, monkeypatch):
        """Q3: 如何影响研究优先级 — local_knowledge_score / needs_tree_work_research /
        research_review.suggested_next_step 都可读。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.tradeflow_service import (
            _enrich_candidate_with_local_knowledge,
        )

        # 命中场景：分数 > 0，flag = False
        hit = _enrich_candidate_with_local_knowledge(_make_candidate("603296.SH"))
        assert hit["local_knowledge_score"] > 0.0
        assert hit["needs_tree_work_research"] is False

        # 知识缺口场景：分数 = 0，flag = True
        gap = _enrich_candidate_with_local_knowledge(
            _make_candidate(
                "999999.SH",
                name="某概念股",
                candidate_type="POLICY_AMBUSH",
                mandate_topic="低空经济",
                mandate_score=4.5,
            )
        )
        assert gap["local_knowledge_score"] == 0.0
        assert gap["needs_tree_work_research"] is True

    def test_q4_trade_action_not_changed(self, fixture_kb, monkeypatch):
        """Q4: 是否改变交易动作 — 强动作门禁字段在 KB attach 前后逐字相等。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.report_service import attach_report_local_knowledge

        report = _make_baseline_report("603296.SH")
        before = {k: report.get(k) for k in STRONG_GATE_FIELDS}
        out = attach_report_local_knowledge(report, symbol="603296.SH")
        for field in STRONG_GATE_FIELDS:
            assert out.get(field) == before[field]


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke — scripts/query_local_knowledge.py 可被外部调用
# ─────────────────────────────────────────────────────────────────────────────


class TestCLISmoke:
    """V-013 line 4730 端到端测试通过：scripts/query_local_knowledge.py 子进程冒烟。"""

    def test_cli_query_symbol(self, fixture_kb, tmp_path: Path):
        import subprocess
        import sys

        out_path = tmp_path / "v013-cli-out.json"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/query_local_knowledge.py",
                "--symbol",
                "603296.SH",
                "--knowledge-root",
                str(fixture_kb),
                "--output",
                str(out_path),
                "--json",
                "--no-cache",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"[V-013] CLI 失败 rc={proc.returncode}\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        )
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data.get("status") == STATUS_HAS_DATA
        # CLI 输出是 result.to_dict()，命中页在 matched_pages 里
        assert len(data.get("matched_pages") or []) >= 1
