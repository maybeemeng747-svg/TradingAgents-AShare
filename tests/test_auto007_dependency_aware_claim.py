# [AUTO-007] dependency_aware_claim
"""Tests for scripts/task_dependency_resolver.py and its wiring into
``scripts/auto_dev_loop.sh``.

Covers the AUTO-007 acceptance fixtures:

  * dependency satisfied / unsatisfied
  * missing dependency (typo) — gating vs non-gating
  * circular dependencies
  * NEEDS_HUMAN / 战略暂停 / blocked-human — must NEVER auto-release
  * legacy tasks without ``depends_on`` / ``auto_release`` fields
  * HY-008 done → HY-009 becomes claimable (and not before)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
import task_dependency_resolver as R  # noqa: E402

LOOP_SCRIPT = SCRIPTS_DIR / "auto_dev_loop.sh"
RESOLVER = SCRIPTS_DIR / "task_dependency_resolver.py"


# ────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ────────────────────────────────────────────────────────────────────

def _write(tmp_path: Path, body: str) -> Path:
    """Write TASKS.md content under tmp_path and return its path."""
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    f = docs / "TASKS.md"
    f.write_text(textwrap.dedent(body), encoding="utf-8")
    return f


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


SIMPLE_DAG = """\
# 任务池

### PARENT-001: 父任务（P1）
- **优先级**：P1
- **状态**：ready
- **验证方式**：无

### CHILD-A: 子任务 A 依赖 PARENT（P2）
- **优先级**：P2
- **状态**：blocked
- **depends_on**：PARENT-001
- **auto_release**：true

