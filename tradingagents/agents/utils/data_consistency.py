from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ConsistencyIssue:
    metric: str
    first_direction: str
    second_direction: str
    first_snippet: str
    second_snippet: str


_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "扣非净利润": ("扣非净利润", "归母扣非净利润", "扣除非经常性损益净利润"),
    "经营现金流": ("经营现金流", "经营活动现金流", "经营活动产生的现金流量净额", "经营现金流净额"),
}

_SPLIT_RE = re.compile(r"[\n。；;]+")
_PCT_RE = re.compile(r"(?:同比|较上年同期|较去年同期).{0,12}(增长|上升|增加|下降|下滑|减少|降低).{0,16}?[-+]?[\d.]+\s*%")
_DIRECTION_RE = re.compile(r"(增长|上升|增加|下降|下滑|减少|降低)")
_NEGATIVE_WORDS = ("下降", "下滑", "减少", "降低")


def _direction_label(raw: str) -> str:
    return "下降" if raw in _NEGATIVE_WORDS else "增长"


def _clip(text: str, limit: int = 96) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def find_financial_direction_conflicts(text: str | None) -> list[ConsistencyIssue]:
    """Find same-metric YoY direction contradictions in generated Chinese reports."""
    if not text:
        return []

    observations: dict[str, list[tuple[str, str]]] = {}
    for segment in _SPLIT_RE.split(text):
        if "同比" not in segment and "较上年同期" not in segment and "较去年同期" not in segment:
            continue
        for metric, aliases in _METRIC_ALIASES.items():
            if not any(alias in segment for alias in aliases):
                continue
            direction_match = _PCT_RE.search(segment) or _DIRECTION_RE.search(segment)
            if not direction_match:
                continue
            observations.setdefault(metric, []).append((_direction_label(direction_match.group(1)), _clip(segment)))

    issues: list[ConsistencyIssue] = []
    for metric, rows in observations.items():
        if len(rows) < 2:
            continue
        first_direction, first_snippet = rows[0]
        for direction, snippet in rows[1:]:
            if direction != first_direction:
                issues.append(
                    ConsistencyIssue(
                        metric=metric,
                        first_direction=first_direction,
                        second_direction=direction,
                        first_snippet=first_snippet,
                        second_snippet=snippet,
                    )
                )
                break
    return issues


def append_financial_consistency_warnings(text: str) -> str:
    issues = find_financial_direction_conflicts(text)
    if not issues:
        return text

    lines = [
        "",
        "### 数据一致性警告",
        "以下同一核心财务指标在报告内出现同比方向冲突，结论使用前必须回查原始财报数据：",
    ]
    for issue in issues:
        lines.append(
            f"- {issue.metric}：先写为{issue.first_direction}，后写为{issue.second_direction}。"
            f"片段1：{issue.first_snippet}；片段2：{issue.second_snippet}"
        )
    return text.rstrip() + "\n" + "\n".join(lines)
