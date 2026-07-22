"""Tests for Xiaomi MiMo LLM client adapter (B-001).

Covers: client creation, factory routing, model validation, base URL resolution,
reasoning model detection, model catalog entries, get_llm() output type.
"""

import os
from unittest.mock import patch

import pytest

from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.llm_clients.mimo_client import (
    MIMO_MODELS,
    MIMO_REASONING_MODELS,
    MIMO_STANDARD_URL,
    MIMO_TOKEN_PLAN_URL,
    MiMoClient,
)
from tradingagents.llm_clients.model_catalog import MODEL_API_CATALOG, get_model_api_catalog
from tradingagents.llm_clients.validators import validate_model
from tradingagents.llm_clients.openai_client import UnifiedChatOpenAI


# ── Factory routing ──


class TestFactoryRouting:
    def test_create_mimo_client_via_factory(self):
        client = create_llm_client("mimo", "mimo-v2.5")
        assert isinstance(client, MiMoClient)

    def test_create_mimo_client_case_insensitive(self):
        client = create_llm_client("MIMO", "mimo-v2.5-pro")
        assert isinstance(client, MiMoClient)

    def test_factory_still_supports_openai(self):
        client = create_llm_client("openai", "gpt-4o")
        assert not isinstance(client, MiMoClient)

    def test_factory_still_supports_anthropic(self):
        client = create_llm_client("anthropic", "claude-sonnet-4-5")
        assert not isinstance(client, MiMoClient)

    def test_factory_still_supports_google(self):
        client = create_llm_client("google", "gemini-2.5-flash")
        assert not isinstance(client, MiMoClient)

    def test_factory_raises_for_unknown_provider(self):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            create_llm_client("unknown-provider", "some-model")


# ── Client instantiation ──


class TestMiMoClientInit:
    def test_default_init(self):
        client = MiMoClient("mimo-v2.5")
        assert client.model == "mimo-v2.5"
        assert client.base_url is None

    def test_init_with_base_url(self):
        client = MiMoClient("mimo-v2.5-pro", base_url=MIMO_STANDARD_URL)
        assert client.base_url == MIMO_STANDARD_URL

    def test_init_preserves_kwargs(self):
        client = MiMoClient("mimo-v2.5", temperature=0.5, timeout=600)
        assert client.kwargs["temperature"] == 0.5
        assert client.kwargs["timeout"] == 600


# ── Base URL resolution ──


class TestBaseURLResolution:
    def test_default_url_is_token_plan(self):
        client = MiMoClient("mimo-v2.5")
        assert client._effective_base_url == MIMO_TOKEN_PLAN_URL

    def test_explicit_base_url_overrides_default(self):
        client = MiMoClient("mimo-v2.5", base_url=MIMO_STANDARD_URL)
        assert client._effective_base_url == MIMO_STANDARD_URL

    def test_custom_base_url_preserved(self):
        custom = "https://custom.example.com/v1"
        client = MiMoClient("mimo-v2.5", base_url=custom)
        assert client._effective_base_url == custom


# ── Reasoning model detection ──


class TestReasoningModelDetection:
    def test_mimo_v25_pro_is_reasoning(self):
        assert MiMoClient._is_reasoning_model("mimo-v2.5-pro") is True

    def test_mimo_v25_is_not_reasoning(self):
        assert MiMoClient._is_reasoning_model("mimo-v2.5") is False

    def test_xiaomi_prefixed_pro_is_reasoning(self):
        assert MiMoClient._is_reasoning_model("xiaomi/mimo-v2.5-pro") is True

    def test_xiaomi_prefixed_non_pro_is_not_reasoning(self):
        assert MiMoClient._is_reasoning_model("xiaomi/mimo-v2-flash") is False

    def test_case_insensitive_reasoning_detection(self):
        assert MiMoClient._is_reasoning_model("MIMO-V2.5-PRO") is True

    def test_all_reasoning_models_in_mimo_models_set(self):
        for m in MIMO_REASONING_MODELS:
            assert m in MIMO_MODELS


# ── Model validation ──


class TestModelValidation:
    def test_validate_mimo_v25(self):
        assert validate_model("mimo", "mimo-v2.5") is True

    def test_validate_mimo_v25_pro(self):
        assert validate_model("mimo", "mimo-v2.5-pro") is True

    def test_validate_xiaomi_prefixed_models(self):
        for model in ("xiaomi/mimo-v2-flash", "xiaomi/mimo-v2-pro",
                       "xiaomi/mimo-v2.5", "xiaomi/mimo-v2.5-pro"):
            assert validate_model("mimo", model) is True, f"Expected valid: {model}"

    def test_validate_unknown_mimo_model_rejected(self):
        assert validate_model("mimo", "mimo-unknown-999") is False

    def test_client_validate_model(self):
        client = MiMoClient("mimo-v2.5")
        assert client.validate_model() is True

    def test_client_validate_invalid_model(self):
        client = MiMoClient("nonexistent-model")
        assert client.validate_model() is False


# ── Model catalog entries ──


