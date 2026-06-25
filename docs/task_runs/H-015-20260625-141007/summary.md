# H-015 昊天主题日报与候选入池/出池解释

- **任务**：H-015
- **状态**：done
- **执行者**：Codex
- **时间**：2026-06-25 14:10

## 变更摘要

- 新增 `tradingagents/tradeflow/mandate_daily_report.py`
  - 从 H-013 topic heatmap 生成昊天主题日报。
  - 输出升温主题、降温主题、主候选、观察候选、入池原因、出池原因、证据缺口。
  - 支持 Markdown 渲染和 latest JSON/Markdown 保存读取。
- 新增 `api.services.tradeflow_service.get_mandate_daily_report()`
  - 优先读取 latest 报告；没有文件时从 heatmap 即时生成只读预览。
- 新增 API
  - `GET /v1/tradeflow/mandate-daily-report/latest`
- 前端 TradeFlow 主题热度 tab 新增“昊天主题日报”卡片。
- 新增 `tests/test_h015_mandate_daily_report.py`。

## 验证

- `python -m py_compile tradingagents/tradeflow/mandate_daily_report.py api/services/tradeflow_service.py api/tradeflow_schemas.py api/main.py`：通过。
- `pytest tests/test_h015_mandate_daily_report.py tests/test_h013_topic_heatmap.py -q`：112 passed。
- `npm run build`：通过。

## 约束

- 未调用 LLM。
- 未修改 prompts。
- 未写生产数据库。
- 日报只做雷达摘要，不输出交易指令。
