# 修改日志

> 每次代码修改后必须更新此文件，保持连续性。

---

## 2026-05-29 | S-001 政策版本因子进入 TradeFlow 候选池

- **执行者**：OpenCode (glm-5.1)
- **任务**：为 TradeFlow 增加政策版本因子，识别 A 股市场政策/产业方向，让候选池从"技术形态先行"升级为"政策版本入池 + 技术确认"
- **修改文件**：
  - `tradingagents/tradeflow/policy_version_signal.py`（新增，~140行）— [S-001] policy_version_signal
    - `detect_policy_version(event_texts, industry_tags)` — 检测事件文本中的政策版本关键词
    - `PolicyVersionResult` — 输出 `policy_tags`、`version_score`（0-30分）、`policy_evidence_refs`
    - 14 个政策版本主题：新质生产力、算力、低空经济、机器人、出海、中特估、国产替代、并购重组、国企改革、半导体、新能源、人工智能、数据要素、军工
    - 仅事件文本命中时给完整权重；行业标签命中给 30% 权重；无原始文本证据时不得加分
    - 累计加分上限 30 分（`MAX_POLICY_BONUS`）
  - `tradingagents/tradeflow/schemas.py` — [S-001]
    - `Candidate` 新增 `policy_tags`、`version_score`、`policy_evidence_refs` 字段
    - `STRATEGY_POLICY_VERSION = "POLICY_VERSION"` 策略常量，加入 `ALL_STRATEGIES`
    - `to_db_row()` / `from_db_row()` 支持新字段持久化
    - `DailyPlan.render_text()` 展示政策标签和证据摘要
  - `tradingagents/tradeflow/candidate_engine.py` — [S-001]
    - `evaluate_symbol()` 在 N-004 信号标签之后调用 `detect_policy_version()`
    - 政策版本命中时加分、加入 `strategy_tags`、写入 `evidence.policy_version`
    - `version_score >= 15` 时设置 `need_deep_ta=True`
    - `init_db()` 新增 `policy_tags_json`、`version_score`、`policy_evidence_refs_json` 列迁移
    - `save_candidate()` INSERT/UPDATE 包含新字段
  - `tradingagents/tradeflow/plan_runner.py` — [S-001]
    - `_build_plan_entry()` 输出 `policy_tags`、`version_score`、`policy_evidence_refs`
  - `tests/test_s001_policy_version_signal.py`（新增，~280行）— 35 个测试
- **关键逻辑**：
  1. 政策版本信号作为 bonus 层叠加在 VCP/Pullback/Event 之上，不破坏现有策略
  2. 无事件文本证据时 `version_score` 恒为 0，不凭空加分
  3. 不同股票各自消费自己的事件文本，按 symbol 隔离
  4. Daily Plan 展示政策标签和证据摘要，动作仍为 OBSERVE/NEED_DEEP_TA
  5. 高政策版本得分（≥15）自动提升 need_deep_ta 优先级
- **标记**：`# [S-001] policy_version_signal`
- **测试结果**：
  - S-001 专项测试：35 passed
  - TradeFlow 全量测试：118 passed
  - 扩展回归测试：370 passed, 2 skipped
- **验收补修**：
  - commit `cf7d928` 修复 `event_overrides` 路径漏传事件标题到政策版本检测的问题
  - 验证："低空经济政策支持" 可正确生成 `policy_tags=["低空经济"]` 和 `POLICY_VERSION` 策略标签

---

## 2026-05-29 | TradeFlow 选股策略任务池重排

- **执行者**：Codex
- **任务**：根据“先选对股票，再做深度 TA”的策略目标，重排 TradeFlow 后续开发任务池
- **修改文件**：
  - `docs/TASKS.md` — 新增 `S-001` 至 `S-005` 选股策略核心任务，并把当前优先队列调整为政策版本、叙事质量、水下风险、博弈平衡优先
  - `docs/AUTO_DEV_PLAN.md` — 同步自动开发路线，把 P1 当前最高业务优先级调整为候选池质量提升
- **策略方向**：
  - 候选池从“技术形态先行”升级为“政策版本入池 + 叙事质量评分 + 水下风险降权 + 技术/资金确认”
  - 小范围 Discovery、资金异动池、Universe 管理器和策略权重配置排在 S 系列之后，避免先扩大扫描范围再产生低质量候选
  - `need_deep_ta` 后续应优先给到政策/事件/技术/资金多类信号共振且风险可解释的股票
- **注意**：
  - 本次只调整任务池和自动开发路线，不改业务代码

## 2026-05-29 | M-002 修复 portfolio_import 测试 SQLite 线程安全问题

- **执行者**：OpenCode
- **任务**：M-002 — 修复 `test_scheduled_job_uses_imported_position_context` 和 `test_scheduled_job_marks_failed_when_underlying_job_fails` 两个测试因 SQLite 线程安全导致的失败
- **根因**：`tests/test_portfolio_import.py` 的 `db` fixture 创建内存 SQLite 引擎时未设置 `check_same_thread=False`，而 `_run_scheduled_job` 内部通过 `asyncio.to_thread()` 在工作线程中访问 DB session，触发 `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`
- **修改文件**：
  - `tests/test_portfolio_import.py`（1 行改动）— `db` fixture 的 `create_engine` 添加 `connect_args={"check_same_thread": False}`
