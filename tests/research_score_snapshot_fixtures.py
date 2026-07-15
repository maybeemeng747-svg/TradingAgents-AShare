# [SCORE-001] research_score_snapshot_contract
"""研究评分快照 fixture 样本集（SCORE-001）。

为 SCORE-001 只读 loader 测试提供**不依赖真实知识库**的、字段 / 状态 / 失败路径
全部固定的人工快照样本。fixture 来源：ZCode 已验收的 v1.1.0 契约示例
（``docs/zcode_research_scorer_handoff.md`` §3），复制为**测试专用样本**并保留来源
说明；603629 ``drafts/`` 草案只用于验证"生产 loader 不得读取草案"。

设计原则
--------
- 每个样本都是一个 JSON ``dict``，可写入 ``tmp_path`` 形成微型快照目录。
- 路径结构严格遵循 ZCode 发布布局：
  ``research_score_snapshots/<symbol>/<snapshot_id>.json``
  草案路径：``research_score_snapshots/drafts/<symbol>/<name>.json``
- **不调用 live LLM，不访问真实知识库，不写生产 DB**，全部在 ``tmp_path`` 下运行。
- 每个样本附带 :class:`SnapshotFixtureSpec`，描述写入路径、预期 status、预期降级
  reason_code 与说明，作为 SCORE-001 回放的"黄金基线"。

复用方式
--------
::

    from tests.research_score_snapshot_fixtures import (
        FIXTURE_SPECS,
        FIXTURES_BY_NAME,
        build_snapshot_fixture_kb,
    )

    def test_something(tmp_path):
        root = build_snapshot_fixture_kb(tmp_path)               # 写入全部样本
        root = build_snapshot_fixture_kb(tmp_path, include=[...]) # 只写指定样本
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 路径常量（与 loader 保持一致）──────────────────────────────────────
SNAPSHOTS_DIR_NAME = "research_score_snapshots"
DRAFTS_DIR_NAME = "drafts"


# ── 样本生成器 ────────────────────────────────────────────────────────

def _base_snapshot(
    *,
    symbol: str = "605589.SH",
    name: str = "圣泉集团",
    as_of: str = "2026-07-13",
    snapshot_id: str = "605589.SH-20260713-r1",
    status: str = "HAS_DATA",
    rec: Optional[int] = 82,
    tqual: Optional[int] = 78,
    rubric_id: str = "research-score-generic",
    rubric_version: str = "1.0.0",
) -> Dict[str, Any]:
    """构造一份合法的基线快照（HAS_DATA，证据闭包完整）。"""
    return {
        "schema_version": "1.1.0",
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "name": name,
        "as_of": as_of,
        "created_at": f"{as_of}T20:00:00+08:00",
        "source_cutoff_at": f"{as_of}T15:00:00+08:00",
        "rubric_id": rubric_id,
        "rubric_version": rubric_version,
        "status": status,
        "scores": {
            "research_evidence_confidence": rec,
            "thesis_quality": tqual,
        },
        "thesis_breakdown": {
            "industry_necessity": 80,
            "barrier": 75,
        },
        "theses": [
            {
                "thesis_id": f"{symbol}-thesis-001",
                "symbol": symbol,
                "as_of": as_of,
                "topic": "示例投资假设",
                "direction": "bullish",
                "status": "active",
                "core_hypothesis": "示例假设已由原始财报与独立研报交叉核验",
                "supporting_evidence_ids": [f"fixture-{symbol}-fact-001"],
                "counter_evidence_ids": [],
                "missing_evidence": [],
                "upgrade_conditions": [],
                "downgrade_conditions": [],
                "invalidation_conditions": [],
            }
        ],
        "score_change": {
            "previous_snapshot_id": None,
            "previous": {},
            "current": {},
            "reasons": [],
        },
        "evidence_refs": [
            {
                "evidence_id": f"fixture-{symbol}-fact-001",
                "claim": "半年报关键财务事实已完成原文定位",
                "claim_type": "financial_fact",
                "source_path": f"fixtures/research_score/{symbol}/2026H1-facts.md",
                "source_type": "financial_report",
                "source_quality_tier": "original_filing",
                "report_date": as_of,
                "financial_period": "2026H1",
                "locator": "facts.key_financials",
            }
        ],
        "missing_evidence": [],
        "upgrade_conditions": [],
        "downgrade_conditions": [],
        "invalidation_conditions": [],
        "warnings": [],
    }


# ── fixture 规格 ─────────────────────────────────────────────────────


@dataclass
class SnapshotFixtureSpec:
    """单个 fixture 样本的写入规格。

    Attributes:
        name: fixture 唯一名（用于 ``include`` 选择）。
        symbol: 标的（决定目录路径）。
        filename: 文件名（不含路径）。
        data: 要写入的 JSON 内容。``None`` 表示写损坏文本（``corrupt_text``）。
        corrupt_text: 写入原始损坏文本（``data is None`` 时使用）。
        in_drafts: True → 写入 ``drafts/<symbol>/`` 而非正式目录。
        expected_status: loader 预期最终 status。
        expected_reason_codes: 预期出现在 degradation_reasons 的 reason_code 集合。
        description: 样本说明（来源 / 覆盖路径）。
    """

    name: str
    symbol: str
    filename: str
    data: Optional[Dict[str, Any]]
    in_drafts: bool = False
    corrupt_text: Optional[str] = None
    expected_status: str = "HAS_DATA"
    expected_reason_codes: List[str] = field(default_factory=list)
    description: str = ""


# 合法基线（HAS_DATA）。
QUALIFIED = SnapshotFixtureSpec(
    name="qualified",
    symbol="605589.SH",
    filename="605589.SH-20260713-r1.json",
    data=_base_snapshot(),
    expected_status="HAS_DATA",
    description="合法正式快照（HAS_DATA，证据闭包完整）",
)

# STALE 状态（快照自身声明）。
STALE_STATUS = SnapshotFixtureSpec(
    name="stale_status",
    symbol="605589.SH",
    filename="605589.SH-20260713-stale.json",
    data=_base_snapshot(
        snapshot_id="605589.SH-20260713-stale",
        status="STALE",
    ),
    expected_status="STALE",
    description="快照自身声明 STALE",
)

# LOW_CONFIDENCE 状态。
LOW_CONFIDENCE = SnapshotFixtureSpec(
    name="low_confidence",
    symbol="605589.SH",
    filename="605589.SH-20260713-low.json",
    data=_base_snapshot(
        snapshot_id="605589.SH-20260713-low",
        status="LOW_CONFIDENCE",
        rec=40,
        tqual=35,
    ),
    expected_status="LOW_CONFIDENCE",
    description="快照自身声明 LOW_CONFIDENCE",
)

# NORMAL_NO_DATA 状态（正式快照，但自身声明无数据）。
NORMAL_NO_DATA = SnapshotFixtureSpec(
    name="normal_no_data",
    symbol="605589.SH",
    filename="605589.SH-20260713-no-data.json",
    data=_base_snapshot(
        snapshot_id="605589.SH-20260713-no-data",
        status="NORMAL_NO_DATA",
        rec=None,
        tqual=None,
    ),
    expected_status="NORMAL_NO_DATA",
    description="正式快照声明 NORMAL_NO_DATA（分数为 null）",
)

# FAILED 状态（快照自身声明 FAILED）。
FAILED_STATUS = SnapshotFixtureSpec(
    name="failed_status",
    symbol="605589.SH",
    filename="605589.SH-20260713-failed.json",
    data=_base_snapshot(
        snapshot_id="605589.SH-20260713-failed",
        status="FAILED",
        rec=None,
        tqual=None,
    ),
    expected_status="FAILED",
    description="正式快照声明 FAILED",
)

# 缺 scores 字段（缺失不等于 0，loader 接受 null）。
MISSING_SCORES = SnapshotFixtureSpec(
    name="missing_scores",
    symbol="605589.SH",
    filename="605589.SH-20260713-missing-scores.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-missing-scores"),
        "scores": {},
    },
    expected_status="HAS_DATA",
    description="scores 子表为空（分数缺失为 null，不报错）",
)

# 损坏 JSON。
CORRUPTED = SnapshotFixtureSpec(
    name="corrupted",
    symbol="605589.SH",
    filename="605589.SH-20260713-corrupt.json",
    data=None,
    corrupt_text="{ this is not valid json,,, }",
    expected_status="NORMAL_NO_DATA",
    description="损坏 JSON（单独存在 → 无可读候选）",
)

# symbol 错配（快照内部 symbol 与目录/查询不一致）。
SYMBOL_MISMATCH = SnapshotFixtureSpec(
    name="symbol_mismatch",
    symbol="605589.SH",
    filename="605589.SH-20260713-symmm.json",
    data=_base_snapshot(
        symbol="000977.SZ",
        name="浪潮信息",
        snapshot_id="605589.SH-20260713-symmm",
    ),
    expected_status="FAILED",
    expected_reason_codes=["SYMBOL_MISMATCH"],
    description="快照内部 symbol 与目录不一致 → fail closed（候选存在但不可用）",
)

# 未知 schema_version。
UNKNOWN_VERSION = SnapshotFixtureSpec(
    name="unknown_version",
    symbol="605589.SH",
    filename="605589.SH-20260713-unknown-version.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-unknown-version"),
        "schema_version": "0.9.0",
    },
    expected_status="FAILED",
    expected_reason_codes=["UNKNOWN_SCHEMA_VERSION"],
    description="未知 schema_version=0.9.0 → fail closed（候选存在但不可用）",
)

# 非法分数（越界）。
ILLEGAL_SCORE = SnapshotFixtureSpec(
    name="illegal_score",
    symbol="605589.SH",
    filename="605589.SH-20260713-illegal-score.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-illegal-score"),
        "scores": {
            "research_evidence_confidence": 150,
            "thesis_quality": 78,
        },
    },
    expected_status="FAILED",
    expected_reason_codes=["ILLEGAL_SCORE"],
    description="分数越界 150 → fail closed（候选存在但不可用）",
)

# 未来快照（as_of 晚于 analysis_time）。
FUTURE_SNAPSHOT = SnapshotFixtureSpec(
    name="future_snapshot",
    symbol="605589.SH",
    filename="605589.SH-20991231-future.json",
    data=_base_snapshot(
        as_of="2099-12-31",
        snapshot_id="605589.SH-20991231-future",
    ),
    expected_status="NORMAL_NO_DATA",
    description="未来快照 as_of=2099-12-31 → 不进入候选（不穿越）",
)

# 悬空 evidence ref（thesis 引用了不存在的 evidence_id）。
DANGLING_REF = SnapshotFixtureSpec(
    name="dangling_ref",
    symbol="605589.SH",
    filename="605589.SH-20260713-dangling.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-dangling"),
        "theses": [
            {
                **_base_snapshot()["theses"][0],
                "supporting_evidence_ids": ["nonexistent-evidence-999"],
            }
        ],
    },
    expected_status="FAILED",
    expected_reason_codes=["DANGLING_EVIDENCE_REF"],
    description="thesis 悬空 evidence ref → fail closed（候选存在但不可用）",
)

# invalidated thesis（合法状态，loader 接受）。
INVALIDATED_THESIS = SnapshotFixtureSpec(
    name="invalidated_thesis",
    symbol="605589.SH",
    filename="605589.SH-20260713-invalidated.json",
    data={
        **_base_snapshot(
            snapshot_id="605589.SH-20260713-invalidated",
            status="LOW_CONFIDENCE",
            rec=20,
            tqual=15,
        ),
        "theses": [
            {
                **_base_snapshot()["theses"][0],
                "status": "invalidated",
                "core_hypothesis": "核心逻辑已被公告澄清证伪",
                "supporting_evidence_ids": [],
                "counter_evidence_ids": [f"fixture-605589.SH-fact-001"],
            }
        ],
    },
    expected_status="LOW_CONFIDENCE",
    description="invalidated thesis 是合法状态（loader 接受）",
)

# 顶层夹带禁止动作字段 action。
FORBIDDEN_TOP = SnapshotFixtureSpec(
    name="forbidden_top",
    symbol="605589.SH",
    filename="605589.SH-20260713-forbidden-top.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-forbidden-top"),
        "action": "BUY",
    },
    expected_status="FAILED",
    expected_reason_codes=["FORBIDDEN_ACTION_FIELD"],
    description="顶层夹带 action 字段 → fail closed（候选存在但不可用）",
)

# scores 子表夹带禁止动作字段 entry_timing。
FORBIDDEN_SCORES = SnapshotFixtureSpec(
    name="forbidden_scores",
    symbol="605589.SH",
    filename="605589.SH-20260713-forbidden-scores.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-forbidden-scores"),
        "scores": {
            "research_evidence_confidence": 82,
            "thesis_quality": 78,
            "entry_timing": 90,
        },
    },
    expected_status="FAILED",
    expected_reason_codes=["FORBIDDEN_ACTION_FIELD"],
    description="scores 子表夹带 entry_timing → fail closed（候选存在但不可用）",
)

# thesis 夹带禁止动作字段 playbook_stage。
FORBIDDEN_THESIS = SnapshotFixtureSpec(
    name="forbidden_thesis",
    symbol="605589.SH",
    filename="605589.SH-20260713-forbidden-thesis.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-forbidden-thesis"),
        "theses": [
            {
                **_base_snapshot()["theses"][0],
                "playbook_stage": "attack",
            }
        ],
    },
    expected_status="FAILED",
    expected_reason_codes=["FORBIDDEN_ACTION_FIELD"],
    description="thesis 夹带 playbook_stage → fail closed（候选存在但不可用）",
)

# 草案（在 drafts/ 下，生产 loader 不得读取）。
DRAFT_603629 = SnapshotFixtureSpec(
    name="draft_603629",
    symbol="603629.SH",
    filename="603629.SH-20260713-draft.json",
    in_drafts=True,
    data=_base_snapshot(
        symbol="603629.SH",
        name="利通电子",
        as_of="2026-07-13",
        snapshot_id="603629.SH-20260713-draft",
        status="LOW_CONFIDENCE",
        rec=45,
        tqual=40,
    ),
    expected_status="NORMAL_NO_DATA",
    description="603629 drafts/ 草案（生产 loader 必须排除）",
)

# 非法 source_quality_tier（不在 KB-014 取值表）。
ILLEGAL_TIER = SnapshotFixtureSpec(
    name="illegal_tier",
    symbol="605589.SH",
    filename="605589.SH-20260713-illegal-tier.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-illegal-tier"),
        "evidence_refs": [
            {
                **_base_snapshot()["evidence_refs"][0],
                "source_quality_tier": "A_PLUS",
            }
        ],
    },
    expected_status="FAILED",
    expected_reason_codes=["CORRUPTED"],
    description="source_quality_tier=A_PLUS 不在 KB-014 取值表 → fail closed（候选存在但不可用）",
)

# 绝对路径 evidence source_path（路径逃逸）。
PATH_ESCAPE = SnapshotFixtureSpec(
    name="path_escape",
    symbol="605589.SH",
    filename="605589.SH-20260713-path-escape.json",
    data={
        **_base_snapshot(snapshot_id="605589.SH-20260713-path-escape"),
        "evidence_refs": [
            {
                **_base_snapshot()["evidence_refs"][0],
                "source_path": "/etc/passwd",
            }
        ],
    },
    expected_status="FAILED",
    expected_reason_codes=["PATH_ESCAPE"],
    description="evidence source_path 绝对路径 → fail closed（候选存在但不可用）",
)

# 多版本（用于 analysis_time 时序选择 / 时间穿越测试）。
MULTI_VERSION_R1 = SnapshotFixtureSpec(
    name="multi_version_r1",
    symbol="600519.SH",
    filename="600519.SH-20260601-r1.json",
    data=_base_snapshot(
        symbol="600519.SH",
        name="贵州茅台",
        as_of="2026-06-01",
        snapshot_id="600519.SH-20260601-r1",
        rec=70,
        tqual=72,
    ),
    expected_status="HAS_DATA",
    description="600519 多版本 r1（旧）",
)

MULTI_VERSION_R2 = SnapshotFixtureSpec(
    name="multi_version_r2",
    symbol="600519.SH",
    filename="600519.SH-20260710-r2.json",
    data=_base_snapshot(
        symbol="600519.SH",
        name="贵州茅台",
        as_of="2026-07-10",
        snapshot_id="600519.SH-20260710-r2",
        rec=85,
        tqual=80,
    ),
    expected_status="HAS_DATA",
    description="600519 多版本 r2（新）",
)


# 全部规格（按顺序）。
FIXTURE_SPECS: List[SnapshotFixtureSpec] = [
    QUALIFIED,
    STALE_STATUS,
    LOW_CONFIDENCE,
    NORMAL_NO_DATA,
    FAILED_STATUS,
    MISSING_SCORES,
    CORRUPTED,
    SYMBOL_MISMATCH,
    UNKNOWN_VERSION,
    ILLEGAL_SCORE,
    FUTURE_SNAPSHOT,
    DANGLING_REF,
    INVALIDATED_THESIS,
    FORBIDDEN_TOP,
    FORBIDDEN_SCORES,
    FORBIDDEN_THESIS,
    DRAFT_603629,
    ILLEGAL_TIER,
    PATH_ESCAPE,
    MULTI_VERSION_R1,
    MULTI_VERSION_R2,
]

FIXTURES_BY_NAME: Dict[str, SnapshotFixtureSpec] = {
    spec.name: spec for spec in FIXTURE_SPECS
}


# ── 写入工具 ──────────────────────────────────────────────────────────


def _write_snapshot(root: Path, spec: SnapshotFixtureSpec) -> Path:
    """按 spec 写入单个快照文件，返回写入路径。

    - 正式快照：``research_score_snapshots/<symbol>/<filename>``
    - 草案：``research_score_snapshots/drafts/<symbol>/<filename>``
    - ``data is None``：写损坏文本。
    """
    snapshots_root = root / SNAPSHOTS_DIR_NAME
    if spec.in_drafts:
        target_dir = snapshots_root / DRAFTS_DIR_NAME / spec.symbol
    else:
        target_dir = snapshots_root / spec.symbol
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / spec.filename
    if spec.data is None:
        target.write_text(spec.corrupt_text or "", encoding="utf-8")
    else:
        target.write_text(
            json.dumps(spec.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return target


def build_snapshot_fixture_kb(
    tmp_path: Path,
    *,
    include: Optional[List[str]] = None,
) -> Path:
    """在 ``tmp_path`` 下写入研究快照 fixture 集合，返回知识库根路径。

    参数:
        tmp_path: pytest ``tmp_path``（或任意临时目录）。
        include: 只写入指定 name 列表；``None`` 写入全部。

    返回:
        知识库根目录 Path（``tmp_path`` 本身）。
    """
    root = Path(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    specs: List[SnapshotFixtureSpec] = FIXTURE_SPECS
    if include is not None:
        specs = [FIXTURES_BY_NAME[n] for n in include if n in FIXTURES_BY_NAME]
    for spec in specs:
        _write_snapshot(root, spec)
    return root


__all__ = [
    "SNAPSHOTS_DIR_NAME",
    "DRAFTS_DIR_NAME",
    "SnapshotFixtureSpec",
    "FIXTURE_SPECS",
    "FIXTURES_BY_NAME",
    "QUALIFIED",
    "STALE_STATUS",
    "LOW_CONFIDENCE",
    "NORMAL_NO_DATA",
    "FAILED_STATUS",
    "MISSING_SCORES",
    "CORRUPTED",
    "SYMBOL_MISMATCH",
    "UNKNOWN_VERSION",
    "ILLEGAL_SCORE",
    "FUTURE_SNAPSHOT",
    "DANGLING_REF",
    "INVALIDATED_THESIS",
    "FORBIDDEN_TOP",
    "FORBIDDEN_SCORES",
    "FORBIDDEN_THESIS",
    "DRAFT_603629",
    "ILLEGAL_TIER",
    "PATH_ESCAPE",
    "MULTI_VERSION_R1",
    "MULTI_VERSION_R2",
    "build_snapshot_fixture_kb",
]
