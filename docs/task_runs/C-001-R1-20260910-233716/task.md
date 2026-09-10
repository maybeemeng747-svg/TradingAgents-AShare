# C-001-R1 运行档案 — 持仓推断与 HOLD 绕门禁补修

- **任务卡**：docs/TASKS.md `### C-001-R1: 持仓推断与 HOLD 绕门禁补修（P2）`
- **开始时间**：2026-09-10 23:37（本地）
- **基线 HEAD**：`b2397e0` docs(tasks): reconcile duplicate cards and release Z Code repair batch
- **执行器**：Z Code（连续模式 `/tasks run`，单执行器已确认：无 OpenCode/auto_dev_loop 进程，crontab 无本项目写入任务）
- **工作区**：起始干净（`git status --short` 为空）

## 任务卡要点

- **描述**：门禁使用显式+推断后的统一持仓上下文；正文出现"等待"不得让错误 HOLD 绕过转换。
- **允许修改**：`tradingagents/agents/utils/position_validation_gate.py`、`tradingagents/graph/intent_parser.py`、`tradingagents/graph/signal_processing.py`、对应测试与任务档案。跨模块扩大范围须先记录具体调用证据。
- **验收**：`pytest tests/test_c001_position_validation_gate.py tests/test_decision_semantics.py -q`；增加显式空仓/推断持仓/未知持仓和正文含"等待"的对抗用例，证明空仓 HOLD 转 WAIT、已持仓 HOLD 保留、入口结果一致。缺数据不得猜成有持仓。
- **依赖**：B-004-R1、F-001-R1（均 done，解析器确认 ready）。

## 复现记录（修复前，基线 b2397e0）

复现脚本：`validate_position_actions` 直调（退出码 0，输出如下）：

| 场景 | 输入 | 修复前结果 | 期望 |
|---|---|---|---|
| A. HOLD+等待绕过 | 空仓 `{"current_position": 0}`，正文 `建议 HOLD，等待回调后再考虑。` | `passed=True`，0 违规 | HOLD 违规，转 WAIT |
| B. 文本空仓无数字字段 | `{"current_position": None}`，无 position_context 通道 | `status=unknown`，不约束 | no_position，约束生效 |
| B2. 文本推断持仓 | `user_context={}`（`我持有5000股` 仅在 position_context） | `status=unknown`，不约束 | has_position，约束生效 |
| 对照 | 空仓，正文 `HOLD position.`（无等待） | 检出 HOLD 违规 | — |
| 结构层对照 | `_resolve_execution_action(None, "看多")` vs `(False, "看多")` | HOLD vs WAIT | 说明 unknown≠空仓 |

根因：
1. `position_validation_gate.py:65` HOLD 模式带负向先行 `(?!.*(?:等待|观察|条件))`，同一行后续出现"等待/观察/条件"即豁免 → 错误 HOLD 绕过转换。
2. `validate_position_actions` 只读 `user_context["current_position"]` 数值字段，不接收意图解析产出的统一持仓上下文 `state["position_context"]`（`_infer_position_context` 已折叠显式数字与否定词/持仓关键词推断）。

## 跨模块扩大范围的调用证据（任务卡要求先记录）

门禁调用点全仓 grep（`grep -rn 'validate_position_actions' --include='*.py' tradingagents/ api/`，排除定义与测试）恰好两处：

1. `tradingagents/agents/managers/research_manager.py:344` — `gate_result = validate_position_actions(full_content, user_context)`，`user_context = state.get('user_context', {})`（:175），`state` 在闭包内可用。
2. `tradingagents/agents/managers/risk_manager.py:467` — `gate_result = validate_position_actions(final_response, user_context)`；同函数 ：452 已有 `position_context=state.get("position_context")` 传给 `build_trade_quality_check` 的先例。

两处各改一行传入 `position_context=state.get("position_context")`，属于任务卡"跨模块扩大范围须先记录具体调用证据"允许的最小扩展；不合并 position_context 进 user_context（会波及 prompts/trade_setup/readiness 等全部 user_context 消费方，超出最小修改）。

## 设计

- 新增 `resolve_unified_has_position(user_context, position_context)`，优先级对齐入口契约 `api/main.py::_resolve_has_position`（position_context.has_position 且 `position_status_explicit is not False` → `current_position` → `current_position_pct` → None）。缺数据返回 None，不猜持仓。
- `validate_position_actions` 增加可选 `position_context` 参数并改用统一解析；输出契约不变。
- HOLD 模式移除"等待/观察/条件"豁免：`(r'\bHOLD\b', 'HOLD')`，未持仓 HOLD 一律违规 → 下游经 `[C-001]` 警告 → `_has_gate_failure` → `execution_action=WAIT`（空仓 HOLD 转 WAIT）；已持仓路径不涉及该模式（已持仓 HOLD 保留）。
- 误报核查：risk_manager 系统诊断块（`<!-- TA_SYSTEM_DIAGNOSTICS_START -->` 后的执行质检）只输出中文动作词（人工复核/等待触发等），无裸 `HOLD` 枚举，收紧模式不产生新误报；模型正文引用动作枚举行（如 `BUY/SELL/HOLD`）修复前同样会被检出，非新增误报类别。

