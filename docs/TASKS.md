# 任务池

> 最后更新：2026-07-22

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

### 当前执行队列（2026-07-22）

> 本队列只放当前产品主线；下面的历史任务总表不代表自动领取顺序。

> **执行前置**：FUND-001A、FUND-003A 与 FUND-003A-B 已完成确定性补修和 Codex 多轮对抗复审。工作区恢复干净后，从 FUND-004B 开始按依赖串行自动领取；自动循环继续遵守 dirty-tree 停止规则。

1. `FUND-001A`：生产公司画像契约与 provider 格式补修（P0，done，人工实现与 Codex 对抗复审通过）。
2. `FUND-003A`：财务因果声明与官方证据逐项绑定（P0，done；由 `2d98abc` 完成复审收口）。
3. `FUND-003A-B`：因果证据按术语/出现位置绑定补修（P0，done，`2d98abc`）。
4. `FUND-004B`：C-006 同日期/同期间口径计算补修（P0，ready；前置依赖已闭环）。
5. `FUND-004A`：基本面语义门禁前移并剔除无效研究权重（P0，done；实现完成，460 项回归通过）。
6. `FUND-005A`：逐 Agent 真实模型与运行时 trace 补修（P1，blocked-auto；等待 FUND-004A done → 可领取）。
7. `FUND-006A`：真实 provider 格式与生产图离线回放验收（P1，done，FUND-006A-20260722-205540）。
8. `FUND-007A`：财报错误模式基准集与持续回归报告（P2，done，FUND-007A-20260722-213159）。
9. `FUND-001~006`：第一版实现（blocked-review；329 项专项/回归测试通过，但存在 4 个 P1，等待上述补修链闭环后统一验收）。
10. `SCORE-001`：TA 只读 `research_score_snapshot` v1.1.0 loader（P1，done，commit 87dcd44 + 24881f6，73 tests passed）。
11. `SCORE-001B`：研究快照接入 KB-020 聚合响应与 TradeFlow candidate detail（P1，done，SCORE-001B-20260723-022706）。
12. `SCORE-002`：复用 TradeFlow 现有因子生成 `entry_timing` 评分卡（P1，done，SCORE-002-20260723-023857）。
13. `SCORE-003`：账户上下文 `portfolio_fit` 评分卡（P1，blocked-auto；等待 SCORE-001）。
14. `SCORE-004`：八类硬性否决统一门禁（P0，blocked-auto；等待 SCORE-002/SCORE-003）。
15. `SCORE-005`：四卡结果到七阶段与动作语义的确定性映射（P1，blocked-auto；等待 SCORE-004）。
16. `SCORE-006`：研究评分接入端到端对抗回放（P1，blocked-human；等待 SCORE-005 与 ZCode 正式快照人工确认）。
17. `AUTO-007`：自动开发依赖感知领取与阻塞任务自动解锁（P1，done；最终补修 `623f45c`）。
18. `KB-019`：ZCode 研报增量摄取清单与重复导入预检（P2，blocked — NEEDS_HUMAN；等待专项收口）。
19. `HY-009`：半年报增量刷新、缓存失效与事实冲突审计（P2，blocked；等待释放）。
20. `V-014`：真实本地知识库只读 smoke 与研报主线验收日报（P2，blocked；等待 HY-009）。
21. `UI-014`：TA 研报证据中心与来源下钻（P2，blocked；等待 API 契约人工确认）。
22. `V-015`：研报增量摄取→证据 API→前端→待更新清单端到端验收（P2，blocked；等待 UI-014/HY-010）。
23. `PLAYBOOK-002`：计划仓位上限与三笔法规则引擎（P1，blocked，战略暂停）。

> FUND 补修链优先级高于 SCORE 与 UI。只有 FUND-006A 离线生产图回放通过后，才能由用户人工确认发起一次 live 603629 验收；模型更换与 A/B 测试不得替代确定性门禁。任一补修任务出现 P0/P1 finding 时停止后续任务。

### 今晚 4 小时执行包（2026-07-22）

> 目标预算约 200-265 分钟。按下列顺序串行执行，不并发修改共享基本面链路。每项必须完成“实现 → 定向测试 → 相关回归 → Codex review → 精确提交 → 状态回写”后才可解锁下一项。Codex review 出现 P0/P1/P2 correctness finding、测试失败、工作区意外变脏或任务超时，立即停止整批并进入人工处理；不得带病跳到下一项。

| 顺序 | 任务 | 预计耗时 | 本轮放行条件 |
|---|---|---:|---|
| 1 | `FUND-003A-B` | done | `2d98abc`；72 项专项、271 项聚焦回归通过，复审无 P0/P1/P2 |
| 2 | `FUND-004B` | 30-40 分钟 | C-006 只使用同日期、同期间、同单位指标组；缺组时 fail closed |
| 3 | `FUND-004A` | 40-50 分钟 | 基本面语义门禁在 Bull/Bear 前执行；无效叙事不参与研究权重或记忆 |
| 4 | `FUND-005A` | 25-35 分钟 | trace 记录真实 actual model/fallback/耗时；未知时写 unknown，不伪装 requested model |
| 5 | `FUND-006A` | 40-50 分钟 | 真实 provider fixture + 完整生产图离线回放通过；独立 review 无 P0/P1/P2 correctness finding |
| 6 | `FUND-007A` | 30-40 分钟 | 形成可重复执行的财报错误模式基准集、机器结果和人读回归报告；不得把一次性样本写死为交易结论 |

> **夜间终点**：只完成离线确定性修复、基准沉淀和验收，不调用 live LLM、不写生产数据库、不自动重跑 603629。即使 4 小时内全部通过，真实 603629 验收仍保持 `blocked-human`，等待孟白天确认 provider、模型和预计调用次数。

#### FUND-003A-B 补修验收口径

- 证据的 `direction`/`negation`/因果关系必须属于具体术语及其出现位置，禁止挂在整条 entry 上被其他指标复用。
- 同一指标多次出现时不得只读第一次；存在相反方向或语义不明时 fail closed。
- 因果同义词必须同时应用于证据侧和 claim 侧；`采购成本下降` 可支持 `原材料成本下降`，但不得只因同时出现“原材料”就证明任意因果。
- `claim_id` 必须确定性且能区分同一报告内同指标的多条声明，不得只对 keyword 做 hash。
- 每个 `evidence_id` 的审计片段必须包含它所支持的具体术语、方向与否定信息。
- 必测对抗样例：中性首次出现+后文上涨；`未采用净额法，仍采用总额法`；`原材料下降+合同负债增加`；同指标多条相反声明；多术语长证据片段。
- 完成标准：专项与相关回归全绿，`codex review --uncommitted` 无 P0/P1/P2 correctness finding；不调 live LLM，不写生产数据库。


### F-001: TA研报执行层修复（P0-P2共10项）
- **描述**：修复002837英维克报告暴露的10个问题，涉及止损价解析、估值sanity check、Opportunity Score封顶、未持仓语义、Buy/Risk Level冲突、舆情VERDICT矛盾、入场区间误识别、打法标签、A股做空措辞、Evidence Coverage保守化
- **优先级**：P0
- **状态**：done -- commit 4e4088b
- **预计耗时**：60-90 分钟
- **depends_on**：无
- **auto_release**：true
- **验收方式**：重跑002837报告，10项全部通过

### PLAYBOOK-002: 计划仓位上限与上车三笔法规则引擎（P1）
- **描述**：把"计划最大仓位 + 试错仓/确认仓/进攻仓"写成可测试规则，禁止系统因为下跌简单提示补仓。
- **优先级**：P1
- **状态**：done -- commit 9cb66f0
- **预计耗时**：40-50 分钟
- **depends_on**：PLAYBOOK-001
- **auto_release**：true
- **验收方式**：fixture覆盖可试错/不可试错/可加确认仓/不可加确认仓/回踩进攻/突破进攻/跌停不补。"跌了/便宜/回调"单独出现不能触发补仓。
- **代码标注要求**：`# [PLAYBOOK-002] staged_entry_rules`

### SCORE-001: TA 只读 research_score_snapshot 契约与安全接入（P1）
- **描述**：建立 TA 侧只读快照 loader/provider，使 ZCode 发布的 research_score_snapshot v1.1.0 可被稳定读取和降级。本任务不接 API/前端/TradeFlow，不重新计算知识库分数，也不接受知识库给出的交易动作。
- **优先级**：P1
- **状态**：done -- commit c6f6ea7
- **预计耗时**：35-45 分钟
- **depends_on**：KB-001, KB-014
- **auto_release**：true
- **验收方式**：fixture覆盖五类状态、完整/缺字段、过期、损坏、symbol错配。断言drafts不会被生产loader选中；同标的多版本严格按analysis_time选择。

### B-001: 接入小米 MiMo 模型到 TA 系统
- **描述**：在 llm_clients/ 中添加小米 MiMo 适配器，支持 mimo-v2.5 和 mimo-v2.5-pro
- **优先级**：高
- **状态**：ready
- **预计耗时**：30-40 分钟
- **depends_on**：无
- **auto_release**：true

### C-001: position_validation_gate（P2）
- **描述**：每份报告生成前必须读取 current_positions.json，根据持仓状态决定可输出的动作类型
- **优先级**：P2
- **状态**：ready
- **预计耗时**：40-50 分钟
- **depends_on**：无
- **auto_release**：true

### C-003: 禁止做空策略输出（P1）
- **描述**：如果 can_short=false，做空相关策略在生成阶段就不进入候选池
- **优先级**：P2
- **状态**：ready
- **预计耗时**：25-35 分钟
- **depends_on**：无
- **auto_release**：true

### C-004: 动作枚举重设计（P1）
- **描述**：将最终动作枚举精简为 5 个
- **优先级**：P2
- **状态**：ready
- **预计耗时**：30-40 分钟
- **depends_on**：无
- **auto_release**：true

### C-005: same_symbol_delta_check（P2）
- **描述**：同一股票结论翻转时，必须输出对比信息
- **优先级**：P2
- **状态**：ready
- **预计耗时**：25-35 分钟
- **depends_on**：无
- **auto_release**：true

### C-006: financial_data_validator — 分期实现（P2）
- **描述**：财报数据异常检测，分两期做
- **优先级**：P2
- **状态**：ready
- **预计耗时**：40-50 分钟
- **depends_on**：无
- **auto_release**：true

### C-007: event_risk_gate（P1）
- **描述**：重大事件发生时，进入风控优先模式
- **优先级**：P2
- **状态**：ready
- **预计耗时**：30-40 分钟
- **depends_on**：无
- **auto_release**：true

### C-008: execution_readiness_score — 简化版（P2）
- **描述**：每份报告输出两个核心质量指标
- **优先级**：P2
- **状态**：ready
- **预计耗时**：25-35 分钟
- **depends_on**：无
- **auto_release**：true

### B-002: 定时任务与 OpenClaw 联动
- **描述**：定时分析完成后自动通知 OpenClaw，由主控 AI 决定是否推送到飞书
- **优先级**：中
- **状态**：ready
- **预计耗时**：30-40 分钟
- **depends_on**：无
- **auto_release**：true

### B-003: 研报导出为飞书文档
- **描述**：分析结果支持导出为飞书云文档，方便分享和存档
- **优先级**：中
- **状态**：ready
- **预计耗时**：35-45 分钟
- **depends_on**：无
- **auto_release**：true

### B-004: 持仓快照与 investment-controller 同步
- **描述**：TA 系统的持仓数据与 investment-controller 项目的 current_holdings.json 双向同步
- **优先级**：中
- **状态**：ready
- **预计耗时**：30-40 分钟
- **depends_on**：无
- **auto_release**：true

### HY-009: 半年报增量刷新、缓存失效与事实冲突审计（P2）
- **描述**：让 HY-003 半年报事实索引能识别新披露、修订稿和知识库页面更新，安全刷新缓存并标记跨版本事实冲突。
- **优先级**：P2
- **状态**：ready
- **预计耗时**：40-50 分钟
- **depends_on**：HY-003, HY-005, HY-008
- **auto_release**：true
- **验收方式**：fixture覆盖新增、修订、删除、缓存损坏、同周期冲突和无变化六类。重复执行幂等；无变化不重建全量索引。
- **代码标注要求**：`# [HY-009] half_year_incremental_refresh`

### M-009: TradeFlow 前端观察池面板（P2）
- **描述**：在前端增加 TradeFlow 观察池/计划展示：候选、策略标签、触发价、失效价、过滤原因、是否需要 TA。
- **优先级**：P2
- **状态**：ready
- **预计耗时**：35-45 分钟
- **depends_on**：M-003, M-005
- **auto_release**：true

### M-010: 飞书/通知链路人工确认版（P2）
- **描述**：把夜间日报、盘中触发、盘后复盘接入飞书，但第一阶段只生成草稿/本地预览，人工确认后再发。
- **优先级**：P2
- **状态**：ready
- **预计耗时**：35-45 分钟
- **depends_on**：无
- **auto_release**：true

### UI-014: TA 研报证据中心与来源下钻（P2）
- **描述**：在报告查看体验中增加轻量"研报证据"入口，让用户看到同股研报共识、分歧、半年报事实、待验证项和来源路径。
- **优先级**：P2
- **状态**：ready
- **预计耗时**：40-50 分钟
- **depends_on**：KB-011, KB-020, REPORT-UX-005
- **auto_release**：true
- **验收方式**：前端类型检查与build通过。组件测试覆盖完整/空/部分失败/冲突四态。
- **代码标注要求**：`// [UI-014] research_evidence_center`


### 历史任务总表（按创建顺序）

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
104. `AUTO-003`：任务建议去重与已完成任务过滤（P1，done — commit 62f0d46，修复 M-012 误把 done/blocked-resolved 任务重复建议）。
105. `REPORT-UX-001`：TA 报告“数据不足观察”端到端回放验收（P1，done — commit b0f4c9c，依赖 DATA-021 ✓）。
  106. `IC-TA-002`：investment-controller 上下文接入 TradeFlow 昊天日报与报告数据缺口（P1，done，依赖 IC-TA-001/DATA-021/H-015 ✓）。
107. `V-010`：小资金试跑 v2 验收：候选收敛→观察→日报→报告缺口（P1，done，依赖 V-009/DATA-021/H-015 ✓）。
108. `NOTIFY-002`：飞书/通知草稿接入昊天日报与数据缺口摘要（P2，done — NOTIFY-002-20260626-214133，依赖 TRACK-NOTIFY-001/H-015/DATA-021 ✓）。
109. `DATA-022`：主力资金/龙虎榜失败矩阵 fixture 回放（P1，done，依赖 DATA-021/DATA-017 ✓）。
110. `TF-OBS-005`：非交易日候选计划到下一交易日观察语义回归（P1，done，依赖 TF-OBS-004 ✓）。
 111. `TRACK-008`：观察仓批量导入/导出 CSV 与去重合并（P1，done，依赖 TRACK-001/TRACK-003 ✓）。
112. `REPORT-UX-002`：历史报告动作语义与数据缺口只读迁移预检（P1，done — commit eecfe9f，依赖 DECISION-004/DATA-021 ✓）。
113. `H-016`：昊天主题日报 CLI 生成与保留策略（P2，done — commit cb8b2ec，依赖 H-015 ✓）。
  114. `IC-TA-003`：investment-controller 盘前/盘后 briefing fixture dry-run（P2，done，依赖 IC-TA-002 ✓）。
115. `TF-REVIEW-005`：盘后 Review 归因接入观察信号与模拟账本（P1，done，依赖 TF-REVIEW-004/TF-PAPER-001 ✓）。
116. `UI-013`：TradeFlow 空状态与下一步 CTA 文案收口（P2，done，依赖 UI-012/H-015 ✓）。
117. `DATA-023`：数据源目录 API/文档同步与供应商能力矩阵导出（P2，done — commit defb365，依赖 DATA-020/DATA-CATALOG-FIX ✓）。
118. `PERF-006`：前端 bundle 体积趋势记录与懒加载候选建议（P2，done，依赖 PERF-005 ✓）。
119. `V-011`：夜间自动开发日报验收与 ready 队列续航检查（P2，done — commit 6f585c3，依赖 AUTO-003 ✓）。
120. `NOTIFY-003`：通知去噪规则回放测试与日报/盘中分层验收（P2，done，依赖 NOTIFY-002 ✓）。
  121. `TRACK-009`：跟踪看板持仓导入入口与 OpenClaw holdings 契约对齐（P1，done，依赖 TRACK-003/TRACK-007 ✓）。
122. `TRACK-010`：跟踪看板盘后复盘乱码与编码/渲染回归（P1，done，依赖 TRACK-005/TRACK-007 ✓）。
  123. `TF-QUALITY-005`：候选池“过多且像抄底”回放校准与强度分层（P1，done — OpenCode 2026-06-29，7269 passed/0 failed，依赖 TF-QUALITY-004/H-014 ✓）。
124. `H-017`：昊天左侧候选证据包：政策-产业-公司三层链路（P1，done，依赖 H-015/H-016 ✓）。
  125. `DATA-024`：主力资金供应商 fallback live-smoke dry-run 与错误归因报告（P1，done — DATA-024-20260629-231953，依赖 DATA-022/DATA-023 ✓）。
