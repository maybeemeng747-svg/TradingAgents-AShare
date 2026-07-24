"""Privacy-safe model and input-contract traces for completed TA runs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


_TIER_BY_AGENT = {
    "market_analyst": "quick", "social_media_analyst": "quick",
    "news_analyst": "quick", "smart_money_analyst": "quick",
    "volume_price_analyst": "quick", "fundamentals_analyst": "mid",
    "macro_analyst": "mid",
}


def annotate_agent_traces(
    traces: Iterable[Mapping[str, Any]] | None,
    *, config: Mapping[str, Any], raw_evidence: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach immutable routing facts and a digest, never prompts or secrets."""
    statuses = _input_statuses(raw_evidence or {})
    digest = _digest(statuses)
    provider = str(config.get("llm_provider") or "unknown")
    output: list[dict[str, Any]] = []
    for raw_trace in traces or []:
        trace = dict(raw_trace)
        tier = _TIER_BY_AGENT.get(str(trace.get("agent") or ""), "unknown")
        model, fallback_from = _model_for_tier_with_fallback(config, tier)
        trace.update({
            "provider": provider, "model": model, "model_tier": tier,
            "prompt_version": "config-driven", "input_contract_digest": digest,
            "input_statuses": statuses, "requested_model": model,
        })
        if "actual_model" not in trace:
            trace["actual_model"] = "unknown"
        if fallback_from and "fallback_from" not in trace:
            trace["fallback_from"] = fallback_from
        output.append(trace)
    return output


def build_run_model_snapshot(config: Mapping[str, Any]) -> dict[str, str]:
    return {"provider": str(config.get("llm_provider") or "unknown"), **{
        tier: _model_for_tier(config, tier) for tier in ("quick", "mid", "deep", "ultra")
    }}


def _extract_actual_model_from_response(last_chunk: Any) -> str | None:
    """Extract the actual model name from the last LLM response chunk.

    Returns the model name if available in response metadata, else None.
    Caller should treat None as "unknown".
    """
    if last_chunk is None:
        return None
    response_meta = getattr(last_chunk, "response_metadata", None) or {}
    model = response_meta.get("model") or response_meta.get("model_name")
    if model:
        return str(model)
    return None


def _model_for_tier(config: Mapping[str, Any], tier: str) -> str:
    model, _ = _model_for_tier_with_fallback(config, tier)
    return model


def _model_for_tier_with_fallback(config: Mapping[str, Any], tier: str) -> tuple[str, str | None]:
    """Return (resolved_model, fallback_from) for a tier.

    fallback_from is the first-choice config key when it was empty and
    the resolved model came from a subsequent candidate.
    """
    candidates = {
        "quick": ("quick_think_llm",),
        "mid": ("mid_think_llm", "deep_think_llm", "quick_think_llm"),
        "deep": ("deep_think_llm", "quick_think_llm"),
        "ultra": ("ultra_think_llm", "deep_think_llm", "quick_think_llm"),
    }.get(tier, ())
    if candidates:
        first_key = candidates[0]
        for key in candidates:
            if config.get(key):
                resolved = str(config[key])
                if key != first_key:
                    return resolved, first_key
                return resolved, None
    return "unknown", None


def _input_statuses(raw_evidence: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"field": str(key), "status": str(entry.get("status") or "UNKNOWN"),
         "vendor": str(entry.get("vendor") or "unknown")}
        for key, entry in sorted(raw_evidence.items())
        if isinstance(entry, Mapping)
    ]


def _digest(statuses: list[dict[str, str]]) -> str:
    canonical = json.dumps(statuses, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
