#!/usr/bin/env python3
"""TA-TUSHARE-2000-001C: read-only Tushare research evidence pack export.

Collects one symbol's Tushare research endpoints (statements, indicators,
forecast, express, main business, audit opinion, dividends) under the 001B
eight-state contract and writes a normalized JSON evidence pack to an
explicitly specified output path.  Existing files are never overwritten and
no knowledge-base directory is written: the caller owns the destination.

The token is read exclusively from the git-ignored ``.env`` (TUSHARE_TOKEN).
``NO_KEY`` fails closed with exit code 2 unless ``--fixture`` supplies an
offline sanitized response fixture.  Real-run stdout only carries per
endpoint states, row counts and SHA-256 digests, never response bodies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from tradingagents.dataflows.tushare_capability import contains_credential_header_hint
from tradingagents.dataflows.tushare_research_evidence import (
    TASK_ID,
    TOKEN_STATUS_HAS_KEY,
    TOKEN_STATUS_NO_KEY,
    build_tushare_research_evidence,
    validate_evidence_pack,
)

# 001A-R1A (final review P2): default to the audited R1 rerun truth source;
# the original 041709 archive is superseded history only.
DEFAULT_PERMISSION_MATRIX = (
    ROOT
    / "docs"
    / "task_runs"
    / "TA-TUSHARE-2000-001A-R1-RERUN-20260816-202237"
    / "tushare_permission_matrix.json"
)


def _write_json_immutable(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise FileExistsError(
                f"refusing to overwrite existing evidence pack: {path}"
            ) from exc
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def make_query_fn(token: str):
    import tushare as ts

    client = ts.pro_api(token)

    def query(endpoint: str, **kwargs: Any):
        return client.query(endpoint, **kwargs)

    return query


def make_fixture_query_fn(fixture_path: Path):
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    responses = payload.get("endpoints") if isinstance(payload, dict) else None
    if not isinstance(responses, dict):
        raise ValueError(f"fixture must contain an 'endpoints' mapping: {fixture_path}")

    def query(endpoint: str, **_kwargs: Any):
        rows = responses.get(endpoint)
        if rows is None:
            return pd.DataFrame()
        if isinstance(rows, dict) and "error" in rows:
            raise RuntimeError(rows["error"])
        return pd.DataFrame(rows)

    return query


def load_endpoint_permissions(matrix_path: Path | None) -> tuple[dict[str, str], str | None, str | None]:
    if matrix_path is None or not Path(matrix_path).exists():
        return {}, None, None
    try:
        matrix = json.loads(Path(matrix_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, None, None
    permissions = {
        str(record.get("endpoint")): str(record.get("permission_status"))
        for record in matrix.get("endpoints", [])
        if record.get("endpoint") and record.get("permission_status")
    }
    tier = matrix.get("tier") or "2000_points"
    ref = str(Path(matrix_path).relative_to(ROOT)) if Path(matrix_path).is_relative_to(ROOT) else str(matrix_path)
    return permissions, tier, ref


def sanitize_summary(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Credential- and body-free summary safe for run archives."""

    return {
        "schema": pack["schema"],
        "evidence_schema_version": pack["evidence_schema_version"],
        "symbol": pack["symbol"],
        "as_of": pack["as_of"],
        "generated_at": pack["generated_at"],
        "token_status": pack["token_status"],
        "permission_level": pack["permission_level"],
        "status": pack["status"],
        "summary": pack["summary"],
        "endpoints": {
            endpoint: {
                "state": record["state"],
                "row_count": record["row_count"],
                "eligible_row_count": record["eligible_row_count"],
                "data_period": record["data_period"],
                "response_sha256": record["response_sha256"],
                "permission_status": record["permission_status"],
                "error": record["error"],
            }
            for endpoint, record in pack["endpoints"].items()
        },
    }


def scan_text_for_secret(text: str, token: str) -> bool:
    if token and token in text:
        return True
    return bool(contains_credential_header_hint(text))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="explicit output JSON path; required, existing files are never overwritten",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="offline sanitized fixture (no network); enables NO_KEY runs",
    )
    parser.add_argument(
        "--permission-matrix",
        type=Path,
        default=DEFAULT_PERMISSION_MATRIX,
        help="001A permission matrix used to annotate endpoint permission status",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.6)
    parser.add_argument(
        "--no-dotenv",
        action="store_true",
        help="skip .env loading (test isolation only; the token must come from the environment)",
    )
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

    if not args.symbol:
        print("[evidence] symbol is required", file=sys.stderr)
        return 2
    if args.output is None:
        print(
            "[evidence] --output is required: the read-only export target must be "
            "explicitly specified by the caller",
            file=sys.stderr,
        )
        return 2

    env = os.environ if environ is None else environ
    token = str(env.get("TUSHARE_TOKEN", "") or "").strip()
    token_status = TOKEN_STATUS_HAS_KEY if token else TOKEN_STATUS_NO_KEY

    if args.fixture is not None:
        query_fn = make_fixture_query_fn(args.fixture)
        print(f"[evidence] fixture mode token={token_status} fixture={args.fixture}")
    elif token_status == TOKEN_STATUS_NO_KEY:
        print(
            "[evidence] TUSHARE_TOKEN NO_KEY：fail closed（离线请用 --fixture），"
            "Token 只允许来自 git-ignored .env"
        )
        return 2
    else:
        query_fn = make_query_fn(token)

    permissions, tier, matrix_ref = load_endpoint_permissions(args.permission_matrix)
    print(
        f"[evidence] token={token_status} symbol={args.symbol} as_of={args.as_of} "
        f"output={args.output}"
    )
    started = datetime.now().astimezone().isoformat(timespec="seconds")

    def throttled_query(endpoint: str, **kwargs: Any):
        time.sleep(args.sleep_seconds)
        return query_fn(endpoint, **kwargs)

    try:
        pack = build_tushare_research_evidence(
            symbol=args.symbol,
            as_of=args.as_of,
            query_fn=throttled_query,
            token=token,
            token_status=token_status,
            collection_mode="fixture" if args.fixture is not None else "live",
            permission_tier=tier,
            permission_matrix_ref=matrix_ref,
            endpoint_permissions=permissions,
            generated_at=started,
        )
    except ValueError as exc:
        print(f"[evidence] invalid request: {exc}", file=sys.stderr)
        return 2

    validate_evidence_pack(pack)
    summary = sanitize_summary(pack)
    dumped = json.dumps(pack, ensure_ascii=False)
    if scan_text_for_secret(dumped, token):
        print("[evidence] credential self-check FAILED: refusing to write", file=sys.stderr)
        return 3
    try:
        _write_json_immutable(args.output, pack)
    except FileExistsError as exc:
        print(f"[evidence] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if pack["status"] == "HAS_DATA" else 2


if __name__ == "__main__":
    raise SystemExit(main())
