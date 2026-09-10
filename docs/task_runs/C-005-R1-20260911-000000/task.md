# C-005-R1 运行档案 — 止损文本方向识别补修

- **任务卡**：docs/TASKS.md `### C-005-R1: 止损文本方向识别补修（P2）`
- **开始时间**：2026-09-11（本地，连续模式第 2 项）
- **基线 HEAD**：`145902c`（C-001-R1 实现提交）
- **批次说明**：按用户 2026-09-10 破例指示，本批次 review 统一安排；
  done = 代码已提交 + 任务指定测试通过。

## 任务卡要点

- **描述**：止损条件属于风险控制，不得单独把研究方向判成偏空。
- **允许修改**：`tradingagents/graph/signal_processing.py`、delta/decision
  相关工具及对应测试、任务档案。
- **验收**：`pytest tests/test_c005_delta_check.py tests/test_decision_semantics.py -q`；
  看多正文附止损条件仍看多，真实看空/退出不被吞掉，否定词和系统覆盖
  文字不污染研究方向，回放持仓及未持仓两类输出。

## 复现记录（修复前，基线 145902c）

`_extract_direction` 直调复现（`signal_processing._infer_research_direction` 同场对照均正确，证明缺口只在 delta_check）：

| 场景 | 文本 | 修复前 | 期望 |
|---|---|---|---|
| A 看多+止损字段 | `看多，建议买入，止损位设在150元。` | bearish | bullish |
| B 看多+止损条件句 | `突破180元买入做多；若跌破150元触发止损，则离场。` | bearish | bullish |
| C 否定卖出 | `不建议卖出，继续持有，回调可加仓。` | bearish | 非 bearish |
| D 枚举文档+看多 | `可选动作：ENTER/WAIT/...`+看多正文 | neutral | bullish |
| E 真实看空 | `看空，建议清仓离场。` | bearish ✓ | 保持 |
| F 止损离场 | `虽然基本面看多，但建议止损离场` | bearish ✓ | 保持 |

根因（`delta_check.py`）：`_BEARISH_KEYWORDS` 与 `strong_bear` 含裸"止损"；
关键词匹配无否定感知；系统追加区块（含上一次保存的 [C-005] 警告，
其中带"看多 → 看空"字样）与动作枚举文档行均参与匹配。

## 设计与修改文件

仅改 `tradingagents/agents/utils/delta_check.py`（任务卡允许的 delta 工具）：

1. 裸"止损"移出 `_BEARISH_KEYWORDS` 与 strong_bear，改为显式退出短语
   "止损离场/止损出局/止损清仓"；真实退出（E/F）保持 bearish。
2. 新增 `_strip_system_blocks()`：方向提取前剥离 ⚠️/📊 系统区块、
   执行质检/证据门禁节、系统诊断起点、动作枚举文档行
   （含裸 `ENTER/WAIT/HOLD/REDUCE/EXIT` 行）。
3. 新增 `_has_effective_keyword()` 否定感知匹配：否定词必须锚定在
   关键词前（`(不|未|勿|无)(宜|应|该|要|建议|…)?` 或
   避免/暂缓/放弃/取消/禁止/停止，窗口 6）。
   迭代中发现并修掉的边界：初版"包含式"窗口把"反弹无力，止损出局"
   的"无力"误判否定（案例 J），"特别建议买入"会被"别"误伤——
   故改为锚定式 + 弃用单字"别"。
4. 混合信号强度判定新增"建议性卖出指令"概念：强买入指令
   （强烈/积极/建议买入/建仓）+ 仅附止损保护条件（无
   建议卖出/清仓/减仓/止损/离场/果断止损）→ 仍判 bullish；
   "若跌破180建议止损离场"这类建议性退出保持保守 bearish。

`signal_processing.py`、`intent_parser.py` 未修改（复现证明其方向
提取正确）。

## 测试结果

| 命令 | 结果 | 退出码 |
|---|---|---:|
| `pytest tests/test_c005_delta_check.py tests/test_decision_semantics.py -q`（验收） | 109 passed（新增 13 项对抗：A-M 矩阵 + 虚假翻转回放 + 持仓/未持仓回放） | 0 |
| `pytest tests/test_c001_position_validation_gate.py tests/test_p0_p1_acceptance.py tests/test_p11_has_position.py tests/test_research_manager_consensus.py tests/test_v006_decision_e2e_replay.py tests/test_astock_signal_tags.py tests/test_h001_mandate_signal.py tests/test_s001_policy_version_signal.py -q` | 295 passed | 0 |

13 项对抗矩阵（A 看多+止损字段 … M 强买+保护止损）全部符合预期；
虚假翻转回放：上一版看多 + 新版看多附止损条件 → `check_delta` 返回
None（修复前产生 bullish→bearish 假警告）；真实翻转（看多→看空清仓）
仍被检出。

## Codex review

按用户 2026-09-10 指示统一安排，本任务不单独 review；本档案即
统一 review 材料。
