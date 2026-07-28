#!/usr/bin/env bash
# T-000: 自动开发巡检基线
# 用法: ./scripts/preflight_check.sh [--skip-tests] [--quiet] [--skip-db-hygiene]
#
# 每次 OpenClaw 自动开发开始前运行，检查项目状态、敏感风险、测试健康和 token/API 消耗风险。
# 可被 auto_dev_loop.sh 在领取任务前调用。
#
# Exit codes:
#   0 = 通过，可进入开发
#   1 = 有风险需人工确认（有未提交变更、低风险敏感文件等）
#   2 = 严重风险禁止继续（生产 DB 被改、prompt 被改、env 泄露等）
#
# 选项:
#   --skip-tests        跳过测试运行（只做静态检查）
#   --skip-db-hygiene   跳过 [AUTO-005] DB hygiene dry-run（仅当 Python 不可用或
#                       明确不需要污染检查时使用）
#   --quiet             减少输出，只打印摘要

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

SKIP_TESTS=false
QUIET=false
# [AUTO-005] db_hygiene_preflight
SKIP_DB_HYGIENE=false

for arg in "$@"; do
    case "$arg" in
        --skip-tests) SKIP_TESTS=true ;;
        --skip-db-hygiene) SKIP_DB_HYGIENE=true ;;  # [AUTO-005] db_hygiene_preflight
        --quiet)      QUIET=true ;;
        -h|--help)
            echo "用法: $0 [--skip-tests] [--skip-db-hygiene] [--quiet]"
            echo "  --skip-tests        跳过测试运行"
            echo "  --skip-db-hygiene   跳过 DB hygiene dry-run"
            echo "  --quiet             减少输出"
            echo ""
            echo "Exit codes: 0=通过, 1=有风险, 2=严重风险"
            exit 0
            ;;
    esac
done

# ─── 颜色和输出辅助 ─────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

RISK_LEVEL=0
RISK_NOTES=()

section() { $QUIET || echo -e "\n--- $1 ---"; }
log()     { $QUIET || echo -e "  $*"; }
warn()    { echo -e "  ${YELLOW}[WARN]${NC} $*"; RISK_LEVEL=$((RISK_LEVEL > 0 ? RISK_LEVEL : 1)); RISK_NOTES+=("$*"); }
err()     { echo -e "  ${RED}[RISK]${NC} $*"; RISK_LEVEL=2; RISK_NOTES+=("$*"); }
ok()      { $QUIET || echo -e "  ${GREEN}[OK]${NC} $*"; }

NOW="$(date '+%Y-%m-%d %H:%M:%S')"
echo "=== 自动开发巡检报告 ==="
echo "时间: $NOW"

# ═══════════════════════════════════════════════════════════
# 1. Git 状态
# ═══════════════════════════════════════════════════════════
section "Git 状态"

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")
log "分支: $BRANCH"

# [SCORE-001C] 工作树冻结门禁：禁止在 codex/score-contract-v1.1 上运行自动开发
if [ "$BRANCH" = "codex/score-contract-v1.1" ]; then
    echo -e "  ${RED}[FROZEN]${NC} codex/score-contract-v1.1 工作树已冻结（落后主分支，禁止新开发）"
    echo -e "  请在主分支 local/tradingagents-custom 上运行自动开发"
    exit 2
fi

REMOTE=$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || echo "无远端跟踪")
log "远端: $REMOTE"

# ahead/behind
AHEAD_BEHIND=""
if git rev-parse '@{upstream}' >/dev/null 2>&1; then
    AHEAD=$(git rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo "?")
    BEHIND=$(git rev-list --count 'HEAD..@{upstream}' 2>/dev/null || echo "?")
    AHEAD_BEHIND="ahead ${AHEAD}, behind ${BEHIND}"
    log "同步: $AHEAD_BEHIND"
else
    log "同步: 无远端分支"
fi

# 未提交文件
DIRTY_FILES=$(git status --porcelain 2>/dev/null || true)
DIRTY_COUNT=0
if [ -n "$DIRTY_FILES" ]; then
    DIRTY_COUNT=$(echo "$DIRTY_FILES" | wc -l | tr -d ' ')
    warn "未提交: ${DIRTY_COUNT} 个文件"
    $QUIET || echo "$DIRTY_FILES" | head -20 | while IFS= read -r line; do
        echo "    $line"
    done
    if [ "$DIRTY_COUNT" -gt 20 ]; then
        log "    ... (仅显示前 20 个)"
    fi
