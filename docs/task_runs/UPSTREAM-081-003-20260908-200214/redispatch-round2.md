# UPSTREAM-081-003 re-dispatch 记录（round2，2026-09-09）

- 方向：孟拍板按 fail-closed 正向重做（round1 的 TypeError→NORMAL_NO_DATA 方向被协调员否决）。
- HEAD 仍为 ad97779；本轮无 commit / push。

## 一、round1 既有 diff 处置清单

| 文件 | round1 改动 | 处置 | 依据 |
| --- | --- | --- | --- |
| `tradingagents/dataflows/providers/cn_akshare_provider.py` | `get_lhb_detail` 增加 `except TypeError → LHB_NORMAL_NO_DATA`（+10 行，唯一 hunk） | **还原**（git restore 至 HEAD） | 把畸形响应/处理 bug 伪装成正常无数据，反转既有 fail-closed 回归；live 探测证明数据实际可取，"未发布"场景未复现 |
| `tests/test_upstream_v081_absorption.py` | `test_lhb_type_error_fails_closed` 改名为 `test_lhb_type_error_means_not_yet_published` 并反转断言 | **还原**（git restore 至 HEAD），断言复位为 `LHB_FAILED` + `TypeError` | 恢复被反转的 fail-closed 回归 |
| `tests/test_upstream081_akshare_gap.py`（未跟踪草稿） | round1 未完成；含 2 个错误方向用例（TypeError→NORMAL_NO_DATA、不触发路由失败） | **重写**：错误方向用例替换为 fail-closed 等价物，其余用例保留（锁定本地正确行为） | 正确语义：TypeError/畸形响应/接口缺失 → LHB_FAILED 并触发 `_is_failure_result` 降级 |
| `docs/DEVLOG.md` | round1 中止条目 | 保留，另追加本轮 re-dispatch 条目 | 历史记录不删 |
| `docs/TASKS.md` | 主控（孟）重派状态更新（blocked→in_progress） | 保留不动 | TASKS.md 状态归主控管理 |
| `docs/data_source_reports/akshare-v081-upstream-audit-2026-09-08.md` | round1 测试 docstring 引用但**从未创建**（引用悬空） | 补齐为 `...-2026-09-09.md` 并修正测试引用 | 审计文档必须有真实内容与证据 |

## 二、差异审计结论（上游 aa79bca vs 本地，akshare 1.18.30）

签名实录与 live 探测见 `docs/data_source_reports/akshare-v081-upstream-audit-2026-09-09.md`。

| # | 接口 | 上游 aa79bca 改动 | 本地现状 | 实测证据 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | 雪球 token | `stock_individual_spot_xq` 透传 `XQ_A_TOKEN` | 已移植（`_fetch_realtime_row_unlocked`） | 1.18.30 签名含 `token: str = None` | 真实缺口：无；测试锁定行为 + 权限失败 fail-closed |
| 2 | 全球新闻 | news_cctv 整体替换为 sina zhibo 私有接口；签名收窄为 `(curr_date)`，异常 fail-open | news_cctv 主路径 + 3 天回看 + `look_back_days/limit` 契约 + NotImplementedError fail-closed | `news_cctv("20260908")` = 12 行，可用 | 不移植：上游破坏 base.py 契约且 fail-open；本地接口仍有效 |
| 3 | 行业资金流 | `stock_board_industry_fund_flow_em` → `stock_fund_flow_industry` | 主源已是 `stock_fund_flow_industry`，另有 `stock_sector_fund_flow_rank` fallback（超集） | 1.18.30 中旧接口 NOT_PRESENT | 真实缺口：无（本地为上游修复的超集） |
| 4 | 龙虎榜参数 | A: 去 `symbol` 参数、紧凑日期、本地过滤（本地已移植且更强）；B: `except TypeError → "数据尚未更新"` | A 已移植 + 代码列缺失单独 `LHB_FAILED` + zfill(6)；B 本地 fail-closed | `stock_lhb_detail_em("20260908"×2)` = 59 行，含"代码"列；600036 过滤后 0 行（正常未上榜→NORMAL_NO_DATA）；round1 协调员两日期探测同样可取数 | A：已移植；B：**纠正还原，不移植**——TypeError 保持 `LHB_FAILED`，命中 `_FAILURE_RESULT_PATTERNS` 触发 `cn_tushare → cn_astock` 降级 |
| 5 | 热门数据接口 | `"最热门"` → `"本周新增"` | 保持 `"最热门"` | 1.18.30 两个 symbol 均返回 5640 行，默认值仍为 `"最热门"` | 不移植：本地选择实测有效；签名守卫防漂移 |

分类口径（任务卡要求）：接口不存在（`stock_board_industry_fund_flow_em` NOT_PRESENT）、签名漂移（上游删除 look_back_days/limit）、空数据/未上榜（NORMAL_NO_DATA）、权限失败（KeyError 异常传播）——四类分别归类，未合并。

## 三、本轮净变更

- 无 provider 生产代码改动（审计结论为零移植；round1 的 10 行已还原）。
- `tests/test_upstream081_akshare_gap.py`：重写完成，26 用例。
- `docs/data_source_reports/akshare-v081-upstream-audit-2026-09-09.md`：新增（真实审计文档）。
- `docs/DEVLOG.md`、本目录：追加记录。

## 四、验证

```
pytest tests/test_upstream081_akshare_gap.py tests/test_upstream_v081_absorption.py -q --tb=short
→ 66 passed in 1.63s
pytest tests/test_api_smoke.py -q --tb=short
→ 87 passed in 28.21s
```

- 低频只读 live smoke：5 次调用（版本/news_cctv/热搜×2/龙虎榜单日单股过滤），不调用 LLM，未写生产 DB，未做全市场扫描、未做个股深度 TA。
- 未改 `tradingagents/prompts/`；未 commit / push / PR。

## 五、待办

- 独立 Codex review；通过后由主控决定提交。
