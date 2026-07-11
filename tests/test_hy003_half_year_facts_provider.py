# [HY-003] half_year_facts_provider
"""Tests for 半年报事实表本地索引与只读查询 provider (HY-003).

覆盖（对应任务验收方式）：
  - fixture 覆盖：有事实、无半年报、过期、字段冲突、缓存损坏重建。
  - 查询结果可 JSON 序列化。
  - 不影响现有 local knowledge 普通查询。
  - 缺字段/过期/冲突输出 data_status，不得假装可用。
  - KB-010 cache 集成：缓存路径与全量扫描语义等价。
  - 数值事实抽取：营收/净利/毛利率/现金流/同比变化。
  - 只读安全性：不写知识库目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tradingagents.dataflows.half_year_facts_provider import (
    DATA_CONFLICT,
    DATA_FRESH,
    DATA_MISSING_FACTS,
    DATA_MISSING_PERIOD,
    DATA_OPINION_ONLY,
    DATA_STALE,
    HalfYearFactsPage,
    HalfYearFactsQueryResult,
    ParsedMetric,
    TASK_CODE,
    VENDOR,
    build_half_year_facts_raw_evidence_entry,
    query_failed_entry,
    query_half_year_facts,
    render_half_year_facts_block,
    render_half_year_facts_report,
    suggest_query_output_path,
    _build_facts_page,
    _build_facts_page_from_cache,
    _classify_metric,
    _detect_conflicts,
    _parse_single_fact,
    _parse_value_and_change,
)
from tradingagents.dataflows.local_knowledge_cache import (
    CachedPageData,
    get_or_build_cache,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    query_local_knowledge,
)
from tests.half_year_fixtures import (
    FIXTURE_SPECS,
    FIXTURES_BY_NAME,
    build_half_year_fixture_kb,
)


# ── 工具 ──────────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_full_kb(tmp_path: Path) -> Path:
    """写入全部 KB-013 fixture 样本。"""
    return build_half_year_fixture_kb(tmp_path)


# ── 数值事实抽取 ──────────────────────────────────────────────────────


class TestParseSingleFact:
    """单条 financial_facts 文本的指标识别与数值抽取。"""

    def test_revenue_with_yoy(self) -> None:
        m = _parse_single_fact("营收 150.2亿 (+30.1% YoY)")
        assert m.metric_key == "revenue"
        assert m.value == "150.2亿"
        assert m.change == "+30.1%"

    def test_net_profit(self) -> None:
        m = _parse_single_fact("归母净利 18.5亿 (+45.0% YoY)")
        assert m.metric_key == "net_profit"
        assert m.value == "18.5亿"
        assert m.change == "+45.0%"

    def test_gross_margin(self) -> None:
        m = _parse_single_fact("毛利率 25.3% (+1.2pp YoY)")
        assert m.metric_key == "gross_margin"
        assert m.value == "25.3%"
        assert m.change == "+1.2pp"

    def test_operating_cash_flow(self) -> None:
        m = _parse_single_fact("经营性现金流 22.1亿 (+18%)")
        assert m.metric_key == "operating_cash_flow"
        assert m.value == "22.1亿"
        assert m.change == "+18%"

    def test_negative_change(self) -> None:
        m = _parse_single_fact("储能系统营收 120亿 (-20.0% YoY)")
        assert m.metric_key == "revenue"
        assert m.value == "120亿"
        assert m.change == "-20.0%"

    def test_unknown_metric_kept_as_other(self) -> None:
        m = _parse_single_fact("研发投入占比 8.5%")
        assert m.metric_key == "other"
        assert m.value is not None
        assert m.raw == "研发投入占比 8.5%"

    def test_raw_clipped_to_max(self) -> None:
        long_text = "营收 " + "1" * 200 + "亿 (+10%)"
        m = _parse_single_fact(long_text)
        assert len(m.raw) <= 122  # 120 + "…"
        assert m.raw.endswith("…")

    def test_empty_text(self) -> None:
        m = _parse_single_fact("")
        assert m.metric_key == "other"
        assert m.raw == ""
        assert m.value is None
        assert m.change is None


class TestClassifyMetric:
    def test_revenue_keywords(self) -> None:
        for kw in ("营收", "营业收入", "Revenue", "总收入"):
            key, _ = _classify_metric(kw)
            assert key == "revenue"

    def test_net_profit_keywords(self) -> None:
        for kw in ("归母净利", "净利润", "Net Profit"):
            key, _ = _classify_metric(kw)
            assert key == "net_profit"

    def test_other(self) -> None:
        key, _ = _classify_metric("存货周转率")
        assert key == "other"


class TestParseValueAndChange:
    def test_no_change(self) -> None:
        v, c = _parse_value_and_change("营收 150亿")
        assert v == "150亿"
        assert c is None

    def test_no_value(self) -> None:
        v, c = _parse_value_and_change("公司增长强劲")
        # "增长" 不含数值；可能匹配不到 value。
        assert v is None or c is None


# ── 主查询：fixture 覆盖 ─────────────────────────────────────────────


class TestQueryHalfYearFactsFixtureCoverage:
    """覆盖任务验收要求的 5 类 fixture 场景。"""

    def test_qualified_half_year_has_facts(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), symbol="000977")
        assert result.status == STATUS_HAS_DATA
        assert len(result.pages) == 1
        page = result.pages[0]
        assert page.data_status == DATA_FRESH
        assert page.financial_period == "2025H1"
        assert page.disclosure_date == "2026-08-29"
        assert "exchange_filing" in page.source_type
        assert page.is_stale is False
        assert page.is_opinion_only is False
        # 关键指标抽取
        metric_keys = {m.metric_key for m in page.financial_facts}
        assert "revenue" in metric_keys
        assert "net_profit" in metric_keys
        # latest_period
        assert result.latest_period == "2025H1"
        assert result.latest_disclosure_date == "2026-08-29"

    def test_no_half_year_page_returns_no_data(self, tmp_path: Path) -> None:
        # 只有非财报页（控制组）
        root = build_half_year_fixture_kb(
            tmp_path, include=["non_financial_control"]
        )
        result = query_half_year_facts(str(root), symbol="000001")
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.pages == []
        assert result.data_status == DATA_MISSING_FACTS

    def test_expired_page_is_stale(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["expired"])
        # expired fixture valid_until=2025-12-31，用 2026-06-29 作为 today。
        from datetime import date

        result = query_half_year_facts(
            str(root), symbol="600519", today=date(2026, 6, 29)
        )
        assert result.status == STATUS_STALE
        assert len(result.pages) == 1
        page = result.pages[0]
        assert page.data_status == DATA_STALE
        assert page.is_stale is True
        assert page.financial_period == "2024H1"

    def test_missing_period_page(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(
            tmp_path, include=["missing_period"]
        )
        result = query_half_year_facts(str(root), symbol="603296")
        assert result.status == STATUS_LOW_CONFIDENCE
        assert len(result.pages) == 1
        page = result.pages[0]
        assert page.data_status == DATA_MISSING_PERIOD
        assert page.financial_period is None
        assert "financial_period" in page.missing_fields

    def test_opinion_only_page(self, tmp_path: Path) -> None:
        # 构造一个 source_type 全为 broker/media 的半年报页。
        content = """---
