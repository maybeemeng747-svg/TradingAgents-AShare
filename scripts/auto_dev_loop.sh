#!/usr/bin/env bash
# AUTO-001: 自动开发闭环 v1
# 用法: ./scripts/auto_dev_loop.sh
#
# 约束:
#   - 只执行一轮
#   - 只处理 docs/TASKS.md 中 status=ready 的最高优先级任务
#   - 工作区不干净时退出
#   - 不允许 push / PR / merge
#   - 单任务最多 2 次修复
#   - 不改 prompts/，不写生产 tradingagents.db

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TASKS_FILE="$REPO_DIR/docs/TASKS.md"
DEVLOG_FILE="$REPO_DIR/docs/DEVLOG.md"
MAX_FIX_ROUNDS=2

cd "$REPO_DIR"

# ─── 颜色 ───────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[AUTO]${NC} $*"; }
warn() { echo -e "${YELLOW}[AUTO]${NC} $*"; }
err()  { echo -e "${RED}[AUTO]${NC} $*"; }

# ─── 0. 前置检查 ──────────────────────────────────────
log "=== AUTO-001 自动开发闭环 v1 ==="
log "仓库: $REPO_DIR"

# 检查工作区是否干净（忽略 untracked 的 docs/）
DIRTY=$(git status --porcelain | grep -v '^?? docs/' | head -5)
if [ -n "$DIRTY" ]; then
    err "工作区不干净，退出以避免覆盖用户改动："
    echo "$DIRTY"
    exit 1
fi

