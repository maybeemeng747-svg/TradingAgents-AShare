#!/usr/bin/env python3
# [KB-012] tree_work_task_pack
"""KB-012 — Tree Work 研报补录任务包导出 CLI。

只读合并 KB-002 lint、KB-005 backlog、KB-007/009 关注度信号，导出为可执行的
Tree Work 补录任务包（Markdown / JSON）。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落；
不输出交易建议或强动作词。

Usage::

    python scripts/tree_work_task_pack.py
    python scripts/tree_work_task_pack.py --knowledge-root ~/Documents/knowledge
    python scripts/tree_work_task_pack.py --output docs/knowledge_reports/tree_work_task_pack-2026-07-05.md
    python scripts/tree_work_task_pack.py --json  # 输出结构化 JSON 而非 Markdown
    python scripts/tree_work_task_pack.py --no-attention  # 跳过 KB-007/009 信号
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.tree_work_task_pack import (  # noqa: E402
    build_tree_work_task_pack,
    render_task_pack_report,
    suggest_task_pack_output_path,
)
from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    default_knowledge_root,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-012 Tree Work 研报补录任务包导出",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库根目录。默认 $AUTO_DEV_KNOWLEDGE_ROOT 或 ~/Documents/knowledge。",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 输出路径。默认 docs/knowledge_reports/tree_work_task_pack-YYYY-MM-DD.md。",
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
        "--no-lint",
        action="store_true",
        help="跳过 KB-002 lint 信号收集。",
    )
    parser.add_argument(
        "--no-backlog",
        action="store_true",
        help="跳过 KB-005 backlog 信号收集。",
    )
    parser.add_argument(
        "--no-attention",
        action="store_true",
        help="跳过 KB-007/009 研究关注度信号收集。",
    )
    args = parser.parse_args()

    knowledge_root = args.knowledge_root or default_knowledge_root()
    pack = build_tree_work_task_pack(
        knowledge_root,
        collect_lint=not args.no_lint,
        collect_backlog=not args.no_backlog,
        collect_attention=not args.no_attention,
    )

    if args.json:
        payload = json.dumps(pack.to_dict(), ensure_ascii=False, indent=2)
    else:
        payload = render_task_pack_report(pack)

    # 默认：未指定 --output 时打印 payload 到 stdout。
    print_to_stdout = args.stdout or not args.output
    if print_to_stdout:
        print(payload)
    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        counts = pack.group_counts()
        print(
            f"[KB-012] task pack written to {args.output} "
            f"(total={pack.total()}, "
            f"missing_symbol={counts.get('missing_symbol', 0)}, "
            f"missing_thesis={counts.get('missing_thesis', 0)}, "
            f"missing_risks={counts.get('missing_risks', 0)}, "
            f"missing_sources={counts.get('missing_sources', 0)}, "
            f"needs_review_stale={counts.get('needs_review_stale', 0)}, "
            f"hot_but_thin={counts.get('hot_but_thin', 0)}, "
            f"ingest_new={counts.get('ingest_new', 0)})",
            file=sys.stderr,
        )

    # 退出码：任务包总能跑完（即使空库）；只在根目录不存在且 0 项时返回 1。
    if pack.total() == 0 and not os.path.isdir(knowledge_root):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
