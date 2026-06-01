# [H-003] mandate_beneficiary_map
"""Industry Mandate Map — map policy topics to industry chain links and company roles.

Maps mandate topics to specific supply-chain / industry-chain segments,
and classifies a company's role within that chain based on event evidence.

Components:
- IndustryChainLink: one segment in a topic's industry chain.
- INDUSTRY_CHAIN_MAP: static topic → chain mapping (maintainable).
- CompanyRole enum: LEADER / CORE_SUPPLIER / INFRA_PROVIDER /
  APPLICATION_SCENE / PERIPHERAL / CONCEPT_ONLY / UNKNOWN.
- BeneficiaryPathResult: output of beneficiary path analysis.
- classify_company_role(): infer role from event title + tags.
- compute_beneficiary_path(): build beneficiary path for a topic + signals.
- compute_beneficiary_paths_for_signals(): batch compute across all signals.

Constraints:
- No LLM calls.
- Does NOT claim a company will necessarily benefit — only outputs path + evidence strength.
- No changes to tradingagents/prompts/.
- No company evidence → UNKNOWN / CONCEPT_ONLY, never high priority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from tradingagents.tradeflow.mandate_signal import MandateSignal


class CompanyRole(Enum):
    LEADER = "LEADER"
    CORE_SUPPLIER = "CORE_SUPPLIER"
    INFRA_PROVIDER = "INFRA_PROVIDER"
    APPLICATION_SCENE = "APPLICATION_SCENE"
    PERIPHERAL = "PERIPHERAL"
    CONCEPT_ONLY = "CONCEPT_ONLY"
    UNKNOWN = "UNKNOWN"


@dataclass
class IndustryChainLink:
    segment: str
    keywords: list[str]
    roles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "segment": self.segment,
            "keywords": self.keywords,
            "roles": self.roles,
        }


_INDUSTRY_CHAIN_MAP: dict[str, list[IndustryChainLink]] = {
    "低空经济": [
        IndustryChainLink("整机", ["整机", "飞行器", "eVTOL", "无人机整机", "载人飞行"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("空管", ["空管", "空中交通", "低空监视", "通信导航", "飞行管理"], ["INFRA_PROVIDER"]),
        IndustryChainLink("运营", ["运营", "低空运营", "通航运营", "飞行服务"], ["APPLICATION_SCENE"]),
        IndustryChainLink("材料", ["碳纤维", "复合材料", "航空材料", "轻量化材料"], ["CORE_SUPPLIER"]),
        IndustryChainLink("导航", ["北斗", "导航", "GNSS", "惯性导航", "定位"], ["INFRA_PROVIDER"]),
        IndustryChainLink("基础设施", ["起降场", "停机坪", "低空基础设施", "机场建设"], ["INFRA_PROVIDER"]),
        IndustryChainLink("动力系统", ["电池", "电机", "电驱", "动力系统", "推进系统"], ["CORE_SUPPLIER"]),
    ],
    "机器人": [
        IndustryChainLink("减速器", ["减速器", "RV减速器", "谐波减速器", "行星减速器"], ["CORE_SUPPLIER"]),
        IndustryChainLink("伺服", ["伺服电机", "伺服驱动", "伺服系统"], ["CORE_SUPPLIER"]),
        IndustryChainLink("控制器", ["控制器", "运动控制", "控制系统"], ["CORE_SUPPLIER"]),
        IndustryChainLink("本体", ["本体", "机器人本体", "工业机器人", "人形机器人"], ["LEADER"]),
        IndustryChainLink("传感器", ["传感器", "力传感器", "视觉传感器", "编码器", "陀螺仪"], ["CORE_SUPPLIER"]),
        IndustryChainLink("应用场景", ["系统集成", "自动化产线", "智能制造", "柔性制造"], ["APPLICATION_SCENE"]),
        IndustryChainLink("零部件", ["轴承", "丝杠", "导轨", "联轴器"], ["PERIPHERAL"]),
    ],
    "算力": [
        IndustryChainLink("芯片", ["芯片", "GPU", "AI芯片", "NPU", "算力芯片", "国产芯片"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("服务器", ["服务器", "AI服务器", "计算节点", "算力集群"], ["CORE_SUPPLIER"]),
        IndustryChainLink("液冷", ["液冷", "散热", "温控", "冷板", "液冷服务器"], ["CORE_SUPPLIER"]),
        IndustryChainLink("IDC", ["IDC", "数据中心", "算力中心", "智算中心"], ["INFRA_PROVIDER"]),
        IndustryChainLink("光模块", ["光模块", "光通信", "光纤", "光器件", "硅光"], ["CORE_SUPPLIER"]),
        IndustryChainLink("电源", ["电源", "UPS", "配电", "供电系统"], ["PERIPHERAL"]),
        IndustryChainLink("软件平台", ["算力调度", "云平台", "虚拟化", "容器", "分布式"], ["APPLICATION_SCENE"]),
    ],
    "军工": [
        IndustryChainLink("航空", ["航空", "战斗机", "运输机", "直升机", "军机"], ["LEADER"]),
        IndustryChainLink("导弹", ["导弹", "火箭弹", "精确制导", "弹药"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("舰船", ["舰船", "驱逐舰", "护卫舰", "航母", "潜艇"], ["LEADER"]),
        IndustryChainLink("发动机", ["航空发动机", "动力", "涡轮", "推进"], ["CORE_SUPPLIER"]),
        IndustryChainLink("电子信息化", ["雷达", "通信", "电子对抗", "信息化", "指控系统"], ["CORE_SUPPLIER"]),
        IndustryChainLink("材料", ["钛合金", "高温合金", "碳纤维", "隐身材料"], ["CORE_SUPPLIER"]),
        IndustryChainLink("零部件", ["紧固件", "连接器", "元器件", "线缆"], ["PERIPHERAL"]),
    ],
    "半导体": [
        IndustryChainLink("设计", ["设计", "IC设计", "芯片设计", "Fabless"], ["LEADER"]),
        IndustryChainLink("制造", ["制造", "晶圆代工", "Foundry", "制程", "工艺节点"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("封测", ["封装", "测试", "封测", "先进封装", "Chiplet"], ["CORE_SUPPLIER"]),
        IndustryChainLink("设备", ["光刻机", "刻蚀", "薄膜", "清洗设备", "检测设备"], ["CORE_SUPPLIER"]),
        IndustryChainLink("材料", ["光刻胶", "硅片", "靶材", "电子气体", "CMP"], ["CORE_SUPPLIER"]),
        IndustryChainLink("EDA/IP", ["EDA", "IP", "设计工具", "仿真"], ["CORE_SUPPLIER"]),
    ],
    "AI应用": [
        IndustryChainLink("大模型", ["大模型", "LLM", "GPT", "通用模型", "基座模型"], ["LEADER"]),
        IndustryChainLink("AIGC", ["AIGC", "生成式", "内容生成", "AI写作", "AI绘画"], ["APPLICATION_SCENE"]),
        IndustryChainLink("行业应用", ["AI+医疗", "AI+教育", "AI+金融", "AI+法律", "AI+办公"], ["APPLICATION_SCENE"]),
        IndustryChainLink("智能体", ["Agent", "智能体", "AI助手", "Copilot"], ["APPLICATION_SCENE"]),
        IndustryChainLink("算力基础设施", ["算力", "智算", "推理", "训练"], ["INFRA_PROVIDER"]),
    ],
    "新质生产力": [
        IndustryChainLink("高端装备", ["高端装备", "数控机床", "工业母机", "高端制造"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("新材料", ["新材料", "先进材料", "功能材料", "结构材料"], ["CORE_SUPPLIER"]),
        IndustryChainLink("新能源", ["新能源", "光伏", "风电", "储能", "氢能"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("数字经济", ["数字经济", "数字化转型", "智能制造"], ["APPLICATION_SCENE"]),
        IndustryChainLink("生物医药", ["生物医药", "创新药", "医疗器械", "合成生物"], ["LEADER"]),
    ],
    "数据要素": [
        IndustryChainLink("数据采集", ["数据采集", "传感器", "物联网", "数据源"], ["CORE_SUPPLIER"]),
        IndustryChainLink("数据存储", ["数据存储", "数据库", "云存储", "分布式存储"], ["INFRA_PROVIDER"]),
        IndustryChainLink("数据处理", ["数据处理", "数据分析", "大数据", "清洗", "标注"], ["CORE_SUPPLIER"]),
        IndustryChainLink("数据交易", ["数据交易", "数据交易所", "数据确权", "数据定价"], ["APPLICATION_SCENE"]),
        IndustryChainLink("数据安全", ["数据安全", "隐私计算", "加密", "脱敏"], ["CORE_SUPPLIER"]),
    ],
    "国产替代": [
        IndustryChainLink("操作系统", ["操作系统", "国产OS", "Linux", "麒麟", "统信"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("数据库", ["数据库", "国产数据库", "分布式数据库"], ["CORE_SUPPLIER"]),
        IndustryChainLink("中间件", ["中间件", "应用服务器"], ["CORE_SUPPLIER"]),
        IndustryChainLink("芯片替代", ["国产芯片", "自主可控芯片", "信创芯片"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("信创整体", ["信创", "国产化", "自主可控", "信息技术应用创新"], ["APPLICATION_SCENE"]),
    ],
    "并购重组": [
        IndustryChainLink("战略并购", ["战略并购", "产业并购", "横向并购", "纵向并购"], ["LEADER"]),
        IndustryChainLink("重组整合", ["重组整合", "资产注入", "业务整合"], ["LEADER"]),
        IndustryChainLink("配套服务", ["评估", "审计", "法律", "财务顾问"], ["PERIPHERAL"]),
    ],
    "国企改革": [
        IndustryChainLink("央企整合", ["央企整合", "央企合并", "央企重组"], ["LEADER"]),
        IndustryChainLink("混改", ["混改", "混合所有制", "引入战投"], ["LEADER", "CORE_SUPPLIER"]),
        IndustryChainLink("股权激励", ["股权激励", "员工持股", "激励计划"], ["APPLICATION_SCENE"]),
        IndustryChainLink("中特估", ["中特估", "央企估值", "价值重估"], ["APPLICATION_SCENE"]),
    ],
    "出海": [
        IndustryChainLink("海外布局", ["海外布局", "海外扩张", "国际市场", "海外建厂"], ["LEADER"]),
        IndustryChainLink("出口贸易", ["出口", "外贸", "跨境电商", "出口订单"], ["CORE_SUPPLIER"]),
        IndustryChainLink("一带一路", ["一带一路", "国际工程", "海外工程"], ["APPLICATION_SCENE"]),
    ],
    "中特估": [
        IndustryChainLink("央企龙头", ["央企龙头", "央企核心", "央企白马"], ["LEADER"]),
        IndustryChainLink("价值重估", ["价值重估", "估值修复", "分红提升"], ["APPLICATION_SCENE"]),
        IndustryChainLink("央企改革", ["央企改革", "央企重组", "国资"], ["CORE_SUPPLIER"]),
    ],
}

_ROLE_KEYWORD_PATTERNS: list[tuple[re.Pattern, CompanyRole]] = [
    (re.compile(r"核心供应商|主要供应商|产业链核心|核心零部件"), CompanyRole.CORE_SUPPLIER),
    (re.compile(r"供应商|供货"), CompanyRole.CORE_SUPPLIER),
    (re.compile(r"龙头|核心标的|领军企业|龙头企业|领军|领先"), CompanyRole.LEADER),
    (re.compile(r"基础设施提供商|IDC|数据中心|算力中心"), CompanyRole.INFRA_PROVIDER),
    (re.compile(r"基础设施|平台"), CompanyRole.INFRA_PROVIDER),
    (re.compile(r"应用场景|系统集成|下游应用|场景落地|应用端|解决方案"), CompanyRole.APPLICATION_SCENE),
    (re.compile(r"涉足|布局|关注|探索|跟踪|有望|可能|研究|潜力"), CompanyRole.CONCEPT_ONLY),
]

_WEAK_ROLE_INDICATORS = re.compile(
    r"涉足|布局|关注|探索|跟踪|有望进入|可能受益|研究|潜力|"
    r"概念股|题材|板块|概念|相关|或受益"
)


@dataclass
class BeneficiaryPathResult:
    beneficiary_path: list[str] = field(default_factory=list)
    company_role: str = CompanyRole.UNKNOWN.value
    mandate_topic: str = ""
    mandate_evidence_refs: list[dict] = field(default_factory=list)
    path_confidence: float = 0.0
    path_reasons: list[str] = field(default_factory=list)
    chain_segments_matched: list[str] = field(default_factory=list)
    has_company_evidence: bool = False

    def to_dict(self) -> dict:
        return {
            "beneficiary_path": self.beneficiary_path,
            "company_role": self.company_role,
            "mandate_topic": self.mandate_topic,
            "mandate_evidence_refs": self.mandate_evidence_refs,
            "path_confidence": round(self.path_confidence, 3),
            "path_reasons": self.path_reasons,
            "chain_segments_matched": self.chain_segments_matched,
            "has_company_evidence": self.has_company_evidence,
        }


def get_industry_chain(topic: str) -> list[IndustryChainLink]:
    return _INDUSTRY_CHAIN_MAP.get(topic, [])


def get_all_topics() -> list[str]:
    return sorted(_INDUSTRY_CHAIN_MAP.keys())


def classify_company_role(title: str, tags: Optional[list[str]] = None) -> CompanyRole:
    if not title and not tags:
        return CompanyRole.UNKNOWN

    text = title or ""
    if tags:
        text = f"{text} {' '.join(tags)}"

    for pattern, role in _ROLE_KEYWORD_PATTERNS:
        if pattern.search(text):
            return role

    return CompanyRole.UNKNOWN


def _match_chain_segments(
    topic: str,
    signals: list[MandateSignal],
) -> tuple[list[str], list[str]]:
    chain = get_industry_chain(topic)
    if not chain or not signals:
        return [], []

    matched_segments: list[str] = []
    matched_reasons: list[str] = []
    all_text = " ".join(
        f"{sig.title} {sig.evidence_text} {' '.join(sig.industry_tags)} {' '.join(sig.policy_tags)}"
        for sig in signals
        if sig.title or sig.evidence_text
    )

    for link in chain:
        for kw in link.keywords:
            if kw in all_text:
                if link.segment not in matched_segments:
                    matched_segments.append(link.segment)
                    matched_reasons.append(f"matched_segment:{link.segment}(keyword:{kw})")
                break

    return matched_segments, matched_reasons


def _infer_role_from_segments(
    segments: list[str],
    topic: str,
    signals: list[MandateSignal],
) -> CompanyRole:
    chain = get_industry_chain(topic)
    if not segments or not chain:
        return CompanyRole.UNKNOWN

    segment_set = set(segments)
    role_scores: dict[CompanyRole, float] = {}

    for link in chain:
        if link.segment in segment_set:
            for role_str in link.roles:
                try:
                    role = CompanyRole(role_str)
                    role_scores[role] = role_scores.get(role, 0.0) + 1.0
                except ValueError:
                    pass

    if role_scores:
        best_role = max(role_scores, key=lambda r: role_scores[r])
        if best_role in (CompanyRole.LEADER, CompanyRole.CORE_SUPPLIER):
            has_strong_evidence = any(
                sig.confidence >= 0.5
                and sig.source_level not in ("MEDIA",)
                for sig in signals
            )
            if not has_strong_evidence:
                return CompanyRole.PERIPHERAL
        return best_role

    return CompanyRole.UNKNOWN


def _collect_path_evidence_refs(
    signals: list[MandateSignal],
    segments: list[str],
) -> list[dict]:
    refs: list[dict] = []
    seen: set[str] = set()
    for sig in signals:
        key = f"{sig.title}|{sig.date}|{sig.source}"
        if key in seen or not sig.title:
            continue
        seen.add(key)
        ref: dict = {
            "title": sig.title,
            "source": sig.source,
            "source_level": sig.source_level,
            "date": sig.date,
            "confidence": round(sig.confidence, 3),
        }
        if segments:
            ref["matched_segments"] = segments
        if sig.evidence_url:
            ref["evidence_url"] = sig.evidence_url
        refs.append(ref)
    return refs


def compute_beneficiary_path(
    topic: str,
    signals: list[MandateSignal],
    symbol: str = "",
) -> BeneficiaryPathResult:
    if not topic or not signals:
        return BeneficiaryPathResult(
            mandate_topic=topic,
            company_role=CompanyRole.UNKNOWN.value,
            path_reasons=["no_topic" if not topic else "no_signals"],
        )

    has_company_evidence = False
    if symbol:
        for sig in signals:
            if sig.symbol == symbol and sig.confidence >= 0.3:
                has_company_evidence = True
                break
    if not has_company_evidence:
        for sig in signals:
            if sig.source_level == "COMPANY_NOTICE" and sig.confidence >= 0.3:
                has_company_evidence = True
                break

    segments, seg_reasons = _match_chain_segments(topic, signals)

    inferred_role = _infer_role_from_segments(segments, topic, signals)

    all_text = " ".join(
        f"{sig.title} {sig.evidence_text} {' '.join(sig.industry_tags)}"
        for sig in signals
        if sig.title or sig.evidence_text
    )
    direct_role = classify_company_role(all_text)

    if not has_company_evidence:
        if direct_role == CompanyRole.CONCEPT_ONLY:
            inferred_role = CompanyRole.CONCEPT_ONLY
        elif inferred_role != CompanyRole.UNKNOWN:
            inferred_role = CompanyRole.CONCEPT_ONLY
    else:
        if direct_role == CompanyRole.CONCEPT_ONLY:
            inferred_role = CompanyRole.CONCEPT_ONLY

    evidence_refs = _collect_path_evidence_refs(signals, segments)

    path_confidence = 0.0
    if segments:
        path_confidence += len(segments) * 0.15
    if has_company_evidence:
        path_confidence += 0.3
    avg_conf = sum(s.confidence for s in signals) / len(signals) if signals else 0.0
    path_confidence += avg_conf * 0.25
    path_confidence = min(path_confidence, 1.0)

    if not has_company_evidence:
        path_confidence = min(path_confidence, 0.4)

    reasons = list(seg_reasons)
    if has_company_evidence:
        reasons.append("has_company_level_evidence")
    else:
        reasons.append("no_company_evidence:role_may_be_concept_only")
    reasons.append(f"inferred_role:{inferred_role.value}")
    if segments:
        reasons.append(f"chain_segments:[{', '.join(segments)}]")

    return BeneficiaryPathResult(
        beneficiary_path=segments,
        company_role=inferred_role.value,
        mandate_topic=topic,
        mandate_evidence_refs=evidence_refs,
        path_confidence=round(path_confidence, 3),
        path_reasons=reasons,
        chain_segments_matched=segments,
        has_company_evidence=has_company_evidence,
    )


def compute_beneficiary_paths_for_signals(
    signals: list[MandateSignal],
    topics: Optional[list[str]] = None,
) -> dict[str, BeneficiaryPathResult]:
    if not signals:
        return {}

    topic_signals: dict[str, list[MandateSignal]] = {}
    for sig in signals:
        sig_topics = topics or []
        if sig.topic and sig.topic not in sig_topics:
            sig_topics = [sig.topic] + sig_topics
        if not sig_topics:
            from tradingagents.tradeflow.mandate_score import match_topics
            matched = match_topics(f"{sig.title} {' '.join(sig.policy_tags)}")
            sig_topics = matched

        for t in sig_topics:
            topic_signals.setdefault(t, []).append(sig)

    by_symbol: dict[str, list[MandateSignal]] = {}
    for t, sigs in topic_signals.items():
        for sig in sigs:
            if sig.symbol:
                by_symbol.setdefault(sig.symbol, []).append(sig)

    results: dict[str, BeneficiaryPathResult] = {}
    for t, sigs in topic_signals.items():
        symbols_in_topic = list(set(sig.symbol for sig in sigs if sig.symbol))
        if len(symbols_in_topic) == 1:
            results[t] = compute_beneficiary_path(t, sigs, symbol=symbols_in_topic[0])
        elif len(symbols_in_topic) > 1:
            for sym in symbols_in_topic:
                sym_sigs = [s for s in sigs if s.symbol == sym]
                key = f"{t}:{sym}"
                results[key] = compute_beneficiary_path(t, sym_sigs, symbol=sym)
        else:
            results[t] = compute_beneficiary_path(t, sigs)

    return results
