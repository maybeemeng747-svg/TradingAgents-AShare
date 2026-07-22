# TradingAgents/graph/setup.py

from typing import Dict, Any, List, Optional
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode

from tradingagents.agents.utils.agent_states import AgentState

from .conditional_logic import ConditionalLogic


def create_fundamentals_integrity_gate(data_collector=None):
    """[FUND-004A] Create a graph node that gates fundamentals report integrity.

    Runs after Fundamentals Analyst, before Bull/Bear. When integrity fails,
    replaces the fundamentals_report with a safe version containing only
    verified facts and blocker reasons — no unsupported narrative.
    """
    import logging
    from tradingagents.agents.utils.fundamental_integrity import (
        evaluate_fundamental_integrity,
        build_gated_fundamentals_report,
    )

    _logger = logging.getLogger(__name__)

    async def fundamentals_integrity_gate_node(state):
        ticker = state.get("company_of_interest", "")
        trade_date = state.get("trade_date", "")
        fundamentals_report = state.get("fundamentals_report", "")

        if not fundamentals_report:
            return {}

        if data_collector is None:
            return {}

        pool = data_collector.get(ticker, trade_date)
        if pool is None:
            _logger.warning("[FUND-004A] No data pool for %s %s, skipping gate", ticker, trade_date)
            return {}

        identity = pool.get("instrument_identity") or {}
        period_facts = pool.get("financial_period_facts") or []
        explanations = pool.get("fundamental_explanations") or {}

        integrity = evaluate_fundamental_integrity(
            identity=identity,
            period_facts=period_facts,
            explanation_context=explanations,
            report_text=fundamentals_report,
        )

        metadata = dict(state.get("metadata") or {})
        metadata["fundamental_integrity"] = integrity

        if not integrity["is_valid"]:
            gated_report = build_gated_fundamentals_report(
                original_report=fundamentals_report,
                integrity=integrity,
                pool=pool,
            )
            _logger.info(
                "[FUND-004A] Gate triggered for %s: %d blockers, replacing fundamentals_report",
                ticker, len(integrity.get("blockers", [])),
            )
            return {
                "fundamentals_report": gated_report,
                "metadata": metadata,
            }

        return {"metadata": metadata}

    return fundamentals_integrity_gate_node


