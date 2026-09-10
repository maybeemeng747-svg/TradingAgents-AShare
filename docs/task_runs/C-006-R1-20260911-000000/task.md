# C-006-R1 运行档案 — 财务质量指标生产接线补修

- **任务卡**：docs/TASKS.md `### C-006-R1: 财务质量指标生产接线补修（P2）`
- **开始时间**：2026-09-11（本地，连续模式第 3 项）
- **基线 HEAD**：`7df555b`（C-005-R1 实现提交）
- **批次说明**：按用户 2026-09-10 破例指示，本批次 review 统一安排；
  done = 代码已提交 + 任务指定测试通过。

## 任务卡要点

- **描述**：Phase 2 指标由真实 normalizer 产出；负利润时现金流质量比不得产生误导性正向结论。
- **允许修改**：`tradingagents/agents/utils/financial_validator.py`、实际财务 normalizer 与调用接线、对应测试、任务档案。
- **验收**：`pytest tests/test_c006_financial_validator.py -q`；从生产 normalizer 驱动测试，覆盖双负值、零利润、缺现金流、跨期/单位不一致；双负比值不得解释成盈利质量良好。API/报告消费结果须保留状态及口径。

## 复现记录（修复前，基线 7df555b）

经生产管线 `extract_financial_anomaly_inputs(period_facts)` →
`check_financial_anomalies(**inputs)` 复现：

| 场景 | 修复前行为 | 问题 |
|---|---|---|
| 双负值（利润-5000万/现金流-8000万） | 比值 1.6 落在"健康"区间，`has_anomaly=False`，无任何状态 | 误导性正向结论 |
| 零利润 | 静默跳过（`net_profit != 0` 守卫），结果无状态 | 口径缺失 |
| 缺现金流 | 静默跳过，结果无状态 | 口径缺失 |
| 亏损+正现金流（-5/3） | 判 `negative_cashflow_quality`："利润未转化为实际现金流入" | 利润本是负的，文本误导 |

生产接线本身（`fundamental_integrity.extract_financial_anomaly_inputs`）在
FUND-004B-R1 已按 (report_date, period_scope, unit) 分组 fail-closed，
复现确认跨单位不混组——"Phase 2 指标由真实 normalizer 产出"已满足，
缺口只在验证器的状态语义。

## 设计与修改文件

仅改 `tradingagents/agents/utils/financial_validator.py` 规则 7：

1. 比值只在净利润>0 时评估；亏损期拆成两个显式状态：
   - 双负 → 新增异常 `loss_with_cash_burn`（needs_manual_review=True），
     文本明确"比值无经济意义，不得解读为盈利质量良好"；
   - 亏损+正现金流（高折旧等）→ 非异常，状态
     `loss_with_positive_cashflow`，不再输出误导文本。
2. 零利润 → `profit_near_zero_ratio_not_applicable`；缺现金流/缺利润/
   双缺 → `missing_operating_cashflow` / `missing_net_profit` /
   `missing_both`。
3. 返回契约新增 `cashflow_quality_status` 字段（7 种状态，已写入
   docstring），供 API/报告消费方区分"评估通过"与"缺数据跳过"。
4. 生产 normalizer（`extract_financial_anomaly_inputs`）未修改：
   跨期/跨单位 fail-closed 行为经测试确认已正确（同组契约下
   缺现金流经管线表现为 `missing_both`，属预期 fail-closed 语义）。

## 测试结果

| 命令 | 结果 | 退出码 |
|---|---|---:|
| `pytest tests/test_c006_financial_validator.py -q`（验收） | 79 passed（71 存量 + 8 新增对抗） | 0 |
| `pytest tests/test_fund003_fund004_integrity.py tests/test_fund004a_integrity_gate.py tests/test_fund002_financial_periods.py tests/test_c005_delta_check.py tests/test_financial_fact_bundle.py -q` | 258 passed | 0 |
| `pytest tests/test_api_smoke.py -q` | 87 passed | 0 |

新增 8 项对抗测试均从生产 normalizer 管线驱动（`_np_cf_facts` 事实 →
`extract_financial_anomaly_inputs` → `check_financial_anomalies`），
覆盖双负、零利润、缺现金流（管线 fail-closed + 直调细粒度状态）、
跨单位不混组、亏损+正现金流无误导文本、正常盈利企业回归。

## Codex review

按用户 2026-09-10 指示统一安排，本任务不单独 review；本档案即
统一 review 材料。
