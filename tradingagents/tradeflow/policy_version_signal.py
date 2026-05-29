# [S-001] policy_version_signal
"""Policy Version Signal — identify policy-driven market themes for TradeFlow.

Outputs:
- policy_tags: list of matched policy theme tags (e.g. "新质生产力", "低空经济")
- version_score: 0-30 bonus score (only when backed by raw text evidence)
- policy_evidence_refs: list of {tag, matched_text, source} for audit trail

Rules:
- No external LLM calls.
- Only keywords / regex matching against event texts and industry labels.
- version_score is 0 when no raw text evidence is found — no speculation.
- Score does NOT replace VCP/Pullback/Event strategies; it is a bonus layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]


@dataclass
class PolicyVersionResult:
    policy_tags: list[str] = field(default_factory=list)
    version_score: float = 0.0
    policy_evidence_refs: list[dict] = field(default_factory=list)


POLICY_VERSION_TOPICS: list[dict] = [
    {
        "tag": "新质生产力",
        "keywords": re.compile(r"新质生产力|新质|新动能|转型升级|高质量发展"),
        "weight": 10,
    },
    {
        "tag": "算力",
        "keywords": re.compile(r"算力|智算|智算中心|算力基础设施|东数西算|AI算力|GPU|国产算力"),
        "weight": 10,
    },
    {
        "tag": "低空经济",
        "keywords": re.compile(r"低空经济|低空|eVTOL|无人机|飞行汽车|低空飞行"),
        "weight": 10,
    },
    {
        "tag": "机器人",
        "keywords": re.compile(r"人形机器人|机器人产业|工业机器人|机器人|具身智能"),
        "weight": 10,
    },
    {
        "tag": "出海",
        "keywords": re.compile(r"出海|一带一路|海外拓展|海外收入|跨境|国际化战略"),
        "weight": 8,
    },
    {
        "tag": "中特估",
        "keywords": re.compile(r"中特估|中国特色估值|央企估值|国企价值|央国企改革"),
        "weight": 8,
    },
    {
        "tag": "国产替代",
        "keywords": re.compile(r"国产替代|自主可控|国产化率|信创|国产芯片|国产软件|自主可控"),
        "weight": 10,
    },
    {
        "tag": "并购重组",
        "keywords": re.compile(r"并购重组|重大资产重组|吸收合并|产业整合|战略重组"),
        "weight": 8,
    },
    {
        "tag": "国企改革",
        "keywords": re.compile(r"国企改革|央企改革|混改|国资|股权激励.*国企|央企.*行动"),
        "weight": 7,
    },
    {
        "tag": "半导体",
        "keywords": re.compile(r"半导体|芯片|集成电路|晶圆|光刻|EDA|封测|存储芯片"),
        "weight": 9,
    },
    {
        "tag": "新能源",
        "keywords": re.compile(r"新能源|光伏|风电|储能|氢能|碳中和|碳达峰|绿色能源"),
        "weight": 7,
    },
    {
        "tag": "人工智能",
        "keywords": re.compile(r"人工智能|AI大模型|大模型|AIGC|生成式AI|深度学习|智能驾驶"),
        "weight": 9,
    },
    {
        "tag": "数据要素",
        "keywords": re.compile(r"数据要素|数据资产|数据交易|数据确权|数字经济"),
        "weight": 8,
    },
    {
        "tag": "军工",
        "keywords": re.compile(r"国防|军工|武器装备|军用|国防科技|国防工业"),
        "weight": 7,
    },
]

MAX_POLICY_BONUS = 30.0


def detect_policy_version(
    event_texts: list[str] | None = None,
    industry_tags: list[str] | None = None,
    cfg: StrategyConfig | None = None,  # [M-004]
) -> PolicyVersionResult:
    """Detect policy version signals from event texts and industry tags.

    Args:
        event_texts: List of news/announcement title strings for this symbol.
        industry_tags: List of industry/sector label strings.

    Returns:
        PolicyVersionResult with tags, score, and evidence refs.
        Score is 0 when no raw text evidence matches any policy topic.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    texts = [t for t in (event_texts or []) if t]
    industries = [t for t in (industry_tags or []) if t]
    combined_text = "\n".join(texts)

    if not combined_text and not industries:
        return PolicyVersionResult()

    matched_tags: list[str] = []
    evidence_refs: list[dict] = []
    raw_score = 0.0

    for topic in POLICY_VERSION_TOPICS:
        tag = topic["tag"]
        kw = topic["keywords"]
        weight = topic["weight"]

        matched_in_text = False
        matched_snippets: list[str] = []

        for text in texts:
            m = kw.search(text)
            if m:
                matched_in_text = True
                snippet = text[:120]
                matched_snippets.append(snippet)

        matched_in_industry = False
        for ind in industries:
            if kw.search(ind):
                matched_in_industry = True
                matched_snippets.append(f"[行业]{ind}")

        if matched_in_text:
            matched_tags.append(tag)
            raw_score += weight
            for s in matched_snippets:
                evidence_refs.append({
                    "tag": tag,
                    "matched_text": s,
                    "source": "event_text",
                })
        elif matched_in_industry:
            matched_tags.append(tag)
            raw_score += weight * cfg.policy_industry_weight_factor  # [M-004]
            evidence_refs.append({
                "tag": tag,
                "matched_text": f"行业标签命中: {tag}",
                "source": "industry_tag",
            })

    version_score = min(raw_score, cfg.policy_max_bonus)  # [M-004]

    if not evidence_refs:
        return PolicyVersionResult()

    return PolicyVersionResult(
        policy_tags=sorted(set(matched_tags)),
        version_score=round(version_score, 2),
        policy_evidence_refs=evidence_refs,
    )
