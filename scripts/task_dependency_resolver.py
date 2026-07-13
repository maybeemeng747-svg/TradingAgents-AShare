#!/usr/bin/env python3
# [AUTO-007] dependency_aware_claim
"""
Dependency-aware task claim + blocked-task auto-release for the auto dev loop.

Replaces the legacy "status=ready only" picker with one that understands the
machine-readable metadata:

  - **depends_on**: TASK-A, TASK-B   # comma-separated task IDs
  - **auto_release**: true|false     # may a blocked task auto-flip to ready?

The picker only claims a task when every listed dependency has status ``done``.
Tasks missing the new fields behave exactly like before (backward compatible),
except that a ``ready`` task that *does* declare ``depends_on`` is now blocked
until those dependencies finish.

Usage::

    python3 scripts/task_dependency_resolver.py claim   --tasks-file docs/TASKS.md
    python3 scripts/task_dependency_resolver.py release --tasks-file docs/TASKS.md --task-id AUTO-XYZ
    python3 scripts/task_dependency_resolver.py dry-run --tasks-file docs/TASKS.md

Commands:

  * ``claim``    - Print the next claimable task as ``ID|title|priority|tests``
                   (or ``NONE|||``) on stdout. Exits non-zero when dependency
                   metadata is inconsistent (missing dep / cycle / status drift)
                   so the shell loop stops and archives the reason.
  * ``release``  - After ``--task-id`` is marked done, flip every downstream
                   ``blocked`` task whose ``auto_release=true`` and whose
                   ``depends_on`` are now satisfied to ``ready``. Rewrites
                   TASKS.md in place. NEVER touches NEEDS_HUMAN / 战略暂停 /
                   blocked-human tasks.
  * ``dry-run``  - Print a detailed report (claimable, blocked, missing deps,
                   cycles, ordering) WITHOUT modifying TASKS.md.

Constraints (per docs/TASKS.md AUTO-007):

  * Never treat a missing dependency as PASS.
  * Never auto-release NEEDS_HUMAN / strategic-pause / auto_release=false tasks.
  * Preserve dirty-tree / test / Codex review / fail-stop gates in the caller.
  * Backward compatible with tasks that lack machine-readable metadata.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ── Exclusion rules (mirror legacy parse_ready_tasks in auto_dev_loop.sh) ────
EXCLUDE_KEYWORDS: tuple[str, ...] = ("preflight", "checklist", "inspection", "baseline")
EXCLUDE_ID_PREFIXES: tuple[str, ...] = ("R-",)
EXCLUDE_IDS: frozenset[str] = frozenset({"T-000"})

PRIORITY_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass
class Task:
    task_id: str
    title: str
    body: str
    body_offset: int
    status: str = ""           # raw status text (lower-cased for kind checks)
    priority: str = "P2"
    depends_on: list[str] = field(default_factory=list)
    auto_release: bool = False
    test_cmds: list[str] = field(default_factory=list)
    status_kind: str = "unknown"   # ready / blocked_auto / blocked_human / in_progress / proposed / done / unknown
    section_header: str = ""

    @property
    def priority_rank(self) -> int:
        return PRIORITY_ORDER.get(self.priority, 9)


# ── Parsing ──────────────────────────────────────────────────────────────────

_SECTION_RE = re.compile(
    r"###\s+([A-Za-z0-9][\w-]*)\s*:\s*(.+?)\n(.*?)(?=\n###|\n---|\Z)",
    re.DOTALL,
)
_STATUS_RE = re.compile(
    r"-\s+\*\*(?:status|状态)\*\*\s*[：:,]+\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_PRIO_RE = re.compile(r"\*\*优先级\*\*\s*[：:,]+\s*P(\d)", re.IGNORECASE)
_PRIO_INLINE_RE = re.compile(r"\(P(\d)\)")
_DEPENDS_ON_RE = re.compile(
    r"-\s+\*\*depends_on\*\*\s*[：:,]+\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_AUTO_RELEASE_RE = re.compile(
    r"-\s+\*\*auto_release\*\*\s*[：:,]+\s*(true|false)",
    re.IGNORECASE,
)
_TEST_CMD_RE = re.compile(r"`(pytest\s+[^`]+)`")
_ID_RE = re.compile(r"[A-Za-z0-9][\w-]*")


def _classify_status(status_raw: str) -> str:
    """Categorise a raw status string into a kind for downstream logic.

    The categories map directly to AUTO-007 release/claim decisions:

    - ``done``          - dependency-satisfying terminal state
    - ``in_progress``   - someone is already working on it; skip
    - ``proposed``      - draft; never auto-claim
    - ``ready``         - queued for the picker
    - ``blocked_human`` - explicit human/strategic gate; never auto-release
    - ``blocked_auto``  - waiting on dependencies; may auto-release
    """
    s = status_raw.lower()
    if not s:
        return "unknown"
    if "done" in s or "✓" in s or "闭环" in s or "已由" in s:
        return "done"
    if "in_progress" in s:
        return "in_progress"
    if "proposed" in s:
        return "proposed"
    if "ready" in s:
        return "ready"
    if "blocked" in s or "等待" in s:
        if (
            "needs_human" in s
            or "need_human" in s
            or "human" in s
            or "战略暂停" in s
            or "strategic" in s
            or "人工" in s
        ):
            return "blocked_human"
        return "blocked_auto"
    return "unknown"


def _is_dev_task(task_id: str, title: str) -> bool:
    """Reproduce the legacy picker's exclusion rules so claim output stays
    backward compatible."""
    if task_id in EXCLUDE_IDS:
        return False
    if any(task_id.startswith(p) for p in EXCLUDE_ID_PREFIXES):
        return False
    title_lower = title.lower()
    if any(kw in title_lower for kw in EXCLUDE_KEYWORDS):
        return False
    if "done" in title_lower or "✓" in title:
        return False
    return True


def parse_tasks(text: str) -> list[Task]:
    """Parse all task sections from TASKS.md content.

    Each section looks like::

        ### TASK-ID: Title text (Pn)
        - **描述**: ...
        - **优先级**: P1
        - **状态**: ready
        - **depends_on**: TASK-A, TASK-B
        - **auto_release**: true

    Tasks that lack the machine-readable fields default to empty dependencies
    and ``auto_release=False`` (i.e. behave like the legacy picker).
    """
    tasks: list[Task] = []
    for m in _SECTION_RE.finditer(text):
        task_id = m.group(1)
        title = m.group(2).strip()
        body = m.group(3)

        status_raw = ""
        status_match = _STATUS_RE.search(body)
        if status_match:
            status_raw = status_match.group(1).strip()

        prio = "P2"
        prio_match = _PRIO_RE.search(body)
        if prio_match:
            prio = f"P{prio_match.group(1)}"
        else:
            inline = _PRIO_INLINE_RE.search(title)
            if inline:
                prio = f"P{inline.group(1)}"
            else:
                body_match = re.search(r"\bP(\d)\b", body[:300])
                if body_match:
                    prio = f"P{body_match.group(1)}"

        depends_on: list[str] = []
        dep_match = _DEPENDS_ON_RE.search(body)
        if dep_match:
            for chunk in dep_match.group(1).split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                # tolerate trailing "✓" or status markers in the value
                ident = _ID_RE.match(chunk)
                if ident:
                    depends_on.append(ident.group(0))

        auto_release = False
        ar_match = _AUTO_RELEASE_RE.search(body)
        if ar_match and ar_match.group(1).lower() == "true":
            auto_release = True

        test_cmds = _TEST_CMD_RE.findall(body)

        # Find the most recent section header before this task.
        section_match = None
        for sm in re.finditer(r"^##\s+(.+?)$", text[: m.start()], re.MULTILINE):
            section_match = sm
        section_header = section_match.group(1).strip() if section_match else ""

        tasks.append(
            Task(
                task_id=task_id,
                title=title,
                body=body,
                body_offset=m.start(),
                status=status_raw,
                priority=prio,
                depends_on=depends_on,
                auto_release=auto_release,
                test_cmds=test_cmds,
                status_kind=_classify_status(status_raw),
                section_header=section_header,
            )
        )
    return tasks


# ── Dependency math ──────────────────────────────────────────────────────────

def collect_done_ids(tasks: Iterable[Task]) -> set[str]:
    """Set of task IDs considered ``done`` for dependency satisfaction.

    Mirrors ``parse_done_task_ids`` + ``_is_done_task`` in
    ``suggest_next_tasks.py`` so the two scripts cannot drift.
    """
    done: set[str] = set()
    for t in tasks:
        if t.status_kind == "done":
            done.add(t.task_id)
            continue
        s = t.status.lower()
        if "done" in s or "闭环" in s or "已由" in s:
            done.add(t.task_id)
            continue
        if "✓" in t.title or "done" in t.title.lower():
            done.add(t.task_id)
    return done


def find_missing_deps(tasks: list[Task]) -> dict[str, list[str]]:
    """Map task_id -> list of declared dependencies that don't exist at all."""
    all_ids = {t.task_id for t in tasks}
    missing: dict[str, list[str]] = {}
    for t in tasks:
        miss = [d for d in t.depends_on if d not in all_ids]
        if miss:
            missing[t.task_id] = miss
    return missing


