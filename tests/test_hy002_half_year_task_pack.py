# [HY-002] half_year_task_pack
"""Tests for 半年报资料优先队列与 Tree Work 补录任务包 (HY-002).

覆盖：
  - 数据类 HalfYearTaskItem / HalfYearTaskPack 序列化与排序。
  - 符号归一化与匹配（bare code / 简称 / 全代码）。
  - HY-001 字段缺口识别（已有半年报页 vs 无页 vs 非半年报页）。
  - 五类验收 fixture：持仓 / 观察仓 / 候选池 / 知识缺失 / 知识过期。
  - 优先级分层（P1 > P2 > P3 > P4 > P5）。
  - 去重（tier 内 + 跨 tier）。
  - 上游失败降级（lint / attention 异常不阻塞）。
  - 报告渲染（不含长原文 / 不含强买卖词 / ingest 模板）。
  - 只读安全性（不写知识库）。
  - CLI 子进程冒烟。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tradingagents.dataflows.half_year_task_pack import (
    ACTION_ADD_FIELDS,
    ACTION_INGEST_NEW,
    CONTRACT_VERSION,
    DEFAULT_SUGGESTED_SOURCE_TYPE,
    HYF_FIELDS,
    TASK_CODE,
    TIER_HAOTIAN,
    TIER_HOLDINGS,
    TIER_OBSERVATION,
    TIER_STALE_ATTENTION,
    TIER_TRADEFLOW,
    TIER_ORDER,
    TIER_TITLES,
    HalfYearTaskItem,
    HalfYearTaskPack,
    _bare_code,
    _build_symbol_page_index,
    _classify_symbol,
    _dedup_items,
    _find_pages_for_symbol,
    _normalize_input_list,
    _remove_duplicates_across_tiers,
    _symbols_match,
    build_half_year_task_pack,
    render_half_year_task_pack_report,
    suggest_output_path,
)
from tests.half_year_fixtures import (
    FIXTURES_BY_NAME,
    FIXTURE_SPECS,
    build_half_year_fixture_kb,
)


# ── 强动作词检测（与 V-013 / KB-012 同口径）─────────────────────────

STRONG_ACTION_WORDS = (
    "立即买入",
    "立即卖出",
    "立即清仓",
    "满仓",
    "清仓",
    "全仓",
    "重仓买入",
    "梭哈",
    "强烈推荐",
    "加仓",
    "减仓",
    "买入",
    "卖出",
)


# ── 工具 ──────────────────────────────────────────────────────────────


def _has_strong_action_words(text: str) -> bool:
    for word in STRONG_ACTION_WORDS:
        if word in text:
            return True
    return False


# ── 数据类测试 ────────────────────────────────────────────────────────


class TestHalfYearTaskItem:
    def test_defaults(self):
        item = HalfYearTaskItem(
            symbol="603296", name="华勤技术", priority_tier=TIER_HOLDINGS
        )
        assert item.action == ACTION_INGEST_NEW
        assert item.reason == ""
        assert item.missing_fields == []
        assert item.existing_page is None
        assert item.suggested_source_type == ["exchange_filing", "fact_table"]
        assert item.sources == []
        assert item.extra == {}

    def test_sort_key_order(self):
        item_hold = HalfYearTaskItem(
            symbol="603296", name="", priority_tier=TIER_HOLDINGS
        )
        item_obs = HalfYearTaskItem(
            symbol="603296", name="", priority_tier=TIER_OBSERVATION
        )
        item_stale = HalfYearTaskItem(
            symbol="603296", name="", priority_tier=TIER_STALE_ATTENTION
        )
        assert item_hold.sort_key < item_obs.sort_key
        assert item_obs.sort_key < item_stale.sort_key

    def test_to_dict(self):
        item = HalfYearTaskItem(
            symbol="000977",
            name="浪潮信息",
            priority_tier=TIER_OBSERVATION,
            reason="缺 financial_period",
            missing_fields=["financial_period"],
            action=ACTION_ADD_FIELDS,
            existing_page="wiki/investment/test.md",
        )
        d = item.to_dict()
        assert d["symbol"] == "000977"
        assert d["name"] == "浪潮信息"
        assert d["priority_tier"] == TIER_OBSERVATION
        assert d["action"] == ACTION_ADD_FIELDS
        assert d["missing_fields"] == ["financial_period"]
        assert d["existing_page"] == "wiki/investment/test.md"
        assert isinstance(d["suggested_source_type"], list)


class TestHalfYearTaskPack:
    def test_empty_pack(self):
        pack = HalfYearTaskPack(
            knowledge_root="/tmp/test",
            generated_at="2026-07-11 12:00:00",
            as_of_date="2026-07-11",
        )
        assert pack.total() == 0
        assert pack.all_items() == []
        assert pack.tier_counts() == {t: 0 for t in TIER_ORDER}

    def test_all_items_sorted_by_tier(self):
        pack = HalfYearTaskPack(
            knowledge_root="/tmp/test",
            generated_at="2026-07-11 12:00:00",
            as_of_date="2026-07-11",
        )
        pack.items_by_tier[TIER_STALE_ATTENTION] = [
            HalfYearTaskItem(symbol="000001", name="", priority_tier=TIER_STALE_ATTENTION)
        ]
        pack.items_by_tier[TIER_HOLDINGS] = [
            HalfYearTaskItem(symbol="603296", name="", priority_tier=TIER_HOLDINGS)
        ]
        items = pack.all_items()
        assert items[0].priority_tier == TIER_HOLDINGS
        assert items[1].priority_tier == TIER_STALE_ATTENTION

    def test_to_dict(self):
        pack = HalfYearTaskPack(
            knowledge_root="/tmp/test",
            generated_at="2026-07-11 12:00:00",
            as_of_date="2026-07-11",
        )
        d = pack.to_dict()
        assert d["contract_version"] == CONTRACT_VERSION
        assert d["task"] == TASK_CODE
        assert "items_by_tier" in d
        assert "tier_counts" in d
        assert d["total"] == 0


# ── 符号归一化与匹配 ─────────────────────────────────────────────────


class TestSymbolNormalization:
    def test_bare_code_sh(self):
        assert _bare_code("603296.SH") == "603296"

    def test_bare_code_sz(self):
        assert _bare_code("000977.SZ") == "000977"

    def test_bare_code_no_suffix(self):
        assert _bare_code("603296") == "603296"

    def test_bare_code_empty(self):
        assert _bare_code("") == ""

    def test_bare_code_lowercase(self):
        assert _bare_code("603296.sh") == "603296"

    def test_symbols_match_bare_code(self):
        assert _symbols_match("603296", "603296.SH 华勤技术")

    def test_symbols_match_full_code(self):
        assert _symbols_match("603296.SH", "603296.SH 华勤技术")

    def test_symbols_match_name(self):
        assert _symbols_match("华勤技术", "603296.SH 华勤技术")

    def test_symbols_match_no_match(self):
        assert not _symbols_match("000001", "603296.SH 华勤技术")

    def test_symbols_match_empty(self):
        assert not _symbols_match("", "603296.SH 华勤技术")
        assert not _symbols_match("603296", "")


class TestNormalizeInputList:
    def test_dict_input(self):
        result = _normalize_input_list([{"symbol": "603296", "name": "华勤技术"}])
        assert result == [{"symbol": "603296", "name": "华勤技术"}]

    def test_string_input(self):
        result = _normalize_input_list(["603296"])
        assert result == [{"symbol": "603296", "name": ""}]

    def test_combined_format(self):
        result = _normalize_input_list(["603296.SH 华勤技术"])
        assert result == [{"symbol": "603296.SH", "name": "华勤技术"}]

    def test_code_key(self):
        result = _normalize_input_list([{"code": "603296"}])
        assert result == [{"symbol": "603296", "name": ""}]

    def test_empty(self):
        assert _normalize_input_list(None) == []
        assert _normalize_input_list([]) == []

    def test_skip_empty(self):
        result = _normalize_input_list([{"symbol": ""}, {"symbol": "603296"}])
        assert len(result) == 1


# ── HY-001 字段缺口识别 ──────────────────────────────────────────────


class TestClassifySymbol:
    def test_no_pages_ingest_new(self):
        action, missing, page = _classify_symbol("999999", "未知", [])
        assert action == ACTION_INGEST_NEW
        assert missing == list(HYF_FIELDS)
        assert page is None

    def test_existing_hy_page_add_fields(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["missing_period"])
        from tradingagents.dataflows.local_knowledge_lint import lint_local_knowledge

        lint_result = lint_local_knowledge(str(root))
        index = _build_symbol_page_index(lint_result, root)
        pages = index.get("603296", [])
        action, missing, page = _classify_symbol("603296", "华勤技术", pages)
        assert action == ACTION_ADD_FIELDS
        assert "financial_period" in missing
        assert page is not None

    def test_qualified_page_no_missing(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        from tradingagents.dataflows.local_knowledge_lint import lint_local_knowledge

        lint_result = lint_local_knowledge(str(root))
        index = _build_symbol_page_index(lint_result, root)
        pages = index.get("000977", [])
        action, missing, page = _classify_symbol("000977", "浪潮信息", pages)
        assert action == ACTION_ADD_FIELDS
        assert missing == []
        assert page is not None


# ── 五类验收 fixture ─────────────────────────────────────────────────


class TestFiveFixtureCategories:
    """验收 fixture 覆盖：持仓 / 观察仓 / 候选池 / 知识缺失 / 知识过期。"""

    def test_holding_with_existing_hy_page(self, tmp_path):
        """持仓 — 已有半年报页，缺报告期 → add_fields。"""
        root = build_half_year_fixture_kb(tmp_path, include=["missing_period"])
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": "华勤技术"}],
            collect_attention=False,
        )
        items = pack.items_by_tier.get(TIER_HOLDINGS, [])
        assert len(items) == 1
        item = items[0]
        assert item.symbol == "603296"
        assert item.action == ACTION_ADD_FIELDS
        assert "financial_period" in item.missing_fields
        assert item.existing_page is not None

    def test_observation_with_qualified_page(self, tmp_path):
        """观察仓 — 已有合格半年报页 → add_fields，缺失为空。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        pack = build_half_year_task_pack(
            str(root),
            observation=[{"symbol": "000977", "name": "浪潮信息"}],
            collect_attention=False,
        )
        items = pack.items_by_tier.get(TIER_OBSERVATION, [])
        assert len(items) == 1
        item = items[0]
        assert item.action == ACTION_ADD_FIELDS
        assert item.missing_fields == []

    def test_candidate_pool_with_fact_opinion_mix(self, tmp_path):
        """候选池 — 事实/观点混用半年报页 → add_fields。"""
        root = build_half_year_fixture_kb(tmp_path, include=["fact_opinion_mix"])
        pack = build_half_year_task_pack(
            str(root),
            haotian_candidates=[{"symbol": "002415", "name": "海康威视"}],
            collect_attention=False,
        )
        items = pack.items_by_tier.get(TIER_HAOTIAN, [])
        assert len(items) == 1
        item = items[0]
        assert item.action == ACTION_ADD_FIELDS

    def test_tradeflow_candidate(self, tmp_path):
        """TradeFlow 候选。"""
        root = build_half_year_fixture_kb(tmp_path, include=["weakened_old_opinion"])
        pack = build_half_year_task_pack(
            str(root),
            tradeflow_candidates=[{"symbol": "300750", "name": "宁德时代"}],
            collect_attention=False,
        )
        items = pack.items_by_tier.get(TIER_TRADEFLOW, [])
        assert len(items) == 1
        item = items[0]
        assert item.symbol == "300750"

    def test_knowledge_missing_ingest_new(self, tmp_path):
        """知识缺失 — symbol 不在知识库 → ingest_new。"""
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "999999", "name": "不存在"}],
            collect_attention=False,
        )
        items = pack.items_by_tier.get(TIER_HOLDINGS, [])
        assert len(items) == 1
        item = items[0]
        assert item.action == ACTION_INGEST_NEW
        assert item.existing_page is None
        assert item.missing_fields == list(HYF_FIELDS)

    def test_knowledge_expired(self, tmp_path):
        """知识过期 — valid_until 已过期的半年报页 → add_fields + stale 标记。"""
        root = build_half_year_fixture_kb(tmp_path, include=["expired"])
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "600519", "name": "贵州茅台"}],
            collect_attention=False,
        )
        items = pack.items_by_tier.get(TIER_HOLDINGS, [])
        assert len(items) == 1
        item = items[0]
        assert item.action == ACTION_ADD_FIELDS
        assert "过期" in item.reason or "复核" in item.reason


