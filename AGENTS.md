# TradingAgents-AShare — 项目协作规则

## 项目简介

A股智能投研多智能体系统，基于 LangGraph + LangChain 构建，模拟 14 名专业 Agent 的多空辩论与风控博弈，为投资者提供结构化的交易建议。

核心模块：
- **Agent 框架**：14 个多智能体（分析师/研究员/风控/交易员），LangGraph 编排
- **后端 API**：FastAPI，提供分析、持仓、定时任务、研报管理等接口
- **前端 UI**：React，意图驱动交互、辩论可视化、持仓看板
- **定时调度**：独立 scheduler，支持自选股定时分析
- **数据层**：AKShare + BaoStock + yfinance，SQLite 持久化

## 多工具协作架构

```
孟（最终决策）
  │
  ├── OpenClaw（主控调度）── 读取上下文 → 判断任务 → 派发执行
  │     ├── 简单任务：直接 exec 执行（git/python/npm）
  │     └── 复杂任务：sessions_spawn 子 agent 或调用 OpenCode 执行
  │
  ├── OpenCode（代码执行器）── 读取同一套上下文 → 改代码 → 跑测试 → 输出 diff
  │
  └── OpenCode TUI/Web（审查界面）── 查看 diff → 确认修改 → 做 review
```

**核心原则**：三者读取同一套项目上下文，不各自保存独立记忆。

## 开发与验收思维模式

### 功能开发：第一性原理 + 剃刀定律
- 开发新功能或修 bug 时，先回到问题本质：用户真正要解决什么、系统必须满足的最小事实是什么、哪些约束不能破坏。
- 默认选择能满足目标的最小实现，优先复用现有模块、数据契约和测试框架，不为“看起来完整”而堆叠复杂功能。
- 每个新增字段、接口、状态和抽象都必须能回答：为什么它不可少；如果删掉它，哪个明确场景会失败。
- 不用大改架构来解决局部问题；除非能证明现有结构已经无法承载目标。

### 功能验收/测试：多 Agent + 墨菲定律 + 对抗性审查
- 验收时默认假设：会遗漏的边界一定会遗漏，会被误用的字段一定会被误用，会超时/缺数据/脏状态的路径一定会发生。
- 重要改动必须至少从三类视角审查：实现者视角、用户操作视角、风控/数据真实性视角；必要时让不同 AI/Agent 分别做 diff review、测试回放和失败路径检查。
- 测试要覆盖失败、空数据、旧数据、字段缺失、重复执行、非交易日、脏工作区、接口顺序、状态迁移等最容易出事的路径。
- Codex review 或人工 review 必须优先找 P0/P1 风险，而不是复述功能亮点；发现问题先打回修复，再谈提交。

## 必读文件（任何 coding agent 开始任务前）

1. `AGENTS.md`（本文件）
2. `docs/project-overview.md`（项目背景 + 架构 + 目录结构）
3. `docs/TASKS.md`（当前任务池）
4. `docs/DECISIONS.md`（架构决策记录）
5. `docs/DEVLOG.md`（最近修改日志）
6. 涉及模型/API 调用时，必须读 `docs/MODEL_API_CATALOG.md` 和 `docs/LLM_API_USAGE.md`，并可运行 `python scripts/audit_llm_config.py` 查看脱敏配置

## 项目目录结构

```
TradingAgents-AShare/
├── tradingagents/            # 核心 Python 包
│   ├── agents/               # 14 个多智能体
│   │   ├── analysts/         # 7 个分析师（基本面/情绪/新闻/技术/宏观/主力/量价）
│   │   ├── researchers/      # 多头/空头研究员 + 研究总监
│   │   ├── risk_mgmt/        # 风控团队
│   │   ├── managers/         # 管理层
│   │   └── trader/           # 交易员
│   ├── dataflows/            # 数据获取与缓存
│   ├── graph/                # LangGraph 编排图
│   ├── llm_clients/          # 多模型厂商适配
│   ├── prompts/              # Agent 提示词模板
│   └── default_config.py     # 默认配置
├── api/                      # FastAPI 后端
│   ├── main.py               # 入口
│   ├── database.py           # SQLite
│   └── services/             # 业务逻辑（分析/持仓/定时/研报）
├── frontend/                 # React 前端
├── scheduler/                # 定时分析调度
├── skills/                   # OpenClaw 技能
├── tests/                    # 测试
├── docs/                     # 项目文档
└── eval_results/             # 回测评估结果
```

## 执行规则

### 代码修改前
1. 执行 `git status`，确认当前分支和未提交变更
2. 激活虚拟环境：`source .venv/bin/activate`
3. 确认修改范围不影响生产数据库（tradingagents.db）
4. 如果涉及核心模块，先跑一遍测试确认基线

### 代码修改后
1. 运行测试，全部通过才算完成
2. 输出：修改文件列表、测试结果、发现的风险点
3. 更新 `docs/DEVLOG.md`

### OpenClaw 调度方式
- **简单任务**（改配置、修 bug、小重构）：OpenClaw 直接 `exec` 执行
- **复杂任务**（新功能、跨模块修改）：调用 OpenCode 执行，由用户确认后执行
- **审查任务**：OpenClaw 不直接审查代码，提示用户用 OpenCode TUI 查看 diff

## 安全红线

- 不误改 `tradingagents.db`（生产数据库）
- 不误改 `eval_results/` 中的历史回测结果
- 不直接修改 `tradingagents/prompts/` 中的提示词模板（需走审批）
- 不删除 `logs/` 中的历史日志
- 测试数据不得写入生产数据库
- 不读取、解密、打印、提交任何模型 API Key 明文；只能使用 `HAS_KEY/NO_KEY` 级别的脱敏状态
- 除用户主动发起的分析/定时任务外，任何 live LLM API 调用前都要说明 provider、base_url、模型名和预计调用次数，并征得用户确认

## 技术栈

- **后端**：Python 3.10+, FastAPI, LangGraph, LangChain
- **前端**：React, TypeScript, Tailwind CSS
- **数据库**：SQLite
- **数据源**：AKShare, BaoStock, yfinance
- **模型**：OpenAI / Anthropic / 智谱 / DeepSeek / 小米 MiMo 等
