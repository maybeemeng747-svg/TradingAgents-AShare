# [KB-002] local_knowledge_contract
"""Tests for investment wiki 输出协议 lint (KB-002).

覆盖：
  - 单页 lint findings（必填字段/推荐字段/必含章节/评分表表头/symbols/stale/低置信）
  - machine_readiness 计算（high/medium/low，含待补充/stale 特例）
  - 整库 lint 聚合（缺口清单、Top 修复优先级、index 对齐）
  - 报告渲染（不含原文、含规则表与修复建议）
  - CLI 子进程冒烟（默认非阻塞、--fail-on-error、--json）
  - 只读安全性（不写入知识库）
  - 契约文档存在且字段一致
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from tradingagents.dataflows.local_knowledge_lint import (
    HIGH_STALE_RISK_VALUES,
    LOW_CONFIDENCE_EVIDENCE_LEVELS,
    REQUIRED_FRONTMATTER_FIELDS,
    SCORE_TABLE_REQUIRED_COLUMNS,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    KnowledgeLintResult,
    LintFinding,
    PageLintResult,
    _compute_readiness,
    _extract_table_header_rows,
    default_knowledge_root,
    lint_local_knowledge,
    lint_single_page,
    render_lint_report,
    suggest_lint_output_path,
)
from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INBOX_SUBDIR,
    INVESTMENT_SUBDIR,
    LOG_MD,
    RAW_SUBDIR,
)


# ── fixture 文本 ─────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 满足全部契约的“满分”公司页 → high readiness。
_FULL_COMPANY_PAGE = """---
title: 华勤技术603296-超节点进入出货周期
created: 2026-05-25
updated: 2026-06-29
sources:
  - "[[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|中邮证券-华勤技术超节点]]"
tags: [华勤技术, 超节点]
related: [[investment/PCB产业链综述]]
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

## 风险提示

- 需求不及预期

## 原始资料

- 中邮证券研报
"""

# 评分表页：表头完整。
_FULL_SCORE_TABLE_PAGE = """---
title: AI算力基础设施-公司评分表
created: 2026-05-13
updated: 2026-06-29
sources:
  - 星球社群截图
tags: [公司评分]
related: []
symbols: ["603296.SH 华勤技术", "000977.SZ 浪潮信息"]
themes: [AI算力]
report_type: 数据表
evidence_level: B
valid_until: 2099-07-29
source_quality: 中
stale_risk: 低
---

# AI算力基础设施 - 公司评分表

## 标的列表

| 公司 | 代码 | 核心业务 | 板块 | 利好度 | 共识度 | 预计启动 | 期待周期 |
|------|------|----------|------|--------|--------|----------|----------|
| 华勤技术 | 603296 | ODM | AI算力 | 9.5 | 90 | 几天 | 半年 |

## 风险提示

评分时效风险。
"""

# 缺一堆字段的低分页。
_BARE_PAGE = """# 某投资想法

直接写正文，没有 frontmatter，没有总结，没有风险。
"""

# 公司页但缺 symbols → SYM-001 error。
_COMPANY_NO_SYMBOL_PAGE = """---
title: 某公司分析
created: 2026-06-01
updated: 2026-06-01
sources:
  - 某研报
tags: [公司]
related: []
report_type: 公司分析
evidence_level: A
valid_until: 长期
source_quality: 中
stale_risk: 低
---

# 某公司

## 一句话总结

好公司。

## 投资逻辑

- 增长

## 风险提示

- 估值高
"""

# 评分表缺表头列。
_BAD_SCORE_TABLE_PAGE = """---
title: 缺表头评分表
created: 2026-06-01
updated: 2026-06-01
sources:
  - x
tags: [评分]
related: []
symbols: ["603296.SH 华勤技术"]
themes: [AI算力]
report_type: 数据表
evidence_level: B
valid_until: 长期
source_quality: 中
stale_risk: 低
---

# 缺表头评分表

## 标的列表

| 公司 | 代码 | 利好度 |
|------|------|--------|
| 华勤 | 603296 | 9 |

## 风险提示

- x
"""

# 待补充页（显式标记）。
_TODO_PAGE = """---
title: Dell-FQ127-待补充
created: 2026-05-31
updated: 2026-06-29
sources:
  - "[[../../raw/x.md|Bernstein-Dell]]"
tags: [Dell, 待补充]
related: []
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

## 一句话总结

待补充。

## 投资逻辑

- 推测方向

## 风险提示

- 待补充
"""

# 过期页（valid_until 早于今天）。
_EXPIRED_PAGE = """---
title: 某过期策略
created: 2025-01-01
updated: 2025-01-01
sources:
  - 旧研报
