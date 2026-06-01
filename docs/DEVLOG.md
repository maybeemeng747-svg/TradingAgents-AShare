# 修改日志

> 每次代码修改后必须更新此文件，保持连续性。

---

## 2026-06-01 | DATA-001: A股数据源能力目录与 fallback 矩阵

- **执行者**：OpenCode
- **任务**：DATA-001 — 建立本项目统一的数据源能力目录，明确每个 vendor/endpoint 能提供什么字段、适用场景、freshness、限流风险和 fallback 顺序
- **修改文件**：
  - `tradingagents/dataflows/source_catalog.py` — 新建：[DATA-001] source_catalog
    - `DataType` 枚举（16 种）：QUOTE / OHLCV / FUND_FLOW / BOARD_FUND_FLOW / LHB / MARGIN_TRADING / NOTICE / REPORT / RATING / NEWS / GLOBAL_NEWS / FINANCIALS / INSIDER / HOT_STOCKS / ZT_POOL / REALTIME_QUOTES
    - `Freshness` 枚举（6 级）：REALTIME / INTRADAY / DAILY / DELAYED / STALE / UNKNOWN
    - `RateLimitRisk` 枚举（4 级）：LOW / MEDIUM / HIGH / UNKNOWN
    - `SourceCapability` 数据类：vendor / endpoint / data_type / fields / unit / freshness / rate_limit_risk / fallback_priority / known_gaps / is_primary / notes
    - `can_be_primary` 属性：缺少字段/单位未知/freshness 为 UNKNOWN 或 STALE 的数据源不得标为 primary
    - `get_sources_for_type(data_type)` → 按 fallback_priority 排序的 SourceCapability 列表
    - `get_primary_source(data_type)` → 该类型的 primary source（通过 can_be_primary 校验）
    - `get_fallback_chain(data_type)` → vendor 名称列表
    - `get_all_data_types()` / `get_catalog_summary()` / `get_vendor_capabilities(vendor)` → 辅助查询
    - `validate_catalog()` → 校验 primary 合规性、空字段、重复条目
    - 覆盖 3 个 vendor（cn_akshare / cn_astock / cn_baostock）× 16 种 data_type，共 35 个 source entry
  - `tests/test_data_source_catalog.py` — 新建，66 个测试覆盖：
    - `TestDataTypeEnum` (3): 全部值、字符串构造、非法值
    - `TestFreshnessEnum` (1): 全部值
    - `TestRateLimitRiskEnum` (1): 全部值
    - `TestSourceCapability` (10): 默认值、to_dict、can_be_primary 各条件（无字段/无单位/quote/fund_flow/非金融/unknown/stale/board_fund_flow）
    - `TestGetSourcesForType` (16): ohlcv/fund_flow/lhb/notice/realtime_quotes/news/global_news/financials/report/insider/zt_pool/hot_stocks/invalid/sorted/board_fund_flow/string_input
    - `TestGetPrimarySource` (6): ohlcv/fund_flow/realtime_quotes/notice/news/invalid
    - `TestGetFallbackChain` (4): ohlcv/fund_flow/lhb/order
    - `TestGetAllDataTypes` (3): list/no_dup/contains_key
    - `TestGetCatalogSummary` (1): structure
    - `TestGetVendorCapabilities` (4): akshare/astock/baostock/unknown
    - `TestValidateCatalog` (2): no_issues、primary_has_fields
    - `TestPrimaryIntegrity` (4): stale/fields/unit/unique_per_type
    - `TestCatalogCompleteness` (4): not_empty/all_key_primary/vendor_endpoint/unique_priority
    - `TestDataSpecificChecks` (7): ohlcv_primary_vendor/realtime_primary/fund_flow_unit/lhb_force/notice_cninfo/report_eastmoney/astock_pe_pb
- **测试结果**：66 passed (DATA-001)；165 passed (readiness + raw_evidence + fund_lhb_provenance)；0 failed
- **关键逻辑**：
  - 每个 source entry 包含完整元数据：vendor、endpoint、data_type、fields、unit、freshness、rate_limit_risk、fallback_priority、known_gaps、is_primary、notes
  - `can_be_primary` 属性执行 5 项检查：有字段列表、金融类数据有单位、freshness 不是 UNKNOWN、freshness 不是 STALE
  - OHLCV 覆盖 3 个 vendor 共 6 条 entry（cn_akshare × 3 + cn_astock × 2 + cn_baostock × 1）
  - 实时行情覆盖 3 个 vendor × 3 entry：新浪(primary)、东财(akshare fallback)、腾讯(astock)
  - 资金流/龙虎榜/公告/研报/新闻等每种 data_type 都有明确的 primary 和 fallback chain
  - `validate_catalog()` 自动检测：primary 不合规、空字段、重复条目
- **执行边界**：未改 `tradingagents/prompts/`、未写生产 `tradingagents.db`、未做全市场扫描、未触发 TA/LLM 调用

---

## 2026-06-01 | TF-OBS-001: TradeFlow 盘中观察执行器与信号落库

- **执行者**：OpenCode
- **任务**：TF-OBS-001 — 在 TF-DATE-001 之后，把现有 `run_observe_check()` 状态机接入真实执行链路，定时读取当日生效候选、拉取实时价格、更新观察状态并写入信号表
- **修改文件**：
  - `tradingagents/tradeflow/observe_runner.py` — 新建：[TF-OBS-001] tradeflow_observe_runner
    - `ObserveRunResult` 数据类：checked/triggered/invalidated/waiting/skipped/signals_written/errors/skipped_reason/run_time/details
    - `_is_trading_day()`：调用 trade_calendar 判断交易日
    - `_fetch_realtime_quotes()`：通过 `route_to_vendor("get_realtime_quotes")` 获取实时行情
    - `_load_active_candidates()`：按 effective_trade_date 加载活跃候选
    - `_save_signal()`：将 Signal 写入 tradeflow_signals 表
    - `_build_signal_from_snapshot()`：将 ObserveSnapshot 转为 Signal（observe_triggered/observe_invalidated/observe_check/observe_expired）
    - `run_observe()`：核心执行函数——读取候选、拉取实时行情、执行 observe_check、更新候选状态、写入信号；非交易日返回明确 skip reason；无实时行情跳过
  - `api/services/tradeflow_service.py` — [TF-OBS-001]
    - `run_observe_check()`：API 层调用 observe_runner，返回执行结果
    - `get_data_health()`：新增 `latest_observe_check_time` 和 `latest_signal_time` 字段
    - `get_observe()`：从最新 signal 中提取 `current_price` 和 `trigger_reason` 填充 observe item
  - `api/main.py` — [TF-OBS-001]
    - 新增 `POST /v1/tradeflow/observe/run?date=YYYY-MM-DD` 端点
    - 导入 `run_observe_check` 服务函数
  - `frontend/src/types/index.ts` — [TF-OBS-001]
    - 新增 `TradeFlowObserveRunResponse` 类型
    - `TradeFlowDataHealthResponse` 新增 `latest_observe_check_time` 和 `latest_signal_time`
  - `frontend/src/services/api.ts` — [TF-OBS-001]
    - 新增 `runTradeFlowObserve()` API 方法
  - `frontend/src/pages/TradeFlow.tsx` — [TF-OBS-001]
    - `ObserveTable` 新增"执行观察"按钮、执行结果状态显示（触发/失效/等待/跳过/信号数）
    - Observe 表新增"触发原因"列
    - DataHealth 面板新增"最新观察"和"最新信号时间"显示
  - `tests/test_tf_obs_001_observe_runner.py` — 新建，35 个测试覆盖：
    - `TestObserveRunResult` (2): 默认值、自定义值
    - `TestIsTradingDay` (3): 工作日、周六、周日
    - `TestFetchRealtimeQuotes` (2): 空 symbols、mock provider
    - `TestLoadActiveCandidates` (4): 无候选、有候选、inactive 过滤、effective_trade_date 跨日查找
    - `TestSaveSignal` (2): 基本保存、持久化验证
    - `TestBuildSignalFromSnapshot` (4): TRIGGERED/INVALIDATED/WAITING/EXPIRED 信号类型
    - `TestRunObserveTriggered` (1): 价格突破触发价→TRIGGERED + signal 落库
    - `TestRunObserveInvalidated` (1): 价格跌破失效价→INVALIDATED
    - `TestRunObserveWaiting` (1): 价格在触发价和失效价之间→WAITING
    - `TestRunObserveNoQuotes` (1): 无实时行情→skipped
    - `TestRunObserveNonTradingDay` (2): 周六/周日→skip with reason
    - `TestRunObserveNoCandidates` (1): 空库→skip with reason
    - `TestRunObserveMultipleSymbols` (1): 3 个 symbol 独立处理
    - `TestRunObservePreservesTriggerCount` (1): 增量触发计数保留
    - `TestRunObserveTerminalStatePreserved` (2): TRIGGERED/INVALIDATED 终态保持
    - `TestRunObserveEffectiveTradeDate` (1): 跨日 effective_trade_date 查找
    - `TestSignalEvidenceCompleteness` (1): signal evidence 包含全部字段
    - `TestRunObserveWithExactTriggerPrice` (1): 恰好触发价→TRIGGERED
    - `TestRunObserveWithExactInvalidPrice` (1): 恰好失效价→INVALIDATED
    - `TestRunObserveNoTriggerPrice` (1): 无触发价→WAITING
    - `TestRunObserveDetails` (2): 详情填充、跳过详情
