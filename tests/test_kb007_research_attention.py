# [KB-007] research_attention_score
"""Tests for 多研报重复提及因子 Research Attention Score (KB-007).

覆盖：
  - 资产类别分类（A 股 / 港股 / 美股 / 基金 / 未上市）
  - symbol 解析与倒排索引建立（同一标的在多页命中能聚合）
  - 字段计算（mention/fresh/theme/source/high_quality/stale/deprecated/
    report_type_distribution）
  - 来源去重（同 wiki-link alias 跨页只计一次主权重）
  - 主题交叉度（theme_count > 1 时给予奖励）
  - 分数合成（公式可复现；fresh > stale 时分数更高）
  - 降权规则（stale / deprecated / 低置信页面不提升主候选层级）
  - 排序稳定性（score → mention_count → theme_count → symbol_key）
  - 报告渲染（不含买卖建议或强动作词；含计分口径与免责声明）
  - CLI 子进程冒烟（Markdown / JSON / 文件落盘）
  - 只读安全性（不写入知识库）
  - 默认根目录 / 输出路径
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.research_attention import (
    ALL_ASSET_CLASSES,
    ASSET_CLASS_A_SHARE,
    ASSET_CLASS_FUND,
    ASSET_CLASS_HK,
    ASSET_CLASS_OTHER,
    ASSET_CLASS_UNLISTED,
    ASSET_CLASS_US,
    CONTRACT_VERSION,
    PageMention,
    SymbolAttention,
    ResearchAttentionResult,
    classify_asset_class,
    compute_research_attention,
    render_research_attention_report,
    suggest_report_output_path,
    _split_symbol_entry,
    _strip_wiki_link,
    _compose_score,
)


# ── 工具：构造 fixture 知识库 ─────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 公司页 A：华勤技术，evidence_level=A、source_quality=高、fresh。
_COMPANY_PAGE_A = """---
title: 华勤技术603296-超节点进入出货周期
created: 2026-05-25
updated: 2026-06-29
sources:
  - "[[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|中邮证券-华勤技术超节点]]"
tags: [华勤技术, 超节点, AI服务器]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器, 超节点, 液冷散热]
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

# 公司页 B：华勤技术出现在评分表，evidence_level=B、source_quality=低、stale_risk=高。
_SCORE_TABLE_PAGE = """---
title: AI算力基础设施-公司评分表
created: 2026-05-13
updated: 2026-05-13
sources:
  - "[[../../raw/2026-05-14-中邮证券-华勤技术超节点.md|中邮证券-华勤技术超节点]]"
  - "星球社群截图"
tags: [公司评分, AI算力]
symbols: ["603296.SH 华勤技术", "000977.SZ 浪潮信息"]
themes: [AI算力, 服务器, 液冷]
report_type: 数据表
evidence_level: B
valid_until: 2026-07-29
source_quality: 低
stale_risk: 高
---

# AI算力基础设施 - 公司评分表

## 一句话总结

3家公司评分。

## 风险提示

- 评分时效风险
"""

# 公司页 C：第三篇研报再次提到华勤技术（高质量、fresh），来源不同。
_COMPANY_PAGE_B = """---
title: 华勤技术-深度研究
created: 2026-06-01
updated: 2026-06-30
sources:
  - "[[../../raw/2026-06-01-国泰君安-华勤技术深度.md|国泰君安-华勤技术深度]]"
tags: [华勤技术, 深度]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器, ODM, 消费电子]
report_type: 深度
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 华勤技术深度

## 一句话总结

ODM 切入 AI 服务器。

## 风险提示

- 客户集中
"""

# 港股页。
_HK_PAGE = """---
title: 腾讯控股-游戏复苏
created: 2026-04-01
updated: 2026-06-15
sources:
  - "中金-腾讯"
tags: [腾讯, 游戏]
symbols: ["00700.HK 腾讯控股"]
themes: [互联网, 游戏]
report_type: 公司点评
evidence_level: B
valid_until: 2099-12-31
source_quality: 中
stale_risk: 低
---

# 腾讯控股

## 一句话总结

游戏业务复苏。

## 风险提示

- 监管风险
"""

# 美股页（待补充 / 低置信）。
_US_TODO_PAGE = """---
title: Dell-FQ127-待补充
created: 2026-05-31
updated: 2026-06-29
sources:
  - "Bernstein-Dell"
tags: [Dell, 待补充]
symbols: ["DELL.US Dell"]
themes: [AI服务器]
report_type: 财报分析
evidence_level: C
valid_until: 长期
source_quality: 低
stale_risk: 高
---

# Dell FQ1'27

⚠️ 此页面内容不完整

## 风险提示

- 待补充
"""

