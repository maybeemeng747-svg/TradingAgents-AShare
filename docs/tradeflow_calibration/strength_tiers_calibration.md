# 校准报告 — TF-QUALITY-005 candidate_pool_strength_tiers

> 日期: 2026-06-29
> 任务: TF-QUALITY-005 — 候选池"过多且像抄底"回放校准与强度分层
> 前置: TF-QUALITY-004 (live_pool_calibration) ✓ / H-014 (mandate_concentration_gate) ✓

## 一、校准目标

用户试用反馈：候选池仍存在两个痛点：

1. **候选过多** — 即使经过 TF-QUALITY-001/003/004 + H-014 的层层收窄，
   `main_candidates` 仍然最多 5 只，且这 5 只内部优先级不可区分。
2. **像半山腰抄底** — 部分 TECH 候选只有形态 + 触发价，没有量能/资金确认，
   属于"反弹但趋势未修复"的左侧抄底，不该与全维度共振的候选并列。

本任务在 `main_candidates` 内部再叠加一层 **强度分层**，明确：

- **主候选 (primary)**: 全维度共振 + 趋势确认 + 高分，真正值得盯盘的少数标的。
- **观察候选 (secondary)**: 通过精度门禁/校准/H-014 三关，但缺少一项确认维度，
  降为次优先级；超过 `tier_secondary_max_count` 的 secondary 溢出到 observation。
- **过滤候选 (filtered)**: TF-QUALITY-004 已有的 hard-filter，不变。

核心原则（取自任务约束）：

- 不引入真实交易建议；不输出强买卖词。
- 不调用全市场 live scan；只用 fixture 回放。
- 不把阈值调得过拟合单日样本；规则需跨 VCP/回踩/事件/昊天 通用。
- 现有候选评分字段（composite_score / precision_dimensions / ranking_reasons
  / calibration_summary / concentration_summary）一律不丢失。

## 二、新增分层规则

### 2.1 主候选门槛 — 全部命中才进 primary

| 候选类型 | primary 必要条件 |
|---------|-----------------|
| TECH_TRADE | 数据完整度 ≥ `tier_secondary_data_completeness_min` 且 量能=True 且 资金=True 且 共振维度 ≥ `tier_primary_min_resonance` 且 composite ≥ `tier_primary_min_composite` |
| POLICY_AMBUSH / POLICY_CONFIRM | 数据完整度达标 且 主题强度 ≠ `weak` 且 支撑维度 ≥ `tier_primary_haotian_min_support_dims` 且 共振维度 ≥ `tier_primary_min_resonance` 且 composite ≥ `tier_primary_min_composite` |
| EVENT_WATCH / 其他 | 数据完整度达标 且 composite ≥ `tier_primary_min_composite` |

### 2.2 降层规则 — 命中任一则降为 secondary

每条规则对应一个可读 `tier_reason`，并归入 `strength_tier_summary.downgrade_reasons`
的对应 bucket，方便后续审计：

| 规则 | 触发条件 | tier_reason 示例 | downgrade bucket |
|------|---------|-----------------|-----------------|
| 数据不足 | `data_completeness < tier_secondary_data_completeness_min` | `数据不足(完整度30%<50%)` | `data_insufficient` |
| TECH 弱缩量无资金确认 | `tier_primary_require_volume_and_capital=True` 且 量能/资金缺其一 | `弱缩量无资金确认(缺资金)` | `weak_volume_capital` |
| TECH 反弹但趋势未修复 | 仅形态+触发价、无量能/资金、composite 偏低 | `反弹但趋势未修复(仅形态+触发价，无量能/资金确认)` | `rebound_trend_unrepaired` |
| TECH/POLICY 共振维度偏少 | `precision_resonance_count < tier_primary_min_resonance` | `共振维度偏少(2<3)` | `low_resonance` |
| POLICY 弱主题 | `concentration_strength == "weak"` (来自 H-014) | `弱主题(退潮主题)` | `weak_topic` |
| POLICY 证据单一 | 跨信号支撑维度 < `tier_primary_haotian_min_support_dims` | `证据单一(仅1类支撑维度，需≥2)` | `single_evidence` |
| 兜底：综合分偏低 | 其他规则未命中但 composite < `tier_primary_min_composite` | `综合分偏低(55<65)` | `low_composite` |