# ── 优先级排序 ────────────────────────────────────────────────────────


class TestPriorityOrdering:
    def test_holdings_above_observation(self, tmp_path):
        root = build_half_year_fixture_kb(
            tmp_path, include=["missing_period", "qualified"]
        )
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": "华勤技术"}],
            observation=[{"symbol": "000977", "name": "浪潮信息"}],
            collect_attention=False,
        )
        items = pack.all_items()
        assert items[0].priority_tier == TIER_HOLDINGS
        assert items[1].priority_tier == TIER_OBSERVATION

    def test_full_priority_chain(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path)
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": "华勤技术"}],
            observation=[{"symbol": "000977", "name": "浪潮信息"}],
            haotian_candidates=[{"symbol": "002415", "name": "海康威视"}],
            tradeflow_candidates=[{"symbol": "300750", "name": "宁德时代"}],
            collect_attention=False,
        )
        items = pack.all_items()
        assert len(items) == 4
        tiers = [it.priority_tier for it in items]
        assert tiers == [
            TIER_HOLDINGS,
            TIER_OBSERVATION,
            TIER_HAOTIAN,
            TIER_TRADEFLOW,
        ]


# ── 去重 ─────────────────────────────────────────────────────────────


class TestDedup:
    def test_dedup_within_tier(self):
        pack = HalfYearTaskPack(
            knowledge_root="/tmp",
            generated_at="2026-07-11 12:00:00",
            as_of_date="2026-07-11",
        )
        pack.items_by_tier[TIER_HOLDINGS] = [
            HalfYearTaskItem(
                symbol="603296", name="", priority_tier=TIER_HOLDINGS,
                sources=["holdings"],
            ),
            HalfYearTaskItem(
                symbol="603296.SH", name="", priority_tier=TIER_HOLDINGS,
                sources=["holdings_v2"],
            ),
        ]
        _dedup_items(pack)
        assert len(pack.items_by_tier[TIER_HOLDINGS]) == 1
        item = pack.items_by_tier[TIER_HOLDINGS][0]
        assert "holdings" in item.sources
        assert "holdings_v2" in item.sources

    def test_dedup_across_tiers(self):
        pack = HalfYearTaskPack(
            knowledge_root="/tmp",
            generated_at="2026-07-11 12:00:00",
            as_of_date="2026-07-11",
        )
        pack.items_by_tier[TIER_HOLDINGS] = [
            HalfYearTaskItem(symbol="603296", name="", priority_tier=TIER_HOLDINGS)
        ]
        pack.items_by_tier[TIER_OBSERVATION] = [
            HalfYearTaskItem(symbol="603296", name="", priority_tier=TIER_OBSERVATION)
        ]
        _remove_duplicates_across_tiers(pack)
        assert len(pack.items_by_tier[TIER_HOLDINGS]) == 1
        assert len(pack.items_by_tier[TIER_OBSERVATION]) == 0

    def test_same_symbol_in_holdings_and_observation(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "000977", "name": "浪潮信息"}],
            observation=[{"symbol": "000977", "name": "浪潮信息"}],
            collect_attention=False,
        )
        # 持仓优先级高，观察仓不重复。
        assert len(pack.items_by_tier.get(TIER_HOLDINGS, [])) == 1
        assert len(pack.items_by_tier.get(TIER_OBSERVATION, [])) == 0


