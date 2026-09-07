# TA-MF-01 运行记录：冻结通用市场事实契约 v1（候选交付）

- **日期**：2026-09-07 12:25（Asia/Shanghai）
- **执行者**：OpenCode（TA 执行代理）
- **Base commit**：`b672d69`（分支 `local/tradingagents-custom`）
- **Commit**：未提交（按派单要求保持工作区未提交，待主控 review 后决定）
- **状态**：CANDIDATE（候选交付，等 Codex 独立 review 后冻结）

## 交付物清单

| 交付物 | 路径 |
|---|---|
| 契约文档（含盘点三列、12 条对照表） | `docs/contracts/market-facts-v1.md` |
| JSON Schema ×7（信封 + 六包，draft-07 子集，自包含单文件可用） | `docs/contracts/schemas/*.schema.json` |
| 脱敏共享 fixture ×13（11 正例 + 2 负例） | `docs/contracts/schemas/fixtures/*.json` |
| fixture 清单（版本 + 逐文件 sha256 + 状态覆盖） | `docs/contracts/schemas/MANIFEST.json` |
| 零依赖 JSON Schema 子集校验器 | `tests/market_facts/schema_lite.py` |
| 契约产物测试 ×32 | `tests/test_market_facts_contract.py` |
| 契约自描述端点（只读+鉴权） | `api/main.py`：`GET /v1/market/facts/contract` |
| 端点测试 ×8（401/JWT/API token readonly） | `tests/test_api_market_facts.py` |

## 实现说明

1. **盘点**（契约 §1）：三列口径（上游已有/本地已有/真实缺口）。核心结论：八状态契约（001B）、财务证据包（001C）、治理事件包（001D）、`get_stock_data` 复权换算、K 线路由、认证三档可直接复用；trading_calendar / market_regime / strategy_inputs / event_calendar 四包无任何发布通道；001C/001D 有数据层无 HTTP 通道；指数映射仅 8 个硬编码（缺中证2000）、ETF/场外基金未接；`/v1/market/kline` 无鉴权记为 KNOWN-GAP-1。
2. **信封与八状态**（契约 §4、§5）：外层 15 字段覆盖交接包第 2 条全部要求；八状态沿用 001B 语义与代码常量（`tushare_query_contract.QUERY_STATES`），包级可用性 READY/PARTIAL/UNAVAILABLE 映射写死判定规则（含 gaps 非空触发 PARTIAL）；失败状态 payload/日期/数值一律 null 由 schema if/then 机检，负例 fixture 验证校验器真的会拒绝。
3. **证券映射**（契约 §6.3 附近、文档 §7）：security_type 由显式注册表决定，禁止首位数字猜类型（例证：000001.SH 上证指数 vs 000001.SZ 平安银行）；交易所代码 SH/SZ/BJ→SSE/SZSE/BSE；供应商代码（tushare/em/akshare）映射只在 vendor_codes 声明。
4. **时间/单位/复权**（契约 §6、§7）：Asia/Shanghai；trade_date/ann_date/actual_disclosure_date/report_period/fetched_at 分开；仅有公告日时 disclosure_timing=UNKNOWN；QFQ（窗口末日基准）用于收益类、NONE 用于价格触发、指数点位不复权；001C/001D records 保留上游 YYYYMMDD 原始格式，信封级统一 ISO，两不混写。
5. **指标规范**（契约 §7.3）：`mf-calc-v1` 冻结 ret_nd/ma_n/vol_nd/mdd_nd/vratio_5d 公式与 required_samples（N+1/N/N+1/N+1/6）；样本不足返回 gap `INSUFFICIENT_SAMPLES`、value=null（fixture 有 20 缺 6 的实例）。
6. **接口清单**（契约 §9）：K 线复用现状不动（KNOWN-GAP-1 写明兼容策略）；新增 `GET /v1/market/facts/contract`（readonly、无业务数据、返回契约版本+schema/manifest sha256，供海瑞机器核对加载版本——TA-MF-06 验收依赖）；六个包路由预留（404 until TA-MF-02~05）；禁止任意 Tushare endpoint 透传写入契约。
7. **边界/缓存/重试**（契约 §8）：标的 ≤20、日线 ≤366 自然日、日历 ≤400、响应 ≤2MB、单上游 30s/整请求 60s、包级 TTL（日历 24h/日线 15min/事件 1h/财务治理 6h）、QUERY_FAILED 重试 ≤2 次、PERMISSION_DENIED/RATE_LIMITED 不重试、"缓存过期 vs 行情落后"两维分开（STALE+cache 字段 vs DATA_LAGGING/DATA_NOT_YET_PUBLISHED gap）。
8. **fixture**（契约 §10）：13 个文件覆盖八状态全部 + PARTIAL/UNAVAILABLE/样本不足/计划-实际修订/负例 2 个；时间戳固定常量保证字节可复现；无任何凭据/个人数据/生产账本值。