## 修改文件

- `tradingagents/agents/utils/position_validation_gate.py`
- `tradingagents/agents/managers/research_manager.py`（1 行调用点）
- `tradingagents/agents/managers/risk_manager.py`（1 行调用点）
- `tests/test_c001_position_validation_gate.py`（对抗用例）
- `tests/test_decision_semantics.py`（空仓 HOLD 转 WAIT / 已持仓 HOLD 保留）
- `docs/TASKS.md`、`docs/DEVLOG.md`、本档案

## 测试与 review 记录

### 实现摘要（2026-09-10 23:50）

- `position_validation_gate.py`：新增 `resolve_unified_has_position()`；
  `validate_position_actions(text, user_context=None, position_context=None)`；
  HOLD 模式改为 `(r'\bHOLD\b', 'HOLD')` 并注明 C-001-R1 语义。
- `research_manager.py` / `risk_manager.py`：唯一两处调用点各改 1 行，
  传入 `position_context=state.get("position_context")`。
- `signal_processing.py`、`intent_parser.py`：本任务最终未修改
  （复现证明转换/推断逻辑本身正确，缺口只在门禁接线与 HOLD 豁免）。
- 测试：`tests/test_c001_position_validation_gate.py` 新增 3 类
  （TestHoldWaitBypassBlocked / TestUnifiedPositionContext /
  TestIntakeContractParity）+ 调用点接线守卫；
  `tests/test_decision_semantics.py` 新增 TestC001R1HoldWaitConversion。

### 测试结果（真实命令与退出码）

| 命令 | 结果 | 退出码 |
|---|---|---:|
| `pytest tests/test_c001_position_validation_gate.py tests/test_decision_semantics.py -q` | 112 passed | 0 |
| `pytest tests/test_p0_p1_acceptance.py tests/test_p11_has_position.py tests/test_research_manager_consensus.py tests/test_decision_replay.py tests/test_v006_decision_e2e_replay.py tests/test_astock_signal_tags.py tests/test_h001_mandate_signal.py tests/test_s001_policy_version_signal.py -q` | 237 passed, 1 failed（见下） | 1 |
| `pytest tests/test_api_smoke.py -q` | 87 passed | 0 |
| `git diff --check` | 干净 | 0 |

### 预存失败（非本任务引入）

`tests/test_decision_replay.py::TestReplay603256::test_semantics`：
断言触发价 `pytest.approx(180.0)`，样本 `603256.SH.json` 提取得 163.61。
**stash 全部修改后在基线 b2397e0 复跑同样失败**（输出：
`assert 163.61 == 180.0 ± 1.8e-04`），证明为预存问题；该文件与样本
不在 C-001-R1 允许修改范围，已在 DEVLOG 标记待人工决定。

### 对抗验证矩阵（实现后直调复现脚本，全部符合预期）

| 场景 | 结果 |
|---|---|
| 空仓 + HOLD+等待 | 违规 HOLD ✓（修复前 passed=True） |
| 文本空仓（position_context） | no_position + 违规 ✓（修复前 unknown） |
| 推断持仓：买入建议 | has_position + 违规 ✓ |
| 推断持仓：HOLD/减仓建议 | 放行 ✓ |
| 无任何证据（缺数据） | unknown 不约束，未猜持仓 ✓ |
| legacy explicit=False + 数字字段 | 正确回退数字字段 ✓ |
| 入口一致性（10 组输入 vs `_resolve_has_position`） | 全部一致 ✓ |
| 空仓 HOLD→WAIT（结构层端到端） | WAIT + GATE_BLOCKED ✓ |
| 已持仓 HOLD 保留 | HOLD ✓ |
| 真实意图解析直连门禁（3 条 query） | 空仓→no_position、持仓→has_position、无证据→unknown ✓ |

### Codex review

**状态：按用户 2026-09-10 指示统一安排，本任务不单独 review**

- 2026-09-10 23:45 首次 `codex review --uncommitted` 因 Codex 额度上限
  失败（重置 2026-09-11 04:05）。
- 随后用户指示本批次破例：连续开发完任务池后统一安排 review。
  本档案即统一 review 的材料：真实命令、退出码、对抗验证矩阵见上。
