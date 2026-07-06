# Tree Work investment wiki 契约 lint 报告 — 2026-07-07

> [KB-002 / HY-001] local_knowledge_contract — 只读 lint，不修改知识库；低分页面只降低置信度，不阻塞 TA。契约见 ``docs/local_knowledge_contract.md``。

## 1. 概览

- knowledge_root: `/Users/maybee/Documents/knowledge`
- scanned_at: 2026-07-07 03:06:02
- contract_version: `kb-002-v1`
- investment_md_pages: **76**
- findings: **34** error / **142** warning / **21** info
- machine_readiness: high **36** (47%) / medium **22** / low **18**
- half_year_pages: **4** （缺报告期 4 / 缺事实 4 / 观点冒充事实 0）

## 2. Machine Readiness 分布

| readiness | 数量 | 占比 | 说明 |
|-----------|------|------|------|
| high | 36 | 47% | 0 error / ≤2 warning / 非待补充 / 非过期，TA 可直接消费 |
| medium | 22 | 29% | ≤2 error / ≤4 warning，TA 消费但需补字段 |
| low | 18 | 24% | 待补充/低置信/缺口过多，TA 标 LOW_CONFIDENCE/STALE，不进入候选加分 |

## 3. 规则命中分布

| rule_id | 命中次数 | 严重度 | 说明 |
|---------|----------|--------|------|
| `EVID-001` | 9 | info | evidence_level=C 低置信 |
| `FMR-001` | 19 | error | 必填 frontmatter 字段缺失/为空 |
| `FMR-002` | 15 | warning | 推荐机器字段缺失 |
| `HYF-001` | 4 | error | 财报/半年报页缺合法 financial_period |
| `HYF-003` | 4 | warning | 财报/半年报页缺合法 disclosure_date |
| `HYF-004` | 4 | warning | 财报/半年报页缺 source_type |
| `HYF-005` | 4 | warning | 财报/半年报页缺 financial_facts |
| `HYF-006` | 4 | warning | 财报/半年报页缺 risk_factors |
| `SEC-001` | 6 | error | 缺一句话总结/核心观点章节 |
| `SEC-002` | 10 | error | 缺风险提示章节 |
| `SEC-003` | 17 | warning | 缺原始资料/关联研报章节 |
| `SEC-004` | 47 | warning | 缺投资逻辑/核心观点章节 |
| `STALE-001` | 28 | warning | stale_risk=高 |
| `STALE-002` | 4 | warning | valid_until 已过期 |
| `SYM-001` | 1 | error | 公司/评分表页缺 symbols 字段 |
| `TBL-001` | 9 | error | 评分表缺标准表头列 |
| `TODO-001` | 12 | info | 显式标记待补充/低置信（符合契约） |

## 4. 缺口页面清单

### 缺一句话总结/核心观点 (SEC-001) (6)

- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/基金分析方法论.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/星球评分体系说明.md`
- `wiki/investment/板块轮动规律.md`

### 缺风险提示 (SEC-002) (10)

- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/基金分析方法论.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/星球评分体系说明.md`
- `wiki/investment/板块轮动规律.md`
- `wiki/investment/超级电容AI电源革命-待补充.md`

### 缺 sources/原始资料 (FMR-001/SEC-003) (4)

- `wiki/investment/基金分析方法论.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/板块轮动规律.md`

### 缺 symbols 字段 (FMR-002/SYM-001) (10)

- `wiki/investment/化工中期策略-反内卷谋双碳.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/星球评分体系说明.md`
- `wiki/investment/板块轮动规律.md`
- `wiki/investment/煤炭中期策略-能源安全价值重估.md`
- `wiki/investment/能源自主成为A股长期驱动力-瑞银策略.md`
- `wiki/investment/超级电容AI电源革命-待补充.md`

### 低置信 evidence_level=C (EVID-001) (9)

- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/基金分析方法论.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/板块轮动规律.md`
- `wiki/investment/超级电容AI电源革命-待补充.md`

### 过期/高 stale_risk (STALE-001/002) (29)

- `wiki/investment/AI算力基础设施-公司评分表.md`
- `wiki/investment/AI算力网络-公司评分表.md`
- `wiki/investment/CPO出货提速与谷歌IO-通信周报要点.md`
- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/PCB化学品与新材料-公司评分表.md`
- `wiki/investment/人形机器人产业周报-量产博弈阶段.md`
- `wiki/investment/半导体产业链-公司评分表.md`
- `wiki/investment/半导体封测-公司评分表.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/台湾周报-TAIEX创新高-科技全面走强.md`
- `wiki/investment/基金分析方法论.md`
- `wiki/investment/多板块-公司评分表.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/宝武镁业002182-全镁产业链龙头.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/新能源产业链-公司评分表.md`
- `wiki/investment/杂项标的-公司评分表.md`
- `wiki/investment/板块轮动规律.md`
- ... 其余 9 项见 ``to_dict()`` 输出

