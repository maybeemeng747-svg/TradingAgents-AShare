# [HY-005] thesis_fact_check
"""旧研报观点 vs 半年报事实反证检测。

在 HY-003 半年报事实表、KB-015 研报观点/事实分离索引、KB-007 研究关注度之上，
本模块把旧研报/评分表中的**核心观点 / 前瞻指引**与最新半年报**结构化事实**做
规则比对，标注每个观点被事实 ``supported`` / ``weakened`` / ``contradicted`` /
``insufficient_data``，并产出 ``contradiction_flags`` 与 ``needs_tree_work_review``。

设计约束（对应任务 HY-005）：
  - **不做投资结论**：只做证据一致性标注，``thesis_check_status`` 是证据状态，
    不是买卖建议；输出文案刻意避免 ``买入/卖出/加仓/减仓/强烈推荐`` 等强动作词。
  - **不把单一指标变化扩大成"逻辑破坏"**：每个 flag 都带 ``evidence_level``
    （strong / moderate / weak / none），聚合状态在多数观点被削弱/打脸时才升级。
  - **不调用 live LLM / 不访问外网**：纯标准库 + 复用 HY-003 / KB-015 已有
    只读解析结果；不重复扫描知识库。
  - **不写生产 DB**：本模块是只读比对器，输入是已查询好的 dataclass。
  - **事实优先于观点**：当事实来自 ``fresh`` 半年报页（exchange_filing）时，
    事实方向压倒观点方向；观点来自 ``opinion`` 类来源时不参与反证。
  - **与 KB-007 research attention 联动**：关注度高但事实反证强的股票优先提醒
    （``priority_reminder=True``），但只影响提醒优先级，不改变强动作门禁。

反证规则（按 metric_key 分流）：
  1. **revenue / net_profit / gross_margin / operating_cash_flow**：
     - 观点方向 vs 事实同比变化方向（正/负）。
     - 方向一致 → ``supported``；方向相反 → ``contradicted``；
       方向一致但事实增幅明显低于观点预期 → ``weakened``。
  2. **segment**（分业务观点）：
     - 观点提到某分业务"放量/爆发/高增"，但事实中该分业务文本含"下滑/萎缩/
       承压/同比-X%" → ``contradicted``；含"增长/提升" → ``supported``。
     - 事实中找不到对应分业务文本 → ``insufficient_data``。
  3. **risk**（风险观点）：
     - 观点提示的风险（如"客户集中度"）在事实 ``risk_factors`` 中复现 →
       ``supported``（风险观点被事实确认）。
  4. **数值断言**：观点含 ``+X%`` 预期，事实同比变化为 ``-Y%`` → ``contradicted``；
     事实变化 ``+Z%`` 但 ``Z < X×0.5`` → ``weakened``。

聚合状态（``thesis_check_status``）优先级：
  ``contradicted`` > ``weakened`` > ``supported`` > ``insufficient_data``

使用示例::

    from tradingagents.dataflows.thesis_fact_check import (
        check_thesis_against_facts,
        render_thesis_fact_check_report,
    )
    from tradingagents.dataflows.half_year_facts_provider import query_half_year_facts
    from tradingagents.dataflows.research_fact_opinion_index import (
        build_research_fact_opinion_index,
    )

    facts = query_half_year_facts(kb_root, symbol="300750")
    opinions = build_research_fact_opinion_index(kb_root, symbol="300750")
    result = check_thesis_against_facts(opinions, facts)
    print(render_thesis_fact_check_report(result))
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.dataflows.half_year_facts_provider import (
    DATA_CONFLICT,
    DATA_FRESH,
    DATA_MISSING_FACTS,
    DATA_MISSING_PERIOD,
    DATA_OPINION_ONLY,
    DATA_STALE,
    HalfYearFactsPage,
    HalfYearFactsQueryResult,
    ParsedMetric,
)
from tradingagents.dataflows.local_knowledge_provider import (
    STATUS_HAS_DATA,
    STATUS_NORMAL_NO_DATA,
)
from tradingagents.dataflows.research_fact_opinion_index import (
    CLAIM_FACT,
    CLAIM_FORECAST,
    CLAIM_OPINION,
    CLAIM_RISK,
    CLAIM_UNKNOWN,
    ResearchClaimItem,
    ResearchFactOpinionIndexResult,
    ResearchFactOpinionPage,
    STALE_FRESH,
)


# ── 常量 ──────────────────────────────────────────────────────────────

TASK_CODE = "HY-005"

# thesis_check_status 枚举（与任务要求严格一致）。
STATUS_SUPPORTED = "supported"
STATUS_WEAKENED = "weakened"
STATUS_CONTRADICTED = "contradicted"
STATUS_INSUFFICIENT_DATA = "insufficient_data"
ALL_THESIS_STATUSES: Tuple[str, ...] = (
    STATUS_SUPPORTED,
    STATUS_WEAKENED,
    STATUS_CONTRADICTED,
    STATUS_INSUFFICIENT_DATA,
)

# 证据等级（用于"不把单一指标变化扩大成逻辑破坏"约束）。
EVIDENCE_STRONG = "strong"
EVIDENCE_MODERATE = "moderate"
EVIDENCE_WEAK = "weak"
EVIDENCE_NONE = "none"

# 方向标签。
DIR_POSITIVE = "positive"
DIR_NEGATIVE = "negative"
DIR_NEUTRAL = "neutral"

# 文本裁剪上限。
_TEXT_MAX_CHARS = 120
_REASON_MAX_CHARS = 160
_MAX_FLAGS = 20
_SUMMARY_MAX_ENTRIES = 6

# 数值百分比解析：``+30.1%`` / ``-20.0%`` / ``+1.2pp``。
_PCT_RE = re.compile(
    r"([-+])\s*(\d+(?:\.\d+)?)\s*(?:%|pp|个百分点)"
)

# 方向关键词（子串匹配，大小写不敏感）。
# 正向：增长/提升/放量/爆发/高增/强劲/突破/超预期/上行/景气。
# 注意：不含「大幅」——它是修饰词，会与「大幅下降」冲突。
_POSITIVE_KEYWORDS: Tuple[str, ...] = (
    "增长", "提升", "放量", "爆发", "高增", "强劲", "突破",
    "超预期", "上行", "景气", "扩张", "回暖", "复苏", "反弹",
    "盈利", "向好", "加速", "攀升", "创新高",
)
# 负向：下滑/下降/萎缩/承压/暴跌/大幅回落/不及预期/下行/疲软/亏损/恶化。
_NEGATIVE_KEYWORDS: Tuple[str, ...] = (
    "下滑", "下降", "萎缩", "承压", "暴跌", "回落", "不及预期",
    "下行", "疲软", "亏损", "恶化", "收缩", "走弱", "大幅下降",
    "负增长", "同比下降", "大幅下滑", "腰斩",
)

# 分业务关键词（从观点文本中提取分业务主题词）。
_SEGMENT_KEYWORDS: Tuple[str, ...] = (
    "储能", "动力电池", "AI服务器", "光伏", "新能源车", "半导体",
    "消费电子", "海外", "国内", "政企", "渠道", "云", "软件",
    "创新业务", "高端", "中端", "低端", "整车", "零部件", "芯片",
    "白酒", "医药", "地产", "银行", "保险", "券商",
)

# 事实页 data_status 是否足够可靠用于反证（只有 fresh / conflict 可用；
# stale / opinion_only / missing 不参与反证，只标 insufficient_data）。
_RELIABLE_FACT_STATUSES = frozenset({DATA_FRESH, DATA_CONFLICT})


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class ThesisCheckFlag:
    """单条观点 vs 事实的比对标记。

    每条 flag 记录一个观点与对应事实的比对结论，携带可读 ``reason``。
    """

    metric_key: str
    """比对的指标维度（revenue / net_profit / gross_margin / operating_cash_flow
    / segment / risk / other）。"""

    opinion_text: str
    """原始观点文本（裁剪到 _TEXT_MAX_CHARS）。"""

    fact_text: str
    """对应的事实文本（裁剪到 _TEXT_MAX_CHARS）。无匹配事实时为空串。"""

    opinion_direction: str = DIR_NEUTRAL
    """观点隐含的方向（positive / negative / neutral）。"""

    fact_direction: str = DIR_NEUTRAL
    """事实隐含的方向（positive / negative / neutral）。"""

    opinion_change: Optional[str] = None
    """观点中隐含的预期变化（如 ``+60%``），无法解析时为 None。"""

    fact_change: Optional[str] = None
    """事实同比变化（如 ``-20.0%``），无法解析时为 None。"""

    check_status: str = STATUS_INSUFFICIENT_DATA
    """本条 flag 的结论状态。"""

    evidence_level: str = EVIDENCE_NONE
    """本条 flag 的证据强度。"""

    reason: str = ""
    """可读说明（裁剪到 _REASON_MAX_CHARS）。"""

    source_path: str = ""
    """观点来源页面路径。"""

    fact_source_path: str = ""
    """事实来源页面路径。"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_key": self.metric_key,
            "opinion_text": self.opinion_text,
            "fact_text": self.fact_text,
            "opinion_direction": self.opinion_direction,
            "fact_direction": self.fact_direction,
            "opinion_change": self.opinion_change,
            "fact_change": self.fact_change,
            "check_status": self.check_status,
            "evidence_level": self.evidence_level,
            "reason": self.reason,
            "source_path": self.source_path,
            "fact_source_path": self.fact_source_path,
        }


