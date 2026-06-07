# Fix task: DATA-015

Codex review found critical issues, please fix per review:

## Codex Review output
```
OpenAI Codex v0.130.0
--------
workdir: /Users/maybee/TradingAgents-AShare
model: gpt-5.5
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: medium
reasoning summaries: none
session id: 019ea40f-85bf-7903-a5a1-9e64dc2885d8
--------
user
current changes
exec
/bin/zsh -lc 'git status --short && git rev-parse --show-toplevel && git branch --show-current' in /Users/maybee/TradingAgents-AShare
 succeeded in 0ms:
 M docs/DEVLOG.md
 M docs/TASKS.md
 M tradingagents/dataflows/evidence_contract.py
 M tradingagents/dataflows/evidence_coverage_audit.py
 M tradingagents/dataflows/fixture_replay.py
 M tradingagents/dataflows/live_smoke.py
 M tradingagents/dataflows/providers/cn_astock_provider.py
 M tradingagents/dataflows/source_catalog.py
?? docs/task_runs/DATA-015-20260608-054126/
?? tests/test_data015_zt_pool.py
/Users/maybee/TradingAgents-AShare
local/tradingagents-custom

exec
/bin/zsh -lc 'git diff --stat && git diff -- tradingagents/dataflows/evidence_contract.py tradingagents/dataflows/evidence_coverage_audit.py tradingagents/dataflows/fixture_replay.py tradingagents/dataflows/live_smoke.py tradingagents/dataflows/providers/cn_astock_provider.py tradingagents/dataflows/source_catalog.py tests/test_data015_zt_pool.py' in /Users/maybee/TradingAgents-AShare
 succeeded in 0ms:
 docs/DEVLOG.md                                     |  46 ++++++
 docs/TASKS.md                                      |   6 +-
 tradingagents/dataflows/evidence_contract.py       |   1 +
 tradingagents/dataflows/evidence_coverage_audit.py |   3 +-
 tradingagents/dataflows/fixture_replay.py          | 165 +++++++++++++++++++--
 tradingagents/dataflows/live_smoke.py              |   8 +
 .../dataflows/providers/cn_astock_provider.py      |  56 +++++++
 tradingagents/dataflows/source_catalog.py          |  10 ++
 8 files changed, 282 insertions(+), 13 deletions(-)
diff --git a/tradingagents/dataflows/evidence_contract.py b/tradingagents/dataflows/evidence_contract.py
index 13dca15..5c791f0 100644
--- a/tradingagents/dataflows/evidence_contract.py
+++ b/tradingagents/dataflows/evidence_contract.py
@@ -188,6 +188,7 @@ _REQUIRED_FIELDS_FOR_COMPLETENESS: Dict[str, List[str]] = {
     "research_report": ["status", "vendor"],  # [DATA-011] research_report_raw_evidence
     "ratings": ["status", "vendor"],  # [DATA-012] rating_raw_evidence
     "buybacks": ["status", "vendor", "unit"],  # [DATA-013] buyback_raw_evidence
+    "zt_pool": ["status", "vendor"],  # [DATA-015] limit_up_pool_fallback
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
