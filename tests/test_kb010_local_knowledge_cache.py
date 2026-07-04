# [KB-010] local_knowledge_cache
"""Tests for 本地知识索引缓存与 freshness manifest (KB-010).

覆盖（对应任务验收方式）：
  - Manifest 构建：基于 (rel_path, size, mtime_ns, sha1_prefix) 的 manifest。
  - manifest 一致性判定：同一知识库连续查询结果一致；文件 mtime/size 变化 → 失效。
  - freshness_status：fresh / stale / missing / error 四态语义。
  - 缓存命中语义等价：cache 路径与全量扫描产出相同的 status / matched_pages /
    summary / symbols / themes / risks / sources（不改变交易动作）。
  - 缓存缺失 / 损坏 / contract_version 不匹配 / knowledge_root 不匹配 → 自动回退。
  - CLI ``--rebuild-cache`` / ``--no-cache`` / ``--cache-path`` / ``--freshness-only``。
  - 只读安全性：不写知识库目录；缓存只写调用方指定路径或 .cache/。
  - 段落抽取一致性（summary/risks/sources 不输出原文）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.local_knowledge_cache import (
    CONTRACT_VERSION,
    CachedPageData,
    FRESHNESS_ERROR,
    FRESHNESS_FRESH,
    FRESHNESS_MISSING,
    FRESHNESS_STALE,
    KnowledgeCache,
    ManifestEntry,
    TASK_CODE,
    build_cache_from_scan,
    build_manifest,
    default_cache_path,
    freshness_summary,
    get_or_build_cache,
    load_cache_from_disk,
    query_local_knowledge_cached,
    save_cache_to_disk,
    _build_cached_page,
    _cached_page_to_match,
    _manifest_matches,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_HAS_DATA,
    STATUS_NORMAL_NO_DATA,
    query_local_knowledge,
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
tags: [华勤技术, 超节点, AI服务器]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器, 超节点, 液冷散热, AI算力]
industry_chain_roles: [AI服务器ODM]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 华勤技术（603296）

## 一句话总结

华勤技术超节点进入出货周期，AI服务器增长强劲。

## 风险提示

- 需求不及预期
- 竞争加剧

## 原始资料

- [[../../raw/x.md|中邮证券-华勤技术超节点]]
"""


_INDUSTRY_PAGE = """---
title: PCB产业链综述
created: 2026-05-10
updated: 2026-05-10
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

## 风险提示

- 原材料价格波动
"""


_TODO_PAGE = """---
title: Dell-FQ127-待补充
created: 2026-05-31
updated: 2026-06-29
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

## 风险提示

- 待补充
"""


