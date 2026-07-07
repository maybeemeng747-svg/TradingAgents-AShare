# [KB-013] half_year_fixture_baseline
"""半年报 fixture 样本集与契约回放基线（KB-013）。

为 HY 系列任务（HY-001 / HY-003 / HY-005 / HY-008）提供**不依赖真实知识库**的、
字段 / 状态 / 失败路径全部固定的半年报样本，可被多个测试文件 ``import`` 复用。

设计原则
--------
- 每个样本都是一段独立的 Markdown 字符串，可写入 ``tmp_path`` 形成微型知识库。
- 字段命名严格遵循 ``docs/local_knowledge_contract.md``（契约 ``kb-002-v1`` + HY-001 扩展）：
  - ``symbols``：``<6位代码>.<交易所> <简称>``，例如 ``000977.SZ 浪潮信息``；
  - ``source_type``：取自 ``SOURCE_TYPE_FACT_VALUES`` ∪ ``SOURCE_TYPE_OPINION_VALUES``；
  - ``financial_period``：优先 ``YYYYH1`` / ``YYYYH2``（也兼容 ``YYYY中报`` / ``FY26Q1``）；
  - ``disclosure_date``：``YYYY-MM-DD``。
- 不包含长篇研报 / 财报原文（受 KB-003 ``_SUMMARY_MAX_CHARS=200`` 约束）。
- 不调用 live LLM，不访问 ``~/Documents/knowledge``，全部在 ``tmp_path`` 下运行。
- 每个样本附带一份 :class:`HalfYearFixtureSpec`，预先固定 KB-002 lint 的预期结果，
  作为后续 HY 任务回放的“黄金基线”。

复用方式
--------
::

    from tests.half_year_fixtures import (
        FIXTURE_SPECS,
        FIXTURES_BY_NAME,
        build_half_year_fixture_kb,
    )

    def test_something(tmp_path):
        root = build_half_year_fixture_kb(tmp_path)           # 写入全部样本
        root = build_half_year_fixture_kb(tmp_path, include=["qualified"])  # 只写指定样本
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from tradingagents.dataflows.local_knowledge_audit import (
    INDEX_MD,
    INVESTMENT_SUBDIR,
    LOG_MD,
)
from tradingagents.dataflows.local_knowledge_lint import (
    HALF_YEAR_REPORT_TYPES,
    SOURCE_TYPE_ALL_VALUES,
    SOURCE_TYPE_FACT_VALUES,
    SOURCE_TYPE_OPINION_VALUES,
)

# ── 字段命名规约（供 baseline 文档与后续 HY 任务对齐）──────────────────
# [KB-013] 统一 symbol / name / source_type / financial_period 的字段命名。

#: symbols / name 取值格式：``<6位代码>.<交易所> <简称>``（A 股 SH/SZ/BJ，港股 HK，美股 US）。
SYMBOL_FORMAT = "<code>.<exchange> <name>"
SYMBOL_EXAMPLES = (
    "000977.SZ 浪潮信息",
    "603296.SH 华勤技术",
    "600519.SH 贵州茅台",
    "DELL.US Dell",
)

#: source_type 取值表（与 HY-001 契约一致）。
SOURCE_TYPE_FACT_VALUES_TUPLE = SOURCE_TYPE_FACT_VALUES          # 事实类
SOURCE_TYPE_OPINION_VALUES_TUPLE = SOURCE_TYPE_OPINION_VALUES    # 观点类
SOURCE_TYPE_ALL_VALUES_TUPLE = SOURCE_TYPE_ALL_VALUES            # 全集

#: financial_period 规范格式（优先 YYYYH1 / YYYYH2）。
FINANCIAL_PERIOD_PREFERRED = ("YYYYH1", "YYYYH2")
FINANCIAL_PERIOD_EXAMPLES = ("2025H1", "2025H2", "2025中报", "FY26Q1")

#: 触发 HYF 扩展的 report_type 取值。
HALF_YEAR_REPORT_TYPES_TUPLE = HALF_YEAR_REPORT_TYPES


# ── fixture 页面文本 ─────────────────────────────────────────────────


# 1. 合格半年报 — 满足 KB-002 通用契约 + HY-001 全部字段 → high readiness，无 finding。
#    样本公司：000977.SZ 浪潮信息（与 HY-001 的华勤技术样本互补，便于 HY-003 多 symbol 查询）。
QUALIFIED_HALF_YEAR_PAGE = """---
title: 浪潮信息000977-2025H1半年报
created: 2026-08-29
updated: 2026-08-29
sources:
  - "[[../../raw/2026-08-29-浪潮信息-2025H1.md|公司公告-2025H1]]"
