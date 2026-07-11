# REPORT-UX-005 回放报告 — 本地知识补充不覆盖动作语义的扩展回放

> 任务 REPORT-UX-005（P2）：扩展 REPORT-UX-004 的回放范围，
> 验证 local_knowledge / research_attention / half_year_facts 三组
> 解释性叠加字段不会把最终动作重新压成笼统「观察」。
>
> 生成时间：2026-07-11　|　测试文件：`tests/test_report_ux005_knowledge_action_semantics_replay.py`（26 tests passed）

## 覆盖矩阵（5 类 action semantics × 三组叠加字段）

| # | Scenario | Action | action_label | KB hit | half_year_facts | wait_reason_codes | 结果 |
|---|----------|--------|--------------|--------|-----------------|-------------------|------|
| 1 | w1_wait__kb_hy | WAIT | 数据不足观察 | HAS_DATA | HAS_DATA | DATA_MISSING | PASS |
| 2 | e1_enter__kb_hy | ENTER | 条件入场 | HAS_DATA | HAS_DATA | (empty) | PASS |
| 3 | h1_hold__kb_hy | HOLD | 持有 | HAS_DATA | HAS_DATA | (empty) | PASS |
| 4 | r1_reduce__kb_hy | REDUCE | 条件减仓 | HAS_DATA | HAS_DATA | (empty) | PASS |
| 5 | x1_exit__kb_hy | EXIT | 条件清仓 | HAS_DATA | HAS_DATA | (empty) | PASS |

所有场景标的均为 603296 华勤技术，同时命中 3 页本地知识（HAS_DATA）+
qualified 半年报事实页（HAS_FACTS），叠加字段强度最大化。

## 验收结论

1. **本地知识强命中不会把 ENTER/HOLD/REDUCE/EXIT 覆写成 WAIT**：
   603296 华勤技术同时命中 3 页本地知识 + qualified 半年报事实页，但
   ENTER（条件入场）/ HOLD（持有）/ REDUCE（条件减仓）/ EXIT（条件清仓）
   全部原样保留，wait_reason_codes 为空。

2. **数据不足时有明确 wait_reason_codes**：
   WAIT 场景（数据不足观察）保留 wait_reason_codes=[DATA_MISSING]，不允许
   只剩笼统 action_label 而无原因码。

3. **叠加字段 additive-only 契约**：
   local_knowledge_block / research_attention / half_year_facts 全部以
   raw_evidence 条目形式注入，不改 decision / execution_action /
   action_label / wait_reason_codes / data_blockers。

**总体：全部通过（5 scenarios）**

## 执行约束确认

- 不调用 live LLM。
- 不写生产 DB（使用 in-memory SQLite）。
- 不改 `tradingagents/prompts/`。
- fixture 全部 inline，知识库在 `tmp_path` 下构建。
