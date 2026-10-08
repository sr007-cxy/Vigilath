from types import SimpleNamespace

from geo.services.publish_render import render_for_publish


def test_baijia_without_images_is_allowed_even_when_cover_is_configured():
    doc = SimpleNamespace(
        id=1,
        title="百家号无图稿",
        source_query_text=None,
        body_markdown="正文没有图片也可以发布。",
    )
    platform = SimpleNamespace(
        code="baijia",
        content_format="html",
        title_limit=30,
        body_limit=0,
        image_max=20,
        needs_cover=True,
    )

    rendered = render_for_publish(doc, platform)

    assert rendered.reject_reason is None
    assert rendered.image_urls == []
    assert rendered.cover_url is None


def test_wechat_draft_without_images_is_allowed_but_default_still_requires_cover():
    doc = SimpleNamespace(
        id=2,
        title="公众号无图草稿",
        source_query_text=None,
        body_markdown="正文没有图片也可以保存草稿。",
    )
    platform = SimpleNamespace(
        code="wxgzh",
        content_format="html",
        title_limit=64,
        body_limit=20000,
        image_max=9,
        needs_cover=True,
    )

    assert render_for_publish(doc, platform).reject_reason is not None
    rendered = render_for_publish(doc, platform, allow_missing_cover=True)

    assert rendered.reject_reason is None
    assert rendered.image_urls == []
    assert rendered.cover_url is None
