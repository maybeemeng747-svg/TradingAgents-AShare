#!/usr/bin/env python3
"""KB-015 — 研报观点事实分离与 TA 可消费摘要索引 CLI。

把 Tree Work 已消化研报中的「观点 / 事实 / 预测 / 风险 / 待验证事项」拆成
TA 可消费的结构化摘要索引。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落；
不写生产 DB。

支持 KB-010 索引缓存（``--rebuild-cache`` / ``--no-cache`` / ``--cache-path``）。

Usage::

    # 按 symbol 查询
    python scripts/research_fact_opinion_index.py --symbol 603296
    python scripts/research_fact_opinion_index.py --symbol 603296.SH --json

    # 按 name 查询
    python scripts/research_fact_opinion_index.py --name "华勤技术"

    # 全库索引模式（不限 symbol/name，扫描全部 investment 页）
    python scripts/research_fact_opinion_index.py --all

    # 输出到文件
    python scripts/research_fact_opinion_index.py --symbol 603296 \
        --output docs/knowledge_reports/research_fact_opinion_index-2026-07-11.md

    # [KB-010] 强制重建缓存 / 禁用缓存
    python scripts/research_fact_opinion_index.py --symbol 603296 --rebuild-cache
    python scripts/research_fact_opinion_index.py --symbol 603296 --no-cache
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.research_fact_opinion_index import (  # noqa: E402
    TASK_CODE,
    build_research_fact_opinion_index,
    render_research_fact_opinion_report,
    suggest_report_output_path,
    to_ta_consumable_summary,
)
from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    default_knowledge_root,
)
from tradingagents.dataflows.local_knowledge_cache import (  # noqa: E402
    TASK_CODE as KB010_TASK_CODE,
    freshness_summary,
    get_or_build_cache,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-015 研报观点事实分离与 TA 可消费摘要索引",
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
        help="全库索引模式（不限 symbol/name，扫描全部 investment 页）。",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="最多返回的命中页数。默认 10。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出结构化 JSON 而非 Markdown 报告。",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="输出 TA 可消费的扁平摘要 JSON（to_ta_consumable_summary）。",
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
    # [KB-010] local_knowledge_cache — 缓存控制参数。
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="[KB-010] 强制重建索引缓存（忽略落盘缓存，重建后写回）。",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="[KB-010] 完全禁用缓存（内存构建，不读/不写落盘）。",
    )
    parser.add_argument(
        "--cache-path",
        default=None,
        help="[KB-010] 缓存文件路径。默认 .cache/knowledge_cache.json。",
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

    # [KB-010] 获取或构建缓存。
    cache = get_or_build_cache(
        knowledge_root,
        cache_path=args.cache_path,
        rebuild=args.rebuild_cache,
        no_cache=args.no_cache,
    )

    result = build_research_fact_opinion_index(
        knowledge_root,
        symbol=args.symbol,
        name=args.name,
        cache=cache,
        max_pages=args.max_pages,
    )

    if args.json:
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    elif args.summary:
        payload = json.dumps(
            to_ta_consumable_summary(result), ensure_ascii=False, indent=2
        )
    else:
        payload = render_research_fact_opinion_report(result)

    # [KB-010] freshness 摘要附到 stderr。
    fr = freshness_summary(cache)
    print(
        f"[{KB010_TASK_CODE}] freshness={fr['freshness_status']} "
        f"page_count={fr['page_count']} reused_disk_cache={fr['reused_disk_cache']}",
        file=sys.stderr,
    )

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
            f"[{TASK_CODE}] index written to {args.output} "
            f"(status={result.status}, pages={len(result.pages)})",
            file=sys.stderr,
        )

    return 0 if result.status != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
