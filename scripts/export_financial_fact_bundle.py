#!/usr/bin/env python3
"""Export a cross-source A-share financial fact bundle without calling an LLM."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradingagents.dataflows.financial_fact_bundle import (
    DEFAULT_PROVIDER_NAMES,
    HAS_DATA,
    QUERY_FAILED,
    build_financial_fact_bundle,
)
from tradingagents.dataflows.providers import build_default_registry
from tradingagents.dataflows.providers.cn_eastmoney_financial_provider import (
    CnEastmoneyFinancialProvider,
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
                f"refusing to overwrite financial fact bundle: {path}"
            ) from exc
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def _is_verified_export(bundle: dict[str, object]) -> bool:
    summary = bundle.get("summary")
    return bool(
        bundle.get("status") == HAS_DATA
        and isinstance(summary, dict)
        and summary.get("verified_cross_source", 0) > 0
        and not _contains_query_failure(bundle.get("providers"))
    )


def _contains_query_failure(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("status") == QUERY_FAILED:
            return True
        return any(_contains_query_failure(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_query_failure(item) for item in value)
    return False


def _retryable_failure_output(path: Path, bundle: dict[str, object]) -> Path:
    generated_at = str(bundle.get("generated_at") or "unknown")
    token = "".join(character for character in generated_at if character.isdigit())
    suffix = path.suffix or ".json"
    stem = path.name[: -len(suffix)] if path.name.endswith(suffix) else path.name
    return path.with_name(f"{stem}.retryable-{token or 'unknown'}{suffix}")


def _write_retryable_audit(path: Path, bundle: dict[str, object]) -> Path:
    """Write every degraded attempt without overwriting an earlier audit."""
    candidate = _retryable_failure_output(path, bundle)
    suffix = candidate.suffix or ".json"
    stem = (
        candidate.name[: -len(suffix)]
        if candidate.name.endswith(suffix)
        else candidate.name
    )
    attempt = 0
    while True:
        actual_path = (
            candidate
            if attempt == 0
            else candidate.with_name(f"{stem}-{attempt:02d}{suffix}")
        )
        try:
            _write_json_immutable(actual_path, bundle)
            return actual_path
        except FileExistsError:
            if not os.path.lexists(actual_path):
                raise
            attempt += 1


def _persist_export_bundle(
    path: Path,
    bundle: dict[str, object],
) -> tuple[bool, Path]:
    """Persist the immutable audit bundle and report whether it is verified.

    Degraded results are still valuable audit evidence.  The exit status and
    return value distinguish them from a verified export; withholding the file
    would make downstream low-confidence handling impossible.  Every
    non-verified result uses a separate attempt path so it cannot occupy the
    destination reserved for a later verified bundle.
    """
    verified = _is_verified_export(bundle)
    if verified:
        actual_path = path
        _write_json_immutable(actual_path, bundle)
    else:
        actual_path = _write_retryable_audit(path, bundle)
    return verified, actual_path


def _publish_verified_bundle(path: Path, bundle: dict[str, object]) -> bool:
    """Backward-compatible boolean wrapper used by existing callers/tests."""
    verified, _actual_path = _persist_export_bundle(path, bundle)
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDER_NAMES),
        help="Comma-separated provider names; default: cn_astock,cn_eastmoney_financial",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    registry = build_default_registry()
    providers = []
    for name in (part.strip() for part in args.providers.split(",")):
        if not name:
            continue
        provider = (
            CnEastmoneyFinancialProvider()
            if name == "cn_eastmoney_financial"
            else registry.get(name)
        )
        if provider is None:
            parser.error(f"unknown provider: {name}")
        providers.append(provider)
    if len(providers) < 2:
        parser.error("at least two providers are required for cross-source verification")

    try:
        bundle = build_financial_fact_bundle(
            symbol=args.symbol,
            as_of=args.as_of,
            providers=providers,
        )
        verified, actual_output = _persist_export_bundle(args.output, bundle)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": bundle["status"],
                "output": str(actual_output),
                "verified": verified,
                "summary": bundle["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