## 测试与结果

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/test_market_facts_contract.py -q` | **32 passed**（fixture schema 校验、负例拒绝、manifest sha256、枚举与代码常量 lockstep、可用性映射语义、as_of 过滤、脱敏断言） |
| `python -m pytest tests/test_api_market_facts.py -q` | **8 passed**（未鉴权 401、垃圾 token 拒绝、JWT 200、API token readonly 不写 last_used_at、schema 摘要与磁盘一致、端点清单完整、无行情数据泄漏） |
| `python -m pytest tests/test_api_smoke.py -q` | **87 passed**（现有 analyze/reports/jobs/config 调用兼容未破坏） |
| `python -m pytest tests/test_tushare_query_contract.py tests/test_tushare_research_evidence.py tests/test_tushare_governance_events.py -q` | **95 passed**（八状态契约与 001C/001D 包零改动兼容） |
| `python -m pytest tests/test_price_intent_contract.py -q` | **199 passed**（K 线链路回归） |

Live smoke：**NOT_RUN** — 本任务只冻结契约与 fixture，无新增上游查询路径；不调用任何 LLM（数据查询 LLM 次数 = 0）。

## 摘要（sha256）

- `MANIFEST.json`：`ab76c35fa1c8e098099ccf35e71fe420cbd0d34d3dcc81c824f5161a08c9a970`
- 7 个 schema 与 13 个 fixture 的逐文件 sha256 全部记录在 `docs/contracts/schemas/MANIFEST.json`，测试 `TestManifestDigests` 保证与字节一致；端点 `/v1/market/facts/contract` 的 `fixture_manifest_sha256` 字段运行时同值。

## 安全与数据边界证据

- 新增路由仅 GET、`Depends(_require_readonly_api_user)`、不触达数据库业务表、不调用上游供应商、无 LLM。
- 未修改 `/v1/market/kline` 代码（diff 中无该函数）；未提交他人未提交 diff（工作区起始仅 docs/TASKS.md 本任务状态行改动）。
- fixture/文档/schema 经 `test_fixtures_are_credential_free` 正则断言无 token/key/私钥；Tushare Token 未出现在任何交付物中。
- 未触碰：生产数据库、海瑞/知识库/controller 仓库、投资规则、LLM prompts、TradeFlow、自动调度、前端。

## 自查（墨菲视角）

- 失败态夹带数据？→ schema if/then 机检 + 负例 fixture 证明拒绝路径生效。
- 空列表冒充成功？→ NORMAL_NO_DATA 与失败态 payload 形状在 schema 分开；governance fixture 保持 001D `records=null` 语义。
- 未公开数据泄入历史查询？→ company-facts fixture 明示 row_count=3/eligible=2（ann_date=20261028 被过滤），契约 §6.2 声明强制。
- 校验器自欺？→ `schema_lite` 对未知关键字抛 `UnsupportedSchema`；测试验证"坏 schema 会抛异常、坏 fixture 会被拒"。
- manifest 漂移？→ 摘要测试双写（schema 侧 const 版本 + 磁盘 sha256 对账），任何文件改动不重算 manifest 即红。

## 遗留缺口（有意后置）

1. KNOWN-GAP-1：`/v1/market/kline` 无鉴权（兼容保留；补鉴权需独立任务评估现有调用方）。
2. 六个包端点未实现（TA-MF-02~05）；预留路由现返回 404。
3. 指数注册表仅 8 个硬编码映射，中证2000 等待 TA-MF-03 登记。
4. venv 无 `jsonschema` 包（无 pip），故校验用仓库内 draft-07 子集实现 `tests/market_facts/schema_lite.py`；若海瑞侧用完整 jsonschema 校验，schema 只用了双方都支持的子集关键字（已由测试防升级）。
5. 契约状态 CANDIDATE：review 通过后需改 FROZEN、重算 manifest（§11 冻结流程）。

## Next eligible task

TA-MF-02（交易日历来源与发布）——依赖本契约冻结；TA-MF-03/05 亦可在冻结后并行领取（TA-PLAN 依赖表）。
