# Run receipt — TA-TUSHARE-2000-001D

- 运行时间：2026-08-16T09:40:25+08:00
- 任务：海瑞治理事件包只读出口（P1）
- 本运行为 **fixture 模式**（`--no-dotenv`，collection_mode=fixture，token_status=NO_KEY）：
  未发起任何真实网络请求、未调用 live LLM、未写生产数据库、未写海瑞/知识库目录

## 交付物

- `tradingagents/dataflows/tushare_governance_events.py`：八端点治理事件包
  （stk_holdernumber、top10_holders、top10_floatholders、pledge_stat、
  pledge_detail、stk_holdertrade、share_float、repurchase），基于 001B 八态
  契约；repurchase 按公告日市场窗口查询后本地过滤 ts_code（hash scope
  `frame_symbol_filtered`，其他标的行不进入 symbol 包）；无事件
  （NORMAL_NO_DATA + records=[]）与查询失败（records=None + 脱敏 error）严格
  区分；每条事件保留公告/报告日期，端点分组携带 source_endpoint；pack 级
  freshness（latest_event_date / days_behind_as_of）与 anomalies 异常清单；
  `validate_governance_pack` fail closed（状态混淆、无日期事件、缺失 source
  endpoint、交易动作词全部拒绝）；NO_KEY live 采集 fail closed。
- `scripts/export_tushare_governance_events.py`：只读导出命令——`--output`
  必须显式指定（无默认目标）、已存在文件拒绝覆盖、路径段命中知识库/海瑞
  目录 denylist（tree_work/knowledge/knowledge_base/haigui/hairui/海瑞/
  judgement/verdicts）时拒绝执行；NO_KEY 无 fixture 退出码 2；stdout 只输出
  脱敏 summary。
- `tests/fixtures/tushare_governance_events/example_sanitized.json`：脱敏合成
  fixture（含无事件端点、市场窗口跨标的过滤样本）。

## 示例产物（fixture 模式生成）

- `governance_pack_example_sanitized.json`：8 endpoint 全覆盖，状态分布
  {HAS_DATA: 7, NORMAL_NO_DATA: 1}，anomalies 为空；repurchase 市场窗口两行
  中 000001.SZ 被本地过滤，仅保留 603629.SH。
- `export_summary.json`：脱敏 summary（无 records、无凭据）。

## 验证

- 新增 `tests/test_tushare_governance_events.py`：**28 passed**（同股八端点、
  无事件/失败严格区分、三失败态互不误判、事件保留日期、未来披露过滤、
  市场窗口本地过滤、freshness、NO_KEY fail closed、fixture 模式、validator
  拒绝失败态空数组/无日期事件/动作词/缺 source endpoint、股东增减持语义词
  不误伤、脚本 output 必填/拒绝覆盖/知识库海瑞目录拒绝/脱敏 summary）。
- Tushare 相关回归（governance + evidence + contract + provider + capability）
  合计 **175 passed**，0 failed。

## 凭据自检

- 对 run archive 产物与新增文件扫描凭据键值对/Authorization/Cookie：clean；
  十六进制长串命中均为 SHA-256 摘要（预期元数据）。
- fixture 与示例 pack 全部为合成数据；真实 Token 只允许从 git-ignored `.env`
  读取，本运行未读取（--no-dotenv）。输出不含交易动作词（validator 强制）。
