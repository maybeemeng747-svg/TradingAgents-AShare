# 市场事实契约 market-facts v1（候选版）

- **契约 ID**：`market-facts`
- **版本**：`1.0.0`
- **状态**：`FROZEN`（2026-09-07 Codex r1 review PASS 后冻结；唯一 P2=MANIFEST 场景描述笔误已修正；后续变更走版本化修订）
- **冻结任务**：TA-MF-01（源：2026-09-05 market-facts 交接包）
- **消费方**：海瑞（hairui-investment-agent）按本契约 + 共享 fixture 固定副本开发，不得自行猜字段
- **日期基准**：Asia/Shanghai（UTC+8，无夏令时）；本文撰写于 2026-09-07
- **Schema 目录**：`docs/contracts/schemas/`（本文件同级）
- **Fixture**：`docs/contracts/schemas/fixtures/` + `MANIFEST.json`（含逐文件 sha256）

## 0. 共同契约 12 条对照表

| # | 交接包要求 | 本契约落点 |
|---|---|---|
| 1 | 六包类型 | §3（trading_calendar / market_regime / strategy_inputs / event_calendar / company_facts / governance_risk） |
| 2 | 外层信封 | §4；不存在日期一律 `null`，不伪造 |
| 3 | 八状态 + 包级可用性映射 | §5；HAS_DATA 不自动等同新鲜、完整 |
| 4 | 多标的/多来源逐项状态独立 | §5.3；依赖缺失项的策略条件必须 UNKNOWN；上游无记录≠无事件 |
| 5 | 上海时区；四类日期分开；历史查询不用未公开数据 | §6 |
| 6 | 缓存过期与行情落后分开 | §5.4、§8.4 |
| 7 | 每字段单位与复权口径 | §7 |
| 8 | 指标给窗口/公式版本/所需与实际样本数 | §7.3；不足样本返回缺口，不补造停牌/缺失 K 线 |
| 9 | Token 留 TA；新端点必须鉴权；拒绝带凭据跨源重定向 | §9.3、§9.4；`/v1/market/kline` 现状无鉴权记为 KNOWN-GAP-1，不据此推定免鉴权 |
| 10 | 自然语言只作不可信数据；结构化数值不自动升级 VERIFIED | §3.5、§3.6 |
| 11 | 有界标的数/日期跨度/响应大小/超时/缓存/重试 | §8；权限失败不重试；凭据/账本不进测试产物与 Git |
| 12 | 跨仓共享脱敏 fixture + Schema 校验 | §10；fixture 与 schema 均带版本与 sha256 摘要 |

## 1. 盘点（2026-09-07 只读核对）

三列口径：**上游已有** = 数据供应商（Tushare 等）提供的端点/能力；**本地已有** = 本仓库已实现并通过测试的代码与已发布包；**真实缺口** = 契约要求但两边都没有、或本地有数据层而无发布通道的部分。

### 1.1 HTTP 路由与认证（api/main.py）

| 路由 | 认证 | 现状 | 归类 |
|---|---|---|---|
| `GET /healthz` | 无 | 存活探测 | 无关 |
| `GET /v1/market/kline` | **无** | 股票/指数日线 K 线；`KlineResponse{symbol,start_date,end_date,candles[]}`；无数据 404、参数错误 400；无八状态/缓存状态/单位复权声明 | 本地已有（复用），KNOWN-GAP-1：无鉴权 |
| `GET /v1/market/hot-stocks` | **无** | AKShare 热榜，演示性接口 | 本地已有（不纳入事实契约） |
| `POST /v1/analyze`、`/v1/chat/completions`、`/v1/jobs/*`、`/v1/reports*`、`/v1/config*`、watchlist/scheduled/portfolio 等 | Bearer JWT / API Token（`_require_api_user` / `_require_web_user` / `_require_readonly_api_user` 三档） | 完整分析链路，会调 LLM | 本地已有（事实查询**不**复用，不启动分析、不调 LLM） |
| `GET /v1/market/facts/contract` | Bearer（readonly，不写 last_used_at） | **本契约新增**：返回契约版本、schema/fixture 摘要、状态与包清单，供海瑞机器核对加载版本 | 本契约交付 |

### 1.2 Provider 能力（tradingagents/dataflows/）

