"""Scheduled analysis service for database operations."""

from typing import Iterable, List, Optional
from uuid import uuid4

from sqlalchemy import orm
from sqlalchemy.orm import Session
import sqlalchemy

from api.database import ScheduledAnalysisDB

MAX_SCHEDULED_ITEMS = 10
# [CONFIG-DS-SCHEDULE-R1] Intraday checkpoint unified with frontend
# Portfolio.tsx DEFAULT_SCHEDULED_TASKS (13:15 盘中 + 20:00 盘后).
DEFAULT_SCHEDULED_TRIGGER_TIMES = ("13:15", "20:00")
# [CONFIG-DS-SCHEDULE-R1] The old backend default intraday checkpoint whose
# collision with the frontend 13:15 default created duplicate pairs.
LEGACY_DEFAULT_INTRADAY_TRIGGER_TIME = "14:30"
# [P2 round7] A-share continuous trading runs 09:30–15:00; only a
# checkpoint inside that window (11:35 / 13:15 / 14:30 ...) is an intraday
# task. Pre-open (e.g. 08:00) or post-close times must NOT be classified
# as intraday coverage, or the auto path would skip creating the 13:15
# default for a user whose only task is a valid pre-market schedule.
_INTRADAY_COVERED_OPEN = "09:30"
_INTRADAY_COVERED_CUTOFF = "15:00"

VALID_HORIZONS = {"short", "medium"}


def _validate_trigger_time(t: str) -> str:
    """Validate HH:MM format.

    The scheduler now supports both intraday watch points and post-close
    review points. Frontend guidance can still recommend 11:35/13:15/20:00,
    but the backend only enforces a valid wall-clock time.
    """
    parts = t.strip().split(":")
    if len(parts) != 2:
        raise ValueError("时间格式错误，请使用 HH:MM")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError("时间格式错误，请使用 HH:MM")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("时间格式错误，请使用 HH:MM")
    return f"{hh:02d}:{mm:02d}"


def _is_intraday_trigger_time(t: str) -> bool:
    """[P2 round7] True only inside the 09:30–15:00 continuous session."""
    return _INTRADAY_COVERED_OPEN <= t < _INTRADAY_COVERED_CUTOFF


def _item_triples(
    items: Iterable[ScheduledAnalysisDB],
) -> List[tuple[str, str, str]]:
    """[P2 round9] (symbol, horizon, trigger_time) projection of rows."""
    return [
        (i.symbol, i.horizon or "short", i.trigger_time or "20:00")
        for i in items
    ]


def _effective_schedule_count(
    triples: Iterable[tuple[str, str, str]],
) -> int:
    """[P2 round7] Effective schedule count over (symbol, horizon, time).

    Counts schedules with the legacy {13:15, 14:30} pair collapsed to a
    single slot: get_pending_tasks claim-deduplicates such a pair to one
    daily run, so its shadowed member never consumes a
    MAX_SCHEDULED_ITEMS slot. Accepts PROJECTED triples so creation and
    update paths can validate the post-edit quota before mutating
    anything ([P2 round9]: completing a pair costs zero slots).
    """
    triple_list = list(triples)
    pair_unified = DEFAULT_SCHEDULED_TRIGGER_TIMES[0]
    pair_legacy = LEGACY_DEFAULT_INTRADAY_TRIGGER_TIME
    pair_keys = (
        {(s, h) for s, h, _t in triple_list if _t == pair_unified}
        & {(s, h) for s, h, _t in triple_list if _t == pair_legacy}
    )
    return len(triple_list) - len(pair_keys)


def _validate_effective_quota(
    triples: Iterable[tuple[str, str, str]],
) -> None:
    """[P2 round8] Reject edits whose projected effective count exceeds
    MAX_SCHEDULED_ITEMS: breaking a shadowed {13:15, 14:30} pair via an
    update must not push the effective schedule count past the quota the
    creation paths enforce."""
    if _effective_schedule_count(triples) > MAX_SCHEDULED_ITEMS:
        raise ValueError(
            f"修改将使定时分析有效数量超过上限 ({MAX_SCHEDULED_ITEMS})，请先删除多余任务"
        )