- **测试结果**：35 passed (TF-OBS-001)；212 passed (tradeflow 全部)；0 failed；前端构建通过
- **关键逻辑**：
  - `run_observe()` 读取 `effective_trade_date = today` 且 `status='active'` 的候选
  - 通过 `route_to_vendor("get_realtime_quotes")` 批量获取实时行情（支持 mock 注入）
  - 每个候选创建 `ObserveTracker`，恢复已有的 observe_state/trigger_count
  - 调用 `run_observe_check()` 执行状态机检查，更新候选并写入 signal
  - 非交易日直接返回 skip reason，不拉实时行情
  - 无实时行情的候选标记为 skipped，不执行状态检查
  - 信号表 `tradeflow_signals` 记录每次检查的完整证据：price_evidence/trigger_reason/quote_source 等
  - API 端点 `POST /v1/tradeflow/observe/run` 支持手动触发
  - 前端 Observe Tab 展示"执行观察"按钮和执行结果
  - Data Health 展示最新观察检查时间和最新信号时间
- **执行边界**：未触发 TA、未调用 LLM、未输出强买卖词、未改 `tradingagents/prompts/`、未改生产 `tradingagents.db` schema

## 2026-06-01 | H-001: 昊天雷达 v0 数据模型与信号分类

- **执行者**：OpenCode
- **任务**：H-001 — 新增政策意图识别的基础数据结构，把政策/事件/产业/公司证据统一归档为可评分的 `MandateSignal`，为后续昊天意志评分提供输入
- **修改文件**：
  - `tradingagents/tradeflow/mandate_signal.py` — 新建：[H-001] mandate_signal_model
    - `SourceLevel` 枚举：CENTRAL / STATE_COUNCIL / MINISTRY / LOCAL_GOV / EXCHANGE / SOE_GROUP / COMPANY_NOTICE / MEDIA（8 级来源权威层级）
    - `MandateEventType` 枚举：POLICY_DOCUMENT / MEETING_SIGNAL / INDUSTRY_PLAN / SUBSIDY_SUPPORT / PROCUREMENT_ORDER / LICENSE_APPROVAL / M_AND_A_RESTRUCTURING / SOE_REFORM / BUYBACK_RATING（9 类信号）
    - `MandateSignal` 数据类：symbol / topic / title / source / source_level / date / evidence_text / evidence_url / event_type / direction / confidence / policy_tags / industry_tags / company_role / raw_refs；自动置信度封顶、低证据信号上限 0.3
    - `classify_source_level()`：基于标题和来源文本的正则匹配，识别来源级别
    - `classify_event_type()`：基于标题关键词的事件类型分类
    - `compute_confidence()`：综合来源权重和证据完整度计算置信度；缺失任一关键字段封顶 0.3；全部缺失返回 0.0
    - `event_item_to_mandate_signal()`：从 `EventItem` 转换为 `MandateSignal`，保留原始 title/source/date/event_type/direction/detail
    - `convert_event_items()`：批量转换 `items_by_symbol`，按 symbol 隔离，空 symbol 和空标题排除
    - `validate_mandate_signal()`：校验信号完整性，返回问题列表
  - `tests/test_h001_mandate_signal.py` — 新建，95 个测试覆盖：
    - `TestSourceLevelEnum` (4): 全部值、数量、字符串构造、非法值
    - `TestMandateEventTypeEnum` (2): 全部值、数量
    - `TestClassifySourceLevel` (17): 党中央/国务院/国办/工信部/证监会/发改委/上交所/国资委/省级/市级/公司公告/东财/财联社/未知/空字符串/来源参数/优先级
    - `TestClassifyEventType` (12): 各事件类型 + 空标题 + 无匹配默认值
    - `TestComputeConfidence` (8): 高权威完整证据/低权威/缺标题/缺日期/缺来源/全缺失/范围/层级排序
    - `TestMandateSignal` (17): 默认值/置信度封顶（有证据/无证据）/负数/低证据上限/高证据放行/枚举构造/属性访问/权重/权威判断/to_dict
    - `TestEventItemToMandateSignal` (10): 国务院/工信部/地方/公司公告/媒体事件/原始数据保留/空标题/缺字段/方向覆盖
    - `TestSymbolIsolation` (4): 无交叉污染/空 symbol 排除/空标题排除/空输入
    - `TestValidateMandateSignal` (6): 合法信号/缺 symbol/缺 title/缺 source/缺 date/缺 source_level
    - `TestSourceLevelWeights` (4): 最高/最低/层级排序/全部有值
    - `TestH001FourEventTypes` (9): 四类事件（国务院/部委/地方/公司）集成验证、无交叉污染、置信度排序、raw_refs 完整、全部合法
    - `TestNoEvidenceLowConfidence` (3): 无证据封顶/媒体<中央/公司<国务院
- **测试结果**：95 passed (H-001)；225 passed (H-001 + tradeflow 全部)；0 failed
- **关键逻辑**：
  - `MandateSignal` 从 `EventItem` 转换时不丢失原始 title/source/date/direction/detail
  - 缺失 title/source/date 任一的信号置信度封顶 0.3，不得按高级别政策处理
  - 来源级别通过正则匹配自动分类：中央/国务院 → 部委 → 交易所/国资 → 地方 → 公司公告 → 媒体
  - 所有信号按 symbol 隔离，不串票
  - 置信度公式：来源权重 × 0.6 + 证据完整度 × 0.4
- **执行边界**：未触发 TA、未调用 LLM、未输出强买卖词、未改 `tradingagents/prompts/`、未改生产 `tradingagents.db` schema

---

## 2026-06-01 | DATA-P0-603629 任务释放：TA 关键数据源补强

- **执行者**：Codex
- **背景**：用户提供的 603629.SH 最新 TA 报告暴露出关键数据源缺失：个股资金流 `ConnectionError`、龙虎榜未 force 查询、换手率/量比/板块资金/公告原文/复权口径不稳定；同时底部数据源可用性将“主力资金报告文本存在”误判为“主力资金数据可用”。
- **参考**：结合 SimonLin1212 `a-stock-data` 的数据源思路，吸收 direct endpoint、fallback、东财限流防封、来源契约和 freshness 标注；不照搬其整套框架。
- **变更**：
  - `docs/TASKS.md` 新增 `DATA-P0-603629`（P0 ready）：TA A股关键数据源补强与假可用修复。
  - 当前优先队列调整为 `DATA-P0-603629` 第一，排在 `TF-DATE-001` 前面。
- **后续**：OpenClaw/OpenCode 下一轮应优先领取 `DATA-P0-603629`。完成后用 603629.SH 复跑，重点验收主力资金、龙虎榜、换手率/量比、公告原文、复权口径和 confidence 降级。

---

## 2026-06-01 | TradeFlow 日期语义任务释放

- **执行者**：Codex
- **背景**：用户确认非交易日生成候选池、下一个交易日观察是合理主流程，例如 2026-05-31 生成候选池，2026-06-01 盘中观察。当前 TradeFlow 将 `trade_date` 同时用于候选生成日期、计划生效日期和观察日期，导致 6月1日 Observe 查不到 5月31日生成的候选。
- **变更**：
  - `docs/TASKS.md` 新增 `TF-DATE-001`（P0 ready）：TradeFlow 日期语义拆分与非交易日计划生效。
  - `docs/TASKS.md` 新增 `TF-OBS-001`（P0 proposed）：盘中观察执行器与信号落库，依赖 `TF-DATE-001`。
  - 当前优先队列调整为先跑 `TF-DATE-001`，再考虑 Observe runner 和 `H-001` 昊天雷达。
- **后续**：OpenClaw/OpenCode 下一轮应优先领取 `TF-DATE-001`。`TF-DATE-001` 完成前，不建议继续扩展 Observe/TA 队列，否则会继续被日期模型卡住。

---

## 2026-06-01 | VLM-001 补修：自选备注保存问题

- **执行者**：OpenCode
- **任务**：修复自选备注保存的三个问题：重复股票 notes 不更新、前端备注编辑缺少保存/取消按钮、`update_watchlist_notes` 的 clear 参数未暴露到 API
- **修改文件**：
  - `api/services/watchlist_service.py` — [VLM-001] watchlist_notes_protection：`add_watchlist_items_with_notes()` 遇到重复股票时，根据新/旧 notes 情况自动合并（空→写入，非空→用 ｜ 分隔追加）；新增 `_merge_notes()` 辅助函数
  - `api/main.py` — [VLM-001] watchlist_notes_protection：`WatchlistNotesUpdate` 增加 `clear: bool = False` 字段；`update_watchlist_notes` endpoint 传递 `clear` 参数到 service
  - `frontend/src/pages/Portfolio.tsx` — [VLM-001] watchlist_notes_protection：备注编辑状态下显示"保存"和"取消"按钮；Enter 仍可保存；Escape 取消；失焦不自动保存
