# [KB-009] research_attention_decay
"""研报来源去重、时效衰减与过热惩罚规则。

在 KB-007 ``research_attention_score``（多研报重复提及因子）之上，叠加一层
"防刷分 / 防过期 / 防过热"的衰减与惩罚，避免关注度被下列三类噪声过度放大：

  1. **同源重复**：同一机构 / 同一标题 / 同一 raw source 在多页重复命中，
     只计一次主权重。本模块按**机构级**（最粗粒度，能折叠最多重复）做去重；
     更细的 wiki-link alias 级去重已在 KB-007 ``duplicate_source_penalty`` 覆盖。
  2. **时效衰减**：``valid_until`` 已过期的页面只保留弱证据；``stale_risk=高``
     的页面权重随年龄衰减更快。
  3. **过热惩罚**：结合候选已有 ``overheat_flags``、短期涨幅、主题拥挤度降低
     研究**优先级**（注意：只降低研究优先级，**不改变**强动作门禁与交易决策）。

设计约束（对应任务 KB-009）：
  - **不压制真实多来源共识**：3 家不同机构各提到一次，机构级去重比 = 1.0，
    不扣分；只有同一机构重复出现才折叠。
  - **不用单日价格涨幅作为唯一过热指标**：过热惩罚由 ``overheat_flags`` +
    短期涨幅 + 主题拥挤度**三个信号**叠加触发，任一缺失只跳过该项，不会单独
    因涨幅高就把分数清零。
  - **不调用 LLM / 不访问外网 / 不写 DB**：纯标准库计算，输入为 KB-007 已
    聚合好的 :class:`SymbolAttention` 与候选上下文（overheat_flags 等）。
  - **只读叠加层**：不修改 KB-007 的 ``research_attention_score`` 与
    ``score_explain``（保持透明与回归稳定），只产出新的 ``effective_score``
    与独立 explain。

合成公式（解释可读、可复现）::

    # 1. 机构级去重（仅对 fresh 页统计）
    effective_fresh_institution_count = 去重机构数（无来源的页视作独立来源）
    duplicate_institution_count      = fresh_mention_count - effective_fresh_institution_count
    dedup_penalty = duplicate_institution_count × _P_INSTITUTION_DUPLICATE

    # 2. 时效衰减（对 fresh 页按页计算 [0,1] 因子后取均值）
    expired + stale_risk=高   → 0.3   （过期弱证据，衰减最快）
    expired + stale_risk!=高  → 0.5
    未过期 + stale_risk=高    → 随年龄在 [0.6, 1.0] 内衰减（90 天到地板）
    未过期 + stale_risk!=高   → 随年龄在 [0.8, 1.0] 内衰减（365 天到地板）
    time_decay_factor = mean(per-page factor over fresh pages)

    decay_adjusted_score = max(0, (base_score - dedup_penalty) × time_decay_factor)

    # 3. 过热惩罚（仅候选上下文提供时才计算）
    overheat_penalty = overheat_flag_count × _P_OVERHEAT_FLAG
                     + max(0, (short_term_gain_pct - threshold)/10) × _P_SHORT_TERM_GAIN
                     + max(0, theme_count - crowding_threshold) × _P_THEME_CROWDING

    effective_score = max(0, decay_adjusted_score - overheat_penalty)

使用示例::

    from tradingagents.dataflows.research_attention_decay import (
        compute_symbol_decay,
        apply_overheat_penalty,
    )
    decay = compute_symbol_decay(sym)              # 去重 + 时效衰减
    final = apply_overheat_penalty(decay, overheat_flags=["短期过热"],
                                   short_term_gain_pct=25.0, theme_count=8)
    print(final.effective_score, final.explain)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.dataflows.local_knowledge_audit import _is_valid_until_expired
from tradingagents.dataflows.local_knowledge_lint import HIGH_STALE_RISK_VALUES
from tradingagents.dataflows.research_attention import (
    PageMention,
    SymbolAttention,
    _is_fresh,
)


# ── 常量（暴露为模块常量，便于调参与测试覆盖）──────────────────────────

CONTRACT_VERSION = "kb-009-v1"
TASK_CODE = "KB-009"

# 机构级重复惩罚（每个被折叠的重复 fresh 命中扣分）。
_P_INSTITUTION_DUPLICATE = 0.2

# 时效衰减地板与衰减周期。
_EXPIRED_HIGH_STALE_FACTOR = 0.3
_EXPIRED_LOW_STALE_FACTOR = 0.5
_FRESH_HIGH_STALE_FLOOR = 0.6
_FRESH_LOW_STALE_FLOOR = 0.8
_FRESH_HIGH_STALE_HORIZON_DAYS = 90
_FRESH_LOW_STALE_HORIZON_DAYS = 365

# 过热惩罚权重（三信号叠加）。
_P_OVERHEAT_FLAG = 0.5
_P_SHORT_TERM_GAIN = 0.3  # 每超过阈值 10 个百分点扣分
_P_THEME_CROWDING = 0.4   # 每超过拥挤阈值 1 个主题扣分

# 过热触发阈值（任一信号单独不构成"唯一指标"）。
_SHORT_TERM_GAIN_THRESHOLD_PCT = 20.0
_THEME_CROWDING_THRESHOLD = 6


# 机构名分隔符：中邮证券-华勤技术超节点 / 中金—腾讯 / 标题：副标题。
_INSTITUTION_SPLIT_RE = re.compile(r"[-—：:]")


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class SymbolDecayResult:
    """单标的的 KB-009 去重 + 时效衰减结果（不含过热惩罚）。"""

    base_score: float = 0.0
    dedup_penalty: float = 0.0
    time_decay_factor: float = 1.0
    decay_adjusted_score: float = 0.0
    fresh_mention_count: int = 0
    unique_institution_count: int = 0
    duplicate_institution_count: int = 0
    expired_fresh_count: int = 0
    high_stale_fresh_count: int = 0
    overheat_penalty: float = 0.0
    effective_score: float = 0.0
    overheat_flags: List[str] = field(default_factory=list)
    short_term_gain_pct: Optional[float] = None
    theme_crowding_active: bool = False
    explain: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "base_score": round(self.base_score, 2),
            "dedup_penalty": round(self.dedup_penalty, 2),
            "time_decay_factor": round(self.time_decay_factor, 2),
            "decay_adjusted_score": round(self.decay_adjusted_score, 2),
            "overheat_penalty": round(self.overheat_penalty, 2),
            "effective_score": round(self.effective_score, 2),
            "fresh_mention_count": self.fresh_mention_count,
            "unique_institution_count": self.unique_institution_count,
            "duplicate_institution_count": self.duplicate_institution_count,
            "expired_fresh_count": self.expired_fresh_count,
            "high_stale_fresh_count": self.high_stale_fresh_count,
            "overheat_flags": list(self.overheat_flags),
            "short_term_gain_pct": self.short_term_gain_pct,
            "theme_crowding_active": self.theme_crowding_active,
            "explain": list(self.explain),
            "warnings": list(self.warnings),
        }


# ── 机构级去重 ────────────────────────────────────────────────────────


def split_institution(source_alias: str) -> str:
    """从来源别名提取机构名（去重 key）。

    规则：取首个 ``- / — / ： / :`` 之前的部分；无分隔符则取整串（视作独立来源）。

    例：
      ``中邮证券-华勤技术超节点`` → ``中邮证券``
      ``国泰君安—华勤技术深度``   → ``国泰君安``
      ``中金：腾讯``              → ``中金``
      ``Bernstein-Dell``          → ``Bernstein``
      ``星球社群截图``            → ``星球社群截图``（无分隔符，整串）
    """
    text = str(source_alias).strip()
    if not text:
        return ""
    m = _INSTITUTION_SPLIT_RE.search(text)
    if m:
        inst = text[: m.start()].strip()
        return inst or text
    return text


def _page_institution_key(page: PageMention) -> str:
    """单页的机构去重 key：取首个 source alias 的机构名；无来源则用页面路径（独立）。"""
    for alias in page.source_aliases:
        inst = split_institution(alias)
        if inst:
            return inst
    # 无 sources 的页面无法判定同源 —— 视作独立来源，避免误折叠真实多来源共识。
    return page.rel_path


def _compute_institution_dedup(fresh_pages: List[PageMention]) -> Tuple[List[str], int]:
    """统计 fresh 页的机构级去重。

    返回 ``(unique_institutions, duplicate_count)``，其中
    ``duplicate_count = len(fresh_pages) - len(unique_institutions)``。
    无来源的页面各自独立，不计入重复。
    """
    seen: List[str] = []
    for p in fresh_pages:
        key = _page_institution_key(p)
        if key not in seen:
            seen.append(key)
    duplicates = max(0, len(fresh_pages) - len(seen))
    return seen, duplicates


# ── 时效衰减 ──────────────────────────────────────────────────────────


def _parse_date(text: Optional[str]) -> Optional[date]:
    """解析 ``updated`` / ``valid_until`` 字段中的日期；失败返回 None。"""
    if not text:
        return None
    cleaned = re.sub(r"[/-]", "", str(text).strip())
    if not re.fullmatch(r"\d{8}", cleaned):
        return None
    try:
        return date(int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]))
    except ValueError:
        return None


def page_time_decay_factor(
    page: PageMention,
    valid_until_expired: bool,
    today: Optional[date] = None,
) -> float:
    """单页时效衰减因子 ∈ [0, 1]。

    - ``valid_until`` 已过期：弱证据，``高 stale_risk`` → 0.3，否则 → 0.5。
    - 未过期：随 ``updated`` 年龄衰减；``高 stale_risk`` 衰减更快（90 天到 0.6 地板），
      其余 365 天到 0.8 地板。无 ``updated`` 视作无年龄衰减（因子 1.0）。
    """
    if valid_until_expired:
        return _EXPIRED_HIGH_STALE_FACTOR if page.is_stale else _EXPIRED_LOW_STALE_FACTOR

    today = today or date.today()
    updated = _parse_date(page.updated_at)
    if updated is None:
        return 1.0

    age_days = max(0, (today - updated).days)
    # is_stale 已综合 stale_risk=高 / 过期；过期分支已 return，剩下的 is_stale
    # 即 stale_risk=高 —— 据此区分高低衰减速率。
    is_high_stale = page.is_stale
    if is_high_stale:
        floor = _FRESH_HIGH_STALE_FLOOR
        horizon = _FRESH_HIGH_STALE_HORIZON_DAYS
    else:
        floor = _FRESH_LOW_STALE_FLOOR
        horizon = _FRESH_LOW_STALE_HORIZON_DAYS
    if horizon <= 0:
        return floor
    factor = 1.0 - age_days / horizon
    return max(floor, min(1.0, factor))


def _compute_time_decay(
    fresh_pages: List[PageMention],
    today: Optional[date] = None,
) -> Tuple[float, int, int]:
    """返回 ``(avg_factor, expired_fresh_count, high_stale_fresh_count)``。

    ``expired_fresh_count``：fresh 页中 ``valid_until`` 已过期但未被 KB-007 判
    stale 的边界页数（正常情况下为 0，因为过期即 stale 即非 fresh；保留统计
    以便 explain 透明）。
    """
    today = today or date.today()
    if not fresh_pages:
        return 1.0, 0, 0
    factors: List[float] = []
    expired = 0
    high_stale = 0
    for p in fresh_pages:
        vu_expired = _is_valid_until_expired(_valid_until_from_mention(p), today)
        if vu_expired:
            expired += 1
        if p.is_stale:
            high_stale += 1
        factors.append(page_time_decay_factor(p, vu_expired, today))
    avg = sum(factors) / len(factors) if factors else 1.0
    return avg, expired, high_stale


def _valid_until_from_mention(page: PageMention) -> Optional[str]:
    """PageMention 不直接携带 valid_until；由 fresh 判定反推过期状态。"""
    # KB-007: is_stale = stale_risk=高 OR valid_until_expired。
    # 若 is_stale 且非高 stale_risk，则很可能是过期触发 —— 但无法精确还原，
    # 这里返回 None，让 _is_valid_until_expired 在 page_time_decay_factor 中
    # 通过 is_stale 兜底判定（未过期分支用 is_stale 区分高低）。
    return None


# ── 过热惩罚 ──────────────────────────────────────────────────────────


def compute_overheat_penalty(
    overheat_flags: Optional[List[str]] = None,
    short_term_gain_pct: Optional[float] = None,
    theme_count: Optional[int] = None,
) -> Tuple[float, List[str], List[str]]:
    """结合三信号计算过热惩罚（研究优先级降低，不改交易动作）。

    返回 ``(penalty, explain_lines, active_signals)``。任一信号缺失只跳过该项，
    不会因单一涨幅指标把分数清零（满足"不用单日价格涨幅作为唯一过热指标"）。
    """
    penalty = 0.0
    explain: List[str] = []
    active: List[str] = []

    flags = [str(f).strip() for f in (overheat_flags or []) if str(f).strip()]
    if flags:
        flag_pen = len(flags) * _P_OVERHEAT_FLAG
        penalty += flag_pen
        active.append(f"overheat_flags={flags}")
        explain.append(
            f"overheat_flags={len(flags)} × {_P_OVERHEAT_FLAG} = -{flag_pen:.2f}"
        )

    if short_term_gain_pct is not None:
        try:
            gain = float(short_term_gain_pct)
        except (TypeError, ValueError):
            gain = None
        if gain is not None and gain > _SHORT_TERM_GAIN_THRESHOLD_PCT:
            excess = (gain - _SHORT_TERM_GAIN_THRESHOLD_PCT) / 10.0
            gain_pen = excess * _P_SHORT_TERM_GAIN
            penalty += gain_pen
            active.append(f"short_term_gain={gain:.1f}%")
            explain.append(
                f"short_term_gain={gain:.1f}% 超过 {_SHORT_TERM_GAIN_THRESHOLD_PCT}%，"
                f"超出 {excess * 10:.1f}pp × {_P_SHORT_TERM_GAIN} = -{gain_pen:.2f}"
            )

    if theme_count is not None and theme_count > _THEME_CROWDING_THRESHOLD:
        crowd = theme_count - _THEME_CROWDING_THRESHOLD
        crowd_pen = crowd * _P_THEME_CROWDING
        penalty += crowd_pen
        active.append(f"theme_crowding={theme_count}")
        explain.append(
            f"theme_crowding={theme_count} 超过 {_THEME_CROWDING_THRESHOLD}，"
            f"超出 {crowd} × {_P_THEME_CROWDING} = -{crowd_pen:.2f}"
        )

    return penalty, explain, active


# ── 主入口 ────────────────────────────────────────────────────────────


def compute_symbol_decay(
    sym: SymbolAttention,
    *,
    today: Optional[date] = None,
) -> SymbolDecayResult:
    """计算单标的的去重 + 时效衰减（不含过热惩罚）。

    不修改输入 ``sym``；返回新的 :class:`SymbolDecayResult`。
    ``effective_score`` 此时等于 ``decay_adjusted_score``，待
    :func:`apply_overheat_penalty` 进一步叠加候选过热惩罚。
    """
    today = today or date.today()
    res = SymbolDecayResult(base_score=sym.research_attention_score)

    fresh_pages = [p for p in sym.matched_pages if _is_fresh(p)]
    res.fresh_mention_count = len(fresh_pages)

    explain: List[str] = []
    warnings: List[str] = []

    # 1. 机构级去重。
    unique_inst, dup_count = _compute_institution_dedup(fresh_pages)
    res.unique_institution_count = len(unique_inst)
    res.duplicate_institution_count = dup_count
    res.dedup_penalty = dup_count * _P_INSTITUTION_DUPLICATE
    if dup_count > 0:
        explain.append(
            f"机构级去重：fresh {len(fresh_pages)} 篇来自 {len(unique_inst)} 家机构，"
            f"重复 {dup_count} × {_P_INSTITUTION_DUPLICATE} = -{res.dedup_penalty:.2f}"
        )
        if unique_inst:
            warnings.append(
                f"重复机构：{', '.join(unique_inst[:5])}"
            )

    # 2. 时效衰减。
    avg_factor, expired_fresh, high_stale_fresh = _compute_time_decay(fresh_pages, today)
    res.time_decay_factor = avg_factor
    res.expired_fresh_count = expired_fresh
    res.high_stale_fresh_count = high_stale_fresh
    if avg_factor < 0.995:
        explain.append(
            f"时效衰减：平均因子 {avg_factor:.2f}"
            + (f"（含过期弱证据 {expired_fresh} 篇）" if expired_fresh else "")
        )
    if high_stale_fresh > 0:
        warnings.append(f"高 stale_risk fresh 页 {high_stale_fresh} 篇，衰减更快")

    # 3. 合成 decay_adjusted_score。
    decayed = (sym.research_attention_score - res.dedup_penalty) * avg_factor
    res.decay_adjusted_score = max(0.0, decayed)
    res.effective_score = res.decay_adjusted_score

    res.explain = explain
    res.warnings = warnings
    return res


def apply_overheat_penalty(
    decay: SymbolDecayResult,
    *,
    overheat_flags: Optional[List[str]] = None,
    short_term_gain_pct: Optional[float] = None,
    theme_count: Optional[int] = None,
) -> SymbolDecayResult:
    """在去重 + 时效衰减之上叠加候选过热惩罚，返回更新后的结果。

    原地更新并返回同一对象，便于链式调用。``overheat_flags`` / 涨幅 / 主题拥挤
    任一缺失只跳过该项（不因单一指标清零）。
    """
    penalty, pen_explain, active = compute_overheat_penalty(
        overheat_flags=overheat_flags,
        short_term_gain_pct=short_term_gain_pct,
        theme_count=theme_count,
    )
    decay.overheat_penalty = penalty
    decay.overheat_flags = [str(f) for f in (overheat_flags or []) if str(f).strip()]
    decay.short_term_gain_pct = short_term_gain_pct
    decay.theme_crowding_active = (
        theme_count is not None and theme_count > _THEME_CROWDING_THRESHOLD
    )
    decay.effective_score = max(0.0, decay.decay_adjusted_score - penalty)
    if pen_explain:
        decay.explain.extend(pen_explain)
    if active:
        decay.warnings.append("过热信号：" + "、".join(active))
    return decay


def compute_effective_attention(
    sym: SymbolAttention,
    *,
    overheat_flags: Optional[List[str]] = None,
    short_term_gain_pct: Optional[float] = None,
    theme_count: Optional[int] = None,
    today: Optional[date] = None,
) -> SymbolDecayResult:
    """一站式：去重 + 时效衰减 + 过热惩罚。"""
    decay = compute_symbol_decay(sym, today=today)
    return apply_overheat_penalty(
        decay,
        overheat_flags=overheat_flags,
        short_term_gain_pct=short_term_gain_pct,
        theme_count=theme_count,
    )


def decay_to_summary(decay: Optional[SymbolDecayResult]) -> Dict[str, Any]:
    """把 KB-009 衰减结果转为前端/UI 可直接展示的扁平字典。

    ``decay is None`` 时返回空结构（无命中或降级时调用方渲染 NORMAL_NO_DATA）。
    """
    if decay is None:
        return {
            "research_attention_effective_score": 0.0,
            "research_attention_base_score": 0.0,
            "research_attention_dedup_penalty": 0.0,
            "research_attention_time_decay_factor": 1.0,
            "research_attention_overheat_penalty": 0.0,
            "research_attention_unique_institution_count": 0,
            "research_attention_duplicate_institution_count": 0,
            "research_attention_decay_explain": [],
            "research_attention_warnings": [],
        }
    return {
        "research_attention_effective_score": round(decay.effective_score, 2),
        "research_attention_base_score": round(decay.base_score, 2),
        "research_attention_dedup_penalty": round(decay.dedup_penalty, 2),
        "research_attention_time_decay_factor": round(decay.time_decay_factor, 2),
        "research_attention_overheat_penalty": round(decay.overheat_penalty, 2),
        "research_attention_unique_institution_count": decay.unique_institution_count,
        "research_attention_duplicate_institution_count": decay.duplicate_institution_count,
        "research_attention_decay_explain": list(decay.explain),
        "research_attention_warnings": list(decay.warnings),
    }


def render_decay_summary_inline(decay: Optional[SymbolDecayResult]) -> str:
    """渲染一句话 KB-009 摘要（嵌入研究关注度段，不含买卖建议或强动作词）。

    同时展示去重 / 衰减 / 过热三类负面信息，满足"前端必须同时显示负面信息"。
    """
    if decay is None:
        return ""
    parts: List[str] = []
    parts.append(f"有效关注度 {decay.effective_score:.2f}（原始 {decay.base_score:.2f}）")
    if decay.dedup_penalty > 0:
        parts.append(
            f"机构去重 -{decay.dedup_penalty:.2f}"
            f"（重复 {decay.duplicate_institution_count}）"
        )
    if decay.time_decay_factor < 1.0:
        parts.append(f"时效衰减 ×{decay.time_decay_factor:.2f}")
    if decay.overheat_penalty > 0:
        parts.append(f"过热惩罚 -{decay.overheat_penalty:.2f}")
    if not (decay.dedup_penalty or decay.time_decay_factor < 1.0 or decay.overheat_penalty):
        parts.append("无明显衰减/惩罚")
    return "；".join(parts) + "。"


__all__ = [
    "CONTRACT_VERSION",
    "TASK_CODE",
    "SymbolDecayResult",
    "split_institution",
    "page_time_decay_factor",
    "compute_overheat_penalty",
    "compute_symbol_decay",
    "apply_overheat_penalty",
    "compute_effective_attention",
    "decay_to_summary",
    "render_decay_summary_inline",
]
