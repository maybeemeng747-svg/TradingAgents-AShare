# 任务池

> 最后更新：2026-05-31

---

## 0. 自动领取规则与当前队列

### 状态规则

- `ready`：允许 `scripts/auto_dev_loop.sh` 自动领取。
- `in_progress`：已有 OpenCode/OpenClaw/Codex 半成品或人工正在处理，自动开发不得重复领取。
- `blocked`：前置任务、人工确认、权限或成本条件未满足。
- `done`：已完成并有 commit / 验收记录。
- `proposed`：候选任务草案，只能人工或 Codex 确认后转为 `ready`。

### 自动领取顺序

1. 自动脚本只领取 `状态=ready` 的任务。
2. 同优先级按文档顺序领取；批量模式会在任务完成后继续领取下一个 `ready` 任务，直到无任务或某个任务失败。
3. 工作区不干净时必须停止，先进入人工/Codex review，不允许覆盖半成品。
4. 每个任务必须写入 `docs/task_runs/<TASK_ID>-YYYYMMDD-HHMMSS/` 运行档案。
5. 通过任务必须同时更新 `docs/TASKS.md`、`docs/DEVLOG.md`。

### 当前优先队列

1. `UI-001`：TradeFlow 只读 API 查询层（P1，done e6d3269）。
2. `UI-002`：前端 TradeFlow 页面骨架与候选池表格（P1，done edf69d9）。
3. `UI-003`：候选详情抽屉：证据、博弈、门禁解释（P1，done 9ee6aea）。
4. `UI-004`：盘中 Observe 与 TA 队列只读面板（P2，done fa75389）。
5. `UI-005`：盘后 Review 前端页面（P2，done 401fb7c）。
6. `UI-006`：数据源健康前端面板（P2，done 52d5f5e）。
7. `UI-007`：TradeFlow 被过滤候选可追溯展示（P1，done）。
8. `UI-008`：TradeFlow 候选字段规范化与名称回填（P1，done）。

---

## S. TradeFlow 选股策略核心任务池（2026-05-29 新增）

> 原则：先提高候选池质量，再扩大扫描范围。TA 深度分析只服务于已经有足够逻辑、事件、资金或风险结构支撑的候选股。

### S-001: 政策版本因子进入 TradeFlow 候选池（P1）
- **描述**：为 TradeFlow 增加政策版本因子，识别当前 A 股市场更容易被定价的政策/产业方向，让候选池从“技术形态先行”升级为“政策版本入池 + 技术确认”。
- **优先级**：P1
- **状态**：done — commit a4d06d4 + cf7d928
- **背景**：
  - A 股很多阶段不是简单的业绩驱动，而是政策方向、产业版本和市场叙事共同驱动。
  - 现在 TradeFlow 已有 VCP、事件催化、A 股标签，但缺少“当前版本需要什么标杆”的第一层判断。
- **执行约束**：
  - 不调用外部 LLM，不跑全市场扫描。
  - 不改 `tradingagents/prompts/`。
  - 不输出买入/卖出强建议。
  - 只在 TradeFlow 候选池层做信号、标签、分数和证据引用。
- **实现要点**：
  1. 新增或扩展政策版本信号模块，输出 `policy_tags`、`version_score`、`policy_evidence_refs`。
  2. 先用可维护关键词/规则覆盖：新质生产力、算力、低空经济、机器人、出海、中特估、国产替代、并购重组、国企改革等。
  3. 事件标题、公告摘要、行业标签命中政策版本时加分；无原始文本证据时不得加分。
  4. 与现有 `event_source`、`event_catalyst`、`candidate_engine` 兼容，不破坏 VCP/回踩策略。
  5. Daily Plan 中展示政策标签和证据摘要，但动作仍只能是 `OBSERVE/NEED_DEEP_TA` 等非强交易动作。
- **验证方式**：
  - 构造政策关键词事件，候选包含对应 `policy_tags` 和 evidence refs。
  - 无政策证据时 `version_score` 不得凭空加分。
  - 不同股票事件必须按 symbol 隔离，不能串票。
  - `pytest tests/test_tradeflow_*.py -q` 或新增对应测试通过。
- **代码标注要求**：`# [S-001] policy_version_signal`

### S-002: 叙事质量评分（P1）
- **描述**：给事件催化增加叙事质量评分，区分“普通公告/普通新闻”和“容易形成市场共识的主线事件”。
- **优先级**：P1
- **状态**：done — commit 9825e22
- **背景**：
  - 选股不只看有没有事件，还要判断事件是否容易被市场理解、传播和接力。
  - 目前事件催化更偏“是否存在”，缺少“事件质量与传播价值”。
- **执行约束**：
  - 第一阶段只做规则评分，不调用外部 LLM。
  - 不做舆情全网抓取，不扫全市场。
  - 评分只影响候选排序和 `need_deep_ta`，不直接生成交易建议。
- **实现要点**：
  1. 新增 `narrative_score`，分解为政策背书、产业落地、订单/回购/评级、传播清晰度、拥挤/过热扣分。
  2. 回购、重大订单、中标、并购重组、业绩预告、评级变化等事件给不同基础分。
  3. 多个事件叠加时做封顶，避免标题堆叠虚高。
  4. 输出 `narrative_reasons` 和 evidence refs，便于日报解释“为什么进候选池”。
- **验证方式**：
  - 普通新闻低分，回购/订单/政策相关事件高分。
  - 重复事件不会无限叠加。
  - `narrative_score` 缺证据时为 0 或保守默认。
- **代码标注要求**：`# [S-002] narrative_quality_score`

### S-003: 水下风险标签与候选降权（P1）
- **描述**：为 TradeFlow 候选池增加水下风险标签，识别解禁、减持、问询、财务异常、融资拥挤、龙虎榜过热等风险，对候选做降权或要求更严格技术确认。
- **优先级**：P1
- **状态**：done — commit a95c3d7
- **背景**：
  - 选对股票不只看上涨逻辑，也要避开“看起来有题材但水下结构很差”的票。
  - 当前已有部分 A 股标签，但风险标签还不够系统，也没有形成候选池统一降权规则。
- **执行约束**：
  - 所有风险标签必须绑定原始字段或事件文本。
  - 不把风险标签直接翻译成清仓/买入建议。
  - 不写生产数据库 schema；如需持久化先用现有 TradeFlow 结构或输出方案。
- **实现要点**：
  1. 统一输出 `risk_flags`：`LOCKUP_RISK`、`REDUCE_HOLDING_RISK`、`INQUIRY_RISK`、`FINANCIAL_QUALITY_RISK`、`MARGIN_CROWDING_RISK`、`LHB_OVERHEAT_RISK`。
  2. 风险标签进入候选分数降权和 `need_deep_ta` 门槛。
  3. 解禁/减持/问询等公告类风险优先从 `event_source` 读取。
  4. 龙虎榜和融资融券风险必须区分 `HAS_DATA/NOT_QUERIED/FAILED`，不得把未查询当作无风险。
- **验证方式**：
  - 构造解禁/减持/问询事件，候选出现对应 `risk_flags` 并降权。
  - 龙虎榜未查询时不生成过热风险。
  - 风险缺证据时不输出高置信风险标签。
- **代码标注要求**：`# [S-003] underwater_risk_flags`

### S-004: 候选股博弈平衡解释（P1）
- **描述**：在 TradeFlow Plan 中为每个候选输出轻量博弈解释，说明多头为何可能买、空头为何可能卖、政策/监管/资金结构是否支持，避免只给策略标签而缺少交易对手视角。
- **优先级**：P1
- **状态**：done — commit c92e626
- **背景**：
  - A 股候选池要处理动态博弈：同一事件可能同时有多头叙事、风险压制和资金拥挤。
  - 在 TA 深度分析前，候选池应先判断这只票是不是值得进一步消耗 token。
- **执行约束**：
  - 规则化生成，不调用外部 LLM。
  - 不输出强买卖词。
  - 不替代 TA 深度分析，只做候选池摘要。
- **实现要点**：
  1. 输出 `game_balance`：`favorable/neutral/crowded/fragile`。
  2. 输出 `bull_case`、`bear_case`、`policy_case`、`fund_flow_case` 的短字段，来源必须来自已存在信号和证据。
  3. 当政策/叙事/资金/技术至少两类共振时，可提高 `need_deep_ta` 优先级。
  4. 当风险标签过多或信号拥挤时，降低候选排序或标记 `fragile`。
- **验证方式**：
  - 政策 + 事件 + VCP 共振时 `game_balance=favorable` 或 `neutral`。
  - 风险标签过多时 `game_balance=fragile/crowded`。
  - 输出中包含证据引用，不出现强买卖词。
- **代码标注要求**：`# [S-004] candidate_game_balance`

