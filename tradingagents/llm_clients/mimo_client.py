"""Xiaomi MiMo LLM client adapter.

Supports mimo-v2.5 and mimo-v2.5-pro via OpenAI-compatible endpoints.
Two endpoint variants:
  - Standard:  https://api.xiaomimimo.com/v1  (model prefix: xiaomi/)
  - Token Plan: https://token-plan-cn.xiaomimimo.com/v1 (no prefix)
"""

import logging
import os
from typing import Any, Optional

from .base_client import BaseLLMClient
from .openai_client import UnifiedChatOpenAI
from .rate_limiter import invoke_with_retry, ainvoke_with_retry
from .validators import validate_model

_logger = logging.getLogger(__name__)

# Endpoints
MIMO_STANDARD_URL = "https://api.xiaomimimo.com/v1"
MIMO_TOKEN_PLAN_URL = "https://token-plan-cn.xiaomimimo.com/v1"

# Model families that support extended reasoning
MIMO_REASONING_MODELS = {"mimo-v2.5-pro"}

# All known MiMo model names (both endpoints)
MIMO_MODELS = {
    # Token Plan models
    "mimo-v2.5",
    "mimo-v2.5-pro",
    # Standard endpoint models (xiaomi/ prefix)
    "xiaomi/mimo-v2-flash",
    "xiaomi/mimo-v2-pro",
    "xiaomi/mimo-v2.5",
    "xiaomi/mimo-v2.5-pro",
}


class MiMoClient(BaseLLMClient):
    """Client for Xiaomi MiMo models (OpenAI-compatible)."""

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model, base_url, **kwargs)

    @property
    def _effective_base_url(self) -> str:
        """Resolve base URL: explicit > env > token-plan default."""
        if self.base_url:
            return self.base_url
        return MIMO_TOKEN_PLAN_URL

    def get_llm(self) -> Any:
        """Return configured ChatOpenAI instance for MiMo."""
        target_url = self._effective_base_url
        llm_kwargs: dict[str, Any] = {"model": self.model}

        # Reasoning models don't support temperature
        if not self._is_reasoning_model(self.model):
            llm_kwargs["temperature"] = self.kwargs.get("temperature", 0)

        llm_kwargs["max_retries"] = 0
        llm_kwargs["timeout"] = self.kwargs.get("timeout", 300.0)

        _logger.info(
            "[LLM Client] Init MiMo (%s) at %s (Timeout=%ss)",
            self.model,
            target_url,
            llm_kwargs["timeout"],
        )

        llm_kwargs["base_url"] = target_url
        api_key = os.environ.get("TA_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if api_key:
            llm_kwargs["api_key"] = api_key

        for key in ("api_key", "callbacks", "reasoning_effort"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        return UnifiedChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        return validate_model("mimo", self.model)

    @staticmethod
    def _is_reasoning_model(model: str) -> bool:
        """Check if the MiMo model supports extended reasoning."""
        m = model.lower().split("/")[-1]  # strip xiaomi/ prefix
        return m in MIMO_REASONING_MODELS