title: 测试-观点冒充事实
created: 2026-08-29
updated: 2026-08-29
symbols: ["999999.SZ 测试公司"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [broker_report, media]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [测试风险]
---

# 测试

## 一句话总结

测试。

## 风险提示

- 测试。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "test.md", content)
        result = query_half_year_facts(str(tmp_path), symbol="999999")
        assert result.status == STATUS_LOW_CONFIDENCE
        page = result.pages[0]
        assert page.data_status == DATA_OPINION_ONLY
        assert page.is_opinion_only is True

    def test_no_query_condition_returns_no_data(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root))
        assert result.status == STATUS_NORMAL_NO_DATA
        assert any("未提供查询条件" in e for e in result.errors)

    def test_knowledge_root_not_exist_returns_failed(self, tmp_path: Path) -> None:
        result = query_half_year_facts(
            str(tmp_path / "nonexistent"), symbol="000977"
        )
        assert result.status == STATUS_FAILED
        assert any("不存在" in e for e in result.errors)


class TestQueryByName:
    """name 匹配查询。"""

    def test_query_by_name_hits(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), name="浪潮信息")
        assert result.status == STATUS_HAS_DATA
        assert len(result.pages) == 1
        assert "000977" in result.pages[0].symbols[0]

    def test_query_by_name_miss(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), name="不存在的公司")
        assert result.status == STATUS_NORMAL_NO_DATA