- **验证**：`pytest tests/test_vlm*.py tests/test_ui001*.py -q` 58 passed；`npm run build` 零错误

---

## 2026-05-31 | VLM-001: 自选截图解析 v2 — 候选表格识别

- **执行者**：OpenCode
- **任务**：VLM-001 — 升级 VLM 截图解析，支持识别"自选候选表格"
- **修改文件**：
  - `api/services/vlm_position_parser.py` — [VLM-001] 新增 `WATCHLIST_TABLE_PROMPT`、`parse_watchlist_table_image()`、`_parse_watchlist_response()`、`_build_watchlist_notes()`、`_extract_6digit_code()`、`_to_int()` 等函数
  - `api/services/watchlist_service.py` — [VLM-001] `add_watchlist_item()` 增加 `notes` 参数，新增 `add_watchlist_items_with_notes()` 批量带备注添加
  - `api/main.py` — [VLM-001] `POST /v1/portfolio/parse-image` 增加 `mode` 参数（position/watchlist），新增 `POST /v1/watchlist/batch-notes` 端点，添加 `Form` import
  - `frontend/src/types/index.ts` — [VLM-001] 新增 `WatchlistTableItem`、`WatchlistTableParseResponse` 类型
  - `frontend/src/services/api.ts` — [VLM-001] `parsePositionImage()` 增加 `mode` 参数，新增 `addToWatchlistBatchNotes()` 方法
  - `frontend/src/pages/Portfolio.tsx` — [VLM-001] 上传截图优先尝试 watchlist 模式，新增候选表格预览与确认添加 UI
  - `frontend/src/components/TrackingBoardPanel.tsx` — 适配 `parsePositionImage()` 新签名
  - `tests/test_vlm_position_parser.py` — [VLM-001] 新增 11 个 watchlist table 相关测试用例
- **验证**：`pytest tests/test_vlm*.py -q` 16 passed；`npm run build` 零错误

---

## 2026-05-31 | T-000: 自动开发巡检基线脚本

- **执行者**：OpenCode
- **任务**：T-000 — 为 OpenClaw 自动开发建立每日/每轮固定巡检清单
- **修改文件**：
  - `scripts/preflight_check.sh` — 新建：[T-000] 自动开发巡检基线脚本
    - 7 个巡检模块：Git 状态、变更摘要与敏感风险标记、测试健康、数据库安全、Token/API 消耗风险、运行产物清理、文档一致性
    - Exit code: 0=通过, 1=有风险需人工确认, 2=严重风险禁止继续
    - 支持 `--skip-tests`（跳过测试）和 `--quiet`（减少输出）
    - 敏感文件检测：prompts/、tradingagents.db、.env、eval_results/、logs/、模型配置、scheduler/
    - LLM 调用模式扫描：检测未提交变更中新增的 ChatOpenAI/langchain/anthropic/zhipuai 调用
    - 高成本模型引用检测：deepseek/gpt-4/claude-3
    - Scheduler 进程检测与 OpenClaw cron 扫描
    - 测试模块感知：TradeFlow 改动运行 tradeflow 测试、执行层改动运行 readiness/G001 测试
  - `docs/TASKS.md` — T-000 状态从 `ready` 更新为 `done`
- **验证**：`bash -n scripts/preflight_check.sh` 无语法错误；脚本在当前工作区运行输出正确报告，exit code 1（scheduler 运行中为 WARN 级别）

---

## 2026-05-31 | UI-007 完成：TradeFlow 被过滤候选可追溯展示

- **执行者**：OpenCode
- **背景**：候选池生成后，Discovery 返回的 filtered 列表刷新后丢失，用户无法追溯股票被过滤的原因（流动性差/数据缺失/无策略命中）。前端候选数量与 daily plan/输入股票池不一致时，无法区分是未显示、未落库还是被策略过滤。
- **变更**：
  - `tradingagents/tradeflow/candidate_engine.py`：新增 `tradeflow_filtered_symbols` 表（字段：trade_date, symbol, name, source, reason, run_id, created_at）；新增 `save_filtered_symbols()` 和 `get_filtered_symbols()` 函数。
  - `tradingagents/tradeflow/discovery.py`：`run_discovery()` 在候选池生成后持久化 filtered 结果，同日期扫描自动覆盖旧数据。
  - `api/services/tradeflow_service.py`：新增 `get_filtered()` 服务函数，按日期查询 filtered 列表及过滤分类统计。
  - `api/tradeflow_schemas.py`：新增 `TradeFlowFilteredItem` 和 `TradeFlowFilteredResponse` Pydantic 模型。
  - `api/main.py`：新增 `GET /v1/tradeflow/filtered?date=YYYY-MM-DD` 只读接口。
  - `frontend/src/types/index.ts`：新增 `TradeFlowFilteredItem` 和 `TradeFlowFilteredResponse` TypeScript 类型。
  - `frontend/src/services/api.ts`：新增 `getTradeFlowFiltered()` API 方法。
  - `frontend/src/pages/TradeFlow.tsx`：新增"被过滤"Tab（FilteredTab 组件），含过滤摘要（总扫描/入池/过滤/入池率）、分类统计、被过滤股票表格（可按原因筛选）、空状态区分（未生成/全部过滤/接口失败）。
  - `tests/test_ui001_tradeflow_api.py`：新增 10 个测试（TestFilteredSymbols 5个 + TestFilteredAPI 3个 + TestDiscoveryPersistsFiltered 2个），覆盖持久化、查询、覆盖、空数据、集成场景。
- **测试结果**：42 tests passed（API 测试），130 tests passed（全部 tradeflow 测试），前端构建通过。
- **执行边界**：未触发深度 TA，未调用 LLM，未输出强买卖词，未改 `tradingagents.db` schema。

---

## 2026-05-31 | 总架构路线图与任务分层收口

- **执行者**：Codex
- **背景**：用户要求不要再零散追加想法，而是总结系统目标架构，把后续任务按阶段布置，避免自动开发方向漂移。
- **变更**：
  - 新增 `docs/ROADMAP.md`，定义七层目标架构：数据层、昊天雷达、TradeFlow 候选引擎、TA 深度分析、执行跟踪、复盘评估、前端看板、自动开发层。
  - 明确两类候选池：`Mandate Pool`（政策左侧/中线）与 `Technical/Trade Pool`（技术确认/做T）。
  - 把 `H-002` 到 `H-006` 从 `ready` 调整为 `proposed`，当前只保留 `H-001` 为 ready，避免自动开发越过依赖链。
  - 在 `docs/TASKS.md` 中加入 Roadmap 引用和任务新增原则：新想法必须归入路线图层级，不能随意插队。
- **执行边界**：只改文档和任务池，不改代码，不触发分析，不调用 LLM API。

---

## 2026-05-31 | H 系列任务释放：昊天雷达 / 政策左侧埋伏

- **执行者**：Codex
- **背景**：用户明确 TradeFlow 主策略不应只偏短线技术/做 T，而要研究 A 股政策版本、中线左侧埋伏和“昊天意志”方向；技术信号应作为执行确认，不作为第一性筛选。
- **变更**：
  - 更新 `docs/TASKS.md` 当前优先队列，把下一轮自动开发切到 `H-001` 到 `H-006`。
  - 新增 `H. 昊天雷达 / 政策左侧埋伏任务池`：
    1. `H-001` 昊天雷达 v0 数据模型与信号分类。
    2. `H-002` 政策连续性与级别权重评分。
    3. `H-003` 产业链受益路径与标杆候选映射。
    4. `H-004` 左侧埋伏评分与候选类型分流。
    5. `H-005` 前端昊天候选池视图。
    6. `H-006` 回放评估与反证机制。
- **执行边界**：先做保守规则与证据链，不调用 LLM，不触发 TA，不输出强买卖词，不凭空揣测。

---

## 2026-05-31 | UI-008 任务释放：TradeFlow 字段规范化与名称回填

- **执行者**：Codex
- **背景**：检查当前 TradeFlow 候选数据发现，`tradeflow_candidates.name` 大量为空，事件源候选还存在无交易所后缀代码（如 `002600`、`688617`），会影响前端展示、详情抽屉、TA 队列和后续 TA 调度一致性。
- **变更**：
  - 更新 `docs/TASKS.md` 当前优先队列，新增 `UI-008` 为 P1 ready。
  - 明确修复范围：symbol 规范化、名称回填、API/前端字段兜底、候选表与 daily plan 字段一致性审计。
