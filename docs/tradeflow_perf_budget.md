# TradeFlow 性能预算回归（PERF-005）

> 建立：2026-06-25 · 任务 `PERF-005` · 代码标注 `# [PERF-005] tradeflow_perf_budget`

本文件记录 TradeFlow 关键 API 端点的运行层级、性能预算、当前观察值，
以及前端 bundle 体积告警。**不写易碎的绝对耗时断言**，只做预算分层和
明显退化检测（见下文“超预算处理策略”）。

---

## 1. 验收范围（5 个端点）

下列端点全部归入 `FAST_RADAR` 层级，对应 PERF-001 的契约：

| 端点（service 函数）                      | runtime_tier  | expected_latency | llm_allowed | 测试预算（empty DB） | 测试预算（populated DB） |
| ----------------------------------------- | ------------- | ---------------- | ----------- | -------------------- | ------------------------ |
| `tradeflow_candidates` (`get_candidates`)  | `FAST_RADAR` | `5-30s`          | `False`     | 1.0 s                | 3.0 s                    |
| `tradeflow_observe` (`get_observe`)         | `FAST_RADAR` | `5-30s`          | `False`     | 1.0 s                | 3.0 s                    |
| `tradeflow_review` (`get_review`)           | `FAST_RADAR` | `5-30s`          | `False`     | 1.0 s                | 3.0 s                    |
| `tradeflow_data_health` (`get_data_health`) | `FAST_RADAR` | `5-30s`          | `False`     | 1.0 s                | 3.0 s                    |
| `tradeflow_topic_heatmap` (`get_topic_heatmap`) | `FAST_RADAR` | `5-30s`     | `False`     | 1.0 s                | 3.0 s                    |

> `expected_latency=5-30s` 是对外广告值，给前端展示和用户预期用；
> **测试预算**（1 s / 3 s）远低于该窗口，用来捕获 5–10 倍级别的明显退化。

---

## 2. 当前观察值（基线）

> 测量方式：`pytest tests/test_perf005_tradeflow_perf_budget.py`，
> 临时 SQLite fixture（empty / 3 candidates），macOS 本地，median of 5。
> 仅作回归参考，**不是**性能 SLA。

### 2.1 Empty DB（no_data 快路径）

| 端点               | median  | min    | max（含冷启动 import） | 状态     |
| ------------------ | ------- | ------ | ---------------------- | -------- |
| candidates         | ~3.4 ms | ~3.0 ms | ~40 ms                | `ok`     |
| observe            | ~3.1 ms | ~3.1 ms | ~226 ms（首次 import） | `ok`     |
| review             | ~4.3 ms | ~4.2 ms | ~6 ms                 | `no_data`|
| data_health        | ~1.6 ms | ~1.6 ms | ~1.6 ms               | `ok`     |
| topic_heatmap      | ~1.7 ms | ~1.7 ms | ~3.4 ms               | `ok`     |

### 2.2 Populated DB（3 条候选 + 1 条 daily_plan）

| 端点               | median  | min    | max     | 状态   |
| ------------------ | ------- | ------ | ------- | ------ |
| candidates         | ~2.3 ms | ~2.3 ms | ~20 ms | `ok`   |
| observe            | ~3.4 ms | ~3.4 ms | ~3.4 ms | `ok`   |
| review             | ~1.6 ms | ~1.6 ms | ~1.6 ms | `ok`   |
| data_health        | ~1.7 ms | ~1.7 ms | ~1.8 ms | `ok`   |
| topic_heatmap      | ~2.0 ms | ~2.0 ms | ~2.0 ms | `ok`   |
| candidate_detail   | ~1.7 ms | ~1.6 ms | ~1.7 ms | `ok`   |

结论：所有端点中位数都在 5 ms 以内，远低于 1 s / 3 s 测试预算，也远低于
FAST_RADAR 广告的 5–30 s 上限。首次调用可能因 Python import 偶发到 ~200 ms，
仍处于预算内。

---

## 3. 超预算处理策略

遵循 PERF-005 验收准则 4：**“超预算时输出建议，不直接失败夜间主链，
除非明显阻塞”**。`tests/test_perf005_tradeflow_perf_budget.py::_check_budget`
实现如下策略：

