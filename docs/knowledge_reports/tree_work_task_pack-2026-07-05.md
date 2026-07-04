# Tree Work 研报补录任务包 — 2026-07-05

> [KB-012] tree_work_task_pack — 只读合并 KB-002 lint / KB-005 backlog / KB-007/009 关注度信号，输出 Tree Work 补录任务包；**不修改知识库、不复制研报原文、不输出交易建议**。

## 1. 概览

- knowledge_root: `/Users/maybee/Documents/knowledge`
- generated_at: 2026-07-05 03:59:44
- as_of_date: 2026-07-05
- contract_version: `kb-012-v1`
- 任务总数: **362**

## 2. 分组统计

| 分组 | 数量 | 说明 |
|------|------|------|
| `missing_symbol` | 20 | 缺 symbol/name（TA 无法按 symbol 命中） |
| `missing_thesis` | 49 | 缺一句话总结/投资逻辑（TA 无法取摘要） |
| `missing_risks` | 10 | 缺风险提示（TA 无法标 risk） |
| `missing_sources` | 23 | 缺 sources/原始资料链接（TA 无法溯源） |
| `needs_review_stale` | 34 | 过期/低置信需回 Tree Work 复核 |
| `hot_but_thin` | 88 | 研究关注度高但证据薄（过热 / 主题拥挤） |
| `ingest_new` | 138 | inbox/raw 未消化（需新建 wiki 页） |

## 3. 上游信号摘要

- **KB-002 lint**：76 页 / error=30 warning=126 info=21 / high=38 medium=21 low=17
- **KB-005 backlog**：raw=164 (referenced 34) / investment_pages=76 / total_backlog=171
- **KB-007 attention**：symbols=228 (A_SHARE 177)
  - Top 5：`NVDA.US`(11.0), `688072.SH`(8.0), `688019.SH`(7.2), `301200.SZ`(7.0), `688521.SH`(7.0)

## 4. 补录任务（按分组）

### 缺 symbol/name（TA 无法按 symbol 命中）（20）

| 优先级 | 路径 | 建议动作 | 原因 | 来源信号 |
|--------|------|----------|------|----------|
| high | `wiki/investment/Dell-FQ127-Bernstein财报分析.md` | `fill_fields` | 占位/待补充页，补内容后转正；缺：风险 | `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/Dell-FQ127-待补充.md` | `fill_fields` | 占位/待补充页，补内容后转正；缺：风险 | `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/太平洋-业绩增长看好8600放量.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；占位/待补充页，补内容后转正；缺：风险 | `KB-002:FMR-002`, `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/宝武镁业002182-全镁产业链龙头.md` | `fill_fields` | 占位/待补充页，补内容后转正 | `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/星源卓镁301398-业绩拐点有望出现.md` | `fill_fields` | 占位/待补充页，补内容后转正 | `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/星球评分体系说明.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；推荐机器字段 ``industry_chain_roles`` 缺失；``score_table`` 类型页面缺 ``symbols`` 字段；缺 machine symbols 字段，TA 无法按 symbol 命中 | `KB-002:FMR-002`, `KB-002:SYM-001`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/格林美002340-业绩稳健增长.md` | `fill_fields` | 占位/待补充页，补内容后转正 | `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/电新行业2025年报及2026Q1综述-盈利显著回暖.md` | `fill_fields` | 占位/待补充页，补内容后转正 | `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/研报核心看点汇总表.md` | `fill_fields` | 占位/待补充页，补内容后转正 | `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/脑机接口-医疗公司清单.md` | `fill_fields` | 占位/待补充页，补内容后转正 | `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/超级电容AI电源革命-待补充.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；占位/待补充页，补内容后转正；缺：风险 | `KB-002:FMR-002`, `KB-005:wiki_to_be_supplemented` |
| high | `wiki/investment/锂电池产业链投资分析-2026Q1.md` | `fill_fields` | 占位/待补充页，补内容后转正 | `KB-005:wiki_to_be_supplemented` |
| medium | `wiki/investment/化工中期策略-反内卷谋双碳.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；缺 machine symbols 字段，TA 无法按 symbol 命中 | `KB-002:FMR-002`, `KB-005:wiki_field_gap` |
| medium | `wiki/investment/华源证券-存储测试设备布局完善.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失 | `KB-002:FMR-002` |
| medium | `wiki/investment/基金分析方法论.md` | `fill_fields` | 推荐机器字段 ``industry_chain_roles`` 缺失 | `KB-002:FMR-002` |
| medium | `wiki/investment/宏观周期.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；推荐机器字段 ``industry_chain_roles`` 缺失；缺 machine symbols 字段，TA 无法按 symbol 命中 | `KB-002:FMR-002`, `KB-005:wiki_field_gap` |
| medium | `wiki/investment/投资方法论.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；推荐机器字段 ``industry_chain_roles`` 缺失；缺 machine symbols 字段，TA 无法按 symbol 命中 | `KB-002:FMR-002`, `KB-005:wiki_field_gap` |
| medium | `wiki/investment/板块轮动规律.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；推荐机器字段 ``industry_chain_roles`` 缺失；缺 machine symbols 字段，TA 无法按 symbol 命中 | `KB-002:FMR-002`, `KB-005:wiki_field_gap` |
| medium | `wiki/investment/煤炭中期策略-能源安全价值重估.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；缺 machine symbols 字段，TA 无法按 symbol 命中 | `KB-002:FMR-002`, `KB-005:wiki_field_gap` |
| medium | `wiki/investment/能源自主成为A股长期驱动力-瑞银策略.md` | `fill_fields` | 推荐机器字段 ``symbols`` 缺失；缺 machine symbols 字段，TA 无法按 symbol 命中 | `KB-002:FMR-002`, `KB-005:wiki_field_gap` |

### 缺一句话总结/投资逻辑（TA 无法取摘要）（49）

