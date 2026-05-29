#!/usr/bin/env bash
# AUTO-002: 自动开发闭环 v1.3 可靠性补修
# 用法: ./scripts/auto_dev_loop.sh [--dry-run]
#
# 约束:
#   - 只执行一轮
#   - 只处理 docs/TASKS.md 中 status=ready 的最高优先级可开发任务
#   - 工作区不干净时退出
#   - 不允许 push / PR / merge
#   - 单任务最多 2 次修复
#   - 不改 prompts/，不写生产 tradingagents.db
#   - 精确提交：只允许 tests/ tradingagents/ docs/ 下的文件
#   - 排除临时文件：*.backup, *_original.py, *_fixed.py, patch_*.py, *.tmp, *.log, __pycache__
#   - 排除根目录 *.py（非 tests/ 和 tradingagents/ 下）
#   - 失败时不得自动提交任何文件
#   - DEVLOG/TASKS 在 commit 前完成，commit 后不再修改文件
#   - Review 输出保存到 docs/reviews/
#   - 每个任务运行过程保存到 docs/task_runs/<task>-<timestamp>/

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TASKS_FILE="$REPO_DIR/docs/TASKS.md"
DEVLOG_FILE="$REPO_DIR/docs/DEVLOG.md"
REVIEW_DIR="$REPO_DIR/docs/reviews"
TASK_RUN_ROOT="$REPO_DIR/docs/task_runs"
LOCK_DIR="$REPO_DIR/.auto_dev.lock"
MAX_FIX_ROUNDS=2
DRY_RUN=false
PROMPT_FILE=""
OPENCODE_LOG=""
REVIEW_FILE=""
HAVE_LOCK=false

# ─── 参数解析 ─────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        -h|--help) echo "用法: $0 [--dry-run]"; exit 0 ;;
    esac
done

cd "$REPO_DIR"

# ─── 颜色 ───────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[AUTO]${NC} $*"; }
warn() { echo -e "${YELLOW}[AUTO]${NC} $*"; }
err()  { echo -e "${RED}[AUTO]${NC} $*" >&2; }

redact_log() {
    # 持久化日志前做基础密钥脱敏，避免把本地 API key 写入仓库。
    sed -E \
        -e 's/sk-[A-Za-z0-9_-]{20,}/[REDACTED_API_KEY]/g' \
        -e 's/(api[_-]?key[=:][[:space:]]*)[^[:space:]]+/\1[REDACTED]/Ig' \
        -e 's/(authorization:[[:space:]]*bearer[[:space:]]+)[^[:space:]]+/\1[REDACTED]/Ig'
}

cleanup() {
    rm -f "$PROMPT_FILE" "$OPENCODE_LOG" "$REVIEW_FILE" 2>/dev/null || true
    if [ "$HAVE_LOCK" = true ]; then
        rm -rf "$LOCK_DIR" 2>/dev/null || true
    fi
}

trap cleanup EXIT

acquire_lock() {
    # [INF-001] task_claim_lock
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        HAVE_LOCK=true
        {
            echo "pid=$$"
            echo "started_at=$(date +%Y-%m-%d_%H:%M:%S)"
            echo "repo=$REPO_DIR"
        } > "$LOCK_DIR/owner"
        return 0
    fi

    err "检测到自动开发锁，退出以避免重复领取任务：$LOCK_DIR"
    [ -f "$LOCK_DIR/owner" ] && cat "$LOCK_DIR/owner" >&2
    exit 1
}

update_task_status() {
    # [INF-001] task_claim_lock
    local status_text="$1"
    python3 - "$TASKS_FILE" "$TASK_ID" "$status_text" <<'PYEOF'
import re
import sys

tasks_file, target_id, status_text = sys.argv[1], sys.argv[2], sys.argv[3]
with open(tasks_file, "r", encoding="utf-8") as f:
    content = f.read()

section_pattern = re.compile(
    r"(###\s+" + re.escape(target_id) + r":.*?)(?=\n###|\n---|\Z)",
    re.DOTALL,
)
match = section_pattern.search(content)
if not match:
    print(f"TASKS.md: 未找到任务 {target_id}")
    sys.exit(1)

section = match.group(1)
new_section, n = re.subn(
    r"(- \*\*状态\*\*[：:]\s*).+",
    r"\1" + status_text,
    section,
    count=1,
)
if n == 0:
    print(f"TASKS.md: 未找到 {target_id} 的状态行")
    sys.exit(1)

new_content = content[: match.start(1)] + new_section + content[match.end(1) :]
with open(tasks_file, "w", encoding="utf-8") as f:
    f.write(new_content)
print(f"TASKS.md: {target_id} -> {status_text}")
PYEOF
}

