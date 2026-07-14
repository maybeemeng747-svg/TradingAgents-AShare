# [KB-002 / HY-001] investment wiki 输出协议契约（TA 可消费字段标准）

> **版本**：`kb-002-v1`（含 HY-001 半年报扩展与 HY-011 元数据 sanity）
> **维护方**：TradingAgents-AShare（TA 侧）／执行方：Tree Work / HR Agent
> **前置**：KB-001 `local_knowledge_audit`（只读审计）已完成；KB-012 Tree Work 任务包导出已完成。
> **相关代码**：`tradingagents/dataflows/local_knowledge_lint.py`、`scripts/lint_local_knowledge.py`
> **最后更新**：2026-07-14

---

## 0. 目标与边界

本契约规定 Tree Work 在 `wiki/investment/` 下产出的每篇投资 wiki **必须满足**的机器可消费字段与章节标准。目的是让 TA 在 KB-003/KB-004 中能稳定地按 `symbol / theme / 产业链角色 / 风险 / 来源 / 时效` 命中并追溯页面，避免“消化了研报但 TA 仍抓不到”。

**边界**：
- 本契约只在 TA 侧定义并 lint，**不批量改写历史 76 篇**；历史页面由 lint 输出缺口清单，Tree Work 逐步补齐。
- lint 是**建议性**工具：`machine_readiness=low` 的页面只降低本地知识源置信度（TA 标 `LOW_CONFIDENCE` / `STALE`），**不阻塞** TA 主链路、不阻止 KB-003 接入。
- TA **只消费** `wiki/investment/` 已消化页面，**禁止**把 `raw/` / `inbox/` 当结论源。

---

## 1. 基础 frontmatter（必填）

每篇 investment wiki 必须保留以下 frontmatter 字段且**非空**。缺失任一即触发 `FMR-001`（error，`related` 为 warning）。

| 字段 | 必填 | 类型 | 示例 | 说明 |
|------|------|------|------|------|
| `title` | ✅ error | string | `华勤技术603296-超节点进入出货周期` | 页面标题，TA 索引展示名 |
| `created` | ✅ error | date `YYYY-MM-DD` | `2026-05-25` | 创建日期 |
| `updated` | ✅ error | date `YYYY-MM-DD` | `2026-06-29` | 最近更新日期；TA 据此判断新鲜度 |
| `sources` | ✅ error | 非空 list | `- "[[../../raw/xxx.md\|中邮证券-华勤技术]]"` | 原始资料引用；空 `[]` 视为缺失 |
| `tags` | ✅ error | 非空 list | `[华勤技术, 超节点]` | 关键词标签 |
| `related` | ⚠️ warning | list | `[[investment/PCB产业链综述]]` | 关联页面；可空 `[]`，但建议补 |

---

## 2. 推荐机器字段（TA 可消费）

以下字段缺失触发 `FMR-002`（warning）。它们是 KB-003（symbol 命中）、KB-004（昊天候选加分）、KB-007（研报关注度）的前置索引字段。

| 字段 | 类型 | 取值 / 示例 | TA 用途 |
|------|------|-------------|---------|
| `symbols` | list[string] | `["603296.SH 华勤技术"]`（代码+空格+简称） | KB-003 按 symbol 命中；公司/评分表页**强烈建议必填**（缺则 `SYM-001` error） |
| `themes` | list[string] | `[AI服务器]` | 按主题召回相关 wiki |
| `industry_chain_roles` | list[string] | `[AI服务器ODM]` | 昊天左侧候选定位产业链角色 |
| `report_type` | string | `公司点评` / `公司分析` / `财报分析` / `行业` / `周报` / `策略` / `深度` / `综述` / `产业链` / `数据表` | 页面类型分类 |
| `evidence_level` | enum | `A` / `B` / `C`（C=低置信） | `C` → TA 标 `LOW_CONFIDENCE`（`EVID-001` info） |
| `valid_until` | date 或 `长期` | `2026-12-31` / `长期` | 过期 → TA 标 `STALE`（`STALE-002` warning） |
| `source_quality` | enum | `高` / `中` / `低` | 来源质量评分 |
| `stale_risk` | enum | `低` / `中` / `高` | `高` → TA 标 `STALE`，不用于实时结论（`STALE-001` warning） |

**`symbols` 格式约定**：`<6位代码>.<交易所> <简称>`，如 `603296.SH 华勤技术`、`000977.SZ 浪潮信息`、`DELL.US Dell`。代码与简称之间用单空格分隔。

---

## 3. 正文必含章节

正文（frontmatter 之后）必须用 `## ` 标题包含以下章节。缺失触发对应规则。

