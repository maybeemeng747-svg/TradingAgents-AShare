# DATA-P1-SOURCE-GAP-AUDIT: Simon 数据源吸收落地差距审计

> 生成日期：2026-06-04
> 任务编号：DATA-P1-SOURCE-GAP-AUDIT
> 前置条件：DATA-001（source_catalog）✓、DATA-004（raw_evidence contract）✓

---

## 1. 审计目标

对照 SimonLin1212 `a-stock-data` / A股数据 Skill 的核心思路（端点目录、vendor fallback、实时补丁、来源溯源、字段契约），审计本项目已吸收和仍未落地的数据源能力，输出可执行差距清单。

## 2. 已落地能力总览

| 能力 | 落地状态 | 关键文件 | 对应任务 |
|------|----------|----------|----------|
| 数据源能力目录 (source_catalog) | ✅ 已落地 | `tradingagents/dataflows/source_catalog.py` | DATA-001 |
| Vendor 注册与 provider registry | ✅ 已落地 | `tradingagents/dataflows/interface.py` | DATA-001 |
| route_to_vendor fallback 链路 | ✅ 已落地（含失败字符串检测） | `tradingagents/dataflows/interface.py` | DATA-P0-FUND-ROUTE |
| raw_evidence 来源契约 | ✅ 已落地 | `tradingagents/dataflows/evidence_contract.py` | DATA-004 |
| Fixture replay 测试框架 | ✅ 已落地 | `tradingagents/dataflows/fixture_replay.py` | DATA-005 |
| 数据源质量日报 | ✅ 已落地 | `tradingagents/dataflows/data_source_daily_digest.py` | DATA-006 |
| Live smoke 测试（环境变量门控） | ✅ 已落地 | `tradingagents/dataflows/live_smoke.py` | DATA-P1-ASTOCK-LIVE-SMOKE |
| 事件源归一化 | ✅ 已落地 | `tradingagents/tradeflow/event_source.py` | DATA-003 |
| 实时行情 freshness 补丁 | ✅ 已落地 | `tradingagents/dataflows/evidence_contract.py` | DATA-002 |
| 失败字符串假成功修复 | ✅ 已落地 | `tradingagents/dataflows/interface.py` `_is_failure_result()` | DATA-P0-FUND-ROUTE |
| 龙虎榜与资金流解耦 | ✅ 已落地 | `tradingagents/graph/data_collector.py` `_compute_lhb_force_decision()` | DATA-P1-LHB-FUND-DECOUPLE |
| cn_astock provider（Eastmoney 直连） | ✅ 已落地 | `tradingagents/dataflows/providers/cn_astock_provider.py` | DATA-P0-603629 |
| 资金流单位统一（万元） | ✅ 已落地 | `cn_astock_provider.py` `/10000` 转换 | DATA-P0-FUND-ROUTE |

---

## 3. 数据类型落地矩阵

对每类数据按 7 个维度审计：source_catalog 登记、provider 实现、route fallback、raw_evidence 进入、报告/前端展示、fixture 覆盖、live smoke 覆盖。

### 3.1 OHLCV / 行情 / 实时 Quote

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `OHLCV`（6 entries）+ `REALTIME_QUOTES`（3 entries），fallback priority 明确 |
| provider 实现 | ✅ | cn_akshare（Eastmoney/Sina/Tencent 内部 fallback）+ cn_astock（Baidu/Eastmoney）+ cn_baostock + yfinance + alpha_vantage |
| route fallback | ✅ | cn_akshare → cn_baostock → yfinance → cn_astock → alpha_vantage |
| raw_evidence | ✅ | `stock_data` + `indicators` + `vpa_indicators` 三个 evidence key |
| 报告/前端 | ✅ | G-005 实时补丁标注 `is_realtime_patched=True` |
| fixture | ✅ | 4 个：normal_quote / stale_daily / realtime_success / realtime_failure |
| live smoke | ⚠️ 部分 | live smoke 仅覆盖 `realtime_quotes`（Tencent），未覆盖 OHLCV 历史数据端点 |

