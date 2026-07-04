# V-013 Tree Work → TA → TradeFlow → investment-controller 知识链路端到端验收

> 任务：V-013 — Tree Work → TA → TradeFlow → investment-controller 知识链路验收（P2）
> 日期：2026-07-05
> 任务档案：`docs/task_runs/V-013-20260705-040318/`
> 依赖：KB-006 ✓ / TF-KB-001 ✓（间接 KB-003/KB-004/KB-008/KB-009/KB-010）

---

## 1. 验收目标

KB-003/KB-004/KB-006/TF-KB-001 已经打通"本地知识 → TA raw_evidence → 报告补充区块
→ TradeFlow 候选知识分 → investment-controller 只读上下文"五段链路。本验收用一只
fixture symbol（华勤技术 603296.SH）从 wiki 一路跑到 IC context，验证：

1. **字段完整**：每一层都携带来源（`rel_path` / `title` / `updated_at` /
   `confidence`）。
2. **来源可追溯**：raw_evidence `vendor=tree_work_wiki` / `endpoint=wiki/investment` /
   `source_type=tree_work_wiki`，沿途不变。
3. **权限只读**：所有 KB API 不写知识库、不调 LLM、不发外网；inbox / raw / 私人
   笔记被 partition isolation 屏蔽。
4. **无强动作越权**：KB attach 不改 `decision` / `direction` / `execution_action` /
   `action_label` / `confidence` / `target_price` / `stop_loss_price` /
   `final_trade_decision` 中的任何一个；合成文本不含强动作词。

## 2. 回放链路

```
[Stage A] query_local_knowledge                       # KB-003 读 wiki/investment/
    ↓ LocalKnowledgeQueryResult
[Stage B] build_raw_evidence_entry                    # KB-003 → raw_evidence 契约
    ↓ raw_evidence["local_knowledge"]
[Stage C] report_service.attach_report_local_knowledge # KB-003 + KB-008 报告顶层
    ↓ local_knowledge_block / local_knowledge_summary /
      research_attention_score / research_attention_block
[Stage D] tradeflow_service._enrich_candidate_with_local_knowledge # KB-004 + TF-KB-001
    ↓ local_knowledge_score / knowledge_hit_count /
      local_knowledge_summary / local_knowledge_detail / needs_tree_work_research
[Stage E] local_knowledge_context_service.collect_local_knowledge_hits  # KB-006
    ↓ local_knowledge_hits bucket (slim, ≤3 top_hits/symbol)
    → investment_controller_context._build_controller_hints
    ↓ research_review lane (background_supplement / needs_research_review)
```

知识库 fixture 复用 KB-007/KB-008/KB-011/KB-012/REPORT-UX-004 同款 mini Tree Work
知识库（9 页 + 1 私人 inbox 页），覆盖 A股/港股/美股/基金/未上市主体/过期/待补充
/无标的 8 类。

## 3. fixture 命中映射

| Symbol              | KB 状态          | 说明                              |
|---------------------|------------------|-----------------------------------|
| `603296.SH` 华勤技术 | `HAS_DATA`       | 3 页命中（公司 A + 评分表 + 深度研究）|
| `999999.SH`         | `NORMAL_NO_DATA` | 无任何命中                         |
| `600000.SH` 浦发银行 | `STALE`          | 仅在 `valid_until=2020-01-01` 页命中|
| `DELL.US` Dell      | `LOW_CONFIDENCE` | 仅在 `evidence_level=C 待补充` 页命中|
| `00700.HK` 腾讯     | `HAS_DATA`       | 1 页命中                          |
| `510300.SH` 沪深300ETF | `HAS_DATA`     | 1 页命中                          |

## 4. 验收结果 — 四问回答（docs/TASKS.md line 4731）

### Q1：命中哪些知识？

每一层都暴露可追溯的命中页摘要：

| Layer                                       | 命中字段                                            |
|---------------------------------------------|----------------------------------------------------|
| KB-003 `LocalKnowledgeQueryResult`          | `matched_pages[].rel_path / title / updated_at`    |
| KB-003 raw_evidence entry                   | `raw.matched_pages[]` 同上 + `vendor / endpoint`   |
| KB-003/KB-008 report attach                 | `local_knowledge_summary.matched_count` + block    |
| KB-004 TradeFlow enrichment                 | `local_knowledge_detail.matched_pages_brief[]`     |
| KB-006 IC search / collect_hits             | `hits[] / items[].top_hits[]` ≤3 (slim)            |