tags: [浪潮信息, 半年报, 2025H1]
related: [[investment/浪潮信息000977-AI服务器放量]]
symbols: ["000977.SZ 浪潮信息"]
themes: [AI服务器]
industry_chain_roles: [AI服务器整机]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
# ── HY-001 半年报扩展字段 ──
financial_period: 2025H1
disclosure_date: 2026-08-29
source_type: [exchange_filing, fact_table, management_commentary]
financial_facts:
  - 营收 420.4亿 (+60.0% YoY)
  - 归母净利 12.5亿 (+45.2% YoY)
segment_facts:
  - AI服务器营收占比提升
management_commentary:
  - 上调全年AI服务器出货指引
forward_guidance:
  - 下半年毛利率企稳
risk_factors: [上游GPU供应, 客户集中度]
source_links:
  - 巨潮资讯 <公告URL>
---

# 浪潮信息（000977）— 2025H1 财报

## 一句话总结

2025H1 营收 420.4亿，同比 +60.0%，AI服务器放量。

## 投资逻辑

- AI服务器整机放量，营收高增。

## 风险提示

- 上游GPU供应紧张。
- 客户集中度风险。

## 原始资料

- 公司公告。
"""


# 2. 缺报告期 — report_type=半年报 但缺 financial_period → HYF-001 error。
#    其余 HY 字段齐全，单点失败便于回归。
MISSING_FINANCIAL_PERIOD_PAGE = """---
title: 华勤技术603296-缺报告期半年报
created: 2026-08-30
updated: 2026-08-30
sources:
  - "[[../../raw/2026-08-30-华勤技术.md|公司公告]]"
tags: [华勤技术, 半年报]
related: [[investment/华勤技术603296-AI服务器]]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器]
industry_chain_roles: [AI服务器ODM]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
# ── HY-001 半年报扩展字段（缺 financial_period）──
disclosure_date: 2026-08-30
source_type: [exchange_filing, fact_table]
financial_facts:
  - 营收 150.2亿 (+30.1% YoY)
risk_factors: [客户集中度]
source_links:
  - 巨潮资讯 <公告URL>
---

# 华勤技术（603296）— 半年报（缺报告期）

## 一句话总结

营收同比增长，AI服务器放量。

## 投资逻辑

- AI服务器增长。

## 风险提示

- 客户集中度风险。

## 原始资料

- 公司公告。
"""


# 3. 事实/观点混用 — source_type 同时含事实类与观点类 → 不触发 HYF-007（非“全是观点”）。
#    该样本为 HY-005 / HY-014 提供基线：观点部分须降权，不得冒充事实。
FACT_OPINION_MIX_PAGE = """---
title: 海康威视002415-2025H1半年报（事实/观点混用）
created: 2026-08-28
updated: 2026-08-28
sources:
  - "[[../../raw/2026-08-28-海康威视-2025H1.md|公司公告]]"
  - 某券商中期策略
tags: [海康威视, 半年报, 2025H1]
related: [[investment/海康威视002415-AI视觉]]
symbols: ["002415.SZ 海康威视"]
themes: [AI视觉, 安防]
industry_chain_roles: [AI视觉整机]
report_type: 半年报
evidence_level: B
valid_until: 2099-12-31
source_quality: 中
stale_risk: 低
# ── HY-001 半年报扩展字段 ──
financial_period: 2025H1
disclosure_date: 2026-08-28
source_type: [exchange_filing, broker_report]
financial_facts:
  - 营收 430.0亿 (+12.0% YoY)（公告口径）
