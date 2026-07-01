# [KB-005] tree_work_backlog
"""Tests for Tree Work inbox/raw/wiki 对齐与未消化研报清单 (KB-005).

覆盖：
  - inbox 文件分类（笔记/截图/prompt/未知）。
  - raw 引用提取（wikilink / sources 字段 / 正文兜底）。
  - raw 未消化识别（未被任何 wiki sources 引用）。
  - wiki 待补充 / 废弃 / 字段缺口判定。
  - index 未同步（missing / orphan）。
  - 整库 backlog 构建 + 分类统计 + 排序。
  - 报告渲染（不含原文、含分类与建议动作）。
  - CLI 子进程冒烟。
  - 只读安全性（不写入知识库）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict

import pytest

from tradingagents.dataflows.tree_work_backlog import (
    ACTION_ADD_INDEX,
    ACTION_ARCHIVE,
    ACTION_FILL_FIELDS,
    ACTION_FILL_RISKS,
    ACTION_FILL_SOURCES,
    ACTION_FILL_SUMMARY,
    ACTION_INGEST,
    ACTION_REMOVE_ORPHAN,
    ACTION_REVIEW,
    CATEGORY_DEPRECATED,
    CATEGORY_FIELD_GAP,
    CATEGORY_INDEX,
    CATEGORY_INBOX,
    CATEGORY_RAW,
    CATEGORY_SUPPLEMENT,
    BacklogItem,
    TreeWorkBacklog,
    build_tree_work_backlog,
    classify_inbox_item,
    classify_raw_item,
    default_knowledge_root,
    extract_raw_references,
    list_raw_files,
    render_backlog_report,
    suggest_backlog_output_path,
)
from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INBOX_SUBDIR,
    INVESTMENT_SUBDIR,
    LOG_MD,
    RAW_SUBDIR,
)


# ── 工具：构造 fixture 知识库 ─────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 高完整度公司页：引用了一个 raw 研报 → 该 raw 不应进入未消化清单。
_COMPANY_PAGE_REFERENCED_RAW = """---
title: 华勤技术603296-超节点进入出货周期
created: 2026-05-25
updated: 2026-06-29
sources:
  - "[[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|中邮证券-华勤技术超节点]]"
tags: [华勤技术, 超节点]
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

## 风险提示

- 需求不及预期
"""

# 行业页：缺风险章节、缺 symbols → 触发字段缺口。
_INDUSTRY_PAGE_MISSING_RISK = """---
title: PCB产业链综述
created: 2026-05-10
updated: 2026-05-10
sources:
  - "某券商研报"
tags: [PCB]
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
"""

# 待补充占位页。
_TODO_PAGE = """---
title: Dell-FQ127-待补充
created: 2026-05-31
updated: 2026-06-29
sources: []
tags: [Dell, 待补充]
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

# 废弃页：frontmatter deprecated=true，正文含"已废弃"。
_DEPRECATED_PAGE = """---
title: 华源证券-存储测试设备布局完善
created: 2026-05-21
updated: 2026-06-29
sources: []
tags: [废弃]
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

# 裸页：无 frontmatter，缺总结/风险/sources/symbols。
_BARE_PAGE = """# 某投资想法

直接写正文，没有 frontmatter，没有总结，没有风险。
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵 mini Tree Work 知识库（只在 tmp_path 下，绝不触碰真实知识库）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE_REFERENCED_RAW)
    _write(inv / "PCB产业链综述.md", _INDUSTRY_PAGE_MISSING_RISK)
    _write(inv / "Dell-FQ127-待补充.md", _TODO_PAGE)
    _write(inv / "华源证券-存储测试设备-废弃.md", _DEPRECATED_PAGE)
    _write(inv / "某投资想法.md", _BARE_PAGE)

    # index.md：引用其中 4 个，故意漏掉 "某投资想法"，并引用一个不存在的孤儿。
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

    # inbox：笔记 + 截图 + prompt + 未知类型。
    _write(tmp_path / INBOX_SUBDIR / "2026-05-27-存储芯片行业趋势.md", "# inbox\n")
    _write(tmp_path / INBOX_SUBDIR / "2026-05-27-AI机架.jpg", "JPG")
    _write(tmp_path / INBOX_SUBDIR / "family-knowledge-prompt.md", "# prompt\n")
    _write(tmp_path / INBOX_SUBDIR / "screenshots" / "x.png", "PNG")

    # raw：1 个被公司页引用的研报 + 2 个未被引用的（md + pdf）。
    _write(tmp_path / RAW_SUBDIR / "2026-05-14-中邮证券-华勤技术超节点.md", "# raw referenced\n")
    _write(tmp_path / RAW_SUBDIR / "2026-05-20-东吴证券-大族激光.md", "# raw undigested\n")
    _write(tmp_path / RAW_SUBDIR / "20260512-东莞证券-PCB产业链.pdf", "%PDF-1.4 fake")
    return tmp_path