| 优先级 | 路径 | 建议动作 | 原因 | 来源信号 |
|--------|------|----------|------|----------|
| high | `wiki/investment/半导体AI芯片-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节；评分表缺标准表头列：共识度 | `KB-002:SEC-004`, `KB-002:TBL-001` |
| high | `wiki/investment/半导体产业链-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节；评分表缺标准表头列：共识度 | `KB-002:SEC-004`, `KB-002:TBL-001` |
| high | `wiki/investment/华源证券-存储测试设备布局完善.md` | `fill_summary` | 缺“一句话总结/核心观点/核心结论/投资逻辑”章节；缺“投资逻辑/核心观点”章节 | `KB-002:SEC-001`, `KB-002:SEC-004` |
| high | `wiki/investment/基金分析方法论.md` | `fill_summary` | 缺“一句话总结/核心观点/核心结论/投资逻辑”章节；缺“投资逻辑/核心观点”章节；缺 ## 一句话总结 / 核心观点 章节 | `KB-002:SEC-001`, `KB-002:SEC-004`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/多板块-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节；评分表缺标准表头列：共识度 | `KB-002:SEC-004`, `KB-002:TBL-001` |
| high | `wiki/investment/宏观周期.md` | `fill_summary` | 缺“一句话总结/核心观点/核心结论/投资逻辑”章节；缺“投资逻辑/核心观点”章节；缺 ## 一句话总结 / 核心观点 章节 | `KB-002:SEC-001`, `KB-002:SEC-004`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/投资方法论.md` | `fill_summary` | 缺“一句话总结/核心观点/核心结论/投资逻辑”章节；缺“投资逻辑/核心观点”章节；缺 ## 一句话总结 / 核心观点 章节 | `KB-002:SEC-001`, `KB-002:SEC-004`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/新能源产业链-公司评分表.md` | `fill_summary` | 评分表缺标准表头列：共识度 | `KB-002:TBL-001` |
| high | `wiki/investment/星球评分体系说明.md` | `fill_summary` | 缺“一句话总结/核心观点/核心结论/投资逻辑”章节；缺“投资逻辑/核心观点”章节；评分表缺标准表头列：公司, 代码, 核心业务, 板块, 利好度, 共识度, 预计启动, 期待周期；缺 ## 一句话总结 / 核心观点 章节 | `KB-002:SEC-001`, `KB-002:SEC-004`, `KB-002:TBL-001`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/板块轮动规律.md` | `fill_summary` | 缺“一句话总结/核心观点/核心结论/投资逻辑”章节；缺“投资逻辑/核心观点”章节；缺 ## 一句话总结 / 核心观点 章节 | `KB-002:SEC-001`, `KB-002:SEC-004`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/液冷数据中心-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节；评分表缺标准表头列：共识度 | `KB-002:SEC-004`, `KB-002:TBL-001` |
| high | `wiki/investment/电子化学品-公司评分表.md` | `fill_summary` | 评分表缺标准表头列：共识度 | `KB-002:TBL-001` |
| high | `wiki/investment/电子材料-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节；评分表缺标准表头列：共识度 | `KB-002:SEC-004`, `KB-002:TBL-001` |
| high | `wiki/investment/电池回收-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节；评分表缺标准表头列：共识度 | `KB-002:SEC-004`, `KB-002:TBL-001` |
| medium | `wiki/investment/AI算力基础设施-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/AI算力网络-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/CPO出货提速与谷歌IO-通信周报要点.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/Dell-FQ127-Bernstein财报分析.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/Dell-FQ127-待补充.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/PCB化学品与新材料-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/Powersports出海-春风动力涛涛车业隆鑫通用.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/Rubin机柜BOM分析-VR200元件含量暴增.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/半导体封测-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/华为韬定律-后摩尔时代芯片新规则.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/台湾周报-TAIEX创新高-科技全面走强.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/太平洋-业绩增长看好8600放量.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/宝武镁业002182-全镁产业链龙头.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/工程机械出海逻辑继续演绎-三一重工徐工中联柳工.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/星源卓镁301398-业绩拐点有望出现.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/杂项标的-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/村田MLCC-AI服务器需求85-90%增长.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/格林美002340-业绩稳健增长.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/特斯拉人形机器人-量产临近行业爆发.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/电子特气与钨-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/研报核心看点汇总表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/稀土板块-公司评分表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/算力资源与市值比-速查表.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/算电时代三大预期差-亟待重估.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/精智达301526-存储测试设备布局完善.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/红宝书-市场策略与个股分析汇总.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/能源自主成为A股长期驱动力-瑞银策略.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/脑机接口-医疗公司清单.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/蘅东光920045-无源光器件收入增长61.5%.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/超级电容AI电源革命-待补充.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/锂电5月洞察-业绩回暖排产上行.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/锂电中期策略-动储共振周期上行.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/锂电池产业链投资分析-2026Q1.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/锂电铜箔与PCB铜箔-周期拐点与供需紧张.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |
| medium | `wiki/investment/阿里腾讯财报-AI硬底气.md` | `fill_summary` | 缺“投资逻辑/核心观点”章节 | `KB-002:SEC-004` |

### 缺风险提示（TA 无法标 risk）（10）

| 优先级 | 路径 | 建议动作 | 原因 | 来源信号 |
|--------|------|----------|------|----------|
| high | `wiki/investment/Dell-FQ127-Bernstein财报分析.md` | `fill_risks` | 缺“风险提示”章节 | `KB-002:SEC-002` |
| high | `wiki/investment/Dell-FQ127-待补充.md` | `fill_risks` | 缺“风险提示”章节 | `KB-002:SEC-002` |
| high | `wiki/investment/华源证券-存储测试设备布局完善.md` | `fill_risks` | 缺“风险提示”章节 | `KB-002:SEC-002` |
| high | `wiki/investment/基金分析方法论.md` | `fill_risks` | 缺“风险提示”章节；缺 ## 风险提示 章节 | `KB-002:SEC-002`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/太平洋-业绩增长看好8600放量.md` | `fill_risks` | 缺“风险提示”章节 | `KB-002:SEC-002` |
| high | `wiki/investment/宏观周期.md` | `fill_risks` | 缺“风险提示”章节；缺 ## 风险提示 章节 | `KB-002:SEC-002`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/投资方法论.md` | `fill_risks` | 缺“风险提示”章节；缺 ## 风险提示 章节 | `KB-002:SEC-002`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/星球评分体系说明.md` | `fill_risks` | 缺“风险提示”章节；缺 ## 风险提示 章节 | `KB-002:SEC-002`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/板块轮动规律.md` | `fill_risks` | 缺“风险提示”章节；缺 ## 风险提示 章节 | `KB-002:SEC-002`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/超级电容AI电源革命-待补充.md` | `fill_risks` | 缺“风险提示”章节 | `KB-002:SEC-002` |

