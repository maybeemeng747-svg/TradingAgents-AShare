You are working on TradingAgents-AShare project.

## Task: UI-006 — 数据源健康前端面板（P2）

Read the full task description from: docs/task_runs/ui006-task.md

Key context:
- The project uses React + TypeScript + Vite
- TradeFlow page is at frontend/src/pages/TradeFlow.tsx
- API client is in frontend/src/services/api.ts (has getTradeFlowDataHealth method)
- Types are in frontend/src/types/index.ts (has TradeflowDataHealthItem)
- API endpoint: GET /v1/tradeflow/data-health
- Response model: api/tradeflow_schemas.py TradeflowDataHealthItem

Implementation steps:
1. Add a "数据健康" (Data Health) tab to the TradeFlow page (alongside existing tabs: candidates, observe, ta-queue, review)
2. Create a DataHealthPanel component that:
   - Fetches data from /v1/tradeflow/data-health
   - Shows a health summary banner at the top (all ok / some degraded / failed / no data)
   - Lists each data source with: name, status badge (colored), fallback vendor, last updated, error (if failed), record count
   - Status colors: OK=green, PARTIAL=yellow, FAILED=red, STALE=orange, NOT_QUERIED=gray
   - Empty state: "未生成健康检查"
3. Add a lightweight health indicator in the summary cards area (near the top of TradeFlow page)
4. No strong buy/sell words

After implementation, run: cd frontend && npm run build
Fix any TypeScript/build errors. Max 2 fix rounds.

When done, print: "UI-006 COMPLETE"
