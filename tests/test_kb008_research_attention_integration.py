# [KB-008] research_attention_integration
"""Tests for TA/TradeFlow 接入研报关注度与主题交叉度展示 (KB-008).

覆盖：
  - ``lookup_research_attention``：单 symbol 查询、symbol 等价性、无命中。
  - ``attention_to_summary``：命中/无命中结构、负面信息（stale/deprecated/主题拥挤）。
  - ``render_research_attention_inline``：渲染段非空、含必要字段、不含买卖建议词。
  - ``attach_report_local_knowledge``：顶层字段、block 拼接、不破坏强动作门禁。
  - TradeFlow ``_enrich_candidate_with_research_attention`` /
    ``_enrich_candidates_with_research_attention``：单条 + 批量、共享扫描、
    低置信不提升、无命中 NORMAL_NO_DATA 语义。
  - 只读安全性（不写知识库）。
  - Pydantic schema 包含新增字段。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import pytest

# 复用 KB-007 的 fixture 常量（同一套 mini 知识库），保证倒排索引口径一致。
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
)
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_HAS_DATA,
    STATUS_NORMAL_NO_DATA,
)
from tradingagents.dataflows.research_attention import (
    ASSET_CLASS_A_SHARE,
    SymbolAttention,
    attention_to_summary,
    lookup_research_attention,
    render_research_attention_inline,
)


# ── fixture（与 KB-007 同构，独立 tmp_path，绝不触碰真实知识库）─────────


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
    _write(inv / "沪深300ETF-指数跟踪.md", _FUND_PAGE)
    _write(inv / "某私募主体-调研纪要.md", _UNLISTED_PAGE)
    _write(inv / "某周期股-已过期.md", _EXPIRED_PAGE)
    _write(inv / "行业综述-无标的.md", _NO_SYMBOLS_PAGE)
    return tmp_path


@pytest.fixture()
def empty_kb(tmp_path: Path) -> Path:
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True)
    return tmp_path


# ── 1. lookup_research_attention ─────────────────────────────────────


class TestLookupResearchAttention:
    def test_hit_returns_symbol_attention(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296")
        assert isinstance(sym, SymbolAttention)
        assert sym.symbol_key == "603296.SH"
        assert sym.mention_count == 3
        assert sym.asset_class == ASSET_CLASS_A_SHARE

    def test_symbol_equivalence_with_sh_suffix(self, fixture_kb: Path):
        # 603296.SH 与 603296 视作同一标的。
        sym = lookup_research_attention(str(fixture_kb), "603296.SH")
        assert sym is not None
        assert sym.symbol_key == "603296.SH"

    def test_symbol_equivalence_case_insensitive(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296.sh")
        assert sym is not None
        assert sym.symbol_key == "603296.SH"

    def test_no_hit_returns_none(self, fixture_kb: Path):
        assert lookup_research_attention(str(fixture_kb), "999999") is None

    def test_empty_symbol_returns_none(self, fixture_kb: Path):
        assert lookup_research_attention(str(fixture_kb), "") is None
        assert lookup_research_attention(str(fixture_kb), None) is None

    def test_hk_symbol_hit(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "00700.HK")
        assert sym is not None
        assert sym.symbol_key == "00700.HK"

    def test_empty_kb_returns_none(self, empty_kb: Path):
        assert lookup_research_attention(str(empty_kb), "603296") is None

    def test_missing_kb_returns_none(self, tmp_path: Path):
        # 知识库根目录不存在 → compute_research_attention 返回空结果，lookup 返回 None。
        missing = tmp_path / "does-not-exist"
        assert lookup_research_attention(str(missing), "603296") is None


# ── 2. attention_to_summary ──────────────────────────────────────────


class TestAttentionToSummary:
    def test_hit_summary_structure(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296")
        s = attention_to_summary(sym)
        assert s["has_hit"] is True
        # 与任务要求对齐的核心字段。
        for key in (
            "research_attention_score",
            "knowledge_theme_count",
            "mention_count",
            "fresh_mention_count",
            "high_quality_mention_count",
            "stale_mention_count",
            "deprecated_mention_count",
            "source_count",
            "themes",
            "sources",
            "latest_updated",
            "matched_pages",
            "score_explain",
            "research_attention_summary",
        ):
            assert key in s, f"missing key: {key}"
        # 华勤 3 篇命中：2 fresh + 1 stale。
        assert s["mention_count"] == 3
        assert s["fresh_mention_count"] == 2
        assert s["stale_mention_count"] == 1
        assert s["deprecated_mention_count"] == 0
        # 主题交叉（COMPANY_A/B + SCORE_TABLE 去重后 >= 5）。
        assert s["knowledge_theme_count"] >= 5
        # 命中页前 5 条裁剪。
        assert len(s["matched_pages"]) <= 5
        assert all("rel_path" in p and "title" in p for p in s["matched_pages"])

    def test_no_hit_summary_structure(self):
        s = attention_to_summary(None)
        assert s["has_hit"] is False
        assert s["research_attention_score"] == 0.0
        assert s["knowledge_theme_count"] == 0
        assert s["mention_count"] == 0
        assert s["research_attention_summary"] == ""
        assert s["matched_pages"] == []

    def test_summary_includes_negative_info(self, fixture_kb: Path):
        """任务约束：必须同时展示负面信息（过期 / 低置信 / 主题拥挤）。"""
        sym = lookup_research_attention(str(fixture_kb), "603296")
        s = attention_to_summary(sym)
        text = s["research_attention_summary"]
        # 华勤 1 篇 stale 命中 → 文本必须出现"过期"字样。
        assert "过期" in text
        # 主题数 >= 6 → 必须出现"主题较拥挤"（KB-007 fixture 实际 >= 5，但若
        # 达到 6 必须触发；此处用 >= 5 的实际值时也容忍不出现"拥挤"）。
        if s["knowledge_theme_count"] >= 6:
            assert "拥挤" in text

    def test_summary_includes_deprecated_for_todo_page(self, fixture_kb: Path):
        """低置信/待补充页面的负面信息必须出现。"""
        sym = lookup_research_attention(str(fixture_kb), "DELL.US")
        s = attention_to_summary(sym)
        text = s["research_attention_summary"]
        # DELL 全部命中为 deprecated → 文本必须出现"低置信/待补充"。
        assert "低置信" in text or "待补充" in text

    def test_summary_no_strong_action_words(self, fixture_kb: Path):
        """研报关注度摘要不得包含买卖建议或强动作词。"""
        sym = lookup_research_attention(str(fixture_kb), "603296")
        s = attention_to_summary(sym)
        text = s["research_attention_summary"]
        for forbidden in ("买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL"):
            assert forbidden not in text, f"summary contains forbidden word: {forbidden}"

    def test_score_rounded_to_two_decimals(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296")
        s = attention_to_summary(sym)
        # 保留 2 位小数。
        assert round(s["research_attention_score"], 2) == s["research_attention_score"]


# ── 3. render_research_attention_inline ──────────────────────────────


class TestRenderResearchAttentionInline:
    def test_renders_non_empty_for_hit(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296")
        md = render_research_attention_inline(sym)
        assert md != ""
        assert "研报关注度" in md

    def test_renders_empty_for_none(self):
        assert render_research_attention_inline(None) == ""

    def test_inline_contains_required_fields(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296")
        md = render_research_attention_inline(sym)
        # 任务要求字段：综合关注度、命中篇数、主题交叉、来源质量、过期数。
        assert "综合关注度" in md
        assert "命中篇数" in md
        assert "过期" in md
        assert "低置信" in md or "待补充" in md
        assert "主题交叉" in md
        # 必须含 disclaimer：不构成买卖建议。
        assert "不构成买卖建议" in md

    def test_inline_lists_matched_pages(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296")
        md = render_research_attention_inline(sym)
        # 命中页路径必须出现。
        assert "华勤技术603296-超节点.md" in md or "wiki/investment" in md

    def test_inline_marks_stale_pages(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296")
        md = render_research_attention_inline(sym)
        # SCORE_TABLE 为 stale → 必须标 STALE。
        assert "STALE" in md

    def test_inline_no_strong_action_words(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "603296")
        md = render_research_attention_inline(sym)
        for forbidden in ("买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL"):
            assert forbidden not in md, f"inline contains forbidden word: {forbidden}"


# ── 4. attach_report_local_knowledge（KB-008 顶层字段）────────────────


class TestAttachReportLocalKnowledgeKB008:
    def test_surfaces_research_attention_fields_at_top_level(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        result_data = {"final_trade_decision": "HOLD"}
        enriched = attach_report_local_knowledge(result_data, symbol="603296")
        # KB-008 顶层字段。
        assert "research_attention_score" in enriched
        assert "knowledge_theme_count" in enriched
        assert "research_attention_summary" in enriched
        assert "research_attention_block" in enriched
        # 数值字段非零（华勤 fixture 有命中）。
        assert enriched["research_attention_score"] > 0.0
        assert enriched["knowledge_theme_count"] >= 5

    def test_summary_dict_contains_attention_fields(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        result_data = {"final_trade_decision": "HOLD"}
        enriched = attach_report_local_knowledge(result_data, symbol="603296")
        summary = enriched["local_knowledge_summary"]
        # local_knowledge_summary 也含 KB-008 字段（前端无需深挖即可渲染）。
        for key in (
            "has_hit",
            "research_attention_score",
            "knowledge_theme_count",
            "mention_count",
            "stale_mention_count",
            "deprecated_mention_count",
            "research_attention_summary",
        ):
            assert key in summary

    def test_local_knowledge_block_appends_attention_section(
        self, fixture_kb: Path, monkeypatch
    ):
        """TA 报告"本地知识补充" markdown 末尾应自动拼接研究关注度段。"""
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        result_data = {"final_trade_decision": "HOLD"}
        enriched = attach_report_local_knowledge(result_data, symbol="603296")
        block = enriched.get("local_knowledge_block") or ""
        assert "### 本地知识补充" in block
        assert "研报关注度" in block

    def test_strong_action_gate_untouched(self, fixture_kb: Path, monkeypatch):
        """研究关注度是解释信息，绝不改变强动作门禁。"""
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        result_data = {
            "final_trade_decision": "WAIT",
            "execution_action": "WAIT",
            "research_direction": "中性",
            "action_label": "数据不足观察",
            "target_price": None,
            "stop_loss_price": 100.0,
        }
        enriched = attach_report_local_knowledge(result_data, symbol="603296")
        assert enriched["execution_action"] == "WAIT"
        assert enriched["action_label"] == "数据不足观察"
        assert enriched["stop_loss_price"] == 100.0
        assert enriched["target_price"] is None

    def test_no_hit_returns_empty_attention_but_normal_no_data(
        self, fixture_kb: Path, monkeypatch
    ):
        """无命中（NORMAL_NO_DATA）不影响 TA 主流程；attention 字段为空结构。"""
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        result_data = {"final_trade_decision": "HOLD"}
        enriched = attach_report_local_knowledge(result_data, symbol="999999")
        # KB-003 主流程仍能完成（NORMAL_NO_DATA 状态）。
        assert (
            enriched["local_knowledge_summary"]["status"] == STATUS_NORMAL_NO_DATA
        )
        # KB-008 字段：score=0、summary=""。
        assert enriched["research_attention_score"] == 0.0
        assert enriched["research_attention_summary"] == ""
        assert enriched["research_attention_block"] == ""


# ── 5. TradeFlow _enrich_candidate_with_research_attention ───────────


class TestTradeFlowCandidateEnrichment:
    def test_single_candidate_enrichment(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {"symbol": "603296.SH", "name": "华勤技术"}
        result = _enrich_candidate_with_research_attention(item)
        assert result["research_attention_score"] > 0.0
        assert result["knowledge_theme_count"] >= 5
        assert "综合关注度" in result["research_attention_summary"]
        assert "research_attention_detail" in result
        assert result["research_attention_detail"]["has_hit"] is True

    def test_single_candidate_bare_code_symbol(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        # 6 位 bare code 也应当命中。
        item = {"symbol": "603296"}
        result = _enrich_candidate_with_research_attention(item)
        assert result["research_attention_score"] > 0.0

    def test_single_candidate_no_hit(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {"symbol": "999999.SH", "name": "无此股"}
        result = _enrich_candidate_with_research_attention(item)
        # NORMAL_NO_DATA 语义：score=0、summary=""、has_hit=False。
        assert result["research_attention_score"] == 0.0
        assert result["research_attention_summary"] == ""
        assert result["research_attention_detail"]["has_hit"] is False

    def test_single_candidate_empty_symbol(self, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        item = {"symbol": "", "name": "x"}
        result = _enrich_candidate_with_research_attention(item)
        assert result["research_attention_score"] == 0.0

    def test_low_confidence_only_does_not_boost(self, fixture_kb: Path, monkeypatch):
        """任务约束：仅低置信命中不会推高候选层级。

        DELL.US fixture 全部命中为 deprecated（evidence_level=C、待补充），
        research_attention_score 应当为 0（fresh=0 → freshness_ratio=0 → 缩放为 0）。
        """
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {"symbol": "DELL.US", "name": "Dell"}
        result = _enrich_candidate_with_research_attention(item)
        assert result["research_attention_score"] == 0.0
        # 但命中数仍透出，便于前端展示"低置信命中"负面信息。
        assert result["research_attention_detail"]["deprecated_mention_count"] >= 1


# ── 6. TradeFlow _enrich_candidates_with_research_attention（批量）─────


class TestTradeFlowBatchEnrichment:
    def test_batch_enrichment_shares_scan(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        items = [
            {"symbol": "603296.SH", "name": "华勤技术"},
            {"symbol": "000977.SZ", "name": "浪潮信息"},
            {"symbol": "00700.HK", "name": "腾讯控股"},
        ]
        result = _enrich_candidates_with_research_attention(items)
        assert len(result) == 3
        # 华勤与浪潮出现在 SCORE_TABLE，腾讯单独一页。
        huaqin = next(it for it in result if it["symbol"] == "603296.SH")
        assert huaqin["research_attention_score"] > 0.0
        # 共享一次扫描：所有 item 都有 KB-008 字段。
        for it in result:
            assert "research_attention_score" in it
            assert "knowledge_theme_count" in it
            assert "research_attention_summary" in it
            assert "research_attention_detail" in it

    def test_batch_enrichment_mixed_hit_and_no_hit(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        items = [
            {"symbol": "603296.SH"},
            {"symbol": "999999.SH"},  # 无命中
        ]
        result = _enrich_candidates_with_research_attention(items)
        hit, miss = result
        assert hit["research_attention_score"] > 0.0
        assert miss["research_attention_score"] == 0.0
        assert miss["research_attention_detail"]["has_hit"] is False

    def test_batch_enrichment_empty_list(self):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_research_attention,
        )

        assert _enrich_candidates_with_research_attention([]) == []

    def test_batch_enrichment_degraded_when_kb_missing(
        self, tmp_path: Path, monkeypatch
    ):
        """知识库不可读时不阻塞候选读取主链路，逐项降级为空结构。"""
        from api.services.tradeflow_service import (
            _enrich_candidates_with_research_attention,
        )

        missing = tmp_path / "does-not-exist"
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(missing))
        items = [{"symbol": "603296.SH"}, {"symbol": "000001.SZ"}]
        result = _enrich_candidates_with_research_attention(items)
        for it in result:
            assert it["research_attention_score"] == 0.0
            assert it["research_attention_detail"]["has_hit"] is False


# ── 7. 只读安全性 ────────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_lookup_does_not_write_to_knowledge_base(
        self, fixture_kb: Path
    ):
        """[KB-008] lookup 必须只读，绝不向知识库写文件。"""
        # 记录所有 wiki/investment 文件的 mtime + 内容哈希。
        files = list((fixture_kb / INVESTMENT_SUBDIR).rglob("*.md"))
        assert files, "fixture should have markdown pages"
        before = {p: (p.stat().st_mtime, p.read_bytes()) for p in files}

        # 多次调用 lookup / batch enrichment，确认无写入。
        lookup_research_attention(str(fixture_kb), "603296")
        lookup_research_attention(str(fixture_kb), "000977")
        lookup_research_attention(str(fixture_kb), "999999")

        for p in files:
            mt, content = before[p]
            assert p.stat().st_mtime == mt, f"mtime changed: {p}"
            assert p.read_bytes() == content, f"content changed: {p}"

    def test_no_new_files_created(self, fixture_kb: Path):
        before = {str(p) for p in fixture_kb.rglob("*")}
        lookup_research_attention(str(fixture_kb), "603296")
        after = {str(p) for p in fixture_kb.rglob("*")}
        assert before == after, "lookup created new files"


# ── 8. Pydantic schema ───────────────────────────────────────────────


class TestTradeFlowSchemaFields:
    def test_candidate_item_has_kb008_fields(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem

        fields = TradeFlowCandidateItem.model_fields
        assert "research_attention_score" in fields
        assert "knowledge_theme_count" in fields
        assert "research_attention_summary" in fields
        assert "research_attention_detail" in fields

    def test_candidate_item_default_values(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem

        item = TradeFlowCandidateItem(symbol="603296.SH")
        # 默认值：无命中状态。
        assert item.research_attention_score == 0.0
        assert item.knowledge_theme_count == 0
        assert item.research_attention_summary == ""
        assert item.research_attention_detail == {}

    def test_candidate_item_accepts_kb008_payload(self, fixture_kb: Path, monkeypatch):
        """端到端：enrichment 后产出的 dict 应能直接构造 Pydantic 模型。"""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )
        from api.tradeflow_schemas import TradeFlowCandidateItem

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = _enrich_candidate_with_research_attention({"symbol": "603296.SH"})
        # Pydantic 应当能验证 enrichment 后的 dict。
        schema_item = TradeFlowCandidateItem(**item)
        assert schema_item.research_attention_score > 0.0
        assert schema_item.knowledge_theme_count >= 5