### S-005: 选股优先级整合与 need_deep_ta 门槛重排（P1）
- **描述**：整合政策版本、叙事质量、水下风险、资金异动和技术形态，重排 TradeFlow 候选优先级，让 TA 深度分析优先消耗在真正值得分析的股票上。
- **优先级**：P1
- **状态**：done — commit b503d63
- **前置条件**：`S-001`、`S-002`、`S-003`、`S-004` 完成。
- **执行约束**：
  - 不自动调用 TA 深度分析。
  - 不做全市场扫描。
  - 不把单一信号直接提升为强结论。
- **实现要点**：
  1. 定义候选综合评分：政策版本、叙事质量、技术确认、资金验证、风险扣分、数据完整度。
  2. `need_deep_ta=True` 至少需要两类正向信号，且无重大未解释风险。
  3. Daily Plan 输出“为什么值得看/为什么暂不深挖/缺什么证据”。
  4. 为后续 `M-003` universe 管理器和 `M-004` 策略配置提供统一字段。
- **验证方式**：
  - 只有技术形态但无逻辑证据的票排序下降。
  - 政策/事件/技术/资金共振的票排序上升。
  - 风险过高或数据缺失时不触发 `need_deep_ta`。
- **代码标注要求**：`# [S-005] selection_priority_gate`

### S-006: 候选误报审计与样本集沉淀（P1）
- **描述**：建立 TradeFlow 候选误报审计机制，把“为什么没选中/为什么误选中”的样本沉淀为可回放 fixtures，持续压低候选池噪声。
- **优先级**：P1
- **状态**：done — commit e876172
- **前置条件**：`T-002`、`T-003`、`S-002`、`S-003` 完成。
- **执行约束**：
  - 不调用外部 LLM。
  - 不跑全市场扫描。
  - 不写生产数据库，只新增测试 fixtures 或本地报告脚本。
- **实现要点**：
  1. 新增候选审计报告，按“命中策略/过滤原因/风险降权/证据缺口”分类。
  2. 将最近人工验证过的样本沉淀为小型 fixtures，覆盖 VCP 命中、流动性过滤、无策略命中、事件催化、资金异动。
  3. 输出误报/漏报原因字段，便于后续调参。
  4. Daily Plan 或 discovery 报告增加“证据缺口”摘要，但不输出强交易建议。
- **验证方式**：
  - fixtures 可稳定回放，输出固定候选数量和过滤原因。
  - 流动性不足、事件缺证据、资金单位未校验等场景不会被误判为高优先级。
  - `pytest tests/test_tradeflow_*.py -q` 通过。
- **代码标注要求**：`# [S-006] candidate_false_positive_audit`

### S-007: 候选分层与 TA token 预算分配（P1）
- **描述**：把候选池分为 A/B/C 三层，明确哪些值得优先深度 TA、哪些只观察、哪些淘汰，避免把 token 消耗在弱候选上。
- **优先级**：P1
- **状态**：done — commit c1c6a83
- **前置条件**：`S-005` 完成。
- **执行约束**：
  - 不自动调用 TA。
  - 不输出强买卖词。
  - 不接入真实扣费 API，只输出预算建议和原因。
- **实现要点**：
  1. 定义候选等级：A=优先深挖、B=观察等待证据、C=淘汰/暂不关注。
  2. 结合综合评分、风险标签、数据完整度、策略共振数量生成等级。
  3. 输出 `ta_budget_priority`、`missing_evidence`、`why_not_deep_ta`。
  4. Daily Plan 按等级排序，A 层数量设上限，防止一天候选过多。
- **验证方式**：
  - 单一弱信号不得进入 A 层。
  - 两类以上强证据且风险可控时可进入 A 层。
  - 高风险/低完整度候选最多 B/C。
- **代码标注要求**：`# [S-007] candidate_tier_budget`

### S-008: TradeFlow 证据完整度门禁（P1）
- **描述**：给候选池增加轻量证据完整度门禁，避免缺行情、缺资金、缺事件来源时分数虚高。
- **优先级**：P1
- **状态**：done — commit d2875cd
- **前置条件**：`M-008`、`T-002`、`T-003` 完成。
- **执行约束**：
  - 不改 TA 深度报告 readiness 逻辑，只在 TradeFlow 候选层做门禁。
  - 数据缺失只能降权或标记缺口，不能编造补全。
- **实现要点**：
  1. 输出 `tradeflow_data_completeness` 和 `missing_data_fields`。
  2. 至少覆盖行情 OHLCV、流动性、事件来源、资金流单位校验、风险标签证据。
  3. 完整度低于阈值时禁止 A 层和 `need_deep_ta=True`。
  4. 报告中展示“缺什么证据才能升级”。
- **验证方式**：
  - 缺资金单位校验时不触发高置信资金异动。
  - 缺行情/流动性数据时不进入 A 层。
  - 完整数据样本不被误降级。
- **代码标注要求**：`# [S-008] tradeflow_evidence_gate`

### S-009: 修复 S-005 选股门控双重计分与资金单位校验（P1）
- **描述**：修复 Codex review 指出的两个候选排序风险：综合分重复叠加政策/叙事/资金/风险分，以及未校验单位的资金流也能作为 `need_deep_ta` 正向类别。
- **优先级**：P1
- **状态**：done — commit 64cc74f
- **前置条件**：`S-005`、`T-003`、`S-008` 完成。
- **执行约束**：
  - 不改 `tradingagents/prompts/`。
  - 不自动调用 TA 深度分析。
  - 不改变强动作门禁，只修 TradeFlow 候选层评分与门控。
- **问题来源**：
  - `docs/task_runs/S-005-20260529-164949/codex-review-round1.txt` 中两个 P2 findings。
- **实现要点**：
  1. 明确 `selection_priority_gate.run_selection_priority_gate(score=...)` 的 `score` 语义：如果传入的是已累计后的 `candidate.score`，不得再次叠加 `version_score/narrative_score/fund_flow_anomaly_score/risk_penalty`。
  2. 或者在 `candidate_engine.evaluate_symbol()` 传入未叠加的基础技术分，并在字段名/注释中标清。
  3. `fund_flow_unit_verified=False` 时，资金异动不得计入正向类别，也不得单独帮助通过 `need_deep_ta`。
  4. 增加回归测试：技术 + 未校验资金流不能触发 `gate_passed=True`；政策/叙事/资金/风险分不能重复计入综合分。
- **验证方式**：
  - `pytest tests/test_s005_selection_priority_gate.py tests/test_s008_evidence_gate.py tests/test_t003_fund_flow_anomaly.py -q` 通过。
  - `pytest tests/test_tradeflow_*.py -q` 通过。
- **代码标注要求**：`# [S-009] selection_gate_fix`

---

## M. 框架路线总任务池（2026-05-28 新增）

### INF-001: 自动开发任务领取锁与 in_progress 状态流转（P0）
- **描述**：给自动开发闭环增加任务领取锁，避免 OpenClaw/OpenCode/Codex 或多个夜间任务同时领取同一个 `ready` 任务。
- **优先级**：P0
- **状态**：done — commit 54a8292
- **背景**：
  - 当前已经出现并发/半成品 diff：代码改动先出现，但任务状态和运行档案未同步。
  - 需要把“领取任务 → 标记 in_progress → 写运行档案 → PASS/FAIL 收口”固化进脚本。
- **执行约束**：
  - 只改 `scripts/auto_dev_loop.sh`、`docs/AUTO_DEV_PLAN.md`、测试或必要文档。
  - 不改业务代码。
  - 不 push / PR / merge。
- **实现要点**：
  1. 非 dry-run 领取任务后，先把 `docs/TASKS.md` 中该任务状态从 `ready` 改为 `in_progress`，写入运行档案。
  2. 任务 PASS 后改为 `done — commit <hash>`。
  3. 任务失败或超轮次后改为 `blocked — NEEDS_HUMAN` 或保留 `in_progress` 并写明原因，不能静默回到 `ready`。
  4. 加锁文件例如 `.auto_dev.lock` 或 `docs/task_runs/.lock`，脚本异常退出时要有清理/提示机制。
  5. dry-run 不得修改任务状态。
- **验证方式**：
  - `bash -n scripts/auto_dev_loop.sh` 通过。
  - 新增或补充脚本测试/静态验证，确认 dry-run 不改文件。
  - 模拟领取任务后，TASKS 状态变为 `in_progress`，PASS 后变为 `done`。
- **代码标注要求**：`# [INF-001] task_claim_lock`

### M-002: 任务运行档案索引与夜间日报聚合（P1）
- **描述**：把 `docs/task_runs/` 中的运行档案汇总成可读索引，让第二天白天快速知道昨晚做了什么、成功/失败、风险在哪里。
- **优先级**：P1
- **状态**：done — commit 6a95046
- **执行约束**：
  - 不调用模型，不跑股票分析。
  - 只读 `docs/task_runs/`、`docs/reviews/`、`git log`，输出文档/脚本。