### 显式待补充 (TODO-001) (12)

- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`
- `wiki/investment/宝武镁业002182-全镁产业链龙头.md`
- `wiki/investment/星源卓镁301398-业绩拐点有望出现.md`
- `wiki/investment/格林美002340-业绩稳健增长.md`
- `wiki/investment/电新行业2025年报及2026Q1综述-盈利显著回暖.md`
- `wiki/investment/研报核心看点汇总表.md`
- `wiki/investment/脑机接口-医疗公司清单.md`
- `wiki/investment/超级电容AI电源革命-待补充.md`
- `wiki/investment/锂电池产业链投资分析-2026Q1.md`

### low readiness（需回 Tree Work 补字段） (18)

- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/华源证券-存储测试设备布局完善.md`
- `wiki/investment/基金分析方法论.md`
- `wiki/investment/太平洋-业绩增长看好8600放量.md`
- `wiki/investment/宏观周期.md`
- `wiki/investment/宝武镁业002182-全镁产业链龙头.md`
- `wiki/investment/投资方法论.md`
- `wiki/investment/星源卓镁301398-业绩拐点有望出现.md`
- `wiki/investment/星球评分体系说明.md`
- `wiki/investment/板块轮动规律.md`
- `wiki/investment/格林美002340-业绩稳健增长.md`
- `wiki/investment/电新行业2025年报及2026Q1综述-盈利显著回暖.md`
- `wiki/investment/研报核心看点汇总表.md`
- `wiki/investment/脑机接口-医疗公司清单.md`
- `wiki/investment/超级电容AI电源革命-待补充.md`
- `wiki/investment/锂电池产业链投资分析-2026Q1.md`
- `wiki/investment/阿里腾讯财报-AI硬底气.md`

### 财报/半年报页缺 financial_period (HYF-001) (4)

- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/PCB产业链2025及26Q1业绩综述-下游需求旺盛.md`
- `wiki/investment/阿里腾讯财报-AI硬底气.md`

### 财报/半年报页缺 financial_facts (HYF-005) (4)

- `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
- `wiki/investment/Dell-FQ127-待补充.md`
- `wiki/investment/PCB产业链2025及26Q1业绩综述-下游需求旺盛.md`
- `wiki/investment/阿里腾讯财报-AI硬底气.md`

## 5. Top 修复优先级（按影响面排序）

1. **`FMR-001`** — 影响 19 页 — 补 ``related: [[investment/xxx]]`` 或空数组 ``related: []``，便于 TA 关联同主题页面。
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
   - `wiki/investment/Powersports出海-春风动力涛涛车业隆鑫通用.md`
   - `wiki/investment/半导体AI芯片-公司评分表.md`
   - `wiki/investment/华源证券-存储测试设备布局完善.md`
2. **`SEC-002`** — 影响 10 页 — 补 ``## 风险提示``，TA 在 raw_evidence 中标记 risk 段，避免只看利好。
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
   - `wiki/investment/华源证券-存储测试设备布局完善.md`
   - `wiki/investment/基金分析方法论.md`
   - `wiki/investment/太平洋-业绩增长看好8600放量.md`
3. **`TBL-001`** — 影响 9 页 — 评分表必须保留表头：``| 公司 | 代码 | 核心业务 | 板块 | 利好度 | 共识度 | 预计启动 | 期待周期 |``，TA 据此解析候选评分。
   - `wiki/investment/半导体AI芯片-公司评分表.md`
   - `wiki/investment/半导体产业链-公司评分表.md`
   - `wiki/investment/多板块-公司评分表.md`
   - `wiki/investment/新能源产业链-公司评分表.md`
   - `wiki/investment/星球评分体系说明.md`
4. **`SEC-001`** — 影响 6 页 — 补 ``## 一句话总结``（1-2 句），TA 仅取该段作为背景摘要。
   - `wiki/investment/华源证券-存储测试设备布局完善.md`
   - `wiki/investment/基金分析方法论.md`
   - `wiki/investment/宏观周期.md`
   - `wiki/investment/投资方法论.md`
   - `wiki/investment/星球评分体系说明.md`
