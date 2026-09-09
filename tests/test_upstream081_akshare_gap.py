"""[UPSTREAM-081-003 re-dispatch] AKShare v0.8.1 差异审计与最小修复 — 上游 aa79bca (#195) 专项测试.

审计结论（证据见 docs/data_source_reports/akshare-v081-upstream-audit-2026-09-09.md；
round1 方向已被协调员否决：不得把 TypeError 一律归类为 NORMAL_NO_DATA）：

1. 雪球 token：上游为 stock_individual_spot_xq 增加 token 参数透传 —— 本地已移植
   （cn_akshare_provider._fetch_realtime_row_unlocked，XQ_A_TOKEN 环境变量），
   本组测试锁定该行为，并验证无凭据/凭据失败时 fail-closed（异常传播，不伪造行情行）。
2. 全球新闻：上游用新浪 zhibo 私有接口整体替换 news_cctv，并删除 look_back_days/limit
   签名（破坏本地 base.py 接口契约）—— 不移植。实测 akshare 1.18.30 的 news_cctv
   可用（20260908 返回 12 行）；本组测试锁定 news_cctv 主路径与 fail-closed 语义。
3. 行业资金流：上游 stock_board_industry_fund_flow_em → stock_fund_flow_industry
   —— 本地早已是 stock_fund_flow_industry 主源 + stock_sector_fund_flow_rank
   fallback（比上游修复多一层降级），无需改动；本组测试锁定主源调用与 fallback 链。
4. 龙虎榜参数：上游移除不存在的 symbol 参数并本地过滤 —— 本地已移植且更强
   （代码列缺失单独归类 LHB_FAILED）。上游另一改动"TypeError → 数据尚未更新"
   【不移植】：协调员 live 探测与本轮复核（20260908 返回 59 行）均证明数据实际
   可取，"未发布"场景未复现；TypeError 一律归 NORMAL_NO_DATA 会把畸形响应、
   处理 bug 伪装成正常无数据并反转 fail-closed 回归。TypeError 必须走
   LHB_FAILED 并触发路由层 provider 降级。
5. 热门数据接口：上游 "最热门" → "本周新增" —— 不移植，实测 akshare 1.18.30 的
   stock_hot_follow_xq 对 "最热门" 正常返回（5640 行）；本组测试锁定现行选择，
   防止无意漂移。

本轮差异审计后无需修改 provider：所有仍有效的上游修复本地均已存在；
本文件负责锁定现状并守护 fail-closed 语义。所有用例使用 fake akshare，
不触网、不调用 LLM。
"""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
from tradingagents.default_config import DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Fake akshare
# ---------------------------------------------------------------------------


class _FakeAkshare:
    """Record kwargs and replay fixture frames; raises scripted errors once per call."""

    def __init__(self) -> None:
        self.spot_kwargs: dict = {}
        self.spot_error: Exception | None = None
        self.news_cctv_calls: list[str] = []
        self.news_by_date: dict[str, pd.DataFrame | Exception] = {}
        self.industry_symbol: str | None = None
        self.industry_error: Exception | None = None
        self.sector_rank_kwargs: dict | None = None
        self.lhb_kwargs: dict = {}
        self.lhb_error: Exception | None = None
        self.lhb_frame: pd.DataFrame | None = None
        self.hot_symbol: str | None = None
        self.hot_frame: pd.DataFrame | None = pd.DataFrame(
            {
                "股票代码": ["603629", "600519"],
                "股票简称": ["利通电子", "贵州茅台"],
                "关注": [120000, 99000000],
                "最新价": [10.2, 1500.0],
            }
        )
        self.hot_error: Exception | None = None

    # ── 雪球 spot ──
    def stock_individual_spot_xq(self, **kwargs):
        self.spot_kwargs = kwargs
        if self.spot_error is not None:
            raise self.spot_error
        return pd.DataFrame(
            {
                "item": ["时间", "今开", "最高", "最低", "现价", "成交量"],
                "value": ["2026-09-04", 10.0, 10.5, 9.8, 10.2, 1000],
            }
        )

    # ── 全球新闻 ──
    def news_cctv(self, date: str):
        self.news_cctv_calls.append(date)
        item = self.news_by_date.get(date)
        if isinstance(item, Exception):
            raise item
        if item is None:
            return pd.DataFrame()
        return item

    # ── 行业资金流 ──
    def stock_fund_flow_industry(self, *, symbol: str):
        self.industry_symbol = symbol
        if self.industry_error is not None:
            raise self.industry_error
        return pd.DataFrame(
            {
                "行业": ["半导体", "化工"],
                "净额": ["1.2亿", "9000万"],
            }
        )

    def stock_sector_fund_flow_rank(self, *, indicator: str, sector_type: str):
        self.sector_rank_kwargs = {"indicator": indicator, "sector_type": sector_type}
        return pd.DataFrame(
            {
                "板块名称": ["白酒", "光伏"],
                "主力净流入": ["5.6亿", "-2.1亿"],
            }
        )

    # ── 龙虎榜 ──
    def stock_lhb_detail_em(self, **kwargs):
        self.lhb_kwargs = kwargs
        if self.lhb_error is not None:
            raise self.lhb_error
        if self.lhb_frame is None:
            return pd.DataFrame()
        return self.lhb_frame

    # ── 雪球热搜 ──
    def stock_hot_follow_xq(self, *, symbol: str):
        self.hot_symbol = symbol
        if self.hot_error is not None:
            raise self.hot_error
        return self.hot_frame