### 缺 sources/原始资料链接（TA 无法溯源）（23）

| 优先级 | 路径 | 建议动作 | 原因 | 来源信号 |
|--------|------|----------|------|----------|
| high | `wiki/investment/基金分析方法论.md` | `fill_source_links` | 必填 frontmatter 字段 ``sources`` 缺失或为空；缺“原始资料/关联研报”章节；frontmatter sources 字段为空，补 raw 反向链接 | `KB-002:FMR-001`, `KB-002:SEC-003`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/宏观周期.md` | `fill_source_links` | 必填 frontmatter 字段 ``sources`` 缺失或为空；缺“原始资料/关联研报”章节；frontmatter sources 字段为空，补 raw 反向链接 | `KB-002:FMR-001`, `KB-002:SEC-003`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/投资方法论.md` | `fill_source_links` | 必填 frontmatter 字段 ``sources`` 缺失或为空；缺“原始资料/关联研报”章节；frontmatter sources 字段为空，补 raw 反向链接 | `KB-002:FMR-001`, `KB-002:SEC-003`, `KB-005:wiki_field_gap` |
| high | `wiki/investment/板块轮动规律.md` | `fill_source_links` | 必填 frontmatter 字段 ``sources`` 缺失或为空；缺“原始资料/关联研报”章节；frontmatter sources 字段为空，补 raw 反向链接 | `KB-002:FMR-001`, `KB-002:SEC-003`, `KB-005:wiki_field_gap` |
| medium | `wiki/investment/Dell-FQ127-Bernstein财报分析.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/Dell-FQ127-待补充.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/Powersports出海-春风动力涛涛车业隆鑫通用.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填） | `KB-002:FMR-001` |
| medium | `wiki/investment/半导体AI芯片-公司评分表.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/华源证券-存储测试设备布局完善.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/多板块-公司评分表.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填） | `KB-002:FMR-001` |
| medium | `wiki/investment/太平洋-业绩增长看好8600放量.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/工程机械出海逻辑继续演绎-三一重工徐工中联柳工.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填） | `KB-002:FMR-001` |
| medium | `wiki/investment/星球评分体系说明.md` | `fill_source_links` | 缺“原始资料/关联研报”章节 | `KB-002:SEC-003` |
| medium | `wiki/investment/液冷数据中心-公司评分表.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/滨化股份601678-氯碱龙头北鲲计划.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填） | `KB-002:FMR-001` |
| medium | `wiki/investment/电子化学品-公司评分表.md` | `fill_source_links` | 缺“原始资料/关联研报”章节 | `KB-002:SEC-003` |
| medium | `wiki/investment/研报核心看点汇总表.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/算力租赁行业分析.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填） | `KB-002:FMR-001` |
| medium | `wiki/investment/聚杰微纤300819-电子布打开成长空间.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填） | `KB-002:FMR-001` |
| medium | `wiki/investment/脑机接口-医疗公司清单.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/超级电容AI电源革命-下一个涨价品种.md` | `fill_source_links` | 缺“原始资料/关联研报”章节 | `KB-002:SEC-003` |
| medium | `wiki/investment/超级电容AI电源革命-待补充.md` | `fill_source_links` | 基础 frontmatter 字段 ``related`` 缺失（KB-002 契约列为必填）；缺“原始资料/关联研报”章节 | `KB-002:FMR-001`, `KB-002:SEC-003` |
| medium | `wiki/investment/锂电池产业链投资分析-2026Q1.md` | `fill_source_links` | 缺“原始资料/关联研报”章节 | `KB-002:SEC-003` |

### 过期/低置信需回 Tree Work 复核（34）

