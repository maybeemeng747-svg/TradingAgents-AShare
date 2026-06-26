# 前端 Bundle 体积趋势报告（PERF-006）
> 建立：2026-06-27 · 任务 `PERF-006` · 代码标注 `# [PERF-006] frontend_bundle_trend`
> 本报告由 `scripts/measure_frontend_bundle.py` 自动生成，记录前端构建产物的 js/css gzip 体积趋势，并给出懒加载候选建议。**本任务不强制 code split**，候选仅供后续优化参考。

_报告生成时间：2026-06-27T00:45:17_

## 1. 最新构建快照

- **记录时间**：2026-06-27T00:44:06
- **commit**：`9e0d083`
- **总 JS**：raw 1212.22 kB / gzip 348.06 kB
- **总 CSS**：raw 169.25 kB / gzip 23.23 kB
- **最大 JS chunk**：`dist/assets/index-CgqzJoWi.js` raw 1212.22 kB / gzip 348.06 kB ⚠️ **超 Vite 默认告警阈值**

## 2. 历史趋势

> 数据源：`docs/perf/frontend_bundle_trend.jsonl` （每次 `--write` 追加一行；不重写历史）。

| 记录时间 | commit | JS raw | JS gzip | CSS raw | CSS gzip | Largest JS raw | Largest JS gzip |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-06-27T00:44:06 | 9e0d083 | 1212.22 kB | 348.06 kB | 169.25 kB | 23.23 kB | 1212.22 kB | 348.06 kB |

## 3. 懒加载候选（建议，不强制执行）

> PERF-006 验收准则：**不要求实际 code split**。下列候选由源码静态分析得出，供后续优化任务参考。

| 候选 | 类型 | 源文件 | 预估影响 | 拆分理由 |
| --- | --- | --- | --- | --- |
| TradeFlow | page | `frontend/src/pages/TradeFlow.tsx` | high | 最大的页面模块（~3800 行）。仅在 /tradeflow 路由下使用，适合 React.lazy + Suspense 路由级拆分。 |
| Reports | page | `frontend/src/pages/Reports.tsx` | medium | 报告列表/详情页（~785 行），含 ReportViewer 子组件。非首屏路由，可安全懒加载。 |
| TrackingBoard | page | `frontend/src/pages/TrackingBoard.tsx` | medium | 跟踪看板路由（依赖 TrackingBoardV2Panel 等较重组件）。用户大多从 Dashboard 进入，可懒加载。 |
| Portfolio | page | `frontend/src/pages/Portfolio.tsx` | medium | 持仓页（~1343 行），含表格与图表组件。非首屏，可懒加载。 |
| Settings | page | `frontend/src/pages/Settings.tsx` | low | 设置页（~947 行），与主链路解耦，访问频率低，适合懒加载。 |
| Analysis | page | `frontend/src/pages/Analysis.tsx` | medium | 智能分析控制台（~430 行 + 多个子组件）。TradeFlow 跳转入口，可懒加载。 |
| AgentCollaboration | chart_component | `frontend/src/components/AgentCollaboration.tsx` | medium | 依赖 @xyflow/react（Flow 图视图），只在报告详情页打开。可用 React.lazy 在 ReportViewer 内部按需加载。 |
| KlinePanel | chart_component | `frontend/src/components/KlinePanel.tsx` | medium | 依赖 lightweight-charts（K 线渲染），仅在 TradeFlow/Reports 中展开。可在父组件中 React.lazy 引入。 |
| MiniKline | chart_component | `frontend/src/components/MiniKline.tsx` | medium | 依赖 lightweight-charts，在候选行/卡片中按需展开。可懒加载以避免首屏引入 chart 引擎。 |

### 3.1 推荐落地顺序（不强制）

1. **路由级 `React.lazy()`**：把 `TradeFlow / Reports / TrackingBoard / Portfolio / Settings` 等大页改为 `const TradeFlow = React.lazy(() => import('./pages/TradeFlow'))`，并用 `<Suspense fallback={…}>` 包裹路由。这是收益最大、风险最低的改造，能直接把首屏 JS gzip 拉到 ~250 kB 以下。
2. **图表组件懒加载**：`AgentCollaboration / KlinePanel / MiniKline` 依赖 `@xyflow/react` 与 `lightweight-charts`，可在父组件内通过 `React.lazy` 在用户展开时再加载，避免首屏引入 chart 引擎。
3. **vendor chunk 拆分**（可选）：在 `vite.config.ts` 中配置 `build.rollupOptions.output.manualChunks`，把 `lightweight-charts` / `@xyflow/react` / `@dnd-kit/*` 拆成独立 vendor chunk，提升缓存命中率。
4. **检查意外整包 import**：例如 `lucide-react` 务必按图标 import （`import { IconX } from 'lucide-react'`），不要 `import * as`。

## 4. 约束与运行方式

- ❌ 本任务不进行大规模前端重构。
- ❌ 本任务不改变路由行为。
- ❌ 性能测试不依赖绝对耗时，只验证解析逻辑与报告格式。
- ✅ 每次构建产物以一行 JSON 追加到 `docs/perf/frontend_bundle_trend.jsonl`，可重放历史。

### 4.1 如何刷新本报告

```bash
# 1. 在 frontend/ 跑一次构建，捕获 stdout 到文件
cd frontend && npm run build > /tmp/build.log 2>&1

# 2. 解析日志 + 追加趋势 + 重写报告
python scripts/measure_frontend_bundle.py --from-log /tmp/build.log --write

# 或者直接由脚本调用 npm run build
python scripts/measure_frontend_bundle.py --run-build --write

# 只重渲染报告（不重新构建）
python scripts/measure_frontend_bundle.py --report-only
```

