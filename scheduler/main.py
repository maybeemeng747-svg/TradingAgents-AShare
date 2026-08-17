"""Standalone scheduler process.

Runs independently of the FastAPI API server. Checks every minute for
scheduled analysis tasks to trigger and executes them with concurrency
control via a simple ``asyncio.Semaphore``.

Start with::

    python -m scheduler.main
"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from dotenv import load_dotenv
from tradingagents.dataflows.network_timeout import install_default_network_timeout

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _log(msg: str):
    logger.info(msg)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("[Scheduler] Invalid %s=%r, using %s", name, value, default)
        return default


def _env_time_allowlist(name: str) -> set[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


# ── Concurrency ──────────────────────────────────────────────────────────────
SCHEDULER_ENABLED = _env_bool("SCHEDULER_ENABLED", True)
SCHEDULER_DRY_RUN = _env_bool("SCHEDULER_DRY_RUN", False)
SCHEDULER_CONCURRENCY = _env_int("SCHEDULER_CONCURRENCY", 1)
SCHEDULER_MAX_TASKS_PER_TICK = _env_int("SCHEDULER_MAX_TASKS_PER_TICK", 1)
SCHEDULER_RUN_INTRADAY = _env_bool("SCHEDULER_RUN_INTRADAY", True)
SCHEDULER_ALLOWED_TRIGGER_TIMES = _env_time_allowlist("SCHEDULER_ALLOWED_TRIGGER_TIMES")

_semaphore: Optional[asyncio.Semaphore] = None
_executor: Optional[ThreadPoolExecutor] = None

# Hold references to fire-and-forget tasks so they are not garbage collected
_background_tasks: set = set()


def _create_tracked_task(coro, *, label: str = "Background task") -> asyncio.Task:
    """Create an asyncio task and keep a reference to prevent GC."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error("%s failed: %s", label, t.exception())

    task.add_done_callback(_on_done)
    return task


# ── Imports from api & tradingagents ─────────────────────────────────────────
from api.database import (
    ScheduledAnalysisDB,
    ReportDB,
    UserDB,
    init_db,
    get_db_ctx,
)
from api.job_store import get_job_store as _new_job_store
from api.services import (
    auth_service,
    report_service,
    scheduled_service,
)

# Thin wrappers & job runner from the API module
from api.main import (
    _build_imported_user_context,
    _build_scheduled_analyze_request,
    _resolve_scheduled_trade_date,
    _run_job,
    _set_job,
    _get_job,
    _emit_job_event,
    get_job_store,
)

from tradingagents.dataflows.providers.cn_akshare_provider import (
    reset_scheduled_task_context,
    set_scheduled_task_context,
)


# ── Semaphore-based concurrency slot ─────────────────────────────────────────

