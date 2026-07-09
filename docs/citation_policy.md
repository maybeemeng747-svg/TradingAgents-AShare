# [KB-014] citation_policy — 研报/财报来源可信度分层与引用政策

> **版本**：`kb-014-v1`
> **维护方**：TradingAgents-AShare（TA 侧）
> **前置**：KB-001 只读审计、KB-002 契约 lint、KB-003 本地知识 raw_evidence、DATA-025 研报源目录
> **相关代码**：`tradingagents/dataflows/citation_policy.py`、`tradingagents/dataflows/local_knowledge_lint.py`（CIT- 规则）、`tradingagents/dataflows/local_knowledge_provider.py`（tier 透传）
> **最后更新**：2026-07-08

---

## 0. 目标与边界

本政策定义 TA 报告 / 本地知识在引用来源时的**可信度分层**，明确公告/财报原文、券商研报、媒体观点、用户笔记的**使用边界**，避免"券商观点"被当作"公司事实"进入 raw_evidence 与候选加分。

**边界**：
- 本政策**不抓取新研报正文**，只对 KB-001/KB-002 已解析的 frontmatter（`source_type` / `sources` / `report_type`）做规则化分类。
- **不把来源可信度直接转成交易动作**：`tier` 只影响 `confidence` / `attention` / `local_knowledge_score` 的**软调节**，不改变 DECISION-001/TF-QUALITY-001 等强动作门禁。
- **对缺来源或来源弱的页面降低 confidence/attention，而不是直接过滤**：`media` / `user_note` / `unknown` 仍保留在 `matched_pages` 中，但不计入 `fresh_hit` / `local_knowledge_score`，只作为线索提示补来源。
- **不改 prompts**。

---

## 1. 来源可信度分层（`source_quality_tier`）

每个 investment wiki 页面在 lint 时被自动分类为以下六类之一：

| tier | confidence_weight | 可作为事实 | 使用边界 |
|------|-------------------|-----------|----------|
| `original_filing` | 1.00 | ✅ | 可作为事实进入 raw_evidence / HY-005 事实反证。 |
| `official_notice` | 1.00 | ✅ | 可作为官方事实；优先级高于券商/媒体观点。 |
| `broker_research` | 0.60 | ❌ | 仅作观点/线索，不得冒充事实进入反证或加分。 |
| `media` | 0.30 | ❌ | 仅作背景/线索，不得作为结论依据。 |
| `user_note` | 0.20 | ❌ | 仅作背景记录；不进入候选加分；建议补来源后升级。 |
| `unknown` | 0.10 | ❌ | 来源不可识别，TA 标 `WEAK_SOURCE`；建议回 Tree Work 补 `source_type` / `sources`。 |

**判级原则**：
- `original_filing` / `official_notice` 为**事实类**（`is_fact_usable=True`），可进入事实反证。
- `broker_research` / `media` / `user_note` / `unknown` 为**观点/线索类**（`is_opinion_only=True`），不得冒充事实。
- `media` / `user_note` / `unknown` 同时标 `weak_source_risk=True`，触发 CIT-003 info。

---

## 2. 分类信号（判定优先级）

`classify_source_quality_tier(frontmatter)` 按以下顺序判定，取**最高可信度**：

1. **`source_type` 字段（HY-001 半结构化白名单）**：
   - `exchange_filing` / `fact_table` / `management_commentary` → `original_filing`；
   - `official_notice` / `regulatory_notice` → `official_notice`；
   - `broker_report` → `broker_research`；
   - `media` → `media`。
   - 同时存在事实类与观点类时，取最高的（事实类优先）。

2. **`sources` 字段关键词识别**：
   - 单条 source 内先识别显式来源主体，再处理 `新闻` / `报道` / `解读` 等泛词，避免 `中信证券新闻点评研报` 被误降为媒体，也避免 `证监会新闻发布会` 被误降为媒体；
   - 多条 sources 之间取最高可信度，例如 `["巨潮资讯公告", "财联社报道"]` → `original_filing`；
   - 命中公司公告/披露关键词（`巨潮资讯` / `公司公告` / `公告披露` / `交易所披露` / `定期报告` …）→ `original_filing`；
   - 命中监管/官方关键词（`证监会` / `证券监督管理委员会` / `证券交易所` / `交易所监管` / `监管函` / `问询函` …）→ `official_notice`；
   - 命中券商关键词（`券商` / `研报` / `首席` / `证券研究` / `研究报告` / `深度报告` …）或券商名称（如 `中信证券`）→ `broker_research`；
   - 命中媒体机构关键词（`媒体` / `财联社` / `中国证券报` / `证券时报` / `新华社` / `新华网` / `新浪` / `腾讯` …）→ `media`；仅有 `新闻` / `报道` / `解读` 这类泛词时，在显式券商/监管/公告主体之后兜底为 `media`；注意不使用单独的 `新华` 关键词，避免误伤 `新华保险公司公告` / `新华制药公司公告`；
   - 命中个人笔记关键词（`个人观点` / `我的` / `笔记` / `记录` / `复盘` …）→ `user_note`；
   - sources 非空但未命中任何关键词 → 保持 `unknown`，让 `report_type` 兜底判断是否为财报/公告页。
   - `source_type` 非空但取值拼错/未知 → 不视作有效来源，不得触发财报兜底升权。
   - 注意：`披露`、`年报`、`中报`、`半年报` 等单词只作为上下文词，不单独作为 sources 公告关键词；否则会把“券商年报点评研报”或“公司披露订单”的观点源误判成公告原文。

