# 任务池

> 最后更新：2026-06-22

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

1. `DATA-P0-603629`：TA A股关键数据源补强与假可用修复（P0，done）。
2. `TF-DATE-001`：TradeFlow 日期语义拆分与非交易日计划生效（P0，done）。
3. `TF-OBS-001`：TradeFlow 盘中观察执行器与信号落库（P0，done）。
4. `H-001`：昊天雷达 v0 数据模型与信号分类（P1，done）。
5. `DATA-001`：A股数据源能力目录与 fallback 矩阵（P1，done）。
6. `H-002`：政策连续性与级别权重评分（P1，done）。
7. `H-003`：产业链受益路径与标杆候选映射（P1，done）。
8. `H-004`：左侧埋伏评分与候选类型分流（P0，done）。
9. `DATA-002`：实时行情 freshness 检测与补丁标注（P1，done）。
10. `DATA-003`：公告/研报/政策事件源归一化接入昊天雷达（P1，done）。
11. `DATA-004`：raw_evidence 来源契约升级（P1，done）。
12. `H-005`：TradeFlow 前端昊天候选池视图（P1，done，依赖 H-004 ✓）。
13. `H-006`：昊天候选池回放评估与反证机制（P1，done，依赖 H-004 ✓）。
14. `DATA-005`：数据源 fixture replay 与限流/失败回放（P1，done，依赖 DATA-001/DATA-004 ✓）。
15. `H-007`：昊天候选到 TA 中线研究队列分流（P1，done，依赖 H-004 ✓）。
16. `H-008`：昊天主题观察清单与自选备注摘要（P1，done，依赖 H-004/VLM-001 ✓）。
17. `DATA-006`：数据源质量报告接入夜间日报（P2，done，依赖 DATA-005 ✓）。
18. `V-004`：昊天链路端到端 smoke 验收（P1，done，依赖 H-004/DATA-004 ✓）。
19. `TF-P0-001`：TradeFlow 运行态 schema 迁移、名称回填与观察路由修复（P0，done）。
20. `TA-UI-001`：智能分析控制台增加短线/中线、分析意图、持仓状态选择（P0，done）。
21. `TF-P0-002`：候选池分层：短线技术池与昊天左侧池显式拆分（P0，done）。
22. `TF-P0-003`：TradeFlow 生成候选池后的端到端 UI smoke 验收（P0，done）。
23. `PERF-001`：运行层级与速度预算契约（P1，done）。
24. `PERF-002`：轻量 TA Profile 与模块路由（P1，done）。
25. `DATA-P0-FUND-ROUTE`：主力资金 fallback 假成功与单位修复（P0，done）。
26. `DATA-P1-LHB-FUND-DECOUPLE`：龙虎榜与资金流触发链路复核（P1，done）。
27. `DATA-P1-ASTOCK-LIVE-SMOKE`：cn_astock/Eastmoney 关键源 live smoke 与限流验证（P1，done）。
28. `DATA-P1-SOURCE-GAP-AUDIT`：Simon 数据源吸收落地差距审计（P1，done，依赖 DATA-001/DATA-004 ✓）。
29. `PERF-003`：分析运行耗时/调用成本遥测与前端展示（P1，done）。
30. `PERF-004`：完整 TA 手动确认与 scheduler 成本门禁（P1，done）。
31. `DATA-007`：raw_evidence 覆盖率审计与候选可信度联动（P1，done，依赖 DATA-004/DATA-006 ✓）。
32. `DATA-008`：A股关键源 fallback smoke fixtures 扩展（P1，done，依赖 DATA-005 ✓）。
33. `H-009`：昊天候选反证/过热降权校准（P1，done，依赖 H-006/V-004 ✓）。
34. `H-010`：政策主题生命周期与版本状态注册表（P1，done，依赖 H-002/H-006 ✓）。
35. `H-011`：候选矛盾证据与负面清单解释（P2，done，依赖 H-009 ✓）。
36. `UI-009`：候选详情一键生成 TA 研究任务预案（P1，done，依赖 H-007/UI-006 ✓）。
37. `UI-010`：昊天候选对比视图与证据缺口排序（P2，done，依赖 H-005/DATA-007 ✓）。
38. `V-005`：夜间昊天候选质量日报与样本回放（P1，done，依赖 V-004/DATA-006 ✓）。
39. `M-012`：任务池空转时自动生成 proposed 任务草案（P1，done，依赖 V-002 ✓）。
40. `M-013`：CodeGraph 影响范围预检接入自动开发日志（P2，done，依赖 INF-001 ✓）。
41. `T-008`：TradeFlow 观察信号 fixture 回放与前端状态一致性验收（P2，done）。
42. `DATA-009`：自选备注与截图识别字段持久化回归保护（P1，done，依赖 H-008/VLM-001 ✓）。
43. `DATA-010`：融资融券数据源注册与 raw_evidence 接入（P1，done，依赖 DATA-001/DATA-004 ✓）。
44. `DATA-011`：研报端点接入 route_to_vendor 与 raw_evidence（P1，done，依赖 DATA-004 ✓）。
45. `V-001`：600584 数据真实性端到端验收（P1，done，依赖 G-009/G-010/N-002/N-003 ✓）。
46. `T-004`：TradeFlow P2 盘中 Observe（本轮 P1，done，依赖 TF-OBS-001/T-008 ✓）。
47. `T-005`：TradeFlow P3 盘后 Review（本轮 P1，done，依赖 M-007/T-008/V-005 ✓）。
48. `DATA-012`：评级数据接入 provider 路由与 raw_evidence（P2，done — commit 3a8351f + 4d9ae52，DATA-012A 收口完成）。
49. `DATA-013`：回购数据接入 provider 路由与 raw_evidence（P2，done，依赖 DATA-003/DATA-004 ✓）。
50. `DATA-014`：新闻/政策事件 fixture 与 live smoke 补充（P2，done）。
51. `DATA-015`：涨停池 cn_astock fallback 与 fixture（P2，done，依赖 DATA-005/DATA-P0-FUND-ROUTE ✓）。
52. `DATA-016`：热门股票 cn_astock fallback 与 fixture（P2，done，依赖 DATA-005/DATA-P0-FUND-ROUTE ✓）。
53. `DATA-012A`：评级数据 data_collector 接线与回归验收（P1，done，依赖 DATA-012 部分落地 ✓）。
54. `DECISION-001`：最终动作语义分层 — 禁止默认 HOLD（P0，done，commit dee415c）。
55. `DECISION-002`：历史报告回放测试 — 覆盖典型场景（P1，done，依赖 DECISION-001 ✓）。
56. `DECISION-003`：前端展示 3 层语义（P1，done，依赖 DECISION-001 ✓）。
57. `DECISION-004`：报告卡片和推送通知不再只取 decision（P2，done，commit f73f4d5）。
58. `TF-QUALITY-001`：TradeFlow 候选池严格收敛门禁（P0，done — commit 5f304db，后续 001A/DATA-COVERAGE 已闭环）。
59. `TF-QUALITY-001A`：收敛门禁回归修复与状态一致性（P0，done — 后续任务已闭环）。
60. `TF-QUALITY-002`：TradeFlow 评分拉开差距与排序解释（P0，done，commit 见下方）。
61. `TF-OBS-002`：盘中观察自动执行与 A 股红绿视觉修正（P0，done，依赖 TF-QUALITY-001A ✓）。
62. `TF-REVIEW-002`：盘后 Review 数据补齐与非交易日计划映射（P0，done，依赖 TF-QUALITY-001A ✓）。
63. `TF-UI-011`：候选详情一键轻量 TA、K 线与公司概览（P1，done，依赖 TF-QUALITY-001A/PERF-002 ✓）。
64. `DATA-017`：主力资金/龙虎榜数据源健康巡检与 fallback 验收（P1，done，依赖 DATA-P0-FUND-ROUTE）。
65. `V-006`：最终动作语义端到端回放验收（P1，done，依赖 DECISION-004 ✓）。
66. `DATA-COVERAGE-001`：raw_evidence 覆盖率分母/质量等级回归修复（P1，done，Codex 修复，待提交）。
67. `CODEGRAPH-002`：CodeGraph 自动开发预检命令修复（P2，done，commit f54a45e）。
68. `TF-QUALITY-003`：候选池精度校准与弱候选压缩（P0，done，依赖 TF-QUALITY-002）。
69. `TF-PAPER-001`：5000 元试跑模拟账户与候选跟踪账本（P1，done，依赖 TF-OBS-002/TF-REVIEW-002）。
70. `H-012`：昊天主题注册表与政策版本 Watchlist（P1，done，依赖 H-010/H-011）。
71. `DATA-018`：A股关键源新鲜度与 fallback 可视化日报（P1，done，依赖 DATA-017/DATA-006）。
72. `UI-012`：TradeFlow 前端降噪与主候选优先工作台（P1，done，依赖 TF-QUALITY-002/TF-OBS-002）。
73. `V-007`：TradeFlow 试用闭环端到端验收（P1，done，commit 9ceedbf）。
74. `TF-API-013`：TradeFlow API 合约收口与 HTTP 路由回归（P0，done，`fce3141` + `289a392`）。
75. `TF-QUALITY-004`：候选池实盘区分度回放校准（P0，done，`OpenCode` 2026-06-16）。
76. `TF-RISK-001`：5000 元试跑风险预算与仓位纪律（P1，done，依赖 TF-PAPER-001 ✓）。
77. `TF-OBS-003`：盘中观察触发到模拟账本待确认联动（P1，done，commit fcce883）。
78. `TF-REVIEW-003`：盘后 Review 策略命中归因与次日反馈（P1，done，依赖 TF-REVIEW-002/TF-PAPER-001）。
79. `DATA-019`：关键数据源实盘抽样健康日报（P1，done，commit 58b5cd2）。
80. `H-013`：昊天主题热度曲线与政策证据看板（P1，done，commit b1fe17d）。
81. `DATA-019A`：实盘抽样 skipped 状态与任务池收口补修（P2，done）。
82. `H-013A`：昊天热度图历史日期 fallback 与窗口 unique 统计补修（P2，done）。
83. `V-008`：TradeFlow 小资金试跑前整体验收（P1，done，55 tests passed）。
84. `TF-UX-001`：TradeFlow 小资金试跑主工作台降噪与默认视图（P1，done，依赖 TF-QUALITY-004/TF-RISK-001 ✓）。
85. `TRACK-001`：观察仓数据模型与只读/写入 API（P0，done，commit 2ef6c37）。
86. `TRACK-002`：跟踪看板 v2 分组接口与今日指引聚合（P0，done，commit fd0d511）。
87. `TRACK-003`：跟踪看板前端四区改版：持仓/观察仓/今日指引/盘后复盘（P0，done，commit d80aa66）。
  88. `IC-TA-001`：investment-controller 只读上下文包与数据契约（P1，done，依赖 TRACK-001 ✓）。
  89. `TRACK-004`：观察仓入场区/失效区规则引擎与状态流转（P1，done，依赖 TRACK-001 ✓, TRACK-002 ✓）。
  90. `TRACK-005`：盘后复盘摘要与次日计划写回跟踪看板（P1，done，依赖 TRACK-002 ✓, TF-REVIEW-003 ✓）。
91. `TRACK-006`：TradeFlow/TA 候选一键加入观察仓与来源追踪（P1，done，commit 5aa512d）。
92. `TRACK-NOTIFY-001`：飞书/总控官通知草稿 payload 与去噪规则（P1，done — TRACK-NOTIFY-001-20260623-122700，前置 IC-TA-001 ✓/TRACK-002 ✓ 均完成）。
93. `TRACK-007`：跟踪看板端到端验收与用户操作手册（P2，done ✓，依赖 TRACK-003 ✓, TRACK-004 ✓, TRACK-005 ✓）。
  94. `DATA-020`：数据源健康日报前端可视化与 skipped/failed 分层展示（P1，done，依赖 DATA-019A）。
  95. `TF-REVIEW-004`：盘后 Review 空数据诊断与一键生成入口（P1，done，依赖 TF-REVIEW-003）。
 96. `H-014`：昊天主题候选减少与主题集中度阈值校准（P1，done，依赖 H-013A/H-012 ✓）。
97. `V-009`：小资金试跑后回归清单与用户操作手册（P1，done，依赖 V-008 ✓）。
 98. `TF-OBS-004`：盘中观察自动刷新、触发提醒与人工确认队列降噪（P1，done，依赖 TF-OBS-003/TF-RISK-001 ✓）。
99. `DATA-021`：TA 报告数据源失败原因透传与字段级降级说明（P1，done — Codex 接管完成，依赖 DATA-020/DATA-004 ✓）。
100. `H-015`：昊天主题日报与候选入池/出池解释（P1，done — Codex 直接开发，依赖 H-014 ✓/H-013A ✓）。
 101. `PERF-005`：TradeFlow 页面与 API 性能预算回归（P2，done，依赖 TF-UX-001/DATA-020 ✓）。
102. `HK-001`：港股输入边界与轻量行情-only 模式声明（P2，done — commit 2326a3d + a9fd09a）。
103. `TF-PERSIST-001`：TradeFlow save_candidate 分项评分持久化补口（P2，done — commit 0016a92 + 2257f32）。
104. `AUTO-003`：任务建议去重与已完成任务过滤（P1，ready，修复 M-012 误把 done/blocked-resolved 任务重复建议）。
105. `REPORT-UX-001`：TA 报告“数据不足观察”端到端回放验收（P1，ready，依赖 DATA-021 ✓）。
  106. `IC-TA-002`：investment-controller 上下文接入 TradeFlow 昊天日报与报告数据缺口（P1，done，依赖 IC-TA-001/DATA-021/H-015 ✓）。
107. `V-010`：小资金试跑 v2 验收：候选收敛→观察→日报→报告缺口（P1，done，依赖 V-009/DATA-021/H-015 ✓）。
108. `NOTIFY-002`：飞书/通知草稿接入昊天日报与数据缺口摘要（P2，done — NOTIFY-002-20260626-214133，依赖 TRACK-NOTIFY-001/H-015/DATA-021 ✓）。
109. `DATA-022`：主力资金/龙虎榜失败矩阵 fixture 回放（P1，done，依赖 DATA-021/DATA-017 ✓）。
110. `TF-OBS-005`：非交易日候选计划到下一交易日观察语义回归（P1，done，依赖 TF-OBS-004 ✓）。
 111. `TRACK-008`：观察仓批量导入/导出 CSV 与去重合并（P1，done，依赖 TRACK-001/TRACK-003 ✓）。
112. `REPORT-UX-002`：历史报告动作语义与数据缺口只读迁移预检（P1，ready，依赖 DECISION-004/DATA-021 ✓）。
113. `H-016`：昊天主题日报 CLI 生成与保留策略（P2，ready，依赖 H-015 ✓）。
  114. `IC-TA-003`：investment-controller 盘前/盘后 briefing fixture dry-run（P2，done，依赖 IC-TA-002 ✓）。
115. `TF-REVIEW-005`：盘后 Review 归因接入观察信号与模拟账本（P1，done，依赖 TF-REVIEW-004/TF-PAPER-001 ✓）。
116. `UI-013`：TradeFlow 空状态与下一步 CTA 文案收口（P2，ready，依赖 UI-012/H-015 ✓）。
117. `DATA-023`：数据源目录 API/文档同步与供应商能力矩阵导出（P2，ready，依赖 DATA-020/DATA-CATALOG-FIX ✓）。
118. `PERF-006`：前端 bundle 体积趋势记录与懒加载候选建议（P2，ready，依赖 PERF-005 ✓）。
119. `V-011`：夜间自动开发日报验收与 ready 队列续航检查（P2，ready，依赖 AUTO-003 ✓）。
120. `NOTIFY-003`：通知去噪规则回放测试与日报/盘中分层验收（P2，ready，依赖 NOTIFY-002 ✓）。

### 数据源治理候选队列

> 参考 SimonLin1212 `a-stock-data` 的数据源 Skill 思路：吸收“端点目录、vendor fallback、实时补丁、来源溯源、字段契约”，不替换本项目的 raw_evidence、强动作门禁、Buy/Risk Level 和自动审核闭环。

1. `DATA-P0-603629`：TA A股关键数据源补强与假可用修复（P0，done）。
2. `DATA-001`：A股数据源能力目录与 fallback 矩阵（P1，done）。
3. `DATA-002`：实时行情 freshness 检测与补丁标注（P1，done）。
4. `DATA-003`：公告/研报/政策事件源归一化接入昊天雷达（P1，done）。
5. `DATA-004`：raw_evidence 来源契约升级（P1，done）。
6. `DATA-005`：数据源 fixture replay 与限流/失败回放（P1，done）。
7. `DATA-006`：数据源质量报告接入夜间日报（P2，done，依赖 DATA-005 ✓）。
8. `DATA-P0-FUND-ROUTE`：主力资金 fallback 假成功与单位修复（P0，done）。
9. `DATA-P1-LHB-FUND-DECOUPLE`：龙虎榜与资金流触发链路复核（P1，done）。
10. `DATA-P1-ASTOCK-LIVE-SMOKE`：cn_astock/Eastmoney 关键源 live smoke 与限流验证（P1，done）。
11. `DATA-P1-SOURCE-GAP-AUDIT`：Simon 数据源吸收落地差距审计（P1，done）。
12. `DATA-007`：raw_evidence 覆盖率审计与候选可信度联动（P1，done）。
13. `DATA-008`：A股关键源 fallback smoke fixtures 扩展（P1，done）。
14. `DATA-009`：自选备注与截图识别字段持久化回归保护（P1，done）。
15. `DATA-010`：融资融券数据源注册与 raw_evidence 接入（P1，done）。
16. `DATA-011`：研报端点接入 route_to_vendor 与 raw_evidence（P1，done）。
17. `DATA-012`：评级数据接入 provider 路由与 raw_evidence（P2，done — commit 3a8351f + 4d9ae52，DATA-012A 收口完成）。
17a. `DATA-012A`：评级数据 data_collector 接线与回归验收（P1，done）。
18. `DATA-013`：回购数据接入 provider 路由与 raw_evidence（P2，done）。
19. `DATA-014`：新闻/政策事件 fixture 与 live smoke 补充（P2，done）。
20. `DATA-015`：涨停池 cn_astock fallback 与 fixture（P2，done）。
21. `DATA-016`：热门股票 cn_astock fallback 与 fixture（P2，done）。

### 总体路线图

- 详见 `docs/ROADMAP.md`。
- 当前主线：先稳定 TradeFlow，再建设 Mandate Radar（昊天雷达），再接入轻量 TA / 完整 TA 分层和回放评估。
- 运行原则：快速雷达优先，轻量研究其次，完整 TA 必须人工确认。
- 任务新增原则：新想法必须归入 Roadmap 的某一层；不能直接插队到 ready，除非它阻塞当前主线。

---

## TRACK. 跟踪看板 / 观察仓 / investment-controller 联动任务池（2026-06-22 新增）

> 目标：把跟踪看板从“持仓实时行情表”升级为“交易日作战面板”。TA 负责数据、报告和状态容器；investment-controller 负责盘前/盘中/盘后调度、飞书播报和是否调用深度 TA 的判断。

### TRACK-001: 观察仓数据模型与只读/写入 API（P0）
- **描述**：新增“观察仓”容器，用于存放想买但尚未买入、等待价格/事件/资金确认的标的。
- **优先级**：P0
- **状态**：done — commit 2ef6c37
- **背景**：
  - 当前跟踪看板只展示真实持仓，无法承载“我想进但等位置”的观察票。
  - 观察仓是盘前计划、盘中提醒、盘后复盘和 investment-controller 调度的基础状态。
- **执行约束**：
  - 不写生产 `tradingagents.db` 测试数据。
  - 不触发 TA/LLM。
  - 不自动推送飞书。
  - 不改 `tradingagents/prompts/`。
- **实现要点**：
  1. 新增观察仓表或等价持久化模型，字段至少包含：
     - `symbol/name`
     - `status`: `watching / near_entry / in_entry_zone / ta_required / entered / invalidated / removed`
     - `entry_low/entry_high`
     - `trigger_price`
     - `invalid_price`
     - `horizon`: `intraday / short / mid`
     - `source`: `manual / tradeflow / ta / investment_controller`
     - `reason`
     - `priority`
     - `notes`
     - `created_at/updated_at/last_reviewed_at`
  2. 新增 API：
     - list observation items
     - create/update item
     - mark invalidated/removed
     - optional bulk upsert
  3. API 返回必须区分“真实持仓”和“观察仓”，禁止把观察仓误算入持仓市值。
  4. 测试覆盖创建、更新、状态流转、空数据、symbol/name 规范化。
- **验收方式**：
  - 后端测试通过。
  - 新建观察仓条目后可查询。
  - `entry_low=0`、`entry_high=0` 等边界值不被显示成 `N/A`。
  - 观察仓条目不会污染真实持仓接口。
- **代码标注要求**：`# [TRACK-001] observation_warehouse`

### TRACK-002: 跟踪看板 v2 分组接口与今日指引聚合（P0）
- **描述**：升级 `GET /v1/dashboard/tracking-board` 或新增 v2 接口，返回持仓、观察仓、今日提醒和复盘摘要四类数据。
- **优先级**：P0
- **状态**：done — commit fd0d511
- **前置条件**：TRACK-001 ✓
- **执行约束**：
  - 不触发 TA/LLM。
  - 不自动发通知。
  - 不改变现有 v1 字段含义，前端旧页面必须兼容。
- **实现要点**：
  1. Response 至少包含：
     - `holdings`
     - `observation_items`
     - `today_guidance`
     - `alerts`
     - `review_summary`
     - `data_freshness`
  2. `today_guidance` 聚合：
     - 持仓风险票
     - 观察仓接近买点票
     - 需要 TA 深度确认票
     - 数据不足只记录票
  3. 每条 guidance 必须有 `source`、`as_of`、`reason`，不得给无来源结论。
  4. 复用已有跟踪看板实时行情与最新报告语义字段：`research_direction / execution_action / action_label`。
- **验收方式**：
  - 无持仓、无观察仓时返回空数组和稳定空状态。
  - 有观察仓但无实时行情时标记 `data_freshness`，不报错。
  - 有最新 TA 报告时透传动作语义；没有报告时显示“未分析/需人工确认”。
- **代码标注要求**：`# [TRACK-002] tracking_board_v2`

### TRACK-003: 跟踪看板前端四区改版（P0）
- **描述**：把跟踪看板前端改为“持仓 / 观察仓 / 今日指引 / 盘后复盘”四区。
- **优先级**：P0
- **状态**：done ✓（commit d80aa66）
- **前置条件**：TRACK-002 ✓
- **执行约束**：
  - 不把观察仓 UI 做成交易下单。
  - 不展示强买卖词。
  - 不隐藏数据缺失/非实时提示。
- **实现要点**：
  1. 持仓区：展示真实持仓、当日表现、浮盈、关键价位、最新 TA 动作语义。
  2. 观察仓：展示观察状态、买入区间、触发价、失效价、来源、理由、优先级。
  3. 今日指引：按 P0/P1/P2/P3 或“风险/接近买点/需 TA/仅记录”分组。
  4. 盘后复盘：展示当日是否触发计划、是否失效、明日是否继续观察。
  5. 提供新增/编辑观察仓的轻量表单。
- **验收方式**：
  - `npm run build` 通过。
  - mock 数据下四区均可展示。
  - 空状态有明确文案，不崩溃。
  - 大 A 红涨绿跌视觉一致。
- **代码标注要求**：`// [TRACK-003] tracking_board_frontend`

### IC-TA-001: investment-controller 只读上下文包与数据契约（P1）
- **描述**：为 investment-controller 提供稳定的 TA 侧只读上下文包，作为其盘前/盘中/盘后调度输入。
- **优先级**：P1
- **状态**：done -- commit bc5b7d3
- **前置条件**：TRACK-001 ✓
- **执行约束**：
  - 只读接口，不写状态。
  - 不触发 TA/LLM。
  - 不暴露 API key/token。
  - 输出必须包含数据来源和时间戳。
- **实现要点**：
  1. 新增或扩展只读 API，返回：
     - holdings snapshot
     - observation warehouse
     - TradeFlow latest candidates
     - latest TA report summary
     - data health summary
     - pending TA required items
  2. 所有条目必须包含 `source` 与 `as_of`。
  3. 明确 `data_status`: `fresh / stale / missing / failed / skipped`。
  4. 不输出最终强动作，只输出结构化事实和软状态。
- **验收方式**：
  - 无数据时返回稳定空结构。
  - 有观察仓和持仓时可被 investment-controller 一次读取。
  - 测试确认接口不包含敏感字段。
- **代码标注要求**：`# [IC-TA-001] investment_controller_context`

### TRACK-004: 观察仓入场区/失效区规则引擎与状态流转（P1）
- **描述**：根据实时价、买入区间、触发价、失效价和数据新鲜度，自动给观察仓标的打状态。
- **优先级**：P1
- **状态**：done -- commit 88dfc76
- **前置条件**：TRACK-001、TRACK-002
- **执行约束**：
  - 不输出“立即买入”。
  - 数据缺失时只能标记 `data_missing` 或 `needs_review`。
- **实现要点**：
  1. 状态判断：
     - `watching`
     - `near_entry`
     - `in_entry_zone`
     - `missed_entry`
     - `invalidated`
     - `ta_required`
  2. 判断结果写入 tracking board v2 的 `today_guidance`。
  3. 每个状态必须有 `reason` 和使用的数据字段。
- **验收方式**：
  - 价格进入区间时显示 `in_entry_zone`。
  - 跌破失效价时显示 `invalidated`。
  - 数据缺失时不误判为可入场。
- **代码标注要求**：`# [TRACK-004] observation_state_engine`

