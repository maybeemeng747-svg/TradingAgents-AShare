# [HY-001] half_year_contract
"""Tests for 半年报/财报 Tree Work 输出协议扩展与 lint 规则（HY-001）。

覆盖：
  - 触发判定：``report_type ∈ {财报分析, 半年报, 中报}`` 才进入 HYF 扩展
  - financial_period / disclosure_date / source_type 格式校验
  - 7 条 HYF 规则：合格页、缺代码、缺报告期、事实/观点混用、缺风险提示、缺事实、缺披露日
  - 与 KB-002 通用规则并存：财报页同时跑 FMR/SEC + HYF
  - readiness 影响：HYF-001/002 error 至少压到 medium
  - 整库聚合：pages_half_year / pages_half_year_opinion_only 等清单
  - 只读安全 / 契约文档存在 / 模板可解析
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from tradingagents.dataflows.local_knowledge_lint import (
    HALF_YEAR_REPORT_TYPES,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SOURCE_TYPE_FACT_VALUES,
    SOURCE_TYPE_OPINION_VALUES,
    KnowledgeLintResult,
    PageLintResult,
    _check_half_year_report,
    _is_half_year_report,
    _is_valid_disclosure_date,
    _is_valid_financial_period,
    _normalize_source_type_list,
    lint_local_knowledge,
    lint_single_page,
    render_lint_report,
)
from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INVESTMENT_SUBDIR,
    LOG_MD,
)


# ── fixture 文本 ─────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 合格半年报页 — 满足 KB-002 通用契约 + HY-001 全部字段 → 应 high readiness，无 HYF finding。
_FULL_HALF_YEAR_PAGE = """---
title: 华勤技术603296-2025H1半年报
created: 2026-08-30
updated: 2026-08-30
sources:
  - "[[../../raw/2026-08-30-华勤技术-2025H1.md|公司公告-2025H1]]"
tags: [华勤技术, 半年报, 2025H1]
related: [[investment/华勤技术603296-超节点进入出货周期]]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器]
industry_chain_roles: [AI服务器ODM]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
# ── HY-001 半年报扩展字段 ──
financial_period: 2025H1
disclosure_date: 2026-08-30
source_type: [exchange_filing, fact_table, management_commentary]
financial_facts:
  - 营收 150.2亿 (+30.1% YoY)
  - 归母净利 18.5亿 (+45.0% YoY)
  - 毛利率 25.3% (+1.2pp YoY)
segment_facts:
  - AI服务器营收 80亿 (+50%)
management_commentary:
  - 上调全年AI收入指引
forward_guidance:
  - 下半年CapEx同比+50%
risk_factors: [客户集中度, 美元汇率]
source_links:
  - 巨潮资讯 <公告URL>
---

# 华勤技术（603296）— 2025H1 财报

## 一句话总结

2025H1 营收 150.2亿，同比 +30.1%，AI服务器放量。

## 投资逻辑

- AI服务器增长

## 风险提示

- 客户集中度风险

## 原始资料

- 公司公告
"""

# 缺报告期 + 缺 symbols → HYF-001 / HYF-002 双 error
_HY_MISSING_PERIOD_AND_SYMBOL = """---
title: 某公司-缺报告期财报
created: 2026-08-30
updated: 2026-08-30
sources:
  - 某公告
tags: [财报]
related: []
report_type: 财报分析
evidence_level: A
valid_until: 长期
source_quality: 高
stale_risk: 低
financial_facts:
  - 营收 100亿
disclosure_date: 2026-08-30
source_type: [exchange_filing]
risk_factors: [客户集中度]
---

# 某公司财报

## 一句话总结

营收增长。

## 投资逻辑

- 增长

## 风险提示

- 客户集中度
"""

# source_type 全是 broker_report → HYF-007 观点冒充事实 warning
_HY_OPINION_ONLY_PAGE = """---
title: 某券商观点-被误标为半年报
created: 2026-08-30
updated: 2026-08-30
sources:
  - 某券商研报
