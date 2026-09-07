from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import traceback
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Lock
from fastapi import Body
from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import uuid4

import logging
import time

# Configure standard logging to include timestamps
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, Depends, Query, Request, UploadFile, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy.orm import Session
import pandas as pd

from api.database import UserDB, UserLLMConfigDB, VersionStatsDB, ReportDB, ImportedPortfolioPositionDB, FeedbackDB, SponsorDB, NotificationLogDB, init_db, get_db, get_db_ctx
from api.job_store import get_job_store as _new_job_store
from api.services import auth_service, portfolio_import_service, report_service, token_service, watchlist_service, scheduled_service, tracking_board_service, feedback_service, sponsor_service, investment_controller_context, notification_draft_service, controller_briefing_payload_service, local_knowledge_context_service, research_evidence_service  # [IC-TA-001] investment_controller_context  # [TRACK-NOTIFY-001] notification_payload_dry_run  # [IC-TA-004] controller_briefing_payload  # [KB-006] local_knowledge_context_api  # [KB-020] research_evidence_api
from api.services import feishu_export_service  # [B-003] feishu_doc_export
from api.services import holdings_sync_service  # [B-004] holdings_sync
from api.services import feishu_webhook_service  # [M-010] feishu_webhook_notification
from api.services import notification_confirmation_service  # [M-010] feishu_notification_confirmation

def _get_real_ip(request: Request) -> Optional[str]:
    """Extract real client IP, preferring Cloudflare/proxy headers."""
    if request is None:
        return None
    # Cloudflare Tunnel injects the real client IP here
    ip = request.headers.get("CF-Connecting-IP")
    if ip:
        return ip.strip()
    # Standard proxy header fallback
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.graph.data_collector import DataCollector, get_fetch_pool_stats

# 全局共享 DataCollector：同一 ticker+date 的数据只拉一次，所有 job 复用缓存
_shared_data_collector = DataCollector()
from tradingagents.dataflows.trade_calendar import cn_today_str
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.network_timeout import install_default_network_timeout
from tradingagents.graph.intent_parser import (
    _EXPLICIT_NO_POSITION_HOLDING_PATTERN,
    _has_completed_full_exit_assertion,
    _has_completed_purchase_assertion,
    _infer_analysis_intent as _infer_analysis_intent_rule,
    _is_future_flat_position_goal,
    _is_position_alternative_question,
    _is_completed_purchase_reference,
    _is_completed_sale_reference,
    _is_failed_sale_reference,
    _is_action_match_negated,
    _is_third_party_action_reference,
    parse_intent as _parse_intent,
)
from tradingagents.agents.utils.context_utils import (
    USER_CONTEXT_KEYS,
    market_today_str,
    normalize_user_context,
)
from tradingagents.agents.utils.agent_states import current_tracker_var


