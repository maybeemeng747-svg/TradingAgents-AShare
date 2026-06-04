import asyncio
import re

from langchain_core.messages import HumanMessage, SystemMessage
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.graph.intent_parser import build_horizon_context
from tradingagents.agents.utils.agent_states import current_tracker_var, extract_verdict
from tradingagents.agents.utils.context_utils import build_prompt_context_block


def _check_fund_flow_anomaly(fund_flow_text: str) -> bool:
    """判断 fund_flow 数据是否显示近5日有单日主力净流入/流出占比异常。

    三种识别路径：
    1. 带 % 的百分比（无符号/正/负均可）：6.2% / +6.2% / -6.2%
    2. "占比/净占比"关键词后的数值（即使无 % 也按百分比处理）
    3. AKShare 表格 header 中含"占比/净占比"列时，解析对应列数据

    避免把日期、金额、成交额等普通数字误判为占比触发 LHB。
    若数据不可解析则返回 True（视为不可判断，需要查询）。
    """
    if not fund_flow_text or fund_flow_text.strip() in ("无数据", ""):
        return True
    lines = fund_flow_text.strip().split("\n")
    data_lines = [l for l in lines if l.strip() and not l.strip().startswith("近")]
    recent = data_lines[-5:] if len(data_lines) >= 5 else data_lines
    if not recent:
        return True

    _PCT_RE = re.compile(r"[-+]?\s*\d+\.?\d*\s*%")
    _RATIO_RE = re.compile(r"(?:占比|净占比|主力净流入占比)[^\d]*?([-+]?\d+\.?\d*)")

    _RATIO_HEADER_RE = re.compile(r"(?:占比|净占比)")
    ratio_col_indices: list[int] = []
    if len(data_lines) >= 2:
        header_line = data_lines[0]
        headers = re.split(r"[\s\t|]+", header_line.strip())
        for i, h in enumerate(headers):
            if _RATIO_HEADER_RE.search(h):
                ratio_col_indices.append(i)

    for line in recent:
        for m in _PCT_RE.finditer(line):
            raw = m.group().replace("%", "").replace(" ", "")
            try:
                val = float(raw)
                if abs(val) >= 5.0:
                    return True
            except ValueError:
                continue

        for m in _RATIO_RE.finditer(line):
            try:
                val = float(m.group(1))
                if abs(val) >= 5.0:
                    return True
            except ValueError:
                continue

        if ratio_col_indices:
            cells = re.split(r"[\s\t|]+", line.strip())
            for idx in ratio_col_indices:
                if idx < len(cells):
                    cell = cells[idx].replace(",", "").strip()
                    try:
                        val = float(cell)
                        if abs(val) >= 5.0:
                            return True
                    except ValueError:
                        continue
    return False


def _should_force_lhb_from_news(news_text: str, stock_data_text: str) -> bool:
    """[DATA-P1-LHB-FUND-DECOUPLE] Check anomaly conditions independent of fund flow."""
    force_keywords = [
        r"龙虎榜",
        r"严重?异常波动",
        r"连续\s*[\d一二三四五六七八九十]+\s*(?:涨停|跌停)",
        r"一字(?:涨停|跌停)",
        r"涨跌幅偏离",
        r"换手率\s*超过\s*\d+",
        r"成交额?\s*(?:超|破|逾)\s*\d+",
        r"量比\s*超过?\s*\d+",
    ]
    combined = f"{news_text or ''}\n{stock_data_text or ''}"
    for pattern in force_keywords:
        if re.search(pattern, combined):
            return True
    return False


