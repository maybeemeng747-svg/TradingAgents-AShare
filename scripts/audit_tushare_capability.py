"""TA-TUSHARE-2000-001A: real Tushare 2000-point permission matrix audit.

Runs one minimal, single-stock, low-frequency probe per endpoint listed in
the task scope and writes a sanitized permission matrix (JSON + Markdown)
plus a run receipt into a ``docs/task_runs/TA-TUSHARE-2000-001A-*``
archive.  The Tushare token is read exclusively from the git-ignored
``.env`` file; every artifact only records ``HAS_KEY``/``NO_KEY``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from tradingagents.dataflows.tushare_capability import (
    ENDPOINT_SPECS,
    EXCLUDED_ENDPOINTS,
    TASK_ID,
    TOKEN_STATUS_HAS_KEY,
    TOKEN_STATUS_NO_KEY,
    annotate_row_cap_hits,
    build_matrix,
    not_queried_record,
    probe_endpoint,
    render_markdown,
    sanitize_error_text,
)

DEFAULT_SYMBOL = "603629"
DEFAULT_PROBE_DATE = "20260814"
DEFAULT_FALLBACK_DATE = "20260813"
DEFAULT_WINDOW_START = "20260801"
DEFAULT_STATEMENT_START = "20250101"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "tushare_capability"
MATRIX_JSON_NAME = "tushare_permission_matrix.json"
MATRIX_MD_NAME = "tushare_permission_matrix.md"
RECEIPT_NAME = "run-receipt.md"


def exchange_for_ts_code(ts_code: str) -> str:
    suffix = ts_code.rsplit(".", 1)[-1].upper()
    if suffix == "SZ":
        return "SZSE"
    return "SSE"


def normalize_symbol(symbol: str) -> str:
    from tradingagents.dataflows.providers.cn_tushare_provider import _normalize_ts_code

    return _normalize_ts_code(symbol)


def build_probe_context(args: argparse.Namespace) -> dict[str, str]:
    ts_code = normalize_symbol(args.symbol)
    return {
        "ts_code": ts_code,
        "exchange": exchange_for_ts_code(ts_code),
        "probe_date": args.probe_date,
        "fallback_date": args.fallback_date,
        "window_start": args.window_start,
        "statement_start": args.statement_start,
    }


def state_evidence_from_fixtures() -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    if not FIXTURES_DIR.is_dir():
        return evidence
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        state = payload.get("expected_state")
        if state:
            evidence[str(state)] = {
                "fixture": f"tests/fixtures/tushare_capability/{path.name}",
                "fixture_kind": payload.get("fixture_kind", "sanitized"),
            }
    return evidence


def make_query_fn(token: str):
    import tushare as ts

    client = ts.pro_api(token)

    def query(endpoint: str, **kwargs: Any):
        return client.query(endpoint, **kwargs)

    return query


def scan_text_for_secret(text: str, token: str) -> bool:
    if token and token in text:
        return True
    lowered = text.lower()
    return "authorization:" in lowered or "cookie:" in lowered


def run_probes(query_fn, probe_context: Mapping[str, str], *, token: str, sleep_seconds: float):
    records = []
    for index, spec in enumerate(ENDPOINT_SPECS):
        if index:
            time.sleep(sleep_seconds)
        try:
            record = probe_endpoint(query_fn, spec, probe_context, token=token)
        except Exception as exc:
            record = not_queried_record(
                spec,
                probe_context,
                f"probe runner unexpected failure: {sanitize_error_text(exc, token)}",
            )
        records.append(record)
        print(
            f"[probe] {record['endpoint']:<20} {record['state']:<18} "
            f"rows={record.get('row_count') if record.get('row_count') is not None else '-'} "
            f"attempts={record.get('attempts', 1)}",
            flush=True,
        )
    return records


def write_outputs(matrix: dict[str, Any], output_dir: Path, *, token: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / MATRIX_JSON_NAME
    md_path = output_dir / MATRIX_MD_NAME
    receipt_path = output_dir / RECEIPT_NAME
    json_path.write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    markdown = render_markdown(matrix)
    md_path.write_text(markdown, encoding="utf-8")
    scanned = [json_path, md_path]
    clean = not any(scan_text_for_secret(path.read_text(encoding="utf-8"), token) for path in scanned)
    retry_lines = [
        f"- `{record['endpoint']}`：attempts={record['attempts']}，reason=`{record['retry_reason']}`"
        for record in matrix["endpoints"]
        if record.get("attempts", 1) > 1
    ]
    receipt = [
        f"# Run receipt — {TASK_ID}",
        "",
        f"- 运行时间：{matrix['generated_at']}",
        f"- Token 来源：git-ignored `.env`（TUSHARE_TOKEN），状态 `{matrix['token_status']}`，"
        "任何产物不包含 Token 明文",
        f"- 探测标的：`{matrix['probe']['symbol']}`；探测日 `{matrix['probe']['probe_date']}`；"
        f"回退日 `{matrix['probe']['fallback_date']}`",
        f"- 实测 endpoint 数：{matrix['summary']['probed_endpoints']}；"
        f"状态分布：{json.dumps(matrix['summary']['by_state'], ensure_ascii=False)}",
        "",
        "## 重试记录",
        "",
        *(retry_lines or ["- 无重试"]),
        "",
        "## 凭据自检",
        "",
        f"- 对 {MATRIX_JSON_NAME} 与 {MATRIX_MD_NAME} 扫描 Token/Authorization/Cookie："
        f"{'clean' if clean else 'LEAK DETECTED'}",
        "- 本回执与矩阵均不含真实响应正文，只含结构化元数据与 SHA-256 摘要",
        "",
    ]
    receipt_path.write_text("\n".join(receipt), encoding="utf-8")
    return [*scanned, receipt_path]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tushare 2000-point real permission matrix audit")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--probe-date", default=DEFAULT_PROBE_DATE)
    parser.add_argument("--fallback-date", default=DEFAULT_FALLBACK_DATE)
    parser.add_argument("--window-start", default=DEFAULT_WINDOW_START)
    parser.add_argument("--statement-start", default=DEFAULT_STATEMENT_START)
    parser.add_argument("--sleep-seconds", type=float, default=0.6)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="default: docs/task_runs/TA-TUSHARE-2000-001A-<YYYYMMDD-HHMMSS>",
    )
    parser.add_argument(
        "--no-dotenv",
        action="store_true",
        help="跳过 .env 加载（仅供测试隔离使用；Token 只能来自环境）",
    )
    parser.add_argument("--dry-run", action="store_true", help="打印探测计划，不发起网络请求")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if not args.no_dotenv:
        load_dotenv(dotenv_path if dotenv_path is not None else ROOT / ".env", override=False)
    import os

    env = os.environ if environ is None else environ
    token = str(env.get("TUSHARE_TOKEN", "") or "").strip()
    token_status = TOKEN_STATUS_HAS_KEY if token else TOKEN_STATUS_NO_KEY
    probe_context = build_probe_context(args)
    if token_status == TOKEN_STATUS_NO_KEY:
        print("[audit] TUSHARE_TOKEN NO_KEY：拒绝探测（fail closed），Token 只允许来自 git-ignored .env")
        return 2
    if args.dry_run:
        print(f"[audit] token={token_status} symbol={probe_context['ts_code']} probe_date={args.probe_date}")
        for spec in ENDPOINT_SPECS:
            print(f"[dry-run] {spec.endpoint:<20} category={spec.category:<10} kinds={spec.param_kinds}")
        print(f"[dry-run] excluded items: {len(EXCLUDED_ENDPOINTS)}")
        return 0
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else ROOT / "docs" / "task_runs" / f"{TASK_ID}-{stamp}"
    )
    print(
        f"[audit] token={token_status} symbol={probe_context['ts_code']} "
        f"probe_date={args.probe_date} output={output_dir}"
    )
    query_fn = make_query_fn(token)
    records = run_probes(query_fn, probe_context, token=token, sleep_seconds=args.sleep_seconds)
    matrix = build_matrix(
        probe_context,
        records,
        token_status=token_status,
        generated_at=started,
        state_evidence=state_evidence_from_fixtures(),
        notes=[
            f"探测脚本：scripts/audit_tushare_capability.py，探测间隔 {args.sleep_seconds}s",
        ],
    )
    annotate_row_cap_hits(matrix)
    written = write_outputs(matrix, output_dir, token=token)
    print(f"[audit] wrote {', '.join(str(path.relative_to(ROOT)) for path in written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