### TRACK-005: 盘后复盘摘要与次日计划写回跟踪看板（P1）
- **描述**：盘后为持仓和观察仓生成复盘摘要，回答“今天是否触发计划、明天是否继续看、是否需要 TA”。
- **优先级**：P1
- **状态**：done -- commit 9090e24
- **前置条件**：TRACK-002 ✓、TF-REVIEW-003 ✓
- **执行约束**：
  - 先做规则版，不调用 LLM。
  - 不发真实飞书，只写本地/数据库摘要。
- **实现要点**：
  1. 持仓复盘：涨跌、是否跌破关键位、是否偏离 TA 计划。
  2. 观察仓复盘：是否接近买点、是否失效、是否需要重新 TA。
  3. 候选池复盘：是否触发、是否淘汰、是否进入观察仓。
  4. 输出 `tomorrow_focus`。
- **验收方式**：
  - 非交易日生成的计划可在下一交易日复盘。
  - 空 Review 时能解释“为何无数据”。
  - 输出不含强买卖词。
- **代码标注要求**：`# [TRACK-005] post_market_tracking_review`

### TRACK-006: TradeFlow/TA 候选一键加入观察仓与来源追踪（P1）
- **描述**：从 TradeFlow 候选详情、TA 报告或分析结果页，将标的一键加入观察仓，并保留来源和理由。
- **优先级**：P1
- **状态**：done -- commit 5aa512d
- **前置条件**：TRACK-001 ✓、UI-009 ✓
- **执行约束**：
  - 不自动加入，必须用户点击或 investment-controller 输出草稿。
  - 不覆盖用户手动备注，除非显式确认。
- **实现要点**：
  1. TradeFlow 候选加入观察仓，自动带入：
     - strategy tags
     - trigger price
     - invalid price
     - score
     - why selected
  2. TA 报告加入观察仓，自动带入：
     - action_label
     - research_direction
     - key support/stop/target
  3. 重复 symbol 做 upsert，并保留历史来源。
- **验收方式**：
  - 从候选池加入后，跟踪看板观察仓立即可见。
  - 重复加入不生成重复记录。
  - 用户 notes 不丢失。
- **代码标注要求**：`# [TRACK-006] add_to_observation`

### TRACK-NOTIFY-001: 飞书/总控官通知草稿 payload 与去噪规则（P1）
- **描述**：TA 侧生成给 investment-controller/飞书使用的通知草稿，不直接推送真实 webhook。
- **优先级**：P1
- **状态**：done — TRACK-NOTIFY-001-20260623-122700
- **前置条件**：IC-TA-001、TRACK-002
- **执行约束**：
  - 第一阶段只生成 dry-run payload。
  - 不读取/打印 webhook。
  - 不真实发送飞书。
  - P2/P3 只进日报，不盘中推送。
- **实现要点**：
  1. 定义通知 payload schema：
     - `priority`: `P0/P1/P2/P3`
     - `symbol`
     - `title`
     - `reason`
     - `source`
     - `as_of`
     - `suggested_next_step`
  2. 去噪规则：
     - 同一标的同一事件 30 分钟内不重复。
     - 数据不足只记录，不推送。
     - P0/P1 才允许进入盘中主动提醒队列。
  3. 生成本地 markdown/json 预览，供 OpenClaw/investment-controller 决定是否发。
- **验收方式**：
  - dry-run 生成 payload。
  - 未配置 webhook 不报错。
  - 重复事件被去重。
  - payload 不含强买卖词。
- **代码标注要求**：`# [TRACK-NOTIFY-001] notification_payload_dry_run`

### TRACK-007: 跟踪看板端到端验收与用户操作手册（P2）
- **描述**：对跟踪看板 v2、观察仓、今日指引、复盘摘要和总控官上下文包做端到端验收，并生成用户操作手册。
- **优先级**：P2
- **状态**：done ✓，依赖 TRACK-003 ✓, TRACK-004 ✓, TRACK-005 ✓
- **前置条件**：TRACK-003 ✓、TRACK-004 ✓、TRACK-005 ✓
- **执行约束**：
  - 不触发 live LLM。
  - 不真实推送飞书。
- **验收方式**：
  - mock 持仓 + mock 观察仓 + mock TradeFlow 候选，可完整展示四区。
  - 观察仓状态流转可回放。
  - investment-controller context 可读。
  - 生成 `docs/tracking_board_v2_acceptance.md`。
- **代码标注要求**：`# [TRACK-007] tracking_board_acceptance`

---

## PERF. 运行成本 / 轻量分析任务池（2026-06-02 新增）

> 目标：系统不能为了“全面”而变慢。TradeFlow 是雷达，轻量 TA 是候选验证，完整 TA 是深度体检。默认链路必须轻，完整多 Agent 分析只能由用户明确触发。

### PERF-001: 运行层级与速度预算契约（P1）
- **描述**：建立统一运行层级契约，所有 API、前端按钮、scheduler、自动开发任务都要标明属于 `fast/light/full` 哪一层，以及是否允许调用 LLM。
- **优先级**：P1
- **状态**：done -- commit ca08846
- **背景**：
  - 用户明确要求系统不能做得太重，不能“一发指令半天才回来”。
  - 候选池/盘中观察需要时效；完整 TA 只能用于少量高价值标的。
- **运行层级定义**：
  1. `fast`：5-30 秒。TradeFlow 筛选、数据健康、证据缺口、候选分类。默认不调用 LLM。
  2. `light`：1-3 分钟。轻量 TA，只跑必要模块，用于中线政策验证、短线技术确认、持仓风险复核。
  3. `full`：10-20 分钟。完整 14 Agent TA，多空辩论和风控全链路，必须人工确认。
- **执行约束**：
  - 不改 prompts。
  - 不触发 live TA。
  - 不新增高成本模型调用。
- **实现要点**：
  1. 新增运行层级枚举/配置，例如 `runtime_tier`：
     - `FAST_RADAR`
     - `LIGHT_RESEARCH`
     - `FULL_TA`
  2. API/服务层统一返回：
     - `runtime_tier`
     - `expected_latency`
     - `llm_allowed`
     - `requires_confirmation`
     - `cost_risk`
  3. 前端按钮文案对应：
     - `快速筛选`
     - `轻量研究`
     - `完整 TA`
  4. `TradeFlow Discovery` 和 `Observe` 必须标记为 `FAST_RADAR`。
  5. 自动任务/scheduler 不得默认进入 `FULL_TA`。
- **验收方式**：
  - TradeFlow candidates/observe/data-health API 返回 fast 层级或元数据。
  - 前端能显示运行层级和预计耗时。
  - 没有用户确认时，full TA 入口不可直接触发。
  - `pytest tests/test_runtime_tier*.py tests/test_ui001_tradeflow_api.py -q` 或等价测试通过。
- **代码标注要求**：`# [PERF-001] runtime_tier_contract` / `// [PERF-001] runtime_tier_contract`

### PERF-002: 轻量 TA Profile 与模块路由（P1）
- **描述**：在完整 TA 之外增加轻量 TA Profile，只跑与目标相关的模块，避免所有候选都进入 14 Agent 全链路。
- **优先级**：P1
- **状态**：done -- commit 20ff126
- **前置条件**：`TA-UI-001`、`H-007` 完成或同步完成。
- **执行约束**：
  - 不删除完整 TA。
  - 不改 prompts。
  - 不自动调用 live LLM；第一版可先做路由配置和 mock/fixture。
- **Profile 建议**：
  1. `MIDLINE_POLICY_LIGHT`：
     - 用于 `POLICY_AMBUSH/POLICY_CONFIRM`。
     - 重点：政策/公告/基本面/风险/量价。
  2. `SHORT_TECH_LIGHT`：
     - 用于 `TECH_TRADE`。
     - 重点：技术/量价/资金/风险。
  3. `POSITION_RISK_LIGHT`：
     - 用于已持仓复盘。
     - 重点：风控/资金/关键价位/公告。
  4. `FULL_TA`：
     - 保持完整多 Agent，只能手动确认。
- **实现要点**：
  1. 新增 profile 配置，声明每个 profile 启用的 analyst/manager/risk 模块。
  2. TradeFlow 候选根据 `candidate_type/research_queue/analysis_intent` 推荐 profile。
  3. `/analysis` 控制台选择中线/短线后，能带入对应 profile。
  4. 结果 metadata 中记录 `runtime_profile` 和实际启用模块。
- **验收方式**：
  - `POLICY_AMBUSH` 默认推荐 `MIDLINE_POLICY_LIGHT`。
  - `TECH_TRADE` 默认推荐 `SHORT_TECH_LIGHT`。
  - 已持仓复盘默认推荐 `POSITION_RISK_LIGHT`。
  - 完整 TA 仍可人工选择。
  - 测试覆盖 profile 路由，不触发真实 LLM。
- **代码标注要求**：`# [PERF-002] lightweight_ta_profiles`

### PERF-003: 分析运行耗时/调用成本遥测与前端展示（P1）
- **描述**：为 TradeFlow、轻量 TA、完整 TA 增加运行遥测，记录耗时、模块、模型调用次数、失败原因和成本风险，让用户知道一次分析到底重不重。
- **优先级**：P1
- **状态**：done — commit 059fc21
- **执行约束**：
  - 不记录 API key/token。
  - 不泄露完整 prompt。
  - 不强行估算真实费用；可先记录调用次数和模型名。
- **实现要点**：
  1. 每次运行记录：
     - `started_at`
     - `finished_at`
     - `elapsed_ms`
     - `runtime_tier`
     - `runtime_profile`
     - `modules_run`
     - `llm_provider/model`
     - `llm_call_count`
     - `status/error`
  2. 前端展示：
     - “快速/轻量/完整”
     - “预计耗时/实际耗时”
     - “是否调用模型”
  3. 夜间日报和 task_runs 引入运行摘要。
- **验收方式**：
  - fast TradeFlow 运行有 elapsed_ms。
  - mock light/full TA 有模块和调用次数记录。
  - 前端能展示运行层级和实际耗时。
  - 敏感信息不进入日志。
- **代码标注要求**：`# [PERF-003] runtime_telemetry`

### PERF-004: 完整 TA 手动确认与 scheduler 成本门禁（P1）
- **描述**：防止定时任务、TradeFlow 或前端误触完整 TA。完整 TA 必须显示模型、预计调用、预计耗时，并由用户确认。
- **优先级**：P1
- **状态**：done — PERF-004-20260604
- **执行约束**：
  - 不禁用用户主动设置的定时分析。
  - 不改变已有允许的 scheduler 行为，只增加透明度和门禁。
  - 不打印 API key。
- **实现要点**：
  1. 前端完整 TA 入口增加确认弹窗：
     - provider/base_url/model
     - runtime_tier=`FULL_TA`
     - 预计耗时
     - 预计调用模块
  2. scheduler 任务记录：
     - 是否 full TA
     - 谁创建
     - 触发频率
     - 最近一次模型调用摘要
  3. 自动开发任务禁止触发 full TA。
  4. 定时任务日报显示可能消耗 token 的任务列表。
- **验收方式**：
  - 未确认时 full TA 不启动。
  - 用户主动定时分析仍可运行，但日志明确记录。
  - 日报能看到 scheduler 成本风险。
  - `pytest tests/test_perf004_full_ta_cost_gate.py tests/test_runtime_tier_contract.py tests/test_scheduled_queue.py -q` 或等价测试通过。
- **代码标注要求**：`# [PERF-004] full_ta_cost_gate` / `// [PERF-004] full_ta_cost_gate`

## P0. 2026-06-02 运行态修复与 TA 控制台任务池

> 背景：用户 2026-06-02 生成今日候选池后发现：名称显示代码、候选类型未分类、昊天分/埋伏分/政策主题/角色/自选备注为空、盘中观察执行报错；同时智能分析控制台没有显式短线/中线选择。这些问题阻塞 TradeFlow/TA 日常试用，优先级高于后续策略优化。

### TF-P0-001: TradeFlow 运行态 schema 迁移、名称回填与观察路由修复（P0）
- **描述**：修复本地运行态 `tradeflow.db` 仍为旧 schema，导致 H-004/H-005/H-007/H-008/TF-DATE 字段无法落库；同时修复候选名称等于代码时不回填中文名、盘中观察执行接口在运行后端中不可用的问题。
- **优先级**：P0
- **状态**：done -- commit 26bf33a
- **问题来源**：
  - `tradeflow_candidates` 缺少 `candidate_type/mandate_score_component/ambush_score/mandate_topic/company_role/watchlist_note_suggested/effective_trade_date` 等新列。
  - 今日候选 `name` 被写成 `601689.SH` 等代码，`resolve_tradeflow_name()` 误认为已有名称而不回填。
  - 前端调用 `POST /v1/tradeflow/observe/run`，当前运行后端返回 405，源码已有路由，需确认重启/路由注册/API client 方法一致。
- **执行约束**：
  - 不删除 `tradeflow.db` 历史数据。
  - 不写生产 `tradingagents.db` schema。
  - 不触发 TA/LLM。
  - 不跑全市场扫描。
- **实现要点**：
  1. 增加 TradeFlow schema migration smoke：
     - 应用启动或 Discovery 前必须调用 `init_db()` 或等价迁移。
     - 旧 `tradeflow.db` 能补齐 H/TF-DATE/H-008 字段。
  2. 名称回填修复：
     - 当 `name == symbol`、`name == bare_code`、`name` 形似 `XXXXXX.SH/SZ/BJ` 时，视为缺失并重新解析中文名。
     - API 层 `_row_to_candidate_item()` 也要兜底。
  3. 盘中观察路由修复：
     - 确认 `POST /v1/tradeflow/observe/run?date=YYYY-MM-DD` 在运行服务中可用。
     - 若生产前端仍打到 GET/static fallback，修正 API client 或路由顺序。
  4. 对旧库当日 4 只候选可重新补字段或至少重新生成后字段完整。
- **验收方式**：
  - 旧 schema fixture 运行迁移后包含 `candidate_type/effective_trade_date/watchlist_note_suggested` 等列。
  - `601689.SH` API 返回 `拓普集团`，不再显示代码作为名称。
  - `POST /v1/tradeflow/observe/run?date=2026-06-02` 返回 JSON，不返回 405/HTML。
  - `pytest tests/test_tradeflow_candidate_engine.py tests/test_ui001_tradeflow_api.py tests/test_tf_obs_001_observe_runner.py -q` 或等价测试通过。
  - `npm run build` 通过。
- **代码标注要求**：`# [TF-P0-001] runtime_schema_name_observe_fix` / `// [TF-P0-001] runtime_schema_name_observe_fix`

### TA-UI-001: 智能分析控制台增加短线/中线、分析意图、持仓状态选择（P0）
- **描述**：在 `/analysis` 智能分析控制台显式暴露 TA 分析周期和意图选择，避免用户只能靠自然语言猜测系统是否按中线运行。
- **优先级**：P0
- **状态**：done -- commit 35b90f5
- **背景**：
  - 后端 G-001 已支持 `horizon/analysis_intent/position_context`。
  - 自选/定时分析页已有短线/中线切换，但智能分析页没有。
  - 昊天左侧候选需要默认走中线研究；短线技术池才走短线确认。
- **执行约束**：
  - 不触发 live TA 测试。
  - 不改 prompts。
  - 不输出强买卖词。
- **实现要点**：
  1. `/analysis` 页面增加控件：
     - 周期：`短线` / `中线`
     - 意图：`观察` / `入场研究` / `持仓复盘` / `加仓判断` / `减仓止损`
     - 持仓状态：`未持仓` / `已持仓`
  2. 提交 `/v1/analyze` 时写入：
     - `horizons: ["short"|"medium"]`
     - `user_context.analysis_intent`
     - `user_context.position_context`
     - 或后端当前接受的等价字段。
  3. UI 选择优先于自然语言推断；自然语言仍可补充具体问题。
  4. 报告/聊天状态顶部展示本次上下文：
     - `本次分析：中线｜入场研究｜未持仓`
  5. 从 TradeFlow 跳转 `/analysis?symbol=...` 时：
     - `POLICY_AMBUSH` 默认中线 + 政策验证/入场研究。
     - `TECH_TRADE` 默认短线 + 趋势确认。
- **验收方式**：
  - 前端选择中线后，请求 payload 含 `medium`。
  - 选择未持仓入场研究时，后端最终 `analysis_intent=entry` 且 `position_context.has_position=false`。
  - 选择已持仓复盘时，后端最终 `analysis_intent=holding` 且 `position_context.has_position=true`。
  - TradeFlow 左侧候选跳转智能分析时默认中线。
  - `npm run build` 通过；相关 API/前端测试通过。
- **代码标注要求**：`# [TA-UI-001] analysis_console_horizon_intent` / `// [TA-UI-001] analysis_console_horizon_intent`

### TF-P0-002: 候选池分层：短线技术池与昊天左侧池显式拆分（P0）
- **描述**：把 TradeFlow 候选池明确拆成“短线技术池”和“昊天左侧池”，防止纯 VCP/回踩支撑候选被误解为昊天战法候选。
- **优先级**：P0
- **状态**：done -- commit 327a7c7
- **背景**：
  - 2026-06-02 今日 4 只候选均来自 watchlist，策略为 `PULLBACK_SUPPORT/VCP`，只有技术信号，数据完整度约 38%-40%。
  - 用户主目标是昊天左侧中线，但当前候选更像短线做 T。
- **执行约束**：
  - 不改变已有技术策略，只增加分类和展示。
  - 不输出买卖建议。
  - 不调用 LLM。
- **实现要点**：
  1. 候选生成时明确 `candidate_type`：
     - 有政策/事件/受益路径证据：按 `POLICY_AMBUSH/POLICY_CONFIRM/EVENT_WATCH`。
     - 仅 VCP/PULLBACK_SUPPORT：标记 `TECH_TRADE`。
     - 无法分类：标记 `UNCLASSIFIED_DATA_GAP` 或给出缺口原因，而不是空字符串。
  2. 前端候选页增加池子筛选：
     - 全部
     - 昊天左侧
     - 政策确认
     - 短线技术
     - 事件观察
     - 证据缺口
  3. 短线技术池隐藏/弱化昊天字段，显示：
     - 技术策略、触发价、失效价、数据完整度、为什么不是昊天票。
  4. 昊天池必须展示：
     - 昊天分、埋伏分、政策主题、公司角色、受益路径、自选备注、反证缺口。
- **验收方式**：
  - 纯 VCP/PULLBACK 样本显示 `TECH_TRADE`，不再“未分类”。
  - 有政策证据样本显示 `POLICY_AMBUSH/POLICY_CONFIRM`。
  - 前端切换“短线技术池/昊天左侧池”结果正确。
  - `pytest tests/test_h004_ambush_score.py tests/test_ui001_tradeflow_api.py tests/test_tradeflow_*.py -q` 通过；`npm run build` 通过。
- **代码标注要求**：`# [TF-P0-002] tradeflow_pool_split` / `// [TF-P0-002] tradeflow_pool_split`

### TF-P0-003: TradeFlow 生成候选池后的端到端 UI smoke 验收（P0）
- **描述**：建立一个低成本端到端 smoke，模拟“生成今日候选池 → 前端候选页 → 盘中观察 → 候选详情 → 跳 TA 预案”的完整链路，专门防止字段落库和前端显示断裂。
- **优先级**：P0
- **状态**：done -- commit 61564c0
- **前置条件**：`TF-P0-001`、`TF-P0-002` 完成或同步完成。
- **执行约束**：
  - 不跑 live TA。
  - 不调用 LLM。
  - 不依赖当天真实行情。
  - 不写生产 DB。
- **实现要点**：
  1. 使用 fixture DB 或临时 `tradeflow.db`。
  2. 构造至少 3 类候选：
     - `TECH_TRADE`
     - `POLICY_AMBUSH`
     - `UNCLASSIFIED_DATA_GAP` 或证据缺口样本。
  3. 验证 API：
     - `/candidates`
     - `/observe`
     - `/candidate detail`
     - `/ta-queue` 或研究预案。
  4. 前端 smoke 可用 Playwright 或组件级 mock：
     - 名称不是代码。
     - 候选类型不是空。
     - 昊天字段在政策候选中可见。
     - 技术候选显示“短线技术池”语义。
- **验收方式**：
  - smoke 报告写入 `docs/tradeflow_acceptance/` 或测试 snapshot。
  - `pytest tests/test_ui001_tradeflow_api.py tests/test_tradeflow_*.py -q` 通过。
  - `npm run build` 通过。
- **代码标注要求**：`# [TF-P0-003] tradeflow_ui_e2e_smoke` / `// [TF-P0-003] tradeflow_ui_e2e_smoke`

## TF. TradeFlow 主链路稳定任务池（2026-06-01 新增）

> 目标：先把 TradeFlow 的时间语义和盘中观察闭环打通，再继续建设昊天雷达。TradeFlow 允许周末/盘后生成候选池，并在下一个 A 股交易日观察；不能把候选生成日期、计划生效日期和实际观察日期混成同一个字段。

### TF-DATE-001: TradeFlow 日期语义拆分与非交易日计划生效（P0）
- **描述**：修复 `trade_date` 同时表示“候选池生成日期/计划生效交易日/盘中观察日期”的问题，支持 2026-05-31 这类非交易日生成候选池，并在 2026-06-01 盘中继续观察。
- **优先级**：P0
- **状态**：done -- commit a200103
- **背景**：
  - 用户可以在周末、节假日或交易日收盘后生成候选池，这些候选应在下一个 A 股交易日进入 Observe，而不是被锁在生成日。
  - 当前 `tradeflow_daily_plans` 和 `tradeflow_candidates` 主要按 `trade_date` 查询，导致前端选择 2026-06-01 的 Observe 时查不到 2026-05-31 生成的候选。
  - 这会阻塞“周末研究/盘前计划/次日盯盘”的实际工作流。
- **日期语义定义**：
  1. `plan_date`：候选池生成日期，周末/节假日也允许。
  2. `effective_trade_date`：计划生效交易日，用于盘中观察和 TA 队列。
  3. `observe_date`：实际观察日期；第一版可等同 `effective_trade_date`，但 API/前端命名要预留。
- **执行约束**：
  - 不写生产 `tradingagents.db` schema；只允许修改 TradeFlow 自有 SQLite 表、兼容迁移或服务层查询。
  - 不触发真实 TA/LLM 调用。
  - 不删除现有 `tradeflow.db` 历史数据。
  - 保持旧数据兼容：没有新字段的旧行仍可按原 `trade_date` 查询。
- **实现要点**：
  1. 新增日期 helper，例如 `resolve_effective_trade_date(plan_date, now=None)`：
     - 非交易日生成：`effective_trade_date = 下一个 A 股交易日`。
     - 交易日盘前生成：`effective_trade_date = 当天`。
     - 交易日盘中生成：默认 `effective_trade_date = 当天`。
     - 交易日收盘后生成：`effective_trade_date = 下一个 A 股交易日`。
  2. `tradeflow_daily_plans` / `tradeflow_candidates` 兼容新增字段：
     - `plan_date`
     - `effective_trade_date`
     - `observe_date`
  3. 生成候选池时：
     - `trade_date` 保持兼容旧字段，可暂等于 `plan_date`。
     - 新字段必须写入 DB 和 daily plan metadata/candidates_json。
  4. Observe / TA Queue / Review 查询时：
     - 优先按 `effective_trade_date` 或 `observe_date` 查询。
     - 如果新字段不存在或为空，fallback 到旧 `trade_date`。
     - 查询 `2026-06-01` Observe 时，应能找到 `plan_date=2026-05-31, effective_trade_date=2026-06-01` 的候选。
  5. 前端 TradeFlow 显示：
     - 候选池日期：`plan_date`
     - 生效交易日：`effective_trade_date`
     - 观察日期：`observe_date`
     - 当三者不同，显示提示：“该候选池由 5月31日生成，将在 6月1日观察”。
  6. 数据健康页补充：
     - `latest_plan_date`
     - `latest_effective_trade_date`
     - `latest_observe_date`
- **验收方式**：
  - 构造 2026-05-31 非交易日生成候选池，`effective_trade_date` 自动为 2026-06-01。
  - `GET /v1/tradeflow/observe?date=2026-06-01` 能返回 2026-05-31 生成的候选。
  - `GET /v1/tradeflow/candidates?date=2026-05-31` 仍能看到候选池本身。
  - 交易日盘前、盘中、盘后三种场景有单测覆盖。
  - 旧库无新字段时 API 不报 500。
  - `pytest tests/test_ui001_tradeflow_api.py tests/test_tradeflow_*.py -q` 通过；前端构建通过。
- **代码标注要求**：`# [TF-DATE-001] tradeflow_date_semantics` / `// [TF-DATE-001] tradeflow_date_semantics`

### TF-OBS-001: TradeFlow 盘中观察执行器与信号落库（P0）
- **描述**：在 TF-DATE-001 之后，把现有 `run_observe_check()` 状态机接入真实执行链路，定时读取当日生效候选、拉取实时价格、更新观察状态并写入信号表。
- **优先级**：P0
- **状态**：done -- commit f8db3de
- **前置条件**：`TF-DATE-001` 完成 ✓
- **背景**：
  - 当前 M-005 已有 Observe 状态机，前端也能展示 Observe Tab，但没有 runner 定时执行，所以 `observe_state` 长期停留在 `WAITING`，`tradeflow_signals` 为空。
  - Deep TA 门控依赖 `observe_state=TRIGGERED`，因此盘中观察不落地会连带阻塞 TA 队列。
- **执行约束**：
  - 不自动调用高成本 LLM。
  - 不自动输出强买卖词。
  - 盘中检查频率第一版保持低频，默认 30 分钟或人工触发。
  - 非交易日不执行实时观察，只输出明确状态。
