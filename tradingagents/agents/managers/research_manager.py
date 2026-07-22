import json
import logging
import time

from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.agents.utils.agent_states import current_tracker_var, extract_verdict
from tradingagents.agents.utils.context_utils import build_prompt_context_block
from tradingagents.agents.utils.debate_utils import (
    format_claim_subset_for_prompt,
    format_claims_for_prompt,
)
from tradingagents.agents.utils.trade_actions import validate_action, TradeAction

_logger = logging.getLogger(__name__)

_BULLISH_DIRS = {"看多", "偏多", "BULLISH", "LEAN_BULLISH"}
_BEARISH_DIRS = {"看空", "偏空", "BEARISH", "LEAN_BEARISH"}
_NEUTRAL_DIRS = {"中性", "NEUTRAL"}

_ANALYST_MAP = [  # [G-003] consensus_weight_fix
    ("market_report", "market_analyst"),
    ("sentiment_report", "sentiment_analyst"),
    ("news_report", "news_analyst"),
    ("fundamentals_report", "fundamentals_analyst"),
    ("smart_money_report", "smart_money_analyst"),
    ("volume_price_report", "volume_price_analyst"),
    ("game_theory_report", "game_theory_analyst"),
]

_FUNDAMENTALS_EXTRA_KEYWORDS = [  # [G-002] consensus_weight
    (["经营现金流"], ["负", "下降", "背离"]),
    (["减持", "内部人"], []),
    (["量价"], ["弱", "缩量"]),
]


def _classify_direction(direction: str) -> str:  # [G-002] consensus_weight
    d = (direction or "").strip()
    if d in _BULLISH_DIRS:
        return "偏多"
    if d in _BEARISH_DIRS:
        return "偏空"
    return "中性"


