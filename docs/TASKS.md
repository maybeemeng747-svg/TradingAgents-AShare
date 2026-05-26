# 任务池

> 最后更新：2026-05-26

---

## Z. 自动开发基础设施（2026-05-26 新增）

### AUTO-001: 自动开发闭环 v1（P0）
- **描述**：实现 `scripts/auto_dev_loop.sh`，串联 TASKS.md → OpenCode → 测试 → Codex review → commit 的单轮闭环
- **优先级**：P0
- **状态**：done
- **实现要点**：
  1. 解析 TASKS.md 找 status=ready 最高优先级任务
  2. 工作区不干净时退出
  3. OpenCode 实现 → 运行测试 → codex review --uncommitted
  4. 测试通过 + review 无 P0/P1 → git commit
  5. 单任务最多 2 轮修复，超过输出 NEEDS_HUMAN
  6. 结果写入 DEVLOG.md
- **验证方式**：
  - 运行 `./scripts/auto_dev_loop.sh` 一轮后任务状态变为 done
  - 工作区不干净时脚本退出
  - 测试失败时进入修复循环
  - NEEDS_HUMAN 时正确输出
- **完成记录**：scripts/auto_dev_loop.sh 已创建

---

## A. 当前进行中

### F-001: TA研报执行层修复（P0-P2共10项）
- **描述**：修复002837英维克报告暴露的10个问题，涉及止损价解析、估值sanity check、Opportunity Score封顶、未持仓语义、Buy/Risk Level冲突、舆情VERDICT矛盾、入场区间误识别、打法标签、A股做空措辞、Evidence Coverage保守化
- **优先级**：P0（1-3）+ P1（4-7）+ P2（8-10）
- **验收**：重跑002837报告，10项全部通过
- **状态**：已派发OpenCode执行

---

## B. 待办

### B-001: 接入小米 MiMo 模型到 TA 系统
- **描述**：在 `llm_clients/` 中添加小米 MiMo 适配器，支持 mimo-v2.5 和 mimo-v2.5-pro
- **优先级**：高
- **前置条件**：无
- **验证方式**：前端设置页切换到小米模型，执行一次分析任务成功

### B-002: 定时任务与 OpenClaw 联动
- **描述**：定时分析完成后自动通知 OpenClaw，由主控 AI 决定是否推送到飞书
- **优先级**：中
- **前置条件**：OpenClaw API 可达
- **验证方式**：定时任务完成后飞书收到通知

### B-003: 研报导出为飞书文档
- **描述**：分析结果支持导出为飞书云文档，方便分享和存档
- **优先级**：中
- **前置条件**：飞书 API 权限
- **验证方式**：点击导出按钮，飞书生成对应文档

### B-004: 持仓快照与 investment-controller 同步
- **描述**：TA 系统的持仓数据与 investment-controller 项目的 current_holdings.json 双向同步
- **优先级**：中
- **前置条件**：investment-controller API 可达
- **验证方式**：两边持仓数据一致

---

## T. TradeFlow 自动开发路线（2026-05-26 新增）

> 详细自动开发边界、日报格式和验收标准见 `docs/AUTO_DEV_PLAN.md`。

### T-000: 自动开发巡检基线
- **描述**：为 OpenClaw 自动开发建立每日/每轮固定巡检清单，先检查项目状态、敏感风险、测试健康和 token/API 消耗风险，再决定是否进入代码开发。
- **优先级**：高
- **状态**：ready
- **执行时机**：
  - 每次 OpenClaw 自动开发开始前
  - OpenCode/OpenClaw 完成代码修改后
  - 用户要求“审核/巡检/看看有没有偷偷消耗 token”时
