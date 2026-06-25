# [H-015] mandate_daily_report

from tradingagents.tradeflow.mandate_daily_report import (
    build_mandate_daily_report,
    load_latest_mandate_daily_report,
    save_mandate_daily_report,
)


def _fixture_heatmap():
    return {
        "as_of": "2026-06-24",
        "status": "ok",
        "topics": [
            {
                "topic": "低空经济",
                "topic_status": "FERMENTING",
                "topic_status_label": "发酵",
                "heat_trend": "RISING",
                "heat_trend_label": "升温",
                "state_change_label": "升温发酵",
                "state_change_positive": True,
                "is_left_side": True,
                "is_confirmed": False,
                "latest_date": "2026-06-24",
                "peak_heat": 82,
                "windows": {"7": {"evidence_count": 5, "candidate_count": 2}},
                "evidence_summary": "低空经济政策支持",
                "counter_evidence_gaps": ["订单兑现仍需跟踪"],
                "overheat_flags": [],
                "candidates": [
                    {
                        "symbol": "000001.SZ",
                        "name": "示例科技",
                        "company_role": "核心设备",
                        "candidate_type": "POLICY_AMBUSH",
                        "tier": "main",
                        "mandate_score": 88,
                        "latest_date": "2026-06-24",
                    }
                ],
            },
            {
                "topic": "机器人",
                "topic_status": "RECEDING",
                "topic_status_label": "退潮",
                "heat_trend": "COOLING",
                "heat_trend_label": "降温",
                "state_change_label": "退潮",
                "state_change_positive": False,
                "is_left_side": False,
                "is_confirmed": False,
                "latest_date": "2026-06-23",
                "peak_heat": 43,
                "heat_curve": [{"date": "2026-06-23", "candidate_count": 1, "heat": 43}],
                "windows": {"7": {"evidence_count": 1, "candidate_count": 0}},
                "counter_evidence_gaps": ["资金承接不足"],
                "overheat_flags": ["短期过热"],
                "candidates": [],
            },
        ],
    }


def test_build_mandate_daily_report_contains_traceable_reasons():
    report = build_mandate_daily_report(_fixture_heatmap())

    assert report.as_of == "2026-06-24"
    assert report.rising_topics[0]["topic"] == "低空经济"
    assert report.cooling_topics[0]["topic"] == "机器人"
    assert report.main_candidates[0].symbol == "000001.SZ"
    assert "主题升温" in report.main_candidates[0].entry_reason
    assert report.exit_reasons[0]["topic"] == "机器人"
    assert report.evidence_gaps[0]["topic"] == "低空经济"


def test_rendered_mandate_daily_report_has_no_strong_trade_words():
    report = build_mandate_daily_report(_fixture_heatmap())
    forbidden = ["立即清仓", "重仓买入", "满仓", "梭哈"]

    for word in forbidden:
        assert word not in report.markdown
    assert "不构成交易建议" in report.markdown


def test_save_and_load_latest_mandate_daily_report(tmp_path):
    report = build_mandate_daily_report(_fixture_heatmap())
    md_path, json_path = save_mandate_daily_report(report, output_dir=str(tmp_path))

    loaded = load_latest_mandate_daily_report(str(tmp_path))

    assert md_path.endswith("mandate-2026-06-24.md")
    assert json_path.endswith("mandate-2026-06-24.json")
    assert loaded is not None
    assert loaded["as_of"] == "2026-06-24"
    assert loaded["source"] == "file"