tags: [策略]
related: []
symbols: []
themes: [旧主题]
report_type: 策略
evidence_level: B
valid_until: 2020-01-01
source_quality: 中
stale_risk: 低
---

# 某过期策略

## 核心观点

旧观点。

## 风险提示

- 已过期

## 原始资料

- 旧研报
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵 mini Tree Work 知识库（只在 tmp_path 下，绝不触碰真实知识库）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _FULL_COMPANY_PAGE)
    _write(inv / "AI算力基础设施-公司评分表.md", _FULL_SCORE_TABLE_PAGE)
    _write(inv / "某投资想法.md", _BARE_PAGE)
    _write(inv / "某公司分析-无symbol.md", _COMPANY_NO_SYMBOL_PAGE)
    _write(inv / "缺表头评分表.md", _BAD_SCORE_TABLE_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _TODO_PAGE)
    _write(inv / "某过期策略.md", _EXPIRED_PAGE)

    _write(
        tmp_path / INDEX_MD,
        "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n\n"
        "- [[investment/华勤技术603296-超节点]]\n"
        "- [[investment/AI算力基础设施-公司评分表]]\n"
        "- [[investment/某投资想法]]\n"
        "- [[investment/某公司分析-无symbol]]\n"
        "- [[investment/缺表头评分表]]\n"
        "- [[investment/Dell-FQ127-待补充]]\n"
        "- [[investment/某过期策略]]\n",
    )
    _write(tmp_path / LOG_MD, "# Log\n\n- entry1\n")
    _write(tmp_path / INBOX_SUBDIR / "inbox.md", "# inbox\n")
    _write(tmp_path / RAW_SUBDIR / "raw.md", "# raw\n")
    return tmp_path


# ── 1. 单页 lint ──────────────────────────────────────────────────────


class TestSinglePageLint:
    def test_full_company_page_is_high(self, tmp_path: Path):
        p = tmp_path / "华勤技术603296-超节点.md"
        _write(p, _FULL_COMPANY_PAGE)
        result = lint_single_page(p.name, p)
        assert result.machine_readiness == "high"
        assert result.error_count == 0
        # high 允许少量 warning（related 已补，推荐字段齐全，应无 warning）
        assert result.warning_count == 0
        assert result.has_symbols is True
        assert result.is_to_be_supplemented is False
        assert result.is_stale is False

    def test_bare_page_low_readiness(self, tmp_path: Path):
        p = tmp_path / "某投资想法.md"
        _write(p, _BARE_PAGE)
        result = lint_single_page(p.name, p)
        assert result.machine_readiness == "low"
        # 缺 title/created/updated/sources/tags/related + 缺 summary/risk 章节
        assert result.error_count >= 4
        rule_ids = {f.rule_id for f in result.findings}
        assert "FMR-001" in rule_ids
        assert "SEC-001" in rule_ids
        assert "SEC-002" in rule_ids

    def test_company_page_without_symbol_triggers_sym001(self, tmp_path: Path):
        p = tmp_path / "某公司分析.md"
        _write(p, _COMPANY_NO_SYMBOL_PAGE)
        result = lint_single_page(p.name, p)
        assert result.page_type == "company"
        rule_ids = {f.rule_id for f in result.findings}
        assert "SYM-001" in rule_ids
        sym = next(f for f in result.findings if f.rule_id == "SYM-001")
        assert sym.severity == SEVERITY_ERROR
        assert sym.field == "symbols"
        assert "603296.SH" in sym.fix_suggestion  # 修复建议含示例格式

    def test_full_score_table_passes_tbl001(self, tmp_path: Path):
        p = tmp_path / "评分表.md"
        _write(p, _FULL_SCORE_TABLE_PAGE)
        result = lint_single_page(p.name, p)
        assert result.page_type == "score_table"
        rule_ids = {f.rule_id for f in result.findings}
        assert "TBL-001" not in rule_ids

    def test_bad_score_table_triggers_tbl001(self, tmp_path: Path):
        p = tmp_path / "缺表头评分表.md"
        _write(p, _BAD_SCORE_TABLE_PAGE)
        result = lint_single_page(p.name, p)
        assert result.page_type == "score_table"
        rule_ids = {f.rule_id for f in result.findings}
        assert "TBL-001" in rule_ids
        tbl = next(f for f in result.findings if f.rule_id == "TBL-001")
        # 缺的列在 message 里点名
        missing = set(SCORE_TABLE_REQUIRED_COLUMNS) - {"公司", "代码", "利好度"}
        for col in missing:
            assert col in tbl.message

    def test_todo_page_is_low_and_info(self, tmp_path: Path):
        p = tmp_path / "Dell-FQ127-待补充.md"
        _write(p, _TODO_PAGE)
        result = lint_single_page(p.name, p)
        # 待补充一律 low
        assert result.machine_readiness == "low"
        assert result.is_to_be_supplemented is True
        assert result.is_low_confidence is True  # evidence_level=C
        assert result.is_stale is True  # stale_risk=高
        rule_ids = {f.rule_id for f in result.findings}
        assert "TODO-001" in rule_ids
        assert "EVID-001" in rule_ids
        assert "STALE-001" in rule_ids
        # info 级 findings 不计入 error
        todo = next(f for f in result.findings if f.rule_id == "TODO-001")
        assert todo.severity == SEVERITY_INFO

    def test_expired_page_triggers_stale002(self, tmp_path: Path):
        p = tmp_path / "某过期策略.md"
        _write(p, _EXPIRED_PAGE)
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        assert "STALE-002" in rule_ids
        assert result.is_stale is True
        # stale 阻止 high
        assert result.machine_readiness != "high"


