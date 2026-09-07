你是独立代码审查者。任务：TA-MF-01 — 冻结通用市场事实契约 v1。仓库：/Users/maybee/TradingAgents-AShare。只读审查。

## 审查对象
工作区未提交候选（docs/task_runs/TA-MF-01-20260907-122509/ 有运行记录）：
- docs/contracts/market-facts-v1.md（契约主文档，含盘点/六包字段/状态/来源/日期约定/边界TTL超时策略）
- docs/contracts/schemas/（7 个 JSON Schema + fixtures/ 13 个脱敏样例 + MANIFEST sha256）
- api/main.py 修改（新增只读端点 GET /v1/market/facts/contract）
- tests/market_facts/、tests/test_market_facts_contract.py、tests/test_api_market_facts.py
- docs/DEVLOG.md

**范围豁免**：docs/TASKS.md 的状态行变更（ready→in_progress→blocked-review）是主控/流程簿记，明确不在审查范围内，不要因此判 FAIL。

## 任务背景与验收标准（源自任务卡与 2026-09-05 交接包）
1. 所有 fixture 必须通过对应 schema 校验；MANIFEST sha256 与文件实际一致。
2. 失败状态不得混入假数值（八状态 HAS_DATA/NORMAL_NO_DATA/QUERY_FAILED/PERMISSION_DENIED/RATE_LIMITED/FIELD_MISSING/NOT_QUERIED/STALE）；包级可用性映射齐全（全部/部分/不可用）。
3. 新端点只读、有鉴权与鉴权测试；不开放任意 Tushare endpoint 透传。
4. 现有 /v1/market/kline 与报告端点行为兼容（回归无破坏）。
5. 六个包类型（trading_calendar/market_regime/strategy_inputs/event_calendar/company_facts/governance_risk）每个都有字段、状态、来源、日期约定；不存在的日期用 null 不伪造；上海时区；历史查询不得使用未公开数据。
6. 证券类型映射不靠首位数字猜指数；Tushare Token 不出现在任何 fixture/文档/测试中（脱敏核查）。
7. 边界：有界标的数/日期跨度/响应大小/超时/缓存TTL有明确数值或明确"契约任务后定"的登记。
8. 确定性：事实查询路径零 LLM 调用。

## 允许操作
只读 + 可运行测试（.venv/bin/python -m pytest，conftest 已隔离 DATABASE_URL；若临时目录受限可用 -s 或 pytest tmp 跳过说明）。不得修改文件、不得 commit。

## 重点怀疑面（必查）
- schema 与 fixture 实际内容是否一致（拿 2-3 个 fixture 跑校验器或人工比对必填字段）
- envelope schema 是否真的覆盖六包类型与八状态枚举
- api/main.py 新端点：鉴权装饰器/依赖是否与其他 /v1 端点一致；错误路径是否泄漏内部信息；是否真的只读
- 测试是否真实断言（非空转）；40 项新测试抽 3-5 个看断言质量
- MANIFEST sha256 抽查 2 个文件
- 兼容性：api/main.py 的修改是否只增不改（存量端点/函数签名）

## 输出（中文，stdout）
- 第一行：`结论: PASS` 或 `结论: FAIL`
- 按验收标准 1-8 逐条核验（引用文件:行）
- 新发现 findings（P0/P1/P2 分级，引用文件:行；没有写"无"）
- 实际运行的命令与结果
