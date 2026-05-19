# 任务池

> 最后更新：2026-05-13

---

## A. 当前进行中

### F-001: TA研报执行层修复（P0-P2共10项）
- **描述**：修复002837英维克报告暴露的10个问题，涉及止损价解析、估值sanity check、Opportunity Score封顶、未持仓语义、Buy/Risk Level冲突、舆情VERDICT矛盾、入场区间误识别、打法标签、A股做空措辞、Evidence Coverage保守化
- **优先级**：P0（1-3）+ P1（4-7）+ P2（8-10）
- **验收**：重跑002837报告，10项全部通过
- **状态**：已派发OpenCode执行

---

## B. 待办

### B-001: 接入小米 MiMo 模型到 TA 系统
- **描述**：在 `llm_clients/` 中添加小米 MiMo 适配器，支持 mimo-v2.5 和 mimo-v2.5-pro
- **优先级**：高
- **前置条件**：无
- **验证方式**：前端设置页切换到小米模型，执行一次分析任务成功

### B-002: 定时任务与 OpenClaw 联动
- **描述**：定时分析完成后自动通知 OpenClaw，由主控 AI 决定是否推送到飞书
- **优先级**：中
- **前置条件**：OpenClaw API 可达
- **验证方式**：定时任务完成后飞书收到通知

### B-003: 研报导出为飞书文档
- **描述**：分析结果支持导出为飞书云文档，方便分享和存档
- **优先级**：中
- **前置条件**：飞书 API 权限
- **验证方式**：点击导出按钮，飞书生成对应文档

### B-004: 持仓快照与 investment-controller 同步
- **描述**：TA 系统的持仓数据与 investment-controller 项目的 current_holdings.json 双向同步
- **优先级**：中
- **前置条件**：investment-controller API 可达
- **验证方式**：两边持仓数据一致

---

## C. 报告质量防线（2026-05-10 新增）

> 以下 8 项基于 ChatGPT 建议 + 主控AI评估，按优先级排列。每项标注了实现要点和注意事项。

### C-001: position_validation_gate（P0 — 最高优先）
- **描述**：每份报告生成前必须读取 `current_positions.json`，根据持仓状态决定可输出的动作类型
- **实现要点**：
  - 文件不存在或为空 → 所有持仓相关策略（减仓/清仓/持有/止损/止盈）一律禁止输出
  - 有持仓 → 允许输出持有、减仓、止损、止盈
  - 无持仓 → 只允许输出观望、建仓、回避
- **代码标注要求**：在 `research_manager.py` 和 `risk_mgmt/` 入口处加 `# [C-001] position_validation_gate` 注释
- **验证方式**：无持仓时触发分析，确认不输出"减仓/清仓"

### C-002: account_capability 配置（P1）
- **描述**：增加账户能力配置，控制可交易品种
- **实现要点**：
  - 配置项：`can_buy_kcb`（科创板）、`can_buy_chinext`（创业板）、`can_short`（做空，默认 false）
  - 不设 `max_single_stock_position` 和 `max_daily_new_position`（交给风控 Agent 动态判断）
  - 配置文件：`tradingagents/default_config.py` 或 `.env`
- **代码标注要求**：`# [C-002] account_capability`
- **验证方式**：`can_short=false` 时不生成做空策略

### C-003: 禁止做空策略输出（P1）
- **描述**：如果 `can_short=false`，做空相关策略在生成阶段就不进入候选池
- **实现要点**：
  - 不是生成后转换措辞，而是从候选策略池直接移除
  - 策略池定义在 `prompts/` 中，过滤逻辑在 `researchers/` 入口
  - 涉及关键词：试空、做空、平空、空头开仓、融券卖出
- **代码标注要求**：`# [C-003] short_filter`
- **验证方式**：`can_short=false` 时，报告中不含任何做空相关表述

### C-004: 动作枚举重设计（P1）
- **描述**：将最终动作枚举精简为 5 个
- **枚举值**：
  - `WAIT` — 观望/等条件（含回避，备注里说明原因）
  - `ENTER` — 建仓
  - `HOLD` — 持有
  - `REDUCE` — 减仓/止损/止盈
  - `EXIT` — 清仓
- **实现要点**：
  - 替换现有的动作枚举定义
  - 更新所有引用动作枚举的代码
  - 报告输出格式同步更新
- **代码标注要求**：`# [C-004] action_enum`
- **验证方式**：所有报告输出的动作值都在 5 个枚举内

### C-005: same_symbol_delta_check（P2）
- **描述**：同一股票结论翻转时，必须输出对比信息
- **实现要点**：
  - 时间窗口：72 小时（不是 48 小时，避免短线正常波动误报）
  - 触发条件：结论翻转 **且** 没有新增数据源
  - 输出内容：上一版结论、本版结论、变化原因、新增证据、是否可能是模型噪音
  - 存储：`portfolio/analysis/delta_log/{stock_code}.json`
- **代码标注要求**：`# [C-005] delta_check`
- **验证方式**：同一股票 72 小时内结论翻转时，报告包含对比信息

### C-006: financial_data_validator — 分期实现（P2）
- **描述**：财报数据异常检测，分两期做
- **一期（规则检测）**：
  - 毛利率跳变 > 20% → 标记
  - 现金流与利润背离（利润增但经营现金流减）→ 标记
  - 资产负债率异常（> 70% 或环比变化 > 10%）→ 标记
  - 大额投资/筹资异动 → 标记
- **二期（精确计算）**：
  - 所有同比、环比由程序计算
  - 财务单位校验
- **代码标注要求**：`# [C-006] financial_validator`
- **验证方式**：检测到异常时，报告中明确标注"需人工复核"

