#!/usr/bin/env python3
"""Print redacted local LLM configuration.

This is a read-only operator/auditor helper. It never decrypts or prints API
keys; it only reports provider/model/base-url fields and whether encrypted key
material exists for a key scope.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "tradingagents.db"


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "<local-user>"
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked = name[:1] + "*"
    else:
        masked = name[:2] + "***" + name[-1:]
    return f"{masked}@{domain}"


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    return sqlite3.connect(str(db_path))


def _print_runtime_configs(conn: sqlite3.Connection, *, show_user: bool) -> None:
    rows = conn.execute(
        """
        SELECT
            u.email,
            COALESCE(c.llm_provider, '') AS provider,
            COALESCE(c.backend_url, '') AS base_url,
            COALESCE(c.quick_think_llm, '') AS quick_model,
            COALESCE(c.deep_think_llm, '') AS deep_model,
            COALESCE(CAST(c.max_debate_rounds AS TEXT), '') AS debate_rounds,
            COALESCE(CAST(c.max_risk_discuss_rounds AS TEXT), '') AS risk_rounds
        FROM user_llm_configs c
        JOIN users u ON u.id = c.user_id
        WHERE u.email NOT LIKE '%@test.com'
        ORDER BY c.updated_at DESC
        """
    ).fetchall()

    print("Runtime LLM configs (redacted):")
    if not rows:
        print("  <none>")
        return
    for row in rows:
        email, provider, base_url, quick, deep, debate, risk = row
        user_label = email if show_user else _mask_email(email)
        print(f"  user={user_label}")
        print(f"    provider={provider or '<unset>'}")
        print(f"    base_url={base_url or '<provider default>'}")
        print(f"    quick_model={quick or '<unset>'}")
        print(f"    deep_model={deep or '<unset>'}")
        print(f"    debate_rounds={debate or '<unset>'} risk_rounds={risk or '<unset>'}")


def _print_provider_key_scopes(conn: sqlite3.Connection, *, show_user: bool) -> None:
    rows = conn.execute(
        """
        SELECT
            u.email,
            k.key_scope,
            CASE
                WHEN k.api_key_encrypted IS NULL OR k.api_key_encrypted = ''
                THEN 'NO_KEY'
                ELSE 'HAS_KEY'
            END AS key_state
        FROM user_llm_provider_keys k
        JOIN users u ON u.id = k.user_id
        WHERE u.email NOT LIKE '%@test.com'
        ORDER BY u.email, k.key_scope
        """
    ).fetchall()

    print("\nProvider key scopes (no plaintext secrets):")
    if not rows:
        print("  <none>")
        return
    for email, key_scope, key_state in rows:
        user_label = email if show_user else _mask_email(email)
        print(f"  user={user_label} scope={key_scope} state={key_state}")


def _print_env_fallbacks() -> None:
    names = [
        "TA_LLM_PROVIDER",
        "TA_BASE_URL",
        "TA_LLM_QUICK",
        "TA_LLM_DEEP",
        "TA_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ]
    print("\nEnvironment fallback state:")
    for name in names:
        value = os.getenv(name)
        if value is None or value == "":
            state = "<unset>"
        elif "KEY" in name:
            state = "HAS_VALUE"
        else:
            state = value
        print(f"  {name}={state}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit redacted local LLM configuration.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to SQLite database")
    parser.add_argument("--show-user", action="store_true", help="Show full user emails")
    args = parser.parse_args()

    conn = _connect(Path(args.db).expanduser().resolve())
    try:
        _print_runtime_configs(conn, show_user=args.show_user)
        _print_provider_key_scopes(conn, show_user=args.show_user)
        _print_env_fallbacks()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