- **标记**：`# [M-002] fix_sqlite_thread_safety`
- **验证**：`pytest tests/test_portfolio_import.py -xvs` 12/12 passed

---

## 2026-05-29 | M-002 任务运行档案索引与夜间日报聚合

- **执行者**：OpenCode
- **任务**：M-002 — 把 `docs/task_runs/` 中的运行档案汇总成可读索引，生成夜间日报
- **修改文件**：
  - `scripts/summarize_auto_dev_runs.py`（新增，~300行）— 扫描 task_runs 目录，解析 task.md / summary.md，聚合 git log / reviews，生成 `docs/auto_dev_reports/YYYY-MM-DD.md` 日报；支持 `--date`、`--dry-run`、`--repo-dir` 参数；内置 API key 脱敏
  - `tests/test_m002_summarize_runs.py`（新增，~280行）— 27 个测试覆盖：密钥脱敏、空目录扫描、日期过滤、PASS/NEEDS_HUMAN 状态解析、trace 文件收集、commit 展示、风险标记、dry-run vs 写文件、reviews 聚合与脱敏
  - `docs/auto_dev_reports/` — 新建日报输出目录
- **关键逻辑**：
  1. `scan_task_runs()` 按 `--date` 过滤目录名中的日期部分，解析 `task.md` 和 `summary.md` 提取任务元数据
  2. `get_git_log_for_date()` 调用 `git log --after --before` 获取当日提交
  3. `collect_reviews()` 扫描 `docs/reviews/` 并做脱敏预览
  4. `generate_report()` 生成 Markdown 日报，包含总览表、任务详情（可折叠 summary）、提交记录、Codex Reviews、风险提示和下一步
  5. `redact()` 使用正则匹配 sk-key、Bearer token、api_key 等敏感信息并替换为 `[REDACTED]`
- **标记**：`# [M-002] auto_dev_report_index`
- **验证**：
  - `python scripts/summarize_auto_dev_runs.py --date 2026-05-28 --dry-run` 可运行并输出日报
  - `pytest tests/test_m002_summarize_runs.py -q` 全部通过

---

## 2026-05-29 | INF-001 自动开发领取锁与状态收口修复

- **执行者**：Codex
- **任务**：收口 2026-05-28 夜间 `INF-001` 自动开发失败，修复 `auto_dev_loop.sh` 失败路径和任务状态不同步问题
- **修改文件**：
  - `scripts/auto_dev_loop.sh` — 新增 `.auto_dev.lock` 目录锁、`update_task_status()` 状态流转函数；非 dry-run 领取后标记 `in_progress`，失败标记 `blocked — NEEDS_HUMAN`，通过后用独立文档提交写入准确 commit hash；修复 `OPENCODE_EXIT` 在中文括号旁触发的 `set -u` 变量展开问题
  - `tests/test_auto_dev_loop_static.py` — 新增脚本语法、dry-run 不改 `TASKS.md`、锁与安全变量展开的静态测试
  - `docs/TASKS.md` — 将 `INF-001` 标记为 Codex 修复中，将已完成的 `N-005` 对齐为 `done — commit 4ec69ec / dd717ca`
  - `docs/AUTO_DEV_PLAN.md` — 同步 `INF-001` / `N-005` 当前状态
  - `docs/task_runs/INF-001-20260528-200320/summary.md` — 补齐失败运行档案 summary
- **运行档案**：`docs/task_runs/INF-001-20260528-200320/`
- **标记**：`# [INF-001] task_claim_lock`

## 2026-05-28 | N-003 cn_astock raw_evidence 溯源接入

- **执行者**：OpenCode
- **任务**：当 `cn_astock` 作为 fallback 数据源时，`raw_evidence.stock_data.vendor` 应反映实际数据源而非硬编码的 "akshare"
- **修改文件**：
  - `tradingagents/dataflows/interface.py` — 新增 `_last_hit_vendor` 模块级字典和 `get_last_hit_vendor()` 函数；在 `route_to_vendor()` 成功 hit 时记录 vendor
  - `tradingagents/graph/data_collector.py` — `build_raw_evidence()` 中 `stock_data` 的 vendor 改为从 `get_last_hit_vendor("get_stock_data")` 读取实际值
  - `tests/test_raw_evidence_vendor.py` — 新增测试：akshare hit / cn_astock fallback / 无追踪 / realtime patch 覆盖 / 非 stock_data 保持默认
- **标记**：`# [N-003] cn_astock_raw_evidence`

---

## 2026-05-28 | 框架任务池与自动领取队列补齐

- **执行者**：Codex
- **任务**：根据当前开发进度补齐长期任务池，让夜间自动开发链路在跑通后有稳定任务来源，并明确领取规则和优先级
- **修改文件**：
  - `docs/TASKS.md` — 新增自动领取规则、当前优先队列、`M/INF` 框架路线总任务池
  - `docs/TASKS.md` — `N-003` 标记为 `done — commit dd63bca`
  - `docs/TASKS.md` — `N-004` 标记为 `done — commit 7fb535f`
  - `docs/TASKS.md` — `T-001` 标记为已由 `b5131cd` + `3b232a3` 完成
  - `docs/AUTO_DEV_PLAN.md` — 同步自动开发优先级，加入 `INF-001/M-002/M-008/M-003~M-010` 路线
