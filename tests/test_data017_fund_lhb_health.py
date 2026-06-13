"""[DATA-017] Tests for fund flow / LHB data source health inspection.

Covers:
1. FundLhbHealthStatus constants (5 states)
2. inspect_fund_flow_health — HAS_DATA / FAILED / STALE / UNIT_UNVERIFIED / NORMAL_NO_DATA
3. inspect_sector_fund_flow_health — sector/board fund flow
4. inspect_lhb_health — HAS_DATA / NORMAL_NO_DATA / FAILED / NOT_QUERIED distinction
5. run_fund_lhb_health_check — combined report + summary
6. render_fund_lhb_health_report — markdown rendering
7. Sample fixtures: HAS_FUND_DATA, NO_LHB_NORMAL, INTERFACE_FAILED
8. Integration: readiness gating, EvidenceContract compatibility
"""

import pytest
from datetime import datetime, timedelta

from tradingagents.dataflows.fund_lhb_health import (
    FundLhbHealthStatus,
    FundFlowHealth,
    SectorFundFlowHealth,
    LhbHealth,
    FundLhbHealthReport,
    inspect_fund_flow_health,
    inspect_sector_fund_flow_health,
    inspect_lhb_health,
    run_fund_lhb_health_check,
    render_fund_lhb_health_report,
    SAMPLE_HAS_FUND_DATA,
    SAMPLE_NO_LHB_NORMAL,
    SAMPLE_INTERFACE_FAILED,
    ALL_SAMPLES,
)


# ── Status Constants ──────────────────────────────────────────────────

class TestFundLhbHealthStatus:
    def test_all_five_statuses_defined(self):
        assert FundLhbHealthStatus.HAS_DATA == "HAS_DATA"
        assert FundLhbHealthStatus.NORMAL_NO_DATA == "NORMAL_NO_DATA"
        assert FundLhbHealthStatus.FAILED == "FAILED"
        assert FundLhbHealthStatus.STALE == "STALE"
        assert FundLhbHealthStatus.UNIT_UNVERIFIED == "UNIT_UNVERIFIED"

    def test_all_list_contains_five(self):
        assert len(FundLhbHealthStatus.ALL) == 5
        for s in FundLhbHealthStatus.ALL:
            assert isinstance(s, str)


# ── inspect_fund_flow_health ──────────────────────────────────────────

