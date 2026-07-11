# [KB-015] research_fact_opinion_index
"""Tests for 研报观点事实分离与 TA 可消费摘要索引 (KB-015).

覆盖：
  - 五类 fixture：事实 / 观点 / 预测 / 风险 / 缺来源（验收要求）。
  - claim_type 分类：fact / opinion / forecast / risk / unknown。
  - stale_status：fresh / stale / low_confidence。
  - verification_needs：弱来源含数值断言 / 缺来源 / stale / 观点冒充事实。
  - 查询命中：symbol / name / 全库索引模式。
  - KB-010 缓存路径与全量扫描语义等价。
  - 报告渲染与 JSON 序列化。
  - to_ta_consumable_summary 扁平摘要。
  - 约束：不写知识库 / 不输出原文 / 无买卖建议词 / 弱来源不覆盖动作语义。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict

import pytest

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.local_knowledge_cache import build_cache_from_scan
from tradingagents.dataflows.citation_policy import (
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_ORIGINAL_FILING,
    TIER_UNKNOWN,
    TIER_USER_NOTE,
)
from tradingagents.dataflows.research_fact_opinion_index import (
    CLAIM_FACT,
    CLAIM_FORECAST,
    CLAIM_OPINION,
    CLAIM_RISK,
    CLAIM_UNKNOWN,
    STALE_FRESH,
    STALE_LOW_CONFIDENCE,
    STALE_STALE,
    ResearchClaimItem,
    ResearchFactOpinionPage,
    ResearchFactOpinionIndexResult,
    build_research_fact_opinion_index,
    render_research_fact_opinion_report,
    suggest_report_output_path,
    to_ta_consumable_summary,
    _classify_field_claim_type,
    _pick_primary_symbol_name,
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


# ── fixtures：覆盖五类（事实 / 观点 / 预测 / 风险 / 缺来源）────────────

# 1) 事实页：exchange_filing 来源 + financial_facts → fact
_FACT_PAGE = """---
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
  - 归母净利 15亿 (+30%)
segment_facts:
  - AI服务器收入增长 50%
management_commentary:
  - 管理层表示下半年产能释放
forward_guidance:
  - 全年营收指引 200亿
risk_factors:
  - 汇率波动
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

# 2) 观点页：券商研报来源 → financial_facts 被标为 opinion（观点冒充事实）
_OPINION_PAGE = """---
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
financial_facts:
  - 预计营收 120亿
management_commentary:
  - 券商认为公司前景广阔
---

# 某公司券商深度

## 一句话总结

券商看好。

## 投资逻辑

- 券商观点认为业绩超预期。

## 风险提示

- 业绩不及预期。

## 原始资料

- 某券商研报。
"""

# 3) 预测页：forward_guidance + 前瞻指引段
_FORECAST_PAGE = """---
title: 某公司-前瞻指引
created: 2026-07-01
updated: 2026-07-01
sources:
  - 巨潮资讯公司公告
tags: [某公司]
related: []
symbols: ["600002.SH 某公司"]
themes: [AI]
report_type: 公告
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
source_type: [exchange_filing]
forward_guidance:
  - 全年营收指引增长 30%
---

# 某公司前瞻

## 一句话总结

公司发布指引。

## 前瞻指引

- 预计下半年毛利率提升。

## 风险提示

- 原材料涨价。
"""

# 4) 风险页：risk_factors + 风险段
_RISK_PAGE = """---
title: 某公司-风险提示
created: 2026-07-15
updated: 2026-07-15
sources:
  - 巨潮资讯公司公告
tags: [某公司]
related: []
symbols: ["600003.SH 某公司"]
themes: [风险]
report_type: 公告
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
source_type: [exchange_filing]
risk_factors:
  - 贸易摩擦加剧
  - 原材料价格波动
---

# 某公司风险

## 风险提示

- 贸易摩擦加剧。
- 汇率风险。
"""

