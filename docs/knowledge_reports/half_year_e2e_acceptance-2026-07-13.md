# HY-008 半年报知识链路端到端回放验收

> 任务：HY-008 — 半年报知识链路端到端回放验收（P2）
> 日期：2026-07-13
> 任务档案：`docs/task_runs/HY-008-20260713-190756/`
> 依赖：HY-003 ✓ / HY-004 ✓ / HY-005 ✓ / HY-006 ✓ / HY-007 ✓ / KB-015 ✓
> 测试：`tests/test_hy008_half_year_e2e_acceptance.py`（24 tests passed）

---

## 1. 验收目标

HY-003 ~ HY-007 已经分别打通"Tree Work 半年报 wiki → TA 报告 → TradeFlow 候选 →
investment-controller briefing"的五段能力。本验收用四个端到端场景从 wiki 一路跑到
IC briefing，验证：

1. **四段链路一致**：HY-003 事实状态 / HY-005 反证状态 / HY-006 因子分 / HY-004 报告
   区块 / HY-007 提醒分类在四个场景下与黄金基线吻合。
2. **字段与来源可追溯**：每层都携带 `rel_path` / `financial_period` / `source_type` /
   `vendor`，沿途不丢；验收报告能回答"事实是什么、来源在哪里、旧逻辑是否被支持/削弱"。
3. **不越权**：半年报知识**只作背景证据**——不覆盖数据不足原因（`wait_reason_codes` /
   `data_blockers`）、不改变最终动作语义（`action_label` / `execution_action`）、
   不输出强买卖词。
4. **去噪正确**：无半年报标的不刷屏；反证提醒无 `priority_reminder` 时只进日报。

## 2. 回放链路

```
[A] HY-003 query_half_year_facts                     # 读 wiki/investment/ 半年报页
    ↓ HalfYearFactsQueryResult (status / data_status / pages)
[B] KB-015 build_research_fact_opinion_index          # 抽取研报观点/事实分离索引
    + HY-005 check_thesis_against_facts               # 观点 vs 事实规则比对
    ↓ ThesisFactCheckResult (thesis_check_status / *_flags)
[C] HY-006 compute_half_year_factor_score            # 聚合因子分 + 降权原因
    + tradeflow _enrich_candidate_with_half_year      # 注入候选 item
    ↓ half_year_fact_score / downgrade_reasons / needs_research_review
[D] HY-004 report_service.attach_report_half_year_facts
    ↓ half_year_facts_block / half_year_facts_summary / half_year_facts_status
[E] HY-007 _collect_half_year_facts + build_half_year_reminders
    ↓ IC briefing reminder_type / priority / notify_level
```

知识库 fixture 全部内联在测试文件中（4 个独立 mini-KB），不依赖
`~/Documents/knowledge`，不调用 live LLM，不写生产 DB（TA 报告回放使用内存 SQLite）。

## 3. 四场景命中映射（黄金基线）

| 场景              | Symbol          | HY-003 命中   | HY-005 反证    | HY-006 分数 | HY-004 区块   | HY-007 提醒             |
|-------------------|-----------------|---------------|----------------|-------------|---------------|-------------------------|
| S1 事实支持        | 000977.SZ 浪潮信息 | HAS_DATA/fresh | supported      | +1.0        | HAS_FACTS     | fact_update (P2 daily)  |
| S2 事实削弱        | 002415.SZ 海康威视 | HAS_DATA/fresh | weakened       | -0.5        | HAS_FACTS     | rebuttal_alert (P2 daily) |
| S3 事实打脸        | 300750.SZ 宁德时代 | HAS_DATA/fresh | contradicted   | -2.0 ~ -3.0 | HAS_FACTS     | rebuttal_alert (P2 daily) |
| S4 无半年报        | 000001.SZ 平安银行 | NORMAL_NO_DATA | insufficient   | 0.0         | NO_DATA (空 block) | （无提醒，不刷屏）       |

**场景设计原理**：

- **S1 事实支持**：研报观点"AI 服务器业务将放量增长，营收高增" vs 公告"营收 +60%
  YoY"——方向一致 → `supported`，因子分 +1.0（正向分上限，只提高研究优先级）。
- **S2 事实削弱**：研报预期"营收将爆发式增长 +60%" vs 公告实际"+20.0%"（20 < 60×0.5）
  → `weakened`，因子分 -0.5 + 降权原因。
- **S3 事实打脸**：研报看多"储能业务下半年将迎爆发式增长" vs 公告"储能营收 -20.0%、
  出货同比下滑"——方向相反 → `contradicted`，因子分 -3.0 + 降权原因 +
  `needs_research_review=True`。
- **S4 无半年报**：仅有 `report_type=公司点评` 页 → HY-003 不命中 → NO_DATA，
  因子分 0，无提醒（避免"没有半年报"噪音）。

## 4. 验收结果 — 三问回答（docs/TASKS.md HY-008 验收方式）

### Q1：事实是什么？来源在哪里？

每一层都暴露可追溯的来源字段，沿途不丢：

| Layer                              | 来源字段                                                   |
|------------------------------------|------------------------------------------------------------|
| HY-003 `HalfYearFactsPage`         | `rel_path` / `financial_period` / `source_type` / `disclosure_date` |
| HY-005 `ThesisCheckFlag`           | `opinion_text` / `fact_text` / `metric_key` / `fact_source_path` |
| HY-006 `half_year_fact_detail`     | `facts_status` / `facts_latest_period` / `thesis_check_status` + 反证计数 |
| HY-004 `half_year_facts_summary`   | `status` / `matched_count` / `latest_period` / 四子区（facts / commentary / needs_verification / risks）|
| HY-007 reminder                    | `symbol` / `origin` / `facts_status` / `thesis_check_status` / `latest_period` |