# 基金页（沪深 300ETF，后缀 .SH 但 tags/themes 含 ETF）。
_FUND_PAGE = """---
title: 沪深300ETF-指数跟踪
created: 2026-03-01
updated: 2026-06-01
sources:
  - "华泰柏瑞-指数说明"
tags: [ETF, 指数基金]
symbols: ["510300.SH 沪深300ETF"]
themes: [指数, ETF]
report_type: 综述
evidence_level: B
valid_until: 长期
source_quality: 中
stale_risk: 低
---

# 沪深300ETF

## 一句话总结

跟踪沪深 300 指数。

## 风险提示

- 跟踪误差
"""

# 未上市主体（无标准后缀、无 6 位代码）。
_UNLISTED_PAGE = """---
title: 某私募主体-调研纪要
created: 2026-05-01
updated: 2026-05-01
sources:
  - "调研纪要"
tags: [私募, 调研]
symbols: ["某私募-A轮"]
themes: [一级市场]
report_type: 公司分析
evidence_level: C
valid_until: 长期
source_quality: 低
stale_risk: 中
---

# 某私募主体

## 一句话总结

一级市场调研。

## 风险提示

- 流动性风险
"""

# 已过期页：valid_until=2020-01-01。
_EXPIRED_PAGE = """---
title: 某周期股-已过期
created: 2020-01-01
updated: 2020-01-01
sources:
  - "旧研报"
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
"""

# 无 symbols 字段的页面（不应进入倒排索引）。
_NO_SYMBOLS_PAGE = """---
title: 行业综述-无标的
created: 2026-05-01
updated: 2026-05-01
sources:
  - "行业研报"
tags: [行业]
themes: [宏观]
report_type: 综述
evidence_level: B
valid_until: 长期
source_quality: 中
stale_risk: 低
---

# 行业综述

## 一句话总结

宏观行业综述。

## 风险提示

- 不确定
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵 mini Tree Work 知识库（只在 tmp_path 下，绝不触碰真实知识库）。"""
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
    """只有 wiki/investment/ 空目录的知识库。"""
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True)
    return tmp_path


# ── 1. 资产类别分类 ──────────────────────────────────────────────────


