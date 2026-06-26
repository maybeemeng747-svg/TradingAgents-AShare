# [DATA-023] source_capability_matrix

import json
from pathlib import Path

import pytest

from tradingagents.dataflows.source_catalog import (
    DataType,
    Freshness,
    RateLimitRisk,
    get_sources_for_type,
)
from tradingagents.dataflows.source_capability_matrix import (
    DATA_TYPE_LABEL_CN,
    FRESHNESS_STATUS_SEMANTICS,
    RATE_LIMIT_RISK_LABEL,
    SOURCE_CAPABILITY_MATRIX_VERSION,
    get_matrix_item,
    get_source_capability_matrix,
    render_source_capability_matrix_markdown,
    render_source_capability_matrix_text,
    validate_matrix_coverage,
)


# ── 基础导出 ──────────────────────────────────────────────────────────
class TestMatrixShape:
    def test_has_version(self):
        matrix = get_source_capability_matrix()
        assert matrix["version"] == SOURCE_CAPABILITY_MATRIX_VERSION

    def test_items_is_list(self):
        matrix = get_source_capability_matrix()
        assert isinstance(matrix["items"], list)
        assert len(matrix["items"]) > 0

    def test_legends_present(self):
        matrix = get_source_capability_matrix()
        assert "freshness_legend" in matrix
        assert "rate_limit_legend" in matrix
        # legend 覆盖所有 enum 值
        assert set(matrix["freshness_legend"].keys()) == {
            f.value for f in Freshness
        }
        assert set(matrix["rate_limit_legend"].keys()) == {
            r.value for r in RateLimitRisk
        }

    def test_returns_deep_copy(self):
        m1 = get_source_capability_matrix()
        original_vendor = m1["items"][0]["primary_vendor"]
        m1["items"][0]["primary_vendor"] = "MUTATED"
        m2 = get_source_capability_matrix()
        assert m2["items"][0]["primary_vendor"] == original_vendor


# ── entry 字段完整性 ──────────────────────────────────────────────────
class TestEntryFields:
    REQUIRED_FIELDS = (
        "data_type",
        "primary_vendor",
        "primary_endpoint",
        "fallback_chain",
        "freshness",
        "unit",
        "known_limits",
        "status_semantics",
    )

    def test_every_entry_has_required_fields(self):
        matrix = get_source_capability_matrix()
        for item in matrix["items"]:
            for name in self.REQUIRED_FIELDS:
                assert name in item, (
                    f"data_type={item.get('data_type')} missing field {name}"
                )

    def test_freshness_in_legal_enum(self):
        legal = {f.value for f in Freshness}
        matrix = get_source_capability_matrix()
        for item in matrix["items"]:
            assert item["freshness"] in legal, (
                f"data_type={item['data_type']} freshness={item['freshness']}"
            )

    def test_rate_limit_risk_in_legal_enum(self):
        legal = {r.value for r in RateLimitRisk}
        matrix = get_source_capability_matrix()
        for item in matrix["items"]:
            assert item["rate_limit_risk"] in legal, (
                f"data_type={item['data_type']} rate_limit_risk={item['rate_limit_risk']}"
            )

    def test_fallback_chain_is_list(self):
        matrix = get_source_capability_matrix()
        for item in matrix["items"]:
            assert isinstance(item["fallback_chain"], list)

    def test_fallback_chain_starts_with_primary(self):
        matrix = get_source_capability_matrix()
        for item in matrix["items"]:
            assert item["fallback_chain"], item
            assert item["fallback_chain"][0] == item["primary_vendor"], (
                f"data_type={item['data_type']} fallback_chain does not start "
                f"with primary_vendor"
            )

    def test_source_count_matches_catalog(self):
        matrix = get_source_capability_matrix()
        for item in matrix["items"]:
            sources = get_sources_for_type(item["data_type"])
            assert item["source_count"] == len(sources), (
                f"data_type={item['data_type']} source_count mismatch: "
                f"matrix={item['source_count']} catalog={len(sources)}"
            )