- **任务设计重点**：
  - 自动开发先补任务领取锁，避免并发抢同一任务
  - 当前队列推进到 `INF-001`、`N-005`、`M-002`、`M-008`、`T-002/T-003`
  - 后续路线覆盖数据源健康、Universe 管理、策略配置、盘中 Observe、TA 调度、盘后 Review、前端面板和通知链路
- **注意**：
  - 本次只提交任务池/路线文档，不纳入当前未提交业务代码 diff

---

## 2026-05-28 | 自动开发时间调整为夜间

- **执行者**：Codex
- **任务**：将 OpenClaw 自动开发从白天 10:30 调整到晚上 20:00，利用夜间较低 token 成本，并让次日白天用于人工 review、决策和协作开发
- **修改文件**：
  - `docs/AUTO_DEV_PLAN.md` — 固定节奏改为每天 20:00 自动开发，次日白天 review
- **自动任务**：
  - Codex App automation `tradingagents-ashare` 已更新为工作日 20:00 触发
- **注意**：
  - 当前存在与本次无关的未提交代码改动，未纳入本次文档提交

---

## 2026-05-28 | 自动开发任务运行档案

- **执行者**：Codex
- **任务**：补齐自动开发链路的可回查审计记录，覆盖 OpenCode 领取任务、开发输出、测试、Codex review、最终 summary
- **修改文件**：
  - `scripts/auto_dev_loop.sh` — 每次非 dry-run 创建 `docs/task_runs/<TASK_ID>-YYYYMMDD-HHMMSS/`，保存 task、prompt、OpenCode 输出、测试输出、Codex review、summary；持久化日志前做基础 API key 脱敏
  - `scripts/auto_dev_loop.sh` — 工作区不干净时不再忽略 untracked docs，避免失败日志被下一轮误带入提交
  - `docs/AUTO_DEV_PLAN.md` — 新增任务运行日志与审计链路规范，明确 Codex 审核和任务布置职责
- **规则**：
  - 通过任务：运行档案随代码和文档一起提交
  - 失败任务：不自动提交，留下运行档案和 DEVLOG，下一轮自动开发停住等人工处理
- **任务池收口**：
  - `N-002` 已由 commit `a0770bb` 完成，补充标记为 `done`
  - `N-002` 完成早于本次运行档案机制，未伪造历史 `task_runs`

---

## 2026-05-28 | N-001 事件源接入候选扫描

- **执行者**：OpenCode
- **任务**：将 `event_source.fetch_daily_events()` 集成到候选扫描流程，使公告/回购/评级事件自动进入候选池
- **修改文件**：
  - `tradingagents/tradeflow/universe.py` — `build_universe()` 新增 `event_symbols` 参数，event-discovered symbols 以 `source="event_source"` 进入 universe
  - `tradingagents/tradeflow/plan_runner.py` — `generate_daily_plan()` 新增 `use_event_source=False` 参数，启用后调用 `fetch_daily_events()` 并将每个 symbol 自身的事件标题作为 `news_texts` 传入 `evaluate_symbol()`
  - `tests/test_event_source_integration.py` — 新增 13 个集成测试，覆盖 universe 扩展、事件隔离、默认关闭、空事件等场景
- **标记**：`# [N-001] event_source_plan_integration`
- **测试**：121 tests passed

---

## 2026-05-28 | V-001 验收任务延后

- **执行者**：Codex
- **任务**：调整明日自动开发顺序，避免在 `cn_astock` fallback / raw_evidence 接入前提前做 600584 端到端验收
- **修改文件**：
  - `docs/TASKS.md` — 将 `V-001` 从 `ready` 改回 `blocked`
  - `docs/TASKS.md` — `V-001` 前置条件补充 `N-002`、`N-003`
- **原因**：
  - 明日自动开发应先吸收 Simon 数据源实践，完成 `N-002/N-003` 后再验收 600584

---

## 2026-05-28 | N-001 任务状态收口

- **执行者**：Codex
- **任务**：将已完成的 `N-001` 从 `ready` 改为 `done`，避免明日自动开发重复选择已完成任务
- **修改文件**：
  - `docs/TASKS.md` — `N-001` 状态标记为 `done — commit 3b232a3`
- **验证**：
  - 提交后运行 `./scripts/auto_dev_loop.sh --dry-run`，预期选中下一项 `N-002`

---

## 2026-05-28 | 自动开发无任务请求协议

- **执行者**：Codex
- **任务**：为明日自动开发链路补充无 ready 任务时的处理方式，避免 OpenClaw 空转或自由发挥开发
- **修改文件**：
  - `docs/AUTO_DEV_PLAN.md` — 增加 `REQUEST_TASKS` 协议
- **协议要点**：
  - 没有 `[ready]` 任务时，只做只读扫描和候选任务草案
  - 候选任务状态只能是 `proposed`，不能自动变成 `ready`
  - 如需 Codex 参与，只允许一次 read-only `codex exec` 任务规划请求
  - 候选任务需等待孟或 Codex 确认后再进入任务池