- **执行边界**：不触发深度 TA，不调用 LLM，不改生产 `tradingagents.db` schema。

---

## 2026-05-31 | UI-007 任务释放：TradeFlow 被过滤候选可追溯

- **执行者**：Codex
- **背景**：排查发现 `daily_plans` 是计划记录，`tradeflow_candidates` 只保存入池候选；未入池股票的过滤原因只在 Discovery 返回瞬间存在，刷新后不可追溯，容易让用户误判为前端漏显示。
- **变更**：
  - 更新 `docs/TASKS.md` 当前优先队列，新增 `UI-007` 为 P1 ready。
  - 明确验收目标：候选池生成后展示并持久化 filtered 列表，刷新后仍可查看过滤原因。
- **执行边界**：不触发深度 TA，不调用 LLM，不输出强买卖词，不改生产 `tradingagents.db` schema。

---

## 2026-05-31 | TradeFlow 候选池生成入口

- **执行者**：Codex
- **背景**：TradeFlow 前端只能查看候选结果，无法从 UI 触发候选池筛选，导致用户看到空池后只能回命令行运行脚本。
- **修改文件**：
  - `api/main.py` — 新增 `POST /v1/tradeflow/discovery`，支持按日期、手动股票池、TopN、自选股、持仓、事件源生成候选。
  - `api/services/tradeflow_service.py` — 新增 `run_discovery_scan()`，只运行 TradeFlow Discovery 并保存候选，不触发深度 TA，不调用 LLM。
  - `frontend/src/services/api.ts`、`frontend/src/types/index.ts` — 新增 Discovery 请求/响应类型与 API 客户端方法。
  - `frontend/src/pages/TradeFlow.tsx` — 候选池页新增“生成候选池”面板，可输入股票池、选择 TopN、自选股、持仓和事件源。
  - `tradingagents/tradeflow/universe.py` — 兼容当前生产库的持仓/自选字段，避免 universe 读取为空。
  - `tests/test_ui001_tradeflow_api.py`、`tests/test_tradeflow_universe.py` — 增加 Discovery 保存候选与当前生产库字段兼容回归测试。
- **测试结果**：`pytest tests/test_ui001_tradeflow_api.py tests/test_tradeflow_universe.py tests/test_tradeflow_*.py -q` 162 passed；`npm run build` 通过。

---

## 2026-05-31 | TradeFlow 打不开排查：重启后端并修复默认 DB 路径

- **执行者**：Codex
- **问题**：
  1. 8000 端口 uvicorn 仍是旧进程，`/v1/tradeflow/*` 未注册，请求被 SPA 兜底返回 HTML。
  2. TradeFlow API 默认数据库路径误指向 `api/tradeflow.db`，实际数据库在项目根目录 `tradeflow.db`。
  3. 根目录旧版 `tradeflow.db` 缺少 `composite_score` 等新字段，候选池查询直接 500。
- **修改文件**：
  - `api/services/tradeflow_service.py` — 默认 TradeFlow DB 路径改为项目根目录；候选池/Observe/TA 队列查询兼容旧表结构。
  - `tests/test_ui001_tradeflow_api.py` — 增加默认 DB 路径与旧表结构回归测试。
- **测试结果**：`pytest tests/test_ui001_tradeflow_api.py tests/test_tradeflow_*.py -q` 160 passed；`npm run build` 通过。

---

## 2026-05-31 | UI-006 验收补修：TradeFlow 前端参数与数据健康字段对齐

- **执行者**：Codex
- **背景**：UI-001 后端端点使用 `date=YYYY-MM-DD`，UI-002~UI-006 前端请求误用 `trade_date`，会导致 TradeFlow 候选池、详情、Observe、TA 队列和 Review 请求 422。
- **修改文件**：
  - `frontend/src/services/api.ts` — TradeFlow 6 个带日期请求统一改为 `date` 参数。
  - `api/tradeflow_schemas.py` — `DataHealthSource` 补齐 `status` 与 `fallback_vendor` 字段。
  - `api/services/tradeflow_service.py` — 数据健康接口为各 TradeFlow 表返回 `OK/FAILED` 状态、更新时间和备用源字段。
  - `tests/test_ui001_tradeflow_api.py` — 增加数据健康状态字段断言。
- **测试结果**：`pytest tests/test_ui001_tradeflow_api.py tests/test_tradeflow_*.py -q` 158 passed；`npm run build` 通过。
- **约束遵守**：只读 API 与前端展示修复，不触发 TA、不调用 LLM、不写生产 DB。

---

## 2026-05-31 | UI-001: TradeFlow 只读 API 查询层

- **执行者**：OpenCode
- **任务**：UI-001 — 新增 TradeFlow 前端所需的 7 个只读 API 端点
- **修改文件**：
  - `api/tradeflow_schemas.py` — 新增 Pydantic 响应模型（CandidateItem/Detail/DailyPlan/Candidates/Observe/TAQueue/Review/DataHealth）
  - `api/services/tradeflow_service.py` — 新增只读数据查询层（从 tradeflow SQLite 读取 candidates/plans/signals）
  - `api/main.py` — 新增 7 个 GET 端点：
    - `GET /v1/tradeflow/daily-plan?date=YYYY-MM-DD`
    - `GET /v1/tradeflow/candidates?date=YYYY-MM-DD&tier=A&need_deep_ta=true`
    - `GET /v1/tradeflow/candidates/{symbol}?date=YYYY-MM-DD`
    - `GET /v1/tradeflow/observe?date=YYYY-MM-DD`
    - `GET /v1/tradeflow/ta-queue?date=YYYY-MM-DD`
    - `GET /v1/tradeflow/review?date=YYYY-MM-DD`
    - `GET /v1/tradeflow/data-health`
  - `tests/test_ui001_tradeflow_api.py` — 29 个单测覆盖空数据、候选数据、详情、Observe、TA 队列、Review、DataHealth、禁止词校验
- **测试结果**：`pytest tests/test_ui001_tradeflow_api.py` 29 passed，`pytest tests/test_tradeflow_*.py` 129 passed
- **约束遵守**：只读、不触发 TA、不调用 LLM、不写生产 DB、不返回 API key、不返回强买卖词

---

## 2026-05-31 | TradeFlow 前端工作台任务释放

- **背景**：用户确认候选池当前前端不可见，需要把 TradeFlow 的候选、证据、Observe、TA 队列、盘后 Review 和数据健康展示到 UI。
- **变更**：
  1. 更新 `docs/TASKS.md` 顶部当前优先队列，清理已完成的 S/M/T/V/M-007 ready 队列。
  2. 新增 `UI-001` 到 `UI-006` 六个前端/接口任务，全部设为 `ready`，避免夜间自动开发空转。
  3. 采用 `UI-*` 编号，避免与既有 `F-001: TA研报执行层修复` 冲突。
- **新队列**：
  - `UI-001` TradeFlow 只读 API 查询层（P1）
  - `UI-002` 前端 TradeFlow 页面骨架与候选池表格（P1）
  - `UI-003` 候选详情抽屉：证据、博弈、门禁解释（P1）
  - `UI-004` 盘中 Observe 与 TA 队列只读面板（P2）
  - `UI-005` 盘后 Review 前端页面（P2）
  - `UI-006` 数据源健康前端面板（P2）
- **执行边界**：第一阶段只读展示为主，不自动触发 TA，不调用 LLM，不跑全市场扫描，不输出强买卖词。

---

## 2026-05-30 | T-007 Round2: 修复 Codex Review P1 — `_detailed` helper 绕过原始 fetch 的异常吞没

- **执行者**：OpenCode
- **任务**：T-007 Round2 — Codex review 发现 `_fetch_*_detailed()` 包装的是已经吞掉异常的公共 `fetch_*_events()` 函数，导致生产路径下 AKShare/network 失败时 `_detailed` 仍标为 `OK`
- **修改文件**：
  - `tradingagents/tradeflow/event_source.py` —
    1. 将 `fetch_notice_events`/`fetch_buyback_events`/`fetch_rating_events` 的核心逻辑提取为 `_fetch_notice_events_raw()`/`_fetch_buyback_events_raw()`/`_fetch_rating_events_raw()`（可抛异常）
    2. 公共 `fetch_*_events()` 变为 thin wrapper：`try: return _raw() except: return []`
    3. `_fetch_*_detailed()` 改为调用 `_raw()` 而非公共函数，确保 AKShare/network 异常能被捕获
  - `tests/test_t006_event_source_discovery.py` — 所有 mock target 从 `fetch_*_events` 改为 `_fetch_*_raw`，验证生产路径异常可观测
- **测试结果**：1815 passed, 17 skipped, 0 failed
- **关键逻辑**：`_raw()` 函数不吞异常 → `_detailed` helper 能看到真实失败 → `fetch_daily_events_detailed()` 正确报告 PARTIAL/FAILED
- **风险点**：无；公共 `fetch_*_events()` 签名不变，`fetch_daily_events()` 不受影响

---

