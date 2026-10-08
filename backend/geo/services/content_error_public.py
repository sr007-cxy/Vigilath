"""Customer-safe content-generation failure messages."""
from __future__ import annotations

import re


def public_generation_error(error: str | None) -> str | None:
    if not (error or "").strip():
        return None
    text = (error or "").lower()
    if "余额不足" in text or "insufficient" in text or "balance" in text:
        return "主题余额不足，充值后可重新生成。"
    if "finish_reason=length" in text or "输出被截断" in text:
        return "文章内容超过本次生成长度限制，请重新生成。"
    if re.search(r"\b(429|500|502|503|504|522|524)\b", text) \
            or "too busy" in text or "service unavailable" in text:
        return "生成服务暂时繁忙，请稍后重新生成。"
    if "api_key" in text or "api key" in text or "unauthor" in text:
        return "生成服务暂不可用，请联系平台支持。"
    return "文章生成未完成，请稍后重新生成或联系平台支持。"
