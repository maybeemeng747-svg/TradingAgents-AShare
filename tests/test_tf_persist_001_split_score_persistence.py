# [TF-PERSIST-001] split_score_persistence
"""Persistence tests for TradeFlow save_candidate split-score fields.

Regression: V-008 found that ``save_candidate()`` wrote candidates without
persisting the 8 ``[TF-QUALITY-002] score_separation`` fields
(``technical_score`` / ``policy_score`` / ``fund_flow_score`` / ``event_score``
/ ``risk_penalty_score`` / ``data_quality_score`` / ``ranking_reasons`` /
``weakness_reasons``). The DB had no columns for them and ``save_candidate``'s
INSERT/UPDATE omitted them, so the API returned the ``_rget`` default (0.0 /
empty list) for candidates saved via the direct ``save_candidate`` path.

These tests pin the round-trip: construct a ``Candidate`` directly, persist
with ``save_candidate``, then read via DB row, ``Candidate.from_db_row`` and
``get_candidates`` / ``get_candidates_tiered`` API service — all four paths
must return the original values.
"""

import json
import os
import sqlite3
import tempfile

import pytest

from tradingagents.tradeflow.candidate_engine import init_db, save_candidate
from tradingagents.tradeflow.schemas import Candidate


SPLIT_FIELDS = (
    "technical_score",
    "policy_score",
    "fund_flow_score",
    "event_score",
    "risk_penalty_score",
    "data_quality_score",
)


def _make_candidate(symbol: str = "300034.SZ") -> Candidate:
    return Candidate(
        symbol=symbol,
        name="钢研高纳",
        source="manual",
        trade_date="2026-06-25",
        tier="A",
        composite_score=75.0,
        score=72.0,
        candidate_type="POLICY_AMBUSH",
        technical_score=55.0,
        policy_score=80.0,
        fund_flow_score=15.0,
        event_score=10.0,
        risk_penalty_score=2.5,
        data_quality_score=82.0,
        ranking_reasons=["政策连续性强", "受益路径明确", "放量突破"],
        weakness_reasons=["估值偏高", "短期涨幅过大"],
    )


@pytest.fixture
def tf_db(tmp_path):
    db_path = str(tmp_path / "tf_persist_001.db")
    init_db(db_path)
    return db_path


# ── DB schema migration ──────────────────────────────────────────