| 能力 | 上游已有 | 本地已有 | 真实缺口 |
|---|---|---|---|
| 股票日线+复权因子 | `daily`、`adj_factor` | `CnTushareProvider.get_stock_data`：QFQ（以窗口末日为基准）、单位已换算（股/元） | 指数日线仅 8 个硬编码映射（`CN_INDEX_SYMBOL_MAP`：000001.SH、399001.SZ、399006.SZ、000300.SH、000688.SH、000905.SH、000852.SH、899050.BJ），**缺中证2000（932000.CSI）等**；ETF 日线、场外基金净值未接 |
| 八状态查询契约 | — | `tushare_query_contract`（TA-TUSHARE-2000-001B）：8 状态、缓存元数据、response_sha256、attempts/retry_reason、失败不带数值 | 信封化（统一外层）未做 |
| 财务证据包 | income/balancesheet/cashflow/fina_indicator/forecast/express/dividend/fina_audit/fina_mainbz | `tushare_research_evidence`（001C）schema 1.0.0 + 脱敏 fixture + as_of 未来披露过滤 | 无 HTTP 发布通道（仅导出脚本/知识库用） |
| 治理事件包 | stk_holdernumber、top10_holders、top10_floatholders、pledge_stat、pledge_detail、stk_holdertrade、share_float、repurchase | `tushare_governance_events`（001D）schema 1.0.0 + 动作词禁用校验 | 无 HTTP 发布通道 |
| 交易日历 | `trade_cal` | 无直接实现（靠行情日期间接推断，未核验休市公告） | **真实缺口**：无核验过的日历快照来源与发布（TA-MF-02） |
| 财报披露计划/分红/解禁事件 | `disclosure_date`（披露计划）、`dividend`、`share_float` | dividend/share_float 已在治理包内 | **真实缺口**：无统一事件日历（计划日/实际日/修订差分/稳定 ID）（TA-MF-04） |
| 确定性指标 | — | 无集中实现（stockstats_utils 仅供分析链） | **真实缺口**：5/20/60 日收益、MA20/60、窗口波动、最大回撤、量比无版本化公式与样本数契约（TA-MF-03） |
| 证券身份 | `stock_basic`（含 exchange/名称/行业） | `instrument_identity`（exchange 推断、身份上下文） | **真实缺口**：统一证券注册表（股票/指数/ETF/场外基金分类型路由，ETF 与基金未登记） |
| 缓存/限流/超时 | — | provider 内缓存 TTL 300s、八状态限流/权限分类、全局网络默认超时（`network_timeout.install_default_network_timeout`）、有界抓取池（UPSTREAM-081-001） | 包级 TTL/边界策略需按 §8 执行（属契约约束，非代码缺口） |
| 权限审计 | 积分/权限分级 | permission_tier / matrix_ref / endpoint_permissions + token_status HAS_KEY/NO_KEY fail-closed | 无 |

### 1.3 结论

- **直接复用**：八状态契约、001C/001D 两个包的数据形状、`get_stock_data` 的复权与单位换算、K 线路由（KNOWN-GAP-1 除外）、认证三档、缓存审计。
- **真实缺口（后置任务，不在本契约实现）**：交易日历快照（TA-MF-02）、指数/ETF 统一行情与确定性指标（TA-MF-03）、事件日历（TA-MF-04）、财务/治理包的 HTTP 发布（TA-MF-05）、联合验收（TA-MF-06）、K 线端点鉴权（KNOWN-GAP-1，见 §9.3）。

## 2. 范围与责任边界

- 事实查询是**只读**的：不启动完整分析、不调用 LLM、不生成订单、不修改投资政策、不写海瑞/知识库/controller 目录。
- 行情不能更新持仓股数、现金或推进持仓快照时间。
- TA 负责：数据源接入、凭据、缓存限流、证券解析、时间与单位规范、确定性指标、只读查询服务。
- 海瑞负责：问题路由、数据适用性检查、与用户已确认规则的匹配、缺口解释、事件草案及审判流程。海瑞读取事实不继承 TA 买卖结论。
- 责任分界：TA 给**事实与日期**（含事件日期），策略条件匹配由海瑞按用户已确认规则计算；TA 输出的指标是版本化统计值，不是"当前适合买入"类结论。