### C-007: event_risk_gate（P1）
- **描述**：重大事件发生时，进入风控优先模式
- **事件类型**：
  - 大比例解禁（解禁比例 > 流通股本 5%）
  - 重大并购/重组
  - 高杠杆（资产负债率 > 80%）
  - 停复牌
  - 业绩暴雷（净利润下降 > 50%）
- **实现要点**：
  - 数据源：AKShare `stock_restricted_release_queue_em`（解禁）、yfinance（停复牌）
  - 事件落地前，除非主控 AI 特批，否则不允许开仓
  - 不确定的数据源宁可不接，不要输出错误信息
- **代码标注要求**：`# [C-007] event_risk_gate`
- **验证方式**：检测到解禁事件时，报告标注"风控优先模式，不建议开仓"

### C-008: execution_readiness_score — 简化版（P2）
- **描述**：每份报告输出两个核心质量指标
- **指标**：
  - `data_completeness` — 数据完整度（源数据覆盖了多少需要的字段，0-100%）
  - `confidence` — 置信度（高/中/低，由 Agent 自评）
- **不做的维度**（等系统跑起来再加）：
  - ~~逻辑可信度~~（与置信度高度相关）
  - ~~是否适合新手~~（用户属性，不该在报告里写死）
  - ~~是否需要主控AI复核~~（这个判断本身需要另一个模型）
- **代码标注要求**：`# [C-008] readiness_score`
- **验证方式**：每份报告末尾包含 `data_completeness` 和 `confidence` 字段

---

## C 系列实施顺序

```
Phase 1（立即做）：C-001 + C-002 + C-003 + C-004
  ↓
Phase 2（稳定后）：C-005 + C-007
  ↓
Phase 3（优化期）：C-006 + C-008
```

---

## D. 修正评分与强动作门禁（2026-05-13 新增）

### D-001: 修正数据完整度评分 ✅ 已完成（含真证据修复）
- **描述**：risk_manager.py 从 state 传入全部 8 项数据源；evidence 不再因报告存在就标 HAS_DATA，需匹配具体原始字段
- **验证方式**：只有 4 个基础报告时 source_coverage=50%；纯文本报告无 OHLC 表格时 evidence 不虚高

### D-002: 强结论证据门禁 ✅ 已完成（含真降级修复）
- **描述**：新增 source_coverage/evidence_coverage 双维度评分；get_strong_action_gate 检查 7 项条件；gate 未通过时真正移除/替换强动作文本
- **验证方式**：coverage<70 时强动作被替换为中性表达，原关键词不再出现在最终报告

### D-003: Risk Level / Buy Level 双等级输出 ✅ 已完成（含真信号修复）
- **描述**：Risk Level 0-4 + Buy Level 0-4；从报告文本提取 14 项交易信号传入计算；持仓未知时 gate 语义修正
- **验证方式**：Level 4 需全部条件满足；持仓未知+无强动作时 gate 通过

### D-004: Opportunity Score 机会评分 ✅ 已完成（含真信号修复）
- **描述**：5 维加权 Opportunity Score 0-100；从报告文本提取趋势/资金共振/催化/盈亏比/入场质量信号传入计算
- **验证方式**：Opportunity Score=100 但 coverage<70 时 Buy Level 仍受限；正向信号文本下 Score 明显高于默认值

---

## E. 基础设施修复（2026-05-13 新增）

### E-001: 运行版本戳 ✅ 已完成
- **描述**：报告末尾追加系统版本区块（git commit hash + 后端启动时间 + 报告生成时间 + 评分版本标签）
- **实现要点**：`api/main.py` 启动时用 `subprocess` 读 git hash 并缓存；`readiness_score.py` 的 `format_readiness_score` 调用 `_format_version_block()`
- **验证方式**：报告末尾包含 `### 系统版本` 区块

### E-002: 代码→名称错配校验 ✅ 已完成
- **描述**：新增 `validate_stock_name()` 名称校验函数，名称不匹配时 `name_mismatch=True`，Level 4 被降级到最高 3
- **实现要点**：`get_strong_action_gate` 增加 `name_mismatch` 参数；`calculate_risk_level`/`calculate_buy_level` Level 4 降级
- **验证方式**：`name_mismatch=True` 时 gate 不通过，Level ≤ 3

### E-003: 执行质检解析器止损关键词扩展 ✅ 已完成
- **描述**：扩展止损关键词识别（止损红线/止损位/止损价/清仓线/止损线）；区分入场区间和减仓区间；新增 `execution_zone_conflict` 冲突检测
- **实现要点**：`extract_execution_signals` 返回 `has_stop_loss` 和 `execution_zone_conflict`；`get_strong_action_gate` 增加 `no_execution_zone_conflict` 参数
- **验证方式**：文本含"止损红线 25.50" → `has_stop_loss=True`；分离语境的入场区间+减仓区间不冲突（如入场28-30+减仓35-38）；区间重叠或止损价高于入场价才触发 `execution_zone_conflict`

### E-004: 原始证据表数据源接入 ✅ 已完成
- **描述**：`infer_evidence_statuses` 优先从 DataCollector 缓存池的结构化数据读取证据状态，fallback 到文本正则
- **实现要点**：DataCollector 新增 `build_raw_evidence()` 方法；`trading_graph.py` 将 raw_evidence 存入 state metadata；`infer_evidence_statuses` 接受 `raw_evidence` 参数
- **验证方式**：有 raw_evidence 时直接从结构化数据判断 OHLCV/fund_flow/lhb/news 状态；无 raw_evidence 时 fallback 到文本正则