class TestSchemaMigration:
    def test_split_score_columns_exist_after_init_db(self, tf_db):
        """[TF-PERSIST-001] init_db() must add the 8 split-score columns."""
        conn = sqlite3.connect(tf_db)
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()
        }
        conn.close()
        for col in SPLIT_FIELDS:
            assert col in cols, f"missing REAL column {col}"
        assert "ranking_reasons_json" in cols
        assert "weakness_reasons_json" in cols

    def test_old_db_without_columns_auto_migrates(self, tmp_path):
        """[TF-PERSIST-001] a pre-existing DB created before this fix must get
        the 8 columns added on the next init_db() call (no manual migration).
        """
        db_path = str(tmp_path / "tf_persist_001_legacy.db")
        # Simulate a legacy DB: only the original CREATE_CANDIDATES_TABLE columns
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE tradeflow_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT DEFAULT '',
                source TEXT DEFAULT 'manual',
                strategy_tags_json TEXT DEFAULT '[]',
                primary_strategy TEXT DEFAULT '',
                score REAL DEFAULT 0.0,
                status TEXT DEFAULT 'active',
                trigger_price REAL,
                support_price REAL,
                invalid_price REAL,
                need_deep_ta INTEGER DEFAULT 0,
                evidence_json TEXT DEFAULT '{}',
                risk_flags_json TEXT DEFAULT '[]',
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(trade_date, symbol)
            );
            """
        )
        conn.commit()
        conn.close()

        # Re-init — must auto-add the columns
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()
        }
        conn.close()
        for col in SPLIT_FIELDS + ("ranking_reasons_json", "weakness_reasons_json"):
            assert col in cols, f"legacy migration missing {col}"


# ── save_candidate round-trip ────────────────────────────────────


class TestSaveCandidateRoundTrip:
    def test_db_row_holds_split_scores(self, tf_db):
        """Raw DB row contains the persisted split-score values."""
        c = _make_candidate()
        save_candidate(c, tf_db)

        conn = sqlite3.connect(tf_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM tradeflow_candidates WHERE symbol='300034.SZ'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["technical_score"] == 55.0
        assert row["policy_score"] == 80.0
        assert row["fund_flow_score"] == 15.0
        assert row["event_score"] == 10.0
        assert row["risk_penalty_score"] == 2.5
        assert row["data_quality_score"] == 82.0
        assert json.loads(row["ranking_reasons_json"]) == [
            "政策连续性强",
            "受益路径明确",
            "放量突破",
        ]
        assert json.loads(row["weakness_reasons_json"]) == [
            "估值偏高",
            "短期涨幅过大",
        ]

    def test_from_db_row_restores_split_scores(self, tf_db):
        """Candidate.from_db_row restores all 8 fields (no default fallback)."""
        c = _make_candidate()
        save_candidate(c, tf_db)

        conn = sqlite3.connect(tf_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM tradeflow_candidates WHERE symbol='300034.SZ'"
        ).fetchone()
        conn.close()

        restored = Candidate.from_db_row(dict(row))
        assert restored.technical_score == 55.0
        assert restored.policy_score == 80.0
        assert restored.fund_flow_score == 15.0
        assert restored.event_score == 10.0
        assert restored.risk_penalty_score == 2.5
        assert restored.data_quality_score == 82.0
        assert restored.ranking_reasons == ["政策连续性强", "受益路径明确", "放量突破"]
        assert restored.weakness_reasons == ["估值偏高", "短期涨幅过大"]

    def test_upsert_updates_split_scores(self, tf_db):
        """A second save_candidate with new split-score values must replace
        the old ones (ON CONFLICT DO UPDATE covers the 8 fields)."""
        save_candidate(_make_candidate(), tf_db)

        c2 = _make_candidate()
        c2.technical_score = 90.0
        c2.policy_score = 95.0
        c2.ranking_reasons = ["新版原因"]
        c2.weakness_reasons = ["新版弱点"]
        save_candidate(c2, tf_db)

        conn = sqlite3.connect(tf_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM tradeflow_candidates WHERE symbol='300034.SZ'"
        ).fetchone()
        conn.close()

        assert row["technical_score"] == 90.0
        assert row["policy_score"] == 95.0
        assert json.loads(row["ranking_reasons_json"]) == ["新版原因"]
        assert json.loads(row["weakness_reasons_json"]) == ["新版弱点"]


# ── API service layer ────────────────────────────────────────────


class TestApiServiceReturnsPersistedSplitScores:
    """get_candidates / get_candidates_tiered must surface the persisted
    values rather than the ``_rget`` default of 0.0 / empty list."""

    def test_get_candidates_returns_persisted_values(self, tf_db):
        save_candidate(_make_candidate(), tf_db)

        from api.services.tradeflow_service import get_candidates

        result = get_candidates("2026-06-25", tf_db_path=tf_db)
        assert result["status"] == "ok"
        assert len(result["candidates"]) >= 1
        cand = next(c for c in result["candidates"] if c["symbol"] == "300034.SZ")
        assert cand["technical_score"] == 55.0
        assert cand["policy_score"] == 80.0
        assert cand["fund_flow_score"] == 15.0
        assert cand["event_score"] == 10.0
        assert cand["risk_penalty_score"] == 2.5
        assert cand["data_quality_score"] == 82.0
        assert cand["ranking_reasons"] == ["政策连续性强", "受益路径明确", "放量突破"]
        assert cand["weakness_reasons"] == ["估值偏高", "短期涨幅过大"]

    def test_get_candidates_tiered_returns_persisted_values(self, tf_db):
        save_candidate(_make_candidate(), tf_db)

        from api.services.tradeflow_service import get_candidates_tiered

        result = get_candidates_tiered("2026-06-25", tf_db_path=tf_db)
        assert result["status"] == "ok"
        all_items = result["main_candidates"] + result["observation_candidates"]
        cand = next(c for c in all_items if c["symbol"] == "300034.SZ")
        assert cand["technical_score"] == 55.0
        assert cand["policy_score"] == 80.0
        assert cand["ranking_reasons"] == ["政策连续性强", "受益路径明确", "放量突破"]
        assert cand["weakness_reasons"] == ["估值偏高", "短期涨幅过大"]

    def test_candidate_detail_returns_persisted_values(self, tf_db):
        save_candidate(_make_candidate(), tf_db)

        from api.services.tradeflow_service import get_candidate_detail

        result = get_candidate_detail("300034.SZ", "2026-06-25", tf_db_path=tf_db)
        assert result["status"] == "ok"
        cand = result["candidate"]
        assert cand["technical_score"] == 55.0
        assert cand["policy_score"] == 80.0
        assert cand["data_quality_score"] == 82.0
        assert cand["ranking_reasons"] == ["政策连续性强", "受益路径明确", "放量突破"]
        assert cand["weakness_reasons"] == ["估值偏高", "短期涨幅过大"]