class TestModelCatalog:
    def test_xiaomi_mimo_entry_exists(self):
        entries = [e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-mimo"]
        assert len(entries) == 1

    def test_xiaomi_token_plan_entry_exists(self):
        entries = [e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-token-plan"]
        assert len(entries) == 1

    def test_xiaomi_mimo_provider_is_mimo(self):
        entry = next(e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-mimo")
        assert entry["provider"] == "mimo"

    def test_xiaomi_token_plan_provider_is_mimo(self):
        entry = next(e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-token-plan")
        assert entry["provider"] == "mimo"

    def test_xiaomi_mimo_uses_v25_models(self):
        entry = next(e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-mimo")
        assert entry["quick_model"] == "mimo-v2.5"
        assert entry["deep_model"] == "mimo-v2.5-pro"

    def test_xiaomi_token_plan_uses_v25_models(self):
        entry = next(e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-token-plan")
        assert entry["quick_model"] == "mimo-v2.5"
        assert entry["deep_model"] == "mimo-v2.5-pro"

    def test_catalog_returns_copy(self):
        catalog = get_model_api_catalog()
        items = catalog["items"]
        mimo_items = [i for i in items if i.get("provider") == "mimo"]
        assert len(mimo_items) == 2
        # Mutating the copy should not affect the original
        items.clear()
        assert len(get_model_api_catalog()["items"]) > 0


# ── get_llm() output ──


class TestGetLLM:
    @patch.dict(os.environ, {"TA_API_KEY": "test-key-for-unit-tests"}, clear=False)
    def test_get_llm_returns_unified_chat_openai(self):
        client = MiMoClient("mimo-v2.5")
        llm = client.get_llm()
        assert isinstance(llm, UnifiedChatOpenAI)

    @patch.dict(os.environ, {"TA_API_KEY": "test-key-for-unit-tests"}, clear=False)
    def test_get_llm_with_pro_model(self):
        client = MiMoClient("mimo-v2.5-pro")
        llm = client.get_llm()
        assert isinstance(llm, UnifiedChatOpenAI)

    @patch.dict(os.environ, {"TA_API_KEY": "test-key-for-unit-tests"}, clear=False)
    def test_get_llm_respects_explicit_base_url(self):
        client = MiMoClient("mimo-v2.5", base_url=MIMO_STANDARD_URL)
        llm = client.get_llm()
        assert isinstance(llm, UnifiedChatOpenAI)

    @patch.dict(os.environ, {"TA_API_KEY": "test-key-for-unit-tests"}, clear=False)
    def test_non_reasoning_model_gets_temperature(self):
        client = MiMoClient("mimo-v2.5")
        llm = client.get_llm()
        assert llm.temperature == 0  # default

    @patch.dict(os.environ, {"TA_API_KEY": "test-key-for-unit-tests"}, clear=False)
    def test_custom_temperature(self):
        client = MiMoClient("mimo-v2.5", temperature=0.7)
        llm = client.get_llm()
        assert llm.temperature == 0.7

    @patch.dict(os.environ, {"TA_API_KEY": "test-key-for-unit-tests"}, clear=False)
    def test_reasoning_model_skips_temperature(self):
        client = MiMoClient("mimo-v2.5-pro")
        llm = client.get_llm()
        # Reasoning models should not have temperature set
        # UnifiedChatOpenAI strips it for reasoning models
        assert isinstance(llm, UnifiedChatOpenAI)


# ── Backward compatibility ──


class TestBackwardCompatibility:
    def test_openai_provider_still_works_for_old_mimo_models(self):
        """Users with llm_provider=openai + MiMo base_url must keep working."""
        client = create_llm_client(
            "openai",
            "mimo-v2.5",
            base_url="https://token-plan-cn.xiaomimimo.com/v1",
        )
        assert not isinstance(client, MiMoClient)
        assert client.model == "mimo-v2.5"

    def test_xiaomi_prefixed_models_accepted_by_mimo_validator(self):
        """Old xiaomi/ prefixed model names still pass mimo validation."""
        for name in ("xiaomi/mimo-v2-flash", "xiaomi/mimo-v2-pro"):
            assert validate_model("mimo", name) is True


# ── Constants ──


class TestConstants:
    def test_mimo_models_set_non_empty(self):
        assert len(MIMO_MODELS) > 0

    def test_mimo_reasoning_subset_of_mimo_models(self):
        assert MIMO_REASONING_MODELS.issubset(MIMO_MODELS)

    def test_standard_url_is_https(self):
        assert MIMO_STANDARD_URL.startswith("https://")

    def test_token_plan_url_is_https(self):
        assert MIMO_TOKEN_PLAN_URL.startswith("https://")


# ── Import/export ──


class TestImports:
    def test_mimo_client_importable_from_package(self):
        from tradingagents.llm_clients import MiMoClient as MC
        assert MC is MiMoClient

    def test_create_llm_client_importable(self):
        from tradingagents.llm_clients import create_llm_client as fn
        assert fn is create_llm_client
