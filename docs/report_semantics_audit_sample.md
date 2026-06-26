<!-- 自动生成示例：REPORT-UX-002 历史报告动作语义与数据缺口只读迁移预检。
     本文件由 scripts/audit_report_semantics.py 在内存 fixture 上生成，
     仅用于展示 audit 输出形态，不代表真实生产数据。 -->

# 历史报告动作语义与数据缺口迁移预检（dry-run）

- 生成时间 (as_of): `2026-06-26T21:30:00+00:00`
- 扫描报告数: **5**
- 无缺口报告数: **1**
- 模式: **只读预检（dry-run）**，未写入任何数据库

## 缺口统计

| 缺口类型 | 数量 | 说明 |
| --- | ---: | --- |
| 动作语义缺失 (research_direction / execution_action / action_label) | 3 | DECISION-001 3 层语义 |
| data_blockers 缺失 | 3 | DATA-021 字段级降级 |
| raw_evidence 缺失 | 2 | DATA-004 来源契约 |

## 建议动作分布

| 建议 | 数量 | 含义 |
| --- | ---: | --- |
| `can_derive` (可读时补算) | 2 | 缺失字段可在只读副本上补算，后续可走独立 backfill 写回 |
| `needs_rerun` (需要重跑) | 1 | 至少一项缺口无法从现有文本补回，建议重跑该报告 |
| `cannot_judge` (无法判断) | 1 | result_data 与 final_trade_decision 均缺失 |
| `ok` (无缺口) | 1 | 3 层语义 + data_blockers 齐全 |
| `audit_error` (扫描异常) | 0 | 单行扫描抛错，已跳过 |

## 备注

- scanned 5 completed report(s); 4 have at least one gap
- 1 report(s) need a rerun
- 1 report(s) cannot be judged

## 报告明细（仅列出有缺口的报告）

| report_id | symbol | trade_date | 缺口 | 建议 | 当前语义 | 补算后语义 |
| --- | --- | --- | --- | --- | --- | --- |
| `r-sem` | 603629.SH | 2026-05-12 | 语义 | `can_derive` | —/—/— | 偏多/HOLD/持有 |
| `r-blk` | 300750.SZ | 2026-05-11 | blockers | `can_derive` | 看多/ENTER/条件入场 | （不适用） |
| `r-run` | 000001.SZ | 2026-05-09 | 语义, blockers, raw_ev | `needs_rerun` | —/—/— | —/—/— |
| `r-unk` | 000002.SZ | 2026-05-08 | 语义, blockers, raw_ev | `cannot_judge` | —/—/— | —/—/— |

> 说明：`补算后语义` 列展示的是**只读 dry-run** 的推导结果，并未写回数据库。`can_derive` 报告可通过后续独立 backfill 任务落地。