- **执行约束**：
  - 不写代码
  - 不跑股票分析
  - 不自动调用外部数据源
  - 不 push

---

## 2026-05-28 | 夜间任务包入库：TradeFlow 事件源 + Simon 实践吸收

- **执行者**：Codex
- **任务**：根据 P1-1 事件源完成情况，以及 Simon `TradingAgents-astock` / `a-stock-data` 仓库实践，布置今晚 OpenCode 开发任务
- **背景**：
  - `P1-1` 已完成：`tradingagents/tradeflow/event_source.py` + `tests/test_event_source.py`，commit `b5131cd`
  - Simon 仓库可吸收点：直连 A 股数据源、政策/游资/解禁标签、结构化输出 schema、数据质量门控
  - 本项目保留自身路线：raw_evidence、强动作门禁、Buy/Risk Level、TradeFlow 候选池、Codex review 自动审核
- **修改文件**：
  - `docs/TASKS.md` — 新增 `N-001` 至 `N-005` 夜间任务包
  - `docs/AUTO_DEV_PLAN.md` — 同步 P1 当前优先级，加入 Simon 实践吸收路线
  - `docs/DEVLOG.md` — 记录本次任务入库
- **任务顺序**：
  1. `N-001` TradeFlow 事件源接入候选扫描
  2. `N-002` cn_astock 数据源验收与 fallback 接入
  3. `N-003` cn_astock raw_evidence 溯源接入
  4. `N-004` 政策/游资/解禁 A 股特化标签先入 TradeFlow
  5. `N-005` 最终执行层 schema 化方案与最小实现
  6. `V-001` 600584 数据真实性端到端验收
- **执行约束**：
  - 不改 prompts
  - 不写生产数据库
  - 不跑全市场扫描
  - 不自动调用 TA 深度分析
  - 不调用 DeepSeek

---

## 2026-05-27 | R-001 收尾巡检

- **执行者**：OpenCode
- **任务**：R-001 自动开发收尾巡检
- **结论**：

  | 子任务 | 结果 | 说明 |
  |--------|------|------|
  | 清理临时文件 | **PASS** | 删除 12 个根目录/子目录临时文件（patch_g008.py, fix_risk_manager.py 等） |
  | G-007 收尾 | **PASS** | 155 tests passed，已提交 `94d726f` |
  | G-008 接线修复 | **PASS** | `state.get("raw_evidence")` → 优先 `state["metadata"]["raw_evidence"]`，兼容旧路径 |
  | G-008 正式测试 | **PASS** | 13 tests in `tests/test_g008_valuation_sanity.py`，已提交 `3903963` |
  | 全量测试 | **PASS** | 653 passed, 9 skipped, 2 failed (pre-existing: test_portfolio_import scheduler DB) |

- **修改文件**：
  - `tradingagents/agents/managers/risk_manager.py` — raw_evidence 接线修复（优先 metadata 路径）
  - `tests/test_g008_valuation_sanity.py` — 新增正式 G-008 测试
  - 删除 12 个临时文件
- **已知 pre-existing 失败**：
  - `test_portfolio_import.py::test_scheduled_job_uses_imported_position_context`
  - `test_portfolio_import.py::test_scheduled_job_marks_failed_when_underlying_job_fails`
  - 原因：测试 DB 缺少 `scheduled_analyses` 表，非本次引入
- **下一步**：执行 `G-009` → `G-010` → `AUTO-002` → `V-001`

---

## 2026-05-27 | 自动开发任务状态收口

- **执行者**：Codex
- **任务**：修正 AUTO-002 收口后任务池仍选择已完成任务的问题
- **修改文件**：
  - `docs/TASKS.md` — 将 `R-001`、`G-009`、`G-010` 状态改为 `done`
  - `docs/TASKS.md` — 将旧 `G-008(P1)` 标记为已由 `G-010` 补修完成
  - `docs/TASKS.md` — 将 `V-001` 从 `blocked` 放开为 `ready`
- **验证**：
  - `./scripts/auto_dev_loop.sh --dry-run` 当前因本次文档变更导致工作区不干净而正确退出
  - 提交后应再次运行 dry-run，预期选中 `V-001` 或下一个真实业务任务，不再选中 `AUTO-002/G-008`

---

## 2026-05-27 | 自动开发进度巡检与新一轮任务布置

- **执行者**：Codex
- **任务**：检查 2026-05-26 自动开发进度，并将新一轮收尾/补修任务写入任务池
- **巡检结论**：
  - `G-005/G-006` 已有独立 commit，属于可复核状态
  - `G-007` 测试可通过，但代码仍处于未提交 diff，任务文档/DEVLOG 与 git 状态不一致
  - `G-008` 已有 commit，但存在生产接线路径疑点与根目录临时测试文件失败问题
  - `AUTO-001` 已有 v1.1/v1.2 修复，但仍需补提交白名单、commit 后工作区污染、临时文件防夹带等保险
- **修改文件**：
  - `docs/TASKS.md` — 新增 `R-001`、`AUTO-002`、`G-009`、`G-010`、`V-001`
  - `docs/DEVLOG.md` — 记录本次巡检与任务入库