## 2026-05-30 | T-007: 修复 T-006 事件源底层失败可观测性

- **执行者**：OpenCode
- **任务**：T-007 — 修复事件源子 fetch 函数吞掉 AKShare/network 异常后，`fetch_daily_events_detailed()` 仍把结果标成 `OK` 的问题
- **修改文件**：
  - `tradingagents/tradeflow/event_source.py` — [T-007] event_source_failure_status
    1. `EventSourceStatus` 新增 `PARTIAL` 枚举值
    2. 新增 `_SubFetchResult` 数据类：`items`/`success`/`error`，追踪每个子 fetch 的成功/失败状态
    3. 新增 `_fetch_notice_events_detailed()`、`_fetch_buyback_events_detailed()`、`_fetch_rating_events_detailed()` 内部 helper，捕获子函数异常并返回 `_SubFetchResult`
    4. `EventSourceResult` 新增 `source_statuses: dict[str, str]` 和 `failed_sources: list[str]` 字段
    5. `fetch_daily_events_detailed()` 改用 detailed helper，聚合状态：全部成功=OK、部分失败=PARTIAL、全部失败=FAILED
    6. 新增 `_sanitize_error()` 函数：清除错误消息中的 API key/token 敏感信息
  - `tradingagents/tradeflow/plan_runner.py` — metadata `event_source` 新增 `failed_sources` 和 `source_statuses` 字段
  - `tradingagents/tradeflow/discovery.py` — metadata `event_source` 新增 `failed_sources` 和 `source_statuses` 字段
  - `tests/test_t006_event_source_discovery.py` — 更新 `test_failure_returns_failed`（mock 全部 3 个子 fetch）；新增 `test_partial_failure_returns_partial`、`test_sub_fetch_swallows_exception_gives_ok`；新增 4 个 T-007 测试类：
    - `TestT007SubFetchFailureStatus`（8）：全部/部分/两个失败、成功空/成功有事件、失败源事件仍保留、source_statuses 三项、API key 脱敏
    - `TestT007PlanRunnerMetadata`（4）：PARTIAL/FAILED/OK/NOT_QUERIED 在 plan metadata 中的 failed_sources 和 source_statuses
    - `TestT007DiscoveryMetadata`（4）：PARTIAL/FAILED/OK/NOT_QUERIED 在 discovery metadata 中的 failed_sources 和 source_statuses
    - `TestT007EventSourceStatusEnum`（2）：PARTIAL 存在、全部枚举值
- **测试结果**：1757 passed, 17 skipped, 0 failed
- **关键逻辑**：
  - 三个子 fetch 函数（notice/buyback/rating）内部 try/except 仍返回 `[]` 保持向后兼容
  - 新增 `_fetch_*_detailed()` 内部 helper 捕获异常并返回 `_SubFetchResult(success=False)`
  - `fetch_daily_events_detailed()` 基于 `_SubFetchResult` 聚合：0 失败=OK、1-2 失败=PARTIAL、3 失败=FAILED
  - 失败源的 items 仍为空列表（因原始子函数吞异常返回 []），成功源的 items 正常合并
  - 错误消息经 `_sanitize_error()` 脱敏，去除 api_key/token/key=xxx 等敏感信息
  - plan_runner 和 discovery metadata 新增 `failed_sources` 和 `source_statuses`，下游可直接看到哪个源失败
- **风险点**：无；原始子 fetch 函数签名不变，`fetch_daily_events()` 完全不受影响

## 2026-05-30 | V-003: TradeFlow 端到端候选质量回放验收

- **执行者**：OpenCode
- **任务**：V-003 — 基于固定样本回放完整 TradeFlow 链路，验证候选池从 universe、事件源、资金异动、证据门禁、候选分层到 deep TA 门控的最终输出是否一致
- **修改文件**：
  - `tradingagents/tradeflow/acceptance_replay.py` — 新建：[V-003] tradeflow_acceptance_replay
    - `AcceptanceFixture` / `AcceptanceReplayResult` 数据类
    - `generate_acceptance_fixtures()` — 6 个固定场景定义
    - `make_vcp_df()` / `make_flat_df()` — 确定性 DataFrame 构造
    - 5 个候选构建器：`_build_strong_resonance_candidate` / `_build_tech_only_candidate` / `_build_unverified_fund_candidate` / `_build_high_risk_candidate` / `_build_yesterday_observation_candidate`
    - 5 个验证器：`_verify_strong_resonance` / `_verify_tech_only` / `_verify_unverified_fund` / `_verify_high_risk` / `_verify_yesterday_observation`
    - `generate_acceptance_report()` — 生成本地验收报告 markdown
    - `extract_replay_result()` — 从 plan entry 提取回放结果
  - `tests/test_v003_tradeflow_acceptance_replay.py` — 新建，84 个测试覆盖：
    - `TestV003StrongResonance` (11): tier=A, need_deep_ta, positive_category>=3, game_balance, fund_flow_unit_verified, policy_tags, action, forbidden words, validate, composite_score, data_completeness
    - `TestV003TechOnlySignal` (8): tier!=A, no deep_ta, positive_category<=2, missing_evidence, why_not_deep_ta, action, forbidden words, evidence_gate
    - `TestV003UnverifiedFundFlow` (9): tier!=A, no deep_ta, unit not verified, missing_data_fields, why_not_deep_ta mentions unverified, fund_flow_tags, evidence_gate, forbidden words, tech+unverified cannot pass gate
    - `TestV003EventSourceFailed` (6): metadata shows FAILED, system processes candidates, no crash, error recorded, not treated as no events, forbidden words
    - `TestV003HighRisk` (8): tier B/C, no deep_ta, risk_flags>=2, game_balance fragile/crowded, negative risk_penalty, why_not_deep_ta, action OBSERVE, forbidden words
    - `TestV003YesterdayObservation` (7): observe_state TRIGGERED, trigger_count preserved, first_trigger_time, universe_sources includes yesterday, fund_flow verified, forbidden words, strategies>=2
    - `TestV003FullPipelineReplay` (6): all scenarios pass, strong resonance highest score, unverified fund not A, high risk lowest, no forbidden words, all validate
    - `TestV003EventSourceFailedFullPipeline` (2): full replay with failure, failure does not block candidates
    - `TestV003StableOutput` (4): deterministic output across 3 repeated runs for 4 scenarios
    - `TestV003ReportGeneration` (3): report contains all scenarios, no forbidden words, tier summary
    - `TestV003EvaluateSymbolIntegration` (4): evaluate_symbol with mocked data for strong resonance, tech only, unverified fund, high risk
    - `TestV003FixtureCompleteness` (4): fixture count, names, builders, verifiers
    - `TestV003TierClassification` (5): tier rules for all 5 candidate scenarios
    - `TestV003DeepTAGate` (4): deep TA gate decisions for all 4 relevant scenarios
    - `TestV003DeepTAGateIntegration` (3): gated_deep_ta module integration with strong resonance, tech only, high risk
  - `docs/tradeflow_acceptance/2026-05-30.md` — 验收报告
- **测试结果**：84 passed (V-003)；test_tradeflow_* 129 passed, 0 failed
- **关键逻辑**：
  - 6 个固定 fixture 覆盖全部验收场景：强共振→A/深挖、技术单信号→C/观察、未校验资金→C/不深挖、事件源失败→FAILED 可观测、高风险→C/脆弱、昨日观察→TRIGGERED 保留
  - 每个 fixture 通过 `generate_daily_plan(candidates=...)` 回放完整链路（plan entry 构建、排序、tier 预算、evidence gate、validation、metadata）
  - 事件源失败场景通过 mock `fetch_daily_events_detailed` 返回 `EventSourceStatus.FAILED`，验证 metadata 正确传播
  - 未校验资金候选 tier!=A 且 need_deep_ta=False，验证 S-009 修复有效
  - Deep TA gate 集成测试验证 `gated_deep_ta.check_deep_ta_gate()` 与候选层决策一致
  - 稳定性测试验证 3 次重复运行输出完全一致
  - 验收报告生成分层汇总：明确列出"可进入 TA / 仅观察 / 淘汰"及原因
- **风险点**：无；所有改动新增文件，不影响现有逻辑

## 2026-05-30 | M-011: 修复 M-003 universe 兼容性与来源 extra 序列化

- **执行者**：OpenCode
- **任务**：M-011 — 修复 Codex review 指出的 universe manager 兼容性问题：事件覆盖来源标签变化，以及 `SourceRecord.extra` 被收集但序列化丢失
- **修改文件**：
  - `tradingagents/tradeflow/universe.py` — [M-011] universe_compat_fix
    1. `UniverseSource.EVENT` 值从 `"event"` 改为 `"event_catalyst"`，保留旧公共输出 `source="event_catalyst"`，避免已有报告、筛选器、测试断裂
    2. `UniverseEntry.to_dict()` 序列化 `universe_source_records` 时包含 `SourceRecord.extra`（当 extra 非空时）
    3. `include_yesterday` 带入的 `strategy_tags/trigger_price/invalid_price` 不再在序列化中丢失
  - `tests/test_tradeflow_universe.py` — 新增 `TestM011EventOverridesCompat`（2 个测试）、`TestM011SourceRecordExtraSerialization`（3 个测试）、`TestM011PlanRunnerDiscoveryCompat`（3 个测试），覆盖事件来源标签兼容、extra 序列化、昨日候选 extra 保留、plan_runner/discovery 集成；更新 6 处既有断言使用 `"event_catalyst"`
