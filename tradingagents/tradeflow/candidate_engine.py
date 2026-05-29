"""TradeFlow Candidate Engine — runs strategies and produces candidates.

Responsibilities:
- Load price data for each symbol in the universe
- Run all three strategies
- Filter out ST / low-liquidity / data-missing stocks
- Deduplicate multi-strategy hits
- Mark need_deep_ta
- Persist to tradeflow tables
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

import pandas as pd

from .schemas import Candidate, CandidateSignal, ALL_STRATEGIES, STRATEGY_POLICY_VERSION, STRATEGY_NARRATIVE  # [S-001] [S-002]
from .strategies.vcp import score_vcp
from .strategies.pullback_support import score_pullback_support
from .strategies.event_catalyst import score_event_catalyst
from .policy_version_signal import detect_policy_version, PolicyVersionResult  # [S-001]
from .narrative_quality import score_narrative_quality, NarrativeQualityResult  # [S-002]
from .underwater_risk_flags import detect_underwater_risks, UnderwaterRiskResult, ALL_RISK_FLAGS  # [S-003]
from .game_balance import assess_game_balance, GameBalanceResult  # [S-004] candidate_game_balance


# ── SQL for table creation ──
CREATE_CANDIDATES_TABLE = """
CREATE TABLE IF NOT EXISTS tradeflow_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    strategy_tags_json TEXT DEFAULT '[]',
    primary_strategy TEXT DEFAULT '',
    score REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active',
    trigger_price REAL,
    support_price REAL,
    invalid_price REAL,
    need_deep_ta INTEGER DEFAULT 0,
    evidence_json TEXT DEFAULT '{}',
    risk_flags_json TEXT DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(trade_date, symbol)
);
"""

CREATE_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS tradeflow_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    signal_level TEXT DEFAULT 'info',
    source TEXT DEFAULT 'tradeflow',
    evidence_json TEXT DEFAULT '{}',
    action_hint TEXT DEFAULT 'OBSERVE',
    status TEXT DEFAULT 'new',
    created_at TEXT
);
"""

CREATE_DAILY_PLANS_TABLE = """
CREATE TABLE IF NOT EXISTS tradeflow_daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    mode TEXT DEFAULT 'pre_market',
    summary TEXT DEFAULT '',
    candidates_json TEXT DEFAULT '[]',
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT,
    UNIQUE(trade_date, mode)
);
"""


