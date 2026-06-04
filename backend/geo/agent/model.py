"""DeepSeek 模型工厂(OpenAI 兼容)—— 复用 query_expander 的 DEEPSEEK_* 环境约定。

注意:此处 DeepSeek 是**当大脑的 API 模型**,与「被观测的 DeepSeek 网页引擎(走 browser-service)」是两条线。
"""
from __future__ import annotations

import os


def get_deepseek_model():
    """构造 Pydantic AI 的 DeepSeek 模型。pydantic-ai 未安装时给出明确报错。"""
    try:
        from pydantic_ai.models.openai import OpenAIModel
        from pydantic_ai.providers.openai import OpenAIProvider
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 pydantic-ai,请先 `pip install pydantic-ai`(见 requirements.txt)") from e

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model_name = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    return OpenAIModel(model_name, provider=provider)