def _provider_with(fake: _FakeAkshare) -> CnAkshareProvider:
    provider = CnAkshareProvider()
    provider._ak = lambda: fake
    return provider


# ---------------------------------------------------------------------------
# [UPSTREAM-081-003] Tushare 主源优先级不降低
# ---------------------------------------------------------------------------


def test_lhb_vendor_chain_keeps_tushare_primary():
    chain = DEFAULT_CONFIG["tool_vendors"]["get_lhb_detail"].split(",")
    assert chain[0] == "cn_tushare"
    assert "cn_akshare" in chain


# ---------------------------------------------------------------------------
# 1. 雪球 token（上游已移植项 —— 锁定行为 + 权限失败 fail-closed）
# ---------------------------------------------------------------------------


def test_xq_spot_forwards_env_token(monkeypatch):
    fake = _FakeAkshare()
    monkeypatch.setenv("XQ_A_TOKEN", "audit-token-081003")

    result = _provider_with(fake)._fetch_realtime_row_unlocked("600036.SH")

    assert fake.spot_kwargs == {"symbol": "SH600036", "token": "audit-token-081003"}
    assert not result.empty


def test_xq_spot_without_env_token_passes_none(monkeypatch):
    fake = _FakeAkshare()
    monkeypatch.delenv("XQ_A_TOKEN", raising=False)

    _provider_with(fake)._fetch_realtime_row_unlocked("600036.SH")

    assert fake.spot_kwargs["token"] is None


def test_xq_permission_failure_fails_closed_without_fabricated_rows(monkeypatch):
    # 雪球无有效 token 时上游实测抛 KeyError('data')（权限/凭据失败）：
    # 提供方必须让异常传播（fail-closed），不得伪造空 OHLCV 行冒充成功。
    fake = _FakeAkshare()
    fake.spot_error = KeyError("data")
    monkeypatch.delenv("XQ_A_TOKEN", raising=False)

    with pytest.raises(KeyError):
        _provider_with(fake)._fetch_realtime_row_unlocked("600036.SH")


# ---------------------------------------------------------------------------
# 2. 全球新闻（上游替换项 —— 不移植，锁定 news_cctv 主路径与状态分类）
# ---------------------------------------------------------------------------


def test_global_news_still_uses_news_cctv_with_compact_date():
    fake = _FakeAkshare()
    fake.news_by_date["20260904"] = pd.DataFrame(
        {
            "title": ["国务院常务会议"],
            "content": ["部署促进平台经济健康发展措施"],
        }
    )

    result = _provider_with(fake).get_global_news("2026-09-04", look_back_days=7, limit=30)

    assert fake.news_cctv_calls == ["20260904"]
    assert "全球市场新闻" in result
    assert "国务院常务会议" in result


def test_global_news_empty_day_falls_back_within_3_days_and_labels_date():
    fake = _FakeAkshare()
    fake.news_by_date["20260903"] = pd.DataFrame(
        {"title": ["央行降准"], "content": ["释放长期资金"]}
    )

    result = _provider_with(fake).get_global_news("2026-09-04")

    assert fake.news_cctv_calls[0] == "20260904"
    assert "20260903" in fake.news_cctv_calls
    assert "回退至 2026-09-03" in result


