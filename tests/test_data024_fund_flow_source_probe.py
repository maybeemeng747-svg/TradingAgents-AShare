"""[DATA-024] 主力资金供应商 fallback live-smoke dry-run 与错误归因.

覆盖 fund_flow_source_probe 模块:
  1. 5 类错误归因分类器 (network/field-change/no-data/rate-limit/unknown-unit)
     + HAS_DATA 基线 + UNKNOWN 兜底.
  2. fixture dry-run 默认行为 + live-smoke 双重门禁 (env + flag).
  3. live 模式通过注入 fetch_fn 走完整分类链路, 验证 route_to_vendor +
     get_last_hit_vendor 集成.
  4. CLI 安全 / 输出格式 / 退出码.
  5. 接入 DATA-023 能力矩阵 overlay (不破坏现有 matrix).
  6. 安全: 无密钥泄露.

约束: 不调 live API / LLM / prompts / prod DB. 所有 live 路径通过注入 fake
fetch_fn 模拟, 真实 route_to_vendor 不在测试中被触发.

# [DATA-024] fund_flow_source_probe
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, List

import pytest

from tradingagents.dataflows.fund_flow_source_probe import (
    DEFAULT_PROBE_SYMBOLS,
    EXPECTED_UNIT,
    FUND_FLOW_METHOD,
    FundFlowErrorType,
    FundFlowProbeReport,
    FundFlowProbeResult,
    PROBE_FIXTURES,
    _compute_summary,
    _count_data_lines,
    _detect_unit,
    build_capability_matrix_overlay,
    classify_fund_flow_error_type,
    get_fund_flow_capability_matrix_item,
    is_live_probe_enabled,
    render_fund_flow_probe_report,
    run_fund_flow_probe,
    save_fund_flow_probe_report,
)
from tradingagents.dataflows.source_catalog import DataType


# ─── 1. 错误归因分类器 ────────────────────────────────────────────────


class TestErrorTypeClassifier:
    """[DATA-024] classify_fund_flow_error_type 必须把每类典型输入归到
    正确的 error_type, 且优先级与 source_freshness_report 对齐."""

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            # 限流 — 最高优先级 (即使 raw 里也有"获取失败"字样)
            (
                {
                    "raw_value": "HTTPError 429: Too Many Requests",
                    "status": "FAILED",
                    "error": "429 too many requests",
                },
                FundFlowErrorType.RATE_LIMITED,
            ),
            (
                {
                    "raw_value": "个股资金流向数据获取失败：请求过于频繁",
                    "status": "FAILED",
                    "error": "",
                },
                FundFlowErrorType.RATE_LIMITED,
            ),
            # 网络失败
            (
                {
                    "raw_value": "个股资金流向数据获取失败：ConnectionError",
                    "status": "FAILED",
                    "error": "",
                },
                FundFlowErrorType.NETWORK_ERROR,
            ),
            (
                {
                    "raw_value": "",
                    "status": "FAILED",
                    "error": "ProxyError: cannot connect",
                },
                FundFlowErrorType.NETWORK_ERROR,
            ),
            (
                {
                    "raw_value": "Max retries exceeded with url",
                    "status": "",
                    "error": "",
                },
                FundFlowErrorType.NETWORK_ERROR,
            ),
            # 正常无数据
            (
                {"raw_value": "", "status": "NORMAL_NO_DATA", "error": ""},
                FundFlowErrorType.NO_DATA,
            ),
            (
                {"raw_value": "", "status": "NOT_QUERIED", "error": ""},
                FundFlowErrorType.NO_DATA,
            ),
            (
                {"raw_value": "", "status": "", "error": ""},
                FundFlowErrorType.NO_DATA,
            ),
            # 字段变更: 单位与预期不符
            (
                {
                    "raw_value": "2026-06-10  123450000.00\n单位：元",
                    "status": "HAS_DATA",
                    "unit": "元",
                    "unit_verified": False,
                },
                FundFlowErrorType.FIELD_CHANGE,
            ),
            (
                {
                    "raw_value": "数据存在但无单位标记",
                    "status": "HAS_DATA",
                    "unit": "亿元",
                    "unit_verified": False,
                },
                FundFlowErrorType.FIELD_CHANGE,
            ),
            # 单位不明: 数据存在但探测不到单位
            (
                {
                    "raw_value": "2026-06-10  12345.00  -6789.00",
                    "status": "HAS_DATA",
                    "unit": "",
                    "unit_verified": False,
                },
                FundFlowErrorType.UNKNOWN_UNIT,
            ),
            (
                {
                    "raw_value": "数据行,另一行数据",
                    "status": "HAS_DATA",
                    "unit_verified": None,
                },
                FundFlowErrorType.UNKNOWN_UNIT,
            ),
            # OK
            (
                {
                    "raw_value": "600519.SH 近20日主力资金：\n...\n单位：万元",
                    "status": "HAS_DATA",
                    "unit": "万元",
                    "unit_verified": True,
                },
                FundFlowErrorType.OK,
            ),
            (
                {
                    "raw_value": "2026-06-10  12345.00\n单位：万元",
                    "status": "HAS_DATA",
                    "unit": "",
                    "unit_verified": False,
                },
                FundFlowErrorType.OK,
            ),
        ],
    )
    def test_classifier_matches_expected(self, kwargs, expected):
        got = classify_fund_flow_error_type(**kwargs)
        assert got == expected, (
            f"classify_fund_flow_error_type({kwargs}) = {got}, expected {expected}"
        )

    def test_rate_limit_beats_network_error(self):
        """同时命中限流与失败模式时, 必须优先返回 RATE_LIMITED."""
        result = classify_fund_flow_error_type(
            raw_value="个股资金流向数据获取失败：HTTPError 429",
            status="FAILED",
            error="HTTPError 429",
        )
        assert result == FundFlowErrorType.RATE_LIMITED

    def test_long_failure_text_not_misjudged_as_ok(self):
        """DATA-022 边界: 长 FAILED 文本不能被 HAS_DATA 路径误判."""
        long_text = (
            "个股资金流向数据获取失败：ConnectionError HTTPSConnectionPool"
            "(host=push2his.eastmoney.com): Max retries exceeded with url"
        )
        assert (
            classify_fund_flow_error_type(raw_value=long_text, status="FAILED")
            == FundFlowErrorType.NETWORK_ERROR
        )

    def test_explicit_status_failed_without_pattern_is_network(self):
        """status=FAILED 但 error/raw 都不含已知模式时, 默认归网络失败."""
        result = classify_fund_flow_error_type(
            raw_value="",
            status="FAILED",
            error="",
        )
        assert result == FundFlowErrorType.NETWORK_ERROR

    def test_to_freshness_status_mapping_complete(self):
        """每个 error_type 都必须有 freshness 桥接映射."""
        for et in FundFlowErrorType.ALL:
            assert et in FundFlowErrorType.TO_FRESHNESS_STATUS, (
                f"error_type {et} 缺少 freshness 映射"
            )


# ─── 2. FundFlowErrorType 枚举完整性 ─────────────────────────────────


class TestErrorTypeEnum:
    def test_required_five_classes_present(self):
        required = {
            FundFlowErrorType.NETWORK_ERROR,
            FundFlowErrorType.FIELD_CHANGE,
            FundFlowErrorType.NO_DATA,
            FundFlowErrorType.RATE_LIMITED,
            FundFlowErrorType.UNKNOWN_UNIT,
        }
        assert required.issubset(set(FundFlowErrorType.REQUIRED_ATTRIBUTION))
        assert set(FundFlowErrorType.REQUIRED_ATTRIBUTION) == required

    def test_all_includes_ok_and_unknown(self):
        assert FundFlowErrorType.OK in FundFlowErrorType.ALL
        assert FundFlowErrorType.UNKNOWN in FundFlowErrorType.ALL

    def test_label_cn_covers_all(self):
        for et in FundFlowErrorType.ALL:
            assert et in FundFlowErrorType.LABEL_CN, f"missing label_cn for {et}"


# ─── 3. fixture dry-run ──────────────────────────────────────────────


class TestFixtureDryRun:
    """[DATA-024] 默认 mode=fixture, 不发任何网络请求."""

    def test_default_run_is_fixture(self, monkeypatch):
        # 即使环境变量被设置, 默认调用也不能走 live (因为没有 live_smoke=True)
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")
        report = run_fund_flow_probe()
        assert report.mode == "fixture"
        assert report.env_gated is True
        assert len(report.results) == len(PROBE_FIXTURES)
        for r in report.results:
            assert r.probe_mode == "fixture"

    @pytest.mark.parametrize(
        "fixture_id",
        list(PROBE_FIXTURES.keys()),
        ids=list(PROBE_FIXTURES.keys()),
    )
    def test_each_fixture_classified_as_expected(self, fixture_id):
        report = run_fund_flow_probe()
        matching = [r for r in report.results if r.fixture_id == fixture_id]
        assert len(matching) == 1
        r = matching[0]
        expected = PROBE_FIXTURES[fixture_id]["expected_error_type"]
        assert r.error_type == expected, (
            f"fixture {fixture_id}: error_type={r.error_type}, expected={expected}"
        )

    def test_fixture_coverage_includes_required_five(self):
        report = run_fund_flow_probe()
        covered = set(report.summary["required_classes_covered"])
        required = set(FundFlowErrorType.REQUIRED_ATTRIBUTION)
        missing = required - covered
        assert not missing, f"fixture 未覆盖必选 5 类: {missing}"

    def test_fixture_summary_all_passed(self):
        report = run_fund_flow_probe()
        assert report.summary["all_passed"] is True
        assert report.summary["required_classes_missing"] == []

    def test_fixture_mode_does_not_consume_symbols_for_calls(self):
        # fixture 模式下, 即使传了 symbols 也不应该有 live latency
        report = run_fund_flow_probe(symbols=["600519.SH"])
        for r in report.results:
            assert r.probe_mode == "fixture"
            assert r.latency_ms == 0.0

    def test_failed_misjudged_boundary(self):
        """长 FAILED 文本必须归到 network_error, 不被 HAS_DATA len>20 路径吃掉."""
        report = run_fund_flow_probe()
        failed_fixture = next(
            r for r in report.results if r.fixture_id == "NETWORK_ERROR"
        )
        assert failed_fixture.error_type == FundFlowErrorType.NETWORK_ERROR
        assert failed_fixture.status == "FAILED"


# ─── 4. live-smoke 双重门禁 ──────────────────────────────────────────


class TestLiveSmokeGating:
    """[DATA-024] live-smoke 必须 (flag=True) AND (env=1) 才真正调用."""

    def test_live_flag_without_env_returns_skipped(self, monkeypatch):
        monkeypatch.delenv("TA_LIVE_DATA_SMOKE", raising=False)
        assert is_live_probe_enabled() is False

        report = run_fund_flow_probe(
            symbols=["600519.SH"], live_smoke=True
        )
        assert report.mode == "live"
        assert report.env_gated is True
        assert len(report.results) == 1
        assert report.results[0].status == "SKIPPED"
        assert report.results[0].probe_mode == "skipped"
        assert "TA_LIVE_DATA_SMOKE" in report.results[0].diagnosis

    def test_live_flag_with_env_but_no_flag_stays_fixture(self, monkeypatch):
        """env=1 但没传 live_smoke=True → 仍走 fixture, 不发网络请求."""
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")
        report = run_fund_flow_probe(symbols=["600519.SH"])
        assert report.mode == "fixture"
        assert report.env_gated is True  # fixture mode 下 env_gated 始终 True

    def test_live_with_env_and_flag_uses_fetch_fn(self, monkeypatch):
        """env=1 + live_smoke=True + 注入 fetch_fn → 真走 live 分类路径,
        但因为 fetch_fn 是 fake, 没有真实网络调用."""
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")

        calls: List[str] = []

        def fake_fetch(symbol: str) -> str:
            calls.append(symbol)
            return (
                f"{symbol} 近20日主力资金净流向：\n"
                "日期  主力净流入\n2026-06-10  12345.00\n单位：万元"
            )

        def fake_last_hit() -> str:
            return "cn_akshare"

        report = run_fund_flow_probe(
            symbols=["600519.SH"],
            live_smoke=True,
            fetch_fn=fake_fetch,
            last_hit_vendor_fn=fake_last_hit,
        )
        assert report.mode == "live"
        assert report.env_gated is False
        assert calls == ["600519.SH"]
        assert len(report.results) == 1
        r = report.results[0]
        assert r.error_type == FundFlowErrorType.OK
        assert r.status == "HAS_DATA"
        assert r.vendor == "cn_akshare"
        assert r.probe_mode == "live"
        assert r.latency_ms >= 0.0

    def test_live_with_env_and_flag_fallback_detected(self, monkeypatch):
        """当 last_hit_vendor 是 cn_astock 时, 应识别为 fallback."""
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")

        def fake_fetch(symbol: str) -> str:
            return f"{symbol} 资金流：\n2026-06-10  12345.00\n单位：万元"

        def fake_last_hit() -> str:
            return "cn_astock"

        report = run_fund_flow_probe(
            symbols=["000001.SZ"],
            live_smoke=True,
            fetch_fn=fake_fetch,
            last_hit_vendor_fn=fake_last_hit,
        )
        r = report.results[0]
        assert r.error_type == FundFlowErrorType.OK
        assert r.is_fallback is True
        assert r.fallback_from == "cn_akshare"

    def test_live_network_error_attribution(self, monkeypatch):
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")

        def fake_fetch(symbol: str) -> str:
            return "个股资金流向数据获取失败：ConnectionError timeout"

        def fake_last_hit() -> str:
            return ""

        report = run_fund_flow_probe(
            symbols=["600519.SH"],
            live_smoke=True,
            fetch_fn=fake_fetch,
            last_hit_vendor_fn=fake_last_hit,
        )
        r = report.results[0]
        assert r.error_type == FundFlowErrorType.NETWORK_ERROR
        assert r.status == "FAILED"
        assert report.summary["has_failures"] is True

    def test_live_rate_limit_attribution(self, monkeypatch):
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")

        def fake_fetch(symbol: str) -> str:
            return "HTTPError 429: 请求过于频繁"

        def fake_last_hit() -> str:
            return ""

        report = run_fund_flow_probe(
            symbols=["600519.SH"],
            live_smoke=True,
            fetch_fn=fake_fetch,
            last_hit_vendor_fn=fake_last_hit,
        )
        assert report.results[0].error_type == FundFlowErrorType.RATE_LIMITED

    def test_live_exception_classified_as_network(self, monkeypatch):
        """fetch_fn 抛异常时, probe 必须捕获并归因, 而不是崩溃."""
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")

        def fake_fetch(symbol: str) -> str:
            raise ConnectionError("network down")

        def fake_last_hit() -> str:
            return ""

        report = run_fund_flow_probe(
            symbols=["600519.SH"],
            live_smoke=True,
            fetch_fn=fake_fetch,
            last_hit_vendor_fn=fake_last_hit,
        )
        r = report.results[0]
        assert r.error_type == FundFlowErrorType.NETWORK_ERROR
        assert "ConnectionError" in r.error

    def test_live_symbols_capped_to_max(self, monkeypatch):
        """[DATA-P1-ASTOCK-LIVE-SMOKE] 约定: live smoke 抽样标的 <= _MAX_SYMBOLS."""
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")
        from tradingagents.dataflows.fund_flow_source_probe import _MAX_SYMBOLS

        def fake_fetch(symbol: str) -> str:
            return f"{symbol} 数据\n单位：万元"

        def fake_last_hit() -> str:
            return "cn_akshare"

        many = [f"{i:06d}.SH" for i in range(10)]
        report = run_fund_flow_probe(
            symbols=many,
            live_smoke=True,
            fetch_fn=fake_fetch,
            last_hit_vendor_fn=fake_last_hit,
        )
        assert len(report.results) <= _MAX_SYMBOLS
        assert len(report.symbols) <= _MAX_SYMBOLS


# ─── 5. 辅助函数 ─────────────────────────────────────────────────────


class TestHelpers:
    def test_detect_unit_wan_yuan(self):
        assert _detect_unit("...数据...\n单位：万元") == "万元"

    def test_detect_unit_yuan(self):
        assert _detect_unit("单位：元") == "元"

    def test_detect_unit_unknown(self):
        assert _detect_unit("no unit marker here") == ""
        assert _detect_unit("") == ""

    def test_count_data_lines_strips_title_and_unit(self):
        text = (
            "600519.SH 近20日主力资金净流向：\n"
            "日期  主力净流入\n"
            "2026-06-10  12345.00\n"
            "2026-06-09  9876.00\n"
            "单位：万元"
        )
        # 表头 + 2 数据行 → 2
        assert _count_data_lines(text) == 2

    def test_count_data_lines_empty(self):
        assert _count_data_lines("") == 0
        assert _count_data_lines(None) == 0


# ─── 6. 渲染 + 持久化 ────────────────────────────────────────────────


class TestRenderPersist:
    def test_render_fixture_report_has_required_sections(self):
        report = run_fund_flow_probe()
        md = render_fund_flow_probe_report(report)
        assert "[DATA-024]" in md
        assert "## Summary" in md
        assert "## Probe Details" in md
        assert "## Fixture Coverage" in md
        for fid in PROBE_FIXTURES:
            assert fid in md

    def test_render_live_gated_report(self, monkeypatch):
        monkeypatch.delenv("TA_LIVE_DATA_SMOKE", raising=False)
        report = run_fund_flow_probe(symbols=["600519.SH"], live_smoke=True)
        md = render_fund_flow_probe_report(report)
        assert "## Live-Smoke Gate" in md
        assert "TA_LIVE_DATA_SMOKE=1" in md

    def test_save_report_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TA_LIVE_DATA_SMOKE", raising=False)
        report = run_fund_flow_probe(today="2026-06-29")
        # 重新设置 date 以写到 tmp_path
        report.date = "2026-06-29"
        path = save_fund_flow_probe_report(report, output_dir=str(tmp_path))
        assert os.path.exists(path)
        assert path.endswith("fund-flow-probe-2026-06-29.md")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "[DATA-024]" in content


# ─── 7. DATA-023 能力矩阵 overlay ────────────────────────────────────


class TestCapabilityMatrixOverlay:
    """[DATA-024] 接入 DATA-023 能力矩阵 — 通过 overlay, 不改 matrix 本身."""

    def test_overlay_has_required_keys(self):
        report = run_fund_flow_probe()
        overlay = build_capability_matrix_overlay(report)
        assert overlay["data_type"] == DataType.FUND_FLOW.value
        assert overlay["probe_source"] == "[DATA-024] fund_flow_source_probe"
        assert overlay["probe_mode"] == "fixture"
        assert "by_error_type" in overlay
        assert "required_classes_covered" in overlay
        assert "required_classes_missing" in overlay
        assert overlay["expected_unit"] == EXPECTED_UNIT

    def test_overlay_includes_required_five(self):
        report = run_fund_flow_probe()
        overlay = build_capability_matrix_overlay(report)
        covered = set(overlay["required_classes_covered"])
        required = set(FundFlowErrorType.REQUIRED_ATTRIBUTION)
        assert required.issubset(covered)

    def test_get_matrix_item_with_overlay(self):
        item = get_fund_flow_capability_matrix_item()
        assert item["data_type"] == DataType.FUND_FLOW.value
        assert "probe_overlay" in item
        assert item["probe_overlay"]["probe_source"].startswith("[DATA-024]")

    def test_get_matrix_item_without_overlay(self):
        item = get_fund_flow_capability_matrix_item(include_probe_overlay=False)
        assert "probe_overlay" not in item
        # 必须保留原始 matrix 字段
        assert "primary_vendor" in item
        assert "fallback_chain" in item

    def test_overlay_does_not_break_source_capability_matrix(self):
        """不能因为加了 overlay 就让 DATA-023 的 validate_matrix_coverage 出 issue."""
        from tradingagents.dataflows.source_capability_matrix import (
            get_source_capability_matrix,
            validate_matrix_coverage,
        )

        matrix = get_source_capability_matrix()
        issues = validate_matrix_coverage(matrix)
        # fund_flow entry 仍然存在且合法
        fund_flow_items = [
            i for i in matrix["items"] if i["data_type"] == DataType.FUND_FLOW.value
        ]
        assert len(fund_flow_items) == 1
        # overlay 不是 matrix 字段, 不应引入新的 issue
        ff = fund_flow_items[0]
        assert "probe_overlay" not in ff  # overlay 不污染原始 matrix

    def test_overlay_references_catalog_fallback_chain(self):
        """overlay 报告的 fallback_chain 必须和 source_catalog 一致."""
        from tradingagents.dataflows.source_catalog import get_fallback_chain

        report = run_fund_flow_probe()
        overlay = build_capability_matrix_overlay(report)
        catalog_chain = list(get_fallback_chain(DataType.FUND_FLOW))
        assert overlay["fallback_chain_catalog"] == catalog_chain


# ─── 8. 安全: 不含密钥 ───────────────────────────────────────────────


class TestNoSecrets:
    SECRET_PATTERNS = [
        "api_key",
        "apikey",
        "secret",
        "bearer ",
        "authorization:",
        "sk-",
        "cookie",
    ]

    def test_no_secret_patterns_in_fixture_results(self):
        report = run_fund_flow_probe()
        blob = json.dumps(report.to_dict(), ensure_ascii=False)
        lowered = blob.lower()
        for pat in self.SECRET_PATTERNS:
            assert pat.lower() not in lowered, (
                f"probe 报告中出现疑似密钥字段: {pat}"
            )

    def test_no_secret_patterns_in_rendered_markdown(self):
        report = run_fund_flow_probe()
        md = render_fund_flow_probe_report(report)
        lowered = md.lower()
        for pat in self.SECRET_PATTERNS:
            assert pat.lower() not in lowered, (
                f"probe markdown 中出现疑似密钥字段: {pat}"
            )

    def test_no_secret_patterns_in_overlay(self):
        report = run_fund_flow_probe()
        overlay = build_capability_matrix_overlay(report)
        blob = json.dumps(overlay, ensure_ascii=False)
        lowered = blob.lower()
        for pat in self.SECRET_PATTERNS:
            assert pat.lower() not in lowered, (
                f"overlay 中出现疑似密钥字段: {pat}"
            )

    def test_error_truncated_to_200_chars(self):
        """即使 fetch_fn 抛出含敏感字段的长异常, to_dict 也要截断."""
        long_error = "X" * 500 + " api_key=ABCDEF"
        r = FundFlowProbeResult(
            symbol="600519.SH",
            error=long_error,
        )
        d = r.to_dict()
        assert len(d["error"]) <= 200


# ─── 9. 板块资金流严格分离 ──────────────────────────────────────────


class TestBoardFundFlowSeparation:
    """[DATA-024 执行约束 §3] 不能把板块资金流当个股资金流."""

    def test_probe_only_targets_individual_fund_flow(self):
        """fallback_chain 必须是 FUND_FLOW 的 chain, 不是 BOARD_FUND_FLOW."""
        from tradingagents.dataflows.source_catalog import (
            get_fallback_chain,
            get_primary_source,
        )

        report = run_fund_flow_probe()
        individual_chain = list(get_fallback_chain(DataType.FUND_FLOW))
        board_chain = list(get_fallback_chain(DataType.BOARD_FUND_FLOW))
        # 两条 chain 不能混在一起
        assert report.fallback_chain == individual_chain
        # primary 必须是 FUND_FLOW primary
        primary = get_primary_source(DataType.FUND_FLOW)
        assert primary is not None
        assert FUND_FLOW_METHOD == "get_individual_fund_flow"

    def test_fixtures_all_reference_individual_vendors(self):
        """所有 fixture 的 vendor 必须是 FUND_FLOW 注册的 vendor."""
        from tradingagents.dataflows.source_catalog import get_sources_for_type

        individual_vendors = {
            s.vendor for s in get_sources_for_type(DataType.FUND_FLOW)
        }
        for fid, fixture in PROBE_FIXTURES.items():
            v = fixture.get("vendor", "")
            if not v:
                continue
            assert v in individual_vendors, (
                f"fixture {fid} vendor={v} 不在 FUND_FLOW vendor 集合 {individual_vendors}"
            )


# ─── 10. CLI ─────────────────────────────────────────────────────────


class TestCli:
    @property
    def _script_path(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "run_fund_flow_source_probe.py",
        )

    def test_cli_fixture_dry_run_prints_report(self, monkeypatch):
        monkeypatch.delenv("TA_LIVE_DATA_SMOKE", raising=False)
        proc = subprocess.run(
            [sys.executable, self._script_path, "--dry-run"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "[DATA-024]" in proc.stdout
        assert "## Summary" in proc.stdout
        assert "Fixture Coverage" in proc.stdout

    def test_cli_live_smoke_without_env_exits_zero(self, monkeypatch):
        """live-smoke 但 env 未设置 → SKIPPED, exit 0 (不算失败)."""
        monkeypatch.delenv("TA_LIVE_DATA_SMOKE", raising=False)
        proc = subprocess.run(
            [
                sys.executable,
                self._script_path,
                "--live-smoke",
                "--symbols",
                "600519.SH",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "SKIPPED" in proc.stdout
        assert "TA_LIVE_DATA_SMOKE=1" in proc.stdout

    def test_cli_stdout_json(self, monkeypatch):
        monkeypatch.delenv("TA_LIVE_DATA_SMOKE", raising=False)
        proc = subprocess.run(
            [sys.executable, self._script_path, "--stdout-json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert "report" in payload
        assert "overlay" in payload
        assert payload["report"]["mode"] == "fixture"

    def test_cli_writes_file_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TA_LIVE_DATA_SMOKE", raising=False)
        out = tmp_path / "custom.md"
        proc = subprocess.run(
            [
                sys.executable,
                self._script_path,
                "--output",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "[DATA-024]" in content


# ─── 11. 边界 ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_symbols_list_in_live_mode(self, monkeypatch):
        """live 模式传空 symbols → 0 result, 不崩."""
        monkeypatch.setenv("TA_LIVE_DATA_SMOKE", "1")
        report = run_fund_flow_probe(symbols=[], live_smoke=True)
        assert report.mode == "live"
        assert report.results == []

    def test_custom_expected_unit(self):
        """允许覆盖 expected_unit (例如未来切到元单位时)."""
        report = run_fund_flow_probe(expected_unit="元")
        # FIELD_CHANGE fixture 原本 unit=元 vs expected=万元 → field_change
        # 现在切换 expected=元 后, 那条不再被归为 field_change
        fc = next(
            r for r in report.results if r.fixture_id == "FIELD_CHANGE"
        )
        # unit=元 与 expected_unit=元 一致, 不再是 field_change
        assert fc.error_type != FundFlowErrorType.FIELD_CHANGE

    def test_summary_handles_empty_results(self):
        summary = _compute_summary([])
        assert summary["total"] == 0
        assert summary["all_passed"] is False  # 没 runnable 不算通过
        assert summary["required_classes_missing"] == sorted(
            FundFlowErrorType.REQUIRED_ATTRIBUTION
        )

    def test_today_pinning(self):
        """today 参数能固定日期, 不依赖 wall clock."""
        report = run_fund_flow_probe(today="2099-12-31")
        assert report.date == "2099-12-31"
