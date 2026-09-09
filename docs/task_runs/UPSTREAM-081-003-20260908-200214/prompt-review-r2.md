你是独立代码审查者。任务：UPSTREAM-081-003 — AKShare v0.8.1 差异审计与最小修复。仓库：/Users/maybee/TradingAgents-AShare。只读审查。

## 背景
- Round1（docs/task_runs/UPSTREAM-081-003-20260908-200214/summary.md）被协调员停止：OpenCode 把 `get_lhb_detail` 所有 TypeError 归类 LHB_NORMAL_NO_DATA，破坏 fail-closed。
- Round2（重派）结论：**零 provider 生产代码移植**——round1 的 10 行错误改动已 `git restore` 还原；审计认定本地实现已是上游 `aa797bca`(#195) 修复的超集或等价，无真实缺口；仅交付测试重写 + 审计文档。
- 注意：本轮交付物 = 测试 + 审计文档 + 日志，**不含 provider 生产代码改动**——审查重点是"零移植"结论是否有据、fail-closed 是否真正恢复。

## 审查对象
- tests/test_upstream081_akshare_gap.py（重写，26 用例，fail-closed 语义）
- docs/data_source_reports/akshare-v0.8.1-upstream-audit-2026-09-09.md（审计文档，5 接口逐项结论+live 探测证据）
- docs/DEVLOG.md、docs/task_runs/UPSTREAM-081-003-20260908-200214/（round1 中止+round2 记录）
- 对照：`git diff` 应仅含 docs/DEVLOG.md 与 docs/TASKS.md（簿记豁免）修改 + 上述新文件；`cn_akshare_provider.py` 相对 HEAD **应无 diff**（round1 改动已还原）——请验证。

## 必须逐项核验（引用文件:行）
1. round1 还原彻底性：`git diff -- tradingagents/dataflows/providers/cn_akshare_provider.py` 为空；`get_lhb_detail` 的 TypeError 处理恢复 fail-closed（LHB_FAILED/降级而非 NORMAL_NO_DATA）；被反转的回归断言（test_upstream_v081_absorption.py 的 fail-closed 用例）已复位。
2. "零移植"结论逐接口有据：雪球 token/全球新闻/行业资金流/龙虎榜参数/热门接口——审计文档的证据（签名实测、live 探测行数）是否支撑结论；有无上游真实修复被漏判。
3. 测试质量：26 用例抽 4-5 个看断言（fail-closed 分类、空数据/异常/权限分别归类、不把全市场龙虎榜当个股数据）；无对生产 DB/凭据引用；无全市场扫描。
4. 范围与约束：无 prompts/、生产 DB、scheduler、门禁、.env.example 改动；无新增第三方依赖；TASKS.md 簿记豁免。
5. 审计文档可复现性：live 探测的命令/参数是否记录到可重跑；结论与证据是否一一对应。

## 已知事实
- 主控本地已实测：153 passed（66 差异+吸收 + 87 api_smoke）。
- round2 使用了 5 次低频只读 live 探测（任务卡允许的"真实小范围证据"）。

## 允许操作
只读；可运行 pytest（沙箱临时目录受限用 -s 说明）；可做只读 python/rg 抽查。不得修改文件、不得 commit。

## 输出（中文，stdout）
- 第一行：`结论: PASS` 或 `结论: FAIL`
- 按 1-5 逐条核验（引用文件:行）
- findings（P0/P1/P2，引用文件:行；没有写"无"）
- 实际运行的命令与结果
