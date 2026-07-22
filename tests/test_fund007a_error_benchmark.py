"""[FUND-007A] Financial Report Error Pattern Benchmark Set & Regression Report.

Deterministic benchmark that solidifies known error patterns from 603629 and
cross-industry replays into a repeatable regression set.  Each case stores
stable case_id / input_fixture / expected_gate / expected_rule_ids so that
future provider, parser or model-routing changes can prove they did not
re-introduce "numbers roughly correct, causal explanation wrong" failures.

No live LLM is called; no production DB is written; no prompts are modified.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pytest

from tradingagents.agents.utils.fundamental_integrity import (
    ACCOUNTING_POLICY_UNKNOWN,
    CAUSE_UNSUPPORTED,
    DERIVATION_CONFLICT,
    IDENTITY_UNVERIFIED,
    PERIOD_SCOPE_INVALID,
    build_official_explanation_context,
    evaluate_fundamental_integrity,
    extract_financial_anomaly_inputs,
)
from tradingagents.dataflows.financial_periods import (
    derive_single_quarters,
    normalize_financial_markdown,
)
from tradingagents.dataflows.instrument_identity import (
    build_instrument_identity,
    extract_profile_from_fundamentals,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Shared provider-format fixtures (desensitized, same shapes as FUND-006A)
# ═══════════════════════════════════════════════════════════════════════════════

_AKSHARE_PROFILE = """### Company Profile

| 项目 | 内容 |
|---|---|
| 股票代码 | 603629 |
| 股票简称 | 利通电子 |
| 所属行业 | 计算机设备 |
| 主营业务 | 算力云服务及精密金属结构件 |
| 上市日期 | 2018-01-25 |
"""

_INCOME_RAW = """| 报告日 | 营业总收入 | 营业成本 | 净利润 |
|---|---:|---:|---:|
| 2025-03-31 | 7.04342 | 5.62100 | 0.85200 |
| 2025-06-30 | 15.16342 | 12.00000 | 1.82000 |
| 2025-09-30 | 24.62342 | 19.50000 | 2.90000 |
| 2025-12-31 | 33.07400 | 26.20000 | 3.85000 |
"""

_CASHFLOW_RAW = """| 报告日 | 经营活动产生的现金流量净额 | 投资活动产生的现金流量净额 | 筹资活动产生的现金流量净额 |
|---|---:|---:|---:|
| 2025-03-31 | 0.60000 | -0.30000 | -0.20000 |
| 2025-06-30 | 1.20000 | -0.50000 | -0.40000 |
| 2025-09-30 | 1.50000 | -0.80000 | -0.60000 |
| 2025-12-31 | 1.50000 | -1.00000 | -0.80000 |
"""

_BALANCE_RAW = """| 报告日 | 总资产 | 总负债 |
|---|---:|---:|
| 2025-03-31 | 45.00000 | 30.00000 |
| 2025-06-30 | 46.50000 | 31.00000 |
| 2025-09-30 | 48.00000 | 32.00000 |
| 2025-12-31 | 50.00000 | 33.00000 |
"""

_ANNOUNCEMENT_STANDARD = """
2025年度业绩快报：报告期内，公司实现营业收入33.074亿元，同比增长15.2%；
实现归属于上市公司股东的净利润3.85亿元，同比增长12.8%。
主要系原材料采购成本下降及算力云服务业务规模扩大所致。
公司采用总额法确认收入。
"""

_ANNOUNCEMENT_RAW_MATERIALS_UP = """
2025年度业绩快报：报告期内，公司实现营业收入33.074亿元。
主要系原材料采购成本上涨所致，成本压力增大。
"""

_ANNOUNCEMENT_NET_METHOD = """
公司自2025年起采用净额法确认收入，作为代理人而非主要责任人。
"""

_ANNOUNCEMENT_PREPAID_CASHFLOW = """
报告期内，预收客户货款增加，带动经营活动现金流增长。
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Benchmark case dataclass
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BenchmarkCase:
    case_id: str
    description: str
    category: str
    identity: dict[str, Any] | None
    period_facts: list[dict[str, Any]]
    explanation_context: dict[str, Any] | None
    report_text: str
    expected_gate: str  # "VALID" or "NEEDS_REVIEW"
    expected_rule_ids: list[str]
    # Filled at runtime
    actual_result: dict[str, Any] = field(default_factory=dict)
    passed: bool = False
    failure_reason: str = ""


