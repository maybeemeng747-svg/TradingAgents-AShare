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
    """Normalize stale DECISION labels on read without mutating the database.

    Older rows may have action_label="数据不足观察" even when the final
    research_direction was directional. Keep true neutral insufficient-data
    cases unchanged, but recover useful labels for directional WAIT reports.
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
    normalized_label = None
    if label == "数据不足观察" and action == "WAIT":
        if direction in ("偏空", "看空"):
            normalized_label = "回避"
        elif direction in ("偏多", "看多"):
            normalized_label = "等待触发"
    if normalized_label:
        if isinstance(report, ReportDB):
            set_committed_value(report, "action_label", normalized_label)
            if isinstance(result_data, dict) and result_action_label == "数据不足观察":
                normalized_result_data = dict(result_data)
                normalized_result_data["action_label"] = normalized_label
                set_committed_value(report, "result_data", normalized_result_data)
        else:
            setattr(report, "action_label", normalized_label)
            if isinstance(result_data, dict) and result_action_label == "数据不足观察":
                normalized_result_data = dict(result_data)
                normalized_result_data["action_label"] = normalized_label
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
_LIST_MARKER_AFTER_NUMBER_RE = re.compile(r"\s*[.)、]\s*(?:[A-Z0-9一二三四五六七八九十]|[*-])")


def _strip_generated_quality_section(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    return _GENERATED_QUALITY_SECTION_RE.sub("", text)


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
            r'目标价[:：][^\d\n]{0,30}(\d+\.?\d*)',
            r'目标价格[:：][^\d\n]{0,30}(\d+\.?\d*)',
            r'目标位[:：][^\d\n]{0,30}(\d+\.?\d*)',
            r'止盈位[:：][^\d\n]{0,30}(\d+\.?\d*)',
            r'target[:：][^\d\n]{0,30}(\d+\.?\d*)',
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
            r'止损价[:：][^\d\n]{0,30}(\d+\.?\d*)',
            r'止损价格[:：][^\d\n]{0,30}(\d+\.?\d*)',
            r'止损位[:：][^\d\n]{0,30}(\d+\.?\d*)',
            r'stop[-\s_]?loss[:：][^\d\n]{0,30}(\d+\.?\d*)',
        ]
        if include_tactical:
            patterns.extend([
                r'止损触发[:：]\s*[^\d\n]{0,50}(\d+\.?\d*)',
                r'股价[^\n。；]{0,20}跌破\s*[¥$]?(\d+\.?\d*)\s*元[^\n。；]{0,40}(?:止损|离场|减仓)',
                r'跌破\s*[¥$]?(\d+\.?\d*)\s*元[^\n。；]{0,40}(?:止损|离场|减仓)',
            ])
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

    research_direction = str(result_data.get("research_direction") or "") if result_data else ""
    execution_action = str(result_data.get("execution_action") or "") if result_data else ""
    action_label = str(result_data.get("action_label") or "") if result_data else ""
    has_resolved_semantics = bool(research_direction and execution_action and action_label)
    if final_trade_decision and not has_resolved_semantics:
        try:
            from tradingagents.graph.signal_processing import _extract_decision_semantics
            semantics = _extract_decision_semantics(
                final_trade_decision,
                has_position=has_position,
                trigger_price=target_price,
                invalid_price=stop_loss_price,
            )
            research_direction = semantics.research_direction
            execution_action = semantics.execution_action
            action_label = semantics.action_label
        except Exception:
            pass
    research_direction = research_direction or None
    execution_action = execution_action or None
    action_label = action_label or None

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
) -> ReportDB:
    """Create or finalize a report."""
    result_data = attach_report_data_blockers(result_data)
    resolved = resolve_report_fields(
        result_data=result_data,
        confidence_override=confidence_override,
        target_price_override=target_price_override,
        stop_loss_override=stop_loss_override,
    )

    now = datetime.now(timezone.utc)
    
    # Check if we should update an existing record (initialized via init_report)
    db_report = None
    if report_id:
        db_report = db.query(ReportDB).filter(ReportDB.id == report_id).first()

    if db_report:
        # Update existing
        db_report.status = "completed"
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