else
    ok "工作区干净"
fi

# 最近 5 个 commit
section "最近 5 个 commit"
git log --oneline -5 2>/dev/null | while IFS= read -r line; do
    log "$line"
done || true

# ═══════════════════════════════════════════════════════════
# 2. Diff 摘要（敏感文件标记）
# ═══════════════════════════════════════════════════════════
section "变更摘要"

# 检查是否有未提交变更
HAS_DIFF=false
STAGED_DIFF=$(git diff --cached --stat 2>/dev/null || true)
UNSTAGED_DIFF=$(git diff --stat 2>/dev/null || true)
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null || true)

if [ -n "$STAGED_DIFF" ] || [ -n "$UNSTAGED_DIFF" ]; then
    HAS_DIFF=true
fi

# 按文件分组显示改动
SENSITIVE_PATTERNS=(
    "tradingagents/prompts/"
    "tradingagents.db"
    ".env"
    "eval_results/"
    "logs/"
    "scheduler/"
    "scripts/check_zai_quota.sh"
    "scripts/auto_dev_loop.sh"
    "tradingagents/llm_clients/"
    "tradingagents/default_config.py"
    ".gitignore"
)

if [ "$HAS_DIFF" = true ]; then
    ALL_CHANGED=$(git diff --cached --name-only 2>/dev/null; git diff --name-only 2>/dev/null)
    if [ -n "$ALL_CHANGED" ]; then
        log "已修改文件:"
        echo "$ALL_CHANGED" | sort -u | while IFS= read -r f; do
            [ -z "$f" ] && continue
            SENSITIVE=""
            for pat in "${SENSITIVE_PATTERNS[@]}"; do
                if [[ "$f" == *"$pat"* ]]; then
                    SENSITIVE=" <<敏感>>"
                    break
                fi
            done
            log "  - $f$SENSITIVE"
        done
    fi
else
    ok "无未提交变更"
fi

# 检查未跟踪文件
if [ -n "$UNTRACKED" ]; then
    UNTRACKED_COUNT=$(echo "$UNTRACKED" | wc -l | tr -d ' ')
    log "未跟踪文件: ${UNTRACKED_COUNT} 个"
    $QUIET || echo "$UNTRACKED" | head -15 | while IFS= read -r f; do
        echo "    ? $f"
    done
    if [ "$UNTRACKED_COUNT" -gt 15 ]; then
        log "    ... (仅显示前 15 个)"
    fi
fi

# ═══════════════════════════════════════════════════════════
# 2b. 敏感文件专项检查
# ═══════════════════════════════════════════════════════════
section "敏感风险"

# tradingagents/prompts/ 是否被修改
PROMPTS_CHANGED=$(git diff --cached --name-only -- 'tradingagents/prompts/' 2>/dev/null; git diff --name-only -- 'tradingagents/prompts/' 2>/dev/null)
if [ -n "$PROMPTS_CHANGED" ]; then
    err "tradingagents/prompts/ 被修改（需审批）："
    echo "$PROMPTS_CHANGED" | while IFS= read -r f; do echo "    $f"; done
fi

# tradingagents.db 是否被修改（staged 或 unstaged 或 untracked）
DB_FILES="tradingagents.db tradingagents.db-shm tradingagents.db-wal"
for db in $DB_FILES; do
    if git diff --cached --name-only -- "$db" 2>/dev/null | grep -q .; then
        err "生产数据库 $db 被 staged"
    fi
    if git diff --name-only -- "$db" 2>/dev/null | grep -q .; then
        err "生产数据库 $db 有未提交修改"
    fi
done

# .env 是否被修改
if git diff --cached --name-only -- '.env' '.env.*' 2>/dev/null | grep -q .; then
    err ".env 文件被 staged（禁止提交密钥）"
fi
if git diff --name-only -- '.env' '.env.*' 2>/dev/null | grep -q .; then
    warn ".env 文件有修改"
fi

# 检查 .env 是否被 git 跟踪
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
    err ".env 被 Git 跟踪（应加入 .gitignore）"
fi

# eval_results/ 是否被修改
if git diff --cached --name-only -- 'eval_results/' 2>/dev/null | grep -q .; then
    warn "eval_results/ 有 staged 变更"
