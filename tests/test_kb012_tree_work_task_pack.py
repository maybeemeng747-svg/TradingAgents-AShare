# [KB-012] tree_work_task_pack
"""Tests for Tree Work 研报补录任务包导出 (KB-012).

覆盖：
  - 数据类 TaskPackItem / TreeWorkTaskPack 序列化与排序。
  - KB-002 finding → 分组映射（rule_id / field → group）。
  - KB-005 backlog → 分组映射（category / action → group）。
  - KB-007 / KB-009 attention → hot_but_thin / needs_review_stale 阈值。
  - 去重合并（同一 group+location 多来源 → 单条任务）。
  - 整库 build_tree_work_task_pack：fixture / 空库 / 缺根目录。
  - 上游失败降级（上游模块抛异常时不阻塞）。
  - 报告渲染（含分组 / ingest 模板 / 不含原文 / 不含强动作词）。
  - 只读安全性（不写知识库）。
  - CLI 子进程冒烟。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict

import pytest

from tradingagents.dataflows.tree_work_task_pack import (
    CONTRACT_VERSION,
    GROUP_HOT_BUT_THIN,
    GROUP_INGEST_NEW,
    GROUP_MISSING_RISKS,
    GROUP_MISSING_SOURCES,
    GROUP_MISSING_SYMBOL,
    GROUP_MISSING_THESIS,
    GROUP_NEEDS_REVIEW_STALE,
    GROUP_ORDER,
    GROUP_TITLES,
    INGEST_TEMPLATE_MD,
    TASK_CODE,
    TaskPackItem,
    TreeWorkTaskPack,
    _collect_from_attention,
    _collect_from_backlog,
    _collect_from_lint,
    _item_dedup_key,
    _lint_finding_action,
    _lint_finding_group,
    _merge_items,
    _priority_max,
    _resolve_field_gap_group,
    build_tree_work_task_pack,
    render_task_pack_report,
    suggest_task_pack_output_path,
)
from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INBOX_SUBDIR,
    INVESTMENT_SUBDIR,
    LOG_MD,
    RAW_SUBDIR,
)
from tradingagents.dataflows.local_knowledge_lint import (
    KnowledgeLintResult,
    LintFinding,
    PageLintResult,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from tradingagents.dataflows.research_attention import (
    PageMention,
    ResearchAttentionResult,
    SymbolAttention,
)
from tradingagents.dataflows.tree_work_backlog import (
    BacklogItem,
    TreeWorkBacklog,
)


# ── 工具：构造 fixture 知识库 ─────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 高完整度公司页：含 symbols / thesis / risks / sources → 不应进入字段缺口任务。
_COMPLETE_COMPANY_PAGE = """---
title: 华勤技术603296-超节点进入出货周期
created: 2026-05-25
updated: 2026-06-29
sources:
  - "[[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|中邮证券-华勤技术超节点]]"
tags: [华勤技术, 超节点]
related: []
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 华勤技术（603296）

## 一句话总结

华勤技术超节点进入出货周期。

## 投资逻辑

- 超节点放量

## 风险提示

- 需求不及预期

## 原始资料

- [[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|中邮证券-华勤技术超节点]]
"""

# 缺 symbols 字段 + 缺风险章节 → 触发 missing_symbol + missing_risks。
_PAGE_MISSING_SYMBOL_AND_RISKS = """---
title: 大族激光002008-调研
created: 2026-05-10
updated: 2026-05-10
sources:
  - "东吴证券研报"
tags: [大族激光]
related: []
symbols: []
themes: [激光]
report_type: 公司点评
evidence_level: B
valid_until: 长期
source_quality: 中
stale_risk: 中
---

# 大族激光

## 一句话总结

公司订单回暖。

## 投资逻辑

- 苹果订单恢复
"""

# 缺 thesis 章节 → missing_thesis。
_PAGE_MISSING_THESIS = """---
title: PCB产业链综述
created: 2026-05-10
updated: 2026-05-10
sources:
  - "某券商研报"
tags: [PCB]
related: []
symbols: ["300124.SZ 汇川技术"]
themes: [PCB]
report_type: 综述
evidence_level: B
valid_until: 长期
source_quality: 中
stale_risk: 中
---

# PCB 产业链综述

## 风险提示

