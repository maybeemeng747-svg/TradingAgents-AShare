from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from tradingagents.dataflows.instrument_identity import (
    IDENTITY_CONFLICT,
    IDENTITY_HAS_DATA,
    IDENTITY_MISSING,
    IDENTITY_PARTIAL,
    build_instrument_identity,
    extract_profile_from_fundamentals,
    render_identity_context,
)


# ── Pure identity contract tests (no provider) ──


def test_complete_profile_allows_commercial_analysis():
    identity = build_instrument_identity(
        "603629.SH",
        {
            "symbol": "603629",
            "security_name": "利通电子",
            "main_business": "算力云服务及精密金属结构件",
            "industry": "计算机设备",
        },
        as_of="2026-07-14",
    )
    assert identity.status == IDENTITY_HAS_DATA
    assert identity.exchange == "SSE"
    assert identity.commercial_analysis_allowed is True


def test_financial_abstract_without_profile_is_not_identity():
    raw = "## Fundamentals for 603629.SH\n\n### Financial Abstract (latest available columns)\n| 指标 | 2025 |"
    profile = extract_profile_from_fundamentals(raw)
    identity = build_instrument_identity("603629.SH", profile)
    assert profile == {}
    assert identity.status == IDENTITY_MISSING
    assert identity.commercial_analysis_allowed is False
    assert "禁止推断商业模式" in render_identity_context(identity.to_dict())


def test_partial_profile_blocks_business_analysis():
    identity = build_instrument_identity("603629.SH", {"security_name": "利通电子"})
    assert identity.status == IDENTITY_PARTIAL
    assert set(identity.missing_fields) == {"main_business", "industry"}
    assert identity.commercial_analysis_allowed is False


def test_source_symbol_conflict_is_fail_closed():
    identity = build_instrument_identity(
        "603629.SH",
        {"symbol": "600000", "security_name": "浦发银行", "main_business": "银行", "industry": "银行"},
    )
    assert identity.status == IDENTITY_CONFLICT
    assert identity.conflict_reason == "profile_symbol_mismatch"
    assert identity.commercial_analysis_allowed is False


def test_markdown_profile_extracts_labeled_fields_only():
    raw = """## Fundamentals

### Company Profile
| item | value |
|---|---|
| 股票代码 | 603629 |
| 股票简称 | 利通电子 |
| 主营业务 | 算力云服务 |
| 所属行业 | 计算机设备 |

### Financial Abstract
| 指标 | 2025 |
|---|---|
| 营收 | 33.07 |
"""
    assert extract_profile_from_fundamentals(raw) == {
        "symbol": "603629",
        "security_name": "利通电子",
        "main_business": "算力云服务",
        "industry": "计算机设备",
    }


def test_cn_astock_list_format_extracts_profile():
    raw = """## Fundamentals

### Company Profile
- **股票代码**: 603629
- **股票简称**: 利通电子
- **主营业务**: 算力云服务及精密金属结构件
- **所属行业**: 计算机设备

### Financial Abstract
| 指标 | 2025 |
|---|---|
| 营收 | 33.07 |
"""
    assert extract_profile_from_fundamentals(raw) == {
        "symbol": "603629",
        "security_name": "利通电子",
        "main_business": "算力云服务及精密金属结构件",
        "industry": "计算机设备",
    }


def test_mixed_table_and_list_format():
    raw = """## Fundamentals

### Company Profile
| item | value |
|---|---|
| 股票代码 | 603629 |
| 股票简称 | 利通电子
- **主营业务**: 算力云服务及精密金属结构件
- **所属行业**: 计算机设备

### Financial Abstract
"""
    assert extract_profile_from_fundamentals(raw) == {
        "symbol": "603629",
        "security_name": "利通电子",
        "main_business": "算力云服务及精密金属结构件",
        "industry": "计算机设备",
    }