- **测试结果**：1711 passed, 17 skipped, 0 failed
- **关键逻辑**：
  - `UniverseSource.EVENT = "event_catalyst"` 恢复 M-003 重构前的公共来源标签，与 `test_tradeflow_plan_runner.py` 中已有的 `"event_catalyst"` 断言一致
  - `to_dict()` 中 `universe_source_records` 只在 `extra` 非空时序列化，避免空字典噪声
  - `include_yesterday` 通过 `add_from_yesterday()` 将 strategy_tags/trigger_price/invalid_price 存入 `SourceRecord.extra`，现在通过 `to_dict()` 可正确传递到下游
- **风险点**：无；`UniverseSource.EVENT` 值从 `"event"` 改为 `"event_catalyst"` 恢复了旧的公共标签，`to_dict()` 的 extra 序列化是纯新增字段

## 2026-05-30 | S-009: 修复 S-005 选股门控双重计分与资金单位校验

- **执行者**：OpenCode
- **任务**：S-009 — 修复 Codex review 指出的两个候选排序风险：综合分重复叠加，以及未校验单位的资金流也能作为正向类别
- **修改文件**：
  - `tradingagents/tradeflow/selection_priority_gate.py` — [S-009] selection_gate_fix
    1. **双重计分修复**：新增 `score_already_includes_subscores` 参数（默认 True）。当为 True 时，composite_score = score + completeness_bonus，不再重复叠加 version_score/narrative_score/fund_flow_anomaly_score/risk_penalty。candidate_engine.evaluate_symbol() 传入的 candidate.score 已经包含这些子分数，默认模式正确避免双重计分。
    2. **资金单位校验修复**：`has_fund` 判定增加 `fund_flow_unit_verified` 条件。未校验单位的资金流不再计入正向类别（fund category），也不能单独帮助通过 need_deep_ta 门控。
  - `tradingagents/tradeflow/candidate_engine.py` — 补充注释说明 candidate.score 已包含子分数，score_already_includes_subscores=True 默认正确
  - `tests/test_s005_selection_priority_gate.py` — 新增 `TestS009NoDoubleScoring`（6 个测试）和 `TestS009FundFlowUnitVerifiedGate`（6 个测试），覆盖：
    - 默认模式不双重计分：composite_score ≈ score + completeness_bonus
    - legacy 模式仍可叠加子分数：score_already_includes_subscores=False
    - evaluate_symbol 端到端不虚高
    - 未校验资金不作为正向类别
    - 技术 + 未校验资金不能通过门控
    - 技术 + 已校验资金可通过门控
    - 未校验资金不影响政策/技术组合的门控
    - evaluate_symbol 未校验资金不触发 need_deep_ta
  - 更新 7 个既有测试适配新逻辑：添加 fund_flow_unit_verified=True、score_already_includes_subscores=False 等参数
- **测试结果**：test_s005 85 passed；test_s005+s008+t003 209 passed, 4 skipped；test_tradeflow_* 121 passed
- **关键逻辑**：
  - `run_selection_priority_gate()` 的 `score` 参数语义：从 candidate_engine 调用时是已累积的 candidate.score（包含所有子分数），从外部直接调用时可以是原始技术分
  - `score_already_includes_subscores=True`（默认）确保候选层到门控层不会二次叠加
  - `score_already_includes_subscores=False` 保留旧公式用于外部调用和兼容测试
  - 资金流类别（fund category）现在必须同时满足：有正向标签 + anomaly_score > 0 + unit_verified = True
- **风险点**：无；所有改动向后兼容，默认参数行为不变，evaluate_symbol 无需修改

## 2026-05-30 | 自动开发验收与下一轮任务释放

- **执行者**：Codex
- **任务**：审核 2026-05-29/30 自动开发结果，处理阻断自动开发的运行态文件，并释放下一轮任务。
- **验收结果**：
  - S-005：1246 passed, 11 skipped；Codex review 留 2 个 P2，已转入 `S-009`。
  - S-006：83 passed。
  - S-007：1401 passed, 13 skipped。
  - S-008：1454 passed, 17 skipped。
  - M-003：第 1 轮 3 failed，第 2 轮 1495 passed, 17 skipped；Codex review 留 2 个 P2，已转入 `M-011`。
  - M-004：1537 passed, 17 skipped。
  - V-002：1570 passed, 17 skipped。
  - M-005：1610 passed, 17 skipped。
  - M-006：第 2 轮 1670 passed, 17 skipped。
  - T-006：121 passed；Codex review 留 1 个 P2，已转入 `T-007`。
- **修复内容**：
  - `.zai_quota_state.json` 从 Git 跟踪中移除，并加入 `.gitignore`，避免额度检查导致工作区变脏、阻断 `auto_dev_loop`。
  - `docs/TASKS.md` 当前优先队列更新为 `S-009`、`M-011`、`T-007`、`V-003`、`M-007`。
  - 新增 `S-009`、`M-011`、`T-007`、`V-003` 四个 follow-up 任务。
  - 将 `M-007` 释放为 ready，进入盘后复盘能力建设。
- **风险点**：
  - `.zai_quota_state.json` 仍保留在本地但不再入库。
  - 下一轮必须优先修 P2 follow-up，再做更高层功能扩展。

## 2026-05-30 | T-006: 事件源自动接入 Discovery / Daily Plan

- **执行者**：OpenCode
- **任务**：T-006 — 把已完成的事件源模块纳入小范围 Discovery 和 Daily Plan，使公告、回购、评级等事件能在不全市场扫描的前提下自动进入候选池
- **修改文件**：
  - `tradingagents/tradeflow/event_source.py` — 新增 `EventSourceStatus` 枚举（OK/FAILED/STALE）、`EventSourceResult` 数据类、`fetch_daily_events_detailed()` 函数：返回包含完整元数据（event_type/direction/source/date）和状态跟踪的事件结果，支持标题去重
  - `tradingagents/tradeflow/plan_runner.py` — `generate_daily_plan()` 改用 `fetch_daily_events_detailed()`；生成 `event_overrides` 从 EventItem 数据并传入候选评估链路；plan.metadata 新增 `event_source` 状态信息；`_build_plan_entry()` 新增 `event_items` 参数，输出 event_titles/event_types/event_sources/event_directions/event_count/event_details
  - `tradingagents/tradeflow/discovery.py` — `run_discovery()` 改用 `fetch_daily_events_detailed()`；生成 `event_overrides` 从 EventItem 数据；result.metadata 新增 `event_source` 状态信息；`_build_discovery_entry()` 新增 `event_items` 参数，输出事件元数据
  - `tests/test_t006_event_source_discovery.py` — 新建，22 个测试覆盖 EventSourceResult/EventSourceStatus、fetch_daily_events_detailed 去重/失败、plan_runner 事件元数据/失败可观测/事件覆盖/symbol 隔离、discovery 事件元数据/失败可观测/事件覆盖/symbol 隔离
  - `tests/test_event_source_integration.py` — 更新 N-001 测试适配 `fetch_daily_events_detailed()` 替代 `fetch_daily_events()`
  - `tests/test_t002_discovery.py` — 更新 T-002 测试适配 `fetch_daily_events_detailed()`
- **测试结果**：1664 passed, 15 skipped, 0 failed
- **关键逻辑**：
  - `fetch_daily_events_detailed()` 包装三个子 fetch 函数，返回 `EventSourceResult`：包含 `events_map`（向后兼容）+ `items_by_symbol`（按 symbol 分组的 EventItem 列表）+ 状态信息
  - 事件源异常时 status=FAILED + error_message，系统仍能正常处理手动 symbols
  - EventItem 自动转换为 `event_overrides`（含 symbol/title/event_type/direction/source/date），传入 `evaluate_symbol()` 的 `event_overrides` 参数，使 event_catalyst 策略获得结构化事件数据
  - 每个候选 plan entry 包含 event_details（完整事件列表）、event_types（去重事件类型）、event_sources（去重事件来源）
  - 所有 symbol 严格隔离，每个 symbol 只消费自己的事件数据
- **风险点**：无；所有改动向后兼容，use_event_source 默认 False，事件元数据字段只在有事件时才出现

## 2026-05-30 | M-006 代码质量修复（Codex review 失败后巡检）

