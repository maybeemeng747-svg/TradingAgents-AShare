"""TA-TUSHARE-2000-001A: real Tushare 2000-point permission matrix audit.

Runs one minimal, single-stock, low-frequency probe per endpoint listed in
the task scope and writes a sanitized permission matrix (JSON + Markdown)
plus a run receipt into a ``docs/task_runs/TA-TUSHARE-2000-001A-*``
archive.  The Tushare token is read exclusively from the git-ignored
``.env`` file; an inherited process-environment token is only honored with
an explicit ``--no-dotenv`` (test isolation only) — a missing or token-less
``.env`` fails closed even when the environment carries a token.  The
``.env`` is parsed without environment interpolation and non-literal token
values (``${VAR}``) are rejected fail-closed, so token provenance can never
silently resolve from the process environment.  Every artifact only
records ``HAS_KEY``/``NO_KEY``.

Exit codes: 0 success; 2 NO_KEY fail-closed; 3 credential leak detected in
composed artifacts (nothing is written, fail closed).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

from tradingagents.dataflows.tushare_capability import (
    ENDPOINT_SPECS,
    EXCLUDED_ENDPOINTS,
    TASK_ID,
    TOKEN_SOURCE_DOTENV,
    TOKEN_SOURCE_INHERITED_ENV,
    TOKEN_STATUS_HAS_KEY,
    TOKEN_STATUS_NO_KEY,
    annotate_row_cap_hits,
    build_matrix,
    contains_credential_header_hint,
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


_SUFFIX_TO_EXCHANGE = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}


_NON_LITERAL_TOKEN_RE = re.compile(r"\$\{[^}]*\}")


def exchange_for_ts_code(ts_code: str) -> str:
    suffix = ts_code.rsplit(".", 1)[-1].upper()
    try:
        return _SUFFIX_TO_EXCHANGE[suffix]
    except KeyError:
        raise ValueError(f"unknown exchange suffix in ts_code: {ts_code!r}") from None


class TokenNotLiteralError(ValueError):
    """A dotenv TUSHARE_TOKEN value is not literal (uses ${VAR} interpolation)."""


def normalize_symbol(symbol: str) -> str:
    from tradingagents.dataflows.providers.cn_tushare_provider import _normalize_ts_code

    return _normalize_ts_code(symbol)


def resolve_token(
    *,
    environ: Mapping[str, str],
    dotenv_path: Path | None,
    allow_dotenv: bool = True,
) -> tuple[str, str, bool]:
    """Resolve TUSHARE_TOKEN with explicit, auditable precedence.

    001A-R1 fix (c73ed4f P1#1): the explicitly specified git-ignored ``.env``
    file always wins over an inherited process environment variable, so the
    permission matrix can never be silently attributed to a foreign token.
    001A-R1A fix (final review P1#1): the inherited environment is only
    consulted when dotenv loading was explicitly disabled (``--no-dotenv``,
    test isolation).  When a ``.env`` was requested but is missing or lacks
    ``TUSHARE_TOKEN``, the inherited token is rejected and resolution fails
    closed, so a typo such as ``--dotenv wrong.env`` can never probe a
    foreign account.  Returns ``(token, token_source, env_override_applied)``
    where source is ``dotenv_file``/``inherited_environment``/``""`` (NO_KEY).

    001A-R1B fix (round2 review P1): the ``.env`` file is parsed without
    environment interpolation and non-literal token values (``${VAR}``) are
    rejected with ``TokenNotLiteralError``, so ``TUSHARE_TOKEN=${FOREIGN_TOKEN}``
    can never be silently resolved from the process environment and recorded
    as ``token_source=dotenv_file``.
    """

    dotenv_token = ""
    if allow_dotenv and dotenv_path is not None:
        try:
            values: Mapping[str, str | None] = dotenv_values(
                dotenv_path, interpolate=False
            )
        except OSError:
            values = {}
        dotenv_token = str(values.get("TUSHARE_TOKEN", "") or "").strip()
        if dotenv_token and _NON_LITERAL_TOKEN_RE.search(dotenv_token):
            raise TokenNotLiteralError(
                "TUSHARE_TOKEN in "
                f"{dotenv_path} contains non-literal interpolation syntax; "
                "token provenance would depend on the process environment — "
                "write a literal token or run with --no-dotenv (test isolation)"
            )
    inherited = str(environ.get("TUSHARE_TOKEN", "") or "").strip()
    if dotenv_token:
        overridden = bool(inherited) and inherited != dotenv_token
        return dotenv_token, TOKEN_SOURCE_DOTENV, overridden
    if inherited and not allow_dotenv:
        return inherited, TOKEN_SOURCE_INHERITED_ENV, False
    return "", "", False


def display_path(path: Path, *, root: Path = ROOT) -> str:
    """Relative display path that never crashes for paths outside root."""

    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


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


def scan_artifacts_for_secret(
    texts: Mapping[str, str], token: str
) -> list[tuple[str, str]]:
    """Return ``(artifact_name, reason)`` leak findings; empty means clean.

    001A-R1A fix (final review P1#2): credential headers are detected in
    plain (``Cookie:``) and quoted/dict-serialized (``{"Cookie": "..."}``)
    forms, so an error text carrying serialized headers fails closed even
    when the header value is too short for token/blob matching.
    """

    leaks: list[tuple[str, str]] = []
    for name, text in texts.items():
        if token and token in text:
            leaks.append((name, "token_substring"))
            continue
        for header in contains_credential_header_hint(text):
            leaks.append((name, f"{header}_header"))
    return leaks


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


def write_outputs(
    matrix: dict[str, Any],
    output_dir: Path,
    *,
    token: str,
    token_source: str = "",
    env_override_applied: bool = False,
) -> tuple[list[Path], list[tuple[str, str]]]:
    """Compose, scan, then write artifacts. Fail closed on credential leaks.

    001A-R1 fix (c73ed4f P1#3): when any composed artifact trips the
    credential scan, nothing is written and the leak findings are returned
    so the caller exits non-zero.  001A-R1A fix (final review P1#2): the
    scan also covers quoted/dict-serialized credential headers, so a
    serialized ``{"Cookie": "..."}`` in probe error text never reaches disk.
    """

    json_text = json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    markdown = render_markdown(matrix)
    leaks = scan_artifacts_for_secret(
        {MATRIX_JSON_NAME: json_text, MATRIX_MD_NAME: markdown}, token
    )
    if leaks:
        return [], leaks
    source_label = (
        "git-ignored .env 文件（TUSHARE_TOKEN）"
        if token_source == TOKEN_SOURCE_DOTENV
        else "继承环境变量（TUSHARE_TOKEN）"
        if token_source == TOKEN_SOURCE_INHERITED_ENV
        else "未知来源"
    )
    retry_lines = [
        f"- `{record['endpoint']}`：attempts={record['attempts']}，reason=`{record['retry_reason']}`"
        for record in matrix["endpoints"]
        if record.get("attempts", 1) > 1
    ]
    repurchase_lines = ["- 本矩阵未包含 repurchase 记录"]
    for record in matrix["endpoints"]:
        if record["endpoint"] == "repurchase":
            repurchase_lines = [
                f"- 查询参数：`{json.dumps(record.get('params', {}), ensure_ascii=False)}`",
                f"- 返回行数：{record.get('row_count') if record.get('row_count') is not None else '-'}"
                f"（state=`{record['state']}`）",
                f"- scope：`{record.get('scope')}`；row_cap_suspected="
                f"`{record.get('row_cap_suspected', False)}`",
            ]
            break
    token_source_line = (
        f"- Token 来源：{source_label}，来源标识 `{token_source or 'unknown'}`，"
        f"状态 `{matrix['token_status']}`，任何产物不包含 Token 明文"
    )
    if env_override_applied:
        token_source_line += (
            "；继承环境变量中的 TUSHARE_TOKEN 已被显式 .env 值覆盖（env_override_applied=true）"
        )
    receipt = [
        f"# Run receipt — {TASK_ID}",
        "",
        f"- 运行时间：{matrix['generated_at']}",
        token_source_line,
        f"- 探测标的：`{matrix['probe']['symbol']}`；探测日 `{matrix['probe']['probe_date']}`；"
        f"回退日 `{matrix['probe']['fallback_date']}`",
        f"- 实测 endpoint 数：{matrix['summary']['probed_endpoints']}；"
        f"状态分布：{json.dumps(matrix['summary']['by_state'], ensure_ascii=False)}",
        "",
        "## 重试记录",
        "",
        *(retry_lines or ["- 无重试"]),
        "",
        "## repurchase 单股查询记录（001A-R1）",
        "",
        *repurchase_lines,
        "",
        "## 凭据自检",
        "",
        f"- 对 {MATRIX_JSON_NAME}、{MATRIX_MD_NAME} 与本回执扫描 Token/Authorization/Cookie：clean"
        "（发现泄漏即拒绝写盘并以退出码 3 fail closed）",
        "- 本回执与矩阵均不含真实响应正文，只含结构化元数据与 SHA-256 摘要",
        "",
    ]
    receipt_text = "\n".join(receipt)
    receipt_leaks = scan_artifacts_for_secret({RECEIPT_NAME: receipt_text}, token)
    if receipt_leaks:
        return [], receipt_leaks
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / MATRIX_JSON_NAME
    md_path = output_dir / MATRIX_MD_NAME
    receipt_path = output_dir / RECEIPT_NAME
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    receipt_path.write_text(receipt_text, encoding="utf-8")
    return [json_path, md_path, receipt_path], []


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
        "--dotenv",
        default=None,
        help="显式指定 .env 文件路径（默认 <repo>/.env）；该文件缺失或无 TUSHARE_TOKEN 时"
        "fail closed，绝不回退到继承环境变量",
    )
    parser.add_argument(
        "--no-dotenv",
        action="store_true",
        help="跳过 .env 加载（仅供测试隔离使用；唯一允许使用继承环境变量 Token 的路径，"
        "来源将记录为继承环境变量）",
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
    import os

    env = os.environ if environ is None else environ
    resolved_dotenv = (
        Path(args.dotenv)
        if args.dotenv
        else (dotenv_path if dotenv_path is not None else ROOT / ".env")
    )
    try:
        token, token_source, env_override_applied = resolve_token(
            environ=env,
            dotenv_path=resolved_dotenv,
            allow_dotenv=not args.no_dotenv,
        )
    except TokenNotLiteralError as exc:
        print(f"[audit] TUSHARE_TOKEN 解析失败：{exc}")
        print("[audit] 拒绝探测（fail closed，退出码 2）")
        return 2
    token_status = TOKEN_STATUS_HAS_KEY if token else TOKEN_STATUS_NO_KEY
    probe_context = build_probe_context(args)
    if token_status == TOKEN_STATUS_NO_KEY:
        inherited_note = (
            "；检测到继承环境变量 TUSHARE_TOKEN，已按 .env-only 契约拒绝使用"
            "（测试隔离需显式传入 --no-dotenv）"
            if str(env.get("TUSHARE_TOKEN", "") or "").strip() and not args.no_dotenv
            else ""
        )
        print(
            "[audit] TUSHARE_TOKEN NO_KEY：拒绝探测（fail closed），"
            f"Token 只允许来自 git-ignored .env（本次来源解析：{resolved_dotenv}）"
            f"{inherited_note}"
        )
        return 2
    if args.dry_run:
        print(
            f"[audit] token={token_status} token_source={token_source or 'unknown'} "
            f"env_override_applied={str(env_override_applied).lower()} "
            f"symbol={probe_context['ts_code']} probe_date={args.probe_date}"
        )
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
        f"[audit] token={token_status} token_source={token_source or 'unknown'} "
        f"env_override_applied={str(env_override_applied).lower()} "
        f"symbol={probe_context['ts_code']} "
        f"probe_date={args.probe_date} output={display_path(output_dir)}"
    )
    query_fn = make_query_fn(token)
    records = run_probes(query_fn, probe_context, token=token, sleep_seconds=args.sleep_seconds)
    matrix = build_matrix(
        probe_context,
        records,
        token_status=token_status,
        token_source=token_source,
        token_env_override_applied=env_override_applied,
        generated_at=started,
        state_evidence=state_evidence_from_fixtures(),
        notes=[
            f"探测脚本：scripts/audit_tushare_capability.py，探测间隔 {args.sleep_seconds}s",
            f"Token 来源标识：{token_source or 'unknown'}（显式解析并记录，杜绝继承环境变量静默覆盖）",
        ],
    )
    annotate_row_cap_hits(matrix)
    written, leaks = write_outputs(
        matrix,
        output_dir,
        token=token,
        token_source=token_source,
        env_override_applied=env_override_applied,
    )
    if leaks:
        for name, reason in leaks:
            print(f"[audit] credential leak detected in {name} ({reason})")
        print("[audit] 拒绝写盘并 fail closed（退出码 3）")
        return 3
    print(f"[audit] wrote {', '.join(display_path(path) for path in written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
