#!/usr/bin/env python3
# [M-002] auto_dev_report_index
# [V-002] nightly_acceptance_report
# [V-011] nightly_auto_dev_acceptance
"""
Scan docs/task_runs/ and generate a nightly dev report in docs/auto_dev_reports/YYYY-MM-DD.md.

V-002 extends M-002 with:
  - Candidate sample replay (VCP, event catalyst, fund flow, liquidity, no-strategy, risk)
  - Ready queue status from TASKS.md
  - Test results summary from test log files
  - "任务池不足" warning when ready queue is empty

V-011 extends V-002 with acceptance / endurance checks:
  - Done task consistency: each done task in the report window is checked for
    run archive / review / DEVLOG entry presence.
  - Ready queue endurance: estimate how long the remaining ready queue can
    sustain the nightly auto-dev loop based on priority buckets.
  - Explicit "ready 队列为空" notice and "fixture/本地数据能生成日报" acceptance
    smoke.

Usage:
    python scripts/summarize_auto_dev_runs.py [--date YYYY-MM-DD] [--dry-run] [--repo-dir PATH]
                                              [--with-sample-replay]
                                              [--with-consistency-check]

Constraints:
    - Read-only: only reads docs/task_runs/, docs/reviews/, git log, TASKS.md
    - No model calls, no stock analysis
    - No API keys or sensitive logs in output
    - Redacts any leaked keys from included content
    - Never modifies TASKS.md status (only reads)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_REDACT_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"(api[_-]?key[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(authorization:\s*bearer\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(token[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(password[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(secret[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, repl in _REDACT_PATTERNS:
        text = pat.sub(repl, text)
    return text


# ── [V-002] nightly_acceptance_report: Ready queue parsing ──

def parse_ready_queue(tasks_md_path: Path) -> list[dict[str, str]]:
    """Parse docs/TASKS.md to extract tasks with status=ready.

    Returns list of dicts with keys: task_id, title, priority.
    """
    if not tasks_md_path.exists():
        return []

    ready_tasks: list[dict[str, str]] = []
    text = tasks_md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current_task_id = ""
    current_title = ""
    current_priority = ""
    current_status = ""

    for line in lines:
        header_match = re.match(r"^###\s+([A-Z]+-[\w-]+)\s*:\s*(.+)$", line)
        if header_match:
            if current_task_id and current_status == "ready":
                ready_tasks.append({
                    "task_id": current_task_id,
                    "title": current_title.strip(),
                    "priority": current_priority,
                })
            current_task_id = header_match.group(1)
            current_title = header_match.group(2)
            current_priority = ""
            current_status = ""
            continue

        if current_task_id:
            status_match = re.match(r"^-\s+\*\*状态\*\*\s*[:：]\s*(.+)$", line)
            if status_match:
                current_status = status_match.group(1).strip()
            priority_match = re.match(r"^-\s+\*\*优先级\*\*\s*[:：]\s*(.+)$", line)
            if priority_match:
                current_priority = priority_match.group(1).strip()

    if current_task_id and current_status == "ready":
        ready_tasks.append({
            "task_id": current_task_id,
            "title": current_title.strip(),
            "priority": current_priority,
        })

    return ready_tasks


# ── [V-011] nightly_auto_dev_acceptance: Done task consistency check ──

def parse_done_tasks_for_ids(
    tasks_md_path: Path, target_ids: "set[str] | None" = None
) -> list[dict[str, str]]:
    """Parse docs/TASKS.md to extract tasks whose status line begins with ``done``.

    If ``target_ids`` is provided, only tasks whose ID appears in that set are
    returned (used to scope the consistency check to today's runs).

    Returns list of dicts with keys: task_id, title, priority, status (raw text).
    """
    if not tasks_md_path.exists():
        return []

    done_tasks: list[dict[str, str]] = []
    text = tasks_md_path.read_text(encoding="utf-8")

    current_task_id = ""
    current_title = ""
    current_priority = ""
    current_status = ""

    for line in text.splitlines():
        header_match = re.match(r"^###\s+([A-Z]+-[\w-]+)\s*:\s*(.+)$", line)
        if header_match:
            if (
                current_task_id
                and current_status.lower().startswith("done")
                and (target_ids is None or current_task_id in target_ids)
            ):
                done_tasks.append({
                    "task_id": current_task_id,
                    "title": current_title.strip(),
                    "priority": current_priority,
                    "status": current_status,
                })
            current_task_id = header_match.group(1)
            current_title = header_match.group(2)
            current_priority = ""
            current_status = ""
            continue

        if current_task_id:
            status_match = re.match(r"^-\s+\*\*状态\*\*\s*[:：]\s*(.+)$", line)
            if status_match:
                current_status = status_match.group(1).strip()
            priority_match = re.match(r"^-\s+\*\*优先级\*\*\s*[:：]\s*(.+)$", line)
            if priority_match:
                current_priority = priority_match.group(1).strip()

    if (
        current_task_id
        and current_status.lower().startswith("done")
        and (target_ids is None or current_task_id in target_ids)
    ):
        done_tasks.append({
            "task_id": current_task_id,
            "title": current_title.strip(),
            "priority": current_priority,
            "status": current_status,
        })

    return done_tasks


def check_done_task_consistency(
    done_tasks: list[dict[str, str]],
    runs: list[TaskRun],
    reviews_dir: Path,
    devlog_path: Path,
) -> list[dict[str, object]]:
    """For each done task, check whether run archive / review / DEVLOG exist.

    Each result dict has keys:
      - task_id
      - has_run_archive (bool): any task_runs/<task_id>-* directory found
      - has_review (bool): a review file in docs/reviews/ contains the task_id
      - has_devlog_entry (bool): the DEVLOG.md mentions the task_id
      - missing (list[str]): human-readable list of missing pieces
    """
    run_task_ids = {r.task_id for r in runs if r.task_id}

    review_hits: dict[str, bool] = {}
    if reviews_dir.exists():
        review_blobs: list[tuple[str, str]] = []
        for f in sorted(reviews_dir.iterdir()):
            if not f.is_file():
                continue
            try:
                review_blobs.append((f.name, f.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
        for tid in run_task_ids:
            review_hits[tid] = any(tid in blob for _, blob in review_blobs)
    else:
        review_hits = {tid: False for tid in run_task_ids}

    devlog_text = ""
    if devlog_path.exists():
        try:
            devlog_text = devlog_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            devlog_text = ""

    results: list[dict[str, object]] = []
    for t in done_tasks:
        tid = t["task_id"]
        has_run = tid in run_task_ids
        has_review = review_hits.get(tid, False)
        has_devlog = tid in devlog_text

        missing: list[str] = []
        if not has_run:
            missing.append("run archive")
        if not has_review:
            missing.append("review")
        if not has_devlog:
            missing.append("DEVLOG")

        results.append({
            "task_id": tid,
            "title": t.get("title", ""),
            "has_run_archive": has_run,
            "has_review": has_review,
            "has_devlog_entry": has_devlog,
            "missing": missing,
        })

    return results


# ── [V-011] nightly_auto_dev_acceptance: Ready queue endurance estimate ──

# Per-task runtime budget (minutes) by priority. Range = (min, max).
_PRIORITY_RUNTIME_MINUTES: dict[str, tuple[int, int]] = {
    "P0": (30, 60),
    "P1": (20, 40),
    "P2": (10, 30),
    "P3": (5, 15),
}
_DEFAULT_RUNTIME_MINUTES: tuple[int, int] = (15, 30)


def estimate_ready_endurance(
    ready_queue: "list[dict[str, str]] | None",
) -> dict[str, object]:
    """Estimate how long the ready queue can sustain the auto-dev loop.

    Returns dict with:
      - count: int
      - total_min_minutes / total_max_minutes: int
      - hours_range: str, e.g. "1.0-2.5h"
      - empty: bool
      - by_priority: dict[priority] -> count
      - summary: str, human-readable Chinese summary line
    """
    if not ready_queue:
        return {
            "count": 0,
            "total_min_minutes": 0,
            "total_max_minutes": 0,
            "hours_range": "0h",
            "empty": True,
            "by_priority": {},
            "summary": "Ready 队列为空，夜间 cron 不应空转。",
        }

    total_min = 0
    total_max = 0
    by_priority: dict[str, int] = {}

    for task in ready_queue:
        prio_raw = (task.get("priority") or "").strip()
        # Extract first P-level token (e.g. "P1" out of "P1（高）")
        token = ""
        for tok in re.split(r"[\s,，、（）()]+", prio_raw):
            if tok.upper() in _PRIORITY_RUNTIME_MINUTES:
                token = tok.upper()
                break
        lo, hi = _PRIORITY_RUNTIME_MINUTES.get(token, _DEFAULT_RUNTIME_MINUTES)
        total_min += lo
        total_max += hi
        by_priority[token or "OTHER"] = by_priority.get(token or "OTHER", 0) + 1

    def _fmt_hours(minutes: int) -> str:
        if minutes <= 0:
            return "0h"
        hours = minutes / 60.0
        if hours >= 1.0:
            return f"{hours:.1f}h"
        return f"{minutes}m"

    hours_range = f"{_fmt_hours(total_min)}-{_fmt_hours(total_max)}"
    summary = (
        f"Ready 队列剩余 {len(ready_queue)} 个任务，"
        f"预计可续航约 {hours_range}（{total_min}-{total_max} 分钟）。"
    )

    return {
        "count": len(ready_queue),
        "total_min_minutes": total_min,
        "total_max_minutes": total_max,
        "hours_range": hours_range,
        "empty": False,
        "by_priority": by_priority,
        "summary": summary,
    }


# ── [AUTO-004] auto_dev_runtime_budget ──────────────────────────────────────
#
# AUTO-004 extends V-011's static priority budget with historical runtime
# estimation, fail-stop strategy verification, and proposed-suggestion
# generation when the ready queue cannot sustain the nightly target window.
#
# Constraints honoured:
#   - Read-only: never modifies TASKS.md status or auto_dev_loop.sh.
#   - Does not change cron time, does not auto-start OpenCode, does not
#     bypass Codex review.
#   - Does not affect the existing auto_dev_loop pickup logic.

# Nightly endurance targets (hours). The user wants ~3h of nightly runs; a
# ready queue projected below ``_LOW_ENDURANCE_HOURS`` triggers a proposed
# task suggestion so the pool does not idle-spin.
_TARGET_ENDURANCE_HOURS = 3.0
_LOW_ENDURANCE_HOURS = 2.0

# Confidence threshold: if historical sample size per priority bucket is
# smaller than this, we blend with the static V-011 budget instead of
# trusting a noisy average outright.
_MIN_HISTORY_SAMPLES = 2

# Sanity caps for elapsed time parsing. Auto-dev OpenCode timeout is 1800s
# and tests 900s, so a single round is bounded by ~2700s + overhead. Anything
# outside this window is treated as a parsing/clock-skew artefact and dropped.
_MAX_PLAUSIBLE_ELAPSED_SECONDS = 6 * 3600  # 6h (multi-round worst case)
_MIN_PLAUSIBLE_ELAPSED_SECONDS = 30        # 30s


def _parse_run_timestamp(ts: str) -> Optional[datetime]:
    """Parse an auto-dev run timestamp of the form ``YYYY-MM-DD_HH:MM:SS``.

    Returns a timezone-naive ``datetime`` or ``None`` if the value is empty or
    does not match the expected format.
    """
    if not ts:
        return None
    ts = ts.strip()
    for fmt in ("%Y-%m-%d_%H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def _extract_priority_token(priority_raw: str) -> str:
    """Extract the canonical P-level token (P0/P1/P2/P3) from a raw string."""
    for tok in re.split(r"[\s,，、（）()]+", (priority_raw or "").strip()):
        if tok.upper() in _PRIORITY_RUNTIME_MINUTES:
            return tok.upper()
    return ""


def compute_historical_runtimes(runs: list[TaskRun]) -> dict[str, object]:
    """Compute average elapsed time per priority from historical task runs.

    Each historical ``TaskRun`` carries ``started_at`` (from task.md) and
    ``finished_at`` (from summary.md). The elapsed seconds are bucketed by
    priority. Out-of-window or unparseable entries are dropped.

    Returns:
        - ``by_priority``: ``{priority: {"count", "avg_seconds", "min", "max"}}``
        - ``overall``: ``{"count", "avg_seconds"}``
        - ``has_history``: bool — True when at least one valid sample exists.
    """
    by_priority: dict[str, list[int]] = {}
    all_elapsed: list[int] = []

    for run in runs:
        started = _parse_run_timestamp(run.started_at)
        finished = _parse_run_timestamp(run.finished_at)
        if started is None or finished is None:
            continue
        elapsed = int((finished - started).total_seconds())
        if elapsed < _MIN_PLAUSIBLE_ELAPSED_SECONDS:
            continue
        if elapsed > _MAX_PLAUSIBLE_ELAPSED_SECONDS:
            # Clock skew or a stale run that never recorded finished_at
            # correctly — skip rather than poison the average.
            continue
        prio = _extract_priority_token(run.priority) or "OTHER"
        by_priority.setdefault(prio, []).append(elapsed)
        all_elapsed.append(elapsed)

    def _stats(samples: list[int]) -> dict[str, int | float]:
        return {
            "count": len(samples),
            "avg_seconds": (sum(samples) / len(samples)) if samples else 0.0,
            "min": min(samples) if samples else 0,
            "max": max(samples) if samples else 0,
        }

    return {
        "by_priority": {p: _stats(s) for p, s in by_priority.items()},
        "overall": _stats(all_elapsed),
        "has_history": bool(all_elapsed),
    }


def _blend_estimate(
    static_minutes: tuple[int, int],
    historical_seconds: float | None,
    samples: int,
) -> tuple[float, float]:
    """Blend the static V-011 budget with a historical average.

    When ``samples >= _MIN_HISTORY_SAMPLES`` the historical average (in
    minutes) replaces the static midpoint; the static range is still used to
    derive a conservative spread. With insufficient history we fall back to
    the static range verbatim.
    """
    static_lo, static_hi = static_minutes
    if historical_seconds is None or samples < _MIN_HISTORY_SAMPLES:
        return (float(static_lo), float(static_hi))
    hist_min = historical_seconds / 60.0
    # Spread derived from the static range width, clamped so the historical
    # midpoint stays centred. This keeps the band informative without
    # trusting a single noisy sample.
    half_spread = max((static_hi - static_lo) / 2.0, 5.0)
    lo = max(1.0, hist_min - half_spread)
    hi = hist_min + half_spread
    return (lo, hi)


def estimate_ready_endurance_from_history(
    ready_queue: "list[dict[str, str]] | None",
    historical: dict[str, object],
    *,
    target_hours: float = _TARGET_ENDURANCE_HOURS,
    low_hours: float = _LOW_ENDURANCE_HOURS,
) -> dict[str, object]:
    """Estimate ready-queue endurance using historical averages.

    Blends V-011 static budgets with AUTO-004 historical per-priority
    averages. Falls back to the static budget when a priority bucket lacks
    enough samples.

    Returns the same shape as :func:`estimate_ready_endurance`, plus:
      - ``method``: ``"historical"`` or ``"static_fallback"``
      - ``target_hours`` / ``low_hours``
      - ``meets_target`` / ``low_endurance`` flags
      - ``per_task``: list of per-task estimates (id, priority, est_minutes)
    """
    if not ready_queue:
        return {
            "count": 0,
            "total_min_minutes": 0,
            "total_max_minutes": 0,
            "hours_range": "0h",
            "empty": True,
            "by_priority": {},
            "summary": "Ready 队列为空，夜间 cron 不应空转。",
            "method": "static_fallback",
            "target_hours": target_hours,
            "low_hours": low_hours,
            "meets_target": False,
            "low_endurance": True,
            "per_task": [],
        }

    hist_by_prio = historical.get("by_priority", {}) or {}
    overall_hist = historical.get("overall", {}) or {}
    has_history = bool(historical.get("has_history", False))

    total_min = 0.0
    total_max = 0.0
    by_priority: dict[str, int] = {}
    per_task: list[dict[str, object]] = []

    for task in ready_queue:
        prio = _extract_priority_token(task.get("priority", ""))
        bucket = prio or "OTHER"
        static = _PRIORITY_RUNTIME_MINUTES.get(prio, _DEFAULT_RUNTIME_MINUTES)
        hist = hist_by_prio.get(prio) if prio else None
        hist_seconds = hist.get("avg_seconds") if hist else None
        samples = hist.get("count", 0) if hist else 0

        lo, hi = _blend_estimate(static, hist_seconds, samples)
        # If no per-priority history but we have an overall average, use it as
        # a weak signal for the midpoint only when samples are sufficient.
        if (hist_seconds is None or samples < _MIN_HISTORY_SAMPLES) and has_history:
            overall_samples = overall_hist.get("count", 0)
            if overall_samples >= _MIN_HISTORY_SAMPLES:
                overall_min = overall_hist.get("avg_seconds", 0) / 60.0
                half_spread = max((static[1] - static[0]) / 2.0, 5.0)
                lo = max(1.0, overall_min - half_spread)
                hi = overall_min + half_spread

        total_min += lo
        total_max += hi
        by_priority[bucket] = by_priority.get(bucket, 0) + 1
        per_task.append({
            "task_id": task.get("task_id", ""),
            "title": task.get("title", ""),
            "priority": prio or "OTHER",
            "est_min_minutes": round(lo, 1),
            "est_max_minutes": round(hi, 1),
            "source": "historical" if samples >= _MIN_HISTORY_SAMPLES else "static",
        })

    def _fmt_hours(minutes: float) -> str:
        if minutes <= 0:
            return "0h"
        hours = minutes / 60.0
        if hours >= 1.0:
            return f"{hours:.1f}h"
        return f"{int(minutes)}m"

    hours_range = f"{_fmt_hours(total_min)}-{_fmt_hours(total_max)}"
    est_hours_mid = (total_min + total_max) / 2.0 / 60.0
    method = "historical" if has_history else "static_fallback"

    meets_target = est_hours_mid >= target_hours
    low_endurance = est_hours_mid < low_hours

    summary = (
        f"Ready 队列剩余 {len(ready_queue)} 个任务，"
        f"基于历史耗时预计可续航约 {hours_range}（{int(total_min)}-"
        f"{int(total_max)} 分钟）。"
        if has_history
        else (
            f"Ready 队列剩余 {len(ready_queue)} 个任务，"
            f"无历史耗时样本，使用静态预算约 {hours_range}（{int(total_min)}-"
            f"{int(total_max)} 分钟）。"
        )
    )

    return {
        "count": len(ready_queue),
        "total_min_minutes": int(total_min),
        "total_max_minutes": int(total_max),
        "hours_range": hours_range,
        "empty": False,
        "by_priority": by_priority,
        "summary": summary,
        "method": method,
        "target_hours": target_hours,
        "low_hours": low_hours,
        "meets_target": meets_target,
        "low_endurance": low_endurance,
        "per_task": per_task,
    }


def sort_ready_queue_for_budget(
    ready_queue: "list[dict[str, str]] | None",
    historical: dict[str, object] | None = None,
) -> "list[dict[str, str]]":
    """Return the ready queue ordered the way auto_dev_loop would pick it.

    Mirrors the ``parse_ready_tasks`` ordering in auto_dev_loop.sh: priority
    ascending (P0 → P3), then document order. This is the order used for the
    dry-run budget projection so the report matches what the loop would
    actually consume.
    """
    if not ready_queue:
        return []
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return sorted(
        ready_queue,
        key=lambda t: (
            priority_order.get(_extract_priority_token(t.get("priority", "")), 9),
            ready_queue.index(t),
        ),
    )


def verify_fail_stop_strategy(script_path: Path) -> dict[str, object]:
    """Read-only verification that auto_dev_loop.sh still stops on failure.

    AUTO-004 acceptance requires confirming the fail-stop policy remains in
    effect. This function greps the loop script for the canonical guards and
    reports which are present. It never edits the script.

    Returns ``{"ok": bool, "checks": {name: bool}, "script": str}``.
    """
    checks = {
        "break_on_failed_tasks": False,
        "break_on_quota_exhausted": False,
        "stop_on_dirty_tree": False,
        "continue_only_on_done": False,
    }
    script_name = ""
    if script_path.exists():
        try:
            text = script_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        script_name = script_path.name

        # break_on_failed_tasks: a shared guard ``if FAILED_TASKS -gt 0; break``
        # (the QUOTA_EXHAUSTED and NEEDS_HUMAN branches both feed this counter).
        checks["break_on_failed_tasks"] = bool(
            re.search(r"FAILED_TASKS[^\n]*-gt\s*0[^\n]*\n\s*break", text, re.MULTILINE)
        ) or (
            bool(re.search(r"FAILED_TASKS[^\n]*-gt\s*0", text))
            and "break" in text
        )

        # break_on_quota_exhausted: the QUOTA_EXHAUSTED branch must either break
        # directly or increment FAILED_TASKS so the shared guard fires.
        checks["break_on_quota_exhausted"] = bool(
            re.search(r'QUOTA_EXHAUSTED', text)
        ) and (
            bool(re.search(r"QUOTA_EXHAUSTED[^\n]*\n(?:.*\n){0,8}?\s*break", text))
            or bool(re.search(r"QUOTA_EXHAUSTED[^\n]*\n(?:.*\n){0,8}?FAILED_TASKS", text))
        )

        # stop_on_dirty_tree: dirty-tree guard that exits before claiming a task.
        checks["stop_on_dirty_tree"] = bool(
            re.search(r"[Ww]orking tree dirty", text)
        )

        # continue_only_on_done: only the DONE branch issues ``continue``; the
        # failure branches fall through to the shared break guard (no continue).
        checks["continue_only_on_done"] = bool(
            re.search(r'RESULT_STATUS"\s*=\s*"DONE"', text)
        ) and bool(re.search(r'DONE"[^\n]*\n(?:.*\n){0,10}?\s*continue', text))

    return {
        "ok": all(checks.values()),
        "checks": checks,
        "script": script_name,
    }


def generate_low_endurance_proposal(
    endurance: dict[str, object],
    *,
    target_hours: float = _TARGET_ENDURANCE_HOURS,
    low_hours: float = _LOW_ENDURANCE_HOURS,
) -> str:
    """Generate a ``proposed`` task-suggestion block when endurance is low.

    AUTO-004 requires that when the ready queue projects below ``low_hours``
    (default 2h) we surface proposed supplement suggestions so the nightly
    cron does not idle-spin. This only emits a *suggestion* markdown block —
    it never auto-promotes tasks to ``ready`` and never edits TASKS.md.
    """
    if endurance.get("empty", False):
        return (
            "## 续航补充建议（ready 队列为空）\n\n"
            "> Ready 队列为空，夜间 cron 不应空转。\n"
            "> 建议人工审核 `docs/task_suggestions/` 并将合适的 `proposed` "
            "任务转为 `ready`，或新增任务。\n"
        )

    est_hours = (
        (endurance.get("total_min_minutes", 0) + endurance.get("total_max_minutes", 0))
        / 2.0
        / 60.0
    )
    if not endurance.get("low_endurance", est_hours < low_hours):
        return ""

    deficit_min = max(0, int((target_hours - est_hours) * 60))
    return (
        "## 续航补充建议（ready 不足）\n\n"
        f"> 当前 ready 队列预计续航约 {est_hours:.1f}h，"
        f"低于 {low_hours:.1f}h 阈值，目标 {target_hours:.1f}h。\n"
        f"> 建议补充约 {deficit_min} 分钟等价任务（参考历史每任务耗时），\n"
        "> 以避免夜间 cron 因任务池耗尽而空转。\n"
        "> 人工审核后将 `proposed` 任务转为 `ready`，或新增任务到 `docs/TASKS.md`。\n"
    )


def format_runtime_budget_section(
    endurance: dict[str, object],
    historical: dict[str, object],
    fail_stop: dict[str, object],
    *,
    target_hours: float = _TARGET_ENDURANCE_HOURS,
    low_hours: float = _LOW_ENDURANCE_HOURS,
) -> str:
    """Format the AUTO-004 runtime budget + fail-stop regression section."""
    lines: list[str] = []
    lines.append("## 续航预算与失败即停回归")
    lines.append("")

    # Historical runtime table
    lines.append("### 历史每任务耗时（AUTO-004）")
    lines.append("")
    if historical.get("has_history"):
        overall = historical.get("overall", {}) or {}
        lines.append("| 维度 | 样本数 | 平均(秒) | 平均(分) | 最小(秒) | 最大(秒) |")
        lines.append("|------|--------|----------|----------|----------|----------|")
        lines.append(
            f"| 总体 | {overall.get('count', 0)} | "
            f"{overall.get('avg_seconds', 0):.0f} | "
            f"{overall.get('avg_seconds', 0) / 60.0:.1f} | "
            f"{overall.get('min', 0)} | {overall.get('max', 0)} |"
        )
        for prio in sorted((historical.get("by_priority", {}) or {}).keys()):
            s = historical["by_priority"][prio]
            lines.append(
                f"| {prio} | {s.get('count', 0)} | "
                f"{s.get('avg_seconds', 0):.0f} | "
                f"{s.get('avg_seconds', 0) / 60.0:.1f} | "
                f"{s.get('min', 0)} | {s.get('max', 0)} |"
            )
    else:
        lines.append("> 无历史耗时样本，续航估计回退到静态优先级预算。")
    lines.append("")

    # Endurance projection
    lines.append("### 续航估计")
    lines.append("")
    if endurance.get("empty", False):
        lines.append("> Ready 队列为空，无续航。请人工补充 `ready` 任务。")
    else:
        lines.append(f"- **任务数**: {endurance.get('count', 0)}")
        lines.append(f"- **预计续航**: {endurance.get('hours_range', 'N/A')} "
                     f"({endurance.get('total_min_minutes', 0)}-"
                     f"{endurance.get('total_max_minutes', 0)} 分钟)")
        lines.append(f"- **估计方法**: {endurance.get('method', 'static_fallback')}")
        lines.append(f"- **目标续航**: {target_hours:.1f}h")
        meets = endurance.get("meets_target", False)
        lines.append(f"- **是否达标**: {'是' if meets else '否'}")
        per_task = endurance.get("per_task", []) or []
        if per_task:
            lines.append("")
            lines.append("| 任务ID | 优先级 | 预计(分) | 来源 |")
            lines.append("|--------|--------|----------|------|")
            for t in per_task:
                lo = t.get("est_min_minutes", 0)
                hi = t.get("est_max_minutes", 0)
                lines.append(
                    f"| {t.get('task_id', '')} | {t.get('priority', '')} | "
                    f"{lo:.0f}-{hi:.0f} | {t.get('source', '')} |"
                )
    lines.append("")
    lines.append(f"> {redact(endurance.get('summary', ''))}")
    lines.append("")

    # Fail-stop verification
    lines.append("### 失败即停策略回归")
    lines.append("")
    checks = fail_stop.get("checks", {}) or {}
    lines.append("| 检查项 | 状态 |")
    lines.append("|--------|------|")
    label_map = {
        "break_on_failed_tasks": "失败任务即停 (FAILED_TASKS>0 break)",
        "break_on_quota_exhausted": "配额耗尽即停 (QUOTA_EXHAUSTED break)",
        "stop_on_dirty_tree": "脏工作区即停 (dirty tree exit)",
        "continue_only_on_done": "仅 DONE 继续 (continue on DONE)",
    }
    for key in ("break_on_failed_tasks", "break_on_quota_exhausted",
                "stop_on_dirty_tree", "continue_only_on_done"):
        ok = checks.get(key, False)
        lines.append(f"| {label_map.get(key, key)} | {'生效' if ok else '缺失'} |")
    lines.append("")
    if fail_stop.get("ok"):
        lines.append("> 失败即停策略全部生效，不影响现有 auto_dev_loop 领取逻辑。")
    else:
        lines.append("> **注意**: 部分失败即停检查未通过，请复核 auto_dev_loop.sh。")
    lines.append("")

    return "\n".join(lines)


# [AUTO-005] auto_dev_runtime_budget
#
# AUTO-005 extends AUTO-004 / V-011 by surfacing a read-only DB hygiene
# snapshot (DATA-026 service) in the nightly report. The section records:
#   - db_path, total_pollution, has_p0_risk, pending_tasks_filter_ok
#   - suggested cleanup command (never auto-executed)
#   - ready queue count + estimated endurance in one consolidated block
#
# Constraints honoured:
#   - Read-only: never invokes cleanup_test_db_pollution --execute.
#   - Never writes to production tradingagents.db.
#   - P0 risks (broken @test.com filter) are surfaced but never auto-fixed.


def collect_db_hygiene_snapshot(skip_pending_tasks_check: bool = False) -> Optional[dict[str, object]]:
    """Run the DATA-026 hygiene check read-only and return its dict payload.

    Returns ``None`` when the service cannot be imported (e.g. missing dep on
    a minimal CI worker) so the report still generates without the section
    instead of crashing. The function never raises.
    """
    # When this script is invoked directly (``python scripts/...``), sys.path
    # only contains ``scripts/`` and not the project root, so the deferred
    # import ``from scripts.cleanup_test_db_pollution`` inside the service
    # would fail. Ensure project root is on the path before importing.
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    try:  # pragma: no cover - defensive import for stripped environments
        from api.services.db_hygiene_service import run_db_hygiene_check
    except Exception:
        return None
    try:
        report = run_db_hygiene_check(skip_pending_tasks_check=skip_pending_tasks_check)
        return report.to_dict()
    except Exception:
        return None

def format_db_hygiene_section(snapshot: Optional[dict[str, object]]) -> str:
    """Format the AUTO-005 DB hygiene + 续航 consolidated preflight section.

    The section is intentionally compact: operators should be able to glance
    at it and answer "is the production DB clean / is the ready queue enough
    for tonight" without opening other files.
    """
    if snapshot is None:
        return (
            "## DB hygiene 与续航门禁（AUTO-005）\n\n"
            "> hygiene 服务不可用（缺少依赖或服务导入失败），跳过该区块。\n"
        )

    lines: list[str] = []
    lines.append("## DB hygiene 与续航门禁（AUTO-005）")
    lines.append("")

    db_path = snapshot.get("db_path", "unknown")
    total_pollution = int(snapshot.get("total_pollution", 0) or 0)
    has_p0 = bool(snapshot.get("has_p0_risk", False))
    all_green = bool(snapshot.get("all_green", False))
    filter_ok = bool(snapshot.get("pending_tasks_filter_ok", True))
    counts = snapshot.get("counts", {}) or {}

    lines.append("| 维度 | 值 |")
    lines.append("|------|------|")
    lines.append(f"| DB 路径 | `{db_path}` |")
    lines.append(f"| @test.com 污染行数 | {total_pollution} |")
    lines.append(f"| pending-task 过滤 | {'生效' if filter_ok else '失效 (P0)'} |")
    lines.append(f"| all_green | {all_green} |")
    lines.append(f"| has_p0_risk | {has_p0} |")
    lines.append("")

    if has_p0 or not filter_ok:
        lines.append(
            "> **P0**: scheduler 可能执行测试用户任务，建议立即人工排查 "
            "`api/services/scheduled_service.get_pending_tasks` 与生产 DB 状态。"
        )
    elif total_pollution > 0:
        summary = ", ".join(
            f"{table}={count}"
            for table, count in sorted(counts.items())
            if isinstance(count, int) and count
        )
        lines.append(
            f"> **P1**: 检测到 {total_pollution} 行 @test.com 污染"
            f"（{summary or '分布未知'}）。"
        )
        lines.append(
            "> 建议命令（dry-run by default；需先备份）："
            "`python scripts/cleanup_test_db_pollution.py`"
        )
    else:
        lines.append("> 生产 DB hygiene 状态：all green。")
    lines.append("")

    risks = snapshot.get("risks", []) or []
    if risks:
        lines.append("风险明细：")
        lines.append("")
        lines.append("| severity | code | message |")
        lines.append("|----------|------|---------|")
        for r in risks:
            severity = r.get("severity", "?")
            code = r.get("code", "?")
            message = redact(str(r.get("message", "")))
            lines.append(f"| {severity} | {code} | {message} |")
        lines.append("")

    return "\n".join(lines)


# [AUTO-006] codex_review_watchdog
#
# AUTO-006 surfaces Codex review elapsed_ms, timed_out flags and partial_output
# metadata in the nightly report. The watchdog records a ``review-meta-roundN.json``
# next to every codex-review-roundN.txt in docs/task_runs/<run>/, even when the
# review times out or is skipped. This block aggregates those JSON files and
# renders a compact section so operators can answer at a glance:
#   - Did any review hit the watchdog?
#   - How long did reviews actually take vs the configured timeout?
#   - Was any partial output captured before killpg so the human can salvage it?
#
# Constraints honoured:
#   - Read-only: only reads review-meta-round*.json from existing run archives.
#   - Never marks a timeout as PASS — the JSON status is taken verbatim.
#   - Never silently drops an unparseable meta file: it is reported as a parse
#     failure so the underlying corruption does not hide a real timeout.


_REVIEW_META_FILENAME_RE = re.compile(r"^review-meta-round(\d+)\.json$")


def _safe_read_json(path: Path) -> Optional[dict[str, object]]:
    """Read a JSON file, returning None on any read/parse failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def parse_review_meta_for_run(run_dir: Path) -> list[dict[str, object]]:
    """Parse all ``review-meta-roundN.json`` files in a single run directory.

    Returns a list sorted by round number. Each entry augments the raw JSON
    with ``run_id`` and ``meta_path`` so the report can reference the source.
    Corrupt or missing files produce no entries (and never raise).
    """
    if not run_dir.exists():
        return []

    entries: list[dict[str, object]] = []
    for f in sorted(run_dir.iterdir()):
        m = _REVIEW_META_FILENAME_RE.match(f.name)
        if not m:
            continue
        data = _safe_read_json(f)
        if not isinstance(data, dict):
            # Record the parse failure so the report can flag the corruption
            # instead of silently hiding a possible timeout.
            entries.append({
                "run_id": run_dir.name,
                "round": int(m.group(1)),
                "meta_path": str(f),
                "parse_error": True,
            })
            continue
        data.setdefault("run_id", run_dir.name)
        data.setdefault("meta_path", str(f))
        data["parse_error"] = False
        entries.append(data)

    entries.sort(key=lambda d: int(d.get("round", 0) or 0))
    return entries


def collect_review_meta(
    task_runs_dir: Path, target_date: Optional[str] = None
) -> list[dict[str, object]]:
    """Aggregate review-meta-roundN.json across all (or date-filtered) runs.

    Each entry mirrors :func:`parse_review_meta_for_run` and additionally
    carries ``task_id`` and ``priority`` when the corresponding run has a
    parseable task.md.
    """
    if not task_runs_dir.exists():
        return []

    all_entries: list[dict[str, object]] = []
    for d in sorted(task_runs_dir.iterdir()):
        if not d.is_dir():
            continue
        # Date filter (mirror scan_task_runs' timestamp extraction)
        if target_date:
            parts = d.name.split("-")
            date_part = None
            for i, p in enumerate(parts):
                if (
                    re.match(r"^\d{8}$", p)
                    and i + 1 < len(parts)
                    and re.match(r"^\d{6}$", parts[i + 1])
                ):
                    date_part = f"{p[:4]}-{p[4:6]}-{p[6:8]}"
                    break
            if date_part != target_date:
                continue

        run_entries = parse_review_meta_for_run(d)
        if not run_entries:
            continue

        # Annotate each entry with task_id / priority parsed from task.md so
        # the report can join review timing back to the originating task.
        task_meta = parse_task_md(d / "task.md")
        task_id_raw = task_meta.get("task", "")
        task_id = task_id_raw
        m = re.match(r"^([A-Z]+-[\w-]+)\s*-\s*(.+)$", task_id_raw)
        if m:
            task_id = m.group(1).strip()
        priority = task_meta.get("priority", "")

        for entry in run_entries:
            entry.setdefault("task_id", task_id)
            entry.setdefault("priority", priority)
            all_entries.append(entry)

    return all_entries


def summarize_review_meta(
    entries: list[dict[str, object]],
) -> dict[str, object]:
    """Compute aggregate review-watchdog stats from raw meta entries.

    Returns:
      - count: total review entries
      - pass_count / fail_count / timeout_count / skipped_count
      - max_elapsed_ms / avg_elapsed_ms
      - any_timed_out: bool
      - any_partial_output: bool
      - parse_errors: int — corrupt meta files that could not be decoded
      - by_task: dict[task_id] -> {count, max_elapsed_ms, any_timed_out}
    """
    if not entries:
        return {
            "count": 0,
            "pass_count": 0,
            "fail_count": 0,
            "timeout_count": 0,
            "skipped_count": 0,
            "max_elapsed_ms": 0,
            "avg_elapsed_ms": 0.0,
            "any_timed_out": False,
            "any_partial_output": False,
            "parse_errors": 0,
            "by_task": {},
        }

    pass_c = 0
    fail_c = 0
    timeout_c = 0
    skipped_c = 0
    parse_errors = 0
    elapsed_samples: list[int] = []
    any_timeout = False
    any_partial = False
    by_task: dict[str, dict[str, object]] = {}

    for e in entries:
        if e.get("parse_error"):
            parse_errors += 1
            continue
        status = str(e.get("status", "")).upper()
        if status == "PASS":
            pass_c += 1
        elif status == "TIMEOUT":
            timeout_c += 1
            any_timeout = True
        elif status == "SKIPPED":
            skipped_c += 1
        elif status == "FAIL":
            fail_c += 1
        if bool(e.get("has_partial_output", False)):
            any_partial = True
        elapsed = int(e.get("elapsed_ms", 0) or 0)
        if elapsed > 0:
            elapsed_samples.append(elapsed)
        # Per-task aggregation
        tid = str(e.get("task_id", "") or "?")
        bucket = by_task.setdefault(tid, {
            "count": 0,
            "max_elapsed_ms": 0,
            "any_timed_out": False,
            "any_partial": False,
            "statuses": [],
        })
        bucket["count"] = int(bucket["count"]) + 1  # type: ignore[operator]
        if elapsed > int(bucket["max_elapsed_ms"] or 0):  # type: ignore[arg-type]
            bucket["max_elapsed_ms"] = elapsed
        if any_timeout and tid == str(e.get("task_id", "")):
            bucket["any_timed_out"] = bool(bucket.get("any_timed_out")) or bool(
                e.get("timed_out", False)
            )
        if bool(e.get("timed_out", False)):
            bucket["any_timed_out"] = True  # type: ignore[assignment]
        if bool(e.get("has_partial_output", False)):
            bucket["any_partial"] = True  # type: ignore[assignment]
        bucket["statuses"].append(status)  # type: ignore[union-attr]

    max_elapsed = max(elapsed_samples) if elapsed_samples else 0
    avg_elapsed = (
        sum(elapsed_samples) / len(elapsed_samples) if elapsed_samples else 0.0
    )

    return {
        "count": len(entries),
        "pass_count": pass_c,
        "fail_count": fail_c,
        "timeout_count": timeout_c,
        "skipped_count": skipped_c,
        "max_elapsed_ms": max_elapsed,
        "avg_elapsed_ms": avg_elapsed,
        "any_timed_out": any_timeout,
        "any_partial_output": any_partial,
        "parse_errors": parse_errors,
        "by_task": by_task,
    }


def _fmt_ms(ms: int | float) -> str:
    """Render milliseconds as a compact human-readable duration."""
    try:
        ms_int = int(ms)
    except (TypeError, ValueError):
        return "0s"
    if ms_int <= 0:
        return "0s"
    if ms_int < 1000:
        return f"{ms_int}ms"
    seconds = ms_int / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60.0
    return f"{hours:.2f}h"


def format_codex_review_watchdog_section(
    entries: list[dict[str, object]],
    summary: Optional[dict[str, object]] = None,
) -> str:
    """Render the AUTO-006 Codex review watchdog section for the nightly report.

    The section is intentionally compact: one summary line, one table of
    per-task rows, and (if any) a P0/P1 callout for timeouts or parse errors.
    The block always renders even when ``entries`` is empty so operators can
    confirm the watchdog is wired in.
    """
    if summary is None:
        summary = summarize_review_meta(entries)

    lines: list[str] = []
    lines.append("## Codex Review 超时 watchdog（AUTO-006）")
    lines.append("")

    count = int(summary.get("count", 0))
    if count == 0:
        lines.append(
            "> 本报告窗口内无 ``review-meta-roundN.json`` 记录：本次 cron 未触发"
            " Codex review，或运行版本 predates AUTO-006。"
        )
        lines.append("")
        return "\n".join(lines)

    lines.append("### 总览")
    lines.append("")
    lines.append("| 维度 | 值 |")
    lines.append("|------|------|")
    lines.append(f"| Review 次数 | {count} |")
    lines.append(f"| PASS | {summary.get('pass_count', 0)} |")
    lines.append(f"| FAIL | {summary.get('fail_count', 0)} |")
    lines.append(f"| TIMEOUT | {summary.get('timeout_count', 0)} |")
    lines.append(f"| SKIPPED | {summary.get('skipped_count', 0)} |")
    lines.append(
        f"| 最大耗时 | {_fmt_ms(summary.get('max_elapsed_ms', 0))} "
        f"({summary.get('max_elapsed_ms', 0)} ms)"
    )
    lines.append(
        f"| 平均耗时 | {_fmt_ms(summary.get('avg_elapsed_ms', 0))} "
        f"({summary.get('avg_elapsed_ms', 0):.0f} ms)"
    )
    lines.append(
        f"| 是否出现超时 | {'是' if summary.get('any_timed_out') else '否'} |"
    )
    lines.append(
        f"| 是否捕获部分输出 | {'是' if summary.get('any_partial_output') else '否'} |"
    )
    lines.append(f"| 解析失败 JSON | {summary.get('parse_errors', 0)} |")
    lines.append("")

    by_task = summary.get("by_task", {}) or {}
    if by_task:
        lines.append("### 按任务聚合")
        lines.append("")
        lines.append("| 任务ID | 次数 | 最大耗时 | 超时 | 部分输出 | 状态分布 |")
        lines.append("|--------|------|----------|------|----------|----------|")
        for tid in sorted(by_task.keys()):
            b = by_task[tid] or {}
            statuses = b.get("statuses", []) or []
            # Compact status histogram: "PASS:2, TIMEOUT:1"
            hist: dict[str, int] = {}
            for s in statuses:
                hist[s] = hist.get(s, 0) + 1
            hist_str = ", ".join(f"{k}:{v}" for k, v in sorted(hist.items())) or "-"
            lines.append(
                f"| {tid} | {b.get('count', 0)} | "
                f"{_fmt_ms(b.get('max_elapsed_ms', 0))} | "
                f"{'是' if b.get('any_timed_out') else '否'} | "
                f"{'是' if b.get('any_partial') else '否'} | "
                f"{hist_str} |"
            )
        lines.append("")

    # Per-entry detail (capped to avoid flooding the report)
    lines.append("### 明细")
    lines.append("")
    lines.append(
        "| 任务ID | Run | 轮次 | 状态 | 耗时 | 超时阈值 | 部分 | 退出码 |"
    )
    lines.append("|--------|-----|------|------|------|----------|------|--------|")
    # Cap detail rows at 30 to keep the report scannable; remaining entries
    # are still represented in the aggregate table above.
    for e in entries[:30]:
        if e.get("parse_error"):
            lines.append(
                f"| {e.get('task_id', '?')} | {e.get('run_id', '?')} | "
                f"{e.get('round', '?')} | PARSE_ERROR | - | - | - | - |"
            )
            continue
        timeout_s = e.get("timeout_seconds", 0)
        timeout_str = (
            f"{int(timeout_s)}s" if timeout_s else "-"
        )
        lines.append(
            f"| {e.get('task_id', '?')} | {e.get('run_id', '?')} | "
            f"{e.get('round', '?')} | {e.get('status', '?')} | "
            f"{_fmt_ms(e.get('elapsed_ms', 0))} | {timeout_str} | "
            f"{'是' if e.get('has_partial_output') else '否'} | "
            f"{e.get('exit_code', '?')} |"
        )
    if len(entries) > 30:
        lines.append(
            f"| ... | (省略 {len(entries) - 30} 行，详见各 run archive) |"
            " - | - | - | - | - | - |"
        )
    lines.append("")

    # Callouts
    callouts: list[str] = []
    if summary.get("any_timed_out"):
        callouts.append(
            "> **P0**: 存在 Codex review 超时；该任务已被标记为 NEEDS_HUMAN，"
            "未触发 commit。请检查对应 ``codex-review-roundN.txt`` 与 "
            "``review-meta-roundN.json`` 决定是否补 review 或调高 "
            "``AUTO_DEV_CODEX_REVIEW_TIMEOUT_SECONDS``。"
        )
    if summary.get("parse_errors", 0) > 0:
        callouts.append(
            f"> **P1**: {summary.get('parse_errors')} 个 ``review-meta-roundN.json`` "
            "解析失败，可能是写入过程中进程被 kill。请检查对应 run archive。"
        )
    if callouts:
        lines.extend(callouts)
        lines.append("")

    return "\n".join(lines)


# ── [V-002] nightly_acceptance_report: Test log parsing ──

def parse_test_summary_from_logs(run: TaskRun) -> dict[str, int]:
    """Parse test result counts from trace files in a task run.

    Looks for pytest-style output lines like 'X passed, Y failed, Z skipped'.
    Returns dict with passed/failed/skipped/error counts.
    """
    summary = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "raw_line": ""}

    for trace_name in run.trace_files:
        if not trace_name.startswith("tests-"):
            continue
        trace_path = run.path / trace_name
        if not trace_path.exists():
            continue
        content = trace_path.read_text(encoding="utf-8", errors="replace")
        for line in reversed(content.splitlines()):
            line = line.strip()
            m = re.match(r"^(\d+) passed", line)
            if m:
                summary["passed"] = int(m.group(1))
                fm = re.search(r"(\d+) failed", line)
                if fm:
                    summary["failed"] = int(fm.group(1))
                sm = re.search(r"(\d+) skipped", line)
                if sm:
                    summary["skipped"] = int(sm.group(1))
                em = re.search(r"(\d+) error", line)
                if em:
                    summary["errors"] = int(em.group(1))
                summary["raw_line"] = line
                return summary

    return summary