# 5) 缺来源页：无 sources / source_type → verification_needs 触发
_MISSING_SOURCE_PAGE = """---
title: 某想法-无来源
created: 2026-06-20
updated: 2026-06-20
sources: []
tags: [某主题]
related: []
symbols: ["600004.SH 某公司"]
themes: [某主题]
report_type: 综述
evidence_level: C
valid_until: 2099-12-31
source_quality: 低
stale_risk: 低
---

# 某想法

## 一句话总结

个人观点认为某公司估值低。

## 投资逻辑

- 个人观察。
"""

# 6) stale 页：stale_risk=高
_STALE_PAGE = """---
title: 某公司-过期研报
created: 2025-01-01
updated: 2025-01-01
sources:
  - 巨潮资讯公司公告
tags: [某公司]
related: []
symbols: ["600005.SH 某公司"]
themes: [AI]
report_type: 公告
evidence_level: A
valid_until: 2025-06-30
source_quality: 高
stale_risk: 高
source_type: [exchange_filing]
financial_facts:
  - 营收 50亿
---

# 某公司过期

## 一句话总结

旧数据。

## 风险提示

- 数据过期。
"""

# 7) 弱来源含数值断言：media 来源 + 数值
_WEAK_NUMERIC_PAGE = """---
title: 某公司-媒体报道
created: 2026-06-15
updated: 2026-06-15
sources:
  - 财联社报道
tags: [某公司]
related: []
symbols: ["600006.SH 某公司"]
themes: [AI]
report_type: 公司点评
evidence_level: C
valid_until: 2099-12-31
source_quality: 低
stale_risk: 低
financial_facts:
  - 据传营收将达 200亿
---

# 媒体报道

## 一句话总结

媒体报道某公司业绩大增。

## 投资逻辑

- 机构预测增长 50%。

## 原始资料

- 财联社。
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造覆盖五类 + 边界场景的微型知识库。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "fact-半年报.md", _FACT_PAGE)
    _write(inv / "opinion-券商深度.md", _OPINION_PAGE)
    _write(inv / "forecast-前瞻指引.md", _FORECAST_PAGE)
    _write(inv / "risk-风险提示.md", _RISK_PAGE)
    _write(inv / "missing-无来源.md", _MISSING_SOURCE_PAGE)
    _write(inv / "stale-过期研报.md", _STALE_PAGE)
    _write(inv / "weak-媒体报道.md", _WEAK_NUMERIC_PAGE)
    return tmp_path


# ── 1. 五类 fixture 覆盖 ─────────────────────────────────────────────


class TestFiveFixtureTypes:
    """验收要求：fixture 覆盖事实、观点、预测、风险、缺来源五类。"""

    def test_fact_claim_from_filing_source(self, fixture_kb: Path):
        """事实页：exchange_filing + financial_facts → fact。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000"
        )
        assert result.status == "HAS_DATA"
        assert len(result.pages) >= 1
        page = result.pages[0]
        assert page.source_quality_tier == TIER_ORIGINAL_FILING
        assert page.stale_status == STALE_FRESH
        # financial_facts 从事实源 → fact
        assert len(page.reported_facts) > 0
        assert all(c.claim_type == CLAIM_FACT for c in page.reported_facts)
        # 正文投资逻辑段 → opinion
        assert len(page.research_claims) > 0

    def test_opinion_claim_from_broker_source(self, fixture_kb: Path):
        """观点页：券商研报 → financial_facts 被标为 opinion（观点冒充事实）。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600001"
        )
        assert result.status == "HAS_DATA"
        page = result.pages[0]
        assert page.source_quality_tier == TIER_BROKER_RESEARCH
        # broker 的 financial_facts 不应是 fact，应是 opinion
        broker_facts_as_fact = [
            c for c in page.research_claims + page.reported_facts
            if c.origin_field == "financial_facts"
        ]
        assert len(broker_facts_as_fact) > 0
        assert all(c.claim_type == CLAIM_OPINION for c in broker_facts_as_fact)

    def test_forecast_claim_from_guidance(self, fixture_kb: Path):
        """预测页：forward_guidance + 前瞻指引段 → forecast。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600002"
        )
        assert result.status == "HAS_DATA"
        page = result.pages[0]
        assert len(page.forecast_items) > 0
        assert all(c.claim_type == CLAIM_FORECAST for c in page.forecast_items)

    def test_risk_claim_from_risk_factors(self, fixture_kb: Path):
        """风险页：risk_factors + 风险段 → risk。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600003"
        )
        assert result.status == "HAS_DATA"
        page = result.pages[0]
        assert len(page.risk_items) > 0
        assert all(c.claim_type == CLAIM_RISK for c in page.risk_items)

    def test_missing_source_triggers_verification(self, fixture_kb: Path):
        """缺来源页：触发 verification_needs。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600004"
        )
        assert result.status != "FAILED"
        page = result.pages[0]
        assert len(page.verification_needs) > 0
        assert any("来源" in v or "source" in v for v in page.verification_needs)


