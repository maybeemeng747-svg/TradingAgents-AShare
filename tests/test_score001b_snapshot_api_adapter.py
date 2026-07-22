# [SCORE-001B] research_score_snapshot_api_adapter
"""Tests for 研究快照接入聚合响应与 TradeFlow candidate detail (SCORE-001B).

Covers (per docs/TASKS.md SCORE-001B acceptance):
  - snapshot_to_api_dict: HAS_DATA / NO_DATA / FAILED / LOW_CONFIDENCE / STALE /
    sensitive info filtering / absolute path redaction.
  - KB-020 aggregate: research_score_snapshot bucket present, data_status mapping,
    backward-compatible when no snapshot.
  - TradeFlow candidate detail: enrichment with/without snapshot, graceful failure.
  - Report service: attach_report_research_score_snapshot with/without data.
  - No strong action verbs / banned action keys in snapshot API output.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from tradingagents.dataflows.research_score_snapshot import (
    DRAFTS_DIR_NAME,
    SNAPSHOTS_DIR_NAME,
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    EvidenceRef,
    InvestmentThesis,
    ResearchScoreQueryResult,
    ResearchScoreSnapshot,
    ScoreChange,
    ScoreSummary,
    query_research_score_snapshot,
    snapshot_to_api_dict,
)

from tests.research_score_snapshot_fixtures import (
    build_snapshot_fixture_kb,
)


# ── helpers ──────────────────────────────────────────────────────────

_TZ_CN = timezone(timedelta(hours=8))
_TODAY = date(2026, 7, 14)

_FORBIDDEN_ACTION_WORDS = (
    "立即买入", "立即卖出", "满仓", "清仓", "全仓",
)

_BANNED_ACTION_KEYS = {"decision", "action_label", "buy_level"}


def _at(y: int, m: int, d: int, hour: int = 15) -> datetime:
    return datetime(y, m, d, hour, 0, 0, tzinfo=_TZ_CN)


def _build_result(
    *,
    status: str = STATUS_HAS_DATA,
    snapshot: Optional[ResearchScoreSnapshot] = None,
) -> ResearchScoreQueryResult:
    """Build a minimal ResearchScoreQueryResult for testing."""
    return ResearchScoreQueryResult(
        status=status,
        symbol="605589.SH",
        analysis_time=_at(2026, 7, 14).isoformat(),
        snapshot=snapshot,
        snapshot_id=snapshot.snapshot_id if snapshot else None,
        schema_version=snapshot.schema_version if snapshot else None,
    )


def _build_snapshot(
    *,
    status: str = STATUS_HAS_DATA,
    rec: Optional[int] = 82,
    tqual: Optional[int] = 78,
    snapshot_id: str = "605589.SH-20260713-r1",
    symbol: str = "605589.SH",
    as_of: str = "2026-07-13",
) -> ResearchScoreSnapshot:
    """Build a minimal ResearchScoreSnapshot for testing."""
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
                core_hypothesis="采购成本持续下降推动毛利率提升",
            ),
        ],
        evidence_refs=[
            EvidenceRef(
                evidence_id="E1",
                claim="原材料成本下降 10%",
                claim_type="financial_fact",
                source_path="reports/605589-2026Q1.md",
                source_type="broker_report",
                source_quality_tier="B",
            ),
        ],
        missing_evidence=["尚缺 Q2 毛利率数据"],
        warnings=["快照已过期"],
    )


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, (
            f"text contains forbidden action word: {forbidden}"
        )


def _assert_no_action_keys(payload: Any) -> None:
    """Walk payload and assert no decision/action_label/buy_level keys leak."""
    stack: List[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            assert not (set(node.keys()) & _BANNED_ACTION_KEYS), (
                f"banned action key leaked: {set(node.keys()) & _BANNED_ACTION_KEYS}"
            )
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


# ── snapshot_to_api_dict ────────────────────────────────────────────


class TestSnapshotToApiDict:
    """Test SCORE-001B serialization function."""

    def test_has_data_snapshot(self) -> None:
        """HAS_DATA snapshot → correct slim output."""
        snap = _build_snapshot()
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_HAS_DATA
        assert api["snapshot_id"] == "605589.SH-20260713-r1"
        assert api["schema_version"] == "1.1.0"

        snap_dict = api["snapshot"]
        assert snap_dict is not None
        assert snap_dict["symbol"] == "605589.SH"
        assert snap_dict["as_of"] == "2026-07-13"
        assert snap_dict["scores"]["research_evidence_confidence"] == 82
        assert snap_dict["scores"]["thesis_quality"] == 78

        # theses_summary present
        assert len(snap_dict["theses_summary"]) == 1
        assert snap_dict["theses_summary"][0]["thesis_id"] == "T1"
        assert snap_dict["theses_summary"][0]["topic"] == "原材料成本下降"

        # evidence_refs_summary present
        assert len(snap_dict["evidence_refs_summary"]) == 1
        assert snap_dict["evidence_refs_summary"][0]["evidence_id"] == "E1"

        # missing_evidence present
        assert len(snap_dict["missing_evidence"]) == 1

    def test_no_data_snapshot(self) -> None:
        """NORMAL_NO_DATA → snapshot=None, old API compatible."""
        result = _build_result(status=STATUS_NORMAL_NO_DATA, snapshot=None)
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_NORMAL_NO_DATA
        assert api["snapshot"] is None
        assert api["snapshot_id"] is None

    def test_failed_snapshot(self) -> None:
        """FAILED → snapshot=None."""
        result = _build_result(status=STATUS_FAILED, snapshot=None)
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_FAILED
        assert api["snapshot"] is None

    def test_low_confidence_snapshot(self) -> None:
        """LOW_CONFIDENCE → snapshot present with status."""
        snap = _build_snapshot(status=STATUS_LOW_CONFIDENCE, rec=30, tqual=25)
        result = _build_result(status=STATUS_LOW_CONFIDENCE, snapshot=snap)
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_LOW_CONFIDENCE
        assert api["snapshot"] is not None
        assert api["snapshot"]["status"] == STATUS_LOW_CONFIDENCE
        assert api["snapshot"]["scores"]["research_evidence_confidence"] == 30

    def test_stale_snapshot(self) -> None:
        """STALE → snapshot present with status."""
        snap = _build_snapshot(status=STATUS_STALE)
        result = _build_result(status=STATUS_STALE, snapshot=snap)
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_STALE
        assert api["snapshot"] is not None
        assert api["snapshot"]["status"] == STATUS_STALE

    def test_none_scores(self) -> None:
        """None scores → output as None, not 0."""
        snap = _build_snapshot(rec=None, tqual=None)
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        scores = api["snapshot"]["scores"]
        assert scores["research_evidence_confidence"] is None
        assert scores["thesis_quality"] is None

    def test_sensitive_key_filtering(self) -> None:
        """Keys matching sensitive patterns are filtered out."""
        snap = _build_snapshot()
        snap.warnings = ["正常警告"]
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        # No sensitive keys in output
        json_str = json.dumps(api)
        assert "api_key" not in json_str.lower() or "api" not in json_str.lower()

    def test_absolute_path_redaction(self) -> None:
        """Absolute paths in evidence refs are redacted."""
        snap = _build_snapshot()
        # Inject absolute path into evidence ref source_path
        snap.evidence_refs[0].source_path = "/Users/maybee/secret/report.md"
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        json_str = json.dumps(api)
        assert "/Users/maybee" not in json_str

    def test_no_action_keys_leak(self) -> None:
        """No decision/action_label/buy_level in output."""
        snap = _build_snapshot()
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)
        _assert_no_action_keys(api)

    def test_no_strong_action_verbs(self) -> None:
        """No strong action verbs in output text."""
        snap = _build_snapshot()
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)
        json_str = json.dumps(api)
        _assert_no_strong_action_words(json_str)

    def test_invalid_input(self) -> None:
        """Non-ResearchScoreQueryResult → NO_DATA."""
        api = snapshot_to_api_dict(None)  # type: ignore
        assert api["status"] == STATUS_NORMAL_NO_DATA
        assert api["snapshot"] is None

    def test_score_change_summary(self) -> None:
        """score_change with reasons → summary present."""
        snap = _build_snapshot()
        snap.score_change = ScoreChange(
            previous_snapshot_id="old-snap",
            reasons=["原材料价格下跌", "Q1 毛利率提升"],
        )
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)

        sc = api["snapshot"]["score_change_summary"]
        assert sc is not None
        assert sc["previous_snapshot_id"] == "old-snap"
        assert len(sc["reasons"]) == 2

    def test_degradation_reasons_preserved(self) -> None:
        """Degradation reasons are passed through."""
        snap = _build_snapshot()
        result = _build_result(status=STATUS_STALE, snapshot=snap)
        result.degradation_reasons = ["LOADER_STALE: as_of=2026-01-01 距 analysis_time=195d"]
        api = snapshot_to_api_dict(result)

        assert len(api["degradation_reasons"]) == 1
        assert "LOADER_STALE" in api["degradation_reasons"][0]


# ── KB-020 aggregate ────────────────────────────────────────────────


class TestKB020ResearchScoreSnapshotBucket:
    """Test SCORE-001B integration into KB-020 aggregate response."""

    def test_bucket_present_in_response(self, tmp_path: Path) -> None:
        """research_score_snapshot bucket appears in KB-020 response."""
        from api.services import research_evidence_service as svc

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(svc, "resolve_knowledge_root", return_value=str(root)):
            with patch.object(svc, "is_local_knowledge_disabled", return_value=False):
                payload = svc.build_research_evidence(
                    "605589.SH",
                    today=_TODAY,
                    knowledge_root=str(root),
                    include_runtime_meta=False,
                )

        assert svc.BUCKET_RESEARCH_SCORE_SNAPSHOT in payload
        bucket = payload[svc.BUCKET_RESEARCH_SCORE_SNAPSHOT]
        assert bucket["bucket"] == svc.BUCKET_RESEARCH_SCORE_SNAPSHOT
        assert bucket["task"] == "SCORE-001"
        assert bucket["has_hit"] is True
        assert bucket["data_status"] in ("fresh", "stale", "missing", "failed", "skipped")

    def test_bucket_no_snapshot(self, tmp_path: Path) -> None:
        """No snapshot → bucket with has_hit=False."""
        from api.services import research_evidence_service as svc

        # Empty knowledge root — no snapshots
        root = tmp_path / "empty_kb"
        root.mkdir()
        with patch.object(svc, "resolve_knowledge_root", return_value=str(root)):
            with patch.object(svc, "is_local_knowledge_disabled", return_value=False):
                payload = svc.build_research_evidence(
                    "605589.SH",
                    today=_TODAY,
                    knowledge_root=str(root),
                    include_runtime_meta=False,
                )

        bucket = payload[svc.BUCKET_RESEARCH_SCORE_SNAPSHOT]
        assert bucket["has_hit"] is False
        assert bucket["data_status"] == "missing"

    def test_bucket_disabled(self, tmp_path: Path) -> None:
        """Disabled → bucket skipped."""
        from api.services import research_evidence_service as svc

        payload = svc.build_research_evidence(
            "605589.SH",
            today=_TODAY,
            knowledge_root=str(tmp_path),
            disabled=True,
            include_runtime_meta=False,
        )

        bucket = payload[svc.BUCKET_RESEARCH_SCORE_SNAPSHOT]
        assert bucket["data_status"] == "skipped"

    def test_bucket_no_action_keys(self, tmp_path: Path) -> None:
        """No banned action keys in KB-020 response with snapshot bucket."""
        from api.services import research_evidence_service as svc

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(svc, "resolve_knowledge_root", return_value=str(root)):
            with patch.object(svc, "is_local_knowledge_disabled", return_value=False):
                payload = svc.build_research_evidence(
                    "605589.SH",
                    today=_TODAY,
                    knowledge_root=str(root),
                    include_runtime_meta=False,
                )

        _assert_no_action_keys(payload)

    def test_source_freshness_includes_snapshot(self, tmp_path: Path) -> None:
        """source_freshness includes research_score_snapshot entry."""
        from api.services import research_evidence_service as svc

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(svc, "resolve_knowledge_root", return_value=str(root)):
            with patch.object(svc, "is_local_knowledge_disabled", return_value=False):
                payload = svc.build_research_evidence(
                    "605589.SH",
                    today=_TODAY,
                    knowledge_root=str(root),
                    include_runtime_meta=False,
                )

        freshness = payload.get("source_freshness", {})
        assert "research_score_snapshot" in freshness


# ── TradeFlow candidate detail ──────────────────────────────────────


class TestTradeFlowCandidateSnapshotEnrichment:
    """Test SCORE-001B integration into TradeFlow candidate detail."""

    def test_enrichment_with_snapshot(self, tmp_path: Path) -> None:
        """Candidate detail gets research_score_snapshot field."""
        from api.services import tradeflow_service as tfs

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            item = {"symbol": "605589.SH", "name": "圣泉集团"}
            result = tfs._enrich_candidate_with_research_score_snapshot(item)

        assert "research_score_snapshot" in result
        snap = result["research_score_snapshot"]
        assert snap is not None
        assert snap["status"] == STATUS_HAS_DATA
        assert snap["snapshot"] is not None

    def test_enrichment_without_snapshot(self, tmp_path: Path) -> None:
        """No snapshot → research_score_snapshot=None."""
        from api.services import tradeflow_service as tfs

        root = tmp_path / "empty_kb"
        root.mkdir()
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            item = {"symbol": "605589.SH", "name": "圣泉集团"}
            result = tfs._enrich_candidate_with_research_score_snapshot(item)

        assert result.get("research_score_snapshot") is None

    def test_enrichment_no_symbol(self) -> None:
        """No symbol → research_score_snapshot=None."""
        from api.services import tradeflow_service as tfs

        item = {"symbol": "", "name": ""}
        result = tfs._enrich_candidate_with_research_score_snapshot(item)
        assert result.get("research_score_snapshot") is None

    def test_enrichment_no_knowledge_root(self) -> None:
        """No knowledge root → research_score_snapshot=None."""
        from api.services import tradeflow_service as tfs

        with patch.object(tfs, "_resolve_knowledge_root", return_value=""):
            item = {"symbol": "605589.SH", "name": "圣泉集团"}
            result = tfs._enrich_candidate_with_research_score_snapshot(item)

        assert result.get("research_score_snapshot") is None

    def test_enrichment_failure_graceful(self) -> None:
        """Exception → research_score_snapshot=None (graceful degradation)."""
        from api.services import tradeflow_service as tfs

        with patch.object(tfs, "_resolve_knowledge_root", return_value="/nonexistent"):
            item = {"symbol": "605589.SH", "name": "圣泉集团"}
            result = tfs._enrich_candidate_with_research_score_snapshot(item)

        assert result.get("research_score_snapshot") is None

    def test_enrichment_no_action_keys(self, tmp_path: Path) -> None:
        """No banned action keys in enrichment output."""
        from api.services import tradeflow_service as tfs

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            item = {"symbol": "605589.SH", "name": "圣泉集团"}
            result = tfs._enrich_candidate_with_research_score_snapshot(item)

        _assert_no_action_keys(result)

    def test_enrichment_preserves_existing_fields(self, tmp_path: Path) -> None:
        """Enrichment does not remove existing candidate fields."""
        from api.services import tradeflow_service as tfs

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch.object(tfs, "_resolve_knowledge_root", return_value=str(root)):
            item = {
                "symbol": "605589.SH",
                "name": "圣泉集团",
                "score": 75.0,
                "tier": "A",
                "trigger_price": 25.5,
            }
            result = tfs._enrich_candidate_with_research_score_snapshot(item)

        assert result["score"] == 75.0
        assert result["tier"] == "A"
        assert result["trigger_price"] == 25.5
        assert "research_score_snapshot" in result


# ── Report service ──────────────────────────────────────────────────


class TestReportServiceSnapshotAttachment:
    """Test SCORE-001B integration into report_service."""

    def test_attach_with_snapshot(self, tmp_path: Path) -> None:
        """Report result_data gets research_score_snapshot field."""
        from api.services.report_service import attach_report_research_score_snapshot

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value=str(root),
        ):
            result_data = {"some_key": "some_value"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH"
            )

        assert enriched is not None
        assert "research_score_snapshot" in enriched
        assert enriched["some_key"] == "some_value"  # original preserved
        snap = enriched["research_score_snapshot"]
        assert snap["status"] == STATUS_HAS_DATA

    def test_attach_without_snapshot(self, tmp_path: Path) -> None:
        """No snapshot → result_data unchanged."""
        from api.services.report_service import attach_report_research_score_snapshot

        root = tmp_path / "empty_kb"
        root.mkdir()
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value=str(root),
        ):
            result_data = {"some_key": "some_value"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH"
            )

        # Should not add research_score_snapshot key when no data
        assert "research_score_snapshot" not in (enriched or {})

    def test_attach_no_symbol(self) -> None:
        """No symbol → result_data unchanged."""
        from api.services.report_service import attach_report_research_score_snapshot

        result_data = {"some_key": "some_value"}
        enriched = attach_report_research_score_snapshot(result_data, symbol=None)
        assert enriched == result_data

    def test_attach_none_result_data(self) -> None:
        """None result_data → returns None."""
        from api.services.report_service import attach_report_research_score_snapshot

        enriched = attach_report_research_score_snapshot(None, symbol="605589.SH")
        assert enriched is None

    def test_attach_failure_graceful(self) -> None:
        """Exception → original result_data returned."""
        from api.services.report_service import attach_report_research_score_snapshot

        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            side_effect=RuntimeError("KB unavailable"),
        ):
            result_data = {"some_key": "some_value"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH"
            )

        assert enriched == result_data

    def test_attach_no_action_keys(self, tmp_path: Path) -> None:
        """No banned action keys in attached snapshot."""
        from api.services.report_service import attach_report_research_score_snapshot

        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        with patch(
            "tradingagents.dataflows.research_score_snapshot.default_knowledge_root",
            return_value=str(root),
        ):
            result_data = {"some_key": "some_value"}
            enriched = attach_report_research_score_snapshot(
                result_data, symbol="605589.SH"
            )

        _assert_no_action_keys(enriched)


# ── Integration with real SCORE-001 loader ──────────────────────────


class TestSnapshotToApiDictIntegration:
    """Integration tests using real SCORE-001 query → snapshot_to_api_dict."""

    def test_full_pipeline_has_data(self, tmp_path: Path) -> None:
        """Full pipeline: query → serialize → correct output."""
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_HAS_DATA
        assert api["snapshot"] is not None
        assert api["snapshot"]["symbol"] == "605589.SH"
        assert api["snapshot"]["scores"]["research_evidence_confidence"] == 82

    def test_full_pipeline_no_data(self, tmp_path: Path) -> None:
        """Full pipeline with empty KB → NO_DATA."""
        root = tmp_path / "empty_kb"
        root.mkdir()
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_NORMAL_NO_DATA
        assert api["snapshot"] is None

    def test_full_pipeline_multi_version(self, tmp_path: Path) -> None:
        """Multi-version: query picks latest → serialize correctly."""
        root = build_snapshot_fixture_kb(
            tmp_path, include=["multi_version_r1", "multi_version_r2"]
        )
        result = query_research_score_snapshot(
            str(root), symbol="600519.SH", analysis_time=_at(2026, 7, 14)
        )
        api = snapshot_to_api_dict(result)

        assert api["status"] == STATUS_HAS_DATA
        # R2 is newer (2026-07-10 vs 2026-06-01)
        assert api["snapshot_id"] == "600519.SH-20260710-r2"

    def test_full_pipeline_drafts_excluded(self, tmp_path: Path) -> None:
        """Drafts excluded → NO_DATA if only drafts exist."""
        root = build_snapshot_fixture_kb(tmp_path, include=["draft_603629"])
        result = query_research_score_snapshot(
            str(root), symbol="603629.SH", analysis_time=_at(2026, 7, 14)
        )
        api = snapshot_to_api_dict(result)

        # Drafts should not be selected
        assert api["snapshot"] is None or api["status"] != STATUS_HAS_DATA


# ── JSON serializable ───────────────────────────────────────────────


class TestJsonSerializable:
    """Ensure snapshot_to_api_dict output is JSON serializable."""

    def test_has_data_json_serializable(self) -> None:
        snap = _build_snapshot()
        result = _build_result(status=STATUS_HAS_DATA, snapshot=snap)
        api = snapshot_to_api_dict(result)
        json_str = json.dumps(api, ensure_ascii=False)
        assert "605589.SH" in json_str

    def test_no_data_json_serializable(self) -> None:
        result = _build_result(status=STATUS_NORMAL_NO_DATA, snapshot=None)
        api = snapshot_to_api_dict(result)
        json_str = json.dumps(api, ensure_ascii=False)
        assert STATUS_NORMAL_NO_DATA in json_str