| 实际耗时                | 行为                                                                    |
| ----------------------- | ----------------------------------------------------------------------- |
| `elapsed ≤ budget`      | 通过，无输出。                                                          |
| `budget < elapsed < 5×` | 通过，但在 module 级 `_SUGGESTIONS` 中记录一条改进建议，结束时统一打印。|
| `elapsed ≥ 5× budget`   | **硬失败**，视为明显阻塞（obvious regression）。                        |

硬失败阈值 5× 是有意宽松的：3 s 预算 × 5 = 15 s，仍落在 FAST_RADAR 广告的
`5-30s` 窗口内；只有真正卡死或拉全市场扫描才会触发。

建议文案示例：
```
[PERF-005] tradeflow_observe (populated_db) elapsed=4.500s exceeds budget=3.0s (x1.5);
consider checking new heavy queries, missing index, or accidental live data fetch.
```

---

## 4. 前端 Bundle 体积告警

> 任务要求：前端 build 后记录 bundle size warning 到文档，**不要求立刻 code split**。

`npm run build`（vite v6.4.2）输出快照（2026-06-25，commit `c9739d8`）：

| 资源                       | 原始体积   | gzip 体积 | 备注                                    |
| -------------------------- | ---------- | --------- | --------------------------------------- |
| `dist/assets/index-*.js`   | 1,204.75 kB | 345.60 kB | **超过 Vite 500 kB 告警阈值**           |
| `dist/assets/index-*.css`  | 169.24 kB  | 23.24 kB  | 正常                                    |
| `dist/index.html`          | 1.15 kB    | 0.65 kB   | 正常                                    |

Vite 构建日志已自带：
```
(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rollupOptions.output.manualChunks to improve chunking
- Adjust chunk size limit for build.chunkSizeWarningLimit.
```

### 4.1 当前结论与建议（不强制执行）

- 现状：单 chunk ~1.2 MB / gzip ~346 kB。首屏加载在弱网下可能偏慢，但
  gzip 后体积可接受，且这是本地内网工具，不阻塞主线。
- **本次 PERF-005 不做 code split**，仅记录基线，留给后续任务处理。
- 当体积明显恶化（例如 gzip > 600 kB，或单 chunk 翻倍）时，建议优先：
  1. `React.lazy()` + 路由级 dynamic import（TradeFlow / Tracking / Reports 等大页）。
  2. `manualChunks` 把 `recharts` / `lightweight-charts` / `@xyflow/react`
     拆成独立 vendor chunk。
  3. 检查是否有意外的整包 import（例如 `lucide-react` 建议按图标 import）。

---

## 5. 如何运行性能 smoke

```bash
# 后端性能 smoke（不含 live LLM / 全市场扫描）
source .venv/bin/activate
pytest tests/test_perf005_tradeflow_perf_budget.py -q

# 前端构建（观察 bundle 告警）
cd frontend && npm run build
```

测试覆盖：
- `TestEndpointTierConsistency`：5 个端点全部声明 `FAST_RADAR`，`llm_allowed=False`。
- `TestEmptyDBSmokeBudget`：no_data 快路径预算与 `runtime_tier_meta` 一致性。
- `TestPopulatedDBSmokeBudget`：3 候选 fixture 路径预算与 tier meta。
- `TestAcceptancePERF005`：端到端 5 端点 smoke + 预算常数合理性。
- `TestSuggestionEmission`：超预算建议/硬失败策略单元测试。

---

## 6. 约束与不变量

- ❌ 不调用真实 LLM（所有测试 `llm_allowed=False`）。
- ❌ 不跑全市场扫描，不触发 live 行情抓取（observe 测试用历史日期避开 auto-run）。
- ❌ 不写生产 `tradingagents.db`（全部用 `tempfile.TemporaryDirectory` 临时库）。
- ❌ 不改 `tradingagents/prompts/`。
- ✅ 所有端点返回的 `runtime_tier_meta.runtime_tier == "FAST_RADAR"`。
- ✅ 所有端点返回的 `runtime_tier_meta.expected_latency == "5-30s"`。
