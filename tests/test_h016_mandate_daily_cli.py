# [H-016] mandate_daily_cli
"""Tests for the H-016 mandate daily CLI: retention + dry-run + fixture write."""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime
from pathlib import Path

import pytest

from tradingagents.tradeflow.mandate_daily_report import (
    MandateDailyReport,
    _parse_mandate_filename,
    build_mandate_daily_report,
    build_mandate_daily_summary,
    purge_old_mandate_daily_reports,
    save_mandate_daily_report,
)


def _fixture_heatmap():
    return {
        "as_of": "2026-06-24",
        "status": "ok",
        "topics": [
            {
                "topic": "低空经济",
                "topic_status": "FERMENTING",
                "topic_status_label": "发酵",
                "heat_trend": "RISING",
                "heat_trend_label": "升温",
                "state_change_label": "升温发酵",
                "state_change_positive": True,
                "is_left_side": True,
                "is_confirmed": False,
                "latest_date": "2026-06-24",
                "peak_heat": 82,
                "windows": {"7": {"evidence_count": 5, "candidate_count": 2}},
                "evidence_summary": "低空经济政策支持",
                "counter_evidence_gaps": ["订单兑现仍需跟踪"],
                "overheat_flags": [],
                "candidates": [
                    {
                        "symbol": "000001.SZ",
                        "name": "示例科技",
                        "company_role": "核心设备",
                        "candidate_type": "POLICY_AMBUSH",
                        "tier": "main",
                        "mandate_score": 88,
                        "latest_date": "2026-06-24",
                    }
                ],
            },
            {
                "topic": "机器人",
                "topic_status": "RECEDING",
                "topic_status_label": "退潮",
                "heat_trend": "COOLING",
                "heat_trend_label": "降温",
                "state_change_label": "退潮",
                "state_change_positive": False,
                "is_left_side": False,
                "is_confirmed": False,
                "latest_date": "2026-06-23",
                "peak_heat": 43,
                "heat_curve": [{"date": "2026-06-23", "candidate_count": 1, "heat": 43}],
                "windows": {"7": {"evidence_count": 1, "candidate_count": 0}},
                "counter_evidence_gaps": ["资金承接不足"],
                "overheat_flags": ["短期过热"],
                "candidates": [],
            },
        ],
    }


def _seed_dir(tree: dict[str, str]) -> Path:
    """Materialise a ``{filename: content}`` mapping onto a temp dir."""
    base = tree.pop("__BASE__", None)
    assert base is not None, "caller must pass __BASE__"
    for name, payload in tree.items():
        full = base / name
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(payload, encoding="utf-8")
    return base


# ---------------------------------------------------------------------------
# Retention helper
# ---------------------------------------------------------------------------


def test_parse_mandate_filename_accepts_canonical_pattern():
    assert _parse_mandate_filename("mandate-2026-06-24.md") == ("2026-06-24", "md")
    assert _parse_mandate_filename("mandate-2026-06-24.json") == ("2026-06-24", "json")


def test_parse_mandate_filename_rejects_other_files():
    assert _parse_mandate_filename("README.md") is None
    assert _parse_mandate_filename("mandate-2026-06-24.txt") is None
    assert _parse_mandate_filename("mandate-20260624.md") is None
    assert _parse_mandate_filename("mandate-2026-13-40.md") is None  # invalid date
    assert _parse_mandate_filename("topic_heatmap-2026-06-24.json") is None


def test_purge_only_deletes_old_mandate_files_and_keeps_siblings(tmp_path):
    base = _seed_dir(
        {
            "__BASE__": tmp_path,
            "mandate-2026-03-01.md": "old md",
            "mandate-2026-03-01.json": "{}",
            "mandate-2026-06-24.md": "fresh md",
            "mandate-2026-06-24.json": "{}",
            "README.md": "do not touch",
            "notes-2026-01-01.md": "sidecar",
        }
    )

    purge_log = purge_old_mandate_daily_reports(
        str(base),
        retention_days=90,
        as_of="2026-06-24",
    )

    # Old mandate pair was purged, fresh pair kept, non-mandate files skipped.
    assert sorted(purge_log["deleted"]) == [
        "mandate-2026-03-01.json",
        "mandate-2026-03-01.md",
    ]
    assert sorted(purge_log["kept"]) == [
        "mandate-2026-06-24.json",
        "mandate-2026-06-24.md",
    ]
    assert sorted(purge_log["skipped"]) == ["README.md", "notes-2026-01-01.md"]

    # Sibling files physically remain on disk.
    assert (base / "README.md").exists()
    assert (base / "notes-2026-01-01.md").exists()
    # Old pair gone, fresh pair still present.
    assert not (base / "mandate-2026-03-01.md").exists()
    assert (base / "mandate-2026-06-24.md").exists()