- **实现要点**：
  1. 新增 observe runner/service：
     - 读取 `effective_trade_date = today` 且 `status='active'` 的候选。
     - 拉取实时 `current_price/current_volume/current_amount/quote_time/source`。
     - 调用 `run_observe_check()`。
     - 更新 `tradeflow_candidates.observe_state/observe_trigger_count/observe_first_trigger_time`。
     - 写入 `tradeflow_signals` 保存每次检查快照和原始证据。
  2. API 增加人工触发入口或 scheduler 可调用函数：
     - 第一版可仅内部函数 + CLI，不必自动 cron。
  3. 前端 Observe Tab 增加状态：
     - 未运行
     - 已运行但无触发
     - 已触发
     - 已失效
     - 非交易日/无实时行情
  4. Data Health 显示最新 observe 检查时间和最新 signal 时间。
- **验收方式**：
  - 构造候选触发价 10、实时价 10.1，执行后 `observe_state=TRIGGERED` 且 signal 落库。
  - 构造实时价跌破失效价，执行后 `observe_state=INVALIDATED`。
  - 构造非交易日，runner 不拉实时行情，返回明确 skip reason。
  - `tradeflow_signals` 有记录，前端 Observe 能显示 `current_price` 和 `trigger_reason`。
  - `pytest tests/test_m005_intraday_observe.py tests/test_ui001_tradeflow_api.py tests/test_tradeflow_*.py -q` 通过。
- **代码标注要求**：`# [TF-OBS-001] tradeflow_observe_runner` / `// [TF-OBS-001] tradeflow_observe_runner`

---

## DATA. 数据源治理 / Simon 数据 Skill 吸收任务池（2026-05-31 新增）

> 目标：把外部 A 股数据 Skill 的优点吸收进本项目 Data Layer，让候选池和 TA 报告更真实、更实时、更可追溯。第一阶段只做数据治理和证据链，不新增 LLM Agent，不替换现有 TA 主链路。

### DATA-P0-603629: TA A股关键数据源补强与假可用修复（P0）
- **描述**：基于 603629.SH 最新 TA 报告暴露的问题，补强 A 股关键数据源路由，修复“正文说数据缺失、底部却显示数据源可用”的假可用问题。
- **优先级**：P0
- **状态**：done -- commit a6d4802
- **背景**：
  - 603629.SH 报告中个股资金流 `ConnectionError`，近 20 日主力净流入/流出缺失。
  - 龙虎榜仅依赖“资金流异常触发”，导致资金流失败后龙虎榜也未强制查询；但该股存在严重异常波动、新闻明确提及龙虎榜、6月1日一字跌停等高风险特征。
  - 换手率、量比、板块资金、公告原文/监管事件、复权口径等关键字段未稳定进入 raw_evidence。
  - 底部数据源可用性将“主力资金报告文本存在”误判为“主力资金数据可用”，与 Evidence Coverage 42% 冲突。
- **参考方向**：
  - 参考 SimonLin1212 `a-stock-data` 的数据源优先级：行情/K线/实时价优先 mootdx/腾讯；东财只用于独有数据，并统一限流防封。
  - 不照搬整套框架，只吸收 direct endpoint、fallback、限流、来源契约和字段 freshness 思路。
- **执行约束**：
  - 不提交任何 API key/cookie。
  - 不改 `tradingagents/prompts/`。
  - 不写生产 `tradingagents.db` schema。
  - 不自动触发 TA/LLM live 调用。
  - 东财接口必须串行限流，禁止并发压测。
- **实现要点**：
  1. **个股资金流 fallback**：
     - 为 `get_individual_fund_flow` 增加 Eastmoney `push2/push2his` 直连 fallback。
     - 保留 AKShare，但失败时不得直接终止资金流证据链。
     - raw_evidence 必须记录 `vendor/endpoint/as_of/unit/status/error/fallback_vendor`。
  2. **龙虎榜与资金流解耦**：
     - 以下场景必须 force 查询龙虎榜：严重异常波动、连续涨跌停、新闻/公告文本出现龙虎榜、涨跌幅偏离、成交额/量比异常。
     - 资金流失败不得阻止龙虎榜查询。
     - 区分 `NOT_QUERIED`、`NORMAL_NO_DATA`、`FAILED`、`HAS_DATA`。
  3. **实时行情与换手率/量比补强**：
     - 增加 Tencent fallback，至少提供 `current_price`、`turnover_rate`、`volume_ratio`、`limit_up/down`、`market_cap`、`PE/PB`。
     - 若 `volume_ratio` 无法由源直接提供，可用当前量/近 N 日均量计算，但必须标注 `computed=True` 和计算口径。
  4. **公告/监管事件补强**：
     - 增加 cninfo/巨潮公告 fallback，用于风险提示、严重异常波动、减持、问询函、澄清公告。
     - 公告必须以原始标题/日期/来源/链接或公告 ID 进入 raw_evidence。
  5. **复权口径与 K 线 freshness**：
     - 市场技术数据必须标注 `adjustment`（前复权/后复权/不复权/未知）。
     - 当天实时补丁不得伪装成完整日 K。
  6. **数据源可用性修正**：
     - 底部数据源可用性必须按 raw_evidence status 判断，不能按报告文本是否存在判断。
     - 若主力资金 raw status 为 `FAILED/NOT_QUERIED`，不得显示 `✅ 主力资金`。
     - `Evidence Coverage < 70%` 时，最终 `Confidence` 不得显示“高”。
- **验收方式**：
  - 用 603629.SH 的 fixture 或 mock 复现：
    - 资金流 AKShare 失败，但 Eastmoney fallback 成功。
    - 资金流失败时，龙虎榜仍因异常波动/新闻触发 force 查询。
    - 主力资金失败时，数据源可用性显示 `❌` 或明确 `FAILED`，不能显示 `✅`。
    - 报告底部能看到换手率、量比、复权口径、公告来源。
    - `Evidence Coverage=42%` 一类低证据场景下，Confidence 自动降为中/低。
  - 新增/更新测试覆盖 readiness/raw_evidence/fund_lhb provenance。
  - `pytest tests/test_g006_raw_evidence_snapshot.py tests/test_g007_fund_lhb_provenance.py tests/test_readiness_score.py tests/test_dataflows*.py -q` 或同等相关测试通过。
- **代码标注要求**：`# [DATA-P0-603629] astock_source_fallback`

### DATA-P0-FUND-ROUTE: 主力资金 fallback 假成功与单位修复（P0）
- **描述**：修复主力资金 AKShare 失败字符串被路由层误判为成功的问题，确保 Eastmoney/cn_astock fallback 真正生效，并统一资金流单位。
- **优先级**：P0
- **状态**：done — commit c0bc042
- **背景**：
  - 现场验证 `route_to_vendor("get_individual_fund_flow", "603629.SH")` 命中 `cn_akshare` 后返回 `ProxyError` 失败字符串，路由层仍记录 `vendor=cn_akshare status=hit`，没有继续 fallback 到 `cn_astock`。
  - 直接调用 `CnAstockProvider.get_individual_fund_flow("603629.SH")` 可取得 Eastmoney push2his 近 20 日资金流，说明不是股票天然缺主力资金，而是 fallback 链路被失败字符串截断。
  - `cn_astock_provider` 当前输出文案写“单位：万元”，但 Eastmoney push2his 原始金额看起来是元级，需要统一 `/10000` 转万元并标注单位校验。
- **执行约束**：
  - 不跑全市场 live 压测。
  - 不提交 cookie/API key/proxy 配置。
  - 不把失败数据填成推测值。
  - 不改 `tradingagents/prompts/`。
- **实现要点**：
  1. 修复 provider route 的“失败字符串假成功”：
     - 对 `get_individual_fund_flow`、`get_lhb_detail`、`get_announcements` 等关键源，返回文本包含 `数据获取失败`、`ProxyError`、`ConnectionError`、`Max retries exceeded`、`Unable to connect`、`暂不可用` 时，不得视为 successful hit。
     - 可选方案 A：在 `route_to_vendor()` 中识别失败文本并继续 fallback。
     - 可选方案 B：改 `CnAkshareProvider.get_individual_fund_flow()`，异常时抛出 provider unavailable 异常，由路由层继续 fallback。
  2. `get_last_hit_vendor("get_individual_fund_flow")` 必须记录最终真实 vendor，例如 fallback 成功时为 `cn_astock`。
  3. `CnAstockProvider.get_individual_fund_flow()` 金额单位统一：
     - Eastmoney push2his 原始金额按元处理。
     - 输出前统一 `/10000` 转为万元。
     - 文本注明 `单位：万元`。
     - raw_evidence 对应 entry 中 `unit=万元`、`unit_verified=True`。
  4. 保持失败状态透明：
     - 如果 AKShare 和 cn_astock 都失败，最终 status 为 `FAILED`，error 保留最后失败原因。
     - 不允许失败字符串被下游当作 `HAS_DATA`。
- **验收方式**：
  - mock AKShare 返回/抛出 ProxyError，cn_astock 返回有效资金流，断言最终结果来自 `cn_astock`。
  - 603629 类 fixture：AKShare 失败但 Eastmoney fallback 成功，raw_evidence.vendor=`cn_astock`，fallback_from=`cn_akshare`。
  - 金额单位转换测试：输入元级原始值，输出万元级文本和 unit_verified=True。
  - `pytest tests/test_data_p0_fund_route.py tests/test_g007_fund_lhb_provenance.py tests/test_data_source_replay.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-P0-FUND-ROUTE] fund_flow_fallback_truth`

### DATA-P1-LHB-FUND-DECOUPLE: 龙虎榜与资金流触发链路复核（P1）
- **描述**：复核并补强龙虎榜查询与主力资金链路的解耦，确保资金流失败不会阻断龙虎榜按异常条件 force 查询，同时明确"无龙虎榜"和"未查询/失败"的区别。
- **优先级**：P1
- **状态**：done -- commit b298cf5
- **前置条件**：`DATA-P0-FUND-ROUTE` 完成。
- **背景**：
  - 龙虎榜不是所有股票每天都有；普通交易日 `force=True` 无记录应为 `NORMAL_NO_DATA`，不是接口失败。
  - 当前 smart_money/data_collector 仍有“先看资金流异常，再决定是否 force 龙虎榜”的强耦合痕迹；当资金流失败时，异常波动/新闻提及龙虎榜/涨跌停等条件仍应能触发 force。
- **实现要点**：
  1. force 条件至少覆盖：
     - 新闻/公告文本出现“龙虎榜”。
     - 连续涨停/跌停、一字板、严重异常波动。
     - 涨跌幅偏离、量比/成交额异常。
     - 资金流可用且显示明显异动。
  2. 资金流失败只影响资金流证据，不得阻止 LHB force 条件继续判断。
  3. raw_evidence.lhb 必须稳定输出四态：
     - `NOT_QUERIED`：force=False，未触发查询。
     - `NORMAL_NO_DATA`：force=True，但当日未上榜。
     - `FAILED`：接口失败/解析失败。
     - `HAS_DATA`：有龙虎榜记录。
  4. 报告底部/数据源摘要：
     - `NORMAL_NO_DATA` 显示为“非异动日无龙虎榜”，不能显示红色失败。
     - `NOT_QUERIED` 显示为“未触发查询”，不能说“无龙虎榜数据”。
- **验收方式**：
  - 构造资金流失败 + 新闻出现龙虎榜，断言仍 force 查询 LHB。
  - 构造 force=True 但无记录，断言状态为 `NORMAL_NO_DATA`。
  - 构造 force=False，断言状态为 `NOT_QUERIED`。
  - 构造接口异常，断言状态为 `FAILED`。
  - `pytest tests/test_g007_fund_lhb_provenance.py tests/test_readiness_score.py -q` 通过。
- **代码标注要求**：`# [DATA-P1-LHB-FUND-DECOUPLE] lhb_fund_decouple`

### DATA-P1-ASTOCK-LIVE-SMOKE: cn_astock/Eastmoney 关键源 live smoke 与限流验证（P1）
- **描述**：建立低频 live smoke 脚本/测试，用少量固定股票验证 cn_astock/Eastmoney 关键端点可用性、限流和字段单位，防止“fixture 通过但实盘接口失效”。
- **优先级**：P1
- **状态**：done -- commit 7e0a5a0
- **前置条件**：`DATA-P0-FUND-ROUTE` 完成。
- **执行约束**：
  - 默认测试必须 mock/fixture；live smoke 必须通过环境变量显式开启，例如 `TA_LIVE_DATA_SMOKE=1`。
  - live smoke 样本不超过 3 只股票。
  - 东财接口串行请求，单端点请求间隔必须限流。
  - 不写生产数据库。
- **实现要点**：
  1. 增加 live smoke 脚本或 pytest marker，覆盖：
     - `cn_astock` 个股资金流 push2his。
     - `cn_astock` 龙虎榜 datacenter。
     - 腾讯/新浪实时 quote。
     - CNInfo/公告端点可用性（只查少量样本）。
  2. 输出 `docs/data_source_reports/live-smoke-YYYY-MM-DD.md`，包含：
     - endpoint、vendor、status、latency、field_count、unit、error。
  3. live smoke 失败不应直接阻断普通测试，但应在夜间日报标红。
  4. 与 `M-008` 数据源健康检查和 `DATA-006` 日报聚合对接。
- **验收方式**：
  - 未设置 `TA_LIVE_DATA_SMOKE` 时 live 测试 skip。
  - 设置后只请求固定 1-3 只样本，输出报告。
  - endpoint 失败时报告 `FAILED`，不能写成 `NORMAL_NO_DATA`。
  - `pytest tests/test_data_source_replay.py tests/test_m008_health_check.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-P1-ASTOCK-LIVE-SMOKE] astock_live_smoke`

### DATA-P1-SOURCE-GAP-AUDIT: Simon 数据源吸收落地差距审计（P1）
- **描述**：对照 SimonLin1212 `a-stock-data` / A股数据 Skill 思路，审计本项目已经吸收和仍未落地的数据源能力，输出可执行差距清单。
- **优先级**：P1
- **状态**：done — commit 9be871f
- **前置条件**：`DATA-001`、`DATA-004` 完成 ✓。
- **背景**：
  - 已落地部分：source_catalog、cn_astock provider 注册、raw_evidence contract、fixture replay、数据源日报、事件源归一化。
  - 未完全落地部分：fallback 执行链仍可能被失败字符串截断；live smoke 不稳定；部分字段源仍只在目录中登记，未真正进入 TA/TradeFlow；资金流单位和 fallback vendor 溯源仍有缺口。
- **实现要点**：
  1. 新增审计文档 `docs/DATA_SOURCE_GAP_AUDIT.md` 或更新既有数据源文档。
  2. 按数据类型输出矩阵：
     - OHLCV/实时 quote/换手率/量比。
     - 个股资金流/板块资金。
     - 龙虎榜/融资融券。
     - 公告/研报/评级/回购。
     - 新闻/政策事件。
  3. 对每项标注：
     - 已登记 source_catalog？
     - provider 是否已实现？
     - 是否进入 route fallback？
     - 是否进入 raw_evidence？
     - 是否进入报告/前端？
     - 是否有 fixture？
     - 是否有 live smoke？
  4. 输出下一轮任务建议，不得把未实现项写成 done。
- **验收方式**：
  - 审计文档能解释为什么“之前说接了 fallback，但主力资金仍失败”：失败字符串假成功导致 route 没继续 fallback。
  - 文档列出至少 5 个未落地风险和对应任务编号。
  - 不修改生产代码，除非只增加无副作用 helper/test。
  - `rg` 检查审计文档包含 `fund_flow`、`lhb`、`raw_evidence`、`fallback`、`live smoke`。
- **代码标注要求**：`# [DATA-P1-SOURCE-GAP-AUDIT] source_gap_audit`

### DATA-001: A股数据源能力目录与 fallback 矩阵（P1）
- **描述**：建立本项目统一的数据源能力目录，明确每个 vendor/endpoint 能提供什么字段、适用场景、freshness、限流风险和 fallback 顺序。
- **优先级**：P1
- **状态**：done -- commit 9821932
- **背景**：
  - SimonLin1212 `a-stock-data` 的核心启发不是 Agent 数量，而是把腾讯/东财/新浪/巨潮/财联社等数据源做成可调用、可组合的端点目录。
  - 本项目已有 AKShare、BaoStock、yfinance、cn_astock、event_source，但调用关系和 fallback 口径还不够透明。
- **执行约束**：
  - 不保存 API key/cookie。
  - 不做全市场 live 压测。
  - 不把任何单一第三方源设为唯一真相。
  - 不改生产数据库。
- **实现要点**：
  1. 新增或完善数据源能力配置，例如 `tradingagents/dataflows/source_catalog.py`。
  2. 至少覆盖：
     - 行情/OHLCV/实时价：AKShare、cn_astock/Tencent、BaoStock、yfinance。
     - 资金流/板块资金：AKShare/Eastmoney 类接口。
     - 龙虎榜/融资融券：AKShare/Eastmoney 类接口。
     - 公告/研报/评级/回购：event_source、CNInfo/Juchao、Eastmoney、Cailianshe 类接口。
  3. 每个 source 记录：
     - `vendor`、`endpoint`、`data_type`、`fields`、`unit`、`freshness`、`rate_limit_risk`、`fallback_priority`、`known_gaps`。
  4. 输出统一 helper：按 `data_type` 查询候选 source 和 fallback 顺序。
- **验收方式**：
  - 单测覆盖按 `data_type=quote/fund_flow/notice/report` 返回正确 source 顺序。
  - 缺少字段或单位未知的数据源不得标为 `primary`。
  - `pytest tests/test_data_source_catalog.py -q` 或同等测试通过。
- **代码标注要求**：`# [DATA-001] source_catalog`

### DATA-002: 实时行情 freshness 检测与补丁标注（P1）
- **描述**：解决“当天实时数据没有/日线滞后”的问题：检测日线数据是否 stale，并用实时行情源补 current price、volume、amount，同时明确标注补丁来源。
- **优先级**：P1
- **状态**：done -- commit 3e5f716
- **前置条件**：`DATA-001` 完成 ✓。
- **执行约束**：
  - 不把实时补丁伪装成完整日 K。
  - 不用补丁数据覆盖历史原始 OHLCV。
  - 不输出强买卖动作。
- **实现要点**：
  1. 新增 freshness 判断：交易日、当前时间、最新 bar 日期、更新时间窗口。
  2. 当日线缺当天数据时，从 realtime quote 源补：
     - `current_price`、`current_volume`、`current_amount`、`quote_time`、`realtime_vendor`。
  3. 在 raw_evidence / candidate evidence 写入：
     - `is_realtime_patched=True`
     - `patch_fields=[...]`
     - `patch_source`
     - `patch_as_of`
  4. 前端数据健康面板显示：历史数据正常但实时补丁存在，避免误以为全字段都是日线原始值。
- **验收方式**：
  - 构造最新日线停留在昨日、实时 quote 有今日数据，候选 evidence 显示 patch。
  - 构造非交易时段/实时 quote 失败，不错误 patch。
  - 单测验证成交额/成交量单位不混淆。
- **代码标注要求**：`# [DATA-002] realtime_freshness_patch`

### DATA-003: 公告/研报/政策事件源归一化接入昊天雷达（P1）
- **描述**：把公告、回购、评级、研报标题、政策新闻等事件源统一转换为 `MandateSignal`，让昊天雷达能消费真实事件，而不是只靠关键词。
- **优先级**：P1
- **状态**：done -- commit 27b8635
- **前置条件**：`H-001` 完成 ✓。
- **执行约束**：
  - 不自动相信媒体标题；必须保留来源级别和原始标题。
  - 不抓取大规模全文；第一版以标题/摘要/链接/日期为主。
  - 不调用 LLM。
- **实现要点**：
  1. 对接现有 `event_source.fetch_daily_events()` 和 cn_astock/CNInfo 类公告能力。
  2. 统一输出字段：
     - `symbol`、`title`、`source`、`source_level`、`date`、`url`、`event_type`、`topic_tags`、`direction`。
  3. 区分：
     - 政策原文/部委会议/地方政策/公司公告/券商研报/媒体新闻。
  4. 研报只能作为“观点/线索”，不能替代公告或政策原文。
  5. 同一标题多源转载去重，保留最高权威来源和所有 raw refs。
- **验收方式**：
  - 同一政策事件多源转载不会重复加分。
  - 券商研报标题不会被当作中央政策。
  - 公司公告可以生成公司层证据，但不能自动提升为政策级别。
- **代码标注要求**：`# [DATA-003] mandate_event_normalization`

### DATA-004: raw_evidence 来源契约升级（P1）
- **描述**：把所有关键字段的来源、端点、时间、单位、状态纳入 raw_evidence，使报告和前端都能回答“这个数从哪里来、是否实时、单位是什么、是否 fallback”。
- **优先级**：P1
- **状态**：done -- commit pending
- **前置条件**：`DATA-001` 完成 ✓。
- **执行约束**：
  - 不提交任何明文密钥。
  - 不大改报告 prompt。
  - 不把缺失数据用推测值填满。
- **实现要点**：
  1. 定义最小 evidence contract：
     - `field`、`value`、`unit`、`vendor`、`endpoint`、`as_of`、`fetched_at`、`status`、`fallback_from`、`source_url`、`error`。
  2. 覆盖关键字段：
     - OHLCV、成交量、成交额、换手率、量比、资金流、龙虎榜、两融、公告、研报/评级。
  3. 数据完整度评分从 contract 读取，不再只看文本是否出现。
  4. 报告底部数据源摘要和前端数据健康面板共用该 contract。
- **验收方式**：
  - fallback 命中时 raw_evidence 显示实际 vendor，而不是默认 akshare。
  - 单位未知或字段冲突时完整度降级。
  - `pytest tests/test_data004_evidence_contract.py tests/test_g006_raw_evidence_snapshot.py tests/test_raw_evidence_vendor.py -q` 通过。
- **代码标注要求**：`# [DATA-004] raw_evidence_contract`

### DATA-005: 数据源 fixture replay 与限流/失败回放（P1）
- **描述**：建立可重复的数据源回放测试，覆盖正常、缺字段、限流、超时、来源冲突、当天实时缺失等场景，避免夜间自动开发误判数据源质量。
- **优先级**：P1
- **状态**：done -- commit 9400f32
- **前置条件**：`DATA-001`、`DATA-004` 完成 ✓。
- **执行约束**：
  - fixture 不包含 cookie/key。
  - live smoke 默认跳过，必须显式开启。
  - 不依赖真实当天市场状态才能通过。
- **实现要点**：
  1. 增加 fixtures：
     - 正常行情、日线 stale、实时 quote 成功、实时 quote 失败、资金流单位异常、龙虎榜无触发、公告源失败。
  2. 增加 replay runner，输出数据源健康报告。
  3. 自动开发夜间巡检可运行 fixture replay，不跑高成本 live 请求。
  4. 失败时写入 `docs/data_source_reports/YYYY-MM-DD.md`。
- **验收方式**：
  - 所有 fixture 输出稳定。
  - 限流/超时不会被当作“无数据”。
  - fixture replay 可被 `scripts/auto_dev_loop.sh` 或 OpenClaw 巡检调用。
- **代码标注要求**：`# [DATA-005] data_source_replay`

### DATA-006: 数据源质量报告接入夜间日报（P2）
- **描述**：把 DATA-005 的 fixture replay 结果接入夜间自动开发日报，让第二天能直接看到数据源健康、失败类型和是否影响候选池/TA 报告可信度。
- **优先级**：P2
- **状态**：done -- commit 82da247
- **前置条件**：`DATA-005` 完成或已有 replay runner 雏形。
- **执行约束**：
  - 不跑 live 全市场请求。
  - 不输出任何 API key、cookie、token。
  - 不修改生产数据库。
- **实现要点**：
  1. 扩展夜间日报聚合逻辑，读取 `docs/data_source_reports/` 或 DATA-005 replay 输出。
  2. 报告按数据类型聚合：行情、实时补丁、资金流、龙虎榜、公告/研报、raw_evidence contract。
  3. 对每类输出：OK/PARTIAL/FAILED、失败样本、是否影响 TradeFlow 候选、是否影响 TA readiness。
  4. 当 replay 不存在时，日报明确显示 `NOT_RUN`，不能假装通过。
- **验收方式**：
  - 构造一个 replay 报告 fixture，夜间日报能展示数据源健康摘要。
  - replay 缺失时显示 `NOT_RUN`。
  - 不泄露敏感环境变量。
- **代码标注要求**：`# [DATA-006] data_source_report_daily`

### DATA-007: raw_evidence 覆盖率审计与候选可信度联动（P1）
- **描述**：把 raw_evidence contract 的覆盖情况转成可读审计结果，并联动 TradeFlow 候选可信度，避免“候选分数高但关键数据缺证据”的情况继续进入高优先级。
- **优先级**：P1
- **状态**：done -- commit a9ab775
- **前置条件**：`DATA-004`、`DATA-006` 完成 ✓。
- **执行约束**：
  - 不调用外部 LLM。
  - 不跑 live 全市场请求。
  - 不修改生产数据库。
  - 不把缺失字段用推测值补满。
- **实现要点**：
  1. 新增 raw_evidence auditor，输入候选或报告 metadata，输出：
     - `evidence_coverage`
     - `critical_missing_fields`
     - `stale_fields`
     - `unit_unknown_fields`
     - `fallback_fields`
     - `evidence_quality_level`
  2. 覆盖至少这些字段族：
     - OHLCV/成交额/换手率/量比。
     - 个股资金流/板块资金。
     - 龙虎榜/两融。
     - 公告/研报/政策事件。
     - 实时补丁 freshness。
  3. TradeFlow 候选层增加联动：
     - 关键字段缺失时不得进入 A 层。
     - `POLICY_AMBUSH` 允许技术未确认，但不允许政策/公司证据缺失。
     - `TECH_TRADE` 缺实时行情/量价证据时降级。
  4. 前端/API 暴露审计摘要，供候选详情和日报复用。
- **验收方式**：
  - 构造完整证据、缺资金、缺公告、stale 行情、单位未知五类 fixture。
  - 完整证据样本不误降级。
  - 缺关键证据样本候选等级和 `need_deep_ta` 被限制。
  - 夜间日报能展示 `evidence_coverage` 和关键缺口。
  - `pytest tests/test_data004_evidence_contract.py tests/test_tradeflow_*.py tests/test_data006_daily_digest.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-007] evidence_coverage_audit`

