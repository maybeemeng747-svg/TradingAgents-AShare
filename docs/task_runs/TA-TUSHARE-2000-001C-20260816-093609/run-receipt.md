# Run receipt — TA-TUSHARE-2000-001C

- 运行时间：2026-08-16T09:36:09+08:00
- 任务：知识库研究证据包补齐（P1）
- 本运行为 **fixture 模式**（`--no-dotenv`，collection_mode=fixture，token_status=NO_KEY）：
  未发起任何真实 Tushare 网络请求、未调用 live LLM、未写生产数据库、未写知识库目录

## 交付物

- `tradingagents/dataflows/tushare_research_evidence.py`：九个研究 endpoint
  （income/balancesheet/cashflow/fina_indicator/forecast/express/dividend/
  fina_audit/fina_mainbz）的证据包构建器，基于 001B 八态契约；每条 endpoint 记录
  携带查询参数、查询时间、数据期间、响应 SHA-256、cache metadata、脱敏 error、
  权限状态与 eligible_row_count；records 按 as_of 过滤未来披露（response hash
  仍指向原始响应）；失败/未查询/字段缺失/过期端点 `records=None`，绝不以空数组
  冒充成功；NO_KEY live 采集 fail closed（全部 NOT_QUERIED，query_fn 不被调用）；
  `validate_evidence_pack` 强制以上全部规则。
- `scripts/export_tushare_research_evidence.py`：只读导出命令——`--output` 必须
  显式指定、存在文件绝不覆盖（硬链接原子创建）、NO_KEY 无 fixture 时退出码 2
  拒绝网络采集；stdout 只输出脱敏 summary（状态/行数/SHA-256，无响应正文）；
  权限等级来自 001A 真实矩阵（tier + matrix_ref + 逐 endpoint permission_status）。
- `tests/fixtures/tushare_research_evidence/example_sanitized.json`：脱敏合成
  fixture（含 HAS_DATA / NORMAL_NO_DATA / PERMISSION_DENIED / FIELD_MISSING 与
  未来披露过滤样本）。

## 示例产物（fixture 模式生成）

- `evidence_pack_example_sanitized.json`：9 endpoint 全覆盖，状态分布
  {HAS_DATA: 6, NORMAL_NO_DATA: 1, PERMISSION_DENIED: 1, FIELD_MISSING: 1}；
  forecast 2 行原始记录中 2027 年披露被 as_of 过滤（eligible=1）。
- `export_summary.json`：脱敏 summary（无 records、无凭据）。

## 验证

- 新增 `tests/test_tushare_research_evidence.py`：**30 passed**（九端点审计字段
  完整、未来披露过滤、NaN→null、四类失败态 records=None 互不误判、NO_KEY fail
  closed、validator 拒绝空数组冒充/未知态/非法 token_status、fixture 示例可构建、
  导出脚本 output 必填、拒绝覆盖、NO_KEY 无 fixture 退出 2、summary 无正文）。
- Tushare 相关回归（contract + provider + capability + financial_fact_bundle +
  evidence）合计 **216 passed**，0 failed。

## 凭据自检

- 对 run archive 三个产物扫描凭据键值对/Authorization/Cookie：clean；64 位
  十六进制命中均为响应 SHA-256 摘要（预期元数据）。
- fixture 与示例 pack 全部为合成数据；真实 Token 只允许从 git-ignored `.env`
  读取，本运行未读取（--no-dotenv）。
