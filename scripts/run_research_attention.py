#!/usr/bin/env python3
"""KB-007 — 多研报重复提及因子 Research Attention Score CLI。

只读扫描 ``~/Documents/knowledge/``，建立 symbol 倒排索引，合成
research_attention_score，输出 Markdown / JSON 报告。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落；
不构成买卖建议或强动作词。

Usage::

    python scripts/run_research_attention.py
    python scripts/run_research_attention.py --knowledge-root ~/Documents/knowledge
    python scripts/run_research_attention.py --output docs/knowledge_reports/research_attention-2026-07-01.md
    python scripts/run_research_attention.py --json  # 输出结构化 JSON 而非 Markdown
    python scripts/run_research_attention.py --stdout  # 只打印到 stdout，不写文件
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.research_attention import (  # noqa: E402
    compute_research_attention,
    render_research_attention_report,
)
from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    default_knowledge_root,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-007 多研报重复提及因子 Research Attention Score",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库根目录。默认 $AUTO_DEV_KNOWLEDGE_ROOT 或 ~/Documents/knowledge。",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 输出路径。默认 docs/knowledge_reports/research_attention-YYYY-MM-DD.md。",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="JSON 输出路径。默认 docs/knowledge_reports/research_attention-YYYY-MM-DD.json。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="stdout 输出结构化 JSON 而非 Markdown（用于下游程序消费）。",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="只打印到 stdout，不写文件（与 --output 互斥）。",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Markdown 报告 Top 榜与详细明细上限（默认 30 / 15）。",
    )
    args = parser.parse_args()

    knowledge_root = args.knowledge_root or default_knowledge_root()
    result = compute_research_attention(knowledge_root)

    # 决定 payload：--json 时 stdout 输出 JSON；否则输出 Markdown。
    if args.json:
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    else:
        top = args.top if args.top is not None else 30
        detail = min(args.top, 15) if args.top is not None else 15
        payload = render_research_attention_report(result, top=top, detail=detail)

    print_to_stdout = args.stdout or (not args.output and not args.json_output)
    if print_to_stdout:
        print(payload)

    # 落盘只在显式指定 --output / --json-output 时发生，与 KB-001/KB-002 CLI
    # 风格一致（默认只打印 stdout，不自动写 docs，避免测试/CI 误污染）。
    for path, body in (
        (args.output, payload if not args.json else None),
        (
            args.json_output,
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
            if not args.json
            else None,
        ),
    ):
        if not path or body is None:
            continue
        out_dir = os.path.dirname(path)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    if args.output:
        print(
            f"[KB-007] markdown written to {args.output} "
            f"(symbols={result.symbol_count}, "
            f"pages_with_symbols={result.page_with_symbols_count})",
            file=sys.stderr,
        )
    if args.json_output:
        print(
            f"[KB-007] json written to {args.json_output}",
            file=sys.stderr,
        )

    # 退出码：缺知识库或 0 命中时返回 1，便于 CI/调度识别空跑。
    if not result.symbols and not result.investment_page_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
