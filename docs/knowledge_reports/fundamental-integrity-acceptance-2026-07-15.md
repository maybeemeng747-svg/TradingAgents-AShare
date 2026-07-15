# Fundamental Integrity Acceptance - 2026-07-15

## Scope

Deterministic replay only. No live LLM call, network request, production DB
write, or prompt-template change was used.

## Controls Accepted

| Control | Result |
|---|---|
| Company identity contract | Profile must provide name, main business, and industry; otherwise commercial analysis is blocked. |
| Period normalization | Income and cash-flow statements use cumulative scopes; Q4 is derived as FY minus Q3 YTD only. |
| Cause evidence | Missing announcements are `NORMAL_NO_DATA`; unsupported causal language becomes `CAUSE_UNSUPPORTED`. |
| Accounting evidence | Net/gross recognition and contract-liability claims require official evidence. |
| C-006 wiring | Structured facts now populate cash-flow/profit and debt-ratio anomaly inputs. |
| Model trace | Analyst traces save provider, model, tier and an input-status digest without prompts, raw content, paths, or credentials. |

## 603629.SH Replay

The historical failure text claiming a chemical business and a chemical-season
explanation is rejected when no verified company profile is available:

- `IDENTITY_UNVERIFIED`
- `CAUSE_UNSUPPORTED`

The financial facts remain available for review. Using the replay fixture,
2025 Q4 revenue is derived as `33.07400 - 24.62342 = 8.45058`, rather than
using annual `33.07400` as a single-quarter value.

## Cross-industry Coverage

Verified identity fixtures cover computing equipment, consumer manufacturing,
banking, ETF and battery manufacturing. The integrity layer preserves their
provider-backed industry values and does not infer an industry from ratios.

## Verification

Focused suites cover FUND-001 through FUND-006 plus existing data-collector
and HK boundary regression suites. They run offline with deterministic fixtures.

## Remaining Limit

This implementation makes a fundamental block fail closed when its upstream
identity, period, or explanation evidence is insufficient. It does not prove
that every provider profile or announcement is economically complete. Provider
quality remains visible in raw evidence and must be reviewed when the gate
returns `NEEDS_REVIEW`.