# ─── 1. 解析 TASKS.md，找最高优先级 ready 任务 ──────────
# 优先级顺序: P0 > P1 > P2 > P3
# 同优先级内按出现顺序
parse_ready_tasks() {
    python3 - "$TASKS_FILE" <<'PYEOF'
import re, sys

tasks_file = sys.argv[1]
with open(tasks_file, "r") as f:
    content = f.read()

# 匹配 task 块: ### ID: Title ... status: ready ... 一直到下一个 ### 或 ---
pattern = re.compile(
    r"###\s+([\w-]+):\s*(.+?)\n(.*?)(?=\n###|\n---|\Z)",
    re.DOTALL
)

priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

tasks = []
for m in pattern.finditer(content):
    task_id = m.group(1)
    title = m.group(2).strip()
    body = m.group(3)

    # 检查 status=ready
    status_match = re.search(r"\*\*状态\*\*:\s*(ready)", body, re.IGNORECASE)
    if not status_match:
        continue

    # 提取优先级
    prio_match = re.search(r"P(\d)", body)
    prio = f"P{prio_match.group(1)}" if prio_match else "P2"

    # 提取测试命令（从验证方式中提取 pytest 命令）
    test_cmds = re.findall(r"`(pytest\s+[^`]+)`", body)

    tasks.append({
        "id": task_id,
        "title": title,
        "priority": prio,
        "priority_num": priority_order.get(prio, 9),
        "test_cmds": test_cmds,
        "body_start": m.start(),
    })

# 按优先级排序，同优先级按出现顺序
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
    log "没有 status=ready 的任务，退出。"
    exit 0
fi

log "选中任务: [$TASK_PRIO] $TASK_ID: $TASK_TITLE"
[ -n "$TASK_TESTS" ] && log "测试命令: $TASK_TESTS"

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
- 修改后更新 docs/DEVLOG.md
- 测试通过后本地 commit，commit message 格式: <type>: $TASK_ID <简述>

## 完成后输出
- 修改了哪些文件
- 关键逻辑说明
- 测试结果（passed/failed 数量）
- commit hash
PROMPT_EOF
log "OpenCode prompt: $PROMPT_FILE"

# ─── 3. 循环：实现 + 测试 + 审核（最多 MAX_FIX_ROUNDS 轮）──
RESULT_STATUS=""
REVIEW_OUTPUT=""
ROUND=0

while [ $ROUND -lt $MAX_FIX_ROUNDS ]; do
    ROUND=$((ROUND + 1))
    log "--- 第 $ROUND 轮 ---"

    # 3a. 运行 OpenCode
    log "启动 OpenCode..."
    OPENCODE_LOG=$(mktemp -t auto-dev-opencode.XXXXXX)
    opencode run < "$PROMPT_FILE" > "$OPENCODE_LOG" 2>&1 || true
    OPENCODE_EXIT=$?
    log "OpenCode 退出码: $OPENCODE_EXIT"

    # 3b. 运行测试
    TEST_PASS=true
    TEST_OUTPUT=""
    if [ -n "$TASK_TESTS" ]; then
        IFS=',' read -ra TEST_CMD_ARRAY <<< "$TASK_TESTS"
        for test_cmd in "${TEST_CMD_ARRAY[@]}"; do
            test_cmd=$(echo "$test_cmd" | xargs)  # trim
            log "运行测试: $test_cmd"
            TEST_OUTPUT=$(eval "source .venv/bin/activate && $test_cmd" 2>&1) || TEST_PASS=false
            if [ "$TEST_PASS" = false ]; then
                err "测试失败: $test_cmd"
                break
            fi
        done
    else
        # 没有指定测试，跑默认的模块测试
        log "未指定测试，运行默认 pytest..."
        TEST_OUTPUT=$(source .venv/bin/activate && pytest tests/ -q --tb=short 2>&1) || TEST_PASS=false
    fi

    if [ "$TEST_PASS" = false ]; then
        warn "测试未通过，准备修复..."
        # 构造修复 prompt
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
- 修复后重新运行测试确认通过
- 更新 docs/DEVLOG.md
- 不要创建新 commit，amend 到上一个 commit 或单独 commit
FIX_EOF
        continue
    fi

    log "测试通过 ✓"

    # 3c. 运行 Codex review
    log "运行 Codex review..."
    REVIEW_FILE=$(mktemp -t auto-dev-review.XXXXXX)
    codex review --uncommitted -o "$REVIEW_FILE" 2>&1 | tee "$REVIEW_FILE.raw" || true

    # 检查 review 结果
    REVIEW_CONTENT=$(cat "$REVIEW_FILE" 2>/dev/null || echo "")
    REVIEW_RAW=$(cat "$REVIEW_FILE.raw" 2>/dev/null || echo "")

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
- 修复后重新运行测试
- 更新 docs/DEVLOG.md
FIX_EOF
        continue
    fi

    log "Codex review 通过 ✓（无 P0/P1 findings）"
    REVIEW_OUTPUT="$REVIEW_CONTENT"
    RESULT_STATUS="PASS"
    break
done

# ─── 4. 提交结果 ──────────────────────────────────────
if [ "$RESULT_STATUS" = "PASS" ]; then
    # 检查是否有未提交的改动
    CHANGED=$(git diff --name-only; git diff --cached --name-only; git ls-files --others --exclude-standard)
    if [ -n "$CHANGED" ]; then
        log "提交改动..."
        git add -A
        COMMIT_MSG="auto: $TASK_ID $TASK_TITLE [AUTO-001]"
        git commit -m "$COMMIT_MSG"
        COMMIT_HASH=$(git rev-parse --short HEAD)
        log "已提交: $COMMIT_HASH"
    else
        COMMIT_HASH=$(git rev-parse --short HEAD)
        log "无新改动需要提交（OpenCode 可能已自行 commit）"
    fi

    # 写入 DEVLOG
    cat >> "$DEVLOG_FILE" <<DEVLOG_EOF

## $(date +%Y-%m-%d) | AUTO-001 自动开发闭环

- **任务**: $TASK_ID — $TASK_TITLE
- **优先级**: $TASK_PRIO
- **轮次**: $ROUND
- **状态**: ✅ PASS
- **Commit**: $COMMIT_HASH
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
DEVLOG_EOF
    log "已写入 DEVLOG.md"

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

# ─── 5. 输出摘要 ──────────────────────────────────────
echo ""
echo "========================================"
echo "  AUTO-001 执行摘要"
echo "========================================"
echo "  任务:   $TASK_ID — $TASK_TITLE"
echo "  优先级: $TASK_PRIO"
echo "  轮次:   $ROUND"
echo "  状态:   $RESULT_STATUS"
echo "========================================"

# ─── 6. 清理 ──────────────────────────────────────────
rm -f "$PROMPT_FILE" "$OPENCODE_LOG" "$REVIEW_FILE" "$REVIEW_FILE.raw" 2>/dev/null || true

exit $([ "$RESULT_STATUS" = "DONE" ] && echo 0 || echo 1)
