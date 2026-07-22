"""Feishu (飞书) document export service.

# [B-003] feishu_doc_export

Exports analysis reports as Feishu cloud documents using the Feishu Open API
(DocX / Drive).  Supports both internal tenant apps and self-built apps.

Environment variables:
    FEISHU_APP_ID       – Feishu app ID (required)
    FEISHU_APP_SECRET   – Feishu app secret (required)
    FEISHU_FOLDER_TOKEN – target folder token in Feishu Drive (optional;
                          if unset the doc is created in the app root)

Usage::

    from api.services.feishu_export_service import export_report_to_feishu

    result = export_report_to_feishu(report)
    # result = {"success": True, "document_url": "https://...", "document_token": "..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import requests

if TYPE_CHECKING:
    from api.database import ReportDB

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FEISHU_BASE = "https://open.feishu.cn/open-apis"
_TOKEN_TTL = 9000  # seconds – token validity is 2h, refresh early
_VERDICT_RE = re.compile(r"<!--\s*VERDICT:\s*(\{[^>]+\})\s*-->")

_DIRECTION_ALIAS = {
    "BULLISH": "看多",
    "LEAN_BULLISH": "偏多",
    "BEARISH": "看空",
    "LEAN_BEARISH": "偏空",
    "NEUTRAL": "中性",
    "CAUTIOUS": "谨慎",
}

_AGENT_SECTIONS = [
    ("market_report", "市场分析"),
    ("sentiment_report", "舆情分析"),
    ("news_report", "新闻分析"),
    ("fundamentals_report", "基本面分析"),
    ("macro_report", "宏观分析"),
    ("smart_money_report", "主力资金分析"),
    ("volume_price_report", "量价分析"),
    ("game_theory_report", "博弈论分析"),
]

_RISK_LEVEL_LABEL = {"high": "高", "medium": "中", "low": "低"}

# ---------------------------------------------------------------------------
# Token cache (module-level singleton)
# ---------------------------------------------------------------------------

_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0.0}


def _get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def is_feishu_configured() -> bool:
    """Return True if the minimum Feishu env vars are set."""
    return bool(_get_env("FEISHU_APP_ID") and _get_env("FEISHU_APP_SECRET"))


def _fetch_tenant_access_token() -> str:
    """Obtain (and cache) a tenant_access_token."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    url = f"{_FEISHU_BASE}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": _get_env("FEISHU_APP_ID"),
        "app_secret": _get_env("FEISHU_APP_SECRET"),
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {data.get('msg', data)}")

    token = data["tenant_access_token"]
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + _TOKEN_TTL
    return token


def _feishu_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_fetch_tenant_access_token()}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Verdict extraction (reused from email_report_service pattern)
# ---------------------------------------------------------------------------

def _extract_verdict(text: str) -> Optional[Dict[str, str]]:
    m = _VERDICT_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(1))
        direction = parsed.get("direction", "")
        reason = parsed.get("reason", "")
        if not direction or not reason:
            return None
        direction = _DIRECTION_ALIAS.get(direction.upper(), direction)
        return {"direction": direction, "reason": reason.strip()[:80]}
    except (json.JSONDecodeError, AttributeError):
        return None


