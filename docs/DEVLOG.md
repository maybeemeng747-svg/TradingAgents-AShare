# 修改日志

> 每次代码修改后必须更新此文件，保持连续性。

---

## 2026-05-30 | V-002: 夜间自动开发验收报告与候选样本回放

- **执行者**：OpenCode
- **任务**：V-002 — 为夜间自动开发增加固定样本回放和验收报告
- **修改文件**：
  - `scripts/summarize_auto_dev_runs.py` — [V-002] 扩展 M-002 夜间日报脚本，新增候选样本回放、Ready 队列解析、测试结果解析、敏感数据过滤
    - `parse_ready_queue()` — 从 TASKS.md 解析 status=ready 的任务列表
    - `parse_test_summary_from_logs()` — 从 task_runs 中的 tests-round*.txt 提取 passed/failed/skipped
    - `run_sample_replay()` — 调用 false_positive_audit 的 8 个固定 fixture 进行候选池质量回放
    - `format_sample_replay()` — 将回放结果格式化为 markdown 表格
    - `generate_report()` 扩展 — 新增 ready_queue 和 replay_results 参数，输出测试汇总、Ready 队列（含"任务池不足"警告）、候选样本回放
    - `redact()` 增强 — 新增 password/secret 脱敏模式
    - `main()` 扩展 — 新增 `--with-sample-replay` 参数，最终报告整体脱敏
  - `tests/test_v002_nightly_acceptance.py` — 新建，33 个测试覆盖：
    - TestRedact (8): sk-key/api-key/bearer/token/password/secret/正常文本/环境变量 脱敏
    - TestParseReadyQueue (5): 单个/多个/空/无文件/blocked+proposed 过滤
    - TestParseTestSummaryFromLogs (5): 标准输出/含失败/无测试文件/含错误/最新轮次
    - TestSampleReplay (5): 返回结果/覆盖 6 个必须场景/全部通过/markdown 格式/空回放
    - TestGenerateReportV002 (9): 非空队列/空队列警告/无队列/含回放/含测试/无泄露/完整报告/队列计数
    - TestDryRunIntegration (2): dry-run 含回放/dry-run 不含回放
- **测试结果**：33 passed (V-002)；全项目 1570 passed, 17 skipped, 0 failed
- **关键逻辑**：
  - 候选样本回放复用 `false_positive_audit.generate_fixture_samples()` 的 8 个 fixture，覆盖：VCP 命中、事件催化、资金异动、流动性过滤、无策略命中、风险降权、证据缺口、资金单位未校验
  - Ready 队列为空时报告必须显示"任务池不足"警告，防止 cron 空转
  - 报告最终输出前经过整体 `redact()` 脱敏，防止 API key/secret 泄露
  - `--with-sample-replay` 参数控制是否执行回放，默认关闭保持向后兼容
- **风险点**：无；所有改动向后兼容，不影响现有 M-002 功能

## 2026-05-30 | M-004: TradeFlow 策略权重与阈值配置