def create_smart_money_analyst(llm, data_collector=None):
    async def _safe(tool, payload):
        try:
            return await asyncio.to_thread(tool.invoke, payload)
        except Exception as exc:
            return f"调用失败：{exc}"

    async def smart_money_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        print(f"[Smart Money Analyst] START {ticker} {current_date}")
        horizon = "short"
        user_intent = state.get("user_intent") or {}
        focus_areas = user_intent.get("focus_areas", [])
        specific_questions = user_intent.get("specific_questions", [])

        config = get_config()
        system_message = get_prompt("smart_money_system_message", config=config) or ""
        horizon_ctx = build_horizon_context(horizon, focus_areas, specific_questions, agent_type="smart_money")
        context_block = build_prompt_context_block(state, "analyst")

        pool = data_collector.get(ticker, current_date) if data_collector else None
        # [E-007] LHB触发链路透明化标注
        lhb_trigger_note = ""

        if pool is not None:
            fund_flow = pool.get("fund_flow_individual", "无数据")
            lhb_raw = pool.get("lhb", "无数据")
            volume = pool.get("indicators", {}).get("vwma", "无数据")

            # [DATA-P1-LHB-FUND-DECOUPLE] LHB trigger: check both fund flow AND news/anomaly
            ff_anomaly = _check_fund_flow_anomaly(fund_flow)
            news_text = pool.get("news", "") or ""
            stock_text = pool.get("stock_data", "") or ""
            anomaly_cond = _should_force_lhb_from_news(news_text, stock_text)
            if ff_anomaly or anomaly_cond:
                reasons = []
                if ff_anomaly:
                    reasons.append("资金异动明显")
                if anomaly_cond:
                    reasons.append("异常条件触发")
                lhb_trigger_note = f"[LHB触发: force=True, 原因={'+'.join(reasons)}]"
            else:
                lhb_trigger_note = f"[LHB触发: force=False, 原因=无触发条件]"

            if lhb_raw and lhb_raw not in ("无数据", ""):
                lhb = f"{lhb_trigger_note}\n{lhb_raw}"
            else:
                lhb = f"{lhb_trigger_note}\n无龙虎榜数据（可能未上榜或查询未返回数据）"
        else:
            from tradingagents.agents.utils.agent_utils import (
                get_individual_fund_flow, get_lhb_detail, get_indicators,
            )

            fund_flow_result = await _safe(get_individual_fund_flow, {"symbol": ticker})
            fund_flow = fund_flow_result

            # [DATA-P1-LHB-FUND-DECOUPLE] LHB trigger: check both fund flow AND news/anomaly
            ff_anomaly = _check_fund_flow_anomaly(fund_flow)
            anomaly_cond = _should_force_lhb_from_news(
                state.get("news", "") or "",
                state.get("stock_data", "") or "",
            )

            if ff_anomaly or anomaly_cond:
                reasons = []
                if ff_anomaly:
                    reasons.append("资金异动明显")
                if anomaly_cond:
                    reasons.append("异常条件触发")
                lhb = await _safe(get_lhb_detail, {
                    "symbol": ticker, "date": current_date, "force": True,
                })
                lhb_trigger_note = f"[LHB触发: force=True, 原因={'+'.join(reasons)}]"
                lhb = f"{lhb_trigger_note}\n{lhb}"
            else:
                lhb_trigger_note = f"[LHB触发: force=False, 原因=无触发条件]"
                lhb = f"{lhb_trigger_note}\n近期无明显异动，龙虎榜查询已跳过"

            volume = await _safe(get_indicators, {
                "symbol": ticker, "indicator": "volume",
                "curr_date": current_date, "look_back_days": 20,
            })

        messages = [
            SystemMessage(content=(
                system_message
                + "\n\n请严格基于提供的量化数据输出分析，全程使用中文。"
            )),
            HumanMessage(content=(
                horizon_ctx + "\n"
                f"{context_block}\n\n"
                f"请分析 {ticker} 在 {current_date} 的主力资金行为。\n\n"
                f"【近20日主力资金净流向】\n{fund_flow}\n\n"
                f"【龙虎榜数据】\n{lhb}\n\n"
                f"【成交量指标(vwma)】\n{volume}"
            )),
        ]

        # ── 实现 Token 级流式输出 ──────────────────
        tracker = current_tracker_var.get()
        full_content = ""
        async for chunk in llm.astream(messages):
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += content
            if tracker:
                tracker._emit_token("Smart Money Analyst", "smart_money_report", content)

        print(f"[Smart Money Analyst] DONE {ticker}, report length={len(full_content)}")
        verdict, confidence = extract_verdict(full_content)
        return {
            "smart_money_report": full_content,
            "analyst_traces": [{
                "agent": "smart_money_analyst",
                "horizon": horizon,
                "data_window": "近期可用",
                "key_finding": f"主力资金分析结论：{verdict}",
                "verdict": verdict,
                "confidence": confidence,
            }],
        }

    return smart_money_analyst_node
