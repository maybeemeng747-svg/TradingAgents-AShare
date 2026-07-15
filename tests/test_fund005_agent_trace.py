from tradingagents.agents.utils.agent_trace import (
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


def test_fundamentals_trace_has_actual_run_snapshot_and_safe_digest():
    traces = annotate_agent_traces(
        [{"agent": "fundamentals_analyst", "verdict": "中性"}],
        config=_config(), raw_evidence=_evidence(),
    )
    trace = traces[0]
    assert trace["provider"] == "zhipu"
    assert trace["model_tier"] == "mid"
    assert trace["actual_model"] == "glm-mid"
    assert trace["input_contract_digest"].startswith("sha256:")
    assert trace["input_statuses"] == [
        {"field": "company_profile", "status": "PARTIAL", "vendor": "akshare"},
        {"field": "fundamentals", "status": "HAS_DATA", "vendor": "akshare"},
    ]


def test_trace_never_contains_secret_or_raw_evidence_content():
    trace = annotate_agent_traces(
        [{"agent": "fundamentals_analyst"}], config=_config(), raw_evidence=_evidence()
    )[0]
    serialized = str(trace)
    assert "must-not-appear" not in serialized
    assert "private.example" not in serialized
    assert "very long text" not in serialized


def test_history_snapshot_is_independent_from_later_config_changes():
    snapshot = build_run_model_snapshot(_config())
    changed = _config()
    changed["mid_think_llm"] = "new-model"
    assert snapshot["mid"] == "glm-mid"
    assert changed["mid_think_llm"] == "new-model"