- **执行者**：OpenCode
- **背景**：Codex review 因网络断连失败（exit code 1），非代码问题。人工巡检发现两个潜在缺陷。
- **修复内容**：
  - `tradingagents/tradeflow/gated_deep_ta.py`：
    1. **新增空模型拦截**：`check_deep_ta_gate()` 在 resolved_model 为空时返回 BLOCKED，避免无模型调度
    2. **修复 `can_retry_deep_ta()` 语义不一致**：原实现 `failures < max_retries` 与 `check_deep_ta_gate()` 的 `failures + retry_count > max_retries` 不一致；改为 `failures + retry_count <= max_retries`，新增 `retry_count` 参数
  - `tests/test_m006_gated_deep_ta.py`：
    - 新增 `test_no_model_blocks` 和 `test_no_model_uses_default` 测试
    - 修正多个测试：未指定 model 时不再通过门控，需设置 `default_model` 或传入 `model`
    - 修正 `can_retry_deep_ta` 测试预期：`failures=1, max_retries=1` → 允许重试（与 check 逻辑一致）
    - 新增 `retry_count` 参数验证
- **测试结果**：60 passed (M-006)；TradeFlow 全部 221 passed；0 failed
- **风险点**：无；修复向后兼容（调用方必须在 config 设置 `deep_ta_default_model` 或传入 `model`）

## 2026-05-30 | M-006: OpenClaw 自动触发 TA 深度分析门控

- **执行者**：OpenCode
- **任务**：M-006 — 在盘中 Observe 触发后，由 OpenClaw 决定是否调用 TA 深度分析，带成本、模型、频率门控
- **修改文件**：
  - `tradingagents/tradeflow/gated_deep_ta.py` — 新建：[M-006] 深度 TA 调度门控模块
    - `DeepTAStatus` 枚举：PENDING / BLOCKED / DISPATCHED / SUCCESS / FAILED
    - `DeepTARecord` 数据类：记录每次调度的 symbol、状态、模型、耗时、报告路径、重试次数、持仓上下文
    - `DeepTADecision` 数据类：门控结果（allowed/reason/model/record）
    - `DeepTADispatcher` 数据类：日内调度状态管理器，跟踪 daily_count、failure_counts、records；支持从 StrategyConfig 构建
    - `check_deep_ta_gate()` — 核心门控函数：依次检查 need_deep_ta、每日上限、observe 状态、综合分、完整度、模型黑名单、重试上限
    - `record_deep_ta_dispatch()` — 记录调度结果，更新 daily_count（DISPATCHED）和 failure_counts（FAILED）
    - `can_retry_deep_ta()` — 判断是否可以重试
  - `tradingagents/tradeflow/strategy_config.py` — StrategyConfig 新增 7 个 deep TA 门控配置：`deep_ta_daily_limit`(默认3)、`deep_ta_default_model`、`deep_ta_blocked_models`(默认屏蔽 deepseek)、`deep_ta_max_retries`(默认1)、`deep_ta_min_composite_score`(默认40)、`deep_ta_min_completeness`(默认0.5)、`deep_ta_require_observe_triggered`(默认True)
  - `tradingagents/tradeflow/schemas.py` — Candidate 新增 6 个字段：`deep_ta_status`、`deep_ta_dispatch_reason`、`deep_ta_model`、`deep_ta_report_path`、`deep_ta_dispatch_time`、`deep_ta_position_context`；`to_db_row()`/`from_db_row()` 同步；`render_text()` 显示深度 TA 调度状态
  - `tradingagents/tradeflow/candidate_engine.py` — `init_db()` 新增 6 列；`save_candidate()` INSERT/UPDATE 包含新列（58→64 字段）
  - `tradingagents/tradeflow/plan_runner.py` — `_build_plan_entry()` 输出 deep_ta_status/.../deep_ta_position_context
  - `tests/test_m006_gated_deep_ta.py` — 新建，58 个测试覆盖：
    - TestDeepTAStatus (2): 枚举值、完整枚举集合
    - TestDeepTARecord (4): 默认值、自定义值、自动日期、显式日期
    - TestDeepTADispatcher (5): 默认初始化、from_config 默认/自定义、自动/显式日期
    - TestCheckDeepTAGate (20): need_deep_ta=False 拦截、每日上限满/未满、observe 非 TRIGGERED 拦截、WAITING/INVALIDATED、TRIGGERED 放行、禁用 require 观察触发、低综合分、综合分在阈值、低完整度、完整度在阈值、DeepSeek 模型拦截、默认模型 DeepSeek、允许模型、默认模型使用、超重试上限、重试内允许、全部门通过、未知持仓、空黑名单
    - TestRecordDeepTADispatch (7): DISPATCHED 增计数、FAILED 增失败、SUCCESS 不变、BLOCKED 不变、多次上限、更新记录、全部字段
    - TestCanRetryDeepTA (4): 无失败可重试、达上限不可、低于上限可、不同 symbol 独立
    - TestCandidateDeepTAFields (4): 默认值、to_db_row、from_db_row、from_db_row 默认值
    - TestDeepTADbPersistence (2): 保存读取、upsert 覆盖
    - TestStrategyConfigDeepTA (3): 默认值、自定义值、to_dict 包含
    - TestFullDeepTAWorkflow (7): 触发→调度→成功、未触发拦截、每日上限耗尽、失败→重试→再失败→拦截、DeepSeek 默认拦截、多 symbol 独立、无无限重试
- **测试结果**：58 passed (M-006)；TradeFlow 全部 219 passed；0 failed
- **关键逻辑**：
  - `check_deep_ta_gate()` 按顺序检查 7 道门：need_deep_ta=True → 每日上限未满 → observe TRIGGERED → 综合分达标 → 完整度达标 → 模型不在黑名单 → 失败次数未超限
  - 默认屏蔽 deepseek 模型，必须在配置中显式移除才能使用
  - `DeepTADispatcher.from_config(cfg)` 从 StrategyConfig 构建调度器，所有阈值集中配置
  - `record_deep_ta_dispatch()` 只记录审计信息，不实际调用 LLM，由上层（OpenClaw/scheduler）负责执行
  - 多 symbol 完全独立，失败计数和调度记录按 symbol 隔离
- **风险点**：无；所有改动向后兼容，deep_ta_status 默认空字符串，不影响现有逻辑

## 2026-05-30 | M-005: 盘中 Observe 状态机

- **执行者**：OpenCode
- **任务**：M-005 — 对盘前候选做低频盘中观察，记录触发价、失效价、放量、跌破等状态
- **修改文件**：
  - `tradingagents/tradeflow/intraday_observe.py` — 新建：[M-005] 盘中观察状态机核心模块
    - `ObserveState` 枚举：WAITING / TRIGGERED / INVALIDATED / EXPIRED
    - `ObserveSnapshot` 数据类：单次检查快照，记录价量证据和触发原因
    - `ObserveTracker` 数据类：跟踪器，维护状态、触发次数、检查历史
    - `run_observe_check()` — 核心检查函数：价格突破触发价→TRIGGERED，跌破失效价→INVALIDATED，支持单日触发次数上限
    - `expire_tracker()` — 收盘到期处理
  - `tradingagents/tradeflow/schemas.py` — Candidate 新增 3 个字段：`observe_state`、`observe_trigger_count`、`observe_first_trigger_time`；`to_db_row()`/`from_db_row()` 同步；`render_text()` 显示盘中观察状态
  - `tradingagents/tradeflow/strategy_config.py` — StrategyConfig 新增 3 个 observe 配置：`observe_max_daily_triggers`(默认3)、`observe_trigger_breach_pct`、`observe_invalidate_breach_pct`
  - `tradingagents/tradeflow/candidate_engine.py` — `init_db()` 新增 3 列；`save_candidate()` INSERT/UPDATE 包含新列（55→58 字段）
  - `tradingagents/tradeflow/plan_runner.py` — `_build_plan_entry()` 输出 observe_state/observe_trigger_count/observe_first_trigger_time
  - `tests/test_m005_intraday_observe.py` — 新建，40 个测试覆盖：
    - TestObserveState (2): 枚举值、完整枚举集合
    - TestObserveSnapshot (2): 基本创建、自动时间戳
    - TestObserveTracker (2): 默认状态、自定义日期
    - TestRunObserveCheck (11): 价格低于触发价/WAITING、突破触发价/TRIGGERED、恰好触发价/TRIGGERED、跌破失效价/INVALIDATED、恰好失效价/INVALIDATED、失效优先于触发检查、无触发价、无失效价、无价格、量价记录、单日触发上限
    - TestTerminalStates (3): TRIGGERED 终态保持、INVALIDATED 终态保持、EXPIRED 终态保持
    - TestExpireTracker (3): WAITING→EXPIRED、TRIGGERED 保持、INVALIDATED 保持
    - TestCandidateObserveIntegration (4): 默认值、to_db_row、from_db_row、from_db_row 默认值
    - TestDbPersistence (2): 保存并读取 observe 状态、upsert 覆盖
    - TestTrackerToCandidateSync (3): 同步 TRIGGERED/INVALIDATED/WAITING
    - TestStrategyConfigObserve (2): 默认配置、自定义配置
    - TestFullObserveWorkflow (4): 完整日间流程、失效流程、未触发无输出、多 symbol 隔离