- **巡检内容**：
  - Git 状态：当前分支、远端跟踪、ahead/behind、未提交文件、最近 5 个 commit。
  - Diff 摘要：按文件分组总结改动，标记是否涉及 `tradingagents/prompts/`、`tradingagents.db`、`logs/`、`eval_results/`、`.env`、模型配置、定时任务。
  - 测试健康：优先运行被改模块对应测试；TradeFlow 改动运行 `pytest tests/test_tradeflow_*.py -q`；TA 执行层改动运行相关 readiness/G001/P0-P1 测试。
  - 数据库安全：确认没有误改生产 `tradingagents.db`，测试数据不写入生产库。
  - 模型/API 消耗风险：检查是否新增或修改会自动调用 LLM/API 的脚本、定时任务、scheduler、OpenClaw cron；标记 DeepSeek/高成本模型调用风险。
  - 运行产物：检查是否产生未跟踪 `.db`、日志、报告、缓存、临时任务文件；该 ignore 的 ignore，该清理的清理。
  - 文档一致性：确认 `docs/DEVLOG.md`、`docs/TASKS.md`、`docs/AUTO_DEV_PLAN.md` 是否需要同步。
- **输出格式**：
  - 当前状态：干净/有变更/有风险
  - 变更摘要：文件列表 + 重点 diff
  - 测试结果：命令 + passed/failed
  - 敏感风险：数据库/prompt/log/env/API/cron
  - token/API 风险：是否可能自动消耗
  - 建议动作：可提交/需补修/暂停等待用户确认
- **验收方式**：
  - 巡检报告能明确区分“代码风险”和“运行/成本风险”。
  - 无高风险项时才允许进入自动开发。
  - 有高风险项时只输出报告，不改代码。

### T-001: TradeFlow P1 真实事件源接入
- **描述**：为 TradeFlow 候选池接入真实公告/新闻事件源，支持公告、业绩预告、回购、增持、中标/订单、并购/重组、监管处罚等事件输入。
- **优先级**：高
- **状态**：ready
- **实现要点**：
  - 优先接入 AKShare 公告/业绩预告/回购/增持等结构化接口；接口不稳定时保守降级，不伪造事件。
  - 接入东财新闻关键词匹配，先做规则分类，不调用外部 LLM。
  - 所有事件必须带 `symbol`，进入 `event_overrides` 后按 symbol 过滤，避免串票。
  - 事件只作为候选入池原因，不直接输出买入/卖出建议。
- **验证方式**：
  - 无事件输入时 `EVENT_CATALYST` 不命中。
  - 事件 symbol 不匹配时不命中。
  - 同一批多只股票各自只消费自己的事件。
  - `pytest tests/test_tradeflow_*.py -q` 通过。

### T-002: TradeFlow P1 小范围 Discovery
- **描述**：在不做全市场扫描的前提下，先对小范围股票池做候选发现，验证策略误报率和运行成本。
- **优先级**：高
- **状态**：ready
- **实现要点**：
  - 先支持手动行业池/自选池/持仓池扫描，不直接扫全 A 股。
  - 行业池优先选择 1-2 个申万二级行业试点。
  - 输出 TopN 候选、入池策略、触发价、失效价、过滤原因。
  - 不自动调用 TA 深度分析，只打 `need_deep_ta` 标记。
- **验证方式**：
  - 可指定行业池或股票列表运行。
  - 过滤原因区分流动性差/数据缺失/无策略命中。
  - 计划中无强买卖词。

### T-003: TradeFlow P1 资金异动池
- **描述**：新增资金异动候选池，将主力资金连续流入、资金占比异常、个股/板块资金共振作为候选入池信号。
- **优先级**：中
- **状态**：ready
- **实现要点**：
  - 使用近 5-20 日个股资金流，校验单位和字段来源。
  - 个股资金与板块资金分开记录，禁止混用。
  - 资金异动只做加分项，不单独触发强结论。
- **验证方式**：
  - 单位未校验时不输出高置信资金信号。
  - 板块资金与个股资金字段分离。

