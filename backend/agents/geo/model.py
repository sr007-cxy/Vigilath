"""DeepSeek brain model factory (OpenAI compatible) — aligned with query_expander's provider fallback:
   prefer direct DEEPSEEK_API_KEY, otherwise use OPENROUTER_API_KEY to call deepseek-chat via OpenRouter.

Note: DeepSeek here is **the API model used as the brain**, a separate line from "the observed DeepSeek web engine (via browser-service)".
"""
from __future__ import annotations

import os


# Scene -> default model: chat is fast (flash), diagnosis/heavy work is strong (pro). Overridable via env AGENT_MODEL_CHAT / AGENT_MODEL_PRO.
_SCENE_DEFAULT = {"chat": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}


def get_deepseek_model(scene: str = "chat"):
    """Build the Pydantic AI model (model chosen by scene). Raises a clear error when pydantic-ai or every key is missing.

    scene: 'chat' (conversation, default fast model v4-flash) / 'pro' (heavy work like diagnosis, v4-pro).
    Override: env AGENT_MODEL_CHAT / AGENT_MODEL_PRO (official model names, e.g. deepseek-v4-pro / deepseek-chat).
    """
    try:
        from pydantic_ai.models.openai import OpenAIModel
        from pydantic_ai.providers.openai import OpenAIProvider
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 pydantic-ai,请先 `pip install pydantic-ai-slim[openai]`") from e

    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    name = (os.environ.get(f"AGENT_MODEL_{scene.upper()}") or "").strip() or _SCENE_DEFAULT.get(scene, "deepseek-v4-pro")

    if ds_key:
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        model_name = name                                     # Official direct: use the raw model name
        provider = OpenAIProvider(base_url=base_url, api_key=ds_key)
    elif or_key:
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        model_name = name if "/" in name else f"deepseek/{name}"   # OpenRouter: add the deepseek/ prefix
        provider = OpenAIProvider(base_url=base_url, api_key=or_key)
    else:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 或 OPENROUTER_API_KEY")

    return OpenAIModel(model_name, provider=provider)


def describe_model(scene: str = "chat") -> tuple[str, str, str]:
    """(provider, model_name, base_url) — for accounting; doesn't build a model object.

    Metric definition identical to get_deepseek_model (same env parsing), so the agent line's provider / model on the
    "model usage" page matches the requests actually sent.
    Provider detection doesn't look at base_url: DEEPSEEK_BASE_URL now points to the neolink aggregation gateway, and guessing would be wrong.
    """
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    name = (os.environ.get(f"AGENT_MODEL_{scene.upper()}") or "").strip() \
        or _SCENE_DEFAULT.get(scene, "deepseek-v4-pro")
    if ds_key:
        return "deepseek", name, os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if or_key:
        return ("openrouter", name if "/" in name else f"deepseek/{name}",
                os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    return "", name, ""
