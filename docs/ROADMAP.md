# TradingAgents-AShare Roadmap

> Last updated: 2026-06-02

## 2026-07-10 Rebaseline

当前阶段不是继续堆叠功能，而是把已经存在的工程能力收敛成可验证的投研闭环。

### 当前唯一主线

```text
Tree Work 研报/半年报
  -> 事实/观点分离与来源分层
  -> 昊天候选与证据缺口
  -> 轻量 TA / 人工确认的深度 TA
  -> 观察仓与盘中状态
  -> 盘后事实复盘与 5/20/60 交易日回放
```

### 近期冻结项

- 暂停 PLAYBOOK-002~006、额外 UI 美化、新数据供应商和新模型适配，除非它们直接阻塞上述主线。
- PLAYBOOK-001 只允许完成契约审查和兼容性收口，不能提前解锁仓位规则。
- 不再把 fixture/mock 链路通过等同于策略有效；真实数据 smoke、真实交易日记录和回放结果必须单独标记。

### 产品验收指标

- 每日主候选不超过 2-3 只，观察候选不超过 5 只。
- 昊天候选必须带政策来源、产业受益路径、反证状态和时间戳；研报关注度不能单独升格候选。
- 每个核心数据字段必须能区分 `HAS_DATA`、`NORMAL_NO_DATA`、`FAILED`、`STALE` 和 `SKIPPED`。
- 观察仓必须形成 `候选 -> 观察 -> 触发/失效 -> 盘后复盘` 的真实记录。
- 至少积累 5/20/60 个交易日结果后，才允许调整策略权重或扩大候选范围。

## Product Direction

TradingAgents-AShare is moving from a report generator toward a layered A-share research operating system.

The core direction is:

```text
Mandate Radar decides what to watch.
TradeFlow decides which symbols enter the candidate pool.
TA decides whether a specific symbol deserves deeper analysis now.
Risk and execution decide what actions are allowed.
Review decides whether the whole system is learning or drifting.
```

The system must not become a pile of disconnected ideas. New work should fit one of the layers below.

## Runtime Principle

The system must stay light enough for real research use. It should not turn every click into a full multi-agent report.

Default runtime layers:

```text
Fast Radar
  └─ Target latency: 5-30 seconds
     No LLM by default. Used for TradeFlow candidate discovery, data health,
     evidence gaps, strategy classification, and "is this worth deeper work?"

Light Research
  └─ Target latency: 1-3 minutes
     Selective modules only. Used for one candidate after user intent is clear:
     medium-term policy validation, short-term technical confirmation, or
     position-aware risk review.

Full TA
  └─ Target latency: 10-20 minutes
     Full multi-agent debate. Manual trigger only, with model/cost confirmation.
     Used for high-conviction candidates, position risk, or deep research.
```

Architecture rule:

```text
Fast first. Light second. Full only by explicit confirmation.
```

Any new feature must declare which layer it belongs to, its expected runtime, whether it calls LLMs, and what user action is required before running costly analysis.

## Target Architecture

```text
Data Layer
  ├─ Market data: OHLCV, volume, turnover, liquidity
  ├─ Event data: announcements, buybacks, ratings, policy/news titles
  ├─ Capital flow: individual fund flow, board flow, LHB, margin data
  ├─ Source router: vendor capability matrix, fallback order, freshness checks
  ├─ Provenance: vendor, endpoint, as_of, fetched_at, unit, status, source_url
  └─ User data: watchlist, holdings, manual pools

Mandate Radar
  ├─ Policy/event signal normalization
  ├─ Source authority and continuity scoring
  ├─ Industry-chain beneficiary mapping
  └─ Ambush score and candidate type classification

TradeFlow Candidate Engine
  ├─ Universe manager
  ├─ Strategy scoring: policy, narrative, event, VCP, pullback, fund flow
  ├─ Evidence gate and risk demotion
  ├─ Candidate tiering and token budget
  └─ Filtered-symbol traceability

TA Deep Analysis
  ├─ Short/mid horizon analysis routing
  ├─ Multi-agent research, debate, risk, execution
  ├─ Data readiness and strong-action sanitizer
  └─ Manual trigger from qualified candidates

Execution And Tracking
  ├─ Watchlist and tracking board
  ├─ Observe state: waiting, triggered, invalidated
  ├─ TA queue and cost gate
  └─ Position-aware action mapping

Review And Evaluation
  ├─ Post-market review
  ├─ Candidate hit/miss/false-positive tracking
  ├─ Mandate replay and反证
  └─ Strategy calibration

Frontend Workbench
  ├─ TradeFlow candidate pool
  ├─ Mandate Radar view
  ├─ Candidate detail drawer
  ├─ Filtered-symbol trace
  ├─ TA queue and report links
  └─ Data source health

Performance And Cost Layer
  ├─ Runtime tier: fast / light / full
  ├─ Latency budget and timeout policy
  ├─ LLM call budget and model disclosure
  ├─ Scheduler cost guard
  ├─ Manual confirmation before full TA
  └─ Per-run telemetry: elapsed time, modules, tokens/calls, reason

Automation Layer
  ├─ TASKS.md task pool
  ├─ OpenClaw scheduling
  ├─ OpenCode implementation
  ├─ Codex review
  └─ DEVLOG/task_runs audit trail
```

## Strategy Positioning

There are two candidate systems, and they should not be mixed.

### Mandate Pool

Purpose: medium-term left-side research.

Primary question:

```text
What policy/industry direction is gaining official support, and which companies may become credible beneficiaries before the market fully prices it?
```

Candidate types:

- `POLICY_AMBUSH`: policy left-side candidate
- `POLICY_CONFIRM`: policy logic with technical/market confirmation
- `PSEUDO_POLICY`: weak or concept-only policy exposure
- `OVERHEATED_AVOID`: good story but already crowded or high-risk

### Technical/Trade Pool

Purpose: execution and short-term timing.

Primary question:

```text
Given an existing logic, is there a technical trigger or risk boundary now?
```

Candidate types:

- `TECH_TRADE`: technical setup, short-term or做T only
- `EVENT_WATCH`: event-driven watch candidate

## Development Phases

### Phase 1: Stabilize TradeFlow Workbench

Goal: make the existing candidate pool usable and auditable.

Status: mostly done.

Key tasks:

- UI-001 to UI-006: done
- UI-007: filtered-symbol traceability
- UI-008: symbol/name normalization

Exit criteria:

- User can generate a candidate pool from the frontend.
- User can see which symbols entered, which were filtered, and why.
- Candidate symbol/name fields are normalized across DB/API/UI.

### Phase 2: Build Mandate Radar v0

Goal: create the first evidence-based policy-left candidate layer.

Key tasks:

- H-001: mandate signal model
- H-002: policy continuity and source authority
- H-003: beneficiary mapping
- H-004: ambush score and candidate type split

Exit criteria:

- A candidate can be classified as policy ambush vs technical trade.
- Every high mandate score has source/date/title evidence.
- Pure technical setups do not pollute the policy-left pool.

### Phase 3: Make Mandate Radar Visible

Goal: turn policy-left research into a usable dashboard.

Key tasks:

- H-005: frontend Mandate Radar view
- UI detail drawer extension for policy logic
- Topic and beneficiary filters

Exit criteria:

- User can see policy topic, company role, evidence, and missing validation.
- The dashboard explains why a symbol is a policy ambush candidate or why it is pseudo-policy.

### Phase 4: Connect Mandate Pool To TA

Goal: let users manually request TA only when it is worth spending tokens.

Key tasks:

- Add manual "run TA" action from candidate detail.
- Add model/cost confirmation before triggering.
- Route `POLICY_AMBUSH` to medium-term context and `TECH_TRADE` to short-term context.

Exit criteria:

- No automatic costly analysis.
- User can manually trigger TA from qualified candidates.
- TA receives candidate context: mandate topic, beneficiary path, evidence gaps, and position context.
- UI clearly separates "light research" from "full TA".
- Every TA trigger shows expected runtime and model/cost risk before execution.

### Phase 4.5: Runtime Budget And Lightweight Analysis

Goal: keep the system responsive enough for盤前/盤中 use.

Key tasks:

- Define runtime tiers and speed budgets across API/UI/scheduler.
- Add lightweight TA profiles:
  - `MIDLINE_POLICY_LIGHT`
  - `SHORT_TECH_LIGHT`
  - `POSITION_RISK_LIGHT`
- Add cost and latency telemetry for every analysis run.
- Prevent scheduler and TradeFlow from silently triggering full TA.

Exit criteria:

- TradeFlow candidate discovery returns within the fast budget in normal fixture/small-pool mode.
- Light research can run with a subset of agents/modules.
- Full TA requires explicit user confirmation.
- Frontend displays runtime tier and estimated cost before analysis starts.

### Phase 5: Review And Calibration

Goal: stop the system from becoming narrative-only.

Key tasks:

- H-006: mandate replay and反证
- Candidate false-positive and false-negative logs
- Weight adjustment from replay results

Exit criteria:

- Every policy-left candidate can be replayed after 5/10/20/60 trading days.
- The system records policy continuation, company validation, price reaction, drawdown, and反证.

### Phase 6: Broaden Data Coverage

Goal: improve truthfulness and stability.

Potential tasks:

- A-stock source integration: absorb the useful parts of SimonLin1212 `a-stock-data` style source skills without replacing this system.
- Vendor capability catalog: Tencent/mootdx for realtime quotes, Eastmoney/AkShare for broad data, Sina/financial endpoints, Juchao/CNInfo for announcements, Cailianshe/news for events, BaoStock/yfinance as historical/fallback sources.
- Realtime quote patching: detect stale daily bars and explicitly patch current price/volume from a realtime source with `is_realtime_patched`.
- More stable announcement source fallback.
- Better industry and concept mapping.
- Board fund flow and individual fund flow consistency.
- LHB/margin availability classification.
- Data freshness and stale detection in UI.
- Data contract fixtures: per endpoint field/unit/freshness tests, including failure and rate-limit states.

Exit criteria:

- Candidate confidence reflects data completeness.
- Data source failures are visible instead of silently lowering quality.
- Every important candidate can explain which vendor/endpoint produced each key field.

## Task Policy

Tasks should be added in these buckets:

- `H-*`: Mandate Radar and policy-left strategy.
- `S-*`: TradeFlow scoring and strategy quality.
- `T-*`: Discovery, event source, and candidate generation.
- `M-*`: State management, review, data health, and infrastructure inside TradeFlow.
- `DATA-*`: Data source routing, provenance, freshness, and vendor fallback.
- `UI-*`: Frontend and API visibility.
- `AUTO-*`: Automation loop and toolchain.
- `V-*`: End-to-end validation and replay.

Only the current critical chain should be `ready`. Future roadmap items should stay `proposed` unless they unblock the current phase.

## Current Focus

Current focus is Phase 2:

```text
H-001 -> H-002 + H-003 -> H-004 -> H-005/H-006
```

UI-007 and UI-008 are support tasks. They are useful but should not derail the mandate radar buildout unless they block frontend testing.
