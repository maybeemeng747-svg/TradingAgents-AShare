from tradingagents.agents.utils.data_consistency import (
    append_financial_consistency_warnings,
    find_financial_direction_conflicts,
)


def test_find_financial_direction_conflicts_for_core_metrics():
    text = (
        "扣非净利润同比增长约64.8%，经营现金流同比下降约35.43%。\n"
        "后文又称扣非净利润同比下降18.65%，经营现金流同比增长18.65%。"
    )

    issues = find_financial_direction_conflicts(text)

    assert {issue.metric for issue in issues} == {"扣非净利润", "经营现金流"}


def test_append_financial_consistency_warnings_adds_review_block():
    text = (
        "扣非净利润同比增长约64.8%。\n"
        "基本面部分写扣非净利润同比下降18.65%。"
    )

    checked = append_financial_consistency_warnings(text)

    assert "### 数据一致性警告" in checked
    assert "扣非净利润" in checked
    assert "回查原始财报数据" in checked
