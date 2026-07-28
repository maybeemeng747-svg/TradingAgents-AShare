# [KB-003] local_knowledge_raw_evidence
"""Tests for TA 本地知识源 raw_evidence 接入 (KB-003).

覆盖：
  - 单页匹配（symbol / name / theme / tag 四个维度 + 组合）
  - 状态机（HAS_DATA / NORMAL_NO_DATA / FAILED / STALE / LOW_CONFIDENCE）
  - 段落抽取（summary / risks / sources 不输出原文、长度上限）
  - 报告区块渲染（最多 3 条摘要、NORMAL_NO_DATA 返回空串）
  - DataCollector.build_raw_evidence 接入 local_knowledge
  - evidence_contract 注册（不影响覆盖率计算）
  - report_service.attach_report_local_knowledge（cache / re-query / 失败容错）
  - CLI 子进程冒烟
  - 只读安全性
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.local_knowledge_provider import (
    LocalKnowledgeMatch,
    LocalKnowledgeQueryResult,
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    VENDOR,
    _extract_list_items,
    _extract_risk_items,
    _extract_sources,
    _strip_wiki_link,
    _name_matches,
    _symbol_matches,
    _theme_matches,
    _tags_match,
    build_raw_evidence_entry,
    compute_local_knowledge_score,
    query_failed_entry,
    query_local_knowledge,
    render_local_knowledge_block,
)


# ── 工具：构造 fixture 知识库 ─────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


_COMPANY_PAGE = """---
title: 华勤技术603296-超节点进入出货周期
created: 2026-05-25
updated: 2026-06-29
sources:
  - "[[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|中邮证券-华勤技术超节点]]"
tags: [华勤技术, 超节点, AI服务器]
related: []
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器, 超节点, 液冷散热, AI算力]
industry_chain_roles: [AI服务器ODM]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
source_type: [broker_report]
---

# 华勤技术（603296）

## 一句话总结

华勤技术超节点进入出货周期，AI服务器增长强劲。

## 投资逻辑

- 超节点架构优势
- AI服务器放量

## 风险提示

- 需求不及预期
- 竞争加剧

## 原始资料 / 关联研报

- [[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|中邮证券-华勤技术超节点（2026-05-14）]]
- [[AI算力基础设施-公司评分表]] — 算力基础设施评分
"""


_INDUSTRY_PAGE = """---
title: PCB产业链综述
created: 2026-05-10
updated: 2026-05-10
sources:
  - "某券商研报"
tags: [PCB, 产业链]
symbols: []
themes: [PCB]
report_type: 综述
evidence_level: B
valid_until: 长期
source_quality: 中
stale_risk: 中
---

# PCB 产业链综述

## 核心观点

PCB 需求旺盛。

## 风险提示

- 原材料价格波动
"""


_SCORE_TABLE_PAGE = """---
title: AI算力基础设施-公司评分表
created: 2026-05-13
updated: 2026-06-29
sources:
  - 星球社群截图
tags: [公司评分]
symbols: ["603296.SH 华勤技术", "000977.SZ 浪潮信息"]
themes: [AI算力, 服务器, 液冷]
report_type: 数据表
evidence_level: B
valid_until: 2099-07-29
source_quality: 低
stale_risk: 高
---

# AI算力基础设施 - 公司评分表

## 一句话总结

3家AI算力基础设施公司评分，华勤技术利好度最高。

## 标的列表

| 公司 | 代码 | 核心业务 | 板块 | 利好度 | 共识度 | 预计启动 | 期待周期 |
|------|------|----------|------|--------|--------|----------|----------|
| 华勤技术 | 603296 | ODM | AI算力 | 9.5 | 90 | 几天 | 半年 |

## 风险提示

- 评分时效风险
- 需求波动
"""


_TODO_PAGE = """---
title: Dell-FQ127-待补充
created: 2026-05-31
updated: 2026-06-29
sources:
  - "[[../../raw/x.md|Bernstein-Dell]]"
