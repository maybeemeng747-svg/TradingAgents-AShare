"""Report service for database operations."""

import json
import json_repair
import logging
import re

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Iterable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import func, inspect as sa_inspect
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm import Session, load_only

from api.database import ReportDB


REPORT_SUMMARY_COLUMNS = (
    ReportDB.id,
    ReportDB.user_id,
    ReportDB.symbol,
    ReportDB.trade_date,
    ReportDB.status,
    ReportDB.error,
    ReportDB.decision,
    ReportDB.direction,
    ReportDB.research_direction,
    ReportDB.execution_action,
    ReportDB.action_label,
    ReportDB.confidence,
    ReportDB.target_price,
    ReportDB.stop_loss_price,
    ReportDB.risk_items,
    ReportDB.key_metrics,
    ReportDB.analyst_traces,
    ReportDB.created_at,
    ReportDB.updated_at,
)

ACTIVE_REPORT_STATUSES = ("pending", "running")
STALE_REPORT_ERROR_MESSAGE = "分析任务已中断，请重新发起分析"


def normalize_report_action_label(report: Any) -> Any:
    """Normalize stale decision presentation on read without database writes.

    Older rows may have action_label="数据不足观察" even when the final
    research_direction was directional. Keep true neutral insufficient-data
    cases unchanged, recover useful labels for directional WAIT reports, and
    hide stale bullish targets from bearish WAIT decisions.
    """
    if not report:
        return report
    if isinstance(report, ReportDB) and "result_data" in sa_inspect(report).unloaded:
        result_data = None
    else:
        result_data = getattr(report, "result_data", None)
    result_action_label = result_data.get("action_label") if isinstance(result_data, dict) else None
    result_execution_action = result_data.get("execution_action") if isinstance(result_data, dict) else None
    result_research_direction = result_data.get("research_direction") if isinstance(result_data, dict) else None
    label = getattr(report, "action_label", None) or result_action_label
    action = getattr(report, "execution_action", None) or result_execution_action
    direction = (
        getattr(report, "research_direction", None)
        or result_research_direction
        or getattr(report, "direction", None)
    )
    decision = (
        getattr(report, "decision", None)
        or (result_data.get("decision") if isinstance(result_data, dict) else None)
    )
    normalized_label = None
    if label == "数据不足观察" and action == "WAIT":
        if direction in ("偏空", "看空"):
            normalized_label = "回避"
        elif direction in ("偏多", "看多"):
            normalized_label = "等待触发"
    normalized_action = str(action or "").upper()
    is_exit_action = (
        normalized_action in ("REDUCE", "EXIT")
        if normalized_action
        else str(decision or "").upper() in ("SELL", "REDUCE", "EXIT")
    )
    suppress_target = not is_exit_action and (
        label in ("回避", "禁止买入")
        or (
            direction in ("偏空", "看空")
            and (
                action == "WAIT"
                or (
                    action in (None, "")
                    and label in (None, "", "数据不足观察", "观望", "等待触发")
                    and str(decision or "").upper() not in ("SELL", "REDUCE", "EXIT")
                )
            )
        )
    )
    normalized_result_data = None
    if isinstance(result_data, dict):
        if normalized_label and result_action_label == "数据不足观察":
            normalized_result_data = dict(result_data)
            normalized_result_data["action_label"] = normalized_label
        if suppress_target and result_data.get("target_price") is not None:
            normalized_result_data = dict(normalized_result_data or result_data)
            normalized_result_data["target_price"] = None

    if normalized_label or suppress_target:
        if isinstance(report, ReportDB):
            if normalized_label:
                set_committed_value(report, "action_label", normalized_label)
            if suppress_target:
                set_committed_value(report, "target_price", None)
            if normalized_result_data is not None:
                set_committed_value(report, "result_data", normalized_result_data)
        else:
            if normalized_label:
                setattr(report, "action_label", normalized_label)
            if suppress_target:
                setattr(report, "target_price", None)
            if normalized_result_data is not None:
                setattr(report, "result_data", normalized_result_data)
    return report


# ─── Structured extraction schemas ───────────────────────────────────────────

from pydantic import field_validator


class RiskItemSchema(BaseModel):
    name: str = Field(..., description="风险名称，15字以内")
    level: str = Field("medium", description="风险等级")
    description: str = Field("", description="一句话说明，30字以内")

    @field_validator("level", mode="before")
    @classmethod
    def _coerce_level(cls, v):
        if isinstance(v, str) and v.lower() in ("high", "medium", "low"):
            return v.lower()
        return "medium"


class KeyMetricSchema(BaseModel):
    name: str = Field(..., description="指标名称，如 PE、ROE、营收增速")
    value: str = Field(..., description="指标值，包含单位，如 28.5x、15.2%")
    status: str = Field("neutral", description="优劣判断")

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v):
        # LLM 可能返回数字而非字符串
        return str(v) if not isinstance(v, str) else v

    @field_validator("status", mode="after")
    @classmethod
    def _infer_status(cls, v, info):
        """如果 LLM 给了 neutral，尝试从 name+value 推断实际优劣。"""
        if v in ("good", "bad"):
            return v
        value = info.data.get("value", "")
        name = info.data.get("name", "").lower()
        vl = str(value).lower()
        # 数据缺失 → bad
        if any(k in vl for k in ["缺失", "nan", "missing", "n/a", "无数据", "-"]):
            return "bad"
        # 尝试提取数字
        import re
        nums = re.findall(r'[\-+]?[\d.]+', vl)
        if not nums:
            return v
        num = float(nums[0])
        if "roe" in name:
            return "good" if num > 15 else ("bad" if num < 8 else v)
        if "roa" in name:
            return "good" if num > 8 else ("bad" if num < 3 else v)
        if any(k in name for k in ["负债", "debt", "liability"]):
            return "bad" if num > 70 else ("good" if num < 40 else v)
        if any(k in name for k in ["增速", "增长", "growth"]):
            return "good" if num > 20 else ("bad" if num < 5 else v)
        if any(k in name for k in ["现金流", "cash flow"]):
            return "bad" if num < 0 else ("good" if num > 50 else v)
        return v


class StructuredReport(BaseModel):
    decision: str = Field("HOLD", description="交易决策关键词：BUY/SELL/HOLD/增持/减持/持有")
    confidence: Optional[int] = Field(None, description="整体置信度 0-100")
    target_price: Optional[float] = Field(None, description="目标价（数字，无单位）")
    stop_loss_price: Optional[float] = Field(None, description="止损价（数字，无单位）")
    risks: List[RiskItemSchema] = Field(default_factory=list, description="主要风险，最多5条")
    key_metrics: List[KeyMetricSchema] = Field(default_factory=list, description="关键指标，最多6条")
    research_direction: Optional[str] = Field(None, description="研究方向：看多/偏多/中性/偏空/看空")
    execution_action: Optional[str] = Field(None, description="执行动作：WAIT/ENTER/HOLD/REDUCE/EXIT")
    action_label: Optional[str] = Field(None, description="动作标签：持有/等待触发/条件入场/回避/条件减仓/数据不足观察")

    @field_validator("target_price", "stop_loss_price", mode="before")
    @classmethod
    def _coerce_price(cls, v):
        # LLM 可能返回数组 [34.0, 32.5] 而非单个数字，取第一个
        if isinstance(v, list):
            return v[0] if v else None
        return v


