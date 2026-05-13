import time
import json
import logging
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.agents.utils.agent_states import current_tracker_var
from tradingagents.agents.utils.context_utils import build_agent_context_view
from tradingagents.agents.utils.trade_setup import (
    build_trade_quality_check,
    format_trade_quality_check,
)
from tradingagents.agents.utils.debate_utils import (
    extract_risk_judge_result,
    format_claim_subset_for_prompt,
    format_claims_for_prompt,
    safe_int,
)
from tradingagents.agents.utils.delta_check import check_delta, save_conclusion, format_delta_warning
from tradingagents.agents.utils.event_risk_gate import check_event_risk, format_event_risk_warning
from tradingagents.agents.utils.financial_validator import check_financial_anomalies, format_financial_anomaly_warning
from tradingagents.agents.utils.readiness_score import (
    calculate_data_completeness,
    calculate_source_coverage,
    calculate_evidence_coverage,
    EvidenceStatus,
    assess_confidence,
    generate_readiness_score,
    format_readiness_score,
    get_position_status,
    get_strong_action_gate,
    calculate_risk_level,
    calculate_buy_level,
    calculate_opportunity_score,
    format_execution_block,
    sanitize_forbidden_strong_actions,
    infer_evidence_statuses,
    extract_execution_signals,
    _split_llm_body_and_system_blocks,
    ConfidenceLevel,
    validate_stock_name,
)

_logger = logging.getLogger(__name__)

_STRONG_BUY_KEYWORDS = [
    '强买', '重仓买入', '立即买入', '立刻买入', '强烈买入', '满仓买入',
    '重仓布局', '强力买入',
    '建议加仓', '执行加仓', '加仓买入', '可以加仓', '应该加仓', '考虑加仓',
    '建议追涨', '可以追涨', '追涨买入',
]
_STRONG_SELL_KEYWORDS = [
    '立即清仓', '立刻清仓', '强制清仓', '清仓离场', '清仓出局', '全部卖出离场',
]