tags: [财报]
related: []
symbols: ["603296.SH 华勤技术"]
report_type: 半年报
evidence_level: B
valid_until: 长期
source_quality: 中
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-30
source_type: [broker_report, media]
financial_facts:
  - 营收 100亿（券商预测）
risk_factors: [预测偏差]
---

# 某公司半年报（券商预测版）

## 一句话总结

券商预测营收大增。

## 投资逻辑

- 看 AI 拉动

## 风险提示

- 预测偏差
"""

# 缺 financial_facts + risk_factors → HYF-005 / HYF-006 双 warning
_HY_MISSING_FACTS_AND_RISKS = """---
title: 某公司-缺事实字段
created: 2026-08-30
updated: 2026-08-30
sources:
  - 某公告
tags: [财报]
related: []
symbols: ["603296.SH 华勤技术"]
report_type: 中报
evidence_level: A
valid_until: 长期
source_quality: 高
stale_risk: 低
financial_period: 2025中报
disclosure_date: 2026-08-30
source_type: [exchange_filing]
---

# 某公司中报

## 一句话总结

营收增长。

## 投资逻辑

- 增长

## 风险提示

- 行业风险
"""

# 缺 disclosure_date → HYF-003 warning（其他字段齐全）
_HY_MISSING_DISCLOSURE = """---
title: 某公司-缺披露日
created: 2026-08-30
updated: 2026-08-30
sources:
  - 某公告
tags: [财报]
related: []
symbols: ["603296.SH 华勤技术"]
report_type: 财报分析
evidence_level: A
valid_until: 长期
source_quality: 高
stale_risk: 低
financial_period: FY26Q1
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿
risk_factors: [客户集中度]
---

# 某公司财报

## 一句话总结

营收增长。

## 投资逻辑

- 增长

## 风险提示

- 客户集中度
"""

# 财报页缺 source_type 字段 → HYF-004 warning（且不触发 HYF-007）
_HY_MISSING_SOURCE_TYPE = """---
title: 某公司-缺source_type
created: 2026-08-30
updated: 2026-08-30
sources:
  - 某公告
tags: [财报]
related: []
symbols: ["603296.SH 华勤技术"]
report_type: 半年报
evidence_level: A
valid_until: 长期
source_quality: 高
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-30
financial_facts:
  - 营收 100亿
risk_factors: [客户集中度]
---

# 某公司半年报

## 一句话总结

营收增长。

## 投资逻辑

- 增长

## 风险提示

- 客户集中度
"""

# 非财报页（report_type=公司点评）→ 不应触发任何 HYF 规则
_NON_FINANCIAL_PAGE = """---
title: 华勤技术-公司点评
created: 2026-05-25
updated: 2026-06-29
sources:
  - 中邮证券研报
tags: [华勤技术]
related: []
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器]
report_type: 公司点评
evidence_level: A
valid_until: 长期
source_quality: 高
stale_risk: 低
---

# 华勤技术

## 一句话总结

AI服务器增长。

## 投资逻辑

- 增长

## 风险提示

- 估值高
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵含半年报页的 mini Tree Work 知识库（只在 tmp_path 下）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-2025H1半年报.md", _FULL_HALF_YEAR_PAGE)
    _write(inv / "某公司-缺报告期财报.md", _HY_MISSING_PERIOD_AND_SYMBOL)
    _write(inv / "某券商观点-被误标为半年报.md", _HY_OPINION_ONLY_PAGE)
    _write(inv / "某公司-缺事实字段.md", _HY_MISSING_FACTS_AND_RISKS)
    _write(inv / "某公司-缺披露日.md", _HY_MISSING_DISCLOSURE)
    _write(inv / "某公司-缺source_type.md", _HY_MISSING_SOURCE_TYPE)
    _write(inv / "华勤技术-公司点评.md", _NON_FINANCIAL_PAGE)
    _write(
        tmp_path / INDEX_MD,
        "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n\n"
        "- [[investment/华勤技术603296-2025H1半年报]]\n"
        "- [[investment/某公司-缺报告期财报]]\n"
        "- [[investment/某券商观点-被误标为半年报]]\n"
        "- [[investment/某公司-缺事实字段]]\n"
        "- [[investment/某公司-缺披露日]]\n"
        "- [[investment/某公司-缺source_type]]\n"
        "- [[investment/华勤技术-公司点评]]\n",
    )
    _write(tmp_path / LOG_MD, "# Log\n")
    return tmp_path