def detect_cycles(tasks: list[Task]) -> list[list[str]]:
    """Return a list of dependency cycles (each a list of task IDs).

    Uses DFS with three colours. A cycle is recorded when we hit a node that
    is currently on the recursion stack (grey).
    """
    graph: dict[str, list[str]] = {t.task_id: list(t.depends_on) for t in tasks}
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {tid: WHITE for tid in graph}
    cycles: list[list[str]] = []

    def visit(node: str, stack: list[str]) -> None:
        colour[node] = GREY
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in colour:
                # References a non-existent task — handled elsewhere as a
                # missing dep; skip to avoid KeyError here.
                continue
            if colour[nxt] == GREY:
                idx = stack.index(nxt)
                cycles.append(stack[idx:] + [nxt])
            elif colour[nxt] == WHITE:
                visit(nxt, stack)
        stack.pop()
        colour[node] = BLACK

    for tid in sorted(graph):
        if colour.get(tid) == WHITE:
            visit(tid, [])
    return cycles


def unsatisfied_deps(task: Task, done_ids: set[str]) -> list[str]:
    return [d for d in task.depends_on if d not in done_ids]


def is_gating_task(task: Task) -> bool:
    """Tasks whose dependency metadata MUST be consistent because the auto
    loop could claim or release them on this run.

    Missing deps / cycles on non-gating tasks (e.g. ``blocked-human`` waiting
    on a ZCode deliverable) are reported as warnings but never hard-stop the
    batch, otherwise the picker could not co-exist with cross-project refs.
    """
    if task.status_kind == "ready":
        return True
    if task.status_kind == "blocked_auto" and task.auto_release:
        return True
    return False


