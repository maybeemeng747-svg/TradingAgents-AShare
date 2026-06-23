# 跟踪看板 V1 端到端验收报告

**验收日期**: 2026-06-23
**验收范围**: TRACK-001 ~ TRACK-NOTIFY-001 + IC-TA-001
**验收结论**: ✅ 通过

---

## 一、交付物清单

| 任务 | Commit | 内容 |
|------|--------|------|
| TF-UX-001 | `5edaff2` | 小资金试跑工作台前端 |
| TRACK-001 | `2ef6c37` | 观察仓数据模型 + 5 个 CRUD API |
| TRACK-002 | `fd0d511` | 跟踪看板 v2 分组聚合 API（持仓/观察仓/今日指引/alerts） |
| TRACK-003 | `d80aa66` + `e1426a1` | 前端四区改版 + Hook 顺序修复 |
| IC-TA-001 | `bc5b7d3` | investment-controller 只读上下文包（6 桶聚合） |
| TRACK-004 | `88dfc76` | 观察仓状态流转引擎（入场区/触发区/失效区） |
| TRACK-005 | `9090e24` | 盘后复盘摘要写回跟踪看板 + 次日计划 |
| TRACK-006 | `5aa512d` | TradeFlow/TA 一键加入观察仓 + 来源追踪 |
| TRACK-NOTIFY-001 | `572649a` | 通知 draft 生成 + dry-run 端点 |

## 二、API 端点验收

### 跟踪看板
| 方法 | 端点 | 用途 | 状态 |
|------|------|------|------|
| GET | `/v1/dashboard/tracking-board` | v1 看板（兼容） | ✅ |
| GET | `/v1/dashboard/tracking-board/v2` | v2 分组聚合（持仓/观察仓/今日指引/alerts/freshness） | ✅ |

### 观察仓管理
| 方法 | 端点 | 用途 | 状态 |
|------|------|------|------|
| GET | `/v1/tradeflow/observation-items` | 查询观察仓列表 | ✅ |
| POST | `/v1/tradeflow/observation-items` | 创建观察项 | ✅ |
| PATCH | `/v1/tradeflow/observation-items/{id}` | 更新观察项状态 | ✅ |
| POST | `/v1/tradeflow/observation-items/{id}/mark` | 标记（触发/失效/确认） | ✅ |
| POST | `/v1/tradeflow/observation-items/bulk-upsert` | 批量写入 | ✅ |
| POST | `/v1/tradeflow/candidates/{symbol}/add-to-observation` | 候选一键加入观察仓 | ✅ |
| POST | `/v1/tradeflow/ta-reports/{report_id}/add-to-observation` | TA 报告一键加入观察仓 | ✅ |

### 投资总控官
| 方法 | 端点 | 用途 | 状态 |
|------|------|------|------|
| GET | `/v1/dashboard/investment-controller/context` | 只读上下文包（6 桶聚合） | ✅ |
| POST | `/v1/dashboard/investment-controller/notify/dry-run` | 通知 draft 生成（dry-run） | ✅ |

### 盘后复盘
| 方法 | 端点 | 用途 | 状态 |
|------|------|------|------|
| GET | `/v1/tradeflow/review` | 查询复盘摘要 | ✅ |
| POST | `/v1/tradeflow/review/generate` | 生成复盘摘要 | ✅ |

## 三、测试覆盖

| 测试套件 | 用例数 | 结果 |
|----------|--------|------|
| test_ic_ta001_investment_controller_context | 35 | ✅ passed |
| test_track001_observation_warehouse | 70 | ✅ passed |
| test_track002_tracking_board_v2 | 21 | ✅ passed |
| test_track004_observation_state_engine | — | ✅ passed |
| test_track005_post_market_tracking_review | — | ✅ passed |
| test_track006_add_to_observation | — | ✅ passed |
| test_track_notify001_notification_draft | 25 | ✅ passed |
| **合计** | **250+** | **全通过** |

## 四、数据契约校验