def init_db(db_path: str) -> None:
    """Create tradeflow tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        CREATE_CANDIDATES_TABLE
        + CREATE_SIGNALS_TABLE
        + CREATE_DAILY_PLANS_TABLE
    )
    try:
        conn.execute("ALTER TABLE tradeflow_candidates ADD COLUMN primary_strategy TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # [S-001] policy_version_signal — add policy version columns
    for _col, _type in [
        ("policy_tags_json", "TEXT DEFAULT '[]'"),
        ("version_score", "REAL DEFAULT 0.0"),
        ("policy_evidence_refs_json", "TEXT DEFAULT '[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [S-002] narrative_quality_score — add narrative quality columns
    for _col, _type in [
        ("narrative_score", "REAL DEFAULT 0.0"),
        ("narrative_reasons_json", "TEXT DEFAULT '[]'"),
        ("narrative_evidence_refs_json", "TEXT DEFAULT '[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [S-003] underwater_risk_flags — add risk penalty columns
    for _col, _type in [
        ("risk_penalty", "REAL DEFAULT 0.0"),
        ("risk_evidence_refs_json", "TEXT DEFAULT '[]'"),
        ("risk_reasons_json", "TEXT DEFAULT '[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [S-004] candidate_game_balance — add game balance columns
    for _col, _type in [
        ("game_balance", "TEXT DEFAULT ''"),
        ("bull_case", "TEXT DEFAULT ''"),
        ("bear_case", "TEXT DEFAULT ''"),
        ("policy_case", "TEXT DEFAULT ''"),
        ("fund_flow_case", "TEXT DEFAULT ''"),
        ("resonance_count", "INTEGER DEFAULT 0"),
        ("game_balance_refs_json", "TEXT DEFAULT '[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def _fetch_price_data(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    """Fetch price data for a symbol using the project's data providers.

    Uses cn_akshare → cn_astock → yfinance fallback chain.  [N-002] cn_astock_fallback
    Returns DataFrame with Date, Open, High, Low, Close, Volume columns.
    """
    from datetime import timedelta
    from io import StringIO

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    def _try_provider(provider_cls, source_name: str) -> Optional[pd.DataFrame]:
        try:
            provider = provider_cls()
            csv_str = provider.get_stock_data(symbol, start, end)
            if csv_str and "No data found" not in csv_str:
                df = pd.read_csv(StringIO(csv_str), comment="#")
                if {"Date", "Open", "High", "Low", "Close", "Volume"}.issubset(df.columns):
                    df["Date"] = pd.to_datetime(df["Date"])
                    df = df.sort_values("Date").reset_index(drop=True)
                    for col in ["Open", "High", "Low", "Close", "Volume"]:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                    df = df.dropna(subset=["Close"])
                    if len(df) > 0:
                        df.attrs["price_source"] = source_name  # [N-002] cn_astock_fallback
                        return df
        except Exception:
            pass
        return None

    # [N-002] cn_astock_fallback: cn_akshare → cn_astock → yfinance
    try:
        from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider
        result = _try_provider(CnAkshareProvider, "cn_akshare")
        if result is not None:
            return result
    except Exception:
        pass

    # [N-002] cn_astock_fallback: middle fallback
    try:
        from tradingagents.dataflows.providers.cn_astock_provider import CnAstockProvider
        result = _try_provider(CnAstockProvider, "cn_astock")
        if result is not None:
            return result
    except Exception:
        pass

    # Final fallback: yfinance
    try:
        from tradingagents.dataflows.providers.yfinance_provider import YFinanceProvider
        result = _try_provider(YFinanceProvider, "yfinance")
        if result is not None:
            return result
    except Exception:
        pass

    return None


def _is_st_symbol(symbol: str) -> bool:
    """Check if symbol is ST/*ST."""
    code = symbol.split(".")[0] if "." in symbol else symbol
    # Can't determine ST from code alone — would need name lookup
    # P0: skip this check, rely on risk_flags from data
    return False


def _calc_liquidity(df: pd.DataFrame) -> tuple[bool, float, float, float]:
    """Check liquidity using Amount (turnover) as primary metric.

    Returns (is_low, avg_amount, avg_volume, threshold).
    - Uses Amount column if available, otherwise estimates via Close * Volume.
    - Threshold: 30,000,000 (3000万).
    """
    AMOUNT_THRESHOLD = 30_000_000

    if len(df) < 5:
        avg_amount = 0.0
        avg_volume = 0.0
        return True, avg_amount, avg_volume, AMOUNT_THRESHOLD

    tail = df.tail(10)
    avg_volume = float(tail["Volume"].mean())

    if "Amount" in tail.columns:
        avg_amount = float(tail["Amount"].mean())
    else:
        avg_amount = float((tail["Close"] * tail["Volume"]).mean())

    return avg_amount < AMOUNT_THRESHOLD, avg_amount, avg_volume, AMOUNT_THRESHOLD


def filter_symbol(symbol: str, df: pd.DataFrame) -> tuple[bool, list[str]]:
    """Check if a symbol should be filtered out.

    Returns (should_filter, reason_list).
    """
    reasons = []

    if df is None or len(df) == 0:
        return True, ["数据缺失"]

    if len(df) < 40:
        return True, ["数据不足(需至少40日)"]

    is_low, avg_amount, avg_volume, threshold = _calc_liquidity(df)
    if is_low:
        reasons.append(
            f"流动性差(avg_amount={avg_amount/1e8:.2f}亿, "
            f"avg_volume={avg_volume/1e4:.0f}万, "
            f"threshold={threshold/1e8:.2f}亿)"
        )

    if reasons:
        return True, reasons

    return False, []


def run_strategies(
    symbol: str,
    df: pd.DataFrame,
    news_texts: Optional[list[str]] = None,
    event_overrides: Optional[list[dict]] = None,
) -> list[CandidateSignal]:
    """Run all strategies on a symbol, return list of signals."""
    signals = []

    # VCP
    vcp = score_vcp(df, symbol)
    if vcp is not None:
        signals.append(vcp)

    # Pullback Support
    pb = score_pullback_support(df, symbol)
    if pb is not None:
        signals.append(pb)

    # Event Catalyst
    latest_close = float(df["Close"].iloc[-1]) if len(df) > 0 else 0.0
    ev = score_event_catalyst(symbol, news_texts, event_overrides, latest_close)
    if ev is not None:
        signals.append(ev)

    return signals


def evaluate_symbol(
    symbol: str,
    name: str = "",
    source: str = "manual",
    trade_date: str = "",
    news_texts: Optional[list[str]] = None,
    event_overrides: Optional[list[dict]] = None,
    df: Optional[pd.DataFrame] = None,
) -> tuple[Optional[Candidate], str]:
    """Evaluate one symbol through all strategies.

    Returns (Candidate, "") if any strategy hits, or (None, reason) if
    filtered or no strategy matched.
    """
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    # Fetch data if not provided
    if df is None:
        df = _fetch_price_data(symbol)

    # Filter check
    filtered, reasons = filter_symbol(symbol, df)
    if filtered:
        return None, "；".join(reasons)

    # Run strategies
    signals = run_strategies(symbol, df, news_texts, event_overrides)

    if not signals:
        return None, "无策略命中"

    # Build candidate
    candidate = Candidate(
        symbol=symbol,
        name=name,
        source=source,
        trade_date=trade_date,
        signals=signals,
    )
    candidate.merge_signals()

    # [N-004] astock_signal_tags — merge extra tags from event_catalyst evidence
    for sig in signals:
        if not isinstance(sig.evidence, dict):
            continue
        extra_tags = sig.evidence.get("astock_tags", [])
        extra_risks = sig.evidence.get("astock_risk_flags", [])
        if extra_tags or extra_risks:
            from .schemas import SIGNAL_TAG_LOCKUP_RISK  # [N-004] astock_signal_tags
            candidate.strategy_tags = sorted(
                set(candidate.strategy_tags) | set(extra_tags)
            )
            candidate.risk_flags = sorted(
                set(candidate.risk_flags) | set(extra_risks)
            )
            if SIGNAL_TAG_LOCKUP_RISK in candidate.risk_flags:
                candidate.score = round(candidate.score * 0.8, 2)

    # [S-001] policy_version_signal — detect policy version and add bonus
    # Collect all available text sources: news_texts + event_override titles
    all_event_texts = list(news_texts) if news_texts else []
    if event_overrides:
        for ev in event_overrides:
            title = ev.get("title", "") or ev.get("headline", "")
            if title:
                all_event_texts.append(title)
    policy_result: PolicyVersionResult = detect_policy_version(
        event_texts=all_event_texts if all_event_texts else None,
    )
    if policy_result.policy_tags:
        candidate.policy_tags = policy_result.policy_tags
        candidate.version_score = policy_result.version_score
        candidate.policy_evidence_refs = policy_result.policy_evidence_refs
        candidate.score = round(candidate.score + policy_result.version_score, 2)
        candidate.strategy_tags = sorted(
            set(candidate.strategy_tags) | {STRATEGY_POLICY_VERSION}
        )
        candidate.evidence["policy_version"] = {
            "policy_tags": policy_result.policy_tags,
            "version_score": policy_result.version_score,
            "evidence_refs": policy_result.policy_evidence_refs,
        }
        if policy_result.version_score >= 15:
            candidate.need_deep_ta = True

    # [S-002] narrative_quality_score — assess event narrative quality
    narrative_result: NarrativeQualityResult = score_narrative_quality(
        event_texts=all_event_texts if all_event_texts else None,
    )
    if narrative_result.narrative_score > 0:
        candidate.narrative_score = narrative_result.narrative_score
        candidate.narrative_reasons = narrative_result.narrative_reasons
        candidate.narrative_evidence_refs = narrative_result.narrative_evidence_refs
        candidate.score = round(candidate.score + narrative_result.narrative_score, 2)
        candidate.strategy_tags = sorted(
            set(candidate.strategy_tags) | {STRATEGY_NARRATIVE}
        )
        candidate.evidence["narrative_quality"] = {
            "narrative_score": narrative_result.narrative_score,
            "narrative_reasons": narrative_result.narrative_reasons,
            "evidence_refs": narrative_result.narrative_evidence_refs,
        }
        if narrative_result.narrative_score >= 20:
            candidate.need_deep_ta = True

    # [S-003] underwater_risk_flags — detect hidden structural risks
    risk_result: UnderwaterRiskResult = detect_underwater_risks(
        event_texts=all_event_texts if all_event_texts else None,
    )
    if risk_result.risk_flags:
        candidate.risk_flags = sorted(
            set(candidate.risk_flags) | set(risk_result.risk_flags)
        )
        candidate.risk_penalty = risk_result.risk_penalty
        candidate.risk_evidence_refs = risk_result.risk_evidence_refs
        candidate.risk_reasons = risk_result.risk_reasons
        candidate.score = round(candidate.score + risk_result.risk_penalty, 2)
        candidate.evidence["underwater_risk"] = {
            "risk_flags": risk_result.risk_flags,
            "risk_penalty": risk_result.risk_penalty,
            "risk_evidence_refs": risk_result.risk_evidence_refs,
            "risk_reasons": risk_result.risk_reasons,
        }
        high_risk_flags = {f for f in risk_result.risk_flags
                           if f in {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}}
        if len(risk_result.risk_flags) >= 3 or high_risk_flags:
            candidate.need_deep_ta = False

    # [S-004] candidate_game_balance — assess bull/bear/policy/fund perspective
    gb_result: GameBalanceResult = assess_game_balance(
        strategy_tags=candidate.strategy_tags,
        score=candidate.score,
        policy_tags=candidate.policy_tags,
        version_score=candidate.version_score,
        policy_evidence_refs=candidate.policy_evidence_refs,
        narrative_score=candidate.narrative_score,
        narrative_reasons=candidate.narrative_reasons,
        risk_flags=candidate.risk_flags,
        risk_penalty=candidate.risk_penalty,
        risk_reasons=candidate.risk_reasons,
        event_texts=all_event_texts if all_event_texts else None,
    )
    candidate.game_balance = gb_result.game_balance
    candidate.bull_case = gb_result.bull_case
    candidate.bear_case = gb_result.bear_case
    candidate.policy_case = gb_result.policy_case
    candidate.fund_flow_case = gb_result.fund_flow_case
    candidate.resonance_count = gb_result.resonance_count
    candidate.game_balance_refs = gb_result.game_balance_refs
    candidate.evidence["game_balance"] = {
        "game_balance": gb_result.game_balance,
        "bull_case": gb_result.bull_case,
        "bear_case": gb_result.bear_case,
        "policy_case": gb_result.policy_case,
        "fund_flow_case": gb_result.fund_flow_case,
        "resonance_count": gb_result.resonance_count,
        "refs": gb_result.game_balance_refs,
    }
    # When >= 2 categories resonate and risk manageable, boost need_deep_ta
    if gb_result.resonance_count >= 2 and gb_result.game_balance in {"favorable", "neutral"}:
        candidate.need_deep_ta = True
    # When fragile or crowded with too many risks, demote need_deep_ta
    if gb_result.game_balance in {"fragile", "crowded"} and len(candidate.risk_flags) >= 2:
        candidate.need_deep_ta = False

    return candidate, ""


def save_candidate(candidate: Candidate, db_path: str) -> int:
    """Save a candidate to the database. Upsert on (trade_date, symbol)."""
    conn = sqlite3.connect(db_path)
    row = candidate.to_db_row()
    try:
        conn.execute(
            "INSERT INTO tradeflow_candidates "
            "(trade_date, symbol, name, source, strategy_tags_json, primary_strategy, "
            "score, status, trigger_price, support_price, invalid_price, need_deep_ta, "
            "evidence_json, risk_flags_json, policy_tags_json, version_score, "
            "policy_evidence_refs_json, narrative_score, narrative_reasons_json, "
            "narrative_evidence_refs_json, risk_penalty, risk_evidence_refs_json, "
            "risk_reasons_json, game_balance, bull_case, bear_case, policy_case, "
            "fund_flow_case, resonance_count, game_balance_refs_json, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(trade_date, symbol) DO UPDATE SET "
            "name=excluded.name, source=excluded.source, strategy_tags_json=excluded.strategy_tags_json, "
            "primary_strategy=excluded.primary_strategy, score=excluded.score, status=excluded.status, "
            "trigger_price=excluded.trigger_price, "
            "support_price=excluded.support_price, invalid_price=excluded.invalid_price, "
            "need_deep_ta=excluded.need_deep_ta, evidence_json=excluded.evidence_json, "
            "risk_flags_json=excluded.risk_flags_json, "
            "policy_tags_json=excluded.policy_tags_json, version_score=excluded.version_score, "
            "policy_evidence_refs_json=excluded.policy_evidence_refs_json, "
            "narrative_score=excluded.narrative_score, "
            "narrative_reasons_json=excluded.narrative_reasons_json, "
            "narrative_evidence_refs_json=excluded.narrative_evidence_refs_json, "
            "risk_penalty=excluded.risk_penalty, "
            "risk_evidence_refs_json=excluded.risk_evidence_refs_json, "
            "risk_reasons_json=excluded.risk_reasons_json, "
            "game_balance=excluded.game_balance, "
            "bull_case=excluded.bull_case, bear_case=excluded.bear_case, "
            "policy_case=excluded.policy_case, fund_flow_case=excluded.fund_flow_case, "
            "resonance_count=excluded.resonance_count, "
            "game_balance_refs_json=excluded.game_balance_refs_json, "
            "updated_at=excluded.updated_at",
            (
                row["trade_date"], row["symbol"], row["name"], row["source"],
                row["strategy_tags_json"], row["primary_strategy"],
                row["score"], row["status"],
                row["trigger_price"], row["support_price"], row["invalid_price"],
                row["need_deep_ta"], row["evidence_json"], row["risk_flags_json"],
                row["policy_tags_json"], row["version_score"],
                row["policy_evidence_refs_json"],
                row["narrative_score"], row["narrative_reasons_json"],
                row["narrative_evidence_refs_json"],
                row["risk_penalty"], row["risk_evidence_refs_json"],
                row["risk_reasons_json"],
                row["game_balance"], row["bull_case"], row["bear_case"],
                row["policy_case"], row["fund_flow_case"],
                row["resonance_count"], row["game_balance_refs_json"],
                candidate.created_at, row["updated_at"],
            ),
        )
        conn.commit()
        row_id = conn.execute(
            "SELECT id FROM tradeflow_candidates WHERE trade_date=? AND symbol=?",
            (row["trade_date"], row["symbol"]),
        ).fetchone()[0]
        return row_id
    finally:
        conn.close()