# ── 1. 触发判定 ──────────────────────────────────────────────────────


class TestHalfYearDetection:
    def test_report_types_set(self):
        # 契约 §10：财报分析 / 半年报 / 中报 三种触发
        assert set(HALF_YEAR_REPORT_TYPES) == {"财报分析", "半年报", "中报"}

    def test_detect_financial_analysis(self):
        assert _is_half_year_report({"report_type": "财报分析"}) is True

    def test_detect_half_year(self):
        assert _is_half_year_report({"report_type": "半年报"}) is True

    def test_detect_interim(self):
        assert _is_half_year_report({"report_type": "中报"}) is True

    def test_non_financial_report_type_not_triggered(self):
        assert _is_half_year_report({"report_type": "公司点评"}) is False
        assert _is_half_year_report({"report_type": "行业"}) is False
        assert _is_half_year_report({"report_type": "数据表"}) is False

    def test_missing_report_type_not_triggered(self):
        assert _is_half_year_report({}) is False
        assert _is_half_year_report({"report_type": None}) is False
        assert _is_half_year_report({"report_type": ""}) is False


# ── 2. 字段格式校验 ──────────────────────────────────────────────────


class TestFieldValidation:
    @pytest.mark.parametrize(
        "period",
        [
            "2025H1",
            "2025H2",
            "2025中报",
            "2025半年报",
            "2025年报",
            "2025一季报",
            "2025三季报",
            "2025Q1",
            "2025Q4",
            "FY26Q1",
            "fy26q1",  # 大小写不敏感
            "FY2026H1",
        ],
    )
    def test_valid_financial_periods(self, period):
        assert _is_valid_financial_period(period) is True

    @pytest.mark.parametrize(
        "period",
        [
            None,
            "",
            "2025",
            "上半年",
            "最新",
            "H1",
            "2025H3",  # 非法期数
            "2025Q5",
            "2025-06",  # 不是日期型 period
            "2025年中",
            "abcd",
        ],
    )
    def test_invalid_financial_periods(self, period):
        assert _is_valid_financial_period(period) is False

    @pytest.mark.parametrize(
        "date_str",
        ["2026-08-30", "2026/08/30", "20260830"],
    )
    def test_valid_disclosure_date(self, date_str):
        assert _is_valid_disclosure_date(date_str) is True

    @pytest.mark.parametrize(
        "date_str",
        [None, "", "2026-8-30", "2026/8/30", "2026083", "2026083011", "abc"],
    )
    def test_invalid_disclosure_date(self, date_str):
        # 注：契约 §10.1 说明 disclosure_date 不校验真实日历日（避免 2/30 边界把整页 lint 打挂）。
        # 因此 2026-13-01 在格式上视为合法；本测试只覆盖格式不合法的情况。
        assert _is_valid_disclosure_date(date_str) is False

    def test_normalize_source_type_list(self):
        assert _normalize_source_type_list(None) == []
        assert _normalize_source_type_list("") == []
        assert _normalize_source_type_list([]) == []
        # 字符串拆分
        assert _normalize_source_type_list("exchange_filing,fact_table") == [
            "exchange_filing",
            "fact_table",
        ]
        # list 归一为小写
        assert _normalize_source_type_list(["Exchange_Filing", "BROKER_REPORT"]) == [
            "exchange_filing",
            "broker_report",
        ]

    def test_source_type_classification(self):
        # 事实类与观点类互不重叠
        assert set(SOURCE_TYPE_FACT_VALUES).isdisjoint(SOURCE_TYPE_OPINION_VALUES)
        # management_commentary 属于事实类（公司自述可进入反证，但需打标）
        assert "management_commentary" in SOURCE_TYPE_FACT_VALUES
        assert "exchange_filing" in SOURCE_TYPE_FACT_VALUES
        assert "fact_table" in SOURCE_TYPE_FACT_VALUES
        assert "broker_report" in SOURCE_TYPE_OPINION_VALUES
        assert "media" in SOURCE_TYPE_OPINION_VALUES