✅ 全部 layer 都能在不输出整页正文的前提下回答"命中哪些知识"。

### Q2：是否过期？

| 命中状态          | 透出字段                                                              | 行为 |
|-------------------|-----------------------------------------------------------------------|------|
| `HAS_DATA` (fresh) | `is_stale=False` / `is_low_confidence=False` / `fresh_hit_count>0`   | 正常 |
| `STALE`            | `is_stale=True` / `stale_hit_count>0` / summary 文本含"过期"          | 透出但 0 分 |
| `LOW_CONFIDENCE`   | `is_low_confidence=True` 或 `is_to_be_supplemented=True`             | 透出但 0 分 |

✅ Stale / 低置信命中**仍渲染**（让用户知道有过期/低置信页），但 KB-009 衰减后
`effective_score=0`，不抬升研究优先级。

### Q3：如何影响研究优先级？

KB 命中是**软研究优先级提示**，不是硬动作信号：

| 场景                              | KB-004 / TF-KB-001 字段                              | controller_hints.research_review lane |
|-----------------------------------|------------------------------------------------------|---------------------------------------|
| fresh 命中（603296）              | `local_knowledge_score>0` / `needs_tree_work_research=False` | `suggested_next_step=background_supplement` |
| stale 命中（600000）              | `local_knowledge_score=0` / `hit_count>0`           | `suggested_next_step=needs_research_review` |
| 知识缺口（999999 + 热主题）       | `local_knowledge_score=0` / `needs_tree_work_research=True` | 不进 research_review（无 hit）       |
| 极端知识分（adversarial）         | tier 不变（TF-KB-001 `verify_no_knowledge_promotion` 通过） | —                                     |

✅ 研究优先级只升一档（`scan → watch`）需要技术/数据门禁单独足以 actionable；
KB 命中既不是充分条件也不是必要条件。

### Q4：是否改变交易动作？

**核心回归**：KB attach 在每一层都是**纯加性**，强动作门禁字段逐字不变。

| Layer                  | 断言                                                                 |
|------------------------|----------------------------------------------------------------------|
| report_service attach  | `decision / direction / execution_action / action_label / confidence / target_price / stop_loss_price / final_trade_decision` 全部保留原值 |
| TradeFlow enrichment   | `tier / action` 不变（`POLICY_AMBUSH + 知识命中` tier 仍为 `C`）     |
| controller_hints       | 输出**不含** `decision / execution_action / action_label / target_price` 任何字段；`suggested_next_step` 仅允许中性词集合 |

✅ 无强动作越权。

## 5. 约束验证（docs/TASKS.md line 4723）

| 约束                                   | 验证方式                                                     | 结果 |
|----------------------------------------|--------------------------------------------------------------|:----:|
| 字段完整                               | Stage A-H 断言命中页 / 评分 / 来源 / 时间戳字段              | ✅   |
| 来源可追溯                             | raw_evidence vendor / endpoint / source_type 全程 `tree_work_wiki` | ✅   |
| 权限只读                               | `test_knowledge_root_not_mutated` — 所有 KB API 调用后文件 SHA 不变 | ✅   |
| inbox / 私人笔记 partition isolation   | `test_inbox_partition_isolated_from_wiki_investment` — "立即买入华勤技术，满仓梭哈" 不出现在任何 payload | ✅   |
| 无强动作越权                           | Stage C/D/E + 扩展词集 `(立即买入, 立即卖出, 立即清仓, 满仓, 清仓, 全仓, 重仓买入, 梭哈, 强烈推荐)` 扫描 | ✅   |
| 知识库不可达时降级                     | `KNOWLEDGE_CONTEXT_DISABLED=1` → `data_status=skipped`，不抛异常 | ✅   |
| `local_knowledge` 是 raw_evidence 可选项 | 缺失不影响 completeness_score（KB-003 wiring 契约）        | ✅   |
| 不调 live LLM / 不写生产 DB             | 全程 fixture / dry-run / in-memory                            | ✅   |

