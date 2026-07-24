# [SCORE-001B-R1] 快照时点、动作词与失败态补修
"""Tests for SCORE-001B-R1: snapshot timing, action verb stripping, failure semantics.

Covers (per docs/TASKS.md SCORE-001B-R1 acceptance):
  - Fix 1: Historical reports query snapshot by trade date (no future data leak).
  - Fix 2: Snapshot text must not leak strong action verbs.
  - Fix 3: Read failure returns FAILED, not NORMAL_NO_DATA.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from tradingagents.dataflows.research_score_snapshot import (
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    EvidenceRef,
    InvestmentThesis,
    ResearchScoreQueryResult,
    ResearchScoreSnapshot,
    ScoreSummary,
    query_research_score_snapshot,
    snapshot_to_api_dict,
    _strip_strong_action_verbs,
    _FORBIDDEN_ACTION_VERBS,
)

from tests.research_score_snapshot_fixtures import (
    build_snapshot_fixture_kb,
)


_TZ_CN = timezone(timedelta(hours=8))
_TODAY = date(2026, 7, 14)


def _at(y: int, m: int, d: int, hour: int = 15) -> datetime:
    return datetime(y, m, d, hour, 0, 0, tzinfo=_TZ_CN)


def _build_snapshot(
    *,
    status: str = STATUS_HAS_DATA,
    rec: Optional[int] = 82,
    tqual: Optional[int] = 78,
    snapshot_id: str = "605589.SH-20260713-r1",
    symbol: str = "605589.SH",
    as_of: str = "2026-07-13",
    core_hypothesis: str = "采购成本持续下降推动毛利率提升",
    claim: str = "原材料成本下降 10%",
) -> ResearchScoreSnapshot:
    return ResearchScoreSnapshot(
        schema_version="1.1.0",
        snapshot_id=snapshot_id,
        symbol=symbol,
        name="圣泉集团",
        as_of=as_of,
        created_at=f"{as_of}T20:00:00+08:00",
        source_cutoff_at=f"{as_of}T15:00:00+08:00",
        rubric_id="research-score-generic",
        rubric_version="1.0.0",
        status=status,
        scores=ScoreSummary(
            research_evidence_confidence=rec,
            thesis_quality=tqual,
        ),
        theses=[
            InvestmentThesis(
                thesis_id="T1",
                symbol=symbol,
                as_of=as_of,
                topic="原材料成本下降",
                direction="bullish",
                status="active",
                core_hypothesis=core_hypothesis,
            ),
        ],
        evidence_refs=[
            EvidenceRef(
                evidence_id="E1",
                claim=claim,
                claim_type="financial_fact",
                source_path="reports/605589-2026Q1.md",
                source_type="broker_report",
                source_quality_tier="B",
            ),
        ],
        missing_evidence=["尚缺 Q2 毛利率数据"],
        warnings=["快照已过期"],
    )


def _build_result(
    *,
    status: str = STATUS_HAS_DATA,
    snapshot: Optional[ResearchScoreSnapshot] = None,
) -> ResearchScoreQueryResult:
    return ResearchScoreQueryResult(
        status=status,
        symbol="605589.SH",
        analysis_time=_at(2026, 7, 14).isoformat(),
        snapshot=snapshot,
        snapshot_id=snapshot.snapshot_id if snapshot else None,
        schema_version=snapshot.schema_version if snapshot else None,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 2: 强动作词清洗
# ═══════════════════════════════════════════════════════════════════════════════


class TestStripStrongActionVerbs:
    """[SCORE-001B-R1] _strip_strong_action_verbs unit tests."""

    def test_strips_immediate_buy(self) -> None:
        assert "建议立即买入" not in _strip_strong_action_verbs("建议立即买入该股")
        assert "[已过滤]" in _strip_strong_action_verbs("建议立即买入该股")

    def test_strips_immediate_sell(self) -> None:
        result = _strip_strong_action_verbs("建议立即卖出")
        assert "建议立即卖出" not in result
        assert "[已过滤]" in result

    def test_strips_full_position(self) -> None:
        result = _strip_strong_action_verbs("满仓操作")
        assert "满仓" not in result
        assert "[已过滤]" in result

    def test_strips_clear_position(self) -> None:
        result = _strip_strong_action_verbs("清仓离场")
        assert "清仓" not in result

    def test_strips_all_in(self) -> None:
        result = _strip_strong_action_verbs("全仓梭哈")
        assert "全仓" not in result
        assert "梭哈" not in result

    def test_strips_strong_buy_sell(self) -> None:
        for verb in _FORBIDDEN_ACTION_VERBS:
            result = _strip_strong_action_verbs(f"前缀{verb}后缀")
            assert verb not in result, f"Failed to strip: {verb}"

    def test_strips_full_strong_recommendation_without_action_suffix(self) -> None:
        result = _strip_strong_action_verbs("强烈推荐买入，强烈推荐卖出")
        assert "买入" not in result
        assert "卖出" not in result

    def test_preserves_directional_words(self) -> None:
        """Directional words (看多/看空/偏多/偏空) must NOT be stripped."""
        directional = "看多方向 偏空信号 看空 看多"
        result = _strip_strong_action_verbs(directional)
        assert result == directional

    def test_preserves_analytical_phrases(self) -> None:
        analytical = "基本面改善 技术面突破 量价配合"
        result = _strip_strong_action_verbs(analytical)
        assert result == analytical

    def test_empty_input(self) -> None:
        assert _strip_strong_action_verbs("") == ""

    def test_none_like_input(self) -> None:
        assert _strip_strong_action_verbs(None) is None  # type: ignore

    def test_no_verb_no_change(self) -> None:
        text = "采购成本持续下降推动毛利率提升"
        assert _strip_strong_action_verbs(text) == text

    def test_multiple_verbs_in_text(self) -> None:
        result = _strip_strong_action_verbs("建议立即买入，满仓操作，梭哈")
        assert "建议立即买入" not in result
        assert "满仓" not in result
        assert "梭哈" not in result
        assert result.count("[已过滤]") == 3


class TestSnapshotToApiDictActionVerbStripping:
    """[SCORE-001B-R1] snapshot_to_api_dict strips action verbs."""

    def test_final_payload_recursively_strips_direct_passthrough_text(self) -> None:
        snap = _build_snapshot()
        snap.name = "强烈推荐买入"
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        result.degradation_reasons = ["建议立即卖出"]
        result.validation_warnings = ["重仓买入"]

        api = snapshot_to_api_dict(result)
        serialized = str(api)

        assert "强烈推荐买入" not in serialized
        assert "立即卖出" not in serialized
        assert "重仓买入" not in serialized
        assert "买入" not in api["snapshot"]["name"]
        assert "卖出" not in api["degradation_reasons"][0]

    def test_failed_payload_recursively_strips_top_level_text(self) -> None:
        result = _build_result(status=STATUS_FAILED, snapshot=None)
        result.degradation_reasons = ["强烈推荐卖出"]

        api = snapshot_to_api_dict(result)

        assert api["snapshot"] is None
        assert "强烈推荐" not in api["degradation_reasons"][0]
        assert "卖出" not in api["degradation_reasons"][0]

    def test_thesis_direction_strips_complete_strong_actions(self) -> None:
        snap = _build_snapshot()
        snap.theses[0].direction = "强烈推荐买入 / 强烈推荐卖出"
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        direction = api["snapshot"]["theses_summary"][0]["direction"]
        assert "强烈推荐" not in direction
        assert "买入" not in direction
        assert "卖出" not in direction
        assert direction.count("[已过滤]") == 2

    def test_thesis_core_hypothesis_stripped(self) -> None:
        snap = _build_snapshot(
            core_hypothesis="建议立即买入该股，满仓操作",
        )
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        hypothesis = api["snapshot"]["theses_summary"][0]["core_hypothesis"]
        assert "建议立即买入" not in hypothesis
        assert "满仓" not in hypothesis
        assert "[已过滤]" in hypothesis

    def test_evidence_claim_stripped(self) -> None:
        snap = _build_snapshot(
            claim="强烈推荐买入，建议加仓",
        )
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        claim_text = api["snapshot"]["evidence_refs_summary"][0]["claim"]
        assert "强烈推荐买入" not in claim_text
        assert "建议加仓" not in claim_text

    def test_warnings_stripped(self) -> None:
        snap = _build_snapshot()
        snap.warnings = ["建议清仓离场", "正常警告"]
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        assert "建议清仓离场" not in str(api["snapshot"]["warnings"])
        assert "正常警告" in api["snapshot"]["warnings"][1]

    def test_missing_evidence_stripped(self) -> None:
        snap = _build_snapshot()
        snap.missing_evidence = ["建议立即卖出该股"]
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        assert "建议立即卖出" not in str(api["snapshot"]["missing_evidence"])

    def test_no_action_verbs_in_full_output(self) -> None:
        """Comprehensive: no forbidden verb leaks in any text field."""
        snap = _build_snapshot(
            core_hypothesis="建议立即买入重仓买入",
            claim="强烈推荐满仓操作",
        )
        snap.warnings = ["强制清仓"]
        snap.missing_evidence = ["建议建仓"]
        snap.upgrade_conditions = ["建议加仓确认"]
        snap.downgrade_conditions = ["建议减仓"]
        snap.invalidation_conditions = ["建议清仓止损"]

        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)
        json_str = json.dumps(api, ensure_ascii=False)

        for verb in _FORBIDDEN_ACTION_VERBS:
            assert verb not in json_str, (
                f"Forbidden verb {verb!r} leaked into API output"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 3: 读取失败不得伪装成正常无数据
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadFailureSemantics:
    """[SCORE-001B-R1] 读取失败返回 FAILED，不是 NORMAL_NO_DATA."""

    def test_corrupt_json_returns_failed(self, tmp_path: Path) -> None:
        """所有 JSON 文件损坏 → FAILED（非 NORMAL_NO_DATA）。"""
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        # 覆盖快照文件为损坏 JSON
        sym_dir = root / "research_score_snapshots" / "605589.SH"
        for f in sym_dir.glob("*.json"):
            f.write_text("{corrupt json!!", encoding="utf-8")

        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert any("读取" in e or "解析" in e for e in result.errors)

    def test_permission_denied_returns_failed(self, tmp_path: Path) -> None:
        """文件权限不足 → FAILED（非 NORMAL_NO_DATA）。"""
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        sym_dir = root / "research_score_snapshots" / "605589.SH"
        for f in sym_dir.glob("*.json"):
            f.chmod(0o000)

        try:
            result = query_research_score_snapshot(
                str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
            )
            assert result.status == STATUS_FAILED
        finally:
            for f in sym_dir.glob("*.json"):
                f.chmod(0o644)

    def test_empty_dir_returns_normal_no_data(self, tmp_path: Path) -> None:
        """目录存在但无文件 → NORMAL_NO_DATA（不是 FAILED）。"""
        root = tmp_path / "kb"
        (root / "research_score_snapshots" / "605589.SH").mkdir(parents=True)

        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_NORMAL_NO_DATA

    def test_no_symbol_dir_returns_normal_no_data(self, tmp_path: Path) -> None:
        """symbol 目录不存在 → NORMAL_NO_DATA。"""
        root = tmp_path / "kb"
        (root / "research_score_snapshots").mkdir(parents=True)

        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_NORMAL_NO_DATA

    def test_partial_corrupt_one_valid(self, tmp_path: Path) -> None:
        """部分文件损坏但有一个可读 → 正常返回（HAS_DATA 或 STALE）。"""
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        sym_dir = root / "research_score_snapshots" / "605589.SH"
        # 写入一个损坏文件
        (sym_dir / "corrupt.json").write_text("not json!", encoding="utf-8")

        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        # 应该能读到合格快照
        assert result.status in (STATUS_HAS_DATA, STATUS_STALE, STATUS_LOW_CONFIDENCE)
        assert any("corrupt" in e for e in result.errors)

    def test_all_future_snapshots_returns_normal_no_data(self, tmp_path: Path) -> None:
        """所有快照都是未来快照 → NORMAL_NO_DATA（非 FAILED，因为文件可读）。"""
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        # analysis_time 在快照 as_of 之前
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 1, 1)
        )
        assert result.status == STATUS_NORMAL_NO_DATA

    def test_report_adapter_preserves_failed_status(self) -> None:
        from api.services.report_service import attach_report_research_score_snapshot

        failed = _build_result(status=STATUS_FAILED, snapshot=None)
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value="/tmp/knowledge",
        ), patch(
            "tradingagents.dataflows.research_score_snapshot.query_research_score_snapshot",
            return_value=failed,
        ):
            enriched = attach_report_research_score_snapshot(
                {"key": "value"},
                symbol="605589.SH",
                trade_date="2026-07-14",
            )

        assert enriched["research_score_snapshot"]["status"] == STATUS_FAILED
        assert enriched["research_score_snapshot"]["snapshot"] is None

    def test_candidate_adapter_preserves_failed_status(self) -> None:
        from api.services import tradeflow_service as tfs

        failed = _build_result(status=STATUS_FAILED, snapshot=None)
        with patch.object(
            tfs, "_resolve_knowledge_root", return_value="/tmp/knowledge"
        ), patch(
            "tradingagents.dataflows.research_score_snapshot.query_research_score_snapshot",
            return_value=failed,
        ):
            item = tfs._enrich_candidate_with_research_score_snapshot(
                {
                    "symbol": "605589.SH",
                    "effective_trade_date": "2026-07-14",
                }
            )

        assert item["research_score_snapshot"]["status"] == STATUS_FAILED
        assert item["research_score_snapshot"]["snapshot"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 1: 历史报告按交易日查询快照
# ═══════════════════════════════════════════════════════════════════════════════


class TestHistoricalReportSnapshotTiming:
    """[SCORE-001B-R1] 历史报告按 trade date 查询快照，无未来数据泄漏。"""

    def test_report_service_uses_trade_date(self, tmp_path: Path) -> None:
        """attach_report_research_score_snapshot 按 trade_date 查询。"""
        from api.services.report_service import attach_report_research_score_snapshot

        # 快照 as_of=2026-07-13
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value=str(root),
        ):
            # trade_date=2026-07-14 → 应该能读到 as_of=2026-07-13 的快照
            result_data = {"key": "val"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH", trade_date="2026-07-14",
            )
            assert enriched is not None
            assert "research_score_snapshot" in enriched

    def test_report_service_historical_date_excludes_future(self, tmp_path: Path) -> None:
        """历史报告 trade_date 早于快照 as_of → 无快照（不泄漏未来数据）。"""
        from api.services.report_service import attach_report_research_score_snapshot

        # 快照 as_of=2026-07-13
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value=str(root),
        ):
            # trade_date=2026-06-01 → 快照 as_of=2026-07-13 是未来 → 不选
            result_data = {"key": "val"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH", trade_date="2026-06-01",
            )
            # 无快照 → result_data 不变
            assert "research_score_snapshot" not in (enriched or {})

    def test_report_service_none_trade_date_uses_now(self, tmp_path: Path) -> None:
        """trade_date=None → fallback 到当前时间（向后兼容）。"""
        from api.services.report_service import attach_report_research_score_snapshot

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value=str(root),
        ):
            result_data = {"key": "val"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH", trade_date=None,
            )
            # 当前时间 > as_of=2026-07-13 → 应该有快照
            assert enriched is not None

    def test_report_service_invalid_trade_date_fallback(self, tmp_path: Path) -> None:
        """trade_date 格式无效 → fallback 到当前时间。"""
        from api.services.report_service import attach_report_research_score_snapshot

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value=str(root),
        ):
            result_data = {"key": "val"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH", trade_date="not-a-date",
            )
            # fallback 到当前时间 → 应该有快照
            assert enriched is not None

    def test_tradeflow_candidate_uses_effective_trade_date(self, tmp_path: Path) -> None:
        """TradeFlow 候选有 effective_trade_date 字段时按日期查询。"""
        from api.services import tradeflow_service as tfs

        # 快照 as_of=2026-07-13
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            # effective_trade_date=2026-07-14 → 能读到 as_of=2026-07-13
            item = {"symbol": "605589.SH", "name": "圣泉集团", "effective_trade_date": "2026-07-14"}
            result = tfs._enrich_candidate_with_research_score_snapshot(item)
            assert result.get("research_score_snapshot") is not None

    def test_tradeflow_candidate_historical_date_excludes_future(self, tmp_path: Path) -> None:
        """TradeFlow 候选 effective_trade_date 早于快照 as_of → 不注入（不泄漏未来数据）。"""
        from api.services import tradeflow_service as tfs

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            # effective_trade_date=2026-06-01 → 快照 as_of=2026-07-13 是未来 → 不注入
            item = {"symbol": "605589.SH", "name": "圣泉集团", "effective_trade_date": "2026-06-01"}
            result = tfs._enrich_candidate_with_research_score_snapshot(item)
            assert result.get("research_score_snapshot") is None

    def test_tradeflow_candidate_trade_date_excludes_future(self, tmp_path: Path) -> None:
        """旧候选仅有 trade_date 时也不得读取该日期之后的快照。"""
        from api.services import tradeflow_service as tfs

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            item = {
                "symbol": "605589.SH",
                "name": "圣泉集团",
                "trade_date": "2026-06-01",
            }
            result = tfs._enrich_candidate_with_research_score_snapshot(item)
        assert result.get("research_score_snapshot") is None

    def test_candidate_row_mapping_preserves_trade_date(self) -> None:
        """候选详情的基础行映射必须把历史 trade_date 传给快照解析器。"""
        from api.services import tradeflow_service as tfs

        item = tfs._row_to_candidate_item({
            "symbol": "605589.SH",
            "name": "圣泉集团",
            "trade_date": "2026-06-01",
        })

        assert item["trade_date"] == "2026-06-01"

    def test_tradeflow_candidate_plan_date_field(self, tmp_path: Path) -> None:
        """TradeFlow 候选使用 plan_date 字段。"""
        from api.services import tradeflow_service as tfs

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            item = {
                "symbol": "605589.SH",
                "name": "圣泉集团",
                "plan_date": "2026-07-14",
            }
            result = tfs._enrich_candidate_with_research_score_snapshot(item)
            assert result.get("research_score_snapshot") is not None

    def test_tradeflow_candidate_no_date_uses_now(self, tmp_path: Path) -> None:
        """TradeFlow 候选无日期字段 → fallback 到当前时间。"""
        from api.services import tradeflow_service as tfs

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            item = {"symbol": "605589.SH", "name": "圣泉集团"}
            result = tfs._enrich_candidate_with_research_score_snapshot(item)
            assert result.get("research_score_snapshot") is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 向后兼容：已有测试不应被破坏
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """确保已有 SCORE-001B 功能不受 R1 补修影响。"""

    def test_snapshot_to_api_dict_has_data(self) -> None:
        snap = _build_snapshot()
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_HAS_DATA
        assert api["snapshot"] is not None
        assert api["snapshot"]["symbol"] == "605589.SH"

    def test_snapshot_to_api_dict_no_data(self) -> None:
        result = _build_result(status=STATUS_NORMAL_NO_DATA, snapshot=None)
        api = snapshot_to_api_dict(result)
        assert api["snapshot"] is None

    def test_snapshot_to_api_dict_failed(self) -> None:
        result = _build_result(status=STATUS_FAILED, snapshot=None)
        api = snapshot_to_api_dict(result)
        assert api["status"] == STATUS_FAILED
        assert api["snapshot"] is None

    def test_query_with_valid_snapshot(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot is not None

    def test_report_service_backward_compat(self, tmp_path: Path) -> None:
        """旧调用方不传 trade_date 仍正常工作。"""
        from api.services.report_service import attach_report_research_score_snapshot

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value=str(root),
        ):
            result_data = {"key": "val"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH"
            )
            assert enriched is not None

    def test_no_action_keys_in_api_output(self) -> None:
        snap = _build_snapshot()
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        banned = {"decision", "action_label", "buy_level"}
        stack: List[Any] = [api]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                assert not (set(node.keys()) & banned)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
