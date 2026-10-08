from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from geo.api.content import _require_complete_doc
from geo.services.content_error_public import public_generation_error


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("deepseek 输出被截断(finish_reason=length,max_tokens=8000)",
         "文章内容超过本次生成长度限制，请重新生成。"),
        ("deepseek 503: service unavailable",
         "生成服务暂时繁忙，请稍后重新生成。"),
        ("生成未开跑:主题余额不足(单篇 300 分)",
         "主题余额不足，充值后可重新生成。"),
    ],
)
def test_public_generation_error_hides_internal_details(raw, expected):
    assert public_generation_error(raw) == expected


def test_failed_placeholder_cannot_be_reviewed():
    doc = SimpleNamespace(
        generation_error="503 provider error",
        title="[生成失败] 示例",
        body_markdown="",
    )
    with pytest.raises(HTTPException) as exc:
        _require_complete_doc(doc)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "GENERATION_INCOMPLETE"


def test_complete_doc_can_be_reviewed():
    doc = SimpleNamespace(
        generation_error=None,
        title="完整文章",
        body_markdown="正文",
    )
    _require_complete_doc(doc)