fi

# logs/ 是否被修改
if git diff --cached --name-only -- 'logs/' 2>/dev/null | grep -q .; then
    warn "logs/ 有 staged 变更"
fi

# 模型配置是否被修改
MODEL_CONFIG_CHANGED=$(git diff --name-only -- 'tradingagents/llm_clients/' 'tradingagents/default_config.py' 2>/dev/null)
if [ -n "$MODEL_CONFIG_CHANGED" ]; then
    warn "模型配置文件被修改："
    echo "$MODEL_CONFIG_CHANGED" | while IFS= read -r f; do echo "    $f"; done
fi

# scheduler 是否被修改
SCHEDULER_CHANGED=$(git diff --name-only -- 'scheduler/' 2>/dev/null)
if [ -n "$SCHEDULER_CHANGED" ]; then
    warn "定时任务调度器被修改："
    echo "$SCHEDULER_CHANGED" | while IFS= read -r f; do echo "    $f"; done
fi

if [ -z "$PROMPTS_CHANGED" ] && [ -z "$MODEL_CONFIG_CHANGED" ] && [ -z "$SCHEDULER_CHANGED" ]; then
    ok "无敏感文件被修改"
fi

# ═══════════════════════════════════════════════════════════
# 3. 测试健康
# ═══════════════════════════════════════════════════════════
section "测试结果"

TEST_SUMMARY="跳过 (--skip-tests)"

if [ "$SKIP_TESTS" = false ]; then
    # 检测被改动模块，决定运行哪些测试
    CHANGED_TRADEFLOW=$(git diff --name-only -- 'tradingagents/tradeflow/' 2>/dev/null || true)
    CHANGED_READINESS=$(git diff --name-only -- 'tradingagents/agents/utils/readiness_score.py' 2>/dev/null || true)
    CHANGED_GRAPH=$(git diff --name-only -- 'tradingagents/graph/' 2>/dev/null || true)

    # 确定虚拟环境
    VENV_ACTIVATE=""
    if [ -f ".venv/bin/activate" ]; then
        VENV_ACTIVATE="source .venv/bin/activate &&"
    fi

    TEST_PASSED=0
    TEST_FAILED=0
    TEST_OUTPUT=""

    if [ -n "$CHANGED_TRADEFLOW" ]; then
        log "TradeFlow 模块有改动，运行 tradeflow 测试..."
        set +e
        TEST_OUTPUT=$(${VENV_ACTIVATE} pytest tests/test_tradeflow_*.py -q --tb=line 2>&1) || true
        set -e
        TP=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1 || echo "0")
        TF=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' | tail -1 || echo "0")
        TEST_PASSED=$((TEST_PASSED + TP))
        TEST_FAILED=$((TEST_FAILED + TF))
        log "  tradeflow: ${TP} passed, ${TF} failed"
    fi

    if [ -n "$CHANGED_READINESS" ] || [ -n "$CHANGED_GRAPH" ]; then
        log "执行层有改动，运行 readiness/G001 测试..."
        set +e
        RD_TEST=$(${VENV_ACTIVATE} pytest tests/test_readiness_score.py tests/test_g001_three_layer.py -q --tb=line 2>&1) || true
        set -e
        RP=$(echo "$RD_TEST" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1 || echo "0")
        RF=$(echo "$RD_TEST" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' | tail -1 || echo "0")
        TEST_PASSED=$((TEST_PASSED + RP))
        TEST_FAILED=$((TEST_FAILED + RF))
        log "  readiness/G001: ${RP} passed, ${RF} failed"
    fi

    # 如果有改动但没有命中专项测试，运行全量测试
    if [ "$TEST_PASSED" -eq 0 ] && [ "$TEST_FAILED" -eq 0 ] && { [ -n "$STAGED_DIFF" ] || [ -n "$UNSTAGED_DIFF" ]; }; then
        log "运行全量测试..."
        set +e
        TEST_OUTPUT=$(${VENV_ACTIVATE} pytest tests/ -q --tb=line 2>&1) || true
        set -e
        TP=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1 || echo "0")
        TF=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' | tail -1 || echo "0")
        TEST_PASSED=$((TEST_PASSED + TP))
        TEST_FAILED=$((TEST_FAILED + TF))
        log "  全量: ${TP} passed, ${TF} failed"
    fi

    if [ "$TEST_PASSED" -eq 0 ] && [ "$TEST_FAILED" -eq 0 ]; then
        TEST_SUMMARY="无需运行（无改动）"
        ok "无代码改动，跳过测试"
    elif [ "$TEST_FAILED" -gt 0 ]; then
        TEST_SUMMARY="${TEST_PASSED} passed, ${TEST_FAILED} failed"
        warn "测试未全部通过: ${TEST_SUMMARY}"
    else
        TEST_SUMMARY="${TEST_PASSED} passed, ${TEST_FAILED} failed"
        ok "${TEST_SUMMARY}"
    fi
