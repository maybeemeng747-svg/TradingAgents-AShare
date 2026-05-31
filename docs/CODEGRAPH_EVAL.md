# CodeGraph 评估报告

**评估时间**: 2026-05-31 15:10 CST
**评估人**: 主控AI

## 基本信息

| 项目 | 值 |
|------|-----|
| 工具 | [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph) |
| 版本 | 0.9.7 |
| 安装位置 | `/opt/homebrew/bin/codegraph` (npm -g) |
| 项目索引 | `TradingAgents-AShare/.codegraph/` (11.74 MB) |
| 后端 | node:sqlite (WAL mode) |
| 联网/API key | 不需要，100% 本地 |

## 支持语言

tree-sitter 解析，支持 7 种语言：

| 语言 | 本项目文件数 |
|------|------------|
| Python | 204 |
| TSX | 28 |
| TypeScript | 14 |
| YAML | 5 |
| JavaScript | 4 |

Python + TypeScript 完整支持 ✅

## 扫描结果

```
Files:     255
Nodes:     6,195
Edges:     12,658
DB Size:   11.74 MB
Time:      1.3s
```

### 节点类型分布

| Kind | 数量 |
|------|------|
| method | 2,126 |
| import | 1,340 |
| function | 1,265 |
| class | 562 |
| variable | 376 |
| file | 250 |
| interface | 123 |
| route | 66 |
| constant | 60 |
| property | 14 |
| type_alias | 13 |

## 核心命令

### 1. symbol 查询
```bash
codegraph query "TradingAgentsGraph"
# 返回：class TradingAgentsGraph 在 tradingagents/graph/trading_graph.py:51
# 包含所有相关方法和属性
```

### 2. caller 追踪（谁调用了 X）
```bash
codegraph callers "run_post_market_review"
# 返回 20 个调用者，全部在 tests/test_m007_post_market_review.py
```

### 3. callee 追踪（X 调用了谁）
```bash
codegraph callees "TradingAgentsGraph"
```

### 4. impact 分析（改 X 会影响什么）
```bash
codegraph impact "CandidatePerformance"
# 返回 66 个受影响的 symbol，包括类本身、方法、测试
```

### 5. affected 测试（改源文件影响哪些测试）
```bash
codegraph affected tradingagents/tradeflow/post_market_review.py
# 注意：当前版本返回 "No test files affected"，可能是已知限制
```

### 6. context 生成（为任务构建上下文）
```bash
codegraph context "how does the tradeflow pipeline work"
# 返回相关入口点、symbol、代码片段
```

## 是否适合纳入自动开发流程

### 适合的场景

1. **OpenCode 任务前**：`codegraph impact <symbol>` 了解改动影响范围
2. **PR review**：`codegraph callers <changed_fn>` 确认没有遗漏调用方
3. **测试覆盖检查**：`codegraph callers <fn>` 看哪些测试覆盖了该函数
4. **新任务上下文**：`codegraph context "<task description>"` 自动生成相关代码上下文

### 不适合的场景

1. **affected 测试发现**：当前版本对 Python 项目的测试发现有局限
2. **运行时依赖**：只做静态分析，不追踪运行时动态导入
3. **数据流分析**：不追踪变量值的流动，只追踪调用关系

## 风险点

1. **缓存文件**：`.codegraph/` 目录（11.74 MB）不应提交到 git
2. **增量同步**：`codegraph sync` 用于增量更新，但大规模重构后需要 `codegraph index` 重新全量索引
3. **版本锁定**：npm -g 安装，版本随 npm 更新，可能引入 breaking changes
4. **node 依赖**：需要 Node.js 运行时（项目已有）

## 下一步建议

1. **加入 .gitignore**：确保 `.codegraph/` 不被提交
2. **集成到 auto_dev_loop.sh**：在 OpenCode 任务前运行 `codegraph impact <target>` 输出影响范围
3. **集成到 codex-review**：review 时用 `codegraph callers <fn>` 验证调用方覆盖
4. **考虑 MCP 集成**：`codegraph install` 可以配置到 OpenCode/Claude Code，让 agent 直接查询

## 验证

- `git status`：仅 `.codegraph/` 未跟踪（已自含 .gitignore）
- 未修改任何业务代码
- 未读取/打印任何 API key
- 未写入 tradingagents.db
