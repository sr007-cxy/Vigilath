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

你也能查/配**舆情监控**:用户问「今天舆情怎么样/有没有负面/风险」→ get_sentiment_today;要改监测词时 → configure_sentiment。
sentiment_score 用百分比表述,stance/intent/factuality 等枚举用中文。

边界(必须遵守):
- **写操作必须有用户明确意图**:create_topic / set_seed_prompts / expand_prompts / set_selected_queries /
  trigger_diagnosis / draft_articles / confirm_template / publish_drafts 等会**改数据/产稿/发布**的工具,
  **只在用户明确要求时才调**。用户只是询问或查看(如「我有没有主题」「今天投放效果」)时,
  **只用只读工具(get_*)查清并如实回答,绝不擅自新建或修改**。当前没有主题时,要告诉用户并询问是否创建,
  **不要自己直接建**。
- **特别注意「查看 vs 生成」别搞反**:用户问「**今天发了哪些文章**」「发布了什么」「看看文章」「文章进度/发布进度」
  都是**查看意图 → 调 get_publish_status(只读)**;**只有**用户明确说「生成/写/帮我产稿/创作文章」时才调 draft_articles。
  「发了哪些」是问过去已发布的,绝不是让你去生成新文章。拿不准就先问,别擅自生成。
- 引擎选择、查询调度、频率由平台固定,你和用户都不能指定;用户只看结果。
- 提示词一旦确认即锁定,不可再改。
- 只服务当前账号,绝不跨账号。
- **主题/品牌名一律以工具实时返回为准**(get_topic / get_sentiment_today 等),**绝不要用历史对话里记得的旧主题名**——
  主题可能已被改名或删除。回答里提到品牌/主题时,用工具这次返回的名字,不要凭记忆说一个可能已不存在的名字。
- **只调与用户当前问题直接相关的工具**:用户问舆情就只查舆情,别顺带把投放/发布/命中也跑一遍;问什么查什么。
- 工具参数务必符合 schema;拿不准就先用只读工具查清,不要臆造 id。
- **发布(publish_drafts)是真实对外发布**:仅在用户明确表达「发布」意图时才调,先确认要发哪些;
  受环境护栏控制(测试环境不会真发)。其余只做诊断/归因/规划/产稿/数据查询。
"""


# 场景 → 模型:对话用快模型(v4-flash),诊断/重活用 v4-pro;均可用 env AGENT_MODEL_{CHAT,PRO} 覆盖(见 model.py)。
@lru_cache(maxsize=4)
def build_agent(scene: str = "chat"):
    """构造并缓存 Agent(按场景选模型;无状态、工具注册一次,每次对话用 deps 注入账号上下文)。"""
    return _make_agent(TOOLS, scene)


def _make_agent(tools, scene: str = "chat"):
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModelSettings

    settings = None
    # 走 OpenRouter 时:强制只路由「支持 tools 参数」且优先 DeepSeek 的 provider,治 tool-call 泄漏。
    if os.environ.get("OPENROUTER_API_KEY", "").strip() and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        settings = OpenAIChatModelSettings(
            extra_body={"provider": {"require_parameters": True, "order": ["DeepSeek"]}},
        )
    return Agent(
        get_deepseek_model(scene),
        deps_type=AgentDeps,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        retries=2,  # DeepSeek tool 漂:ModelRetry 回灌纠正
        model_settings=settings,
        # DeepSeek 常在同一条响应里同时吐文本 + tool_call,默认 end_strategy='early'
        # 会把文本当最终结果、跳过工具(卡片全 0/0)。exhaustive 强制执行完所有工具调用。
        end_strategy="exhaustive",
    )


# ── 对外(embed)agent:工具按能力收敛,且**永不**含 publish_drafts ────────
from geo.agent import tools as _t   # noqa: E402

_READ_TOOLS = [
    _t.get_topic, _t.get_prompts, _t.get_report, _t.get_batch_results,
    _t.get_growth_summary, _t.get_query_coverage, _t.get_today_effect,
    _t.get_publish_status, _t.list_unhit_queries, _t.list_pending_articles,
    _t.list_articles, _t.get_article, _t.get_sentiment_today, _t.ask_knowledge,
]
_WRITE_TOOLS = [   # 对外可写,但不含 publish_drafts(真实外发只内部触发)
    _t.create_topic, _t.set_seed_prompts, _t.expand_prompts, _t.set_selected_queries,
    _t.run_geo_checks, _t.trigger_diagnosis, _t.draft_articles,
    _t.approve_article, _t.reject_article, _t.configure_sentiment, _t.ingest_material,
    _t.confirm_template,
]


@lru_cache(maxsize=4)
def build_embed_agent(can_write: bool, scene: str = "chat"):
    """对外 agent:read-only 只给只读工具;含 write 再加写工具(永不含 publish_drafts)。按场景选模型。"""
    return _make_agent(_READ_TOOLS + (_WRITE_TOOLS if can_write else []), scene)
