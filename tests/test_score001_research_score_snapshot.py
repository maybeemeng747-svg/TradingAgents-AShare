# [SCORE-001] research_score_snapshot_contract
"""Tests for TA 只读 research_score_snapshot v1.1.0 loader / provider (SCORE-001).

覆盖（对应任务验收方式）：
  - fixture 覆盖五类状态、完整/缺字段、过期、损坏、symbol 错配、未知版本、
    非法分数、未来快照、悬空 evidence ref、invalidated thesis 和夹带动作字段。
  - drafts 不被生产 loader 选中。
  - 同标的多版本严格按 analysis_time 选择，未来快照不穿越。
  - provider 不写知识库/DB、不调用 LLM；重复读取幂等。
  - 路径逃逸/符号链接逃逸 fail closed。
  - 知识快照极高分时 decision/execution_action/action_tier 不发生变化。
  - 来源等级复用 KB-014，不建第二套。
"""

from __future__ import annotations

import json
import os
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingagents.dataflows import research_score_snapshot as rss
from tradingagents.dataflows.citation_policy import SOURCE_QUALITY_TIERS
from tradingagents.dataflows.research_score_snapshot import (
    DEFAULT_STALE_AFTER_DAYS,
    DRAFTS_DIR_NAME,
    FORBIDDEN_ACTION_FIELDS,
    SCHEMA_VERSION,
    SNAPSHOTS_DIR_NAME,
    STATUS_FAILED,
    STATUS_HAS_DATA,
    STATUS_LOW_CONFIDENCE,
    STATUS_NORMAL_NO_DATA,
    STATUS_STALE,
    SUPPORTED_SCHEMA_VERSIONS,
    EvidenceRef,
    InvestmentThesis,
    ResearchScoreQueryResult,
    ResearchScoreSnapshot,
    ScoreChange,
    ScoreSummary,
    SnapshotValidationError,
    query_research_score_snapshot,
    render_research_score_block,
    render_research_score_report,
    suggest_query_output_path,
)

from tests.research_score_snapshot_fixtures import (
    FIXTURES_BY_NAME,
    FIXTURE_SPECS,
    SnapshotFixtureSpec,
    build_snapshot_fixture_kb,
)


# ── 工具 ──────────────────────────────────────────────────────────────

_TZ_CN = timezone(timedelta(hours=8))


def _at(y: int, m: int, d: int, hour: int = 15) -> datetime:
    return datetime(y, m, d, hour, 0, 0, tzinfo=_TZ_CN)


def _write_snapshot_file(
    root: Path,
    symbol: str,
    filename: str,
    data: dict,
    *,
    in_drafts: bool = False,
) -> Path:
    snapshots_root = root / SNAPSHOTS_DIR_NAME
    if in_drafts:
        target_dir = snapshots_root / DRAFTS_DIR_NAME / symbol
    else:
        target_dir = snapshots_root / symbol
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _base_snapshot_dict(symbol="605589.SH", as_of="2026-07-13", rec=82, tqual=78, **overrides):
    from tests.research_score_snapshot_fixtures import _base_snapshot

    snap = _base_snapshot(symbol=symbol, as_of=as_of, rec=rec, tqual=tqual)
    snap.update(overrides)
    return snap


# ── 五类合法状态 ──────────────────────────────────────────────────────