def test_cninfo_fields_parsed_by_extract_profile():
    raw = """## Fundamentals

### Company Profile
- **代码**: 603629
- **名称**: 利通电子
- **行业**: 计算机设备

### Company Profile (巨潮资讯)
- **主营业务**: 算力云服务及精密金属结构件
- **经营范围**: 精密金属结构件、算力云服务

### Valuation Snapshot
"""
    profile = extract_profile_from_fundamentals(raw)
    assert profile["main_business"] == "算力云服务及精密金属结构件"
    assert profile["industry"] == "计算机设备"
    assert profile["security_name"] == "利通电子"


# ── cn_astock provider: real output format + cninfo fallback ──


def test_cn_astock_without_main_business_is_partial():
    """Real eastmoney push2 output has no 主营业务 → without cninfo → PARTIAL."""
    raw = """## Fundamentals for 603629.SH

### Company Profile (东财)
- **代码**: 603629
- **名称**: 利通电子
- **行业**: 计算机设备
- **总股本**: 260000000
- **流通股**: 200000000
- **总市值(元)**: 12000000000
- **流通市值(元)**: 9000000000
- **上市日期**: 20200701
- **现价**: 46.15
"""
    profile = extract_profile_from_fundamentals(raw)
    identity = build_instrument_identity("603629.SH", profile)
    assert identity.status == IDENTITY_PARTIAL
    assert "main_business" in identity.missing_fields
    assert identity.security_name == "利通电子"
    assert identity.industry == "计算机设备"
    assert identity.commercial_analysis_allowed is False


@patch("tradingagents.dataflows.providers.cn_astock_provider._cninfo_profile")
@patch("tradingagents.dataflows.providers.cn_astock_provider.CnAstockProvider._tencent_quote")
@patch("tradingagents.dataflows.providers.cn_astock_provider.CnAstockProvider._eastmoney_stock_info")
def test_cn_astock_cninfo_fallback_success(mock_em_info, mock_tencent, mock_cninfo):
    """cninfo success → 主营业务 filled → HAS_DATA."""
    mock_em_info.return_value = {
        "代码": "603629",
        "名称": "利通电子",
        "行业": "计算机设备",
        "总股本": 260000000,
        "流通股": 200000000,
        "总市值(元)": 12000000000,
        "流通市值(元)": 9000000000,
        "上市日期": "20200701",
        "现价": 46.15,
    }
    mock_tencent.return_value = {}
    mock_cninfo.return_value = {"主营业务": "算力云服务及精密金属结构件", "经营范围": "精密金属结构件、算力云服务"}

    from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
    provider = CnAstockProvider()
    result = provider.get_fundamentals("603629.SH")

    assert "### Company Profile (东财)" in result
    assert "### Company Profile (巨潮资讯)" in result
    assert "主营业务" in result
    assert "算力云服务及精密金属结构件" in result
    mock_cninfo.assert_called_once_with("603629")

    profile = extract_profile_from_fundamentals(result)
    identity = build_instrument_identity("603629.SH", profile, source="cn_astock")
    assert identity.status == IDENTITY_HAS_DATA
    assert identity.commercial_analysis_allowed is True


@patch("tradingagents.dataflows.providers.cn_astock_provider._cninfo_profile")
@patch("tradingagents.dataflows.providers.cn_astock_provider.CnAstockProvider._tencent_quote")
@patch("tradingagents.dataflows.providers.cn_astock_provider.CnAstockProvider._eastmoney_stock_info")
def test_cn_astock_cninfo_fallback_failure_is_partial(mock_em_info, mock_tencent, mock_cninfo):
    """cninfo failure → no 主营业务 → PARTIAL (not guessed)."""
    mock_em_info.return_value = {
        "代码": "603629",
        "名称": "利通电子",
        "行业": "计算机设备",
        "总股本": 260000000,
        "流通股": 200000000,
        "总市值(元)": 12000000000,
        "流通市值(元)": 9000000000,
        "上市日期": "20200701",
        "现价": 46.15,
    }
    mock_tencent.return_value = {}
    mock_cninfo.return_value = {}

    from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
    provider = CnAstockProvider()
    result = provider.get_fundamentals("603629.SH")

    assert "### Company Profile (东财)" in result
    assert "巨潮资讯" not in result
    assert "主营业务" not in result

    profile = extract_profile_from_fundamentals(result)
    identity = build_instrument_identity("603629.SH", profile, source="cn_astock")
    assert identity.status == IDENTITY_PARTIAL
    assert "main_business" in identity.missing_fields
    assert identity.commercial_analysis_allowed is False