# ─── 0. 前置检查 ──────────────────────────────────────
log "=== AUTO-002 自动开发闭环 v1.3 ==="
log "仓库: $REPO_DIR"

mkdir -p "$REVIEW_DIR" "$TASK_RUN_ROOT"

if [ -d "$LOCK_DIR" ]; then
    err "检测到自动开发锁，退出以避免重复领取任务：$LOCK_DIR"
    [ -f "$LOCK_DIR/owner" ] && cat "$LOCK_DIR/owner" >&2
    exit 1
fi

DIRTY=$(git status --porcelain | head -5 || true)
if [ -n "$DIRTY" ]; then
    err "工作区不干净，退出以避免覆盖用户改动："
    echo "$DIRTY"
    exit 1
fi
log "工作区干净 ✓"

acquire_lock

# ─── 1. 解析 TASKS.md，找最高优先级 ready 任务 ──────────
parse_ready_tasks() {
    python3 - "$TASKS_FILE" <<'PYEOF'
import re, sys

tasks_file = sys.argv[1]
with open(tasks_file, "r") as f:
    content = f.read()

pattern = re.compile(
    r"###\s+([\w-]+):\s*(.+?)\n(.*?)(?=\n###|\n---|\Z)",
    re.DOTALL
)

priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

EXCLUDE_KEYWORDS = {"巡检", "规则", "基线", "checklist", "inspection", "收尾"}
EXCLUDE_ID_PREFIXES = ("R-", "AUTO-")
EXCLUDE_IDS = {"T-000"}

tasks = []
for m in pattern.finditer(content):
    task_id = m.group(1)
    title = m.group(2).strip()
    body = m.group(3)

    if not re.search(r"状态.*?ready", body, re.IGNORECASE):
        continue

    if "已完成" in title or "\u2713" in title:
        continue

    if task_id in EXCLUDE_IDS:
        continue
    if any(task_id.startswith(p) for p in EXCLUDE_ID_PREFIXES):
        continue

    title_lower = title.lower()
    if any(kw in title_lower for kw in EXCLUDE_KEYWORDS):
        continue

    prio_match = re.search(r"P(\d)", body)
    prio = f"P{prio_match.group(1)}" if prio_match else "P2"

    test_cmds = re.findall(r"`(pytest\s+[^`]+)`", body)

    tasks.append({
        "id": task_id,
        "title": title,
        "priority": prio,
        "priority_num": priority_order.get(prio, 9),
        "test_cmds": test_cmds,
        "body_start": m.start(),
    })

tasks.sort(key=lambda t: (t["priority_num"], t["body_start"]))

if tasks:
    t = tasks[0]
    print(f"{t['id']}|{t['title']}|{t['priority']}|{','.join(t['test_cmds'])}")
else:
    print("NONE|||")
PYEOF
}

TASK_LINE=$(parse_ready_tasks)
TASK_ID=$(echo "$TASK_LINE" | cut -d'|' -f1)
TASK_TITLE=$(echo "$TASK_LINE" | cut -d'|' -f2)
TASK_PRIO=$(echo "$TASK_LINE" | cut -d'|' -f3)
TASK_TESTS=$(echo "$TASK_LINE" | cut -d'|' -f4)

if [ "$TASK_ID" = "NONE" ]; then
    log "没有 status=ready 的可开发任务，退出。"
    exit 0
fi

log "选中任务: [$TASK_PRIO] $TASK_ID: $TASK_TITLE"
[ -n "$TASK_TESTS" ] && log "测试命令: $TASK_TESTS"

