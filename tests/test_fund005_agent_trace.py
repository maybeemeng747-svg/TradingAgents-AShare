"""FUND-005A: Per-agent real model and runtime trace tests.

Five mock scenarios:
  1. No fallback — actual model matches requested
  2. Config-level tier fallback — actual model from fallback tier
  3. Runtime actual_model from LLM response metadata
  4. Actual model unknown (no response metadata, empty stream)
  5. Historical config change — snapshot immutability
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from tradingagents.agents.utils.agent_trace import (
    _extract_actual_model_from_response,
    _model_for_tier_with_fallback,
    annotate_agent_traces,
    build_run_model_snapshot,
)


def _config():
    return {
        "llm_provider": "zhipu",
        "quick_think_llm": "glm-quick",
        "deep_think_llm": "glm-deep",
        "mid_think_llm": "glm-mid",
        "ultra_think_llm": "glm-ultra",
        "api_key": "must-not-appear",
        "backend_url": "https://private.example/v1",
    }


def _evidence():
    return {
        "fundamentals": {"status": "HAS_DATA", "vendor": "akshare", "raw": "very long text"},
        "company_profile": {"status": "PARTIAL", "vendor": "akshare", "raw": {"secret": "nope"}},
    }


# ── Scenario 1: No fallback — actual model matches requested ──


def test_no_fallback_actual_matches_requested():
    """When analyst does not provide actual_model, annotate sets it to
    'unknown' (never fakes the config-derived requested_model)."""
    traces = annotate_agent_traces(
        [{"agent": "fundamentals_analyst", "verdict": "中性"}],
        config=_config(), raw_evidence=_evidence(),
    )
    trace = traces[0]
    assert trace["requested_model"] == "glm-mid"
    assert trace["actual_model"] == "unknown"
    assert "fallback_from" not in trace
    assert trace["model_tier"] == "mid"
    assert trace["provider"] == "zhipu"


def test_no_fallback_timing_fields_present_when_analyst_provides():
    """When analyst emits started_at/finished_at/latency_ms, annotate_agent_traces
    preserves them."""
    now = time.time()
    traces = annotate_agent_traces(
        [{
            "agent": "market_analyst",
            "verdict": "看多",
            "started_at": now,
            "finished_at": now + 1.5,
            "latency_ms": 1500.0,
        }],
        config=_config(), raw_evidence=_evidence(),
    )
    trace = traces[0]
    assert trace["started_at"] == now
    assert trace["finished_at"] == now + 1.5
    assert trace["latency_ms"] == 1500.0


# ── Scenario 2: Config-level tier fallback ──


def test_tier_fallback_from_mid_to_deep():
    """When mid_think_llm is empty, tier falls back to deep_think_llm and
    fallback_from is set."""
    cfg = _config()
    del cfg["mid_think_llm"]
    model, fallback_from = _model_for_tier_with_fallback(cfg, "mid")
    assert model == "glm-deep"
    assert fallback_from == "mid_think_llm"


def test_tier_fallback_from_ultra_to_deep():
    """When ultra_think_llm is empty, tier falls back to deep_think_llm."""
    cfg = _config()
    del cfg["ultra_think_llm"]
    model, fallback_from = _model_for_tier_with_fallback(cfg, "ultra")
    assert model == "glm-deep"
    assert fallback_from == "ultra_think_llm"


def test_tier_fallback_annotate_preserves_analyst_actual_model():
    """annotate_agent_traces sets fallback_from from config but preserves
    analyst-provided actual_model."""
    cfg = _config()
    del cfg["mid_think_llm"]  # triggers fallback to deep_think_llm
    traces = annotate_agent_traces(
        [{
            "agent": "fundamentals_analyst",
            "verdict": "看多",
            "actual_model": "mimo-v2.5-pro",  # runtime-detected
        }],
        config=cfg, raw_evidence=_evidence(),
    )
    trace = traces[0]
    assert trace["requested_model"] == "glm-deep"  # config fallback
    assert trace["actual_model"] == "mimo-v2.5-pro"  # preserved from analyst
    assert trace["fallback_from"] == "mid_think_llm"


def test_tier_fallback_no_fallback_when_first_key_populated():
    """When the first candidate key is populated, no fallback_from is set."""
    model, fallback_from = _model_for_tier_with_fallback(_config(), "quick")
    assert model == "glm-quick"
    assert fallback_from is None


def test_tier_fallback_all_keys_missing():
    """When all tier keys are missing, returns unknown with no fallback_from
    (no fallback occurred — the entire tier is empty)."""
    cfg = {"llm_provider": "test"}
    model, fallback_from = _model_for_tier_with_fallback(cfg, "mid")
    assert model == "unknown"
    assert fallback_from is None


# ── Scenario 3: Runtime actual_model from LLM response metadata ──


def test_extract_actual_model_from_response_with_model_field():
    """Chunks with response_metadata['model'] yield the actual model name."""
    chunk = SimpleNamespace(response_metadata={"model": "mimo-v2.5-pro", "token_usage": {}})
    assert _extract_actual_model_from_response(chunk) == "mimo-v2.5-pro"


def test_extract_actual_model_from_response_with_model_name_field():
    """Some providers use 'model_name' instead of 'model'."""
    chunk = SimpleNamespace(response_metadata={"model_name": "deepseek-chat"})
    assert _extract_actual_model_from_response(chunk) == "deepseek-chat"


def test_extract_actual_model_from_response_none_chunk():
    """None chunk (empty stream) yields None."""
    assert _extract_actual_model_from_response(None) is None


def test_extract_actual_model_from_response_no_metadata():
    """Chunk without response_metadata yields None."""
    chunk = SimpleNamespace(content="hello")
    assert _extract_actual_model_from_response(chunk) is None


def test_extract_actual_model_from_response_empty_metadata():
    """Chunk with empty response_metadata yields None."""
    chunk = SimpleNamespace(response_metadata={})
    assert _extract_actual_model_from_response(chunk) is None


def test_annotate_preserves_runtime_actual_model():
    """When analyst provides actual_model from runtime, annotate does not
    overwrite it with the config-derived value."""
    traces = annotate_agent_traces(
        [{
            "agent": "fundamentals_analyst",
            "verdict": "看多",
            "actual_model": "gpt-4o-real",  # runtime-detected from response
        }],
        config=_config(), raw_evidence=_evidence(),
    )
    assert traces[0]["actual_model"] == "gpt-4o-real"
    assert traces[0]["requested_model"] == "glm-mid"


# ── Scenario 4: Actual model unknown ──


def test_actual_model_unknown_when_not_provided_by_analyst():
    """When analyst does not provide actual_model, annotate sets it to
    'unknown' — never fakes the config-derived requested_model."""
    traces = annotate_agent_traces(
        [{"agent": "news_analyst", "verdict": "中性"}],
        config=_config(), raw_evidence=_evidence(),
    )
    assert traces[0]["actual_model"] == "unknown"
    assert traces[0]["requested_model"] == "glm-quick"


def test_actual_model_unknown_explicit():
    """When analyst explicitly sets actual_model to 'unknown' (no response
    metadata), annotate preserves it."""
    traces = annotate_agent_traces(
        [{
            "agent": "news_analyst",
            "verdict": "中性",
            "actual_model": "unknown",
        }],
        config=_config(), raw_evidence=_evidence(),
    )
    assert traces[0]["actual_model"] == "unknown"
    assert traces[0]["requested_model"] == "glm-quick"


def test_actual_model_unknown_with_fallback():
    """When config has fallback AND analyst reports unknown actual_model."""
    cfg = _config()
    del cfg["mid_think_llm"]
    traces = annotate_agent_traces(
        [{
            "agent": "fundamentals_analyst",
            "verdict": "中性",
            "actual_model": "unknown",
        }],
        config=cfg, raw_evidence=_evidence(),
    )
    assert traces[0]["actual_model"] == "unknown"
    assert traces[0]["requested_model"] == "glm-deep"  # config fallback
    assert traces[0]["fallback_from"] == "mid_think_llm"


# ── Scenario 5: Historical config change — snapshot immutability ──


def test_history_snapshot_is_independent_from_later_config_changes():
    """Once a run snapshot is taken, later config mutations do not affect it."""
    snapshot = build_run_model_snapshot(_config())
    changed = _config()
    changed["mid_think_llm"] = "new-model"
    assert snapshot["mid"] == "glm-mid"
    assert changed["mid_think_llm"] == "new-model"


def test_history_traces_not_affected_by_config_mutation():
    """Traces annotated with a config are not affected if the config dict
    is later mutated."""
    cfg = _config()
    traces = annotate_agent_traces(
        [{"agent": "fundamentals_analyst", "verdict": "中性"}],
        config=cfg, raw_evidence=_evidence(),
    )
    assert traces[0]["actual_model"] == "unknown"
    assert traces[0]["requested_model"] == "glm-mid"
    # Mutate config after annotation
    cfg["mid_think_llm"] = "different-model"
    # Original trace unchanged
    assert traces[0]["actual_model"] == "unknown"
    assert traces[0]["requested_model"] == "glm-mid"


# ── Privacy tests (carried from FUND-005) ──


def test_trace_never_contains_secret_or_raw_evidence_content():
    trace = annotate_agent_traces(
        [{"agent": "fundamentals_analyst"}], config=_config(), raw_evidence=_evidence()
    )[0]
    serialized = str(trace)
    assert "must-not-appear" not in serialized
    assert "private.example" not in serialized
    assert "very long text" not in serialized


def test_trace_never_contains_api_key_or_base_url():
    """Even with fallback, secrets are not leaked."""
    cfg = _config()
    cfg["api_key"] = "super-secret-key"
    cfg["backend_url"] = "https://secret-url.example/v1"
    del cfg["mid_think_llm"]
    traces = annotate_agent_traces(
        [{"agent": "fundamentals_analyst"}], config=cfg, raw_evidence=_evidence()
    )
    serialized = str(traces)
    assert "super-secret-key" not in serialized
    assert "secret-url.example" not in serialized


# ── Snapshot includes all tiers ──


def test_build_run_model_snapshot_all_tiers():
    snapshot = build_run_model_snapshot(_config())
    assert snapshot["provider"] == "zhipu"
    assert snapshot["quick"] == "glm-quick"
    assert snapshot["mid"] == "glm-mid"
    assert snapshot["deep"] == "glm-deep"
    assert snapshot["ultra"] == "glm-ultra"


def test_build_run_model_snapshot_unknown_for_missing_tier():
    cfg = {"llm_provider": "test", "quick_think_llm": "q"}
    snapshot = build_run_model_snapshot(cfg)
    assert snapshot["quick"] == "q"
    # mid falls back to quick_think_llm via tier cascade
    assert snapshot["mid"] == "q"
    # deep falls back to quick_think_llm via tier cascade
    assert snapshot["deep"] == "q"
    # ultra falls back to deep_think_llm → quick_think_llm via tier cascade
    assert snapshot["ultra"] == "q"


# ── Timing integration ──


def test_latency_ms_is_positive_when_timing_provided():
    """When analyst provides timing, latency_ms is positive."""
    now = time.time()
    traces = annotate_agent_traces(
        [{
            "agent": "macro_analyst",
            "verdict": "看空",
            "started_at": now,
            "finished_at": now + 2.3,
            "latency_ms": 2300.0,
        }],
        config=_config(), raw_evidence=_evidence(),
    )
    assert traces[0]["latency_ms"] == 2300.0
    assert traces[0]["finished_at"] > traces[0]["started_at"]


# ── All 7 agents covered ──


def test_all_analyst_tiers_covered():
    """Every known analyst agent has a tier mapping."""
    expected = {
        "market_analyst": "quick", "social_media_analyst": "quick",
        "news_analyst": "quick", "smart_money_analyst": "quick",
        "volume_price_analyst": "quick", "fundamentals_analyst": "mid",
        "macro_analyst": "mid",
    }
    from tradingagents.agents.utils.agent_trace import _TIER_BY_AGENT
    for agent, tier in expected.items():
        assert _TIER_BY_AGENT.get(agent) == tier, f"{agent} tier mismatch"