def is_claimable(task: Task, done_ids: set[str]) -> tuple[bool, str]:
    """Decide if ``task`` may be claimed by the auto dev loop right now.

    Returns ``(claimable, reason)``:

    * ``ready`` tasks are claimable only when all ``depends_on`` are done.
      This closes the legacy hole where a downstream task was prematurely
      flipped to ``ready`` while its dependency was still in flight.
    * ``blocked_auto`` tasks are claimable only when they opt in via
      ``auto_release=true`` AND all ``depends_on`` are done.
    """
    if not _is_dev_task(task.task_id, task.title):
        return False, "excluded by picker rules"
    if task.status_kind == "ready":
        miss = unsatisfied_deps(task, done_ids)
        if miss:
            return False, f"deps_not_satisfied:{','.join(miss)}"
        return True, "ready"
    if task.status_kind == "blocked_auto":
        if not task.auto_release:
            return False, "blocked_auto_without_auto_release"
        miss = unsatisfied_deps(task, done_ids)
        if miss:
            return False, f"deps_not_satisfied:{','.join(miss)}"
        return True, "blocked_auto_released"
    return False, f"status_{task.status_kind}"


def order_candidates(tasks: list[Task]) -> list[Task]:
    """Stable order: priority rank ascending, then document order."""
    return sorted(tasks, key=lambda t: (t.priority_rank, t.body_offset))