# ── 3. 单页 HYF 规则 ────────────────────────────────────────────────


class TestHalfYearPageRules:
    def test_full_half_year_page_passes_hyf(self, tmp_path: Path):
        p = tmp_path / "华勤技术603296-2025H1半年报.md"
        _write(p, _FULL_HALF_YEAR_PAGE)
        result = lint_single_page(p.name, p)
        assert result.is_half_year_report is True
        assert result.half_year_period == "2025H1"
        assert result.half_year_opinion_only is False
        # 不应有任何 HYF finding
        hyf_findings = [f for f in result.findings if f.rule_id.startswith("HYF-")]
        assert hyf_findings == [], (
            f"满分半年报页不应触发 HYF 规则，实际命中: {[(f.rule_id, f.message) for f in hyf_findings]}"
        )

    def test_missing_period_and_symbol_triggers_hyf001_hyf002(self, tmp_path: Path):
        p = tmp_path / "缺报告期和代码.md"
        _write(p, _HY_MISSING_PERIOD_AND_SYMBOL)
        result = lint_single_page(p.name, p)
        assert result.is_half_year_report is True
        rule_ids = {f.rule_id for f in result.findings}
        assert "HYF-001" in rule_ids  # 缺 financial_period
        assert "HYF-002" in rule_ids  # 缺 symbols
        # 两条都是 error
        for f in result.findings:
            if f.rule_id in ("HYF-001", "HYF-002"):
                assert f.severity == SEVERITY_ERROR
        assert result.half_year_period is None
        # 双 error 应阻止 high
        assert result.machine_readiness != "high"

    def test_opinion_only_triggers_hyf007(self, tmp_path: Path):
        p = tmp_path / "观点冒充.md"
        _write(p, _HY_OPINION_ONLY_PAGE)
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        assert "HYF-007" in rule_ids
        assert result.half_year_opinion_only is True
        # HYF-007 是 warning
        hyf007 = next(f for f in result.findings if f.rule_id == "HYF-007")
        assert hyf007.severity == SEVERITY_WARNING
        assert "exchange_filing" in hyf007.fix_suggestion

    def test_missing_facts_and_risk_factors_triggers_hyf005_hyf006(self, tmp_path: Path):
        p = tmp_path / "缺事实.md"
        _write(p, _HY_MISSING_FACTS_AND_RISKS)
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        assert "HYF-005" in rule_ids  # 缺 financial_facts
        assert "HYF-006" in rule_ids  # 缺 risk_factors
        # 注意 HYF-006（结构化风险字段）与 SEC-002（正文 ## 风险提示）互补：
        # 该页有 ## 风险提示，不应触发 SEC-002，但仍触发 HYF-006
        assert "SEC-002" not in rule_ids

    def test_missing_disclosure_date_triggers_hyf003(self, tmp_path: Path):
        p = tmp_path / "缺披露日.md"
        _write(p, _HY_MISSING_DISCLOSURE)
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        assert "HYF-003" in rule_ids
        hyf003 = next(f for f in result.findings if f.rule_id == "HYF-003")
        assert hyf003.severity == SEVERITY_WARNING

    def test_missing_source_type_triggers_hyf004_not_hyf007(self, tmp_path: Path):
        # source_type 完全缺失 → HYF-004；不触发 HYF-007（不是观点冒充，是没标）
        p = tmp_path / "缺source_type.md"
        _write(p, _HY_MISSING_SOURCE_TYPE)
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        assert "HYF-004" in rule_ids
        assert "HYF-007" not in rule_ids
        assert result.half_year_opinion_only is False

    def test_non_financial_page_no_hyf(self, tmp_path: Path):
        # 公司点评页不应触发任何 HYF 规则
        p = tmp_path / "公司点评.md"
        _write(p, _NON_FINANCIAL_PAGE)
        result = lint_single_page(p.name, p)
        assert result.is_half_year_report is False
        assert result.half_year_period is None
        assert result.half_year_opinion_only is False
        hyf_findings = [f for f in result.findings if f.rule_id.startswith("HYF-")]
        assert hyf_findings == []

    def test_invalid_period_format_triggers_hyf001(self, tmp_path: Path):
        # period 格式非法（如 "2025"）也应触发 HYF-001
        page = _FULL_HALF_YEAR_PAGE.replace(
            "financial_period: 2025H1", "financial_period: 2025"
        )
        p = tmp_path / "period格式非法.md"
        _write(p, page)
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        assert "HYF-001" in rule_ids
        # 非法格式 → period 被置 None
        assert result.half_year_period is None

    def test_hyf_rules_run_alongside_kb002(self, tmp_path: Path):
        # 财报页同时跑通用规则（FMR/SEC）和 HYF 规则
        p = tmp_path / "合格半年报.md"
        _write(p, _FULL_HALF_YEAR_PAGE)
        result = lint_single_page(p.name, p)
        rule_ids = {f.rule_id for f in result.findings}
        # 不应触发 SEC-001/SEC-002（页面已有这些章节）
        assert "SEC-001" not in rule_ids
        assert "SEC-002" not in rule_ids
        # 也不应触发 HYF
        assert not any(r.startswith("HYF-") for r in rule_ids)


