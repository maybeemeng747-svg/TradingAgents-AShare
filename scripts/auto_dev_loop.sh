#!/usr/bin/env bash
# AUTO-001: 自动开发闭环 v1.1
# 用法: ./scripts/auto_dev_loop.sh [--dry-run]
#
# 约束:
#   - 只执行一轮
#   - 只处理 docs/TASKS.md 中 status=ready 的最高优先级可开发任务
#   - 工作区不干净时退出
#   - 不允许 push / PR / merge
#   - 单任务最多 2 次修复
#   - 不改 prompts/，不写生产 tradingagents.db

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TASKS_FILE="$REPO_DIR/docs/TASKS.md"
DEVLOG_FILE="$REPO_DIR/docs/DEVLOG.md"
MAX_FIX_ROUNDS=2
DRY_RUN=false

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

# ─── 0. 前置检查 ──────────────────────────────────────
log "=== AUTO-001 自动开发闭环 v1.1 ==="
log "仓库: $REPO_DIR"

# 检查工作区是否干净（忽略 untracked 的 docs/）
# 注意: || true 防止 grep 在无匹配时 set -e 退出
DIRTY=$(git status --porcelain | grep -v '^?? docs/' | head -5 || true)
if [ -n "$DIRTY" ]; then
    err "工作区不干净，退出以避免覆盖用户改动："
    echo "$DIRTY"
    exit 1
fi
log "工作区干净 ✓"

# ─── 1. 解析 TASKS.md，找最高优先级 ready 任务 ──────────
# 优先级顺序: P0 > P1 > P2 > P3
# 排除巡检/规则/基线类任务（非代码开发任务）
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

# 排除关键词：巡检、规则、基线、checklist 等非开发任务
EXCLUDE_KEYWORDS = {"巡检", "规则", "基线", "checklist", "inspection"}

tasks = []
for m in pattern.finditer(content):
    task_id = m.group(1)
    title = m.group(2).strip()
    body = m.group(3)

    # 检查 status=ready
    if not re.search(r"状态.*?ready", body, re.IGNORECASE):
        continue

    # 排除巡检/规则类任务
    title_lower = title.lower()
    if any(kw in title_lower for kw in EXCLUDE_KEYWORDS):
        continue

    # 提取优先级
    prio_match = re.search(r"P(\d)", body)
    prio = f"P{prio_match.group(1)}" if prio_match else "P2"

    # 提取测试命令
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
    echo "  AUTO-001 DRY-RUN"
    echo "========================================"
    echo "  任务 ID:   $TASK_ID"
    echo "  任务标题:  $TASK_TITLE"
    echo "  优先级:    $TASK_PRIO"
    echo "  测试命令:  ${TASK_TESTS:-（默认 pytest）}"
    echo "========================================"
    exit 0
fi

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

while [ $ROUND -lt $MAX_FIX_ROUNDS ]; do
    ROUND=$((ROUND + 1))
    log "--- 第 $ROUND 轮 ---"

    # 3a. 运行 OpenCode（不吞失败）
    log "启动 OpenCode..."
    OPENCODE_LOG=$(mktemp -t auto-dev-opencode.XXXXXX)
    set +e
    opencode run < "$PROMPT_FILE" > "$OPENCODE_LOG" 2>&1
    OPENCODE_EXIT=$?
    set -e
    log "OpenCode 退出码: $OPENCODE_EXIT"

    if [ $OPENCODE_EXIT -ne 0 ]; then
        err "OpenCode 执行失败（exit=$OPENCODE_EXIT）"
        err "日志: $OPENCODE_LOG"
        # 构造修复 prompt
        cat > "$PROMPT_FILE" <<FIX_EOF
# 修复任务: $TASK_ID