### T-004: TradeFlow P2 盘中 Observe
- **描述**：对候选池做盘中低频触发检查，发现突破触发价、跌破失效价、异常放量等事件。
- **优先级**：中
- **状态**：blocked
- **前置条件**：T-001/T-002 稳定运行后再做。
- **实现要点**：
  - 默认静默，只在触发条件满足时记录 signal。
  - 先不自动推送飞书，不自动调用 TA。
  - 每日触发次数设置上限，避免噪声。
- **验证方式**：
  - 未触发时不输出提醒。
  - 触发信号包含原始价量证据。

### T-005: TradeFlow P3 盘后 Review
- **描述**：复盘候选池信号是否有效，记录命中率、误报率、继续观察/移除理由。
- **优先级**：中
- **状态**：blocked
- **前置条件**：盘前 Plan 和盘中 Observe 有稳定信号记录。
- **验证方式**：
  - 每日可输出候选复盘表。
  - 对失效候选给出明确移除原因。

---

## G. 研究经理层改进（2026-05-26 新增）

### G-002: 研究经理一致性降权机制（P1）
- **描述**：在 research_manager 层增加"多数一致性权重"与"孤立偏多降权"机制，减少同票多份矛盾报告和基本面孤证误导
- **背景**：603256.SH、002353.SZ、002138.SZ 暴露同类问题——基本面分析师孤立偏多时，研究经理缺少显式降权机制，导致最终结论可能被单点意见带偏
- **优先级**：P1
- **状态**：done（已由 `G-003` 补修并通过测试）
- **实现要点**：
  1. 统计 7 个 analyst 的方向分布，形成结构化多数/少数结果
  2. 当出现 1 对 6 或 2 对 5 时，孤立少数意见默认降权
  3. 若孤立少数来自 fundamentals，且同时命中"经营现金流背离 / 内部人减持 / 量价走弱"中的至少 1-2 项，再额外降权
  4. 研究经理输出里必须包含"多数方向、少数方向、采纳/降权原因"的冲突摘要
  5. 不只改 prompt，优先在 research_manager 层补 deterministic 规则
- **验收方式**：
  - 603256.SH：基本面单点偏多不应压过其余 6 个偏空信号
  - 002353.SZ：Q1 现金流转弱需被纳入降权逻辑
  - 002138.SZ：同日多份矛盾报告问题应明显收敛
  - 最终报告应能解释"为什么降权"，而不是只改最终方向
- **代码标注要求**：`# [G-002] consensus_weight`

### G-003: 修复 G-002 少数方向识别与测试缺口（P0）
- **描述**：修复 `research_manager.py` 中 G-002 多数/少数方向识别错误，并补齐单元测试，确保孤立偏多/偏空真的触发降权摘要。
- **背景**：Codex 巡检发现 5 空 / 1 多的典型场景下 `_build_consensus_block()` 返回 `None`。原因是当前实现把 0 票方向也纳入少数方向排序，导致 minority 先选到 0 票方向后直接跳过。
- **优先级**：P0
- **状态**：done
- **执行约束**：
  - 只修 deterministic 规则与测试，不改 `tradingagents/prompts/`。
  - 保持 G-002 输出为辅助降权摘要，不直接输出强买卖建议。
  - 修改后更新 `docs/DEVLOG.md`。
- **实现要点**：
  1. minority 只允许从非零票方向中选择，不能把 0 票方向当少数方向。
  2. `_ANALYST_MAP` 补入 `game_theory_report`，满足 7 个 analyst 统计要求。
  3. 覆盖 6:1、5:2、5:1、含中性、无明显多数、基本面额外降权等边界。
  4. 输出摘要必须包含多数方向、少数方向、少数 analyst、降权原因。
- **验证方式**：
  - 新增 `tests/test_research_manager_consensus.py` 或等价测试文件。
  - `pytest tests/test_research_manager_consensus.py -q` 通过。
  - `pytest tests/test_g001_three_layer.py tests/test_readiness_score.py -q` 通过。