✅ `test_source_traceability_and_field_completeness`（3 场景）逐层断言来源字段完整。
HY-005 反证 flag 同时携带观点原文 + 事实原文 + 指标 key，可回答"旧逻辑被什么事实
支持/削弱"。

### Q2：旧逻辑是否被支持/削弱/打脸？

HY-005 `thesis_check_status` 与 HY-006 因子分严格对应：

| thesis_check_status | HY-006 score | 含义                           | 是否需要 Tree Work/TA 复核 |
|---------------------|--------------|--------------------------------|---------------------------|
| supported           | +1.0         | 观点方向与事实一致              | 否（可提高研究优先级）      |
| weakened            | -0.5         | 方向一致但增幅不及预期一半       | 视 weakened 数量           |
| contradicted        | -2.0 ~ -3.0  | 方向相反（事实打脸）             | 是（needs_tree_work_review）|
| insufficient_data   | 0.0          | 事实不足以反证                  | 否                        |

✅ `test_e2e_half_year_knowledge_chain_consistency`（4 场景）断言四层状态/分数一致。

### Q3：是否需要 Tree Work/TA 复核？

- **contradicted**（S3）：`needs_tree_work_review=True`，HY-007 产出
  `rebuttal_alert` 提醒，提示回 Tree Work 复核旧研报观点。
- **weakened**（S2）：`rebuttal_alert` 提醒（P2 日报）。
- **无半年报**（S4）：不产出提醒（避免噪音）。

✅ `test_rebuttal_alerts_route_to_daily_digest_without_priority_reminder` /
`test_no_half_year_symbol_produces_no_reminder_noise`。

## 5. 核心隔离契约（实现要点 4 回归）

> 半年报知识只作背景证据，不覆盖数据不足原因和最终动作语义。

| 契约                                    | 验证测试                                              | 结果 |
|-----------------------------------------|-------------------------------------------------------|:----:|
| 半年报命中不掩盖 `wait_reason_codes`     | `test_half_year_facts_do_not_mask_data_missing_reasons` | ✅   |
| 半年报命中不掩盖 `data_blockers`         | 同上（`individual_fund_flow=query_failed` 保留）       | ✅   |
| 半年报命中不改 `action_label`            | 同上（仍为"数据不足观察"）                              | ✅   |
| 半年报因子不改 TradeFlow `tier/action`   | `test_half_year_knowledge_never_overrides_action_gate` | ✅   |
| 全链路文本不含强买卖词                    | `test_e2e_no_strong_action_words_across_chain`        | ✅   |

**S1 回放实证**（数据不足 + 事实支持的叠加场景）：
- `half_year_facts_status = HAS_FACTS`（区块渲染）
- `wait_reason_codes` 仍含 `DATA_MISSING`
- `data_blockers['individual_fund_flow'].status = query_failed`
- `action_label = 数据不足观察`（未改成方向性动作）

## 6. 约束验证（docs/TASKS.md HY-008 执行约束）

| 约束                          | 验证方式                                                  | 结果 |
|-------------------------------|-----------------------------------------------------------|:----:|
| fixture/dry-run，禁止 live LLM | `test_chain_does_not_call_llm_or_write_prod_db`（禁用 LLM 构造函数后链路仍跑通）| ✅   |
| 不写生产数据库                 | 同上（TA 报告写入内存 SQLite，非 tradingagents.db）        | ✅   |
| 重点检查字段、来源、状态、去噪  | Q1/Q2/Q3 + 去噪测试                                       | ✅   |
| 不越权                        | 第 5 节隔离契约                                           | ✅   |

## 7. 失败路径与边界（墨菲定律）

| 边界场景                          | 验证测试                                          | 结果 |
|-----------------------------------|---------------------------------------------------|:----:|
| 知识库只读（文件 SHA 不变）        | `test_knowledge_root_not_mutated`                 | ✅   |
| 损坏研报页不阻塞链路               | `test_corrupted_opinion_page_does_not_break_chain`| ✅   |
| 无半年报标的不刷屏                 | `test_no_half_year_symbol_produces_no_reminder_noise` | ✅   |
| Buy Level / Risk Level 文本逐字保留 | `test_half_year_facts_do_not_mask_data_missing_reasons` | ✅   |

## 8. 测试清单（24 tests）

```
tests/test_hy008_half_year_e2e_acceptance.py
  test_e2e_half_year_knowledge_chain_consistency[4 场景]
  test_e2e_no_strong_action_words_across_chain[4 场景]
  test_half_year_facts_do_not_mask_data_missing_reasons[3 HAS_FACTS 场景]
  test_half_year_knowledge_never_overrides_action_gate[4 场景]
  test_source_traceability_and_field_completeness[3 HAS_FACTS 场景]
  test_rebuttal_alerts_route_to_daily_digest_without_priority_reminder
  test_supported_facts_route_to_fact_update_daily_digest
  test_no_half_year_symbol_produces_no_reminder_noise
  test_knowledge_root_not_mutated
  test_chain_does_not_call_llm_or_write_prod_db
  test_corrupted_opinion_page_does_not_break_chain
```

回归基线：HY-001~008 + KB-015 + REPORT-UX-004 共 **505 tests passed**，无回归。

## 9. 结论

✅ 半年报知识链路（Tree Work wiki → TA 报告 → TradeFlow 候选 → IC briefing）端到端
回放通过。四场景的字段、来源、状态、去噪和动作门禁隔离全部符合 HY-008 验收标准。

下一步可释放 **HY-009**（半年报增量刷新、缓存失效与事实冲突审计，前置 HY-008 ✓）。