fi

# ═══════════════════════════════════════════════════════════
# 4. 数据库安全
# ═══════════════════════════════════════════════════════════
section "数据库安全"

# 检查生产 DB 是否存在且被 git 跟踪
if [ -f "tradingagents.db" ]; then
    if git ls-files --error-unmatch tradingagents.db >/dev/null 2>&1; then
        err "tradingagents.db 被 Git 跟踪（应加入 .gitignore）"
    else
        ok "tradingagents.db 未被 Git 跟踪"
    fi
else
    ok "tradingagents.db 不存在（正常）"
fi

# 检查 tradeflow.db
if [ -f "tradeflow.db" ]; then
    if git ls-files --error-unmatch tradeflow.db >/dev/null 2>&1; then
        err "tradeflow.db 被 Git 跟踪"
    else
        ok "tradeflow.db 未被 Git 跟踪"
    fi
fi

# 检查 ta_data.db
if [ -f "ta_data.db" ]; then
    if git ls-files --error-unmatch ta_data.db >/dev/null 2>&1; then
        err "ta_data.db 被 Git 跟踪"
    else
        ok "ta_data.db 未被 Git 跟踪"
    fi
fi

# 检查 graph_checkpoints.db
if [ -f "graph_checkpoints.db" ]; then
    if git ls-files --error-unmatch graph_checkpoints.db >/dev/null 2>&1; then
        err "graph_checkpoints.db 被 Git 跟踪"
    else
        ok "graph_checkpoints.db 未被 Git 跟踪"
    fi
fi

# ═══════════════════════════════════════════════════════════
# 4b. [AUTO-005] db_hygiene_preflight — read-only DB hygiene dry-run
# ═══════════════════════════════════════════════════════════
#
# Wires scripts/run_db_hygiene_check.py (DATA-026) into the preflight so a
# polluted production DB or a broken @test.com filter is surfaced before the
# auto-dev loop claims a task. The check is read-only and never executes the
# suggested cleanup command; cleanup is left to a human (per AUTO-005 spec).
#
# Risk mapping:
#   - has_p0_risk=true  (broken @test.com filter)  -> err (exit=2)
#   - total_pollution>0 (P1 pollution present)     -> warn (exit=1)
#   - all_green=true                                 -> ok
#
# The DB path is resolved by the service via DATABASE_URL (set by conftest in
# tests); preflight does NOT pass --db so it inherits the deployment default.

section "DB Hygiene (AUTO-005)"

DB_HYGIENE_SCRIPT="$REPO_DIR/scripts/run_db_hygiene_check.py"
DB_HYGIENE_STATUS="skipped"

# [AUTO-005] db_hygiene_preflight — activate venv so the service module and its
# deps (sqlalchemy, langchain_core via api.services chain) are importable.
VENV_ACTIVATE=""
if [ -f "$REPO_DIR/.venv/bin/activate" ]; then
    VENV_ACTIVATE="source $REPO_DIR/.venv/bin/activate &&"
elif command -v python3 >/dev/null 2>&1; then
    VENV_ACTIVATE=""
fi

if [ "$SKIP_DB_HYGIENE" = true ]; then
    log "DB hygiene 跳过 (--skip-db-hygiene)"
elif [ ! -f "$DB_HYGIENE_SCRIPT" ]; then
    warn "scripts/run_db_hygiene_check.py 不存在，跳过 DB hygiene"
elif ! command -v python3 >/dev/null 2>&1; then
    warn "python3 不可用，跳过 DB hygiene"
