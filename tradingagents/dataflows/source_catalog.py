# [DATA-001] source_catalog
"""
A股数据源能力目录与 fallback 矩阵。

提供统一的 source capability 查询接口：
  - 每种 data_type (quote / fund_flow / lhb / notice / report / ...)
    有明确的主选 vendor / fallback 顺序
  - 每个 source 记录 fields / unit / freshness / rate_limit_risk / known_gaps
  - 缺少字段或单位未知的数据源不得标为 primary

使用示例：
    from tradingagents.dataflows.source_catalog import (
        get_sources_for_type,
        get_primary_source,
        SourceCapability,
    )
    sources = get_sources_for_type("quote")
    for s in sources:
        print(s.vendor, s.fallback_priority, s.fields)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DataType(str, Enum):
    QUOTE = "quote"
    OHLCV = "ohlcv"
    FUND_FLOW = "fund_flow"
    BOARD_FUND_FLOW = "board_fund_flow"
    LHB = "lhb"
    MARGIN_TRADING = "margin_trading"
    NOTICE = "notice"
    REPORT = "report"
    RATING = "rating"
    NEWS = "news"
    GLOBAL_NEWS = "global_news"
    FINANCIALS = "financials"
    INSIDER = "insider"
    HOT_STOCKS = "hot_stocks"
    ZT_POOL = "zt_pool"
    REALTIME_QUOTES = "realtime_quotes"
    BUYBACK = "buyback"  # [DATA-013] buyback_raw_evidence


class Freshness(str, Enum):
    REALTIME = "realtime"
    INTRADAY = "intraday"
    DAILY = "daily"
    DELAYED = "delayed"
    STALE = "stale"
    UNKNOWN = "unknown"


class RateLimitRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass
class SourceCapability:
    vendor: str
    endpoint: str
    data_type: DataType
    fields: List[str] = field(default_factory=list)
    unit: str = ""
    freshness: Freshness = Freshness.UNKNOWN
    rate_limit_risk: RateLimitRisk = RateLimitRisk.UNKNOWN
    fallback_priority: int = 99
    known_gaps: List[str] = field(default_factory=list)
    is_primary: bool = False
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "data_type": self.data_type.value,
            "fields": self.fields,
            "unit": self.unit,
            "freshness": self.freshness.value,
            "rate_limit_risk": self.rate_limit_risk.value,
            "fallback_priority": self.fallback_priority,
            "known_gaps": self.known_gaps,
            "is_primary": self.is_primary,
            "notes": self.notes,
        }

    @property
    def can_be_primary(self) -> bool:
        if not self.fields:
            return False
        if not self.unit and self.data_type in (
            DataType.FUND_FLOW,
            DataType.BOARD_FUND_FLOW,
            DataType.OHLCV,
            DataType.QUOTE,
            DataType.REALTIME_QUOTES,
        ):
            return False
        if self.freshness == Freshness.UNKNOWN:
            return False
        if self.freshness == Freshness.STALE:
            return False
        return True


_SOURCE_CATALOG: List[SourceCapability] = []


def _register(
    vendor: str,
    endpoint: str,
    data_type: DataType,
    *,
    fields: Optional[List[str]] = None,
    unit: str = "",
    freshness: Freshness = Freshness.UNKNOWN,
    rate_limit_risk: RateLimitRisk = RateLimitRisk.UNKNOWN,
    fallback_priority: int = 99,
    known_gaps: Optional[List[str]] = None,
    is_primary: bool = False,
    notes: str = "",
) -> None:
    cap = SourceCapability(
        vendor=vendor,
        endpoint=endpoint,
        data_type=data_type,
        fields=fields or [],
        unit=unit,
        freshness=freshness,
        rate_limit_risk=rate_limit_risk,
        fallback_priority=fallback_priority,
        known_gaps=known_gaps or [],
        is_primary=is_primary,
        notes=notes,
    )
    if is_primary and not cap.can_be_primary:
        cap.is_primary = False
    _SOURCE_CATALOG.append(cap)


def _build_catalog() -> None:
    if _SOURCE_CATALOG:
        return

    # ── OHLCV / K-line ──────────────────────────────────────────────────
    _register(
        "cn_akshare", "stock_zh_a_hist",
        DataType.OHLCV,
        fields=["Date", "Open", "High", "Low", "Close", "Volume"],
        unit="元/股, 股",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        notes="东财前复权日线，通过 AKShare 调用；盘中实时 patch 由 G-005 补丁机制提供",
    )
    _register(
        "cn_akshare", "stock_zh_a_daily",
        DataType.OHLCV,
        fields=["Date", "Open", "High", "Low", "Close", "Volume"],
        unit="元/股, 股",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=2,
        known_gaps=["新浪历史接口偶有停机"],
        notes="新浪前复权日线，cn_akshare 内部 fallback",
    )
    _register(
        "cn_akshare", "stock_zh_a_hist_tx",
        DataType.OHLCV,
        fields=["Date", "Open", "High", "Low", "Close", "Volume"],
        unit="元/股, 股",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=3,
        known_gaps=["腾讯历史接口数据覆盖不如东财"],
        notes="腾讯前复权日线，cn_akshare 内部 fallback",
    )
    _register(
        "cn_astock", "finance.pae.baidu.com/kline",
        DataType.OHLCV,
        fields=["Date", "Open", "High", "Low", "Close", "Volume", "Amount"],
        unit="未知",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=4,
        known_gaps=["adjustment type unknown", "amount 单位不明确"],
        notes="百度股市通 K 线，cn_astock 内部首选",
    )
    _register(
        "cn_astock", "push2his.eastmoney.com/kline",
        DataType.OHLCV,
        fields=["Date", "Open", "High", "Low", "Close", "Volume"],
        unit="元/股, 股",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=5,
        notes="东财 push2his 前复权 K 线，cn_astock 内部 fallback",
    )
    _register(
        "cn_baostock", "query_history_k_data_plus",
        DataType.OHLCV,
        fields=["Date", "Open", "High", "Low", "Close", "Volume"],
        unit="元/股, 股",
        freshness=Freshness.DELAYED,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=6,
        known_gaps=["前复权 adjustflag=2，更新有延迟"],
        notes="BaoStock 日线，延迟较大但稳定",
    )
    _register(
        "cn_tushare", "daily + adj_factor",
        DataType.OHLCV,
        fields=["Date", "Open", "High", "Low", "Close", "Volume", "Amount"],
        unit="元/股, 股, 元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=7,
        known_gaps=["非实时行情", "需要已配置的 Tushare Pro 凭据"],
        notes="Tushare Pro 日线与复权因子；provider 统一换算成交量和成交额单位",
    )

    # ── 实时行情 ────────────────────────────────────────────────────────
    _register(
        "cn_akshare", "hq.sinajs.cn",
        DataType.REALTIME_QUOTES,
        fields=["price", "open", "high", "low", "previous_close", "change",
                "change_pct", "volume", "amount", "quote_time"],
        unit="元, 股, 元",
        freshness=Freshness.REALTIME,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=1,
        is_primary=True,
        notes="新浪实时行情，轻量且稳定，cn_akshare 的首选实时源",
    )
    _register(
        "cn_akshare", "stock_zh_a_spot_em",
        DataType.REALTIME_QUOTES,
        fields=["price", "open", "high", "low", "previous_close", "change",
                "change_pct", "volume", "amount"],
        unit="元, 股, 元",
        freshness=Freshness.REALTIME,
        rate_limit_risk=RateLimitRisk.HIGH,
        fallback_priority=2,
        known_gaps=["东财全量接口需串行限流", "有 8s TTL 缓存"],
        notes="东财全市场快照，cn_akshare 的 fallback 实时源",
    )
    _register(
        "cn_astock", "qt.gtimg.cn",
        DataType.REALTIME_QUOTES,
        fields=["price", "open", "high", "low", "previous_close", "change",
                "change_pct", "volume", "amount", "turnover_rate", "volume_ratio",
                "limit_up", "limit_down", "market_cap", "pe_ttm", "pe_static", "pb"],
        unit="元, 股, 元, %, 亿元",
        freshness=Freshness.REALTIME,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=3,
        notes="腾讯实时行情，字段最丰富（PE/PB/换手率/量比/涨停价）",
    )

    # ── 资金流 / 个股资金 ──────────────────────────────────────────────
    _register(
        "cn_tushare", "moneyflow",
        DataType.FUND_FLOW,
        fields=["日期", "主力净流入(大单+超大单)", "L2总净流入", "小单买卖额", "中单买卖额", "大单买卖额", "超大单买卖额"],
        unit="万元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=1,
        is_primary=True,
        known_gaps=["需要已配置的 Tushare Pro 凭据", "非板块资金流"],
        notes="Tushare Pro 个股资金流；主力口径由大单与超大单买卖额确定性计算，单位为万元",
    )
    _register(
        "cn_akshare", "stock_individual_fund_flow",
        DataType.FUND_FLOW,
        fields=["日期", "主力净流入", "小单净流入", "中单净流入", "大单净流入", "超大单净流入"],
        unit="万元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.HIGH,
        fallback_priority=2,
        known_gaps=["高限流风险", "ConnectionError 常见"],
        notes="AKShare/东财个股资金流，限流风险高，fallback 必须可切换",
    )
    _register(
        "cn_astock", "push2his.eastmoney.com/fflow",
        DataType.FUND_FLOW,
        fields=["日期", "主力净流入", "小单净流入", "中单净流入", "大单净流入", "超大单净流入"],
        unit="万元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=3,
        notes="东财 push2his 直连个股资金流，不经过 AKShare",
    )

    # ── 板块资金流 ──────────────────────────────────────────────────────
    _register(
        "cn_akshare", "stock_fund_flow_industry",
        DataType.BOARD_FUND_FLOW,
        fields=["行业", "行业指数", "流入资金", "流出资金", "净额"],
        unit="亿元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        notes="AKShare/同花顺行业板块资金流",
    )
    _register(
        "cn_akshare", "stock_sector_fund_flow_rank",
        DataType.BOARD_FUND_FLOW,
        fields=["板块名称", "主力净流入"],
        unit="万元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        known_gaps=["概念板块，非行业板块"],
        notes="AKShare fallback 板块资金流",
    )
    _register(
        "cn_astock", "push2.eastmoney.com/clist",
        DataType.BOARD_FUND_FLOW,
        fields=["板块代码", "板块名称", "涨跌幅", "主力净流入"],
        unit="万元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=3,
        notes="东财 push2 直连板块资金流",
    )

    # ── 龙虎榜 ─────────────────────────────────────────────────────────
    _register(
        "cn_tushare", "top_list",
        DataType.LHB,
        fields=["证券代码", "证券简称", "上榜原因", "买入额", "卖出额", "净买入额"],
        unit="万元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=1,
        is_primary=True,
        known_gaps=["仅异动日有记录", "需要 force=True", "需要已配置的 Tushare Pro 凭据"],
        notes="Tushare Pro 龙虎榜；空表表示非异动日正常无数据",
    )
    _register(
        "cn_akshare", "stock_lhb_detail_em",
        DataType.LHB,
        fields=["代码", "名称", "上榜原因", "买入额", "卖出额", "净买额"],
        unit="万元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.HIGH,
        fallback_priority=2,
        known_gaps=["高限流风险", "force=False 时返回 NOT_QUERIED"],
        notes="AKShare/东财龙虎榜，必须 force=True 才实际查询",
    )
    _register(
        "cn_astock", "datacenter-web.eastmoney.com/RPT_DAILYBILLBOARD",
        DataType.LHB,
        fields=["SECURITY_CODE", "EXPLANATION", "BILLBOARD_NET_AMT", "BUY_AMOUNT", "SELL_AMOUNT"],
        unit="元(需/10000转万元)",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=3,
        notes="东财 datacenter 直连龙虎榜",
    )

    # ── 融资融券 ────────────────────────────────────────────────────────
    _register(
        "cn_tushare", "margin_detail",
        DataType.MARGIN_TRADING,
        fields=["证券代码", "融资余额", "融券余额", "融资买入额", "融券卖出量"],
        unit="元, 股",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=1,
        is_primary=True,
        known_gaps=["非两融标的或无交易时为空", "需要已配置的 Tushare Pro 凭据"],
        notes="Tushare Pro 个股融资融券明细",
    )
    _register(
        "cn_akshare", "stock_margin_underlying_info_szse",
        DataType.MARGIN_TRADING,
        fields=["标的证券代码", "标的证券简称", "融资买入额", "融资余额", "融券卖出量", "融券余量"],
        unit="万元/股",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        notes="AKShare 深交所融资融券标的；沪市用 stock_margin_underlying_info_sse",
    )
    _register(
        "cn_astock", "datacenter-web.eastmoney.com/RPT_RZRQ_LSHJ",
        DataType.MARGIN_TRADING,
        fields=["RZRQ_YE", "RZRQ_MRE", "RQYE", "RQMRL", "RZRQ_JME"],
        unit="元(需/10000转万元)",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=3,
        notes="东财 datacenter 融资融券汇总",
    )

    # ── 公告 ───────────────────────────────────────────────────────────
    _register(
        "cn_astock", "cninfo.com.cn/hisAnnouncement",
        DataType.NOTICE,
        fields=["announcementTitle", "announcementTypeName", "announcementTime", "announcementId"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=1,
        is_primary=True,
        notes="巨潮公告全文检索，需 orgId 映射",
    )
    _register(
        "cn_akshare", "stock_notice_report",
        DataType.NOTICE,
        fields=["公告标题", "公告类型", "公告日期"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        known_gaps=["AKShare 公告接口不稳定", "字段覆盖不完整"],
        notes="AKShare 公告接口，可靠性较低",
    )

    # ── 研报 / 评级 ────────────────────────────────────────────────────
    _register(
        "cn_akshare", "stock_research_report_em",
        DataType.REPORT,
        fields=["日期", "机构", "东财评级", "报告名称", "盈利预测", "报告PDF链接"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        known_gaps=["仅为研报元数据/观点源，不替代公告和财报原文"],
        notes="AKShare 东方财富个股研报元数据与 PDF 链接",
    )
    _register(
        "cn_astock", "reportapi.eastmoney.com/report/list",
        DataType.REPORT,
        fields=["publishDate", "orgSName", "title", "emRatingName", "predictThisYearEps"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        notes="东财研报列表，支持分页",
    )

    # ── 评级（分析师评级/目标价，区别于研报全文）──────────────────────────
    _register(
        "cn_akshare", "stock_institute_recommend_detail",
        DataType.RATING,
        fields=["股票代码", "股票名称", "目标价", "最新评级", "评级机构", "分析师", "行业", "评级日期"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        known_gaps=["新浪财经评级数据覆盖不全"],
        notes="AKShare 新浪财经股票评级记录",
    )
    _register(
        "cn_astock", "reportapi.eastmoney.com/report/list",
        DataType.RATING,
        fields=["publishDate", "orgSName", "emRatingName", "lastEmRatingName", "emRatingChange", "indvAimPriceT", "title"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        notes="东财研报列表提取评级变更与目标价",
    )

    # ── 新闻 ───────────────────────────────────────────────────────────
    _register(
        "cn_akshare", "stock_news_em",
        DataType.NEWS,
        fields=["新闻标题", "文章来源", "新闻内容", "新闻链接", "发布时间"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        notes="AKShare/东财个股新闻",
    )
    _register(
        "cn_astock", "search-api-web.eastmoney.com",
        DataType.NEWS,
        fields=["title", "mediaName", "content", "url", "date"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        notes="东财搜索接口个股新闻 (JSONP)",
    )

    # ── 全球新闻 ───────────────────────────────────────────────────────
    _register(
        "cn_akshare", "news_cctv",
        DataType.GLOBAL_NEWS,
        fields=["title", "content"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=1,
        is_primary=True,
        notes="央视新闻，AKShare 封装",
    )
    _register(
        "cn_astock", "cls.cn/telegraphList",
        DataType.GLOBAL_NEWS,
        fields=["title", "content", "ctime"],
        unit="条",
        freshness=Freshness.REALTIME,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=2,
        notes="财联社电报，实时性强",
    )
    _register(
        "cn_astock", "np-weblist.eastmoney.com/getFastNewsList",
        DataType.GLOBAL_NEWS,
        fields=["title", "summary", "showTime"],
        unit="条",
        freshness=Freshness.REALTIME,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=3,
        notes="东财 7x24 快讯",
    )

    # ── 财务三表 ───────────────────────────────────────────────────────
    _register(
        "cn_tushare", "stock_basic + stock_company + daily_basic + income + balancesheet + cashflow + fina_indicator + forecast",
        DataType.FINANCIALS,
        fields=[
            "证券代码", "证券简称", "所属行业", "交易所", "主营业务",
            "公告日期", "实际公告日期", "报告日", "报告类型", "更新标记",
            "利润表", "资产负债表", "现金流量表", "财务指标", "估值快照", "业绩预告",
        ],
        unit="元, %, 倍, 万元",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=1,
        is_primary=True,
        known_gaps=["非实时行情", "公告原文仍以交易所/巨潮为准", "需要已配置的 Tushare Pro 凭据"],
        notes="Tushare Pro 结构化财务主源；保留期间口径、公告日和更新标记",
    )
    _register(
        "cn_akshare", "stock_individual_info_em",
        DataType.FINANCIALS,
        fields=["公司基本信息"],
        unit="项",
        freshness=Freshness.DELAYED,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        notes="AKShare/东财个股基本信息",
    )
    _register(
        "cn_akshare", "stock_financial_report_sina",
        DataType.FINANCIALS,
        fields=["报告日", "资产负债表/利润表/现金流量表"],
        unit="元",
        freshness=Freshness.DELAYED,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=3,
        notes="新浪财报三表",
    )
    _register(
        "cn_astock", "quotes.sina.cn/CompanyFinanceService",
        DataType.FINANCIALS,
        fields=["报告日", "资产负债表/利润表/现金流量表"],
        unit="元",
        freshness=Freshness.DELAYED,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=4,
        notes="新浪财报三表直连",
    )
    _register(
        "cn_baostock", "query_profit_data / query_balance_data / query_cash_flow_data",
        DataType.FINANCIALS,
        fields=["报告期", "营业收入", "净利润", "总资产", "负债合计"],
        unit="元",
        freshness=Freshness.DELAYED,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=5,
        known_gaps=["接口封装中，暂未暴露到 provider 层"],
        notes="BaoStock 财务数据，当前 provider 未实现（NotImplementedError）",
    )

    # ── 内部交易 / 股东 ────────────────────────────────────────────────
    _register(
        "cn_akshare", "stock_main_stock_holder",
        DataType.INSIDER,
        fields=["股东名称", "持股数", "增减"],
        unit="股",
        freshness=Freshness.DELAYED,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        notes="AKShare 十大股东",
    )
    _register(
        "cn_astock", "datacenter-web.eastmoney.com/RPT_INSIDER_SHAREHOLDERS",
        DataType.INSIDER,
        fields=["CHANGE_DATE", "HOLDER_NAME", "CHANGE_TYPE", "CHANGE_SHARES", "AVG_PRICE"],
        unit="股",
        freshness=Freshness.DELAYED,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        notes="东财 datacenter 股东持股变动",
    )

    # ── 涨停池 ─────────────────────────────────────────────────────────
    _register(
        "cn_akshare", "stock_zt_pool_em",
        DataType.ZT_POOL,
        fields=["代码", "名称", "涨停价", "连板数"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        notes="AKShare/东财涨停池",
    )
    _register(  # [DATA-015] limit_up_pool_fallback
        "cn_astock", "push2ex.eastmoney.com/getTopicZTPool",
        DataType.ZT_POOL,
        fields=["SECURITY_CODE", "SECURITY_NAME_ABBR", "EXPLANATION", "CHANGE_RATE"],
        unit="条",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        notes="东财 datacenter + push2ex 涨停池直连 fallback",
    )

    # ── 热门股票 ───────────────────────────────────────────────────────
    _register(
        "cn_akshare", "stock_hot_follow_xq",
        DataType.HOT_STOCKS,
        fields=["代码", "名称", "热度"],
        unit="条",
        freshness=Freshness.INTRADAY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        notes="雪球热搜股票",
    )
    _register(  # [DATA-016] hot_stock_fallback
        "cn_astock", "push2.eastmoney.com/getHotStock",
        DataType.HOT_STOCKS,
        fields=["SECURITY_CODE", "SECURITY_NAME_ABBR", "CHANGE_RATE", "TRADE_VOLUME"],
        unit="条",
        freshness=Freshness.INTRADAY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=2,
        notes="东财热榜 push2 直连 fallback",
    )

    # ── 回购 ──────────────────────────────────────────────────────────
    # [DATA-013] buyback_raw_evidence
    _register(
        "cn_akshare", "stock_repurchase_em",
        DataType.BUYBACK,
        fields=["股票代码", "最新公告日期", "已回购金额", "已回购股份数量", "实施进度"],
        unit="元, 股, 元/股",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=1,
        is_primary=True,
        notes="AKShare 全市场回购表，provider 按股票代码严格过滤",
    )
    _register(
        "cn_astock", "datacenter-web.eastmoney.com/RPT_SHAREBUYBACK",
        DataType.BUYBACK,
        fields=["NOTICE_DATE", "BUYBACK_AMOUNT", "BUYBACK_VOLUME", "PROGRESS", "PURPOSE"],
        unit="元(需/10000转万元)",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.MEDIUM,
        fallback_priority=3,
        notes="东财 datacenter 回购数据",
    )
    _register(
        "cn_tushare", "repurchase",
        DataType.BUYBACK,
        fields=["证券代码", "公告日期", "截止日期", "实施进度", "回购数量", "回购金额"],
        unit="股, 元, 元/股",
        freshness=Freshness.DAILY,
        rate_limit_risk=RateLimitRisk.LOW,
        fallback_priority=2,
        known_gaps=["公告原文仍以交易所/巨潮为准", "需要已配置的 Tushare Pro 凭据"],
        notes="Tushare Pro 结构化回购记录 fallback",
    )


_build_catalog()


def get_sources_for_type(data_type: str | DataType) -> List[SourceCapability]:
    if isinstance(data_type, str):
        try:
            data_type = DataType(data_type)
        except ValueError:
            return []
    return sorted(
        [s for s in _SOURCE_CATALOG if s.data_type == data_type],
        key=lambda s: s.fallback_priority,
    )


def get_primary_source(data_type: str | DataType) -> Optional[SourceCapability]:
    sources = get_sources_for_type(data_type)
    for s in sources:
        if s.is_primary:
            return s
    return None


def get_fallback_chain(data_type: str | DataType) -> List[str]:
    sources = get_sources_for_type(data_type)
    return [s.vendor for s in sources]


def get_all_data_types() -> List[str]:
    seen = set()
    result = []
    for s in _SOURCE_CATALOG:
        if s.data_type.value not in seen:
            seen.add(s.data_type.value)
            result.append(s.data_type.value)
    return result


def get_catalog_summary() -> List[Dict[str, Any]]:
    return [s.to_dict() for s in _SOURCE_CATALOG]


def get_vendor_capabilities(vendor: str) -> List[SourceCapability]:
    return [s for s in _SOURCE_CATALOG if s.vendor == vendor]


def validate_catalog() -> List[str]:
    issues = []
    for dt in DataType:
        sources = get_sources_for_type(dt)
        if not sources:
            continue
        primaries = [s for s in sources if s.is_primary]
        if primaries:
            p = primaries[0]
            if not p.can_be_primary:
                issues.append(
                    f"{dt.value}: primary source '{p.vendor}/{p.endpoint}' "
                    f"fails can_be_primary check (fields={len(p.fields)}, "
                    f"unit='{p.unit}', freshness={p.freshness.value})"
                )
        no_fields = [s for s in sources if not s.fields]
        for s in no_fields:
            issues.append(
                f"{dt.value}: source '{s.vendor}/{s.endpoint}' has empty fields list"
            )
    seen_keys = set()
    for s in _SOURCE_CATALOG:
        key = (s.vendor, s.endpoint, s.data_type)
        if key in seen_keys:
            issues.append(
                f"duplicate entry: vendor={s.vendor} endpoint={s.endpoint} "
                f"data_type={s.data_type.value}"
            )
        seen_keys.add(key)
    return issues