**风险**：低。历史 OHLCV fixture 覆盖充分，但 live smoke 不验证 OHLCV 端点可用性。

---

### 3.2 个股资金流

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `FUND_FLOW`：cn_akshare(p1) + cn_astock(p2) |
| provider 实现 | ✅ | cn_akshare `get_individual_fund_flow()` + cn_astock `get_individual_fund_flow()` |
| route fallback | ✅ | AKShare 失败 → cn_astock Eastmoney push2his（DATA-P0-FUND-ROUTE 修复） |
| raw_evidence | ✅ | `fund_flow_individual` key，含 `vendor/unit/unit_verified/fallback_from` |
| 报告/前端 | ✅ | 单位万元标注，fallback vendor 溯源可见 |
| fixture | ✅ | `fund_flow_unit_anomaly`：astock fallback 返回元级，检测 unit_verified=False |
| live smoke | ✅ | `cn_astock/push2his_fund_flow` 端点 |

**风险**：低。DATA-P0-FUND-ROUTE 修复后 fallback 链路可靠。

---

### 3.3 板块资金流

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `BOARD_FUND_FLOW`：cn_akshare(p1,p2) + cn_astock(p3) |
| provider 实现 | ✅ | cn_akshare `get_board_fund_flow()` + cn_astock `get_board_fund_flow()` |
| route fallback | ✅ | cn_akshare 优先 → cn_astock |
| raw_evidence | ✅ | `fund_flow_board` key |
| 报告/前端 | ✅ | 在 TA 报告中展示 |
| fixture | ⚠️ 无专项 | 无独立板块资金 fixture，仅在 normal_quote 中覆盖 |
| live smoke | ❌ | 未覆盖 |

**风险**：中。板块资金数据在 TA 分析中作为宏观/行业参考，但无专项 fixture 和 live smoke 验证 fallback 链路。

---

### 3.4 龙虎榜 (LHB)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `LHB`：cn_akshare(p1) + cn_astock(p2) |
| provider 实现 | ✅ | cn_akshare `get_lhb_detail()` + cn_astock `get_lhb_detail()` |
| route fallback | ✅ | cn_akshare → cn_astock |
| raw_evidence | ✅ | `lhb` key，4 态：NOT_QUERIED / NORMAL_NO_DATA / FAILED / HAS_DATA |
| 报告/前端 | ✅ | 4 态显示语义区分（中性/红色），force reason 标注 |
| fixture | ✅ | `lhb_no_trigger`：force=False 返回 NOT_QUERIED |
| live smoke | ✅ | `cn_astock/datacenter_lhb` 端点 |

**风险**：低。DATA-P1-LHB-FUND-DECOUPLE 修复后资金流与龙虎榜完全解耦。

---

### 3.5 融资融券 (Margin Trading)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ❌ | 无 `DataType` 枚举值 |
| provider 实现 | ❌ | 无任何 provider 实现融资融券接口 |
| route fallback | ❌ | 未接入路由 |
| raw_evidence | ❌ | 无 evidence key |
| 报告/前端 | ❌ | 不展示 |
| fixture | ❌ | 无 |
| live smoke | ❌ | 无 |

**风险**：**高**。融资融券是 A 股重要杠杆指标，影响风险判断和情绪分析。Simon `a-stock-data` 已覆盖此端点。当前系统完全缺失。

**建议任务**：DATA-010 融资融券数据源注册与 raw_evidence 接入。

---

### 3.6 公告 / 研报 / 评级 / 回购

#### 3.6.1 公告 (Announcements)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `NOTICE`：cn_astock(p1, CNInfo) + cn_akshare(p2) |
| provider 实现 | ✅ | cn_astock `get_announcements()`（CNInfo） |
| route fallback | ✅ | cn_astock → cn_akshare |
| raw_evidence | ✅ | `announcements` key |
| 报告/前端 | ✅ | 在 TA 报告和昊天雷达中展示 |
| fixture | ✅ | `announcement_failure`：CNInfo ConnectionError |
| live smoke | ✅ | `cn_astock/cninfo_announcements` 端点 |

