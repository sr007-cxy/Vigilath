"""build_agent() —— 构造 Pydantic AI Agent(单 agent + DeepSeek + 工具注册)。

框架耦合集中在本文件 + tools.py;换框架只改这两处,deps/methods/api 不动。
"""
from __future__ import annotations

from functools import lru_cache

from geo.agent.deps import AgentDeps
from geo.agent.model import get_deepseek_model
from geo.agent.tools import TOOLS

SYSTEM_PROMPT = """你是 Vigilath 的 GEO/AEO 优化助手,帮品牌提升在 AI 引擎(ChatGPT/Perplexity/DeepSeek/豆包/通义/文心/元宝)中的可见性与被引用率。

工作流(对话推进):建主题 → 上传资料 → 共创并锁定提示词 → 诊断报告(带证据根因)→ 发文模板 → 模板确认后自动发文 → 复测飞轮。

边界(必须遵守):
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

    return Agent(
        get_deepseek_model(),
        deps_type=AgentDeps,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        retries=2,  # DeepSeek tool 漂:ModelRetry 回灌纠正
    )