## 3. 六个包类型

包类型是**用途分类**，不是微服务。同一上游端点可出现在多个包中（如 `share_float` 同时服务 event_calendar 与 governance_risk）。

### 3.1 trading_calendar（交易日历）

| 项 | 约定 |
|---|---|
| 用途 | 判断某日是否交易日、未来 N 个交易日列表、覆盖期边界 |
| 覆盖范围 | `payload.coverage`（start/end，含闭区间）；覆盖将到期必须提前给出 gap |
| 字段 | `payload.trading_days[]`（YYYY-MM-DD 升序）、`payload.closed_days[]`（覆盖期内休市日）、`payload.source_ref`（来源与版本，如交易所公告核验记录） |
| 状态语义 | HAS_DATA=覆盖期内经来源核验；与第二来源冲突 → gap `CALENDAR_CONFLICT` 且不得宣称确定 |
| 来源 | 交易所日历（Tushare `trade_cal` 或等价），发布前与休市公告核对 |
| 日期 | 仅交易日期（trade_date）语义，无披露期 |

### 3.2 market_regime（市场基准状态）

| 项 | 约定 |
|---|---|
| 用途 | 基准指数（沪深300、中证500/1000/2000、科创50、创业板指等注册表成员）在指定窗口的相对表现与已定义统计值 |
| 字段 | `payload.benchmarks[]`：`{symbol, name?, daily:[{date,close,pct_chg}], adjustment:"NONE_INDEX_POINTS", unit:{price:"index_points"}}`；统计值进信封 `computation.indicators`（逐基准逐指标，含窗口/公式版本/所需/实际样本数） |
| 比较口径 | 多基准比较必须同日期窗口；窗口不同 → 分别声明且 gap `WINDOW_MISMATCH` |
| 盘中 | 仅返回已完成日线并明示截至日期；无法评估盘中量能，不冒充实时 |
| 来源 | 指数日线（注册表映射的供应商端点） |
| 初始覆盖 | 复用 `CN_INDEX_SYMBOL_MAP` 8 指数注册表；中证2000 等增量在注册表登记后生效（TA-MF-03） |

### 3.3 strategy_inputs（策略输入指标）

| 项 | 约定 |
|---|---|
| 用途 | 单标的确定性指标：5/20/60 交易日收益、MA20/60、窗口波动、窗口最大回撤、收盘总量相对前 5 日均量 |
| 字段 | `payload.series`（date[]、close[]、volume[]、amount[]，等长升序）、`payload.adjustment`（QFQ，见 §7.2）、`payload.units`、指标进 `computation.indicators` |
| 样本规则 | 见 §7.3；不足样本不返回数值，gap `INSUFFICIENT_SAMPLES` |
| 边界 | 标签（若有）只能来自版本化公式输出；本包不产生买卖建议；依赖缺失项的策略条件在海瑞侧必须为 UNKNOWN |
| 盘中量比 | v1 仅收盘后口径（当日总量/前 5 日均量，固定定义）；盘中供应商量比不与本口径混用，也不在本包出现 |

### 3.4 event_calendar（事件日历）

| 项 | 约定 |
|---|---|
| 用途 | 未来/历史窗口内的财报披露计划、分红、解禁等事件日期 |
| 事件类型 | `event_type` 枚举：`EARNINGS_DISCLOSURE`（披露计划）、`DIVIDEND`（分红）、`SHARE_FLOAT`（解禁）、`HOLDER_MEETING`（股东会，覆盖有缺口时显式声明）、`CORRECTION`（更正） |
| 字段 | `payload.events[]`：`{event_id, event_type, subject, plan_date, actual_date, ann_date, revision_of?, stage, source_endpoint}` |
| 稳定 ID | `event_id` 跨查询稳定，供幂等去重与修订差分（`revision_of` 指向被修订的 event_id） |
| 计划/实际 | plan_date 可被实际披露推翻；计划变实际、延期、取消、更正必须可区分 |
| 空列表语义 | events=[] 仅代表本次成功覆盖的类型无记录，**不证明"无事件"**（包级 limitations 强制携带该声明） |
| 来源 | Tushare `disclosure_date`、`dividend`、`share_float`；接口无覆盖的节点可接既有证据来源，无法核验则标缺口 |