126. `REPORT-UX-003`：报告最终结论“数据不足观察”原因分解与前端显示（P1，done — REPORT-UX-003-20260630-180010，依赖 REPORT-UX-001/REPORT-UX-002 ✓）。
127. `TF-OBS-006`：盘中观察自动执行入口与红涨绿跌视觉语义修正（P2，done，依赖 TF-OBS-005/UI-013 ✓）。
128. `IC-TA-004`：investment-controller 飞书 briefing payload 与 TA 调度闭环验收（P2，done，依赖 IC-TA-003/NOTIFY-003 ✓）。
129. `V-012`：5000 元小资金试跑 5 日回放验收与人工操作手册 v3（P2，done，依赖 V-010/V-011 ✓）。
130. `AUTO-004`：夜间三小时任务续航预算与失败后停止策略回归（P2，done — AUTO-004-20260702-231123，依赖 AUTO-003/V-011 ✓）。
131. `KB-001`：Tree Work 本地知识库只读索引与健康审计（P1，done — OpenCode 产出 `tradingagents/dataflows/local_knowledge_audit.py` + `scripts/audit_local_knowledge.py` + `tests/test_kb001_local_knowledge_audit.py` + `docs/knowledge_reports/local_knowledge_audit-2026-07-01.md`，47 tests passed）。
132. `KB-002`：investment wiki 输出协议升级：TA 可消费字段 lint（P1，done — OpenCode 产出 `tradingagents/dataflows/local_knowledge_lint.py` + `scripts/lint_local_knowledge.py` + `docs/local_knowledge_contract.md` + `tests/test_kb002_local_knowledge_lint.py`（52 tests）+ 真实知识库 lint 报告；99 tests passed（KB-001+KB-002））。
133. `KB-003`：TA 本地知识源 raw_evidence 接入与报告“本地知识补充”区块（P1，done — OpenCode 产出 `tradingagents/dataflows/local_knowledge_provider.py` + `scripts/query_local_knowledge.py` + `tests/test_kb003_local_knowledge_provider.py`（83 tests）+ 接入 `data_collector.build_raw_evidence` / `evidence_contract` / `report_service.attach_report_local_knowledge` / `api/main.py` 响应顶层；7586 tests passed，无回归）。
134. `KB-004`：TradeFlow 昊天候选接入本地知识命中分与证据摘要（P1，done — commit 1b94c70，OpenCode 产出 `compute_local_knowledge_score` / `needs_tree_work_research` + `MandateEvidencePacket.local_knowledge_summary` + TradeFlow candidate/frontend enrichment + `tests/test_kb004_tradeflow_knowledge_score.py`（61 tests），KB/H 系列相关回归 222 passed）。
135. `KB-005`：Tree Work inbox/raw/wiki 对齐与未消化研报清单（P2，done — OpenCode 产出 `tradingagents/dataflows/tree_work_backlog.py` + `scripts/tree_work_backlog.py` + `tests/test_kb005_tree_work_backlog.py`（39 tests）+ `docs/knowledge_reports/tree_work_ingest_backlog-2026-07-01.md`，KB 系列 338 tests passed）。
136. `KB-006`：本地知识库查询 API 与 investment-controller 只读上下文接入（P2，done — OpenCode 产出 `api/services/local_knowledge_context_service.py` + `GET /v1/knowledge/local/search` + IC context `local_knowledge_hits` bucket + `controller_hints.research_review` lane + `tests/test_kb006_local_knowledge_context_api.py`（65 tests），依赖 KB-003/IC-TA-004 ✓）。
137. `KB-007`：多研报重复提及因子 Research Attention Score（P1，done — 实现完成，待外层 commit）。
138. `KB-008`：TA/TradeFlow 接入研报关注度与主题交叉度展示（P1，done — 实现完成，待外层 commit）。
139. `DATA-025`：免费研报来源目录与 Eastmoney/AKShare 研报源 smoke（P2，done — DATA-025-20260701-202937，依赖 DATA-011/DATA-023 ✓）。
140. `KB-009`：研报来源去重、时效衰减与过热惩罚规则（P2，done — OpenCode 产出 `tradingagents/dataflows/research_attention_decay.py` + `tests/test_kb009_research_attention_decay.py`（45 tests），依赖 KB-007 ✓）。
141. `KB-010`：本地知识索引缓存与 freshness manifest（P1，done — OpenCode 产出 `tradingagents/dataflows/local_knowledge_cache.py` + `scripts/query_local_knowledge.py` 缓存参数 + `tests/test_kb010_local_knowledge_cache.py`（53 tests），依赖 KB-003/KB-007/KB-009 ✓）。
142. `KB-011`：本地知识/研报关注度前端与 API 契约回归（P1，done — OpenCode 产出 `frontend/src/utils/knowledgeContract.ts` + `tests/test_kb011_knowledge_contract_ui.py`（14 tests）+ `frontend/src/utils/kb011KnowledgeContract.test.ts`（26 tests），修复 `ReportResponse` setattr/serialize 静默丢失 + 观察仓 KB schema 缺失 + 前端 research_attention 类型与渲染缺口，依赖 KB-008/KB-009 ✓）。
143. `DATA-026`：生产库测试污染健康检查与 scheduler 启动告警（P1，done — commit dfc4460，2026-07-06 范围补审通过并完成 P2 补修，依赖 DB 污染治理脚本 ✓）。
144. `AUTO-005`：自动开发前置检查接入 DB hygiene 与续航门禁（P1，done — commit 553366b + 8a58462，依赖 AUTO-004/DATA-026 ✓）。
 145. `REPORT-UX-004`：本地知识补充区块历史报告回放验收（P1，done — REPORT-UX-004-20260705-031930，依赖 KB-003/KB-008/REPORT-UX-003 ✓）。
 146. `TF-KB-001`：TradeFlow 本地知识分校准回放与弱候选防提升（P1，done，依赖 KB-004/KB-009 ✓）。
147. `KB-012`：Tree Work 研报补录任务包导出（P2，done — commit 53cebbc，2026-07-06 范围补审通过，依赖 KB-002/KB-005/KB-007 ✓）。
  148. `V-013`：Tree Work → TA → TradeFlow → investment-controller 知识链路验收（P2，done — V-013-20260705-040318，依赖 KB-006/TF-KB-001 ✓）。
149. `HY-001`：半年报 Tree Work 输出协议扩展与 lint 规则（P1，done，依赖 KB-002/KB-012 ✓）。
150. `HY-002`：半年报资料优先队列与 Tree Work 补录任务包（P1，done，依赖 HY-001/KB-005/KB-012 ✓）。
151. `HY-003`：半年报事实表本地索引与只读查询 provider（P1，done，依赖 HY-001/KB-010 ✓）。
152. `HY-004`：TA 报告接入“半年报事实对照”区块（P1，done — HY-004-20260711-225740，44 tests passed，依赖 HY-003/KB-003/REPORT-UX-004 ✓）。
153. `HY-005`：旧研报观点 vs 半年报事实反证检测（P1，done — HY-005-20260711-232608，76 tests passed，依赖 HY-003/KB-007/KB-009 ✓）。
154. `HY-006`：TradeFlow/昊天候选接入半年报因子与降权规则（P1，done — HY-006-20260711-233936，46 tests passed，依赖 HY-003/H-017/KB-004 ✓）。
155. `HY-007`：investment-controller 半年报 briefing payload 与去噪规则（P2，done — base commit 5463b16，P2 补修已通过最终 Codex review）。
156. `HY-008`：半年报知识链路端到端回放验收（P2，done — HY-008-20260713-190756，24 tests passed，依赖 HY-004/HY-005/HY-006/HY-007 ✓）。
157. `AUTO-006`：Codex review 超时 watchdog 与收口策略（P1，done — commit f06793e + b638dca，依赖 AUTO-004 ✓）。
158. `KB-013`：半年报 fixture 样本集与契约回放基线（P1，done，依赖 KB-002/KB-012 ✓）。
159. `KB-014`：研报/财报来源可信度分层与 citation policy（P1，done — OpenCode 产出 `tradingagents/dataflows/citation_policy.py` + 6 类 `source_quality_tier` + 3 条 CIT- lint 规则 + provider/cache 软降级 + `tests/test_kb014_citation_policy.py`（91 tests）+ `docs/citation_policy.md`；KB/HY/V013 系列 1075 passed，Codex review 补修边界后 KB 组合回归 581 passed，依赖 DATA-025/KB-002 ✓）。
160. `DATA-027`：免费研报/公告/半年报源 smoke 扩展与失败归因（P2，done — commit 559e95b + 4e7f5d3，103 tests passed，依赖 DATA-023/DATA-025 ✓）。
161. `REPORT-UX-005`：本地知识补充不覆盖动作语义的扩展回放（P2，done — 待外层 commit，依赖 REPORT-UX-004/KB-003 ✓）。
162. `PLAYBOOK-001`：上车—在车上—下车战法字段契约与 API 读写闭环（P1，done — Codex review PASS，304 focused tests passed）。
163. `PLAYBOOK-002`：计划仓位上限与上车三笔法规则引擎（P1，blocked — 战略暂停，待研报/半年报主线完成后人工释放）。
164. `PLAYBOOK-003`：持仓拆分与机动仓/核心仓/防守仓规则引擎（P1，blocked — 等 PLAYBOOK-001/PLAYBOOK-002 完成）。
165. `PLAYBOOK-004`：TA 报告动作语义接入战法阶段与仓位建议（P1，blocked — 等 PLAYBOOK-002/PLAYBOOK-003 完成）。
166. `PLAYBOOK-005`：跟踪看板战法字段前端展示与观察仓导入映射（P2，blocked — 等 PLAYBOOK-001/PLAYBOOK-004 完成）。
167. `PLAYBOOK-006`：战法回放验收与利通电子样例 fixture（P2，blocked — 等 PLAYBOOK-004/PLAYBOOK-005 完成）。
168. `KB-015`：研报观点事实分离与 TA 可消费摘要索引（P1，done — OpenCode 产出 `tradingagents/dataflows/research_fact_opinion_index.py` + `scripts/research_fact_opinion_index.py` + `tests/test_kb015_research_fact_opinion_index.py`（54 tests）+ `docs/knowledge_reports/research_fact_opinion_index-2026-07-11.md`，依赖 KB-014/KB-003/HY-001 ✓）。
169. `KB-016`：多研报一致性/分歧矩阵与关注度去重回放（P1，done — commit 7e1c6af，依赖 KB-015/KB-007/KB-009 ✓）。
170. `KB-017`：研报观点 vs 公告/半年报事实 citation 审计（P1，done — commit 0f8789b，依赖 KB-015/HY-003/KB-014 ✓）。
171. `KB-018`：同股研报观点版本演化与共识漂移时间线（P1，done — commit 0879941，依赖 KB-016/KB-017 ✓）。
172. `HY-009`：半年报增量刷新、缓存失效与事实冲突审计（P2，blocked，等待 HY-008）。
173. `V-014`：真实本地知识库只读 smoke 与研报主线验收日报（P2，blocked，等待 HY-009）。
174. `SCORE-001`：TA 只读 `research_score_snapshot` v1.1.0 loader 与安全契约（P1，done，commit 87dcd44 + 24881f6）。
175. `SCORE-001B`：研究快照接入 KB-020 聚合响应与 TradeFlow candidate detail（P1，done，SCORE-001B-20260723-022706）。
176. `SCORE-002`：复用 TradeFlow 现有因子生成 `entry_timing` 评分卡（P1，done，SCORE-002-20260723-023857）。
177. `SCORE-003`：账户上下文 `portfolio_fit` 评分卡（P1，blocked-auto，依赖 SCORE-001）。
178. `SCORE-004`：八类硬性否决统一门禁（P0，blocked-auto，依赖 SCORE-002/SCORE-003）。
179. `SCORE-005`：四卡结果到七阶段与动作语义的确定性映射（P1，blocked-auto，依赖 SCORE-004/PLAYBOOK-001）。
180. `SCORE-006`：研究评分接入端到端对抗回放（P1，blocked-human，依赖 SCORE-005 与 ZCode 正式发布的真实快照）。
181. `FUND-001`：标的身份与公司画像硬门禁（P0，blocked-review，第一版待 FUND-001A 补修）。
182. `FUND-002`：财务期间口径结构化与单季度确定性计算（P0，blocked-review，第一版待 FUND-004B 补修）。
183. `FUND-003`：财务异动官方解释与会计口径证据上下文（P0，blocked-review，第一版待 FUND-003A 补修）。
184. `FUND-004`：基本面语义质量门禁与 C-006 真实接线（P0，blocked-review，第一版待 FUND-004A/FUND-004B 补修）。
185. `FUND-005`：逐 Agent 模型与输入契约脱敏 trace（P1，blocked-review，第一版待 FUND-005A 补修）。
186. `FUND-006`：603629 与跨行业财报对抗回放验收（P1，blocked-review，第一版待 FUND-006A 重验）。
187. `FUND-001A`：生产公司画像契约与 provider 格式补修（P0，done，人工实现与 Codex 对抗复审通过）。
188. `FUND-003A`：财务因果声明与官方证据逐项绑定（P0，done，由 `2d98abc` 完成复审收口）。
189. `FUND-004B`：C-006 同日期/同期间口径计算补修（P0，ready，依赖已闭环）。
190. `FUND-004A`：基本面语义门禁前移并剔除无效研究权重（P0，done，460 项回归通过）。
191. `FUND-005A`：逐 Agent 真实模型与运行时 trace 补修（P1，blocked-auto，依赖 FUND-004A）。
192. `FUND-006A`：真实 provider 格式与生产图离线回放验收（P1，done，FUND-006A-20260722-205540）。
193. `FUND-003A-B`：因果证据按术语/出现位置绑定补修（P0，done，`2d98abc`）。
194. `FUND-007A`：财报错误模式基准集与持续回归报告（P2，done，FUND-007A-20260722-213159）。

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

## PLAYBOOK. 上车—在车上—下车战法任务池（2026-07-08 新增）

> 目标：把“观察、试错、确认、进攻、持有、风控、退出”变成跟踪看板和 TA 报告的统一生命周期语言。系统不直接替用户下单，只负责把仓位阶段、证据强度、加减仓边界讲清楚。
> 参考契约：`docs/trade_playbook_lifecycle.md`。

### PLAYBOOK-001: 上车—在车上—下车战法字段契约与状态枚举（P1）
- **描述**：建立战法生命周期字段契约，统一跟踪看板、观察仓、TA 报告和 investment-controller 可读取的阶段/仓位字段。
- **优先级**：P1
- **状态**：done — API 读写闭环已补，Codex review PASS（无 P0/P1/P2 correctness findings）
- **前置条件**：TRACK-001、TRACK-003、DECISION-004 完成。
- **执行约束**：
  - 不调用 live LLM。
  - 不改 `tradingagents/prompts/`。
  - 不写生产 `tradingagents.db` 测试数据。
  - 不改变现有 `decision` / `execution_action` 向后兼容字段。
- **实现要点**：
  1. 新增或扩展只读 schema/dataclass，定义：
     - `playbook_stage`: `observe / trial / confirm / attack / hold / risk / exit`
     - `industry_evidence_score` / `earnings_validation_score` / `fund_confirmation_score` / `risk_pressure_score`
     - `planned_max_position_pct` / `current_position_pct`
     - `trial_lot_status` / `confirm_lot_status` / `attack_lot_status`
     - `core_position_qty` / `tactical_position_qty` / `defensive_cash_required_pct`
     - `allow_add` / `allow_replenish` / `allow_chase`
     - `add_trigger` / `reduce_trigger` / `exit_trigger`
  2. 字段优先采用派生/可选方式接入，不强制旧数据迁移。
  3. 输出 `docs/trade_playbook_lifecycle.md` 字段与代码 schema 的映射表。
  4. observation item 与 TA report response 能承载并回读这些字段；旧 SQLite 自动迁移，不写生产测试数据。
- **验收方式**：
  - 新增 schema/serialization 测试，旧报告和旧观察仓数据不报错。
  - 缺字段时返回空/默认值，不把未知阶段误判为 `hold`。
  - `git diff --check` 通过。
- **代码标注要求**：`# [PLAYBOOK-001] lifecycle_contract`

### PLAYBOOK-002: 计划仓位上限与上车三笔法规则引擎（P1）
- **描述**：把“计划最大仓位 + 试错仓/确认仓/进攻仓”写成可测试规则，禁止系统因为下跌简单提示补仓。
- **优先级**：P1
- **状态**：blocked — 战略暂停，待研报/半年报主线完成后人工释放
- **前置条件**：PLAYBOOK-001 完成；同时需满足当前产品主线已完成一次真实交易日回放。
- **执行约束**：
  - 不自动下单，不输出“立即买入/重仓买入”等强动作。
  - 不调 live LLM。
  - 不改 prompts。
- **实现要点**：
  1. 新增规则函数：按标的类型给出默认计划仓位上限（ETF、主板龙头、题材弹性股、业绩未验证、高波动传闻票、单票绝对上限）。
  2. 新增三笔法判断：
     - `trial_lot`: 投资假设清晰、非极端高位、板块未退潮、亏损可控、仓位小。
     - `confirm_lot`: 产业/业绩/资金三类证据至少两类增强。
     - `attack_lot`: 确认后出现回踩企稳或突破站稳。
  3. 若当前仓位已超过计划仓位，强制 `allow_add=False`。
  4. 第一次大跌、跌停封死、放量破位、板块退潮时，`allow_replenish=False`。
- **验收方式**：
  - fixture 覆盖：可试错、不可试错、可加确认仓、不可加确认仓、回踩进攻、突破进攻、跌停不补。
  - “跌了/便宜/回调”单独出现不能触发补仓。
  - 资金/业绩/产业证据不足时不得进入确认仓。
- **代码标注要求**：`# [PLAYBOOK-002] staged_entry_rules`

### PLAYBOOK-003: 持仓拆分与机动仓/核心仓/防守仓规则引擎（P1）
- **描述**：持仓后不再把仓位看成一坨，统一拆成核心仓、机动仓、防守仓，并定义高抛低吸和证伪减仓边界。
- **优先级**：P1
- **状态**：blocked — 等 PLAYBOOK-001/PLAYBOOK-002 完成
- **前置条件**：PLAYBOOK-001、PLAYBOOK-002 完成。
- **执行约束**：
  - 不修改真实持仓数量。
  - 不生成交易委托。
  - 不改 prompts。
- **实现要点**：
  1. 持仓拆分默认：核心仓 50%、机动仓 30%、防守现金 20%，允许用户配置覆盖。
  2. 盈利 15%—20%、30%、50%+、极端亢奋分别给出机动仓处理建议。
  3. 风险分层：轻度风险减机动仓，中度风险减到核心仓，重度证伪允许核心仓退出。
  4. 满仓/现金不足时提示缺乏防守仓，不适合继续加仓。
  5. 卖飞追回规则：站稳新平台 2—3 天且板块同步走强，追回不超过卖出机动仓一半。
- **验收方式**：
  - 已持仓样本能返回 core/tactical/defensive 三类数量或比例。
  - 赚钱下车与证伪下车输出不同 reason code。
  - 连续跌停/重大利空场景不得输出补仓。
- **代码标注要求**：`# [PLAYBOOK-003] position_bucket_rules`

### PLAYBOOK-004: TA 报告动作语义接入战法阶段与仓位建议（P1）
- **描述**：把 PLAYBOOK 阶段和仓位建议接入 TA 报告的结构化输出，使最终结论不再只有 HOLD/WAIT/观察。
- **优先级**：P1
- **状态**：blocked — 等 PLAYBOOK-002/PLAYBOOK-003 完成
- **前置条件**：PLAYBOOK-002、PLAYBOOK-003、DECISION-004 完成。
- **执行约束**：
  - 不改 prompts。
  - 不绕过现有 Buy Level / Risk Level / 强动作门禁。
  - 数据不足时不得输出强动作。
- **实现要点**：
  1. `StructuredReport` / API response 可选透传 `playbook_stage`、四项评分、仓位建议、触发条件。
  2. 对 `execution_action` 增加更细的 user-facing `playbook_action_label`：
     - 观察、试错仓、加确认仓、加进攻仓、持有、减机动仓、减到核心仓、证伪退出。
  3. “数据不足观察”必须带 reason codes，不允许只剩笼统观察。
  4. 本地知识/研报关注度只影响证据评分，不直接覆盖动作语义。
- **验收方式**：
  - 回放 WAIT/ENTER/HOLD/REDUCE/EXIT 五类报告，新字段不破坏旧字段。
  - 未持仓 + 只满足试错条件：只能输出试错仓/观察，不能输出重仓。
  - 已持仓 + 逻辑证伪：可输出减机动仓/减到核心仓/证伪退出。
- **代码标注要求**：`# [PLAYBOOK-004] report_playbook_semantics`

### PLAYBOOK-005: 跟踪看板战法字段前端展示与观察仓导入映射（P2）
- **描述**：在跟踪看板和观察仓展示阶段、三笔仓状态、核心/机动/防守拆分和下一步触发条件。
- **优先级**：P2
- **状态**：blocked — 等 PLAYBOOK-001/PLAYBOOK-004 完成
- **前置条件**：PLAYBOOK-001、PLAYBOOK-004、TRACK-003 完成。
- **执行约束**：
  - 不在前端制造强买卖词。
  - 字段缺失时使用空状态，不显示误导性默认值。
  - 保持移动端可读。
- **实现要点**：
  1. 跟踪看板新增战法阶段 badge 与证据评分小面板。
  2. 观察仓导入支持可选字段：计划仓位、阶段、触发条件、投资假设。
  3. 已持仓卡片展示核心仓/机动仓/防守仓估算。
  4. 今日指引按 playbook reason code 聚合。
- **验收方式**：
  - 前端类型检查通过。
  - 旧数据字段缺失不崩溃。
  - 截图/手册说明能回答“这只票现在是观察、试错、确认还是风控”。
- **代码标注要求**：`# [PLAYBOOK-005] playbook_dashboard_ui`

