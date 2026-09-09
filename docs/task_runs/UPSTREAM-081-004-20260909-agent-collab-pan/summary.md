# Summary — UPSTREAM-081-004（round1，2026-09-09）

## 改动文件
| 文件 | 变更 |
| --- | --- |
| frontend/src/components/AgentCollaboration.tsx | +44/-9：#118 分组框 860；#203 平移/重 fit 适配移植 |
| tests/test_upstream081_agent_collaboration_ui.py | 新建，19 用例 |
| docs/task_runs/UPSTREAM-081-004-20260909-agent-collab-pan/ | task.md + summary.md |
| docs/DEVLOG.md | 追加本任务条目 |

（docs/TASKS.md 为主控簿记，本轮未触碰。）

## 测试结果（真实输出）
- `pytest tests/test_upstream081_agent_collaboration_ui.py -q --tb=short` → `.................. 19 passed in 0.04s`
- `cd frontend && npm run build` → `tsc` 通过，`✓ built in 1.46s`（chunk >500kB 提示为既有，与本轮无关）
- `npx eslint src/components/AgentCollaboration.tsx` → 0 问题

## 关键设计决策
1. **translateExtent = [[-300, -160], [2050, 900]]（上游 #203 原值）**：本地内容坐标系实测边界 x∈[-16, 1688]（组合经理 1470+218px 卡宽）、y∈[-30, 830]（group-sources -30+860）；四边余量 ≥284px，测试以保守卡高 140px（verdict 两行展开上界）逐一验证 15 个节点全部落在 extent 内。旧值 y 上限 660 < VPA 底部（630+~110）即裁切根因。
2. **窄屏回退**：摸底结论——本地已有替代展示（`lg:hidden` mobile compact workflow，4 组 button 列表，全部 15 个 agent 覆盖、点击复用 handleNodeClick），故按任务卡"若有替代则不重建"，仅加测试守卫（META 与 mobileGroups 集合相等 + 禁用语义一致），不新增视图代码。
3. **画布高度**：保留本地响应式 `h-[620px] xl:h-[810px]`（上游为固定 810）；fitView + FIT_VIEW_OPTIONS.minZoom=0.72 保证 lg 档 620px 下初始整图可见（620×0.88≈546 / 内容高≈860 → 恰在 0.72 缩放附近），xl 提到 810 对齐 #118 意图；越界部分靠放宽后的平移访问。
4. **平移 vs 点击**：nodesDraggable=false + panOnDrag=true 下，React Flow（@xyflow/react 12.10.2，d3-drag）以位移阈值区分 click/drag，拖拽后的 click 被抑制，不会误触 onNodeClick；节点点击逻辑原样保留并测试守卫。
5. **本地保留项**：`minZoom={0.35}` 组件级 prop（本地既有，上游无）、`hidden lg:block` 显隐策略、深色高密度节点样式全部未动。

## 遗留缺口
- Playwright 截图验收未做（环境未装 Playwright，任务卡要求不安装）：**截图验收由主控用浏览器工具补做**，建议覆盖 lg/xl 桌面平移 + <lg 回退视图 + 分析结束自动重 fit。
- fit 的视觉结果（fitView 在 display:none 容器中的行为、跨 lg 断点 resize 时序）属运行时行为，静态测试只能断言逻辑存在，需截图验收确认。
- 节点卡高 140px 为测试用保守上界（源码高度为内容驱动，无固定值）；若未来 verdict 行数放宽需同步调整该估计。
