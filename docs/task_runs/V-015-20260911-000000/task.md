# V-015 运行档案 — 研报增量摄取→证据 API→前端→待更新清单端到端验收

- **任务卡**：docs/TASKS.md `### V-015: 研报增量摄取→证据 API→前端→待更新清单端到端验收（P2）`
- **开始时间**：2026-09-11（本地，连续模式第 8 项 / 批次末项）
- **基线 HEAD**：`8c09735`（V-014 实现提交）
- **批次说明**：按用户 2026-09-10 破例指示，本批次 review 统一安排。

## 任务卡要点

- **描述**：对 KB-019/KB-020/UI-014/HY-010 做最终 fixture + 真实知识库
  只读验收，确认半年报集中导入时链路可用、可追溯、不会影响交易动作。
- **允许修改**：端到端测试、隔离 fixture、验收报告、任务档案。
- **约束**：fixture 与真实只读 smoke 分开报告；不调 LLM、不写生产 DB、
  不改知识库、不触发完整 TA、不改 prompts。

## 交付物

- `tests/test_v015_research_operations_e2e.py`（fixture 端到端，隔离 tmp
  知识库）：**10 passed**。
  - 五类回放：新增研报（ingest_key 幂等）、重复研报（duplicate_of）、
    缺元数据（needs_metadata）、半年报修订（HY-009 revised 贯穿证据）、
    事实冲突（证据 conflict + HY-010 队列 conflict）。
  - 链路一致性：证据 summary 字段与前端 UI-014 视图模型消费字段
    （FRONTEND_CONSUMED_FIELDS 对照表）逐一核对；全链路无动作语义字段。
  - 降级：局部失败、缓存损坏（损坏 JSON → 重建）、空目录均可解释降级。
- `scripts/run_v015_operations_acceptance.py` + 生成
  `docs/knowledge_reports/research-operations-acceptance-2026-09-11.md`：
  - Part 1 fixture：pytest 退出码 0（10 passed）。
  - Part 2 真实只读 smoke：KB-019 增量清单 total=1090
    （new 44 / duplicate 56 / needs_metadata 914 / stale 67 / conflict 2 /
    digested 7）；KB-020 抽样 300750.SZ、688041.SH 均 fresh、契约无违例；
    HY-010 队列（合成 universe，不读生产 DB）正常产出。
  - 结论 **PASS**。

## 过程迭代

- 冲突回放首轮未触发：只改正文未改 `financial_facts` frontmatter——
  修正为改 facts 值（冲突检测口径在 frontmatter 事实）。
- HY-010 断言两轮修正：队列聚合字段为 `.items`/`.status`；知识覆盖
  状态消费 HY-007 口径的 `has_conflict`/`facts_status`（非 data_status）。
- 报告脚本两轮修正：`delta.total()` 方法调用；smoke 返回值作用域。

## 测试与回归

| 命令 | 结果 | 退出码 |
|---|---|---:|
| `pytest tests/test_v015_research_operations_e2e.py -q` | 10 passed | 0 |
| `pytest tests/test_kb019... tests/test_kb020... tests/test_hy009... tests/test_hy010... tests/test_v015... -q` | 224 passed | 0 |
| 真实只读 smoke（脚本 Part 2） | PASS，无契约违例 | 0 |

## Codex review

按用户 2026-09-10 指示统一安排，本任务不单独 review；本档案、
端到端测试与验收报告即统一 review 材料。
