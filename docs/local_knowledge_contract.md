# [KB-002] investment wiki 输出协议契约（TA 可消费字段标准）

> **版本**：`kb-002-v1`
> **维护方**：TradingAgents-AShare（TA 侧）／执行方：Tree Work / HR Agent
> **前置**：KB-001 `local_knowledge_audit`（只读审计）已完成。
> **相关代码**：`tradingagents/dataflows/local_knowledge_lint.py`、`scripts/lint_local_knowledge.py`
> **最后更新**：2026-07-01

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

## 10. 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| `kb-002-v1` | 2026-07-01 | 首版契约：定义必填/推荐字段、必含章节、评分表表头、待补充标记、readiness 评分、12 条 lint 规则。 |
