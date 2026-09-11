#!/usr/bin/env python3
"""V-015 研报增量摄取→证据 API→前端→待更新清单 端到端验收报告生成.

两部分、分开报告（任务卡 V-015 约束）：
  1. fixture 端到端回放：调用 pytest 跑 ``tests/test_v015_research_operations_e2e.py``
     （五类回放 / 链路一致性 / 降级），嵌入真实退出码与输出摘要。
  2. 真实知识库只读 smoke：对 ``~/Documents/knowledge`` 只读运行
     KB-019 增量清单、KB-020 证据聚合与 HY-010 待更新队列（universe 为
     合成样本，不读取生产数据库），记录状态与契约核验。

约束：只读知识库；不写生产 DB；不调用 live LLM；不触发完整 TA；
真实目录不可用不得伪造 PASS。

Usage:
    .venv/bin/python scripts/run_v015_operations_acceptance.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import research_evidence_service  # noqa: E402
from tradingagents.dataflows.research_ingest_delta import (  # noqa: E402
    build_research_ingest_delta,
)
from tradingagents.tradeflow.half_year_update_queue import (  # noqa: E402
    build_half_year_update_queue,
)

FORBIDDEN_ACTION_KEYS = {
    "decision", "action_label", "buy_level", "execution_action",
    "playbook_stage",
}

E2E_TEST_PATH = "tests/test_v015_research_operations_e2e.py"


def _walk_forbidden(node, path, hits):
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() in FORBIDDEN_ACTION_KEYS:
                hits.append(f"{path}.{k}")
            _walk_forbidden(v, f"{path}.{k}", hits)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_forbidden(v, f"{path}[{i}]", hits)


def run_fixture_e2e() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", E2E_TEST_PATH, "-q", "--tb=short"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-6:])
    return proc.returncode, tail


def run_real_smoke(knowledge_root: Path) -> dict:
    out: dict = {"knowledge_root": str(knowledge_root)}
    gates: list[str] = []

    # ── 可用性门禁 0：真实库结构必须存在（区分"空数据降级测试"与"链路可用"）──
    investment = knowledge_root / "wiki" / "investment"
    page_count = (
        sum(1 for p in investment.rglob("*.md")) if investment.is_dir() else 0
    )
    out["structure"] = {
        "investment_dir_exists": investment.is_dir(),
        "page_count": page_count,
    }
    if not investment.is_dir():
        gates.append(f"必需目录缺失: {investment}")
    elif page_count == 0:
        gates.append(f"真实知识库 investment 分区为空（0 页）: {investment}")

    # KB-019：真实增量摄取清单（只读）
    delta = build_research_ingest_delta(str(knowledge_root))
    counts = delta.status_counts()
    out["ingest_delta"] = {
        "total": delta.total(),
        "status_counts": dict(counts),
        "errors": list(delta.errors)[:3],
        "top_new": [
            {"location": it.location, "ingest_key": it.ingest_key}
            for it in delta.items_by_status("new")[:3]
        ],
    }
    # ── 可用性门禁 1：摄取阶段错误不得静默 ──
    if delta.errors:
        gates.append(f"KB-019 摄取扫描产生 {len(delta.errors)} 个错误（首个: {delta.errors[0]}）")

    # KB-020：真实证据聚合（沿用 V-014 抽样中的两只）
    probes = {}
    usable_consensus = 0
    for sym in ("300750.SZ", "688041.SH"):
        try:
            evidence = research_evidence_service.build_research_evidence(
                sym, knowledge_root=str(knowledge_root)
            )
            buckets = {
                name: {
                    "has_hit": bool(evidence.get(name, {}).get("has_hit")),
                    "data_status": evidence.get(name, {}).get("data_status"),
                }
                for name in (
                    "consensus", "citation_audit", "thesis_timeline",
                    "half_year_facts", "research_score_snapshot",
                )
            }
            if buckets["consensus"]["has_hit"]:
                usable_consensus += 1
            hits: list[str] = []
            _walk_forbidden(evidence, "$", hits)
            probes[sym] = {
                "data_status": evidence.get("data_status"),
                "buckets": buckets,
                "forbidden_action_keys": hits[:3],
            }
        except Exception as exc:
            probes[sym] = {"error": f"{type(exc).__name__}: {exc}"}
    out["evidence_probes"] = probes
    # ── 可用性门禁 2：至少一只抽样 symbol 的共识链路真实可用（has_hit）。
    # 全部 missing 只说明"降级路径正常"，不能证明真实链路可用。
    if usable_consensus == 0:
        gates.append(
            "KB-020 抽样全部无共识命中（consensus has_hit 均为 false）——"
            "证据链路在真实库上不可用，不得判定 PASS"
        )

    # 契约违例也是门禁
    for sym, probe in probes.items():
        if probe.get("forbidden_action_keys"):
            gates.append(f"KB-020 `{sym}` 契约违例: {probe['forbidden_action_keys']}")
        if "error" in probe:
            gates.append(f"KB-020 `{sym}` 聚合异常: {probe['error']}")

    # HY-010：真实知识根 + 合成 universe（不读生产 DB）
    context = {
        "as_of": date.today().isoformat() + " 09:30:00",
        "holdings": {"items": [{"symbol": "000977.SZ", "name": "浪潮信息"}]},
        "observation_warehouse": {"items": []},
        "tradeflow_candidates": {"items": []},
        "mandate_daily_report": {"data_status": "missing"},
        "half_year_facts": {"items": []},
    }
    queue = build_half_year_update_queue(context)
    qd = queue.to_dict()
    hits: list[str] = []
    _walk_forbidden(qd, "$", hits)
    out["update_queue"] = {
        "universe_size": qd.get("universe_size"),
        "summary_by_status": qd.get("summary_by_status"),
        "sample_symbol_status": next(
            (
                it["status"] for it in qd.get("items", [])
                if it["symbol"] == "000977.SZ"
            ),
            None,
        ),
        "forbidden_action_keys": hits[:3],
        "note": "universe 为合成样本（不读取生产数据库）",
    }
    if hits:
        gates.append(f"HY-010 队列契约违例: {hits[:3]}")

    out["usability_gates_failed"] = gates
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge-root",
        default=os.path.expanduser("~/Documents/knowledge"),
    )
    parser.add_argument(
        "--report",
        default="docs/knowledge_reports/research-operations-acceptance-"
        + date.today().isoformat()
        + ".md",
    )
    args = parser.parse_args()

    root = Path(args.knowledge_root).expanduser()
    lines: list[str] = []
    overall_ok = True

    lines.append(f"# 研报运营端到端验收报告（V-015）— {date.today().isoformat()}")
    lines.append("")
    lines.append(
        "链路：KB-019 增量摄取（ingest_key/status）→ KB-020 证据 API → "
        "UI-014 前端契约 → HY-010 待更新清单。全程只读、无 LLM、不写生产 DB。"
    )
    lines.append("")

    # ── Part 1: fixture e2e ──
    lines.append("## Part 1 — fixture 端到端回放（隔离 tmp 知识库）")
    lines.append("")
    if root.is_dir():
        rc, tail = run_fixture_e2e()
        if rc != 0:
            overall_ok = False
        lines.append(f"- `pytest {E2E_TEST_PATH}` 退出码：**{rc}**")
        lines.append("")
        lines.append("```")
        lines.append(tail)
        lines.append("```")
        lines.append("")
        lines.append(
            "覆盖：新增研报（ingest_key 幂等）/ 重复研报（duplicate_of）/ "
            "缺元数据（needs_metadata）/ 半年报修订（HY-009 revised 贯穿）/ "
            "事实冲突（证据 conflict + HY-010 conflict）；链路字段与前端 "
            "UI-014 视图模型消费字段一致；全链路无动作语义字段；局部失败/"
            "缓存损坏/空目录可解释降级。"
        )
    else:
        overall_ok = False
        lines.append(f"- fixture e2e 未执行（异常）")
    lines.append("")

    # ── Part 2: 真实只读 smoke ──
    lines.append("## Part 2 — 真实知识库只读 smoke（含链路可用性门禁）")
    lines.append("")
    if not root.is_dir():
        overall_ok = False
        lines.append(f"- **FAIL**：真实知识库目录不可用：{root}（不得伪造 PASS）")
    else:
        smoke = run_real_smoke(root)
        lines.append(f"- 知识库：`{root}`（只读）")
        structure = smoke["structure"]
        lines.append(
            f"- 结构核验：investment 分区存在={structure['investment_dir_exists']}，"
            f"页面数={structure['page_count']}"
        )
        ingest = smoke["ingest_delta"]
        lines.append(
            f"- KB-019 增量清单：total={ingest['total']}，"
            f"状态分布 {json.dumps(ingest['status_counts'], ensure_ascii=False)}"
        )
        for sym, probe in smoke["evidence_probes"].items():
            if "error" in probe:
                lines.append(f"- KB-020 `{sym}`：异常 {probe['error']}")
            else:
                bad = probe["forbidden_action_keys"]
                bucket_str = "；".join(
                    f"{k}={v['data_status']}/hit={str(v['has_hit']).lower()}"
                    for k, v in probe["buckets"].items()
                )
                lines.append(
                    f"- KB-020 `{sym}`：aggregate={probe['data_status']}；"
                    f"{bucket_str}；契约违例：{bad or '无'}"
                )
        q = smoke["update_queue"]
        lines.append(
            f"- HY-010 待更新队列：universe={q['universe_size']}（{q['note']}），"
            f"状态分布 {json.dumps(q['summary_by_status'], ensure_ascii=False)}，"
            f"样本 symbol 状态={q['sample_symbol_status']}，"
            f"契约违例：{q['forbidden_action_keys'] or '无'}"
        )
        # [V-015-fix] 链路可用性门禁：区分"空数据降级测试通过"与"真实链路可用"
        gates = smoke["usability_gates_failed"]
        lines.append("")
        lines.append("### 链路可用性门禁")
        lines.append("")
        if gates:
            overall_ok = False
            for g in gates:
                lines.append(f"- **未通过**：{g}")
            lines.append("")
            lines.append(
                "> 说明：fixture 降级回放通过 ≠ 真实链路可用。真实库必须"
                "通过门禁（必需目录存在且非空、摄取无错误、至少一只抽样"
                "共识命中、无契约违例）方可判定 PASS。"
            )
        else:
            lines.append(
                "- 全部通过：必需目录存在且非空、摄取无错误、抽样共识命中、无契约违例"
            )
    lines.append("")

    lines.append("## 结论")
    lines.append("")
    if overall_ok:
        lines.append(
            "- **PASS**：fixture 端到端回放全部通过；真实只读 smoke 无异常、"
            "无契约违例。链路可用、可追溯、不影响交易动作。"
        )
    else:
        lines.append("- **FAIL**：存在异常或契约违例，见上文。")
    lines.append("")
    lines.append(
        "> fixture 与真实只读 smoke 分开报告；本报告不修改任何业务代码，"
        "真实库侧数据质量问题记录为 findings（参见 V-014 日报）。"
    )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[V-015] 报告已生成: {report_path}")
    print(f"[V-015] 总体结论: {'PASS' if overall_ok else 'FAIL'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    import json

    raise SystemExit(main())