# ── [V-002] nightly_acceptance_report: Candidate sample replay ──

def run_sample_replay() -> list[dict]:
    """Run TradeFlow candidate sample replay using false_positive_audit fixtures.

    Returns list of dicts with fixture name, description, and audit result.
    """
    try:
        from tradingagents.tradeflow.false_positive_audit import (
            generate_fixture_samples,
            replay_fixtures,
        )
    except ImportError:
        return []

    fixtures = generate_fixture_samples()
    results = replay_fixtures(fixtures)
    return results


def format_sample_replay(replay_results: list[dict]) -> str:
    """Format sample replay results into a markdown section."""
    if not replay_results:
        return ""

    lines = ["## 候选样本回放", ""]
    lines.append("| 样本 | 描述 | 分类 | 正误判 | 匹配 |")
    lines.append("|------|------|------|--------|------|")

    all_match = True
    for r in replay_results:
        name = r.get("fixture_name", "?")
        desc = r.get("description", "")[:40]
        category = r.get("actual_category", r.get("actual_subcategory", ""))
        fp_type = r.get("actual_fp_type", r.get("actual_fn_type", ""))
        cat_match = r.get("category_match", r.get("subcategory_match", None))
        fp_match = r.get("fp_match", r.get("fn_match", None))

        if cat_match is not None and fp_match is not None:
            match_str = "PASS" if (cat_match and fp_match) else "FAIL"
        elif cat_match is not None:
            match_str = "PASS" if cat_match else "FAIL"
        else:
            match_str = "N/A"

        if match_str == "FAIL":
            all_match = False

        lines.append(f"| {name} | {desc} | {category} | {fp_type} | {match_str} |")

    lines.append("")
    if all_match:
        lines.append("> 所有样本回放通过，候选池质量基线稳定。")
    else:
        lines.append("> **注意**: 部分样本回放不匹配，请检查候选策略或审计逻辑。")
    lines.append("")

    return "\n".join(lines)