class TestSymbolNormalization:
    """symbol 后缀归一化。"""

    def test_symbol_with_suffix(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), symbol="000977.SZ")
        assert result.status == STATUS_HAS_DATA

    def test_symbol_bare_code(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), symbol="000977")
        assert result.status == STATUS_HAS_DATA


# ── 事实冲突检测 ──────────────────────────────────────────────────────


class TestConflictDetection:
    """同 symbol 同 period 多页关键指标数值冲突。"""

    def test_conflict_marks_pages(self, tmp_path: Path) -> None:
        # 两页同 symbol 同 period，营收数值不同。
        page_a = """---
title: 公司A-2025H1-来源1
created: 2026-08-29
updated: 2026-08-29
symbols: ["888888.SZ 冲突公司"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [风险A]
---

# A

## 一句话总结

A。

## 风险提示

- A。
"""
        page_b = """---
title: 公司A-2025H1-来源2
created: 2026-08-29
updated: 2026-08-29
symbols: ["888888.SZ 冲突公司"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-30
source_type: [fact_table]
financial_facts:
  - 营收 150亿 (+15%)
risk_factors: [风险B]
---

# B

## 一句话总结

B。

## 风险提示

- B。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "a.md", page_a)
        _write(inv / "b.md", page_b)
        result = query_half_year_facts(str(tmp_path), symbol="888888")
        assert len(result.pages) == 2
        conflict_pages = [p for p in result.pages if p.data_status == DATA_CONFLICT]
        assert len(conflict_pages) >= 1
        assert result.data_status == DATA_CONFLICT
        # 冲突描述包含指标名
        assert any(
            p.conflict_detail and "revenue" in p.conflict_detail
            for p in conflict_pages
        )

    def test_same_value_no_conflict(self, tmp_path: Path) -> None:
        # 两页同 period 同营收数值 → 不算冲突。
        base = """---