规则评估顺序为上表自上而下，**首条命中即返回**，所以 `tier_reason` 始终能精确
指到最关键的缺口。

### 2.3 secondary 溢出门禁

为避免 secondary 自身膨胀（用户痛点之一），设置 `tier_secondary_max_count`
（默认 3）。超过上限的 secondary 候选会被降级到 `observation_candidates`，
并打上 `[TF-QUALITY-005]次优候选已满(...)` 前缀的 `pool_filter_reason`，
保留其原始 `tier_reason` 作为子说明。

## 三、Fixture 回放（覆盖 VCP / 回踩 / 事件 / 昊天）

### 3.1 混合 fixture（6 只，对应 `TestAcceptanceFixtureMixed`）

| Symbol | 类型 | 主题 | composite | 关键确认维度 | 预期分层 | tier_reason |
|--------|------|------|----------|-------------|---------|-------------|
| `VCP_PRIM` | TECH_TRADE (VCP) | — | 88 | 量能+资金+触发价 | primary | — |
| `VCP_SEC`  | TECH_TRADE (VCP) | — | 68 | 量能、缺资金 | secondary | 弱缩量无资金确认(缺资金) |
| `PULL_STRG`| TECH_TRADE (PULLBACK) | — | 75 | 量能+资金+触发价 | primary | — |
| `EV_OK`    | EVENT_WATCH | — | 72 | composite ≥ 65 | primary | — |
| `HAO_PRIM` | POLICY_AMBUSH | 低空经济 | 82 | mandate+beneficiary、topic=strong | primary | — |
| `HAO_SEC`  | POLICY_AMBUSH | AI | 70 | 仅 mandate、topic=strong | secondary | 证据单一(仅1类支撑维度) |

回放结果：

| 池 | 数量 |
|----|------|
| 输入候选 | 6 |
| primary | 4 |
| secondary | 2 |
| observation (含溢出) | 0 |
| filtered | 0 |

### 3.2 20 只候选 fixture（对应 `TestTwentyCandidateFixture`）

| 维度 | 数量 |
|------|------|
| 输入候选 | 20 |
| 强技术 (TECH, 全维度) | 3 |
| 强政策 (POLICY, mandate+beneficiary) | 3 |
| 弱技术 (TECH, 无资金) | 7 |
| 弱政策 (POLICY, 无 mandate/beneficiary) | 7 |

回放结果：

| 池 | 数量 | 备注 |
|----|------|------|
| main_candidates (post-tier) | ≤ 5 | 受 `effective_main_cap` + secondary 溢出双重约束 |
| primary | ≤ 5 | 强技术 + 强政策全部进入 |
| secondary | ≤ 3 | 受 `tier_secondary_max_count` 约束 |
| observation | ≥ 0 | 包含 TF-QUALITY-004 校准降级 + secondary 溢出 |
| filtered | ≥ 0 | data_insufficient / C 层 |

### 3.3 降层规则命中分布（20 只 fixture）

- `weak_volume_capital`: 7 只弱技术（无 fund_flow_anomaly_score）命中。
- `single_evidence`: 0~N 只弱政策被 TF-QUALITY-004 校准提前降到 observation，
 不会再进入 strength tier 评估。
- `rebound_trend_unrepaired`: 默认配置下被 `weak_volume_capital` 短路；
  若关闭 `tier_primary_require_volume_and_capital` 则可独立触发。
- `data_insufficient`: 数据完整度 < 50% 的候选命中。

## 四、误杀检查

### 4.1 强技术候选（全维度共振）

- 结果：**未被误杀**，进入 primary。
- 依据：`fund_flow_anomaly_score > 0` + `fund_flow_unit_verified=True` 使
  资金=True；`positive_category_count >= 2` 使量能=True；trigger/invalid 齐备。