- **实现要点**：
  1. 新增脚本，例如 `scripts/summarize_auto_dev_runs.py`，扫描最近 N 个 task run。
  2. 生成 `docs/auto_dev_reports/YYYY-MM-DD.md`，包含任务、commit、测试、review、风险、下一步。
  3. 自动开发日报中引用该报告路径。
  4. 报告不得包含 API key、完整敏感日志。
- **验证方式**：
  - 构造最小 task_runs fixture，脚本能输出日报。
  - `python scripts/summarize_auto_dev_runs.py --date YYYY-MM-DD --dry-run` 可运行。
- **代码标注要求**：`# [M-002] auto_dev_report_index`

### M-003: TradeFlow universe 管理器（P1）
- **描述**：统一管理候选池来源：自选池、持仓池、手动行业池、事件池、资金异动池，避免每个策略各自拼 symbols。
- **优先级**：P1
- **状态**：done — commit 5af9ff2
- **前置条件**：`N-004` 和 `T-002` 完成。
- **执行约束**：
  - 不做全市场扫描。
  - 不写生产数据库 schema；如需表结构先输出迁移方案。
- **实现要点**：
  1. 定义 universe source 枚举：watchlist/holding/manual/event/fund_flow/industry。
  2. 每个 symbol 记录来源、入池原因、时间戳、过滤原因。
  3. 与 `generate_daily_plan()` 的现有 universe 构建兼容。
- **验证方式**：
  - 同一 symbol 来自多个来源时去重但保留来源列表。
  - 无来源时不触发全市场扫描。
- **代码标注要求**：`# [M-003] tradeflow_universe_manager`

### M-004: TradeFlow 策略权重与阈值配置（P1）
- **描述**：把 VCP、回踩、事件催化、资金异动、A股标签的权重和阈值集中配置，便于后续复盘调参。
- **优先级**：P1
- **状态**：done — commit bab3d5c
- **前置条件**：`N-004`、`T-003` 完成。
- **执行约束**：
  - 不让 LLM 动态改权重。
  - 默认配置必须保守，不输出强买卖词。
- **实现要点**：
  1. 新增配置对象或 yaml/json，保留默认值。
  2. 所有策略从配置读取阈值，测试覆盖默认值。
  3. 生成 daily plan 时输出使用的策略版本/配置版本。
- **验证方式**：
  - 修改阈值 fixture 后候选分数变化可预测。
  - 缺配置时使用默认值。
- **代码标注要求**：`# [M-004] strategy_config`

### M-005: 盘中 Observe 状态机（P2）
- **描述**：对盘前候选做低频盘中观察，记录触发价、失效价、放量、跌破等状态，不直接调用 TA。
- **优先级**：P2
- **状态**：done — commit 10ce312
- **前置条件**：`M-003`、`M-004` 完成。
- **执行约束**：
  - 每 30 分钟以内的低频观察，不做高频交易。
  - 不自动推送飞书，不自动调用深度 TA。
- **实现要点**：
  1. 定义 observe 状态：WAITING/TRIGGERED/INVALIDATED/EXPIRED。
  2. 每次检查保存原始价量证据和触发原因。
  3. 单日触发次数上限，防止噪声。
- **验证方式**：
  - 价格突破触发价 → TRIGGERED。
  - 跌破失效价 → INVALIDATED。
  - 未触发时不输出提醒。
- **代码标注要求**：`# [M-005] intraday_observe_state`

### M-006: OpenClaw 自动触发 TA 深度分析门控（P2）
- **描述**：在盘中 Observe 触发后，由 OpenClaw 决定是否调用 TA 深度分析，但必须有成本、模型、频率门控。
- **优先级**：P2
- **状态**：done — commit a22e4f2
- **前置条件**：`M-005` 完成并稳定运行。
- **执行约束**：
  - 默认不调用 DeepSeek。
  - 每日 TA 深度分析次数设置硬上限。
  - 未持仓/已持仓必须带 position_context。
- **实现要点**：
  1. 定义 `need_deep_ta` 的可执行条件。
  2. 记录触发 TA 的原因、模型、耗时、报告路径。
  3. 失败时不重试无限循环。
- **验证方式**：
  - `need_deep_ta=False` 不调用 TA。
  - 超出每日次数上限不调用 TA。
- **代码标注要求**：`# [M-006] gated_deep_ta_dispatch`

### M-007: 盘后 Review 与策略命中率复盘（P2）
- **描述**：复盘盘前候选和盘中触发是否有效，输出命中率、误报率、移除理由，服务下一轮策略调参。
- **优先级**：P2
- **状态**：done — commit 6c5b23a
- **前置条件**：`M-005` 稳定运行至少 3 个交易日。
- **执行约束**：
  - 不自动调整策略权重，只输出建议。
  - 不生成投资建议，只做信号质量复盘。
- **实现要点**：
  1. 每个候选记录次日/3日/5日表现。
  2. 统计各策略命中率和误报率。
  3. 输出 `docs/tradeflow_reviews/YYYY-MM-DD.md`。
- **验证方式**：
  - 给定候选和行情 fixture，能生成复盘表。
- **代码标注要求**：`# [M-007] post_market_review`

### M-011: 修复 M-003 universe 兼容性与来源 extra 序列化（P1）
- **描述**：修复 Codex review 指出的 universe manager 兼容性问题：事件覆盖来源标签变化，以及 `SourceRecord.extra` 被收集但序列化丢失。
- **优先级**：P1
- **状态**：done — commit 9bb3dd8
- **前置条件**：`M-003` 完成。
- **执行约束**：
  - 不做全市场扫描。
  - 不改生产数据库 schema。
  - 保持已有报告和过滤逻辑兼容。
- **问题来源**：
  - `docs/task_runs/M-003-20260530-002721/codex-review-round2.txt` 中两个 P2 findings。
- **实现要点**：
  1. `event_overrides` 进入 universe 时保留旧公共输出 `source="event_catalyst"`，避免已有报告、筛选器、测试断裂。
  2. `UniverseSymbol.to_dict()` 的 `universe_source_records` 序列化 `SourceRecord.extra`。
  3. `include_yesterday` 带入的 `strategy_tags/trigger_price/invalid_price` 不能丢失。
  4. 增加兼容性回归测试。
- **验证方式**：
  - `pytest tests/test_tradeflow_universe.py tests/test_t002_discovery.py tests/test_t003_fund_flow_anomaly.py -q` 通过。
  - `pytest tests/test_tradeflow_*.py -q` 通过。
- **代码标注要求**：`# [M-011] universe_compat_fix`

### M-008: 数据源健康检查与 fallback 可观测性（P1）
- **描述**：建立数据源健康检查，持续观察 AKShare、cn_astock、BaoStock、yfinance、公告/资金/LHB 等源的成功率、延迟和失败原因。
- **优先级**：P1
- **状态**：done — commit 4876cc8
- **执行约束**：
  - 只跑小样本 smoke，不扫全市场。
  - 不保存 cookie/API key。
  - live 测试必须可跳过。
- **实现要点**：
  1. 新增数据源 health check 脚本，默认测试 2-3 只样例股。
  2. 输出每个 endpoint 的 `OK/FAILED/STALE/NOT_QUERIED`。
  3. 记录 fallback 是否发生以及最终 vendor。
  4. 生成 `docs/data_source_health/YYYY-MM-DD.md`。
- **验证方式**：
  - mock 主源失败时 health report 显示 fallback 命中。
  - `pytest tests/test_*provider*.py -q` 或新增专门测试通过。
- **代码标注要求**：`# [M-008] data_source_health`

### M-009: TradeFlow 前端观察池面板（P2）
- **描述**：在前端增加 TradeFlow 观察池/计划展示：候选、策略标签、触发价、失效价、过滤原因、是否需要 TA。
- **优先级**：P2
- **状态**：blocked
- **前置条件**：`M-003`、`M-005` 数据结构稳定。
- **执行约束**：
  - 先做只读展示，不加一键交易。
  - 不展示强买卖词。
- **实现要点**：
  1. 后端提供只读 API 或复用现有 plan 输出。
  2. 前端表格支持按策略/状态/need_deep_ta 筛选。
  3. 每个候选可展开 evidence。
- **验证方式**：
  - 前端类型检查通过。
  - mock 数据展示不溢出、不误显示强动作。
- **代码标注要求**：`# [M-009] tradeflow_dashboard`