else
    DB_HYGIENE_OUTPUT=""
    set +e
    DB_HYGIENE_OUTPUT=$(bash -lc "${VENV_ACTIVATE} python3 \"$DB_HYGIENE_SCRIPT\" --json" 2>&1)
    DB_HYGIENE_EXIT=$?
    set -e

    # run_db_hygiene_check.py returns 0 when all-green, 1 otherwise. Any
    # other exit code means the check itself crashed (missing dep, import
    # error); treat that as a warning instead of failing the whole preflight.
    if echo "$DB_HYGIENE_OUTPUT" | grep -q '"db_path"'; then
        # Parse JSON fields without jq (keep preflight deps minimal).
        DB_HYGIENE_ALL_GREEN=$(echo "$DB_HYGIENE_OUTPUT" | python3 -c \
            "import json,sys; print(str(json.load(sys.stdin).get('all_green', False)).lower())" \
            2>/dev/null || echo "false")
        DB_HYGIENE_HAS_P0=$(echo "$DB_HYGIENE_OUTPUT" | python3 -c \
            "import json,sys; print(str(json.load(sys.stdin).get('has_p0_risk', False)).lower())" \
            2>/dev/null || echo "false")
        DB_HYGIENE_TOTAL=$(echo "$DB_HYGIENE_OUTPUT" | python3 -c \
            "import json,sys; print(json.load(sys.stdin).get('total_pollution', 0))" \
            2>/dev/null || echo "0")
        DB_HYGIENE_FILTER_OK=$(echo "$DB_HYGIENE_OUTPUT" | python3 -c \
            "import json,sys; print(str(json.load(sys.stdin).get('pending_tasks_filter_ok', True)).lower())" \
            2>/dev/null || echo "true")

        log "all_green: $DB_HYGIENE_ALL_GREEN"
        log "total_pollution: $DB_HYGIENE_TOTAL"
        log "pending_tasks_filter_ok: $DB_HYGIENE_FILTER_OK"

        if [ "$DB_HYGIENE_HAS_P0" = "true" ]; then
            err "DB hygiene [P0]: @test.com 过滤失效或 pending-task 检查异常，scheduler 可能执行测试用户任务"
            log "  cleanup (read-only 命令仅供参考):"
            log "    python scripts/cleanup_test_db_pollution.py --execute"
            log "  详情:"
            echo "$DB_HYGIENE_OUTPUT" | python3 -c \
                "import json,sys; [print(f'    [{r[\"severity\"]}] {r[\"code\"]}: {r[\"message\"]}') for r in json.load(sys.stdin).get('risks',[])]" \
                2>/dev/null | head -10 || true
            DB_HYGIENE_STATUS="p0"
        elif [ "$DB_HYGIENE_TOTAL" != "0" ]; then
            warn "DB hygiene [P1]: 检测到 $DB_HYGIENE_TOTAL 行 @test.com 污染（cleanup 命令仅供参考，preflight 不自动执行）"
            log "  cleanup 命令（dry-run by default）:"
            log "    python scripts/cleanup_test_db_pollution.py"
            log "  备份目录: var/db_backups/"
            DB_HYGIENE_STATUS="p1"
        else
            ok "DB hygiene all green（无 @test.com 污染，pending-task 过滤生效）"
            DB_HYGIENE_STATUS="green"
        fi
    else
        # Check ran but didn't produce JSON — surface the failure as warning.
        warn "DB hygiene 检查未返回 JSON (exit=$DB_HYGIENE_EXIT)"
        $QUIET || echo "$DB_HYGIENE_OUTPUT" | head -10 | while IFS= read -r line; do
            echo "    $line"
        done
        DB_HYGIENE_STATUS="error"
    fi
fi

# ═══════════════════════════════════════════════════════════
# 5. 模型/API 消耗风险
# ═══════════════════════════════════════════════════════════
section "Token/API 风险"

# 检查是否有新增的 LLM 调用脚本或定时任务
# 搜索最近修改的 Python 文件中是否包含 LLM 调用模式
LLM_PATTERNS=("ChatOpenAI\|langchain\|openai\.ChatCompletion\|anthropic\.Anthropic\|zhipuai\|ZhipuAI")

# 检查未提交文件中是否有新增 LLM 调用
NEW_LLM_FILES=""
if [ -n "$DIRTY_FILES" ]; then
    for f in $(git diff --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null); do
        # 只检查 Python 文件
        [[ "$f" != *.py ]] && continue
        if git show ":$f" 2>/dev/null | grep -qE "$LLM_PATTERNS"; then
            NEW_LLM_FILES="$NEW_LLM_FILES $f"
        elif [ -f "$f" ] && grep -qE "$LLM_PATTERNS" "$f" 2>/dev/null; then
            NEW_LLM_FILES="$NEW_LLM_FILES $f"
        fi
    done