@asynccontextmanager
async def _concurrency_slot(job_id: str, symbol: str):
    """Acquire/release a concurrency slot for a scheduled job."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(SCHEDULER_CONCURRENCY)

    if SCHEDULER_CONCURRENCY <= 0:
        # 0 = unlimited concurrency
        yield
        return

    _log(
        f"[Scheduler] Waiting for slot job={job_id} symbol={symbol}"
    )
    await _semaphore.acquire()
    try:
        _log(
            f"[Scheduler] Acquired slot job={job_id} symbol={symbol}"
        )
        yield
    finally:
        _semaphore.release()
        _log(
            f"[Scheduler] Released slot job={job_id} symbol={symbol}"
        )


# ── Notification ─────────────────────────────────────────────────────────────

async def _send_scheduled_report_notifications(
    user_id: str, report_id: str, symbol: str
) -> None:
    """Send configured scheduled report notifications (email & WeCom).

    Bark is sent from the API report-persist hook so every report creation
    path behaves consistently and scheduled jobs do not double-push.
    """
    try:
        from api.services.email_report_service import send_report_email_with_retry
        from api.services.wecom_notification_service import send_report_message_with_retry

        def _load_notification_targets():
            email_user = None
            report_to_send = None
            webhook_url = None
            wecom_report_enabled = True
            with get_db_ctx() as db:
                user = db.query(UserDB).filter(UserDB.id == user_id).first()
                report = db.query(ReportDB).filter(ReportDB.id == report_id).first()
                user_cfg = auth_service.get_user_llm_config(db, user_id)
                webhook_url = auth_service.decrypt_secret(
                    getattr(user_cfg, "wecom_webhook_encrypted", None)
                )
                if report:
                    db.expunge(report)
                    report_to_send = report
                if user:
                    wecom_report_enabled = getattr(user, "wecom_report_enabled", True)
                    if getattr(user, "email_report_enabled", True):
                        db.expunge(user)
                        email_user = user
            return email_user, report_to_send, webhook_url, wecom_report_enabled

        email_user, report_to_send, webhook_url, wecom_report_enabled = (
            await asyncio.to_thread(_load_notification_targets)
        )
        if email_user and report_to_send:
            _log(f"[Scheduler] Sending email report for {symbol} to {email_user.email}")
            _create_tracked_task(
                send_report_email_with_retry(email_user, report_to_send),
                label=f"Email notification task ({symbol})",
            )
        if report_to_send and webhook_url and wecom_report_enabled:
            _log(f"[Scheduler] Sending WeCom report for {symbol}")
            _create_tracked_task(
                send_report_message_with_retry(report_to_send, webhook_url),
                label=f"WeCom notification task ({symbol})",
            )
    except Exception as e:
        logger.warning(f"[Scheduler] Notification send failed for {symbol}: {e}")


async def _send_openclaw_callback(
    user_id: str, report_id: str, symbol: str, trade_date: str, horizon: str, source: str
) -> None:
    """Send OpenClaw callback after scheduled analysis completion.

    [B-002] Loads report result_data and sends a structured payload to the
    configured OpenClaw callback URL.  OpenClaw (主控 AI) decides whether
    to push to 飞书 / 企业微信.
    """
    try:
        from api.services.openclaw_callback_service import (
            is_openclaw_callback_enabled,
            notify_openclaw_on_report_completion,
        )

        if not is_openclaw_callback_enabled():
            return

        def _load_report_payload() -> dict | None:
            with get_db_ctx() as db:
                report = db.query(ReportDB).filter(ReportDB.id == report_id).first()
                if report:
                    if not isinstance(report.result_data, dict):
                        report.result_data = {}
                    # [B-002-R2] only fields that exist on ReportDB schema;
                    # horizon/analysis_summary/opinion are not ORM columns and
                    # raised AttributeError on read. horizon comes from the
                    # task argument; readiness is parsed by the callback
                    # service from the persisted final_trade_decision block.
                    return {
                        "id": report.id,
                        "symbol": report.symbol,
                        "trade_date": report.trade_date,
                        "result_data": report.result_data,
                        "decision": report.decision,
                        "direction": report.direction,
                        "research_direction": report.research_direction,
                        "execution_action": report.execution_action,
                        "action_label": report.action_label,
                        "confidence": report.confidence,
                        "risk_items": report.risk_items,
                        "key_metrics": report.key_metrics,
                        "final_trade_decision": report.final_trade_decision,
                        "trader_investment_plan": report.trader_investment_plan,
                        "investment_plan": report.investment_plan,
                    }
                return None

        report_obj = await asyncio.to_thread(_load_report_payload)
        result_data = report_obj.get("result_data") if isinstance(report_obj, dict) else None
        notify_openclaw_on_report_completion(
            report_id=report_id,
            symbol=symbol,
            trade_date=trade_date,
            user_id=user_id,
            horizon=horizon,
            source=source,
            report_obj=report_obj if isinstance(report_obj, dict) else None,
            result_data=result_data,
        )
        _log(f"[Scheduler] OpenClaw callback queued for {symbol}")
    except Exception as e:
        logger.warning(f"[Scheduler] OpenClaw callback failed for {symbol}: {e}")


# [M-010] feishu_notification_confirmation
async def _send_feishu_notification_draft(
    user_id: str, report_id: str, symbol: str
) -> None:
    """Auto-generate pending feishu notification drafts after scheduled analysis.

    Unlike email/WeCom which send immediately, feishu notifications are stored
    as 'pending_confirmation' for human review before sending.
    """
    try:
        from api.services.feishu_webhook_service import is_feishu_webhook_enabled
        from api.services.notification_confirmation_service import generate_pending

        if not is_feishu_webhook_enabled():
            return

        def _generate():
            with get_db_ctx() as db:
                return generate_pending(db, user_id, channel="feishu")

        result = await asyncio.to_thread(_generate)
        generated = result.get("generated_count", 0)
        if generated > 0:
            _log(f"[Scheduler] Generated {generated} feishu notification draft(s) for {symbol}")
    except Exception as e:
        logger.warning(f"[Scheduler] Feishu notification draft failed for {symbol}: {e}")


# ── Single scheduled analysis execution ──────────────────────────────────────

async def _run_scheduled_analysis_once(
    task: dict,
    requested_trade_date: str,
    job_id: str,
    *,
    mark_schedule_run: bool,
) -> None:
    """Execute one scheduled analysis, optionally recording it as the daily run."""
    task_id = task["id"]
    user_id = task["user_id"]
    symbol = task["symbol"]
    horizon = task.get("horizon") or "short"

    actual_trade_date = _resolve_scheduled_trade_date(requested_trade_date)
    _log(f"[Scheduler] {symbol} trade_date={actual_trade_date} (requested={requested_trade_date})")

    scheduled_context_token = set_scheduled_task_context(True)

    def _build_request_sync():
        with get_db_ctx() as db:
            scheduled_user_context = task.get("manual_user_context") or _build_imported_user_context(
                db, user_id, symbol
            )
            return _build_scheduled_analyze_request(
                db=db,
                user_id=user_id,
                symbol=symbol,
                horizon=horizon,
                trade_date=actual_trade_date,
                scheduled_user_context=scheduled_user_context,
            )

    def _record_success_sync():
        with get_db_ctx() as db:
            if mark_schedule_run:
                scheduled_service.mark_run_success(db, task_id, requested_trade_date, job_id)
            else:
                scheduled_service.record_manual_test_result(db, task_id, "success", report_id=job_id)

    def _record_failure_sync():
        with get_db_ctx() as db:
            if mark_schedule_run:
                scheduled_service.mark_run_failed(db, task_id, requested_trade_date)
            else:
                scheduled_service.record_manual_test_result(db, task_id, "failed")
    try:
        async with _concurrency_slot(job_id, symbol):
            req = await asyncio.to_thread(_build_request_sync)

            await _run_job(
                job_id,
                req,
                False,
                True,
                user_id,
                "scheduled" if mark_schedule_run else "scheduled_manual",
            )
        job_state = _get_job(job_id)
        if job_state.get("status") == "failed":
            raise RuntimeError(job_state.get("error") or f"scheduled analysis job {job_id} failed")
        await asyncio.to_thread(_record_success_sync)
        _log(f"[Scheduler] Completed {symbol}")

        await _send_scheduled_report_notifications(user_id, job_id, symbol)

        # [B-002] OpenClaw callback: notify 主控 AI so it can decide whether to push to 飞书
        await _send_openclaw_callback(
            user_id, job_id, symbol, actual_trade_date, horizon,
            "scheduled" if mark_schedule_run else "scheduled_manual",
        )

        # [M-010] Feishu webhook: auto-generate pending notification drafts
        await _send_feishu_notification_draft(user_id, job_id, symbol)
    except Exception as e:
        logger.error(f"[Scheduler] Failed {symbol}: {e}\n{traceback.format_exc()}")
        try:
            await asyncio.to_thread(_record_failure_sync)
        except Exception as db_exc:
            logger.error(f"[Scheduler] Could not record failure: {db_exc}")
    finally:
        reset_scheduled_task_context(scheduled_context_token)


async def _run_scheduled_job(task: dict, trade_date: str):
    """Execute a single scheduled analysis job.

    Args:
        task: dict with keys id, user_id, symbol, horizon (plain values,
              not an ORM instance, to avoid DetachedInstanceError).
        trade_date: YYYY-MM-DD string.
    """
    user_id = task["user_id"]
    symbol = task["symbol"]

    _log(f"[Scheduler] Running {symbol} for user={user_id}")
    job_id = uuid4().hex
    _log(f"[Scheduler] {symbol} runtime_tier=FULL_TA cost_risk=high estimated_calls=20 (user-initiated scheduled task)")  # [PERF-001] [PERF-004]
    try:
        await _run_scheduled_analysis_once(
            task,
            trade_date,
            job_id,
            mark_schedule_run=True,
        )
    finally:
        get_job_store().delete_job(job_id)


# ── Scheduler loop ───────────────────────────────────────────────────────────

async def _scheduler_loop():
    """Background loop: check every minute for scheduled tasks to trigger.

    Each task has its own trigger_time (HH:MM). The scheduler runs on trading
    days only, including intraday watch points and post-close reviews. Tasks
    are triggered when current time >= task.trigger_time and the task hasn't
    run today yet.
    """
    from tradingagents.dataflows.trade_calendar import is_cn_trading_day
    from zoneinfo import ZoneInfo

    _log("[Scheduler] Loop started.")
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
            today = now.strftime("%Y-%m-%d")
            current_hhmm = now.strftime("%H:%M")

            if not SCHEDULER_ENABLED:
                continue
            if not is_cn_trading_day(today):
                continue
            time_val = now.hour * 60 + now.minute
            if not SCHEDULER_RUN_INTRADAY and 8 * 60 < time_val < 20 * 60:
                continue

            def _claim_pending_tasks():
                with get_db_ctx() as db:
                    tasks = scheduled_service.get_pending_tasks(db, today, current_hhmm)
                    if not tasks:
                        return []
                    if SCHEDULER_ALLOWED_TRIGGER_TIMES:
                        tasks = [
                            task for task in tasks
                            if (task.trigger_time or "20:00") in SCHEDULER_ALLOWED_TRIGGER_TIMES
                        ]
                    if SCHEDULER_MAX_TASKS_PER_TICK > 0:
                        tasks = tasks[:SCHEDULER_MAX_TASKS_PER_TICK]
                    if not tasks:
                        return []
                    snapshots = [
                        {
                            "id": task.id,
                            "user_id": task.user_id,
                            "symbol": task.symbol,
                            "horizon": task.horizon,
                            "trigger_time": task.trigger_time or "20:00",
                        }
                        for task in tasks
                    ]
                    if SCHEDULER_DRY_RUN:
                        return snapshots
                    for task in tasks:
                        task.last_run_date = today
                        task.last_run_status = "running"
                    db.commit()
                    return snapshots

            task_snapshots = await asyncio.to_thread(_claim_pending_tasks)
            if not task_snapshots:
                continue
            if SCHEDULER_DRY_RUN:
                _log(
                    "[Scheduler] Dry run: would launch %s task(s): %s"
                    % (
                        len(task_snapshots),
                        ", ".join(
                            f"{snap['symbol']}@{snap.get('trigger_time', '20:00')}"
                            for snap in task_snapshots
                        ),
                    )
                )
                continue

            _log(f"[Scheduler] Launching {len(task_snapshots)} tasks (staggered)")
            for i, snap in enumerate(task_snapshots):
                if i > 0:
                    await asyncio.sleep(1)
                _create_tracked_task(_run_scheduled_job(snap, today))

        except Exception as e:
            logger.error(f"[Scheduler] Error: {e}")


# ── Stale task recovery ──────────────────────────────────────────────────────

# [DATA-026] db_hygiene_check — single read-only warning on scheduler startup.
# Never triggers TA / LLM. P0 (test users would be executed) is logged at ERROR
# so operators can alert on it; the scheduler keeps running real-user tasks so
# long as the pending-task filter still excludes @test.com.
def _warn_db_hygiene_on_startup() -> None:
    try:
        from api.services.db_hygiene_service import (
            log_startup_hygiene_warning,
            run_db_hygiene_check,
        )

        report = run_db_hygiene_check()
        log_startup_hygiene_warning(report, logger=logger)
    except Exception as exc:  # pragma: no cover - defensive, never break startup
        logger.warning(
            "[Scheduler] DB hygiene check failed (non-blocking): %s", exc
        )


def _recover_stale_tasks():
    """Reset tasks stuck in 'running' state (from previous crash/restart)."""
    with get_db_ctx() as db:
        stale = (
            db.query(ScheduledAnalysisDB)
            .filter(ScheduledAnalysisDB.last_run_status == "running")
            .all()
        )
        if stale:
            recovered_count = 0
            reset_count = 0
            for item in stale:
                has_report = (
                    item.last_report_id
                    and item.last_run_date
                    and db.query(ReportDB)
                    .filter(
                        ReportDB.id == item.last_report_id,
                        ReportDB.status == "completed",
                        ReportDB.created_at >= item.last_run_date,
                    )
                    .first()
                )
                if has_report:
                    item.last_run_status = "success"
                    recovered_count += 1
                else:
                    item.last_run_status = "stale"
                    item.last_run_date = None
                    reset_count += 1
            db.commit()
            _log(
                f"[Scheduler] Reset {len(stale)} stale 'running' tasks on startup "
                f"(recovered={recovered_count}, reset_to_stale={reset_count})."
            )
        report_reset = report_service.recover_stale_active_reports(db)
        if report_reset["total"]:
            _log(
                "[Reports] Recovered %s stale active reports on startup (marked failed)."
                % report_reset["total"]
            )


# ── Startup / main ───────────────────────────────────────────────────────────

async def _startup():
    """Initialize DB, pre-load caches, recover stale tasks, then run the loop."""
    global _semaphore, _executor

    network_timeout = float(os.getenv("TA_SOCKET_DEFAULT_TIMEOUT", "60"))
    install_default_network_timeout(network_timeout)
    _log(
        "[Scheduler] Default socket and requests timeout set to "
        f"{network_timeout:g}s."
    )

    # Each scheduled `_run_job` fans out many `asyncio.to_thread` calls (DB
    # writes, akshare data collection, LLM extraction). The CPython default
    # of `min(32, cpu_count + 4)` is too small to absorb concurrent jobs +
    # the per-tick DB transaction the scheduler loop now runs in to_thread.
    try:
        loop = asyncio.get_running_loop()
        executor_workers = int(
            os.getenv("ASYNCIO_DEFAULT_EXECUTOR_WORKERS", str(max(64, SCHEDULER_CONCURRENCY * 16)))
        )
        loop.set_default_executor(
            ThreadPoolExecutor(
                max_workers=executor_workers,
                thread_name_prefix="ta-sched-asyncio",
            )
        )
        _log(f"[Scheduler] Default asyncio executor set to {executor_workers} workers.")
    except Exception as exc:
        _log(f"[Scheduler] Could not configure default asyncio executor: {exc}")

    init_db()
    _log("Database initialized.")

    _semaphore = asyncio.Semaphore(SCHEDULER_CONCURRENCY)
    _log(
        "[Scheduler] Config enabled=%s dry_run=%s concurrency=%s max_per_tick=%s "
        "run_intraday=%s allowed_times=%s"
        % (
            SCHEDULER_ENABLED,
            SCHEDULER_DRY_RUN,
            SCHEDULER_CONCURRENCY,
            SCHEDULER_MAX_TASKS_PER_TICK,
            SCHEDULER_RUN_INTRADAY,
            sorted(SCHEDULER_ALLOWED_TRIGGER_TIMES) or "all",
        )
    )

    _executor = ThreadPoolExecutor(max_workers=SCHEDULER_CONCURRENCY + 2)

    # Recover stale tasks from previous run
    _recover_stale_tasks()

    # [DATA-026] db_hygiene_check — emit one consolidated warning block so
    # operators know whether the prod DB has accumulated test-account rows
    # and whether get_pending_tasks still filters them out. Read-only.
    _warn_db_hygiene_on_startup()

    # Pre-load trade calendar (uses mini_racer/V8 which is not thread-safe)
    from tradingagents.dataflows.trade_calendar import _load_cn_trade_dates

    _load_cn_trade_dates()
    _log("Trade calendar pre-loaded.")

    # Pre-load stock + ETF name map
    from api.main import _load_cn_stock_map

    await asyncio.to_thread(_load_cn_stock_map)
    _log("Stock map pre-loaded on startup.")

    # Run the scheduler loop (blocks until cancelled)
    await _scheduler_loop()


def main():
    """Entry point for ``python -m scheduler.main``."""
    _log("[Scheduler] Starting standalone scheduler process ...")
    try:
        asyncio.run(_startup())
    except KeyboardInterrupt:
        _log("[Scheduler] Stopped by user.")


# Alias for pyproject.toml script entry (must be sync)
sync_main = main


if __name__ == "__main__":
    main()
