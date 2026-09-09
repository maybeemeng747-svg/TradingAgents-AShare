# Task: UPSTREAM-081-004 — Agent 协同图平移与节点完整显示（P2）

参考上游提交（均在本地仓库，`git show` 查看）：
- `74b22ad`（#118 量价分析节点显示不全）：分组框 760→860、画布 700→810、translateExtent y 660→780
- `06a0e28`（#203 协同工作流完整平移）：onInit + flowInstanceRef、FIT_VIEW_OPTIONS、分析结束/resize 后重新 fit、translateExtent 放宽

## 关键约束
- 本地文件已与上游分叉（响应式高度、hidden lg:block、已有 mobile compact workflow、minZoom 0.35），禁止盲 cherry-pick，需适配移植。
- 窄屏（<lg）回退：先摸底——本地 447-494 行已有 `lg:hidden` mobile compact workflow（4 组、15 个 agent 全覆盖、button 可点击），故无需新建回退视图，测试验证其覆盖完整性即可。
- 允许改动：AgentCollaboration.tsx、新测试文件、task_runs 记录、DEVLOG 一行。
- 禁止：TASKS.md、依赖安装、git commit、Agent 编排/后端/主题。

## 实施记录（OpenCode round1）
1. `group-sources` height 760→860（#118 直译，容纳 7 分析师 + verdict 展开）。
2. 移植 #203：imports（useEffect/useRef/ReactFlowInstance）、`FIT_VIEW_OPTIONS{padding:0.06,minZoom:0.72,maxZoom:1}`、`CollaborationNode` 类型别名、`flowInstanceRef` + `fitGraph(duration=300)`、isAnalyzing true→false 后 requestAnimationFrame 重新 fit、window resize 120ms 防抖 fitGraph(0)、onInit 捕获实例、`panOnDrag={true}` 显式化、translateExtent `[[-300,-160],[2050,900]]`。
3. 适配点（非照抄）：
   - 画布高度保持响应式 `hidden lg:block h-[620px] xl:h-[810px]`（本地无固定 700→810；xl 700→810 对齐 #118 意图，lg 620 保留高密度），fitView + minZoom 0.72 保证各档高度下初始完整呈现；
   - 保留本地 `minZoom={0.35} maxZoom={1}` 组件级 props（上游没有，本地新增，不动）；
   - 保留 fitView 布尔 prop，`fitViewOptions` 换成 FIT_VIEW_OPTIONS（原 `{padding:0.08, includeHiddenNodes:false}`）。
4. 新测试 `tests/test_upstream081_agent_collaboration_ui.py`（19 用例）：源码级断言（解析 NODE_POSITIONS/GROUP_LABELS/translateExtent/META/mobileGroups 字面量）+ 纯几何逻辑单测（content_bounds、extent 覆盖全部 15 节点坐标边界）。

## 验证
- `pytest tests/test_upstream081_agent_collaboration_ui.py -q --tb=short` → 19 passed
- `cd frontend && npm run build` → tsc + vite 通过（chunk>500kB 警告为既有）
- `npx eslint src/components/AgentCollaboration.tsx` → 0 问题
- Playwright 截图：环境未装，由主控用浏览器工具补做
