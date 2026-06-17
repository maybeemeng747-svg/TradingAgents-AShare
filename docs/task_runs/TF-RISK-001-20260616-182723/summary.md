# TF-RISK-001 — 5000 元试跑风险预算与仓位纪律

## 状态
✅ 完成

## 执行者
OpenCode

## 摘要
为 TF-PAPER-001 的 paper ledger 增加风险预算与仓位纪律：加入模拟跟踪时强制触发价/
失效价/数据质量门槛（硬拒绝），并对单票金额、单日新增、并发跟踪、剩余预算施加约束
（超额降级为仅观察，不可成交）。账本 summary 暴露 risk_exposure；前端展示风险预算卡片。

## 校验规则
- require_trigger_price（默认 true）→ 缺失 rejected
- require_invalid_price（默认 true）→ 缺失 rejected
- min_data_quality_score（默认 40）→ 低于 rejected
- per_ticket_max（默认 1500）→ 金额截断
- daily_new_max（默认 3）→ observation 降级
- max_concurrent_tracking（默认 5）→ observation 降级
- 剩余预算不足 → observation 降级

## 修改文件
- api/services/tradeflow_service.py
- api/tradeflow_schemas.py
- api/main.py
- frontend/src/types/index.ts
- frontend/src/pages/TradeFlow.tsx
- tests/test_tf_risk001_paper_risk_budget.py（新增）
- tests/test_tf_paper001_paper_ledger.py（既有用例适配）
- docs/TASKS.md, docs/DEVLOG.md

## 测试结果
- targeted（新+既有 paper ledger）: 81 passed
- tradeflow 全量: 827 passed
- 全量回归: 5867 passed, 17 skipped
- 前端 tsc --noEmit: 通过

## 约束遵守
不接真实券商 / 不输出强买卖词 / 不调用 LLM / 未触碰生产库与 eval_results/prompts/logs。