- 原材料波动
"""

# 待补充占位页（低置信 + stale_risk=高）→ needs_review_stale。
_TODO_PAGE = """---
title: Dell-FQ127-待补充
created: 2026-05-31
updated: 2026-06-29
sources: []
tags: [Dell, 待补充]
related: []
symbols: []
themes: [AI服务器]
report_type: 财报分析
evidence_level: C
valid_until: 长期
source_quality: 低
stale_risk: 高
---

# Dell FQ1'27 — 待补充

⚠️ 此页面内容不完整，不能作为强证据
"""

# 废弃页 → needs_review_stale（archive 动作）。
_DEPRECATED_PAGE = """---
title: 华源证券-存储测试设备布局完善
created: 2026-05-21
updated: 2026-06-29
sources: []
tags: [废弃]
related: []
symbols: []
themes: []
report_type: 公司点评
evidence_level: C
valid_until: 长期
source_quality: 低
stale_risk: 高
deprecated: true
deprecated_reason: 已由精智达301526替代
---

# 华源证券-存储测试设备布局完善

⚠️ 已废弃，已由精智达301526替代。
"""

# 裸页：无 frontmatter → 多重缺口。
_BARE_PAGE = """# 某投资想法

直接写正文，没有 frontmatter，没有总结，没有风险。
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵 mini Tree Work 知识库（只在 tmp_path 下，绝不触碰真实知识库）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPLETE_COMPANY_PAGE)
    _write(inv / "大族激光002008-调研.md", _PAGE_MISSING_SYMBOL_AND_RISKS)
    _write(inv / "PCB产业链综述.md", _PAGE_MISSING_THESIS)
    _write(inv / "Dell-FQ127-待补充.md", _TODO_PAGE)
    _write(inv / "华源证券-存储测试设备-废弃.md", _DEPRECATED_PAGE)
    _write(inv / "某投资想法.md", _BARE_PAGE)

    # index.md：引用其中 4 个，故意漏掉 "某投资想法"，并引用一个孤儿。
    _write(
        tmp_path / INDEX_MD,
        "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n\n"
        "- [[investment/华勤技术603296-超节点]] — 公司页\n"
        "- [[investment/PCB产业链综述]] — 行业页\n"
        "- [[investment/Dell-FQ127-待补充]] — 待补充\n"
        "- [[investment/华源证券-存储测试设备-废弃]] — 废弃\n"
        "- [[investment/不存在的孤儿页]] — 孤儿\n",
    )
    _write(tmp_path / LOG_MD, "# Log\n\n- entry1\n")

    # inbox：笔记 + 截图。
    _write(tmp_path / INBOX_SUBDIR / "2026-05-27-存储芯片行业趋势.md", "# inbox\n")
    _write(tmp_path / INBOX_SUBDIR / "2026-05-27-AI机架.jpg", "JPG")

    # raw：1 个被公司页引用的研报 + 1 个未被引用的 md。
    _write(tmp_path / RAW_SUBDIR / "2026-05-14-中邮证券-华勤技术超节点.md", "# raw referenced\n")
    _write(tmp_path / RAW_SUBDIR / "2026-05-20-东吴证券-大族激光.md", "# raw undigested\n")
    return tmp_path


# ── 1. 数据类 ────────────────────────────────────────────────────────


class TestTaskPackItem:
    def test_to_dict(self):
        item = TaskPackItem(
            group=GROUP_MISSING_SYMBOL,
            location="wiki/investment/x.md",
            suggested_action="fill_fields",
            detail="缺 symbols",
            priority="high",
            sources=["KB-002:SYM-001"],
            extra={"rule_id": "SYM-001"},
        )
        d = item.to_dict()
        assert d["group"] == GROUP_MISSING_SYMBOL
        assert d["location"] == "wiki/investment/x.md"
        assert d["priority"] == "high"
        assert d["sources"] == ["KB-002:SYM-001"]
        assert d["extra"] == {"rule_id": "SYM-001"}

    def test_default_fields(self):
        item = TaskPackItem(
            group=GROUP_MISSING_THESIS,
            location="x",
            suggested_action="fill_summary",
        )
        assert item.priority == "medium"
        assert item.sources == []
        assert item.extra == {}


