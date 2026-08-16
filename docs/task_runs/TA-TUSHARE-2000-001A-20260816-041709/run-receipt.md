# Run receipt — TA-TUSHARE-2000-001A

- 运行时间：2026-08-16T04:17:09+08:00
- Token 来源：git-ignored `.env`（TUSHARE_TOKEN），状态 `HAS_KEY`，任何产物不包含 Token 明文
- 探测标的：`603629.SH`；探测日 `20260814`；回退日 `20260813`
- 实测 endpoint 数：24；状态分布：{"HAS_DATA": 22, "NORMAL_NO_DATA": 2}

## 重试记录

- `top_list`：attempts=2，reason=`probe_date_no_rows_retry_on_fallback_trading_date`

## 凭据自检

- 对 tushare_permission_matrix.json 与 tushare_permission_matrix.md 扫描 Token/Authorization/Cookie：clean
- 本回执与矩阵均不含真实响应正文，只含结构化元数据与 SHA-256 摘要
