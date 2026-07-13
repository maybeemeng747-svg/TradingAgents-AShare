from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse

import requests

if TYPE_CHECKING:
    from api.database import ReportDB

logger = logging.getLogger(__name__)
_BARK_OFFICIAL_HOST = "api.day.app"
_BARK_DEFAULT_GROUP = "TradingAgents"


def _clip_text(text: str | None, limit: int = 720) -> str:
    if not text:
        return ""
    compact = " ".join(str(text).split()).strip()
    return compact[:limit]


def _format_value(value: object) -> str | None:
    """Format a numeric value, returning None for missing/placeholder values."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        if not text or text in ("-", "0", "0.0", "0.00"):
            return None
        return text
    if number == 0:
        return None
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _plain_report_text(report: "ReportDB") -> str:
    parts = [
        getattr(report, "final_trade_decision", None),
        getattr(report, "trader_investment_plan", None),
        getattr(report, "investment_plan", None),
    ]
    text = "\n".join(str(part) for part in parts if part)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"[#*_`>\[\]]+", " ", text)
    return text


def _extract_phrases(text: str, keyword: str, limit: int = 86) -> list[str]:
    phrases: list[str] = []
    for segment in re.split(r"[\n。；;]+", text):
        compact = " ".join(segment.split()).strip(" -*\t")
        if keyword in compact:
            compact = compact[compact.find(keyword):]
            phrases.append(compact[:limit])
    return phrases


def _extract_phrase(text: str, keyword: str, limit: int = 86) -> str | None:
    phrases = _extract_phrases(text, keyword, limit)
    return phrases[0] if phrases else None


def _extract_risk_review(text: str) -> str | None:
    patterns = (
        r"审核结论[：:\s*]*([A-Za-z]+|买入|卖出|持有|观望|减仓|清仓)",
        r"风控结论[：:\s*]*([A-Za-z]+|买入|卖出|持有|观望|减仓|清仓)",
        r"最终交易建议[：:\s*]*(买入|卖出|观望|持有|减仓|清仓|BUY|SELL|HOLD)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _remove_leading_label(text: str, labels: tuple[str, ...]) -> str:
    cleaned = text
    for label in labels:
        cleaned = cleaned.replace(label, "")
    return cleaned.lstrip(" ：:-")


def _is_meaningful_guidance(text: str) -> bool:
    """Keep concise real guidance while rejecting empty label fragments."""
    compact = " ".join(str(text or "").split()).strip(" ：:;-，。,.()（）")
    if compact in {"", "-", "若存在", "如有", "暂无", "无"}:
        return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", compact))


def _extract_trigger_phrase(text: str) -> str | None:
    """Extract the condition itself instead of a sentence containing it."""
    explicit_candidates: list[tuple[int, str]] = []
    technical_candidates: list[tuple[int, str]] = []

    for match in re.finditer(
        r"触发(?:条件|价格|价|点位|点)?(?:[：:]|为|是)\s*([^，,。；;\n]{1,86})",
        text,
    ):
        candidate = match.group(1).strip()
        if _is_meaningful_guidance(candidate) and candidate not in {"待定", "等待确认"}:
            explicit_candidates.append((match.start(), candidate))

    technical_pattern = re.compile(
        r"(?:^|[\s，,。；;：:\n])"
        r"((?:(?:若|如|待|等待|确认|股价|收盘价|价格|未|只有|仅当|建议(?:在)?)\s*)?"
        r"(?:站稳|站上|突破|跌破|守住|回升|上穿|下穿)"
        r"[^，,。；;\n]{1,60})"
    )
    for match in technical_pattern.finditer(text):
        candidate = match.group(1).strip()
        if _is_meaningful_guidance(candidate):
            technical_candidates.append((match.start(1), candidate))

    if explicit_candidates:
        return min(explicit_candidates, key=lambda item: item[0])[1]
    if technical_candidates:
        return min(technical_candidates, key=lambda item: item[0])[1]
    return None


def _semantic_field(report: "ReportDB", key: str) -> str | None:
    value = getattr(report, key, None)
    if value:
        return str(value)
    result_data = getattr(report, "result_data", None)
    if isinstance(result_data, dict):
        value = result_data.get(key)
        if value:
            return str(value)
    return None


def _display_action(report: "ReportDB") -> str:
    """[DECISION-004] Prefer user-facing action_label over legacy decision."""
    return _semantic_field(report, "action_label") or getattr(report, "decision", None) or "-"


def _display_direction(report: "ReportDB") -> str:
    return _semantic_field(report, "research_direction") or getattr(report, "direction", None) or "-"


def _build_action_lines(report: "ReportDB", text: str) -> list[str]:
    """Build concise bullet points for the Bark notification.

    Rules:
    - Only include lines with real, specific content from the report.
    - Skip generic fallback phrases ("观望，等待确认信号" etc.).
    - Skip truncated/meaningless fragments.
    - Max 4 lines.
    """
    lines: list[str] = []
    decision = str(getattr(report, "decision", "") or "").upper()

    # Risk review: only if it differs from the main decision and is substantive
    risk_review = _extract_risk_review(text)
    if risk_review and risk_review.upper() not in {"", decision}:
        lines.append(f"风控：{risk_review}")

    # Holding guidance: only from actual report text, no generic fallbacks
    holding = _extract_phrase(text, "已持仓者") or _extract_phrase(text, "已持仓")
    if holding:
        cleaned = _remove_leading_label(holding, ("已持仓者（若存在）", "已持仓者", "已持仓"))
        if _is_meaningful_guidance(cleaned):
            lines.append(f"持仓：{cleaned}")

    # Not-holding guidance: only from actual report text
    not_holding = _extract_phrase(text, "未持仓者") or _extract_phrase(text, "未持仓")
    if not_holding:
        cleaned = _remove_leading_label(not_holding, ("未持仓者", "未持仓"))
        if _is_meaningful_guidance(cleaned):
            lines.append(f"未持仓：{cleaned}")

    # Trigger/price: keep concise complete conditions such as "站稳年线".
    trigger = _extract_trigger_phrase(text)
    if trigger:
        lines.append(f"触发：{trigger}")

    # Event risk: only if it adds new info not already in other lines
    event_risk = _extract_phrase(text, "事件风险") or _extract_phrase(text, "风险")
    if event_risk and len(event_risk) >= 8:
        # Deduplicate against existing lines
        if not any(event_risk in line for line in lines):
            lines.append(f"风险：{event_risk}")

    return lines[:4]


def normalize_bark_url(value: str) -> str:
    """Normalize a Bark device key or endpoint URL to a HTTPS endpoint."""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Bark 地址不能为空")

    if not normalized.startswith(("http://", "https://")):
        if not all(char.isalnum() or char in "-_" for char in normalized):
            raise ValueError("Bark 设备 Key 格式不正确")
        return f"https://{_BARK_OFFICIAL_HOST}/{normalized}"

    parsed = urlparse(normalized)
    if parsed.scheme != "https":
        raise ValueError("Bark 地址必须使用 HTTPS")
    if not parsed.netloc:
        raise ValueError("Bark 地址格式不正确")
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ValueError("Bark 地址缺少设备 Key")
    if parsed.params or parsed.fragment:
        raise ValueError("Bark 地址格式不正确")
    key = path_parts[0]
    if not all(char.isalnum() or char in "-_" for char in key):
        raise ValueError("Bark 设备 Key 格式不正确")
    return f"https://{parsed.netloc}/{key}"


def build_report_payload(report: "ReportDB") -> dict:
    decision = _display_action(report)
    direction = _display_direction(report)
    execution_action = _semantic_field(report, "execution_action")
    confidence = getattr(report, "confidence", None)

    # Title: symbol + direction + confidence; skip redundant action if same as direction
    title_parts = [str(report.symbol)]
    if direction and direction != "-":
        title_parts.append(direction)
    if decision and decision != "-" and decision != direction:
        title_parts.append(decision)
    if confidence is not None:
        title_parts.append(f"{confidence}%")
    title = " ".join(title_parts)

    lines = [f"TradingAgents 定时分析 | {report.trade_date}"]

    # Conclusion line: only include fields that have real values
    conclusion_parts: list[str] = []
    if decision and decision != "-":
        conclusion_parts.append(f"结论：{decision}")
    if direction and direction != "-":
        conclusion_parts.append(f"方向：{direction}")
    if execution_action and execution_action != "-":
        conclusion_parts.append(f"动作：{execution_action}")
    if confidence is not None:
        conclusion_parts.append(f"置信度：{confidence}%")
    if conclusion_parts:
        lines.append("，".join(conclusion_parts))

    # Price line: only show when both values are real numbers
    target = _format_value(getattr(report, "target_price", None))
    stop_loss = _format_value(getattr(report, "stop_loss_price", None))
    if target and stop_loss:
        lines.append(f"价位：目标 {target} / 止损 {stop_loss}")
    elif target:
        lines.append(f"目标价：{target}")
    elif stop_loss:
        lines.append(f"止损价：{stop_loss}")

    # Action lines: only include non-generic, non-empty content
    text = _plain_report_text(report)
    action_lines = _build_action_lines(report, text)
    if action_lines:
        lines.append("要点：")
        lines.extend(f"- {line}" for line in action_lines)

    return {
        "title": title[:128],
        "body": "\n".join(lines)[:900],
        "group": _BARK_DEFAULT_GROUP,
    }


def build_test_payload(content: str | None = None) -> dict:
    body = _clip_text(content, 500) or "这是一条 TradingAgents Bark 推送测试消息。"
    return {
        "title": "TradingAgents Bark 测试",
        "body": body,
        "group": _BARK_DEFAULT_GROUP,
    }


def send_message(payload: dict, bark_url: str) -> bool:
    url = normalize_bark_url(bark_url)
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    try:
        body = response.json()
    except Exception:
        logger.warning("[bark] non-JSON response body=%s", _clip_text(getattr(response, "text", None), 240))
        return False

    code = body.get("code")
    if code is None:
        return True
    try:
        return int(code) == 200
    except (TypeError, ValueError):
        return False


async def send_report_message_with_retry(report: "ReportDB", bark_url: str) -> bool:
    payload = build_report_payload(report)
    try:
        ok = await asyncio.to_thread(send_message, payload, bark_url)
        if ok:
            logger.info("[bark] sent OK for %s", report.symbol)
            return True
    except Exception as exc:
        logger.warning("[bark] first send failed for %s: %s", report.symbol, exc)

    await asyncio.sleep(15)
    try:
        ok = await asyncio.to_thread(send_message, payload, bark_url)
        if ok:
            logger.info("[bark] retry sent OK for %s", report.symbol)
            return True
    except Exception as exc:
        logger.error("[bark] retry failed for %s: %s", report.symbol, exc)
    return False


def mask_bark_url(bark_url: str | None) -> str | None:
    normalized = str(bark_url or "").strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.scheme:
        key = normalized
        return f"{key[:4]}{'*' * max(6, len(key) - 8)}{key[-4:]}" if len(key) > 8 else "******"
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        return f"{parsed.scheme}://{parsed.netloc}/******"
    key = path_parts[0]
    masked_key = f"{key[:4]}{'*' * max(6, len(key) - 8)}{key[-4:]}" if len(key) > 8 else "******"
    suffix = "/".join(quote(part) for part in path_parts[1:])
    suffix_part = f"/{suffix}" if suffix else ""
    return f"{parsed.scheme}://{parsed.netloc}/{masked_key}{suffix_part}"
