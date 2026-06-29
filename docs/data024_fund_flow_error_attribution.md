# 主力资金 Probe 错误归因矩阵 — DATA-024

> 来源：`tradingagents/dataflows/fund_flow_source_probe.py` (`# [DATA-024] fund_flow_source_probe`)
> CLI：`python scripts/run_fund_flow_source_probe.py`
> 关联任务：DATA-022（失败矩阵 fixture 回放）、DATA-023（数据源能力矩阵）、DATA-P0-FUND-ROUTE（fallback 假成功修复）。

本文档说明主力资金（`fund_flow` / `get_individual_fund_flow`）供应商 probe 的 5 类错误归因语义、每类的典型输入特征、对下游报告动作的影响，以及与现有 `SourceFreshnessStatus` (DATA-018) / `EvidenceStatus` (readiness_score) / capability matrix (DATA-023) 的桥接关系。

## 1. 五类错误归因

`FundFlowErrorType` 把主力资金 probe 的结果归到下表之一。优先级与 `source_freshness_report.classify_source_status` 一致：限流 → 网络/接口失败 → 正常无数据 → 字段契约变更 → 单位不明 → OK → 兜底 UNKNOWN。

| error_type | label_cn | 优先级 | 触发条件（典型输入特征） | 对应 `SourceFreshnessStatus` | 影响 |
|------------|----------|--------|--------------------------|------------------------------|------|
| `network_error` | 网络失败 | 2 | `error` 或 `raw` 命中 `_FAILURE_PATTERNS`（`获取失败` / `ProxyError` / `ConnectionError` / `Max retries exceeded` / `TimeoutError` …）且未命中限流模式；或 `status="FAILED"`。 | `FAILED` | 必须触发 fallback（cn_akshare → cn_astock），fallback 全失败则该字段进入 blocker，报告降级为"数据不足观察"。 |
| `rate_limited` | 限流 | 1 | `error` 或 `raw` 命中限流模式（`429` / `Too Many Requests` / `请求过于频繁` / `限流` / `频繁访问` …）。 | `RATE_LIMITED` | 不等于永久故障；退避重试或立即切 fallback，刷新后可能恢复 HAS_DATA。 |
| `field_change` | 接口字段变更 | 4 | 数据存在但显式单位与预期 `万元` 不符（例如 `单位：元`，或探测到 `元` 而预期 `万元`）。 | `UNIT_UNVERIFIED` | 数值不能直接展示；若误展示会导致数量级错误（如把"元"当"万元"），需触发单位校验失败门禁。 |
| `unknown_unit` | 单位不明 | 5 | 数据存在但既无显式单位标记、入参 `unit` 也为空，且 `unit_verified=False`。 | `UNIT_UNVERIFIED` | 与 `field_change` 同属 `UNIT_UNVERIFIED`，但归因更弱——不知道单位，而不是单位错；建议追加单位探测或人工核实后再使用。 |
| `no_data` | 正常无数据 | 3 | `status="NORMAL_NO_DATA"` / `"NOT_QUERIED"` / `"SKIPPED"`；或 `raw` 为空且 `error` 为空；或 `raw` 仅含 `无数据 / 暂无 / 未查询到` 等 NORMAL_NO_DATA 标记。 | `NORMAL_NO_DATA` | 接口本身可用，标的新股 / 停牌 / 退市 / 非异动日无记录；不计入失败率，但会进入"数据缺口"提示。 |
| `ok` | 正常 | 6 | 数据存在、单位校验通过（或文本带 `单位：万元` 且数据 ≥1 行）。 | `HAS_DATA` | 可作为下游 raw_evidence 使用。 |
| `unknown` | 未知 | 7 | 兜底（例如 live 返回了非字符串类型，或所有规则都没命中）。 | `FAILED` | 视同失败处理，触发人工 review。 |

> **5 类任务必选归因** = `network_error` / `rate_limited` / `field_change` / `unknown_unit` / `no_data`（见 TASKS.md DATA-024 实现要点 3）。`ok` / `unknown` 是辅助基线。

## 2. Fixture 覆盖矩阵

`PROBE_FIXTURES`（在 `fund_flow_source_probe.py` 顶部）用与 `DataCollector.build_raw_evidence` 一致的结构化形态（`status` / `raw` / `unit` / `unit_verified` / `vendor` / `record_count` / `error`）覆盖下表 6 种典型输入。fixture dry-run 默认会回放全部 6 条，每条必须命中 `expected_error_type` 才算通过。

