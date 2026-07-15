"""Deterministic financial-report period normalization.

Chinese interim statements are cumulative.  This module labels the source
scope and derives single-quarter income/cash-flow values only when both inputs
are present.  It never substitutes FY cumulative values for Q4.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


Q1_YTD = "Q1_YTD"
H1_YTD = "H1_YTD"
Q3_YTD = "Q3_YTD"
FY_YTD = "FY_YTD"
SINGLE_QUARTER = "SINGLE_QUARTER"
POINT_IN_TIME = "POINT_IN_TIME"
FIELD_MISSING = "FIELD_MISSING"

_DATE_RE = re.compile(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})")
_METRIC_ALIASES = {
    "revenue": ("营业总收入", "营业收入", "营收"),
    "operating_cost": ("营业成本",),
    "net_profit": ("归属于母公司股东的净利润", "归母净利润", "净利润"),
    "deducted_net_profit": ("扣除非经常性损益后的净利润", "扣非净利润"),
    "operating_cashflow": ("经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"),
    "investing_cashflow": ("投资活动产生的现金流量净额",),
    "financing_cashflow": ("筹资活动产生的现金流量净额",),
    "total_assets": ("资产总计", "总资产"),
    "total_liabilities": ("负债合计", "总负债"),
}


@dataclass(frozen=True)
class FinancialFact:
    metric: str
    report_date: str
    period_scope: str
    value: float | None
    unit: str
    source: str
    is_derived: bool = False
    formula: str | None = None
    input_evidence_ids: tuple[str, ...] = ()
    status: str = "HAS_DATA"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "report_date": self.report_date,
            "period_scope": self.period_scope,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "is_derived": self.is_derived,
            "formula": self.formula,
            "input_evidence_ids": list(self.input_evidence_ids),
            "status": self.status,
        }


def period_scope_for_date(report_date: str, statement_type: str) -> str | None:
    match = _DATE_RE.search(str(report_date or ""))
    if not match:
        return None
    month_day = f"{match.group(2)}{match.group(3)}"
    if statement_type == "balance_sheet":
        return POINT_IN_TIME
    return {
        "0331": Q1_YTD,
        "0630": H1_YTD,
        "0930": Q3_YTD,
        "1231": FY_YTD,
    }.get(month_day)


def normalize_financial_records(
    records: Iterable[Mapping[str, Any]],
    *,
    statement_type: str,
    source: str,
    unit: str = "元",
) -> list[FinancialFact]:
    """Normalize provider records whose columns include a report-date field."""
    facts: list[FinancialFact] = []
    for index, record in enumerate(records):
        report_date = _record_date(record)
        scope = period_scope_for_date(report_date or "", statement_type)
        if not report_date or not scope:
            continue
        for metric, aliases in _METRIC_ALIASES.items():
            raw_value = _record_value(record, aliases)
            if raw_value is _NO_VALUE:
                continue
            value = _to_number(raw_value)
            facts.append(
                FinancialFact(
                    metric=metric,
                    report_date=report_date,
                    period_scope=scope,
                    value=value,
                    unit=unit,
                    source=source,
                    input_evidence_ids=(f"{statement_type}:{index}:{report_date}:{metric}",),
                    status="HAS_DATA" if value is not None else FIELD_MISSING,
                )
            )
    return facts


def derive_single_quarters(facts: Iterable[FinancialFact]) -> list[FinancialFact]:
    """Derive Q2/Q3/Q4 from cumulative income/cash-flow facts where possible."""
    grouped: dict[str, dict[tuple[int, str], FinancialFact]] = defaultdict(dict)
    for fact in facts:
        if fact.period_scope in {Q1_YTD, H1_YTD, Q3_YTD, FY_YTD}:
            year = _year(fact.report_date)
            if year:
                grouped[fact.metric][(year, fact.period_scope)] = fact

    derived: list[FinancialFact] = []
    for metric, period_facts in grouped.items():
        years = {year for year, _scope in period_facts}
        for year in years:
            derivations = (
                (H1_YTD, Q1_YTD, "Q2"),
                (Q3_YTD, H1_YTD, "Q3"),
                (FY_YTD, Q3_YTD, "Q4"),
            )
            for current_scope, previous_scope, quarter in derivations:
                current = period_facts.get((year, current_scope))
                previous = period_facts.get((year, previous_scope))
                if current is None:
                    continue
                date = _quarter_end(year, quarter)
                if previous is None or current.value is None or previous.value is None:
                    derived.append(
                        FinancialFact(
                            metric=metric,
                            report_date=date,
                            period_scope=SINGLE_QUARTER,
                            value=None,
                            unit=current.unit,
                            source=current.source,
                            is_derived=True,
                            formula=f"{current_scope}-{previous_scope}",
                            input_evidence_ids=current.input_evidence_ids,
                            status=FIELD_MISSING,
                        )
                    )
                    continue
                derived.append(
                    FinancialFact(
                        metric=metric,
                        report_date=date,
                        period_scope=SINGLE_QUARTER,
                        value=round(current.value - previous.value, 10),
                        unit=current.unit,
                        source=current.source,
                        is_derived=True,
                        formula=f"{current_scope}-{previous_scope}",
                        input_evidence_ids=current.input_evidence_ids + previous.input_evidence_ids,
                    )
                )
    return sorted(derived, key=lambda item: (item.metric, item.report_date))


def normalize_financial_markdown(raw: Any, *, statement_type: str, source: str) -> list[FinancialFact]:
    """Parse the provider's Markdown table without changing its raw payload."""
    return normalize_financial_records(
        _markdown_records(raw), statement_type=statement_type, source=source
    )


