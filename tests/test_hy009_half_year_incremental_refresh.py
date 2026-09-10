# [HY-009] half_year_incremental_refresh
"""Tests for 半年报增量刷新、缓存失效与事实冲突审计 (HY-009).

覆盖六类 fixture 场景（任务验收方式）：
  1. 新增：新半年报页面出现 → 检测为 new，缓存增量刷新。
  2. 修订：已有页面内容变更 → 检测为 revised，缓存更新。
  3. 删除：已有页面被移除 → 检测为 deleted，缓存条目失效。
  4. 缓存损坏：旧缓存不可用 → 全量重建。
  5. 同周期冲突：同报告期多页关键指标不一致 → conflict + fact_conflict_flags。
  6. 无变化：页面未变 → 复用旧缓存，不重建全量索引。

额外覆盖：
  - 重复执行幂等：连续两次相同输入 → 结果一致。
  - 冲突事实不进入 HAS_DATA 强结论路径。
  - JSON 序列化。
  - 只读安全性：不写知识库目录。
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from tradingagents.dataflows.half_year_facts_provider import (
    DATA_CONFLICT,
    DATA_FRESH,
    DATA_STALE,
    HalfYearFactsPage,
    HalfYearFactsQueryResult,
    ParsedMetric,
    query_half_year_facts,
)
from tradingagents.dataflows.half_year_incremental_refresh import (
    CHANGE_DELETED,
    CHANGE_EXPIRED,
    CHANGE_NEW,
    CHANGE_REVISED,
    CHANGE_UNCHANGED,
    FactConflictFlag,
    IncrementalRefreshResult,
    PageChange,
    TASK_CODE,
    _build_incremental_cache,
    _detect_cross_version_conflicts,
    _detect_page_changes,
    _is_expired,
    incremental_refresh_half_year_facts,
)
from tradingagents.dataflows.local_knowledge_cache import (
    CONTRACT_VERSION,
    CachedPageData,
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    KnowledgeCache,
    ManifestEntry,
    build_cache_from_scan,
    build_manifest,
    get_or_build_cache,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
)
from tests.half_year_fixtures import (
    QUALIFIED_HALF_YEAR_PAGE,
    EXPIRED_HALF_YEAR_PAGE,
    WEAKENED_OLD_OPINION_PAGE,
    build_half_year_fixture_kb,
)


# ── HY-009 专用 fixture 页面 ──────────────────────────────────────────

# 修订版浪潮信息半年报：营收和利润数据被修正
REVISED_LANGCHAO_PAGE = """---
title: 浪潮信息000977-2025H1半年报（修订版）
created: 2026-08-29
updated: 2026-09-15
sources:
  - "[[../../raw/2026-09-15-浪潮信息-2025H1-修订.md|公司公告-2025H1修订]]"
tags: [浪潮信息, 半年报, 2025H1, 修订]
related: [[investment/浪潮信息000977-AI服务器放量]]
symbols: ["000977.SZ 浪潮信息"]
themes: [AI服务器]
industry_chain_roles: [AI服务器整机]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-09-15
source_type: [exchange_filing, fact_table, management_commentary]
financial_facts:
  - 营收 435.8亿 (+63.2% YoY)
  - 归母净利 13.1亿 (+50.0% YoY)
segment_facts:
  - AI服务器营收占比提升至65%
management_commentary:
  - 上调全年AI服务器出货指引（修订版）
forward_guidance:
  - 下半年毛利率企稳回升
risk_factors: [上游GPU供应, 客户集中度]
source_links:
  - 巨潮资讯 <修订公告URL>
---

# 浪潮信息（000977）— 2025H1 财报（修订版）

## 一句话总结

2025H1 营收 435.8亿，同比 +63.2%，AI服务器放量（修订后数据）。

## 投资逻辑

- AI服务器整机放量，营收高增。

## 风险提示

- 上游GPU供应紧张。
- 客户集中度风险。

## 原始资料

- 公司公告（修订版）。
"""

# 冲突版浪潮信息：与 QUALIFIED 版本同周期但数据不同
CONFLICT_LANGCHAO_PAGE = """---
title: 浪潮信息000977-2025H1半年报（券商口径）
created: 2026-08-30
updated: 2026-08-30
sources:
  - 某券商研报
tags: [浪潮信息, 半年报, 2025H1, 券商]
related: [[investment/浪潮信息000977-AI服务器放量]]
symbols: ["000977.SZ 浪潮信息"]
themes: [AI服务器]
industry_chain_roles: [AI服务器整机]
report_type: 半年报
evidence_level: B
valid_until: 2099-12-31
source_quality: 中
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-30
source_type: [broker_report]
financial_facts:
  - 营收 410.0亿 (+55.0% YoY)
  - 归母净利 11.8亿 (+38.0% YoY)
