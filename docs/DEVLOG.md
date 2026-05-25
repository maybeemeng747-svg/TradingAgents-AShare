# 修改日志

> 每次代码修改后必须更新此文件，保持连续性。

---

## 2026-05-24 | 挑选式同步 GitHub 上游修复

- **执行者**：Codex
- **任务**：检查 `KylinMountain/TradingAgents-AShare` 上游更新，挑选合入不覆盖本地 D/E/F/G 系列投研优化的修复
- **修改文件**：
  - `api/main.py` — 默认任务超时 `TA_JOB_TIMEOUT` 从 600s 调整为 1800s，保留智谱 Coding 专用 `TA_ZHIPU_CODING_JOB_TIMEOUT=2700`
  - `api/main.py` — `_ai_extract_symbol_and_date()` 与 streaming 版本新增 regex 兜底：LLM 失败、限流、返回空或返回无法解析的中文名时，优先保留用户输入中的明确股票代码
  - `frontend/src/components/DebateDrawer.tsx` — 辩论抽屉容器增加 `dark` class，确保暗色模式上下文生效
  - `frontend/src/components/DebateTimeline.tsx` — Markdown 内容显式使用 `text-slate-200`，避免深色背景文字不可读
  - `tests/test_stock_extract_fallback.py` — 新增股票识别兜底测试，覆盖 LLM 失败与中文名解析失败场景
- **未合入**：
  - 前端依赖小版本升级（react-router-dom/zustand/postcss 等）暂缓，避免与本地前端定制混在同一批
  - pytest lock 小版本升级暂缓，后续可单独维护依赖
- **验证**：
  - `pytest tests/test_stock_extract_fallback.py tests/test_api_smoke.py::TestChatCompletionsEndpoint -q`：6 passed
  - `python -m py_compile api/main.py`：通过
  - `npm run build`：通过（Vite chunk size warning 为既有体积提示）

---

## 2026-05-13 | E-001~E-004 基础设施修复

- **执行者**：OpenCode
- **任务**：TASK_BATCH_E.md — E 系列基础设施修复
- **修改文件**：
  - `api/main.py` — [E-001] 启动时读取 git commit hash 并缓存为 `_GIT_COMMIT_SHORT`，记录 `_BACKEND_START_TIME`
  - `tradingagents/agents/utils/readiness_score.py` — [E-001] `format_readiness_score` 追加系统版本区块；[E-002] 新增 `validate_stock_name` 名称校验函数 + `get_strong_action_gate` 增加 `name_mismatch` 和 `no_execution_zone_conflict` 参数 + `calculate_risk_level`/`calculate_buy_level` Level 4 降级；[E-003] 扩展止损关键词(`止损红线/止损位/止损价/清仓线/止损线`) + 入场/减仓区间冲突检测 + `has_stop_loss`/`execution_zone_conflict` 信号；[E-004] `infer_evidence_statuses` 支持从 `raw_evidence` 结构化数据直接读取，fallback 到文本正则
  - `tradingagents/agents/managers/risk_manager.py` — [E-002] 集成 `validate_stock_name`；[E-003] 传递 `execution_zone_conflict` 到 gate；[E-004] 传递 `raw_evidence` 到 `infer_evidence_statuses`
  - `tradingagents/graph/data_collector.py` — [E-004] 新增 `build_raw_evidence()` 方法从缓存池构建证据摘要
  - `tradingagents/graph/trading_graph.py` — [E-004] `propagate` 和 `propagate_async` 中将 `raw_evidence` 存入 state metadata
  - `tests/test_readiness_score.py` — 新增 26 个测试覆盖 E-001~E-004
- **验证**：`pytest tests/test_readiness_score.py` 100 passed；`pytest -q` 294 passed, 4 pre-existing SQLite failures

---

## 2026-05-13 | 自选股拖拽排序功能

- **执行者**：OpenCode
- **任务**：TASK_DRAG_SORT.md — 自选股列表拖拽排序
- **修改文件**：
  - `api/services/watchlist_service.py` — 新增 `reorder_watchlist()` 函数，批量更新 sort_order，含 id 归属校验和唯一性校验
  - `api/main.py` — 新增 `PUT /v1/watchlist/reorder` 路由
  - `frontend/src/services/api.ts` — 新增 `reorderWatchlist()` API 方法
  - `frontend/src/pages/Portfolio.tsx` — 用 @dnd-kit 实现拖拽排序：SortableWatchlistItem 组件、DndContext 包裹、乐观更新+失败回滚
  - `frontend/package.json` — 新增 @dnd-kit/core、@dnd-kit/sortable、@dnd-kit/utilities 依赖
