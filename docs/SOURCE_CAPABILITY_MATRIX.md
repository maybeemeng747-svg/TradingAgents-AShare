# A 股数据源能力矩阵

> 本文档由 `tradingagents/dataflows/source_capability_matrix.py` 自动生成。记录每个 data_type 的首选 vendor、fallback、实时性、单位、已知限制与状态语义。不包含任何 API Key 或密钥信息。

- 代码目录：`tradingagents/dataflows/source_capability_matrix.py`
- 数据源目录：`tradingagents/dataflows/source_catalog.py`
- API 目录：`GET /v1/config/source-capability-matrix`
- 矩阵版本：`2026-07-27`
- data_type 数量：`16`

## 矩阵总览

| data_type | 中文名 | primary_vendor | primary_endpoint | fallback_vendor | freshness | unit | 限流风险 | source_count |
|-----------|--------|----------------|------------------|-----------------|-----------|------|---------|--------------|
| `ohlcv` | K 线 / 历史行情 | cn_akshare | `stock_zh_a_hist` | cn_astock | `daily` | 元/股, 股 | `medium` | 6 |
| `fund_flow` | 个股资金流 | cn_akshare | `stock_individual_fund_flow` | cn_astock | `daily` | 万元 | `high` | 2 |
| `board_fund_flow` | 板块资金流 | cn_akshare | `stock_fund_flow_industry` | cn_astock | `daily` | 亿元 | `medium` | 3 |
| `lhb` | 龙虎榜 | cn_akshare | `stock_lhb_detail_em` | cn_astock | `daily` | 万元 | `high` | 2 |
| `margin_trading` | 融资融券 | cn_akshare | `stock_margin_underlying_info_szse` | cn_astock | `daily` | 万元/股 | `medium` | 2 |
| `notice` | 公司公告 | cn_astock | `cninfo.com.cn/hisAnnouncement` | cn_akshare | `daily` | 条 | `low` | 2 |
| `report` | 券商研报 | cn_akshare | `stock_institute_recommend` | cn_astock | `daily` | 条 | `medium` | 2 |
| `rating` | 分析师评级 | cn_akshare | `stock_institute_recommend_detail` | cn_astock | `daily` | 条 | `medium` | 2 |
| `news` | 个股新闻 | cn_akshare | `stock_news_em` | cn_astock | `daily` | 条 | `medium` | 2 |
| `global_news` | 全市场快讯 | cn_akshare | `news_cctv` | cn_astock | `daily` | 条 | `low` | 3 |
| `financials` | 财务三表 | cn_akshare | `stock_individual_info_em` | cn_astock | `delayed` | 项 | `medium` | 4 |
| `insider` | 内部交易 / 股东 | cn_akshare | `stock_main_stock_holder` | cn_astock | `delayed` | 股 | `medium` | 2 |
| `hot_stocks` | 热门股票 | cn_akshare | `stock_hot_follow_xq` | cn_astock | `intraday` | 条 | `medium` | 2 |
| `zt_pool` | 涨停池 | cn_akshare | `stock_zt_pool_em` | cn_astock | `daily` | 条 | `medium` | 2 |
| `realtime_quotes` | 实时行情 | cn_akshare | `hq.sinajs.cn` | cn_astock | `realtime` | 元, 股, 元 | `low` | 3 |
| `buyback` | 回购 | cn_akshare | `stock_repurchase` | cn_astock | `daily` | 万元 | `medium` | 2 |

## 状态语义详情

下表说明每个 data_type 的实时性边界、已知限制与 agent 应当如何使用。

### `ohlcv` — K 线 / 历史行情

- **首选源**：`cn_akshare` / `stock_zh_a_hist`
- **Fallback 链**：cn_akshare → cn_akshare → cn_akshare → cn_astock → cn_astock → cn_baostock
- **实时性**：`daily`
- **单位**：元/股, 股
- **字段**：Date, Open, High, Low, Close, Volume
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：东财前复权日线，通过 AKShare 调用；盘中实时 patch 由 G-005 补丁机制提供