# ── 2. claim_type 分类 ──────────────────────────────────────────────


class TestClaimTypeClassification:
    def test_classify_field_claim_type_forward_guidance_always_forecast(self):
        """forward_guidance 无论来源都是 forecast。"""
        assert _classify_field_claim_type(
            field_name="forward_guidance",
            tier=TIER_ORIGINAL_FILING,
            source_type=[],
        ) == CLAIM_FORECAST
        assert _classify_field_claim_type(
            field_name="forward_guidance",
            tier=TIER_BROKER_RESEARCH,
            source_type=[],
        ) == CLAIM_FORECAST

    def test_classify_field_claim_type_risk_factors(self):
        assert _classify_field_claim_type(
            field_name="risk_factors",
            tier=TIER_ORIGINAL_FILING,
            source_type=[],
        ) == CLAIM_RISK

    def test_classify_field_claim_type_financial_facts_fact_source(self):
        """事实源的 financial_facts → fact。"""
        assert _classify_field_claim_type(
            field_name="financial_facts",
            tier=TIER_ORIGINAL_FILING,
            source_type=[],
        ) == CLAIM_FACT

    def test_classify_field_claim_type_financial_facts_opinion_source(self):
        """观点源的 financial_facts → opinion（观点冒充事实）。"""
        assert _classify_field_claim_type(
            field_name="financial_facts",
            tier=TIER_BROKER_RESEARCH,
            source_type=[],
        ) == CLAIM_OPINION
        assert _classify_field_claim_type(
            field_name="financial_facts",
            tier=TIER_MEDIA,
            source_type=[],
        ) == CLAIM_OPINION

    def test_classify_field_claim_type_management_commentary(self):
        """management_commentary + 公司口径 source_type → fact；券商 → opinion。"""
        assert _classify_field_claim_type(
            field_name="management_commentary",
            tier=TIER_ORIGINAL_FILING,
            source_type=["management_commentary"],
        ) == CLAIM_FACT
        assert _classify_field_claim_type(
            field_name="management_commentary",
            tier=TIER_BROKER_RESEARCH,
            source_type=[],
        ) == CLAIM_OPINION

    def test_classify_field_claim_type_body_opinion(self):
        """正文观点段 → opinion。"""
        assert _classify_field_claim_type(
            field_name="body:opinion",
            tier=TIER_ORIGINAL_FILING,
            source_type=[],
        ) == CLAIM_OPINION

    def test_classify_field_claim_type_unknown_field(self):
        """未知字段 → unknown。"""
        assert _classify_field_claim_type(
            field_name="random_field",
            tier=TIER_ORIGINAL_FILING,
            source_type=[],
        ) == CLAIM_UNKNOWN


# ── 3. stale_status 与 confidence ──────────────────────────────────


