You are working on TradingAgents-AShare project.

## Task: UI-005 — 盘后 Review 前端页面

Read the full task from: docs/task_runs/ui005-task.md

Key context:
- TradeFlow page exists at frontend/src/pages/TradeFlow.tsx (already has 3 tabs: 候选池, 盘中观察, TA 队列)
- Types: TradeFlowReviewResponse, TradeFlowReviewItem in frontend/src/types/index.ts
- API: getTradeFlowReview(date) in frontend/src/services/api.ts
- UI uses Tailwind CSS + lucide-react

Implementation:
1. Add 4th tab "盘后 Review" to TradeFlow.tsx
2. Implement Review tab with:
   - Summary cards (total candidates, hit rate, miss rate, avg returns)
   - Strategy stats table
   - Tier stats table
   - Removal reasons list
   - Suggestions list
3. 0% must display as "0.0%", not "N/A%"

After implementation: cd frontend && npm run build
Fix errors. Max 2 rounds.

When done, print: "UI-005 COMPLETE"
