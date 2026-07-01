# [KB-001] local_knowledge_audit
"""Tests for Tree Work 本地知识库只读索引与健康审计 (KB-001).

覆盖：
  - frontmatter 解析（PyYAML / 极简解析器回退 / 缺失）
  - 页面类型识别（company/industry/score_table/summary/to_be_supplemented/unclassified）
  - 单页审计字段
  - 整库审计聚合（页面数、inbox、index 对齐、缺口统计）
  - 报告渲染（不含原文、至少 5 类缺口）
  - CLI 子进程冒烟
  - 只读安全性（不写入知识库）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from tradingagents.dataflows.local_knowledge_audit import (
    INVESTMENT_SUBDIR,
    LOG_MD,
    INDEX_MD,
    INBOX_SUBDIR,
    RAW_SUBDIR,
    WikiPageAudit,
    LocalKnowledgeAuditResult,
    _audit_single_page,
    _collect_index_misalignment,
    _compute_machine_readiness,
    _is_nonempty_field,
    _is_valid_until_expired,
    _parse_frontmatter,
    _split_frontmatter,
    audit_local_knowledge,
    classify_page_type,
    default_knowledge_root,
    render_audit_report,
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
tags: [华勤技术, 超节点]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器]
industry_chain_roles: [AI服务器ODM]
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

- AI服务器增长
- 多元化产品

## 风险提示

- 需求不及预期
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

## 投资逻辑

详细正文。
"""


_SCORE_TABLE_PAGE = """---
title: AI算力基础设施-公司评分表
created: 2026-05-13
updated: 2026-06-29
sources:
  - 星球社群截图
tags: [公司评分]
symbols: ["603296.SH 华勤技术", "000977.SZ 浪潮信息"]
themes: [AI算力]
report_type: 数据表
evidence_level: B
valid_until: 2099-07-29
source_quality: 低
stale_risk: 高
---

# AI算力基础设施 - 公司评分表

## 标的列表

| 公司 | 代码 | 核心业务 | 板块 | 利好度 | 共识度 | 预计启动 | 期待周期 |
|------|------|----------|------|--------|--------|----------|----------|
| 华勤技术 | 603296 | ODM | AI算力 | 9.5 | 90 | 几天 | 半年 |

## 风险提示

评分时效风险。
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
"""


# 缺 frontmatter / 缺关键章节 / 空 symbols 的低分页。
_BARE_PAGE = """# 某投资想法

直接写正文，没有 frontmatter，没有总结，没有风险。
"""