# ── cn_akshare provider: real output format + cninfo fallback ──


def _make_real_cninfo_wide(symbol="603629", name="利通电子", industry="计算机设备", main_business="算力云服务及精密金属结构件"):
    """Create a realistic cninfo single-row wide DataFrame matching AKShare output."""
    return pd.DataFrame([{
        "公司名称": name,
        "英文名称": "",
        "曾用简称": "",
        "A股代码": symbol,
        "A股简称": name,
        "B股代码": "",
        "B股简称": "",
        "H股代码": "",
        "H股简称": "",
        "入选指数": "",
        "所属市场": "A股",
        "所属行业": industry,
        "法人代表": "",
        "注册资金": "",
        "成立日期": "",
        "上市日期": "",
        "官方网站": "",
        "电子邮箱": "",
        "联系电话": "",
        "传真": "",
        "注册地址": "",
        "办公地址": "",
        "邮政编码": "",
        "主营业务": main_business,
        "经营范围": "精密金属结构件",
        "机构简介": "",
    }])


def _make_fake_akshare(cninfo_df=None, cninfo_raises=None, info_df=None):
    """Build a mock akshare module with controllable return values.

    ``cninfo_df`` should be a **wide** DataFrame matching the real
    ``stock_profile_cninfo`` format (columns = field names, one row).
    """
    ak = MagicMock()

    def fake_stock_profile_cninfo(symbol):
        if cninfo_raises:
            raise cninfo_raises
        return cninfo_df

    ak.stock_profile_cninfo = MagicMock(side_effect=fake_stock_profile_cninfo)
    ak.stock_individual_info_em = MagicMock(return_value=info_df)
    ak.stock_financial_abstract = MagicMock(return_value=None)
    return ak


def test_cn_akshare_without_main_business_is_partial():
    """Real eastmoney info_df has no 主营业务 row → without cninfo → PARTIAL."""
    raw = """## Fundamentals for 603629.SH

### Company Profile
| item | value |
|---|---|
| 股票代码 | 603629 |
| 股票简称 | 利通电子 |
| 总市值 | 12000000000 |
| 上市时间 | 20200701 |
"""
    profile = extract_profile_from_fundamentals(raw)
    identity = build_instrument_identity("603629.SH", profile)
    assert identity.status == IDENTITY_PARTIAL
    assert "main_business" in identity.missing_fields
    assert identity.commercial_analysis_allowed is False


def test_cn_akshare_cninfo_fallback_success():
    """cninfo success (wide format) → 主营业务 appended to info_df → HAS_DATA."""
    info_df = pd.DataFrame({
        "item": ["股票代码", "股票简称", "所属行业", "总市值", "上市时间"],
        "value": ["603629", "利通电子", "计算机设备", "12000000000", "20200701"],
    })
    cninfo_df = _make_real_cninfo_wide()
    ak = _make_fake_akshare(cninfo_df=cninfo_df, info_df=info_df)

    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
    provider = CnAkshareProvider()

    with patch.object(provider, "_ak", return_value=ak):
        result = provider.get_fundamentals("603629.SH")

    assert "### Company Profile" in result
    assert "主营业务" in result
    assert "算力云服务及精密金属结构件" in result
    ak.stock_profile_cninfo.assert_called_once_with(symbol="603629")

    profile = extract_profile_from_fundamentals(result)
    identity = build_instrument_identity("603629.SH", profile, source="cn_akshare")
    assert identity.status == IDENTITY_HAS_DATA
    assert identity.commercial_analysis_allowed is True