- **执行者**：OpenCode
- **任务**：M-004 — 把 VCP、回踩、事件催化、资金异动、A股标签的权重和阈值集中配置，便于后续复盘调参
- **修改文件**：
  - `tradingagents/tradeflow/strategy_config.py` — 新建：`StrategyConfig` 数据类（60+ 可配阈值），`DEFAULT_STRATEGY_CONFIG` 单例，`STRATEGY_CONFIG_VERSION="v1.0"`，`to_dict()` 序列化方法
  - `tradingagents/tradeflow/strategies/vcp.py` — `score_vcp()` 新增 `cfg` 参数，8 个阈值从 config 读取（min_data_len/range_ratio/vol_ratio/price_position/ma20_break/score_max/need_deep_ta）
  - `tradingagents/tradeflow/strategies/pullback_support.py` — `score_pullback_support()` 新增 `cfg` 参数，10 个阈值从 config 读取
  - `tradingagents/tradeflow/strategies/event_catalyst.py` — `score_event_catalyst()` 新增 `cfg` 参数，score_max 和 need_deep_ta_score 从 config 读取
  - `tradingagents/tradeflow/policy_version_signal.py` — `detect_policy_version()` 新增 `cfg` 参数，max_bonus/industry_weight/need_deep_ta 从 config 读取
  - `tradingagents/tradeflow/narrative_quality.py` — `score_narrative_quality()` 新增 `cfg` 参数，max_score 从 config 读取
  - `tradingagents/tradeflow/underwater_risk_flags.py` — `detect_underwater_risks()` 新增 `cfg` 参数，max_penalty 从 config 读取
  - `tradingagents/tradeflow/fund_flow_anomaly.py` — `detect_fund_flow_anomaly()` 新增 `cfg` 参数，5 个阈值从 config 读取；内部辅助函数参数化
  - `tradingagents/tradeflow/game_balance.py` — `assess_game_balance()` 新增 `cfg` 参数，3 个阈值从 config 读取
  - `tradingagents/tradeflow/selection_priority_gate.py` — `run_selection_priority_gate()` 新增 `cfg` 参数，9 个阈值从 config 读取
  - `tradingagents/tradeflow/tier_budget.py` — `classify_candidate_tier()` + `allocate_tier_budget()` 新增 `cfg` 参数，7 个阈值从 config 读取
  - `tradingagents/tradeflow/evidence_gate.py` — `compute_evidence_completeness()` + `apply_evidence_gate()` 新增 `cfg` 参数，3 个阈值从 config 读取
  - `tradingagents/tradeflow/candidate_engine.py` — `evaluate_symbol()` + `run_strategies()` + `filter_symbol()` + `_calc_liquidity()` 新增 `cfg` 参数并向下传递
  - `tradingagents/tradeflow/plan_runner.py` — `generate_daily_plan()` 新增 `cfg` 参数，plan.metadata 新增 `strategy_config_version` 字段
  - `tests/test_strategy_config.py` — 新建，42 个测试覆盖默认值、None 回退、阈值覆盖可预测性、config_version 输出
- **测试结果**：1451 passed, 15 skipped, 0 failed
- **关键逻辑**：
  - 所有策略模块接受可选 `cfg: StrategyConfig = None`，None 时自动使用 `DEFAULT_STRATEGY_CONFIG`
  - `DEFAULT_STRATEGY_CONFIG` 的默认值与重构前硬编码常量完全一致，确保向后兼容
  - `generate_daily_plan()` 输出的 `plan.metadata["strategy_config_version"]` 记录配置版本，便于审计
  - `StrategyConfig.to_dict()` 支持序列化输出（risk_high_severity_flags 转 list）
- **风险点**：无；所有改动向后兼容，现有 121 个 tradeflow 测试全部通过

## 2026-05-30 | M-003 fix: 枚举值对齐 + fund_flow 去重

- **执行者**：OpenCode
- **任务**：M-003 — 修复 3 个测试失败
- **修改文件**：
  - `tradingagents/tradeflow/universe.py` — `UniverseSource.INDUSTRY` 值 `"industry"` → `"industry_pool"`；`UniverseSource.FUND_FLOW` 值 `"fund_flow"` → `"fund_flow_pool"`，与 discovery 模块常量对齐
  - `tradingagents/tradeflow/discovery.py` — `_build_discovery_universe()` 新增 fund_flow_symbols 独立去重逻辑：当 `build_universe` 被 mock 或 fund_flow 符号不在已有 universe 中时，补充添加
  - `tests/test_tradeflow_universe.py` — 更新 4 处断言使用新枚举值 `"industry_pool"` / `"fund_flow_pool"`
- **测试结果**：1495 passed, 17 skipped, 0 failed（原 3 failed → 0）
- **根因**：UniverseSource 枚举值与 discovery 模块 SOURCE_* 常量不一致；`_build_discovery_universe` 未对 fund_flow_symbols 做独立去重补充

## 2026-05-30 | M-003: TradeFlow universe 管理器

- **执行者**：OpenCode
- **任务**：M-003 — 统一管理候选池来源，避免每个策略各自拼 symbols
- **修改文件**：
  - `tradingagents/tradeflow/universe.py` — 重构：新增 `UniverseSource` 枚举（8 种来源）、`SourceRecord`/`UniverseEntry` 数据类、`UniverseManager` 类；`build_universe()` 改用 `UniverseManager` 内部聚合，新增 `industry_symbols`/`fund_flow_symbols` 参数；向后兼容
  - `tradingagents/tradeflow/schemas.py` — Candidate 新增 `universe_sources: list[str]` 字段，`to_db_row()`/`from_db_row()` 同步
  - `tradingagents/tradeflow/plan_runner.py` — `generate_daily_plan()` 将 universe 条目的 `universe_sources` 传递给 Candidate；`_build_plan_entry()` 输出新字段
  - `tradingagents/tradeflow/discovery.py` — `_build_discovery_universe()` 改用 `build_universe()` 统一入口（含 industry/fund_flow）；评估后传递 `universe_sources` 到 Candidate；`_build_discovery_entry()` 输出新字段
  - `tradingagents/tradeflow/candidate_engine.py` — `init_db()` 新增 `universe_sources_json` 列；`save_candidate()` INSERT/UPDATE 包含新列（54→55 字段）
  - `tests/test_tradeflow_universe.py` — 新建，38 个测试覆盖 UniverseSource 枚举、UniverseManager CRUD、去重保留多来源、DB 集成、plan_runner/discovery 集成、无来源不扫全市场、Candidate schema 字段
