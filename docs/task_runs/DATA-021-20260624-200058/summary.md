# DATA-021 接管完成摘要

- **任务**：TA 报告数据源失败原因透传与字段级降级说明
- **状态**：done
- **执行者**：Codex
- **说明**：该 run 由夜间自动开发链领取，但目录内只有 claim/pre 文件，没有 OpenCode 产出；2026-06-25 由 Codex 直接接管开发并完成。

## 变更摘要

- `readiness_score.py` 新增 `build_data_blockers()` / `summarize_data_blockers()`。
- `report_service.py` 在报告保存时写入 `result_data.data_blockers` 与 `data_blocker_summary`。
- `api/main.py` 报告响应透传数据缺口字段。
- `Reports.tsx` 报告详情页新增“数据缺口”面板。
- `types/index.ts` 增加 `DataBlocker` / `DataBlockerSummary` 类型。
- 新增 `tests/test_data021_report_data_blockers.py`。

## 验证

- `pytest tests/test_data021_report_data_blockers.py tests/test_readiness_score.py tests/test_decision_semantics.py -q`：154 passed。
- `python -m py_compile tradingagents/agents/utils/readiness_score.py api/services/report_service.py api/main.py`：通过。
- `pytest tests/test_report_recovery.py tests/test_data021_report_data_blockers.py -q`：9 passed。
- `npm run build`：通过。

## 风险说明

- 本任务只增加解释性 metadata，不改 `action_label`、买卖等级或强动作门禁。
- 未调用 live API、未修改 prompts、未写生产数据库。