def test_cn_akshare_cninfo_fallback_failure_is_partial():
    """cninfo failure → no 主营业务 → PARTIAL (not guessed)."""
    info_df = pd.DataFrame({
        "item": ["股票代码", "股票简称", "总市值", "上市时间"],
        "value": ["603629", "利通电子", "12000000000", "20200701"],
    })
    ak = _make_fake_akshare(
        cninfo_raises=RuntimeError("cninfo API timeout"),
        info_df=info_df,
    )

    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
    provider = CnAkshareProvider()

    with patch.object(provider, "_ak", return_value=ak):
        result = provider.get_fundamentals("603629.SH")

    assert "### Company Profile" in result
    assert "主营业务" not in result

    profile = extract_profile_from_fundamentals(result)
    identity = build_instrument_identity("603629.SH", profile, source="cn_akshare")
    assert identity.status == IDENTITY_PARTIAL
    assert "main_business" in identity.missing_fields
    assert identity.commercial_analysis_allowed is False


def test_cn_akshare_empty_business_value_still_uses_cninfo():
    """An empty placeholder is missing data, not evidence that fallback is unnecessary."""
    info_df = pd.DataFrame({
        "item": ["股票代码", "股票简称", "所属行业", "主营业务"],
        "value": ["603629", "利通电子", "计算机设备", ""],
    })
    ak = _make_fake_akshare(
        cninfo_df=_make_real_cninfo_wide(),
        info_df=info_df,
    )

    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

    provider = CnAkshareProvider()
    with patch.object(provider, "_ak", return_value=ak):
        result = provider.get_fundamentals("603629.SH")

    ak.stock_profile_cninfo.assert_called_once_with(symbol="603629")
    identity = build_instrument_identity(
        "603629.SH",
        extract_profile_from_fundamentals(result),
        source="cn_akshare",
    )
    assert identity.status == IDENTITY_HAS_DATA
    assert identity.main_business == "算力云服务及精密金属结构件"


def test_cn_akshare_primary_all_fail_cninfo_restores_identity():
    """东财+雪球全失败、巨潮成功 → CNInfo 重建完整身份 → HAS_DATA."""
    cninfo_df = _make_real_cninfo_wide()
    ak = _make_fake_akshare(cninfo_df=cninfo_df, info_df=None)
    # info_df=None means stock_individual_info_em returns None
    ak.stock_individual_info_em = MagicMock(return_value=None)
    ak.stock_individual_basic_info_xq = MagicMock(side_effect=RuntimeError("xq timeout"))

    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
    provider = CnAkshareProvider()

    with patch.object(provider, "_ak", return_value=ak):
        result = provider.get_fundamentals("603629.SH")

    assert "### Company Profile" in result
    assert "主营业务" in result
    assert "算力云服务及精密金属结构件" in result
    assert "利通电子" in result
    assert "计算机设备" in result

    profile = extract_profile_from_fundamentals(result)
    identity = build_instrument_identity("603629.SH", profile, source="cn_akshare")
    assert identity.status == IDENTITY_HAS_DATA
    assert identity.security_name == "利通电子"
    assert identity.commercial_analysis_allowed is True


def test_cn_akshare_mismatched_cninfo_cannot_supply_business():
    """主源 603629 + 巨潮 600000 must fail closed before field merging."""
    info_df = pd.DataFrame({
        "item": ["股票代码", "股票简称", "所属行业"],
        "value": ["603629", "利通电子", "电子设备"],
    })
    cninfo_df = _make_real_cninfo_wide(
        symbol="600000",
        name="浦发银行",
        industry="银行",
        main_business="银行业务",
    )
    ak = _make_fake_akshare(cninfo_df=cninfo_df, info_df=info_df)

    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
    provider = CnAkshareProvider()

    with patch.object(provider, "_ak", return_value=ak):
        result = provider.get_fundamentals("603629.SH")

    assert "### Company Profile (巨潮资讯)" in result
    assert "requested=603629, returned=600000" in result
    assert "浦发银行" not in result
    assert "银行业务" not in result
    profile = extract_profile_from_fundamentals(result)
    identity = build_instrument_identity("603629.SH", profile, source="cn_akshare")
    assert identity.status == IDENTITY_CONFLICT
    assert identity.conflict_reason == "cross_source_code_mismatch"
    assert identity.main_business is None
    assert identity.commercial_analysis_allowed is False