### PLAYBOOK-006: 战法回放验收与利通电子样例 fixture（P2）
- **描述**：用历史报告和利通电子示例回放验证战法不会变成“跌了就补/涨了就追”的自动化口号。
- **优先级**：P2
- **状态**：blocked — 等 PLAYBOOK-004/PLAYBOOK-005 完成
- **前置条件**：PLAYBOOK-004、PLAYBOOK-005 完成。
- **执行约束**：
  - 不调用 live LLM。
  - fixture 不写入生产 DB。
  - 不包含用户隐私资金明细。
- **实现要点**：
  1. 新增 fixture：观察、试错、确认、进攻、持有、风控、退出各一例。
  2. 利通电子样例覆盖：仓位过重、跌停风险、半年报验证、反抽无力先减机动仓。
  3. 检查赚钱下车与证伪下车 reason code 不混用。
  4. 生成 `docs/playbook_acceptance.md`。
- **验收方式**：
  - 所有 fixture 回放通过。
  - 跌停/第一次大跌场景 `allow_replenish=False`。
  - 证据增强场景才允许确认仓/进攻仓。
- **代码标注要求**：`# [PLAYBOOK-006] playbook_replay_acceptance`

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
- **状态**：done — 70 项专项通过，279 项回归通过

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
- **状态**：done — commit f97c6f6；2026-07-06 范围补审通过
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
- **状态**：done -- commit 62f0d46
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
- **状态**：done -- commit a1896ad
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
- **状态**：done -- commit 1901fe2
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
- **状态**：done -- commit defb365
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
- **状态**：done -- commit 6dbdf55
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
- **状态**：done -- commit 6f585c3
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
- **状态**：done -- commit 4aa806c
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

### TRACK-009: 跟踪看板持仓导入入口与 OpenClaw holdings 契约对齐（P1）
- **描述**：用户当前持仓通过 OpenClaw 导入，但 TA 跟踪看板前端没有明显入口，也没有说明 `/api/holdings` 与跟踪看板数据如何对齐。本任务补一个低成本、可验证的导入入口与契约说明。
- **优先级**：P1
- **状态**：done -- commit 09864cc
- **前置条件**：TRACK-003、TRACK-007 完成。
- **执行约束**：
  - 不接真实券商交易。
  - 不覆盖用户已有持仓备注。
  - 不写生产数据库测试数据。
- **实现要点**：
  1. 后端新增 holdings import dry-run/validate helper，校验 symbol、name、shares、avg_cost、source。
  2. 前端跟踪看板持仓区增加“导入/同步持仓”入口，支持粘贴 JSON/CSV 或显示 OpenClaw API 契约。
  3. 导入前展示差异：新增、更新、缺失、字段异常。
  4. 写入路径必须走既有 holdings service，避免旁路 DB。
- **验收方式**：
  - CSV/JSON fixture 导入 dry-run 测试通过。
  - 重复导入不清空 notes。
  - 前端构建通过。
- **代码标注要求**：`# [TRACK-009] holdings_import_contract` / `// [TRACK-009] holdings_import_contract`

### TRACK-010: 跟踪看板盘后复盘乱码与编码/渲染回归（P1）
- **描述**：用户反馈跟踪看板“盘后复盘”区域出现乱码。本任务追踪从 Review 文本、数据库、API JSON 到前端渲染的编码链路，补充中文回归 fixture。
- **优先级**：P1
- **状态**：done -- commit 41d6989
- **前置条件**：TRACK-005、TRACK-007 完成。
- **执行约束**：
  - 不重写 Review 生成逻辑。
  - 不修改历史生产数据，只做读取/渲染修复。
  - 不调用 LLM。
- **实现要点**：
  1. 构造含中文、百分号、表格、特殊符号的 review_summary fixture。
  2. 校验 SQLite 读取、FastAPI JSON 序列化、前端展示均保持 UTF-8。
  3. 如有 bytes/escape/markdown 二次转义，统一在 service 层清洗。
  4. 前端盘后复盘区域增加安全换行和空状态。
- **验收方式**：
  - 中文 fixture API 返回不乱码。
  - 前端单测或渲染测试覆盖复盘文本。
  - 盘后复盘为空时不显示异常字符。
- **代码标注要求**：`# [TRACK-010] review_encoding_regression` / `// [TRACK-010] review_encoding_regression`

### TF-QUALITY-005: 候选池“过多且像抄底”回放校准与强度分层（P1）
- **描述**：用户试用后认为候选池过多、像半山腰抄底，缺少真正可做 T 的优先级。本任务基于历史候选 fixture 做校准，明确主候选、观察候选、过滤候选三层。
- **优先级**：P1
- **状态**：done -- commit ed75000
- **前置条件**：TF-QUALITY-004、H-014 完成。
- **执行约束**：
  - 不引入真实交易建议。
  - 不调用全市场 live scan。
  - 不把阈值调得过拟合单日样本。
- **实现要点**：
  1. 收集最近候选池样本 fixture，覆盖 VCP、回踩、事件、昊天主题。
  2. 增加 `primary_candidate/secondary_candidate/filtered_reason` 分层规则。
  3. 对“弱缩量无资金确认”“反弹但趋势未修复”“数据不足”降层。
  4. 输出候选收敛报告，说明从 N 只压缩到主候选 M 只的原因。
- **验收方式**：
  - fixture 中主候选数量明显少于总候选。
  - 每只被降层候选有可读原因。
  - 现有候选评分字段不丢失。
- **代码标注要求**：`# [TF-QUALITY-005] candidate_pool_strength_tiers`

### H-017: 昊天左侧候选证据包：政策-产业-公司三层链路（P1）
- **描述**：昊天左侧候选不能只显示分数，需要展示政策版本、产业位置、公司角色三层证据，让用户判断是否值得长期观察。本任务生成可落库、可前端展示的 evidence packet。
- **优先级**：P1
- **状态**：done -- commit dafab39
- **前置条件**：H-015、H-016 完成。
- **执行约束**：
  - 不调用 LLM。
  - 不制造政策结论；只整理已有事件、主题、公告、研报证据。
  - 不输出买入建议。
- **实现要点**：
  1. 定义 `MandateEvidencePacket`：policy_theme、industry_chain_role、company_role、evidence_titles、missing_evidence。
  2. 从 H-015 日报、事件源、公告/研报 raw evidence 中汇总证据。
  3. 对证据不足的候选标记 `needs_manual_research`。
  4. 前端或 API 返回三层链路摘要。
- **验收方式**：
  - fixture 生成三层证据包。
  - 缺政策/缺公司证据时不虚高。
  - 禁用词扫描通过。
- **代码标注要求**：`# [H-017] mandate_evidence_packet`

### DATA-024: 主力资金供应商 fallback live-smoke dry-run 与错误归因报告（P1）
- **描述**：主力资金仍是用户高频痛点。本任务不直接更换供应商，而是建立 provider fallback 的 dry-run 检查与错误归因报告，明确 AKShare、备用源、网络、限流、无数据各自原因。
- **优先级**：P1
- **状态**：done -- commit bfc6d3e
- **前置条件**：DATA-022、DATA-023 完成。
- **执行约束**：
  - 默认使用 fixture；live-smoke 必须显式开关。
  - 不打印密钥或 cookie。
  - 不把板块资金流当个股资金流。
- **实现要点**：
  1. 新增 `fund_flow_source_probe`，返回 vendor、status、error_type、unit、record_count。
  2. 支持 fixture replay 和 `--live-smoke --symbols` 小样本。
  3. 报告区分：网络失败、接口字段变更、正常无数据、限流、单位不明。
  4. 接入 DATA-023 能力矩阵。
- **验收方式**：
  - fixture 覆盖 5 类失败/无数据。
  - live-smoke 默认关闭。
  - 报告不出现明文敏感信息。
- **代码标注要求**：`# [DATA-024] fund_flow_source_probe`

### REPORT-UX-003: 报告最终结论“数据不足观察”原因分解与前端显示（P1）
- **描述**：用户反馈最近三篇报告最终都变成“数据不足观察”，过于笼统。本任务把观察原因拆成数据缺口、门禁降级、结论冲突、未触发入场、风险优先等可解释标签。
- **优先级**：P1
- **状态**：done -- commit 81511d1
- **前置条件**：REPORT-UX-001、REPORT-UX-002 完成。
- **执行约束**：
  - 不放宽强动作门禁。
  - 不修改 prompts。
  - 不调用 LLM。
- **实现要点**：
  1. 定义 `wait_reason_codes`：DATA_MISSING、GATE_BLOCKED、CONFLICT、NO_TRIGGER、RISK_FIRST、NORMAL_NO_DATA。
  2. 在 report_service 或 signal_processing 输出 reason codes。
  3. 前端报告卡片显示“观察原因”，而不是单一“数据不足观察”。
  4. 历史报告 fallback 兼容旧字段。
- **验收方式**：
  - 3 类 WAIT fixture 输出不同 reason codes。
  - 数据正常但未触发不显示“数据不足”。
  - 前端构建通过。
- **代码标注要求**：`# [REPORT-UX-003] wait_reason_codes` / `// [REPORT-UX-003] wait_reason_codes`

### TF-OBS-006: 盘中观察自动执行入口与红涨绿跌视觉语义修正（P2）
- **描述**：用户认为盘中观察需要手动点执行，且距离触发价的红绿含义反大 A 直觉。本任务增加更清晰的刷新入口和视觉语义说明。
- **优先级**：P2
- **状态**：done -- commit 70c289f
- **前置条件**：TF-OBS-005、UI-013 完成。
- **执行约束**：
  - 不开启后台高频轮询。
  - 不自动发送交易动作。
  - 不改触发算法。
- **实现要点**：
  1. 盘中观察 tab 增加“一键刷新观察”和最近刷新时间。
  2. 颜色改为：接近触发/强势接近用红系，远离/走弱用绿或灰，并加图例。
  3. 非交易时段显示“可查看上次观察/等待交易时段”。
  4. 增加前端测试或 snapshot。
- **验收方式**：
  - 前端构建通过。
  - 红涨绿跌语义不再反直觉。
  - 不产生自动交易措辞。
- **代码标注要求**：`// [TF-OBS-006] observe_refresh_visual_semantics`

### IC-TA-004: investment-controller 飞书 briefing payload 与 TA 调度闭环验收（P2）
- **描述**：investment-controller 已转型为 TA 调度官/播报官。本任务固定它给飞书/总控的 briefing payload，验证盘前、盘中、盘后各自只做调度与摘要，不越权下最终交易结论。
- **优先级**：P2
- **状态**：done -- commit 4473a4d
- **前置条件**：IC-TA-003、NOTIFY-003 完成。
- **执行约束**：
  - dry-run，不真实发送。
  - 不调用 LLM。
  - 不输出强买卖词。
- **实现要点**：
  1. 定义 `controller_briefing_payload`：scene、summary、ta_requests、watch_items、data_warnings、notify_level。
  2. 盘前、盘中、盘后三类 fixture。
  3. 接入通知去噪规则，P2/P3 进入日报。
  4. 输出 markdown 预览供飞书卡片后续接入。
- **验收方式**：
  - 三场景 dry-run 测试通过。
  - 禁用词扫描通过。
  - payload 不包含密钥/敏感配置。
- **代码标注要求**：`# [IC-TA-004] controller_briefing_payload`

### V-012: 5000 元小资金试跑 5 日回放验收与人工操作手册 v3（P2）
- **描述**：用户准备拿约 5000 元试跑，需要一份 5 日纸面交易/模拟观察回放验收，验证候选收敛、观察触发、人工确认、盘后归因能闭环。
- **优先级**：P2
- **状态**：done -- commit 5e494c7
- **前置条件**：V-010、V-011 完成。
- **执行约束**：
  - 不连接真实交易。
  - 不输出收益承诺。
  - 不调用 LLM。
- **实现要点**：
  1. 构造 5 个交易日 fixture：候选、观察、模拟账本、盘后 Review。
  2. 输出用户手册：每天盘前看什么、盘中点什么、盘后复盘什么。
  3. 记录风险预算：单票金额、最大回撤、失败退出条件。
  4. 生成验收文档。
- **验收方式**：
  - 5 日 fixture replay 通过。
  - 能回答“今天看哪几只、为什么、何时触发、盘后如何处理”。
  - 无强买卖词。
- **代码标注要求**：`# [V-012] paper_trial_5day_replay`

### AUTO-004: 夜间三小时任务续航预算与失败后停止策略回归（P2）
- **描述**：用户希望夜间能跑 3 小时左右。本任务给自动开发链增加续航预算报告：当前 ready 任务预计可跑多久、失败是否停止、是否会因任务池空而空转。
- **优先级**：P2
- **状态**：done -- commit 6b9e8b5
- **前置条件**：AUTO-003、V-011 完成。
- **执行约束**：
  - 不改变 cron 时间。
  - 不自动启动 OpenCode。
  - 不绕过 Codex review。
- **实现要点**：
  1. 根据历史 task_runs 估算每类任务平均耗时。
  2. dry-run 输出预计续航时长和 ready 队列排序。
  3. 确认失败即停策略仍生效。
  4. ready 不足 2 小时时生成 proposed 补充建议。
- **验收方式**：
  - dry-run 报告包含预计时长。
  - ready 为空时不直接空转。
  - 不影响现有 auto_dev_loop 领取逻辑。
- **代码标注要求**：`# [AUTO-004] auto_dev_runtime_budget`

### KB-001: Tree Work 本地知识库只读索引与健康审计（P1）
- **描述**：把 `~/Documents/knowledge/` 作为 TA 的本地投研知识源进行只读审计，确认 Tree Work 产物是否能被机器稳定检索和追溯。
- **优先级**：P1
- **状态**：done -- commit 97e997f
- **产出**：
  - `tradingagents/dataflows/local_knowledge_audit.py`（只读审计模块）
  - `scripts/audit_local_knowledge.py`（CLI）
  - `tests/test_kb001_local_knowledge_audit.py`（47 tests）
  - `docs/knowledge_reports/local_knowledge_audit-2026-07-01.md`（真实知识库审计报告）
- **前置条件**：无。
- **背景**：用户的 Tree Work 基于 Karpathy LLM Wiki 模式，`wiki/investment/` 已有 76 篇投资知识、`raw/` 保存原始资料、`inbox/` 有待处理材料。TA 需要接入消化后的 wiki，而不是直接消化 raw 研报。
- **执行约束**：
  - 只读扫描 `~/Documents/knowledge/`，不得修改知识库内容。
  - 不读取或输出大段研报原文；只输出文件名、字段、统计、缺口。
  - 不把本地知识观点当成实时事实或交易建议。
  - 不调用 LLM，不访问外网。
- **实现要点**：
  1. 新增只读扫描脚本或服务 helper，统计 wiki/investment、raw、inbox、index/log 覆盖情况。
  2. 输出 `docs/knowledge_reports/local_knowledge_audit-YYYY-MM-DD.md`，包含：页面数、frontmatter 覆盖、索引缺失、风险段缺失、摘要段缺失、未消化 inbox。
  3. 识别 investment 页面类型：公司页、行业页、评分表、汇总表、待补充页。
  4. 生成 TA 接入建议字段清单，不直接改 Tree Work。
- **验收方式**：
  - fixture 或真实只读扫描可运行。
  - 报告不包含长篇原文和敏感信息。
  - 能列出至少 5 类结构缺口和接入建议。
- **代码标注要求**：`# [KB-001] local_knowledge_audit`

### KB-002: investment wiki 输出协议升级：TA 可消费字段 lint（P1）
- **描述**：为 Tree Work 提出稳定的 investment wiki 输出协议，并在 TA 仓库实现 lint，避免后续消化研报后 TA 仍抓不到股票、主题、风险、来源和时效。
- **优先级**：P1
- **状态**：done -- commit f5bbb9d
- **产出**：
  - `tradingagents/dataflows/local_knowledge_lint.py`（lint 引擎：12 条规则、findings、machine_readiness、修复建议）
  - `scripts/lint_local_knowledge.py`（CLI：`--fail-on-error` CI 门禁，默认非阻塞）
  - `docs/local_knowledge_contract.md`（契约 `kb-002-v1`，Tree Work 交付标准）
  - `tests/test_kb002_local_knowledge_lint.py`（52 tests）
  - `docs/knowledge_reports/local_knowledge_lint-2026-07-01.md`（真实知识库 lint 报告）
- **最近一次自动运行**：KB-002-20260630-182708 因 review/收口阶段进入 NEEDS_HUMAN，未产生可提交实现；已归档 run/review 记录，允许下一轮重新领取。
- **前置条件**：KB-001 完成。
- **Tree Work 新要求草案**：
  1. 每篇 investment wiki 必须保留基础 frontmatter：`title/created/updated/sources/tags/related`。
  2. 推荐增加机器字段：`symbols`、`themes`、`industry_chain_roles`、`report_type`、`evidence_level`、`valid_until`、`source_quality`、`stale_risk`。
  3. 正文必须至少包含：`一句话总结`、`投资逻辑/核心观点`、`风险提示`、`原始资料/关联研报`。
  4. 公司评分表必须保留标准表头：公司、代码、核心业务、板块、利好度、共识度、预计启动、期待周期。
  5. 未验证或扫描失败内容必须显式标记 `待补充/低置信`。
- **执行约束**：
  - 本任务只在 TA 侧写 lint 与文档，不批量改写知识库。
  - 不要求 Tree Work 一次性重写历史 76 篇，只输出缺口清单。
- **实现要点**：
  1. 新增 `scripts/lint_local_knowledge.py` 或等价模块，检查字段和章节。
  2. 输出每篇 investment wiki 的 `machine_readiness`：high/medium/low。
  3. 对缺 symbol、缺 risk、缺 summary、缺 source 的页面给出修复建议。
  4. 生成 `docs/local_knowledge_contract.md`，作为 Tree Work 后续消化研报的交付标准。
- **验收方式**：
  - lint 对当前知识库能跑通。
  - 低分页面不会阻塞 TA，只降低本地知识源置信度。
  - 文档可直接发给 Tree Work/HR Agent 执行。
- **代码标注要求**：`# [KB-002] local_knowledge_contract`

### KB-003: TA 本地知识源 raw_evidence 接入与报告“本地知识补充”区块（P1）
- **描述**：TA 分析股票时，从 Tree Work 已消化的 `wiki/investment/` 中只读查询相关公司/主题，写入 `metadata.raw_evidence.local_knowledge`，并在报告中展示“本地知识补充”。
- **优先级**：P1
- **状态**：done -- commit 01d751c
- **前置条件**：KB-001、KB-002 完成。
- **执行约束**：
  - TA 只接入消化后的 wiki，不直接读 raw PDF 做结论。
  - 本地知识只能作为背景/观点源，不能替代行情、公告、财务、资金流。
  - 命中低置信/过期页面时必须标记 `STALE` 或 `LOW_CONFIDENCE`。
  - 不输出长篇研报原文。
- **实现要点**：
  1. 新增 `local_knowledge_provider`，按 symbol/name/theme/tags 查询 investment wiki。
  2. 返回结构：`status/vendor=tree_work_wiki/matched_pages/symbols/themes/summary/risks/sources/updated_at/confidence`。
  3. 接入 DataCollector/raw_evidence，状态支持 `HAS_DATA/NORMAL_NO_DATA/FAILED/STALE/LOW_CONFIDENCE`。
  4. 报告增加“本地知识补充”区块：最多 3 条摘要 + 风险 + 原页面路径。
- **验收方式**：
  - 给定 `603296` 能命中华勤技术页面。
  - 给定主题“AI算力基础设施”能命中评分表。
  - 无命中时返回 NORMAL_NO_DATA，不影响报告主结论。
- **代码标注要求**：`# [KB-003] local_knowledge_raw_evidence`

