"""IntentParser: parse natural language query into structured trading intent."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agents.utils.context_utils import normalize_user_context
from tradingagents.prompts import get_prompt
from tradingagents.dataflows.config import get_config

_HORIZON_LABELS = {
    "short": "短线（1-2周，技术面主导）",
    "medium": "中线（1-3月，基本面主导）",
}

# Analysis intent constants
ANALYSIS_INTENT_VALUES = ("watch", "entry", "holding", "add", "reduce", "stop_loss")

# Keyword → (analysis_intent, horizon) mapping for rule-based routing
_INTENT_KEYWORD_MAP = [
    # (keywords_regex, intent, horizon)
    # Explicit English user actions. Keep these subject/command anchored so
    # analyst prose such as "the report says sell" is not treated as the
    # user's execution intent.
    (
        r"\b(?:should|can|could|may)\s+i\s+(?:add(?:\s+to)?|buy\s+more)\b|"
        r"\bi\s+(?:want|plan|intend|need|would\s+like)\s+to\s+"
        r"(?:add(?:\s+to)?|buy\s+more)\b",
        "add",
        "short",
    ),
    (
        r"\b(?:should|can|could|may)\s+i\s+"
        r"(?:exit|sell\s+all|close\s+(?:my|the)\s+position|stop\s+out)\b|"
        r"\bi\s+(?:want|plan|intend|need|would\s+like)\s+to\s+"
        r"(?:exit|sell\s+all|close\s+(?:my|the)\s+position|stop\s+out)\b|"
        r"^\s*(?:please\s+)?(?:exit|sell\s+all)\b",
        "stop_loss",
        "short",
    ),
    (
        r"\b(?:should|can|could|may)\s+i\s+(?:sell|reduce|trim)\b|"
        r"\bi\s+(?:want|plan|intend|need|would\s+like)\s+to\s+"
        r"(?:sell|reduce|trim)\b|"
        r"^\s*(?:please\s+)?(?:sell|reduce|trim)\b",
        "reduce",
        "short",
    ),
    (
        r"\b(?:should|can|could|may)\s+i\s+"
        r"(?:buy|enter|re[\s-]?enter|open\s+(?:a|the)\s+position(?:\s+in)?)\b|"
        r"\bi\s+(?:want|plan|intend|need|would\s+like)\s+to\s+"
        r"(?:buy|enter|re[\s-]?enter|open\s+(?:a|the)\s+position(?:\s+in)?)\b|"
        r"^\s*(?:please\s+)?(?:buy|enter|re[\s-]?enter)\b",
        "entry",
        "short",
    ),
    (
        r"\bshould\s+i\s+(?:hold|keep\s+holding|continue\s+holding)\b|"
        r"\bi\s+(?:want|plan|intend|would\s+like)\s+to\s+"
        r"(?:hold|keep\s+holding|continue\s+holding)\b|"
        r"\b(?:keep|continue)\s+holding\b",
        "holding",
        "short",
    ),
    # 短线专用规则（优先匹配，防止被通用规则抢先）
    (r"短线.*?(?:买点|入场|建仓)|想.*?短线.*?(?:买|入)|短线机会", "entry", "short"),
    # 中线专用规则（优先匹配）
    (r"中线.*?(?:买点|入场|建仓)|想.*?中线.*?(?:买|入|建仓)", "entry", "medium"),
    (r"中线.*?(?:拿|持有|持仓)|中线.*?(?:卖不卖|走不走)", "holding", "medium"),
    (r"中线.*?(?:机会|走势|行情|空间)", "watch", "medium"),
    # 通用规则（无周期前缀时默认 short）
    (r"加仓|补仓|追加|买入更多", "add", "short"),
    (
        r"买点|买入|入场|建仓|进场|能不能买|能否买|能买吗|可以买|"
        r"(?:想|准备|打算|计划|考虑)买|(?<!主)(?<!需)要买(?!方|盘)|"
        r"(?:要不要|该不该|是否应该|值不值得)买|值得买吗|"
        r"(?:什么时(?:候|间)|何时)(?:适合)?买|买多少(?:合适|股|仓位)?",
        "entry",
        "short",
    ),
    (r"减仓|部分.*?(?:卖出|离场)|降低仓位", "reduce", "short"),
    (r"止损|割肉|认赔", "stop_loss", "short"),
    (r"清仓|全部.*?(?:卖出|离场)", "stop_loss", "short"),
    (r"继续.*?拿|继续.*?持有|拿着不动|套.*?(?:怎么办|怎么)|被套|持仓.*?(?:怎么办|如何)", "holding", "short"),
    (r"短线.*?(?:机会|走势|行情)", "watch", "short"),
    (r"先观察|先观望|继续观察|先看看|观望", "watch", "short"),
]


_ACTION_CLAUSE_BOUNDARIES = ("。", "，", ",", "；", ";", "！", "!", "？", "?", "\n")

_EXPLICIT_NO_POSITION_HOLDING_PATTERN = (
    r"(?:(?:我|本人)\s*)?(?:(?:当前|现在|目前|已经|已)\s*)?"
    r"(?:"
    r"(?:未|不(?:再)?)持有(?:该股|这只股票|该股票|本股|任何股票|股票)"
    r"|(?:未|不(?:再)?)持有\s*"
    r"(?!(?:观点|看法|意见|态度|立场|预期|判断|信心|偏见)"
    r"(?:[，,。；;！？!?\n]|$))"
    r"(?:\d{6}(?:\.(?:SH|SZ|SS|BJ))?|"
    r"[A-Za-z*ＳＴｓｔ\u4e00-\u9fff][A-Za-z0-9*ＳＴｓｔ\u4e00-\u9fff]{1,23})"
    r"(?=\s*(?:了\s*)?(?:(?:但(?:是)?|不过|然而|可是|却)\s*|"
    r"[，,。；;！？!?\n]|$))"
    r"|(?:未|不(?:再)?)持有(?=\s*(?:了\s*)?"
    r"(?:(?:但(?:是)?|不过|然而|可是|却)\s*|[，,。；;！？!?\n]|$))"
    r")"
)


def _is_future_flat_position_goal(text: str, start: int, end: int) -> bool:
    """Return whether a flat-position phrase describes a future target state."""
    prefix = text[:start]
    clause_start = max(prefix.rfind(mark) for mark in _ACTION_CLAUSE_BOUNDARIES) + 1
    clause_prefix = prefix[clause_start:]
    suffix = text[end:end + 10]

    # An explicit present-tense marker nearest the assertion wins over an
    # earlier future action: "明天清仓，但现在持仓为0" is currently flat.
    if re.search(r"(?:当前|现在|目前)\s*$", clause_prefix):
        return False
    return bool(
        re.search(
            r"(?:为了|目标(?:是|为)?|希望|计划|打算|准备|明天|之后|后续|"
            r"收盘后|今晚)"
            r"(?:(?!当前|现在|目前)[^，,。；;！？!?\n]){0,40}$",
            clause_prefix,
        )
        or re.match(r"\s*(?:过夜|到|至|进入|留到)", suffix)
    )


def _is_position_alternative_question(text: str, start: int, end: int) -> bool:
    """Return whether a position phrase is one option in a user question."""
    return bool(
        re.match(
            r"\s*(?:还是|或是|或者|亦或|/|／|\bor\b)",
            text[end:end + 24],
            re.IGNORECASE,
        )
    )


def _is_third_party_action_reference(text: str, start: int) -> bool:
    """Return whether an action belongs to a market actor rather than the user."""
    prefix = text[:start]
    clause_start = max(prefix.rfind(mark) for mark in _ACTION_CLAUSE_BOUNDARIES) + 1
    clause_prefix = text[clause_start:start]
    contrasts = list(re.finditer(r"(?:但(?:是)?|不过|然而|可是|却)\s*", clause_prefix))
    if contrasts:
        clause_prefix = clause_prefix[contrasts[-1].end():]

    actors = list(
        re.finditer(
            r"(?:北向资金|主力资金|主力|基金经理|券商分析师|机构分析师|"
            r"首席分析师|机构|游资|外资|基金|券商|股东|大股东|"
            r"控股股东|实际控制人|实控人|高管|董事长|董监高|董事|监事|"
            r"管理层|员工持股计划|公司|市场|分析师|专家|研究员|研报)",
            clause_prefix,
        )
    )
    if not actors:
        return False
    actor = actors[-1]
    actor_tail = clause_prefix[actor.end():]
    if re.search(r"(?:我|本人)", actor_tail):
        return False

    # The actor must grammatically govern the action.  Merely mentioning the
    # analyzed company earlier in the clause is insufficient: in "这家公司能
    # 买吗" the company is the object of the user's question, and in "公司回购
    # 后可以买入吗" the intervening event breaks the actor/action relation.
    action_lead = text[start:start + 12]
    if actor.group() in {"公司", "基金"} and re.match(
        r"(?:能不能买|能否买|能买吗|可以买|可买|是否可以买)",
        action_lead,
    ):
        return False
    if actor_tail and not re.fullmatch(
        r"\s*(?:(?:数据|报告|公告|资料|统计)?"
            r"(?:显示|表明|称|指出|披露|认为|建议|推荐|给出|给了|维持)|"
            r"(?:是否|有无|有没有|是否有)|"
            r"(?:今日|今天|昨日|昨天|近期|当前|现在|目前|已经|已|刚刚|刚|正|正在|持续|"
            r"大幅|小幅|净|拟|计划)|"
            r"(?:以[^，,。；;！？!?\n]{1,20}(?:方式)?)|"
            r"(?:通过[^，,。；;！？!?\n]{1,20})|"
            r"(?:在[^，,。；;！？!?\n]{1,20}(?:时|期间))|"
            r"回购)*\s*",
        actor_tail,
    ):
        return False
    return True


def _is_action_match_negated(text: str, start: int, end: int) -> bool:
    """Return whether an action mention is explicitly rejected by its clause."""
    prefix = text[:start]
    clause_start = max(prefix.rfind(mark) for mark in _ACTION_CLAUSE_BOUNDARIES) + 1
    clause_prefix = text[clause_start:start]

    # A contrast starts a new semantic clause even when punctuation is omitted.
    contrasts = list(re.finditer(r"(?:但(?:是)?|不过|然而|可是|却)\s*", clause_prefix))
    if contrasts:
        clause_prefix = clause_prefix[contrasts[-1].end():]

    # "要不要止损" / "有没有买点" ask whether to act; their embedded
    # negative characters are not vetoes.
    if re.search(
        r"(?:要不要|该不该|有没有)\s*"
        r"(?:(?:现在|当前|目前|继续|再|重新|立即|马上|进一步|适度|"
        r"部分|分批|轻仓|小仓位)\s*)*$",
        clause_prefix,
    ):
        return False

    # Affirmative double negatives express an action request rather than a
    # veto: "不得不减仓", "不能不加仓", "并非不能买入".
    if re.search(
        r"(?:不得不|不能不|不可不|(?:并)?(?:不是|并非)\s*"
        r"(?:不能|不可|不应|不建议|不推荐|不允许|不宜))\s*$",
        clause_prefix,
    ):
        return False

    if re.search(
        r"(?:无需|暂不|不要|不再|不应|不需(?:要)?|不考虑|不建议|"
        r"尚未|还未|还没(?:有)?|没(?:有)?|未|"
        r"不允许|不宜|不可|不能|不支持|不适合|避免|禁止|勿|别|并非|不是|非|不)\s*"
        r"(?:(?:说|表示|代表|意味着|说明|考虑|建议|计划|选择|打算|准备|尝试|在|于|继续|"
        r"买入|建仓|入场|加仓|减仓|卖出|清仓|止盈|止损|"
        r"和|或|及|以及|、|"
        r"要|想|"
        r"立即|立刻|马上|贸然|盲目|轻易|随意|急于|过早|现在|当前|目前|"
        r"这里|此处|这个位置|当前位置)\s*)*"
        r"(?:当前|现在|目前|这里|此处|这个位置|当前位置)?\s*$",
        clause_prefix,
    ):
        return True

    clause_end = min(
        (pos for mark in _ACTION_CLAUSE_BOUNDARIES if (pos := text.find(mark, end)) >= 0),
        default=len(text),
    )
    clause_suffix = text[end:clause_end]
    return bool(
        re.match(
            r"\s*(?:并)?不(?:"
            r"是\s*(?:当前|现在|目前)?\s*(?:的)?\s*(?:选择|计划|目标|意图)|"
            r"合适|适合|可行|需要|应当|应该|建议|允许|考虑|推荐)",
            clause_suffix,
        )
    )


def _is_completed_purchase_reference(text: str, start: int, end: int) -> bool:
    """Return whether a buy mention describes an already completed user action."""
    if _is_third_party_action_reference(text, start):
        return False

    prefix = text[:start]
    clause_start = max(prefix.rfind(mark) for mark in _ACTION_CLAUSE_BOUNDARIES) + 1
    clause_prefix = text[clause_start:start]
    contrasts = list(re.finditer(r"(?:但(?:是)?|不过|然而|可是|却)\s*", clause_prefix))
    if contrasts:
        clause_prefix = clause_prefix[contrasts[-1].end():]
    if not re.search(
        r"(?:(?:我|本人)\s*)?"
        r"(?:已经|已|刚刚|刚|昨天|昨日|前天|上周(?:[一二三四五六日天])?|"
        r"上个月|去年|(?:\d+|[一二两三四五六七八九十]+)天前|"
        r"今天(?:已经|已|又|重新))\s*"
        r"(?:(?:我|本人)\s*)?$",
        clause_prefix,
    ):
        return False

    suffix = text[end:]
    clause_end = min(
        (pos for mark in _ACTION_CLAUSE_BOUNDARIES if (pos := text.find(mark, end)) >= 0),
        default=len(text),
    )
    action_result = text[end:clause_end]
    if re.search(
        r"(?:失败|未成交|没有成交|没成交|未执行|没有执行|没执行|"
        r"未成功|没有成功|没成功|已撤(?:单)?|撤单|取消)",
        action_result,
    ):
        return False
    if re.match(
        r"\s*(?:计划|信号|建议|条件|机会|意图|动作|理由|资格|能力|方案|盘点)",
        suffix,
    ):
        return False
    if re.match(r"\s*了?\s*(?:吗|么|[?？])", suffix):
        return False
    return True


def _has_completed_purchase_assertion(text: str) -> bool:
    """Detect completed purchases without treating a ticker as share count."""
    for match in re.finditer(r"买入|购入|买", text or ""):
        if _is_completed_purchase_reference(text, match.start(), match.end()):
            return True
    return False


def _is_completed_sale_reference(text: str, start: int, end: int) -> bool:
    """Return whether a sell mention is a dated/completed user action."""
    if _is_third_party_action_reference(text, start):
        return False

    prefix = text[:start]
    clause_start = max(prefix.rfind(mark) for mark in _ACTION_CLAUSE_BOUNDARIES) + 1
    clause_prefix = text[clause_start:start]
    contrasts = list(re.finditer(r"(?:但(?:是)?|不过|然而|可是|却)\s*", clause_prefix))
    if contrasts:
        clause_prefix = clause_prefix[contrasts[-1].end():]
    clause_end = min(
        (
            pos
            for mark in _ACTION_CLAUSE_BOUNDARIES
            if (pos := text.find(mark, end)) >= 0
        ),
        default=len(text),
    )
    action_result = text[end:clause_end]
    if re.search(
        r"(?:失败|未成交|没有成交|没成交|未执行|没有执行|没执行|"
        r"未成功|没有成功|没成功|已撤(?:单)?|撤单|取消)",
        action_result,
    ):
        return False
    return bool(
        re.search(
            r"(?:(?:我|本人)\s*)?"
            r"(?:已经|已|刚刚|刚|昨天|昨日|前天|上周(?:[一二三四五六日天])?|"
            r"上个月|去年|(?:\d+|[一二两三四五六七八九十]+)天前)\s*"
            r"(?:(?:我|本人)\s*)?"
            r"(?:全部(?:持仓)?|部分(?:持仓)?)?\s*$",
            clause_prefix,
        )
        and not re.match(r"\s*了?\s*(?:吗|么|[?？])", text[end:])
    )


def _is_failed_sale_reference(text: str, start: int, end: int) -> bool:
    """Return whether a dated sale attempt explicitly failed or was cancelled."""
    prefix = text[:start]
    clause_start = max(prefix.rfind(mark) for mark in _ACTION_CLAUSE_BOUNDARIES) + 1
    clause_prefix = text[clause_start:start]
    dated_attempt = bool(
        re.search(
            r"(?:(?:我|本人)\s*)?"
            r"(?:已经|已|刚刚|刚|昨天|昨日|前天|上周(?:[一二三四五六日天])?|"
            r"上个月|去年|(?:\d+|[一二两三四五六七八九十]+)天前)\s*"
            r"(?:(?:我|本人)\s*)?"
            r"(?:全部(?:持仓)?|部分(?:持仓)?)?\s*$",
            clause_prefix,
        )
    )
    if not dated_attempt:
        return False
    clause_end = min(
        (
            pos
            for mark in _ACTION_CLAUSE_BOUNDARIES
            if (pos := text.find(mark, end)) >= 0
        ),
        default=len(text),
    )
    return bool(
        re.search(
            r"(?:失败|未成交|没有成交|没成交|未执行|没有执行|没执行|"
            r"未成功|没有成功|没成功|已撤(?:单)?|撤单|取消)",
            text[end:clause_end],
        )
    )


def _has_completed_full_exit_assertion(text: str) -> bool:
    """Detect a user statement that the whole position was already exited."""
    query = str(text or "")
    completion_marker = (
        r"(?:已经|已|刚刚|刚|昨天|昨日|前天|上周(?:[一二三四五六日天])?|"
        r"上个月|去年|(?:\d+|[一二两三四五六七八九十]+)天前)"
    )
    full_exit = (
        r"(?:清仓|全部(?:持仓)?(?:卖出|离场)|"
        r"卖(?:出|掉)全部持仓|全部持仓卖出)"
    )
    patterns = (
        rf"(?:(?:我|本人)\s*)?{completion_marker}\s*"
        rf"(?:(?:我|本人)\s*)?{full_exit}",
        rf"(?:(?:我|本人)\s*)?全部持仓\s*{completion_marker}\s*卖出",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, query):
            action = re.search(r"清仓|卖出|卖掉|离场", match.group())
            if action and _is_third_party_action_reference(
                query, match.start() + action.start()
            ):
                continue
            clause_end = min(
                (
                    pos
                    for mark in _ACTION_CLAUSE_BOUNDARIES
                    if (pos := query.find(mark, match.end())) >= 0
                ),
                default=len(query),
            )
            action_result = query[match.end():clause_end]
            if re.search(
                r"(?:失败|未成交|没有成交|没成交|未执行|没有执行|没执行|"
                r"未成功|没有成功|没成功|已撤(?:单)?|撤单|取消)",
                action_result,
            ):
                continue

            later_text = query[match.end():]
            has_later_holding = bool(
                re.search(
                    r"(?:当前|现在|目前|今天|后来|之后|随后|又|重新)"
                    r".{0,16}(?:我|本人)?\s*"
                    r"(?:仍|还|又|重新|已经|已)?\s*"
                    r"(?:持有|持仓)"
                    r"(?:\s*\d+(?:\.\d+)?\s*(?:股|手))?",
                    later_text,
                )
            )
            if has_later_holding or _has_completed_purchase_assertion(later_text):
                continue
            return True

    english_patterns = (
        r"\bi\s+(?:have\s+)?(?:already\s+)?(?:sold|exited|closed)\s+"
        r"(?:out\s+of\s+)?(?:all\s+(?:(?:of\s+)?my\s+)?"
        r"(?:shares|holdings?)|(?:my\s+)?(?:entire|full)\s+"
        r"(?:position|holding)|all\s+[A-Z][A-Z0-9.\-]{0,15})"
        r"(?:\s+(?:yesterday|today|last\s+(?:week|month)|"
        r"\d+\s+days?\s+ago))?",
        r"(?:\bi\s+(?:currently\s+)?(?:do\s+not|don't|no\s+longer)|"
        r"\band\s+(?:i\s+)?no\s+longer)\s+"
        r"(?:hold|own)(?=\s*(?:it|this\s+(?:stock|position)|"
        r"the\s+(?:stock|position))?\s*(?:[,.!?]|$|and\b))",
    )
    for pattern in english_patterns:
        for match in re.finditer(pattern, query, re.IGNORECASE):
            clause_end = min(
                (
                    pos
                    for mark in _ACTION_CLAUSE_BOUNDARIES
                    if (pos := query.find(mark, match.end())) >= 0
                ),
                default=len(query),
            )
            action_result = query[match.end():clause_end]
            if re.search(
                r"\b(?:failed|did\s+not\s+(?:execute|fill|complete)|"
                r"was\s+not\s+filled|cancelled|canceled)\b",
                action_result,
                re.IGNORECASE,
            ):
                continue
            later_text = query[match.end():]
            has_later_holding = bool(
                re.search(
                    r"\b(?:i\s+)?(?:still|currently|now)\s+"
                    r"(?:hold|own|have)\b|"
                    r"\b(?:i\s+)?(?:then|later|subsequently|afterwards)?\s*"
                    r"(?:bought|purchased|re-entered|rebought)\b",
                    later_text,
                    re.IGNORECASE,
                )
            )
            if has_later_holding:
                continue
            return True
    return False


def _infer_analysis_intent(query: str) -> tuple[str, str]:
    """Rule-based inference of (analysis_intent, horizon) from query text.

    Returns (intent, horizon) where intent is one of ANALYSIS_INTENT_VALUES
    and horizon is 'short' or 'medium'.
    """
    text = (query or "").strip()
    completed_full_exit = _has_completed_full_exit_assertion(text)
    for pattern, intent, horizon in _INTENT_KEYWORD_MAP:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _is_third_party_action_reference(text, match.start()):
                continue
            if re.search(r"买入|购入|买", match.group()) and _is_completed_purchase_reference(
                text, match.start(), match.end()
            ):
                continue
            if re.search(r"卖出|卖掉|减仓|清仓|离场", match.group()):
                if _is_completed_sale_reference(
                    text, match.start(), match.end()
                ) or _is_failed_sale_reference(text, match.start(), match.end()):
                    continue
            if completed_full_exit and intent in {"reduce", "stop_loss"}:
                continue
            if _is_action_match_negated(text, match.start(), match.end()):
                continue
            return intent, horizon
    return "watch", "short"


def _infer_position_context(query: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
    """Infer position_context from query text and user_context.

    Returns a dict with keys: has_position, avg_cost, shares, position_pct, holding_days.
    """
    ctx: Dict[str, Any] = {
        "has_position": False,
        "position_status_explicit": False,
        "avg_cost": None,
        "shares": None,
        "position_pct": None,
        "holding_days": None,
    }

    # ── [P0-1] Negation keyword priority ──
    # Negation keywords force has_position=False even if holding keywords appear.
    text = (query or "").strip()
    completed_full_exit = _has_completed_full_exit_assertion(text)
    no_position_pattern = re.compile(
        r"(?:未|无|没有)(?:任何)?(?:实际)?持仓"
        r"(?!成本|均价|记录|信息|数据|明细|天数|比例|市值|价值|风险|必要|意义|需求)|"
        r"(?:还没(?:有)?|还未|尚未|没(?:有)?|未)"
        r"(?:买入|买(?!入))"
        r"(?!\s*(?:更多|额外|计划|信号|建议|条件|机会|必要|需求|打算|意图|动作|理由|资格|能力))|"
        rf"{_EXPLICIT_NO_POSITION_HOLDING_PATTERN}|"
        r"(?:(?:我|本人)\s*)?(?:(?:当前|现在|目前|已经|已)\s*)?"
        r"(?<!不)(?<!非)(?:是|为|=)\s*空仓|"
        r"(?:(?:我|本人)\s*)?(?:当前|现在|目前|已经|已)\s*空仓|"
        r"(?:我|本人)\s*空仓|"
        r"(?:^|[，,。；;\n])\s*空仓(?=\s*(?:状态|[，,。；;\n]|$))|"
        r"(?:当前)?(?:持仓|仓位)\s*(?:为|是|=)\s*(?:0(?![\d.])|零)"
    )
    has_negation = False
    third_party_position_spans: list[tuple[int, int]] = []
    for match in no_position_pattern.finditer(text):
        if re.search(
            r"(?:未|不(?:再)?)持有.{0,24}(?:观点|看法|意见|态度|立场|预期|判断|信心|偏见)",
            match.group(),
        ):
            continue
        if _is_position_alternative_question(text, match.start(), match.end()):
            continue
        if _is_future_flat_position_goal(text, match.start(), match.end()):
            continue
        prefix = text[max(0, match.start() - 20):match.start()]
        if (
            not re.search(r"(?:我|本人)", match.group())
            and re.search(
                r"(?:北向资金|主力资金|机构|游资|外资|基金|券商|"
                r"股东|大股东|控股股东|公司|市场)"
                r"(?:(?:数据|报告|公告|资料|统计)?"
                r"(?:显示|表明|称|指出|披露|认为))?"
                r"(?:当前|现在|目前|已经|已)?\s*$",
                prefix,
            )
        ):
            third_party_position_spans.append(match.span())
            continue
        has_negation = True
        break
    has_negation = has_negation or text == "空仓"

    # From explicit user_context fields
    pos = user_context.get("current_position")
    pos_pct = user_context.get("current_position_pct")
    avg_cost = user_context.get("average_cost")

    # [P0-1] explicit_no_position: 否定词或 current_position=0
    explicit_no_position = has_negation or completed_full_exit

    if pos is not None:
        ctx["position_status_explicit"] = True
        pos_value = float(pos)
        if pos_value > 0 and not explicit_no_position:
            ctx["has_position"] = True
            ctx["shares"] = pos_value
        elif pos_value == 0:
            # [P0-1] current_position=0 也算 explicit_no_position
            explicit_no_position = True
            ctx["has_position"] = False
    if pos_pct is not None:
        ctx["position_status_explicit"] = True
        ctx["position_pct"] = float(pos_pct)
        if float(pos_pct) > 0 and not explicit_no_position:
            ctx["has_position"] = True
    if avg_cost is not None:
        ctx["avg_cost"] = float(avg_cost)

    # [P0-1] 否定词优先：如果有否定词或 explicit_no_position，强制清空持仓数据
    if explicit_no_position:
        ctx["has_position"] = False
        ctx["position_status_explicit"] = True
        ctx["shares"] = None
        ctx["avg_cost"] = None
        ctx["position_pct"] = None
        return ctx

    if pos is None and pos_pct is None and _has_completed_purchase_assertion(text):
        ctx["has_position"] = True
        ctx["position_status_explicit"] = True

    # 只有在没有否定词且没有 explicit user_context 时，才用关键词推断。
    # “持仓价值/持仓风险”和“如果有持仓”只是研究问题或假设，
    # 不是用户对账户状态的陈述。
    if not ctx["has_position"] and not has_negation and pos is None and pos_pct is None:
        holding_keywords = ["持有", "持仓", "拿着", "被套", "套牢", "仓位", "减仓", "止损", "加仓", "补仓"]
        explicit_user_position_reference = bool(
            re.search(
                r"(?:我|本人)\s*(?:(?:(?:当前|现在|目前)\s*)?的\s*|"
                r"(?:当前|现在|目前)\s*)?"
                r"(?:实际)?(?:持仓|仓位)",
                text,
            )
        )
        non_assertive_position_reference = (
            not explicit_user_position_reference
            and bool(
            re.search(
                r"持仓\s*(?:价值|风险|意义|必要|需求|能力)|"
                r"(?:最大|最高|最低|建议|目标|计划|理想|初始|试探性|"
                r"单票|个股|风险预算|预算)(?:的)?\s*(?:持仓|仓位)|"
                r"(?:持仓|仓位)\s*(?:上限|下限|限制|控制|建议|目标|计划|配置|预算)|"
                r"止损(?:位|价|线|红线|点|阈值|区间|条件)"
                r".{0,8}(?:设|设置|设定|定为|多少|怎么|如何|在哪|是什么)|"
                r"(?:如果|假如|假设|若)\s*(?:我|本人)?\s*(?:有|已有|已经有)?\s*(?:持仓|仓位)|"
                r"(?:我|本人)?\s*(?:有|是否有|有没有)\s*(?:持仓|仓位)\s*(?:吗|么|\?|？)|"
                r"(?:空仓|无持仓|未持仓).{0,8}(?:还是|或是|或者|亦或|/|／|\bor\b)"
                r".{0,12}(?:持有|持仓|减仓|止损)",
                text,
                re.IGNORECASE,
            )
            or (
                _infer_analysis_intent(text)[0] == "entry"
                and re.search(
                    r"止损(?:位|价|线|红线|点|阈值|区间|条件)?\s*"
                    r"(?:(?:设|设置|设定|定为|放在|在|为|到)\s*)?"
                    r"\d+(?:\.\d+)?\s*(?:元|块)?",
                    text,
                )
            )
            )
        )
        has_user_holding_keyword = any(
            not any(start <= match.start() < end for start, end in third_party_position_spans)
            and not _is_third_party_action_reference(text, match.start())
            for keyword in holding_keywords
            for match in re.finditer(re.escape(keyword), text)
        )
        if not non_assertive_position_reference and has_user_holding_keyword:
            ctx["has_position"] = True
            ctx["position_status_explicit"] = True

    return ctx


def parse_intent(
    query: str,
    llm,
    fallback_ticker: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse natural language query into structured intent dict.

    Returns dict with keys: ticker, horizons, focus_areas, specific_questions, user_context, raw_query.
    Falls back gracefully to defaults if LLM output is unparseable.
    """
    config = get_config()
    system_msg = get_prompt("intent_parser_system", config=config)
    fallback_user_context = _extract_user_context_fallback(query)

    try:
        result = llm.invoke([
            SystemMessage(content=system_msg),
            HumanMessage(content=query),
        ])
        raw = result.content.strip()
        # Clean markdown code fences more robustly (handle potential whitespace/newlines)
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        
        # Simple cleanup for common LLM JSON errors
        raw = re.sub(r",\s*([\]}])", r"\1", raw)
        
        parsed = json.loads(raw) or {}
        parsed_user_context = normalize_user_context(parsed.get("user_context") or {})
        merged_context = _merge_inferred_user_context(parsed_user_context, fallback_user_context)

        # G-001: Infer analysis_intent and horizon from query + user_context
        analysis_intent, inferred_horizon = _infer_analysis_intent(query)
        position_context = _infer_position_context(query, merged_context)

        # Determine horizons based on analysis_intent + position_context
        # If user has position, default to holding; otherwise watch/entry
        if position_context["has_position"] and analysis_intent == "watch":
            analysis_intent = "holding"

        horizons = [inferred_horizon]

        return {
            "raw_query": query,
            "ticker": parsed.get("ticker") or fallback_ticker or "",
            "horizons": horizons,
            "analysis_intent": analysis_intent,
            "position_context": position_context,
            "focus_areas": parsed.get("focus_areas") if isinstance(parsed.get("focus_areas"), list) else [],
            "specific_questions": parsed.get("specific_questions") if isinstance(parsed.get("specific_questions"), list) else [],
            "user_context": merged_context,
        }
    except Exception:
        analysis_intent, inferred_horizon = _infer_analysis_intent(query)
        position_context = _infer_position_context(query, fallback_user_context)
        if position_context["has_position"] and analysis_intent == "watch":
            analysis_intent = "holding"
        return {
            "raw_query": query,
            "ticker": fallback_ticker or "",
            "horizons": [inferred_horizon],
            "analysis_intent": analysis_intent,
            "position_context": position_context,
            "focus_areas": [],
            "specific_questions": [],
            "user_context": fallback_user_context,
        }


