#!/usr/bin/env python3
# [DATA-023] source_capability_matrix
"""导出 A 股数据源能力矩阵（Markdown + JSON）。

完全静态导出，不调用任何 live API，不读取任何 API Key。

用法：
    python scripts/export_source_capability_matrix.py
    python scripts/export_source_capability_matrix.py --json docs/source_capability_matrix.json
    python scripts/export_source_capability_matrix.py --md docs/SOURCE_CAPABILITY_MATRIX.md
    python scripts/export_source_capability_matrix.py --stdout-json
    python scripts/export_source_capability_matrix.py --validate

默认行为：在仓库根目录写 docs/SOURCE_CAPABILITY_MATRIX.md，
并把 JSON 打印到 stdout（如果没传 --json 也没传 --stdout-json，则只写 MD）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MD = ROOT / "docs" / "SOURCE_CAPABILITY_MATRIX.md"
DEFAULT_JSON = ROOT / "docs" / "source_capability_matrix.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="导出 A 股数据源能力矩阵（Markdown + JSON）。",
    )
    parser.add_argument(
        "--md",
        default=str(DEFAULT_MD),
        help=f"Markdown 输出路径（默认 {DEFAULT_MD}，传空字符串跳过）",
    )
    parser.add_argument(
        "--json",
        default="",
        help=f"JSON 输出路径（默认不写文件，需显式指定路径，例如 {DEFAULT_JSON}）",
    )
    parser.add_argument(
        "--stdout-json",
        action="store_true",
        help="把 JSON 打印到 stdout（便于管道处理）",
    )
    parser.add_argument(
        "--stdout-text",
        action="store_true",
        help="把紧凑文本打印到 stdout（便于塞进 agent 上下文）",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="只跑覆盖率校验，不写文件",
    )
    parser.add_argument(
        "--no-md",
        action="store_true",
        help="不写默认 Markdown 文件",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from tradingagents.dataflows.source_capability_matrix import (
        get_source_capability_matrix,
        render_source_capability_matrix_markdown,
        render_source_capability_matrix_text,
        validate_matrix_coverage,
    )

    matrix = get_source_capability_matrix()

    if args.validate:
        issues = validate_matrix_coverage(matrix)
        if issues:
            print("VALIDATION FAILED:")
            for issue in issues:
                print(f"  - {issue}")
            return 1
        print(
            f"OK: matrix covers all {len(matrix['items'])} data_types, "
            "no coverage issues."
        )
        return 0

    # 写 Markdown
    md_paths: list[Path] = []
    if not args.no_md and args.md:
        md_path = Path(args.md).expanduser().resolve()
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            render_source_capability_matrix_markdown(matrix),
            encoding="utf-8",
        )
        md_paths.append(md_path)
    if args.json:
        json_path = Path(args.json).expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(matrix, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_paths.append(json_path)

    if args.stdout_json:
        print(json.dumps(matrix, ensure_ascii=False, indent=2))
    if args.stdout_text:
        print(render_source_capability_matrix_text(matrix))

    # 简短人类可读总结（走 stderr，避免污染 stdout-json 的管道解析）
    if md_paths:
        for p in md_paths:
            print(f"wrote: {p}", file=sys.stderr)
    print(
        f"data_types={len(matrix['items'])} version={matrix['version']} "
        f"validation_issues={len(validate_matrix_coverage(matrix))}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