- **要点**：
  - 拖拽手柄为左侧 ⠿ (GripVertical) 图标，避免误触
  - PointerSensor 需移动 5px 才激活；TouchSensor 长按 200ms 触发
  - 拖拽中半透明+阴影效果
  - 乐观更新本地 state，API 失败时回滚并提示
- **验证**：TypeScript 编译通过，`git diff --check` 无报错

---

## 2026-05-13 | D-001~D-004 修复：真降级、真证据、真信号、持仓未知语义

- **执行者**：OpenCode
- **任务**：TASK_BATCH_FIX.md 全部 P1/P2/P3 修复
- **修改文件**：
  - `tradingagents/agents/utils/readiness_score.py` — 新增 3 个函数 + 修改 1 个函数：
    - `sanitize_forbidden_strong_actions()` — gate 未通过时真正移除/替换强动作文本，而非仅追加警告
    - `infer_evidence_statuses()` — 基于报告文本正则匹配推断证据状态，不再因报告存在就标 HAS_DATA
    - `extract_execution_signals()` — 从报告文本保守提取 14 项交易信号，传入 Buy/Risk/Opportunity 计算
    - `get_strong_action_gate()` — 新增 `contains_strong_action` 参数，持仓未知+无强动作时 gate 通过
  - `tradingagents/agents/managers/risk_manager.py` — 集成 3 个新函数，替换旧的 evidence 硬编码和信号缺失逻辑
  - `tests/test_readiness_score.py` — 从 32 项测试扩展至 74 项，覆盖全部 P1/P2 测试要求
  - `docs/DEVLOG.md` — 合并重复 D-001~D-004 记录，补充本次修复
- **测试结果**：
  - `tests/test_readiness_score.py`: 74 passed
  - `pytest -q`: 270 passed, 9 skipped, 2 failed（pre-existing SQLite thread issue）
- **关键变更**：
  - P1-真降级：gate 未通过时 `立即清仓`/`重仓买入`/`追涨` 等强动作被替换为中性表达，原关键词不再出现在最终报告
  - P1-真证据：evidence 不再因报告字符串存在就标 HAS_DATA，需匹配 OHLC 表格/成交量值/换手率等具体字段；龙虎榜根据文本判断状态；融资融券保持 NOT_QUERIED
  - P1-真信号：从报告文本提取跌破支撑/主力资金流向/放量破位/趋势确认/量能健康等 14 项信号，传入 Buy/Risk Level 和 Opportunity Score 计算
  - P2-持仓未知语义：gate 只在持仓未知+包含强动作时才失败，观察/中性报告不会因持仓未知被阻断

---

## 2026-05-13 | D-001~D-004: 修正评分、强动作门禁、双等级输出、机会评分

- **执行者**：OpenCode
- **任务**：TASK_BATCH.md 全部 4 个子任务（初始实现）
- **修改文件**：
  - `tradingagents/agents/utils/readiness_score.py` — 大幅扩展：新增 source_coverage、evidence_coverage、get_strong_action_gate、calculate_risk_level、calculate_buy_level、calculate_opportunity_score、format_execution_block
  - `tradingagents/agents/managers/risk_manager.py` — 传入全部 8 项数据源、集成新评分模块、报告末尾追加执行等级区块
  - `tests/test_readiness_score.py` — 新建，32 个测试覆盖 D-001~D-004 全部 8 项测试要求
- **测试结果**：32/32 passed（test_readiness_score.py），全项目 228 passed（2 个 pre-existing SQLite 线程安全失败不相关）
- **关键变更**：
  - D-001: risk_manager 现传入 smart_money/volume_price/user_context/position 全部 8 项，不再卡在 50 分
  - D-002: 新增 EvidenceStatus 区分"正常无数据"与"查询失败"；强动作门禁 7 项条件
  - D-003: Risk Level 0-4 / Buy Level 0-4 双等级；Risk/Buy Level 4 需全部条件满足且 coverage>=85；持仓未知 Buy Level 上限 2
  - D-004: Opportunity Score 0-100，5 维加权，风险事件降权；不单独决定买入
- **风险点**：evidence_coverage 当前部分字段用默认值（margin_trading=not_queried），后续可接入实际数据源

---

## 2026-05-10 | 报告质量防线实现（C-001~C-008 全部完成）