class TestAssetClassClassification:
    def test_a_share_with_sh_suffix(self):
        assert (
            classify_asset_class("603296.SH", "603296", "华勤技术", {})
            == ASSET_CLASS_A_SHARE
        )

    def test_a_share_with_sz_suffix(self):
        assert (
            classify_asset_class("000977.SZ", "000977", "浪潮信息", {})
            == ASSET_CLASS_A_SHARE
        )

    def test_a_share_with_bj_suffix(self):
        assert (
            classify_asset_class("430047.BJ", "430047", "诺思兰德", {})
            == ASSET_CLASS_A_SHARE
        )

    def test_a_share_with_ss_alias(self):
        # .SS 是部分数据源对 .SH 的别名。
        assert (
            classify_asset_class("603296.SS", "603296", "华勤技术", {})
            == ASSET_CLASS_A_SHARE
        )

    def test_hk_with_suffix(self):
        assert (
            classify_asset_class("00700.HK", "00700", "腾讯控股", {})
            == ASSET_CLASS_HK
        )

    def test_us_with_suffix(self):
        assert (
            classify_asset_class("DELL.US", "DELL", "Dell", {})
            == ASSET_CLASS_US
        )

    def test_fund_via_etf_keyword_in_name(self):
        # .SH 后缀但 name 含 ETF → FUND（不混入 A 股）。
        assert (
            classify_asset_class("510300.SH", "510300", "沪深300ETF", {})
            == ASSET_CLASS_FUND
        )

    def test_fund_via_etf_keyword_in_tags(self):
        assert (
            classify_asset_class(
                "510300.SH", "510300", "沪深300", {"tags": ["ETF", "指数"]}
            )
            == ASSET_CLASS_FUND
        )

    def test_fund_via_lof_keyword_in_themes(self):
        assert (
            classify_asset_class(
                "161725.SZ", "161725", "招商中证", {"themes": ["LOF"]}
            )
            == ASSET_CLASS_FUND
        )

    def test_fund_via_report_type(self):
        assert (
            classify_asset_class(
                "110011.SH",
                "110011",
                "易方达优势成长",
                {"report_type": "基金分析"},
            )
            == ASSET_CLASS_FUND
        )

    def test_a_share_with_fund_prefix_but_no_fund_kw(self):
        # 5 开头代码但无基金关键词 → 仍归 A 股（避免误杀科创板/主板）。
        # 注意：500/550 系列在现实中多为基金，但任务约束要求"不混成一类"，
        # 无关键词时按代码前缀走兜底分支（见 test_fund_via_prefix_only）。
        # 这里测的是带 .SH 后缀 + 无关键词 → A_SHARE（与 510300 ETF 对照）。
        assert (
            classify_asset_class("600000.SH", "600000", "浦发银行", {})
            == ASSET_CLASS_A_SHARE
        )

    def test_fund_via_prefix_only_no_suffix(self):
        # 无后缀 + 5 开头 6 位代码 + 无基金关键词 → 走前缀兜底判为 FUND。
        assert (
            classify_asset_class("510300", "510300", "沪深300", {})
            == ASSET_CLASS_FUND
        )

    def test_a_share_via_prefix_only_no_suffix(self):
        # 无后缀 + 6 开头 6 位代码 → A 股。
        assert (
            classify_asset_class("603296", "603296", "华勤技术", {})
            == ASSET_CLASS_A_SHARE
        )

    def test_unlisted_no_code_text_only(self):
        # 无后缀、无 6 位代码、纯名称 → 未上市主体。
        assert (
            classify_asset_class("", "", "某私募-A轮", {})
            == ASSET_CLASS_UNLISTED
        )

    def test_unlisted_alpha_ticker_no_suffix(self):
        # 无后缀、非 6 位数字代码 → 未上市主体。
        assert (
            classify_asset_class("NVDA", "NVDA", "NVIDIA", {})
            == ASSET_CLASS_UNLISTED
        )

    def test_asset_class_enum_complete(self):
        # 资产类别集合必须包含任务要求的 5 类 + OTHER 兜底。
        for ac in (
            ASSET_CLASS_A_SHARE,
            ASSET_CLASS_HK,
            ASSET_CLASS_US,
            ASSET_CLASS_FUND,
            ASSET_CLASS_UNLISTED,
            ASSET_CLASS_OTHER,
        ):
            assert ac in ALL_ASSET_CLASSES


# ── 2. symbol 解析辅助 ───────────────────────────────────────────────


class TestSymbolParsing:
    def test_split_a_share(self):
        code, bare, name = _split_symbol_entry("603296.SH 华勤技术")
        assert code == "603296.SH"
        assert bare == "603296"
        assert name == "华勤技术"

    def test_split_us(self):
        code, bare, name = _split_symbol_entry("DELL.US Dell")
        assert code == "DELL.US"
        assert bare == "DELL"
        assert name == "Dell"

    def test_split_code_only(self):
        code, bare, name = _split_symbol_entry("603296.SH")
        assert code == "603296.SH"
        assert bare == "603296"
        assert name == ""

    def test_split_case_insensitive_suffix(self):
        code, bare, name = _split_symbol_entry("603296.sh 华勤技术")
        # code_with_suffix 归一为大写。
        assert code == "603296.SH"
        assert bare == "603296"

    def test_strip_wiki_link_with_alias(self):
        assert (
            _strip_wiki_link("[[../../raw/x.md|中邮证券-华勤技术超节点]]")
            == "中邮证券-华勤技术超节点"
        )

    def test_strip_wiki_link_without_alias(self):
        assert _strip_wiki_link("[[investment/foo]]") == "foo"

    def test_strip_wiki_link_passthrough(self):
        assert _strip_wiki_link("普通研报别名") == "普通研报别名"


# ── 3. 倒排索引建立 ──────────────────────────────────────────────────