### CHILD-B: 子任务 B 依赖 PARENT 且依赖 CHILD-A（P2）
- **优先级**：P2
- **状态**：blocked
- **depends_on**：PARENT-001, CHILD-A
- **auto_release**：true
"""


# ────────────────────────────────────────────────────────────────────
# 1. Parser
# ────────────────────────────────────────────────────────────────────

class TestParser:
    def test_extracts_machine_readable_depends_on(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: x (P1)
            - **状态**：ready
            - **depends_on**：T-002, T-003
            - **auto_release**：true
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        t = tasks[0]
        assert t.task_id == "T-001"
        assert t.depends_on == ["T-002", "T-003"]
        assert t.auto_release is True

    def test_depends_on_strips_trailing_markers(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: x (P1)
            - **状态**：ready
            - **前置条件**：T-002 完成 ✓, T-003 完成
            - **depends_on**：T-002 ✓, T-003
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        # The legacy "前置条件" line is NOT used for depends_on — only the
        # explicit machine-readable field is. Trailing ✓ must be stripped.
        assert tasks[0].depends_on == ["T-002", "T-003"]

    def test_auto_release_false_default(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: x (P1)
            - **状态**：blocked
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        assert tasks[0].auto_release is False
        assert tasks[0].depends_on == []

    def test_legacy_task_has_no_machine_fields(self, tmp_path):
        """Tasks written before AUTO-007 must parse cleanly."""
        f = _write(tmp_path, """\
            ### T-001: 老任务 (P1)
            - **优先级**：P1
            - **状态**：ready
            - **前置条件**：T-OLD 完成 ✓
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        assert tasks[0].depends_on == []
        assert tasks[0].auto_release is False
        assert tasks[0].status_kind == "ready"

    @pytest.mark.parametrize(
        "status, expected",
        [
            ("ready", "ready"),
            ("done — commit abc123", "done"),
            ("done ✓", "done"),
            ("闭环", "done"),
            ("已由 后续任务 闭环", "done"),
            ("in_progress — claimed X", "in_progress"),
            ("proposed", "proposed"),
            ("blocked — 等待 X", "blocked_auto"),
            ("blocked-auto — 等待 X", "blocked_auto"),
            ("blocked-human — NEEDS_HUMAN", "blocked_human"),
            ("blocked — NEEDS_HUMAN, see X", "blocked_human"),
            ("blocked — 战略暂停", "blocked_human"),
            ("blocked — 等待人工确认", "blocked_human"),
        ],
    )
    def test_status_classification(self, status, expected):
        assert R._classify_status(status) == expected

    def test_priority_inline_overrides_body(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: 标题（P0）
            - **优先级**：P2
            - **状态**：ready
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        # 优先级字段优先于标题内联
        assert tasks[0].priority == "P2"

    def test_priority_fallback_to_inline_title(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: 标题（P0）
            - **状态**：ready
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        assert tasks[0].priority == "P0"


# ────────────────────────────────────────────────────────────────────
# 2. Done detection
# ────────────────────────────────────────────────────────────────────

class TestDoneDetection:
    def test_done_via_status(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: x (P1)
            - **状态**：done -- commit abc
            ### T-002: y (P1)
            - **状态**：ready
            - **depends_on**：T-001
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        done = R.collect_done_ids(tasks)
        assert "T-001" in done

    def test_done_via_title_checkmark(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: 已完成任务 ✓ (P1)
            - **状态**：done
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        assert "T-001" in R.collect_done_ids(tasks)

    def test_done_via_closure_status(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: 任务 (P1)
            - **状态**：blocked — 已由 后续任务 闭环
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        assert "T-001" in R.collect_done_ids(tasks)


# ────────────────────────────────────────────────────────────────────
# 3. Missing deps + cycle detection
# ────────────────────────────────────────────────────────────────────

class TestMissingDeps:
    def test_finds_missing_dep(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: x (P1)
            - **状态**：ready
            - **depends_on**：T-TYPO
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        missing = R.find_missing_deps(tasks)
        assert missing == {"T-001": ["T-TYPO"]}

    def test_missing_dep_on_non_gating_task_is_warning(self, tmp_path):
        """blocked-human task waiting on a ZCode deliverable must not
        block the whole batch."""
        f = _write(tmp_path, """\
            ### T-001: x (P1)
            - **状态**：blocked-human — 等待 ZC-RS-001
            - **depends_on**：ZC-RS-001

            ### T-002: y (P1)
            - **状态**：ready
            """)
        result = _run(
            [sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)],
        )
        assert result.returncode == 0, result.stderr
        assert "WARN missing_dep" in result.stderr
        # Should still pick the claimable task
        assert "T-002|" in result.stdout

    def test_missing_dep_on_gating_task_stops_batch(self, tmp_path):
        f = _write(tmp_path, """\
            ### T-001: x (P1)
            - **状态**：ready
            - **depends_on**：T-TYPO
            """)
        result = _run(
            [sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)],
        )
        assert result.returncode == 1
        assert "MISSING_DEP" in result.stderr
        assert "stopping batch" in result.stderr


class TestCycleDetection:
    def test_detects_simple_cycle(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：ready
            - **depends_on**：B
            ### B: b (P1)
            - **状态**：ready
            - **depends_on**：A
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        cycles = R.detect_cycles(tasks)
        assert cycles, "expected at least one cycle"

    def test_detects_self_cycle(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：ready
            - **depends_on**：A
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        cycles = R.detect_cycles(tasks)
        assert cycles

    def test_dag_has_no_cycle(self, tmp_path):
        f = _write(tmp_path, SIMPLE_DAG)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        assert R.detect_cycles(tasks) == []

    def test_cycle_on_gating_task_stops_batch(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：ready
            - **depends_on**：B
            ### B: b (P1)
            - **状态**：ready
            - **depends_on**：A
            """)
        result = _run(
            [sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)],
        )
        assert result.returncode == 1
        assert "CYCLE" in result.stderr

    def test_cycle_on_non_gating_task_is_warning(self, tmp_path):
        """A cycle between two blocked-human tasks should not stop the batch."""
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：blocked-human
            - **depends_on**：B
            ### B: b (P1)
            - **状态**：blocked-human
            - **depends_on**：A
            ### C: c (P1)
            - **状态**：ready
            """)
        result = _run(
            [sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)],
        )
        assert result.returncode == 0, result.stderr
        assert "WARN cycle" in result.stderr
        assert "C|" in result.stdout


# ────────────────────────────────────────────────────────────────────
# 4. Claimability matrix
# ────────────────────────────────────────────────────────────────────

class TestClaimability:
    def test_ready_no_deps_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="ready", status_kind="ready")
        ok, _ = R.is_claimable(t, set())
        assert ok

    def test_ready_with_satisfied_deps_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="ready", status_kind="ready", depends_on=["A"])
        ok, _ = R.is_claimable(t, {"A"})
        assert ok

    def test_ready_with_unsatisfied_deps_not_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="ready", status_kind="ready", depends_on=["A"])
        ok, reason = R.is_claimable(t, set())
        assert not ok
        assert "deps_not_satisfied" in reason

    def test_blocked_auto_with_release_and_satisfied_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="blocked", status_kind="blocked_auto",
                   depends_on=["A"], auto_release=True)
        ok, _ = R.is_claimable(t, {"A"})
        assert ok

    def test_blocked_auto_without_auto_release_not_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="blocked", status_kind="blocked_auto",
                   depends_on=["A"], auto_release=False)
        ok, reason = R.is_claimable(t, {"A"})
        assert not ok
        assert "auto_release" in reason

    def test_blocked_human_never_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="blocked-human", status_kind="blocked_human",
                   depends_on=["A"], auto_release=True)
        ok, _ = R.is_claimable(t, {"A"})
        assert not ok

    def test_in_progress_not_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="in_progress", status_kind="in_progress")
        ok, _ = R.is_claimable(t, set())
        assert not ok

    def test_proposed_not_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="proposed", status_kind="proposed")
        ok, _ = R.is_claimable(t, set())
        assert not ok

    def test_done_not_claimable(self):
        t = R.Task(task_id="X", title="x", body="", body_offset=0,
                   status="done", status_kind="done")
        ok, _ = R.is_claimable(t, set())
        assert not ok