# ── 覆盖率验收（核心：新增 data_type 不会漏进矩阵）────────────────────
class TestCoverage:
    """验收要点：新增 DataType 不会漏进矩阵。"""

    def test_all_registered_data_types_in_matrix(self):
        matrix = get_source_capability_matrix()
        present = {item["data_type"] for item in matrix["items"]}
        registered = {
            dt.value for dt in DataType if get_sources_for_type(dt)
        }
        missing = registered - present
        assert not missing, (
            f"以下已注册的 data_type 漏进 matrix: {sorted(missing)}"
        )

    def test_no_unregistered_data_type_in_matrix(self):
        matrix = get_source_capability_matrix()
        legal = {dt.value for dt in DataType}
        present = {item["data_type"] for item in matrix["items"]}
        extra = present - legal
        assert not extra, f"matrix 出现未注册的 data_type: {sorted(extra)}"

    def test_validate_matrix_coverage_returns_no_issues(self):
        matrix = get_source_capability_matrix()
        issues = validate_matrix_coverage(matrix)
        assert issues == [], f"matrix coverage issues: {issues}"

    def test_validate_catches_missing_data_type(self):
        """模拟新增 DataType 后 matrix 未同步的情况。"""
        matrix = get_source_capability_matrix()
        # 移除一条 entry，模拟"漏掉"
        tampered = {
            "version": matrix["version"],
            "items": matrix["items"][:-1],
            "freshness_legend": matrix["freshness_legend"],
            "rate_limit_legend": matrix["rate_limit_legend"],
        }
        issues = validate_matrix_coverage(tampered)
        assert issues, "validate_matrix_coverage 应该检测出漏掉的 data_type"
        assert any("未出现在 matrix" in i for i in issues)

    def test_validate_catches_empty_required_field(self):
        matrix = get_source_capability_matrix()
        first = dict(matrix["items"][0])
        # 强行清空 unit（对定量类型应当报错）
        if first["data_type"] in (
            "ohlcv", "fund_flow", "board_fund_flow",
            "realtime_quotes", "quote",
        ):
            first["unit"] = ""
        else:
            first["status_semantics"] = ""
        tampered = {
            "version": matrix["version"],
            "items": [first, *matrix["items"][1:]],
            "freshness_legend": matrix["freshness_legend"],
            "rate_limit_legend": matrix["rate_limit_legend"],
        }
        issues = validate_matrix_coverage(tampered)
        assert issues, "validate 应当报告被清空的字段"


# ── 业务覆盖验收（任务要求：资金/龙虎榜/公告/评级/回购/研报）──────────
class TestBusinessCoverage:
    REQUIRED = [
        "fund_flow",
        "board_fund_flow",
        "lhb",
        "notice",
        "rating",
        "buyback",
        "report",
    ]

    def test_required_business_types_covered(self):
        matrix = get_source_capability_matrix()
        present = {item["data_type"] for item in matrix["items"]}
        for dt in self.REQUIRED:
            assert dt in present, f"业务必需 data_type 缺失: {dt}"

    def test_required_business_types_have_primary(self):
        for dt in self.REQUIRED:
            item = get_matrix_item(dt)
            assert item is not None, f"{dt} 不在 matrix"
            assert item["primary_vendor"], f"{dt} 缺 primary_vendor"
            assert item["primary_endpoint"], f"{dt} 缺 primary_endpoint"
            assert item["freshness"], f"{dt} 缺 freshness"
            assert item["known_limits"], f"{dt} 缺 known_limits"
            assert item["status_semantics"], f"{dt} 缺 status_semantics"

    def test_fund_flow_unit_is_wan_yuan(self):
        item = get_matrix_item("fund_flow")
        assert item is not None
        assert "万元" in item["unit"]

    def test_lhb_force_requirement_in_semantics_or_limits(self):
        item = get_matrix_item("lhb")
        assert item is not None
        combined = item["known_limits"] + " " + item["status_semantics"]
        assert "force" in combined or "NOT_QUERIED" in combined, (
            "LHB 的 force=True 要求应当反映在 known_limits 或 status_semantics"
        )

    def test_buyback_present(self):
        item = get_matrix_item("buyback")
        assert item is not None
        assert item["primary_vendor"]

    def test_report_and_rating_distinct(self):
        rep = get_matrix_item("report")
        rat = get_matrix_item("rating")
        assert rep and rat
        assert rep["data_type"] != rat["data_type"]


