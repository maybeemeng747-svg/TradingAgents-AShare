# [KB-014] citation_policy
"""研报/财报来源可信度分层与 citation policy。

在 KB-001~KB-013 之上，本模块定义本地知识 / TA 报告引用来源的**可信度分层**，
明确公告/财报原文、券商研报、媒体观点、用户笔记的使用边界，避免把"券商观点"
当成"公司事实"。

设计约束（对应任务 KB-014）：
  - **不抓取新研报正文**：只读取 KB-001/KB-002 已解析的 frontmatter / 章节信息，
    不向外网发请求。
  - **不把来源可信度直接转成交易动作**：``tier`` 只影响 confidence / attention，
    不改变强动作门禁（Buy/Risk Level 仍由 DECISION-001/TF-QUALITY-001 等控制）。
  - **不改 prompts**：本模块只产出结构化字段与 lint findings，由调用方决定如何
    透传给报告渲染。
  - **对缺来源或来源弱的页面降低 confidence/attention，而不是直接过滤**：
    ``unknown`` / ``media`` / ``user_note`` 仍保留在命中列表中，但
    ``confidence_weight`` 较低，并显式标注 ``OPINION_AS_FACT`` / ``WEAK_SOURCE``
    风险。

tier 定义（与任务原文一致）：
  - ``original_filing``：交易所公告、定期报告披露原文（``exchange_filing`` /
    ``fact_table`` / 上市公司半年报/年报/中报）。
  - ``official_notice``：监管/官方文件（政策原文、交易所监管函、证监会公告）。
  - ``broker_research``：券商研报、卖方观点（``broker_report``）。
  - ``media``：媒体报道、二手解读（``media``）。
  - ``user_note``：用户自填笔记 / 观察（无 ``sources``、无 ``source_type``，
    仅有 ``tags`` / 正文）。
  - ``unknown``：来源字段全缺或无法识别。

使用边界（citation policy 详见 ``docs/citation_policy.md``）：
  - ``original_filing`` / ``official_notice``：可作为**事实**进入 raw_evidence
    与 HY-005 事实反证。
  - ``broker_research`` / ``media``：**只能作为观点/线索**，不进入事实反证，
    不直接拉高 candidate tier / score。
  - ``user_note``：仅作背景记录，不进入候选加分。
  - ``unknown``：默认降权，TA 标 ``WEAK_SOURCE``，提示补来源。

使用示例::

    from tradingagents.dataflows.citation_policy import (
        classify_source_quality_tier,
        compute_tier_confidence_weight,
        render_citation_summary,
    )
    tier = classify_source_quality_tier(frontmatter)
    weight = compute_tier_confidence_weight(tier, machine_readiness="high")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.dataflows.local_knowledge_audit import _safe_str

# [KB-014] citation_policy — 本模块**不**依赖 local_knowledge_lint（避免循环导入），
# 自带 source_type 取值表与归一化逻辑。lint 的 HY-001 取值表与这里保持同步。
SOURCE_TYPE_FACT_VALUES: Tuple[str, ...] = (
    "exchange_filing",
    "fact_table",
    "management_commentary",  # 公司自述视作半事实
    "official_notice",
    "regulatory_notice",
)
SOURCE_TYPE_OPINION_VALUES: Tuple[str, ...] = (
    "broker_report",
    "media",
)
SOURCE_TYPE_ALL_VALUES: Tuple[str, ...] = SOURCE_TYPE_FACT_VALUES + SOURCE_TYPE_OPINION_VALUES

# 触发 HYF 扩展的 report_type 取值（与 lint HALF_YEAR_REPORT_TYPES 保持同步）。
HALF_YEAR_REPORT_TYPES: Tuple[str, ...] = (
    "财报分析",
    "半年报",
    "中报",
)


def _normalize_source_type_list(value: Any) -> List[str]:
    """[KB-014] 把 frontmatter ``source_type`` 字段归一为小写字符串列表。

    与 lint 的实现完全等价（避免循环导入，本模块自带一份）。
    """
    if value is None:
        return []
    if isinstance(value, str):
        items = [s.strip() for s in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(s).strip() for s in value]
    else:
        return []
    return [s.lower() for s in items if s]


# ── 常量 ──────────────────────────────────────────────────────────────

# [KB-014] citation_policy tier 取值表（与任务原文字段名严格一致）。
TIER_ORIGINAL_FILING = "original_filing"
TIER_OFFICIAL_NOTICE = "official_notice"
TIER_BROKER_RESEARCH = "broker_research"
TIER_MEDIA = "media"
TIER_USER_NOTE = "user_note"
TIER_UNKNOWN = "unknown"

SOURCE_QUALITY_TIERS: Tuple[str, ...] = (
    TIER_ORIGINAL_FILING,
    TIER_OFFICIAL_NOTICE,
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_USER_NOTE,
    TIER_UNKNOWN,
)

# source_type → tier 映射（与 HY-001 取值表对齐）。
# 事实类 source_type 升为 original_filing；观点类降到 broker_research / media。
_SOURCE_TYPE_TIER_MAP: Dict[str, str] = {
    "exchange_filing": TIER_ORIGINAL_FILING,
    "fact_table": TIER_ORIGINAL_FILING,
    "management_commentary": TIER_ORIGINAL_FILING,  # 公司口径仍属"半事实"
    "official_notice": TIER_OFFICIAL_NOTICE,
    "regulatory_notice": TIER_OFFICIAL_NOTICE,
    "broker_report": TIER_BROKER_RESEARCH,
    "media": TIER_MEDIA,
}

# report_type → tier 提示（财报/年报/中报页天然偏向 original_filing）。
# 注意：report_type 只是**弱提示**，最终 tier 由 source_type / sources 决定。
_REPORT_TYPE_FILING_TYPES: Tuple[str, ...] = (
    "财报分析",
    "半年报",
    "中报",
    "年报",
    "一季报",
    "三季报",
    "公告",
)

# sources 字段中常见的"公司公告/披露"关键词（用于无 source_type 时的兜底识别）。
_FILING_SOURCE_KEYWORDS: Tuple[str, ...] = (
    "巨潮资讯",
    "公司公告",
    "公告URL",
    "公告披露",
    "交易所披露",
    "定期报告",
)
_OFFICIAL_SOURCE_KEYWORDS: Tuple[str, ...] = (
    "证监会",
    "证券监督管理委员会",
    "交易所监管",
    "监管函",
    "问询函",
    "监管问询",
    "政策原文",
    "官方文件",
    "上交所",
    "深交所",
    "港交所",
    "交易所",
)
_BROKER_SOURCE_KEYWORDS: Tuple[str, ...] = (
    "券商",
    "研报",
    "首席",
    "证券研究",
    "证券报告",
    "研究报告",
    "深度报告",
    "行业深度",
    "公司点评",
    "年报点评",
    "半年报点评",
    "中报点评",
)
_BROKER_ORG_SOURCE_KEYWORDS: Tuple[str, ...] = (
    "中信证券",
    "华泰证券",
    "国泰君安",
    "中金公司",
    "海通证券",
    "广发证券",
    "招商证券",
    "申万宏源",
    "国信证券",
    "东方证券",
    "光大证券",
    "方正证券",
    "浙商证券",
    "天风证券",
    "民生证券",
    "东吴证券",
    "开源证券",
    "国盛证券",
    "兴业证券",
    "长江证券",
    "银河证券",
    "国金证券",
    "财通证券",
    "西部证券",
    "华西证券",
    "东北证券",
    "中泰证券",
    "德邦证券",
    "国海证券",
    "信达证券",
    "华创证券",
)
_MEDIA_SOURCE_KEYWORDS: Tuple[str, ...] = (
    "媒体",
    "财联社",
    "中国证券报",
    "证券时报",
    "证券日报",
    "新华社",
    "新华网",
    "新浪",
    "腾讯",
    "新闻",
    "报道",
    "解读",
)
_MEDIA_ORG_SOURCE_KEYWORDS: Tuple[str, ...] = (
    "媒体",
    "财联社",
    "中国证券报",
    "证券时报",
    "证券日报",
    "新华社",
    "新华网",
    "新浪",
    "腾讯",
)
_USER_NOTE_SOURCE_KEYWORDS: Tuple[str, ...] = (
    "个人观点",
    "我的",
    "笔记",
    "记录",
    "复盘",
    "自整理",
    "自选备注",
)
_BROKER_SECURITIES_EXCLUDE: Tuple[str, ...] = (
    "中国证券报",
    "证券时报",
    "证券日报",
    "证券报",
    "证券交易所",
    "证券监督管理",
    "证监会",
)

# 每页 tier 的 confidence 权重（0.0 ~ 1.0）。
# 调用方据此对 attention / local_knowledge_score 做软调节。
TIER_CONFIDENCE_WEIGHTS: Dict[str, float] = {
    TIER_ORIGINAL_FILING: 1.0,
    TIER_OFFICIAL_NOTICE: 1.0,
    TIER_BROKER_RESEARCH: 0.6,
    TIER_MEDIA: 0.3,
    TIER_USER_NOTE: 0.2,
    TIER_UNKNOWN: 0.1,
}

# tier 对应的使用边界说明（citation policy 文档与 lint finding 复用）。
TIER_USAGE_POLICY: Dict[str, str] = {
    TIER_ORIGINAL_FILING: "可作为事实进入 raw_evidence / HY-005 事实反证。",
    TIER_OFFICIAL_NOTICE: "可作为官方事实；优先级高于券商/媒体观点。",
    TIER_BROKER_RESEARCH: "仅作观点/线索，不得冒充事实进入反证或加分。",
    TIER_MEDIA: "仅作背景/线索，不得作为结论依据。",
    TIER_USER_NOTE: "仅作背景记录；不进入候选加分；建议补来源后升级。",
    TIER_UNKNOWN: "来源不可识别，TA 标 WEAK_SOURCE；建议回 Tree Work 补 source_type / sources。",
}

# 不可作为"事实"的 tier（仅作观点/线索）。
OPINION_ONLY_TIERS: Tuple[str, ...] = (
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_USER_NOTE,
    TIER_UNKNOWN,
)


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass
class CitationAssessment:
    """单页来源可信度评估结果。

    携带 tier / 命中证据 / 软权重 / 风险标签，供 lint finding、provider
    confidence 计算、KB-004 local_knowledge_score 软调节使用。

    通过 ``classify_source_quality_tier`` 构造时，``is_fact_usable`` /
    ``is_opinion_only`` / ``weak_source_risk`` / ``confidence_weight`` 会基于
    ``tier`` 自动计算（``_build_assessment`` 走该路径）。直接构造 dataclass 时，
    若未显式传入这些字段，``__post_init__`` 会按 tier 取值表兜底，保证语义一致。
    """

    tier: str = TIER_UNKNOWN
    """来源可信度分层（见 :data:`SOURCE_QUALITY_TIERS`）。"""

    confidence_weight: float = -1.0
    """软权重（0.0~1.0），调用方据此降低 attention / score。
    默认 -1.0 表示"未计算"，``__post_init__`` 会按 tier 兜底。"""

    is_fact_usable: bool = False
    """是否可作为"事实"进入 raw_evidence / HY-005 反证。"""

    is_opinion_only: bool = True
    """是否仅作观点/线索（True 表示不可冒充事实）。"""

    weak_source_risk: bool = True
    """是否缺来源或来源弱（``unknown`` / ``media`` / ``user_note``）。"""

    signals: List[str] = field(default_factory=list)
    """分类命中证据（如 ``report_type=半年报`` / ``source_type=[exchange_filing]``）。"""

    def __post_init__(self) -> None:
        # 直接构造 dataclass（未走 _build_assessment）时，按 tier 兜底计算字段，
        # 保证 ``CitationAssessment(tier='original_filing')`` 也能正确反映
        # is_fact_usable / confidence_weight。
        if self.confidence_weight < 0:
            self.confidence_weight = TIER_CONFIDENCE_WEIGHTS.get(
                self.tier, TIER_CONFIDENCE_WEIGHTS[TIER_UNKNOWN]
            )
        self.is_fact_usable = self.tier in (TIER_ORIGINAL_FILING, TIER_OFFICIAL_NOTICE)
        self.is_opinion_only = self.tier in OPINION_ONLY_TIERS
        self.weak_source_risk = self.tier in (
            TIER_UNKNOWN,
            TIER_MEDIA,
            TIER_USER_NOTE,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "confidence_weight": self.confidence_weight,
            "is_fact_usable": self.is_fact_usable,
            "is_opinion_only": self.is_opinion_only,
            "weak_source_risk": self.weak_source_risk,
            "signals": list(self.signals),
            "usage_policy": TIER_USAGE_POLICY.get(
                self.tier, TIER_USAGE_POLICY[TIER_UNKNOWN]
            ),
        }


# ── 分类逻辑 ──────────────────────────────────────────────────────────


def _has_any_keyword(text: str, keywords: Tuple[str, ...]) -> bool:
    if not text or not keywords:
        return False
    lower = text.lower()
    return any(kw and kw.lower() in lower for kw in keywords)


def _looks_like_broker_source(text: str) -> bool:
    """识别"中邮证券/中信证券"等券商名，同时排除证券媒体。"""
    if not text or "证券" not in text:
        return False
    return not _has_any_keyword(text, _BROKER_SECURITIES_EXCLUDE)


def _classify_source_item(item: str) -> Tuple[str, str]:
    """识别单条 source 的 tier。

    单条 source 内先识别显式媒体/监管/披露/券商主体，再处理"研报/新闻/报道/解读"
    这类泛词；例如"中信证券新闻点评研报"仍是券商观点，
    "证监会新闻发布会"仍是官方来源。多条 sources 会在外层取最高可信度，
    允许 ``["巨潮资讯公告", "财联社报道"]`` 升为 original_filing。
    """
    if _has_any_keyword(item, _MEDIA_ORG_SOURCE_KEYWORDS):
        return TIER_MEDIA, "sources 含媒体关键词"
    if _has_any_keyword(item, _OFFICIAL_SOURCE_KEYWORDS):
        return TIER_OFFICIAL_NOTICE, "sources 含监管/官方关键词"
    if _has_any_keyword(item, _FILING_SOURCE_KEYWORDS):
        return TIER_ORIGINAL_FILING, "sources 含公告/披露关键词"
    if _has_any_keyword(item, _BROKER_ORG_SOURCE_KEYWORDS):
        return TIER_BROKER_RESEARCH, "sources 含券商机构关键词"
    if _looks_like_broker_source(item):
        return TIER_BROKER_RESEARCH, "sources 含券商名称关键词"
    if _has_any_keyword(item, _USER_NOTE_SOURCE_KEYWORDS):
        return TIER_USER_NOTE, "sources 含个人笔记关键词"
    if _has_any_keyword(item, _BROKER_SOURCE_KEYWORDS):
        return TIER_BROKER_RESEARCH, "sources 含券商/研报关键词"
    if _has_any_keyword(item, _MEDIA_SOURCE_KEYWORDS):
        return TIER_MEDIA, "sources 含媒体关键词"
    return TIER_UNKNOWN, ""


def _classify_sources_field(sources_raw: Any) -> Tuple[str, List[str]]:
    """根据 frontmatter ``sources`` 字段（list/str）识别最佳 tier。

    返回 ``(tier, signals)``：
      - 单条 source 内先识别显式来源主体，避免泛词覆盖券商/监管来源；
      - 多条 sources 之间取最高可信度，避免官方公告被媒体/券商条目拖低；
      - 全空或未识别 → ``unknown``。
    """
    signals: List[str] = []
    if sources_raw is None:
        return TIER_UNKNOWN, signals
    if isinstance(sources_raw, str):
        stripped = sources_raw.strip()
        items = [stripped] if stripped else []
    elif isinstance(sources_raw, (list, tuple)):
        items = [
            str(s).strip()
            for s in sources_raw
            if s is not None and str(s).strip()
        ]
    else:
        items = []

    if not items:
        return TIER_UNKNOWN, signals

    best_tier = TIER_UNKNOWN
    for item in items:
        tier, signal = _classify_source_item(item)
        if signal:
            signals.append(signal)
        if _tier_rank(tier) < _tier_rank(best_tier):
            best_tier = tier

    if best_tier != TIER_UNKNOWN:
        return best_tier, signals
    # sources 非空但未识别 → 保持 unknown，让 report_type 兜底有机会判定
    # 财报/公告页；避免把 opaque URL / 本地 PDF 路径误判为券商观点。
    signals.append("sources 非空但未识别")
    return TIER_UNKNOWN, signals


def _tier_rank(tier: str) -> int:
    """tier 优先级排序：rank 越小越可信。用于多信号混合时取最高。"""
    order = {
        TIER_ORIGINAL_FILING: 0,
        TIER_OFFICIAL_NOTICE: 1,
        TIER_BROKER_RESEARCH: 2,
        TIER_MEDIA: 3,
        TIER_USER_NOTE: 4,
        TIER_UNKNOWN: 5,
    }
    return order.get(tier, 5)


def classify_source_quality_tier(frontmatter: Dict[str, Any]) -> CitationAssessment:
    """根据单页 frontmatter 评估来源可信度分层。

    判定优先级（取**最高**可信度）：
      1. ``source_type`` 含事实类（``exchange_filing`` / ``fact_table`` /
         ``management_commentary``）→ ``original_filing``。
      2. ``source_type`` 含 ``media`` 但无事实类 → ``media``。
      3. ``source_type`` 仅含 ``broker_report`` → ``broker_research``。
      4. ``sources`` 字段命中公告/券商/媒体关键词。
      5. ``report_type`` 为财报类但 sources 不可识别 → ``user_note``。
      6. 其余：``user_note``（有 sources 或 tags 但无法识别）或 ``unknown``。

    参数:
        frontmatter: KB-001/KB-002 解析出的 frontmatter dict。

    返回:
        :class:`CitationAssessment`。绝不抛异常（缺字段时返回 ``unknown``）。
    """
    if not isinstance(frontmatter, dict):
        return _build_assessment(TIER_UNKNOWN, ["frontmatter 非 dict"])

    signals: List[str] = []
    best_tier = TIER_UNKNOWN

    # 1) source_type 取值（HY-001 半结构化白名单）
    source_types = _normalize_source_type_list(frontmatter.get("source_type"))
    if source_types:
        tiers_from_st = [
            _SOURCE_TYPE_TIER_MAP.get(st, TIER_UNKNOWN) for st in source_types
        ]
        # 取最高 rank（最可信）
        best_from_st = min(tiers_from_st, key=_tier_rank)
        signals.append(f"source_type={source_types} → {best_from_st}")
        if _tier_rank(best_from_st) < _tier_rank(best_tier):
            best_tier = best_from_st

    has_unknown_only_source_type = bool(source_types) and all(
        st not in _SOURCE_TYPE_TIER_MAP for st in source_types
    )
    has_recognized_source_type = any(
        st in _SOURCE_TYPE_TIER_MAP for st in source_types
    )
    has_fact_source_type = any(st in SOURCE_TYPE_FACT_VALUES for st in source_types)

    # 2) sources 字段关键词
    sources_tier, src_signals = _classify_sources_field(frontmatter.get("sources"))
    signals.extend(src_signals)
    can_sources_upgrade = (not has_recognized_source_type) or has_fact_source_type
    if can_sources_upgrade and _tier_rank(sources_tier) < _tier_rank(best_tier):
        best_tier = sources_tier
    elif (
        not can_sources_upgrade
        and sources_tier != TIER_UNKNOWN
        and _tier_rank(sources_tier) < _tier_rank(best_tier)
    ):
        signals.append("source_type 为观点源，sources 不升权")

    # 3) report_type 提示（仅在 sources/source_type 都未给出明确 tier 时升权）。
    # 避免把"半年报 + 券商研报来源"误升为 original_filing（观点冒充事实）。
    report_type = _safe_str(frontmatter.get("report_type")) or ""
    # _safe_str 对 list 返回 None，需显式判断 sources 是否非空。
    # 注意：source_type 非空但全是拼错/未知值，不得被当成“有来源”兜底升权。
    sources_raw = frontmatter.get("sources")
    if isinstance(sources_raw, (list, tuple)):
        has_sources = any(
            bool(str(s).strip()) for s in sources_raw if s is not None
        )
    elif isinstance(sources_raw, str):
        has_sources = bool(sources_raw.strip())
    else:
        has_sources = False
    if (
        report_type in _REPORT_TYPE_FILING_TYPES
        and has_unknown_only_source_type
        and best_tier == TIER_UNKNOWN
    ):
        # source_type 有值但全是未知/拼错，说明作者试图标注来源却没落在白名单。
        # 这种场景不得被 report_type + opaque sources 抬成事实源。
        signals.append("source_type 非空但未识别，禁用 report_type 兜底")
        best_tier = TIER_USER_NOTE
    elif (
        report_type in _REPORT_TYPE_FILING_TYPES
        and has_sources
        and best_tier == TIER_UNKNOWN
    ):
        # sources 未识别出 tier，即使 report_type 是财报类也不能抬成原始财报。
        # 只有 sources/source_type 明确命中公告/交易所/定期报告等信号时，才可作为事实源。
        signals.append(f"report_type={report_type} 但 sources 未识别，禁用事实兜底")
        best_tier = TIER_USER_NOTE
    elif (
        report_type in _REPORT_TYPE_FILING_TYPES
        and not has_sources
        and best_tier == TIER_UNKNOWN
    ):
        # 财报页但完全缺来源 → 仍降级为 user_note，并显式提示弱来源风险。
        signals.append(f"report_type={report_type} 但缺 sources")
        best_tier = TIER_USER_NOTE

    # 4) 全缺 → user_note（有正文/tags）或 unknown
    if best_tier == TIER_UNKNOWN:
        tags_raw = frontmatter.get("tags")
        if isinstance(tags_raw, (list, tuple, set)):
            has_tags = any(bool(str(t).strip()) for t in tags_raw)
        else:
            has_tags = bool(_safe_str(tags_raw))
        has_title = bool(_safe_str(frontmatter.get("title")))
        if has_tags or has_title:
            best_tier = TIER_USER_NOTE
            signals.append("仅有 tags/title，无来源 → user_note")
        else:
            signals.append("frontmatter 完全缺来源字段 → unknown")

    return _build_assessment(best_tier, signals)


def _build_assessment(tier: str, signals: List[str]) -> CitationAssessment:
    """根据 tier 与 signals 组装 CitationAssessment（统一计算 is_fact_usable 等）。"""
    weight = TIER_CONFIDENCE_WEIGHTS.get(tier, TIER_CONFIDENCE_WEIGHTS[TIER_UNKNOWN])
    is_fact_usable = tier in (TIER_ORIGINAL_FILING, TIER_OFFICIAL_NOTICE)
    is_opinion_only = tier in OPINION_ONLY_TIERS
    weak_source_risk = tier in (
        TIER_UNKNOWN,
        TIER_MEDIA,
        TIER_USER_NOTE,
    )
    return CitationAssessment(
        tier=tier,
        confidence_weight=weight,
        is_fact_usable=is_fact_usable,
        is_opinion_only=is_opinion_only,
        weak_source_risk=weak_source_risk,
        signals=signals,
    )


# ── 权重 / 降权 ──────────────────────────────────────────────────────


def compute_tier_confidence_weight(
    tier: str,
    machine_readiness: str = "high",
    *,
    is_stale: bool = False,
    is_low_confidence: bool = False,
    is_to_be_supplemented: bool = False,
) -> float:
    """综合 tier 与 KB-001 readiness 计算 confidence 权重（0.0~1.0）。

    规则：
      - 基础权重 = :data:`TIER_CONFIDENCE_WEIGHTS` 中该 tier 的权重。
      - 待补充/低置信/stale 一律再乘 0.3，体现"来源虽强但页面本身不可信"。
      - machine_readiness=medium 再乘 0.7；low 再乘 0.3。
      - 永不低于 0.0，不超过 1.0。
    """
    base = TIER_CONFIDENCE_WEIGHTS.get(tier, TIER_CONFIDENCE_WEIGHTS[TIER_UNKNOWN])
    multiplier = 1.0
    if is_to_be_supplemented or is_low_confidence:
        multiplier *= 0.3
    if is_stale:
        multiplier *= 0.3
    if machine_readiness == "medium":
        multiplier *= 0.7
    elif machine_readiness == "low":
        multiplier *= 0.3
    weight = base * multiplier
    return round(max(0.0, min(1.0, weight)), 3)


def apply_tier_to_confidence(
    base_confidence: str,
    assessment: CitationAssessment,
) -> str:
    """根据 tier 把 high/medium/low confidence 软降级。

    - ``original_filing`` / ``official_notice``：保持原 confidence。
    - ``broker_research``：high → medium（观点不应等同事实）。
    - ``media`` / ``user_note`` / ``unknown``：high/medium → low。
    """
    if not isinstance(assessment, CitationAssessment):
        return base_confidence
    tier = assessment.tier
    if tier in (TIER_ORIGINAL_FILING, TIER_OFFICIAL_NOTICE):
        return base_confidence
    if tier == TIER_BROKER_RESEARCH:
        if base_confidence == "high":
            return "medium"
        return base_confidence
    # media / user_note / unknown
    if base_confidence in ("high", "medium"):
        return "low"
    return base_confidence


# ── 报告渲染 ──────────────────────────────────────────────────────────


def render_citation_summary(assessment: CitationAssessment) -> str:
    """渲染一句话 citation 摘要，用于报告/前端透出。

    必须包含 tier + 使用边界；不得包含买卖建议词。
    """
    if not isinstance(assessment, CitationAssessment):
        return "来源未知（unknown）：建议回 Tree Work 补 source_type / sources。"
    tier = assessment.tier
    policy = TIER_USAGE_POLICY.get(tier, TIER_USAGE_POLICY[TIER_UNKNOWN])
    weight = assessment.confidence_weight
    fact_tag = "事实可用" if assessment.is_fact_usable else "仅观点/线索"
    weak_tag = " · 弱来源" if assessment.weak_source_risk else ""
    return f"来源层级：{tier}（{fact_tag}{weak_tag}，权重 {weight:.2f}）— {policy}"


def render_tier_table() -> str:
    """渲染 tier 取值表 Markdown，供文档与报告复用。"""
    lines: List[str] = []
    lines.append("| tier | confidence_weight | 可作为事实 | 使用边界 |")
    lines.append("|------|-------------------|-----------|----------|")
    for tier in SOURCE_QUALITY_TIERS:
        weight = TIER_CONFIDENCE_WEIGHTS[tier]
        fact = "✅" if tier in (TIER_ORIGINAL_FILING, TIER_OFFICIAL_NOTICE) else "❌"
        policy = TIER_USAGE_POLICY.get(tier, "")
        lines.append(f"| `{tier}` | {weight:.2f} | {fact} | {policy} |")
    return "\n".join(lines)


__all__ = [
    "TIER_ORIGINAL_FILING",
    "TIER_OFFICIAL_NOTICE",
    "TIER_BROKER_RESEARCH",
    "TIER_MEDIA",
    "TIER_USER_NOTE",
    "TIER_UNKNOWN",
    "SOURCE_QUALITY_TIERS",
    "TIER_CONFIDENCE_WEIGHTS",
    "TIER_USAGE_POLICY",
    "OPINION_ONLY_TIERS",
    "CitationAssessment",
    "classify_source_quality_tier",
    "compute_tier_confidence_weight",
    "apply_tier_to_confidence",
    "render_citation_summary",
    "render_tier_table",
]
