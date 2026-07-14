# [HY-011] half_year_metadata_sanity
"""Tests for 半年报报告期/披露日/修订版本元数据 sanity check（HY-011）。

覆盖：
  - 单页规则 HYM-001~004/007：合法、缺失、未来日期、错期、symbol 错配、
    period_end_date-period 不一致、period_end_date>disclosure_date。
  - 跨页规则 HYM-005/006：同 symbol+period 多版本无修订标记 / 检测到修订稿。
  - 缺失 vs 非法严格分开（HY-001 已覆盖缺失，HY-011 只在字段存在时做组合校验）。
  - 集成到 ``lint_local_knowledge``：findings 聚合、report 渲染、to_dict 序列化。
  - 与 HY-001/KB-002/KB-014 现有 lint 回归兼容（HYM 规则不破坏 HYF/CIT 行为）。
  - 只读安全 / 不写知识库 / 不调 LLM。
  - 修订稿只用于事实索引选择，不生成交易动作。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

import pytest

from tradingagents.dataflows.half_year_metadata_sanity import (
    HYM_RULE_IDS,
    REVISION_FIELDS,
    STRICT_HALF_YEAR_REPORT_TYPES,
    _extract_period_token,
    _parse_date,
    _parse_symbol_entry,
    check_half_year_metadata_cross_page,
    check_half_year_metadata_sanity_single,
    detect_revision_marker,
)
from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INVESTMENT_SUBDIR,
    LOG_MD,
)
from tradingagents.dataflows.local_knowledge_lint import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    KnowledgeLintResult,
    LintFinding,
    PageLintResult,
    lint_local_knowledge,
    lint_single_page,
    render_lint_report,
)


# ── fixture helpers ──────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 固定 today 避免测试随时间漂移；HYM-001 需要稳定 reference。
_REF_TODAY = date(2026, 7, 14)


def _make_hy_page(
    *,
    rel_name: str = "base.md",
    report_type: str = "半年报",
    financial_period: str = "2025H1",
    period_end_date: str | None = "2025-06-30",
    disclosure_date: str | None = "2025-08-30",
    symbols: List[str] | None = None,
    name: str | None = None,
    revision: str | None = None,
    is_revised: str | None = None,
    supersedes: str | None = None,
    amendment: str | None = None,
    extra_fm: str = "",
) -> str:
    """构造一篇合法半年报页 markdown（默认全字段齐全，可通过参数注入缺陷）。"""
    if symbols is None:
        symbols = ["603296.SH 华勤技术"]
    symbols_yaml = "\n".join(f'  - "{s}"' for s in symbols)
    name_line = f"name: {name}\n" if name else ""
    rd_line = f"period_end_date: {period_end_date}\n" if period_end_date else ""
    dd_line = f"disclosure_date: {disclosure_date}\n" if disclosure_date else ""
    rev_line = f"revision: {revision}\n" if revision else ""
    isrev_line = f"is_revised: {is_revised}\n" if is_revised else ""
    sup_line = f"supersedes: {supersedes}\n" if supersedes else ""
    amd_line = f"amendment: {amendment}\n" if amendment else ""
    return f"""---
title: {rel_name}
created: 2025-08-30
updated: 2025-08-30
sources:
  - "公司公告"
tags: [半年报]
related: []
symbols:
{symbols_yaml}
{name_line}{rd_line}{dd_line}{rev_line}{isrev_line}{sup_line}{amd_line}report_type: {report_type}
financial_period: {financial_period}
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿
risk_factors: [客户集中度]
{extra_fm}---

# {rel_name}

## 一句话总结

测试页。

## 投资逻辑

- 增长

## 风险提示

- 客户集中度

## 原始资料

- 公司公告
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵含合法半年报页的 mini KB（用于集成测试）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    # 合法页：全字段齐全，disclosure_date 在过去
    _write(
        inv / "合法半年报.md",
        _make_hy_page(
            rel_name="合法半年报.md",
            disclosure_date="2025-08-30",
            period_end_date="2025-06-30",
        ),
    )
    _write(
        tmp_path / INDEX_MD,
        "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n\n"
        "- [[investment/合法半年报]]\n",
    )
    _write(tmp_path / LOG_MD, "# Log\n")
    return tmp_path


