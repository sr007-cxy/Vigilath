"""Add 7 open-source-derived GEO content templates

Revision ID: b3f8c1d59e42
Revises: e6c1a9d47b83
Create Date: 2026-08-05 16:20:00.000000

Article generation templates ported from two open-source GEO projects, filling topics we lacked:

  - yaojingang/GEOFlow (Apache-2.0): GEO trust-style body / GEO ranking-style body, one set each in Chinese and English.
    Originals in database/migrations/2026_04_26_10*_add_{chinese,english}_geo_content_prompts.php
  - TD-yios/ai-content-forge (CC BY-NC-SA 4.0): TOP X ranked recommendations / product reviews / research reports.
    Originals in packages/api/src/modules/templates/template-presets.ts

Prompt bodies are copied verbatim from the original projects, with only three necessary adaptations (otherwise they would not run in our generation pipeline):

  1. Placeholders replaced with mustache variables supported by our _render_template
     forge:{currentYear}→{{year}} {currentYearMonth}→{{year_month}} {productName}→{{brand}}
           {productDescription}→{{product_desc}} {region}→{{region}} {competitors}→{{competitors}}
           {keywordSEO}→{{keyword_seo}} {primaryKeyword}→{{query}} {targetConversionWords}→{{seed}}
           {wordCountMin/Max}→{{length_min}}/{{length_max}} {targetPlatforms}→{{platform}}
           {rankPosition}→{{rank_position}}
     GEOFlow: {{title}} -> we do not preset a title, changed to 「围绕 {{query}} 自拟标题」 ("write your own title around {{query}}"); {{keyword}} -> {{seed}};
             {{Knowledge}} -> {{brand_block}}; Handlebars {{#if}} conditional sections are unsupported and expanded inline
  2. Output contract: the original projects output Markdown body directly, while our _call_llm only accepts strict JSON
     (title / summary / body), so each template ends with an appended JSON output requirement, and the original Format section
     is demoted to "the internal structure of body"
  3. Remove platform-specific CTAs / site names ("GeoFlow site" in the GEOFlow originals)

License note: GEOFlow is Apache-2.0; ai-content-forge is CC BY-NC-SA 4.0 (NonCommercial +
ShareAlike); copying its text is a decision borne by the project owners, and the source is credited here. Comparison notes in
docs/01-立项与规划/竞品研究/竞品/开源文章模板对照-ai-content-forge-GEOFlow.md
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f8c1d59e42'
down_revision: Union[str, None] = 'e6c1a9d47b83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Output contract section shared by all templates: our generation pipeline only parses JSON, so the originals' "output the body directly" cannot stay
_JSON_TAIL_ZH = (
    "\n\n【输出格式(本系统要求,优先级最高)】\n"
    "上面描述的结构是**正文 body 的内部结构**。最终只输出一个严格 JSON 对象,字段:\n"
    "title(≤40 字纯文本标题)/ summary(200 字内摘要)/ body(正文)。\n"
    "body 内允许 Markdown(## 小标题、列表、表格、**加粗**),表格和 FAQ 是 AI 摘要的抓手,"
    "该有就要有。不要输出 JSON 以外的任何内容,不要加 ``` 围栏。"
)

_JSON_TAIL_EN = (
    "\n\n[Output Format (system requirement, highest priority)]\n"
    "The structure described above is the internal structure of the article body. "
    "Return exactly one strict JSON object with the fields: "
    "title (plain text, <= 90 characters) / summary (<= 400 characters) / body (the article). "
    "Markdown is allowed inside body (## headings, lists, tables, **bold**) — tables and the FAQ "
    "are what AI systems extract, so keep them. Output nothing but the JSON object, no code fences."
)


TEMPLATES = [
    # ── GEOFlow (Apache-2.0) · Chinese ────────────────────────────────────────
    {
        "scope": "system",
        "locked": False,
        "name": "GEO 信任型正文(GEOFlow)",
        "kind": "信任长文",
        "target_platforms_json": '["知乎", "公众号", "搜狐号"]',
        "length_min": 1200,
        "length_max": 2200,
        "prompt_template": (
            "【Role - GEO内容策略专家】\n"
            "你是一位专精于GEO内容策略的资深编辑,擅长把复杂主题转化为适合AI搜索引用、摘要提炼和用户决策的中文文章。"
            "你写作时同时兼顾:\n"
            "- 信任建设:通过事实、案例、场景和可验证信息建立可信度\n"
            "- 语义主导权:围绕主题、关键词和问题空间构建答案块\n"
            "- 机器可读性:让AI系统能稳定提取结构、结论、表格和FAQ\n\n"
            "【Context】\n"
            "本文主题(用户搜索词):{{query}}\n"
            "核心关键词:{{seed}}\n"
            "品牌:{{brand}} · 行业:{{industry}} · 服务地域:{{region}}\n"
            "参考知识(品牌资料):\n{{brand_block}}\n\n"
            "【Task - 生成可发布的GEO正文】\n"
            "请围绕主题与关键词,生成一篇可直接发布到「{{platform}}」的中文长文。"
            "文章必须兼顾用户可读性、SEO/GEO可提取性和品牌信任感。标题由你自拟,不要沿用搜索词原文。\n\n"
            "【写作目标】\n"
            "1. 直接回答用户最关心的问题,帮助用户完成理解、比较或决策,而不是堆砌概念。\n"
            "2. 把主题写成可被AI搜索系统引用的答案型内容,而不是单纯的信息拼接。\n"
            "3. 在正文中体现经验、专业、权威、可信(E-E-A-T)的信号。\n\n"
            "【写作要求】\n"
            "1. 正文使用Markdown,标题层级清晰,控制在 {{length_min}}-{{length_max}} 字。\n"
            "2. 文章结构必须包含:\n"
            "   - 引言\n"
            "   - 3-5个主体小节\n"
            "   - 1个总结/结论小节\n"
            "   - 1组FAQ(2-4问)\n"
            "3. 引言要先解释问题背景、用户痛点或行业变化,快速交代本文会解决什么。\n"
            "4. 主体小节每节都要包含:核心结论、解释依据、场景化建议;避免空洞套话。\n"
            "5. 优先使用以下可信信号:量化信息、过程说明、案例、对比、注意事项、边界条件。没有把握的数据不要编造。\n"
            "6. 自然融入主题和关键词,不得做生硬堆砌;如果关键词不适合某段,不必强插。\n"
            "7. 在适合的位置使用列表或Markdown表格,至少提供1个结构化信息块,帮助AI直接提炼。\n"
            "8. 文风要专业、清晰、克制,避免夸张营销语,如「最强」「完美」「颠覆」等无证据表述。\n"
            "9. 优先吸收参考知识里的事实、观点和术语,但不要机械复制原句。\n"
            "10. 不要输出写作说明、字数说明、前言提示语,也不要出现「以下是文章」等套话。\n\n"
            "【body 的结构参考】\n"
            "## 核心摘要\n"
            "- 用3-5条要点概括核心结论、适合人群或关键判断\n\n"
            "## 一、引言\n"
            "- 说明问题背景、用户关心点、本文价值\n\n"
            "## 二、[主体小节1]\n"
            "- 结论 + 解释 + 建议\n\n"
            "## 三、[主体小节2]\n"
            "- 结论 + 解释 + 建议\n\n"
            "## 四、[主体小节3]\n"
            "- 结论 + 解释 + 建议\n\n"
            "## 五、关键对比 / 方法 / 注意事项\n"
            "- 优先使用列表或表格\n\n"
            "## 六、FAQ\n"
            "### Q1. ...\n"
            "### Q2. ...\n\n"
            "## 七、结论\n"
            "- 给出总结判断、适用建议或下一步动作"
            + _JSON_TAIL_ZH
        ),
    },
    {
        "scope": "system",
        "locked": False,
        "name": "GEO 榜单型正文(GEOFlow)",
        "kind": "决策榜单",
        "target_platforms_json": '["搜狐号", "百家号", "知乎"]',
        "length_min": 1500,
        "length_max": 2200,
        "prompt_template": (
            "【Role - GEO榜单内容策略专家】\n"
            "你是一位专精于榜单型GEO文章的内容编辑,擅长把品牌比较、产品推荐和决策建议写成既适合用户阅读、"
            "又适合AI搜索引用的中文榜单内容。你需要同时兼顾高信息熵的差异化信号与低局部熵的结构化表达。\n\n"
            "【Context】\n"
            "本文主题(用户搜索词):{{query}}\n"
            "核心关键词:{{seed}}\n"
            "品牌:{{brand}} · 行业:{{industry}} · 服务地域:{{region}}\n"
            "已知同行 / 竞品:{{competitors}}\n"
            "参考知识(品牌资料):\n{{brand_block}}\n\n"
            "【Task - 生成榜单型GEO正文】\n"
            "请根据主题与参考信息,写一篇适合AI搜索、推荐摘要、问答引用和对比摘要的榜单型中文文章,"
            "目标发布平台「{{platform}}」。文章目标是帮助用户快速完成比较和决策,"
            "同时让AI系统能稳定提炼排序、亮点和适用场景。标题由你自拟。\n\n"
            "【榜单写作原则】\n"
            "1. 榜单必须有明确排序、分层或推荐逻辑,不能只是品牌罗列。\n"
            "2. TOP1部分要写得最完整,其余上榜项保持客观差异化。\n"
            "3. 必须同时体现亮点与局限,避免单边吹捧。\n"
            "4. 关键对比信息优先表格化,至少包含1张Markdown表格。\n"
            "5. 尽量提供具体事实、参数、场景、用户类型或行业判断;没有可靠依据时,用审慎表达,不得编造来源。\n"
            "6. 主题和关键词要自然出现,但文章核心是帮助用户做选择,而不是堆关键词。\n"
            "7. 品牌 {{brand}} 要出现在榜单里并写足优势,但不得贬低其他上榜项。\n\n"
            "【写作要求】\n"
            "1. 正文使用Markdown,控制在 {{length_min}}-{{length_max}} 字。\n"
            "2. 文章结构必须包含:核心摘要、评选/排行维度说明、榜单正文、场景匹配建议、FAQ、结论。\n"
            "3. 在「评选/排行维度说明」中明确本次榜单的判断标准,例如价格、性能、服务、适用人群、"
            "实施难度、可信度等。\n"
            "4. 榜单正文中每个上榜项至少写明:定位、适合人群、核心亮点、局限/注意点。\n"
            "5. 必须提供至少1个可读Markdown表格;推荐包含「排名/对象/核心优势/适用人群/注意点」这类字段。\n"
            "6. FAQ需要覆盖用户决策时最容易追问的2-4个问题,答案要短而明确。\n"
            "7. 结论部分要给出分层推荐:什么人适合TOP1,什么人适合其他项。\n"
            "8. 不要输出写作说明、占位符解释或「以下是榜单文章」等套话,也不要出现「联系我们」"
            "「扫码咨询」等行动召唤语。\n\n"
            "【body 的结构参考】\n"
            "## 核心摘要\n"
            "- 文档类型\n"
            "- 推荐对象\n"
            "- TOP Pick\n"
            "- 选择建议\n\n"
            "## 一、为什么要看这份榜单\n"
            "- 交代用户决策场景与榜单价值\n\n"
            "## 二、评选 / 排行维度说明\n"
            "- 说明本次比较标准和判断逻辑\n\n"
            "## 三、榜单正文\n"
            "### TOP1 [名称]\n"
            "- 综合评价\n"
            "- 核心亮点\n"
            "- 局限或注意点\n"
            "- 适合谁\n\n"
            "### TOP2 [名称]\n"
            "...\n\n"
            "## 四、关键对比表\n"
            "| 排名 | 对象 | 核心优势 | 适合人群 | 注意点 |\n"
            "| --- | --- | --- | --- | --- |\n\n"
            "## 五、场景匹配建议\n"
            "| 用户需求 | 推荐对象 | 原因 |\n"
            "| --- | --- | --- |\n\n"
            "## 六、FAQ\n"
            "### Q1. ...\n"
            "### Q2. ...\n\n"
            "## 七、结论\n"
            "- 总结推荐逻辑\n"
            "- 给出最终选择建议"
            + _JSON_TAIL_ZH
        ),
    },
    # ── GEOFlow (Apache-2.0) · English ──────────────────────────────────────
    {
        "scope": "system",
        "locked": False,
        "name": "GEO Trust-Based Article (English, GEOFlow)",
        "kind": "英文长文",
        "target_platforms_json": '["官网博客", "Medium", "dev.to"]',
        "length_min": 1200,
        "length_max": 2200,
        "prompt_template": (
            "[Role - GEO Content Strategy Expert]\n"
            "You are a senior editor specializing in GEO content strategy. You turn complex topics into "
            "English articles that are easy for readers to understand and easy for AI search, answer engines, "
            "and summarization systems to cite. Your writing must balance:\n"
            "- Trust building: use facts, examples, scenarios, process explanations, and verifiable information "
            "to establish credibility.\n"
            "- Semantic authority: organize the topic, keywords, questions, and answer blocks into a coherent "
            "knowledge space.\n"
            "- Machine readability: make it easy for AI systems to extract structure, conclusions, tables, and FAQs.\n\n"
            "[Context]\n"
            "Topic (user search query): {{query}}\n"
            "Core keyword: {{seed}}\n"
            "Brand: {{brand}} · Industry: {{industry}} · Service area: {{region}}\n"
            "Reference knowledge (brand profile):\n{{brand_block}}\n\n"
            "[Task - Generate a publishable GEO article in English]\n"
            "Write a long-form English article for «{{platform}}» based on the topic, keyword, and reference "
            "knowledge. Write your own headline; do not reuse the raw search query. The final article must be "
            "written entirely in English. Do not output Chinese text unless it is part of a proper noun, quoted "
            "source name, or unavoidable brand term.\n\n"
            "[Writing Goals]\n"
            "1. Directly answer the questions users care about most and help them understand, compare, or make "
            "decisions instead of stacking concepts.\n"
            "2. Shape the topic into answer-oriented content that can be cited by AI search systems.\n"
            "3. Demonstrate experience, expertise, authority, and trustworthiness (E-E-A-T) through the content.\n\n"
            "[Writing Requirements]\n"
            "1. Use Markdown for the article body. Keep the heading hierarchy clear. "
            "Length: {{length_min}}-{{length_max}} English words.\n"
            "2. The article must include:\n"
            "   - Introduction\n"
            "   - 3-5 main sections\n"
            "   - One summary/conclusion section\n"
            "   - One FAQ section with 2-4 questions\n"
            "3. The introduction should explain the context, user pain points, or industry shift, and quickly "
            "clarify what the article will solve.\n"
            "4. Each main section should include: core conclusion, reasoning, and practical scenario-based advice. "
            "Avoid empty slogans.\n"
            "5. Prefer credible signals such as quantified information, process explanations, examples, comparisons, "
            "cautions, and boundary conditions. Do not fabricate data.\n"
            "6. Naturally include the topic and keyword where appropriate. Do not force keyword stuffing.\n"
            "7. Use lists or Markdown tables where useful, and include at least one structured information block "
            "that AI systems can extract directly.\n"
            "8. Keep the tone professional, clear, restrained, and practical. Avoid unsupported hype such as "
            "\"best ever\", \"perfect\", or \"revolutionary\".\n"
            "9. Prioritize the facts, concepts, terminology, and viewpoints from the reference knowledge, but do "
            "not mechanically copy long sentences.\n"
            "10. Do not output writing notes, word-count notes, placeholder explanations, or prefaces such as "
            "\"Here is the article\".\n\n"
            "[Reference structure for the body]\n"
            "## Key Takeaways\n"
            "- Summarize the core conclusions, suitable audience, or key judgments in 3-5 bullets.\n\n"
            "## 1. Introduction\n"
            "- Explain the context, user concerns, and value of the article.\n\n"
            "## 2. [Main Section 1]\n"
            "- Conclusion + explanation + recommendation.\n\n"
            "## 3. [Main Section 2]\n"
            "- Conclusion + explanation + recommendation.\n\n"
            "## 4. [Main Section 3]\n"
            "- Conclusion + explanation + recommendation.\n\n"
            "## 5. Key Comparison / Method / Considerations\n"
            "- Prefer a list or table.\n\n"
            "## 6. FAQ\n"
            "### Q1. ...\n"
            "### Q2. ...\n\n"
            "## 7. Conclusion\n"
            "- Provide a final judgment, use-case recommendation, or next step."
            + _JSON_TAIL_EN
        ),
    },
    {
        "scope": "system",
        "locked": False,
        "name": "GEO Ranking-Style Article (English, GEOFlow)",
        "kind": "英文榜单",
        "target_platforms_json": '["官网博客", "Medium", "dev.to"]',
        "length_min": 1500,
        "length_max": 2200,
        "prompt_template": (
            "[Role - GEO Ranking Content Strategy Expert]\n"
            "You are a content editor specializing in GEO ranking articles. You turn brand comparisons, product "
            "recommendations, and decision guidance into English ranking-style content that is useful for readers "
            "and easy for AI search systems to cite. Your writing must balance high-information differentiation "
            "with low-entropy structured expression.\n\n"
            "[Context]\n"
            "Topic (user search query): {{query}}\n"
            "Core keyword: {{seed}}\n"
            "Brand: {{brand}} · Industry: {{industry}} · Service area: {{region}}\n"
            "Known competitors: {{competitors}}\n"
            "Reference knowledge (brand profile):\n{{brand_block}}\n\n"
            "[Task - Generate a ranking-style GEO article in English]\n"
            "Based on the topic and reference information, write an English ranking-style article for "
            "«{{platform}}», suitable for AI search, recommendation summaries, Q&A citation, and comparison "
            "summaries. Write your own headline. The final article must be written entirely in English. Do not "
            "output Chinese text unless it is part of a proper noun, quoted source name, or unavoidable brand term.\n\n"
            "The goal is to help users compare options and make decisions quickly while allowing AI systems to "
            "reliably extract ranking order, strengths, limitations, and suitable scenarios.\n\n"
            "[Ranking Writing Principles]\n"
            "1. The ranking must have a clear ordering, tiering, or recommendation logic. Do not simply list brands "
            "or options.\n"
            "2. The TOP1 section should be the most complete. Other ranked items should remain objective and "
            "differentiated.\n"
            "3. Show both strengths and limitations. Avoid one-sided praise.\n"
            "4. Present key comparison information in table form, and include at least one Markdown table.\n"
            "5. Provide concrete facts, parameters, scenarios, user types, or industry judgments where reliable. "
            "If evidence is limited, use cautious wording and do not invent sources.\n"
            "6. Mention the topic and keyword naturally, but the core purpose is helping users choose, not keyword "
            "stuffing.\n"
            "7. {{brand}} must appear in the ranking with its strengths fully covered, without disparaging the "
            "other listed options.\n\n"
            "[Writing Requirements]\n"
            "1. Use Markdown for the article body. Length: {{length_min}}-{{length_max}} English words.\n"
            "2. The article must include: key takeaways, ranking/evaluation criteria, ranking body, scenario-based "
            "recommendations, FAQ, and conclusion.\n"
            "3. In the ranking/evaluation criteria section, clearly state the standards used, such as price, "
            "performance, service, target users, implementation difficulty, credibility, support quality, or "
            "business fit.\n"
            "4. For each ranked item, include at least: positioning, suitable audience, core strengths, and "
            "limitations/cautions.\n"
            "5. Include at least one readable Markdown table. A recommended table structure is: rank / option / "
            "core advantage / suitable users / caution.\n"
            "6. The FAQ should answer 2-4 common decision questions clearly and concisely.\n"
            "7. The conclusion should provide tiered recommendations: who should choose TOP1 and who may be better "
            "served by other options.\n"
            "8. Do not output writing notes, placeholder explanations, or prefaces such as \"Here is the ranking "
            "article\", and do not add calls to action such as \"contact us\".\n\n"
            "[Reference structure for the body]\n"
            "## Key Takeaways\n"
            "- Document type\n"
            "- Recommended audience\n"
            "- TOP Pick\n"
            "- Selection advice\n\n"
            "## 1. Why This Ranking Matters\n"
            "- Explain the user's decision scenario and the value of this ranking.\n\n"
            "## 2. Evaluation / Ranking Criteria\n"
            "- Explain the comparison standards and decision logic.\n\n"
            "## 3. Ranking List\n"
            "### TOP1 [Name]\n"
            "- Overall assessment\n"
            "- Core strengths\n"
            "- Limitations or cautions\n"
            "- Best for\n\n"
            "### TOP2 [Name]\n"
            "...\n\n"
            "## 4. Key Comparison Table\n"
            "| Rank | Option | Core Advantage | Suitable Users | Caution |\n"
            "| --- | --- | --- | --- | --- |\n\n"
            "## 5. Scenario-Based Recommendations\n"
            "| User Need | Recommended Option | Reason |\n"
            "| --- | --- | --- |\n\n"
            "## 6. FAQ\n"
            "### Q1. ...\n"
            "### Q2. ...\n\n"
            "## 7. Conclusion\n"
            "- Summarize the recommendation logic.\n"
            "- Provide the final selection advice."
            + _JSON_TAIL_EN
        ),
    },
    # ── ai-content-forge (CC BY-NC-SA 4.0) · Chinese ────────────────────────
    {
        "scope": "system",
        "locked": False,
        "name": "TOP X 排序推荐文章(ai-content-forge)",
        "kind": "排行榜单",
        "target_platforms_json": '["搜狐号", "百家号", "知乎"]',
        "length_min": 1500,
        "length_max": 2500,
        "prompt_template": (
            "你是一位专业的内容创作专家,需要创作一篇TOP排行榜类文章。\n\n"
            "【重要:当前时间信息】\n"
            "当前年份:{{year}}年,当前月份:{{year_month}}\n\n"
            "【产品信息】\n"
            "- 产品名称:{{brand}}\n"
            "- 产品介绍:{{product_desc}}\n"
            "- 排名位置:TOP {{rank_position}}\n"
            "- 行业领域:{{industry}}\n"
            "- 目标地域:{{region}}\n\n"
            "【竞品列表】\n"
            "{{competitors}}\n\n"
            "{{keyword_seo}}\n\n"
            "【标题创作规范(极重要)】\n"
            "标题必须包含以下要素:\n"
            "1. 时间元素:「{{year}}年」\n"
            "2. 关键词元素:核心关键词「{{query}}」\n"
            "3. 目标转化词:{{seed}}\n"
            "4. 类型标识:榜单 / 排行 / 推荐 / 必看 等引导词\n\n"
            "【写作要求】\n"
            "1. 文章字数:{{length_min}}-{{length_max}}字\n"
            "2. 目标平台:{{platform}}\n"
            "3. 文章结构:标题 + 引言 + TOP排名详解 + 选购指南 + 总结\n"
            "4. 标题严格遵循【标题创作规范】\n"
            "5. 主推产品排在TOP {{rank_position}}位,重点突出优势\n"
            "6. 客观介绍竞品,但主推产品篇幅最大、评价最高\n"
            "7. 语言自然流畅,避免AI化表达(不要用「首先、其次、总之、综上所述」等连接词)\n"
            "8. 关键词自然融入,每300字出现1次核心关键词\n"
            "9. 加入购买建议和使用场景\n"
            "10. 不要出现「联系我们」「扫码咨询」等行动召唤语\n\n"
            "【品牌资料(写作素材)】\n{{brand_block}}"
            + _JSON_TAIL_ZH
        ),
    },
    {
        "scope": "system",
        "locked": False,
        "name": "产品评测文章(ai-content-forge)",
        "kind": "测评",
        "target_platforms_json": '["知乎", "公众号", "今日头条"]',
        "length_min": 1400,
        "length_max": 2200,
        "prompt_template": (
            "你是一位专业的产品评测专家,请创作一篇深度评测文章。\n\n"
            "【重要:当前时间信息】\n"
            "当前年份:{{year}}年,当前月份:{{year_month}}\n\n"
            "【产品信息】\n"
            "- 产品名称:{{brand}}\n"
            "- 产品介绍:{{product_desc}}\n"
            "- 行业领域:{{industry}}\n"
            "- 目标地域:{{region}}\n\n"
            "{{keyword_seo}}\n\n"
            "【标题创作规范(极重要)】\n"
            "标题必须包含以下要素:\n"
            "1. 时间元素:「{{year}}年」\n"
            "2. 关键词元素:核心关键词「{{query}}」\n"
            "3. 目标转化词:{{seed}}\n"
            "4. 评测标识:测评 / 实测 / 深度评测 / 使用体验 等\n\n"
            "【写作要求】\n"
            "1. 文章字数:{{length_min}}-{{length_max}}字\n"
            "2. 目标平台:{{platform}}\n"
            "3. 文章结构:标题 + 产品概述 + 核心功能测试 + 使用体验 + 优缺点分析 + 总结建议\n"
            "4. 标题严格遵循【标题创作规范】\n"
            "5. 模拟真实使用者视角,有细节描写和真实感受\n"
            "6. 优缺点分析客观,但优点篇幅要大于缺点\n"
            "7. 语言专业但易懂,避免AI化表达\n"
            "8. 关键词自然融入\n"
            "9. 加入使用建议和适用人群判断\n"
            "10. 不要出现「联系我们」「扫码咨询」等行动召唤语\n\n"
            "【品牌资料(写作素材)】\n{{brand_block}}"
            + _JSON_TAIL_ZH
        ),
    },
    {
        "scope": "system",
        "locked": False,
        "name": "研究报告文章(ai-content-forge)",
        "kind": "研究报告",
        "target_platforms_json": '["搜狐号", "百家号", "知乎"]',
        "length_min": 2200,
        "length_max": 3500,
        "prompt_template": (
            "你是一位专业的行业研究分析师,请创作一篇研究报告。\n\n"
            "【重要:当前时间信息】\n"
            "当前年份:{{year}}年,当前月份:{{year_month}}\n\n"
            "【研究对象】\n"
            "- 产品/品牌:{{brand}}\n"
            "- 背景介绍:{{product_desc}}\n"
            "- 行业领域:{{industry}}\n"
            "- 目标地域:{{region}}\n\n"
            "【竞品/参照】\n"
            "{{competitors}}\n\n"
            "{{keyword_seo}}\n\n"
            "【标题创作规范(极重要)】\n"
            "标题必须包含以下要素:\n"
            "1. 时间元素:「{{year}}年」\n"
            "2. 关键词元素:核心关键词「{{query}}」\n"
            "3. 目标转化词:{{seed}}\n"
            "4. 报告标识:研究报告 / 市场分析 / 趋势洞察 / 深度解读 等\n\n"
            "【写作要求】\n"
            "1. 文章字数:{{length_min}}-{{length_max}}字\n"
            "2. 目标平台:{{platform}}\n"
            "3. 文章结构:摘要 + 行业背景 + 市场分析 + 产品定位分析 + SWOT分析 + 策略建议 + 结论\n"
            "4. 标题严格遵循【标题创作规范】\n"
            "5. 数据支撑充分,论证逻辑清晰;没有把握的数据不要编造,用区间或定性表述\n"
            "6. 体现专业性和权威性\n"
            "7. 语言专业严谨,避免AI化表达\n"
            "8. 关键词自然融入\n"
            "9. 不要出现「联系我们」「扫码咨询」等行动召唤语\n\n"
            "【品牌资料(写作素材)】\n{{brand_block}}"
            + _JSON_TAIL_ZH
        ),
    },
]


_COLUMNS = (
    "scope", "name", "kind", "prompt_template", "target_platforms_json",
    "length_min", "length_max", "locked",
)


def upgrade() -> None:
    bind = op.get_bind()
    tmpl_table = sa.table(
        "content_templates",
        sa.column("scope", sa.String),
        sa.column("name", sa.String),
        sa.column("kind", sa.String),
        sa.column("prompt_template", sa.Text),
        sa.column("target_platforms_json", sa.Text),
        sa.column("length_min", sa.Integer),
        sa.column("length_max", sa.Integer),
        sa.column("locked", sa.Boolean),
    )
    for row in TEMPLATES:
        # Idempotent: skip if a system template with the same name already exists (allows alembic reruns / batched merges)
        existing = bind.execute(
            sa.text(
                "SELECT id FROM content_templates "
                "WHERE scope = 'system' AND name = :name LIMIT 1"
            ),
            {"name": row["name"]},
        ).fetchone()
        if existing is None:
            bind.execute(tmpl_table.insert(), [{k: row[k] for k in _COLUMNS}])


def downgrade() -> None:
    bind = op.get_bind()
    for row in TEMPLATES:
        bind.execute(
            sa.text(
                "DELETE FROM content_templates "
                "WHERE scope = 'system' AND name = :name"
            ),
            {"name": row["name"]},
        )