上一轮 OpenCode 执行失败（exit code $OPENCODE_EXIT）。请修复：

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
    if [ -n "$TASK_TESTS" ]; then
        IFS=',' read -ra TEST_CMD_ARRAY <<< "$TASK_TESTS"
        for test_cmd in "${TEST_CMD_ARRAY[@]}"; do
            test_cmd=$(echo "$test_cmd" | xargs)
            log "运行测试: $test_cmd"
            set +e
            TEST_OUTPUT=$(source .venv/bin/activate && eval "$test_cmd" 2>&1)
            TEST_EXIT=$?
            set -e
            if [ $TEST_EXIT -ne 0 ]; then
                TEST_PASS=false
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
        if [ $TEST_EXIT -ne 0 ]; then
            TEST_PASS=false
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

    # 3c. 运行 Codex review（stdout 重定向，不用 -o）
    log "运行 Codex review..."
    REVIEW_FILE=$(mktemp -t auto-dev-review.XXXXXX)
    set +e
    codex review --uncommitted > "$REVIEW_FILE" 2>&1
    CODEX_EXIT=$?
    set -e
    log "Codex 退出码: $CODEX_EXIT"

    REVIEW_CONTENT=$(cat "$REVIEW_FILE" 2>/dev/null || echo "")

    # Codex review 失败 → 不信任结果，进入修复或 NEEDS_HUMAN
    if [ $CODEX_EXIT -ne 0 ]; then
        err "Codex review 执行失败（exit=$CODEX_EXIT），不信任空结果"
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

# ─── 4. 更新 DEVLOG + TASKS 状态（commit 之前）────────
if [ "$RESULT_STATUS" = "PASS" ]; then
    # 写入 DEVLOG
    COMMIT_HASH_PLACEHOLDER="pending"
    cat >> "$DEVLOG_FILE" <<DEVLOG_EOF

## $(date +%Y-%m-%d) | AUTO-001 自动开发闭环

- **任务**: $TASK_ID — $TASK_TITLE
- **优先级**: $TASK_PRIO
- **轮次**: $ROUND
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
DEVLOG_EOF
    log "已写入 DEVLOG.md"

    # 更新 TASKS.md 中该任务状态为 done
    python3 - "$TASKS_FILE" "$TASK_ID" <<'PYEOF'
import re, sys

tasks_file, target_id = sys.argv[1], sys.argv[2]
with open(tasks_file, "r") as f:
    content = f.read()

# 替换该任务块中的状态行
pattern = re.compile(
    r"(###\s+" + re.escape(target_id) + r":.*?\n.*?)"
    r"(\*\*状态\*\*[：:]\s*)ready",
    re.DOTALL
)
new_content, n = pattern.subn(r"\1\2done", content)
if n > 0:
    with open(tasks_file, "w") as f:
        f.write(new_content)
    print(f"TASKS.md: {target_id} → done")
else:
    print(f"TASKS.md: 未找到 {target_id} 的 ready 状态行")
PYEOF

    # 5. 提交改动
    CHANGED=$(git diff --name-only; git diff --cached --name-only; git ls-files --others --exclude-standard)
    if [ -n "$CHANGED" ]; then
        log "提交改动..."
        git add -A
        COMMIT_MSG="auto: $TASK_ID $TASK_TITLE [AUTO-001]"
        git commit -m "$COMMIT_MSG"
        COMMIT_HASH=$(git rev-parse --short HEAD)
        log "已提交: $COMMIT_HASH"

        # 回填 commit hash 到 DEVLOG
        sed -i '' "s/$COMMIT_HASH_PLACEHOLDER/$COMMIT_HASH/" "$DEVLOG_FILE"
    else
        log "无新改动需要提交"
    fi

    RESULT_STATUS="DONE"
else
    # 超过最大轮数
    err "超过最大修复轮次 ($MAX_FIX_ROUNDS)，需要人工介入"
    RESULT_STATUS="NEEDS_HUMAN"

    cat >> "$DEVLOG_FILE" <<DEVLOG_EOF

## $(date +%Y-%m-%d) | AUTO-001 自动开发闭环

- **任务**: $TASK_ID — $TASK_TITLE
- **优先级**: $TASK_PRIO
- **轮次**: $ROUND (max)
- **状态**: ❌ NEEDS_HUMAN
- **原因**: 测试或 review 未通过，超过最大修复轮次
DEVLOG_EOF
    log "已写入 DEVLOG.md"
fi

# ─── 6. 输出摘要 ──────────────────────────────────────
echo ""
echo "========================================"
echo "  AUTO-001 执行摘要"
echo "========================================"
echo "  任务:   $TASK_ID — $TASK_TITLE"
echo "  优先级: $TASK_PRIO"
echo "  轮次:   $ROUND"
echo "  状态:   $RESULT_STATUS"
echo "========================================"

# ─── 7. 清理 ──────────────────────────────────────────
rm -f "$PROMPT_FILE" "$OPENCODE_LOG" "$REVIEW_FILE" 2>/dev/null || true

exit $([ "$RESULT_STATUS" = "DONE" ] && echo 0 || echo 1)