title: {title}
created: 2026-08-29
updated: 2026-08-29
symbols: ["777777.SZ 一致公司"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [exchange_filing]
financial_facts:
  - 营收 200亿 (+10%)
risk_factors: [风险]
---

# {title}

## 一句话总结

一致。

## 风险提示

- 一致。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "a.md", base.format(title="一致A"))
        _write(inv / "b.md", base.format(title="一致B"))
        result = query_half_year_facts(str(tmp_path), symbol="777777")
        assert all(p.data_status != DATA_CONFLICT for p in result.pages)

    def test_different_period_no_conflict(self, tmp_path: Path) -> None:
        # 同 symbol 不同 period → 不冲突。
        page_a = """---
title: A-2024H1
symbols: ["666666.SZ 分期公司"]
report_type: 半年报
financial_period: 2024H1
disclosure_date: 2024-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [风险]
---
## 一句话总结
A。
## 风险提示
- A。
"""
        page_b = """---
title: B-2025H1
symbols: ["666666.SZ 分期公司"]
report_type: 半年报
financial_period: 2025H1
disclosure_date: 2025-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 150亿 (+50%)
risk_factors: [风险]
---
## 一句话总结
B。
## 风险提示
- B。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "a.md", page_a)
        _write(inv / "b.md", page_b)
        result = query_half_year_facts(str(tmp_path), symbol="666666")
        assert all(p.data_status == DATA_FRESH for p in result.pages)


class TestDetectConflictsDirect:
    """直接测试 _detect_conflicts 函数。"""

    def test_conflict_between_two_pages(self) -> None:
        p1 = HalfYearFactsPage(
            rel_path="a.md",
            title="A",
            financial_period="2025H1",
            disclosure_date="2026-08-29",
            source_type=["exchange_filing"],
            financial_facts=[
                ParsedMetric("revenue", "营收", "营收 100亿", "100亿", "+10%")
            ],
            data_status=DATA_FRESH,
        )
        p2 = HalfYearFactsPage(
            rel_path="b.md",
            title="B",
            financial_period="2025H1",
            disclosure_date="2026-08-30",
            source_type=["fact_table"],
            financial_facts=[
                ParsedMetric("revenue", "营收", "营收 150亿", "150亿", "+15%")
            ],
            data_status=DATA_FRESH,
        )
        _detect_conflicts([p1, p2])
        assert p1.data_status == DATA_CONFLICT
        assert p2.data_status == DATA_CONFLICT
        assert p1.conflict_detail is not None

    def test_no_conflict_single_page(self) -> None:
        p = HalfYearFactsPage(
            rel_path="a.md",
            title="A",
            financial_period="2025H1",
            disclosure_date="2026-08-29",
            data_status=DATA_FRESH,
        )
        _detect_conflicts([p])
        assert p.data_status == DATA_FRESH

    def test_missing_period_skipped(self) -> None:
        p = HalfYearFactsPage(
            rel_path="a.md",
            title="A",
            financial_period=None,
            disclosure_date=None,
            data_status=DATA_MISSING_PERIOD,
        )
        _detect_conflicts([p])
        assert p.data_status == DATA_MISSING_PERIOD


# ── KB-010 缓存集成 ──────────────────────────────────────────────────


class TestCacheIntegration:
    """KB-010 cache 路径与全量扫描语义等价。"""

    def test_cache_path_equivalent_to_scan(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(
            tmp_path, include=["qualified", "expired", "missing_period"]
        )
        cache = get_or_build_cache(str(root), no_cache=True)
        result_cache = query_half_year_facts(
            str(root), symbol="000977", cache=cache
        )
        result_scan = query_half_year_facts(str(root), symbol="000977")
        assert result_cache.status == result_scan.status
        assert len(result_cache.pages) == len(result_scan.pages)
        for pc, ps in zip(result_cache.pages, result_scan.pages):
            assert pc.rel_path == ps.rel_path
            assert pc.financial_period == ps.financial_period
            assert pc.data_status == ps.data_status
            assert pc.is_stale == ps.is_stale

    def test_cache_does_not_write_knowledge_base(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        # 记录知识库文件 mtime。
        inv_files = list((root / "wiki" / "investment").glob("*.md"))
        mtimes_before = {f: f.stat().st_mtime_ns for f in inv_files}
        cache = get_or_build_cache(str(root), no_cache=True)
        query_half_year_facts(str(root), symbol="000977", cache=cache)
        mtimes_after = {f: f.stat().st_mtime_ns for f in inv_files}
        assert mtimes_before == mtimes_after

    def test_cache_rebuild_after_corruption(self, tmp_path: Path) -> None:
        """缓存损坏后重建，查询仍正确。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        cache_path = str(tmp_path / ".cache" / "knowledge_cache.json")
        # 先正常构建一次落盘缓存。
        get_or_build_cache(str(root), cache_path=cache_path)
        # 写入损坏的缓存 JSON。
        Path(cache_path).write_text("{ not valid json", encoding="utf-8")
        # get_or_build_cache 应自动回退重建。
        cache = get_or_build_cache(str(root), cache_path=cache_path)
        result = query_half_year_facts(str(root), symbol="000977", cache=cache)
        assert result.status == STATUS_HAS_DATA
        assert len(result.pages) == 1


