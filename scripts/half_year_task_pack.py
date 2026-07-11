#!/usr/bin/env python3
# [HY-002] half_year_task_pack
"""HY-002 — 半年报资料优先队列与 Tree Work 补录任务包 CLI。

只读合并持仓/观察仓/候选/研报关注度信号，基于 HY-001 契约生成半年报补录
优先队列，导出为可执行的 Tree Work 任务包（Markdown / JSON）。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落；
不输出交易建议或强动作词。

输入来源（全部可选）：
  - ``--holdings``：逗号分隔的 symbol 列表（如 ``603296,000977``）。
  - ``--observation``：逗号分隔的 symbol 列表。
  - ``--haotian``：逗号分隔的 symbol 列表。
  - ``--tradeflow``：逗号分隔的 symbol 列表。
  - ``--from-db``：从 TradeFlow DB 自动读取持仓/观察仓/候选（只读）。
  - 如不提供任何输入，只生成 KB-007 stale attention 层的任务。

Usage::

    python scripts/half_year_task_pack.py
    python scripts/half_year_task_pack.py --holdings 603296,000977 --observation 002415
    python scripts/half_year_task_pack.py --from-db
    python scripts/half_year_task_pack.py --output docs/knowledge_reports/half_year_tree_work_tasks-2026-07-11.md
    python scripts/half_year_task_pack.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.half_year_task_pack import (  # noqa: E402
    build_half_year_task_pack,
    render_half_year_task_pack_report,
    suggest_output_path,
)
from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    default_knowledge_root,
)


def _parse_symbol_list(raw: str) -> list[dict[str, str]]:
    """逗号分隔的 symbol 列表 → [{symbol, name}, ...]。"""
    if not raw:
        return []
    result: list[dict[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if " " in part:
            sym, name = part.split(maxsplit=1)
            result.append({"symbol": sym.strip(), "name": name.strip()})
        else:
            result.append({"symbol": part, "name": ""})
    return result


def _collect_from_db() -> dict[str, list[dict[str, str]]]:
    """从 TradeFlow DB 只读读取持仓/观察仓/候选（失败时降级为空）。"""
    result: dict[str, list[dict[str, str]]] = {
        "holdings": [],
        "observation": [],
        "tradeflow_candidates": [],
    }
    try:
        from api.services.tradeflow_service import (
            get_candidates,
            get_data_health,
            get_observation_items,
        )
    except Exception:
        return result

    # 观察仓
    try:
        obs = get_observation_items(status=None, include_removed=False)
        if isinstance(obs, dict):
            for item in obs.get("items", []):
                sym = item.get("symbol") or ""
                name = item.get("name") or ""
                if sym:
                    result["observation"].append({"symbol": sym, "name": name})
    except Exception:
        pass

    # TradeFlow 候选
    try:
        health = get_data_health()
        trade_date = (
            health.get("latest_effective_trade_date")
            or health.get("latest_candidates_date")
            or health.get("latest_plan_date")
        )
        if trade_date:
            cand = get_candidates(trade_date)
            if isinstance(cand, dict):
                for c in cand.get("candidates", []):
                    sym = c.get("symbol") or ""
                    name = c.get("name") or ""
                    if sym:
                        result["tradeflow_candidates"].append(
                            {"symbol": sym, "name": name}
                        )
    except Exception:
        pass

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HY-002 半年报资料优先队列与 Tree Work 补录任务包",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库根目录。默认 $AUTO_DEV_KNOWLEDGE_ROOT 或 ~/Documents/knowledge。",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 输出路径。默认 docs/knowledge_reports/half_year_tree_work_tasks-YYYY-MM-DD.md。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出结构化 JSON 而非 Markdown（用于下游程序消费）。",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="只打印到 stdout，不写文件（与 --output 互斥）。",
    )
    parser.add_argument(
        "--holdings",
        default=None,
        help="逗号分隔的持仓 symbol 列表（如 603296,000977）。",
    )
    parser.add_argument(
        "--observation",
        default=None,
        help="逗号分隔的观察仓 symbol 列表。",
    )
    parser.add_argument(
        "--haotian",
        default=None,
        help="逗号分隔的昊天候选 symbol 列表。",
    )
    parser.add_argument(
        "--tradeflow",
        default=None,
        help="逗号分隔的 TradeFlow 候选 symbol 列表。",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="从 TradeFlow DB 自动读取观察仓/候选（只读）。",
    )
    parser.add_argument(
        "--no-attention",
        action="store_true",
        help="跳过 KB-007 研究关注度信号收集。",
    )
    args = parser.parse_args()

    knowledge_root = args.knowledge_root or default_knowledge_root()

    holdings = _parse_symbol_list(args.holdings) if args.holdings else None
    observation = (
        _parse_symbol_list(args.observation) if args.observation else None
    )
    haotian = _parse_symbol_list(args.haotian) if args.haotian else None
    tradeflow = (
        _parse_symbol_list(args.tradeflow) if args.tradeflow else None
    )

    if args.from_db:
        db_data = _collect_from_db()
        if not holdings:
            holdings = db_data.get("holdings") or None
        if not observation:
            observation = db_data.get("observation") or None
        if not tradeflow:
            tradeflow = db_data.get("tradeflow_candidates") or None

    pack = build_half_year_task_pack(
        knowledge_root,
        holdings=holdings,
        observation=observation,
        haotian_candidates=haotian,
        tradeflow_candidates=tradeflow,
        collect_attention=not args.no_attention,
    )

    if args.json:
        payload = json.dumps(pack.to_dict(), ensure_ascii=False, indent=2)
    else:
        payload = render_half_year_task_pack_report(pack)

    print_to_stdout = args.stdout or not args.output
    if print_to_stdout:
        print(payload)
    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        counts = pack.tier_counts()
        print(
            f"[HY-002] task pack written to {args.output} "
            f"(total={pack.total()}, "
            f"holdings={counts.get('P1_HOLDINGS', 0)}, "
            f"observation={counts.get('P2_OBSERVATION', 0)}, "
            f"haotian={counts.get('P3_HAOTIAN', 0)}, "
            f"tradeflow={counts.get('P4_TRADEFLOW', 0)}, "
            f"stale_attention={counts.get('P5_STALE_ATTENTION', 0)})",
            file=sys.stderr,
        )

    if pack.total() == 0 and not os.path.isdir(knowledge_root):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
