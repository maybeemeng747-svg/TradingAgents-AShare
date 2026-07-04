# REPORT-UX-004 本地知识补充区块历史报告回放验收

> 任务：REPORT-UX-004 — 本地知识补充区块历史报告回放验收（P1）
> 日期：2026-07-05
> 任务档案：`docs/task_runs/REPORT-UX-004-20260705-031930/`
> 依赖：KB-003 ✓ / KB-008 ✓ / KB-009 ✓ / REPORT-UX-003 ✓

---

## 1. 验收目标

KB-003/KB-008/KB-009 已经把"本地知识补充"和"研报关注度"接入 TA 报告响应（顶层字段
+ Markdown 区块 + KB-009 衰减字段）。本验收用 5 份历史报告 fixture 回放验证三者**互
不污染**：

1. **本地知识补充**（KB-003 `local_knowledge_block` / `local_knowledge_summary`）
2. **研报关注度**（KB-008/KB-009 `research_attention_score` / `_effective_score`）
3. **数据不足观察原因**（REPORT-UX-003 `wait_reason_codes` / DATA-021 `data_blockers`）

核心问题：**本地知识命中不能被误当成行情事实或最终动作依据**，也不能冲掉真实的数
据缺口原因。

## 2. 回放链路

```
inline fixture result_data (含 raw_evidence)
  → report_service.attach_report_data_blockers        # DATA-021
  → report_service.resolve_report_fields              # 计算动作语义 + wait_reason_codes
  → report_service.attach_report_wait_reason_codes    # REPORT-UX-003
  → report_service.attach_report_local_knowledge      # KB-003 + KB-008 + KB-009
  → report_service.create_report                      # 写入 in-memory SQLite
  → _attach_report_data_blockers_for_response         # 顶层字段镜像 (api/main.py)
  → ReportResponse.model_validate / model_dump        # Pydantic schema 回环
```

知识库 fixture 复用 KB-007/KB-008/KB-011 同款 mini Tree Work 知识库（10 页，覆盖
A股/港股/美股/基金/未上市主体/过期/待补充/无标的 8 类），命中映射：

| Symbol            | KB 状态         | 说明                                |
|-------------------|-----------------|-------------------------------------|
| `603296.SH` 华勤技术 | `HAS_DATA`      | 3 页命中（公司 A + 评分表 + 深度研究）|
| `999999.SH`       | `NORMAL_NO_DATA`| 无任何命中                           |
| `600000.SH` 浦发银行| `STALE`         | 仅在 `valid_until=2020-01-01` 页命中 |
| `DELL.US` Dell    | `LOW_CONFIDENCE`| 仅在 `evidence_level=C 待补充` 页命中|

## 3. 五个回放场景

| # | 场景                            | Symbol         | KB 命中           | 动作                  | wait_reason_codes     |
|---|---------------------------------|----------------|-------------------|-----------------------|-----------------------|
| S1| 数据不足观察 + KB 命中           | 603296.SH      | HAS_DATA          | WAIT · 数据不足观察   | `[DATA_MISSING]`      |
| S2| 数据不足观察 + 无命中            | 999999.SH      | NORMAL_NO_DATA    | WAIT · 数据不足观察   | `[DATA_MISSING]`      |
| S3| 数据不足观察 + 仅 stale 命中     | 600000.SH      | STALE             | WAIT · 数据不足观察   | `[DATA_MISSING]`      |
| S4| 数据不足观察 + 低置信/待补充命中 | DELL.US        | LOW_CONFIDENCE    | WAIT · 数据不足观察   | `[DATA_MISSING]`      |
| S5| 方向性结论 + KB 命中             | 603296.SH      | HAS_DATA          | ENTER · 条件入场      | `[]`（非 WAIT）       |

## 4. 验收结果

### 4.1 三层隔离（核心验收）

| 场景 | KB block 渲染 | `wait_reason_codes` 保留 | `data_blockers` 保留 | `action_label` 不被改写 |
|------|:---:|:---:|:---:|:---:|
| S1 数据不足 + KB 命中    | ✅ 华勤技术 / 3 命中页 | ✅ `[DATA_MISSING]` | ✅ `individual_fund_flow: query_failed` | ✅ 仍为 `数据不足观察` |
| S2 数据不足 + 无命中     | ⚪ 空（隐藏区块）     | ✅ `[DATA_MISSING]` | ✅ `individual_fund_flow: query_failed` | ✅ 仍为 `数据不足观察` |
| S3 数据不足 + 仅 stale   | ⚠️ STALE 提示        | ✅ `[DATA_MISSING]` | ✅ `individual_fund_flow: query_failed` | ✅ 仍为 `数据不足观察` |
| S4 数据不足 + 低置信     | ⚠️ LOW_CONFIDENCE 提示 | ✅ `[DATA_MISSING]` | ✅ `individual_fund_flow: query_failed` | ✅ 仍为 `数据不足观察` |
| S5 方向性 + KB 命中      | ✅ 华勤技术 / 3 命中页 | ✅ `[]`（非 WAIT 不产生 code） | ✅ 行情/资金/龙虎榜均 NOT in blockers | ✅ 仍为 `条件入场` |

### 4.2 字段快照（probe 实测值）