def _cors_allow_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    default_origins = [
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5175",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    if not raw:
        return default_origins
    return [item.strip() for item in raw.split(",") if item.strip()]


def _cors_allow_origin_regex() -> str | None:
    raw = os.getenv("CORS_ALLOW_ORIGIN_REGEX", "").strip()
    return raw or None


def _report_version_stats() -> None:
    """Report anonymous version stats to the official site."""
    import threading, uuid

    def _send():
        try:
            requests.post(
                "https://app.510168.xyz/api/version-stats",
                json={"v": APP_VERSION, "nonce": uuid.uuid4().hex},
                timeout=30,
            )
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


def _resolve_scheduled_trade_date(trade_date: str) -> str:
    """Use the requested trading day, or fall back to the latest CN trading day."""
    from tradingagents.dataflows.trade_calendar import is_cn_trading_day, previous_cn_trading_day

    return trade_date if is_cn_trading_day(trade_date) else previous_cn_trading_day(trade_date)


def _resolve_has_position(
    user_intent: Optional[Dict[str, Any]],
    request: Optional["AnalyzeRequest"] = None,
) -> Optional[bool]:
    """Resolve has_position from user_intent / request with priority fallback.

    Priority order:
    1. user_intent.position_context.has_position
    2. user_intent.user_context.current_position > 0
    3. user_intent.user_context.current_position_pct > 0
    4. request.current_position > 0
    5. request.current_position_pct > 0
    6. None (unknown)
    """
    if user_intent:
        # 1. position_context.has_position (nested dict)
        pos_ctx = user_intent.get("position_context")
        if isinstance(pos_ctx, dict) and "has_position" in pos_ctx:
            # New parser payloads distinguish an explicit flat position from
            # the legacy default False used when account state is unknown.
            # Older payloads without the marker retain their prior behavior.
            if pos_ctx.get("position_status_explicit") is not False:
                return bool(pos_ctx["has_position"])

        # 2. user_context.current_position > 0
        user_ctx = user_intent.get("user_context")
        if isinstance(user_ctx, dict):
            cp = user_ctx.get("current_position")
            if cp is not None:
                return (cp or 0) > 0
            position_pct = user_ctx.get("current_position_pct")
            if position_pct is not None:
                return (position_pct or 0) > 0

    # 3. request.current_position > 0
    if request is not None:
        cp = getattr(request, "current_position", None)
        if cp is not None:
            return (cp or 0) > 0
        position_pct = getattr(request, "current_position_pct", None)
        if position_pct is not None:
            return (position_pct or 0) > 0

    # 4. Unknown
    return None


def _resolve_final_has_position(
    final_state: Dict[str, Any],
    request: "AnalyzeRequest",
) -> Optional[bool]:
    """Resolve final actions with the same position contract used at intake."""
    return _resolve_has_position(
        {
            "position_context": final_state.get("position_context"),
            "user_context": final_state.get("user_context") or {},
        },
        request,
    )


def _ensure_query_and_user_intent(
    request: "AnalyzeRequest",
    symbol: str,
    *,
    materialize_missing_intent: bool = True,
    numeric_position_was_explicit: Optional[bool] = None,
    position_pct_was_explicit: Optional[bool] = None,
) -> str:
    """Fill missing query/intent fields without overwriting explicit values."""
    query_was_missing = not request.query
    if query_was_missing:
        request.query = f"分析{symbol}的短线机会"
    explicit_numeric_position = (
        "current_position" in request.model_fields_set
        if numeric_position_was_explicit is None
        else numeric_position_was_explicit
    )
    explicit_position_pct = (
        "current_position_pct" in request.model_fields_set
        if position_pct_was_explicit is None
        else position_pct_was_explicit
    )
    current_position = request.current_position
    if explicit_numeric_position:
        has_position = None if current_position is None else current_position > 0
    elif explicit_position_pct:
        has_position = (
            None
            if request.current_position_pct is None
            else request.current_position_pct > 0
        )
    else:
        has_position = None if current_position is None else current_position > 0
    if has_position is None and request.current_position_pct is not None:
        has_position = request.current_position_pct > 0
    imported_zero_conflicts_with_explicit_positive_pct = bool(
        current_position is not None
        and current_position <= 0
        and not explicit_numeric_position
        and explicit_position_pct
        and request.current_position_pct is not None
        and request.current_position_pct > 0
    )
    if imported_zero_conflicts_with_explicit_positive_pct:
        # A manually supplied account-level percentage is authoritative. An
        # imported zero-share row may be stale or partial, so discard that
        # conflicting share count instead of erasing the explicit percentage.
        current_position = None
        request.current_position = None
    elif current_position is not None and current_position <= 0:
        current_position = 0
        request.current_position = 0
        request.current_position_pct = None
        request.average_cost = None
    query_text = str(request.query or "")
    objective_text = str(request.objective or "")

    def _action_state(text: str, keywords: tuple[str, ...]) -> tuple[bool, bool]:
        mentioned = False
        for keyword in keywords:
            for match in re.finditer(re.escape(keyword), text):
                if keyword == "要买" and (
                    (match.start() > 0 and text[match.start() - 1] in "主需")
                    or text[match.end():].startswith(("方", "盘"))
                ):
                    continue
                if keyword in {"买入", "买"} and _is_completed_purchase_reference(
                    text, match.start(), match.end()
                ):
                    # Historical/completed purchase evidence establishes a
                    # holding; it is not a request to buy again.
                    continue
                if re.search(r"卖出|卖掉|减仓|清仓|离场", keyword) and _is_completed_sale_reference(
                    text, match.start(), match.end()
                ):
                    # A dated/completed sale is account history, not a request
                    # to execute another reduction.
                    continue
                if re.search(r"卖出|卖掉|减仓|清仓|离场", keyword) and _is_failed_sale_reference(
                    text, match.start(), match.end()
                ):
                    # A failed/cancelled historical exit is account history,
                    # not a request to execute another reduction.
                    continue
                if keyword == "止损":
                    stop_field = text[match.start():match.end() + 12]
                    entry_plan_present = bool(
                        has_position is not True
                        and re.search(
                            r"(?:建仓|买入|入场|想买|准备买|打算买|计划买|"
                            r"考虑买|能买吗|能否买|能不能买|可以买|值得买吗)",
                            text,
                        )
                    )
                    if (
                        re.match(
                            r"止损(?:位|价|线|红线|点|阈值|区间|条件)"
                            r".{0,8}(?:设|设置|设定|定为|多少|怎么|如何|在哪|是什么)",
                            stop_field,
                        )
                        or (
                            entry_plan_present
                            and re.match(
                                r"止损(?:位|价|线|红线|点|阈值|区间|条件)?"
                                r"\s*(?:(?:设|设置|设定|定为|放在|在|为|到)\s*)?"
                                r"\d+(?:\.\d+)?\s*(?:元|块)?",
                                stop_field,
                            )
                        )
                    ):
                        # An entry plan can contain a proposed stop-loss field.
                        # That field is a risk parameter, not an instruction to
                        # exit a position.
                        continue
                mentioned = True
                if _is_third_party_action_reference(text, match.start()):
                    continue
                if _is_action_match_negated(text, match.start(), match.end()):
                    continue
                return mentioned, True
        return mentioned, False

    inferred_query_intent = "watch"
    if not query_was_missing:
        inferred_query_intent, _ = _infer_analysis_intent_rule(query_text)
    action_groups = (
        (
            "止损", "清仓", "割肉", "认赔", "全部卖出", "全部离场",
            "卖出全部持仓", "卖掉全部持仓", "全部持仓卖出",
        ),
        ("减仓", "止盈", "降低仓位", "部分卖出", "卖出止盈", "卖出"),
        ("加仓", "补仓", "追加", "买入更多"),
        (
            "建仓", "买入", "入场", "想买", "要买", "准备买", "打算买",
            "计划买", "考虑买", "能买吗", "能否买", "能不能买", "可以买",
            "要不要买", "该不该买", "是否应该买", "值不值得买", "值得买吗",
            "什么时候买", "什么时间买", "何时买", "买多少",
        ),
        ("持有", "持仓观察"),
    )
    query_requests_action = any(
        _action_state(query_text, keywords)[1] for keywords in action_groups
    ) or inferred_query_intent != "watch"
    query_requests_watch = _action_state(
        query_text,
        ("观察", "观望", "先看看", "只做研究", "等待"),
    )[1]
    query_overrides_saved_intent = query_requests_action or query_requests_watch
    action_text = (
        query_text
        if not query_was_missing and query_overrides_saved_intent
        else "\n".join(part for part in (objective_text, query_text) if part)
    )

    def _position_assertion(text: str) -> Optional[bool]:
        if _has_completed_full_exit_assertion(text):
            return False
        positive_pattern = re.compile(
            r"(?:我|本人)\s*(?:(?:当前|现在|目前)\s*)?的?\s*"
            r"(?:实际)?(?:持仓|仓位)|"
            r"(?:(?:(?:我|本人)\s*)(?:(?:当前|现在|目前|已经|已)\s*)?|"
            r"(?:(?:当前|现在|目前|已经|已)\s*))"
            r"(?:有|持有)?(?:实际)?(?:持仓|仓位)"
            r"(?!价值|风险|必要|意义|需求|建议|上限|下限|限制|策略|配置|"
            r"目标|应该|应当|多少|几成|怎么|如何|\s*(?:为|是|=)\s*(?:0|否\b|false\b))|"
            r"(?:持仓|仓位)\s*(?:为|是|=|[:：])?\s*[1-9]\d*\s*(?:股|手|%)?|"
            r"(?:(?:我|本人)\s*(?:已经|已|刚刚|刚)|"
            r"(?:已经|已|刚刚|刚)\s*)"
            r"(?:买入|购入|买)(?:了)?\s*[1-9]\d*\s*(?:股|手)?|"
            r"(?:已持仓|已有持仓|持有实际持仓|持有仓位)"
            r"(?!价值|风险|必要|意义|需求)|"
            r"(?:不是|并非|非)\s*(?:空仓|无持仓|未持仓)|"
            r"(?:当前)?(?:持仓|仓位)\s*(?:为|是|=)\s*(?:有|是|true)\b",
            re.IGNORECASE,
        )
        no_position_pattern = re.compile(
            r"(?:未|无|没有)(?:任何)?(?:实际)?持仓"
            r"(?!成本|均价|记录|信息|数据|明细|天数|比例|市值|价值|风险|必要|意义|需求)|"
            r"(?:(?:我|本人)\s*)?"
            r"(?:还没(?:有)?|还未|尚未|没(?:有)?|未)"
            r"(?:买入|买(?!入))(?:该股|这只股票)?"
            r"(?!\s*(?:更多|额外|计划|信号|建议|条件|机会|必要|需求|打算|意图|动作|理由|资格|能力|方|盘|量|额))|"
            r"(?:(?:我|本人|当前|现在|目前)\s*)?不(?:再)?持仓"
            r"(?=\s*(?:了\s*)?(?:[，,。；;！？!?\n]|$))|"
            rf"{_EXPLICIT_NO_POSITION_HOLDING_PATTERN}|"
            r"(?:(?:我|本人)\s*)?(?:(?:当前|现在|目前|已经|已)\s*)?"
            r"(?<!不)(?<!非)(?:是|为|=)\s*空仓|"
            r"(?:(?:我|本人)\s*)?(?:当前|现在|目前|已经|已)\s*空仓|"
            r"(?:我|本人)\s*空仓|"
            r"(?:^|[，,。；;\n])\s*空仓(?=\s*(?:状态|[，,。；;\n]|$))|"
            r"(?:当前)?(?:持仓|仓位)\s*(?:为|是|=)\s*"
            r"(?:0(?![\d.])|零|否\b|false\b)"
        )

        assertions: list[tuple[int, bool]] = []
        for value, pattern in ((True, positive_pattern), (False, no_position_pattern)):
            for match in pattern.finditer(text):
                if not value and re.search(
                    r"(?:未|不(?:再)?)持有.{0,24}"
                    r"(?:观点|看法|意见|态度|立场|预期|判断|信心|偏见)",
                    match.group(),
                ):
                    continue
                prefix = text[max(0, match.start() - 8):match.start()]
                suffix = text[match.end():match.end() + 2]
                position_goal_prefix = text[
                    max(0, match.start() - 20):match.start()
                ]
                third_party_prefix = text[max(0, match.start() - 20):match.start()]
                if (
                    not re.search(r"(?:我|本人)", match.group())
                    and re.search(
                        r"(?:北向资金|主力资金|基金经理|券商分析师|"
                        r"机构分析师|首席分析师|机构|游资|外资|基金|"
                        r"券商|股东|大股东|控股股东|公司|市场|"
                        r"分析师|专家|研究员|研报)"
                        r"(?:(?:数据|报告|公告|资料|统计)?"
                        r"(?:显示|表明|称|指出|披露|认为))?"
                        r"(?:当前|现在|目前|已经|已)?\s*$",
                        third_party_prefix,
                    )
                ):
                    continue
                if re.search(
                    r"(?:如果|假如|假设|若|倘若|是否|有没有)"
                    r"(?:我|本人|当前|现在)?\s*$",
                    prefix,
                ):
                    continue
                if (
                    match.start() > 0
                    and text[match.start() - 1] == "有"
                    and match.group().startswith("没有")
                ):
                    continue
                if re.search(r"(?:之前|曾经|过去|原来)(?:我|本人)?\s*$", prefix):
                    continue
                if not value and re.search(
                    r"(?:建议|推荐|应该|应当|最好|考虑|主张|选择|保持)"
                    r"(?:(?:我|本人|当前|现在|目前|继续|暂时|先)\s*)*$",
                    prefix,
                ):
                    continue
                if re.match(r"\s*(?:了|着)?(?:吗|么|？|\?)", suffix):
                    continue
                if not value and re.search(r"(?:不是|并非|非)\s*$", prefix):
                    continue
                if not value and _is_future_flat_position_goal(
                    text,
                    match.start(),
                    match.end(),
                ):
                    # Desired/future flat state is an exit objective, not a
                    # statement that the current holding is already zero.
                    continue
                if not value and _is_position_alternative_question(
                    text,
                    match.start(),
                    match.end(),
                ):
                    # This is an action choice, not a new account snapshot.
                    continue
                if value and re.search(
                    r"(?:建议|推荐|目标|计划|希望|准备|打算|考虑|预计|理想|拟|"
                    r"最大|最高|最低|单票|个股|风险预算|预算|仓位上限|仓位下限)"
                    r"(?:(?:我|本人|当前|现在|目前|最终|初始|试探性)\s*)*$",
                    position_goal_prefix,
                ):
                    # A proposed/target position size is an entry plan, not
                    # evidence that the user already owns the instrument.
                    continue
                assertions.append((match.start(), value))

        if not assertions and _has_completed_purchase_assertion(text):
            return True
        if not assertions:
            if text.strip() == "空仓":
                return False
            return None
        return max(assertions, key=lambda item: item[0])[1]

    query_position = None if query_was_missing else _position_assertion(query_text)
    objective_position = _position_assertion(objective_text)
    explicit_query_position = query_position is not None
    explicit_flat_position = bool(
        (
            explicit_numeric_position
            and current_position is not None
            and current_position <= 0
        )
        or (
            explicit_position_pct
            and request.current_position_pct is not None
            and request.current_position_pct <= 0
        )
        or query_position is False
        or (query_position is None and objective_position is False)
    )

    # Current request facts outrank imported/saved portfolio context. Clear the
    # whole position tuple together so downstream gates cannot see a flat
    # position_context alongside stale shares or cost basis.
    if explicit_flat_position:
        has_position = False
        current_position = 0
        request.current_position = 0
        request.current_position_pct = None
        request.average_cost = None

    # The full intent parser may have extracted numeric position fields from
    # the query between the first deterministic routing pass and this second
    # normalization pass. Preserve those richer values unless the current
    # query explicitly says the user is flat.
    parsed_intent = request.user_intent or {}
    parsed_user_context = parsed_intent.get("user_context") or {}
    parsed_position_context = parsed_intent.get("position_context") or {}
    if current_position is None and not explicit_flat_position:
        parsed_current_position = parsed_user_context.get("current_position")
        if parsed_current_position is None:
            parsed_current_position = parsed_position_context.get("shares")
        if parsed_current_position is not None:
            current_position = float(parsed_current_position)
            request.current_position = current_position
            has_position = current_position > 0
    if request.current_position_pct is None and not explicit_flat_position:
        parsed_position_pct = parsed_user_context.get("current_position_pct")
        if parsed_position_pct is None:
            parsed_position_pct = parsed_position_context.get("position_pct")
        if parsed_position_pct is not None:
            request.current_position_pct = float(parsed_position_pct)
            if request.current_position_pct > 0:
                has_position = True
                if current_position == 0 and not explicit_numeric_position:
                    current_position = None
                    request.current_position = None
            elif has_position is None:
                has_position = request.current_position_pct > 0
    if has_position:
        if request.average_cost is None:
            parsed_average_cost = parsed_user_context.get("average_cost")
            if parsed_average_cost is None:
                parsed_average_cost = parsed_position_context.get("avg_cost")
            if parsed_average_cost is not None:
                request.average_cost = float(parsed_average_cost)

    inferred_position = query_position
    if inferred_position is None and current_position is None:
        inferred_position = objective_position

    if inferred_position is False:
        has_position = False
        current_position = 0
        request.current_position = 0
        request.current_position_pct = None
        request.average_cost = None
    elif inferred_position is True and not explicit_flat_position and not (
        (explicit_numeric_position and current_position == 0)
        or (
            explicit_position_pct
            and request.current_position_pct is not None
            and request.current_position_pct <= 0
        )
    ):
        has_position = True
        if current_position is not None and current_position <= 0:
            current_position = None
            request.current_position = None

    stop_loss_objective = _action_state(
        action_text,
        (
            "止损", "清仓", "割肉", "认赔", "全部卖出", "全部离场",
            "卖出全部持仓", "卖掉全部持仓", "全部持仓卖出",
        ),
    )[1]
    reduce_objective = _action_state(
        action_text, ("减仓", "止盈", "降低仓位", "部分卖出", "卖出止盈")
    )[1]
    generic_sell_objective = _action_state(action_text, ("卖出",))[1]
    if generic_sell_objective and not stop_loss_objective:
        reduce_objective = True
    add_objective = _action_state(
        action_text, ("加仓", "补仓", "追加", "买入更多")
    )[1]
    holding_objective = _action_state(
        action_text, ("持有", "持仓观察")
    )[1] or bool(re.search(r"持仓(?:复盘|处理|怎么办|如何)", action_text))
    entry_objective = _action_state(
        action_text,
        (
            "建仓", "买入", "入场", "想买", "要买", "准备买", "打算买",
            "计划买", "考虑买", "能买吗", "能否买", "能不能买", "可以买",
            "要不要买", "该不该买", "是否应该买", "值不值得买", "值得买吗",
            "什么时候买", "什么时间买", "何时买", "买多少",
        ),
    )[1]
    # English action requests are recognized by the shared deterministic
    # parser. Mirror that result into the local objective flags before profile
    # selection, including when chat supplied a stale/incorrect pre-intent.
    stop_loss_objective = stop_loss_objective or inferred_query_intent == "stop_loss"
    reduce_objective = reduce_objective or inferred_query_intent == "reduce"
    add_objective = add_objective or inferred_query_intent == "add"
    holding_objective = holding_objective or inferred_query_intent == "holding"
    entry_objective = entry_objective or inferred_query_intent == "entry"
    if has_position and reduce_objective:
        default_intent = "reduce"
    elif has_position and stop_loss_objective:
        default_intent = "stop_loss"
    elif has_position and (add_objective or entry_objective):
        default_intent = "add"
    elif has_position:
        default_intent = "holding"
    elif has_position is None and reduce_objective:
        default_intent = "reduce"
    elif has_position is None and stop_loss_objective:
        default_intent = "stop_loss"
    elif has_position is None and add_objective:
        default_intent = "add"
    elif has_position is None and holding_objective:
        default_intent = "holding"
    elif entry_objective:
        default_intent = "entry"
    else:
        default_intent = "watch"

    # Direct natural-language requests select a runtime profile before the
    # full parser runs. Reuse the parser's deterministic rule so common entry,
    # holding and exit wording cannot be routed through the watch-only profile.
    if not query_was_missing and default_intent == "watch":
        inferred_intent = inferred_query_intent
        position_only_intents = {"holding", "add", "reduce", "stop_loss"}
        if inferred_intent != "watch" and not (
            has_position is False and inferred_intent in position_only_intents
        ):
            default_intent = inferred_intent

    default_position_context = None
    if has_position is not None:
        default_position_context = {
            "has_position": has_position,
            "avg_cost": request.average_cost if has_position else None,
            "shares": current_position if has_position else None,
            "position_pct": request.current_position_pct if has_position else None,
            "holding_days": None,
        }

    existing = dict(request.user_intent or {})
    if existing:
        existing.setdefault("ticker", symbol)
        existing.setdefault("horizons", request.horizons or ["short"])
        if explicit_query_position or (
            not query_was_missing and query_overrides_saved_intent
        ):
            existing["analysis_intent"] = default_intent
        elif (
            current_position is not None
            and not has_position
            and str(existing.get("analysis_intent") or "")
            in {"holding", "add", "reduce", "stop_loss"}
        ):
            existing["analysis_intent"] = default_intent
        else:
            existing.setdefault("analysis_intent", default_intent)
        saved_position_context = existing.get("position_context")
        clean_flat_context = (
            current_position == 0
            and isinstance(saved_position_context, dict)
            and saved_position_context.get("has_position") is False
            and not any(
                saved_position_context.get(key) is not None
                for key in ("avg_cost", "shares", "position_pct", "holding_days")
            )
        )
        if (
            explicit_query_position
            or (current_position is not None and not clean_flat_context)
            or (
                has_position is True
                and request.current_position_pct is not None
                and request.current_position_pct > 0
            )
        ) and default_position_context is not None:
            existing["position_context"] = default_position_context
        elif existing.get("position_context") is None and default_position_context is not None:
            existing["position_context"] = default_position_context
        elif (
            has_position is None
            and isinstance(saved_position_context, dict)
            and saved_position_context.get("has_position") is False
            and saved_position_context.get("position_status_explicit") is False
        ):
            # The parser initializes unknown account state as false for
            # compatibility. Do not let that non-explicit sentinel reach
            # agents as an asserted flat/no-position fact.
            existing["position_context"] = None
        user_context = dict(existing.get("user_context") or {})
        if current_position is not None:
            user_context["current_position"] = current_position
        elif explicit_query_position:
            user_context.pop("current_position", None)
        elif (
            has_position is True
            and request.current_position_pct is not None
            and request.current_position_pct > 0
        ):
            user_context.pop("current_position", None)
        if request.current_position_pct is not None and has_position:
            user_context["current_position_pct"] = request.current_position_pct
        if (explicit_query_position or current_position is not None) and not has_position:
            user_context.pop("current_position_pct", None)
            user_context.pop("average_cost", None)
        existing["user_context"] = user_context
        existing.setdefault("raw_query", request.query)
        request.user_intent = existing
        return default_intent

    # Direct /v1/analyze natural-language requests still need the full intent
    # parser.  The deterministic pass may normalize explicit position fields
    # and provide a routing hint, but must not create a placeholder intent that
    # would make _run_job_inner skip _parse_intent.
    if not materialize_missing_intent:
        return default_intent

    user_context = {}
    if current_position is not None:
        user_context["current_position"] = current_position
    if request.current_position_pct is not None and has_position:
        user_context["current_position_pct"] = request.current_position_pct
    request.user_intent = {
        "ticker": symbol,
        "horizons": request.horizons or ["short"],
        "analysis_intent": default_intent,
        "position_context": default_position_context,
        "user_context": user_context,
        "raw_query": request.query,
    }
    return default_intent


def _build_scheduled_analyze_request(
    db: Session,
    user_id: str,
    symbol: str,
    horizon: str,
    trade_date: str,
    scheduled_user_context: Optional[Dict[str, Any]] = None,
) -> "AnalyzeRequest":
    scheduled_user_context = scheduled_user_context or _build_imported_user_context(db, user_id, symbol)
    # Read user's saved analyst selection from DB
    user_cfg = auth_service.get_user_llm_config(db, user_id)
    selected = None
    if user_cfg and user_cfg.default_analysts:
        try:
            selected = json.loads(user_cfg.default_analysts)
        except Exception:
            pass
    req = AnalyzeRequest(
        symbol=symbol,
        trade_date=trade_date,
        horizons=[horizon],
        query=f"定时分析 {symbol}",
        user_intent={
            "ticker": symbol,
            "horizons": [horizon],
            "analysis_intent": "holding" if scheduled_user_context.get("current_position", 0) > 0 else "watch",
            "position_context": {
                "has_position": (scheduled_user_context.get("current_position", 0) or 0) > 0,
                "avg_cost": scheduled_user_context.get("average_cost"),
                "shares": scheduled_user_context.get("current_position"),
                "position_pct": scheduled_user_context.get("current_position_pct"),
                "holding_days": None,
            } if scheduled_user_context.get("current_position", 0) else None,
            "focus_areas": [],
            "specific_questions": [],
            "user_context": scheduled_user_context,
        },
        objective=scheduled_user_context.get("objective"),
        current_position=scheduled_user_context.get("current_position"),
        current_position_pct=scheduled_user_context.get("current_position_pct"),
        average_cost=scheduled_user_context.get("average_cost"),
        user_notes=scheduled_user_context.get("user_notes"),
        runtime_tier="FULL_TA",  # [PERF-001] scheduler explicitly opts into FULL_TA
        confirmed_full_ta=True,  # [PERF-001] scheduler is user-initiated, counts as confirmed
        runtime_profile="FULL_TA",  # [PERF-002] scheduler runs full TA
    )
    if selected:
        req.selected_analysts = selected
    return req


async def _run_manual_trigger(
    task: dict,
    requested_trade_date: str,
    job_id: str,
) -> None:
    """Execute a manual-trigger analysis (no scheduler concurrency control).

    Used by the /v1/scheduled/{id}/trigger and /v1/scheduled/batch/trigger
    endpoints. Calls _run_job directly then records the test result.
    """
    task_id = task["id"]
    user_id = task["user_id"]
    symbol = task["symbol"]
    horizon = task.get("horizon") or "short"

    actual_trade_date = _resolve_scheduled_trade_date(requested_trade_date)
    _log(f"[Manual Trigger] {symbol} trade_date={actual_trade_date} (requested={requested_trade_date})")

    try:
        with get_db_ctx() as db:
            scheduled_user_context = task.get("manual_user_context") or _build_imported_user_context(
                db, user_id, symbol
            )
            req = _build_scheduled_analyze_request(
                db=db,
                user_id=user_id,
                symbol=symbol,
                horizon=horizon,
                trade_date=actual_trade_date,
                scheduled_user_context=scheduled_user_context,
            )

        await _run_job(job_id, req, False, True, user_id, "scheduled_manual")
        job_state = _get_job(job_id)
        if job_state.get("status") == "failed":
            raise RuntimeError(job_state.get("error") or f"manual trigger job {job_id} failed")
        with get_db_ctx() as db:
            scheduled_service.record_manual_test_result(db, task_id, "success", report_id=job_id)
        _log(f"[Manual Trigger] Completed {symbol}")
    except Exception as e:
        logger.error(f"[Manual Trigger] Failed {symbol}: {e}\n{traceback.format_exc()}")
        with get_db_ctx() as db:
            scheduled_service.record_manual_test_result(db, task_id, "failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup and cleanup on shutdown."""
    network_timeout = float(os.getenv("TA_SOCKET_DEFAULT_TIMEOUT", "60"))
    install_default_network_timeout(network_timeout)
    _log(
        "Default socket and requests timeout set to "
        f"{network_timeout:g}s."
    )
    # Raise the AnyIO thread limiter ceiling so frequent sync endpoints
    # (tracking-board polling, /v1/jobs/{id} polling, akshare-backed
    # market endpoints) cannot starve each other when the event loop is
    # also running long-lived `_run_job` tasks.
    try:
        from anyio import to_thread as _anyio_to_thread

        limiter = _anyio_to_thread.current_default_thread_limiter()
        desired = int(os.getenv("ANYIO_THREAD_LIMIT", "120"))
        if limiter.total_tokens < desired:
            limiter.total_tokens = desired
            _log(f"AnyIO thread limiter raised to {desired}.")
    except Exception as exc:
        _log(f"Could not raise AnyIO thread limiter: {exc}")

    # Default asyncio executor is used by `asyncio.to_thread`. The CPython
    # default is `min(32, cpu_count + 4)`, which is too small when many
    # `_run_job_inner` coroutines fan out concurrent `to_thread` calls for
    # DB writes, LLM extraction, and akshare data collection.
    global _default_executor
    new_default_executor: Optional[ThreadPoolExecutor] = None
    try:
        loop = asyncio.get_running_loop()
        executor_workers = int(os.getenv("ASYNCIO_DEFAULT_EXECUTOR_WORKERS", "64"))
        new_default_executor = ThreadPoolExecutor(
            max_workers=executor_workers,
            thread_name_prefix="ta-asyncio",
        )
        loop.set_default_executor(new_default_executor)
        _default_executor = new_default_executor
        _log(f"Default asyncio executor set to {executor_workers} workers.")
    except Exception as exc:
        _log(f"Could not configure default asyncio executor: {exc}")

    init_db()
    _log("Database initialized.")
    try:
        from tradingagents.tradeflow.candidate_engine import init_db as _tf_init_db  # [TF-P0-001] runtime_schema_name_observe_fix
        _tf_init_db(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tradeflow.db"))
        _log("TradeFlow DB schema migrated.")
    except Exception as _tf_exc:
        _log(f"TradeFlow DB init skipped: {_tf_exc}")
    store = get_job_store()
    store.clear()
    _background_tasks.clear()

    # Security: warn loudly if using default secret key
    if not os.getenv("TA_APP_SECRET_KEY"):
        _log("=" * 70)
        _log("WARNING: TA_APP_SECRET_KEY is not set!")
        _log("Using hardcoded default key. ALL encryption and JWT signing")
        _log("is INSECURE. Set TA_APP_SECRET_KEY env var before production use.")
        _log("=" * 70)

    _report_version_stats()
    # Pre-load trade calendar (uses mini_racer/V8 which is not thread-safe)
    from tradingagents.dataflows.trade_calendar import _load_cn_trade_dates
    _load_cn_trade_dates()
    _log("Trade calendar pre-loaded.")
    # Pre-load stock + ETF name map
    await asyncio.to_thread(_load_cn_stock_map)
    _log("Stock map pre-loaded on startup.")
    yield
    _log("Shutting down: Cleaning up resources...")
    _executor.shutdown(wait=True)
    if new_default_executor is not None:
        new_default_executor.shutdown(wait=False)
    _log("Executor shutdown complete.")


_is_prod = os.getenv("ENV", "").lower() == "prod"

_BACKEND_START_TIME: str = ""
_GIT_COMMIT_SHORT: str = "unknown"


def _init_version_info() -> None:
    """Read git commit hash once at startup; record process start time."""
    global _BACKEND_START_TIME, _GIT_COMMIT_SHORT
    _BACKEND_START_TIME = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        import subprocess as _sp
        _GIT_COMMIT_SHORT = _sp.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=_sp.DEVNULL,
            cwd=str(Path(__file__).resolve().parent),
        ).decode().strip()
    except Exception:
        _GIT_COMMIT_SHORT = "unknown"


_init_version_info()


def _get_version() -> str:
    """Get app version: APP_VERSION env > package metadata > 'dev'."""
    v = os.getenv("APP_VERSION")
    if v:
        return v
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version("tradingagents")
    except Exception:
        return "dev"


APP_VERSION = _get_version()

app = FastAPI(
    title="TradingAgents-AShare API",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_origin_regex=_cors_allow_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=int(os.getenv("TA_MAX_WORKERS", "2")))
_default_executor: Optional[ThreadPoolExecutor] = None
# [UPSTREAM-081-001] healthz 探针排队上限（秒）：no-op 超过该时间排不上默认
# executor 即判饱和返回 503，避免探针自身长时间占用请求。
_HEALTHZ_PROBE_TIMEOUT = float(os.getenv("TA_HEALTHZ_PROBE_TIMEOUT", "5"))

# ── Singleton job store (in-memory or Redis depending on REDIS_URL) ─────────
_job_store_instance: Optional[Any] = None

def get_job_store():
    global _job_store_instance
    if _job_store_instance is None:
        _job_store_instance = _new_job_store()
    return _job_store_instance

# Runtime config overrides via PATCH /v1/config
_global_config_overrides: Dict[str, Any] = {}

# Allowlist for config_overrides from client requests.
# Security: prevents injection of api_key, backend_url, or other sensitive keys.
_CONFIG_OVERRIDES_ALLOWLIST = {
    "llm_provider", "deep_think_llm", "quick_think_llm",
    "max_debate_rounds", "max_risk_discuss_rounds",
    "prompt_language",
}
# Hold references to fire-and-forget tasks so they are not garbage collected
_background_tasks: set = set()

# ── A-share stock name → code cache ──────────────────────────────────────────
_cn_stock_map: Optional[Dict[str, str]] = None  # name -> "XXXXXX.SH/SZ"
_cn_stock_reverse_map: Optional[Dict[str, str]] = None  # code -> name
_cn_stock_map_lock = Lock()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_JOB_TIMEOUT = int(os.getenv("TA_JOB_TIMEOUT", "1800"))  # seconds
_ZHIPU_CODING_JOB_TIMEOUT = int(os.getenv("TA_ZHIPU_CODING_JOB_TIMEOUT", "2700"))  # seconds


def _job_timeout_for_config(config: Dict[str, Any]) -> int:
    base_url = str(config.get("backend_url") or "").lower().rstrip("/")
    if "open.bigmodel.cn/api/coding" in base_url:
        return _ZHIPU_CODING_JOB_TIMEOUT
    return _JOB_TIMEOUT


def _resolve_job_timeout(request: AnalyzeRequest, user_id: Optional[str]) -> int:
    try:
        if user_id:
            with get_db_ctx() as db:
                config = _build_runtime_config(request.config_overrides, user_id=user_id, db=db)
        else:
            config = _build_runtime_config(request.config_overrides, user_id=user_id)
        return _job_timeout_for_config(config)
    except Exception as exc:
        _log(f"[Job Timeout] failed to resolve runtime timeout, falling back to {_JOB_TIMEOUT}s: {exc}")
        return _JOB_TIMEOUT


def _create_tracked_task(coro, *, label: str = "Background task") -> asyncio.Task:
    """Create an asyncio task and keep a reference to prevent GC.
    Also logs unhandled exceptions via a done callback."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error("%s failed: %s", label, t.exception())

    task.add_done_callback(_on_done)
    return task


async def _send_report_bark_notification(
    user_id: Optional[str],
    report_id: str,
    symbol: str,
    *,
    source: str = "report_saved",
) -> None:
    """Send Bark once a report is persisted, for all report creation paths."""
    if not user_id or not report_id:
        return
    try:
        from api.services.bark_notification_service import send_report_message_with_retry

        bark_url = None
        report_to_send = None
        with get_db_ctx() as db:
            user = db.query(UserDB).filter(UserDB.id == user_id).first()
            if not user or not getattr(user, "bark_report_enabled", True):
                return
            user_cfg = auth_service.get_user_llm_config(db, user_id)
            bark_url = auth_service.decrypt_secret(getattr(user_cfg, "bark_url_encrypted", None))
            if not bark_url:
                return
            report = db.query(ReportDB).filter(ReportDB.id == report_id).first()
            if not report or report.status != "completed":
                return
            db.expunge(report)
            report_to_send = report

        if report_to_send and bark_url:
            _log(f"[Bark] Sending report notification for {symbol} source={source}")
            await send_report_message_with_retry(report_to_send, bark_url)
    except Exception as exc:
        logger.warning("[Bark] report notification failed for %s: %s", symbol, exc)


def _send_report_bark_notification_background(
    user_id: Optional[str],
    report_id: str,
    symbol: str,
    source: str = "report_saved",
) -> None:
    asyncio.run(_send_report_bark_notification(user_id, report_id, symbol, source=source))


def _log(msg: str):
    """Helper to log with timestamp via standard logging."""
    logger.info(msg)


def _serialize_datetime_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


_cn_stock_map_loaded_at: float = 0  # timestamp of last load
_STOCK_MAP_TTL = 7 * 86400  # 7 days


def _load_cn_stock_map() -> Dict[str, str]:
    """Lazy-load and cache A-share stock + ETF/fund name→code mapping (7-day TTL).

    Uses akshare stock_info_a_code_name (static list, no anti-crawl) for A-shares,
    plus fund_name_em for ETFs/funds.
    """
    global _cn_stock_map, _cn_stock_reverse_map, _cn_stock_map_loaded_at
    import time as _time
    now = _time.time()
    if _cn_stock_map is not None and (now - _cn_stock_map_loaded_at) > _STOCK_MAP_TTL:
        _cn_stock_map = None  # expire cache
        _cn_stock_reverse_map = None
    if _cn_stock_map is not None:
        return _cn_stock_map
    with _cn_stock_map_lock:
        if _cn_stock_map is not None and (now - _cn_stock_map_loaded_at) <= _STOCK_MAP_TTL:
            return _cn_stock_map
        result: Dict[str, str] = {}
        try:
            import akshare as ak
            # A-share stocks (static list, no anti-crawl issue)
            df = ak.stock_info_a_code_name()
            for _, row in df.iterrows():
                name = str(row.get("name", "")).strip()
                code = str(row.get("code", "")).strip()
                if name and code:
                    result[name] = _normalize_symbol(code)
            stock_count = len(result)
            # ETF / funds
            fund_count = 0
            try:
                fund_df = ak.fund_name_em()
                existing_codes = set(result.values())
                for _, row in fund_df.iterrows():
                    code = str(row.get("基金代码", "")).strip()
                    name = str(row.get("基金简称", "")).strip()
                    if (
                        name
                        and code
                        and len(code) == 6
                        and code.isdigit()
                        and code.startswith(("5", "15", "16", "18"))
                    ):
                        normalized = _normalize_symbol(code)
                        if normalized not in existing_codes:
                            result[name] = normalized
                            existing_codes.add(normalized)
                fund_count = len(result) - stock_count
            except Exception as fe:
                _log(f"[StockMap] ETF/fund load skipped: {fe}")
            _cn_stock_map = result
            _cn_stock_reverse_map = {code: name for name, code in result.items()}
            _cn_stock_map_loaded_at = now
            _log(f"[StockMap] Loaded {stock_count} stocks + {fund_count} ETFs/funds = {len(result)} total.")
        except Exception as e:
            _log(f"[StockMap] Failed to load: {e}")
            if _cn_stock_map is None:
                _cn_stock_map = {}
                _cn_stock_reverse_map = {}
    return _cn_stock_map


def _get_reverse_stock_map() -> Dict[str, str]:
    """Return code→name mapping."""
    _load_cn_stock_map()
    return dict(_cn_stock_reverse_map or {})


def _get_reverse_stock_map_cached_only() -> Dict[str, str]:
    """Return code→name mapping only from already-warmed cache.

    For list pages we prefer a fast response over blocking on a cold AkShare lookup.
    When the cache is cold we simply return an empty mapping and let the UI fall back
    to stock codes. Search endpoints can still call _load_cn_stock_map() explicitly.
    """
    if _cn_stock_map is None:
        return {}
    return {code: name for name, code in _cn_stock_map.items()}


def _get_cn_stock_map_cached_only() -> Dict[str, str]:
    """Return name→code entries without triggering a remote cold load."""
    return dict(_cn_stock_map or {})


def _search_cn_stock_by_name(query: str) -> Optional[str]:
    """Look up A-share stock code by company name (exact then partial match)."""
    query = query.strip()
    if not query:
        return None
    stock_map = _load_cn_stock_map()
    # 1. Exact match
    if query in stock_map:
        return stock_map[query]
    # 2. Partial match: query is substring of a stock name or vice versa
    candidates = [(name, code) for name, code in stock_map.items()
                  if query in name or name in query]
    if len(candidates) == 1:
        return candidates[0][1]
    # 3. If multiple partial matches, pick the one with shortest name (closest match)
    if candidates:
        candidates.sort(key=lambda x: len(x[0]))
        return candidates[0][1]
    return None


def _extract_cn_symbol_from_query(text: str) -> Optional[str]:
    """Resolve exactly one A-share/fund name mentioned in a free-form query.

    Ambiguous multi-instrument queries fail closed instead of silently choosing
    one symbol. This runs before job/report creation so every downstream
    artifact has an authoritative non-empty instrument key.
    """
    matches = _extract_cn_symbols_from_query(text)
    if len(matches) != 1:
        return None
    symbol = next(iter(matches))
    if not _is_supported_cn_analysis_symbol(symbol):
        return None
    return symbol


def _extract_cn_symbols_from_query(
    text: str,
    *,
    stock_map: Optional[Dict[str, str]] = None,
) -> set[str]:
    """Return every explicitly mentioned local instrument name.

    Nested aliases are collapsed only when they occupy the same source span.
    A separate shorter mention therefore remains visible and makes a
    multi-instrument request fail closed.
    """
    query = str(text or "").strip()
    if not query:
        return set()
    name_matches: List[tuple[str, str, int, int]] = []
    resolved_stock_map = _load_cn_stock_map() if stock_map is None else stock_map
    for name, code in resolved_stock_map.items():
        normalized_name = str(name or "").strip()
        normalized_code = str(code or "").strip().upper()
        if not normalized_name or not normalized_code:
            continue
        for match in re.finditer(re.escape(normalized_name), query):
            name_matches.append(
                (normalized_name, normalized_code, match.start(), match.end())
            )
    maximal_matches = [
        candidate
        for candidate in name_matches
        if not any(
            candidate[2] >= other[2]
            and candidate[3] <= other[3]
            and (candidate[2], candidate[3]) != (other[2], other[3])
            for other in name_matches
        )
    ]
    return {
        code
        for name, code, _, _ in maximal_matches
        if _is_explicit_cn_name_mention(query, name)
    }


def _is_explicit_cn_name_mention(query: str, name: str) -> bool:
    escaped = re.escape(name)
    prefix_context = (
        r"(?:分析|研究|查看|看看|跟踪|关注|评估|比较|对比|持有|买入|卖出|"
        r"加仓|减仓)\s*"
    )
    suffix_context = (
        r"(?:[（(]|的|股票|个股|公司|近期|现在|今日|走势|机会|风险|估值|"
        r"基本面|技术面|业绩|财报|年报|季报|公告|盈利|收入|利润|现金流|"
        r"增长|前景|近况|表现|股价|价格|行情|涨跌|涨了|跌了|涨了吗|跌了吗|"
        r"能买吗|能否买|能不能买|可以买|该不该买|是否应该买|"
        r"值得(?:买|买入|关注|持有|研究)?吗|值不值得(?:买|买入|关注|持有|研究)|"
        r"今天|今日|最近(?:如何|怎么样|怎么看)?|如何|怎样|怎么样|怎么看|和|与|vs\.?|"
        r"[，,。；;！？!?]|$)"
    )
    return bool(
        re.search(prefix_context + escaped, query)
        or re.search(escaped + suffix_context, query)
        or query == name
    )


def _resolve_cn_name_from_text_cached(text: str) -> Optional[str]:
    """[UPSTREAM-081-001] Last-resort deterministic CN name lookup on raw text.

    Used only when the intent LLM failed (rate limit / model offline / network)
    and the regex fast path found no code, so a user typing a plain company
    name ("分析一下 飞沃科技") still gets an analysis instead of a generic
    "cannot identify" error. Stronger than the upstream shortest-name pick:
    - reads only the already-warm stock-name cache (no remote cold load in an
      already-degraded path);
    - reuses the fail-closed single-instrument resolver, so ambiguous
      multi-name mentions return None instead of guessing;
    - non A-share-analysis symbols are rejected by the resolver itself.
    """
    query = str(text or "").strip()
    if not query:
        return None
    try:
        matches = _extract_cn_symbols_from_query(
            query, stock_map=_get_cn_stock_map_cached_only()
        )
    except Exception:
        return None
    if len(matches) != 1:
        return None
    return next(iter(matches))


def _is_supported_cn_analysis_symbol(symbol: str) -> bool:
    normalized = _normalize_symbol(symbol)
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", normalized)
    if not match:
        return False
    code, exchange = match.groups()
    if exchange == "BJ":
        return code.startswith(("4", "8", "920"))
    if exchange == "SH":
        return code.startswith(("5", "6", "9"))
    return code.startswith(("000", "001", "002", "003", "20", "300", "301", "15", "16", "18"))


def _split_watchlist_batch_text(text: str) -> List[str]:
    return [token.strip() for token in re.split(r"[\s,，、；;]+", text.strip()) if token.strip()]


def _resolve_watchlist_identifier(
    raw: str,
    name_to_code: Dict[str, str],
    code_to_name: Dict[str, str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    token = raw.strip()
    if not token:
        return None, None, "输入为空"
    if token in name_to_code:
        symbol = name_to_code[token]
        return symbol, code_to_name.get(symbol, token), None
    symbol = _normalize_symbol(token)
    if symbol in code_to_name:
        return symbol, code_to_name.get(symbol, symbol), None
    return None, None, f"未识别的股票代码或名称: {token}"


_auth_scheme = HTTPBearer(auto_error=False)

FIXED_TEAMS = {
    "Analyst Team": [
        "Market Analyst",
        "Social Analyst",
        "News Analyst",
        "Fundamentals Analyst",
        "Macro Analyst",
        "Smart Money Analyst",
        "Volume Price Analyst",
    ],
    "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
    "Trading Team": ["Trader"],
    "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
    "Portfolio Management": ["Portfolio Manager"],
}
ANALYST_ORDER = ["market", "social", "news", "fundamentals", "macro", "smart_money", "volume_price"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
    "macro": "Macro Analyst",
    "volume_price": "Volume Price Analyst",
    "smart_money": "Smart Money Analyst",
    "bull": "Bull Researcher",
    "bear": "Bear Researcher",
    "Bull_Initial": "Bull Researcher",
    "Bear_Initial": "Bear Researcher",
    "Bull_Rebuttal": "Bull Researcher",
    "Bear_Rebuttal": "Bear Researcher",
    "research_manager": "Research Manager",
    "trader": "Trader",
    "aggressive": "Aggressive Analyst",
    "neutral": "Neutral Analyst",
    "conservative": "Conservative Analyst",
    "portfolio_manager": "Portfolio Manager",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "macro": "macro_report",
    "smart_money": "smart_money_report",
    "volume_price": "volume_price_report",
}

# All analysts always run — each uses its own natural time window
# (technical/funds → short, fundamentals/macro → medium)
def _get_horizon_analysts(horizon: str, available: List[str]) -> List[str]:
    """Return all available analysts regardless of horizon."""
    return list(available)


def _announcements_file() -> Path:
    return Path(__file__).resolve().parent / "announcements.json"


def _load_latest_announcement() -> Optional[Dict[str, Any]]:
    path = _announcements_file()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log(f"[Announcements] Failed to read {path.name}: {exc}")
        return None

    announcements = raw.get("announcements") if isinstance(raw, dict) else raw
    if not isinstance(announcements, list):
        return None

    for item in announcements:
        if not isinstance(item, dict):
            continue
        if item.get("active", True) is False:
            continue
        return item
    return None


class UserContextInput(BaseModel):
    objective: Optional[str] = Field(None, description="用户目标动作，如建仓/加仓/减仓/止损/观察")
    risk_profile: Optional[str] = Field(None, description="风险偏好，如保守/平衡/激进")
    investment_horizon: Optional[str] = Field(None, description="持有周期，如短线/波段/中线")
    cash_available: Optional[float] = Field(None, description="可用资金")
    current_position: Optional[float] = Field(None, description="当前持仓数量")
    current_position_pct: Optional[float] = Field(None, description="当前仓位占比")
    average_cost: Optional[float] = Field(None, description="当前持仓成本")
    max_loss_pct: Optional[float] = Field(None, description="最大容忍亏损百分比")
    constraints: List[str] = Field(default_factory=list, description="用户的硬约束列表")
    user_notes: Optional[str] = Field(None, description="用户补充说明")


class AnalyzeRequest(UserContextInput):
    symbol: str = Field(default="", description="股票代码，如 600519.SH（当 query 包含代码时可省略）")
    trade_date: str = Field(default_factory=cn_today_str, description="交易日期 YYYY-MM-DD")
    selected_analysts: List[str] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals", "macro", "smart_money", "volume_price"]
    )
    config_overrides: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    # When set, triggers intent-driven analysis via streaming dual-horizon path
    query: Optional[str] = Field(default=None, description="自然语言查询，如：分析贵州茅台短线机会")
    horizons: List[str] = Field(default_factory=lambda: ["short"], description="分析周期列表，如 ['short'] 或 ['short','medium']")
    # Pre-parsed intent from _ai_extract_symbol_and_date (avoids second LLM call in _run_job)
    user_intent: Optional[Dict[str, Any]] = Field(default=None, description="预解析的用户意图，由 chat_completions 传入")
    runtime_tier: Optional[str] = Field(default=None, description="运行层级 FAST_RADAR/LIGHT_RESEARCH/FULL_TA [PERF-001]")  # [PERF-001]
    confirmed_full_ta: bool = Field(default=False, description="用户是否确认完整 TA [PERF-001]")  # [PERF-001]
    runtime_profile: Optional[str] = Field(default=None, description="轻量 TA Profile [PERF-002]")  # [PERF-002]


class AnalyzeResponse(BaseModel):
    job_id: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: str
    runtime_tier: str = "FULL_TA"  # [PERF-001]
    runtime_tier_label: str = "完整 TA"  # [PERF-001]
    expected_latency: str = "10-20min"  # [PERF-001]
    runtime_profile: Optional[str] = None  # [PERF-002]
    runtime_profile_label: Optional[str] = None  # [PERF-002]
    enabled_modules: Optional[List[str]] = None  # [PERF-002]


class BatchScheduledTriggerJob(BaseModel):
    item_id: str
    job_id: str
    symbol: str
    name: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: str
    current_position: Optional[float] = None
    average_cost: Optional[float] = None
    waiting_ahead_count: Optional[int] = None
    scheduled_running_count: Optional[int] = None
    scheduled_concurrency_limit: Optional[int] = None


class BatchScheduledTriggerResponse(BaseModel):
    summary: Dict[str, int]
    jobs: List[BatchScheduledTriggerJob]


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    symbol: str
    trade_date: str
    error: Optional[str] = None
    waiting_ahead_count: Optional[int] = None
    scheduled_running_count: Optional[int] = None
    scheduled_concurrency_limit: Optional[int] = None


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(UserContextInput):
    model: Optional[str] = "tradingagents-ashare"
    messages: List[ChatMessage]
    stream: bool = True
    selected_analysts: List[str] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals", "macro", "smart_money", "volume_price"]
    )
    config_overrides: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    runtime_tier: Optional[str] = Field(default=None, description="运行层级 [PERF-004]")  # [PERF-004]
    confirmed_full_ta: bool = Field(default=False, description="是否确认完整 TA [PERF-004]")  # [PERF-004]
    runtime_profile: Optional[str] = Field(default=None, description="TA Profile [PERF-004]")  # [PERF-004]


class KlineResponse(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    candles: List[Dict[str, Any]]


# Report API Models
class ReportCreateRequest(BaseModel):
    symbol: str = Field(..., description="股票代码")
    trade_date: str = Field(..., description="交易日期 YYYY-MM-DD")
    decision: Optional[str] = Field(None, description="交易决策")
    result_data: Optional[Dict[str, Any]] = Field(None, description="完整分析结果")
    # [PLAYBOOK-001] lifecycle_contract — persisted inside result_data until
    # report-specific columns are introduced.
    playbook_stage: Optional[str] = None
    playbook_contract: Dict[str, Any] = Field(default_factory=dict)


class ReportResponse(BaseModel):
    id: str
    user_id: Optional[str]
    symbol: str
    name: Optional[str] = None
    trade_date: str
    status: Literal["pending", "running", "completed", "failed"] = "completed"
    error: Optional[str] = None
    decision: Optional[str]
    direction: Optional[str]
    research_direction: Optional[str] = None
    execution_action: Optional[str] = None
    action_label: Optional[str] = None
    confidence: Optional[int]
    target_price: Optional[float]
    stop_loss_price: Optional[float]
    risk_items: Optional[List[Dict[str, Any]]] = None
    key_metrics: Optional[List[Dict[str, Any]]] = None
    analyst_traces: Optional[List[Dict[str, Any]]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    waiting_ahead_count: Optional[int] = None
    scheduled_running_count: Optional[int] = None
    scheduled_concurrency_limit: Optional[int] = None
    data_blockers: Optional[List[Dict[str, Any]]] = None
    data_blocker_summary: Optional[Dict[str, Any]] = None
    # [REPORT-UX-003] wait_reason_codes — explainable codes for WAIT actions.
    wait_reason_codes: Optional[List[str]] = None
    wait_reason_labels: Optional[Dict[str, str]] = None
    # [KB-011] knowledge_contract_ui — KB fields surfaced at top level so the
    # frontend can render "本地知识补充 / 研报关注度" without digging into
    # result_data. These are attached by ``_attach_report_data_blockers_for_response``
    # and were previously dropped because the Pydantic model did not declare them.
    # [KB-003] local knowledge block (markdown) + summary (dict shape).
    local_knowledge_block: Optional[str] = None
    local_knowledge_summary: Optional[Dict[str, Any]] = None
    # [KB-008] research attention — flat fields mirrored from result_data.
    research_attention_score: Optional[float] = None
    knowledge_theme_count: Optional[int] = None
    research_attention_summary: Optional[str] = None
    research_attention_block: Optional[str] = None
    # [HY-004] half_year_report_block — surfaced at top level so the frontend
    # can render "半年报事实对照" without digging into result_data. Attached
    # by ``_attach_report_data_blockers_for_response``; read-only with
    # respect to the strong action gate.
    half_year_facts_block: Optional[str] = None
    half_year_facts_summary: Optional[Dict[str, Any]] = None
    half_year_facts_status: Optional[str] = None
    # [PLAYBOOK-001] lifecycle_contract — optional playbook stage + summary
    # dict for TA report passthrough. Populated best-effort from result_data
    # by report_service; defaults to None/empty so old reports are unaffected.
    # Unknown stages are never coerced to "hold".
    playbook_stage: Optional[str] = None
    playbook_summary: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_report_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class ReportDetailResponse(ReportResponse):
    market_report: Optional[str]
    sentiment_report: Optional[str]
    news_report: Optional[str]
    fundamentals_report: Optional[str]
    macro_report: Optional[str]
    smart_money_report: Optional[str]
    volume_price_report: Optional[str]
    game_theory_report: Optional[str]
    investment_plan: Optional[str]
    trader_investment_plan: Optional[str]
    final_trade_decision: Optional[str]
    result_data: Optional[Dict[str, Any]]


class ReportSummaryResponse(BaseModel):
    """Lightweight report row returned by summary/list endpoints.

    Summary queries deliberately defer ``result_data``.  Keep lifecycle fields
    on detail responses until they have a persisted storage column, instead of
    turning a history list into an N+1 detail fetch.
    """

    id: str
    user_id: Optional[str]
    symbol: str
    name: Optional[str] = None
    trade_date: str
    status: Literal["pending", "running", "completed", "failed"] = "completed"
    error: Optional[str] = None
    decision: Optional[str]
    direction: Optional[str]
    research_direction: Optional[str] = None
    execution_action: Optional[str] = None
    action_label: Optional[str] = None
    confidence: Optional[int]
    target_price: Optional[float]
    stop_loss_price: Optional[float]
    risk_items: Optional[List[Dict[str, Any]]] = None
    key_metrics: Optional[List[Dict[str, Any]]] = None
    analyst_traces: Optional[List[Dict[str, Any]]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    waiting_ahead_count: Optional[int] = None
    scheduled_running_count: Optional[int] = None
    scheduled_concurrency_limit: Optional[int] = None
    data_blockers: Optional[List[Dict[str, Any]]] = None
    data_blocker_summary: Optional[Dict[str, Any]] = None
    wait_reason_codes: Optional[List[str]] = None
    wait_reason_labels: Optional[Dict[str, str]] = None
    local_knowledge_block: Optional[str] = None
    local_knowledge_summary: Optional[Dict[str, Any]] = None
    research_attention_score: Optional[float] = None
    knowledge_theme_count: Optional[int] = None
    research_attention_summary: Optional[str] = None
    research_attention_block: Optional[str] = None
    # [HY-004] half_year_report_block — summary fields mirrored at top level.
    half_year_facts_block: Optional[str] = None
    half_year_facts_summary: Optional[Dict[str, Any]] = None
    half_year_facts_status: Optional[str] = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_report_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class ReportListResponse(BaseModel):
    total: int
    reports: List[ReportSummaryResponse]


class ReportBatchDeleteRequest(BaseModel):
    report_ids: List[str] = Field(default_factory=list)


class ReportBatchDeleteResponse(BaseModel):
    deleted_ids: List[str]
    missing_ids: List[str]


class LatestReportsBySymbolsRequest(BaseModel):
    symbols: List[str] = Field(default_factory=list)


class LatestReportsBySymbolsResponse(BaseModel):
    reports: List[ReportSummaryResponse]


class FeishuExportResponse(BaseModel):
    """[B-003] Response for Feishu document export."""
    success: bool
    document_url: Optional[str] = None
    document_token: Optional[str] = None
    title: Optional[str] = None
    error: Optional[str] = None


class PortfolioOverviewResponse(BaseModel):
    watchlist: List[dict]
    scheduled: List[dict]
    latest_reports: List[ReportSummaryResponse]
    portfolio_import: Optional[dict] = None


class WatchlistAddRequest(BaseModel):
    text: Optional[str] = None
    symbol: Optional[str] = None


class ScheduledBatchIdsRequest(BaseModel):
    item_ids: List[str] = Field(default_factory=list)


class ScheduledBatchUpdateRequest(BaseModel):
    item_ids: List[str] = Field(default_factory=list)
    is_active: Optional[bool] = None
    horizon: Optional[str] = None
    trigger_time: Optional[str] = None


class AnnouncementItemResponse(BaseModel):
    title: str
    detail: str


class AnnouncementResponse(BaseModel):
    id: str
    tag: Optional[str] = None
    title: str
    summary: Optional[str] = None
    published_at: str
    items: List[AnnouncementItemResponse]
    cta_label: Optional[str] = None
    cta_path: Optional[str] = None


class LatestAnnouncementResponse(BaseModel):
    announcement: Optional[AnnouncementResponse] = None


class UserResponse(BaseModel):
    id: str
    email: str
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    email_report_enabled: bool = True
    wecom_report_enabled: bool = True
    bark_report_enabled: bool = True

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "last_login_at", when_used="json")
    def serialize_user_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class AuthRequestCodeRequest(BaseModel):
    email: str


class AuthVerifyCodeRequest(BaseModel):
    email: str
    code: str


class AuthVerifyCodeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class UserRuntimeConfigResponse(BaseModel):
    llm_provider: str
    deep_think_llm: str
    quick_think_llm: str
    backend_url: str
    max_debate_rounds: int
    max_risk_discuss_rounds: int
    has_api_key: bool = False
    has_wecom_webhook: bool = False
    wecom_webhook_display: Optional[str] = None
    has_bark_url: bool = False
    bark_url_display: Optional[str] = None
    server_fallback_enabled: bool = True
    email_report_enabled: bool = True
    wecom_report_enabled: bool = True
    bark_report_enabled: bool = True
    default_analysts: List[str] = Field(default_factory=lambda: ["market", "social", "news", "fundamentals", "macro", "smart_money", "volume_price"])
    current_api_key_scope: Optional[str] = None
    api_key_scopes: List[str] = Field(default_factory=list)


class UserRuntimeConfigUpdateRequest(BaseModel):
    llm_provider: Optional[str] = None
    deep_think_llm: Optional[str] = None
    quick_think_llm: Optional[str] = None
    backend_url: Optional[str] = None
    max_debate_rounds: Optional[int] = None
    max_risk_discuss_rounds: Optional[int] = None
    email_report_enabled: Optional[bool] = None
    wecom_report_enabled: Optional[bool] = None
    bark_report_enabled: Optional[bool] = None
    api_key: Optional[str] = None
    wecom_webhook_url: Optional[str] = None
    bark_url: Optional[str] = None
    clear_api_key: bool = False
    clear_wecom_webhook: bool = False
    clear_bark_url: bool = False
    warmup: bool = True
    force_warmup: bool = False
    default_analysts: Optional[List[str]] = None


class UserRuntimeWarmupRequest(UserRuntimeConfigUpdateRequest):
    prompt: str = "你好"


class RuntimeWarmupResult(BaseModel):
    model: str
    targets: List[str] = Field(default_factory=list)
    content: Optional[str] = None
    error: Optional[str] = None


class ModelApiCatalogItem(BaseModel):
    id: str
    label: str
    provider: str
    protocol: str
    base_url: str = ""
    quick_model: str = ""
    deep_model: str = ""
    key_scope: str
    status: str
    cost_note: str = ""


class ModelApiCatalogResponse(BaseModel):
    version: str
    items: List[ModelApiCatalogItem]


class SourceCapabilityMatrixItem(BaseModel):
    # [DATA-023] source_capability_matrix
    data_type: str
    label_cn: str = ""
    primary_vendor: str
    primary_endpoint: str
    is_primary_confirmed: bool = False
    fallback_vendor: str = ""
    fallback_chain: List[str] = []
    freshness: str
    unit: str = ""
    fields: List[str] = []
    rate_limit_risk: str = ""
    known_limits: str = ""
    status_semantics: str = ""
    source_count: int = 0
    notes: str = ""


class SourceCapabilityMatrixResponse(BaseModel):
    # [DATA-023] source_capability_matrix
    version: str
    items: List[SourceCapabilityMatrixItem]
    freshness_legend: Dict[str, str] = {}
    rate_limit_legend: Dict[str, str] = {}
    # [DATA-025] free_research_report_sources — 只读 supplement, 默认空 dict
    research_report_free_sources: Dict[str, Any] = {}


class UserRuntimeWarmupResponse(BaseModel):
    prompt: str
    results: List[RuntimeWarmupResult]


class WecomWebhookWarmupRequest(BaseModel):
    wecom_webhook_url: Optional[str] = None
    content: Optional[str] = None


class WecomWebhookWarmupResponse(BaseModel):
    sent: bool = True
    message: str
    webhook_display: Optional[str] = None


class BarkWarmupRequest(BaseModel):
    bark_url: Optional[str] = None
    content: Optional[str] = None


class BarkWarmupResponse(BaseModel):
    sent: bool = True
    message: str
    bark_url_display: Optional[str] = None


class PortfolioPositionItem(BaseModel):
    symbol: str = Field(..., description="股票代码，如 600519.SH 或 600519")
    name: Optional[str] = Field(None, description="股票名称")
    current_position: Optional[float] = Field(None, description="持仓数量")
    available_position: Optional[float] = Field(None, description="可用数量")
    average_cost: Optional[float] = Field(None, description="成本价")
    market_value: Optional[float] = Field(None, description="市值")
    current_position_pct: Optional[float] = Field(None, description="仓位占比 %")


class PortfolioImportSyncRequest(BaseModel):
    positions: List[PortfolioPositionItem] = Field(..., description="持仓列表")
    source: str = Field("manual", description="持仓来源标识")
    auto_apply_scheduled: bool = Field(False, description="是否自动将持仓股票加入定时任务")


# [TRACK-009] holdings_import_contract — dry-run / text-import request models
class PortfolioImportDryRunRequest(BaseModel):
    text: Optional[str] = Field(None, description="持仓文本，支持 JSON / CSV / TSV / whitespace；与 positions 二选一")
    positions: Optional[List[PortfolioPositionItem]] = Field(None, description="结构化持仓列表；与 text 二选一")
    source: str = Field("manual", description="持仓来源标识，用于 dry-run 差异对比")


class PortfolioImportTextRequest(BaseModel):
    text: str = Field(..., description="持仓文本，支持 JSON / CSV / TSV / whitespace")
    source: str = Field("manual", description="持仓来源标识")
    auto_apply_scheduled: bool = Field(False, description="是否自动将持仓股票加入定时任务")


class UserTokenResponse(BaseModel):
    id: str
    name: str
    token: str
    token_hint: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "last_used_at", when_used="json")
    def serialize_token_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class UserTokenListItem(BaseModel):
    """Token info for list endpoint — never exposes the full token."""
    id: str
    name: str
    token_hint: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "last_used_at", when_used="json")
    def serialize_token_datetimes(self, value: Optional[datetime]) -> Optional[str]:
        return _serialize_datetime_utc(value)


class UserTokenCreateRequest(BaseModel):
    name: str


def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _user_config_overrides(user_id: Optional[str], db: Optional[Session] = None) -> Dict[str, Any]:
    if not user_id:
        return {}

    def _query(sess: Session) -> Dict[str, Any]:
        user_cfg = auth_service.get_user_llm_config(sess, user_id)
        if not user_cfg:
            return {}
        result: Dict[str, Any] = {}
        for key in (
            "llm_provider",
            "backend_url",
            "quick_think_llm",
            "deep_think_llm",
            "max_debate_rounds",
            "max_risk_discuss_rounds",
        ):
            value = getattr(user_cfg, key, None)
            if value is not None:
                result[key] = value
        api_key = auth_service.get_user_provider_api_key(
            sess,
            user_id,
            result.get("llm_provider"),
            result.get("backend_url"),
        )
        if not api_key and not auth_service.has_user_provider_keys(sess, user_id):
            api_key = auth_service.decrypt_secret_with_fallback(user_cfg.api_key_encrypted)
        if api_key:
            result["api_key"] = api_key
        return result

    if db is not None:
        return _query(db)
    with get_db_ctx() as own_db:
        return _query(own_db)


def _build_runtime_config(overrides: Dict[str, Any], user_id: Optional[str] = None, db: Optional[Session] = None) -> Dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    server_fallback_enabled = os.getenv("ALLOW_SERVER_LLM_FALLBACK", "1").strip().lower() in ("1", "true", "yes", "on")
    config["server_fallback_enabled"] = server_fallback_enabled

    # Security: filter request overrides to allowlist only
    overrides = {k: v for k, v in overrides.items() if k in _CONFIG_OVERRIDES_ALLOWLIST}

    # Apply global config overrides (from PATCH /v1/config)
    if _global_config_overrides:
        config = _deep_merge(config, dict(_global_config_overrides))
    
    # Fetch user specific overrides from DB (pass db to reuse caller's session)
    user_overrides = _user_config_overrides(user_id, db=db)

    # ── Critical: Filter out empty strings before merging ──
    # This prevents an empty DB field from wiping out an Env Var default.
    filtered_user_overrides = {k: v for k, v in user_overrides.items() if v not in (None, "", [])}
    filtered_request_overrides = {k: v for k, v in overrides.items() if v not in (None, "", [])}

    if filtered_user_overrides:
        config = _deep_merge(config, filtered_user_overrides)
    if filtered_request_overrides:
        config = _deep_merge(config, filtered_request_overrides)

    config["llm_provider"] = auth_service.canonicalize_llm_provider(
        config.get("llm_provider"),
        config.get("backend_url"),
    )

    # ── Intelligent fallback between models ──
    # If one is provided but the other is missing (even after env var merge), cross-fill.
    quick = config.get("quick_think_llm")
    deep = config.get("deep_think_llm")

    if not deep and quick:
        config["deep_think_llm"] = quick
    if not quick and deep:
        config["quick_think_llm"] = deep

    # UI/user configs currently expose quick/deep only. When a user switches to
    # another OpenAI-compatible endpoint, keep hidden tiers on the same endpoint
    # by mapping mid/ultra to deep instead of leaking env-level model names.
    if filtered_user_overrides or filtered_request_overrides:
        config["mid_think_llm"] = config.get("deep_think_llm") or config.get("quick_think_llm")
        config["ultra_think_llm"] = config.get("deep_think_llm") or config.get("quick_think_llm")

    if user_id and db is not None and auth_service.has_user_provider_keys(db, user_id):
        config["api_key"] = auth_service.get_user_provider_api_key(
            db,
            user_id,
            config.get("llm_provider"),
            config.get("backend_url"),
        ) or ""

    return config


class RequireUser:
    def __init__(self, allow_api_token: bool = True, update_api_token_usage: bool = True):
        self.allow_api_token = allow_api_token
        self.update_api_token_usage = update_api_token_usage

    def __call__(
        self,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_auth_scheme),
    ) -> UserDB:
        if not credentials:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

        token = credentials.credentials

        with get_db_ctx() as db:
            # 1. 优先尝试 JWT (网页登录)
            try:
                payload = auth_service.decode_access_token(token)
                user_id = str(payload.get("sub") or "")
                user = auth_service.get_user_by_id(db, user_id)
                if user and user.is_active:
                    # expunge 使 ORM 对象脱离 session，close 后仍可访问属性
                    db.expunge(user)
                    return user
            except Exception:
                # 不是有效的 JWT 或已过期，尝试 API Token
                pass

            # 2. 尝试 API Token (仅在允许时)
            if self.allow_api_token and token.startswith(token_service.TOKEN_PREFIX):
                if self.update_api_token_usage:
                    user = token_service.verify_token(db, token)
                else:
                    user = token_service.verify_token_readonly(db, token)
                if user and user.is_active:
                    db.expunge(user)
                    return user

        detail = "身份验证失败或该接口不支持 API Token 访问" if self.allow_api_token else "该接口仅限网页端登录访问"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


# 快捷依赖定义
_require_api_user = RequireUser(allow_api_token=True)    # 允许 API Token
_require_web_user = RequireUser(allow_api_token=False)   # 仅限网页登录
_require_readonly_api_user = RequireUser(
    allow_api_token=True,
    update_api_token_usage=False,
)  # 允许 API Token，但认证过程不写 last_used_at


def _optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_auth_scheme),
) -> Optional[UserDB]:
    if not credentials:
        return None
    try:
        payload = auth_service.decode_access_token(credentials.credentials)
    except Exception:
        return None
    user_id = str(payload.get("sub") or "")
    if not user_id:
        return None
    with get_db_ctx() as db:
        user = auth_service.get_user_by_id(db, user_id)
        if user:
            db.expunge(user)
        return user


def _set_job(job_key: str, **kwargs) -> None:
    # Callers may pass job_id=<value> as a stored field.  Since
    # store.set_job()'s first positional param is also called job_id,
    # we must strip it from kwargs to avoid a "got multiple values" TypeError.
    # _get_job() always injects job_id back into the returned dict.
    kwargs.pop("job_id", None)
    get_job_store().set_job(job_key, **kwargs)


def _get_job(job_key: str) -> Dict[str, Any]:
    d = get_job_store().get_job(job_key)
    if d:
        d.setdefault("job_id", job_key)
    return d


def _emit_job_event(job_id: str, event: str, data: Dict[str, Any]) -> None:
    get_job_store().emit_event(job_id, event, data)


def _attach_job_runtime_state(target: Any, job_id: Optional[str]) -> Any:
    if not job_id:
        return target
    job = _get_job(job_id)
    if not job:
        return target

    for field in ("waiting_ahead_count", "scheduled_running_count", "scheduled_concurrency_limit"):
        value = job.get(field)
        if value is not None or hasattr(target, field):
            setattr(target, field, value)
    return target


def _extract_request_user_context(request: UserContextInput) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key in USER_CONTEXT_KEYS:
        value = getattr(request, key, None)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if key == "constraints" and not value:
            continue
        payload[key] = value
    return payload


def _merge_user_context_payload(
    explicit_context: Dict[str, Any],
    inferred_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = normalize_user_context(inferred_context or {})
    merged.update(normalize_user_context(explicit_context or {}))
    return merged


def _compose_analysis_user_context(
    db: Session,
    user_id: str,
    symbol: str,
    *,
    explicit_context: Optional[Dict[str, Any]] = None,
    inferred_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    imported_context = _build_manual_imported_user_context(db, user_id, symbol)
    merged_with_imported = _merge_user_context_payload(inferred_context or {}, imported_context)
    return _merge_user_context_payload(explicit_context or {}, merged_with_imported)


def _apply_user_context_to_request(request: "AnalyzeRequest", user_context: Dict[str, Any]) -> "AnalyzeRequest":
    request.objective = user_context.get("objective")
    request.risk_profile = user_context.get("risk_profile")
    request.investment_horizon = user_context.get("investment_horizon")
    request.cash_available = user_context.get("cash_available")
    request.current_position = user_context.get("current_position")
    request.current_position_pct = user_context.get("current_position_pct")
    request.average_cost = user_context.get("average_cost")
    request.max_loss_pct = user_context.get("max_loss_pct")
    request.constraints = user_context.get("constraints", [])
    request.user_notes = user_context.get("user_notes")
    return request


def _build_result_payload(final_state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": final_state.get("company_of_interest"),
        "trade_date": final_state.get("trade_date"),
        "direction": None,
        "instrument_context": final_state.get("instrument_context"),
        "market_context": final_state.get("market_context"),
        "user_context": final_state.get("user_context"),
        "workflow_context": final_state.get("workflow_context"),
        "market_report": final_state.get("market_report"),
        "sentiment_report": final_state.get("sentiment_report"),
        "news_report": final_state.get("news_report"),
        "fundamentals_report": final_state.get("fundamentals_report"),
        "macro_report": final_state.get("macro_report"),
        "smart_money_report": final_state.get("smart_money_report"),
        "volume_price_report": final_state.get("volume_price_report"),
        "game_theory_report": final_state.get("game_theory_report"),
        "game_theory_signals": final_state.get("game_theory_signals"),
        "analyst_traces": final_state.get("analyst_traces"),
        "investment_plan": final_state.get("investment_plan"),
        "trader_investment_plan": final_state.get("trader_investment_plan"),
        "risk_feedback_state": final_state.get("risk_feedback_state"),
        "metadata": final_state.get("metadata"),
        "trade_quality_check": (final_state.get("metadata") or {}).get("trade_quality_check"),
        "final_trade_decision": final_state.get("final_trade_decision"),
        "analysis_intent": final_state.get("analysis_intent", "watch"),
        "position_context": final_state.get("position_context"),
    }


class AgentProgressTracker:
    # 阶段标题映射
    STAGE_TITLES = {
        "market_analysis": "市场分析完成",
        "sentiment_analysis": "舆情分析完成",
        "news_analysis": "新闻分析完成",
        "fundamentals_analysis": "基本面分析完成",
        "research_decision": "研究团队决策",
        "trader_plan": "交易计划制定",
        "risk_assessment": "风险评估完成",
        "final_decision": "最终决策",
    }
    
    def __init__(self, selected_analysts: List[str], job_id: str, horizon: Optional[str] = None):
        self.job_id = job_id
        self.horizon = horizon
        self.selected_analysts = [a.lower() for a in selected_analysts]
        self.status: Dict[str, str] = {}
        self.start_times: Dict[str, float] = {}  # 记录每个 agent 开始时间
        self.report_sections: Dict[str, Optional[str]] = {
            "market_report": None,
            "sentiment_report": None,
            "news_report": None,
            "fundamentals_report": None,
            "macro_report": None,
            "smart_money_report": None,
            "volume_price_report": None,
            "game_theory_report": None,
            "investment_plan": None,
            "trader_investment_plan": None,
            "final_trade_decision": None,
        }
        # 跟踪已完成的阶段，避免重复发送里程碑
        self._completed_stages: set = set()
        # 跟踪已发送的 writing 状态，避免重复发送
        self._writing_status_sent: set = set()
        
        for team_agents in FIXED_TEAMS.values():
            for agent in team_agents:
                self.status[agent] = "pending"

        # 未选中的分析师标记为 skipped（仍展示，便于固定 12-agent 看板）
        for key in ANALYST_ORDER:
            agent = ANALYST_AGENT_NAMES[key]
            if key not in self.selected_analysts:
                self.status[agent] = "skipped"

    def _emit_milestone(self, stage: str, summary: str = "") -> None:
        """发送用户可见的里程碑事件"""
        if stage in self._completed_stages:
            return
        self._completed_stages.add(stage)
        
        title = self.STAGE_TITLES.get(stage, stage)
        _emit_job_event(
            self.job_id,
            "agent.milestone",
            {
                "stage": stage,
                "title": title,
                "summary": summary,
                "timestamp": _utcnow_iso(),
                "horizon": self.horizon,
            },
        )
        _log(f"[Milestone] {title}: {summary[:100]}...")

    def _emit_report_chunked(self, job_id: str, section: str, content: str) -> None:
        """将报告内容分片发送，直接透传不做人工延迟
        
        按较大块分片（如按段落），让前端自然渲染
        """
        # 按段落分割，保持Markdown结构
        paragraphs = content.split('\n\n')
        
        for i, para in enumerate(paragraphs):
            if not para.strip():
                continue
                
            _emit_job_event(
                job_id,
                "agent.report.chunk",
                {
                    "section": section,
                    "chunk": para + '\n\n',
                    "index": i,
                    "is_complete": False,
                    "horizon": self.horizon,
                },
            )
        
        # 发送完成标记
        _emit_job_event(
            job_id,
            "agent.report.chunk",
            {
                "section": section,
                "chunk": "",
                "index": -1,
                "is_complete": True,
                "horizon": self.horizon,
            },
        )

    def snapshot(self) -> Dict[str, Any]:
        agents = []
        for team, members in FIXED_TEAMS.items():
            for m in members:
                agents.append({"team": team, "agent": m, "status": self.status.get(m, "pending")})
        return {"agents": agents, "horizon": self.horizon}

    def _set_status(self, agent: str, status: str) -> None:
        prev = self.status.get(agent)
        if prev == status:
            return
        self.status[agent] = status
        
        # 记录时间
        if status == "in_progress":
            self.start_times[agent] = time.time()
        elif status == "completed" and agent in self.start_times:
            duration = time.time() - self.start_times[agent]
            _log(f"[Timer] Agent {agent} ({self.horizon or 'main'}) finished in {duration:.2f}s")

        _emit_job_event(
            self.job_id,
            "agent.status",
            {"agent": agent, "status": status, "previous_status": prev, "horizon": self.horizon},
        )

    def _update_research_team_status(self, status: str) -> None:
        for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
            self._set_status(agent, status)

    def _generate_stage_summary(self, stage: str, chunk: Dict[str, Any]) -> str:
        """根据阶段生成简要总结"""
        if stage == "market_analysis":
            report = chunk.get("market_report", "")
            # 提取关键信息
            if "支撑" in report or "压力" in report:
                return "技术面关键位已识别"
            return "技术面分析完成"
        elif stage == "sentiment_analysis":
            return "舆情数据已收集"
        elif stage == "news_analysis":
            return "新闻影响已评估"
        elif stage == "fundamentals_analysis":
            return "基本面指标已计算"
        elif stage == "research_decision":
            return "多空观点已形成"
        elif stage == "trader_plan":
            return "交易策略已制定"
        elif stage == "risk_assessment":
            return "风险水平已评估"
        elif stage == "final_decision":
            decision = chunk.get("final_trade_decision", "")
            return f"最终建议: {decision[:50]}..." if len(decision) > 50 else f"最终建议: {decision}"
        return ""

    def _emit_writing_status(self, agent_name: str, report_type: str) -> None:
        """发送正在编写报告的状态（每个agent只发送一次）"""
        # 检查是否已经发送过
        status_key = f"{agent_name}:{report_type}"
        if status_key in self._writing_status_sent:
            return
        self._writing_status_sent.add(status_key)
        
        report_names = {
            "market_report": "市场分析",
            "sentiment_report": "舆情分析",
            "news_report": "新闻分析",
            "fundamentals_report": "基本面分析",
            "investment_plan": "投资计划",
            "trader_investment_plan": "交易计划",
            "final_trade_decision": "最终交易决策",
        }
        _emit_job_event(
            self.job_id,
            "agent.writing",
            {
                "agent": agent_name,
                "report": report_type,
                "report_name": report_names.get(report_type, report_type),
                "status": "writing",
                "horizon": self.horizon,
            },
        )

    def _emit_token(self, agent_name: str, report_type: str, token: str) -> None:
        """推送 Token 级别的流式内容（跳过空 token，避免思维模型推理阶段刷屏）"""
        if not token:
            return
        _emit_job_event(
            self.job_id,
            "agent.token",
            {
                "agent": agent_name,
                "report": report_type,
                "token": token,
                "horizon": self.horizon,
            },
        )

    def emit_debate_token(
        self, debate: str, agent: str, round_num: int, token: str,
    ) -> None:
        """推送辩论 token（流式输出，每个 chunk 调用一次）"""
        if not token:
            return
        try:
            _emit_job_event(
                self.job_id,
                "agent.debate.token",
                {
                    "debate": debate,
                    "agent": agent,
                    "round": round_num,
                    "token": token,
                    "horizon": self.horizon,
                },
            )
        except Exception:
            pass

    def emit_debate_message(
        self, debate: str, agent: str, round_num: int,
        content: str, is_verdict: bool = False,
    ) -> None:
        """推送辩论消息（每个 agent 每轮完成后调用一次）"""
        if not content:
            return
        try:
            _emit_job_event(
                self.job_id,
                "agent.debate",
                {
                    "debate": debate,
                    "agent": agent,
                    "round": round_num,
                    "content": content,
                    "is_verdict": is_verdict,
                    "horizon": self.horizon,
                },
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to emit debate message for %s in %s", agent, debate, exc_info=True,
            )

    def apply_chunk(self, chunk: Dict[str, Any]) -> None:
        # 分析师阶段状态推进
        found_active = False
        for analyst_key in ANALYST_ORDER:
            if analyst_key not in self.selected_analysts:
                continue

            agent_name = ANALYST_AGENT_NAMES[analyst_key]
            report_key = ANALYST_REPORT_MAP[analyst_key]
            has_report = bool(chunk.get(report_key))

            if has_report:
                if self.status.get(agent_name) != "completed":
                    self._set_status(agent_name, "completed")
                    self.report_sections[report_key] = chunk.get(report_key)
            elif not found_active:
                # 只在状态从 pending 变为 in_progress 时发送 writing 状态
                prev_status = self.status.get(agent_name)
                if prev_status != "in_progress":
                    self._set_status(agent_name, "in_progress")
                    # 发送正在分析的状态（只发送一次）
                    self._emit_writing_status(agent_name, report_key)
                found_active = True
            else:
                self._set_status(agent_name, "pending")

        # 分析师全部完成后，启动 Bull Researcher
        if not found_active and self.selected_analysts:
            if self.status.get("Bull Researcher") == "pending":
                self._set_status("Bull Researcher", "in_progress")

        # 研究团队状态更新
        debate_state = chunk.get("investment_debate_state") or {}
        bull_hist = str(debate_state.get("bull_history", "")).strip()
        bear_hist = str(debate_state.get("bear_history", "")).strip()
        judge = str(debate_state.get("judge_decision", "")).strip()
        if bull_hist or bear_hist:
            self._update_research_team_status("in_progress")
        if judge:
            self._update_research_team_status("completed")
            if self.status.get("Trader") != "in_progress":
                self._set_status("Trader", "in_progress")
                self._emit_writing_status("Trader", "trader_investment_plan")

        # 交易团队
        if chunk.get("trader_investment_plan"):
            if self.status.get("Trader") != "completed":
                self._set_status("Trader", "completed")
                self._set_status("Aggressive Analyst", "in_progress")

        # 风控与组合团队（发送最终决策）
        risk_state = chunk.get("risk_debate_state") or {}
        risk_judge = str(risk_state.get("judge_decision", "")).strip()

        if risk_judge:
            if self.status.get("Portfolio Manager") != "completed":
                self._set_status("Portfolio Manager", "in_progress")
                self._set_status("Aggressive Analyst", "completed")
                self._set_status("Conservative Analyst", "completed")
                self._set_status("Neutral Analyst", "completed")
                self._set_status("Portfolio Manager", "completed")
                final_summary = self._generate_stage_summary("final_decision", chunk)
                self._emit_milestone("final_decision", final_summary)


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content)


def _generate_tool_description(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """生成工具调用的可读描述"""
    if tool_name == "get_indicators":
        indicator = tool_args.get("indicator")
        if isinstance(indicator, str) and indicator:
            indicator_map = {
                "close_50_sma": "50日均线",
                "close_200_sma": "200日均线",
                "close_10_ema": "10日EMA",
                "close_20_ema": "20日EMA",
                "rsi": "RSI",
                "macd": "MACD",
                "boll": "布林中轨",
                "boll_ub": "布林上轨",
                "boll_lb": "布林下轨",
                "atr": "ATR波动率",
                "vwma": "VWMA量价均线",
                "obv": "OBV能量潮",
            }
            return f"计算 {indicator_map.get(indicator, indicator)}"
        return "获取技术指标"
    elif tool_name == "get_stock_data":
        return "获取股票历史数据"
    elif tool_name == "get_fundamentals":
        metrics = tool_args.get("metrics", [])
        if metrics:
            return f"获取 {', '.join(metrics[:2])}{' 等' if len(metrics) > 2 else ''} 基本面数据"
        return "获取基本面数据"
    elif tool_name == "get_income_statement":
        return "获取利润表"
    elif tool_name == "get_balance_sheet":
        return "获取资产负债表"
    elif tool_name == "get_cash_flow":
        return "获取现金流量表"
    elif tool_name == "get_news":
        return "获取相关新闻"
    elif tool_name == "get_social_sentiment":
        return "获取舆情数据"
    return f"调用 {tool_name}"


async def _run_job(
    job_id: str,
    request: AnalyzeRequest,
    stream_events: bool = False,
    save_report: bool = True,
    user_id: Optional[str] = None,
    request_source: str = "api",
) -> None:
    # 用 asyncio.Task + sleep 竞速代替 wait_for，避免 cancel 卡在 to_thread 导致
    # semaphore 永远不释放的问题。超时后标记失败但不 cancel 内部协程（让线程自然结束）。
    job_timeout = _resolve_job_timeout(request, user_id)
    inner_task = asyncio.create_task(
        _run_job_inner(job_id, request, stream_events, save_report, user_id, request_source)
    )
    done, _ = await asyncio.wait({inner_task}, timeout=job_timeout)
    if inner_task in done:
        # 正常完成（可能成功也可能异常）
        if not inner_task.cancelled() and inner_task.exception():
            _log(f"[Job {job_id}] failed: {inner_task.exception()}")
        return
    # 超时：标记失败，但不 cancel 内部 task（避免 cancel 卡住）
    err_msg = f"任务超时（超过 {job_timeout} 秒），已自动终止"
    _log(f"[Job {job_id}] {err_msg}")
    _set_job(job_id, status="failed", error=err_msg, finished_at=_utcnow_iso())
    # 注意：不能用 asyncio.to_thread 写 DB，因为线程池可能被僵尸任务占满导致死锁。
    # 用同步方式直接写，SQLite 的写入足够快不会阻塞事件循环。
    try:
        with get_db_ctx() as db:
            report_service.mark_report_failed(db, job_id, err_msg)
    except Exception:
        pass
    _emit_job_event(job_id, "job.failed", {"job_id": job_id, "error": err_msg})


# [UPSTREAM-081-001] 分析失败错误语义：把常见 LLM/网络原始报错翻译成用户能
# 看懂的一句话 + 建议动作。本地在上游基础上加强：拼入的原始错误先脱敏
# （key/token、URL、堆栈换行），用户可见文案绝不泄漏凭据、服务地址或内部堆栈。
_ANALYSIS_ERROR_HINTS: List[tuple] = [
    (r"Insufficient Balance|Error code: 402",
     "您配置的大模型 API Key 余额不足。请前往模型服务商充值，或在「设置」中更换其他模型。"),
    (r"DataInspectionFailed|sensitive words detect|data_inspection",
     "模型服务商的内容安全审查拦截了本次分析输出。请重试一次；若频繁出现，建议在「设置」中更换其他模型服务商。"),
    (r"Error code: 429|too.?many.?requests|throttling|rate.?limit",
     "模型服务限流（请求过于频繁或额度受限）。请稍后重试，或在「设置」中更换模型。"),
    (r"Error code: 401|Authorization Failed|invalid.*api.?key|authentication",
     "模型 API Key 无效或已过期。请在「设置」中检查 API Key 配置并点击「测试」验证。"),
    (r"Unsupported model|invalid_parameter.*model|model.*not.*(exist|found)",
     "配置的模型名称不被服务商支持（可能已下线或改名）。请在「设置」中更换模型名称。"),
    (r"Error code: 5\d\d|overloaded|InternalError|upload file failed",
     "模型服务端暂时故障。请稍后重试；若持续失败，建议在「设置」中更换模型。"),
    (r"Connection error|peer closed connection|Request timed out|timed?.?out|ConnectTimeout|ConnectError|GetAddrInfoError|NameResolutionError|Connection refused",
     "连接模型服务失败（网络波动或服务不可达）。请稍后重试，并确认「设置」中的 Base URL 配置正确。"),
]

_ERROR_URL_RE = re.compile(
    r"https?://\S+|www\.\S+|\b[A-Za-z0-9.-]+\.(?:com|cn|net|org|io|ai|tech|vip)\b(?:\.\S*)?(?:/\S*)?",
    re.IGNORECASE,
)
_ERROR_CREDENTIAL_RE = re.compile(
    # [UPSTREAM-081-001] Authorization header forms: "Authorization: Bearer
    # <jwt>", '"Authorization": "Bearer <jwt>"' (JSON), "authorization=basic
    # xyz" — the credential (dotted JWT included) must be swallowed whole,
    # not just the literal "Authorization: Bearer" prefix.
    r"\bauthorization\b[\"'=:\s]*(?:bearer|basic|token)?[\"'=:\s]*[^\s\"',;)]+"
    # bare "Bearer <credential>" without the header keyword
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"
    # standalone JWT (header.payload.signature)
    r"|\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"
    r"|\bsk-[A-Za-z0-9_\-]{8,}"
    r"|\b(?:api[_-]?key|apikey|token|secret|password)\b[\"'=:\s]+\S+"
    r"|\b[A-Za-z0-9_\-]{32,}\b",
    re.IGNORECASE,
)
_ERROR_MAX_ORIGIN_LEN = 200


def _sanitize_analysis_error_text(err: str) -> str:
    """Redact credentials/URLs/stack continuation from a raw error string.

    Only the first line is kept (exception chains embed multi-line internal
    frames), then URLs and key-like tokens are masked, then the text is
    truncated for display. Stable under repeated application.
    """
    raw = str(err or "").strip()
    if not raw:
        return raw
    first_line = raw.splitlines()[0]
    first_line = _ERROR_URL_RE.sub("<url已隐藏>", first_line)
    first_line = _ERROR_CREDENTIAL_RE.sub("<凭据已隐藏>", first_line)
    return first_line[:_ERROR_MAX_ORIGIN_LEN]


def _humanize_analysis_error(err: str) -> str:
    """[UPSTREAM-081-001] Translate raw LLM/network failures into a user
    actionable hint.

    Recognized errors get a Chinese explanation plus the sanitized original
    (for troubleshooting feedback); unrecognized errors are returned
    sanitized-only. Never leaks keys, URLs, or internal stack frames.
    """
    raw = str(err or "")
    if not raw.strip():
        return raw
    if "（原始错误：" in raw:  # already humanized; keep idempotent
        # [UPSTREAM-081-001] The shortcut must not bypass redaction: an
        # already-humanized payload can still carry URLs/credentials (e.g.
        # composed upstream). Re-sanitize; repeated sanitization is stable.
        return _sanitize_analysis_error_text(raw)
    safe = _sanitize_analysis_error_text(raw)
    for pat, hint in _ANALYSIS_ERROR_HINTS:
        if re.search(pat, raw, re.IGNORECASE):
            return f"{hint}（原始错误：{safe}）"
    return safe


async def _run_job_inner(
    job_id: str,
    request: AnalyzeRequest,
    stream_events: bool = False,
    save_report: bool = True,
    user_id: Optional[str] = None,
    request_source: str = "api",
) -> None:
    job_start_t = time.time()
    # Normalize for logic but keep original for display
    display_name = request.symbol
    normalized_symbol = _normalize_analysis_symbol(request.symbol)

    # ── Step 0: Initialize report in DB (short-lived session) ──
    def _init_and_configure():
        with get_db_ctx() as db:
            try:
                report_service.init_report(
                    db=db,
                    report_id=job_id,
                    symbol=normalized_symbol,
                    trade_date=request.trade_date,
                    user_id=user_id,
                )
                report_service.update_report_partial(db, job_id, status="running")
                db.commit()
            except Exception as e:
                _log(f"CRITICAL: Failed to initialize report in DB: {e}")
        return _build_runtime_config(request.config_overrides, user_id=user_id)

    config = await asyncio.to_thread(_init_and_configure)

    _set_job(job_id, status="running", started_at=_utcnow_iso(), symbol=normalized_symbol)

    _emit_job_event(
        job_id,
        "job.running",
        {
            "job_id": job_id,
            "symbol": normalized_symbol,
            "display_name": display_name,
            "trade_date": request.trade_date
        },
    )
    # Ensure request object uses the normalized symbol for internal logic
    request.symbol = normalized_symbol
    user_context_payload = _extract_request_user_context(request)
    preparsed_user_context = (
        request.user_intent.get("user_context")
        if isinstance(request.user_intent, dict)
        else None
    )
    user_context_payload = _merge_user_context_payload(
        user_context_payload,
        preparsed_user_context,
    )
    tracker = AgentProgressTracker(request.selected_analysts, job_id)
    _emit_job_event(job_id, "agent.snapshot", tracker.snapshot())

    try:
        if request.dry_run:
            result = {
                "mode": "dry_run",
                "symbol": request.symbol,
                "trade_date": request.trade_date,
                "selected_analysts": request.selected_analysts,
                "user_context": user_context_payload,
                "llm_provider": config.get("llm_provider"),
                "data_vendors": config.get("data_vendors"),
            }
            _set_job(
                job_id,
                status="completed",
                result=result,
                decision="DRY_RUN",
                finished_at=_utcnow_iso(),
            )
            _emit_job_event(
                job_id,
                "job.completed",
                {"job_id": job_id, "decision": "DRY_RUN", "result": result},
            )
            return

        _shared_data_collector.ref(request.symbol, request.trade_date)
        graph = TradingAgentsGraph(
            selected_analysts=request.selected_analysts,
            debug=False,
            config=config,
            data_collector=_shared_data_collector,
        )
        final_state: Optional[Dict[str, Any]] = None

        # 强制单周期：多个 horizon 时只取第一个，避免 dual-horizon 双倍开销
        if not request.horizons:
            request.horizons = ["short"]
        elif len(request.horizons) > 1:
            request.horizons = [request.horizons[0]]

        # ── Dual-horizon intent-driven path ──────────────────────────────────
        # Fill missing fields in pre-parsed chat intents, normalize direct API
        # queries, and generate a fallback query when none was supplied.
        generated_query = not request.query
        direct_query_needs_parser = bool(request.query and request.user_intent is None)
        _ensure_query_and_user_intent(
            request,
            request.symbol,
            materialize_missing_intent=not direct_query_needs_parser,
        )
        # The current query can explicitly override a saved holding state.
        # Refresh the payload after normalization so the stale request
        # snapshot captured before this block cannot win the later merge.
        user_context_payload = _extract_request_user_context(request)
        if generated_query:
            _log(f"[auto-query] No query provided, generated default intent for {request.symbol}")

        intent_start_t = time.time()
        ticker = request.symbol or display_name
        if request.query:

            # 优先使用已由 chat_completions 预解析的 intent（单次 LLM），避免二次调用
            if request.user_intent:
                user_intent = dict(request.user_intent)
                user_intent["ticker"] = ticker
                user_intent["horizons"] = request.horizons
            else:
                # 直接 POST /v1/analyze 时的兕底（无预解析 intent）
                _log(f"[auto-query] Calling _parse_intent for query: {request.query}")
                user_intent = await asyncio.to_thread(_parse_intent, request.query, graph.quick_thinking_llm, fallback_ticker=ticker)
                _log(f"[auto-query] _parse_intent done: {user_intent}")
                if not request.horizons:
                    request.horizons = user_intent["horizons"]
                user_intent["horizons"] = request.horizons
                # Preserve the parser's richer fields, then apply deterministic
                # current-query action/position overrides on top.
                request.user_intent = dict(user_intent)
                _ensure_query_and_user_intent(request, request.symbol)
                user_intent = dict(request.user_intent or user_intent)
            _log(f"[Timer] Intent Parsing took {time.time() - intent_start_t:.2f}s")

            inferred_user_context = user_intent.get("user_context") or {}
            user_context_payload = _merge_user_context_payload(
                user_context_payload,
                inferred_user_context,
            )
            user_intent["user_context"] = user_context_payload

            # The API resolved the authoritative instrument before job/report
            # creation. The LLM parser may enrich intent fields but must never
            # redirect an analysis to another ticker.
            parsed_ticker = _normalize_symbol(str(user_intent.get("ticker") or ""))
            if parsed_ticker and parsed_ticker != request.symbol:
                _log(
                    f"[auto-query] Ignoring parser ticker {parsed_ticker}; "
                    f"authoritative request symbol is {request.symbol}"
                )
            ticker = request.symbol
            user_intent["ticker"] = ticker

            # 2. 一次性采集数据，短线/中线共用缓存
            lookback_label = "14天关键行情" if request.horizons == ["short"] else "90天全量行情、财务、新闻、资金"
            _emit_job_event(job_id, "agent.tool_call", {
                "agent": "数据采集", "tool": "data_collector",
                "description": f"预加载 {ticker} 近{lookback_label}数据…",
            })
            _log(f"[DualHorizon] Collecting data for {ticker} {request.trade_date} (horizons={request.horizons})…")
            collect_start_t = time.time()
            await asyncio.to_thread(graph.data_collector.collect, ticker, request.trade_date, horizons=request.horizons)
            _log(f"[Timer] Data Collection step in _run_job took {time.time() - collect_start_t:.2f}s")

            _emit_job_event(job_id, "agent.tool_call", {
                "agent": "数据采集", "tool": "data_collector",
                "description": "数据采集完成，开始多维度分析",
            })

            report_keys = (
                "market_report", "sentiment_report", "news_report", "fundamentals_report",
                "macro_report", "smart_money_report", "volume_price_report",
                "investment_plan", "trader_investment_plan", "final_trade_decision",
            )

            horizon_states: Dict[str, Any] = {}

            async def _process_horizon(horizon: str):
                """Async helper to run analysis for a single horizon."""
                # 根据周期过滤 analyst，共享已采集的数据缓存
                horizon_analysts = _get_horizon_analysts(horizon, request.selected_analysts)
                horizon_graph = TradingAgentsGraph(
                    selected_analysts=horizon_analysts,
                    debug=False,
                    config=config,
                    data_collector=graph.data_collector,
                )

                horizon_label = "短线" if horizon == "short" else "中线"
                _emit_job_event(job_id, "agent.horizon_start", {
                    "horizon": horizon, "label": horizon_label,
                })
                # 每轮重置 tracker，前端进度条重新走一遍
                h_tracker = AgentProgressTracker(horizon_analysts, job_id, horizon=horizon)
                _emit_job_event(job_id, "agent.snapshot", h_tracker.snapshot())
                # 告知前端本轮参与的 analyst 即将开始
                for analyst_key in ANALYST_ORDER:
                    if analyst_key in horizon_analysts:
                        aname = ANALYST_AGENT_NAMES[analyst_key]
                        h_tracker._set_status(aname, "in_progress")
                        h_tracker._emit_writing_status(aname, ANALYST_REPORT_MAP[analyst_key])

                h_args = horizon_graph.propagator.get_graph_args()

                # Use thread_id for LangGraph checkpointer persistence
                if "config" not in h_args:
                    h_args["config"] = {}
                h_args["config"]["configurable"] = {"thread_id": f"{job_id}_{horizon}"}

                init_state = horizon_graph.propagator.create_initial_state(
                    ticker, request.trade_date,
                    user_context=user_context_payload,
                    selected_analysts=horizon_analysts,
                    request_source=request_source,
                    user_intent=user_intent, horizon=horizon,
                )
                # [E-006] 确保raw_evidence写入metadata，与propagate/propagate_async一致
                init_state["metadata"]["raw_evidence"] = graph.data_collector.build_raw_evidence(
                    ticker, request.trade_date
                )
                last_report: Dict[str, str] = {}
                seen: Dict[str, bool] = {}   # 追踪哪些字段已出现过，避免重复事件
                horizon_final = None

                # DB 更新使用短生命周期 session，避免长期占用连接池
                def _horizon_partial_update(updates: dict):
                    with get_db_ctx() as _hdb:
                        report_service.update_report_partial(_hdb, job_id, **updates)

                # 通过 ContextVar 将 tracker 传入 async 节点（LangGraph 不传递 schema 外的字段）
                _tracker_token = current_tracker_var.set(h_tracker)
                try:
                    async for chunk in horizon_graph.graph.astream(init_state, **h_args):
                        horizon_final = chunk

                        # ── 并行感知的状态推进 ──────────────────
                        # 1. 每个 analyst 报告首次出现 → completed
                        for analyst_key in ANALYST_ORDER:
                            if analyst_key not in horizon_analysts:
                                continue
                            rkey = ANALYST_REPORT_MAP[analyst_key]
                            aname = ANALYST_AGENT_NAMES[analyst_key]
                            if chunk.get(rkey) and not seen.get(rkey):
                                seen[rkey] = True
                                h_tracker._set_status(aname, "completed")

                        # 2. 分析师全部完成后 → Bull/Bear/ResearchManager 开始
                        all_analysts_done = all(
                            seen.get(ANALYST_REPORT_MAP.get(a, "")) for a in h_tracker.selected_analysts
                        )
                        if all_analysts_done and not seen.get("_research_started"):
                            seen["_research_started"] = True
                            h_tracker._set_status(ANALYST_AGENT_NAMES["bull"], "in_progress")
                            h_tracker._set_status(ANALYST_AGENT_NAMES["bear"], "in_progress")
                            h_tracker._set_status(ANALYST_AGENT_NAMES["research_manager"], "in_progress")

                        # 3. research judge → 研究团队完成, Trader 开始
                        debate = chunk.get("investment_debate_state") or {}
                        if debate.get("judge_decision") and not seen.get("judge_decision"):
                            seen["judge_decision"] = True
                            for r_key in ["bull", "bear", "research_manager"]:
                                h_tracker._set_status(ANALYST_AGENT_NAMES[r_key], "completed")
                            h_tracker._set_status(ANALYST_AGENT_NAMES["trader"], "in_progress")
                            h_tracker._emit_writing_status(ANALYST_AGENT_NAMES["trader"], "trader_investment_plan")

                        # 4. trader plan → Trader completed, 风控开始
                        if chunk.get("trader_investment_plan") and not seen.get("trader_investment_plan"):
                            seen["trader_investment_plan"] = True
                            h_tracker._set_status(ANALYST_AGENT_NAMES["trader"], "completed")
                            h_tracker._set_status(ANALYST_AGENT_NAMES["aggressive"], "in_progress")

                        # 5. risk judge → 风控全部完成
                        risk = chunk.get("risk_debate_state") or {}
                        if risk.get("judge_decision") and not seen.get("risk_judge_decision"):
                            seen["risk_judge_decision"] = True
                            for r_key in ["aggressive", "neutral", "conservative", "portfolio_manager"]:
                                h_tracker._set_status(ANALYST_AGENT_NAMES[r_key], "completed")
                        # ── end 并行感知 ────────────────────────────────────────────

                        # 报告分片推送与数据库即时更新
                        db_updates = {}
                        for key in report_keys:
                            value = chunk.get(key)
                            if value and value != last_report.get(key):
                                last_report[key] = value
                                db_updates[key] = str(value)
                                h_tracker._emit_report_chunked(job_id, key, str(value))

                        if db_updates:
                            await asyncio.to_thread(_horizon_partial_update, db_updates)
                except Exception as e:
                    _log(
                        f"Error during horizon streaming ({horizon}): {e!r}\n"
                        f"{traceback.format_exc()}"
                    )
                    raise
                finally:
                    current_tracker_var.reset(_tracker_token)

                horizon_states[horizon] = horizon_final
                for agent, st in h_tracker.status.items():
                    if st not in ("completed", "skipped"):
                        h_tracker._set_status(agent, "completed")
                _emit_job_event(job_id, "agent.horizon_done", {"horizon": horizon})

            # 3. 按解析出的 horizons 并行运行 astream()，事件实时推给前端
            results = await asyncio.gather(
                *[_process_horizon(h) for h in request.horizons],
                return_exceptions=True,
            )
            horizon_errors = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    tb = "".join(traceback.format_exception(type(r), r, r.__traceback__))
                    _log(f"Horizon '{request.horizons[i]}' failed: {r!r}\n{tb}")
                    horizon_errors.append(f"{request.horizons[i]}: {r}")
            if horizon_errors:
                raise RuntimeError(f"Horizon analysis failed: {'; '.join(horizon_errors)}")

            short_r = graph._build_horizon_result("short", horizon_states.get("short") or {})
            medium_r = graph._build_horizon_result("medium", horizon_states.get("medium") or {})
            primary_r = short_r if horizon_states.get("short") else medium_r
            decision = graph.process_signal(
                primary_r.get("final_trade_decision", ""),
                has_position=_resolve_has_position(user_intent, request),
            ) or "UNKNOWN"

            # E-009: Add override disclaimer when execution layer overrides VERDICT
            ftd = primary_r.get("final_trade_decision", "")
            if decision in ("HOLD", "WAIT") and ftd:
                from tradingagents.graph.signal_processing import _execution_layer_overrides_hold
                if _execution_layer_overrides_hold(ftd):
                    # Detect upstream direction for more specific disclaimer
                    upstream_buy = any(k in ftd for k in ["买入", "看多", "偏多", "BUY"])
                    upstream_sell = any(k in ftd for k in ["卖出", "看空", "偏空", "SELL"])
                    upstream_dir = "买入" if upstream_buy else ("卖出" if upstream_sell else "交易")
                    override_note = (
                        f"\n\n---\n"
                        f"⚠️ **上游{upstream_dir}建议已被最终门禁降级，系统最终动作以 WAIT/等待触发为准。**\n"
                        f"请以「系统动作」和「最终裁决」字段为执行依据，上游中间层建议仅供参考。"
                    )
                    if "系统最终动作以 WAIT/等待触发为准" not in ftd:  # idempotent
                        ftd = ftd + override_note
                        primary_r["final_trade_decision"] = ftd

            result = {
                "symbol": ticker,
                "trade_date": request.trade_date,
                "mode": "dual_horizon",
                "user_intent": user_intent,
                "short_term": short_r,
                "medium_term": medium_r,
                "decision": decision,
                # Hoist primary horizon's report fields to top level so that
                # resolve_report_fields / create_report can find them directly.
                "final_trade_decision": primary_r.get("final_trade_decision", ""),
                "investment_plan": primary_r.get("investment_plan", ""),
                "trader_investment_plan": primary_r.get("trader_investment_plan", ""),
                "market_report": primary_r.get("market_report", ""),
                "sentiment_report": primary_r.get("sentiment_report", ""),
                "news_report": primary_r.get("news_report", ""),
                "fundamentals_report": primary_r.get("fundamentals_report", ""),
                "macro_report": primary_r.get("macro_report", ""),
                "smart_money_report": primary_r.get("smart_money_report", ""),
                "volume_price_report": primary_r.get("volume_price_report", ""),
                "analyst_traces": (
                    short_r.get("analyst_traces", []) + medium_r.get("analyst_traces", [])
                ),
                "analysis_intent": user_intent.get("analysis_intent", "watch"),
                "position_context": user_intent.get("position_context"),
            }
            # [G-006] raw_evidence_snapshot: hoist metadata to top-level
            primary_metadata = primary_r.get("metadata", {})
            if primary_metadata:
                result["metadata"] = primary_metadata
            # LLM 结构化提取（目标价、止损、信心、风险、关键指标）
            # 注意：必须在 _set_job(status="completed") 之前完成，否则 SSE 超时
            # 会因为看到 status="completed" 而提前关闭流，导致 job.completed 事件丢失。
            structured = None
            try:
                structured = await asyncio.to_thread(
                    report_service.extract_structured_data,
                    final_trade_decision=primary_r.get("final_trade_decision", ""),
                    fundamentals_report=primary_r.get("fundamentals_report", ""),
                    config=config,
                )
            except Exception as e:
                _log(f"Structured extraction failed (non-fatal): {e}")

            resolved = await asyncio.to_thread(
                report_service.resolve_report_fields,
                result_data=result,
                confidence_override=structured.confidence if structured else None,
                target_price_override=structured.target_price if structured else None,
                stop_loss_override=structured.stop_loss_price if structured else None,
                has_position=_resolve_has_position(user_intent, request),
            )
            result.update({
                "direction": resolved["direction"],
                "confidence": resolved["confidence"],
                "target_price": resolved["target_price"],
                "stop_loss_price": resolved["stop_loss_price"],
                "research_direction": resolved["research_direction"],
                "execution_action": resolved["execution_action"],
                "action_label": resolved["action_label"],
            })

            # 自动保存报告到数据库
            if save_report:
                def _save_report_sync():
                    with get_db_ctx() as save_db:
                        saved_report = report_service.create_report(
                            db=save_db,
                            symbol=request.symbol,
                            trade_date=request.trade_date,
                            decision=decision,
                            result_data=result,
                            user_id=user_id,
                            risk_items=([r.model_dump() for r in structured.risks] if structured else None),
                            key_metrics=([m.model_dump() for m in structured.key_metrics] if structured else None),
                            confidence_override=result["confidence"],
                            target_price_override=result["target_price"],
                            stop_loss_override=result["stop_loss_price"],
                            report_id=job_id,
                            analyst_traces=result.get("analyst_traces"),
                        )
                        save_db.commit()
                        return saved_report.id

                try:
                    saved_report_id = await asyncio.to_thread(_save_report_sync)
                    _create_tracked_task(
                        _send_report_bark_notification(user_id, saved_report_id, request.symbol, source=request_source),
                        label=f"Bark report notification ({request.symbol})",
                    )
                except Exception as e:
                    _log(f"Failed to save report: {e}")

            # 所有后处理完成后再标记 completed，防止 SSE 超时提前关闭流
            _set_job(
                job_id,
                status="completed",
                result=result,
                decision=decision,
                direction=result["direction"],
                risk_items=[r.model_dump() for r in structured.risks] if structured else [],
                key_metrics=[m.model_dump() for m in structured.key_metrics] if structured else [],
                confidence=result["confidence"],
                target_price=result["target_price"],
                stop_loss_price=result["stop_loss_price"],
                finished_at=_utcnow_iso(),
            )
            _emit_job_event(job_id, "job.completed", {
                "job_id": job_id, "decision": decision,
                "direction": result["direction"],
                "result": result, "mode": "dual_horizon",
                "risk_items": [r.model_dump() for r in structured.risks] if structured else [],
                "key_metrics": [m.model_dump() for m in structured.key_metrics] if structured else [],
                "confidence": result["confidence"],
                "target_price": result["target_price"],
                "stop_loss_price": result["stop_loss_price"],
            })
            _log(f"Job completed successfully: {job_id}")
            _log(f"[Timer] TOTAL Job execution (dual_horizon) took {time.time() - job_start_t:.2f}s")
            return
        # ── End dual-horizon path ─────────────────────────────────────────────

        if stream_events:
            init_state = graph.propagator.create_initial_state(
                request.symbol,
                request.trade_date,
                user_context=user_context_payload,
                selected_analysts=request.selected_analysts,
                request_source=request_source,
            )
            args = graph.propagator.get_graph_args()
            
            # Pass job_id as thread_id for LangGraph checkpointer persistence
            if "config" not in args:
                args["config"] = {}
            args["config"]["configurable"] = {"thread_id": job_id}

            report_keys = (
                "market_report",
                "sentiment_report",
                "news_report",
                "fundamentals_report",
                "macro_report",
                "smart_money_report",
                "volume_price_report",
                "investment_plan",
                "trader_investment_plan",
                "final_trade_decision",
            )
            last_report: Dict[str, str] = {}
            seen: Dict[str, bool] = {}

            _tracker_token = current_tracker_var.set(tracker)
            try:
                async for chunk in graph.graph.astream(init_state, **args):
                    final_state = chunk
                    # ── 并行感知的状态推进 ──────────────────
                    # 1. 每个 analyst 报告首次出现 → completed
                    for analyst_key in ANALYST_ORDER:
                        if analyst_key not in request.selected_analysts:
                            continue
                        rkey = ANALYST_REPORT_MAP[analyst_key]
                        aname = ANALYST_AGENT_NAMES[analyst_key]
                        if chunk.get(rkey) and not seen.get(rkey):
                            seen[rkey] = True
                            tracker._set_status(aname, "completed")

                    # 2. 分析师全部完成 → 研究团队开始
                    all_analysts_done = all(
                        seen.get(ANALYST_REPORT_MAP.get(a, "")) for a in tracker.selected_analysts
                    )
                    if all_analysts_done and not seen.get("_research_started"):
                        seen["_research_started"] = True
                        tracker._set_status(ANALYST_AGENT_NAMES["bull"], "in_progress")
                        tracker._set_status(ANALYST_AGENT_NAMES["bear"], "in_progress")
                        tracker._set_status(ANALYST_AGENT_NAMES["research_manager"], "in_progress")

                    debate = chunk.get("investment_debate_state") or {}
                    if debate.get("judge_decision") and not seen.get("judge_decision"):
                        seen["judge_decision"] = True
                        for r_key in ["bull", "bear", "research_manager"]:
                            tracker._set_status(ANALYST_AGENT_NAMES[r_key], "completed")
                        tracker._set_status(ANALYST_AGENT_NAMES["trader"], "in_progress")

                    if chunk.get("trader_investment_plan") and not seen.get("trader_investment_plan"):
                        seen["trader_investment_plan"] = True
                        tracker._set_status(ANALYST_AGENT_NAMES["trader"], "completed")
                        tracker._set_status(ANALYST_AGENT_NAMES["aggressive"], "in_progress")

                    risk = chunk.get("risk_debate_state") or {}
                    if risk.get("judge_decision") and not seen.get("risk_judge_decision"):
                        seen["risk_judge_decision"] = True
                        for r_key in ["aggressive", "neutral", "conservative", "portfolio_manager"]:
                            tracker._set_status(ANALYST_AGENT_NAMES[r_key], "completed")
                    # ────────────────────────────────────────────

                    # ── Partial DB Persistence & UI Streaming ──
                    db_updates = {}
                    for key in report_keys:
                        value = chunk.get(key)
                        if value and value != last_report.get(key):
                            last_report[key] = value
                            db_updates[key] = str(value)
                            # 立即推送报告分片，前端即可“即产即看”
                            tracker._emit_report_chunked(job_id, key, str(value))
                    
                    if db_updates:
                        def _partial_update(updates=db_updates):
                            with get_db_ctx() as _db:
                                report_service.update_report_partial(_db, job_id, **updates)
                        await asyncio.to_thread(_partial_update)
                    
                    # ── Message & Tool Call Handling ──
                    messages = chunk.get("messages", [])
                    if messages:
                        msg = messages[-1]
                        content = _extract_message_text(getattr(msg, "content", ""))
                        agent_name = getattr(msg, "name", None)
                        msg_type = getattr(msg, "type", "unknown")  # human/system/ai/tool

                        if content:
                            if agent_name:
                                _log(f"[Agent Message] {agent_name}: {content[:200]}...")
                            elif msg_type in ("human", "system"):
                                # Graph 入口的初始 prompt，不是 agent 产出，跳过
                                pass
                            else:
                                _log(f"[Agent Message] {msg_type}: {content[:200]}...")

                        for tool_call in getattr(msg, "tool_calls", []) or []:
                            tool_name = tool_call.get("name", "unknown") if isinstance(tool_call, dict) else getattr(tool_call, "name", "unknown")
                            tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
                            _log(f"[Tool Call] {agent_name or msg_type}: {tool_name}")

                            agent_display = agent_name
                            if not agent_display:
                                tool_to_agent = {
                                    "get_stock_data": "数据获取",
                                    "get_indicators": "技术分析师",
                                    "get_fundamentals": "基本面分析师",
                                    "get_income_statement": "基本面分析师",
                                    "get_balance_sheet": "基本面分析师",
                                    "get_cash_flow": "基本面分析师",
                                    "get_news": "新闻分析师",
                                    "get_social_sentiment": "舆情分析师",
                                }
                                agent_display = tool_to_agent.get(tool_name, "系统")

                            tool_description = _generate_tool_description(tool_name, tool_args)
                            _emit_job_event(
                                job_id,
                                "agent.tool_call",
                                {
                                    "agent": agent_display,
                                    "tool": tool_name,
                                    "description": tool_description,
                                },
                            )
                
            except Exception as e:
                _log(f"Error during default streaming: {e}")
            finally:
                current_tracker_var.reset(_tracker_token)

        if not final_state:
            raise RuntimeError("graph returned empty final state")

        _has_pos = _resolve_final_has_position(final_state, request)
        decision = graph.process_signal(final_state["final_trade_decision"], has_position=_has_pos) or "UNKNOWN"
        result = _build_result_payload(final_state)
        result["decision"] = decision

        # [G-006] raw_evidence_snapshot: ensure legacy path also has raw_evidence
        if not (result.get("metadata") or {}).get("raw_evidence"):
            _ticker_legacy = final_state.get("company_of_interest", "")
            _td_legacy = final_state.get("trade_date", "")
            if _ticker_legacy and _td_legacy and hasattr(graph, 'data_collector'):
                re_data = graph.data_collector.build_raw_evidence(_ticker_legacy, _td_legacy)
                if re_data:
                    result.setdefault("metadata", {})
                    result["metadata"]["raw_evidence"] = re_data

        # 全量收口为 completed/skipped
        for agent, status in tracker.status.items():
            if status not in ("completed", "skipped"):
                tracker._set_status(agent, "completed")

        # LLM 结构化提取（非阻塞，失败不影响主流程）
        # 注意：_set_job(status="completed") 必须在此之后调用，否则 SSE 超时会提前关闭流
        structured = None
        try:
            structured = await asyncio.to_thread(
                report_service.extract_structured_data,
                final_trade_decision=result.get("final_trade_decision", ""),
                fundamentals_report=result.get("fundamentals_report", ""),
                config=config,
            )
        except Exception as e:
            _log(f"Structured extraction failed (non-fatal): {e}")

        # 一次性解析所有字段（方向、信心、目标价等）
        resolved = await asyncio.to_thread(
            report_service.resolve_report_fields,
            result_data=result,
            confidence_override=structured.confidence if structured else None,
            target_price_override=structured.target_price if structured else None,
            stop_loss_override=structured.stop_loss_price if structured else None,
            has_position=_has_pos,
        )

        # 注入结果字典以便通知和保存使用
        result.update({
            "direction": resolved["direction"],
            "confidence": resolved["confidence"],
            "target_price": resolved["target_price"],
            "stop_loss_price": resolved["stop_loss_price"],
            "research_direction": resolved["research_direction"],
            "execution_action": resolved["execution_action"],
            "action_label": resolved["action_label"],
        })

        # 自动保存/收口报告到数据库
        if save_report:
            def _save_report_final_sync():
                with get_db_ctx() as save_db:
                    saved_report = report_service.create_report(
                        db=save_db,
                        symbol=request.symbol,
                        trade_date=request.trade_date,
                        decision=decision,
                        result_data=result,
                        user_id=user_id,
                        risk_items=([r.model_dump() for r in structured.risks] if structured else None),
                        key_metrics=([m.model_dump() for m in structured.key_metrics] if structured else None),
                        confidence_override=result["confidence"],
                        target_price_override=result["target_price"],
                        stop_loss_override=result["stop_loss_price"],
                        report_id=job_id,
                        analyst_traces=result.get("analyst_traces"),
                    )
                    save_db.commit()
                    return saved_report.id

            try:
                saved_report_id = await asyncio.to_thread(_save_report_final_sync)
                _create_tracked_task(
                    _send_report_bark_notification(user_id, saved_report_id, request.symbol, source=request_source),
                    label=f"Bark report notification ({request.symbol})",
                )
            except Exception as e:
                _log(f"Failed to finalize report: {e}")
        # 所有后处理完成后再标记 completed，防止 SSE 超时提前关闭流
        _set_job(
            job_id,
            status="completed",
            result=result,
            decision=decision,
            direction=result["direction"],
            risk_items=[r.model_dump() for r in structured.risks] if structured else [],
            key_metrics=[m.model_dump() for m in structured.key_metrics] if structured else [],
            confidence=result["confidence"],
            target_price=result["target_price"],
            stop_loss_price=result["stop_loss_price"],
            finished_at=_utcnow_iso(),
        )
        _emit_job_event(
            job_id,
            "job.completed",
            {
                "job_id": job_id,
                "decision": decision,
                "direction": result["direction"],
                "result": result,
                "risk_items": [r.model_dump() for r in structured.risks] if structured else [],
                "key_metrics": [m.model_dump() for m in structured.key_metrics] if structured else [],
                "confidence": result["confidence"],
                "target_price": result["target_price"],
                "stop_loss_price": result["stop_loss_price"],
            },
        )
        _log(f"Job completed successfully: {job_id}")
        _log(f"[Timer] TOTAL Job execution (single_horizon) took {time.time() - job_start_t:.2f}s")
    except Exception as exc:
        err_msg = _humanize_analysis_error(f"{type(exc).__name__}: {exc}")
        _set_job(
            job_id,
            status="failed",
            error=err_msg,
            traceback=traceback.format_exc(),
            finished_at=_utcnow_iso(),
        )
        
        # ── Persistent failure recording (short-lived session) ──
        try:
            def _record_failure():
                with get_db_ctx() as err_db:
                    report_service.mark_report_failed(err_db, job_id, f"{err_msg}\n\n{traceback.format_exc()}")
            await asyncio.to_thread(_record_failure)
        except Exception as db_exc:
            _log(f"Failed to record failure in DB: {db_exc}")

        _emit_job_event(
            job_id,
            "job.failed",
            {"job_id": job_id, "error": err_msg},
        )
    finally:
        _shared_data_collector.evict(request.symbol, request.trade_date)


def _normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    hk_match = re.fullmatch(r"(\d{1,5})\.HK", s)
    if hk_match:
        return f"{hk_match.group(1)}.HK"
    # Never let an invalid HK token fall through to the six-digit CN matcher.
    # For example, 123456.HK must be rejected, not silently rewritten to
    # 123456.SH/SZ or truncated to 23456.HK.
    if re.search(r"\d+\.HK\b", s):
        return s
    # Preserve malformed exchange-qualified numeric tokens so the analysis
    # validator can reject them. Falling through would turn "5.SH" into the
    # unrelated generic ticker "SH".
    qualified_cn = re.fullmatch(r"(\d+)\.(SH|SZ|SS|BJ)", s)
    if qualified_cn and len(qualified_cn.group(1)) != 6:
        return s
    # Priority: 6-digit CN stock code
    m = re.search(r"(\d{6})(?:\.(SH|SZ|SS|BJ))?", s)
    if m:
        code = m.group(1)
        suffix = m.group(2)
        if suffix:
            if suffix == "SS":
                return f"{code}.SH"
            return f"{code}.{suffix}"
        if code.startswith(("4", "8", "920")):
            market = "BJ"
        else:
            market = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        return f"{code}.{market}"
    # Fallback: 1-6 letter ticker
    m2 = re.search(r"([A-Z]{1,6}(?:\.[A-Z]{1,3})?)", s)
    if m2:
        return m2.group(1)
        
    # Final Fallback: Check Chinese Name Map (e.g. "三花智控" -> "002050.SZ")
    stock_map = _load_cn_stock_map()
    if s in stock_map:
        return stock_map[s]
        
    return s


def _extract_chat_text(messages: List[ChatMessage]) -> str:
    if not messages:
        return ""
    last = messages[-1]
    return _extract_message_text(last.content)


def _six_digit_span_is_amount_or_quantity(
    text: str,
    start: int,
    end: int,
) -> bool:
    """Exclude six-digit cash/share quantities from bare A-share parsing."""
    prefix = text[max(0, start - 24):start]
    suffix = text[end:end + 24]
    return bool(
        re.search(
            r"(?:现金|预算|资金|可用资金|余额|本金)\s*"
            r"(?:(?:是|为|约(?:为)?|大约(?:为)?|大概(?:为)?|有)\s*)?"
            r"[:：=]?\s*$|"
            r"(?:当前持仓|持仓数量|持股数量|仓位数量)\s*"
            r"(?:(?:是|为|约(?:为)?|大约(?:为)?|大概(?:为)?|有)\s*)?"
            r"[:：=]?\s*$|"
            r"(?:budget|cash|funds?|balance|capital)\s*(?:of\s*)?[:=]?\s*\$?\s*$|"
            r"\$\s*$",
            prefix,
            re.IGNORECASE,
        )
        or re.match(
            r"\s*(?:万?元|块(?:钱)?|人民币|现金|预算|资金|股(?!票)|"
            r"shares?\b|stocks?\b|dollars?\b|USD\b|CNY\b|RMB\b|"
            r"budget\b|cash\b|funds?\b)",
            suffix,
            re.IGNORECASE,
        )
    )


def _six_digit_token_is_amount_or_quantity(text: str, match: re.Match[str]) -> bool:
    return _six_digit_span_is_amount_or_quantity(text, match.start(), match.end())


def _extract_symbol_and_date(text: str) -> tuple[Optional[str], Optional[str]]:
    # Date extraction (flexible boundaries)
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    date = date_match.group(0) if date_match else None

    # Priority 1: Strict exchange-qualified numeric symbols. SH/SZ/BJ always
    # use six digits; accepted HK analysis codes use four or five digits.
    explicit_symbols = {
        _normalize_symbol(match.group(1))
        for match in re.finditer(
            r"(?<![A-Za-z0-9])((?:\d{6}\.(?:SH|SZ|SS|BJ)|\d{4,5}\.HK))"
            r"(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
    }
    # Bare A-share 6-digit code (even if stuck to Chinese characters). Explicit
    # and bare forms are evaluated together so mixed-format comparisons fail
    # closed instead of silently selecting the suffixed code.
    bare_symbols = set()
    for sym_match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", text):
        prefix = text[max(0, sym_match.start() - 12):sym_match.start()]
        suffix = text[sym_match.end():]
        # A numeric token followed by any explicit exchange-like suffix must
        # be handled as one qualified symbol.  An unsupported suffix is not a
        # safe reason to reinterpret the digits as a bare A-share code.
        if re.match(r"\.[A-Za-z]{1,5}\b", suffix):
            continue
        if _six_digit_token_is_amount_or_quantity(text, sym_match) or (
            re.search(
                r"(?:现金|预算|资金|可用资金|余额|本金)\s*[:：=]?\s*$",
                prefix,
            )
            or re.match(
                r"\s*(?:万?元|块(?:钱)?|人民币|现金|预算|资金|股(?!票))",
                suffix,
            )
        ):
            continue
        bare_symbols.add(_normalize_symbol(sym_match.group(1)))
    numeric_symbols = explicit_symbols | bare_symbols

    # Priority 3: explicit US tickers. Match case-insensitively, then rely on
    # placement/context checks so ordinary prose is not promoted to a symbol.
    us_stopwords = {
        "A", "AN", "AND", "ANALYZE", "ANALYSIS", "BUY", "CHECK", "ENTRY", "EXIT",
        "ABOUT", "AS", "BALANCE", "BUDGET", "CAPITAL", "CASH", "FOR",
        "FUNDS", "GIVE",
        "HAVE", "HELP", "HOLD", "I", "IS", "ME", "MY", "NOW", "OF",
        "PLEASE", "SELL", "SHOULD", "TELL", "TODAY",
        "IN", "STOCK", "THE", "THIS", "TO", "WATCH", "WE", "WITH",
        "YOU", "YOUR",
        "EVALUATE", "RESEARCH",
        "EARNINGS", "FUNDAMENTAL", "FUNDAMENTALS", "GROWTH", "MARKET",
        "MOMENTUM", "NEWS", "OUTLOOK", "PRICE", "PROFIT", "REVENUE",
        "REWARD", "RISK", "TECHNICAL", "VALUE", "VALUATION",
    }
    prose_only_stopwords = {
        "A", "ABOUT", "ANALYZE", "ANALYSIS", "AS", "BALANCE", "BUDGET",
        "AT", "BEFORE", "BY", "CAN", "CAPITAL",
        "CASH", "CHECK", "FOR", "FUNDS", "GIVE", "HAVE", "HELP", "NOW", "OF", "PLEASE",
        "AFTER", "FROM", "IN", "INTO", "IT", "ITS", "ME", "ON", "SHOULD",
        "STOCK", "TELL", "THE", "THEM", "THIS", "TO", "TODAY", "WITH", "YOU", "YOUR",
        "EVALUATE", "RESEARCH",
    }
    financial_indicators = {
        "ATR", "CAGR", "DCF", "EBIT", "EBITDA", "EMA", "EPS", "FCF",
        "MACD", "NPV", "OCF", "PB", "PE", "PEG", "PS", "ROA", "ROE",
        "ROI", "RSI", "SMA", "TTM", "VWAP", "VWMA",
    }
    analysis_topic_tokens = {
        "EARNINGS", "FUNDAMENTAL", "FUNDAMENTALS", "GROWTH", "MARKET",
        "MOMENTUM", "NEWS", "OUTLOOK", "PRICE", "PROFIT", "REVENUE",
        "REWARD", "RISK", "TECHNICAL", "VALUE", "VALUATION",
    }
    category_acronyms = {"AI", "CPU", "ETF", "GPU", "LOF", "ST"}
    us_symbols: set[str] = set()
    for us_match in re.finditer(
        r"(?<![A-Za-z0-9])([A-Z][A-Z0-9.\-]{0,10})(?![A-Za-z0-9])",
        text,
        re.IGNORECASE,
    ):
        raw_candidate = us_match.group(1).rstrip(".")
        candidate = raw_candidate.upper()
        immediate_suffix = text[us_match.end():]
        if candidate in {"A", "B", "H"} and immediate_suffix.startswith("股"):
            continue
        if candidate in analysis_topic_tokens and not raw_candidate.isupper():
            continue
        if numeric_symbols and candidate in {
            "A", "B", "H", "ETF", "LOF", "SH", "SZ", "SS", "BJ", "HK",
        }:
            continue
        prefix = text[:us_match.start()].rstrip()
        explicit_context = bool(
            re.search(
                r"(?:(?:请)?(?:分析|研究|查看|看看|跟踪|关注)(?:一下|下)?|"
                r"比较|对比|持有|买入|卖出|加仓|减仓|股票|标的|"
                r"(?:我)?(?:想|要|准备|打算)?(?:买|买入)|能不能买|可以买入?|"
                r"(?:please\s+)?(?:analyze|analysis|research|evaluate|compare|buy|sell|hold|watch|check)|"
                r"ticker|symbol|own|about)"
                r"(?:\s+(?:of|for|the|stock|ticker|symbol))*\s*$",
                prefix,
                re.IGNORECASE,
            )
            or re.search(
                r"(?:I\s+)?own\s+\d+(?:\.\d+)?\s+shares?\s+of\s*$",
                prefix,
                re.IGNORECASE,
            )
            or re.search(
                r"(?:I\s+)?(?:bought|sold|hold|held|own)\s*$",
                prefix,
                re.IGNORECASE,
            )
            or re.search(
                r"(?:I\s+)?(?:want\s+to\s+|plan\s+to\s+)?"
                r"(?:buy|sell)\s+\d+(?:\.\d+)?\s+shares?\s+of\s*$",
                prefix,
                re.IGNORECASE,
            )
            or re.search(r"(?:and|or|vs\.?|versus|和|与|及)\s*$", prefix, re.IGNORECASE)
        )
        explicit_ticker_label = bool(
            re.search(r"(?:ticker|symbol|美股代码|股票代码)\s*$", prefix, re.IGNORECASE)
        )
        standalone_ticker = text.strip().upper() == candidate
        explicit_symbol_context = bool(
            explicit_context or explicit_ticker_label or standalone_ticker
        )
        if candidate in prose_only_stopwords and not (
            raw_candidate.isupper() and explicit_symbol_context
        ):
            continue
        if candidate == "ST" and re.match(r"[\u4e00-\u9fff]", immediate_suffix):
            continue
        if candidate in us_stopwords and not explicit_symbol_context:
            continue
        if candidate in financial_indicators:
            continue
        category_usage = bool(
            re.match(
                r"(?:概念|行业|算力|板块|主题|指数|产业|赛道)",
                immediate_suffix,
            )
        )
        if candidate in category_acronyms and (
            category_usage or not explicit_symbol_context
        ):
            continue
        # Uppercase financial indicators (PE/ROE/RSI/MACD, etc.) are common
        # inside Chinese analysis requests.  Only promote a bare uppercase token
        # to a US symbol when its placement actually identifies the instrument.
        suffix = text[us_match.end():].lstrip()
        starts_query = not prefix and (
            not suffix
            or re.match(r"[A-Za-z0-9]", suffix) is None
            or re.match(r"\d{4}-\d{2}-\d{2}\b", suffix) is not None
        )
        ticker_first_prose = bool(
            not prefix
            and raw_candidate.isupper()
            and len(candidate) >= 2
            and re.match(
                r"(?:is|looks?|seems?|has|had|rose|fell|rises|falls|"
                r"trades?|reports?|announced)\b",
                suffix,
                re.IGNORECASE,
            )
        )
        ticker_first_context = not prefix and bool(
            re.match(
                r"(?:stock\s+)?(?:analysis|outlook|price|chart|news|stock)\b",
                suffix,
                re.IGNORECASE,
            )
        )
        ticker_followed_by_comparator = not prefix and bool(
            re.match(r"(?:and|or|vs\.?|versus)\b", suffix, re.IGNORECASE)
        )
        ticker_follows_list_separator = bool(
            re.search(r"(?:[/／、,，])\s*$", prefix)
        )
        question_ticker_context = bool(
            re.search(
                r"(?:should\s+i\s+(?:hold|buy|sell)|"
                r"what(?:\s+do\s+you\s+think)?\s+(?:about|of)|"
                r"what\s+is\s+the\s+(?:pe|pb|price|valuation)\s+of|"
                r"^is)\s*$",
                prefix,
                re.IGNORECASE,
            )
            or re.match(
                r"(?:overvalued|undervalued|worth\s+(?:buying|holding)|"
                r"a\s+good\s+(?:buy|hold))\b",
                suffix,
                re.IGNORECASE,
            )
        )
        command_target_context = bool(
            re.search(
                r"(?:please\s+)?(?:analyze|research|evaluate|check|watch)\b"
                r"(?:\s+(?:the|stock|ticker|symbol|of|for))*\s*$",
                prefix,
                re.IGNORECASE,
            )
        )
        lowercase_explicit_context = bool(
            raw_candidate.islower()
            and re.fullmatch(
                r"\s*(?:(?:please\s+)?(?:analyze|analysis|ticker|symbol|stock)|"
                r"(?:请)?(?:分析|研究|查看|看看|跟踪|关注)(?:一下|下)?)\s*",
                prefix,
                re.IGNORECASE,
            )
        )
        # Lower/mixed-case plain words are too ambiguous inside prose. Accept
        # them only as a standalone ticker; dotted/hyphenated tickers are
        # distinctive enough when paired with an explicit command.
        lowercase_target_context = bool(
            raw_candidate.islower()
            and (
                explicit_symbol_context
                or question_ticker_context
                or command_target_context
                or ticker_first_context
                or ticker_followed_by_comparator
                or ticker_follows_list_separator
            )
        )
        if (
            not raw_candidate.isupper()
            and not re.search(r"[.\-]", raw_candidate)
            and text.strip().casefold() != raw_candidate.casefold()
            and not lowercase_explicit_context
            and not lowercase_target_context
        ):
            continue
        if (
            not explicit_context
            and text.strip().upper() != candidate
            and not starts_query
            and not ticker_first_prose
            and not ticker_first_context
            and not ticker_followed_by_comparator
            and not ticker_follows_list_separator
            and not question_ticker_context
            and not command_target_context
        ):
            continue
        us_symbols.add(candidate)

    all_symbols = numeric_symbols | us_symbols
    if len(all_symbols) == 1:
        return next(iter(all_symbols)), date

    return None, date


def _resolve_query_symbol_and_date(text: str) -> tuple[Optional[str], Optional[str]]:
    parsed_symbol, parsed_date = _extract_symbol_and_date(text)
    query = str(text or "")
    has_cn_text = bool(re.search(r"[\u4e00-\u9fff]", query))
    cached_stock_map = _get_cn_stock_map_cached_only() if has_cn_text else {}
    name_symbols = (
        _extract_cn_symbols_from_query(query, stock_map=cached_stock_map)
        if cached_stock_map
        else set()
    )
    parsed_latin_name_prefix = bool(
        parsed_symbol
        and re.fullmatch(r"[A-Z][A-Z0-9]{0,10}", parsed_symbol)
        and re.search(
            rf"{re.escape(parsed_symbol)}"
            r"(?=(?!(?:的|风险|收益|估值|走势|价格|股价|行情|机会|"
            r"基本面|技术面|财报|业绩|新闻|公告|能买吗|值得|如何|"
            r"怎么样|怎么看))[\u4e00-\u9fff])",
            query,
            re.IGNORECASE,
        )
    )
    explicit_name_comparison = bool(
        parsed_symbol
        and re.fullmatch(
            r"\d{6}\.(?:SH|SZ|BJ)",
            _normalize_analysis_symbol(parsed_symbol),
        )
        and re.search(
            r"(?:比较|对比|和|与|及|[/／、]|\b(?:compare|vs\.?|versus)\b)",
            query,
            re.IGNORECASE,
        )
    )
    explicit_name_code_pair = _query_has_explicit_cn_name_code_pair(query)
    if has_cn_text and (
        (parsed_symbol is None and _query_plausibly_contains_cn_company_name(query))
        or (parsed_latin_name_prefix and not name_symbols)
        or explicit_name_code_pair
        or explicit_name_comparison
    ):
        # Pure company-name queries (and Latin-prefixed local names such as
        # TCL科技) need the full map. Explicit code/ticker queries do not:
        # their multi-target guard already performs comparison lookup.
        cached_stock_map = _load_cn_stock_map()
        name_symbols = _extract_cn_symbols_from_query(
            query,
            stock_map=cached_stock_map,
        )
    if parsed_symbol and name_symbols and re.fullmatch(
        r"[A-Z][A-Z0-9]{0,10}", parsed_symbol
    ):
        # Local company names may begin with Latin brands (for example
        # TCL科技).  Once the complete local name resolves uniquely, a Latin
        # prefix occupying that same name span is not a second US instrument.
        for local_name, local_symbol in cached_stock_map.items():
            normalized_name = str(local_name or "").strip()
            if (
                str(local_symbol or "").strip().upper() in name_symbols
                and normalized_name in text
                and re.match(
                    rf"{re.escape(parsed_symbol)}(?=[\u4e00-\u9fff])",
                    normalized_name,
                    re.IGNORECASE,
                )
            ):
                parsed_symbol = None
                break
    if parsed_symbol:
        combined = {parsed_symbol, *name_symbols}
        return (next(iter(combined)), parsed_date) if len(combined) == 1 else (None, parsed_date)
    if _query_contains_instrument_code(text):
        return None, parsed_date
    if len(name_symbols) == 1:
        candidate = next(iter(name_symbols))
        if _is_supported_cn_analysis_symbol(candidate):
            return candidate, parsed_date
    return None, parsed_date


def _query_plausibly_contains_cn_company_name(text: str) -> bool:
    """Avoid a remote name-map load for ordinary supplied-symbol commands."""
    query = str(text or "")
    if not re.search(r"[\u4e00-\u9fff]", query):
        return False
    if _query_has_explicit_cn_name_code_pair(query):
        return True

    residual = re.sub(
        r"(?<![A-Za-z0-9])(?:\d{6}(?:\.(?:SH|SZ|SS|BJ))?|\d{4,5}\.HK)"
        r"(?![A-Za-z0-9])",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    generic_phrases = (
        "是否值得买入", "是否值得买", "值不值得买入", "值不值得买",
        "能不能买入", "能不能买", "可以买入吗", "可以买吗", "能买吗",
        "是否应该买入", "是否应该买", "该不该买入", "该不该买",
        "帮我", "请问", "请", "一下", "分析", "研究", "查看", "看看",
        "跟踪", "关注", "评估", "重点看", "重点", "当前", "现在", "今日",
        "今天", "最近", "短线", "中线", "长线", "机会", "风险", "收益",
        "走势", "行情", "价格", "股价", "估值", "技术面", "基本面",
        "财务", "财报", "业绩", "新闻", "公告", "营收", "利润", "现金流",
        "买入", "卖出", "买", "卖", "持有", "加仓", "减仓", "止损", "止盈",
        "怎么样", "怎么看", "如何", "是否", "值得", "股票", "个股", "这只",
        "这支", "我想", "想要", "继续", "给出", "判断", "建议", "的", "吗",
    )
    for phrase in sorted(generic_phrases, key=len, reverse=True):
        residual = residual.replace(phrase, " ")
    residual = re.sub(r"\b(?:PE|PB|ROE|ROA|EPS|MACD|RSI|ETF)\b", " ", residual, flags=re.IGNORECASE)
    residual = re.sub(r"[\d\s，,。；;！？!?：:（）()、/／._+-]+", "", residual)
    return bool(re.search(r"[\u4e00-\u9fff]{2,}", residual))


def _query_has_explicit_cn_name_code_pair(text: str) -> bool:
    """Detect a local company-name label immediately paired with a code.

    Parentheses are common but optional (``贵州茅台 600519.SH`` is also a
    name/code assertion). This only decides whether the authoritative name map
    must be loaded; resolved names and codes are compared by the caller.
    """
    query = str(text or "")
    if re.search(
        r"[\u4e00-\u9fffA-Za-z*ＳＴｓｔ]{2,24}\s*"
        r"[（(]\s*\d{6}(?:\.(?:SH|SZ|SS|BJ))?\s*[）)]",
        query,
        re.IGNORECASE,
    ):
        return True

    for code_match in re.finditer(
        r"(?<!\d)\d{6}(?:\.(?:SH|SZ|SS|BJ))?(?![A-Za-z0-9])",
        query,
        re.IGNORECASE,
    ):
        if _six_digit_token_is_amount_or_quantity(query, code_match):
            continue
        clause_prefix = re.split(r"[，,。；;！？!?\n]", query[:code_match.start()])[-1]
        candidate = re.sub(
            r"^\s*(?:请)?(?:分析|研究|查看|看看|跟踪|关注|评估)"
            r"(?:一下|下)?\s*",
            "",
            clause_prefix,
            flags=re.IGNORECASE,
        ).strip(" \t\r\n（(")
        if re.fullmatch(
            r"(?=.{2,24}$)(?=.*[\u4e00-\u9fff])"
            r"[\u4e00-\u9fffA-Za-z*ＳＴｓｔ]+",
            candidate,
            re.IGNORECASE,
        ):
            return True
    return False


def _query_has_multiple_explicit_instruments(text: str) -> bool:
    """Detect comparison prompts that name more than one instrument.

    Chat extraction is allowed to enrich a single target, but it must not let
    an LLM collapse a multi-instrument request into whichever ticker it emits
    first. This detector is deliberately conservative and only treats Latin
    tokens as instruments when the query contains comparison wording.
    """
    query = str(text or "").strip()
    if not query:
        return False

    candidates: set[str] = set()
    for match in re.finditer(
        r"(?<![A-Za-z0-9])(?:\d{6}\.(?:SH|SZ|SS|BJ)|\d{4,5}\.HK)(?![A-Za-z0-9])",
        query,
        re.IGNORECASE,
    ):
        candidate = _normalize_analysis_symbol(match.group(0))
        if _is_valid_analysis_symbol(candidate):
            candidates.add(candidate)
    for match in re.finditer(r"(?<!\d)\d{6}(?!\d)", query):
        prefix = query[max(0, match.start() - 12):match.start()]
        suffix = query[match.end():]
        if _six_digit_token_is_amount_or_quantity(query, match) or (
            re.search(r"(?:现金|预算|资金|可用资金|余额|本金)\s*[:：=]?\s*$", prefix)
            or re.match(r"\.[A-Za-z]{1,5}\b", suffix)
            or re.match(
                r"\s*(?:万?元|块(?:钱)?|人民币|现金|预算|股(?!票))",
                suffix,
            )
        ):
            continue
        candidate = _normalize_analysis_symbol(match.group(0))
        if _is_valid_analysis_symbol(candidate):
            candidates.add(candidate)

    ticker_token = r"[A-Za-z][A-Za-z0-9]{0,10}(?:[.\-][A-Za-z0-9]+)?"
    non_ticker_tokens = {
        "A", "AN", "AND", "ANALYSIS", "ANALYZE", "ATR", "BUY", "CAGR",
        "CHECK", "COMPARE", "DCF", "EBIT", "EBITDA", "EMA", "ENTRY",
        "EPS", "EXIT", "FCF", "FOR", "HOLD", "I", "IS", "MACD", "MY",
        "NPV", "OCF", "OF", "OR", "PB", "PE", "PEG", "PLEASE", "PS",
        "ROA", "ROE", "ROI", "RSI", "SELL", "SHOULD", "SMA", "STOCK",
        "HE", "HER", "HIS", "ITS", "SHE", "THE", "THEIR", "THEM", "TO",
        "TTM", "VERSUS", "VS", "VWAP", "VWMA", "WAIT", "WATCH",
        "WE", "WITH", "EARNINGS", "FUNDAMENTALS", "GROWTH", "MARKET",
        "NEWS", "OUTLOOK", "PRICE", "REVENUE", "RISK", "VALUATION",
        "VALUE", "MOMENTUM", "TECHNICAL", "FUNDAMENTAL", "SIGNAL", "SIGNALS",
        "REPORT", "REVIEW", "SKIP", "TELL",
        "BENCHMARK", "GROUP", "INDUSTRY", "PEER", "PEERS", "SECTOR",
    }
    comparison_pair = re.compile(
        rf"(?<![A-Za-z0-9])({ticker_token})\s*"
        rf"(?:\band\b|\bor\b|\bto\b|\bwith\b|\bvs\.?\b|\bversus\b|和|与|及|[/／、,，])\s*"
        rf"({ticker_token})(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    for pair in comparison_pair.finditer(query):
        list_prefix = query[:pair.start()]
        list_suffix = query[pair.end():]
        connector_text = query[pair.end(1):pair.start(2)]
        explicit_target_list = bool(
            not list_suffix.strip(" \t\r\n?.!")
            and re.search(
                r"(?:please\s+)?(?:analyze|analysis|compare|check|watch)"
                r"(?:\s+(?:stock|stocks|ticker|tickers))?\s*$",
                list_prefix,
                re.IGNORECASE,
            )
        )
        explicit_comparison = bool(
            re.search(r"(?:比较|对比|\bcompare)\s*$", list_prefix, re.IGNORECASE)
            or re.search(
                r"\b(?:vs\.?|versus)\b",
                connector_text,
                re.IGNORECASE,
            )
        )
        for group_index, raw_candidate in enumerate(pair.groups(), start=1):
            if raw_candidate.upper() in non_ticker_tokens:
                continue
            suffix = query[pair.end(group_index):]
            if re.match(
                r"\s*(?:行业|板块|产业链?|主题|概念|赛道|市场|指数)",
                suffix,
            ):
                continue
            distinctive = raw_candidate.isupper() or bool(
                re.search(r"[.\-]", raw_candidate)
            )
            if not distinctive and not (explicit_target_list or explicit_comparison):
                continue
            candidate = _normalize_analysis_symbol(raw_candidate)
            if _is_valid_analysis_symbol(candidate):
                candidates.add(candidate)

    connector = r"(?:\band\b|\bor\b|\bto\b|\bwith\b|\bvs\.?\b|\bversus\b|和|与|及|[/／、,，])"
    cn_code_token = r"\d{6}(?:\.(?:SH|SZ|SS|BJ))?"
    mixed_code_patterns = (
        re.compile(
            rf"(?<![A-Za-z0-9])({cn_code_token})\s*{connector}\s*"
            rf"({ticker_token})(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?<![A-Za-z0-9])({ticker_token})\s*{connector}\s*"
            rf"({cn_code_token})(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
    )
    for pattern in mixed_code_patterns:
        for pair in pattern.finditer(query):
            for group_index, raw_candidate in enumerate(pair.groups(), start=1):
                if raw_candidate.upper() in non_ticker_tokens:
                    continue
                if re.fullmatch(r"\d{6}", raw_candidate) and (
                    _six_digit_span_is_amount_or_quantity(
                        query,
                        pair.start(group_index),
                        pair.end(group_index),
                    )
                ):
                    continue
                suffix = query[pair.end(group_index):]
                if re.match(
                    r"\s*(?:行业|板块|产业链?|主题|概念|赛道|市场|指数)",
                    suffix,
                ):
                    continue
                candidate = _normalize_analysis_symbol(raw_candidate)
                if _is_valid_analysis_symbol(candidate):
                    candidates.add(candidate)

    # Tickers may belong to different action clauses without an adjacent list
    # connector (for example: "I hold AAPL, I want to buy MSFT").  Resolve
    # each independently scoped clause so account context from one instrument
    # can never be applied to another instrument selected by the LLM.
    clause_boundary = re.compile(
        r"[，,。；;！？!?\n]+|"
        r"(?:\band\b|\bbut\b|\bthen\b|但(?:是)?|不过|然后|同时|再)"
        r"(?=\s*(?:(?:I|we)\s+)?(?:want|plan|consider|hold|own|buy|sell|"
        r"analy[sz]e|watch|check|想|要|准备|打算|考虑|持有|买|卖|分析|查看|关注))",
        re.IGNORECASE,
    )
    for clause in clause_boundary.split(query):
        scoped_symbol, _ = _extract_symbol_and_date(clause.strip())
        normalized_scoped = _normalize_analysis_symbol(scoped_symbol or "")
        if (
            normalized_scoped
            and normalized_scoped not in non_ticker_tokens
            and _is_valid_analysis_symbol(normalized_scoped)
        ):
            candidates.add(normalized_scoped)

    # Explicit code/ticker pairs need no company-name lookup. Resolve them
    # first so common Chinese command words do not trigger a remote cold load.
    if len(candidates) > 1:
        return True

    semantic_name_connector = bool(
        re.search(
            r"(?:比较|对比|和|与|及|[/／、]|\b(?:compare|vs\.?|versus)\b)",
            query,
            re.IGNORECASE,
        )
    )
    punctuation_only_name_list = bool(
        not candidates
        and re.search(r"[\u4e00-\u9fff]", query)
        and re.search(r"[,，。；;！？!?\n]", query)
    )
    query_without_codes = re.sub(
        r"(?<![A-Za-z0-9])(?:\d{6}(?:\.(?:SH|SZ|SS|BJ))?|\d{4,5}\.HK)"
        r"(?![A-Za-z0-9])",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    generic_cn_terms = {
        "分析", "研究", "查看", "看看", "跟踪", "关注", "评估", "比较", "对比",
        "估值", "风险", "收益", "走势", "行情", "价格", "技术", "基本面", "财务",
        "营收", "利润", "现金流", "行业", "板块", "主题", "概念", "指数", "市场",
    }
    possible_cn_name_fragments = set()
    for pattern in (
        r"(?:^|[，,。；;！？!?\s])(?:请)?(?:分析|研究|查看|看看|跟踪|关注|评估|比较|对比)?"
        r"\s*([\u4e00-\u9fff*ＳＴｓｔ]{2,12})\s*(?=和|与|及|[/／、,，])",
        r"(?:和|与|及|[/／、,，])\s*([\u4e00-\u9fff*ＳＴｓｔ]{2,12}?)"
        r"(?=的|[，,。；;！？!?\s]|$)",
    ):
        for match in re.finditer(pattern, query_without_codes, re.IGNORECASE):
            fragment = match.group(1).strip().lstrip("的对")
            if fragment and fragment not in generic_cn_terms:
                possible_cn_name_fragments.add(fragment)
    needs_cn_name_resolution = bool(
        re.search(r"[\u4e00-\u9fff]", query)
        and (semantic_name_connector or punctuation_only_name_list)
        and (not candidates or possible_cn_name_fragments)
    )
    cached_cn_map = _load_cn_stock_map() if needs_cn_name_resolution else {}
    cn_name_symbols = (
        set(_extract_cn_symbols_from_query(query, stock_map=cached_cn_map))
        if cached_cn_map
        else set()
    )
    candidates.update(cn_name_symbols)

    # A Latin ticker paired directly with a resolved local company name is
    # also a multi-target request.  Match only around the connector instead of
    # scanning every uppercase token: PE/ROE/MACD and English action words are
    # common in otherwise single-instrument questions.
    if candidates and cn_name_symbols:
        for local_name, local_symbol in cached_cn_map.items():
            normalized_name = str(local_name or "").strip()
            normalized_local_symbol = str(local_symbol or "").strip().upper()
            if (
                not normalized_name
                or normalized_local_symbol not in candidates
                or normalized_name not in query
            ):
                continue
            escaped_name = re.escape(normalized_name)
            cross_market_patterns = (
                re.compile(
                    rf"(?<![A-Za-z0-9])({ticker_token})\s*{connector}\s*{escaped_name}",
                    re.IGNORECASE,
                ),
                re.compile(
                    rf"{escaped_name}\s*{connector}\s*({ticker_token})(?![A-Za-z0-9])",
                    re.IGNORECASE,
                ),
            )
            for pattern in cross_market_patterns:
                for match in pattern.finditer(query):
                    raw_candidate = match.group(1)
                    if raw_candidate.upper() in non_ticker_tokens:
                        continue
                    distinctive = raw_candidate.isupper() or bool(
                        re.search(r"[.\-]", raw_candidate)
                    )
                    if not distinctive:
                        continue
                    candidate = _normalize_analysis_symbol(raw_candidate)
                    if _is_valid_analysis_symbol(candidate):
                        candidates.add(candidate)

    return len(candidates) > 1


def _query_contains_instrument_code(text: str) -> bool:
    """Return whether text contains a code-like instrument reference.

    Cash amounts and share counts are excluded so a company-name query with a
    budget can still be resolved, while multi-instrument comparisons do not
    fall through to a single company-name match.
    """
    if re.search(
        r"(?<![A-Za-z0-9])\d{1,6}\.(?:SH|SZ|SS|BJ|HK)(?![A-Za-z0-9])",
        text,
        re.IGNORECASE,
    ):
        return True
    for match in re.finditer(r"(?<!\d)\d{6}(?!\d)", text):
        prefix = text[max(0, match.start() - 12):match.start()]
        suffix = text[match.end():]
        is_cash_amount = _six_digit_token_is_amount_or_quantity(text, match) or bool(
            re.search(
                r"(?:现金|预算|资金|可用资金|余额|本金)\s*[:：=]?\s*$",
                prefix,
            )
            or re.match(
                r"\s*(?:万?元|块(?:钱)?|人民币|股(?!票))",
                suffix,
            )
        )
        if not is_cash_amount:
            return True
    return False


def _resolve_analysis_trade_date(
    *,
    symbol: str,
    current_trade_date: str,
    parsed_query_date: Optional[str],
    trade_date_was_explicit: bool,
    query_text: Optional[str] = None,
) -> str:
    if trade_date_was_explicit:
        return current_trade_date
    scoped_query_date = _extract_scoped_analysis_date(query_text, symbol)
    if scoped_query_date:
        return scoped_query_date
    if parsed_query_date and _query_date_is_analysis_date(
        query_text,
        parsed_query_date,
    ):
        return parsed_query_date
    return market_today_str(symbol)


def _extract_scoped_analysis_date(
    query_text: Optional[str],
    symbol: str,
) -> Optional[str]:
    """Return the date explicitly scoped to analysis, in market-local time."""
    text = str(query_text or "")
    if not text:
        return None

    try:
        market_today = datetime.strptime(market_today_str(symbol), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None

    date_pattern = re.compile(
        r"\d{4}-\d{2}-\d{2}|"
        r"\d{4}年\d{1,2}月\d{1,2}日?|"
        r"\d{4}[/.]\d{1,2}[/.]\d{1,2}|"
        r"今天|今日|当天|昨天|昨日|前天|大前天|"
        r"上个交易日|上一交易日|最近一个交易日|"
        r"\b(?:previous|last)\s+trading\s+day\b|"
        r"\btoday\b|\byesterday\b|\bday\s+before\s+yesterday\b",
        re.IGNORECASE,
    )
    relative_offsets = {
        "今天": 0,
        "今日": 0,
        "当天": 0,
        "today": 0,
        "昨天": -1,
        "昨日": -1,
        "yesterday": -1,
        "前天": -2,
        "大前天": -3,
        "day before yesterday": -2,
    }
    previous_trading_day_tokens = {
        "上个交易日",
        "上一交易日",
        "最近一个交易日",
        "previous trading day",
        "last trading day",
    }

    scoped_dates: list[str] = []
    for match in date_pattern.finditer(text):
        token = match.group(0)
        prefix = text[max(0, match.start() - 100):match.start()]
        suffix = text[match.end():min(len(text), match.end() + 80)]
        clause_prefix = re.split(r"[，,。；;！？!?\n]", prefix)[-1]
        clause_suffix = re.split(r"[，,。；;！？!?\n]", suffix)[0]

        explicit_cutoff_scoped = bool(
            re.search(
                r"(?:截至|截止|分析日期|交易日|行情日期|as\s+of)\s*$",
                clause_prefix,
                re.IGNORECASE,
            )
        )

        transaction_scoped = bool(
            re.search(
                r"(?:买入|购入|建仓|卖出|成交|成本|持仓成本|"
                r"bought|purchased|sold|cost(?:\s+basis)?)"
                r"[^，,。；;！？!?\n]{0,24}(?:在|于|on)?\s*$",
                clause_prefix,
                re.IGNORECASE,
            )
            or re.match(
                r"\s*(?:买入|购入|建仓|卖出|成交|成本|"
                r"bought|purchased|sold|cost(?:\s+basis)?)\b",
                clause_suffix,
                re.IGNORECASE,
            )
        )
        if transaction_scoped:
            continue

        event_scoped = bool(
            not explicit_cutoff_scoped
            and (
                re.search(
                    r"(?:公告|财报|年报|半年报|季报|业绩预告|业绩快报|"
                    r"事件|减持|增持|回购|分红|停牌|复牌|发布|披露)"
                    r"[^，,。；;！？!?\n]{0,24}$",
                    clause_prefix,
                    re.IGNORECASE,
                )
                or re.match(
                    r"\s*(?:发布|披露|公告|发生|实施|完成)?\s*"
                    r"(?:公告|财报|年报|半年报|季报|业绩预告|业绩快报|"
                    r"事件|减持|增持|回购|分红|停牌|复牌)",
                    clause_suffix,
                    re.IGNORECASE,
                )
                or re.match(
                    r"\s*(?:发布|披露)(?:公告|财报|年报|半年报|季报)",
                    clause_suffix,
                    re.IGNORECASE,
                )
            )
        )
        if event_scoped:
            continue

        analysis_scoped = bool(
            explicit_cutoff_scoped
            or re.search(
                r"(?:分析|研究|查看|评估|回测|复盘|"
                r"analy[sz]e|research|review|evaluate|backtest|check)"
                r"[^，,。；;！？!?\n]{0,80}$",
                clause_prefix,
                re.IGNORECASE,
            )
            or re.match(
                r"\s*(?:的|时|当日)?\s*(?:表现|走势|行情|收盘|分析|复盘|"
                r"涨|跌|performance|trend|price|close|analysis|review)",
                clause_suffix,
                re.IGNORECASE,
            )
        )
        if not analysis_scoped:
            continue

        normalized_token = re.sub(r"\s+", " ", token.strip().lower())
        if normalized_token in previous_trading_day_tokens:
            market_date = market_today.strftime("%Y-%m-%d")
            if re.match(r"^\d{6}(?:\.(?:SH|SZ|SS|BJ))?$", symbol, re.IGNORECASE):
                from tradingagents.dataflows.trade_calendar import previous_cn_trading_day

                scoped_dates.append(previous_cn_trading_day(market_date))
            else:
                previous = market_today - timedelta(days=1)
                while previous.weekday() >= 5:
                    previous -= timedelta(days=1)
                scoped_dates.append(previous.strftime("%Y-%m-%d"))
            continue
        if normalized_token in relative_offsets:
            resolved = market_today + timedelta(days=relative_offsets[normalized_token])
            scoped_dates.append(resolved.strftime("%Y-%m-%d"))
            continue
        normalized_explicit = token
        normalized_explicit = re.sub(r"年|[/.]", "-", normalized_explicit)
        normalized_explicit = normalized_explicit.replace("月", "-").replace("日", "")
        try:
            parsed = datetime.strptime(normalized_explicit, "%Y-%m-%d")
        except ValueError:
            try:
                parts = [int(part) for part in normalized_explicit.split("-")]
                parsed = datetime(parts[0], parts[1], parts[2])
            except (TypeError, ValueError, IndexError):
                # Preserve an explicitly scoped but invalid date so the
                # request-level date validator can reject it. Silently
                # dropping it would run the analysis for today instead.
                scoped_dates.append(normalized_explicit)
                continue
        scoped_dates.append(parsed.strftime("%Y-%m-%d"))

    return scoped_dates[-1] if scoped_dates else None


def _query_date_is_analysis_date(
    query_text: Optional[str],
    parsed_query_date: str,
) -> bool:
    """Distinguish an analysis date from a transaction or cost-basis date."""
    if query_text is None:
        # Preserve the helper's standalone contract for callers that already
        # scoped parsed_query_date before invoking it.
        return True

    text = str(query_text or "")
    explicit_source_matches: list[re.Match[str]] = list(
        re.finditer(re.escape(parsed_query_date), text)
    )
    try:
        parsed_dt = datetime.strptime(parsed_query_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        parsed_dt = None
    if parsed_dt is not None:
        localized_patterns = (
            rf"{parsed_dt.year}年0?{parsed_dt.month}月0?{parsed_dt.day}日?",
            rf"{parsed_dt.year}[/.]0?{parsed_dt.month}[/.]0?{parsed_dt.day}",
        )
        for pattern in localized_patterns:
            explicit_source_matches.extend(re.finditer(pattern, text))
    # Relative phrases can explain an LLM-resolved date only when the query
    # does not also contain a different explicit date (for example a buy date).
    source_matches = explicit_source_matches or list(
        re.finditer(
            r"(?:今天|今日|当天|昨天|昨日|前天|大前天|"
            r"上个交易日|上一交易日|最近一个交易日|"
            r"\btoday\b|\byesterday\b|\bday\s+before\s+yesterday\b)",
            text,
            re.IGNORECASE,
        )
    )
    if not source_matches:
        return False
    if len(explicit_source_matches) == 1:
        shorthand_date = re.compile(
            r"^\s*(?:分析|查看|研究|复盘|analy[sz]e|check|review)?\s*"
            r"(?:[A-Za-z][A-Za-z0-9.\-]{0,15}|\d{6}(?:\.(?:SH|SZ|BJ))?)"
            r"\s*(?:on\s*)?"
            r"(?:\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日?|"
            r"\d{4}[/.]\d{1,2}[/.]\d{1,2})\s*$",
            re.IGNORECASE,
        )
        if shorthand_date.fullmatch(text):
            # A sole code/ticker plus one explicit date is an unambiguous
            # historical-analysis shorthand. Event and transaction dates carry
            # additional wording and therefore do not match this narrow form.
            return True
    for source_match in source_matches:
        start = max(0, source_match.start() - 24)
        end = min(len(text), source_match.end() + 24)
        context = text[start:end]
        source_date = re.escape(source_match.group())
        explicit_analysis_scope = bool(
            re.search(
                r"(?:截至|截止|分析日期|交易日|行情日期|as\s+of)\s*"
                + source_date,
                context,
                re.IGNORECASE,
            )
            or re.search(
                r"(?:分析|研究|查看|评估|回测)"
                r"[^，。；;！？!?\n]{0,60}\s*"
                + source_date
                + r"\s*$",
                text[:source_match.end()],
                re.IGNORECASE,
            )
            or re.search(
                source_date
                + r"\s*(?:的|时|当日)?\s*(?:表现|走势|行情|收盘|分析)",
                context,
                re.IGNORECASE,
            )
        )
        if explicit_analysis_scope:
            return True
    return False


def _is_valid_analysis_trade_date(value: str) -> bool:
    try:
        datetime.strptime(str(value or ""), "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return True


def _sse_pack(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_stock_csv(raw: str) -> List[Dict[str, Any]]:
    if not raw:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return []

    try:
        df = pd.read_csv(StringIO("\n".join(lines)))
    except Exception:
        return []

    if "Date" not in df.columns:
        return []

    rename_map = {k: k.strip() for k in df.columns}
    df = df.rename(columns=rename_map)
    required = ["Date", "Open", "High", "Low", "Close"]
    for col in required:
        if col not in df.columns:
            return []

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date")
    if df.empty:
        return []

    candles: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        candles.append(
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]) if "Volume" in df.columns and pd.notna(row.get("Volume")) else None,
            }
        )
    return candles


CN_INDEX_SYMBOL_MAP = {
    "000001.SH": "sh000001",
    "399001.SZ": "sz399001",
    "399006.SZ": "sz399006",
    "000300.SH": "sh000300",
    "000688.SH": "sh000688",
    "000905.SH": "sh000905",
    "000852.SH": "sh000852",
    "899050.BJ": "bj899050",
}


def _is_cn_index_symbol(symbol: str) -> bool:
    return symbol.upper() in CN_INDEX_SYMBOL_MAP


def _normalize_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    col_map = {
        "日期": "Date",
        "date": "Date",
        "Date": "Date",
        "开盘": "Open",
        "open": "Open",
        "Open": "Open",
        "最高": "High",
        "high": "High",
        "High": "High",
        "最低": "Low",
        "low": "Low",
        "Low": "Low",
        "收盘": "Close",
        "close": "Close",
        "Close": "Close",
        "成交量": "Volume",
        "volume": "Volume",
        "Volume": "Volume",
        "成交额": "Amount",
        "amount": "Amount",
        "Amount": "Amount",
        "涨跌幅": "ChangePercent",
        "涨跌额": "Change",
        "换手率": "TurnoverRate",
    }
    out = df.rename(columns=col_map).copy()
    required = ["Date", "Open", "High", "Low", "Close"]
    if any(col not in out.columns for col in required):
        return pd.DataFrame()

    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out.dropna(subset=["Date"]).sort_values("Date")
    for col in ["Open", "High", "Low", "Close", "Volume", "Amount", "ChangePercent", "Change", "TurnoverRate"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    return out.reset_index(drop=True)


def _fetch_index_kline(symbol: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    import akshare as ak  # type: ignore

    symbol_key = symbol.upper()
    vendor_symbol = CN_INDEX_SYMBOL_MAP.get(symbol_key)
    if not vendor_symbol:
        return []

    yyyymmdd_start = start_date.replace("-", "")
    yyyymmdd_end = end_date.replace("-", "")
    last_exc: Exception | None = None

    for fetcher in (
        lambda: ak.stock_zh_index_daily_em(
            symbol=vendor_symbol,
            start_date=yyyymmdd_start,
            end_date=yyyymmdd_end,
        ),
        lambda: ak.stock_zh_index_daily(symbol=vendor_symbol),
        lambda: ak.index_zh_a_hist(
            symbol=symbol_key.split(".")[0],
            period="daily",
            start_date=yyyymmdd_start,
            end_date=yyyymmdd_end,
        ),
    ):
        try:
            raw_df = fetcher()
            df = _normalize_kline_df(raw_df)
            if df.empty:
                continue
            df = df[(df["Date"] >= pd.to_datetime(start_date)) & (df["Date"] <= pd.to_datetime(end_date))]
            if df.empty:
                continue
            candles: List[Dict[str, Any]] = []
            prev_close: float | None = None
            for _, row in df.iterrows():
                close = float(row["Close"])
                change = float(row["Change"]) if "Change" in df.columns and pd.notna(row.get("Change")) else (close - prev_close if prev_close is not None else None)
                change_pct = (
                    float(row["ChangePercent"])
                    if "ChangePercent" in df.columns and pd.notna(row.get("ChangePercent"))
                    else ((change / prev_close) * 100 if prev_close not in (None, 0) and change is not None else None)
                )
                candles.append(
                    {
                        "date": row["Date"].strftime("%Y-%m-%d"),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": close,
                        "volume": float(row["Volume"]) if "Volume" in df.columns and pd.notna(row.get("Volume")) else None,
                        "amount": float(row["Amount"]) if "Amount" in df.columns and pd.notna(row.get("Amount")) else None,
                        "change": change,
                        "change_percent": change_pct,
                        "turnover_rate": float(row["TurnoverRate"]) if "TurnoverRate" in df.columns and pd.notna(row.get("TurnoverRate")) else None,
                    }
                )
                prev_close = close
            return candles
        except Exception as exc:
            last_exc = exc
            continue

    if last_exc:
        _log(f"[kline] index fetch failed for {symbol}: {type(last_exc).__name__}: {last_exc}")
    return []


async def _stream_job_events(job_id: str):
    store = get_job_store()
    yield _sse_pack("job.ready", {"job_id": job_id})
    async for event in store.subscribe(job_id):
        evt_name = event["event"]
        yield _sse_pack(evt_name, event["data"])
        if evt_name in ("job.completed", "job.failed"):
            yield "event: done\ndata: [DONE]\n\n"
            return


@app.get("/healthz")
async def healthz():
    """Report process health and detect a starved asyncio executor."""
    payload: Dict[str, Any] = {"status": "ok"}
    if _default_executor is not None:
        payload["executor_queued"] = _default_executor._work_queue.qsize()
        payload["executor_threads"] = len(_default_executor._threads)
    # [UPSTREAM-081-001] shared fetch-pool leak/backlog gauges: stuck fetch
    # threads are capped by max_workers and visible here instead of growing
    # unbounded across rounds.
    payload["fetch_pool"] = get_fetch_pool_stats()
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, int), timeout=_HEALTHZ_PROBE_TIMEOUT
        )
    except asyncio.TimeoutError:
        payload["status"] = "thread_pool_starved"
        return JSONResponse(status_code=503, content=payload)
    return payload


# [DATA-026] db_hygiene_check — read-only DB test pollution + scheduler filter health.
# Never mutates the DB, never triggers TA/LLM, never prints keys. Used by ops
# dashboards and AUTO-005 preflight to decide whether human cleanup is needed.
@app.get("/v1/db-hygiene")
def db_hygiene(
    current_user: UserDB = Depends(_require_readonly_api_user),
) -> Dict[str, Any]:
    from api.services.db_hygiene_service import run_db_hygiene_check

    return run_db_hygiene_check().to_dict()


# Simple in-memory rate limiter for version stats: {ip: last_timestamp}
_vs_rate_limit: Dict[str, float] = {}
_VS_RATE_INTERVAL = 3600  # at most once per hour per IP


@app.post("/api/version-stats")
def version_stats(payload: Dict[str, Any] = Body(...), request: Request = None, db: Session = Depends(get_db)):
    """Collect anonymous version statistics from deployed instances."""
    remote_ip = _get_real_ip(request)

    # Rate limit by IP
    now = time.time()
    if remote_ip:
        last = _vs_rate_limit.get(remote_ip, 0)
        if now - last < _VS_RATE_INTERVAL:
            return {"status": "ok"}
        _vs_rate_limit[remote_ip] = now

    record = VersionStatsDB(
        version=str(payload.get("v", ""))[:50],
        nonce=str(payload.get("nonce", ""))[:64],
        remote_ip=remote_ip,
    )
    db.add(record)
    db.commit()
    return {"status": "ok"}


_RESOLVABLE_SYMBOL_RE = re.compile(
    r"^("
    r"\d{6}\.(SH|SZ|BJ)"          # A 股 / 北交所
    r"|\d{4,5}\.HK"                # 港股
    r"|[A-Z][A-Z0-9]{0,10}"         # 美股 ticker（可含数字）
    r"|[A-Z][A-Z0-9]{0,5}-[A-Z0-9]"  # 美股类股 ticker
    r"|[A-Z][A-Z0-9]{0,5}\.[A-Z0-9]{1,3}"  # 显式美股交易所后缀
    r")$"
)


def _has_consistent_cn_exchange(symbol: str) -> bool:
    """Reject CN codes whose explicit exchange suffix contradicts the code."""
    if _is_cn_index_symbol(str(symbol or "")):
        return True
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", str(symbol or "").upper())
    if not match:
        return True
    code, suffix = match.groups()
    if code.startswith(("4", "8", "920")):
        expected = "BJ"
    elif code.startswith(("5", "6", "9")):
        expected = "SH"
    else:
        expected = "SZ"
    return suffix == expected


def _is_valid_analysis_symbol(symbol: str) -> bool:
    return bool(
        _RESOLVABLE_SYMBOL_RE.fullmatch(str(symbol or ""))
        and _has_consistent_cn_exchange(symbol)
    )


def _normalize_analysis_symbol(raw: str) -> str:
    """Normalize an analysis symbol without truncating a valid generic ticker."""
    candidate = str(raw or "").strip().upper()
    candidate = re.sub(r"\.SS$", ".SH", candidate)
    prefixed_cn = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", candidate)
    if prefixed_cn:
        exchange, code = prefixed_cn.groups()
        normalized = f"{code}.{exchange}"
        return normalized if _is_valid_analysis_symbol(normalized) else candidate
    if _is_valid_analysis_symbol(candidate):
        return candidate
    if re.fullmatch(r"\d{6}", candidate):
        return _normalize_symbol(candidate)
    # Malformed explicit symbols must be rejected by the validator, never
    # truncated by the permissive legacy normalizer (1234567 -> 123456.SZ).
    return candidate


def _resolve_analysis_symbol(raw: str) -> str:
    """Resolve a direct analysis target without fuzzy company-name guesses."""
    normalized = _normalize_analysis_symbol(raw)
    if _is_valid_analysis_symbol(normalized):
        return normalized

    candidate = str(raw or "").strip()
    if not re.search(r"[\u4e00-\u9fff]", candidate):
        return normalized
    mapped = _load_cn_stock_map().get(candidate)
    if not mapped:
        return normalized
    resolved = _normalize_analysis_symbol(mapped)
    return resolved if _is_valid_analysis_symbol(resolved) else normalized


@app.get("/v1/market/kline", response_model=KlineResponse)
def get_kline(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> KlineResponse:
    end = end_date or cn_today_str()
    if start_date:
        start = start_date
    else:
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=120)).strftime("%Y-%m-%d")

    if _is_cn_index_symbol(symbol):
        candles = _fetch_index_kline(symbol, start, end)
    else:
        # Normalize symbol (convert "阳光电源" -> "300274.SZ")
        original = symbol
        symbol = (
            _normalize_symbol(symbol)
            if re.search(r"[\u4e00-\u9fff]", symbol)
            else _normalize_analysis_symbol(symbol)
        )
        if not _is_valid_analysis_symbol(symbol):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unrecognized symbol {original!r} (normalized to {symbol!r}); "
                    f"expected formats: '300394.SZ' / 'AAPL' / '00700.HK'"
                ),
            )
        config = _build_runtime_config({})
        set_config(config)
        raw = route_to_vendor("get_stock_data", symbol, start, end)
        candles = _parse_stock_csv(raw)
    if not candles:
        raise HTTPException(status_code=404, detail="no kline data")
    return KlineResponse(
        symbol=symbol,
        start_date=start,
        end_date=end,
        candles=candles,
    )


# TA-MF-01: frozen market-facts contract (candidate) self-description.
# Read-only, authenticated; the ONLY new facts endpoint in TA-MF-01 — the six
# package routes stay reserved until TA-MF-02..05 implement them.
MARKET_FACTS_CONTRACT_VERSION = "1.0.0-candidate.1"
MARKET_FACTS_PACK_TYPES = (
    "trading_calendar",
    "market_regime",
    "strategy_inputs",
    "event_calendar",
    "company_facts",
    "governance_risk",
)
MARKET_FACTS_READONLY_ENDPOINTS = (
    {"path": "/v1/market/kline", "package": "raw_kline", "auth": "none", "status": "LEGACY", "note": "KNOWN-GAP-1: unauthenticated legacy route kept for compatibility"},
    {"path": "/v1/market/facts/contract", "package": "self_description", "auth": "bearer_readonly", "status": "ACTIVE", "note": "this endpoint"},
    {"path": "/v1/market/facts/trading-calendar", "package": "trading_calendar", "auth": "bearer_readonly", "status": "RESERVED", "note": "TA-MF-02"},
    {"path": "/v1/market/facts/market-regime", "package": "market_regime", "auth": "bearer_readonly", "status": "RESERVED", "note": "TA-MF-03"},
    {"path": "/v1/market/facts/strategy-inputs", "package": "strategy_inputs", "auth": "bearer_readonly", "status": "RESERVED", "note": "TA-MF-03"},
    {"path": "/v1/market/facts/event-calendar", "package": "event_calendar", "auth": "bearer_readonly", "status": "RESERVED", "note": "TA-MF-04"},
    {"path": "/v1/market/facts/company-facts", "package": "company_facts", "auth": "bearer_readonly", "status": "RESERVED", "note": "TA-MF-05"},
    {"path": "/v1/market/facts/governance-risk", "package": "governance_risk", "auth": "bearer_readonly", "status": "RESERVED", "note": "TA-MF-05"},
)
_MARKET_FACTS_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "docs" / "contracts" / "schemas"


class MarketFactsContractResponse(BaseModel):
    contract: str
    version: str
    status: str
    timezone: str
    packages: List[str]
    query_states: List[str]
    read_only_endpoints: List[Dict[str, str]]
    schema_dir_available: bool
    schemas: Optional[List[Dict[str, str]]] = None
    fixture_manifest_sha256: Optional[str] = None
    contract_doc_ref: str


def _market_facts_schema_digests() -> tuple[bool, Optional[List[Dict[str, str]]], Optional[str]]:
    if not _MARKET_FACTS_SCHEMA_DIR.is_dir():
        return False, None, None
    try:
        schemas = [
            {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(_MARKET_FACTS_SCHEMA_DIR.glob("*.schema.json"))
        ]
        manifest_path = _MARKET_FACTS_SCHEMA_DIR / "MANIFEST.json"
        manifest_sha = (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            if manifest_path.is_file()
            else None
        )
        return True, schemas or None, manifest_sha
    except OSError:
        return False, None, None


@app.get("/v1/market/facts/contract", response_model=MarketFactsContractResponse)
def get_market_facts_contract(
    current_user: UserDB = Depends(_require_readonly_api_user),
) -> MarketFactsContractResponse:
    """Contract self-description so consumers can pin the loaded version.

    Read-only; serves no market data and never calls upstream providers.
    """
    from tradingagents.dataflows.tushare_query_contract import QUERY_STATES

    available, schemas, manifest_sha = _market_facts_schema_digests()
    return MarketFactsContractResponse(
        contract="market-facts",
        version=MARKET_FACTS_CONTRACT_VERSION,
        status="CANDIDATE",
        timezone="Asia/Shanghai",
        packages=list(MARKET_FACTS_PACK_TYPES),
        query_states=list(QUERY_STATES),
        read_only_endpoints=[dict(entry) for entry in MARKET_FACTS_READONLY_ENDPOINTS],
        schema_dir_available=available,
        schemas=schemas,
        fixture_manifest_sha256=manifest_sha,
        contract_doc_ref="docs/contracts/market-facts-v1.md",
    )


def _normalize_ths_code(code: str) -> str:
    """Convert THS/XQ code like SH601xxx → 601xxx.SH"""
    code = str(code).strip()
    if code.upper().startswith("SH"):
        return f"{code[2:]}.SH"
    if code.upper().startswith("SZ"):
        return f"{code[2:]}.SZ"
    if code.upper().startswith("BJ") or code.upper().startswith("NQ"):
        return f"{code[2:]}.BJ"
    # Bare 6-digit code — guess exchange
    if code.startswith(("6", "5")):
        return f"{code}.SH"
    if code.startswith(("0", "3", "2")):
        return f"{code}.SZ"
    return code


@app.get("/v1/market/hot-stocks")
def get_hot_stocks(source: str = "em", limit: int = 30) -> Dict:
    """Return hot A-share stocks from different sources.
    
    Args:
        source: Data source selection
            - 'em': 东方财富热榜 (EastMoney hot stocks)
            - 'xq': 雪球热门 (Xueqiu most-followed stocks)
            - 'ths': 连涨榜 (Consecutive rising stocks, not general hot list)
        limit: Maximum number of stocks to return
    
    Returns:
        Dict with stocks list, total count, source info, and fallback status
    """
    import akshare as ak

    # 定义数据源尝试顺序（如果主数据源失败，自动尝试备用源）
    source_configs = {
        "em": ("stock_hot_rank_em", None, "东方财富热榜"),
        "xq": ("stock_hot_follow_xq", "最热门", "雪球热门"),
        "ths": ("stock_rank_lxsz_ths", None, "连涨榜"),
    }

    if source not in source_configs:
        raise HTTPException(status_code=400, detail=f"Unknown source: {source}")

    # 尝试主数据源，失败则尝试其他源
    sources_to_try = [source] + [s for s in ["xq", "em", "ths"] if s != source]
    last_error = None

    for src in sources_to_try:
        try:
            func_name, param, desc = source_configs[src]
            func = getattr(ak, func_name)

            # 调用 akshare 函数
            if param:
                df = func(symbol=param).head(limit)
            else:
                df = func().head(limit)

            stocks = []

            if src == "em":
                for i, (_, row) in enumerate(df.iterrows()):
                    stocks.append({
                        "rank": i + 1,
                        "symbol": _normalize_ths_code(str(row.get("代码", ""))),
                        "name": str(row.get("股票名称", "")),
                        "price": float(row.get("最新价", 0) or 0),
                        "change": float(row.get("涨跌额", 0) or 0),
                        "change_pct": float(row.get("涨跌幅", 0) or 0),
                        "extra": "",
                    })

            elif src == "xq":
                for i, (_, row) in enumerate(df.iterrows()):
                    stocks.append({
                        "rank": i + 1,
                        "symbol": _normalize_ths_code(str(row.get("股票代码", ""))),
                        "name": str(row.get("股票简称", "")),
                        "price": float(row.get("最新价", 0) or 0),
                        "change": 0.0,
                        "change_pct": 0.0,
                        "extra": f"关注 {int(row.get('关注', 0)):,}",
                    })

            elif src == "ths":
                for i, (_, row) in enumerate(df.iterrows()):
                    days = int(row.get("连涨天数", 0) or 0)
                    change_pct = float(row.get("连续涨跌幅", 0) or 0)
                    stocks.append({
                        "rank": i + 1,
                        "symbol": _normalize_ths_code(str(row.get("股票代码", ""))),
                        "name": str(row.get("股票简称", "")),
                        "price": float(row.get("收盘价", 0) or 0),
                        "change": 0.0,
                        "change_pct": change_pct,
                        "extra": f"连涨{days}天",
                    })

            # 成功获取数据
            fallback_msg = f" (fallback from {source_configs[source][2]})" if src != source else ""
            _log(f"Hot stocks: successfully fetched from {desc}{fallback_msg}")
            return {
                "stocks": stocks,
                "total": len(stocks),
                "source": src,
                "requested_source": source,
                "fallback": src != source,
            }

        except Exception as e:
            last_error = e
            _log(f"Hot stocks: {desc} failed - {type(e).__name__}: {str(e)[:100]}")
            continue

    # 所有数据源都失败
    raise HTTPException(
        status_code=503,
        detail=f"All data sources failed. Last error: {type(last_error).__name__}: {str(last_error)[:200]}"
    )


# [PERF-004] full_ta_cost_gate
class FullTACostPreviewResponse(BaseModel):
    runtime_tier: str = "FULL_TA"
    tier_label: str = "完整 TA"
    expected_latency: str = "10-20min"
    llm_allowed: bool = True
    cost_risk: str = "high"
    requires_confirmation: bool = True
    llm_provider: str = ""
    llm_model: str = ""
    base_url_display: str = ""
    enabled_modules: List[str] = Field(default_factory=list)
    estimated_llm_calls: int = 0
    description: str = ""


@app.get("/v1/analyze/cost-preview", response_model=FullTACostPreviewResponse)
def get_full_ta_cost_preview(
    current_user: UserDB = Depends(_require_api_user),
):
    from api.runtime_tier import get_full_ta_cost_preview as _preview
    config = _build_runtime_config({}, user_id=current_user.id)
    llm_provider = config.get("llm_provider", "")
    llm_model = config.get("deep_think_llm", "")
    base_url = str(config.get("backend_url", "") or "")
    base_url_display = base_url if base_url else ""
    preview = _preview(
        llm_provider=llm_provider,
        llm_model=llm_model,
        base_url_display=base_url_display,
    )
    return FullTACostPreviewResponse(**preview.to_dict())


@app.post("/v1/analyze", response_model=AnalyzeResponse)
async def analyze(
    request: AnalyzeRequest,
    current_user: UserDB = Depends(_require_api_user),
) -> AnalyzeResponse:
    from api.runtime_tier import (  # [PERF-001] runtime_tier_contract
        RuntimeTier,
        is_full_ta_allowed_without_confirmation,
        tier_to_meta,
    )
    from api.ta_profile import (  # [PERF-002] lightweight_ta_profiles
        TAProfile,
        get_profile_spec,
        profile_to_meta,
        recommend_profile,
        filter_analysts_for_profile,
    )

    # A direct request may omit symbol when the query contains an unambiguous
    # ticker or A-share/fund name. Resolve it before saved-context lookup and
    # job creation so reports, holdings and cache keys never use an empty or
    # model-inferred instrument.
    trade_date_was_explicit = "trade_date" in request.model_fields_set
    query_date: Optional[str] = None
    if request.symbol:
        request.symbol = await asyncio.to_thread(
            _resolve_analysis_symbol,
            request.symbol,
        )
    if request.query:
        if await asyncio.to_thread(
            _query_has_multiple_explicit_instruments,
            request.query,
        ):
            raise HTTPException(
                status_code=422,
                detail="一次分析只支持一个明确标的，请拆分多标的请求。",
            )
        if request.symbol:
            query_symbol, query_date = await asyncio.to_thread(
                _resolve_query_symbol_and_date,
                request.query,
            )
            if query_symbol is None and _query_has_explicit_cn_name_code_pair(
                request.query
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "查询文本中的公司名称与代码无法确认一致，请只保留一个明确标的。"
                    ),
                )
            if query_symbol is None and _query_contains_instrument_code(
                request.query
            ):
                raise HTTPException(
                    status_code=422,
                    detail="查询文本中的代码无法确认，请只保留一个明确标的。",
                )
            if (
                query_symbol
                and _normalize_analysis_symbol(query_symbol)
                != _normalize_analysis_symbol(request.symbol)
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "请求字段与查询文本中的分析标的不一致，请只保留一个明确标的。"
                    ),
                )
        else:
            query_symbol, query_date = await asyncio.to_thread(
                _resolve_query_symbol_and_date,
                request.query,
            )
            if query_symbol:
                request.symbol = query_symbol
    if not str(request.symbol or "").strip():
        raise HTTPException(
            status_code=422,
            detail="无法唯一识别分析标的，请提供股票代码或明确的股票名称。",
        )
    request.symbol = _normalize_analysis_symbol(request.symbol)
    if not _is_valid_analysis_symbol(request.symbol):
        raise HTTPException(
            status_code=422,
            detail="分析标的格式无效，请使用 600519.SH、0700.HK 或 AAPL 等明确格式。",
        )
    request.trade_date = _resolve_analysis_trade_date(
        symbol=request.symbol,
        current_trade_date=request.trade_date,
        parsed_query_date=query_date,
        trade_date_was_explicit=trade_date_was_explicit,
        query_text=request.query,
    )
    if not _is_valid_analysis_trade_date(request.trade_date):
        raise HTTPException(
            status_code=422,
            detail="分析日期无效，请使用真实存在的 YYYY-MM-DD 日期。",
        )

    explicit_context = _extract_request_user_context(request)

    tier_value = request.runtime_tier or RuntimeTier.LIGHT_RESEARCH.value
    try:
        resolved_tier = RuntimeTier(tier_value)
    except ValueError:
        resolved_tier = RuntimeTier.LIGHT_RESEARCH

    if request.dry_run:
        resolved_tier = RuntimeTier.LIGHT_RESEARCH

    if resolved_tier == RuntimeTier.FULL_TA and not request.confirmed_full_ta:
        meta = tier_to_meta(RuntimeTier.FULL_TA)
        raise HTTPException(
            status_code=403,
            detail={
                "message": "完整 TA 需要用户确认。请设置 confirmed_full_ta=true 后重试。",
                "runtime_tier": meta["runtime_tier"],
                "expected_latency": meta["expected_latency"],
                "cost_risk": meta["cost_risk"],
                "requires_confirmation": True,
            },
        )

    tier_meta = tier_to_meta(resolved_tier)

    def _load_user_context() -> Dict[str, Any]:
        with get_db_ctx() as db:
            return _compose_analysis_user_context(
                db,
                current_user.id,
                request.symbol,
                explicit_context=explicit_context,
            )

    # Don't block the event loop on a sync SQLite read while the scheduler
    # process may be holding write locks.
    merged_user_context = await asyncio.to_thread(_load_user_context)
    _apply_user_context_to_request(request, merged_user_context)
    direct_query_needs_parser = bool(request.query and request.user_intent is None)
    analysis_intent_hint = _ensure_query_and_user_intent(
        request,
        request.symbol,
        materialize_missing_intent=not direct_query_needs_parser,
        numeric_position_was_explicit="current_position" in explicit_context,
        position_pct_was_explicit="current_position_pct" in explicit_context,
    )

    resolved_profile: Optional[TAProfile] = None
    if request.runtime_profile:
        try:
            resolved_profile = TAProfile(request.runtime_profile)
        except ValueError:
            resolved_profile = None
    if resolved_profile is None and resolved_tier == RuntimeTier.LIGHT_RESEARCH:
        analysis_intent = analysis_intent_hint
        if request.user_intent and isinstance(request.user_intent, dict):
            analysis_intent = str(request.user_intent.get("analysis_intent", ""))
        resolved_profile = recommend_profile(
            analysis_intent=analysis_intent,
            has_position=_resolve_has_position(request.user_intent, request),
        )
    elif resolved_profile is None and resolved_tier == RuntimeTier.FULL_TA:
        resolved_profile = TAProfile.FULL_TA
    elif resolved_profile is None:
        resolved_profile = TAProfile.FULL_TA

    profile_meta = profile_to_meta(resolved_profile)
    if resolved_profile != TAProfile.FULL_TA:
        request.selected_analysts = filter_analysts_for_profile(
            resolved_profile, request.selected_analysts,
        )

    job_id = uuid4().hex
    now = _utcnow_iso()
    _set_job(
        job_id,
        job_id=job_id,
        user_id=current_user.id,
        status="pending",
        created_at=now,
        started_at=None,
        finished_at=None,
        symbol=request.symbol,
        trade_date=request.trade_date,
        error=None,
        result=None,
        decision=None,
    )
    _emit_job_event(
        job_id,
        "job.created",
        {"job_id": job_id, "symbol": request.symbol, "trade_date": request.trade_date},
    )
    if request.dry_run:
        await _run_job(job_id, request, True, True, current_user.id, "api")
        final_status = _get_job(job_id).get("status", "completed")
        return AnalyzeResponse(
            job_id=job_id, status=final_status, created_at=now,
            runtime_tier=tier_meta["runtime_tier"],
            runtime_tier_label=tier_meta["tier_label"],
            expected_latency=tier_meta["expected_latency"],
            runtime_profile=profile_meta["runtime_profile"],  # [PERF-002]
            runtime_profile_label=profile_meta["profile_label"],  # [PERF-002]
            enabled_modules=sorted(profile_meta["enabled_analysts"] + profile_meta["enabled_risk_modules"] + profile_meta["enabled_managers"]),  # [PERF-002]
        )
    _create_tracked_task(_run_job(job_id, request, True, True, current_user.id, "api"))
    return AnalyzeResponse(
        job_id=job_id, status="pending", created_at=now,
        runtime_tier=tier_meta["runtime_tier"],
        runtime_tier_label=tier_meta["tier_label"],
        expected_latency=tier_meta["expected_latency"],
        runtime_profile=profile_meta["runtime_profile"],  # [PERF-002]
        runtime_profile_label=profile_meta["profile_label"],  # [PERF-002]
        enabled_modules=sorted(profile_meta["enabled_analysts"] + profile_meta["enabled_risk_modules"] + profile_meta["enabled_managers"]),  # [PERF-002]
    )


def _require_job_owner(job_id: str, current_user: UserDB) -> Dict[str, Any]:
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    owner_id = job.get("user_id")
    if owner_id and owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/v1/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, current_user: UserDB = Depends(_require_api_user)) -> JobStatusResponse:
    job = _require_job_owner(job_id, current_user)
    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        created_at=job["created_at"],
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
        symbol=job["symbol"],
        trade_date=job["trade_date"],
        error=job.get("error"),
        waiting_ahead_count=job.get("waiting_ahead_count"),
        scheduled_running_count=job.get("scheduled_running_count"),
        scheduled_concurrency_limit=job.get("scheduled_concurrency_limit"),
    )


@app.get("/v1/jobs/{job_id}/result")
def get_job_result(job_id: str, current_user: UserDB = Depends(_require_api_user)) -> Dict[str, Any]:
    job = _require_job_owner(job_id, current_user)
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"job status is {job['status']}")
    return {
        "job_id": job_id,
        "status": job["status"],
        "decision": job.get("decision"),
        "result": job.get("result"),
        "finished_at": job.get("finished_at"),
    }


@app.get("/v1/jobs/{job_id}/events")
def stream_job_events(job_id: str, current_user: UserDB = Depends(_require_api_user)):
    _require_job_owner(job_id, current_user)
    return StreamingResponse(
        _stream_job_events(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _ai_extract_symbol_and_date_streaming(
    text: str, config: Dict[str, Any], job_id: str
) -> tuple[Optional[str], Optional[str], List[str], List[str], List[str], Dict[str, Any]]:
    """
    Async streaming version of _ai_extract_symbol_and_date.
    Emits agent.token events so the frontend can show streaming output during extraction.
    """
    from tradingagents.llm_clients.factory import create_llm_client
    import json as _json

    today = datetime.now().strftime("%Y-%m-%d")
    fast_symbol, fast_date = _extract_symbol_and_date(text)
    fast_fallback_date = fast_date or (
        market_today_str(fast_symbol) if fast_symbol else None
    )
    llm_name: Optional[str] = None
    llm_date: Optional[str] = None
    llm_horizons: List[str] = ["short"]
    llm_focus_areas: List[str] = []
    llm_specific_questions: List[str] = []
    llm_user_context: Dict[str, Any] = {}

    try:
        client = create_llm_client(
            provider=config.get("llm_provider", "openai"),
            model=config.get("quick_think_llm"),
            base_url=config.get("backend_url"),
            api_key=config.get("api_key"),
        )
        prompt = f"""你是金融数据助手。从用户消息中提取以下字段并以 JSON 输出。

字段说明：
- stock_name：用户提到的公司名称或股票代码原文（如"华盛天成"、"贵州茅台"、"600519"、"AAPL"）；美股直接填 ticker。
- date：YYYY-MM-DD 格式。今天是 {today}；用户未提及日期时必须填 null。
- horizons：分析周期，只能选一个：
  * 用户明确提到"中线/中期/几个月/季度/长期/趋势投资"→ ["medium"]
  * 其他所有情况（含未提及）→ ["short"]
- focus_areas：用户关注的分析维度关键词列表，如 ["技术面", "资金面", "业绩"]，未提及则 []。
- specific_questions：用户提出的具体问题列表，如 ["近期有无催化剂？", "主力是否出货？"]，未提及则 []。
- user_context：从自然语言中提取的账户与约束对象。若未提及返回 {{}}。可包含：
  * objective：建仓 / 加仓 / 减仓 / 止损 / 观察 / 持有处理
  * risk_profile：保守 / 平衡 / 激进
  * investment_horizon：短线 / 波段 / 中线 / 长期
  * cash_available / current_position / current_position_pct / average_cost / max_loss_pct：数字
  * constraints：字符串数组
  * user_notes：仅保留重要但未能结构化归类的信息

仅输出 JSON，不要任何其他文字：
{{"stock_name": "...", "date": null, "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {{}}}}

如果无法识别股票标的：{{"stock_name": null, "date": null, "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {{}}}}

用户消息："{text}"
"""
        llm = client.get_llm()
        _log(f"[LLM Debug] Streaming StockExtract with model: {getattr(llm, 'model_name', 'unknown')}")

        full_content = ""
        async for chunk in llm.astream(prompt):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += token
            if token:
                _emit_job_event(job_id, "agent.token", {
                    "agent": "意图解析",
                    "report": "stock_extract",
                    "token": token,
                })

        _log(f"[LLM Debug] StockExtract response: {full_content[:200]}")
        m = re.search(r"\{.*\}", full_content, re.DOTALL)
        if m:
            data = _json.loads(m.group(0))
            llm_name = (data.get("stock_name") or "").strip() or None
            llm_date = data.get("date") or None
            llm_horizons = data.get("horizons") or ["short"]
            llm_focus_areas = data.get("focus_areas") or []
            llm_specific_questions = data.get("specific_questions") or []
            llm_user_context = normalize_user_context(data.get("user_context") or {})
    except Exception as e:
        _log(f"[StockExtract streaming] LLM failed: {e}")

    if not llm_name:
        if fast_symbol:
            _log(f"[StockExtract] LLM 未返回 stock_name，使用 regex 兜底: {fast_symbol}")
            return fast_symbol, fast_fallback_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context
        # [UPSTREAM-081-001] LLM 失败（限流/下线/网络）且 regex 无果时，用原文在
        # 已预热的本地股票名单里做一次 fail-closed 兜底；歧义或冷缓存保持 None。
        local_code = await asyncio.to_thread(_resolve_cn_name_from_text_cached, text)
        if local_code:
            _log(f"[StockExtract] LLM 失败，本地名单从原文兜底命中: {local_code}")
            return local_code, fast_fallback_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context
        return None, None, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    _log(f"[StockExtract] extracted name='{llm_name}', date={llm_date}, horizons={llm_horizons}")
    resolved_llm_date = llm_date or fast_date
    direct_symbol = _normalize_analysis_symbol(llm_name)
    if _is_valid_analysis_symbol(direct_symbol):
        return direct_symbol, resolved_llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    local_code = await asyncio.to_thread(_search_cn_stock_by_name, llm_name)
    if local_code:
        return local_code, resolved_llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    fallback = _normalize_analysis_symbol(llm_name)
    if _is_valid_analysis_symbol(fallback):
        return fallback, resolved_llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    if fast_symbol:
        _log(f"[StockExtract] LLM 名 '{llm_name}' 无法解析为代码，使用 regex 兜底: {fast_symbol}")
        return fast_symbol, llm_date or fast_fallback_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    return None, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context


def _ai_extract_symbol_and_date(
    text: str, config: Dict[str, Any]
) -> tuple[Optional[str], Optional[str], List[str], List[str], List[str], Dict[str, Any]]:
    """
    Single-LLM extraction: stock name, date, horizons, focus_areas, specific_questions.
    Then resolves the stock name to an authoritative code via akshare.
    Returns (symbol, date, horizons, focus_areas, specific_questions, inferred_user_context).
    """
    from tradingagents.llm_clients.factory import create_llm_client
    import json as _json

    today = datetime.now().strftime("%Y-%m-%d")
    fast_symbol, fast_date = _extract_symbol_and_date(text)
    fast_fallback_date = fast_date or (
        market_today_str(fast_symbol) if fast_symbol else None
    )

    llm_name: Optional[str] = None
    llm_date: Optional[str] = None
    llm_horizons: List[str] = ["short"]
    llm_focus_areas: List[str] = []
    llm_specific_questions: List[str] = []
    llm_user_context: Dict[str, Any] = {}
    try:
        client = create_llm_client(
            provider=config.get("llm_provider", "openai"),
            model=config.get("quick_think_llm"),
            base_url=config.get("backend_url"),
            api_key=config.get("api_key"),
        )
        prompt = f"""你是金融数据助手。从用户消息中提取以下字段并以 JSON 输出。

字段说明：
- stock_name：用户提到的公司名称或股票代码原文（如"华盛天成"、"贵州茅台"、"600519"、"AAPL"）；美股直接填 ticker。
- date：YYYY-MM-DD 格式。今天是 {today}；用户未提及日期时必须填 null。
- horizons：分析周期，只能选一个：
  * 用户明确提到"中线/中期/几个月/季度/长期/趋势投资"→ ["medium"]
  * 其他所有情况（含未提及）→ ["short"]
- focus_areas：用户关注的分析维度关键词列表，如 ["技术面", "资金面", "业绩"]，未提及则 []。
- specific_questions：用户提出的具体问题列表，如 ["近期有无催化剂？", "主力是否出货？"]，未提及则 []。
- user_context：从自然语言中提取的账户与约束对象。若未提及返回 {{}}。可包含：
  * objective：建仓 / 加仓 / 减仓 / 止损 / 观察 / 持有处理
  * risk_profile：保守 / 平衡 / 激进
  * investment_horizon：短线 / 波段 / 中线 / 长期
  * cash_available / current_position / current_position_pct / average_cost / max_loss_pct：数字
  * constraints：字符串数组
  * user_notes：仅保留重要但未能结构化归类的信息

仅输出 JSON，不要任何其他文字：
{{"stock_name": "...", "date": null, "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {{}}}}

如果无法识别股票标的：{{"stock_name": null, "date": null, "horizons": ["short"], "focus_areas": [], "specific_questions": [], "user_context": {{}}}}

用户消息："{text}"
"""
        llm = client.get_llm()
        
        # 调试日志：打印请求参数
        target_url = getattr(llm, 'openai_api_base', 'default')
        _log(f"[LLM Debug] Requesting StockExtract with model: {getattr(llm, 'model_name', 'unknown')} at {target_url}")
        _log(f"[LLM Debug] Prompt: {prompt[:500]}...")

        response = llm.invoke(prompt)
        raw = response if isinstance(response, str) else getattr(response, "content", str(response))
        
        # 调试日志：打印原始响应
        _log(f"[LLM Debug] Raw Response: {raw}")

        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            data = _json.loads(m.group(0))
            llm_name = (data.get("stock_name") or "").strip() or None
            llm_date = data.get("date") or None
            llm_horizons = data.get("horizons") or ["short"]
            llm_focus_areas = data.get("focus_areas") or []
            llm_specific_questions = data.get("specific_questions") or []
            llm_user_context = normalize_user_context(data.get("user_context") or {})
    except Exception as e:
        _log(f"[StockExtract] LLM failed: {e}")

    if not llm_name:
        if fast_symbol:
            _log(f"[StockExtract] LLM 未返回 stock_name，使用 regex 兜底: {fast_symbol}")
            return fast_symbol, fast_fallback_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context
        # [UPSTREAM-081-001] LLM 失败（限流/下线/网络）且 regex 无果时，用原文在
        # 已预热的本地股票名单里做一次 fail-closed 兜底；歧义或冷缓存保持 None。
        local_code = _resolve_cn_name_from_text_cached(text)
        if local_code:
            _log(f"[StockExtract] LLM 失败，本地名单从原文兜底命中: {local_code}")
            return local_code, fast_fallback_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context
        _log(f"[StockExtract] LLM returned no stock name for: '{text[:40]}'")
        return None, None, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    _log(f"[StockExtract] LLM extracted name='{llm_name}', date={llm_date}, horizons={llm_horizons}")
    resolved_llm_date = llm_date or fast_date

    # ── Step 2: If looks like a direct code (digits / letters), normalize it ──
    direct_symbol = _normalize_analysis_symbol(llm_name)
    if _is_valid_analysis_symbol(direct_symbol):
        _log(f"[StockExtract] Direct code: {direct_symbol}")
        return direct_symbol, resolved_llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    # ── Step 3: Search akshare A-share name database ──────────────────────────
    local_code = _search_cn_stock_by_name(llm_name)
    if local_code:
        _log(f"[StockExtract] akshare match: '{llm_name}' → {local_code}")
        return local_code, resolved_llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    # ── Step 4: Last resort — treat LLM name as a raw code ────────────────────
    fallback = _normalize_analysis_symbol(llm_name)
    if _is_valid_analysis_symbol(fallback):
        _log(f"[StockExtract] Fallback normalize: '{llm_name}' → {fallback}")
        return fallback, resolved_llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    if fast_symbol:
        _log(f"[StockExtract] LLM 名 '{llm_name}' 无法解析为代码，使用 regex 兜底: {fast_symbol}")
        return fast_symbol, llm_date or fast_fallback_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

    _log(f"[StockExtract] Could not resolve '{llm_name}' to a stock code")
    return None, llm_date, llm_horizons, llm_focus_areas, llm_specific_questions, llm_user_context

@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    current_user: UserDB = Depends(_require_api_user),
):
    text = _extract_chat_text(request.messages)
    if await asyncio.to_thread(_query_has_multiple_explicit_instruments, text):
        raise HTTPException(
            status_code=422,
            detail="一次分析只支持一个明确标的，请拆分多标的请求。",
        )
    config = await asyncio.to_thread(_build_runtime_config, request.config_overrides, user_id=current_user.id)

    # ── 流式模式：立刻返回 SSE 流，在后台异步提取意图再启动任务 ──────────────────
    # 这样用户提交查询后立刻收到 job.ready，不用等待 thinking 模型的 StockExtract。
    if request.stream:
        job_id = uuid4().hex

        async def _extract_and_run():
            try:
                symbol, trade_date, horizons, focus_areas, specific_questions, inferred_user_context = \
                    await _ai_extract_symbol_and_date_streaming(text, config, job_id)

                if not symbol:
                    _emit_job_event(job_id, "job.failed", {
                        "error": "抱歉，我没能从您的消息中识别出股票标的。请输入代码（如 600519.SH）或可识别的公司名称。"
                    })
                    return
                symbol = _normalize_analysis_symbol(symbol)
                if not _is_valid_analysis_symbol(symbol):
                    _emit_job_event(job_id, "job.failed", {
                        "error": "分析标的格式或交易所后缀无效。"
                    })
                    return
                trade_date = _resolve_analysis_trade_date(
                    symbol=symbol,
                    current_trade_date=trade_date or market_today_str(symbol),
                    parsed_query_date=trade_date,
                    trade_date_was_explicit=False,
                    query_text=text,
                )
                if not _is_valid_analysis_trade_date(trade_date):
                    _emit_job_event(job_id, "job.failed", {
                        "error": "分析日期无效，请使用真实存在的 YYYY-MM-DD 日期。"
                    })
                    return

                pre_intent = {
                    "raw_query": text,
                    "ticker": symbol,
                    "horizons": horizons,
                    "focus_areas": focus_areas,
                    "specific_questions": specific_questions,
                }
                explicit_context = _extract_request_user_context(request)

                def _load_user_context() -> Dict[str, Any]:
                    with get_db_ctx() as db:
                        return _compose_analysis_user_context(
                            db,
                            current_user.id,
                            symbol,
                            explicit_context=explicit_context,
                            inferred_context=inferred_user_context,
                        )

                merged_user_context = await asyncio.to_thread(_load_user_context)
                pre_intent["user_context"] = merged_user_context
                analyze_req = AnalyzeRequest(
                    symbol=symbol,
                    trade_date=trade_date or market_today_str(symbol),
                    selected_analysts=request.selected_analysts,
                    config_overrides=request.config_overrides,
                    dry_run=request.dry_run,
                    query=text,
                    horizons=horizons,
                    user_intent=pre_intent,
                    **explicit_context,
                    runtime_tier=request.runtime_tier,  # [PERF-004]
                    confirmed_full_ta=request.confirmed_full_ta,  # [PERF-004]
                    runtime_profile=request.runtime_profile,  # [PERF-004]
                )
                now = _utcnow_iso()
                _set_job(
                    job_id,
                    job_id=job_id,
                    user_id=current_user.id,
                    status="pending",
                    created_at=now,
                    started_at=None,
                    finished_at=None,
                    symbol=analyze_req.symbol,
                    trade_date=analyze_req.trade_date,
                    error=None,
                    result=None,
                    decision=None,
                )
                _emit_job_event(
                    job_id,
                    "job.created",
                    {"job_id": job_id, "symbol": analyze_req.symbol, "trade_date": analyze_req.trade_date},
                )
                await _run_job(job_id, analyze_req, True, True, current_user.id, "chat")
            except Exception as exc:
                _log(f"[chat] _extract_and_run failed: {exc}")
                _emit_job_event(
                    job_id,
                    "job.failed",
                    {"error": _humanize_analysis_error(str(exc))},
                )

        _create_tracked_task(_extract_and_run())
        return StreamingResponse(
            _stream_job_events(job_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # ── 非流式模式：保持原有阻塞行为 ─────────────────────────────────────────────
    symbol, trade_date, horizons, focus_areas, specific_questions, inferred_user_context = \
        await asyncio.to_thread(_ai_extract_symbol_and_date, text, config)

    if not symbol:
        raise HTTPException(status_code=400, detail="抱歉，我没能从您的消息中识别出股票标的。请输入代码（如 600519.SH）或可识别的公司名称。")
    symbol = _normalize_analysis_symbol(symbol)
    if not _is_valid_analysis_symbol(symbol):
        raise HTTPException(status_code=400, detail="分析标的格式或交易所后缀无效。")
    trade_date = _resolve_analysis_trade_date(
        symbol=symbol,
        current_trade_date=trade_date or market_today_str(symbol),
        parsed_query_date=trade_date,
        trade_date_was_explicit=False,
        query_text=text,
    )
    if not _is_valid_analysis_trade_date(trade_date):
        raise HTTPException(
            status_code=422,
            detail="分析日期无效，请使用真实存在的 YYYY-MM-DD 日期。",
        )

    pre_intent = {
        "raw_query": text,
        "ticker": symbol,
        "horizons": horizons,
        "focus_areas": focus_areas,
        "specific_questions": specific_questions,
    }
    explicit_context = _extract_request_user_context(request)

    def _load_user_context_nonstream() -> Dict[str, Any]:
        with get_db_ctx() as db:
            return _compose_analysis_user_context(
                db,
                current_user.id,
                symbol,
                explicit_context=explicit_context,
                inferred_context=inferred_user_context,
            )

    merged_user_context = await asyncio.to_thread(_load_user_context_nonstream)
    pre_intent["user_context"] = merged_user_context
    analyze_req = AnalyzeRequest(
        symbol=symbol,
        trade_date=trade_date or market_today_str(symbol),
        selected_analysts=request.selected_analysts,
        config_overrides=request.config_overrides,
        dry_run=request.dry_run,
        query=text,
        horizons=horizons,
        user_intent=pre_intent,
        **explicit_context,
        runtime_tier=request.runtime_tier,  # [PERF-004]
        confirmed_full_ta=request.confirmed_full_ta,  # [PERF-004]
        runtime_profile=request.runtime_profile,  # [PERF-004]
    )
    job_id = uuid4().hex
    now = _utcnow_iso()
    _set_job(
        job_id,
        job_id=job_id,
        user_id=current_user.id,
        status="pending",
        created_at=now,
        started_at=None,
        finished_at=None,
        symbol=analyze_req.symbol,
        trade_date=analyze_req.trade_date,
        error=None,
        result=None,
        decision=None,
    )
    _emit_job_event(
        job_id,
        "job.created",
        {"job_id": job_id, "symbol": analyze_req.symbol, "trade_date": analyze_req.trade_date},
    )
    if request.dry_run:
        await _run_job(job_id, analyze_req, True, True, current_user.id, "chat")
        status_text = _get_job(job_id).get("status", "completed")
        decision_text = _get_job(job_id).get("decision", "DRY_RUN")
        return {
            "id": f"chatcmpl-{job_id}",
            "object": "chat.completion",
            "created": int(datetime.now().timestamp()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": (
                            f"已完成分析任务：{job_id}\n"
                            f"symbol={analyze_req.symbol}, trade_date={analyze_req.trade_date}\n"
                            f"status={status_text}, decision={decision_text}"
                        ),
                    },
                }
            ],
        }
    _create_tracked_task(_run_job(job_id, analyze_req, True, True, current_user.id, "chat"))
    return {
        "id": f"chatcmpl-{job_id}",
        "object": "chat.completion",
        "created": int(datetime.now().timestamp()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": (
                        f"已启动分析任务：{job_id}\n"
                        f"symbol={analyze_req.symbol}, trade_date={analyze_req.trade_date}\n"
                        f"可通过 /v1/jobs/{job_id} 与 /v1/jobs/{job_id}/result 查询结果。"
                    ),
                },
            }
        ],
    }


# Report API Endpoints
def _attach_report_data_blockers_for_response(report: Any) -> Any:
    # [DATA-021] report_data_blockers
    # [REPORT-UX-003] wait_reason_codes — also surfaced at top level so the
    # frontend can render the WAIT reason chips without digging into result_data.
    # [KB-003] local_knowledge_summary — surfaced at top level so the frontend
    # can render the "本地知识补充" section without digging into result_data.
    result_data = getattr(report, "result_data", None)
    if isinstance(result_data, dict):
        # [PLAYBOOK-001] lifecycle_contract — mirror optional report fields
        # from result_data; ReportDB intentionally has no migration yet.
        # Normalize both fields before exposing them. Older/manual rows may
        # contain arbitrary values, and the response contract must never make
        # an invalid lifecycle stage look like a valid one.
        from tradingagents.tradeflow.playbook_contract import (
            normalize_playbook_stage,
            playbook_contract_from_dict,
            playbook_contract_summary,
        )

        raw_contract = result_data.get("playbook_contract")
        raw_summary = result_data.get("playbook_summary")
        summary_source = (
            raw_contract
            if isinstance(raw_contract, dict)
            else raw_summary
            if isinstance(raw_summary, dict)
            else result_data
        )
        normalized_contract = playbook_contract_from_dict(summary_source)
        top_level_stage = normalize_playbook_stage(result_data.get("playbook_stage"))
        if top_level_stage is not None:
            normalized_contract.playbook_stage = top_level_stage
        setattr(report, "playbook_stage", normalized_contract.playbook_stage)
        setattr(report, "playbook_summary", playbook_contract_summary(normalized_contract))
        setattr(report, "data_blockers", result_data.get("data_blockers"))
        setattr(report, "data_blocker_summary", result_data.get("data_blocker_summary"))
        setattr(
            report,
            "local_knowledge_block",
            result_data.get("local_knowledge_block"),
        )
        setattr(
            report,
            "local_knowledge_summary",
            result_data.get("local_knowledge_summary"),
        )
        wait_codes = result_data.get("wait_reason_codes")
        if wait_codes is None:
            # Recompute on read for legacy rows that predate REPORT-UX-003 so
            # the frontend still gets an explanation instead of a flat label.
            try:
                resolved = report_service.resolve_report_fields(result_data=result_data)
                wait_codes = resolved.get("wait_reason_codes") or []
            except Exception:
                wait_codes = []
        from tradingagents.graph.signal_processing import WAIT_REASON_LABELS
        codes_list = list(wait_codes or [])
        setattr(report, "wait_reason_codes", codes_list)
        setattr(
            report,
            "wait_reason_labels",
            {code: WAIT_REASON_LABELS.get(code, code) for code in codes_list},
        )
        # [KB-003] local_knowledge_raw_evidence — recompute on read for legacy
        # rows that predate KB-003 so the frontend still gets the section. Only
        # runs when symbol is available; never alters decisions or gates.
        # [KB-008] research_attention_integration — surfaced at top level so
        # the frontend can render 研报关注度 / 主题交叉度 without digging into
        # result_data; recompute on read for legacy rows.
        if (
            getattr(report, "local_knowledge_block", None) is None
            and getattr(report, "symbol", None)
        ):
            try:
                enriched = report_service.attach_report_local_knowledge(
                    result_data=dict(result_data),
                    symbol=getattr(report, "symbol", None),
                )
                if isinstance(enriched, dict):
                    setattr(
                        report,
                        "local_knowledge_block",
                        enriched.get("local_knowledge_block"),
                    )
                    setattr(
                        report,
                        "local_knowledge_summary",
                        enriched.get("local_knowledge_summary"),
                    )
                    setattr(
                        report,
                        "research_attention_score",
                        enriched.get("research_attention_score"),
                    )
                    setattr(
                        report,
                        "knowledge_theme_count",
                        enriched.get("knowledge_theme_count"),
                    )
                    setattr(
                        report,
                        "research_attention_summary",
                        enriched.get("research_attention_summary"),
                    )
                    setattr(
                        report,
                        "research_attention_block",
                        enriched.get("research_attention_block"),
                    )
            except Exception:
                pass
        else:
            # 即使 local_knowledge_block 已经在 result_data 里（新写入的报告），
            # 也把 KB-008 顶层字段同步出来，方便前端直接读。
            for fld in (
                "research_attention_score",
                "knowledge_theme_count",
                "research_attention_summary",
                "research_attention_block",
            ):
                if getattr(report, fld, None) is None:
                    setattr(report, fld, result_data.get(fld))
        # [HY-004] half_year_report_block — surfaced at top level so the
        # frontend can render "半年报事实对照" without digging into
        # result_data. Recompute on read for legacy rows that predate HY-004
        # so the section still appears; never alters decisions or gates.
        if (
            getattr(report, "half_year_facts_block", None) is None
            and getattr(report, "symbol", None)
        ):
            try:
                hy_enriched = report_service.attach_report_half_year_facts(
                    result_data=dict(result_data),
                    symbol=getattr(report, "symbol", None),
                )
                if isinstance(hy_enriched, dict):
                    setattr(
                        report,
                        "half_year_facts_block",
                        hy_enriched.get("half_year_facts_block"),
                    )
                    setattr(
                        report,
                        "half_year_facts_summary",
                        hy_enriched.get("half_year_facts_summary"),
                    )
                    setattr(
                        report,
                        "half_year_facts_status",
                        hy_enriched.get("half_year_facts_status"),
                    )
            except Exception:
                pass
        else:
            for hy_fld in (
                "half_year_facts_block",
                "half_year_facts_summary",
                "half_year_facts_status",
            ):
                if getattr(report, hy_fld, None) is None:
                    setattr(report, hy_fld, result_data.get(hy_fld))
    return report


@app.post("/v1/reports", response_model=ReportResponse)
def create_report_endpoint(
    request: ReportCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """手动创建报告（通常由系统自动调用）."""
    report = report_service.create_report(
        db=db,
        symbol=request.symbol,
        trade_date=request.trade_date,
        decision=request.decision,
        result_data=request.result_data,
        playbook_stage=request.playbook_stage,
        playbook_contract=request.playbook_contract,
        user_id=current_user.id,
    )
    _attach_report_data_blockers_for_response(report)
    background_tasks.add_task(
        _send_report_bark_notification_background,
        current_user.id,
        report.id,
        report.symbol,
        "manual_report_create",
    )
    return report


@app.get("/v1/announcements/latest", response_model=LatestAnnouncementResponse)
def get_latest_announcement():
    return {"announcement": _load_latest_announcement()}


@app.get("/v1/reports", response_model=ReportListResponse)
def list_reports(
    symbol: Optional[str] = Query(None, description="按股票代码筛选"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """获取报告列表."""
    total = report_service.count_reports(db=db, user_id=current_user.id, symbol=symbol)
    reports = report_service.get_reports_by_user(
        db=db,
        user_id=current_user.id,
        symbol=symbol,
        skip=skip,
        limit=limit,
    )
    code_to_name = _get_reverse_stock_map_cached_only()
    for r in reports:
        r.name = code_to_name.get(r.symbol, r.symbol)
        _attach_job_runtime_state(r, str(getattr(r, "id", "")))
    return {"total": total, "reports": reports}


@app.post("/v1/reports/latest-by-symbols", response_model=LatestReportsBySymbolsResponse)
def list_latest_reports_by_symbols(
    body: LatestReportsBySymbolsRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    reports = report_service.get_latest_reports_by_symbols(
        db=db,
        user_id=current_user.id,
        symbols=body.symbols,
    )
    return {"reports": reports}


@app.get("/v1/reports/{report_id}", response_model=ReportDetailResponse)
def get_report_endpoint(
    report_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """获取报告详情."""
    report = report_service.get_report(db, report_id, user_id=current_user.id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if str(report.status or "") in report_service.ACTIVE_REPORT_STATUSES and not _get_job(report_id):
        report = report_service.finalize_orphan_report(db, report)
    code_to_name = _get_reverse_stock_map()
    report.name = code_to_name.get(report.symbol, report.symbol)
    _attach_job_runtime_state(report, report_id)
    _attach_report_data_blockers_for_response(report)
    return report


@app.delete("/v1/reports/{report_id}")
def delete_report_endpoint(
    report_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """删除报告."""
    success = report_service.delete_report(db, report_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"message": "报告已删除"}


@app.post("/v1/reports/batch/delete", response_model=ReportBatchDeleteResponse)
def batch_delete_reports_endpoint(
    body: ReportBatchDeleteRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    try:
        return report_service.batch_delete_reports(db, body.report_ids, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/v1/reports/{report_id}/export/feishu", response_model=FeishuExportResponse)
def export_report_to_feishu_endpoint(
    report_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
):
    """[B-003] 导出报告为飞书云文档."""
    report = report_service.get_report(db, report_id, user_id=current_user.id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if str(report.status or "") != "completed":
        raise HTTPException(status_code=400, detail="报告尚未完成，无法导出")
    result = feishu_export_service.export_report_to_feishu(report)
    if not result["success"]:
        raise HTTPException(status_code=502, detail=result.get("error", "导出失败"))
    return FeishuExportResponse(**result)


# ─── API Token Endpoints ────────────────────────────────────────────────────

@app.get("/v1/tokens", response_model=List[UserTokenListItem])
def list_tokens(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_web_user),
):
    """获取当前用户的所有 API Token（不返回完整 token）。"""
    return token_service.list_user_tokens(db, current_user.id)


@app.post("/v1/tokens", response_model=UserTokenResponse)
def create_token(
    request: UserTokenCreateRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_web_user),
):
    """创建一个新的 API Token。完整 token 仅在此接口返回一次。"""
    try:
        return token_service.create_token(db, current_user.id, request.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/v1/tokens/{token_id}")
def delete_token(
    token_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_web_user),
):
    """吊销并删除一个 API Token。"""
    success = token_service.delete_token(db, current_user.id, token_id)
    if not success:
        raise HTTPException(status_code=404, detail="Token 不存在")
    return {"message": "Token 已吊销"}


# ─── Backtest Endpoints ───────────────────────────────────────────────────────

from api.services import backtest_service as _bt


class BacktestRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    selected_analysts: List[str] = ["market", "news", "fundamentals", "sentiment"]
    hold_days: int = 5
    sample_interval: int = 7
    config_overrides: Optional[Dict[str, Any]] = None


@app.post("/v1/backtest")
def submit_backtest(
    request: BacktestRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_api_user),
) -> Dict:
    """提交历史回测任务，返回 job_id."""
    config = _build_runtime_config(request.config_overrides or {}, user_id=current_user.id, db=db)
    job_id = _bt.submit(
        symbol=request.symbol,
        start_date=request.start_date,
        end_date=request.end_date,
        selected_analysts=request.selected_analysts,
        hold_days=request.hold_days,
        sample_interval=request.sample_interval,
        config=config,
    )
    return {"job_id": job_id, "status": "pending"}


@app.get("/v1/backtest")
def list_backtests() -> Dict:
    """列出所有回测任务."""
    jobs = _bt.list_jobs()
    return {"jobs": jobs, "total": len(jobs)}


@app.get("/v1/backtest/{job_id}")
def get_backtest(job_id: str) -> Dict:
    """获取回测任务状态和结果."""
    job = _bt.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="回测任务不存在")
    return job


@app.delete("/v1/backtest/{job_id}")
def delete_backtest(job_id: str) -> Dict:
    """删除回测任务."""
    if not _bt.delete_job(job_id):
        raise HTTPException(status_code=404, detail="回测任务不存在")
    return {"message": "已删除"}


# ─── Runtime Config Endpoints ────────────────────────────────────────────────

_CONFIG_ALLOWED_KEYS = {
    "llm_provider", "deep_think_llm", "quick_think_llm",
    "backend_url", "max_debate_rounds", "max_risk_discuss_rounds",
}
_CONFIG_PREFERENCE_KEYS = {"email_report_enabled", "wecom_report_enabled", "bark_report_enabled"}
_CONFIG_MODEL_KEYS = ("llm_provider", "backend_url", "quick_think_llm", "deep_think_llm")
_CONFIG_MODEL_LABELS = {
    "quick_think_llm": "常规模型",
    "deep_think_llm": "推理模型",
}
_CONFIG_PROBE_TIMEOUT_SECONDS = 12.0
_CONFIG_PROBE_PROMPT = "Reply with the single word OK."
_CONFIG_WARMUP_TIMEOUT_SECONDS = 20.0
_CONFIG_WARMUP_PROMPT = "Reply with the single word OK."


def _mask_secret_value(value: Optional[str], *, head: int = 4, tail: int = 4) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) <= head + tail:
        return "*" * max(6, len(normalized))
    return f"{normalized[:head]}{'*' * max(6, len(normalized) - head - tail)}{normalized[-tail:]}"


def _mask_wecom_webhook(webhook_url: Optional[str]) -> Optional[str]:
    normalized = str(webhook_url or "").strip()
    if not normalized:
        return None
    prefix = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
    if normalized.startswith(prefix):
        masked_key = _mask_secret_value(normalized[len(prefix):])
        return f"{prefix}{masked_key}"
    if normalized.startswith("http"):
        if "key=" in normalized:
            base, key = normalized.rsplit("key=", 1)
            return f"{base}key={_mask_secret_value(key)}"
        return _mask_secret_value(normalized, head=18, tail=8)
    return _mask_secret_value(normalized)


def _mask_bark_url(bark_url: Optional[str]) -> Optional[str]:
    from api.services.bark_notification_service import mask_bark_url

    return mask_bark_url(bark_url)


def _warmup_model_names(config: Dict[str, Any]) -> List[str]:
    seen: set[str] = set()
    models: List[str] = []
    for key in ("quick_think_llm", "deep_think_llm"):
        value = str(config.get(key) or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        models.append(value)
    return models


def _warmup_model_targets(config: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
    targets: Dict[str, List[str]] = {}
    for key in ("quick_think_llm", "deep_think_llm"):
        model = str(config.get(key) or "").strip()
        if not model:
            continue
        labels = targets.setdefault(model, [])
        label = _CONFIG_MODEL_LABELS.get(key, key)
        if label not in labels:
            labels.append(label)
    return [(model, labels) for model, labels in targets.items()]


def _should_trigger_config_warmup(
    before_cfg: UserRuntimeConfigResponse,
    after_cfg: UserRuntimeConfigResponse,
    updates: UserRuntimeConfigUpdateRequest,
) -> bool:
    if not updates.warmup:
        return False
    if updates.force_warmup:
        return True
    if updates.api_key:
        return True
    before = before_cfg.model_dump()
    after = after_cfg.model_dump()
    return any(before.get(key) != after.get(key) for key in _CONFIG_MODEL_KEYS)


def _build_pending_runtime_config(
    updates: UserRuntimeConfigUpdateRequest,
    user_id: str,
    db: Session,
) -> Dict[str, Any]:
    config = _build_runtime_config({}, user_id=user_id, db=db)
    for key in _CONFIG_ALLOWED_KEYS:
        value = getattr(updates, key, None)
        if value is not None:
            config[key] = value

    config["llm_provider"] = auth_service.canonicalize_llm_provider(
        config.get("llm_provider"),
        config.get("backend_url"),
    )

    quick = config.get("quick_think_llm")
    deep = config.get("deep_think_llm")
    if not deep and quick:
        config["deep_think_llm"] = quick
    if not quick and deep:
        config["quick_think_llm"] = deep

    if updates.clear_api_key:
        config["api_key"] = ""
    elif updates.api_key:
        config["api_key"] = updates.api_key
    else:
        scoped_api_key = auth_service.get_user_provider_api_key(
            db,
            user_id,
            config.get("llm_provider"),
            config.get("backend_url"),
        )
        if scoped_api_key:
            config["api_key"] = scoped_api_key
        elif auth_service.has_user_provider_keys(db, user_id):
            config["api_key"] = ""
    return config


def _should_probe_runtime_config(
    before_cfg: UserRuntimeConfigResponse,
    pending_cfg: Dict[str, Any],
    updates: UserRuntimeConfigUpdateRequest,
) -> bool:
    if updates.clear_api_key:
        return False
    if updates.api_key:
        return True
    if not str(pending_cfg.get("api_key") or "").strip():
        return False

    before = before_cfg.model_dump()
    for key in _CONFIG_MODEL_KEYS:
        next_value = getattr(updates, key, None)
        if next_value is not None and next_value != before.get(key):
            return True
    return False


def _probe_runtime_config(config: Dict[str, Any]) -> Dict[str, str]:
    from tradingagents.llm_clients.factory import create_llm_client

    provider = str(config.get("llm_provider") or "openai")
    base_url = config.get("backend_url")
    api_key = str(config.get("api_key") or "").strip()
    model = str(config.get("quick_think_llm") or config.get("deep_think_llm") or "").strip()

    if not model or not api_key:
        return {"status": "skipped", "reason": "missing_model_or_key"}

    try:
        client = create_llm_client(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=_CONFIG_PROBE_TIMEOUT_SECONDS,
            max_retries=0,
        )
        llm = client.get_llm()
        response = llm.invoke(_CONFIG_PROBE_PROMPT)
        raw = response if isinstance(response, str) else getattr(response, "content", str(response))
        preview = str(raw).strip().replace("\n", " ")[:80] or "<empty>"
        return {"status": "ok", "model": model, "preview": preview}
    except Exception as exc:
        detail = str(exc).strip()
        lowered = detail.lower()
        if "401" in lowered or "invalid authentication" in lowered or "authenticationerror" in lowered:
            raise HTTPException(
                status_code=400,
                detail="模型 Key 验证失败：上游返回 401 Invalid Authentication，请检查 API Key 是否正确。",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail=f"模型连接验证失败：{detail[:200] or 'unknown error'}",
        ) from exc


def _invoke_runtime_warmup(
    config: Dict[str, Any],
    prompt: str,
    user_id: str,
    timeout: float = _CONFIG_WARMUP_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    from tradingagents.llm_clients.factory import create_llm_client

    provider = str(config.get("llm_provider") or "openai")
    base_url = config.get("backend_url")
    api_key = config.get("api_key")
    targets = _warmup_model_targets(config)

    if not targets:
        raise HTTPException(status_code=400, detail="请先配置至少一个可用模型。")

    _log(
        f"[LLM Warmup] user={user_id} invoking provider={provider} "
        f"models={[model for model, _ in targets]} base_url={base_url or 'default'}"
    )

    results: List[Dict[str, Any]] = []
    errors: List[str] = []
    for model, labels in targets:
        try:
            client = create_llm_client(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                max_retries=0,
            )
            llm = client.get_llm()
            response = llm.invoke(prompt)
            raw = response if isinstance(response, str) else getattr(response, "content", str(response))
            content = str(raw).strip() or "<empty>"
            preview = content.replace("\n", " ")[:80]
            _log(f"[LLM Warmup] user={user_id} model={model} success response={preview}")
            results.append({
                "model": model,
                "targets": labels,
                "content": content,
                "error": None,
            })
        except Exception as exc:
            detail = str(exc).strip() or "unknown error"
            errors.append(f"{model}: {detail}")
            logger.warning(
                "[LLM Warmup] user=%s model=%s failed: %s",
                user_id,
                model,
                exc,
            )
            results.append({
                "model": model,
                "targets": labels,
                "content": None,
                "error": detail[:200],
            })

    if not any(item.get("content") for item in results):
        raise HTTPException(
            status_code=400,
            detail=f"模型 warmup 失败：{'; '.join(errors)[:300]}",
        )

    return results


def _run_config_warmup(config: Dict[str, Any], user_id: str) -> None:
    models = _warmup_model_names(config)
    if not models:
        _log(f"[LLM Warmup] user={user_id} skipped: no models configured")
        return
    try:
        _invoke_runtime_warmup(config, _CONFIG_WARMUP_PROMPT, user_id, timeout=_CONFIG_WARMUP_TIMEOUT_SECONDS)
    except HTTPException as exc:
        logger.warning("[LLM Warmup] user=%s failed: %s", user_id, exc.detail)


def _config_response_for_user(user: Optional[UserDB], db: Session) -> UserRuntimeConfigResponse:
    cfg = _build_runtime_config({}, user_id=user.id if user else None, db=db)
    user_cfg = auth_service.get_user_llm_config(db, user.id) if user else None
    current_api_key_scope = auth_service.normalize_provider_key_scope(cfg.get("llm_provider"), cfg.get("backend_url"))
    api_key_scopes = auth_service.list_user_provider_key_scopes(db, user.id) if user else []
    api_key = auth_service.get_user_provider_api_key(
        db,
        user.id,
        cfg.get("llm_provider"),
        cfg.get("backend_url"),
    ) if user else None
    if not api_key and not api_key_scopes:
        api_key = auth_service.decrypt_secret_with_fallback(getattr(user_cfg, "api_key_encrypted", None))
    webhook_url = auth_service.decrypt_secret_with_fallback(getattr(user_cfg, "wecom_webhook_encrypted", None))
    bark_url = auth_service.decrypt_secret_with_fallback(getattr(user_cfg, "bark_url_encrypted", None))
    return UserRuntimeConfigResponse(
        llm_provider=cfg["llm_provider"],
        deep_think_llm=cfg["deep_think_llm"],
        quick_think_llm=cfg["quick_think_llm"],
        backend_url=cfg["backend_url"],
        max_debate_rounds=cfg["max_debate_rounds"],
        max_risk_discuss_rounds=cfg["max_risk_discuss_rounds"],
        has_api_key=bool(api_key),
        has_wecom_webhook=bool(webhook_url),
        wecom_webhook_display=_mask_wecom_webhook(webhook_url),
        has_bark_url=bool(bark_url),
        bark_url_display=_mask_bark_url(bark_url),
        server_fallback_enabled=bool(cfg.get("server_fallback_enabled", True)),
        email_report_enabled=user.email_report_enabled if user and hasattr(user, 'email_report_enabled') else True,
        wecom_report_enabled=user.wecom_report_enabled if user and hasattr(user, "wecom_report_enabled") else True,
        bark_report_enabled=user.bark_report_enabled if user and hasattr(user, "bark_report_enabled") else True,
        default_analysts=json.loads(user_cfg.default_analysts) if user_cfg and user_cfg.default_analysts else ["market", "social", "news", "fundamentals", "macro", "smart_money", "volume_price"],
        current_api_key_scope=current_api_key_scope,
        api_key_scopes=api_key_scopes,
    )


@app.post("/v1/auth/request-code")
def request_login_code(request: AuthRequestCodeRequest):
    email = auth_service.normalize_email(request.email)
    if not re.match(r"^[^@\s]+@[^@\s.]+\.[^@\s.]+$", email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    with get_db_ctx() as db:
        code = auth_service.upsert_login_code(db, email)
    # DB session 已释放，SMTP 不会阻塞连接池
    dev_code = auth_service.send_login_code(email, code)
    response = {"message": "验证码已发送"}
    if dev_code:
        response["dev_code"] = dev_code
    return response


@app.post("/v1/auth/verify-code", response_model=AuthVerifyCodeResponse)
def verify_login_code(body: AuthVerifyCodeRequest, request: Request, db: Session = Depends(get_db)):
    user = auth_service.verify_login_code(db, body.email, body.code, client_ip=_get_real_ip(request))
    if not user:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    access_token = auth_service.create_access_token(user)
    return AuthVerifyCodeResponse(access_token=access_token, user=user)


@app.get("/v1/auth/me", response_model=UserResponse)
def get_me(current_user: UserDB = Depends(_require_web_user)):
    return current_user


@app.get("/v1/config", response_model=UserRuntimeConfigResponse)
def get_runtime_config(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_web_user),
):
    """获取当前用户运行时配置。"""
    return _config_response_for_user(current_user, db)


@app.get("/v1/config/model-catalog", response_model=ModelApiCatalogResponse)
def get_model_api_catalog_endpoint(
    current_user: UserDB = Depends(_require_web_user),
):
    """返回项目已整理的模型/API 端点目录，不包含任何密钥。"""
    from tradingagents.llm_clients import get_model_api_catalog

    return get_model_api_catalog()


@app.get(
    "/v1/config/source-capability-matrix",
    response_model=SourceCapabilityMatrixResponse,
)
def get_source_capability_matrix_endpoint(
    current_user: UserDB = Depends(_require_web_user),
):
    """返回数据源能力矩阵（不含任何密钥 / live 调用）。

    [DATA-023] source_capability_matrix
    """
    from tradingagents.dataflows.source_capability_matrix import (
        get_source_capability_matrix,
    )

    return get_source_capability_matrix()


@app.patch("/v1/config")
def update_runtime_config(
    updates: UserRuntimeConfigUpdateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_web_user),
):
    """更新当前用户运行时配置，下次分析时生效。"""
    normalized_wecom_webhook = None
    if updates.wecom_webhook_url:
        from api.services.wecom_notification_service import normalize_webhook_url

        try:
            normalized_wecom_webhook = normalize_webhook_url(updates.wecom_webhook_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized_bark_url = None
    if updates.bark_url:
        from api.services.bark_notification_service import normalize_bark_url

        try:
            normalized_bark_url = normalize_bark_url(updates.bark_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    persistent_user = db.query(UserDB).filter(UserDB.id == current_user.id).first() or current_user
    before_cfg = _config_response_for_user(persistent_user, db)
    pending_cfg = _build_pending_runtime_config(updates, persistent_user.id, db)
    if _should_probe_runtime_config(before_cfg, pending_cfg, updates):
        probe = _probe_runtime_config(pending_cfg)
        _log(
            f"[LLM Probe] user={persistent_user.id} provider={pending_cfg.get('llm_provider')} "
            f"model={probe.get('model', '')} status={probe.get('status')}"
        )
    row = auth_service.upsert_user_llm_config(
        db,
        persistent_user.id,
        llm_provider=updates.llm_provider,
        deep_think_llm=updates.deep_think_llm,
        quick_think_llm=updates.quick_think_llm,
        backend_url=updates.backend_url,
        max_debate_rounds=updates.max_debate_rounds,
        max_risk_discuss_rounds=updates.max_risk_discuss_rounds,
        api_key=updates.api_key,
        wecom_webhook_url=normalized_wecom_webhook,
        bark_url=normalized_bark_url,
        clear_api_key=updates.clear_api_key,
        clear_wecom_webhook=updates.clear_wecom_webhook,
        clear_bark_url=updates.clear_bark_url,
        default_analysts=updates.default_analysts,
    )
    if updates.api_key:
        auth_service.upsert_user_provider_api_key(
            db,
            persistent_user.id,
            pending_cfg.get("llm_provider"),
            pending_cfg.get("backend_url"),
            updates.api_key,
        )
    elif updates.clear_api_key:
        auth_service.clear_user_provider_api_key(
            db,
            persistent_user.id,
            pending_cfg.get("llm_provider"),
            pending_cfg.get("backend_url"),
        )
    user_pref_updated = False
    if updates.email_report_enabled is not None:
        persistent_user.email_report_enabled = updates.email_report_enabled
        user_pref_updated = True
    if updates.wecom_report_enabled is not None:
        persistent_user.wecom_report_enabled = updates.wecom_report_enabled
        user_pref_updated = True
    if updates.bark_report_enabled is not None:
        persistent_user.bark_report_enabled = updates.bark_report_enabled
        user_pref_updated = True
    if user_pref_updated:
        db.commit()
    current_cfg = _config_response_for_user(persistent_user, db)
    warmup_models = _warmup_model_names(current_cfg.model_dump())
    should_warmup = _should_trigger_config_warmup(before_cfg, current_cfg, updates)
    warmup_payload: Dict[str, Any]
    if should_warmup and warmup_models:
        warmup_payload = {
            "requested": True,
            "triggered": True,
            "status": "scheduled",
            "models": warmup_models,
            "message": f"模型配置已保存，后台正在预热 {len(warmup_models)} 个模型。",
        }
        background_tasks.add_task(
            _run_config_warmup,
            _build_runtime_config({}, user_id=persistent_user.id, db=db),
            persistent_user.id,
        )
    elif updates.warmup:
        warmup_payload = {
            "requested": True,
            "triggered": False,
            "status": "skipped",
            "models": warmup_models,
            "message": "模型配置已保存，本次未触发 warmup。",
        }
    else:
        warmup_payload = {
            "requested": False,
            "triggered": False,
            "status": "disabled",
            "models": [],
            "message": "模型配置已保存。",
        }
    filtered = {
        k: v
        for k, v in updates.model_dump().items()
        if v is not None
        and k not in {"api_key", "wecom_webhook_url", "bark_url", "warmup", "force_warmup"}
        and (
            k in _CONFIG_ALLOWED_KEYS
            or k in _CONFIG_PREFERENCE_KEYS
            or (k in {"clear_api_key", "clear_wecom_webhook", "clear_bark_url"} and bool(v))
        )
    }
    return {
        "message": "用户配置已更新",
        "applied": filtered,
        "has_api_key": bool(row.api_key_encrypted),
        "current": current_cfg,
        "warmup": warmup_payload,
    }


@app.post("/v1/config/warmup", response_model=UserRuntimeWarmupResponse)
def warmup_runtime_config(
    request: UserRuntimeWarmupRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_web_user),
):
    pending_cfg = _build_pending_runtime_config(request, current_user.id, db)
    prompt = (request.prompt or "").strip() or "你好"
    results = _invoke_runtime_warmup(pending_cfg, prompt, current_user.id)
    return {
        "prompt": prompt,
        "results": results,
    }


@app.post("/v1/config/wecom/warmup", response_model=WecomWebhookWarmupResponse)
async def warmup_wecom_webhook(
    request: WecomWebhookWarmupRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_web_user),
):
    from api.services.wecom_notification_service import build_test_message, normalize_webhook_url, send_message

    webhook_url = (request.wecom_webhook_url or "").strip()
    if not webhook_url:
        user_cfg = auth_service.get_user_llm_config(db, current_user.id)
        webhook_url = auth_service.decrypt_secret_with_fallback(getattr(user_cfg, "wecom_webhook_encrypted", None)) or ""
    if not webhook_url:
        raise HTTPException(status_code=400, detail="请先填写或保存企业微信 Webhook")
    try:
        webhook_url = normalize_webhook_url(webhook_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        sent = await asyncio.to_thread(send_message, build_test_message(request.content), webhook_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Webhook 测试发送失败：{exc}") from exc
    if not sent:
        raise HTTPException(status_code=400, detail="Webhook 测试发送失败，请检查地址或机器人状态")

    return {
        "sent": True,
        "message": "Webhook 测试发送成功",
        "webhook_display": _mask_wecom_webhook(webhook_url),
    }


@app.post("/v1/config/bark/warmup", response_model=BarkWarmupResponse)
async def warmup_bark(
    request: BarkWarmupRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(_require_web_user),
):
    from api.services.bark_notification_service import build_test_payload, normalize_bark_url, send_message

    bark_url = (request.bark_url or "").strip()
    if not bark_url:
        user_cfg = auth_service.get_user_llm_config(db, current_user.id)
        bark_url = auth_service.decrypt_secret_with_fallback(getattr(user_cfg, "bark_url_encrypted", None)) or ""
    if not bark_url:
        raise HTTPException(status_code=400, detail="请先填写或保存 Bark 地址/设备 Key")
    try:
        bark_url = normalize_bark_url(bark_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        sent = await asyncio.to_thread(send_message, build_test_payload(request.content), bark_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bark 测试发送失败：{exc}") from exc
    if not sent:
        raise HTTPException(status_code=400, detail="Bark 测试发送失败，请检查地址或 App 状态")

    return {
        "sent": True,
        "message": "Bark 测试发送成功",
        "bark_url_display": _mask_bark_url(bark_url),
    }


# ── Stock Search ──────────────────────────────────────────────────────────────

@app.get("/v1/market/stock-search")
def search_stocks(
    q: str = Query("", min_length=1, max_length=20),
    current_user: UserDB = Depends(_require_api_user),
):
    """Search stocks by code prefix or name substring."""
    q = q.strip()
    if not q:
        return {"results": []}

    name_to_code = _load_cn_stock_map()
    code_to_name = _get_reverse_stock_map()
    results = []
    q_upper = q.upper()

    for code, name in code_to_name.items():
        if code.upper().startswith(q_upper) or code.split(".")[0].startswith(q):
            results.append({"symbol": code, "name": name})
            if len(results) >= 20:
                break

    if len(results) < 20:
        for name, code in name_to_code.items():
            if q in name and not any(r["symbol"] == code for r in results):
                results.append({"symbol": code, "name": name})
                if len(results) >= 20:
                    break

    return {"results": results}


def _annotate_scheduled_with_imported_context(items: List[dict], db: Session, user_id: str) -> List[dict]:
    imported_map: Dict[str, Dict[str, Any]] = {}
    for item in portfolio_import_service.list_imported_positions(db, user_id):
        imported_map[item["symbol"]] = item
    from api.runtime_tier import build_scheduled_cost_meta  # [PERF-004] full_ta_cost_gate
    for item in items:
        imported = imported_map.get(item["symbol"])
        item["has_imported_context"] = imported is not None
        item["imported_current_position"] = imported.get("current_position") if imported else None
        item["imported_average_cost"] = imported.get("average_cost") if imported else None
        item["imported_trade_points_count"] = imported.get("trade_points_count") if imported else 0
        cost_meta = build_scheduled_cost_meta(
            trigger_time=item.get("trigger_time", "20:00"),
            last_run_status=item.get("last_run_status"),
        )
        item["cost_meta"] = cost_meta.to_dict()
    return items


def _merge_imported_user_context(*contexts: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    note_parts: List[str] = []
    for ctx in contexts:
        if not ctx:
            continue
        for key, value in ctx.items():
            if key == "user_notes":
                if value:
                    note_parts.append(str(value).strip())
                continue
            if value is not None:
                merged[key] = value
    if note_parts:
        merged["user_notes"] = "\n\n".join(part for part in note_parts if part)
    return normalize_user_context(merged)


def _build_imported_user_context(db: Session, user_id: str, symbol: str) -> Dict[str, Any]:
    context = portfolio_import_service.build_scheduled_user_context(db, user_id, symbol)
    return _merge_imported_user_context(context)


def _build_manual_imported_user_context(db: Session, user_id: str, symbol: str) -> Dict[str, Any]:
    """Build imported position context for manual/ad-hoc analysis runs."""
    return _build_imported_user_context(db, user_id, symbol)


def _attach_stock_names(items: List[dict], code_to_name: Dict[str, str]) -> List[dict]:
    for item in items:
        symbol = str(item.get("symbol") or "").upper()
        item["name"] = code_to_name.get(symbol, symbol or item.get("name") or "")
    return items


def _repair_portfolio_position_symbols(positions: List[dict]) -> List[dict]:
    """Correct OCR/manual imports when stock name and code disagree."""
    if not positions:
        return positions

    name_to_code = _load_cn_stock_map()
    code_to_name = _get_reverse_stock_map()
    repaired: List[dict] = []
    for raw in positions:
        item = dict(raw)
        name = str(item.get("name") or "").strip()
        symbol = _normalize_symbol(str(item.get("symbol") or "").strip())
        resolved_by_name = name_to_code.get(name) if name else None

        if resolved_by_name and symbol != resolved_by_name:
            item["symbol"] = resolved_by_name
            item["name"] = name or code_to_name.get(resolved_by_name, resolved_by_name)
            item["symbol_correction"] = {
                "from": symbol,
                "to": resolved_by_name,
                "reason": "name_code_mismatch",
            }
        else:
            item["symbol"] = symbol
            if not name and symbol in code_to_name:
                item["name"] = code_to_name[symbol]
        repaired.append(item)
    return repaired


@app.get("/v1/portfolio/imports")
def get_portfolio_import_state(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    return portfolio_import_service.get_import_state(db, current_user.id)


@app.post("/v1/portfolio/imports")
def sync_portfolio_import(
    body: PortfolioImportSyncRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    try:
        positions = _repair_portfolio_position_symbols([p.model_dump() for p in body.positions])
        return portfolio_import_service.sync_positions(
            db=db,
            user_id=current_user.id,
            positions=positions,
            source=body.source,
            auto_apply_scheduled=body.auto_apply_scheduled,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/v1/portfolio/imports", status_code=204)
def clear_portfolio_import_state(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    portfolio_import_service.clear_imported_portfolio(db, current_user.id)


# [TRACK-009] holdings_import_contract — OpenClaw/外部持仓契约（只读）
@app.get("/v1/portfolio/imports/contract")
def get_portfolio_import_contract(
    current_user: UserDB = Depends(_require_api_user),
):
    return portfolio_import_service.OPENCLAW_HOLDINGS_CONTRACT


# [TRACK-009] holdings_import_contract — dry-run 预览（不写库）
@app.post("/v1/portfolio/imports/dry-run")
def portfolio_import_dry_run(
    body: PortfolioImportDryRunRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    if (body.text is None or body.text.strip() == "") and not body.positions:
        raise HTTPException(400, "text 或 positions 至少需要提供一项")
    try:
        if body.text is not None and body.text.strip() != "":
            raw_rows = portfolio_import_service.parse_positions_text(body.text)
        else:
            raw_rows = [p.model_dump() for p in (body.positions or [])]
        validated = portfolio_import_service.validate_positions(raw_rows)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    diff = portfolio_import_service.dry_run_import(
        db=db,
        user_id=current_user.id,
        positions=validated["valid"],
        source=body.source,
    )
    # Preserve invalid-row diagnostics even though they're excluded from the
    # valid set used for the diff.
    diff["errors"] = validated["invalid"]
    diff["warnings"] = validated["warnings"]
    diff["valid_count"] = validated["valid_count"]
    diff["invalid_count"] = validated["invalid_count"]
    return diff


# [TRACK-009] holdings_import_contract — 从文本解析并提交
@app.post("/v1/portfolio/imports/import-text")
def portfolio_import_from_text(
    body: PortfolioImportTextRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    try:
        result = portfolio_import_service.import_positions_from_text(
            db=db,
            user_id=current_user.id,
            text=body.text,
            source=body.source,
            auto_apply_scheduled=body.auto_apply_scheduled,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@app.post("/v1/portfolio/parse-image")
async def parse_position_image_endpoint(
    file: UploadFile = File(...),
    mode: str = Form("position"),
    current_user: UserDB = Depends(_require_api_user),
):
    """Parse a broker position screenshot using server-side VLM.

    # [VLM-001] watchlist_table_parser
    mode='position' (default): existing position parsing.
    mode='watchlist': candidate table parsing with notes.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "只支持图片文件")

    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 10MB")

    try:
        if mode == "watchlist":
            # [VLM-001] watchlist_table_parser
            from api.services.vlm_position_parser import parse_watchlist_table_image
            items = await asyncio.to_thread(parse_watchlist_table_image, image_bytes, file.content_type)
            items = await asyncio.to_thread(_repair_portfolio_position_symbols, items)
            return {"mode": "watchlist", "items": items}
        else:
            from api.services.vlm_position_parser import parse_position_image
            positions = await asyncio.to_thread(parse_position_image, image_bytes, file.content_type)
            positions = await asyncio.to_thread(_repair_portfolio_position_symbols, positions)
            return {"positions": positions}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.warning("[parse-image] VLM parsing failed: %s", exc)
        raise HTTPException(500, "图片解析失败，请稍后重试") from exc


# [B-004] holdings_sync — TA ↔ investment-controller holdings sync
class HoldingsSyncRequest(BaseModel):
    file_path: str = ""
    # Keep this as a string so unsupported values reach the service's
    # structured invalid_direction response instead of becoming a generic 422.
    direction: str = "bidirectional"


_HOLDINGS_SYNC_CLIENT_ERRORS = {
    "invalid_direction",
    "path_not_allowed",
    "malformed_rows",
    "holdings_not_list",
    "unexpected_format",
    "json_parse_error",
}


@app.post("/v1/portfolio/holdings-sync")
def sync_holdings_with_controller(
    body: HoldingsSyncRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """Bidirectional holdings sync between TA and investment-controller.

    ``direction``: ``"export"`` (TA→file), ``"import"`` (file→TA),
    or ``"bidirectional"`` (both, default). Runtime tier=FAST_RADAR.
    """
    try:
        result = holdings_sync_service.sync_holdings(
            db=db,
            user_id=current_user.id,
            file_path=body.file_path or None,
            direction=body.direction,
        )
        error_code = str(result.get("error") or "").split(":", 1)[0]
        if not result.get("success") and error_code in _HOLDINGS_SYNC_CLIENT_ERRORS:
            raise HTTPException(400, detail=result)
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/portfolio/holdings-sync/status")
def get_holdings_sync_status(
    file_path: str = "",
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """Read-only sync status: diff between TA and external file."""
    return holdings_sync_service.get_sync_status(
        db=db,
        user_id=current_user.id,
        file_path=file_path or None,
    )


@app.get("/v1/portfolio/holdings-sync/external")
def read_external_holdings_file(
    file_path: str = "",
    current_user: UserDB = Depends(_require_api_user),
):
    """Read the external current_holdings.json (read-only)."""
    path = file_path or holdings_sync_service.get_default_sync_path()
    result = holdings_sync_service.read_external_holdings(path)
    error_code = str(result.get("error") or "").split(":", 1)[0]
    if error_code in _HOLDINGS_SYNC_CLIENT_ERRORS:
        raise HTTPException(400, detail=result)
    return result


@app.get("/v1/dashboard/tracking-board")
def get_dashboard_tracking_board(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    return tracking_board_service.get_tracking_board(db, current_user.id)


# [TRACK-002] tracking_board_v2_groups
@app.get("/v1/dashboard/tracking-board/v2")
def get_dashboard_tracking_board_v2(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    return tracking_board_service.get_tracking_board_v2(db, current_user.id)


# [IC-TA-001] investment_controller_context
@app.get("/v1/dashboard/investment-controller/context")
def get_investment_controller_context(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """Read-only context pack for the investment-controller.

    Aggregates holdings, observation warehouse, TradeFlow candidates, latest
    TA report summary, data health and pending TA-required items, plus the
    IC-TA-002 mandate daily report digest, recent report data blockers and
    soft controller routing hints. READ-ONLY: never writes state, never
    triggers TA/LLM. Every entry carries source + as_of; every bucket
    declares data_status (fresh/stale/missing/failed/skipped).
    """
    return investment_controller_context.get_investment_controller_context(db, current_user.id)


# [TRACK-NOTIFY-001] notification_payload_dry_run
class NotificationDryRunRequest(BaseModel):
    force_refresh: bool = False
    tf_db_path: str = ""


@app.post("/v1/dashboard/investment-controller/notify/dry-run")
def post_notification_dry_run(
    body: NotificationDryRunRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """生成飞书 / 总控官通知草稿 dry-run payload.

    第一阶段只产出本地预览（markdown + json），不读取 / 打印 webhook，
    不真实发送飞书。只有 P0/P1 且非数据不足的草稿进入 ``intraday_push``；
    P2/P3 与数据不足草稿只进 ``daily_digest``。同一标的同一事件 30 分钟内
    去重。runtime_tier=FAST_RADAR（不触发 LLM）。
    """
    return notification_draft_service.build_notification_dry_run(
        db, current_user.id,
        tf_db_path=body.tf_db_path,
        force_refresh=body.force_refresh,
    )


# [IC-TA-004] controller_briefing_payload
class BriefingPayloadRequest(BaseModel):
    scene: str = "pre_market"  # pre_market / intraday / post_market
    tf_db_path: str = ""


@app.post("/v1/dashboard/investment-controller/briefing/dry-run")
def post_briefing_payload_dry_run(
    body: BriefingPayloadRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """生成 investment-controller 飞书 briefing payload（dry-run）.

    三场景统一 payload：盘前（TA 调度 + 昊天主题）/ 盘中（观察 + 风险告警，不调度
    新 TA）/ 盘后（今日表现 + 数据缺口 + 次日 TA 候选）。每条 ta_request /
    watch_item / data_warning 带 notify_level（P0/P1 → intraday_push，P2/P3 →
    daily_digest）。只做调度与摘要，不下最终交易结论。dry-run：不调用 LLM、
    不发送飞书、不写数据库。runtime_tier=FAST_RADAR。
    """
    return controller_briefing_payload_service.build_briefing_payload_dry_run(
        db, current_user.id,
        scene=body.scene,
        tf_db_path=body.tf_db_path,
    )


# [M-010] feishu_notification_confirmation
class NotificationGenerateRequest(BaseModel):
    # Keep this open at the transport layer so service validation can
    # normalize case variants and return a structured 400 for bad channels.
    channel: str = "feishu"
    force_refresh: bool = False


@app.post("/v1/notifications/generate")
def post_notification_generate(
    body: NotificationGenerateRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """生成通知草稿并存入待确认队列（M-010 人工确认版）.

    从 notification_draft 引擎生成草稿，存入 notification_logs 表，
    状态为 pending_confirmation。用户后续通过 confirm 接口确认发送。
    runtime_tier=FAST_RADAR（不触发 LLM）。
    """
    try:
        return notification_confirmation_service.generate_pending(
            db, current_user.id,
            channel=body.channel,
            force_refresh=body.force_refresh,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/notifications/pending")
def get_notifications_pending(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """获取待确认通知队列（M-010）."""
    return notification_confirmation_service.list_pending(db, current_user.id)


@app.get("/v1/notifications/log")
def get_notifications_log(
    status: str | None = Query(None, description="按状态筛选"),
    limit: int = Query(100, ge=1, le=500),
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """获取通知历史日志（M-010）."""
    return notification_confirmation_service.list_log(
        db, current_user.id, status=status, limit=limit,
    )


class NotificationActionRequest(BaseModel):
    pass


@app.post("/v1/notifications/{notification_id}/confirm")
def post_notification_confirm(
    notification_id: str,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """确认单条通知并通过飞书 webhook 发送（M-010）."""
    return notification_confirmation_service.confirm_and_send(
        db, notification_id, current_user.id,
    )


@app.post("/v1/notifications/{notification_id}/dismiss")
def post_notification_dismiss(
    notification_id: str,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """拒绝单条通知，不再发送（M-010）."""
    return notification_confirmation_service.dismiss(
        db, notification_id, current_user.id,
    )


@app.post("/v1/notifications/confirm-all")
def post_notification_confirm_all(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """批量确认所有待发送通知（M-010）."""
    return notification_confirmation_service.confirm_all_pending(
        db, current_user.id,
    )


@app.post("/v1/config/feishu/warmup")
def post_feishu_webhook_warmup(
    current_user: UserDB = Depends(_require_web_user),
    db: Session = Depends(get_db),
):
    """测试飞书 Webhook 连通性（M-010）."""
    webhook_url = feishu_webhook_service.get_feishu_webhook_url()
    if not webhook_url:
        return {"success": False, "error": "飞书 Webhook 未配置 (FEISHU_WEBHOOK_URL)"}
    import asyncio as _asyncio
    try:
        ok = _asyncio.get_event_loop().run_until_complete(
            feishu_webhook_service.send_warmup(webhook_url)
        )
    except RuntimeError:
        ok = _asyncio.run(feishu_webhook_service.send_warmup(webhook_url))
    if ok:
        return {"success": True, "message": "飞书 Webhook 连通测试成功"}
    return {"success": False, "error": "飞书 Webhook 发送失败，请检查 URL 配置"}


# [KB-020] research_evidence_api
# Fixed routes under /v1/knowledge/research/evidence must be declared BEFORE
# the dynamic {symbol} route so FastAPI matches them first. Regression covered
# by tests/test_kb020_research_evidence_api.py::TestRouteOrderingRegression.
@app.get("/v1/knowledge/research/evidence/_meta")
def research_evidence_meta(
    current_user: UserDB = Depends(_require_api_user),
):
    """Cheap fixed-route probe so the UI can verify the evidence endpoint is
    available without issuing a per-symbol query. ``runtime_tier=FAST_RADAR``.

    Also serves as the route-ordering regression anchor: if a future fixed
    sub-path is added under this prefix it MUST be declared before
    ``/v1/knowledge/research/evidence/{symbol}`` or this route will be
    shadowed by the symbol param.
    """
    from api.runtime_tier import tradeflow_meta as _tradeflow_meta  # [PERF-001]
    return {
        "source": research_evidence_service.CONTEXT_SOURCE,
        "task": research_evidence_service.TASK_CODE,
        "buckets": list(research_evidence_service.ALL_BUCKETS),
        "allowed_window_months": list(research_evidence_service.ALLOWED_WINDOW_MONTHS),
        "default_window_months": research_evidence_service.DEFAULT_WINDOW_MONTHS,
        "runtime_tier_meta": _tradeflow_meta("research_evidence_lookup"),
        "read_only": True,
    }


# [KB-020] research_evidence_api — dynamic symbol route (MUST stay after _meta).
@app.get("/v1/knowledge/research/evidence/{symbol}")
def get_research_evidence(
    symbol: str,
    as_of: Optional[str] = Query(
        None, description="可选时间戳，仅回显，不触发 LLM/网络"
    ),
    window_months: int = Query(
        research_evidence_service.DEFAULT_WINDOW_MONTHS,
        description="KB-016 时间窗口（3/6/12 个月）",
    ),
    knowledge_root: Optional[str] = Query(
        None, description="可选知识库根目录覆盖（默认走 KB-006 配置）"
    ),
    current_user: UserDB = Depends(_require_api_user),
):
    """Aggregate all read-only research evidence for ``symbol``.

    Bundles KB-016 consensus matrix, KB-017 citation audit, KB-018 thesis
    timeline and HY-003 half-year facts into one response. Each bucket
    degrades independently — a single failure does not blank out the
    others.

    Constraints:
      - READ-ONLY: no LLM, no network, no DB writes, no full body text.
      - Never returns ``decision`` / ``action_label`` / ``buy_level``.
      - Source paths are validated to live inside the knowledge root.
      - Invalid symbol → 400.
      - runtime_tier=FAST_RADAR.
    """
    try:
        return research_evidence_service.build_research_evidence(
            symbol,
            as_of=as_of,
            window_months=window_months,
            knowledge_root=knowledge_root,
        )
    except research_evidence_service.InvalidSymbolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# [KB-006] local_knowledge_context_api
@app.get("/v1/knowledge/local/search")
def search_local_knowledge(
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    themes: Optional[str] = None,
    tags: Optional[str] = None,
    max_pages: Optional[int] = None,
    knowledge_root: Optional[str] = None,
    current_user: UserDB = Depends(_require_api_user),
):
    """Read-only search of Tree Work ``wiki/investment`` pages.

    Returns a slim summary (title / rel_path / updated_at / confidence /
    summary_snippet / risks / negative flags) of pages matching the supplied
    ``symbol`` / ``name`` / ``themes`` / ``tags``. Only the ``wiki/investment``
    partition is visible; other partitions (``inbox/``, ``raw/``, private
    notes) are never exposed.

    READ-ONLY: never writes to the knowledge base, never calls LLM, never
    hits the network. Supports disable via ``KNOWLEDGE_CONTEXT_DISABLED``
    / ``KNOWLEDGE_LOCAL_DISABLED`` env (returns ``data_status=skipped``).
    runtime_tier=FAST_RADAR.
    """
    theme_list = local_knowledge_context_service._split_multi(themes)
    tag_list = local_knowledge_context_service._split_multi(tags)
    return local_knowledge_context_service.search_local_knowledge(
        symbol=symbol,
        name=name,
        themes=theme_list or None,
        tags=tag_list or None,
        max_pages=max_pages,
        knowledge_root=knowledge_root,
    )


# ── Watchlist ─────────────────────────────────────────────────────────────────

@app.get("/v1/watchlist")
def list_watchlist(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    items = watchlist_service.list_watchlist(db, current_user.id)
    _attach_stock_names(items, _get_reverse_stock_map())
    return {"items": items}


@app.post("/v1/watchlist")
def add_to_watchlist(
    body: WatchlistAddRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    text = str(body.text or body.symbol or "").strip()
    if not text:
        raise HTTPException(400, "text or symbol is required")

    tokens = _split_watchlist_batch_text(text)
    if not tokens:
        raise HTTPException(400, "至少提供一个股票代码或名称")

    name_to_code = _load_cn_stock_map()
    code_to_name = _get_reverse_stock_map()

    resolved_entries: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    for idx, token in enumerate(tokens):
        symbol, name, error = _resolve_watchlist_identifier(token, name_to_code, code_to_name)
        if error:
            results.append({
                "_order": idx,
                "input": token,
                "status": "invalid",
                "message": error,
            })
            continue
        resolved_entries.append({
            "_order": idx,
            "input": token,
            "symbol": symbol,
            "name": name,
        })

    add_results = watchlist_service.add_watchlist_items(
        db,
        current_user.id,
        [entry["symbol"] for entry in resolved_entries],
    )
    for entry, result in zip(resolved_entries, add_results):
        item = result.get("item")
        if item:
            item["name"] = entry["name"]
            item["has_scheduled"] = False
        results.append({
            "_order": entry["_order"],
            "input": entry["input"],
            "symbol": entry["symbol"],
            "name": entry["name"],
            "status": result["status"],
            "message": result["message"],
            "item": item,
        })

    results.sort(key=lambda row: row["_order"])
    for row in results:
        row.pop("_order", None)
    summary = {
        "total": len(tokens),
        "added": sum(1 for row in results if row["status"] == "added"),
        "duplicate": sum(1 for row in results if row["status"] == "duplicate"),
        "failed": sum(1 for row in results if row["status"] in {"invalid", "failed"}),
    }
    message_parts = [f"共处理 {summary['total']} 项"]
    if summary["added"]:
        message_parts.append(f"新增 {summary['added']} 项")
    if summary["duplicate"]:
        message_parts.append(f"重复 {summary['duplicate']} 项")
    if summary["failed"]:
        message_parts.append(f"失败 {summary['failed']} 项")
    return {
        "message": "，".join(message_parts),
        "summary": summary,
        "results": results,
    }


# [VLM-001] watchlist_table_parser
class WatchlistBatchWithNotesRequest(BaseModel):
    entries: List[Dict[str, Any]] = Field(default_factory=list)


@app.post("/v1/watchlist/batch-notes")
def add_to_watchlist_batch_notes(
    body: WatchlistBatchWithNotesRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    """Batch-add watchlist items with optional notes (from VLM watchlist table parsing)."""
    if not body.entries:
        raise HTTPException(400, "entries is required")

    name_to_code = _load_cn_stock_map()
    code_to_name = _get_reverse_stock_map()

    resolved: List[Dict[str, Any]] = []
    for entry in body.entries:
        symbol_raw = str(entry.get("symbol", "")).strip()
        notes = entry.get("notes")
        symbol, name, _ = _resolve_watchlist_identifier(symbol_raw, name_to_code, code_to_name)
        if not symbol:
            symbol = symbol_raw
        if not name:
            name = code_to_name.get(symbol, symbol)
        # [DATA-009] watchlist_notes_persistence — pass structured fields
        resolved.append({
            "symbol": symbol,
            "name": name,
            "notes": notes,
            "topic": entry.get("topic"),
            "benefit_score": entry.get("benefit_score"),
            "consensus_score": entry.get("consensus_score"),
            "expected_window": entry.get("expected_window"),
            "evidence_gap": entry.get("evidence_gap"),
            "watchlist_note_suggested": entry.get("watchlist_note_suggested"),
        })

    add_results = watchlist_service.add_watchlist_items_with_notes(
        db, current_user.id, resolved
    )
    for entry, result in zip(resolved, add_results):
        item = result.get("item")
        if item:
            item["name"] = entry["name"]
            item["has_scheduled"] = False
        result["name"] = entry["name"]

    added = sum(1 for r in add_results if r["status"] == "added")
    duplicate = sum(1 for r in add_results if r["status"] == "duplicate")
    failed = sum(1 for r in add_results if r["status"] == "failed")
    message_parts = [f"共处理 {len(add_results)} 项"]
    if added:
        message_parts.append(f"新增 {added} 项")
    if duplicate:
        message_parts.append(f"重复 {duplicate} 项")
    if failed:
        message_parts.append(f"失败 {failed} 项")

    return {
        "message": "，".join(message_parts),
        "summary": {"total": len(add_results), "added": added, "duplicate": duplicate, "failed": failed},
        "results": add_results,
    }


@app.delete("/v1/watchlist/{item_id}", status_code=204)
def delete_from_watchlist(
    item_id: str,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    if not watchlist_service.delete_watchlist_item(db, current_user.id, item_id):
        raise HTTPException(404, "未找到该自选股")


# [VLM-001] watchlist_notes_protection
class WatchlistNotesUpdate(BaseModel):
    notes: str
    clear: bool = False


class WatchlistReorderRequest(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


@app.put("/v1/watchlist/reorder")
def reorder_watchlist(
    body: WatchlistReorderRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    try:
        watchlist_service.reorder_watchlist(db, current_user.id, body.items)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": "排序已更新"}


@app.patch("/v1/watchlist/{item_id}")
def update_watchlist_notes(
    item_id: str,
    body: WatchlistNotesUpdate,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    result = watchlist_service.update_watchlist_notes(db, current_user.id, item_id, body.notes, clear=body.clear)
    if not result:
        raise HTTPException(404, "未找到该自选股")
    return result


# ── Scheduled Analysis ────────────────────────────────────────────────────────

@app.get("/v1/scheduled")
def list_scheduled_analyses(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    items = scheduled_service.list_scheduled(db, current_user.id)
    _attach_stock_names(items, _get_reverse_stock_map_cached_only())
    return {"items": _annotate_scheduled_with_imported_context(items, db, current_user.id)}


@app.get("/v1/portfolio/overview", response_model=PortfolioOverviewResponse)
def get_portfolio_overview(
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    code_to_name = _get_reverse_stock_map_cached_only()

    watchlist_items = watchlist_service.list_watchlist(db, current_user.id)
    _attach_stock_names(watchlist_items, code_to_name)

    scheduled_items = scheduled_service.list_scheduled(db, current_user.id)
    _attach_stock_names(scheduled_items, code_to_name)
    scheduled_items = _annotate_scheduled_with_imported_context(scheduled_items, db, current_user.id)

    latest_reports = report_service.get_latest_reports_by_symbols(
        db=db,
        user_id=current_user.id,
        symbols=[item["symbol"] for item in watchlist_items],
    )
    for report in latest_reports:
        report.name = code_to_name.get(report.symbol, report.symbol)

    portfolio_import = portfolio_import_service.get_import_state(db, current_user.id)

    return {
        "watchlist": watchlist_items,
        "scheduled": scheduled_items,
        "latest_reports": latest_reports,
        "portfolio_import": portfolio_import,
    }


@app.post("/v1/scheduled", status_code=201)
def create_scheduled_analysis(
    body: dict,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    symbol = body.get("symbol", "").strip().upper()
    horizon = body.get("horizon", "short")
    trigger_time = body.get("trigger_time", "20:00")
    if not symbol:
        raise HTTPException(400, "symbol is required")
    code_to_name = _get_reverse_stock_map()
    if symbol not in code_to_name:
        raise HTTPException(400, f"未知的股票代码: {symbol}")
    try:
        item = scheduled_service.create_scheduled(db, current_user.id, symbol, horizon, trigger_time)
        item["name"] = code_to_name.get(symbol, symbol)
        _annotate_scheduled_with_imported_context([item], db, current_user.id)
        return item
    except ValueError as e:
        raise HTTPException(400, str(e))


def _extract_scheduled_update_kwargs(body: dict) -> dict:
    kwargs = {}
    if "is_active" in body:
        kwargs["is_active"] = bool(body["is_active"])
    if "horizon" in body:
        kwargs["horizon"] = body["horizon"]
    if "trigger_time" in body:
        kwargs["trigger_time"] = body["trigger_time"]
    return kwargs


@app.patch("/v1/scheduled/batch")
def batch_update_scheduled_analyses(
    body: ScheduledBatchUpdateRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    kwargs = _extract_scheduled_update_kwargs(body.model_dump(exclude_unset=True))
    if not kwargs:
        raise HTTPException(400, "至少提供一个更新字段")
    try:
        items = scheduled_service.batch_update_scheduled(
            db,
            current_user.id,
            body.item_ids,
            **kwargs,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    code_to_name = _get_reverse_stock_map()
    for item in items:
        item["name"] = code_to_name.get(item["symbol"], item["symbol"])
    return {"items": _annotate_scheduled_with_imported_context(items, db, current_user.id)}


@app.post("/v1/scheduled/batch/delete")
def batch_delete_scheduled_analyses(
    body: ScheduledBatchIdsRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    try:
        return scheduled_service.batch_delete_scheduled(db, current_user.id, body.item_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/v1/scheduled/batch/trigger", response_model=BatchScheduledTriggerResponse)
async def trigger_scheduled_analyses_batch(
    body: ScheduledBatchIdsRequest,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    if not body.item_ids:
        raise HTTPException(400, "请至少选择 1 个定时任务")

    requested_trade_date = cn_today_str()
    actual_trade_date = _resolve_scheduled_trade_date(requested_trade_date)
    code_to_name = _get_reverse_stock_map()
    jobs: List[Dict[str, Any]] = []
    with_position_context = 0
    available_tasks = {
        task["id"]: task
        for task in scheduled_service.list_scheduled(db, current_user.id)
    }
    valid_item_ids = []
    missing_item_ids = []
    for raw_item_id in body.item_ids:
        item_id = str(raw_item_id or "").strip()
        if not item_id:
            continue
        if item_id in available_tasks:
            valid_item_ids.append(item_id)
        else:
            missing_item_ids.append(item_id)

    if not valid_item_ids:
        raise HTTPException(400, "选中的定时任务已失效，请刷新页面后重试")

    if missing_item_ids:
        _log(
            f"[Scheduled Batch Trigger] user={current_user.id} skipped missing item_ids={missing_item_ids}"
        )

    for item_id in valid_item_ids:
        task = available_tasks[item_id]

        task_snapshot = dict(task)
        task_snapshot["user_id"] = current_user.id
        task_snapshot["manual_user_context"] = _build_manual_imported_user_context(db, current_user.id, task["symbol"])

        scheduled_user_context = task_snapshot["manual_user_context"]
        if scheduled_user_context.get("current_position") is not None:
            with_position_context += 1

        now = _utcnow_iso()
        job_id = uuid4().hex
        _set_job(
            job_id,
            job_id=job_id,
            status="pending",
            created_at=now,
            symbol=task["symbol"],
            trade_date=actual_trade_date,
            user_id=current_user.id,
            request_source="scheduled_manual_batch",
        )
        _emit_job_event(
            job_id,
            "job.queued",
            {"job_id": job_id, "symbol": task["symbol"], "trade_date": actual_trade_date},
        )
        _create_tracked_task(
            _run_manual_trigger(
                task_snapshot,
                requested_trade_date,
                job_id,
            )
        )

        jobs.append({
            "item_id": task["id"],
            "job_id": job_id,
            "symbol": task["symbol"],
            "name": code_to_name.get(task["symbol"], task["symbol"]),
            "status": "pending",
            "created_at": now,
            "current_position": scheduled_user_context.get("current_position"),
            "average_cost": scheduled_user_context.get("average_cost"),
        })

    return {
        "summary": {
            "total": len(jobs),
            "with_position_context": with_position_context,
        },
        "jobs": jobs,
    }


@app.post("/v1/scheduled/{item_id}/trigger", response_model=AnalyzeResponse)
async def trigger_scheduled_analysis_once(
    item_id: str,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    task = scheduled_service.get_scheduled(db, current_user.id, item_id)
    if task is None:
        raise HTTPException(404, "未找到该定时任务")

    requested_trade_date = cn_today_str()
    actual_trade_date = _resolve_scheduled_trade_date(requested_trade_date)
    now = _utcnow_iso()
    job_id = uuid4().hex

    task_snapshot = dict(task)
    task_snapshot["user_id"] = current_user.id
    task_snapshot["manual_user_context"] = _build_manual_imported_user_context(db, current_user.id, task["symbol"])

    _set_job(
        job_id,
        job_id=job_id,
        status="pending",
        created_at=now,
        symbol=task["symbol"],
        trade_date=actual_trade_date,
        user_id=current_user.id,
        request_source="scheduled_manual",
    )
    _emit_job_event(
        job_id,
        "job.queued",
        {"job_id": job_id, "symbol": task["symbol"], "trade_date": actual_trade_date},
    )
    _create_tracked_task(
        _run_manual_trigger(
            task_snapshot,
            requested_trade_date,
            job_id,
        )
    )
    return AnalyzeResponse(job_id=job_id, status="pending", created_at=now)


@app.patch("/v1/scheduled/{item_id}")
def update_scheduled_analysis(
    item_id: str,
    body: dict,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    kwargs = _extract_scheduled_update_kwargs(body)
    try:
        result = scheduled_service.update_scheduled(db, current_user.id, item_id, **kwargs)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:
        raise HTTPException(404, "未找到该定时任务")
    code_to_name = _get_reverse_stock_map()
    result["name"] = code_to_name.get(result["symbol"], result["symbol"])
    _annotate_scheduled_with_imported_context([result], db, current_user.id)
    return result


@app.delete("/v1/scheduled/{item_id}", status_code=204)
def delete_scheduled_analysis(
    item_id: str,
    current_user: UserDB = Depends(_require_api_user),
    db: Session = Depends(get_db),
):
    if not scheduled_service.delete_scheduled(db, current_user.id, item_id):
        raise HTTPException(404, "未找到该定时任务")


# ─── Sponsor endpoints (public, no auth) ────────────────────────────────────


class SponsorItem(BaseModel):
    id: str
    sponsor_type: str
    name: str
    github: Optional[str] = None
    avatar: Optional[str] = None
    email: Optional[str] = None
    provider: Optional[str] = None
    date: str
    # NOTE: amount is intentionally excluded from the public API


class SponsorsResponse(BaseModel):
    money: List[SponsorItem]
    token: List[SponsorItem]


def _sponsor_to_item(s: SponsorDB) -> SponsorItem:
    return SponsorItem(
        id=s.id,
        sponsor_type=s.sponsor_type,
        name=s.name,
        github=s.github,
        avatar=s.avatar,
        email=s.email,
        provider=s.provider,
        date=s.date,
    )


@app.get("/v1/sponsors", response_model=SponsorsResponse)
def list_sponsors(db: Session = Depends(get_db)):
    """Public endpoint: list all visible sponsors grouped by type."""
    all_sponsors = sponsor_service.list_sponsors(db)
    money = [_sponsor_to_item(s) for s in all_sponsors if s.sponsor_type == "money"]
    token = [_sponsor_to_item(s) for s in all_sponsors if s.sponsor_type == "token"]
    return SponsorsResponse(money=money, token=token)


# ─── Feedback endpoints ─────────────────────────────────────────────────────


class FeedbackCreateRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=5000)


class FeedbackItem(BaseModel):
    id: str
    user_email: str
    subject: str
    content: str
    admin_reply: Optional[str] = None
    replied_at: Optional[datetime] = None
    is_read: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_serializer("replied_at", "created_at", "updated_at")
    def serialize_dt(self, v: Optional[datetime], _info: Any) -> Optional[str]:
        return v.isoformat() if v else None


class FeedbackListResponse(BaseModel):
    total: int
    feedbacks: List[FeedbackItem]


class FeedbackUnreadResponse(BaseModel):
    unread_count: int


def _fb_to_item(fb: FeedbackDB) -> FeedbackItem:
    return FeedbackItem(
        id=fb.id,
        user_email=fb.user_email,
        subject=fb.subject,
        content=fb.content,
        admin_reply=fb.admin_reply,
        replied_at=fb.replied_at,
        is_read=fb.is_read,
        created_at=fb.created_at,
        updated_at=fb.updated_at,
    )


@app.post("/v1/feedbacks", response_model=FeedbackItem, status_code=201)
def create_feedback(
    req: FeedbackCreateRequest,
    current_user: UserDB = Depends(_require_web_user),
    db: Session = Depends(get_db),
):
    fb = feedback_service.create_feedback(db, current_user, req.subject, req.content)
    return _fb_to_item(fb)


@app.get("/v1/feedbacks", response_model=FeedbackListResponse)
def list_feedbacks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserDB = Depends(_require_web_user),
    db: Session = Depends(get_db),
):
    items, total = feedback_service.list_feedbacks(db, current_user.id, page, page_size)
    return FeedbackListResponse(total=total, feedbacks=[_fb_to_item(fb) for fb in items])


@app.get("/v1/feedbacks/unread-count", response_model=FeedbackUnreadResponse)
def feedback_unread_count(
    current_user: UserDB = Depends(_require_web_user),
    db: Session = Depends(get_db),
):
    count = feedback_service.unread_count(db, current_user.id)
    return FeedbackUnreadResponse(unread_count=count)


@app.get("/v1/feedbacks/{feedback_id}", response_model=FeedbackItem)
def get_feedback(
    feedback_id: str,
    current_user: UserDB = Depends(_require_web_user),
    db: Session = Depends(get_db),
):
    fb = feedback_service.get_feedback(db, feedback_id)
    if not fb or fb.user_id != current_user.id:
        raise HTTPException(404, "未找到该反馈")
    # auto mark read
    if not fb.is_read and fb.admin_reply:
        feedback_service.mark_read(db, feedback_id, current_user.id)
        fb.is_read = True
    return _fb_to_item(fb)


@app.post("/v1/feedbacks/{feedback_id}/read")
def mark_feedback_read(
    feedback_id: str,
    current_user: UserDB = Depends(_require_web_user),
    db: Session = Depends(get_db),
):
    fb = feedback_service.mark_read(db, feedback_id, current_user.id)
    if not fb:
        raise HTTPException(404, "未找到该反馈")
    return {"ok": True}


from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# [UI-001] tradeflow_api — imports
from api.tradeflow_schemas import (
    TradeFlowDailyPlanResponse,
    TradeFlowCandidatesResponse,
    TradeFlowCandidateDetailResponse,
    TradeFlowObserveResponse,
    TradeFlowTAQueueResponse,
    TradeFlowReviewResponse,
    TradeFlowDataHealthResponse,
    TradeFlowFilteredResponse,  # [UI-007] tradeflow_filtered_trace
    TradeFlowTieredCandidatesResponse,  # [TF-UX-001]
    TradeFlowReviewGenerateResponse,  # [TF-UX-003]
    TradeFlowEvidenceAuditResponse,  # [DATA-007] evidence_coverage_audit
    TradeFlowResearchPlanResponse,  # [UI-009] candidate_ta_plan_draft
    TradeFlowCompareResponse,  # [UI-010] mandate_candidate_compare
    CompanyOverviewResponse,  # [TF-UI-011] candidate_research_entry
    PaperLedgerResponse,  # [TF-PAPER-001] paper_trading_ledger
    PaperActionRequest,  # [TF-PAPER-001] paper_trading_ledger
    PaperAddCandidateRequest,  # [TF-PAPER-001] paper_trading_ledger
    PaperActionResponse,  # [TF-PAPER-001] paper_trading_ledger
    PaperReviewResponse,  # [TF-PAPER-001] paper_trading_ledger
    TopicRegistryResponse,  # [H-012] mandate_topic_registry
    TopicWatchlistResponse,  # [H-012] mandate_topic_registry
    TopicHeatmapResponse,  # [H-013] mandate_topic_heatmap
    MandateDailyReportResponse,  # [H-015] mandate_daily_report
    SourceFreshnessResponse,  # [DATA-018] source_freshness_report
    SourceFreshnessEntryItem,  # [DATA-018] source_freshness_report
    SourceFreshnessSummary,  # [DATA-018] source_freshness_report
    LiveSamplingResponse,  # [DATA-020] live_sampling_health_ui
    ObservationItemResponse,  # [TRACK-001] observation_warehouse
    ObservationItemListResponse,  # [TRACK-001] observation_warehouse
    ObservationItemCreateRequest,  # [TRACK-001] observation_warehouse
    ObservationItemUpdateRequest,  # [TRACK-001] observation_warehouse
    ObservationItemMarkRequest,  # [TRACK-001] observation_warehouse
    ObservationBulkUpsertRequest,  # [TRACK-001] observation_warehouse
    ObservationBulkUpsertResponse,  # [TRACK-001] observation_warehouse
    ObservationActionResponse,  # [TRACK-001] observation_warehouse
    ObservationAddFromCandidateRequest,  # [TRACK-006] add_to_observation
    ObservationAddFromTAReportRequest,  # [TRACK-006] add_to_observation
    ObservationAddResponse,  # [TRACK-006] add_to_observation
    ObservationImportRequest,  # [TRACK-008] observation_bulk_import_export
    ObservationImportResponse,  # [TRACK-008] observation_bulk_import_export
    ObservationExportResponse,  # [TRACK-008] observation_bulk_import_export
)
from api.services.tradeflow_service import (
    get_daily_plan as _tf_get_daily_plan,
    get_candidates as _tf_get_candidates,
    get_candidate_detail as _tf_get_candidate_detail,
    get_observe as _tf_get_observe,
    get_ta_queue as _tf_get_ta_queue,
    get_review as _tf_get_review,
    get_data_health as _tf_get_data_health,
    run_discovery_scan as _tf_run_discovery_scan,
    get_filtered as _tf_get_filtered,  # [UI-007] tradeflow_filtered_trace
    run_observe_check as _tf_run_observe_check,  # [TF-OBS-001] tradeflow_observe_runner
    get_candidates_tiered as _tf_get_candidates_tiered,  # [TF-UX-001]
    generate_review as _tf_generate_review,  # [TF-UX-003]
    get_evidence_audit as _tf_get_evidence_audit,  # [DATA-007] evidence_coverage_audit
    generate_research_plan as _tf_generate_research_plan,  # [UI-009] candidate_ta_plan_draft
    get_candidate_comparison as _tf_get_candidate_comparison,  # [UI-010] mandate_candidate_compare
    get_observe_scheduler_status as _tf_get_observe_scheduler_status,  # [T-004] intraday_observe_scheduler
    get_company_overview as _tf_get_company_overview,  # [TF-UI-011] candidate_research_entry
    get_paper_ledger as _tf_get_paper_ledger,  # [TF-PAPER-001] paper_trading_ledger
    add_paper_candidate as _tf_add_paper_candidate,  # [TF-PAPER-001] paper_trading_ledger
    remove_paper_candidate as _tf_remove_paper_candidate,  # [TF-PAPER-001] paper_trading_ledger
    confirm_paper_action as _tf_confirm_paper_action,  # [TF-PAPER-001] paper_trading_ledger
    update_paper_observe_state as _tf_update_paper_observe_state,  # [TF-PAPER-001] paper_trading_ledger
    get_paper_review as _tf_get_paper_review,  # [TF-PAPER-001] paper_trading_ledger
    get_topic_registry as _tf_get_topic_registry,  # [H-012] mandate_topic_registry
    get_topic_watchlist as _tf_get_topic_watchlist,  # [H-012] mandate_topic_registry
    get_topic_heatmap as _tf_get_topic_heatmap,  # [H-013] mandate_topic_heatmap
    get_mandate_daily_report as _tf_get_mandate_daily_report,  # [H-015] mandate_daily_report
    get_source_freshness as _tf_get_source_freshness,  # [DATA-018] source_freshness_report
    get_live_sampling_report as _tf_get_live_sampling_report,  # [DATA-020] live_sampling_health_ui
    get_observation_items as _tf_get_observation_items,  # [TRACK-001] observation_warehouse
    create_observation_item as _tf_create_observation_item,  # [TRACK-001] observation_warehouse
    update_observation_item as _tf_update_observation_item,  # [TRACK-001] observation_warehouse
    mark_observation_item_status as _tf_mark_observation_item_status,  # [TRACK-001] observation_warehouse
    bulk_upsert_observation_items as _tf_bulk_upsert_observation_items,  # [TRACK-001] observation_warehouse
    add_candidate_to_observation as _tf_add_candidate_to_observation,  # [TRACK-006] add_to_observation
    add_ta_report_to_observation as _tf_add_ta_report_to_observation,  # [TRACK-006] add_to_observation
    import_observation_csv as _tf_import_observation_csv,  # [TRACK-008] observation_bulk_import_export
    export_observation_csv as _tf_export_observation_csv,  # [TRACK-008] observation_bulk_import_export
)

# [UI-001] tradeflow_api — read-only endpoints


class TradeFlowDiscoveryRequest(BaseModel):
    date: str = Field(..., description="交易日期 YYYY-MM-DD")
    symbols: List[str] = Field(default_factory=list, description="手动股票池")
    top_n: int = Field(default=20, ge=1, le=100, description="最多返回候选数")
    include_holdings: bool = Field(default=True, description="纳入持仓")
    include_watchlist: bool = Field(default=True, description="纳入自选股")
    use_event_source: bool = Field(default=False, description="接入公告/评级等事件源")
    news_texts: List[str] = Field(default_factory=list, description="手动事件文本")
    save_candidates: bool = Field(default=True, description="保存候选到 TradeFlow DB")


@app.get("/v1/tradeflow/daily-plan", response_model=TradeFlowDailyPlanResponse)
def tradeflow_daily_plan(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_get_daily_plan(date)


@app.get("/v1/tradeflow/candidates", response_model=TradeFlowCandidatesResponse)
def tradeflow_candidates(
    date: str = Query(..., description="交易日期 YYYY-MM-DD"),
    tier: Optional[str] = Query(None, description="层级过滤 A/B/C"),
    need_deep_ta: Optional[bool] = Query(None, description="是否需要深度TA"),
    candidate_type: Optional[str] = Query(None, description="候选类型过滤 POLICY_AMBUSH/POLICY_CONFIRM/TECH_TRADE/EVENT_WATCH/PSEUDO_POLICY/OVERHEATED_AVOID/UNCLASSIFIED_DATA_GAP"),  # [H-005] mandate_radar_ui [TF-P0-002]
    pool: Optional[str] = Query(None, description="候选池过滤 all/haotian/policy/tech/event/gap"),  # [TF-P0-002] tradeflow_pool_split
):
    return _tf_get_candidates(date, tier=tier, need_deep_ta=need_deep_ta, candidate_type=candidate_type, pool=pool)


# [TF-UX-001] tiered candidates
@app.get("/v1/tradeflow/candidates/tiered", response_model=TradeFlowTieredCandidatesResponse)
def tradeflow_candidates_tiered(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_get_candidates_tiered(date)


# [TF-UI-011] candidate_research_entry
@app.get("/v1/tradeflow/candidates/{symbol}/overview", response_model=CompanyOverviewResponse)
def tradeflow_candidate_overview(
    symbol: str,
    date: str = Query("", description="交易日期 YYYY-MM-DD（可选，用于名称回填）"),
):
    return _tf_get_company_overview(symbol)


# [UI-010] mandate_candidate_compare
@app.get("/v1/tradeflow/candidates/compare", response_model=TradeFlowCompareResponse)
def tradeflow_candidates_compare(
    date: str = Query(..., description="交易日期 YYYY-MM-DD"),
    sort_by: str = Query("mandate_score", description="排序字段: mandate_score/ambush_score/evidence_coverage/counter_evidence_count/evidence_gap_count/topic_lifecycle_state/company_role"),
    sort_order: str = Query("desc", description="排序方向: desc/asc"),
    pool: Optional[str] = Query(None, description="候选池过滤: all/haotian/policy/tech/event/gap"),
):
    return _tf_get_candidate_comparison(date, sort_by=sort_by, sort_order=sort_order, pool=pool)


@app.get("/v1/tradeflow/candidates/{symbol}", response_model=TradeFlowCandidateDetailResponse)
def tradeflow_candidate_detail(
    symbol: str,
    date: str = Query(..., description="交易日期 YYYY-MM-DD"),
):
    return _tf_get_candidate_detail(symbol, date)


@app.get("/v1/tradeflow/observe", response_model=TradeFlowObserveResponse)
def tradeflow_observe(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_get_observe(date)


@app.get("/v1/tradeflow/ta-queue", response_model=TradeFlowTAQueueResponse)
def tradeflow_ta_queue(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_get_ta_queue(date)


@app.get("/v1/tradeflow/review", response_model=TradeFlowReviewResponse)
def tradeflow_review(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_get_review(date)


@app.get("/v1/tradeflow/data-health", response_model=TradeFlowDataHealthResponse)
def tradeflow_data_health():
    return _tf_get_data_health()


# [DATA-007] evidence_coverage_audit
@app.get("/v1/tradeflow/evidence-audit", response_model=TradeFlowEvidenceAuditResponse)
def tradeflow_evidence_audit(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_get_evidence_audit(date)


# [UI-007] tradeflow_filtered_trace
@app.get("/v1/tradeflow/filtered", response_model=TradeFlowFilteredResponse)
def tradeflow_filtered(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_get_filtered(date)


@app.post("/v1/tradeflow/discovery")
def tradeflow_discovery(request: TradeFlowDiscoveryRequest):
    return _tf_run_discovery_scan(
        trade_date=request.date,
        symbols=request.symbols,
        top_n=request.top_n,
        include_holdings=request.include_holdings,
        include_watchlist=request.include_watchlist,
        use_event_source=request.use_event_source,
        news_texts=request.news_texts,
        save_candidates=request.save_candidates,
    )


# [TF-OBS-001] tradeflow_observe_runner
@app.post("/v1/tradeflow/observe/run")
def tradeflow_observe_run(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_run_observe_check(date)


# [T-004] intraday_observe_scheduler
@app.get("/v1/tradeflow/observe/scheduler-status")
def tradeflow_observe_scheduler_status():
    return _tf_get_observe_scheduler_status()


# [TF-UX-003] post_market_review
@app.post("/v1/tradeflow/review/generate", response_model=TradeFlowReviewGenerateResponse)
def tradeflow_review_generate(date: str = Query(..., description="交易日期 YYYY-MM-DD")):
    return _tf_generate_review(date)


# [UI-009] candidate_ta_plan_draft
@app.post("/v1/tradeflow/research-plan", response_model=TradeFlowResearchPlanResponse)
def tradeflow_research_plan(
    symbol: str = Query(..., description="股票代码"),
    date: str = Query(..., description="交易日期 YYYY-MM-DD"),
):
    return _tf_generate_research_plan(symbol, date)


# [TF-PAPER-001] paper_trading_ledger — endpoints
@app.get("/v1/tradeflow/paper-ledger", response_model=PaperLedgerResponse)
def tradeflow_paper_ledger():
    return _tf_get_paper_ledger()


@app.post("/v1/tradeflow/paper-ledger/add", response_model=PaperActionResponse)
def tradeflow_paper_ledger_add(request: PaperAddCandidateRequest):
    return _tf_add_paper_candidate(
        symbol=request.symbol,
        name=request.name,
        trade_date=request.trade_date,
        trigger_price=request.trigger_price,
        invalid_price=request.invalid_price,
        planned_amount=request.planned_amount,
        candidate_type=request.candidate_type,
        plan_date=request.plan_date,
        note=request.note,
        data_quality_score=request.data_quality_score,  # [TF-RISK-001] paper_risk_budget
    )


@app.post("/v1/tradeflow/paper-ledger/remove", response_model=PaperActionResponse)
def tradeflow_paper_ledger_remove(trade_id: int = Query(..., description="模拟跟踪记录 ID")):
    return _tf_remove_paper_candidate(trade_id)


@app.post("/v1/tradeflow/paper-ledger/confirm", response_model=PaperActionResponse)
def tradeflow_paper_ledger_confirm(request: PaperActionRequest):
    return _tf_confirm_paper_action(
        trade_id=request.trade_id,
        action_type=request.action_type,
        price=request.price,
        note=request.note,
    )


@app.get("/v1/tradeflow/paper-ledger/review", response_model=PaperReviewResponse)
def tradeflow_paper_ledger_review(date: str = Query(..., description="复盘日期 YYYY-MM-DD")):
    return _tf_get_paper_review(date)


# [H-012] mandate_topic_registry
@app.get("/v1/tradeflow/topic-registry", response_model=TopicRegistryResponse)
def tradeflow_topic_registry():
    return _tf_get_topic_registry()


# [H-012] mandate_topic_registry
@app.get("/v1/tradeflow/topic-watchlist", response_model=TopicWatchlistResponse)
def tradeflow_topic_watchlist(
    date: str = Query(..., description="交易日期 YYYY-MM-DD"),
    max_symbols: int = Query(5, description="每个主题最多标的数", ge=1, le=20),
):
    return _tf_get_topic_watchlist(date, max_symbols_per_topic=max_symbols)


# [H-013] mandate_topic_heatmap
@app.get("/v1/tradeflow/topic-heatmap", response_model=TopicHeatmapResponse)
def tradeflow_topic_heatmap(
    as_of: str = Query("", description="截止日期 YYYY-MM-DD（默认最新候选日）"),
    window_days: int = Query(60, description="回看窗口天数", ge=7, le=180),
):
    return _tf_get_topic_heatmap(as_of=as_of, window_days=window_days)


# [H-015] mandate_daily_report
@app.get("/v1/tradeflow/mandate-daily-report/latest", response_model=MandateDailyReportResponse)
def tradeflow_mandate_daily_report_latest(
    as_of: str = Query("", description="截止日期 YYYY-MM-DD（为空时优先读取最新日报）"),
    window_days: int = Query(60, description="回看窗口天数", ge=7, le=180),
    save_report: bool = Query(False, description="是否将本次生成结果写入 docs/mandate_daily_reports"),
):
    return _tf_get_mandate_daily_report(
        as_of=as_of,
        window_days=window_days,
        save_report=save_report,
    )


# [DATA-018] source_freshness_report
@app.get("/v1/data-sources/freshness", response_model=SourceFreshnessResponse)
def data_sources_freshness(symbol: str = Query("", description="股票代码（可选）")):
    return _tf_get_source_freshness(symbol=symbol)


# [DATA-020] live_sampling_health_ui
@app.get("/v1/data-sources/live-sampling", response_model=LiveSamplingResponse)
def data_sources_live_sampling():
    return _tf_get_live_sampling_report()


# [TRACK-001] observation_warehouse — read & write endpoints
@app.get("/v1/tradeflow/observation-items", response_model=ObservationItemListResponse)
def tradeflow_observation_items(
    status: Optional[str] = Query(None, description="按状态过滤 watching/near_entry/in_entry_zone/ta_required/entered/invalidated/removed"),
    include_removed: bool = Query(False, description="是否包含已 removed 的条目（默认隐藏）"),
):
    return _tf_get_observation_items(status=status, include_removed=include_removed)


@app.post("/v1/tradeflow/observation-items", response_model=ObservationActionResponse)
def tradeflow_observation_item_create(request: ObservationItemCreateRequest):
    return _tf_create_observation_item(
        symbol=request.symbol,
        name=request.name,
        status=request.status,
        entry_low=request.entry_low,
        entry_high=request.entry_high,
        trigger_price=request.trigger_price,
        invalid_price=request.invalid_price,
        horizon=request.horizon,
        source=request.source,
        reason=request.reason,
        priority=request.priority,
        notes=request.notes,
        strategy_tags=request.strategy_tags,
        score=request.score,
        action_label=request.action_label,
        research_direction=request.research_direction,
        playbook_stage=request.playbook_stage,
        playbook_contract=request.playbook_contract,
    )


@app.patch("/v1/tradeflow/observation-items/{item_id}", response_model=ObservationActionResponse)
def tradeflow_observation_item_update(
    item_id: int,
    request: ObservationItemUpdateRequest,
):
    return _tf_update_observation_item(
        item_id,
        name=request.name,
        status=request.status,
        entry_low=request.entry_low,
        entry_high=request.entry_high,
        trigger_price=request.trigger_price,
        invalid_price=request.invalid_price,
        horizon=request.horizon,
        source=request.source,
        reason=request.reason,
        priority=request.priority,
        notes=request.notes,
        touch_last_reviewed=request.touch_last_reviewed,
        strategy_tags=request.strategy_tags,
        score=request.score,
        action_label=request.action_label,
        research_direction=request.research_direction,
        playbook_stage=request.playbook_stage,
        playbook_contract=request.playbook_contract,
    )


@app.post("/v1/tradeflow/observation-items/{item_id}/mark", response_model=ObservationActionResponse)
def tradeflow_observation_item_mark(
    item_id: int,
    request: ObservationItemMarkRequest,
):
    return _tf_mark_observation_item_status(item_id, request.status, note=request.note)


@app.post("/v1/tradeflow/observation-items/bulk-upsert", response_model=ObservationBulkUpsertResponse)
def tradeflow_observation_items_bulk_upsert(request: ObservationBulkUpsertRequest):
    payload = [item.model_dump() for item in request.items]
    return _tf_bulk_upsert_observation_items(payload)


# [TRACK-008] observation_bulk_import_export — CSV / text bulk import.
# Parses the blob, dedups within it by normalized symbol (merging notes), then
# forwards to the shared bulk_upsert write path so all TRACK-001 / TRACK-006
# contracts (UNIQUE(symbol), notes preservation, price boundary 0.0) hold.
@app.post("/v1/tradeflow/observation-items/import", response_model=ObservationImportResponse)
def tradeflow_observation_items_import(request: ObservationImportRequest):
    return _tf_import_observation_csv(
        request.csv_text,
        force_overwrite_notes=request.force_overwrite_notes,
    )


# [TRACK-008] observation_bulk_import_export — CSV export.
# Returns a CSV string (UTF-8 BOM for Excel) whose headers are the Chinese
# aliases import recognizes, so export→import round-trips. Never triggers
# TA / LLM and never touches tradingagents.db.
@app.get("/v1/tradeflow/observation-items/export", response_model=ObservationExportResponse)
def tradeflow_observation_items_export(include_removed: bool = Query(True, description="是否包含已 removed 条目（导出默认全量）")):
    return _tf_export_observation_csv(include_removed=include_removed)


# [TRACK-006] add_to_observation — one-click add from TradeFlow candidate.
# Resolves the full candidate row from tradeflow_candidates by symbol+date so
# the frontend just forwards the click; never auto-fires (no scheduler path).
@app.post(
    "/v1/tradeflow/candidates/{symbol}/add-to-observation",
    response_model=ObservationAddResponse,
)
def tradeflow_candidate_add_to_observation(
    symbol: str,
    request: ObservationAddFromCandidateRequest,
):
    # Pull the persisted candidate so we don't trust client-supplied scores.
    # If the candidate row is missing (e.g. trade_date mismatch) we still
    # add the item with sane defaults — the click never fails.
    candidate_dict: dict[str, Any] = {"symbol": symbol}
    try:
        detail = _tf_get_candidate_detail(symbol=symbol, trade_date=request.trade_date)
        if detail and detail.get("candidate"):
            candidate_dict = detail["candidate"].model_dump()
            candidate_dict["symbol"] = candidate_dict.get("symbol") or symbol
    except Exception as exc:  # never let candidate lookup break the click
        logger.warning(
            "[TRACK-006] candidate lookup failed for %s@%s: %s",
            symbol, request.trade_date, exc,
        )

    return _tf_add_candidate_to_observation(
        candidate_dict,
        via=request.via or "candidate_drawer",
        force_overwrite_notes=request.force_overwrite_notes,
        extra_notes=request.extra_notes or "",
    )


# [TRACK-006] add_to_observation — one-click add from a TA report.
# Prefers re-reading the stored ReportDB row by id; falls back to the inline
# fields the caller supplied (e.g. live preview before the report is saved).
@app.post(
    "/v1/tradeflow/ta-reports/{symbol}/add-to-observation",
    response_model=ObservationAddResponse,
)
def tradeflow_ta_report_add_to_observation(
    symbol: str,
    request: ObservationAddFromTAReportRequest,
):
    report_payload: dict[str, Any] = {
        "symbol": symbol,
        "name": request.name or "",
        "action_label": request.action_label or "",
        "research_direction": request.research_direction or "",
        "target_price": request.target_price or 0.0,
        "stop_loss_price": request.stop_loss_price or 0.0,
    }

    if request.report_id:
        try:
            with get_db_ctx() as db:
                row = db.query(ReportDB).filter(
                    ReportDB.id == request.report_id,
                    ReportDB.symbol == symbol,
                ).first()
                if row is not None:
                    report_payload.update({
                        "name": report_payload["name"] or "",
                        "action_label": row.action_label or report_payload["action_label"],
                        "research_direction": row.research_direction or row.direction or report_payload["research_direction"],
                        "target_price": row.target_price if row.target_price is not None else report_payload["target_price"],
                        "stop_loss_price": row.stop_loss_price if row.stop_loss_price is not None else report_payload["stop_loss_price"],
                    })
        except Exception as exc:  # never let DB read break the add click
            logger.warning("[TRACK-006] ReportDB lookup failed for %s: %s", request.report_id, exc)

    return _tf_add_ta_report_to_observation(
        report_payload,
        via=request.via or "analysis_page",
        force_overwrite_notes=request.force_overwrite_notes,
        extra_notes=request.extra_notes or "",
    )


# ─── Static Files & SPA Routing ──────────────────────────────────────────────

# Serve uploaded files (avatars etc.) from shared uploads directory
_uploads_dir = Path(os.getenv("UPLOAD_DIR", str(Path(__file__).parent.parent / "uploads")))
if _uploads_dir.is_dir():
    app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")

# Mount frontend if dist exists
dist_path = os.path.join(os.getcwd(), "frontend/dist")
if os.path.exists(dist_path):
    app.mount("/assets", StaticFiles(directory=os.path.join(dist_path, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # 1. Define and resolve the absolute safe root
        base_path = os.path.realpath(dist_path)
        
        # 2. Resolve the requested path (handling .. and symlinks)
        # We lstrip("/") to prevent os.path.join from treating it as an absolute path
        fullpath = os.path.realpath(os.path.join(base_path, full_path.lstrip("/")))
        
        # 3. Security Check: The normalized path must start with the base_path
        if not fullpath.startswith(base_path):
            return FileResponse(os.path.join(base_path, "index.html"))
            
        # 4. Final check: if it's a valid file, serve it
        if os.path.isfile(fullpath):
            return FileResponse(fullpath)
            
        # Otherwise fallback to index.html for SPA routing
        return FileResponse(os.path.join(base_path, "index.html"))


def run() -> None:
    import uvicorn
    from pathlib import Path

    log_config = str(Path(__file__).parent / "logging_config.yaml")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False, log_config=log_config)