# ─── dry-run 模式：只打印任务信息，不执行 ────────────────
if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "========================================"
    echo "  AUTO-002 DRY-RUN"
    echo "========================================"
    echo "  任务 ID:   $TASK_ID"
    echo "  任务标题:  $TASK_TITLE"
    echo "  优先级:    $TASK_PRIO"
    echo "  测试命令:  ${TASK_TESTS:-（默认 pytest）}"
    echo "========================================"
    exit 0
fi

# ─── 2a. 建立任务运行档案 ──────────────────────────────
RUN_ID="${TASK_ID}-$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$TASK_RUN_ROOT/$RUN_ID"
mkdir -p "$RUN_DIR"
update_task_status "in_progress — claimed $RUN_ID"

cat > "$RUN_DIR/task.md" <<TASK_META_EOF
# Auto Dev Task Run

- Task: $TASK_ID — $TASK_TITLE
- Priority: $TASK_PRIO
- Status: CLAIMED
- Started at: $(date +%Y-%m-%d_%H:%M:%S)
- Git HEAD: $(git rev-parse --short HEAD)
- Test commands: ${TASK_TESTS:-pytest tests/ -q --tb=short}
- Runner: scripts/auto_dev_loop.sh

## Trace Files

- OpenCode prompts: prompt-round*.md
- OpenCode logs: opencode-round*.txt
- Test logs: tests-round*.txt
- Codex reviews: codex-review-round*.txt
- Final summary: summary.md
TASK_META_EOF
log "任务运行档案: $RUN_DIR"

# ─── 2. 构造 OpenCode prompt ────────────────────────────
PROMPT_FILE=$(mktemp -t auto-dev-prompt.XXXXXX)
cat > "$PROMPT_FILE" <<PROMPT_EOF
# 任务: $TASK_ID: $TASK_TITLE

请从 docs/TASKS.md 中读取任务 "$TASK_ID" 的完整描述，按要求实现。

## 执行约束
- 不改 tradingagents/prompts/
- 不写入生产 tradingagents.db
- 不跑全市场扫描或股票深度 TA
- 不 push / PR / merge
- **不要 git commit** — commit 由外层脚本统一执行
- 修改后更新 docs/DEVLOG.md

## 完成后输出
- 修改了哪些文件
- 关键逻辑说明
- 测试结果（passed/failed 数量）
PROMPT_EOF
log "OpenCode prompt: $PROMPT_FILE"

# ─── 3. 循环：实现 + 测试 + 审核（最多 MAX_FIX_ROUNDS 轮）──
RESULT_STATUS=""
REVIEW_OUTPUT=""
ROUND=0
LAST_FAILURE_REASON=""

while [ $ROUND -lt $MAX_FIX_ROUNDS ]; do
    ROUND=$((ROUND + 1))
    log "--- 第 $ROUND 轮 ---"
    cp "$PROMPT_FILE" "$RUN_DIR/prompt-round${ROUND}.md"

    # 3a. 运行 OpenCode
    log "启动 OpenCode..."
    OPENCODE_LOG=$(mktemp -t auto-dev-opencode.XXXXXX)
    set +e
    opencode run < "$PROMPT_FILE" > "$OPENCODE_LOG" 2>&1
    OPENCODE_EXIT=$?
    set -e
    log "OpenCode 退出码: $OPENCODE_EXIT"
    redact_log < "$OPENCODE_LOG" > "$RUN_DIR/opencode-round${ROUND}.txt"

    if [ $OPENCODE_EXIT -ne 0 ]; then
        LAST_FAILURE_REASON="OpenCode failed with exit ${OPENCODE_EXIT}"
        err "OpenCode 执行失败（exit=${OPENCODE_EXIT}）"
        err "日志: $OPENCODE_LOG"
        cat > "$PROMPT_FILE" <<FIX_EOF
# 修复任务: $TASK_ID

上一轮 OpenCode 执行失败（exit code ${OPENCODE_EXIT}）。请修复：

