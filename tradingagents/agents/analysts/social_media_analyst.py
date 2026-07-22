import asyncio
import time

from langchain_core.messages import HumanMessage, SystemMessage
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.graph.intent_parser import build_horizon_context
from tradingagents.agents.utils.agent_states import current_tracker_var, extract_verdict
from tradingagents.agents.utils.agent_trace import _extract_actual_model_from_response
from tradingagents.agents.utils.context_utils import build_prompt_context_block


def create_social_media_analyst(llm, data_collector=None):
    async def _safe(tool, payload):
        try:
            return await asyncio.to_thread(tool.invoke, payload)
        except Exception as exc:
            return f"调用失败：{exc}"

    async def social_media_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        horizon = "short"  # 情绪面固定短期视角
        user_intent = state.get("user_intent") or {}
        focus_areas = user_intent.get("focus_areas", [])
        specific_questions = user_intent.get("specific_questions", [])

        config = get_config()
        system_message = get_prompt("social_system_message", config=config)
        horizon_ctx = build_horizon_context(horizon, focus_areas, specific_questions, agent_type="social")
        context_block = build_prompt_context_block(state, "analyst")

        pool = data_collector.get(ticker, current_date) if data_collector else None

        if pool is not None:
            news_text = pool.get("news", "无数据")
            zt_data = pool.get("zt_pool", "无数据")
            hot_stocks = pool.get("hot_stocks", "无数据")
        else:
            from datetime import datetime, timedelta
            from tradingagents.agents.utils.agent_utils import get_news, get_zt_pool, get_hot_stocks_xq
            days = 7
            end_dt = datetime.strptime(current_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=days)
            
            # Parallelize fallback fetches
            results = await asyncio.gather(
                _safe(get_news, {
                    "ticker": ticker, "start_date": start_dt.strftime("%Y-%m-%d"), "end_date": current_date,
                }),
                _safe(get_zt_pool, {"date": current_date}),
                _safe(get_hot_stocks_xq, {})
            )
            news_text, zt_data, hot_stocks = results

        messages = [
            SystemMessage(content=(
                system_message
                + "\n\n请严格基于提供的舆情数据输出报告，全程使用中文。"
            )),
            HumanMessage(content=(
                horizon_ctx + "\n"
                f"{context_block}\n\n"
                f"以下是 {ticker} 在 {current_date} 的舆情近似资料。\n\n"
                f"【get_news】\n{news_text}\n\n"
                f"【涨停池数据】\n{zt_data}\n\n"
                f"【雪球热门股票】\n{hot_stocks}\n"
            )),
        ]

        # ── 实现 Token 级流式输出 ──────────────────
        tracker = current_tracker_var.get()
        full_content = ""
        started_at = time.time()
        last_chunk = None
        async for chunk in llm.astream(messages):
            last_chunk = chunk
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += content
            if tracker:
                tracker._emit_token("Social Analyst", "sentiment_report", content)

        finished_at = time.time()
        actual_model = _extract_actual_model_from_response(last_chunk) or "unknown"
        verdict, confidence = extract_verdict(full_content)
        return {
            "sentiment_report": full_content,
            "analyst_traces": [{
                "agent": "social_media_analyst",
                "horizon": horizon,
                "data_window": "7天",
                "key_finding": f"舆情分析结论：{verdict}",
                "verdict": verdict,
                "confidence": confidence,
                "started_at": started_at,
                "finished_at": finished_at,
                "latency_ms": round((finished_at - started_at) * 1000, 1),
                "actual_model": actual_model,
            }],
        }

    return social_media_analyst_node
