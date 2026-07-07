# [KB-013] half_year_fixture_baseline
"""KB-013 半年报 fixture 样本集与契约回放基线测试。

目标（来自 docs/TASKS.md KB-013）：
  - fixture 测试可独立运行；
  - 不依赖真实知识库内容；
  - 可被 HY-001 / HY-003 / HY-005 后续任务复用。

覆盖：
  - 字段命名规约：symbol / name / source_type / financial_period 一致性；
  - 每个 fixture 单页跑 KB-002 + HY-001 lint，命中 :class:`HalfYearFixtureSpec` 的
    预期契约（readiness / HYF error / HYF warning / STALE / opinion_only / period）；
  - 整库聚合：包含全部样本的微型知识库 lint 无异常、half_year 页数与预期一致；
  - fixture 自治：不访问 ``~/Documents/knowledge``、可任意子集写入；
  - 只读安全 / 无长篇正文 / 无强动作词。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import pytest

from tradingagents.dataflows.local_knowledge_lint import (
    HALF_YEAR_REPORT_TYPES,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    SOURCE_TYPE_FACT_VALUES,
    SOURCE_TYPE_OPINION_VALUES,
    lint_local_knowledge,
    lint_single_page,
)
from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INVESTMENT_SUBDIR,
    LOG_MD,
)

from tests.half_year_fixtures import (
    FACT_OPINION_MIX_PAGE,
    FIXTURES_BY_NAME,
    FIXTURE_SPECS,
    FINANCIAL_PERIOD_EXAMPLES,
    FINANCIAL_PERIOD_PREFERRED,
    MISSING_FINANCIAL_PERIOD_PAGE,
    NON_FINANCIAL_CONTROL_PAGE,
    QUALIFIED_HALF_YEAR_PAGE,
    SOURCE_TYPE_ALL_VALUES_TUPLE,
    SYMBOL_EXAMPLES,
    SYMBOL_FORMAT,
    WEAKENED_OLD_OPINION_PAGE,
    EXPIRED_HALF_YEAR_PAGE,
    HalfYearFixtureSpec,
    build_half_year_fixture_kb,
)

#: 全部禁止出现的强动作词（项目级约定）。
STRONG_ACTION_VERBS = ("立即买入", "卖出", "满仓", "清仓", "全仓", "强烈推荐")


# ── 1. 字段命名规约 ──────────────────────────────────────────────────


class TestFieldNamingConvention:
    """KB-013 要求统一 symbol / name / source_type / financial_period 字段命名。"""

    def test_symbol_format_documented(self):
        assert SYMBOL_FORMAT == "<code>.<exchange> <name>"
        assert SYMBOL_EXAMPLES, "应提供至少一个 symbol 示例"

    @pytest.mark.parametrize("example", SYMBOL_EXAMPLES)
    def test_symbol_examples_match_format(self, example: str):
        # 形如 ``XXXXXX.XX 名称``（代码.交易所 单空格 简称）。
        code, _, name = example.partition(" ")
        assert "." in code, f"symbol 代码缺少交易所后缀: {example}"
        assert code.split(".")[-1] in {"SH", "SZ", "BJ", "HK", "US"}, example
        assert name, f"symbol 缺少简称: {example}"

    def test_source_type_table_consistent_with_lint(self):
        # fixture 模块的取值表必须与 HY-001 lint 常量同源。
        assert set(SOURCE_TYPE_FACT_VALUES) <= set(SOURCE_TYPE_ALL_VALUES_TUPLE)
        assert set(SOURCE_TYPE_OPINION_VALUES) <= set(SOURCE_TYPE_ALL_VALUES_TUPLE)
        assert set(SOURCE_TYPE_FACT_VALUES) ^ set(SOURCE_TYPE_OPINION_VALUES)

    def test_financial_period_preferred_format(self):
        assert FINANCIAL_PERIOD_PREFERRED == ("YYYYH1", "YYYYH2")
        assert "2025H1" in FINANCIAL_PERIOD_EXAMPLES

    def test_half_year_report_types_covered(self):
        # fixture 必须覆盖 HY-001 三种触发 report_type 中的至少两种。
        used_types = {spec.report_type for spec in FIXTURE_SPECS}
        assert used_types & set(HALF_YEAR_REPORT_TYPES), used_types


# ── 2. fixture 内容静态检查 ──────────────────────────────────────────


class TestFixtureContent:
    """不跑 lint，仅静态校验 fixture 文本本身。"""

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_has_frontmatter_and_sections(self, spec: HalfYearFixtureSpec):
        assert spec.content.startswith("---\n"), f"{spec.name} 缺少 frontmatter 起始 ---"
        # 至少包含一句话总结 / 投资逻辑 / 风险提示 三个章节（KB-002 SEC-001/002/004）。
        assert "## 一句话总结" in spec.content
        assert "## 投资逻辑" in spec.content
        assert "## 风险提示" in spec.content

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_no_strong_action_verbs(self, spec: HalfYearFixtureSpec):
        for verb in STRONG_ACTION_VERBS:
            assert verb not in spec.content, f"{spec.name} 含强动作词 {verb}"

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_no_long_original_text(self, spec: HalfYearFixtureSpec):
        # KB-003 _SUMMARY_MAX_CHARS=200 约束：单个 financial_fact 条目不应是长段落。
        for line in spec.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") and any(
                kw in stripped for kw in ("营收", "净利", " YoY", "披露口径")
            ):
                assert len(stripped) <= 200, (
                    f"{spec.name} financial_facts 条目过长（>200 字）: {stripped}"
                )

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_spec_serializable(self, spec: HalfYearFixtureSpec):
        # spec 必须可被 dataclasses.asdict 转 JSON-friendly 结构（供 baseline 报告导出）。
        from dataclasses import asdict, is_dataclass

        assert is_dataclass(spec)
        d = asdict(spec)
        assert isinstance(d["name"], str)
        assert isinstance(d["expected_symbols"], list)

    def test_fixture_names_unique(self):
        names = [s.name for s in FIXTURE_SPECS]
        assert len(names) == len(set(names)), f"fixture name 重复: {names}"

    def test_required_scenarios_present(self):
        # KB-013 任务明列的 5 个场景 + 控制组。
        names = set(FIXTURES_BY_NAME)
        for required in (
            "qualified",
            "missing_period",
            "fact_opinion_mix",
            "expired",
            "weakened_old_opinion",
            "non_financial_control",
        ):
            assert required in names, f"缺少必需 fixture: {required}"


# ── 3. 单页 lint 回放（黄金基线）──────────────────────────────────────


def _lint_spec(spec: HalfYearFixtureSpec, tmp_path: Path):
    """把单个 spec 写成单页知识库并 lint，返回 (PageLintResult, KnowledgeLintResult)。"""
    root = build_half_year_fixture_kb(tmp_path, include=[spec.name])
    kb_result = lint_local_knowledge(str(root))
    assert kb_result.page_count == 1, (
        f"{spec.name}: 期望单页知识库，实际 {kb_result.page_count} 页"
    )
    page = kb_result.page_results[0]
    return page, kb_result


class TestSinglePageLintBaseline:
    """每个 fixture 的实际 lint 结果必须命中其 HalfYearFixtureSpec 预期契约。"""

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_half_year_detection(self, spec: HalfYearFixtureSpec, tmp_path: Path):
        page, _ = _lint_spec(spec, tmp_path)
        assert page.is_half_year_report is spec.is_half_year, (
            f"{spec.name}: is_half_year_report 预期 {spec.is_half_year}，"
            f"实际 {page.is_half_year_report}"
        )

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_readiness(self, spec: HalfYearFixtureSpec, tmp_path: Path):
        page, _ = _lint_spec(spec, tmp_path)
        assert page.machine_readiness == spec.expected_readiness, (
            f"{spec.name}: readiness 预期 {spec.expected_readiness}，"
            f"实际 {page.machine_readiness}；findings="
            f"{[(f.rule_id, f.severity) for f in page.findings]}"
        )

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_hyf_errors_match(self, spec: HalfYearFixtureSpec, tmp_path: Path):
        page, _ = _lint_spec(spec, tmp_path)
        actual = sorted(
            f.rule_id
            for f in page.findings
            if f.rule_id.startswith("HYF-") and f.severity == SEVERITY_ERROR
        )
        assert actual == sorted(spec.expected_hyf_errors), (
            f"{spec.name}: HYF error 预期 {spec.expected_hyf_errors}，实际 {actual}"
        )

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_hyf_warnings_match(self, spec: HalfYearFixtureSpec, tmp_path: Path):
        page, _ = _lint_spec(spec, tmp_path)
        actual = sorted(
            f.rule_id
            for f in page.findings
            if f.rule_id.startswith("HYF-") and f.severity == SEVERITY_WARNING
        )
        assert actual == sorted(spec.expected_hyf_warnings), (
            f"{spec.name}: HYF warning 预期 {spec.expected_hyf_warnings}，实际 {actual}"
        )

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_other_warnings_match(self, spec: HalfYearFixtureSpec, tmp_path: Path):
        page, _ = _lint_spec(spec, tmp_path)
        actual = sorted(
            f.rule_id
            for f in page.findings
            if not f.rule_id.startswith("HYF-") and f.severity == SEVERITY_WARNING
        )
        assert actual == sorted(spec.expected_other_warnings), (
            f"{spec.name}: 非 HYF warning 预期 {spec.expected_other_warnings}，实际 {actual}"
        )

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_half_year_period(self, spec: HalfYearFixtureSpec, tmp_path: Path):
        page, _ = _lint_spec(spec, tmp_path)
        assert page.half_year_period == spec.expected_half_year_period, (
            f"{spec.name}: half_year_period 预期 {spec.expected_half_year_period}，"
            f"实际 {page.half_year_period}"
        )

    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_opinion_only(self, spec: HalfYearFixtureSpec, tmp_path: Path):
        page, _ = _lint_spec(spec, tmp_path)
        assert page.half_year_opinion_only is spec.expected_opinion_only, (
            f"{spec.name}: opinion_only 预期 {spec.expected_opinion_only}，"
            f"实际 {page.half_year_opinion_only}"
        )


# ── 4. 关键场景显式断言（便于 failure 定位）──────────────────────────


class TestKeyScenarioSemantics:
    """对几个关键场景做语义级显式断言，防止参数化失败时丢失上下文。"""

    def test_qualified_has_no_findings(self, tmp_path: Path):
        page, _ = _lint_spec(FIXTURES_BY_NAME["qualified"], tmp_path)
        assert page.error_count == 0
        assert page.warning_count == 0
        assert page.machine_readiness == "high"
        assert page.is_half_year_report is True
        assert page.half_year_period == "2025H1"

    def test_missing_period_triggers_hyf001(self, tmp_path: Path):
        page, _ = _lint_spec(FIXTURES_BY_NAME["missing_period"], tmp_path)
        rules = {f.rule_id for f in page.findings}
        assert "HYF-001" in rules
        assert page.error_count >= 1
        # 单点失败：除 HYF-001 外不应有其它 HYF finding。
        hyf_findings = {r for r in rules if r.startswith("HYF-")}
        assert hyf_findings == {"HYF-001"}

    def test_fact_opinion_mix_not_opinion_only(self, tmp_path: Path):
        page, _ = _lint_spec(FIXTURES_BY_NAME["fact_opinion_mix"], tmp_path)
        rules = {f.rule_id for f in page.findings}
        assert "HYF-007" not in rules, "事实/观点混用不应触发 HYF-007"
        assert page.half_year_opinion_only is False
        assert page.machine_readiness == "high"

    def test_expired_triggers_stale002(self, tmp_path: Path):
        page, _ = _lint_spec(FIXTURES_BY_NAME["expired"], tmp_path)
        rules = {f.rule_id for f in page.findings}
        assert "STALE-002" in rules
        assert page.is_stale is True
        # is_stale 阻止 high → 即使 0 error 也应落到 medium。
        assert page.machine_readiness == "medium"
        # HY 字段齐全，不应有 HYF finding。
        assert not any(r.startswith("HYF-") for r in rules)

    def test_weakened_passes_lint_but_has_contradiction_signal(self, tmp_path: Path):
        """当前 lint 不检测矛盾（HY-005 才做），故通过；但 fixture 必须含矛盾数据。"""
        spec = FIXTURES_BY_NAME["weakened_old_opinion"]
        page, _ = _lint_spec(spec, tmp_path)
        assert page.machine_readiness == "high"
        assert page.error_count == 0
        # 矛盾信号：management_commentary 含“爆发式增长”，financial_facts 含“-20.0%”。
        assert "爆发式增长" in spec.content
        assert "-20.0% YoY" in spec.content

    def test_non_financial_control_no_hyf(self, tmp_path: Path):
        page, _ = _lint_spec(FIXTURES_BY_NAME["non_financial_control"], tmp_path)
        assert page.is_half_year_report is False
        assert not any(f.rule_id.startswith("HYF-") for f in page.findings)


# ── 5. 整库聚合 ─────────────────────────────────────────────────────


class TestFullFixtureAggregation:
    @pytest.fixture()
    def full_kb(self, tmp_path: Path) -> Path:
        return build_half_year_fixture_kb(tmp_path)

    def test_all_pages_scanned(self, full_kb: Path):
        result = lint_local_knowledge(str(full_kb))
        assert result.page_count == len(FIXTURE_SPECS)
        assert not result.errors

    def test_half_year_page_count(self, full_kb: Path):
        result = lint_local_knowledge(str(full_kb))
        expected_hy = sum(1 for s in FIXTURE_SPECS if s.is_half_year)
        assert len(result.pages_half_year) == expected_hy

    def test_period_missing_aggregation(self, full_kb: Path):
        result = lint_local_knowledge(str(full_kb))
        # 仅 missing_period 缺报告期。
        assert len(result.pages_half_year_period_missing) == 1

    def test_opinion_only_aggregation(self, full_kb: Path):
        result = lint_local_knowledge(str(full_kb))
        # 没有页面是 opinion_only（fact_opinion_mix 含事实类 source_type）。
        assert result.pages_half_year_opinion_only == []

    def test_stale_aggregation_includes_expired(self, full_kb: Path):
        result = lint_local_knowledge(str(full_kb))
        assert any("过期" in p or "expired" in p.lower() for p in result.pages_stale)

    def test_index_alignment_no_missing(self, full_kb: Path):
        # 所有 fixture 都写入 index，不应有 index_missing_pages。
        result = lint_local_knowledge(str(full_kb))
        assert result.index_missing_pages == []


# ── 6. fixture 自治 / 只读安全 ───────────────────────────────────────


class TestFixtureAutonomy:
    def test_does_not_touch_real_knowledge(self, tmp_path: Path, monkeypatch):
        # 真实知识库路径不应被创建/访问。
        real_root = Path.home() / "Documents" / "knowledge"
        snapshot_before = (
            {str(p): p.stat().st_size for p in real_root.rglob("*.md")}
            if real_root.exists()
            else {}
        )
        build_half_year_fixture_kb(tmp_path)
        lint_local_knowledge(str(tmp_path))
        snapshot_after = (
            {str(p): p.stat().st_size for p in real_root.rglob("*.md")}
            if real_root.exists()
            else {}
        )
        assert snapshot_before == snapshot_after

    def test_readonly_idempotent(self, tmp_path: Path):
        root = build_half_year_fixture_kb(tmp_path)
        sizes_before = {
            str(p): p.stat().st_size for p in root.rglob("*.md")
        }
        lint_local_knowledge(str(root))
        lint_local_knowledge(str(root))  # 跑两次
        sizes_after = {
            str(p): p.stat().st_size for p in root.rglob("*.md")
        }
        assert sizes_before == sizes_after

    def test_include_subset(self, tmp_path: Path):
        root = build_half_year_fixture_kb(
            tmp_path, include=["qualified", "non_financial_control"]
        )
        result = lint_local_knowledge(str(root))
        assert result.page_count == 2

    def test_include_unknown_name_raises(self, tmp_path: Path):
        with pytest.raises(KeyError):
            build_half_year_fixture_kb(tmp_path, include=["does_not_exist"])

    def test_include_all_individual(self, tmp_path: Path):
        # 每个样本都能独立写入并 lint（不依赖其它样本）。
        for spec in FIXTURE_SPECS:
            d = tmp_path / spec.name
            d.mkdir()
            root = build_half_year_fixture_kb(d, include=[spec.name])
            result = lint_local_knowledge(str(root))
            assert result.page_count == 1, spec.name
            assert not result.errors, spec.name

    def test_runs_without_home_env(self, tmp_path: Path, monkeypatch):
        # 即使 home 指向临时目录，也不应访问真实知识库。
        monkeypatch.setenv("HOME", str(tmp_path / "fake_home"))
        root = build_half_year_fixture_kb(tmp_path / "kb")
        result = lint_local_knowledge(str(root))
        assert result.page_count == len(FIXTURE_SPECS)


# ── 7. 可复用性元数据 ──────────────────────────────────────────────


class TestReusabilityMetadata:
    @pytest.mark.parametrize("spec", FIXTURE_SPECS, ids=[s.name for s in FIXTURE_SPECS])
    def test_each_spec_has_reuse_targets(self, spec: HalfYearFixtureSpec):
        assert spec.reuse_for, f"{spec.name} 未声明 reuse_for"

    def test_hy005_seed_present(self):
        # HY-005（事实反证）需要 weakened_old_opinion 作为数据种子。
        weakened = FIXTURES_BY_NAME["weakened_old_opinion"]
        assert "HY-005" in weakened.reuse_for

    def test_hy003_query_seed_present(self):
        # HY-003（事实表 provider）需要至少一个合格样本可按 symbol 查询。
        qualified = FIXTURES_BY_NAME["qualified"]
        assert "HY-003" in qualified.reuse_for
        assert qualified.expected_symbols

    def test_distinct_symbols_for_provider_query(self):
        # 为 HY-003 多 symbol 查询提供区分度。
        all_symbols = set()
        for spec in FIXTURE_SPECS:
            for sym in spec.expected_symbols:
                all_symbols.add(sym.split(" ")[0])  # 代码部分
        assert len(all_symbols) >= 5, (
            f"fixture symbol 区分度不足（应≥5）: {all_symbols}"
        )
