# 免费研报/公告/半年报源 Smoke 报告 — DATA-027

> [DATA-027] research_source_smoke. 在 DATA-025 基础上扩展免费源 smoke, 覆盖研报元数据 / 公告披露 / 半年报披露三类入口, 并增加失败归因 (网络失败 / 限流 / 字段缺失 / 结构变更 / 正常无数据).

- **Date**: 2026-07-10
- **Run at**: 2026-07-10 03:24:20
- **Mode**: `fixture`
- **Env gated**: n/a (fixture dry-run)
- **Symbols**: fixture

## 披露源目录 (研报 / 公告 / 半年报)

> 字段与 DATA-023 能力矩阵一致: data_type / vendor / endpoint / freshness / unit / fields / rate_limit_risk.

| source_id | source_type | 名称 | vendor | endpoint | data_type | freshness | rate_limit | 角色 |
|-----------|-------------|------|--------|----------|-----------|------------|------------|------|
| `eastmoney_research_report_em` | 研报元数据 | 东方财富研报中心 / 个股研报 | cn_akshare | `reportapi.eastmoney.com/report/list` | `report` | `daily` | `medium` | 观点源 |
| `cninfo_announcement` | 公告披露 | 巨潮资讯 / CNInfo 公告披露 | cn_astock | `cninfo.com.cn/hisAnnouncement` | `notice` | `daily` | `low` | 事实源 |
| `eastmoney_announcement` | 公告披露 | 东方财富公告 / 数据中心 | cn_astock | `np-anotice-stock.eastmoney.com/api/security/ann` | `notice` | `daily` | `medium` | 事实源 |
| `cninfo_half_year_report` | 半年报披露 | 巨潮资讯 / 半年报定期披露 | cn_astock | `cninfo.com.cn/hisAnnouncement (category=半年度报告)` | `notice` | `daily` | `low` | 事实源 |

### 各来源字段与限制

#### `eastmoney_research_report_em` — 东方财富研报中心 / 个股研报

- **source_type**: `research_report`
- **vendor / endpoint**: `cn_akshare` / `reportapi.eastmoney.com/report/list`
- **AKShare 方法**: `stock_research_report_em`
- **data_type**: `report` (与 DATA-023 一致)
- **freshness / unit / rate_limit**: `daily` / `条` / `medium`
- **字段**: 日期, 报告名称, 股票简称, 股票代码, 机构, 东财评级, 行业, 盈利预测-收益, 盈利预测-市盈率, 报告PDF链接, 近一月个股研报数
- **用途**: AKShare stock_research_report_em(symbol) 个股研报元数据、机构、评级、盈利预测、PDF 链接。仅作观点/关注度源, 不替代公告或财报。
- **已知限制**: 字段覆盖依赖东财上书机构, 部分研报缺盈利预测/目标价；PDF 正文受版权保护, 不得下载入库

#### `cninfo_announcement` — 巨潮资讯 / CNInfo 公告披露

- **source_type**: `announcement`
- **vendor / endpoint**: `cn_astock` / `cninfo.com.cn/hisAnnouncement`
- **data_type**: `notice` (与 DATA-023 一致)
- **freshness / unit / rate_limit**: `daily` / `条` / `low`
- **字段**: 公告标题, 公告类型, 公告时间, 公告ID, 股票代码, 股票简称
- **用途**: 公告/法披真源, 用于公告披露交叉验证。研报观点不能凌驾于公告事实之上。
- **已知限制**: 需 orgId 映射；公告数量大需按类型筛选

#### `eastmoney_announcement` — 东方财富公告 / 数据中心

- **source_type**: `announcement`
- **vendor / endpoint**: `cn_astock` / `np-anotice-stock.eastmoney.com/api/security/ann`
- **data_type**: `notice` (与 DATA-023 一致)
- **freshness / unit / rate_limit**: `daily` / `条` / `medium`
- **字段**: 公告标题, 公告类型, 公告日期, 公告代码, 股票代码
- **用途**: 东财公告接口, 作为 CNInfo 公告源的补充, 公告类型与时间字段为主。
- **已知限制**: 字段命名与 CNInfo 不同, 需归一化

#### `cninfo_half_year_report` — 巨潮资讯 / 半年报定期披露

- **source_type**: `half_year_report`
- **vendor / endpoint**: `cn_astock` / `cninfo.com.cn/hisAnnouncement (category=半年度报告)`
- **data_type**: `notice` (与 DATA-023 一致)
- **freshness / unit / rate_limit**: `daily` / `条` / `low`
- **字段**: 公告标题, 公告类型, 公告时间, 公告ID, 股票代码, 报告年度, 报告类型
- **用途**: CNInfo 半年度报告披露入口, 报告期内可获取半年报披露公告与 PDF 链接。非报告期返回空属 NORMAL_NO_DATA。
- **已知限制**: 仅在半年报披露季 (7-9 月) 有数据；需按 category 筛选半年度报告