segment_facts:
  - AI服务器营收占比约60%
risk_factors: [上游GPU供应]
source_links:
  - 某券商研报 <URL>
---

# 浪潮信息（000977）— 2025H1 财报（券商口径）

## 一句话总结

2025H1 营收约 410亿，同比 +55%，AI服务器放量。

## 投资逻辑

- AI服务器增长。

## 风险提示

- 上游GPU供应紧张。

## 原始资料

- 某券商研报。
"""

# 新增页面：比亚迪半年报
BYD_HALF_YEAR_PAGE = """---
title: 比亚迪002594-2025H1半年报
created: 2026-08-29
updated: 2026-08-29
sources:
  - "[[../../raw/2026-08-29-比亚迪-2025H1.md|公司公告-2025H1]]"
tags: [比亚迪, 半年报, 2025H1]
related: [[investment/比亚迪002594-新能源龙头]]
symbols: ["002594.SZ 比亚迪"]
themes: [新能源汽车]
industry_chain_roles: [新能源汽车龙头]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [exchange_filing, fact_table]
financial_facts:
  - 营收 3200.5亿 (+25.0% YoY)
  - 归母净利 155.2亿 (+30.0% YoY)
segment_facts:
  - 新能源汽车销量创新高
management_commentary:
  - 海外市场持续扩张
forward_guidance:
  - 全年销量目标上调
risk_factors: [原材料价格, 海外政策]
source_links:
  - 巨潮资讯 <公告URL>
---

# 比亚迪（002594）— 2025H1 财报

## 一句话总结

2025H1 营收 3200.5亿，同比 +25.0%，新能源汽车销量创新高。

## 投资逻辑

- 新能源汽车龙头，营收利润双增。

## 风险提示

- 原材料价格波动。
- 海外政策风险。

## 原始资料

- 公司公告。
"""


# ── 工具函数 ──────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_kb_with_custom(
    tmp_path: Path,
    custom_pages: dict[str, str],
    include_standard: list[str] | None = None,
) -> Path:
    """构建包含自定义页面的微型知识库。"""
    if include_standard:
        build_half_year_fixture_kb(tmp_path, include=include_standard)
    else:
        # 只写 index/log
        inv = tmp_path / "wiki" / "investment"
        inv.mkdir(parents=True, exist_ok=True)
        _write(tmp_path / "wiki" / "index.md", "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n")
        _write(tmp_path / "wiki" / "log.md", "# Log\n")

    for filename, content in custom_pages.items():
        _write(tmp_path / "wiki" / "investment" / filename, content)

    return tmp_path


def _build_initial_cache(
    tmp_path: Path,
    custom_pages: dict[str, str],
    include_standard: list[str] | None = None,
) -> KnowledgeCache:
    """构建初始缓存。"""
    root = _build_kb_with_custom(tmp_path, custom_pages, include_standard)
    return build_cache_from_scan(str(root))


# ── 场景 1：新增 ──────────────────────────────────────────────────────


class TestScenarioNew:
    """新增半年报页面 → 检测为 new，缓存增量刷新。"""

    def test_new_page_detected(self, tmp_path: Path) -> None:
        """新页面被检测为 CHANGE_NEW。"""
        # 初始状态：只有标准 fixture
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 新增比亚迪页面
        _write(root / "wiki" / "investment" / "比亚迪002594-2025H1半年报.md", BYD_HALF_YEAR_PAGE)

        changes, _, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=date(2026, 9, 1),
        )

        new_changes = [c for c in changes if c.change_type == CHANGE_NEW]
        assert len(new_changes) == 1
        assert "比亚迪" in new_changes[0].rel_path
        assert new_changes[0].new_period == "2025H1"

    def test_new_page_in_result(self, tmp_path: Path) -> None:
        """新增页面出现在查询结果中。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        _write(root / "wiki" / "investment" / "比亚迪002594-2025H1半年报.md", BYD_HALF_YEAR_PAGE)

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="002594",
            old_cache=old_cache,
            today=date(2026, 9, 1),
        )

        assert result.status == "refreshed"
        assert result.added_count == 1
        assert result.facts_result is not None
        assert any("比亚迪" in p.rel_path for p in result.facts_result.pages)

    def test_new_page_summary(self, tmp_path: Path) -> None:
        """刷新摘要包含新增信息。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        _write(root / "wiki" / "investment" / "比亚迪002594-2025H1半年报.md", BYD_HALF_YEAR_PAGE)

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="002594",
            old_cache=old_cache,
            today=date(2026, 9, 1),
        )

        assert "新增" in result.refresh_summary
        assert "1" in result.refresh_summary  # added_count=1


# ── 场景 2：修订 ──────────────────────────────────────────────────────


class TestScenarioRevised:
    """已有页面内容变更 → 检测为 revised，缓存更新。"""

    def test_revised_page_detected(self, tmp_path: Path) -> None:
        """修订页面被检测为 CHANGE_REVISED。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 修订浪潮信息页面
        langchao_file = root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        langchao_file.write_text(REVISED_LANGCHAO_PAGE, encoding="utf-8")

        changes, _, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=date(2026, 9, 1),
        )

        revised = [c for c in changes if c.change_type == CHANGE_REVISED]
        assert len(revised) == 1
        assert revised[0].old_hash is not None
        assert revised[0].new_hash is not None
        assert revised[0].old_hash != revised[0].new_hash

    def test_revised_page_updates_facts(self, tmp_path: Path) -> None:
        """修订后的页面反映新的财务数据。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 修订
        langchao_file = root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        langchao_file.write_text(REVISED_LANGCHAO_PAGE, encoding="utf-8")

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=old_cache,
            today=date(2026, 9, 1),
        )

        assert result.status == "refreshed"
        assert result.revised_count == 1
        assert result.facts_result is not None

        # 检查修订后的数据
        pages = result.facts_result.pages
        langchao_pages = [p for p in pages if "浪潮信息" in p.rel_path]
        assert len(langchao_pages) >= 1
        facts = langchao_pages[0].financial_facts
        revenue = [m for m in facts if m.metric_key == "revenue"]
        assert revenue
        assert "435.8" in revenue[0].value  # 修订后的数据

    def test_revised_disclosure_date_detected(self, tmp_path: Path) -> None:
        """披露日变更被正确检测。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 修改披露日
        langchao_file = root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        content = QUALIFIED_HALF_YEAR_PAGE.replace("disclosure_date: 2026-08-29", "disclosure_date: 2026-09-20")
        langchao_file.write_text(content, encoding="utf-8")

        changes, _, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=date(2026, 9, 1),
        )

        revised = [c for c in changes if c.change_type == CHANGE_REVISED]
        assert len(revised) == 1
        assert "披露日" in revised[0].reason