| 场景 | action_label | execution | wait_codes | blockers | KB status | matched | attention | effective | themes |
|------|---|---|---|---|---|---|---|---|---|
| S1 | 数据不足观察 | WAIT | `[DATA_MISSING]` | 9 项（fund_flow=query_failed） | HAS_DATA | 3 | 3.93 | 3.87 | 8 |
| S2 | 数据不足观察 | WAIT | `[DATA_MISSING]` | 9 项（fund_flow=query_failed） | NORMAL_NO_DATA | 0 | 0.00 | 0.00 | 0 |
| S3 | 数据不足观察 | WAIT | `[DATA_MISSING]` | 9 项（fund_flow=query_failed） | STALE | 1 | 0.00 | 0.00 | 1 |
| S4 | 数据不足观察 | WAIT | `[DATA_MISSING]` | 9 项（fund_flow=query_failed） | LOW_CONFIDENCE | 1 | 0.00 | 0.00 | 1 |
| S5 | 条件入场     | ENTER | `[]` | 7 项（fund_flow 不在 blockers） | HAS_DATA | 3 | 3.93 | 3.87 | 8 |

### 4.3 KB 命中 ≠ 行情事实

S1 是核心回归用例：603296 同时存在 3 页 fresh KB 命中（华勤技术，attention_score
= 3.93），但 `individual_fund_flow=query_failed` 仍以 `DATA_MISSING` 形式透出到顶层
`wait_reason_codes`。本地知识补充区块只是背景/观点源，**没有被误当作行情事实或最终
动作依据**。

### 4.4 Stale / Low-confidence 不提升研究优先级

S3/S4 验证 KB-009 衰减规则生效：

- S3 仅 stale 命中：`matched_count=1`，但 `research_attention_effective_score=0.00`
  （不是 base_score=1.0）。
- S4 仅低置信命中：同上，effective_score 被 KB-009 衰减到 0。

即 KB 区块仍会渲染（让用户知道有过期/低置信页面存在），但**不会把候选的研究优先级
悄悄抬高**。

### 4.5 方向性结论不被 KB 推成 WAIT

S5 验证反向回归：603296 同样有 3 页 KB 命中，但报告本身是看多 ENTER。KB 命中既没有
把动作推成 WAIT，也没有产生空的 `wait_reason_codes`。本地知识只增加研究解释力，不
改变强动作门禁。

### 4.6 ReportResponse schema 不破坏前端

5 个场景的 `ReportResponse.model_validate(report).model_dump()` 全部成功，KB/RESEARCH
顶层字段（`local_knowledge_block` / `local_knowledge_summary` / `research_attention_score`
/ `knowledge_theme_count` / `research_attention_summary` / `research_attention_block`）
都序列化到了顶层，前端无需从 `result_data` 深挖。

`final_trade_decision` 中的 `Buy Level: 1` / `Risk Level: 2` 文本在所有场景下逐字
保留，证明强决策文本没有被解释性层悄悄重写。

## 5. 测试覆盖

测试文件：`tests/test_report_ux004_local_knowledge_replay.py`

| 测试类                                              | 用例 | 覆盖内容 |
|-----------------------------------------------------|:----:|----------|
| `test_replay_scenario_preserves_action_gate`        | 5    | 强动作门禁（decision/action_label/direction/execution）在 KB attach 后不变 |
| `test_replay_scenario_wait_reason_codes_preserved`  | 5    | `wait_reason_codes` 不被 KB 冲掉；非 WAIT 动作产生空 code 列表 |
| `test_replay_scenario_data_blockers_preserved`      | 5    | `data_blockers` 字段级状态在 KB attach 后逐字保留 |
| `test_replay_scenario_local_knowledge_block_matches_state` | 5 | KB block 渲染状态与符号映射一致（HAS_DATA/NORMAL_NO_DATA/STALE/LOW_CONFIDENCE） |
| `test_replay_response_schema_serializes_all_layers` | 5    | `ReportResponse` schema 回环保留三层字段 |
| `test_headline_kb_hit_does_not_mask_data_missing`   | 1    | **核心验收**：KB 命中不掩盖 DATA_MISSING |
| `test_stale_and_low_confidence_hits_do_not_inflate_research_score` | 1 | stale/低置信命中不抬升 effective_score |
| `test_attach_report_local_knowledge_is_additive_only` | 1  | attach 函数纯加性，不修改任何既有键 |
| **合计**                                            | **28** | **全部通过** |

## 6. 验收结论

| 任务要求 | 结果 |
|---|---|
| 选 3-5 份历史报告 fixture（命中/无命中/低置信过期/数据不足观察） | ✅ 5 份覆盖全部 4 类 + 方向性回归 |
| 回放 `attach_report_local_knowledge` 与报告响应字段 | ✅ 28 个测试全链路回放 |
| 验证 `wait_reason_codes`/`data_blockers` 不被本地知识冲掉 | ✅ 5 场景全部保留 |
| "数据不足观察"仍显示真实数据缺口原因 | ✅ S1-S4 全部 `[DATA_MISSING]` + `individual_fund_flow: query_failed` |
| 本地知识区块只作为补充背景 | ✅ KB block 与 `data_blockers` 共存，不互相覆盖 |
| 历史报告 response schema 不破坏前端 | ✅ `ReportResponse.model_dump()` 三层字段全部序列化 |

**结论：REPORT-UX-004 验收通过。** 本地知识命中、研报关注度、数据不足观察原因三层
在历史报告回放下保持完全隔离，没有发现互相污染的回归风险。