| 优先级 | 路径 | 建议动作 | 原因 | 来源信号 |
|--------|------|----------|------|----------|
| medium | `wiki/investment/AI算力基础设施-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`603296.SH` 华勤技术 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002837.SZ` 英维克 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`000977.SZ` 浪潮信息 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/AI算力网络-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`688041.SH` 海光信息 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`000063.SZ` 中兴通讯 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300308.SZ` 中际旭创 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002281.SZ` 光迅科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002396.SZ` 星网锐捷 研报关注度页面过期/高 stale_risk， | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/CPO出货提速与谷歌IO-通信周报要点.md` | `review` | ``stale_risk=高``，知识可能过时；``valid_until=2026-06-30`` 已过期；`688017.SH` 绿的谐波 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300308.SZ` 中际旭创 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300394.SZ` 天孚通信 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300502.SZ` 新易盛 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688599.SH | `KB-002:STALE-001`, `KB-002:STALE-002`, `KB-007:stale_mention` |
| medium | `wiki/investment/Dell-FQ127-Bernstein财报分析.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条）；``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信 | `KB-002:TODO-001`, `KB-002:STALE-001`, `KB-002:EVID-001` |
| medium | `wiki/investment/Dell-FQ127-待补充.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条）；``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信 | `KB-002:TODO-001`, `KB-002:STALE-001`, `KB-002:EVID-001` |
| medium | `wiki/investment/PCB化学品与新材料-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`002741.SZ` 光华科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`603281.SH` 江瀚新材 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`603938.SH` 三孚股份 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688359.SH` 三孚新科 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688603.SH` 天承科技 研报关注度页面过期/高 stale_risk， | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/人形机器人产业周报-量产博弈阶段.md` | `review` | ``stale_risk=高``，知识可能过时；`002850.SZ` 科达利 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688017.SH` 绿的谐波 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`003021.SZ` 兆威机电 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300115.SZ` 长盈精密 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300953.SZ` 震裕科技 研报关注度页面过期/高 stale_risk，需 | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/半导体产业链-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`688256.SH` 寒武纪 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688041.SH` 海光信息 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002371.SZ` 北方华创 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688012.SH` 中微公司 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`301308.SZ` 江波龙 研报关注度页面过期/高 stale_risk，需回 | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/半导体封测-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`688372.SH` 伟测科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`600584.SH` 长电科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688200.SH` 华峰测控 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/华源证券-存储测试设备布局完善.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条）；``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信；废弃页：已由更完整的精智达301526-存储测试设备布局完善.md替代；建议归档或合并到替代页 | `KB-002:TODO-001`, `KB-002:STALE-001`, `KB-002:EVID-001`, `KB-005:wiki_deprecated` |
| medium | `wiki/investment/台湾周报-TAIEX创新高-科技全面走强.md` | `review` | ``stale_risk=高``，知识可能过时；``valid_until=2026-06-15`` 已过期 | `KB-002:STALE-001`, `KB-002:STALE-002` |
| medium | `wiki/investment/基金分析方法论.md` | `review` | ``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信 | `KB-002:STALE-001`, `KB-002:EVID-001` |
| medium | `wiki/investment/多板块-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`301631.SZ` 壹连科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/太平洋-业绩增长看好8600放量.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条）；``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信 | `KB-002:TODO-001`, `KB-002:STALE-001`, `KB-002:EVID-001` |
| medium | `wiki/investment/宏观周期.md` | `review` | ``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信 | `KB-002:STALE-001`, `KB-002:EVID-001` |
| medium | `wiki/investment/宝武镁业002182-全镁产业链龙头.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条）；``stale_risk=高``，知识可能过时；`002182.SZ` 宝武镁业 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:TODO-001`, `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/投资方法论.md` | `review` | ``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信 | `KB-002:STALE-001`, `KB-002:EVID-001` |
| medium | `wiki/investment/新能源产业链-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`300014.SZ` 亿纬锂能 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300750.SZ` 宁德时代 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`603799.SH` 华友钴业 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002487.SZ` 大金重工 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300274.SZ` 阳光电源 研报关注度页面过期/高 stale_risk， | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/杂项标的-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`301631.SZ` 壹连科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/板块轮动规律.md` | `review` | ``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信 | `KB-002:STALE-001`, `KB-002:EVID-001` |
| medium | `wiki/investment/电子材料-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`688019.SH` 安集科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300666.SZ` 江丰电子 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300054.SZ` 鼎龙股份 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002409.SZ` 雅克科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002643.SZ` 万润股份 研报关注度页面过期/高 stale_risk， | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/电子特气与钨-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`600549.SH` 厦门钨业 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002378.SZ` 章源钨业 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300346.SZ` 南大光电 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`600378.SH` 昊华科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688146.SH` 中船特气 研报关注度页面过期/高 stale_risk， | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/电池回收-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`600549.SH` 厦门钨业 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002340.SZ` 格林美 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`603799.SH` 华友钴业 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/稀土板块-公司评分表.md` | `review` | ``stale_risk=高``，知识可能过时；`000831.SZ` 中国稀土 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`600111.SH` 北方稀土 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`600259.SH` 中稀有色 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`600392.SH` 盛和资源 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/算力资源与市值比-速查表.md` | `review` | ``stale_risk=高``，知识可能过时；`300442.SZ` 润泽科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002229.SZ` 鸿博股份 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002261.SZ` 拓维信息 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300017.SZ` 网宿科技 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`603220.SH` 中贝通信 研报关注度页面过期/高 stale_risk， | `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/红宝书-市场策略与个股分析汇总.md` | `review` | ``stale_risk=高``，知识可能过时；``valid_until=2026-06-15`` 已过期；`688126.SH` 沪硅产业 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300608.SZ` 思特奇 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300616.SZ` 尚品宅配 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300632.SZ` 光莆股份 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300634.SZ | `KB-002:STALE-001`, `KB-002:STALE-002`, `KB-007:stale_mention` |
| medium | `wiki/investment/脑机接口-医疗公司清单.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条）；``stale_risk=高``，知识可能过时；`301293.SZ` 三博脑科 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`301363.SZ` 美好医疗 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:TODO-001`, `KB-002:STALE-001`, `KB-007:stale_mention` |
| medium | `wiki/investment/超级电容AI电源革命-待补充.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条）；``stale_risk=高``，知识可能过时；``evidence_level=C`` 为低置信 | `KB-002:TODO-001`, `KB-002:STALE-001`, `KB-002:EVID-001` |
| medium | `wiki/investment/锂电5月洞察-业绩回暖排产上行.md` | `review` | ``valid_until=2026-06-30`` 已过期；`300014.SZ` 亿纬锂能 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`300750.SZ` 宁德时代 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`002850.SZ` 科达利 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证；`688733.SH` 厦钨新能 研报关注度页面过期/高 stale_risk，需回 Tree Work 复核或补证 | `KB-002:STALE-002`, `KB-007:stale_mention` |
| low | `wiki/investment/星源卓镁301398-业绩拐点有望出现.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条） | `KB-002:TODO-001` |
| low | `wiki/investment/格林美002340-业绩稳健增长.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条） | `KB-002:TODO-001` |
| low | `wiki/investment/电新行业2025年报及2026Q1综述-盈利显著回暖.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条） | `KB-002:TODO-001` |
| low | `wiki/investment/研报核心看点汇总表.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条） | `KB-002:TODO-001` |
| low | `wiki/investment/锂电池产业链投资分析-2026Q1.md` | `review` | 页面显式标记“待补充/低置信”（符合契约第 5 条） | `KB-002:TODO-001` |

### 研究关注度高但证据薄（过热 / 主题拥挤）（88）