- **验证**：
  - `pytest tests/test_g007_fund_lhb_provenance.py tests/test_readiness_score.py -q` → 155 passed
  - `pytest test_g008_valuation.py -q` → failed，根目录临时测试缺少 `Optional` import
  - `./scripts/auto_dev_loop.sh --dry-run` → 正确因工作区不干净退出
- **下一步**：
  - OpenClaw 先执行 `R-001`，再按顺序执行 `G-009` → `G-010` → `AUTO-002`
  - `V-001` 在 `G-009/G-010` 完成后再验收

---

## 2026-05-26 | G-007 资金流与龙虎榜数据源口径校验

- **执行者**：OpenCode
- **任务**：修复"主力资金报告说缺失，但下游仍把主力净流出当强证据"的口径冲突；龙虎榜必须区分未查询/无触发/查询失败/有数据
- **修改文件**：
  - `tradingagents/dataflows/providers/cn_akshare_provider.py` — [G-007]
    - `get_lhb_detail()`: 返回文本加 G-007 状态标签（LHB_NOT_QUERIED / LHB_NORMAL_NO_DATA / LHB_FAILED / LHB_HAS_DATA）
  - `tradingagents/graph/data_collector.py` — [G-007]
    - `_infer_source_status()`: 新增 LHB 状态标签识别（"查询未触发" → NOT_QUERIED，而非 HAS_DATA）
    - `_fetch_all()`: 新增 `_lhb_query_mode` 追踪 LHB 是否被强制查询
    - `build_raw_evidence()`: fund_flow_individual 新增 `source_type`、`unit_verified` 字段；fund_flow_board 新增 `source_type`；lhb 新增 `query_mode`
  - `tradingagents/agents/utils/readiness_score.py` — [G-007]
    - 新增 `build_fund_flow_provenance()`: 从 raw_evidence 构建资金流溯源，区分个股资金/板块资金/新闻转述资金，仅 individual HAS_DATA + unit 校验通过可作强证据
    - 新增 `build_lhb_provenance()`: 从 raw_evidence 构建龙虎榜溯源，区分 HAS_DATA / NOT_QUERIED / NORMAL_NO_DATA / FAILED
    - 新增 `format_fund_lhb_provenance()`: 格式化资金流与龙虎榜口径摘要，追加到报告底部
    - `infer_evidence_statuses()`: LHB "查询未触发" → NOT_QUERIED（原为 NORMAL_NO_DATA）
  - `tradingagents/agents/managers/risk_manager.py` — [G-007]
    - 集成 `build_fund_flow_provenance` 和 `build_lhb_provenance`
    - `get_strong_action_gate()` 调用使用 provenance 推导的 `fund_flow_unit_verified` 和 `fund_flow_not_mixed`
    - 报告追加 provenance 口径摘要区块
  - `tests/test_g007_fund_lhb_provenance.py` — 39 tests (全部通过)
  - `tests/test_readiness_score.py` — 更新 LHB 未触发测试断言为 NOT_QUERIED
- **测试结果**：
  - G-007: 39/39 passed
  - 全量回归: 597 passed, 9 skipped, 2 failed (pre-existing SQLite thread)
- **关键逻辑**：
  - 个股资金流接口失败 + 新闻中出现"主力净流出" → 门禁失败，新闻转述仅作弱证据
  - 龙虎榜 force=False → NOT_QUERIED，不显示"无显著资金异动"
  - 龙虎榜历史有数据但当日未查 → 不说"无龙虎榜数据"，正确显示"未查询（非异动触发）"
- **代码标注要求**：`# [G-007] fund_lhb_provenance`

---

## 2026-05-26 | AUTO-001 自动开发闭环 v1

- **执行者**：主控AI
- **任务**：实现 `scripts/auto_dev_loop.sh`，串联 TASKS.md → OpenCode → 测试 → Codex review → commit
- **新增文件**：
  - `scripts/auto_dev_loop.sh` — 单轮自动开发闭环脚本（约 200 行）
- **流程**：
  1. 解析 TASKS.md 找 status=ready 最高优先级任务
  2. 工作区不干净时退出
  3. OpenCode 实现 → 运行测试 → codex review --uncommitted
  4. 测试通过 + review 无 P0/P1 → git commit
  5. 单任务最多 2 轮修复，超过输出 NEEDS_HUMAN
  6. 结果写入 DEVLOG.md
- **约束**：不改 prompts、不写生产 DB、不 push、不跑全市场扫描

---

## 2026-05-26 | G-005 + G-006 P0 修复