tags: [Dell, 待补充]
symbols: ["DELL.US Dell"]
themes: [AI服务器]
report_type: 财报分析
evidence_level: C
valid_until: 长期
source_quality: 低
stale_risk: 高
---

# Dell FQ1'27 — 待补充

⚠️ 此页面内容不完整，不能作为强证据

## 已知信息

- 推测方向

## 风险提示

- 待补充
"""


# 仅 stale_risk=高，但 evidence_level=A、machine_readiness 应为 medium/high。
_STALE_ONLY_PAGE = """---
title: 某周期股-已过期
created: 2026-04-01
updated: 2026-04-01
sources:
  - "某研报"
tags: [周期]
symbols: ["600000.SH 浦发银行"]
themes: [银行]
report_type: 公司点评
evidence_level: A
valid_until: 2020-01-01
source_quality: 中
stale_risk: 高
---

# 某周期股

## 一句话总结

已过期的旧观点。

## 风险提示

- 已过期

## 原始资料

- 旧研报
"""


_BARE_PAGE = """# 某投资想法

直接写正文，没有 frontmatter，没有总结，没有风险。
"""


_PEER_COMPANY_PAGE = """---
title: 安集科技-先进制程材料验证
created: 2026-07-01
updated: 2026-07-20
sources: [某券商研报]
tags: [半导体材料]
symbols: ["688019.SH 安集科技", "002409.SZ 雅克科技"]
themes: [半导体材料]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 安集科技

## 一句话总结

安集科技先进制程材料验证加速。

## 风险提示

- 安集科技客户验证不及预期
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵 mini Tree Work 知识库（只在 tmp_path 下，绝不触碰真实知识库）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE)
    _write(inv / "PCB产业链综述.md", _INDUSTRY_PAGE)
    _write(inv / "AI算力基础设施-公司评分表.md", _SCORE_TABLE_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _TODO_PAGE)
    _write(inv / "某周期股-已过期.md", _STALE_ONLY_PAGE)
    _write(inv / "某投资想法.md", _BARE_PAGE)
    _write(inv / "安集科技-先进制程材料验证.md", _PEER_COMPANY_PAGE)
    return tmp_path


@pytest.fixture()
def empty_kb(tmp_path: Path) -> Path:
    """只有 wiki/investment/ 空目录的知识库。"""
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True)
    return tmp_path


# ── 1. 匹配辅助函数 ────────────────────────────────────────────────────


class TestSymbolMatching:
    def test_bare_code_matches_full_entry(self):
        assert _symbol_matches("603296", ["603296.SH 华勤技术"]) is True

    def test_full_code_matches_full_entry(self):
        assert _symbol_matches("603296.SH", ["603296.SH 华勤技术"]) is True

    def test_full_code_case_insensitive(self):
        assert _symbol_matches("603296.sh", ["603296.SH 华勤技术"]) is True

    def test_name_in_symbol_entry_matches(self):
        assert _symbol_matches("华勤技术", ["603296.SH 华勤技术"]) is True

    def test_non_matching_symbol(self):
        assert _symbol_matches("999999", ["603296.SH 华勤技术"]) is False

    def test_empty_inputs(self):
        assert _symbol_matches("", ["603296.SH 华勤技术"]) is False
        assert _symbol_matches("603296", []) is False
        assert _symbol_matches("603296", None) is False