def _load_agent_factories() -> dict[str, Any]:
    """Load graph node factories lazily to avoid circular imports.

    Analyst modules import ``tradingagents.graph.intent_parser``; if this module
    eagerly imports ``tradingagents.agents`` during package initialization, the
    partially initialized package can miss symbols such as
    ``create_market_analyst``. Delaying these imports until graph construction
    keeps module import order stable for API requests and scheduled jobs.
    """
    from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
    from tradingagents.agents.analysts.macro_analyst import create_macro_analyst
    from tradingagents.agents.analysts.market_analyst import create_market_analyst
    from tradingagents.agents.analysts.news_analyst import create_news_analyst
    from tradingagents.agents.analysts.smart_money_analyst import create_smart_money_analyst
    from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
    from tradingagents.agents.analysts.volume_price_analyst import create_volume_price_analyst
    from tradingagents.agents.managers.research_manager import create_research_manager
    from tradingagents.agents.managers.risk_manager import create_risk_manager
    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
    from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
    from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
    from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
    from tradingagents.agents.trader.trader import create_trader

    return {
        "create_aggressive_debator": create_aggressive_debator,
        "create_bear_researcher": create_bear_researcher,
        "create_bull_researcher": create_bull_researcher,
        "create_conservative_debator": create_conservative_debator,
        "create_fundamentals_analyst": create_fundamentals_analyst,
        "create_fundamentals_integrity_gate": create_fundamentals_integrity_gate,
        "create_macro_analyst": create_macro_analyst,
        "create_market_analyst": create_market_analyst,
        "create_neutral_debator": create_neutral_debator,
        "create_news_analyst": create_news_analyst,
        "create_research_manager": create_research_manager,
        "create_risk_manager": create_risk_manager,
        "create_smart_money_analyst": create_smart_money_analyst,
        "create_social_media_analyst": create_social_media_analyst,
        "create_volume_price_analyst": create_volume_price_analyst,
        "create_trader": create_trader,
    }


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: ChatOpenAI,
        mid_thinking_llm: ChatOpenAI,
        deep_thinking_llm: ChatOpenAI,
        ultra_thinking_llm: ChatOpenAI,
        tool_nodes: Dict[str, ToolNode],
        bull_memory,
        bear_memory,
        trader_memory,
        invest_judge_memory,
        risk_manager_memory,
        conditional_logic: ConditionalLogic,
        data_collector=None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.mid_thinking_llm = mid_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.ultra_thinking_llm = ultra_thinking_llm
        self.tool_nodes = tool_nodes
        self.bull_memory = bull_memory
        self.bear_memory = bear_memory
        self.trader_memory = trader_memory
        self.invest_judge_memory = invest_judge_memory
        self.risk_manager_memory = risk_manager_memory
        self.conditional_logic = conditional_logic
        self.data_collector = data_collector

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals", "macro", "smart_money"],
        checkpointer=None
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include.
            checkpointer: Optional LangGraph checkpointer for state persistence.
        """
        if len(selected_analysts) == 0:
            raise ValueError("Trading Agents Graph Setup Error: no analysts selected!")

        factories = _load_agent_factories()

        # Create analyst nodes
        analyst_nodes = {}
        tool_nodes = {}
        done_nodes = {}

        def analyst_done_node(_state):
            return {}

        # ── 模型分档分配 ──────────────────────────────
        # quick (glm-4.5-air): 数据采集层，信息抽取为主
        # mid   (glm-4.7):     强分析+风控辩论，需要强推理
        # deep  (glm-5-turbo): 辩论核心+交易执行，综合构建论据
        # ultra (glm-5.1):     双终审，最高决策权重

        if "market" in selected_analysts:
            analyst_nodes["market"] = factories["create_market_analyst"](
                self.quick_thinking_llm, self.data_collector
            )
            tool_nodes["market"] = self.tool_nodes["market"]
            done_nodes["market"] = analyst_done_node

        if "social" in selected_analysts:
            analyst_nodes["social"] = factories["create_social_media_analyst"](
                self.quick_thinking_llm, self.data_collector
            )
            tool_nodes["social"] = self.tool_nodes["social"]
            done_nodes["social"] = analyst_done_node

        if "news" in selected_analysts:
            analyst_nodes["news"] = factories["create_news_analyst"](
                self.quick_thinking_llm, self.data_collector
            )
            tool_nodes["news"] = self.tool_nodes["news"]
            done_nodes["news"] = analyst_done_node

        if "fundamentals" in selected_analysts:
            analyst_nodes["fundamentals"] = factories["create_fundamentals_analyst"](
                self.mid_thinking_llm, self.data_collector
            )
            tool_nodes["fundamentals"] = self.tool_nodes["fundamentals"]
            done_nodes["fundamentals"] = analyst_done_node

        if "macro" in selected_analysts:
            analyst_nodes["macro"] = factories["create_macro_analyst"](
                self.mid_thinking_llm, self.data_collector
            )
            tool_nodes["macro"] = self.tool_nodes["macro"]
            done_nodes["macro"] = analyst_done_node

        if "smart_money" in selected_analysts:
            analyst_nodes["smart_money"] = factories["create_smart_money_analyst"](
                self.quick_thinking_llm, self.data_collector
            )
            tool_nodes["smart_money"] = self.tool_nodes["smart_money"]
            done_nodes["smart_money"] = analyst_done_node

        if "volume_price" in selected_analysts:
            analyst_nodes["volume_price"] = factories["create_volume_price_analyst"](
                self.quick_thinking_llm, self.data_collector
            )
            tool_nodes["volume_price"] = self.tool_nodes["volume_price"]
            done_nodes["volume_price"] = analyst_done_node

        # Create researcher and manager nodes
        bull_researcher_node = factories["create_bull_researcher"](
            self.deep_thinking_llm, self.bull_memory
        )
        bear_researcher_node = factories["create_bear_researcher"](
            self.deep_thinking_llm, self.bear_memory
        )
        research_manager_node = factories["create_research_manager"](
            self.ultra_thinking_llm, self.invest_judge_memory
        )
        trader_node = factories["create_trader"](self.deep_thinking_llm, self.trader_memory)

        # [FUND-004A] fundamental_semantic_gate_forward
        fundamentals_integrity_gate_node = factories["create_fundamentals_integrity_gate"](
            self.data_collector
        )

        # Create risk analysis nodes
        aggressive_analyst = factories["create_aggressive_debator"](self.mid_thinking_llm)
        neutral_analyst = factories["create_neutral_debator"](self.mid_thinking_llm)
        conservative_analyst = factories["create_conservative_debator"](self.mid_thinking_llm)
        risk_manager_node = factories["create_risk_manager"](
            self.ultra_thinking_llm, self.risk_manager_memory
        )

        # Create workflow
        workflow = StateGraph(AgentState)

        def analyst_display_name(analyst_type: str) -> str:
            """Convert analyst_type key to display name, e.g. 'smart_money' -> 'Smart Money'."""
            return analyst_type.replace("_", " ").title()

        # Add analyst nodes to the graph
        for analyst_type, node in analyst_nodes.items():
            workflow.add_node(f"{analyst_display_name(analyst_type)} Analyst", node)
            workflow.add_node(f"tools_{analyst_type}", tool_nodes[analyst_type])
            workflow.add_node(f"{analyst_display_name(analyst_type)} Analyst Done", done_nodes[analyst_type])

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Risk Judge", risk_manager_node)
        # [FUND-004A] Insert integrity gate between Fundamentals Analyst and Bull/Bear
        workflow.add_node("Fundamentals Integrity Gate", fundamentals_integrity_gate_node)

        # Define edges
        # Fan out all selected analysts in parallel from START
        for analyst_type in selected_analysts:
            workflow.add_edge(START, f"{analyst_display_name(analyst_type)} Analyst")

        # Each analyst runs independently, then fans in to Bull Researcher
        for analyst_type in selected_analysts:
            current_analyst = f"{analyst_display_name(analyst_type)} Analyst"
            current_tools = f"tools_{analyst_type}"
            current_done = f"{analyst_display_name(analyst_type)} Analyst Done"
            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                {
                    "continue": current_tools,
                    "done": current_done,
                },
            )
            workflow.add_edge(current_tools, current_analyst)

        # [FUND-004A] fundamental_semantic_gate_forward
        # Fundamentals Analyst Done → Integrity Gate → Bull Researcher
        # All other analysts complete → Bull Researcher (direct)
        non_fundamentals_done = [
            f"{analyst_display_name(analyst_type)} Analyst Done"
            for analyst_type in selected_analysts
            if analyst_type != "fundamentals"
        ]
        if "fundamentals" in selected_analysts:
            workflow.add_edge("Fundamentals Analyst Done", "Fundamentals Integrity Gate")
            workflow.add_edge(
                ["Fundamentals Integrity Gate"] + non_fundamentals_done,
                "Bull Researcher",
            )
        else:
            # No fundamentals analyst selected — all done nodes go directly to Bull
            workflow.add_edge(non_fundamentals_done, "Bull Researcher")

        # Add remaining edges
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Risk Judge": "Risk Judge",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Risk Judge": "Risk Judge",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Risk Judge": "Risk Judge",
            },
        )

        workflow.add_conditional_edges(
            "Risk Judge",
            self.conditional_logic.should_revise_after_risk_judge,
            {
                "Trader": "Trader",
                "END": END,
            },
        )

        # Compile and return
        return workflow.compile(checkpointer=checkpointer)
