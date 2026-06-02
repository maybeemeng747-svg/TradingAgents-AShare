# PERF-002 Task Run Summary

- Task: PERF-002 — 轻量 TA Profile 与模块路由（P1）
- Status: DONE
- Started: 2026-06-02 19:28:58
- Completed: 2026-06-02
- Rounds: 1

## Deliverables

### Core Implementation
| File | Purpose |
|------|---------|
| `api/ta_profile.py` | TAProfile enum, spec definitions, routing logic |
| `api/runtime_tier.py` | RuntimeTier enum, tier specs, metadata helpers |
| `tests/test_perf002_ta_profiles.py` | 630-line test suite |

### Integration Points
| File | Change |
|------|--------|
| `api/main.py` | Runtime tier gate + profile routing in /v1/analyze |
| `api/tradeflow_schemas.py` | RuntimeProfileMeta schema |
| `api/services/tradeflow_service.py` | Profile-aware candidate routing |
| `frontend/src/types/index.ts` | Profile type fields |
| `frontend/src/services/api.ts` | Profile in analyze API |
| `frontend/src/stores/analysisStore.ts` | Profile state |
| `frontend/src/pages/Analysis.tsx` | Profile-aware analysis console |
| `frontend/src/pages/TradeFlow.tsx` | Profile metadata display |
| `frontend/src/components/ChatCopilotPanel.tsx` | Profile in chat context |
| `frontend/src/components/TradeFlowCandidateDrawer.tsx` | Profile in candidate drawer |
| `scheduler/main.py` | Scheduler uses FULL_TA with confirmation |

## Profiles Defined
1. **MIDLINE_POLICY_LIGHT** — 政策/公告/基本面/风险/量价
2. **SHORT_TECH_LIGHT** — 技术/量价/资金/风险
3. **POSITION_RISK_LIGHT** — 风控/资金/量价/公告
4. **FULL_TA** — 完整 14 Agent（需确认）

## Acceptance Criteria Met
- [x] POLICY_AMBUSH → MIDLINE_POLICY_LIGHT
- [x] TECH_TRADE → SHORT_TECH_LIGHT
- [x] 已持仓复盘 → POSITION_RISK_LIGHT
- [x] 完整 TA 仍可人工选择
- [x] 测试覆盖 profile 路由，不触发真实 LLM
- [x] RuntimeProfileMeta in response
- [x] enabled_modules in response

## Test Results
- 49 smoke tests: PASSED
- 273 profile/tier tests: PASSED
- Frontend build: PASSED

## Follow-up
- PERF-003: 运行遥测（依赖 PERF-002 ✓）
- PERF-004: 完整 TA 手动确认与 scheduler 成本门禁（依赖 PERF-002 ✓）
