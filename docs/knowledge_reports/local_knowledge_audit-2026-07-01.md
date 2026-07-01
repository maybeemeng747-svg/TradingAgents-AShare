# Tree Work 本地知识库只读审计报告 — 2026-07-01

> [KB-001] local_knowledge_audit — 只读扫描，不修改知识库；不含原文段落与敏感信息。

## 1. 扫描概览

- knowledge_root: `/Users/maybee/Documents/knowledge`
- scanned_at: 2026-07-01 12:20:21
- investment_md_pages: **76**
- inbox_items: **8**
- raw_md_files: **61**
- wiki/index.md: 存在 (investment 引用 78 条)
- wiki/log.md: 存在 (918 行)

## 2. 页面类型分布

| 类型 | 数量 |
|------|------|
| company | 40 |
| industry | 3 |
| score_table | 16 |
| summary | 2 |
| to_be_supplemented | 12 |
| unclassified | 3 |

## 3. Machine Readiness 分布

| readiness | 数量 | 占比 |
|-----------|------|------|
| high | 59 | 78% |
| medium | 2 | 3% |
| low | 15 | 20% |

## 4. Frontmatter 字段覆盖 (investment)

| 字段 | 出现页数 | 覆盖率 |
|------|----------|--------|
| `title` | 76 | 100% |
| `created` | 76 | 100% |
| `updated` | 76 | 100% |
| `sources` | 76 | 100% |
| `tags` | 76 | 100% |
| `symbols` | 76 | 100% |
| `themes` | 76 | 100% |
| `industry_chain_roles` | 76 | 100% |
| `report_type` | 76 | 100% |
| `evidence_level` | 76 | 100% |
| `valid_until` | 76 | 100% |
| `source_quality` | 76 | 100% |
| `stale_risk` | 76 | 100% |
| `deprecated` | 1 | 1% |
| `deprecated_reason` | 1 | 1% |
| `related` | 76 | 100% |

## 5. 结构缺口（>=5 类）

| 缺口类型 | 数量 | 接入建议 |
|----------|------|----------|
| 缺一句话总结/核心观点章节 | 6 | 为每篇缺失摘要的 wiki 补 ``## 一句话总结``，TA 仅取该段作为背景摘要。 |
| 缺风险提示章节 | 10 | 补 ``## 风险提示``，TA 在 raw_evidence 中标记 risk 段，避免只看利好。 |
| 缺 sources/原始资料字段或章节 | 4 | frontmatter ``sources`` 必填且非空，或在正文补 ``## 原始资料``；TA 据此溯源。 |
| 缺 machine symbols 字段 | 10 | 为每篇补 ``symbols: ["603296.SH 华勤技术"]``，TA 按 symbol 命中 wiki。 |
| 待补充/低置信页面 | 12 | TA 接入时对这些页降级为 LOW_CONFIDENCE，不进入候选加分。 |
| stale_risk=高 或 valid_until 已过期页面 | 29 | TA 命中后标记 STALE 并提示 ``needs_tree_work_research``，不直接用于实时结论。 |
| index.md 未引用的 investment 页 | 0 | Tree Work 在 index.md 补 ``[[investment/<name>]]`` 引用，保证可发现性。 |
| inbox 未消化材料 | 8 | Tree Work 优先消化 inbox；TA 只读 wiki/investment，不直接读 inbox。 |

### 缺口样本 — 缺一句话总结/核心观点章节

- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/基金分析方法论.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/星球评分体系说明.md`

### 缺口样本 — 缺风险提示章节

- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/基金分析方法论.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`

### 缺口样本 — 缺 sources/原始资料字段或章节

- `wiki/investment/基金分析方法论.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/板块轮动规律.md`

### 缺口样本 — 缺 machine symbols 字段

- `wiki/investment/化工中期策略-反内卷谋双碳.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/投资方法论.md`

### 缺口样本 — 待补充/低置信页面

- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`
- `wiki/investment/宝武镁业002182-全镁产业链龙头.md`

### 缺口样本 — stale_risk=高 或 valid_until 已过期页面

- `wiki/investment/AI算力基础设施-公司评分表.md`
- `wiki/investment/AI算力网络-公司评分表.md`
- `wiki/investment/CPO出货提速与谷歌IO-通信周报要点.md`
- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`

### 缺口样本 — inbox 未消化材料

- `2026-04-28-想法.md`
- `2026-05-27-AI机架结构剧变-内存赛道分析.jpg`
- `2026-05-27-全球存储芯片公司季度净利润排名.jpg`
- `2026-05-27-存储芯片行业趋势.md`
- `family-knowledge-prompt.md`

## 6. index.md 对齐

- 所有 investment 页均被 index.md 引用 ✓

## 7. inbox 未消化清单

- `2026-04-28-想法.md`
- `2026-05-27-AI机架结构剧变-内存赛道分析.jpg`
- `2026-05-27-全球存储芯片公司季度净利润排名.jpg`
- `2026-05-27-存储芯片行业趋势.md`
- `family-knowledge-prompt.md`
- `screenshots/20260515-152102.png`
- `screenshots/20260515-152300.png`
- `screenshots/20260515-152320.png`

## 8. TA 接入建议（KB-002/KB-003 前置）

1. TA 仅消费 ``wiki/investment`` 已消化页面，禁止把 raw/inbox 当结论源。
2. 对接字段建议（KB-002 契约草案）: symbols / themes / industry_chain_roles / report_type / evidence_level / valid_until / source_quality / stale_risk。
3. 命中 evidence_level=C 或 stale_risk=高 的页面，TA 标 LOW_CONFIDENCE/STALE，不提升候选层级。
4. 评分表类型必须保留表头: 公司|代码|核心业务|板块|利好度|共识度|预计启动|期待周期。
5. 无 symbols/themes 的页面不进入 KB-003 symbol 命中索引，避免误召回。
6. machine_readiness=low 的页面优先回 Tree Work 补字段，TA 侧只列缺口不阻塞主链路。

---

_本报告由 ``scripts/audit_local_knowledge.py`` 只读生成；对应模块 ``tradingagents.dataflows.local_knowledge_audit``。_
