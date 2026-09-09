# AKShare v0.8.1 上游差异审计（UPSTREAM-081-003 re-dispatch）

- 日期：2026-09-09（round1 方向被协调员否决后的重派轮）
- 上游基线：`aa79bca`（fix: fix akshare API issues in cn_akshare_provider, #195）
- 本地安装：akshare **1.18.30**（`python -c "import akshare; print(ak.__version__)"`）
- 审计对象：雪球 token / 全球新闻 / 行业资金流 / 龙虎榜参数 / 热门数据接口，共 5 项
- 结论总览：**5 项均无需移植新代码** —— 3 项上游修复本地早已存在（部分更强），
  2 项上游替换经实测判定为不适用；round1 唯一新增项（TypeError → NORMAL_NO_DATA）
  已纠正还原。

## 关键函数签名（本地安装 akshare 1.18.30，2026-09-09 实录）

```
stock_individual_spot_xq(symbol: str = 'SH600000', token: str = None, timeout: float = None)
news_cctv(date: str = '20240424')
stock_fund_flow_industry(symbol: str = '即时')
stock_lhb_detail_em(start_date: str = '20230403', end_date: str = '20230417')
stock_hot_follow_xq(symbol: str = '最热门')
stock_sector_fund_flow_rank(indicator: str = '今日', sector_type: str = '行业资金流')
stock_board_industry_fund_flow_em: NOT_PRESENT（接口在本版本不存在）
```

## 逐项审计

### 1. 雪球 token —— 本地已移植，无缺口
- 上游：`stock_individual_spot_xq(..., token=os.environ.get("XQ_A_TOKEN"))`。
- 本地（cn_akshare_provider.py `_fetch_realtime_row_unlocked`）：完全一致，
  `token=os.getenv("XQ_A_TOKEN")`。
- 归类：上游改动 / 本地已有。守护测试：gap 测试组 1（透传 + 无凭据 None +
  KeyError 权限失败异常传播 fail-closed）。

### 2. 全球新闻 —— 上游替换不移植（接口仍有效 + 上游属破坏性变更）
- 上游：用新浪 zhibo 私有接口（`requests` 直连 `zhibo.sina.com.cn`）整体替换
  news_cctv 路径，且把 `get_global_news(curr_date, look_back_days, limit)`
  收窄为 `get_global_news(curr_date)` —— 破坏本地 `base.py` 接口契约签名；
  异常一律转成"未获取到"字符串（fail-open）。
- 本地：保留 news_cctv 主路径 + 3 天回看 + look_back_days/limit 签名 + 异常抛
  NotImplementedError（fail-closed，触发路由降级）。
- 实测证据：`news_cctv("20260908")` 返回 12 行（date/title/content）。
- 归类：上游改动 / 本地更强。不移植。

### 3. 行业资金流 —— 本地早已移植且多一层 fallback
- 上游：`stock_board_industry_fund_flow_em(symbol="今日")` → `stock_fund_flow_industry(symbol="即时")`。
- 本地：主源已是 `stock_fund_flow_industry(symbol="即时")`，主源失败还有
  `stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")` 兜底。
- 实测证据：1.18.30 中 `stock_board_industry_fund_flow_em` 不存在（NOT_PRESENT），
  印证上游修复方向；本地实现方向一致且更强。
- 归类：上游改动 / 本地已有（超集）。

### 4. 龙虎榜参数 —— 已移植；上游 TypeError 处理【纠正还原，不移植】
- 上游修复 A：移除不存在的 `symbol` 参数、日期去短横线、本地过滤目标股票
  —— 本地已移植且更强：代码列缺失单独归类 `LHB_FAILED`，代码 zfill(6) 归一化。
- 上游修复 B：`except TypeError → "龙虎榜数据尚未更新"`。
  **不移植（round1 曾移植后被协调员否决，本轮已还原）**，理由：
  1. fail-closed 回归：把所有 TypeError 归类 NORMAL_NO_DATA，会让畸形响应、
     处理 bug 伪装成"正常无数据"，抑制失败与降级（本地既有回归
     `test_lhb_type_error_fails_closed` 即守护此点）。
  2. 失败场景未复现：协调员 round1 live 探测两日期均可取数；本轮复核
     `stock_lhb_detail_em("20260908"×2)` 返回 59 行（含"代码"列），
     600036 本地过滤后 0 行 —— "该股未上榜"走既有 NORMAL_NO_DATA 路径即可，
     与"数据未发布"无需区分特殊形态。
  3. 降级链保护：TypeError 现走通用 `except Exception → LHB_FAILED`，
     命中 `_is_failure_result`（interface.py `_FAILURE_RESULT_PATTERNS` 含
     "LHB_FAILED"），正确触发 `cn_tushare → cn_astock` 降级。
- 归类：接口签名漂移（symbol 参数不存在）= 已移植；TypeError 语义 = 按
  fail-closed 纠正；空数据 / 该股未上榜 = NORMAL_NO_DATA；传输/解析故障 =
  LHB_FAILED。四类状态分别归类，不合并。

### 5. 热门数据接口 —— 上游替换不移植（本地选择实测可用）
- 上游：`stock_hot_follow_xq(symbol="最热门")` → `"本周新增"`。
- 本地：保持 `"最热门"`。
- 实测证据（1.18.30）：`"最热门"` 返回 5640 行（股票代码/股票简称/关注/最新价）；
  `"本周新增"` 亦可用 —— 两者在新版并存，本地选择无失效风险，签名守卫锁定
  默认值 `"最热门"` 防漂移。
- 归类：上游改动 / 本地仍有效。不移植。

## Live 探测记录（低频只读，各 1 次调用，不调用 LLM，未写生产 DB）

| 探测 | 参数 | 结果 |
| --- | --- | --- |
| news_cctv | date=20260908 | 12 行 |
| stock_hot_follow_xq | symbol=最热门 | 5640 行 |
| stock_hot_follow_xq | symbol=本周新增 | 5640 行 |
| stock_lhb_detail_em | 20260908~20260908 | 59 行；600036 本地过滤 0 行（正常未上榜） |
| akshare 版本 | — | 1.18.30 |

## round1 既有 diff 处置清单

| 文件 | round1 改动 | 处置 |
| --- | --- | --- |
| `cn_akshare_provider.py` | +10 行：`except TypeError → LHB_NORMAL_NO_DATA` | **还原（git restore）**：恢复严格 fail-closed，TypeError 走 LHB_FAILED |
| `tests/test_upstream_v081_absorption.py` | `test_lhb_type_error_fails_closed` 反转为 `..._means_not_yet_published` | **还原（git restore）**：fail-closed 断言（LHB_FAILED + TypeError）复位 |
| `tests/test_upstream081_akshare_gap.py` | 未完成草稿，含 2 个错误方向断言 | **重写**：TypeError fail-closed 断言替换错误方向用例，其余锁定本地正确行为 |
| `docs/DEVLOG.md` / `docs/TASKS.md` | round1 中止记录 / 主控重派状态 | 保留（TASKS.md 归主控管理） |