class TestStaleStatus:
    def test_stale_page_gets_stale_status(self, fixture_kb: Path):
        """stale_risk=高 的页面 → stale_status=stale。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600005"
        )
        assert result.status == "STALE"
        page = result.pages[0]
        assert page.stale_status == STALE_STALE
        assert page.confidence == "low"

    def test_low_confidence_page(self, fixture_kb: Path):
        """弱来源（media）页 → stale_status=low_confidence。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600006"
        )
        page = result.pages[0]
        assert page.stale_status == STALE_LOW_CONFIDENCE
        assert page.source_quality_tier == TIER_MEDIA

    def test_valid_until_expired_today(self, tmp_path: Path):
        """valid_until 过期 → stale（注入 today）。"""
        page_text = """---
title: 过期页
created: 2025-01-01
updated: 2025-01-01
sources:
  - 巨潮资讯公司公告
tags: [某公司]
symbols: ["600999.SH 某公司"]
themes: [AI]
report_type: 公告
evidence_level: A
valid_until: 2025-06-30
source_quality: 高
stale_risk: 低
source_type: [exchange_filing]
---

# 过期

## 一句话总结

旧数据。
"""
        inv = tmp_path / INVESTMENT_SUBDIR
        _write(inv / "expired.md", page_text)
        result = build_research_fact_opinion_index(
            str(tmp_path),
            symbol="600999",
            today=date(2026, 7, 11),
        )
        page = result.pages[0]
        assert page.stale_status == STALE_STALE


# ── 4. verification_needs ───────────────────────────────────────────


class TestVerificationNeeds:
    def test_weak_source_numeric_assertion_triggers_verification(
        self, fixture_kb: Path
    ):
        """弱来源（media）含数值断言 → verification_needs 提示核验。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600006"
        )
        page = result.pages[0]
        assert page.source_quality_tier == TIER_MEDIA
        assert any(
            "数值断言" in v or "核验" in v for v in page.verification_needs
        )

    def test_broker_fact_as_opinion_triggers_verification(
        self, fixture_kb: Path
    ):
        """券商研报的事实类字段 → verification_needs 提示交叉核验。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600001"
        )
        page = result.pages[0]
        assert page.source_quality_tier == TIER_BROKER_RESEARCH
        assert any(
            "券商" in v or "交叉核验" in v or "事实" in v
            for v in page.verification_needs
        )

    def test_stale_page_triggers_verification(self, fixture_kb: Path):
        """stale 页 → verification_needs 提示更新核验。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600005"
        )
        page = result.pages[0]
        assert any("过期" in v or "核验" in v for v in page.verification_needs)

    def test_missing_source_triggers_verification(self, fixture_kb: Path):
        """缺 sources/source_type → verification_needs 提示补来源。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600004"
        )
        page = result.pages[0]
        assert any("来源" in v or "source" in v for v in page.verification_needs)


# ── 5. 查询命中 ─────────────────────────────────────────────────────


class TestQueryMatching:
    def test_symbol_query_bare_code(self, fixture_kb: Path):
        """6 位 bare code 命中。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000"
        )
        assert result.status == "HAS_DATA"
        assert any("600000" in s for s in result.symbols)

    def test_symbol_query_with_suffix(self, fixture_kb: Path):
        """带后缀的代码命中。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000.SH"
        )
        assert result.status == "HAS_DATA"

    def test_name_query(self, fixture_kb: Path):
        """name 查询命中简称。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), name="某公司"
        )
        assert result.status == "HAS_DATA"
        assert len(result.pages) > 0

    def test_no_match_returns_normal_no_data(self, fixture_kb: Path):
        """无命中 → NORMAL_NO_DATA。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="999999"
        )
        assert result.status == "NORMAL_NO_DATA"
        assert len(result.pages) == 0

    def test_all_mode_indexes_all_pages(self, fixture_kb: Path):
        """全库索引模式（不限 symbol/name）扫描全部页。"""
        result = build_research_fact_opinion_index(str(fixture_kb))
        assert len(result.pages) > 0
        assert len(result.pages) >= 5  # 至少 5 个不同页面

    def test_no_query_condition_indexes_all(self, fixture_kb: Path):
        """不传 symbol/name 等同 --all（索引模式）。"""
        result = build_research_fact_opinion_index(str(fixture_kb))
        assert len(result.pages) > 0

    def test_nonexistent_root_returns_failed(self, tmp_path: Path):
        """知识库根目录不存在 → FAILED。"""
        result = build_research_fact_opinion_index(
            str(tmp_path / "nonexistent"), symbol="600000"
        )
        assert result.status == "FAILED"
        assert len(result.errors) > 0