class TestThemeMatching:
    def test_exact_match(self):
        assert _theme_matches(["AI服务器"], ["AI服务器"]) is True

    def test_query_substring_of_page(self):
        # query "AI" matches page theme "AI服务器"
        assert _theme_matches(["AI"], ["AI服务器"]) is True

    def test_page_substring_of_query(self):
        # query "AI服务器龙头" matches page theme "AI服务器"
        assert _theme_matches(["AI服务器龙头"], ["AI服务器"]) is True

    def test_no_match(self):
        assert _theme_matches(["PCB"], ["AI服务器"]) is False

    def test_any_in_list_matches(self):
        assert _theme_matches(["AI", "PCB"], ["液冷", "AI服务器"]) is True

    def test_case_insensitive(self):
        assert _theme_matches(["ai"], ["AI"]) is True

    def test_empty_inputs(self):
        assert _theme_matches([], ["AI"]) is False
        assert _theme_matches(["AI"], []) is False


class TestNameMatching:
    def test_name_in_title(self):
        assert _name_matches("华勤技术", "华勤技术603296-超节点", "x.md") is True

    def test_name_in_filename(self):
        assert _name_matches("华勤技术", "Other Title", "华勤技术603296-超节点.md") is True

    def test_case_insensitive(self):
        assert _name_matches("dell", "Dell FQ127", "x.md") is True

    def test_no_match(self):
        assert _name_matches("腾讯", "华勤技术", "x.md") is False

    def test_empty_name(self):
        assert _name_matches("", "Title", "x.md") is False


class TestTagsMatching:
    def test_intersection(self):
        assert _tags_match(["AI", "PCB"], ["液冷", "AI"]) is True

    def test_no_intersection(self):
        assert _tags_match(["AI"], ["PCB"]) is False

    def test_case_insensitive(self):
        assert _tags_match(["ai"], ["AI"]) is True

    def test_empty(self):
        assert _tags_match([], ["AI"]) is False


# ── 2. 状态机：query_local_knowledge ──────────────────────────────────


