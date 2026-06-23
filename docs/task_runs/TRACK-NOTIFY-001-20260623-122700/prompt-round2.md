# Fix task: TRACK-NOTIFY-001

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
session id: 019ef2c7-bf2e-78e0-a192-a05690985242
--------
user
current changes
2026-06-23T04:40:38.926426Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer resource_metadata=\"https://mcp.figma.com/.well-known/oauth-protected-resource\",scope=\"mcp:connect\",authorization_uri=\"https://api.figma.com/.well-known/oauth-authorization-server\"" })
2026-06-23T04:40:39.059998Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer realm=\"OAuth\", resource_metadata=\"https://mcp.notion.com/.well-known/oauth-protected-resource/mcp\", error=\"invalid_token\", error_description=\"Missing or invalid access token\"" })
2026-06-23T04:40:40.986087Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer resource_metadata=\"https://mcp.figma.com/.well-known/oauth-protected-resource\",scope=\"mcp:connect\",authorization_uri=\"https://api.figma.com/.well-known/oauth-authorization-server\"" })
2026-06-23T04:40:42.194894Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when AuthRequired(AuthRequiredError { www_authenticate_header: "Bearer realm=\"OAuth\", resource_metadata=\"https://mcp.notion.com/.well-known/oauth-protected-resource/mcp\", error=\"invalid_token\", error_description=\"Missing or invalid access token\"" })
exec
/bin/zsh -lc "sed -n '1,220p' docs/project-overview.md; printf '\\n---TASKS---\\n'; sed -n '1,220p' docs/TASKS.md; printf '\\n---DECISIONS---\\n'; sed -n '1,220p' docs/DECISIONS.md; printf '\\n---DEVLOG---\\n'; sed -n '1,220p' docs/DEVLOG.md" in /Users/maybee/TradingAgents-AShare
 succeeded in 0ms:
# 项目说明

## 项目定位

TradingAgents-AShare 是一个面向 A 股投资者的多智能体投研系统，核心理念：

- **模拟真实投研机构**：14 个专业角色分工协作
- **多空辩论机制**：多头研究员 vs 空头研究员，研究总监裁决
- **多模型厂商支持**：OpenAI / Anthropic / Google / 智谱 / DeepSeek / 小米等
- **意图驱动交互**：自然语言输入，自动识别标的和周期
- **持仓追踪**：导入持仓，自动跟踪，连续分析

## 核心架构

### 1. 分析师团队（7 个）

| 角色 | 职责 | 数据源 |
|------|------|--------|
| 基本面分析师 | 财务指标、估值分析 | AKShare/BaoStock |
| 情绪分析师 | 社交媒体情绪、舆情 | AKShare |
| 新闻分析师 | 新闻事件、政策影响 | AKShare |
| 技术分析师 | K线形态、技术指标 | AKShare/BaoStock |
| 宏观分析师 | 宏观经济、利率环境 | AKShare |
| 主力资金分析师 | 大单流向、主力动向 | AKShare |
| 量价分析师 | 成交量、量价关系 | AKShare/BaoStock |

### 2. 研究员团队

- **多头研究员**：综合分析师结论，构建看多 Claim
- **空头研究员**：综合分析师结论，构建看空 Claim
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