def _ensure_no_schedule_conflict(
    db: Session,
    user_id: str,
    symbol: str,
    trigger_time: str,
    *,
    exclude_id: Optional[str] = None,
) -> None:
    query = db.query(ScheduledAnalysisDB).filter(
        ScheduledAnalysisDB.user_id == user_id,
        ScheduledAnalysisDB.symbol == symbol,
        ScheduledAnalysisDB.trigger_time == trigger_time,
    )
    if exclude_id:
        query = query.filter(ScheduledAnalysisDB.id != exclude_id)
    if query.first():
        raise ValueError(f"{symbol} 在 {trigger_time} 已有定时分析任务")


def list_scheduled(db: Session, user_id: str) -> List[dict]:
    """List user's scheduled analysis tasks."""
    items = (
        db.query(ScheduledAnalysisDB)
        .filter(ScheduledAnalysisDB.user_id == user_id)
        .order_by(ScheduledAnalysisDB.created_at)
        .all()
    )
    return [_to_dict(item) for item in items]


def get_scheduled(db: Session, user_id: str, item_id: str) -> Optional[dict]:
    """Get a single scheduled analysis task for the user."""
    item = (
        db.query(ScheduledAnalysisDB)
        .filter(ScheduledAnalysisDB.user_id == user_id, ScheduledAnalysisDB.id == item_id)
        .first()
    )
    if not item:
        return None
    return _to_dict(item)


def get_scheduled_batch(db: Session, user_id: str, item_ids: Iterable[str]) -> List[dict]:
    """Get multiple scheduled analysis tasks in the requested order."""

    normalized_ids = _normalize_item_ids(item_ids)
    if not normalized_ids:
        raise ValueError("请至少选择一个定时任务")

    items = (
        db.query(ScheduledAnalysisDB)
        .filter(
            ScheduledAnalysisDB.user_id == user_id,
            ScheduledAnalysisDB.id.in_(normalized_ids),
        )
        .all()
    )
    item_map = {item.id: item for item in items}
    missing_ids = [item_id for item_id in normalized_ids if item_id not in item_map]
    if missing_ids:
        raise ValueError("部分定时任务不存在或已失效，请刷新后重试")

    return [_to_dict(item_map[item_id]) for item_id in normalized_ids]