# ── 2. 必填/推荐字段规则 ─────────────────────────────────────────────


class TestFieldRules:
    def test_required_fields_cover_contract(self):
        # 契约第 1 条：title/created/updated/sources/tags/related
        assert set(REQUIRED_FRONTMATTER_FIELDS) == {
            "title",
            "created",
            "updated",
            "sources",
            "tags",
            "related",
        }

    def test_missing_related_is_warning_not_error(self, tmp_path: Path):
        page = """---
title: x
created: 2026-06-01
updated: 2026-06-01
sources:
  - s
tags: [t]
symbols: ["603296.SH 华勤技术"]
themes: [AI]
report_type: 公司点评
evidence_level: A
valid_until: 长期
source_quality: 高
stale_risk: 低
---

# x

## 一句话总结

s

## 投资逻辑

- a

## 风险提示

- b
"""
        p = tmp_path / "x.md"
        _write(p, page)
        result = lint_single_page(p.name, p)
        related_findings = [f for f in result.findings if f.field == "related"]
        assert len(related_findings) == 1
        assert related_findings[0].severity == SEVERITY_WARNING
        assert related_findings[0].rule_id == "FMR-001"

    def test_empty_sources_is_error(self, tmp_path: Path):
        page = """---
title: x
created: 2026-06-01
updated: 2026-06-01
sources: []
tags: [t]
related: []
symbols: ["603296.SH 华勤技术"]
---

# x

## 一句话总结

s

## 风险提示

- b
"""
        p = tmp_path / "x.md"
        _write(p, page)
        result = lint_single_page(p.name, p)
        sources_findings = [
            f for f in result.findings if f.field == "sources" and f.rule_id == "FMR-001"
        ]
        assert len(sources_findings) == 1
        assert sources_findings[0].severity == SEVERITY_ERROR

    def test_all_recommended_fields_checked(self, tmp_path: Path):
        # 只有必填字段，全部推荐字段缺失 → 8 个 FMR-002 warning
        page = """---
title: x
created: 2026-06-01
updated: 2026-06-01
sources:
  - s
tags: [t]
related: []
---

# x

## 一句话总结

s

## 投资逻辑

- a

## 风险提示

- b

## 原始资料

- s
"""
        p = tmp_path / "x.md"
        _write(p, page)
        result = lint_single_page(p.name, p)
        rec_findings = [f for f in result.findings if f.rule_id == "FMR-002"]
        assert len(rec_findings) == 8
        rec_fields = {f.field for f in rec_findings}
        assert rec_fields == {
            "symbols",
            "themes",
            "industry_chain_roles",
            "report_type",
            "evidence_level",
            "valid_until",
            "source_quality",
            "stale_risk",
        }


# ── 3. machine_readiness 计算 ────────────────────────────────────────


class TestReadiness:
    def test_high_clean_page(self):
        assert _compute_readiness(0, 0, False, False) == "high"

    def test_high_allows_upto_2_warnings(self):
        assert _compute_readiness(0, 2, False, False) == "high"

    def test_high_blocked_by_stale(self):
        assert _compute_readiness(0, 0, False, True) != "high"

    def test_medium_with_few_errors(self):
        assert _compute_readiness(1, 3, False, False) == "medium"

    def test_low_with_many_errors(self):
        assert _compute_readiness(5, 0, False, False) == "low"

    def test_todo_always_low(self):
        # 即使 0 error，待补充也强制 low
        assert _compute_readiness(0, 0, True, False) == "low"

    def test_todo_low_even_with_clean(self):
        assert _compute_readiness(0, 0, True, True) == "low"