### M-010: 飞书/通知链路人工确认版（P2）
- **描述**：把夜间日报、盘中触发、盘后复盘接入飞书，但第一阶段只生成草稿/本地预览，人工确认后再发。
- **优先级**：P2
- **状态**：blocked
- **前置条件**：孟确认 webhook、推送格式和频率。
- **执行约束**：
  - 不自动推送真实 webhook。
  - 不发送强买卖词。
  - 不泄露 API key/token。
- **实现要点**：
  1. 统一通知 payload schema。
  2. 支持 `--dry-run` 输出本地 markdown/json。
  3. 真实发送需显式开关。
- **验证方式**：
  - dry-run 生成 payload。
  - 未配置 webhook 时不报错、不发送。
- **代码标注要求**：`# [M-010] notification_dry_run`

---

## UI. TradeFlow 前端工作台任务池（2026-05-31 新增）

> 目标：把已经建好的 TradeFlow 候选池、分层、证据门禁、盘中 Observe、TA 队列和盘后 Review 露给用户。第一阶段只做可解释展示和人工触发入口，不做交易指令，不自动调用高成本 LLM。

### UI-001: TradeFlow 只读 API 查询层（P1）
- **描述**：新增 TradeFlow 前端所需的只读 API，让前端能查询每日计划、候选池、候选详情、盘中 Observe、TA 队列、盘后 Review 和数据健康状态。
- **优先级**：P1
- **状态**：done — commit e6d3269
- **前置条件**：`S-009`、`M-011`、`T-007`、`M-007` 已完成。
- **执行约束**：
  - 只读接口优先，不触发 TA，不跑全市场扫描，不调用 LLM。
  - 不写生产 `tradingagents.db`，除非复用既有只读查询。
  - 不读取、打印、返回任何 API key/token。
  - 不返回强买卖词；动作字段保持 `OBSERVE/WAIT_TRIGGER/NEED_DEEP_TA/REMOVE_FROM_WATCH`。
- **建议接口**：
  1. `GET /v1/tradeflow/daily-plan?date=YYYY-MM-DD`
  2. `GET /v1/tradeflow/candidates?date=YYYY-MM-DD&tier=A&need_deep_ta=true`
  3. `GET /v1/tradeflow/candidates/{symbol}?date=YYYY-MM-DD`
  4. `GET /v1/tradeflow/observe?date=YYYY-MM-DD`
  5. `GET /v1/tradeflow/ta-queue?date=YYYY-MM-DD`
  6. `GET /v1/tradeflow/review?date=YYYY-MM-DD`
  7. `GET /v1/tradeflow/data-health`
- **实现要点**：
  1. 新增 Pydantic response schema，字段覆盖 `tier/composite_score/strategy_tags/trigger_price/invalid_price/need_deep_ta/observe_state/tradeflow_data_completeness/missing_data_fields/game_balance/why_deep_ta/why_not_deep_ta`。
  2. API 可先从 TradeFlow SQLite 表、DailyPlan JSON、review markdown/对象或已有服务层读取；没有数据时返回空列表和明确 metadata，不报 500。
  3. 候选详情必须包含 evidence refs、bull/bear/policy/fund case、数据缺失字段、资金单位校验状态。
  4. 为 UI 提供 summary 聚合：总候选数、A/B/C 数量、need_deep_ta 数量、平均完整度、事件源状态。
- **验证方式**：
  - 新增 API 单测覆盖空数据、候选数据、详情数据、review 数据。
  - `pytest tests/test_tradeflow_*.py -q` 通过。
  - `pytest tests/test_api*.py -q` 或新增对应 API 测试通过。
- **代码标注要求**：`# [UI-001] tradeflow_api`

### UI-002: 前端 TradeFlow 页面骨架与候选池表格（P1）
- **描述**：新增 `/tradeflow` 页面和侧边栏入口，展示 TradeFlow 摘要卡片和候选池表格，让用户能直接看到“今天哪些票值得看、为什么进池、是否需要深度 TA”。
- **优先级**：P1
- **状态**：done — commit edf69d9
- **前置条件**：`UI-001` 完成；若 API 未完成，可用 typed mock/fallback，但必须保留真实 API 接入路径。
- **执行约束**：
  - 只做只读展示，不做一键交易。
  - 不展示“立即买入/重仓/清仓”等强动作词。
  - 设计风格保持现有后台工具风格：信息密度高、少装饰、不做营销式 hero。
- **实现要点**：
  1. `frontend/src/services/api.ts` 增加 TradeFlow API client。
  2. `frontend/src/types/index.ts` 增加 TradeFlow 类型。
  3. 新增 `frontend/src/pages/TradeFlow.tsx` 或组件目录，包含摘要栏和候选表格。
  4. 候选表格字段：symbol/name/tier/composite_score/strategy_tags/trigger_price/invalid_price/tradeflow_data_completeness/need_deep_ta/observe_state/action。
  5. 支持按 tier、策略标签、need_deep_ta、observe_state 筛选。
  6. loading/empty/error 状态要完整，空候选时解释“无候选/数据未生成/接口无数据”。
- **验证方式**：
  - `npm test` 或现有前端测试命令通过。
  - 新增 sidebar/router 测试，确认 `/tradeflow` 入口存在。
  - 表格最长字段不溢出，移动端可横向滚动或响应式折叠。
- **代码标注要求**：`// [UI-002] tradeflow_page`

### UI-003: 候选详情抽屉：证据、博弈、门禁解释（P1）
- **描述**：为 TradeFlow 候选池增加右侧详情抽屉，展示入池理由、多空博弈、证据质检、门禁解释和后续升级条件。
- **优先级**：P1
- **状态**：done — commit 9ee6aea
- **前置条件**：`UI-002` 完成。
- **执行约束**：
  - 详情抽屉只解释信号质量，不生成投资建议。
  - 所有强结论必须绑定 evidence refs；缺证据时显示“缺失/未查询/失败”。
- **实现要点**：
  1. 展示策略命中：VCP、PULLBACK_SUPPORT、EVENT_CATALYST、POLICY_VERSION、NARRATIVE_QUALITY、FUND_FLOW_ANOMALY。
  2. 展示 `bull_case/bear_case/policy_case/fund_flow_case/game_balance/resonance_count`。
  3. 展示证据门禁：完整度、缺失字段、资金流单位是否校验、事件源状态、LHB/融资融券状态。
  4. 展示 `why_deep_ta/why_not_deep_ta/what_to_upgrade/missing_evidence_for_upgrade`。
  5. 抽屉内提供“加入自选/复制摘要/查看 TA 报告路径”的只读或轻操作入口；真实触发 TA 放到后续任务。
- **验证方式**：
  - 组件测试覆盖有证据、缺证据、风险拥挤、数据失败四类候选。
  - 文案不包含强买卖词。
- **代码标注要求**：`// [UI-003] tradeflow_candidate_drawer`

### UI-004: 盘中 Observe 与 TA 队列只读面板（P2）
- **描述**：在 TradeFlow 页面增加“盘中观察”和“TA 队列”Tab，展示候选状态、触发价/失效价、TA 调度状态和成本门禁原因。
- **优先级**：P2
- **状态**：done — commit fa75389
- **前置条件**：`M-005`、`M-006`、`UI-001` 完成。
- **执行约束**：
  - 第一版只读，不自动触发 TA。
  - 不调用 DeepSeek，不绕过 TA 预算门禁。
- **实现要点**：
  1. 展示 `WAITING/TRIGGERED/EXPIRED/INVALIDATED` 状态。
  2. 展示 current price/trigger price/invalid price 的相对距离；无实时价格时明确显示“实时行情不可用”。
  3. 展示 `deep_ta_status/deep_ta_dispatch_reason/deep_ta_model/deep_ta_report_path/deep_ta_position_context`。
  4. 展示今日 TA 调度数量、上限、剩余额度。
- **验证方式**：
  - mock 状态机数据下展示正确。
  - 无实时行情时不误判触发。
- **代码标注要求**：`// [UI-004] tradeflow_observe_queue`

### UI-005: 盘后 Review 前端页面（P2）
- **描述**：把 M-007 盘后复盘结果展示到前端，按策略、层级、候选日期统计命中率、误报率、失效率和后续调参建议。
- **优先级**：P2
- **状态**：done — commit 401fb7c
- **前置条件**：`M-007` 补修完成，`UI-001` review API 可用。
- **执行约束**：
  - 只做策略质量复盘，不做个股买卖建议。
  - 0% 必须显示为 `0.0%`，不能显示 `N/A%`。
- **实现要点**：
  1. 展示总体概览：总候选、有评分、无数据、命中、误报、失效、平均次日/3日/5日收益。
  2. 展示策略命中率表：策略、总数、命中、误报、无数据、失效、命中率、误报率。
  3. 展示分层统计：A/B/C 层表现。
  4. 展示移除理由和调参建议。
- **验证方式**：
  - 覆盖 0%、100%、N/A 三种展示。
  - 前端测试通过。
