import time
import json
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
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "risk_feedback_state": new_risk_feedback_state,
            "metadata": metadata,
            "final_trade_decision": final_response,
        }

    return risk_manager_node