### KB-004: TradeFlow 昊天候选接入本地知识命中分与证据摘要（P1）
- **描述**：TradeFlow 昊天左侧候选应优先利用 Tree Work 已消化的产业认知，候选入池时显示“本地知识命中：公司/主题/产业链角色/风险”。
- **优先级**：P1
- **状态**：done — commit 1b94c70；claimed KB-004-20260704-020304，Codex review 补修完成
- **前置条件**：KB-003、H-017 完成。
- **执行约束**：
  - 本地知识命中只加解释力，不单独触发候选入池。
  - 过期知识不得加分，只能提示需更新。
  - 不把券商观点当政策原文。
- **实现要点**：
  1. 候选评分增加 `local_knowledge_score` 与 `knowledge_hit_count`。
  2. H-017 evidence packet 增加 `local_knowledge_summary`。
  3. 前端候选详情显示命中页面、更新时间、风险提示。
  4. 对知识缺失但主题热的候选标记 `needs_tree_work_research`。
- **验收方式**：
  - 本地知识命中页面能展示在候选详情。
  - 无知识命中的候选不被误降为失败。
  - 过期/低置信页面不提升主候选层级。
- **代码标注要求**：`# [KB-004] tradeflow_knowledge_score`

### KB-005: Tree Work inbox/raw/wiki 对齐与未消化研报清单（P2）
- **描述**：建立本地知识库清理清单，找出 inbox 积压、raw 已存但 wiki 未消化、wiki 有占位但未补充的内容，方便 Tree Work 后续处理。
- **优先级**：P2
- **状态**：done -- commit ca3a477
- **前置条件**：KB-001 完成。
- **执行约束**：
  - 只生成清单，不移动/删除 knowledge 文件。
  - 不批量读取 PDF 正文。
- **实现要点**：
  1. 输出 `docs/knowledge_reports/tree_work_ingest_backlog-YYYY-MM-DD.md`。
  2. 分类：inbox 未处理、raw 未消化、wiki 待补充、index 未同步。
  3. 为每项生成建议动作：ingest、补字段、补风险、补原始资料链接、归档。
  4. 支持后续复制给 Tree Work 执行。
- **验收方式**：
  - 当前 inbox 5-8 项均被列出。
  - 至少识别 `待补充` 页面。
  - 不修改 knowledge 目录。
- **代码标注要求**：`# [KB-005] tree_work_backlog`

### KB-006: 本地知识库查询 API 与 investment-controller 只读上下文接入（P2）
- **描述**：提供只读 API/服务函数，让 investment-controller 在盘前/盘后 briefing 中引用 Tree Work 知识命中，而不是让它自己读文件或编造背景。
- **优先级**：P2
- **状态**：done — commit 96970d8；2026-07-06 范围补审通过并完成 P2 补修
- **前置条件**：KB-003、IC-TA-004 完成。
- **执行约束**：
  - 只读，不写 knowledge。
  - API 不返回全文，只返回摘要、路径、更新时间和风险。
  - 不暴露用户隐私分类内容，默认只开放 `wiki/investment`。
- **实现要点**：
  1. 新增 `/v1/knowledge/local/search` 或 service helper，支持 symbol/theme 查询。
  2. investment-controller context 增加 `local_knowledge_hits` bucket。
  3. briefing 中仅用作“背景补充/待研究提示”。
  4. 增加访问路径配置，默认 `~/Documents/knowledge`，支持禁用。
- **验收方式**：
  - symbol 查询返回结构化摘要。
  - 非 investment 分区默认不可见。
  - controller dry-run payload 不含长篇原文。
- **代码标注要求**：`# [KB-006] local_knowledge_context_api`
- **交付物**：
  - `api/services/local_knowledge_context_service.py`（`search_local_knowledge` +
    `collect_local_knowledge_hits` + `is_local_knowledge_disabled` +
    `resolve_knowledge_root` + slim 输出契约 + disable env）
  - `api/services/investment_controller_context.py`（新增第 9 个 bucket
    `local_knowledge_hits` + `controller_hints.research_review` lane +
    `assert_no_strong_action_verbs` 扩展）
  - `api/main.py`（`GET /v1/knowledge/local/search` 端点）
  - `api/runtime_tier.py`（`local_knowledge_search` 注册为 FAST_RADAR）
  - `tests/test_kb006_local_knowledge_context_api.py`（68 tests）

### KB-007: 多研报重复提及因子 Research Attention Score（P1）
- **描述**：统计 Tree Work `wiki/investment/` 中每只股票被多少篇研报/评分表/主题页重复提及，形成“研报关注度/主题交叉度”因子，用于候选发现和中线研究优先级。
- **优先级**：P1
- **状态**：done -- commit 398917b
- **前置条件**：KB-001、KB-002 完成。
- **执行约束**：
  - 不把多研报提及当成买入信号。
  - 不读取 raw PDF 全文，只使用 Tree Work 已消化 wiki 的 frontmatter 和短摘要。
  - 不调用 LLM，不访问外网。
  - deprecated、过期、低置信页面必须降权或仅作弱证据。
- **实现要点**：
  1. 建立本地倒排索引：`symbol -> matched_pages[]`。
  2. 计算字段：`mention_count`、`fresh_mention_count`、`theme_count`、`source_count`、`high_quality_mention_count`、`stale_mention_count`、`deprecated_mention_count`、`report_type_distribution`。
  3. 合成 `research_attention_score`：提及次数 + 主题交叉 + 来源质量 + 新鲜度 - 过期/低置信/重复来源惩罚。
  4. 输出 `docs/knowledge_reports/research_attention-YYYY-MM-DD.md/json`，列出 Top symbols 和明细。
  5. 对基金代码、港股、美股、未上市主体与 A 股股票分类型处理，不混成一类。
- **验收方式**：
  - 对当前知识库可生成 symbol 关注度榜。
  - 同一股票多篇命中能汇总主题与来源。
  - 过期/低置信/废弃页面不提升强信号。
  - 无任何买卖建议或强动作词。
- **代码标注要求**：`# [KB-007] research_attention_score`
- **交付物**：
  - `tradingagents/dataflows/research_attention.py`（倒排索引 + 资产分类 + 分数合成 + 报告渲染）
  - `scripts/run_research_attention.py`（CLI：`--knowledge-root / --output / --json-output / --json / --stdout / --top`）
  - `tests/test_kb007_research_attention.py`（78 tests）
  - `docs/knowledge_reports/research_attention-2026-07-01.md` / `.json`（真实知识库 76 页 / 228 标的报告）

### KB-008: TA/TradeFlow 接入研报关注度与主题交叉度展示（P1）
- **描述**：将 KB-007 的研报关注度接入 TA 报告、TradeFlow 候选池和观察仓，使“多份研报反复提到同一股票”成为研究优先级和解释信息。
- **优先级**：P1
- **状态**：done -- commit 5be0f52
- **前置条件**：KB-003、KB-007 完成。
- **执行约束**：
  - 仅作为候选排序/研究优先级因子，不直接改变交易动作。
  - 不与公告、财务、行情、资金流混用。
  - 前端展示必须同时显示负面信息：过期数、低置信数、主题是否拥挤。
- **实现要点**：
  1. TA “本地知识补充”区块增加：命中篇数、主题交叉、来源质量、过期数、综合关注度。
  2. TradeFlow 候选增加 `research_attention_score`、`knowledge_theme_count`、`research_attention_summary` 字段。
  3. 昊天左侧池优先展示“多主题交叉但未过热”的候选。
  4. 观察仓详情展示命中页面列表和最近更新时间。
- **验收方式**：
  - fixture 中同一股票多篇命中，前端/API 展示关注度摘要。
  - 仅低置信命中不会推高候选层级。
  - 无命中时返回 NORMAL_NO_DATA，不影响 TA 主流程。
- **代码标注要求**：`# [KB-008] research_attention_integration` / `// [KB-008] research_attention_integration`
- **交付物**：
  - `tradingagents/dataflows/research_attention.py` 扩展（`lookup_research_attention` + `attention_to_summary` + `render_research_attention_inline`）
  - `api/services/report_service.py`（`attach_report_local_knowledge` 注入 KB-008 顶层字段 + block 拼接）
  - `api/main.py`（`_attach_report_data_blockers_for_response` 同步 KB-008 字段）
  - `api/tradeflow_schemas.py`（`TradeFlowCandidateItem` 新增 4 个字段）
  - `api/services/tradeflow_service.py`（`_enrich_candidate_with_research_attention` + `_enrich_candidates_with_research_attention` + 4 个读取入口注入）
  - `tests/test_kb008_research_attention_integration.py`（39 tests）

### DATA-025: 免费研报来源目录与 Eastmoney/AKShare 研报源 smoke（P2）
- **描述**：梳理免费可用研报来源，优先验证东方财富研报中心/AKShare `stock_research_report_em`，作为外部研报元数据补充源；其它免费页面先列入目录，不做重抓。
- **优先级**：P2
- **状态**：done -- commit a50e45c
- **前置条件**：DATA-011、DATA-023 完成。
- **免费来源初版**：
  1. 东方财富研报中心/个股研报：可通过 AKShare `stock_research_report_em(symbol)` 获取个股研报元数据、机构、评级、盈利预测、日期、PDF链接。
  2. 新浪财经研究评级页：可作为补充索引源，字段以标题、日期、评级为主。
  3. 巨潮资讯/CNInfo：这是公告/法披真源，不是券商研报源；只用于公告与原始披露交叉验证。
  4. 券商官网/上市公司 IR 页面：可作为人工补充，不做默认爬取。
- **执行约束**：
  - 第一版只做目录和小样本 smoke，不批量下载 PDF。
  - 尊重来源网站访问限制；默认 fixture，live-smoke 必须显式开关。
  - 免费研报仅作为观点/关注度/预期源，不能替代公告或财报。
  - 不提交下载的 PDF 正文或版权内容。
- **实现要点**：
  1. 更新数据源能力矩阵，增加 `research_report_free_sources`。
  2. 为 Eastmoney/AKShare 个股研报做 3-5 只样本 smoke：有数据、无数据、接口失败三类。
  3. 输出 `docs/data_source_reports/research_report_sources-YYYY-MM-DD.md`。
  4. 对接 DATA-011 的 `research_report` raw_evidence 状态，不改变强动作门禁。
- **验收方式**：
  - AKShare 个股研报 fixture 解析字段完整。
  - 无研报返回 NORMAL_NO_DATA，接口失败返回 FAILED。
  - 报告明确区分“研报观点源”和“公告/财报事实源”。
- **代码标注要求**：`# [DATA-025] free_research_report_sources`

### KB-009: 研报来源去重、时效衰减与过热惩罚规则（P2）
- **描述**：防止 Research Attention Score 被同源重复、过期周报或热门题材过度放大，加入去重、时效衰减和过热惩罚。
- **优先级**：P2
- **状态**：done -- commit 029d625
- **前置条件**：KB-007 完成。
- **执行约束**：
  - 不压制真实多来源共识，只惩罚重复和过期。
  - 不用单日价格涨幅作为唯一过热指标。
  - 不调用 LLM。
- **实现要点**：
  1. source 去重：同一机构/同一标题/同一 raw source 重复命中只计一次主权重。
  2. 时效衰减：`valid_until` 过期后只保留弱证据；高 stale_risk 页面权重衰减更快。
  3. 过热惩罚：结合候选已有 overheat_flags、短期涨幅、主题拥挤度降低研究优先级。
  4. 输出 explain：为什么加分、为什么降权。
- **验收方式**：
  - 同源重复不会把关注度刷高。
  - 过期页面不提升主候选层级。
  - explain 字段可读。
- **代码标注要求**：`# [KB-009] research_attention_decay`

### KB-010: 本地知识索引缓存与 freshness manifest（P1）
- **描述**：为 `local_knowledge_provider` / `research_attention` 增加轻量索引缓存和 freshness manifest，减少每次查询全量扫描 `~/Documents/knowledge` 的成本，并明确知识库是否过期。
- **优先级**：P1
- **状态**：done — commit f97c6f6；2026-07-06 范围补审通过并完成 P2 补修
- **前置条件**：KB-003、KB-007、KB-009 完成。
- **执行约束**：
  - 只读 `~/Documents/knowledge`，不得修改 knowledge 文件。
  - 缓存只写项目本地运行目录或 `docs/knowledge_reports/` 里的报告，不写生产 DB。
  - 缓存命中不能改变交易动作，只能提高查询速度和解释稳定性。
- **实现要点**：
  1. 基于文件路径、mtime、size 生成 knowledge manifest。
  2. manifest 未变化时复用索引；变化时自动重建。
  3. 输出 `freshness_status`：fresh/stale/missing/error。
  4. CLI 增加 `--rebuild-cache` / `--no-cache` 或等价参数。
- **验收方式**：
  - 同一知识库连续查询结果一致，第二次查询明显少走全量扫描逻辑。
  - 修改 fixture 文件 mtime 后缓存失效并重建。
  - 缓存缺失/损坏时自动回退全量扫描，不影响 TA 主流程。
- **代码标注要求**：`# [KB-010] local_knowledge_cache`

### KB-011: 本地知识/研报关注度前端与 API 契约回归（P1）
- **描述**：系统已经接入 `local_knowledge_summary`、`research_attention_score`、`research_attention_effective_score` 等字段，但需要确认 API schema、前端展示、空状态和负面信息不丢失。
- **优先级**：P1
- **状态**：done — commit e75eef4；2026-07-06 范围补审通过
- **前置条件**：KB-008、KB-009 完成。
- **执行约束**：
  - 不新增模型调用。
  - 不改 prompts。
  - 前端只展示摘要、路径、更新时间、风险，不展示研报长原文。
- **实现要点**：
  1. 补齐报告详情、TradeFlow 候选详情、观察仓详情中本地知识字段的 schema/type。
  2. 前端展示必须同时显示加分项与降权项：过期、低置信、同源重复、过热惩罚。
  3. 无命中时显示 NORMAL_NO_DATA/暂无本地知识，不当成错误。
  4. 增加 API/前端 contract tests。
- **验收方式**：
  - fixture 候选有知识命中时，API 和前端字段一致。
  - 只有低置信/过期命中时，前端不显示为强正面。
  - `npm run build` 或 `tsc --noEmit` 通过。
- **代码标注要求**：`# [KB-011] knowledge_contract_ui` / `// [KB-011] knowledge_contract_ui`

### DATA-026: 生产库测试污染健康检查与 scheduler 启动告警（P1）
- **描述**：把本次 `@test.com` 数据库污染治理固化为运行时健康检查，防止生产库再次积累测试用户、测试定时任务或测试报告。
- **优先级**：P1
- **状态**：done — commit dfc4460；2026-07-06 范围补审通过并完成 P2 补修
- **前置条件**：`scripts/cleanup_test_db_pollution.py` 已存在。
- **执行约束**：
  - 默认只读，不自动删除生产数据。
  - 不打印密钥字段。
  - 不阻塞真实用户定时任务，除非发现 pending 队列会执行测试用户。
- **实现要点**：
  1. 新增 DB hygiene check service/helper，统计 `@test.com` users/scheduled/reports/watchlist/imported positions。
  2. scheduler 启动时输出一次 warning；API 可暴露只读健康状态。
  3. 检查 `get_pending_tasks` 是否仍排除测试用户，若失效标记 P0 risk。
  4. 文档说明清理命令和备份路径。
- **验收方式**：
  - fixture 污染库能返回污染计数。
  - 干净库返回 all_green。
  - scheduler 不会因 warning 触发 TA/LLM。
- **代码标注要求**：`# [DATA-026] db_hygiene_check`

### AUTO-005: 自动开发前置检查接入 DB hygiene 与续航门禁（P1）
- **描述**：自动开发开始前应检查生产 DB 是否有测试污染、ready 队列是否足够续航、工作区是否干净；异常时停止或写明 NEEDS_HUMAN，避免夜间空转或继续污染。
- **优先级**：P1
- **状态**：done -- commit 553366b
- **前置条件**：AUTO-004、DATA-026 完成。
- **执行约束**：
  - preflight 默认只读。
  - 不自动执行 DB 清理；只输出建议命令。
  - 失败即停规则不能被削弱。
- **实现要点**：
  1. `scripts/preflight_check.sh` 或 `auto_dev_loop.sh` 接入 DB hygiene dry-run。
  2. `summarize_auto_dev_runs.py` 报告 ready 续航不足时给出明确补任务提示。
  3. 自动开发日报记录：ready 数、预计续航、DB hygiene 状态。
  4. 增加静态测试，防止 preflight 写生产 DB。
- **验收方式**：
  - 污染 fixture 下 preflight 返回 warning/needs-human，不执行 OpenCode。
  - 干净库下自动开发 dry-run 可继续选任务。
  - `AUTO_DEV_MAX_TASKS` 与失败即停逻辑不回归。
- **代码标注要求**：`# [AUTO-005] db_hygiene_preflight`

### REPORT-UX-004: 本地知识补充区块历史报告回放验收（P1）
- **描述**：用历史报告样本回放验证"本地知识补充/研报关注度/数据不足观察原因"三者不会互相污染，避免本地知识被误当成行情事实或最终动作依据。
- **优先级**：P1
- **状态**：done — commit c0de17e；2026-07-06 范围补审通过
- **前置条件**：KB-003、KB-008、REPORT-UX-003 完成。
- **执行约束**：
  - 不重跑 live TA。
  - 不调用 LLM。
  - 不把本地知识命中作为覆盖数据缺口的理由。
- **实现要点**：
  1. 选 3-5 份历史报告 fixture：有知识命中、无命中、低置信/过期、数据不足观察。
  2. 回放 `attach_report_local_knowledge` 与报告响应字段。
  3. 验证 wait_reason_codes/data_blockers 不被本地知识冲掉。
  4. 输出回放报告到 `docs/report_reviews/` 或 `docs/knowledge_reports/`。
- **验收方式**：
  - “数据不足观察”仍显示真实数据缺口原因。
  - 本地知识区块只作为补充背景。
  - 历史报告 response schema 不破坏前端。
- **代码标注要求**：`# [REPORT-UX-004] local_knowledge_replay`

### TF-KB-001: TradeFlow 本地知识分校准回放与弱候选防提升（P1）
- **描述**：验证 KB-004/KB-009 接入后，TradeFlow 不会因为本地知识命中把技术弱、数据弱或过热候选错误提升为主候选。
- **优先级**：P1
- **状态**：done — commit 2ae32df；2026-07-06 范围补审通过
- **前置条件**：KB-004、KB-009 完成。
- **执行约束**：
  - 本地知识分只能作为解释和排序辅助，不得单独触发候选入池。
  - 弱数据、过热、反证强的候选必须保持降级。
  - 不写生产 DB。
- **实现要点**：
  1. 构造强知识命中但技术弱、强知识命中且技术确认、无知识命中但技术强三类 fixture。
  2. 校准 `local_knowledge_score` 和 `research_attention_effective_score` 的权重上限。
  3. 输出候选排序 explain，明确“为什么没提升/为什么只是加解释”。
  4. 增加回归测试防止知识分越权。
- **验收方式**：
  - 技术弱 + 知识强不会进入主候选。
  - 技术确认 + 知识强可提高研究优先级但不输出强买卖。
  - 数据不足时仍走 observation/filtered。
- **代码标注要求**：`# [TF-KB-001] knowledge_score_calibration`

### KB-012: Tree Work 研报补录任务包导出（P2）
- **描述**：把 KB-002 lint、KB-005 backlog、KB-007/009 关注度结果整理成可直接发给 Tree Work 的补录任务包，帮助后续研报消化更符合 TA 消费要求。
- **优先级**：P2
- **状态**：done — commit 53cebbc；2026-07-06 范围补审通过
- **前置条件**：KB-002、KB-005、KB-007 完成。
- **执行约束**：
  - 只生成任务包，不修改 knowledge。
  - 不复制研报长文本。
  - 输出面向 Tree Work 的字段要求和优先级，不输出交易建议。