# ────────────────────────────────────────────────────────────────────
# 5. Ordering
# ────────────────────────────────────────────────────────────────────

class TestOrdering:
    def test_priority_before_document_order(self, tmp_path):
        f = _write(tmp_path, """\
            ### FIRST: a (P2)
            - **优先级**：P2
            - **状态**：ready

            ### SECOND: b (P1)
            - **优先级**：P1
            - **状态**：ready
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        ordered = R.order_candidates(tasks)
        # P1 must come first despite appearing later in the document
        assert ordered[0].task_id == "SECOND"
        assert ordered[1].task_id == "FIRST"

    def test_same_priority_keeps_document_order(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **优先级**：P1
            - **状态**：ready

            ### B: b (P1)
            - **优先级**：P1
            - **状态**：ready
            """)
        tasks = R.parse_tasks(f.read_text(encoding="utf-8"))
        ordered = R.order_candidates(tasks)
        assert [t.task_id for t in ordered] == ["A", "B"]


# ────────────────────────────────────────────────────────────────────
# 6. Release — HY-008 done → HY-009 claimable
# ────────────────────────────────────────────────────────────────────

class TestRelease:
    HY_FIXTURE = """\
        # 任务池

        ### HY-008: 半年报端到端验收（P2）
        - **优先级**：P2
        - **状态**：ready
        - **depends_on**：

        ### HY-009: 半年报增量刷新与冲突审计（P2）
        - **优先级**：P2
        - **状态**：blocked — 等待 HY-008
        - **depends_on**：HY-008
        - **auto_release**：true
        """

    def test_hy009_not_claimable_when_hy008_pending(self, tmp_path):
        f = _write(tmp_path, self.HY_FIXTURE)
        result = _run([sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)])
        assert result.returncode == 0
        assert "HY-008|" in result.stdout
        assert "HY-009|" not in result.stdout

    def test_hy009_claimable_after_hy008_done(self, tmp_path):
        f = _write(tmp_path, self.HY_FIXTURE)
        # Mark HY-008 done first
        text = f.read_text(encoding="utf-8").replace(
            "### HY-008: 半年报端到端验收（P2）\n- **优先级**：P2\n- **状态**：ready",
            "### HY-008: 半年报端到端验收（P2）\n- **优先级**：P2\n- **状态**：done -- commit abc",
        )
        f.write_text(text, encoding="utf-8")
        result = _run([sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)])
        assert result.returncode == 0
        assert "HY-009|" in result.stdout

    def test_release_flips_blocked_to_ready_after_dep_done(self, tmp_path):
        f = _write(tmp_path, self.HY_FIXTURE)
        before = f.read_text(encoding="utf-8")
        result = _run([
            sys.executable, str(RESOLVER), "release",
            "--tasks-file", str(f), "--task-id", "HY-008",
        ])
        assert result.returncode == 0, result.stderr
        after = f.read_text(encoding="utf-8")
        assert before != after, "TASKS.md must be modified by release"
        # HY-009 should now have status ready
        assert "状态**：ready" in after
        assert "RELEASED HY-009" in result.stdout

    def test_release_respects_auto_release_false(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：done
            ### B: b (P1)
            - **状态**：blocked — 等待 A
            - **depends_on**：A
            - **auto_release**：false
            """)
        before = f.read_text(encoding="utf-8")
        result = _run([
            sys.executable, str(RESOLVER), "release",
            "--tasks-file", str(f), "--task-id", "A",
        ])
        assert result.returncode == 0
        after = f.read_text(encoding="utf-8")
        assert before == after, "must NOT release when auto_release=false"
        assert "no downstream tasks to release" in result.stdout

    def test_release_skips_needs_human(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：done
            ### B: b (P1)
            - **状态**：blocked — NEEDS_HUMAN
            - **depends_on**：A
            - **auto_release**：true
            """)
        before = f.read_text(encoding="utf-8")
        _run([sys.executable, str(RESOLVER), "release",
              "--tasks-file", str(f), "--task-id", "A"])
        assert before == f.read_text(encoding="utf-8")

    def test_release_skips_strategic_pause(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：done
            ### B: b (P1)
            - **状态**：blocked — 战略暂停
            - **depends_on**：A
            - **auto_release**：true
            """)
        before = f.read_text(encoding="utf-8")
        _run([sys.executable, str(RESOLVER), "release",
              "--tasks-file", str(f), "--task-id", "A"])
        assert before == f.read_text(encoding="utf-8")

    def test_release_does_not_touch_ready_or_inprogress(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：done
            ### R: r (P1)
            - **状态**：ready
            ### I: i (P1)
            - **状态**：in_progress
            """)
        before = f.read_text(encoding="utf-8")
        _run([sys.executable, str(RESOLVER), "release",
              "--tasks-file", str(f), "--task-id", "A"])
        assert before == f.read_text(encoding="utf-8")

    def test_release_dry_run_does_not_modify(self, tmp_path):
        f = _write(tmp_path, self.HY_FIXTURE)
        before = f.read_text(encoding="utf-8")
        result = _run([
            sys.executable, str(RESOLVER), "release",
            "--tasks-file", str(f), "--task-id", "HY-008", "--dry-run",
        ])
        assert result.returncode == 0
        assert before == f.read_text(encoding="utf-8")
        # Should still report what WOULD be released
        assert "HY-009" in result.stdout

    def test_release_counts_just_finished_task_as_done(self, tmp_path):
        """Even if the just-finished task's status line has not yet been
        rewritten by the shell, release must still treat it as done."""
        f = _write(tmp_path, self.HY_FIXTURE)
        # HY-008 is still 'ready' in the file (shell rewrites status AFTER
        # release). Pass --task-id HY-008 so the resolver counts it as done.
        result = _run([
            sys.executable, str(RESOLVER), "release",
            "--tasks-file", str(f), "--task-id", "HY-008",
        ])
        assert result.returncode == 0
        assert "RELEASED HY-009" in result.stdout