management_commentary:
  - 券商观点：下半年海外需求复苏（观点，非事实）
forward_guidance:
  - 公司指引：创新业务占比提升
risk_factors: [海外政策, 汇率]
source_links:
  - 巨潮资讯 <公告URL>
  - 某券商中期策略 <研报URL>
---

# 海康威视（002415）— 2025H1 财报（事实/观点混用）

## 一句话总结

公告营收同比 +12.0%，券商对下半年海外需求持乐观观点。

## 投资逻辑

- 公告口径营收增长。
- 券商观点：海外需求复苏（标注为观点）。

## 风险提示

- 海外政策风险。
- 汇率波动。

## 原始资料

- 公司公告。
- 某券商中期策略。
"""


# 4. 过期 — valid_until 已过期 → STALE-002 warning + is_stale=True，readiness 压到 medium。
#    HY 字段齐全（仍跑 HYF 规则），但知识时效已失，TA 命中后须标 STALE。
EXPIRED_HALF_YEAR_PAGE = """---
title: 贵州茅台600519-2024H1半年报（已过期）
created: 2024-08-30
updated: 2024-08-30
sources:
  - "[[../../raw/2024-08-30-贵州茅台-2024H1.md|公司公告-2024H1]]"
tags: [贵州茅台, 半年报, 2024H1]
related: [[investment/贵州茅台600519-白酒龙头]]
symbols: ["600519.SH 贵州茅台"]
themes: [高端白酒]
industry_chain_roles: [白酒龙头]
report_type: 半年报
evidence_level: A
valid_until: 2025-12-31
source_quality: 高
stale_risk: 低
# ── HY-001 半年报扩展字段 ──
financial_period: 2024H1
disclosure_date: 2024-08-30
source_type: [exchange_filing, fact_table]
financial_facts:
  - 营收 819.3亿 (+17.6% YoY)
  - 归母净利 416.9亿 (+15.9% YoY)
segment_facts:
  - 茅台酒营收占比稳定
forward_guidance:
  - 全年营收增长目标 15%（2024 年口径）
risk_factors: [宏观经济, 批价波动]
source_links:
  - 巨潮资讯 <公告URL>
---

# 贵州茅台（600519）— 2024H1 财报（已过期）

## 一句话总结

2024H1 营收 819.3亿，同比 +17.6%；本页 valid_until 已过期。

## 投资逻辑

- 高端白酒龙头，营收稳健。

## 风险提示

- 宏观经济波动。
- 批价波动。

## 原始资料

- 公司公告。
"""


# 5. 旧观点被削弱 — 早期券商观点被更新的财报事实削弱（HY-005 事实反证基线）。
#    management_commentary 持有 2026-02 旧观点（储能下半年爆发），financial_facts
#    （2026-08 披露）显示储能营收同比下滑 → 事实与旧观点矛盾。当前 lint 不检测
#    矛盾（通过即 high），HY-005 将基于本样本实现反证检测。
WEAKENED_OLD_OPINION_PAGE = """---
title: 宁德时代300750-2025H1半年报（旧观点已被事实削弱）
created: 2026-08-28
updated: 2026-08-28
sources:
  - "[[../../raw/2026-08-28-宁德时代-2025H1.md|公司公告-2025H1]]"
  - 某券商2026-02深度
tags: [宁德时代, 半年报, 2025H1]
related: [[investment/宁德时代300750-电池龙头]]
symbols: ["300750.SZ 宁德时代"]
themes: [动力电池, 储能]
industry_chain_roles: [电池龙头]
report_type: 半年报
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
# ── HY-001 半年报扩展字段 ──
financial_period: 2025H1
disclosure_date: 2026-08-28
source_type: [exchange_filing, fact_table, broker_report]
financial_facts:
  - 储能系统营收 120亿 (-20.0% YoY)（2026-08 披露口径）
  - 动力电池营收 400亿 (+10.0% YoY)
