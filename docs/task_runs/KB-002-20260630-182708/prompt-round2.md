# Fix task: KB-002

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
reasoning effort: xhigh
reasoning summaries: none
session id: 019f1813-7ca2-73e3-88b5-2fe04d139237
--------
user
current changes
2026-06-30T10:29:19.577793Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer realm=\"OAuth\", resource_metadata=\"https://mcp.notion.com/.well-known/oauth-protected-resource/mcp\", error=\"invalid_token\", error_description=\"Missing or invalid access token\"" })
2026-06-30T10:29:19.829902Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer resource_metadata=\"https://mcp.figma.com/.well-known/oauth-protected-resource\",scope=\"mcp:connect\",authorization_uri=\"https://api.figma.com/.well-known/oauth-authorization-server\"" })
2026-06-30T10:29:20.373651Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer realm=\"OAuth\", resource_metadata=\"https://mcp.notion.com/.well-known/oauth-protected-resource/mcp\", error=\"invalid_token\", error_description=\"Missing or invalid access token\"" })
2026-06-30T10:29:20.373654Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer resource_metadata=\"https://mcp.figma.com/.well-known/oauth-protected-resource\",scope=\"mcp:connect\",authorization_uri=\"https://api.figma.com/.well-known/oauth-authorization-server\"" })
exec
/bin/zsh -lc 'git status --short && git branch --show-current' in /Users/maybee/TradingAgents-AShare
 succeeded in 0ms:
 M docs/TASKS.md
?? docs/task_runs/KB-002-20260630-182708/
local/tradingagents-custom

exec
/bin/zsh -lc 'git diff -- docs/TASKS.md && find docs/task_runs/KB-002-20260630-182708 -maxdepth 3 -type f -print' in /Users/maybee/TradingAgents-AShare
 succeeded in 0ms:
diff --git a/docs/TASKS.md b/docs/TASKS.md
index ec9d636..30d6267 100644
--- a/docs/TASKS.md
+++ b/docs/TASKS.md
@@ -4339,7 +4339,7 @@
 ### KB-002: investment wiki 输出协议升级：TA 可消费字段 lint（P1）
 - **描述**：为 Tree Work 提出稳定的 investment wiki 输出协议，并在 TA 仓库实现 lint，避免后续消化研报后 TA 仍抓不到股票、主题、风险、来源和时效。
 - **优先级**：P1
-- **状态**：ready
+- **状态**：in_progress — claimed KB-002-20260630-182708
 - **前置条件**：KB-001 完成。
 - **Tree Work 新要求草案**：
   1. 每篇 investment wiki 必须保留基础 frontmatter：`title/created/updated/sources/tags/related`。
docs/task_runs/KB-002-20260630-182708/tests-round1.txt
docs/task_runs/KB-002-20260630-182708/codegraph-status.json
docs/task_runs/KB-002-20260630-182708/prompt-round1.md
docs/task_runs/KB-002-20260630-182708/codegraph-context.txt
docs/task_runs/KB-002-20260630-182708/opencode-round1.txt
docs/task_runs/KB-002-20260630-182708/task.md
docs/task_runs/KB-002-20260630-182708/codegraph-impact.txt

exec
/bin/zsh -lc "for f in docs/task_runs/KB-002-20260630-182708/*; do echo '---' "'$f; sed -n '"'1,220p' \""'$f"; done' in /Users/maybee/TradingAgents-AShare
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
