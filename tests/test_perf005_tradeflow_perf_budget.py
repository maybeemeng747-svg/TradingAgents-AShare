# [PERF-005] tradeflow_perf_budget
"""Lightweight performance budget regression smoke for TradeFlow.

Validates:
1. Candidates / observe / review / data-health / topic-heatmap endpoints
   respond within a generous fast-tier budget on both empty DB and a small
   fixture DB.
2. Endpoints consistently inject `runtime_tier_meta` set to FAST_RADAR with
   the matching `expected_latency` budget string.
3. No LLM is allowed (`llm_allowed` is False) — no live model calls.
4. Over-budget runs emit a structured suggestion instead of hard-failing,
   unless the regression is obviously blocking (>= HARD_FAIL_MULT x budget).
5. The test never triggers real data fetching or full-market scan — all
   paths go through fixture DBs or non-existent paths.

Budgets are deliberately loose so the test is not flaky on slow CI; they
exist to catch obvious 5-10x regressions, not to enforce absolute timing
(see PERF-005 acceptance criteria).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

# ── Module under test ──
from api.runtime_tier import (
    RuntimeTier,
    RuntimeTierSpec,
    get_tier_spec,
    tier_to_meta,
    tradeflow_endpoint_tier,
    tradeflow_meta,
)
from api.services.tradeflow_service import (
    get_candidates,
    get_candidate_detail,
    get_data_health,
    get_observe,
    get_review,
    get_topic_heatmap,
)


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Budget tiers
# ═══════════════════════════════════════════════════════════════════
# Generous budgets (seconds). FAST_RADAR spec is "5-30s", so even the
# fixture budget is comfortably under the lower bound. Anything beyond
# HARD_FAIL_MULT x budget is treated as an obvious regression.
BUDGET_EMPTY_DB: float = 1.0
BUDGET_WITH_FIXTURE: float = 3.0
HARD_FAIL_MULT: int = 5

# Suggestions accumulated across the run; surfaced at the end of the
# module so the nightly chain still passes unless something is obviously
# broken (a >= HARD_FAIL_MULT x regression hard-fails the test).
_SUGGESTIONS: List[str] = []


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Budget assertion helper
# ═══════════════════════════════════════════════════════════════════
def _check_budget(
    endpoint: str,
    elapsed: float,
    budget: float,
    scenario: str,
) -> None:
    """Emit suggestion if over budget; hard-fail only on obvious regression.

    Matches PERF-005: "超预算时输出建议，不直接失败夜间主链，除非明显阻塞".
    A regression of >= HARD_FAIL_MULT x budget is considered obviously
    blocking and fails the test.
    """
    if elapsed <= budget:
        return

    over_ratio = elapsed / budget
    suggestion = (
        f"[PERF-005] {endpoint} ({scenario}) elapsed={elapsed:.3f}s "
        f"exceeds budget={budget:.1f}s (x{over_ratio:.1f}); "
        f"consider checking new heavy queries, missing index, or "
        f"accidental live data fetch."
    )
    _SUGGESTIONS.append(suggestion)

    if over_ratio >= HARD_FAIL_MULT:
        raise AssertionError(
            f"[PERF-005] OBVIOUS REGRESSION: {suggestion} "
            f"(>= {HARD_FAIL_MULT}x budget — treated as blocking)."
        )


def _time_call(fn: Callable[[], dict]) -> Tuple[dict, float]:
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    return result, elapsed


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Fixture trade date
# ═══════════════════════════════════════════════════════════════════
# IMPORTANT: must NOT equal today. get_observe() auto-runs the observe
# runner (which fetches live realtime quotes) only when trade_date ==
# datetime.now(). Using a fixed past date keeps the test hermetic and
# honours the PERF-005 constraint of "no real data/model calls".
FIXTURE_TRADE_DATE = "2026-06-02"


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Fixture DB builders
# ═══════════════════════════════════════════════════════════════════
def _create_empty_db(tmp_path: str) -> str:
    """Create an empty TradeFlow DB with the full schema but no rows."""
    db_path = os.path.join(tmp_path, "tradeflow_empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tradeflow_daily_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            mode TEXT DEFAULT 'pre_market',
            summary TEXT DEFAULT '',
            candidates_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT '',
            plan_date TEXT DEFAULT '',
            effective_trade_date TEXT DEFAULT '',
            observe_date TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tradeflow_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            tier TEXT DEFAULT '',
            composite_score REAL DEFAULT 0.0,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            plan_date TEXT DEFAULT '',
            effective_trade_date TEXT DEFAULT '',
            observe_date TEXT DEFAULT '',
            status TEXT DEFAULT 'active'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tradeflow_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal_type TEXT DEFAULT '',
            signal_time TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '{}'
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _create_populated_db(tmp_path: str, trade_date: str = FIXTURE_TRADE_DATE) -> str:
    """Create a TradeFlow DB with one daily plan and 3 candidates."""
    db_path = os.path.join(tmp_path, "tradeflow_populated.db")
    conn = sqlite3.connect(db_path)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tradeflow_daily_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            mode TEXT DEFAULT 'pre_market',
            summary TEXT DEFAULT '',
            candidates_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT '',
            plan_date TEXT DEFAULT '',
            effective_trade_date TEXT DEFAULT '',
            observe_date TEXT DEFAULT ''
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tradeflow_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            tier TEXT DEFAULT '',
            composite_score REAL DEFAULT 0.0,
            score REAL DEFAULT 0.0,
            strategy_tags_json TEXT DEFAULT '[]',
            primary_strategy TEXT DEFAULT '',
            trigger_price REAL,
            support_price REAL,
            invalid_price REAL,
            need_deep_ta INTEGER DEFAULT 0,
            observe_state TEXT DEFAULT 'WAITING',
            observe_trigger_count INTEGER DEFAULT 0,
            observe_first_trigger_time TEXT DEFAULT '',
            tradeflow_data_completeness REAL DEFAULT 0.0,
            missing_data_fields_json TEXT DEFAULT '[]',
            data_completeness REAL DEFAULT 0.0,
            missing_evidence_json TEXT DEFAULT '[]',
            game_balance TEXT DEFAULT '',
            bull_case TEXT DEFAULT '',
            bear_case TEXT DEFAULT '',
            policy_case TEXT DEFAULT '',
            fund_flow_case TEXT DEFAULT '',
            why_deep_ta TEXT DEFAULT '',
            why_not_deep_ta TEXT DEFAULT '',
            risk_flags_json TEXT DEFAULT '[]',
            policy_tags_json TEXT DEFAULT '[]',
            candidate_type TEXT DEFAULT '',
            mandate_score_component REAL DEFAULT 0.0,
            ambush_score REAL DEFAULT 0.0,
            mandate_topic TEXT DEFAULT '',
            company_role TEXT DEFAULT '',
            plan_date TEXT DEFAULT '',
            effective_trade_date TEXT DEFAULT '',
            observe_date TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '{}',
            policy_evidence_refs_json TEXT DEFAULT '[]'
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tradeflow_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal_type TEXT DEFAULT '',
            signal_time TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '{}'
        )
        """
    )

    candidates_json = json.dumps(
        [
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "tier": "A",
                "composite_score": 85.0,
                "strategy_tags": ["VCP"],
                "trigger_price": 1700.0,
                "invalid_price": 1600.0,
                "candidate_type": "TECH_TRADE",
            },
            {
                "symbol": "601689.SH",
                "name": "拓普集团",
                "tier": "B",
                "composite_score": 72.0,
                "strategy_tags": ["POLICY_AMBUSH"],
                "trigger_price": 50.0,
                "invalid_price": 45.0,
                "candidate_type": "POLICY_AMBUSH",
                "mandate_topic": "新能源车",
            },
            {
                "symbol": "000333.SZ",
                "name": "美的集团",
                "tier": "B",
                "composite_score": 68.0,
                "strategy_tags": ["PULLBACK_SUPPORT"],
                "trigger_price": 70.0,
                "invalid_price": 65.0,
                "candidate_type": "TECH_TRADE",
            },
        ],
        ensure_ascii=False,
    )

    conn.execute(
        """
        INSERT INTO tradeflow_daily_plans
            (trade_date, mode, summary, candidates_json, metadata_json,
             created_at, plan_date, effective_trade_date, observe_date)
        VALUES (?, 'pre_market', ?, ?, '{}', ?, ?, ?, ?)
        """,
        (
            trade_date,
            "fixture plan for PERF-005",
            candidates_json,
            f"{trade_date}T09:00:00",
            trade_date,
            trade_date,
            trade_date,
        ),
    )

    # Also insert candidate rows directly so candidate / observe / heatmap
    # paths return data rather than no_data.
    for sym, name, tier, score, c_type, topic in [
        ("600519.SH", "贵州茅台", "A", 85.0, "TECH_TRADE", ""),
        ("601689.SH", "拓普集团", "B", 72.0, "POLICY_AMBUSH", "新能源车"),
        ("000333.SZ", "美的集团", "B", 68.0, "TECH_TRADE", ""),
    ]:
        conn.execute(
            """
            INSERT INTO tradeflow_candidates
                (trade_date, symbol, name, tier, composite_score,
                 candidate_type, mandate_topic, status, created_at, updated_at,
                 plan_date, effective_trade_date, observe_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                trade_date, sym, name, tier, score,
                c_type, topic,
                f"{trade_date}T09:00:00", f"{trade_date}T09:00:00",
                trade_date, trade_date, trade_date,
            ),
        )

    conn.commit()
    conn.close()
    return db_path


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Tier-meta consistency
# ═══════════════════════════════════════════════════════════════════
class TestEndpointTierConsistency:
    """All five PERF-005 endpoints must declare FAST_RADAR tier."""

    ENDPOINTS = [
        "tradeflow_candidates",
        "tradeflow_observe",
        "tradeflow_review",
        "tradeflow_data_health",
        "tradeflow_topic_heatmap",
    ]

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_endpoint_is_fast_radar(self, endpoint: str):
        assert tradeflow_endpoint_tier(endpoint) == RuntimeTier.FAST_RADAR

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_endpoint_meta_no_llm(self, endpoint: str):
        meta = tradeflow_meta(endpoint)
        assert meta["runtime_tier"] == "FAST_RADAR"
        assert meta["llm_allowed"] is False
        assert meta["requires_confirmation"] is False

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_endpoint_meta_latency_budget(self, endpoint: str):
        """The advertised `expected_latency` must match the FAST_RADAR spec."""
        meta = tradeflow_meta(endpoint)
        spec = get_tier_spec(RuntimeTier.FAST_RADAR)
        assert meta["expected_latency"] == spec.expected_latency
        # FAST_RADAR advertises "5-30s" — a tight upper bound we hold the
        # implementation to.
        assert meta["expected_latency"] == "5-30s"

    def test_fast_radar_cost_risk_none(self):
        meta = tradeflow_meta("tradeflow_candidates")
        assert meta["cost_risk"] == "none"

    def test_fast_radar_tier_label(self):
        meta = tradeflow_meta("tradeflow_data_health")
        assert meta["tier_label"] == "快速筛选"


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Smoke perf — empty DB (no_data fast paths)
# ═══════════════════════════════════════════════════════════════════
class TestEmptyDBSmokeBudget:
    """No-data paths must return FAST_RADAR meta and stay well under budget."""

    def test_candidates_empty_db(self, tmp_path):
        db_path = _create_empty_db(str(tmp_path))
        result, elapsed = _time_call(
            lambda: get_candidates(FIXTURE_TRADE_DATE, tf_db_path=db_path)
        )
        assert "runtime_tier_meta" in result
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget("tradeflow_candidates", elapsed, BUDGET_EMPTY_DB, "empty_db")

    def test_observe_empty_db(self, tmp_path):
        db_path = _create_empty_db(str(tmp_path))
        result, elapsed = _time_call(
            lambda: get_observe(FIXTURE_TRADE_DATE, tf_db_path=db_path)
        )
        assert "runtime_tier_meta" in result
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget("tradeflow_observe", elapsed, BUDGET_EMPTY_DB, "empty_db")

    def test_review_empty_db(self, tmp_path):
        db_path = _create_empty_db(str(tmp_path))
        result, elapsed = _time_call(
            lambda: get_review(FIXTURE_TRADE_DATE, tf_db_path=db_path)
        )
        assert "runtime_tier_meta" in result
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        # Review on empty DB is allowed to return either ok (with empty
        # results) or no_data — both are valid fast responses.
        assert result["status"] in {"ok", "no_data"}
        _check_budget("tradeflow_review", elapsed, BUDGET_EMPTY_DB, "empty_db")

    def test_data_health_empty_db(self, tmp_path):
        db_path = _create_empty_db(str(tmp_path))
        result, elapsed = _time_call(lambda: get_data_health(tf_db_path=db_path))
        assert "runtime_tier_meta" in result
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget("tradeflow_data_health", elapsed, BUDGET_EMPTY_DB, "empty_db")

    def test_topic_heatmap_empty_db(self, tmp_path):
        db_path = _create_empty_db(str(tmp_path))
        result, elapsed = _time_call(
            lambda: get_topic_heatmap(tf_db_path=db_path)
        )
        assert "runtime_tier_meta" in result
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget(
            "tradeflow_topic_heatmap", elapsed, BUDGET_EMPTY_DB, "empty_db"
        )

    def test_nonexistent_db_path_returns_fast(self):
        """A non-existent DB path must not blow up or stall the request."""
        result, elapsed = _time_call(
            lambda: get_candidates(
                FIXTURE_TRADE_DATE, tf_db_path="/nonexistent/path/perf005.db"
            )
        )
        assert result.get("status") == "no_data"
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget(
            "tradeflow_candidates", elapsed, BUDGET_EMPTY_DB, "nonexistent_db"
        )


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Smoke perf — populated fixture DB
# ═══════════════════════════════════════════════════════════════════
class TestPopulatedDBSmokeBudget:
    """Small fixture DB must stay well under the FAST_RADAR lower bound."""

    TRADE_DATE = FIXTURE_TRADE_DATE

    def test_candidates_populated(self, tmp_path):
        db_path = _create_populated_db(str(tmp_path), self.TRADE_DATE)
        result, elapsed = _time_call(
            lambda: get_candidates(self.TRADE_DATE, tf_db_path=db_path)
        )
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        # Populated plan path returns "ok".
        assert result["status"] == "ok"
        _check_budget(
            "tradeflow_candidates", elapsed, BUDGET_WITH_FIXTURE, "populated_db"
        )

    def test_observe_populated(self, tmp_path):
        db_path = _create_populated_db(str(tmp_path), self.TRADE_DATE)
        result, elapsed = _time_call(
            lambda: get_observe(self.TRADE_DATE, tf_db_path=db_path)
        )
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget(
            "tradeflow_observe", elapsed, BUDGET_WITH_FIXTURE, "populated_db"
        )

    def test_review_populated(self, tmp_path):
        db_path = _create_populated_db(str(tmp_path), self.TRADE_DATE)
        result, elapsed = _time_call(
            lambda: get_review(self.TRADE_DATE, tf_db_path=db_path)
        )
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        assert result["status"] == "ok"
        _check_budget(
            "tradeflow_review", elapsed, BUDGET_WITH_FIXTURE, "populated_db"
        )

    def test_data_health_populated(self, tmp_path):
        db_path = _create_populated_db(str(tmp_path), self.TRADE_DATE)
        result, elapsed = _time_call(lambda: get_data_health(tf_db_path=db_path))
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget(
            "tradeflow_data_health", elapsed, BUDGET_WITH_FIXTURE, "populated_db"
        )

    def test_topic_heatmap_populated(self, tmp_path):
        db_path = _create_populated_db(str(tmp_path), self.TRADE_DATE)
        result, elapsed = _time_call(
            lambda: get_topic_heatmap(tf_db_path=db_path)
        )
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget(
            "tradeflow_topic_heatmap", elapsed, BUDGET_WITH_FIXTURE, "populated_db"
        )

    def test_candidate_detail_populated(self, tmp_path):
        db_path = _create_populated_db(str(tmp_path), self.TRADE_DATE)
        result, elapsed = _time_call(
            lambda: get_candidate_detail(
                "600519.SH", self.TRADE_DATE, tf_db_path=db_path
            )
        )
        assert "runtime_tier_meta" in result
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget(
            "tradeflow_candidate_detail",
            elapsed,
            BUDGET_WITH_FIXTURE,
            "populated_db",
        )

    def test_observe_populated_skips_live_data_call(self, tmp_path):
        """Safety: observe must not auto-run live quote fetching when the
        fixture trade_date is in the past. Guards the PERF-005
        "no real data/model calls" constraint against future regressions
        in the auto-run trigger logic.
        """
        from datetime import datetime

        db_path = _create_populated_db(str(tmp_path), self.TRADE_DATE)
        # FIXTURE_TRADE_DATE is 2026-06-02; today is later, so auto-run
        # should be skipped.
        assert self.TRADE_DATE != datetime.now().strftime("%Y-%m-%d")
        result, elapsed = _time_call(
            lambda: get_observe(self.TRADE_DATE, tf_db_path=db_path)
        )
        assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
        _check_budget(
            "tradeflow_observe", elapsed, BUDGET_WITH_FIXTURE, "populated_db_no_live"
        )


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Acceptance — consolidated smoke
# ═══════════════════════════════════════════════════════════════════
class TestAcceptancePERF005:
    """Single end-to-end acceptance covering all PERF-005 acceptance points:

    - perf smoke passes on all 5 endpoints
    - runtime_tier_meta consistency holds
    - no live data/model calls (FAST_RADAR, llm_allowed=False)
    """

    ENDPOINTS = [
        "tradeflow_candidates",
        "tradeflow_observe",
        "tradeflow_review",
        "tradeflow_data_health",
        "tradeflow_topic_heatmap",
    ]

    def test_all_five_endpoints_fast_tier(self):
        for ep in self.ENDPOINTS:
            assert tradeflow_endpoint_tier(ep) == RuntimeTier.FAST_RADAR

    def test_all_five_endpoints_llm_disallowed(self):
        for ep in self.ENDPOINTS:
            assert tradeflow_meta(ep)["llm_allowed"] is False

    def test_full_endpoint_smoke_no_live_calls(self, tmp_path):
        """Run every endpoint against a fixture DB; none should raise or
        take longer than the FAST_RADAR lower bound allows."""
        db_path = _create_populated_db(str(tmp_path), FIXTURE_TRADE_DATE)

        calls: List[Tuple[str, Callable[[], dict]]] = [
            ("tradeflow_candidates", lambda: get_candidates(FIXTURE_TRADE_DATE, tf_db_path=db_path)),
            ("tradeflow_observe", lambda: get_observe(FIXTURE_TRADE_DATE, tf_db_path=db_path)),
            ("tradeflow_review", lambda: get_review(FIXTURE_TRADE_DATE, tf_db_path=db_path)),
            ("tradeflow_data_health", lambda: get_data_health(tf_db_path=db_path)),
            ("tradeflow_topic_heatmap", lambda: get_topic_heatmap(tf_db_path=db_path)),
        ]

        for endpoint, fn in calls:
            result, elapsed = _time_call(fn)
            assert "runtime_tier_meta" in result, f"{endpoint} missing tier meta"
            assert result["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"
            assert result["runtime_tier_meta"]["llm_allowed"] is False
            _check_budget(endpoint, elapsed, BUDGET_WITH_FIXTURE, "acceptance")

    def test_budget_constants_within_fast_radar_window(self):
        """Sanity: our test budgets must be well below the 30s upper bound
        advertised by the FAST_RADAR tier."""
        assert BUDGET_EMPTY_DB < 5.0
        assert BUDGET_WITH_FIXTURE < 5.0
        # HARD_FAIL_MULT keeps the obvious-regression trigger inside the
        # advertised fast window (5 * 3s = 15s, still within 5-30s).
        assert BUDGET_WITH_FIXTURE * HARD_FAIL_MULT <= 30.0


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] Suggestion surfacing — soft budget breach
# ═══════════════════════════════════════════════════════════════════
class TestSuggestionEmission:
    """The _check_budget helper must:

    - pass silently when within budget
    - record a suggestion (not fail) when over budget but < HARD_FAIL_MULT
    - hard-fail when over budget by >= HARD_FAIL_MULT
    """

    def test_within_budget_no_suggestion(self):
        before = len(_SUGGESTIONS)
        _check_budget("test_endpoint", 0.4, 1.0, "unit")
        assert len(_SUGGESTIONS) == before  # no new suggestion

    def test_slightly_over_budget_records_suggestion(self):
        before = len(_SUGGESTIONS)
        _check_budget("test_endpoint_slighly_over", 1.2, 1.0, "unit")
        assert len(_SUGGESTIONS) == before + 1
        # Should NOT raise.
        assert "test_endpoint_slighly_over" in _SUGGESTIONS[-1]

    def test_gross_regression_hard_fails(self):
        with pytest.raises(AssertionError) as exc_info:
            _check_budget(
                "test_endpoint_gross",
                float(HARD_FAIL_MULT) * 2.0,
                1.0,
                "unit",
            )
        assert "OBVIOUS REGRESSION" in str(exc_info.value)

    def test_suggestion_format_mentions_endpoint_and_action(self):
        before = len(_SUGGESTIONS)
        _check_budget("my_endpoint_xyz", 1.5, 1.0, "scenario_test")
        suggestion = _SUGGESTIONS[-1]
        assert "my_endpoint_xyz" in suggestion
        assert "scenario_test" in suggestion
        assert "consider" in suggestion.lower()


# ═══════════════════════════════════════════════════════════════════
# [PERF-005] End-of-module suggestion summary
# ═══════════════════════════════════════════════════════════════════
def test_module_perf_suggestion_summary() -> None:
    """Aggregate perf suggestions captured during the run.

    This is intentionally a normal test (always passes) — its purpose is
    to surface the collected PERF-005 suggestions in the pytest output so
    the nightly chain does not fail on a soft budget breach but still
    leaves an actionable trace in the log.
    """
    if _SUGGESTIONS:
        msg = "\n[PERF-005] Soft budget suggestions (%d):\n" % len(_SUGGESTIONS)
        msg += "\n".join("  - " + s for s in _SUGGESTIONS)
        # Print rather than fail — see PERF-005 acceptance criterion 4.
        print(msg)
    # Always pass unless a hard regression already failed an earlier test.
    assert True
