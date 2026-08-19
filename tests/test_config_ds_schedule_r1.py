# [CONFIG-DS-SCHEDULE-R1] tests
"""Tests for CONFIG-DS-SCHEDULE-R1.

Covers:
1. P1 — DeepSeek explicit authorization gate: default blocked, explicit
   switch to lift, fail-closed when the switch is off — enforced by the
   gate on the dispatcher itself, including direct construction.
2. P2 — 13:15 default intraday time: backend defaults unified with the
   frontend Portfolio.tsx DEFAULT_SCHEDULED_TASKS contract.
3. P2 — Legacy 14:30 schedules: no row rewriting at all — existing
   13:15/14:30 pairs cannot be retroactively classified as auto-created
   vs user-chosen, so the scheduler claims at most one intraday task per
   (user, symbol) per day (get_pending_tasks), and
   ensure_scheduled_for_symbols never auto-creates a second intraday task.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import (
    Base,
    ScheduledAnalysisDB,
)
from api.services import scheduled_service
from api.services.scheduled_service import get_pending_tasks
from tradingagents.tradeflow.gated_deep_ta import (
    DeepTADispatcher,
    check_deep_ta_gate,
)
from tradingagents.tradeflow.strategy_config import (
    DEFAULT_STRATEGY_CONFIG,
    StrategyConfig,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ── P1: DeepSeek explicit authorization gate ──


class TestDeepSeekExplicitAuthorizationConfig:
    def test_default_config_blocks_deepseek_and_is_unauthorized(self):
        cfg = StrategyConfig()
        assert cfg.deep_ta_blocked_models == ("deepseek",)
        assert cfg.deep_ta_deepseek_authorized is False
        assert DEFAULT_STRATEGY_CONFIG.deep_ta_blocked_models == ("deepseek",)
        assert DEFAULT_STRATEGY_CONFIG.deep_ta_deepseek_authorized is False

    def test_dispatcher_class_default_blocks_deepseek(self):
        d = DeepTADispatcher()
        assert d.blocked_models == ("deepseek",)

    def test_from_config_default_keeps_deepseek_blocked(self):
        d = DeepTADispatcher.from_config()
        assert "deepseek" in d.blocked_models

    def test_from_config_unauthorized_readds_deepseek_fail_closed(self):
        # A config that drops deepseek from the blocked list WITHOUT the
        # explicit authorization switch must not silently unblock it.
        cfg = StrategyConfig(deep_ta_blocked_models=())
        d = DeepTADispatcher.from_config(cfg)
        assert "deepseek" in d.blocked_models

    def test_from_config_authorized_removes_only_deepseek(self):
        cfg = StrategyConfig(
            deep_ta_blocked_models=("deepseek", "doubao"),
            deep_ta_deepseek_authorized=True,
        )
        d = DeepTADispatcher.from_config(cfg)
        assert d.blocked_models == ("doubao",)

    def test_from_config_authorized_does_not_mutate_declared_config(self):
        cfg = StrategyConfig(
            deep_ta_blocked_models=("deepseek",),
            deep_ta_deepseek_authorized=True,
        )
        d = DeepTADispatcher.from_config(cfg)
        assert d.blocked_models == ()
        assert cfg.deep_ta_blocked_models == ("deepseek",)

    def test_to_dict_includes_authorization_switch(self):
        d = StrategyConfig().to_dict()
        assert d["deep_ta_deepseek_authorized"] is False


class TestDeepSeekGateDecisions:
    def _check(self, dispatcher: DeepTADispatcher, model: str):
        return check_deep_ta_gate(
            dispatcher=dispatcher,
            symbol="002138.SZ",
            need_deep_ta=True,
            observe_state="TRIGGERED",
            composite_score=90.0,
            completeness=0.95,
            position_context="holding",
            model=model,
        )

    def test_gate_blocks_deepseek_by_default(self):
        decision = self._check(DeepTADispatcher.from_config(), "deepseek-chat")
        assert decision.allowed is False
        assert "deepseek" in decision.reason.lower()

    def test_gate_allows_deepseek_when_authorized(self):
        cfg = StrategyConfig(deep_ta_deepseek_authorized=True)
        decision = self._check(DeepTADispatcher.from_config(cfg), "deepseek-chat")
        assert decision.allowed is True
        assert decision.model == "deepseek-chat"

    def test_gate_blocks_other_models_without_authorization_relevance(self):
        cfg = StrategyConfig(
            deep_ta_blocked_models=("deepseek", "doubao"),
            deep_ta_deepseek_authorized=True,
        )
        decision = self._check(DeepTADispatcher.from_config(cfg), "doubao-pro")
        assert decision.allowed is False

    def test_direct_construction_cannot_bypass_authorization(self):
        # [P1 round1] Authorization lives on the dispatcher and is enforced
        # by the gate itself: DeepTADispatcher(blocked_models=()) used to
        # allow deepseek-chat; it must now fail closed.
        decision = self._check(DeepTADispatcher(blocked_models=()), "deepseek-chat")
        assert decision.allowed is False
        assert "deepseek" in decision.reason.lower()

    def test_direct_construction_authorized_allows_deepseek(self):
        decision = self._check(
            DeepTADispatcher(blocked_models=(), deepseek_authorized=True),
            "deepseek-chat",
        )
        assert decision.allowed is True
        assert decision.model == "deepseek-chat"


# ── P2: 13:15 frontend/backend default unification ──


class TestDefaultTriggerTimeUnification:
    def test_backend_default_trigger_times(self):
        assert scheduled_service.DEFAULT_SCHEDULED_TRIGGER_TIMES == ("13:15", "20:00")

    def test_frontend_default_tasks_match_backend_defaults(self):
        tsx_path = PROJECT_ROOT / "frontend" / "src" / "pages" / "Portfolio.tsx"
        assert tsx_path.exists(), "Portfolio.tsx not found"
        src = tsx_path.read_text(encoding="utf-8")

        block = re.search(
            r"DEFAULT_SCHEDULED_TASKS\s*=\s*\[(.*?)\]", src, re.DOTALL
        )
        assert block, "DEFAULT_SCHEDULED_TASKS block not found in Portfolio.tsx"
        frontend_times = re.findall(r"trigger_time:\s*'(\d{2}:\d{2})'", block.group(1))

        assert frontend_times == list(
            scheduled_service.DEFAULT_SCHEDULED_TRIGGER_TIMES
        ), (
            "frontend DEFAULT_SCHEDULED_TASKS times "
            f"{frontend_times} must equal backend DEFAULT_SCHEDULED_TRIGGER_TIMES "
            f"{list(scheduled_service.DEFAULT_SCHEDULED_TRIGGER_TIMES)}"
        )

    def test_frontend_guidance_mentions_unified_intraday_time(self):
        tsx_path = PROJECT_ROOT / "frontend" / "src" / "pages" / "Portfolio.tsx"
        src = tsx_path.read_text(encoding="utf-8")
        assert "13:15" in src

    def test_frontend_quota_uses_effective_count_contract(self):
        # [P2 round7] The Portfolio UI must enforce the MAX 10 limit with
        # the same EFFECTIVE-count contract as the backend (legacy
        # {13:15, 14:30} pairs collapse to one slot), never the raw row
        # count — otherwise the auto path can fill slots the UI then
        # refuses to ever use again.
        tsx_path = PROJECT_ROOT / "frontend" / "src" / "pages" / "Portfolio.tsx"
        src = tsx_path.read_text(encoding="utf-8")

        helper = re.search(
            r"const LEGACY_PAIR_SHADOW_TIMES.*?const effectiveScheduledCount = \(.*?\n\}",
            src,
            re.DOTALL,
        )
        assert helper, "effectiveScheduledCount helper not found in Portfolio.tsx"
        helper_src = helper.group(0)
        assert "13:15" in helper_src and "14:30" in helper_src, (
            "effectiveScheduledCount must collapse the legacy {13:15, 14:30} pair"
        )

        # Raw-row quota checks must be gone from every enforcement point.
        assert not re.search(r"scheduled\.length\s*>=\s*10", src), (
            "Portfolio.tsx still enforces the quota with the raw row count"
        )
        assert not re.search(
            r"scheduled\.length\s*\+\s*missingTasks\.length\s*>\s*10", src
        ), "createDefaultScheduledTasks still checks the raw row count"
        # ...and the effective-count checks must be present.
        assert re.search(
            r"effectiveScheduledCount\(scheduled\)\s*>=\s*10", src
        ), "quota disable checks must use effectiveScheduledCount"
        assert re.search(
            r"effectiveScheduledCount\(\[\.\.\.scheduled,\s*\.\.\.candidateTasks\]\)\s*>\s*10",
            src,
        ), (
            "createDefaultScheduledTasks must budget with the PROJECTED "
            "effective count (pair completion costs zero slots)"
        )


# ── P2: legacy 14:30 schedules — scheduler claim dedup + import guard ──


@pytest.fixture
def migration_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _add_task(
    db,
    user_id,
    symbol,
    trigger_time,
    created_at="2026-08-01 10:00:00",
    is_active=True,
    horizon="short",
    **extra,
):
    from uuid import uuid4

    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    db.add(
        ScheduledAnalysisDB(
            id=uuid4().hex,
            user_id=user_id,
            symbol=symbol,
            horizon=horizon,
            trigger_time=trigger_time,
            is_active=is_active,
            created_at=created_at,
            **extra,
        )
    )
    db.commit()


def _states(db, user_id, symbol):
    rows = (
        db.query(ScheduledAnalysisDB)
        .filter(
            ScheduledAnalysisDB.user_id == user_id,
            ScheduledAnalysisDB.symbol == symbol,
        )
        .all()
    )
    return {row.trigger_time: bool(row.is_active) for row in rows}


class TestSchedulerIntradayClaimDedup:
    """[CONFIG-DS-SCHEDULE-R1] get_pending_tasks dedups ONLY the exact
    legacy default pair {13:15, 14:30} created by the frontend/backend
    default mismatch — the pair cannot be retroactively classified as
    auto-created vs user-chosen, so no row is rewritten; the scheduler just
    claims at most one of the two per (user, symbol) per day. Every other
    combination keeps exact-time semantics."""

    def test_pair_claims_only_earliest_intraday(self, migration_db):
        # The frontend/backend mismatch pair: both rows active, none ran.
        _add_task(migration_db, "u1", "600519.SH", "14:30")
        _add_task(migration_db, "u1", "600519.SH", "13:15")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.trigger_time for t in tasks] == ["13:15"]
        # Nothing was rewritten: both rows stay exactly as created.
        assert _states(migration_db, "u1", "600519.SH") == {
            "13:15": True,
            "14:30": True,
        }

    def test_non_default_intraday_combinations_keep_exact_semantics(
        self, migration_db
    ):
        # [P1 round4] Deliberate multi-checkpoint setups that are not the
        # default {13:15, 14:30} collision must keep running every task.
        _add_task(migration_db, "u2a", "600519.SH", "11:35")
        _add_task(migration_db, "u2a", "600519.SH", "14:30")
        _add_task(migration_db, "u2b", "000001.SZ", "11:35")
        _add_task(migration_db, "u2b", "000001.SZ", "13:15")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert sorted((t.user_id, t.trigger_time) for t in tasks) == [
            ("u2a", "11:35"),
            ("u2a", "14:30"),
            ("u2b", "11:35"),
            ("u2b", "13:15"),
        ]

    def test_extra_checkpoint_alongside_default_pair(self, migration_db):
        # A deliberate 11:35 checkpoint coexisting with the default pair
        # still runs; only the pair itself is deduplicated.
        _add_task(migration_db, "u2c", "600519.SH", "11:35")
        _add_task(migration_db, "u2c", "600519.SH", "13:15")
        _add_task(migration_db, "u2c", "600519.SH", "14:30")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.trigger_time for t in tasks] == ["11:35", "13:15"]

    def test_allowlist_applied_before_pair_dedup(self, migration_db):
        # [P1 round5] The caller's allowlist must be applied BEFORE the pair
        # dedup: with only 14:30 allowed, the pair resolves to the allowed
        # 14:30 member instead of picking 13:15 and then claiming nothing.
        _add_task(migration_db, "u2d", "600519.SH", "13:15")
        _add_task(migration_db, "u2d", "600519.SH", "14:30")
        tasks = get_pending_tasks(
            migration_db,
            "2026-08-19",
            "16:00",
            allowed_trigger_times={"14:30"},
        )
        assert [(t.user_id, t.trigger_time) for t in tasks] == [
            ("u2d", "14:30")
        ]
        # Without an allowlist the unified 13:15 member wins as before.
        tasks2 = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.trigger_time for t in tasks2 if t.user_id == "u2d"] == ["13:15"]

    def test_distinct_horizons_are_not_duplicates(self, migration_db):
        # [P1 round5] The same symbol deliberately scheduled at different
        # horizons (short @13:15, medium @14:30) is two distinct analyses,
        # not a default collision — both run. The schema's unique
        # (user_id, symbol, trigger_time) constraint means horizons can only
        # differ across different trigger times, which is exactly the case
        # the pair key must not conflate.
        _add_task(migration_db, "u2e", "600519.SH", "13:15", horizon="short")
        _add_task(migration_db, "u2e", "600519.SH", "14:30", horizon="medium")
        # Same-horizon pairs on another symbol are still deduplicated.
        _add_task(migration_db, "u2f", "000001.SZ", "13:15", horizon="medium")
        _add_task(migration_db, "u2f", "000001.SZ", "14:30", horizon="medium")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert sorted(
            (t.user_id, t.horizon, t.trigger_time) for t in tasks
        ) == [
            ("u2e", "medium", "14:30"),  # distinct horizon: exact semantics
            ("u2e", "short", "13:15"),   # distinct horizon: exact semantics
            ("u2f", "medium", "13:15"),  # same-horizon pair: deduped
        ]
        # A same-day run consumes only that horizon's pair slot (the schema's
        # unique (user, symbol, trigger_time) constraint prevents a second
        # horizon from sharing 14:30, so cross-horizon conflation is
        # structurally impossible — only same-horizon siblings exist).
        _add_task(
            migration_db,
            "u2g",
            "600584.SH",
            "13:15",
            horizon="short",
            last_run_date="2026-08-19",
            last_run_status="success",
        )
        _add_task(migration_db, "u2g", "600584.SH", "14:30", horizon="short")
        tasks3 = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.user_id for t in tasks3 if t.user_id == "u2g"] == []

    def test_pair_before_later_trigger_time(self, migration_db):
        _add_task(migration_db, "u2", "600519.SH", "14:30")
        _add_task(migration_db, "u2", "600519.SH", "13:15")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "13:20")
        assert [t.trigger_time for t in tasks] == ["13:15"]

    def test_earlier_intraday_ran_today_claims_nothing_else(self, migration_db):
        # 13:15 already ran today; the 14:30 sibling must not trigger a
        # second paid analysis for the same symbol on the same day.
        _add_task(
            migration_db,
            "u3",
            "600519.SH",
            "13:15",
            last_run_date="2026-08-19",
            last_run_status="success",
        )
        _add_task(migration_db, "u3", "600519.SH", "14:30")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.user_id for t in tasks if t.user_id == "u3"] == []

    def test_later_intraday_ran_today_claims_nothing_else(self, migration_db):
        # 14:30 already ran today (mismatch window duplicate); a pending
        # 13:15 row must not re-run the same symbol the same day.
        _add_task(
            migration_db,
            "u4",
            "600519.SH",
            "14:30",
            last_run_date="2026-08-19",
            last_run_status="success",
        )
        _add_task(migration_db, "u4", "600519.SH", "13:15")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.user_id for t in tasks if t.user_id == "u4"] == []

    def test_disabled_intraday_that_ran_today_still_suppresses(
        self, migration_db
    ):
        # The run-state signal lives on the row, not on its active flag.
        _add_task(
            migration_db,
            "u5",
            "600519.SH",
            "14:30",
            is_active=False,
            last_run_date="2026-08-19",
            last_run_status="success",
        )
        _add_task(migration_db, "u5", "600519.SH", "13:15")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.user_id for t in tasks if t.user_id == "u5"] == []

    def test_failed_intraday_attempt_today_counts_as_run(self, migration_db):
        # mark_run_failed also sets last_run_date: no same-day retry via a
        # sibling row.
        _add_task(
            migration_db,
            "u6",
            "600519.SH",
            "13:15",
            last_run_date="2026-08-19",
            last_run_status="failed",
            consecutive_failures=1,
        )
        _add_task(migration_db, "u6", "600519.SH", "14:30")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.user_id for t in tasks if t.user_id == "u6"] == []

    def test_failure_deactivated_row_is_never_reactivated(self, migration_db):
        # [P1 round3] A row auto-deactivated after 3 failures stays
        # inactive; the healthy sibling keeps the symbol covered on the
        # next day without any state being copied or reset.
        _add_task(
            migration_db,
            "u7",
            "600519.SH",
            "13:15",
            is_active=False,
            last_run_date="2026-08-18",
            last_run_status="failed",
            consecutive_failures=3,
        )
        _add_task(migration_db, "u7", "600519.SH", "14:30")
        # Today nothing ran yet: the healthy 14:30 row is claimed.
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert [t.trigger_time for t in tasks if t.user_id == "u7"] == ["14:30"]
        deactivated = (
            migration_db.query(ScheduledAnalysisDB)
            .filter_by(user_id="u7", trigger_time="13:15")
            .one()
        )
        assert deactivated.is_active is False
        assert deactivated.consecutive_failures == 3

    def test_post_close_checkpoint_unaffected(self, migration_db):
        # 20:00 always remains claimable regardless of intraday history.
        _add_task(
            migration_db,
            "u8",
            "600519.SH",
            "13:15",
            last_run_date="2026-08-19",
            last_run_status="success",
        )
        _add_task(migration_db, "u8", "600519.SH", "20:00")
        _add_task(migration_db, "u8b", "000001.SZ", "20:00")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "20:30")
        assert [(t.user_id, t.trigger_time) for t in tasks] == [
            ("u8", "20:00"),
            ("u8b", "20:00"),
        ]

    def test_intraday_dedup_is_per_user_and_symbol(self, migration_db):
        _add_task(migration_db, "u9a", "600519.SH", "13:15")
        _add_task(migration_db, "u9a", "600519.SH", "14:30")
        _add_task(migration_db, "u9a", "000001.SZ", "14:30")
        _add_task(migration_db, "u9b", "600519.SH", "14:30")
        tasks = get_pending_tasks(migration_db, "2026-08-19", "16:00")
        assert sorted((t.user_id, t.trigger_time) for t in tasks) == [
            ("u9a", "13:15"),  # pair deduped to the unified default
            ("u9a", "14:30"),  # different symbol, no pair: exact semantics
            ("u9b", "14:30"),  # different user, no pair: exact semantics
        ]


class TestNoDuplicateTasksAfterMigration:
    def test_auto_import_does_not_duplicate_legacy_intraday(
        self, migration_db
    ):
        # User auto-imported positions under the old 14:30 default. The lone
        # 14:30 row is never rewritten, and the ensure() intraday guard —
        # not a row rewrite — prevents the duplicate: re-import reports the
        # symbol as covered and creates nothing new.
        _add_task(migration_db, "user-import", "600519.SH", "14:30")
        _add_task(migration_db, "user-import", "600519.SH", "20:00")

        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-import",
            symbols=["600519.SH"],
        )
        migration_db.commit()

        assert result["created"] == []
        assert result["existing"] == ["600519.SH", "600519.SH"]
        assert _states(migration_db, "user-import", "600519.SH") == {
            "14:30": True,
            "20:00": True,
        }

    def test_auto_import_guard_covers_any_intraday_time(self, migration_db):
        # An 11:35 checkpoint equally satisfies the intraday default.
        _add_task(migration_db, "user-cover", "600519.SH", "11:35")
        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-cover",
            symbols=["600519.SH"],
        )
        migration_db.commit()
        assert result["created"] == ["600519.SH"]  # only 20:00 added
        assert _states(migration_db, "user-cover", "600519.SH") == {
            "11:35": True,
            "20:00": True,
        }

    def test_auto_import_guard_respects_disabled_intraday_task(
        self, migration_db
    ):
        # A disabled intraday task still counts as covered: the auto path
        # must not silently re-enable intraday analysis the user turned off.
        _add_task(
            migration_db, "user-off", "600519.SH", "14:30", is_active=False
        )
        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-off",
            symbols=["600519.SH"],
        )
        migration_db.commit()
        assert result["created"] == ["600519.SH"]  # only 20:00 added
        assert _states(migration_db, "user-off", "600519.SH") == {
            "14:30": False,
            "20:00": True,
        }

    def test_auto_import_with_existing_pair_creates_nothing(
        self, migration_db
    ):
        # Mismatch-window pair: exact-key matching already covers 13:15,
        # the intraday guard is irrelevant, and 20:00 exists — nothing new.
        _add_task(migration_db, "user-pair", "600519.SH", "14:30")
        _add_task(migration_db, "user-pair", "600519.SH", "13:15")
        _add_task(migration_db, "user-pair", "600519.SH", "20:00")

        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-pair",
            symbols=["600519.SH"],
        )
        migration_db.commit()

        assert result["created"] == []
        assert result["existing"] == ["600519.SH", "600519.SH"]
        assert _states(migration_db, "user-pair", "600519.SH") == {
            "13:15": True,
            "14:30": True,
            "20:00": True,
        }

    def test_explicit_trigger_time_keeps_exact_semantics(
        self, migration_db
    ):
        # The fuzzy intraday guard applies only to the defaults; an explicit
        # trigger_time request still uses exact-(symbol, time) matching.
        _add_task(migration_db, "user-explicit", "600519.SH", "14:30")
        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-explicit",
            symbols=["600519.SH"],
            trigger_time="11:35",
        )
        migration_db.commit()
        assert result["created"] == ["600519.SH"]
        assert _states(migration_db, "user-explicit", "600519.SH") == {
            "11:35": True,
            "14:30": True,
        }

    def test_explicit_default_list_keeps_exact_semantics(self, migration_db):
        # [P2 round6] An explicit ["13:15", "20:00"] list is NOT default
        # mode: with a legacy 14:30 row present, the explicitly requested
        # 13:15 task is still created (no fuzzy guard).
        _add_task(migration_db, "user-expl-list", "600519.SH", "14:30")
        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-expl-list",
            symbols=["600519.SH"],
            trigger_time=["13:15", "20:00"],
        )
        migration_db.commit()
        assert result["created"] == ["600519.SH", "600519.SH"]
        assert _states(migration_db, "user-expl-list", "600519.SH") == {
            "13:15": True,
            "14:30": True,
            "20:00": True,
        }

    def test_import_guard_is_horizon_scoped(self, migration_db):
        # [P2 round6] A medium-horizon intraday task does not cover a
        # short-horizon auto import: distinct horizons are distinct
        # analyses and the short 13:15 default is still created.
        _add_task(
            migration_db, "user-hz", "600519.SH", "14:30", horizon="medium"
        )
        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-hz",
            symbols=["600519.SH"],
        )
        migration_db.commit()
        assert result["created"] == ["600519.SH", "600519.SH"]
        assert _states(migration_db, "user-hz", "600519.SH") == {
            "13:15": True,
            "14:30": True,
            "20:00": True,
        }

    def test_quota_collapses_legacy_default_pairs(self, migration_db):
        # [P2 round6] A {13:15, 14:30} pair is claim-deduplicated to one
        # daily run, so its shadowed member must not consume a
        # MAX_SCHEDULED_ITEMS slot: three symbols with pair+20:00 (9 raw
        # rows) still leave room for a fourth symbol's full defaults.
        for sym in ("600519.SH", "000001.SZ", "300750.SZ"):
            _add_task(migration_db, "user-quota", sym, "13:15")
            _add_task(migration_db, "user-quota", sym, "14:30")
            _add_task(migration_db, "user-quota", sym, "20:00")

        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-quota",
            symbols=["600584.SH"],
        )
        migration_db.commit()
        assert result["created"] == ["600584.SH", "600584.SH"]
        assert _states(migration_db, "user-quota", "600584.SH") == {
            "13:15": True,
            "20:00": True,
        }

    def test_premarket_task_is_not_intraday_coverage(self, migration_db):
        # [P2 round7] A valid pre-market schedule (e.g. 08:00) is NOT
        # intraday coverage: with the old "< 15:00" rule the auto-default
        # path suppressed the 13:15 task forever; it must be created.
        _add_task(migration_db, "user-premarket", "600519.SH", "08:00")
        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-premarket",
            symbols=["600519.SH"],
        )
        migration_db.commit()
        assert result["created"] == ["600519.SH", "600519.SH"]
        assert _states(migration_db, "user-premarket", "600519.SH") == {
            "08:00": True,
            "13:15": True,
            "20:00": True,
        }

    def test_intraday_coverage_requires_market_open(self, migration_db):
        # [P2 round7] Coverage window is [09:30, 15:00): 09:29 pre-open
        # is not coverage (13:15 still created); 09:30 open is.
        _add_task(migration_db, "user-edge-a", "600519.SH", "09:29")
        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-edge-a",
            symbols=["600519.SH"],
        )
        migration_db.commit()
        assert result["created"] == ["600519.SH", "600519.SH"]
        assert _states(migration_db, "user-edge-a", "600519.SH") == {
            "09:29": True,
            "13:15": True,
            "20:00": True,
        }

        _add_task(migration_db, "user-edge-b", "000001.SZ", "09:30")
        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-edge-b",
            symbols=["000001.SZ"],
        )
        migration_db.commit()
        assert result["created"] == ["000001.SZ"]  # only 20:00 added
        assert _states(migration_db, "user-edge-b", "000001.SZ") == {
            "09:30": True,
            "20:00": True,
        }

    def test_manual_create_quota_uses_effective_count(self, migration_db):
        # [P2 round7] create_scheduled must share the effective-count
        # contract with the auto path: after ensure() fills a user to 11
        # raw rows with 3 shadowed members (8 effective schedules),
        # manual creation is still allowed and still fails closed at 10
        # EFFECTIVE schedules — not at 10 raw rows.
        for sym in ("600519.SH", "000001.SZ", "300750.SZ"):
            _add_task(migration_db, "user-quota2", sym, "13:15")
            _add_task(migration_db, "user-quota2", sym, "14:30")
            _add_task(migration_db, "user-quota2", sym, "20:00")

        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-quota2",
            symbols=["600584.SH"],
        )
        migration_db.commit()
        assert result["created"] == ["600584.SH", "600584.SH"]

        # 11 raw rows / 8 effective: the round7 failing scenario — manual
        # creation was rejected by the raw-row count; it must now pass.
        created = scheduled_service.create_scheduled(
            migration_db, "user-quota2", "601127.SH", trigger_time="20:00"
        )
        assert created["symbol"] == "601127.SH"

        # ...but the limit still fails closed at 10 effective schedules.
        scheduled_service.create_scheduled(
            migration_db, "user-quota2", "601138.SH", trigger_time="20:00"
        )
        with pytest.raises(ValueError, match="上限"):
            scheduled_service.create_scheduled(
                migration_db, "user-quota2", "601988.SH", trigger_time="20:00"
            )

    def test_manual_create_quota_fails_closed_without_pairs(self, migration_db):
        # [P2 round7] Without shadowed pairs the effective contract equals
        # the raw count: the classic 10-row limit still rejects the 11th.
        for idx in range(10):
            _add_task(
                migration_db, "user-quota3", f"60000{idx}.SH", "20:00"
            )
        with pytest.raises(ValueError, match="上限"):
            scheduled_service.create_scheduled(
                migration_db, "user-quota3", "600519.SH", trigger_time="13:15"
            )

    def test_update_breaking_shadowed_pair_enforces_effective_quota(
        self, migration_db
    ):
        # [P2 round8] 13 raw rows / 10 effective schedules (three shadowed
        # legacy pairs): an update that BREAKS a pair — moving the 14:30
        # member off the pair times, or onto another horizon — would push
        # the effective count to 11 and must be rejected; the update path
        # shares the effective-count quota contract.
        for sym in ("600519.SH", "000001.SZ", "300750.SZ"):
            _add_task(migration_db, "user-quota4", sym, "13:15")
            _add_task(migration_db, "user-quota4", sym, "14:30")
            _add_task(migration_db, "user-quota4", sym, "20:00")
        for sym in ("600584.SH", "601127.SH", "601138.SH", "601988.SH"):
            scheduled_service.create_scheduled(
                migration_db, "user-quota4", sym, trigger_time="20:00"
            )
        assert (
            migration_db.query(ScheduledAnalysisDB)
            .filter(ScheduledAnalysisDB.user_id == "user-quota4")
            .count()
            == 13
        )

        legacy_member = (
            migration_db.query(ScheduledAnalysisDB)
            .filter_by(
                user_id="user-quota4", symbol="600519.SH", trigger_time="14:30"
            )
            .one()
        )
        with pytest.raises(ValueError, match="上限"):
            scheduled_service.update_scheduled(
                migration_db, "user-quota4", legacy_member.id, trigger_time="11:35"
            )
        with pytest.raises(ValueError, match="上限"):
            scheduled_service.update_scheduled(
                migration_db, "user-quota4", legacy_member.id, horizon="medium"
            )
        with pytest.raises(ValueError, match="上限"):
            scheduled_service.batch_update_scheduled(
                migration_db, "user-quota4", [legacy_member.id], trigger_time="11:35"
            )
        # Rejected edits mutate nothing.
        assert legacy_member.trigger_time == "14:30"
        assert legacy_member.horizon == "short"

        # A non-pair edit (horizon on a plain 20:00 row) stays allowed.
        plain_row = (
            migration_db.query(ScheduledAnalysisDB)
            .filter_by(
                user_id="user-quota4", symbol="600584.SH", trigger_time="20:00"
            )
            .one()
        )
        updated = scheduled_service.update_scheduled(
            migration_db, "user-quota4", plain_row.id, horizon="medium"
        )
        assert updated["horizon"] == "medium"

    def test_update_breaking_pair_below_limit_is_allowed(self, migration_db):
        # [P2 round8] Below the quota the same edit is fine: with one
        # legacy pair (3 raw / 2 effective), moving the shadowed 14:30
        # member to 11:35 yields 3 effective schedules — allowed.
        _add_task(migration_db, "user-quota5", "600519.SH", "13:15")
        _add_task(migration_db, "user-quota5", "600519.SH", "14:30")
        _add_task(migration_db, "user-quota5", "600519.SH", "20:00")

        updated = scheduled_service.update_scheduled(
            migration_db,
            "user-quota5",
            (
                migration_db.query(ScheduledAnalysisDB)
                .filter_by(
                    user_id="user-quota5", symbol="600519.SH", trigger_time="14:30"
                )
                .one()
            ).id,
            trigger_time="11:35",
        )
        assert updated["trigger_time"] == "11:35"
        assert sorted(_states(migration_db, "user-quota5", "600519.SH")) == [
            "11:35",
            "13:15",
            "20:00",
        ]

    def test_pair_completion_costs_zero_effective_slots(self, migration_db):
        # [P2 round9] At 10 effective schedules including a lone 14:30
        # row for the symbol, adding the missing same-horizon 13:15
        # member merely completes a shadowed pair (zero effective slots)
        # and must be allowed; adding any genuinely new schedule still
        # fails closed.
        for sym in ("600519.SH", "000001.SZ", "300750.SZ"):
            _add_task(migration_db, "user-quota6", sym, "13:15")
            _add_task(migration_db, "user-quota6", sym, "14:30")
            _add_task(migration_db, "user-quota6", sym, "20:00")
        # A lone 14:30 row (no 13:15 sibling): 12 raw / 10 effective.
        _add_task(migration_db, "user-quota6", "600584.SH", "14:30")
        _add_task(migration_db, "user-quota6", "601127.SH", "20:00")
        _add_task(migration_db, "user-quota6", "601138.SH", "20:00")
        _add_task(migration_db, "user-quota6", "601988.SH", "20:00")

        # Completing the pair: 13 raw / still 10 effective — allowed.
        created = scheduled_service.create_scheduled(
            migration_db, "user-quota6", "600584.SH", trigger_time="13:15"
        )
        assert created["trigger_time"] == "13:15"

        # Any further genuinely-new schedule exceeds the quota.
        with pytest.raises(ValueError, match="上限"):
            scheduled_service.create_scheduled(
                migration_db, "user-quota6", "600584.SH", trigger_time="20:00"
            )

    def test_ensure_explicit_pair_completion_respects_projected_quota(
        self, migration_db
    ):
        # [P2 round9] The ensure path validates the projected set per
        # insert: with an explicit ["13:15", "20:00"] request against 10
        # effective schedules that include a lone same-horizon 14:30, the
        # 13:15 completes the pair (created) and only the 20:00 is
        # skipped for quota.
        for sym in ("600519.SH", "000001.SZ", "300750.SZ"):
            _add_task(migration_db, "user-quota7", sym, "13:15")
            _add_task(migration_db, "user-quota7", sym, "14:30")
            _add_task(migration_db, "user-quota7", sym, "20:00")
        _add_task(migration_db, "user-quota7", "600584.SH", "14:30")
        _add_task(migration_db, "user-quota7", "601127.SH", "20:00")
        _add_task(migration_db, "user-quota7", "601138.SH", "20:00")
        _add_task(migration_db, "user-quota7", "601988.SH", "20:00")

        result = scheduled_service.ensure_scheduled_for_symbols(
            db=migration_db,
            user_id="user-quota7",
            symbols=["600584.SH"],
            trigger_time=["13:15", "20:00"],
        )
        migration_db.commit()
        assert result["created"] == ["600584.SH"]  # only the pair member
        assert result["skipped_limit"] == ["600584.SH"]  # 20:00 over quota
        assert _states(migration_db, "user-quota7", "600584.SH") == {
            "13:15": True,
            "14:30": True,
        }
