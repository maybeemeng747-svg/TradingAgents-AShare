# Tushare 2000 积分真实权限矩阵（TA-TUSHARE-2000-001A）

- 生成时间：2026-08-16T20:22:38+08:00
- Token 状态：`HAS_KEY`（不记录 Token 任何明文信息）
- Token 来源：`dotenv_file`（git-ignored .env 文件）
- 探测标的：`603629.SH`（SSE），探测日 `20260814`，回退日 `20260813`
- 探测策略：每个 endpoint 最多一次正常探测；仅瞬态网络错误或日期型空结果允许一次带原因记录的重试；权限拒绝与限流绝不重试；探测间 sleep 限速

## 汇总

- 实测 endpoint 数：24
- `HAS_DATA`：22
- `NORMAL_NO_DATA`：2
- 权限 `allowed`：24

## 实测权限矩阵

| endpoint | 类别 | 状态 | 权限 | 行数 | 数据时间范围 | 额外付费 | 重试 | SHA-256(前12位) |
|---|---|---|---|---:|---|---|---|---|
| income | 财务 | `HAS_DATA` | allowed | 6 | 20250429~20260428(ann_date) | 否 | 否 | `929bf66e64a8` |
| balancesheet | 财务 | `HAS_DATA` | allowed | 8 | 20250429~20260428(ann_date) | 否 | 否 | `4a2eb4aa6e80` |
| cashflow | 财务 | `HAS_DATA` | allowed | 11 | 20250429~20260428(ann_date) | 否 | 否 | `cec366c8b17d` |
| fina_indicator | 财务 | `HAS_DATA` | allowed | 6 | 20250429~20260428(ann_date) | 否 | 否 | `9655af964f48` |
| forecast | 财务 | `HAS_DATA` | allowed | 2 | 20260127~20260715(ann_date) | 否 | 否 | `3facd01ce43d` |
| express | 财务 | `NORMAL_NO_DATA` | allowed | 0 | - | 否 | 否 | `4f53cda18c2b` |
| dividend | 财务 | `HAS_DATA` | allowed | 37 | 20190416~20260529(ann_date) | 否 | 否 | `1092f6ad167b` |
| fina_audit | 财务 | `HAS_DATA` | allowed | 12 | 20181203~20260428(ann_date) | 否 | 否 | `a02cf4a2bc33` |
| fina_mainbz | 财务 | `HAS_DATA` | allowed | 150 | 20181231~20251231(end_date) | 否 | 否 | `188af791f5b7` |
| top10_holders | 治理事件 | `HAS_DATA` | allowed | 322 | 20180322~20260428(ann_date) | 否 | 否 | `3975b947cc67` |
| top10_floatholders | 治理事件 | `HAS_DATA` | allowed | 231 | 20190416~20260428(ann_date) | 否 | 否 | `b7e3b1ba6e87` |
| pledge_stat | 治理事件 | `HAS_DATA` | allowed | 336 | 20191227~20260814(end_date) | 否 | 否 | `3e228452b258` |
| pledge_detail | 治理事件 | `HAS_DATA` | allowed | 18 | 20201223~20260429(ann_date) | 否 | 否 | `30e1bc019571` |
| repurchase | 治理事件 | `HAS_DATA` | allowed | 6 | 20240207~20260326(ann_date) | 否 | 否 | `100e04316fae` |
| share_float | 治理事件 | `HAS_DATA` | allowed | 72 | 20181221~20260429(ann_date) | 否 | 否 | `6f2e8faa123c` |
| stk_holdernumber | 治理事件 | `HAS_DATA` | allowed | 64 | 20180322~20260626(ann_date) | 否 | 否 | `afa3424154f8` |
| stk_holdertrade | 治理事件 | `HAS_DATA` | allowed | 35 | 20200520~20260722(ann_date) | 否 | 否 | `2cc84dd693e1` |
| daily | 市场行为 | `HAS_DATA` | allowed | 10 | 20260803~20260814(trade_date) | 否 | 否 | `819f51ad5bbd` |
| adj_factor | 市场行为 | `HAS_DATA` | allowed | 10 | 20260803~20260814(trade_date) | 否 | 否 | `53b12b2b7ea8` |
| daily_basic | 市场行为 | `HAS_DATA` | allowed | 10 | 20260803~20260814(trade_date) | 否 | 否 | `a1dc8a32debf` |
| moneyflow | 市场行为 | `HAS_DATA` | allowed | 10 | 20260803~20260814(trade_date) | 否 | 否 | `678a14ed7bb8` |
| margin | 市场行为 | `HAS_DATA` | allowed | 1 | 20260814~20260814(trade_date) | 否 | 否 | `da5edfc5e983` |
| top_list | 市场行为 | `NORMAL_NO_DATA` | allowed | 0 | - | 否 | 是 | `4f53cda18c2b` |
| block_trade | 市场行为 | `HAS_DATA` | allowed | 62 | 20220627~20260227(trade_date) | 否 | 否 | `81d9fd22adc6` |

