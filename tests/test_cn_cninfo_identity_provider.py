from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.dataflows.providers.cn_cninfo_identity_provider import (
    CninfoIdentityProvider,
)


def test_cninfo_identity_provider_returns_profile_without_financial_facts(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "A股代码": "600487",
                "A股简称": "亨通光电",
                "所属行业": "通信设备",
                "主营业务": "光纤光缆及海洋通信",
            }
        ]
    )
    installed_timeouts = []
    monkeypatch.setattr(
        "tradingagents.dataflows.providers.cn_cninfo_identity_provider.install_default_network_timeout",
        installed_timeouts.append,
    )
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_profile_cninfo=lambda symbol: frame),
    )
    provider = CninfoIdentityProvider()

    profile = provider.get_fundamentals("600487.SH")

    assert "Company Profile (巨潮资讯)" in profile
    assert "亨通光电" in profile
    assert provider.identity_source_id == "cninfo"
    assert installed_timeouts == [20.0]
    assert "No income statement data" in provider.get_income_statement("600487.SH")


def test_cninfo_identity_provider_fails_closed_on_symbol_mismatch(monkeypatch):
    frame = pd.DataFrame([{"A股代码": "000001", "A股简称": "错误公司"}])
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_profile_cninfo=lambda symbol: frame),
    )

    profile = CninfoIdentityProvider().get_fundamentals("600487.SH")

    assert "身份冲突" in profile
    assert "错误公司" not in profile


def test_cninfo_identity_provider_rejects_empty_profile(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_profile_cninfo=lambda symbol: pd.DataFrame()),
    )
    with pytest.raises(RuntimeError, match="no data"):
        CninfoIdentityProvider().get_fundamentals("600487.SH")


def test_cninfo_identity_provider_rejects_profile_without_returned_code(monkeypatch):
    frame = pd.DataFrame(
        [{"A股简称": "亨通光电", "主营业务": "光纤光缆及海洋通信"}]
    )
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_profile_cninfo=lambda symbol: frame),
    )

    with pytest.raises(RuntimeError, match="lacks returned A-share code"):
        CninfoIdentityProvider().get_fundamentals("600487.SH")


def test_cninfo_identity_provider_rejects_profile_without_security_name(monkeypatch):
    frame = pd.DataFrame(
        [{"A股代码": "600487", "所属行业": "通信设备", "主营业务": "光纤"}]
    )
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_profile_cninfo=lambda symbol: frame),
    )

    with pytest.raises(RuntimeError, match="lacks security name"):
        CninfoIdentityProvider().get_fundamentals("600487.SH")


@pytest.mark.parametrize("missing_name", ["—", "null", pd.NA, float("nan")])
def test_cninfo_identity_provider_rejects_missing_name_sentinels(
    monkeypatch, missing_name
):
    frame = pd.DataFrame([{"A股代码": "600487", "A股简称": missing_name}])
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_profile_cninfo=lambda symbol: frame),
    )

    with pytest.raises(RuntimeError, match="lacks security name"):
        CninfoIdentityProvider().get_fundamentals("600487.SH")
