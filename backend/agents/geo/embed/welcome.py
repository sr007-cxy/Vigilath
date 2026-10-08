"""Welcome card copy sent to the admin after an ISV app is installed (shared by Feishu / WeCom ISV, single source of truth).

Hard requirement of Feishu app marketplace review: after the app is installed, a welcome card must be pushed to the admin, containing
the app intro, help docs, service desk entry and contact phone, app entry and admin console entry. Same for WeCom.

The body is markdown: Feishu uses _send_card (markdown card), WeCom uses _send_markdown; both render
**bold** and [text](link) as markdown. Change copy / links / phone numbers only here.
"""
from __future__ import annotations

import os

# Admin-side entries and contact info (for brand messaging see project_locked_brand_messaging; align before changing)
HELP_DOC_URL = os.getenv("HELP_DOC_URL", "<help-doc-url>")
ADMIN_CONSOLE_URL = "https://www.vigilath.cn"
SUPPORT_EMAIL = "support@zen7.com"
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "")


def welcome_markdown() -> str:
    """Welcome card body (markdown) sent to the admin after install."""
    return (
        "🎉 **欢迎开通 Vigilath 智能助手**\n\n"
        "Vigilath 是 AI 搜索可见性全链路解决方案(检测·审计·优化·监控)。"
        "智能助手帮你提升品牌在 ChatGPT、Perplexity、DeepSeek、豆包、通义、文心、元宝等 "
        "AI 引擎里的可见性,并随时盯着品牌舆情。\n\n"
        "**▸ 应用入口**:直接在本应用里跟我说话即可——例如「今天舆情怎么样」"
        "「帮我做一次 GEO 诊断」,不用记任何命令。\n"
        f"**▸ 帮助文档**:[使用指南]({HELP_DOC_URL})\n"
        f"**▸ 管理后台**:[登录控制台]({ADMIN_CONSOLE_URL})\n"
        f"**▸ 服务台 / 客服**:邮箱 {SUPPORT_EMAIL}"
        + (f" · 电话 {SUPPORT_PHONE}" if SUPPORT_PHONE else "")
        + "\n"
    )