# ────────────────────────────────────────────────────────────────────
# 7. cmd_claim end-to-end
# ────────────────────────────────────────────────────────────────────

class TestCmdClaim:
    def test_picks_first_ready(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **优先级**：P1
            - **状态**：ready
            - **验证方式**：`pytest tests/test_a.py -q`
            """)
        result = _run([sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)])
        assert result.returncode == 0
        assert result.stdout.strip() == "A|a (P1)|P1|pytest tests/test_a.py -q"

    def test_returns_none_when_empty(self, tmp_path):
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **状态**：done
            """)
        result = _run([sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)])
        assert result.returncode == 0
        assert result.stdout.strip() == "NONE|||"

    def test_skip_legacy_ready_with_unsatisfied_natural_language_dep(self, tmp_path):
        """AUTO-007 mandate: don't claim a ready task whose machine-readable
        depends_on is unsatisfied. Closes the legacy hole where downstream
        tasks were prematurely flipped to ready."""
        f = _write(tmp_path, """\
            ### A: a (P1)
            - **优先级**：P1
            - **状态**：in_progress
            ### B: b (P1)
            - **优先级**：P1
            - **状态**：ready
            - **depends_on**：A
            """)
        result = _run([sys.executable, str(RESOLVER), "claim", "--tasks-file", str(f)])
        assert result.returncode == 0
        # B is ready but A is not done, so nothing claimable
        assert result.stdout.strip() == "NONE|||"
        assert "B: skip" in result.stderr