### `fund_flow` — 个股资金流

- **首选源**：`cn_akshare` / `stock_individual_fund_flow`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：万元
- **字段**：日期, 主力净流入, 小单净流入, 中单净流入, 大单净流入, 超大单净流入
- **已知限制**：限流风险：高（必须限流 + 可切换 fallback） | 已知缺口：高限流风险；ConnectionError 常见
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）；高限流风险，必须启用 fallback 与重试退避
- **备注**：AKShare/东财个股资金流，限流风险高，fallback 必须可切换

### `board_fund_flow` — 板块资金流

- **首选源**：`cn_akshare` / `stock_fund_flow_industry`
- **Fallback 链**：cn_akshare → cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：亿元
- **字段**：行业, 行业指数, 流入资金, 流出资金, 净额
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：AKShare/同花顺行业板块资金流

### `lhb` — 龙虎榜

- **首选源**：`cn_akshare` / `stock_lhb_detail_em`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：万元
- **字段**：代码, 名称, 上榜原因, 买入额, 卖出额, 净买额
- **已知限制**：限流风险：高（必须限流 + 可切换 fallback） | 已知缺口：高限流风险；force=False 时返回 NOT_QUERIED
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）；高限流风险，必须启用 fallback 与重试退避；需 force=True 才会实际查询，默认返回 NOT_QUERIED
- **备注**：AKShare/东财龙虎榜，必须 force=True 才实际查询

### `margin_trading` — 融资融券

- **首选源**：`cn_akshare` / `stock_margin_underlying_info_szse`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：万元/股
- **字段**：标的证券代码, 标的证券简称, 融资买入额, 融资余额, 融券卖出量, 融券余量
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：AKShare 深交所融资融券标的；沪市用 stock_margin_underlying_info_sse

### `notice` — 公司公告

- **首选源**：`cn_astock` / `cninfo.com.cn/hisAnnouncement`
- **Fallback 链**：cn_astock → cn_akshare
- **实时性**：`daily`
- **单位**：条
- **字段**：announcementTitle, announcementTypeName, announcementTime, announcementId
- **已知限制**：限流风险：低（可频繁调用）
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：巨潮公告全文检索，需 orgId 映射

### `report` — 券商研报

- **首选源**：`cn_akshare` / `stock_institute_recommend`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：条
- **字段**：日期, 研究机构, 评级, 标题, 目标价
- **已知限制**：限流风险：中（建议带缓存与退避） | 已知缺口：AKShare 研报接口字段覆盖不稳定
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）；字段覆盖不稳定，需校验后再信任
- **备注**：AKShare 券商研报/评级推荐接口

### `rating` — 分析师评级

- **首选源**：`cn_akshare` / `stock_institute_recommend_detail`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：条
- **字段**：股票代码, 股票名称, 目标价, 最新评级, 评级机构, 分析师, 行业, 评级日期
- **已知限制**：限流风险：中（建议带缓存与退避） | 已知缺口：新浪财经评级数据覆盖不全
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：AKShare 新浪财经股票评级记录

### `news` — 个股新闻

- **首选源**：`cn_akshare` / `stock_news_em`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：条
- **字段**：新闻标题, 文章来源, 新闻内容, 新闻链接, 发布时间
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：AKShare/东财个股新闻

### `global_news` — 全市场快讯

- **首选源**：`cn_akshare` / `news_cctv`
- **Fallback 链**：cn_akshare → cn_astock → cn_astock
- **实时性**：`daily`
- **单位**：条
- **字段**：title, content
- **已知限制**：限流风险：低（可频繁调用）
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：央视新闻，AKShare 封装

### `financials` — 财务三表

- **首选源**：`cn_akshare` / `stock_individual_info_em`
- **Fallback 链**：cn_akshare → cn_akshare → cn_astock → cn_baostock
- **实时性**：`delayed`
- **单位**：项
- **字段**：公司基本信息
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：延迟 >1 个交易日（如 T+1 / T+2 财务数据）。仅适合长期回看与季度评估。；角色：首选源（primary）
- **备注**：AKShare/东财个股基本信息

