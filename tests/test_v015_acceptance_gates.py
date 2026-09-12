# [V-015-fix2] 验收脚本门禁自动回归
"""覆盖 run_v015_operations_acceptance 的链路可用性门禁本身：

  - 空库（结构门禁）
  - 部分模块结构化失败（共识命中、其余 data_status=failed）
  - 全部模块失败（含共识）
  - 健康样本（共识命中、无 failed）

审核指出：真实 API 会把部分异常转换成结构化失败 bucket（而非抛异常），
门禁若只查共识命中 / except / 禁用字段，`failed` 模块会漏判为 PASS。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from api.services import research_evidence_service
from scripts.run_v015_operations_acceptance import (
    evaluate_evidence_gates,
    run_real_smoke,
)
from tests.half_year_fixtures import build_half_year_fixture_kb


def _bucket(name: str, data_status: str, *, has_hit: bool = False,
            errors: list[str] | None = None) -> dict:
    """与 research_evidence_service._failed_bucket / 正常 bucket 同形的探测条目。"""
    return {
        "has_hit": has_hit,
        "data_status": data_status,
        "errors": list(errors or []),
    }


def _probe(buckets: dict[str, dict], *, error: str | None = None,
           forbidden: list[str] | None = None) -> dict:
    if error is not None:
        return {"error": error}
    return {
        "data_status": "fresh",
        "buckets": buckets,
        "forbidden_action_keys": list(forbidden or []),
    }


def _healthy_buckets() -> dict:
    return {
        "consensus": _bucket("consensus", "fresh", has_hit=True),
        "citation_audit": _bucket("citation_audit", "missing"),
        "thesis_timeline": _bucket("thesis_timeline", "missing"),
        "half_year_facts": _bucket("half_year_facts", "missing"),
        "research_score_snapshot": _bucket("research_score_snapshot", "missing"),
    }


def _failed_bucket_probe(name: str, detail: str = "RuntimeError: boom") -> dict:
    return _bucket(name, "failed", errors=[f"{name}: {detail}"])


# ── 评估器（纯函数）─────────────────────────────────────────────────────────


class TestEvaluateEvidenceGates:
    def test_healthy_sample_passes(self):
        # 健康样本：共识命中 + 其余正常 missing（无数据 ≠ 查询失败）
        gates = evaluate_evidence_gates({"300750.SZ": _probe(_healthy_buckets())})
        assert gates == []

    def test_missing_is_not_failure(self):
        # 正常 missing 与查询失败分开处理：missing 不阻断
        buckets = _healthy_buckets()
        buckets["half_year_facts"] = _bucket("half_year_facts", "missing")
        assert evaluate_evidence_gates({"000977.SZ": _probe(buckets)}) == []

    def test_partial_module_failure_flagged(self):
        # 审核 fault-injection 场景：共识正常命中，其余四模块全部 failed
        buckets = _healthy_buckets()
        for name in ("citation_audit", "thesis_timeline",
                     "half_year_facts", "research_score_snapshot"):
            buckets[name] = _failed_bucket_probe(name)
        gates = evaluate_evidence_gates({"300750.SZ": _probe(buckets)})
        # 每个失败模块都必须单独点名
        for name in ("citation_audit", "thesis_timeline",
                     "half_year_facts", "research_score_snapshot"):
            assert any(
                f"模块 {name} 返回 failed" in g for g in gates
            ), f"{name} 的结构化失败未被门禁捕获"
        # 共识命中：不触发"全部无共识"门禁
        assert not any("无共识命中" in g for g in gates)

    def test_all_modules_failed_flagged(self):
        buckets = {
            name: _failed_bucket_probe(name)
            for name in (
                "consensus", "citation_audit", "thesis_timeline",
                "half_year_facts", "research_score_snapshot",
            )
        }
        gates = evaluate_evidence_gates({"300750.SZ": _probe(buckets)})
        assert sum(1 for g in gates if "返回 failed" in g) == 5
        assert any("无共识命中" in g for g in gates)

    def test_aggregate_exception_flagged(self):
        gates = evaluate_evidence_gates(
            {"300750.SZ": _probe({}, error="InvalidSymbolError: bad symbol")}
        )
        assert any("聚合异常" in g for g in gates)
        assert any("无共识命中" in g for g in gates)

    def test_forbidden_keys_flagged(self):
        gates = evaluate_evidence_gates({
            "300750.SZ": _probe(_healthy_buckets(), forbidden=["$.decision"]),
        })
        assert any("契约违例" in g for g in gates)


# ── 脚本集成（真实 build_research_evidence + 注入模块失败）──────────────────


def _patch_builder(monkeypatch, name: str, data_status: str,
                   *, has_hit: bool = False) -> None:
    """把 service 内的 bucket 构造器替换为固定状态（模拟结构化失败/健康）。"""
    bucket = {
        "bucket": name,
        "status": data_status,
        "task": name,
        "has_hit": has_hit,
        "data_status": data_status,
        "errors": [f"{name}: 注入的{data_status}状态"] if data_status == "failed" else [],
        "summary": {},
    }
    monkeypatch.setattr(
        research_evidence_service,
        f"_build_{name}_bucket",
        lambda **kwargs: dict(bucket),
    )


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    return build_half_year_fixture_kb(tmp_path, include=["qualified"])


class TestScriptGateIntegration:
    def test_empty_directory_structural_gate(self, tmp_path: Path):
        empty = tmp_path / "empty-kb"
        empty.mkdir()
        result = run_real_smoke(empty, probe_symbols=("000977",))
        gates = result["usability_gates_failed"]
        assert any("必需目录缺失" in g for g in gates)
        assert any("无共识命中" in g for g in gates)

    def test_partial_structured_failure_blocks_pass(self, fixture_kb, monkeypatch):
        # 审核注入场景：共识命中，其余四模块结构化 failed → 门禁必须失败
        _patch_builder(monkeypatch, "consensus", "fresh", has_hit=True)
        for name in ("citation_audit", "thesis_timeline",
                     "half_year_facts", "research_score_snapshot"):
            _patch_builder(monkeypatch, name, "failed")
        result = run_real_smoke(fixture_kb, probe_symbols=("000977",))
        gates = result["usability_gates_failed"]
        for name in ("citation_audit", "thesis_timeline",
                     "half_year_facts", "research_score_snapshot"):
            assert any(f"模块 {name} 返回 failed" in g for g in gates)

    def test_all_structured_failure_blocks_pass(self, fixture_kb, monkeypatch):
        for name in ("consensus", "citation_audit", "thesis_timeline",
                     "half_year_facts", "research_score_snapshot"):
            _patch_builder(monkeypatch, name, "failed")
        result = run_real_smoke(fixture_kb, probe_symbols=("000977",))
        gates = result["usability_gates_failed"]
        assert sum(1 for g in gates if "返回 failed" in g) == 5
        assert any("无共识命中" in g for g in gates)

    def test_healthy_sample_passes_gates(self, fixture_kb, monkeypatch):
        _patch_builder(monkeypatch, "consensus", "fresh", has_hit=True)
        for name in ("citation_audit", "thesis_timeline",
                     "half_year_facts", "research_score_snapshot"):
            _patch_builder(monkeypatch, name, "missing")
        result = run_real_smoke(fixture_kb, probe_symbols=("000977",))
        assert result["usability_gates_failed"] == []