**风险**：低。

#### 3.6.2 研报 (Research Reports)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `REPORT`：cn_astock(p1, Eastmoney reportapi) |
| provider 实现 | ⚠️ 部分 | cn_astock 有 `get_research_reports()` 方法，但未在 `TOOLS_CATEGORIES` 注册 |
| route fallback | ❌ | 未接入 `route_to_vendor()` |
| raw_evidence | ❌ | 未被 `data_collector._fetch_all()` 收集 |
| 报告/前端 | ❌ | 不进入 TA 报告 raw_evidence |
| fixture | ❌ | 无 |
| live smoke | ❌ | 无 |

**风险**：**中高**。source_catalog 已登记但 provider 方法未接入路由/数据收集管线。研报数据在 Simon Skill 中是独立端点，可提供券商观点和目标价。

**建议任务**：DATA-011 研报端点接入 route_to_vendor 与 raw_evidence。

#### 3.6.3 评级 (Ratings)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ❌ | 无 `DataType` 枚举值 |
| provider 实现 | ⚠️ 部分 | 仅通过 `event_source.py` 调用 `ak.stock_rank_foreforecast_cninfo()` |
| route fallback | ❌ | event_source 不走 provider 路由 |
| raw_evidence | ❌ | 不进入 raw_evidence，仅进入 TradeFlow 事件流 |
| 报告/前端 | ⚠️ 部分 | 仅在 TradeFlow 昊天雷达事件中可见，TA 报告不引用 |
| fixture | ❌ | 无 |
| live smoke | ❌ | 无 |

**风险**：**中**。评级数据对中线研究有价值，但当前仅在事件流中作为信号，不进入 TA 分析链路。

**建议任务**：DATA-012 评级数据接入 provider 路由与 raw_evidence。

#### 3.6.4 回购 (Buybacks)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ❌ | 无 `DataType` 枚举值 |
| provider 实现 | ⚠️ 部分 | 仅通过 `event_source.py` 调用 `ak.stock_repurchase_em()` |
| route fallback | ❌ | event_source 不走 provider 路由 |
| raw_evidence | ❌ | 不进入 raw_evidence，仅进入 TradeFlow 事件流 |
| 报告/前端 | ⚠️ 部分 | 仅在 TradeFlow 昊天雷达事件中可见 |
| fixture | ❌ | 无 |
| live smoke | ❌ | 无 |

**风险**：**中**。回购是重要的公司行为信号，但与评级类似，仅走事件流。

**建议任务**：DATA-013 回购数据接入 provider 路由与 raw_evidence。

---

### 3.7 新闻 / 政策事件

#### 3.7.1 新闻 (News)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `NEWS`：cn_akshare(p1) + cn_astock(p2) |
| provider 实现 | ✅ | cn_akshare `get_news()` + cn_astock `get_news()` |
| route fallback | ✅ | cn_akshare → cn_astock |
| raw_evidence | ✅ | `news` key |
| 报告/前端 | ✅ | 在 TA 报告中展示 |
| fixture | ⚠️ 部分 | 无独立新闻 fixture，仅在 normal_quote 中隐含 |
| live smoke | ❌ | 未覆盖 |

**风险**：低中。fallback 链路存在，但缺少专项 fixture 和 live smoke。

#### 3.7.2 全球新闻/政策事件

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `GLOBAL_NEWS`：cn_akshare(p1, CCTV) + cn_astock(p2 CLS + p3 Eastmoney 7x24) |
| provider 实现 | ✅ | cn_akshare `get_global_news()` + cn_astock `get_global_news()` |
| route fallback | ✅ | cn_akshare → cn_astock（CLS + Eastmoney 7x24） |
| raw_evidence | ✅ | `global_news` key |
| 报告/前端 | ✅ | 在宏观分析中引用 |
| fixture | ❌ | 无专项 fixture |
| live smoke | ❌ | 无 |