def test_purge_retention_zero_is_noop(tmp_path):
    base = _seed_dir(
        {
            "__BASE__": tmp_path,
            "mandate-2025-01-01.md": "ancient",
        }
    )
    purge_log = purge_old_mandate_daily_reports(str(base), retention_days=0)
    assert purge_log == {"deleted": [], "kept": [], "skipped": []}
    assert (base / "mandate-2025-01-01.md").exists()


def test_purge_missing_dir_is_safe():
    purge_log = purge_old_mandate_daily_reports(
        "/nonexistent/path/does-not-exist",
        retention_days=30,
    )
    assert purge_log == {"deleted": [], "kept": [], "skipped": []}


# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------


def test_build_mandate_daily_summary_is_auditable_and_has_no_trade_words():
    report = build_mandate_daily_report(_fixture_heatmap())
    summary = build_mandate_daily_summary(
        report, source="dry_run", status="ok"
    )

    assert "as_of=2026-06-24" in summary
    assert "source=dry_run" in summary
    assert "status=ok" in summary
    assert "rising=1" in summary
    assert "cooling=1" in summary
    assert "main=1" in summary
    for forbidden in ["立即清仓", "重仓买入", "满仓", "梭哈", "买入", "卖出"]:
        assert forbidden not in summary


def test_build_mandate_daily_summary_handles_empty_report():
    empty = MandateDailyReport(as_of="2026-06-24")
    summary = build_mandate_daily_summary(empty)
    assert "rising=0" in summary
    assert "cooling=0" in summary
    assert "main=0" in summary


# ---------------------------------------------------------------------------
# End-to-end CLI behaviour (fixture -> md/json, dry-run, retention)
# ---------------------------------------------------------------------------


def _load_cli_module():
    """Import the CLI script as a module without invoking ``__main__``."""
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "run_mandate_daily_report.py"
    spec = importlib.util.spec_from_file_location(
        "run_mandate_daily_report", script_path
    )
    assert spec and spec.loader, "unable to build spec for CLI module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_dry_run_does_not_write_files(tmp_path, monkeypatch, capsys):
    cli = _load_cli_module()

    # Patch the heatmap loader so we do NOT touch tradeflow.db / prod DB.
    def _fake_load(as_of, window_days, tf_db_path):
        return _fixture_heatmap()

    monkeypatch.setattr(cli, "_load_topic_heatmap", _fake_load)

    out_dir = tmp_path / "reports"
    rc = cli._cli(
        [
            "--as-of",
            "2026-06-24",
            "--output-dir",
            str(out_dir),
            "--dry-run",
        ]
    )

    assert rc == 0
    assert not out_dir.exists() or not any(out_dir.iterdir())
    captured = capsys.readouterr()
    assert "source=dry_run" in captured.out
    assert "as_of=2026-06-24" in captured.out


def test_cli_writes_md_json_and_purges_old(tmp_path, monkeypatch, capsys):
    cli = _load_cli_module()

    # Pre-seed an old report pair + a sidecar file that must survive.
    _seed_dir(
        {
            "__BASE__": tmp_path,
            "mandate-2025-01-01.md": "ancient md",
            "mandate-2025-01-01.json": "{}",
            "README.md": "keep me",
        }
    )

    def _fake_load(as_of, window_days, tf_db_path):
        return _fixture_heatmap()

    monkeypatch.setattr(cli, "_load_topic_heatmap", _fake_load)

    rc = cli._cli(
        [
            "--as-of",
            "2026-06-24",
            "--output-dir",
            str(tmp_path),
            "--retention-days",
            "90",
        ]
    )

    assert rc == 0
    # Fresh report pair materialised.
    assert (tmp_path / "mandate-2026-06-24.md").exists()
    assert (tmp_path / "mandate-2026-06-24.json").exists()
    # Old pair purged.
    assert not (tmp_path / "mandate-2025-01-01.md").exists()
    assert not (tmp_path / "mandate-2025-01-01.json").exists()
    # Sidecar survived.
    assert (tmp_path / "README.md").exists()

    captured = capsys.readouterr()
    assert "source=generated_file" in captured.out
    assert "deleted=2" in captured.out
    assert "kept=2" in captured.out  # the freshly written md+json


