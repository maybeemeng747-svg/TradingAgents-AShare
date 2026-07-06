# PASS_UNREVIEWED Follow-up Review — 2026-07-06

## Scope

Reviewed commits after `a61e0eb`, covering the July 5 automatic-development batch that was committed as `PASS_UNREVIEWED` because Codex review quota was unavailable.

## Codex Review Configuration

- Model: `gpt-5.5`
- Provider: `openai`
- Reasoning effort: `xhigh`
- Review command: `codex review --base a61e0eb`

## Findings

Codex review returned three P2 findings:

1. IC local-knowledge bucket counted empty/failed lookup digests as hits.
2. Local-knowledge cache accepted malformed cache files with a valid manifest but missing/incomplete `pages`.
3. `/v1/db-hygiene` exposed internal DB hygiene information without API authentication.

## Fixes

- Empty no-hit lookups are skipped; failed lookups now set bucket `data_status=failed`.
- Disk cache loading now rejects missing/incomplete `pages` and triggers rebuild.
- `/v1/db-hygiene` now requires read-only auth. JWT and API Token callers are
  allowed, but API Token verification does not update `last_used_at`.
- Added regression tests for all three findings.
- Follow-up Codex reviews found two failed-lookup edge cases:
  - failed zero-hit symbol lookups were filtered as missing;
  - failed theme-only lookups were also reported as missing.
- Both are fixed by propagating failed symbol/theme lookup status before normal no-hit filtering.
- A second follow-up Codex review found that regular API-token auth would update
  `last_used_at`, violating the hygiene endpoint's read-only contract. Fixed by
  adding `verify_token_readonly()` and a read-only endpoint dependency.

## Verification

- `pytest tests/test_kb006_local_knowledge_context_api.py -q`: 68 passed
- `pytest tests/test_kb010_local_knowledge_cache.py -q`: 55 passed
- `pytest tests/test_kb006_local_knowledge_context_api.py tests/test_kb010_local_knowledge_cache.py tests/test_api_smoke.py tests/test_runtime_tier_contract.py -q --tb=short`: 244 passed
- `pytest tests/test_api_smoke.py tests/test_data026_db_hygiene.py tests/test_kb006_local_knowledge_context_api.py tests/test_kb010_local_knowledge_cache.py -q --tb=short`: 192 passed
- Final `codex review --uncommitted`: passed, no correctness issues
