from tradingagents.dataflows.instrument_identity import (
    IDENTITY_CONFLICT,
    IDENTITY_HAS_DATA,
    IDENTITY_MISSING,
    IDENTITY_PARTIAL,
    build_instrument_identity,
    extract_profile_from_fundamentals,
    render_identity_context,
)


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