## Smoke Summary

| Metric | Value |
|--------|-------|
| Total probes | 9 |
| HAS_DATA | 4 |
| NORMAL_NO_DATA | 3 |
| FAILED | 2 |
| SKIPPED | 0 |
| Total records parsed | 9 |
| Required attribution covered | field_missing, network_error, no_data, rate_limited |
| Required attribution missing | none ✓ |
| All runnable passed | Yes |
| Has live failures | No |

### By error_type (失败归因)

| error_type | label_cn | count |
|------------|----------|-------|
| `ok` | 正常 | 3 |
| `network_error` | 网络/接口失败 | 1 |
| `rate_limited` | 限流 | 1 |
| `field_missing` | 字段缺失 | 1 |
| `schema_change` | 接口结构变更 | 1 |
| `no_data` | 正常无数据 | 2 |

### By source_type

| source_type | count |
|-------------|-------|
| `announcement` | 3 |
| `half_year_report` | 2 |
| `research_report` | 4 |

## Probe Details

| Symbol | source_type | Source | Vendor | Mode | Status | error_type | Records | Fixture | Diagnosis |
|--------|-------------|--------|--------|------|--------|------------|---------|---------|-----------|
| 600519.SH | research_report | `eastmoney_research_report_em` | cn_akshare | fixture | HAS_DATA | `ok` | 2 | HAS_DATA | 东财研报中心正常返回多份券商研报元数据 |
| 000001.SZ | announcement | `cninfo_announcement` | cn_astock | fixture | HAS_DATA | `ok` | 2 | HAS_DATA_ANNOUNCEMENT | CNInfo 公告披露正常返回近期公告 |
| 603629.SH | half_year_report | `cninfo_half_year_report` | cn_astock | fixture | HAS_DATA | `ok` | 1 | HAS_DATA_HALF_YEAR | CNInfo 半年报披露季正常返回半年报公告 |
| 603629.SH | research_report | `eastmoney_research_report_em` | cn_akshare | fixture | NORMAL_NO_DATA | `no_data` | 0 | NORMAL_NO_DATA | 接口正常但标的近期无研报覆盖 (新股 / 冷门股 / 停牌) |
| 600519.SH | half_year_report | `cninfo_half_year_report` | cn_astock | fixture | NORMAL_NO_DATA | `no_data` | 0 | NORMAL_NO_DATA_HALF_YEAR | 非半年报披露季, CNInfo 半年报入口正常返回空 (属正常无数据) |
| 000001.SZ | research_report | `eastmoney_research_report_em` | cn_akshare | fixture | FAILED | `network_error` | 0 | FAILED | AKShare / 东财接口失败 (ConnectionError / 超时) |
| 000001.SZ | announcement | `eastmoney_announcement` | cn_astock | fixture | FAILED | `rate_limited` | 0 | RATE_LIMITED | 东财公告接口 429 限流 — 退避后重试或切 fallback |
| 600519.SH | research_report | `eastmoney_research_report_em` | cn_akshare | fixture | HAS_DATA | `field_missing` | 2 | FIELD_MISSING | 接口返回行但关键契约字段 (标题/日期) 为空 — 字段缺失 |
| 000001.SZ | announcement | `eastmoney_announcement` | cn_astock | fixture | NORMAL_NO_DATA | `schema_change` | 2 | SCHEMA_CHANGE | 接口列名/结构变更, 解析后字段全空 |

## Sample Records (有数据)

> 仅展示披露**元数据** (标题/机构/类型/日期/PDF 链接); 不含 PDF 正文, 不含版权内容.

### 600519.SH — research_report — 2 records

| 日期 | 标题 | 机构/主体 | 类型 | 评级 | PDF 链接 |
|------|------|-----------|------|------|----------|
| 2026-06-15 | 贵州茅台2026年深度研究 | 中信证券 | 买入 | 买入 | https://pdf.dfcfw.com/pdf/H3_ABC123_1.pdf |
| 2026-06-10 | 贵州茅台一季报点评 | 中金公司 | 增持 | 增持 | https://pdf.dfcfw.com/pdf/H3_DEF456_1.pdf |

### 000001.SZ — announcement — 2 records

