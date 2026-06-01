# Fix task: DATA-002

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
session id: 019e841c-c5a9-78d2-973b-8be1f0456b8e
--------
user
current changes
exec
/bin/zsh -lc "sed -n '1,160p' docs/DECISIONS.md && sed -n '1,160p' docs/DEVLOG.md" in /Users/maybee/TradingAgents-AShare
 succeeded in 0ms:
# 架构决策记录

> 记录项目中的关键架构决策，方便后续回溯"为什么这样做"。

---

## ADR-001: 采用 LangGraph 编排多智能体
- **日期**：项目初始化
- **背景**：需要 14 个 Agent 按特定流程协作，有循环（辩论）和条件分支
- **决策**：使用 LangGraph 状态图编排，而非简单的链式调用
- **原因**：LangGraph 原生支持循环、条件路由、状态管理，适合辩论机制
- **影响**：所有 Agent 间通信通过 Graph State 传递，新增 Agent 需要注册到图中

## ADR-002: 多模型厂商适配层
- **日期**：项目初始化
- **背景**：不同 Agent 对模型能力需求不同，用户可能有不同的模型偏好
- **决策**：通过 `llm_clients/` 统一适配，支持通过配置切换厂商和模型
- **原因**：避免硬编码单一厂商，降低用户使用门槛
- **影响**：新增模型厂商需要在 `llm_clients/` 中添加适配器

## ADR-003: SQLite 作为持久化方案
- **日期**：项目初始化
- **背景**：需要存储研报、自选股、定时任务等结构化数据
- **决策**：使用 SQLite，单文件部署，无需额外数据库服务
- **原因**：本地部署场景，轻量级，零运维
- **影响**：并发写入有限，定时任务和分析任务需要串行化写入

## ADR-004: OpenClaw 作为项目协作调度层
- **日期**：2026-05-09
- **背景**：需要 OpenClaw、OpenCode、OpenCode TUI 三者协作处理同一项目
- **决策**：OpenClaw 负责任务判断和调度，不直接做代码修改；OpenCode 负责代码执行；OpenCode TUI 负责审查
- **原因**：OpenClaw 有完整的 agent 调度能力（exec / sessions_spawn / cron），适合做编排层
- **影响**：所有代码修改任务必须经过 OpenClaw 判断复杂度后决定执行路径
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