def _semantic_field(report: "ReportDB", key: str) -> Optional[str]:
    value = getattr(report, key, None)
    if value:
        return str(value)
    rd = getattr(report, "result_data", None)
    if isinstance(rd, dict):
        value = rd.get(key)
        if value:
            return str(value)
    return None


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_report_markdown(report: "ReportDB") -> str:
    """Render a *ReportDB* instance as a Feishu-friendly Markdown document.

    The Markdown is later uploaded via the DocX raw content API so it must
    use Feishu-compatible syntax (standard GFM mostly works).
    """
    symbol = report.symbol or ""
    trade_date = report.trade_date or ""
    direction = _semantic_field(report, "research_direction") or report.direction or ""
    action = _semantic_field(report, "action_label") or report.decision or ""
    execution_action = _semantic_field(report, "execution_action") or ""
    confidence = report.confidence
    target_price = report.target_price
    stop_loss = report.stop_loss_price

    parts: list[str] = []

    # ── Title ──
    parts.append(f"# TradingAgents 投研报告 — {symbol} ({trade_date})\n")

    # ── Summary table ──
    parts.append("| 项目 | 详情 |")
    parts.append("| --- | --- |")
    parts.append(f"| **标的** | {symbol} |")
    parts.append(f"| **日期** | {trade_date} |")
    if direction:
        parts.append(f"| **方向** | {direction} |")
    if action:
        parts.append(f"| **动作** | {action} |")
    if execution_action:
        parts.append(f"| **执行动作** | {execution_action} |")
    if confidence is not None:
        parts.append(f"| **置信度** | {confidence}% |")
    if target_price is not None:
        parts.append(f"| **目标价** | ¥{target_price:.2f} |")
    if stop_loss is not None:
        parts.append(f"| **止损价** | ¥{stop_loss:.2f} |")
    parts.append("")

    # ── Agent verdicts ──
    verdicts: list[tuple[str, Dict[str, str]]] = []
    for attr, title in _AGENT_SECTIONS:
        content = getattr(report, attr, None)
        if content is None:
            continue
        v = _extract_verdict(content)
        if v:
            verdicts.append((title, v))

    if verdicts:
        parts.append("## 各方观点\n")
        for title, v in verdicts:
            parts.append(f"- **{title}**：{v['direction']} — {v['reason']}")
        parts.append("")

    # ── Key metrics ──
    key_metrics: Optional[List[dict]] = getattr(report, "key_metrics", None)
    if key_metrics:
        parts.append("## 关键指标\n")
        parts.append("| 指标 | 数值 | 状态 |")
        parts.append("| --- | --- | --- |")
        status_label = {"good": "良好", "neutral": "中性", "bad": "不佳"}
        for item in key_metrics:
            name = item.get("name", "")
            value = item.get("value", "")
            status = status_label.get(item.get("status", ""), item.get("status", ""))
            parts.append(f"| {name} | {value} | {status} |")
        parts.append("")

    # ── Risk items ──
    risk_items: Optional[List[dict]] = getattr(report, "risk_items", None)
    if risk_items:
        parts.append("## 风险提示\n")
        for item in risk_items:
            name = item.get("name", "")
            level = _RISK_LEVEL_LABEL.get(item.get("level", ""), item.get("level", ""))
            desc = item.get("description", "")
            parts.append(f"- **[{level}]** {name}：{desc}")
        parts.append("")

    # ── Final trade decision ──
    ftd = getattr(report, "final_trade_decision", None)
    if ftd:
        parts.append("## 最终交易决策\n")
        parts.append(ftd)
        parts.append("")

    # ── Investment plan ──
    ip = getattr(report, "investment_plan", None)
    if ip:
        parts.append("## 投资计划\n")
        parts.append(ip)
        parts.append("")

    # ── Trader investment plan ──
    tip = getattr(report, "trader_investment_plan", None)
    if tip:
        parts.append("## 交易员计划\n")
        parts.append(tip)
        parts.append("")

    # ── Local knowledge block (KB-003) ──
    rd = getattr(report, "result_data", None)
    if isinstance(rd, dict):
        lk = rd.get("local_knowledge_block")
        if lk:
            parts.append("## 本地知识补充\n")
            parts.append(str(lk))
            parts.append("")

        # ── Half-year facts block (HY-004) ──
        hy = rd.get("half_year_facts_block")
        if hy:
            parts.append("## 半年报事实对照\n")
            parts.append(str(hy))
            parts.append("")

    # ── Footer ──
    parts.append("---")
    parts.append("*本报告由 TradingAgents 多智能体系统自动生成，仅供参考，不构成投资建议。*")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Feishu DocX API helpers