| 章节（任一匹配即可） | 规则 | 严重度 | TA 用途 |
|----------------------|------|--------|---------|
| `一句话总结` / `核心观点` / `核心结论` / `投资逻辑` / `核心数据` | `SEC-001` | error | TA 取该段作为背景摘要 |
| `风险提示` / `风险` / `风险与不确定性` | `SEC-002` | error | TA 在 raw_evidence 中标记 risk 段，避免只看利好 |
| `原始资料` / `关联研报` / `资料来源` / `参考资料` | `SEC-003` | warning | 溯源；若 frontmatter `sources` 已非空则可省略 |
| `投资逻辑` / `核心观点` / `核心结论`（专用） | `SEC-004` | warning | TA 作为观点源 |

> **注**：`SEC-001` 与 `SEC-004` 有重叠——只要存在 `投资逻辑`/`核心观点`/`核心结论` 之一，两条规则同时满足。若仅有 `一句话总结`，则 `SEC-004` 仍会提示补 `投资逻辑`。

---

## 4. 公司评分表表头契约

`report_type` 为 `数据表` / `评分表` 或文件名含“评分表”的页面，必须包含标准 markdown 表头（顺序无关，8 列都必须出现）。缺失触发 `TBL-001`（error）。

**标准表头**：

```markdown
| 公司 | 代码 | 核心业务 | 板块 | 利好度 | 共识度 | 预计启动 | 期待周期 |
|------|------|----------|------|--------|--------|----------|----------|
| 华勤技术 | 603296 | ODM | AI算力 | 9.5 | 90 | 几天 | 半年 |
```

TA 据此解析候选评分（利好度/共识度/预计启动/期待周期），用于昊天左侧候选排序。

---

## 5. 待补充 / 低置信显式标记（契约第 5 条）

未验证、扫描失败或内容不完整的页面**必须**显式标记，符合契约第 5 条。这是**预期行为**，触发 `TODO-001`（info，不计入 readiness 惩罚）。

**标记方式（任一）**：
- frontmatter `evidence_level: C`
- 正文包含任一关键词：`此页面内容不完整` / `待补充` / `内容待补充` / `占位`
- 文件名或 title 含 `待补充`

**TA 行为**：命中此类页面 → `machine_readiness=low` → 标 `LOW_CONFIDENCE`，**不进入候选加分**，仅作背景记录。

---

## 6. Machine Readiness 评分规则

lint 基于规则 findings 显式计算每页 `machine_readiness`：

| readiness | 条件 | TA 行为 |
|-----------|------|---------|
| **high** | 0 error 且 ≤2 warning 且非待补充 且非 stale | 直接消费，作为正常背景/观点源 |
| **medium** | ≤2 error 且 ≤4 warning | 消费但需补字段；KB-003 命中后提示“字段待补” |
| **low** | 待补充标记 / evidence_level=C / 缺口过多 / stale | 标 `LOW_CONFIDENCE`/`STALE`，不进入候选加分 |

**特例**：`is_to_be_supplemented=True`（含待补充标记）一律为 `low`，无论 findings 数量——TA 不应基于未完成页做结论。

---

## 7. lint 规则总表

| rule_id | 严重度 | 触发条件 | 默认修复建议 |
|---------|--------|----------|--------------|
| `FMR-001` | error / warning* | 必填 frontmatter 字段缺失/为空（`related` 为 warning） | 补对应字段 |
| `FMR-002` | warning | 推荐机器字段缺失 | 补对应机器字段 |
| `SEC-001` | error | 缺一句话总结/核心观点章节 | 补 `## 一句话总结` |
| `SEC-002` | error | 缺风险提示章节 | 补 `## 风险提示` |
| `SEC-003` | warning | 缺原始资料/关联研报章节 | 补 `## 原始资料` 或 frontmatter sources |
| `SEC-004` | warning | 缺投资逻辑/核心观点章节 | 补 `## 投资逻辑` |
| `SYM-001` | error | 公司/评分表页缺 symbols | 补 `symbols: ["603296.SH 华勤技术"]` |
| `TBL-001` | error | 评分表缺标准表头列 | 补全 8 列表头 |
| `TODO-001` | info | 显式标记待补充/低置信（符合契约） | 无需修复，保持标记 |
| `STALE-001` | warning | `stale_risk=高` | TA 标 STALE，回 Tree Work 更新 |
| `STALE-002` | warning | `valid_until` 已过期 | 更新 valid_until 或重新消化 |
| `EVID-001` | info | `evidence_level=C` 低置信 | TA 标 LOW_CONFIDENCE，补证后升级 |