- **实现要点**：
  1. 合并 lint findings、inbox/raw backlog、research attention low-confidence/stale 页面。
  2. 生成 `docs/knowledge_reports/tree_work_task_pack-YYYY-MM-DD.md`。
  3. 按优先级分组：缺 symbol/name、缺 thesis、缺 risks、过期需复核、热门但证据薄。
  4. 给出 Tree Work ingest 模板。
- **验收方式**：
  - 当前真实知识库可生成任务包。
  - 每条任务有来源路径和建议修复动作。
  - 不包含长篇原文。
- **代码标注要求**：`# [KB-012] tree_work_task_pack`

### V-013: Tree Work → TA → TradeFlow → investment-controller 知识链路验收（P2）
- **描述**：端到端验收本地知识链路：Tree Work wiki 被 TA raw_evidence 读取，报告展示补充区块，TradeFlow 候选显示知识分，investment-controller context 只读引用。
- **优先级**：P2
- **状态**：done — commit 72c20de；2026-07-06 范围补审通过
- **前置条件**：KB-006、TF-KB-001 完成。
- **执行约束**：
  - 使用 fixture / dry-run，不调用 live LLM。
  - 不写生产 DB。
  - 验收重点是字段完整、来源可追溯、权限只读、无强动作越权。
- **实现要点**：
  1. 建立一条 symbol fixture，从本地知识命中到 TA response。
  2. 验证 TradeFlow candidate detail 带知识摘要和降权信息。
  3. 验证 investment-controller context 有 `local_knowledge_hits`，且不含长原文。
  4. 生成验收报告。
- **验收方式**：
  - 端到端测试通过。
  - 输出报告能回答：命中哪些知识、是否过期、如何影响研究优先级、是否改变交易动作。
  - 无强买卖词新增。
- **代码标注要求**：`# [V-013] knowledge_e2e_acceptance`

### HY-001: 半年报 Tree Work 输出协议扩展与 lint 规则（P1）
- **描述**：在现有 `docs/local_knowledge_contract.md` 基础上扩展半年报/中报消化协议，让 Tree Work 产出的半年报页面能被 TA 稳定消费。
- **优先级**：P1
- **状态**：done -- commit 58c23f8
- **前置条件**：KB-002、KB-012 完成。
- **背景**：半年报季将带来大量公司事实数据。TA 不能只读“研报观点”，必须能区分财报事实、管理层表述、券商观点和二级市场演绎。
- **执行约束**：
  - 不改写 `~/Documents/knowledge/` 原文。
  - 不复制长篇财报/研报正文。
  - 不把券商观点写成事实；字段必须标注来源类型。
  - 不调用 live LLM。
- **实现要点**：
  1. 扩展本地知识契约，新增 `financial_period`、`disclosure_date`、`source_type`、`financial_facts`、`segment_facts`、`management_commentary`、`forward_guidance`、`risk_factors`、`source_links` 等半年报字段。
  2. 在 `local_knowledge_lint.py` 增加半年报页面 lint：日期、代码、事实字段、来源类型、风险提示缺失检测。
  3. 为 `report_type=财报分析/半年报/中报` 的页面增加专门规则，缺关键事实时输出 warning/error。
  4. 更新 `docs/local_knowledge_contract.md`，给 Tree Work 一份可直接照抄的半年报 ingest 模板。
- **验收方式**：
  - 新增单元测试覆盖合格半年报页面、缺代码、缺报告期、事实/观点混用、缺风险提示。
  - lint 对当前知识库 dry-run 可执行，不修改知识库。
  - 输出文档能直接发给 Tree Work 执行。
- **代码标注要求**：`# [HY-001] half_year_contract`

### HY-002: 半年报资料优先队列与 Tree Work 补录任务包（P1）
- **描述**：基于持仓、观察仓、TradeFlow 候选、研报关注度和知识过期状态，生成 Tree Work 半年报补录优先队列。
- **优先级**：P1
- **状态**：done -- commit 9b84bf1
- **前置条件**：HY-001、KB-005、KB-012 完成。
- **执行约束**：
  - 只读 TA 数据库和 `~/Documents/knowledge/`。
  - 不抓取付费研报正文。
  - 不输出交易建议，只输出“需要补录什么资料”。
- **实现要点**：
  1. 新增脚本/模块生成 `docs/knowledge_reports/half_year_tree_work_tasks-YYYY-MM-DD.md`。
  2. 优先级排序：已持仓 > 观察仓 > 昊天主候选 > TradeFlow 主候选 > 研报关注度高但知识过期。
  3. 每条任务包含 symbol/name、为什么需要补录、缺哪些 HY-001 字段、建议来源类型。
  4. 对已有 wiki 页面输出“补字段”而不是重复新建。
- **验收方式**：
  - fixture 覆盖持仓、观察仓、候选池、知识缺失、知识过期五类。
  - 生成报告不含长原文，不含强买卖词。
  - 输出可被 OpenClaw/Tree Work 直接复制执行。
- **代码标注要求**：`# [HY-002] half_year_task_pack`

### HY-003: 半年报事实表本地索引与只读查询 provider（P1）
- **描述**：从 Tree Work 已消化的半年报 wiki 中抽取结构化事实表，提供 TA/TradeFlow 可复用的只读查询接口。
- **优先级**：P1
- **状态**：done -- commit 678b234
- **前置条件**：HY-001、KB-010 完成。
- **执行约束**：
  - 只读本地知识库，不写生产 DB。
  - 仅抽取摘要字段和数值事实，不输出长原文。
  - 字段来源必须保留页面路径、来源类型和报告期。
- **实现要点**：
  1. 新增 `half_year_knowledge_provider` 或在 `local_knowledge_provider` 中增加半年报 facts 查询。
  2. 输出结构至少包含：报告期、披露日期、营收/利润/毛利率/现金流、分业务事实、管理层表述、风险、来源路径、stale 状态。
  3. 接入 KB-010 cache/freshness，避免每次全量扫描。
  4. 对缺字段、过期、事实冲突输出 `data_status`，不得假装可用。
- **验收方式**：
  - fixture 覆盖有事实、无半年报、过期、字段冲突、缓存损坏重建。
  - 查询结果可 JSON 序列化。
  - 不影响现有 local knowledge 普通查询。
- **代码标注要求**：`# [HY-003] half_year_facts_provider`

### HY-004: TA 报告接入“半年报事实对照”区块（P1）
- **描述**：TA 分析报告中新增“半年报事实对照”区块，把 Tree Work 半年报事实作为背景证据展示，但不越权改变强动作门禁。
- **优先级**：P1
- **状态**：done -- commit 87ebda2
- **前置条件**：HY-003、KB-003、REPORT-UX-004 完成。
- **执行约束**：
  - 本地知识只能作为背景补充，不得替代行情/资金/公告原文。
  - 数据不足时只能提示缺口，不能把缺口包装成结论。
  - 不改 prompts。
- **实现要点**：
  1. 在 report service attach 链路中追加 `half_year_facts_summary`、`half_year_facts_block`、`half_year_facts_status`。
  2. 区块明确分为：事实、管理层表述、待验证事项、风险。
  3. 与 `data_blockers`/`wait_reason_codes` 解耦，避免“知识命中”覆盖“数据不足观察”。
  4. 前端/接口字段保持向后兼容。
- **验收方式**：
  - 历史报告回放验证：有半年报事实时只增加区块，不改变原 decision/action_label。
  - 无半年报事实时显示缺口，不误判 failed。
  - 数据不足报告仍保留原缺口原因。
- **代码标注要求**：`# [HY-004] half_year_report_block`

### HY-005: 旧研报观点 vs 半年报事实反证检测（P1）
- **描述**：检测 Tree Work 旧研报/评分表中的核心观点是否被最新半年报事实支持、削弱或打脸，形成“反证提醒”。
- **优先级**：P1
- **状态**：done -- commit b5db468
- **前置条件**：HY-003、KB-007、KB-009 完成。
- **执行约束**：
  - 不做投资结论，只做证据一致性标注。
  - 不调用 live LLM。
  - 不把单一指标变化扩大成“逻辑破坏”，必须输出证据等级。
- **实现要点**：
  1. 设计 `thesis_check_status`: `supported` / `weakened` / `contradicted` / `insufficient_data`。
  2. 对营收、利润、现金流、毛利率、分业务进展、风险暴露做基础规则校验。
  3. 输出 `contradiction_flags` 和 `needs_tree_work_review`。
  4. 与 KB-007 research attention 结合：关注度高但事实反证强的股票优先提醒。
- **验收方式**：
  - fixture 覆盖支持、削弱、打脸、缺数据四类。
  - 报告不出现强买卖词。
  - 反证提醒能进入 HY-004 区块或本地知识摘要。
- **代码标注要求**：`# [HY-005] thesis_fact_check`

### HY-006: TradeFlow/昊天候选接入半年报因子与降权规则（P1）
- **描述**：让昊天左侧候选和 TradeFlow 候选展示半年报事实摘要，并在半年报事实明显削弱逻辑时降低候选优先级。
- **优先级**：P1
- **状态**：done -- commit 2225e51
- **前置条件**：HY-003、H-017、KB-004 完成。
- **执行约束**：
  - 半年报知识不能单独把弱候选提升为主候选。
  - 只允许降权/解释/研究优先级调整；强买卖仍由现有门禁决定。
  - 数据缺失不得惩罚过度，只标 `needs_research_review`。
- **实现要点**：
  1. 候选详情增加 `half_year_fact_score`、`half_year_fact_summary`、`half_year_risk_flags`。
  2. 对 `contradicted/weakened` 输出降权原因。
  3. 对 HY-003 有 fresh facts 的候选，提高“研究优先级”但不直接改变 action tier。
  4. 前端显示简短摘要和“需 Tree Work 复核”标签。
- **验收方式**：
  - 强知识 + 弱技术不能升主候选。
  - 半年报反证强时候选优先级下降。
  - 无半年报数据时只提示缺口。
- **代码标注要求**：`# [HY-006] tradeflow_half_year_factor`

### HY-007: investment-controller 半年报 briefing payload 与去噪规则（P2）
- **描述**：让 investment-controller 的盘前/盘后 briefing 能读取半年报事实摘要和反证提醒，但只做调度提醒，不替 TA 下结论。
- **优先级**：P2
- **状态**：done — base commit `5463b16`；24 小时去重、冲突降级与混合 fresh/conflict 补修已通过最终 Codex review
- **前置条件**：HY-004、IC-TA-004、KB-006 完成。
- **执行约束**：
  - 不直接输出补仓/清仓等动作。
  - P2/P3 仅进入日报；P0/P1 才触发盘中提醒。
  - 同一 symbol 同一事实 24 小时内去重。
- **实现要点**：
  1. IC context 增加 `half_year_facts` bucket。
  2. briefing payload 增加“半年报事实更新/反证提醒/需要 TA 复核”三类。
  3. 缺数据只记录，不推送噪音。
  4. 与观察仓/持仓优先级联动。
- **验收方式**：
  - fixture 覆盖持仓、观察仓、候选池三类。
  - 去噪规则生效。
  - payload 不包含长原文和强动作词。
- **代码标注要求**：`# [HY-007] controller_half_year_briefing`

### HY-008: 半年报知识链路端到端回放验收（P2）
- **描述**：端到端验收 Tree Work 半年报 wiki → TA 报告 → TradeFlow 候选 → investment-controller briefing 的整条链路。
- **优先级**：P2
- **状态**：done — HY-008-20260713-190756，24 tests passed
- **前置条件**：HY-004、HY-005、HY-006、HY-007 完成。
- **执行约束**：
  - fixture/dry-run，禁止 live LLM。
  - 不写生产数据库。
  - 重点检查字段、来源、状态、去噪和不越权。
- **实现要点**：
  1. 构造 4 个场景：事实支持、事实削弱、事实打脸、无半年报。
  2. 验证 TA 报告区块、TradeFlow 候选字段、IC briefing payload 一致。
  3. 生成 `docs/knowledge_reports/half_year_e2e_acceptance-YYYY-MM-DD.md`。
  4. 回归验证本地知识不会覆盖数据不足原因和最终动作语义。
- **验收方式**：
  - 新增端到端测试通过。
  - 验收报告能回答：事实是什么、来源在哪里、旧逻辑是否被支持/削弱、是否需要 Tree Work/TA 复核。
  - 无强动作词新增。
- **代码标注要求**：`# [HY-008] half_year_e2e_acceptance`

### AUTO-006: Codex review 超时 watchdog 与收口策略（P1）
- **描述**：修复自动开发中 Codex review 过长或卡住时的收口策略，避免 review 进程本身阻塞后续任务或留下锁/脏状态。
- **优先级**：P1
- **状态**：done -- commit f06793e
- **前置条件**：AUTO-004 完成。
- **背景**：复杂文档/代码 review 可能超过 5 分钟；如果外层 timeout 先杀进程，容易留下半成品、锁文件和未归档 review。
- **执行约束**：
  - 不降低“commit 前必须 review”的硬规则。
  - 不把超时 review 当 PASS。
  - 不吞掉 review 输出；必须归档到 `docs/reviews/`。
- **实现要点**：
  1. 为 `codex review --uncommitted` 增加可配置超时与 graceful terminate/killpg。
  2. 超时时任务标记 `NEEDS_HUMAN`，并恢复/保留现场说明，不继续下一个任务。
  3. 自动开发日报展示 review 耗时、是否超时、是否已有 partial output。
  4. 增加 shell/python 测试或 dry-run fixture，覆盖 review timeout、正常 PASS、FAIL 三类。
- **验收方式**：
  - 模拟 review 超时不会 commit。
  - 模拟 review PASS 才能进入 commit。
  - 锁文件与 task_runs/reviews 归档完整。
- **代码标注要求**：`# [AUTO-006] codex_review_watchdog`

### KB-013: 半年报 fixture 样本集与契约回放基线（P1）
- **描述**：为 HY 系列准备不依赖真实知识库变动的半年报 fixture 样本，提前固定字段、状态和失败路径。
- **优先级**：P1
- **状态**：done [PASS_UNREVIEWED]
- **前置条件**：KB-002、KB-012 完成。
- **执行约束**：
  - 不修改 `~/Documents/knowledge/`。
  - fixture 不包含长篇研报/财报原文。
  - 不调用 live LLM。
- **实现要点**：
  1. 新增 fixture 页面/字符串：合格半年报、缺报告期、事实/观点混用、过期、旧观点被削弱。
  2. 将 fixture 接入 KB-002 lint 或新增专用测试 helper。
  3. 输出一份 `docs/knowledge_reports/half_year_fixture_baseline.md`，说明未来 HY 任务可复用的样本。
  4. 统一 symbol/name/source_type/financial_period 的字段命名。
- **验收方式**：
  - fixture 测试可独立运行。
  - 不依赖真实知识库内容。
  - 可被 HY-001/HY-003/HY-005 后续任务复用。
- **代码标注要求**：`# [KB-013] half_year_fixture_baseline`

### KB-014: 研报/财报来源可信度分层与 citation policy（P1）
- **描述**：建立本地知识和 TA 报告引用来源的可信度分层，明确公告/财报原文、券商研报、媒体观点、用户笔记的使用边界。
- **优先级**：P1
- **状态**：done — Codex review 补修完成，91 KB-014 tests + 581 KB 组合回归通过
- **前置条件**：DATA-025、KB-002 完成。
- **执行约束**：
  - 不抓取新研报正文。
  - 不把“来源可信度”直接转成交易动作。
  - 不改 prompts。
- **实现要点**：
  1. 定义 `source_quality_tier`: `original_filing` / `official_notice` / `broker_research` / `media` / `user_note` / `unknown`。
  2. 在 local knowledge lint/provider 中识别或透传来源层级。
  3. 输出 citation policy 文档：哪些能作为事实、哪些只能作为观点/线索。
  4. 对缺来源或来源弱的页面降低 confidence/attention，而不是直接过滤。
- **验收方式**：
  - fixture 覆盖五类来源。
  - 报告/摘要中能显示来源层级。
  - 弱来源不会被当作公告/财报事实。
- **代码标注要求**：`# [KB-014] citation_policy`

### DATA-027: 免费研报/公告/半年报源 smoke 扩展与失败归因（P2）
- **描述**：在 DATA-025 基础上扩展免费源 smoke，覆盖研报元数据、公告/半年报披露、接口空结果和失败原因归因。
- **优先级**：P2
- **状态**：done — commit 559e95b + 4e7f5d3；Codex review 通过，103 tests passed
- **前置条件**：DATA-023、DATA-025 完成。
- **执行约束**：
  - 默认 fixture/dry-run，不做大批量抓取。
  - 不下载或提交 PDF 正文。
  - 不把无研报当失败；必须区分 NORMAL_NO_DATA 和 FAILED。
- **实现要点**：
  1. 扩展 `research_report_sources` 或新增 smoke 脚本，覆盖 Eastmoney/AKShare 研报元数据与公告披露入口。
  2. 输出 vendor、endpoint、字段、限制、失败类型。
  3. 生成 `docs/data_source_reports/research-source-smoke-YYYY-MM-DD.md`。
  4. 与 DATA-023 能力矩阵字段保持一致。
- **验收方式**：
  - fixture 覆盖有数据、无数据、接口失败、字段缺失。
  - smoke 报告不含正文，只含元数据与状态。
  - 不触发 live LLM。
- **代码标注要求**：`# [DATA-027] research_source_smoke`

### REPORT-UX-005: 本地知识补充不覆盖动作语义的扩展回放（P2）
- **描述**：扩展 REPORT-UX-004 的回放范围，验证本地知识/研报关注度/未来半年报事实不会把最终动作重新压成笼统“观察”。
- **优先级**：P2
- **状态**：done -- commit 4c91d02
- **前置条件**：REPORT-UX-004、KB-003 完成。
- **执行约束**：
  - 不改 prompts。
  - 不调用 live LLM。
  - 不写生产 DB。
- **实现要点**：
  1. 增加 action semantics fixture：WAIT/ENTER/HOLD/REDUCE/EXIT 各一类。
  2. 叠加 local_knowledge/research_attention/half_year_facts mock 字段，验证语义不被覆盖。
  3. 对“数据不足观察”要求输出原因码，不允许只剩笼统 action_label。
  4. 生成简短回放报告。
- **验收方式**：
- 新增回放测试通过。
- 本地知识强命中不会把 ENTER/HOLD/REDUCE 覆写成 WAIT。
- 数据不足时有明确 wait_reason_codes。
- **代码标注要求**：`# [REPORT-UX-005] knowledge_action_semantics_replay`

### KB-015: 研报观点事实分离与 TA 可消费摘要索引（P1）
- **描述**：把 Tree Work 已消化研报中的“观点、事实、预测、风险、待验证事项”拆成 TA 可消费的结构化摘要，避免研报观点被误当成公告/财报事实。
- **优先级**：P1
- **状态**：done -- commit d3ecc0f
- **前置条件**：KB-014、KB-003、HY-001 完成。
- **执行约束**：
  - 只读 `~/Documents/knowledge/`，不得修改知识库。
  - 不抓取新研报正文，不复制长篇原文。
  - 不把券商观点直接转成交易动作。
  - 不调用 live LLM。
- **实现要点**：
  1. 新增只读 provider 或索引 helper，输出每篇 investment wiki 的 `research_claims`、`reported_facts`、`forecast_items`、`risk_items`、`verification_needs`。
  2. 每条记录必须带 `symbol/name/source_path/source_quality_tier/report_date/stale_status`。
  3. 根据 KB-014 citation policy 标注 `claim_type`: `fact / opinion / forecast / risk / unknown`。
  4. 输出 `docs/knowledge_reports/research_fact_opinion_index-YYYY-MM-DD.md`，只展示摘要和路径，不展示长正文。
  5. 与 KB-010 cache/freshness 兼容，索引损坏时可重建。