### DATA-008: A股关键源 fallback smoke fixtures 扩展（P1）
- **描述**：扩展 DATA-005 fixture replay，重点覆盖用户报告中经常缺失的 A 股行情、资金流、龙虎榜、公告和实时补丁 fallback，防止数据源“假可用”再次回归。
- **优先级**：P1
- **状态**：done -- commit 421389f
  - **前置条件**：`DATA-005` 完成 ✓。
- **执行约束**：
  - 默认只跑 fixture/mock，不做 live 压测。
  - 不保存 cookie、API key、token。
  - 不把第三方接口失败静默解释为“无数据”。
- **实现要点**：
  1. 增加 replay 场景：
     - AKShare 个股资金流失败但 fallback 成功。
     - 龙虎榜 `NORMAL_NO_DATA` 与 `FAILED` 区分。
     - 日线 stale 后实时 quote 补丁成功。
     - 公告源失败但事件源有弱证据。
     - 换手率/量比缺失导致完整度降级。
  2. replay 输出必须包含 `vendor/endpoint/status/fallback_vendor/error/as_of`。
  3. replay 报告聚合到 `docs/data_source_reports/`。
  4. 与 DATA-007 auditor 对接，确保 fixture 能触发覆盖率变化。
- **验收方式**：
  - `FAILED` 不被显示为 `NORMAL_NO_DATA`。
  - fallback 成功时显示实际 vendor。
  - stale 行情补丁显示 `is_realtime_patched=True`。
  - `pytest tests/test_data_source_replay.py tests/test_g007_fund_lhb_provenance.py tests/test_tradeflow_*.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-008] astock_fallback_replay`

### DATA-009: 自选备注与截图识别字段持久化回归保护（P1）
- **描述**：针对用户反馈“前端更新后备注全没了”，为自选备注、截图识别导入字段、昊天备注摘要建立持久化和回归测试，保证后续 UI/API 修改不会清空用户备注。
- **优先级**：P1
- **状态**：done -- commit 8216b22
- **前置条件**：`H-008`、`VLM-001` 完成 ✓。
- **执行约束**：
  - 不读取用户真实图片内容；测试使用 mock OCR/VLM 结果。
  - 不调用 live VLM/LLM。
  - 不覆盖用户已有备注。
  - 不修改生产 `tradingagents.db`。
- **实现要点**：
  1. 后端 watchlist schema/API 回归保护：
     - `notes`
     - `topic`
     - `benefit_score`
     - `consensus_score`
     - `expected_window`
     - `evidence_gap`
     - `watchlist_note_suggested`
  2. 前端“添加自选/图片识别”流程：
     - 识别结果写入建议备注。
     - 用户已有备注保留，建议备注只能追加或待确认。
     - 多图上传时按 symbol 合并，不重复覆盖。
  3. 增加测试覆盖：
     - 更新自选名称/排序不清空 notes。
     - 前端刷新后 notes 字段仍返回。
     - 0 分/空字符串/None 区分正确。
- **验收方式**：
  - 构造已备注自选，调用更新接口后 notes 不丢。
  - mock 图片识别结果能生成短备注：`半导体设备｜利好9.1｜共识89｜窗口1月｜缺口:订单/资金`。
  - `pytest tests/test_watchlist_*.py tests/test_vlm_position_parser.py tests/test_h008_mandate_watchlist_note.py -q` 或等价测试通过。
  - 前端构建通过。
- **代码标注要求**：`# [DATA-009] watchlist_notes_persistence` / `// [DATA-009] watchlist_notes_persistence`

### DATA-010: 融资融券数据源注册与 raw_evidence 接入（P1）
- **描述**：把融资融券数据纳入 source_catalog、provider 路由和 raw_evidence contract，补齐 TA 风控和情绪判断里的杠杆资金证据。
- **优先级**：P1
- **状态**：done -- commit b4b7313
- **前置条件**：`DATA-001`、`DATA-004` 完成 ✓。
- **背景**：
  - `DATA_SOURCE_GAP_AUDIT` 标记融资融券为高风险缺口。
  - 当前 TA 报告经常把融资融券显示为 `NOT_QUERIED` 或完全缺失，无法评估杠杆风险。
- **执行约束**：
  - 不调用 LLM。
  - 不写生产数据库。
  - 不跑全市场 live 请求；测试默认 mock/fixture。
  - 不把缺失融资融券数据推测成结论。
- **实现要点**：
  1. 在 `source_catalog` 中注册融资融券数据类型和可用 vendor/endpoint。
  2. provider 层提供最小接口，例如 `get_margin_trading(symbol, date_range?)`。
  3. route 层支持 fallback，并正确记录 `get_last_hit_vendor`。
  4. `raw_evidence` entry 包含 `vendor/endpoint/status/as_of/unit/error/fallback_from`。
  5. readiness/evidence coverage 能识别 `HAS_DATA/FAILED/NOT_QUERIED`，不能只靠文本存在判断。
- **验收方式**：
  - mock 成功样本：raw_evidence.margin_trading 为 `HAS_DATA`，包含融资余额/融券余额或等价字段。
  - mock 失败样本：状态为 `FAILED`，不被当成可用数据。
  - 缺失样本：状态为 `NOT_QUERIED` 或 `NORMAL_NO_DATA`，语义清晰。
  - `pytest tests/test_data_source_catalog.py tests/test_data004_evidence_contract.py tests/test_readiness_score.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-010] margin_trading_raw_evidence`

### DATA-011: 研报端点接入 route_to_vendor 与 raw_evidence（P1）
- **描述**：把券商研报/研究报告端点从“目录登记”推进到 provider route 和 raw_evidence，服务中线研究和估值 sanity check。
- **优先级**：P1
- **状态**：done — commit 1f86ad1
- **前置条件**：`DATA-004` 完成 ✓。
- **执行约束**：
  - 不抓取大批量研报正文；第一版只要标题、机构、日期、评级/目标价摘要、来源链接或 ID。
  - 不把研报观点当作交易建议。
  - 不调用 LLM。
  - 默认测试 mock/fixture。
- **实现要点**：
  1. provider 增加研报查询方法，例如 `get_research_reports(symbol, limit=...)`。
  2. route_to_vendor 支持研报方法和 fallback。
  3. raw_evidence 增加 `research_reports` entry。
  4. 报告/数据源摘要可展示“有研报/无研报/查询失败”，不要把空结果当失败。
  5. 若目标价与实时价偏离过大，只标注口径风险，不生成买卖建议。
- **验收方式**：
  - 有研报 fixture 能进入 raw_evidence，包含 vendor、endpoint、机构、日期。
  - 空列表显示 `NORMAL_NO_DATA` 或等价正常无数据状态。
  - 接口失败显示 `FAILED`，不污染估值结论。
  - `pytest tests/test_data004_evidence_contract.py tests/test_data_source_replay.py tests/test_g008_valuation_sanity.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-011] research_report_raw_evidence`

### DATA-012: 评级数据接入 provider 路由与 raw_evidence（P2）
- **描述**：把分析师评级数据从事件流扩展到 provider route 与 raw_evidence，让 TA 报告和 TradeFlow 候选能追溯评级变化。
- **优先级**：P2
- **状态**：done — commit 3a8351f + 4d9ae52（由 DATA-012A 收口完成）
- **前置条件**：`DATA-003`、`DATA-004` 完成 ✓。
- **执行约束**：
  - 不输出“因评级买入/卖出”的强动作。
  - 不调用 LLM。
  - 不跑全市场 live。
- **实现要点**：
  1. provider 增加评级查询方法，至少返回机构、评级、日期、变动方向、来源。
  2. route_to_vendor 和 source_catalog 增加 data type。
  3. raw_evidence 增加 `ratings` entry。
  4. TradeFlow/TA 只把评级作为事件证据，不直接提升到强结论。
- **验收方式**：
  - mock 评级上调/下调/无数据/失败四类 fixture。
  - raw_evidence status 区分 `HAS_DATA/NORMAL_NO_DATA/FAILED`。
  - `pytest tests/test_event_source.py tests/test_data004_evidence_contract.py tests/test_data_source_replay.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-012] rating_raw_evidence`

### DATA-012A: 评级数据 data_collector 接线与回归验收（P1）
- **描述**：DATA-012 的 provider 方法和 readiness 评分已落地（commit 6b9cb02），但 data_collector.py 未调用 get_ratings，评级数据不会进入 raw_evidence。本任务只补接线和回归验收。
- **优先级**：P1
- **状态**：done — commit 3a8351f + 4d9ae52
- **前置条件**：`DATA-012` 部分落地（provider/get_ratings + readiness_score + source_catalog 已就位）✓。
- **执行约束**：
  - 不重写 provider 方法，只接线。
  - 不调用 LLM。
  - 不跑全市场 live。
- **实现要点**：
  1. `data_collector.py` 增加 `get_ratings` 调用，结果写入 `raw_evidence["ratings"]`。
  2. raw_evidence ratings entry 包含 status（HAS_DATA/NORMAL_NO_DATA/FAILED）、vendor、data 字段。
  3. 跑 `tests/test_data012_ratings.py` 全过。
  4. 路径：`pytest tests/test_data012_ratings.py tests/test_data004_evidence_contract.py -q` 回归通过。
- **验收方式**：
  - `pytest tests/test_data012_ratings.py -q` 全过。
  - `pytest tests/test_data004_evidence_contract.py -q` 回归无破坏。
  - data_collector 收集后 `raw_evidence["ratings"]` 有结构化 status。
- **代码标注要求**：`# [DATA-012A] rating_collector_wiring`

### DATA-013: 回购数据接入 provider 路由与 raw_evidence（P2）
- **描述**：把回购计划/回购进展从事件流扩展到 provider route 与 raw_evidence，作为公司行为和中线信号证据。
- **优先级**：P2
- **状态**：done -- commit 9b05a2b
- **前置条件**：`DATA-003`、`DATA-004` 完成 ✓。
- **执行约束**：
  - 回购只作为事件证据，不直接生成买入结论。
  - 不调用 LLM。
  - 默认测试 mock/fixture。
- **实现要点**：
  1. provider 增加回购查询方法，返回公告日期、金额区间、进度、来源。
  2. route_to_vendor 和 source_catalog 增加 data type。
  3. raw_evidence 增加 `buybacks` entry。
  4. 与事件源归一化保持一致，避免同一回购重复计分。
- **验收方式**：
  - mock 回购计划/进展/无数据/失败四类 fixture。
  - 重复事件不会导致候选分数重复叠加。
  - `pytest tests/test_event_source.py tests/test_data004_evidence_contract.py tests/test_tradeflow_*.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-013] buyback_raw_evidence`

### DATA-014: 新闻/政策事件 fixture 与 live smoke 补充（P2）
- **描述**：补齐新闻/政策事件源的 fixture replay 和低频 live smoke，验证昊天雷达核心事件源在失败、空结果、限流时的状态语义。
- **优先级**：P2
- **状态**：done -- commit ed3fc04
- **前置条件**：`DATA-005`、`DATA-P1-ASTOCK-LIVE-SMOKE` 完成 ✓。
- **执行约束**：
  - live smoke 必须环境变量显式开启。
  - 不抓取大批量新闻。
  - 不调用 LLM。
  - 不把新闻热度当政策原文。
- **实现要点**：
  1. 增加新闻/政策事件 fixture：成功、有空结果、部分失败、全部失败、限流。
  2. live smoke 输出 vendor、endpoint、status、latency、count、error。
  3. 与 `DATA-006` 日报和 `DATA-007` evidence audit 对接。
  4. 报告中区分“政策原文”“新闻转述”“市场传闻”。
- **验收方式**：
  - mock 全部失败时 status=`FAILED`。
  - 成功但无事件时 status=`OK` 且 count=0。
  - live smoke 未开启时 skip，不阻断普通测试。
  - `pytest tests/test_event_source.py tests/test_data_source_replay.py tests/test_data006_daily_digest.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-014] policy_news_fixture_smoke`

### DATA-015: 涨停池 cn_astock fallback 与 fixture（P2）
- **描述**：为涨停池/涨停板情绪数据增加 cn_astock/Eastmoney fallback 和 fixture，降低 AKShare 单点失败对短线候选和市场情绪的影响。
- **优先级**：P2
- **状态**：done -- commit 4bb21cf
- **前置条件**：`DATA-005`、`DATA-P0-FUND-ROUTE` 完成 ✓。
- **执行约束**：
  - 不全市场高频轮询。
  - fallback 失败时透明标注，不推测涨停池。
  - 不输出买卖建议。
- **实现要点**：
  1. provider 层补充涨停池 fallback 方法或复用 Eastmoney 端点。
  2. source_catalog 增加 fallback 顺序。
  3. fixture 覆盖 AKShare 成功、AKShare 失败 fallback 成功、全部失败、空池。
  4. 数据源健康面板能显示最终 vendor。
- **验收方式**：
  - AKShare 失败时 fallback 成功显示 `vendor=cn_astock` 或等价实际 vendor。
  - 全部失败显示 `FAILED`，不能显示空池正常。
  - `pytest tests/test_data_source_replay.py tests/test_m008_health_check.py tests/test_tradeflow_*.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-015] limit_up_pool_fallback`

### DATA-016: 热门股票 cn_astock fallback 与 fixture（P2）
- **描述**：为热门股票/热搜/市场关注度数据增加 cn_astock/Eastmoney fallback 和 fixture，避免短线情绪源单点失效。
- **优先级**：P2
- **状态**：done -- commit ea85f4c
- **前置条件**：`DATA-005`、`DATA-P0-FUND-ROUTE` 完成 ✓。
- **执行约束**：
  - 不把热度当作政策证据。
  - 不全市场高频轮询。
  - 不输出买卖建议。
- **实现要点**：
  1. provider 层补充热门股票/热搜 fallback 方法。
  2. source_catalog 增加字段、freshness 和 fallback priority。
  3. fixture 覆盖成功、空结果、限流、fallback 成功、全部失败。
  4. TradeFlow 候选只把热度作为弱证据或排序辅助。
- **验收方式**：
  - 热门股票失败不会导致候选池整体失败。
  - 新闻/政策证据与热门热度证据不混用。
  - `pytest tests/test_data_source_replay.py tests/test_tradeflow_*.py tests/test_s006_false_positive_audit.py -q` 或等价测试通过。
- **代码标注要求**：`# [DATA-016] hot_stock_fallback`

---

## H. 昊天雷达 / 政策左侧埋伏任务池（2026-05-31 新增）

> 目标：把 TradeFlow 的主目标从“技术形态候选池”升级为“政策版本下的中线候选识别”。技术信号只作为执行层确认，不作为第一性筛选。所有结论必须绑定政策/事件/产业/公司证据，不允许凭空揣测。

### H-001: 昊天雷达 v0 数据模型与信号分类（P1）
- **描述**：新增政策意图识别的基础数据结构，把政策/事件/产业/公司证据统一归档为可评分的 `MandateSignal`，为后续“昊天意志评分”提供输入。
- **优先级**：P1
- **状态**：done -- commit 4daa738
- **背景**：
  - 当前 TradeFlow 已有 `POLICY_VERSION`，但还是关键词加分，不能表达政策级别、连续性、产业链位置和公司受益路径。
  - 昊天战法第一步不是看 K 线，而是识别“当前版本要扶什么方向、哪些公司可能成为标杆”。
- **执行约束**：
  - 不调用 LLM。
  - 不触发 TA。
  - 不输出买卖建议。
  - 不改 `tradingagents/prompts/`。
  - 不改生产 `tradingagents.db` schema；新增数据优先放在 TradeFlow 自有模块/SQLite 表或纯函数结构中。
- **实现要点**：
  1. 新增 `tradingagents/tradeflow/mandate_signal.py` 或同等模块。
  2. 定义 `MandateSignal` 字段：
     - `symbol/topic/title/source/source_level/date/evidence_text/evidence_url/event_type/direction/confidence`
     - `policy_tags/industry_tags/company_role/raw_refs`
  3. 定义来源级别枚举：
     - `CENTRAL`、`STATE_COUNCIL`、`MINISTRY`、`LOCAL_GOV`、`EXCHANGE`、`SOE_GROUP`、`COMPANY_NOTICE`、`MEDIA`
  4. 定义信号类型：
     - `POLICY_DOCUMENT`、`MEETING_SIGNAL`、`INDUSTRY_PLAN`、`SUBSIDY_SUPPORT`、`PROCUREMENT_ORDER`、`LICENSE_APPROVAL`、`M_AND_A_RESTRUCTURING`、`SOE_REFORM`、`BUYBACK_RATING`
  5. 从现有 event source / manual event overrides 转换出 `MandateSignal`，不能丢失原始 title/source/date。
  6. 所有无法识别来源级别的信号默认低权重，不得按高级别政策处理。
- **验收方式**：
  - 构造国务院/工信部/地方/公司公告四类事件，能生成不同 `source_level`。
  - 无来源、无日期、无原文标题的事件不得生成高置信信号。
  - 同一事件按 symbol 隔离，不串票。
  - `pytest tests/test_h001_mandate_signal.py -q` 或新增同等测试通过。
- **代码标注要求**：`# [H-001] mandate_signal_model`

### H-002: 政策连续性与级别权重评分（P1）
- **描述**：实现 `Mandate Score` 的第一层：识别某个政策/产业主题是否持续升温，以及其信号来自什么级别的来源。
- **优先级**：P1
- **状态**：done -- commit ed0ffce
- **前置条件**：`H-001` 完成 ✓。
- **执行约束**：
  - 规则评分，不调用 LLM。
  - 不做全网抓取；只消费已有 event source/manual events/公告标题。
  - 缺少原始证据不得给高分。
- **实现要点**：
  1. 新增 `mandate_score.py`，输出：
     - `mandate_score`
     - `policy_continuity_score`
     - `source_authority_score`
     - `topic_heat_delta`
     - `mandate_reasons`
     - `mandate_evidence_refs`
  2. 级别权重建议：
     - 中央/国务院 > 部委 > 交易所/国资体系 > 地方政府 > 公司公告 > 媒体。
  3. 连续性规则：
     - 同主题多日、多来源、多层级重复出现加分。
     - 单日孤立标题不进入高分。
     - 重复转载/同源重复不得无限叠加。
  4. 支持主题词表 v0：
     - 新质生产力、低空经济、机器人、算力、军工、半导体、国产替代、并购重组、国企改革、出海、中特估、AI 应用、数据要素。
  5. 输出“为什么是当前版本方向/为什么只是噪声”的可解释字段。
- **验收方式**：
  - 多来源同主题信号分数高于单一公司公告。
  - 同源重复标题不会无限加分。
  - 媒体标题无政策原文时不得高置信。
  - 结果包含 evidence refs。
- **代码标注要求**：`# [H-002] mandate_policy_continuity`

### H-003: 产业链受益路径与标杆候选映射（P1）
- **描述**：把政策主题映射到产业链环节和公司角色，区分真正受益、间接受益和蹭概念，让昊天候选池不是只靠题材关键词。
- **优先级**：P1
- **状态**：done -- commit 9284a27
- **前置条件**：`H-001` 完成 ✓。
- **执行约束**：
  - 第一版使用可维护静态映射 + 事件证据，不调用 LLM。
  - 不声称公司必然受益，只输出 `beneficiary_path` 与证据强弱。
  - 不改 prompts。
- **实现要点**：
  1. 新增 `industry_mandate_map.py`，维护主题到产业链环节的映射：
     - 例如低空经济：整机、空管、运营、材料、导航、基础设施。
     - 机器人：减速器、伺服、控制器、本体、传感器、应用场景。
     - 算力：芯片、服务器、液冷、IDC、光模块、电源。
  2. 输出公司角色：
     - `LEADER`、`CORE_SUPPLIER`、`INFRA_PROVIDER`、`APPLICATION_SCENE`、`PERIPHERAL`、`CONCEPT_ONLY`、`UNKNOWN`
  3. 从公司公告/事件标题/行业标签中提取受益路径证据。
  4. 没有公司层证据时，只能标记 `UNKNOWN/CONCEPT_ONLY`，不得直接进入高优先级。
  5. 为 Candidate 增加或复用字段：
     - `beneficiary_path`
     - `company_role`
     - `mandate_topic`
     - `mandate_evidence_refs`
- **验收方式**：
  - 构造低空经济核心供应商事件，输出明确 `beneficiary_path`。
  - 只有“涉足/布局/关注”等弱词时标记为 `CONCEPT_ONLY` 或低置信。
  - 无公司证据时不进入 A 层。
- **代码标注要求**：`# [H-003] mandate_beneficiary_map`

### H-004: 左侧埋伏评分与候选类型分流（P0）
- **描述**：引入昊天候选池的核心分类：把候选分为政策左侧埋伏、政策右侧确认、技术交易、伪政策题材、过热不追。
- **优先级**：P0
- **状态**：done -- commit 9daef3a
- **前置条件**：`H-002`、`H-003` 完成 ✓。
- **执行约束**：
  - 不输出买入/卖出强建议。
  - 不自动触发 TA。
  - 不把纯技术票混入政策左侧池。
  - 所有高分必须有证据 refs。
- **实现要点**：
  1. 新增候选类型字段：
     - `POLICY_AMBUSH`
     - `POLICY_CONFIRM`
     - `TECH_TRADE`
     - `EVENT_WATCH`
     - `PSEUDO_POLICY`
     - `OVERHEATED_AVOID`
  2. 新增左侧埋伏评分：
     - `ambush_score`
     - `mandate_score`
     - `beneficiary_score`
     - `pricing_gap_score`
     - `overheat_penalty`
  3. 左侧埋伏可以不要求突破，但必须满足：
     - 政策连续性足够。
     - 公司受益路径不为空。
     - 风险标签不过重。
     - 未明显过热。
  4. 右侧确认由技术/资金确认辅助，不再作为主筛选第一性。
  5. `need_deep_ta` 分流：
     - `POLICY_AMBUSH`：优先进入“中线研究/政策验证”队列。
     - `POLICY_CONFIRM`：可进入 TA 深度分析队列。
     - `TECH_TRADE`：标记为短线/做 T，不混入主池。
- **验收方式**：
  - 政策强、公司路径明确、技术未突破的样本进入 `POLICY_AMBUSH`。
  - 纯 VCP 无政策证据的样本只能是 `TECH_TRADE` 或 B/C 观察。
  - 过热/高位/风险拥挤样本标记 `OVERHEATED_AVOID` 或降级。
  - Daily Plan 与 candidates API 返回候选类型和评分。
- **代码标注要求**：`# [H-004] mandate_ambush_score`

### H-005: TradeFlow 前端昊天候选池视图（P1）
- **描述**：在 TradeFlow 前端增加“昊天雷达/政策左侧”视图，把政策主题、受益路径、候选类型、证据和下一步验证条件展示出来。
- **优先级**：P1
- **状态**：done -- commit 7e2cb39
- **前置条件**：`H-004` 完成 ✓。
- **执行约束**：
  - 不做花哨营销页，保持工作台信息密度。
  - 不出现强买卖词。
  - 不隐藏证据缺口。
- **实现要点**：
  1. 候选池增加模式筛选：
     - 全部、昊天左侧、政策确认、技术交易、事件观察、过热规避。
  2. 表格新增列：
     - `candidate_type`
     - `mandate_score`
     - `ambush_score`
     - `mandate_topic`
     - `company_role`
     - `beneficiary_path`
  3. 详情抽屉新增“政策逻辑”区：
     - 政策来源级别
     - 连续性证据
     - 公司受益路径
     - 缺什么验证
     - 为什么不是单纯技术交易
  4. 空状态说明：如果无昊天候选，显示是政策源不足、公司路径不足、还是都已过热。
- **验收方式**：
  - mock 三类候选分别正确展示。
  - 长文本不撑破表格。
  - `npm run build` 通过。
- **代码标注要求**：`// [H-005] mandate_radar_ui`

### H-006: 昊天候选池回放评估与反证机制（P1）
- **描述**：建立昊天候选池的回放验证机制，避免系统越做越玄。每个高分政策候选都要能被后续走势、公告兑现、政策延续或反证记录检验。
- **优先级**：P1
- **状态**：done -- commit 6424a5b
- **前置条件**：`H-004` 完成 ✓。
- **执行约束**：
  - 不做收益承诺。
  - 不写生产历史回测结果目录 `eval_results/`。
  - 不调用 LLM。
- **实现要点**：
  1. 新增回放脚本或服务：
     - 输入某日昊天候选池。
     - 观察 5/10/20/60 日后表现。
     - 记录是否出现政策二次确认、公告兑现、趋势确认、风险反证。
  2. 指标：
     - 后续最大涨幅/最大回撤。
     - 是否跑赢行业/指数。
     - 政策信号是否延续。
     - 公司受益是否兑现。
  3. 输出反证原因：
     - 政策消退、公司路径伪、过热回撤、基本面雷、资金不认。
  4. 形成 fixtures，持续修正 `H-002/H-003/H-004` 权重。
- **验收方式**：
  - 用固定 fixtures 回放，输出稳定指标。
  - 0% 显示为 `0.0%`，无数据显示为 `N/A`。
  - 不修改 `eval_results/`。
- **代码标注要求**：`# [H-006] mandate_replay_eval`

### H-007: 昊天候选到 TA 中线研究队列分流（P1）
- **描述**：把 H-004 产生的 `POLICY_AMBUSH/POLICY_CONFIRM/TECH_TRADE` 候选分流到不同研究队列，避免左侧中线候选被短线 TA 逻辑误判，也避免纯技术票污染昊天池。
- **优先级**：P1
- **状态**：done -- commit 3eca229
- **前置条件**：`H-004` 完成 ✓。
- **执行约束**：
  - 不自动调用高成本 LLM。
  - 不自动触发 TA 深度分析；只生成队列和理由。
  - 不输出强买卖词。