def _report_sections_for_data_blockers(result_data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(result_data, dict):
        return {}
    return {
        "market_report": result_data.get("market_report") or "",
        "volume_price_report": result_data.get("volume_price_report") or "",
        "smart_money_report": result_data.get("smart_money_report") or "",
        "news_report": result_data.get("news_report") or "",
    }


def _raw_evidence_for_data_blockers(result_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(result_data, dict):
        return None
    raw = result_data.get("raw_evidence")
    if isinstance(raw, dict):
        return raw
    metadata = result_data.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("raw_evidence"), dict):
        return metadata["raw_evidence"]
    return None


def build_verified_financial_key_metrics(
    result_data: Optional[Dict[str, Any]],
    fallback_metrics: Optional[List[dict]] = None,
) -> List[dict]:
    """Build report-card metrics only from deterministic normalized facts.

    The LLM extractor is retained as a fallback for legacy reports, but once
    ``financial_period_facts`` exist it must not reinterpret scientific
    notation or invent units.  Missing comparable periods stay explicitly
    missing.
    """
    raw_evidence = _raw_evidence_for_data_blockers(result_data)
    fact_entry = raw_evidence.get("financial_period_facts") if raw_evidence else None
    facts = fact_entry.get("raw") if isinstance(fact_entry, dict) else None
    if not isinstance(facts, list) or not facts:
        return list(fallback_metrics or [])

    from tradingagents.agents.utils.fundamental_integrity import (
        extract_financial_anomaly_inputs,
    )

    values = extract_financial_anomaly_inputs(facts)

    def _latest_fact(metric: str) -> Optional[Dict[str, Any]]:
        candidates = [
            fact
            for fact in facts
            if isinstance(fact, dict)
            and fact.get("metric") == metric
            and isinstance(fact.get("value"), (int, float))
            and fact.get("status", "HAS_DATA") == "HAS_DATA"
        ]
        return max(
            candidates,
            key=lambda fact: str(fact.get("report_date") or ""),
            default=None,
        )

    def _fact_to_yi(fact: Optional[Dict[str, Any]]) -> Optional[float]:
        if not fact:
            return None
        value = float(fact["value"])
        unit = str(fact.get("unit") or "元")
        if unit in {"亿", "亿元"}:
            return value
        if unit in {"万", "万元"}:
            return value / 10000
        if unit == "元":
            return value / 100000000
        return None

    def _percent_status(value: Optional[float], *, good: float, bad: float) -> str:
        if value is None:
            return "bad"
        if value > good:
            return "good"
        if value < bad:
            return "bad"
        return "neutral"

    def _metric(
        name: str,
        value: Optional[float],
        *,
        unit: str,
        status: str,
        decimals: int = 1,
    ) -> dict:
        rendered = "数据缺失" if value is None else f"{value:.{decimals}f}{unit}"
        return {"name": name, "value": rendered, "status": status}

    debt_ratio = values.get("debt_ratio")
    operating_cashflow = values.get("operating_cashflow")
    if operating_cashflow is None:
        operating_cashflow = _fact_to_yi(_latest_fact("operating_cashflow"))
    revenue_growth = values.get("revenue_growth_yoy")
    net_profit_growth = values.get("net_profit_growth_yoy")
    roe = values.get("roe")
    total_assets = values.get("total_assets")
    if total_assets is None:
        total_assets = _fact_to_yi(_latest_fact("total_assets"))

    return [
        _metric(
            "资产负债率",
            debt_ratio,
            unit="%",
            status=(
                "bad" if debt_ratio is not None and debt_ratio > 70
                else "good" if debt_ratio is not None and debt_ratio < 40
                else "neutral" if debt_ratio is not None
                else "bad"
            ),
        ),
        _metric(
            "经营现金流净额",
            operating_cashflow,
            unit="亿元",
            status=(
                "good" if operating_cashflow is not None and operating_cashflow > 0
                else "bad"
            ),
            decimals=2,
        ),
        _metric(
            "营收增速（同比）",
            revenue_growth,
            unit="%",
            status=_percent_status(revenue_growth, good=20, bad=5),
        ),
        _metric(
            "净利润增速（同比）",
            net_profit_growth,
            unit="%",
            status=_percent_status(net_profit_growth, good=20, bad=5),
        ),
        _metric(
            "ROE（报告期）",
            roe,
            unit="%",
            status=_percent_status(roe, good=15, bad=8),
        ),
        _metric(
            "总资产",
            total_assets,
            unit="亿元",
            status="neutral" if total_assets is not None else "bad",
            decimals=2,
        ),
    ]


def attach_report_playbook_contract(
    result_data: Optional[Dict[str, Any]],
    *,
    playbook_stage: Optional[str] = None,
    playbook_contract: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Normalize and persist the optional playbook payload in result_data."""
    if not isinstance(result_data, dict):
        if playbook_stage is None and not playbook_contract:
            return result_data
        result_data = {}

    from tradingagents.tradeflow.playbook_contract import (
        playbook_contract_from_dict,
        playbook_contract_is_empty,
        playbook_contract_summary,
        playbook_contract_to_dict,
        validate_playbook_contract_safety,
    )

    existing = result_data.get("playbook_contract")
    if not isinstance(existing, dict):
        existing = result_data.get("playbook_summary")
    payload = dict(existing) if isinstance(existing, dict) else {}
    if isinstance(playbook_contract, dict):
        payload.update(playbook_contract)
    if playbook_stage is not None:
        payload["playbook_stage"] = playbook_stage

    contract = playbook_contract_from_dict(payload)
    if not validate_playbook_contract_safety(contract):
        raise ValueError("playbook_contract 包含禁止的强动作词")
    if playbook_contract_is_empty(contract):
        return result_data

    enriched = dict(result_data)
    enriched["playbook_stage"] = contract.playbook_stage
    enriched["playbook_contract"] = playbook_contract_to_dict(contract)
    enriched["playbook_summary"] = playbook_contract_summary(contract)
    return enriched


def attach_report_data_blockers(result_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Attach field-level data blockers to report result_data.

    [DATA-021] report_data_blockers
    This function is deliberately read-only with respect to trading logic: it
    only adds explanatory metadata and does not alter action labels or gates.
    """
    if not isinstance(result_data, dict):
        return result_data
    try:
        from tradingagents.agents.utils.readiness_score import (
            build_data_blockers,
            summarize_data_blockers,
        )

        reports = _report_sections_for_data_blockers(result_data)
        raw_evidence = _raw_evidence_for_data_blockers(result_data)
        blockers = build_data_blockers(reports, raw_evidence=raw_evidence)
        enriched = dict(result_data)
        enriched["data_blockers"] = blockers
        enriched["data_blocker_summary"] = summarize_data_blockers(blockers)
        return enriched
    except Exception as exc:
        logger.warning("DATA-021 data blocker attachment failed: %s", exc)
        return result_data


def attach_report_wait_reason_codes(
    result_data: Optional[Dict[str, Any]],
    wait_reason_codes: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Attach [REPORT-UX-003] wait_reason_codes + labels to report result_data.

    Read-only with respect to the strong action gate: only writes the
    explanatory ``wait_reason_codes`` / ``wait_reason_labels`` keys so the UI
    can show *why* the final action is WAIT instead of a flat "数据不足观察".
    """
    if not isinstance(result_data, dict):
        return result_data
    try:
        from tradingagents.graph.signal_processing import WAIT_REASON_LABELS

        enriched = dict(result_data)
        codes = list(wait_reason_codes or enriched.get("wait_reason_codes") or [])
        enriched["wait_reason_codes"] = codes
        enriched["wait_reason_labels"] = {
            code: WAIT_REASON_LABELS.get(code, code) for code in codes
        }
        return enriched
    except Exception as exc:
        logger.warning("REPORT-UX-003 wait_reason_codes attach failed: %s", exc)
        return result_data


def attach_report_local_knowledge(
    result_data: Optional[Dict[str, Any]],
    symbol: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Attach [KB-003] local knowledge block to report result_data.

    Read-only with respect to the strong action gate: only writes the
    explanatory ``local_knowledge_block`` (Markdown) and
    ``local_knowledge_summary`` keys so the UI can show a "本地知识补充"
    section without altering decisions, targets or gates.

    Priority:
      1. Reuse existing ``metadata.raw_evidence.local_knowledge`` payload when
         the data collector already produced one (avoids re-parsing the wiki
         for every report read).
      2. Otherwise re-query by ``symbol`` (for legacy rows that predate KB-003
         or runs without data_collector).
      3. Skip silently when no symbol and no cached entry are available.
    """
    if not isinstance(result_data, dict):
        return result_data
    try:
        from tradingagents.dataflows.local_knowledge_provider import (
            LocalKnowledgeQueryResult,
            STATUS_NORMAL_NO_DATA,
            default_knowledge_root,
            query_local_knowledge,
            render_local_knowledge_block,
        )

        # [REPORT-UX-006] knowledge_evidence_card — honor the KB disabled flag
        # (KNOWLEDGE_CONTEXT_DISABLED / KNOWLEDGE_LOCAL_DISABLED) so the report
        # surfaces a "知识库已禁用" gap instead of issuing a slow/failed query
        # against a KB the operator explicitly turned off. Mirrors the check
        # already used by KB-006 local_knowledge_context_service. Disabled is
        # a *gap reason*, never an action change.
        try:
            from api.services.local_knowledge_context_service import (
                is_local_knowledge_disabled,
            )
            kb_disabled = is_local_knowledge_disabled()
        except Exception:
            kb_disabled = False
        if kb_disabled:
            enriched = dict(result_data)
            enriched["local_knowledge_block"] = None
            enriched["local_knowledge_summary"] = {
                "status": STATUS_NORMAL_NO_DATA,
                "matched_count": 0,
                "confidence": "low",
                "themes": [],
                "symbols": [],
                "updated_at": None,
                "kb_disabled": True,
            }
            return enriched

        # 1. Reuse cached raw_evidence entry.
        cached_entry = _cached_local_knowledge_entry(result_data)
        result_obj: Optional[LocalKnowledgeQueryResult] = None
        if isinstance(cached_entry, dict):
            payload = cached_entry.get("raw")
            if isinstance(payload, dict):
                result_obj = LocalKnowledgeQueryResult.from_dict(payload)

        # 2. Re-query for legacy rows when no usable cache.
        if result_obj is None and symbol:
            resolved_symbol = str(symbol).strip()
            if resolved_symbol:
                try:
                    result_obj = query_local_knowledge(
                        default_knowledge_root(), symbol=resolved_symbol
                    )
                except Exception as exc:
                    logger.warning(
                        "KB-003 local knowledge re-query failed for %s: %s",
                        resolved_symbol,
                        exc,
                    )
                    result_obj = None

        if result_obj is None:
            return result_data

        enriched = dict(result_data)
        enriched["local_knowledge_block"] = render_local_knowledge_block(result_obj)
        enriched["local_knowledge_summary"] = {
            "status": result_obj.status,
            "matched_count": len(result_obj.matched_pages),
            "confidence": result_obj.confidence,
            "themes": list(result_obj.themes)[:10],
            "symbols": list(result_obj.symbols)[:10],
            "updated_at": result_obj.updated_at,
        }

        # [KB-008] research_attention_integration — 在 local_knowledge_summary
        # 之上扩展研究关注度（多研报重复提及因子）。只作为研究优先级/解释信息，
        # 不改变强动作门禁；负面信息（stale / deprecated / 主题拥挤）必须同时透出。
        try:
            from tradingagents.dataflows.research_attention import (
                attention_to_summary as _kb008_to_summary,
                lookup_research_attention as _kb008_lookup,
                render_research_attention_inline as _kb008_render_inline,
            )

            sym_attention = None
            if symbol:
                sym_attention = _kb008_lookup(
                    default_knowledge_root(), str(symbol).strip()
                )
            attention_summary = _kb008_to_summary(sym_attention)
            # 把扁平字段提到 summary 顶层，便于前端无需深挖即可渲染。
            enriched["local_knowledge_summary"].update(attention_summary)
            enriched["research_attention_score"] = attention_summary[
                "research_attention_score"
            ]
            enriched["knowledge_theme_count"] = attention_summary[
                "knowledge_theme_count"
            ]
            enriched["research_attention_summary"] = attention_summary[
                "research_attention_summary"
            ]
            attention_md = _kb008_render_inline(sym_attention)
            enriched["research_attention_block"] = attention_md
            # 把研究关注度段拼到"本地知识补充" markdown 末尾，使 TA 报告渲染
            # 时一段就能展示完整本地知识 + 研报关注度。
            if attention_md:
                base_block = enriched.get("local_knowledge_block") or ""
                enriched["local_knowledge_block"] = (
                    base_block + "\n" + attention_md
                ).strip()
        except Exception as exc:
            logger.warning("KB-008 research attention attach failed: %s", exc)

        return enriched
    except Exception as exc:
        logger.warning("KB-003 local knowledge attachment failed: %s", exc)
        return result_data


def _cached_local_knowledge_entry(
    result_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return ``metadata.raw_evidence.local_knowledge`` if present."""
    metadata = result_data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw_ev = metadata.get("raw_evidence")
    if not isinstance(raw_ev, dict):
        return None
    entry = raw_ev.get("local_knowledge")
    return entry if isinstance(entry, dict) else None


# [HY-004] half_year_report_block
def _cached_half_year_facts_entry(
    result_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return ``metadata.raw_evidence.half_year_facts`` if present.

    Mirrors :func:`_cached_local_knowledge_entry` so the HY-004 attach step can
    reuse the data collector payload instead of re-parsing the wiki on every
    report read.
    """
    metadata = result_data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw_ev = metadata.get("raw_evidence")
    if not isinstance(raw_ev, dict):
        return None
    entry = raw_ev.get("half_year_facts")
    return entry if isinstance(entry, dict) else None


def attach_report_half_year_facts(
    result_data: Optional[Dict[str, Any]],
    symbol: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Attach [HY-004] half-year facts block to report result_data.

    Adds the "半年报事实对照" section to TA reports. Strictly read-only with
    respect to the strong action gate: this function MUST NOT alter
    ``decision`` / ``execution_action`` / ``action_label`` /
    ``research_direction`` / ``wait_reason_codes`` / ``data_blockers``.
    Half-year facts serve as background evidence only and never substitute for
    missing market/fund/news data (see REPORT-UX-004 isolation contract).

    Priority (same shape as :func:`attach_report_local_knowledge`):
      1. Reuse existing ``metadata.raw_evidence.half_year_facts`` payload when
         the data collector already produced one (avoids re-parsing the wiki
         for every report read).
      2. Otherwise re-query by ``symbol`` (for legacy rows that predate HY-004
         or runs without data_collector).
      3. Skip silently when no symbol and no cached entry are available.

    Output keys (all backward-compatible; old reports simply get None/empty):
      - ``half_year_facts_block``: Markdown string. Empty when no half-year
        page hit so the UI can hide the section.
      - ``half_year_facts_summary``: dict with structured fields the frontend
        can render without re-parsing markdown (status / data_status /
        matched_count / latest_period / latest_disclosure_date / sections /
        has_conflict / has_stale / has_opinion_only).
      - ``half_year_facts_status``: short string (``HAS_FACTS`` / ``NO_DATA`` /
        ``STALE`` / ``LOW_CONFIDENCE`` / ``CONFLICT`` / ``FAILED``) for
        quick filtering. ``NO_DATA`` is a *gap*, not a failure — the report
        must still surface the original ``wait_reason_codes``.
    """
    if not isinstance(result_data, dict):
        return result_data
    try:
        from tradingagents.dataflows.half_year_facts_provider import (
            HalfYearFactsQueryResult,
            default_knowledge_root as _hy_default_root,
            query_half_year_facts as _hy_query,
            render_half_year_facts_block as _hy_render,
        )

        # 1. Reuse cached raw_evidence entry (preferred path).
        cached_entry = _cached_half_year_facts_entry(result_data)
        result_obj: Optional[HalfYearFactsQueryResult] = None
        if isinstance(cached_entry, dict):
            payload = cached_entry.get("raw")
            if isinstance(payload, dict):
                result_obj = HalfYearFactsQueryResult.from_dict(payload)

        # 2. Re-query for legacy rows when no usable cache.
        if result_obj is None and symbol:
            resolved_symbol = str(symbol).strip()
            if resolved_symbol:
                try:
                    result_obj = _hy_query(
                        _hy_default_root(), symbol=resolved_symbol
                    )
                except Exception as exc:
                    logger.warning(
                        "HY-004 half_year_facts re-query failed for %s: %s",
                        resolved_symbol,
                        exc,
                    )
                    result_obj = None

        if result_obj is None:
            return result_data

        # Build the short status string for top-level filtering.
        status_short = _half_year_status_to_short(
            result_obj.status,
            result_obj.data_status,
        )

        enriched = dict(result_data)
        enriched["half_year_facts_block"] = _hy_render(result_obj)
        enriched["half_year_facts_summary"] = _build_half_year_facts_summary(
            result_obj
        )
        enriched["half_year_facts_status"] = status_short

        return enriched
    except Exception as exc:
        logger.warning("HY-004 half_year_facts attachment failed: %s", exc)
        return result_data


def _half_year_status_to_short(status: str, data_status: str) -> str:
    """Map the provider status/data_status pair to a short filter token.

    ``NO_DATA`` represents a *gap* (no half-year pages matched) and must NOT
    be confused with ``FAILED`` (provider exception / unreadable KB). This
    distinction is the core of the HY-004 acceptance criterion: "无半年报事实
    时显示缺口，不误判 failed".
    """
    from tradingagents.dataflows.half_year_facts_provider import DATA_CONFLICT
    from tradingagents.dataflows.local_knowledge_provider import (
        STATUS_FAILED,
        STATUS_HAS_DATA,
        STATUS_LOW_CONFIDENCE,
        STATUS_NORMAL_NO_DATA,
        STATUS_STALE,
    )

    if status == STATUS_FAILED:
        return "FAILED"
    if status == STATUS_NORMAL_NO_DATA:
        return "NO_DATA"
    if status == STATUS_STALE:
        return "STALE"
    if status == STATUS_LOW_CONFIDENCE:
        return "LOW_CONFIDENCE"
    if data_status == DATA_CONFLICT:
        return "CONFLICT"
    if status == STATUS_HAS_DATA:
        return "HAS_FACTS"
    return "NO_DATA"


def _build_half_year_facts_summary(
    result: Any,
) -> Dict[str, Any]:
    """Build the structured ``half_year_facts_summary`` dict.

    The summary exposes the four required sub-sections (事实 / 管理层表述 /
    待验证事项 / 风险) so the frontend can render distinct cards without
    re-parsing the markdown block. Each sub-section carries an
    ``available`` flag, a ``count`` and a short ``preview`` list.
    """
    from tradingagents.dataflows.half_year_facts_provider import (
        DATA_CONFLICT,
        DATA_MISSING_FACTS,
        DATA_MISSING_PERIOD,
    )

    pages = list(getattr(result, "pages", []) or [])
    # 事实：聚合 financial_facts raw（每页前 3 条，全局去重保序）。
    facts_preview: List[str] = []
    commentary_preview: List[str] = []
    needs_verification_preview: List[str] = []
    risks_preview: List[str] = []

    has_conflict = False
    has_stale = False
    has_opinion_only = False
    has_missing_period = False
    has_missing_facts = False

    for p in pages:
        if getattr(p, "data_status", None) == DATA_CONFLICT:
            has_conflict = True
        if getattr(p, "is_stale", False):
            has_stale = True
        if getattr(p, "is_opinion_only", False):
            has_opinion_only = True
        if getattr(p, "data_status", None) == DATA_MISSING_PERIOD:
            has_missing_period = True
        if getattr(p, "data_status", None) == DATA_MISSING_FACTS:
            has_missing_facts = True

        # 事实：financial_facts raw。
        for m in (getattr(p, "financial_facts", None) or [])[:3]:
            raw = getattr(m, "raw", None)
            if raw and raw not in facts_preview:
                facts_preview.append(raw)
        # 管理层表述。
        for mc in (getattr(p, "management_commentary", None) or [])[:2]:
            if mc and mc not in commentary_preview:
                commentary_preview.append(mc)
        # 待验证事项：前瞻指引（公司指引，半事实）+ 事实冲突 + 缺字段。
        for fg in (getattr(p, "forward_guidance", None) or [])[:2]:
            if fg and fg not in needs_verification_preview:
                needs_verification_preview.append(fg)
        if getattr(p, "data_status", None) == DATA_CONFLICT and getattr(
            p, "conflict_detail", None
        ):
            cd = p.conflict_detail
            if cd and cd not in needs_verification_preview:
                needs_verification_preview.append(cd)
        missing_fields = list(getattr(p, "missing_fields", None) or [])
        if missing_fields:
            tag = "缺字段需复核：" + ",".join(missing_fields)
            if tag not in needs_verification_preview:
                needs_verification_preview.append(tag)
        # 风险：risk_factors。
        for r in (getattr(p, "risk_factors", None) or [])[:3]:
            if r and r not in risks_preview:
                risks_preview.append(r)

    # 截断 preview 列表，避免 summary 过长。
    facts_preview = facts_preview[:5]
    commentary_preview = commentary_preview[:3]
    needs_verification_preview = needs_verification_preview[:5]
    risks_preview = risks_preview[:6]

    return {
        "status": result.status,
        "data_status": result.data_status,
        "matched_count": len(pages),
        "latest_period": result.latest_period,
        "latest_disclosure_date": result.latest_disclosure_date,
        "vendor": getattr(result, "vendor", None),
        "task": getattr(result, "task", None),
        "updated_at": getattr(result, "pages", None) and any(
            getattr(p, "updated_at", None) for p in pages
        ) and next(
            (getattr(p, "updated_at", None) for p in pages if getattr(p, "updated_at", None)),
            None,
        ),
        "sections": {
            "facts": {
                "available": bool(facts_preview),
                "count": len(facts_preview),
                "preview": facts_preview,
            },
            "management_commentary": {
                "available": bool(commentary_preview),
                "count": len(commentary_preview),
                "preview": commentary_preview,
            },
            "needs_verification": {
                "available": bool(needs_verification_preview),
                "count": len(needs_verification_preview),
                "preview": needs_verification_preview,
            },
            "risks": {
                "available": bool(risks_preview),
                "count": len(risks_preview),
                "preview": risks_preview,
            },
        },
        "has_conflict": has_conflict,
        "has_stale": has_stale,
        "has_opinion_only": has_opinion_only,
        "has_missing_period": has_missing_period,
        "has_missing_facts": has_missing_facts,
    }


# [SCORE-001B] research_score_snapshot_api_adapter
# [SCORE-001B-R1] 新增 trade_date 参数，历史报告按其交易日查询快照。
def attach_report_research_score_snapshot(
    result_data: Optional[Dict[str, Any]],
    symbol: Optional[str] = None,
    trade_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Attach [SCORE-001B] research score snapshot summary to report result_data.

    Strictly read-only with respect to the strong action gate: this function
    MUST NOT alter ``decision`` / ``execution_action`` / ``action_label`` /
    ``research_direction`` / ``wait_reason_codes`` / ``data_blockers``.

    Output keys (all backward-compatible; old reports simply get None):
      - ``research_score_snapshot``: dict with structured snapshot summary
        (status / scores / theses_summary / missing_evidence).

    [SCORE-001B-R1] 参数:
      trade_date: 报告交易日 (YYYY-MM-DD)。历史回放时用于查询快照，
                  确保不使用未来快照。None 时 fallback 到当前时间。
    """
    if not isinstance(result_data, dict):
        return result_data
    try:
        from datetime import datetime, timezone
        from tradingagents.dataflows.research_score_snapshot import (
            STATUS_NORMAL_NO_DATA as _SNAP_NO_DATA,
            default_knowledge_root as _snap_default_root,
            query_research_score_snapshot as _snap_query,
            snapshot_to_api_dict as _snap_to_api,
        )

        if not symbol:
            return result_data

        resolved_symbol = str(symbol).strip()
        if not resolved_symbol:
            return result_data

        knowledge_root = _snap_default_root()
        if not knowledge_root:
            return result_data

        # [SCORE-001B-R1] 按报告交易日查询快照，避免未来数据泄漏。
        analysis_time = _resolve_report_analysis_time(trade_date)

        result = _snap_query(
            knowledge_root,
            symbol=resolved_symbol,
            analysis_time=analysis_time,
        )
        api_dict = _snap_to_api(result)

        # FAILED is an observable data-source state even without a parsed
        # snapshot. Preserve it so callers can distinguish read failure from
        # normal no-data/not-queried.
        if api_dict.get("snapshot") is None and api_dict.get("status") != "FAILED":
            return result_data

        enriched = dict(result_data)
        enriched["research_score_snapshot"] = api_dict
        return enriched
    except Exception as exc:
        logger.warning("SCORE-001B research_score_snapshot attachment failed: %s", exc)
        return result_data


def _resolve_report_analysis_time(trade_date: Optional[str]) -> datetime:
    """[SCORE-001B-R1] 从 trade_date 解析 analysis_time。

    历史报告按其交易日查询快照（as_of <= trade_date），确保不使用未来快照。
    trade_date 格式为 YYYY-MM-DD；解析失败时 fallback 到当前 UTC 时间。
    """
    from datetime import datetime, timezone
    if trade_date:
        try:
            # trade_date 是 YYYY-MM-DD 格式；取当天 15:00 CST (UTC+8) 作为分析时间
            dt = datetime.strptime(trade_date, "%Y-%m-%d")
            from datetime import timedelta
            return dt.replace(hour=15, minute=0, second=0, tzinfo=timezone(timedelta(hours=8)))
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)


def extract_structured_data(
    final_trade_decision: str,
    fundamentals_report: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> Optional[StructuredReport]:
    """Use LLM structured output to extract key data from report text."""
    if not final_trade_decision:
        return None
    if config is None:
        from tradingagents.default_config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG

    try:
        from langchain_core.messages import HumanMessage
        from tradingagents.llm_clients import create_llm_client

        client = create_llm_client(
            provider=config.get("llm_provider", "openai"),
            model=config.get("quick_think_llm", "gpt-4o-mini"),
            base_url=config.get("backend_url"),
            api_key=config.get("api_key"),
        )
        llm = client.get_llm()

        prompt = (
            "请从以下投资分析报告中提取结构化信息，并以 JSON 格式返回。\n\n"
            f"【最终交易决策】\n{final_trade_decision[:3000]}\n\n"
            f"【基本面报告摘要】\n{fundamentals_report[:1000]}\n\n"
            "提取要求（请确保输出为有效的 JSON 对象，不要包裹在 markdown 代码块中）：\n"
            "1. decision：决策方向关键词（BUY/SELL/HOLD 或 增持/减持/持有）\n"
            "2. confidence：整体置信度（0-100整数），若文中未明确给出则根据语气判断\n"
            "3. target_price / stop_loss_price：纯数字，若未提及则为 null\n"
            "4. risks：最多5条主要风险，每条包含名称（15字内）、等级（high/medium/low）、一句话说明\n"
            "5. key_metrics：最多6条关键财务/估值指标，每条包含名称、值（含单位）、优劣（good/neutral/bad）。"
            "**必须严格执行优劣判断，不要全部标neutral**："
            "ROE>15%为good，<8%为bad；营收增速>20%为good，<5%为bad；"
            "资产负债率<40%为good，>70%为bad，60%左右为neutral；"
            "经营现金流净额为正且>净利润50%为good，为负为bad；"
            "净利润为正且增长为good，下降为bad；数据缺失的指标必须标bad并在value中注明'数据缺失'。"
            "每个指标都必须独立判断，至少要有1个good和1个bad（如果数据支持）。"
        )

        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content if hasattr(response, "content") else str(response)
        parsed = json_repair.loads(raw)
        result = StructuredReport(**parsed)
        if result.confidence is not None and not (0 <= result.confidence <= 100):
            result.confidence = None
        return result
    except Exception as e:
        logger.warning(f"LLM structured extraction failed: {e}")
        if 'raw' in locals():
            logger.warning(f"Raw LLM output:\n{raw}")
        return None


# ─── Fallback regex extraction (used when LLM extraction unavailable) ─────────

_GENERATED_QUALITY_SECTION_RE = re.compile(r"\n?###?\s*执行质检[\s\S]*$", re.IGNORECASE)
_SYSTEM_DIAGNOSTICS_MARKER = "<!-- TA_SYSTEM_DIAGNOSTICS_START -->"
_LIST_MARKER_AFTER_NUMBER_RE = re.compile(r"\s*[.)、]\s*(?:[A-Z0-9一二三四五六七八九十]|[*-])")


def _strip_generated_quality_section(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    return _GENERATED_QUALITY_SECTION_RE.sub("", text)


def _model_authored_report_text(
    text: Optional[str],
    trusted_diagnostics_offset: Optional[int] = None,
) -> str:
    """Return model text before a validated application diagnostics block."""
    source = text or ""
    if trusted_diagnostics_offset is not None:
        if (
            isinstance(trusted_diagnostics_offset, int)
            and not isinstance(trusted_diagnostics_offset, bool)
            and 0 <= trusted_diagnostics_offset <= len(source)
        ):
            tail = source[trusted_diagnostics_offset:]
            if tail.startswith(_SYSTEM_DIAGNOSTICS_MARKER):
                quality = re.search(r"(?m)^#{1,6}\s*执行质检\s*$", tail)
                summary = re.search(r"(?m)^#{1,6}\s*系统执行结论\s*$", tail)
                if quality and summary and quality.start() < summary.start():
                    return source[:trusted_diagnostics_offset].rstrip()
        # When metadata claims an application boundary but it is invalid, do
        # not fall back to a copied marker inside model or historical text.
        return source
    marker_matches = list(re.finditer(re.escape(_SYSTEM_DIAGNOSTICS_MARKER), source))
    for marker in reversed(marker_matches):
        tail = source[marker.end():]
        quality = re.search(r"(?m)^#{1,6}\s*执行质检\s*$", tail)
        summary = re.search(r"(?m)^#{1,6}\s*系统执行结论\s*$", tail)
        if quality and summary and quality.start() < summary.start():
            return source[:marker.start()].rstrip()
    return source


def _is_likely_list_marker(text: str, end_index: int) -> bool:
    return bool(_LIST_MARKER_AFTER_NUMBER_RE.match(text[end_index : end_index + 16]))

def _extract_confidence_regex(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    for pattern in (r'置信度[:：]\s*(\d+)%', r'confidence[:：]\s*(\d+)%'):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            v = int(m.group(1))
            return v if 0 <= v <= 100 else None
    return None


def _extract_price_regex(
    text: Optional[str],
    price_type: str = "target",
    *,
    include_tactical: bool = False,
) -> Optional[float]:
    if not text:
        return None
    if price_type == "target":
        patterns = [
            r'目标价[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
            r'目标价格[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
            r'目标位[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
            r'止盈位[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
            r'target[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
        ]
        if include_tactical:
            patterns.extend([
                r'第一目标[:：]\s*[^\d\n]{0,30}(\d+\.?\d*)',
                r'第二目标[:：]\s*[^\d\n]{0,30}(\d+\.?\d*)',
                r'止盈[/／、和]?减仓条件[\s\S]{0,140}?(?:第一目标|目标)[^\d]{0,30}(\d+\.?\d*)',
                r'止盈(?:区间|条件)?[:：]\s*[^\d\n]{0,30}(\d+\.?\d*)',
                r'股价(?:回落)?(?:至|到)\s*[¥$]?(\d+\.?\d*)\s*元[^\n。；]{0,30}(?:减仓|止盈|目标)',
            ])
    else:
        patterns = [
            r'(?:最终)?止损(?:价|价格|位)?[^\d\n。！？；;]{0,24}'
            r'(?:仍为|维持(?:在|为)?|调整为|改为|设为)\s*'
            r'[^\d\n。！？；;]{0,6}(\d+\.?\d*)',
            r'止损价[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
            r'止损价格[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
            r'止损位[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
            r'stop[-\s_]?loss[:：][^\d\n。！？；;]{0,30}(\d+\.?\d*)',
        ]
        if include_tactical:
            patterns.extend([
                r'止损触发[:：]\s*[^\d\n]{0,50}(\d+\.?\d*)',
                r'股价[^\n。；]{0,20}跌破\s*[¥$]?(\d+\.?\d*)\s*元[^\n。；]{0,40}(?:止损|离场|减仓)',
                r'跌破\s*[¥$]?(\d+\.?\d*)\s*元[^\n。；]{0,40}(?:止损|离场|减仓)',
            ])
    if price_type == "stop_loss":
        candidates: list[tuple[int, float]] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                if _is_likely_list_marker(text, match.end(1)):
                    continue
                if re.match(
                    r"\s*(?:[%％]|个百分点|个百分比点|个点)",
                    text[match.end(1):match.end(1) + 12],
                ):
                    continue
                candidates.append((match.start(1), float(match.group(1))))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            if _is_likely_list_marker(text, m.end(1)):
                continue
            return float(m.group(1))
    return None


def _extract_price_from_sections(
    sections: Iterable[Optional[str]],
    price_type: Literal["target", "stop_loss"],
) -> Optional[float]:
    """Extract an actionable price across report sections.

    Final risk reports may intentionally show "目标价：—" for a current watch
    stance while upstream manager/trader sections still contain conditional
    execution prices. Use strict labels first, then broader tactical labels.
    """
    texts = [text for text in (_strip_generated_quality_section(text) for text in sections) if text]
    for text in texts:
        price = _extract_price_regex(text, price_type)
        if price is not None:
            return price
    for text in texts:
        price = _extract_price_regex(text, price_type, include_tactical=True)
        if price is not None:
            return price
    return None


def _extract_verdict(text: Optional[str]) -> Optional[Dict[str, str]]:
    if not text:
        return None
    match = re.search(r"<!--\s*VERDICT:\s*(\{.*?\})\s*-->", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    try:
        # Clean potential newlines or invisible characters common in LLM outputs
        raw_json = match.group(1).strip().replace('\n', ' ').replace('\r', ' ')
        payload = json.loads(raw_json)
    except Exception:
        return None
    direction = str(payload.get("direction") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not direction:
        return None
    return {"direction": direction, "reason": reason}


def resolve_report_fields(
    result_data: Optional[Dict[str, Any]] = None,
    confidence_override: Optional[int] = None,
    target_price_override: Optional[float] = None,
    stop_loss_override: Optional[float] = None,
    *,
    has_position: Optional[bool] = None,
) -> Dict[str, Any]:
    """Resolve the final structured fields once for both SSE payloads and DB writes."""
    market_report = sentiment_report = news_report = None
    fundamentals_report = macro_report = smart_money_report = volume_price_report = game_theory_report = None
    investment_plan = trader_investment_plan = None
    final_trade_decision = None

    if result_data:
        market_report = result_data.get("market_report")
        sentiment_report = result_data.get("sentiment_report")
        news_report = result_data.get("news_report")
        fundamentals_report = result_data.get("fundamentals_report")
        macro_report = result_data.get("macro_report")
        smart_money_report = result_data.get("smart_money_report")
        volume_price_report = result_data.get("volume_price_report")
        game_theory_report = result_data.get("game_theory_report")
        investment_plan = result_data.get("investment_plan")
        trader_investment_plan = result_data.get("trader_investment_plan")
        final_trade_decision = result_data.get("final_trade_decision")

    trusted_diagnostics_offset: Optional[int] = None
    if isinstance(result_data, dict):
        metadata = result_data.get("metadata")
        if isinstance(metadata, dict) and "system_diagnostics_offset" in metadata:
            trusted_diagnostics_offset = metadata.get("system_diagnostics_offset")
    model_authored_decision = _model_authored_report_text(
        final_trade_decision,
        trusted_diagnostics_offset,
    )

    verdict = _extract_verdict(final_trade_decision)
    direction = verdict["direction"] if verdict else None

    confidence = confidence_override if confidence_override is not None else _extract_confidence_regex(final_trade_decision)

    price_sections = (
        final_trade_decision,
        trader_investment_plan,
        investment_plan,
        volume_price_report,
        market_report,
    )
    target_price = (
        target_price_override
        if target_price_override is not None
        else _extract_price_from_sections(price_sections, "target")
    )

    stop_loss_price = (
        stop_loss_override
        if stop_loss_override is not None
        else _extract_price_from_sections(price_sections, "stop_loss")
    )
    if final_trade_decision and re.search(
        r"待定|不适用|取消|撤销|未设置|未给出|无法确定|暂无|"
        r"不(?:再)?(?:设置|设定|设|采用|使用|执行)|(?:^|[：:，,；;。\s])无(?:$|[，,；;。\s])|"
        r"not applicable|cancelled|canceled|withdrawn|unset|not set|\bTBD\b|\bN/?A\b|\bnone\b",
        model_authored_decision,
        re.IGNORECASE,
    ):
        # The final decision is authoritative. An explicit withdrawal must not
        # leak an older trader/manager stop into API fields or the UI. Preserve
        # the legacy dash-only convention, which denotes an omitted display
        # value rather than an explicit cancellation of an upstream risk level.
        from tradingagents.agents.utils.trade_setup import _field_explicitly_invalidated

        if _field_explicitly_invalidated(
            model_authored_decision,
            (
                "止损价", "止损位", "初始止损", "硬性止损", "止损红线",
                "止损线", "止损", "失效价", "失效位", "防守位",
                "Stop-loss price", "Stop loss price", "Stop-loss", "Stop loss",
            ),
        ):
            stop_loss_price = None

    research_direction = str(result_data.get("research_direction") or "") if result_data else ""
    execution_action = str(result_data.get("execution_action") or "") if result_data else ""
    action_label = str(result_data.get("action_label") or "") if result_data else ""
    has_resolved_semantics = bool(research_direction and execution_action and action_label)
    # [REPORT-UX-003] wait_reason_codes — data_blockers may already be attached
    # upstream (create_report calls attach_report_data_blockers first). When
    # absent, we still try to compute codes from text-only signals.
    data_blockers_for_reason = (
        result_data.get("data_blockers") if isinstance(result_data, dict) else None
    )
    existing_wait_reason_codes = (
        result_data.get("wait_reason_codes") if isinstance(result_data, dict) else None
    )
    if final_trade_decision and not has_resolved_semantics:
        try:
            from tradingagents.graph.signal_processing import _extract_decision_semantics
            semantics = _extract_decision_semantics(
                final_trade_decision,
                has_position=has_position,
                trigger_price=target_price,
                invalid_price=stop_loss_price,
                data_blockers=data_blockers_for_reason,
            )
            research_direction = semantics.research_direction
            execution_action = semantics.execution_action
            action_label = semantics.action_label
            if semantics.wait_reason_codes:
                existing_wait_reason_codes = list(semantics.wait_reason_codes)
        except Exception:
            pass
    research_direction = research_direction or None
    execution_action = execution_action or None
    action_label = action_label or None

    # A bearish WAIT/avoid conclusion must not expose an upstream bullish
    # target as if it were an executable objective.  Neutral/bullish WAIT may
    # still use target_price as a trigger level.
    if (
        execution_action == "WAIT"
        and (
            research_direction in {"偏空", "看空"}
            or action_label in {"回避", "禁止买入"}
        )
    ):
        target_price = None

    # [REPORT-UX-003] wait_reason_codes — recompute when semantics were already
    # resolved but codes are missing (e.g. legacy rows read back from DB, or
    # semantics supplied via result_data without codes). Keeps the strong action
    # gate intact: only adds explanatory metadata.
    wait_reason_codes = existing_wait_reason_codes
    if wait_reason_codes is None and execution_action == "WAIT":
        try:
            from tradingagents.graph.signal_processing import (
                compute_wait_reason_codes,
                _has_gate_failure,
                _is_data_insufficient,
                _text_has_conflict,
            )

            wait_reason_codes = compute_wait_reason_codes(
                execution_action=execution_action,
                research_direction=research_direction or "中性",
                action_label=action_label,
                has_position=has_position,
                trigger_price=target_price,
                gate_blocked=bool(final_trade_decision and _has_gate_failure(final_trade_decision)),
                data_insufficient=bool(final_trade_decision and _is_data_insufficient(final_trade_decision)),
                data_blockers=data_blockers_for_reason,
                has_conflict=bool(final_trade_decision and _text_has_conflict(final_trade_decision)),
            )
        except Exception:
            wait_reason_codes = []
    elif wait_reason_codes is None:
        wait_reason_codes = []

    return {
        "market_report": market_report,
        "sentiment_report": sentiment_report,
        "news_report": news_report,
        "fundamentals_report": fundamentals_report,
        "macro_report": macro_report,
        "smart_money_report": smart_money_report,
        "volume_price_report": volume_price_report,
        "game_theory_report": game_theory_report,
        "investment_plan": investment_plan,
        "trader_investment_plan": trader_investment_plan,
        "final_trade_decision": final_trade_decision,
        "direction": direction,
        "confidence": confidence,
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
        "research_direction": research_direction,
        "execution_action": execution_action,
        "action_label": action_label,
        # [REPORT-UX-003] wait_reason_codes
        "wait_reason_codes": list(wait_reason_codes or []),
    }


# ─── CRUD ────────────────────────────────────────────────────────────────────

def init_report(
    db: Session,
    report_id: str,
    symbol: str,
    trade_date: str,
    user_id: Optional[str] = None,
) -> ReportDB:
    """Create a pending report record when a job is submitted."""
    now = datetime.now(timezone.utc)
    db_report = ReportDB(
        id=report_id,
        user_id=user_id,
        symbol=symbol,
        trade_date=trade_date,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(db_report)
    db.commit()
    db.refresh(db_report)
    return db_report


def update_report_partial(
    db: Session,
    report_id: str,
    status: Optional[str] = None,
    **fields: Any
) -> Optional[ReportDB]:
    """Update specific fields of an existing report (e.g., partial analyst reports)."""
    db_report = db.query(ReportDB).filter(ReportDB.id == report_id).first()
    if not db_report:
        return None
    
    if status:
        db_report.status = status
    
    for key, value in fields.items():
        if hasattr(db_report, key):
            setattr(db_report, key, value)
    
    db_report.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_report)
    return db_report


def finalize_orphan_report(
    db: Session,
    report: ReportDB,
    *,
    error_message: str = STALE_REPORT_ERROR_MESSAGE,
) -> ReportDB:
    """Mark an orphaned pending/running report as failed."""
    if str(report.status or "") not in ACTIVE_REPORT_STATUSES:
        return report

    report.status = "failed"
    report.error = error_message
    report.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(report)
    return report


def recover_stale_active_reports(
    db: Session,
    *,
    active_job_ids: Optional[Iterable[str]] = None,
    error_message: str = STALE_REPORT_ERROR_MESSAGE,
) -> Dict[str, int]:
    """Recover stale pending/running reports left behind by interrupted jobs."""
    active_job_id_set = {str(job_id) for job_id in (active_job_ids or []) if str(job_id).strip()}
    rows = (
        db.query(ReportDB)
        .filter(ReportDB.status.in_(ACTIVE_REPORT_STATUSES))
        .all()
    )
    if not rows:
        return {"total": 0, "completed": 0, "failed": 0}

    failed = 0
    changed = False
    now = datetime.now(timezone.utc)
    for row in rows:
        if str(row.id) in active_job_id_set:
            continue
        row.status = "failed"
        row.error = error_message
        row.updated_at = now
        changed = True
        failed += 1

    if changed:
        db.commit()

    return {
        "total": failed,
        "completed": 0,
        "failed": failed,
    }


def mark_report_failed(
    db: Session,
    report_id: str,
    error_message: str
) -> Optional[ReportDB]:
    """Mark a report as failed with an error message."""
    return update_report_partial(db, report_id, status="failed", error=error_message)


def create_report(
    db: Session,
    symbol: str,
    trade_date: str,
    decision: Optional[str] = None,
    result_data: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    risk_items: Optional[List[dict]] = None,
    key_metrics: Optional[List[dict]] = None,
    analyst_traces: Optional[List[dict]] = None,
    confidence_override: Optional[int] = None,
    target_price_override: Optional[float] = None,
    stop_loss_override: Optional[float] = None,
    report_id: Optional[str] = None,  # If provided, update existing
    playbook_stage: Optional[str] = None,  # [PLAYBOOK-001]
    playbook_contract: Optional[Dict[str, Any]] = None,  # [PLAYBOOK-001]
) -> ReportDB:
    """Create or finalize a report."""
    result_data = attach_report_playbook_contract(
        result_data,
        playbook_stage=playbook_stage,
        playbook_contract=playbook_contract,
    )
    result_data = attach_report_data_blockers(result_data)
    key_metrics = build_verified_financial_key_metrics(result_data, key_metrics)
    if isinstance(result_data, dict):
        result_data = dict(result_data)
        result_data["verified_financial_key_metrics"] = list(key_metrics)
    resolved = resolve_report_fields(
        result_data=result_data,
        confidence_override=confidence_override,
        target_price_override=target_price_override,
        stop_loss_override=stop_loss_override,
    )
    # [REPORT-UX-003] wait_reason_codes — store into result_data so the UI can
    # show *why* the final action is WAIT without altering the strong gate.
    result_data = attach_report_wait_reason_codes(
        result_data, wait_reason_codes=resolved.get("wait_reason_codes")
    )
    # [KB-003] local_knowledge_raw_evidence — render "本地知识补充" block.
    # Reuses metadata.raw_evidence.local_knowledge when present; otherwise
    # re-queries by symbol. Read-only with respect to the strong action gate.
    result_data = attach_report_local_knowledge(result_data, symbol=symbol)
    # [HY-004] half_year_report_block — render "半年报事实对照" block.
    # Reuses metadata.raw_evidence.half_year_facts when present; otherwise
    # re-queries by symbol. Background evidence only — must NOT alter the
    # strong action gate or mask data_blockers / wait_reason_codes.
    result_data = attach_report_half_year_facts(result_data, symbol=symbol)
    # [SCORE-001B] research_score_snapshot_api_adapter — attach snapshot summary.
    # Background evidence only — must NOT alter the strong action gate.
    # [SCORE-001B-R1] 传入 trade_date，历史报告按其交易日查询快照。
    result_data = attach_report_research_score_snapshot(
        result_data, symbol=symbol, trade_date=trade_date,
    )

    now = datetime.now(timezone.utc)
    
    # Check if we should update an existing record (initialized via init_report)
    db_report = None
    if report_id:
        db_report = db.query(ReportDB).filter(ReportDB.id == report_id).first()

    if db_report:
        # Update existing
        db_report.status = "completed"
        # [UPSTREAM-081-002] A report may previously have been marked failed by
        # an older worker or timeout policy (e.g. the legacy watchdog flipped
        # long analyses to failed while the workflow was still running).
        # Successful finalisation is authoritative: clear the stale error so a
        # completed report never carries a failure message.
        db_report.error = None
        db_report.decision = decision
        db_report.direction = resolved["direction"]
        db_report.research_direction = resolved["research_direction"]
        db_report.execution_action = resolved["execution_action"]
        db_report.action_label = resolved["action_label"]
        db_report.confidence = resolved["confidence"]
        db_report.target_price = resolved["target_price"]
        db_report.stop_loss_price = resolved["stop_loss_price"]
        db_report.result_data = result_data
        db_report.risk_items = risk_items
        db_report.key_metrics = key_metrics
        db_report.analyst_traces = analyst_traces
        db_report.market_report = resolved["market_report"]
        db_report.sentiment_report = resolved["sentiment_report"]
        db_report.news_report = resolved["news_report"]
        db_report.fundamentals_report = resolved["fundamentals_report"]
        db_report.macro_report = resolved["macro_report"]
        db_report.smart_money_report = resolved["smart_money_report"]
        db_report.volume_price_report = resolved["volume_price_report"]
        db_report.game_theory_report = resolved["game_theory_report"]
        db_report.investment_plan = resolved["investment_plan"]
        db_report.trader_investment_plan = resolved["trader_investment_plan"]
        db_report.final_trade_decision = resolved["final_trade_decision"]
        db_report.updated_at = now
    else:
        # Create new
        db_report = ReportDB(
            id=report_id or str(uuid4()),
            user_id=user_id,
            symbol=symbol,
            trade_date=trade_date,
            status="completed",
            decision=decision,
            direction=resolved["direction"],
            research_direction=resolved["research_direction"],
            execution_action=resolved["execution_action"],
            action_label=resolved["action_label"],
            confidence=resolved["confidence"],
            target_price=resolved["target_price"],
            stop_loss_price=resolved["stop_loss_price"],
            result_data=result_data,
            risk_items=risk_items,
            key_metrics=key_metrics,
            analyst_traces=analyst_traces,
            market_report=resolved["market_report"],
            sentiment_report=resolved["sentiment_report"],
            news_report=resolved["news_report"],
            fundamentals_report=resolved["fundamentals_report"],
            macro_report=resolved["macro_report"],
            smart_money_report=resolved["smart_money_report"],
            volume_price_report=resolved["volume_price_report"],
            game_theory_report=resolved["game_theory_report"],
            investment_plan=resolved["investment_plan"],
            trader_investment_plan=resolved["trader_investment_plan"],
            final_trade_decision=resolved["final_trade_decision"],
            created_at=now,
            updated_at=now,
        )
        db.add(db_report)

    db.commit()
    db.refresh(db_report)
    return db_report


def get_report(db: Session, report_id: str, user_id: Optional[str] = None) -> Optional[ReportDB]:
    query = db.query(ReportDB).filter(ReportDB.id == report_id)
    if user_id:
        query = query.filter(ReportDB.user_id == user_id)
    return normalize_report_action_label(query.first())


def get_reports_by_user(
    db: Session,
    user_id: Optional[str] = None,
    symbol: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[ReportDB]:
    query = db.query(ReportDB).options(load_only(*REPORT_SUMMARY_COLUMNS))
    if user_id:
        query = query.filter(ReportDB.user_id == user_id)
    if symbol:
        query = query.filter(ReportDB.symbol == symbol)
    reports = query.order_by(ReportDB.created_at.desc()).offset(skip).limit(limit).all()
    return [normalize_report_action_label(report) for report in reports]


def get_latest_reports_by_symbols(
    db: Session,
    symbols: List[str],
    user_id: Optional[str] = None,
) -> List[ReportDB]:
    normalized_symbols = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    if not normalized_symbols:
        return []

    query = db.query(ReportDB).options(load_only(*REPORT_SUMMARY_COLUMNS))
    if user_id:
        query = query.filter(ReportDB.user_id == user_id)

    rows = (
        query.filter(ReportDB.symbol.in_(normalized_symbols))
        .order_by(ReportDB.symbol.asc(), ReportDB.created_at.desc())
        .all()
    )

    latest_by_symbol: dict[str, ReportDB] = {}
    for row in rows:
        symbol = str(row.symbol or "").upper()
        if symbol and symbol not in latest_by_symbol:
            latest_by_symbol[symbol] = row

    return [
        normalize_report_action_label(latest_by_symbol[symbol])
        for symbol in normalized_symbols
        if symbol in latest_by_symbol
    ]


def count_reports(
    db: Session,
    user_id: Optional[str] = None,
    symbol: Optional[str] = None,
) -> int:
    query = db.query(func.count(ReportDB.id))
    if user_id:
        query = query.filter(ReportDB.user_id == user_id)
    if symbol:
        query = query.filter(ReportDB.symbol == symbol)
    return query.scalar() or 0


def delete_report(db: Session, report_id: str, user_id: Optional[str] = None) -> bool:
    query = db.query(ReportDB).filter(ReportDB.id == report_id)
    if user_id:
        query = query.filter(ReportDB.user_id == user_id)
    report = query.first()
    if report:
        db.delete(report)
        db.commit()
        return True
    return False


def batch_delete_reports(db: Session, report_ids: Iterable[str], user_id: Optional[str] = None) -> dict:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for raw_report_id in report_ids:
        report_id = str(raw_report_id or "").strip()
        if not report_id or report_id in seen:
            continue
        seen.add(report_id)
        normalized_ids.append(report_id)

    if not normalized_ids:
        raise ValueError("请至少选择 1 份报告")

    query = db.query(ReportDB).filter(ReportDB.id.in_(normalized_ids))
    if user_id:
        query = query.filter(ReportDB.user_id == user_id)

    rows = query.all()
    row_by_id = {str(row.id): row for row in rows}
    deleted_ids: list[str] = []
    missing_ids: list[str] = []

    for report_id in normalized_ids:
        row = row_by_id.get(report_id)
        if row is None:
            missing_ids.append(report_id)
            continue
        db.delete(row)
        deleted_ids.append(report_id)

    if deleted_ids:
        db.commit()

    return {
        "deleted_ids": deleted_ids,
        "missing_ids": missing_ids,
    }