_STALE_ONLY_PAGE = """---
title: 某周期股-已过期
created: 2026-04-01
updated: 2026-04-01
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


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造一棵 mini Tree Work 知识库（只在 tmp_path 下）。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE)
    _write(inv / "PCB产业链综述.md", _INDUSTRY_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _TODO_PAGE)
    _write(inv / "某周期股-已过期.md", _STALE_ONLY_PAGE)
    return tmp_path


@pytest.fixture()
def empty_kb(tmp_path: Path) -> Path:
    """只有 wiki/investment/ 空目录的知识库。"""
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True)
    return tmp_path


def _bump_mtime(path: Path, delta_seconds: float = 1.0) -> None:
    """显式调整文件 mtime，确保 manifest 检测到变化。

    macOS APFS mtime 纳秒精度，sleep 不可靠；这里用 os.utime 直接设置。
    """
    stat = path.stat()
    new_ts = stat.st_mtime + delta_seconds
    os.utime(path, (new_ts, new_ts))


# ── 1. Manifest 构建 ─────────────────────────────────────────────────


class TestManifestBuild:
    def test_build_manifest_returns_per_file_entry(self, fixture_kb: Path):
        manifest, errors = build_manifest(str(fixture_kb))
        assert errors == []
        # 4 个 md 文件全部进 manifest。
        assert len(manifest) == 4
        for rel, entry in manifest.items():
            assert isinstance(entry, ManifestEntry)
            assert entry.size > 0
            assert entry.mtime_ns > 0
            assert len(entry.sha1_prefix) == 16
            assert rel.startswith("wiki/investment/")

    def test_build_manifest_missing_root(self, tmp_path: Path):
        missing = tmp_path / "no_such_dir"
        manifest, errors = build_manifest(str(missing))
        assert manifest == {}
        assert len(errors) == 1
        assert "不存在" in errors[0]

    def test_build_manifest_missing_investment_subdir(self, tmp_path: Path):
        (tmp_path / "wiki").mkdir()
        manifest, errors = build_manifest(str(tmp_path))
        assert manifest == {}
        assert len(errors) == 1
        assert "investment" in errors[0]

    def test_build_manifest_empty_investment(self, empty_kb: Path):
        manifest, errors = build_manifest(str(empty_kb))
        assert manifest == {}
        assert errors == []

    def test_manifest_entry_roundtrip(self):
        raw_prefix = "abc" * 5 + "d"  # 16 chars
        entry = ManifestEntry(size=100, mtime_ns=12345, sha1_prefix=raw_prefix)
        d = entry.to_dict()
        assert d == {"size": 100, "mtime_ns": 12345, "sha1_prefix": raw_prefix}
        entry2 = ManifestEntry.from_dict(d)
        assert entry2.size == 100
        assert entry2.mtime_ns == 12345
        assert entry2.sha1_prefix == raw_prefix

    def test_manifest_entry_truncates_long_sha1_on_load(self):
        # from_dict 应截断到 _SHA1_PREFIX_LEN，保持缓存可读性。
        long_entry = ManifestEntry.from_dict(
            {"size": 1, "mtime_ns": 1, "sha1_prefix": "x" * 32}
        )
        assert len(long_entry.sha1_prefix) == 16


class TestManifestMatch:
    def test_identical_manifest_matches(self, fixture_kb: Path):
        m1, _ = build_manifest(str(fixture_kb))
        m2, _ = build_manifest(str(fixture_kb))
        is_match, reasons = _manifest_matches(m1, m2)
        assert is_match is True
        assert reasons == []

    def test_size_change_detected(self, fixture_kb: Path):
        m1, _ = build_manifest(str(fixture_kb))
        target = fixture_kb / INVESTMENT_SUBDIR / "华勤技术603296-超节点.md"
        # 改文件内容（size 变化）。
        _write(target, _COMPANY_PAGE + "\n## 新增段\n\n更多内容。\n")
        m2, _ = build_manifest(str(fixture_kb))
        is_match, reasons = _manifest_matches(m1, m2)
        assert is_match is False
        assert any("修改" in r for r in reasons)

    def test_mtime_change_detected(self, fixture_kb: Path):
        m1, _ = build_manifest(str(fixture_kb))
        target = fixture_kb / INVESTMENT_SUBDIR / "华勤技术603296-超节点.md"
        _bump_mtime(target, delta_seconds=10.0)
        m2, _ = build_manifest(str(fixture_kb))
        is_match, reasons = _manifest_matches(m1, m2)
        assert is_match is False
        assert any("修改" in r for r in reasons)

    def test_added_file_detected(self, fixture_kb: Path):
        m1, _ = build_manifest(str(fixture_kb))
        _write(
            fixture_kb / INVESTMENT_SUBDIR / "新增页.md",
            "# 新增页\n\n## 一句话总结\n\n新页面。\n",
        )
        m2, _ = build_manifest(str(fixture_kb))
        is_match, reasons = _manifest_matches(m1, m2)
        assert is_match is False
        assert any("新增" in r for r in reasons)

    def test_removed_file_detected(self, fixture_kb: Path):
        m1, _ = build_manifest(str(fixture_kb))
        (fixture_kb / INVESTMENT_SUBDIR / "Dell-FQ127-待补充.md").unlink()
        m2, _ = build_manifest(str(fixture_kb))
        is_match, reasons = _manifest_matches(m1, m2)
        assert is_match is False
        assert any("删除" in r for r in reasons)


# ── 2. get_or_build_cache：freshness 四态 ────────────────────────────


class TestGetOrBuildCache:
    def test_first_call_is_missing(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "c.json")
        cache = get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        assert cache.freshness_status == FRESHNESS_MISSING
        assert cache.reused_disk_cache is False
        assert len(cache.pages) == 4
        assert len(cache.manifest) == 4
        # 缓存应已落盘。
        assert os.path.exists(cache_path)

    def test_second_call_is_fresh(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "c.json")
        get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        cache2 = get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        assert cache2.freshness_status == FRESHNESS_FRESH
        assert cache2.reused_disk_cache is True
        assert len(cache2.pages) == 4

    def test_file_change_triggers_stale_rebuild(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "c.json")
        get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        # 修改文件 mtime → manifest 不一致。
        target = fixture_kb / INVESTMENT_SUBDIR / "华勤技术603296-超节点.md"
        _bump_mtime(target, delta_seconds=10.0)
        cache2 = get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        assert cache2.freshness_status == FRESHNESS_STALE
        assert cache2.reused_disk_cache is False
        assert len(cache2.pages) == 4
        # 缓存应已更新（写回）。
        assert os.path.exists(cache_path)

    def test_rebuild_flag_forces_rebuild(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "c.json")
        get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        cache2 = get_or_build_cache(
            str(fixture_kb), cache_path=cache_path, rebuild=True
        )
        assert cache2.freshness_status == FRESHNESS_STALE
        assert cache2.reused_disk_cache is False
        assert len(cache2.pages) == 4

    def test_no_cache_skips_disk(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "c.json")
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=cache_path, no_cache=True
        )
        # no_cache 不写盘。
        assert not os.path.exists(cache_path)
        assert cache.cache_path is None
        # no_cache 仍能正常构建。
        assert len(cache.pages) == 4

    def test_missing_root_is_error(self, tmp_path: Path):
        cache = get_or_build_cache(str(tmp_path / "no_such"))
        assert cache.freshness_status == FRESHNESS_ERROR
        assert len(cache.pages) == 0
        assert any("不存在" in e for e in cache.errors)

    def test_empty_investment_is_stale_not_error(self, empty_kb: Path, tmp_path: Path):
        # investment 分区存在但无 md 文件 → manifest 为空，无错误 → not error。
        cache = get_or_build_cache(
            str(empty_kb), cache_path=str(tmp_path / "c.json")
        )
        # 无 md 文件时 manifest 为空，pages 也为空。
        assert cache.freshness_status in (FRESHNESS_MISSING, FRESHNESS_STALE)
        assert cache.pages == {}


# ── 3. 缓存损坏 / 版本不匹配 / knowledge_root 不匹配自动回退 ─────────


class TestCacheFallback:
    def test_corrupt_json_triggers_rebuild(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "c.json")
        get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        # 写入损坏 JSON。
        Path(cache_path).write_text("{not valid json", encoding="utf-8")
        cache = get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        # 损坏 → 回退重建（freshness=stale，错误透出）。
        assert cache.freshness_status == FRESHNESS_STALE
        assert any("缓存" in e or "损坏" in e or "不可用" in e for e in cache.errors)
        assert len(cache.pages) == 4
        # 缓存应已修复（写回）。
        with open(cache_path, "r", encoding="utf-8") as fh:
            json.load(fh)

    def test_wrong_contract_version_triggers_rebuild(
        self, fixture_kb: Path, tmp_path: Path
    ):
        cache_path = str(tmp_path / "c.json")
        get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        # 篡改 contract_version。
        with open(cache_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["contract_version"] = "kb-010-v0-old"
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        cache = get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        assert cache.freshness_status == FRESHNESS_STALE
        assert len(cache.pages) == 4

    def test_wrong_knowledge_root_triggers_rebuild(
        self, fixture_kb: Path, tmp_path: Path
    ):
        cache_path = str(tmp_path / "c.json")
        get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        # 篡改 knowledge_root。
        with open(cache_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["knowledge_root"] = "/totally/different/path"
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        cache = get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        assert cache.freshness_status == FRESHNESS_STALE
        assert len(cache.pages) == 4

    def test_load_missing_disk_cache_returns_none(self, tmp_path: Path):
        cache, err = load_cache_from_disk(str(tmp_path / "absent.json"))
        assert cache is None
        assert err is not None
        assert "不存在" in err

    def test_save_and_load_roundtrip(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "roundtrip.json")
        original = build_cache_from_scan(str(fixture_kb))
        err = save_cache_to_disk(original, cache_path)
        assert err is None
        loaded, load_err = load_cache_from_disk(cache_path)
        assert loaded is not None
        assert load_err is None
        assert loaded.contract_version == CONTRACT_VERSION
        assert loaded.knowledge_root == original.knowledge_root
        assert set(loaded.manifest.keys()) == set(original.manifest.keys())
        assert set(loaded.pages.keys()) == set(original.pages.keys())
        # 抽查一页字段保持一致。
        sample_rel = next(iter(original.pages.keys()))
        assert loaded.pages[sample_rel].title == original.pages[sample_rel].title
        assert loaded.pages[sample_rel].symbols == original.pages[sample_rel].symbols


# ── 4. 缓存命中与全量扫描语义等价 ───────────────────────────────────


class TestCachedQueryEquivalence:
    """验收核心：缓存命中不能改变交易动作，只能提高查询速度。

    对同一知识库、同一查询条件，cache 路径与全量扫描路径必须产出：
      - 相同的 status
      - 相同的 matched_pages（rel_path / summary / symbols / themes / risks / sources
        / is_stale / is_low_confidence / confidence / matched_by）
      - 相同的聚合字段
    """

    def test_symbol_query_equivalent(self, fixture_kb: Path, tmp_path: Path):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        full = query_local_knowledge(str(fixture_kb), symbol="603296")
        cached = query_local_knowledge_cached(cache, symbol="603296")
        assert cached.status == full.status
        assert len(cached.matched_pages) == len(full.matched_pages)
        assert cached.matched_pages[0].rel_path == full.matched_pages[0].rel_path
        assert cached.matched_pages[0].summary == full.matched_pages[0].summary
        assert cached.matched_pages[0].symbols == full.matched_pages[0].symbols
        assert cached.matched_pages[0].themes == full.matched_pages[0].themes
        assert cached.matched_pages[0].risks == full.matched_pages[0].risks
        assert cached.matched_pages[0].sources == full.matched_pages[0].sources
        assert cached.matched_pages[0].is_stale == full.matched_pages[0].is_stale
        assert (
            cached.matched_pages[0].is_low_confidence
            == full.matched_pages[0].is_low_confidence
        )
        assert cached.matched_pages[0].confidence == full.matched_pages[0].confidence

    def test_provider_accepts_cache_param(self, fixture_kb: Path, tmp_path: Path):
        """KB-003 query_local_knowledge(cache=...) 应委托到缓存路径。"""
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        via_cache = query_local_knowledge(str(fixture_kb), symbol="603296", cache=cache)
        full = query_local_knowledge(str(fixture_kb), symbol="603296")
        assert via_cache.status == full.status
        assert len(via_cache.matched_pages) == len(full.matched_pages)

    def test_theme_query_equivalent(self, fixture_kb: Path, tmp_path: Path):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        full = query_local_knowledge(str(fixture_kb), themes=["PCB"])
        cached = query_local_knowledge_cached(cache, themes=["PCB"])
        assert cached.status == full.status
        assert [m.rel_path for m in cached.matched_pages] == [
            m.rel_path for m in full.matched_pages
        ]

    def test_name_query_equivalent(self, fixture_kb: Path, tmp_path: Path):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        full = query_local_knowledge(str(fixture_kb), name="华勤技术")
        cached = query_local_knowledge_cached(cache, name="华勤技术")
        assert cached.status == full.status
        assert [m.rel_path for m in cached.matched_pages] == [
            m.rel_path for m in full.matched_pages
        ]

    def test_tag_query_equivalent(self, fixture_kb: Path, tmp_path: Path):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        full = query_local_knowledge(str(fixture_kb), tags=["PCB"])
        cached = query_local_knowledge_cached(cache, tags=["PCB"])
        assert cached.status == full.status
        assert [m.rel_path for m in cached.matched_pages] == [
            m.rel_path for m in full.matched_pages
        ]

    def test_no_match_equivalent(self, fixture_kb: Path, tmp_path: Path):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        full = query_local_knowledge(str(fixture_kb), symbol="999999")
        cached = query_local_knowledge_cached(cache, symbol="999999")
        assert cached.status == STATUS_NORMAL_NO_DATA
        assert full.status == STATUS_NORMAL_NO_DATA
        assert cached.matched_pages == []

    def test_no_query_condition_returns_normal_no_data(
        self, fixture_kb: Path, tmp_path: Path
    ):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        result = query_local_knowledge_cached(cache)
        assert result.status == STATUS_NORMAL_NO_DATA
        assert any("未提供查询条件" in e for e in result.errors)

    def test_max_pages_truncation(self, fixture_kb: Path, tmp_path: Path):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        # AI服务器 主题命中多个页面（华勤 + Dell）。
        cached = query_local_knowledge_cached(cache, themes=["AI服务器"], max_pages=1)
        assert len(cached.matched_pages) <= 1

    def test_summary_caps_no_raw_text(self, fixture_kb: Path, tmp_path: Path):
        """缓存命中页 summary 不输出原文（与 KB-003 长度上限一致）。"""
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        cached = query_local_knowledge_cached(cache, symbol="603296")
        for m in cached.matched_pages:
            # summary 上限 200 字符（含省略号）；不含正文中风险段列表项。
            assert len(m.summary) <= 205
            # 风险条目上限 _RISK_MAX_CHARS。
            for r in m.risks:
                assert len(r) <= 125


# ── 5. 缓存命中与全量扫描：freshness 一致性 ─────────────────────────


class TestCachedQueryFreshnessSemantics:
    def test_failed_when_knowledge_unreadable(self, tmp_path: Path):
        cache = get_or_build_cache(str(tmp_path / "no_such"))
        # manifest 为空 + 有 errors → FAILED。
        result = query_local_knowledge_cached(cache, symbol="603296")
        assert result.status == "FAILED"
        assert any("不存在" in e for e in result.errors)

    def test_failed_cache_path_carried_into_result(
        self, fixture_kb: Path, tmp_path: Path
    ):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        # 预设 cache.errors 模拟降级路径。
        cache.errors = ["模拟警告：某页解析失败"]
        result = query_local_knowledge_cached(cache, symbol="603296")
        # errors 透传到 result。
        assert any("模拟警告" in e for e in result.errors)


# ── 6. CachedPageData 与 _cached_page_to_match ───────────────────────


class TestCachedPageData:
    def test_cached_page_built_correctly(self, fixture_kb: Path):
        rel = "wiki/investment/华勤技术603296-超节点.md"
        abs_path = fixture_kb / rel
        page, err = _build_cached_page(rel, abs_path)
        assert err is None
        assert page is not None
        assert page.title.startswith("华勤技术")
        assert page.page_type in ("company", "score_table", "industry", "summary")
        assert "603296.SH 华勤技术" in page.symbols
        assert "AI服务器" in page.themes
        assert "华勤技术" in page.tags
        assert "超节点" in page.summary
        # 段落预抽取生效。
        assert any("需求" in r or "竞争" in r for r in page.risks)

    def test_cached_page_to_match_preserves_confidence(self, fixture_kb: Path):
        rel = "wiki/investment/华勤技术603296-超节点.md"
        abs_path = fixture_kb / rel
        page, _ = _build_cached_page(rel, abs_path)
        match = _cached_page_to_match(page, matched_by=["symbol"])
        assert match.rel_path == page.rel_path
        assert match.title == page.title
        assert match.symbols == page.symbols
        assert match.summary == page.summary
        assert match.is_stale is False  # valid_until=2099, stale_risk=低
        assert match.confidence in ("high", "medium")

    def test_cached_page_to_match_stale(self, fixture_kb: Path):
        rel = "wiki/investment/某周期股-已过期.md"
        abs_path = fixture_kb / rel
        page, _ = _build_cached_page(rel, abs_path)
        match = _cached_page_to_match(page, matched_by=["symbol"])
        # valid_until=2020-01-01 已过期 → is_stale=True, confidence=low。
        assert match.is_stale is True
        assert match.confidence == "low"

    def test_cached_page_to_match_todo(self, fixture_kb: Path):
        rel = "wiki/investment/Dell-FQ127-待补充.md"
        abs_path = fixture_kb / rel
        page, _ = _build_cached_page(rel, abs_path)
        match = _cached_page_to_match(page, matched_by=["symbol"])
        assert match.is_to_be_supplemented is True
        assert match.confidence == "low"

    def test_cached_page_data_roundtrip(self):
        page = CachedPageData(
            rel_path="wiki/investment/x.md",
            title="X",
            page_type="company",
            machine_readiness="high",
            symbols=["600000.SH 浦发银行"],
            themes=["银行"],
            tags=["银行"],
            summary="测试摘要",
            risks=["风险1"],
            sources=["来源1"],
            frontmatter={"title": "X", "custom_field": "value"},
        )
        d = page.to_dict()
        page2 = CachedPageData.from_dict(d)
        assert page2.rel_path == page.rel_path
        assert page2.title == page.title
        assert page2.symbols == page.symbols
        assert page2.summary == page.summary
        assert page2.frontmatter.get("custom_field") == "value"


# ── 7. freshness_summary ────────────────────────────────────────────


class TestFreshnessSummary:
    def test_summary_has_required_fields(self, fixture_kb: Path, tmp_path: Path):
        cache = get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        summary = freshness_summary(cache)
        assert summary["freshness_status"] == FRESHNESS_MISSING  # 首次构建
        assert summary["page_count"] == 4
        assert summary["manifest_size"] == 4
        assert summary["cache_path"] == str(tmp_path / "c.json")
        assert summary["reused_disk_cache"] is False
        assert isinstance(summary["errors"], list)

    def test_summary_fresh_status_second_call(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "c.json")
        get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        cache2 = get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        summary = freshness_summary(cache2)
        assert summary["freshness_status"] == FRESHNESS_FRESH
        assert summary["reused_disk_cache"] is True


# ── 8. 只读安全性 ────────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_cache_does_not_write_to_knowledge_dir(
        self, fixture_kb: Path, tmp_path_factory
    ):
        """缓存文件必须只写在调用方指定路径，不写知识库目录。"""
        # 用独立的 tmp 目录存缓存，确保与知识库目录完全隔离。
        outside = tmp_path_factory.mktemp("outside_kb")
        cache_path = str(outside / "c.json")
        get_or_build_cache(str(fixture_kb), cache_path=cache_path)
        assert os.path.exists(cache_path)
        # 知识库目录下不应出现 .json 缓存文件。
        json_files = list(fixture_kb.rglob("*.json"))
        assert json_files == []

    def test_default_cache_path_under_dotcache(self):
        path = default_cache_path()
        assert path == os.path.join(".cache", "knowledge_cache.json")

    def test_build_cache_does_not_modify_kb_files(self, fixture_kb: Path, tmp_path: Path):
        # 记录所有 md 文件 mtime/content。
        md_files = list((fixture_kb / INVESTMENT_SUBDIR).glob("*.md"))
        before = {p: (p.stat().st_mtime_ns, p.read_text(encoding="utf-8")) for p in md_files}
        get_or_build_cache(
            str(fixture_kb), cache_path=str(tmp_path / "c.json")
        )
        after = {p: (p.stat().st_mtime_ns, p.read_text(encoding="utf-8")) for p in md_files}
        for p in md_files:
            assert before[p][0] == after[p][0], f"mtime 改变: {p}"
            assert before[p][1] == after[p][1], f"内容改变: {p}"


# ── 9. CLI 集成 ──────────────────────────────────────────────────────


class TestCli:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch, fixture_kb):
        # 确保 CLI 默认知识库根目录指向 fixture。
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        # CLI 脚本在项目根目录，cwd 必须指向项目根（而非 tmp_path）。
        project_root = Path(__file__).resolve().parent.parent
        return subprocess.run(
            [sys.executable, "scripts/query_local_knowledge.py", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_cli_default_uses_cache(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "cli_cache.json")
        # 第一次：构建缓存（MISSING）。
        r1 = self._run(
            "--symbol", "603296",
            "--cache-path", cache_path,
            "--json",
        )
        assert r1.returncode == 0, r1.stderr
        assert os.path.exists(cache_path)
        # stderr 应有 freshness 透出。
        assert "[KB-010]" in r1.stderr
        # 第二次：复用缓存（FRESH）。
        r2 = self._run(
            "--symbol", "603296",
            "--cache-path", cache_path,
            "--json",
        )
        assert r2.returncode == 0, r2.stderr
        assert "freshness=fresh" in r2.stderr
        # 两次 stdout payload 应一致（缓存语义等价）。
        assert json.loads(r1.stdout) == json.loads(r2.stdout)

    def test_cli_rebuild_cache(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "cli_rebuild.json")
        self._run(
            "--symbol", "603296",
            "--cache-path", cache_path,
        )
        r = self._run(
            "--symbol", "603296",
            "--cache-path", cache_path,
            "--rebuild-cache",
        )
        assert r.returncode == 0, r.stderr
        assert "freshness=stale" in r.stderr

    def test_cli_no_cache(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "cli_nocache.json")
        r = self._run(
            "--symbol", "603296",
            "--cache-path", cache_path,
            "--no-cache",
            "--json",
        )
        assert r.returncode == 0, r.stderr
        # no_cache 不写盘。
        assert not os.path.exists(cache_path)
        # 结果仍正确。
        payload = json.loads(r.stdout)
        assert payload["status"] in ("HAS_DATA", "STALE", "LOW_CONFIDENCE")

    def test_cli_freshness_only(self, fixture_kb: Path, tmp_path: Path):
        cache_path = str(tmp_path / "cli_fr.json")
        r = self._run(
            "--cache-path", cache_path,
            "--freshness-only",
        )
        assert r.returncode == 0, r.stderr
        summary = json.loads(r.stdout)
        assert "freshness_status" in summary
        assert summary["page_count"] == 4

    def test_cli_freshness_only_no_query_required(self, fixture_kb: Path, tmp_path: Path):
        """--freshness-only 不应要求 --symbol 等查询条件。"""
        r = self._run(
            "--cache-path", str(tmp_path / "f.json"),
            "--freshness-only",
        )
        assert r.returncode == 0, r.stderr

    def test_cli_requires_query_without_freshness_only(
        self, fixture_kb: Path, tmp_path: Path
    ):
        r = self._run(
            "--cache-path", str(tmp_path / "f.json"),
        )
        assert r.returncode != 0
        assert "至少需要" in r.stderr or "至少需要" in r.stdout


# ── 10. 模块常量 ─────────────────────────────────────────────────────


class TestModuleConstants:
    def test_task_code(self):
        assert TASK_CODE == "KB-010"

    def test_contract_version(self):
        assert CONTRACT_VERSION == "kb-010-v1"

    def test_freshness_constants_distinct(self):
        vals = {FRESHNESS_FRESH, FRESHNESS_STALE, FRESHNESS_MISSING, FRESHNESS_ERROR}
        assert len(vals) == 4
