# H-012 Task Run — 昊天主题注册表与政策版本 Watchlist

- **Task**: H-012
- **Date**: 2026-06-14
- **Executor**: OpenCode
- **Status**: done

## Changed Files

1. `tradingagents/tradeflow/topic_registry.py` (new) — Topic registry module
2. `tradingagents/tradeflow/candidate_engine.py` — Integration call after H-008
3. `api/tradeflow_schemas.py` — API response models
4. `api/services/tradeflow_service.py` — Service functions
5. `api/main.py` — API endpoints
6. `tests/test_h012_topic_registry.py` (new) — 113 tests
7. `docs/DEVLOG.md` — Updated
8. `docs/TASKS.md` — Status updated

## Key Logic

- 8 pre-defined topics: 低空经济, 算力, 半导体设备, 机器人, 新能源, 国产替代, 并购重组, 出海
- Each topic has: keywords, aliases, policy level, industry chain segments
- Topic status maps from H-010 lifecycle: EMERGING→酝酿, ACCELERATING→发酵, CONFIRMING→确认, CROWDED/FADING→退潮
- Candidate registration: keyword matching via mandate_topic/policy_tags/name
- Watchlist generation: aggregates candidates per topic, sorted by score, max 5 per topic
- Note suggestions: `主题｜状态｜级别｜核心X只｜缺口:a/b` — no buy/sell words

## Test Results

- H-012 tests: 113 passed
- H-008/H-010/H-011 regression: 240 passed
- Tradeflow full: 136 passed
