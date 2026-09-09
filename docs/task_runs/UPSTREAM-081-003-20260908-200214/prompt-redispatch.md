# Task: UPSTREAM-081-003 re-dispatch — 按正确方向重新实现（round1 方向已被协调员否决）

Read task "UPSTREAM-081-003" from docs/TASKS.md and read
docs/task_runs/UPSTREAM-081-003-20260908-200214/summary.md（round1 被停原因）.

## 背景：round1 为什么被停（必须先读懂）
- Round1 OpenCode 把 `get_lhb_detail` 的**所有 TypeError 一律捕获并归类为 LHB_NORMAL_NO_DATA**——这会把畸形响应、处理 bug 等真实故障伪装成"正常无数据"，**反转了既有 fail-closed 回归**。
- Live 探测：两个日期的龙虎榜数据**实际可取到**；round1 假设的"未发布数据失败"场景没有复现，不得围绕未经验证的失败模式写代码。
- 协调员停止进程树，所有变更保留在工作区（未提交）。HEAD = ad97779。

## 本次方向（先读懂再动手）
1. **先审工作区既有 diff**（cn_akshare_provider.py +10 行、tests 两处）：凡属"吞 TypeError 归类 NORMAL_NO_DATA"方向的改动**一律纠正**——恢复严格分类：只有上游明确返回空/无记录才算 NORMAL_NO_DATA；TypeError、畸形响应、签名不匹配必须走 QUERY_FAILED/明确错误态并触发既有降级路径，不得静默吞掉。
2. 回到任务卡本义做**差异审计**：先记录当前 AKShare 版本与相关函数签名；对雪球 token、全球新闻、行业资金流、龙虎榜参数、热门数据接口逐项对比上游 v0.8.1（`aa79bca`）与本地实现，只移植仍有效且有真实小范围证据的修复；接口不存在、签名漂移、空数据、权限失败**分别归类**。
3. 不得把龙虎榜全市场结果直接当目标股票数据；不得默认扩大查询范围。
4. tests/test_upstream081_akshare_gap.py（round1 未完成）按正确语义补完；恢复被反转的 fail-closed 回归断言（test_upstream_v081_absorption.py 有被改动的回归，核对是否需要还原）。

## 验证
- `pytest tests/test_upstream081_akshare_gap.py tests/test_upstream_v081_absorption.py -q --tb=short`
- `pytest tests/test_api_smoke.py -q --tb=short`（回归）
- 不做全市场扫描、不做个股深度 TA、不写生产 DB

## Constraints（不变）
- No changes to tradingagents/prompts/
- No push / PR / merge
- **Do NOT git commit** — 等待独立 Codex review
- Do not update docs/TASKS.md 状态（主控管理）
- docs/DEVLOG.md 追加 re-dispatch 条目；docs/task_runs/UPSTREAM-081-003-20260908-200214/ 下追加本轮记录

## Output when done
- 既有 diff 的处置清单（保留/纠正/还原，逐项）
- 差异审计结论（逐接口：上游改动/本地已有/真实缺口）
- 测试结果（贴 pytest 汇总行）