class TestTreeWorkTaskPack:
    def test_all_items_sorted_by_group_then_priority(self):
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        # 先放 ingest_new high，再放 missing_symbol low —— all_items 应按 group_order
        # 排在前。
        pack.items_by_group[GROUP_INGEST_NEW] = [
            TaskPackItem(GROUP_INGEST_NEW, "z", "ingest", priority="high"),
        ]
        pack.items_by_group[GROUP_MISSING_SYMBOL] = [
            TaskPackItem(GROUP_MISSING_SYMBOL, "a", "fill_fields", priority="low"),
        ]
        items = pack.all_items()
        # GROUP_ORDER 中 missing_symbol 在 ingest_new 之前。
        assert items[0].group == GROUP_MISSING_SYMBOL
        assert items[1].group == GROUP_INGEST_NEW

    def test_group_counts(self):
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        pack.items_by_group[GROUP_MISSING_SYMBOL] = [
            TaskPackItem(GROUP_MISSING_SYMBOL, "a", "fill_fields"),
            TaskPackItem(GROUP_MISSING_SYMBOL, "b", "fill_fields"),
        ]
        counts = pack.group_counts()
        assert counts[GROUP_MISSING_SYMBOL] == 2
        # 未设置的分组应为 0。
        assert counts[GROUP_HOT_BUT_THIN] == 0
        assert pack.total() == 2

    def test_to_dict_serializable(self):
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        pack.items_by_group[GROUP_MISSING_THESIS] = [
            TaskPackItem(
                GROUP_MISSING_THESIS,
                "wiki/x.md",
                "fill_summary",
                sources=["KB-002:SEC-001"],
            ),
        ]
        d = pack.to_dict()
        text = json.dumps(d, ensure_ascii=False)
        assert "items_by_group" in text
        assert "missing_thesis" in text
        assert d["total"] == 1

    def test_empty_pack(self):
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        assert pack.total() == 0
        assert pack.all_items() == []
        # 空包仍可序列化。
        d = pack.to_dict()
        assert d["total"] == 0
        # 所有分组都有 key（即使为空列表）。
        for group in GROUP_ORDER:
            assert group in d["items_by_group"]


# ── 2. 分组映射规则 ─────────────────────────────────────────────────


class TestGroupMapping:
    def test_lint_rule_to_group_symbols(self):
        assert _lint_finding_group("SYM-001", None) == GROUP_MISSING_SYMBOL
        assert _lint_finding_group("FMR-002", "symbols") == GROUP_MISSING_SYMBOL

    def test_lint_rule_to_group_thesis(self):
        assert _lint_finding_group("SEC-001", None) == GROUP_MISSING_THESIS
        assert _lint_finding_group("SEC-004", None) == GROUP_MISSING_THESIS

    def test_lint_rule_to_group_risks(self):
        assert _lint_finding_group("SEC-002", None) == GROUP_MISSING_RISKS

    def test_lint_rule_to_group_sources(self):
        assert _lint_finding_group("SEC-003", None) == GROUP_MISSING_SOURCES
        assert _lint_finding_group("FMR-001", "sources") == GROUP_MISSING_SOURCES

    def test_lint_rule_to_group_stale(self):
        assert _lint_finding_group("STALE-001", None) == GROUP_NEEDS_REVIEW_STALE
        assert _lint_finding_group("STALE-002", None) == GROUP_NEEDS_REVIEW_STALE
        assert _lint_finding_group("EVID-001", None) == GROUP_NEEDS_REVIEW_STALE

    def test_lint_action_per_group(self):
        assert _lint_finding_action("SYM-001", GROUP_MISSING_SYMBOL) == "fill_fields"
        assert _lint_finding_action("SEC-001", GROUP_MISSING_THESIS) == "fill_summary"
        assert _lint_finding_action("SEC-002", GROUP_MISSING_RISKS) == "fill_risks"
        assert (
            _lint_finding_action("FMR-001", GROUP_MISSING_SOURCES)
            == "fill_source_links"
        )
        assert _lint_finding_action("STALE-001", GROUP_NEEDS_REVIEW_STALE) == "review"

    def test_resolve_field_gap_group(self):
        assert _resolve_field_gap_group("fill_fields") == GROUP_MISSING_SYMBOL
        assert _resolve_field_gap_group("fill_summary") == GROUP_MISSING_THESIS
        assert _resolve_field_gap_group("fill_risks") == GROUP_MISSING_RISKS
        assert _resolve_field_gap_group("fill_source_links") == GROUP_MISSING_SOURCES
        assert _resolve_field_gap_group("ingest") == GROUP_INGEST_NEW
        assert _resolve_field_gap_group("archive") == GROUP_NEEDS_REVIEW_STALE

    def test_unknown_rule_falls_back_to_review(self):
        # 未知 rule_id 默认归 needs_review_stale。
        assert (
            _lint_finding_group("UNKNOWN-999", None) == GROUP_NEEDS_REVIEW_STALE
        )