- **代码标注要求**：`// [UI-005] tradeflow_review_page`

### UI-006: 数据源健康前端面板（P2）
- **描述**：把 M-008/T-007 的数据源健康状态露出到前端，帮助用户判断候选池质量问题来自策略还是数据源。
- **优先级**：P2
- **状态**：done — commit 52d5f5e
- **前置条件**：`M-008`、`T-007`、`UI-001` 完成。
- **执行约束**：
  - 不暴露 API key、cookie、内部 token。
  - 不主动发起高频 live 数据探测；只展示后端已有 health/check 结果。
- **实现要点**：
  1. 展示行情、资金流、龙虎榜、公告、事件源、融资融券等源的 `OK/PARTIAL/FAILED/STALE/NOT_QUERIED`。
  2. 展示最近更新时间、失败原因、fallback vendor。
  3. 在 TradeFlow 候选页顶部显示数据健康摘要和异常提示。
  4. 与候选详情中的证据缺失字段联动。
- **验证方式**：
  - mock OK/PARTIAL/FAILED/STALE 状态均能正确展示。
  - 没有健康数据时显示“未生成健康检查”，而不是报错。
- **代码标注要求**：`// [UI-006] tradeflow_data_health`

### UI-007: TradeFlow 被过滤候选可追溯展示（P1）
- **描述**：候选池生成后，前端必须展示"哪些股票被过滤、为什么没进候选池"。解决用户看到 daily plan/输入股票池与候选表数量不一致时，无法判断是未显示、未落库还是被策略过滤的问题。
- **优先级**：P1
- **状态**：done
- **前置条件**：`UI-001` 到 `UI-006` 完成；`feat(tradeflow): add candidate discovery trigger` 已完成。
- **背景**：
  - 当前 `tradeflow_daily_plans` 只保存最终候选摘要，`tradeflow_candidates` 只保存入池股票。
  - Discovery 接口返回 `filtered` 列表，但刷新页面后过滤原因丢失。
  - 用户误以为 “daily plan 有两条/输入了多只股票，所以前端应显示多只”，实际很多票已被流动性、数据缺失或无策略命中过滤。
- **执行约束**：
  - 不触发深度 TA。
  - 不调用 LLM。
  - 不输出强买卖词。
  - 不写 `tradingagents.db` schema；如需持久化，优先写 TradeFlow 自有 SQLite 表或 daily plan metadata。
  - 不暴露 API key/token/内部路径。
- **实现要点**：
  1. 后端新增或扩展 TradeFlow 只读接口，能按 `date` 返回最近一次 Discovery/Plan 的 `filtered` 列表。
     - 推荐方案：新增 `tradeflow_filtered_symbols` 表，字段至少包含 `trade_date/symbol/name/source/reason/run_id/created_at`。
     - 备选方案：把 filtered 写入 `daily_plans.metadata.filtered`，但需避免 metadata 过大。
  2. `run_discovery_scan()` 在生成候选池时持久化 filtered 结果，并在新一轮同日期扫描前清理/覆盖该日期旧 filtered。
  3. 前端 TradeFlow 候选池页在“生成候选池”后显示过滤摘要：
     - 总扫描数、入池数、过滤数。
     - 过滤分类：流动性差、数据缺失、无策略命中、其他。
  4. 前端增加“被过滤”折叠表或 Tab，字段：
     - `symbol/name/source/reason`
     - 可按过滤原因筛选。
  5. 空候选时，页面要区分：
     - “尚未生成候选池”
     - “已扫描，但全部被过滤”
     - “接口/数据源失败”
  6. 文案说明 `daily_plans` 是计划记录，不等同于候选股票数量。
- **验收方式**：
  - 构造 3 只股票：1 只入池、1 只流动性差、1 只无策略命中；前端显示 1 只候选 + 2 只过滤原因。
  - 刷新页面后，被过滤列表仍可查询。
  - 同一日期二次生成时，过滤列表不会重复累积旧数据。
  - API 测试覆盖：filtered 持久化、按日期查询、同日覆盖、空数据返回。
  - 前端构建通过：`npm run build`。
  - 后端测试通过：`pytest tests/test_ui001_tradeflow_api.py tests/test_tradeflow_*.py -q`。
- **代码标注要求**：`# [UI-007] tradeflow_filtered_trace` / `// [UI-007] tradeflow_filtered_trace`

### UI-008: TradeFlow 候选字段规范化与名称回填（P1）
- **描述**：修复 TradeFlow 候选池里股票名称为空、事件源候选代码缺少交易所后缀、部分字段显示兜底不足的问题，确保前端候选池、详情抽屉、TA 队列和后续 TA 调度使用同一套规范字段。
- **优先级**：P1
- **状态**：done — commit b1fcc68
- **前置条件**：`UI-001` 到 `UI-006` 完成；建议在 `UI-007` 后执行，便于 filtered 列表也复用同一套字段规范化逻辑。
- **已观察到的问题**：
  - `tradeflow_candidates.name` 大量为空，前端名称列只能显示空白。
  - 事件源候选出现无交易所后缀代码，例如 `002600`、`000338`、`688617`，而 TA/行情/报告链路通常需要 `002600.SZ`、`688617.SH` 这种规范代码。
  - 手动输入、事件源、自选股、持仓、昨日观察池的 `symbol/name/source/universe_sources` 来源不同，字段完整度不一致。
  - `daily_plan.candidates_json` 与 `tradeflow_candidates` 可能字段不一致，前端不同 Tab 容易显示出不同值。
- **执行约束**：
  - 不触发深度 TA。
  - 不调用 LLM。
  - 不输出强买卖词。
  - 不改 `tradingagents.db` schema。
  - 不暴露 API key/token/内部路径。
- **实现要点**：
  1. 新增统一字段规范化 helper，例如 `normalize_tradeflow_symbol()` / `resolve_tradeflow_name()`：
     - `6/5/9` 开头默认补 `.SH`。
     - `0/2/3` 开头默认补 `.SZ`。
     - `4/8` 开头按北交所规则补 `.BJ` 或保持既有项目规范。
     - 已带 `.SH/.SZ/.BJ` 的不得重复补。
  2. 股票名称回填优先级：
     - universe/source 自带名称。
     - 生产库自选股/持仓名称。
     - 项目股票映射缓存，如 `_load_cn_stock_map()` / reverse map 或 dataflow 内已有 stock map。
     - 仍找不到时前端显示 `--`，不留空白。
  3. 在候选生成链路统一应用：
     - `build_universe()` 输出前规范化 symbol/name。
     - `event_source` 或 event_symbols 入池时规范化 symbol。
     - `evaluate_symbol()` 创建 Candidate 前确保 `symbol/name` 已规范。
     - `save_candidate()` 落库前兜底规范化，防止旧调用绕过。
  4. 在 API 层兜底：
     - `_row_to_candidate_item()` 如果 `name` 为空，返回 `--` 或通过映射回填。
     - `daily_plan` JSON 转候选时也走同一套字段补齐。
  5. 前端显示兜底：
     - 候选表、详情抽屉、Observe、TA 队列、filtered 列表都不要显示空名称。
     - 代码列统一展示规范 symbol。
  6. 做一次字段审计，确认以下字段在候选表/详情/API/前端一致：
     - `symbol/name/source/universe_sources/strategy_tags/primary_strategy/tier/composite_score/score/tradeflow_data_completeness/created_at/updated_at`。
- **验收方式**：
  - 构造事件源输入 `002600`，落库和 API 返回为 `002600.SZ`，名称可回填或显示 `--`。
  - 构造 `688617`，落库和 API 返回为 `688617.SH`。
  - 自选股/持仓已有名称时，候选池显示真实名称。
  - 手动输入带后缀代码不会被重复补后缀。
  - `daily-plan` API 与 `candidates` API 对同一候选的 `symbol/name/strategy_tags/tier/composite_score` 保持一致。
  - 前端构建通过：`npm run build`。
  - 后端测试通过：`pytest tests/test_ui001_tradeflow_api.py tests/test_tradeflow_universe.py tests/test_tradeflow_*.py -q`。
- **代码标注要求**：`# [UI-008] tradeflow_field_normalization` / `// [UI-008] tradeflow_field_normalization`

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

### Q-001: 自动开发运行态文件出库与工作区清洁修复（P0）
- **描述**：修复 `.zai_quota_state.json` 被 Git 跟踪导致每次额度检查后工作区变脏、自动开发无法继续领取任务的问题。
- **优先级**：P0
- **状态**：done — 当前巡检提交
- **背景**：
  - 2026-05-30 巡检发现 `.zai_quota_state.json` 被提交进仓库，运行后变为 modified。
  - `scripts/auto_dev_loop.sh --dry-run` 因工作区不干净退出，阻断后续自动开发。