\*`FMR-001` 对 `sources/title/created/updated/tags` 为 error，对 `related` 为 warning。

---

## 8. 如何运行 lint

```bash
# 默认扫描 ~/Documents/knowledge，打印 Markdown 到 stdout
python scripts/lint_local_knowledge.py

# 指定知识库根目录
python scripts/lint_local_knowledge.py --knowledge-root ~/Documents/knowledge

# 写入报告文件
python scripts/lint_local_knowledge.py \
  --output docs/knowledge_reports/local_knowledge_lint-2026-07-01.md

# 结构化 JSON（下游程序消费）
python scripts/lint_local_knowledge.py --json --stdout

# CI 门禁：有 error 级 finding 时返回退出码 2
python scripts/lint_local_knowledge.py --fail-on-error
```

**程序化调用**：

```python
from tradingagents.dataflows.local_knowledge_lint import (
    lint_local_knowledge,
    render_lint_report,
)
result = lint_local_knowledge("/Users/maybee/Documents/knowledge")
# result.readiness_counts -> {"high": N, "medium": N, "low": N}
# result.page_results[i].findings -> [LintFinding(rule_id, severity, fix_suggestion, ...)]
# result.top_fix_priorities -> 按影响面排序的修复清单
print(render_lint_report(result))
```

**退出码策略**（KB-002 验收要求“低分页面不阻塞 TA”）：
- 默认退出码 `0`：即使有 error finding 也只输出报告，不阻塞。
- 根目录不存在且 0 页：退出码 `1`（环境异常）。
- `--fail-on-error`：有 error finding 返回 `2`（仅 CI 门禁场景）。

---

## 9. Tree Work 交付清单（按优先级）

执行方按 lint 报告 `## 5. Top 修复优先级` 顺序处理，建议顺序：

1. **补 `symbols` 字段**（公司/评分表页）：KB-003 symbol 命中的硬前置。
2. **补 `## 风险提示`**：TA 不应只看利好。
3. **补 `## 一句话总结`**：TA 取该段作背景摘要。
4. **补非空 `sources`**：溯源基础。
5. **更新过期 / stale_risk=高 页面**：降低 STALE 误判。
6. `related` / 推荐机器字段：非阻塞，逐步补齐。

历史 76 篇不要求一次性重写，按 lint 输出的缺口清单逐篇补齐即可。

---

## 10. 半年报 / 财报分析扩展契约（HY-001）

> **触发条件**：`report_type ∈ {财报分析, 半年报, 中报}` 的页面（含港股/美股中报页），自动进入半年报扩展 lint。普通公司点评/行业/评分表页**不**触发本节规则。
> **目的**：让 Tree Work 消化的财报页能稳定区分 **财报事实 / 管理层表述 / 券商观点 / 二级市场演绎**，避免 TA 把“券商观点”当成“公司事实”。

### 10.1 半年报扩展 frontmatter 字段

| 字段 | 必填 | 类型 | 取值 / 示例 | TA 用途 |
|------|------|------|-------------|---------|
| `financial_period` | ✅ error | string | `2025H1` / `2025中报` / `2025年报` / `FY26Q1` | 报告期唯一标识；TA 据此判断是否最新、是否过期 |
| `period_end_date` | optional | date `YYYY-MM-DD` | `2025-06-30` | 财务报告期末日；存在时参与 HY-011 组合校验 |
| `report_date` | optional | date `YYYY-MM-DD` | `2026-08-29` | 资料/研报发布日期；供 ZCode `evidence_refs` 使用，不参与财务期末校验 |
| `disclosure_date` | ⚠️ warning | date `YYYY-MM-DD` | `2026-08-30` | 交易所披露日；缺失则 TA 标 `disclosure_unknown` |
| `scheduled_disclosure_date` | optional | date `YYYY-MM-DD` | `2026-08-30` | 尚未发生的预排期披露日；不得提前写入 `disclosure_date` |
| `source_type` | ⚠️ warning | 非空 list，至少含一个“事实类”来源 | `[exchange_filing, management_commentary]` | 标注每个事实/观点的来源类型，见下表 |
| `financial_facts` | ⚠️ warning | 非空 list | `[营收 150亿 (+30%), 毛利率 25% (-2pp)]` | 结构化财务事实；TA/HY-003 据此反证研报观点 |
| `segment_facts` | optional | list | `[服务器营收 80亿 (+50%)]` | 分业务/分产品事实 |
| `management_commentary` | optional | list | `[管理层上调全年AI收入指引]` | 管理层口径；与事实分开存 |
| `forward_guidance` | optional | list | `[下半年CapEx同比+50%]` | 公司前瞻指引；属“半事实”，单独存 |
| `risk_factors` | ⚠️ warning | 非空 list | `[汇率风险, 客户集中度]` | 结构化风险清单；与正文 `## 风险提示` 章节互补 |
| `source_links` | optional | list | `[巨潮资讯 <url>, 交易所公告 <url>]` | 公告/披露原文链接，便于追溯 |

