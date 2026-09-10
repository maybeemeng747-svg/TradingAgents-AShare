# HY-009-R1 运行档案 — 半年报增量缓存与冲突审计补修

- **任务卡**：docs/TASKS.md `### HY-009-R1: 半年报增量缓存与冲突审计补修（P2）`
- **开始时间**：2026-09-11（本地，连续模式第 4 项）
- **基线 HEAD**：`e3fd5b8`（C-006-R1 实现提交）
- **批次说明**：按用户 2026-09-10 破例指示，本批次 review 统一安排。

## 任务卡要点

- **描述**：expiry-only 变化也使缓存失效；冲突检查不得受 max_pages 截断；现金流冲突进入不可用状态。
- **允许修改**：`tradingagents/dataflows/half_year_incremental_refresh.py`、直接调用的本地知识缓存/事实审计工具、对应测试、任务档案。
- **验收**：`pytest tests/test_hy009_half_year_incremental_refresh.py -q`；覆盖仅有效期变化、冲突证据落在 max_pages 之后、经营现金流冲突、删除/修订/缓存损坏及重复执行。知识库只读，冲突不可被截断伪装 HAS_DATA。

## 复现记录（修复前，基线 e3fd5b8）

合成微型知识库（带经营现金流事实的半年报页）复现：

| 缺陷 | 构造 | 修复前行为 |
|---|---|---|
| ① expiry-only 不失效缓存 | valid_until=明天，建缓存后注入 today 推进 3 天，文件零改动 | `changes=[]`、`expired_count=0`、`reused_cache=True`——增量层完全无感知 |
| ② 冲突检查受 max_pages 截断 | A/B 同 2023H1 现金流冲突（15 vs 9），C 为 2025H1 新鲜页；max_pages=2 | `conflict_count=0`、`status=no_changes`、`data_status=fresh`——冲突证据（B 页）被排序截断丢弃，伪装无冲突 |
| ③ 现金流冲突不进不可用 | A/B 同期仅现金流不同 | provider `_CONFLICT_METRIC_KEYS` 无 `operating_cash_flow`，页保持 `fresh`；模块虽出 `FactConflictFlag` 但 `facts_result.data_status` 仍 `fresh`/HAS_DATA |

根因：
1. `_detect_page_changes` 的 sha1 完全一致快速路径直接 `continue`，时间驱动的 valid_until 过期无法被识别。
2. 主流程 `_detect_cross_version_conflicts(facts.pages)` 消费的是 provider **已按 max_pages 截断**的页列表（provider 自身的 `_detect_conflicts` 在截断前跑，但模块级跨版本结构化冲突输出没有同等保障）。
3. provider 冲突键缺 `operating_cash_flow`，现金流冲突页不标 conflict、聚合成 fresh。

## 设计与修改文件

- `tradingagents/dataflows/half_year_incremental_refresh.py`：
  1. sha1 一致快速路径增加 expiry-only 转移检测：缓存快照
     （`valid_until_expired`/`stale_risk_high`）未过期、按注入基准日期
     `_is_expired` 已过期 → 记 `CHANGE_EXPIRED`（缓存失效 + 审计可见）。
     内容一致则 frontmatter 一致，stale_risk 变化必然走内容变更路径，
     无需复查。
  2. 新增 `_CONFLICT_SCAN_MAX_PAGES`（10^9）：冲突扫描用不截断全量查询，
     展示结果仍按 `max_pages` 截断。
  3. 存在冲突时 `facts_result.data_status = DATA_CONFLICT`：查询级结果
     不得以 fresh/HAS_DATA 伪装（与 `_aggregate_result` 的
     HAS_DATA+conflict 组合口径一致）。
- `tradingagents/dataflows/half_year_facts_provider.py`：
  `_CONFLICT_METRIC_KEYS` 增补 `operating_cash_flow`（页级冲突 →
  conflict 不可用）。
- 幂等边界说明：expiry-only 检测依赖"缓存快照未过期→已过期"转移；
  生产真实时钟下重建后快照翻转为已过期，天然幂等。测试注入 `today`
  与真实构建时钟不一致时（仅测试场景），重复运行会持续报告过期——
  审计始终真实，不影响只读安全。

## 测试结果

| 命令 | 结果 | 退出码 |
|---|---|---:|
| `pytest tests/test_hy009_half_year_incremental_refresh.py -q`（验收） | 44 passed（37 存量 + 7 新增对抗） | 0 |
| `pytest tests/test_hy001..hy007 半年报域 7 文件 -q` | 399 passed | 0 |
| `pytest tests/test_hy008 + kb010 + kb013 + kb001 + kb003 + hy004/5/6/7 -q` | 524 passed | 0 |
| `git diff --check` | 干净 | 0 |

新增 7 项对抗覆盖：仅有效期变化检出、缓存失效+审计、未过期页幂等回归、
截断外冲突检出、现金流冲突页标 conflict、冲突结果不宣称 fresh、
provider 页级现金流冲突。

## Codex review

按用户 2026-09-10 指示统一安排，本任务不单独 review；本档案即
统一 review 材料。