- **完成记录**：
  - `pytest tests/test_research_manager_consensus.py -q` → 17 passed
  - `pytest tests/test_g001_three_layer.py tests/test_readiness_score.py -q` → 163 passed
- **代码标注要求**：`# [G-003] consensus_weight_fix`

### G-004: 修复 TradeFlow save_candidates 日期落库回归（P0）
- **描述**：修复 `generate_daily_plan(..., trade_date=..., candidates=..., save_candidates=True)` 保存候选时未使用传入 `trade_date` 的问题。
- **背景**：Codex 巡检运行 `pytest tests/test_tradeflow_*.py -q` 出现 1 个失败：`TestPlanPersistence.test_save_candidates_persisted`。已有候选对象使用默认当天日期，导致查询指定计划日期时查不到落库记录。
- **优先级**：P0
- **状态**：done
- **执行约束**：
  - 只修 TradeFlow P0 回归，不引入真实事件源/全市场扫描。
  - 不写入生产 `tradingagents.db`。
  - 修改后更新 `docs/DEVLOG.md`。
- **实现要点**：
  1. 当 `generate_daily_plan` 收到外部 `candidates` 和显式 `trade_date` 时，持久化前确保每个 `Candidate.trade_date` 与计划日期一致。
  2. 不破坏由 `evaluate_symbol` 生成候选时已有的 trade_date 传递逻辑。
  3. 如需调整测试，必须保持 `save_candidates=False` 不落库的断言。
- **验证方式**：
  - `pytest tests/test_tradeflow_plan_runner.py::TestPlanPersistence::test_save_candidates_persisted -q` 通过。
  - `pytest tests/test_tradeflow_*.py -q` 全部通过。
- **完成记录**：
  - `pytest tests/test_tradeflow_*.py -q` → 83 passed
- **代码标注要求**：`# [G-004] tradeflow_candidate_date`

### G-005: 当日日线缺失时用实时行情补齐 TA 行情输入（P0） ✅ 已完成
- **描述**：修复 TA 分析报告缺少当天实时行情的问题。当 `get_stock_data(symbol, ..., trade_date)` 的日线历史接口未返回 `trade_date` 当天数据时，必须调用 `get_realtime_quotes()` 合成当天 OHLCV 行，并在报告证据中标注补齐来源。
- **背景**：600584.SH 报告生成于 2026-05-26 收盘后，但市场/量价分析仍只使用 2026-05-25 收盘 `80.17`，未使用 2026-05-26 新浪实时行情 `88.19`。代码中 `_maybe_append_realtime_row()` 走雪接口且异常静默吞掉，而更稳定的 `get_realtime_quotes()` 已能返回当日开高低收、成交量、成交额。
- **优先级**：P0
- **状态**：done
- **执行约束**：
  - 不改 `tradingagents/prompts/`。
  - 不写入生产 `tradingagents.db`。
  - 不自动跑全市场扫描或深度 TA。
  - 修改后更新 `docs/DEVLOG.md`。
- **实现要点**：
  1. 在 `CnAkshareProvider._maybe_append_realtime_row()` 或 `get_stock_data()` 中，优先复用 `get_realtime_quotes()`/Sina quote 逻辑补当天行，避免只依赖 `stock_individual_spot_xq`。
  2. 只在 `end_date == 今日` 且历史日线缺少今日时补行；历史回测日期禁止补实时行。
  3. 补行必须记录 `quote_time`、`source`、`is_realtime_patched=True`，至少进入 raw evidence 或返回文本 header。
  4. 成交量单位必须统一：Sina 返回股数时转成与历史 OHLCV 一致的单位，避免 275万手 vs 339,836,664 股混用。
  5. 若实时接口失败，不能静默；应在数据质量状态中标记 `STALE` 或 `FAILED`。