segment_facts:
  - 储能出货同比下滑
management_commentary:
  - 2026-02 券商观点：储能业务下半年将迎爆发式增长（旧观点，已被事实削弱）
forward_guidance:
  - 公司指引：储能全年持平
risk_factors: [储能增速不及预期, 原材料价格]
source_links:
  - 巨潮资讯 <公告URL>
  - 某券商2026-02深度 <研报URL>
---

# 宁德时代（300750）— 2025H1 财报（旧观点已被事实削弱）

## 一句话总结

2025H1 储能营收同比 -20.0%，与 2026-02 券商“爆发式增长”观点相悖；旧观点被事实削弱。

## 投资逻辑

- 动力电池营收同比 +10.0%（公告口径）。
- 储能营收同比 -20.0%（公告口径），早期“爆发式增长”观点已被事实削弱。

## 风险提示

- 储能增速不及预期。
- 原材料价格波动。

## 原始资料

- 公司公告。
- 某券商 2026-02 深度（旧观点）。
"""


# 6. 控制组 — 非财报页（report_type=公司点评），不应触发任何 HYF 规则。
NON_FINANCIAL_CONTROL_PAGE = """---
title: 平安银行000001-公司点评
created: 2026-05-20
updated: 2026-06-29
sources:
  - 某券商研报
tags: [平安银行]
related: [[investment/平安银行000001-零售转型]]
symbols: ["000001.SZ 平安银行"]
themes: [银行]
industry_chain_roles: [股份制银行]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 平安银行（000001）

## 一句话总结

零售业务转型推进。

## 投资逻辑

- 财富管理收入增长。

## 风险提示

- 资产质量波动。

## 原始资料