## OpenCode 日志（最后 30 行）
\`\`\`
$(tail -30 "$OPENCODE_LOG")
\`\`\`

## 约束
- 不改 tradingagents/prompts/
- 不写入生产 tradingagents.db
- 不 push
- **不要 git commit**
- 修复后更新 docs/DEVLOG.md
FIX_EOF
        continue
    fi

    # 3b. 运行测试
    TEST_PASS=true
    TEST_OUTPUT=""
    TEST_LOG_FILE="$RUN_DIR/tests-round${ROUND}.txt"
    : > "$TEST_LOG_FILE"
    if [ -n "$TASK_TESTS" ]; then
        IFS=',' read -ra TEST_CMD_ARRAY <<< "$TASK_TESTS"
        for test_cmd in "${TEST_CMD_ARRAY[@]}"; do
            test_cmd=$(echo "$test_cmd" | xargs)
            log "运行测试: $test_cmd"
            {
                echo "## $test_cmd"
                echo
            } >> "$TEST_LOG_FILE"
            set +e
            TEST_OUTPUT=$(source .venv/bin/activate && eval "$test_cmd" 2>&1)
            TEST_EXIT=$?
            set -e
            echo "$TEST_OUTPUT" | redact_log >> "$TEST_LOG_FILE"
            {
                echo
                echo "Exit code: $TEST_EXIT"
                echo
            } >> "$TEST_LOG_FILE"
            if [ $TEST_EXIT -ne 0 ]; then
                TEST_PASS=false
                LAST_FAILURE_REASON="Test failed: ${test_cmd} (exit ${TEST_EXIT})"
                err "测试失败: $test_cmd (exit=$TEST_EXIT)"
                break
            fi
        done
    else
        log "未指定测试，运行默认 pytest..."
        set +e
        TEST_OUTPUT=$(source .venv/bin/activate && pytest tests/ -q --tb=short 2>&1)
        TEST_EXIT=$?
        set -e
        {
            echo "## pytest tests/ -q --tb=short"
            echo
            echo "$TEST_OUTPUT" | redact_log
            echo
            echo "Exit code: $TEST_EXIT"
        } >> "$TEST_LOG_FILE"
        if [ $TEST_EXIT -ne 0 ]; then
            TEST_PASS=false
            LAST_FAILURE_REASON="Default pytest failed with exit ${TEST_EXIT}"
        fi
    fi

    if [ "$TEST_PASS" = false ]; then
        warn "测试未通过，准备修复..."
        cat > "$PROMPT_FILE" <<FIX_EOF
# 修复任务: $TASK_ID

之前的实现测试未通过。请修复以下问题：

## 测试输出（最后 30 行）
\`\`\`
$(echo "$TEST_OUTPUT" | tail -30)
\`\`\`

## 约束
- 不改 tradingagents/prompts/
- 不写入生产 tradingagents.db
- 不 push
- **不要 git commit**
- 修复后更新 docs/DEVLOG.md
FIX_EOF
        continue
    fi

    log "测试通过 ✓"

    # 3c. 运行 Codex review
    log "运行 Codex review..."
    REVIEW_FILE=$(mktemp -t auto-dev-review.XXXXXX)
    set +e
    codex review --uncommitted > "$REVIEW_FILE" 2>&1
    CODEX_EXIT=$?
    set -e
    log "Codex 退出码: $CODEX_EXIT"

    REVIEW_CONTENT=$(cat "$REVIEW_FILE" 2>/dev/null || echo "")

    # 保存完整 review 到 docs/reviews/（含退出码）
    REVIEW_SAVE_PATH="$REVIEW_DIR/${TASK_ID}-$(date +%Y%m%d)-round${ROUND}.txt"
    {
        echo "Task: $TASK_ID — $TASK_TITLE"
        echo "Date: $(date +%Y-%m-%d_%H:%M:%S)"
        echo "Codex exit code: $CODEX_EXIT"
        echo "Round: $ROUND"
        echo "---"
        cat "$REVIEW_FILE"
    } > "$REVIEW_SAVE_PATH"
    cp "$REVIEW_SAVE_PATH" "$RUN_DIR/codex-review-round${ROUND}.txt"
    log "Review 已保存: $REVIEW_SAVE_PATH"

    # Codex review 失败 → 不信任结果，进入修复或 NEEDS_HUMAN
    if [ $CODEX_EXIT -ne 0 ]; then
        LAST_FAILURE_REASON="Codex review failed with exit ${CODEX_EXIT}"
        err "Codex review 执行失败（exit=${CODEX_EXIT}），不信任空结果"
        cat > "$PROMPT_FILE" <<FIX_EOF
# 修复任务: $TASK_ID

Codex review 上一轮执行失败（exit code $CODEX_EXIT）。请检查代码质量并修复潜在问题。

## 约束
- 不改 tradingagents/prompts/
- 不写入生产 tradingagents.db
- 不 push
- **不要 git commit**
- 修复后更新 docs/DEVLOG.md
FIX_EOF
        continue
    fi

    # 检查是否有 P0/P1 findings
    HAS_CRITICAL=false
    if echo "$REVIEW_CONTENT" | grep -qiE "(P0|P1|critical|must.fix|blocker|严重|必须修复)"; then
        HAS_CRITICAL=true
    fi

    if [ "$HAS_CRITICAL" = true ]; then
        LAST_FAILURE_REASON="Codex review reported P0/P1 findings"
        warn "Codex review 发现 P0/P1 问题，准备修复..."
        cat > "$PROMPT_FILE" <<FIX_EOF
# 修复任务: $TASK_ID

Codex review 发现了关键问题，请按 review 意见修复：

## Codex Review 输出
\`\`\`
$(echo "$REVIEW_CONTENT" | head -50)
\`\`\`

## 约束
- 不改 tradingagents/prompts/
- 不写入生产 tradingagents.db
- 不 push
- **不要 git commit**
- 修复后更新 docs/DEVLOG.md
FIX_EOF
        continue
    fi

    log "Codex review 通过 ✓（无 P0/P1 findings）"
    REVIEW_OUTPUT="$REVIEW_CONTENT"
    RESULT_STATUS="PASS"
    break
done

# ─── 4. 处理结果 ──────────────────────────────────────
COMMIT_HASH=""

if [ "$RESULT_STATUS" = "PASS" ]; then
    cat > "$RUN_DIR/summary.md" <<SUMMARY_EOF
# Auto Dev Summary

- Task: $TASK_ID — $TASK_TITLE
- Priority: $TASK_PRIO
- Final status: PASS
- Rounds: $ROUND
- Tests: PASS
- Codex review: no P0/P1 findings
- Review file: docs/reviews/${TASK_ID}-$(date +%Y%m%d)-round${ROUND}.txt
- Run directory: docs/task_runs/$RUN_ID
- Finished at: $(date +%Y-%m-%d_%H:%M:%S)
SUMMARY_EOF

    # 4a. 精确 git add：允许 tests/ tradingagents/ docs/ scripts/
    log "精确提交：git add tests/ tradingagents/ docs/ scripts/"
    git add tests/ tradingagents/ docs/ scripts/

    # 4b. 排除临时文件和备份文件
    EXCLUDE_PATTERNS=('*.backup' '*_original.py' '*_fixed.py' 'patch_*.py' '*.tmp' '*.log')
    for pat in "${EXCLUDE_PATTERNS[@]}"; do
        git reset HEAD -- "$pat" 2>/dev/null || true
    done

    # 排除根目录 .py 文件（只保留 tests/ 和 tradingagents/ 下的）
    git diff --cached --name-only -- '*.py' | while read -r f; do
        if [ "$(dirname "$f")" = "." ]; then
            git reset HEAD -- "$f"
        fi
    done

    # 4c. 提交前确认清单：检查暂存区文件
    STAGED=$(git diff --cached --name-only)
    if [ -z "$STAGED" ]; then
        log "无有效文件需要提交"
        RESULT_STATUS="DONE"
    else
        log "将提交的文件清单："
        echo "$STAGED"

        # 检查是否有不应提交的文件
        SUSPICIOUS=$(echo "$STAGED" | grep -E '^([^/]+\.py$|.*\.backup$|.*_original\.py$|.*_fixed\.py$|patch_.*\.py$|.*\.tmp$|.*\.log$|__pycache__)' || true)
        if [ -n "$SUSPICIOUS" ]; then
            err "检测到不应提交的文件："
            echo "$SUSPICIOUS"
            err "中止提交，标记 NEEDS_HUMAN"
            git reset HEAD
            RESULT_STATUS="NEEDS_HUMAN"
        fi
    fi
fi

if [ "$RESULT_STATUS" = "PASS" ]; then
    # 4d. 写入 DEVLOG（commit 前，将包含在 commit 中）
    cat >> "$DEVLOG_FILE" <<DEVLOG_EOF

## $(date +%Y-%m-%d) | AUTO-002 自动开发闭环

- **任务**: $TASK_ID — $TASK_TITLE
- **优先级**: $TASK_PRIO
- **轮次**: $ROUND
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/${TASK_ID}-$(date +%Y%m%d)-round${ROUND}.txt
- **运行档案**: docs/task_runs/$RUN_ID/
DEVLOG_EOF
    log "已写入 DEVLOG.md"

    # 4e. 将 DEVLOG/TASKS 变更加入暂存区
    git add docs/

    # 4f. 提交实现与运行档案。
    COMMIT_MSG="auto: $TASK_ID $TASK_TITLE [AUTO-002]"
    git commit -m "$COMMIT_MSG"
    COMMIT_HASH=$(git rev-parse --short HEAD)
    log "已提交: $COMMIT_HASH"

    # 4g. PASS 后用独立文档提交写入准确 commit hash，避免任务继续被领取。
    update_task_status "done — commit ${COMMIT_HASH}"
    git add docs/TASKS.md
    git commit -m "docs: mark $TASK_ID done after auto run"
    RESULT_STATUS="DONE"

elif [ "$RESULT_STATUS" != "DONE" ]; then
    # 超过最大轮数 或 pre-commit 检查失败 → 不提交任何文件
    err "需要人工介入"
    RESULT_STATUS="NEEDS_HUMAN"
fi

if [ "$RESULT_STATUS" = "NEEDS_HUMAN" ]; then
    if [ -z "$LAST_FAILURE_REASON" ]; then
        LAST_FAILURE_REASON="tests/review/pre-commit did not pass within the allowed rounds"
    fi
    cat > "$RUN_DIR/summary.md" <<SUMMARY_EOF
# Auto Dev Summary

- Task: $TASK_ID — $TASK_TITLE
- Priority: $TASK_PRIO
- Final status: NEEDS_HUMAN
- Rounds: $ROUND
- Reason: $LAST_FAILURE_REASON
- Run directory: docs/task_runs/$RUN_ID
- Finished at: $(date +%Y-%m-%d_%H:%M:%S)

人工处理前请先查看本目录下的 OpenCode、测试和 Codex review 日志。
SUMMARY_EOF

    update_task_status "blocked — NEEDS_HUMAN, see docs/task_runs/$RUN_ID"

    cat >> "$DEVLOG_FILE" <<DEVLOG_EOF

## $(date +%Y-%m-%d) | AUTO-002 自动开发闭环

- **任务**: $TASK_ID — $TASK_TITLE
- **优先级**: $TASK_PRIO
- **轮次**: $ROUND (max)
- **状态**: ❌ NEEDS_HUMAN
- **原因**: $LAST_FAILURE_REASON
- **运行档案**: docs/task_runs/$RUN_ID/
DEVLOG_EOF
    log "已写入 DEVLOG.md（未提交）"
fi

# ─── 5. 输出摘要 ──────────────────────────────────────
echo ""
echo "========================================"
echo "  AUTO-002 执行摘要"
echo "========================================"
echo "  任务:   $TASK_ID — $TASK_TITLE"
echo "  优先级: $TASK_PRIO"
echo "  轮次:   $ROUND"
echo "  状态:   $RESULT_STATUS"
echo "  档案:   docs/task_runs/$RUN_ID/"
if [ -n "$COMMIT_HASH" ]; then
    echo "  提交:   $COMMIT_HASH"
fi
echo "========================================"

exit $([ "$RESULT_STATUS" = "DONE" ] && echo 0 || echo 1)
