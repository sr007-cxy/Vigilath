"""build_agent() —— 构造 Pydantic AI Agent(单 agent + DeepSeek + 工具注册)。

框架耦合集中在本文件 + tools.py;换框架只改这两处,deps/methods/api 不动。
"""
from __future__ import annotations

import os
from functools import lru_cache

from geo.agent.deps import AgentDeps
from geo.agent.model import get_deepseek_model
from geo.agent.tools import TOOLS

SYSTEM_PROMPT = """你是 Vigilath 的 GEO/AEO 优化助手,帮品牌提升在 AI 引擎(ChatGPT/Perplexity/DeepSeek/豆包/通义/文心/元宝)中的可见性与被引用率。

工作流(对话推进):建主题 → 上传资料 → 共创并锁定提示词 → 诊断报告(带证据根因)→ 发文模板 → 模板确认后自动发文 → 复测飞轮。

边界(必须遵守):
- **写操作必须有用户明确意图**:create_topic / set_seed_prompts / expand_prompts / set_selected_queries /
  trigger_diagnosis / draft_articles / confirm_template / publish_drafts 等会**改数据/产稿/发布**的工具,
  **只在用户明确要求时才调**。用户只是询问或查看(如「我有没有主题」「今天投放效果」)时,
  **只用只读工具(get_*)查清并如实回答,绝不擅自新建或修改**。当前没有主题时,要告诉用户并询问是否创建,
  **不要自己直接建**。
- 引擎选择、查询调度、频率由平台固定,你和用户都不能指定;用户只看结果。
- 提示词一旦确认即锁定,不可再改。
- 只服务当前账号,绝不跨账号。
- 工具参数务必符合 schema;拿不准就先用只读工具查清,不要臆造 id。
- **发布(publish_drafts)是真实对外发布**:仅在用户明确表达「发布」意图时才调,先确认要发哪些;
  受环境护栏控制(测试环境不会真发)。其余只做诊断/归因/规划/产稿/数据查询。
"""


@lru_cache(maxsize=1)
def build_agent():
    """构造并缓存 Agent(无状态、工具注册一次;每次对话用 deps 注入账号上下文)。"""
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModelSettings

    settings = None
    # 走 OpenRouter 时:强制只路由「支持 tools 参数」的 provider,并优先 DeepSeek 一方,
    # 避免某些 provider 把 DeepSeek 原生 tool-call 格式泄漏成文本(实测间歇性发生)。
    # DeepSeek 官方 API(DEEPSEEK_API_KEY)原生稳定,不需要此 extra_body。
    if os.environ.get("OPENROUTER_API_KEY", "").strip() and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        settings = OpenAIChatModelSettings(
            extra_body={"provider": {"require_parameters": True, "order": ["DeepSeek"]}},
        )

    return Agent(
        get_deepseek_model(),
        deps_type=AgentDeps,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        retries=2,  # DeepSeek tool 漂:ModelRetry 回灌纠正
        model_settings=settings,
    )