- **验收方式**：
  - fixture 覆盖事实、观点、预测、风险、缺来源五类。
  - 真实知识库 dry-run 可执行，不写入 production DB。
  - 输出结果可 JSON 序列化，并可被 TA/TradeFlow 后续任务复用。
  - 弱来源/过期来源只降置信，不覆盖动作语义。
- **代码标注要求**：`# [KB-015] research_fact_opinion_index`

### KB-016: 多研报一致性/分歧矩阵与关注度去重回放（P1）
- **描述**：当多份研报涉及同一只股票时，聚合它们的一致性、分歧点、过期风险和重复覆盖度，形成“研报共识/分歧矩阵”。
- **优先级**：P1
- **状态**：done -- commit 7e1c6af
- **前置条件**：KB-015、KB-007、KB-009 完成。
- **执行约束**：
  - 不把“多份研报提及”直接等同于买入信号。
  - 不复制研报正文。
  - 不调用 live LLM。
- **实现要点**：
  1. 按 symbol 聚合近 3/6/12 个月研报观点，输出 `consensus_score`、`disagreement_score`、`attention_count_effective`。
  2. 对同机构重复报告、同标题重复导入、过期观点做去重/衰减。
  3. 分歧矩阵至少包含：业绩预测分歧、产业链角色分歧、风险判断分歧、估值假设分歧。
  4. 输出 `needs_fact_check=True` 的候选，交给 HY-003/HY-005 做事实反证。
- **验收方式**：
  - fixture 覆盖一致看多、一致看空、观点分裂、重复报告、过期报告。
  - 多研报高关注只能提高研究优先级，不能绕过 TradeFlow/TA 门禁。
  - 生成报告不包含强买卖词。
- **代码标注要求**：`# [KB-016] research_consensus_matrix`

### KB-017: 研报观点 vs 公告/半年报事实 citation 审计（P1）
- **描述**：用 KB-015 的研报观点结构和 HY-003 的半年报事实表做交叉审计，标记“已被事实支持/削弱/打脸/仍待验证”的观点。
- **优先级**：P1
- **状态**：done -- commit 0f8789b
- **前置条件**：KB-015、HY-003、KB-014 完成。
- **执行约束**：
  - 只做证据一致性审计，不输出交易动作。
  - 缺半年报事实时不得强行判定观点错误。
  - 不改 prompts，不调用 live LLM。
- **实现要点**：
  1. 定义 `citation_audit_status`: `supported / weakened / contradicted / pending / insufficient_data`。
  2. 事实源优先级按 KB-014：公告/财报原文 > 官方公告 > 券商研报 > 媒体/用户笔记。
  3. 对营收、利润、现金流、毛利率、分业务进展、风险暴露做基础规则匹配。
  4. 输出可进入 TA 报告“本地知识补充/半年报事实对照”的 `audit_summary`。
- **验收方式**：
  - fixture 覆盖支持、削弱、打脸、待验证、缺事实五类。
  - 弱来源不得覆盖强来源。
  - TA 报告动作语义不被 audit 直接覆写。
- **代码标注要求**：`# [KB-017] citation_fact_audit`

### KB-018: 同股研报观点版本演化与共识漂移时间线（P1）
- **描述**：在 KB-016 共识矩阵和 KB-017 事实审计之上，按时间还原同一股票核心观点如何被强化、削弱、反转或自然过期，避免只看最新一篇研报丢失上下文。
- **优先级**：P1
- **状态**：done -- commit 0879941
- **前置条件**：KB-015、KB-016、KB-017 完成。
- **执行约束**：
  - 不调用 live LLM，不抓取新研报正文。
  - 时间线只描述观点和证据变化，不输出买卖动作。
  - 同机构重复覆盖必须去重，不能制造虚假共识增强。
- **实现要点**：
  1. 定义 `thesis_version_status`: `new / reinforced / weakened / reversed / stale / pending_fact_check`。
  2. 按 symbol、主题和核心观点生成 `research_thesis_timeline`，保留报告日期、来源路径、质量等级和 citation audit 状态。
  3. 输出 `consensus_drift_score` 与变化原因，区分真实新增证据和重复报告。
  4. 生成只含摘要和路径的时间线报告，供 TA/TradeFlow/投资总控读取。
- **验收方式**：
  - fixture 覆盖观点强化、削弱、反转、重复覆盖、过期和缺日期六类。
  - 乱序输入得到稳定时间线；弱来源不得覆盖强来源。
  - 输出不含强动作词，不改变现有 decision/action_label。
- **代码标注要求**：`# [KB-018] research_thesis_timeline`

### HY-009: 半年报增量刷新、缓存失效与事实冲突审计（P2）
- **描述**：让 HY-003 半年报事实索引能识别新披露、修订稿和知识库页面更新，安全刷新缓存并标记跨版本事实冲突。
- **优先级**：P2
- **状态**：blocked — 等待 HY-008 完成
- **前置条件**：HY-003、HY-005、HY-008 完成。
- **执行约束**：
  - 只读本地知识库，不写生产数据库。
  - 不联网抓取、不调用 live LLM。
  - 冲突未消解时必须降级为待复核，禁止静默采用较新的任意值。
- **实现要点**：
  1. 基于页面 mtime/content hash/report_period/disclosure_date 判断新增、修订、过期和删除。
  2. 缓存键加入 schema/version，损坏或协议升级时可重建。
  3. 对同报告期营收、利润、现金流、毛利率等冲突输出 `fact_conflict_flags` 和来源路径。
  4. 生成增量审计摘要，说明刷新数量、冲突数量、失效缓存和待 Tree Work 复核项。
- **验收方式**：
  - fixture 覆盖新增、修订、删除、缓存损坏、同周期冲突和无变化六类。
  - 重复执行幂等；无变化不重建全量索引。
  - 冲突事实不会进入 HAS_DATA 强结论路径。
- **代码标注要求**：`# [HY-009] half_year_incremental_refresh`

### V-014: 真实本地知识库只读 smoke 与研报主线验收日报（P2）
- **描述**：在 fixture 验收之后，对 `~/Documents/knowledge/` 做一次只读 smoke，验证真实研报/半年报能否贯穿索引、共识、反证、报告区块和 briefing 契约。
- **优先级**：P2
- **状态**：blocked — 等待 HY-009 完成；作为本批次最终验收
- **前置条件**：HY-008、HY-009、KB-016、KB-017、KB-018 完成。
- **执行约束**：
  - 只读真实知识库，不修改 Tree Work 页面，不写生产数据库。
  - 不调用 live LLM，不触发完整 TA，不联网补数据。
  - 真实目录不可用时必须失败并写明路径/权限原因，不能用 fixture 冒充真实验收。
- **实现要点**：
  1. 抽样至少覆盖多研报同股、已有半年报、无半年报和过期知识四类股票。
  2. 统计观点/事实分离覆盖率、citation 完整率、事实冲突率、待验证率和缓存新鲜度。
  3. 验证 TA/TradeFlow/IC 输出字段可消费，但不要求调用 LLM 生成新结论。
  4. 生成 `docs/knowledge_reports/research_mainline_acceptance-YYYY-MM-DD.md`，列出通过项、阻塞项和下一阶段建议。
- **验收方式**：
  - 报告明确区分 fixture PASS 与真实知识库 PASS。
  - 每个失败样本带来源路径和失败分类，不泄露长研报正文。
  - 验收不新增强动作词，不改变历史报告动作语义。
- **代码标注要求**：`# [V-014] research_mainline_live_smoke`

### KB-019: Tree Work 研报增量摄取清单与重复导入预检（P2）
- **描述**：面向半年报集中披露期，把 `inbox/raw/wiki` 的新增、已消化、重复、缺字段和待更新资料整理为可回查的增量摄取清单，避免同一研报重复消化或遗漏。
- **优先级**：P2
- **状态**：blocked — NEEDS_HUMAN, see docs/task_runs/KB-019-20260713-191013
- **前置条件**：KB-005、KB-010、KB-012 完成。
- **执行约束**：
  - 只读 `~/Documents/knowledge/`，不得移动、改写或删除 Tree Work 文件。
  - 不复制长篇研报正文，不调用 live LLM，不写生产数据库。
  - 优先复用 `tree_work_backlog.py`、`local_knowledge_cache.py` 和 `half_year_task_pack.py`，禁止另造第二套索引。
- **实现要点**：
  1. 基于相对路径、文件指纹、symbol、report_date、institution/source_url 生成稳定 `ingest_key`。
  2. 输出 `new / digested / duplicate / needs_metadata / stale / conflict` 六类状态及可解释原因。
  3. 对同一研报多文件、同机构同日同股近似标题做重复预检，但不得自动删除。
  4. 提供 CLI/JSON/Markdown 输出，并生成 `docs/knowledge_reports/research-ingest-delta-YYYY-MM-DD.md`。
- **验收方式**：
  - fixture 覆盖六类状态、乱序输入、重复执行和空目录。
  - 重复执行幂等，真实知识库 dry-run 前后文件 hash 不变。
  - 输出只含元数据、摘要和路径，不含长正文或交易动作。
- **代码标注要求**：`# [KB-019] research_ingest_delta`

### KB-020: 同股研报/半年报证据聚合只读 API（P2）
- **描述**：为单只股票提供统一的证据查询契约，把 KB-016 共识矩阵、KB-017 citation 审计、KB-018 观点时间线和 HY-003 半年报事实聚合到一个只读响应中。
- **优先级**：P2
- **状态**：done -- commit 77c8921
- **前置条件**：KB-016、KB-017、KB-018、HY-003 完成。
- **执行约束**：
  - 不调用 LLM、不抓取正文、不写数据库；只返回摘要、状态、指标和来源路径。
  - API 不输出或改写 `decision/action_label/buy_level`。
  - 固定路由必须放在动态 symbol 路由之前，并补路由顺序回归。
- **实现要点**：
  1. 新增聚合 service，统一返回 `symbol/as_of/data_status/source_freshness/consensus/citation_audit/thesis_timeline/half_year_facts/gaps/errors`。
  2. 新增 `GET /v1/knowledge/research/evidence/{symbol}`，支持可选 `as_of` 与窗口参数，非法 symbol 明确 4xx。
  3. 子模块失败按桶降级，不能因为一项失败把其他证据全部清空。
  4. 对来源路径做知识根目录内校验，响应不得泄露 API key 或文件正文。
- **验收方式**：
  - fixture 覆盖完整、部分缺失、冲突、过期、单桶异常和非法 symbol。
  - API schema、service、路由 smoke 和 JSON 序列化测试通过。
  - 旧 `/v1/knowledge/local/search` 契约无回归。
- **代码标注要求**：`# [KB-020] research_evidence_api`

### UI-014: TA 研报证据中心与来源下钻（P2）
- **描述**：在报告查看体验中增加轻量“研报证据”入口，让用户看到同股研报共识、分歧、半年报事实、待验证项和来源路径，而不是只看到一个知识分数。
- **优先级**：P2
- **状态**：blocked — 等待 KB-020 完成并人工确认 API 契约
- **前置条件**：KB-011、KB-020、REPORT-UX-005 完成。
- **执行约束**：
  - 复用现有 ReportViewer/抽屉/Tab 风格，不新建重型独立应用。
  - 首屏懒加载，失败不阻塞报告正文；不显示长篇研报原文。
  - 证据颜色表达支持/削弱/冲突/待验证，不映射成买卖颜色或动作。
- **实现要点**：
  1. API client/type 接入 KB-020 契约。
  2. 展示摘要：有效研报数、机构去重数、共识/分歧、最新半年报期、冲突/待验证数。
  3. 下钻列表展示观点时间线、citation 状态、来源等级、报告日期和可复制相对路径。
  4. 空数据、知识库禁用、部分失败、加载中均有明确状态。
- **验收方式**：
  - 前端类型检查与 build 通过。
  - 组件测试或静态契约测试覆盖完整/空/部分失败/冲突四态。
  - 移动端和桌面端不溢出，报告主体不因接口慢而阻塞。
- **代码标注要求**：`// [UI-014] research_evidence_center`

### HY-010: 持仓/观察仓半年报待更新清单（P2）
- **描述**：把持仓、观察仓和昊天候选映射到半年报知识覆盖状态，输出“哪些票已有新事实、哪些仍缺半年报、哪些存在冲突或待 Tree Work 消化”的优先清单。
- **优先级**：P2
- **状态**：done -- commit 1b1b639
- **前置条件**：HY-003、HY-007、TRACK-001 完成；HY-009 完成后再补增量冲突字段，不阻塞本任务基础版。
- **执行约束**：
  - 只读持仓/观察仓/候选和本地知识，不写生产数据库、不调用 LLM。
  - 优先级只表示资料补全顺序，不输出交易动作。
  - 缺披露日时标记 unknown，不得猜日期。
- **实现要点**：
  1. 统一 symbol universe，并保留 `holding / observation / mandate_candidate` 来源。
  2. 输出 `up_to_date / missing / stale / conflict / needs_digest / unknown` 状态。
  3. 排序优先级：持仓冲突 > 持仓缺失 > 接近触发观察仓 > 昊天主候选 > 其他。
  4. 提供 CLI/JSON/Markdown 摘要，供 investment-controller 后续只读消费。
- **验收方式**：
  - fixture 覆盖三类来源、重复 symbol、无知识根、冲突和未知日期。
  - 不读取密钥、不写 DB，重复执行稳定。
  - 输出能回答“今晚优先消化哪几份资料以及为什么”。
- **代码标注要求**：`# [HY-010] half_year_update_queue`

### V-015: 研报增量摄取→证据 API→前端→待更新清单端到端验收（P2）
- **描述**：对 KB-019/KB-020/UI-014/HY-010 做最终 fixture + 真实知识库只读验收，确认半年报集中导入时链路可用、可追溯、不会影响交易动作。
- **优先级**：P2
- **状态**：blocked — 等待 KB-019、KB-020、UI-014、HY-010 完成
- **前置条件**：KB-019、KB-020、UI-014、HY-010 完成。
- **执行约束**：
  - fixture 与真实只读 smoke 分开报告；真实目录不可用不得伪造 PASS。
  - 不调用 live LLM、不写生产数据库、不修改知识库、不触发完整 TA。
  - 不修改 prompts，不新增强动作词。
- **实现要点**：
  1. 回放新增研报、重复研报、缺元数据、半年报修订、事实冲突五类。
  2. 验证 ingest_key/status → 证据 API → 前端契约 → 待更新队列字段一致。
  3. 验证局部失败、缓存损坏和空目录均可解释降级。
  4. 生成 `docs/knowledge_reports/research-operations-acceptance-YYYY-MM-DD.md`。
- **验收方式**：
  - 定向回归、API smoke、前端 build 全部通过。
  - 验收报告列出通过项、失败样本、来源路径和人工下一步。
  - 确认 `decision/execution_action/action_label` 在全链路前后不变。
- **代码标注要求**：`# [V-015] research_operations_e2e`

### AUTO-007: 自动开发依赖感知领取与阻塞任务自动解锁（P1）
- **描述**：让任务领取器真正理解依赖关系，避免把尚未满足前置条件的下游任务提前标成 ready，也避免前项完成后仍需人工改状态。
- **优先级**：P1
- **状态**：done — 依赖领取、自动解锁、dry-run 与显式状态优先级均已闭环；最终补修 `623f45c`
- **前置条件**：AUTO-006 完成。
- **执行约束**：
  - 不降低 dirty-tree、测试、Codex review、失败即停四道门禁。
  - 不把依赖缺失当 PASS，不自动释放 `NEEDS_HUMAN` 或战略暂停任务。
  - 兼容没有机器依赖字段的历史任务。
- **实现要点**：
  1. 为新任务支持机器可读 `depends_on`（逗号分隔任务 ID）和 `auto_release=true/false` 元数据，不能只解析自然语言。
  2. picker 仅在所有依赖详情状态为 done 时领取 queued/blocked-auto 任务；缺失、循环依赖、状态不一致均停止并归档原因。
  3. 当前任务完成后重新计算可领取项，不要求手工改下游 ready。
  4. dry-run 展示依赖阻塞原因、可释放任务和排序，不修改 TASKS.md。
- **验收方式**：
  - fixture 覆盖依赖完成、未完成、缺失、循环、NEEDS_HUMAN、战略暂停和旧格式。
  - 模拟 HY-008 done 后 HY-009 可领取，HY-008 未完成时 HY-009 不可领取。
  - shell syntax、dry-run、锁恢复和失败即停回归通过。
- **代码标注要求**：`# [AUTO-007] dependency_aware_claim`

### REPORT-UX-006: TA 报告知识证据来源卡与缺口解释（P2）
- **描述**：基于现有 `local_knowledge_summary` 与 `half_year_facts_summary`，在 TA 报告中清晰显示“用了哪些本地资料、资料是否过期/冲突、还缺什么”，不等待新的聚合 API。
- **优先级**：P2
- **状态**：done -- commit b4fa3bc
- **前置条件**：KB-011、HY-004、REPORT-UX-005 完成。
- **执行约束**：
  - 复用 ReportViewer，不新增重型页面；不展示长正文。
  - 来源状态不得映射成买卖动作，不改变 `decision/execution_action/action_label`。
  - 慢查询或缺知识库不阻塞报告主体。
- **实现要点**：
  1. 增加轻量来源卡：命中文档数、来源等级、最新日期、半年报期、fresh/stale/conflict/disabled。
  2. 缺口解释至少区分：无命中、缺来源、过期、事实冲突、待 Tree Work 消化、知识库禁用。
  3. 来源路径仅显示知识根目录内相对路径，可复制但不直接暴露任意绝对路径。
  4. 旧报告字段缺失时保持兼容空态。
- **验收方式**：
  - 前端 build/typecheck 通过。
  - 契约/组件测试覆盖完整、空、过期、冲突、禁用和旧报告六态。
  - 报告动作语义回放无变化。
- **代码标注要求**：`// [REPORT-UX-006] knowledge_evidence_card`

### HY-011: 半年报报告期/披露日/修订版本元数据 sanity check（P2）
- **描述**：在大量半年报进入 Tree Work 前，对报告期、披露日期、修订版本、symbol/name 和来源类型做轻量校验，阻止错期、未来日期和修订稿覆盖原稿等元数据污染。
- **优先级**：P2
- **状态**：done — 超时半成品已人工收口；366 项定向/回归测试通过，真实知识库 115 页只读 smoke 无 HYM 误报且前后哈希一致，独立 Codex review 无 correctness finding
- **前置条件**：HY-001、KB-014、KB-002 完成。
- **执行约束**：
  - 只做 lint/sanity，不修改知识库文件，不调用 LLM。
  - 不猜缺失日期；缺失与非法必须分开。
  - 修订稿优先级只用于事实索引选择，不生成交易动作。
- **实现要点**：
  1. 校验 `financial_period/period_end_date/disclosure_date/source_quality_tier/symbol/name` 的格式与组合关系；`report_date` 保留给资料发布日期，不作财务期末日。
  2. 识别未来披露日、报告期晚于披露日、非半年报周期、symbol/name 错配、同周期多版本无修订标记。
  3. 输出 `error/warning/info` 与稳定 rule_id，并接入现有 local knowledge lint 报告。
  4. 对修订稿保留原始/修订来源路径，不静默覆盖。
- **验收方式**：
  - fixture 覆盖合法、缺失、非法日期、未来日期、错周期、错 symbol、修订稿和多版本冲突。
  - 与 KB-002/HY-001 现有 lint 回归兼容。
  - 真实知识库 dry-run 只读且报告不含正文。
- **代码标注要求**：`# [HY-011] half_year_metadata_sanity`