**风险**：中。政策事件对昊天雷达核心（中线政策验证），但缺少 fixture 和 live smoke 验证。

**建议任务**：DATA-014 新闻/政策事件 fixture 与 live smoke 补充。

---

### 3.8 财务数据 (Financials)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `FINANCIALS`：cn_akshare(p1,p2) + cn_astock(p3) + cn_baostock(p4) |
| provider 实现 | ✅ | cn_akshare + cn_astock + cn_baostock + yfinance + alpha_vantage |
| route fallback | ✅ | cn_akshare → cn_astock → cn_baostock → yfinance → alpha_vantage |
| raw_evidence | ✅ | `fundamentals` + `balance_sheet` + `cashflow` + `income_statement` |
| 报告/前端 | ✅ | 在 TA 报告中展示 |
| fixture | ⚠️ 部分 | 仅 normal_quote 隐含覆盖 |
| live smoke | ❌ | 无 |

**风险**：低中。多 vendor fallback 充分，但 fixture 和 smoke 不足。

---

### 3.9 内部人交易 (Insider)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `INSIDER`：cn_akshare(p1) + cn_astock(p2) |
| provider 实现 | ✅ | cn_akshare `get_insider_transactions()` + cn_astock `get_insider_transactions()` |
| route fallback | ✅ | cn_akshare → cn_astock |
| raw_evidence | ✅ | `insider_transactions` key |
| 报告/前端 | ✅ | 在 TA 报告中展示 |
| fixture | ❌ | 无 |
| live smoke | ❌ | 无 |

**风险**：低中。

---

### 3.10 涨停池 (ZT Pool)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `ZT_POOL`：cn_akshare(p1)，无 fallback |
| provider 实现 | ⚠️ 部分 | 仅 cn_akshare 实现，cn_astock 未实现 |
| route fallback | ❌ | 单点依赖 cn_akshare |
| raw_evidence | ✅ | `zt_pool` key |
| 报告/前端 | ✅ | 在 TA 报告中展示 |
| fixture | ❌ | 无 |
| live smoke | ❌ | 无 |

**风险**：中。AKShare 单点，若 AKShare 限流或失败则无 fallback。

**建议任务**：DATA-015 涨停池 cn_astock fallback 与 fixture。

---

### 3.11 热门股票 (Hot Stocks)

| 维度 | 状态 | 详情 |
|------|------|------|
| source_catalog | ✅ | `HOT_STOCKS`：cn_akshare(p1)，无 fallback |
| provider 实现 | ⚠️ 部分 | 仅 cn_akshare 实现，cn_astock 未实现 |
| route fallback | ❌ | 单点依赖 cn_akshare |
| raw_evidence | ✅ | `hot_stocks` key |
| 报告/前端 | ✅ | 在 TA 报告中展示 |
| fixture | ❌ | 无 |
| live smoke | ❌ | 无 |

**风险**：中。同涨停池，AKShare 单点。

---

## 4. 关键差距清单

### 差距 1：融资融券完全缺失（HIGH）

**现象**：无 provider、无 catalog 条目、无 evidence、无 fixture、无 smoke。
**影响**：风控经理无法评估杠杆风险；情绪分析师缺少融资买入/融券卖出数据。
**根因**：融资融券从未被纳入数据源治理范围。
**建议**：DATA-010 融资融券数据源注册与 raw_evidence 接入。

### 差距 2：研报端点已登记但未接入管线（MEDIUM-HIGH）

**现象**：`source_catalog` 有 `REPORT` 条目，cn_astock 有 `get_research_reports()` 方法，但 `TOOLS_CATEGORIES` 未注册 → `route_to_vendor()` 不路由 → `data_collector._fetch_all()` 不收集 → 不进入 raw_evidence。
**影响**：TA 报告中无券商研报观点和目标价，基本面分析信息不完整。
**根因**：bonus 方法未走完接入管线。
**建议**：DATA-011 研报端点接入 route_to_vendor 与 raw_evidence。