def create_risk_manager(llm, memory):
    async def risk_manager_node(state) -> dict:
        # [C-001] position_validation_gate
        user_context = state.get('user_context', {})
        current_position = user_context.get('current_position', 0)
        has_position = current_position is not None and current_position > 0

        company_name = state["company_of_interest"]

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        market_research_report = state["market_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        sentiment_report = state["sentiment_report"]
        trader_plan = state["trader_investment_plan"]
        risk_feedback_state = state.get("risk_feedback_state", {})

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        context_view = build_agent_context_view(state, "risk")
        claims = risk_debate_state.get("claims", [])
        unresolved_claim_ids = risk_debate_state.get("unresolved_claim_ids", [])
        prompt = get_prompt("risk_manager_prompt", config=get_config()).format(
            trader_plan=trader_plan,
            past_memory_str=past_memory_str,
            history=history,
            market_context_summary=context_view["market_context_summary"],
            user_context_summary=context_view["user_context_summary"],
            claims_text=format_claims_for_prompt(claims, empty_message="当前没有已登记风控 claim。"),
            unresolved_claims_text=format_claim_subset_for_prompt(claims, unresolved_claim_ids),
            round_summary=risk_debate_state.get("round_summary", "暂无风险轮次摘要。"),
        )

        # ── 流式输出 ──
        tracker = current_tracker_var.get()
        full_content = ""
        async for chunk in llm.astream(prompt):
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += content
            if tracker:
                tracker.emit_debate_token(
                    debate="risk", agent="Portfolio Manager",
                    round_num=-1, token=content,
                )

        judge_result = extract_risk_judge_result(full_content)
        cleaned_response = judge_result["cleaned_response"]
        verdict = judge_result["verdict"]
        hard_constraints = judge_result["hard_constraints"]
        soft_constraints = judge_result["soft_constraints"]
        execution_preconditions = judge_result["execution_preconditions"]
        de_risk_triggers = judge_result["de_risk_triggers"]
        revision_reason = judge_result["revision_reason"]
        trade_quality_check = build_trade_quality_check(
            investment_plan=state.get("investment_plan", ""),
            trader_plan=trader_plan,
            final_decision=cleaned_response,
            user_context=state.get("user_context", {}),
        )
        final_response = cleaned_response + "\n\n" + format_trade_quality_check(trade_quality_check)

        # [C-001] position_validation_gate — 校验输出动作
        if not has_position:
            reduce_keywords = ['减仓', '清仓', '止损', '止盈', '卖出', 'SELL', 'EXIT', 'REDUCE']
            if any(kw in final_response for kw in reduce_keywords):
                final_response += "\n\n⚠️ [C-001] 未持仓状态，已将减仓/清仓建议自动转换为观望（WAIT）。"
                _logger.warning("[C-001] position_validation_gate: 未持仓但输出了减仓/清仓建议，已自动转换")

        # [C-005] delta_check — 检测结论是否翻转
        stock_code = state.get("ticker", company_name)
        delta_info = check_delta(stock_code, final_response, ["risk_manager"])
        if delta_info is not None:
            final_response += format_delta_warning(delta_info)
            _logger.warning("[C-005] delta_check: %s 结论翻转, possible_noise=%s", stock_code, delta_info["possible_noise"])
        save_conclusion(stock_code, final_response, "medium", ["risk_manager"])

        # [C-007] event_risk_gate — 检查重大风险事件
        event_risk_info = check_event_risk(stock_code)
        if event_risk_info["has_risk"]:
            final_response += format_event_risk_warning(event_risk_info)
            _logger.warning("[C-007] event_risk_gate: %s 检测到风险事件: %s", stock_code, event_risk_info["risk_events"])

        # [C-006] financial_validator — 检测财报数据异常
        financial_anomaly_info = check_financial_anomalies(stock_code)
        if financial_anomaly_info["has_anomaly"]:
            final_response += format_financial_anomaly_warning(financial_anomaly_info)
            _logger.warning("[C-006] financial_validator: %s 检测到异常: %s", stock_code, financial_anomaly_info["anomalies"])

        # [C-008] readiness_score — 生成报告质量评分
        has_market = bool(market_research_report)
        has_sentiment = bool(sentiment_report)
        has_news = bool(news_report)
        has_fundamentals = bool(fundamentals_report)
        has_smart_money = bool(state.get("smart_money_report", ""))
        has_volume_price = bool(state.get("volume_price_report", ""))
        has_user_context = bool(state.get("user_context"))
        has_position_data = user_context.get("current_position") is not None

        data_completeness = calculate_data_completeness(
            has_market_data=has_market,
            has_sentiment_data=has_sentiment,
            has_news_data=has_news,
            has_fundamentals_data=has_fundamentals,
            has_smart_money_data=has_smart_money,
            has_volume_price_data=has_volume_price,
            has_user_context=has_user_context,
            has_position_data=has_position_data,
        )

        source_coverage = calculate_source_coverage(
            has_market_data=has_market,
            has_sentiment_data=has_sentiment,
            has_news_data=has_news,
            has_fundamentals_data=has_fundamentals,
            has_smart_money_data=has_smart_money,
            has_volume_price_data=has_volume_price,
            has_user_context=has_user_context,
            has_position_data=has_position_data,
        )

        reports_dict = {
            "market_report": market_research_report or "",
            "volume_price_report": state.get("volume_price_report", "") or "",
            "smart_money_report": state.get("smart_money_report", "") or "",
            "news_report": news_report or "",
        }

        evidence_statuses = infer_evidence_statuses(
            reports_dict,
            raw_evidence=state.get("metadata", {}).get("raw_evidence"),
        )

        evidence_coverage = calculate_evidence_coverage(
            ohlcv_5d=evidence_statuses["ohlcv_5d"],
            volume=evidence_statuses["volume"],
            turnover_rate=evidence_statuses["turnover_rate"],
            volume_ratio=evidence_statuses["volume_ratio"],
            individual_fund_flow=evidence_statuses["individual_fund_flow"],
            lhb_status=evidence_statuses["lhb_status"],
            margin_trading=evidence_statuses["margin_trading"],
            announcements=evidence_statuses["announcements"],
        )

        confidence = assess_confidence(
            data_completeness,
            event_risk_active=event_risk_info["has_risk"],
        )
        readiness = generate_readiness_score(data_completeness, confidence)
        final_response += format_readiness_score(readiness)

        data_sources = [
            ("市场技术数据", has_market),
            ("舆情数据", has_sentiment),
            ("新闻数据", has_news),
            ("基本面数据", has_fundamentals),
            ("主力资金", has_smart_money),
            ("量价分析", has_volume_price),
            ("用户上下文", has_user_context),
            ("持仓数据", has_position_data),
        ]
        checklist_lines = []
        for name, available in data_sources:
            status = "✅" if available else "❌"
            checklist_lines.append(f"  {status} {name}")
        checklist = "\n".join(checklist_lines)
        final_response += f"\n\n📊 数据源可用性：\n{checklist}"

        # ── D-002 ~ D-004: 证据门禁 + 双等级 + 机会评分 ──
        position_status = get_position_status(user_context)

        signals = extract_execution_signals(state, final_response, reports_dict)

        _llm_body, _ = _split_llm_body_and_system_blocks(final_response)
        contains_strong = any(
            kw in _llm_body for kw in _STRONG_BUY_KEYWORDS + _STRONG_SELL_KEYWORDS
        )

        # [E-002] name_mismatch check
        name_check = validate_stock_name(stock_code, _llm_body)
        is_name_mismatch = name_check["name_mismatch"]
        if is_name_mismatch:
            final_response += f"\n\n{name_check['note']}"
            _logger.warning("[E-002] name_mismatch: %s", name_check["note"])

        gate = get_strong_action_gate(
            source_coverage=source_coverage,
            evidence_coverage=evidence_coverage,
            position_status=position_status,
            contains_strong_action=contains_strong,
            no_execution_field_conflict=signals["no_execution_conflict"],
            no_unresolved_analyst_conflict=signals["no_unresolved_analyst_conflict"],
            name_mismatch=is_name_mismatch,
            no_execution_zone_conflict=not signals["execution_zone_conflict"],
        )

        risk_result = calculate_risk_level(
            source_coverage=source_coverage,
            evidence_coverage=evidence_coverage,
            broke_support=signals["broke_support"],
            main_capital_outflow_days=signals["main_capital_outflow_days"],
            volume_breakdown=signals["volume_breakdown"],
            has_major_positive_announcement=signals["has_major_positive_announcement"],
            position_status=position_status,
            name_mismatch=is_name_mismatch,
        )
        buy_result = calculate_buy_level(
            source_coverage=source_coverage,
            evidence_coverage=evidence_coverage,
            trend_confirmed=signals["trend_confirmed"],
            main_capital_inflow_days=signals["main_capital_inflow_days"],
            volume_healthy_expansion=signals["volume_healthy_expansion"],
            has_major_negative_announcement=signals["has_major_negative_announcement"],
            no_execution_conflict=signals["no_execution_conflict"],
            no_unresolved_analyst_conflict=signals["no_unresolved_analyst_conflict"],
            position_status=position_status,
            name_mismatch=is_name_mismatch,
        )

        opp_score = calculate_opportunity_score(
            trend_confirmed=signals["trend_confirmed"],
            capital_resonance=signals["capital_resonance"],
            catalyst_strength=signals["catalyst_strength"],
            risk_reward_ratio=signals["risk_reward_ratio"],
            entry_quality=signals["entry_quality"],
            event_risk_active=event_risk_info["has_risk"],
        )

        # ── D-002 真降级：移除/替换强动作文本 ──
        final_response, _sanitize_changes = sanitize_forbidden_strong_actions(
            final_response, gate, position_status,
            buy_result["level"], risk_result["level"],
        )
        if _sanitize_changes:
            _logger.warning("[D-002] sanitize_forbidden_strong_actions: %s", _sanitize_changes)

        final_response += "\n\n" + format_execution_block(
            source_coverage=source_coverage,
            evidence_coverage=evidence_coverage,
            confidence=confidence.value,
            opportunity_score=opp_score,
            risk_level=risk_result["level"],
            buy_level=buy_result["level"],
            risk_level_note=risk_result["note"],
            buy_level_note=buy_result["note"],
            strong_action_gate=gate,
            position_status=position_status,
        )

        # ── 推送辩论裁决（用 cleaned 覆盖流式 raw content）──
        if tracker:
            tracker.emit_debate_message(
                debate="risk", agent="Portfolio Manager",
                round_num=-1, content=final_response, is_verdict=True,
            )

        new_risk_debate_state = {
            "judge_decision": cleaned_response,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
            "claims": claims,
            "focus_claim_ids": risk_debate_state.get("focus_claim_ids", []),
            "open_claim_ids": risk_debate_state.get("open_claim_ids", []),
            "resolved_claim_ids": risk_debate_state.get("resolved_claim_ids", []),
            "unresolved_claim_ids": unresolved_claim_ids,
            "round_summary": risk_debate_state.get("round_summary", ""),
            "round_goal": risk_debate_state.get("round_goal", ""),
            "claim_counter": risk_debate_state.get("claim_counter", 0),
        }
        new_risk_feedback_state = {
            "retry_count": safe_int(risk_feedback_state.get("retry_count", 0), 0) + (1 if verdict == "revise" else 0),
            "max_retries": safe_int(risk_feedback_state.get("max_retries", 1), 1),
            "revision_required": verdict == "revise",
            "latest_risk_verdict": verdict,
            "hard_constraints": hard_constraints,
            "soft_constraints": soft_constraints,
            "execution_preconditions": execution_preconditions,
            "de_risk_triggers": de_risk_triggers,
            "revision_reason": revision_reason or ("风控要求交易员按硬约束重写方案" if verdict == "revise" else ""),
        }
        metadata = {
            **(state.get("metadata") or {}),
            "trade_quality_check": trade_quality_check,
            "source_coverage": source_coverage,
            "evidence_coverage": evidence_coverage,
            "buy_level": buy_result["level"],
            "risk_level": risk_result["level"],
            "opportunity_score": opp_score,
            "strong_action_gate_passed": gate["passed"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "risk_feedback_state": new_risk_feedback_state,
            "metadata": metadata,
            "final_trade_decision": final_response,
        }

    return risk_manager_node
