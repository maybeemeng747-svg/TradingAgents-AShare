#!/usr/bin/env python3
"""KB-003 — Tree Work 本地知识源查询 CLI。

按 symbol / name / theme / tag 只读查询 ``~/Documents/knowledge/wiki/investment/``，
返回 raw_evidence 兼容的结构化结果，并渲染"本地知识补充" Markdown 区块。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落。

KB-010 起支持索引缓存（``--rebuild-cache`` / ``--no-cache`` / ``--cache-path``），
默认会在 ``.cache/knowledge_cache.json`` 落盘一份 manifest + 预解析页面索引，
连续查询复用缓存，仅在知识库文件变更时重建。

Usage::

    python scripts/query_local_knowledge.py --symbol 603296
    python scripts/query_local_knowledge.py --symbol 603296.SH --json
    python scripts/query_local_knowledge.py --theme "AI算力基础设施"
    python scripts/query_local_knowledge.py --name "华勤技术" --output docs/knowledge_reports/local_knowledge_query-2026-07-01.md
    python scripts/query_local_knowledge.py --symbol 603296 --theme "AI服务器" --tag "AI服务器"
    # [KB-010] 强制重建缓存：
    python scripts/query_local_knowledge.py --symbol 603296 --rebuild-cache
    # [KB-010] 完全禁用缓存（每次全量扫描）：
    python scripts/query_local_knowledge.py --symbol 603296 --no-cache
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.local_knowledge_provider import (  # noqa: E402
    query_local_knowledge,
    render_local_knowledge_block,
)
from tradingagents.dataflows.local_knowledge_audit import (  # noqa: E402
    default_knowledge_root,
)
from tradingagents.dataflows.local_knowledge_cache import (  # noqa: E402
    TASK_CODE as KB010_TASK_CODE,
    get_or_build_cache,
    freshness_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-003 Tree Work 本地知识源 raw_evidence 查询",
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
        help="公司/主题简称（与 title/filename 子串匹配）。",
    )
    parser.add_argument(
        "--theme",
        action="append",
        default=None,
        help="主题（可多次传入，与页面 themes 子串匹配）。",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=None,
        help="tag（可多次传入，与页面 tags 集合交集匹配）。",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="最多返回的命中页数。默认 5。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出结构化 JSON 而非 Markdown 区块。",
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
    parser.add_argument(
        "--freshness-only",
        action="store_true",
        help="[KB-010] 只输出缓存 freshness manifest 摘要，不执行查询。",
    )
    args = parser.parse_args()

    if not any([args.symbol, args.name, args.theme, args.tag]) and not args.freshness_only:
        parser.error(
            "至少需要 --symbol / --name / --theme / --tag 中的一个"
            "（或使用 --freshness-only 只查看缓存状态）"
        )

    knowledge_root = args.knowledge_root or default_knowledge_root()

    # [KB-010] local_knowledge_cache — 获取或构建缓存（freshness 仅依赖 manifest）。
    cache = get_or_build_cache(
        knowledge_root,
        cache_path=args.cache_path,
        rebuild=args.rebuild_cache,
        no_cache=args.no_cache,
    )

    if args.freshness_only:
        payload = json.dumps(freshness_summary(cache), ensure_ascii=False, indent=2)
        print(payload)
        return 0 if cache.freshness_status != "error" else 1

    result = query_local_knowledge(
        knowledge_root,
        symbol=args.symbol,
        name=args.name,
        themes=args.theme,
        tags=args.tag,
        max_pages=args.max_pages,
        cache=cache,
    )

    if args.json:
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    else:
        block = render_local_knowledge_block(result)
        if not block:
            payload = (
                f"> [KB-003] status={result.status} — 无可渲染的本地知识补充区块。\n"
            )
            if result.errors:
                payload += ">\n> 错误：\n"
                for err in result.errors[:10]:
                    payload += f"> - {err}\n"
        else:
            payload = block

    # [KB-010] 把 freshness 摘要附到 stderr，不影响 stdout payload 的可解析性。
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
            f"[KB-003] query written to {args.output} "
            f"(status={result.status}, matched_pages={len(result.matched_pages)})",
            file=sys.stderr,
        )

    return 0 if result.status != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

