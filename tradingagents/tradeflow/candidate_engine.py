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

from .schemas import Candidate, CandidateSignal, ALL_STRATEGIES, STRATEGY_POLICY_VERSION, STRATEGY_NARRATIVE, STRATEGY_FUND_FLOW  # [S-001] [S-002] [T-003]
from .strategies.vcp import score_vcp
from .strategies.pullback_support import score_pullback_support
from .strategies.event_catalyst import score_event_catalyst
from .policy_version_signal import detect_policy_version, PolicyVersionResult  # [S-001]
from .narrative_quality import score_narrative_quality, NarrativeQualityResult  # [S-002]
from .underwater_risk_flags import detect_underwater_risks, UnderwaterRiskResult, ALL_RISK_FLAGS  # [S-003]
from .game_balance import assess_game_balance, GameBalanceResult  # [S-004] candidate_game_balance
from .fund_flow_anomaly import detect_fund_flow_anomaly, FundFlowAnomalyResult, FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT  # [T-003] fund_flow_anomaly_pool
from .selection_priority_gate import run_selection_priority_gate, SelectionPriorityResult  # [S-005] selection_priority_gate
from .tier_budget import classify_candidate_tier, TierBudgetResult  # [S-007] candidate_tier_budget
from .evidence_gate import compute_evidence_completeness, apply_evidence_gate, EvidenceGateResult  # [S-008] tradeflow_evidence_gate
from .strategy_config import StrategyConfig, DEFAULT_STRATEGY_CONFIG  # [M-004]
from .symbol_utils import normalize_tradeflow_symbol, resolve_tradeflow_name  # [UI-008] tradeflow_field_normalization


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
    # [T-003] fund_flow_anomaly_pool — add fund flow anomaly columns
    for _col, _type in [
        ("fund_flow_anomaly_score", "REAL DEFAULT 0.0"),
        ("fund_flow_anomaly_tags_json", "TEXT DEFAULT '[]'"),
        ("fund_flow_anomaly_refs_json", "TEXT DEFAULT '[]'"),
        ("fund_flow_unit_verified", "INTEGER DEFAULT 0"),
        ("fund_flow_individual_summary", "TEXT DEFAULT ''"),
        ("fund_flow_board_summary", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [S-005] selection_priority_gate — add priority gate columns
    for _col, _type in [
        ("composite_score", "REAL DEFAULT 0.0"),
        ("signal_category_hits_json", "TEXT DEFAULT '[]'"),
        ("positive_category_count", "INTEGER DEFAULT 0"),
        ("data_completeness", "REAL DEFAULT 0.0"),
        ("missing_evidence_json", "TEXT DEFAULT '[]'"),
        ("why_deep_ta", "TEXT DEFAULT ''"),
        ("why_not_deep_ta", "TEXT DEFAULT ''"),
        ("priority_rank", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [S-007] candidate_tier_budget — add tier budget columns
    for _col, _type in [
        ("tier", "TEXT DEFAULT ''"),
        ("ta_budget_priority", "INTEGER DEFAULT 0"),
        ("tier_reason", "TEXT DEFAULT ''"),
        ("missing_evidence_for_upgrade_json", "TEXT DEFAULT '[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [S-008] tradeflow_evidence_gate — add evidence gate columns
    for _col, _type in [
        ("tradeflow_data_completeness", "REAL DEFAULT 0.0"),
        ("missing_data_fields_json", "TEXT DEFAULT '[]'"),
        ("what_to_upgrade_json", "TEXT DEFAULT '[]'"),
        ("evidence_gate_applied", "INTEGER DEFAULT 0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [M-003] tradeflow_universe_manager — add universe_sources column
    for _col, _type in [
        ("universe_sources_json", "TEXT DEFAULT '[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [M-005] intraday_observe_state — add observe state columns
    for _col, _type in [
        ("observe_state", "TEXT DEFAULT 'WAITING'"),
        ("observe_trigger_count", "INTEGER DEFAULT 0"),
        ("observe_first_trigger_time", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tradeflow_candidates ADD COLUMN {_col} {_type}")
        except sqlite3.OperationalError:
            pass
    # [M-006] gated_deep_ta_dispatch — add deep TA dispatch columns
    for _col, _type in [
        ("deep_ta_status", "TEXT DEFAULT ''"),
        ("deep_ta_dispatch_reason", "TEXT DEFAULT ''"),
        ("deep_ta_model", "TEXT DEFAULT ''"),
        ("deep_ta_report_path", "TEXT DEFAULT ''"),
        ("deep_ta_dispatch_time", "TEXT DEFAULT ''"),
        ("deep_ta_position_context", "TEXT DEFAULT ''"),
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


def _calc_liquidity(df: pd.DataFrame, cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG) -> tuple[bool, float, float, float]:  # [M-004]
    """Check liquidity using Amount (turnover) as primary metric.

    Returns (is_low, avg_amount, avg_volume, threshold).
    - Uses Amount column if available, otherwise estimates via Close * Volume.
    """
    AMOUNT_THRESHOLD = cfg.amount_threshold  # [M-004]

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


def filter_symbol(symbol: str, df: pd.DataFrame, cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG) -> tuple[bool, list[str]]:  # [M-004]
    """Check if a symbol should be filtered out.

    Returns (should_filter, reason_list).
    """
    reasons = []

    if df is None or len(df) == 0:
        return True, ["数据缺失"]

    if len(df) < 40:
        return True, ["数据不足(需至少40日)"]

    is_low, avg_amount, avg_volume, threshold = _calc_liquidity(df, cfg)  # [M-004]
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
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,  # [M-004]
) -> list[CandidateSignal]:
    """Run all strategies on a symbol, return list of signals."""
    signals = []

    # VCP
    vcp = score_vcp(df, symbol, cfg=cfg)  # [M-004]
    if vcp is not None:
        signals.append(vcp)

    # Pullback Support
    pb = score_pullback_support(df, symbol, cfg=cfg)  # [M-004]
    if pb is not None:
        signals.append(pb)

    # Event Catalyst
    latest_close = float(df["Close"].iloc[-1]) if len(df) > 0 else 0.0
    ev = score_event_catalyst(symbol, news_texts, event_overrides, latest_close, cfg=cfg)  # [M-004]
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
    fund_flow_individual: Optional[str] = None,  # [T-003] fund_flow_anomaly_pool
    fund_flow_board: Optional[str] = None,  # [T-003] fund_flow_anomaly_pool
    cfg: Optional[StrategyConfig] = None,  # [M-004]
) -> tuple[Optional[Candidate], str]:
    """Evaluate one symbol through all strategies.

    Returns (Candidate, "") if any strategy hits, or (None, reason) if
    filtered or no strategy matched.
    """
    if cfg is None:
        cfg = DEFAULT_STRATEGY_CONFIG

    symbol = normalize_tradeflow_symbol(symbol)  # [UI-008] tradeflow_field_normalization
    name = resolve_tradeflow_name(symbol, name)  # [UI-008]

    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    # Fetch data if not provided
    if df is None:
        df = _fetch_price_data(symbol)

    # Filter check
    filtered, reasons = filter_symbol(symbol, df, cfg)  # [M-004]
    if filtered:
        return None, "；".join(reasons)

    # Run strategies
    signals = run_strategies(symbol, df, news_texts, event_overrides, cfg=cfg)  # [M-004]

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
                candidate.score = round(candidate.score * cfg.risk_lockup_score_factor, 2)  # [M-004]

    # [S-001] policy_version_signal — detect policy version and add bonus
    # Collect all available text sources: news_texts + event_override titles
    all_event_texts = list(news_texts) if news_texts else []
    if event_overrides:
        for ev in event_overrides:
            title = ev.get("title", "") or ev.get("headline", "")
            if title:
                all_event_texts.append(title)
    policy_result: PolicyVersionResult = detect_policy_version(  # [M-004]
        event_texts=all_event_texts if all_event_texts else None,
        cfg=cfg,
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
        if policy_result.version_score >= cfg.policy_need_deep_ta_score:  # [M-004]
            candidate.need_deep_ta = True

    # [S-002] narrative_quality_score — assess event narrative quality
    narrative_result: NarrativeQualityResult = score_narrative_quality(  # [M-004]
        event_texts=all_event_texts if all_event_texts else None,
        cfg=cfg,
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
        if narrative_result.narrative_score >= cfg.narrative_need_deep_ta_score:  # [M-004]
            candidate.need_deep_ta = True

    # [S-003] underwater_risk_flags — detect hidden structural risks
    risk_result: UnderwaterRiskResult = detect_underwater_risks(  # [M-004]
        event_texts=all_event_texts if all_event_texts else None,
        cfg=cfg,
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
    gb_result: GameBalanceResult = assess_game_balance(  # [M-004]
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
        cfg=cfg,
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

    # [T-003] fund_flow_anomaly_pool — detect capital anomaly
    ff_result: FundFlowAnomalyResult = detect_fund_flow_anomaly(  # [M-004]
        fund_flow_individual=fund_flow_individual,
        fund_flow_board=fund_flow_board,
        cfg=cfg,
    )
    if ff_result.fund_flow_anomaly_tags:
        candidate.fund_flow_anomaly_score = ff_result.fund_flow_anomaly_score
        candidate.fund_flow_anomaly_tags = ff_result.fund_flow_anomaly_tags
        candidate.fund_flow_anomaly_refs = ff_result.fund_flow_anomaly_refs
        candidate.fund_flow_unit_verified = ff_result.fund_flow_unit_verified
        candidate.fund_flow_individual_summary = ff_result.fund_flow_individual_summary
        candidate.fund_flow_board_summary = ff_result.fund_flow_board_summary
        # Fund flow anomaly only adds bonus, does not trigger strong conclusions
        positive_ff_tags = set(ff_result.fund_flow_anomaly_tags) - {FUND_FLOW_TAG_NET_OUTFLOW_DOMINANT}
        if positive_ff_tags:
            candidate.score = round(candidate.score + ff_result.fund_flow_anomaly_score, 2)
            candidate.strategy_tags = sorted(
                set(candidate.strategy_tags) | {STRATEGY_FUND_FLOW}
            )
        candidate.evidence["fund_flow_anomaly"] = {
            "anomaly_score": ff_result.fund_flow_anomaly_score,
            "anomaly_tags": ff_result.fund_flow_anomaly_tags,
            "anomaly_refs": ff_result.fund_flow_anomaly_refs,
            "unit_verified": ff_result.fund_flow_unit_verified,
            "individual_summary": ff_result.fund_flow_individual_summary,
            "board_summary": ff_result.fund_flow_board_summary,
        }
        # Unit unverified: do not boost need_deep_ta from fund flow alone
        if ff_result.fund_flow_unit_verified and positive_ff_tags and ff_result.fund_flow_anomaly_score >= cfg.fund_flow_need_deep_ta_score:  # [M-004]
            candidate.need_deep_ta = True
        # Net outflow dominant does not add score but records as observation

    # [S-005] selection_priority_gate — unified composite score and need_deep_ta gate
    # [S-009] selection_gate_fix — candidate.score already includes subscores;
    #   run_selection_priority_gate(score_already_includes_subscores=True by default)
    #   will NOT re-add version_score/narrative_score/fund_flow_anomaly_score/risk_penalty.
    gate_result: SelectionPriorityResult = run_selection_priority_gate(  # [M-004]
        score=candidate.score,
        strategy_tags=candidate.strategy_tags,
        policy_tags=candidate.policy_tags,
        version_score=candidate.version_score,
        narrative_score=candidate.narrative_score,
        fund_flow_anomaly_score=candidate.fund_flow_anomaly_score,
        fund_flow_anomaly_tags=candidate.fund_flow_anomaly_tags,
        fund_flow_unit_verified=candidate.fund_flow_unit_verified,
        risk_flags=candidate.risk_flags,
        risk_penalty=candidate.risk_penalty,
        resonance_count=candidate.resonance_count,
        game_balance=candidate.game_balance,
        has_price_data=True,
        has_event_data=bool(all_event_texts),
        has_fund_flow_data=bool(fund_flow_individual or fund_flow_board),
        has_risk_assessment=True,
        cfg=cfg,
    )
    candidate.composite_score = gate_result.composite_score
    candidate.signal_category_hits = gate_result.signal_category_hits
    candidate.positive_category_count = gate_result.positive_category_count
    candidate.data_completeness = gate_result.data_completeness
    candidate.missing_evidence = gate_result.missing_evidence
    candidate.why_deep_ta = gate_result.why_deep_ta
    candidate.why_not_deep_ta = gate_result.why_not_deep_ta
    candidate.priority_rank = gate_result.priority_rank
    candidate.evidence["selection_priority_gate"] = {
        "composite_score": gate_result.composite_score,
        "signal_category_hits": gate_result.signal_category_hits,
        "positive_category_count": gate_result.positive_category_count,
        "data_completeness": gate_result.data_completeness,
        "missing_evidence": gate_result.missing_evidence,
        "why_deep_ta": gate_result.why_deep_ta,
        "why_not_deep_ta": gate_result.why_not_deep_ta,
        "gate_passed": gate_result.gate_passed,
        "priority_rank": gate_result.priority_rank,
        "refs": gate_result.gate_refs,
    }
    candidate.need_deep_ta = gate_result.gate_passed

    # [S-007] candidate_tier_budget — classify into A/B/C tier with budget
    tier_result: TierBudgetResult = classify_candidate_tier(  # [M-004]
        priority_rank=gate_result.priority_rank,
        composite_score=gate_result.composite_score,
        positive_category_count=gate_result.positive_category_count,
        data_completeness=gate_result.data_completeness,
        missing_evidence=gate_result.missing_evidence,
        risk_flags=candidate.risk_flags,
        risk_penalty=candidate.risk_penalty,
        game_balance=candidate.game_balance,
        gate_passed=gate_result.gate_passed,
        why_not_deep_ta=gate_result.why_not_deep_ta,
        cfg=cfg,
    )
    candidate.tier = tier_result.tier
    candidate.ta_budget_priority = tier_result.ta_budget_priority
    candidate.tier_reason = tier_result.tier_reason
    candidate.missing_evidence_for_upgrade = tier_result.missing_evidence_for_upgrade
    candidate.evidence["tier_budget"] = {
        "tier": tier_result.tier,
        "ta_budget_priority": tier_result.ta_budget_priority,
        "tier_reason": tier_result.tier_reason,
        "missing_evidence_for_upgrade": tier_result.missing_evidence_for_upgrade,
        "why_not_deep_ta": tier_result.why_not_deep_ta,
    }

    # [S-008] tradeflow_evidence_gate — compute evidence completeness and apply gate
    has_ohlcv = df is not None and len(df) >= 40
    is_low_liquidity, _, _, _ = _calc_liquidity(df, cfg) if df is not None and len(df) >= 5 else (True, 0.0, 0.0, 0.0)  # [M-004]
    has_liquidity = not is_low_liquidity
    has_event_source = bool(all_event_texts)
    has_fund_flow_unit = candidate.fund_flow_unit_verified
    has_risk_labels = len(candidate.risk_flags) > 0 or candidate.risk_penalty != 0.0
    has_tech_signal = "VCP" in set(candidate.strategy_tags) or "PULLBACK_SUPPORT" in set(candidate.strategy_tags)
    has_policy_signal = candidate.version_score > 0 or len(candidate.policy_tags) > 0
    has_narrative_signal = candidate.narrative_score > 0
    positive_ff_tags = set(candidate.fund_flow_anomaly_tags) - {"NET_OUTFLOW_DOMINANT"}
    has_fund_signal = bool(positive_ff_tags) and candidate.fund_flow_anomaly_score > 0
    has_game_assessment = bool(candidate.game_balance)

    eg_result: EvidenceGateResult = compute_evidence_completeness(  # [M-004]
        has_ohlcv=has_ohlcv,
        has_liquidity=has_liquidity,
        has_event_source=has_event_source,
        has_fund_flow_unit=has_fund_flow_unit,
        has_risk_labels=has_risk_labels,
        has_tech_signal=has_tech_signal,
        has_policy_signal=has_policy_signal,
        has_narrative_signal=has_narrative_signal,
        has_fund_signal=has_fund_signal,
        has_game_assessment=has_game_assessment,
        cfg=cfg,
    )
    gate_override = apply_evidence_gate(
        candidate_completeness=eg_result.tradeflow_data_completeness,
        candidate_tier=candidate.tier,
        candidate_need_deep_ta=candidate.need_deep_ta,
        can_enter_a_tier=eg_result.can_enter_a_tier,
        can_trigger_deep_ta=eg_result.can_trigger_deep_ta,
        what_to_upgrade=eg_result.what_to_upgrade,
        missing_data_fields=eg_result.missing_data_fields,
        cfg=cfg,
    )
    candidate.tradeflow_data_completeness = eg_result.tradeflow_data_completeness
    candidate.missing_data_fields = eg_result.missing_data_fields
    candidate.what_to_upgrade = gate_override["what_to_upgrade"]
    candidate.evidence_gate_applied = gate_override["gate_applied"]
    if gate_override["gate_applied"]:
        if gate_override["tier"] != candidate.tier:
            candidate.tier = gate_override["tier"]
            candidate.ta_budget_priority = gate_override["ta_budget_priority"]
            if gate_override["tier_reason_addition"]:
                candidate.tier_reason = f"{gate_override['tier_reason_addition']}；{candidate.tier_reason}" if candidate.tier_reason else gate_override["tier_reason_addition"]
        if not gate_override["need_deep_ta"] and candidate.need_deep_ta:
            candidate.need_deep_ta = False
            if gate_override["why_not_deep_ta_addition"]:
                candidate.why_not_deep_ta = f"{gate_override['why_not_deep_ta_addition']}；{candidate.why_not_deep_ta}" if candidate.why_not_deep_ta else gate_override["why_not_deep_ta_addition"]
    candidate.evidence["evidence_gate"] = {
        "tradeflow_data_completeness": eg_result.tradeflow_data_completeness,
        "missing_data_fields": eg_result.missing_data_fields,
        "can_enter_a_tier": eg_result.can_enter_a_tier,
        "can_trigger_deep_ta": eg_result.can_trigger_deep_ta,
        "what_to_upgrade": eg_result.what_to_upgrade,
        "gate_applied": gate_override["gate_applied"],
        "refs": eg_result.evidence_gate_refs,
    }

    return candidate, ""


def save_candidate(candidate: Candidate, db_path: str) -> int:
    """Save a candidate to the database. Upsert on (trade_date, symbol)."""
    candidate.symbol = normalize_tradeflow_symbol(candidate.symbol)  # [UI-008]
    if not candidate.name or candidate.name == "--":
        candidate.name = resolve_tradeflow_name(candidate.symbol, candidate.name)  # [UI-008]
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
            "fund_flow_anomaly_score, fund_flow_anomaly_tags_json, "
            "fund_flow_anomaly_refs_json, fund_flow_unit_verified, "
            "fund_flow_individual_summary, fund_flow_board_summary, "
            "composite_score, signal_category_hits_json, positive_category_count, "
            "data_completeness, missing_evidence_json, why_deep_ta, why_not_deep_ta, "
            "priority_rank, "
            "tier, ta_budget_priority, tier_reason, missing_evidence_for_upgrade_json, "
            "tradeflow_data_completeness, missing_data_fields_json, what_to_upgrade_json, evidence_gate_applied, "
            "universe_sources_json, "
            "observe_state, observe_trigger_count, observe_first_trigger_time, "
            "deep_ta_status, deep_ta_dispatch_reason, deep_ta_model, "
            "deep_ta_report_path, deep_ta_dispatch_time, deep_ta_position_context, "
            "created_at, updated_at) "
            "VALUES ({}) ".format(",".join(["?"] * 64))
            + "ON CONFLICT(trade_date, symbol) DO UPDATE SET "
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
            "fund_flow_anomaly_score=excluded.fund_flow_anomaly_score, "
            "fund_flow_anomaly_tags_json=excluded.fund_flow_anomaly_tags_json, "
            "fund_flow_anomaly_refs_json=excluded.fund_flow_anomaly_refs_json, "
            "fund_flow_unit_verified=excluded.fund_flow_unit_verified, "
            "fund_flow_individual_summary=excluded.fund_flow_individual_summary, "
            "fund_flow_board_summary=excluded.fund_flow_board_summary, "
            "composite_score=excluded.composite_score, "
            "signal_category_hits_json=excluded.signal_category_hits_json, "
            "positive_category_count=excluded.positive_category_count, "
            "data_completeness=excluded.data_completeness, "
            "missing_evidence_json=excluded.missing_evidence_json, "
            "why_deep_ta=excluded.why_deep_ta, why_not_deep_ta=excluded.why_not_deep_ta, "
            "priority_rank=excluded.priority_rank, "
            "tier=excluded.tier, ta_budget_priority=excluded.ta_budget_priority, "
            "tier_reason=excluded.tier_reason, "
            "missing_evidence_for_upgrade_json=excluded.missing_evidence_for_upgrade_json, "
            "tradeflow_data_completeness=excluded.tradeflow_data_completeness, "
            "missing_data_fields_json=excluded.missing_data_fields_json, "
            "what_to_upgrade_json=excluded.what_to_upgrade_json, "
            "evidence_gate_applied=excluded.evidence_gate_applied, "
            "universe_sources_json=excluded.universe_sources_json, "
            "observe_state=excluded.observe_state, "
            "observe_trigger_count=excluded.observe_trigger_count, "
            "observe_first_trigger_time=excluded.observe_first_trigger_time, "
            "deep_ta_status=excluded.deep_ta_status, "
            "deep_ta_dispatch_reason=excluded.deep_ta_dispatch_reason, "
            "deep_ta_model=excluded.deep_ta_model, "
            "deep_ta_report_path=excluded.deep_ta_report_path, "
            "deep_ta_dispatch_time=excluded.deep_ta_dispatch_time, "
            "deep_ta_position_context=excluded.deep_ta_position_context, "
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
                row["fund_flow_anomaly_score"], row["fund_flow_anomaly_tags_json"],
                row["fund_flow_anomaly_refs_json"], row["fund_flow_unit_verified"],
                row["fund_flow_individual_summary"], row["fund_flow_board_summary"],
                row["composite_score"], row["signal_category_hits_json"],
                row["positive_category_count"], row["data_completeness"],
                row["missing_evidence_json"], row["why_deep_ta"], row["why_not_deep_ta"],
                row["priority_rank"],
                row["tier"], row["ta_budget_priority"],
                row["tier_reason"], row["missing_evidence_for_upgrade_json"],
                row["tradeflow_data_completeness"], row["missing_data_fields_json"],
                row["what_to_upgrade_json"], row["evidence_gate_applied"],
                row["universe_sources_json"],
                row["observe_state"], row["observe_trigger_count"],
                row["observe_first_trigger_time"],
                row["deep_ta_status"], row["deep_ta_dispatch_reason"],
                row["deep_ta_model"], row["deep_ta_report_path"],
                row["deep_ta_dispatch_time"], row["deep_ta_position_context"],
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
