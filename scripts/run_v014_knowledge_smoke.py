#!/usr/bin/env python3
"""V-014 真实本地知识库只读 smoke 与研报主线验收 CLI.

对 ``~/Documents/knowledge/`` 做一次只读 smoke：抽样覆盖 多研报同股 /
已有半年报 / 无半年报 / 过期知识 四类股票，贯穿 KB-020 研报证据聚合
（KB-016 共识 / KB-017 引用审计 / KB-018 时间线 / HY-003 半年报事实 /
SCORE-001B 评分快照），统计观点/事实分离覆盖率、citation 完整率、事实
冲突率、待验证率和缓存新鲜度，并验证 TA/TradeFlow/IC 消费契约字段。

约束（任务卡 V-014）：
  - READ-ONLY：绝不写知识库；不写生产 DB；不调用 live LLM；不联网。
  - 真实目录不可用时以退出码 1 失败并写明路径/权限原因，不得用
    fixture 冒充真实验收。
  - 业务缺陷记录为 finding，不在验收任务中跨范围改代码。

Usage:
    .venv/bin/python scripts/run_v014_knowledge_smoke.py \\
        --knowledge-root ~/Documents/knowledge \\
        --report docs/knowledge_reports/research_mainline_acceptance-2026-09-11.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# frontmatter symbols 形如 "300750.SZ 宁德时代"，提取前导代码 token
_CODE_TOKEN_RE = re.compile(r"^(\d{6}(?:\.\w{2})?)\b")

from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    _iter_markdown_files,
    _is_valid_until_expired,
    _parse_frontmatter,
    _read_text_safe,
    _split_frontmatter,
)
from tradingagents.dataflows.local_knowledge_cache import (  # noqa: E402
    build_cache_from_scan,
)
from tradingagents.dataflows.local_knowledge_lint import (  # noqa: E402
    HALF_YEAR_REPORT_TYPES,
    HIGH_STALE_RISK_VALUES,
    SOURCE_TYPE_ALL_VALUES,
    _is_half_year_report,
    _normalize_source_type_list,
)
from tradingagents.dataflows.local_knowledge_provider import (  # noqa: E402
    _normalize_symbol_list,
)
from api.services import research_evidence_service  # noqa: E402

# TA/TradeFlow/IC 契约：证据层不得携带任何动作语义字段
FORBIDDEN_ACTION_KEYS = {
    "decision",
    "action_label",
    "buy_level",
    "execution_action",
    "playbook_stage",
}

SAMPLE_PER_CATEGORY = 2


def _safe_str(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def index_pages(knowledge_root: Path) -> List[Dict[str, Any]]:
    """只读枚举 wiki/investment 页面并解析 frontmatter 索引。"""
    investment = knowledge_root / "wiki" / "investment"
    pages: List[Dict[str, Any]] = []
    for md in _iter_markdown_files(investment):
        rel = str(md.relative_to(knowledge_root))
        try:
            text = _read_text_safe(md)
            fm_text, _body = _split_frontmatter(text)
            fm = _parse_frontmatter(fm_text)
        except Exception as exc:  # 解析失败计入索引错误
            pages.append({"rel_path": rel, "error": repr(exc)})
            continue
        source_types = _normalize_source_type_list(fm.get("source_type"))
        valid_until = _safe_str(fm.get("valid_until")) or None
        stale_val = _safe_str(fm.get("stale_risk"))
        pages.append(
            {
                "rel_path": rel,
                "symbols": _normalize_symbol_list(fm.get("symbols")),
                "report_type": _safe_str(fm.get("report_type")),
                "is_half_year": _is_half_year_report(fm),
                "source_types": source_types,
                "source_type_known": all(
                    s in SOURCE_TYPE_ALL_VALUES for s in source_types
                )
                if source_types
                else False,
                "expired": (
                    stale_val in HIGH_STALE_RISK_VALUES
                    or _is_valid_until_expired(valid_until)
                ),
            }
        )
    return pages


def pick_samples(pages: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """四类抽样：多研报同股 / 已有半年报 / 无半年报 / 过期知识。"""
    ok_pages = [p for p in pages if "error" not in p]

    symbol_pages: Dict[str, List[Dict[str, Any]]] = {}
    for p in ok_pages:
        for sym in p["symbols"]:
            symbol_pages.setdefault(sym, []).append(p)

    research_pages = [p for p in ok_pages if not p["is_half_year"]]

    # 1) 多研报同股：非半年报页面 symbol 出现 ≥2 次
    multi_counts = Counter(
        sym for p in research_pages for sym in p["symbols"]
    )
    multi_symbols = [
        sym for sym, _ in multi_counts.most_common() if multi_counts[sym] >= 2
    ][:SAMPLE_PER_CATEGORY]

    # 2) 已有半年报 / 3) 无半年报
    with_hy: List[str] = []
    without_hy: List[str] = []
    for sym, plist in symbol_pages.items():
        if any(p["is_half_year"] for p in plist):
            if sym not in with_hy:
                with_hy.append(sym)
        else:
            if sym not in without_hy and any(not p["is_half_year"] for p in plist):
                without_hy.append(sym)
    with_hy = with_hy[:SAMPLE_PER_CATEGORY]
    without_hy = [s for s in without_hy if s not in with_hy][:SAMPLE_PER_CATEGORY]

    # 4) 过期知识（页面级抽样，取其首个 symbol 做证据查询）
    expired_pages = [p for p in ok_pages if p["expired"]][:SAMPLE_PER_CATEGORY]

    return {
        "multi_report_symbols": multi_symbols,
        "with_half_year_symbols": with_hy,
        "without_half_year_symbols": without_hy,
        "expired_pages": expired_pages,
    }


def _walk_forbidden_keys(node: Any, path: str, hits: List[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            key = str(k).lower()
            if key in FORBIDDEN_ACTION_KEYS:
                hits.append(f"{path}.{k}")
            _walk_forbidden_keys(v, f"{path}.{k}", hits)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_forbidden_keys(v, f"{path}[{i}]", hits)


def _extract_code(symbol_entry: str) -> Optional[str]:
    """从 frontmatter symbol 条目提取 6 位代码（含可选后缀）。"""
    m = _CODE_TOKEN_RE.match(symbol_entry.strip())
    return m.group(1) if m else None


def probe_symbol(symbol: str, knowledge_root: str) -> Dict[str, Any]:
    """对单个 symbol 跑 KB-020 只读聚合并做契约核验。"""
    record: Dict[str, Any] = {"symbol": symbol}
    try:
        evidence = research_evidence_service.build_research_evidence(
            symbol, knowledge_root=knowledge_root
        )
    except Exception as exc:
        record["status"] = "EXCEPTION"
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    buckets = {
        name: evidence.get(name)
        for name in (
            "consensus",
            "citation_audit",
            "thesis_timeline",
            "half_year_facts",
            "research_score_snapshot",
        )
    }
    record["status"] = "OK"
    record["data_status"] = evidence.get("data_status")
    record["buckets"] = {
        name: {
            "has_hit": bool(b.get("has_hit")),
            "data_status": b.get("data_status"),
            "errors": list(b.get("errors") or [])[:2],
        }
        for name, b in buckets.items()
        if isinstance(b, dict)
    }

    # 契约 1：全树不得出现动作语义字段
    forbidden: List[str] = []
    _walk_forbidden_keys(evidence, "$", forbidden)
    record["forbidden_action_keys"] = forbidden[:5]

    # 契约 2：consensus has_hit 时必须携带真实 schema 消费键
    consensus = buckets.get("consensus") or {}
    if consensus.get("has_hit"):
        summary = consensus.get("summary") or {}
        record["consensus_keys_ok"] = all(
            k in summary
            for k in ("consensus_score", "dimensions_brief", "consensus_summary")
        )
    citation = buckets.get("citation_audit") or {}
    if citation.get("has_hit"):
        summary = citation.get("summary") or {}
        record["citation_counts"] = {
            "total": summary.get("total_claim_count"),
            "checked": summary.get("checked_claim_count"),
            "pending": (summary.get("counts") or {}).get("pending"),
        }
    hy = buckets.get("half_year_facts") or {}
    if isinstance(hy, dict):
        record["half_year"] = {
            "has_hit": bool(hy.get("has_hit")),
            "data_status": hy.get("data_status"),
            "page_conflicts": sum(
                1
                for p in (hy.get("summary") or {}).get("pages", [])
                if p.get("data_status") == "conflict"
            ),
        }
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge-root",
        default=os.path.expanduser("~/Documents/knowledge"),
    )
    parser.add_argument(
        "--report",
        default="docs/knowledge_reports/research_mainline_acceptance-"
        + date.today().isoformat()
        + ".md",
    )
    args = parser.parse_args()

    root = Path(args.knowledge_root).expanduser()
    if not root.is_dir():
        # 真实目录不可用：必须失败，不得用 fixture 冒充
        print(f"[V-014] FAIL: 真实知识库目录不可用: {root}", file=sys.stderr)
        return 1

    print(f"[V-014] 只读 smoke 知识库: {root}")
    pages = index_pages(root)
    parse_errors = sum(1 for p in pages if "error" in p)
    print(f"[V-014] 索引页面: {len(pages)}（解析失败 {parse_errors}）")

    samples = pick_samples(pages)
    print(f"[V-014] 抽样: {json.dumps({k: v for k, v in samples.items()}, ensure_ascii=False, default=str)[:400]}")

    symbols_to_probe: List[str] = []
    name_only_skipped = 0
    for sym in samples["multi_report_symbols"]:
        symbols_to_probe.append(sym)
    for sym in samples["with_half_year_symbols"]:
        if sym not in symbols_to_probe:
            symbols_to_probe.append(sym)
    for sym in samples["without_half_year_symbols"]:
        if sym not in symbols_to_probe:
            symbols_to_probe.append(sym)
    for p in samples["expired_pages"]:
        for sym in p["symbols"]:
            if sym not in symbols_to_probe:
                symbols_to_probe.append(sym)
                break

    # frontmatter symbol 可能是 "代码 名称"，聚合器需要纯代码；纯名称
    # （无代码）条目跳过探测并记录
    probes: List[Dict[str, Any]] = []
    for entry in symbols_to_probe:
        code = _extract_code(entry)
        if code is None:
            name_only_skipped += 1
            probes.append(
                {
                    "symbol": entry,
                    "status": "SKIPPED",
                    "error": "frontmatter 条目无 6 位代码（仅名称），无法聚合查询",
                }
            )
            continue
        probes.append(probe_symbol(code, str(root)))

    # 全局统计（全索引页级 + 抽样证据级）
    ok_pages = [p for p in pages if "error" not in p]
    known_source_pages = sum(1 for p in ok_pages if p["source_type_known"])
    separation_rate = (
        known_source_pages / len(ok_pages) if ok_pages else 0.0
    )
    expired_pages = sum(1 for p in ok_pages if p["expired"])
    hy_pages = sum(1 for p in ok_pages if p["is_half_year"])

    total_claims = sum(
        (p.get("citation_counts") or {}).get("total") or 0 for p in probes
    )
    checked_claims = sum(
        (p.get("citation_counts") or {}).get("checked") or 0 for p in probes
    )
    pending_claims = sum(
        (p.get("citation_counts") or {}).get("pending") or 0 for p in probes
    )
    citation_complete_rate = checked_claims / total_claims if total_claims else None
    pending_rate = pending_claims / total_claims if total_claims else None

    hy_probes = [p for p in probes if p.get("half_year")]
    hy_conflict_probes = sum(
        1
        for p in hy_probes
        if p["half_year"]["data_status"] == "conflict"
        or p["half_year"]["page_conflicts"] > 0
    )
    conflict_rate = (
        hy_conflict_probes / len(hy_probes) if hy_probes else None
    )

    # 缓存新鲜度（KB-010 只读构建，不落盘）
    try:
        cache = build_cache_from_scan(str(root))
        cache_freshness = cache.freshness_status
        cache_pages = len(cache.pages)
    except Exception as exc:
        cache_freshness = f"error: {type(exc).__name__}: {exc}"
        cache_pages = -1

    probe_failures = [
        p for p in probes
        if p["status"] not in ("OK", "SKIPPED")
    ]
    skipped_probes = [p for p in probes if p["status"] == "SKIPPED"]
    contract_violations = [
        p for p in probes if p.get("forbidden_action_keys")
    ]

    # ── 生成验收日报 ──
    lines: List[str] = []
    lines.append(f"# 研报主线验收日报（V-014）— {date.today().isoformat()}")
    lines.append("")
    lines.append(f"- 知识库：`{root}`（只读 smoke，未写入任何文件）")
    lines.append(
        f"- 索引页面：{len(pages)}（解析失败 {parse_errors}）；半年报页 "
        f"{hy_pages}；过期页 {expired_pages}"
    )
    lines.append(
        f"- 缓存新鲜度（KB-010 只读构建）：{cache_freshness}"
        f"（{cache_pages} 页）"
    )
    lines.append("")
    lines.append("## 抽样（四类）")
    lines.append("")
    lines.append(
        "- 多研报同股："
        + (", ".join(samples["multi_report_symbols"]) or "（无样本）")
    )
    lines.append(
        "- 已有半年报："
        + (", ".join(samples["with_half_year_symbols"]) or "（无样本）")
    )
    lines.append(
        "- 无半年报："
        + (", ".join(samples["without_half_year_symbols"]) or "（无样本）")
    )
    lines.append(
        "- 过期知识页："
        + (
            ", ".join(p["rel_path"] for p in samples["expired_pages"])
            or "（无样本）"
        )
    )
    lines.append("")
    lines.append("## 核心统计")
    lines.append("")
    lines.append(
        f"- 观点/事实分离覆盖率（source_type 全部落白名单）："
        f"{known_source_pages}/{len(ok_pages)} = {separation_rate:.1%}"
    )
    if citation_complete_rate is not None:
        lines.append(
            f"- citation 完整率（抽样 Σ已核查/Σ总声明）："
            f"{checked_claims}/{total_claims} = {citation_complete_rate:.1%}"
        )
        lines.append(
            f"- 待验证率（抽样 Σpending/Σ总声明）："
            f"{pending_claims}/{total_claims} = {pending_rate:.1%}"
        )
    else:
        lines.append("- citation 完整率：抽样无可审计声明（total=0）")
    if conflict_rate is not None:
        lines.append(
            f"- 事实冲突率（半年报 bucket conflict 或页级冲突）："
            f"{hy_conflict_probes}/{len(hy_probes)} = {conflict_rate:.1%}"
        )
    else:
        lines.append("- 事实冲突率：抽样无半年报命中")
    lines.append("")
    lines.append("## 抽样证据探测（KB-020 只读聚合）")
    lines.append("")
    lines.append("| symbol | 状态 | 聚合 data_status | 契约违例 |")
    lines.append("|---|---|---|---|")
    for p in probes:
        lines.append(
            f"| {p['symbol']} | {p['status']} | {p.get('data_status', '-')} "
            f"| {'; '.join(p.get('forbidden_action_keys') or []) or '无'} |"
        )
    lines.append("")
    lines.append("### 抽样 bucket 明细")
    lines.append("")
    lines.append("| symbol | consensus | citation_audit | thesis_timeline | half_year_facts | score_snapshot |")
    lines.append("|---|---|---|---|---|---|")
    for p in probes:
        b = p.get("buckets") or {}
        if not b:
            lines.append(f"| {p['symbol']} | - | - | - | - | - |")
            continue

        def _cell(name: str) -> str:
            info = b.get(name)
            if not info:
                return "-"
            return f"{info.get('data_status', '-')}/hit={str(info.get('has_hit')).lower()}"

        lines.append(
            f"| {p['symbol']} | {_cell('consensus')} | {_cell('citation_audit')} "
            f"| {_cell('thesis_timeline')} | {_cell('half_year_facts')} "
            f"| {_cell('research_score_snapshot')} |"
        )
    lines.append("")
    lines.append("## 通过项")
    lines.append("")
    passed: List[str] = []
    if root.is_dir():
        passed.append("真实知识库目录可用，只读贯穿完成（未写入任何文件）")
    if not probe_failures:
        passed.append(f"抽样 {len(probes)} 个 symbol 的 KB-020 聚合全部正常返回")
    if not contract_violations:
        passed.append("TA/TradeFlow/IC 契约核验通过：证据树无 decision/action_label/buy_level 等动作字段")
    if cache_freshness in ("fresh", "stale"):
        passed.append(f"KB-010 缓存只读构建可用（freshness={cache_freshness}）")
    for item in passed:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 阻塞项 / Findings")
    lines.append("")
    findings: List[str] = []
    for p in probe_failures:
        findings.append(
            f"`{p['symbol']}` 聚合异常：{p.get('error', 'unknown')}"
        )
    for p in contract_violations:
        findings.append(
            f"`{p['symbol']}` 契约违例：{p.get('forbidden_action_keys')}"
        )
    for p in skipped_probes:
        findings.append(
            f"`{p['symbol']}`：{p.get('error', '')}（知识库侧数据质量问题，"
            f"建议 Tree Work 补代码字段）"
        )
    # 特征化 finding：citation 降级与半年报页缺机读事实（业务缺陷只记录，
    # 不在验收任务中跨范围改代码）
    citation_degraded = sum(
        1
        for p in probes
        if (p.get("buckets") or {}).get("citation_audit", {}).get("data_status")
        == "missing"
    )
    if citation_degraded:
        findings.append(
            f"{citation_degraded}/{len(probes)} 个抽样 symbol 的 citation "
            f"审计降级（半年报事实缺机读数据或状态不可用），审计链路本身"
            f"按契约 fail-closed，无契约违例"
        )
    hy_hit_missing = sum(
        1
        for p in probes
        if (p.get("buckets") or {}).get("half_year_facts", {}).get("has_hit")
        and (p.get("buckets") or {}).get("half_year_facts", {}).get("data_status")
        == "missing"
    )
    if hy_hit_missing:
        findings.append(
            f"{hy_hit_missing} 个抽样 symbol 的半年报页面存在但 "
            f"data_status=missing（页面缺机读 financial_facts），建议知识库"
            f"侧补事实表"
        )
    if parse_errors:
        findings.append(f"{parse_errors} 页 frontmatter 解析失败（见索引）")
    if not findings:
        findings.append("（无）")
    for item in findings:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 下一阶段建议")
    lines.append("")
    lines.append("- 冲突/待验证条目按 KB-017 口径进入 Tree Work 复核队列")
    lines.append("- 过期页建议安排知识库侧刷新（本任务只读，未修改）")
    lines.append("- 统一 review 时以本报告与各任务运行档案为材料")

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[V-014] 验收日报已生成: {report_path}")

    # 退出码：探测异常或契约违例 → 1；否则 0
    if probe_failures or contract_violations:
        print(
            f"[V-014] 存在 {len(probe_failures)} 个探测异常 / "
            f"{len(contract_violations)} 个契约违例",
            file=sys.stderr,
        )
        return 1
    print("[V-014] PASS: 只读 smoke 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