_MISSING_FIELDS_PAGE = """---
title: 某行业策略
created: 2026-06-01
updated: 2026-06-01
sources: []
tags: [策略]
---

# 某行业策略

## 核心观点

只有观点，无 symbols / themes / 风险 / sources 字段。
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵 mini Tree Work 知识库（只在 tmp_path 下，绝不触碰真实知识库）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE)
    _write(inv / "PCB产业链综述.md", _INDUSTRY_PAGE)
    _write(inv / "AI算力基础设施-公司评分表.md", _SCORE_TABLE_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _TODO_PAGE)
    _write(inv / "某投资想法.md", _BARE_PAGE)
    _write(inv / "某行业策略-缺字段.md", _MISSING_FIELDS_PAGE)

    # index.md 引用其中 5 个，故意漏掉 "某投资想法" 制造 missing，引用 1 个不存在的孤儿。
    _write(
        tmp_path / INDEX_MD,
        "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n\n"
        "- [[investment/华勤技术603296-超节点]] — 公司页\n"
        "- [[investment/PCB产业链综述]] — 行业页\n"
        "- [[investment/AI算力基础设施-公司评分表]] — 评分表\n"
        "- [[investment/Dell-FQ127-待补充]] — 待补充\n"
        "- [[investment/某行业策略-缺字段]] — 缺字段\n"
        "- [[investment/不存在的孤儿页]] — 孤儿\n",
    )
    # log.md
    _write(tmp_path / LOG_MD, "# Log\n\n- entry1\n- entry2\n")
    # inbox
    _write(tmp_path / INBOX_SUBDIR / "2026-05-27-inbox.md", "# inbox\n")
    _write(tmp_path / INBOX_SUBDIR / "screenshots" / "x.png", "PNG")
    # raw
    _write(tmp_path / RAW_SUBDIR / "2026-05-14-中邮证券.md", "# raw\n")
    return tmp_path


# ── 1. frontmatter 解析 ──────────────────────────────────────────────


class TestFrontmatterParse:
    def test_split_no_frontmatter(self):
        fm, body = _split_frontmatter("hello\nworld")
        assert fm is None
        assert body == "hello\nworld"

    def test_split_with_frontmatter(self):
        text = "---\ntitle: x\ntags: [a]\n---\n\n# Body\n"
        fm, body = _split_frontmatter(text)
        assert fm is not None
        assert "title: x" in fm
        # 正文首行保留（闭合 --- 之后的换行）。
        assert body.strip().startswith("# Body")

    def test_split_table_separator_not_treated_as_frontmatter(self):
        # 表格分隔 --- 不能被误判为 frontmatter 结束（已经在 body 中）
        text = "---\ntitle: x\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
        fm, body = _split_frontmatter(text)
        assert fm is not None
        assert "title: x" in fm
        assert "|---|---|" in body

    def test_parse_yaml_list_inline(self):
        fm = _parse_frontmatter('tags: [a, "b,c", d]')
        assert fm["tags"] == ["a", "b,c", "d"]

    def test_parse_yaml_multiline_list(self):
        fm_text = "sources:\n  - alpha\n  - beta\n"
        fm = _parse_frontmatter(fm_text)
        assert fm["sources"] == ["alpha", "beta"]

    def test_parse_empty_value_treated_as_empty(self):
        # PyYAML 把 ``sources:`` 解析为 None；极简解析器返回 []。
        # 两种实现都必须被 _is_nonempty_field 视为"无有效值"。
        fm = _parse_frontmatter("sources:\ntags: [a]")
        assert not _is_nonempty_field(fm.get("sources"))
        assert fm["tags"] == ["a"]

    def test_parse_invalid_yaml_returns_empty_dict(self):
        # 损坏 frontmatter：安全降级，不抛异常
        fm = _parse_frontmatter(": : : broken")
        assert isinstance(fm, dict)

    def test_parse_none_returns_empty(self):
        assert _parse_frontmatter(None) == {}


# ── 2. 辅助函数 ─────────────────────────────────────────────────────


class TestHelpers:
    def test_is_nonempty_field(self):
        assert _is_nonempty_field(None) is False
        assert _is_nonempty_field([]) is False
        assert _is_nonempty_field("") is False
        assert _is_nonempty_field("暂无") is False
        assert _is_nonempty_field("x") is True
        assert _is_nonempty_field([1]) is True

    def test_valid_until_expired_iso(self):
        assert _is_valid_until_expired("2020-01-01") is True
        assert _is_valid_until_expired("2099-12-31") is False

    def test_valid_until_expired_other_formats(self):
        assert _is_valid_until_expired("2020/01/01") is True
        assert _is_valid_until_expired("20200101") is True

    def test_valid_until_non_date_is_not_expired(self):
        assert _is_valid_until_expired("长期") is False
        assert _is_valid_until_expired("") is False
        assert _is_valid_until_expired(None) is False
        assert _is_valid_until_expired("notadate") is False

    def test_valid_until_invalid_calendar_date(self):
        # 2020-13-40 不是合法日历日，不应被判定过期
        assert _is_valid_until_expired("2020-13-40") is False


# ── 3. 页面类型识别 ─────────────────────────────────────────────────


class TestClassifyPageType:
    def test_company_by_filename_stock_code(self):
        # 文件名含 6 位股票代码 + company report_type → company
        assert (
            classify_page_type(
                "华勤技术603296-超节点.md", "华勤技术603296", {"report_type": "公司点评"}, [], False
            )
            == "company"
        )

    def test_industry_by_report_type(self):
        assert (
            classify_page_type(
                "PCB综述.md", "PCB综述", {"report_type": "综述"}, [], False
            )
            == "industry"
        )

    def test_score_table_by_keyword(self):
        assert (
            classify_page_type(
                "AI算力-公司评分表.md", "AI算力-公司评分表", {}, [], False
            )
            == "score_table"
        )

    def test_score_table_by_section_headers(self):
        headers = ["标的列表", "利好度 vs 共识度"]
        assert (
            classify_page_type("某表.md", "某表", {}, headers, False)
            == "score_table"
        )

    def test_summary_by_keyword(self):
        assert (
            classify_page_type(
                "研报核心看点汇总.md", "研报核心看点汇总", {}, [], False
            )
            == "summary"
        )

    def test_to_be_supplemented_priority(self):
        # body 含待补充标记 → 优先 to_be_supplemented，即便文件名是评分表
        assert (
            classify_page_type(
                "X-评分表.md", "X", {"report_type": "数据表"}, [], True
            )
            == "to_be_supplemented"
        )

    def test_to_be_supplemented_by_title(self):
        assert (
            classify_page_type(
                "Dell-FQ127-待补充.md", "Dell-FQ127-待补充", {}, [], False
            )
            == "to_be_supplemented"
        )

    def test_unclassified_when_no_signal(self):
        assert (
            classify_page_type("foo.md", "foo", {}, [], False)
            == "unclassified"
        )

    def test_company_via_stock_code_only(self):
        # 没有明确 report_type，但文件名有股票代码 → company
        assert (
            classify_page_type("大族激光002008-激光设备.md", "大族激光", {}, [], False)
            == "company"
        )


# ── 4. 单页审计 ─────────────────────────────────────────────────────


class TestSinglePageAudit:
    def test_company_page_high_readiness(self, tmp_path: Path):
        p = tmp_path / "华勤技术603296.md"
        _write(p, _COMPANY_PAGE)
        page = _audit_single_page("wiki/investment/华勤技术603296.md", p)
        assert page.page_type == "company"
        assert page.frontmatter_present is True
        assert page.has_summary_section is True
        assert page.has_risk_section is True
        assert page.has_sources_field is True
        assert page.has_symbols_field is True
        assert page.has_themes_field is True
        assert page.stale_risk_high is False
        assert page.valid_until_expired is False
        assert page.machine_readiness == "high"
        assert page.is_to_be_supplemented is False
        assert page.missing_recommended_fields == []

    def test_todo_page_low_readiness(self, tmp_path: Path):
        p = tmp_path / "Dell.md"
        _write(p, _TODO_PAGE)
        page = _audit_single_page("wiki/investment/Dell.md", p)
        assert page.page_type == "to_be_supplemented"
        assert page.is_to_be_supplemented is True
        assert page.machine_readiness == "low"
        assert page.stale_risk_high is True

    def test_score_table(self, tmp_path: Path):
        p = tmp_path / "AI算力-公司评分表.md"
        _write(p, _SCORE_TABLE_PAGE)
        page = _audit_single_page("wiki/investment/AI算力-公司评分表.md", p)
        assert page.page_type == "score_table"
        assert page.has_risk_section is True

    def test_bare_page_low_readiness(self, tmp_path: Path):
        p = tmp_path / "bare.md"
        _write(p, _BARE_PAGE)
        page = _audit_single_page("wiki/investment/bare.md", p)
        assert page.frontmatter_present is False
        assert page.machine_readiness == "low"
        assert page.has_symbols_field is False

    def test_expired_valid_until_detected(self, tmp_path: Path):
        text = _COMPANY_PAGE.replace("valid_until: 2099-12-31", "valid_until: 2020-01-01")
        p = tmp_path / "expired.md"
        _write(p, text)
        page = _audit_single_page("wiki/investment/expired.md", p)
        assert page.valid_until_expired is True
        assert page.valid_until == "2020-01-01"

    def test_compute_readiness_thresholds(self):
        high = WikiPageAudit(
            rel_path="x",
            title="x",
            page_type="company",
            frontmatter_present=True,
            has_summary_section=True,
            has_risk_section=True,
            has_sources_field=True,
            has_symbols_field=True,
            has_themes_field=True,
            missing_recommended_fields=[],
        )
        assert _compute_machine_readiness(high) == "high"

        low = WikiPageAudit(
            rel_path="y",
            title="y",
            page_type="company",
            frontmatter_present=True,
            has_summary_section=False,
            has_risk_section=False,
            has_sources_field=False,
            has_symbols_field=False,
            has_themes_field=False,
            missing_recommended_fields=[
                "symbols",
                "themes",
                "industry_chain_roles",
                "report_type",
                "evidence_level",
                "valid_until",
            ],
        )
        assert _compute_machine_readiness(low) == "low"


# ── 5. 整库审计 ─────────────────────────────────────────────────────


class TestFullAudit:
    def test_audit_fixture_kb(self, fixture_kb: Path):
        result = audit_local_knowledge(str(fixture_kb))
        assert result.investment_page_count == 6
        assert result.inbox_item_count == 2
        assert result.raw_md_count == 1
        assert result.index_present is True
        assert result.log_present is True
        assert result.log_line_count >= 3

        # 类型分布
        assert result.type_counts["company"] == 1
        assert result.type_counts["score_table"] == 1
        assert result.type_counts["to_be_supplemented"] == 1
        # 行业页 + 缺字段页 + 裸页 中至少有 industry
        assert result.type_counts.get("industry", 0) >= 1

        # readiness 至少有 high/low
        assert result.machine_readiness_counts["high"] >= 1
        assert result.machine_readiness_counts["low"] >= 1

    def test_index_misalignment(self, fixture_kb: Path):
        result = audit_local_knowledge(str(fixture_kb))
        # "某投资想法" 没有被 index 引用 → 出现在 missing
        assert "wiki/investment/某投资想法.md" in result.index_missing_pages
        # "不存在的孤儿页" 在 index 但无文件 → orphan
        assert "不存在的孤儿页" in result.index_orphan_entries

    def test_collect_index_misalignment_directly(self):
        result = LocalKnowledgeAuditResult(knowledge_root="x", scanned_at="d")
        result.page_audits = [
            WikiPageAudit(rel_path="wiki/investment/a.md", title="a", page_type="company", frontmatter_present=True),
            WikiPageAudit(rel_path="wiki/investment/b.md", title="b", page_type="company", frontmatter_present=True),
        ]
        # index 引用 a 和不存在的 c
        _collect_index_misalignment(result, ["investment/a", "investment/c"])
        assert result.index_missing_pages == ["wiki/investment/b.md"]
        assert result.index_orphan_entries == ["c"]

    def test_gaps_at_least_five_classes(self, fixture_kb: Path):
        result = audit_local_knowledge(str(fixture_kb))
        # 任务硬性要求 >=5 类结构缺口
        assert len(result.structural_gaps) >= 5
        gap_names = [g["gap"] for g in result.structural_gaps]
        # 关键缺口类别必须出现
        assert any("总结" in n or "核心观点" in n for n in gap_names)
        assert any("风险" in n for n in gap_names)
        assert any("symbols" in n for n in gap_names)
        assert any("待补充" in n for n in gap_names)
        assert any("inbox" in n for n in gap_names)
        # 每个缺口都带接入建议
        for g in result.structural_gaps:
            assert g["suggestion"]
            assert isinstance(g["count"], int)

    def test_integration_suggestions_present(self, fixture_kb: Path):
        result = audit_local_knowledge(str(fixture_kb))
        assert len(result.integration_suggestions) >= 5
        # 必须强调只读 wiki/investment，不读 inbox/raw
        joined = "\n".join(result.integration_suggestions)
        assert "wiki/investment" in joined

    def test_to_dict_serializable(self, fixture_kb: Path):
        import json

        result = audit_local_knowledge(str(fixture_kb))
        d = result.to_dict()
        # 必须可 JSON 序列化
        text = json.dumps(d, ensure_ascii=False)
        assert "page_audits" in text
        assert "structural_gaps" in text

    def test_missing_root_records_error(self, tmp_path: Path):
        result = audit_local_knowledge(str(tmp_path / "does_not_exist"))
        assert result.investment_page_count == 0
        assert any("不存在" in e for e in result.errors)
        # 即使根目录不存在也要产出 >=5 类缺口
        assert len(result.structural_gaps) >= 5

    def test_empty_root_still_runs(self, tmp_path: Path):
        # 空目录：investment 分区不存在，不应抛异常
        result = audit_local_knowledge(str(tmp_path))
        assert result.investment_page_count == 0
        assert result.index_present is False


# ── 6. 报告渲染 ─────────────────────────────────────────────────────


class TestRenderReport:
    def test_report_basic_sections(self, fixture_kb: Path):
        result = audit_local_knowledge(str(fixture_kb))
        md = render_audit_report(result)
        assert "Tree Work 本地知识库只读审计报告" in md
        assert "扫描概览" in md
        assert "页面类型分布" in md
        assert "Machine Readiness" in md
        assert "Frontmatter 字段覆盖" in md
        assert "结构缺口" in md
        assert "index.md 对齐" in md
        assert "inbox 未消化清单" in md
        assert "TA 接入建议" in md

    def test_report_no_original_paragraphs(self, fixture_kb: Path):
        """报告里绝不能出现正文段落（如投资逻辑下的 bullet 正文）。"""
        result = audit_local_knowledge(str(fixture_kb))
        md = render_audit_report(result)
        # 这些是 fixture 正文里的具体句子，不应出现在只读审计报告
        assert "AI服务器增长" not in md
        assert "PCB 需求旺盛" not in md
        assert "需求不及预期" not in md

    def test_report_contains_counts(self, fixture_kb: Path):
        result = audit_local_knowledge(str(fixture_kb))
        md = render_audit_report(result)
        assert f"investment_md_pages: **{result.investment_page_count}**" in md


# ── 7. 默认根目录 ────────────────────────────────────────────────────


class TestDefaultRoot:
    def test_env_priority(self, monkeypatch):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", "/tmp/env_kb")
        assert default_knowledge_root() == "/tmp/env_kb"

    def test_fallback_to_home(self, monkeypatch):
        monkeypatch.delenv("AUTO_DEV_KNOWLEDGE_ROOT", raising=False)
        monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
        root = default_knowledge_root()
        assert root.endswith("Documents/knowledge")


# ── 8. CLI 子进程冒烟 ────────────────────────────────────────────────


class TestCliSmoke:
    def test_cli_help(self):
        proc = subprocess.run(
            [sys.executable, "scripts/audit_local_knowledge.py", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        assert "KB-001" in proc.stdout

    def test_cli_markdown_stdout(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/audit_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 0
        assert "Tree Work 本地知识库只读审计报告" in proc.stdout

    def test_cli_json_output(self, fixture_kb: Path):
        import json

        proc = subprocess.run(
            [
                sys.executable,
                "scripts/audit_local_knowledge.py",
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
        assert payload["investment_page_count"] == 6
        assert len(payload["structural_gaps"]) >= 5

    def test_cli_write_file(self, fixture_kb: Path, tmp_path: Path):
        out_file = tmp_path / "out" / "report.md"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/audit_local_knowledge.py",
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
        assert "结构缺口" in content


# ── 9. 只读安全性 ───────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_audit_does_not_modify_kb(self, fixture_kb: Path):
        """审计前后，知识库内所有文件内容/大小不应变化。"""
        def snapshot(root: Path) -> Dict[str, int]:
            snap = {}
            for p in root.rglob("*"):
                if p.is_file():
                    snap[str(p.relative_to(root))] = p.stat().st_size
            return snap

        before = snapshot(fixture_kb)
        audit_local_knowledge(str(fixture_kb))
        audit_local_knowledge(str(fixture_kb))  # 跑两次确保幂等
        after = snapshot(fixture_kb)
        assert before == after

    def test_no_new_files_in_kb(self, fixture_kb: Path):
        files_before = {p for p in fixture_kb.rglob("*") if p.is_file()}
        audit_local_knowledge(str(fixture_kb))
        files_after = {p for p in fixture_kb.rglob("*") if p.is_file()}
        # 审计不应在知识库内新增任何文件
        assert files_after == files_before