| 优先级 | 路径 | 建议动作 | 原因 | 来源信号 |
|--------|------|----------|------|----------|
| medium | `symbol:000063.SZ` | `review` | `000063.SZ` 中兴通讯 研究关注度高但证据薄，score=3.20；mention=2；stale_ratio=0.50；theme_crowding=12 | `KB-007:hot_but_thin` |
| medium | `symbol:000157.SZ` | `review` | `000157.SZ` 中联重科 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:000425.SZ` | `review` | `000425.SZ` 徐工机械 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:000528.SZ` | `review` | `000528.SZ` 柳工 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:000612.SZ` | `review` | `000612.SZ` 神火股份 研究关注度高但证据薄，score=4.00；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:000938.SZ` | `review` | `000938.SZ` 紫光股份 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:000988.SZ` | `review` | `000988.SZ` 华工科技 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:002008.SZ` | `review` | `002008.SZ` 大族激光 研究关注度高但证据薄，score=2.25；mention=2；stale_ratio=0.00；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:002074.SZ` | `review` | `002074.SZ` 国轩高科 研究关注度高但证据薄，score=4.00；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:002080.SZ` | `review` | `002080.SZ` 中材科技 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:002371.SZ` | `review` | `002371.SZ` 北方华创 研究关注度高但证据薄，score=2.45；mention=2；stale_ratio=0.50；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:002409.SZ` | `review` | `002409.SZ` 雅克科技 研究关注度高但证据薄，score=2.95；mention=2；stale_ratio=0.50；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:002428.SZ` | `review` | `002428.SZ` 云南锗业 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:002463.SZ` | `review` | `002463.SZ` 沪电股份 研究关注度高但证据薄，score=3.50；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:002594.SZ` | `review` | `002594.SZ` 比亚迪 研究关注度高但证据薄，score=2.17；mention=3；stale_ratio=0.00；theme_crowding=16 | `KB-007:hot_but_thin` |
| medium | `symbol:002634.SZ` | `review` | `002634.SZ` 方大特钢 研究关注度高但证据薄，score=4.00；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:002643.SZ` | `review` | `002643.SZ` 万润股份 研究关注度高但证据薄，score=2.95；mention=2；stale_ratio=0.50；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:002709.SZ` | `review` | `002709.SZ` 天赐材料 研究关注度高但证据薄，score=2.75；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:002738.SZ` | `review` | `002738.SZ` 中矿资源 研究关注度高但证据薄，score=4.00；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:002741.SZ` | `review` | `002741.SZ` 光华科技 研究关注度高但证据薄，score=2.45；mention=2；stale_ratio=0.50；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:002756.SZ` | `review` | `002756.SZ` 永兴材料 研究关注度高但证据薄，score=4.00；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:002837.SZ` | `review` | `002837.SZ` 英维克 研究关注度高但证据薄，score=1.70；mention=2；stale_ratio=0.50；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:002916.SZ` | `review` | `002916.SZ` 深南电路 研究关注度高但证据薄，score=3.50；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:002938.SZ` | `review` | `002938.SZ` 鹏鼎控股 研究关注度高但证据薄，score=3.50；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:002975.SZ` | `review` | `002975.SZ` 博杰股份 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:300014.SZ` | `review` | `300014.SZ` 亿纬锂能 研究关注度高但证据薄，score=2.07；mention=3；stale_ratio=0.67；theme_crowding=13 | `KB-007:hot_but_thin` |
| medium | `symbol:300054.SZ` | `review` | `300054.SZ` 鼎龙股份 研究关注度高但证据薄，score=5.03；mention=3；stale_ratio=0.33；theme_crowding=12 | `KB-007:hot_but_thin` |
| medium | `symbol:300121.SZ` | `review` | `300121.SZ` 阳谷华泰 研究关注度高但证据薄，score=2.95；mention=2；stale_ratio=0.50；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:300383.SZ` | `review` | `300383.SZ` 光环新网 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:300442.SZ` | `review` | `300442.SZ` 润泽科技 研究关注度高但证据薄，score=2.70；mention=2；stale_ratio=0.50；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:300476.SZ` | `review` | `300476.SZ` 胜宏科技 研究关注度高但证据薄，score=3.50；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:300604.SZ` | `review` | `300604.SZ` 长川科技 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:300666.SZ` | `review` | `300666.SZ` 江丰电子 研究关注度高但证据薄，score=6.70；mention=3；stale_ratio=0.33；theme_crowding=16 | `KB-007:hot_but_thin` |
| medium | `symbol:300738.SZ` | `review` | `300738.SZ` 奥飞数据 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:300819.SZ` | `review` | `300819.SZ` 聚杰微纤 研究关注度高但证据薄，score=2.25；mention=2；stale_ratio=0.00；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:301200.SZ` | `review` | `301200.SZ` 大族数控 研究关注度高但证据薄，score=7.00；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:301308.SZ` | `review` | `301308.SZ` 江波龙 研究关注度高但证据薄，score=2.20；mention=2；stale_ratio=0.50；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:301345.SZ` | `review` | `301345.SZ` 涛涛车业 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:301377.SZ` | `review` | `301377.SZ` 鼎泰高科 研究关注度高但证据薄，score=3.50；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:301526.SZ` | `review` | `301526.SZ` 精智达 研究关注度高但证据薄，score=4.17；mention=3；stale_ratio=0.00；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:600019.SH` | `review` | `600019.SH` 宝钢股份 研究关注度高但证据薄，score=4.00；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:600031.SH` | `review` | `600031.SH` 三一重工 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:600183.SH` | `review` | `600183.SH` 生益科技 研究关注度高但证据薄，score=3.50；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:600206.SH` | `review` | `600206.SH` 有研新材 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:600487.SH` | `review` | `600487.SH` 亨通光电 研究关注度高但证据薄，score=6.50；mention=3；stale_ratio=0.00；theme_crowding=16 | `KB-007:hot_but_thin` |
| medium | `symbol:600489.SH` | `review` | `600489.SH` 中金黄金 研究关注度高但证据薄，score=4.00；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:600498.SH` | `review` | `600498.SH` 烽火通信 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:600522.SH` | `review` | `600522.SH` 中天科技 研究关注度高但证据薄，score=6.50；mention=3；stale_ratio=0.00；theme_crowding=16 | `KB-007:hot_but_thin` |
| medium | `symbol:600547.SH` | `review` | `600547.SH` 山东黄金 研究关注度高但证据薄，score=2.75；mention=2；stale_ratio=0.00；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:600549.SH` | `review` | `600549.SH` 厦门钨业 研究关注度高但证据薄，score=2.07；mention=3；stale_ratio=0.67；theme_crowding=14 | `KB-007:hot_but_thin` |
| medium | `symbol:600584.SH` | `review` | `600584.SH` 长电科技 研究关注度高但证据薄，score=1.95；mention=2；stale_ratio=0.50；theme_crowding=8 | `KB-007:hot_but_thin` |
| medium | `symbol:600589.SH` | `review` | `600589.SH` 大位科技 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:600595.SH` | `review` | `600595.SH` 中孚实业 研究关注度高但证据薄，score=4.00；mention=1；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:600875.SH` | `review` | `600875.SH` 东方电气 研究关注度高但证据薄，score=2.25；mention=2；stale_ratio=0.00；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:600893.SH` | `review` | `600893.SH` 航发动力 研究关注度高但证据薄，score=2.25；mention=2；stale_ratio=0.00；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:601100.SH` | `review` | `601100.SH` 恒立液压 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:601208.SH` | `review` | `601208.SH` 东材科技 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:601678.SH` | `review` | `601678.SH` 滨化股份 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:601869.SH` | `review` | `601869.SH` 长飞光纤 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:601899.SH` | `review` | `601899.SH` 紫金矿业 研究关注度高但证据薄，score=2.75；mention=2；stale_ratio=0.00；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:603269.SH` | `review` | `603269.SH` 海鸥股份 研究关注度高但证据薄，score=5.50；mention=2；stale_ratio=0.00；theme_crowding=8 | `KB-007:hot_but_thin` |
| medium | `symbol:603281.SH` | `review` | `603281.SH` 江瀚新材 研究关注度高但证据薄，score=2.45；mention=2；stale_ratio=0.50；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:603296.SH` | `review` | `603296.SH` 华勤技术 研究关注度高但证据薄，score=5.70；mention=3；stale_ratio=0.33；theme_crowding=13 | `KB-007:hot_but_thin` |
| medium | `symbol:603650.SH` | `review` | `603650.SH` 彤程新材 研究关注度高但证据薄，score=2.95；mention=2；stale_ratio=0.50；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:603766.SH` | `review` | `603766.SH` 隆鑫通用 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:603790.SH` | `review` | `603790.SH` 春风动力 研究关注度高但证据薄，score=2.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:603938.SH` | `review` | `603938.SH` 三孚股份 研究关注度高但证据薄，score=2.45；mention=2；stale_ratio=0.50；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:605589.SH` | `review` | `605589.SH` 圣泉集团 研究关注度高但证据薄，score=2.95；mention=2；stale_ratio=0.50；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:688012.SH` | `review` | `688012.SH` 中微公司 研究关注度高但证据薄，score=2.45；mention=2；stale_ratio=0.50；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:688019.SH` | `review` | `688019.SH` 安集科技 研究关注度高但证据薄，score=7.20；mention=4；stale_ratio=0.25；theme_crowding=13 | `KB-007:hot_but_thin` |
| medium | `symbol:688035.SH` | `review` | `688035.SH` 德邦科技 研究关注度高但证据薄，score=2.95；mention=2；stale_ratio=0.50；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:688041.SH` | `review` | `688041.SH` 海光信息 研究关注度高但证据薄，score=5.65；mention=4；stale_ratio=0.50；theme_crowding=20 | `KB-007:hot_but_thin` |
| medium | `symbol:688072.SH` | `review` | `688072.SH` 拓荆科技 研究关注度高但证据薄，score=8.00；mention=2；stale_ratio=0.00；theme_crowding=12 | `KB-007:hot_but_thin` |
| medium | `symbol:688082.SH` | `review` | `688082.SH` 盛美上海 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:688110.SH` | `review` | `688110.SH` 东芯股份 研究关注度高但证据薄，score=6.00；mention=2；stale_ratio=0.00；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:688126.SH` | `review` | `688126.SH` 沪硅产业 研究关注度高但证据薄，score=3.70；mention=2；stale_ratio=0.50；theme_crowding=14 | `KB-007:hot_but_thin` |
| medium | `symbol:688150.SH` | `review` | `688150.SH` 莱特光电 研究关注度高但证据薄，score=2.95；mention=2；stale_ratio=0.50；theme_crowding=11 | `KB-007:hot_but_thin` |
| medium | `symbol:688256.SH` | `review` | `688256.SH` 寒武纪 研究关注度高但证据薄，score=6.27；mention=3；stale_ratio=0.33；theme_crowding=16 | `KB-007:hot_but_thin` |
| medium | `symbol:688359.SH` | `review` | `688359.SH` 三孚新科 研究关注度高但证据薄，score=2.45；mention=2；stale_ratio=0.50；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:688372.SH` | `review` | `688372.SH` 伟测科技 研究关注度高但证据薄，score=2.70；mention=2；stale_ratio=0.50；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:688388.SH` | `review` | `688388.SH` 嘉元科技 研究关注度高但证据薄，score=6.50；mention=2；stale_ratio=0.00；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:688521.SH` | `review` | `688521.SH` 芯原股份 研究关注度高但证据薄，score=7.00；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:688545.SH` | `review` | `688545.SH` 兴福电子 研究关注度高但证据薄，score=5.00；mention=2；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:688603.SH` | `review` | `688603.SH` 天承科技 研究关注度高但证据薄，score=2.45；mention=2；stale_ratio=0.50；theme_crowding=9 | `KB-007:hot_but_thin` |
| medium | `symbol:688702.SH` | `review` | `688702.SH` 盛科通信 研究关注度高但证据薄，score=4.50；mention=1；stale_ratio=0.00；theme_crowding=7 | `KB-007:hot_but_thin` |
| medium | `symbol:688720.SH` | `review` | `688720.SH` 艾森股份 研究关注度高但证据薄，score=5.00；mention=2；stale_ratio=0.00；theme_crowding=6 | `KB-007:hot_but_thin` |
| medium | `symbol:688981.SH` | `review` | `688981.SH` 中芯国际 研究关注度高但证据薄，score=6.50；mention=2；stale_ratio=0.00；theme_crowding=10 | `KB-007:hot_but_thin` |
| medium | `symbol:920045.BJ` | `review` | `920045.BJ` 蘅东光 研究关注度高但证据薄，score=2.00；mention=2；stale_ratio=0.00；theme_crowding=9 | `KB-007:hot_but_thin` |

