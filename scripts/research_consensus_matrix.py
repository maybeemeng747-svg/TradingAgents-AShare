#!/usr/bin/env python3
"""KB-016 — 多研报一致性/分歧矩阵与关注度去重回放 CLI。

把 Tree Work 已消化研报中的「观点/事实/预测/风险」按 symbol 聚合成共识/分歧矩阵。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落；
不写生产 DB；**不输出买卖建议或强动作词**。

Usage::

    # 按 symbol 查询（默认 12 个月窗口）
    python scripts/research_consensus_matrix.py --symbol 603296
    python scripts/research_consensus_matrix.py --symbol 603296.SH --json

    # 按 name 查询
    python scripts/research_consensus_matrix.py --name "华勤技术"

    # 全库索引模式（不限 symbol/name，扫描所有 investment 页）
    python scripts/research_consensus_matrix.py --all

    # 指定时间窗口（3/6/12 月）
    python scripts/research_consensus_matrix.py --symbol 603296 --window 6

    # 只列出需要事实反证的候选
    python scripts/research_consensus_matrix.py --all --needs-fact-check

    # 输出到文件
    python scripts/research_consensus_matrix.py --symbol 603296 \
        --output docs/knowledge_reports/research_consensus_matrix-2026-07-12.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.research_consensus_matrix import (  # noqa: E402
    TASK_CODE,
    build_research_consensus_matrix,
    matrix_to_ta_consumable_summary,
    render_research_consensus_matrix_report,
    suggest_report_output_path,
)
from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    default_knowledge_root,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-016 多研报一致性/分歧矩阵与关注度去重回放",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库根目录。默认 $AUTO_DEV_KNOWLEDGE_ROOT 或 ~/Documents/knowledge。",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="A 股代码或代码+后缀（如 603296 / 603296.SH）。",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="公司/主题简称（与 title/symbols 简称子串匹配）。",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="全库索引模式（不限 symbol/name，扫描所有 investment 页）。",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=12,
        choices=(3, 6, 12),
        help="时间窗口（月）。默认 12。",
    )
    parser.add_argument(
        "--needs-fact-check",
        action="store_true",
        help="只列出 needs_fact_check=True 的候选。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出结构化 JSON 而非 Markdown 报告。",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="输出 TA 可消费的扁平摘要 JSON（matrix_to_ta_consumable_summary）。",
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
        help="打印建议的输出路径后退出（不执行查询）。",
    )
    args = parser.parse_args()

    if args.suggest_output:
        print(suggest_report_output_path())
        return 0

    if not args.all and not any([args.symbol, args.name]):
        parser.error(
            "需要 --symbol / --name 中的一个，或使用 --all 全库索引模式"
        )

    knowledge_root = args.knowledge_root or default_knowledge_root()

    result = build_research_consensus_matrix(
        knowledge_root,
        symbol=args.symbol,
        name=args.name,
        window_months=args.window,
    )

    if args.needs_fact_check:
        # 过滤后只保留 needs_fact_check=True 的 symbol。
        result.symbols = [
            s for s in result.symbols if s.needs_fact_check
        ]
        result.needs_fact_check_symbols = [
            s.symbol_key for s in result.symbols
        ]

    if args.json:
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    elif args.summary:
        if len(result.symbols) == 1:
            payload = json.dumps(
                matrix_to_ta_consumable_summary(result.symbols[0]),
                ensure_ascii=False, indent=2,
            )
        else:
            payload = json.dumps(
                {
                    "status": result.status,
                    "symbols": [
                        matrix_to_ta_consumable_summary(s)
                        for s in result.symbols
                    ],
                },
                ensure_ascii=False, indent=2,
            )
    else:
        payload = render_research_consensus_matrix_report(result)

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
            f"[{TASK_CODE}] matrix written to {args.output} "
            f"(status={result.status}, symbols={len(result.symbols)}, "
            f"needs_fact_check={len(result.needs_fact_check_symbols)})",
            file=sys.stderr,
        )

    return 0 if result.status != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
