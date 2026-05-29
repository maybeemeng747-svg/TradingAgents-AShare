"""Static checks for the auto development runner."""  # [INF-001] task_claim_lock

from __future__ import annotations

import shutil
import subprocess
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
