# V-014 运行档案 — 真实本地知识库只读 smoke 与研报主线验收日报

- **任务卡**：docs/TASKS.md `### V-014: 真实本地知识库只读 smoke 与研报主线验收日报（P2）`
- **开始时间**：2026-09-11（本地，连续模式第 7 项）
- **基线 HEAD**：`d8910b4`（UI-014-R1 实现提交）
- **批次说明**：按用户 2026-09-10 破例指示，本批次 review 统一安排。

## 任务卡要点

- **描述**：fixture 验收之后，对 `~/Documents/knowledge/` 做只读 smoke，
  验证真实研报/半年报能否贯穿索引、共识、反证、报告区块和 briefing 契约。
- **允许修改**：只读验收脚本、隔离测试、`docs/knowledge_reports/`、任务档案。
  业务缺陷记录为 finding，不跨范围改代码。
- **执行约束**：只读真实知识库；不写生产 DB；不调 live LLM；不触发完整
  TA；不联网；真实目录不可用必须失败并写明原因，不得用 fixture 冒充。
- **实现要点**：抽样覆盖 多研报同股 / 已有半年报 / 无半年报 / 过期知识
  四类；统计观点/事实分离覆盖率、citation 完整率、事实冲突率、待验证率、
  缓存新鲜度；验证 TA/TradeFlow/IC 输出字段可消费（不生成新结论）；
  生成 `docs/knowledge_reports/research_mainline_acceptance-YYYY-MM-DD.md`。

## 执行记录

- **脚本**：新增 `scripts/run_v014_knowledge_smoke.py`（只读；索引
  frontmatter → 四类抽样 → KB-020 逐 symbol 聚合 → 契约核验 → 统计 →
  生成日报）。
- **命令与退出码**：
  `.venv/bin/python scripts/run_v014_knowledge_smoke.py` → **exit 0（PASS）**，
  日报 `docs/knowledge_reports/research_mainline_acceptance-2026-09-11.md`。
- **真实知识库**：`/Users/maybee/Documents/knowledge`，索引 435 页
  （解析失败 0），半年报页 4，过期页 111；KB-010 只读构建缓存 435 页
  （freshness=missing，即无落盘缓存，属预期——验收不落盘）。
- **四类抽样**：多研报同股（300750/688019）、已有半年报
  （688041/688256）、无半年报（688008/603019）、过期知识页（2 页评分表）。
- **探测结果**：7 个 code symbol 全部 OK 且聚合 fresh；契约核验通过
  （证据树无 decision/action_label/buy_level 等动作字段）。
- **核心统计**：观点/事实分离覆盖率 103/435=23.7%；citation 完整率/
  待验证率——抽样无可审计声明（total=0，见 findings）；事实冲突率
  0/7=0%。
- **Findings（业务缺陷只记录，不跨范围改代码）**：
  1. `中际旭创` 等 frontmatter 条目仅有名称无 6 位代码，无法聚合查询
     （知识库侧补字段）。
  2. 7/8 抽样 citation 审计降级 insufficient_data——半年报事实缺机读
     数据或状态不可用；审计链路按契约 fail-closed，非缺陷性违例。
  3. 3 个抽样 symbol 半年报页面存在但 data_status=missing（缺机读
     financial_facts）。
  4. 观点/事实分离覆盖率 23.7% 偏低：多数历史页面 source_type 未按
     白名单标注。
- **过程迭代**：首轮探测 8 个 EXCEPTION——脚本误用 frontmatter
  "代码 名称"整串做查询；修正为提取前导代码 token，纯名称条目改记
  SKIPPED finding。知识库全程只读（未写入任何文件）。

## Codex review

按用户 2026-09-10 指示统一安排，本任务不单独 review；本档案与验收
日报即统一 review 材料。
