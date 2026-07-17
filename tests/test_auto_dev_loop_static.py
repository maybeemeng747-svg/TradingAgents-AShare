"""Static checks for the auto development runner."""  # [INF-001] task_claim_lock

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "auto_dev_loop.sh"


def test_auto_dev_loop_shell_syntax():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_dry_run_does_not_change_task_status(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir()
    shutil.copy2(SCRIPT, repo / "scripts" / "auto_dev_loop.sh")

    tasks = """# Tasks

### INF-001: 自动开发任务领取锁与 in_progress 状态流转（P0）
- **优先级**：P0
- **状态**：ready
- **验证方式**：
  - `bash -n scripts/auto_dev_loop.sh` 通过。
"""
    tasks_path = repo / "docs" / "TASKS.md"
    tasks_path.write_text(tasks, encoding="utf-8")
    (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        ["bash", "scripts/auto_dev_loop.sh", "--dry-run"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "AUTO-002 DRY-RUN" in result.stdout
    assert tasks_path.read_text(encoding="utf-8") == tasks
    assert not (repo / ".auto_dev.lock").exists()


def test_runner_contains_claim_lock_and_safe_exit_expansion():
    script_text = SCRIPT.read_text(encoding="utf-8")

    assert "# [INF-001] task_claim_lock" in script_text
    assert "update_task_status \"in_progress — claimed $RUN_ID\"" in script_text
    assert "update_task_status \"blocked — NEEDS_HUMAN" in script_text
    assert "exit=${OPENCODE_EXIT}" in script_text
    assert "exit=$OPENCODE_EXIT）" not in script_text


def test_runner_continues_after_success_for_cron_batch_window():
    script_text = SCRIPT.read_text(encoding="utf-8")

    assert 'AUTO_DEV_MAX_TASKS="${AUTO_DEV_MAX_TASKS:-0}"' in script_text
    assert "0 = no explicit cap" in script_text
    assert "continuing to next ready task" in script_text
    assert "stopping (one task per run)" not in script_text


def test_runner_resolves_modern_opencode_cli_before_execution():
    script_text = SCRIPT.read_text(encoding="utf-8")

    assert "resolve_opencode_bin()" in script_text
    assert 'AUTO_DEV_OPENCODE_BIN' in script_text
    assert 'grep -Fq "opencode run"' in script_text
    assert '"$OPENCODE_BIN" run' in script_text
    assert '"$OPENCODE_BIN" debug config' in script_text
    assert 'run_with_timeout "$OPENCODE_TIMEOUT_SECONDS" opencode run' not in script_text
    assert "opencode debug config" not in script_text
    assert "/opt/homebrew/bin/opencode" in script_text


def test_stale_lock_recovery_pid_alive(tmp_path):
    """Lock held by a live process must NOT be cleaned."""
    import os, signal, time

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir()
    shutil.copy2(SCRIPT, repo / "scripts" / "auto_dev_loop.sh")

    tasks = """# Tasks

### TEST-001: Test task (P1)
- **优先级**：P1
- **状态**：in_progress — claimed TEST-001-20260629
"""
    (repo / "docs" / "TASKS.md").write_text(tasks, encoding="utf-8")
    (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com",
         "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )

    # Create lock with a live PID (self)
    lock_dir = repo / ".auto_dev.lock"
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(
        f"pid={os.getpid()}\nstarted_at=2026-01-01_00:00:00\nrepo={repo}\ntask_id=TEST-001\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "scripts/auto_dev_loop.sh", "--dry-run"],
        cwd=repo, text=True, capture_output=True, check=False,
    )

    assert result.returncode != 0
    assert "live process" in result.stderr or "lock" in result.stderr.lower()
    assert lock_dir.exists(), "Lock must NOT be deleted when PID is alive"


def test_stale_lock_recovery_pid_dead_age_too_young(tmp_path):
    """Lock with dead PID but < 30min old must NOT be cleaned."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir()
    shutil.copy2(SCRIPT, repo / "scripts" / "auto_dev_loop.sh")

    tasks = """# Tasks

### TEST-001: Test task (P1)
- **优先级**：P1
- **状态**：in_progress — claimed TEST-001-20260629
"""
    (repo / "docs" / "TASKS.md").write_text(tasks, encoding="utf-8")
    (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com",
         "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )

    # Create lock with dead PID (999999) and recent timestamp
    lock_dir = repo / ".auto_dev.lock"
    lock_dir.mkdir()
    recent_time = time.strftime("%Y-%m-%d_%H:%M:%S", time.localtime(time.time() - 60))
    (lock_dir / "owner").write_text(
        f"pid=999999\nstarted_at={recent_time}\nrepo={repo}\ntask_id=TEST-001\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "scripts/auto_dev_loop.sh", "--dry-run"],
        cwd=repo, text=True, capture_output=True, check=False,
    )

    assert result.returncode != 0
    assert "30min" in result.stderr or "only" in result.stderr.lower()
    assert lock_dir.exists(), "Lock must NOT be deleted when age < 30min"


def test_stale_lock_recovery_clean_tree(tmp_path):
    """Stale lock + clean tree → auto-recover: delete lock, restore task to ready."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir()
    shutil.copy2(SCRIPT, repo / "scripts" / "auto_dev_loop.sh")

    tasks = """# Tasks

### TEST-001: Test task (P1)
- **优先级**：P1
- **状态**：in_progress — claimed TEST-001-20260629
"""
    (repo / "docs" / "TASKS.md").write_text(tasks, encoding="utf-8")
    (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com",
         "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )

    # Create lock with dead PID and old timestamp (> 30min)
    lock_dir = repo / ".auto_dev.lock"
    lock_dir.mkdir()
    old_time = time.strftime("%Y-%m-%d_%H:%M:%S", time.localtime(time.time() - 3600))
    (lock_dir / "owner").write_text(
        f"pid=999999\nstarted_at={old_time}\nrepo={repo}\ntask_id=TEST-001\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "scripts/auto_dev_loop.sh", "--dry-run"],
        cwd=repo, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not lock_dir.exists(), "Stale lock must be deleted"
    updated_tasks = (repo / "docs" / "TASKS.md").read_text(encoding="utf-8")
    assert "ready" in updated_tasks, "Task must be restored to ready"
    assert "in_progress" not in updated_tasks


def test_stale_lock_recovery_dirty_tree(tmp_path):
    """Stale lock + dirty tree → MUST NOT clean, output NEEDS_HUMAN."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir()
    shutil.copy2(SCRIPT, repo / "scripts" / "auto_dev_loop.sh")

    tasks = """# Tasks

### TEST-001: Test task (P1)
- **优先级**：P1
- **状态**：in_progress — claimed TEST-001-20260629
"""
    (repo / "docs" / "TASKS.md").write_text(tasks, encoding="utf-8")
    (repo / "docs" / "DEVLOG.md").write_text("# Devlog\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com",
         "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )

    # Create stale lock
    lock_dir = repo / ".auto_dev.lock"
    lock_dir.mkdir()
    old_time = time.strftime("%Y-%m-%d_%H:%M:%S", time.localtime(time.time() - 3600))
    (lock_dir / "owner").write_text(
        f"pid=999999\nstarted_at={old_time}\nrepo={repo}\ntask_id=TEST-001\n",
        encoding="utf-8",
    )

    # Create dirty file (simulating interrupted OpenCode output)
    (repo / "dirty_file.py").write_text("print('half done')", encoding="utf-8")

    result = subprocess.run(
        ["bash", "scripts/auto_dev_loop.sh", "--dry-run"],
        cwd=repo, text=True, capture_output=True, check=False,
    )

    assert result.returncode != 0
    assert "NEEDS_HUMAN" in result.stderr
    assert lock_dir.exists(), "Lock must NOT be deleted when tree is dirty"
    # Task must stay in_progress
    updated_tasks = (repo / "docs" / "TASKS.md").read_text(encoding="utf-8")
    assert "in_progress" in updated_tasks, "Task must stay in_progress when tree is dirty"