@dataclass
class ThesisFactCheckResult:
    """整次观点-事实反证检测的聚合结果。"""

    symbol: str = ""
    name: str = ""
    thesis_check_status: str = STATUS_INSUFFICIENT_DATA
    """聚合状态（优先级 contradicted > weakened > supported > insufficient_data）。"""

    flags: List[ThesisCheckFlag] = field(default_factory=list)
    """全部比对 flag（含 supported / weakened / contradicted / insufficient_data）。"""

    contradiction_flags: List[ThesisCheckFlag] = field(default_factory=list)
    """被事实打脸的 flag 子集（check_status=contradicted）。"""

    weakened_flags: List[ThesisCheckFlag] = field(default_factory=list)
    """被事实削弱的 flag 子集（check_status=weakened）。"""

    supported_flags: List[ThesisCheckFlag] = field(default_factory=list)
    """被事实支持的 flag 子集（check_status=supported）。"""

    needs_tree_work_review: bool = False
    """是否存在需要 Tree Work 人工复核的反证。"""

    research_attention_score: Optional[float] = None
    """KB-007 研究关注度分数（可选传入）。"""

    priority_reminder: bool = False
    """高关注度 + 强反证时为 True，提示优先提醒。"""

    evidence_level: str = EVIDENCE_NONE
    """聚合证据强度。"""

    checked_opinion_count: int = 0
    """参与了比对（非 insufficient_data）的观点数。"""

    total_opinion_count: int = 0
    """检测覆盖的观点总数。"""

    fact_period: Optional[str] = None
    """作为比对基准的事实报告期。"""

    fact_data_status: Optional[str] = None
    """事实页的 data_status（fresh / stale / ...）。"""

    summary: List[str] = field(default_factory=list)
    """人类可读的摘要条目（最多 _SUMMARY_MAX_ENTRIES）。"""

    task: str = TASK_CODE
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "thesis_check_status": self.thesis_check_status,
            "flags": [f.to_dict() for f in self.flags],
            "contradiction_flags": [f.to_dict() for f in self.contradiction_flags],
            "weakened_flags": [f.to_dict() for f in self.weakened_flags],
            "supported_flags": [f.to_dict() for f in self.supported_flags],
            "needs_tree_work_review": self.needs_tree_work_review,
            "research_attention_score": (
                round(self.research_attention_score, 2)
                if self.research_attention_score is not None
                else None
            ),
            "priority_reminder": self.priority_reminder,
            "evidence_level": self.evidence_level,
            "checked_opinion_count": self.checked_opinion_count,
            "total_opinion_count": self.total_opinion_count,
            "fact_period": self.fact_period,
            "fact_data_status": self.fact_data_status,
            "summary": list(self.summary),
            "task": self.task,
            "errors": list(self.errors),
        }