# ---------------------------------------------------------------------------

def _create_document(title: str, folder_token: Optional[str] = None) -> Dict[str, str]:
    """Create an empty Feishu document and return {document_id, url}."""
    url = f"{_FEISHU_BASE}/docx/v1/documents"
    body: Dict[str, Any] = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token

    resp = requests.post(url, headers=_feishu_headers(), json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu create document failed: {data.get('msg', data)}")

    doc = data["data"]["document"]
    return {
        "document_id": doc["document_id"],
        "url": f"https://feishu.cn/docx/{doc['document_id']}",
    }


def _get_document_blocks(document_id: str) -> List[Dict[str, Any]]:
    """Retrieve the block list of a document (page block + children)."""
    url = f"{_FEISHU_BASE}/docx/v1/documents/{document_id}/blocks"
    resp = requests.get(url, headers=_feishu_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu get blocks failed: {data.get('msg', data)}")
    return data["data"]["items"]


def _batch_create_blocks(
    document_id: str,
    parent_block_id: str,
    children: List[Dict[str, Any]],
    index: int = -1,
) -> None:
    """Append block children under *parent_block_id*."""
    url = f"{_FEISHU_BASE}/docx/v1/documents/{document_id}/blocks/{parent_block_id}/children"
    body: Dict[str, Any] = {"children": children}
    if index >= 0:
        body["index"] = index
    resp = requests.post(url, headers=_feishu_headers(), json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu batch create blocks failed: {data.get('msg', data)}")


# ---------------------------------------------------------------------------
# Markdown → Feishu block conversion
# ---------------------------------------------------------------------------

def _text_element(content: str, bold: bool = False) -> Dict[str, Any]:
    """Build a single text element for a block."""
    style: Dict[str, Any] = {}
    if bold:
        style["bold"] = True
    elem: Dict[str, Any] = {
        "text_run": {
            "content": content,
        }
    }
    if style:
        elem["text_run"]["text_element_style"] = style
    return elem


def _heading_block(level: int, text: str) -> Dict[str, Any]:
    """Build a heading block (level 2-9 → Feishu heading2-heading9)."""
    block_type = level + 1  # Feishu: 3=heading1, 4=heading2, ...
    return {
        "block_type": block_type,
        "heading": {  # generic key; Feishu uses heading2..heading9 per level
            "elements": [_text_element(text)],
        },
    }


def _paragraph_block(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "block_type": 2,
        "text": {"elements": elements},
    }


def _bullet_block(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "block_type": 12,
        "bullet": {"elements": elements},
    }


def _ordered_block(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "block_type": 13,
        "ordered": {"elements": elements},
    }


def _divider_block() -> Dict[str, Any]:
    return {"block_type": 22, "divider": {}}


def _parse_inline(text: str) -> List[Dict[str, Any]]:
    """Parse inline markdown bold (**text**) into text elements."""
    elements: List[Dict[str, Any]] = []
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            elements.append(_text_element(part[2:-2], bold=True))
        else:
            elements.append(_text_element(part))
    return elements


def markdown_to_blocks(md: str) -> List[Dict[str, Any]]:
    """Convert a subset of Markdown to Feishu document blocks.

    Supports: headings (#), bold (**), bullet lists (- / *), ordered lists (1.),
    horizontal rules (---), and plain paragraphs.
    """
    blocks: List[Dict[str, Any]] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            blocks.append(_divider_block())
            i += 1
            continue

        # Headings
        heading_match = re.match(r"^(#{1,9})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            if level == 1:
                # H1 → heading1 (block_type 3)
                blocks.append({
                    "block_type": 3,
                    "heading1": {"elements": [_text_element(text)]},
                })
            elif level == 2:
                blocks.append({
                    "block_type": 4,
                    "heading2": {"elements": [_text_element(text)]},
                })
            elif level == 3:
                blocks.append({
                    "block_type": 5,
                    "heading3": {"elements": [_text_element(text)]},
                })
            else:
                # heading4+ uses block_type = level + 1
                key = f"heading{level}"
                blocks.append({
                    "block_type": level + 1,
                    key: {"elements": [_text_element(text)]},
                })
            i += 1
            continue

        # Bullet list
        if re.match(r"^[-*]\s+", stripped):
            text = re.sub(r"^[-*]\s+", "", stripped)
            blocks.append(_bullet_block(_parse_inline(text)))
            i += 1
            continue

        # Ordered list
        ol_match = re.match(r"^\d+\.\s+", stripped)
        if ol_match:
            text = re.sub(r"^\d+\.\s+", "", stripped)
            blocks.append(_ordered_block(_parse_inline(text)))
            i += 1
            continue

        # Table: collect contiguous table lines and convert to a code block
        # (Feishu block API tables are complex; render as a readable code block)
        if "|" in stripped and stripped.startswith("|"):
            table_lines: list[str] = []
            while i < len(lines) and "|" in lines[i].strip():
                table_lines.append(lines[i])
                i += 1
            # Render as a paragraph with each row on its own line, escaping pipes
            for tl in table_lines:
                cells = [c.strip() for c in tl.strip().strip("|").split("|")]
                # Skip separator rows
                if all(set(c) <= {"-", ":", " "} for c in cells):
                    continue
                blocks.append(_paragraph_block([_text_element(" | ".join(cells))]))
            continue

        # Default: paragraph
        blocks.append(_paragraph_block(_parse_inline(stripped)))
        i += 1

    return blocks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_report_to_feishu(report: "ReportDB") -> Dict[str, Any]:
    """Export a report to a Feishu cloud document.

    Returns::

        {
            "success": True,
            "document_url": "https://feishu.cn/docx/...",
            "document_token": "...",
            "title": "...",
        }

    On failure::

        {
            "success": False,
            "error": "...",
        }
    """
    if not is_feishu_configured():
        return {"success": False, "error": "飞书应用凭证未配置（FEISHU_APP_ID / FEISHU_APP_SECRET）"}

    symbol = report.symbol or "未知"
    trade_date = report.trade_date or ""
    direction = _semantic_field(report, "research_direction") or report.direction or ""
    title = f"TradingAgents {symbol} {trade_date} {direction}".strip()

    try:
        # 1. Create empty document
        folder_token = _get_env("FEISHU_FOLDER_TOKEN") or None
        doc_info = _create_document(title, folder_token)
        document_id = doc_info["document_id"]

        # 2. Render markdown
        md = render_report_markdown(report)

        # 3. Convert to blocks and insert
        blocks = markdown_to_blocks(md)
        if blocks:
            # The page block is the first block returned by get_blocks
            page_blocks = _get_document_blocks(document_id)
            page_block_id = page_blocks[0]["block_id"] if page_blocks else document_id
            # Feishu API has a limit of ~50 children per batch
            for chunk_start in range(0, len(blocks), 50):
                chunk = blocks[chunk_start : chunk_start + 50]
                _batch_create_blocks(document_id, page_block_id, chunk)

        return {
            "success": True,
            "document_url": doc_info["url"],
            "document_token": document_id,
            "title": title,
        }
    except requests.RequestException as exc:
        logger.error("Feishu API request failed: %s", exc)
        return {"success": False, "error": f"飞书 API 请求失败: {exc}"}
    except RuntimeError as exc:
        logger.error("Feishu API error: %s", exc)
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error during Feishu export")
        return {"success": False, "error": f"导出失败: {exc}"}


async def export_report_to_feishu_async(report: "ReportDB") -> Dict[str, Any]:
    """Async wrapper for :func:`export_report_to_feishu`."""
    return await asyncio.to_thread(export_report_to_feishu)