- **执行约束**：
  - 保留本地 quota 状态文件，但从 Git 跟踪中移除。
  - 加入 `.gitignore`。
  - 不删除用户本地运行状态。
- **实现要点**：
  1. `git rm --cached .zai_quota_state.json`。
  2. `.gitignore` 增加 `.zai_quota_state.json`。
  3. 验证 `git status` 不再因额度检查文件变脏。
  4. `./scripts/auto_dev_loop.sh --dry-run` 能继续选择下一项 ready 任务。
- **验证方式**：
  - `git status --short` 不出现 `.zai_quota_state.json` modified。
  - `./scripts/auto_dev_loop.sh --dry-run` 不被 quota state 文件阻断。
- **代码标注要求**：`# [Q-001] quota_state_gitignore`

---

## A. 当前进行中

### F-001: TA研报执行层修复（P0-P2共10项）
- **描述**：修复002837英维克报告暴露的10个问题，涉及止损价解析、估值sanity check、Opportunity Score封顶、未持仓语义、Buy/Risk Level冲突、舆情VERDICT矛盾、入场区间误识别、打法标签、A股做空措辞、Evidence Coverage保守化
- **优先级**：P0（1-3）+ P1（4-7）+ P2（8-10）
- **验收**：重跑002837报告，10项全部通过
- **状态**：已派发OpenCode执行

### R-001: 2026-05-26 自动开发收尾巡检（P0） ✅ 已完成
- **描述**：对昨日 OpenClaw/OpenCode 自动开发结果做收口验收，先处理未提交 diff、临时文件、脚本可靠性和 G-008 接线问题，再允许进入新功能开发。
- **优先级**：P0
- **状态**：done
- **背景**：
  - 当前分支相对 `myfork/local/tradingagents-custom` 为 `ahead 13, behind 1`，不适合直接 push。
  - 工作区存在 `G-007` 未提交代码改动、`tests/test_g007_fund_lhb_provenance.py` 未跟踪测试文件，以及多份 `G-008` 临时补丁/备份文件。
  - `G-008` 已有 commit，但当前测试文件 `test_g008_valuation.py` 缺少 `Optional` import，且生产代码从 `state.get("raw_evidence")` 取值，可能无法读取实际的 `state.metadata.raw_evidence`。
- **执行约束**：
  - 不改 `tradingagents/prompts/`。
  - 不写入生产 `tradingagents.db`。
  - 不 push / PR / merge。
  - 不自动跑全市场扫描或深度 TA。
  - 每个子任务最多修复 2 轮，超过标记 `NEEDS_HUMAN`。
- **验收方式**：
  - `git status -sb` 只剩预期代码/文档变更，无临时补丁/备份文件。
  - `G-007/G-008/AUTO-001` 各自有明确 PASS/FAIL 结论。
  - 相关测试命令全部通过，或失败项明确标注为 pre-existing。
  - `docs/DEVLOG.md` 与 `docs/TASKS.md` 状态一致。

### AUTO-002: 自动开发闭环 v1.3 可靠性补修（P0） ✅ 已完成
- **描述**：修复 `scripts/auto_dev_loop.sh` 仍可能误提交临时文件、commit 后污染工作区、review 误判等问题，让自动开发脚本可以安全进入日常试运行。
- **优先级**：P0
- **状态**：done
- **完成记录**：commit 7ea198d
- **背景**：
  - `AUTO-001 v1.1/v1.2` 已修复 `grep` 退出、OpenCode exit code、`codex review -o` 等问题，但仍存在剩余风险。
  - 脚本当前在 PASS 后 `git add -A`，可能把 OpenCode 产生的根目录临时脚本、备份文件、测试草稿全部提交。
  - 脚本 commit 后再回填 DEVLOG commit hash，会导致 commit 后工作区重新变脏。
  - `codex review` 输出只用关键词粗判，仍需记录完整 review 文件路径和退出码。
- **实现要点**：
  1. 只允许提交任务声明中允许的路径，或至少排除根目录临时文件、`*.backup`、`*_original.py`、`*_fixed.py`、`patch_*.py`、`__pycache__/`。
  2. DEVLOG/TASKS 更新必须在 commit 前完成；禁止 commit 后再改文件。
  3. PASS 后提交前再次运行 `git status --porcelain` 并输出将提交文件清单。
  4. `--dry-run` 在干净工作区能选中真实开发任务，不选 `T-000/R-001` 等巡检任务。
  5. 失败时不得自动提交任何文件，只写明 `NEEDS_HUMAN`。
- **验证方式**：
  - `./scripts/auto_dev_loop.sh --dry-run` 在干净工作区可正常输出候选任务。
  - 构造根目录临时文件后，脚本不得把临时文件纳入 commit。
  - `bash -n scripts/auto_dev_loop.sh` 通过。
  - 不实际调用 OpenCode 的情况下，至少完成 dry-run 与静态检查。

### G-009: G-007 资金流与龙虎榜口径校验收尾提交（P0） ✅ 已完成
- **描述**：把 `G-007` 当前未提交实现收口成干净 commit，并清理无关临时文件，避免任务文档显示 done 但代码仍悬在工作区。
- **优先级**：P0
- **状态**：done
- **背景**：
  - `docs/DEVLOG.md` 已记录 G-007 完成，但相关代码仍在未提交 diff 中。
  - 相关改动涉及 `readiness_score.py`、`cn_akshare_provider.py`、`data_collector.py`、`tests/test_readiness_score.py`、`tests/test_g007_fund_lhb_provenance.py`。
- **执行约束**：
  - 只收口 G-007，不夹带 G-008 临时文件。
  - 不改 `tradingagents/prompts/`。
  - 不写生产数据库。
  - 不 push。
- **实现要点**：
  1. 保留 G-007 必要代码与测试，删除或忽略与 G-007 无关的临时文件。
  2. 确认 `LHB_NOT_QUERIED / LHB_NORMAL_NO_DATA / LHB_FAILED / LHB_HAS_DATA` 四态可被 raw_evidence 和文本 fallback 正确识别。
  3. 确认个股资金、板块资金、新闻转述资金不会混用为强证据。
  4. `docs/TASKS.md` 与 `docs/DEVLOG.md` 的 G-007 状态保持一致。
- **验证方式**：
  - `pytest tests/test_g007_fund_lhb_provenance.py tests/test_readiness_score.py -q` 通过。
  - `git diff --stat` 中只包含 G-007 预期文件。
  - 形成单独 commit，建议消息：`fix: G-007 fund flow and LHB provenance gate`。

### G-010: G-008 估值 sanity check 补修与生产接线（P0） ✅ 已完成
- **描述**：修复 `G-008` 已提交实现的测试缺口和 raw_evidence 接线问题，确保估值旧价污染真的能在生产报告中触发。
- **优先级**：P0
- **状态**：done
- **背景**：
  - 当前 `G-008` commit 只改了 `risk_manager.py`，独立测试文件在根目录且 pytest 收集失败。
  - `risk_manager.py` 中 G-008 从 `state.get("raw_evidence")` 取值，但系统实际 raw evidence 位于 `state.get("metadata", {}).get("raw_evidence")`。
  - 价格解析直接按 CSV 第 5 列读取，缺少 header 校验和错误分支测试。
- **执行约束**：
  - 不改 prompt。
  - 不写生产数据库。
  - 不扩大为估值模型重构，只修旧价污染拦截。
  - 临时根目录测试/补丁文件必须清理或移入正式测试目录。
- **实现要点**：
  1. 新增正式测试 `tests/test_g008_valuation_sanity.py`，不要保留根目录 `test_g008_valuation.py`。
  2. 把 raw evidence 读取改为优先 `state.metadata.raw_evidence`，兼容旧格式 `state.raw_evidence`。
  3. CSV 解析按 header 查找 `close`/`收盘` 列，无法识别时保守 fallback，不抛异常。
  4. 最新价 80、估值价 30 时触发 `valuation_mismatch=True`；最新价 80、估值价 78 时不触发。
  5. 触发后必须进入最终报告执行质检区和 `metadata.valuation_mismatch`。
- **验证方式**：
  - `pytest tests/test_g008_valuation_sanity.py -q` 通过。
  - `pytest tests/test_readiness_score.py -q` 通过。
  - `pytest tests/test_g001_three_layer.py tests/test_readiness_score.py -q` 通过。
  - 根目录不存在 `test_g008_valuation.py`、`patch_g008.py`、`risk_manager_*` 等临时文件。