## FUND. 财报真实性与基本面语义门禁（2026-07-14 新增）

> 背景：`603629.SH` 在 2026-07-14 的基本面报告把利通电子误写为化工企业，并把 2025 全年累计营收当作 2025Q4。原始财务数字多数可追溯，但公司身份、期间语义和异动原因未被系统约束，形成“真数字 + 假叙事”。本任务线先修事实和语义门禁，再讨论更换模型。

### FUND-001: 标的身份与公司画像硬门禁（P0）
- **描述**：为基本面分析建立权威、结构化、可追溯的标的身份契约；公司画像缺失或代码/名称/交易所冲突时停止商业模式分析，禁止模型从财务特征猜行业。
- **优先级**：P0
- **状态**：done — 本轮直接开发，待专项 Codex review/提交
- **前置条件**：DATA-004、E-002、KB-014 完成。
- **depends_on**：DATA-004, E-002, KB-014
- **auto_release**：false
- **执行约束**：
  - 不调用 live LLM，不写生产数据库，不修改 `tradingagents/prompts/`。
  - 公司名称、交易所、主营业务、行业必须携带来源与状态；弱来源只能补充，不能覆盖官方身份。
  - AKShare 公司画像失败但财务摘要成功时，必须标记“部分成功/画像缺失”，不得把整项 fundamentals 当成完整 `HAS_DATA`。
- **实现要点**：
  1. 定义 `instrument_identity`/`company_profile` 结构：`symbol/security_name/exchange/main_business/industry/source/status/as_of`。
  2. 修正 A 股 `instrument_context.security_name` 仅等于代码的问题，优先使用缓存代码表与权威 profile；解析失败时保留显式 unknown。
  3. 基本面 Agent 调用前执行 identity gate；缺主营/行业时只允许输出财务事实，不允许输出商业模式、行业周期和竞争格局判断。
  4. 扩展 E-002：除错误公司名外，增加“身份缺失”和“主营/行业无证据”状态，但不依靠正则猜行业。
  5. `raw_evidence` 单独记录 company profile 的成功、部分成功、失败与 fallback，不泄漏 API key。
- **验收方式**：
  - fixture 覆盖正确身份、代码名称冲突、profile 主源失败 fallback 成功、全部失败、ETF/港股边界。
  - 603629 无画像时不能生成“化工品/危化物流/消费电子”等未经证实的主营描述。
  - 身份失败时报告进入明确 blocker，不得以普通 `completed + 中置信度` 冒充可用。
- **代码标注要求**：`# [FUND-001] instrument_identity_gate`

### FUND-002: 财务期间口径结构化与单季度确定性计算（P0）
- **描述**：把利润表/现金流量表的累计期与资产负债表的时点口径结构化，所有同比、环比和单季度值由程序计算，禁止将年度累计值直接当 Q4。
- **优先级**：P0
- **状态**：done — 本轮直接开发，待专项 Codex review/提交
- **前置条件**：DATA-004、HY-011 完成。
- **depends_on**：DATA-004, HY-011
- **auto_release**：false
- **执行约束**：
  - 不调用 LLM、不联网、不写生产数据库、不改 prompts；使用 fixture 和纯确定性计算。
  - 保留原始累计值、来源、单位和报告期；派生值必须标记公式与输入 evidence ids。
  - 缺任一输入时返回 `FIELD_MISSING`，不得猜值或把全年值复制成 Q4。
- **实现要点**：
  1. 定义 `period_scope`: `Q1_YTD/H1_YTD/Q3_YTD/FY_YTD/SINGLE_QUARTER/POINT_IN_TIME`。
  2. 程序计算 Q2=`H1-Q1`、Q3=`Q3YTD-H1`、Q4=`FY-Q3YTD`；同比/环比基于同口径值计算。
  3. 输出给基本面 Agent 的表格明确展示 `period_scope/is_derived/formula/source/unit`，不再只给 `报告日`。
  4. 对收入、营业成本、净利润、扣非净利润、经营现金流等核心指标做单位与方向 sanity check。
  5. 保留 AKShare 原始表，结构化层作为可回放副产物，不改变 provider 原始记录。
- **验收方式**：
  - 用 603629 fixture 断言 2025Q4 营收来自 2025FY 减 2025Q3，而不是 33.074 亿元全年值。
  - 覆盖缺 Q3、负值、修订数据、跨年度、现金流累计和资产负债表时点六类边界。
  - 所有派生值可追溯且重复执行幂等。
- **代码标注要求**：`# [FUND-002] financial_period_normalizer`

### FUND-003: 财务异动官方解释与会计口径证据上下文（P0）
- **描述**：为收入、毛利率、利润、现金流和负债异常建立“公司解释优先”上下文，识别净额法/总额法、主要责任人/代理人、预收款、合同负债和集中交付；无强来源时只输出“原因未确认”。
- **优先级**：P0
- **状态**：done — 本轮直接开发，待专项 Codex review/提交
- **前置条件**：FUND-001、FUND-002、KB-014、HY-003 完成。
- **depends_on**：FUND-001, FUND-002, KB-014, HY-003
- **auto_release**：true
- **执行约束**：
  - 不把新闻、券商观点或用户笔记当作公司解释；事实优先级沿用 KB-014。
  - 不修改 `tradingagents/prompts/`；先通过结构化上下文与确定性 post-check 落地。若确需改 prompt，另行人工审批。
  - 不抓取付费研报正文，不调用 live LLM，不写生产数据库。
- **实现要点**：
  1. 从公告/财报事实/半年报事实中抽取 `management_explanations` 与 `accounting_policies`，每项带来源、日期、证据等级和关联指标。
  2. 对异常指标只允许三态：`officially_explained / evidence_conflict / unexplained`。
  3. 识别关键词与结构字段：净额法、总额法、主要责任人、代理人、预收款、合同负债、设备经销、集中交付。
  4. 将正确的公司画像、官方解释和低置信本地知识分层传给基本面 Agent；禁止跨层覆盖。
  5. 公告查询为正常无数据时标记 `NORMAL_NO_DATA`，不得把 `No announcements found` 记作 `HAS_DATA`。
- **验收方式**：
  - 603629 fixture 在有官方说明时能识别算力/设备经销与合同负债；无说明时不猜“原材料下降/化工淡旺季”。
  - 覆盖强弱来源冲突、无公告、净额法、总额法和合同负债五类场景。
- **代码标注要求**：`# [FUND-003] official_explanation_context`

### FUND-004: 基本面语义质量门禁与 C-006 真实接线（P0）
- **描述**：把现有空调用的 C-006 财务异常检测接入结构化事实，并增加身份、期间、计算和因果声明门禁；基础语义失败时报告必须进入 `NEEDS_REVIEW/INVALID`，不能只禁止强动作。
- **优先级**：P0
- **状态**：done — 本轮直接开发，待专项 Codex review/提交
- **前置条件**：FUND-001、FUND-002、FUND-003、C-006、D-002 完成。
- **depends_on**：FUND-001, FUND-002, FUND-003, C-006, D-002
- **auto_release**：true
- **执行约束**：
  - 复用并收口 C-006，不建立第二套同名财务异常引擎。
  - 门禁是确定性规则，不调用 LLM、不改 prompts、不写生产数据库测试数据。
  - 语义失败不得用“降低置信度”掩盖；必须有稳定 blocker code 和失败证据。
- **实现要点**：
  1. 把毛利率、经营现金流、净利润、资产负债率、投资/筹资现金流等真实参数传入 `check_financial_anomalies()`，删除当前全 `None` 空调用。
  2. 增加 blocker：`IDENTITY_UNVERIFIED/PERIOD_SCOPE_INVALID/DERIVATION_CONFLICT/CAUSE_UNSUPPORTED/ACCOUNTING_POLICY_UNKNOWN`。
  3. 校验核心计算：毛利率、同比/环比、流动/速动比率；合同负债不得自动等同于有息债务。
  4. `source_coverage` 不再只按报告字符串非空计分；公司画像缺失、公告零记录、结构化期间失败必须反映到质量状态。
  5. 基本面块无效时，研究经理/风控必须明确剔除该块权重并输出人工复核，而不是继续引用错误叙事。
- **验收方式**：
  - 注入 2026-07-14 的错误 603629 基本面文本，至少命中身份/期间/因果 blocker，禁止进入可执行完成态。
  - 覆盖数值正确但叙事错误、数值与公式冲突、公告正常无数据、C-006 异常真实触发和旧报告兼容。
- **代码标注要求**：`# [FUND-004] fundamental_semantic_gate`

### FUND-005: 逐 Agent 模型与输入契约脱敏 trace（P1）
- **描述**：记录每个 Agent 实际使用的 provider、模型、tier、运行时间和输入证据状态，使事后可以确认“哪一个模型基于哪些数据生成了哪一段报告”。
- **优先级**：P1
- **状态**：done — 本轮直接开发，待专项 Codex review/提交
- **前置条件**：PERF-003、DATA-004 完成。
- **depends_on**：PERF-003, DATA-004
- **auto_release**：false
- **执行约束**：
  - 绝不记录 API key、Authorization、cookie、完整 prompt、长篇原文或本机敏感绝对路径。
  - 不调用 live LLM；用 mock client/fixture 验证。
  - 新字段可选，旧报告/API 保持兼容。
- **实现要点**：
  1. `analyst_traces` 增加 `provider/model/model_tier/prompt_version/input_contract_digest/input_statuses`。
  2. `input_contract_digest` 只保存字段名、状态、来源和哈希，不保存正文。
  3. 报告 metadata 保存运行级配置快照，避免只能根据用户当前配置反推历史模型。
  4. 对 provider fallback/模型 fallback 明确记录 requested 与 actual。
- **验收方式**：
  - fixture 证明 fundamentals 使用 mid tier 时能记录实际模型；用户事后改配置不影响历史 trace。
  - 脱敏扫描确保无 key/token/Authorization/长 prompt。
- **代码标注要求**：`# [FUND-005] agent_model_input_trace`

### FUND-006: 603629 与跨行业财报对抗回放验收（P1）
- **描述**：对 FUND-001~005 做端到端回放，证明系统能拒绝“真数字 + 假公司/假期间/假原因”，再决定是否需要更换基本面模型。
- **优先级**：P1
- **状态**：done — 本轮直接开发，待专项 Codex review/提交
- **前置条件**：FUND-001、FUND-002、FUND-003、FUND-004、FUND-005 完成。
- **depends_on**：FUND-001, FUND-002, FUND-003, FUND-004, FUND-005
- **auto_release**：true
- **执行约束**：
  - 第一阶段只做 fixture/历史报告回放，不调用 live LLM、不写生产数据库、不改 prompts。
  - 模型 A/B 属于第二阶段，必须由用户确认 provider、模型和预计调用次数后另行执行。
- **实现要点**：
  1. 回放 603629 历史报告中“化工、危化物流、消费电子、工程项目”等互相冲突的主营猜测。
  2. 增加至少 5 个跨行业样本：制造、软件/算力、金融、周期、ETF；覆盖累计报表、净额法、合同负债和无公告。
  3. 断言错误报告被门禁拒绝，正确的财务事实仍被保留，不因一处 blocker 丢失全部原始证据。
  4. 生成 `docs/knowledge_reports/fundamental-integrity-acceptance-YYYY-MM-DD.md`，区分系统修复结果与模型能力结论。
- **验收方式**：
  - 对抗回放全部通过；603629 不再出现未经证实的化工/危化物流描述，不再把全年值当 Q4。
  - 输出能回答：身份来自哪里、期间如何计算、原因由谁披露、哪个 Agent/模型使用了哪些证据、为何被阻断。
- **代码标注要求**：`# [FUND-006] fundamental_integrity_replay`

## FUND-R. 财报真实性门禁补修链（2026-07-15 Codex review 新增）

> 审核基线：第一版 FUND-001~006 的 103 项专项测试与 226 项 API/readiness/graph 回归均通过，但生产格式与图执行顺序仍有 4 个 P1 correctness finding。补修期间不得把第一版标成可提交完成，也不得消耗 live LLM 重跑 603629。

### FUND-001A: 生产公司画像契约与 provider 格式补修（P0）
- **描述**：修复公司画像只支持 Markdown 表格、无法消费 `cn_astock` 列表格式，以及常用主源缺主营业务导致 A 股长期全量降级的问题。
- **优先级**：P0
- **状态**：done — 真实巨潮宽表、主源全失败 fallback、跨源冲突截断、占位符容错和字段优先级均已通过对抗复审
- **预计耗时**：20-30 分钟（仅 Round 4 收口，不扩大范围）
- **depends_on**：FUND-001
- **auto_release**：false
- **实现约束**：
  - 优先建立 provider-native 结构化 `company_profile` 契约；展示层 Markdown 只作兼容 fallback，不作为唯一真源。
  - 同时兼容 `cn_akshare` 的 `item/value` 表格与 `cn_astock` 的 `- **字段**: 值` 格式；代码、名称、交易所必须交叉校验。
  - 多个 `Company Profile` 来源必须独立解析后再合并；证券代码不一致时返回 `IDENTITY_CONFLICT`，禁止从冲突来源补入名称、行业或主营。
  - 同一代码的公司全称/简称差异不得误报冲突；`主营业务` 必须优先于宽泛的 `经营范围`。
  - 主营业务缺失时保持 `PARTIAL`，不得从行业或财务比率猜主营；但不得因为解析器不兼容让本可取得的名称/行业丢失。
  - 补官方/强来源主营业务 fallback；ETF、港股和无主营字段标的使用明确的 instrument-type 边界，不冒充普通 A 股公司。
- **验收方式**：
  - 用两个生产 provider 的原样输出 fixture 验证，不得只用手写理想表格。
  - 603629 必须稳定得到代码 `603629.SH`、名称“利通电子”和可追溯行业/主营；缺主营时只降级对应字段。
  - 覆盖主源成功、主源部分成功、fallback 成功、代码冲突、ETF、港股六类场景。
  - 新增对抗样本：东财 `603629` + 巨潮 `600000` 必须 `IDENTITY_CONFLICT` 且 `commercial_analysis_allowed=false`；同代码正常补主营必须保持 `HAS_DATA`。
  - `pytest tests/test_fund001_instrument_identity.py -q` 与相关 provider 回归通过。

### FUND-003A: 财务因果声明与官方证据逐项绑定（P0）
- **描述**：把当前“公告出现任意关键词即可放行整段解释”改成 claim-level 证据绑定，防止无关合同负债公告放行“原材料下降/化工旺季/净额法”等错误叙事。
- **优先级**：P0
- **状态**：done — `aac9add` + `f5fc13f`，由 FUND-003A-B commit `2d98abc` 完成复审收口
- **预计耗时**：35-45 分钟
- **depends_on**：FUND-001A, FUND-003, KB-014
- **auto_release**：true
- **实现约束**：
  - 每条解释输出稳定 `claim_id/metric/cause_terms/policy_terms/evidence_ids/source_type/as_of/status`。
  - 报告中的每个因果或会计口径声明必须命中同指标、同语义的强证据；“合同负债”不得证明“净额法”，“预收款”不得证明“原材料下降”。
  - 支持 `officially_explained/evidence_conflict/unexplained` 的逐 claim 状态，不得用一个全局布尔值替代。
  - 无匹配公告时 `fundamental_explanations` 的 raw-evidence 状态必须是 `NORMAL_NO_DATA`，不得固定写 `HAS_DATA`。
- **验收方式**：
  - 新增对抗测试：公告仅含“合同负债增加”，报告写“化工旺季/原材料下降”必须命中 `CAUSE_UNSUPPORTED`。
  - 公告仅含“合同负债”，报告写“采用净额法”必须命中 `ACCOUNTING_POLICY_UNKNOWN`。
  - 正确的“预收款/合同负债推动经营现金流”可以通过，且保留具体 evidence id。

### FUND-003A-B: 因果证据按术语/出现位置绑定补修（P0）
- **描述**：收口 `aac9add` + `f5fc13f` 的复审问题，将证据方向、否定和因果关系从 entry 全局属性下沉到具体术语及每次出现，防止多指标/多句子误绑定。
- **优先级**：P0
- **状态**：done — commit `2d98abc`；72 项专项、271 项聚焦回归通过，Codex review 无 P0/P1/P2 correctness finding
- **预计耗时**：35-50 分钟
- **depends_on**：FUND-001A
- **auto_release**：true
- **实现约束**：
  - 按具体术语及出现 span 保存 `direction/negation/relation/evidence_id`，不得用 entry 级单值支持多个指标。
  - 扫描同一术语的每次出现；冲突、模糊或未形成同一因果关系时 fail closed。
  - cause/accounting 同义词在证据侧与 claim 侧共用同一规范化映射。
  - `claim_id` 同时具备确定性和发生次数区分能力；审计片段必须展示该 evidence 支持的具体术语与语义。
- **验收方式**：
  - 覆盖“中性首次出现+后文上涨”、“未采用净额法+仍采用总额法”、“原材料下降+合同负债增加”、同指标相反声明、原因不同但共享指标、多术语长证据六类对抗样例。
  - `pytest tests/test_fund003_fund004_integrity.py tests/test_fund006_integrity_replay.py tests/test_data_collector.py tests/test_data004_evidence_contract.py tests/test_readiness_score.py -q`
  - `codex review --uncommitted` 无 P0/P1/P2 correctness finding；不调 live LLM、不写生产数据库。

### FUND-004B: C-006 同日期/同期间口径计算补修（P0）
- **描述**：修复 C-006 按指标各取最新值导致收入、成本、资产和负债跨日期或跨累计/单季度口径混算的问题。
- **优先级**：P0
- **状态**：done -- commit 33fa819
- **预计耗时**：30-40 分钟
- **depends_on**：FUND-002, FUND-003A-B
- **auto_release**：true
- **实现约束**：
  - 按 `(report_date, period_scope, unit)` 建立完整指标组；毛利率只使用同组收入和营业成本。
  - 利润表/现金流比较必须同 scope；`FY_YTD` 不得与 `SINGLE_QUARTER` 互作前后期。
  - 资产负债率只使用同一报告日的 `POINT_IN_TIME` 资产与负债。
  - 若找不到完整同口径组，返回 `None/FIELD_MISSING`，不得拼接邻近日期。
  - 明确单位归一化，保留原始单位和选择依据供 trace/测试审计。
- **验收方式**：
  - 复现审核样本时不得再产生 `88%/-500%` 的虚假毛利率。
  - 覆盖同日重复累计值+单季度值、缺成本、跨日期、跨单位、修订值和资产负债表六类边界。

### FUND-004A: 基本面语义门禁前移并剔除无效研究权重（P0）
- **描述**：把语义门禁从最终 Risk Judge 前移到基本面 Agent 输出后；无效基本面不得继续污染 Bull/Bear、Research Manager、Trader 和记忆。
- **优先级**：P0
- **状态**：done -- commit d250dd8
- **预计耗时**：40-50 分钟
- **depends_on**：FUND-001A, FUND-003A-B, FUND-004B, FUND-004
- **auto_release**：true
- **实现约束**：
  - 基本面 Agent 输出后立即生成结构化 integrity result；保留可追溯财务事实与原始报告，但决策输入使用 `fundamentals_report_for_decision` 或等价安全字段。
  - `NEEDS_REVIEW/INVALID` 时 Bull/Bear/Research Manager 必须看到明确“该模块不参与方向权重”，不得继续引用被拒绝叙事。
  - 最终 Risk Judge 继续保留 D-002 强动作门禁作为第二道防线。
  - 无效叙事不得写入长期投资记忆；旧报告/API 保持兼容。
- **验收方式**：
  - 图级测试注入“603629 是化工股、Q1 化工淡季”，断言 Bull/Bear prompt 与研究经理决策输入不含该叙事，只含 blocker 和保留财务事实。
  - 断言最终动作门禁失败、报告状态明确为 `NEEDS_REVIEW`，而非普通完成态。

