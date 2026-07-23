#!/usr/bin/env bash
# AUTO-002: Auto dev loop v1.4 timeout governance patch
# Usage: ./scripts/auto_dev_loop.sh [--dry-run]
#
# Constraints:
#   - Batch mode: keep claiming highest-priority ready tasks until none remain,
#     a task fails, or AUTO_DEV_MAX_TASKS is reached
#   - Only one task is active at a time
#   - Exit if working tree is dirty
#   - No push / PR / merge
#   - Max 2 fix rounds per task
#   - No changes to prompts/, no writes to prod tradingagents.db
#   - Precise commit: only tests/ tradingagents/ docs/ scripts/ files
#   - Exclude temp files: *.backup, *_original.py, *_fixed.py, patch_*.py, *.tmp, *.log, __pycache__
#   - Exclude root-level *.py (outside tests/ and tradingagents/)
#   - Never auto-commit on failure
#   - DEVLOG/TASKS finalized before commit; no post-commit file changes
#   - Review output saved to docs/reviews/
#   - Each task run saved to docs/task_runs/<task>-<timestamp>/

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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
OPENCODE_TIMEOUT_SECONDS="${AUTO_DEV_OPENCODE_TIMEOUT_SECONDS:-1800}"
TEST_TIMEOUT_SECONDS="${AUTO_DEV_TEST_TIMEOUT_SECONDS:-900}"
CODEX_REVIEW_TIMEOUT_SECONDS="${AUTO_DEV_CODEX_REVIEW_TIMEOUT_SECONDS:-1200}"
DEFAULT_TEST_CMD="${AUTO_DEV_DEFAULT_TEST_CMD:-pytest tests/test_api_smoke.py tests/test_runtime_tier_contract.py -q --tb=short}"
FULL_TEST_CMD="${AUTO_DEV_FULL_TEST_CMD:-pytest tests/ -q --tb=short}"
AUTO_DEV_FULL_TESTS="${AUTO_DEV_FULL_TESTS:-0}"
AUTO_DEV_MAX_TASKS="${AUTO_DEV_MAX_TASKS:-0}"  # 0 = no explicit cap; cron timeout remains the outer cap.

# Zhipu API Key (for quota check)
ZAI_API_KEY="${ZAI_API_KEY:-}"
if [[ -z "$ZAI_API_KEY" ]]; then
    OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
    if [[ -f "$OPENCLAW_CONFIG" ]]; then
        ZAI_API_KEY=$(python3 -c "
import json
with open('$OPENCLAW_CONFIG') as f:
    cfg = json.load(f)
providers = cfg.get('models', {}).get('providers', {})
for name, prov in providers.items():
    if 'zai' in name.upper():
        print(prov.get('apiKey', ''))
        break
" 2>/dev/null || echo "")
    fi
fi
export ZAI_API_KEY

# --- Argument parsing ---
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        -h|--help) echo "Usage: $0 [--dry-run]"; exit 0 ;;
    esac
done

cd "$REPO_DIR"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[AUTO]${NC} $*"; }
warn() { echo -e "${YELLOW}[AUTO]${NC} $*"; }
err()  { echo -e "${RED}[AUTO]${NC} $*" >&2; }

run_with_timeout() {
    local seconds="$1"
    shift

    if command -v gtimeout &>/dev/null; then
        gtimeout --kill-after=5s "$seconds" "$@"
        return $?
    fi
    if command -v timeout &>/dev/null; then
        timeout --kill-after=5s "$seconds" "$@"
        return $?
    fi

    python3 -c '
import os
import signal
import subprocess
import sys

seconds = float(sys.argv[1])
cmd = sys.argv[2:]
try:
    proc = subprocess.Popen(cmd, start_new_session=True)
except FileNotFoundError:
    sys.exit(127)

try:
    sys.exit(proc.wait(timeout=seconds))
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    sys.exit(124)
' "$seconds" "$@"
}

# Resolve the current TypeScript OpenCode CLI instead of relying on cron's
# PATH order. An older Go binary (0.0.x) may also be installed as `opencode`;
# it has no `run` subcommand and fails with a misleading "agent coder" error.
resolve_opencode_bin() {
    local configured="${AUTO_DEV_OPENCODE_BIN:-}"
    local path_candidate=""
    local candidate=""
    local seen="|"
    local candidates=()

    if [ -n "$configured" ]; then
        candidates+=("$configured")
    fi
    if path_candidate=$(command -v opencode 2>/dev/null); then
        candidates+=("$path_candidate")
    fi
    candidates+=(
        "/opt/homebrew/bin/opencode"
        "$HOME/.local/bin/opencode"
        "/usr/local/bin/opencode"
    )

    for candidate in "${candidates[@]}"; do
        [ -n "$candidate" ] || continue
        case "$seen" in
            *"|$candidate|"*) continue ;;
        esac
        seen="${seen}${candidate}|"
        [ -x "$candidate" ] || continue
        if "$candidate" --help 2>&1 | grep -Fq "opencode run"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

redact_log() {
    # Redact API keys before persisting logs.
    sed -E \
        -e 's/sk-[A-Za-z0-9_-]{20,}/[REDACTED_API_KEY]/g' \
        -e 's/(api[_-]?key[=:][[:space:]]*)[^[:space:]]+/\1[REDACTED]/Ig' \
        -e 's/(authorization:[[:space:]]*bearer[[:space:]]+)[^[:space:]]+/\1[REDACTED]/Ig'
}

# [AUTO-006] codex_review_watchdog
# Persist a review-meta-roundN.json next to the codex-review-roundN.txt archive
# so summarize_auto_dev_runs.py can surface review elapsed_ms, timed_out and
# partial_output in the nightly report. Always emits valid JSON; never raises.
write_review_meta() {
    local out_file="$1"      # path to review-meta-roundN.json
    local round="$2"         # round number
    local started_epoch="$3" # codex review start epoch (seconds)
    local finished_epoch="$4"
    local timeout_seconds="$5"
    local exit_code="$6"
    local timed_out="$7"        # "true" / "false" (bash literal)
    local review_skipped="$8"   # "true" / "false" (bash literal)
    local review_file_path="$9" # path to the captured review output
    local status="${10:-UNKNOWN}"   # PASS / FAIL / TIMEOUT / SKIPPED
    local task_id="${TASK_ID:-unknown}"

    local partial_bytes=0
    local has_partial_output=false
    if [ -f "$review_file_path" ]; then
        partial_bytes=$(wc -c < "$review_file_path" 2>/dev/null | tr -d ' ' || echo 0)
        if [ "${partial_bytes:-0}" -gt 0 ]; then
            has_partial_output=true
        fi
    fi

    local elapsed_ms=0
    if [ -n "$started_epoch" ] && [ -n "$finished_epoch" ]; then
        elapsed_ms=$(( (finished_epoch - started_epoch) * 1000 ))
    fi

    # Translate bash true/false literals to Python True/False so the inline
    # heredoc below produces valid JSON. Defaults to False on unexpected
    # input to keep the JSON writable even when callers pass garbage.
    local py_timed_out py_review_skipped py_has_partial
    if [ "$timed_out" = "true" ]; then py_timed_out="True"; else py_timed_out="False"; fi
    if [ "$review_skipped" = "true" ]; then py_review_skipped="True"; else py_review_skipped="False"; fi
    if [ "$has_partial_output" = "true" ]; then py_has_partial="True"; else py_has_partial="False"; fi

    python3 - "$out_file" <<PYEOF || warn "[AUTO-006] failed to write review meta: $out_file"
import json, os, sys
path = sys.argv[1]
data = {
    "task_id": "$task_id",
    "round": int("$round") if "$round".isdigit() else 0,
    "started_at_epoch": int("$started_epoch") if "$started_epoch".lstrip("-").isdigit() else 0,
    "finished_at_epoch": int("$finished_epoch") if "$finished_epoch".lstrip("-").isdigit() else 0,
    "elapsed_ms": int("$elapsed_ms") if "$elapsed_ms".lstrip("-").isdigit() else 0,
    "timeout_seconds": int("$timeout_seconds") if "$timeout_seconds".lstrip("-").isdigit() else 0,
    "exit_code": int("$exit_code") if "$exit_code".lstrip("-").isdigit() else -1,
    "timed_out": $py_timed_out,
    "review_skipped": $py_review_skipped,
    "has_partial_output": $py_has_partial,
    "partial_output_bytes": int("$partial_bytes") if "$partial_bytes".lstrip("-").isdigit() else 0,
    "status": "$status",
}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PYEOF
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
            echo "task_id=${TASK_ID:-unknown}"
        } > "$LOCK_DIR/owner"
        return 0
    fi

    err "Auto dev lock detected, exiting to avoid duplicate task claim: $LOCK_DIR"
    [ -f "$LOCK_DIR/owner" ] && cat "$LOCK_DIR/owner" >&2
    exit 1
}