class TestBuildFactsPageFromCache:
    """缓存驱动单页事实抽取。"""

    def test_qualified_page_from_cache(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        cache = get_or_build_cache(str(root), no_cache=True)
        # 找到 qualified 页的 CachedPageData。
        qualified_rel = None
        for rel, page in cache.pages.items():
            if "浪潮信息" in (page.title or ""):
                qualified_rel = rel
                break
        assert qualified_rel is not None
        cached_page = cache.pages[qualified_rel]
        hy_page = _build_facts_page_from_cache(cached_page)
        assert hy_page is not None
        assert hy_page.financial_period == "2025H1"
        assert hy_page.data_status == DATA_FRESH

    def test_non_half_year_page_returns_none(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(
            tmp_path, include=["non_financial_control"]
        )
        cache = get_or_build_cache(str(root), no_cache=True)
        for _rel, page in cache.pages.items():
            assert _build_facts_page_from_cache(page) is None


# ── JSON 序列化 ──────────────────────────────────────────────────────


class TestJsonSerializable:
    """查询结果可 JSON 序列化。"""

    def test_result_to_dict_json_serializable(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), symbol="000977")
        data = result.to_dict()
        json_str = json.dumps(data, ensure_ascii=False)
        assert json_str  # 非空
        restored = json.loads(json_str)
        assert restored["status"] == STATUS_HAS_DATA
        assert restored["task"] == TASK_CODE
        assert restored["vendor"] == VENDOR

    def test_page_to_dict_json_serializable(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), symbol="000977")
        page_dict = result.pages[0].to_dict()
        json_str = json.dumps(page_dict, ensure_ascii=False)
        restored = json.loads(json_str)
        assert restored["financial_period"] == "2025H1"

    def test_from_dict_roundtrip(self) -> None:
        page = HalfYearFactsPage(
            rel_path="test.md",
            title="Test",
            financial_period="2025H1",
            disclosure_date="2026-08-29",
            source_type=["exchange_filing"],
            financial_facts=[
                ParsedMetric("revenue", "营收", "营收 100亿", "100亿", "+10%")
            ],
            data_status=DATA_FRESH,
        )
        d = page.to_dict()
        restored = HalfYearFactsPage.from_dict(d)
        assert restored.rel_path == page.rel_path
        assert restored.financial_period == page.financial_period
        assert len(restored.financial_facts) == 1
        assert restored.financial_facts[0].metric_key == "revenue"

    def test_raw_evidence_entry_json_serializable(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), symbol="000977")
        entry = build_half_year_facts_raw_evidence_entry(
            result, "2026-08-30", "2026-08-30T10:00:00"
        )
        json_str = json.dumps(entry, ensure_ascii=False)
        restored = json.loads(json_str)
        assert restored["field"] == "half_year_facts"
        assert restored["vendor"] == VENDOR

    def test_failed_entry_json_serializable(self) -> None:
        entry = query_failed_entry(
            "2026-08-30", "2026-08-30T10:00:00", "测试错误"
        )
        json_str = json.dumps(entry, ensure_ascii=False)
        restored = json.loads(json_str)
        assert restored["status"] == STATUS_FAILED
        assert restored["error"] == "测试错误"


# ── 不影响现有 local knowledge 查询 ──────────────────────────────────


