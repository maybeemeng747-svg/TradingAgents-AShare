# 通知去噪规则回放验收报告（NOTIFY-003）

> 任务：NOTIFY-003 — 通知去噪规则回放测试与日报/盘中分层验收（P2）
> 回放时间：2026-06-27 10:00:00
> 依赖：NOTIFY-002 ✓ / TRACK-NOTIFY-001 ✓
> 结果：**19/19 场景通过**，全部 dry-run，未真实发送、未调用 LLM。

## 1. 验收目标

NOTIFY-002 接入昊天日报摘要与数据缺口摘要后，最大风险是把普通数据缺口、正常
无数据、观察项误推成盘中主动提醒。本报告用一张覆盖 P0/P1/P2/P3 与各类数据缺口的
fixture 矩阵回放整条通知链路，验证分层规则：

- **P0/P1**（且非 record_only）→ `intraday_push` 盘中主动提醒队列；
- **P2/P3** → `daily_digest` 夜间日报，不盘中推送；
- **NORMAL_NO_DATA / 数据缺口 / 无行情** → `record_only=True`，只进日报摘要；
- 同一标的同一事件 30 分钟窗口内去重；
- 全矩阵草稿不含强动作词。

## 2. 总览

| 指标 | 值 |
|------|----|
| 回放场景总数 | 19 |
| 通过 | 19 |
| 失败 | 0 |
| 盘中提醒场景（期望 intraday） | 6 |
| 噪声隔离场景（全部 record_only） | 8 |

## A. 优先级覆盖矩阵（P0/P1/P2/P3）

| # | 场景 | 描述 | 期望优先级 | 实际通道 | record_only | 结果 |
|---|------|------|:---:|:---:|:---:|:---:|
| 1 | `P0-HOLDINGS-LARGE-DROP` | 持仓日跌幅 -5% 超过 -3% 大跌阈值 → P0 盘中提醒 | P0 | intraday×1 | 0/1 | ✅ |
| 2 | `P0-OBS-IN-ENTRY-ZONE` | 观察仓现价落入买入区间 → P0 盘中提醒 | P0 | intraday×1 | 0/1 | ✅ |
| 3 | `P1-HOLDINGS-MODERATE-DROP` | 持仓日跌幅 -2% 超过 -1.5% 关注线 → P1 盘中提醒 | P1 | intraday×1 | 0/1 | ✅ |
| 4 | `P1-OBS-INVALIDATED` | 观察仓跌破失效价 → P1 盘中提醒 | P1 | intraday×1 | 0/1 | ✅ |
| 5 | `P1-OBS-NEAR-ENTRY` | 观察仓接近买点下沿 5% 内 → P1 盘中提醒 | P1 | intraday×1 | 0/1 | ✅ |
| 6 | `P1-OBS-TA-REQUIRED` | 观察仓标记 ta_required → P1 盘中提醒 | P1 | intraday×1 | 0/1 | ✅ |
| 7 | `P2-OBS-MISSED-ENTRY` | 观察仓现价高于买入区上沿 5% → P2 仅日报 | P2 | daily×1 | 0/1 | ✅ |
| 8 | `P2-HOLDINGS-NO-ANALYSIS` | 持仓无最新 TA 报告 → P2 仅日报 | P2 | daily×1 | 0/1 | ✅ |
| 9 | `P2-CANDIDATE-PENDING-TA` | 候选池标记 need_deep_ta → P2 仅日报 | P2 | daily×1 | 0/1 | ✅ |
| 10 | `P3-OBS-WATCHING` | 观察仓价格远离买点持续观察 → P3 仅日报 | P3 | daily×1 | 0/1 | ✅ |

## B. NORMAL_NO_DATA / 数据缺口噪声隔离

| # | 场景 | 描述 | 期望优先级 | 实际通道 | record_only | 结果 |
|---|------|------|:---:|:---:|:---:|:---:|
| 1 | `NOISE-HOLDINGS-BUCKET-FAILED` | 持仓 bucket data_status=failed + 大跌 → 整体 record_only | P0 | daily×1 | 1/1 | ✅ |
| 2 | `NOISE-HOLDINGS-BUCKET-MISSING` | 持仓 bucket data_status=missing + 大跌 → 整体 record_only | P0 | daily×1 | 1/1 | ✅ |
| 3 | `NOISE-OBS-DATA-MISSING` | 观察仓交易日无行情（live_price=None）→ data_missing record_only | P2 | daily×1 | 1/1 | ✅ |
| 4 | `NOISE-OBS-NEEDS-REVIEW` | 观察仓非交易日无行情 → needs_review record_only（P3 仅日报） | P3 | daily×1 | 1/1 | ✅ |
| 5 | `NOISE-OBS-BUCKET-FAILED` | 观察仓 bucket data_status=failed（即使 in_zone）→ record_only | P0 | daily×1 | 1/1 | ✅ |
| 6 | `NOISE-DATA-HEALTH-STALE` | 数据健康 stale（交易日）→ 系统级 record_only 仅日报 | P2 | daily×1 | 1/1 | ✅ |
| 7 | `NOISE-DATA-SOURCE-FAILURE` | 数据源 FAILED → 系统级 record_only 仅日报 | P2 | daily×1 | 1/1 | ✅ |
| 8 | `NOISE-DATA-BLOCKER-DIGEST` | 数据缺口摘要（has_blockers）→ record_only 仅日报 | P2 | daily×1 | 1/1 | ✅ |

## C. NOTIFY-002 全局摘要日报

| # | 场景 | 描述 | 期望优先级 | 实际通道 | record_only | 结果 |
|---|------|------|:---:|:---:|:---:|:---:|
| 1 | `DIGEST-MANDATE-DAILY` | 昊天日报 fresh → P2 日报（非 record_only） | P2 | daily×1 | 0/1 | ✅ |

## 分层规则小结

- NORMAL_NO_DATA 噪声全部隔离（不进 intraday）：**✅ 通过**
- P0/P1/P2/P3 优先级分层正确：**✅ 通过**
- 禁用词扫描（全矩阵）：**✅ 通过**（引擎内置 `assert_no_forbidden_words`）
- dry-run 不真实发送：**✅ 通过**（`webhook_configured` 恒 `None`）