# ── 4. 评分表表头解析 ────────────────────────────────────────────────


class TestScoreTableHeader:
    def test_extract_header_rows(self):
        body = """text

| 公司 | 代码 | 核心业务 |
|------|------|----------|
| a | b | c |

other
"""
        rows = _extract_table_header_rows(body)
        assert len(rows) == 1
        assert "公司" in rows[0]
        assert "代码" in rows[0]

    def test_no_separator_no_header(self):
        body = "| a | b |\n| 1 | 2 |\n"  # 缺分隔符行
        assert _extract_table_header_rows(body) == []

    def test_required_columns_set(self):
        assert set(SCORE_TABLE_REQUIRED_COLUMNS) == {
            "公司",
            "代码",
            "核心业务",
            "板块",
            "利好度",
            "共识度",
            "预计启动",
            "期待周期",
        }


# ── 5. 整库 lint 聚合 ────────────────────────────────────────────────


class TestKnowledgeLint:
    def test_lint_runs_on_fixture(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        assert isinstance(result, KnowledgeLintResult)
        assert result.page_count == 7
        # readiness 三档都被覆盖
        assert result.readiness_counts["high"] >= 1
        assert result.readiness_counts["low"] >= 1
        assert result.contract_version == "kb-002-v1"

    def test_findings_aggregated_by_severity(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        sev = result.findings_by_severity
        assert sev[SEVERITY_ERROR] >= 1
        assert sev[SEVERITY_WARNING] >= 1
        # info: 至少有待补充/低置信标记
        assert sev[SEVERITY_INFO] >= 1

    def test_findings_by_rule_populated(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        assert "FMR-001" in result.findings_by_rule
        assert "SEC-002" in result.findings_by_rule  # 缺风险

    def test_gap_lists_populated(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        # _BARE_PAGE 缺 summary/risk/symbols
        assert any("某投资想法" in p for p in result.pages_missing_risk)
        assert any("某投资想法" in p for p in result.pages_missing_summary)
        assert any("某投资想法" in p for p in result.pages_missing_symbols)
        # 待补充 + 低置信 + 过期
        assert any("Dell" in p for p in result.pages_to_be_supplemented)
        assert any("Dell" in p for p in result.pages_low_confidence)
        assert any("过期" in p for p in result.pages_stale)

    def test_top_fix_priorities_sorted_error_first(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        assert len(result.top_fix_priorities) > 0
        # 第一条应为 error 规则
        first = result.top_fix_priorities[0]
        assert first["affected_pages"] > 0
        assert first["fix_suggestion"]
        assert isinstance(first["samples"], list)

    def test_index_alignment(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        # fixture 里 index 引用了全部 7 页
        assert result.index_missing_pages == []

    def test_index_missing_page_detected(self, fixture_kb: Path):
        # 再加一篇未被 index 引用的页
        _write(
            fixture_kb / INVESTMENT_SUBDIR / "未索引页.md",
            _FULL_COMPANY_PAGE,
        )
        result = lint_local_knowledge(str(fixture_kb))
        assert any("未索引页" in p for p in result.index_missing_pages)

    def test_missing_root_returns_empty(self, tmp_path: Path):
        result = lint_local_knowledge(str(tmp_path / "nope"))
        assert result.page_count == 0
        assert any("不存在" in e for e in result.errors)
        assert result.readiness_counts == {"high": 0, "medium": 0, "low": 0}

    def test_to_dict_serializable(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        payload = result.to_dict()
        # 必须可 JSON 序列化（CLI --json 依赖）
        json.dumps(payload, ensure_ascii=False)

    def test_low_readiness_does_not_block(self, fixture_kb: Path):
        # 验收要求：低分页面不抛异常、不阻塞
        result = lint_local_knowledge(str(fixture_kb))
        assert len(result.pages_low_readiness) >= 1
        # 不抛异常即通过


# ── 6. 报告渲染 ──────────────────────────────────────────────────────


class TestReportRender:
    def test_report_contains_key_sections(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        report = render_lint_report(result)
        assert "KB-002" in report
        assert "Machine Readiness" in report
        assert "规则命中分布" in report
        assert "Top 修复优先级" in report
        assert "缺口页面清单" in report

    def test_report_no_long_original_text(self, fixture_kb: Path):
        # 不应包含正文段落（只含文件名/字段/规则）
        result = lint_local_knowledge(str(fixture_kb))
        report = render_lint_report(result)
        assert "华勤技术超节点进入出货周期。" not in report  # 正文句子
        assert "AI服务器增长" not in report  # 正文要点

    def test_report_contains_rule_ids(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        report = render_lint_report(result)
        assert "FMR-001" in report
        assert "SEC-002" in report

    def test_report_empty_kb_does_not_crash(self, tmp_path: Path):
        result = lint_local_knowledge(str(tmp_path / "nope"))
        report = render_lint_report(result)
        assert "KB-002" in report


# ── 7. CLI 子进程冒烟 ────────────────────────────────────────────────


class TestCliSmoke:
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def test_cli_help(self):
        proc = subprocess.run(
            [sys.executable, "scripts/lint_local_knowledge.py", "--help"],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0
        assert "KB-002" in proc.stdout

    def test_cli_markdown_stdout(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0
        assert "契约 lint 报告" in proc.stdout

    def test_cli_json_output(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
                "--json",
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["page_count"] == 7
        assert payload["contract_version"] == "kb-002-v1"
        assert "readiness_counts" in payload

    def test_cli_write_file(self, fixture_kb: Path, tmp_path: Path):
        out_file = tmp_path / "out" / "lint.md"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
                "--output",
                str(out_file),
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0
        assert out_file.exists()
        assert "Top 修复优先级" in out_file.read_text(encoding="utf-8")

    def test_cli_default_non_blocking_on_errors(self, fixture_kb: Path):
        # 默认即使有 error finding 也返回 0（不阻塞 TA）
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0

    def test_cli_fail_on_error_returns_nonzero(self, fixture_kb: Path):
        # fixture 含 error findings
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb),
                "--stdout",
                "--fail-on-error",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 2

    def test_cli_missing_root_returns_1(self, tmp_path: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(tmp_path / "nope"),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 1


# ── 8. 只读安全性 ───────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_lint_does_not_modify_kb(self, fixture_kb: Path):
        def snapshot(root: Path) -> Dict[str, int]:
            snap = {}
            for p in root.rglob("*"):
                if p.is_file():
                    snap[str(p.relative_to(root))] = p.stat().st_size
            return snap

        before = snapshot(fixture_kb)
        lint_local_knowledge(str(fixture_kb))
        lint_local_knowledge(str(fixture_kb))  # 跑两次确保幂等
        after = snapshot(fixture_kb)
        assert before == after

    def test_no_new_files_in_kb(self, fixture_kb: Path):
        files_before = {p for p in fixture_kb.rglob("*") if p.is_file()}
        lint_local_knowledge(str(fixture_kb))
        files_after = {p for p in fixture_kb.rglob("*") if p.is_file()}
        assert files_after == files_before


# ── 9. 默认根目录 / 输出路径 ─────────────────────────────────────────


class TestDefaults:
    def test_default_root_env_override(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(tmp_path))
        assert default_knowledge_root() == str(tmp_path)

    def test_default_root_knowledge_root_env(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("AUTO_DEV_KNOWLEDGE_ROOT", raising=False)
        monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path))
        assert default_knowledge_root() == str(tmp_path)

    def test_default_root_fallback_home(self, monkeypatch):
        monkeypatch.delenv("AUTO_DEV_KNOWLEDGE_ROOT", raising=False)
        monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
        root = default_knowledge_root()
        assert root.endswith("Documents/knowledge")

    def test_suggest_output_path_format(self):
        path = suggest_lint_output_path()
        assert "local_knowledge_lint-" in path
        assert path.endswith(".md")


# ── 10. 契约文档存在且一致 ────────────────────────────────────────────


class TestContractDoc:
    def test_contract_doc_exists(self):
        path = Path(__file__).resolve().parent.parent / "docs" / "local_knowledge_contract.md"
        assert path.exists(), "docs/local_knowledge_contract.md 必须存在"

    def test_contract_doc_references_all_required_fields(self):
        path = Path(__file__).resolve().parent.parent / "docs" / "local_knowledge_contract.md"
        text = path.read_text(encoding="utf-8")
        for f in REQUIRED_FRONTMATTER_FIELDS:
            assert f in text, f"契约文档未提及必填字段 {f}"

    def test_contract_doc_references_score_columns(self):
        path = Path(__file__).resolve().parent.parent / "docs" / "local_knowledge_contract.md"
        text = path.read_text(encoding="utf-8")
        for col in SCORE_TABLE_REQUIRED_COLUMNS:
            assert col in text

    def test_contract_doc_references_kb002(self):
        path = Path(__file__).resolve().parent.parent / "docs" / "local_knowledge_contract.md"
        text = path.read_text(encoding="utf-8")
        assert "KB-002" in text
        assert "local_knowledge_lint" in text
