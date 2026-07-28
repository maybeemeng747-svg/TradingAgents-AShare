"""Regression tests for the automatic-development primary-worktree guard."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = REPO_ROOT / "scripts" / "primary_worktree_guard.sh"
PREFLIGHT = REPO_ROOT / "scripts" / "preflight_check.sh"
AUTO_DEV = REPO_ROOT / "scripts" / "auto_dev_loop.sh"


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _build_repo_with_secondary_worktree(tmp_path: Path) -> tuple[Path, Path]:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    assert _run("git", "init", "-b", "main", cwd=primary).returncode == 0
    assert _run("git", "config", "user.email", "test@example.com", cwd=primary).returncode == 0
    assert _run("git", "config", "user.name", "Test", cwd=primary).returncode == 0
    (primary / "README.md").write_text("fixture\n", encoding="utf-8")
    assert _run("git", "add", "README.md", cwd=primary).returncode == 0
    assert _run("git", "commit", "-m", "fixture", cwd=primary).returncode == 0
    result = _run(
        "git",
        "worktree",
        "add",
        "-b",
        "secondary",
        str(secondary),
        cwd=primary,
    )
    assert result.returncode == 0, result.stderr
    return primary, secondary


def test_primary_worktree_is_allowed(tmp_path: Path) -> None:
    primary, _ = _build_repo_with_secondary_worktree(tmp_path)
    result = _run(str(GUARD), str(primary), cwd=primary)
    assert result.returncode == 0, result.stderr


def test_secondary_worktree_is_frozen_even_after_branch_rename(tmp_path: Path) -> None:
    primary, secondary = _build_repo_with_secondary_worktree(tmp_path)
    assert _run("git", "branch", "-m", "renamed-secondary", cwd=secondary).returncode == 0
    result = _run(str(GUARD), str(secondary), cwd=secondary)
    assert result.returncode == 2
    assert "[FROZEN]" in result.stderr
    assert str(primary) in result.stderr


def test_both_auto_dev_entrypoints_call_shared_guard() -> None:
    for script in (PREFLIGHT, AUTO_DEV):
        text = script.read_text(encoding="utf-8")
        assert "primary_worktree_guard.sh" in text
        assert '"$PRIMARY_WORKTREE_GUARD" "$REPO_DIR"' in text
