from datetime import datetime
from types import SimpleNamespace

from geo.api.ai_telemetry import _serialize_media
from geo.services import content_generator


def _image(filename: str = "a" * 32 + ".png"):
    return SimpleNamespace(
        id=1,
        topic_id=7,
        kind="image",
        filename=filename,
        description="",
        storage_path=f"data/topic_media/7/{filename}",
    )


def test_uploaded_image_is_embedded_with_relative_url(monkeypatch):
    monkeypatch.delenv("GEO_PUBLIC_MEDIA_BASE", raising=False)
    body = content_generator._embed_media_images("正文第一段。", [_image()])

    assert body.endswith(
        "![文章配图 1](/api/ai-telemetry/public/topic-media/7/"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png)"
    )


def test_existing_image_is_not_duplicated():
    media = _image()
    url = content_generator._media_public_url(media)
    body = f"正文。\n\n![已有配图]({url})"

    assert content_generator._embed_media_images(body, [media]) == body


def test_repeated_selected_image_is_reduced_to_one_reference():
    media = _image()
    url = content_generator._media_public_url(media)
    body = f"第一段。\n\n![配图A]({url})\n\n第二段。\n\n![配图B]({url})"

    result = content_generator._embed_media_images(body, [media])

    assert result.count(url) == 1
    assert f"![配图A]({url})" in result


def test_only_first_selected_image_is_embedded():
    first = _image("a" * 32 + ".png")
    second = _image("b" * 32 + ".png")

    result = content_generator._embed_media_images("正文。", [first, second])

    assert content_generator._media_public_url(first) in result
    assert content_generator._media_public_url(second) not in result


def test_image_is_inserted_before_first_heading():
    media = _image()
    url = content_generator._media_public_url(media)
    body = "开头钩子段。\n\n## 第一节\n\n内容一。\n\n## 第二节\n\n内容二。"

    result = content_generator._embed_media_images(body, [media])

    assert result == (f"开头钩子段。\n\n![文章配图 1]({url})\n\n"
                      "## 第一节\n\n内容一。\n\n## 第二节\n\n内容二。")


def test_body_starting_with_heading_appends_image_at_end():
    media = _image()
    url = content_generator._media_public_url(media)
    body = "## 第一节\n\n内容。"

    assert content_generator._embed_media_images(body, [media]) == (
        f"## 第一节\n\n内容。\n\n![文章配图 1]({url})")


def test_system_prompt_no_longer_mentions_media():
    prompt = content_generator._build_system_prompt(
        content_generator.BrandProfile(), copywriting_type="long_form")

    assert "配图" not in prompt


def test_uploaded_media_response_contains_public_path():
    media = _image()
    media.filename = "用户上传.png"
    media.mime = "image/png"
    media.size = 123
    media.use_count = 3
    media.uploaded_at = datetime(2026, 9, 16)

    result = _serialize_media(media)

    assert result.public_url == (
        "/api/ai-telemetry/public/topic-media/7/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png")
    assert result.useCount == 3