record_lock_task() {
    if [ "$HAVE_LOCK" != true ] || [ ! -f "$LOCK_DIR/owner" ]; then
        return 0
    fi
    local tmp_owner="$LOCK_DIR/owner.tmp"
    awk -v task_id="${TASK_ID:-unknown}" '
        BEGIN { seen = 0 }
        /^task_id=/ { print "task_id=" task_id; seen = 1; next }
        { print }
        END { if (!seen) print "task_id=" task_id }
    ' "$LOCK_DIR/owner" > "$tmp_owner" && mv "$tmp_owner" "$LOCK_DIR/owner"
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
    print(f"TASKS.md: task {target_id} not found")
    sys.exit(1)

section = match.group(1)
new_section, n = re.subn(
    r"(- \*\*(status|状态)\*\*[：:,]+\s*).+",
    r"\1" + status_text,
    section,
    count=1,
)
if n == 0:
    print(f"TASKS.md: status line for {target_id} not found")
    sys.exit(1)

new_content = content[: match.start(1)] + new_section + content[match.end(1) :]
with open(tasks_file, "w", encoding="utf-8") as f:
    f.write(new_content)
print(f"TASKS.md: {target_id} -> {status_text}")
PYEOF
}

# --- Stale lock recovery ---
recover_stale_lock() {
    # [INF-001] task_claim_lock — stale lock recovery (conservative)
    local owner_file="$LOCK_DIR/owner"
    if [ ! -f "$owner_file" ]; then
        err "Lock exists but no owner file, exiting"
        exit 1
    fi

    # Parse owner file
    local lock_pid lock_started_at lock_task_id
    lock_pid=$(grep '^pid=' "$owner_file" | cut -d= -f2)
    lock_started_at=$(grep '^started_at=' "$owner_file" | cut -d= -f2)
    lock_task_id=$(grep '^task_id=' "$owner_file" | cut -d= -f2)

    # Check if pid is still alive
    if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
        err "Lock held by live process PID=$lock_pid, exiting"
        cat "$owner_file" >&2
        exit 1
    fi
    log "Lock PID=$lock_pid is not alive"

    # Check lock age (must be > 30 minutes)
    if [ -n "$lock_started_at" ]; then
        local lock_epoch now_epoch age_seconds
        lock_epoch=$(date -j -f '%Y-%m-%d_%H:%M:%S' "$lock_started_at" '+%s' 2>/dev/null || echo 0)
        now_epoch=$(date '+%s')
        age_seconds=$((now_epoch - lock_epoch))
        if [ "$age_seconds" -lt 1800 ]; then
            err "Lock is only ${age_seconds}s old (< 30min), exiting to be safe"
            cat "$owner_file" >&2
            exit 1
        fi
        log "Lock age: ${age_seconds}s (> 30min threshold)"
    fi

    # Stale lock confirmed. Check working tree.
    local dirty_files
    dirty_files=$(git status --porcelain 2>/dev/null | grep -v '^?? .auto_dev.lock' || true)
    if [ -n "$dirty_files" ]; then
        err "NEEDS_HUMAN: Stale lock detected but working tree is dirty — cannot auto-recover"
        err "Dirty files:"
        echo "$dirty_files" >&2
        err "Please review the dirty files, then either:"
        err "  1. Commit/restore them manually, then re-run"
        err "  2. Or delete .auto_dev.lock manually if safe"
        # Write stale lock report
        mkdir -p "$REVIEW_DIR"
        local report_file="$REVIEW_DIR/stale-lock-$(date +%Y-%m-%d).md"
        {
            echo "# Stale Lock Recovery — NEEDS_HUMAN"
            echo ""
            echo "- **Time**: $(date '+%Y-%m-%d %H:%M:%S')"
            echo "- **Lock PID**: $lock_pid (dead)"
            echo "- **Lock Task**: ${lock_task_id:-unknown}"
            echo "- **Lock Age**: ${age_seconds:-?}s"
            echo "- **Reason**: Working tree dirty, cannot auto-recover"
            echo ""
            echo "## Dirty Files"
            echo '```'
            echo "$dirty_files"
            echo '```'
        } >> "$report_file"
        exit 1
    fi

    # Working tree clean — safe to recover
    log "Stale lock recovery: PID=$lock_pid dead, lock age ${age_seconds:-?}s, tree clean"
    rm -rf "$LOCK_DIR"
    log "Deleted stale lock: $LOCK_DIR"

    # Restore the specific task to ready (only the one in owner file)
    if [ -n "$lock_task_id" ]; then
        python3 - "$TASKS_FILE" "$lock_task_id" <<'PYEOF'
import re, sys
tasks_file, target_id = sys.argv[1], sys.argv[2]
with open(tasks_file, "r", encoding="utf-8") as f:
    content = f.read()
pattern = re.compile(
    r"(###\s+" + re.escape(target_id) + r":.*?)(?=\n###|\n---|\Z)",
    re.DOTALL,
)
m = pattern.search(content)
if m:
    section = m.group(1)
    new_section, n = re.subn(
        r"(- \*\*(status|状态)\*\*[：:,]+\s*).+",
        r"\1ready",
        section,
        count=1,
    )
    if n > 0:
        content = content[:m.start()] + new_section + content[m.end():]
        with open(tasks_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"TASKS.md: {target_id} restored to ready")
    else:
        print(f"TASKS.md: {target_id} — no status line found to update")
else:
    print(f"TASKS.md: {target_id} not found")
PYEOF
        log "Task $lock_task_id restored to ready"
    fi

    # Write stale lock report
    mkdir -p "$REVIEW_DIR"
    local report_file="$REVIEW_DIR/stale-lock-$(date +%Y-%m-%d).md"
    {
        echo "# Stale Lock Recovery — Auto Recovered"
        echo ""
        echo "- **Time**: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "- **Lock PID**: $lock_pid (dead)"
        echo "- **Lock Task**: ${lock_task_id:-unknown}"
        echo "- **Lock Age**: ${age_seconds:-?}s"
        echo "- **Action**: Deleted lock, restored ${lock_task_id:-unknown} to ready"
        echo "- **Tree Status**: Clean"
    } >> "$report_file"
}

