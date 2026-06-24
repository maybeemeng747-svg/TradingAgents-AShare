# DATA-020 Summary

- **Task**: DATA-020 数据源健康日报前端可视化与 skipped/failed 分层展示（P1）
- **Status**: ✅ done
- **Runner**: OpenCode (glm-5.2)

## Files Changed

### Backend (Python)
- `tradingagents/dataflows/live_source_sampling.py` — JSON sidecar save + `find_latest_live_sampling_json` + `load_latest_live_sampling_report`
- `api/tradeflow_schemas.py` — `LiveSamplingSampleItem/ResultItem/Summary/Response`
- `api/services/tradeflow_service.py` — `get_live_sampling_report()` (no_data envelope)
- `api/main.py` — `GET /v1/data-sources/live-sampling` + imports
- `api/runtime_tier.py` — register `tradeflow_live_sampling` in fast tier

### Frontend (TypeScript/React)
- `frontend/src/types/index.ts` — LiveSampling types
- `frontend/src/services/api.ts` — `getLiveSamplingReport()`
- `frontend/src/pages/TradeFlow.tsx` — `LiveSamplingPanel` + state + fetch + render; incidental TF-REVIEW-004 `emptyReason` render fix

### Tests
- `tests/test_data020_live_sampling_api.py` (new, 27 cases)

### Docs
- `docs/TASKS.md` — DATA-020 status → done
- `docs/DEVLOG.md` — entry added

## Key Logic

- Live sampling report (DATA-019/019A) is saved as Markdown; DATA-020 adds a **JSON sidecar** so the API/UI get structured data without parsing Markdown.
- API returns a **`no_data` envelope** when no report exists → UI shows clean empty state, never errors.
- Layered status contract enforced in UI: **SKIPPED = grey "未启用实盘抽样" (never green)**, FAILED/RATE_LIMITED = red fault, NORMAL_NO_DATA = grey "正常无数据", fallback = blue accent. Each red fault shows an "影响哪些报告字段" hint.

## Test Results

- `tests/test_data020_live_sampling_api.py`: **27 passed**
- Data-source regression (`test_data019/018/runtime_tier/ui001/g006/g007`): **408 passed**
- Full `pytest tests/ -q -x`: **5376 passed**, 1 failed (pre-existing TF-REVIEW-004 `test_non_trading_day_mapped`, unrelated), 17 skipped
- `npm run build`: **passed**

## Risks / Notes

- TF-REVIEW-004 was in-progress in the working tree (uncommitted) with an unused `emptyReason` that blocked `tsc`; rendered it per its own comment intent (no semantic change to DATA-020).
- TF-REVIEW-004 test failure `test_non_trading_day_mapped` is pre-existing and out of scope.
- Did NOT git commit (per task constraints).