# ── 3. priority 合并与去重 ──────────────────────────────────────────


class TestMergeAndDedup:
    def test_priority_max(self):
        assert _priority_max("low", "medium", "high") == "high"
        assert _priority_max("medium", "low") == "medium"
        assert _priority_max("low") == "low"
        assert _priority_max() == "medium"

    def test_item_dedup_key(self):
        item = TaskPackItem(GROUP_MISSING_THESIS, "x.md", "fill_summary")
        assert _item_dedup_key(item) == (GROUP_MISSING_THESIS, "x.md")

    def test_merge_single_item(self):
        item = TaskPackItem(GROUP_MISSING_THESIS, "x.md", "fill_summary", priority="high")
        merged = _merge_items([item])
        assert merged is item

    def test_merge_multiple_items_same_location(self):
        items = [
            TaskPackItem(
                GROUP_MISSING_THESIS,
                "x.md",
                "fill_summary",
                detail="缺总结",
                priority="medium",
                sources=["KB-002:SEC-001"],
                extra={"rule_id": "SEC-001"},
            ),
            TaskPackItem(
                GROUP_MISSING_THESIS,
                "x.md",
                "fill_summary",
                detail="缺投资逻辑",
                priority="high",
                sources=["KB-005:fill_summary"],
                extra={"rule_id": "SEC-004"},
            ),
        ]
        merged = _merge_items(items)
        assert merged.location == "x.md"
        # 优先级取最严。
        assert merged.priority == "high"
        # sources 合并去重。
        assert "KB-002:SEC-001" in merged.sources
        assert "KB-005:fill_summary" in merged.sources
        # detail 合并。
        assert "缺总结" in merged.detail
        assert "缺投资逻辑" in merged.detail

    def test_merge_different_locations_returns_first(self):
        # 防御性：调用方应保证同 location，不同 location 时返回首条。
        items = [
            TaskPackItem(GROUP_MISSING_THESIS, "a.md", "fill_summary"),
            TaskPackItem(GROUP_MISSING_THESIS, "b.md", "fill_summary"),
        ]
        merged = _merge_items(items)
        assert merged.location == "a.md"


# ── 4. _collect_from_lint ───────────────────────────────────────────