## 6. 测试覆盖

测试文件：`tests/test_v013_knowledge_e2e_acceptance.py`

| Stage | 测试类                                        | 用例 | 覆盖内容 |
|-------|-----------------------------------------------|:----:|----------|
| A     | `TestStageA_KB003_Query`                      | 5    | KB-003 状态机（HAS_DATA / STALE / LOW_CONFIDENCE / NORMAL_NO_DATA / FAILED） |
| B     | `TestStageB_RawEvidenceContract`              | 3    | raw_evidence 契约字段 + 往返序列化 + 禁词扫描 |
| C     | `TestStageC_ReportAttach`                     | 5    | KB-003/KB-008 attach 加性 / 不动门禁 / 失败兜底 |
| D     | `TestStageD_TradeFlowEnrichment`              | 6    | KB-004 + TF-KB-001 候选 enrichment + tier 不变式 |
| E     | `TestStageE_InvestmentControllerContext`      | 6    | KB-006 search/collect + controller_hints research_review lane |
| F     | `TestStageF_EndToEndChain`                    | 2    | 单标的从 wiki → raw_evidence → report → TF → IC 整链路一致性 |
| G     | `TestStageG_ConstraintsAndSafety`             | 4    | 只读 / partition isolation / 禁词 / disabled env 降级 |
| H     | `TestStageH_FourQuestionsAnswerable`          | 4    | Q1/Q2/Q3/Q4 字段可回答性 |
| CLI   | `TestCLISmoke`                                | 1    | `scripts/query_local_knowledge.py` 子进程冒烟 |
| **合计** |                                             | **36** | **全部通过** |

## 7. 测试执行

```bash
pytest tests/test_v013_knowledge_e2e_acceptance.py -q
# → 36 passed in 1.60s

# 任务档案指定的 smoke：
pytest tests/test_api_smoke.py tests/test_runtime_tier_contract.py -q
# → 119 passed in 16.42s

# KB 全链路回归：
pytest tests/test_kb003_local_knowledge_provider.py \
       tests/test_kb004_tradeflow_knowledge_score.py \
       tests/test_kb006_local_knowledge_context_api.py \
       tests/test_tf_kb001_knowledge_score_calibration.py \
       tests/test_report_ux004_local_knowledge_replay.py \
       tests/test_v013_knowledge_e2e_acceptance.py -q
# → 337 passed（仅 1 例 KB-009 时效衰减 flaky 失败，与 V-013 无关）
```

KB-009 单点 flaky 失败（`test_different_institutions_consensus_not_suppressed`）
是**预存在的时间漂移问题**：fixture 日期 2026-05-14 距今 52 天，时效衰减因子
0.9854 已跌破测试断言阈值 0.99；与 V-013 链路无关，V-013 只新增测试文件，未修改
任何生产代码。

## 8. 验收结论

| 任务要求（TASKS.md V-013）                                | 结果 |
|-----------------------------------------------------------|:----:|
| 建立 symbol fixture，从本地知识命中到 TA response         | ✅ 603296/600000/999999/DELL.US 四态覆盖 |
| 验证 TradeFlow candidate detail 带知识摘要和降权信息     | ✅ Stage D 6 例 |
| 验证 IC context 有 `local_knowledge_hits` 且不含长原文   | ✅ Stage E 6 例，summary_snippet ≤200 字符，top_hits ≤3 |
| 端到端测试通过                                            | ✅ 36/36 |
| 输出报告回答四问                                          | ✅ 第 4 节 |
| 无强买卖词新增                                            | ✅ Stage C/D/E + 扩展词集扫描通过 |
| 使用 fixture / dry-run，不调 live LLM                    | ✅ 全程 tmp_path fixture |
| 不写生产 DB                                               | ✅ 无任何 DB 写入 |

**结论：V-013 验收通过。** Tree Work → TA → TradeFlow → investment-controller
知识链路字段完整、来源可追溯、权限只读、无强动作越权。KB 命中作为软研究优先级
提示和背景解释层，与最终动作门禁保持完全隔离。
