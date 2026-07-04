# KB-004 Run Summary

- Task: KB-004 - TradeFlow 昊天候选接入本地知识命中分与证据摘要（P1）
- Status: ✅ DONE (implementation complete; outer script handles commit)
- Completed at: 2026-07-04

## Files Changed

### Source code
- `tradingagents/dataflows/local_knowledge_provider.py` — new
  `compute_local_knowledge_score()` and `needs_tree_work_research()` helpers.
- `tradingagents/tradeflow/mandate_evidence_packet.py` — added
  `local_knowledge_summary` field to `MandateEvidencePacket`; threaded
  optional `local_knowledge_result` through `build_evidence_packet` /
  `build_evidence_packets_for_candidates`.
- `tradingagents/tradeflow/mandate_daily_report.py` — `build_mandate_daily_report`
  now accepts optional `knowledge_root` to inject local-knowledge summaries.
- `api/services/tradeflow_service.py` — new enrichment helpers
  (`_enrich_candidate_with_local_knowledge`, batch variant, etc.); wired
  into `get_daily_plan` / `get_candidates` / `get_candidate_detail`.
- `api/tradeflow_schemas.py` — new fields on `TradeFlowCandidateItem`
  (local_knowledge_score, knowledge_hit_count, local_knowledge_summary,
  local_knowledge_detail, needs_tree_work_research) and
  `MandateEvidencePacketItem` (local_knowledge_summary).

### Tests
- `tests/test_kb004_tradeflow_knowledge_score.py` — 58 new tests.

### Docs
- `docs/DEVLOG.md` — entry added.
- `docs/TASKS.md` — KB-004 marked done.

## Key Logic

- `local_knowledge_score` is computed from `LocalKnowledgeQueryResult`:
  fresh pages weighted by confidence (high=1.0, medium=0.6, low=0.3),
  capped at 3.0; **stale / low-confidence / to-be-supplemented pages
  contribute 0** but are still surfaced via `stale_hit_count` /
  `low_confidence_hit_count` and the `matched_pages_brief` list.
- The score is purely explanatory: it never modifies tier, action,
  confidence, or `needs_manual_research`. KB-008 research-attention
  fields remain untouched alongside the new KB-004 fields.
- `needs_tree_work_research` fires when a 昊天 left-side candidate
  (POLICY_AMBUSH/POLICY_CONFIRM/EVENT_WATCH or non-empty mandate_topic)
  with a hot topic (mandate_score>0 or topic_status in RISING/CONFIRMED/…)
  has no fresh local-knowledge hit.
- H-017 evidence packet carries `local_knowledge_summary` so the daily
  report / candidate detail can render matched pages, update time, and
  risks alongside the policy/industry/company layers.

## Test Results

- `pytest tests/test_kb004_tradeflow_knowledge_score.py -q` → **58 passed**.
- Combined regression
  (`test_kb004 + test_h017 + test_kb008 + test_kb003 + test_h015`) →
  **219 passed**.
- task.md commands (`test_api_smoke + test_runtime_tier_contract`) →
  **119 passed**.
- Wider subset (`-k "tradeflow or mandate or kb00 or h017 or h015"`) →
  **1711 passed / 1 pre-existing failure** (KB-009 date-sensitive test,
  verified to fail on clean checkout via `git stash`).

## Constraints Honored

- No changes to `tradingagents/prompts/`.
- No writes to prod `tradingagents.db` (no schema migration; only in-memory
  field enrichment on candidate reads).
- No full-market scan or stock deep TA.
- No push / PR / merge / git commit.
- Local-knowledge hits only add explanatory power; never trigger candidate
  entry; expired knowledge does not add score.