# --- 0. Pre-checks ---
log "=== AUTO-002 Auto Dev Loop v1.4 ==="
log "Repo: $REPO_DIR"
log "Timeout budget: OpenCode=${OPENCODE_TIMEOUT_SECONDS}s, tests=${TEST_TIMEOUT_SECONDS}s, Codex review=${CODEX_REVIEW_TIMEOUT_SECONDS}s"
if [ "$AUTO_DEV_FULL_TESTS" = "1" ]; then
    log "Default tests: full suite (${FULL_TEST_CMD})"
else
    log "Default tests: smoke suite (${DEFAULT_TEST_CMD})"
fi

mkdir -p "$REVIEW_DIR" "$TASK_RUN_ROOT"

RECOVERED_STALE_LOCK=false

if [ -d "$LOCK_DIR" ]; then
    log "Lock detected, attempting stale lock recovery..."
    recover_stale_lock
    RECOVERED_STALE_LOCK=true
fi

# 0a. Preflight check (T-000)
if [ -x "${SCRIPT_DIR}/preflight_check.sh" ]; then
    log "Running preflight check..."
    set +e
    "${SCRIPT_DIR}/preflight_check.sh" --skip-tests --quiet
    PREFLIGHT_EXIT=$?
    set -e
    if [ "${PREFLIGHT_EXIT}" -eq 2 ]; then
        err "Preflight found severe risks (exit=2), stopping"
        exit 1
    fi
    if [ "${PREFLIGHT_EXIT}" -eq 1 ]; then
        warn "Preflight found warnings (exit=1), continuing with caution"
    fi
    log "Preflight completed (exit=${PREFLIGHT_EXIT})"
fi

DIRTY=$(git status --porcelain | grep -v '^?? .auto_dev.lock' | head -5 || true)
if [ -n "$DIRTY" ] && [ "$RECOVERED_STALE_LOCK" = false ]; then
    err "Working tree dirty, exiting to avoid overwriting user changes:"
    echo "$DIRTY"
    exit 1
fi
if [ "$RECOVERED_STALE_LOCK" = true ] && [ -n "$DIRTY" ]; then
    log "Tree has changes from stale lock recovery (TASKS.md + review), continuing"
fi
log "Working tree clean"

acquire_lock

# --- 1. Parse TASKS.md for highest-priority ready task ---
# [AUTO-007] dependency_aware_claim
# Delegate to scripts/task_dependency_resolver.py so the picker honours
# machine-readable depends_on / auto_release metadata. Output format stays
# ID|title|priority|test_cmds (or NONE|||) for backward compatibility. A
# non-zero exit (missing dep on a gating task, cycle touching a gating task)
# aborts the batch under `set -e` and the reason is on stderr.
DEP_RESOLVER="$SCRIPT_DIR/task_dependency_resolver.py"