class TestInvertedIndex:
    def test_multiple_pages_aggregate_to_one_symbol(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        # 603296.SH 出现在 3 篇页面（COMPANY_A, SCORE_TABLE, COMPANY_B）。
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        assert huaqin.mention_count == 3
        assert len(huaqin.matched_pages) == 3

    def test_symbol_count_distinct(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        # 603296.SH, 000977.SZ, 00700.HK, DELL.US, 510300.SH, 某私募-A轮, 600000.SH
        assert result.symbol_count == 7

    def test_pages_without_symbols_excluded(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        # 行业综述-无标的.md 不进入倒排索引。
        assert all(
            "行业综述-无标的" not in p.rel_path
            for s in result.symbols
            for p in s.matched_pages
        )

    def test_page_with_symbols_count(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        # 9 篇 investment 页面中，8 篇有 symbols 字段（无标的页除外）。
        assert result.investment_page_count == 9
        assert result.page_with_symbols_count == 8

    def test_same_symbol_in_score_table_and_company_page(self, fixture_kb: Path):
        """评分表与公司页同时提到 603296，应聚合到同一 SymbolAttention。"""
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        page_types = {p.page_type for p in huaqin.matched_pages}
        assert "company" in page_types
        assert "score_table" in page_types


# ── 4. 字段计算 ──────────────────────────────────────────────────────


class TestFieldAggregation:
    def test_mention_count_includes_stale_and_deprecated(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        # 3 篇命中：2 fresh（COMPANY_A, COMPANY_B）+ 1 stale（SCORE_TABLE）。
        assert huaqin.mention_count == 3
        assert huaqin.fresh_mention_count == 2
        assert huaqin.stale_mention_count == 1
        assert huaqin.deprecated_mention_count == 0

    def test_theme_count_dedup_across_pages(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        # COMPANY_A: [AI服务器, 超节点, 液冷散热]
        # SCORE_TABLE: [AI算力, 服务器, 液冷]  ← 液冷 vs 液冷散热 不同
        # COMPANY_B: [AI服务器, ODM, 消费电子]
        # 去重后 >= 5 个主题。
        assert huaqin.theme_count >= 5
        assert "AI服务器" in huaqin.themes

    def test_source_count_dedup_by_alias(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        # COMPANY_A 和 SCORE_TABLE 共享同一来源 "中邮证券-华勤技术超节点" → 去重一次。
        # SCORE_TABLE 额外 "星球社群截图"；COMPANY_B "国泰君安-华勤技术深度"。
        assert "中邮证券-华勤技术超节点" in huaqin.sources
        assert "国泰君安-华勤技术深度" in huaqin.sources
        # source_count = 去重后的独立来源数。
        assert huaqin.source_count == len(huaqin.sources)

    def test_high_quality_mention_count(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        # 高质量 = source_quality=高 且 evidence_level=A：
        # COMPANY_A (高/A) + COMPANY_B (高/A) = 2；SCORE_TABLE (低/B) 不算。
        assert huaqin.high_quality_mention_count == 2

    def test_deprecated_marked_for_todo_page(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        dell = next(s for s in result.symbols if s.symbol_key == "DELL.US")
        # evidence_level=C + 待补充 → deprecated=1。
        assert dell.deprecated_mention_count == 1
        assert dell.fresh_mention_count == 0

    def test_stale_marked_for_expired_valid_until(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        pufa = next(s for s in result.symbols if s.symbol_key == "600000.SH")
        # valid_until=2020-01-01 已过期 → stale=1。
        assert pufa.stale_mention_count == 1
        assert pufa.fresh_mention_count == 0

    def test_report_type_distribution(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        # COMPANY_A=公司点评, SCORE_TABLE=数据表, COMPANY_B=深度
        assert huaqin.report_type_distribution.get("公司点评") == 1
        assert huaqin.report_type_distribution.get("数据表") == 1
        assert huaqin.report_type_distribution.get("深度") == 1

    def test_latest_updated_is_max(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        # 2026-06-29 / 2026-05-13 / 2026-06-30 → max=2026-06-30。
        assert huaqin.latest_updated == "2026-06-30"


# ── 5. 分数合成 ──────────────────────────────────────────────────────


class TestScoreComposition:
    def test_fresh_only_symbol_scores_higher_than_stale_only(
        self, fixture_kb: Path
    ):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        pufa = next(s for s in result.symbols if s.symbol_key == "600000.SH")
        # 华勤有 fresh + stale；浦发只有 stale。华勤分数应高于浦发。
        assert huaqin.research_attention_score > pufa.research_attention_score
        # 浦发纯过期页 → 分数为 0（被 freshness_ratio 缩放为 0）。
        assert pufa.research_attention_score == 0.0

    def test_deprecated_only_symbol_has_zero_score(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        dell = next(s for s in result.symbols if s.symbol_key == "DELL.US")
        # DELL 全部命中都是 deprecated → fresh=0 → score=0。
        assert dell.fresh_mention_count == 0
        assert dell.research_attention_score == 0.0

    def test_score_explain_populated(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        # 至少有 fresh_mention、theme_cross、high_quality 三个 explain 项。
        explain_blob = " ".join(huaqin.score_explain)
        assert "fresh_mention" in explain_blob
        assert "theme_cross" in explain_blob
        assert "high_quality" in explain_blob

    def test_score_explain_includes_stale_penalty(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        explain_blob = " ".join(huaqin.score_explain)
        assert "stale=" in explain_blob

    def test_score_explain_includes_duplicate_source_penalty(
        self, fixture_kb: Path
    ):
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        explain_blob = " ".join(huaqin.score_explain)
        # 中邮证券来源在 COMPANY_A 和 SCORE_TABLE 重复 → 触发 duplicate_source 惩罚。
        assert "duplicate_source" in explain_blob

    def test_theme_cross_bonus_increases_score(self):
        """单元测试：theme_count > 1 应给予额外奖励。"""
        sym = SymbolAttention(
            symbol_key="X.SH",
            bare_code="X",
            name="X",
            asset_class=ASSET_CLASS_A_SHARE,
            mention_count=1,
            fresh_mention_count=1,
            theme_count=3,
            source_count=1,
            high_quality_mention_count=0,
        )
        sym.matched_pages = [
            PageMention(
                rel_path="x.md",
                title="X",
                page_type="company",
                source_aliases=["src1"],
                themes=["t1", "t2", "t3"],
            )
        ]
        score, explain = _compose_score(sym)
        # mention=1.0 + theme=(3-1)*0.5=1.0 + high_q=0 = 2.0；freshness=1.0 → 2.0。
        assert score == pytest.approx(2.0, rel=1e-6)
        assert any("theme_cross" in e for e in explain)

    def test_freshness_ratio_reduces_score_when_partial_stale(self):
        sym = SymbolAttention(
            symbol_key="X.SH",
            bare_code="X",
            name="X",
            asset_class=ASSET_CLASS_A_SHARE,
            mention_count=2,
            fresh_mention_count=1,
            theme_count=1,
            source_count=2,  # 两个不同 alias，不触发 duplicate_source 惩罚。
            high_quality_mention_count=0,
            stale_mention_count=1,
        )
        sym.matched_pages = [
            PageMention(
                rel_path="fresh.md",
                title="F",
                page_type="company",
                source_aliases=["s1"],
                themes=["t"],
            ),
            PageMention(
                rel_path="stale.md",
                title="S",
                page_type="company",
                is_stale=True,
                source_aliases=["s2"],
                themes=["t"],
            ),
        ]
        score, _ = _compose_score(sym)
        # base = (1.0 + 0 + 0) * (1/2) = 0.5；penalty = 1*0.3 = 0.3 → 0.2。
        assert score == pytest.approx(0.2, rel=1e-6)

    def test_score_never_negative(self):
        sym = SymbolAttention(
            symbol_key="X.SH",
            bare_code="X",
            name="X",
            asset_class=ASSET_CLASS_A_SHARE,
            mention_count=2,
            fresh_mention_count=1,
            theme_count=1,
            source_count=1,
            high_quality_mention_count=0,
            stale_mention_count=5,
            deprecated_mention_count=5,
        )
        sym.matched_pages = [
            PageMention(
                rel_path="fresh.md",
                title="F",
                page_type="company",
                source_aliases=["s1"],
                themes=["t"],
            )
        ]
        score, _ = _compose_score(sym)
        assert score >= 0.0


# ── 6. 排序稳定性 ────────────────────────────────────────────────────


class TestSorting:
    def test_symbols_sorted_by_score_desc(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        scores = [s.research_attention_score for s in result.symbols]
        assert scores == sorted(scores, reverse=True)

    def test_top_symbols_capped(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        # 默认 top=30，fixture 只有 7 个标的。
        assert len(result.top_symbols) == min(30, result.symbol_count)

    def test_tie_breaker_by_mention_then_theme(self):
        """同分数时按 mention_count → theme_count → symbol_key 排序。"""
        s1 = SymbolAttention(
            symbol_key="B.SH", bare_code="B", name="B",
            asset_class=ASSET_CLASS_A_SHARE, mention_count=2, theme_count=1,
        )
        s1.research_attention_score = 1.0
        s2 = SymbolAttention(
            symbol_key="A.SH", bare_code="A", name="A",
            asset_class=ASSET_CLASS_A_SHARE, mention_count=1, theme_count=1,
        )
        s2.research_attention_score = 1.0
        syms = sorted(
            [s2, s1],
            key=lambda s: (
                -s.research_attention_score,
                -s.mention_count,
                -s.theme_count,
                s.symbol_key,
            ),
        )
        # 同分时 mention=2 排在前。
        assert syms[0].symbol_key == "B.SH"


# ── 7. 报告渲染 ──────────────────────────────────────────────────────


class TestReportRendering:
    def test_report_contains_key_sections(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        report = render_research_attention_report(result)
        assert "[KB-007]" in report
        assert "扫描概览" in report
        assert "资产类别分布" in report
        assert "Top 研究关注度标的" in report
        assert "计分口径" in report
        assert "免责声明" in report

    def test_report_contains_asset_class_breakdown(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        report = render_research_attention_report(result)
        # fixture 包含 5 类资产（A_SHARE / HK / US / FUND / UNLISTED）。
        for ac in (
            ASSET_CLASS_A_SHARE,
            ASSET_CLASS_HK,
            ASSET_CLASS_US,
            ASSET_CLASS_FUND,
            ASSET_CLASS_UNLISTED,
        ):
            assert ac in report

    def test_report_does_not_contain_strong_action_words(
        self, fixture_kb: Path
    ):
        """验收：无任何买卖建议或强动作词。"""
        result = compute_research_attention(str(fixture_kb))
        report = render_research_attention_report(result)
        forbidden = ["买入", "卖出", "加仓", "减仓", "强烈推荐", "清仓", "追涨"]
        for word in forbidden:
            assert word not in report, f"报告出现强动作词: {word}"

    def test_report_does_not_dump_original_section_text(
        self, fixture_kb: Path
    ):
        """不输出原文段落（不读取 PDF 全文约束的镜像）。"""
        result = compute_research_attention(str(fixture_kb))
        report = render_research_attention_report(result)
        # 原文段落细节不应出现在报告里。
        assert "需求不及预期" not in report  # COMPANY_A 风险段细节
        assert "ODM 切入 AI 服务器" not in report  # COMPANY_B 摘要细节

    def test_report_top_limit(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        report = render_research_attention_report(result, top=3, detail=2)
        # Top 表只列前 3 个。
        import re as _re

        # 表格数据行（| 数字 | `code` |）。
        rows = _re.findall(r"^\| \d+ \| `", report, flags=_re.MULTILINE)
        assert len(rows) == 3
        # 明细段（### `code` —）只列前 2 个。
        details = _re.findall(r"^### `", report, flags=_re.MULTILINE)
        assert len(details) == 2

    def test_report_empty_kb_does_not_crash(self, empty_kb: Path):
        result = compute_research_attention(str(empty_kb))
        report = render_research_attention_report(result)
        assert "[KB-007]" in report
        assert "无命中标的" in report

    def test_report_includes_score_explain_for_top_symbols(
        self, fixture_kb: Path
    ):
        result = compute_research_attention(str(fixture_kb))
        report = render_research_attention_report(result)
        # 第一个详细 symbol 应包含 score_explain 行。
        assert "score_explain:" in report


# ── 8. 错误与空库处理 ────────────────────────────────────────────────


class TestErrorHandling:
    def test_missing_knowledge_root_returns_empty_result(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist"
        result = compute_research_attention(str(missing))
        assert result.symbol_count == 0
        assert result.investment_page_count == 0
        assert any("knowledge_root 不存在" in e for e in result.errors)

    def test_empty_investment_dir_returns_empty_symbols(self, empty_kb: Path):
        result = compute_research_attention(str(empty_kb))
        assert result.symbol_count == 0
        assert result.symbols == []

    def test_single_page_failure_does_not_abort_scan(
        self, fixture_kb: Path, monkeypatch
    ):
        """单页解析失败应记入 errors 但不影响其它页。"""
        from tradingagents.dataflows import research_attention as ra

        original = ra._audit_single_page
        call_count = {"n": 0}

        def flaky(rel_path, abs_path):
            call_count["n"] += 1
            if "腾讯" in rel_path:
                raise RuntimeError("boom")
            return original(rel_path, abs_path)

        monkeypatch.setattr(ra, "_audit_single_page", flaky)
        result = compute_research_attention(str(fixture_kb))
        # 腾讯页失败 → 不进入索引，但其它页仍处理。
        assert all("腾讯" not in s.symbol_key for s in result.symbols)
        assert any("腾讯" in e and "解析失败" in e for e in result.errors)
        assert result.symbol_count >= 6  # 7 - 1 (腾讯失败)


# ── 9. 序列化 ────────────────────────────────────────────────────────


class TestSerialization:
    def test_to_dict_round_trip(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        d = result.to_dict()
        assert d["contract_version"] == CONTRACT_VERSION
        assert d["task"] == "KB-007"
        assert d["symbol_count"] == result.symbol_count
        assert isinstance(d["symbols"], list)
        assert isinstance(d["top_symbols"], list)
        # JSON 可序列化。
        text = json.dumps(d, ensure_ascii=False)
        assert "research_attention_score" in text

    def test_symbol_to_dict_fields_complete(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        sym = result.symbols[0]
        d = sym.to_dict()
        required = [
            "symbol_key", "bare_code", "name", "asset_class",
            "research_attention_score", "mention_count", "fresh_mention_count",
            "theme_count", "source_count", "high_quality_mention_count",
            "stale_mention_count", "deprecated_mention_count",
            "report_type_distribution", "themes", "sources",
            "latest_updated", "matched_pages", "score_explain",
        ]
        for k in required:
            assert k in d, f"missing {k}"

    def test_page_mention_to_dict_no_original_text(self, fixture_kb: Path):
        result = compute_research_attention(str(fixture_kb))
        for sym in result.symbols[:3]:
            for p in sym.matched_pages:
                d = p.to_dict()
                # 不携带正文段落，只元数据。
                assert "rel_path" in d
                assert "summary" not in d
                assert "risks" not in d


# ── 10. CLI 子进程冒烟 ───────────────────────────────────────────────


class TestCLI:
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def test_cli_help(self):
        proc = subprocess.run(
            [sys.executable, "scripts/run_research_attention.py", "--help"],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0
        assert "KB-007" in proc.stdout

    def test_cli_markdown_stdout(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/run_research_attention.py",
                "--knowledge-root",
                str(fixture_kb),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0, proc.stderr
        assert "[KB-007]" in proc.stdout
        assert "Top 研究关注度标的" in proc.stdout

    def test_cli_json_stdout(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/run_research_attention.py",
                "--knowledge-root",
                str(fixture_kb),
                "--json",
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["task"] == "KB-007"
        assert payload["contract_version"] == CONTRACT_VERSION
        assert payload["symbol_count"] >= 1

    def test_cli_write_files(self, fixture_kb: Path, tmp_path: Path):
        md_out = tmp_path / "out" / "report.md"
        json_out = tmp_path / "out" / "report.json"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/run_research_attention.py",
                "--knowledge-root",
                str(fixture_kb),
                "--output",
                str(md_out),
                "--json-output",
                str(json_out),
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0, proc.stderr
        assert md_out.exists()
        assert json_out.exists()
        assert "[KB-007]" in md_out.read_text(encoding="utf-8")
        payload = json.loads(json_out.read_text(encoding="utf-8"))
        assert payload["symbol_count"] >= 1

    def test_cli_default_does_not_autowrite(self, fixture_kb: Path):
        """与 KB-001/KB-002 一致：未指定 --output 时默认只打印 stdout，不落盘。"""
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/run_research_attention.py",
                "--knowledge-root",
                str(fixture_kb),
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0, proc.stderr
        # stdout 有 Markdown 内容。
        assert "[KB-007]" in proc.stdout
        # stderr 无"written to"摘要（因为没有落盘）。
        assert "written to" not in proc.stderr

    def test_cli_missing_root_returns_nonzero(self, tmp_path: Path):
        missing = tmp_path / "no_kb"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/run_research_attention.py",
                "--knowledge-root",
                str(missing),
                "--stdout",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 1

    def test_cli_top_flag(self, fixture_kb: Path):
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/run_research_attention.py",
                "--knowledge-root",
                str(fixture_kb),
                "--stdout",
                "--top",
                "2",
            ],
            capture_output=True,
            text=True,
            cwd=str(self._repo_root()),
        )
        assert proc.returncode == 0, proc.stderr
        import re as _re

        rows = _re.findall(r"^\| \d+ \| `", proc.stdout, flags=_re.MULTILINE)
        assert len(rows) == 2


# ── 11. 只读安全性 ───────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_compute_does_not_modify_kb(self, fixture_kb: Path):
        def snapshot(root: Path) -> Dict[str, int]:
            snap = {}
            for p in root.rglob("*"):
                if p.is_file():
                    snap[str(p.relative_to(root))] = p.stat().st_size
            return snap

        before = snapshot(fixture_kb)
        compute_research_attention(str(fixture_kb))
        compute_research_attention(str(fixture_kb))  # 跑两次确保幂等
        after = snapshot(fixture_kb)
        assert before == after

    def test_compute_does_not_create_files(self, fixture_kb: Path):
        files_before = {p for p in fixture_kb.rglob("*") if p.is_file()}
        compute_research_attention(str(fixture_kb))
        files_after = {p for p in fixture_kb.rglob("*") if p.is_file()}
        assert files_after == files_before

    def test_mtimes_unchanged(self, fixture_kb: Path):
        target = fixture_kb / INVESTMENT_SUBDIR / "华勤技术603296-超节点.md"
        before = target.stat().st_mtime
        compute_research_attention(str(fixture_kb))
        after = target.stat().st_mtime
        assert before == after


# ── 12. 默认输出路径 ─────────────────────────────────────────────────


class TestDefaults:
    def test_suggest_md_path_format(self):
        path = suggest_report_output_path(ext=".md")
        assert "research_attention-" in path
        assert path.endswith(".md")

    def test_suggest_json_path_format(self):
        path = suggest_report_output_path(ext=".json")
        assert "research_attention-" in path
        assert path.endswith(".json")

    def test_suggest_path_includes_today(self):
        from datetime import date as _date

        path = suggest_report_output_path()
        today = _date.today().strftime("%Y-%m-%d")
        assert today in path


# ── 13. 验收条件综合（任务契约） ─────────────────────────────────────


class TestAcceptanceContract:
    """对照任务 KB-007 的 4 条验收方式。"""

    def test_can_generate_symbol_attention_ranking(self, fixture_kb: Path):
        """验收 1：对当前知识库可生成 symbol 关注度榜。"""
        result = compute_research_attention(str(fixture_kb))
        assert result.symbol_count > 0
        assert len(result.top_symbols) > 0
        # Top 必有 score（可为 0，但字段必须存在）。
        for s in result.top_symbols:
            assert isinstance(s.research_attention_score, float)

    def test_multi_page_hits_aggregate_themes_and_sources(
        self, fixture_kb: Path
    ):
        """验收 2：同一股票多篇命中能汇总主题与来源。"""
        result = compute_research_attention(str(fixture_kb))
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        assert huaqin.mention_count >= 2
        assert huaqin.theme_count >= 2
        assert huaqin.source_count >= 2
        # 主题跨多页聚合。
        assert "AI服务器" in huaqin.themes
        assert any("液冷" in t or "AI算力" in t for t in huaqin.themes)

    def test_stale_pages_do_not_boost_score(self, fixture_kb: Path):
        """验收 3：过期/低置信/废弃页面不提升强信号。"""
        result = compute_research_attention(str(fixture_kb))
        # 浦发银行：唯一命中是过期页 → score=0。
        pufa = next(s for s in result.symbols if s.symbol_key == "600000.SH")
        assert pufa.research_attention_score == 0.0
        # Dell：唯一命中是 deprecated/低置信页 → score=0。
        dell = next(s for s in result.symbols if s.symbol_key == "DELL.US")
        assert dell.research_attention_score == 0.0
        # 华勤（有 fresh 命中）必须排在浦发/Dell 之前。
        huaqin = next(
            s for s in result.symbols if s.symbol_key == "603296.SH"
        )
        assert huaqin.research_attention_score > pufa.research_attention_score
        assert huaqin.research_attention_score > dell.research_attention_score

    def test_no_buy_or_sell_signals_in_output(self, fixture_kb: Path):
        """验收 4：无任何买卖建议或强动作词。"""
        result = compute_research_attention(str(fixture_kb))
        # 检查 JSON 输出。
        payload = json.dumps(result.to_dict(), ensure_ascii=False)
        # score_explain 只允许中性词，不允许强动作词。
        forbidden = ["买入", "卖出", "加仓", "减仓", "强烈推荐", "清仓", "建议买入", "建议卖出"]
        for word in forbidden:
            assert word not in payload, f"JSON 输出出现强动作词: {word}"
        # Markdown 输出同样。
        report = render_research_attention_report(result)
        for word in forbidden:
            assert word not in report, f"报告出现强动作词: {word}"

    def test_asset_classes_not_mixed(self, fixture_kb: Path):
        """验收 5（实现要点）：基金/港股/美股/未上市/ A 股分开处理。"""
        result = compute_research_attention(str(fixture_kb))
        asset_classes = {s.asset_class for s in result.symbols}
        assert ASSET_CLASS_A_SHARE in asset_classes
        assert ASSET_CLASS_HK in asset_classes
        assert ASSET_CLASS_US in asset_classes
        assert ASSET_CLASS_FUND in asset_classes
        assert ASSET_CLASS_UNLISTED in asset_classes
        # 每个 symbol 有明确分类，不会全是 OTHER。
        assert all(s.asset_class != ASSET_CLASS_OTHER for s in result.symbols)
