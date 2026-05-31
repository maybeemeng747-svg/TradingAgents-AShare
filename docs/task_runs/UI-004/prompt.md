You are working on TradingAgents-AShare project.

## Task: UI-004 — 盘中 Observe 与 TA 队列只读面板

Read the full task from: docs/task_runs/ui004-task.md

Key context:
- TradeFlow page exists at frontend/src/pages/TradeFlow.tsx
- Types: TradeFlowObserveItem, TradeFlowObserveResponse, TradeFlowTAQueueItem, TradeFlowTAQueueResponse in frontend/src/types/index.ts
- API: getTradeFlowObserve(date), getTradeFlowTaQueue(date) in frontend/src/services/api.ts
- UI uses Tailwind CSS + lucide-react

Implementation:
1. Add Tab switcher at the top of TradeFlow.tsx (3 tabs: 候选池, 盘中观察, TA 队列)
2. Implement Observe tab with table showing observe items
3. Implement TA Queue tab with table showing queue items
4. Use existing API methods for data fetching

After implementation: cd frontend && npm run build
Fix errors. Max 2 rounds.

When done, print: "UI-004 COMPLETE"