- **验证方式**：
  - 新增或补充测试：mock 历史 OHLCV 缺今日，mock realtime quote 返回今日数据，断言输出 CSV 包含今日行。
  - mock 历史 OHLCV 已含今日，断言不会重复补行。
  - mock 非今日/历史回测日期，断言不会补实时行。
  - 手工只读验证：`route_to_vendor('get_stock_data','600584.SH','2026-05-20','2026-05-26')` 在收盘后应包含 `2026-05-26` 行。
- **代码标注要求**：`# [G-005] realtime_ohlcv_patch`

### G-006: 报告原始证据快照落库与可追溯数据质量状态（P0） ✅ 已完成
- **描述**：每份 TA 报告必须保存本次分析实际使用的原始证据快照，支持事后核查行情、资金、龙虎榜、新闻、财报到底来自哪个源、是否失败、是否过期。
- **背景**：600584.SH 报告已入 `reports` 表，但 `result_data` 中缺少 `metadata.raw_evidence` 和完整原始行情快照。事后只能看到模型文字，无法从数据库还原模型实际使用的数据，导致“数据真伪/来源”难审计。
- **优先级**：P0
- **状态**：done
- **执行约束**：
  - 优先写入 `reports.result_data.metadata.raw_evidence`，暂不大规模迁移生产数据库。
  - 如需新增表，先输出迁移方案并使用安全迁移函数，不直接破坏旧表。
  - 不保存 API key、cookie、token。
  - 修改后更新 `docs/DEVLOG.md`。
- **实现要点**：
  1. `DataCollector.build_raw_evidence()` 扩展为包含：`stock_data`、`realtime_quote`、`fund_flow_individual`、`fund_flow_board`、`lhb`、`news`、`global_news`、`fundamentals`、`balance_sheet`、`cashflow`、`income_statement`。
  2. 每个数据源附带 `status`：`HAS_DATA / PARTIAL / NOT_QUERIED / NORMAL_NO_DATA / FAILED / STALE`。
  3. 每个数据源附带 `vendor/source`、`as_of`、`fetched_at`、`record_count`、`unit`、`error`、`is_realtime_patched`。
  4. `reports.result_data` 保存 `metadata.raw_evidence`，并确保 dual_horizon/quick_analysis 两条路径都不丢。
  5. 报告底部数据源可用性从简单 ✅/❌ 改为多态摘要，至少显示“行情截至时间”和“是否补实时”。
- **验证方式**：
  - 新增测试：生成/构造 report result 后，`result_data.metadata.raw_evidence.stock_data` 存在。
  - 新增测试：资金流失败时 status 为 `FAILED`，龙虎榜未触发时为 `NOT_QUERIED`，非异动无数据时为 `NORMAL_NO_DATA`。
  - 新增测试：dual_horizon 结果中 metadata 不丢失 raw_evidence。
  - 数据库只读验证：最新报告可通过 SQL 查到 raw evidence 摘要。
- **代码标注要求**：`# [G-006] raw_evidence_snapshot`

### G-007: 资金流与龙虎榜数据源口径校验（P1）
- **描述**：修复“主力资金报告说缺失，但下游仍把主力净流出当强证据”的口径冲突；龙虎榜必须区分未查询、无触发、查询失败、有数据。
- **背景**：600584.SH 报告中 `smart_money_report` 明确写近20日主力资金 ProxyError、龙虎榜未触发，但研究经理/交易员仍多次使用“主力资金净流出”作为强证据，且将 5月22日龙虎榜事实与 5月26日未查询混在一起。
- **优先级**：P1
- **状态**：ready
- **执行约束**：
  - 不把新闻中的“资金流向日报”直接等同于个股主力资金接口。
  - 个股资金、板块资金、新闻转述资金必须分字段记录，禁止混用。
  - 修改后更新 `docs/DEVLOG.md`。