def test_global_news_all_empty_is_normal_no_data_not_failure():
    fake = _FakeAkshare()

    result = _provider_with(fake).get_global_news("2026-09-04")

    assert len(fake.news_cctv_calls) == 4
    assert "未获取到全球市场新闻" in result
    assert "获取失败" not in result


def test_global_news_provider_error_raises_not_implemented():
    fake = _FakeAkshare()
    fake.news_by_date["20260904"] = ConnectionError("network down")

    with pytest.raises(NotImplementedError):
        _provider_with(fake).get_global_news("2026-09-04")


# ---------------------------------------------------------------------------
# 3. 行业资金流（上游已移植项 —— 锁定主源 + fallback 链）
# ---------------------------------------------------------------------------


def test_board_fund_flow_primary_uses_stock_fund_flow_industry():
    fake = _FakeAkshare()

    result = _provider_with(fake).get_board_fund_flow()

    assert fake.industry_symbol == "即时"
    assert fake.sector_rank_kwargs is None
    assert "半导体" in result
    assert result.index("半导体") < result.index("化工")


def test_board_fund_flow_falls_back_to_sector_rank_on_primary_failure():
    fake = _FakeAkshare()
    fake.industry_error = ConnectionError("ths blocked")

    result = _provider_with(fake).get_board_fund_flow()

    assert fake.sector_rank_kwargs == {"indicator": "今日", "sector_type": "行业资金流"}
    assert "fallback" in result
    assert "白酒" in result


def test_board_fund_flow_double_failure_reports_failure_for_router():
    class _BothFailFake(_FakeAkshare):
        def stock_fund_flow_industry(self, *, symbol: str):
            self.industry_symbol = symbol
            raise ConnectionError("ths blocked")

        def stock_sector_fund_flow_rank(self, *, indicator: str, sector_type: str):
            self.sector_rank_kwargs = {"indicator": indicator, "sector_type": sector_type}
            raise ConnectionError("em blocked")

    result = _provider_with(_BothFailFake()).get_board_fund_flow()

    assert "获取失败" in result


# ---------------------------------------------------------------------------
# 4. 龙虎榜（fail-closed：TypeError/畸形响应/接口缺失必须走 LHB_FAILED，
#    不得伪装成 NORMAL_NO_DATA —— round1 方向已否决）
# ---------------------------------------------------------------------------


def _lhb_frame_with_leading_zero_numeric_code() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "代码": ["603629", 1],  # 数值型 1 → zfill(6) → "000001"
            "名称": ["利通电子", "平安银行"],
            "上榜原因": ["日涨幅偏离值达7%", "日振幅达15%"],
        }
    )


def test_lhb_compact_date_and_target_stock_filter_only():
    fake = _FakeAkshare()
    fake.lhb_frame = _lhb_frame_with_leading_zero_numeric_code()

    result = _provider_with(fake).get_lhb_detail("000001.SZ", "2026-09-04", force=True)

    assert fake.lhb_kwargs == {"start_date": "20260904", "end_date": "20260904"}
    assert "LHB_HAS_DATA" in result
    assert "平安银行" in result
    assert "利通电子" not in result  # 全市场结果不得混入目标股票行


def test_lhb_no_match_after_filter_is_normal_no_data():
    # 只有上游明确返回空/本地过滤后无记录才允许 NORMAL_NO_DATA。
    fake = _FakeAkshare()
    fake.lhb_frame = _lhb_frame_with_leading_zero_numeric_code()

    result = _provider_with(fake).get_lhb_detail("600519.SH", "2026-09-04", force=True)

    assert fake.lhb_kwargs == {"start_date": "20260904", "end_date": "20260904"}
    assert "LHB_NORMAL_NO_DATA" in result
    assert "非异动日属正常" in result


def test_lhb_type_error_fails_closed_not_marketed_as_no_data():
    # [re-dispatch 核心纠正] 东财 data_json["result"]=null 或畸形响应都可能以
    # TypeError 冒头；无法区分"未发布"与"接口/处理故障"时必须 fail-closed。
    # 禁止把 TypeError 归类为 LHB_NORMAL_NO_DATA（round1 已否决方向）。
    fake = _FakeAkshare()
    fake.lhb_error = TypeError("'NoneType' object is not subscriptable")

    result = _provider_with(fake).get_lhb_detail("603629.SH", "2026-09-04", force=True)

    assert "LHB_FAILED" in result
    assert "TypeError" in result
    assert "LHB_NORMAL_NO_DATA" not in result
    assert "尚未更新" not in result