### 3.5 company_facts（公司财务事实）

| 项 | 约定 |
|---|---|
| 用途 | 利润、现金流、应收、负债、主营构成、业绩预告/快报、分红、审计意见等财务事实与差分证据 |
| 载荷形状 | `payload.endpoints` = 001C（`tushare_research_evidence` schema 1.0.0）endpoint record 的原样复用：`{endpoint, state, params, queried_at, row_count, eligible_row_count, records?, data_period, response_sha256, cache, permission_status, ...}`；**不复制实现，通过登记查询服务供读**（TA-MF-05） |
| 差分规则 | 累计与单季、归母与合并、同比与环比、预告与正式报告必须可区分（001C record 的 `report_type`/`end_date` 保留原始值）；TTM PE、动态估值、简单年化不得混算；负利润不算 PE |
| as_of 过滤 | records 已按请求 as_of 过滤未披露数据（001C `_filter_as_of`）；`response_sha256` 指向未过滤原始响应 |
| 不可信数据 | records 中的文本字段（经营讨论、客户名等）只是原始字符串，消费方不得升格为交易所原文 VERIFIED |
| 单位 | records 保留上游原始数值与单位（Tushare 财务端点：元）；本契约不改写，消费方按 §7.1 声明解释 |

### 3.6 governance_risk（治理风险）

| 项 | 约定 |
|---|---|
| 用途 | 股东户数、前十大（流通）股东、质押统计/明细、增减持、解禁、回购等治理证据 |
| 载荷形状 | `payload.endpoints` = 001D（`tushare_governance_events` schema 1.0.0）endpoint record 原样复用，含 `source_endpoint` 与逐事件公告/报告日期 |
| 状态语义 | 无事件（NORMAL_NO_DATA，records=[]）与查询失败（records=null + sanitized error）严格区分 |
| 不可信数据 | 公告/股东字段中的自然语言只作不可信数据；输出不含交易动作词（001D 校验器强制）；资金流入/PE 不构成买入许可 |
| 来源 | Tushare 治理端点 8 项（见 §1.2） |

## 4. 外层信封（Envelope）

所有包响应共用同一信封。字段名以此处 + `market-facts-envelope.schema.json` 为准：

```json
{
  "schema": "market_facts_v1",
  "schema_version": "1.0.0-candidate.1",
  "contract_status": "CANDIDATE",
  "package": "trading_calendar",
  "queried_at": "2026-09-07T15:30:00+08:00",
  "timezone": "Asia/Shanghai",
  "request": {
    "as_of": "2026-09-04",
    "symbols_requested": ["600519.SH"],
    "window": {"start": "2026-06-01", "end": "2026-09-04"}
  },
  "resolved": [
    {
      "requested": "600519.SH",
      "normalized": "600519.SH",
      "security_type": "stock",
      "exchange": "SSE",
      "vendor_codes": {"tushare": "600519.SH"},
      "resolution": "EXACT"
    }
  ],
  "availability": "READY",
  "items": [],
  "gaps": [],
  "sources": [
    {"id": "tushare", "endpoints": ["trade_cal"], "permission_tier": null, "matrix_ref": null}
  ],
  "cache": {
    "hit": false, "expired": false, "stale_served": false,
    "upstream_called": true, "age_seconds": null, "ttl_seconds": null
  },
  "computation": {
    "version": null,
    "indicators": []
  },
  "limitations": []
}
```