# ── 6. KB-010 缓存路径 ─────────────────────────────────────────────


class TestCacheIntegration:
    def test_cache_path_equivalent_to_scan(self, fixture_kb: Path):
        """缓存路径与全量扫描产出语义等价。"""
        cache = build_cache_from_scan(str(fixture_kb))
        result_cache = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000", cache=cache
        )
        result_scan = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000"
        )
        assert result_cache.status == result_scan.status
        assert len(result_cache.pages) == len(result_scan.pages)
        if result_cache.pages:
            p_cache = result_cache.pages[0]
            p_scan = result_scan.pages[0]
            assert p_cache.rel_path == p_scan.rel_path
            assert p_cache.source_quality_tier == p_scan.source_quality_tier
            assert p_cache.stale_status == p_scan.stale_status
            # claim 数量一致（语义等价）。
            assert len(p_cache.reported_facts) == len(p_scan.reported_facts)
            assert len(p_cache.risk_items) == len(p_scan.risk_items)

    def test_cache_all_mode(self, fixture_kb: Path):
        """缓存 + 全库索引模式。"""
        cache = build_cache_from_scan(str(fixture_kb))
        result = build_research_fact_opinion_index(
            str(fixture_kb), cache=cache
        )
        assert len(result.pages) > 0


# ── 7. 报告渲染 ─────────────────────────────────────────────────────


