You are working on TradingAgents-AShare project.

## Task: UI-002 — 前端 TradeFlow 页面骨架与候选池表格

Read the full task description from: docs/task_runs/ui002-task.md

Key context:
- The project uses React + TypeScript + Vite
- API Pydantic models are in api/tradeflow_schemas.py
- API service layer is in api/services/tradeflow_service.py
- Frontend pages are in frontend/src/pages/
- API client is in frontend/src/services/api.ts (ApiService class with private request<T> method)
- Types are in frontend/src/types/index.ts
- Sidebar nav is in frontend/src/components/sidebarNav.ts (navItems array)
- Routes are in frontend/src/App.tsx (uses react-router-dom)
- UI uses shadcn/ui components in frontend/src/components/ui/

Implementation steps:
1. Add TradeFlow types to frontend/src/types/index.ts (mirror api/tradeflow_schemas.py)
2. Add API client methods to frontend/src/services/api.ts (add to ApiService class)
3. Add sidebar nav entry in frontend/src/components/sidebarNav.ts (use Target icon from lucide-react)
4. Create frontend/src/pages/TradeFlow.tsx:
   - Top: summary cards (total candidates, A/B/C count, need_deep_ta count, avg completeness)
   - Main: candidate table with columns: Symbol, Name, Tier, Score, Strategy Tags, Trigger Price, Invalid Price, Completeness, need_deep_ta, Observe State, Action
   - Tier badges: A=green, B=yellow, C=red
   - Filters: by tier, need_deep_ta, observe_state
   - Date picker (default today)
   - Loading/empty/error states
5. Register route in frontend/src/App.tsx: <Route path="/tradeflow" element={<TradeFlow />} />

After implementation, run: cd frontend && npm run build
Fix any TypeScript/build errors. Max 2 fix rounds.

When done, print: "UI-002 COMPLETE"