@patch("tradingagents.dataflows.providers.cn_astock_provider._cninfo_profile")
@patch("tradingagents.dataflows.providers.cn_astock_provider.CnAstockProvider._tencent_quote")
@patch("tradingagents.dataflows.providers.cn_astock_provider.CnAstockProvider._eastmoney_stock_info")
def test_cn_astock_mismatched_cninfo_is_redacted_before_llm(
    mock_em_info, mock_tencent, mock_cninfo
):
    """Direct provider must not expose a mismatched issuer's profile fields."""
    mock_em_info.return_value = {
        "代码": "603629",
        "名称": "利通电子",
        "行业": "电子设备",
    }
    mock_tencent.return_value = {}
    mock_cninfo.return_value = {
        "A股代码": "600000",
        "A股简称": "浦发银行",
        "所属行业": "银行",
        "主营业务": "银行业务",
        "经营范围": "商业银行业务",
    }

    from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider

    result = CnAstockProvider().get_fundamentals("603629.SH")

    assert "requested=603629, returned=600000" in result
    assert "浦发银行" not in result
    assert "银行业务" not in result
    profile = extract_profile_from_fundamentals(result)
    identity = build_instrument_identity("603629.SH", profile, source="cn_astock")
    assert identity.status == IDENTITY_CONFLICT
    assert identity.conflict_reason == "cross_source_code_mismatch"
    assert identity.commercial_analysis_allowed is False


# ── FUND-001A Round 4: Cross-source conflict detection ──


def test_cross_source_code_conflict_is_identity_conflict():
    """东财 603629 + 巨潮 600000 → cross_source_conflict → IDENTITY_CONFLICT."""
    raw = """## Fundamentals for 603629.SH

### Company Profile (东财)
- **代码**: 603629
- **名称**: 利通电子
- **行业**: 计算机设备

### Company Profile (巨潮资讯)
- **代码**: 600000
- **名称**: 浦发银行
- **行业**: 银行
- **主营业务**: 银行业务

### Valuation Snapshot
"""
    profile = extract_profile_from_fundamentals(raw)
    assert profile.get("_cross_source_conflict") == "True"
    assert profile.get("symbol") == "603629"

    identity = build_instrument_identity("603629.SH", profile, source="cn_astock")
    assert identity.status == IDENTITY_CONFLICT
    assert identity.conflict_reason == "cross_source_code_mismatch"
    assert identity.commercial_analysis_allowed is False


def test_placeholder_code_is_ignored_when_fallback_has_valid_code():
    raw = """## Fundamentals for 603629.SH

### Company Profile (东财)
- **代码**: —
- **名称**: 利通电子
- **行业**: 计算机设备

### Company Profile (巨潮资讯)
- **代码**: 603629
- **主营业务**: 算力云服务及精密金属结构件
"""
    profile = extract_profile_from_fundamentals(raw)
    identity = build_instrument_identity("603629.SH", profile)
    assert identity.status == IDENTITY_HAS_DATA
    assert identity.conflict_reason is None
    assert identity.source_symbol == "603629"


def test_main_business_beats_business_scope_regardless_of_row_order():
    raw = """### Company Profile (巨潮资讯)
- **股票代码**: 603629
- **经营范围**: 宽泛经营范围
- **主营业务**: 精确主营业务
- **股票简称**: 利通电子
- **所属行业**: 计算机设备
"""
    profile = extract_profile_from_fundamentals(raw)
    assert profile["main_business"] == "精确主营业务"


