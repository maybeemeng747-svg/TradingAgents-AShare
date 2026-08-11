import logging
import time
from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.graph.intent_parser import build_horizon_context
from tradingagents.agents.utils.context_utils import build_prompt_context_block

logger = logging.getLogger("volume_price_analyst")
from tradingagents.agents.utils.agent_states import current_tracker_var, extract_verdict
from tradingagents.agents.utils.agent_trace import _extract_actual_model_from_response


def create_volume_price_analyst(llm, data_collector=None):
    async def volume_price_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        horizon = "short"
        user_intent = state.get("user_intent") or {}
        focus_areas = user_intent.get("focus_areas", [])
        specific_questions = user_intent.get("specific_questions", [])

        config = get_config()
        horizon_ctx = build_horizon_context(horizon, focus_areas, specific_questions, agent_type="volume_price")
        context_block = build_prompt_context_block(state, "analyst")
        system_message = get_prompt("volume_price_system_message", config=config)

        if data_collector is not None:
            pool = data_collector.get(ticker, current_date)
            logger.info(f"[VPA-DEBUG] dc_id={id(data_collector)} ticker={ticker} date={current_date} pool={'None' if pool is None else type(pool).__name__} cache_keys={list(data_collector._cache.keys())}")
            if pool is not None:
                windowed = data_collector.get_window(pool, horizon, current_date)
                vpa_data = windowed.get("vpa_indicators", "无数据")
                stock_data = windowed.get("stock_data", "无数据")
                data_window = windowed.get("_data_window", "14天")
                logger.info(f"[VPA-DEBUG] vpa_len={len(vpa_data) if isinstance(vpa_data, str) else 'N/A'} stock_len={len(stock_data) if isinstance(stock_data, str) else 'N/A'} vpa_preview={vpa_data[:100] if isinstance(vpa_data, str) else vpa_data}")
            else:
                vpa_data, stock_data, data_window = "无数据", "无数据", "14天"
                logger.warning("[VPA-DEBUG] pool is None!")
        else:
            vpa_data, stock_data, data_window = "无数据", "无数据", "14天"
            logger.warning("[VPA-DEBUG] data_collector is None!")

        # Detect data availability to prevent LLM from fabricating data
        has_vpa = vpa_data not in ("无数据", "VPA 数据不足", "VPA 数据不足：缺少 OHLCV 列", "VPA 数据不足：历史 K 线数量不够") and not vpa_data.startswith("VPA 计算失败")
        has_stock = stock_data not in ("无数据", "") and not stock_data.startswith("No data found") and len(stock_data) > 100

        if not has_vpa and not has_stock:
            full_content = (
                f"量价分析数据不可用（{ticker}，{current_date}）。"
                f"VPA 预计算指标和原始 K 线数据均获取失败，无法进行量价分析。"
                f"\n\n⚠️ 数据缺失，本报告不提供任何价格、成交量或量价关系的判断。"
                f"其他分析师的报告不应引用本量价分析作为证据。"
            )
            return {
                "volume_price_report": full_content,
                "analyst_traces": [{
                    "agent": "volume_price_analyst",
                    "horizon": horizon,
                    "data_window": "数据不可用",
                    "key_finding": "量价分析数据不可用，无法给出结论",
                    "verdict": "中性",
                    "confidence": "低",
                }],
            }

        # Build data sections with clear availability markers
        data_sections = []
        if has_vpa:
            data_sections.append(
                f"以下是 {ticker} 截至 {current_date} 可用的已完成日线量价指标"
                f"（数据窗口：{data_window}）。盘中最新价只能引用“价格口径”。\n\n"
                f"{vpa_data}"
            )
        else:
            data_sections.append(f"⚠️ VPA 预计算指标数据不可用，跳过该部分分析。")

        if has_stock:
            data_sections.append(f"【原始 K 线数据参考】\n{stock_data}")
        else:
            data_sections.append(f"⚠️ 原始 K 线数据不可用，跳过该部分分析。")

        no_fabricate_instruction = (
            "\n\n【严禁事项】你只能基于以上提供的真实数据进行分析。"
            "绝对禁止编造、假设或虚构任何价格、成交量、K线形态数据。"
            "如果提供的数据不足以得出结论，你必须明确说明数据不足，给出'中性'判断，"
            "而不是基于假设数据做分析。"
        )

        messages = [
            SystemMessage(content=horizon_ctx + context_block + "\n\n" + system_message + "\n\n请全程使用中文。" + no_fabricate_instruction),
            HumanMessage(content="\n\n".join(data_sections)),
        ]

        tracker = current_tracker_var.get()
        full_content = ""
        started_at = time.time()
        last_chunk = None
        async for chunk in llm.astream(messages):
            last_chunk = chunk
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += content
            if tracker:
                tracker._emit_token("Volume Price Analyst", "volume_price_report", content)

        finished_at = time.time()
        actual_model = _extract_actual_model_from_response(last_chunk) or "unknown"
        verdict, confidence = extract_verdict(full_content)

        # Override confidence to low if data was partial
        if not has_vpa or not has_stock:
            confidence = "低"
            full_content += "\n\n⚠️ 注意：本分析因部分数据缺失，置信度较低。"

        return {
            "volume_price_report": full_content,
            "analyst_traces": [{
                "agent": "volume_price_analyst",
                "horizon": horizon,
                "data_window": data_window,
                "key_finding": f"量价分析结论：{verdict}",
                "verdict": verdict,
                "confidence": confidence,
                "started_at": started_at,
                "finished_at": finished_at,
                "latency_ms": round((finished_at - started_at) * 1000, 1),
                "actual_model": actual_model,
            }],
        }

    return volume_price_analyst_node