- **实现要点**：
  1. 新增或扩展队列字段：
     - `research_queue`: `MIDLINE_POLICY` / `TA_CONFIRM` / `SHORT_TERM_TRADE` / `WATCH_ONLY` / `REJECTED`
     - `research_intent`: `policy_validation` / `trend_confirmation` / `risk_review`
  2. 分流规则：
     - `POLICY_AMBUSH` → `MIDLINE_POLICY`
     - `POLICY_CONFIRM` → `TA_CONFIRM`
     - `TECH_TRADE` → `SHORT_TERM_TRADE`
     - `OVERHEATED_AVOID/PSEUDO_POLICY` → `WATCH_ONLY` 或 `REJECTED`
  3. API/日报输出队列统计和原因。
  4. 与 G-001 的 `analysis_intent/position_context` 不冲突。
- **验收方式**：
  - 构造四类候选，队列分流稳定。
  - 未持仓/已持仓上下文不被覆盖。
  - 无强交易词。
- **代码标注要求**：`# [H-007] mandate_ta_queue_router`

### H-008: 昊天主题观察清单与自选备注摘要（P1）
- **描述**：把昊天候选的政策主题、受益路径、利好度/共识度/证据缺口压缩成短备注，供自选股和前端观察清单展示，解决用户手工备注易丢失、信息太散的问题。
- **优先级**：P1
- **状态**：done -- commit 8dbea20
- **前置条件**：`H-004`、`VLM-001` 完成 ✓。
- **执行约束**：
  - 不覆盖用户已有备注；只能追加或生成建议备注。
  - 不输出强买卖词。
  - 不把无证据题材写成确定利好。
- **实现要点**：
  1. 新增摘要生成 helper，例如：
     - `topic`
     - `benefit_score`
     - `consensus_score`
     - `expected_window`
     - `evidence_gap`
     - `note_summary`
  2. 输出格式短而稳定，例如：`半导体设备｜利好9.1｜共识89｜窗口1月｜缺口:订单/资金`
  3. 自选添加/截图解析/TradeFlow 候选详情可复用该摘要。
  4. 已有 notes 不被前端刷新清空。
- **验收方式**：
  - 用户已有 notes 保留。
  - 长字段被压缩，不撑破表格。
  - 无证据时显示 `缺证据`，不伪造利好度。
- **代码标注要求**：`# [H-008] mandate_watchlist_note`

### H-009: 昊天候选反证/过热降权校准（P1）
- **描述**：把 H-006 回放中的反证信号沉淀为评分校准规则，重点降低“抄在半山腰”的候选：过热、政策弱兑现、公司路径伪、资金不认、风险事件未消化。
- **优先级**：P1
- **状态**：done — commit bd17235
- **前置条件**：`H-006`、`V-004` 完成 ✓。
- **执行约束**：
  - 不调用 LLM。
  - 不输出买卖建议。
  - 不把短期回撤简单等同于逻辑失败。
  - 不修改 prompts。
- **实现要点**：
  1. 新增或扩展反证规则：
     - `overheated_price_position`
     - `policy_signal_decay`
     - `weak_company_benefit_path`
     - `funding_not_confirmed`
     - `negative_event_unresolved`
     - `crowded_consensus_risk`
  2. 对 `POLICY_AMBUSH` 加入降权约束：
     - 高位连续放量但政策/公司证据不新增 → 降级。
     - 仅媒体热度、无原始政策/公告 → 不得高分。
     - 公司路径为 `CONCEPT_ONLY/UNKNOWN` → 不得进入 A 层。
  3. 输出可解释字段：
     - `counter_evidence`
     - `overheat_flags`
     - `downgrade_reasons`
     - `what_would_change_mind`
  4. 反证字段进入 API/日报，前端可展示。
- **验收方式**：
  - 过热样本不得进入 `POLICY_AMBUSH` A 层。
  - 政策强但公司路径弱的样本降级到观察。
  - 明确公司受益且未过热的样本不被误杀。
  - `pytest tests/test_h006_mandate_replay_eval.py tests/test_h004_ambush_score.py tests/test_v004_mandate_e2e_smoke.py tests/test_h009_counter_evidence_calibration.py -q` 或等价测试通过。
- **代码标注要求**：`# [H-009] mandate_counter_evidence_calibration`

### H-010: 政策主题生命周期与版本状态注册表（P1）
- **描述**：建立“政策主题生命周期”注册表，区分新版本、升温、兑现、拥挤、退潮，帮助昊天雷达判断当前主题适合左侧埋伏、右侧确认还是只观察。
- **优先级**：P1
- **状态**：done（2026-06-07，commit 850205e）
- **前置条件**：`H-002`、`H-006` 完成 ✓。
- **执行约束**：
  - 第一版规则化，不调用 LLM。
  - 不声称主题一定上涨。
  - 不自动调 TA。
- **实现要点**：
  1. 新增主题状态枚举：
     - `EMERGING`
     - `ACCELERATING`
     - `CONFIRMING`
     - `CROWDED`
     - `FADING`
     - `UNKNOWN`
  2. 基于政策连续性、事件密度、候选拥挤度、价格热度、回放结果更新主题状态。
  3. Candidate 输出：
     - `topic_lifecycle_state`
     - `topic_lifecycle_reason`
     - `topic_last_signal_date`
     - `topic_signal_count`
  4. 左侧埋伏优先：
     - `EMERGING/ACCELERATING` 且公司路径明确。
     - `CROWDED/FADING` 降级或只观察。
- **验收方式**：
  - 单一孤立事件为 `UNKNOWN/EMERGING`，不得直接高置信。
  - 多日多源政策信号进入 `ACCELERATING/CONFIRMING`。
  - 过热且信号不延续进入 `CROWDED/FADING`。
  - `pytest tests/test_h002_mandate_score.py tests/test_h006_mandate_replay*.py tests/test_tradeflow_*.py -q` 或等价测试通过。
- **代码标注要求**：`# [H-010] mandate_topic_lifecycle`

### H-011: 候选矛盾证据与负面清单解释（P2）
- **描述**：为每个昊天候选输出"为什么可能错"的负面清单，让用户看到政策逻辑、公司受益、资金、技术和风险之间的矛盾，而不是只看正面叙事。
- **优先级**：P2
- **状态**：done（2026-06-07，commit 1e4b3ef）
- **前置条件**：`H-009` 完成或已有反证字段。
- **执行约束**：
  - 不调用 LLM。
  - 不输出强动作词。
  - 不把未知项写成确定利空。
- **实现要点**：
  1. 新增 `candidate_contradictions` 聚合器：
     - 政策强但公司路径弱。
     - 主题热但资金不认。
     - 基本面差但题材强。
     - 技术破位但中线逻辑未破。
     - 数据完整度低但分数高。
  2. 输出字段：
     - `contradiction_level`
     - `contradiction_items`
     - `blocking_evidence_gaps`
     - `next_verification_steps`
  3. 前端/日报使用同一字段展示，避免重复拼文案。
- **验收方式**：
  - 构造 4 类矛盾样本，均能输出不同解释。
  - 无矛盾样本显示空数组，不报错。
  - `pytest tests/test_tradeflow_*.py tests/test_h009_*.py -q` 或等价测试通过。
- **代码标注要求**：`# [H-011] candidate_contradiction_explainer`

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

### M-012: 任务池空转时自动生成 proposed 任务草案（P1）
- **描述**：当 `docs/TASKS.md` 没有 ready 任务时，自动开发链不应只空转退出，而应生成一批 `proposed` 任务草案和原因，等待 Codex/用户审核后释放为 ready。
- **优先级**：P1
- **状态**：done（2026-06-07，commit b256707）
- **前置条件**：`V-002` 完成 ✓。
- **执行约束**：
  - 不自动把 proposed 改成 ready。
  - 不调用高成本模型；第一版基于 ROADMAP/TASKS/DEVLOG 静态分析。
  - 不修改业务代码。
- **实现要点**：
  1. 新增或扩展脚本，例如 `scripts/suggest_next_tasks.py`。
  2. 输入：
     - `docs/ROADMAP.md`
     - `docs/TASKS.md`
     - `docs/DEVLOG.md`
     - 最近 `docs/task_runs/`
  3. 输出：
     - `docs/task_suggestions/YYYY-MM-DD.md`
     - 每个建议包含 priority、依赖、原因、验收方式、风险。
  4. `scripts/auto_dev_loop.sh` 在无 ready 时提示建议报告路径。
  5. OpenClaw 可读取建议报告后再派 Codex/用户确认。
- **验收方式**：
  - 无 ready 任务时生成 proposed 报告。
  - 有 ready 任务时不生成或只提示无需建议。
  - 报告不直接改 TASKS 状态。
  - `bash -n scripts/auto_dev_loop.sh` 通过；新增脚本测试或 dry-run 通过。
- **代码标注要求**：`# [M-012] task_pool_suggestion`

### M-013: CodeGraph 影响范围预检接入自动开发日志（P2）
- **描述**：把 CodeGraph 的 symbol/context/impact 输出接入每个任务的运行档案，帮助 OpenCode 开发前知道影响范围，也方便 Codex review 时检查调用方覆盖。
- **优先级**：P2
- **状态**：done（2026-06-07，commit 589e373）
- **前置条件**：`INF-001` 完成 ✓；CodeGraph 已本地安装或可优雅跳过。
- **执行约束**：
  - CodeGraph 不可用时不能阻塞任务执行，只记录 `SKIPPED`。
  - 不提交 `.codegraph/` 索引。
  - 不调用外部网络。
- **实现要点**：
  1. 自动开发领取任务后，在 `docs/task_runs/<TASK>/` 写入：
     - `codegraph-context.txt`
     - `codegraph-impact.txt`
     - `codegraph-status.json`
  2. 根据任务描述关键词尝试生成相关 symbol context。
  3. 任务完成后，对变更文件跑 impact 摘要，写入 run archive。
  4. DEVLOG 中记录 CodeGraph 是否成功、影响范围是否异常大。
- **验收方式**：
  - CodeGraph 可用时 run archive 有 context/impact 文件。
  - CodeGraph 不可用时任务仍可继续。
  - `.codegraph/` 不进入 git。
  - `bash -n scripts/auto_dev_loop.sh` 通过。
- **代码标注要求**：`# [M-013] codegraph_auto_dev_preflight`

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

### UI-009: 候选详情一键生成 TA 研究任务预案（P1）
- **描述**：在 TradeFlow 候选详情中提供“生成 TA 研究预案/加入研究队列”的轻量入口，只生成队列记录和分析参数预案，不直接触发高成本 TA/LLM。
- **优先级**：P1
- **状态**：done（2026-06-07，commit 5523480）
- **前置条件**：`H-007`、`UI-006` 完成 ✓。
- **执行约束**：
  - 不自动启动 TA 深度分析。
  - 不调用 LLM。
  - 不输出强买卖词。
  - 不覆盖用户已有任务。
- **实现要点**：
  1. 后端新增/复用队列预案接口：
     - 输入 candidate id/symbol/date。
     - 输出 `research_queue`、`analysis_intent`、`horizon`、`position_context`、`required_evidence`、`route_reason`。
  2. 前端详情抽屉增加按钮：
     - `生成中线研究预案`
     - `生成短线确认预案`
     - 根据 candidate type 自动禁用不适合的选项。
  3. 页面显示：
     - 预案已生成/已存在/缺证据不能生成。
     - 后续人工确认后才真正跑 TA。
  4. 与 G-001 中短线路由保持字段一致。
- **验收方式**：
  - `POLICY_AMBUSH` 默认生成中线政策验证预案。
  - `TECH_TRADE` 默认生成短线确认预案。
  - 缺关键 evidence 时显示不能生成原因。
  - 刷新页面后预案状态可见。
  - `pytest tests/test_h007_ta_queue_router.py tests/test_ui001_tradeflow_api.py -q` 通过；`npm run build` 通过。
- **代码标注要求**：`# [UI-009] candidate_ta_plan_draft` / `// [UI-009] candidate_ta_plan_draft`

### UI-010: 昊天候选对比视图与证据缺口排序（P2）
- **描述**：在 TradeFlow 前端增加候选对比视图，按政策主题、受益路径、证据覆盖率、反证风险和下一步验证条件排序，帮助用户从少量昊天候选中挑重点研究对象。
- **优先级**：P2
- **状态**：done（2026-06-08，commit c21b8b0 + 056a072）
- **前置条件**：`H-005`、`DATA-007` 完成或具备等价字段。
- **执行约束**：
  - 不做营销页。
  - 不输出强买卖词。
  - 不隐藏证据缺口。
- **实现要点**：
  1. 新增候选对比 Tab 或表格模式：
     - `mandate_score`
     - `ambush_score`
     - `evidence_coverage`
     - `company_role`
     - `topic_lifecycle_state`
     - `counter_evidence`
     - `next_verification_steps`
  2. 支持排序：
     - 政策强度。
     - 证据覆盖率。
     - 反证风险。
     - 验证缺口最少。
  3. 长文本压缩为短标签，详情抽屉展示完整解释。
  4. 空字段显示 `--/未验证/缺证据`，不显示空白。
- **验收方式**：
  - mock 三只候选，排序和字段显示稳定。
  - 长文本不撑破表格。
  - `npm run build` 通过。
- **代码标注要求**：`// [UI-010] mandate_candidate_compare`

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
- **状态**：blocked — NEEDS_HUMAN, see docs/task_runs/V-001-20260608-022250
- **前置条件**：`G-009`、`G-010`、`N-002`、`N-003` 完成 ✓。
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

### V-004: 昊天链路端到端 smoke 验收（P1）
- **描述**：为 H-001~H-008 建立低成本 smoke 验收，验证政策事件 → MandateSignal → Mandate Score → 受益路径 → 左侧分类 → 前端/队列字段的整条链路不字段断裂。
- **优先级**：P1
- **状态**：done -- commit fd6e1c3
- **前置条件**：`H-004`、`DATA-003`、`DATA-004` 完成 ✓。
- **执行约束**：
  - 不调用外部 LLM。
  - 不跑全市场扫描。
  - 不写生产数据库。
  - 不输出强买卖词。
- **实现要点**：
  1. 构造固定 policy fixtures：
     - 中央/部委政策连续信号。
     - 公司公告受益路径明确。
     - 研报/媒体弱证据。
     - 过热/伪政策反例。
  2. 串联现有 H/DATA 模块输出：
     - `MandateSignal`
     - `mandate_score`
     - `beneficiary_path`
     - `candidate_type`
     - `research_queue`
     - `note_summary`
  3. 输出 smoke 报告到 `docs/mandate_acceptance/YYYY-MM-DD.md` 或测试 snapshot。
  4. 报告必须列出字段缺口和无法判断原因。
- **验收方式**：
  - fixtures 输出稳定。
  - 弱证据不进入高分左侧。
  - 过热样本不进入 `POLICY_AMBUSH`。
  - `pytest tests/test_h*_*.py tests/test_data003_mandate_event_normalizer.py tests/test_data004_evidence_contract.py -q` 或等价测试通过。
- **代码标注要求**：`# [V-004] mandate_e2e_smoke`

### V-005: 夜间昊天候选质量日报与样本回放（P1）
- **描述**：把 DATA-006、H-006、V-004 的结果合并成夜间昊天候选质量日报，第二天可以直接看到候选池有没有选对方向、哪些是数据问题、哪些是策略问题。
- **优先级**：P1
- **状态**：done（2026-06-07，commit 3997254）
- **前置条件**：`V-004`、`DATA-006` 完成 ✓。
- **执行约束**：
  - 不调用外部 LLM。
  - 不跑全市场扫描。
  - 不写生产数据库。
  - 不输出强买卖词。
- **实现要点**：
  1. 生成 `docs/mandate_reports/YYYY-MM-DD.md` 或并入现有 auto dev report。
  2. 报告包含：
     - 昊天候选数量、类型分布、主题分布。
     - A/B/C 或候选等级分布。
     - 数据覆盖率摘要。
     - 反证/过热/证据缺口 TopN。
     - 后续需人工研究的候选。
  3. 当候选为空时，区分：
     - 无事件源。
     - 事件源失败。
     - 全部被过滤。
     - 策略门槛过严。
  4. 与 `docs/task_runs/` 关联，便于回查每晚自动开发和候选质量。
- **验收方式**：
  - 构造有候选、无候选、数据源失败三类 fixture，报告输出不同原因。
  - 报告不包含 API key/token/敏感路径。
  - `pytest tests/test_v004_mandate_e2e_smoke.py tests/test_data006_daily_digest.py tests/test_v005_mandate_quality_report.py -q` 或等价测试通过。
- **代码标注要求**：`# [V-005] nightly_mandate_quality_report`

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

### DECISION-001: 最终动作语义分层 — 禁止默认 HOLD（P0）
- **描述**：当前 HOLD 被过度复用（已持仓持有、未持仓观望、偏多等待、数据不足、风控降级全压成 HOLD）。拆成 3 层语义：`research_direction`（看多/偏多/中性/偏空/看空）、`execution_action`（WAIT/ENTER/HOLD/REDUCE/EXIT）、`action_label`（回避/等待触发/条件入场/持有/减仓/清仓）。禁止默认 HOLD。
- **优先级**：P0
- **状态**：done — commit dee415c
- **前置条件**：`N-005` 完成（已有 WAIT/ENTER/HOLD/REDUCE/EXIT 枚举和 execution_schema）✓。
- **执行约束**：
  - 不改 prompt 作为唯一方案，必须在 signal_processing.py 和 report_service.py 做规则层改造。
  - 保持向后兼容：旧报告的 `decision` 字段仍可读，新字段并行输出。
  - 不调用 LLM。
- **实现要点**：
  1. `signal_processing.py`：`process_signal` 返回值从单一 `decision` 扩展为 3 层结构。`_extract_decision_keyword` 重构为 `_extract_decision_semantics`，分别提取方向、动作、标签。
  2. `report_service.py`：`StructuredReport` 新增 `research_direction`、`execution_action`、`action_label` 字段，`decision` 保留但不再作为唯一展示源。
  3. `signal_processing.py`：`_execution_layer_overrides_hold` 改为返回结构化动作（WAIT/ENTER/HOLD/REDUCE/EXIT），不再一律返回 HOLD。
  4. `direction_map` 扩展：偏多→WAIT（未持仓）或 ENTER（有触发价），中性→HOLD（已持仓）或 WAIT（未持仓），偏空→WAIT（未持仓）。
  5. 新增 `_derive_action_label(has_position, research_direction, execution_action, trigger_price, invalid_price)` 函数生成中文标签。
- **验收方式**：
  - 新增测试覆盖 6 种场景（未持仓偏多等待触发、未持仓强偏多条件入场、未持仓偏空回避、已持仓中性持有、已持仓风险升高减仓、数据不足）。
  - `pytest tests/test_signal_processing.py -q` 通过。
  - `pytest tests/test_report_service.py -q` 通过（如有）。
  - 旧报告 `decision` 字段不受影响。
- **代码标注要求**：`# [DECISION-001] action_semantics_layering`

### DECISION-002: 历史报告回放测试 — 覆盖典型场景（P1）
- **描述**：用历史报告样本（002709.SZ、300750.SZ、603256.SH 等）做回放测试，验证 DECISION-001 的语义分层在真实报告文本上正确工作。
- **优先级**：P1
- **状态**：done
- **前置条件**：`DECISION-001` 完成。
- **执行约束**：
  - 不修改历史报告内容。
  - 不调用 LLM。
- **实现要点**：
  1. 提取 002709.SZ、300750.SZ、603256.SH 的历史报告文本。
  2. 用 `process_signal` 重新解析，验证 3 层输出与预期一致。
  3. 新增 `tests/test_decision_replay.py` 覆盖至少 3 个样本。
- **验收方式**：
  - `pytest tests/test_decision_replay.py -q` 通过。
  - 每个样本的 `research_direction`、`execution_action`、`action_label` 与预期一致。
- **代码标注要求**：`# [DECISION-002] decision_replay_test`

### DECISION-003: 前端展示 3 层语义（P1）
- **描述**：前端报告页面和分析结果卡片展示 `research_direction + execution_action + action_label`，不再只取 `decision` 字段。
- **优先级**：P1
- **状态**：done — commit 61a09a7
- **前置条件**：`DECISION-001` 完成。
- **执行约束**：
  - 保持旧报告兼容：如果新字段不存在，fallback 到 `decision`。
  - 不改后端 API schema（新字段已经在 StructuredReport 中）。
- **实现要点**：
  1. `frontend/src/pages/Analysis.tsx`：报告卡片展示 `action_label`（大字）+ `research_direction`（辅助）。
  2. 候选池/观察池列表中，动作标签用颜色区分（红=减仓/清仓，黄=等待触发，绿=条件入场/持有，灰=回避）。
  3. 推送通知/日报中使用 `action_label` 而非 `decision`。
- **验收方式**：
  - 前端 build 无错误。
  - 报告卡片展示 3 层语义。
  - 旧报告 fallback 到 `decision` 不报错。
- **代码标注要求**：`# [DECISION-003] frontend_action_label`

### DECISION-004: 报告卡片和推送通知不再只取 decision（P2）
- **描述**：报告导出、飞书推送、日报汇总中，使用 `action_label` 替代 `decision` 作为用户可见的动作描述。
- **优先级**：P2
- **状态**：done — commit f73f4d5，依赖 DECISION-001 ✓
- **前置条件**：`DECISION-001` 完成。
- **执行约束**：
  - 不改飞书 API 调用方式，只改推送内容字段。
  - 向后兼容：旧报告 fallback 到 `decision`。
- **实现要点**：
  1. 报告导出模块使用 `action_label`。
  2. 飞书/通知推送使用 `action_label`。
  3. 夜间日报汇总使用 `action_label`。
- **验收方式**：
  - 导出报告包含 `action_label`。
  - 推送通知内容与报告一致。
- **代码标注要求**：`# [DECISION-004] notification_action_label`

## 2026-06-09 系统任务框架：从选股雷达到执行闭环

> 目标：后续任务不再按零散想法排队，而是按“数据真实性 → 候选发现 → 候选收敛 → 盘中观察 → 盘后复盘 → TA 分流 → 动作语义 → 自动验收”逐层推进。每层必须能独立验收，且上游坏了不继续堆下游功能。

| 层级 | 模块 | 当前目标 | 代表任务 |
|------|------|----------|----------|
| L0 | 数据源与证据契约 | 行情、资金、龙虎榜、公告、评级、研报必须能标注 HAS_DATA/NORMAL_NO_DATA/FAILED/STALE/UNIT_UNVERIFIED | DATA-017、DATA-COVERAGE-001 |
| L1 | 候选发现 Discovery | 同时支持短线技术池与昊天左侧池，候选入池必须有策略标签和证据字段 | TF-P0、H 系列、T-002 |
| L2 | 候选收敛与排序 | 默认只给少量主候选，观察/过滤候选保留原因，分数要能拉开差距 | TF-QUALITY-001A、TF-QUALITY-002 |
| L3 | 盘中 Observe | 自动读取最近有效计划，低频观察触发/等待/失效，不调 LLM | TF-OBS-002、T-004 |
| L4 | 盘后 Review | 支持非交易日计划映射到下一交易日，复盘命中/误报/无数据 | TF-REVIEW-002、T-005 |
| L5 | 轻量 TA / 完整 TA 分流 | 候选详情能看公司/K线/证据，并生成轻量 TA 预案；完整 TA 需用户确认 | TF-UI-011、PERF-002 |
| L6 | 最终动作语义 | 不再一片 HOLD，区分 research_direction / execution_action / action_label | DECISION-001~004、V-006 |
| L7 | 自动开发治理 | 任务领取、日志、CodeGraph 预检、Codex review、失败即停、日报可回查 | AUTO/M 系列、CODEGRAPH-002 |

## 2026-06-09 释放任务：TradeFlow 可试用收敛线

> 背景：用户试用反馈候选池过多、打分区分不明显、盘中观察需手动点击且红绿视觉反直觉、盘后 Review 无数据、候选详情缺少一键轻量 TA/K 线/公司概览。目标是让 TradeFlow 从“能生成一堆票”升级为“能给 5000 元小仓位试错时真正可用的少量目标”。

### TF-QUALITY-001: TradeFlow 候选池严格收敛门禁（P0）
- **描述**：收窄候选池，默认只输出少量高质量候选，弱信号进入过滤/观察原因，不进入主候选池。
- **优先级**：P0
- **状态**：done — commit 5f304db；NEEDS_HUMAN 已由 TF-QUALITY-001A 与 DATA-COVERAGE-001 拆分闭环
- **前置条件**：TF-P0-002、T-008、H-010 完成 ✓。
- **执行约束**：
  - 不改 prompts。
  - 不调用 live LLM。
  - 不写生产数据库；测试使用 fixture/临时库。
  - 不删除现有策略，只增加收敛门禁和过滤原因。
- **实现要点**：
  1. 新增候选池上限配置：默认主候选 Top 5，技术做 T 池与昊天左侧池分别限额。
  2. 主候选必须满足“至少两个维度共振”或“单一强信号 + 数据完整度达标”：
     - 技术池：形态/量能/资金/关键价位至少两项有效。
     - 昊天池：政策主题/产业链受益/事件证据/反证风险至少两项有效。
  3. 低分、单一弱信号、数据不完整候选进入 `filtered_candidates`，保留过滤原因。
  4. 页面/接口区分：主候选、观察候选、过滤原因，不再把所有票堆在同一个池里。
- **验收方式**：
  - fixture 20 只股票时，主候选默认不超过 5 只。
  - 单一弱 VCP/弱事件不得进入主候选。
  - 被过滤股票必须有明确原因：低分/单信号/数据缺口/流动性不足。
  - TradeFlow 前端候选池展示数量与后端主候选一致。