### 探测参数与说明

| endpoint | 查询参数摘要 | scope | 说明 |
|---|---|---|---|
| income | end_date=20260814, start_date=20250101, ts_code=603629.SH | symbol |  |
| balancesheet | end_date=20260814, start_date=20250101, ts_code=603629.SH | symbol |  |
| cashflow | end_date=20260814, start_date=20250101, ts_code=603629.SH | symbol |  |
| fina_indicator | end_date=20260814, start_date=20250101, ts_code=603629.SH | symbol |  |
| forecast | end_date=20260814, start_date=20250101, ts_code=603629.SH | symbol |  |
| express | end_date=20260814, start_date=20250101, ts_code=603629.SH | symbol |  |
| dividend | ts_code=603629.SH | symbol |  |
| fina_audit | ts_code=603629.SH | symbol |  |
| fina_mainbz | ts_code=603629.SH | symbol |  |
| top10_holders | ts_code=603629.SH | symbol |  |
| top10_floatholders | ts_code=603629.SH | symbol |  |
| pledge_stat | ts_code=603629.SH | symbol |  |
| pledge_detail | ts_code=603629.SH | symbol |  |
| repurchase | ts_code=603629.SH | symbol | repurchase 单 ts_code 最小查询（001A-R1 修复：原公告日窗口查询命中全市场行数上限，违反单股最小探测约束） |
| share_float | ts_code=603629.SH | symbol |  |
| stk_holdernumber | ts_code=603629.SH | symbol |  |
| stk_holdertrade | ts_code=603629.SH | symbol |  |
| daily | end_date=20260814, start_date=20260801, ts_code=603629.SH | symbol |  |
| adj_factor | end_date=20260814, start_date=20260801, ts_code=603629.SH | symbol |  |
| daily_basic | end_date=20260814, start_date=20260801, ts_code=603629.SH | symbol |  |
| moneyflow | end_date=20260814, start_date=20260801, ts_code=603629.SH | symbol |  |
| margin | exchange=SSE, trade_date=20260814 | exchange_aggregate | margin 为交易所级融资融券汇总接口，不含个股维度；按 ts_code 所属交易所单日探测 |
| top_list | trade_date=20260814, ts_code=603629.SH | symbol |  |
| block_trade | ts_code=603629.SH | symbol |  |

## 明确排除（未实测，不推测）

| 范围 | 定性 | 是否额外付费 | 原因 |
|---|---|---|---|
| vip_*（5000 积分 VIP 全市场批量接口族） | `unknown` | unknown | 官方标注 5000 积分 VIP 权限，未实测，明确排除 |
| top_inst | `unknown` | unknown | 龙虎榜机构明细，任务范围明确排除，未实测 |
| 实时行情类接口 | `unknown` | likely | 实时行情属额外付费能力，未实测 |
| 历史分钟行情类接口 | `unknown` | likely | 分钟线属额外付费能力，未实测 |
| 新闻类接口 | `unknown` | unknown | 任务范围明确排除，未实测 |
| 公告全文类接口 | `unknown` | likely | 公告全文属额外付费能力，未实测 |
| 券商研报类接口 | `unknown` | unknown | 任务范围明确排除，未实测 |
| 董秘问答类接口 | `unknown` | unknown | 任务范围明确排除，未实测 |
| 其余未实测接口 | `unknown` | unknown | 凡未在本矩阵实测的接口一律保持 unknown，不做积分等级推测 |

## 状态证据

| 状态 | 真实运行观察到 | 真实示例 | fixture 证据 |
|---|---|---|---|
| `HAS_DATA` | 是 | income, balancesheet, cashflow, fina_indicator, forecast, dividend, fina_audit, fina_mainbz, top10_holders, top10_floatholders, pledge_stat, pledge_detail, repurchase, share_float, stk_holdernumber, stk_holdertrade, daily, adj_factor, daily_basic, moneyflow, margin, block_trade | - |
| `NORMAL_NO_DATA` | 是 | express, top_list | tests/fixtures/tushare_capability/normal_no_data.json |
| `QUERY_FAILED` | 否（未验证） | - | tests/fixtures/tushare_capability/query_failed.json |
| `PERMISSION_DENIED` | 否（未验证） | - | tests/fixtures/tushare_capability/permission_denied.json |
| `RATE_LIMITED` | 否（未验证） | - | tests/fixtures/tushare_capability/rate_limited.json |
| `NOT_QUERIED` | 否（未验证） | - | - |

## 备注

- 矩阵结果只来自本次真实探测，不引用静态 SOURCE_CAPABILITY_MATRIX，也不按积分等级推测
- 矩阵不包含真实响应正文、Token、Cookie 或 Authorization；错误文本经脱敏清洗
- 原始响应以结构化元数据（行/列数、列名、日期范围）与 SHA-256 摘要形式留存
- 探测脚本：scripts/audit_tushare_capability.py，探测间隔 0.6s
- Token 来源标识：dotenv_file（显式解析并记录，杜绝继承环境变量静默覆盖）