# ── 4. helper _check_half_year_report 直接调用 ──────────────────────


class TestHalfYearCheckHelper:
    def test_non_half_year_returns_false_no_findings(self):
        findings: List = []
        out = _check_half_year_report({"report_type": "公司点评"}, True, findings)
        assert out == (False, None, False)
        assert findings == []

    def test_half_year_full_no_findings(self):
        findings: List = []
        frontmatter = {
            "report_type": "半年报",
            "financial_period": "2025H1",
            "symbols": ["603296.SH 华勤技术"],
            "disclosure_date": "2026-08-30",
            "source_type": ["exchange_filing"],
            "financial_facts": ["营收 100亿"],
            "risk_factors": ["客户集中度"],
        }
        out = _check_half_year_report(frontmatter, True, findings)
        assert out == (True, "2025H1", False)
        assert findings == []

    def test_opinion_only_detected(self):
        findings: List = []
        frontmatter = {
            "report_type": "中报",
            "financial_period": "2025中报",
            "symbols": ["603296.SH 华勤技术"],
            "disclosure_date": "2026-08-30",
            "source_type": ["broker_report", "media"],
            "financial_facts": ["营收 100亿"],
            "risk_factors": ["客户集中度"],
        }
        out = _check_half_year_report(frontmatter, True, findings)
        assert out == (True, "2025中报", True)
        rule_ids = {f.rule_id for f in findings}
        assert "HYF-007" in rule_ids


# ── 5. 整库聚合 ──────────────────────────────────────────────────────