### FUND-005A: 逐 Agent 真实模型与运行时 trace 补修（P1）
- **描述**：修复 `actual_model=requested_model` 的静态回填，补齐真实运行模型、fallback 和运行时间证据。
- **优先级**：P1
- **状态**：done -- commit fad65d9
- **预计耗时**：25-35 分钟
- **depends_on**：FUND-004A, FUND-005, PERF-003
- **auto_release**：true
- **实现约束**：
  - 从实际创建/调用的 client 或响应 metadata 获取 provider/model；无法确认时写 `unknown`，不得用 requested 冒充 actual。
  - 记录 `requested_model/actual_model/model_tier/fallback_from/started_at/finished_at/latency_ms`。
  - 不记录 key、URL 凭据、完整 prompt、长原文或本机敏感绝对路径。
- **验收方式**：mock 覆盖无 fallback、模型 fallback、provider fallback、actual 不可知和历史配置变化五类场景。

### FUND-006A: 真实 provider 格式与生产图离线回放验收（P1）
- **描述**：用真实 provider 输出形态和完整 LangGraph 顺序重验 FUND 补修链；这是 live 603629 前的唯一放行门。
- **优先级**：P1
- **状态**：done -- commit 3655339
- **预计耗时**：40-50 分钟
- **depends_on**：FUND-001A, FUND-003A-B, FUND-004A, FUND-004B, FUND-005A
- **auto_release**：true
- **实现约束**：
  - 使用脱敏的 `cn_akshare` 表格、`cn_astock` 列表、无关公告、累计/单季度混合数据 fixture。
  - 覆盖 DataCollector → Fundamentals Analyst → Bull/Bear → Research Manager → Trader → Risk Judge 全链路，不调用 live LLM。
  - 排除 4 份仅生成时间变化的 TradeFlow 验收文档；SCORE-001 与 FUND 补修精确拆分提交。
- **验收方式**：
  - 专项、API/readiness/graph 回归和 `git diff --check` 全过，独立 Codex review 无 P0/P1/P2 correctness finding。
  - 验收报告回答身份来源、期间公式、claim-evidence 绑定、研究权重剔除、实际模型 trace 五项。
  - 通过后只把“真实重跑 603629”改为 `blocked-human` 待用户确认；不得自动消耗模型 Token。

### FUND-007A: 财报错误模式基准集与持续回归报告（P2）
- **描述**：把 603629 及跨行业财报回放中已经暴露的错误模式固化为确定性基准集和回归报告，使后续 provider、解析器或模型路由调整都能先证明没有重新引入"数字大体正确、因果解释错误"的故障。
- **优先级**：P2
- **状态**：done -- commit 208bd4f
- **预计耗时**：30-40 分钟
- **depends_on**：FUND-006A
- **auto_release**：true
- **实现约束**：
  - 复用 FUND-001A~006A 已有 fixture、完整性结果和生产图离线回放，不复制第二套身份、期间或因果判定器。
  - 基准至少覆盖：证券身份错配、年度累计冒充 Q4、跨日期/跨单位计算、无官方解释的季节性猜测、净额法/总额法误判、预收款与经营现金流误读、来源缺失和证据冲突。
  - 每个 case 必须保存稳定 `case_id/input_fixture/expected_gate/expected_rule_ids/actual_result`；失败时指出具体规则，不只输出 PASS/FAIL。
  - 同时提供正向控制样本，确保合法主营补全、同口径计算和有官方证据的因果解释不会被误杀。
  - 生成机器可读结果和简洁 Markdown 报告；不得包含 API Key、完整研报原文、本机敏感路径或交易动作建议。
  - 只运行离线 fixture；不调用 live provider、live LLM，不写生产数据库，不修改 prompts。
- **验收方式**：
  - 新增独立 benchmark runner 或等价测试入口，一条命令可重复执行并返回非零失败码。
  - 至少 8 个负向样本和 3 个正向控制样本全部符合预期；故意篡改 expected rule 时测试必须失败。
  - 运行 FUND-001A~006A 相关回归、`git diff --check` 和独立 Codex review；无 P0/P1/P2 correctness finding 才可标记 done。

## SCORE. 研究评分快照接入与四卡裁决链（2026-07-13 新增）

> 目标：知识库只提供慢变量 `research_score_snapshot`（研究证据可信度、投资逻辑质量及变化原因）；TradeFlow 继续负责实时入场时机，账户上下文负责组合适配，TA 统一执行硬门禁并映射到七阶段。禁止生成一个混合总分，也禁止知识分直接覆盖动作语义。
>
> **代码归属边界**：`research-scorer` 的公式、证据条目、投资假设和正式快照写入属于 ZCode 知识库接入项目，不在本仓库实现。本仓库的 SCORE-001~006 只负责读取、校验、动态补齐和裁决。现有 Tree Work 知识页继续作为历史上游输入，不做全局改名；新增评分与快照发布统一由 ZCode 负责。知识库侧任务包见 `docs/zcode_research_scorer_handoff.md`。

### SCORE-001: TA 只读 research_score_snapshot 契约与安全接入（P1）
- **描述**：建立 TA 侧只读快照 loader/provider，使 ZCode 发布的 `research_score_snapshot` v1.1.0 可被稳定读取和降级，但本任务不接 API/前端/TradeFlow，不重新计算知识库分数，也不接受知识库给出的交易动作。
- **优先级**：P1
- **状态**：done — commit 87dcd44 + 24881f6，73 tests passed
- **前置条件**：KB-001、KB-014 完成；ZCode v1.1.0 Schema/validator/fixture 已人工确认。
- **depends_on**：KB-001, KB-014
- **auto_release**：false
- **执行约束**：
  - 只读知识库；不修改知识库文件，不写生产数据库，不调用 live LLM。
  - 生产读取范围只允许 `research_score_snapshots/<symbol>/` 的正式快照；必须排除 `research_score_snapshots/drafts/`，不存在正式快照时返回显式无数据状态。
  - 快照只允许研究字段；输入含 `action/execution_action/playbook_stage/planned_position/buy_level/risk_level/entry_timing/portfolio_fit` 时 fail closed 并记录 schema warning，禁止静默消费。
  - 不用 `0` 代替“缺证据/不可评分”；必须区分 `HAS_DATA / STALE / LOW_CONFIDENCE / NORMAL_NO_DATA / FAILED`。
  - 不建立第二套 A-E 来源等级；复用 KB-014 `source_quality_tier` 与 citation 权重。
  - 不复制 ZCode 评分公式；TA 只校验消费者所需的 v1.1.0 契约和跨字段安全条件。
- **实现要点**：
  1. 定义版本化消费者模型：`schema_version=1.1.0`、`symbol/name/as_of/created_at/source_cutoff_at/rubric_id/rubric_version/status`。
  2. 只承载两项核心分数、`thesis_breakdown`、`theses`、`score_change`、`evidence_refs`、缺失证据及升级/降级/证伪条件；`investment_thesis.status` 支持 `active/weakening/invalidated/insufficient_evidence`。
  3. 增加本地只读 loader/provider；按 `symbol + as_of <= analysis_time` 选择最新合法正式快照，校验路径边界、symbol、时区日期、分数范围、Schema/rubric 版本、thesis 证据闭包和禁止字段。
  4. 快照过期、损坏、标的不匹配、版本未知、未来数据或 `$ref`/结构不合法时 fail closed，只返回降级状态，不得沿用上一只股票或默认高分。
  5. fixture 从 ZCode 已验收契约复制为测试专用样本，并保留来源说明；603629 `drafts/` 草案只用于验证“生产 loader 不得读取草案”。
  6. 不实现证据分/逻辑分公式；不在本任务新增 API、数据库列、TradeFlow 字段或动作映射。
- **验收方式**：
  - fixture 覆盖五类状态、完整/缺字段、过期、损坏、symbol 错配、未知版本、非法分数、未来快照、悬空 evidence ref、invalidated thesis 和夹带动作字段。
  - 断言 drafts 不会被生产 loader 选中；同标的多版本严格按 analysis_time 选择，未来快照不穿越。
  - 断言 provider 不写知识库/DB、不调用 LLM；重复读取幂等，路径逃逸/符号链接逃逸失败关闭。
  - 断言知识快照极高分时 `decision/execution_action/action_tier` 仍不发生变化。
- **代码标注要求**：`# [SCORE-001] research_score_snapshot_contract`

### SCORE-001B: 研究快照接入聚合响应与 TradeFlow candidate detail（P1）
- **描述**：在 SCORE-001 loader 验收后，把只读摘要接入 KB-020 聚合响应、TA metadata 和 TradeFlow candidate detail；本任务只透传研究卡，不参与候选原分、动作或阶段计算。
- **优先级**：P1
- **状态**：done -- commit f366724
- **前置条件**：SCORE-001、KB-020 完成。
- **depends_on**：SCORE-001, KB-020
- **auto_release**：true
- **执行约束**：
  - API 只输出必要摘要、证据引用和缺口，不返回整篇研报正文或本机绝对路径。
  - 新字段全部可选；无正式快照时保持旧 API/前端兼容，不输出伪造 0 分。
  - 不改候选原始 score、Opportunity/Action Tier、decision 或 execution_action。
- **实现要点**：
  1. 输出稳定 `research_score_snapshot` 只读桶，包含版本、状态、两项分数、theses 摘要、delta 原因、evidence refs 和缺口。
  2. 路径只允许知识根目录相对路径；API 层过滤绝对路径、token、cookie、key 等敏感信息。
  3. 在 TA metadata、KB-020 与 candidate detail 复用同一序列化函数，避免三套字段漂移。
- **验收方式**：
  - 覆盖有快照、无快照、LOW_CONFIDENCE、FAILED、旧候选无字段和敏感路径过滤。
  - API schema/服务层/前端类型回归通过；接入前后候选排序和动作完全一致。
- **代码标注要求**：`# [SCORE-001B] research_score_snapshot_api_adapter`

### SCORE-002: 复用 TradeFlow 现有因子生成 entry_timing 评分卡（P1）
- **描述**：把 TradeFlow 已有技术、资金、事件、拥挤/过热、触发距离和赔率字段组合成独立 `entry_timing` 评分卡，禁止另造一套行情和资金计算。
- **优先级**：P1
- **状态**：done — SCORE-002-20260723-023857
- **前置条件**：SCORE-001、TF-QUALITY-004、TF-RISK-001 完成。
- **depends_on**：SCORE-001, TF-QUALITY-004, TF-RISK-001
- **auto_release**：true
- **执行约束**：
  - 不调用 LLM、不联网、不改 prompts；纯确定性映射。
  - 复用 `technical_score/fund_flow_score/event_score/risk_penalty_score/data_quality_score`、触发距离、Observe 状态和现有 Opportunity/Action Tier 规则。
  - 资金单位未校验或数据失败时不得输出“资金确认充分”；缺数据必须降低时机置信度而非伪造中性满分。
- **实现要点**：
  1. 输出 `entry_timing` 0–100 和分项：估值预期位置、拥挤度、资金价格确认、催化有效性、下行赔率。
  2. 每个分项携带 `status/source_fields/reasons/missing_fields`，可追溯到现有候选字段。
  3. 第一次大跌、跌停未打开、爆量破位、板块退潮作为时机风险标志，不在本任务直接改最终动作。
  4. API/前端只增加可选字段，保留原有分项和 action tier。
- **验收方式**：
  - fixture 覆盖高分确认、过热、无资金、单位未校验、首次大跌、跌停和字段全缺。
  - 相同原始字段得到稳定分数；不得因研究快照高分抬高 `entry_timing`。
- **代码标注要求**：`# [SCORE-002] entry_timing_adapter`

### SCORE-003: 账户上下文 portfolio_fit 评分卡（P1）
- **描述**：基于账户权限、真实持仓、可用现金、行业集中度、标的流动性和风险预算生成独立 `portfolio_fit`，回答“这只票是否适合当前账户参与”。
- **优先级**：P1
- **状态**：ready
- **前置条件**：SCORE-001、C-002、TF-RISK-001、IC-TA-001 完成。
- **depends_on**：SCORE-001, C-002, TF-RISK-001, IC-TA-001
- **auto_release**：true
- **执行约束**：
  - 只读 holdings/账户能力/观察仓/风险预算；不修改真实持仓、不生成委托。
  - 真实账户上下文缺失时状态为 `unknown`，不得假定有权限、有现金或零持仓。
  - 模拟账本与真实持仓必须标明来源，禁止混用。
- **实现要点**：
  1. 输出 `portfolio_fit` 0–100 及六分项：交易权限、仓位与上限、现金防守空间、相关性集中度、流动性执行难度、账户风险预算。
  2. 同时输出布尔/三态事实：`tradable_by_user/position_overweight/insufficient_cash/concentration_exceeded/risk_budget_exceeded/context_unknown`。
  3. 对科创板/创业板权限、当前单票占比、同主题暴露、最低一手成本和可用现金做确定性校验。
  4. 评分只描述适配度；硬性阻断由 SCORE-004 集中裁决。
- **验收方式**：
  - fixture 覆盖不可交易、空仓现金足、已超配、现金不足、高相关集中、持仓未知及模拟/真实来源冲突。
  - 任意缺省上下文不会被解释为“适配良好”。
- **代码标注要求**：`# [SCORE-003] portfolio_fit_card`

### SCORE-004: 八类硬性否决统一门禁（P0）
- **描述**：把研究证据、TradeFlow 时机、账户适配和既有强动作门禁汇总成单一 `vetoes` 裁决层；评分再高也不得绕过否决项。
- **优先级**：P0
- **状态**：blocked-auto — 等待 SCORE-002/SCORE-003
- **前置条件**：SCORE-002、SCORE-003、D-002、D-003、PLAYBOOK-001 完成。
- **depends_on**：SCORE-002, SCORE-003, D-002, D-003, PLAYBOOK-001
- **auto_release**：true
- **执行约束**：
  - 必须复用现有 `source_coverage/evidence_coverage`、Buy/Risk Level、C-001/C-002 和强动作 sanitizer，不平行重写。
  - 门禁只收紧、不放宽既有 70%/85% 阈值；冲突时采用更保守规则并记录来源。
  - 不改 prompts、不调用 LLM、不把知识库分数当执行证据覆盖率。
- **实现要点**：
  1. 证据覆盖任一核心门禁低于 70%，禁止强动作；Level 4 继续要求 source/evidence 均达到 85% 及既有全部条件。
  2. 用户不可买时禁止 ENTER/试错/确认/进攻；当前持仓超配时禁止继续增加风险暴露；现金不足时禁止新增试错。
  3. 第一次大跌或跌停未打开时禁止进攻仓；仅传闻/弱来源时禁止确认仓；核心逻辑被公告澄清或事实反证时进入风控/退出候选。
  4. 输出稳定 `veto_code/severity/evidence_refs/blocked_actions/reason`，保留所有命中的否决项而非只返回第一项。
  5. 门禁优先级固定：核心假设证伪 > 持仓重大风险 > 权限/账户否决 > 证据否决 > 时机否决。
- **验收方式**：
  - 对八类否决逐项 fixture，并覆盖多否决并发、字段未知、旧报告和重复执行。
  - 对抗测试：四卡均 100 分仍不能绕过任一硬门禁。
  - 无持仓时不得输出 REDUCE/EXIT 持仓动作；逻辑退出语义需安全映射为 WAIT/移出观察。
- **代码标注要求**：`# [SCORE-004] unified_veto_gate`

### SCORE-005: 四卡结果到七阶段与动作语义的确定性映射（P1）
- **描述**：在 SCORE-004 后，把研究分、入场时机、组合适配、分数变化和否决结果映射到 PLAYBOOK-001 七阶段，同时保留 WAIT/ENTER/HOLD/REDUCE/EXIT 为底层兼容动作。
- **优先级**：P1
- **状态**：blocked-auto — 等待 SCORE-004
- **前置条件**：SCORE-004、PLAYBOOK-001、DECISION-004 完成。
- **depends_on**：SCORE-004, PLAYBOOK-001, DECISION-004
- **auto_release**：true
- **执行约束**：
  - 本任务只做阶段与动作语义，不实现计划仓位百分比或三笔法数量；PLAYBOOK-002/003 继续战略暂停。
  - 未知阶段不得回退为 `hold`；缺持仓不得生成持仓动作。
  - 知识分不能单独触发 `trial/confirm/attack`，价格上涨也不能单独触发确认。
- **实现要点**：
  1. 研究分高但时机差：未持仓映射 `observe + WAIT`；已持仓且无风险映射 `hold + HOLD`。
  2. 研究分高、时机合格、组合允许且无 veto：可依次进入 `trial/confirm/attack`；确认必须有产业/客户订单/业绩至少两类证据增强，进攻必须已有确认状态及明确撤退条件。
  3. 研究分下降、冲突/反证增加：已持仓映射 `risk + REDUCE`，未持仓保持 `observe + WAIT` 并阻断进入。
  4. 核心假设证伪：已持仓映射 `exit + EXIT`；未持仓映射 `exit + WAIT` 并移出研究/观察候选，不违反 C-001。
  5. 输出 `playbook_stage/playbook_action_label/execution_action/stage_reason_codes/upgrade_conditions/downgrade_conditions/invalidation_conditions`。
- **验收方式**：
  - 覆盖用户给出的四类映射，以及持仓/未持仓、分数不变/上升/下降、冲突和证伪组合。
  - 回放 WAIT/ENTER/HOLD/REDUCE/EXIT，旧字段兼容且不会再次全部退化为“观察”。
- **代码标注要求**：`# [SCORE-005] four_card_stage_resolver`

### SCORE-006: 研究评分接入端到端对抗回放（P1）
- **描述**：从知识快照读取到 TradeFlow 评分、账户适配、硬门禁、七阶段、TA 报告/API/前端做完整回放，证明分层职责和失败降级真实生效。
- **优先级**：P1
- **状态**：blocked-human — 等待 SCORE-005 与 ZCode 发布至少一份真实 `research_score_snapshot` 后人工释放
- **前置条件**：SCORE-005、REPORT-UX-006、V-014 完成；ZC-RS-005 与 ZC-RS-006 由 ZCode 交付并提供至少一份真实快照，经人工确认。
- **depends_on**：SCORE-005, REPORT-UX-006, V-014
- **auto_release**：false
- **执行约束**：
  - 先 fixture，再真实知识库只读 smoke；真实目录不可用不得用 fixture 冒充通过。
  - 不调用 live LLM、不写生产数据库、不修改知识库、不改 prompts。
  - 验收采用实现、用户操作、风控/数据真实性三视角；Codex 必须对抗审查。
- **实现要点**：
  1. 至少回放：高研究低时机、高研究好时机但无权限、高分但超配/缺现金、仅传闻、首次大跌、研究分下降、核心逻辑证伪、数据缺失八类。
  2. 验证知识快照不携带/不覆盖动作，动态数据变化只影响 timing/fit，静态研究变化有 delta 与原因。
  3. 报告能同时回答四卡分数、否决项、当前阶段、动作、升级/降级/证伪条件和数据缺口。
  4. 生成 `docs/knowledge_reports/research-score-integration-acceptance-YYYY-MM-DD.md`。
- **验收方式**：
  - 定向测试、API schema、前端 typecheck/build、旧报告回放及 `git diff --check` 全部通过。
  - 四卡全高 + 任一 veto 仍不得进入禁止阶段；评分缺失不出现伪精确 0 分或默认 HOLD。
- **代码标注要求**：`# [SCORE-006] research_score_integration_e2e`

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
- **状态**：done — `tradingagents/default_config.py` 已接入 `account_capability`，既有回归持续覆盖
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
