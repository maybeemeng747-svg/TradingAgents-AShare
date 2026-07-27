"""Tests for B-001-R1: MiMo provider config & credential routing fixes.

Covers:
- _get_provider_kwargs passes api_key for mimo provider (graph path)
- normalize_provider_key_scope produces mimo: scope for MiMo URLs
- get_user_provider_api_key falls back from mimo: to openai: scope
- model_catalog key_scope entries use mimo: prefix for MiMo
- MiMoClient.get_llm() uses api_key from kwargs when provided
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.llm_clients.model_catalog import MODEL_API_CATALOG
from tradingagents.llm_clients.mimo_client import MiMoClient, MIMO_TOKEN_PLAN_URL, MIMO_STANDARD_URL
from tradingagents.llm_clients.openai_client import UnifiedChatOpenAI


# ── _get_provider_kwargs: mimo passes api_key ──


class TestGraphProviderKwargsMiMo:
    """_get_provider_kwargs must pass api_key for mimo provider."""

    def _make_graph_stub(self, config: dict):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        stub = TradingAgentsGraph.__new__(TradingAgentsGraph)
        stub.config = config
        return stub

    def test_mimo_provider_passes_api_key(self):
        stub = self._make_graph_stub({
            "llm_provider": "mimo",
            "api_key": "test-mimo-key-123",
        })
        kwargs = stub._get_provider_kwargs()
        assert kwargs.get("api_key") == "test-mimo-key-123"

    def test_mimo_provider_no_api_key(self):
        stub = self._make_graph_stub({
            "llm_provider": "mimo",
            "api_key": "",
        })
        kwargs = stub._get_provider_kwargs()
        assert "api_key" not in kwargs

    def test_mimo_provider_missing_api_key(self):
        stub = self._make_graph_stub({
            "llm_provider": "mimo",
        })
        kwargs = stub._get_provider_kwargs()
        assert "api_key" not in kwargs

    def test_openai_provider_still_passes_api_key(self):
        stub = self._make_graph_stub({
            "llm_provider": "openai",
            "api_key": "openai-key",
        })
        kwargs = stub._get_provider_kwargs()
        assert kwargs.get("api_key") == "openai-key"

    def test_anthropic_provider_still_passes_api_key(self):
        stub = self._make_graph_stub({
            "llm_provider": "anthropic",
            "api_key": "anthropic-key",
        })
        kwargs = stub._get_provider_kwargs()
        assert kwargs.get("api_key") == "anthropic-key"

    def test_google_provider_still_passes_api_key(self):
        stub = self._make_graph_stub({
            "llm_provider": "google",
            "api_key": "google-key",
            "google_thinking_level": "low",
        })
        kwargs = stub._get_provider_kwargs()
        assert kwargs.get("api_key") == "google-key"
        assert kwargs.get("thinking_level") == "low"


# ── normalize_provider_key_scope: MiMo scope ──


class TestNormalizeProviderKeyScopeMiMo:
    """normalize_provider_key_scope must produce mimo: scope for MiMo URLs."""

    def test_mimo_standard_url_scope(self):
        from api.services.auth_service import normalize_provider_key_scope

        scope = normalize_provider_key_scope("mimo", MIMO_STANDARD_URL)
        assert scope == f"mimo:{MIMO_STANDARD_URL}"

    def test_mimo_token_plan_url_scope(self):
        from api.services.auth_service import normalize_provider_key_scope

        scope = normalize_provider_key_scope("mimo", MIMO_TOKEN_PLAN_URL)
        assert scope == f"mimo:{MIMO_TOKEN_PLAN_URL}"

    def test_openai_provider_scope_unchanged(self):
        from api.services.auth_service import normalize_provider_key_scope

        scope = normalize_provider_key_scope("openai", "https://api.openai.com/v1")
        assert scope == "openai:https://api.openai.com/v1"

    def test_mimo_no_base_url_scope(self):
        from api.services.auth_service import normalize_provider_key_scope

        scope = normalize_provider_key_scope("mimo", None)
        assert scope == "mimo"

    def test_mimo_scope_differs_from_openai_scope(self):
        from api.services.auth_service import normalize_provider_key_scope

        mimo_scope = normalize_provider_key_scope("mimo", MIMO_TOKEN_PLAN_URL)
        openai_scope = normalize_provider_key_scope("openai", MIMO_TOKEN_PLAN_URL)
        assert mimo_scope != openai_scope
        assert mimo_scope == f"mimo:{MIMO_TOKEN_PLAN_URL}"
        assert openai_scope == f"openai:{MIMO_TOKEN_PLAN_URL}"


# ── legacy runtime provider migration ──


class TestLegacyMiMoRuntimeMigration:
    """Legacy openai + MiMo URL configs must use MiMo in every runtime path."""

    @pytest.mark.parametrize("backend_url", [MIMO_STANDARD_URL, MIMO_TOKEN_PLAN_URL])
    def test_canonicalize_legacy_mimo_provider(self, backend_url):
        from api.services.auth_service import canonicalize_llm_provider

        assert canonicalize_llm_provider("openai", backend_url) == "mimo"
        assert canonicalize_llm_provider("mimo", backend_url) == "mimo"

    def test_non_mimo_openai_url_stays_openai(self):
        from api.services.auth_service import canonicalize_llm_provider

        assert canonicalize_llm_provider("openai", "https://example.com/v1") == "openai"

    def test_runtime_config_canonicalizes_saved_legacy_config(self):
        from api.main import _build_runtime_config

        legacy_config = {
            "llm_provider": "openai",
            "backend_url": MIMO_TOKEN_PLAN_URL,
            "quick_think_llm": "mimo-v2.5",
            "deep_think_llm": "mimo-v2.5-pro",
            "api_key": "legacy-key",
        }
        with patch("api.main._user_config_overrides", return_value=legacy_config):
            config = _build_runtime_config({}, user_id="legacy-user")

        assert config["llm_provider"] == "mimo"
        assert config["api_key"] == "legacy-key"

    def test_pending_config_canonicalizes_legacy_update(self):
        from api.main import UserRuntimeConfigUpdateRequest, _build_pending_runtime_config

        updates = UserRuntimeConfigUpdateRequest(
            llm_provider="openai",
            backend_url=MIMO_STANDARD_URL,
            quick_think_llm="xiaomi/mimo-v2-flash",
            deep_think_llm="xiaomi/mimo-v2-pro",
            warmup=False,
        )
        with (
            patch(
                "api.main._build_runtime_config",
                return_value={
                    "llm_provider": "openai",
                    "backend_url": "https://api.openai.com/v1",
                    "quick_think_llm": "gpt-4o-mini",
                    "deep_think_llm": "gpt-4o",
                },
            ),
            patch("api.main.auth_service.get_user_provider_api_key", return_value=None),
            patch("api.main.auth_service.has_user_provider_keys", return_value=False),
        ):
            config = _build_pending_runtime_config(updates, "legacy-user", MagicMock())

        assert config["llm_provider"] == "mimo"


# ── get_user_provider_api_key: backward-compat fallback ──


class TestGetUserProviderApiKeyMiMoFallback:
    """get_user_provider_api_key must fall back from mimo: to openai: scope."""

    def test_fallback_to_openai_scope(self):
        from api.services.auth_service import get_user_provider_api_key

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.api_key_encrypted = "encrypted"

        # First call (mimo: scope) returns None; second call (openai: scope) returns row
        mock_db.query.return_value.filter.return_value.first.side_effect = [None, mock_row]

        with patch("api.services.auth_service.decrypt_secret_with_fallback", return_value="fallback-key"):
            result = get_user_provider_api_key(mock_db, "user1", "mimo", MIMO_TOKEN_PLAN_URL)

        assert result == "fallback-key"
        # Verify two queries were made
        assert mock_db.query.return_value.filter.return_value.first.call_count == 2

    def test_primary_scope_found_no_fallback(self):
        from api.services.auth_service import get_user_provider_api_key

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.api_key_encrypted = "encrypted"

        mock_db.query.return_value.filter.return_value.first.return_value = mock_row

        with patch("api.services.auth_service.decrypt_secret_with_fallback", return_value="primary-key"):
            result = get_user_provider_api_key(mock_db, "user1", "mimo", MIMO_TOKEN_PLAN_URL)

        assert result == "primary-key"
        assert mock_db.query.return_value.filter.return_value.first.call_count == 1

    def test_no_key_at_either_scope(self):
        from api.services.auth_service import get_user_provider_api_key

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = get_user_provider_api_key(mock_db, "user1", "mimo", MIMO_TOKEN_PLAN_URL)

        assert result is None
        assert mock_db.query.return_value.filter.return_value.first.call_count == 2

    def test_openai_provider_no_fallback(self):
        from api.services.auth_service import get_user_provider_api_key

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = get_user_provider_api_key(mock_db, "user1", "openai", "https://api.openai.com/v1")

        assert result is None
        # Only one query (no fallback for non-mimo provider)
        assert mock_db.query.return_value.filter.return_value.first.call_count == 1

    def test_legacy_openai_provider_reads_new_mimo_scope(self):
        from api.services.auth_service import get_user_provider_api_key

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.api_key_encrypted = "encrypted"
        with (
            patch(
                "api.services.auth_service.get_user_provider_key",
                return_value=mock_row,
            ) as getter,
            patch(
                "api.services.auth_service.decrypt_secret_with_fallback",
                return_value="new-scope-key",
            ),
        ):
            result = get_user_provider_api_key(
                mock_db,
                "legacy-user",
                "openai",
                MIMO_TOKEN_PLAN_URL,
            )

        assert result == "new-scope-key"
        getter.assert_called_once_with(
            mock_db,
            "legacy-user",
            f"mimo:{MIMO_TOKEN_PLAN_URL}",
        )

    def test_legacy_openai_provider_falls_back_to_old_scope(self):
        from api.services.auth_service import get_user_provider_api_key

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.api_key_encrypted = "encrypted"
        with (
            patch(
                "api.services.auth_service.get_user_provider_key",
                side_effect=[None, mock_row],
            ) as getter,
            patch(
                "api.services.auth_service.decrypt_secret_with_fallback",
                return_value="legacy-scope-key",
            ),
        ):
            result = get_user_provider_api_key(
                mock_db,
                "legacy-user",
                "openai",
                MIMO_STANDARD_URL,
            )

        assert result == "legacy-scope-key"
        assert [call.args[2] for call in getter.call_args_list] == [
            f"mimo:{MIMO_STANDARD_URL}",
            f"openai:{MIMO_STANDARD_URL}",
        ]


# ── Model catalog key_scope entries ──


class TestModelCatalogMiMoKeyScope:
    """Model catalog MiMo entries must use mimo: prefix in key_scope."""

    def test_xiaomi_mimo_key_scope_uses_mimo_prefix(self):
        entry = next(e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-mimo")
        assert entry["key_scope"] == "mimo:https://api.xiaomimimo.com/v1"

    def test_xiaomi_token_plan_key_scope_uses_mimo_prefix(self):
        entry = next(e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-token-plan")
        assert entry["key_scope"] == "mimo:https://token-plan-cn.xiaomimimo.com/v1"

    def test_xiaomi_mimo_provider_is_mimo(self):
        entry = next(e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-mimo")
        assert entry["provider"] == "mimo"

    def test_xiaomi_token_plan_provider_is_mimo(self):
        entry = next(e for e in MODEL_API_CATALOG if e["id"] == "xiaomi-token-plan")
        assert entry["provider"] == "mimo"

    def test_key_scope_matches_provider_plus_url(self):
        """key_scope must equal '{provider}:{base_url}' for all entries with base_url."""
        from api.services.auth_service import normalize_provider_key_scope

        for entry in MODEL_API_CATALOG:
            if entry.get("base_url"):
                expected = normalize_provider_key_scope(entry["provider"], entry["base_url"])
                assert entry["key_scope"] == expected, (
                    f"Entry {entry['id']}: key_scope={entry['key_scope']} != expected={expected}"
                )


# ── MiMoClient kwargs forwarding ──


class TestMiMoClientKwargsForwarding:
    """MiMoClient.get_llm() must use api_key from kwargs when provided."""

    def test_kwargs_api_key_used(self):
        client = MiMoClient("mimo-v2.5", api_key="kwargs-key-123")
        llm = client.get_llm()
        assert isinstance(llm, UnifiedChatOpenAI)
        assert llm.openai_api_key.get_secret_value() == "kwargs-key-123"

    @patch.dict(os.environ, {"TA_API_KEY": "env-key"}, clear=False)
    def test_kwargs_api_key_overrides_env(self):
        client = MiMoClient("mimo-v2.5", api_key="kwargs-override")
        llm = client.get_llm()
        assert isinstance(llm, UnifiedChatOpenAI)
        assert llm.openai_api_key.get_secret_value() == "kwargs-override"

    @patch.dict(os.environ, {"TA_API_KEY": "env-key"}, clear=False)
    def test_env_key_used_when_no_kwargs(self):
        client = MiMoClient("mimo-v2.5")
        llm = client.get_llm()
        assert isinstance(llm, UnifiedChatOpenAI)
        assert llm.openai_api_key.get_secret_value() == "env-key"

    def test_base_url_from_kwargs(self):
        client = MiMoClient("mimo-v2.5", base_url=MIMO_STANDARD_URL, api_key="test-key")
        llm = client.get_llm()
        assert isinstance(llm, UnifiedChatOpenAI)
        base = llm.openai_api_base
        if hasattr(base, "get_secret_value"):
            base = base.get_secret_value()
        assert str(base) == MIMO_STANDARD_URL