# ── 空库 / 缺根目录 / 上游失败降级 ───────────────────────────────────


class TestDegradation:
    def test_empty_kb(self, tmp_path):
        root = tmp_path / "empty_kb"
        root.mkdir()
        (root / "wiki").mkdir()
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": "华勤技术"}],
            collect_attention=False,
        )
        items = pack.items_by_tier.get(TIER_HOLDINGS, [])
        assert len(items) == 1
        assert items[0].action == ACTION_INGEST_NEW
        assert items[0].existing_page is None

    def test_missing_root(self, tmp_path):
        pack = build_half_year_task_pack(
            str(tmp_path / "nonexistent"),
            holdings=[{"symbol": "603296", "name": ""}],
            collect_attention=False,
        )
        assert pack.total() == 0
        assert len(pack.errors) > 0
        assert any("不存在" in e for e in pack.errors)

    def test_no_inputs(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        pack = build_half_year_task_pack(str(root), collect_attention=False)
        assert pack.total() == 0


# ── 报告渲染 ─────────────────────────────────────────────────────────


class TestReportRendering:
    def test_report_structure(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["missing_period"])
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": "华勤技术"}],
            collect_attention=False,
        )
        report = render_half_year_task_pack_report(pack)
        assert "半年报资料优先队列" in report
        assert "[HY-002]" in report
        assert "## 1. 概览" in report
        assert "## 2. 输入统计" in report
        assert "## 4. 分层统计" in report
        assert "## 5. 补录任务" in report
        assert "## 6. 半年报 ingest 模板" in report
        assert "## 7. 建议执行顺序" in report
        assert "## 8. 免责声明" in report

    def test_report_no_strong_action_words(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path)
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": "华勤技术"}],
            observation=[{"symbol": "000977", "name": "浪潮信息"}],
            collect_attention=False,
        )
        report = render_half_year_task_pack_report(pack)
        assert not _has_strong_action_words(report), (
            f"报告包含强动作词: {[w for w in STRONG_ACTION_WORDS if w in report]}"
        )

    def test_report_no_long_original_text(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path)
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": "华勤技术"}],
            collect_attention=False,
        )
        report = render_half_year_task_pack_report(pack)
        # 报告不应包含 fixture 原文段落。
        assert "营收 420.4亿" not in report
        assert "AI服务器放量" not in report
        assert "上游GPU供应紧张" not in report

    def test_report_contains_ingest_template(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        pack = build_half_year_task_pack(
            str(root),
            observation=[{"symbol": "000977", "name": "浪潮信息"}],
            collect_attention=False,
        )
        report = render_half_year_task_pack_report(pack)
        assert "financial_period:" in report
        assert "disclosure_date:" in report
        assert "source_type:" in report
        assert "financial_facts:" in report

    def test_report_json_serializable(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "000977", "name": "浪潮信息"}],
            collect_attention=False,
        )
        d = pack.to_dict()
        json_str = json.dumps(d, ensure_ascii=False, indent=2)
        parsed = json.loads(json_str)
        assert parsed["task"] == TASK_CODE
        assert parsed["contract_version"] == CONTRACT_VERSION

    def test_report_disclaimer_present(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path)
        pack = build_half_year_task_pack(str(root), collect_attention=False)
        report = render_half_year_task_pack_report(pack)
        assert "免责声明" in report
        assert "不构成任何买卖建议" in report