### V-001: 600584 数据真实性端到端验收（P1）
- **描述**：在 G-007/G-008 收口后，用 600584.SH 做一次低成本验收，确认当天行情补齐、raw evidence、资金/LHB 口径、估值旧价拦截都能在报告或结果 metadata 中看见。
- **优先级**：P1
- **状态**：blocked
- **前置条件**：`G-009`、`G-010`、`N-002`、`N-003` 完成。
- **执行约束**：
  - 默认只跑低成本/轻量路径；不要用 DeepSeek。
  - 不自动全市场扫描。
  - 如果需要完整 TA 深度分析，先输出预计模型与成本风险，等待用户确认。
- **验收方式**：
  - 行情证据包含最新交易日或明确标记 `STALE/FAILED`。
  - `metadata.raw_evidence.stock_data`、`fund_flow_individual`、`lhb` 可追溯。
  - 主力资金接口失败时，下游不得把新闻转述资金当强证据。
  - 估值价与行情价偏离超过 20% 时，报告必须出现估值口径冲突提示。

### V-002: 夜间自动开发验收报告与候选样本回放（P1）
- **描述**：为夜间自动开发增加固定样本回放和验收报告，第二天可以直接看到候选池质量、测试结果、review 结论和剩余风险。
- **优先级**：P1
- **状态**：done — commit ba62fc2
- **前置条件**：`M-002`、`T-002`、`T-003`、`M-008` 完成。
- **执行约束**：
  - 不调用外部 LLM。
  - 不跑全市场扫描。
  - 不写生产数据库。
  - 报告不得包含 API key、完整 token 日志或敏感环境变量。
- **实现要点**：
  1. 新增或扩展夜间验收脚本，读取最近一次 `docs/task_runs/`、TradeFlow 样本 fixtures 和测试日志。
  2. 输出 `docs/auto_dev_reports/YYYY-MM-DD.md`，包含完成任务、commit、测试、Codex review 摘要、候选样本回放和后续 ready 队列。
  3. 固定回放至少覆盖：VCP 命中、事件催化、资金异动、流动性过滤、无策略命中、风险降权。
  4. 如果 ready 队列为空，报告必须明确提示“任务池不足”，避免夜间 cron 空转。
- **验证方式**：
  - `python scripts/summarize_auto_dev_runs.py --date YYYY-MM-DD --dry-run` 或对应脚本可运行。
  - 构造 fixture 后报告包含候选样本回放摘要。
  - 不泄露敏感变量。
- **代码标注要求**：`# [V-002] nightly_acceptance_report`

### V-003: TradeFlow 端到端候选质量回放验收（P1）
- **描述**：基于固定样本回放一条完整 TradeFlow 链路，验证候选池从 universe、事件源、资金异动、证据门禁、候选分层到 deep TA 门控的最终输出是否一致。
- **优先级**：P1
- **状态**：done — commit 92a3873
- **前置条件**：`S-009`、`M-011`、`T-007` 完成。
- **执行约束**：
  - 不调用外部 LLM。
  - 不跑全市场扫描。
  - 不写生产数据库。
  - 不输出强交易建议。
- **实现要点**：
  1. 建立固定 fixtures：强共振候选、技术单信号候选、未校验资金候选、事件源失败候选、高风险候选、昨日观察延续候选。
  2. 回放 `run_discovery()` 或 `generate_daily_plan()`，输出稳定的候选层级、过滤原因、missing evidence、event_source status、deep_ta gate reason。
  3. 生成本地验收报告路径，例如 `docs/tradeflow_acceptance/YYYY-MM-DD.md`。
  4. 报告明确列出“可进入 TA / 仅观察 / 淘汰”的原因。
- **验证方式**：
  - 固定 fixtures 输出稳定。
  - 未校验资金候选不得进入 A 层或触发 deep TA。
  - 事件源失败时报告显示 `FAILED/PARTIAL`，不当成无事件。
  - `pytest tests/test_tradeflow_*.py -q` 通过。
- **代码标注要求**：`# [V-003] tradeflow_acceptance_replay`

---

## N. 2026-05-28 夜间任务包（结合 Simon 仓库实践）

> 执行顺序：`N-001` → `N-002` → `N-003` → `N-004` → `V-001`。  
> 总原则：吸收 Simon 的数据源/事件源/结构化经验，但不照搬整套框架；继续保留本项目的 raw_evidence、强动作门禁、Buy/Risk Level 和自动审核闭环。

### N-001: TradeFlow P1-2 事件源接入候选扫描（P0）
- **描述**：将已完成的 `event_source.fetch_daily_events()` 接入 `generate_daily_plan()` / candidate evaluation，让公告、回购、评级事件能自动进入候选池并触发 `EVENT_CATALYST`。
- **优先级**：P0
- **状态**：done — commit 3b232a3
- **背景**：
  - 上一阶段事件源已完成：`tradingagents/tradeflow/event_source.py` + `tests/test_event_source.py`，commit `b5131cd`。
  - 当前事件源只完成数据获取，尚未接入 `candidate_engine.py` / `plan_runner.py` 的候选扫描。
- **执行约束**：
  - 不做全市场扫描，只在指定 symbols、自选池、事件池小范围内运行。
  - 不自动调用 TA 深度分析，只设置 `need_deep_ta`。
  - 不输出买入/卖出强建议。
  - 不调用 DeepSeek。
  - 不写生产 `tradingagents.db`；测试使用临时 DB。
- **实现要点**：
  1. `generate_daily_plan()` 新增开关，例如 `use_event_source: bool = False`，默认关闭，避免无意拉取大量公告。
  2. 当 `use_event_source=True` 时，按 `trade_date` 调用 `fetch_daily_events(YYYYMMDD)`，得到 `{symbol: [title, ...]}`。
  3. 构建 universe 时必须把事件池股票纳入候选范围，但仅限当日事件池，不做全 A 股扩展。
  4. 每只股票只消费自己的事件文本，禁止把全局 `news_texts` 串给所有股票。
  5. 候选 evidence 中记录事件来源：`event_source=akshare_notice/buyback/rating` 或至少记录 `event_titles`。
  6. CLI/脚本如已有入口，可加 `--use-event-source`；没有入口则先只做 Python API。
- **验证方式**：
  - 新增或更新测试：mock `fetch_daily_events()` 返回 `{"002138": ["回购进展"], "600519": ["减持评级"]}`。
  - 指定 symbols 为空时，事件池股票可进入 universe。
  - 多只股票各自只消费自己的事件，不串票。
  - 无事件时不触发 `EVENT_CATALYST`。
  - `pytest tests/test_event_source.py tests/test_tradeflow_*.py -q` 通过。
- **代码标注要求**：`# [N-001] event_source_plan_integration`

### N-002: cn_astock 数据源验收与 fallback 接入（P0）
- **描述**：参考 Simon `a-stock-data` 的数据源分层，把本地已接入的 `cn_astock` provider 从“已注册”推进到“可验收 fallback 源”。
- **背景**：
  - `b8e0023` 已新增 `tradingagents/dataflows/providers/cn_astock_provider.py` 并注册到 provider registry。
  - Simon `a-stock-data` 的优势是直连腾讯/东财/新浪/财联社/巨潮等 HTTP 数据源，适合补 AKShare 不稳定和当天行情滞后的问题。
- **优先级**：P0
- **状态**：done — commit a0770bb
- **执行约束**：
  - 不把 `cn_astock` 直接设为唯一主源；先作为 fallback / cross-check。
  - 不改 prompt。
  - 不写生产数据库。
  - 测试优先 mock HTTP，少量 live smoke 必须可跳过。
- **实现要点**：
  1. 为 `CnAstockProvider` 增加正式测试，覆盖 symbol 规范化、registry 注册、CSV 字段、realtime quote JSON、失败降级。
  2. 在 TradeFlow `_fetch_price_data()` 中加入 fallback 顺序：`cn_akshare → cn_astock → yfinance`。
  3. fallback 命中时，候选 evidence 或 metadata 记录 `price_source=cn_astock`。
  4. 对腾讯实时行情字段做 sanity check：`price/open/high/low/previous_close/amount/source` 必须存在；不得把成交额误写为成交量。
  5. 不要求今晚接完全部 28 端点，先确保行情/实时估值/公告能力可被系统稳定识别。
- **验证方式**：
  - `pytest tests/test_cn_astock_provider.py -q` 通过。
  - `pytest tests/test_tradeflow_*.py -q` 通过。
  - 构造 `cn_akshare` 失败时，TradeFlow 能用 `cn_astock` 价格数据继续评估。
- **代码标注要求**：`# [N-002] cn_astock_fallback`

### N-003: cn_astock raw_evidence 溯源接入（P1）
- **描述**：把 `cn_astock` 的数据源身份纳入 raw_evidence / 数据质量状态，让报告能看到数据来自腾讯、东财、巨潮、财联社等具体来源。
- **背景**：
  - 本项目已经有 `G-006 raw_evidence`，但新增 `cn_astock` 后需要补 vendor/source/as_of/unit/status。
  - Simon 的 `a-stock-data` 强在端点多，但我们必须把端点结果纳入可审计证据体系。
