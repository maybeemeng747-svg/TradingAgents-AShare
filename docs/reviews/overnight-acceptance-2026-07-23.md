# 2026-07-22 夜间自动开发验收

## 结论

- 时间窗口：2026-07-22 20:20 至 2026-07-23 06:29（Asia/Shanghai）。
- 产出：25 个 `auto:` 实现提交，另有对应状态提交。
- 真正通过最终 Codex correctness 验收：`C-004` 1 项。
- `C-008` 仅新增测试/文档，final review 干净，但未形成生产实现提交。
- 其余 24 个实现提交均保留至少 1 个 P1/P2 finding，当前不得视为生产验收通过。
- 未 push、未调用 live LLM、未写生产数据库、未自动重跑 603629。

## 自动验收误判根因

旧 `scripts/auto_dev_loop.sh` 使用：

```bash
echo "$REVIEW_CONTENT" | grep -qiE "(P0|P1|critical|must.fix|blocker)"
```

在 `set -o pipefail` 下，大 review 命中后 `grep -q` 提前退出，`echo` 遭遇 SIGPIPE，管道返回非零，导致 `HAS_CRITICAL=false`。此外，整份日志包含任务上下文/代码回显，且旧规则未阻断 P2。结果是 review 明确列出 finding，脚本仍写入 PASS 并提交。

`AUTO-008`（`0901b91`）已改为：

1. 只解析最后一个独立 `codex` 段落。
2. P0/P1/P2 进入修复轮；P3 仅归档。
3. UNKNOWN、缺 marker、review 超时、配置/额度错误一律 fail closed。
4. 覆盖常见 clean wording 与 2 MB review 的 SIGPIPE 回归。

## 高优先级 findings

| 任务 | 严重度 | 验收 finding | 修复任务 |
|---|---|---|---|
| FUND-004B | P1 | 利润与经营现金流仍可能从不同财务组选择 | FUND-004B-R1 |
| FUND-004A | P1 | 复用预计算 integrity 时 `period_facts` 未定义 | FUND-004A-R1 |
| FUND-005A | P1 | actual model 缺失时伪装成 requested model | FUND-005A-R1 |
| SCORE-001B | P1/P2 | 历史查询未来泄漏；动作词和 FAILED 状态处理错误 | SCORE-001B-R1 |
| SCORE-002 | P1/P2 | 评分卡未进入真实响应；risk penalty 未持久化 | SCORE-002-R1 |
| SCORE-003 | P1/P2 | 卡片未暴露；缺账户上下文被判高适配 | SCORE-003-R1 |
| B-001 | P1/P2 | MiMo key/base/model 在完整图路径未一致接入 | B-001-R1 |
| B-004 | P1/P2 | 任意路径写入、失败仍成功、非法输入 500 | B-004-R1 |
| C-003 | P1/P2 | 多头退出被误删；流式输出先暴露未清洗文本 | C-003-R1 |
| C-007 | P1/P2 | 事件门禁只追加告警，不覆盖 BUY | C-007-R1 |
| M-010 | P1/P2 | HTTP 异常可能泄漏 webhook；渠道未限制 | M-010-R1 |

## 第二批 findings

- FUND-006A：七类离线回放测试未真正约束生产行为。
- FUND-007A：漏识别“所致”等因果短语，部分断言过弱。
- F-001：未持仓试错 Buy Level 可进入 3。
- PLAYBOOK-002：计划仓位未传入检查、首次大跌仍允许进攻、缺试错下行上限。
- SCORE-001：验收文档引用不可达 commit，运行档案仍为 CLAIMED。
- B-002/B-003：调度回调丢字段；飞书文档链接/根节点写入不可靠。
- C-001/C-005/C-006：持仓推断绕门禁、止损误判研究方向、财务质量指标未接生产 normalizer。
- HY-009：expiry-only 缓存、分页后冲突检查、现金流冲突状态均有缺口。
- M-009：观察池继承候选页隐藏筛选。
- UI-014：前端读取 fixture-only schema、切换标的显示旧数据、失败后无限重试。

## 验证结果

- 夜间新增 Python 测试集合：1179 passed，2 个顺序敏感失败。
- 失败位于 M-010 webhook 异步测试：依赖全局 event loop；单独运行 41 passed 但出现弃用警告，已纳入 M-010-R1。
- 前端：`npx tsc --noEmit` 通过；Vitest 114 passed；生产构建通过。
- AUTO-008：140 passed；`bash -n scripts/auto_dev_loop.sh`、`git diff --check` 通过；两轮独立 Codex 复核后无 correctness finding。

## 放行规则

历史实现提交保留以便增量补修，不回滚用户代码；但在对应 `*-R1` 完成前，不得标记为产品验收通过。当前只释放 `FUND-004B-R1`，其余任务按依赖自动解锁。SCORE-004/005/006、V-014/V-015 和新功能开发暂缓。