- 某券商研报。
"""


# ── 契约基线 spec ───────────────────────────────────────────────────


@dataclass(frozen=True)
class HalfYearFixtureSpec:
    """单个半年报 fixture 样本的预期契约（lint 回放黄金基线）。

    预先固定该样本在 KB-002 + HY-001 lint 下的预期结果，后续 HY 任务复用本样本时
    可直接断言这些字段，避免因契约漂移而静默失败。
    """

    name: str
    """稳定标识（``include=`` / ``FIXTURES_BY_NAME`` 的 key）。"""

    content: str
    """Markdown 页面原文。"""

    filename: str
    """写入 ``wiki/investment/`` 时使用的文件名。"""

    description: str
    """样本用途说明（中文）。"""

    report_type: str
    """frontmatter ``report_type``。"""

    is_half_year: bool
    """预期 ``PageLintResult.is_half_year_report``。"""

    expected_readiness: str
    """预期 ``machine_readiness``：high / medium / low。"""

    expected_hyf_errors: List[str]
    """预期命中的 HYF error 规则（如 ``["HYF-001"]``）。"""

    expected_hyf_warnings: List[str]
    """预期命中的 HYF warning 规则（如 ``["HYF-003"]``）。"""

    expected_other_warnings: List[str]
    """预期命中的非 HYF warning 规则（如 ``["STALE-002"]``）。"""

    expected_half_year_period: Optional[str]
    """预期 ``PageLintResult.half_year_period``（None 表示无 HY 页）。"""

    expected_opinion_only: bool
    """预期 ``half_year_opinion_only``（HYF-007 命中时为 True）。"""

    expected_symbols: List[str]
    """页面声明的 symbols（用于 HY-003 provider 按 symbol 查询的回放基线）。"""

    reuse_for: List[str] = field(default_factory=list)
    """该样本可被哪些后续 HY 任务复用（如 ``["HY-003", "HY-005"]``）。"""

    notes: str = ""
    """补充说明（如“HY-005 反证检测的数据种子”）。"""


#: 全部 fixture 契约基线（顺序即文档顺序）。
FIXTURE_SPECS: List[HalfYearFixtureSpec] = [
    HalfYearFixtureSpec(
        name="qualified",
        content=QUALIFIED_HALF_YEAR_PAGE,
        filename="浪潮信息000977-2025H1半年报.md",
        description="合格半年报：KB-002 + HY-001 全字段满足，high readiness，无 finding。",
        report_type="半年报",
        is_half_year=True,
        expected_readiness="high",
        expected_hyf_errors=[],
        expected_hyf_warnings=[],
        expected_other_warnings=[],
        expected_half_year_period="2025H1",
        expected_opinion_only=False,
        expected_symbols=["000977.SZ 浪潮信息"],
        reuse_for=["HY-001", "HY-003", "HY-004", "HY-008"],
        notes="契约黄金样本；任何 lint 改动不应使其产生 finding。",
    ),
    HalfYearFixtureSpec(
        name="missing_period",
        content=MISSING_FINANCIAL_PERIOD_PAGE,
        filename="华勤技术603296-缺报告期半年报.md",
        description="缺报告期：report_type=半年报 但缺 financial_period → HYF-001 error。",
        report_type="半年报",
        is_half_year=True,
        expected_readiness="medium",
        expected_hyf_errors=["HYF-001"],
        expected_hyf_warnings=[],
        expected_other_warnings=[],
        expected_half_year_period=None,
        expected_opinion_only=False,
        expected_symbols=["603296.SH 华勤技术"],
        reuse_for=["HY-001", "HY-002"],
        notes="单点失败样本：仅缺 financial_period，便于回归 HYF-001。",
    ),
    HalfYearFixtureSpec(
        name="fact_opinion_mix",
        content=FACT_OPINION_MIX_PAGE,
        filename="海康威视002415-2025H1半年报-事实观点混用.md",
        description="事实/观点混用：source_type 同时含事实类与观点类，不触发 HYF-007。",
        report_type="半年报",
        is_half_year=True,
        expected_readiness="high",
        expected_hyf_errors=[],
        expected_hyf_warnings=[],
        expected_other_warnings=[],
        expected_half_year_period="2025H1",
        expected_opinion_only=False,
        expected_symbols=["002415.SZ 海康威视"],
        reuse_for=["HY-005", "HY-014", "KB-014"],
        notes=(
            "为 HY-005/KB-014 提供基线：lint 不判观点冒充事实，但下游须把 "
            "broker_report 部分降权，不得作为事实进入反证。"
        ),
    ),
    HalfYearFixtureSpec(
        name="expired",
        content=EXPIRED_HALF_YEAR_PAGE,
        filename="贵州茅台600519-2024H1半年报-已过期.md",
        description="过期：valid_until 已过期 → STALE-002 warning + is_stale，readiness 压到 medium。",
        report_type="半年报",
        is_half_year=True,
        expected_readiness="medium",
        expected_hyf_errors=[],
        expected_hyf_warnings=[],
        expected_other_warnings=["STALE-002"],
        expected_half_year_period="2024H1",
        expected_opinion_only=False,
        expected_symbols=["600519.SH 贵州茅台"],
        reuse_for=["HY-003", "HY-004"],
        notes="HY 字段齐全但仍跑 HYF 规则；TA 命中后须标 STALE，不得用于实时结论。",
    ),
    HalfYearFixtureSpec(
        name="weakened_old_opinion",
        content=WEAKENED_OLD_OPINION_PAGE,
        filename="宁德时代300750-2025H1半年报-旧观点被削弱.md",
        description="旧观点被削弱：早期券商观点被更新财报事实反驳（HY-005 事实反证数据种子）。",
        report_type="半年报",
        is_half_year=True,
        expected_readiness="high",
        expected_hyf_errors=[],
        expected_hyf_warnings=[],
        expected_other_warnings=[],
        expected_half_year_period="2025H1",
        expected_opinion_only=False,
        expected_symbols=["300750.SZ 宁德时代"],
        reuse_for=["HY-005", "HY-008"],
        notes=(
            "当前 lint 通过（事实字段齐全），但 management_commentary 的旧观点与 "
            "financial_facts 矛盾；HY-005 将基于本样本实现反证检测。"
        ),
    ),
    HalfYearFixtureSpec(
        name="non_financial_control",
        content=NON_FINANCIAL_CONTROL_PAGE,
        filename="平安银行000001-公司点评.md",
        description="控制组：非财报页（report_type=公司点评），不应触发任何 HYF 规则。",
        report_type="公司点评",
        is_half_year=False,
        expected_readiness="high",
        expected_hyf_errors=[],
        expected_hyf_warnings=[],
        expected_other_warnings=[],
        expected_half_year_period=None,
        expected_opinion_only=False,
        expected_symbols=["000001.SZ 平安银行"],
        reuse_for=["HY-001", "HY-003"],
        notes="负样本控制：确保 HYF 规则不会误伤非财报页。",
    ),
]


#: 按 name 索引的 fixture 字典。
FIXTURES_BY_NAME: Dict[str, HalfYearFixtureSpec] = {
    spec.name: spec for spec in FIXTURE_SPECS
}


# ── 微型知识库构造 helper ────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_half_year_fixture_kb(
    tmp_path: Path,
    include: Optional[List[str]] = None,
    with_index: bool = True,
) -> Path:
    """在 ``tmp_path`` 下构造微型 Tree Work 知识库，写入指定的半年报 fixture 样本。

    参数:
        tmp_path: 临时目录根（充当 ``knowledge_root``）。
        include: 要包含的 fixture ``name`` 列表（见 :data:`FIXTURE_SPECS`）。
            ``None`` 表示写入全部样本。传入未知 name 抛 ``KeyError``。
        with_index: 是否写入 ``wiki/index.md`` 与 ``wiki/log.md``。
            KB-002 lint 用 index 做“只读对齐”，缺失时只跳过 index 校验不报错。

    返回:
        ``knowledge_root``（即 ``tmp_path`` 本身），可直接传给
        :func:`lint_local_knowledge`。

    目录结构::

        tmp_path/
          wiki/
            index.md
            log.md
            investment/
              <fixture filename>.md
              ...
    """
    if include is None:
        specs = list(FIXTURE_SPECS)
    else:
        unknown = [n for n in include if n not in FIXTURES_BY_NAME]
        if unknown:
            available = ", ".join(sorted(FIXTURES_BY_NAME))
            raise KeyError(
                f"未知 fixture name: {unknown}；可用: {available}"
            )
        specs = [FIXTURES_BY_NAME[n] for n in include]

    inv = tmp_path / INVESTMENT_SUBDIR
    for spec in specs:
        _write(inv / spec.filename, spec.content)

    if with_index:
        bullet = "\n".join(
            f"- [[investment/{Path(spec.filename).stem}]]" for spec in specs
        )
        _write(
            tmp_path / INDEX_MD,
            "---\ntitle: Wiki Index\n---\n\n# Wiki Index\n\n" + bullet + "\n",
        )
        _write(tmp_path / LOG_MD, "# Log\n")

    return tmp_path


__all__ = [
    "SYMBOL_FORMAT",
    "SYMBOL_EXAMPLES",
    "SOURCE_TYPE_FACT_VALUES_TUPLE",
    "SOURCE_TYPE_OPINION_VALUES_TUPLE",
    "SOURCE_TYPE_ALL_VALUES_TUPLE",
    "FINANCIAL_PERIOD_PREFERRED",
    "FINANCIAL_PERIOD_EXAMPLES",
    "HALF_YEAR_REPORT_TYPES_TUPLE",
    "QUALIFIED_HALF_YEAR_PAGE",
    "MISSING_FINANCIAL_PERIOD_PAGE",
    "FACT_OPINION_MIX_PAGE",
    "EXPIRED_HALF_YEAR_PAGE",
    "WEAKENED_OLD_OPINION_PAGE",
    "NON_FINANCIAL_CONTROL_PAGE",
    "HalfYearFixtureSpec",
    "FIXTURE_SPECS",
    "FIXTURES_BY_NAME",
    "build_half_year_fixture_kb",
]