| 日期 | 标题 | 机构/主体 | 类型 | 评级 | PDF 链接 |
|------|------|-----------|------|------|----------|
| 2026-08-30 | 平安银行2026年半年度报告 | - | 定期报告 | - | - |
| 2026-07-15 | 平安银行关于召开2026年第三次临时股东大会的通知 | - | 股东大会 | - | - |

### 603629.SH — half_year_report — 1 records

| 日期 | 标题 | 机构/主体 | 类型 | 评级 | PDF 链接 |
|------|------|-----------|------|------|----------|
| 2026-08-28 | 利通电子2026年半年度报告 | - | 半年度报告 | - | - |

### 600519.SH — research_report — 2 records

| 日期 | 标题 | 机构/主体 | 类型 | 评级 | PDF 链接 |
|------|------|-----------|------|------|----------|
| - | - | - | - | - | - |
| - | - | - | - | - | - |

### 000001.SZ — announcement — 2 records

| 日期 | 标题 | 机构/主体 | 类型 | 评级 | PDF 链接 |
|------|------|-----------|------|------|----------|
| - | - | - | - | - | - |
| - | - | - | - | - | - |

## Fixture Coverage

验收要求的 4 类场景 (有数据 / 无数据 / 接口失败 / 字段缺失) 均已回放, 并补充限流与结构变更两类, 覆盖研报 / 公告 / 半年报三类 source_type.

| fixture_id | source_type | expected → actual | description |
|------------|-------------|-------------------|-------------|
| `HAS_DATA` | research_report | `ok` → `ok` ✓ | 东财研报中心正常返回多份券商研报元数据 |
| `HAS_DATA_ANNOUNCEMENT` | announcement | `ok` → `ok` ✓ | CNInfo 公告披露正常返回近期公告 |
| `HAS_DATA_HALF_YEAR` | half_year_report | `ok` → `ok` ✓ | CNInfo 半年报披露季正常返回半年报公告 |
| `NORMAL_NO_DATA` | research_report | `no_data` → `no_data` ✓ | 接口正常但标的近期无研报覆盖 (新股 / 冷门股 / 停牌) |
| `NORMAL_NO_DATA_HALF_YEAR` | half_year_report | `no_data` → `no_data` ✓ | 非半年报披露季, CNInfo 半年报入口正常返回空 (属正常无数据) |
| `FAILED` | research_report | `network_error` → `network_error` ✓ | AKShare / 东财接口失败 (ConnectionError / 超时) |
| `RATE_LIMITED` | announcement | `rate_limited` → `rate_limited` ✓ | 东财公告接口 429 限流 — 退避后重试或切 fallback |
| `FIELD_MISSING` | research_report | `field_missing` → `field_missing` ✓ | 接口返回行但关键契约字段 (标题/日期) 为空 — 字段缺失 |
| `SCHEMA_CHANGE` | announcement | `schema_change` → `schema_change` ✓ | 接口列名/结构变更, 解析后字段全空 |

## 失败归因说明

DATA-027 把 DATA-025 的 3 类粗粒度 fixture 升级为失败归因分类器 (借鉴 DATA-024). 关键区分:

| error_type | 含义 | 对应 status |
|------------|------|--------------|
| `ok` | 正常 | `HAS_DATA` |
| `network_error` | 网络/接口失败 | `FAILED` |
| `rate_limited` | 限流 | `FAILED` |
| `field_missing` | 字段缺失 | `HAS_DATA` |
| `schema_change` | 接口结构变更 | `NORMAL_NO_DATA` |
| `no_data` | 正常无数据 | `NORMAL_NO_DATA` |
| `unknown` | 未知 | `FAILED` |

- **字段缺失 (field_missing)**: 接口返回了行但关键契约字段 (标题/日期) 全空, 既不是 FAILED 也不是 NORMAL_NO_DATA — 需单独标记, 防止空壳数据被当 HAS_DATA.
- **正常无数据 (no_data)** 与 **接口失败 (network_error)** 严格区分: 无研报/无公告是正常, 接口崩溃才是失败.

## 边界声明

- 默认 fixture / dry-run, 不做大批量抓取; 不下载或提交 PDF 正文.
- 研报/评级仅作**观点 / 关注度 / 预期源**, 公告/半年报才是**事实源**.
- 严格区分 NORMAL_NO_DATA (无数据属正常) 与 FAILED (接口失败).
- 与 DATA-023 能力矩阵字段一致 (data_type / vendor / endpoint / freshness / unit / fields / rate_limit_risk); 不修改 matrix items.
- live-smoke 默认关闭, 需 `TA_LIVE_DATA_SMOKE=1` + `--live-smoke` 双重门禁.

---
*Generated by research_source_smoke.py — `[DATA-027] research_source_smoke`*
