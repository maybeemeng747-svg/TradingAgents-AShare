# TF-UX-001 Run Summary

- **Task**: TF-UX-001 — TradeFlow 小资金试跑主工作台降噪与默认视图（P1）
- **Executor**: OpenCode (glm-5.2)
- **Started**: 2026-06-22 ~13:19 (Asia/Shanghai)
- **Finished**: 2026-06-22 ~13:35
- **Status**: ✅ PASS

## Goal

把 TradeFlow 前端默认工作流收敛成“小资金试跑”视角。后端候选评分不动，只调整前端默认展示与解释，让用户一眼看到“今天先看哪几只、为什么、风险额度”。

## Implementation

### New modules

- `frontend/src/utils/tradeflowFocus.ts`（~370 行）
  - `groupMainCandidates()`：四桶分流 `pending_confirm / near_trigger / main / invalidated`，三桶进默认主视图，`invalidated` 折叠。
  - `computeTrialBudgetView()`：从 PaperLedger 算出 单票预算 / 最大占用 / 待确认动作 / 软提示。
  - `computeCandidateRiskView()`：单卡风险占用（已跟踪 / 待确认 / 超出上限）。
  - `pickWhySelected / pickWhyNotMain`：入选与“为什么未入选主候选”原因聚合。
  - `buildFixtureCandidate()`：smoke fixture builder。

### Edited

- `frontend/src/pages/TradeFlow.tsx`
  - 焦点视图三组（待确认 / 接近触发 / 主候选）+ 折叠的 已失效/已过期 池 + 折叠观察池。
  - 顶部 `<TrialBudgetPrompt>`：本金 / 单票预算 / 最大占用 / 待确认动作 / 软提示。
  - 主候选卡片增加 综合分 + 核心触发价 + 风险占用徽标。
  - 观察池卡片显式 `未入选主候选：<reasons>`。
  - 进入 candidates tab 时静默预取 PaperLedger（非阻塞）。

### Tests

- 新增 `frontend/src/utils/tradeflowFocus.test.ts`：18 个 smoke 测试。
- 10 候选 fixture 默认主视图 = 5 只（2 pending + 2 near + 1 main），符合验收上限。
- 所有提示文案断言不含 买/卖/加仓/减仓/满仓/清仓。

## Verification

| Check | Command | Result |
| --- | --- | --- |
| Frontend unit tests | `cd frontend && npx vitest run` | 27 passed (新增 18 + 旧 9) |
| TypeScript typecheck | `cd frontend && npx tsc --noEmit` | clean |
| Frontend build | `cd frontend && npm run build` | PASS (1.34s) |
| TradeFlow API tests | `pytest tests/test_ui001_tradeflow_api.py tests/test_v007_tradeflow_trial_e2e.py -q` | 108 passed |

### Lint

`npm run lint` 报 48 problems / 41 errors。基线 47 problems / 40 errors（`git stash` 对照），新增 1 个 `react-hooks/set-state-in-effect` 告警，来自 PaperLedger 预取 effect，与同文件原有 `fetchData / auto-refresh` 模式一致，未改写。

## Acceptance criteria mapping

| 验收 | 结果 |
| --- | --- |
| 前端 smoke 测试覆盖默认视图和折叠观察池 | ✅ groupMainCandidates + computeTrialBudgetView + renderObservationPool 都有对应 case |
| 10 只候选 fixture 中默认主视图不超过 5 只 | ✅ 默认主视图恰好 5 只，超 cap 时 `main_view_truncated=true` |
| 用户能一眼看到“今天先看哪几只、为什么、风险额度” | ✅ TrialBudgetPrompt 顶部展示，每张卡显示 综合分 / 核心触发价 / 风险占用 / 入选原因 |
| 运行相关测试确认通过 | ✅ vitest 27 + pytest 108 passed |

## Constraints honored

- 不调用 LLM ✅
- 不写生产数据库 ✅
- 不改变后端候选评分 ✅（所有改动只在 `frontend/`）
- 代码标注 `// [TF-UX-001] small_cap_trial_workbench` ✅

## Files changed

```
frontend/src/utils/tradeflowFocus.ts             (新增)
frontend/src/utils/tradeflowFocus.test.ts        (新增)
frontend/src/pages/TradeFlow.tsx                 (修改)
docs/TASKS.md                                    (修改)
docs/DEVLOG.md                                   (修改)
docs/task_runs/TF-UX-001-20260622-133149/        (新增)
```

## Followups / Risks

- `PERF-005`（TradeFlow 页面与 API 性能预算回归）解除 blocked 后，需复核 focus 视图新增的 paperLedger 预取对首屏耗时的影响。
- 前端 `react-hooks/set-state-in-effect` lint 规则项目范围内有 40 个 pre-existing 违规，本任务新增 1 个同模式违规，建议在 PERF-005 或独立 tech-debt 任务里统一处理。