class TestQueryStatusMachine:
    def test_symbol_hit_returns_has_data(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        assert result.status == STATUS_HAS_DATA
        assert result.vendor == VENDOR
        assert len(result.matched_pages) >= 1
        # The company page should be ranked first.
        assert any("华勤技术" in m.title for m in result.matched_pages)

    def test_full_symbol_with_suffix(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296.SH")
        assert result.status == STATUS_HAS_DATA
        assert any("华勤技术" in m.title for m in result.matched_pages)

    def test_name_match(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), name="华勤技术")
        assert result.status == STATUS_HAS_DATA
        assert any("华勤" in m.title for m in result.matched_pages)

    def test_peer_company_mention_is_not_target_company_evidence(
        self, fixture_kb: Path
    ):
        result = query_local_knowledge(str(fixture_kb), symbol="002409.SZ")
        assert len(result.matched_pages) == 1
        match = result.matched_pages[0]
        assert match.title.startswith("安集科技")
        assert match.evidence_scope == "peer_background"
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.summary[0].startswith("[行业/同业背景，不是目标公司事实]")

        block = render_local_knowledge_block(result)
        assert "未命中目标公司直接研究页" in block
        assert "行业/同行背景（非目标公司事实）" in block
        assert "证据范围：`peer_background`" in block

        score = compute_local_knowledge_score(result)
        assert score["knowledge_hit_count"] == 1
        assert score["fresh_hit_count"] == 0
        assert score["local_knowledge_score"] == 0.0
        assert score["has_hit"] is False
        assert score["matched_pages_brief"][0]["evidence_scope"] == "peer_background"

    def test_theme_match_hits_score_table(self, fixture_kb: Path):
        result = query_local_knowledge(
            str(fixture_kb), themes=["AI算力基础设施"], max_pages=20
        )
        assert result.status == STATUS_HAS_DATA
        # Score table should be among matches (acceptance criterion).
        assert any(
            "AI算力基础设施" in m.rel_path and m.page_type == "score_table"
            for m in result.matched_pages
        )

    def test_tag_match(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), tags=["PCB"])
        assert result.status == STATUS_HAS_DATA
        assert any("PCB" in m.title for m in result.matched_pages)

    def test_combined_query(self, fixture_kb: Path):
        result = query_local_knowledge(
            str(fixture_kb), symbol="603296", themes=["AI服务器"]
        )
        assert result.status == STATUS_HAS_DATA
        # Each match should record at least one matched_by reason.
        assert all(m.matched_by for m in result.matched_pages)

    def test_no_match_returns_normal_no_data(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="999999")
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.matched_pages == []
        assert result.confidence == "low"

    def test_no_query_criteria_returns_normal_no_data(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb))
        assert result.status == STATUS_NORMAL_NO_DATA
        assert "未提供查询条件" in " ".join(result.errors)

    def test_missing_knowledge_root_returns_failed(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist"
        result = query_local_knowledge(str(missing), symbol="603296")
        assert result.status == STATUS_FAILED
        assert any("knowledge_root 不存在" in e for e in result.errors)

    def test_empty_investment_dir_returns_normal_no_data(self, empty_kb: Path):
        result = query_local_knowledge(str(empty_kb), symbol="603296")
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.matched_pages == []

    def test_only_stale_match_returns_stale(self, fixture_kb: Path):
        # "600000.SH 浦发银行" only appears in the stale-only page.
        result = query_local_knowledge(str(fixture_kb), symbol="600000")
        assert result.status == STATUS_STALE
        assert all(m.is_stale for m in result.matched_pages)
        assert result.confidence == "low"

    def test_only_low_confidence_match_returns_low_confidence(
        self, fixture_kb: Path
    ):
        # DELL.US appears only in the todo/low-confidence page.
        result = query_local_knowledge(str(fixture_kb), symbol="DELL.US")
        assert result.status == STATUS_LOW_CONFIDENCE
        assert all(
            m.is_low_confidence or m.is_to_be_supplemented
            for m in result.matched_pages
        )

    def test_mixed_fresh_and_stale_returns_has_data(self, fixture_kb: Path):
        # "603296" matches the company page (fresh) AND the score table (stale).
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        assert result.status == STATUS_HAS_DATA
        # Fresh match present.
        assert any(
            not (m.is_stale or m.is_low_confidence or m.is_to_be_supplemented)
            for m in result.matched_pages
        )


# ── 3. 字段聚合与 confidence ──────────────────────────────────────────


class TestResultAggregation:
    def test_symbols_aggregated_dedup(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        # 603296.SH appears in both company page and score table.
        symbols = result.symbols
        # No duplicates.
        assert len(symbols) == len(set(symbols))
        assert any("603296" in s for s in symbols)

    def test_themes_aggregated(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        assert any("AI服务器" in t for t in result.themes)

    def test_summary_capped_at_three(self, fixture_kb: Path):
        result = query_local_knowledge(
            str(fixture_kb), symbol="603296", max_pages=10
        )
        assert len(result.summary) <= 3

    def test_risks_capped_at_five(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        assert len(result.risks) <= 5

    def test_sources_capped(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        assert len(result.sources) <= 5

    def test_updated_at_is_max(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        # All matched pages have updated=2026-06-29.
        assert result.updated_at == "2026-06-29"

    def test_confidence_high_when_fresh_high_readiness(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        # Company page is readiness=high with evidence_level=A.
        assert result.confidence in {"high", "medium"}

    def test_to_dict_round_trip(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        d = result.to_dict()
        assert d["status"] == result.status
        assert d["vendor"] == VENDOR
        rebuilt = LocalKnowledgeQueryResult.from_dict(d)
        assert rebuilt.status == result.status
        assert len(rebuilt.matched_pages) == len(result.matched_pages)
        assert rebuilt.matched_pages[0].title == result.matched_pages[0].title

    def test_query_recorded(self, fixture_kb: Path):
        result = query_local_knowledge(
            str(fixture_kb), symbol="603296", themes=["AI"]
        )
        assert result.query.get("symbol") == "603296"
        assert "AI" in result.query.get("themes", [])


# ── 4. 段落抽取：不输出原文，长度上限 ─────────────────────────────────


class TestSectionExtraction:
    def test_strip_wiki_link_with_alias(self):
        assert (
            _strip_wiki_link("[[../../raw/x.md|某研报]]") == "某研报"
        )

    def test_strip_wiki_link_without_alias(self):
        assert _strip_wiki_link("[[investment/foo]]") == "foo"

    def test_strip_wiki_link_passthrough(self):
        assert _strip_wiki_link("普通文本") == "普通文本"

    def test_extract_risk_items(self):
        body = (
            "## 风险提示\n\n"
            "- 需求不及预期\n"
            "- 竞争加剧\n"
            "* 第三条\n"
            "1. 第四条\n"
        )
        risks = _extract_risk_items(body, max_items=5)
        assert "需求不及预期" in risks
        assert "竞争加剧" in risks
        assert "第三条" in risks
        assert "第四条" in risks

    def test_extract_risk_items_truncates_long(self):
        long_text = "超长风险" + "x" * 300
        body = f"## 风险提示\n\n- {long_text}\n"
        risks = _extract_risk_items(body, max_items=5)
        assert len(risks) == 1
        assert len(risks[0]) <= 121  # _RISK_MAX_CHARS + "…"

    def test_extract_sources_prefers_frontmatter(self):
        items = _extract_sources(["某研报 1", "某研报 2"], body="", max_items=5)
        assert "某研报 1" in items
        assert "某研报 2" in items

    def test_extract_sources_falls_back_to_body(self):
        body = (
            "## 原始资料\n\n"
            "- [[../../raw/x.md|研报A]]\n"
            "- [[investment/foo]] — 交叉验证\n"
        )
        items = _extract_sources([], body=body, max_items=5)
        assert "研报A" in items
        assert "foo — 交叉验证" in items

    def test_extract_list_items_respects_section_boundary(self):
        body = (
            "## 原始资料\n\n"
            "- 研报A\n"
            "\n"
            "## 相关页面\n\n"
            "- 不应被抽取\n"
        )
        items = _extract_list_items(body, ("原始资料",), 5)
        assert "研报A" in items
        assert "不应被抽取" not in items


# ── 5. 报告区块渲染 ──────────────────────────────────────────────────


class TestBlockRendering:
    def test_has_data_renders_block(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        block = render_local_knowledge_block(result)
        assert "### 本地知识补充" in block
        assert "tree_work_wiki" in block
        assert "华勤技术" in block
        # Original section bodies are NOT dumped — only summary line.
        assert "超节点架构优势" not in block  # body detail not in summary

    def test_normal_no_data_returns_empty(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="999999")
        assert render_local_knowledge_block(result) == ""

    def test_failed_returns_empty(self, tmp_path: Path):
        missing = tmp_path / "missing"
        result = query_local_knowledge(str(missing), symbol="603296")
        assert render_local_knowledge_block(result) == ""

    def test_stale_renders_with_warning(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="600000")
        block = render_local_knowledge_block(result)
        assert "本地知识补充" in block
        assert "STALE" in block or "过期" in block

    def test_low_confidence_renders_with_warning(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="DELL.US")
        block = render_local_knowledge_block(result)
        assert "本地知识补充" in block
        assert "LOW_CONFIDENCE" in block or "低置信" in block

    def test_block_has_at_most_three_summaries(self, fixture_kb: Path):
        result = query_local_knowledge(
            str(fixture_kb), themes=["AI"], max_pages=10
        )
        block = render_local_knowledge_block(result)
        # Each entry starts with "**N. " — count them.
        import re

        entries = re.findall(r"^\*\*\d+\. ", block, flags=re.MULTILINE)
        assert len(entries) <= 3

    def test_block_includes_source_path(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        block = render_local_knowledge_block(result)
        assert "wiki/investment/" in block

    def test_block_includes_risks_section_when_present(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        block = render_local_knowledge_block(result)
        assert "风险提示" in block

    def test_block_includes_disclaimer(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="603296")
        block = render_local_knowledge_block(result)
        # Must explicitly note: background only, not a substitute.
        assert "不替代" in block


# ── 6. raw_evidence 接入辅助 ──────────────────────────────────────────


class TestEvidenceEntryHelpers:
    def test_build_raw_evidence_entry_structure(self):
        result = LocalKnowledgeQueryResult(
            status=STATUS_HAS_DATA,
            matched_pages=[
                LocalKnowledgeMatch(
                    rel_path="wiki/investment/x.md",
                    title="X",
                    page_type="company",
                )
            ],
        )
        entry = build_raw_evidence_entry(result, "2026-07-01", "2026-07-01T00:00:00")
        required = [
            "raw", "field", "status", "vendor", "endpoint",
            "as_of", "fetched_at", "record_count", "unit", "error",
            "fallback_from", "source_url", "is_realtime_patched",
            "source_type",
        ]
        for k in required:
            assert k in entry, f"missing {k}"
        assert entry["field"] == "local_knowledge"
        assert entry["status"] == STATUS_HAS_DATA
        assert entry["vendor"] == VENDOR
        assert entry["source_type"] == "tree_work_wiki"
        assert entry["endpoint"] == "wiki/investment"
        assert entry["record_count"] == 1

    def test_query_failed_entry_structure(self):
        entry = query_failed_entry(
            "2026-07-01", "2026-07-01T00:00:00", "boom"
        )
        assert entry["status"] == STATUS_FAILED
        assert entry["vendor"] == VENDOR
        assert entry["error"] == "boom"
        assert entry["record_count"] == 0
        assert entry["raw"] is None

    def test_query_failed_entry_truncates_long_error(self):
        long_err = "x" * 500
        entry = query_failed_entry(
            "2026-07-01", "2026-07-01T00:00:00", long_err
        )
        assert len(entry["error"]) <= 200


# ── 7. DataCollector.build_raw_evidence 接入 ─────────────────────────


class TestDataCollectorIntegration:
    def test_local_knowledge_key_present(self, fixture_kb: Path, monkeypatch):
        # Redirect KB root to fixture so the test is deterministic.
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from tradingagents.graph.data_collector import DataCollector

        dc = DataCollector()
        dc._cache["603296.SH_2026-07-01"] = {"stock_data": "some data"}
        evidence = dc.build_raw_evidence("603296.SH", "2026-07-01")
        assert "local_knowledge" in evidence
        entry = evidence["local_knowledge"]
        assert entry["field"] == "local_knowledge"
        assert entry["vendor"] == VENDOR
        assert entry["status"] in {
            STATUS_HAS_DATA,
            STATUS_NORMAL_NO_DATA,
            STATUS_FAILED,
            STATUS_STALE,
            STATUS_LOW_CONFIDENCE,
        }

    def test_local_knowledge_independent_of_pool(
        self, fixture_kb: Path, monkeypatch
    ):
        """local_knowledge 应当即使 pool 不含 local_knowledge 键也能产出。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from tradingagents.graph.data_collector import DataCollector

        dc = DataCollector()
        dc._cache["603296.SH_2026-07-01"] = {"stock_data": "x"}
        evidence = dc.build_raw_evidence("603296.SH", "2026-07-01")
        # Should hit the fixture KB (status=HAS_DATA).
        assert evidence["local_knowledge"]["status"] == STATUS_HAS_DATA

    def test_local_knowledge_failed_when_kb_missing(
        self, tmp_path: Path, monkeypatch
    ):
        missing = tmp_path / "no_kb"
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(missing))
        from tradingagents.graph.data_collector import DataCollector

        dc = DataCollector()
        dc._cache["999999.SH_2026-07-01"] = {"stock_data": "x"}
        evidence = dc.build_raw_evidence("999999.SH", "2026-07-01")
        assert evidence["local_knowledge"]["status"] == STATUS_FAILED
        assert evidence["local_knowledge"]["error"] is not None

    def test_existing_keys_unaffected(self, fixture_kb: Path, monkeypatch):
        """新增 local_knowledge 不应破坏其它 key 的结构。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from tradingagents.graph.data_collector import DataCollector

        dc = DataCollector()
        dc._cache["603296.SH_2026-07-01"] = {
            "stock_data": "x" * 100,
            "ratings": "603296.SH RATINGS_HAS_DATA: 买入",
        }
        evidence = dc.build_raw_evidence("603296.SH", "2026-07-01")
        assert "stock_data" in evidence
        assert "ratings" in evidence
        assert evidence["ratings"]["status"] == "HAS_DATA"


# ── 8. evidence_contract 注册 ────────────────────────────────────────


class TestEvidenceContractRegistration:
    def test_data_type_registered(self):
        from tradingagents.dataflows.evidence_contract import (
            resolve_data_type,
        )
        assert resolve_data_type("local_knowledge") == "local_knowledge"

    def test_local_knowledge_does_not_affect_completeness_missing(self):
        """raw_evidence 中无 local_knowledge 时，completeness 不应受影响。"""
        from tradingagents.dataflows.evidence_contract import (
            compute_contract_completeness,
        )
        baseline = {
            "stock_data": {
                "raw": "x",
                "status": "HAS_DATA",
                "vendor": "akshare",
                "unit": "股",
            },
        }
        without = compute_contract_completeness(dict(baseline))
        # Add a sentinel key to ensure we're not silently including it.
        with_sentinel = compute_contract_completeness(
            dict(baseline, local_knowledge=None)
        )
        assert without["completeness_score"] == with_sentinel["completeness_score"]

    def test_local_knowledge_not_required(self):
        from tradingagents.dataflows.evidence_contract import (
            _REQUIRED_FIELDS_FOR_COMPLETENESS,
            _OPTIONAL_FIELDS_FOR_COMPLETENESS,
        )
        assert "local_knowledge" not in _REQUIRED_FIELDS_FOR_COMPLETENESS
        assert "local_knowledge" in _OPTIONAL_FIELDS_FOR_COMPLETENESS


# ── 9. report_service.attach_report_local_knowledge ─────────────────


class TestAttachReportLocalKnowledge:
    def test_uses_cached_entry_when_present(self, fixture_kb: Path):
        from api.services.report_service import attach_report_local_knowledge

        cached_result = LocalKnowledgeQueryResult(
            status=STATUS_HAS_DATA,
            matched_pages=[
                LocalKnowledgeMatch(
                    rel_path="wiki/investment/x.md",
                    title="X 公司",
                    page_type="company",
                    summary="X 是一家公司。",
                )
            ],
            summary=["X 是一家公司。"],
            symbols=["603296.SH X"],
            confidence="high",
            updated_at="2026-07-01",
        )
        result_data = {
            "metadata": {
                "raw_evidence": {
                    "local_knowledge": build_raw_evidence_entry(
                        cached_result, "2026-07-01", "2026-07-01T00:00:00"
                    )
                }
            }
        }
        enriched = attach_report_local_knowledge(result_data, symbol="603296")
        assert isinstance(enriched, dict)
        assert "local_knowledge_block" in enriched
        assert "### 本地知识补充" in enriched["local_knowledge_block"]
        assert enriched["local_knowledge_summary"]["status"] == STATUS_HAS_DATA
        assert enriched["local_knowledge_summary"]["matched_count"] == 1

    def test_requeries_for_legacy_rows(self, fixture_kb: Path, monkeypatch):
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        # Legacy row: no metadata.raw_evidence.local_knowledge.
        result_data = {"final_trade_decision": "HOLD"}
        enriched = attach_report_local_knowledge(result_data, symbol="603296")
        assert "local_knowledge_block" in enriched
        assert "华勤技术" in enriched["local_knowledge_block"]

    def test_no_symbol_no_cache_returns_unchanged(self):
        from api.services.report_service import attach_report_local_knowledge

        result_data = {"final_trade_decision": "HOLD"}
        enriched = attach_report_local_knowledge(result_data, symbol=None)
        # No symbol + no cache -> no enrichment.
        assert enriched == result_data

    def test_normal_no_data_does_not_render_block(self, fixture_kb: Path, monkeypatch):
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        result_data = {"final_trade_decision": "HOLD"}
        enriched = attach_report_local_knowledge(result_data, symbol="999999")
        # Summary still attached so the UI can show "no hit" state, but block empty.
        assert enriched.get("local_knowledge_block") in ("", None)
        assert (
            enriched["local_knowledge_summary"]["status"] == STATUS_NORMAL_NO_DATA
        )

    def test_strong_action_gate_untouched(self, fixture_kb: Path, monkeypatch):
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
        # Strong-action fields must NOT change.
        assert enriched["execution_action"] == "WAIT"
        assert enriched["action_label"] == "数据不足观察"
        assert enriched["stop_loss_price"] == 100.0


# ── 10. CLI 冒烟 ──────────────────────────────────────────────────────


class TestCLI:
    def test_cli_symbol_query(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/query_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
                "--symbol",
                "603296",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert "本地知识补充" in proc.stdout
        assert "华勤技术" in proc.stdout

    def test_cli_json_output(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/query_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
                "--symbol",
                "603296",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["status"] == STATUS_HAS_DATA
        assert payload["vendor"] == VENDOR

    def test_cli_no_criteria_fails(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/query_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
        assert "至少需要" in proc.stderr

    def test_cli_failed_status_returns_nonzero(self, tmp_path: Path):
        missing = tmp_path / "no_kb"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/query_local_knowledge.py",
                "--knowledge-root",
                str(missing),
                "--symbol",
                "603296",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 1


# ── 11. 只读安全性 ────────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_provider_does_not_write_to_kb(self, fixture_kb: Path):
        """查询前后知识库文件 mtime 不应改变。"""
        target = fixture_kb / INVESTMENT_SUBDIR / "华勤技术603296-超节点.md"
        before_mtime = target.stat().st_mtime
        # Run multiple queries.
        query_local_knowledge(str(fixture_kb), symbol="603296")
        query_local_knowledge(str(fixture_kb), themes=["AI"])
        query_local_knowledge(str(fixture_kb), name="华勤")
        after_mtime = target.stat().st_mtime
        assert before_mtime == after_mtime

    def test_provider_does_not_create_files(self, fixture_kb: Path):
        files_before = set(p for p in fixture_kb.rglob("*") if p.is_file())
        query_local_knowledge(str(fixture_kb), symbol="603296")
        files_after = set(p for p in fixture_kb.rglob("*") if p.is_file())
        assert files_before == files_after


# ── 12. round-trip: data_collector -> report_service ─────────────────


class TestEndToEndFlow:
    def test_data_collector_result_can_render_via_report_service(
        self, fixture_kb: Path, monkeypatch
    ):
        """完整链路：data_collector 产出 raw_evidence.local_knowledge ->
        report_service.attach_report_local_knowledge 渲染区块。"""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from tradingagents.graph.data_collector import DataCollector
        from api.services.report_service import attach_report_local_knowledge

        dc = DataCollector()
        dc._cache["603296.SH_2026-07-01"] = {"stock_data": "x" * 100}
        evidence = dc.build_raw_evidence("603296.SH", "2026-07-01")
        result_data = {"metadata": {"raw_evidence": evidence}}
        enriched = attach_report_local_knowledge(result_data, symbol="603296.SH")
        assert "### 本地知识补充" in enriched["local_knowledge_block"]
        assert enriched["local_knowledge_summary"]["matched_count"] >= 1