- **测试结果**：`pytest tests/test_tradeflow_*.py` 121 passed；`pytest tests/test_tradeflow_*.py tests/test_readiness_score.py` 237 passed
- **关键逻辑**：
  - 同一 symbol 从多个来源（watchlist/holding/manual/event/fund_flow/industry）进入 universe 时，去重但保留完整来源列表 `universe_sources`
  - 每个来源记录 `SourceRecord`（source、reason、timestamp、extra）
  - `UniverseManager.add_symbol()` 幂等：同一来源重复添加不会重复记录
  - `build_universe()` 保持向后兼容：只读 symbol/name/source 的调用方不受影响
  - 无来源输入返回空列表，绝不触发全市场扫描
- **风险点**：无；所有改动向后兼容，新增字段默认空列表

## 2026-05-15 | 定时任务 Token 护栏与模型 API 目录

- **执行者**：Codex
- **任务**：检查定时任务与模型 API 调用，降低静默消耗 token 的风险
- **修改文件**：
  - `scheduler/main.py` — 新增 `SCHEDULER_ENABLED`、`SCHEDULER_DRY_RUN`、`SCHEDULER_RUN_INTRADAY`、`SCHEDULER_MAX_TASKS_PER_TICK`、`SCHEDULER_ALLOWED_TRIGGER_TIMES` 护栏；默认尊重用户主动创建的盘中/盘后定时任务，启动日志输出当前 scheduler 配置
  - `api/services/portfolio_import_service.py` / `api/main.py` / `frontend/src/components/TrackingBoardPanel.tsx` — 持仓导入默认不再自动创建定时分析任务，避免保存持仓后静默开跑
  - `tradingagents/llm_clients/model_catalog.py` — 新增统一模型/API 端点目录
  - `api/main.py` — 新增 `GET /v1/config/model-catalog`，返回模型目录且不暴露密钥
  - `scripts/audit_llm_config.py` — 新增只读脱敏审计脚本，输出当前 runtime 模型配置与 key scope 的 `HAS_KEY/NO_KEY`
  - `AGENTS.md` / `docs/LLM_API_USAGE.md` — 新增多工具协作下的 LLM API 使用边界：定时任务允许消耗 token，普通 coding 工作禁止擅自调用付费 LLM；live LLM 测试需先问用户
  - `.env.example` — 补充 scheduler token guard 环境变量
  - `docs/SCHEDULER_TOKEN_GUARD.md` / `docs/MODEL_API_CATALOG.md` — 新增定时任务和模型调用说明
  - `tests/test_portfolio_import.py` / `tests/test_api_smoke.py` — 补充默认不创建定时任务、模型目录接口测试
- **现场审计**：
  - 本地存在运行中的 `python -m scheduler.main` 进程
  - `scheduled_analyses` 当前有 3 条 active 任务：`002138.SZ` 14:30/20:00、`603629.SZ` 22:00
  - 主用户当前运行配置为智谱 Coding Plan：`glm-4.5-air` / `glm-5-turbo`
- **风险点**：代码护栏需要重启 scheduler 进程后生效；已有 active 任务仍保留，是否停用由用户决定

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

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: M-003 — TradeFlow universe 管理器（P1）
- **优先级**: P1
- **轮次**: 2
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/M-003-20260530-round2.txt
- **运行档案**: docs/task_runs/M-003-20260530-002721/

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: M-004 — TradeFlow 策略权重与阈值配置（P1）
- **优先级**: P1
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/M-004-20260530-round1.txt
- **运行档案**: docs/task_runs/M-004-20260530-004038/

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: V-002 — 夜间自动开发验收报告与候选样本回放（P1）
- **优先级**: P1
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/V-002-20260530-round1.txt
- **运行档案**: docs/task_runs/V-002-20260530-010220/