**`financial_period` 格式约定**（任一即可）：
- `YYYYH1` / `YYYYH2`（如 `2025H1`）
- `YYYY中报` / `YYYY年报` / `YYYY一季报` / `YYYY三季报`
- `FY<YY>Q<N>`（如 `FY26Q1`，对应美股/港股财年口径）

**`source_type` 取值表**：

| 取值 | 类别 | 说明 |
|------|------|------|
| `exchange_filing` | 事实类 | 交易所公告、定期报告披露原文 |
| `fact_table` | 事实类 | 数据表/统计表（营收、毛利率等可验证数字） |
| `management_commentary` | 公司口径 | 管理层电话会/业绩说明会/经营讨论（公司自述） |
| `broker_report` | 券商观点 | 卖方研报观点（**不得作为事实**） |
| `media` | 媒体观点 | 媒体报道/二手解读（**不得作为事实**） |

### 10.2 半年报页面正文必含章节（在 KB-002 通用章节之外）

半年报页**仍需**满足第 3 节通用章节（一句话总结/风险提示/原始资料）。除此之外：
- 推荐章节 `## 核心数据` / `## 关键财务指标`（结构化事实区）。
- 推荐章节 `## 管理层表述` / `## 经营讨论`（与事实分开）。
- 推荐章节 `## 前瞻指引`（forward_guidance 章节化版）。
- 若页面只有券商观点而无公告/事实：在 frontmatter `source_type` 显式声明 `broker_report`，并在正文章节标题加 `券商观点` 字样，**不得**用“营收/利润”等事实性章节名。

### 10.3 半年报扩展 lint 规则

| rule_id | 严重度 | 触发条件 | 默认修复建议 |
|---------|--------|----------|--------------|
| `HYF-001` | error | 半年报/财报页缺 `financial_period` 或格式不合法 | 补 `financial_period: 2025H1` / `FY26Q1` / `2025中报` |
| `HYF-002` | error | 半年报/财报页缺 `symbols`（含 `report_type=财报分析` 但 symbols 为空） | 补 `symbols: ["603296.SH 华勤技术"]` |
| `HYF-003` | warning | 半年报/财报页缺 `disclosure_date` 或日期格式不合法 | 补 `disclosure_date: 2026-08-30` |
| `HYF-004` | warning | 半年报/财报页缺 `source_type` | 补 `source_type: [exchange_filing, fact_table]` |
| `HYF-005` | warning | 半年报/财报页缺 `financial_facts`（无可机读事实） | 补 `financial_facts: [营收 150亿 (+30%)]`，HY-003 据此反证 |
| `HYF-006` | warning | 半年报/财报页缺 `risk_factors`（结构化风险） | 补 `risk_factors: [客户集中度, 汇率]`，与 `## 风险提示` 互补 |
| `HYF-007` | warning | 半年报/财报页 `source_type` 全是 `broker_report`/`media`（券商/媒体观点冒充事实） | 至少补一个 `exchange_filing` / `fact_table` / `management_commentary`；或在 `report_type` 改回 `公司点评` |

> **判级原则**：HYF-001/HYF-002 为 error（缺报告期或缺代码，HY-003 事实索引无法建表）；HYF-003~006 为 warning（事实缺失降低置信度但不阻塞）；HYF-007 为 warning（观点/事实混用是最常见错误，TA 命中后标 `OPINION_AS_FACT`，不直接加分）。

### 10.4 Tree Work 半年报 ingest 模板（可直接照抄）

> 复制此模板填字段，Tree Work 每篇半年报/中报页都应满足 HY-001 契约。

