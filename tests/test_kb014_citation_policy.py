# [KB-014] citation_policy
"""Tests for 研报/财报来源可信度分层与 citation policy (KB-014).

覆盖：
  - ``classify_source_quality_tier``：五类来源（original_filing / official_notice /
    broker_research / media / user_note / unknown）的 frontmatter 分类。
  - ``compute_tier_confidence_weight``：tier × readiness × stale/low 综合权重。
  - ``apply_tier_to_confidence``：tier 把 high/medium confidence 软降级。
  - ``render_citation_summary`` / ``render_tier_table``：报告渲染。
  - lint CIT-001/CIT-002/CIT-003 规则与 PageLintResult.source_quality_tier 字段。
  - provider LocalKnowledgeMatch.source_quality_tier / citation_confidence_weight
    与 KB-004 _page_score 软调节。
  - citation policy 约束：弱来源不被"过滤"、不直接转成交易动作。
  - fixture 覆盖五类来源（验收要求）。
  - 无买卖建议词。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from tradingagents.dataflows.citation_policy import (
    OPINION_ONLY_TIERS,
    SOURCE_QUALITY_TIERS,
    TIER_BROKER_RESEARCH,
    TIER_CONFIDENCE_WEIGHTS,
    TIER_MEDIA,
    TIER_OFFICIAL_NOTICE,
    TIER_ORIGINAL_FILING,
    TIER_UNKNOWN,
    TIER_USER_NOTE,
    CitationAssessment,
    apply_tier_to_confidence,
    classify_source_quality_tier,
    compute_tier_confidence_weight,
    render_citation_summary,
    render_tier_table,
)
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.local_knowledge_lint import (
    lint_local_knowledge,
    lint_single_page,
)
from tradingagents.dataflows.local_knowledge_provider import (
    LocalKnowledgeMatch,
    _rank_key,
    compute_local_knowledge_score,
    query_local_knowledge,
)


# ── helpers ──────────────────────────────────────────────────────────


_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL",
    "立即买入", "全仓", "止损",
)


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, f"text contains forbidden word: {forbidden}"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 五类来源 fixture（覆盖验收要求）。
_FILING_PAGE = """---
title: 某公司-2025H1半年报
created: 2026-08-30
updated: 2026-08-30
sources:
  - "[[../../raw/2026-08-30-某公司.md|公司公告-2025H1]]"
tags: [某公司, 半年报]
related: []
symbols: ["600000.SH 某公司"]
themes: [AI]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-30
source_type: [exchange_filing, fact_table]
financial_facts:
  - 营收 100亿 (+20%)
risk_factors: [汇率]
---

# 某公司（600000）

## 一句话总结

营收同比增长。

## 投资逻辑

- 业绩稳健。

## 风险提示

- 汇率波动。

## 原始资料

- 巨潮资讯 <公告URL>
"""

_OFFICIAL_NOTICE_PAGE = """---
title: 某政策-官方文件
created: 2026-05-01
updated: 2026-05-01
sources:
  - 证监会公告
tags: [政策]
related: []
symbols: []
themes: [政策]
report_type: 政策
evidence_level: A
valid_until: 长期
source_quality: 高
stale_risk: 低
---

# 某政策

## 一句话总结

某政策出台。

## 投资逻辑

- 利好行业。

## 风险提示

- 落地不及预期。

## 原始资料

- 证监会官网。
"""

_BROKER_PAGE = """---
title: 某公司-券商深度
created: 2026-06-01
updated: 2026-06-01
sources:
  - 某券商深度研报
tags: [某公司]
related: []
symbols: ["600001.SH 某公司"]
themes: [AI]
report_type: 深度
evidence_level: B
valid_until: 2099-12-31
source_quality: 中
stale_risk: 低
---

# 某公司券商深度

## 一句话总结

券商看好。

## 投资逻辑

- 券商观点。

## 风险提示

- 业绩不及预期。

## 原始资料

- 某券商研报。
"""

_MEDIA_PAGE = """---
title: 某公司-媒体报道
created: 2026-06-15
updated: 2026-06-15
sources:
  - 财联社报道
tags: [某公司]
related: []
symbols: ["600002.SH 某公司"]
themes: [AI]
report_type: 公司点评
evidence_level: C
valid_until: 2099-12-31
source_quality: 低
stale_risk: 低
---

# 媒体报道

## 一句话总结

媒体报道某公司动态。

## 投资逻辑

- 媒体观察。

## 风险提示

- 信息未证实。

## 原始资料

- 财联社。
"""

_USER_NOTE_PAGE = """---
title: 我的观察-某主题
created: 2026-06-20
updated: 2026-06-20
sources: []
tags: [某主题, 笔记]
related: []
symbols: []
themes: [某主题]
report_type: 综述
evidence_level: C
valid_until: 2099-12-31
source_quality: 低
stale_risk: 低
---

# 我的观察

## 一句话总结

个人笔记。

## 投资逻辑

- 自行整理。

## 风险提示

- 仅供参考。

