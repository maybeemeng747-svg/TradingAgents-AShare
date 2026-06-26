# Fix task: H-016

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
session id: 019f0441-913c-77a1-86d7-10616161786a
--------
user
current changes
2026-06-26T14:07:15.378929Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer realm=\"OAuth\", resource_metadata=\"https://mcp.notion.com/.well-known/oauth-protected-resource/mcp\", error=\"invalid_token\", error_description=\"Missing or invalid access token\"" })
2026-06-26T14:07:15.434737Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer resource_metadata=\"https://mcp.figma.com/.well-known/oauth-protected-resource\",scope=\"mcp:connect\",authorization_uri=\"https://api.figma.com/.well-known/oauth-authorization-server\"" })
2026-06-26T14:07:16.380282Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer realm=\"OAuth\", resource_metadata=\"https://mcp.notion.com/.well-known/oauth-protected-resource/mcp\", error=\"invalid_token\", error_description=\"Missing or invalid access token\"" })
2026-06-26T14:07:16.454565Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer resource_metadata=\"https://mcp.figma.com/.well-known/oauth-protected-resource\",scope=\"mcp:connect\",authorization_uri=\"https://api.figma.com/.well-known/oauth-authorization-server\"" })
exec
/bin/zsh -lc 'git status --short && git status --branch --short' in /Users/maybee/TradingAgents-AShare
 succeeded in 0ms:
 M docs/TASKS.md
 M docs/tradeflow_trial_acceptance.md
 M docs/tradeflow_trial_acceptance_v2.md
 M docs/tradeflow_trial_acceptance_v3.md
 M tradingagents/tradeflow/mandate_daily_report.py
?? docs/task_runs/H-016-20260626-215858/
?? scripts/run_mandate_daily_report.py
?? tests/test_h016_mandate_daily_cli.py
## local/tradingagents-custom...myfork/local/tradingagents-custom [ahead 182]
 M docs/TASKS.md
 M docs/tradeflow_trial_acceptance.md
 M docs/tradeflow_trial_acceptance_v2.md
 M docs/tradeflow_trial_acceptance_v3.md
 M tradingagents/tradeflow/mandate_daily_report.py
?? docs/task_runs/H-016-20260626-215858/
?? scripts/run_mandate_daily_report.py
?? tests/test_h016_mandate_daily_cli.py

exec
/bin/zsh -lc 'git diff -- tradingagents/tradeflow/mandate_daily_report.py scripts/run_mandate_daily_report.py tests/test_h016_mandate_daily_cli.py && git diff --stat' in /Users/maybee/TradingAgents-AShare
 succeeded in 0ms:
diff --git a/tradingagents/tradeflow/mandate_daily_report.py b/tradingagents/tradeflow/mandate_daily_report.py
index 0c2b98e..d7dfa5a 100644
--- a/tradingagents/tradeflow/mandate_daily_report.py
+++ b/tradingagents/tradeflow/mandate_daily_report.py
@@ -10,12 +10,17 @@ from __future__ import annotations
 
 import json
 import os
+import re
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
