"""aitoearn multi-platform distribution channel adapter (counterpart of ruanwenjiang_publisher).

Responsibilities:
  - aitoearn_client.py only handles the protocol (auth/envelope/flow/asset upload);
  - this module translates "one article + one authorized account" into an aitoearn publish flow:
      · Platform-agnostic article support: any authorized account whose contentLimits.modes includes article
        is selectable (bilibili/pinterest require option; P0 does not support dynamic options, excluded for now)
      · Body rendering has two tiers: wxGzh (official account) takes HTML; the rest get plain text + image list
      · Image pipeline: images in the article markdown are moved into assets.aitoearn.cn (local disk read first,
        external download as fallback); asset_map is stored in channel_meta for reuse on retry
      · Free channel (publishes to self-authorized accounts), no charge gate; dedup/timeout handling is the API-layer state machine's
        optimistic lock; publish_task() here only does a single call and error classification
  - The single authoritative mapping of platform type <-> Chinese platform name in the task table is TYPE_TO_PLATFORM
    (Chinese names align with the aliases in frontend/src/constants/platformCatalog.ts;
    official account/Xiaohongshu/Douyin etc. match catalog entries -> attribution badges/platform rules apply automatically).

wxGzh publish body constraints (verified against open-source wechat-official-publish.provider.ts):
  - cover or >= 1 media image is required, otherwise server-side validation rejects it
  - body is HTML; media[].url values are replaced one by one with WeChat asset URLs on the platform side (string replace),
    so embedded images must appear in body as "a string exactly equal to media[].url"
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import time
import warnings as py_warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

from geo.models.ai_telemetry import TopicGeneratedDocORM
from geo.services.aitoearn_client import (
    AitoearnApiError, AitoearnError, get_client,
)
from geo.services.aitoearn_client import configured as client_configured
from geo.services.content_marking import outbound_body

log = logging.getLogger(__name__)

CHANNEL = "aitoearn"      # Discriminator value for channel_meta_json.channel

# aitoearn account platform type -> Chinese platform name in the task table (single authoritative mapping)
TYPE_TO_PLATFORM = {
    "wxGzh": "公众号",
    "xhs": "小红书",
    "douyin": "抖音",
    "wxSph": "视频号",
    "bilibili": "B站",
    "KWAI": "快手",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "twitter": "X",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "threads": "Threads",
    "pinterest": "Pinterest",
    "linkedin": "LinkedIn",
    "google_business": "Google商家",
}
PLATFORM_TO_TYPE = {v: k for k, v in TYPE_TO_PLATFORM.items()}

# Vigilath currently only enables overseas platforms that can directly reuse the existing image-and-text publish pipeline.
ENABLED_ACCOUNT_TYPES = frozenset({
    "twitter", "facebook", "instagram", "threads", "linkedin", "tiktok",
})

# Platforms whose body takes HTML (fallback when the platform metadata editor field is missing; measured wxGzh editor='html',
# xhs/douyin editor='text'; metadata is trusted first, see _is_html_platform)
_HTML_TYPES = {"wxGzh"}
# Platforms where the server requires a cover/image (wxGzh: cover or >= 1 image)
_COVER_REQUIRED_TYPES = {"wxGzh"}
# Platforms requiring option when creating a flow -- P0 has no dynamic option form, so not enabled yet
_OPTION_REQUIRED_TYPES = {"bilibili", "pinterest"}

_DOWNLOAD_TIMEOUT = 30.0
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_TIKTOK_MIN_SHORT_EDGE = 360
_TIKTOK_MAX_SHORT_EDGE = 1080
_JPEG_QUALITIES = (90, 85, 80, 75, 70, 60, 50, 40)
_TIKTOK_DUPLICATE_ASSET_KEY = "__tiktok_duplicate_image__"
_PREFLIGHT_RULESET_VERSION = 1


class AitoearnPublishRejected(AitoearnApiError):
    """Content fails a hard requirement of the target platform (e.g. official account with no image); do not retry, ask the user to add content."""


# -- Platform metadata (1h in-process cache, aligned with rwj _filters_cache) ----------------

_platforms_cache: dict = {}


async def platform_meta(force: bool = False) -> dict[str, dict]:
    """platform type -> metadata (contentLimits/editor/optionSchema...)."""
    if not force and _platforms_cache and _platforms_cache["at"] > time.time() - 3600:
        return _platforms_cache["data"]
    metas = {p["platform"]: p for p in await get_client().list_platforms()
             if p.get("platform")}
    _platforms_cache.update(at=time.time(), data=metas)
    return metas


def _content_limits(meta: dict) -> dict:
    cl = meta.get("contentLimits")
    return cl if isinstance(cl, dict) else {}


def _supports_article(meta: dict) -> bool:
    return "article" in (_content_limits(meta).get("modes") or [])


def _supports(meta: dict, capability: str) -> bool:
    value = (meta.get("capabilities") or {}).get(capability) or {}
    return meta.get("status") == "available" and value.get("supported") is True


def can_auth_and_publish(meta: dict) -> bool:
    return (_supports_article(meta)
            and _supports(meta, "auth")
            and _supports(meta, "publish"))


async def auth_platforms(force: bool = False) -> list[str]:
    """Configured overseas platforms that can authorize and publish articles."""
    metas = await platform_meta(force)
    return sorted(type_ for type_ in ENABLED_ACCOUNT_TYPES
                  if can_auth_and_publish(metas.get(type_) or {}))


def _is_html_platform(meta: dict) -> bool:
    editor = str(meta.get("editor") or "").strip().lower()
    if editor:
        return editor == "html"
    return str(meta.get("platform") or "") in _HTML_TYPES


# -- Channel status / account list ------------------------------------------------

def channel_status_offline() -> dict:
    """Minimal offline status (endpoints use this directly when not configured)."""
    return {"configured": client_configured()}


async def list_article_accounts(force: bool = False) -> list[dict]:
    """Accounts that are authorized x support article x option not required, with platform limits."""
    metas = await platform_meta(force)
    out = []
    for acc in await get_client().list_accounts():
        type_ = str(acc.get("type") or "")
        if type_ not in ENABLED_ACCOUNT_TYPES:
            continue
        meta = metas.get(type_)
        if meta is None or not can_auth_and_publish(meta):
            continue
        if type_ in _OPTION_REQUIRED_TYPES:
            continue
        cl = _content_limits(meta)
        out.append({
            "account_id": str(acc.get("id") or ""),
            "account_type": type_,
            "platform": TYPE_TO_PLATFORM.get(type_, type_),
            "nickname": str(acc.get("nickname") or acc.get("account") or ""),
            "avatar": acc.get("avatar"),
            "status": acc.get("status"),
            "fans_count": acc.get("fansCount"),
            "max_title": cl.get("maxTitleLength"),
            "max_body": cl.get("maxBodyLength"),
            "max_images": cl.get("maxImages"),
            "need_cover": type_ in _COVER_REQUIRED_TYPES,
            "render": "html" if _is_html_platform(meta) else "plain",
            # Platform publish option definitions. Mainly for integration diagnostics and future dynamic frontend forms;
            # Aitoearn may change a platform's required fields, so a static platform list alone is not enough.
            "option_schema": meta.get("optionSchema"),
        })
    return out


# -- Body rendering -------------------------------------------------------------

# ![alt](url "title") -- capture url (up to the first whitespace or closing paren)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(\S+?)(?:\s+[\"'][^)]*)?\)")
_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_MD_INLINE = re.compile(r"(\*\*|__|`)|(?<![\w*])[*_](?![\s*_])|(?<![\s*_])[*_](?![\w*])")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")


def img_block(url: str) -> str:
    """The **canonical shape** of a standalone image in an HTML body.

    🔴 This is not just layout, it is a **string exact-match contract**: `build_content` relies on
    `body.replace(img_block(url), "")` to strip dead images that failed upload. If the shape changes (extra alt,
    different attribute order, different self-closing style) that replace silently misses -- leaving an <img> pointing to a dead
    URL in the body, while the publish is already done and irreversible.

    So generation and stripping **must** both go through this one function. The URL is deliberately not HTML-escaped: replacement relies on exact matching,
    and an escaped string would not match the raw URL in image_urls.
    """
    return f'<p><img src="{url}"/></p>'


@dataclass
class RenderedContent:
    title: str
    body: str
    image_urls: list[str] = field(default_factory=list)   # Raw image URLs before rendering
    cover_url: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    reject_reason: Optional[str] = None
    original_image_count: int = 0


@dataclass
class ImageIssue:
    code: str
    severity: str
    message: str
    media_index: Optional[int] = None
    action: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "media_index": self.media_index,
            "action": self.action,
        }


@dataclass
class InspectedImage:
    url: str
    filename: str
    data: Optional[bytes]
    format: Optional[str] = None
    size: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    animated: bool = False
    sha256: Optional[str] = None
    issues: list[ImageIssue] = field(default_factory=list)

    def as_dict(self, index: int) -> dict:
        return {
            "index": index,
            "url": self.url,
            "filename": self.filename,
            "format": self.format,
            "bytes": self.size,
            "width": self.width,
            "height": self.height,
            "animated": self.animated,
            "aspect_ratio": (round(self.width / self.height, 4)
                             if self.width and self.height else None),
            "sha256": self.sha256,
            "planned_actions": [i.action for i in self.issues if i.action],
        }


@dataclass
class PublishPreflight:
    rendered: RenderedContent
    images: list[InspectedImage] = field(default_factory=list)
    issues: list[ImageIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(i.severity == "fatal" for i in self.issues):
            return "blocked"
        return "repairable" if self.issues else "ready"

    @property
    def reject_reason(self) -> Optional[str]:
        messages = [i.message for i in self.issues if i.severity == "fatal"]
        return "；".join(dict.fromkeys(messages)) or None

    @property
    def warning_messages(self) -> list[str]:
        return list(dict.fromkeys(
            i.message for i in self.issues if i.severity == "repairable"))


def platform_rules_version(meta: dict) -> str:
    payload = {
        "version": _PREFLIGHT_RULESET_VERSION,
        "platform": meta.get("platform"),
        "contentLimits": _content_limits(meta),
        "mediaRules": _media_rules(meta),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _render_hash(rendered: RenderedContent) -> str:
    raw = json.dumps({
        "title": rendered.title,
        "body": rendered.body,
        "image_urls": rendered.image_urls,
        "cover_url": rendered.cover_url,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _extract_images(md: str) -> tuple[str, list[str]]:
    """Extract all images; returns (markdown without images, deduplicated order-preserving URL list)."""
    urls: list[str] = []

    def _grab(m: re.Match) -> str:
        u = m.group(1).strip()
        if u and u not in urls:
            urls.append(u)
        return ""

    return _MD_IMAGE.sub(_grab, md or ""), urls


def _strip_inline(text: str) -> str:
    text = _MD_LINK.sub(lambda m: f"{m.group(1)}({m.group(2)})", text)
    return _MD_INLINE.sub("", text).strip()


def _norm_heading(text: str) -> str:
    """Normalized form for title comparison: strip inline markup, remove all whitespace, full-width spaces count as whitespace."""
    t = _strip_inline(text or "")
    return re.sub(r"[\s　]+", "", t)


def strip_duplicate_title_h1(md: str, title: str,
                             warnings: Optional[list[str]] = None) -> str:
    """Drop the first body line when it is an H1 that "says the same thing as the title" -- otherwise the title appears twice on the platform.

    🔴 Hit in production on 2026-08-14: after doc 81 was published to Zhihu, the article page's H1 and first body line were the same sentence.
    The cause: article markdown naturally carries `# Title` (needed when copying to an editor/repo), while every platform's
    publish body **also** has a separate title field -- sending both duplicates it.

    Only touch the **first line**, and only when "equal after normalization": truncated titles (_truncate), author case or
    punctuation changes still match via normalization; when it really is a different sentence, **do not delete**, attach a warning instead --
    losing a sentence from the body is irreversible, an extra title is just ugly.

    setext form (`Title` + next line `===`) is handled too: another spelling of the same defect.
    """
    lines = (md or "").splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return md
    want = _norm_heading(title)
    if not want:
        return md

    drop_upto = None
    head_text = None
    hm = _MD_HEADING.match(lines[i])
    if hm:
        head_text, drop_upto = hm.group(2), i
    elif i + 1 < len(lines) and re.match(r"^\s{0,3}=+\s*$", lines[i + 1]):
        head_text, drop_upto = lines[i], i + 1
    if head_text is None:
        return md

    if _norm_heading(head_text) != want:
        if warnings is not None:
            warnings.append(
                f"正文首行是另一个标题「{_strip_inline(head_text)[:30]}」,与稿件标题不同 —— "
                f"平台上会出现两个标题,已原样保留")
        return md

    rest = lines[drop_upto + 1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    return "\n".join(rest)


def _md_to_plain(md: str) -> str:
    """markdown -> plain text (editors like Xiaohongshu/Douyin image posts accept no markup at all)."""
    lines: list[str] = []
    for block in re.split(r"\n\s*\n", md or ""):
        block_lines = []
        for ln in block.splitlines():
            if not ln.strip():
                continue
            hm = _MD_HEADING.match(ln)
            if hm:
                block_lines.append(_strip_inline(hm.group(2)))
                continue
            stripped = ln.strip()
            if re.match(r"^[-*+]\s+", stripped):
                stripped = "· " + re.sub(r"^[-*+]\s+", "", stripped)
            elif re.match(r"^>\s*", stripped):
                stripped = re.sub(r"^>\s*", "", stripped)
            block_lines.append(_strip_inline(stripped))
        if block_lines:
            lines.append("\n".join(block_lines))
    return "\n\n".join(lines).strip()


# HTML allowlist. **Only list tags that body editors like official accounts/Zhihu actually render** --
# anything outside the allowlist is escaped by bleach into visible text rather than dropped: eaten content is irreversible,
# an extra `&lt;script&gt;` line is just ugly.
_HTML_ALLOWED_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "del", "code", "pre",
    "blockquote", "ul", "ol", "li", "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
}
_HTML_ALLOWED_ATTRS = {"a": ["href", "title"], "img": ["src", "alt", "title"]}
# Standalone <img> paragraphs: normalize any attribute order/alt from the library into the exact img_block shape.
_HTML_IMG_PARA_RE = re.compile(
    r"<p>\s*<img\b[^>]*?\bsrc=\"([^\"]+)\"[^>]*>\s*</p>", re.IGNORECASE)
# Clamp h1/h5/h6 to h2~h4 (h1 clashes with the title in official account bodies; h5/h6 are too small to matter).
# 🔴 Clamping must happen **after** bleach, so h1/h5/h6 must be in the allowlist -- if reversed,
# bleach escapes them as non-allowlisted tags first and the page shows visible `&lt;h1&gt;Big title&lt;/h1&gt;`.
_HTML_H1_RE = re.compile(r"<(/?)h1>", re.IGNORECASE)
_HTML_H56_RE = re.compile(r"<(/?)h[56]>", re.IGNORECASE)


def _md_to_html(md: str) -> str:
    """markdown -> HTML (rich text editors such as official accounts).

    🔴 Replaced the hand-written regex version on 2026-08-14. What the old version lost, measured:
        links `[text](url)` -> literal `text(url)`, **not a single <a>** (for GEO content
        this is the last thing to lose); `**bold**`/`*italic*`/`` `code` `` markup was stripped to plain text;
        `- x` became `<p>· x</p>` (no <ul><li>); `>` was escaped into a visible `&gt;`;
        GFM tables were dumped row by row as `<p>`.
    Why not reuse the frontend marked+turndown: format is a function of "article x platform", and the platform is only known at claim
    time (one article to many platforms: Zhihu markdown / official account html / Xiaohongshu plain); the frontend editor does not know
    the targets yet; and the worker is headless while the frontend stack depends on the DOM (DOMPurify).

    Only four extensions are deliberately enabled, not `extra`: the latter brings `attr_list` (allows injecting arbitrary HTML attributes into the body)
    and `md_in_html`; opening those holes for AI-generated body text has no benefit.
    `nl2br` matches Chinese writing habits -- with one sentence per line, single newlines must stay as <br>, otherwise they merge into one paragraph.

    Output always passes the bleach allowlist, then standalone image paragraphs are normalized into the exact `img_block` shape (that is
    the string contract `build_content` uses to strip dead images).
    """
    import bleach
    import markdown as _md

    raw = _md.markdown(md or "", extensions=["tables", "fenced_code",
                                             "sane_lists", "nl2br"])
    # strip=False: tags outside the allowlist are **escaped**, not removed -- see the note above _HTML_ALLOWED_TAGS.
    html = bleach.clean(raw, tags=_HTML_ALLOWED_TAGS, attributes=_HTML_ALLOWED_ATTRS,
                        protocols=["http", "https", "data", "mailto"], strip=False)
    html = _HTML_H1_RE.sub(r"<\1h2>", html)
    html = _HTML_H56_RE.sub(r"<\1h4>", html)
    # bleach re-serializes img (alphabetical attributes + alt), which no longer matches build_content's
    # replace. Normalize into the single shape, dropping alt text along the way -- official account bodies do not display it.
    html = _HTML_IMG_PARA_RE.sub(lambda m: img_block(m.group(1)), html)
    return html.strip()


def _truncate(text: str, limit: Optional[int], warnings: list[str], what: str) -> str:
    if not limit or len(text) <= int(limit):
        return text
    limit = int(limit)
    cut = text[:limit]
    # Sentence-boundary fallback: find the last sentence punctuation; if too early (<60%), hard-cut
    m = max(cut.rfind(p) for p in ("。", "!", "?", ".", "\n"))
    if m >= limit * 0.6:
        cut = cut[: m + 1]
    warnings.append(f"{what}超过平台上限 {limit} 字,已截断到 {len(cut)} 字")
    return cut.rstrip()


def render_for_platform(doc: TopicGeneratedDocORM, meta: dict,
                        extra_image_urls: Optional[list[str]] = None) -> RenderedContent:
    """One article x one platform -> publish body (offline, can dry-run for preview)."""
    type_ = str(meta.get("platform") or "")
    cl = _content_limits(meta)
    warnings: list[str] = []

    title = (doc.title or doc.source_query_text or f"稿件 #{doc.id}").strip()
    title = _truncate(title, cl.get("maxTitleLength"), warnings, "标题")

    # Always strip content labels from outbound body first -- legacy article bodies still carry the "内容由AI生成…生成方 …" ("AI-generated content... generated by ...")
    # line and zero-width watermarks at the tail, which would be published verbatim by the platform
    md = outbound_body(doc.body_markdown)
    if not md.strip():
        return RenderedContent(title=title, body="", warnings=warnings,
                               reject_reason="稿件正文为空,无法发布")
    # The publish body has its own title field, so a same-named H1 in the body is a duplicate -- see strip_duplicate_title_h1.
    # Must be done **before** image extraction and conversion, otherwise the duplicate line is already in body.
    md = strip_duplicate_title_h1(md, title, warnings)
    md_no_imgs, images = _extract_images(md)
    inline_images = set(images)
    for url in extra_image_urls or []:
        if url and url not in images:
            images.append(url)
    original_image_count = len(images)

    max_images = cl.get("maxImages")
    if max_images is not None and len(images) > int(max_images) > 0:
        warnings.append(f"图片 {len(images)} 张超过平台上限 {max_images} 张,多余的不随稿发布")
        images = images[: int(max_images)]

    if _is_html_platform(meta):
        body = _md_to_html(md)     # Images stay embedded (same string as media[].url)
        # Assets temporarily chosen in the publish center do not change the original article, but HTML platforms still need images embedded in the body.
        body += "".join(
            img_block(url)
            for url in extra_image_urls or []
            if url in images and url not in inline_images
        )
    else:
        body = _md_to_plain(md_no_imgs)
    body = _truncate(body, cl.get("maxBodyLength"), warnings, "正文")

    cover = images[0] if images else None
    reject = None
    if type_ == "tiktok" and not images:
        reject = "TikTok 图文发布至少需要 2 张图片，请先在稿件里配图"
    elif type_ == "tiktok" and len(images) == 1:
        warnings.append("TikTok 图文至少需要 2 张图片，发布时将复制并再次上传首图")
    elif type_ in _COVER_REQUIRED_TYPES and not images:
        reject = "该平台要求封面图,稿件没有任何图片 —— 请先在稿件里配图再发布"
    return RenderedContent(title=title, body=body, image_urls=images,
                           cover_url=cover, warnings=warnings, reject_reason=reject,
                           original_image_count=original_image_count)


# -- Image pipeline (local disk read first -> external download fallback -> upload) ---------------------

def _resolve_local(url: str) -> Optional[Path]:
    """/api/ai-telemetry/public/topic-media/{tid}/{32hex}.{ext} -> local file path.

    Article images are self-hosted (public URLs generated by content_generator); read from disk directly,
    so the service process does not request itself over the public internet.
    """
    from geo.services import media_storage
    return media_storage.local_file_from_url(urlparse(url).path)


async def _fetch_image(url: str) -> tuple[bytes, str]:
    """Get image bytes and filename (local read or external download)."""
    local = _resolve_local(url)
    if local is not None:
        data = local.read_bytes()
        if len(data) > _MAX_IMAGE_BYTES:
            raise AitoearnError(
                f"图片超过 {_MAX_IMAGE_BYTES // 1024 // 1024}MB:{url}")
        return data, local.name
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT,
                                 follow_redirects=True) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    if len(resp.content) > _MAX_IMAGE_BYTES:
        raise AitoearnError(f"图片超过 {_MAX_IMAGE_BYTES // 1024 // 1024}MB:{url}")
    name = Path(urlparse(url).path).name or "image.jpg"
    if "." not in name:
        name += ".jpg"
    return resp.content, name


def _media_rules(meta: dict) -> dict:
    rules = meta.get("mediaRules")
    return rules if isinstance(rules, dict) else {}


def _number(mapping: dict, key: str) -> Optional[float]:
    try:
        value = float(mapping[key])
        return value if value > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def _format_name(value: str) -> str:
    value = value.strip().lower()
    return {"jpeg": "jpg", "tif": "tiff"}.get(value, value)


def _filename_for_format(filename: str, format_: str) -> str:
    stem = Path(filename).stem.strip() or "image"
    ext = "jpg" if _format_name(format_) == "jpg" else _format_name(format_)
    return f"{stem}.{ext}"


def _geometry_plan(width: int, height: int, meta: dict) -> tuple[int, int, int, int]:
    """Returns (padded width/height, final width/height); rejects on constraint conflicts or oversized results."""
    rules = _media_rules(meta)
    padded_width, padded_height = width, height
    ratio_rule = rules.get("aspectRatio")
    ratio_rule = ratio_rule if isinstance(ratio_rule, dict) else {}
    min_ratio = _number(ratio_rule, "min")
    max_ratio = _number(ratio_rule, "max")
    ratio = width / height
    if min_ratio and ratio < min_ratio:
        padded_width = math.ceil(height * min_ratio)
    elif max_ratio and ratio > max_ratio:
        padded_height = math.ceil(width / max_ratio)

    min_width = _number(rules, "minImageWidth")
    min_height = _number(rules, "minImageHeight")
    max_width = _number(rules, "maxImageWidth")
    max_height = _number(rules, "maxImageHeight")
    min_short = _number(rules, "minImageShortSide")
    max_short = _number(rules, "maxImageShortSide")
    if str(meta.get("platform") or "") == "tiktok":
        min_short = min_short or _TIKTOK_MIN_SHORT_EDGE
        max_short = max_short or _TIKTOK_MAX_SHORT_EDGE

    lower = max(
        min_width / padded_width if min_width else 0,
        min_height / padded_height if min_height else 0,
        min_short / min(padded_width, padded_height) if min_short else 0,
    )
    upper = min(
        max_width / padded_width if max_width else math.inf,
        max_height / padded_height if max_height else math.inf,
        max_short / min(padded_width, padded_height) if max_short else math.inf,
    )
    if lower > upper:
        raise ValueError("平台图片尺寸规则互相冲突")
    scale = lower if lower > 1 else upper if upper < 1 else 1
    final_width = max(1, round(padded_width * scale))
    final_height = max(1, round(padded_height * scale))
    max_pixels = Image.MAX_IMAGE_PIXELS
    if max_pixels and max(padded_width * padded_height,
                          final_width * final_height) > max_pixels:
        raise ValueError("修复后的图片像素数量超过安全上限")
    return padded_width, padded_height, final_width, final_height


def _inspect_image(url: str, data: bytes, filename: str) -> InspectedImage:
    """Fully decode the image using its real encoding; neither extension nor MIME is used to determine format."""
    if len(data) > _MAX_IMAGE_BYTES:
        raise ValueError(
            f"图片超过 {_MAX_IMAGE_BYTES // 1024 // 1024}MB 读取安全上限")
    with py_warnings.catch_warnings():
        py_warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            format_ = _format_name(str(source.format or ""))
            animated = bool(getattr(source, "is_animated", False))
            source.verify()
        with Image.open(io.BytesIO(data)) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            image.load()
            width, height = image.size
    if not format_ or width <= 0 or height <= 0:
        raise ValueError("图片编码或像素尺寸无效")
    return InspectedImage(
        url=url, filename=filename, data=data, format=format_, size=len(data),
        width=width, height=height, animated=animated,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _evaluate_image(image: InspectedImage, meta: dict, index: int) -> None:
    platform_type = str(meta.get("platform") or "")
    platform = TYPE_TO_PLATFORM.get(platform_type, platform_type or "目标平台")
    rules = _media_rules(meta)
    allowed = {
        _format_name(str(value)) for value in (rules.get("imageFormats") or [])
        if value
    }
    force_jpg = platform_type == "tiktok" and image.format != "jpg"
    if force_jpg or (allowed and image.format not in allowed):
        if allowed and "jpg" not in allowed:
            image.issues.append(ImageIssue(
                "unsupported_format", "fatal",
                f"第 {index} 张图片真实格式为 {image.format.upper()}，{platform} 不支持且不接受 JPG",
                index,
            ))
        else:
            image.issues.append(ImageIssue(
                "unsupported_format", "repairable",
                f"第 {index} 张图片真实格式为 {image.format.upper()}，{platform} 发布时转为 JPG",
                index, "convert_jpg",
            ))

    max_bytes = _number(rules, "maxImageSize")
    if max_bytes and image.size > max_bytes:
        image.issues.append(ImageIssue(
            "image_too_large", "repairable",
            f"第 {index} 张图片 {image.size / 1024 / 1024:.1f}MB 超过 {platform} 的 "
            f"{max_bytes / 1024 / 1024:.0f}MB 上限，发布时压缩为 JPG",
            index, "compress_jpg",
        ))

    try:
        padded_w, padded_h, final_w, final_h = _geometry_plan(
            int(image.width or 0), int(image.height or 0), meta)
    except ValueError as e:
        image.issues.append(ImageIssue(
            "invalid_dimensions", "fatal", f"第 {index} 张图片无法适配 {platform}：{e}", index))
        return
    if (padded_w, padded_h) != (image.width, image.height):
        image.issues.append(ImageIssue(
            "invalid_aspect_ratio", "repairable",
            f"第 {index} 张图片宽高比不符合 {platform} 要求，发布时加白边并转为 JPG",
            index, "pad_jpg",
        ))
    if (final_w, final_h) != (padded_w, padded_h):
        image.issues.append(ImageIssue(
            "invalid_dimensions", "repairable",
            f"第 {index} 张图片尺寸将在发布时调整为 {final_w}×{final_h} 并转为 JPG",
            index, "resize_jpg",
        ))
    if image.animated and any(i.action and i.action.endswith("jpg") for i in image.issues):
        image.issues.append(ImageIssue(
            "animated_first_frame", "repairable",
            f"第 {index} 张动图发布时取首帧并转为 JPG",
            index, "first_frame_jpg",
        ))


def _minimum_images(meta: dict, image_count: int) -> int:
    platform = str(meta.get("platform") or "")
    minimum = int(_number(_content_limits(meta), "minImages") or 0)
    if platform == "tiktok":
        return max(2, minimum)
    if image_count == 0 and platform not in _COVER_REQUIRED_TYPES:
        return 0
    return minimum


async def preflight_media(
    rendered: RenderedContent,
    meta: dict,
    *,
    fetched: Optional[list[tuple[bytes, str]]] = None,
) -> PublishPreflight:
    """Read-only precheck: download and decode the image and build a repair plan, without converting or uploading."""
    result = PublishPreflight(rendered=rendered)
    known_messages: set[str] = set()
    if rendered.reject_reason:
        result.issues.append(ImageIssue(
            "content_rejected", "fatal", rendered.reject_reason))
    if rendered.original_image_count > len(rendered.image_urls):
        message = (f"图片 {rendered.original_image_count} 张超过平台上限 "
                   f"{len(rendered.image_urls)} 张，发布时仅保留前 {len(rendered.image_urls)} 张")
        result.issues.append(ImageIssue(
            "too_many_images", "repairable", message, action="drop_extra_images"))
        known_messages.add(message)

    minimum = _minimum_images(meta, len(rendered.image_urls))
    if rendered.image_urls and len(rendered.image_urls) < minimum:
        message = (f"{TYPE_TO_PLATFORM.get(str(meta.get('platform') or ''), '该平台')}至少需要 "
                   f"{minimum} 张图片，发布时将复制首图补足")
        result.issues.append(ImageIssue(
            "too_few_images", "repairable", message, action="duplicate_first_image"))
        known_messages.add(message)

    for message in rendered.warnings:
        if message not in known_messages and not (
                message.startswith("图片 ") or message.startswith("TikTok 图文至少")):
            result.issues.append(ImageIssue(
                "content_adjustment", "repairable", message, action="adjust_content"))

    for index, url in enumerate(rendered.image_urls, 1):
        try:
            data, filename = (fetched[index - 1] if fetched is not None
                              else await _fetch_image(url))
            image = _inspect_image(url, data, filename)
            _evaluate_image(image, meta, index)
        except Exception as e:  # noqa: BLE001 -- the precheck must turn per-image errors into displayable results
            image = InspectedImage(url=url, filename=Path(urlparse(url).path).name,
                                   data=None)
            image.issues.append(ImageIssue(
                "invalid_image", "fatal",
                f"第 {index} 张图片损坏或无法读取：{str(e)[:160]}", index))
        result.images.append(image)
        result.issues.extend(image.issues)
    return result


async def preflight_content(doc: TopicGeneratedDocORM, meta: dict,
                            extra_image_urls: Optional[list[str]] = None) -> PublishPreflight:
    rendered = render_for_platform(doc, meta, extra_image_urls=extra_image_urls)
    return await preflight_media(rendered, meta)


def preflight_snapshot(check: PublishPreflight, meta: dict) -> dict:
    """Minimal reusable result inside the signed token; contains no raw image bytes."""
    return {
        "rules_version": platform_rules_version(meta),
        "render_hash": _render_hash(check.rendered),
        "status": check.status,
        "issues": [issue.as_dict() for issue in check.issues],
        "images": [{
            **image.as_dict(index),
            "issues": [issue.as_dict() for issue in image.issues],
        } for index, image in enumerate(check.images, 1)],
    }


def _issue_from_dict(value: dict) -> ImageIssue:
    return ImageIssue(
        code=str(value.get("code") or "preflight_issue"),
        severity=("fatal" if value.get("severity") == "fatal" else "repairable"),
        message=str(value.get("message") or "图片预检问题"),
        media_index=value.get("media_index"),
        action=value.get("action"),
    )


async def revalidate_preflight(
    rendered: RenderedContent,
    meta: dict,
    snapshot: Optional[dict],
) -> tuple[PublishPreflight, bool]:
    """Reuse the signed result when rules and SHA-256 are unchanged; on change, fully re-check with Pillow."""
    if (not isinstance(snapshot, dict)
            or snapshot.get("rules_version") != platform_rules_version(meta)
            or snapshot.get("render_hash") != _render_hash(rendered)):
        return await preflight_media(rendered, meta), False
    saved_images = snapshot.get("images")
    if not isinstance(saved_images, list) or len(saved_images) != len(rendered.image_urls):
        return await preflight_media(rendered, meta), False

    fetched: list[tuple[bytes, str]] = []
    unchanged = True
    for index, url in enumerate(rendered.image_urls):
        try:
            data, filename = await _fetch_image(url)
        except Exception:
            return await preflight_media(rendered, meta), False
        fetched.append((data, filename))
        saved = saved_images[index]
        if (not isinstance(saved, dict) or saved.get("url") != url
                or saved.get("sha256") != hashlib.sha256(data).hexdigest()):
            unchanged = False
    if not unchanged:
        return await preflight_media(rendered, meta, fetched=fetched), False

    try:
        issues = [_issue_from_dict(value) for value in snapshot.get("issues") or []]
        images = []
        for index, saved in enumerate(saved_images):
            data, filename = fetched[index]
            images.append(InspectedImage(
                url=rendered.image_urls[index], filename=filename, data=data,
                format=str(saved["format"]), size=len(data),
                width=int(saved["width"]), height=int(saved["height"]),
                animated=bool(saved.get("animated", False)),
                sha256=str(saved["sha256"]),
                issues=[_issue_from_dict(value) for value in saved.get("issues") or []],
            ))
        return PublishPreflight(rendered=rendered, images=images, issues=issues), True
    except (KeyError, TypeError, ValueError):
        return await preflight_media(rendered, meta, fetched=fetched), False


def _prepare_image(image: InspectedImage, meta: dict) -> tuple[bytes, str]:
    """Execute the repair plan from the precheck and ensure the output passes the same rules again."""
    if image.data is None or image.format is None or image.width is None or image.height is None:
        raise AitoearnPublishRejected("图片没有可用数据")
    actions = {issue.action for issue in image.issues if issue.action}
    transform = bool(actions & {
        "convert_jpg", "compress_jpg", "pad_jpg", "resize_jpg", "first_frame_jpg",
    })
    if not transform:
        return image.data, _filename_for_format(image.filename, image.format)

    with Image.open(io.BytesIO(image.data)) as source:
        source.seek(0)
        opened = ImageOps.exif_transpose(source)
        opened.load()
    if "A" in opened.getbands() or "transparency" in opened.info:
        rgba = opened.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
    else:
        rgb = opened.convert("RGB")

    padded_w, padded_h, final_w, final_h = _geometry_plan(*rgb.size, meta)
    if (padded_w, padded_h) != rgb.size:
        canvas = Image.new("RGB", (padded_w, padded_h), "white")
        canvas.paste(rgb, ((padded_w - rgb.width) // 2,
                           (padded_h - rgb.height) // 2))
        rgb = canvas
    if (final_w, final_h) != rgb.size:
        rgb = rgb.resize((final_w, final_h), Image.Resampling.LANCZOS)

    rules = _media_rules(meta)
    max_bytes = int(_number(rules, "maxImageSize") or _MAX_IMAGE_BYTES)
    min_width = int(_number(rules, "minImageWidth") or 1)
    min_height = int(_number(rules, "minImageHeight") or 1)
    min_short = int(_number(rules, "minImageShortSide") or (
        _TIKTOK_MIN_SHORT_EDGE if meta.get("platform") == "tiktok" else 1))
    while True:
        encoded = b""
        for quality in _JPEG_QUALITIES:
            output = io.BytesIO()
            rgb.save(output, format="JPEG", quality=quality,
                     optimize=True, progressive=True)
            encoded = output.getvalue()
            if len(encoded) <= max_bytes:
                name = _filename_for_format(image.filename, "jpg")
                checked = _inspect_image(image.url, encoded, name)
                _evaluate_image(checked, meta, 1)
                if checked.issues:
                    raise AitoearnPublishRejected(
                        "自动修复后的图片仍不符合平台要求：" +
                        "；".join(i.message for i in checked.issues))
                return encoded, name

        scale = min(0.9, math.sqrt(max_bytes / len(encoded)) * 0.95)
        next_size = (max(1, round(rgb.width * scale)),
                     max(1, round(rgb.height * scale)))
        if (next_size == rgb.size or next_size[0] < min_width
                or next_size[1] < min_height or min(next_size) < min_short):
            raise AitoearnPublishRejected(
                f"图片无法在平台最小尺寸内压缩到 {max_bytes // 1024 // 1024}MB 以下")
        rgb = rgb.resize(next_size, Image.Resampling.LANCZOS)


async def ensure_assets(image_urls: list[str],
                        cached: Optional[dict] = None, *,
                        platform: Optional[dict] = None,
                        preflight: Optional[PublishPreflight] = None) -> tuple[dict, dict]:
    """Check, repair per platform and upload images. Old cache cannot replace this real file check."""
    del cached  # Old assets may correspond to changed source files or stale rules; they must not bypass the formal publish precheck.
    platform = platform or {}
    check = preflight or await preflight_media(
        RenderedContent(title="", body="", image_urls=image_urls,
                        original_image_count=len(image_urls)), platform)
    if check.status == "blocked":
        raise AitoearnPublishRejected(check.reject_reason or "图片预检未通过")

    asset_map: dict[str, str] = {}
    failed: dict[str, str] = {}
    client = get_client()
    for image in check.images:
        data, name = _prepare_image(image, platform)
        try:
            asset_map[image.url] = await client.upload_asset(data, name)
        except Exception as e:  # noqa: BLE001 -- upload failures keep the per-image degradation semantics
            log.warning("[aitoearn] image upload failed url=%s: %s", image.url, e)
            failed[image.url] = str(e)
    return asset_map, failed


# -- Publish --------------------------------------------------------------------

def make_flow_id(task_id: int, attempt: int) -> str:
    return f"vig-{task_id}-{attempt}"


def build_content(rendered: RenderedContent, asset_map: dict) -> dict:
    """Rendered result + image mapping -> flow content. Images that failed upload are stripped everywhere."""
    body = rendered.body
    media = []
    for url in rendered.image_urls:
        asset = asset_map.get(url)
        if asset:
            body = body.replace(url, asset)
            media.append({"url": asset})
        else:
            # Strip dead images from the body (HTML img paragraphs removed whole; plain text has no images anyway).
            # The shape must come from the **same function** as at generation -- see img_block's docstring.
            body = body.replace(img_block(url), "")
    cover_asset = asset_map.get(rendered.cover_url) if rendered.cover_url else None
    content: dict[str, Any] = {"title": rendered.title, "body": body, "media": media}
    if cover_asset:
        content["cover"] = {"url": cover_asset}
    return content


async def publish_task(task, doc: TopicGeneratedDocORM,
                       signed_preflight: Optional[dict] = None) -> dict:
    """Run a real publish for one task (create a single-item flow).

    Precondition: the API layer has set the task to publishing via the state-machine optimistic lock, written this run's
    flow_id / attempt / publish_started_at into channel_meta and committed --
    "submitted" evidence is persisted before going out, so timeout/unknown-result reconcile has a flow_id to verify.
    A single call, no retries. Returns {asset_map, warnings, response} to persist into channel_meta.
    """
    import json as json_mod

    meta = {}
    try:
        meta = json_mod.loads(task.channel_meta_json or "{}")
        meta = meta if isinstance(meta, dict) else {}
    except ValueError:
        pass
    account_id = str(meta.get("account_id") or "")
    account_type = str(meta.get("account_type") or "")
    if not account_id or not account_type:
        raise AitoearnApiError("任务缺少 aitoearn 账号信息(account_id/account_type)")
    flow_id = str(meta.get("flow_id") or "")
    if not flow_id:
        raise AitoearnApiError("任务缺少 flow_id(调用方需先派发并落库)")

    pm = (await platform_meta()).get(account_type)
    if pm is None:
        raise AitoearnApiError(f"aitoearn 未返回平台 {account_type} 的元数据")
    rendered = render_for_platform(
        doc, pm, extra_image_urls=list(meta.get("selected_media_urls") or []))
    preflight, reused_preflight = await revalidate_preflight(
        rendered, pm, signed_preflight)
    if preflight.status == "blocked":
        raise AitoearnPublishRejected(preflight.reject_reason or "内容预检未通过")

    asset_map, failed = await ensure_assets(
        rendered.image_urls, meta.get("asset_map"), platform=pm, preflight=preflight)
    warnings = preflight.warning_messages
    for url, why in failed.items():
        warnings.append(f"图片上传失败已剔除:{url}({why[:120]})")
    duplicate_assets: list[str] = []
    uploaded_urls = [u for u in rendered.image_urls if asset_map.get(u)]
    required_images = _minimum_images(pm, len(rendered.image_urls))
    if required_images and not uploaded_urls:
        raise AitoearnPublishRejected(
            f"{TYPE_TO_PLATFORM.get(account_type, account_type)}至少需要 {required_images} 张有效图片")
    if uploaded_urls and len(uploaded_urls) < required_images:
        source = next(image for image in preflight.images
                      if image.url == uploaded_urls[0])
        data, name = _prepare_image(source, pm)
        for number in range(len(uploaded_urls), required_images):
            duplicate = await get_client().upload_asset(
                data, f"copy-{name}" if number == 1 else f"copy-{number}-{name}")
            duplicate_assets.append(duplicate)
            asset_map[_TIKTOK_DUPLICATE_ASSET_KEY if number == 1
                      else f"__duplicate_image_{number}__"] = duplicate
    if account_type in _COVER_REQUIRED_TYPES and not any(
            asset_map.get(u) for u in rendered.image_urls):
        raise AitoearnPublishRejected("封面图全部上传失败,无法发布到该平台,请稍后重试")

    content = build_content(rendered, asset_map)
    content["media"].extend({"url": asset} for asset in duplicate_assets)
    response = await get_client().create_flow(
        flow_id=flow_id,
        content=content,
        items=[{"platform": account_type, "accountId": account_id}],
        publish_at=datetime.now(timezone.utc).isoformat(),
    )
    return {"asset_map": asset_map, "warnings": warnings, "response": response,
            "preflight_reused": reused_preflight}


# -- Reconciliation status mapping ------------------------------------------------

def map_flow_task(rec: dict) -> tuple[str, Optional[str], str]:
    """One task in flow details -> (action, url, detail).

    action in publish (link obtained, loop can close) / wait (keep polling) / link_failed (sent
    but link fetch failed, manual backfill) / failed (publish failed).
    Numeric status enum: see aitoearn_client.PUBLISH_STATUS.
    """
    status = rec.get("status")
    link_status = str(rec.get("linkStatus") or "")
    work_link = str(rec.get("workLink") or "").strip()
    err = str(rec.get("errorMsg") or rec.get("linkError") or "").strip()

    if status in (-1,):
        return "failed", None, err or "平台返回发布失败"
    if status == 9:
        return "failed", None, "发布已在 aitoearn 侧取消"
    if work_link.startswith("http"):
        return "publish", work_link, "已取得作品链接"
    if status == 1 and link_status == "failed":
        detail = "已发布成功但作品链接获取失败"
        if rec.get("platformWorkId"):
            detail += f"(平台作品 ID:{rec['platformWorkId']})"
        return "link_failed", None, detail + ",可在平台后台找到文章后手工回填 URL"
    return "wait", None, f"发布进行中(状态 {status},链接 {link_status or '未产出'})"
