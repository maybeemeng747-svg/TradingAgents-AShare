# [DATA-022] 主力资金/龙虎榜状态失败矩阵

> 任务：DATA-022 — 主力资金/龙虎榜失败矩阵 fixture 回放（P1）
> 依赖：DATA-021（report data_blockers）、DATA-017（fund/lhb health inspection）
> 测试：`tests/test_data022_fund_lhb_status_matrix.py`（54 cases，全部通过）

本文档说明主力资金与龙虎榜各状态在 TA 报告中产生的不同影响，确保系统不会把"正常无数据"误判为查询失败，也不会把"查询失败"因文本长度大于 20 而误判为 HAS_DATA。

---

## 1. 状态矩阵

### 1.1 主力资金（fund_flow_individual）

| 状态 | 含义 | 进入 blocker? | severity | 对报告动作的影响 |
|---|---|---|---|---|
| `HAS_DATA` | 接口返回有效记录，单位已校验 | 否（被排除） | — | 主力资金强证据可用，允许进入买入/减仓强结论路径。`build_fund_flow_provenance.strong_evidence_allowed = True` |
| `FAILED` | 接口超时/ConnectionError/限流，或文本含"失败" | 是 | **high** | 强动作必须降级。`strong_evidence_allowed = False`；`summarize_data_blockers.level = warning`；提示"资金面不能支撑买入/减仓强结论" |
| `NORMAL_NO_DATA` | 接口正常但当日无记录（空字符串） | 是 | info | 仅作信息记录，**不计入查询失败计数**，不单独触发强降级。多源叠加仍是 info/低权重 |
| `SKIPPED` | 数据源本轮跳过（如港股对 A 股专用字段） | 是 | low | 视同"已跳过"，不增加 high/medium 计数，level 不升级 |
| `NOT_QUERIED` | 本轮未查询该字段（raw 为 None） | 是 | medium | 提示"本次分析未查询该字段"，结论需保留条件，level=caution |

### 1.2 龙虎榜（lhb）

| 状态 | 含义 | 进入 blocker? | severity | 对报告动作的影响 |
|---|---|---|---|---|
| `HAS_DATA` | 当日上榜，存在席位/游资明细 | 否（被排除） | — | 可作为异动证据，辅助短线/事件型结论 |
| `FAILED` | 龙虎榜接口异常（ConnectionError、限流） | 是 | **high** | 无法确认异动席位结构；提示"查询失败"，强结论需要降级 |
| `NORMAL_NO_DATA` | 已强制查询但当日未上榜（非异动日属正常） | 是 | info | **明确不算失败**。blocker reason 显式写"未上龙虎榜或非异动日无龙虎榜数据，属于正常无数据"，不影响强动作门禁 |
| `NOT_QUERIED` | 无异动触发、未强制查询（query_mode=on_demand） | 是 | medium | 提示"本次分析未查询该字段"，结论需保留条件 |

---

## 2. 关键边界与防回归点

### 2.1 `FAILED` 不被 `len > 20` 误判为 HAS_DATA

这是本任务最重要的防回归边界。历史上曾出现：`infer_evidence_statuses` 与 `build_fund_flow_provenance._status_from_text` 在判断"是否为有效数据"时使用 `len(text) > 20` 启发式，若一段超过 20 字符的错误文本（如 `"主力资金获取失败：AKShare timeout，Eastmoney push2 返回 502 错误"`）先被长度规则匹配，就会被错误地标为 HAS_DATA，掩盖真实的查询失败。

**当前实现保证（已由 fixture 回放验证）**：

- `infer_evidence_statuses`：`len > 20 and "失败" not in text` 才进 HAS_DATA 分支；含"失败"的字符串无论长度都走 `QUERY_FAILED`。
- `build_fund_flow_provenance._status_from_text`：先检查 `"获取失败"` / `"不可用"`，再判断长度。
- `DataCollector._infer_source_status`：先识别 `[G-007] LHB_FAILED` / `获取失败` / `error` 等标记，再回退到 HAS_DATA。

### 2.2 `NORMAL_NO_DATA` 不计入查询失败

`summarize_data_blockers` 把 `normal_no_data` 与 `skipped` 归入 `normal` 桶，只有 `query_failed` + `field_missing` 才会让 level 升到 `warning`。当所有 blocker 都是 normal_no_data/skipped 时，level 保持 `info`，message 为"不单独构成强降级理由"。

### 2.3 结构化契约优先于文本启发式

`DataCollector.build_raw_evidence` 写入的 G-006 结构（`{"status": "...", "raw": ..., "vendor": ..., "unit": ...}`）会被 `_infer_source_status`（HK-001 显式状态路径）和 `infer_evidence_statuses._structured_evidence_status` 直接采纳，绕过文本正则。因此：

- 上游 fetcher 必须尽量写入结构化 `status` 字段；
- 仅当缺失结构化字段时才退化到 legacy text 启发式；
- `[G-007] LHB_*` 文本标记在 `DataCollector._infer_source_status` 中被识别，但在 `infer_evidence_statuses` 的纯文本回退路径中只有部分被识别，因此**结构化路径是首选**。

---

## 3. 防回归矩阵覆盖

`tests/test_data022_fund_lhb_status_matrix.py` 共 54 个测试，按 7 个维度组织：

| 维度 | 覆盖内容 |
|---|---|
| `TestDataCollectorStatusInference` | `DataCollector._infer_source_status` 对结构化 status、`[G-007] LHB_*` 标记、`获取失败`、空值的识别 |
| `TestInferEvidenceStatusesMatrix` | fund_flow 4 态 + lhb 4 态，结构化与 legacy_text 两条路径 |
| `TestFailedNotMisjudgedAsHasData` | 长 FAILED 文本不变成 HAS_DATA（fund_flow 长/短文本、lhb 长文本、provenance 路径） |
| `TestNormalNoDataNotCountedAsFailure` | NORMAL_NO_DATA severity=info、不进 query_failed 计数、纯 normal_no_data 时 level=info、lhb NORMAL_NO_DATA 不阻断 strong evidence |
| `TestFailedSeverityAndHasDataExclusion` | FAILED→high、HAS_DATA 被排除、SKIPPED→low |
| `TestProvenanceMatrix` | fund/lhb provenance 对每个状态返回正确 status 与 strong_evidence_allowed |
| `TestStatusesDoNotCollapse` | 4 个状态产出 4 个不同的 wire value，不复用同一桶 |

---

## 4. 字段→blocker 映射速查

| raw_evidence key | blocker key | EvidenceStatus 字段名 |
|---|---|---|
| `fund_flow_individual` | `individual_fund_flow` | `has_data` / `query_failed` / `normal_no_data` / `skipped` / `not_queried` |
| `lhb` | `lhb_status` | `has_data` / `query_failed` / `normal_no_data` / `not_queried` |

severity 映射（来自 `_BLOCKER_STATUS_META`）：

```
query_failed   → high    （强降级）
field_missing  → medium  （结论需保留条件）
not_queried    → medium  （结论需保留条件）
normal_no_data → info    （不单独降级）
skipped        → low     （不单独降级）
not_available  → low     （不单独降级）
```