@pytest.fixture()
def fixture_kb_with_conflicts(tmp_path: Path) -> Path:
    """构造含多版本冲突的 KB（同 symbol+period 多页）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    # 3 页同 symbol+period，无修订标记 → HYM-005
    _write(
        inv / "版本A.md",
        _make_hy_page(
            rel_name="版本A.md",
            symbols=["603296.SH 华勤技术"],
            financial_period="2025H1",
        ),
    )
    _write(
        inv / "版本B.md",
        _make_hy_page(
            rel_name="版本B.md",
            symbols=["603296.SH 华勤技术"],
            financial_period="2025H1",
        ),
    )
    _write(
        tmp_path / INDEX_MD,
        "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n\n"
        "- [[investment/版本A]]\n- [[investment/版本B]]\n",
    )
    _write(tmp_path / LOG_MD, "# Log\n")
    return tmp_path


@pytest.fixture()
def fixture_kb_with_revision(tmp_path: Path) -> Path:
    """构造含修订稿的 KB（同 symbol+period，一个带 revision 标记）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(
        inv / "原稿.md",
        _make_hy_page(
            rel_name="原稿.md",
            symbols=["603296.SH 华勤技术"],
            financial_period="2025H1",
        ),
    )
    _write(
        inv / "修订稿.md",
        _make_hy_page(
            rel_name="修订稿.md",
            symbols=["603296.SH 华勤技术"],
            financial_period="2025H1",
            revision="2",
            supersedes="[[investment/原稿]]",
        ),
    )
    _write(
        tmp_path / INDEX_MD,
        "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n\n"
        "- [[investment/原稿]]\n- [[investment/修订稿]]\n",
    )
    _write(tmp_path / LOG_MD, "# Log\n")
    return tmp_path


# ── 1. 单元：工具函数 ────────────────────────────────────────────────


class TestHelpers:
    def test_parse_date_valid(self):
        assert _parse_date("2025-06-30") == date(2025, 6, 30)
        assert _parse_date("2025/06/30") == date(2025, 6, 30)
        assert _parse_date("20250630") == date(2025, 6, 30)

    def test_parse_date_invalid(self):
        assert _parse_date(None) is None
        assert _parse_date("") is None
        assert _parse_date("abc") is None
        assert _parse_date("2025-13-01") is None  # 非法月
        assert _parse_date("2025-02-30") is None  # 非法日历日
        assert _parse_date("2025-6-30") is None  # 必须两位

    def test_parse_symbol_entry(self):
        assert _parse_symbol_entry("603296.SH 华勤技术") == (
            "603296.SH",
            "华勤技术",
        )
        assert _parse_symbol_entry("603296 华勤技术") == (
            "603296",
            "华勤技术",
        )
        # 多空格 / 前后空格
        assert _parse_symbol_entry("  603296.SH   华勤技术  ") == (
            "603296.SH",
            "华勤技术",
        )
        # 无空格 → None
        assert _parse_symbol_entry("603296.SH") is None
        # 空 → None
        assert _parse_symbol_entry(None) is None
        assert _parse_symbol_entry("") is None

    @pytest.mark.parametrize(
        "period,expected",
        [
            ("2025H1", "H1"),
            ("2025H2", "H2"),
            ("2025中报", "中报"),
            ("2025半年报", "半年报"),
            ("2025年报", "年报"),
            ("2025一季报", "一季报"),
            ("2025三季报", "三季报"),
            ("2025Q1", "Q1"),
            ("2025Q2", "Q2"),
            ("FY26Q3", "Q3"),
            ("FY26Q4", "Q4"),
            # 半年报应优先于年报匹配（避免 "半年报" 被截为 "年报"）
            ("2025半年报", "半年报"),
        ],
    )
    def test_extract_period_token(self, period, expected):
        assert _extract_period_token(period) == expected

    def test_extract_period_token_unrecognized(self):
        assert _extract_period_token(None) is None
        assert _extract_period_token("") is None
        assert _extract_period_token("abcd") is None

    def test_is_revision_marked_revision(self):
        is_rev, fields = detect_revision_marker({"revision": "2"})
        assert is_rev is True
        assert "revision" in fields

    def test_is_revision_marked_revision_int(self):
        is_rev, fields = detect_revision_marker({"revision": 3})
        assert is_rev is True
        assert "revision" in fields

    def test_is_revision_marked_revision_zero(self):
        # revision=0 视为未标记
        is_rev, _ = detect_revision_marker({"revision": 0})
        assert is_rev is False

    def test_is_revision_marked_is_revised_bool(self):
        is_rev, fields = detect_revision_marker({"is_revised": True})
        assert is_rev is True
        assert "is_revised" in fields

    def test_is_revision_marked_is_revised_str(self):
        is_rev, _ = detect_revision_marker({"is_revised": "yes"})
        assert is_rev is True

    def test_is_revision_marked_supersedes_str(self):
        is_rev, fields = detect_revision_marker({"supersedes": "[[investment/原稿]]"})
        assert is_rev is True
        assert "supersedes" in fields

    def test_is_revision_marked_supersedes_list(self):
        is_rev, _ = detect_revision_marker(
            {"supersedes": ["[[investment/原稿]]"]}
        )
        assert is_rev is True

    def test_is_revision_marked_amendment(self):
        is_rev, fields = detect_revision_marker({"amendment": "更新营收数据"})
        assert is_rev is True
        assert "amendment" in fields

    def test_is_revision_marked_empty(self):
        assert detect_revision_marker({}) == (False, [])
        assert detect_revision_marker({"revision": "0"}) == (False, [])
        assert detect_revision_marker({"is_revised": "false"}) == (False, [])

    def test_revision_fields_constant(self):
        # 任务要求：修订标记字段稳定（供 lint 报告引用）
        assert "revision" in REVISION_FIELDS
        assert "is_revised" in REVISION_FIELDS
        assert "supersedes" in REVISION_FIELDS
        assert "amendment" in REVISION_FIELDS

    def test_hym_rule_ids_stable(self):
        # 7 条规则 ID 稳定，供 lint 报告 / 前端引用
        assert HYM_RULE_IDS == (
            "HYM-001",
            "HYM-002",
            "HYM-003",
            "HYM-004",
            "HYM-005",
            "HYM-006",
            "HYM-007",
        )


