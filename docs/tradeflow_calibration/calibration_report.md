# 校准报告 — TF-QUALITY-004 live_pool_calibration

> 日期: 2026-06-16
> 任务: TF-QUALITY-004 — 候选池实盘区分度回放校准

## 一、校准目标

在 TF-QUALITY-001~003 基础上进一步收窄候选池，解决"主候选过多、分数差异不明显、像半山腰抄底"的问题。

核心原则：
- 技术做 T 与昊天左侧必须分池校准，不能用一刀切高阈值误杀昊天左侧候选。
- 每只主候选必须有明确"为什么值得盯"的证据。
- 主候选默认不超过 5 只，技术池不超过 3 只，昊天池不超过 3 只。

## 二、新增校准规则

### 2.1 技术池 — 弱 VCP 降级 (`weak_vcp_downgraded`)

**规则**: TECH_TRADE 候选，若精度门禁维度中 `量能=False` 且 `资金=False`，降级到 observation。

**原因**: 只有形态 + 触发价但无量能确认、无资金流验证的 VCP，属于"半山腰抄底"风险。

**配置**: 内置于 `_apply_live_calibration`，无独立阈值。

### 2.2 技术池 — 数据不足过滤 (`data_insufficient_filtered`)

**规则**: TECH_TRADE 候选，若 `data_quality_score < 50` 且 `data_completeness < 0.5`，降级到 filtered。

**配置**: `calibration_tech_data_quality_min = 50.0`

**原因**: 数据不足的技术候选无法支撑短线交易决策。

### 2.3 昊天左侧 — 证据不足降级 (`haotian_evidence_insufficient`)

**规则**: POLICY_AMBUSH / POLICY_CONFIRM 候选，必须满足：
1. `policy_score > 0`（有政策主题分）
2. 至少 2 个支撑维度（event / fund_flow / narrative / beneficiary / mandate）

不满足则降级到 observation（**永远不会被 filtered**，保留昊天保护机制）。

**配置**: `calibration_haotian_min_support_dims = 2`

**原因**: 昊天左侧候选不要求短线突破，但必须有政策主题 + 多维交叉验证，避免仅凭单一政策标签入选。

### 2.4 分项评分差异不足 — 主候选上限缩减 (`score_spread_reduced`)

**规则**: 当候选池中 top N（N = `pool_main_max`）候选的综合分差 < 5 分时，主候选上限自动缩减 2（floor = 2）。

**配置**: `calibration_score_spread_min = 5.0`, `calibration_main_cap_reduction = 2`

**触发条件**: `pool_main_max >= 4` 且候选数 > `pool_main_max`。

**原因**: 当多个候选分数接近时（"平堆"），无法区分优劣，缩减主候选数量避免噪音。

## 三、候选池分布（回放测试）

### 3.1 20 只混合候选回放

| 维度 | 数量 |
|------|------|
| 输入候选 | 20 |
| 强技术 (TECH_TRADE, 全维度) | 3 |
| 强政策 (POLICY_AMBUSH, mandate+beneficiary) | 3 |
| 弱技术 (TECH_TRADE, 仅形态) | 7 |
| 弱政策 (POLICY_AMBUSH, 无证据) | 7 |

### 3.2 回放结果

| 池 | 预期 | 实际 |
|----|------|------|
| 主候选 | ≤ 5 | ≤ 5 ✓ |
| 技术池 main | ≤ 3 | ≤ 3 ✓ |
| 昊天池 main | ≤ 3 | ≤ 3 ✓ |
| 弱技术 | → observation/filtered | ✓ |
| 弱政策 | → observation (非 filtered) | ✓ |

### 3.3 降级规则命中

- **弱 VCP 降级**: 弱技术候选（仅形态，无量能/资金）被降级到 observation。
- **数据不足过滤**: data_completeness < 0.5 的技术候选被过滤。
- **昊天证据不足**: 无 mandate/beneficiary/event/fund_flow/narrative 的政策候选降级到 observation。
- **分数平堆缩减**: 当 top 候选分差 < 5 时，主候选上限从 5 缩减到 3。

## 四、误杀检查

### 4.1 强技术候选（全维度共振）

- 结果：**未被误杀**，正常进入 main。
- 依据：positive_category_count >= 2 使量能=True，或有 fund_flow_anomaly_score > 0 使资金=True。

### 4.2 强昊天候选（mandate + beneficiary）

- 结果：**未被误杀**，正常进入 main。
- 依据：mandate_score_component > 0 + beneficiary_path 非空 = 2 个支撑维度。

### 4.3 昊天跨信号候选（policy + event + narrative）

- 结果：**未被误杀**，正常进入 main。
- 依据：mandate(1) + event_score > 0(1) = 2 个支撑维度。

### 4.4 弱政策候选（仅 policy_tags）

- 结果：降级到 observation（**非 filtered**），保留昊天保护。
- 评估：合理，因为单一政策标签不足以支撑主候选。

## 五、配置变更摘要

| 配置项 | 默认值 | 位置 |
|--------|--------|------|
| `calibration_score_spread_min` | 5.0 | StrategyConfig |
| `calibration_main_cap_reduction` | 2 | StrategyConfig |
| `calibration_tech_data_quality_min` | 50.0 | StrategyConfig |
| `calibration_haotian_min_support_dims` | 2 | StrategyConfig |

## 六、代码变更

| 文件 | 变更 |
|------|------|
| `tradingagents/tradeflow/strategy_config.py` | 新增 4 个校准配置项 |
| `tradingagents/tradeflow/candidate_pool_gate.py` | 新增 `_compute_effective_main_cap` / `_apply_live_calibration`，集成到 `run_pool_gate` |
| `tests/test_tf_quality004_calibration.py` | 新增 35 个校准测试 |
| `tests/test_tf_quality001_pool_gate.py` | 修正 4 个测试的 POLICY 候选数据（补充 mandate/beneficiary） |
| `tests/test_v007_tradeflow_trial_e2e.py` | TECH 候选补充 fund_flow 确认数据 |

## 七、测试结果

```
tests/test_tf_quality004_calibration.py: 35 passed
tests/test_tf_quality001_pool_gate.py:    72 passed
tests/test_v007_tradeflow_trial_e2e.py:   50 passed
合计: 157 passed, 0 failed
```