- **实现要点**：
  1. `extract_execution_signals` 或研究经理输入中加入资金源类型：`individual_fund_flow`、`board_fund_flow`、`news_reported_fund_flow`。
  2. 只有 `individual_fund_flow.status == HAS_DATA` 且单位校验通过，才允许作为“主力资金”强证据。
  3. `lhb` 状态细分：`HAS_DATA / NOT_QUERIED / NORMAL_NO_DATA / FAILED`。
  4. 下游冲突摘要必须指出“资金流接口失败，新闻转述资金仅作弱证据”。
- **验证方式**：
  - 构造资金流接口失败 + 新闻中出现“主力净流出”，最终强动作门禁不得把它当主力资金强证据。
  - 龙虎榜 `force=False` 时底部显示 `NOT_QUERIED`，不显示“无显著资金异动”。
  - 龙虎榜历史有数据但当日未查时，不得说“无龙虎榜数据”。
- **代码标注要求**：`# [G-007] fund_lhb_provenance`

### G-008: 估值 sanity check 与旧价污染拦截（P1）
- **描述**：修复基本面报告中用旧股价/假设股价计算市值和 PE 的问题，禁止在已有最新行情价时使用过期假设价格。
- **背景**：600584.SH 报告中一边使用 80.17 的行情，一边在基本面估值处使用“股价假设30元”计算总市值和 PE，导致 PE 从约90倍级别错写为36倍。
- **优先级**：P1
- **状态**：ready
- **执行约束**：
  - 不改 prompt 作为唯一方案，优先在 readiness/quality 层做 deterministic sanity check。
  - 修改后更新 `docs/DEVLOG.md`。
- **实现要点**：
  1. 从 raw evidence/latest quote 中提取最新价格，作为估值计算基准。
  2. 若报告中出现“假设股价/约X元”且与最新价格偏离超过 20%，标记 `valuation_stale_price_conflict`。
  3. 出现该冲突时，基本面估值结论降权，Confidence 下调，不允许输出“估值合理/安全边际高”等强表述。
  4. 报告底部执行质检列出冲突：`估值价格与行情价格不一致`。
- **验证方式**：
  - 构造最新价 80、报告估值用 30，断言触发冲突。
  - 构造最新价 80、报告估值用 78，断言不触发。
  - 触发冲突时 Evidence/Confidence 降级。
- **代码标注要求**：`# [G-008] valuation_price_sanity`

---

## C. 报告质量防线（2026-05-10 新增）

> 以下 8 项基于 ChatGPT 建议 + 主控AI评估，按优先级排列。每项标注了实现要点和注意事项。

### C-001: position_validation_gate（P0 — 最高优先）
- **描述**：每份报告生成前必须读取 `current_positions.json`，根据持仓状态决定可输出的动作类型
- **实现要点**：
  - 文件不存在或为空 → 所有持仓相关策略（减仓/清仓/持有/止损/止盈）一律禁止输出
  - 有持仓 → 允许输出持有、减仓、止损、止盈
  - 无持仓 → 只允许输出观望、建仓、回避
- **代码标注要求**：在 `research_manager.py` 和 `risk_mgmt/` 入口处加 `# [C-001] position_validation_gate` 注释
- **验证方式**：无持仓时触发分析，确认不输出"减仓/清仓"

### C-002: account_capability 配置（P1）
- **描述**：增加账户能力配置，控制可交易品种
- **实现要点**：
  - 配置项：`can_buy_kcb`（科创板）、`can_buy_chinext`（创业板）、`can_short`（做空，默认 false）
  - 不设 `max_single_stock_position` 和 `max_daily_new_position`（交给风控 Agent 动态判断）
  - 配置文件：`tradingagents/default_config.py` 或 `.env`
- **代码标注要求**：`# [C-002] account_capability`
- **验证方式**：`can_short=false` 时不生成做空策略

### C-003: 禁止做空策略输出（P1）
- **描述**：如果 `can_short=false`，做空相关策略在生成阶段就不进入候选池
- **实现要点**：
  - 不是生成后转换措辞，而是从候选策略池直接移除
  - 策略池定义在 `prompts/` 中，过滤逻辑在 `researchers/` 入口
  - 涉及关键词：试空、做空、平空、空头开仓、融券卖出