### 差距 3：评级/回购仅在事件流，未进入 TA 管线（MEDIUM）

**现象**：`event_source.py` 已实现评级和回购采集，但走的是 TradeFlow 事件流路径，不经过 provider 路由、不进入 raw_evidence、不被 data_collector 收集。
**影响**：TA 报告中无评级和回购信息；昊天雷达可见但深度 TA 不可见。
**根因**：event_source 和 data_collector 是两条独立管线。
**建议**：DATA-012 评级数据接入 provider 路由；DATA-013 回购数据接入 provider 路由。

### 差距 4：涨停池/热门股票 AKShare 单点依赖（MEDIUM）

**现象**：`zt_pool` 和 `hot_stocks` 仅 cn_akshare 实现，cn_astock 未实现对应方法。
**影响**：AKShare 限流/故障时这两个数据完全缺失，影响短线技术分析和市场情绪判断。
**根因**：cn_astock 建设时优先覆盖了资金流/龙虎榜/公告等高优先级端点。
**建议**：DATA-015 涨停池 cn_astock fallback；DATA-016 热门股票 cn_astock fallback。

### 差距 5：Live smoke 覆盖不完整（MEDIUM）

**现象**：live smoke 仅覆盖 4 个端点（push2his_fund_flow / datacenter_lhb / tencent_realtime_quote / cninfo_announcements），未覆盖 OHLCV、板块资金、新闻、财务、内部人交易、涨停池、热门股票。
**影响**：fixture 通过但实盘接口可能失效的风险未被检测。
**根因**：DATA-P1-ASTOCK-LIVE-SMOKE 聚焦 4 个关键端点。
**建议**：DATA-008 A股关键源 fallback smoke fixtures 扩展。

### 差距 6：新闻/全球新闻缺少专项 fixture（LOW-MEDIUM）

**现象**：新闻和政策事件无独立 fixture 场景（失败、限流、空结果）。
**影响**：新闻采集失败时的 fallback 行为未被 fixture replay 验证。
**根因**：DATA-005 优先覆盖了行情/资金流/龙虎榜/公告。
**建议**：DATA-014 新闻/政策事件 fixture 补充。

### 差距 7：历史根因——失败字符串假成功（已修复，需持续监控）

**现象**：DATA-P0-FUND-ROUTE 修复前，AKShare 返回 `ProxyError` 等失败字符串被路由层误判为成功，导致 fallback 不触发。
**根因**：`route_to_vendor()` 仅检查异常，未检查返回文本。
**修复**：新增 `_is_failure_result()` 检测 10 个失败文本模式。
**持续风险**：新 provider 或新方法可能返回未被覆盖的失败文本。
**建议**：DATA-007 raw_evidence 覆盖率审计可持续监控。

---

## 5. "为什么之前说接了 fallback 但主力资金仍失败"的解释

### 原始问题

用户发现 603629.SH TA 报告中个股资金流 `ConnectionError`，底部数据源可用性却显示"主力资金 ✅"。

### 根因链

1. `CnAkshareProvider.get_individual_fund_flow()` 因 AKShare 后端代理问题返回 `"数据获取失败..."` 或 `"ProxyError"` 字符串。
2. `route_to_vendor()` 原逻辑仅检查 Python 异常（`except Exception`），不检查返回文本。
3. 失败字符串被视为"成功 hit"，`last_hit_vendor` 记录为 `cn_akshare`，不再 fallback 到 `cn_astock`。
4. 下游 raw_evidence 将此字符串当作"有数据"，报告底部按"文本存在=可用"逻辑显示 ✅。
5. 实际证据覆盖率仅 42%，与"全部可用"矛盾。

### 修复时间线

