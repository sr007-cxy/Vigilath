"""Draft -> server_pool publish payload (title / body / images).

The self-hosted account pool's body source is **exactly the same** as other channels: `topic_generated_docs.title` +
`body_markdown` -- i.e. the two fields of the frontend `ContentDoc` (frontend/src/services/contentApi.ts:31-33).
There's only one copy of a draft; every channel takes it from here; there is no "publish pool's own body".

markdown -> html / plain conversion reuses the aitoearn channel's code directly
(services/aitoearn_publisher.py:189-239). Deliberately not rewritten: two markdown converters drift,
the same article renders differently on the aggregate channel vs the own pool, and that only surfaces when ops compares two platforms.

The only difference from the aitoearn version is **where limits come from**: that one reads Aitoearn's platform meta API,
this one reads our own `publish_platforms` config table (plan section 4 P1 item 12: adding a platform is an INSERT).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from geo.models.ai_telemetry import TopicGeneratedDocORM
from geo.models.publish_accounts import PublishPlatformORM
from geo.services.aitoearn_publisher import (
    _extract_images,
    _md_to_html,
    _md_to_plain,
    _truncate,
    strip_duplicate_title_h1,
)


@dataclass
class PublishBody:
    title: str
    body: str
    # "html" | "plain" | "markdown" -- the adapter uses this to decide how to feed the body into the editor.
    body_format: str = "html"
    # Markdown with the duplicate title removed but images kept in place. Core sidecar preprocesses it itself;
    # the old Python publisher still reads only body/images; the two don't affect each other.
    source_markdown: str = ""
    image_urls: list[str] = field(default_factory=list)
    cover_url: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    reject_reason: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "body": self.body,
            "body_format": self.body_format,
            "source_markdown": self.source_markdown,
            "image_urls": list(self.image_urls),
            "cover_url": self.cover_url,
            "warnings": list(self.warnings),
            "reject_reason": self.reject_reason,
        }


def render_for_publish(doc: TopicGeneratedDocORM,
                       platform: Optional[PublishPlatformORM],
                       *,
                       allow_missing_cover: bool = False) -> PublishBody:
    """One draft x one platform -> publish payload. No network; can dry-run for preview.

    A missing platform row is treated as "no limit + html": a missing config row shouldn't block publishing (the adapter
    will hit the real limit in the editor), but a warning is logged to make it visible.
    """
    warnings: list[str] = []
    if platform is None:
        fmt, title_limit, body_limit, image_max, needs_cover = "html", 0, 0, 0, False
        warnings.append("publish_platforms 里没有该平台的配置行,按无限额处理")
    else:
        fmt = (platform.content_format or "html").strip().lower()
        platform_code = (getattr(platform, "code", "") or "").strip().lower()
        # 0 = no limit (follows the engine_sessions.daily_cap convention, see seeds/publish_platforms.py:28)
        title_limit = int(platform.title_limit or 0)
        body_limit = int(platform.body_limit or 0)
        image_max = int(platform.image_max or 0)
        needs_cover = (
            bool(platform.needs_cover)
            and platform_code != "baijia"
            and not allow_missing_cover
        )

    title = (doc.title or doc.source_query_text or f"稿件 #{doc.id}").strip()
    title = _truncate(title, title_limit, warnings, "标题")

    md = doc.body_markdown or ""
    if not md.strip():
        # Empty body must be stopped **before** going to the network: discovering it in the editor costs a login +
        # an empty draft left on the platform, while this check costs nothing.
        return PublishBody(title=title, body="", body_format=fmt, source_markdown="",
                           warnings=warnings,
                           reject_reason="稿件正文为空,无法发布")

    # The payload carries its own title field, so the same-named H1 on the body's first line is a duplicate -- if not stripped, the title
    # appears twice on the platform (the live article for doc 81 on 2026-08-14 did exactly that). Must run before image extraction and conversion.
    md = strip_duplicate_title_h1(md, title, warnings)
    source_markdown = md.strip()
    md_no_imgs, images = _extract_images(md)
    if image_max > 0 and len(images) > image_max:
        warnings.append(f"图片 {len(images)} 张超过平台上限 {image_max} 张,多余的不随稿发布")
        images = images[:image_max]

    if fmt == "markdown":
        # The editor consumes markdown itself (Zhihu with "Markdown 语法输入中" ("Markdown syntax input on") enabled is this case).
        # Send as-is -- converting to html for the editor to reinterpret only double-escapes.
        body = md_no_imgs.strip()
    elif fmt == "plain":
        body = _md_to_plain(md_no_imgs)
    else:
        body = _md_to_html(md)
    body = _truncate(body, body_limit, warnings, "正文")

    reject = None
    if needs_cover and not images:
        reject = "该平台要求封面图,稿件没有任何图片 —— 请先在稿件里配图再发布"
    return PublishBody(title=title, body=body, body_format=fmt,
                       source_markdown=source_markdown, image_urls=images,
                       cover_url=images[0] if images else None,
                       warnings=warnings, reject_reason=reject)