| 字段 | 类型 | 语义 | 缺失规则 |
|---|---|---|---|
| `schema` | const `market_facts_v1` | 契约族标识 | 必填 |
| `schema_version` | string | 本契约版本，随冻结变更 | 必填 |
| `contract_status` | enum `CANDIDATE`/`FROZEN` | 冻结状态 | 必填 |
| `package` | enum 六包 | 包类型 | 必填 |
| `queried_at` | RFC3339 带偏移 | 查询时点（TA 侧执行查询的时刻） | 必填 |
| `timezone` | const `Asia/Shanghai` | 全部日期语义时区 | 必填 |
| `request.as_of` | YYYY-MM-DD 或 null | 请求截止时点（数据可见性截止日） | 必填（可为 null 表示"最新"） |
| `request.symbols_requested` | string[] | 请求原始标的 | 必填（市场级包可为 []） |
| `request.window` | {start,end} 或 null | 请求日期窗口 | 可选 |
| `resolved[]` | 数组 | 逐标的解析结果（§6.2）；解析失败也要逐项记录 | 必填 |
| `availability` | enum `READY`/`PARTIAL`/`UNAVAILABLE` | 包级可用性映射结果（§5.2） | 必填 |
| `items[]` | 数组 | 包内容，逐标的/逐项，见各包 schema | 必填（可 []，但必须伴随 gaps/失败状态，不得伪装成功） |
| `gaps[]` | 数组 | 缺口清单 `{subject, code, detail, related_item?}`；code 枚举见 §5.5 | 必填 |
| `sources[]` | 数组 | 来源声明（供应商、端点、权限档位与矩阵引用） | 必填 |
| `cache` | 对象 | 缓存状态，字段同 001B `TushareCacheMetadata` | 必填 |
| `computation` | 对象 | 计算版本 + 指标元数据（§7.3）；无指标时 version=null, indicators=[] | 必填 |
| `limitations` | string[] | 包级语义限制声明 | 必填 |

**不伪造规则**：失败状态（QUERY_FAILED/PERMISSION_DENIED/RATE_LIMITED/NOT_QUERIED）与 FIELD_MISSING 的 item：`payload=null`、`data_date/ann_date/actual_disclosure_date/report_period=null`、数值字段一律 null；日期不存在就写 null，不用 0/空串/占位日期冒充。

## 5. 八状态与可用性

### 5.1 查询状态（沿用 TA-TUSHARE-2000-001B）

| 状态 | 语义 | 载荷约束 |
|---|---|---|
| `HAS_DATA` | 上游成功返回且必需字段齐备 | 有 payload；row/记录数 ≥1 |
| `NORMAL_NO_DATA` | 上游成功但零记录 | payload 内记录=[]（证据性空列表） |
| `QUERY_FAILED` | 上游/传输失败（已脱敏 error） | payload=null，无数值 |
| `PERMISSION_DENIED` | 供应商权限/积分不足 | payload=null；**不重试** |
| `RATE_LIMITED` | 供应商限流 | payload=null；**不重试** |
| `FIELD_MISSING` | 有行但契约必需列缺失 | payload=null；missing_fields 非空 |
| `NOT_QUERIED` | 未发起上游调用（未配置/跳过/NO_KEY fail-closed） | payload=null |
| `STALE` | 仅存过期缓存 | payload=过期数据作证据；cache.expired=true；data_date 保留真实日期 |

失败状态永远不携带数值行数/数据期间（001B `_NON_DATA_STATES` 硬约束）。

### 5.2 包级可用性映射

| availability | 判定 |
|---|---|
| `READY` | 全部请求项均 HAS_DATA 或 NORMAL_NO_DATA（含端点级聚合），且 `gaps=[]`、无 STALE/FIELD_MISSING |
| `PARTIAL` | 至少一项可用（HAS_DATA/NORMAL_NO_DATA），同时存在失败/缺口（gaps 非空）/过期/字段缺失项 |
| `UNAVAILABLE` | 无任何可用项（全部失败/未查询/过期） |

- HAS_DATA ≠ 新鲜、完整：新鲜度看 `data_date` 与 §8.4 的落后判定；完整性看 `gaps` 与 FIELD_MISSING。
- NORMAL_NO_DATA 是"成功查到空"，不是失败；但"上游无记录"不证明"无事件/无风险"——含此语义的包必须在 limitations 携带对应声明。
- 一个 item 失败不得伪装整包完整；依赖缺失项的策略条件在海瑞侧必须 UNKNOWN。

### 5.3 逐项独立

`resolved[]` 与 `items[]` 按标的/端点逐项携带自己的 state；多标的、多来源之间不共享成功/失败。

### 5.4 缓存过期 vs 行情落后

- 缓存是否过期：`cache.expired` / `cache.stale_served` / STALE 状态。
- 行情是否落后：`item.data_date` 相对 as_of/最近交易日的落后（gap `DATA_LAGGING`、`DATA_NOT_YET_PUBLISHED`）。
- 新抓取的旧数据依然可能不适合当前判断；收盘后数据尚未入库时输出等待/缺口（`DATA_NOT_YET_PUBLISHED`），**不借前日补齐**。

