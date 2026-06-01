# [DATA-004] raw_evidence_contract
"""
raw_evidence 来源契约定义。

所有关键字段的来源、端点、时间、单位、状态纳入统一契约结构，
使报告和前端都能回答"这个数从哪里来、是否实时、单位是什么、是否 fallback"。

契约字段：
  - field: 字段名称 (如 stock_data, fund_flow_individual)
  - value: 原始数据 (raw)
  - unit: 单位 (万元, 股, 条, %, 元/股 等)
  - vendor: 实际返回数据的厂商 (cn_akshare, cn_astock, sina, ...)
  - endpoint: 实际调用的端点 (stock_zh_a_hist, push2his.eastmoney.com/fflow, ...)
  - as_of: 数据日期或行情时间
  - fetched_at: 获取时间 ISO
  - status: HAS_DATA / FAILED / NOT_QUERIED / NORMAL_NO_DATA
  - fallback_from: 如果走了 fallback，记录原始厂商名
  - source_url: 可选，数据源链接
  - error: 失败时的错误摘要

使用示例：
    from tradingagents.dataflows.evidence_contract import (
        EvidenceContract,
        build_evidence_contract,
        compute_contract_completeness,
    )
    contract = build_evidence_contract("stock_data", raw_value, ...)
    score = compute_contract_completeness(raw_evidence)
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional


@dataclass
class EvidenceContract:
    field: str
    value: Any = None
    unit: Optional[str] = None
    vendor: str = ""
    endpoint: str = ""
    as_of: str = ""
    fetched_at: str = ""
    status: str = "NOT_QUERIED"
    fallback_from: Optional[str] = None
    source_url: Optional[str] = None
    error: Optional[str] = None

    is_realtime_patched: bool = False
    source_type: Optional[str] = None
    unit_verified: Optional[bool] = None
    query_mode: Optional[str] = None
    adjustment: Optional[str] = None
    force_reason: Optional[str] = None
    record_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.value,
            "field": self.field,
            "unit": self.unit,
            "vendor": self.vendor,
            "endpoint": self.endpoint,
            "as_of": self.as_of,
            "fetched_at": self.fetched_at,
            "status": self.status,
            "fallback_from": self.fallback_from,
            "source_url": self.source_url,
            "error": self.error,
            "is_realtime_patched": self.is_realtime_patched,
            "source_type": self.source_type,
            "unit_verified": self.unit_verified,
            "query_mode": self.query_mode,
            "adjustment": self.adjustment,
            "force_reason": self.force_reason,
            "record_count": self.record_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceContract":
        return cls(
            field=data.get("field", ""),
            value=data.get("raw"),
            unit=data.get("unit"),
            vendor=data.get("vendor", ""),
            endpoint=data.get("endpoint", ""),
            as_of=data.get("as_of", ""),
            fetched_at=data.get("fetched_at", ""),
            status=data.get("status", "NOT_QUERIED"),
            fallback_from=data.get("fallback_from"),
            source_url=data.get("source_url"),
            error=data.get("error"),
            is_realtime_patched=data.get("is_realtime_patched", False),
            source_type=data.get("source_type"),
            unit_verified=data.get("unit_verified"),
            query_mode=data.get("query_mode"),
            adjustment=data.get("adjustment"),
            force_reason=data.get("force_reason"),
            record_count=data.get("record_count", 0),
        )

    @property
    def has_data(self) -> bool:
        return self.status == "HAS_DATA"

    @property
    def is_failed(self) -> bool:
        return self.status == "FAILED"

    @property
    def is_fallback(self) -> bool:
        return self.fallback_from is not None and self.fallback_from != ""

    @property
    def unit_known(self) -> bool:
        return self.unit is not None and self.unit != ""

    @property
    def endpoint_known(self) -> bool:
        return self.endpoint != ""


_EVIDENCE_KEY_TO_DATA_TYPE: Dict[str, str] = {
    "stock_data": "ohlcv",
    "news": "news",
    "global_news": "global_news",
    "fund_flow_board": "board_fund_flow",
    "fund_flow_individual": "fund_flow",
    "lhb": "lhb",
    "fundamentals": "financials",
    "balance_sheet": "financials",
    "cashflow": "financials",
    "income_statement": "financials",
    "insider_transactions": "insider",
    "zt_pool": "zt_pool",
    "hot_stocks": "hot_stocks",
    "indicators": "ohlcv",
    "vpa_indicators": "ohlcv",
    "announcements": "notice",
}


def resolve_data_type(evidence_key: str) -> str:
    return _EVIDENCE_KEY_TO_DATA_TYPE.get(evidence_key, "")


def resolve_endpoint(vendor: str, data_type: str) -> str:
    try:
        from .source_catalog import get_primary_source, get_sources_for_type
        sources = get_sources_for_type(data_type)
        for s in sources:
            if s.vendor == vendor:
                return s.endpoint
        if sources:
            return sources[0].endpoint
    except Exception:
        pass
    return ""


def resolve_fallback_info(vendor: str, data_type: str) -> Optional[str]:
    try:
        from .source_catalog import get_primary_source
        primary = get_primary_source(data_type)
        if primary and primary.vendor != vendor:
            return primary.vendor
    except Exception:
        pass
    return None


_REQUIRED_FIELDS_FOR_COMPLETENESS: Dict[str, List[str]] = {
    "stock_data": ["status", "vendor", "unit"],
    "fund_flow_individual": ["status", "vendor", "unit", "unit_verified"],
    "fund_flow_board": ["status", "vendor"],
    "lhb": ["status", "vendor"],
    "news": ["status", "vendor"],
    "global_news": ["status", "vendor"],
    "announcements": ["status", "vendor"],
    "fundamentals": ["status", "vendor"],
}


def compute_contract_completeness(raw_evidence: Dict[str, Any]) -> Dict[str, Any]:
    total = 0
    satisfied = 0
    missing_details: Dict[str, List[str]] = {}

    for ev_key, required in _REQUIRED_FIELDS_FOR_COMPLETENESS.items():
        entry = raw_evidence.get(ev_key)
        if entry is None:
            total += len(required)
            missing_details[ev_key] = required
            continue

        if isinstance(entry, dict):
            contract = EvidenceContract.from_dict(entry)
        else:
            total += len(required)
            missing_details[ev_key] = required
            continue

        for req_field in required:
            total += 1
            if req_field == "status" and contract.has_data:
                satisfied += 1
            elif req_field == "vendor" and contract.vendor != "":
                satisfied += 1
            elif req_field == "unit" and contract.unit_known:
                satisfied += 1
            elif req_field == "unit_verified" and contract.unit_verified is True:
                satisfied += 1
            elif req_field == "endpoint" and contract.endpoint_known:
                satisfied += 1
            else:
                if ev_key not in missing_details:
                    missing_details[ev_key] = []
                missing_details[ev_key].append(req_field)

    score = int((satisfied / total) * 100) if total > 0 else 0
    return {
        "completeness_score": score,
        "total_checks": total,
        "satisfied_checks": satisfied,
        "missing_details": missing_details,
    }


def build_data_source_summary(raw_evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary = []
    for key in sorted(raw_evidence.keys()):
        entry = raw_evidence[key]
        if not isinstance(entry, dict):
            continue
        contract = EvidenceContract.from_dict(entry)
        summary.append({
            "field": key,
            "vendor": contract.vendor,
            "endpoint": contract.endpoint,
            "status": contract.status,
            "unit": contract.unit,
            "unit_verified": contract.unit_verified,
            "is_fallback": contract.is_fallback,
            "fallback_from": contract.fallback_from,
            "is_realtime_patched": contract.is_realtime_patched,
            "as_of": contract.as_of,
            "record_count": contract.record_count,
            "error": contract.error,
        })
    return summary