# ── 文本工具 ──────────────────────────────────────────────────────────


def _clip(text: str, max_chars: int = _TEXT_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "…"
    return text


def _classify_direction(text: str) -> str:
    """从文本推断隐含方向（positive / negative / neutral）。

    优先看数值变化的正负号；无数值时按关键词判定。
    """
    if not text:
        return DIR_NEUTRAL
    # 优先：解析到的 change（``+30%`` / ``-20%``）。
    pct = _parse_pct(text)
    if pct is not None:
        return DIR_POSITIVE if pct > 0 else (DIR_NEGATIVE if pct < 0 else DIR_NEUTRAL)
    # 关键词。
    lower = text.lower()
    has_pos = any(kw in text for kw in _POSITIVE_KEYWORDS)
    has_neg = any(kw in text for kw in _NEGATIVE_KEYWORDS)
    if has_neg and not has_pos:
        return DIR_NEGATIVE
    if has_pos and not has_neg:
        return DIR_POSITIVE
    return DIR_NEUTRAL


def _parse_pct(text: str) -> Optional[float]:
    """从文本中解析首个 ``+X%`` / ``-X%`` 的数值部分，返回带符号的 float。

    ``+30.1%`` → ``30.1``；``-20.0%`` → ``-20.0``；无匹配 → ``None``。
    """
    if not text:
        return None
    m = _PCT_RE.search(text)
    if not m:
        return None
    sign = m.group(1)
    val = float(m.group(2))
    return val if sign == "+" else -val


def _extract_segment_topic(text: str) -> Optional[str]:
    """从观点文本中提取分业务主题词。

    返回首个命中的 ``_SEGMENT_KEYWORDS`` 条目；无命中返回 ``None``。
    """
    if not text:
        return None
    for kw in _SEGMENT_KEYWORDS:
        if kw in text:
            return kw
    return None


def _strip_symbol_to_code(entry: str) -> str:
    """``000977.SZ 浪潮信息`` → ``000977``。

    取首个 token，去交易所后缀，只保留 6 位数字代码；无代码时原样返回。
    """
    if not entry:
        return ""
    code = entry.split()[0] if " " in entry else entry
    return re.sub(r"\.(SH|SZ|BJ|HK|US|SS)$", "", code, flags=re.IGNORECASE).strip()


# ── 事实索引构建 ──────────────────────────────────────────────────────


@dataclass
class _FactIndex:
    """从 :class:`HalfYearFactsQueryResult` 构建的按指标索引的事实快照。

    只索引 ``data_status ∈ _RELIABLE_FACT_STATUSES`` 的页面中的事实，
    确保反证只用可靠事实。
    """

    metrics: Dict[str, ParsedMetric] = field(default_factory=dict)
    """metric_key → 最新事实指标（revenue / net_profit / ...）。"""

    segments: List[str] = field(default_factory=list)
    """分业务事实文本列表（用于分业务观点比对）。"""

    risks: List[str] = field(default_factory=list)
    """结构化风险文本列表（用于风险观点比对）。"""

    period: Optional[str] = None
    """事实基准报告期。"""

    data_status: Optional[str] = None
    """事实页的 data_status。"""

    source_path: str = ""
    """事实来源页面路径。"""


def _build_fact_index(facts: HalfYearFactsQueryResult) -> _FactIndex:
    """从半年报事实查询结果构建按指标索引的事实快照。

    选择策略：
      1. 优先取 ``data_status=fresh`` 的最新报告期页面。
      2. 若只有 stale 页（STALE），降级使用 stale 页（反证结果标 weak evidence）。
      3. opinion_only / missing 页不参与反证。
    """
    idx = _FactIndex()
    if not isinstance(facts, HalfYearFactsQueryResult):
        return idx
    if not facts.pages:
        return idx

    # 优先选 fresh 页（或 conflict 页，同样有可读事实）。
    candidate: Optional[HalfYearFactsPage] = None
    fallback_stale: Optional[HalfYearFactsPage] = None
    for p in facts.pages:
        if p.data_status in _RELIABLE_FACT_STATUSES:
            if candidate is None or _period_ge(p.financial_period, candidate.financial_period):
                candidate = p
        elif p.data_status == DATA_STALE:
            if fallback_stale is None or _period_ge(p.financial_period, fallback_stale.financial_period):
                fallback_stale = p

    chosen = candidate or fallback_stale
    if chosen is None:
        return idx

    idx.period = chosen.financial_period
    idx.data_status = chosen.data_status
    idx.source_path = chosen.rel_path

    for m in chosen.financial_facts:
        if m.metric_key in ("revenue", "net_profit", "gross_margin", "operating_cash_flow"):
            # 同指标取首个（页面内已裁剪）；不覆盖已有（避免重复指标干扰）。
            if m.metric_key not in idx.metrics:
                idx.metrics[m.metric_key] = m

    idx.segments = list(chosen.segment_facts or [])
    idx.risks = list(chosen.risk_factors or [])
    return idx


def _period_ge(a: Optional[str], b: Optional[str]) -> bool:
    """粗粒度比较报告期 a >= b（按年份 + H1/H2）。"""
    if not a:
        return False
    if not b:
        return True
    return _period_sort_key(a) >= _period_sort_key(b)


def _period_sort_key(period: str) -> Tuple[int, int]:
    """报告期排序键：``2025H1`` → ``(2025, 1)``；``2025H2`` → ``(2025, 2)``。"""
    m = re.match(r"(\d{4})", period)
    year = int(m.group(1)) if m else 0
    half = 2 if ("H2" in period or "年报" in period or "三季报" in period) else 1
    return (year, half)


# ── 观点收集 ──────────────────────────────────────────────────────────


def _collect_opinions(
    index_result: ResearchFactOpinionIndexResult,
) -> List[Tuple[ResearchClaimItem, str]]:
    """从 KB-015 索引收集可参与反证的观点。

    收集范围：
      - ``research_claims``（opinion/unknown 类型）。
      - ``forecast_items``（预测类）。
      - ``reported_facts`` 中含**观点方向关键词**的 claim（broker 观点存在
        management_commentary 等 fact-origin 字段时，KB-015 可能标为 fact，
        但文本含「爆发/放量/高增/下滑」等方向词 → 仍是可反证的观点）。

    纯事实声明（``营收 420.4亿 (+60%)`` 无方向关键词）不参与反证。
    risk 类由 :func:`_collect_risk_opinions` 单独处理。

    返回 ``(claim, page_rel_path)`` 列表。
    """
    opinions: List[Tuple[ResearchClaimItem, str]] = []
    if not isinstance(index_result, ResearchFactOpinionIndexResult):
        return opinions
    for page in index_result.pages:
        for claim in page.research_claims + page.forecast_items:
            opinions.append((claim, page.rel_path))
        # reported_facts 中含方向关键词的也参与（broker 观点冒充事实的兜底）。
        for claim in page.reported_facts:
            direction = _classify_direction(claim.text)
            if direction != DIR_NEUTRAL:
                opinions.append((claim, page.rel_path))
    return opinions


def _collect_risk_opinions(
    index_result: ResearchFactOpinionIndexResult,
) -> List[Tuple[ResearchClaimItem, str]]:
    """从 KB-015 索引收集风险类观点（用于风险确认比对）。"""
    risks: List[Tuple[ResearchClaimItem, str]] = []
    if not isinstance(index_result, ResearchFactOpinionIndexResult):
        return risks
    for page in index_result.pages:
        for claim in page.risk_items:
            risks.append((claim, page.rel_path))
    return risks


# ── 单观点比对 ────────────────────────────────────────────────────────


def _match_opinion_to_metric(text: str) -> str:
    """从观点文本推断它讨论的指标维度。

    返回 metric_key（revenue / net_profit / gross_margin / operating_cash_flow
    / segment / risk / other）。
    """
    lower = (text or "").lower()
    if any(kw in lower for kw in ("营收", "营业收入", "营业总收入", "revenue", "总收入")):
        return "revenue"
    if any(kw in lower for kw in ("归母净利", "归母净利润", "净利润", "净利", "利润", "net profit", "net income")):
        return "net_profit"
    if any(kw in lower for kw in ("毛利率", "gross margin")):
        return "gross_margin"
    if any(kw in lower for kw in ("经营性现金流", "经营现金流", "经营活动现金流", "operating cash flow", "现金流净额")):
        return "operating_cash_flow"
    # 分业务：含分业务关键词且不含上述通用指标词。
    if _extract_segment_topic(text):
        return "segment"
    return "other"


def _check_financial_metric_opinion(
    claim: ResearchClaimItem,
    rel_path: str,
    metric_key: str,
    fact_idx: _FactIndex,
) -> ThesisCheckFlag:
    """比对一个财务指标观点与对应事实。

    比对逻辑：
      - 无对应事实 → insufficient_data。
      - 观点方向 vs 事实方向：
        - 相反 → contradicted（strong）。
        - 一致且观点有数值预期 → 比较幅度（事实 << 预期 → weakened）。
        - 一致且幅度接近 → supported。
    """
    metric = fact_idx.metrics.get(metric_key)
    opinion_text = _clip(claim.text)
    opinion_dir = _classify_direction(claim.text)
    opinion_pct = _parse_pct(claim.text)

    if metric is None:
        return ThesisCheckFlag(
            metric_key=metric_key,
            opinion_text=opinion_text,
            fact_text="",
            opinion_direction=opinion_dir,
            fact_direction=DIR_NEUTRAL,
            opinion_change=(f"+{opinion_pct}%" if opinion_pct is not None else None),
            fact_change=None,
            check_status=STATUS_INSUFFICIENT_DATA,
            evidence_level=EVIDENCE_NONE,
            reason=f"观点讨论{metric_key}但半年报中无对应{metric_key}事实，无法反证。",
            source_path=rel_path,
            fact_source_path=fact_idx.source_path,
        )

    fact_text = _clip(metric.raw)
    fact_dir = _classify_direction(metric.raw)
    fact_pct = _parse_pct(metric.raw or "") or _parse_pct(metric.change or "")

    base = ThesisCheckFlag(
        metric_key=metric_key,
        opinion_text=opinion_text,
        fact_text=fact_text,
        opinion_direction=opinion_dir,
        fact_direction=fact_dir,
        opinion_change=(f"+{opinion_pct}%" if opinion_pct is not None else (claim.text if "%" not in claim.text else None)),
        fact_change=metric.change,
        source_path=rel_path,
        fact_source_path=fact_idx.source_path,
    )

    # 方向相反 → contradicted。
    if (
        opinion_dir in (DIR_POSITIVE, DIR_NEGATIVE)
        and fact_dir in (DIR_POSITIVE, DIR_NEGATIVE)
        and opinion_dir != fact_dir
    ):
        base.check_status = STATUS_CONTRADICTED
        base.evidence_level = _metric_evidence_level(fact_idx, strong=True)
        base.reason = _clip(
            f"观点预期{metric_key}{opinion_dir}（{opinion_dir}），"
            f"但半年报事实显示{fact_dir}（{metric.change or fact_text}），方向相反。",
            _REASON_MAX_CHARS,
        )
        return base

    # 方向一致 → 比较 amplitude。
    if opinion_dir == fact_dir and opinion_dir != DIR_NEUTRAL:
        # 如果观点有具体数值预期，比较幅度。
        if opinion_pct is not None and fact_pct is not None:
            expected = abs(opinion_pct)
            actual = abs(fact_pct)
            if expected > 0 and actual < expected * 0.5:
                base.check_status = STATUS_WEAKENED
                base.evidence_level = _metric_evidence_level(fact_idx, strong=False)
                base.reason = _clip(
                    f"观点预期{metric_key}增长约{expected:.1f}%，"
                    f"但事实仅{actual:.1f}%，增幅明显低于预期。",
                    _REASON_MAX_CHARS,
                )
                return base
        base.check_status = STATUS_SUPPORTED
        base.evidence_level = _metric_evidence_level(fact_idx, strong=False)
        base.reason = _clip(
            f"观点预期{metric_key}{opinion_dir}，半年报事实同为{fact_dir}（{metric.change or fact_text}），方向一致。",
            _REASON_MAX_CHARS,
        )
        return base

    # 至少一方 neutral。
    if fact_dir != DIR_NEUTRAL and opinion_dir == DIR_NEUTRAL:
        # 观点无明确方向但有数值预期。
        if opinion_pct is not None and fact_pct is not None:
            if (opinion_pct > 0) != (fact_pct > 0):
                base.check_status = STATUS_CONTRADICTED
                base.evidence_level = _metric_evidence_level(fact_idx, strong=True)
                base.reason = _clip(
                    f"观点隐含{metric_key}正向预期（+{opinion_pct:.1f}%），"
                    f"但事实为{fact_pct:.1f}%，方向相反。",
                    _REASON_MAX_CHARS,
                )
                return base
        base.check_status = STATUS_INSUFFICIENT_DATA
        base.evidence_level = EVIDENCE_WEAK
        base.reason = _clip(
            f"观点无明确方向，无法与{metric_key}事实做方向比对。",
            _REASON_MAX_CHARS,
        )
        return base

    # 双方 neutral。
    base.check_status = STATUS_INSUFFICIENT_DATA
    base.evidence_level = EVIDENCE_WEAK
    base.reason = _clip(
        f"观点与{metric_key}事实均无明确方向，无法反证。",
        _REASON_MAX_CHARS,
    )
    return base


def _check_segment_opinion(
    claim: ResearchClaimItem,
    rel_path: str,
    fact_idx: _FactIndex,
) -> ThesisCheckFlag:
    """比对一个分业务观点与对应分业务事实。

    通过关键词匹配观点提到的分业务主题（如"储能"），然后在 segment_facts
    中查找含该关键词的文本做方向比对。
    """
    topic = _extract_segment_topic(claim.text) or "segment"
    opinion_text = _clip(claim.text)
    opinion_dir = _classify_direction(claim.text)

    # 在 segment_facts 中查找含 topic 的文本。
    matched_seg = ""
    for seg in fact_idx.segments:
        if topic in seg:
            matched_seg = seg
            break

    if not matched_seg:
        # 分业务事实中找不到，也看 financial_facts raw 里有没有含 topic 的。
        for m in fact_idx.metrics.values():
            if topic in (m.raw or ""):
                matched_seg = m.raw
                break

    if not matched_seg:
        return ThesisCheckFlag(
            metric_key="segment",
            opinion_text=opinion_text,
            fact_text="",
            opinion_direction=opinion_dir,
            fact_direction=DIR_NEUTRAL,
            check_status=STATUS_INSUFFICIENT_DATA,
            evidence_level=EVIDENCE_NONE,
            reason=_clip(
                f"观点讨论分业务「{topic}」，但半年报分业务事实中无对应记录，无法反证。",
                _REASON_MAX_CHARS,
            ),
            source_path=rel_path,
            fact_source_path=fact_idx.source_path,
        )

    fact_dir = _classify_direction(matched_seg)
    fact_text = _clip(matched_seg)
    flag = ThesisCheckFlag(
        metric_key="segment",
        opinion_text=opinion_text,
        fact_text=fact_text,
        opinion_direction=opinion_dir,
        fact_direction=fact_dir,
        source_path=rel_path,
        fact_source_path=fact_idx.source_path,
    )

    if opinion_dir in (DIR_POSITIVE, DIR_NEGATIVE) and fact_dir in (DIR_POSITIVE, DIR_NEGATIVE):
        if opinion_dir != fact_dir:
            flag.check_status = STATUS_CONTRADICTED
            flag.evidence_level = _metric_evidence_level(fact_idx, strong=True)
            flag.reason = _clip(
                f"观点预期分业务「{topic}」{opinion_dir}，但事实显示{fact_dir}（{fact_text}），方向相反。",
                _REASON_MAX_CHARS,
            )
        else:
            flag.check_status = STATUS_SUPPORTED
            flag.evidence_level = _metric_evidence_level(fact_idx, strong=False)
            flag.reason = _clip(
                f"观点预期分业务「{topic}」{opinion_dir}，事实同向（{fact_text}）。",
                _REASON_MAX_CHARS,
            )
    else:
        flag.check_status = STATUS_INSUFFICIENT_DATA
        flag.evidence_level = EVIDENCE_WEAK
        flag.reason = _clip(
            f"观点或事实方向不明确，分业务「{topic}」无法反证。",
            _REASON_MAX_CHARS,
        )
    return flag


def _check_risk_opinion(
    claim: ResearchClaimItem,
    rel_path: str,
    fact_idx: _FactIndex,
) -> ThesisCheckFlag:
    """比对一个风险观点与事实风险列表。

    风险观点在事实 risk_factors 中复现 → supported（风险被确认）；
    否则 → insufficient_data（不轻易说风险被"打脸"，因为没列入不代表不存在）。
    """
    opinion_text = _clip(claim.text)
    # 提取风险关键词（取前 4 个字做模糊匹配）。
    risk_kw = _extract_risk_keyword(claim.text)

    matched = ""
    for r in fact_idx.risks:
        if risk_kw and risk_kw in r:
            matched = r
            break

    flag = ThesisCheckFlag(
        metric_key="risk",
        opinion_text=opinion_text,
        fact_text=_clip(matched),
        opinion_direction=DIR_NEUTRAL,
        fact_direction=DIR_NEUTRAL,
        source_path=rel_path,
        fact_source_path=fact_idx.source_path,
    )

    if matched:
        flag.check_status = STATUS_SUPPORTED
        flag.evidence_level = EVIDENCE_MODERATE
        flag.reason = _clip(
            f"风险观点「{risk_kw}」在半年报风险因素中复现，风险被事实确认。",
            _REASON_MAX_CHARS,
        )
    else:
        flag.check_status = STATUS_INSUFFICIENT_DATA
        flag.evidence_level = EVIDENCE_NONE
        flag.reason = _clip(
            f"风险观点「{risk_kw}」未在半年报风险因素中找到直接对应，不构成反证。",
            _REASON_MAX_CHARS,
        )
    return flag


def _extract_risk_keyword(text: str) -> str:
    """从风险观点文本中提取关键词用于在事实风险列表中匹配。

    策略：优先取分业务关键词；否则取首个 2-4 字中文片段。
    """
    seg = _extract_segment_topic(text)
    if seg:
        return seg
    # 常见风险关键词。
    for kw in ("客户集中度", "原材料", "汇率", "政策", "GPU供应", "供应紧张",
               "宏观经济", "批价", "资产质量", "库存", "应收", "现金流"):
        if kw in text:
            return kw
    # 兜底：取前 4 字。
    return text[:4].strip()


def _metric_evidence_level(fact_idx: _FactIndex, *, strong: bool) -> str:
    """根据事实页 data_status 决定证据等级。

    fresh 页 → strong/moderate；stale 页 → weak（降级）。
    """
    if fact_idx.data_status == DATA_FRESH:
        return EVIDENCE_STRONG if strong else EVIDENCE_MODERATE
    if fact_idx.data_status == DATA_STALE:
        return EVIDENCE_WEAK
    return EVIDENCE_WEAK


# ── 聚合 ──────────────────────────────────────────────────────────────


_STATUS_PRIORITY = {
    STATUS_CONTRADICTED: 4,
    STATUS_WEAKENED: 3,
    STATUS_SUPPORTED: 2,
    STATUS_INSUFFICIENT_DATA: 1,
}


def _aggregate_thesis_status(flags: List[ThesisCheckFlag]) -> str:
    """聚合 flag 状态为整体 thesis_check_status。

    优先级：contradicted > weakened > supported > insufficient_data。
    但需要至少 1 个非 insufficient_data 的 flag，否则整体 insufficient_data。
    """
    if not flags:
        return STATUS_INSUFFICIENT_DATA
    best = STATUS_INSUFFICIENT_DATA
    best_pri = 0
    for f in flags:
        pri = _STATUS_PRIORITY.get(f.check_status, 0)
        if pri > best_pri:
            best_pri = pri
            best = f.check_status
    return best


def _aggregate_evidence_level(flags: List[ThesisCheckFlag]) -> str:
    """聚合证据等级：取所有非 insufficient flag 中最高的。"""
    if not flags:
        return EVIDENCE_NONE
    rank = {EVIDENCE_STRONG: 3, EVIDENCE_MODERATE: 2, EVIDENCE_WEAK: 1, EVIDENCE_NONE: 0}
    best = EVIDENCE_NONE
    for f in flags:
        if f.check_status == STATUS_INSUFFICIENT_DATA:
            continue
        if rank.get(f.evidence_level, 0) > rank.get(best, 0):
            best = f.evidence_level
    return best


def _build_summary(result: ThesisFactCheckResult) -> None:
    """生成人类可读的 summary 条目。"""
    summary: List[str] = []
    status_label = {
        STATUS_CONTRADICTED: "观点被事实打脸",
        STATUS_WEAKENED: "观点被事实削弱",
        STATUS_SUPPORTED: "观点被事实支持",
        STATUS_INSUFFICIENT_DATA: "事实不足以反证",
    }.get(result.thesis_check_status, result.thesis_check_status)

    summary.append(
        f"检测覆盖 {result.total_opinion_count} 条观点，"
        f"其中 {result.checked_opinion_count} 条有对应事实可比对；"
        f"整体结论：{status_label}。"
    )

    if result.contradiction_flags:
        count = len(result.contradiction_flags)
        detail = result.contradiction_flags[0]
        summary.append(
            f"⚠️ 有 {count} 条观点被半年报事实打脸，"
            f"例如：{detail.opinion_text[:50]}（{detail.metric_key}）"
            f"vs 事实 {detail.fact_text[:40]}。"
        )

    if result.weakened_flags:
        count = len(result.weakened_flags)
        detail = result.weakened_flags[0]
        summary.append(
            f"有 {count} 条观点被事实削弱，"
            f"例如：{detail.opinion_text[:50]}（{detail.metric_key}）。"
        )

    if result.supported_flags:
        count = len(result.supported_flags)
        summary.append(f"有 {count} 条观点被半年报事实支持。")

    if result.fact_period:
        summary.append(f"事实基准报告期：{result.fact_period}（data_status={result.fact_data_status}）。")

    if result.needs_tree_work_review:
        summary.append("建议回 Tree Work 复核相关研报观点，更新或标注已过时。")

    if result.priority_reminder:
        summary.append(
            "该标的研究关注度高且存在强反证，优先提醒复核。"
            "（只影响提醒优先级，不改变强动作门禁。）"
        )

    result.summary = summary[:_SUMMARY_MAX_ENTRIES]


# ── 主入口 ────────────────────────────────────────────────────────────


def check_thesis_against_facts(
    opinion_index: ResearchFactOpinionIndexResult,
    facts: HalfYearFactsQueryResult,
    *,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    research_attention_score: Optional[float] = None,
) -> ThesisFactCheckResult:
    """把旧研报观点与半年报事实做规则比对，标注反证状态。

    参数:
        opinion_index: KB-015 ``build_research_fact_opinion_index`` 的结果。
        facts: HY-003 ``query_half_year_facts`` 的结果。
        symbol: 标的代码（可选，默认从 facts/opinion_index 推断）。
        name: 标的名称（可选）。
        research_attention_score: KB-007 研究关注度分数（可选）。
            传入时用于 ``priority_reminder`` 联动。

    返回:
        :class:`ThesisFactCheckResult`。永远不会因单条观点比对失败而抛异常。

    约束:
        - 事实页 data_status 不是 fresh/conflict/stale 时，整体 insufficient_data。
        - 观点来自 opinion/forecast claim；纯 fact claim（original_filing）不参与。
        - 不输出买卖建议词。
    """
    result = ThesisFactCheckResult(
        symbol=(symbol or "").strip(),
        name=(name or "").strip(),
        research_attention_score=research_attention_score,
    )

    # 推断 symbol / name。
    if not result.symbol and isinstance(facts, HalfYearFactsQueryResult):
        result.symbol = facts.symbol
    if not result.name and isinstance(facts, HalfYearFactsQueryResult):
        result.name = facts.name
    if isinstance(opinion_index, ResearchFactOpinionIndexResult):
        if not result.symbol and opinion_index.symbols:
            result.symbol = _strip_symbol_to_code(opinion_index.symbols[0])
        if not result.name and opinion_index.names:
            result.name = opinion_index.names[0]

    # 构建事实索引。
    fact_idx = _build_fact_index(facts)
    result.fact_period = fact_idx.period
    result.fact_data_status = fact_idx.data_status

    # 事实页不可用（opinion_only / missing / 无 fresh）→ 整体 insufficient_data。
    if fact_idx.data_status is None:
        result.thesis_check_status = STATUS_INSUFFICIENT_DATA
        result.evidence_level = EVIDENCE_NONE
        result.total_opinion_count = 0
        result.checked_opinion_count = 0
        if isinstance(facts, HalfYearFactsQueryResult) and facts.status not in (
            STATUS_HAS_DATA,
        ):
            result.errors.append(
                f"半年报事实状态不可用（status={facts.status}），无法做反证检测。"
            )
        _build_summary(result)
        return result

    # 收集观点。
    opinions = _collect_opinions(opinion_index)
    risk_opinions = _collect_risk_opinions(opinion_index)
    result.total_opinion_count = len(opinions) + len(risk_opinions)

    if result.total_opinion_count == 0:
        result.thesis_check_status = STATUS_INSUFFICIENT_DATA
        result.evidence_level = EVIDENCE_NONE
        result.errors.append("无可用观点（opinion/forecast/risk）参与反证检测。")
        _build_summary(result)
        return result

    flags: List[ThesisCheckFlag] = []

    # 比对财务指标 / 分业务观点。
    for claim, rel_path in opinions:
        try:
            metric_key = _match_opinion_to_metric(claim.text)
            if metric_key in ("revenue", "net_profit", "gross_margin", "operating_cash_flow"):
                flag = _check_financial_metric_opinion(claim, rel_path, metric_key, fact_idx)
            elif metric_key == "segment":
                flag = _check_segment_opinion(claim, rel_path, fact_idx)
            else:
                # other：尝试分业务兜底。
                if _extract_segment_topic(claim.text):
                    flag = _check_segment_opinion(claim, rel_path, fact_idx)
                else:
                    # 无法匹配任何指标维度 → insufficient_data。
                    flag = ThesisCheckFlag(
                        metric_key="other",
                        opinion_text=_clip(claim.text),
                        fact_text="",
                        opinion_direction=_classify_direction(claim.text),
                        check_status=STATUS_INSUFFICIENT_DATA,
                        evidence_level=EVIDENCE_NONE,
                        reason=_clip(
                            "观点未命中已知指标维度，无法做事实反证。",
                            _REASON_MAX_CHARS,
                        ),
                        source_path=rel_path,
                        fact_source_path=fact_idx.source_path,
                    )
            flags.append(flag)
        except Exception as exc:  # pragma: no cover - 容错
            result.errors.append(f"观点比对失败（{claim.text[:30]}）: {exc!r}")

    # 比对风险观点。
    for claim, rel_path in risk_opinions:
        try:
            flag = _check_risk_opinion(claim, rel_path, fact_idx)
            flags.append(flag)
        except Exception as exc:  # pragma: no cover - 容错
            result.errors.append(f"风险观点比对失败（{claim.text[:30]}）: {exc!r}")

    # 截断 flag 数量。
    if len(flags) > _MAX_FLAGS:
        # 优先保留 contradicted > weakened > supported > insufficient。
        flags.sort(
            key=lambda f: -_STATUS_PRIORITY.get(f.check_status, 0)
        )
        flags = flags[:_MAX_FLAGS]

    result.flags = flags
    result.contradiction_flags = [f for f in flags if f.check_status == STATUS_CONTRADICTED]
    result.weakened_flags = [f for f in flags if f.check_status == STATUS_WEAKENED]
    result.supported_flags = [f for f in flags if f.check_status == STATUS_SUPPORTED]
    result.checked_opinion_count = sum(
        1 for f in flags if f.check_status != STATUS_INSUFFICIENT_DATA
    )

    # 聚合状态。
    result.thesis_check_status = _aggregate_thesis_status(flags)
    result.evidence_level = _aggregate_evidence_level(flags)

    # needs_tree_work_review：有 contradicted 或 ≥2 weakened。
    result.needs_tree_work_review = bool(
        result.contradiction_flags or len(result.weakened_flags) >= 2
    )

    # priority_reminder：高关注度 + 强反证。
    # 高关注度阈值：score >= 1.0（KB-007 fresh 单页即 1.0）。
    result.priority_reminder = bool(
        research_attention_score is not None
        and research_attention_score >= 1.0
        and result.thesis_check_status in (STATUS_CONTRADICTED, STATUS_WEAKENED)
    )

    _build_summary(result)
    return result


# ── 报告渲染 ──────────────────────────────────────────────────────────


_STATUS_LABELS = {
    STATUS_SUPPORTED: "观点被事实支持",
    STATUS_WEAKENED: "观点被事实削弱",
    STATUS_CONTRADICTED: "观点被事实打脸",
    STATUS_INSUFFICIENT_DATA: "事实不足以反证",
}

_EVIDENCE_LABELS = {
    EVIDENCE_STRONG: "强",
    EVIDENCE_MODERATE: "中",
    EVIDENCE_WEAK: "弱",
    EVIDENCE_NONE: "无",
}


def render_thesis_fact_check_report(result: ThesisFactCheckResult) -> str:
    """渲染完整的观点 vs 事实反证检测 Markdown 报告。

    只展示摘要和 flag 列表，不输出买卖建议词。状态为 insufficient_data 时
    返回最小化报告。
    """
    lines: List[str] = []
    lines.append(f"# 旧研报观点 vs 半年报事实反证检测 — {TASK_CODE}")
    lines.append("")
    lines.append(f"- 标的：`{result.symbol}`{f' {result.name}' if result.name else ''}")
    status_label = _STATUS_LABELS.get(result.thesis_check_status, result.thesis_check_status)
    evidence_label = _EVIDENCE_LABELS.get(result.evidence_level, result.evidence_level)
    lines.append(
        f"- 整体结论：**{result.thesis_check_status}**（{status_label}）"
        f" · 证据强度：{evidence_label}"
    )
    lines.append(
        f"- 检测覆盖：{result.total_opinion_count} 条观点，"
        f"{result.checked_opinion_count} 条有对应事实可比对"
    )
    if result.fact_period:
        lines.append(
            f"- 事实基准：{result.fact_period}"
            f"（data_status={result.fact_data_status}）"
        )
    if result.research_attention_score is not None:
        lines.append(f"- 研究关注度：{result.research_attention_score:.2f}")
    if result.priority_reminder:
        lines.append("- **优先提醒**：高关注度 + 强反证，建议优先复核")
    lines.append("")
    lines.append(
        "> 本检测只做证据一致性标注，不输出买卖建议。单一指标变化不扩大为"
        "逻辑破坏；每个 flag 都带证据等级。"
    )
    lines.append("")

    if result.summary:
        for s in result.summary:
            lines.append(f"- {s}")
        lines.append("")

    if not result.flags:
        lines.append("（无比对 flag）")
        return "\n".join(lines)

    # 按 contradicted > weakened > supported > insufficient 排序展示。
    sorted_flags = sorted(
        result.flags,
        key=lambda f: -_STATUS_PRIORITY.get(f.check_status, 0),
    )
    for idx, f in enumerate(sorted_flags, 1):
        s_label = _STATUS_LABELS.get(f.check_status, f.check_status)
        e_label = _EVIDENCE_LABELS.get(f.evidence_level, f.evidence_level)
        lines.append(
            f"## {idx}. [{f.check_status}] {f.metric_key} — {s_label}"
            f"（证据：{e_label}）"
        )
        lines.append("")
        lines.append(f"- 观点：{f.opinion_text}")
        if f.opinion_direction != DIR_NEUTRAL:
            lines.append(f"  - 观点方向：{f.opinion_direction}")
        if f.fact_text:
            lines.append(f"- 事实：{f.fact_text}")
            if f.fact_direction != DIR_NEUTRAL:
                lines.append(f"  - 事实方向：{f.fact_direction}")
        else:
            lines.append("- 事实：（无对应记录）")
        if f.reason:
            lines.append(f"- 说明：{f.reason}")
        if f.source_path:
            lines.append(f"- 观点来源：`{f.source_path}`")
        if f.fact_source_path:
            lines.append(f"- 事实来源：`{f.fact_source_path}`")
        lines.append("")

    return "\n".join(lines)


def render_thesis_check_inline(result: ThesisFactCheckResult) -> str:
    """渲染单行内联摘要，适合放入 HY-004 区块或本地知识摘要。

    格式：``[HY-005] 3 条观点被事实打脸，1 条被削弱，建议复核。``
    无反证时返回空串。
    """
    if not isinstance(result, ThesisFactCheckResult):
        return ""
    if result.thesis_check_status == STATUS_INSUFFICIENT_DATA:
        return ""
    parts: List[str] = []
    if result.contradiction_flags:
        parts.append(f"{len(result.contradiction_flags)} 条观点被事实打脸")
    if result.weakened_flags:
        parts.append(f"{len(result.weakened_flags)} 条被削弱")
    if result.supported_flags:
        parts.append(f"{len(result.supported_flags)} 条被支持")
    if not parts:
        return ""
    suffix = ""
    if result.needs_tree_work_review:
        suffix = "，建议回 Tree Work 复核"
    if result.priority_reminder:
        suffix += "（优先提醒）"
    return f"[{TASK_CODE}] " + "，".join(parts) + suffix + "。"


# ── 便利函数 ──────────────────────────────────────────────────────────


def suggest_report_output_path(
    docs_dir: str = "docs/knowledge_reports",
) -> str:
    import os
    from datetime import date

    today = date.today().strftime("%Y-%m-%d")
    return os.path.join(docs_dir, f"thesis_fact_check-{today}.md")


__all__ = [
    "TASK_CODE",
    "STATUS_SUPPORTED",
    "STATUS_WEAKENED",
    "STATUS_CONTRADICTED",
    "STATUS_INSUFFICIENT_DATA",
    "ALL_THESIS_STATUSES",
    "EVIDENCE_STRONG",
    "EVIDENCE_MODERATE",
    "EVIDENCE_WEAK",
    "EVIDENCE_NONE",
    "DIR_POSITIVE",
    "DIR_NEGATIVE",
    "DIR_NEUTRAL",
    "ThesisCheckFlag",
    "ThesisFactCheckResult",
    "check_thesis_against_facts",
    "render_thesis_fact_check_report",
    "render_thesis_check_inline",
    "suggest_report_output_path",
]