def pick_next(tasks: list[Task], done_ids: set[str]) -> tuple[Task | None, list[str]]:
    """Return the next claimable task plus diagnostic lines for the log.

    Only emits diagnostics for tasks that the picker could have considered
    (``ready`` or ``blocked_auto``); terminal / non-dev tasks are skipped
    silently to keep the shell log readable on large TASKS.md files.
    """
    diagnostics: list[str] = []
    for task in order_candidates(tasks):
        claimable, reason = is_claimable(task, done_ids)
        if claimable:
            return task, diagnostics
        if task.status_kind in ("ready", "blocked_auto"):
            diagnostics.append(f"- {task.task_id}: skip ({reason})")
    return None, diagnostics


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_claim(args: argparse.Namespace) -> int:
    tasks_file = Path(args.tasks_file)
    text = tasks_file.read_text(encoding="utf-8")
    tasks = parse_tasks(text)
    by_id = {t.task_id: t for t in tasks}

    missing = find_missing_deps(tasks)
    blocking_missing: dict[str, list[str]] = {}
    if missing:
        for tid, deps in missing.items():
            owner = by_id.get(tid)
            if owner is not None and is_gating_task(owner):
                blocking_missing[tid] = deps
            else:
                # Non-gating task (e.g. blocked-human waiting on a ZCode
                # deliverable). Warn but do not break the batch.
                print(
                    f"[AUTO-007] WARN missing_dep (non-gating) {tid} -> {deps}",
                    file=sys.stderr,
                )
        if blocking_missing:
            for tid, deps in sorted(blocking_missing.items()):
                print(f"[AUTO-007] MISSING_DEP {tid} -> {deps}", file=sys.stderr)
            print(
                "[AUTO-007] dependency metadata on a claimable/releaseable "
                "task references unknown IDs; stopping batch for human review",
                file=sys.stderr,
            )
            return 1

    cycles = detect_cycles(tasks)
    if cycles:
        # Only hard-stop when a cycle touches a gating task; otherwise the
        # cycle is metadata drift on tasks the loop would not touch anyway.
        gating_ids = {t.task_id for t in tasks if is_gating_task(t)}
        blocking_cycle = False
        for cyc in cycles:
            touches_gating = any(node in gating_ids for node in cyc)
            level = "CYCLE" if touches_gating else "WARN cycle (non-gating)"
            print(f"[AUTO-007] {level} {' -> '.join(cyc)}", file=sys.stderr)
            if touches_gating:
                blocking_cycle = True
        if blocking_cycle:
            print(
                "[AUTO-007] circular dependency touches a claimable task; "
                "stopping batch",
                file=sys.stderr,
            )
            return 1

    done_ids = collect_done_ids(tasks)
    task, diagnostics = pick_next(tasks, done_ids)
    for line in diagnostics:
        print(line, file=sys.stderr)

    if task is None:
        print("NONE|||")
        return 0

    test_cmds = ",".join(task.test_cmds)
    print(f"{task.task_id}|{task.title}|{task.priority}|{test_cmds}")
    return 0


def _flip_status_to_ready(text: str, task_id: str) -> tuple[str, bool]:
    """Rewrite the ``**状态**`` line of ``task_id`` to ``ready``.

    Returns the new text and a flag indicating whether a change was made.
    Only flips when the current status is a blocked_auto candidate; never
    touches blocked_human / in_progress / done / proposed.
    """
    section_re = re.compile(
        r"(###\s+" + re.escape(task_id) + r"\s*:.*?)(?=\n###|\n---|\Z)",
        re.DOTALL,
    )
    m = section_re.search(text)
    if not m:
        return text, False
    section = m.group(1)
    status_match = _STATUS_RE.search(section)
    if not status_match:
        return text, False
    current = status_match.group(1).strip()
    kind = _classify_status(current)
    if kind != "blocked_auto":
        return text, False

    new_section = (
        section[: status_match.start(1)]
        + "ready"
        + section[status_match.end(1):]
    )
    new_text = text[: m.start()] + new_section + text[m.end():]
    return new_text, True


