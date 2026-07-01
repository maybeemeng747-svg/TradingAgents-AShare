#!/usr/bin/env python3
# [KB-002] local_knowledge_contract
"""KB-002 — investment wiki 输出协议 lint CLI。

只读 lint ``~/Documents/knowledge/wiki/investment`` 下每篇页面是否符合 TA 可消费
契约（必填字段、推荐机器字段、必含章节、评分表表头、待补充/低置信标记），输出
每页 ``machine_readiness``（high/medium/low）与可执行的修复建议。

**只读**：绝不向知识库写文件；不调用 LLM；不访问外网；不输出原文段落。
**不阻塞 TA**：低分页面只降低置信度，lint 默认退出码为 0（除非根目录不存在）。

Usage::

    python scripts/lint_local_knowledge.py
    python scripts/lint_local_knowledge.py --knowledge-root ~/Documents/knowledge
    python scripts/lint_local_knowledge.py --output docs/knowledge_reports/local_knowledge_lint-2026-07-01.md
    python scripts/lint_local_knowledge.py --json          # 结构化 JSON
    python scripts/lint_local_knowledge.py --fail-on-error  # 有 error finding 时返回非零（CI 门禁）
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.local_knowledge_lint import (  # noqa: E402
    SEVERITY_ERROR,
    default_knowledge_root,
    lint_local_knowledge,
    render_lint_report,
    suggest_lint_output_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KB-002 investment wiki 输出协议 lint（TA 可消费字段检查）",
    )
    parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识库根目录。默认 $AUTO_DEV_KNOWLEDGE_ROOT 或 ~/Documents/knowledge。",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 输出路径。默认 docs/knowledge_reports/local_knowledge_lint-YYYY-MM-DD.md。",
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
        "--fail-on-error",
        action="store_true",
        help="有 error 级 finding 时返回退出码 2（默认不阻塞，返回 0）。用于 CI 门禁。",
    )
    args = parser.parse_args()

    knowledge_root = args.knowledge_root or default_knowledge_root()
    result = lint_local_knowledge(knowledge_root)

    if args.json:
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    else:
        payload = render_lint_report(result)

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
        print(
            f"[KB-002] lint written to {args.output} "
            f"(pages={result.page_count}, "
            f"readiness={result.readiness_counts}, "
            f"errors={result.findings_by_severity.get(SEVERITY_ERROR, 0)})",
            file=sys.stderr,
        )

    # 退出码策略（KB-002 验收要求：低分页面不阻塞 TA）：
    #   - 默认：即使有 error finding 也返回 0，lint 只是建议性工具。
    #   - 根目录不存在且 0 页：返回 1（环境异常）。
    #   --fail-on-error：有 error finding 返回 2（仅 CI 门禁场景）。
    if result.page_count == 0 and not result.errors:
        # 空知识库不算失败（fixture 场景），返回 0。
        return 0
    if result.errors and result.page_count == 0:
        return 1
    if args.fail_on_error and result.findings_by_severity.get(SEVERITY_ERROR, 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