- **优先级**：P1
- **状态**：done — commit dd63bca
- **执行约束**：
  - 不新建大规模历史行情库。
  - 不保存 cookie、token、API key。
  - 不改生产 DB schema；优先写入 `metadata.raw_evidence`。
- **实现要点**：
  1. DataCollector 或 provider route 返回中标注实际 vendor：`akshare` / `cn_astock` / `sina` / `tencent` / `eastmoney` / `cninfo`。
  2. 当 `cn_astock` 作为 fallback 命中时，`raw_evidence.stock_data.source` 不得仍显示 `akshare`。
  3. 公告/龙虎榜/估值类数据如来自 cn_astock，必须保留 `source_url` 或可追溯字段。
  4. 报告底部数据源摘要能显示 `source=cn_astock` 或更细粒度供应商。
- **验证方式**：
  - mock akshare 失败 + cn_astock 成功，断言 `raw_evidence.stock_data.vendor/source` 为 cn_astock。
  - `pytest tests/test_g006_raw_evidence_snapshot.py tests/test_g007_fund_lhb_provenance.py -q` 通过。
- **代码标注要求**：`# [N-003] cn_astock_raw_evidence`

### N-004: A股特化信号标签（政策/游资/解禁）先入 TradeFlow（P1）
- **描述**：吸收 Simon `TradingAgents-astock` 中“政策分析师/游资追踪师/解禁监控师”的设计，但第一阶段不新增 LLM Agent，先在 TradeFlow 中做 deterministic 信号标签。
- **背景**：
  - Simon 的 3 个 A 股特化角色方向正确，但直接新增 Agent 会增加成本和 prompt 复杂度。
  - 对本项目更稳的路径是先把政策、游资、解禁作为候选池标签和风险标签。
- **优先级**：P1
- **状态**：done — commit 7fb535f
- **执行约束**：
  - 不新增 LLM Agent。
  - 不改 `tradingagents/prompts/`。
  - 只做规则标签，不输出强买卖动作。
- **实现要点**：
  1. 从事件标题/公告类型中提取 `POLICY_CATALYST`、`HOT_MONEY_LHB`、`LOCKUP_RISK`、`BUYBACK_EVENT`、`RATING_CHANGE` 标签。
  2. 标签进入 Candidate `strategy_tags` 或 `risk_flags`，并写入 evidence。
  3. `LOCKUP_RISK` 只能降权或标风险，不能作为入场理由。
  4. `HOT_MONEY_LHB` 必须区分龙虎榜 `HAS_DATA` 与 `NOT_QUERIED`，复用 G-007 状态。
- **验证方式**：
  - 构造回购公告 → `BUYBACK_EVENT` / bullish event evidence。
  - 构造风险提示/解禁公告 → `risk_flags` 包含 `LOCKUP_RISK` 或对应风险标签。
  - 构造龙虎榜未查询 → 不生成 `HOT_MONEY_LHB` 强信号。
  - `pytest tests/test_tradeflow_*.py -q` 通过。
- **代码标注要求**：`# [N-004] astock_signal_tags`

### N-005: 最终执行层 schema 化方案与最小实现（P2）
- **描述**：参考 Simon 的 `schemas.py`，为本项目最终执行层增加结构化输出的最小 schema，减少模型自由文本导致的动作字段冲突。
- **优先级**：P2
- **状态**：done — commit 4ec69ec / dd717ca
- **执行约束**：
  - 不重写全部 report。
  - 不改 prompt 作为唯一方案。
  - 第一阶段只做 schema/helper/test，可不接生产路径。
- **实现要点**：
  1. 定义最小执行 schema：`action`、`buy_level`、`risk_level`、`trigger_price`、`invalid_price`、`position_context`、`data_quality_flags`、`evidence_refs`。
  2. 提供从现有 `format_execution_block()` / metadata 构造 schema 的 helper。
  3. schema 只允许 `WAIT/ENTER/HOLD/REDUCE/EXIT` 动作枚举。
  4. 不改变现有报告文本输出，只新增结构化副产物。
- **验证方式**：
  - 新增测试覆盖未持仓 WAIT、条件入场 ENTER、已持仓 HOLD/REDUCE/EXIT。
  - 字段冲突时 schema 标记 `data_quality_flags`，不强行给高等级动作。
- **代码标注要求**：`# [N-005] execution_schema`

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
- **状态**：done — commit TBD
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
- **状态**：done — 由 `b5131cd` + `3b232a3` 完成事件源与候选扫描接入
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
- **状态**：done — commit 84813df
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
- **状态**：done — commit 7c825f4
- **实现要点**：
  - 使用近 5-20 日个股资金流，校验单位和字段来源。
  - 个股资金与板块资金分开记录，禁止混用。
  - 资金异动只做加分项，不单独触发强结论。
- **验证方式**：
  - 单位未校验时不输出高置信资金信号。
  - 板块资金与个股资金字段分离。

### T-006: 事件源自动接入 Discovery / Daily Plan（P1）
- **描述**：把已完成的事件源模块纳入小范围 Discovery 和 Daily Plan，使公告、回购、评级等事件能在不全市场扫描的前提下自动进入候选池。
- **优先级**：高
- **状态**：done — commit 6944b2c
- **前置条件**：`N-001`、`N-004`、`T-002` 完成。
- **执行约束**：
  - 不做全市场扫描；只在指定 symbols、自选池、持仓池、手动行业池或事件源返回的小范围内运行。
  - 事件只作为候选入池和评分证据，不直接生成交易建议。
  - 所有事件必须按 symbol 隔离，缺 symbol 的全局事件要保守处理。
- **实现要点**：
  1. 在 Discovery / Daily Plan 入口增加 `use_event_source` 或等价开关，默认保持向后兼容。
  2. 接入 `fetch_daily_events()`，生成 `event_overrides` 并传入候选评估链路。
  3. Daily Plan 输出事件来源、标题摘要、事件类型、去重结果和证据引用。
  4. 事件源失败时标记 `FAILED` 或 `STALE`，不得把无事件当作无风险。
- **验证方式**：
  - mock 事件源返回 symbol 事件时，对应股票进入候选 universe。
  - symbol 不匹配时不串票。
  - 事件源失败时输出可观测状态，系统仍能处理手动 symbols。
  - `pytest tests/test_tradeflow_*.py -q` 通过。
- **代码标注要求**：`# [T-006] event_source_discovery`

### T-007: 修复 T-006 事件源底层失败可观测性（P1）
- **描述**：修复事件源子 fetch 函数吞掉 AKShare/network 异常后，`fetch_daily_events_detailed()` 仍把结果标成 `OK` 的问题。
- **优先级**：P1
- **状态**：done — commit 0fc5552
- **前置条件**：`T-006`、`M-008` 完成。
- **执行约束**：
  - 不做全市场扫描。
  - 不把“源失败”解释为“无事件”。
  - 不泄露接口错误中的敏感信息。
- **问题来源**：
  - `docs/task_runs/T-006-20260530-013347/codex-review-round1.txt` 中 P2 finding。
- **实现要点**：
  1. 让 `fetch_notice_events`、`fetch_buyback_events`、`fetch_rating_events` 的失败状态可被详细聚合函数感知。
  2. 可以增加内部 detailed helper，或让子函数返回结构化状态；保持旧 `fetch_daily_events()` 兼容。
  3. `fetch_daily_events_detailed()` 必须区分：全部成功但无事件 = `OK`；部分失败 = `STALE/PARTIAL`；全部失败 = `FAILED`。
  4. Discovery / Daily Plan metadata 展示失败源和精简错误。
- **验证方式**：
  - mock 一个子 fetch 抛异常时，metadata 不得显示纯 `OK`。
  - mock 全部子 fetch 失败时，status=`FAILED`。
  - mock 成功但空事件时，status=`OK` 且 count=0。
  - `pytest tests/test_t006_event_source_discovery.py tests/test_event_source_integration.py -q` 通过。
- **代码标注要求**：`# [T-007] event_source_failure_status`

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

### G-007: 资金流与龙虎榜数据源口径校验（P1） ✅ 已完成
- **描述**：修复"主力资金报告说缺失，但下游仍把主力净流出当强证据"的口径冲突；龙虎榜必须区分未查询、无触发、查询失败、有数据。
- **背景**：600584.SH 报告中 `smart_money_report` 明确写近20日主力资金 ProxyError、龙虎榜未触发，但研究经理/交易员仍多次使用"主力资金净流出"作为强证据，且将 5月22日龙虎榜事实与 5月26日未查询混在一起。
- **优先级**：P1
- **状态**：done
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
- **状态**：done（已由 `G-010` 补修并通过正式测试）
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