# ── 场景 3：删除 ──────────────────────────────────────────────────────


class TestScenarioDeleted:
    """已有页面被移除 → 检测为 deleted，缓存条目失效。"""

    def test_deleted_page_detected(self, tmp_path: Path) -> None:
        """删除页面被检测为 CHANGE_DELETED。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 删除页面
        langchao_file = root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        langchao_file.unlink()

        changes, _, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=date(2026, 9, 1),
        )

        deleted = [c for c in changes if c.change_type == CHANGE_DELETED]
        assert len(deleted) == 1
        assert deleted[0].old_period == "2025H1"

    def test_deleted_page_in_result(self, tmp_path: Path) -> None:
        """删除后查询返回无数据。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 删除
        langchao_file = root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        langchao_file.unlink()

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=old_cache,
            today=date(2026, 9, 1),
        )

        assert result.status == "refreshed"
        assert result.deleted_count == 1
        assert result.pending_review_count >= 1  # 删除的需要复核

    def test_deleted_page_summary(self, tmp_path: Path) -> None:
        """刷新摘要包含删除信息。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        langchao_file = root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        langchao_file.unlink()

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=old_cache,
            today=date(2026, 9, 1),
        )

        assert "删除" in result.refresh_summary


# ── 场景 4：缓存损坏 ─────────────────────────────────────────────────


class TestScenarioCorruptedCache:
    """旧缓存不可用 → 全量重建。"""

    def test_corrupted_cache_rebuilds(self, tmp_path: Path) -> None:
        """传入 None 旧缓存时全量构建（视为首次，无 old manifest 比对）。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=None,  # 无旧缓存
            today=date(2026, 9, 1),
        )

        # 首次构建：无旧 manifest，所有页面都是"新增"
        assert result.status == "refreshed"
        assert result.facts_result is not None
        assert result.facts_result.status == STATUS_HAS_DATA
        assert result.reused_cache is False

    def test_empty_cache_pages_rebuilds(self, tmp_path: Path) -> None:
        """旧缓存 pages 为空时全量构建。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])

        # 构造空缓存
        empty_cache = KnowledgeCache(
            contract_version=CONTRACT_VERSION,
            knowledge_root=str(root),
            built_at="2026-09-01T00:00:00",
            manifest={},
            pages={},
        )

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=empty_cache,
            today=date(2026, 9, 1),
        )

        assert result.facts_result is not None
        assert result.facts_result.status == STATUS_HAS_DATA

    def test_corrupted_manifest_rebuilds(self, tmp_path: Path) -> None:
        """manifest 与实际文件不匹配时重建。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])

        # 构造 manifest 包含不存在的文件
        bad_cache = KnowledgeCache(
            contract_version=CONTRACT_VERSION,
            knowledge_root=str(root),
            built_at="2026-09-01T00:00:00",
            manifest={
                "wiki/investment/nonexistent.md": ManifestEntry(
                    size=100, mtime_ns=123, sha1_prefix="abc123"
                ),
            },
            pages={},
        )

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=bad_cache,
            today=date(2026, 9, 1),
        )

        # 应该检测到删除（manifest 中有但文件系统没有）
        # 和新增（文件系统有但 manifest 中没有）
        assert result.facts_result is not None


