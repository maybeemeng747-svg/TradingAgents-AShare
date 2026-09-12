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

## 修复轮 1（统一 review finding 2 [P1]，2026-09-12）

审核确认：真实 smoke 对"空目录 + 证据全 missing"仍判 PASS——把
"fixture 降级测试通过"误当"真实链路可用"；摄取错误只打印不影响结论。

修复（`run_v015_operations_acceptance.py`）新增链路可用性门禁，任一
未通过即总体 FAIL（exit 1）：

1. 结构门禁：真实根必须有 `wiki/investment` 且 ≥1 页；
2. 摄取门禁：KB-019 扫描 errors 非空 → FAIL；
3. 可用性门禁：抽样中至少一只 consensus has_hit——全部 missing 只证明
   降级路径正常，不能证明链路可用；
4. 契约门禁：证据/队列出现动作语义字段或聚合异常 → FAIL。

报告显式区分"fixture 回放"与"真实链路可用性"，门禁段落落盘。

验证：审核复现场景（空目录）实跑 **exit 1 / FAIL**（报告注明必需
目录缺失、全部 evidence missing）；真实库重跑 **exit 0 / PASS**，
437 页、门禁全绿，报告重新生成为
`docs/knowledge_reports/research-operations-acceptance-2026-09-12.md`
（旧的带缺陷 09-11 报告已删除）。fixture e2e 10 passed 不变；链路域
回归 5 套件 224 passed 不变。

## Codex review

按用户 2026-09-10 指示统一安排。第一轮统一 review 结论为暂不通过
（本任务占 1 项 P1，见上"修复轮 1"）；修复已完成并实跑正反两个场景
验证，待复审。

## 修复轮 2（复审 finding [P1]，2026-09-12）

复审确认：故障注入"共识正常命中，引用审计/逻辑时间线/半年报事实/评分
快照全部返回 failed"时 `usability_gates_failed = []`——门禁只查共识
命中/except/禁用字段，未检查模块级结构化失败（真实 API 会把部分异常
转换为 `data_status=failed` 的 bucket，不抛异常）。

修复（`run_v015_operations_acceptance.py`）：

1. 门禁评估器抽为纯函数 `evaluate_evidence_gates(probes)`（可注入
   测试）；探测收集新增各 bucket 的 errors 携带；
2. 新增结构化失败门禁：五个模块任一 `data_status=failed` 即逐一点名
   FAIL（含其 errors），`failed` 不得宣称全链路可用；正常 `missing`
   与查询失败分开处理（missing 不阻断，failed 阻断）；
3. `run_real_smoke` 支持 `probe_symbols` 注入供回归使用。

自动回归（新增 `tests/test_v015_acceptance_gates.py`，**10 passed**）：

- 评估器 6 项：健康样本通过 / missing≠failed / 部分模块 failed 逐一点名
  （审核注入场景）/ 全部 failed（5 failed + 共识门禁）/ 聚合异常 /
  契约违例；
- 脚本集成 4 项：空库结构门禁、部分结构化失败阻断 PASS、全部失败
  阻断 PASS、健康样本通过（走真实 `build_research_evidence` +
  monkeypatch 模块构造器注入状态）。

双向实跑：空目录 **exit 1 / FAIL**；真实库 437 页 **exit 0 / PASS**，
报告重新生成。链路域回归（gates/e2e/kb020/kb019）**138 passed**。

## Codex review

第一轮：暂不通过（本任务 P1 空库误判）→ 修复轮 1 完成。
复审：其余通过，剩 1 项 P1 结构化失败门禁 → 修复轮 2 完成（本轮），
待再次复审。