class TestNoRegressionOnLocalKnowledge:
    """HY-003 模块不影响 KB-003 普通查询。"""

    def test_local_knowledge_query_still_works(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_local_knowledge(str(root), symbol="000977")
        # KB-003 仍能命中。
        assert result.status in (STATUS_HAS_DATA,)
        assert len(result.matched_pages) >= 1

    def test_half_year_query_does_not_modify_kb(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        files_before = sorted(
            str(p.relative_to(root)) for p in root.rglob("*.md")
        )
        query_half_year_facts(str(root), symbol="000977")
        files_after = sorted(
            str(p.relative_to(root)) for p in root.rglob("*.md")
        )
        assert files_before == files_after


# ── 渲染 ─────────────────────────────────────────────────────────────


class TestRenderBlock:
    def test_render_has_data(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), symbol="000977")
        block = render_half_year_facts_block(result)
        assert "### 半年报事实" in block
        assert "2025H1" in block
        assert "exchange_filing" in block

    def test_render_no_data_returns_empty(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(
            tmp_path, include=["non_financial_control"]
        )
        result = query_half_year_facts(str(root), symbol="000001")
        block = render_half_year_facts_block(result)
        assert block == ""

    def test_render_failed_returns_empty(self, tmp_path: Path) -> None:
        result = query_half_year_facts(
            str(tmp_path / "nonexistent"), symbol="000977"
        )
        block = render_half_year_facts_block(result)
        assert block == ""

    def test_render_conflict_shows_warning(self, tmp_path: Path) -> None:
        page_a = """---
title: A-2025H1-冲突渲染A
symbols: ["555555.SZ 冲突渲染公司"]
report_type: 半年报
financial_period: 2025H1
disclosure_date: 2026-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [风险]
---
## 一句话总结
A。
## 风险提示
- A。
"""
        page_b = """---
title: B-2025H1-冲突渲染B
symbols: ["555555.SZ 冲突渲染公司"]
report_type: 半年报
financial_period: 2025H1
disclosure_date: 2026-08-30
valid_until: 2099-12-31
stale_risk: 低
source_type: [fact_table]
financial_facts:
  - 营收 200亿 (+20%)
risk_factors: [风险]
---
## 一句话总结
B。
## 风险提示
- B。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "a.md", page_a)
        _write(inv / "b.md", page_b)
        result = query_half_year_facts(str(tmp_path), symbol="555555")
        block = render_half_year_facts_block(result)
        assert "CONFLICT" in block
        assert "事实冲突" in block

    def test_render_report_has_metadata(self, tmp_path: Path) -> None:
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = query_half_year_facts(str(root), symbol="000977")
        report = render_half_year_facts_report(result)
        assert TASK_CODE in report
        assert "浪潮信息" in report or "000977" in report

    def test_render_opinion_only_label(self, tmp_path: Path) -> None:
        content = """---
title: 观点冒充-渲染测试
symbols: ["444444.SZ 渲染观点公司"]
report_type: 半年报
financial_period: 2025H1
disclosure_date: 2026-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [broker_report]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [风险]
---
## 一句话总结
测试。
## 风险提示
- 测试。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "opinion.md", content)
        result = query_half_year_facts(str(tmp_path), symbol="444444")
        block = render_half_year_facts_block(result)
        assert "观点冒充事实" in block


# ── 缺字段处理 ───────────────────────────────────────────────────────


class TestMissingFields:
    """缺字段页面正确标注 missing_fields。"""

    def test_missing_facts_warning(self, tmp_path: Path) -> None:
        content = """---
title: 缺事实-测试
symbols: ["333333.SZ 缺事实公司"]
report_type: 半年报
financial_period: 2025H1
disclosure_date: 2026-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
risk_factors: [风险]
---
## 一句话总结
无 financial_facts。
## 风险提示
- 无。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "no_facts.md", content)
        result = query_half_year_facts(str(tmp_path), symbol="333333")
        page = result.pages[0]
        assert page.data_status == DATA_MISSING_FACTS
        assert "financial_facts" in page.missing_fields

    def test_missing_disclosure_date(self, tmp_path: Path) -> None:
        content = """---
title: 缺披露日-测试
symbols: ["222222.SZ 缺披露公司"]
report_type: 半年报
financial_period: 2025H1
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [风险]
---
## 一句话总结
无披露日。
## 风险提示
- 无。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "no_disclosure.md", content)
        result = query_half_year_facts(str(tmp_path), symbol="222222")
        page = result.pages[0]
        # 缺披露日是 warning 不阻塞，data_status 仍 fresh。
        assert page.data_status == DATA_FRESH
        assert "disclosure_date" in page.missing_fields
        assert page.disclosure_date is None


# ── 排序与聚合 ───────────────────────────────────────────────────────