def build_horizon_context(
    horizon: str,
    focus_areas: List[str],
    specific_questions: List[str],
    agent_type: Optional[str] = None,
    analysis_intent: Optional[str] = None,
    position_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the horizon context block to prepend to any agent's system prompt."""
    config = get_config()
    template = get_prompt("horizon_context_block", config=config)

    horizon_label = _HORIZON_LABELS.get(horizon, horizon)
    focus_str = "、".join(focus_areas) if focus_areas else "无特殊关注"
    questions_str = "；".join(specific_questions) if specific_questions else "无"
    weight_hint = _build_weight_hint(horizon, agent_type)

    # G-001: Append three-layer decision context
    intent_block = ""
    if analysis_intent:
        intent_labels = {
            "watch": "观察",
            "entry": "寻找入场机会",
            "holding": "持仓处理",
            "add": "评估加仓条件",
            "reduce": "评估减仓条件",
            "stop_loss": "止损/清仓评估",
        }
        intent_label = intent_labels.get(analysis_intent, analysis_intent)
        intent_block = f"\n- 交易目的：{intent_label}"

    position_block = ""
    if position_context:
        pos = position_context
        has_pos = pos.get("has_position", False)
        pos_status = "已持仓" if has_pos else "未持仓"
        parts = [pos_status]
        if has_pos:
            if pos.get("avg_cost") is not None:
                parts.append(f"成本 {pos['avg_cost']}")
            if pos.get("shares") is not None:
                parts.append(f"持仓 {pos['shares']} 股")
            if pos.get("position_pct") is not None:
                parts.append(f"仓位 {pos['position_pct']}%")
        position_block = "\n- 持仓状态：" + " | ".join(parts)

    return template.format(
        horizon_label=horizon_label,
        focus_areas_str=focus_str,
        specific_questions_str=questions_str,
        weight_hint=weight_hint,
    ) + intent_block + position_block


def _build_weight_hint(horizon: str, agent_type: Optional[str]) -> str:
    """Return a concise weighting hint for single-run mixed-horizon analysis."""
    if not agent_type:
        return ""

    normalized_agent = agent_type.strip().lower()
    if horizon == "short" and normalized_agent in {"fundamentals", "macro"}:
        return "短线决策中该维度作为次要约束使用，重点识别硬风险、估值压力和中期失效条件，不要覆盖量价、资金与市场结构信号。"
    if horizon == "short" and normalized_agent in {"market", "smart_money", "volume_price", "social", "news"}:
        return "短线决策中该维度权重较高，请优先关注1-2周内可验证的触发条件、失效条件和风险收益比。"
    if horizon == "medium" and normalized_agent in {"fundamentals", "macro"}:
        return "中线决策中该维度权重较高，请优先关注盈利质量、估值消化、行业周期和政策约束。"
    return ""


def _merge_inferred_user_context(
    parsed_context: Dict[str, Any],
    fallback_context: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(parsed_context)
    for key, value in fallback_context.items():
        if key in {"cash_available", "current_position", "current_position_pct", "average_cost", "max_loss_pct"}:
            merged[key] = value
            continue
        if key == "constraints":
            existing = [str(item).strip() for item in merged.get("constraints", []) if str(item).strip()]
            for item in value:
                text = str(item).strip()
                if text and text not in existing:
                    existing.append(text)
            if existing:
                merged["constraints"] = existing
            continue
        if key not in merged or merged.get(key) in (None, "", []):
            merged[key] = value
    return normalize_user_context(merged)


def _extract_user_context_fallback(query: str) -> Dict[str, Any]:
    text = (query or "").strip()
    if not text:
        return {}

    context: Dict[str, Any] = {}

    objective_patterns = [
        (r"(想|准备|打算|计划).*建仓|想建仓|准备建仓|打算建仓", "建仓"),
        (r"(想|准备|打算|计划|考虑).*加仓|想加仓|准备加仓|考虑加仓", "加仓"),
        (r"(想|准备|打算|计划|考虑).*减仓|想减仓|准备减仓|考虑减仓", "减仓"),
        (r"(想|准备|打算|计划|考虑).*止损|想止损|准备止损|考虑止损", "止损"),
        (r"继续拿着|继续持有|拿着不动|持有中|被套|套牢", "持有处理"),
        (r"先观察|先观望|继续观察|先看看|观望", "观察"),
    ]
    for pattern, label in objective_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            context["objective"] = label
            break

    risk_keywords = {
        "保守": "保守",
        "稳健": "保守",
        "平衡": "平衡",
        "激进": "激进",
        "高风险": "激进",
    }
    for keyword, label in risk_keywords.items():
        if keyword in text:
            context["risk_profile"] = label
            break

    horizon_keywords = {
        "短线": "短线",
        "短期": "短线",
        "波段": "波段",
        "中线": "中线",
        "中期": "中线",
        "长期": "长期",
    }
    for keyword, label in horizon_keywords.items():
        if keyword in text:
            context["investment_horizon"] = label
            break

    position_keywords = {
        "满仓": 100.0,
        "重仓": 80.0,
        "半仓": 50.0,
        "轻仓": 20.0,
        "空仓": 0.0,
    }
    for keyword, pct in position_keywords.items():
        if keyword in text:
            context["current_position_pct"] = pct
            break

    cash_match = re.search(r"(?:可用资金|现金|仓位资金)[^\d]{0,8}(\d+(?:\.\d+)?)(万|亿)?", text, re.IGNORECASE)
    if cash_match:
        amount = cash_match.group(1)
        unit = cash_match.group(2) or ""
        context["cash_available"] = f"{amount}{unit}"

    patterns = {
        "average_cost": r"(?:成本价?|均价|持仓成本|买入价|在高位)\D{0,6}(\d+(?:\.\d+)?)",
        "max_loss_pct": r"(?:最大(?:亏损|回撤)|容忍亏损|止损(?:位)?|最多(?:只能)?亏)[^\d]{0,8}(\d+(?:\.\d+)?)\s*%",
        "current_position": r"(?:持有|现有|目前有)[^\d]{0,8}(\d+(?:\.\d+)?)\s*股",
        "current_position_pct": r"(?:仓位|持仓占比)[^\d]{0,8}(\d+(?:\.\d+)?)\s*%",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            context[key] = match.group(1)

    constraints: List[str] = []
    constraint_keywords = {
        "不加杠杆": "不加杠杆",
        "不融资": "不融资",
        "不追高": "不追高",
        "只做t+1": "只做T+1",
        "只做T+1": "只做T+1",
        "不能补仓": "不能补仓",
        "不接受隔夜": "不接受隔夜",
    }
    lowered = text.lower()
    for keyword, label in constraint_keywords.items():
        if keyword.lower() in lowered and label not in constraints:
            constraints.append(label)
    if constraints:
        context["constraints"] = constraints

    return normalize_user_context(context)