- **执行者**：OpenCode
- **任务**：G-005 当日日线缺失时用实时行情补齐 + G-006 报告原始证据快照落库
- **修改文件**：
  - `tradingagents/dataflows/providers/cn_akshare_provider.py` — [G-005] realtime_ohlcv_patch
    - 新增 `_fetch_realtime_ohlcv_from_quotes()`: 用 Sina/Eastmoney 实时行情替代不可靠的雪球接口
    - 重写 `_maybe_append_realtime_row()`: 优先 Sina → Eastmoney fallback，失败时记录 STALE/FAILED 状态
    - 更新 `_format_ak_hist()`: CSV header 记录 `is_realtime_patched`, `source`, `quote_time`
    - 成交量单位自适应：通过中位数比率检测并修正股/手单位不一致
    - 新增 `get_realtime_patch_info()` 辅助方法
  - `tradingagents/graph/data_collector.py` — [G-006] raw_evidence_snapshot
    - `build_raw_evidence()` 从 4 个 key 扩展到 15 个数据源全覆盖
    - 每个数据源附带: `raw`, `status`, `vendor`, `as_of`, `fetched_at`, `record_count`, `unit`, `error`, `is_realtime_patched`
    - 新增 `_infer_source_status()` 和 `_count_records()` 静态方法
  - `tradingagents/agents/utils/readiness_score.py` — [G-006]
    - `infer_evidence_statuses()` 新增 `_unwrap_raw()` 兼容 G-006 结构化格式
    - 对 `stock_data`, `fund_flow_individual`, `lhb`, `news` 四个 key 解包 `.raw` 字段
  - `api/main.py` — [G-006]
    - dual_horizon 路径: 在 result dict 顶层 hoist `metadata` (含 raw_evidence)
    - legacy streaming 路径: 若 `_build_result_payload` 丢失 raw_evidence，从 DataCollector 补回
  - `tests/test_g005_realtime_ohlcv_patch.py` — 8 tests (全部通过)
  - `tests/test_g006_raw_evidence_snapshot.py` — 10 tests (全部通过)
- **测试结果**：
  - G-005: 8/8 passed (补行/不重复/历史不补/失败状态/header/盘前不补/量单位/非交易日)
  - G-006: 10/10 passed (stock_data存在/资金流FAILED/lhb NOT_QUERIED/全key/realtime标记/dual_horizon保留/空pool/无密钥/infer兼容新格式/infer兼容旧格式)
  - 全量回归: 181 tests passed (含 test_readiness_score, test_e_series_fixes, test_data_collector, test_realtime_quote_provider)
- **Commit**:
  - `778892b` fix: G-005 realtime OHLCV patch for missing intraday data
  - `720040f` feat: G-006 raw evidence snapshot in report result_data

---

## 2026-05-26 | 数据真实性与证据快照任务入库

- **执行者**：Codex
- **任务**：将 600584.SH 报告暴露的数据接入问题拆成 OpenClaw 可执行任务
- **修改文件**：
  - `docs/TASKS.md` — 新增 `G-005` 当日日线缺失时用实时行情补齐 TA 行情输入、`G-006` 报告原始证据快照落库、`G-007` 资金流与龙虎榜数据源口径校验、`G-008` 估值 sanity check 与旧价污染拦截
  - `docs/TASKS.md` — 同步 `G-002/G-003/G-004` 状态为已完成/已补修
  - `docs/DEVLOG.md` — 记录本次任务入库
- **验证**：
  - 文档变更，无代码测试
- **下一步**：
  - OpenClaw 优先执行 `G-005` 与 `G-006`
  - `G-007/G-008` 在数据快照链路稳定后继续执行

---

## 2026-05-26 | G-003 + G-004 P0 修复

- **执行者**：OpenCode
- **任务**：G-003 修复研究经理少数方向识别 + G-004 修复 TradeFlow save_candidates 日期落库回归
- **修改文件**：
  - `tradingagents/agents/managers/research_manager.py` — [G-003]
    - `_ANALYST_MAP` 补入 `game_theory_report` → `game_theory_analyst`，满足 7 analyst 统计
    - `_build_consensus_block()` 从 `counts` 改为 `nonzero_dirs`（排除 0 票方向），minority 只从非零票中选择
    - 新增 `majority_cnt - minority_cnt < 2` 阈值检查，4:3 等无明显多数场景返回 None
  - `tradingagents/tradeflow/plan_runner.py` — [G-004]
    - `generate_daily_plan` 持久化候选前确保 `c.trade_date` 与计划 `trade_date` 一致
  - `tests/test_research_manager_consensus.py` — 新增 17 个测试覆盖 G-003 全部场景
- **测试结果**：
  - `pytest tests/test_research_manager_consensus.py -q` → 17 passed
  - `pytest tests/test_g001_three_layer.py tests/test_readiness_score.py -q` → 163 passed
  - `pytest tests/test_tradeflow_*.py -q` → 83 passed

---

## 2026-05-26 | 巡检发现问题入任务池

- **执行者**：Codex
- **任务**：将开发巡检发现的阻塞问题整理为 OpenClaw 可执行任务，避免只停留在聊天记录中
- **修改文件**：
  - `docs/TASKS.md` — 将 `G-002` 状态改为 `in_review`，新增 `G-003` 修复研究经理多数/少数方向识别与测试缺口，新增 `G-004` 修复 TradeFlow `save_candidates` 日期落库回归
  - `docs/DEVLOG.md` — 记录本次任务入库
- **验证**：
  - 文档变更，无代码测试
- **下一步**：
  - OpenClaw 优先执行 `G-003` 与 `G-004`
  - 修复后由 Codex 复跑相关测试并做 diff 审核

---

## 2026-05-26 | 自动开发巡检任务入库

