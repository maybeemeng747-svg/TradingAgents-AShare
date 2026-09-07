Independent correctness review — Task UPSTREAM-081-001, round 3.

You are reviewing an UNCOMMITTED working-tree diff in this repo (branch local/tradingagents-custom):
- Modified (code): api/main.py, tradingagents/graph/data_collector.py
- New (untracked, part of the change): tests/test_upstream081_runtime_resilience.py
- Modified (docs, context only, not review targets): docs/TASKS.md, docs/DEVLOG.md

## Context

- Task: selective upstream v0.8.1 absorption (thread-pool saturation protection, stock identification fallback, user-comprehensible error semantics). Task detail: docs/TASKS.md "UPSTREAM-081-001". Design intent: docs/task_runs/UPSTREAM-081-001-20260902-191510/implementation.md.
- Round 2 review (docs/reviews/UPSTREAM-081-001-20260903-round2.txt) found 3×P1 + 2×P2. Fix round 2 claims to address them:
  1. [P1] LHB force retry outside whole-round deadline → expected fix: LHB force query bounded by remaining round budget in data_collector.py (`_apply_forced_lhb` + `round_deadline` wiring in `_fetch_all`); on budget exhaustion it is skipped with an explicit degraded status text.
  2. [P1] per-round executor abandoned workers unbounded → expected fix: module-level shared bounded named daemon pool (`_BoundedFetchPool`, `get_fetch_pool`/`get_fetch_pool_stats`/`mark_abandoned`), `/healthz` exposes `fetch_pool` gauges.
  3. [P1] `Authorization: Bearer <token>` sanitization incomplete in api/main.py `_sanitize_analysis_error_text` → expected fix: full credential/JWT redaction including key=/value= and header shapes.
  4. [P2] idempotent shortcut bypasses sanitization when text contains "（原始错误：" → expected fix: shortcut runs `_sanitize_analysis_error_text` before returning, idempotency preserved.
  5. [P2] custom `TA_DATA_FETCH_TIMEOUT ≥ 360` breaks lock-margin invariant → expected fix: `FETCH_LOCK_TIMEOUT = max(env, FETCH_ALL_TIMEOUT + 60)` derivation at import time, default 300/360 unchanged, explicit larger lock still honoured.

## Your job

1. For each of the 5 findings above, verify the fix is correct and COMPLETE in the current working diff (not merely present).
2. Review the ENTIRE uncommitted code diff for NEW correctness issues introduced by fix round 2: concurrency/races, deadline/timeout semantics, sanitization bypass or over-redaction, config invariant edge cases, error-status semantics (no fake success), and test correctness (tests must not be tautological, must not deadlock or flake by design).
3. Regression safety per docs/TASKS.md acceptance criteria: no silent 524 (queue overflow observable), same task must not double-run the probe, deterministic symbol parsing first with clear error on fallback failure, user-facing error text must not leak keys/URLs/internal stacks.
4. Assess whether tests/test_upstream081_runtime_resilience.py (44 tests) genuinely cover the 5 fixes.

## Constraints

- READ-ONLY review: do not modify any file; do not run pytest or any network calls; static review of the diff and code only.
- Focus on correctness. Style nits are not findings.

## Output format (plain text)

结论: PASS（无 P0/P1/P2 correctness finding） 或 FAIL + findings list
[P0|P1|P2] file:line — issue description
（可选）[NOTE] 非阻塞建议