- **代码标注要求**：`# [TF-QUALITY-001] candidate_pool_gate` / `// [TF-QUALITY-001] candidate_pool_gate`
- **收口说明**：
  - 原 task_run 的失败由两类问题构成：TradeFlow 三池契约回归、DATA 覆盖率分母回归。
  - TradeFlow 回归已由 `TF-QUALITY-001A` 闭环。
  - DATA 覆盖率回归已由 `DATA-COVERAGE-001` 闭环。
  - 因此原任务不再需要保持 blocked，避免 M-012 反复生成错误建议。

### TF-QUALITY-001A: 收敛门禁回归修复与状态一致性（P0）
- **描述**：审核 TF-QUALITY-001（commit 5f304db）后发现全量回归中存在真实 TradeFlow 断裂：候选主池收敛后，旧候选接口、盘中 Observe、E2E smoke 对 data_gap/observation/filtered 的契约不一致。先修这个补丁，再继续评分和前端任务。
- **优先级**：P0
- **状态**：done — 后续任务已闭环
- **前置条件**：TF-QUALITY-001 已提交但 blocked；必须读取 `docs/task_runs/TF-QUALITY-001-20260609-202554/summary.md` 与 `tests-round2.txt`。
- **执行约束**：
  - 不改 prompts。
  - 不调用 live LLM。
  - 不写生产数据库；测试使用 fixture/临时库。
  - 不简单改测试绕过，必须恢复 TradeFlow API/Observe 的业务契约。
- **问题清单**：
  1. `get_candidates()` / `get_candidates_tiered()` 只返回主候选后，`pool=all`、`UNCLASSIFIED_DATA_GAP`、data gap 视图回归失败。
  2. T-004 盘中 Observe fixture 变成“无活跃候选”，说明 Observe 读取候选的范围与新三池契约不一致。
  3. gate-filtered 候选只进 discovery metadata，未稳定落库/追踪过滤原因。
  4. discovery 保存候选时仍可能保存原始 `top_candidates`，而非带 `pool_status` 的主候选/观察候选，导致 DB/API 二次门禁结果不一致。
  5. task_run 中 CodeGraph 预检因命令参数失败，不应影响本任务，但需要记录给 CODEGRAPH-002。
- **Codex Review P1 必修项**：
  1. `api/services/tradeflow_service.py`：必须保留 `pool=all` 语义，不得把 legacy `candidates` 永远替换成 `main_candidates`。
  2. `tradingagents/tradeflow/discovery.py`：必须落库 gated pools，而不是落库 pre-gate `top_candidates` 后再让 DB/API 二次门禁。
- **实现要点**：
  1. 明确 API 契约：默认候选表展示 `main_candidates`；`pool=all` 必须能返回主候选 + 观察候选 + data gap/过滤候选，且保留 `pool_status`。
  2. Observe 读取“可观察候选”时应包含主候选和 observation_candidates；被过滤候选不得触发 Observe。
  3. `save_candidates` 路径要写入或可重建 `pool_status`、`pool_filter_reason`、`pool_gate_summary`，避免 DB/API 前后不一致。
  4. discovery audit/summary 中要同时统计主候选、观察候选、过滤候选，不再只看原始 top_candidates。
  5. gate-filtered 候选要有可回查的过滤原因，至少在 API metadata 和 task_run 中一致。
- **验收方式**：
  - `.venv/bin/python -m pytest tests/test_tf_quality001_pool_gate.py tests/test_t002_discovery.py -q`
  - `.venv/bin/python -m pytest tests/test_t004_intraday_observe.py tests/test_tf_p0_003_e2e_smoke.py -q`
  - 不要求本任务修复 DATA 覆盖率老失败，但必须在 summary 中列出剩余 data coverage failures。
  - Codex review 无 P0/P1 findings。
- **代码标注要求**：`# [TF-QUALITY-001A] pool_gate_contract`

### TF-QUALITY-002: TradeFlow 评分拉开差距与排序解释（P0）
- **描述**：修复候选分数区分不明显的问题，让同一批股票能看出 A/B/C 层差距、为什么排前、为什么不够强。
- **优先级**：P0
- **状态**：done
- **完成时间**：2026-06-13
- **Commit**：待确认
- **前置条件**：TF-P0-002、T-008 完成 ✓；TF-QUALITY-001A 完成。
- **执行约束**：
  - 不改 prompts。
  - 不调用 live LLM。
  - 不用单一总分掩盖数据缺口，必须输出分项分。
- **实现要点**：
  1. 拆分评分字段：
     - `technical_score`
     - `policy_score`
     - `fund_flow_score`
     - `event_score`
     - `risk_penalty`
     - `data_quality_score`
  2. 新增 `tier`：
     - A：可重点盯盘/可轻量 TA
     - B：观察
     - C：过滤或低优先级
  3. 新增 `ranking_reasons` 与 `weakness_reasons`，前端展示“排前原因/扣分原因”。
  4. 分数归一化，避免一批票全部 45-55 分看不出差异。
- **验收方式**：
  - fixture 中强共振候选、单一技术候选、弱事件候选的排序稳定。
  - A/B/C 层不全为空，且分数差距可解释。
  - 前端候选表至少显示 tier、总分、前两条排前原因和扣分原因入口。
- **代码标注要求**：`# [TF-QUALITY-002] score_separation`

### TF-OBS-002: 盘中观察自动执行与 A 股红绿视觉修正（P0）
- **描述**：盘中观察不应必须用户点“执行”才看到结果；同时距离触发价的颜色要符合 A 股直觉：上涨/接近突破用红，跌破/远离/风险用绿或灰。
- **优先级**：P0
- **状态**：done -- commit 8ede481
- **前置条件**：T-004、T-008 完成 ✓；TF-QUALITY-001A 完成。
- **执行约束**：
  - 不做高频盯盘；默认低频/手动刷新即可。
  - 不调用 LLM。
  - 不输出强买卖词，只输出触发/等待/失效/风险。
- **实现要点**：
  1. 打开 TradeFlow 盘中观察页时，自动读取最近有效计划并拉取 observe 状态。
  2. 如果当日无计划但存在上一非交易日计划，按 TF-DATE-001 的有效交易日映射读取。
  3. 保留“手动刷新/执行”按钮，但页面不能空白等待用户第一次点击。
  4. 修正视觉语义：
     - 接近/突破触发价：红色或暖色。
     - 跌破失效价/风险扩大：绿色或冷色。
     - 未到条件：灰色。
  5. API 返回 `observe_auto_run`、`last_observed_at`、`observe_reason`。
- **验收方式**：
  - 进入页面后无需点击即可看到最近计划的观察状态或明确空状态原因。
  - 触发价距离颜色符合 A 股红涨绿跌直觉。
  - fixture 覆盖：已触发、等待、失效、无计划、非交易日计划映射。
- **代码标注要求**：`# [TF-OBS-002] observe_auto_run` / `// [TF-OBS-002] observe_auto_run`

### TF-REVIEW-002: 盘后 Review 数据补齐与非交易日计划映射（P0）
- **描述**：修复盘后 Review 无数据。非交易日生成的候选池应映射到下一交易日复盘；缺行情时要显示原因而不是空表。
- **优先级**：P0
- **状态**：done -- commit 19b879b
- **前置条件**：T-005、M-007、TF-DATE-001 完成 ✓；TF-QUALITY-001A 完成。
- **执行约束**：
  - 不写生产库测试数据。
  - 不调用 LLM。
  - 不把缺数据当作策略失败。
- **实现要点**：
  1. `review_date` 与 `candidate_plan_date` 拆分：
     - 5 月 31 日生成计划，可在 6 月 1 日或下一交易日 Review。
  2. Review 页面默认选择最近一个有候选的计划，并计算可用交易日收益。
  3. `compute_returns()` 在进入 Review 时自动触发或提示缺少行情原因。
  4. 输出分层统计：命中/误报/无数据/失效，按策略和候选层级拆分。
  5. 无数据时显示 `NO_MARKET_DATA / NON_TRADING_DAY / SOURCE_FAILED / NOT_ENOUGH_DAYS`。
- **验收方式**：
  - fixture：周末计划映射到下一交易日 Review。
  - 页面不再出现“全部空白但无解释”。
  - 0% 收益显示为 `0.0%`，不是 `N/A%`。
  - 数据不足 3/5 日时，只有对应周期显示 `N/A%`。
- **代码标注要求**：`# [TF-REVIEW-002] review_date_mapping`

### TF-UI-011: 候选详情一键轻量 TA、K 线与公司概览（P1）
- **描述**：候选池中的票必须能继续研究：详情里显示公司概览、K 线入口、入池证据，并提供一键轻量 TA 预案。
- **优先级**：P1
- **状态**：done -- commit 52680d9
- **前置条件**：PERF-002 完成 ✓；TF-QUALITY-001A 完成。
- **执行约束**：
  - 默认只生成轻量 TA 预案，不直接启动 full TA。
  - 不调用 live LLM，除非用户手动确认完整 TA。
  - 不改 prompts。
- **实现要点**：
  1. 候选详情展示：
     - 股票中文名/代码/所属板块/候选类型。
     - 核心业务或公司简介（优先用已有数据源，缺失则明确标注）。
     - 最近 K 线入口或嵌入轻量 K 线图。
     - 入池策略、触发价、失效价、证据缺口、过滤/扣分原因。
  2. 新增“一键轻量 TA”按钮：
     - `TECH_TRADE` 推荐 `SHORT_TECH_LIGHT`。
     - `POLICY_AMBUSH/POLICY_CONFIRM` 推荐 `MIDLINE_POLICY_LIGHT`。
     - 只创建预案/队列记录，需用户确认才执行。
  3. 前端显示预计耗时和是否调用模型。
- **验收方式**：
  - 候选详情不再只有代码和分数。
  - 点击一键轻量 TA 后生成 profile 正确的预案。
  - full TA 未确认时不可启动。
- **代码标注要求**：`# [TF-UI-011] candidate_research_entry`

### DATA-017: 主力资金/龙虎榜数据源健康巡检与 fallback 验收（P1）
- **描述**：针对 TA 报告里主力资金仍失败的问题，建立可复现的数据源健康巡检，区分“该股无龙虎榜”“接口失败”“单位未校验”“fallback 可用”。
- **优先级**：P1
- **状态**：done -- commit 653ec9b
- **前置条件**：DATA-P0-FUND-ROUTE 完成 ✓。
- **执行约束**：
  - 不把新闻/舆情转述当作主力资金原始证据。
  - 不调用 LLM。
  - 不写生产 DB。
- **实现要点**：
  1. 为 `fund_flow_individual`、`fund_flow_sector`、`lhb` 增加 live smoke/fixture 对比：
     - HAS_DATA
     - NORMAL_NO_DATA
     - FAILED
     - STALE
     - UNIT_UNVERIFIED
  2. 龙虎榜必须区分：
     - 未上榜/非异动日：正常无数据。
     - 查询失败/接口异常：失败。
  3. 主力资金必须记录单位、日期、供应商、是否 fallback。
  4. 报告数据源摘要里显示资金/LHB 的真实状态。
  5. 选取 3 个样本：有资金数据、无龙虎榜、接口失败 fixture。
- **验收方式**：
  - fixture 覆盖三类状态。
  - 主力资金失败时 readiness 不得标 HAS_DATA。
  - 龙虎榜无触发不得降低完整度为失败。
  - 603629 或等价样本能说明失败原因。
- **代码标注要求**：`# [DATA-017] fund_lhb_health`

### V-006: 最终动作语义端到端回放验收（P1）
- **描述**：DECISION-001~004 已完成后，用历史报告和最新报告回放验证系统不再“一片 HOLD/持有”，并确认前端/通知/DB 三处一致。
- **优先级**：P1
- **状态**：done -- commit a1d2a89
- **前置条件**：DECISION-004 完成 ✓。
- **执行约束**：
  - 不调 LLM。
  - 不改 prompts。
  - 测试使用历史报告 fixtures 或临时数据库。
- **实现要点**：
  1. 建立端到端回放脚本/测试：
     - 未持仓偏多无触发价 → 等待触发/WAIT。
     - 未持仓看多有触发价 → 条件入场/ENTER。
     - 未持仓偏空 → 回避/WAIT。
     - 已持仓中性 → 持有/HOLD。
     - 已持仓偏空 → 条件减仓/REDUCE。
  2. 验证 DB 轻量列、报告详情 result_data、前端 Report 类型、通知 payload 一致。
  3. 输出一份 `docs/decision_replay_report.md`，列出样本与结果。
- **验收方式**：
  - 至少 5 个样本动作标签不全为“持有”。
  - 保存报告后不会丢失 position-aware 语义。
  - Bark/企业微信/报告列表均优先显示 `action_label`。
- **代码标注要求**：`# [V-006] decision_semantics_e2e`

### DATA-COVERAGE-001: raw_evidence 覆盖率分母/质量等级回归修复（P1）
- **描述**：TF-QUALITY-001 全量回归暴露 DATA-007/E-004/V-001 相关覆盖率断言下降：新增 ratings/buybacks/report 等证据字段后，分母变化与 NORMAL_NO_DATA/NOT_AVAILABLE 处理导致“完整证据”样本被误降级为 LOW。需要单独修复数据覆盖率契约，不和 TradeFlow 门禁混在一起。
- **优先级**：P1
- **状态**：done — Codex 修复，待提交
- **前置条件**：DATA-012A 完成 ✓；数据源目录回归修复 commit `e4a4062` 已完成。
- **执行约束**：
  - 不改 prompts。
  - 不调用 LLM。
  - 不写生产数据库。
  - 不用改测试期望掩盖真实分母变化；必须解释每个新增 evidence key 是否进入分母、是否 critical。
- **问题样本**：
  - `tests/test_data007_evidence_coverage_audit.py::TestAuditRawEvidence::test_full_evidence`
  - `tests/test_e_series_fixes.py::TestE004NotAvailable::*`
  - `tests/test_v001_600584_data_authenticity.py::TestEndToEndScenarios::test_scenario_happy_path`
- **实现要点**：
  1. 梳理 raw_evidence key 清单：OHLC、volume、fund_flow、lhb、margin、announcements、ratings、buybacks、research_report 等。
  2. 明确 `NORMAL_NO_DATA`、`NOT_AVAILABLE`、`NOT_QUERIED` 对分母和 critical_missing 的影响。
  3. 对新增字段设置合理 criticality，避免评级/回购/研报缺失把完整行情样本误降为 LOW。
  4. 更新 evidence coverage audit 输出，显示分母变化原因。
- **验收方式**：
  - `.venv/bin/python -m pytest tests/test_data007_evidence_coverage_audit.py tests/test_e_series_fixes.py tests/test_v001_600584_data_authenticity.py -q`
  - 旧完整样本不得误降级为 LOW。
  - 失败/单位未校验仍应触发降级，不得一刀切放宽。
- **代码标注要求**：`# [DATA-COVERAGE-001] evidence_denominator_contract`

### CODEGRAPH-002: CodeGraph 自动开发预检命令修复（P2）
- **描述**：TF-QUALITY-001 task_run 中 CodeGraph 预检失败，原因是自动开发脚本使用了当前 codegraph CLI 不支持的参数（如 `-p`）。需要修复命令封装，避免影响后续任务的影响范围日志。
- **优先级**：P2
- **状态**：done -- commit f54a45e
- **前置条件**：M-013 完成 ✓。
- **执行约束**：
  - 不改业务代码。
  - 不要求 CodeGraph 决定任务成败；预检失败只能降级为 warning。
- **实现要点**：
  1. 检查本机 `codegraph --help` / `codegraph context --help` 的真实参数。
  2. 修复自动开发中调用 CodeGraph 的命令。
  3. task_run 中记录 `codegraph-status.json`：`ok/skipped/warn`，并包含实际命令和 stderr 摘要。
- **验收方式**：
  - dry-run 自动开发能生成有效 codegraph status。
  - 无 codegraph 或命令失败时不阻塞 OpenCode 开发，只进入 warning。
- **代码标注要求**：`# [CODEGRAPH-002] codegraph_preflight`

### TF-QUALITY-003: 候选池精度校准与弱候选压缩（P0）
- **描述**：在 TF-QUALITY-002 分项评分落地后，继续把候选池从"看起来很多"压缩到"真正值得盯的少数票"，避免弱 VCP、弱事件、数据不足候选进入主候选。
- **优先级**：P0
- **状态**：done -- commit ab3cb1f
- **完成时间**：2026-06-13
- **前置条件**：TF-QUALITY-002 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不改 prompts。
  - 不输出强买卖词。
  - 不用提高阈值一刀切误杀昊天左侧候选，必须区分短线技术池与昊天左侧池。
- **实现要点**：
  1. 基于 `technical_score/policy_score/fund_flow_score/event_score/data_quality_score/risk_penalty_score` 建立主候选准入规则。
  2. `TECH_TRADE` 主候选必须满足至少两类共振：形态、量能、资金、触发/失效价、数据质量。
  3. `POLICY_AMBUSH/POLICY_CONFIRM` 主候选必须满足政策主题、受益路径、反证不过热、证据覆盖四项中的至少两项。
  4. 单一弱信号进入 observation 或 filtered，并写清 `pool_filter_reason`。
  5. 输出主候选默认上限建议：技术池 3 只、昊天池 3 只、总主候选 5 只；超出进入 observation。
- **验收方式**：
  - fixture 中 20 只候选最终主候选不超过 5 只。
  - 弱 VCP、弱事件、数据不足候选不进入 main。
  - 昊天左侧候选不会因短线未突破被直接过滤，只能进入 observation 或 haotian main。
  - `pytest tests/test_tf_quality001_pool_gate.py tests/test_ui001_tradeflow_api.py -q` 或等价测试通过。
- **代码标注要求**：`# [TF-QUALITY-003] candidate_precision_gate`

### TF-PAPER-001: 5000 元试跑模拟账户与候选跟踪账本（P1）
- **描述**：为用户准备 5000 元小资金试跑环境，记录候选进入、观察触发、轻量 TA、模拟买入/卖出、收益和复盘，不连接真实交易。
- **优先级**：P1
- **状态**：done -- commit b5e7b18
- **前置条件**：TF-OBS-002、TF-REVIEW-002 完成。
- **执行约束**：
  - 严禁接入真实券商交易。
  - 不输出“立即买入/清仓”等强动作，只记录模拟动作与触发条件。
  - 不调用 LLM。
- **实现要点**：
  1. 新增 TradeFlow paper ledger 表或轻量 JSON 存储：本金、候选、计划日期、触发价、失效价、模拟仓位、备注。
  2. 候选详情支持“加入模拟跟踪”，默认金额上限 5000，可配置但不自动下单。
  3. 盘中 Observe 触发后可写入“待人工确认模拟动作”。
  4. 盘后 Review 聚合模拟账本收益、误报、未触发、失效。
  5. 前端展示试跑看板：本金、持仓模拟、待确认、已完成复盘。
- **验收方式**：
  - 临时 DB/fixture 能创建 5000 元账本并加入 2 只候选。
  - 未经人工确认不会生成模拟成交。
  - 盘后 Review 能读取账本并展示收益或无数据原因。
- **代码标注要求**：`# [TF-PAPER-001] paper_trading_ledger` / `// [TF-PAPER-001] paper_trading_ledger`

### H-012: 昊天主题注册表与政策版本 Watchlist（P1）
- **描述**：把昊天战法从零散政策关键词升级为可维护主题注册表，记录主题级别、生命周期、政策证据、产业链角色和重点观察标的。
- **优先级**：P1
- **状态**：done -- commit 2ceb56a
- **前置条件**：H-010、H-011 完成。
- **执行约束**：
  - 不调 LLM。
  - 不把政策主题直接翻译成买卖建议。
  - 不覆盖用户自选备注，只生成建议备注/主题标签。
- **实现要点**：
  1. 新增主题注册表结构：主题名、政策级别、状态、最近信号日、证据链接/摘要、核心产业链环节。
  2. 候选入池时关联 `mandate_topic` 到主题注册表，输出主题状态：酝酿/发酵/确认/兑现/退潮。
  3. 生成主题 Watchlist：每个主题最多列出核心标的、受益路径、反证缺口。
  4. 与 H-008 自选备注联动，生成简短备注建议。
- **验收方式**：
  - fixture 中“低空经济/算力/半导体设备”等主题能注册并匹配候选。
  - 主题状态变化不影响交易动作，只影响候选解释和排序。
  - 前端或 API 能返回主题清单与候选映射。
- **代码标注要求**：`# [H-012] mandate_topic_registry`

### DATA-018: A股关键源新鲜度与 fallback 可视化日报（P1）
- **描述**：把 DATA-017 的资金/LHB 健康巡检扩展到行情、资金、龙虎榜、公告、研报、评级、回购等关键源，形成每日可读的数据源新鲜度与 fallback 报告。
- **优先级**：P1
- **状态**：done -- commit 98725b2
- **前置条件**：DATA-017、DATA-006 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不打印 API key。
  - live smoke 必须限频，失败不阻塞业务，只输出状态。
- **实现要点**：
  1. 建立 `source_freshness_report`：数据类型、主 vendor、fallback vendor、最近数据日、状态、错误摘要。
  2. 区分 `HAS_DATA/NORMAL_NO_DATA/STALE/FAILED/RATE_LIMITED/UNIT_UNVERIFIED`。
  3. 写入 `docs/data_source_reports/` 并接入夜间日报摘要。
  4. 前端数据健康面板读取最新报告并显示红黄绿状态。
- **验收方式**：
  - fixture 能模拟 AKShare 失败、cn_astock fallback 成功、龙虎榜正常无数据。
  - 报告中不出现明文密钥。
  - 数据健康 API/前端能看到各源状态。
- **代码标注要求**：`# [DATA-018] source_freshness_report`

### UI-012: TradeFlow 前端降噪与主候选优先工作台（P1）
- **描述**：当前候选池信息过散，用户要试跑时需要一眼看到少数主候选、原因、触发条件和下一步动作。本任务把前端默认视图改为主候选工作台。
- **优先级**：P1
- **状态**：done -- commit af33ece
- **前置条件**：TF-QUALITY-002、TF-OBS-002 完成。
- **执行约束**：
  - 不新增营销式页面。
  - 不隐藏 observation/filtered，只是默认折叠。
  - 不触发 full TA。
- **实现要点**：
  1. TradeFlow 默认首屏展示 `main_candidates`，observation/filtered 折叠到独立 tab 或 drawer。
  2. 每只主候选显示：候选类型、tier、总分、分项分、前两条排前原因、前两条扣分原因、触发价/失效价、观察状态。
  3. 技术池与昊天池视觉区分，但颜色遵守 A 股红涨绿跌直觉。
  4. 增加“下一步”按钮：观察、轻量 TA 预案、加入模拟账本。
- **验收方式**：
  - 主候选为空时有明确原因，不显示空白。
  - 观察/过滤候选可追溯但不干扰主视线。
  - 前端 build 通过。
- **代码标注要求**：`// [UI-012] tradeflow_focus_workspace`

### V-007: TradeFlow 试用闭环端到端验收（P1）
- **描述**：把候选筛选、盘中观察、轻量 TA 预案、模拟账本、盘后 Review 串成一条可回放链路，验证系统能支撑用户小资金试跑。
- **优先级**：P1
- **状态**：done -- commit 9ceedbf
- **前置条件**：TF-QUALITY-003、TF-PAPER-001 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不写生产数据库。
  - 不接真实交易。
- **实现要点**：
  1. 新增 E2E fixture：生成 5 只候选 → 主候选 2 只 → 盘中触发 1 只 → 加入模拟账本 → 盘后 Review。
  2. 验证所有日期语义：非交易日计划、有效交易日观察、盘后复盘。
  3. 验证前端/API 字段不丢：中文名、候选类型、分项分、观察状态、模拟账本、Review 结果。
  4. 输出 `docs/tradeflow_trial_acceptance.md`。
- **验收方式**：
  - smoke/e2e 测试通过。
  - 试跑报告能说明“为什么入池、什么时候触发、触发后如何记录、盘后结果如何”。
  - 无强买卖建议。
- **代码标注要求**：`# [V-007] tradeflow_trial_acceptance`

### TF-API-013: TradeFlow API 合约收口与 HTTP 路由回归（P0）
- **描述**：收口 2026-06-14 Codex review 发现的 API 合约问题：静态路由被 `/candidates/{symbol}` 吞、`response_model` 过滤分项评分字段、daily-plan 字段缺失。当前已有人工补修，仍需补齐剩余字段与 HTTP 层测试后提交。
- **优先级**：P0
- **状态**：done — `fce3141` fix(tradeflow): close API response contract gaps；`289a392` docs(tasks): release next TradeFlow trial tasks。818 passed, 2 skipped, 0 failed。
- **前置条件**：V-007 完成 ✓。
- **执行约束**：
  - 不调用 LLM。
  - 不改 prompts。
  - 不写生产数据库。
  - 不把测试刷新生成时间的 `docs/tradeflow_trial_acceptance.md` 混入提交，除非验收报告内容确实变化。
- **实现要点**：
  1. 确认 `/v1/tradeflow/candidates/tiered`、`/v1/tradeflow/candidates/compare`、`/v1/tradeflow/candidates/{symbol}/overview` 注册顺序在 `/v1/tradeflow/candidates/{symbol}` 之前。
  2. `TradeFlowCandidateItem` 必须包含 8 个分项字段：`technical_score/policy_score/fund_flow_score/event_score/risk_penalty_score/data_quality_score/ranking_reasons/weakness_reasons`。
  3. `get_daily_plan()` 从 `candidates_json` 回填候选类型、昊天字段和 8 个分项字段。
  4. 新增 HTTP 层回归测试，不能只测 service：真实请求 `tiered/compare` 返回正确 response shape，候选 response 不丢 8 个字段。
- **验收方式**：
  - `.venv/bin/python -m pytest tests/test_v007_tradeflow_trial_e2e.py tests/test_tf_ui011_candidate_research_entry.py tests/test_tf_quality001_pool_gate.py -q`
  - 额外验证 `TradeFlowCandidateItem.model_fields` 包含 8 个分项字段。
  - TestClient 访问 `/v1/tradeflow/candidates/tiered` 返回 `actionable/watch/scan/main_candidates`，不是 `candidate:null`。
