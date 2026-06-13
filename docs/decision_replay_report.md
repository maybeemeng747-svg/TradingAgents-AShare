# 最终动作语义端到端回放验收报告

> 任务：V-006 — 最终动作语义端到端回放验收（P1）
> 日期：2026-06-14
> 依赖：DECISION-001 ✓ / DECISION-002 ✓ / DECISION-003 ✓ / DECISION-004 ✓

---

## 1. 验收目标

DECISION-001~004 完成了动作语义 3 层拆分（`research_direction` / `execution_action` / `action_label`），禁止默认 HOLD。本验收用 5 个典型场景从信号文本到 DB 持久化到通知推送做端到端回放，验证系统不再"一片 HOLD/持有"，且 DB 轻量列、`result_data` JSON、Bark/企业微信推送 payload 三处一致。

## 2. 测试链路

```
signal text
  → _extract_decision_semantics(text, has_position, trigger_price)
  → resolve_report_fields(result_data, has_position)
  → result_data.update(semantics)          # 模拟 api/main.py 2175-2183
  → create_report(db, result_data)         # 写入 in-memory SQLite
  → get_report(db, report_id)              # 读回验证
  → build_report_payload(report)           # Bark
  → build_report_message(report)           # 企业微信
```

## 3. 五个回放场景

| # | 场景 | has_position | trigger_price | research_direction | execution_action | action_label |
|---|------|:---:|:---:|:---:|:---:|:---:|
| S1 | 未持仓偏多无触发价 | False | None | 偏多 | WAIT | 等待触发 |
| S2 | 未持仓看多有触发价 | False | 25.50 | 看多 | ENTER | 条件入场 |
| S3 | 未持仓偏空 | False | None | 偏空 | WAIT | 回避 |
| S4 | 已持仓中性 | True | None | 中性 | HOLD | 持有 |
| S5 | 已持仓偏空 | True | None | 偏空 | REDUCE | 条件减仓 |

## 4. 验收结果

### 4.1 语义层（`_extract_decision_semantics`）

5 个场景的 `research_direction` / `execution_action` / `action_label` 全部与预期一致。

### 4.2 DB 持久化层

| 检查项 | 结果 |
|--------|------|
| DB 轻量列 `research_direction` 与预期一致 | ✅ 5/5 |
| DB 轻量列 `execution_action` 与预期一致 | ✅ 5/5 |
| DB 轻量列 `action_label` 与预期一致 | ✅ 5/5 |
| `result_data` JSON 中 3 字段与预期一致 | ✅ 5/5 |
| DB 列与 `result_data` JSON 一致 | ✅ 5/5 |
| 保存后 position-aware 语义不丢失（DECISION-004 回归） | ✅ |

### 4.3 通知推送层

| 场景 | Bark title 含 action_label | 企业微信含 `动作：{label}` |
|------|:---:|:---:|
| S1 等待触发 | ✅ | ✅ |
| S2 条件入场 | ✅ | ✅ |
| S3 回避 | ✅ | ✅ |
| S4 持有 | ✅ | ✅ |
| S5 条件减仓 | ✅ | ✅ |

### 4.4 动作多样性（V-006 验收核心）

- **action_label 集合**：`{等待触发, 条件入场, 回避, 持有, 条件减仓}` — 5 个不同标签，不是一片"持有"。
- **execution_action 集合**：`{WAIT, ENTER, HOLD, REDUCE}` — 4 种不同动作码。

## 5. 测试覆盖

| 测试类 | 用例数 | 覆盖内容 |
|--------|:---:|----------|
| `TestDecisionSemanticsLayer` | 5 | 信号文本 → 3 层语义提取 |
| `TestReportPersistenceE2E` | 15 | DB 列 + result_data + 列/JSON 一致性 |
| `TestNotificationPayloadE2E` | 10 | Bark + 企业微信 payload 使用 action_label |
| `TestNotAllHold` | 2 | 标签多样性和动作码多样性 |
| `TestFullChainConsistency` | 1 | 5 场景全链路单测试 |
| **合计** | **33** | **全部通过** |

## 6. 前端 Report 类型一致性

前端 TypeScript `Report` 和 `AnalysisReport` 接口已包含 `research_direction` / `execution_action` / `action_label` 字段（DECISION-003 完成）。本验收验证的 DB 列和 `result_data` JSON 结构与前端接口完全对应，无需额外适配。

## 7. 结论

DECISION-001~004 的动作语义分层在端到端链路（信号 → DB → 通知）中工作正确：

1. 系统不再"一片 HOLD/持有" — 5 个场景产生 5 个不同的 `action_label`。
2. position-aware 语义在保存后不丢失。
3. Bark/企业微信推送均优先显示 `action_label`，而非 legacy `decision`。
4. DB 轻量列与 `result_data` JSON 保持一致。

---

*测试文件：`tests/test_v006_decision_e2e_replay.py`*
*代码标注：`# [V-006] decision_semantics_e2e`*