def test_later_precise_business_overrides_earlier_scope():
    raw = """### Company Profile (东财)
- **股票代码**: 603629
- **股票简称**: 利通电子
- **所属行业**: 计算机设备
- **经营范围**: 宽泛经营范围

### Company Profile (巨潮资讯)
- **股票代码**: 603629
- **主营业务**: 精确主营业务
"""
    assert extract_profile_from_fundamentals(raw)["main_business"] == "精确主营业务"


def test_stock_short_name_overrides_company_full_name_across_sections():
    raw = """### Company Profile (主源)
- **股票代码**: 603629
- **公司名称**: 江苏利通电子股份有限公司

### Company Profile (巨潮资讯)
- **股票代码**: 603629
- **股票简称**: 利通电子
- **所属行业**: 金属制品业
- **主营业务**: 精密金属结构件
"""
    assert extract_profile_from_fundamentals(raw)["security_name"] == "利通电子"


def test_single_source_cninfo_only_code_conflict():
    """请求 603629 + 仅巨潮返回 600000 → profile_symbol_mismatch → CONFLICT."""
    raw = """## Fundamentals for 603629.SH

### Company Profile (巨潮资讯)
- **代码**: 600000
- **名称**: 浦发银行
- **行业**: 银行
- **主营业务**: 银行业务

### Valuation Snapshot
"""
    profile = extract_profile_from_fundamentals(raw)
    assert profile.get("symbol") == "600000"
    assert profile.get("_cross_source_conflict") is None

    identity = build_instrument_identity("603629.SH", profile, source="cn_astock")
    assert identity.status == IDENTITY_CONFLICT
    assert identity.conflict_reason == "profile_symbol_mismatch"
    assert identity.commercial_analysis_allowed is False


def test_same_code_different_name_alias_not_conflict():
    """同代码 603629 + 简称 vs 全称 → 不冲突 → HAS_DATA."""
    raw = """## Fundamentals for 603629.SH

### Company Profile (东财)
- **代码**: 603629
- **名称**: 利通电子
- **行业**: 计算机设备

### Company Profile (巨潮资讯)
- **代码**: 603629
- **名称**: 江苏利通电子股份有限公司
- **行业**: 计算机设备
- **主营业务**: 算力云服务及精密金属结构件
"""
    profile = extract_profile_from_fundamentals(raw)
    assert "_cross_source_conflict" not in profile
    assert profile.get("symbol") == "603629"
    assert profile.get("security_name") == "利通电子"
    assert profile.get("main_business") == "算力云服务及精密金属结构件"

    identity = build_instrument_identity("603629.SH", profile, source="cn_astock")
    assert identity.status == IDENTITY_HAS_DATA
    assert identity.security_name == "利通电子"
    assert identity.main_business == "算力云服务及精密金属结构件"
    assert identity.commercial_analysis_allowed is True


def test_same_code_normal_supplement():
    """同代码 603629 + 正常补主营 → 不冲突 → HAS_DATA."""
    raw = """## Fundamentals for 603629.SH

### Company Profile (东财)
- **代码**: 603629
- **名称**: 利通电子
- **行业**: 计算机设备

### Company Profile (巨潮资讯)
- **代码**: 603629
- **主营业务**: 算力云服务及精密金属结构件
- **经营范围**: 精密金属结构件、算力云服务
"""
    profile = extract_profile_from_fundamentals(raw)
    assert "_cross_source_conflict" not in profile
    assert profile.get("symbol") == "603629"
    assert profile.get("industry") == "计算机设备"
    assert profile.get("main_business") == "算力云服务及精密金属结构件"

    identity = build_instrument_identity("603629.SH", profile, source="cn_astock")
    assert identity.status == IDENTITY_HAS_DATA
    assert identity.commercial_analysis_allowed is True