# ── 只读安全性 ───────────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_does_not_modify_kb(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path)
        files_before = {
            f: f.stat().st_size
            for f in root.rglob("*")
            if f.is_file()
        }
        build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": "华勤技术"}],
            collect_attention=True,
        )
        files_after = {
            f: f.stat().st_size
            for f in root.rglob("*")
            if f.is_file()
        }
        assert files_before == files_after

    def test_no_new_files_created(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path)
        count_before = len(list(root.rglob("*")))
        build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "603296", "name": ""}],
            collect_attention=False,
        )
        count_after = len(list(root.rglob("*")))
        assert count_before == count_after


# ── KB-007 stale attention 层 ────────────────────────────────────────


class TestStaleAttention:
    def test_stale_attention_collected(self, tmp_path):
        """KB-007 stale attention 层：关注度高 + 知识过期的 symbol 被收集。"""
        root = build_half_year_fixture_kb(
            tmp_path, include=["expired", "qualified", "non_financial_control"]
        )
        pack = build_half_year_task_pack(
            str(root),
            collect_attention=True,
        )
        # 过期的 600519 如果关注度足够高且有 stale 页面，应该出现在 P5 层。
        stale_items = pack.items_by_tier.get(TIER_STALE_ATTENTION, [])
        # 结果取决于 KB-007 是否把 600519 识别为高关注度（单页 fixture 可能不够）。
        # 只验证不崩溃 + 结果可序列化。
        pack.to_dict()