# ── 1. inbox 分类 ────────────────────────────────────────────────────


class TestClassifyInbox:
    def test_note_md_is_ingest(self):
        action, detail, priority = classify_inbox_item("inbox/2026-05-27-存储芯片行业趋势.md")
        assert action == ACTION_INGEST
        assert priority == "medium"

    def test_image_is_ingest(self):
        action, detail, priority = classify_inbox_item("inbox/2026-05-27-AI机架.jpg")
        assert action == ACTION_INGEST
        assert priority == "medium"

    def test_prompt_is_review(self):
        action, detail, priority = classify_inbox_item("inbox/family-knowledge-prompt.md")
        assert action == ACTION_REVIEW
        assert priority == "low"

    def test_pdf_is_ingest(self):
        action, detail, priority = classify_inbox_item("inbox/report.pdf")
        assert action == ACTION_INGEST

    def test_unknown_is_review(self):
        action, detail, priority = classify_inbox_item("inbox/data.dat")
        assert action == ACTION_REVIEW


# ── 2. raw 引用提取 ──────────────────────────────────────────────────


class TestExtractRawReferences:
    def test_wikilink_in_sources_list(self):
        frontmatter = {
            "sources": ["[[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|alias]]"]
        }
        refs = extract_raw_references(frontmatter, "")
        assert "2026-05-14-中邮证券-华勤技术超节点.md" in refs

    def test_multiple_sources(self):
        frontmatter = {
            "sources": [
                "[[../../raw/a.md|a]]",
                "[[../../raw/b.md]]",
                "raw/c.md",
                "某券商研报",
            ]
        }
        refs = extract_raw_references(frontmatter, "")
        assert "a.md" in refs
        assert "b.md" in refs
        assert "c.md" in refs

    def test_body_wikilink_fallback(self):
        refs = extract_raw_references({}, "正文见 [[../../raw/body-ref.md|body]]。")
        assert "body-ref.md" in refs

    def test_no_references(self):
        assert extract_raw_references({"sources": ["某券商研报"]}, "正文无 raw 链接") == []

    def test_dedup(self):
        frontmatter = {"sources": ["[[../../raw/dup.md]]"]}
        refs = extract_raw_references(frontmatter, "又见 [[../../raw/dup.md]]")
        assert refs.count("dup.md") == 1


# ── 3. raw 分类与列举 ────────────────────────────────────────────────


class TestClassifyRawAndList:
    def test_raw_md_is_ingest_high(self):
        action, detail, priority = classify_raw_item("raw/2026-05-20-东吴证券.md")
        assert action == ACTION_INGEST
        assert priority == "high"

    def test_raw_pdf_is_ingest_medium(self):
        action, detail, priority = classify_raw_item("raw/20260512-东莞证券.pdf")
        assert action == ACTION_INGEST
        assert priority == "medium"

    def test_raw_image_is_ingest_low(self):
        action, detail, priority = classify_raw_item("raw/pic.png")
        assert action == ACTION_INGEST
        assert priority == "low"

    def test_list_raw_files_recursive(self, tmp_path: Path):
        raw = tmp_path / RAW_SUBDIR
        _write(raw / "a.md", "x")
        _write(raw / "sub" / "b.pdf", "y")
        files = list_raw_files(raw)
        names = [f.name for f in files]
        assert "a.md" in names
        assert "b.pdf" in names

    def test_list_raw_missing_dir(self, tmp_path: Path):
        assert list_raw_files(tmp_path / "no_raw") == []


# ── 4. 整库 backlog 构建 ─────────────────────────────────────────────


