#!/usr/bin/env python3
"""KB-005 — Tree Work inbox/raw/wiki 对齐与未消化研报清单 CLI。

只读扫描 ``~/Documents/knowledge/``，把 inbox 积压、raw 未消化、wiki 占位/废弃、
index 未同步等缺口，转换为可执行动作清单，输出 Markdown 报告。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不批量读取 PDF 正文；
不输出原文段落。

Usage::

    python scripts/tree_work_backlog.py
    python scripts/tree_work_backlog.py --knowledge-root ~/Documents/knowledge
    python scripts/tree_work_backlog.py --output docs/knowledge_reports/tree_work_ingest_backlog-2026-07-01.md
    python scripts/tree_work_backlog.py --json  # 输出结构化 JSON 而非 Markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.tree_work_backlog import (  # noqa: E402
    build_tree_work_backlog,
    default_knowledge_root,
    render_backlog_report,
    suggest_backlog_output_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-005 Tree Work inbox/raw/wiki 对齐与未消化研报清单",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库根目录。默认 $AUTO_DEV_KNOWLEDGE_ROOT 或 ~/Documents/knowledge。",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 输出路径。默认 docs/knowledge_reports/tree_work_ingest_backlog-YYYY-MM-DD.md。",
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
    args = parser.parse_args()

    knowledge_root = args.knowledge_root or default_knowledge_root()
    backlog = build_tree_work_backlog(knowledge_root)

    if args.json:
        payload = json.dumps(backlog.to_dict(), ensure_ascii=False, indent=2)
    else:
        payload = render_backlog_report(backlog)

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
        counts = backlog.category_counts()
        print(
            f"[KB-005] backlog written to {args.output} "
            f"(inbox={counts['inbox_unprocessed']}, "
            f"raw_undigested={counts['raw_undigested']}, "
            f"wiki_supplement={counts['wiki_to_be_supplemented']}, "
            f"deprecated={counts['wiki_deprecated']}, "
            f"field_gap={counts['wiki_field_gap']}, "
            f"index={counts['index_not_synced']})",
            file=sys.stderr,
        )

    # 退出码：清单总能跑完（即使空库）；只在根目录不存在且 0 项时返回 1。
    total = len(backlog.all_items())
    if total == 0 and not os.path.isdir(knowledge_root):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
