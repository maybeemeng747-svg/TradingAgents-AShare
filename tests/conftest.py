"""Pytest-wide safeguards.

Some API smoke tests import the real FastAPI app and use ``api.database``.
Without an early DATABASE_URL override those tests write to the local
``tradingagents.db``. Keep test runs isolated by default.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROD_DB = (PROJECT_ROOT / "tradingagents.db").resolve()


def _is_prod_sqlite_url(url: str | None) -> bool:
    if not url or not url.startswith("sqlite"):
        return False
    raw_path = url.replace("sqlite:///", "").replace("sqlite://", "")
    if raw_path in (":memory:", ""):
        return False
    return Path(raw_path).resolve() == PROD_DB


def _install_isolated_database_url() -> None:
    current = os.environ.get("DATABASE_URL")
    if current and not _is_prod_sqlite_url(current):
        return
    temp_dir = Path(tempfile.mkdtemp(prefix="tradingagents-pytest-"))
    os.environ["DATABASE_URL"] = f"sqlite:///{temp_dir / 'tradingagents-test.db'}"


_install_isolated_database_url()