### 5.5 缺口 code 枚举（v1）

`UNRESOLVED_SYMBOL`、`OUT_OF_COVERAGE`、`CALENDAR_CONFLICT`、`INSUFFICIENT_SAMPLES`、`DATA_NOT_YET_PUBLISHED`、`DATA_LAGGING`、`FIELD_MISSING`（与状态同义的显式缺口）、`UPSTREAM_UNAVAILABLE`、`RESPONSE_TOO_LARGE`、`SOURCE_UNVERIFIED`。

## 6. 时间与日期

### 6.1 时区

- 全部日期语义时区为 `Asia/Shanghai`（UTC+8，无夏令时）；时点字段用 RFC3339 带偏移（如 `2026-09-07T15:30:00+08:00`）。
- 日期字段用 `YYYY-MM-DD`（ISO）。

### 6.2 四类日期分开

| 字段 | 语义 | 示例 |
|---|---|---|
| `trade_date` / `data_date` | 交易日/数据所属日 | 2026-09-04 |
| `ann_date` | 公告日期（预约/名义） | 2026-04-15 |
| `actual_disclosure_date` | 实际披露日期（`f_ann_date`） | 2026-04-18 |
| `report_period` | 报告期（`end_date`） | 2026-03-31 |
| `fetched_at` / `queried_at` | 抓取时点/查询时点 | RFC3339 带偏移 |

- 仅有公告日期、无法确定盘前盘后时，`disclosure_timing` 必须为 `UNKNOWN`，不得默认盘前。
- **历史查询不得使用当时尚未公开的数据**：所有已发布包按 as_of 过滤未披露记录（001C/001D 已实现，本契约为强制要求）。
- 001C/001D records 内保留上游原始 YYYYMMDD 格式（审计诚实）；信封与 item 级 canonical 字段用 ISO 格式，两者不得混写在同一字段。

## 7. 单位、复权与指标

### 7.1 单位声明

| 数据 | 单位 | 说明 |
|---|---|---|
| 股票价格 | CNY（元） | payload.units 显式声明 |
| 成交量 volume | share（股） | TA 已将 Tushare `daily.vol`（手，100 股）换算为股 |
| 成交额 amount | CNY（元） | TA 已将 Tushare `daily.amount`（千元）换算为元 |
| 指数点位 | index_points（点） | 无复权概念 |
| ETF 价格 | CNY（元） | 未复权价；分红/拆分事件另列，**不冒用股票复权接口** |
| 场外基金净值 | NAV_CNY_PER_UNIT（元/份） | 累计净值 ≠ 含再投资总回报，两者分开声明 |
| 财务金额 | CNY（元） | 001C records 保留上游原始值 |

### 7.2 复权口径

| adjustment 值 | 口径 | 用途 |
|---|---|---|
| `QFQ`（as_of_end_date） | 以请求窗口末日为基准的前复权（`get_stock_data` 现行为） | 收益率、均线、波动、回撤等收益类指标 |
| `NONE` | 未复权 | 股价触发、成交价等价格类判断 |
| `NONE_INDEX_POINTS` | 指数点位 | 指数序列 |

- 每个价格序列与每个指标必须携带 adjustment；同一响应中不同序列可有不同 adjustment，逐项声明。
- 缺口：复权因子不完整时 `get_stock_data` 已 fail-closed（NotImplementedError → QUERY_FAILED），不得用未复权价冒充复权价。

### 7.3 指标规范（公式版本冻结）

`computation.indicators[]` 每项：`{name, subject, window_days, formula_version, required_samples, actual_samples, adjustment}`。v1 冻结：

| name | 公式（formula_version=`mf-calc-v1`） | required_samples |
|---|---|---|
| `ret_nd` | `close[t]/close[t-N] - 1` | N+1 个有效收盘点 |
| `ma_n` | N 日简单移动平均 | N |
| `vol_nd` | N 日日收益样本标准差（ddof=1） | N+1 |
| `mdd_nd` | 窗口内 `min(close[t]/max(close[0..t]) - 1)` | N+1 |
| `vratio_5d` | 当日收盘总量 / 前 5 日均量 | 6 |

