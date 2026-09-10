# 研报主线验收日报（V-014）— 2026-09-11

- 知识库：`/Users/maybee/Documents/knowledge`（只读 smoke，未写入任何文件）
- 索引页面：435（解析失败 0）；半年报页 4；过期页 111
- 缓存新鲜度（KB-010 只读构建）：missing（435 页）

## 抽样（四类）

- 多研报同股：300750.SZ 宁德时代, 688019.SH 安集科技
- 已有半年报：688041.SH 海光信息, 688256.SH 寒武纪
- 无半年报：688008.SH XD澜起科技, 603019.SH 中科曙光
- 过期知识页：wiki/investment/AI算力-公司评分表.md, wiki/investment/AI算力基础设施-公司评分表.md

## 核心统计

- 观点/事实分离覆盖率（source_type 全部落白名单）：103/435 = 23.7%
- citation 完整率：抽样无可审计声明（total=0）
- 事实冲突率（半年报 bucket conflict 或页级冲突）：0/7 = 0.0%

## 抽样证据探测（KB-020 只读聚合）

| symbol | 状态 | 聚合 data_status | 契约违例 |
|---|---|---|---|
| 300750.SZ | OK | fresh | 无 |
| 688019.SH | OK | fresh | 无 |
| 688041.SH | OK | fresh | 无 |
| 688256.SH | OK | fresh | 无 |
| 688008.SH | OK | fresh | 无 |
| 603019.SH | OK | fresh | 无 |
| 中际旭创 | SKIPPED | - | 无 |
| 603296.SH | OK | fresh | 无 |

### 抽样 bucket 明细

| symbol | consensus | citation_audit | thesis_timeline | half_year_facts | score_snapshot |
|---|---|---|---|---|---|
| 300750.SZ | fresh/hit=true | missing/hit=false | fresh/hit=true | missing/hit=false | missing/hit=false |
| 688019.SH | fresh/hit=true | missing/hit=false | fresh/hit=true | missing/hit=false | missing/hit=false |
| 688041.SH | fresh/hit=true | missing/hit=false | fresh/hit=true | missing/hit=true | missing/hit=false |
| 688256.SH | fresh/hit=true | missing/hit=false | fresh/hit=true | missing/hit=true | missing/hit=false |
| 688008.SH | fresh/hit=true | missing/hit=false | fresh/hit=true | missing/hit=false | missing/hit=false |
| 603019.SH | fresh/hit=true | missing/hit=false | fresh/hit=true | missing/hit=false | missing/hit=false |
| 中际旭创 | - | - | - | - | - |
| 603296.SH | fresh/hit=true | missing/hit=false | fresh/hit=true | missing/hit=true | missing/hit=false |

## 通过项

- 真实知识库目录可用，只读贯穿完成（未写入任何文件）
- 抽样 8 个 symbol 的 KB-020 聚合全部正常返回
- TA/TradeFlow/IC 契约核验通过：证据树无 decision/action_label/buy_level 等动作字段

## 阻塞项 / Findings

- `中际旭创`：frontmatter 条目无 6 位代码（仅名称），无法聚合查询（知识库侧数据质量问题，建议 Tree Work 补代码字段）
- 7/8 个抽样 symbol 的 citation 审计降级（半年报事实缺机读数据或状态不可用），审计链路本身按契约 fail-closed，无契约违例
- 3 个抽样 symbol 的半年报页面存在但 data_status=missing（页面缺机读 financial_facts），建议知识库侧补事实表

## 下一阶段建议

- 冲突/待验证条目按 KB-017 口径进入 Tree Work 复核队列
- 过期页建议安排知识库侧刷新（本任务只读，未修改）
- 统一 review 时以本报告与各任务运行档案为材料
