#!/usr/bin/env python3
"""KB-019 — Tree Work 研报增量摄取清单与重复导入预检 CLI。

只读扫描 ``~/Documents/knowledge/`` 的 inbox / raw / wiki 分区，把新增、已消化、
重复、缺字段、过期、冲突的资料整理为可回查的增量摄取清单，输出 Markdown / JSON。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落；
不写生产 DB；**不自动删除重复**；**不输出买卖建议或强动作词**。

Usage::

    # 默认：Markdown 输出到 stdout
    python scripts/research_ingest_delta.py

    # 指定知识库根目录
    python scripts/research_ingest_delta.py --knowledge-root ~/Documents/knowledge

    # 只看 raw 增量（不含 wiki / inbox）
    python scripts/research_ingest_delta.py --no-inbox --no-wiki

    # 输出结构化 JSON
    python scripts/research_ingest_delta.py --json

    # 输出到文件
    python scripts/research_ingest_delta.py \
        --output docs/knowledge_reports/research-ingest-delta-2026-07-13.md

    # 只打印建议输出路径
    python scripts/research_ingest_delta.py --suggest-output
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.research_ingest_delta import (  # noqa: E402
    ALL_STATUSES,
    TASK_CODE,
    build_research_ingest_delta,
    render_ingest_delta_report,
    suggest_ingest_delta_output_path,
)
from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    default_knowledge_root,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-019 Tree Work 研报增量摄取清单与重复导入预检",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库根目录。默认 $AUTO_DEV_KNOWLEDGE_ROOT 或 ~/Documents/knowledge。",
    )
    parser.add_argument(
        "--no-inbox",
        action="store_true",
        help="不扫描 inbox 分区。",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="不扫描 raw 分区。",
    )
    parser.add_argument(
        "--no-wiki",
        action="store_true",
        help="不扫描 wiki/investment 分区（不输出 wiki stale/needs_metadata 信号）。",
    )
    parser.add_argument(
        "--no-backlog",
        action="store_true",
        help="不调用 KB-005 build_tree_work_backlog 收集上游统计。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出结构化 JSON 而非 Markdown（用于下游程序消费）。",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出路径。默认仅打印到 stdout。",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="同时打印 payload 到 stdout（与 --output 配合）。",
    )
    parser.add_argument(
        "--suggest-output",
        action="store_true",
        help="打印建议的输出路径后退出（不执行扫描）。",
    )
    args = parser.parse_args()

    if args.suggest_output:
        print(suggest_ingest_delta_output_path())
        return 0

    knowledge_root = args.knowledge_root or default_knowledge_root()

    delta = build_research_ingest_delta(
        knowledge_root,
        include_inbox=not args.no_inbox,
        include_raw=not args.no_raw,
        include_wiki=not args.no_wiki,
        use_backlog=not args.no_backlog,
    )

    if args.json:
        payload = json.dumps(delta.to_dict(), ensure_ascii=False, indent=2)
    else:
        payload = render_ingest_delta_report(delta)

    print_to_stdout = args.stdout or not args.output
    if print_to_stdout:
        print(payload)
    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        counts = delta.status_counts()
        counts_str = ", ".join(f"{s}={counts[s]}" for s in ALL_STATUSES)
        print(
            f"[{TASK_CODE}] ingest delta written to {args.output} "
            f"(total={delta.total()}, {counts_str})",
            file=sys.stderr,
        )

    # 退出码：根目录不存在且 0 项时返回 1。
    if delta.total() == 0 and not os.path.isdir(knowledge_root):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