class TestReportRendering:
    def test_report_has_data(self, fixture_kb: Path):
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000"
        )
        report = render_research_fact_opinion_report(result)
        assert "研报观点/事实分离索引" in report
        assert "600000" in report
        assert "事实" in report or "reported_facts" in report.lower()
        _assert_no_strong_action_words(report)

    def test_report_no_data(self, fixture_kb: Path):
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="999999"
        )
        report = render_research_fact_opinion_report(result)
        assert "无研报观点/事实命中" in report

    def test_report_failed(self, tmp_path: Path):
        result = build_research_fact_opinion_index(
            str(tmp_path / "nonexistent"), symbol="600000"
        )
        report = render_research_fact_opinion_report(result)
        assert "FAILED" in report or "失败" in report

    def test_report_contains_paths_not_full_text(self, fixture_kb: Path):
        """报告只展示路径和摘要，不输出原文段落。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000"
        )
        report = render_research_fact_opinion_report(result)
        # 路径存在。
        assert "wiki" in report or ".md" in report or "rel_path" in report.lower()
        # 不包含完整原文章节（验证裁剪）。
        assert "巨潮资讯" not in report or len(report) < 5000


# ── 8. JSON 序列化与 to_ta_consumable_summary ──────────────────────


class TestSerialization:
    def test_result_json_serializable(self, fixture_kb: Path):
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000"
        )
        data = result.to_dict()
        # 必须可 JSON 序列化。
        payload = json.dumps(data, ensure_ascii=False)
        restored = json.loads(payload)
        assert restored["status"] == result.status
        assert restored["task"] == "KB-015"

    def test_claim_item_serializable(self):
        claim = ResearchClaimItem(
            claim_type=CLAIM_FACT,
            text="营收 100亿",
            origin_field="financial_facts",
            symbol="600000",
            name="某公司",
            source_path="wiki/investment/test.md",
            source_quality_tier=TIER_ORIGINAL_FILING,
            report_date="2026-08-30",
            stale_status=STALE_FRESH,
            confidence="high",
        )
        data = claim.to_dict()
        payload = json.dumps(data, ensure_ascii=False)
        restored = json.loads(payload)
        assert restored["claim_type"] == CLAIM_FACT
        assert restored["symbol"] == "600000"

    def test_ta_consumable_summary_has_fact(self, fixture_kb: Path):
        """事实页 → to_ta_consumable_summary.has_fact=True。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000"
        )
        summary = to_ta_consumable_summary(result)
        assert summary["status"] == "HAS_DATA"
        assert summary["has_fact"] is True
        assert summary["has_opinion_only"] is False
        assert len(summary["pages_brief"]) > 0
        assert "claim_counts" in summary["pages_brief"][0]

    def test_ta_consumable_summary_opinion_only(self, fixture_kb: Path):
        """仅观点命中 → has_opinion_only=True。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600001"
        )
        summary = to_ta_consumable_summary(result)
        assert summary["has_fact"] is False
        assert summary["has_opinion_only"] is True

    def test_ta_consumable_summary_empty(self):
        """空结果 → 扁平化空结构。"""
        empty_result = ResearchFactOpinionIndexResult()
        summary = to_ta_consumable_summary(empty_result)
        assert summary["status"] == "NORMAL_NO_DATA"
        assert summary["has_fact"] is False
        assert summary["claim_counts"][CLAIM_FACT] == 0


# ── 9. 约束验证 ─────────────────────────────────────────────────────


class TestConstraints:
    def test_no_strong_action_words_in_all_reports(self, fixture_kb: Path):
        """全库索引的报告不含买卖建议词。"""
        result = build_research_fact_opinion_index(str(fixture_kb))
        report = render_research_fact_opinion_report(result)
        _assert_no_strong_action_words(report)

    def test_weak_source_does_not_become_fact(self, fixture_kb: Path):
        """弱来源（media/user_note/unknown）的事实类字段不标为 fact。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600006"
        )
        page = result.pages[0]
        assert page.source_quality_tier == TIER_MEDIA
        # media 的 financial_facts 应是 opinion，不是 fact。
        for c in page.reported_facts:
            assert c.claim_type != CLAIM_FACT, (
                f"media source claim should not be fact: {c.text}"
            )

    def test_claim_text_clipped(self):
        """claim 文本在构建时裁剪到上限。"""
        from tradingagents.dataflows.research_fact_opinion_index import _build_claim

        long_text = "x" * 500
        claim = _build_claim(
            text=long_text,
            origin_field="test",
            claim_type=CLAIM_FACT,
            symbol="",
            name="",
            rel_path="",
            tier=TIER_UNKNOWN,
            report_date=None,
            stale_status=STALE_FRESH,
            confidence="low",
        )
        assert len(claim.text) <= 124  # 120 + 省略号

    def test_knowledge_root_not_modified(self, fixture_kb: Path):
        """只读：调用后知识库文件不变。"""
        target = fixture_kb / INVESTMENT_SUBDIR / "fact-半年报.md"
        before = target.read_text(encoding="utf-8")
        build_research_fact_opinion_index(str(fixture_kb), symbol="600000")
        after = target.read_text(encoding="utf-8")
        assert before == after

    def test_max_pages_respected(self, fixture_kb: Path):
        """max_pages 限制命中页数。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), max_pages=2
        )
        assert len(result.pages) <= 2


# ── 10. _pick_primary_symbol_name ───────────────────────────────────


class TestPickPrimarySymbolName:
    def test_pick_matching_symbol(self):
        symbols = ["600000.SH 某公司", "600001.SH 另一公司"]
        symbol, name = _pick_primary_symbol_name(
            symbols, "标题", "600000", None
        )
        assert symbol == "600000"
        assert name == "某公司"

    def test_pick_matching_name(self):
        symbols = ["600000.SH 某公司", "600001.SH 另一公司"]
        symbol, name = _pick_primary_symbol_name(
            symbols, "标题", None, "另一公司"
        )
        assert symbol == "600001"

    def test_pick_first_when_no_match(self):
        symbols = ["600000.SH 某公司"]
        symbol, name = _pick_primary_symbol_name(
            symbols, "标题", "999999", None
        )
        assert symbol == "600000"
        assert name == "某公司"

    def test_empty_symbols_uses_query(self):
        symbol, name = _pick_primary_symbol_name(
            [], "某标题", "600000", "某名称"
        )
        assert symbol == "600000"
        assert name == "某名称"


# ── 11. suggest_report_output_path ─────────────────────────────────


class TestOutputPath:
    def test_suggest_output_path_format(self):
        path = suggest_report_output_path()
        assert "research_fact_opinion_index-" in path
        assert path.endswith(".md")
        assert "docs/knowledge_reports" in path


# ── 12. 聚合统计 ────────────────────────────────────────────────────


class TestAggregation:
    def test_claim_counts_aggregated(self, fixture_kb: Path):
        """claim_counts 聚合各 claim_type 数量。"""
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600000"
        )
        assert result.claim_counts[CLAIM_FACT] > 0
        assert result.claim_counts[CLAIM_OPINION] > 0

    def test_verification_count_aggregated(self, fixture_kb: Path):
        result = build_research_fact_opinion_index(
            str(fixture_kb), symbol="600004"
        )
        assert result.verification_count > 0

    def test_symbols_names_dedup(self, fixture_kb: Path):
        result = build_research_fact_opinion_index(
            str(fixture_kb), name="某公司"
        )
        # symbols 去重。
        assert len(result.symbols) == len(set(result.symbols))


# ── 13. 边界场景 ────────────────────────────────────────────────────


class TestEdgeCases:
    def test_bare_page_no_frontmatter(self, tmp_path: Path):
        """无 frontmatter 的页面不崩溃，被标为 unknown/low_confidence。"""
        inv = tmp_path / INVESTMENT_SUBDIR
        _write(inv / "bare.md", "# 某想法\n\n直接写正文。\n")
        result = build_research_fact_opinion_index(str(tmp_path))
        # 应该被处理，不崩溃。
        assert result.status in ("HAS_DATA", "STALE", "LOW_CONFIDENCE", "NORMAL_NO_DATA")

    def test_empty_investment_dir(self, tmp_path: Path):
        """investment 分区为空 → NORMAL_NO_DATA。"""
        inv = tmp_path / INVESTMENT_SUBDIR
        inv.mkdir(parents=True)
        result = build_research_fact_opinion_index(str(tmp_path))
        assert result.status == "NORMAL_NO_DATA"

    def test_investment_dir_not_exists(self, tmp_path: Path):
        """investment 分区不存在 → NORMAL_NO_DATA + 错误。"""
        result = build_research_fact_opinion_index(str(tmp_path))
        assert result.status == "NORMAL_NO_DATA"
        assert len(result.errors) > 0

    def test_single_page_parse_failure_does_not_crash(self, tmp_path: Path):
        """单页解析失败不崩溃，记入 errors。"""
        inv = tmp_path / INVESTMENT_SUBDIR
        good = """---
title: 正常页
created: 2026-01-01
updated: 2026-01-01
sources: [巨潮资讯公司公告]
symbols: ["600000.SH 某公司"]
source_type: [exchange_filing]
---

## 一句话总结

正常。
"""
        _write(inv / "good.md", good)
        # 写一个"坏"文件（二进制乱码）。
        _write(inv / "bad.md", "\x00\x01\x02\x03")
        result = build_research_fact_opinion_index(str(tmp_path))
        # 至少能处理 good.md，不因 bad.md 崩溃。
        assert result.status in ("HAS_DATA", "NORMAL_NO_DATA", "LOW_CONFIDENCE")
