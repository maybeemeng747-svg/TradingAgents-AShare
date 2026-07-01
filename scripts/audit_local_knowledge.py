#!/usr/bin/env python3
"""KB-001 — Tree Work 本地知识库只读索引与健康审计 CLI。

只读扫描 ``~/Documents/knowledge/``，统计 wiki/investment、inbox、raw、index/log
覆盖情况，识别页面类型与结构缺口，输出 Markdown 审计报告。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落。

Usage::

    python scripts/audit_local_knowledge.py
    python scripts/audit_local_knowledge.py --knowledge-root ~/Documents/knowledge
    python scripts/audit_local_knowledge.py --output docs/knowledge_reports/local_knowledge_audit-2026-07-01.md
    python scripts/audit_local_knowledge.py --json  # 输出结构化 JSON 而非 Markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    audit_local_knowledge,
    default_knowledge_root,
    render_audit_report,
    suggest_audit_output_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-001 Tree Work 本地知识库只读审计",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库根目录。默认 $AUTO_DEV_KNOWLEDGE_ROOT 或 ~/Documents/knowledge。",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 输出路径。默认 docs/knowledge_reports/local_knowledge_audit-YYYY-MM-DD.md。",
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
    result = audit_local_knowledge(knowledge_root)

    if args.json:
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    else:
        payload = render_audit_report(result)

    # 默认：未指定 --output 时打印 payload 到 stdout。
    # --output: 写文件 + stderr 摘要；--stdout 强制同时打印 payload。
    print_to_stdout = args.stdout or not args.output
    if print_to_stdout:
        print(payload)
    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print(
            f"[KB-001] audit written to {args.output} "
            f"(investment_pages={result.investment_page_count}, "
            f"inbox_items={result.inbox_item_count}, "
            f"structural_gaps={len(result.structural_gaps)})",
            file=sys.stderr,
        )

    # 退出码：审计总能跑完（即使空库），缺口是预期产物；只在根目录不存在且 0 页时返回 1。
    if result.investment_page_count == 0 and result.inbox_item_count == 0 and result.raw_md_count == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