### inbox/raw 未消化（需新建 wiki 页）（138）

| 优先级 | 路径 | 建议动作 | 原因 | 来源信号 |
|--------|------|----------|------|----------|
| high | `raw/2023-08-30-杭州萧山机场-货站服务收费标准.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-777F-打板要求.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-B737-300F-载重平衡与地面操作.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-B747-400ERF-载重平衡与地面操作.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-B757-200PCF-载重平衡与地面操作.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-B767-300BCF-载重平衡与地面操作.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-B767-300F-Maersk-Configurations.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-B777F-组板要求.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-K4-747BCF-组板要求.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-K4-777-ERSF.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-K4-B747-400F-组板要求.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-K4-B777F-组板要求-版本1.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-W6-板型及限重-OCR.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-W6-板型及限重.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-YTOCARGO-B767SF-training-中文版.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-中文DGR-2025.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-危险品-运输手册.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-国货航-货物装载手册.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-马士基-打板指引-OCR.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-04-28-马士基-打板指引.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-05-15-源达信息-全地形车摩托车出海.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-05-15-锂电池产业链投资分析-星球截图.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-05-17-开源证券-通信周报-CPO与谷歌IO.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-05-21-family-knowledge-prompt.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-05-21-东吴证券-工程机械出海逻辑继续演绎.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-05-21-华源证券-电子布业务打开成长空间.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| high | `raw/2026-05-29-开源证券-氯碱化工龙头北鲲计划.md` | `ingest` | raw 笔记/研报 md，提炼为 wiki/investment 页面，补 sources 反向链接 | `KB-005:raw_undigested` |
| medium | `inbox/2026-04-28-想法.md` | `ingest` | inbox 笔记/想法，提炼为 wiki 页面 | `KB-005:inbox_unprocessed` |
| medium | `inbox/2026-05-27-AI机架结构剧变-内存赛道分析.jpg` | `ingest` | inbox 截图/图片，需 OCR/转写后转写到 wiki | `KB-005:inbox_unprocessed` |
| medium | `inbox/2026-05-27-全球存储芯片公司季度净利润排名.jpg` | `ingest` | inbox 截图/图片，需 OCR/转写后转写到 wiki | `KB-005:inbox_unprocessed` |
| medium | `inbox/2026-05-27-存储芯片行业趋势.md` | `ingest` | inbox 笔记/想法，提炼为 wiki 页面 | `KB-005:inbox_unprocessed` |
| medium | `inbox/screenshots/20260515-152102.png` | `ingest` | inbox 截图/图片，需 OCR/转写后转写到 wiki | `KB-005:inbox_unprocessed` |
| medium | `inbox/screenshots/20260515-152300.png` | `ingest` | inbox 截图/图片，需 OCR/转写后转写到 wiki | `KB-005:inbox_unprocessed` |
| medium | `inbox/screenshots/20260515-152320.png` | `ingest` | inbox 截图/图片，需 OCR/转写后转写到 wiki | `KB-005:inbox_unprocessed` |
| medium | `raw/2026-05-22-K4-航班时刻表-2026年6月-HGH.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260510-中银证券-电新行业2025年报及2026Q1综述.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260512-东莞证券-PCB产业链2025及26Q1业绩综述.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260512-中银证券-电子材料行业综述.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260512-中银证券-电子行业2025年报及2026Q1综述.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260512-华源证券-人形机器人产业周报.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260512-山西证券-星源卓镁业绩拐点.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260513-万联证券-动储双轮驱动锂电业绩加速回升.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260513-东吴证券-磷化铟光之基石.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260513-华鑫证券-格林美业绩稳健增长.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/20260514-国盛证券-算力租赁专题.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/104ff08e-fe86-4e1d-888c-e6ed1881f7e4_危险品运输手册(1).pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/117f12f8-9eb8-4755-9eb4-5ca7decfb4cf_中文DGRcompressed2025_small.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/1773817f-d87e-4bbf-8238-247235d30f9c_777F打板要求(1).pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/1a984126-2ebb-48ab-910a-44e3b3aa9f02_国货航货物装载手册.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2023-08-30-杭州萧山机场-货站服务收费标准.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-09-特斯拉人形机器人量产临近.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-10-中银证券-电新行业年报一季报综述.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-10-国海证券-芯原股份688521.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-10-国金证券-锂电5月洞察.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-11-东北证券-湿电子化学品.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-11-华源证券-算电时代三大预期差.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-11-开源证券-壹连科技与高端机床.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-14-中邮证券-华勤技术超节点.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-14-华源证券-无源光器件收入增长61.5%.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-14-国金证券-PCB化学品与光纤材料.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-14-开源证券-阿里腾讯AI硬底气.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-15-东北证券-锂电铜箔周期拐点与PCB铜箔供需紧张.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-15-中邮证券-异构聚力智算未来.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-15-源达信息-全地形车摩托车出海.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-16-东吴证券-光纤光缆算力时代物理基石.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-17-国金证券-CPU涨价能持续多久.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-17-开源证券-通信周报-CPO与谷歌IO.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-18-开源证券-金属中期策略-地缘压制基本面稳健.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-18-开源证券-金属中期策略.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-18-开源证券-锂电中期策略-动储共振周期上行.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-18-开源证券-锂电中期策略.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-18-瑞银-能源自主A股驱动力.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-18-瑞银-能源自主成为A股长期驱动力.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-19-国金证券-机床行业-周期向上国产替代.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-19-开源证券-化工中期策略-反内卷谋双碳.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-19-开源证券-化工中期策略.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-19-开源证券-煤炭中期策略-能源安全价值重估.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-19-开源证券-煤炭中期策略.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-20-MorganStanley-RubinRackBOM.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-20-东吴证券-激光设备巨擘-PCB设备3D打印.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-20-中邮证券-Token套餐上线.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-20-太平洋-业绩增长看好8600放量.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-20-红宝书.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-20-财通证券-国防军工-AI缺电燃机国产化.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-21-MorganStanley-MurataMLCC.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-21-东吴证券-工程机械出海逻辑继续演绎.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-21-华源证券-存储测试设备布局完善.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-21-华源证券-电子布业务打开成长空间.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-21-国金证券-超级电容AI电源革命.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-22-国金证券-匠心铸就龙头.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-23-GoldmanSachs-TaiwanWeekly.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-25-KResearch-华为韬定律.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-29-Bernstein-Dell-FQ127.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-29-开源证券-氯碱化工龙头北鲲计划.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/2026-05-29-开源证券-氯碱化工龙头深度.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/327af291-7395-4bf9-8a64-06d89c09d152_波音737-300F载重平衡和地面操作培训教材20141215.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/3c9d7ce1-4935-4298-b101-16229a469869_波音757-200PCF载重平衡和地面操作培训教材 20150730.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/4fcab0b7-b33d-42b2-a23c-5f42b264f621_波音767-300BCF载重平衡和地面操作培训教材(缺STC手册).pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/7828df56-f65a-4336-b97b-ce9f32da754f_B767-300F Maersk Configurations Rev03.23.23.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/aa77697f-c78c-421b-a4d2-b17e2ca1a972_K4-777-ERSF(1).pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/e551d2f5-b754-48e1-9a6e-4fef64eab710_波音747-400ERF载重平衡和地面操作培训教材20141215.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/e96fad85-9c03-43e3-ac54-a237ab06cb55_马士基打板指引.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| medium | `raw/assets/eb35cbac-ccfa-4e5a-9589-a45376511745_W6板型及限重.pdf` | `ingest` | raw PDF 研报，精读后提炼 wiki 页面（不批量读取正文） | `KB-005:raw_undigested` |
| low | `inbox/family-knowledge-prompt.md` | `review` | meta prompt/模板，确认归属分区后归档 | `KB-005:inbox_unprocessed` |
| low | `raw/.DS_Store` | `review` | raw 未知类型文件，人工确认是否需要消化 | `KB-005:raw_undigested` |
| low | `raw/20260515-152102.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/20260515-152300.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/20260515-152320.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/01ef83ca-5c4f-4858-a691-fe8166e428b1_YTOCARGO B767SF training - 中文版.pptx` | `review` | raw 未知类型文件，人工确认是否需要消化 | `KB-005:raw_undigested` |
| low | `raw/assets/0de2ac10-4e54-4b82-91c6-a2ef24102bfa_K4 747BCF组板要求.docx` | `review` | raw 未知类型文件，人工确认是否需要消化 | `KB-005:raw_undigested` |
| low | `raw/assets/199c268b-b3b9-497f-b086-dcd529877c7c_K4 B777F 全货机组板要求.docx` | `review` | raw 未知类型文件，人工确认是否需要消化 | `KB-005:raw_undigested` |
| low | `raw/assets/2026-05-15-锂电池-公司清单.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/2026-05-15-锂电池-研报摘要.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/2026-05-15-锂电池-评分体系.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/2026-05-27-AI机架结构剧变-内存赛道分析.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/2026-05-27-全球存储芯片公司季度净利润排名.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260512-杂项标的评分表.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260512-电池回收新能源评分表.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260512-算力资源市值比.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260513-AI算力基础设施评分表.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260513-AI算力网络评分表.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260513-PCB化学品新材料评分表.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260513-电子特气钨评分表.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260514-半导体封测评分表.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260514-稀土板块评分表.png` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-PCB产业链利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-人形机器人产业链利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-半导体产业链利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-新能源产业链利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-星源卓镁等镁合金利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-格林美等新能源利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-电子材料利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-磷化铟利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/20260516-算力租赁利好公司表格.jpg` | `ingest` | raw 图片素材，OCR/转写后归档或转写 | `KB-005:raw_undigested` |
| low | `raw/assets/4a9937a8-8178-46bd-9d8c-887689ec0ec0_B777F 全货机组板要求.docx` | `review` | raw 未知类型文件，人工确认是否需要消化 | `KB-005:raw_undigested` |
| low | `raw/assets/ff58281e-1626-4eb2-960e-ff39c0a1258a_K4 B747-400F全货机组板要求.docx` | `review` | raw 未知类型文件，人工确认是否需要消化 | `KB-005:raw_undigested` |
| low | `raw/videos/2026-04-29_02年小伙海外生意.mp4` | `review` | raw 未知类型文件，人工确认是否需要消化 | `KB-005:raw_undigested` |
| low | `raw/videos/7635990501313424970.mp4` | `review` | raw 未知类型文件，人工确认是否需要消化 | `KB-005:raw_undigested` |

## 5. Tree Work ingest 模板

新建 / 重写 wiki/investment 页面时，按以下 frontmatter + 章节骨架填写；对应契约见 `docs/local_knowledge_contract.md`。

```markdown
---
title: <公司名/主题> — <一句话标题>
created: 2026-07-05
updated: 2026-07-05
sources:
  - "[[../../raw/<原始资料文件名>.md|<来源别名>]]"