- `actual_samples < required_samples` → 不返回数值，gap `INSUFFICIENT_SAMPLES`。
- 停牌/缺失 K 线不补造：样本只按实际有效交易日序列计。
- 盘中查询只给已完成日线的指标并明示截至日期，不冒充实时。
- 指标标签（若有）只能来自版本化公式；不生成"当前适合买入"类结论。

## 8. 查询边界、缓存、重试与更新策略

### 8.1 请求边界（v1 冻结值）

| 边界 | 值 | 超出行为 |
|---|---|---|
| 单请求标的数 | ≤ 20 | 400 拒绝 |
| 日线日期跨度 | ≤ 366 自然日 | 400 拒绝 |
| trading_calendar 覆盖期 | ≤ 400 自然日 | 400 拒绝 |
| 响应大小 | ≤ 2 MB | gap `RESPONSE_TOO_LARGE` + 截断说明，不静默截断 |
| 单上游调用超时 | 30 s（`network_timeout` 默认） | 状态 QUERY_FAILED |
| 整请求超时 | 60 s | 超时项标 QUERY_FAILED，可用项照常返回（部分语义） |

### 8.2 缓存 TTL（v1 冻结值）

| 数据 | 包级 TTL | 说明 |
|---|---|---|
| trading_calendar | 24 h | 已核验日历日内不变 |
| 日线类（market_regime / strategy_inputs 的日线与指标） | 15 min | 收盘后数据日内不变；盘中缓存短 TTL + 明示截至日期 |
| event_calendar | 1 h | 披露计划可能当日更新 |
| company_facts / governance_risk | 6 h | 财务/治理数据低频更新 |
| provider 内部查询缓存 | 300 s | 现状 `_cache_ttl_seconds=300`，属实现细节不进契约 |

### 8.3 重试策略

| 状态 | 重试 |
|---|---|
| QUERY_FAILED（传输/5xx 类） | 最多 2 次重试（attempts ≤ 3，指数退避） |
| PERMISSION_DENIED | **不重试** |
| RATE_LIMITED | **不重试** |
| STALE / FIELD_MISSING / NORMAL_NO_DATA / NOT_QUERIED | 不重试（按状态返回） |

### 8.4 更新时间策略

- `queried_at`（查询时点）、`fetched_at`（抓取时点）、`data_date`（数据日期）三者分开携带，不得互相替代。
- 收盘后数据尚未入库：返回 `DATA_NOT_YET_PUBLISHED` 缺口（或 item NORMAL_NO_DATA），不借前日数据补齐。
- 数据落后（最新数据早于应有时点）：gap `DATA_LAGGING`，原有数据照常给出但不得标 READY。
- 缓存过期与行情落后是两个独立维度（§5.4），分别用 cache 字段与 gap 表达。

## 9. 只读接口清单（v1 冻结）

### 9.1 清单

| 状态 | 路由 | 方法 | 鉴权 | 包 | 说明 |
|---|---|---|---|---|---|
| 复用 | `/v1/market/kline` | GET | **无（KNOWN-GAP-1）** | （K 线原语） | 兼容保留，响应为既有 `KlineResponse`，**不**是信封格式；事实包需要 K 线时由 TA 内部复用同一数据路径 |
| 本契约新增 | `/v1/market/facts/contract` | GET | Bearer（readonly，不写 last_used_at） | 自描述 | 返回契约版本、状态枚举、包清单、schema/fixture sha256；供海瑞核对加载版本 |
| 预留 | `/v1/market/facts/trading-calendar` | GET | Bearer（readonly） | trading_calendar | TA-MF-02 实现 |
| 预留 | `/v1/market/facts/market-regime` | GET | Bearer（readonly） | market_regime | TA-MF-03 实现 |
| 预留 | `/v1/market/facts/strategy-inputs` | GET | Bearer（readonly） | strategy_inputs | TA-MF-03 实现 |
| 预留 | `/v1/market/facts/event-calendar` | GET | Bearer（readonly） | event_calendar | TA-MF-04 实现 |
| 预留 | `/v1/market/facts/company-facts` | GET | Bearer（readonly） | company_facts | TA-MF-05 实现 |
| 预留 | `/v1/market/facts/governance-risk` | GET | Bearer（readonly） | governance_risk | TA-MF-05 实现 |

