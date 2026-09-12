# 研报运营端到端验收报告（V-015）— 2026-09-12

链路：KB-019 增量摄取（ingest_key/status）→ KB-020 证据 API → UI-014 前端契约 → HY-010 待更新清单。全程只读、无 LLM、不写生产 DB。

## Part 1 — fixture 端到端回放（隔离 tmp 知识库）

- `pytest tests/test_v015_research_operations_e2e.py` 退出码：**0**

```
..........                                                               [100%]
10 passed in 1.23s
```

覆盖：新增研报（ingest_key 幂等）/ 重复研报（duplicate_of）/ 缺元数据（needs_metadata）/ 半年报修订（HY-009 revised 贯穿）/ 事实冲突（证据 conflict + HY-010 conflict）；链路字段与前端 UI-014 视图模型消费字段一致；全链路无动作语义字段；局部失败/缓存损坏/空目录可解释降级。

## Part 2 — 真实知识库只读 smoke（含链路可用性门禁）

- 知识库：`/Users/maybee/Documents/knowledge`（只读）
- 结构核验：investment 分区存在=True，页面数=437
- KB-019 增量清单：total=1094，状态分布 {"new": 44, "digested": 7, "duplicate": 56, "needs_metadata": 917, "stale": 68, "conflict": 2}
- KB-020 `300750.SZ`：aggregate=fresh；consensus=fresh/hit=true；citation_audit=missing/hit=false；thesis_timeline=fresh/hit=true；half_year_facts=missing/hit=false；research_score_snapshot=missing/hit=false；契约违例：无
- KB-020 `688041.SH`：aggregate=fresh；consensus=fresh/hit=true；citation_audit=missing/hit=false；thesis_timeline=fresh/hit=true；half_year_facts=missing/hit=true；research_score_snapshot=missing/hit=false；契约违例：无
- HY-010 待更新队列：universe=1（universe 为合成样本（不读取生产数据库）），状态分布 {"up_to_date": 0, "missing": 1, "stale": 0, "conflict": 0, "needs_digest": 0, "unknown": 0}，样本 symbol 状态=missing，契约违例：无

### 链路可用性门禁

- 全部通过：必需目录存在且非空、摄取无错误、抽样共识命中、无契约违例

## 结论

- **PASS**：fixture 端到端回放全部通过；真实只读 smoke 无异常、无契约违例。链路可用、可追溯、不影响交易动作。

> fixture 与真实只读 smoke 分开报告；本报告不修改任何业务代码，真实库侧数据质量问题记录为 findings（参见 V-014 日报）。