class TestCollectFromLint:
    def _make_page(self, rel, findings):
        page = PageLintResult(
            rel_path=rel,
            title="t",
            page_type="company",
        )
        page.findings = findings
        return page

    def test_error_finding_assigned_to_group(self):
        result = KnowledgeLintResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
        )
        result.page_results.append(
            self._make_page(
                "wiki/investment/x.md",
                [
                    LintFinding(
                        rule_id="SEC-001",
                        severity=SEVERITY_ERROR,
                        field=None,
                        message="缺一句话总结",
                        fix_suggestion="补 ## 一句话总结",
                    ),
                ],
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_lint(result, pack)
        items = pack.items_by_group.get(GROUP_MISSING_THESIS, [])
        assert len(items) == 1
        assert items[0].priority == "high"
        assert items[0].sources == ["KB-002:SEC-001"]

    def test_warning_finding_medium_priority(self):
        result = KnowledgeLintResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
        )
        result.page_results.append(
            self._make_page(
                "wiki/investment/y.md",
                [
                    LintFinding(
                        rule_id="FMR-002",
                        severity=SEVERITY_WARNING,
                        field="themes",
                        message="缺 themes 字段",
                        fix_suggestion="补 themes",
                    ),
                ],
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_lint(result, pack)
        items = pack.items_by_group.get(GROUP_MISSING_SYMBOL, [])
        assert len(items) == 1
        assert items[0].priority == "medium"

    def test_info_finding_todo_goes_to_needs_review(self):
        result = KnowledgeLintResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
        )
        result.page_results.append(
            self._make_page(
                "wiki/investment/todo.md",
                [
                    LintFinding(
                        rule_id="TODO-001",
                        severity=SEVERITY_INFO,
                        field=None,
                        message="页面标记待补充",
                        fix_suggestion="保持标记",
                    ),
                ],
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_lint(result, pack)
        # TODO-001 不应进入 missing_* 分组，应进入 needs_review_stale。
        assert pack.items_by_group.get(GROUP_MISSING_THESIS, []) == []
        items = pack.items_by_group.get(GROUP_NEEDS_REVIEW_STALE, [])
        assert len(items) == 1
        assert items[0].priority == "low"

    def test_field_symbols_overrides_group(self):
        """FMR-001 field=symbols 应归 missing_symbol 而非 missing_sources。"""
        result = KnowledgeLintResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
        )
        result.page_results.append(
            self._make_page(
                "wiki/investment/s.md",
                [
                    LintFinding(
                        rule_id="FMR-001",
                        severity=SEVERITY_ERROR,
                        field="symbols",
                        message="缺 symbols",
                        fix_suggestion="补 symbols",
                    ),
                ],
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_lint(result, pack)
        assert len(pack.items_by_group.get(GROUP_MISSING_SYMBOL, [])) == 1


# ── 5. _collect_from_backlog ────────────────────────────────────────


class TestCollectFromBacklog:
    def test_inbox_goes_to_ingest_new(self):
        backlog = TreeWorkBacklog(
            knowledge_root="/x",
            generated_at="2026-07-05",
        )
        backlog.inbox_unprocessed.append(
            BacklogItem(
                category="inbox_unprocessed",
                location="inbox/note.md",
                suggested_action="ingest",
                detail="inbox 笔记",
                priority="medium",
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_backlog(backlog, pack)
        items = pack.items_by_group.get(GROUP_INGEST_NEW, [])
        assert len(items) == 1
        assert items[0].sources == ["KB-005:inbox_unprocessed"]

    def test_field_gap_fill_summary_goes_to_thesis(self):
        backlog = TreeWorkBacklog(
            knowledge_root="/x",
            generated_at="2026-07-05",
        )
        backlog.wiki_field_gaps.append(
            BacklogItem(
                category="wiki_field_gap",
                location="wiki/investment/x.md",
                suggested_action="fill_summary",
                detail="缺总结",
                priority="medium",
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_backlog(backlog, pack)
        assert len(pack.items_by_group.get(GROUP_MISSING_THESIS, [])) == 1

    def test_deprecated_goes_to_needs_review(self):
        backlog = TreeWorkBacklog(
            knowledge_root="/x",
            generated_at="2026-07-05",
        )
        backlog.wiki_deprecated.append(
            BacklogItem(
                category="wiki_deprecated",
                location="wiki/investment/dep.md",
                suggested_action="archive",
                detail="废弃页",
                priority="medium",
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_backlog(backlog, pack)
        assert len(pack.items_by_group.get(GROUP_NEEDS_REVIEW_STALE, [])) == 1

    def test_index_not_synced_skipped(self):
        backlog = TreeWorkBacklog(
            knowledge_root="/x",
            generated_at="2026-07-05",
        )
        backlog.index_items.append(
            BacklogItem(
                category="index_not_synced",
                location="wiki/investment/x.md",
                suggested_action="add_index_link",
                detail="未被 index 引用",
                priority="low",
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_backlog(backlog, pack)
        # index_not_synced 不应进入任何分组。
        assert pack.total() == 0


# ── 6. _collect_from_attention ──────────────────────────────────────


def _make_page_mention(rel_path="wiki/x.md", is_stale=False):
    return PageMention(
        rel_path=rel_path,
        title="t",
        page_type="company",
        is_stale=is_stale,
        themes=[],
        source_aliases=[],
    )


def _make_symbol(
    symbol_key="603296.SH",
    name="华勤技术",
    score=2.0,
    mention_count=3,
    stale_count=0,
    theme_count=2,
    asset_class="A_SHARE",
    stale_pages=False,
):
    sym = SymbolAttention(
        symbol_key=symbol_key,
        bare_code=symbol_key.split(".")[0],
        name=name,
        asset_class=asset_class,
        research_attention_score=score,
        mention_count=mention_count,
        fresh_mention_count=max(0, mention_count - stale_count),
        theme_count=theme_count,
    )
    sym.stale_mention_count = stale_count
    for i in range(mention_count):
        sym.matched_pages.append(
            _make_page_mention(
                rel_path=f"wiki/investment/{symbol_key}_{i}.md",
                is_stale=(i < stale_count) or stale_pages,
            )
        )
    return sym


class TestCollectFromAttention:
    def test_stale_page_goes_to_needs_review(self):
        result = ResearchAttentionResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        result.symbols.append(
            _make_symbol(stale_count=1, stale_pages=False, score=0.5, mention_count=2)
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_attention(result, pack)
        items = pack.items_by_group.get(GROUP_NEEDS_REVIEW_STALE, [])
        # 至少 1 条（stale page）。
        assert len(items) >= 1

    def test_hot_but_thin_low_score_not_emitted(self):
        result = ResearchAttentionResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        result.symbols.append(
            _make_symbol(score=0.5, mention_count=2, theme_count=2, stale_count=0)
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_attention(result, pack)
        # score < 1.5 不进入 hot_but_thin。
        assert pack.items_by_group.get(GROUP_HOT_BUT_THIN, []) == []

    def test_hot_but_thin_high_score_with_crowding(self):
        result = ResearchAttentionResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        # 高分 + theme_count >= 6 → hot_but_thin。
        result.symbols.append(
            _make_symbol(score=3.0, mention_count=4, theme_count=7, stale_count=0)
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_attention(result, pack)
        items = pack.items_by_group.get(GROUP_HOT_BUT_THIN, [])
        assert len(items) == 1
        assert items[0].extra.get("score") == 3.0
        assert items[0].extra.get("theme_count") == 7

    def test_hot_but_thin_high_score_with_stale_ratio(self):
        result = ResearchAttentionResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        # score=2.0, mention=4, stale=2 → stale_ratio=0.5 > 0.34。
        result.symbols.append(
            _make_symbol(score=2.0, mention_count=4, stale_count=2, theme_count=2)
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_attention(result, pack)
        items = pack.items_by_group.get(GROUP_HOT_BUT_THIN, [])
        assert len(items) == 1

    def test_non_a_share_excluded(self):
        result = ResearchAttentionResult(
            knowledge_root="/x",
            scanned_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        result.symbols.append(
            _make_symbol(
                symbol_key="0700.HK",
                name="腾讯",
                asset_class="HK",
                score=5.0,
                mention_count=5,
                theme_count=8,
                stale_count=3,
            )
        )
        pack = TreeWorkTaskPack(
            knowledge_root="/x",
            generated_at="2026-07-05",
            as_of_date="2026-07-05",
        )
        _collect_from_attention(result, pack)
        # 非 A 股不进入任务包。
        assert pack.items_by_group.get(GROUP_HOT_BUT_THIN, []) == []
        assert pack.items_by_group.get(GROUP_NEEDS_REVIEW_STALE, []) == []


# ── 7. 整库 build ───────────────────────────────────────────────────


class TestBuildTaskPack:
    def test_fixture_kb_produces_tasks(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb))
        # fixture 有 6 个 wiki 页（含 1 个完整）+ 2 个 inbox + 2 个 raw。
        assert pack.total() > 0
        # 至少触发 missing_symbol / missing_thesis / missing_risks 三类。
        counts = pack.group_counts()
        assert counts[GROUP_MISSING_SYMBOL] > 0
        assert counts[GROUP_MISSING_THESIS] > 0
        assert counts[GROUP_MISSING_RISKS] > 0
        assert counts[GROUP_INGEST_NEW] > 0  # inbox + raw

    def test_upstream_summary_populated(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb))
        assert "kb002" in pack.upstream_summary
        assert "kb005" in pack.upstream_summary
        assert "kb007" in pack.upstream_summary
        assert pack.upstream_summary["kb002"]["page_count"] > 0
        assert pack.upstream_summary["kb005"]["raw_total"] >= 2

    def test_dedup_merges_same_location(self, fixture_kb: Path):
        """同一页面可能同时被 KB-002（SEC-002）和 KB-005（fill_risks）标记缺风险，
        应合并为 1 条任务。
        """
        pack = build_tree_work_task_pack(str(fixture_kb))
        # 找大族激光页（缺风险）。
        risks_items = [
            it for it in pack.items_by_group.get(GROUP_MISSING_RISKS, [])
            if "大族激光" in it.location
        ]
        assert len(risks_items) == 1  # 去重后只剩 1 条
        # sources 应包含 KB-002 + KB-005。
        assert any(s.startswith("KB-002") for s in risks_items[0].sources)
        assert any(s.startswith("KB-005") for s in risks_items[0].sources)

    def test_missing_root(self, tmp_path: Path):
        pack = build_tree_work_task_pack(str(tmp_path / "does_not_exist"))
        assert any("不存在" in e for e in pack.errors)
        assert pack.total() == 0

    def test_empty_root_still_runs(self, tmp_path: Path):
        pack = build_tree_work_task_pack(str(tmp_path))
        assert pack.total() == 0
        # 空包仍可序列化。
        d = pack.to_dict()
        assert d["total"] == 0

    def test_skip_lint(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb), collect_lint=False)
        # 跳过 lint 后不应有 KB-002 来源任务（但 KB-005 fill_fields 仍可能进 missing_symbol）。
        # 主要断言：upstream_summary 不含 kb002。
        assert "kb002" not in pack.upstream_summary

    def test_skip_backlog(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb), collect_backlog=False)
        assert "kb005" not in pack.upstream_summary
        # inbox / raw 不会进入 ingest_new。
        ingest_items = pack.items_by_group.get(GROUP_INGEST_NEW, [])
        # 只有 KB-002 的 sources 字段缺失类任务可能进入 missing_sources，但 ingest_new 应为空。
        assert ingest_items == []

    def test_skip_attention(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb), collect_attention=False)
        assert "kb007" not in pack.upstream_summary
        # hot_but_thin / attention 来源的 needs_review 应为空。
        assert pack.items_by_group.get(GROUP_HOT_BUT_THIN, []) == []


# ── 8. 报告渲染 ─────────────────────────────────────────────────────


class TestRenderReport:
    def test_report_basic_sections(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb))
        md = render_task_pack_report(pack)
        assert "Tree Work 研报补录任务包" in md
        assert "概览" in md
        assert "分组统计" in md
        assert "上游信号摘要" in md
        assert "补录任务" in md
        assert "ingest 模板" in md
        assert "建议执行顺序" in md
        assert "免责声明" in md

    def test_report_contains_group_titles(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb))
        md = render_task_pack_report(pack)
        for group in GROUP_ORDER:
            assert GROUP_TITLES[group] in md

    def test_report_contains_ingest_template(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb))
        md = render_task_pack_report(pack)
        # 模板里应有 frontmatter 必填字段。
        assert "symbols:" in md
        assert "## 一句话总结" in md
        assert "## 投资逻辑" in md
        assert "## 风险提示" in md

    def test_report_contains_sources_signal(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb))
        md = render_task_pack_report(pack)
        assert "KB-002" in md
        assert "KB-005" in md

    def test_report_no_strong_action_verbs(self, fixture_kb: Path):
        """任务包不应包含强买卖词。"""
        pack = build_tree_work_task_pack(str(fixture_kb))
        md = render_task_pack_report(pack)
        for verb in ("立即买入", "立即卖出", "满仓", "清仓", "全仓", "强烈推荐"):
            assert verb not in md, f"报告中不应包含强动作词: {verb}"

    def test_report_empty_pack(self, tmp_path: Path):
        pack = build_tree_work_task_pack(str(tmp_path))
        md = render_task_pack_report(pack)
        assert "任务总数: **0**" in md
        # 空分组应有占位符。
        assert "（无）" in md

    def test_report_no_original_paragraphs(self, fixture_kb: Path):
        """报告不应包含 fixture 的正文段落。"""
        pack = build_tree_work_task_pack(str(fixture_kb))
        md = render_task_pack_report(pack)
        assert "华勤技术超节点进入出货周期" not in md
        assert "需求不及预期" not in md
        assert "苹果订单恢复" not in md

    def test_report_each_item_has_source_path_and_action(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb))
        md = render_task_pack_report(pack)
        # 每条任务行（含表格分隔）都应包含路径（`...`）和动作（`...`）。
        # 这里只做结构性断言：检查 markdown 含至少一行表格任务。
        lines = md.splitlines()
        task_table_rows = [
            ln for ln in lines
            if ln.startswith("| ") and "KB-" in ln
        ]
        assert len(task_table_rows) > 0
        for row in task_table_rows:
            # 每行必须包含至少一个反引号路径和一个反引号动作。
            assert row.count("`") >= 4  # 至少 location + action 两对反引号

    def test_report_as_of_date_in_template(self, fixture_kb: Path):
        pack = build_tree_work_task_pack(str(fixture_kb))
        md = render_task_pack_report(pack)
        assert pack.as_of_date in md


# ── 9. 常量与导出 ───────────────────────────────────────────────────


class TestConstants:
    def test_group_order_complete(self):
        # GROUP_ORDER 应包含所有 7 个分组。
        assert len(GROUP_ORDER) == 7
        assert GROUP_MISSING_SYMBOL in GROUP_ORDER
        assert GROUP_INGEST_NEW in GROUP_ORDER

    def test_group_titles_cover_all(self):
        for group in GROUP_ORDER:
            assert group in GROUP_TITLES

    def test_contract_version(self):
        assert CONTRACT_VERSION == "kb-012-v1"

    def test_task_code(self):
        assert TASK_CODE == "KB-012"

    def test_ingest_template_has_today_placeholder(self):
        assert "{today}" in INGEST_TEMPLATE_MD


# ── 10. 输出路径 ────────────────────────────────────────────────────


class TestOutputPath:
    def test_output_path_format(self):
        path = suggest_task_pack_output_path()
        assert "tree_work_task_pack-" in path
        assert path.endswith(".md")


# ── 11. CLI 子进程冒烟 ──────────────────────────────────────────────


class TestCliSmoke:
    def test_cli_help(self):
        proc = subprocess.run(
            [sys.executable, "scripts/tree_work_task_pack.py", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        assert "KB-012" in proc.stdout

    def test_cli_markdown_stdout(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/tree_work_task_pack.py",
                "--knowledge-root",
                str(fixture_kb),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        assert "Tree Work 研报补录任务包" in proc.stdout

    def test_cli_json_output(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/tree_work_task_pack.py",
                "--knowledge-root",
                str(fixture_kb),
                "--json",
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["contract_version"] == CONTRACT_VERSION
        assert payload["total"] > 0
        # items_by_group 必须覆盖所有分组。
        for group in GROUP_ORDER:
            assert group in payload["items_by_group"]

    def test_cli_write_file(self, fixture_kb: Path, tmp_path: Path):
        out_file = tmp_path / "out" / "task_pack.md"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/tree_work_task_pack.py",
                "--knowledge-root",
                str(fixture_kb),
                "--output",
                str(out_file),
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "补录任务" in content
        assert "[KB-012]" in proc.stderr

    def test_cli_skip_attention_flag(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/tree_work_task_pack.py",
                "--knowledge-root",
                str(fixture_kb),
                "--no-attention",
                "--json",
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert "kb007" not in payload["upstream_summary"]

    def test_cli_missing_root_exit_code(self, tmp_path: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/tree_work_task_pack.py",
                "--knowledge-root",
                str(tmp_path / "does_not_exist"),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 1


# ── 12. 只读安全性 ──────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_build_does_not_modify_kb(self, fixture_kb: Path):
        """构建任务包前后，知识库内所有文件内容/大小不应变化。"""

        def snapshot(root: Path) -> Dict[str, int]:
            snap = {}
            for p in root.rglob("*"):
                if p.is_file():
                    snap[str(p.relative_to(root))] = p.stat().st_size
            return snap

        before = snapshot(fixture_kb)
        build_tree_work_task_pack(str(fixture_kb))
        build_tree_work_task_pack(str(fixture_kb))  # 跑两次确保幂等
        after = snapshot(fixture_kb)
        assert before == after

    def test_no_new_files_in_kb(self, fixture_kb: Path):
        files_before = {p for p in fixture_kb.rglob("*") if p.is_file()}
        build_tree_work_task_pack(str(fixture_kb))
        files_after = {p for p in fixture_kb.rglob("*") if p.is_file()}
        assert files_after == files_before


# ── 13. 上游失败降级 ────────────────────────────────────────────────


class TestUpstreamDegradation:
    def test_partial_failure_does_not_crash(self, monkeypatch, fixture_kb: Path):
        """模拟 KB-002 抛异常，任务包仍应产出（来自 KB-005 / KB-007 的部分结果）。"""
        import tradingagents.dataflows.tree_work_task_pack as mod

        def _raise_lint(root):
            raise RuntimeError("simulated KB-002 failure")

        monkeypatch.setattr(mod, "lint_local_knowledge", _raise_lint)
        pack = build_tree_work_task_pack(str(fixture_kb))
        assert any("KB-002 lint 失败" in e for e in pack.errors)
        # 仍应有 KB-005 / KB-007 来源的任务。
        assert pack.total() > 0

    def test_all_upstreams_fail_returns_empty_pack(self, monkeypatch, fixture_kb: Path):
        import tradingagents.dataflows.tree_work_task_pack as mod

        def _raise(root):
            raise RuntimeError("simulated")

        monkeypatch.setattr(mod, "lint_local_knowledge", _raise)
        monkeypatch.setattr(mod, "build_tree_work_backlog", _raise)
        monkeypatch.setattr(mod, "compute_research_attention", _raise)
        pack = build_tree_work_task_pack(str(fixture_kb))
        assert pack.total() == 0
        assert len(pack.errors) >= 3
        # 报告仍能渲染。
        md = render_task_pack_report(pack)
        assert "KB-012" in md