# ── 场景 5：同周期冲突 ────────────────────────────────────────────────


class TestScenarioConflict:
    """同报告期多页关键指标不一致 → conflict + fact_conflict_flags。"""

    def test_conflict_detected(self, tmp_path: Path) -> None:
        """同周期不同数据被检测为冲突。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        # 添加冲突页面
        _write(
            root / "wiki" / "investment" / "浪潮信息000977-2025H1-券商.md",
            CONFLICT_LANGCHAO_PAGE,
        )

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            today=date(2026, 9, 1),
        )

        assert result.conflict_count > 0
        assert len(result.fact_conflict_flags) > 0
        assert result.status == "conflict"

    def test_conflict_flag_structure(self, tmp_path: Path) -> None:
        """冲突标记包含 period / metric_key / values / detail。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        _write(
            root / "wiki" / "investment" / "浪潮信息000977-2025H1-券商.md",
            CONFLICT_LANGCHAO_PAGE,
        )

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            today=date(2026, 9, 1),
        )

        flag = result.fact_conflict_flags[0]
        assert flag.period == "2025H1"
        assert flag.metric_key in ("revenue", "net_profit")
        assert len(flag.values) >= 2
        for v in flag.values:
            assert "value" in v
            assert "source" in v
        assert "冲突" in flag.detail

    def test_conflict_pages_marked_conflict_status(self, tmp_path: Path) -> None:
        """冲突页面 data_status 被标记为 conflict。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        _write(
            root / "wiki" / "investment" / "浪潮信息000977-2025H1-券商.md",
            CONFLICT_LANGCHAO_PAGE,
        )

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            today=date(2026, 9, 1),
        )

        # 至少有一个页面被标记为 conflict
        conflict_pages = [
            p for p in result.facts_result.pages
            if p.data_status == DATA_CONFLICT
        ]
        assert len(conflict_pages) >= 1

    def test_conflict_not_in_has_data_strong_path(self, tmp_path: Path) -> None:
        """冲突事实不进入 HAS_DATA 强结论路径。

        当存在冲突时，status 应为 conflict（不是简单的 HAS_DATA）。
        """
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        _write(
            root / "wiki" / "investment" / "浪潮信息000977-2025H1-券商.md",
            CONFLICT_LANGCHAO_PAGE,
        )

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            today=date(2026, 9, 1),
        )

        # 刷新结果状态为 conflict
        assert result.status == "conflict"

        # facts_result 仍可能有 HAS_DATA（因为有 fresh 页），
        # 但 data_status 应为 conflict
        if result.facts_result is not None:
            assert result.facts_result.data_status == DATA_CONFLICT

    def test_conflict_summary(self, tmp_path: Path) -> None:
        """刷新摘要包含冲突明细和警告。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        _write(
            root / "wiki" / "investment" / "浪潮信息000977-2025H1-券商.md",
            CONFLICT_LANGCHAO_PAGE,
        )

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            today=date(2026, 9, 1),
        )

        assert "事实冲突明细" in result.refresh_summary
        assert "不进入 HAS_DATA" in result.refresh_summary


# ── 场景 6：无变化 ────────────────────────────────────────────────────