fi

if [ -n "$NEW_LLM_FILES" ]; then
    warn "以下文件包含 LLM 调用模式（可能产生 token 消耗）："
    echo "$NEW_LLM_FILES" | tr ' ' '\n' | while IFS= read -r f; do
        [ -n "$f" ] && log "  - $f"
    done
else
    ok "未提交变更中无新增 LLM 调用"
fi

# 检查 scheduler 是否有运行中进程
SCHEDULER_RUNNING=$(pgrep -f "scheduler" 2>/dev/null || true)
if [ -n "$SCHEDULER_RUNNING" ]; then
    warn "检测到 scheduler 进程运行中 (PID: $(echo "$SCHEDULER_RUNNING" | tr '\n' ' '))"
    warn "scheduler 运行中可能产生 LLM API 调用"
else
    ok "无 scheduler 进程运行"
fi

# 检查 OpenClaw cron 是否有新增任务
if command -v crontab >/dev/null 2>&1; then
    OPENCLAW_CRON=$(crontab -l 2>/dev/null | grep -i "openclaw\|auto_dev\|preflight" || true)
    if [ -n "$OPENCLAW_CRON" ]; then
        log "OpenClaw cron 任务:"
        echo "$OPENCLAW_CRON" | while IFS= read -r line; do
            log "  $line"
        done
    fi
fi

# 检查 DeepSeek / 高成本模型引用
HIGH_COST_MODELS="deepseek\|gpt-4\|claude-3"
HIGH_COST_FILES=""
if [ -n "$DIRTY_FILES" ]; then
    for f in $(git diff --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null); do
        [[ "$f" != *.py ]] && [[ "$f" != *.yaml ]] && [[ "$f" != *.json ]] && continue
        CONTENT=$(git show ":$f" 2>/dev/null || cat "$f" 2>/dev/null || echo "")
        if echo "$CONTENT" | grep -qiE "$HIGH_COST_MODELS"; then
            HIGH_COST_FILES="$HIGH_COST_FILES $f"
        fi
    done
fi

if [ -n "$HIGH_COST_FILES" ]; then
    warn "以下文件引用高成本模型："
    echo "$HIGH_COST_FILES" | tr ' ' '\n' | while IFS= read -r f; do
        [ -n "$f" ] && log "  - $f"
    done
fi

# ═══════════════════════════════════════════════════════════
# 6. 运行产物清理
# ═══════════════════════════════════════════════════════════
section "运行产物"

# 检查未跟踪的 .db 文件
UNTRACKED_DB=$(git ls-files --others --exclude-standard -- '*.db' '*.db-shm' '*.db-wal' 2>/dev/null || true)
if [ -n "$UNTRACKED_DB" ]; then
    warn "发现未跟踪的数据库文件："
    echo "$UNTRACKED_DB" | while IFS= read -r f; do
        log "  - $f ($(du -sh "$f" 2>/dev/null | cut -f1 || echo '?'))"
    done
fi

# 检查未跟踪的 .log 文件
UNTRACKED_LOG=$(git ls-files --others --exclude-standard -- '*.log' 2>/dev/null || true)
if [ -n "$UNTRACKED_LOG" ]; then
    log "未跟踪日志文件:"
    echo "$UNTRACKED_LOG" | while IFS= read -r f; do
        log "  - $f"
    done
fi

# 检查 __pycache__ 目录
PYCACHE_DIRS=$(find . -name '__pycache__' -not -path './.venv/*' 2>/dev/null | head -5 || true)
if [ -n "$PYCACHE_DIRS" ]; then
    $QUIET || log "存在 __pycache__ 目录（已被 .gitignore 忽略）"
fi

# 检查根目录临时 Python 文件
TEMP_PY=$(git ls-files --others --exclude-standard -- './*_test*.py' './*_tmp*.py' './*_backup*.py' './*_original*.py' './*_fixed*.py' './patch_*.py' 2>/dev/null | grep -v '^tests/' | grep -v '^tradingagents/' || true)
if [ -n "$TEMP_PY" ]; then
    warn "发现根目录临时 Python 文件（应清理）："
    echo "$TEMP_PY" | while IFS= read -r f; do
        log "  - $f"
    done