@dataclass
class TaskRun:
    run_id: str
    path: Path
    task_id: str = ""
    task_title: str = ""
    priority: str = ""
    status: str = ""
    started_at: str = ""
    finished_at: str = ""
    git_head: str = ""
    rounds: int = 0
    test_commands: str = ""
    reason: str = ""
    review_file: str = ""
    has_summary: bool = False
    summary_content: str = ""
    trace_files: list[str] = field(default_factory=list)


def parse_task_md(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not path.exists():
        return meta
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" in line:
            key, _, val = line[2:].partition(":")
            meta[key.strip().lower().replace(" ", "_")] = val.strip()
    return meta


def parse_summary_md(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not path.exists():
        return meta
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" in line:
            key, _, val = line[2:].partition(":")
            meta[key.strip().lower().replace(" ", "_")] = val.strip()
    return meta


def collect_trace_files(run_dir: Path) -> list[str]:
    traces: list[str] = []
    skip = {"task.md", "summary.md"}
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.name not in skip:
            traces.append(f.name)
    return traces


def scan_task_runs(task_runs_dir: Path, target_date: Optional[str] = None) -> list[TaskRun]:
    if not task_runs_dir.exists():
        return []

    runs: list[TaskRun] = []
    for d in sorted(task_runs_dir.iterdir()):
        if not d.is_dir():
            continue
        parts = d.name.split("-")
        date_part = None
        for i, p in enumerate(parts):
            if re.match(r"^\d{8}$", p) and i + 1 < len(parts) and re.match(r"^\d{6}$", parts[i + 1]):
                date_part = f"{p[:4]}-{p[4:6]}-{p[6:8]}"
                break

        if target_date and date_part != target_date:
            continue

        task_md = d / "task.md"
        summary_md = d / "summary.md"

        task_meta = parse_task_md(task_md)
        summary_meta = parse_summary_md(summary_md)

        task_id = task_meta.get("task", "")
        task_title = ""
        if "—" in task_id:
            task_id, task_title = task_id.split("—", 1)
            task_id = task_id.strip()
            task_title = task_title.strip()
        else:
            # [V-011] Real auto-dev task.md uses " - " (regular hyphen) as the
            # ID/title separator. Extract the canonical task ID via regex and
            # treat the remainder as the title so consistency checks can match.
            m = re.match(r"^([A-Z]+-[\w-]+)\s*-\s*(.+)$", task_id)
            if m:
                task_id = m.group(1).strip()
                task_title = m.group(2).strip()

        summary_content = ""
        if summary_md.exists():
            summary_content = redact(summary_md.read_text(encoding="utf-8"))

        run = TaskRun(
            run_id=d.name,
            path=d,
            task_id=task_id,
            task_title=task_title,
            priority=task_meta.get("priority", ""),
            status=task_meta.get("status", ""),
            started_at=task_meta.get("started_at", ""),
            git_head=task_meta.get("git_head", ""),
            test_commands=task_meta.get("test_commands", ""),
            has_summary=summary_md.exists(),
            summary_content=summary_content,
            trace_files=collect_trace_files(d),
        )

        if summary_meta:
            run.finished_at = summary_meta.get("finished_at", "")
            # [AUTO-004] Defensive parse: some legacy summaries store a free
            # form rounds string (e.g. "2 auto rounds + 1 manual closeout").
            # Extract the leading integer; fall back to 0 so a single bad
            # archive cannot crash the whole scan.
            rounds_raw = summary_meta.get("rounds", "0") or "0"
            m_round = re.search(r"\d+", rounds_raw)
            run.rounds = int(m_round.group(0)) if m_round else 0
            run.reason = redact(summary_meta.get("reason", ""))
            run.review_file = summary_meta.get("review_file", "")
            final_status = summary_meta.get("final_status", "")
            if final_status:
                run.status = final_status

        runs.append(run)

    return runs


def get_git_log_for_date(repo_dir: Path, target_date: str) -> list[dict[str, str]]:
    try:
        after = f"{target_date}T00:00:00"
        before_suffix = target_date + "T23:59:59"
        result = subprocess.run(
            [
                "git", "log",
                "--after", after,
                "--before", before_suffix,
                "--pretty=format:%h|%s|%an|%ai",
                "--no-merges",
            ],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        commits = []
        for line in result.stdout.strip().splitlines():
            if "|" not in line:
                continue
            parts = line.split("|", 3)
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                })
        return commits
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def collect_reviews(reviews_dir: Path, target_date: Optional[str] = None) -> list[dict[str, str]]:
    reviews: list[dict[str, str]] = []
    if not reviews_dir.exists():
        return reviews
    for f in sorted(reviews_dir.iterdir()):
        if not f.is_file():
            continue
        date_match = re.search(r"(\d{8})", f.name)
        file_date = None
        if date_match:
            raw = date_match.group(1)
            file_date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        if target_date and file_date != target_date:
            continue
        content = redact(f.read_text(encoding="utf-8"))
        lines = content.splitlines()
        first_lines = "\n".join(lines[:20])
        reviews.append({
            "file": f.name,
            "date": file_date or "",
            "preview": first_lines,
        })
    return reviews


def generate_report(
    runs: list[TaskRun],
    commits: list[dict[str, str]],
    reviews: list[dict[str, str]],
    target_date: str,
    ready_queue: Optional[list[dict[str, str]]] = None,  # [V-002]
    replay_results: Optional[list[dict]] = None,  # [V-002]
    consistency_results: Optional[list[dict[str, object]]] = None,  # [V-011]
    endurance: Optional[dict[str, object]] = None,  # [V-011]
    runtime_budget_section: Optional[str] = None,  # [AUTO-004]
    low_endurance_proposal: Optional[str] = None,  # [AUTO-004]
    db_hygiene_section: Optional[str] = None,  # [AUTO-005]
    codex_review_watchdog_section: Optional[str] = None,  # [AUTO-006]
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append(f"# 自动开发日报 — {target_date}")
    lines.append("")
    lines.append(f"> 生成时间: {now}  ")
    lines.append(f"> 运行档案数: {len(runs)}  ")
    lines.append(f"> 提交数: {len(commits)}  ")
    lines.append(f"> Review 数: {len(reviews)}  ")

    if ready_queue is not None:  # [V-002]
        lines.append(f"> Ready 队列: {len(ready_queue)} 个任务  ")
    if endurance is not None:  # [V-011]
        lines.append(f"> 预计续航: {endurance.get('hours_range', 'N/A')}  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"> 生成时间: {now}  ")
    lines.append(f"> 运行档案数: {len(runs)}  ")
    lines.append(f"> 提交数: {len(commits)}  ")
    lines.append(f"> Review 数: {len(reviews)}  ")
    lines.append("")

    if ready_queue is not None:  # [V-002]
        lines.append(f"> Ready 队列: {len(ready_queue)} 个任务  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary table
    pass_count = sum(1 for r in runs if r.status == "PASS")
    fail_count = sum(1 for r in runs if r.status in ("NEEDS_HUMAN", "FAIL"))
    other_count = len(runs) - pass_count - fail_count

    lines.append("## 总览")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 任务运行 | {len(runs)} |")
    lines.append(f"| PASS | {pass_count} |")
    lines.append(f"| FAIL/NEEDS_HUMAN | {fail_count} |")
    lines.append(f"| 其他 | {other_count} |")
    lines.append(f"| 提交 | {len(commits)} |")
    lines.append("")

    # Task runs detail
    if runs:
        lines.append("## 任务运行详情")
        lines.append("")
        for run in runs:
            status_icon = "PASS" if run.status == "PASS" else ("NEEDS_HUMAN" if run.status == "NEEDS_HUMAN" else run.status)
            lines.append(f"### {run.run_id}")
            lines.append("")
            lines.append(f"- **任务**: {run.task_id} — {run.task_title}")
            lines.append(f"- **优先级**: {run.priority}")
            lines.append(f"- **最终状态**: {status_icon}")
            lines.append(f"- **Git HEAD**: {run.git_head}")
            lines.append(f"- **轮次**: {run.rounds}")
            if run.started_at:
                lines.append(f"- **开始时间**: {run.started_at}")
            if run.finished_at:
                lines.append(f"- **结束时间**: {run.finished_at}")
            if run.test_commands:
                lines.append(f"- **测试命令**: `{run.test_commands}`")
            if run.reason:
                lines.append(f"- **失败原因**: {redact(run.reason)}")
            if run.review_file:
                lines.append(f"- **Review**: {run.review_file}")
            if run.trace_files:
                lines.append(f"- **Trace 文件**: {', '.join(run.trace_files)}")
            lines.append("")

            if run.has_summary and run.summary_content:
                safe_summary = redact(run.summary_content)
                lines.append("<details>")
                lines.append(f"<summary>summary.md</summary>")
                lines.append("")
                lines.append("```")
                lines.append(safe_summary.strip())
                lines.append("```")
                lines.append("")
                lines.append("</details>")
                lines.append("")

    # Commits
    if commits:
        lines.append("## 提交记录")
        lines.append("")
        lines.append("| Hash | Message | Author | Date |")
        lines.append("|------|---------|--------|------|")
        for c in commits:
            msg = c["message"].replace("|", "\\|")
            lines.append(f"| {c['hash']} | {msg} | {c['author']} | {c['date'][:16]} |")
        lines.append("")

    # Reviews
    if reviews:
        lines.append("## Codex Reviews")
        lines.append("")
        for rv in reviews:
            lines.append(f"### {rv['file']}")
            lines.append("")
            lines.append("<details>")
            lines.append("<summary>预览</summary>")
            lines.append("")
            lines.append("```")
            lines.append(rv["preview"])
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    # Risks
    lines.append("## 风险与注意事项")
    lines.append("")
    risk_items: list[str] = []
    for run in runs:
        if run.status in ("NEEDS_HUMAN", "FAIL"):
            risk_items.append(f"- **{run.task_id}** 需要人工介入: {redact(run.reason)[:200]}")
    if not risk_items:
        risk_items.append("- 无特殊风险")
    lines.extend(risk_items)
    lines.append("")

    # Next steps
    lines.append("## 下一步")
    lines.append("")
    if fail_count > 0:
        lines.append("- 优先处理 NEEDS_HUMAN 任务")
    lines.append("- 检查 `docs/TASKS.md` 中下一个 `ready` 任务")
    lines.append("- 确认测试基线未被破坏")
    lines.append("")

    # [V-002] nightly_acceptance_report: Test results summary
    if runs:
        lines.append("## 测试结果汇总")
        lines.append("")
        for run in runs:
            test_summary = parse_test_summary_from_logs(run)
            if test_summary["raw_line"]:
                lines.append(f"- **{run.task_id}**: {test_summary['passed']} passed, "
                             f"{test_summary['failed']} failed, {test_summary['skipped']} skipped")
            else:
                lines.append(f"- **{run.task_id}**: 无测试日志")
        lines.append("")

    # [V-002] nightly_acceptance_report: Ready queue
    if ready_queue is not None:
        lines.append("## Ready 队列")
        lines.append("")
        if not ready_queue:
            lines.append("> **任务池不足**: 当前无 `ready` 状态任务，夜间 cron 不应空转。")
            lines.append("> 请人工添加新任务到 `docs/TASKS.md`，或将 `proposed` 任务转为 `ready`。")
        else:
            lines.append("| 任务ID | 标题 | 优先级 |")
            lines.append("|--------|------|--------|")
            for t in ready_queue:
                lines.append(f"| {t['task_id']} | {t['title']} | {t['priority']} |")
        lines.append("")

    # [V-011] nightly_auto_dev_acceptance: Ready queue endurance
    if endurance is not None:
        lines.append("## Ready 队列续航估计")
        lines.append("")
        count = endurance.get("count", 0)
        if endurance.get("empty", count == 0):
            lines.append("> Ready 队列为空，无续航。请人工补充 `ready` 任务。")
        else:
            lines.append(f"- **任务数**: {count}")
            lines.append(f"- **预计续航**: {endurance.get('hours_range', 'N/A')} "
                         f"({endurance.get('total_min_minutes', 0)}-"
                         f"{endurance.get('total_max_minutes', 0)} 分钟)")
            by_prio = endurance.get("by_priority", {}) or {}
            if by_prio:
                prio_str = ", ".join(f"{k}: {v}" for k, v in sorted(by_prio.items()))
                lines.append(f"- **优先级分布**: {prio_str}")
            lines.append("")
            lines.append(f"> {endurance.get('summary', '')}")
        lines.append("")

    # [V-011] nightly_auto_dev_acceptance: Done task consistency check
    if consistency_results is not None:
        lines.append("## Done 任务一致性验收")
        lines.append("")
        if not consistency_results:
            lines.append("> 本报告窗口内无 done 任务需要验收。")
        else:
            lines.append("| 任务ID | Run | Review | DEVLOG | 缺失 |")
            lines.append("|--------|-----|--------|--------|------|")
            all_complete = True
            for c in consistency_results:
                missing = c.get("missing", []) or []
                if missing:
                    all_complete = False
                missing_str = "、".join(missing) if missing else "无"
                lines.append(
                    f"| {c.get('task_id', '')} | "
                    f"{'是' if c.get('has_run_archive') else '否'} | "
                    f"{'是' if c.get('has_review') else '否'} | "
                    f"{'是' if c.get('has_devlog_entry') else '否'} | "
                    f"{missing_str} |"
                )
            lines.append("")
            if all_complete:
                lines.append("> 所有 done 任务的 run archive / review / DEVLOG 均已记录。")
            else:
                lines.append("> **注意**: 部分 done 任务缺少记录，请补齐对应 run/review/DEVLOG。")
        lines.append("")

    # [V-002] nightly_acceptance_report: Candidate sample replay
    if replay_results:
        replay_section = format_sample_replay(replay_results)
        if replay_section:
            lines.append(replay_section)

    # [AUTO-004] auto_dev_runtime_budget: historical runtime + fail-stop regression
    if runtime_budget_section:
        lines.append(runtime_budget_section)

    # [AUTO-004] auto_dev_runtime_budget: low-endurance proposed supplement
    if low_endurance_proposal:
        lines.append(low_endurance_proposal)

    # [AUTO-005] db_hygiene_preflight: DB hygiene + 续航门禁 consolidated block
    if db_hygiene_section:
        lines.append(db_hygiene_section)

    # [AUTO-006] codex_review_watchdog: review elapsed_ms / timed_out / partial_output
    if codex_review_watchdog_section:
        lines.append(codex_review_watchdog_section)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="[M-002/V-002/V-011/AUTO-004/AUTO-005/AUTO-006] Summarize auto dev runs into a daily report"
    )
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Print report to stdout without writing file")
    parser.add_argument("--repo-dir", default=None, help="Repository root directory")
    parser.add_argument("--with-sample-replay", action="store_true",  # [V-002]
                        help="Include TradeFlow candidate sample replay in report")
    parser.add_argument("--with-data-source-digest", action="store_true",  # [DATA-006]
                        help="Include data source health digest in report")
    parser.add_argument("--with-consistency-check", action="store_true",  # [V-011]
                        help="Check each done task in window has run/review/DEVLOG")
    parser.add_argument("--no-endurance", action="store_true",  # [V-011]
                        help="Skip ready queue endurance estimate (default: on)")
    parser.add_argument("--with-runtime-budget", action="store_true",  # [AUTO-004]
                        help="Add historical runtime estimate, fail-stop regression "
                             "and low-endurance proposal (default: on)")
    parser.add_argument("--no-runtime-budget", action="store_true",  # [AUTO-004]
                        help="Skip AUTO-004 runtime budget section")
    parser.add_argument("--no-db-hygiene", action="store_true",  # [AUTO-005]
                        help="Skip AUTO-005 DB hygiene + 续航门禁 section (default: on)")
    parser.add_argument("--no-codex-review-watchdog", action="store_true",  # [AUTO-006]
                        help="Skip AUTO-006 Codex review watchdog section (default: on)")
    parser.add_argument("--target-hours", type=float, default=_TARGET_ENDURANCE_HOURS,  # [AUTO-004]
                        help=f"Nightly endurance target in hours (default {_TARGET_ENDURANCE_HOURS})")
    parser.add_argument("--low-hours", type=float, default=_LOW_ENDURANCE_HOURS,  # [AUTO-004]
                        help=f"Low-endurance threshold in hours (default {_LOW_ENDURANCE_HOURS})")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir) if args.repo_dir else Path(__file__).resolve().parent.parent
    task_runs_dir = repo_dir / "docs" / "task_runs"
    reviews_dir = repo_dir / "docs" / "reviews"
    reports_dir = repo_dir / "docs" / "auto_dev_reports"
    tasks_md_path = repo_dir / "docs" / "TASKS.md"  # [V-002]
    devlog_path = repo_dir / "docs" / "DEVLOG.md"  # [V-011]

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    runs = scan_task_runs(task_runs_dir, target_date=target_date)
    commits = get_git_log_for_date(repo_dir, target_date)
    reviews = collect_reviews(reviews_dir, target_date=target_date)

    ready_queue = parse_ready_queue(tasks_md_path)  # [V-002]

    replay_results = None  # [V-002]
    if args.with_sample_replay:
        replay_results = run_sample_replay()

    # [V-011] nightly_auto_dev_acceptance: endurance estimate (default on)
    endurance = None
    if not args.no_endurance:
        endurance = estimate_ready_endurance(ready_queue)

    # [V-011] nightly_auto_dev_acceptance: done task consistency check
    consistency_results = None
    if args.with_consistency_check:
        window_ids = {r.task_id for r in runs if r.task_id}
        # When no runs in window, fall back to all done tasks so the report still
        # documents which done tasks lack records.
        done_tasks = parse_done_tasks_for_ids(
            tasks_md_path,
            target_ids=window_ids if window_ids else None,
        )
        consistency_results = check_done_task_consistency(
            done_tasks, runs, reviews_dir, devlog_path,
        )

    # [AUTO-004] auto_dev_runtime_budget: historical runtime estimate +
    # fail-stop regression + low-endurance proposal (default on).
    runtime_budget_section: Optional[str] = None
    low_endurance_proposal: Optional[str] = None
    if not args.no_runtime_budget:
        # Scan ALL historical runs (not just today) to estimate per-task
        # average elapsed time. This is read-only and never edits the runs.
        all_runs = scan_task_runs(task_runs_dir, target_date=None)
        historical = compute_historical_runtimes(all_runs)
        ordered_queue = sort_ready_queue_for_budget(ready_queue, historical)
        budget_endurance = estimate_ready_endurance_from_history(
            ordered_queue, historical,
            target_hours=args.target_hours, low_hours=args.low_hours,
        )
        fail_stop = verify_fail_stop_strategy(
            repo_dir / "scripts" / "auto_dev_loop.sh"
        )
        runtime_budget_section = format_runtime_budget_section(
            budget_endurance, historical, fail_stop,
            target_hours=args.target_hours, low_hours=args.low_hours,
        )
        low_endurance_proposal = generate_low_endurance_proposal(
            budget_endurance,
            target_hours=args.target_hours, low_hours=args.low_hours,
        )
        # When AUTO-004 is on, prefer the historically-informed endurance in
        # the report header over the static V-011 estimate.
        endurance = budget_endurance

    # [AUTO-005] db_hygiene_preflight: read-only DB hygiene snapshot for the
    # nightly report. Defaults to on so the report always answers "is the
    # production DB clean" alongside the endurance/ready-queue block.
    db_hygiene_section: Optional[str] = None
    if not args.no_db_hygiene:
        snapshot = collect_db_hygiene_snapshot()
        db_hygiene_section = format_db_hygiene_section(snapshot)

    # [AUTO-006] codex_review_watchdog: aggregate review-meta-roundN.json from
    # the report window so operators can see review elapsed_ms, timed_out and
    # partial_output at a glance. Defaults to on.
    codex_review_watchdog_section: Optional[str] = None
    if not args.no_codex_review_watchdog:
        review_meta_entries = collect_review_meta(task_runs_dir, target_date=target_date)
        review_meta_summary = summarize_review_meta(review_meta_entries)
        codex_review_watchdog_section = format_codex_review_watchdog_section(
            review_meta_entries, review_meta_summary
        )

    report = generate_report(
        runs, commits, reviews, target_date,
        ready_queue=ready_queue, replay_results=replay_results,  # [V-002]
        consistency_results=consistency_results, endurance=endurance,  # [V-011]
        runtime_budget_section=runtime_budget_section,  # [AUTO-004]
        low_endurance_proposal=low_endurance_proposal,  # [AUTO-004]
        db_hygiene_section=db_hygiene_section,  # [AUTO-005]
        codex_review_watchdog_section=codex_review_watchdog_section,  # [AUTO-006]
    )

    report = redact(report)  # [V-002] final redaction pass

    if args.dry_run:
        print(report)
        return

    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{target_date}.md"
    report_path.write_text(report, encoding="utf-8")

    if args.with_data_source_digest:  # [DATA-006]
        try:
            from tradingagents.dataflows.data_source_daily_digest import (
                build_digest_section_for_nightly_report,
            )
            ds_reports = str(repo_dir / "docs" / "data_source_reports")
            section = build_digest_section_for_nightly_report(
                reports_dir=ds_reports, target_date=target_date,
            )
            with open(report_path, "a", encoding="utf-8") as f:
                f.write("\n")
                f.write(section)
        except Exception:
            pass

    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