def _build_consensus_block(  # [G-003] consensus_weight_fix
    state: dict,
) -> str | None:
    # [FUND-004A] Exclude fundamentals_analyst from consensus when integrity gate fired
    metadata = state.get("metadata") or {}
    fundamental_integrity = metadata.get("fundamental_integrity") or {}
    fundamentals_gated = not fundamental_integrity.get("is_valid", True)

    reports: list[tuple[str, str, str]] = []
    for state_key, analyst_name in _ANALYST_MAP:
        # [FUND-004A] Skip fundamentals when integrity gate has blocked it
        if analyst_name == "fundamentals_analyst" and fundamentals_gated:
            continue
        text = state.get(state_key, "")
        if not text:
            continue
        direction, _ = extract_verdict(text)
        bucket = _classify_direction(direction)
        reports.append((analyst_name, bucket, text))

    if len(reports) < 3:
        return None

    counts: dict[str, int] = {"偏多": 0, "偏空": 0, "中性": 0}
    analyst_dirs: list[dict] = []
    for name, bucket, _ in reports:
        counts[bucket] += 1
        analyst_dirs.append({"name": name, "direction": bucket})

    nonzero_dirs = {d: c for d, c in counts.items() if c > 0}  # [G-003] consensus_weight_fix
    if len(nonzero_dirs) < 2:
        return None

    sorted_dirs = sorted(nonzero_dirs.items(), key=lambda x: x[1], reverse=True)
    majority_dir, majority_cnt = sorted_dirs[0]
    minority_dir, minority_cnt = sorted_dirs[-1]

    if minority_cnt >= majority_cnt:
        return None
    if majority_cnt - minority_cnt < 2:
        return None
    total = len(reports)

    minority_analysts = [a for a in analyst_dirs if a["direction"] == minority_dir]

    downweighted = minority_cnt <= 2

    extra_downweight = False
    extra_reasons: list[str] = []

    if downweighted:
        for ma in minority_analysts:
            if ma["name"] != "fundamentals_analyst":
                continue
            fundamentals_text = state.get("fundamentals_report", "")
            for pos_kws, neg_kws in _FUNDAMENTALS_EXTRA_KEYWORDS:
                pos_hit = any(kw in fundamentals_text for kw in pos_kws)
                if not pos_hit:
                    continue
                if not neg_kws:
                    extra_downweight = True
                    extra_reasons.append(pos_kws[0])
                else:
                    for nkw in neg_kws:
                        if nkw in fundamentals_text:
                            extra_downweight = True
                            extra_reasons.append(f"{pos_kws[0]}-{nkw}")
                            break
            break

    minority_info = []
    for ma in minority_analysts:
        info = {
            "name": ma["name"],
            "direction": ma["direction"],
            "downweighted": downweighted,
            "extra_downweight": extra_downweight if ma["name"] == "fundamentals_analyst" else False,
            "extra_reasons": extra_reasons if ma["name"] == "fundamentals_analyst" else [],
        }
        minority_info.append(info)

    consensus_meta = {
        "majority_direction": majority_dir,
        "minority_count": minority_cnt,
        "minority_analysts": minority_info,
    }

    minority_names = ", ".join(a["name"] for a in minority_analysts)
    status_parts = []
    if extra_downweight:
        status_parts.append("已降权（" + " + ".join([a["name"] for a in minority_analysts]) + " 孤立偏" + minority_dir[-1] + " + " + "、".join(extra_reasons) + "）")
    elif downweighted:
        status_parts.append("已降权（" + " + ".join([a["name"] for a in minority_analysts]) + " 孤立偏" + minority_dir[-1] + "）")
    else:
        status_parts.append("未降权（少数比例较高）")

    block = (
        f"<!-- CONSENSUS_WEIGHT: {json.dumps(consensus_meta, ensure_ascii=False)} -->\n\n"
        f"【多数一致性摘要】\n"
        f"- 多数方向：{majority_dir}（{majority_cnt}/{total}）\n"
        f"- 少数方向：{minority_dir}（{minority_cnt}/{total}，{minority_names}）\n"
        f"- 降权状态：{status_parts[0]}\n"
        f"- 建议：{minority_names}的偏{minority_dir[-1]}判断权重降低，"
        f"最终决策应以多数方向为基准，但需说明{'基本面' if any(a['name'] == 'fundamentals_analyst' for a in minority_analysts) else ''}分歧点"
    )

    _logger.info(
        "[research_manager] consensus: majority=%s(%d/%d) minority=%s(%d/%d,%s) "
        "downweighted=%s extra_downweight=%s reasons=%s",
        majority_dir, majority_cnt, total,
        minority_dir, minority_cnt, total, ",".join(a["name"] for a in minority_analysts),
        downweighted, extra_downweight, extra_reasons,
    )

    return block