tags: [<主题标签>]
related: []
symbols: ["<6位代码>.SH <简称>"]   # 例：603296.SH 华勤技术
themes: [<主题>]                   # 例：AI服务器
industry_chain_roles: [<产业链角色>]
report_type: 公司点评|行业|综述|数据表|财报分析
evidence_level: A|B|C              # C=低置信
valid_until: YYYY-MM-DD 或 长期
source_quality: 高|中|低
stale_risk: 低|中|高
---

# <标题>

## 一句话总结

<1-2 句话点出核心驱动 / 结论，TA 仅取该段作为背景摘要。>

## 投资逻辑

- <核心驱动 1>
- <核心驱动 2>
- <核心驱动 3>

## 风险提示

- <风险 1>
- <风险 2>

## 原始资料

- [[../../raw/<原始资料>.md|<研报别名>]]
```

## 6. 建议执行顺序

1. **先补字段（missing_symbol / thesis / risks / sources）**：
   - 这些是 TA 已能命中的页面，补字段后立刻能被 KB-003 / KB-008 高置信引用。
2. **复核过期/低置信页（needs_review_stale）**：
   - 更新 `valid_until` / 重写 stale_risk；无法补证的标记 `evidence_level=C`。
3. **处理热门但证据薄（hot_but_thin）**：
   - 补高质量来源（`source_quality=高` + `evidence_level=A`）或降权。
4. **新建 ingest 页面（ingest_new）**：
   - 优先消化 raw/ 下高优先级 .md 研报；inbox 笔记按主题归类后合并。

> 每条任务完成后，在对应 wiki 页 frontmatter 更新 `updated`；后续运行 `scripts/tree_work_task_pack.py` 会自动剔除已完成项。

## 7. 免责声明

- 本任务包只提供 Tree Work ingest 字段要求与优先级，**不构成任何买卖建议或强动作词**。
- 所有任务来源可追溯到 KB-002/KB-005/KB-007/KB-009 的规则 ID；执行后可重跑对应 CLI 验证。
- 任务包不含研报原文段落，仅引用相对路径与简短原因。

---

_由 `scripts/tree_work_task_pack.py` 只读生成；对应模块 `tradingagents.dataflows.tree_work_task_pack`。_