class TestSortingAndAggregation:
    def test_latest_period_picked(self, tmp_path: Path) -> None:
        page_a = """---
title: A-2024H1-排序
symbols: ["111111.SZ 排序公司"]
report_type: 半年报
financial_period: 2024H1
disclosure_date: 2024-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [风险]
---
## 一句话总结
A。
## 风险提示
- A。
"""
        page_b = """---
title: B-2025H1-排序
symbols: ["111111.SZ 排序公司"]
report_type: 半年报
financial_period: 2025H1
disclosure_date: 2025-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 150亿 (+50%)
risk_factors: [风险]
---
## 一句话总结
B。
## 风险提示
- B。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "a.md", page_a)
        _write(inv / "b.md", page_b)
        result = query_half_year_facts(str(tmp_path), symbol="111111")
        assert result.latest_period == "2025H1"
        # 最新 period 排在前。
        assert result.pages[0].financial_period == "2025H1"

    def test_risks_aggregated_dedup(self, tmp_path: Path) -> None:
        page_a = """---
title: A-风险聚合
symbols: ["101010.SZ 风险聚合公司"]
report_type: 半年报
financial_period: 2025H1
disclosure_date: 2026-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 100亿 (+10%)
risk_factors: [汇率, 客户集中度]
---
## 一句话总结
A。
## 风险提示
- A。
"""
        page_b = """---
title: B-风险聚合
symbols: ["101010.SZ 风险聚合公司"]
report_type: 半年报
financial_period: 2024H1
disclosure_date: 2024-08-29
valid_until: 2099-12-31
stale_risk: 低
source_type: [exchange_filing]
financial_facts:
  - 营收 80亿 (+5%)
risk_factors: [汇率, 原材料]
---
## 一句话总结
B。
## 风险提示
- B。
"""
        inv = tmp_path / "wiki" / "investment"
        _write(inv / "a.md", page_a)
        _write(inv / "b.md", page_b)
        result = query_half_year_facts(str(tmp_path), symbol="101010")
        # 去重后应含 汇率、客户集中度、原材料。
        assert "汇率" in result.risks
        assert "客户集中度" in result.risks
        assert "原材料" in result.risks
        # 汇率不重复。
        assert result.risks.count("汇率") == 1


# ── 全 fixture 集成 ──────────────────────────────────────────────────


class TestFullFixtureKb:
    """写入全部 KB-013 fixture 样本的集成测试。"""

    def test_all_fixtures_queryable(self, tmp_path: Path) -> None:
        root = _build_full_kb(tmp_path)
        # 每个 fixture 都能按其 symbol 查询到（非财报页除外）。
        for spec in FIXTURE_SPECS:
            if not spec.is_half_year:
                continue
            # 从 symbols 提取 bare code。
            sym = spec.expected_symbols[0].split()[0] if spec.expected_symbols else ""
            result = query_half_year_facts(str(root), symbol=sym)
            assert result.status in (
                STATUS_HAS_DATA,
                STATUS_STALE,
                STATUS_LOW_CONFIDENCE,
            ), f"{spec.name}: {result.status}"
            assert len(result.pages) >= 1

    def test_non_financial_control_not_picked(self, tmp_path: Path) -> None:
        root = _build_full_kb(tmp_path)
        result = query_half_year_facts(str(root), symbol="000001")
        # 控制组是 report_type=公司点评，不应进入半年报事实表。
        assert result.status == STATUS_NORMAL_NO_DATA
        assert all(
            "平安银行" not in p.title for p in result.pages
        )


# ── 便利函数 ─────────────────────────────────────────────────────────


class TestUtilityFunctions:
    def test_suggest_query_output_path(self) -> None:
        path = suggest_query_output_path()
        assert "half_year_facts_query" in path
        assert path.endswith(".md")

    def test_task_code_constant(self) -> None:
        assert TASK_CODE == "HY-003"

    def test_vendor_constant(self) -> None:
        assert VENDOR == "tree_work_wiki"