- **代码标注要求**：`# [TF-API-013] tradeflow_api_contract`

### TF-QUALITY-004: 候选池实盘区分度回放校准（P0）
- **描述**：在候选池已经收敛后，用近期真实候选池/fixture 做回放，校准“主候选过多、分数差异不明显、像半山腰抄底”的问题。目标是让主候选默认非常少，且每只主候选都有明确“为什么值得盯”的证据。
- **优先级**：P0
- **状态**：done — 2026-06-16 OpenCode 执行，5842 passed/0 failed，4 条校准规则上线。
- **前置条件**：TF-API-013、V-007 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不改 prompts。
  - 不写生产数据库。
  - 不用一刀切高阈值误杀昊天左侧候选；技术做 T 与昊天左侧必须分池校准。
- **实现要点**：
  1. 新增 `docs/tradeflow_calibration/` 回放样本摘要，至少覆盖最近 2-3 次候选池。
  2. 统计每次候选：main/observation/filtered 数量、候选类型、分项评分、触发后表现。
  3. 技术池主候选默认不超过 3 只；弱 VCP、无资金/无量能/数据不足必须降到 observation 或 filtered。
  4. 昊天左侧候选不要求短线突破，但必须有政策主题+受益路径/证据覆盖至少两类支撑。
  5. 输出校准报告：哪些规则让候选减少、哪些票被降级、是否误杀。
- **验收方式**：
  - fixture/样本 20 只候选时主候选默认不超过 5 只，技术池不超过 3 只。
  - 每只 main candidate 至少有 2 条 `ranking_reasons` 或 2 个 precision dimensions。
  - `filtered_candidates` 有可读 `pool_filter_reason`。
- **代码标注要求**：`# [TF-QUALITY-004] live_pool_calibration`

### TF-RISK-001: 5000 元试跑风险预算与仓位纪律（P1）
- **描述**：为用户 5000 元小资金试跑增加明确纪律：单票上限、单日上限、触发前不模拟成交、失效价缺失不得加入待执行动作，避免候选池变成随手点买。
- **优先级**：P1
- **状态**：done — 2026-06-16 OpenCode 执行，57+24 passed/0 failed。
- **前置条件**：TF-PAPER-001 完成。
- **执行约束**：
  - 不接真实券商。
  - 不输出强买卖词。
  - 不调用 LLM。
- **实现要点**：
  1. paper ledger 增加配置：本金、单票计划金额上限、单日新增候选上限、最大同时跟踪数。
  2. 加入模拟跟踪时校验 trigger/invalid price、candidate_type、data_quality_score。
  3. 风险预算不足时只允许观察，不允许进入待确认模拟动作。
  4. 前端显示“剩余额度/单票上限/当前风险暴露”。
- **验收方式**：
  - 5000 元账本单票默认不超过 1000-1500 元。
  - 缺失失效价或数据质量过低时，加入模拟动作被拒绝并显示原因。
  - 不影响已有 paper ledger 测试。
- **代码标注要求**：`# [TF-RISK-001] paper_risk_budget` / `// [TF-RISK-001] paper_risk_budget`

### TF-OBS-003: 盘中观察触发到模拟账本待确认联动（P1）
- **描述**：盘中 Observe 触发后，不只是显示触发状态，还要把已加入模拟账本的候选同步为“待人工确认”，让用户能清楚看到哪些票真的到了执行条件。
- **优先级**：P1
- **状态**：done -- commit fcce883
- **前置条件**：TF-OBS-002、TF-PAPER-001 完成。
- **执行约束**：
  - 不自动模拟成交。
  - 不调用 LLM。
  - 不接真实交易。
- **实现要点**：
  1. Observe runner 更新候选状态后，同步调用 paper ledger 的 observe state update。
  2. 触发、失效、等待三种状态分别进入 pending/invalidated/tracking。
  3. 前端 Observe 面板增加“已加入试跑/待确认”标识。
  4. 保留手动“执行观察”按钮，但页面打开自动观察成功时也同步账本。
- **验收方式**：
  - fixture 中一只候选触发后，paper ledger 对应记录进入 pending。
  - 失效后进入 invalidated。
  - 未加入 paper ledger 的候选不会生成模拟记录。
- **代码标注要求**：`# [TF-OBS-003] observe_paper_sync`

### TF-REVIEW-003: 盘后 Review 策略命中归因与次日反馈（P1）
- **描述**：盘后 Review 不只显示是否继续观察，还要解释策略质量：命中/未命中、触发后表现、失效原因、是否应该降低类似候选权重，为下一日候选池校准提供反馈。
- **优先级**：P1
- **状态**：done -- commit 738baa2
- **前置条件**：TF-REVIEW-002、TF-PAPER-001 完成。
- **执行约束**：
  - 不自动调参。
  - 不调用 LLM。
  - 不生成投资建议，只做信号质量复盘。
- **实现要点**：
  1. Review item 增加命中归因：technical_hit/policy_hit/fund_flow_hit/data_issue/risk_hit。
  2. 聚合策略标签表现：VCP、PULLBACK、POLICY_AMBUSH、EVENT_WATCH 等。
  3. 输出“明日关注/降级原因/需要补证据”的结构化字段。
  4. 与 TF-QUALITY-004 的校准报告共享数据格式。
- **验收方式**：
  - fixture 能产生至少 1 个命中、1 个失效、1 个未触发样本。
  - Review API 返回策略归因字段。
  - 前端 Review 不再只有“继续观察”。
- **代码标注要求**：`# [TF-REVIEW-003] strategy_attribution_review`

### DATA-019: 关键数据源实盘抽样健康日报（P1）
- **描述**：在 DATA-018 的 freshness 报告基础上，增加小样本实盘抽样：行情、主力资金、龙虎榜、公告、评级、回购、研报等源每天抽 3-5 只票验证状态，避免“接口看似可用但真实股票取不到”。
- **优先级**：P1
- **状态**：done -- commit 58b5cd2
- **前置条件**：DATA-018、DATA-017 完成。
- **执行约束**：
  - live smoke 必须限频，失败只记录，不阻塞主业务。
  - 不打印 API key。
  - 不把新闻/公告文本误当资金流证据。
- **实现要点**：
  1. 抽样 universe：一只大票、一只中小票、一只近期候选、一只用户自选。
  2. 每个 data type 记录 `HAS_DATA/NORMAL_NO_DATA/STALE/FAILED/RATE_LIMITED/UNIT_UNVERIFIED`。
  3. 对主力资金和龙虎榜单独说明：龙虎榜无触发是 NORMAL_NO_DATA，主力资金一般应可取，失败需 fallback。
  4. 报告写入 `docs/data_source_reports/YYYY-MM-DD-live-smoke.md`。
- **验收方式**：
  - fixture 模拟 AKShare 失败、cn_astock fallback 成功、龙虎榜正常无数据。
  - live smoke 可通过环境变量关闭。
  - 报告不含密钥。
- **代码标注要求**：`# [DATA-019] live_source_sampling`

### H-013: 昊天主题热度曲线与政策证据看板（P1）
- **描述**：把 H-012 的主题注册表做成可观察的“主题热度/生命周期”数据，帮助用户看政策重心是否持续，而不是只看单只股票技术形态。
- **优先级**：P1
- **状态**：done -- commit b1fe17d
- **前置条件**：H-012、H-010 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不把主题热度直接等价为交易动作。
  - 不覆盖用户自选备注。
- **实现要点**：
  1. 每个主题记录最近 7/20/60 天信号数、政策级别、候选数量、证据缺口、过热标记。
  2. 输出主题状态变化：酝酿、发酵、确认、兑现、退潮。
  3. 前端主题看板显示主题、核心证据、候选数、热度变化和反证。
  4. 与候选详情互链：从主题进入候选，从候选回到主题。
- **验收方式**：
  - fixture 中低空经济/算力/半导体设备能生成主题热度。
  - 主题热度变化不会直接改变最终动作，只影响排序解释。
  - API/前端均能展示主题证据和候选映射。
- **代码标注要求**：`# [H-013] mandate_topic_heatmap` / `// [H-013] mandate_topic_heatmap`

### DATA-019A: 实盘抽样 skipped 状态与任务池收口补修（P2）
- **描述**：收口 DATA-019 Codex review 发现的 P2：当 `TA_LIVE_DATA_SMOKE` 未开启导致所有检查都是 `SKIPPED` 时，报告不能显示为 all green；并确保任务池顶部/详情状态一致。
- **优先级**：P2
- **状态**：done — skipped-only 不再误报 ALL GREEN；targeted tests 与前端 build 通过。
- **前置条件**：DATA-019 完成。
- **执行约束**：
  - 不调用 live API，除非显式设置 `TA_LIVE_DATA_SMOKE=1`。
  - 不打印密钥。
  - 不阻塞主业务。
- **实现要点**：
  1. `live_source_sampling.py` 汇总状态增加 skipped-only 场景，`overall_status` 应为 `skipped` 或等价非绿色状态。
  2. Markdown 报告明确写“未执行实盘抽样/等待启用 live smoke”，不得写 `ALL GREEN`。
  3. 补 skipped-only 回归测试。
  4. 检查 `docs/TASKS.md` 顶部与详情状态一致。
- **验收方式**：
  - skipped-only fixture 不再 `all_green=True`。
  - DATA-019 相关测试通过。
  - `docs/TASKS.md` 不再残留 DATA-019 `ready/in_progress`。
- **代码标注要求**：`# [DATA-019A] live_source_sampling_skip_status`

### H-013A: 昊天热度图历史日期 fallback 与窗口 unique 统计补修（P2）
- **描述**：收口 H-013 Codex review 发现的 P2/P3：topic heatmap 查询不能漏掉 `effective_trade_date` 为空但 `trade_date` 有值的历史候选；窗口统计里的 `unique_candidates` 不能永远为 0；前端 topic→candidate 点击应保留 symbol 上下文。
- **优先级**：P2
- **状态**：done — 日期 fallback、unique_candidates、前端 symbol 跳转已修复；targeted tests 与前端 build 通过。
- **前置条件**：H-013 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不写生产数据库。
  - 不改变主题热度对最终动作的非强制属性。
- **实现要点**：
  1. `get_topic_heatmap()` 默认 as_of 查询与 window 过滤使用 `COALESCE(NULLIF(effective_trade_date, ''), trade_date)`。
  2. `topic_heatmap.py` 的 7/20/60 日窗口统计正确填充候选 symbol 集合。
  3. 前端点击关联候选时切到候选池并保留/打开对应 symbol。
  4. 补 legacy empty effective_trade_date 与 unique_candidates 回归测试。
- **验收方式**：
  - legacy row 只有 `trade_date` 时仍进入 heatmap。
  - 非空热度曲线窗口 `unique_candidates > 0`。
  - 前端点击 topic candidate 不丢 symbol。
- **代码标注要求**：`# [H-013A] mandate_topic_heatmap_fix` / `// [H-013A] mandate_topic_heatmap_fix`

### V-008: TradeFlow 小资金试跑前整体验收（P1）
- **描述**：在 API 合约、候选压缩、风险预算、Observe 联动和 Review 归因完成后，做一次完整试跑前验收，确认用户可以用 5000 元模拟流程安全试用。
- **优先级**：P1
- **状态**：done — 55 passed；TradeFlow 回归 676 passed，5 个 pre-existing 周日非交易日失败与本任务无关。
- **前置条件**：TF-QUALITY-004、TF-RISK-001、TF-OBS-003、TF-REVIEW-003、DATA-019A、H-013A 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不写生产数据库。
  - 不接真实交易。
- **实现要点**：
  1. 新增 E2E fixture：生成候选 → 压缩主候选 → 加入模拟账本 → 盘中触发 → 人工确认模拟动作 → 盘后 Review 归因。
  2. 验证 API route/response_model 不丢字段。
  3. 验证前端关键字段：主候选、分项评分、排前/扣分原因、额度、待确认、复盘归因。
  4. 输出 `docs/tradeflow_trial_acceptance_v2.md`。
- **验收方式**：
  - E2E 测试通过。
  - 试跑报告能回答：今天看哪几只、为什么、何时触发、风险额度、盘后表现。
  - 无强买卖建议。
- **代码标注要求**：`# [V-008] paper_trial_acceptance_v2`

### TF-UX-001: TradeFlow 小资金试跑主工作台降噪与默认视图（P1）
- **描述**：把 TradeFlow 前端默认工作流收敛成“小资金试跑”视角，减少用户看到一堆候选却不知道先看哪只的问题。
- **优先级**：P1
- **状态**：done — OpenCode 2026-06-22（commit `5edaff2`，运行档案 `docs/task_runs/TF-UX-001-20260622-133149/`）
- **前置条件**：TF-QUALITY-004、TF-RISK-001 完成 ✓
- **执行约束**：
  - 不调用 LLM。
  - 不写生产数据库。
  - 不改变后端候选评分，只调整前端默认展示与解释。
- **实现要点**：
  1. 默认进入主候选优先视图，只显示主候选、接近触发、待确认三组；弱观察池默认折叠。
  2. 候选卡片必须展示：候选类型、综合分、核心触发价、风险预算占用、为什么入选、为什么未入选主候选。
  3. 增加“5000 元试跑”提示区：单票预算、最大占用、待确认动作，不出现强买卖词。
  4. 保持完整候选表可展开查看，避免丢信息。
- **验收方式**：
  - 前端 smoke 测试覆盖默认视图和折叠观察池。
  - 10 只候选 fixture 中默认主视图不超过 5 只。
  - 用户能一眼看到“今天先看哪几只、为什么、风险额度”。
- **代码标注要求**：`// [TF-UX-001] small_cap_trial_workbench`

### DATA-020: 数据源健康日报前端可视化与 skipped/failed 分层展示（P1）
- **描述**：把 DATA-019/019A 的关键数据源抽样结果接入 TradeFlow/数据健康面板，让 skipped、failed、normal-no-data、fallback 不再混在一起。
- **优先级**：P1
- **状态**：done — DATA-020-20260624-111210
- **前置条件**：DATA-019A 完成。
- **执行约束**：
  - 默认不触发 live smoke。
  - 不打印密钥。
  - 不把 skipped 显示为健康。
- **实现要点**：
  1. API 提供 latest live sampling report 读取接口，缺报告时返回 no_data。
  2. 前端数据健康 tab 增加“实盘抽样”区块：总览、按数据源、按样本股票、fallback 次数。
  3. skipped-only 显示为“未启用实盘抽样”，failed/rate_limited 显示为红色故障，normal-no-data 显示为灰色正常无数据。
  4. 数据源故障时给出“影响哪些报告字段”的简短说明。
- **验收方式**：
  - fixture latest report 能渲染 skipped/failed/fallback 三类状态。
  - 没有报告时前端显示空状态，不报错。
  - 不触发真实数据接口。
- **代码标注要求**：`# [DATA-020] live_sampling_health_ui` / `// [DATA-020] live_sampling_health_ui`

### TF-REVIEW-004: 盘后 Review 空数据诊断与一键生成入口（P1）
- **描述**：用户反馈盘后 Review 经常“没有数据”。本任务要求把空数据原因讲清楚，并提供一键生成/补生成入口。
- **优先级**：P1
- **状态**：done -- commit da55c62
- **前置条件**：TF-REVIEW-003 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不写生产数据库以外的临时验证数据。
  - 不更改真实交易状态。
- **实现要点**：
  1. Review API 空结果时返回诊断字段：无候选、无 observe、非交易日映射、行情缺失、尚未生成。
  2. 前端 Review tab 显示具体空状态原因，而不是空白。
  3. 增加“一键生成盘后 Review”按钮，调用现有生成逻辑并刷新。
  4. 非交易日计划映射到下一个交易日时，Review 显示 plan_date/effective_trade_date/review_date。
- **验收方式**：
  - fixture 覆盖 4 类空状态。
  - 一键生成后能看到 review summary 或明确失败原因。
  - 非交易日生成候选、下个交易日 review 的链路可回放。
- **代码标注要求**：`# [TF-REVIEW-004] review_empty_diagnostics` / `// [TF-REVIEW-004] review_empty_diagnostics`

### H-014: 昊天主题候选减少与主题集中度阈值校准（P1）
- **描述**：左侧埋伏不应该像短线做 T 一样吐出太多票。本任务用主题集中度、政策证据强度和反证缺口压缩昊天候选池。
- **优先级**：P1
- **状态**：done -- commit 13c257d
- **前置条件**：H-013A、H-012 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不输出强买卖词。
  - 不删除原始候选，只改变主候选/观察候选分层。
- **实现要点**：
  1. 新增主题集中度指标：同主题候选数、政策证据数、主题热度趋势、生命周期状态。
  2. 昊天左侧主候选默认只保留强主题下的 Top 1-3，其他进入观察/证据不足。
  3. 对政策证据弱、主题扩散弱、反证缺口多的候选降级，并说明原因。
  4. 前端展示“主题集中度/为什么只选这几只”。
- **验收方式**：
  - fixture 20 只昊天候选默认主候选不超过 3-5 只。
  - 弱主题不进入主候选，但保留观察理由。
  - 输出能回答：现在昊天重心在哪个主题、该主题先看哪几只、证据缺口是什么。
- **代码标注要求**：`# [H-014] mandate_concentration_gate` / `// [H-014] mandate_concentration_gate`

### V-009: 小资金试跑后回归清单与用户操作手册（P1）
- **描述**：在 V-008 完成后，把 5000 元模拟试跑流程沉淀成可重复验收清单和用户操作手册，方便用户白天按步骤验证，而不是靠口头记忆。
- **优先级**：P1
- **状态**：done -- commit 4610bd9
- **前置条件**：V-008 完成 ✓
- **执行约束**：
  - 不调用 LLM。
  - 不接真实交易。
  - 不写生产交易数据。
- **实现要点**：
  1. 输出 `docs/tradeflow_trial_user_guide.md`：生成候选池、查看主候选、查看风险预算、盘中观察、人工确认、盘后 Review。
  2. 输出 `docs/tradeflow_trial_regression_checklist.md`：每个步骤对应 API/前端字段和期望结果。
  3. 增加最小 smoke 测试，保证手册中引用的 API route 和核心字段仍存在。
  4. 标明所有动作均为模拟/观察，不是实盘交易建议。
- **验收方式**：
  - 文档能让用户从空白页面跑完整个模拟流程。
  - smoke 测试通过。
  - 文档不出现强买卖词。
- **代码标注要求**：`# [V-009] trial_user_guide`

### TF-OBS-004: 盘中观察自动刷新、触发提醒与人工确认队列降噪（P1）
- **描述**：盘中观察不能靠用户反复手点执行。本任务优化观察 tab 的刷新、触发提醒和待确认队列，让真正接近触发的票浮上来。
- **优先级**：P1
- **状态**：done — TF-OBS-004-20260624-124926（36 tests passed + 302 observe 回归 + 211 API/账本回归 + npm build 通过）
- **前置条件**：TF-OBS-003、TF-RISK-001 完成。
- **执行约束**：
  - 不接真实交易。
  - 不调用 LLM。
  - 不新增高频轮询，遵守现有速度预算。
- **实现要点**：
  1. 前端 observe tab 显示自动刷新状态、上次刷新时间、下次刷新倒计时。
  2. 触发/接近触发/失效三类信号分组展示，待确认优先。
  3. 触发提醒只进入模拟待确认队列，不自动下单。
  4. 后端返回“为什么触发/为什么未触发/差多少”的结构化字段。
- **验收方式**：
  - fixture 中触发、接近触发、失效三类都能正确分组。
  - 无强买卖词。
  - 不增加 FULL_TA 或 LLM 调用。
- **代码标注要求**：`# [TF-OBS-004] observe_refresh_alert_queue` / `// [TF-OBS-004] observe_refresh_alert_queue`

### DATA-021: TA 报告数据源失败原因透传与字段级降级说明（P1）
- **描述**：用户经常看到 TA 报告“数据不足观察”，但不知道是哪类数据不足。本任务把 DATA-020/关键源健康状态透传到 TA 报告 metadata 和前端展示。
- **优先级**：P1
- **状态**：done — Codex 接管完成，commit 待提交
- **前置条件**：DATA-020、DATA-004 完成。
- **执行约束**：
  - 不调用 live API。
  - 不修改 prompts。
  - 不降低强动作门禁。
- **实现要点**：
  1. 在报告 metadata 增加字段级 `data_blockers`：行情、主力资金、龙虎榜、公告、评级、回购、研报。
  2. `readiness_score` 输出“数据不足”的具体原因和影响动作。
  3. 前端报告卡/详情显示“缺什么、是否正常无数据、是否查询失败、是否 skipped”。
  4. 对 `NORMAL_NO_DATA` 和 `FAILED/SKIPPED` 做显式区分。
- **验收方式**：
  - fixture 报告能显示主力资金失败 vs 龙虎榜正常无数据。
  - “数据不足观察”必须附具体字段原因。
  - 不改变原有强动作门禁。
- **代码标注要求**：`# [DATA-021] report_data_blockers` / `// [DATA-021] report_data_blockers`
- **完成记录**：
  - `readiness_score.py` 新增字段级 `data_blockers` 与摘要，显式区分 `normal_no_data` / `query_failed` / `not_queried` / `field_missing` / `skipped`。
  - `report_service.py` 在报告保存时写入 `result_data.data_blockers` / `data_blocker_summary`，不改变动作等级和强动作门禁。
  - `api/main.py` 与前端报告详情页透传并展示数据缺口。
  - 验证：154 个后端定向测试通过，前端 `npm run build` 通过。

### H-015: 昊天主题日报与候选入池/出池解释（P1）
- **描述**：把昊天雷达从单页看板推进到每日摘要：今天主题重心是什么，哪些票入池/出池，为什么入池/出池。
- **优先级**：P1
- **状态**：done — Codex 直接开发，commit 待提交
- **前置条件**：H-014、H-013A 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不输出强买卖词。
  - 不替代 TA 中线研究，只做雷达摘要。
- **实现要点**：
  1. 生成 `docs/mandate_daily_reports/mandate-YYYY-MM-DD.md`。
  2. 内容包括：升温主题、降温主题、主候选、观察候选、入池原因、出池原因、证据缺口。
  3. API 提供 latest mandate daily report。
  4. 前端主题热度 tab 增加日报入口。
- **验收方式**：
  - fixture 能生成一份日报。
  - 入池/出池原因可追溯到候选字段和主题热度字段。
  - 无强买卖词。
- **代码标注要求**：`# [H-015] mandate_daily_report` / `// [H-015] mandate_daily_report`
- **完成记录**：
  - 新增 `mandate_daily_report.py`，从 topic heatmap 生成升温/降温主题、主候选、观察候选、入池原因、出池原因和证据缺口。
  - 新增 `/v1/tradeflow/mandate-daily-report/latest`，支持读取 latest 或即时生成只读预览。
  - TradeFlow 主题热度 tab 增加“昊天主题日报”入口。
  - 验证：H-015/H-013 共 112 个测试通过，前端 `npm run build` 通过。

### PERF-005: TradeFlow 页面与 API 性能预算回归（P2）
- **描述**：随着 TradeFlow UI 和数据面板增多，需要防止页面越来越重、API 越来越慢。本任务建立轻量性能回归，保证默认操作仍然快。
- **优先级**：P2
- **状态**：done -- commit 10a3626
- **前置条件**：TF-UX-001、DATA-020 完成。
- **执行约束**：
  - 不调用真实 LLM。
  - 不跑全市场扫描。
  - 不把性能测试写成易碎的绝对耗时，只做预算分层和明显退化检测。
- **实现要点**：
  1. 为 candidates、observe、review、data-health、topic-heatmap 增加 fast 层级 smoke 性能测试。
  2. 前端 build 后记录 bundle size warning 到文档，不要求立刻 code split。
  3. API 返回 runtime_tier_meta 的端点做一致性检查。
  4. 超预算时输出建议，不直接失败夜间主链，除非明显阻塞。
- **验收方式**：
  - 性能 smoke 测试通过。
  - 文档列出各端点预算和当前观察值。
  - 不触发真实数据/模型调用。
- **代码标注要求**：`# [PERF-005] tradeflow_perf_budget`

### HK-001: 港股输入边界与轻量行情-only 模式声明（P2）
- **描述**：当前系统定位 A 股，港股只能局部走 yfinance 行情。为避免用户输入港股时被 A 股资金/LHB/政策门禁误判，本任务加边界识别和清晰提示。
- **优先级**：P2
- **状态**：done — commit 2326a3d + a9fd09a
- **前置条件**：无。
- **执行约束**：
  - 不承诺完整港股 TA。
  - 不接港股资金流/LHB/公告等未验证数据。
  - 不改 A 股主链。
- **实现要点**：
  1. `0700.HK/9988.HK` 等识别为 `market_country=HK`、`currency=HKD`。
  2. 港股请求默认返回“轻量行情-only/数据源不足”的提示，禁用 A 股特有 LHB、融资融券、主力资金关键门禁。
  3. 前端输入港股时显示“港股暂不支持完整 TA，只支持轻量行情/新闻参考”。
  4. 加测试避免港股被误判为 US 或 A 股。
- **验收方式**：
  - `0700.HK` 不再被标记为 US/USD。
  - 港股不会触发 A 股专属数据缺失导致的误导性结论。
  - A 股现有测试不回归。
- **代码标注要求**：`# [HK-001] hk_market_boundary` / `// [HK-001] hk_market_boundary`

### TF-PERSIST-001: TradeFlow save_candidate 分项评分持久化补口（P2）
- **描述**：V-008 验收发现 `save_candidate()` 直接保存候选时没有持久化 8 个分项评分/解释字段，导致 API 从 DB 读取时返回默认值。生产 `evaluate_symbol` 内存链路可计算，但持久化层存在缺口。
- **优先级**：P2
- **状态**：done — commit 0016a92 + 2257f32
- **前置条件**：V-008 完成。
- **执行约束**：
  - 不改候选评分算法。
  - 不写生产数据库。
  - 不破坏旧 DB 自动迁移。