- **代码标注要求**：`# [C-003] short_filter`
- **验证方式**：`can_short=false` 时，报告中不含任何做空相关表述

### C-004: 动作枚举重设计（P1）
- **描述**：将最终动作枚举精简为 5 个
- **枚举值**：
  - `WAIT` — 观望/等条件（含回避，备注里说明原因）
  - `ENTER` — 建仓
  - `HOLD` — 持有
  - `REDUCE` — 减仓/止损/止盈
  - `EXIT` — 清仓
- **实现要点**：
  - 替换现有的动作枚举定义
  - 更新所有引用动作枚举的代码
  - 报告输出格式同步更新
- **代码标注要求**：`# [C-004] action_enum`
- **验证方式**：所有报告输出的动作值都在 5 个枚举内

### C-005: same_symbol_delta_check（P2）
- **描述**：同一股票结论翻转时，必须输出对比信息
- **实现要点**：
  - 时间窗口：72 小时（不是 48 小时，避免短线正常波动误报）
  - 触发条件：结论翻转 **且** 没有新增数据源
  - 输出内容：上一版结论、本版结论、变化原因、新增证据、是否可能是模型噪音
  - 存储：`portfolio/analysis/delta_log/{stock_code}.json`
- **代码标注要求**：`# [C-005] delta_check`
- **验证方式**：同一股票 72 小时内结论翻转时，报告包含对比信息

### C-006: financial_data_validator — 分期实现（P2）
- **描述**：财报数据异常检测，分两期做
- **一期（规则检测）**：
  - 毛利率跳变 > 20% → 标记
  - 现金流与利润背离（利润增但经营现金流减）→ 标记
  - 资产负债率异常（> 70% 或环比变化 > 10%）→ 标记
  - 大额投资/筹资异动 → 标记
- **二期（精确计算）**：
  - 所有同比、环比由程序计算
  - 财务单位校验
- **代码标注要求**：`# [C-006] financial_validator`
- **验证方式**：检测到异常时，报告中明确标注"需人工复核"

### C-007: event_risk_gate（P1）
- **描述**：重大事件发生时，进入风控优先模式
- **事件类型**：
  - 大比例解禁（解禁比例 > 流通股本 5%）
  - 重大并购/重组
  - 高杠杆（资产负债率 > 80%）
  - 停复牌
  - 业绩暴雷（净利润下降 > 50%）
- **实现要点**：
  - 数据源：AKShare `stock_restricted_release_queue_em`（解禁）、yfinance（停复牌）
  - 事件落地前，除非主控 AI 特批，否则不允许开仓
  - 不确定的数据源宁可不接，不要输出错误信息
- **代码标注要求**：`# [C-007] event_risk_gate`
- **验证方式**：检测到解禁事件时，报告标注"风控优先模式，不建议开仓"

### C-008: execution_readiness_score — 简化版（P2）
- **描述**：每份报告输出两个核心质量指标
- **指标**：
  - `data_completeness` — 数据完整度（源数据覆盖了多少需要的字段，0-100%）
  - `confidence` — 置信度（高/中/低，由 Agent 自评）
- **不做的维度**（等系统跑起来再加）：
  - ~~逻辑可信度~~（与置信度高度相关）
  - ~~是否适合新手~~（用户属性，不该在报告里写死）
  - ~~是否需要主控AI复核~~（这个判断本身需要另一个模型）
- **代码标注要求**：`# [C-008] readiness_score`
- **验证方式**：每份报告末尾包含 `data_completeness` 和 `confidence` 字段

---

## C 系列实施顺序

```
Phase 1（立即做）：C-001 + C-002 + C-003 + C-004
  ↓
Phase 2（稳定后）：C-005 + C-007
  ↓
Phase 3（优化期）：C-006 + C-008
```