| 任务 | 修复内容 |
|------|----------|
| DATA-P0-FUND-ROUTE | `_is_failure_result()` 检测失败字符串 → 路由继续 fallback → cn_astock 成功 → `vendor=cn_astock` |
| DATA-P0-FUND-ROUTE | cn_astock 资金流 `/10000` 元→万元，`unit_verified=True` |
| DATA-P1-LHB-FUND-DECOUPLE | 资金流失败不再阻止龙虎榜 force 查询 |
| DATA-P0-603629 | 底部数据源可用性改按 raw_evidence status 判断，不按文本存在判断 |

### 当前状态

修复后：AKShare 失败 → `_is_failure_result()` 检测 → 继续 fallback → cn_astock push2his 成功 → `vendor=cn_astock, fallback_from=cn_akshare, unit=万元, unit_verified=True`。底部显示 ✅ 与实际一致。

---

## 6. Simon Skill 思路 vs 本项目实现对比

| Simon Skill 思路 | 本项目实现 | 状态 |
|------------------|-----------|------|
| 端点目录（每个数据类型多个 vendor） | source_catalog 16 个 DataType，38 个 SourceEntry | ✅ |
| Vendor fallback（失败自动切换） | route_to_vendor() 含失败字符串检测 | ✅ |
| 限流防护（串行请求、间隔控制） | cn_astock _rate_limit() 200ms 间隔 | ✅ |
| 来源溯源（vendor/endpoint/as_of） | raw_evidence contract 16 个字段 | ✅ |
| 字段契约（unit/unit_verified） | evidence_contract + _verify_unit() | ✅ |
| 实时补丁（日线 stale + realtime） | G-005 is_realtime_patched | ✅ |
| 融资融券 | 无实现 | ❌ |
| 研报独立端点 | 已登记未接入管线 | ⚠️ |
| 评级独立端点 | 仅事件流 | ⚠️ |
| 回购独立端点 | 仅事件流 | ⚠️ |
| 全端点 live smoke | 仅 4 端点 | ⚠️ |

---

## 7. 下一轮任务建议

| 编号 | 任务 | 优先级 | 依赖 |
|------|------|--------|------|
| DATA-010 | 融资融券数据源注册与 raw_evidence 接入 | P1 | DATA-001 ✓ |
| DATA-011 | 研报端点接入 route_to_vendor 与 raw_evidence | P1 | DATA-004 ✓ |
| DATA-012 | 评级数据接入 provider 路由与 raw_evidence | P2 | DATA-003 ✓ |
| DATA-013 | 回购数据接入 provider 路由与 raw_evidence | P2 | DATA-003 ✓ |
| DATA-014 | 新闻/政策事件 fixture 与 live smoke 补充 | P2 | DATA-005 ✓ |
| DATA-015 | 涨停池 cn_astock fallback 与 fixture | P2 | DATA-005 ✓ |
| DATA-016 | 热门股票 cn_astock fallback 与 fixture | P2 | DATA-005 ✓ |
| DATA-008 | A股关键源 fallback smoke fixtures 扩展 | P1 | DATA-005 ✓ |

> 注：DATA-008 已在 TASKS.md 中为 ready 状态，优先级高于上述新建议。

---

## 8. 审计方法

本次审计通过以下方式完成：

1. 静态代码分析：读取 `source_catalog.py`（38 个 SourceEntry）、`interface.py`（6 个 TOOLS_CATEGORIES + route_to_vendor）、`evidence_contract.py`（EvidenceContract 16 字段）、`data_collector.py`（16 个 data key）、`fixture_replay.py`（7 个 fixture）、`live_smoke.py`（4 个端点）、`event_source.py`（3 个事件源）。
2. Provider 方法扫描：cn_akshare（15 方法）、cn_astock（17 方法含 bonus）、cn_baostock（2 方法 + 7 NotImplementedError）、yfinance（9 方法）、alpha_vantage（9 方法）。
3. 不涉及 live 调用、不改生产代码、不触发 LLM。