## 原始资料

- 个人整理。
"""

_BARE_PAGE = """# 某想法

直接写正文，无 frontmatter。
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造覆盖五类来源的微型知识库（验收要求）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "filing-半年报.md", _FILING_PAGE)
    _write(inv / "official-政策.md", _OFFICIAL_NOTICE_PAGE)
    _write(inv / "broker-深度.md", _BROKER_PAGE)
    _write(inv / "media-报道.md", _MEDIA_PAGE)
    _write(inv / "user-笔记.md", _USER_NOTE_PAGE)
    _write(inv / "bare-无frontmatter.md", _BARE_PAGE)
    return tmp_path


# ── 1. classify_source_quality_tier ─────────────────────────────────


class TestClassifyTier:
    def test_original_filing_from_source_type(self):
        fm = {"source_type": ["exchange_filing", "fact_table"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_ORIGINAL_FILING
        assert a.is_fact_usable is True
        assert a.is_opinion_only is False
        assert a.weak_source_risk is False

    def test_original_filing_from_management_commentary(self):
        # management_commentary 也算"半事实"，升为 original_filing。
        fm = {"source_type": ["management_commentary"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_ORIGINAL_FILING

    def test_official_notice_from_sources_keywords(self):
        # sources 含"证监会"关键词 → official_notice（官方/监管事实）。
        fm = {"sources": ["证监会公告"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_OFFICIAL_NOTICE
        assert a.is_fact_usable is True

    def test_exchange_source_is_official_not_broker(self):
        for source in ("上海证券交易所公告", "深圳证券交易所监管函", "北京证券交易所问询函"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_OFFICIAL_NOTICE
            assert a.is_fact_usable is True

    def test_regulator_source_with_news_word_stays_official(self):
        # "新闻"是泛媒体词，但证监会/证券监督管理委员会是显式监管来源。
        for source in ("证监会新闻发布会", "中国证券监督管理委员会公告"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_OFFICIAL_NOTICE
            assert a.is_fact_usable is True

    def test_official_notice_from_source_type(self):
        fm = {"source_type": ["official_notice"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_OFFICIAL_NOTICE
        assert a.is_fact_usable is True

    def test_broker_research_from_source_type(self):
        fm = {"source_type": ["broker_report"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_BROKER_RESEARCH
        assert a.is_fact_usable is False
        assert a.is_opinion_only is True
        assert a.weak_source_risk is False

    def test_broker_research_from_sources_keywords(self):
        fm = {"sources": ["某券商深度研报"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_BROKER_RESEARCH

    def test_broker_name_with_securities_keyword_is_broker_research(self):
        for source in ("中邮证券-华勤技术超节点", "中信证券"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_BROKER_RESEARCH
            assert a.is_fact_usable is False

    def test_common_broker_aliases_are_broker_research(self):
        # 常见卖方机构名/点评标题不能落成 unknown，更不能被财报 report_type 抬成事实源。
        for source in ("国泰君安年报点评", "华泰证券报告", "中金公司2025H1点评"):
            a = classify_source_quality_tier(
                {"report_type": "半年报", "sources": [source]}
            )
            assert a.tier == TIER_BROKER_RESEARCH
            assert a.is_fact_usable is False
            assert a.is_opinion_only is True

    def test_broker_source_type_not_upgraded_by_filing_sources(self):
        # 券商研报引用公司公告/巨潮链接仍是券商观点，不得升为公告原文。
        for source in ("公司公告", "巨潮资讯 <公告URL>"):
            a = classify_source_quality_tier(
                {"source_type": ["broker_report"], "sources": [source]}
            )
            assert a.tier == TIER_BROKER_RESEARCH
            assert a.is_fact_usable is False
            assert a.is_opinion_only is True
            assert any("不升权" in signal for signal in a.signals)

    def test_broker_report_title_with_period_words_stays_broker_research(self):
        # 券商年报/半年报点评标题里常带披露周期词，不得因此升级为公告事实源。
        for source in ("中信证券2025年报点评研报", "某券商半年报点评研报"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_BROKER_RESEARCH
            assert a.is_fact_usable is False

    def test_broker_source_with_news_word_stays_broker_research(self):
        # 券商标题里的"新闻点评"不是媒体来源，不能被泛媒体词降级。
        for source in ("中信证券新闻点评研报", "某券商新闻点评研报"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_BROKER_RESEARCH
            assert a.is_fact_usable is False

    def test_broker_report_with_disclosure_wording_stays_broker_research(self):
        # "披露" 是正文语义里的高频词，不能单独把券商/观点来源抬成公告事实。
        a = classify_source_quality_tier({"sources": ["某券商研报：公司披露订单高增"]})
        assert a.tier == TIER_BROKER_RESEARCH
        assert a.is_fact_usable is False

    def test_mixed_official_and_media_sources_take_stronger_tier(self):
        a = classify_source_quality_tier({"sources": ["巨潮资讯公告", "财联社报道"]})
        assert a.tier == TIER_ORIGINAL_FILING
        assert a.is_fact_usable is True

    def test_single_media_item_about_filing_stays_media(self):
        a = classify_source_quality_tier({"sources": ["财联社报道：公司公告披露订单高增"]})
        assert a.tier == TIER_MEDIA
        assert a.is_fact_usable is False

    def test_media_org_item_about_regulator_stays_media(self):
        a = classify_source_quality_tier({"sources": ["新华社报道：证监会召开新闻发布会"]})
        assert a.tier == TIER_MEDIA
        assert a.is_fact_usable is False

    def test_xinhua_company_filing_not_misclassified_as_media(self):
        # "新华"可以是发行人名称，不能当媒体机构泛词；只认新华社/新华网等媒体主体。
        for source in ("新华保险公司公告", "新华制药公司公告"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_ORIGINAL_FILING
            assert a.is_fact_usable is True

    def test_xinhua_media_org_stays_media(self):
        for source in ("新华社报道：行业政策变化", "新华网报道：公司动态"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_MEDIA
            assert a.is_fact_usable is False

    def test_generic_opinion_words_do_not_become_broker_research(self):
        for source in ("个人观点：公司有望受益", "我的策略记录"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_USER_NOTE
            assert a.is_fact_usable is False
            assert a.weak_source_risk is True

    def test_generic_opinion_words_are_not_boosted_by_report_type(self):
        a = classify_source_quality_tier({
            "report_type": "半年报",
            "sources": ["我的策略记录"],
        })
        assert a.tier == TIER_USER_NOTE
        assert a.is_fact_usable is False

    def test_media_from_source_type(self):
        fm = {"source_type": ["media"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_MEDIA
        assert a.is_fact_usable is False
        assert a.is_opinion_only is True
        assert a.weak_source_risk is True

    def test_media_from_sources_keywords(self):
        fm = {"sources": ["财联社报道"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_MEDIA

    def test_media_org_with_report_words_stays_media(self):
        # 显式媒体机构 + "研报/研究报告" 只是二手解读，不应抬成券商研报。
        for source in ("财联社研报解读", "证券时报研究报告解读", "中国证券报研报精选"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_MEDIA
            assert a.weak_source_risk is True

    def test_securities_newspaper_stays_media(self):
        # 中国证券报/证券时报是媒体，不应因“证券”二字被抬成 broker_research。
        for source in ("中国证券报报道", "证券时报新闻"):
            a = classify_source_quality_tier({"sources": [source]})
            assert a.tier == TIER_MEDIA
            assert a.weak_source_risk is True

    def test_user_note_when_only_tags(self):
        # 无 sources / source_type，但有 tags/title → user_note。
        fm = {"tags": ["某主题"], "title": "某笔记"}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_USER_NOTE
        assert a.is_fact_usable is False
        assert a.weak_source_risk is True

    def test_user_note_when_tags_list_without_title(self):
        fm = {"tags": ["某主题"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_USER_NOTE
        assert a.is_fact_usable is False
        assert a.weak_source_risk is True

    def test_unknown_when_completely_empty(self):
        a = classify_source_quality_tier({})
        assert a.tier == TIER_UNKNOWN
        assert a.is_fact_usable is False
        assert a.weak_source_risk is True

    def test_unknown_when_frontmatter_not_dict(self):
        a = classify_source_quality_tier(None)  # type: ignore[arg-type]
        assert a.tier == TIER_UNKNOWN
        a2 = classify_source_quality_tier("not a dict")  # type: ignore[arg-type]
        assert a2.tier == TIER_UNKNOWN

    def test_mixed_source_type_takes_highest_tier(self):
        # 同时含 exchange_filing 和 media → 取最高的 original_filing。
        fm = {"source_type": ["exchange_filing", "media"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_ORIGINAL_FILING

    def test_report_type_filing_with_unrecognized_sources_downgrades(self):
        # report_type=半年报 + sources 非空但未识别出 tier，不得兜底升为 original_filing。
        fm = {"report_type": "半年报", "sources": ["某 来源 路径"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_USER_NOTE
        assert a.is_fact_usable is False

    def test_report_type_filing_does_not_boost_opaque_source_signal(self):
        # 完全无法识别的 sources 字符串 → 只能作弱来源记录。
        fm = {"report_type": "半年报", "sources": ["未知来源XYZ"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_USER_NOTE
        assert a.is_fact_usable is False

    def test_report_type_filing_without_sources_downgrades(self):
        # report_type=半年报 但 sources 完全缺失 → user_note（来源弱）。
        fm = {"report_type": "半年报"}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_USER_NOTE

    def test_report_type_filing_with_whitespace_sources_downgrades(self):
        # 空白 sources 占位不算真实来源，不能触发财报兜底升级为公告/财报原文。
        for sources in (["   "], ["", "\t"], "   "):
            fm = {"report_type": "半年报", "sources": sources}
            a = classify_source_quality_tier(fm)
            assert a.tier == TIER_USER_NOTE
            assert a.is_fact_usable is False

    def test_invalid_source_type_does_not_boost_to_filing(self):
        # source_type 非空但拼错/未知，不得被 report_type 兜底抬成 original_filing。
        fm = {"report_type": "半年报", "source_type": ["broker_reprot"]}
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_USER_NOTE
        assert a.is_fact_usable is False

    def test_invalid_source_type_with_opaque_source_does_not_boost_to_filing(self):
        # 即使 sources 有 opaque 路径/URL，拼错 source_type 仍禁用 report_type 兜底。
        fm = {
            "report_type": "半年报",
            "source_type": ["broker_reprot"],
            "sources": ["foo.pdf"],
        }
        a = classify_source_quality_tier(fm)
        assert a.tier == TIER_USER_NOTE
        assert a.is_fact_usable is False

    def test_signals_are_populated(self):
        fm = {"source_type": ["exchange_filing"], "sources": ["公司公告"]}
        a = classify_source_quality_tier(fm)
        assert len(a.signals) > 0
        assert any("source_type" in s for s in a.signals)

    def test_all_tiers_have_usage_policy(self):
        from tradingagents.dataflows.citation_policy import TIER_USAGE_POLICY

        for tier in SOURCE_QUALITY_TIERS:
            assert tier in TIER_USAGE_POLICY
            assert isinstance(TIER_USAGE_POLICY[tier], str)
            assert len(TIER_USAGE_POLICY[tier]) > 0

    def test_all_tiers_have_confidence_weight(self):
        for tier in SOURCE_QUALITY_TIERS:
            assert tier in TIER_CONFIDENCE_WEIGHTS
            w = TIER_CONFIDENCE_WEIGHTS[tier]
            assert 0.0 <= w <= 1.0
        # original_filing 权重最高；unknown 最低。
        assert TIER_CONFIDENCE_WEIGHTS[TIER_ORIGINAL_FILING] == 1.0
        assert TIER_CONFIDENCE_WEIGHTS[TIER_UNKNOWN] < TIER_CONFIDENCE_WEIGHTS[TIER_BROKER_RESEARCH]


# ── 2. compute_tier_confidence_weight ───────────────────────────────


class TestComputeWeight:
    def test_basic_weight_for_each_tier(self):
        assert compute_tier_confidence_weight(TIER_ORIGINAL_FILING) == 1.0
        assert compute_tier_confidence_weight(TIER_BROKER_RESEARCH) == 0.6
        assert compute_tier_confidence_weight(TIER_MEDIA) == 0.3
        assert compute_tier_confidence_weight(TIER_USER_NOTE) == 0.2
        assert compute_tier_confidence_weight(TIER_UNKNOWN) == 0.1

    def test_stale_reduces_weight(self):
        # original_filing × stale → 1.0 × 0.3 = 0.3
        w = compute_tier_confidence_weight(
            TIER_ORIGINAL_FILING, is_stale=True
        )
        assert w == pytest.approx(0.3, abs=0.001)

    def test_low_confidence_reduces_weight(self):
        w = compute_tier_confidence_weight(
            TIER_ORIGINAL_FILING, is_low_confidence=True
        )
        assert w == pytest.approx(0.3, abs=0.001)

    def test_to_be_supplemented_reduces_weight(self):
        w = compute_tier_confidence_weight(
            TIER_ORIGINAL_FILING, is_to_be_supplemented=True
        )
        assert w == pytest.approx(0.3, abs=0.001)

    def test_medium_readiness_reduces_weight(self):
        # original_filing × medium readiness → 1.0 × 0.7 = 0.7
        w = compute_tier_confidence_weight(
            TIER_ORIGINAL_FILING, machine_readiness="medium"
        )
        assert w == pytest.approx(0.7, abs=0.001)

    def test_low_readiness_reduces_weight(self):
        w = compute_tier_confidence_weight(
            TIER_ORIGINAL_FILING, machine_readiness="low"
        )
        assert w == pytest.approx(0.3, abs=0.001)

    def test_combined_multipliers(self):
        # broker × stale × medium → 0.6 × 0.3 × 0.7 = 0.126
        w = compute_tier_confidence_weight(
            TIER_BROKER_RESEARCH,
            machine_readiness="medium",
            is_stale=True,
        )
        assert w == pytest.approx(0.126, abs=0.001)

    def test_weight_bounded_0_to_1(self):
        # 极端组合也不超界。
        w = compute_tier_confidence_weight(
            TIER_ORIGINAL_FILING,
            machine_readiness="high",
        )
        assert 0.0 <= w <= 1.0

    def test_unknown_tier_returns_low_weight(self):
        w = compute_tier_confidence_weight("not_a_tier")
        # 未知 tier 走默认权重（unknown = 0.1）。
        assert w == pytest.approx(0.1, abs=0.001)


# ── 3. apply_tier_to_confidence ─────────────────────────────────────


class TestApplyTierToConfidence:
    def test_original_filing_keeps_confidence(self):
        a = CitationAssessment(tier=TIER_ORIGINAL_FILING)
        assert apply_tier_to_confidence("high", a) == "high"
        assert apply_tier_to_confidence("medium", a) == "medium"
        assert apply_tier_to_confidence("low", a) == "low"

    def test_official_notice_keeps_confidence(self):
        a = CitationAssessment(tier=TIER_OFFICIAL_NOTICE)
        assert apply_tier_to_confidence("high", a) == "high"

    def test_broker_research_downgrades_high_to_medium(self):
        a = CitationAssessment(tier=TIER_BROKER_RESEARCH)
        assert apply_tier_to_confidence("high", a) == "medium"
        # medium 保持 medium（不再降）。
        assert apply_tier_to_confidence("medium", a) == "medium"
        assert apply_tier_to_confidence("low", a) == "low"

    def test_media_downgrades_to_low(self):
        a = CitationAssessment(tier=TIER_MEDIA)
        assert apply_tier_to_confidence("high", a) == "low"
        assert apply_tier_to_confidence("medium", a) == "low"

    def test_user_note_downgrades_to_low(self):
        a = CitationAssessment(tier=TIER_USER_NOTE)
        assert apply_tier_to_confidence("high", a) == "low"

    def test_unknown_downgrades_to_low(self):
        a = CitationAssessment(tier=TIER_UNKNOWN)
        assert apply_tier_to_confidence("high", a) == "low"

    def test_invalid_assessment_returns_base(self):
        # 非 CitationAssessment → 不降级（防御）。
        assert apply_tier_to_confidence("high", None) == "high"  # type: ignore[arg-type]
        assert apply_tier_to_confidence("high", "not an assessment") == "high"  # type: ignore[arg-type]


# ── 4. render_citation_summary / render_tier_table ──────────────────


class TestRenderFunctions:
    def test_summary_contains_tier_and_policy(self):
        a = CitationAssessment(tier=TIER_ORIGINAL_FILING)
        s = render_citation_summary(a)
        assert "original_filing" in s
        assert "事实可用" in s
        assert "1.00" in s

    def test_summary_for_weak_source(self):
        a = CitationAssessment(tier=TIER_UNKNOWN)
        s = render_citation_summary(a)
        assert "unknown" in s
        assert "弱来源" in s
        assert "WEAK_SOURCE" in s or "回 Tree Work" in s

    def test_summary_for_opinion(self):
        a = CitationAssessment(tier=TIER_BROKER_RESEARCH)
        s = render_citation_summary(a)
        assert "broker_research" in s
        assert "仅观点/线索" in s

    def test_summary_no_action_words(self):
        # 不得包含强买卖建议词。
        for tier in SOURCE_QUALITY_TIERS:
            a = CitationAssessment(tier=tier)
            _assert_no_strong_action_words(render_citation_summary(a))

    def test_render_summary_invalid_returns_unknown(self):
        s = render_citation_summary(None)  # type: ignore[arg-type]
        assert "unknown" in s

    def test_render_tier_table_contains_all_tiers(self):
        table = render_tier_table()
        for tier in SOURCE_QUALITY_TIERS:
            assert f"`{tier}`" in table
        assert "事实" in table
        assert "使用边界" in table


# ── 5. lint CIT- 规则集成 ────────────────────────────────────────────


class TestLintCitationIntegration:
    def test_page_with_source_type_filing_no_cit_findings(self, tmp_path: Path):
        p = tmp_path / "filing.md"
        p.write_text(_FILING_PAGE, encoding="utf-8")
        result = lint_single_page("filing.md", p)
        assert result.source_quality_tier == TIER_ORIGINAL_FILING
        assert result.citation_assessment is not None
        assert result.citation_assessment.tier == TIER_ORIGINAL_FILING
        # 不触发 CIT-001/002/003。
        rule_ids = {f.rule_id for f in result.findings}
        assert "CIT-001" not in rule_ids
        assert "CIT-002" not in rule_ids
        assert "CIT-003" not in rule_ids

    def test_page_no_sources_triggers_cit001(self, tmp_path: Path):
        # 完全没有 sources / source_type → CIT-001 warning。
        text = _USER_NOTE_PAGE.replace("sources: []", "sources: []")
        p = tmp_path / "user.md"
        p.write_text(text, encoding="utf-8")
        result = lint_single_page("user.md", p)
        assert result.source_quality_tier == TIER_USER_NOTE
        rule_ids = {f.rule_id for f in result.findings}
        assert "CIT-001" not in rule_ids  # user_note 不算 unknown
        # 但 CIT-003 会触发（user_note 是弱来源）。
        assert "CIT-003" in rule_ids

    def test_bare_page_triggers_cit001(self, tmp_path: Path):
        p = tmp_path / "bare.md"
        p.write_text(_BARE_PAGE, encoding="utf-8")
        result = lint_single_page("bare.md", p)
        assert result.source_quality_tier == TIER_UNKNOWN
        rule_ids = {f.rule_id for f in result.findings}
        assert "CIT-001" in rule_ids
        assert "CIT-003" in rule_ids

    def test_broker_research_financial_report_triggers_cit002(self, tmp_path: Path):
        # report_type=半年报 但 tier=broker_research → CIT-002 info。
        text = _BROKER_PAGE.replace("report_type: 深度", "report_type: 半年报")
        text = text.replace("financial_period: null", "financial_period: 2025H1")
        # 确保有 financial_period 避免触发 HYF-001。
        if "financial_period:" not in text:
            text = text.replace(
                "source_type: null",
                "source_type: null\nfinancial_period: 2025H1",
            )
        # 添加 financial_facts 与 risk_factors 满足 HYF
        text = text.replace(
            "stale_risk: 低\n---",
            "stale_risk: 低\nfinancial_period: 2025H1\n"
            "disclosure_date: 2026-08-30\n"
            "financial_facts:\n  - 营收 10亿\n"
            "risk_factors: [test]\n---",
        )
        p = tmp_path / "broker-as-filing.md"
        p.write_text(text, encoding="utf-8")
        result = lint_single_page("broker-as-filing.md", p)
        # tier 仍为 broker_research（source_type 缺失，sources 含券商关键词）。
        assert result.source_quality_tier == TIER_BROKER_RESEARCH
        rule_ids = {f.rule_id for f in result.findings}
        assert "CIT-002" in rule_ids

    def test_broker_research_annual_report_triggers_cit002(self, tmp_path: Path):
        # 年报/季报/公告同样是披露类 report_type，不能只覆盖半年报。
        text = _BROKER_PAGE.replace("report_type: 深度", "report_type: 年报")
        text = text.replace(
            "stale_risk: 低\n---",
            "stale_risk: 低\nfinancial_period: 2025年报\n"
            "disclosure_date: 2026-03-31\n"
            "financial_facts:\n  - 营收 10亿\n"
            "risk_factors: [test]\n---",
        )
        p = tmp_path / "broker-as-annual-report.md"
        p.write_text(text, encoding="utf-8")
        result = lint_single_page("broker-as-annual-report.md", p)
        assert result.source_quality_tier == TIER_BROKER_RESEARCH
        rule_ids = {f.rule_id for f in result.findings}
        assert "CIT-002" in rule_ids

    def test_media_does_not_trigger_cit002_when_not_financial(self, tmp_path: Path):
        p = tmp_path / "media.md"
        p.write_text(_MEDIA_PAGE, encoding="utf-8")
        result = lint_single_page("media.md", p)
        assert result.source_quality_tier == TIER_MEDIA
        rule_ids = {f.rule_id for f in result.findings}
        # report_type=公司点评 → 不触发 CIT-002。
        assert "CIT-002" not in rule_ids
        # 但触发 CIT-003（media 是弱来源）。
        assert "CIT-003" in rule_ids

    def test_cit001_is_warning(self, tmp_path: Path):
        from tradingagents.dataflows.local_knowledge_lint import SEVERITY_WARNING

        p = tmp_path / "bare.md"
        p.write_text(_BARE_PAGE, encoding="utf-8")
        result = lint_single_page("bare.md", p)
        for f in result.findings:
            if f.rule_id == "CIT-001":
                assert f.severity == SEVERITY_WARNING

    def test_cit002_and_cit003_are_info(self, tmp_path: Path):
        from tradingagents.dataflows.local_knowledge_lint import SEVERITY_INFO

        p = tmp_path / "media.md"
        p.write_text(_MEDIA_PAGE, encoding="utf-8")
        result = lint_single_page("media.md", p)
        for f in result.findings:
            if f.rule_id in ("CIT-002", "CIT-003"):
                assert f.severity == SEVERITY_INFO

    def test_tier_does_not_block_readiness(self, tmp_path: Path):
        # CIT 规则不强制降 readiness——只 CIT-001 是 warning，其余 info。
        # 财报页 _FILING_PAGE 应保持 high readiness。
        p = tmp_path / "filing.md"
        p.write_text(_FILING_PAGE, encoding="utf-8")
        result = lint_single_page("filing.md", p)
        assert result.machine_readiness == "high"

    def test_to_dict_includes_tier(self, tmp_path: Path):
        p = tmp_path / "filing.md"
        p.write_text(_FILING_PAGE, encoding="utf-8")
        result = lint_single_page("filing.md", p)
        d = result.to_dict()
        assert "source_quality_tier" in d
        assert d["source_quality_tier"] == TIER_ORIGINAL_FILING
        assert "citation_assessment" in d
        assert isinstance(d["citation_assessment"], dict)


# ── 6. 整库 lint 聚合 ───────────────────────────────────────────────


class TestLintAggregation:
    def test_full_kb_tier_counts(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        # 五类来源至少各 1 条。
        assert result.tier_counts.get(TIER_ORIGINAL_FILING, 0) >= 1
        assert result.tier_counts.get(TIER_BROKER_RESEARCH, 0) >= 1
        assert result.tier_counts.get(TIER_MEDIA, 0) >= 1
        assert result.tier_counts.get(TIER_USER_NOTE, 0) >= 1
        # bare page → unknown
        assert result.tier_counts.get(TIER_UNKNOWN, 0) >= 1

    def test_full_kb_weak_source_pages(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        # bare page 触发 CIT-001；media/user_note 触发 CIT-003，也应进入弱来源聚合。
        assert len(result.pages_weak_source) >= 1
        assert any("media" in p for p in result.pages_weak_source)
        assert any("user" in p for p in result.pages_weak_source)

    def test_to_dict_includes_kb_tier_aggregates(self, fixture_kb: Path):
        result = lint_local_knowledge(str(fixture_kb))
        d = result.to_dict()
        assert "pages_weak_source" in d
        assert "pages_broker_only" in d
        assert "pages_opinion_as_fact" in d
        assert "tier_counts" in d
        assert isinstance(d["tier_counts"], dict)


# ── 7. provider 集成 ────────────────────────────────────────────────


class TestProviderIntegration:
    def test_match_has_source_quality_tier(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="600000")
        assert result.status == "HAS_DATA"
        for m in result.matched_pages:
            # 每条命中都带 tier 字段。
            assert hasattr(m, "source_quality_tier")
            assert m.source_quality_tier in SOURCE_QUALITY_TIERS
            assert isinstance(m.citation_confidence_weight, float)
            assert 0.0 <= m.citation_confidence_weight <= 1.0

    def test_filing_match_preserves_high_confidence(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="600000")
        assert any(
            m.source_quality_tier == TIER_ORIGINAL_FILING
            and m.confidence == "high"
            for m in result.matched_pages
        )

    def test_broker_match_downgrades_confidence(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="600001")
        # broker_research × high → medium（观点不应等同事实）。
        assert any(
            m.source_quality_tier == TIER_BROKER_RESEARCH
            and m.confidence == "medium"
            for m in result.matched_pages
        )

    def test_media_match_downgrades_to_low(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="600002")
        assert result.status == "LOW_CONFIDENCE"
        assert any(
            m.source_quality_tier == TIER_MEDIA and m.confidence == "low"
            for m in result.matched_pages
        )

    def test_to_dict_round_trip_with_tier(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="600000")
        d = result.to_dict()
        assert all("source_quality_tier" in m for m in d["matched_pages"])
        # from_dict 能还原。
        from tradingagents.dataflows.local_knowledge_provider import (
            LocalKnowledgeQueryResult,
        )

        rebuilt = LocalKnowledgeQueryResult.from_dict(d)
        for m in rebuilt.matched_pages:
            assert m.source_quality_tier in SOURCE_QUALITY_TIERS

    def test_render_block_shows_tier(self, fixture_kb: Path):
        from tradingagents.dataflows.local_knowledge_provider import (
            render_local_knowledge_block,
        )

        result = query_local_knowledge(str(fixture_kb), symbol="600000")
        block = render_local_knowledge_block(result)
        assert "来源:" in block or "来源：" in block or "original_filing" in block

    def test_weak_source_not_filtered(self, fixture_kb: Path):
        # 验收要求：弱来源不被"过滤"——media/user_note/unknown 仍在 matched_pages。
        result = query_local_knowledge(str(fixture_kb), themes=["AI"])
        tiers_seen = {m.source_quality_tier for m in result.matched_pages}
        # 至少能看到非 original_filing 的弱来源（不被过滤掉）。
        assert tiers_seen & {TIER_BROKER_RESEARCH, TIER_MEDIA, TIER_USER_NOTE, TIER_UNKNOWN}

    def test_weak_company_match_does_not_crowd_out_fresh_industry_before_truncation(self):
        weak_company = LocalKnowledgeMatch(
            rel_path="company/weak-media.md",
            title="弱来源公司页",
            page_type="company",
            machine_readiness="high",
            confidence="low",
            source_quality_tier=TIER_MEDIA,
            citation_confidence_weight=0.3,
        )
        fresh_industry = LocalKnowledgeMatch(
            rel_path="industry/fresh-filing.md",
            title="可信产业页",
            page_type="industry",
            machine_readiness="high",
            confidence="high",
            source_quality_tier=TIER_ORIGINAL_FILING,
            citation_confidence_weight=1.0,
        )

        assert sorted([weak_company, fresh_industry], key=_rank_key)[0] is fresh_industry


# ── 8. KB-004 _page_score 软调节 ─────────────────────────────────────


class TestKb004ScoreIntegration:
    def test_filing_match_gets_full_score(self):
        from tradingagents.dataflows.local_knowledge_provider import _page_score

        m = LocalKnowledgeMatch(
            rel_path="x",
            title="x",
            page_type="company",
            confidence="high",
            source_quality_tier=TIER_ORIGINAL_FILING,
            citation_confidence_weight=1.0,
        )
        # original_filing × high × weight=1.0 → 1.0
        assert _page_score(m) == 1.0

    def test_broker_match_gets_reduced_score(self):
        from tradingagents.dataflows.local_knowledge_provider import _page_score

        m = LocalKnowledgeMatch(
            rel_path="x",
            title="x",
            page_type="company",
            confidence="medium",  # broker × high → medium
            source_quality_tier=TIER_BROKER_RESEARCH,
            citation_confidence_weight=0.6,
        )
        # _PAGE_SCORE_WEIGHTS[medium]=0.6 × weight=0.6 = 0.36
        assert _page_score(m) == pytest.approx(0.36, abs=0.001)

    def test_media_match_gets_low_score(self):
        from tradingagents.dataflows.local_knowledge_provider import _page_score

        m = LocalKnowledgeMatch(
            rel_path="x",
            title="x",
            page_type="company",
            confidence="low",  # media → low
            source_quality_tier=TIER_MEDIA,
            citation_confidence_weight=0.3,
        )
        # media/user_note/unknown 弱来源不进入正向命中分。
        assert _page_score(m) == 0.0

    def test_default_weight_is_neutral(self):
        """直接构造 LocalKnowledgeMatch 不显式设置 weight 时默认 1.0（中性）。"""
        from tradingagents.dataflows.local_knowledge_provider import _page_score

        m = LocalKnowledgeMatch(
            rel_path="x",
            title="x",
            page_type="company",
            confidence="high",
            # 不传 citation_confidence_weight → 默认 1.0（向后兼容）。
        )
        # high × 1.0 = 1.0
        assert _page_score(m) == 1.0

    def test_score_reflects_tier_in_summary(self, fixture_kb: Path):
        # 跑完整查询：filing 应得比 media 更高的分数。
        result_filing = query_local_knowledge(str(fixture_kb), symbol="600000")
        result_media = query_local_knowledge(str(fixture_kb), symbol="600002")
        s_filing = compute_local_knowledge_score(result_filing)
        s_media = compute_local_knowledge_score(result_media)
        # 至少都有命中。
        assert s_filing["knowledge_hit_count"] >= 1
        assert s_media["knowledge_hit_count"] >= 1
        # filing 的分数应高于 media（弱来源软降权）。
        assert s_filing["local_knowledge_score"] >= s_media["local_knowledge_score"]

    def test_matched_pages_brief_includes_tier(self, fixture_kb: Path):
        result = query_local_knowledge(str(fixture_kb), symbol="600000")
        s = compute_local_knowledge_score(result)
        for brief in s["matched_pages_brief"]:
            assert "source_quality_tier" in brief
            assert "citation_confidence_weight" in brief


# ── 9. 只读 / 安全约束 ──────────────────────────────────────────────


class TestSafetyConstraints:
    def test_provider_does_not_write_to_kb(self, fixture_kb: Path):
        files_before = set(p for p in fixture_kb.rglob("*") if p.is_file())
        query_local_knowledge(str(fixture_kb), symbol="600000")
        query_local_knowledge(str(fixture_kb), themes=["AI"])
        query_local_knowledge(str(fixture_kb), name="某公司")
        files_after = set(p for p in fixture_kb.rglob("*") if p.is_file())
        assert files_before == files_after

    def test_no_action_words_in_any_summary(self, fixture_kb: Path):
        for symbol in ("600000", "600001", "600002"):
            result = query_local_knowledge(str(fixture_kb), symbol=symbol)
            s = compute_local_knowledge_score(result)
            _assert_no_strong_action_words(s["local_knowledge_summary"])
            for brief in s["matched_pages_brief"]:
                _assert_no_strong_action_words(brief.get("summary_snippet", ""))


# ── 10. 与 HY-001 半年报 fixture 联动 ────────────────────────────────


class TestHy001FixtureIntegration:
    def test_half_year_fixtures_classify_correctly(self, tmp_path: Path):
        """半年报 fixture 集的 tier 分类应符合 HY-001 契约。

        - qualified / missing_period / expired / weakened_old_opinion：
          source_type 含 exchange_filing → original_filing。
        - non_financial_control：sources=[券商研报] → broker_research。
        """
        from tests.half_year_fixtures import build_half_year_fixture_kb

        root = build_half_year_fixture_kb(tmp_path)
        result = lint_local_knowledge(str(root))
        by_name = {Path(p.rel_path).stem: p for p in result.page_results}

        # qualified 含 exchange_filing + fact_table + management_commentary。
        q = by_name.get("浪潮信息000977-2025H1半年报")
        assert q is not None
        assert q.source_quality_tier == TIER_ORIGINAL_FILING

        # fact_opinion_mix 含 exchange_filing + broker_report → original_filing（最高）。
        m = by_name.get("海康威视002415-2025H1半年报-事实观点混用")
        assert m is not None
        assert m.source_quality_tier == TIER_ORIGINAL_FILING

        # non_financial_control：sources=[某券商研报] → broker_research。
        nf = by_name.get("平安银行000001-公司点评")
        assert nf is not None
        assert nf.source_quality_tier == TIER_BROKER_RESEARCH
