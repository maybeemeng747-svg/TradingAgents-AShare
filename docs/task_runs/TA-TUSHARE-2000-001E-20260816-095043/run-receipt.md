# Run receipt — TA-TUSHARE-2000-001E

- 运行时间：2026-08-16T09:50:43+08:00
- 任务：缓存、退避、动作降级与端到端验收（P1）
- Token：git-ignored `.env`（TUSHARE_TOKEN），状态 `HAS_KEY`；所有产物凭据扫描 clean，
  只含 HAS_KEY/NO_KEY 级别状态
- 真实运行范围：单股票 `603629.SH`、财务 9 + 治理 8 + 市场行为 7 = 24 次 endpoint
  查询，探测间隔 0.6s，无全市场批量查询；未调用 live LLM、未写生产数据库

## 实现内容

- **限流有界退避**（`CnTushareProvider`）：仅 `RATE_LIMITED` 触发重试，指数退避
  `base * 2^(n-1)` 且单次 sleep 封顶 `max_backoff_seconds`（默认 1/2/4→8s 封顶，
  总 sleep 有界）；权限拒绝与通用失败绝不重试；`attempts`/`retry_reason`
  写入结构化审计（`rate_limit_backoff_recovered`/`rate_limit_backoff_exhausted`）。
- **可审计缓存**：缓存条目升级为 `(monotonic, wall_time ISO, frame)`，记录查询
  时间、endpoint、参数、数据期间、TTL、age、expired；`cache_audit_records()`
  输出脱敏审计；旧 2 元组条目向后兼容可读。
- **STALE 只读出口**：`read_cache()` 永不触发上游调用——新鲜命中返回
  `HAS_DATA`（cache.hit），过期条目返回 `STALE`（stale_served=true，保留旧帧
  行数与哈希但不冒充最新）；上游失败 + 过期缓存时审计记 `STALE`。
- **契约扩展**：`TushareQueryResult` 增加 `attempts`/`retry_reason` 并进入
  `to_dict()` 审计记录。
- **动作降级集成链**（fixture 证明）：Tushare 关键证据失败（资金流限流耗尽、
  两融/回购权限拒绝）→ raw_evidence 状态 FAILED → `calculate_evidence_coverage`
  跌破 70 → `get_strong_action_gate` 不通过、`calculate_buy_level` 被限制 →
  `sanitize_forbidden_strong_actions` 将最终响应中的"立即买入"真实移除/替换，
  并非只追加警告。

## 验证

- 新增 `tests/test_tushare_resilience_and_downgrade.py`：**16 passed**（退避恢复/
  耗尽/封顶有界/权限与通用失败不重试/非法配置 fail closed；缓存审计字段/
  新鲜命中/过期 STALE/缺失 None/旧格式兼容；权限、无数据、字段缺失、限流、
  查询失败、旧缓存六态经 provider 审计互不误判；证据失败→Buy Level 受限→
  最终动作降级 健康基线对照；审计与缓存记录 JSON 无凭据）。
- Tushare 全量相关回归（contract/provider/capability/evidence/governance/
  resilience + financial_fact_bundle 等）：**334 passed**，0 failed。

## 真实运行证据（单股票 603629.SH）

- `research_evidence_summary.json`：财务九端点，HAS_DATA 8 + NORMAL_NO_DATA 1
  （express 窗口内无快报），pack status HAS_DATA。
- `governance_events_summary.json`：治理八端点，HAS_DATA 7 + NORMAL_NO_DATA 1
  （pledge_detail 无质押明细记录），pack status HAS_DATA。
- `market_behavior_audit.json`：daily/adj_factor/daily_basic/moneyflow 各 9 行、
  margin 1 行、block_trade 62 行全部 HAS_DATA；top_list 探测日 20260814
  NORMAL_NO_DATA（非上榜日属正常）；全部 attempts=1，未触发限流重试。
- 真实 pack 全文（含响应正文）只写入 `/tmp/ta001e/` 调用方目录，run archive
  只保存哈希与元数据。
- `still_unknown_endpoints.json`：9 项明确排除/未实测接口（VIP 批量、top_inst、
  实时/分钟行情、新闻、公告全文、券商研报、董秘问答等），保持 unknown 不推测。
- 最终权限矩阵沿用 001A 真实矩阵
  `docs/task_runs/TA-TUSHARE-2000-001A-20260816-041709/tushare_permission_matrix.json`
  （24/24 allowed）；知识库证据包与海瑞治理事件包示例见 001C/001D run archive。