class TestKnowledgeLintHalfYearAggregation:
    def test_half_year_pages_aggregated(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        assert len(result.pages_half_year) == 6  # 6 个财报页（公司点评不算）
        assert result.page_count == 7

    def test_period_missing_aggregated(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        assert any("缺报告期财报" in p for p in result.pages_half_year_period_missing)
        # 缺 period 的页面是 1 个（HY_MISSING_PERIOD_AND_SYMBOL）
        assert len(result.pages_half_year_period_missing) >= 1

    def test_opinion_only_aggregated(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        assert any("券商观点" in p for p in result.pages_half_year_opinion_only)

    def test_facts_missing_aggregated(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        assert any("缺事实字段" in p for p in result.pages_half_year_facts_missing)

    def test_findings_by_rule_includes_hyf(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        by_rule = result.findings_by_rule
        # 至少命中 HYF-001~007 中的多条
        hyf_rules_hit = {r for r in by_rule if r.startswith("HYF-")}
        assert "HYF-001" in hyf_rules_hit
        assert "HYF-007" in hyf_rules_hit
        assert "HYF-005" in hyf_rules_hit

    def test_to_dict_serializable_with_hy(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        payload = result.to_dict()
        # 必须可 JSON 序列化
        json.dumps(payload, ensure_ascii=False)
        # HY 字段存在
        assert "pages_half_year" in payload
        assert "pages_half_year_opinion_only" in payload
        # 单页 to_dict 也含 HY 字段
        assert all(
            "is_half_year_report" in p for p in payload["page_results"]
        )

    def test_full_half_year_page_gets_high_readiness(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        full_page = next(
            p for p in result.page_results if "华勤技术603296-2025H1半年报" in p.rel_path
        )
        assert full_page.machine_readiness == "high"
        assert full_page.is_half_year_report is True


# ── 6. 报告渲染 ──────────────────────────────────────────────────────


class TestReportRenderHalfYear:
    def test_report_contains_hyf_section(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        report = render_lint_report(result)
        # 报告头注脚含 HY-001
        assert "HY-001" in report
        # 财报页缺口清单标题
        assert "HYF-001" in report
        assert "HYF-007" in report
        # 概览含 half_year_pages
        assert "half_year_pages" in report

    def test_report_no_long_original_text(self, fixture_kb: Path):
        # 不应包含正文段落
        result = lint_local_knowledge(str(fixture_kb))
        report = render_lint_report(result)
        assert "营收 150.2亿，同比 +30.1%" not in report  # 正文句子
        assert "AI服务器放量" not in report


# ── 7. 只读安全 ──────────────────────────────────────────────────────


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


# ── 8. 契约文档存在且含半年报扩展 ────────────────────────────────────


class TestContractDocHalfYear:
    def _doc_text(self) -> str:
        path = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "local_knowledge_contract.md"
        )
        assert path.exists(), "docs/local_knowledge_contract.md 必须存在"
        return path.read_text(encoding="utf-8")

    def test_doc_mentions_hy001(self):
        text = self._doc_text()
        assert "HY-001" in text
        assert "半年报" in text

    def test_doc_mentions_all_hyf_fields(self):
        text = self._doc_text()
        # 9 个新字段都应在文档里
        for field in (
            "financial_period",
            "disclosure_date",
            "source_type",
            "financial_facts",
            "segment_facts",
            "management_commentary",
            "forward_guidance",
            "risk_factors",
            "source_links",
        ):
            assert field in text, f"契约文档未提及字段 {field}"

    def test_doc_mentions_all_hyf_rules(self):
        text = self._doc_text()
        for rule_id in (
            "HYF-001",
            "HYF-002",
            "HYF-003",
            "HYF-004",
            "HYF-005",
            "HYF-006",
            "HYF-007",
        ):
            assert rule_id in text, f"契约文档未提及规则 {rule_id}"

    def test_doc_has_ingest_template(self):
        text = self._doc_text()
        # 模板段必须包含示例 frontmatter 字段
        assert "financial_period: 2025H1" in text
        assert "source_type: [exchange_filing" in text
        assert "risk_factors: [客户集中度" in text

    def test_doc_has_source_type_value_table(self):
        text = self._doc_text()
        for value in ("exchange_filing", "fact_table", "broker_report", "media"):
            assert value in text


# ── 9. CLI 子进程冒烟（HYF 规则在真实 lint CLI 下生效） ──────────────


class TestCliHalfYearSmoke:
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def test_cli_json_includes_hyf(self, fixture_kb: Path):
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
        assert len(payload["pages_half_year"]) == 6
        assert "pages_half_year_opinion_only" in payload

    def test_cli_markdown_contains_hyf(self, fixture_kb: Path):
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
        assert "HY-001" in proc.stdout
        assert "HYF-001" in proc.stdout
        assert "half_year_pages" in proc.stdout

    def test_cli_fail_on_error_returns_nonzero(self, fixture_kb: Path):
        # fixture 含 HYF-001/002 error
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