class TestScenarioNoChanges:
    """页面未变 → 复用旧缓存，不重建全量索引。"""

    def test_no_changes_reuses_cache(self, tmp_path: Path) -> None:
        """无变更时复用旧缓存。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=old_cache,
            today=date(2026, 9, 1),
        )

        assert result.status == "no_changes"
        assert result.reused_cache is True
        assert result.added_count == 0
        assert result.revised_count == 0
        assert result.deleted_count == 0

    def test_no_changes_summary(self, tmp_path: Path) -> None:
        """无变更时摘要说明无变更。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=old_cache,
            today=date(2026, 9, 1),
        )

        assert "无变更" in result.refresh_summary
        assert "未重建全量索引" in result.refresh_summary

    def test_no_changes_facts_still_available(self, tmp_path: Path) -> None:
        """无变更时事实仍然可用。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        result = incremental_refresh_half_year_facts(
            str(root),
            symbol="000977",
            old_cache=old_cache,
            today=date(2026, 9, 1),
        )

        assert result.facts_result is not None
        assert result.facts_result.status == STATUS_HAS_DATA


# ── 幂等性 ────────────────────────────────────────────────────────────


class TestIdempotency:
    """重复执行幂等：连续两次相同输入 → 结果一致。"""

    def test_idempotent_no_changes(self, tmp_path: Path) -> None:
        """连续两次无变更调用 → 结果一致。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        cache = build_cache_from_scan(str(root))

        result1 = incremental_refresh_half_year_facts(
            str(root), symbol="000977", old_cache=cache, today=date(2026, 9, 1),
        )
        result2 = incremental_refresh_half_year_facts(
            str(root), symbol="000977", old_cache=cache, today=date(2026, 9, 1),
        )

        assert result1.status == result2.status
        assert result1.added_count == result2.added_count
        assert result1.conflict_count == result2.conflict_count

    def test_idempotent_after_refresh(self, tmp_path: Path) -> None:
        """刷新后再次用新缓存调用 → 无变更。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 新增页面
        _write(root / "wiki" / "investment" / "比亚迪002594-2025H1半年报.md", BYD_HALF_YEAR_PAGE)

        # 第一次刷新
        result1 = incremental_refresh_half_year_facts(
            str(root), symbol="002594", old_cache=old_cache, today=date(2026, 9, 1),
        )
        assert result1.status == "refreshed"
        assert result1.added_count == 1

        # 构建新缓存（模拟刷新后的状态）
        new_cache = build_cache_from_scan(str(root))

        # 第二次刷新（用新缓存）
        result2 = incremental_refresh_half_year_facts(
            str(root), symbol="002594", old_cache=new_cache, today=date(2026, 9, 1),
        )
        assert result2.status == "no_changes"
        assert result2.added_count == 0


# ── JSON 序列化 ───────────────────────────────────────────────────────


class TestSerialization:
    """结果可 JSON 序列化。"""

    def test_result_to_dict(self, tmp_path: Path) -> None:
        """IncrementalRefreshResult.to_dict() 可序列化。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        result = incremental_refresh_half_year_facts(
            str(root), symbol="000977", old_cache=old_cache, today=date(2026, 9, 1),
        )

        d = result.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert "HY-009" in json_str

    def test_conflict_flag_to_dict(self) -> None:
        """FactConflictFlag.to_dict() 可序列化。"""
        flag = FactConflictFlag(
            period="2025H1",
            metric_key="revenue",
            values=[{"value": "100亿", "source": "a.md"}, {"value": "90亿", "source": "b.md"}],
            detail="2025H1 revenue 冲突：100亿(a.md) vs 90亿(b.md)",
        )
        d = flag.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert "2025H1" in json_str

    def test_page_change_to_dict(self) -> None:
        """PageChange.to_dict() 可序列化。"""
        change = PageChange(
            rel_path="wiki/investment/foo.md",
            change_type=CHANGE_REVISED,
            old_hash="abc123",
            new_hash="def456",
            old_period="2025H1",
            new_period="2025H1",
            reason="内容变更",
        )
        d = change.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert "revised" in json_str


# ── 只读安全性 ────────────────────────────────────────────────────────