def cmd_release(args: argparse.Namespace) -> int:
    tasks_file = Path(args.tasks_file)
    text = tasks_file.read_text(encoding="utf-8")
    tasks = parse_tasks(text)
    done_ids = collect_done_ids(tasks)

    if args.task_id:
        # Explicitly count the just-finished task as done even if its status
        # line in TASKS.md has not been rewritten yet (the shell writes the
        # new status AFTER this call returns).
        done_ids.add(args.task_id)

    released: list[str] = []
    new_text = text
    for task in tasks:
        if task.task_id == args.task_id:
            continue
        if task.status_kind != "blocked_auto":
            continue
        if not task.auto_release:
            continue
        if unsatisfied_deps(task, done_ids):
            continue
        new_text, changed = _flip_status_to_ready(new_text, task.task_id)
        if changed:
            released.append(task.task_id)

    if released and not args.dry_run:
        tasks_file.write_text(new_text, encoding="utf-8")

    for tid in released:
        print(f"[AUTO-007] RELEASED {tid} -> ready")
    if not released:
        print("[AUTO-007] no downstream tasks to release")
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    tasks_file = Path(args.tasks_file)
    text = tasks_file.read_text(encoding="utf-8")
    tasks = parse_tasks(text)
    done_ids = collect_done_ids(tasks)

    print("# AUTO-007 Dependency-Aware Dry-Run Report")
    print(f"- tasks parsed: {len(tasks)}")
    print(f"- done ids: {len(done_ids)}")
    print()

    missing = find_missing_deps(tasks)
    if missing:
        print("## Missing dependencies (BLOCKING)")
        for tid, deps in sorted(missing.items()):
            print(f"- `{tid}` references unknown: {', '.join(deps)}")
        print()

    cycles = detect_cycles(tasks)
    if cycles:
        print("## Circular dependencies (BLOCKING)")
        for cyc in cycles:
            print(f"- {' -> '.join(cyc)}")
        print()

    claimable: list[Task] = []
    blocked_report: list[tuple[Task, str]] = []
    for task in order_candidates(tasks):
        ok, reason = is_claimable(task, done_ids)
        if ok:
            claimable.append(task)
        elif task.status_kind in ("ready", "blocked_auto"):
            blocked_report.append((task, reason))

    print("## Claimable tasks (in pick order)")
    if not claimable:
        print("- (none)")
    for task in claimable:
        deps = ", ".join(task.depends_on) if task.depends_on else "—"
        print(f"- `{task.task_id}` [{task.priority}] deps: {deps}")
    print()

    print("## Active tasks blocked by dependencies")
    if not blocked_report:
        print("- (none)")
    for task, reason in blocked_report:
        miss = unsatisfied_deps(task, done_ids)
        if miss:
            print(
                f"- `{task.task_id}` [{task.priority}] status={task.status_kind} "
                f"reason={reason} missing={','.join(miss)} auto_release={task.auto_release}"
            )
        else:
            print(
                f"- `{task.task_id}` [{task.priority}] status={task.status_kind} "
                f"reason={reason} auto_release={task.auto_release}"
            )
    print()

    release_candidates = [
        t for t in tasks
        if t.status_kind == "blocked_auto"
        and t.auto_release
        and not unsatisfied_deps(t, done_ids)
    ]
    print("## Auto-release candidates (would flip to ready after a dep finishes)")
    if not release_candidates:
        print("- (none)")
    for task in release_candidates:
        print(f"- `{task.task_id}` [{task.priority}] deps satisfied")
    print()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="[AUTO-007] dependency-aware task picker & auto-release",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_claim = sub.add_parser("claim", help="Print the next claimable task")
    p_claim.add_argument("--tasks-file", default="docs/TASKS.md")
    p_claim.add_argument("--run-dir", default=None, help="Optional archive dir")
    p_claim.set_defaults(func=cmd_claim)

    p_release = sub.add_parser(
        "release", help="Auto-release downstream blocked tasks after --task-id finishes",
    )
    p_release.add_argument("--tasks-file", default="docs/TASKS.md")
    p_release.add_argument("--task-id", required=True)
    p_release.add_argument("--dry-run", action="store_true")
    p_release.set_defaults(func=cmd_release)

    p_dry = sub.add_parser("dry-run", help="Print a dependency report without modifying TASKS.md")
    p_dry.add_argument("--tasks-file", default="docs/TASKS.md")
    p_dry.set_defaults(func=cmd_dry_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