class TestInspectFundFlowHealth:

    def test_has_data_with_verified_unit(self):
        raw = {
            "fund_flow_individual": {
                "raw": "600519.SH 近20日主力资金：\n日期 净流入(万元)\n2026-06-10 12345",
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_akshare",
                "endpoint": "stock_individual_fund_flow",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
                "fetched_at": datetime.now().isoformat(),
                "record_count": 20,
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.HAS_DATA
        assert h.vendor == "cn_akshare"
        assert h.unit == "万元"
        assert h.unit_verified is True
        assert h.record_count == 20
        assert "正常" in h.diagnosis

    def test_has_data_with_fallback(self):
        raw = {
            "fund_flow_individual": {
                "raw": "603629.SH 近20日主力资金...",
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_astock",
                "endpoint": "push2his.eastmoney.com/fflow",
                "fallback_from": "cn_akshare",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
                "record_count": 20,
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.HAS_DATA
        assert h.is_fallback is True
        assert h.fallback_from == "cn_akshare"
        assert "fallback from cn_akshare" in h.diagnosis

    def test_unit_unverified(self):
        raw = {
            "fund_flow_individual": {
                "raw": "数据存在但单位未知",
                "status": "HAS_DATA",
                "unit": None,
                "unit_verified": False,
                "vendor": "cn_akshare",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
                "record_count": 10,
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.UNIT_UNVERIFIED
        assert h.unit_verified is False
        assert "单位未校验" in h.diagnosis

    def test_failed_status(self):
        raw = {
            "fund_flow_individual": {
                "raw": "个股资金流向数据获取失败：ProxyError",
                "status": "FAILED",
                "unit": None,
                "unit_verified": False,
                "vendor": "cn_akshare",
                "error": "ProxyError: Cannot connect",
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.FAILED
        assert "ProxyError" in h.error
        assert "失败" in h.diagnosis

    def test_stale_data(self):
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        raw = {
            "fund_flow_individual": {
                "raw": "数据存在但太旧",
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_akshare",
                "as_of": old_date,
                "record_count": 5,
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.STALE
        assert "过期" in h.diagnosis

    def test_normal_no_data(self):
        raw = {
            "fund_flow_individual": {
                "raw": "",
                "status": "NORMAL_NO_DATA",
                "unit": None,
                "unit_verified": False,
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert "无数据" in h.diagnosis

    def test_not_queried_treated_as_failed(self):
        raw = {
            "fund_flow_individual": {
                "raw": None,
                "status": "NOT_QUERIED",
                "unit_verified": False,
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.FAILED
        assert "未查询" in h.diagnosis

    def test_missing_entry(self):
        h = inspect_fund_flow_health({})
        assert h.status == FundLhbHealthStatus.FAILED
        assert "未在 raw_evidence 中找到" in h.diagnosis

    def test_legacy_string_format_has_data(self):
        raw = {
            "fund_flow_individual": "600519.SH 近20日主力资金净流向：\n日期 净流入\n2026-05-26 12345",
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.HAS_DATA
        assert h.unit_verified is True

    def test_legacy_string_format_failed(self):
        raw = {
            "fund_flow_individual": "个股资金流向数据获取失败：ProxyError",
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.FAILED


# ── inspect_sector_fund_flow_health ───────────────────────────────────

class TestInspectSectorFundFlowHealth:

    def test_has_data(self):
        raw = {
            "fund_flow_board": {
                "raw": "板块资金流数据...",
                "status": "HAS_DATA",
                "vendor": "cn_akshare",
                "record_count": 5,
            }
        }
        h = inspect_sector_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.HAS_DATA
        assert h.vendor == "cn_akshare"

    def test_not_present_is_normal(self):
        h = inspect_sector_fund_flow_health({})
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA

    def test_failed(self):
        raw = {
            "fund_flow_board": {
                "raw": "板块资金流获取失败",
                "status": "FAILED",
                "error": "ConnectionError",
            }
        }
        h = inspect_sector_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.FAILED


# ── inspect_lhb_health ────────────────────────────────────────────────

class TestInspectLhbHealth:

    def test_has_data(self):
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_HAS_DATA: 龙虎榜明细（2026-06-10）",
                "status": "HAS_DATA",
                "vendor": "cn_akshare",
                "endpoint": "stock_lhb_detail_em",
                "query_mode": "forced",
                "force_reason": "anomaly_condition",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
                "record_count": 3,
            }
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.HAS_DATA
        assert h.query_mode == "forced"
        assert h.force_reason == "anomaly_condition"

    def test_normal_no_data_non_anomaly_day(self):
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NORMAL_NO_DATA: 在 2026-06-10 无龙虎榜数据（非异动日属正常）",
                "status": "NORMAL_NO_DATA",
                "vendor": "cn_akshare",
                "query_mode": "forced",
                "force_reason": "anomaly_condition",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
                "record_count": 0,
            }
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert "非异动日正常" in h.diagnosis
        assert "不应降低完整度" in h.diagnosis

    def test_not_queried_is_normal(self):
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发（force=False）",
                "status": "NOT_QUERIED",
                "query_mode": "not_queried",
            }
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert "未触发查询" in h.diagnosis

    def test_failed(self):
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_FAILED: 龙虎榜数据获取失败",
                "status": "FAILED",
                "vendor": "cn_akshare",
                "query_mode": "forced",
                "error": "ConnectionError",
            }
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.FAILED
        assert "查询失败" in h.diagnosis

    def test_stale_lhb(self):
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_HAS_DATA: 龙虎榜明细",
                "status": "HAS_DATA",
                "vendor": "cn_akshare",
                "as_of": old_date,
                "record_count": 2,
            }
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.STALE

    def test_missing_entry_is_normal(self):
        h = inspect_lhb_health({})
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA

    def test_legacy_format_not_queried(self):
        raw = {
            "lhb": "[G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发",
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA

    def test_legacy_format_normal_no_data(self):
        raw = {
            "lhb": "[G-007] LHB_NORMAL_NO_DATA: 在 2026-06-10 无龙虎榜数据",
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA

    def test_legacy_format_failed(self):
        raw = {
            "lhb": "[G-007] LHB_FAILED: 龙虎榜数据获取失败",
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.FAILED

    def test_legacy_format_has_data(self):
        raw = {
            "lhb": "[G-007] LHB_HAS_DATA: 龙虎榜明细（2026-06-10）",
        }
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.HAS_DATA


# ── run_fund_lhb_health_check ─────────────────────────────────────────

class TestRunFundLhbHealthCheck:

    def test_all_healthy(self):
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_akshare",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
                "record_count": 20,
            },
            "fund_flow_board": {
                "status": "HAS_DATA",
                "vendor": "cn_akshare",
            },
            "lhb": {
                "status": "NORMAL_NO_DATA",
                "vendor": "cn_akshare",
                "query_mode": "forced",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            },
        }
        report = run_fund_lhb_health_check(raw, symbol="600519.SH")
        assert report.symbol == "600519.SH"
        assert report.fund_flow_individual.status == FundLhbHealthStatus.HAS_DATA
        assert report.lhb.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert report.summary["all_critical_ok"] is True
        assert report.summary["has_any_failed"] is False

    def test_fund_flow_failed(self):
        raw = {
            "fund_flow_individual": {
                "status": "FAILED",
                "error": "ProxyError",
                "unit_verified": False,
            },
            "lhb": {
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        report = run_fund_lhb_health_check(raw)
        assert report.fund_flow_individual.status == FundLhbHealthStatus.FAILED
        assert report.summary["has_any_failed"] is True
        assert report.summary["all_critical_ok"] is False

    def test_unit_unverified_summary(self):
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": None,
                "unit_verified": False,
                "vendor": "cn_akshare",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            },
            "lhb": {
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        report = run_fund_lhb_health_check(raw)
        assert report.fund_flow_individual.status == FundLhbHealthStatus.UNIT_UNVERIFIED
        assert report.summary["has_unit_unverified"] is True
        assert report.summary["all_critical_ok"] is False

    def test_lhb_failed_not_normal(self):
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            },
            "lhb": {
                "status": "FAILED",
                "query_mode": "forced",
                "error": "ConnectionError",
            },
        }
        report = run_fund_lhb_health_check(raw)
        assert report.lhb.status == FundLhbHealthStatus.FAILED
        assert report.summary["has_any_failed"] is True
        assert report.summary["all_critical_ok"] is False

    def test_lhb_normal_no_data_does_not_fail(self):
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            },
            "lhb": {
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            },
        }
        report = run_fund_lhb_health_check(raw)
        assert report.lhb.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert report.summary["has_any_failed"] is False
        assert report.summary["all_critical_ok"] is True

    def test_empty_raw_evidence(self):
        report = run_fund_lhb_health_check({})
        assert report.fund_flow_individual.status == FundLhbHealthStatus.FAILED
        assert report.lhb.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert report.summary["has_any_failed"] is True

    def test_to_dict(self):
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_akshare",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            },
            "lhb": {
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        report = run_fund_lhb_health_check(raw)
        d = report.to_dict()
        assert "fund_flow_individual" in d
        assert "fund_flow_sector" in d
        assert "lhb" in d
        assert "summary" in d
        assert d["fund_flow_individual"]["status"] == FundLhbHealthStatus.HAS_DATA


# ── render_fund_lhb_health_report ─────────────────────────────────────

class TestRenderFundLhbHealthReport:

    def test_renders_markdown(self):
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_akshare",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            },
            "lhb": {
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            },
        }
        report = run_fund_lhb_health_check(raw, symbol="600519.SH")
        md = render_fund_lhb_health_report(report)
        assert "# Fund Flow & LHB Health Report" in md
        assert "600519.SH" in md
        assert "HAS_DATA" in md
        assert "NORMAL_NO_DATA" in md
        assert "[DATA-017]" in md

    def test_includes_diagnosis(self):
        raw = {
            "fund_flow_individual": {
                "status": "FAILED",
                "error": "ProxyError",
            },
        }
        report = run_fund_lhb_health_check(raw)
        md = render_fund_lhb_health_report(report)
        assert "ProxyError" in md
        assert "失败" in md

    def test_includes_fallback_info(self):
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_astock",
                "fallback_from": "cn_akshare",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            },
            "lhb": {"status": "NORMAL_NO_DATA", "query_mode": "forced"},
        }
        report = run_fund_lhb_health_check(raw)
        md = render_fund_lhb_health_report(report)
        assert "cn_akshare" in md
        assert "fallback" in md.lower() or "Fallback" in md


# ── Sample Fixtures ───────────────────────────────────────────────────

class TestSampleFixtures:

    def test_sample_has_fund_data(self):
        report = run_fund_lhb_health_check(SAMPLE_HAS_FUND_DATA, symbol="600519.SH")
        assert report.fund_flow_individual.status == FundLhbHealthStatus.HAS_DATA
        assert report.fund_flow_individual.unit_verified is True
        assert report.lhb.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert report.summary["all_critical_ok"] is True

    def test_sample_no_lhb_normal(self):
        report = run_fund_lhb_health_check(SAMPLE_NO_LHB_NORMAL, symbol="000001.SZ")
        assert report.fund_flow_individual.status == FundLhbHealthStatus.HAS_DATA
        assert report.fund_flow_individual.is_fallback is True
        assert report.lhb.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert report.summary["has_any_failed"] is False
        assert report.summary["all_critical_ok"] is True

    def test_sample_interface_failed(self):
        report = run_fund_lhb_health_check(SAMPLE_INTERFACE_FAILED, symbol="603629.SH")
        assert report.fund_flow_individual.status == FundLhbHealthStatus.FAILED
        assert report.lhb.status == FundLhbHealthStatus.FAILED
        assert report.summary["has_any_failed"] is True
        assert report.summary["all_critical_ok"] is False

    def test_all_samples_registered(self):
        assert "HAS_FUND_DATA" in ALL_SAMPLES
        assert "NO_LHB_NORMAL" in ALL_SAMPLES
        assert "INTERFACE_FAILED" in ALL_SAMPLES
        assert len(ALL_SAMPLES) == 3

    def test_each_sample_has_three_keys(self):
        for name, sample in ALL_SAMPLES.items():
            assert "fund_flow_individual" in sample, f"{name} missing fund_flow_individual"
            assert "fund_flow_board" in sample, f"{name} missing fund_flow_board"
            assert "lhb" in sample, f"{name} missing lhb"


# ── Critical Behavioral Guarantees ────────────────────────────────────

class TestCriticalBehavior:

    def test_failed_fund_flow_not_has_data(self):
        """主力资金失败时不能标记为 HAS_DATA"""
        raw = {
            "fund_flow_individual": {
                "raw": "个股资金流向数据获取失败：ProxyError",
                "status": "FAILED",
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status != FundLhbHealthStatus.HAS_DATA
        assert h.status == FundLhbHealthStatus.FAILED

    def test_lhb_no_trigger_not_failed(self):
        """龙虎榜无触发不得降低完整度为失败"""
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NOT_QUERIED: 龙虎榜查询未触发",
                "status": "NOT_QUERIED",
                "query_mode": "not_queried",
            }
        }
        h = inspect_lhb_health(raw)
        assert h.status != FundLhbHealthStatus.FAILED
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA

    def test_lhb_normal_no_data_not_failed(self):
        """龙虎榜查询成功但无记录不等于失败"""
        raw = {
            "lhb": {
                "raw": "[G-007] LHB_NORMAL_NO_DATA: 非异动日",
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            }
        }
        h = inspect_lhb_health(raw)
        assert h.status != FundLhbHealthStatus.FAILED
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA

    def test_unit_unverified_not_has_data(self):
        """单位未校验不能作为 HAS_DATA 强证据"""
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": None,
                "unit_verified": False,
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.UNIT_UNVERIFIED
        assert h.status != FundLhbHealthStatus.HAS_DATA

    def test_fallback_vendor_recorded(self):
        """fallback 成功时必须记录实际 vendor"""
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_astock",
                "fallback_from": "cn_akshare",
                "as_of": datetime.now().strftime("%Y-%m-%d"),
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.vendor == "cn_astock"
        assert h.fallback_from == "cn_akshare"
        assert h.is_fallback is True

    def test_603629_like_failure_explained(self):
        """603629 等价样本能说明失败原因"""
        report = run_fund_lhb_health_check(SAMPLE_INTERFACE_FAILED, symbol="603629.SH")
        ff = report.fund_flow_individual
        assert ff.status == FundLhbHealthStatus.FAILED
        assert ff.diagnosis
        assert any(kw in ff.diagnosis for kw in ("失败", "ProxyError", "vendor"))

    def test_fund_flow_records_unit_date_vendor(self):
        """主力资金必须记录单位、日期、供应商"""
        raw = {
            "fund_flow_individual": {
                "status": "HAS_DATA",
                "unit": "万元",
                "unit_verified": True,
                "vendor": "cn_akshare",
                "as_of": "2026-06-14",
                "fetched_at": "2026-06-14T10:00:00",
                "record_count": 20,
            }
        }
        h = inspect_fund_flow_health(raw)
        assert h.unit == "万元"
        assert h.as_of == "2026-06-14"
        assert h.vendor == "cn_akshare"
        assert h.record_count == 20


# ── EvidenceContract Compatibility ────────────────────────────────────

class TestEvidenceContractCompat:

    def test_health_from_evidence_contract_dict(self):
        """Health module can consume EvidenceContract.to_dict() output."""
        from tradingagents.dataflows.evidence_contract import EvidenceContract

        contract = EvidenceContract(
            field="fund_flow_individual",
            value="600519.SH 资金流数据",
            unit="万元",
            vendor="cn_akshare",
            endpoint="stock_individual_fund_flow",
            status="HAS_DATA",
            unit_verified=True,
            as_of=datetime.now().strftime("%Y-%m-%d"),
            record_count=20,
        )
        raw = {"fund_flow_individual": contract.to_dict()}
        h = inspect_fund_flow_health(raw)
        assert h.status == FundLhbHealthStatus.HAS_DATA
        assert h.vendor == "cn_akshare"

    def test_health_from_lhb_evidence_contract(self):
        from tradingagents.dataflows.evidence_contract import EvidenceContract

        contract = EvidenceContract(
            field="lhb",
            value="[G-007] LHB_NORMAL_NO_DATA: 非异动日",
            vendor="cn_akshare",
            endpoint="stock_lhb_detail_em",
            status="NORMAL_NO_DATA",
            query_mode="forced",
        )
        raw = {"lhb": contract.to_dict()}
        h = inspect_lhb_health(raw)
        assert h.status == FundLhbHealthStatus.NORMAL_NO_DATA
        assert h.query_mode == "forced"


# ── Readiness Gate Integration ────────────────────────────────────────

class TestReadinessGateIntegration:

    def test_failed_fund_flow_triggers_gate_block(self):
        """主力资金失败时 readiness strong action gate 应被阻断"""
        from tradingagents.agents.utils.readiness_score import build_fund_flow_provenance

        raw = {
            "fund_flow_individual": {
                "raw": "个股资金流向数据获取失败：ProxyError",
                "status": "FAILED",
                "unit_verified": False,
            }
        }
        prov = build_fund_flow_provenance(raw, {})
        assert prov["strong_evidence_allowed"] is False

    def test_normal_no_data_lhb_does_not_block(self):
        """龙虎榜 NORMAL_NO_DATA 不应阻断 strong action gate"""
        from tradingagents.agents.utils.readiness_score import build_lhb_provenance

        raw = {
            "lhb": {
                "status": "NORMAL_NO_DATA",
                "query_mode": "forced",
            }
        }
        prov = build_lhb_provenance(raw, {})
        assert prov["status"] == "NORMAL_NO_DATA"
        assert "正常" in prov["display_text"]