# ── 安全：不含密钥 ────────────────────────────────────────────────────
class TestNoSecrets:
    SECRET_PATTERNS = [
        "api_key",
        "apikey",
        "secret",
        "token",
        "Bearer ",
        "sk-",
    ]

    def test_no_secret_patterns_in_matrix(self):
        matrix = get_source_capability_matrix()
        blob = json.dumps(matrix, ensure_ascii=False)
        lowered = blob.lower()
        for pat in self.SECRET_PATTERNS:
            assert pat.lower() not in lowered, (
                f"matrix 中出现疑似密钥字段: {pat}"
            )

    def test_no_secret_patterns_in_markdown(self):
        md = render_source_capability_matrix_markdown()
        lowered = md.lower()
        for pat in self.SECRET_PATTERNS:
            assert pat.lower() not in lowered, (
                f"matrix markdown 中出现疑似密钥字段: {pat}"
            )

    def test_no_secret_patterns_in_text(self):
        text = render_source_capability_matrix_text()
        lowered = text.lower()
        for pat in self.SECRET_PATTERNS:
            assert pat.lower() not in lowered, (
                f"matrix text 中出现疑似密钥字段: {pat}"
            )


# ── 渲染 ─────────────────────────────────────────────────────────────
class TestRender:
    def test_markdown_has_overview_table(self):
        md = render_source_capability_matrix_markdown()
        assert "# A 股数据源能力矩阵" in md
        assert "## 矩阵总览" in md
        assert "## 状态语义详情" in md
        assert "## Freshness 语义图例" in md
        assert "## 限流风险图例" in md

    def test_markdown_includes_every_data_type(self):
        matrix = get_source_capability_matrix()
        md = render_source_capability_matrix_markdown(matrix)
        for item in matrix["items"]:
            assert f"`{item['data_type']}`" in md

    def test_text_render_compact(self):
        matrix = get_source_capability_matrix()
        text = render_source_capability_matrix_text(matrix)
        assert "[Source Capability Matrix]" in text
        assert f"version: {matrix['version']}" in text
        # 每条 entry 一行
        lines = [
            ln for ln in text.splitlines() if ln.startswith("- ")
        ]
        assert len(lines) == len(matrix["items"])


# ── 一致性：matrix ↔ source_catalog ──────────────────────────────────
class TestCatalogConsistency:
    def test_primary_matches_catalog_primary(self):
        from tradingagents.dataflows.source_catalog import get_primary_source

        matrix = get_source_capability_matrix()
        for item in matrix["items"]:
            catalog_primary = get_primary_source(item["data_type"])
            if catalog_primary is None:
                continue
            assert item["primary_vendor"] == catalog_primary.vendor, (
                f"data_type={item['data_type']} primary vendor 不一致: "
                f"matrix={item['primary_vendor']} "
                f"catalog={catalog_primary.vendor}"
            )

    def test_freshness_matches_catalog_primary(self):
        from tradingagents.dataflows.source_catalog import get_primary_source

        matrix = get_source_capability_matrix()
        for item in matrix["items"]:
            catalog_primary = get_primary_source(item["data_type"])
            if catalog_primary is None:
                continue
            assert item["freshness"] == catalog_primary.freshness.value


# ── 脚本：CLI 生成的 MD 与 JSON 文件 ──────────────────────────────────
class TestScriptCli:
    """验证 scripts/export_source_capability_matrix.py 能稳定生成产物。"""

    def test_script_validate_returns_zero(self):
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "scripts/export_source_capability_matrix.py",
             "--validate", "--no-md"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"validate 失败: {result.stderr or result.stdout}"
        )
        assert "OK" in result.stdout

    def test_script_stdout_json_is_valid_json(self):
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "scripts/export_source_capability_matrix.py",
             "--stdout-json", "--no-md"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["version"] == SOURCE_CAPABILITY_MATRIX_VERSION
        assert isinstance(payload["items"], list)
        assert len(payload["items"]) > 0