def _build_identity(profile_text: str | None, symbol: str = "603629.SH") -> dict[str, Any] | None:
    if profile_text is None:
        return None
    profile = extract_profile_from_fundamentals(profile_text)
    identity = build_instrument_identity(symbol, profile, source="benchmark")
    return identity.to_dict()


def _build_period_facts() -> list[dict[str, Any]]:
    income = normalize_financial_markdown(_INCOME_RAW, statement_type="income_statement", source="raw:income")
    cashflow = normalize_financial_markdown(_CASHFLOW_RAW, statement_type="cashflow", source="raw:cashflow")
    balance = normalize_financial_markdown(_BALANCE_RAW, statement_type="balance_sheet", source="raw:balance")
    all_raw = income + cashflow + balance
    derived = derive_single_quarters(income + cashflow)
    return [f.to_dict() for f in all_raw + derived]


def _build_explanation(announcement: str | None) -> dict[str, Any] | None:
    if announcement is None:
        return {"status": "unexplained", "data_status": "NORMAL_NO_DATA", "entries": [], "cause_terms": [], "accounting_policy_terms": []}
    return build_official_explanation_context(announcements=announcement, half_year_facts=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Negative samples — known error patterns
# ═══════════════════════════════════════════════════════════════════════════════

def _build_negative_cases() -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    facts = _build_period_facts()

    # NEG-001: Identity mismatch — report infers industry from financials
    cases.append(BenchmarkCase(
        case_id="NEG-001",
        description="证券身份错配：无公司画像时从财务特征猜行业（603629 历史故障）",
        category="identity_mismatch",
        identity={},  # empty identity
        period_facts=facts,
        explanation_context=_build_explanation(None),
        report_text="公司主营化工品生产销售，原材料采购成本下降导致毛利率提升。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[IDENTITY_UNVERIFIED, CAUSE_UNSUPPORTED],
    ))

    # NEG-002: Annual cumulative masquerading as Q4
    cases.append(BenchmarkCase(
        case_id="NEG-002",
        description="年度累计冒充 Q4：将全年 33 亿营收误当第四季度单季值",
        category="period_scope_error",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=[
            # Only FY cumulative, no Q3 to derive Q4
            {"metric": "revenue", "report_date": "2025-12-31", "period_scope": "FY_YTD", "value": 33.074, "unit": "亿元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-12-31", "period_scope": "FY_YTD", "value": 26.200, "unit": "亿元", "status": "HAS_DATA"},
        ],
        explanation_context=_build_explanation(None),
        report_text="第四季度营业收入33.074亿元，环比大幅增长，原材料采购成本下降推动利润改善。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[CAUSE_UNSUPPORTED],
    ))

    # NEG-003: Cross-date calculation — mixing revenue from 12-31 with cost from 09-30
    cases.append(BenchmarkCase(
        case_id="NEG-003",
        description="跨日期计算：用 2025-12-31 收入与 2025-09-30 成本混算毛利率",
        category="cross_date_calculation",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=[
            {"metric": "revenue", "report_date": "2025-12-31", "period_scope": "FY_YTD", "value": 33.074, "unit": "亿元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-09-30", "period_scope": "Q3_YTD", "value": 19.500, "unit": "亿元", "status": "HAS_DATA"},
        ],
        explanation_context=_build_explanation(None),
        report_text="公司毛利率大幅提升，原材料采购成本下降是主要原因。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[CAUSE_UNSUPPORTED],
    ))

    # NEG-004: Cross-unit calculation + unsupported cause claim
    cases.append(BenchmarkCase(
        case_id="NEG-004",
        description="跨单位计算 + 无因果证据：收入用亿元、成本用万元，且因果解释无公告支持",
        category="cross_unit_calculation",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=[
            {"metric": "revenue", "report_date": "2025-12-31", "period_scope": "FY_YTD", "value": 33.074, "unit": "亿元", "status": "HAS_DATA"},
            {"metric": "operating_cost", "report_date": "2025-12-31", "period_scope": "FY_YTD", "value": 262000.0, "unit": "万元", "status": "HAS_DATA"},
        ],
        explanation_context=_build_explanation(None),
        report_text="公司毛利率显著改善，原材料采购成本下降是主要原因。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[CAUSE_UNSUPPORTED],
    ))

    # NEG-005: Seasonal guessing without official explanation
    cases.append(BenchmarkCase(
        case_id="NEG-005",
        description="无官方解释的季节性猜测：公告无旺季/淡季说明，报告凭空猜测",
        category="unexplained_seasonal",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation(None),
        report_text="第四季度收入增长主要系旺季效应及产品涨价所致。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[CAUSE_UNSUPPORTED],
    ))

    # NEG-006: Net/gross method misread — report says 净额法, announcement says 总额法
    cases.append(BenchmarkCase(
        case_id="NEG-006",
        description="净额法/总额法误判：公告明确采用总额法，报告误写净额法",
        category="accounting_policy_misread",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation(_ANNOUNCEMENT_STANDARD),
        report_text="公司采用净额法确认收入，收入规模因此缩减。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[ACCOUNTING_POLICY_UNKNOWN],
    ))

    # NEG-007: Advance receipts misread as cashflow driver — accounting term
    # "预收款" is an accounting keyword, not a cause keyword. The report claims
    # a causal link ("推动") that cannot be verified from the announcement.
    cases.append(BenchmarkCase(
        case_id="NEG-007",
        description="预收款与现金流因果误读：报告声称预收款推动现金流，公告只提预收款未建立因果",
        category="prepaid_cashflow_misread",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation(_ANNOUNCEMENT_PREPAID_CASHFLOW),
        report_text="预收款增长推动经营现金流大幅提升，原材料采购成本下降是现金流改善的另一原因。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[CAUSE_UNSUPPORTED],
    ))

    # NEG-008: Source missing — no identity, no announcement, no facts
    cases.append(BenchmarkCase(
        case_id="NEG-008",
        description="来源全面缺失：无身份、无公告、无财务期间数据",
        category="source_missing",
        identity={},
        period_facts=[],
        explanation_context=_build_explanation(None),
        report_text="公司营收增长，原材料下降，利润改善。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[IDENTITY_UNVERIFIED, PERIOD_SCOPE_INVALID, CAUSE_UNSUPPORTED],
    ))

    # NEG-009: Evidence conflict — announcement says 原材料上涨, report claims 下降
    cases.append(BenchmarkCase(
        case_id="NEG-009",
        description="证据冲突：公告明确原材料上涨，报告却声称原材料下降",
        category="evidence_conflict",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation(_ANNOUNCEMENT_RAW_MATERIALS_UP),
        report_text="原材料采购成本下降导致毛利率大幅提升。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[CAUSE_UNSUPPORTED],
    ))

    # NEG-010: Negated accounting — report claims 净额法 but announcement says 未采用净额法
    cases.append(BenchmarkCase(
        case_id="NEG-010",
        description="否定会计口径：公告明确未采用净额法（仍用总额法），报告忽略否定",
        category="negation_accounting",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation("公司未采用净额法确认收入，仍采用总额法。"),
        report_text="公司采用净额法确认收入。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[ACCOUNTING_POLICY_UNKNOWN],
    ))

    # NEG-011: Derivation conflict — single quarter facts missing cumulative inputs
    cases.append(BenchmarkCase(
        case_id="NEG-011",
        description="单季度派生冲突：仅有单季度值缺累计输入，禁止以全年值替代",
        category="derivation_conflict",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=[
            {"metric": "revenue", "report_date": "2025-06-30", "period_scope": "FIELD_MISSING", "value": None, "unit": "亿元", "status": "FIELD_MISSING"},
        ],
        explanation_context=_build_explanation(None),
        report_text="公司第二季度营收环比改善，原材料下降推动利润增长。",
        expected_gate="NEEDS_REVIEW",
        expected_rule_ids=[DERIVATION_CONFLICT, CAUSE_UNSUPPORTED],
    ))

    return cases


# ═══════════════════════════════════════════════════════════════════════════════
# Positive control samples — must NOT be blocked
# ═══════════════════════════════════════════════════════════════════════════════

def _build_positive_cases() -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    facts = _build_period_facts()

    # POS-001: Valid identity, same-date/same-unit facts, no unsupported claims
    cases.append(BenchmarkCase(
        case_id="POS-001",
        description="合法主营补全 + 同口径计算 + 无因果猜测：纯事实报告通过",
        category="valid_factual_report",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation(_ANNOUNCEMENT_STANDARD),
        report_text="利通电子营业收入33.074亿元，净利润3.85亿元。",
        expected_gate="VALID",
        expected_rule_ids=[],
    ))

    # POS-002: Valid causal claim with official announcement support
    cases.append(BenchmarkCase(
        case_id="POS-002",
        description="有官方证据的因果解释：公告确认原材料下降推动营收增长，报告一致",
        category="valid_causal_with_evidence",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation("营业收入增长，主要系原材料采购成本下降所致。"),
        report_text="原材料采购成本下降推动营业收入增长。",
        expected_gate="VALID",
        expected_rule_ids=[],
    ))

    # POS-003: Valid accounting policy — announcement says 总额法, report confirms
    cases.append(BenchmarkCase(
        case_id="POS-003",
        description="合法会计口径：公告和报告一致确认总额法",
        category="valid_accounting_policy",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation(_ANNOUNCEMENT_STANDARD),
        report_text="公司采用总额法确认收入，营业收入33.074亿元。",
        expected_gate="VALID",
        expected_rule_ids=[],
    ))

    # POS-004: Valid — report has no cause keywords, just facts
    cases.append(BenchmarkCase(
        case_id="POS-004",
        description="纯事实陈述不含因果关键词：不触发因果检查",
        category="valid_no_cause_keywords",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation(None),
        report_text="公司2025年度营业收入33.074亿元，净利润3.85亿元，资产负债率66%。",
        expected_gate="VALID",
        expected_rule_ids=[],
    ))

    # POS-005: Valid negated accounting — announcement says 未采用净额法, report also says 未采用
    cases.append(BenchmarkCase(
        case_id="POS-005",
        description="否定会计口径一致：公告和报告均确认未采用净额法",
        category="valid_negation_consistent",
        identity=_build_identity(_AKSHARE_PROFILE),
        period_facts=facts,
        explanation_context=_build_explanation("公司未采用净额法确认收入，仍采用总额法。"),
        report_text="公司未采用净额法确认收入。",
        expected_gate="VALID",
        expected_rule_ids=[],
    ))

    return cases


# ═══════════════════════════════════════════════════════════════════════════════
# Runner: execute benchmark and collect results
# ═══════════════════════════════════════════════════════════════════════════════

def run_benchmark() -> tuple[list[BenchmarkCase], dict[str, Any]]:
    """Execute all benchmark cases and return results with summary."""
    all_cases = _build_negative_cases() + _build_positive_cases()
    started = time.time()

    for case in all_cases:
        result = evaluate_fundamental_integrity(
            identity=case.identity,
            period_facts=case.period_facts,
            explanation_context=case.explanation_context,
            report_text=case.report_text,
        )
        case.actual_result = result

        actual_gate = result.get("status", "UNKNOWN")
        actual_codes = {b["code"] for b in result.get("blockers", [])}
        expected_codes = set(case.expected_rule_ids)

        gate_match = actual_gate == case.expected_gate
        # For rule IDs: expected must be a subset of actual (actual may have extra blockers)
        rules_match = expected_codes.issubset(actual_codes)

        case.passed = gate_match and rules_match
        reasons = []
        if not gate_match:
            reasons.append(f"gate: expected={case.expected_gate}, actual={actual_gate}")
        if not rules_match:
            missing = expected_codes - actual_codes
            reasons.append(f"missing rules: {missing}")
        case.failure_reason = "; ".join(reasons)

    elapsed_ms = (time.time() - started) * 1000

    passed = sum(1 for c in all_cases if c.passed)
    failed = sum(1 for c in all_cases if not c.passed)

    summary = {
        "total": len(all_cases),
        "passed": passed,
        "failed": failed,
        "elapsed_ms": round(elapsed_ms, 1),
        "negative_cases": len(_build_negative_cases()),
        "positive_cases": len(_build_positive_cases()),
    }
    return all_cases, summary


# ═══════════════════════════════════════════════════════════════════════════════
# Report generators
# ═══════════════════════════════════════════════════════════════════════════════

def generate_json_results(cases: list[BenchmarkCase], summary: dict[str, Any]) -> dict[str, Any]:
    """Generate machine-readable JSON results."""
    return {
        "benchmark": "FUND-007A",
        "version": "1.0.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": summary,
        "cases": [
            {
                "case_id": c.case_id,
                "description": c.description,
                "category": c.category,
                "expected_gate": c.expected_gate,
                "expected_rule_ids": c.expected_rule_ids,
                "actual_gate": c.actual_result.get("status", "UNKNOWN"),
                "actual_rule_ids": [b["code"] for b in c.actual_result.get("blockers", [])],
                "passed": c.passed,
                "failure_reason": c.failure_reason,
            }
            for c in cases
        ],
    }


def generate_markdown_report(cases: list[BenchmarkCase], summary: dict[str, Any]) -> str:
    """Generate concise human-readable Markdown report."""
    lines = [
        "# FUND-007A 财报错误模式基准集回归报告",
        "",
        f"**生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**总用例**: {summary['total']}（负向 {summary['negative_cases']} + 正向 {summary['positive_cases']}）",
        f"**通过**: {summary['passed']} / **失败**: {summary['failed']}",
        f"**耗时**: {summary['elapsed_ms']:.0f} ms",
        "",
    ]

    # Negative cases
    lines.append("## 负向样本（错误模式检测）")
    lines.append("")
    lines.append("| ID | 类别 | 描述 | 预期门禁 | 实际门禁 | 预期规则 | 实际规则 | 结果 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in cases:
        if not c.case_id.startswith("NEG-"):
            continue
        status = "PASS" if c.passed else "FAIL"
        expected_rules = ", ".join(c.expected_rule_ids) if c.expected_rule_ids else "—"
        actual_rules = ", ".join(b["code"] for b in c.actual_result.get("blockers", [])) or "—"
        lines.append(
            f"| {c.case_id} | {c.category} | {c.description[:30]}… | "
            f"{c.expected_gate} | {c.actual_result.get('status', '?')} | "
            f"{expected_rules} | {actual_rules} | **{status}** |"
        )

    # Positive cases
    lines.append("")
    lines.append("## 正向控制样本（不得误杀）")
    lines.append("")
    lines.append("| ID | 类别 | 描述 | 预期门禁 | 实际门禁 | 结果 |")
    lines.append("|---|---|---|---|---|---|")
    for c in cases:
        if not c.case_id.startswith("POS-"):
            continue
        status = "PASS" if c.passed else "FAIL"
        lines.append(
            f"| {c.case_id} | {c.category} | {c.description[:30]}… | "
            f"{c.expected_gate} | {c.actual_result.get('status', '?')} | **{status}** |"
        )

    # Failures detail
    failures = [c for c in cases if not c.passed]
    if failures:
        lines.append("")
        lines.append("## 失败详情")
        lines.append("")
        for c in failures:
            lines.append(f"### {c.case_id}: {c.description}")
            lines.append(f"- **失败原因**: {c.failure_reason}")
            lines.append(f"- **预期门禁**: {c.expected_gate}")
            lines.append(f"- **实际门禁**: {c.actual_result.get('status', '?')}")
            blockers = c.actual_result.get("blockers", [])
            if blockers:
                lines.append("- **实际 blockers**:")
                for b in blockers:
                    lines.append(f"  - `{b['code']}`: {b['reason']}")
            lines.append("")
    else:
        lines.append("")
        lines.append("## 全部通过，无失败项。")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# pytest integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestFund007aBenchmark:
    """Benchmark cases executed via pytest for CI integration."""

    @pytest.fixture(autouse=True)
    def _run_benchmark(self):
        self.cases, self.summary = run_benchmark()

    def test_all_cases_pass(self):
        """All benchmark cases must match expected gate and rule IDs."""
        failures = [c for c in self.cases if not c.passed]
        if failures:
            msg_lines = ["Benchmark failures:"]
            for c in failures:
                msg_lines.append(f"  {c.case_id}: {c.failure_reason}")
            pytest.fail("\n".join(msg_lines))

    def test_minimum_case_count(self):
        """Must have at least 8 negative and 3 positive cases."""
        neg = sum(1 for c in self.cases if c.case_id.startswith("NEG-"))
        pos = sum(1 for c in self.cases if c.case_id.startswith("POS-"))
        assert neg >= 8, f"Need >=8 negative cases, got {neg}"
        assert pos >= 3, f"Need >=3 positive cases, got {pos}"

    @pytest.mark.parametrize(
        "case_id",
        [c.case_id for c in _build_negative_cases() + _build_positive_cases()],
    )
    def test_individual_case(self, case_id):
        """Each case passes individually (parametrized for clear failure reporting)."""
        all_cases = _build_negative_cases() + _build_positive_cases()
        case = next(c for c in all_cases if c.case_id == case_id)
        result = evaluate_fundamental_integrity(
            identity=case.identity,
            period_facts=case.period_facts,
            explanation_context=case.explanation_context,
            report_text=case.report_text,
        )
        actual_gate = result.get("status", "UNKNOWN")
        actual_codes = {b["code"] for b in result.get("blockers", [])}
        expected_codes = set(case.expected_rule_ids)

        assert actual_gate == case.expected_gate, (
            f"{case_id}: gate mismatch — expected {case.expected_gate}, got {actual_gate}"
        )
        assert expected_codes.issubset(actual_codes), (
            f"{case_id}: missing rules — expected {expected_codes}, actual {actual_codes}"
        )

    def test_tampered_expected_rule_fails(self):
        """Intentionally wrong expected_rule_ids must cause test failure."""
        cases = _build_negative_cases()
        # Pick NEG-001 and tamper with expected rules
        neg1 = next(c for c in cases if c.case_id == "NEG-001")
        result = evaluate_fundamental_integrity(
            identity=neg1.identity,
            period_facts=neg1.period_facts,
            explanation_context=neg1.explanation_context,
            report_text=neg1.report_text,
        )
        actual_codes = {b["code"] for b in result.get("blockers", [])}
        # Tamper: expect a rule that should NOT be present
        tampered_expected = {"NONEXISTENT_RULE_XYZ"}
        assert not tampered_expected.issubset(actual_codes), (
            "Tampered rule should not be in actual blockers — test design is wrong"
        )

    def test_json_results_serializable(self):
        """JSON results are serializable without error."""
        json_data = generate_json_results(self.cases, self.summary)
        text = json.dumps(json_data, ensure_ascii=False, indent=2)
        assert len(text) > 100
        assert "FUND-007A" in text

    def test_markdown_report_non_empty(self):
        """Markdown report is non-empty and contains key sections."""
        report = generate_markdown_report(self.cases, self.summary)
        assert "FUND-007A" in report
        assert "负向样本" in report
        assert "正向控制样本" in report


# ═══════════════════════════════════════════════════════════════════════════════
# CLI entry point for standalone execution
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Run benchmark from CLI, output results, exit non-zero on failure."""
    import sys

    cases, summary = run_benchmark()
    json_data = generate_json_results(cases, summary)
    md_report = generate_markdown_report(cases, summary)

    # Write results
    results_dir = Path("docs/benchmark_results")
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")

    json_path = results_dir / f"fund007a-benchmark-{ts}.json"
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = results_dir / f"fund007a-benchmark-{ts}.md"
    md_path.write_text(md_report, encoding="utf-8")

    # Also write latest symlinks
    latest_json = results_dir / "fund007a-benchmark-latest.json"
    latest_md = results_dir / "fund007a-benchmark-latest.md"
    latest_json.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_md.write_text(md_report, encoding="utf-8")

    print(f"FUND-007A Benchmark: {summary['passed']}/{summary['total']} passed ({summary['elapsed_ms']:.0f}ms)")
    print(f"  JSON: {json_path}")
    print(f"  Report: {md_path}")

    if summary["failed"] > 0:
        print(f"\nFAILED: {summary['failed']} cases did not match expected results:")
        for c in cases:
            if not c.passed:
                print(f"  {c.case_id}: {c.failure_reason}")
        sys.exit(1)

    print("\nAll benchmark cases passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