def render_financial_period_context(facts: Iterable[FinancialFact | Mapping[str, Any]]) -> str:
    rows = list(facts)
    if not rows:
        return "【财务期间口径】无可结构化的财报期间；禁止把年度累计值解释为单季度。"
    lines = ["【财务期间口径（程序计算）】"]
    for fact in rows[:40]:
        data = fact.to_dict() if isinstance(fact, FinancialFact) else dict(fact)
        raw_value = data.get("value")
        value = "缺失" if raw_value is None else f"{float(raw_value):g} {data.get('unit', '元')}"
        suffix = f"；公式={data.get('formula')}" if data.get("formula") else ""
        lines.append(
            f"- {data.get('metric', 'unknown')} {data.get('report_date', '—')} "
            f"[{data.get('period_scope', 'UNKNOWN')}] = {value}"
            f"；派生={bool(data.get('is_derived'))}；状态={data.get('status', 'UNKNOWN')}{suffix}"
        )
    return "\n".join(lines)


_NO_VALUE = object()


def _markdown_records(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, str):
        return []
    lines = [line for line in raw.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return []
    header = _markdown_cells(lines[0])
    if not header:
        return []
    records: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = _markdown_cells(line)
        if len(cells) != len(header):
            continue
        records.append(dict(zip(header, cells)))
    return records


def _markdown_cells(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def _record_date(record: Mapping[str, Any]) -> str | None:
    for key, value in record.items():
        if str(key).strip() in {"报告日", "报告期", "date", "report_date"}:
            match = _DATE_RE.search(str(value or ""))
            if match:
                return "-".join(match.groups())
    return None


def _record_value(record: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for key, value in record.items():
        if any(alias == str(key).strip() for alias in aliases):
            return value
    return _NO_VALUE


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"nan", "none", "-", "--"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def _year(report_date: str) -> int | None:
    match = _DATE_RE.search(report_date or "")
    return int(match.group(1)) if match else None


def _quarter_end(year: int, quarter: str) -> str:
    return {"Q2": f"{year}-06-30", "Q3": f"{year}-09-30", "Q4": f"{year}-12-31"}[quarter]