- **执行者**：Codex
- **任务**：将项目巡检内容整理为自动开发前置任务，纳入任务池和自动开发路线图
- **修改文件**：
  - `docs/TASKS.md` — 新增 `T-000 自动开发巡检基线`，覆盖 git 状态、diff、测试、生产数据库、敏感文件、token/API 消耗、运行产物和文档一致性巡检
  - `docs/AUTO_DEV_PLAN.md` — 将巡检设为每轮自动开发前置项，并补充日报中的巡检结果区块
- **验证**：
  - 文档变更，无代码测试

---

## 2026-05-26 | 自动开发路线文档入库

- **执行者**：Codex
- **任务**：将 OpenClaw 自动开发路线固化为项目文档，并同步任务池
- **修改文件**：
  - `docs/AUTO_DEV_PLAN.md` — 新增自动开发路线图、允许/禁止事项、任务优先级、验收标准和日报格式
  - `docs/TASKS.md` — 新增 TradeFlow 自动开发路线任务节（T-001~T-005），覆盖真实事件源、小范围 Discovery、资金异动池、盘中 Observe、盘后 Review
- **验证**：
  - 文档变更，无代码测试

---

## 2026-05-25 | P0-3 rate_limiter 补修（async 死锁 + 移除失败队列）

- **执行者**：subagent
- **任务**：修复 `rate_limiter.py` 的 async 死锁问题，移除名存实亡的 RetryQueue，补测试
- **修改文件**：
  - `tradingagents/llm_clients/rate_limiter.py` — 核心重写
    - sync `invoke_with_retry` 继续用 `threading.Semaphore`
    - async `ainvoke_with_retry` 改用 `asyncio.Semaphore`（`async with sem` 代替 `sem.acquire()`）
    - 移除 `RetryTask`、`RetryQueue`、`_try_enqueue` 等队列相关代码（`retry_count=3 > max_queue_retries=2` 导致队列永远不消费）
    - 429 重试耗尽后直接抛异常，不再假装入队
    - `_get_async_semaphore()` 缓存到模块级变量，确保并发限制生效
  - `tests/test_llm_rate_limiter.py` — 测试重写
    - 移除 `TestRetryTask`、`TestRetryQueue` 类
    - 新增 `TestAsyncSemaphoreContention`：max_concurrent=1 两个 ainvoke 必须串行完成且不死锁
    - 新增 async 429 exhausted 直接报错测试
    - 新增 async 402/auth 立即失败测试
    - 新增 async 5xx/timeout 只重试一次测试
    - 新增 sync 402 立即失败测试
- **验证**：
  - `pytest tests/test_llm_rate_limiter.py` → 37 passed
  - `pytest tests/test_p0_p1_acceptance.py tests/test_g001_three_layer.py tests/test_readiness_score.py` → 175 passed

---

## 2026-05-25 | TradeFlow P0 补修（事件源 + 价位覆盖 + 评分 + 落库 + 清理）

- **执行者**：OpenCode
- **任务**：TradeFlow P0 补修 6 项
- **修改文件**：
  - `tradingagents/tradeflow/schemas.py` — 新增 `primary_strategy` 字段，`merge_signals()` 只取最高分信号的价位
  - `tradingagents/tradeflow/strategies/vcp.py` — 评分公式修正：`(1 - range_ratio) * 50`，强收缩得分更高
  - `tradingagents/tradeflow/candidate_engine.py` — DB schema 新增 `primary_strategy` 列 + ALTER TABLE 迁移
  - `tradingagents/tradeflow/plan_runner.py` — 新增 `news_texts/event_overrides/save_candidates` 参数
  - `tradingagents/tradeflow/universe.py` — 支持 `event_overrides` 构造候选
  - `scripts/run_tradeflow_plan.py` — CLI 新增 `--save-candidates` 和 `--news` 参数
  - `.gitignore` — 新增 `tradeflow.db`
  - `tests/` — 新增 11 个测试（事件源/价位覆盖/VCP评分/候选落库）
- **验证**：
  - `pytest tests/test_tradeflow_*.py` → 67 passed
  - `python scripts/run_tradeflow_plan.py --symbols 002353.SZ,603256.SH` → 无事件输入时正确不命中

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
## 2026-05-27 | G-008 估值sanity check与旧价污染拦截

- **执行者**：主控AI
- **任务**：实现估值sanity check与旧价污染拦截，解决"报告引用过期估值价格导致误导性结论"问题
- **修改文件**：
  - `tradingagents/agents/managers/risk_manager.py` — [G-008]
    - 增强估值检查功能：从raw_evidence.stock_data.raw提取当前价格（支持G-006新格式和旧格式CSV）
    - 添加价格提取函数：支持从CSV最新行获取收盘价，支持从CSV头部查找价格
    - 集成到估值检查流程：优先使用raw_evidence价格，降级到原有文本提取方法
    - 增强错误处理：价格提取失败时优雅降级，不影响原有功能
    - 日志增强：添加G-008标签的详细日志记录价格提取过程和检测结果
- **验证方式**：
  - 创建独立测试文件验证价格提取功能
  - 测试高偏差（62.5%）、低偏差（2.5%）、无价格、无证据等场景
  - 验证CSV格式解析正确性
  - 验证集成到risk_manager后不影响其他功能