```markdown
---
title: <公司简称><代码>-<报告期>财报
created: 2026-08-30
updated: 2026-08-30
sources:
  - "[[../../raw/2026-08-30-<券商>-<公司>-2025H1.md|<券商>-<公司>-2025H1]]"
tags: [<公司简称>, 财报, 2025H1]
related: [[investment/<公司简称>公司分析]]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器]
industry_chain_roles: [AI服务器ODM]
report_type: 半年报
evidence_level: A
valid_until: 2027-08-31
source_quality: 高
stale_risk: 低
# ── HY-001 半年报扩展字段 ──
financial_period: 2025H1
period_end_date: 2025-06-30
disclosure_date: 2026-08-30
source_type: [exchange_filing, fact_table, management_commentary]
financial_facts:
  - 营收 150.2亿 (+30.1% YoY)
  - 归母净利 18.5亿 (+45.0% YoY)
  - 毛利率 25.3% (+1.2pp YoY)
  - 经营性现金流 22.1亿 (+18%)
segment_facts:
  - AI服务器营收 80亿 (+50% YoY)，占总营收 53%
  - 消费电子营收 50亿 (-5% YoY)
management_commentary:
  - 管理层上调全年AI收入指引至 +60%
forward_guidance:
  - 下半年CapEx同比+50%，扩产AI服务器
risk_factors: [客户集中度, 美元汇率, AI需求波动]
source_links:
  - 巨潮资讯 <公告URL>
  - 交易所披露 <URL>
---

# <公司简称>（<代码>）— <报告期>财报

## 一句话总结

<1-2 句：本次报告期最关键的事实变化，不带“强烈看好”等观点词。>

## 核心数据

| 指标 | <报告期> | 同比 |
|------|----------|------|
| 营收 | 150.2亿 | +30.1% |
| 归母净利 | 18.5亿 | +45.0% |
| 毛利率 | 25.3% | +1.2pp |

## 分业务事实

- AI服务器：营收 80亿（+50%），占总营收 53%
- 消费电子：营收 50亿（-5%）

## 管理层表述

- 上调全年AI收入指引至 +60%

## 前瞻指引

- 下半年CapEx同比+50%，扩产AI服务器

## 风险提示

- 客户集中度：前五大客户占比 >70%
- 美元汇率波动
- AI需求不及预期

## 原始资料 / 公告链接

- 巨潮资讯：<公告URL>
- 关联研报：[[../../raw/2026-08-30-<券商>-<公司>-2025H1.md|<券商>]]
```

### 10.5 半年报 lint 与 KB-002 通用 lint 的关系

- 半年报页**同时**跑通用规则（FMR/SEC/SYM/STALE）和 HYF 规则；通用规则先跑，HYF 规则在通用之后追加。
- HYF-001 / HYF-002 计入 `error_count`，会影响 `machine_readiness`：财报页缺报告期或缺代码 → readiness 至多为 `medium`；若同时缺 4+ 字段 → `low`。
- HYF-007 命中时 readiness 不强制降到 low，但 TA 在 KB-003/HY-003 接入时会给该页 `OPINION_AS_FACT` 标记，**不进入事实反证**（HY-005），只作为观点源。
- 待补充页（`is_to_be_supplemented=True`）仍按 KB-002 规则一律 `low`，HYF 规则照常报但不重复降级。

### 10.6 HY-011 元数据组合与修订版本规则

HY-011 不补猜缺失字段，只在字段存在时检查组合关系：

| rule_id | 严重度 | 触发条件 |
|---------|--------|----------|
| `HYM-001` | warning | 实际 `disclosure_date` 不是合法日历日或晚于检查日；预排期应写 `scheduled_disclosure_date` |
| `HYM-002` | error | `period_end_date` 晚于实际披露日 |
| `HYM-003` | warning | `report_type=半年报/中报`，但 `financial_period` 不是 H1/中报周期 |
| `HYM-004` | warning | `symbols` 不是 `CODE NAME`，代码非法，或显式 `name` 与简称不一致 |
| `HYM-005` | warning | 同股票、同报告期存在多个页面，但没有任何修订关系标记 |
| `HYM-006` | info | 检测到 `revision/is_revised/supersedes/amendment`，保留原稿与修订稿路径 |
| `HYM-007` | warning | `period_end_date` 非法，或不等于 `financial_period` 对应的完整期末日期（含年份） |

`period_end_date` 与 `report_date` 不得混用：前者是被分析财务周期的期末日，后者是
资料本身的发布日期，并与 ZCode `evidence_refs.report_date` 保持一致。修订标记只
影响事实索引选择，不产生交易动作，也不删除原稿来源。

---

## 11. 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| `kb-002-v1` | 2026-07-01 | 首版契约：定义必填/推荐字段、必含章节、评分表表头、待补充标记、readiness 评分、12 条 lint 规则。 |
| `kb-002-v1 + HY-001` | 2026-07-07 | HY-001 半年报/财报扩展：新增 9 个 frontmatter 字段、source_type 取值表、7 条 HYF lint 规则、Tree Work ingest 模板。通用规则不变，HYF 规则为追加层。 |
| `kb-002-v1 + HY-011` | 2026-07-14 | 明确 period_end_date/report_date/disclosure_date 语义，新增 HYM-001~007 组合关系与修订版本 lint。 |