def test_cli_no_data_heatmap_does_not_crash(tmp_path, monkeypatch):
    cli = _load_cli_module()

    def _fake_load(as_of, window_days, tf_db_path):
        return {"status": "no_data", "as_of": as_of or "2026-06-24", "topics": []}

    monkeypatch.setattr(cli, "_load_topic_heatmap", _fake_load)

    rc = cli._cli(
        [
            "--as-of",
            "2026-06-24",
            "--output-dir",
            str(tmp_path),
            "--dry-run",
        ]
    )

    assert rc == 0
    # Dry-run must not have written anything even on no_data.
    assert not any(p.name.startswith("mandate-") for p in tmp_path.iterdir())


def test_cli_heatmap_loader_is_genuinely_read_only(tmp_path):
    """[H-016] regression: pointing the CLI at a DB must never mutate it.

    Codex round-1 found that ``--dry-run`` / the read-only report path could
    still call ``candidate_engine.init_db()`` (CREATE/ALTER TABLE) on
    ``tradeflow.db``. This test builds an empty SQLite file (no
    ``tradeflow_candidates`` table), runs the real heatmap loader against it,
    and asserts the loader degrades to ``no_data`` WITHOUT creating any
    tables — i.e. ``init_db()`` was never called.
    """
    import sqlite3

    cli = _load_cli_module()

    empty_db = tmp_path / "tradeflow.db"
    # Touch an empty SQLite DB (header only, zero tables).
    sqlite3.connect(str(empty_db)).close()
    assert empty_db.exists()

    heatmap = cli._load_topic_heatmap("", window_days=60, tf_db_path=str(empty_db))

    # Read-only loader must degrade gracefully to no_data.
    assert heatmap["status"] == "no_data"

    # Crucially: no tradeflow tables were created (init_db never ran).
    conn = sqlite3.connect(str(empty_db))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "tradeflow_candidates" not in tables
    assert tables == set()  # the empty DB is still completely empty


def test_get_topic_heatmap_read_only_preserves_populated_db(tmp_path):
    """[H-016] read-only mode must not ALTER an existing DB either.

    Seed a DB whose ``tradeflow_candidates`` table is deliberately missing the
    newer ``effective_trade_date`` column (a stale schema). In read-only mode
    the heatmap loader must NOT run init_db's ALTER TABLE migration; the
    column set on disk has to stay exactly as seeded.
    """
    import sqlite3

    from api.services.tradeflow_service import get_topic_heatmap

    stale_db = tmp_path / "stale.db"
    conn = sqlite3.connect(str(stale_db))
    try:
        # Minimal stale schema: trade_date only, no effective_trade_date,
        # none of the newer mandate columns. init_db() would ALTER these in.
        conn.execute(
            "CREATE TABLE tradeflow_candidates ("
            "symbol TEXT, name TEXT, trade_date TEXT, mandate_topic TEXT)"
        )
        conn.execute(
            "INSERT INTO tradeflow_candidates (symbol, name, trade_date, mandate_topic) "
            "VALUES ('000001.SZ', '示例', '2026-06-24', '低空经济')"
        )
        conn.commit()
    finally:
        conn.close()

    cols_before = _db_columns(stale_db)

    heatmap = get_topic_heatmap(
        as_of="2026-06-24",
        window_days=60,
        tf_db_path=str(stale_db),
        read_only=True,
    )
    # It reads what it can (status ok) without mutating the schema.
    assert heatmap["status"] == "ok"

    cols_after = _db_columns(stale_db)
    # No ALTER TABLE migration happened on disk.
    assert cols_before == cols_after
    # The deliberately-absent column was NOT added in read-only mode.
    assert "effective_trade_date" not in cols_after


def _db_columns(db_path) -> set:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return {
            row[1]
            for row in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()
        }
    finally:
        conn.close()


def test_fixture_generates_md_and_json_round_trip(tmp_path):
    """Acceptance: a fixture heatmap can produce md+json and survive a reload."""
    from tradingagents.tradeflow.mandate_daily_report import (
        find_latest_mandate_daily_report,
        load_latest_mandate_daily_report,
    )

    report = build_mandate_daily_report(_fixture_heatmap())
    md_path, json_path = save_mandate_daily_report(report, output_dir=str(tmp_path))

    assert md_path.endswith("mandate-2026-06-24.md")
    assert json_path.endswith("mandate-2026-06-24.json")
    assert "不构成交易建议" in Path(md_path).read_text(encoding="utf-8")

    latest = load_latest_mandate_daily_report(str(tmp_path))
    assert latest is not None
    assert latest["as_of"] == "2026-06-24"
    assert find_latest_mandate_daily_report(str(tmp_path)).endswith(
        "mandate-2026-06-24.json"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