class TestReadOnlySafety:
    """不写知识库目录。"""

    def test_knowledge_root_unchanged(self, tmp_path: Path) -> None:
        """刷新后知识库文件不变。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 记录原始文件
        inv = root / "wiki" / "investment"
        original_files = {f.name for f in inv.iterdir() if f.is_file()}
        original_contents = {}
        for f in inv.iterdir():
            if f.is_file():
                original_contents[f.name] = f.read_text(encoding="utf-8")

        # 执行刷新
        incremental_refresh_half_year_facts(
            str(root), symbol="000977", old_cache=old_cache, today=date(2026, 9, 1),
        )

        # 验证文件未变
        current_files = {f.name for f in inv.iterdir() if f.is_file()}
        assert current_files == original_files
        for name, content in original_contents.items():
            assert (inv / name).read_text(encoding="utf-8") == content


# ── 边界情况 ──────────────────────────────────────────────────────────


class TestEdgeCases:
    """边界情况覆盖。"""

    def test_nonexistent_root(self, tmp_path: Path) -> None:
        """知识库不存在 → failed。"""
        result = incremental_refresh_half_year_facts(
            str(tmp_path / "nonexistent"),
            symbol="000977",
            today=date(2026, 9, 1),
        )
        assert result.status == "failed"
        assert any("不存在" in e for e in result.errors)

    def test_no_query_conditions(self, tmp_path: Path) -> None:
        """无查询条件 → failed。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = incremental_refresh_half_year_facts(
            str(root), today=date(2026, 9, 1),
        )
        assert result.status == "failed"

    def test_non_half_year_pages_ignored(self, tmp_path: Path) -> None:
        """非半年报页面变更不触发刷新。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified", "non_financial_control"])
        old_cache = build_cache_from_scan(str(root))

        # 修改非半年报页面
        control_file = root / "wiki" / "investment" / "平安银行000001-公司点评.md"
        content = control_file.read_text(encoding="utf-8")
        control_file.write_text(content + "\n\n额外内容", encoding="utf-8")

        changes, _, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=date(2026, 9, 1),
        )

        # 非半年报页面变更不应出现在 changes 中
        assert len(changes) == 0

    def test_expired_new_page_detected(self, tmp_path: Path) -> None:
        """新增过期页面被检测为 expired。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 新增过期页面
        _write(root / "wiki" / "investment" / "贵州茅台600519-2024H1半年报.md", EXPIRED_HALF_YEAR_PAGE)

        changes, _, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=date(2026, 9, 1),
        )

        expired = [c for c in changes if c.change_type == CHANGE_EXPIRED]
        assert len(expired) == 1

    def test_mixed_changes(self, tmp_path: Path) -> None:
        """同时有新增、修订、删除。"""
        root = build_half_year_fixture_kb(
            tmp_path, include=["qualified", "expired"]
        )
        old_cache = build_cache_from_scan(str(root))

        # 新增
        _write(root / "wiki" / "investment" / "比亚迪002594-2025H1半年报.md", BYD_HALF_YEAR_PAGE)

        # 修订
        langchao = root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        langchao.write_text(REVISED_LANGCHAO_PAGE, encoding="utf-8")

        # 删除
        (root / "wiki" / "investment" / "贵州茅台600519-2024H1半年报-已过期.md").unlink()

        changes, _, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=date(2026, 9, 1),
        )

        change_types = {c.change_type for c in changes}
        assert CHANGE_NEW in change_types
        assert CHANGE_REVISED in change_types
        assert CHANGE_DELETED in change_types

    def test_cross_version_conflict_empty_pages(self) -> None:
        """空页面列表 → 无冲突。"""
        flags = _detect_cross_version_conflicts([])
        assert flags == []

    def test_cross_version_conflict_single_page(self) -> None:
        """单页 → 无冲突。"""
        page = HalfYearFactsPage(
            rel_path="test.md",
            title="Test",
            financial_period="2025H1",
            disclosure_date="2026-08-29",
            financial_facts=[
                ParsedMetric(metric_key="revenue", metric_label="营收", raw="营收 100亿", value="100亿", change=None),
            ],
        )
        flags = _detect_cross_version_conflicts([page])
        assert flags == []

    def test_cross_version_conflict_same_values(self) -> None:
        """同周期同值 → 无冲突。"""
        page1 = HalfYearFactsPage(
            rel_path="a.md",
            title="A",
            financial_period="2025H1",
            disclosure_date="2026-08-29",
            financial_facts=[
                ParsedMetric(metric_key="revenue", metric_label="营收", raw="营收 100亿", value="100亿", change=None),
            ],
        )
        page2 = HalfYearFactsPage(
            rel_path="b.md",
            title="B",
            financial_period="2025H1",
            disclosure_date="2026-08-30",
            financial_facts=[
                ParsedMetric(metric_key="revenue", metric_label="营收", raw="营收 100亿", value="100亿", change=None),
            ],
        )
        flags = _detect_cross_version_conflicts([page1, page2])
        assert flags == []

    def test_cross_version_conflict_different_values(self) -> None:
        """同周期不同值 → 冲突。"""
        page1 = HalfYearFactsPage(
            rel_path="a.md",
            title="A",
            financial_period="2025H1",
            disclosure_date="2026-08-29",
            financial_facts=[
                ParsedMetric(metric_key="revenue", metric_label="营收", raw="营收 100亿", value="100亿", change=None),
            ],
        )
        page2 = HalfYearFactsPage(
            rel_path="b.md",
            title="B",
            financial_period="2025H1",
            disclosure_date="2026-08-30",
            financial_facts=[
                ParsedMetric(metric_key="revenue", metric_label="营收", raw="营收 90亿", value="90亿", change=None),
            ],
        )
        flags = _detect_cross_version_conflicts([page1, page2])
        assert len(flags) == 1
        assert flags[0].metric_key == "revenue"
        assert len(flags[0].values) == 2