- **实现要点**：
  1. `tradeflow_candidates` schema/迁移补齐：`technical_score`、`policy_score`、`fund_flow_score`、`event_score`、`risk_penalty_score`、`data_quality_score`、`ranking_reasons_json`、`weakness_reasons_json`。
  2. `save_candidate()` INSERT/UPDATE 写入上述字段。
  3. `get_candidates()` / `get_candidates_tiered()` 从 DB 读取后保留原值，不回落到默认 0/空 list。
  4. 补 fixture：直接构造 Candidate + save_candidate 后，API 返回分项评分与原因不丢。
- **验收方式**：
  - 新增持久化测试通过。
  - V-008 中直接 save_candidate 路径可以验证分项评分真实返回。
  - 老 DB 自动补列，不需要手动删库。
- **代码标注要求**：`# [TF-PERSIST-001] split_score_persistence`

### AUTO-003: 任务建议去重与已完成任务过滤（P1）
- **描述**：M-012 生成的 `docs/task_suggestions/2026-06-25.md` 把大量已完成任务、已由 follow-up 闭环的 blocked 任务重新建议为 ready，导致任务池误报和 cron 空转风险。本任务修复 suggest_next_tasks 逻辑。
- **优先级**：P1
- **状态**：ready
- **前置条件**：M-012 完成。
- **执行约束**：
  - 不改自动开发执行链主流程，只改建议生成/过滤逻辑。
  - 不删除历史 task_suggestions，历史文件只归档。
  - 不把 proposed 自动改 ready。
- **实现要点**：
  1. 解析 `docs/TASKS.md` 顶部队列与详情段落时，统一识别 `done`、`done —`、`blocked — 已由 ... 闭环`、标题含 `✅ 已完成` 的任务，禁止再进入建议列表。
  2. 对 `blocked — NEEDS_HUMAN` 增加 follow-up 检测：若同 ID 后缀任务（如 `TF-QUALITY-001A`）和拆分任务均 done，则建议“收口状态”而不是“转 ready”。
  3. 输出建议文件时增加 `filtered_out` 摘要，列出被跳过的 done/obsolete 任务数量。
  4. 为 2026-06-25 这种重复建议 fixture 增加回归测试。
- **验收方式**：
  - dry-run 不再建议 D/E 已完成任务、TF-QUALITY-001、HK-001、TF-PERSIST-001。
  - 无 ready 时仍能生成 proposed 草案。
  - `docs/task_suggestions/YYYY-MM-DD.md` 不自动改 TASKS 状态。
- **代码标注要求**：`# [AUTO-003] task_suggestion_dedupe`

### REPORT-UX-001: TA 报告“数据不足观察”端到端回放验收（P1）
- **描述**：DATA-021 已把字段级 data_blockers 接入报告 metadata 和前端。需要用 fixture/历史样本验证“数据不足观察”不再是笼统黑盒，且不会改变强动作门禁。
- **优先级**：P1
- **状态**：done -- commit b0f4c9c
- **前置条件**：DATA-021 完成。
- **执行约束**：
  - 不调用 live API。
  - 不调用 LLM。
  - 不修改 prompts。
  - 不写生产数据库。
- **实现要点**：
  1. 构造至少 3 个报告 fixture：主力资金失败 + 龙虎榜正常无数据；公告失败 + 评级正常无数据；行情缺失 + skipped 辅助源。
  2. 通过 `report_service.create_report()` 或 API schema 回放，确认 `result_data.data_blockers` 与 response 顶层字段一致。
  3. 验证前端类型/构建不回归，报告详情能显示 `query_failed`、`normal_no_data`、`skipped`。
  4. 验证 `decision/action_label/Buy Level/Risk Level` 不因 DATA-021 metadata 被改写。
- **验收方式**：
  - 新增回放测试通过。
  - `npm run build` 通过。
  - 测试断言覆盖“主力资金失败 vs 龙虎榜正常无数据”的差异。
- **代码标注要求**：`# [REPORT-UX-001] data_blocker_replay` / `// [REPORT-UX-001] data_blocker_replay`

### IC-TA-002: investment-controller 上下文接入 TradeFlow 昊天日报与报告数据缺口（P1）
- **描述**：investment-controller v5 的职责是 TA 调度官/飞书播报官。当前只读上下文需要继续纳入 H-015 昊天主题日报和 DATA-021 报告数据缺口，帮助它决定“是否值得让 TA 出手”和“推送什么”。
- **优先级**：P1
- **状态**：done -- commit 257b14a
- **前置条件**：IC-TA-001、DATA-021、H-015 完成。
- **执行约束**：
  - 只读上下文，不触发 TA、不写交易动作。
  - 不输出强买卖词。
  - 不调用 LLM。
- **实现要点**：
  1. `investment_controller_context` 增加两个 bucket：`mandate_daily_report`、`recent_report_data_blockers`。
  2. `mandate_daily_report` 读取 `/tradeflow/mandate-daily-report/latest` 等价服务函数，不做网络调用。
  3. `recent_report_data_blockers` 汇总最近 TA 报告中 `query_failed/field_missing` 的字段和影响。
  4. 输出 `controller_hints`：哪些观察仓/候选需要 TA、哪些只进日报、哪些因数据不足不推送。
- **验收方式**：
  - endpoint fixture 包含昊天日报摘要和报告缺口摘要。
  - 只读测试证明不写 DB。
  - 不含“立即清仓/重仓买入”等强词。
- **代码标注要求**：`# [IC-TA-002] controller_context_tradeflow_report`

### V-010: 小资金试跑 v2 验收：候选收敛→观察→日报→报告缺口（P1）
- **描述**：在 TF-QUALITY、DATA-021、H-015、TRACK 系列完成后，补一条新的端到端验收，确认用户 5000 元小资金试跑链路能回答“看哪几只、为什么、何时观察、数据缺什么、盘后怎么复盘”。
- **优先级**：P1
- **状态**：done -- commit cd6e73b
- **前置条件**：V-009、DATA-021、H-015、TF-OBS-004、TRACK-007 完成。
- **执行约束**：
  - fixture/临时库，不写生产数据库。
  - 不调用 LLM/live API。
  - 不发真实通知。
- **实现要点**：
  1. 构造小型候选池 fixture：技术主候选、昊天主候选、观察候选、过滤候选各至少 1 个。
  2. 跑候选 API、Observe、Mandate Daily Report、Report data_blockers、Tracking Board v2 只读摘要。
  3. 输出 `docs/tradeflow_trial_acceptance_v3.md`，包含 5 个问题答案。
  4. 验证强词扫描、runtime_tier 不升级、无真实通知。
- **验收方式**：
  - 新增 V-010 测试通过。
  - 验收文档生成。
  - 链路能明确展示主候选不超过配置上限。
- **代码标注要求**：`# [V-010] small_cap_trial_v2_acceptance`

### NOTIFY-002: 飞书/通知草稿接入昊天日报与数据缺口摘要（P2）
- **描述**：TRACK-NOTIFY-001 已完成通知草稿 payload 与去噪规则。本任务把 H-015 昊天日报和 DATA-021 数据缺口纳入通知草稿，让用户早上/盘后能看到主题重心与数据风险，而不是只看到候选数量。
- **优先级**：P2
- **状态**：done -- commit 67d056d
- **前置条件**：TRACK-NOTIFY-001、H-015、DATA-021 完成。
- **执行约束**：
  - dry-run 生成草稿，不真实发送飞书。
  - P2/P3 信息只进日报，不触发盘中强提醒。
  - 不输出强买卖词。
- **实现要点**：
  1. 通知草稿 payload 增加 `mandate_daily_digest`：升温主题、主候选、证据缺口。
  2. 增加 `data_blocker_digest`：最近报告中失败最多的数据源和影响。
  3. 去噪：仅 P0/P1 数据失败或候选触发才进入即时草稿；普通正常无数据只进日报。
  4. 增加 dry-run markdown 示例。
- **验收方式**：
  - dry-run payload 包含昊天日报和数据缺口摘要。
  - 不真实发送。
  - 禁用词扫描通过。
- **代码标注要求**：`# [NOTIFY-002] notification_mandate_data_blockers`

### DATA-022: 主力资金/龙虎榜失败矩阵 fixture 回放（P1）
- **描述**：用户多次遇到主力资金失败、龙虎榜无数据/未触发/查询失败混淆。本任务用 fixture 矩阵回放数据源状态，确保 DATA-021 和 TA 报告不会把“正常无数据”当失败，也不会把失败当可用。
- **优先级**：P1
- **状态**：done -- commit 163e04e
- **前置条件**：DATA-021、DATA-017 完成。
- **执行约束**：
  - 不调用 live API。
  - 不修改 prompts。
  - 不写生产数据库。
- **实现要点**：
  1. 构造主力资金 `HAS_DATA/FAILED/NORMAL_NO_DATA/SKIPPED` fixture。
  2. 构造龙虎榜 `HAS_DATA/FAILED/NORMAL_NO_DATA/NOT_QUERIED` fixture。
  3. 覆盖 `infer_evidence_statuses()`、`build_data_blockers()`、`data_collector` 状态推断边界。
  4. 输出一份简短矩阵文档，说明各状态对报告动作的影响。
- **验收方式**：
  - fixture 测试通过。
  - `NORMAL_NO_DATA` 不计作查询失败。
  - `FAILED` 不被 `len > 20` 文本误判为 HAS_DATA。
- **代码标注要求**：`# [DATA-022] fund_lhb_status_matrix`

### TF-OBS-005: 非交易日候选计划到下一交易日观察语义回归（P1）
- **描述**：用户会在周末/盘后生成候选池，用下一个交易日观察。本任务固定候选生成日、计划生效日、实际观察日三者的语义，避免盘中观察显示无数据或错取日期。
- **优先级**：P1
- **状态**：done -- commit 698d0ef
- **前置条件**：TF-OBS-004 完成。
- **执行约束**：
  - 不调用 live API。
  - 不接真实交易。
  - 不修改观察触发阈值算法。
- **实现要点**：
  1. 增加周末生成、下周一观察 fixture。
  2. `observe/run` 默认按 `effective_trade_date` 或下一交易日映射候选。
  3. API 返回中显式区分 `plan_date/effective_trade_date/observe_date`。
  4. 前端观察 tab 对非交易日计划显示“用于下一交易日观察”。
- **验收方式**：
  - 周末候选在下一交易日可被 observe 读取。
  - 不再返回“无活跃候选”的误判。
  - 旧交易日 fixture 不回归。
- **代码标注要求**：`# [TF-OBS-005] observe_date_semantics` / `// [TF-OBS-005] observe_date_semantics`

### TRACK-008: 观察仓批量导入/导出 CSV 与去重合并（P1）
- **描述**：观察仓现在有手动新增，但用户常会从截图/表格/外部清单批量导入。本任务提供 CSV/文本批量导入导出与去重合并，减少重复手工维护。
- **优先级**：P1
- **状态**：done -- commit 002da18
- **前置条件**：TRACK-001、TRACK-003 完成。
- **执行约束**：
  - 不覆盖用户已有备注，除非显式 merge。
  - 不写生产数据库测试数据。
  - 不调用 OCR/LLM。
- **实现要点**：
  1. 后端新增观察仓 CSV/text parse + bulk upsert helper。
  2. 去重键使用 `symbol + horizon + source`，备注采用 append/keep 策略。
  3. 前端观察仓增加“批量导入/导出”入口。
  4. 支持字段：代码、名称、主题、入场区、失效价、理由、备注、优先级。
- **验收方式**：
  - 重复导入不产生重复记录。
  - 已有 notes 不被清空。
  - 导出再导入 round-trip 通过。
- **代码标注要求**：`# [TRACK-008] observation_bulk_import_export` / `// [TRACK-008] observation_bulk_import_export`

### REPORT-UX-002: 历史报告动作语义与数据缺口只读迁移预检（P1）
- **描述**：历史报告可能缺少 `research_direction/execution_action/action_label/data_blockers`。本任务做只读迁移预检，告诉用户哪些历史报告可以补语义、哪些需要重新跑，不直接改生产数据。
- **优先级**：P1
- **状态**：done -- commit eecfe9f
- **前置条件**：DECISION-004、DATA-021 完成。
- **执行约束**：
  - 只读扫描，不写生产 DB。
  - 不调用 LLM。
  - 不修改 prompts。
- **实现要点**：
  1. 新增历史报告 dry-run audit helper。
  2. 输出缺字段统计：动作语义缺失、data_blockers 缺失、raw_evidence 缺失。
  3. 提供建议动作：可读时补算、需要重跑、无法判断。
  4. 前端或文档显示 audit 示例。
- **验收方式**：
  - fixture 历史报告扫描通过。
  - dry-run 不写 DB。
  - 输出能解释为什么旧报告仍显示旧字段。
- **代码标注要求**：`# [REPORT-UX-002] report_semantics_audit`

### H-016: 昊天主题日报 CLI 生成与保留策略（P2）
- **描述**：H-015 已支持 API/latest。本任务补 CLI/脚本入口与保留策略，让夜间或手工可以生成 `docs/mandate_daily_reports/mandate-YYYY-MM-DD.*`，并清理过旧报告。
- **优先级**：P2
- **状态**：done -- commit cb8b2ec
- **前置条件**：H-015 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不扫描全市场。
  - 默认只读现有 TradeFlow DB/fixture。
- **实现要点**：
  1. 新增脚本或 CLI：按日期生成昊天主题日报。
  2. 支持 `--as-of`、`--window-days`、`--output-dir`、`--retention-days`。
  3. 增加 dry-run 模式，只打印摘要不写文件。
  4. 文档说明如何被 OpenClaw 或 cron 调用。
- **验收方式**：
  - fixture 能生成 md/json。
  - dry-run 不写文件。
  - retention 只清理目标目录中的旧 mandate 文件。
- **代码标注要求**：`# [H-016] mandate_daily_cli`

### IC-TA-003: investment-controller 盘前/盘后 briefing fixture dry-run（P2）
- **描述**：IC-TA-002 接入更多上下文后，需要固定盘前/盘后 briefing 的 dry-run 输出，确保 investment-controller 拿到的是流程提示和调度建议，而不是另一个 TA 结论。
- **优先级**：P2
- **状态**：done — IC-TA-003-20260626-222709
- **前置条件**：IC-TA-002 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不发送飞书。
  - 不输出强买卖词。
- **实现要点**：
  1. 构造盘前 fixture：持仓、观察仓、昊天日报、数据健康、待处理 TA。
  2. 构造盘后 fixture：今日表现、Review、报告数据缺口。
  3. 输出 briefing payload，包含待调度 TA、只进日报项、需要人工确认项。
  4. 禁用词扫描。
- **验收方式**：
  - dry-run fixture 测试通过。
  - briefing 中包含“为什么要/不要调 TA”。
  - 不出现强动作建议。
- **代码标注要求**：`# [IC-TA-003] controller_briefing_dry_run`

### TF-REVIEW-005: 盘后 Review 归因接入观察信号与模拟账本（P1）
- **描述**：盘后 Review 需要解释候选盘中是否触发、是否进入模拟账本、是否被人工确认。当前 Review 更多看候选自身，本任务接入 observe signals 与 paper ledger 归因。
- **优先级**：P1
- **状态**：done -- commit d1d90e0
- **前置条件**：TF-REVIEW-004、TF-PAPER-001 完成。
- **执行约束**：
  - 不接真实交易。
  - 不调用 LLM。
  - 不写生产数据库测试数据。
- **实现要点**：
  1. Review 读取观察信号和模拟账本状态。
  2. 输出每只候选：未触发/触发待确认/已确认/已失效/缺数据。
  3. 盘后汇总区展示“今天实际值得复盘的票”。
  4. 生成 fixture 文档片段。
- **验收方式**：
  - 有 pending/open/invalidated 三类 fixture。
  - Review 不再空白或只显示候选原始分数。
  - 不输出强买卖词。
- **代码标注要求**：`# [TF-REVIEW-005] review_observe_paper_attribution`

### UI-013: TradeFlow 空状态与下一步 CTA 文案收口（P2）
- **描述**：用户看到候选池、观察、Review、主题日报为空时，不知道该点什么。本任务统一 TradeFlow 各 tab 空状态和下一步按钮，降低试用迷路感。
- **优先级**：P2
- **状态**：ready
- **前置条件**：UI-012、H-015 完成。
- **执行约束**：
  - 不改核心业务逻辑。
  - 不增加营销式页面。
  - 不调用 API 以外的新服务。
- **实现要点**：
  1. 候选池空：提示生成候选池/导入观察仓。
  2. 观察空：提示先生成候选或加入观察仓。
  3. Review 空：提示一键生成/说明非交易日映射。
  4. 主题日报空：提示先生成候选池或 heatmap。
- **验收方式**：
  - 前端构建通过。
  - 空状态不遮挡主流程。
  - 文案不含买卖建议。
- **代码标注要求**：`// [UI-013] tradeflow_empty_state_cta`

### DATA-023: 数据源目录 API/文档同步与供应商能力矩阵导出（P2）
- **描述**：数据源能力分散在 source_catalog、docs 和报告里。用户需要知道行情/资金/龙虎榜/公告/评级/回购/研报分别来自哪里、fallback 是什么、是否实时。本任务导出统一矩阵。
- **优先级**：P2
- **状态**：ready
- **前置条件**：DATA-020、DATA-CATALOG-FIX 完成。
- **执行约束**：
  - 不调用 live API。
  - 不打印 API key。
  - 不改变 provider 路由。
- **实现要点**：
  1. 新增数据源能力矩阵导出函数/API 或文档生成脚本。
  2. 字段包括 data_type、primary_vendor、fallback_vendor、freshness、known_limits、status_semantics。
  3. 与 `MODEL_API_CATALOG` 类似，输出给 agent 可读的文档。
  4. 增加测试确保新增 data type 不会漏进矩阵。
- **验收方式**：
  - 生成文档/JSON。
  - 覆盖资金、龙虎榜、公告、评级、回购、研报。
  - 不含密钥。
- **代码标注要求**：`# [DATA-023] source_capability_matrix`

### PERF-006: 前端 bundle 体积趋势记录与懒加载候选建议（P2）
- **描述**：前端构建长期提示 bundle 超 500KB。PERF-005 只记录预算，本任务补体积趋势记录和低风险 code-split 建议，不强制拆大模块。
- **优先级**：P2
- **状态**：ready
- **前置条件**：PERF-005 完成。
- **执行约束**：
  - 不做大规模前端重构。
  - 不改变路由行为。
  - 不让性能测试依赖绝对耗时。
- **实现要点**：
  1. 解析 `npm run build` 输出，记录 js/css gzip 尺寸。
  2. 生成 `docs/perf/frontend_bundle_report.md`。
  3. 给出懒加载候选：Reports、TradeFlow、TrackingBoard、Charts。
  4. 测试只验证解析逻辑和报告格式。
- **验收方式**：
  - fixture build output 可解析。
  - 报告生成。
  - 不要求实际 code split。
- **代码标注要求**：`# [PERF-006] frontend_bundle_trend`

### V-011: 夜间自动开发日报验收与 ready 队列续航检查（P2）
- **描述**：自动开发跑完后需要稳定回答“昨晚做了什么、失败在哪、明晚还有多少任务”。本任务验收 auto_dev_reports / DEVLOG / task_runs 的一致性。
- **优先级**：P2
- **状态**：ready
- **前置条件**：AUTO-003 完成。
- **执行约束**：
  - 不调用 OpenCode。
  - 不修改历史 run 内容，只做读取和报告。
  - 不发送通知。
- **实现要点**：
  1. 扫描最近 24 小时 commits、task_runs、reviews。
  2. 检查每个 done task 是否有 run archive/review/DEVLOG 记录。
  3. 输出 ready 队列剩余数量和预计可跑时长。
  4. 生成 `docs/auto_dev_reports/YYYY-MM-DD.md`。
- **验收方式**：
  - fixture/本地数据能生成日报。
  - ready 队列为空时给出明确提示。
  - 不误改 TASKS 状态。
- **代码标注要求**：`# [V-011] nightly_auto_dev_acceptance`

### NOTIFY-003: 通知去噪规则回放测试与日报/盘中分层验收（P2）
- **描述**：NOTIFY-002 接入更多摘要后，需要保证不会把普通数据缺口、正常无数据、观察项全部推成盘中提醒。本任务做通知分层回放测试。
- **优先级**：P2
- **状态**：ready
- **前置条件**：NOTIFY-002 完成。
- **执行约束**：
  - dry-run，不真实发送。
  - 不调用 LLM。
  - 不输出强动作词。
- **实现要点**：
  1. 构造 P0/P1/P2/P3 通知 fixture。
  2. 验证 P0/P1 可即时草稿，P2/P3 只进日报。
  3. NORMAL_NO_DATA 只进日报摘要，不盘中打扰。
  4. 输出去噪回放报告。
- **验收方式**：
  - 通知分层测试通过。
  - dry-run payload 可读。
  - 禁用词扫描通过。
- **代码标注要求**：`# [NOTIFY-003] notification_noise_replay`

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
- **状态**：done — commit 2e0c946
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

### T-008: TradeFlow 观察信号 fixture 回放与前端状态一致性验收（P2）
- **描述**：在不启用真实盘中盯盘的情况下，用 fixture 回放验证 Observe 状态机、signals 落库、API 和前端显示一致，避免候选池有票但盘中观察页空白或状态断裂。
- **优先级**：P2
- **状态**：done（2026-06-08，commit f1bfd54 + 056a072）
- **前置条件**：`TF-OBS-001`、`UI-004` 完成 ✓。
- **执行约束**：
  - 不启动真实 scheduler。
  - 不拉 live 行情。
  - 不调用 TA/LLM。
  - 不输出强买卖词。
- **实现要点**：
  1. 构造 Observe fixtures：
     - 未触发。
     - 达到触发价。
     - 跌破失效价。
     - 行情 stale。
     - 非交易日计划生效到下一交易日。
  2. 回放后验证：
     - `observe_state`
     - `tradeflow_signals`
     - `current_price`
     - `trigger_reason`
     - `data_status`
  3. API 和前端使用同一状态枚举，避免后端有状态、前端显示空。
  4. 与 TF-DATE-001 的 `plan_date/effective_trade_date/observe_date` 语义保持一致。
- **验收方式**：
  - 5 类 fixture 状态稳定。
  - 前端 Observe Tab 能显示 replay 后状态。
  - `pytest tests/test_m005_intraday_observe.py tests/test_ui001_tradeflow_api.py tests/test_tradeflow_*.py -q` 通过；`npm run build` 通过。
- **代码标注要求**：`# [T-008] observe_fixture_replay` / `// [T-008] observe_fixture_replay`

### T-004: TradeFlow P2 盘中 Observe
- **描述**：对候选池做盘中低频触发检查，发现突破触发价、跌破失效价、异常放量等事件。
- **优先级**：P1（本轮提升；用户反馈盘中观察无数据/需手动执行，阻塞 TradeFlow 日常使用）
- **状态**：done — commit 6b9cb02
- **前置条件**：`TF-OBS-001`、`T-008` 完成 ✓。
- **实现要点**：
  - 默认静默，只在触发条件满足时记录 signal。
  - 先不自动推送飞书，不自动调用 TA。
  - 每日触发次数设置上限，避免噪声。
- **验证方式**：
  - 未触发时不输出提醒。
  - 触发信号包含原始价量证据。

### T-005: TradeFlow P3 盘后 Review
- **描述**：复盘候选池信号是否有效，记录命中率、误报率、继续观察/移除理由。
- **优先级**：P1（本轮提升；用户反馈盘后 Review 无数据，阻塞候选池质量闭环）
- **状态**：done — commit 6b9cb02
- **前置条件**：`M-007`、`T-008`、`V-005` 完成 ✓。
- **验证方式**：
  - 每日可输出候选复盘表。
  - 对失效候选给出明确移除理由。
- **代码标注要求**：`# [T-005] review_fixture_replay`

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

### VLM-001: 自选截图解析 v2 — 候选表格识别（P1）
- **描述**：升级 VLM 截图解析，支持识别"自选候选表格"（含核心业务、所属板块、利好度、启动时间、周期、共识度），解析结果写入 watchlist_items.notes。
- **优先级**：P1
- **状态**：done
- **前置条件**：现有 VLM 链路可用（TA_VLM_* 环境变量）
- **现有链路**：`Portfolio.tsx` → `POST /v1/portfolio/parse-image` → `vlm_position_parser.py` → `vlm_service.py`
- **实现要点**：
  1. 新增 `WATCHLIST_TABLE_PROMPT`，专门识别"自选候选表格"格式，字段：symbol/name/business/sector/bullish_score/startup_eta/holding_period/consensus
  2. 新增 `parse_watchlist_table_image()` 函数，返回带 notes 的结构化数据
  3. notes 格式：`半导体设备｜刻蚀/沉积设备｜利好9.3｜启动几天｜周期1月｜共识92`
  4. 前端上传后展示"识别到 N 只，备注预览"，确认后批量添加
  5. 批量添加自选时支持 `{ symbol, notes }`，自动写入 notes 字段
- **验收方式**：
  - 上传半导体分析表格截图，返回 7 条记录（6 股 + 1 ETF）
  - 每条记录包含 symbol/name/business/sector/bullish_score/startup_eta/holding_period/consensus/notes
  - notes 格式正确：`板块｜业务｜利好X｜启动X｜周期X｜共识X`
  - 前端预览正确，确认后批量添加到 watchlist 并写入 notes
  - `npm run build` 零错误
  - `pytest tests/test_vlm*.py -q` 通过
- **代码标注要求**：`# [VLM-001] watchlist_table_parser`
- **完成记录**：2026-05-31, commit f52aa21