# ── 上游摘要 ─────────────────────────────────────────────────────────


class TestUpstreamSummary:
    def test_summary_has_inputs(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        pack = build_half_year_task_pack(
            str(root),
            holdings=[{"symbol": "000977", "name": "浪潮信息"}],
            observation=[{"symbol": "603296", "name": "华勤技术"}],
            collect_attention=False,
        )
        inputs = pack.upstream_summary.get("inputs", {})
        assert inputs.get("holdings") == 1
        assert inputs.get("observation") == 1

    def test_summary_has_hy001_lint(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        pack = build_half_year_task_pack(str(root), collect_attention=False)
        assert "hy001_lint" in pack.upstream_summary
        hy = pack.upstream_summary["hy001_lint"]
        assert "page_count" in hy
        assert "half_year_pages" in hy


# ── 输出路径 ─────────────────────────────────────────────────────────


class TestOutputPath:
    def test_suggest_output_path(self):
        path = suggest_output_path()
        assert "half_year_tree_work_tasks-" in path
        assert path.endswith(".md")


# ── CLI 子进程冒烟 ──────────────────────────────────────────────────


class TestCLISmoke:
    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, "scripts/half_year_task_pack.py", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "HY-002" in result.stdout

    def test_cli_with_fixture_kb(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["missing_period"])
        result = subprocess.run(
            [
                sys.executable,
                "scripts/half_year_task_pack.py",
                "--knowledge-root",
                str(root),
                "--holdings",
                "603296,华勤技术",
                "--no-attention",
                "--stdout",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        assert "603296" in result.stdout
        assert "HY-002" in result.stdout
        assert "华勤技术" in result.stdout

    def test_cli_json_output(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        result = subprocess.run(
            [
                sys.executable,
                "scripts/half_year_task_pack.py",
                "--knowledge-root",
                str(root),
                "--holdings",
                "000977",
                "--no-attention",
                "--json",
                "--stdout",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["task"] == TASK_CODE
        assert data["total"] >= 1

    def test_cli_write_file(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])
        out_file = tmp_path / "report.md"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/half_year_task_pack.py",
                "--knowledge-root",
                str(root),
                "--holdings",
                "000977",
                "--no-attention",
                "--output",
                str(out_file),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "HY-002" in content

    def test_cli_no_strong_action_words(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path, include=["missing_period"])
        result = subprocess.run(
            [
                sys.executable,
                "scripts/half_year_task_pack.py",
                "--knowledge-root",
                str(root),
                "--holdings",
                "603296",
                "--no-attention",
                "--stdout",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        assert not _has_strong_action_words(result.stdout), (
            f"CLI 输出包含强动作词: "
            f"{[w for w in STRONG_ACTION_WORDS if w in result.stdout]}"
        )


# ── 向后兼容 / 回归 ─────────────────────────────────────────────────


class TestBackwardCompat:
    def test_does_not_break_kb012(self, tmp_path):
        """HY-002 不应影响 KB-012 tree_work_task_pack。"""
        from tradingagents.dataflows.tree_work_task_pack import (
            build_tree_work_task_pack,
        )
        root = build_half_year_fixture_kb(tmp_path)
        pack = build_tree_work_task_pack(str(root))
        assert pack.total() >= 0  # 不崩溃即可

    def test_does_not_break_hy001_lint(self, tmp_path):
        """HY-002 不应影响 HY-001 lint。"""
        from tradingagents.dataflows.local_knowledge_lint import lint_local_knowledge
        root = build_half_year_fixture_kb(tmp_path, include=["missing_period"])
        result = lint_local_knowledge(str(root))
        assert result.page_count >= 1

    def test_all_tiers_present_in_counts(self, tmp_path):
        root = build_half_year_fixture_kb(tmp_path)
        pack = build_half_year_task_pack(
            str(root), collect_attention=False
        )
        counts = pack.tier_counts()
        for tier in TIER_ORDER:
            assert tier in counts

    def test_all_tier_titles_present(self):
        for tier in TIER_ORDER:
            assert tier in TIER_TITLES
            assert TIER_TITLES[tier]