### `insider` — 内部交易 / 股东

- **首选源**：`cn_akshare` / `stock_main_stock_holder`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`delayed`
- **单位**：股
- **字段**：股东名称, 持股数, 增减
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：延迟 >1 个交易日（如 T+1 / T+2 财务数据）。仅适合长期回看与季度评估。；角色：首选源（primary）
- **备注**：AKShare 十大股东

### `hot_stocks` — 热门股票

- **首选源**：`cn_akshare` / `stock_hot_follow_xq`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`intraday`
- **单位**：条
- **字段**：代码, 名称, 热度
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：盘中更新但有延迟（分钟级）。适合盘中观察，不宜做毫秒级套利。；角色：首选源（primary）
- **备注**：雪球热搜股票

### `zt_pool` — 涨停池

- **首选源**：`cn_akshare` / `stock_zt_pool_em`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：条
- **字段**：代码, 名称, 涨停价, 连板数
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：AKShare/东财涨停池

### `realtime_quotes` — 实时行情

- **首选源**：`cn_akshare` / `hq.sinajs.cn`
- **Fallback 链**：cn_akshare → cn_akshare → cn_astock
- **实时性**：`realtime`
- **单位**：元, 股, 元
- **字段**：price, open, high, low, previous_close, change, change_pct, volume, amount, quote_time
- **已知限制**：限流风险：低（可频繁调用）
- **状态语义**：盘中实时推送（秒级）。最接近实时价，可作盘中决策依据；停牌 / 非交易时段无更新。；角色：首选源（primary）
- **备注**：新浪实时行情，轻量且稳定，cn_akshare 的首选实时源

### `buyback` — 回购

- **首选源**：`cn_akshare` / `stock_repurchase`
- **Fallback 链**：cn_akshare → cn_astock
- **实时性**：`daily`
- **单位**：万元
- **字段**：公告日期, 回购金额, 回购数量, 回购进度, 回购目的
- **已知限制**：限流风险：中（建议带缓存与退避）
- **状态语义**：收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。；角色：首选源（primary）
- **备注**：AKShare 个股回购计划/进展

## Freshness 语义图例

| freshness | 说明 |
|-----------|------|
| `realtime` | 盘中实时推送（秒级）。最接近实时价，可作盘中决策依据；停牌 / 非交易时段无更新。 |
| `intraday` | 盘中更新但有延迟（分钟级）。适合盘中观察，不宜做毫秒级套利。 |
| `daily` | 收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。 |
| `delayed` | 延迟 >1 个交易日（如 T+1 / T+2 财务数据）。仅适合长期回看与季度评估。 |
| `stale` | 数据陈旧或长期不更新。不可作为决策依据，需触发 fallback 或人工核实。 |
| `unknown` | 新鲜度未知。使用前必须 verify，未 verify 不得作为 primary source。 |

## 限流风险图例

| rate_limit_risk | 说明 |
|-----------------|------|
| `low` | 低（可频繁调用） |
| `medium` | 中（建议带缓存与退避） |
| `high` | 高（必须限流 + 可切换 fallback） |
| `unknown` | 未知 |

## 字段说明

- `data_type`：数据类型枚举（quote / ohlcv / fund_flow / lhb / notice / report / rating / news / buyback ...）。
- `primary_vendor`：当前首选数据源 vendor（cn_akshare / cn_astock / cn_baostock ...）。
- `fallback_vendor`：首选 vendor 失败时的下一个 fallback vendor。
- `fallback_chain`：完整 fallback vendor 列表（按 priority 排序）。
- `freshness`：实时性等级（realtime / intraday / daily / delayed / stale / unknown）。
- `unit`：主要字段单位（万元 / 元 / 股 / 条 ...）。
- `known_limits`：已知限制摘要（限流风险 + known_gaps）。
- `status_semantics`：状态语义说明（freshness 含义 + 角色提示 + 关键风险）。
- `source_count`：该 data_type 在 source_catalog 中登记的 source 数量。
