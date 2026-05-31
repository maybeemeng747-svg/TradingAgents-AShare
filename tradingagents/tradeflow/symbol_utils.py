# [UI-008] tradeflow_field_normalization
"""TradeFlow symbol normalization and name resolution helpers.

Central place for:
- normalize_tradeflow_symbol(): ensures 6-digit codes carry .SH/.SZ/.BJ suffix
- resolve_tradeflow_name(): fills missing stock names via fallback chain
- symbol_bare_code(): strips suffix for akshare / event_source lookups
"""

from __future__ import annotations

import re
import threading
from typing import Optional

_SUFFIX_RE = re.compile(r"\.(SH|SZ|SS|BJ|sh|sz|ss|bj)$")
_SIX_DIGIT_RE = re.compile(r"^(\d{6})$")


def normalize_tradeflow_symbol(raw: str) -> str:
    """Ensure a tradeflow symbol is in ``XXXXXX.SH/SZ/BJ`` form.

    Rules:
    - Already has a recognised suffix → normalise case (.SS → .SH), do not double-add.
    - Bare 6-digit code starting with 5/6/9 → .SH
    - Bare 6-digit code starting with 0/2/3 → .SZ
    - Bare 6-digit code starting with 4/8 → .BJ
    - Anything else (US tickers, non-standard) → return upper-cased as-is.
    """
    s = raw.strip().upper()
    if not s:
        return s

    m = _SUFFIX_RE.search(s)
    if m:
        suffix = m.group(1)
        base = s[: m.start()]
        if suffix == "SS":
            return f"{base}.SH"
        return f"{base}.{suffix}"

    dm = _SIX_DIGIT_RE.match(s)
    if dm:
        code = dm.group(1)
        first = code[0]
        if first in ("5", "6", "9"):
            return f"{code}.SH"
        if first in ("0", "2", "3"):
            return f"{code}.SZ"
        if first in ("4", "8"):
            return f"{code}.BJ"
        return code

    return s


def symbol_bare_code(symbol: str) -> str:
    """Strip exchange suffix, returning the bare 6-digit code."""
    s = symbol.strip().upper()
    m = _SUFFIX_RE.search(s)
    if m:
        return s[: m.start()]
    return s


_NAME_PLACEHOLDER = "--"


class StockNameResolver:
    """Thread-safe, lazy-loaded code→name resolver with TTL cache.

    Fallback priority:
    1. Caller-supplied name (non-empty)
    2. Cached reverse stock map (from akshare or prod DB)
    3. ``"--"`` placeholder
    """

    _instance: Optional[StockNameResolver] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._reverse_map: dict[str, str] = {}
        self._loaded_at: float = 0.0
        self._ttl: float = 7 * 86400.0
        self._map_lock = threading.Lock()

    @classmethod
    def shared(cls) -> StockNameResolver:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def warm(self, extra_map: dict[str, str]) -> None:
        """Merge extra code→name entries into cache without resetting TTL."""
        if not extra_map:
            return
        with self._map_lock:
            self._reverse_map.update(extra_map)

    def _ensure_loaded(self) -> None:
        import time

        now = time.time()
        if self._reverse_map and (now - self._loaded_at) <= self._ttl:
            return
        with self._map_lock:
            if self._reverse_map and (now - self._loaded_at) <= self._ttl:
                return
            try:
                self._reverse_map = self._load_from_akshare()
                self._loaded_at = now
            except Exception:
                if not self._reverse_map:
                    self._reverse_map = {}

    @staticmethod
    def _load_from_akshare() -> dict[str, str]:
        import akshare as ak

        result: dict[str, str] = {}
        try:
            df = ak.stock_info_a_code_name()
            for _, row in df.iterrows():
                name = str(row.get("name", "")).strip()
                code = str(row.get("code", "")).strip()
                if name and code:
                    result[normalize_tradeflow_symbol(code)] = name
        except Exception:
            pass
        return result

    def resolve(self, symbol: str, name: str = "") -> str:
        """Resolve name for *symbol*, falling back through cache.

        Returns the supplied *name* when non-empty, otherwise looks up
        the cached map, and finally returns ``"--"``.
        """
        if name and name.strip():
            return name.strip()

        norm_sym = normalize_tradeflow_symbol(symbol)
        self._ensure_loaded()

        with self._map_lock:
            cached = self._reverse_map.get(norm_sym)
            if cached:
                return cached

        bare = symbol_bare_code(norm_sym)
        with self._map_lock:
            for k, v in self._reverse_map.items():
                if symbol_bare_code(k) == bare:
                    return v

        return _NAME_PLACEHOLDER


def resolve_tradeflow_name(symbol: str, name: str = "") -> str:
    """Convenience wrapper around :pymeth:`StockNameResolver.resolve`."""
    return StockNameResolver.shared().resolve(symbol, name)
