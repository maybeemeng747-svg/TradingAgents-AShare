You are working on TradingAgents-AShare project.

## Task: UI-003 — 候选详情抽屉

Read the full task from: docs/task_runs/ui003-task.md

Key context:
- TradeFlow page exists at frontend/src/pages/TradeFlow.tsx
- Types are in frontend/src/types/index.ts (TradeFlowCandidateItem, TradeFlowCandidateDetail)
- API client is in frontend/src/services/api.ts (getTradeFlowCandidateDetail)
- UI uses Tailwind CSS + lucide-react icons
- No shadcn/ui directory found, use custom Tailwind components

Implementation:
1. Create frontend/src/components/TradeFlowCandidateDrawer.tsx
   - Slide-in panel from right side
   - 5 sections: Strategy Hits, Game Balance, Evidence Gate, TA Suggestion, Risk Flags
   - Use the TradeFlowCandidateItem type (from list) + optional detail from API
   - Close button, backdrop click to close

2. Modify frontend/src/pages/TradeFlow.tsx
   - Add onClick handler to table rows
   - Pass selected candidate to drawer
   - Manage drawer open/close state

After implementation, run: cd frontend && npm run build
Fix any errors. Max 2 rounds.

When done, print: "UI-003 COMPLETE"