- **测试结果**：40 passed (M-005)；TradeFlow 全部 121 passed；0 failed
- **关键逻辑**：
  - `run_observe_check()` 先检查失效价（INVALIDATED），再检查触发价（TRIGGERED），失效优先于触发
  - 单日触发次数上限由 `StrategyConfig.observe_max_daily_triggers` 控制（默认3），超出上限后价格虽突破但不再触发
  - 终态（TRIGGERED/INVALIDATED/EXPIRED）为不可逆状态，后续检查只记录快照不改变状态
  - `expire_tracker()` 收盘时将 WAITING 状态转为 EXPIRED，已触发/已失效保持不变
  - 每次 `run_observe_check()` 自动追加到 `tracker.check_history`，保存完整检查历史
  - 多 symbol 完全隔离，互不影响
- **风险点**：无；所有改动向后兼容，observe_state 默认 WAITING，不触发任何自动动作



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

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: M-005 — 盘中 Observe 状态机（P2）
- **优先级**: P2
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/M-005-20260530-round1.txt
- **运行档案**: docs/task_runs/M-005-20260530-010959/

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: M-006 — OpenClaw 自动触发 TA 深度分析门控（P2）
- **优先级**: P2
- **轮次**: 2
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/M-006-20260530-round2.txt
- **运行档案**: docs/task_runs/M-006-20260530-011554/

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: T-006 — 事件源自动接入 Discovery / Daily Plan（P1）
- **优先级**: P2
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/T-006-20260530-round1.txt
- **运行档案**: docs/task_runs/T-006-20260530-013347/

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: S-009 — 修复 S-005 选股门控双重计分与资金单位校验（P1）
- **优先级**: P1
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/S-009-20260530-round1.txt
- **运行档案**: docs/task_runs/S-009-20260530-022215/

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: M-011 — 修复 M-003 universe 兼容性与来源 extra 序列化（P1）
- **优先级**: P1
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/M-011-20260530-round1.txt
- **运行档案**: docs/task_runs/M-011-20260530-022843/

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: V-003 — TradeFlow 端到端候选质量回放验收（P1）
- **优先级**: P1
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/V-003-20260530-round1.txt
- **运行档案**: docs/task_runs/V-003-20260530-023514/

## 2026-05-30 | AUTO-002 自动开发闭环

- **任务**: T-007 — 修复 T-006 事件源底层失败可观测性（P1）
- **优先级**: P1
- **轮次**: 2
- **状态**: ✅ PASS
- **测试**: 通过
- **Codex Review**: 无 P0/P1 findings
- **Review 文件**: docs/reviews/T-007-20260530-round2.txt
- **运行档案**: docs/task_runs/T-007-20260530-024618/

## 2026-05-31 | AUTO-002 自动开发闭环

- **任务**: M-007 — 盘后 Review 与策略命中率复盘（P2）
- **优先级**: P2
- **轮次**: 1
- **状态**: ✅ PASS
- **测试**: 62 passed (unit) + 129 passed (tradeflow)
- **Commit**: 6c5b23a
- **运行档案**: docs/task_runs/M-007-20260530-030217/

## 2026-05-31 | M-007 补修

- **原因**: 用户审核发现 P1(工作区脏) + P2(0%显示N/A) + P2(auto-compute)
- **Commit**: 54ab806
- **修复项**:
  1. render_review_markdown `or 'N/A'` → `is not None`
  2. run_post_market_review 聚合前自动 compute_returns()
  3. 补齐 task_runs/M-007-20260530-030217/ 全部文件
- **测试**: 193 passed
- **工作区**: 干净

## 2026-05-31 | UI 任务收口 + CodeGraph 评估

### CodeGraph 评估
- 工具: colbymchenry/codegraph v0.9.7
- 扫描: 255 文件, 6195 节点, 12658 边, 1.3s
- 支持 Python(204) + TypeScript(42), 100% 本地
- 结论: 适合集成自动开发流程 (impact/callers/context)
- Commit: b157b36

### UI-001~UI-005 状态收口
- UI-001: e6d3269 — TradeFlow API 层 (29 passed)
- UI-002: edf69d9 — TradeFlow 前端骨架 (build 零错误)
- UI-003: 9ee6aea — 候选详情抽屉
- UI-004: fa75389 — 盘中 Observe 面板
- UI-005: 401fb7c — 盘后 Review 页面
- task_runs 临时文件已整理进标准目录

### 下一步
- UI-006 数据源健康前端面板 (P2, ready)
- CodeGraph 可作为 OpenCode 任务前的辅助工具

## 2026-05-31 | UI-008 TradeFlow 候选字段规范化与名称回填

### 新增模块
- `tradingagents/tradeflow/symbol_utils.py`: `normalize_tradeflow_symbol()` / `resolve_tradeflow_name()` / `symbol_bare_code()` / `StockNameResolver`

### 修改文件
- `tradingagents/tradeflow/universe.py`: UniverseManager.add_symbol/add_manual/add_from_events/add_from_fund_flow/add_from_industry 规范化 symbol 并解析 name
- `tradingagents/tradeflow/candidate_engine.py`: evaluate_symbol/save_candidate 落库前兜底规范化
- `tradingagents/tradeflow/discovery.py`: 事件源 bare code 查找兼容，fund_flow symbol 规范化
- `api/services/tradeflow_service.py`: _row_to_candidate_item/get_daily_plan/get_observe/get_ta_queue/run_discovery_scan 全链路规范化
- `frontend/src/pages/TradeFlow.tsx`: 候选表/观察表/TA队列表名称空值显示 `--`
- `frontend/src/components/TradeFlowCandidateDrawer.tsx`: 详情抽屉名称空值显示 `--`

### 测试
- 新增 `tests/test_ui008_tradeflow_field_normalization.py`: 39 tests (symbol normalization, name resolution, universe/engine/API integration)
- 全量通过: 201 passed (162 existing + 39 new)
- 前端构建通过: npm run build 零错误

## 2026-05-31 | Simon 数据 Skill 思路吸收进数据源治理路线

### 背景
- 用户要求参考 SimonLin1212 GitHub 实践，重点吸收 A 股数据源 Skill 的有用部分。
- 结论：不替换本项目 TA/TradeFlow/风控门禁，只吸收“端点目录、vendor fallback、实时补丁、来源溯源、字段契约、fixture replay”。

### 修改
- `docs/ROADMAP.md`: Data Layer 增加 source router/provenance，Phase 6 增加 A-stock source integration、实时补丁和数据契约任务方向。
- `docs/TASKS.md`: 新增 `DATA-001`~`DATA-005` 数据源治理任务池，状态全部为 `proposed`，避免抢占当前 `H-001` 主线。

### 后续
- 当前唯一 ready 仍为 `H-001`。
- 待 H-001 定义 MandateSignal 后，可把 `DATA-003` 转为 ready，服务昊天雷达事件源归一化。

## 2026-06-01 | AUTO-002 Auto Dev Loop

- **Task**: H-001 - 昊天雷达 v0 数据模型与信号分类（P1）
- **Priority**: P1
- **Rounds**: 1
- **Status**: OK PASS
- **Tests**: Passed
- **Codex Review**: no P0/P1 findings
- **Review file**: docs/reviews/H-001-20260601-round1.txt
- **Run archive**: docs/task_runs/H-001-20260601-182415/

## 2026-06-01 | Task Pool Queue Refresh

- **Purpose**: Keep the 20:00 auto-dev window from stopping after a single ready task.
- **Ready queue**: `TF-OBS-001`, `DATA-001`, `H-002`, `H-003`.
- **Dependency rule**: `H-004` remains proposed until both `H-002` and `H-003` are done; `DATA-002` remains proposed until `DATA-001` is done.
- **Note**: Scheduler warning during dry-run is expected because user-approved scheduled analysis is running.

## 2026-06-01 | AUTO-002 Auto Dev Loop

- **Task**: TF-OBS-001 - TradeFlow 盘中观察执行器与信号落库（P0）
- **Priority**: P0
- **Rounds**: 1
- **Status**: OK PASS
- **Tests**: Passed
- **Codex Review**: no P0/P1 findings
- **Review file**: docs/reviews/TF-OBS-001-20260601-round1.txt
- **Run archive**: docs/task_runs/TF-OBS-001-20260601-220225/

## 2026-06-01 | AUTO-002 Auto Dev Loop

- **Task**: DATA-001 - A股数据源能力目录与 fallback 矩阵（P1）
- **Priority**: P1
- **Rounds**: 1
- **Status**: OK PASS
- **Tests**: Passed
- **Codex Review**: no P0/P1 findings
- **Review file**: docs/reviews/DATA-001-20260601-round1.txt
- **Run archive**: docs/task_runs/DATA-001-20260601-221610/