3. **`report_type` 兜底**（仅当 source_type / sources 都未给出明确 tier）：
   - `report_type ∈ {财报分析, 半年报, 中报, 年报, 一季报, 三季报, 公告}` + 有 sources → 兜底升为 `original_filing`；
   - 上述 report_type 但完全缺 sources → 降级为 `user_note`。
   - **关键约束**：若 sources 已识别为 broker_research / media，或只有无法识别的 `source_type`，**不**因 report_type 而强行升级，避免"半年报 + 券商研报来源"或"拼错 source_type"误判为 original_filing（观点/未知来源冒充事实）。

4. **完全缺来源字段**：仅有 `tags` / `title` → `user_note`；全空 → `unknown`。

---

## 3. lint 规则（CIT-）

KB-014 在 KB-002 通用规则与 HYF- 规则之后追加三条 CIT- 规则：

| rule_id | 严重度 | 触发条件 | 默认修复建议 |
|---------|--------|----------|--------------|
| `CIT-001` | warning | 页面缺来源字段（`sources` / `source_type` 均空且无公告关键词）→ tier=unknown | 在 frontmatter 补 `source_type: [exchange_filing]` 或 `sources: [巨潮资讯 <公告URL>]` |
| `CIT-002` | info | `report_type` 为财报/公告披露类但 tier 为 `broker_research` / `media` / `user_note`（观点冒充事实风险） | 补 `source_type: [exchange_filing, fact_table]` 升级为 original_filing，或把 `report_type` 改回 `公司点评` |
| `CIT-003` | info | tier 为 `media` / `user_note` / `unknown`（弱来源） | TA 标 `WEAK_SOURCE`，不进入候选加分；建议补公告/研报链接后升级 |

**重要约束**：
- CIT 规则**不强制降 `machine_readiness`**：CIT-001 是 warning，CIT-002/CIT-003 是 info，确保弱来源页面只被**软降权**而不是被"过滤"。
- CIT 规则在 HYF 规则之后跑，便于报告里"通用规则 → HYF → CIT"的顺序阅读。

---

## 4. 对 confidence / score 的软调节

### 4.1 `apply_tier_to_confidence`

在 `LocalKnowledgeMatch` 构造时，把 KB-003 的 `confidence`（基于 readiness/stale/low 计算）按 tier 软降级：

| tier | high → | medium → | low → |
|------|--------|----------|-------|
| `original_filing` / `official_notice` | high | medium | low |
| `broker_research` | **medium** | medium | low |
| `media` / `user_note` / `unknown` | **low** | **low** | low |

**示例**：一份券商深度研报（`broker_research`），即使 `readiness=high` + `evidence_level=A`，最终 `confidence` 也只有 `medium`——观点不应等同事实。

### 4.2 `compute_tier_confidence_weight`

综合 tier × readiness × stale/low 计算最终权重（0.0~1.0），供 KB-004 `_page_score` 软调节使用：

```
base = TIER_CONFIDENCE_WEIGHTS[tier]      # 1.0 / 0.6 / 0.3 / 0.2 / 0.1
multiplier = 1.0
if is_to_be_supplemented or is_low_confidence: multiplier *= 0.3
if is_stale:                                multiplier *= 0.3
if readiness == 'medium':                   multiplier *= 0.7
if readiness == 'low':                      multiplier *= 0.3
weight = clamp(base * multiplier, 0.0, 1.0)
```

### 4.3 KB-004 `_page_score` 与 weak-source gate

`local_knowledge_score` 的单页命中分：

```
score = PAGE_SCORE_WEIGHTS[confidence] * citation_confidence_weight
```

| 命中类型 | confidence | weight | 单页贡献 |
|----------|-----------|--------|---------|
| 财报原文 + readiness=high | high | 1.0 | 1.00 |
| 券商研报 + readiness=high | medium | 0.6 | 0.36 |
| 媒体 + readiness=high | low | 0.3 | 0.00（弱来源，不加分） |
| 用户笔记 + readiness=high | low | 0.2 | 0.00（弱来源，不加分） |
| 来源不可识别 + readiness=high | low | 0.1 | 0.00（弱来源，不加分） |

**关键约束**：弱来源**不被过滤**，仍计入 `matched_pages_brief` 与 `knowledge_hit_count`，方便前端提示用户更新；但不计入 `fresh_hit_count` / `has_hit`，不会阻止 `needs_tree_work_research=True`。

---

