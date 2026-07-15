"""[FUND-006] Deterministic replay of historical fundamental failure modes."""

import pytest

from tradingagents.agents.utils.fundamental_integrity import evaluate_fundamental_integrity
from tradingagents.dataflows.financial_periods import (
    derive_single_quarters,
    normalize_financial_markdown,
)
from tradingagents.dataflows.instrument_identity import build_instrument_identity


def _profile(symbol, name, business, industry):
    return build_instrument_identity(
        symbol,
        {"symbol": symbol[:6], "security_name": name, "main_business": business, "industry": industry},
    ).to_dict()


def _facts():
    return [{"metric": "revenue", "report_date": "2026-03-31", "value": 10.0, "status": "HAS_DATA"}]


@pytest.mark.parametrize(
    ("symbol", "name", "business", "industry"),
    [
        ("603629.SH", "利通电子", "算力云服务及精密金属结构件", "计算机设备"),
        ("600519.SH", "贵州茅台", "白酒生产销售", "食品饮料"),
        ("600000.SH", "浦发银行", "商业银行服务", "银行"),
        ("159516.SZ", "半导体设备ETF", "指数基金", "基金"),
        ("300750.SZ", "宁德时代", "动力电池", "电力设备"),
    ],
)
def test_verified_cross_industry_identity_is_not_reclassified_from_financial_ratios(
    symbol, name, business, industry
):
    identity = _profile(symbol, name, business, industry)
    assert identity["commercial_analysis_allowed"] is True
    assert identity["industry"] == industry


def test_603629_historical_chemical_guess_is_rejected_without_profile():
    integrity = evaluate_fundamental_integrity(
        identity={},
        period_facts=_facts(),
        explanation_context={"status": "unexplained"},
        report_text="公司主营化工品生产销售，Q1是化工淡季且原材料下降。",
    )
    codes = {item["code"] for item in integrity["blockers"]}
    assert "IDENTITY_UNVERIFIED" in codes
    assert "CAUSE_UNSUPPORTED" in codes


def test_603629_fy_is_not_replayed_as_q4():
    raw = """| 报告日 | 营业总收入 |
|---|---:|
| 2025-03-31 | 7.04342 |
| 2025-06-30 | 15.16342 |
| 2025-09-30 | 24.62342 |
| 2025-12-31 | 33.07400 |
"""
    facts = normalize_financial_markdown(raw, statement_type="income_statement", source="fixture")
    q4 = next(item for item in derive_single_quarters(facts) if item.report_date == "2025-12-31")
    assert q4.value == pytest.approx(8.45058)
    assert q4.formula == "FY_YTD-Q3_YTD"


def test_true_financial_facts_are_retained_when_narrative_is_rejected():
    facts = _facts()
    integrity = evaluate_fundamental_integrity(
        identity={}, period_facts=facts, explanation_context={}, report_text="化工行业原料下降"
    )
    assert integrity["status"] == "NEEDS_REVIEW"
    assert facts[0]["value"] == 10.0