class TestFiveStatuses:
    """覆盖 HAS_DATA / STALE / LOW_CONFIDENCE / NORMAL_NO_DATA / FAILED。"""

    def test_qualified_has_data(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot is not None
        assert result.snapshot.symbol == "605589.SH"
        assert result.snapshot.scores.research_evidence_confidence == 82
        assert result.snapshot.scores.thesis_quality == 78
        assert result.schema_version == SCHEMA_VERSION
        assert result.snapshot_id == "605589.SH-20260713-r1"

    def test_stale_status_passthrough(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(
            tmp_path, include=["qualified", "stale_status"]
        )
        # stale_status has newer mtime / same as_of; make qualified older by using
        # a dedicated dir: only include stale_status.
        root = build_snapshot_fixture_kb(tmp_path, include=["stale_status"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_STALE
        assert result.snapshot is not None
        assert result.snapshot.status == STATUS_STALE

    def test_low_confidence_passthrough(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["low_confidence"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_LOW_CONFIDENCE
        assert result.snapshot is not None
        assert result.snapshot.scores.research_evidence_confidence == 40

    def test_normal_no_data_declared(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["normal_no_data"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.snapshot is not None  # declared in formal file
        assert result.snapshot.scores.research_evidence_confidence is None
        assert result.snapshot.scores.thesis_quality is None

    def test_failed_status_declared(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["failed_status"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert result.snapshot is not None


# ── 缺字段 / null 语义 ────────────────────────────────────────────────


class TestMissingFieldsAndNullSemantics:
    """不用 0 代替缺证据/不可评分。"""

    def test_missing_scores_kept_as_none(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["missing_scores"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot.scores.research_evidence_confidence is None
        assert result.snapshot.scores.thesis_quality is None

    def test_zero_is_valid_score_not_missing(self, tmp_path: Path) -> None:
        # 0 是合法分数（低分），不应被当作缺失。
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["scores"] = {
            "research_evidence_confidence": 0,
            "thesis_quality": 0,
        }
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot.scores.research_evidence_confidence == 0
        assert result.snapshot.scores.thesis_quality == 0


# ── fail-closed 校验路径 ─────────────────────────────────────────────


class TestFailClosedValidation:
    """损坏 / symbol 错配 / 未知版本 / 非法分数 / 悬空 ref / 路径逃逸 → FAILED。"""

    def test_corrupted_json_only_candidate(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["corrupted"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        # [SCORE-001B-R1] 损坏文件在磁盘上存在但读取失败 → FAILED（非 NORMAL_NO_DATA）。
        assert result.status == STATUS_FAILED
        assert any("JSON" in e or "解析" in e for e in result.errors)

    def test_symbol_mismatch_fail_closed(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["symbol_mismatch"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        # 候选存在但 symbol 不匹配 → FAILED（fail closed，不返回错配快照）。
        assert result.status == STATUS_FAILED
        assert "SYMBOL_MISMATCH" in result.degradation_reasons
        assert result.snapshot is None

    def test_unknown_schema_version_fail_closed(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["unknown_version"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "UNKNOWN_SCHEMA_VERSION" in result.degradation_reasons
        assert result.snapshot is None

    def test_illegal_score_fail_closed(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["illegal_score"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "ILLEGAL_SCORE" in result.degradation_reasons
        assert result.snapshot is None

    def test_negative_score_fail_closed(self, tmp_path: Path) -> None:
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["scores"] = {
            "research_evidence_confidence": -5,
            "thesis_quality": 78,
        }
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "ILLEGAL_SCORE" in result.degradation_reasons

    def test_string_score_fail_closed(self, tmp_path: Path) -> None:
        # 字符串 "82" 不是数值 → 拒绝（防止 "0" 字符串冒充）。
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["scores"] = {
            "research_evidence_confidence": "82",
            "thesis_quality": 78,
        }
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "ILLEGAL_SCORE" in result.degradation_reasons

    def test_dangling_evidence_ref_fail_closed(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["dangling_ref"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "DANGLING_EVIDENCE_REF" in result.degradation_reasons

    def test_illegal_tier_fail_closed(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["illegal_tier"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "CORRUPTED" in result.degradation_reasons

    def test_path_escape_evidence_fail_closed(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["path_escape"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "PATH_ESCAPE" in result.degradation_reasons

    def test_dotdot_source_path_rejected(self, tmp_path: Path) -> None:
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["evidence_refs"][0]["source_path"] = "../../../etc/passwd"
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "PATH_ESCAPE" in result.degradation_reasons

    def test_missing_rubric_fail_closed(self, tmp_path: Path) -> None:
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["rubric_version"] = ""
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "CORRUPTED" in result.degradation_reasons

    def test_invalid_thesis_status_fail_closed(self, tmp_path: Path) -> None:
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["theses"][0]["status"] = "bogus"
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "CORRUPTED" in result.degradation_reasons


# ── 禁止动作字段（fail closed）────────────────────────────────────────


class TestForbiddenActionFields:
    """夹带 action/execution_action/playbook_stage/... → fail closed。"""

    @pytest.mark.parametrize("field", list(FORBIDDEN_ACTION_FIELDS))
    def test_top_level_forbidden_field_rejected(
        self, tmp_path: Path, field: str
    ) -> None:
        root = tmp_path
        snap = _base_snapshot_dict()
        snap[field] = " injected "
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "FORBIDDEN_ACTION_FIELD" in result.degradation_reasons

    def test_scores_subtable_forbidden_field_rejected(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["forbidden_scores"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "FORBIDDEN_ACTION_FIELD" in result.degradation_reasons

    def test_thesis_forbidden_field_rejected(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["forbidden_thesis"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "FORBIDDEN_ACTION_FIELD" in result.degradation_reasons

    def test_thesis_breakdown_forbidden_field_rejected(self, tmp_path: Path) -> None:
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["thesis_breakdown"]["planned_position"] = 0.5
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "FORBIDDEN_ACTION_FIELD" in result.degradation_reasons

    def test_forbidden_top_field_rejected(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["forbidden_top"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED
        assert "FORBIDDEN_ACTION_FIELD" in result.degradation_reasons


# ── drafts 排除 ──────────────────────────────────────────────────────


class TestDraftsExclusion:
    """生产 loader 不得读取 drafts/。"""

    def test_draft_only_returns_no_data(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["draft_603629"])
        result = query_research_score_snapshot(
            str(root), symbol="603629.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.snapshot is None
        # drafts 存在应被标注。
        assert any("drafts" in w for w in result.validation_warnings)

    def test_draft_ignored_when_formal_exists(self, tmp_path: Path) -> None:
        # 正式快照 + 同标的草案：正式优先，草案不参与。
        root = tmp_path
        formal = _base_snapshot_dict(
            symbol="603629.SH", as_of="2026-07-13", rec=80, tqual=75
        )
        _write_snapshot_file(root, "603629.SH", "formal.json", formal)
        draft = _base_snapshot_dict(
            symbol="603629.SH", as_of="2099-12-31", rec=99, tqual=99
        )
        _write_snapshot_file(
            root, "603629.SH", "draft.json", draft, in_drafts=True
        )
        result = query_research_score_snapshot(
            str(root), symbol="603629.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot.snapshot_id == formal["snapshot_id"]
        # 草案高分 99 没有污染结果。
        assert result.snapshot.scores.research_evidence_confidence == 80

    def test_draft_not_counted_as_candidate(self, tmp_path: Path) -> None:
        # 只有草案 + 一个损坏正式文件 → 正式文件读取失败 → FAILED。
        root = tmp_path
        (root / SNAPSHOTS_DIR_NAME / "603629.SH").mkdir(parents=True)
        (root / SNAPSHOTS_DIR_NAME / "603629.SH" / "broken.json").write_text(
            "not json", encoding="utf-8"
        )
        draft = _base_snapshot_dict(symbol="603629.SH")
        _write_snapshot_file(
            root, "603629.SH", "draft.json", draft, in_drafts=True
        )
        result = query_research_score_snapshot(
            str(root), symbol="603629.SH", analysis_time=_at(2026, 7, 14)
        )
        # [SCORE-001B-R1] 正式文件损坏在磁盘上存在但读取失败 → FAILED。
        assert result.status == STATUS_FAILED


# ── 多版本 / 时序 / 时间穿越 ─────────────────────────────────────────


class TestMultiVersionTimeTravel:
    """同标的多版本严格按 analysis_time 选择，未来快照不穿越。"""

    def test_picks_latest_valid_before_analysis_time(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(
            tmp_path, include=["multi_version_r1", "multi_version_r2"]
        )
        result = query_research_score_snapshot(
            str(root), symbol="600519.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        # r2 (2026-07-10) 比 r1 (2026-06-01) 新，应被选中。
        assert result.snapshot.snapshot_id == "600519.SH-20260710-r2"
        assert result.snapshot.as_of == "2026-07-10"

    def test_older_analysis_time_picks_older_snapshot(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(
            tmp_path, include=["multi_version_r1", "multi_version_r2"]
        )
        # analysis_time 在 r1 与 r2 之间 → 只能选 r1（r2 是未来快照）。
        result = query_research_score_snapshot(
            str(root), symbol="600519.SH", analysis_time=_at(2026, 6, 15)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot.snapshot_id == "600519.SH-20260601-r1"
        assert result.snapshot.as_of == "2026-06-01"

    def test_future_snapshot_not_selected(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["future_snapshot"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        # 唯一候选是 2099 → 不穿越 → NORMAL_NO_DATA。
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.snapshot is None
        assert any("未来" in e for e in result.errors)

    def test_analysis_time_none_uses_now(self, tmp_path: Path) -> None:
        # 不传 analysis_time → 用当前时间；fixture 是 2026-07-13，当前显然更晚。
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(str(root), symbol="605589.SH")
        # 仍可能命中（除非真实当前时间早于 2026-07-13；此时为 NO_DATA，也合法）。
        assert result.status in (STATUS_HAS_DATA, STATUS_NORMAL_NO_DATA)


# ── loader 侧过期安全网 ──────────────────────────────────────────────


class TestLoaderStaleness:
    """as_of 距 analysis_time 过期 → 降级 STALE（安全网）。"""

    def test_stale_after_days_downgrade(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        # analysis_time 距 as_of=2026-07-13 超过 120 天 → STALE。
        result = query_research_score_snapshot(
            str(root),
            symbol="605589.SH",
            analysis_time=_at(2026, 12, 31),
            stale_after_days=DEFAULT_STALE_AFTER_DAYS,
        )
        assert result.status == STATUS_STALE
        assert result.snapshot is not None
        assert any("LOADER_STALE" in r for r in result.degradation_reasons)

    def test_custom_short_stale_window(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        # 只过 10 天，但阈值设为 5 → STALE。
        result = query_research_score_snapshot(
            str(root),
            symbol="605589.SH",
            analysis_time=_at(2026, 7, 23),
            stale_after_days=5,
        )
        assert result.status == STATUS_STALE

    def test_does_not_override_failed_status(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["failed_status"])
        result = query_research_score_snapshot(
            str(root),
            symbol="605589.SH",
            analysis_time=_at(2026, 12, 31),
        )
        # 自身 FAILED 不被 loader 改成 STALE。
        assert result.status == STATUS_FAILED


# ── 安全性：只读 / 幂等 / 路径逃逸 ───────────────────────────────────


class TestReadOnlyAndSafety:
    """provider 不写知识库/DB、不调用 LLM；幂等；路径逃逸 fail closed。"""

    def test_no_files_written_to_kb(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        assert before == after

    def test_idempotent_reads(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        r1 = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        r2 = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert r1.to_dict() == r2.to_dict()

    def test_missing_knowledge_root(self, tmp_path: Path) -> None:
        result = query_research_score_snapshot(
            str(tmp_path / "nope"),
            symbol="605589.SH",
            analysis_time=_at(2026, 7, 14),
        )
        assert result.status == STATUS_FAILED
        assert result.snapshot is None

    def test_missing_snapshots_root(self, tmp_path: Path) -> None:
        result = query_research_score_snapshot(
            str(tmp_path),
            symbol="605589.SH",
            analysis_time=_at(2026, 7, 14),
        )
        assert result.status == STATUS_NORMAL_NO_DATA

    def test_no_symbol_returns_no_data(self, tmp_path: Path) -> None:
        result = query_research_score_snapshot(
            str(tmp_path), symbol="", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_NORMAL_NO_DATA

    def test_unknown_symbol_returns_no_data(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="999999.XX", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.snapshot is None

    @pytest.mark.skipif(
        os.name == "nt", reason="symlink behavior differs on Windows"
    )
    def test_symlink_escape_fail_closed(self, tmp_path: Path) -> None:
        # 在正式目录建一个符号链接指向外部文件 → 解析后不在 snapshots_root 内 → 排除。
        root = tmp_path
        external = tmp_path / "external_secret.json"
        snap = _base_snapshot_dict()
        snap["symbol"] = "605589.SH"
        external.write_text(json.dumps(snap), encoding="utf-8")

        sym_dir = root / SNAPSHOTS_DIR_NAME / "605589.SH"
        sym_dir.mkdir(parents=True)
        link = sym_dir / "link.json"
        try:
            link.symlink_to(external)
        except OSError:
            pytest.skip("cannot create symlink on this environment")
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        # 符号链接逃逸 → 不被选为候选 → NORMAL_NO_DATA。
        assert result.status == STATUS_NORMAL_NO_DATA
        assert result.snapshot is None

    def test_nested_drafts_in_symbol_dir_excluded(self, tmp_path: Path) -> None:
        # <symbol>/drafts/ 下的文件也要排除（路径含 drafts 组件）。
        root = tmp_path
        nested_draft_dir = (
            root / SNAPSHOTS_DIR_NAME / "605589.SH" / DRAFTS_DIR_NAME
        )
        nested_draft_dir.mkdir(parents=True)
        (nested_draft_dir / "hidden.json").write_text(
            json.dumps(_base_snapshot_dict()), encoding="utf-8"
        )
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_NORMAL_NO_DATA


# ── 动作不因高分改变（SCORE-001 安全契约核心）─────────────────────────


class TestActionNonInfluence:
    """知识快照极高分时 decision/execution_action/action_tier 不发生变化。

    SCORE-001 不输出交易动作；result 不携带任何动作字段。
    """

    def test_result_has_no_action_fields(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        payload = result.to_dict()
        for field in FORBIDDEN_ACTION_FIELDS:
            assert field not in payload, f"result 顶层不应含 {field}"
            if payload.get("snapshot"):
                assert field not in payload["snapshot"], (
                    f"snapshot 不应含 {field}"
                )

    def test_extreme_high_score_still_no_action(self, tmp_path: Path) -> None:
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["scores"] = {
            "research_evidence_confidence": 100,
            "thesis_quality": 100,
        }
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        payload = result.to_dict()
        for field in FORBIDDEN_ACTION_FIELDS:
            assert field not in payload

    def test_snapshot_injected_action_field_rejected(self, tmp_path: Path) -> None:
        # 即使快照试图带 execution_action，loader 也 fail closed。
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["execution_action"] = "ENTER"
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_FAILED


# ── 来源等级复用 KB-014 ──────────────────────────────────────────────


class TestSourceTierReuse:
    """不建立第二套 A-E 等级；复用 KB-014 SOURCE_QUALITY_TIERS。"""

    @pytest.mark.parametrize("tier", list(SOURCE_QUALITY_TIERS))
    def test_all_kb014_tiers_accepted(self, tmp_path: Path, tier: str) -> None:
        root = tmp_path
        snap = _base_snapshot_dict()
        snap["evidence_refs"][0]["source_quality_tier"] = tier
        _write_snapshot_file(root, "605589.SH", "s.json", snap)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot.evidence_refs[0].source_quality_tier == tier

    def test_loader_does_not_invent_new_tier(self) -> None:
        # loader 模块本身不应定义新的 tier 常量。
        module_tiers = getattr(rss, "SOURCE_QUALITY_TIERS", None)
        assert module_tiers is None or set(module_tiers) == set(SOURCE_QUALITY_TIERS)


# ── invalidated thesis 合法 ──────────────────────────────────────────


class TestInvalidatedThesis:
    def test_invalidated_thesis_accepted(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["invalidated_thesis"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_LOW_CONFIDENCE
        assert result.snapshot is not None
        assert result.snapshot.theses[0].status == "invalidated"


# ── 序列化 / 渲染 ────────────────────────────────────────────────────


class TestSerializationAndRender:
    def test_result_json_serializable(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        payload = json.dumps(result.to_dict(), ensure_ascii=False)
        assert "605589.SH" in payload

    def test_snapshot_to_dict_roundtrip(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        snap = result.snapshot
        d = snap.to_dict()
        assert d["symbol"] == snap.symbol
        assert d["scores"]["research_evidence_confidence"] == 82

    def test_render_block_has_data(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        block = render_research_score_block(result)
        assert "研究评分快照" in block
        assert "82" in block
        # 渲染区块不得出现强买卖/动作语义词（disclaimer 中的字段名除外）。
        for word in ("买入", "卖出", "加仓", "重仓", "BUY", "SELL", "ENTER", "EXIT"):
            assert word not in block

    def test_render_block_empty_for_no_data(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["draft_603629"])
        result = query_research_score_snapshot(
            str(root), symbol="603629.SH", analysis_time=_at(2026, 7, 14)
        )
        assert render_research_score_block(result) == ""

    def test_render_report(self, tmp_path: Path) -> None:
        root = build_snapshot_fixture_kb(tmp_path, include=["qualified"])
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        report = render_research_score_report(result)
        assert "SCORE-001" in report
        assert "605589.SH" in report

    def test_suggest_output_path(self) -> None:
        p = suggest_query_output_path()
        assert p.endswith(".md")
        assert "research_score_snapshot" in p


# ── 降级到更旧合法候选 ───────────────────────────────────────────────


class TestFallbackToOlderValid:
    """最新候选损坏时，降级尝试更旧合法版本。"""

    def test_newest_corrupt_falls_back_to_older(self, tmp_path: Path) -> None:
        root = tmp_path
        older = _base_snapshot_dict(
            snapshot_id="605589.SH-20260701-r1",
            as_of="2026-07-01",
            rec=60,
            tqual=62,
        )
        _write_snapshot_file(root, "605589.SH", "old.json", older)
        # 写一个损坏的"更新"文件。
        sym_dir = root / SNAPSHOTS_DIR_NAME / "605589.SH"
        (sym_dir / "new.json").write_text("not json", encoding="utf-8")
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot.snapshot_id == "605589.SH-20260701-r1"
        assert any("JSON" in e or "解析" in e for e in result.errors)

    def test_newest_invalid_falls_back_to_older(self, tmp_path: Path) -> None:
        root = tmp_path
        older = _base_snapshot_dict(
            snapshot_id="605589.SH-20260701-r1",
            as_of="2026-07-01",
        )
        _write_snapshot_file(root, "605589.SH", "old.json", older)
        # 写一个带禁止字段的"更新"文件。
        bad = _base_snapshot_dict(
            snapshot_id="605589.SH-20260713-bad", as_of="2026-07-13"
        )
        bad["action"] = "BUY"
        _write_snapshot_file(root, "605589.SH", "new.json", bad)
        result = query_research_score_snapshot(
            str(root), symbol="605589.SH", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot.snapshot_id == "605589.SH-20260701-r1"
        assert "FORBIDDEN_ACTION_FIELD" in result.degradation_reasons


# ── 数据类直接构造 ───────────────────────────────────────────────────


class TestDataclasses:
    def test_score_summary_defaults_none(self) -> None:
        s = ScoreSummary()
        assert s.research_evidence_confidence is None
        assert s.thesis_quality is None

    def test_evidence_ref_default_tier_unknown(self) -> None:
        e = EvidenceRef()
        assert e.source_quality_tier == "unknown"

    def test_thesis_defaults(self) -> None:
        t = InvestmentThesis()
        assert t.status == "active"
        assert t.supporting_evidence_ids == []

    def test_validation_error_carries_reason_code(self) -> None:
        err = SnapshotValidationError("REASON_X", "boom")
        assert err.reason_code == "REASON_X"
        assert "boom" in str(err)


class TestSymbolNormalization:
    def test_lowercase_exchange_suffix_reads_uppercase_snapshot_dir(self, tmp_path: Path) -> None:
        _write_snapshot_file(tmp_path, "605589.SH", "s.json", _base_snapshot_dict())
        result = query_research_score_snapshot(
            str(tmp_path), symbol="605589.sh", analysis_time=_at(2026, 7, 14)
        )
        assert result.status == STATUS_HAS_DATA
        assert result.snapshot.symbol == "605589.SH"
