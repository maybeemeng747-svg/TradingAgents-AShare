# Run receipt — TA-TUSHARE-2000-001B

- 运行时间：2026-08-16T09:28:45+08:00
- 任务：Tushare 统一八态错误契约（P0）
- 本运行不发起任何真实 Tushare 网络请求（mock/fixture 验收），不调用 live LLM，不写生产数据库

## 交付物

- `tradingagents/dataflows/tushare_query_contract.py`：八态结构化查询契约
  （HAS_DATA / NORMAL_NO_DATA / QUERY_FAILED / PERMISSION_DENIED / RATE_LIMITED /
  FIELD_MISSING / NOT_QUERIED / STALE），每条结果携带 endpoint、查询参数摘要、
  query time、data period、response SHA-256、cache metadata、脱敏 error；
  构造器强制失败态不得携带数值 row_count/data_period，FIELD_MISSING 必须列出
  missing_fields 且 row_count ≥ 1，STALE 必须附带过期帧证据。
- `tradingagents/dataflows/providers/cn_tushare_provider.py`：所有 Tushare 查询
  （`_query`/`_optional_query`）改经 `_run_structured_query` 路由，真实状态写入
  有界（128 条）provider 审计队列 `structured_results()` / `last_structured_result()`；
  兼容文本出口（财务/资金流/龙虎榜/两融/回购）与异常语义保持不变：
  - `_optional_query` 崩塌消除：权限不足/限流/上游失败/过期缓存/未配置在审计中
    各自可区分，仅文本层维持“空表+unavailable 标注”的旧行为；
  - 上游失败但存在过期缓存副本 → 审计记 `STALE`（cache.expired=true、保留旧帧
    哈希与行数），不冒充最新数据；
  - 本地未配置（无 token/缺 tushare 包）→ `NOT_QUERIED`（upstream_called=false），
    抛出的 RuntimeError 不含 Token；
  - `get_lhb_detail(force=False)` → `NOT_QUERIED`；moneyflow 大单字段缺失 →
    `FIELD_MISSING`（missing_fields 全列）。

## 验证

- 新增 `tests/test_tushare_query_contract.py`：**33 passed**（八态全覆盖、
  权限/限流/空数据/字段缺失互不误判、业务数值 0 保留为数据、失败态不得写成 0、
  token 全链路脱敏、新鲜缓存短路、过期缓存+上游失败=STALE、审计有界且 JSON 可序列化、
  query_fn 的 NotImplementedError 不误判为 NOT_QUERIED）。
- 既有回归：`test_cn_tushare_provider.py` + `test_tushare_capability.py` +
  `test_data004_evidence_contract.py` + `test_data010_margin_trading.py` +
  `test_financial_fact_bundle.py` + `test_fund002_financial_periods.py`
  共 **334 passed**（含新契约测试），0 failed。
- `py_compile` 通过；`git diff --check` 通过。

## 凭据自检

- 新增/修改文件扫描凭据键值对与 40+ 位不透明 blob：仅命中参数名与测试函数名，无凭据。
- 对照 git-ignored `.env` 真实 TUSHARE_TOKEN（状态 HAS_KEY）逐文件包含性检查：CLEAN。
- 审计产物（`structured_results()`）不含 frame 正文与异常对象，只含 SHA-256 与脱敏文本。
