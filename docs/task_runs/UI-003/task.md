# UI-003: 候选详情抽屉

## 目标
为 TradeFlow 候选池表格增加点击展开的详情抽屉（Drawer/Sheet），展示候选的入池理由、多空博弈、证据质检、门禁解释和后续升级条件。

## 约束
- 详情抽屉只解释信号质量，不生成投资建议
- 所有强结论必须绑定 evidence refs；缺证据时显示"缺失/未查询/失败"
- 不包含强买卖词（买入/重仓/清仓/梭哈等）
- 代码标注：`// [UI-003] tradeflow_candidate_drawer`

## 实现

### 1. 创建详情抽屉组件
创建 `frontend/src/components/TradeFlowCandidateDrawer.tsx`：

使用现有的 Sheet/Drawer 组件（如果有的话），或者用简单的侧边面板实现。

抽屉内容分 5 个区块：

**区块 1: 策略命中**
- 显示命中的策略标签（VCP, PULLBACK_SUPPORT, EVENT_CATALYST, POLICY_VERSION, NARRATIVE_QUALITY, FUND_FLOW_ANOMALY）
- 用 Badge/Tag 展示，命中高亮，未命中灰色

**区块 2: 多空博弈**
- bull_case / bear_case / policy_case / fund_flow_case
- game_balance（favorable=绿/neutral=黄/crowded=橙/fragile=红）
- resonance_count

**区块 3: 证据门禁**
- tradeflow_data_completeness（百分比进度条）
- missing_data_fields（缺失字段列表）
- fund_flow_unit_verified（✓/✗）
- event_source_status
- evidence_gate_applied（是否已应用）

**区块 4: TA 建议**
- why_deep_ta（为什么需要深度 TA）
- why_not_deep_ta（为什么不需要）
- what_to_upgrade（升级条件）
- missing_evidence_for_upgrade（缺失证据）

**区块 5: 风险标签**
- risk_flags 列表
- policy_tags 列表

### 2. 集成到 TradeFlow 页面
修改 `frontend/src/pages/TradeFlow.tsx`：
- 候选表格每行可点击
- 点击后打开详情抽屉，传入候选数据
- 从 API 获取详情（getTradeFlowCandidateDetail）或使用列表数据

### 3. 验证
```bash
cd frontend && npm run build
```

## 文件变更
- `frontend/src/components/TradeFlowCandidateDrawer.tsx`（新建）
- `frontend/src/pages/TradeFlow.tsx`（修改）