def test_lhb_type_error_result_is_router_failure_and_triggers_fallback():
    # TypeError 产物必须被路由层识别为失败，触发 cn_tushare → cn_astock 降级，
    # 而不是被当成正常无数据静默通过。
    from tradingagents.dataflows.interface import _is_failure_result

    fake = _FakeAkshare()
    fake.lhb_error = TypeError("'NoneType' object is not subscriptable")

    result = _provider_with(fake).get_lhb_detail("603629.SH", "2026-09-04", force=True)

    assert _is_failure_result(result)


def test_lhb_transport_error_still_fails_closed():
    fake = _FakeAkshare()
    fake.lhb_error = ConnectionError("eastmoney unreachable")

    result = _provider_with(fake).get_lhb_detail("603629.SH", "2026-09-04", force=True)

    assert "LHB_FAILED" in result
    assert "ConnectionError" in result


def test_lhb_missing_interface_is_classified_not_failed_silently():
    # 接口不存在（AttributeError）与空数据（NORMAL_NO_DATA）分别归类。
    class _NoLhbAkshare:
        def __getattr__(self, name):
            raise AttributeError(name)

    provider = _provider_with(_NoLhbAkshare())

    result = provider.get_lhb_detail("603629.SH", "2026-09-04", force=True)

    assert "LHB_FAILED" in result
    assert "AttributeError" in result


def test_lhb_gate_not_queried_without_force():
    fake = _FakeAkshare()

    result = _provider_with(fake).get_lhb_detail("603629.SH", "2026-09-04", force=False)

    assert "LHB_NOT_QUERIED" in result
    assert fake.lhb_kwargs == {}  # 未触发实际查询


# ---------------------------------------------------------------------------
# 5. 雪球热搜（上游替换项 —— 不移植，锁定 "最热门"；实测 1.18.30 可用）
# ---------------------------------------------------------------------------


def test_hot_stocks_keeps_zuiremen_choice_and_top20_format():
    fake = _FakeAkshare()

    result = _provider_with(fake).get_hot_stocks_xq()

    assert fake.hot_symbol == "最热门"
    assert "雪球热搜前20" in result
    assert "利通电子" in result


def test_hot_stocks_empty_is_normal_no_data():
    fake = _FakeAkshare()
    fake.hot_frame = pd.DataFrame()

    result = _provider_with(fake).get_hot_stocks_xq()

    assert "暂不可用" in result


def test_hot_stocks_error_reports_failure_for_router():
    fake = _FakeAkshare()
    fake.hot_error = ConnectionError("xq blocked")

    result = _provider_with(fake).get_hot_stocks_xq()

    assert "获取失败" in result
    assert "ConnectionError" in result


# ---------------------------------------------------------------------------
# 函数签名漂移守卫（针对本地安装的 akshare 1.18.30；缺失则跳过）
# ---------------------------------------------------------------------------


def test_installed_akshare_signatures_match_provider_contract():
    ak = pytest.importorskip("akshare")

    # 1. 雪球 spot 必须支持 token 透传
    spot_params = inspect.signature(ak.stock_individual_spot_xq).parameters
    assert "symbol" in spot_params
    assert "token" in spot_params

    # 2. 全球新闻 news_cctv 必须存在且接受 date（上游 sina 替换不移植的依据之一）
    cctv_params = inspect.signature(ak.news_cctv).parameters
    assert "date" in cctv_params

    # 3. 行业资金流主源必须存在且接受 symbol
    industry_params = inspect.signature(ak.stock_fund_flow_industry).parameters
    assert "symbol" in industry_params

    # 4. 龙虎榜接口必须没有 symbol 参数（全市场按日期查询，本地过滤目标股票）
    lhb_params = inspect.signature(ak.stock_lhb_detail_em).parameters
    assert "symbol" not in lhb_params
    assert "start_date" in lhb_params
    assert "end_date" in lhb_params

    # 5. 雪球热搜 symbol_map 默认仍是 "最热门"（实测 1.18.30 可用，无需换 "本周新增"）
    hot_params = inspect.signature(ak.stock_hot_follow_xq).parameters
    assert hot_params["symbol"].default == "最热门"