# ────────────────────────────────────────────────────────────────────
# 8. cmd_dry_run — must never modify TASKS.md
# ────────────────────────────────────────────────────────────────────

class TestCmdDryRun:
    def test_does_not_modify_tasks_file(self, tmp_path):
        f = _write(tmp_path, SIMPLE_DAG)
        before = f.read_text(encoding="utf-8")
        result = _run([sys.executable, str(RESOLVER), "dry-run", "--tasks-file", str(f)])
        assert result.returncode == 0
        assert before == f.read_text(encoding="utf-8")

    def test_includes_all_sections(self, tmp_path):
        f = _write(tmp_path, SIMPLE_DAG)
        result = _run([sys.executable, str(RESOLVER), "dry-run", "--tasks-file", str(f)])
        assert "Claimable tasks" in result.stdout
        assert "blocked by dependencies" in result.stdout
        assert "Auto-release candidates" in result.stdout


# ────────────────────────────────────────────────────────────────────
# 9. Shell integration
# ────────────────────────────────────────────────────────────────────

class TestShellIntegration:
    def test_auto_dev_loop_shell_syntax(self):
        result = _run(["bash", "-n", str(LOOP_SCRIPT)])
        assert result.returncode == 0, result.stderr

    def test_resolver_invoked_when_present(self, tmp_path):
        """When task_dependency_resolver.py is present alongside the loop,
        AUTO-007 markers must appear in the output."""
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "docs").mkdir()
        shutil.copy2(LOOP_SCRIPT, repo / "scripts" / "auto_dev_loop.sh")
        shutil.copy2(RESOLVER, repo / "scripts" / "task_dependency_resolver.py")
        (repo / "docs" / "TASKS.md").write_text(textwrap.dedent("""\
            ### A: a (P1)
            - **优先级**：P1
            - **状态**：ready
            """), encoding="utf-8")
        (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "i"],
            cwd=repo, check=True, capture_output=True,
        )
        result = _run(["bash", "scripts/auto_dev_loop.sh", "--dry-run"], cwd=repo)
        assert result.returncode == 0, result.stderr
        assert "AUTO-007" in result.stdout  # dependency report
        # dry-run prints "  Task ID:    A" not the raw ID|title payload
        assert "Task ID:    A" in result.stdout

    def test_dry_run_does_not_modify_tasks_file_with_resolver(self, tmp_path):
        """The dry-run report path must not touch TASKS.md."""
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "docs").mkdir()
        shutil.copy2(LOOP_SCRIPT, repo / "scripts" / "auto_dev_loop.sh")
        shutil.copy2(RESOLVER, repo / "scripts" / "task_dependency_resolver.py")
        tasks_text = textwrap.dedent("""\
            ### A: a (P1)
            - **优先级**：P1
            - **状态**：ready
            """)
        (repo / "docs" / "TASKS.md").write_text(tasks_text, encoding="utf-8")
        (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "i"],
            cwd=repo, check=True, capture_output=True,
        )
        _run(["bash", "scripts/auto_dev_loop.sh", "--dry-run"], cwd=repo)
        assert (repo / "docs" / "TASKS.md").read_text(encoding="utf-8") == tasks_text

    def test_legacy_fallback_when_resolver_missing(self, tmp_path):
        """If the resolver script is missing, the loop must still run via
        the legacy fallback parser and emit the AUTO-007 warning."""
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "docs").mkdir()
        shutil.copy2(LOOP_SCRIPT, repo / "scripts" / "auto_dev_loop.sh")
        # NOTE: deliberately NOT copying task_dependency_resolver.py
        (repo / "docs" / "TASKS.md").write_text(textwrap.dedent("""\
            ### A: a (P1)
            - **优先级**：P1
            - **状态**：ready
            """), encoding="utf-8")
        (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "i"],
            cwd=repo, check=True, capture_output=True,
        )
        result = _run(["bash", "scripts/auto_dev_loop.sh", "--dry-run"], cwd=repo)
        assert result.returncode == 0, result.stderr
        assert "[AUTO-007] task_dependency_resolver.py missing" in result.stderr
        # legacy picker still found the task — dry-run prints "  Task ID:    A"
        assert "Task ID:    A" in result.stdout

    def test_lock_recovery_still_works_with_resolver(self, tmp_path):
        """Stale lock recovery is a fail-stop gate (AUTO-007 must not
        weaken it). End-to-end smoke with the resolver present."""
        import os
        import time
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "docs").mkdir()
        shutil.copy2(LOOP_SCRIPT, repo / "scripts" / "auto_dev_loop.sh")
        shutil.copy2(RESOLVER, repo / "scripts" / "task_dependency_resolver.py")
        (repo / "docs" / "TASKS.md").write_text(textwrap.dedent("""\
            ### TEST-001: t (P1)
            - **状态**：in_progress — claimed TEST-001-20260629
            """), encoding="utf-8")
        (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "i"],
            cwd=repo, check=True, capture_output=True,
        )
        lock_dir = repo / ".auto_dev.lock"
        lock_dir.mkdir()
        old_time = time.strftime("%Y-%m-%d_%H:%M:%S", time.localtime(time.time() - 3600))
        (lock_dir / "owner").write_text(
            f"pid=999999\nstarted_at={old_time}\nrepo={repo}\ntask_id=TEST-001\n",
            encoding="utf-8",
        )
        result = _run(["bash", "scripts/auto_dev_loop.sh", "--dry-run"], cwd=repo)
        assert result.returncode == 0, result.stderr
        assert not lock_dir.exists(), "stale lock must be cleared"
        updated = (repo / "docs" / "TASKS.md").read_text(encoding="utf-8")
        assert "ready" in updated


# ────────────────────────────────────────────────────────────────────
# 10. Static markers — AUTO-007 wiring must be present
# ────────────────────────────────────────────────────────────────────

class TestStaticMarkers:
    def test_resolver_has_auto007_marker(self):
        text = RESOLVER.read_text(encoding="utf-8")
        assert "# [AUTO-007] dependency_aware_claim" in text

    def test_loop_script_has_auto007_marker(self):
        text = LOOP_SCRIPT.read_text(encoding="utf-8")
        assert "# [AUTO-007] dependency_aware_claim" in text

    def test_loop_script_invokes_resolver_claim(self):
        text = LOOP_SCRIPT.read_text(encoding="utf-8")
        assert 'claim --tasks-file' in text

    def test_loop_script_invokes_resolver_release(self):
        text = LOOP_SCRIPT.read_text(encoding="utf-8")
        assert 'release \\' in text
        assert '--task-id' in text

    def test_loop_script_invokes_resolver_dry_run(self):
        text = LOOP_SCRIPT.read_text(encoding="utf-8")
        assert 'dry-run --tasks-file' in text
