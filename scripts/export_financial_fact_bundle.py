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
    )


def _publish_verified_bundle(path: Path, bundle: dict[str, object]) -> bool:
    if not _is_verified_export(bundle):
        return False
    _write_json_immutable(path, bundle)
    return True


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
        published = _publish_verified_bundle(args.output, bundle)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": bundle["status"],
                "output": str(args.output) if published else None,
                "summary": bundle["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if published else 2


if __name__ == "__main__":
    raise SystemExit(main())