5. **`HYF-001`** — 影响 4 页 — 补 ``financial_period: 2025H1`` / ``FY26Q1`` / ``2025中报`` / ``2025年报`` 之一；TA 据此判断是否最新、是否过期。
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
   - `wiki/investment/PCB产业链2025及26Q1业绩综述-下游需求旺盛.md`
   - `wiki/investment/阿里腾讯财报-AI硬底气.md`
6. **`SYM-001`** — 影响 1 页 — 补 ``symbols: ["603296.SH 华勤技术"]``，否则 TA 无法按 symbol 命中本页。
   - `wiki/investment/星球评分体系说明.md`
7. **`SEC-004`** — 影响 47 页 — 补 ``## 投资逻辑``，列 3-5 条核心驱动，TA 作为观点源。
   - `wiki/investment/AI算力基础设施-公司评分表.md`
   - `wiki/investment/AI算力网络-公司评分表.md`
   - `wiki/investment/CPO出货提速与谷歌IO-通信周报要点.md`
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
8. **`STALE-001`** — 影响 28 页 — TA 命中后标 STALE 并提示 ``needs_tree_work_research``，不直接用于实时结论。
   - `wiki/investment/AI算力基础设施-公司评分表.md`
   - `wiki/investment/AI算力网络-公司评分表.md`
   - `wiki/investment/CPO出货提速与谷歌IO-通信周报要点.md`
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
9. **`SEC-003`** — 影响 17 页 — 补 ``## 原始资料`` 或在 frontmatter ``sources`` 写非空列表，TA 据此溯源。
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
   - `wiki/investment/半导体AI芯片-公司评分表.md`
   - `wiki/investment/华源证券-存储测试设备布局完善.md`
   - `wiki/investment/基金分析方法论.md`
10. **`FMR-002`** — 影响 15 页 — 补 ``symbols: ["603296.SH 华勤技术"]``（代码+空格+简称）；缺失则不进入 KB-003 symbol 命中索引。
   - `wiki/investment/化工中期策略-反内卷谋双碳.md`
   - `wiki/investment/华源证券-存储测试设备布局完善.md`
   - `wiki/investment/基金分析方法论.md`
   - `wiki/investment/太平洋-业绩增长看好8600放量.md`
   - `wiki/investment/宏观周期.md`
11. **`HYF-003`** — 影响 4 页 — 补 ``disclosure_date: 2026-08-30``（交易所披露日）；缺失则 TA 标 ``disclosure_unknown``。
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
   - `wiki/investment/PCB产业链2025及26Q1业绩综述-下游需求旺盛.md`
   - `wiki/investment/阿里腾讯财报-AI硬底气.md`
12. **`HYF-004`** — 影响 4 页 — 补 ``source_type: [exchange_filing, fact_table, management_commentary]``，区分事实/公司口径/券商观点；详见 ``docs/local_knowledge_contract.md`` §10.1。
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
   - `wiki/investment/PCB产业链2025及26Q1业绩综述-下游需求旺盛.md`
   - `wiki/investment/阿里腾讯财报-AI硬底气.md`
13. **`HYF-005`** — 影响 4 页 — 补 ``financial_facts: [营收 150亿 (+30%), 毛利率 25%]``，HY-003 据此建事实表、HY-005 据此反证旧研报观点。
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
   - `wiki/investment/PCB产业链2025及26Q1业绩综述-下游需求旺盛.md`
   - `wiki/investment/阿里腾讯财报-AI硬底气.md`
14. **`HYF-006`** — 影响 4 页 — 补 ``risk_factors: [客户集中度, 汇率]``；与正文 ``## 风险提示`` 互补，TA 在 HY-005 反证时按字段抽取。
   - `wiki/investment/Dell-FQ127-Bernstein财报分析.md`
   - `wiki/investment/Dell-FQ127-待补充.md`
   - `wiki/investment/PCB产业链2025及26Q1业绩综述-下游需求旺盛.md`
   - `wiki/investment/阿里腾讯财报-AI硬底气.md`
15. **`STALE-002`** — 影响 4 页 — 更新 ``valid_until`` 或回 Tree Work 重新消化；过期页 TA 标 STALE。
   - `wiki/investment/CPO出货提速与谷歌IO-通信周报要点.md`
   - `wiki/investment/台湾周报-TAIEX创新高-科技全面走强.md`
   - `wiki/investment/红宝书-市场策略与个股分析汇总.md`
   - `wiki/investment/锂电5月洞察-业绩回暖排产上行.md`

---

_本报告由 ``scripts/lint_local_knowledge.py`` 只读生成；对应模块 ``tradingagents.dataflows.local_knowledge_lint``；契约文档 ``docs/local_knowledge_contract.md``。_
