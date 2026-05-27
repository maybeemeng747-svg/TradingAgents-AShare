import logging
import os
import time
from json import JSONDecodeError
from typing import Any, Optional

from langchain_openai import ChatOpenAI

_logger = logging.getLogger(__name__)

from .base_client import BaseLLMClient
from .rate_limiter import invoke_with_retry, ainvoke_with_retry
from .validators import validate_model


class UnifiedChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass that strips incompatible params for certain models."""

    def __init__(self, **kwargs):
        # 彻底移除重试参数，由构造函数统一控制
        kwargs.pop("response_parse_retries", None)
        kwargs.pop("response_parse_retry_delay", None)

        model = kwargs.get("model") or kwargs.get("model_name", "")
        base_url = kwargs.get("base_url")

        # LOG_LEVEL=DEBUG 时开启 LangChain verbose，打印完整的 LLM 请求和响应
        if os.environ.get("LOG_LEVEL", "").upper() == "DEBUG":
            kwargs["verbose"] = True

        # 1. Reasoning models (O1 etc) typically don't support temperature
        if self._is_reasoning_model(model):
            kwargs.pop("temperature", None)
            kwargs.pop("top_p", None)

        # 2. Moonshot (Kimi) models often strictly require temperature=1
        if self._is_moonshot_model(model, base_url):
            kwargs["temperature"] = 1

        super().__init__(**kwargs)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """Invoke with retry + concurrency control via rate_limiter."""
        result = invoke_with_retry(self, input, config=config, **kwargs)
        if _logger.isEnabledFor(logging.DEBUG):
            content = result.content if hasattr(result, "content") else str(result)
            _logger.debug(f"[LLM Response] model={self.model_name} length={len(content)}\n{content}")
        return result

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """Async invoke with retry + concurrency control via rate_limiter."""
        result = await ainvoke_with_retry(self, input, config=config, **kwargs)
        if _logger.isEnabledFor(logging.DEBUG):
            content = result.content if hasattr(result, "content") else str(result)
            _logger.debug(f"[LLM Response] model={self.model_name} length={len(content)}\n{content}")
        return result

    @staticmethod
    def _is_reasoning_model(model: str) -> bool:
        """Check if model is a reasoning model."""
        model_lower = str(model).lower()
        return (
            model_lower.startswith("o1")
            or model_lower.startswith("o3")
            or "gpt-5" in model_lower
            or "-r1" in model_lower
            or "thinking" in model_lower
            or "reasoning" in model_lower
            or "deepseek-v4-pro" in model_lower
            or "reasoner" in model_lower  # deepseek-reasoner
        )

    @staticmethod
    def _is_moonshot_model(model: str, base_url: Optional[str] = None) -> bool:
        """Check if model or base_url is from Moonshot (Kimi)."""
        m = str(model).lower()
        b = (base_url or "").lower()
        return "moonshot" in m or "kimi" in m or "moonshot" in b or "kimi" in b


class OpenAIClient(BaseLLMClient):
    """Client for OpenAI, Ollama, OpenRouter, and xAI providers."""

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = provider.lower()

    def get_llm(self) -> Any:
        """Return configured ChatOpenAI instance with retry + concurrency control."""
        llm_kwargs = {"model": self.model}

        if not UnifiedChatOpenAI._is_reasoning_model(self.model):
            llm_kwargs["temperature"] = self.kwargs.get("temperature", 0)

        # ── 稳定性配置 ──
        # 1. 禁用 LangChain 内置重试：由 rate_limiter 统一控制重试策略
        llm_kwargs["max_retries"] = 0

        # 2. 超长超时：默认 300 秒，给足推理模型思考时间
        llm_kwargs["timeout"] = self.kwargs.get("timeout", 300.0)

        target_url = self.base_url or "https://api.openai.com/v1"
        if self.provider == "xai": target_url = "https://api.x.ai/v1"
        elif self.provider == "openrouter": target_url = "https://openrouter.ai/api/v1"
        elif self.provider == "ollama": target_url = "http://localhost:11434/v1"

        print(f"[LLM Client] Init {self.provider} ({self.model}) at {target_url} (Retries=rate_limiter, Timeout={llm_kwargs['timeout']}s)")

        if self.provider == "xai":
            llm_kwargs["base_url"] = "https://api.x.ai/v1"
            api_key = os.environ.get("XAI_API_KEY")
            if api_key: llm_kwargs["api_key"] = api_key
        elif self.provider == "openrouter":
            llm_kwargs["base_url"] = "https://openrouter.ai/api/v1"
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if api_key: llm_kwargs["api_key"] = api_key
        elif self.provider == "ollama":
            llm_kwargs["base_url"] = "http://localhost:11434/v1"
            llm_kwargs["api_key"] = "ollama"
        elif self.base_url:
            llm_kwargs["base_url"] = self.base_url
            # For custom OpenAI-compatible endpoints (e.g. Zhipu), read TA_API_KEY
            api_key = os.environ.get("TA_API_KEY") or os.environ.get("OPENAI_API_KEY")
            if api_key:
                llm_kwargs["api_key"] = api_key

        # Pass remaining keys
        for key in ("api_key", "callbacks", "reasoning_effort"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        return UnifiedChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        return validate_model(self.provider, self.model)