def _normalize_item_ids(item_ids: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_id in item_ids:
        item_id = (raw_id or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        normalized.append(item_id)
    return normalized


def _validate_horizon(horizon: str) -> str:
    if horizon not in VALID_HORIZONS:
        raise ValueError("horizon 必须为 short 或 medium")
    return horizon


def _apply_scheduled_updates(item: ScheduledAnalysisDB, **kwargs) -> None:
    if "is_active" in kwargs:
        item.is_active = kwargs["is_active"]
        if kwargs["is_active"]:
            item.consecutive_failures = 0
    if "horizon" in kwargs:
        item.horizon = _validate_horizon(kwargs["horizon"])
    if "trigger_time" in kwargs:
        item.trigger_time = _validate_trigger_time(kwargs["trigger_time"])


def create_scheduled(
    db: Session,
    user_id: str,
    symbol: str,
    horizon: str = "short",
    trigger_time: str = "20:00",
) -> dict:
    """Create a scheduled analysis task."""
    horizon = _validate_horizon(horizon)
    trigger_time = _validate_trigger_time(trigger_time)
    _ensure_no_schedule_conflict(db, user_id, symbol, trigger_time)
    # [P2 round7/round9] Quota validates the PROJECTED effective count:
    # a legacy {13:15, 14:30} pair is claim-deduplicated to one daily
    # run, so completing such a pair costs zero effective slots and must
    # not be rejected by a pre-insert raw/effective row count.
    existing_items = (
        db.query(ScheduledAnalysisDB)
        .filter(ScheduledAnalysisDB.user_id == user_id)
        .all()
    )
    projected = _item_triples(existing_items)
    projected.append((symbol, horizon, trigger_time))
    if _effective_schedule_count(projected) > MAX_SCHEDULED_ITEMS:
        raise ValueError(f"定时分析数量已达上限 ({MAX_SCHEDULED_ITEMS})")

    item = ScheduledAnalysisDB(
        id=uuid4().hex,
        user_id=user_id,
        symbol=symbol,
        horizon=horizon,
        trigger_time=trigger_time,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_dict(item)


def ensure_scheduled_for_symbols(
    db: Session,
    user_id: str,
    symbols: Iterable[str],
    horizon: str = "short",
    trigger_time: str | Iterable[str] | None = None,
) -> dict:
    """Ensure the given symbols exist in scheduled tasks without duplicating existing items."""

    horizon = _validate_horizon(horizon)
    # [P2 round6] Default mode is determined by whether trigger_time was
    # omitted, not by value comparison: an explicit ["13:15", "20:00"]
    # request keeps exact-(symbol, time) semantics.
    if trigger_time is None:
        trigger_times = list(DEFAULT_SCHEDULED_TRIGGER_TIMES)
        using_defaults = True
    elif isinstance(trigger_time, str):
        trigger_times = [_validate_trigger_time(trigger_time)]
        using_defaults = False
    else:
        trigger_times = [_validate_trigger_time(item) for item in trigger_time]
        if not trigger_times:
            trigger_times = list(DEFAULT_SCHEDULED_TRIGGER_TIMES)
            using_defaults = True
        else:
            using_defaults = False

    existing_items = (
        db.query(ScheduledAnalysisDB)
        .filter(ScheduledAnalysisDB.user_id == user_id)
        .order_by(ScheduledAnalysisDB.created_at)
        .all()
    )
    existing_keys = {(item.symbol, item.trigger_time or "20:00") for item in existing_items}
    # [P2 round6/round9] Quota validates the PROJECTED effective count:
    # a legacy {13:15, 14:30} default pair (same symbol AND horizon) is
    # claim-deduplicated to one daily run in get_pending_tasks, so its
    # shadowed member consumes no MAX_SCHEDULED_ITEMS slot — completing
    # such a pair costs zero effective slots.
    projected_triples = _item_triples(existing_items)
    # [CONFIG-DS-SCHEDULE-R1] Intraday guard for the auto path: a symbol
    # that already has ANY intraday task at the SAME horizon (active or not
    # — e.g. a legacy 14:30 row whose auto/user origin cannot be
    # distinguished) is covered; the default intraday checkpoint must not
    # duplicate it into a second paid analysis. [P2 round7] Only times
    # inside the 09:30–15:00 continuous session count as intraday, so a
    # pre-market schedule (e.g. 08:00) does not suppress the 13:15
    # default. Different-horizon analyses are distinct schedules and never
    # count as coverage. Applies only when the defaults are in use;
    # explicit trigger_time requests keep exact-(symbol, time) semantics.
    intraday_covered_keys = {
        (item.symbol, item.horizon or "short")
        for item in existing_items
        if _is_intraday_trigger_time(item.trigger_time or "20:00")
    }

    created: list[str] = []
    existing: list[str] = []
    skipped_limit: list[str] = []
    seen: set[str] = set()

    for raw_symbol in symbols:
        symbol = (raw_symbol or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)

        for trigger_time_item in trigger_times:
            key = (symbol, trigger_time_item)
            if key in existing_keys:
                existing.append(symbol)
                continue

            if (
                using_defaults
                and _is_intraday_trigger_time(trigger_time_item)
                and (symbol, horizon) in intraday_covered_keys
            ):
                existing.append(symbol)
                continue

            candidate_triple = (symbol, horizon, trigger_time_item)
            if (
                _effective_schedule_count(projected_triples + [candidate_triple])
                > MAX_SCHEDULED_ITEMS
            ):
                skipped_limit.append(symbol)
                continue

            db.add(
                ScheduledAnalysisDB(
                    id=uuid4().hex,
                    user_id=user_id,
                    symbol=symbol,
                    horizon=horizon,
                    trigger_time=trigger_time_item,
                )
            )
            existing_keys.add(key)
            projected_triples.append(candidate_triple)
            created.append(symbol)

    if created:
        db.flush()

    return {
        "created": created,
        "existing": existing,
        "skipped_limit": skipped_limit,
    }


def update_scheduled(db: Session, user_id: str, item_id: str, **kwargs) -> Optional[dict]:
    """Update a scheduled analysis task. Returns None if not found."""
    item = (
        db.query(ScheduledAnalysisDB)
        .filter(ScheduledAnalysisDB.id == item_id, ScheduledAnalysisDB.user_id == user_id)
        .first()
    )
    if not item:
        return None

    next_trigger_time = _validate_trigger_time(kwargs["trigger_time"]) if "trigger_time" in kwargs else (item.trigger_time or "20:00")
    if "trigger_time" in kwargs:
        _ensure_no_schedule_conflict(db, user_id, item.symbol, next_trigger_time, exclude_id=item.id)
    # [P2 round8] Validate the POST-EDIT effective quota: breaking a
    # shadowed legacy pair (changing its trigger_time or horizon) must
    # not push the effective schedule count past MAX_SCHEDULED_ITEMS.
    next_horizon = _validate_horizon(kwargs["horizon"]) if "horizon" in kwargs else (item.horizon or "short")
    all_items = (
        db.query(ScheduledAnalysisDB)
        .filter(ScheduledAnalysisDB.user_id == user_id)
        .all()
    )
    _validate_effective_quota(
        (
            i.symbol,
            next_horizon if i.id == item_id else (i.horizon or "short"),
            next_trigger_time if i.id == item_id else (i.trigger_time or "20:00"),
        )
        for i in all_items
    )
    _apply_scheduled_updates(item, **kwargs)

    db.commit()
    db.refresh(item)
    return _to_dict(item)


def batch_update_scheduled(
    db: Session,
    user_id: str,
    item_ids: Iterable[str],
    **kwargs,
) -> List[dict]:
    """Update multiple scheduled analysis tasks in a single transaction."""

    normalized_ids = _normalize_item_ids(item_ids)
    if not normalized_ids:
        raise ValueError("请至少选择一个定时任务")
    if not kwargs:
        raise ValueError("至少提供一个更新字段")

    items = (
        db.query(ScheduledAnalysisDB)
        .filter(
            ScheduledAnalysisDB.user_id == user_id,
            ScheduledAnalysisDB.id.in_(normalized_ids),
        )
        .all()
    )
    item_map = {item.id: item for item in items}
    missing_ids = [item_id for item_id in normalized_ids if item_id not in item_map]
    if missing_ids:
        raise ValueError("部分定时任务不存在或已失效，请刷新后重试")

    if "trigger_time" in kwargs:
        next_trigger_time = _validate_trigger_time(kwargs["trigger_time"])
        seen_keys: set[tuple[str, str]] = set()
        for item_id in normalized_ids:
            item = item_map[item_id]
            key = (item.symbol, next_trigger_time)
            if key in seen_keys:
                raise ValueError(f"{item.symbol} 在 {next_trigger_time} 已有定时分析任务")
            seen_keys.add(key)
            _ensure_no_schedule_conflict(db, user_id, item.symbol, next_trigger_time, exclude_id=item.id)

    # [P2 round8] Validate the POST-EDIT effective quota for the whole
    # batch: breaking shadowed legacy pairs (trigger_time or horizon
    # changes) must not push the effective count past MAX_SCHEDULED_ITEMS.
    next_horizon = _validate_horizon(kwargs["horizon"]) if "horizon" in kwargs else None
    next_time = _validate_trigger_time(kwargs["trigger_time"]) if "trigger_time" in kwargs else None
    all_items = (
        db.query(ScheduledAnalysisDB)
        .filter(ScheduledAnalysisDB.user_id == user_id)
        .all()
    )
    _validate_effective_quota(
        (
            i.symbol,
            next_horizon or (i.horizon or "short") if i.id in item_map else (i.horizon or "short"),
            next_time or (i.trigger_time or "20:00") if i.id in item_map else (i.trigger_time or "20:00"),
        )
        for i in all_items
    )

    for item_id in normalized_ids:
        _apply_scheduled_updates(item_map[item_id], **kwargs)

    db.commit()
    for item in items:
        db.refresh(item)
    return [_to_dict(item_map[item_id]) for item_id in normalized_ids]


def delete_scheduled(db: Session, user_id: str, item_id: str) -> bool:
    """Delete a scheduled analysis task."""
    item = (
        db.query(ScheduledAnalysisDB)
        .filter(ScheduledAnalysisDB.id == item_id, ScheduledAnalysisDB.user_id == user_id)
        .first()
    )
    if not item:
        return False
    db.delete(item)
    db.commit()
    return True


def batch_delete_scheduled(db: Session, user_id: str, item_ids: Iterable[str]) -> dict:
    """Delete multiple scheduled analysis tasks."""

    normalized_ids = _normalize_item_ids(item_ids)
    if not normalized_ids:
        raise ValueError("请至少选择一个定时任务")

    items = (
        db.query(ScheduledAnalysisDB)
        .filter(
            ScheduledAnalysisDB.user_id == user_id,
            ScheduledAnalysisDB.id.in_(normalized_ids),
        )
        .all()
    )
    item_map = {item.id: item for item in items}
    deleted_ids: list[str] = []
    missing_ids: list[str] = []

    for item_id in normalized_ids:
        item = item_map.get(item_id)
        if item is None:
            missing_ids.append(item_id)
            continue
        db.delete(item)
        deleted_ids.append(item_id)

    if deleted_ids:
        db.commit()

    return {
        "deleted_ids": deleted_ids,
        "missing_ids": missing_ids,
    }


def get_pending_tasks(
    db: Session,
    today: str,
    current_hhmm: str,
    allowed_trigger_times: Optional[Iterable[str]] = None,
) -> List[ScheduledAnalysisDB]:
    """Get all active tasks that haven't run today and whose trigger time has passed.

    Only returns tasks for real (non-test) users to avoid duplicate runs
    from dashboard test accounts.

    ``allowed_trigger_times`` is the caller's claim-time allowlist (e.g. the
    scheduler's SCHEDULER_ALLOWED_TRIGGER_TIMES). It is applied BEFORE the
    pair dedup below so the surviving pair member is always chosen among
    claimable times.

    [CONFIG-DS-SCHEDULE-R1] Default-pair claim dedup — scoped to exactly the
    legacy default collision {13:15, 14:30} created by the historical
    frontend/backend default mismatch. Such pairs (same user, symbol AND
    horizon — different-horizon analyses are distinct schedules, not
    duplicates) cannot be retroactively classified as auto-created vs
    user-chosen, so no row is rewritten or deactivated; instead the
    scheduler claims at most one of the two per pair per day (the earlier
    13:15 first) and claims neither once one of them — active or disabled,
    including a failed attempt — already ran today. Every other scheduling
    combination (e.g. a deliberate 11:35 + 14:30) keeps exact-time
    semantics.
    """
    from api.database import UserDB

    # Exclude dashboard test accounts (@test.com) to prevent
    # 452 test users from triggering duplicate scheduled runs.
    # Users NOT in the users table (e.g. test fixtures) are kept.
    test_user_ids = (
        db.query(UserDB.id)
        .filter(UserDB.email.like("%@test.com"))
        .subquery()
    )

    all_active = (
        db.query(ScheduledAnalysisDB)
        .filter(
            ScheduledAnalysisDB.is_active == True,
            ~ScheduledAnalysisDB.user_id.in_(sqlalchemy.select(test_user_ids)),
            (ScheduledAnalysisDB.last_run_date != today) | (ScheduledAnalysisDB.last_run_date == None),
        )
        .all()
    )
    if allowed_trigger_times:
        allowed = set(allowed_trigger_times)
        all_active = [
            t for t in all_active if (t.trigger_time or "20:00") in allowed
        ]

    # All rows (including disabled ones) for the default-pair analysis: a
    # disabled row that already ran today still means its pair consumed the
    # daily intraday default slot.
    all_rows = (
        db.query(ScheduledAnalysisDB)
        .filter(
            ~ScheduledAnalysisDB.user_id.in_(sqlalchemy.select(test_user_ids))
        )
        .all()
    )
    pair_times = (
        DEFAULT_SCHEDULED_TRIGGER_TIMES[0],
        LEGACY_DEFAULT_INTRADAY_TRIGGER_TIME,
    )

    def _pair_key(t: ScheduledAnalysisDB) -> tuple[str, str, str]:
        return (t.user_id, t.symbol, t.horizon or "short")

    default_pair_keys = {
        _pair_key(t) for t in all_rows if t.trigger_time == pair_times[0]
    } & {
        _pair_key(t) for t in all_rows if t.trigger_time == pair_times[1]
    }
    pair_ran_today = {
        _pair_key(t)
        for t in all_rows
        if t.last_run_date == today and t.trigger_time in pair_times
    }

    # Filter by trigger_time <= current_hhmm
    pending = [t for t in all_active if (t.trigger_time or "20:00") <= current_hhmm]

    claimed_pair: set[tuple[str, str, str]] = set()
    result: list[ScheduledAnalysisDB] = []
    # Earliest checkpoint first so the dedup prefers the unified 13:15 task
    # over its legacy 14:30 sibling.
    for task in sorted(pending, key=lambda t: t.trigger_time or "20:00"):
        if (task.trigger_time or "20:00") in pair_times:
            key = _pair_key(task)
            if key in default_pair_keys:
                if key in pair_ran_today or key in claimed_pair:
                    continue
                claimed_pair.add(key)
        result.append(task)
    return result


def mark_run_success(db: Session, item_id: str, trade_date: str, report_id: str):
    """Mark a scheduled task as successfully run."""
    item = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == item_id).first()
    if item:
        item.last_run_date = trade_date
        item.last_run_status = "success"
        item.last_report_id = report_id
        item.consecutive_failures = 0
        db.commit()


def mark_run_failed(db: Session, item_id: str, trade_date: str):
    """Mark a scheduled task as failed. Auto-deactivate after 3 consecutive failures."""
    item = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == item_id).first()
    if item:
        item.last_run_date = trade_date
        item.last_run_status = "failed"
        item.consecutive_failures = (item.consecutive_failures or 0) + 1
        if item.consecutive_failures >= 3:
            item.is_active = False
        db.commit()


def record_manual_test_result(
    db: Session,
    item_id: str,
    status: str,
    report_id: Optional[str] = None,
) -> None:
    """Record the latest manual test result without consuming the day's schedule."""
    item = db.query(ScheduledAnalysisDB).filter(ScheduledAnalysisDB.id == item_id).first()
    if not item:
        return
    item.last_run_status = status
    if report_id:
        item.last_report_id = report_id
    if status == "success":
        item.consecutive_failures = 0
    db.commit()


def _to_dict(item: ScheduledAnalysisDB) -> dict:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "horizon": item.horizon or "short",
        "trigger_time": item.trigger_time or "20:00",
        "is_active": item.is_active,
        "last_run_date": item.last_run_date,
        "last_run_status": item.last_run_status,
        "last_report_id": item.last_report_id,
        "consecutive_failures": item.consecutive_failures,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