## 5. 使用边界（citation policy 摘要）

### 5.1 可作为"事实"的来源

- ✅ 交易所公告 / 定期报告披露原文（`exchange_filing` / `fact_table`）；
- ✅ 监管 / 官方文件（`official_notice`）；
- ✅ 管理层电话会 / 业绩说明会 / 经营讨论（`management_commentary`，公司自述视作"半事实"，可进入反证但需打公司口径标）。

**用途**：进入 `raw_evidence.local_knowledge` / HY-005 事实反证 / 候选加分。

### 5.2 只能作为"观点/线索"的来源

- ❌ 券商研报 / 卖方观点（`broker_research`）；
- ❌ 媒体报道 / 二手解读（`media`）。

**用途**：作背景摘要 / 主题热度线索；**不得**进入事实反证；**不得**直接拉高 candidate tier / score。TA 命中后标 `OPINION_AS_FACT` 风险。

### 5.3 仅作"背景记录"的来源

- ❌ 用户自填笔记 / 观察（`user_note`）；
- ❌ 来源字段全缺或无法识别（`unknown`）。

**用途**：仅作背景；**不进入候选加分**；建议回 Tree Work 补来源后升级。TA 命中后标 `WEAK_SOURCE`。

---

## 6. 程序化调用

```python
from tradingagents.dataflows.citation_policy import (
    classify_source_quality_tier,
    compute_tier_confidence_weight,
    apply_tier_to_confidence,
    render_citation_summary,
    render_tier_table,
)

# 1) 评估单页 tier
assessment = classify_source_quality_tier(frontmatter)
print(assessment.tier)               # 'original_filing' / 'broker_research' / ...
print(assessment.is_fact_usable)     # True / False
print(assessment.confidence_weight)  # 0.0 ~ 1.0

# 2) 综合权重
weight = compute_tier_confidence_weight(
    assessment.tier,
    machine_readiness='high',
    is_stale=False,
    is_low_confidence=False,
)

# 3) 把 confidence 软降级
adjusted = apply_tier_to_confidence('high', assessment)

# 4) 渲染报告
print(render_citation_summary(assessment))
print(render_tier_table())
```

**lint 集成**：

```python
from tradingagents.dataflows.local_knowledge_lint import lint_local_knowledge

result = lint_local_knowledge('/path/to/knowledge')
for page in result.page_results:
    print(page.rel_path, page.source_quality_tier)
# 整库聚合
print(result.tier_counts)               # {'original_filing': N, 'broker_research': M, ...}
print(result.pages_weak_source)         # CIT-001 / CIT-003 命中页面
print(result.pages_opinion_as_fact)     # CIT-002 命中页面
```

---

## 7. 与现有契约的关系

| 模块 | 关系 |
|------|------|
| KB-001 `local_knowledge_audit` | 只读审计；KB-014 复用其 frontmatter 解析。 |
| KB-002 `local_knowledge_lint` | CIT- 规则追加在 HYF 之后；`PageLintResult` 新增 `source_quality_tier` / `citation_assessment` 字段。 |
| KB-003 `local_knowledge_provider` | `LocalKnowledgeMatch` 新增 `source_quality_tier` / `citation_confidence_weight`；`_build_match` 调用 `classify_source_quality_tier`。 |
| KB-004 `compute_local_knowledge_score` | `_page_score` 再乘 `citation_confidence_weight`；`broker_research` 软降权，`media/user_note/unknown` 保留展示但不加分。 |
| HY-001 半年报扩展 | KB-014 的 `source_type` 取值表与 HY-001 完全一致；HYF-007（全是观点）是 CIT-002（观点冒充事实）的前置检测。 |
| HY-005 事实反证 | `is_fact_usable=True` 的 tier 才能进入事实反证；`broker_research` / `media` 不进入。 |
| DECISION-001 最终动作语义 | tier **不**改变强动作门禁；Buy/Risk Level 仍由 DECISION-001 控制。 |

---

## 8. 验收要点

对应任务 KB-014 验收要求：

1. **fixture 覆盖五类来源**：`tests/test_kb014_citation_policy.py` 构造 `original_filing` / `official_notice` / `broker_research` / `media` / `user_note` / `unknown` 六类 fixture，覆盖验收"五类来源"要求。
2. **报告/摘要中能显示来源层级**：`render_local_knowledge_block` 在每条摘要末尾追加 `来源:<tier>`；`render_citation_summary` 输出 tier + 使用边界。
3. **弱来源不会被当作公告/财报事实**：
   - `apply_tier_to_confidence` 把 broker/media/user_note/unknown 的 confidence 软降级；
   - `_page_score` 把 broker 权重乘到命中分上，但 media/user_note/unknown 不加分；
   - `is_fact_usable=False` 的 tier 不进入 HY-005 事实反证。

---

## 9. 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| `kb-014-v1` | 2026-07-07 | 首版政策：定义 6 类 source_quality_tier、3 条 CIT- lint 规则、confidence/score 软调节、citation policy 使用边界。 |
