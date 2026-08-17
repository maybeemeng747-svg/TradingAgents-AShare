# Run receipt — TA-TUSHARE-2000-001A

- 运行时间：2026-08-16T20:22:38+08:00
- Token 来源：git-ignored .env 文件（TUSHARE_TOKEN），来源标识 `dotenv_file`，状态 `HAS_KEY`，任何产物不包含 Token 明文
- 探测标的：`603629.SH`；探测日 `20260814`；回退日 `20260813`
- 实测 endpoint 数：24；状态分布：{"HAS_DATA": 22, "NORMAL_NO_DATA": 2}

## 重试记录

- `top_list`：attempts=2，reason=`probe_date_no_rows_retry_on_fallback_trading_date`

## repurchase 单股查询记录（001A-R1）

- 查询参数：`{"ts_code": "603629.SH"}`
- 返回行数：6（state=`HAS_DATA`）
- scope：`symbol`；row_cap_suspected=`False`

## 凭据自检

- 对 tushare_permission_matrix.json、tushare_permission_matrix.md 与本回执扫描 Token/Authorization/Cookie：clean（发现泄漏即拒绝写盘并以退出码 3 fail closed）
- 本回执与矩阵均不含真实响应正文，只含结构化元数据与 SHA-256 摘要

## 新旧矩阵差异（vs `TA-TUSHARE-2000-001A-20260816-041709`）

本档案为 TA-TUSHARE-2000-001A-R1 修复 c73ed4f review findings（3×P1+2×P2）后的重跑真源；
001A 旧档案的 24/24 allowed 仅视为历史运行结果。逐 endpoint 对比：

| 差异项 | 旧矩阵（041709） | 新矩阵（本档案） | 说明 |
|---|---|---|---|
| `repurchase` 行数 | 2000（公告日窗口全市场抓取，scope=`market_window_filtered`，命中行数上限） | 6（单 `ts_code` 最小查询，scope=`symbol`，公告日 20240207~20260326） | P1 修复生效：上游接受 `ts_code` 参数，单股最小查询成立，不再抓取全市场；权限结论不变（allowed） |
| `pledge_stat` 行数 | 335 | 336 | 上游数据自然漂移（上次运行后新增一条质押统计），与本修复无关；digest 相应变化 |
| 其余 22 个 endpoint | — | — | 状态、行数、响应 SHA-256 摘要全部一致 |
| Token 来源记录 | 无来源字段（继承环境变量可能静默覆盖 `.env`，归属不可审计） | `token_source=dotenv_file`，`token_env_override_applied=false`（脚本显式解析，`.env` 恒优先） | P1 修复生效 |
| 矩阵 schema | 1.0 | 1.1 | 新增 token_source / token_env_override_applied 字段 |
| 行数上限标注 | 仅限 market_window_filtered scope | 任意 scope 命中 2000 行上限都标注 `row_cap_suspected` | 本轮 24 个 endpoint 均未命中 |

本次运行环境无继承 `TUSHARE_TOKEN`（`env_override_applied=false` 为真实观测值，非默认占位）。
