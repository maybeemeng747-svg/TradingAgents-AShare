#!/usr/bin/env bash
# [AUTO-WORKTREE-GUARD] Auto development may run only from Git's primary worktree.

set -euo pipefail

REPO_DIR="${1:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
if [ -z "$REPO_DIR" ] || [ ! -d "$REPO_DIR" ]; then
    echo "[FROZEN] 无法确认当前仓库目录，禁止自动开发" >&2
    exit 2
fi

PRIMARY_WORKTREE="$(
    git -C "$REPO_DIR" worktree list --porcelain 2>/dev/null \
        | awk '/^worktree / { print substr($0, 10); exit }'
)"
if [ -z "$PRIMARY_WORKTREE" ] || [ ! -d "$PRIMARY_WORKTREE" ]; then
    echo "[FROZEN] 无法确认 Git 主工作树，禁止自动开发" >&2
    exit 2
fi

CURRENT_REAL="$(cd "$REPO_DIR" && pwd -P)"
PRIMARY_REAL="$(cd "$PRIMARY_WORKTREE" && pwd -P)"
if [ "$CURRENT_REAL" != "$PRIMARY_REAL" ]; then
    echo "[FROZEN] 自动开发只允许在主工作树运行" >&2
    echo "  当前: $CURRENT_REAL" >&2
    echo "  主工作树: $PRIMARY_REAL" >&2
    exit 2
fi

exit 0