def create_research_manager(llm, memory):
    async def research_manager_node(state) -> dict:
        # [C-001] position_validation_gate
        user_context = state.get('user_context', {})
        current_position = user_context.get('current_position', 0)
        has_position = current_position is not None and current_position > 0

        history = state["investment_debate_state"].get("history", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        smart_money_report = state.get("smart_money_report", "")
        volume_price_report = state.get("volume_price_report", "")

        investment_debate_state = state["investment_debate_state"]
        claims = investment_debate_state.get("claims", [])
        unresolved_claim_ids = investment_debate_state.get("unresolved_claim_ids", [])
        round_summary = investment_debate_state.get("round_summary", "")

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        context_block = build_prompt_context_block(state, "research_manager")
        claims_text = format_claims_for_prompt(claims)
        unresolved_claims_text = format_claim_subset_for_prompt(claims, unresolved_claim_ids)
        round_summary_text = round_summary or "暂无轮次摘要。"

        # [G-003] consensus_weight_fix — inject conflict summary after context_block
        consensus_block = _build_consensus_block(state)
        consensus_section = ("\n\n" + consensus_block + "\n\n") if consensus_block else "\n\n"

        prompt = context_block + consensus_section + get_prompt("research_manager_prompt", config=get_config()).format(
            past_memory_str=past_memory_str,
            history=history,
            smart_money_report=smart_money_report,
            volume_price_report=volume_price_report,
            sentiment_report=sentiment_report,
            claims_text=claims_text,
            unresolved_claims_text=unresolved_claims_text,
            round_summary=round_summary_text,
        )

        _logger.info(
            "[research_manager] prompt size: total=%d chars | "
            "history=%d, smart_money=%d, volume_price=%d, sentiment=%d, "
            "memory=%d, claims=%d, unresolved=%d, round_summary=%d",
            len(prompt),
            len(history or ""),
            len(smart_money_report or ""),
            len(volume_price_report or ""),
            len(sentiment_report or ""),
            len(past_memory_str or ""),
            len(claims_text or ""),
            len(unresolved_claims_text or ""),
            len(round_summary_text or ""),
        )

        # ── 实现 Token 级流式输出 ──────────────────
        tracker = current_tracker_var.get()
        full_content = ""
        reasoning_buf: list[str] = []
        first_token_at: float | None = None
        first_reasoning_at: float | None = None
        start = time.monotonic()

        async for chunk in llm.astream(prompt):
            now = time.monotonic()
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += content

            # reasoning_content (thinking 模型) 仅做 server 端日志，不发前端
            reasoning = None
            extra = getattr(chunk, "additional_kwargs", None) or {}
            if isinstance(extra, dict):
                reasoning = extra.get("reasoning_content")
            if reasoning:
                if first_reasoning_at is None:
                    first_reasoning_at = now
                reasoning_buf.append(reasoning)

            if content:
                if first_token_at is None:
                    first_token_at = now
                if tracker:
                    tracker._emit_token("Research Manager", "investment_plan", content)
                    tracker.emit_debate_token(
                        debate="research", agent="Research Manager",
                        round_num=-1, token=content,
                    )

        total_elapsed = time.monotonic() - start
        reasoning_text = "".join(reasoning_buf)
        _logger.info(
            "[research_manager] streaming done: total_elapsed=%.2fs | "
            "ttft_reasoning=%.2fs ttft_content=%.2fs | "
            "reasoning_chars=%d content_chars=%d",
            total_elapsed,
            (first_reasoning_at - start) if first_reasoning_at else -1,
            (first_token_at - start) if first_token_at else -1,
            len(reasoning_text),
            len(full_content),
        )
        if reasoning_text:
            _logger.debug(
                "[research_manager] reasoning preview (%d chars): %s",
                len(reasoning_text),
                reasoning_text[:1500],
            )

        # ── 推送辩论裁决（标记流式结束）──
        if tracker:
            tracker.emit_debate_message(
                debate="research", agent="Research Manager",
                round_num=-1, content=full_content, is_verdict=True,
            )

        # [C-001] position_validation_gate — 校验输出动作
        # 如果未持仓，检查是否包含减仓/清仓/止损/止盈关键词，如有则替换为 WAIT
        if not has_position:
            reduce_keywords = ['减仓', '清仓', '止损', '止盈', '卖出', 'SELL', 'EXIT', 'REDUCE']
            if any(kw in full_content for kw in reduce_keywords):
                full_content += "\n\n⚠️ [C-001] 未持仓状态，已将减仓/清仓建议自动转换为观望（WAIT）。"
                _logger.warning("[C-001] position_validation_gate: 未持仓但输出了减仓/清仓建议，已自动转换")

        new_investment_debate_state = {
            "judge_decision": full_content,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_speaker": investment_debate_state.get("current_speaker", ""),
            "current_response": full_content,
            "count": investment_debate_state["count"],
            "claims": claims,
            "focus_claim_ids": investment_debate_state.get("focus_claim_ids", []),
            "open_claim_ids": investment_debate_state.get("open_claim_ids", []),
            "resolved_claim_ids": investment_debate_state.get("resolved_claim_ids", []),
            "unresolved_claim_ids": unresolved_claim_ids,
            "round_summary": round_summary,
            "round_goal": investment_debate_state.get("round_goal", ""),
            "claim_counter": investment_debate_state.get("claim_counter", 0),
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": full_content,
        }

    return research_manager_node