# ── 跨场景集成 ────────────────────────────────────────────────────────


class TestIntegration:
    """跨场景集成测试。"""

    def test_full_lifecycle(self, tmp_path: Path) -> None:
        """完整生命周期：初始 → 新增 → 修订 → 冲突 → 无变更。"""
        # 1. 初始状态
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        cache = build_cache_from_scan(str(root))

        # 2. 无变更
        r1 = incremental_refresh_half_year_facts(
            str(root), symbol="000977", old_cache=cache, today=date(2026, 9, 1),
        )
        assert r1.status == "no_changes"

        # 3. 新增
        _write(root / "wiki" / "investment" / "比亚迪002594-2025H1半年报.md", BYD_HALF_YEAR_PAGE)
        r2 = incremental_refresh_half_year_facts(
            str(root), symbol="002594", old_cache=cache, today=date(2026, 9, 1),
        )
        assert r2.status == "refreshed"
        assert r2.added_count == 1

        # 4. 用新缓存
        cache2 = build_cache_from_scan(str(root))

        # 5. 修订
        langchao = root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        langchao.write_text(REVISED_LANGCHAO_PAGE, encoding="utf-8")
        r3 = incremental_refresh_half_year_facts(
            str(root), symbol="000977", old_cache=cache2, today=date(2026, 9, 1),
        )
        assert r3.status == "refreshed"
        assert r3.revised_count == 1

        # 6. 冲突
        cache3 = build_cache_from_scan(str(root))
        _write(root / "wiki" / "investment" / "浪潮信息000977-2025H1-券商.md", CONFLICT_LANGCHAO_PAGE)
        r4 = incremental_refresh_half_year_facts(
            str(root), symbol="000977", old_cache=cache3, today=date(2026, 9, 1),
        )
        assert r4.status == "conflict"
        assert r4.conflict_count > 0

    def test_incremental_cache_preserves_unchanged(self, tmp_path: Path) -> None:
        """增量缓存保留未变更页面。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        old_cache = build_cache_from_scan(str(root))

        # 新增页面
        _write(root / "wiki" / "investment" / "比亚迪002594-2025H1半年报.md", BYD_HALF_YEAR_PAGE)

        changes, current_manifest, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=date(2026, 9, 1),
        )

        new_cache, invalidated = _build_incremental_cache(
            str(root), old_cache, changes, current_manifest,
            today=date(2026, 9, 1),
        )

        # 旧页面仍在
        old_langchao = [
            r for r in old_cache.pages if "浪潮信息" in r
        ]
        for rel in old_langchao:
            assert rel in new_cache.pages

        # 新页面已加入
        byd_pages = [r for r in new_cache.pages if "比亚迪" in r]
        assert len(byd_pages) == 1


# ── [HY-009-R1] expiry-only / 截断冲突 / 现金流冲突 ───────────────────────


def _hy_page(symbol: str, period: str, *, cashflow: str = "经营现金流 15.0亿",
             valid_until: str = "2099-12-31", title_suffix: str = "") -> str:
    """构造带现金流事实的半年报页面。"""
    return f"""---
title: 测试股{symbol}-{period}半年报{title_suffix}
created: 2025-08-29
updated: 2025-08-29
symbols: ["{symbol}.SZ 测试股"]
report_type: 半年报
evidence_level: A
valid_until: {valid_until}
source_quality: 高
stale_risk: 低
financial_period: {period}
disclosure_date: 2025-08-29
source_type: [exchange_filing, fact_table]
financial_facts:
  - 营收 100.0亿
  - 归母净利 10.0亿
  - {cashflow}