- 全部事实端点仅 GET，只读；无 POST/PUT/DELETE。
- 预留路由在实现任务交付前返回 404；本契约冻结其路径与鉴权要求，实现时不得更改。
- **禁止开放任意 Tushare endpoint 透传**（如 `/v1/tushare/{endpoint}` 形式）；新增数据能力必须按包登记后再发布。

### 9.2 KNOWN-GAP-1（`/v1/market/kline` 无鉴权）

- 现状：该端点早于本契约存在、无鉴权，前端/现有调用方在用；本任务**不改动它**（兼容承诺）。
- 兼容策略：保留现状不动；事实契约的包端点全部带鉴权；**不因旧 K 线端点无鉴权就推定其他端点免鉴权**。
- 后续：为 K 线补鉴权或提供带鉴权替代端点属独立任务，需评估现有调用方后另立任务卡。

### 9.3 认证

- 方式：`Authorization: Bearer <token>`；JWT（网页登录）或 API Token（`token_service` 前缀）均接受；推荐海瑞使用专用 API Token。
- 事实端点使用 readonly 档（`_require_readonly_api_user`）：认证过程不写 `last_used_at`，无业务写入。
- 未携带/无效 token → 401；与现有 API 端点行为一致。

### 9.4 网络与重定向

- 事实查询拒绝携带凭据的跨源重定向：上游会话不跨源转发 Authorization。
- 出站仅访问已登记的供应商域名（嵌套白名单）；响应内容按 schema 类型校验后才输出。

## 10. 共享 fixture 与 Schema 校验

- 位置：`docs/contracts/schemas/`（7 个 JSON Schema，draft-07 子集）+ `docs/contracts/schemas/fixtures/`（脱敏样例）+ `MANIFEST.json`（版本与逐文件 sha256）。
- 覆盖场景：HAS_DATA、NORMAL_NO_DATA、QUERY_FAILED、PERMISSION_DENIED、RATE_LIMITED、FIELD_MISSING、NOT_QUERIED、STALE、部分可用（PARTIAL）、样本不足、计划/实际事件修订，以及 2 个**负例**（应被 schema 拒绝：失败状态夹带伪造数值/可用性谎报 READY）。
- 脱敏承诺：fixture 只含公开市场数据与虚构/公开代码，无任何 token、密钥、个人数据、生产账本。
- 校验：仓库内 `tests/market_facts/schema_lite.py` 为零依赖的 JSON Schema（draft-07 子集）校验器；`tests/test_market_facts_contract.py` 保证 ① 全部正例通过对应 schema；② 负例被拒绝；③ fixture/manifest sha256 一致；④ schema 内状态/包枚举与代码常量（`tushare_query_contract.QUERY_STATES` 等）一致。
- 海瑞侧固定副本：复制 `schemas/` 目录 + `MANIFEST.json`，按 manifest 逐文件核对 sha256 后开发；契约冻结（FROZEN）后摘要变更即代表契约变更。

## 11. 验收与冻结流程

1. 本候选（candidate）经 Codex 独立 review：重点核对 12 条对照表、状态语义、边界值与 fixture 覆盖。
2. review 通过后由主控提交并宣告冻结：`contract_status` 改 `FROZEN`，版本号去 `-candidate.N` 后缀，manifest 重算。
3. 冻结后字段变更必须升版本号（1.0.1 / 1.1.0 / 2.0.0），海瑞按 `schema_version` 判断兼容。
4. 实际服务加载版本以 `GET /v1/market/facts/contract` 响应为准（TA-MF-06 核对），不以工作树代码为准。

## 12. 已知缺口与后续任务映射

| 缺口 | 后续任务 |
|---|---|
| 交易日历快照与来源核验 | TA-MF-02 |
| 指数/ETF 统一注册表与行情、确定性指标计算 | TA-MF-03 |
| 事件日历（披露计划/分红/解禁、修订差分、稳定 ID） | TA-MF-04 |
| company_facts / governance_risk 的 HTTP 发布 | TA-MF-05 |
| K 线端点鉴权（KNOWN-GAP-1） | 独立任务（需评估现有调用方） |
| 契约端点版本核对与联合验收 | TA-MF-06 |