fi

# 检查 auto_dev.lock 残留
if [ -d ".auto_dev.lock" ]; then
    warn "发现 .auto_dev.lock 目录（可能为上次异常退出残留）"
    log "  所有者信息: $(cat '.auto_dev.lock/owner' 2>/dev/null || echo '无')"
fi

if [ -z "$UNTRACKED_DB" ] && [ -z "$TEMP_PY" ] && [ ! -d ".auto_dev.lock" ]; then
    ok "无异常运行产物"
fi

# ═══════════════════════════════════════════════════════════
# 7. 文档一致性
# ═══════════════════════════════════════════════════════════
section "文档一致性"

# 检查 DEVLOG.md 最近条目日期是否为今天或昨天
TODAY=$(date '+%Y-%m-%d')
YESTERDAY=$(date -v-1d '+%Y-%m-%d' 2>/dev/null || date -d 'yesterday' '+%Y-%m-%d' 2>/dev/null || echo "")
LATEST_DEVLOG_DATE=$(grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}' docs/DEVLOG.md 2>/dev/null | head -1 || echo "")
if [ -n "$LATEST_DEVLOG_DATE" ]; then
    if [ "$LATEST_DEVLOG_DATE" = "$TODAY" ] || [ "$LATEST_DEVLOG_DATE" = "$YESTERDAY" ]; then
        ok "DEVLOG.md 最近更新: $LATEST_DEVLOG_DATE"
    else
        log "DEVLOG.md 最近更新: $LATEST_DEVLOG_DATE（非今日/昨日）"
    fi
fi

# 检查 AUTO_DEV_PLAN.md 是否存在
if [ -f "docs/AUTO_DEV_PLAN.md" ]; then
    ok "docs/AUTO_DEV_PLAN.md 存在"
else
    log "docs/AUTO_DEV_PLAN.md 不存在（非必须）"
fi

# 检查 TASKS.md 中 in_progress 任务数量
IN_PROGRESS_COUNT=$(grep -cE '\*\*状态\*\*.*in_progress' docs/TASKS.md 2>/dev/null || true)
IN_PROGRESS_COUNT=${IN_PROGRESS_COUNT:-0}
if [ "$IN_PROGRESS_COUNT" -gt 0 ] 2>/dev/null; then
    warn "TASKS.md 中有 ${IN_PROGRESS_COUNT} 个 in_progress 任务"
fi

READY_COUNT=$(grep -cE '\*\*状态\*\*.*ready' docs/TASKS.md 2>/dev/null || true)
READY_COUNT=${READY_COUNT:-0}
log "TASKS.md 中有 ${READY_COUNT} 个 ready 任务"

# ═══════════════════════════════════════════════════════════
# 最终汇总与 exit code
# ═══════════════════════════════════════════════════════════
echo ""
echo "=== 巡检结论 ==="

STATUS_TEXT="干净"
if [ "$RISK_LEVEL" -eq 1 ]; then
    STATUS_TEXT="有变更"
elif [ "$RISK_LEVEL" -ge 2 ]; then
    STATUS_TEXT="有风险"
elif [ "$DIRTY_COUNT" -gt 0 ]; then
    STATUS_TEXT="有变更"
fi

echo "当前状态: $STATUS_TEXT"

if [ ${#RISK_NOTES[@]} -gt 0 ]; then
    echo ""
    echo "风险项 (${#RISK_NOTES[@]}):"
    for note in "${RISK_NOTES[@]}"; do
        echo "  - $note"
    done
fi

echo ""
echo "--- 建议动作 ---"
case $RISK_LEVEL in
    0)
        if [ "$DIRTY_COUNT" -gt 0 ]; then
            echo "  可提交：工作区有变更但无敏感风险"
        else
            echo "  可进入开发：工作区干净，无风险项"
        fi
        ;;
    1)
        echo "  需人工确认：有低风险项需确认后才能继续"
        echo "  建议检查上述 [WARN] 项"
        ;;
    2)
        echo "  暂停等待用户确认：发现严重风险项"
        echo "  必须解决上述 [RISK] 项后才能继续自动开发"
        ;;
esac

echo ""
echo "=== 巡检完成 ==="

exit $RISK_LEVEL
