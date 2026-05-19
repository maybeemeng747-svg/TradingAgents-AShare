"""Shared model/API catalog for UI, docs, and operator audits.

The catalog describes endpoints this project knows how to call. It never
stores API keys. User keys are still resolved from encrypted per-user config
or environment variables at runtime.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


MODEL_API_CATALOG_VERSION = "2026-05-15"


MODEL_API_CATALOG: list[dict[str, Any]] = [
    {
        "id": "openai",
        "label": "OpenAI",
        "provider": "openai",
        "protocol": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "quick_model": "gpt-4o-mini",
        "deep_model": "gpt-4o",
        "key_scope": "openai:https://api.openai.com/v1",
        "status": "supported",
        "cost_note": "Use paid OpenAI account quota.",
    },
    {
        "id": "zhipu",
        "label": "Zhipu AI",
        "provider": "openai",
        "protocol": "OpenAI-compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "quick_model": "glm-4.5-air",
        "deep_model": "glm-4.5",
        "key_scope": "openai:https://open.bigmodel.cn/api/paas/v4",
        "status": "supported",
        "cost_note": "Use standard Zhipu account quota.",
    },
    {
        "id": "zhipu-coding",
        "label": "Zhipu Coding Plan",
        "provider": "openai",
        "protocol": "OpenAI-compatible",
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "quick_model": "glm-4.5-air",
        "deep_model": "glm-5-turbo",
        "key_scope": "openai:https://open.bigmodel.cn/api/coding/paas/v4",
        "status": "recommended",
        "cost_note": "Preferred local default when Coding Plan quota is available.",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "provider": "openai",
        "protocol": "OpenAI-compatible",
        "base_url": "https://api.deepseek.com/v1",
        "quick_model": "deepseek-chat",
        "deep_model": "deepseek-reasoner",
        "key_scope": "openai:https://api.deepseek.com/v1",
        "status": "supported",
        "cost_note": "Paid DeepSeek quota. Avoid as default when balance is low.",
    },
    {
        "id": "moonshot",
        "label": "Moonshot AI (Kimi)",
        "provider": "openai",
        "protocol": "OpenAI-compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "quick_model": "moonshot-v1-8k",
        "deep_model": "moonshot-v1-32k",
        "key_scope": "openai:https://api.moonshot.cn/v1",
        "status": "supported",
        "cost_note": "Paid Moonshot quota.",
    },
    {
        "id": "xiaomi-mimo",
        "label": "Xiaomi MiMo",
        "provider": "openai",
        "protocol": "OpenAI-compatible",
        "base_url": "https://api.xiaomimimo.com/v1",
        "quick_model": "xiaomi/mimo-v2-flash",
        "deep_model": "xiaomi/mimo-v2-pro",
        "key_scope": "openai:https://api.xiaomimimo.com/v1",
        "status": "supported",
        "cost_note": "Paid Xiaomi MiMo quota.",
    },
    {
        "id": "xiaomi-token-plan",
        "label": "Xiaomi MiMo Token Plan",
        "provider": "openai",
        "protocol": "OpenAI-compatible",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "quick_model": "mimo-v2.5",
        "deep_model": "mimo-v2.5-pro",
        "key_scope": "openai:https://token-plan-cn.xiaomimimo.com/v1",
        "status": "supported",
        "cost_note": "Use MiMo token-plan quota.",
    },
    {
        "id": "dashscope",
        "label": "Alibaba DashScope",
        "provider": "openai",
        "protocol": "OpenAI-compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "quick_model": "qwen-plus",
        "deep_model": "qwen-max",
        "key_scope": "openai:https://dashscope.aliyuncs.com/compatible-mode/v1",
        "status": "supported",
        "cost_note": "Paid DashScope quota.",
    },
    {
        "id": "siliconflow",
        "label": "SiliconFlow",
        "provider": "openai",
        "protocol": "OpenAI-compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "quick_model": "",
        "deep_model": "",
        "key_scope": "openai:https://api.siliconflow.cn/v1",
        "status": "custom-model",
        "cost_note": "Model names depend on the selected SiliconFlow route.",
    },
    {
        "id": "anthropic",
        "label": "Anthropic Claude",
        "provider": "anthropic",
        "protocol": "Anthropic",
        "base_url": "",
        "quick_model": "claude-haiku-4-5",
        "deep_model": "claude-sonnet-4-5",
        "key_scope": "anthropic",
        "status": "supported",
        "cost_note": "Paid Anthropic quota.",
    },
    {
        "id": "google",
        "label": "Google Gemini",
        "provider": "google",
        "protocol": "Google",
        "base_url": "",
        "quick_model": "gemini-2.5-flash",
        "deep_model": "gemini-2.5-pro",
        "key_scope": "google",
        "status": "supported",
        "cost_note": "Paid Google AI quota.",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "provider": "openrouter",
        "protocol": "OpenAI-compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "quick_model": "",
        "deep_model": "",
        "key_scope": "openrouter:https://openrouter.ai/api/v1",
        "status": "custom-model",
        "cost_note": "Cost depends on the routed model.",
    },
    {
        "id": "ollama",
        "label": "Ollama",
        "provider": "ollama",
        "protocol": "OpenAI-compatible local",
        "base_url": "http://localhost:11434/v1",
        "quick_model": "",
        "deep_model": "",
        "key_scope": "ollama:http://localhost:11434/v1",
        "status": "local",
        "cost_note": "Local runtime. No hosted API token cost.",
    },
]


def get_model_api_catalog() -> dict[str, Any]:
    """Return a copy so callers cannot mutate module-level catalog entries."""
    return {
        "version": MODEL_API_CATALOG_VERSION,
        "items": deepcopy(MODEL_API_CATALOG),
    }