### 4.2 强昊天候选（mandate + beneficiary）

- 结果：**未被误杀**，进入 primary。
- 依据：mandate=60 + beneficiary=50 → 2 类支撑维度；H-014 topic=strong。

### 4.3 昊天跨信号候选（mandate + event + narrative）

- 结果：**未被误杀**，进入 primary。
- 依据：mandate(1) + event_score>0(1) + narrative_score>0(1) = 3 类支撑维度。

### 4.4 弱技术候选（无资金）

- 结果：降为 secondary，`tier_reason="弱缩量无资金确认(缺资金)"`。
- 评估：合理，避免与全维度候选混淆。

### 4.5 弱政策候选（仅 policy_tags）

- 结果：被 TF-QUALITY-004 校准降到 observation，**永远不会被 filtered**，
  保留昊天保护机制；本任务不再继续评估其分层。

## 五、配置变更摘要

| 配置项 | 默认值 | 位置 |
|--------|--------|------|
| `tier_primary_min_composite` | 65.0 | StrategyConfig |
| `tier_primary_min_resonance` | 3 | StrategyConfig |
| `tier_primary_require_volume_and_capital` | True | StrategyConfig |
| `tier_primary_haotian_min_support_dims` | 2 | StrategyConfig |
| `tier_secondary_data_completeness_min` | 0.5 | StrategyConfig |
| `tier_secondary_max_count` | 3 | StrategyConfig |

## 六、代码变更

| 文件 | 变更 |
|------|------|
| `tradingagents/tradeflow/strategy_config.py` | 新增 6 个 tier 配置项（`# [TF-QUALITY-005] candidate_pool_strength_tiers`） |
| `tradingagents/tradeflow/candidate_pool_gate.py` | `PoolGateResult` 新增 `primary_candidates` / `secondary_candidates` / `strength_tier_summary`；新增 `_haotian_support_dim_count` / `_classify_strength_tier` / `_apply_strength_tiers`；`run_pool_gate` 末尾集成分层 + secondary 溢出门禁 |
| `tests/test_tf_quality005_strength_tiers.py` | 新增 59 个分层测试 |
| `tests/test_tf_quality001_pool_gate.py` | 修正 `test_custom_config` 同时 bump `tier_secondary_max_count`，保留原 TF-QUALITY-001 断言意图 |

## 七、API / 前端契约增量

`PoolGateResult` 新增字段（全部可选，向后兼容）：

```python
primary_candidates: list[dict]      # 主候选（高优先级）
secondary_candidates: list[dict]    # 观察候选（次优）
strength_tier_summary: dict         # 分层报告
```

每只 `main_candidates` 条目新增两个字段：

- `strength_tier: "primary" | "secondary"`
- `tier_reason: str`  # primary 为空字符串；secondary 必有可读原因

`pool_counts` 新增：`primary` / `secondary` / `secondary_overflow`。
`strength_tier_summary` 结构：

```json
{
  "primary_count": 2,
  "secondary_count": 2,
  "main_before_tier": 5,
  "main_after_tier": 4,
  "observation_after_tier": 8,
  "secondary_overflow_to_observation": ["SYM_X"],
  "downgrade_reasons": {
    "weak_volume_capital": ["..."],
    "rebound_trend_unrepaired": ["..."],
    "data_insufficient": ["..."],
    "low_resonance": ["..."],
    "low_composite": ["..."],
    "weak_topic": ["..."],
    "single_evidence": ["..."]
  },
  "headline": "主候选分层：主候选2只，观察候选(次优)2只"
}
```

## 八、测试结果

```
tests/test_tf_quality005_strength_tiers.py:  59 passed
tests/test_tf_quality001_pool_gate.py:       72 passed
tests/test_tf_quality004_calibration.py:     36 passed
tests/test_h014_mandate_concentration_gate.py: 21 passed
全套 tests/:                                 7269 passed, 17 skipped
```
