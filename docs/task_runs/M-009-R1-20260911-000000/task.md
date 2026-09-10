# M-009-R1 运行档案 — 观察池独立数据源补修

- **任务卡**：docs/TASKS.md `### M-009-R1: 观察池独立数据源补修（P2）`
- **开始时间**：2026-09-11（本地，连续模式第 5 项）
- **基线 HEAD**：`ba5b5df`（HY-009-R1 实现提交）
- **批次说明**：按用户 2026-09-10 破例指示，本批次 review 统一安排。

## 任务卡要点

- **描述**：观察池不得继承候选页隐藏筛选条件，必须读取完整观察状态集合。
- **允许修改**：`frontend/src/pages/TradeFlow.tsx`、相关 API client/types 和只读观察池 service、对应测试及任务档案。
- **验收**：前端定向测试与 `npm run build`；候选页筛选后进入观察池仍显示完整观察集合；分页、空态、请求失败、快速切换不串数据。用离线 mock UI 截图验证桌面/手机，不触发分析或观察执行接口。

## 复现记录（修复前，基线 ba5b5df）

代码路径复现（TradeFlow.tsx:2641-2669 + 2891-2894）：

- 观察池 tab（`watch-pool`）复用 `fetchCandidates(date)`；
- `fetchCandidates` 把候选页筛选传给服务端：
  `api.getTradeFlowCandidates(date, tierFilter, deepTaFilter…, candidateTypeFilter, poolFilter…)`；
- 随后还套客户端过滤 `items.filter(c => c.observe_state === observeFilter)`。

后果：用户在候选页设过 tier=A / pool=main / observeFilter=TRIGGERED 后
切到观察池，观察池只显示"A 层 × 主池 × 已触发"的子集，完整观察状态
集合丢失。

## 设计与修改文件

- 新增 `frontend/src/services/watchPoolService.ts`（只读观察池 service）：
  - `extractWatchPoolCandidates(res)`：完整集合提取口径（不做
    observe_state 过滤；函数签名只接收 response，候选页筛选在类型层面
    无法进入观察池数据路径）；
  - `createLatestRequestGuard()`：单调请求序号守卫，快速切换时过期
    响应不得覆盖新数据。
- `frontend/src/pages/TradeFlow.tsx`：
  - 新增 `watchPoolCandidates` 状态 + `watchPoolRequestGuardRef`；
  - 新增 `fetchWatchPool(date)`：调 `api.getTradeFlowCandidates(date)`
    不带任何筛选参数（API client 参数本就全部可选，client 无需改动），
    响应/错误/finally 均经守卫校验（请求失败进共享 error，快速切换
    丢弃过期响应）；
  - `fetchData` 的 `watch-pool` 分支改用 `fetchWatchPool + fetchFiltered`，
    不再经过 `fetchCandidates`；
  - `WatchPoolTab` 的 `candidates` prop 改绑 `watchPoolCandidates`；
  - `WatchPoolTab` export（供离线 mock 验收）。
- 空态/分页：空态逻辑（candidates+filteredItems 均空 → "尚无观察池
  数据"；观察中空 → "无观察中候选"）不变；表格横向滚动分页不变；
  数据源解耦后这些路径消费的就是完整集合。
- mock 验收辅助文件：`frontend/watchpool-mock.html` +
  `frontend/src/watchPoolMockEntry.tsx`（纯 mock 数据渲染真实
  WatchPoolTab，零 API 调用）。

## 测试结果

| 命令 | 结果 | 退出码 |
|---|---|---:|
| `npx vitest run src/services/watchPoolService.test.ts` | 5 passed（完整集合/非 ok 空集/筛选参数类型隔离/守卫时序/慢响应不覆盖） | 0 |
| `npx tsc --noEmit -p tsconfig.json` | 无错误 | 0 |
| `npm run build` | ✓ built in 1.49s | 0 |
| 离线 mock UI 截图（桌面 1280×860 + 手机 390×844，纯 mock 数据，零 API 调用） | 3 张存于本档案目录：mock-desktop-watching.png / mock-desktop-filtered.png / mock-mobile-watching.png | — |

截图验证要点：候选页筛选场景（tier=A / pool=main /
observeFilter=TRIGGERED）下观察池仍显示完整观察状态集合——摘要卡
观察中 3 / 已触发 1 / 需深度 TA 1 / 被过滤 2 / 策略标签 6，表格含
WAITING/TRIGGERED（INVALIDATED/EXPIRED 计入被过滤视图）；被过滤视图
正常；手机视口卡片堆叠、表格横向滚动。全程未触发分析或观察执行接口。

## Codex review

按用户 2026-09-10 指示统一安排，本任务不单独 review；本档案即
统一 review 材料。
