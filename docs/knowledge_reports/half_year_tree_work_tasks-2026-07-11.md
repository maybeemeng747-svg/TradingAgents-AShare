# 半年报资料优先队列与 Tree Work 补录任务包 — 2026-07-11

> [HY-002] half_year_task_pack — 基于持仓 / 观察仓 / 候选 / 研报关注度，只读生成半年报补录优先队列；**不修改知识库、不复制研报原文、不输出交易建议**。

## 1. 概览

- knowledge_root: `/var/folders/r9/2qykv_gx0_9fs2jb0zkccf8w0000gn/T/tmp0ttuqw1l`
- generated_at: 2026-07-11 20:11:22
- as_of_date: 2026-07-11
- contract_version: `hy-002-v1`
- 任务总数: **4**

## 2. 输入统计

| 输入来源 | 数量 |
|----------|------|
| 持仓 | 1 |
| 观察仓 | 1 |
| 昊天候选 | 1 |
| TradeFlow 候选 | 1 |

## 3. 上游信号摘要

- **HY-001 lint**：6 页 / 半年报页 5 （缺报告期 1 / 缺事实 0 / 观点冒充事实 0）

## 4. 分层统计

| 层级 | 数量 | 说明 |
|------|------|------|
| `P1_HOLDINGS` | 1 | 已持仓（半年报补录最高优先） |
| `P2_OBSERVATION` | 1 | 观察仓（半年报补录次优先） |
| `P3_HAOTIAN` | 1 | 昊天主候选 |
| `P4_TRADEFLOW` | 1 | TradeFlow 主候选 |
| `P5_STALE_ATTENTION` | 0 | 研报关注度高但知识过期 |

## 5. 补录任务（按优先级分层）

### 已持仓（半年报补录最高优先）（1）

| symbol | name | 动作 | 缺失字段 | 已有页 | 原因 | 来源 |
|--------|------|------|----------|--------|------|------|
| `603296` | 华勤技术 | `add_fields` | financial_period | `wiki/investment/华勤技术603296-缺报告期半年报.md` | 已有半年报页 `wiki/investment/华勤技术603296-缺报告期半年报.md` 缺字段：financial_period；持仓标的，半年报补录最高优先 | `holdings` |

### 观察仓（半年报补录次优先）（1）

| symbol | name | 动作 | 缺失字段 | 已有页 | 原因 | 来源 |
|--------|------|------|----------|--------|------|------|
| `000977` | 浪潮信息 | `add_fields` | - | `wiki/investment/浪潮信息000977-2025H1半年报.md` | 已有半年报页 `wiki/investment/浪潮信息000977-2025H1半年报.md` 字段齐全，建议复核确认时效；观察仓标的，半年报补录次优先 | `observation_warehouse` |

### 昊天主候选（1）

| symbol | name | 动作 | 缺失字段 | 已有页 | 原因 | 来源 |
|--------|------|------|----------|--------|------|------|
| `002415` | 海康威视 | `add_fields` | - | `wiki/investment/海康威视002415-2025H1半年报-事实观点混用.md` | 已有半年报页 `wiki/investment/海康威视002415-2025H1半年报-事实观点混用.md` 字段齐全，建议复核确认时效；昊天主候选 | `haotian_candidate` |

### TradeFlow 主候选（1）

| symbol | name | 动作 | 缺失字段 | 已有页 | 原因 | 来源 |
|--------|------|------|----------|--------|------|------|
| `300750` | 宁德时代 | `add_fields` | - | `wiki/investment/宁德时代300750-2025H1半年报-旧观点被削弱.md` | 已有半年报页 `wiki/investment/宁德时代300750-2025H1半年报-旧观点被削弱.md` 字段齐全，建议复核确认时效；TradeFlow 主候选 | `tradeflow_candidate` |

### 研报关注度高但知识过期（0）

_（无）_

## 6. 半年报 ingest 模板（HY-001 字段骨架）

新建或补字段时，按以下 frontmatter 填写；对应契约见 `docs/local_knowledge_contract.md` §10。

```markdown
---
title: <公司名>—<YYYYH1>半年报
created: 2026-07-11
updated: 2026-07-11
sources:
  - "[[../../raw/<公告文件名>.md|公司公告]]"
symbols: ["<6位代码>.SH <简称>"]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
# ── HY-001 半年报扩展字段 ──
financial_period: 2025H1
disclosure_date: <YYYY-MM-DD>
source_type: [exchange_filing, fact_table, management_commentary]
financial_facts:
  - 营收 <数值>亿 (+/-<%> YoY)
  - 归母净利 <数值>亿 (+/-<%> YoY)
segment_facts:
  - <分业务事实>
management_commentary:
  - <管理层表述/公司口径>
forward_guidance:
  - <公司指引>
risk_factors: [<风险1>, <风险2>]
source_links:
  - 巨潮资讯 <公告URL>
---

# <标题>

## 一句话总结

<1-2 句核心事实摘要>

## 投资逻辑

- <核心事实 1>

## 风险提示

- <风险 1>

## 原始资料

- [[../../raw/<原始资料>.md|<来源别名>]]
```

## 7. 建议执行顺序

1. **先补已持仓（P1_HOLDINGS）**：
   - 持仓标的的半年报事实直接影响 TA 报告和 investment-controller。
   - 已有页 → 补 HY-001 字段；无页 → 新建半年报页。
2. **再补观察仓（P2_OBSERVATION）**：
   - 观察仓标的接近关注区间，需提前消化半年报事实。
3. **昊天主候选（P3_HAOTIAN）**：
   - 左侧埋伏候选需半年报事实验证投资逻辑。
4. **TradeFlow 主候选（P4_TRADEFLOW）**：
   - 技术候选的半年报事实作为背景证据。
5. **研报关注度高但知识过期（P5_STALE_ATTENTION）**：
   - 关注度高但页面过期/缺事实，优先复核或补事实。

> 每条任务完成后，在对应 wiki 页 frontmatter 更新 `updated`；后续运行 `scripts/half_year_task_pack.py` 会自动剔除已完成项。

## 8. 免责声明

- 本任务包只提供 Tree Work 半年报 ingest 字段要求与优先级，**不构成任何买卖建议或强动作词**。
- 所有任务来源可追溯到 HY-001 lint / KB-007 关注度信号；执行后可重跑对应 CLI 验证。
- 任务包不含研报原文段落，仅引用 symbol / 字段缺口 / 简短原因。

---

_由 `scripts/half_year_task_pack.py` 只读生成；对应模块 `tradingagents.dataflows.half_year_task_pack`。_