- **实现要点**：
  1. 支持新G-006格式：`raw_evidence.stock_data.raw` 为CSV字符串
  2. 支持旧格式：`raw_evidence.stock_data` 为CSV字符串
  3. 价格提取策略：优先使用最新行的close价格，降级到头部价格查找
  4. 集成位置：Fix-2估值检查环节，raw_evidence处理之后
  5. 错误处理：价格提取失败时不中断流程，使用原有方法
  6. 日志记录：区分新格式提取、旧格式提取、失败等场景

## 2026-05-28 | P1-1 akshare 事件源接入

- **执行者**：OpenCode (glm-5.1)
- **任务**：接入 akshare 公告接口，创建 TradeFlow 事件源模块
- **修改文件**：
  - `tradingagents/tradeflow/event_source.py`（265行）— 4 个核心函数
    - `fetch_notice_events(date)` — 沪深公告（东方财富），支持按类型筛选
    - `fetch_buyback_events()` — 回购计划，近30天内，全部 bullish
    - `fetch_rating_events(date)` — 分析师评级（巨潮），买入/增持→bullish
    - `fetch_daily_events(date)` — 聚合全部事件，返回 `{symbol: [title, ...]}`
  - `tests/test_event_source.py`（251行）— 24 个测试
- **数据源**：
  - `ak.stock_notice_report` — 每日 1000-1500 条公告
  - `ak.stock_repurchase_em` — 全量 5000+ 回购记录
  - `ak.stock_rank_forecast_cninfo` — 每日 40-60 条评级
- **集成点**：`fetch_daily_events()` 返回格式直接兼容 `score_event_catalyst(news_texts=)`
- **验证**：24 + 67 = 91 tests passed，commit b5131cd

## 2026-05-28 | N-002 cn_astock fallback 接入

- **执行者**：OpenCode
- **任务**：cn_astock 数据源验收与 fallback 接入
- **修改文件**：
  - `tests/test_cn_astock_provider.py`（新增，251行）— 50 测试覆盖 symbol 规范化、registry 注册、CSV 字段、realtime quote JSON、失败降级、amount/volume 区分
  - `tradingagents/tradeflow/candidate_engine.py` — `_fetch_price_data()` fallback 链改为 `cn_akshare → cn_astock → yfinance`，记录 `price_source` 到 `df.attrs`
  - `tradingagents/dataflows/providers/cn_astock_provider.py` — `get_realtime_quotes()` 增加 amount < 10000 的 sanity check 日志
- **标记**：`# [N-002] cn_astock_fallback`
- **测试**：111 passed, 2 skipped
- **Commit**：a0770bb

## 2026-05-28 | V-001 600584 数据真实性端到端验收

- **执行者**：主控AI
- **任务**：用 600584 做轻量端到端验收，确认数据源、raw_evidence、事件源、信号标签链路正常
- **验收结果**：
  - ✅ 行情数据：cn_akshare 提供，5/20-5/28 共 11 行，最新 84.74
  - ✅ 实时行情：cn_akshare 提供，价格/open/high/low/previous_close/amount/source 字段齐全
  - ✅ Vendor 追踪：`get_last_hit_vendor("get_stock_data")` 返回 "cn_akshare"（N-003 功能正常）
  - ✅ 事件源：`fetch_daily_events('20260528')` 返回 911 个 symbol 的事件（N-001 功能正常）
  - ✅ 信号标签：回购公告 → BUYBACK_EVENT 策略标签；解禁公告 → LOCKUP_RISK 风险标签（N-004 功能正常）
  - 600584 今日无公告（正常现象）
- **标记**：`# [V-001]`

## 2026-05-28 | N-005 执行层 schema 化最小实现

- **执行者**：OpenCode
- **任务**：为最终执行层增加结构化输出的最小 schema
- **修改文件**：
  - `tradingagents/agents/utils/execution_schema.py`（220行）— ExecutionSchema dataclass + Action/BuyLevel/RiskLevel 枚举 + build_execution_schema() helper + validate() 方法
  - `tests/test_execution_schema.py`（180行）— 覆盖 WAIT/ENTER/HOLD/REDUCE/EXIT 动作推导、字段冲突验证、低覆盖率标记、序列化
- **Schema 字段**：action, buy_level, risk_level, trigger_price, invalid_price, confidence, opportunity_score, source_coverage, evidence_coverage, strong_action_gate_passed, gate_failures, position_status, analysis_intent, horizon, data_quality_flags, evidence_refs, valuation_mismatch
- **设计**：sidecar 模式，不改变现有文本输出
- **标记**：`# [N-005] execution_schema`
- **测试**：139 passed（含 readiness_score 测试）
- **Commit**：4ec69ec

## 2026-05-29 | AUTO-002 自动开发闭环

- **任务**: M-002 — 任务运行档案索引与夜间日报聚合（P1）
- **优先级**: P1
- **轮次**: 2
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/M-002-20260529-round2.txt
- **运行档案**: docs/task_runs/M-002-20260529-112320/

## 2026-05-29 | AUTO-002 自动开发闭环

- **任务**: S-001 — 政策版本因子进入 TradeFlow 候选池（P1）
- **优先级**: P1
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/S-001-20260529-round1.txt
- **运行档案**: docs/task_runs/S-001-20260529-124731/