# ── 2. 单页规则 HYM-001~004/007 ──────────────────────────────────────


class TestSinglePageRules:
    def test_clean_page_no_findings(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-06-30",
            "disclosure_date": "2025-08-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert findings == []

    def test_non_financial_page_skipped(self):
        # 公司点评页不触发任何 HYM 规则
        fm = {
            "report_type": "公司点评",
            "financial_period": "2025H1",
            "period_end_date": "2099-06-30",  # 非财报页，不应触发
            "disclosure_date": "2099-08-30",
            "symbols": ["bad_code BadName"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert findings == []

    def test_hym001_future_disclosure_date(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-06-30",
            "disclosure_date": "2099-08-30",  # 未来
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        hym = [f for f in findings if f.rule_id == "HYM-001"]
        assert len(hym) == 1
        assert hym[0].severity == SEVERITY_WARNING

    def test_hym001_triggered_without_period_end_date(self):
        # disclosure_date 是已发生披露日；未来预排期必须使用独立字段。
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "disclosure_date": "2099-08-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert any(f.rule_id == "HYM-001" for f in findings)

    def test_hym001_not_triggered_when_disclosure_today(self):
        # 边界：disclosure_date == today 不算未来
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-06-30",
            "disclosure_date": _REF_TODAY.isoformat(),
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id == "HYM-001" for f in findings)

    def test_hym001_rejects_impossible_calendar_date(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "disclosure_date": "2025-13-40",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert any(f.rule_id == "HYM-001" for f in findings)

    def test_hym002_report_after_disclosure(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-12-31",  # 晚于 disclosure
            "disclosure_date": "2025-08-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        hym = [f for f in findings if f.rule_id == "HYM-002"]
        assert len(hym) == 1
        assert hym[0].severity == SEVERITY_ERROR

    def test_hym002_not_triggered_when_report_before_disclosure(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-06-30",
            "disclosure_date": "2025-08-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id == "HYM-002" for f in findings)

    def test_hym003_period_mismatch_half_year_with_q1(self):
        # report_type=半年报 但 period=Q1 → HYM-003
        fm = {
            "report_type": "半年报",
            "financial_period": "2025Q1",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        hym = [f for f in findings if f.rule_id == "HYM-003"]
        assert len(hym) == 1
        assert hym[0].severity == SEVERITY_WARNING

    def test_hym003_period_mismatch_with_annual(self):
        # report_type=中报 但 period=年报 → HYM-003
        fm = {
            "report_type": "中报",
            "financial_period": "2025年报",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        assert any(f.rule_id == "HYM-003" for f in findings)

    def test_hym003_not_triggered_for_generic_financial_analysis(self):
        # report_type=财报分析 是通用类型，不进入 HYM-003
        fm = {
            "report_type": "财报分析",
            "financial_period": "2025Q1",  # Q1 但通用类型 OK
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        assert not any(f.rule_id == "HYM-003" for f in findings)

    def test_hym003_not_triggered_when_period_missing(self):
        # period 缺失由 HYF-001 覆盖；HYM-003 不重复报
        fm = {
            "report_type": "半年报",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        assert not any(f.rule_id == "HYM-003" for f in findings)

    def test_hym003_strict_types_constant(self):
        # 严格类型只有 半年报/中报（财报分析 不算）
        assert STRICT_HALF_YEAR_REPORT_TYPES == ("半年报", "中报")

    def test_hym004_bad_symbol_code(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "symbols": ["bad_code 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        hym = [f for f in findings if f.rule_id == "HYM-004"]
        assert len(hym) == 1
        assert "CODE" in hym[0].message or "code" in hym[0].message.lower()

    def test_hym004_accepts_hk_and_us_contract_symbols(self):
        fm = {
            "report_type": "财报分析",
            "financial_period": "2025H1",
            "symbols": ["0700.HK 腾讯控股", "DELL.US Dell"],
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        assert not any(f.rule_id == "HYM-004" for f in findings)

    def test_hym004_unparseable_symbol(self):
        # symbols 条目无空格分隔 → 解析失败
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "symbols": ["603296.SH华勤技术"],  # 无空格
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        assert any(f.rule_id == "HYM-004" for f in findings)

    def test_hym004_name_mismatch(self):
        # frontmatter name 与 symbols 内 NAME 不一致
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "symbols": ["603296.SH 华勤技术"],
            "name": "其他公司",
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        hym = [f for f in findings if f.rule_id == "HYM-004"]
        assert len(hym) == 1
        assert "name" in hym[0].field or "name" in hym[0].message.lower()

    def test_hym004_name_consistent(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "symbols": ["603296.SH 华勤技术"],
            "name": "华勤技术",
        }
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=True)
        assert not any(f.rule_id == "HYM-004" for f in findings)

    def test_hym004_no_symbols_no_trigger(self):
        # symbols 缺失由 HYF-002 覆盖；HYM-004 不重复报
        fm = {"report_type": "半年报", "financial_period": "2025H1"}
        findings = check_half_year_metadata_sanity_single(fm, has_symbols=False)
        assert not any(f.rule_id == "HYM-004" for f in findings)

    def test_hym007_period_end_date_period_mismatch(self):
        # period=2025H1 期望 period_end_date=2025-06-30，但填 2025-12-31
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-12-31",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        hym = [f for f in findings if f.rule_id == "HYM-007"]
        assert len(hym) == 1
        assert hym[0].severity == SEVERITY_WARNING

    def test_hym007_consistent(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-06-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id == "HYM-007" for f in findings)

    def test_source_report_date_is_not_treated_as_period_end(self):
        fm = {
            "report_type": "财报分析",
            "financial_period": "2025H1",
            "report_date": "2025-08-31",
            "disclosure_date": "2025-08-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id in {"HYM-002", "HYM-007"} for f in findings)

    def test_hym007_rejects_wrong_year_with_same_month_day(self):
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2024-06-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert any(f.rule_id == "HYM-007" for f in findings)

    def test_hym007_q3_period(self):
        # Q3 期望 9-30
        fm = {
            "report_type": "财报分析",
            "financial_period": "2025Q3",
            "period_end_date": "2025-09-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id == "HYM-007" for f in findings)

    def test_hym007_q3_mismatch(self):
        fm = {
            "report_type": "财报分析",
            "financial_period": "2025Q3",
            "period_end_date": "2025-06-30",  # Q3 期望 9-30
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert any(f.rule_id == "HYM-007" for f in findings)

    def test_hym007_skipped_when_period_invalid(self):
        # period 格式非法（如 "2025"）→ HYM-007 不触发（由 HYF-001 覆盖）
        fm = {
            "report_type": "半年报",
            "financial_period": "2025",
            "period_end_date": "2025-06-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id == "HYM-007" for f in findings)


# ── 3. 缺失 vs 非法严格分开 ──────────────────────────────────────────


class TestMissingVsInvalid:
    """任务约束：缺失与非法必须分开。"""

    def test_missing_period_end_date_no_hym002(self):
        # period_end_date 缺失 → HYM-002 跳过（不假定默认值）
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "disclosure_date": "2025-08-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id == "HYM-002" for f in findings)

    def test_missing_disclosure_date_no_hym001(self):
        # disclosure_date 缺失 → HYM-001 跳过
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-06-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id == "HYM-001" for f in findings)

    def test_invalid_date_format_no_hym002(self):
        # period_end_date 非法时 HYM-002 跳过，HYM-007 显式报告非法值。
        fm = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "period_end_date": "2025-13-40",  # 非日历日
            "disclosure_date": "2025-08-30",
            "symbols": ["603296.SH 华勤技术"],
        }
        findings = check_half_year_metadata_sanity_single(
            fm, has_symbols=True, today=_REF_TODAY
        )
        assert not any(f.rule_id == "HYM-002" for f in findings)
        assert any(f.rule_id == "HYM-007" for f in findings)


# ── 4. 跨页规则 HYM-005/006 ──────────────────────────────────────────


class _FakePage:
    """轻量 PageLintResult 替身，供 cross-page 测试直接构造。"""

    def __init__(
        self,
        rel_path: str,
        symbols: List[str],
        period: str,
        is_revision: bool = False,
        revision_fields: List[str] | None = None,
    ):
        self.rel_path = rel_path
        self.is_half_year_report = True
        self.half_year_period = period
        self._hym_symbol_codes = symbols
        self._hym_is_revision = is_revision
        self._hym_revision_fields = revision_fields or []


class TestCrossPageRules:
    def test_no_conflict_single_page(self):
        pages = [
            _FakePage("a.md", ["603296.SH"], "2025H1"),
        ]
        out = check_half_year_metadata_cross_page(pages)
        assert out == {}

    def test_hym005_multi_version_no_revision(self):
        pages = [
            _FakePage("a.md", ["603296.SH"], "2025H1"),
            _FakePage("b.md", ["603296.SH"], "2025H1"),
            _FakePage("c.md", ["603296.SH"], "2025H1"),
        ]
        out = check_half_year_metadata_cross_page(pages)
        # 3 页都加 HYM-005 warning
        assert len(out) == 3
        for rel, findings in out.items():
            assert rel in ("a.md", "b.md", "c.md")
            hym005 = [f for f in findings if f.rule_id == "HYM-005"]
            assert len(hym005) == 1
            assert hym005[0].severity == SEVERITY_WARNING

    def test_hym005_same_period_different_symbol_no_conflict(self):
        # 不同 symbol 不算冲突
        pages = [
            _FakePage("a.md", ["603296.SH"], "2025H1"),
            _FakePage("b.md", ["000001.SZ"], "2025H1"),
        ]
        out = check_half_year_metadata_cross_page(pages)
        assert out == {}

    def test_hym005_same_symbol_different_period_no_conflict(self):
        pages = [
            _FakePage("a.md", ["603296.SH"], "2025H1"),
            _FakePage("b.md", ["603296.SH"], "2024H1"),
        ]
        out = check_half_year_metadata_cross_page(pages)
        assert out == {}

    def test_hym005_normalizes_symbol_and_half_year_period_aliases(self):
        pages = [
            _FakePage("a.md", ["603296"], "2025H1"),
            _FakePage("b.md", ["603296.SH"], "2025中报"),
            _FakePage("c.md", ["603296.SH"], "2025Q2"),
        ]
        out = check_half_year_metadata_cross_page(pages)
        assert set(out) == {"a.md", "b.md", "c.md"}
        assert all(any(f.rule_id == "HYM-005" for f in fs) for fs in out.values())

    def test_hym006_revision_detected_across_aliases(self):
        pages = [
            _FakePage("原稿.md", ["603296"], "2025半年报"),
            _FakePage(
                "修订稿.md",
                ["603296.SH"],
                "2025H1",
                is_revision=True,
                revision_fields=["supersedes"],
            ),
        ]
        out = check_half_year_metadata_cross_page(pages)
        assert "修订稿.md" in out
        assert any(f.rule_id == "HYM-006" for f in out["修订稿.md"])

    def test_hym006_revision_detected(self):
        # 多版本中至少一个带 revision 标记 → HYM-006（仅对修订稿页）
        pages = [
            _FakePage("原稿.md", ["603296.SH"], "2025H1"),
            _FakePage(
                "修订稿.md",
                ["603296.SH"],
                "2025H1",
                is_revision=True,
                revision_fields=["revision"],
            ),
        ]
        out = check_half_year_metadata_cross_page(pages)
        # HYM-006 只加在修订稿页
        assert "修订稿.md" in out
        hym006 = [f for f in out["修订稿.md"] if f.rule_id == "HYM-006"]
        assert len(hym006) == 1
        assert hym006[0].severity == SEVERITY_INFO
        # 原稿页不触发 HYM-005（因为存在修订稿，不再是"全部无标记"）
        assert "原稿.md" not in out or all(
            f.rule_id != "HYM-005" for f in out["原稿.md"]
        )

    def test_hym006_message_keeps_original_path(self):
        # 任务要求：对修订稿保留原始/修订来源路径，不静默覆盖
        pages = [
            _FakePage("原稿.md", ["603296.SH"], "2025H1"),
            _FakePage(
                "修订稿.md",
                ["603296.SH"],
                "2025H1",
                is_revision=True,
                revision_fields=["revision", "supersedes"],
            ),
        ]
        out = check_half_year_metadata_cross_page(pages)
        msg = out["修订稿.md"][0].message + out["修订稿.md"][0].fix_suggestion
        # 修订稿 fix_suggestion 应包含原稿路径（保留来源路径）
        assert "原稿.md" in out["修订稿.md"][0].fix_suggestion

    def test_non_half_year_page_skipped(self):
        # 非半年报页不参与跨页分组
        page = _FakePage("a.md", ["603296.SH"], "2025H1")
        page.is_half_year_report = False
        pages = [page, _FakePage("b.md", ["603296.SH"], "2025H1")]
        out = check_half_year_metadata_cross_page(pages)
        # 只有 b.md 是半年报页，单页 → 无冲突
        assert out == {}

    def test_multi_symbol_page(self):
        # 一页含多 symbol，每个 symbol 独立分组
        pages = [
            _FakePage("a.md", ["603296.SH", "000001.SZ"], "2025H1"),
            _FakePage("b.md", ["603296.SH"], "2025H1"),
        ]
        out = check_half_year_metadata_cross_page(pages)
        # a/b 在 603296.SH 组冲突；000001.SZ 组只有 a
        assert "a.md" in out
        assert "b.md" in out


# ── 5. 集成：lint_single_page ────────────────────────────────────────


class TestLintSinglePageIntegration:
    def test_hym_findings_attached_to_page(self, tmp_path: Path):
        p = tmp_path / "future.md"
        _write(
            p,
            _make_hy_page(
                rel_name="future.md",
                disclosure_date="2099-08-30",  # 未来
                period_end_date="2025-06-30",
            ),
        )
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        assert "HYM-001" in rule_ids

    def test_hym_does_not_break_hyf(self, tmp_path: Path):
        # 同一页同时跑 HYF + HYM + CIT，互不干扰
        p = tmp_path / "mixed.md"
        _write(p, _make_hy_page(rel_name="mixed.md"))
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        # 合法页不应触发任何 HYF/HYM
        assert not any(r.startswith("HYF-") for r in rule_ids)
        assert not any(r.startswith("HYM-") for r in rule_ids)

    def test_hym_error_lowers_readiness(self, tmp_path: Path):
        # HYM-002 是 error，应把 readiness 至少压到 medium
        p = tmp_path / "bad.md"
        _write(
            p,
            _make_hy_page(
                rel_name="bad.md",
                period_end_date="2025-12-31",  # 晚于 disclosure
                disclosure_date="2025-08-30",
            ),
        )
        result = lint_single_page(p.name, p)
        assert "HYM-002" in {f.rule_id for f in result.findings}
        assert result.machine_readiness != "high"

    def test_existing_hy001_fixture_compatible(self, tmp_path: Path):
        # HY-001 fixture 没有 period_end_date，关系校验仍不猜期末日；但未来的实际
        # disclosure_date 应由 HYM-001 明确提示。
        hy001_page = """---
title: test
created: 2026-08-30
updated: 2026-08-30
sources:
  - x
tags: [t]
related: []
symbols: ["603296.SH 华勤技术"]
report_type: 半年报
evidence_level: A
valid_until: 长期
source_quality: 高
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-30
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿
risk_factors: [客户集中度]
---

# t

## 一句话总结

s

## 投资逻辑

- l

## 风险提示

- r
"""
        p = tmp_path / "hy001.md"
        _write(p, hy001_page)
        result = lint_single_page(p.name, p)
        hym_findings = [f for f in result.findings if f.rule_id.startswith("HYM-")]
        assert {f.rule_id for f in hym_findings} == {"HYM-001"}


# ── 6. 集成：lint_local_knowledge 跨页 ────────────────────────────────


class TestLintLocalKnowledgeCrossPage:
    def test_multi_version_conflict_detected(self, fixture_kb_with_conflicts: Path):
        result = lint_local_knowledge(str(fixture_kb_with_conflicts))
        # 2 页同 symbol+period 无修订标记 → HYM-005
        assert len(result.pages_multi_version_conflict) == 2
        by_rule = result.findings_by_rule
        assert by_rule.get("HYM-005") == 2

    def test_revision_detected(self, fixture_kb_with_revision: Path):
        result = lint_local_knowledge(str(fixture_kb_with_revision))
        # 修订稿页触发 HYM-006
        assert len(result.pages_revision_detected) == 1
        assert "修订稿" in result.pages_revision_detected[0]
        # 原稿不触发 HYM-005（因为存在修订稿）
        assert len(result.pages_multi_version_conflict) == 0

    def test_clean_kb_no_hym_conflicts(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        assert result.pages_multi_version_conflict == []
        assert result.pages_revision_detected == []
        assert result.pages_invalid_or_future_disclosure == []
        assert result.pages_period_end_after_disclosure == []
        assert result.pages_period_mismatch == []
        assert result.pages_symbol_name_mismatch == []

    def test_cross_page_findings_affect_readiness(
        self, fixture_kb_with_conflicts: Path
    ):
        # HYM-005 是 warning，2 个 warning 应保持 high（阈值 2）
        # 但 KB 中每页还有其他 warning，所以 readiness 至少不破为 low
        result = lint_local_knowledge(str(fixture_kb_with_conflicts))
        for page in result.page_results:
            assert page.machine_readiness in ("high", "medium")
            # 不应有 error
            assert page.error_count == 0

    def test_to_dict_includes_hym_aggregates(self, fixture_kb_with_conflicts: Path):
        result = lint_local_knowledge(str(fixture_kb_with_conflicts))
        payload = result.to_dict()
        json.dumps(payload, ensure_ascii=False)
        for key in (
            "pages_invalid_or_future_disclosure",
            "pages_period_end_after_disclosure",
            "pages_period_mismatch",
            "pages_symbol_name_mismatch",
            "pages_multi_version_conflict",
            "pages_revision_detected",
            "pages_period_end_mismatch",
        ):
            assert key in payload


# ── 7. 报告渲染 ──────────────────────────────────────────────────────


class TestReportRendering:
    def test_report_includes_hym_when_present(self, fixture_kb_with_conflicts: Path):
        result = lint_local_knowledge(str(fixture_kb_with_conflicts))
        report = render_lint_report(result)
        assert "HY-011" in report
        assert "HYM-005" in report
        assert "half_year_metadata_sanity" in report

    def test_report_includes_hym_when_revision(self, fixture_kb_with_revision: Path):
        result = lint_local_knowledge(str(fixture_kb_with_revision))
        report = render_lint_report(result)
        assert "HYM-006" in report

    def test_report_no_hym_section_when_clean(self, fixture_kb: Path):
        # 合法 KB：没有 HYM finding 时，概览不显示 HYM 行（避免噪声）
        result = lint_local_knowledge(str(fixture_kb))
        report = render_lint_report(result)
        assert "half_year_metadata_sanity" not in report

    def test_report_no_long_original_text(self, fixture_kb_with_conflicts: Path):
        # 任务约束：报告不含正文
        result = lint_local_knowledge(str(fixture_kb_with_conflicts))
        report = render_lint_report(result)
        assert "测试页" not in report  # 正文句子
        assert "营收 100亿" not in report

    def test_rule_descriptions_include_hym(self):
        from tradingagents.dataflows.local_knowledge_lint import _RULE_DESCRIPTIONS

        for rule_id in HYM_RULE_IDS:
            assert rule_id in _RULE_DESCRIPTIONS, (
                f"_RULE_DESCRIPTIONS 缺规则 {rule_id}"
            )
            sev, desc = _RULE_DESCRIPTIONS[rule_id]
            assert sev in (SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO)
            assert desc  # 非空


# ── 8. 只读安全 ──────────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_lint_does_not_modify_kb(self, fixture_kb_with_conflicts: Path):
        def snapshot(root: Path) -> Dict[str, int]:
            snap = {}
            for p in root.rglob("*"):
                if p.is_file():
                    snap[str(p.relative_to(root))] = p.stat().st_size
            return snap

        before = snapshot(fixture_kb_with_conflicts)
        lint_local_knowledge(str(fixture_kb_with_conflicts))
        lint_local_knowledge(str(fixture_kb_with_conflicts))  # 跑两次确保幂等
        after = snapshot(fixture_kb_with_conflicts)
        assert before == after

    def test_lint_idempotent(self, fixture_kb_with_conflicts: Path):
        # 跑两次结果应一致（无累积 finding）
        r1 = lint_local_knowledge(str(fixture_kb_with_conflicts))
        r2 = lint_local_knowledge(str(fixture_kb_with_conflicts))
        assert r1.findings_by_severity == r2.findings_by_severity
        assert r1.findings_by_rule == r2.findings_by_rule
        assert len(r1.pages_multi_version_conflict) == len(
            r2.pages_multi_version_conflict
        )


# ── 9. CLI 子进程冒烟 ────────────────────────────────────────────────


class TestCliSmoke:
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def test_cli_json_includes_hym(self, fixture_kb_with_conflicts: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb_with_conflicts),
                "--json",
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert "pages_multi_version_conflict" in payload
        assert len(payload["pages_multi_version_conflict"]) == 2

    def test_cli_markdown_includes_hym(self, fixture_kb_with_conflicts: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(fixture_kb_with_conflicts),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0
        assert "HY-011" in proc.stdout
        assert "HYM-005" in proc.stdout

    def test_cli_fail_on_error_with_hym002(self, tmp_path: Path):
        # 含 HYM-002（error）的 fixture → --fail-on-error 应返回 2
        inv = tmp_path / INVESTMENT_SUBDIR
        _write(
            inv / "bad.md",
            _make_hy_page(
                rel_name="bad.md",
                period_end_date="2025-12-31",
                disclosure_date="2025-08-30",
            ),
        )
        _write(tmp_path / INDEX_MD, "# Index\n")
        _write(tmp_path / LOG_MD, "# Log\n")
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/lint_local_knowledge.py",
                "--knowledge-root",
                str(tmp_path),
                "--stdout",
                "--fail-on-error",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 2


# ── 10. 修订稿只用于事实索引，不生成交易动作 ─────────────────────────


class TestRevisionDoesNotGenerateAction:
    """任务约束：修订稿优先级只用于事实索引选择，不生成交易动作。"""

    def test_hym006_is_info_severity(self, fixture_kb_with_revision: Path):
        # HYM-006 是 info，不计入 readiness 惩罚，不会降低 page readiness
        result = lint_local_knowledge(str(fixture_kb_with_revision))
        for page in result.page_results:
            if "修订稿" in page.rel_path:
                # HYM-006 是 info，不应改 readiness
                # （但可能有其他 warning，所以只检查 HYM-006 本身不引起 error）
                hym006 = [
                    f for f in page.findings if f.rule_id == "HYM-006"
                ]
                for f in hym006:
                    assert f.severity == SEVERITY_INFO

    def test_revision_message_does_not_contain_action(
        self, fixture_kb_with_revision: Path
    ):
        result = lint_local_knowledge(str(fixture_kb_with_revision))
        for page in result.page_results:
            for f in page.findings:
                if f.rule_id == "HYM-006":
                    # 不应包含买卖建议词
                    text = f.message + f.fix_suggestion
                    for action_word in ("买入", "卖出", "Buy", "Sell", "加仓", "减仓"):
                        assert action_word not in text


# ── 11. 多场景 fixture 覆盖 ───────────────────────────────────────────


class TestScenarioFixtures:
    """任务验收：fixture 覆盖合法、缺失、非法日期、未来日期、错周期、错 symbol、
    修订稿和多版本冲突。"""

    def test_scenario_legal(self, tmp_path: Path):
        p = tmp_path / "legal.md"
        _write(p, _make_hy_page(rel_name="legal.md"))
        result = lint_single_page(p.name, p)
        hym = [f for f in result.findings if f.rule_id.startswith("HYM-")]
        assert hym == []

    def test_scenario_missing_dates(self, tmp_path: Path):
        # 缺 period_end_date / disclosure_date → HYM-001/002/007 全部跳过
        p = tmp_path / "missing.md"
        _write(
            p,
            _make_hy_page(
                rel_name="missing.md",
                period_end_date=None,
                disclosure_date=None,
            ),
        )
        result = lint_single_page(p.name, p)
        hym = [f for f in result.findings if f.rule_id.startswith("HYM-")]
        # 缺日期不应触发 HYM（缺失与非法分开）
        assert hym == []

    def test_scenario_invalid_date_format(self, tmp_path: Path):
        # period_end_date 格式非法 → HYM-002 跳过，HYM-007 报非法日期。
        p = tmp_path / "invalid.md"
        _write(
            p,
            _make_hy_page(
                rel_name="invalid.md",
                period_end_date="2025-13-40",  # 非法日历日
            ),
        )
        result = lint_single_page(p.name, p)
        hym = {f.rule_id for f in result.findings if f.rule_id.startswith("HYM-")}
        # 非法 period_end_date 不触发 HYM-002（无法比较），但不能静默放过。
        assert "HYM-002" not in hym
        assert "HYM-007" in hym

    def test_scenario_future_date(self, tmp_path: Path):
        p = tmp_path / "future.md"
        _write(
            p,
            _make_hy_page(
                rel_name="future.md",
                disclosure_date="2099-08-30",
                period_end_date="2025-06-30",
            ),
        )
        result = lint_single_page(p.name, p)
        assert "HYM-001" in {f.rule_id for f in result.findings}

    def test_scenario_wrong_period(self, tmp_path: Path):
        p = tmp_path / "wrong_period.md"
        _write(
            p,
            _make_hy_page(
                rel_name="wrong_period.md",
                financial_period="2025Q1",
            ),
        )
        result = lint_single_page(p.name, p)
        assert "HYM-003" in {f.rule_id for f in result.findings}

    def test_scenario_wrong_symbol(self, tmp_path: Path):
        p = tmp_path / "wrong_symbol.md"
        _write(
            p,
            _make_hy_page(
                rel_name="wrong_symbol.md",
                symbols=["BAD_CODE 华勤技术"],
            ),
        )
        result = lint_single_page(p.name, p)
        assert "HYM-004" in {f.rule_id for f in result.findings}

    def test_scenario_revision_draft(self, tmp_path: Path):
        p = tmp_path / "revision.md"
        _write(
            p,
            _make_hy_page(
                rel_name="revision.md",
                revision="2",
                supersedes="[[investment/原稿]]",
            ),
        )
        # 单页 lint 不检测修订（修订只在跨页时才有意义）
        result = lint_single_page(p.name, p)
        # 不应报 HYM-006（跨页规则只在 lint_local_knowledge 跑）
        assert "HYM-006" not in {f.rule_id for f in result.findings}

    def test_scenario_multi_version_conflict(self, fixture_kb_with_conflicts: Path):
        result = lint_local_knowledge(str(fixture_kb_with_conflicts))
        assert len(result.pages_multi_version_conflict) >= 2