parse_ready_tasks_legacy() {
    # Fallback used only when task_dependency_resolver.py is unavailable
    # (e.g. partial vendoring / older test fixtures). Preserves the original
    # AUTO-002 picker semantics that ignored depends_on metadata.
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

EXCLUDE_KEYWORDS = {"preflight", "checklist", "inspection", "baseline"}
EXCLUDE_ID_PREFIXES = ("R-",)
EXCLUDE_IDS = {"T-000"}

tasks = []
for m in pattern.finditer(content):
    task_id = m.group(1)
    title = m.group(2).strip()
    body = m.group(3)

    if not re.search(r"\*\*(status|状态)\*\*.*?ready", body, re.IGNORECASE):
        continue

    if "done" in title.lower() or "\u2713" in title:
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

parse_ready_tasks() {
    if [ -f "$DEP_RESOLVER" ]; then
        python3 "$DEP_RESOLVER" claim --tasks-file "$TASKS_FILE"
    else
        # NOTE: warn must go to stderr so it does not contaminate the
        # captured stdout (which carries the ID|title|... payload).
        warn "[AUTO-007] task_dependency_resolver.py missing — falling back to legacy picker" >&2
        parse_ready_tasks_legacy
    fi
}

# --- Main loop: execute until no ready tasks remain ---
COMPLETED_TASKS=0
FAILED_TASKS=0

# Quota check function (Zhipu API)
check_zai_quota() {
    if [[ -x "${SCRIPT_DIR}/check_zai_quota.sh" ]]; then
        local result
        result=$(ZAI_API_KEY="${ZAI_API_KEY:-}" bash "${SCRIPT_DIR}/check_zai_quota.sh" --json 2>/dev/null || echo '{"status":"ERROR"}')
        local status
        status=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','ERROR'))" 2>/dev/null || echo "ERROR")
        # Save quota state to file (for other scripts)
        echo "$result" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    data['last_check'] = '$(date -Iseconds)'
    with open('${REPO_DIR}/.zai_quota_state.json', 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
except: pass
" 2>/dev/null || true
        if [[ "$status" == "EXHAUSTED" ]]; then
            local reset_time msg
            reset_time=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reset_time',''))" 2>/dev/null || echo "")
            msg=$(echo "$result" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || echo "")
            err "[QUOTA] Zhipu API quota exhausted!"
            [[ -n "$reset_time" ]] && err "[QUOTA] Next reset: $reset_time"
            [[ -n "$msg" ]] && err "[QUOTA] Details: $msg"
            return 1
        fi
    fi
    return 0
}

check_local_knowledge_access() {
    # [KB-001] local_knowledge_audit — fail fast before OpenCode if the local
    # Tree Work knowledge base cannot be read by this project.
    local run_dir="$1"
    local knowledge_root="${AUTO_DEV_KNOWLEDGE_ROOT:-$HOME/Documents/knowledge}"
    local investment_dir="$knowledge_root/wiki/investment"
    local preflight_log="$run_dir/knowledge-preflight.txt"

    {
        echo "# Local Knowledge Preflight"
        echo ""
        echo "- Time: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "- knowledge_root: $knowledge_root"
        echo "- investment_dir: $investment_dir"
        echo ""
    } > "$preflight_log"

    if [ ! -d "$knowledge_root" ]; then
        echo "FAIL: knowledge root does not exist" >> "$preflight_log"
        return 1
    fi
    if [ ! -r "$knowledge_root" ] || [ ! -x "$knowledge_root" ]; then
        echo "FAIL: knowledge root is not readable/searchable" >> "$preflight_log"
        return 1
    fi
    if [ ! -d "$investment_dir" ]; then
        echo "FAIL: investment wiki directory does not exist" >> "$preflight_log"
        return 1
    fi
    if [ ! -r "$investment_dir" ] || [ ! -x "$investment_dir" ]; then
        echo "FAIL: investment wiki directory is not readable/searchable" >> "$preflight_log"
        return 1
    fi

    local page_count
    page_count=$(find "$investment_dir" -maxdepth 1 -type f -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
    echo "- investment_md_pages: $page_count" >> "$preflight_log"
    if [ "${page_count:-0}" -eq 0 ]; then
        echo "FAIL: no investment markdown pages found" >> "$preflight_log"
        return 1
    fi

    local permission_state
    permission_state=$(
        "$OPENCODE_BIN" debug config 2>/dev/null | python3 -c '
import json, sys
try:
    cfg = json.load(sys.stdin)
except Exception:
    print("unknown")
    raise SystemExit
perm = cfg.get("permission", {})
if isinstance(perm, str):
    print(perm)
else:
    val = perm.get("external_directory", "ask")
    if isinstance(val, dict):
        print(val.get("*", "ask"))
    else:
        print(val)
' 2>/dev/null || echo "unknown"
    )
    echo "- opencode_external_directory_permission: $permission_state" >> "$preflight_log"
    if [ "$permission_state" != "allow" ]; then
        {
            echo "FAIL: OpenCode external_directory permission is not allow"
            echo "Hint: create .opencode/opencode.json with:"
            echo '{ "permission": { "external_directory": "allow" } }'
        } >> "$preflight_log"
        return 1
    fi

    echo "PASS: local knowledge is readable and OpenCode external_directory is allowed" >> "$preflight_log"
    return 0
}

while true; do

TASK_LINE=$(parse_ready_tasks)
TASK_ID=$(echo "$TASK_LINE" | cut -d'|' -f1)
TASK_TITLE=$(echo "$TASK_LINE" | cut -d'|' -f2)
TASK_PRIO=$(echo "$TASK_LINE" | cut -d'|' -f3)
TASK_TESTS=$(echo "$TASK_LINE" | cut -d'|' -f4)

if [ "$TASK_ID" = "NONE" ]; then
    log "No status=ready tasks found."
    # [M-012] task_pool_suggestion: generate proposed task suggestions
    SUGGEST_SCRIPT="$SCRIPT_DIR/suggest_next_tasks.py"
    if [ -f "$SUGGEST_SCRIPT" ]; then
        log "Generating task suggestions..."
        SUGGEST_ARGS=""
        if [ "$DRY_RUN" = true ]; then
            SUGGEST_ARGS="--dry-run"
        fi
        set +e
        python3 "$SUGGEST_SCRIPT" $SUGGEST_ARGS 2>&1 | tail -5
        set -e
        SUGGEST_DIR="$REPO_DIR/docs/task_suggestions"
        SUGGEST_FILE="$SUGGEST_DIR/$(date +%Y-%m-%d).md"
        if [ -f "$SUGGEST_FILE" ]; then
            log "Suggestions saved: $SUGGEST_FILE"
            log "Review and promote proposed tasks to ready in docs/TASKS.md"
        fi
    fi
    break
fi

log "Selected task: [$TASK_PRIO] $TASK_ID: $TASK_TITLE"
record_lock_task
[ -n "$TASK_TESTS" ] && log "Test commands: $TASK_TESTS"

# --- dry-run mode: print task info only ---
if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "========================================"
    echo "  AUTO-002 DRY-RUN"
    echo "========================================"
    echo "  Task ID:    $TASK_ID"
    echo "  Task title: $TASK_TITLE"
    echo "  Priority:   $TASK_PRIO"
    echo "  Test cmd:   ${TASK_TESTS:-$DEFAULT_TEST_CMD}"
    echo "  OpenCode timeout: ${OPENCODE_TIMEOUT_SECONDS}s"
    echo "  Test timeout:     ${TEST_TIMEOUT_SECONDS}s"
    echo "  Codex timeout:    ${CODEX_REVIEW_TIMEOUT_SECONDS}s"
    echo "  Full tests:       ${AUTO_DEV_FULL_TESTS}"
    echo "  Max tasks:        ${AUTO_DEV_MAX_TASKS} (0 means until no ready tasks)"
    echo "========================================"
    # [AUTO-007] dependency_aware_claim — surface dependency state so the
    # human can see blocked reasons, releasable tasks and ordering without
    # modifying TASKS.md.
    DEP_RESOLVER="$SCRIPT_DIR/task_dependency_resolver.py"
    if [ -f "$DEP_RESOLVER" ]; then
        echo ""
        echo "--- [AUTO-007] dependency report (read-only) ---"
        set +e
        python3 "$DEP_RESOLVER" dry-run --tasks-file "$TASKS_FILE" 2>&1 | tail -60
        set -e
    fi
    break
fi

# [AUTO-OPENCODE-BIN] Pin a CLI that supports the non-interactive `run`
# command. This prevents isolated cron sessions from selecting the legacy
# /usr/local/bin/opencode 0.0.x binary ahead of the current Homebrew CLI.
if ! OPENCODE_BIN=$(resolve_opencode_bin); then
    err "No compatible OpenCode CLI found (required command: opencode run)."
    err "Set AUTO_DEV_OPENCODE_BIN to the absolute path of OpenCode 1.x."
    exit 127
fi
OPENCODE_VERSION=$("$OPENCODE_BIN" --version 2>/dev/null | tail -1 | tr -d '\r')
log "OpenCode CLI: $OPENCODE_BIN (version=${OPENCODE_VERSION:-unknown})"

# --- 2a. Create task run archive ---
RUN_ID="${TASK_ID}-$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$TASK_RUN_ROOT/$RUN_ID"
mkdir -p "$RUN_DIR"

cat > "$RUN_DIR/task.md" <<TASK_META_EOF
# Auto Dev Task Run

- Task: $TASK_ID - $TASK_TITLE
- Priority: $TASK_PRIO
- Status: CLAIMED
- Started at: $(date +%Y-%m-%d_%H:%M:%S)
- Git HEAD: $(git rev-parse --short HEAD)
- Test commands: ${TASK_TESTS:-$DEFAULT_TEST_CMD}
- OpenCode timeout seconds: $OPENCODE_TIMEOUT_SECONDS
- Test timeout seconds: $TEST_TIMEOUT_SECONDS
- Codex review timeout seconds: $CODEX_REVIEW_TIMEOUT_SECONDS
- OpenCode binary: $OPENCODE_BIN
- OpenCode version: ${OPENCODE_VERSION:-unknown}
- Full tests enabled: $AUTO_DEV_FULL_TESTS
- Runner: scripts/auto_dev_loop.sh

## Trace Files

- OpenCode prompts: prompt-round*.md
- OpenCode logs: opencode-round*.txt
- Test logs: tests-round*.txt
- Codex reviews: codex-review-round*.txt
- Final summary: summary.md
TASK_META_EOF
log "Task run archive: $RUN_DIR"

if [[ "$TASK_ID" == KB-* ]]; then
    log "[KB] Running local knowledge preflight..."
    if ! check_local_knowledge_access "$RUN_DIR"; then
        err "[KB] Local knowledge preflight failed; not starting OpenCode"
        update_task_status "blocked — NEEDS_HUMAN, local knowledge preflight failed, see docs/task_runs/$RUN_ID"
        cat > "$RUN_DIR/summary.md" <<SUMMARY_EOF
# Auto Dev Summary

- Task: $TASK_ID - $TASK_TITLE
- Priority: $TASK_PRIO
- Final status: NEEDS_HUMAN
- Reason: local knowledge preflight failed
- Run directory: docs/task_runs/$RUN_ID
- Details: docs/task_runs/$RUN_ID/knowledge-preflight.txt
SUMMARY_EOF
        cat >> "$DEVLOG_FILE" <<DEVLOG_EOF

## $(date +%Y-%m-%d) | AUTO-002 Local Knowledge Preflight

- **Task**: $TASK_ID - $TASK_TITLE
- **Status**: FAIL NEEDS_HUMAN
- **Reason**: local knowledge preflight failed before OpenCode
- **Run archive**: docs/task_runs/$RUN_ID/
DEVLOG_EOF
        FAILED_TASKS=$((FAILED_TASKS + 1))
        break
    fi
    log "[KB] Local knowledge preflight passed"
fi

update_task_status "in_progress — claimed $RUN_ID"

    # --- 2a. [M-013] CodeGraph preflight: generate context before development ---
    CG_PREFLIGHT_SCRIPT="$SCRIPT_DIR/codegraph_preflight.py"
    if [ -f "$CG_PREFLIGHT_SCRIPT" ]; then
        log "[M-013] Running CodeGraph preflight (pre)..."
        set +e
        python3 "$CG_PREFLIGHT_SCRIPT" pre \
            --run-dir "$RUN_DIR" \
            --task-id "$TASK_ID" \
            --task-title "$TASK_TITLE" \
            --repo-dir "$REPO_DIR" 2>&1 | tail -5
        CG_PRE_EXIT=$?
        set -e
        if [ $CG_PRE_EXIT -eq 0 ]; then
            log "[M-013] CodeGraph preflight done"
        else
            warn "[M-013] CodeGraph preflight failed (exit=$CG_PRE_EXIT), continuing"
        fi
    fi

# --- 2. Build OpenCode prompt ---
PROMPT_FILE=$(mktemp -t auto-dev-prompt.XXXXXX)
cat > "$PROMPT_FILE" <<PROMPT_EOF
# Task: $TASK_ID: $TASK_TITLE

Read task "$TASK_ID" from docs/TASKS.md and implement as described.

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No full-market scan or stock deep TA
- No push / PR / merge
- **Do NOT git commit** -- outer script handles commits
- Update docs/DEVLOG.md after changes

## Output when done
- Which files were changed
- Key logic summary
- Test results (passed/failed count)
PROMPT_EOF
log "OpenCode prompt: $PROMPT_FILE"

# --- 3. Loop: implement + test + review (max MAX_FIX_ROUNDS rounds) ---
RESULT_STATUS=""
REVIEW_OUTPUT=""
ROUND=0
LAST_FAILURE_REASON=""
ISSUES_LOG=()

while [ $ROUND -lt $MAX_FIX_ROUNDS ]; do
    ROUND=$((ROUND + 1))
    log "--- Round $ROUND ---"
    cp "$PROMPT_FILE" "$RUN_DIR/prompt-round${ROUND}.md"

    # 3a. Run OpenCode
    log "Starting OpenCode (timeout=${OPENCODE_TIMEOUT_SECONDS}s)..."
    OPENCODE_LOG=$(mktemp -t auto-dev-opencode.XXXXXX)
    
    # Quota pre-check: pause if exhausted
    if ! check_zai_quota; then
        err "[QUOTA] Quota exhausted, waiting 10 min before retry..."
        sleep 600
        if ! check_zai_quota; then
            err "[QUOTA] Quota still unavailable after 10 min, aborting task"
            LAST_FAILURE_REASON="Zhipu API quota exhausted"
            ISSUES_LOG+=("[Round $ROUND] Zhipu API quota exhausted, cannot continue")
            RESULT_STATUS="QUOTA_EXHAUSTED"
            break
        fi
    fi
    
    set +e
    run_with_timeout "$OPENCODE_TIMEOUT_SECONDS" "$OPENCODE_BIN" run < "$PROMPT_FILE" > "$OPENCODE_LOG" 2>&1
    OPENCODE_EXIT=$?
    set -e
    log "OpenCode exit=${OPENCODE_EXIT}"
    redact_log < "$OPENCODE_LOG" > "$RUN_DIR/opencode-round${ROUND}.txt"

    if [ $OPENCODE_EXIT -ne 0 ]; then
        if [ $OPENCODE_EXIT -eq 124 ]; then
            LAST_FAILURE_REASON="OpenCode timed out after ${OPENCODE_TIMEOUT_SECONDS}s"
            ISSUES_LOG+=("[Round $ROUND] OpenCode timeout after ${OPENCODE_TIMEOUT_SECONDS}s")
            err "OpenCode timed out after ${OPENCODE_TIMEOUT_SECONDS}s"
            RESULT_STATUS="NEEDS_HUMAN"
            break
        fi
        # Check if failure is due to quota exhaustion
        if grep -qi "429\|rate.limit\|quota\|exhausted\|too many requests" "$OPENCODE_LOG" 2>/dev/null; then
            err "[QUOTA] OpenCode failed: Zhipu API quota exhausted"
            check_zai_quota  # show details
            LAST_FAILURE_REASON="Zhipu API quota exhausted (429)"
            ISSUES_LOG+=("[Round $ROUND] Zhipu API quota exhausted")
            RESULT_STATUS="QUOTA_EXHAUSTED"
            break
        fi
        LAST_FAILURE_REASON="OpenCode failed with exit ${OPENCODE_EXIT}"
        ISSUES_LOG+=("[Round $ROUND] OpenCode exit $OPENCODE_EXIT: $(tail -5 "$OPENCODE_LOG" | tr '\n' ' ')")
        err "OpenCode failed (exit=${OPENCODE_EXIT})"
        err "Log: $OPENCODE_LOG"
        cat > "$PROMPT_FILE" <<FIX_EOF
# Fix task: $TASK_ID

Previous OpenCode run failed (exit code ${OPENCODE_EXIT}). Please fix:

## OpenCode log (last 30 lines)
\`\`\`
$(tail -30 "$OPENCODE_LOG")
\`\`\`

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
FIX_EOF
        continue
    fi

    # 3b. Run tests
    TEST_PASS=true
    TEST_OUTPUT=""
    TEST_LOG_FILE="$RUN_DIR/tests-round${ROUND}.txt"
    : > "$TEST_LOG_FILE"
    if [ -n "$TASK_TESTS" ]; then
        IFS=',' read -ra TEST_CMD_ARRAY <<< "$TASK_TESTS"
        for test_cmd in "${TEST_CMD_ARRAY[@]}"; do
            test_cmd=$(echo "$test_cmd" | xargs)
            log "Running test: $test_cmd"
            {
                echo "## $test_cmd"
                echo
            } >> "$TEST_LOG_FILE"
            set +e
            TEST_OUTPUT=$(source .venv/bin/activate && run_with_timeout "$TEST_TIMEOUT_SECONDS" bash -lc "$test_cmd" 2>&1)
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
                if [ $TEST_EXIT -eq 124 ]; then
                    LAST_FAILURE_REASON="Test timed out after ${TEST_TIMEOUT_SECONDS}s: ${test_cmd}"
                    FAILED_SUMMARY="timeout after ${TEST_TIMEOUT_SECONDS}s"
                else
                    LAST_FAILURE_REASON="Test failed: ${test_cmd} (exit ${TEST_EXIT})"
                    FAILED_SUMMARY=$(echo "$TEST_OUTPUT" | grep -E "FAILED|ERROR|AssertionError" | head -5 | tr '\n' ' ')
                fi
                ISSUES_LOG+=("[Round $ROUND] Test failed ($test_cmd): $FAILED_SUMMARY")
                err "Test failed: $test_cmd (exit=$TEST_EXIT)"
                break
            fi
        done
    else
        if [ "$AUTO_DEV_FULL_TESTS" = "1" ]; then
            EFFECTIVE_DEFAULT_TEST_CMD="$FULL_TEST_CMD"
            log "No tests specified, running full default tests..."
        else
            EFFECTIVE_DEFAULT_TEST_CMD="$DEFAULT_TEST_CMD"
            log "No tests specified, running smoke default tests..."
        fi
        set +e
        TEST_OUTPUT=$(source .venv/bin/activate && run_with_timeout "$TEST_TIMEOUT_SECONDS" bash -lc "$EFFECTIVE_DEFAULT_TEST_CMD" 2>&1)
        TEST_EXIT=$?
        set -e
        {
            echo "## $EFFECTIVE_DEFAULT_TEST_CMD"
            echo
            echo "$TEST_OUTPUT" | redact_log
            echo
            echo "Exit code: $TEST_EXIT"
        } >> "$TEST_LOG_FILE"
        if [ $TEST_EXIT -ne 0 ]; then
            TEST_PASS=false
            if [ $TEST_EXIT -eq 124 ]; then
                LAST_FAILURE_REASON="Default tests timed out after ${TEST_TIMEOUT_SECONDS}s"
                FAILED_SUMMARY="timeout after ${TEST_TIMEOUT_SECONDS}s"
            else
                LAST_FAILURE_REASON="Default tests failed with exit ${TEST_EXIT}"
                FAILED_SUMMARY=$(echo "$TEST_OUTPUT" | grep -E "FAILED|ERROR" | head -5 | tr '\n' ' ')
            fi
            ISSUES_LOG+=("[Round $ROUND] Default pytest failed: $FAILED_SUMMARY")
        fi
    fi

    if [ "$TEST_PASS" = false ]; then
        warn "Tests failed, preparing fix..."
        cat > "$PROMPT_FILE" <<FIX_EOF
# Fix task: $TASK_ID

Previous implementation failed tests. Please fix:

## Test output (last 30 lines)
\`\`\`
$(echo "$TEST_OUTPUT" | tail -30)
\`\`\`

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
FIX_EOF
        continue
    fi

    log "Tests passed"

    # --- 3b2. [M-013] CodeGraph post-task: generate impact from changed files ---
    if [ -f "$CG_PREFLIGHT_SCRIPT" ]; then
        CHANGED_FILES_LIST=$(git diff --name-only 2>/dev/null | tr '\n' ',' | sed 's/,$//')
        if [ -n "$CHANGED_FILES_LIST" ]; then
            log "[M-013] Running CodeGraph impact (post)..."
            set +e
            python3 "$CG_PREFLIGHT_SCRIPT" post \
                --run-dir "$RUN_DIR" \
                --changed-files "$CHANGED_FILES_LIST" \
                --repo-dir "$REPO_DIR" 2>&1 | tail -5
            CG_POST_EXIT=$?
            set -e
            if [ $CG_POST_EXIT -eq 0 ]; then
                log "[M-013] CodeGraph impact done"
            else
                warn "[M-013] CodeGraph impact failed (exit=$CG_POST_EXIT), continuing"
            fi
        fi
    fi

    # 3c. Run Codex review (MANDATORY — never skip even after manual recovery)
    # [CRITICAL] Review must run before ANY commit. If recovering from test failure,
    # always run `codex review --uncommitted` before `git add/commit`.
    CODEX_AVAILABLE=true
    REVIEW_SKIPPED=false
    CODEX_EXIT=127  # default: not run (codex unavailable)
    set +e
    run_with_timeout 30 codex review --help > /dev/null 2>&1
    CODEX_HELP_EXIT=$?
    set -e
    if [ $CODEX_HELP_EXIT -ne 0 ]; then
        CODEX_AVAILABLE=false
        warn "Codex unavailable (exit=$CODEX_HELP_EXIT) — blocking commit"
        REVIEW_SKIPPED=true
    fi

    REVIEW_FILE=$(mktemp -t auto-dev-review.XXXXXX)
    if [ "$CODEX_AVAILABLE" = true ]; then
        log "Running Codex review..."
        # [AUTO-006] codex_review_watchdog — record start/finish epoch so the
        # nightly report can show review elapsed_ms, timed_out, and whether any
        # partial output was captured before killpg.
        CODEX_REVIEW_START_EPOCH=$(date +%s)
        set +e
        run_with_timeout "$CODEX_REVIEW_TIMEOUT_SECONDS" codex review --uncommitted > "$REVIEW_FILE" 2>&1
        CODEX_EXIT=$?
        set -e
        CODEX_REVIEW_END_EPOCH=$(date +%s)
        log "Codex exit code: $CODEX_EXIT"
        log "Codex review elapsed: $(( CODEX_REVIEW_END_EPOCH - CODEX_REVIEW_START_EPOCH ))s (timeout=${CODEX_REVIEW_TIMEOUT_SECONDS}s)"
        if [ $CODEX_EXIT -ne 0 ]; then
            REVIEW_ERR=$(cat "$REVIEW_FILE" 2>/dev/null || echo "")
            if echo "$REVIEW_ERR" | grep -qiE "(auth|token|quota|usage[[:space:]_-]*limit|credits|rate.limit|401|403|429|unauthorized|billing)"; then
                warn "Codex token/auth error — blocking commit"
                CODEX_AVAILABLE=false
                REVIEW_SKIPPED=true
            fi
        fi
    fi

    REVIEW_CONTENT=$(cat "$REVIEW_FILE" 2>/dev/null || echo "")

    # Save full review to docs/reviews/ (with exit code)
    REVIEW_SAVE_PATH="$REVIEW_DIR/${TASK_ID}-$(date +%Y%m%d)-round${ROUND}.txt"
    {
        echo "Task: $TASK_ID - $TASK_TITLE"
        echo "Date: $(date +%Y-%m-%d_%H:%M:%S)"
        echo "Codex exit code: $CODEX_EXIT"
        echo "Round: $ROUND"
        echo "---"
        cat "$REVIEW_FILE"
    } > "$REVIEW_SAVE_PATH"
    cp "$REVIEW_SAVE_PATH" "$RUN_DIR/codex-review-round${ROUND}.txt"
    log "Review saved: $REVIEW_SAVE_PATH"

    # [AUTO-008] Parse only the final Codex answer.  Review logs include task
    # context and diffs that may mention P0/P1/P2; scanning the whole file
    # creates false positives.  The old `echo ... | grep -q` check also became
    # a false negative under pipefail when large output made echo hit SIGPIPE.
    REVIEW_DECISION="UNKNOWN"
    REVIEW_PARSE_OUTPUT=""
    REVIEW_PARSE_EXIT=20
    REVIEW_PARSER="$SCRIPT_DIR/parse_codex_review.py"
    if [ "$REVIEW_SKIPPED" = false ] && [ "$CODEX_EXIT" -eq 0 ]; then
        if [ -f "$REVIEW_PARSER" ]; then
            set +e
            REVIEW_PARSE_OUTPUT=$(python3 "$REVIEW_PARSER" "$REVIEW_FILE" 2>&1)
            REVIEW_PARSE_EXIT=$?
            set -e
            case "$REVIEW_PARSE_EXIT" in
                0) REVIEW_DECISION="CLEAN" ;;
                10) REVIEW_DECISION="FINDINGS" ;;
                *) REVIEW_DECISION="UNKNOWN" ;;
            esac
        else
            REVIEW_PARSE_OUTPUT="STATUS=UNKNOWN"$'\n'"ERROR=missing parser: $REVIEW_PARSER"
        fi
    fi
    log "Codex review decision: $REVIEW_DECISION"

    # [AUTO-006] codex_review_watchdog — persist review meta for the nightly
    # report regardless of outcome.  A zero Codex process exit is not enough:
    # the final review must explicitly classify as clean.
    CODEX_META_STATUS="UNKNOWN"
    if [ "$REVIEW_SKIPPED" = true ]; then
        CODEX_META_STATUS="SKIPPED"
    elif [ "$CODEX_EXIT" -eq 124 ]; then
        CODEX_META_STATUS="TIMEOUT"
    elif [ "$CODEX_EXIT" -eq 0 ] && [ "$REVIEW_DECISION" = "CLEAN" ]; then
        CODEX_META_STATUS="PASS"
    else
        CODEX_META_STATUS="FAIL"
    fi
    write_review_meta \
        "$RUN_DIR/review-meta-round${ROUND}.json" \
        "$ROUND" \
        "${CODEX_REVIEW_START_EPOCH:-0}" \
        "${CODEX_REVIEW_END_EPOCH:-0}" \
        "$CODEX_REVIEW_TIMEOUT_SECONDS" \
        "$CODEX_EXIT" \
        "$([ "$CODEX_EXIT" -eq 124 ] && echo true || echo false)" \
        "$REVIEW_SKIPPED" \
        "$REVIEW_FILE" \
        "$CODEX_META_STATUS"

    # Review skipped (token unavailable) -> STOP, do not commit without review
    # [2026-06-04] Hard rule: no commit without Codex review. No exceptions.
    if [ "$REVIEW_SKIPPED" = true ]; then
        err "Codex unavailable — cannot commit without review"
        RESULT_STATUS="NEEDS_HUMAN"
        LAST_FAILURE_REASON="Codex unavailable (token/auth), review is mandatory"
        ISSUES_LOG+=("[Round $ROUND] Codex unavailable — blocked commit, NEEDS_HUMAN")
        break
    fi

    if [ $CODEX_EXIT -eq 124 ]; then
        # [AUTO-006] codex_review_watchdog — record partial-output metadata so
        # the human recovery path knows whether the timeout produced any
        # salvageable review text. We never treat a timeout as PASS; the
        # batch stops here and the dirty tree + review archive stay in place
        # for human/Codex follow-up.
        _partial_bytes=0
        if [ -f "$REVIEW_FILE" ]; then
            _partial_bytes=$(wc -c < "$REVIEW_FILE" 2>/dev/null | tr -d ' ' || echo 0)
        fi
        LAST_FAILURE_REASON="Codex review timed out after ${CODEX_REVIEW_TIMEOUT_SECONDS}s (partial_output_bytes=${_partial_bytes})"
        ISSUES_LOG+=("[Round $ROUND] Codex review timed out after ${CODEX_REVIEW_TIMEOUT_SECONDS}s; partial_output_bytes=${_partial_bytes}; archive=$RUN_DIR/codex-review-round${ROUND}.txt")
        err "Codex review timed out, stopping batch and leaving files for human review"
        err "  Partial review archive: docs/task_runs/$RUN_ID/codex-review-round${ROUND}.txt"
        err "  Review metadata:        docs/task_runs/$RUN_ID/review-meta-round${ROUND}.json"
        err "  Tree intentionally left dirty for human inspection (no commit)."
        RESULT_STATUS="NEEDS_HUMAN"
        break
    fi

    # Codex review failed -> infrastructure/config issue, not an implementation
    # issue. Stop the batch and leave the dirty tree for human/Codex recovery
    # instead of burning OpenCode rounds on a non-code failure.
    if [ $CODEX_EXIT -ne 0 ]; then
        LAST_FAILURE_REASON="Codex review failed with exit ${CODEX_EXIT}"
        ISSUES_LOG+=("[Round $ROUND] Codex review failed (exit=$CODEX_EXIT); archive=$RUN_DIR/codex-review-round${ROUND}.txt")
        err "Codex review failed (exit=${CODEX_EXIT}), stopping batch for human recovery"
        err "  Review archive: docs/task_runs/$RUN_ID/codex-review-round${ROUND}.txt"
        err "  Review metadata: docs/task_runs/$RUN_ID/review-meta-round${ROUND}.json"
        RESULT_STATUS="NEEDS_HUMAN"
        break
    fi

    if [ "$REVIEW_DECISION" = "FINDINGS" ]; then
        LAST_FAILURE_REASON="Codex review reported P0/P1/P2 correctness findings"
        warn "Codex review found P0/P1/P2 issues, preparing fix..."
        cat > "$PROMPT_FILE" <<FIX_EOF
# Fix task: $TASK_ID

Codex review found correctness issues, please fix every finding:

## Final Codex Review
\`\`\`
$(printf '%s\n' "$REVIEW_PARSE_OUTPUT")
\`\`\`

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
FIX_EOF
        continue
    fi

    if [ "$REVIEW_DECISION" != "CLEAN" ]; then
        LAST_FAILURE_REASON="Codex review verdict was ambiguous"
        ISSUES_LOG+=("[Round $ROUND] Codex review verdict UNKNOWN; fail-closed, archive=$RUN_DIR/codex-review-round${ROUND}.txt")
        err "Codex review did not contain an explicit clean verdict; stopping batch"
        RESULT_STATUS="NEEDS_HUMAN"
        break
    fi

    log "Codex review passed (no P0/P1/P2 correctness findings)"
    REVIEW_OUTPUT="$REVIEW_CONTENT"
    RESULT_STATUS="PASS"
    break
done

# --- 4. Process results ---
COMMIT_HASH=""

if [ "$RESULT_STATUS" = "PASS" ]; then
    REVIEW_NOTE="no P0/P1/P2 correctness findings"
    cat > "$RUN_DIR/summary.md" <<SUMMARY_EOF
# Auto Dev Summary

- Task: $TASK_ID - $TASK_TITLE
- Priority: $TASK_PRIO
- Final status: $RESULT_STATUS
- Rounds: $ROUND
- Tests: PASS
- Codex review: $REVIEW_NOTE
- OpenCode timeout seconds: $OPENCODE_TIMEOUT_SECONDS
- Test timeout seconds: $TEST_TIMEOUT_SECONDS
- Codex review timeout seconds: $CODEX_REVIEW_TIMEOUT_SECONDS
- Review file: docs/reviews/${TASK_ID}-$(date +%Y%m%d)-round${ROUND}.txt
- Run directory: docs/task_runs/$RUN_ID
- Finished at: $(date +%Y-%m-%d_%H:%M:%S)
SUMMARY_EOF

    # 4a. Stage all implementation directories (tests/tradingagents/api/frontend/scheduler/docs/scripts)
    log "Precise commit: git add tests/ tradingagents/ api/ frontend/ scheduler/ docs/ scripts/"
    git add tests/ tradingagents/ api/ frontend/ scheduler/ docs/ scripts/

    # 4b. Exclude temp and backup files
    EXCLUDE_PATTERNS=('*.backup' '*_original.py' '*_fixed.py' 'patch_*.py' '*.tmp' '*.log')
    for pat in "${EXCLUDE_PATTERNS[@]}"; do
        git reset HEAD -- "$pat" 2>/dev/null || true
    done

    # Exclude root-level .py files (keep only tests/ and tradingagents/)
    git diff --cached --name-only -- '*.py' | while read -r f; do
        if [ "$(dirname "$f")" = "." ]; then
            git reset HEAD -- "$f"
        fi
    done

    # 4c. Pre-commit checklist: check staged files
    STAGED=$(git diff --cached --name-only)
    if [ -z "$STAGED" ]; then
        log "No valid files to commit"
        RESULT_STATUS="DONE"
    else
        log "Files to be committed:"
        echo "$STAGED"

        # Check for files that should not be committed
        SUSPICIOUS=$(echo "$STAGED" | grep -E '^([^/]+\.py$|.*\.backup$|.*_original\.py$|.*_fixed\.py$|patch_.*\.py$|.*\.tmp$|.*\.log$|__pycache__)' || true)
        if [ -n "$SUSPICIOUS" ]; then
            err "Found files that should not be committed:"
            echo "$SUSPICIOUS"
            err "Aborting commit, marking NEEDS_HUMAN"
            git reset HEAD
            RESULT_STATUS="NEEDS_HUMAN"
        fi
    fi
fi

if [ "$RESULT_STATUS" = "PASS" ]; then
    REVIEW_DEVLOG_NOTE="no P0/P1/P2 correctness findings"
    REVIEW_COMMIT_NOTE=""
    # 4d. Write DEVLOG (before commit, will be included)
    cat >> "$DEVLOG_FILE" <<DEVLOG_EOF

## $(date +%Y-%m-%d) | AUTO-002 Auto Dev Loop

- **Task**: $TASK_ID - $TASK_TITLE
- **Priority**: $TASK_PRIO
- **Rounds**: $ROUND
- **Status**: OK $RESULT_STATUS
- **Tests**: Passed
- **Codex Review**: $REVIEW_DEVLOG_NOTE
- **Timeout budget**: OpenCode ${OPENCODE_TIMEOUT_SECONDS}s / tests ${TEST_TIMEOUT_SECONDS}s
- **Review file**: docs/reviews/${TASK_ID}-$(date +%Y%m%d)-round${ROUND}.txt
- **Run archive**: docs/task_runs/$RUN_ID/
DEVLOG_EOF
    log "Wrote to DEVLOG.md"

    # 4e. Stage DEVLOG/TASKS changes
    git add docs/

    # 4f. Commit implementation and run archive.
    COMMIT_MSG="auto: $TASK_ID $TASK_TITLE [AUTO-002]${REVIEW_COMMIT_NOTE}"
    git commit -m "$COMMIT_MSG"
    COMMIT_HASH=$(git rev-parse --short HEAD)
    log "Committed: $COMMIT_HASH"

    # 4g. After PASS, commit with accurate hash to prevent re-claim.
    update_task_status "done -- commit ${COMMIT_HASH}"

    # [AUTO-007] dependency_aware_claim
    # Auto-release downstream blocked tasks that opted in via
    # `auto_release: true` and whose depends_on are now satisfied. Never
    # touches NEEDS_HUMAN / 战略暂停 / blocked-human / auto_release=false
    # tasks. Output is logged; failures are non-fatal (worst case: the next
    # picker iteration just won't see the released task and a human can do
    # it manually).
    DEP_RESOLVER="$SCRIPT_DIR/task_dependency_resolver.py"
    if [ -f "$DEP_RESOLVER" ]; then
        log "[AUTO-007] releasing downstream tasks of $TASK_ID..."
        set +e
        python3 "$DEP_RESOLVER" release \
            --tasks-file "$TASKS_FILE" \
            --task-id "$TASK_ID" 2>&1 | tee -a "$RUN_DIR/release.log" | tail -10
        REL_EXIT=${PIPESTATUS[0]}
        set -e
        if [ "$REL_EXIT" -ne 0 ]; then
            warn "[AUTO-007] release step exited $REL_EXIT, continuing (status update already applied)"
        fi
    fi

    git add docs/TASKS.md
    git commit -m "docs: mark $TASK_ID done after auto run"
    RESULT_STATUS="DONE"

elif [ "$RESULT_STATUS" != "DONE" ]; then
    # Max rounds exceeded or pre-commit check failed -> no commit
    err "Needs human intervention"
    RESULT_STATUS="NEEDS_HUMAN"
fi

if [ "$RESULT_STATUS" = "NEEDS_HUMAN" ]; then
    if [ -z "$LAST_FAILURE_REASON" ]; then
        LAST_FAILURE_REASON="tests/review/pre-commit did not pass within the allowed rounds"
    fi
    cat > "$RUN_DIR/summary.md" <<SUMMARY_EOF
# Auto Dev Summary

- Task: $TASK_ID - $TASK_TITLE
- Priority: $TASK_PRIO
- Final status: NEEDS_HUMAN
- Rounds: $ROUND
- Reason: $LAST_FAILURE_REASON
- OpenCode timeout seconds: $OPENCODE_TIMEOUT_SECONDS
- Test timeout seconds: $TEST_TIMEOUT_SECONDS
- Run directory: docs/task_runs/$RUN_ID
- Finished at: $(date +%Y-%m-%d_%H:%M:%S)

## Issue log

SUMMARY_EOF
    for issue in "${ISSUES_LOG[@]}"; do
        echo "- $issue" >> "$RUN_DIR/summary.md"
    done
    if [ ${#ISSUES_LOG[@]} -eq 0 ]; then
        echo "(no issues recorded)" >> "$RUN_DIR/summary.md"
    fi
    echo "" >> "$RUN_DIR/summary.md"
    echo "Before manual intervention, check OpenCode, test, and Codex review logs in this directory." >> "$RUN_DIR/summary.md"

    update_task_status "blocked — NEEDS_HUMAN, see docs/task_runs/$RUN_ID"

    cat >> "$DEVLOG_FILE" <<DEVLOG_EOF

## $(date +%Y-%m-%d) | AUTO-002 Auto Dev Loop

- **Task**: $TASK_ID - $TASK_TITLE
- **Priority**: $TASK_PRIO
- **Rounds**: $ROUND (max)
- **Status**: FAIL NEEDS_HUMAN
- **Reason**: $LAST_FAILURE_REASON
- **Run archive**: docs/task_runs/$RUN_ID/
DEVLOG_EOF
    log "Wrote to DEVLOG.md (not committed)"
fi

# --- 5. Output summary ---
echo ""
echo "========================================"
echo "  AUTO-002 Execution Summary"
echo "========================================"
echo "  Task:   $TASK_ID - $TASK_TITLE"
echo "  Priority: $TASK_PRIO"
echo "  Rounds: $ROUND"
echo "  Status: $RESULT_STATUS"
echo "  Archive: docs/task_runs/$RUN_ID/"
if [ -n "$COMMIT_HASH" ]; then
    echo "  Commit: $COMMIT_HASH"
fi
echo "========================================"
if [ ${#ISSUES_LOG[@]} -gt 0 ]; then
    echo ""
    echo "  Issues (${#ISSUES_LOG[@]} items):"
    for issue in "${ISSUES_LOG[@]}"; do
        echo "  - $issue"
    done
    echo "========================================"
fi

# --- Count and decide whether to continue ---
if [ "$RESULT_STATUS" = "DONE" ]; then
    COMPLETED_TASKS=$((COMPLETED_TASKS + 1))
    if [ "$AUTO_DEV_MAX_TASKS" != "0" ] && [ "$COMPLETED_TASKS" -ge "$AUTO_DEV_MAX_TASKS" ]; then
        log "--- Task $TASK_ID done, reached AUTO_DEV_MAX_TASKS=${AUTO_DEV_MAX_TASKS}, stopping ---"
        break
    fi
    log "--- Task $TASK_ID done, continuing to next ready task ---"
    continue
elif [ "$RESULT_STATUS" = "QUOTA_EXHAUSTED" ]; then
    FAILED_TASKS=$((FAILED_TASKS + 1))
    err "--- Task $TASK_ID failed (quota exhausted), stopping batch ---"
    err "--- Re-run after quota recovers ---"
else
    FAILED_TASKS=$((FAILED_TASKS + 1))
    err "--- Task $TASK_ID failed, stopping batch, needs human ---"
fi

# Reset per-task variables
RESULT_STATUS=""
ROUND=0
LAST_FAILURE_REASON=""
COMMIT_HASH=""
ISSUES_LOG=()

# Stop on failure
if [ $FAILED_TASKS -gt 0 ]; then
    break
fi


done

# --- Batch summary ---
echo ""
echo "========================================"
echo "  AUTO-002 Batch Summary"
echo "========================================"
echo "  Completed: $COMPLETED_TASKS"
echo "  Failed: $FAILED_TASKS"
echo "  Total: $((COMPLETED_TASKS + FAILED_TASKS))"
echo "========================================"

exit $([ $FAILED_TASKS -eq 0 ] && echo 0 || echo 1)