---
# 测试股 {period}
"""


class TestHY009R1ExpiryOnlyChange:
    """仅有效期状态变化（内容未动）也必须识别为过期并失效缓存。"""

    def test_expiry_only_change_detected(self, tmp_path: Path) -> None:
        # valid_until=明天：缓存构建时未过期，注入日期推进 3 天后过期
        valid_until = (date.today() + timedelta(days=1)).isoformat()
        root = _build_kb_with_custom(tmp_path, {
            "d-2024H1.md": _hy_page("000996", "2024H1", valid_until=valid_until),
        })
        old_cache = build_cache_from_scan(str(root))
        future = date.today() + timedelta(days=3)

        changes, _, _ = _detect_page_changes(
            str(root),
            cached_manifest=old_cache.manifest,
            cached_pages=old_cache.pages,
            today=future,
        )
        expired = [c for c in changes if c.change_type == CHANGE_EXPIRED]
        assert len(expired) == 1
        assert "仅有效期状态变化" in expired[0].reason

    def test_expiry_only_invalidates_cache_and_audit(self, tmp_path: Path) -> None:
        valid_until = (date.today() + timedelta(days=1)).isoformat()
        root = _build_kb_with_custom(tmp_path, {
            "d-2024H1.md": _hy_page("000996", "2024H1", valid_until=valid_until),
        })
        old_cache = build_cache_from_scan(str(root))
        future = date.today() + timedelta(days=3)

        result = incremental_refresh_half_year_facts(
            str(root), symbol="000996", old_cache=old_cache, today=future,
        )
        assert result.expired_count == 1
        assert result.reused_cache is False
        assert result.status == "refreshed"

    def test_unexpired_page_still_idempotent(self, tmp_path: Path) -> None:
        # 回归：未过期页面重复执行保持 no_changes 幂等
        root = _build_kb_with_custom(tmp_path, {
            "ok.md": _hy_page("000996", "2024H1"),
        })
        cache = build_cache_from_scan(str(root))
        for _ in range(2):
            result = incremental_refresh_half_year_facts(
                str(root), symbol="000996", old_cache=cache,
                today=date.today() + timedelta(days=3),
            )
            assert result.status == "no_changes"
            assert result.reused_cache is True
            cache = build_cache_from_scan(str(root))


class TestHY009R1ConflictScanNotTruncated:
    """冲突检查不得受 max_pages 截断；现金流冲突进入不可用状态。"""

    def _conflicting_kb(self, tmp_path: Path) -> Path:
        # A/B 同为 2023H1（经营现金流 15 vs 9 冲突），C 为 2025H1 新期：
        # max_pages=2 时 B 被截掉，冲突证据落在截断之外
        return _build_kb_with_custom(tmp_path, {
            "a-2023H1.md": _hy_page("000997", "2023H1"),
            "b-2023H1-v2.md": _hy_page("000997", "2023H1",
                                       cashflow="经营现金流 9.0亿",
                                       title_suffix="（B版）"),
            "c-2025H1.md": _hy_page("000997", "2025H1",
                                    cashflow="经营现金流 18.0亿",
                                    title_suffix="（新期）"),
        })

    def test_conflict_detected_despite_truncation(self, tmp_path: Path) -> None:
        root = self._conflicting_kb(tmp_path)
        old_cache = build_cache_from_scan(str(root))
        result = incremental_refresh_half_year_facts(
            str(root), symbol="000997", old_cache=old_cache,
            today=date(2026, 9, 1), max_pages=2,
        )
        assert result.status == "conflict"
        assert result.conflict_count >= 1

    def test_cashflow_conflict_page_marked_unavailable(self, tmp_path: Path) -> None:
        root = self._conflicting_kb(tmp_path)
        old_cache = build_cache_from_scan(str(root))
        result = incremental_refresh_half_year_facts(
            str(root), symbol="000997", old_cache=old_cache,
            today=date(2026, 9, 1), max_pages=10,
        )
        assert result.conflict_count >= 1
        assert any("operating_cash_flow" in f.metric_key
                   for f in result.fact_conflict_flags)
        # 冲突页 data_status 必须是 conflict（不可用），不得保持 fresh
        page_status = {p.rel_path: p.data_status for p in result.facts_result.pages}
        assert page_status["wiki/investment/a-2023H1.md"] == DATA_CONFLICT
        assert page_status["wiki/investment/b-2023H1-v2.md"] == DATA_CONFLICT

    def test_conflict_result_never_claims_fresh(self, tmp_path: Path) -> None:
        # 冲突不可被截断伪装 HAS_DATA/fresh：查询级 data_status 必须 conflict
        root = self._conflicting_kb(tmp_path)
        old_cache = build_cache_from_scan(str(root))
        result = incremental_refresh_half_year_facts(
            str(root), symbol="000997", old_cache=old_cache,
            today=date(2026, 9, 1), max_pages=2,
        )
        assert result.fact_conflict_flags, "冲突必须被检出"
        assert result.facts_result is not None
        assert result.facts_result.data_status == DATA_CONFLICT

    def test_provider_flags_cashflow_conflict(self, tmp_path: Path) -> None:
        # provider 页级冲突检测也覆盖经营现金流
        from tradingagents.dataflows.half_year_facts_provider import _detect_conflicts

        root = _build_kb_with_custom(tmp_path, {
            "a-2023H1.md": _hy_page("000995", "2023H1"),
            "b-2023H1-v2.md": _hy_page("000995", "2023H1",
                                       cashflow="经营现金流 9.0亿",
                                       title_suffix="（B版）"),
        })
        result = query_half_year_facts(str(root), symbol="000995")
        statuses = {p.data_status for p in result.pages}
        assert DATA_CONFLICT in statuses