- **执行者**：OpenClaw（主控AI）
- **任务**：实现 8 项报告质量防线
- **修改/新增文件**：
  - `tradingagents/agents/utils/trade_actions.py` — 新建，C-004 动作枚举
  - `tradingagents/agents/utils/delta_check.py` — 新建，C-005 结论翻转检测
  - `tradingagents/agents/utils/event_risk_gate.py` — 新建，C-007 事件风控门禁
  - `tradingagents/agents/utils/financial_validator.py` — 新建，C-006 财务异常检测
  - `tradingagents/agents/utils/readiness_score.py` — 新建，C-008 报告质量评分
  - `tradingagents/default_config.py` — 修改，C-002 account_capability
  - `tradingagents/agents/managers/research_manager.py` — 修改，C-001 position_validation_gate
  - `tradingagents/agents/researchers/bear_researcher.py` — 修改，C-003 short_filter
  - `docs/TASKS.md` — 更新，C-001~C-008 任务详情
- **Commits**：
  - `14e8e3d` — Phase 1 (C-001~C-004)
  - `ed73926` — Phase 2+3 (C-005~C-008)
  - `13dc562` — C-001 集成到 risk_manager.py
  - `4231144` — C-005 集成到 risk_manager.py
  - `2959a16` — C-007 集成到 risk_manager.py
  - `e314d8d` — C-006 + C-008 集成到 risk_manager.py
- **测试结果**：所有模块 import 成功
- **风险点**：C-006 的财务数据目前传空值（需要从 fundamentals_report 中提取）
- **下一步**：
  - [ ] 端到端测试：运行一次完整分析验证防线生效
  - [ ] C-006 需要从 fundamentals_report 中提取实际财务数据

---

## 2026-05-09 | 搭建项目协作工作流

- **执行者**：OpenClaw（主控AI）
- **任务**：创建项目协作基础设施，建立多工具协作规范
- **修改文件**：
  - `AGENTS.md` — 新建，项目协作规则、安全红线、执行流程
  - `docs/project-overview.md` — 新建，项目背景 + 架构 + 目录结构
  - `docs/TASKS.md` — 新建，任务池
  - `docs/DECISIONS.md` — 新建，架构决策记录
  - `docs/DEVLOG.md` — 新建，本文件
  - `docs/CODE_REVIEW.md` — 新建，代码审查清单
  - `.codex/config.toml` — 已弃用（Codex CLI → OpenCode 替换）
- **测试结果**：不涉及代码修改
- **风险点**：无
- **下一步**：
  - [ ] B-001: 接入小米 MiMo 模型
  - [ ] B-002: 定时任务与 OpenClaw 联动

---

## 2026-05-11 — 报告质量改进（12项清单执行）

### P0 — Prompt 层改动（已完成）
1. 所有分析师 prompt 加「缺字段不输出对应结论」规则（市场/新闻/舆情/基本面/量价/smart_money）
2. 所有分析师 prompt 加「引用数据必须附原始值」规则
3. 所有分析师 prompt 加「所有数字标注来源，禁止 LLM 推算值不加标注」规则
4. 基本面 prompt 补充：负债端字段缺失时禁止输出偿债能力结论

### P1 — 小改动（已完成）
5. 资金流查询从 tail(5) 扩到 tail(20)，smart_money analyst 数据窗口描述同步更新
6. 研究经理 prompt 加冲突摘要表：各模块原始方向 + 核心论据 + 采纳/舍弃 + 理由
7. 数据完整度与建议动作挂钩：<50% 禁止买入/加仓，50-79% 禁止加仓，≥80% 全部允许（readiness_score.py 新增 get_allowed_actions + 格式化输出包含允许/禁止动作）
8. 风控 prompt 加「上游 verdict 保留规则」：禁止加仓时上游建议保留原样，仅冲突表标注分歧

### P2 — 代码层改动（已完成 2/3）
9. 最终报告加数据源可用性 checklist（✅/❌ 标记）— risk_manager.py
10. 资产负债表字段映射补全：get_balance_sheet 新增 _select_balance_sheet_cols 选出 23 个核心字段（资产端+负债端+权益端），_shrink_table 不再截断负债端 — cn_akshare_provider.py
11. 龙虎榜改为按需触发：get_lhb_detail 新增 force 参数，默认不查询；smart_money_analyst 新增 _check_fund_flow_anomaly 检测资金异动，仅异动时 force=True 查询；data_collector 批量预取时 force=False — cn_akshare_provider.py / game_theory_tools.py / smart_money_analyst.py / data_collector.py

### 待做 — P3 架构层
12. 打通 investment-controller 与 TA 持仓数据
13. 调度器 14:30 任务逻辑修正