### IC-TA-001 只读契约
- [x] GET-only，不写状态
- [x] FAST_RADAR tier，不调用 LLM
- [x] 每个 bucket + item 都有 `source` 和 `as_of`
- [x] `data_status` 枚举：fresh / stale / missing / failed / skipped
- [x] 空数据返回稳定结构，不 500
- [x] 不合成强动作词（买入/卖出/清仓）
- [x] 不泄漏敏感字段（api_key/token/secret/password）
- [x] 持仓隔离（tradeflow.db vs tradingagents.db）

### 观察仓状态流转
- [x] 状态机：WAITING → TRIGGERED → CONFIRMED / EXPIRED / INVALIDATED
- [x] 入场区（WAITING + 无效价未到）→ 触发区（价格触及）→ 失效区（过期/价破）
- [x] 手动标记 override 可用

## 五、前端验收

- [x] 四区布局：持仓 / 观察仓 / 今日指引 / 盘后复盘
- [x] React Hooks 顺序正确（`e1426a1` 修复）
- [x] `npm run build` 通过
- [x] lint 通过

## 六、已知限制

1. **通知仅 dry-run**：`TRACK-NOTIFY-001` 当前只生成通知文本，不实际推送飞书。真实推送需单独接入飞书 API。
2. **IC-TA-001 TA 报告范围**：`_collect_latest_ta_reports` 目前只覆盖持仓 + 观察仓 universe，不含全部历史报告。
3. **观察仓无 LLM**：状态流转引擎纯规则驱动，不做 LLM 判断（符合 FAST_RADAR tier）。

## 七、用户操作手册

### 1. 跟踪看板 V2

**入口**：前端跟踪看板页面

**四区含义**：
- **持仓区**：已买入的股票，显示当前市值、盈亏、最新 TA 观点
- **观察仓区**：跟踪中的标的，显示状态（等待/触发/失效）、入场价、失效价
- **今日指引**：当天应关注的候选 + 信号摘要
- **盘后复盘**：昨天的市场回顾 + 持仓表现 + 次日计划

### 2. 观察仓操作

**添加标的到观察仓**：
- 在 TradeFlow 候选列表中，点击"加入观察仓"
- 或在 TA 报告详情中，点击"加入观察仓"
- 需要指定：观察价（触发价）、失效价（止损参考）、观察原因

**状态流转**：
- `WAITING`（等待触发）→ 价格触及观察价时变为 `TRIGGERED`
- `TRIGGERED` → 手动确认变 `CONFIRMED`，或超时变 `EXPIRED`
- 任何时候可手动标记为 `INVALIDATED`（失效）

### 3. 投资总控官上下文

**用途**：investment-controller agent 通过此端点获取决策所需的全部数据，无需分别调多个 API。

**调用**：`GET /v1/dashboard/investment-controller/context`

**返回结构**：
```json
{
  "schema_version": "1.0",
  "read_only": true,
  "holdings": { "source": "...", "as_of": "...", "count": N, "items": [...] },
  "observation_warehouse": { "source": "...", "as_of": "...", "count": N, "items": [...] },
  "tradeflow_candidates": { "source": "...", "as_of": "...", "count": N, "items": [...] },
  "latest_ta_reports": { "source": "...", "as_of": "...", "count": N, "items": [...] },
  "data_health": { "source": "...", "as_of": "...", "count": N, "items": [...] },
  "pending_ta_required": { "source": "...", "as_of": "...", "count": N, "items": [...] }
}
```

### 4. 盘后复盘

**触发**：每日收盘后自动生成，也可手动触发。

**内容**：市场概况 + 持仓表现 + 观察仓状态变化 + 候选池更新 + 次日关注清单。

### 5. 通知 draft

**端点**：`POST /v1/dashboard/investment-controller/notify/dry-run`

**用途**：预览 investment-controller 会发送什么通知。当前仅返回文本，不实际推送。

---

**验收人**：主控 AI
**审核人**：孟（待确认）
