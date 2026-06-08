"""DeepSeek 大脑模型工厂(OpenAI 兼容)——对齐 query_expander 的 provider 回退:
   优先 DEEPSEEK_API_KEY 直连,否则用 OPENROUTER_API_KEY 走 OpenRouter 调 deepseek-chat。

注意:此处 DeepSeek 是**当大脑的 API 模型**,与「被观测的 DeepSeek 网页引擎(走 browser-service)」是两条线。
"""
from __future__ import annotations

import os


def get_deepseek_model():
    """构造 Pydantic AI 模型。无 pydantic-ai / 无任何 key 时给出明确报错。"""
    try:
        from pydantic_ai.models.openai import OpenAIModel
        from pydantic_ai.providers.openai import OpenAIProvider
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 pydantic-ai,请先 `pip install pydantic-ai-slim[openai]`") from e

    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()

    if ds_key:
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        model_name = "deepseek-v4-pro"          # 写死:官方直连用 deepseek-v4-pro
        provider = OpenAIProvider(base_url=base_url, api_key=ds_key)
    elif or_key:
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        model_name = "deepseek/deepseek-v4-pro"  # 写死:OpenRouter 回退也用 v4-pro
        provider = OpenAIProvider(base_url=base_url, api_key=or_key)
    else:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 或 OPENROUTER_API_KEY")

    return OpenAIModel(model_name, provider=provider)