class TestBuildBacklog:
    def test_backlog_fixture_kb(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        # 基础统计。
        assert backlog.investment_page_count == 5
        assert backlog.inbox_total == 4
        assert backlog.raw_total == 3
        assert backlog.raw_referenced == 1  # 仅中邮证券被公司页引用

        # inbox 全部进入未消化清单。
        assert len(backlog.inbox_unprocessed) == 4
        inbox_locs = {it.location for it in backlog.inbox_unprocessed}
        assert "inbox/2026-05-27-存储芯片行业趋势.md" in inbox_locs
        assert "inbox/screenshots/x.png" in inbox_locs

        # raw 未消化：2 个（大族激光 md + 东莞证券 pdf）。
        raw_locs = {it.location for it in backlog.raw_undigested}
        assert "raw/2026-05-20-东吴证券-大族激光.md" in raw_locs
        assert "raw/20260512-东莞证券-PCB产业链.pdf" in raw_locs
        assert "raw/2026-05-14-中邮证券-华勤技术超节点.md" not in raw_locs

    def test_wiki_to_be_supplemented(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        supp_locs = {it.location for it in backlog.wiki_to_be_supplemented}
        assert "wiki/investment/Dell-FQ127-待补充.md" in supp_locs
        # 占位页会同时产出 fill_fields + fill_source_links 两条。
        supp_actions = {
            it.suggested_action
            for it in backlog.wiki_to_be_supplemented
            if it.location == "wiki/investment/Dell-FQ127-待补充.md"
        }
        assert ACTION_FILL_FIELDS in supp_actions
        assert ACTION_FILL_SOURCES in supp_actions

    def test_deprecated_page_archive(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        dep_locs = {it.location for it in backlog.wiki_deprecated}
        assert "wiki/investment/华源证券-存储测试设备-废弃.md" in dep_locs
        dep = [it for it in backlog.wiki_deprecated if "华源证券-存储测试设备-废弃" in it.location]
        assert dep[0].suggested_action == ACTION_ARCHIVE
        # 废弃页不应同时进入待补充清单。
        supp_locs = {it.location for it in backlog.wiki_to_be_supplemented}
        assert "wiki/investment/华源证券-存储测试设备-废弃.md" not in supp_locs

    def test_wiki_field_gaps(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        # 裸页缺 sources/symbols/总结/风险，应触发多个 field_gap。
        bare_items = [
            it for it in backlog.wiki_field_gaps if it.location == "wiki/investment/某投资想法.md"
        ]
        bare_actions = {it.suggested_action for it in bare_items}
        assert ACTION_FILL_SUMMARY in bare_actions
        assert ACTION_FILL_RISKS in bare_actions
        assert ACTION_FILL_SOURCES in bare_actions
        assert ACTION_FILL_FIELDS in bare_actions
        # 行业页缺风险 + symbols。
        industry_items = [
            it for it in backlog.wiki_field_gaps if it.location == "wiki/investment/PCB产业链综述.md"
        ]
        industry_actions = {it.suggested_action for it in industry_items}
        assert ACTION_FILL_RISKS in industry_actions
        assert ACTION_FILL_FIELDS in industry_actions
        # 高完整度公司页不应有字段缺口。
        company_items = [
            it for it in backlog.wiki_field_gaps
            if it.location == "wiki/investment/华勤技术603296-超节点.md"
        ]
        assert company_items == []

    def test_index_not_synced(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        add_index = [
            it for it in backlog.index_items if it.suggested_action == ACTION_ADD_INDEX
        ]
        remove_orphan = [
            it for it in backlog.index_items if it.suggested_action == ACTION_REMOVE_ORPHAN
        ]
        # "某投资想法" 未被 index 引用 → add_index_link。
        assert any("某投资想法" in it.location for it in add_index)
        # "不存在的孤儿页" 在 index 但无文件 → remove_orphan_link。
        assert any("不存在的孤儿页" in it.location for it in remove_orphan)

    def test_category_counts_and_total(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        counts = backlog.category_counts()
        assert counts[CATEGORY_INBOX] == 4
        assert counts[CATEGORY_RAW] == 2
        assert counts[CATEGORY_SUPPLEMENT] >= 1
        assert counts[CATEGORY_DEPRECATED] == 1
        assert counts[CATEGORY_INDEX] == 2
        # all_items 总数等于各分类之和。
        assert len(backlog.all_items()) == sum(counts.values())

    def test_all_items_sorted_by_priority(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        items = backlog.all_items()
        priorities = [it.priority for it in items]
        # high 应排在 medium / low 之前。
        order = {"high": 0, "medium": 1, "low": 2}
        idx = [order[p] for p in priorities]
        assert idx == sorted(idx)

    def test_to_dict_serializable(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        d = backlog.to_dict()
        text = json.dumps(d, ensure_ascii=False)
        assert "raw_undigested" in text
        assert "stats" in text
        assert d["stats"]["total_backlog"] == len(backlog.all_items())

    def test_missing_root_records_error(self, tmp_path: Path):
        backlog = build_tree_work_backlog(str(tmp_path / "does_not_exist"))
        assert any("不存在" in e for e in backlog.errors)
        assert len(backlog.all_items()) == 0

    def test_empty_root_still_runs(self, tmp_path: Path):
        backlog = build_tree_work_backlog(str(tmp_path))
        assert backlog.investment_page_count == 0
        assert backlog.all_items() == []


# ── 5. 报告渲染 ─────────────────────────────────────────────────────


class TestRenderReport:
    def test_report_basic_sections(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        md = render_backlog_report(backlog)
        assert "Tree Work inbox/raw/wiki 待消化清单" in md
        assert "概览" in md
        assert "分类统计" in md
        assert "inbox 未消化" in md
        assert "raw 未消化" in md
        assert "wiki 待补充" in md
        assert "wiki 废弃页" in md
        assert "wiki 字段/章节缺口" in md
        assert "index.md 未同步" in md
        assert "建议执行顺序" in md

    def test_report_contains_actions(self, fixture_kb: Path):
        backlog = build_tree_work_backlog(str(fixture_kb))
        md = render_backlog_report(backlog)
        assert f"`{ACTION_INGEST}`" in md
        assert f"`{ACTION_FILL_FIELDS}`" in md
        assert f"`{ACTION_ARCHIVE}`" in md

    def test_report_no_original_paragraphs(self, fixture_kb: Path):
        """报告里绝不能出现正文段落。"""
        backlog = build_tree_work_backlog(str(fixture_kb))
        md = render_backlog_report(backlog)
        assert "需求不及预期" not in md
        assert "PCB 需求旺盛" not in md
        assert "直接写正文" not in md

    def test_report_empty_backlog(self, tmp_path: Path):
        backlog = build_tree_work_backlog(str(tmp_path))
        md = render_backlog_report(backlog)
        assert "backlog 总项: **0**" in md
        # 空分类应有占位符。
        assert "（无）" in md


# ── 6. 默认根目录与输出路径 ──────────────────────────────────────────


class TestDefaults:
    def test_env_priority(self, monkeypatch):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", "/tmp/env_kb")
        assert default_knowledge_root() == "/tmp/env_kb"

    def test_fallback_to_home(self, monkeypatch):
        monkeypatch.delenv("AUTO_DEV_KNOWLEDGE_ROOT", raising=False)
        monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
        root = default_knowledge_root()
        assert root.endswith("Documents/knowledge")

    def test_output_path_format(self):
        path = suggest_backlog_output_path()
        assert "tree_work_ingest_backlog-" in path
        assert path.endswith(".md")


# ── 7. BacklogItem 数据类 ───────────────────────────────────────────


class TestBacklogItem:
    def test_to_dict(self):
        item = BacklogItem(
            category=CATEGORY_RAW,
            location="raw/x.md",
            suggested_action=ACTION_INGEST,
            detail="d",
            priority="high",
            extra={"k": "v"},
        )
        d = item.to_dict()
        assert d["category"] == CATEGORY_RAW
        assert d["location"] == "raw/x.md"
        assert d["priority"] == "high"
        assert d["extra"] == {"k": "v"}


# ── 8. CLI 子进程冒烟 ───────────────────────────────────────────────


class TestCliSmoke:
    def test_cli_help(self):
        proc = subprocess.run(
            [sys.executable, "scripts/tree_work_backlog.py", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        assert "KB-005" in proc.stdout

    def test_cli_markdown_stdout(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/tree_work_backlog.py",
                "--knowledge-root",
                str(fixture_kb),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        assert "Tree Work inbox/raw/wiki 待消化清单" in proc.stdout

    def test_cli_json_output(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/tree_work_backlog.py",
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
        assert payload["stats"]["raw_total"] == 3
        assert payload["stats"]["raw_undigested"] == 2

    def test_cli_write_file(self, fixture_kb: Path, tmp_path: Path):
        out_file = tmp_path / "out" / "backlog.md"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/tree_work_backlog.py",
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
        assert "raw 未消化" in content
        assert "[KB-005]" in proc.stderr


# ── 9. 只读安全性 ───────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_backlog_does_not_modify_kb(self, fixture_kb: Path):
        """构建清单前后，知识库内所有文件内容/大小不应变化。"""

        def snapshot(root: Path) -> Dict[str, int]:
            snap = {}
            for p in root.rglob("*"):
                if p.is_file():
                    snap[str(p.relative_to(root))] = p.stat().st_size
            return snap

        before = snapshot(fixture_kb)
        build_tree_work_backlog(str(fixture_kb))
        build_tree_work_backlog(str(fixture_kb))  # 跑两次确保幂等
        after = snapshot(fixture_kb)
        assert before == after

    def test_no_new_files_in_kb(self, fixture_kb: Path):
        files_before = {p for p in fixture_kb.rglob("*") if p.is_file()}
        build_tree_work_backlog(str(fixture_kb))
        files_after = {p for p in fixture_kb.rglob("*") if p.is_file()}
        assert files_after == files_before