---

## D. 修正评分与强动作门禁（2026-05-13 新增）

### D-001: 修正数据完整度评分 ✅ 已完成（含真证据修复）
- **描述**：risk_manager.py 从 state 传入全部 8 项数据源；evidence 不再因报告存在就标 HAS_DATA，需匹配具体原始字段
- **验证方式**：只有 4 个基础报告时 source_coverage=50%；纯文本报告无 OHLC 表格时 evidence 不虚高

### D-002: 强结论证据门禁 ✅ 已完成（含真降级修复）
- **描述**：新增 source_coverage/evidence_coverage 双维度评分；get_strong_action_gate 检查 7 项条件；gate 未通过时真正移除/替换强动作文本
- **验证方式**：coverage<70 时强动作被替换为中性表达，原关键词不再出现在最终报告

### D-003: Risk Level / Buy Level 双等级输出 ✅ 已完成（含真信号修复）
- **描述**：Risk Level 0-4 + Buy Level 0-4；从报告文本提取 14 项交易信号传入计算；持仓未知时 gate 语义修正
- **验证方式**：Level 4 需全部条件满足；持仓未知+无强动作时 gate 通过

### D-004: Opportunity Score 机会评分 ✅ 已完成（含真信号修复）
- **描述**：5 维加权 Opportunity Score 0-100；从报告文本提取趋势/资金共振/催化/盈亏比/入场质量信号传入计算
- **验证方式**：Opportunity Score=100 但 coverage<70 时 Buy Level 仍受限；正向信号文本下 Score 明显高于默认值

---

## E. 基础设施修复（2026-05-13 新增）

### E-001: 运行版本戳 ✅ 已完成
- **描述**：报告末尾追加系统版本区块（git commit hash + 后端启动时间 + 报告生成时间 + 评分版本标签）
- **实现要点**：`api/main.py` 启动时用 `subprocess` 读 git hash 并缓存；`readiness_score.py` 的 `format_readiness_score` 调用 `_format_version_block()`
- **验证方式**：报告末尾包含 `### 系统版本` 区块

### E-002: 代码→名称错配校验 ✅ 已完成
- **描述**：新增 `validate_stock_name()` 名称校验函数，名称不匹配时 `name_mismatch=True`，Level 4 被降级到最高 3
- **实现要点**：`get_strong_action_gate` 增加 `name_mismatch` 参数；`calculate_risk_level`/`calculate_buy_level` Level 4 降级
- **验证方式**：`name_mismatch=True` 时 gate 不通过，Level ≤ 3

### E-003: 执行质检解析器止损关键词扩展 ✅ 已完成
- **描述**：扩展止损关键词识别（止损红线/止损位/止损价/清仓线/止损线）；区分入场区间和减仓区间；新增 `execution_zone_conflict` 冲突检测
- **实现要点**：`extract_execution_signals` 返回 `has_stop_loss` 和 `execution_zone_conflict`；`get_strong_action_gate` 增加 `no_execution_zone_conflict` 参数
- **验证方式**：文本含"止损红线 25.50" → `has_stop_loss=True`；分离语境的入场区间+减仓区间不冲突（如入场28-30+减仓35-38）；区间重叠或止损价高于入场价才触发 `execution_zone_conflict`

### E-004: 原始证据表数据源接入 ✅ 已完成
- **描述**：`infer_evidence_statuses` 优先从 DataCollector 缓存池的结构化数据读取证据状态，fallback 到文本正则
- **实现要点**：DataCollector 新增 `build_raw_evidence()` 方法；`trading_graph.py` 将 raw_evidence 存入 state metadata；`infer_evidence_statuses` 接受 `raw_evidence` 参数
- **验证方式**：有 raw_evidence 时直接从结构化数据判断 OHLCV/fund_flow/lhb/news 状态；无 raw_evidence 时 fallback 到文本正则