| fixture_id | 描述 | expected_error_type | 关键标记 |
|------------|------|---------------------|----------|
| `HAS_DATA` | cn_akshare 正常返回 20 日个股资金流 | `ok` | `单位：万元` + 20 行数据 |
| `NETWORK_ERROR` | AKShare `ConnectionError`，需 fallback | `network_error` | `个股资金流向数据获取失败：ConnectionError` |
| `RATE_LIMITED` | Eastmoney push2his 429 | `rate_limited` | `HTTPError 429: Too Many Requests` |
| `FIELD_CHANGE` | 单位从 万元 变成 元 | `field_change` | `单位：元`（与预期不符） |
| `NORMAL_NO_DATA` | 新股 / 停牌 / 退市，接口正常无记录 | `no_data` | `raw=""` 且 `status="NORMAL_NO_DATA"` |
| `UNKNOWN_UNIT` | 返回了表格但找不到单位标记 | `unknown_unit` | 数据 ≥1 行，但无 `单位：xx` 标记 |

## 3. Live-smoke 双重门禁

实盘抽样同时满足以下两个条件才会真正调用 `route_to_vendor`：

1. 显式传 `--live-smoke`（库调用 `live_smoke=True`）；
2. 环境变量 `TA_LIVE_DATA_SMOKE=1`（沿用 `live_smoke.py` 的全局 live 总开关）。

任一条件不满足时，所有标的标记为 `status="SKIPPED"`，不发任何网络请求。这是为了防止"开发任务"误触成本敏感的实盘调用（与 PERF-004 完整 TA 成本门禁一致）。

## 4. 与现有数据治理链路的桥接

| 链路 | 关系 |
|------|------|
| DATA-P0-FUND-ROUTE | probe 通过 `route_to_vendor` 走完整 fallback 链；命中 `_is_failure_result` 的 vendor 会被跳过，最终 `get_last_hit_vendor` 标记的是真正成功的 vendor。 |
| DATA-018 (freshness 6 态) | `FundFlowErrorType.TO_FRESHNESS_STATUS` 把 5 类错误映射回 freshness 6 态，可直接复用前端可视化。 |
| DATA-022 (失败矩阵 fixture) | 复用相同的"区分 NORMAL_NO_DATA 与 FAILED，长 FAILED 文本不被误判为 HAS_DATA"边界；fixture 形态一致。 |
| DATA-019 (实盘抽样日报) | live-smoke 走相同 `route_to_vendor` + `get_last_hit_vendor` 入口；probe 比 DATA-019 更聚焦：只针对 fund_flow 且强制归因到 5 类。 |
| DATA-023 (能力矩阵) | 通过 `build_capability_matrix_overlay(report)` 输出只读 overlay，附加到 matrix 的 `fund_flow` entry，不修改 matrix 模块本身（避免破坏已发布的 docs/SOURCE_CAPABILITY_MATRIX.md）。 |

## 5. 板块资金流严格分离

任务执行约束 §3 明确禁止把板块资金流（`DataType.BOARD_FUND_FLOW`，源 `stock_board_industry_fund_flow_em` / `stock_sector_fund_flow_rank` / `push2.eastmoney.com/clist`）当作个股资金流。本 probe 只覆盖 `DataType.FUND_FLOW`（`stock_individual_fund_flow` / `push2his.eastmoney.com/fflow`），不会把板块聚合数值落到个股 raw_evidence。

## 6. 安全约束

- 不读取 / 打印 / 持久化任何 API Key、cookie、Authorization header；`FundFlowProbeResult.error` 在 `to_dict()` 时被截断到 200 字符。
- 默认 fixture dry-run；live-smoke 必须双重门禁。
- 不写生产 `tradingagents.db`；不调用 LLM；不改 `tradingagents/prompts/`。

## 7. 退出码约定（CLI）

| 场景 | exit code |
|------|-----------|
| fixture dry-run，所有 fixture 命中 `expected_error_type` | 0 |
| fixture dry-run，任一 fixture 归因错误 | 1 |
| live 模式，env gated（SKIPPED） | 0 |
| live 模式，env 放行，全部 OK / NORMAL_NO_DATA | 0 |
| live 模式，env 放行，出现网络失败/限流/字段变更/单位不明/未知 | 1 |
