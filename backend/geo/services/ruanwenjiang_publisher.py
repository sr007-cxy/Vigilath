"""Adapter for the Meijieku-family paid sponsored-article channels (Ruanwenjiang + Meijieku, counterpart of mediumsly_publisher).

The two are two sites on the same backend (Ruanwenjiang's API domain is literally `tenant.meijieku.com`);
all protocol differences live in `rwj_client.SiteSpec`; this module only adds a `provider` parameter:
`rwj` = Ruanwenjiang, `mjk` = Meijieku; the task table platform stores 「软文匠」 ("Ruanwenjiang") / 「媒介库」 ("Meijieku") respectively.
Both share the charging gate `RWJ_PUBLISH_ENABLED` -- one switch controls every paid-publishing network egress point.

Responsibility boundaries:
  - rwj_client.py handles protocol only (login/renewal/list/draft/order);
  - this module translates "one draft + one media resource" into Ruanwenjiang requests and adds charging guardrails:
      - credentials not configured -> RwjNotConfigured (endpoint degrades to feature unavailable)
      - when RWJ_PUBLISH_ENABLED=False, publish() always refuses (drafts are unrestricted, free)
      - before ordering, re-fetch the list to verify the resource still exists and the price hasn't changed (trust no cached price)
      - /Article.{Type}/add has no server-side idempotency -- dedupe/timeout handling is the API layer's state machine
        optimistic lock; this module's publish() only makes a single call and classifies errors
  - Initially only Website (web media) is exposed at the business layer: the output is a crawlable web URL, directly satisfying
    the "no URL, no published" invariant and cited_by citation attribution; the other four types stay in the client layer.

Body format: the platform editor consumes Word-paste-style HTML (styled <p><span>, 16pt bold
subheadings) -- style constants are kept in sync with tmp/ruanwenjiang/articles/build_article.py.
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from geo.database import settings
from geo.models.ai_telemetry import TopicGeneratedDocORM
from geo.services.content_marking import outbound_body
from geo.services.rwj_client import (
    DEFAULT_PROVIDER, SITES, RwjApiError, RwjAuthError, RwjClient, delivery_options,
    get_client, pick_delivery, resource_price, site,
)

log = logging.getLogger(__name__)

PLATFORM_NAME = "软文匠"          # publish_task.platform value for the default site (old call sites still use it)
# Task table platform <-> provider bidirectional mapping. Task lookup / receipt polling / publish center badges all key off platform;
# this is the single source of truth -- adding a site only changes rwj_client.SITES, and these two tables follow automatically.
PLATFORM_BY_PROVIDER = {p: s.label for p, s in SITES.items()}
PROVIDER_BY_PLATFORM = {s.label: p for p, s in SITES.items()}
PLATFORM_NAMES = tuple(PLATFORM_BY_PROVIDER.values())


def platform_name(provider: str = DEFAULT_PROVIDER) -> str:
    """provider -> task table platform value (「软文匠」 ("Ruanwenjiang") / 「媒介库」 ("Meijieku"))."""
    return site(provider).label


def provider_of(platform: str) -> Optional[str]:
    """Task table platform value -> provider; returns None for platforms not on this channel."""
    return PROVIDER_BY_PLATFORM.get(platform)

# Resource types open at the business layer: website media + self-media (platform accounts like Toutiao / Baijiahao / Sohu published on behalf).
# Shortvideo (co-shoot placement) / Question / Ghostwrite have very different payloads; to be opened later.
SUPPORTED_TYPES = ("Website", "Wemedia")

# ── Platform editor layout constants (same source as build_article.py) ──────────────────────
_LEAD = "margin-top: 24pt; margin-bottom: 24pt; margin-left: 0in; margin-right: 0in;"
_BODY = "text-align: left; margin-top: 6pt; margin-bottom: 6pt; margin-left: 0in; margin-right: 0in;"
_HEAD = "text-align: left; margin-top: 16pt; margin-bottom: 6pt; margin-left: 0in; margin-right: 0in;"
_S_BODY = "font-family: &#39;Arial&#39;; font-size: 11pt;"
_S_HEAD = "font-family: &#39;Arial&#39;; font-size: 16pt; font-weight: bold;"


class RwjNotConfigured(RwjApiError):
    """Credentials not configured / charging gate not open -- endpoints return "channel not enabled" for these."""


class RwjBalanceError(RwjApiError):
    """Insufficient balance -- the task should stay planned, prompting a retry after top-up."""


@dataclass
class DraftResult:
    draft_id: Optional[int]
    resource_id: int
    resource_title: str
    price: str            # quote snapshot (CNY, string, from the live list)
    level: int
    delivery: dict        # {label, value, prefix}
    # Images that couldn't be carried into the body [{url, name, reason}] -- the draft still goes out, but people need to know images are missing
    dropped_images: list[dict] = field(default_factory=list)


@dataclass
class PublishResult:
    raw: dict             # raw /add response, archived whole into channel_meta
    price: str
    level: int
    article_id: Optional[int] = None   # platform article id (liveness checks / after-sales appeals query by it directly)
    order_no: Optional[str] = None     # platform order number (receipts query by it exactly, no paging)
    idempotent: bool = False           # True = platform judged it a duplicate submission, not charged again
    dropped_images: list[dict] = field(default_factory=list)   # same as DraftResult


# ── Body rendering ───────────────────────────────────────────────────────────

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_MD_INLINE = re.compile(r"(\*\*|__|\*|_|`)")


def _strip_inline_md(text: str) -> str:
    """Strip inline Markdown markers (bold/italic/inline code); the platform editor doesn't accept them.

    Image markers are stripped here too: a block that is a single image goes through render_html's image branch to become <img>;
    images embedded in text can't be their own paragraph, and rather than publishing `![alt](url)` literally in the body, drop them.
    """
    return _MD_INLINE.sub("", _MD_IMAGE.sub("", text or "")).strip()


# ── Images in the body ────────────────────────────────────────────────────────────
#
# Images in draft bodies are markdown `![alt](/api/ai-telemetry/public/topic-media/{topic}/{name})`,
# pointing to **our own** public endpoint. Putting that address directly into the media's body gives readers
# a broken external link (that machine isn't reachable from the internet). So before pushing, images must be uploaded to the platform's own object storage,
# leaving only platform URLs in the body -- which is what the membership site's editor does.

_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?[^)]*\)")
_TOPIC_MEDIA_RE = re.compile(r"/api/ai-telemetry/public/topic-media/(\d+)/([A-Za-z0-9._-]+)$")
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp"}
# The platform only accepts these types (imageAllowFiles from ?action=config, still true on recheck 2026-09-16).
# webp isn't among them -- but the image generation gateway returns webp, so webp is converted to png before upload,
# not skipped (see _uploadable_bytes)
_UPLOADABLE = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
_TRANSCODE_TO = {".webp": ".png"}


def _media_file(url: str) -> Optional[Path]:
    """Image URL in a draft -> local file. Returns None if unrecognized (external image).

    Reads from disk directly instead of hitting our own HTTP endpoint -- one less hop, unaffected by local nginx / ports.
    """
    from geo.services import media_storage
    return media_storage.local_file_from_url(url)


def _image_map_path(provider: str) -> Path:
    from geo.services import media_storage
    root = media_storage.data_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{provider}_image_map.json"


def _load_image_map(provider: str) -> dict:
    try:
        d = json.loads(_image_map_path(provider).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _uploadable_bytes(f: Path) -> tuple[bytes, str, str]:
    """Local asset -> (bytes, filename, mime), converting formats the platform doesn't accept first.

    webp -> png goes through image_marking.transcode, carrying over the content id in AI images;
    a plain Image.save would drop the marking, and off-site the image would become "unlabeled AI-generated content".
    """
    ext = f.suffix.lower()
    to = _TRANSCODE_TO.get(ext)
    if to:
        from geo.services import image_marking
        return image_marking.transcode(f, to), f.stem + to, _MIME[to]
    return f.read_bytes(), f.name, _MIME.get(ext, "application/octet-stream")


def _risky_media_urls(topic_id: Optional[int]) -> dict[str, str]:
    """Images under the topic flagged high / medium by external review -> {public URL: reason}. Last line of defense before publishing:
    draft generation recall no longer offers these images, but the body may have been edited by hand, or the review result may have arrived after generation.
    """
    if not topic_id:
        return {}
    try:
        from geo.database import SessionLocal
        from geo.models.ai_telemetry import TopicMediaORM
        from geo.services import media_storage
        s = SessionLocal()
        try:
            rows = (s.query(TopicMediaORM)
                      .filter(TopicMediaORM.topic_id == topic_id,
                              TopicMediaORM.external_risk.in_(("high", "medium")))
                      .all())
            return {media_storage.public_url(m): f"外检判定{m.external_risk}风险" for m in rows}
        finally:
            s.close()
    except Exception:  # noqa: BLE001 — if the lookup fails don't block; don't let a DB blip stall publishing
        log.warning("risky media lookup failed topic=%s", topic_id, exc_info=True)
        return {}


def upload_body_images(body_markdown: str, *, provider: str = DEFAULT_PROVIDER,
                       dropped: Optional[list[dict]] = None,
                       blocked: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Upload body images to the platform; returns {image URL in draft: platform URL}.

    - The same image is reused across drafts; upload results are cached in `data/{provider}_image_map.json`
      (the platform names files by content md5, so re-uploading only wastes bandwidth)
    - **A single image failure doesn't sink the whole article**: skip it, that image is omitted from the body, the rest publishes --
      images aren't a precondition for the draft
    - But skipping can't be just a warning (2026-09-16): the caller passes in a `dropped` list, and each image that failed to
      upload appends {url, name, reason}, returned to the user with the publish result -- before, users only found out
      the article had no images once the media published it
    - `blocked` {url: reason}: images not allowed out (external review risk); a hit is recorded as dropped directly, not uploaded
    """
    urls = [m.group(2) for m in _MD_IMAGE.finditer(body_markdown or "")]
    if not urls:
        return {}
    cache = _load_image_map(provider)
    out: dict[str, str] = {}
    dirty = False

    def _drop(url: str, name: str, reason: str) -> None:
        log.warning("[%s] image not carried over (%s): %s", provider, reason, name or url[:120])
        if dropped is not None:
            dropped.append({"url": url, "name": name, "reason": reason})

    for url in dict.fromkeys(urls):          # dedupe preserving order
        why = (blocked or {}).get(url.split("?")[0].strip())
        if why:
            _drop(url, Path(url).name, why)
            continue
        hit = cache.get(url)
        if isinstance(hit, str) and hit.startswith("http"):
            out[url] = hit
            continue
        f = _media_file(url)
        if f is None:
            from geo.services import media_storage
            _drop(url, "", "外链图片,不是本站素材" if media_storage.parse_public_url(url) is None
                  else "文件不在本机(素材在另一台服务器上)")
            continue
        ext = f.suffix.lower()
        if ext not in _UPLOADABLE and ext not in _TRANSCODE_TO:
            _drop(url, f.name, f"平台不收 {ext} 格式")
            continue
        try:
            data, name, mime = _uploadable_bytes(f)
            got = get_client(provider).upload_image(data, name, mime)
        except (RwjApiError, OSError) as e:  # noqa: BLE001
            _drop(url, f.name, f"上传失败:{e}")
            continue
        out[url] = got
        cache[url] = got
        dirty = True
    if dirty:
        try:
            _image_map_path(provider).write_text(
                json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    return out


def render_html(body_markdown: str, *, images: Optional[dict[str, str]] = None) -> str:
    """Draft markdown -> platform editor HTML (Word-paste style).

    Minimal conversion set: # heading lines -> 16pt bold subheadings; list items keep the "· " prefix; everything else as paragraphs;
    first paragraph uses lead margins. All text is HTML-escaped before wrapping in span.

    Input first goes through `outbound_body` -- the single body entry point for both Ruanwenjiang / Meijieku push paths;
    the 「内容由AI生成…生成方 …」 ("content generated by AI... generator ...") label line at the tail of existing drafts and zero-width watermarks are stripped here in one go,
    otherwise media would publish them verbatim in the article body.
    """
    parts: list[str] = []
    first_para = True
    for block in re.split(r"\n\s*\n", outbound_body(body_markdown)):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        # Images are their own paragraphs (that's how the generator lays them out): a block that is a single image -> output <img>.
        # Images without a platform URL are **dropped as a whole block** -- better no image than a broken external link.
        img = _MD_IMAGE.fullmatch(lines[0].strip()) if len(lines) == 1 else None
        if img:
            src = (images or {}).get(img.group(2))
            if src:
                alt = html_mod.escape(_strip_inline_md(img.group(1)))
                parts.append(
                    f'<p style="text-align: center;"><img src="{html_mod.escape(src)}"'
                    f' alt="{alt}" style="max-width: 100%;"/></p>')
                parts.append("<p>&nbsp;</p>")
            continue

        m = _MD_HEADING.match(lines[0])
        if m and len(lines) == 1:
            text = html_mod.escape(_strip_inline_md(m.group(2)))
            parts.append("<p>&nbsp;</p>")
            parts.append(f'<p style="{_HEAD}"><span style="{_S_HEAD}">{text}</span></p>')
            parts.append("<p>&nbsp;</p>")
            continue
        buf: list[str] = []
        for ln in lines:
            hm = _MD_HEADING.match(ln)
            if hm:
                # A heading line inside a paragraph block: flush accumulated paragraphs first
                if buf:
                    parts.append(_para("".join(buf) if len(buf) == 1 else " ".join(buf), first_para))
                    first_para = False
                    buf = []
                text = html_mod.escape(_strip_inline_md(hm.group(2)))
                parts.append("<p>&nbsp;</p>")
                parts.append(f'<p style="{_HEAD}"><span style="{_S_HEAD}">{text}</span></p>')
                parts.append("<p>&nbsp;</p>")
                continue
            stripped = ln.strip()
            if re.match(r"^[-*+]\s+", stripped):
                stripped = "· " + re.sub(r"^[-*+]\s+", "", stripped)
            elif re.match(r"^\d+[.、)]\s*", stripped):
                pass  # ordered lists keep their numbering as-is
            buf.append(html_mod.escape(_strip_inline_md(stripped)))
        if buf:
            parts.append(_para(" ".join(buf), first_para))
            first_para = False
    return "".join(parts)


def _para(text: str, lead: bool) -> str:
    style = _LEAD if lead else _BODY
    out = f'<p style="{style}"><span style="{_S_BODY}">{text}</span></p>'
    if not lead:
        out += "<p>&nbsp;</p>"
    return out


# ── Amounts ───────────────────────────────────────────────────────────────

def price_to_cents(price: str | float | int) -> Optional[int]:
    try:
        return int((Decimal(str(price)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


# ── Session / resource verification ────────────────────────────────────────────────────

def _client_or_raise(provider: str = DEFAULT_PROVIDER) -> RwjClient:
    c = get_client(provider)
    if not c.configured:
        env = "RWJ" if c.provider == "rwj" else c.provider.upper()
        raise RwjNotConfigured(
            f"{c.spec.label}通道未配置({env}_MOBILE / {env}_PASSWORD)")
    return c


def channel_status(provider: str = DEFAULT_PROVIDER) -> dict:
    """Channel status: configured / level / balance (best effort; None if unavailable)."""
    spec = site(provider)
    c = get_client(spec.provider)
    base = {"provider": spec.provider, "label": spec.label}
    if not c.configured:
        return {**base, "configured": False, "level": None, "balance": None}
    info = c.ensure_session()
    balance = info.get("balance")
    return {
        **base,
        "configured": True,
        "level": int(info.get("level") or 1),
        "balance": str(balance) if balance is not None else None,
    }


def verify_resource(c: RwjClient, type_: str, resource_id: int, title_hint: str,
                    level: int) -> tuple[dict, dict, str]:
    """Live verification before publishing/pushing drafts: resource still listed, placement method available, fetch live price.

    Returns (raw resource row, chosen placement method, live price). The list has no lookup by id, so matching is by
    name keyword with paging -- title_hint is required.
    """
    if type_ not in SUPPORTED_TYPES:
        raise RwjApiError(f"暂不支持的媒体类型 {type_!r}")
    res = c.find_resource(type_, resource_id, title_hint)
    if res is None:
        raise RwjApiError(f"媒体资源 {title_hint!r}(编号 {resource_id})已不在列表中,可能已下架")
    delivery = pick_delivery(res)
    price = resource_price(res, level, delivery=delivery)
    return res, delivery, price


# ── Drafts (free) ────────────────────────────────────────────────────────

def push_drafts(doc: TopicGeneratedDocORM, *, type_: str, targets: list[dict],
                provider: str = DEFAULT_PROVIDER,
                ) -> tuple[list[DraftResult], list[dict]]:
    """Push a draft to the platform's draft box (no charge), binding multiple target media at once.

    The platform's `resource` is already an array -- multiple outlets are merged into **one draft** (same shape as ticking
    multiple outlets in the member center); N outlets share one draft_id, but each outlet's live price/placement method is returned
    separately, and the caller persists them as separate placement tasks.

    targets: [{resource_id, resource_title}]
    Returns (DraftResult for each successful outlet, failures [{resource_id, resource_title, message}]).
    One outlet being delisted doesn't affect others; if none verified, raise directly so the endpoint errors.
    """
    if not targets:
        raise RwjApiError("未选择媒体资源")
    c = _client_or_raise(provider)
    info = c.ensure_session()
    level = int(info.get("level") or 1)

    title = (doc.title or doc.source_query_text or f"稿件 #{doc.id}").strip()
    # Upload images to the platform's own object storage first; leave only platform URLs in the body (see upload_body_images)
    dropped: list[dict] = []
    content = render_html(doc.body_markdown or "",
                          images=upload_body_images(doc.body_markdown or "",
                                                    provider=provider, dropped=dropped,
                                                    blocked=_risky_media_urls(doc.topic_id)))
    if not content:
        raise RwjApiError("稿件正文为空,无法推送")

    verified: list[tuple[dict, dict, str, str]] = []   # (res, delivery, price, param name)
    failed: list[dict] = []
    for tg in targets:
        rid = int(tg["resource_id"])
        rtitle = str(tg.get("resource_title") or "")
        try:
            res, delivery, price = verify_resource(c, type_, rid, rtitle, level)
        except RwjAuthError:
            raise                      # login session problems are global and shouldn't be recorded as a single-outlet failure
        except RwjApiError as e:
            failed.append({"resource_id": rid, "resource_title": rtitle, "message": str(e)})
            continue
        verified.append((res, delivery, str(price), rtitle))

    if not verified:
        raise RwjApiError(failed[0]["message"] if failed else "没有可用的媒体资源")

    body = c.build_draft_body(type_, title=title, content=content,
                              resources=[v[0] for v in verified], level=level)
    saved = c.draft_save(body)
    draft_id: Optional[int] = saved if isinstance(saved, int) else None

    # Read back to confirm: draft_save's return structure reliability is unknown; the list is authoritative
    try:
        page = c.draft_list(type_, pageSize=20) or {}
        rows = page.get("data") if isinstance(page, dict) else page
        for row in rows or []:
            if draft_id is not None and row.get("draft_id") == draft_id:
                break
            if draft_id is None and row.get("title") == title:
                draft_id = row.get("draft_id")
                break
        else:
            log.warning("[%s] draft saved but not found in list (doc=%s title=%r)",
                        c.provider, doc.id, title)
    except RwjApiError:
        log.exception("[%s] draft list read-back failed doc=%s", c.provider, doc.id)

    out = [
        DraftResult(
            draft_id=draft_id,
            resource_id=int(res["resource_id"]),
            resource_title=str(res.get("title") or rtitle),
            price=price,
            level=level,
            delivery={"label": delivery.get("label"), "value": delivery.get("value"),
                      "prefix": delivery.get("prefix")},
            dropped_images=list(dropped),
        )
        for res, delivery, price, rtitle in verified
    ]
    return out, failed


def push_draft(doc: TopicGeneratedDocORM, *, type_: str, resource_id: int,
               resource_title: str, provider: str = DEFAULT_PROVIDER) -> DraftResult:
    """Single-outlet version (old call sites like auto media selection still use it) -- semantics identical to the multi-outlet version."""
    results, _failed = push_drafts(
        doc, type_=type_, provider=provider,
        targets=[{"resource_id": resource_id, "resource_title": resource_title}],
    )
    return results[0]


# ── Formal publish (charged, double gate) ────────────────────────────────────────────────

def publish(doc: TopicGeneratedDocORM, *, type_: str, resource_id: int,
            resource_title: str, expected_price: str,
            provider: str = DEFAULT_PROVIDER,
            out_order_no: str = "") -> PublishResult:
    """Place the real order for a confirmed task (Article/{Type}/add, charged).

    Guardrails (inside this function): the gate must be open, re-check the price before ordering, refuse if it differs from the confirmed price.
    Idempotency/dedupe (caller): the endpoint layer first uses the state machine optimistic lock to set the task to publishing and commit,
    then calls here; timeouts/unknown results are not retried, verified via reconcile.
    """
    if not settings.RWJ_PUBLISH_ENABLED:
        raise RwjNotConfigured(
            f"{site(provider).label}扣费闸门未开(RWJ_PUBLISH_ENABLED=0),仅允许推草稿")
    c = _client_or_raise(provider)
    info = c.ensure_session()
    level = int(info.get("level") or 1)
    res, delivery, price = verify_resource(c, type_, resource_id, resource_title, level)
    if str(price) != str(expected_price):
        raise RwjApiError(
            f"价格已变化:确认时 ¥{expected_price},当前 ¥{price},请重新确认")

    title = (doc.title or doc.source_query_text or f"稿件 #{doc.id}").strip()
    dropped: list[dict] = []
    content = render_html(doc.body_markdown or "",
                          images=upload_body_images(doc.body_markdown or "",
                                                    provider=provider, dropped=dropped,
                                                    blocked=_risky_media_urls(doc.topic_id)))
    body = c.build_publish_body(type_, title=title, content=content,
                                resources=[res], level=level,
                                out_order_no=out_order_no)
    try:
        raw = c.publish(type_, body)
    except RwjAuthError:
        raise
    except RwjApiError as e:
        if "余额" in str(e):
            raise RwjBalanceError(str(e), status=e.status, payload=e.payload) from e
        raise
    out = raw if isinstance(raw, dict) else {"data": raw}
    # The submit response carries article_id / order_id directly (API 2), no need to page through the list;
    # idempotent=true means this was a duplicate submission and **was not charged again**; what we get is the first order
    got = out.get("result") if isinstance(out.get("result"), list) else []
    first = got[0] if got and isinstance(got[0], dict) else {}
    return PublishResult(raw=out, price=str(price), level=level,
                         article_id=(int(first["article_id"])
                                     if str(first.get("article_id") or "").isdigit() else None),
                         order_no=str(first.get("order_id") or "") or None,
                         idempotent=bool(out.get("idempotent")),
                         dropped_images=dropped)


# ── Receipts (measured against the real article list on 2026-08-19; parsing centralized here) ──────────────────

# A measured row (Meijieku /Article/Website|Wemedia/list; Ruanwenjiang shares the same backend, same shape):
#   {"article_id":538357,"order_id":"2026081913203900102943","resource_id":170129,
#    "resource_title":"<media-site-a>","title":"<article title>","price":"4.00","link":"http://…",
#    "rejection_info":null,"release_time":1787117654,"create_time":1787116839,"status":2}
# **One row is one outlet**: the same article placed with multiple outlets = multiple rows with the same title, resource info flattened onto the row
# (`resource_title` always present; `resource_id` only on self-media etc., null for websites);
# the nested `resource` array assumed early on doesn't exist -- only a compatible read is kept, no longer relied on.
_URL_KEYS = ("link", "url", "article_url", "publish_url", "push_url", "view_url")
_ORDER_KEYS = ("order_id", "order_no", "orderno", "article_id", "id")
_NAME_KEYS = ("resource_title", "name", "media_name")

# Official status codes (API 3 in the "downstream agent API integration doc"):
#   0 pending (awaiting editor) / 1 received (scheduling publish) / 2 published (link always present) /
#   4 refunded (publish failed, reason in refund_info or rejection_info) / 9 in after-sales
# Earlier only 1/2/4 were inferred from measurements, missing 0 and 9, and 4 was called "rejected" --
# the precise meaning of 4 is "publish failed, payment returned to balance"; rejection is just one cause.
_STATUS_TEXT = {0: "待处理", 1: "已收稿", 2: "已发布", 4: "已退款(发稿失败)", 9: "售后中"}
# Will there still be a result: 0/1/9 are still in progress, 2 succeeded, 4 is a terminal failure
_STATUS_PENDING = (0, 1, 9)


def _first_url(d: dict) -> Optional[str]:
    for k in _URL_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return None


def _row_media(row: dict) -> list[dict]:
    """Media items in one order row, normalized to {resource_id, name, url}.

    The measured shape is flat (one outlet per row), returning a single element; the nested `resource` array is an early assumed
    shape that can still be read if the platform ever returns it. When the row has no media info at all, returns an empty list -- so the caller
    knows "this row can't be attributed to an outlet".
    """
    def _norm(d: dict) -> dict:
        name = ""
        for k in _NAME_KEYS:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                name = v.strip()
                break
        return {"resource_id": d.get("resource_id"), "name": name, "url": _first_url(d)}

    items = row.get("resource")
    if isinstance(items, list):
        out = [_norm(i) for i in items if isinstance(i, dict)]
        if out:
            return out
    if row.get("resource_title") or row.get("resource_id"):
        return [_norm(row)]
    return []


def _media_matches(item: dict, resource_id: Optional[int],
                   resource_title: Optional[str]) -> bool:
    """Whether this media item is the outlet our task placed with (match id first, fall back to name)."""
    if resource_id is not None:
        try:
            if int(item.get("resource_id")) == int(resource_id):
                return True
        except (TypeError, ValueError):
            pass
    if resource_title and item.get("name"):
        return str(item["name"]).strip() == resource_title.strip()
    return False


def _status_text(row: dict) -> str:
    raw = row.get("status")
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return str(row.get("status_text") or row.get("status_name") or raw or "")
    text = _STATUS_TEXT.get(code) or f"状态{code}"
    for k in ("rejection_info", "refund_info", "rewrite_info"):
        reason = row.get(k)
        if isinstance(reason, str) and reason.strip():
            return f"{text}:{reason.strip()}"
    return text


def _row_rank(row: dict) -> tuple:
    """Which row to pick when the same title and outlet appear in multiple rows (re-order/re-submit): has link > not rejected > ordered later."""
    return (1 if _first_url(row) else 0,
            0 if _is_rejected(row) else 1,
            int(row.get("create_time") or 0),
            int(row.get("article_id") or 0))


def _is_rejected(row: dict) -> bool:
    """Whether this row is "publish failed" (status=4, payment returned; reason in the rejection or refund field)."""
    if str(row.get("status") or "") == "4":
        return True
    for k in ("rejection_info", "refund_info"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return True
    return False


def _extract_receipt(row: dict, resource_id: Optional[int] = None,
                     resource_title: Optional[str] = None) -> dict:
    """Extract (order identifier, article URL, status text) from one row of Article/{Type}/list.

    When resource_id/resource_title is passed, **only that outlet's** URL is accepted; never treat outlet A's link
    as outlet B's receipt (otherwise mark_published's URL dedupe would reject every outlet from the second on).
    """
    media = _row_media(row)
    targeted = resource_id is not None or bool(resource_title)
    mine = [m for m in media if _media_matches(m, resource_id, resource_title)] if targeted else media

    url = None
    for m in mine:
        if m["url"]:
            url = m["url"]
            break
    # Row-level URL only counts when "no outlet was specified" or "this row has only one outlet (and it's ours)"
    if url is None and (not targeted or not media or (mine and len(media) == 1)):
        url = _first_url(row)

    order_no = None
    for k in _ORDER_KEYS:
        if row.get(k) is not None:
            order_no = str(row[k])
            break
    article_id = row.get("article_id")
    return {"order_no": order_no, "url": url, "status_text": _status_text(row),
            "rejected": _is_rejected(row),
            "article_id": int(article_id) if str(article_id or "").isdigit() else None,
            "raw_keys": sorted(row.keys())}


def find_article(type_: str, title: str, *, max_pages: int = 5,
                 page_size: int = 50, provider: str = DEFAULT_PROVIDER,
                 resource_id: Optional[int] = None,
                 resource_title: Optional[str] = None,
                 order_no: Optional[str] = None,
                 out_order_no: Optional[str] = None) -> Optional[dict]:
    """Look up by title (+ outlet) in the article list (shared by post-timeout submitted-or-not checks and receipt polling).

    When one article is placed with multiple outlets, the title appears repeatedly in the list with **one row per outlet** -- you must pass
    resource_id/resource_title to locate your own outlet, otherwise another outlet's link gets backfilled as
    your receipt. When a row has no media info at all (shouldn't happen in theory; guards against platform structure changes) fall back to
    title-only matching, keeping old single-outlet task behavior unchanged.

    Returns the _extract_receipt result + the raw row; None if not found.
    """
    c = _client_or_raise(provider)

    # ── With an order number, query exactly; don't page ──
    # The platform supports exact lookup by order_id (API 3) and reverse lookup by downstream order number (API 5). Paging + title matching
    # is the fallback when there's no order number -- the earlier multi-outlet bug that mistook another outlet's link as ours came from that path.
    if order_no:
        try:
            data = c.articles(type_, pageNo=1, pageSize=5, order_id=str(order_no), status="all")
            rows = (data.get("data") if isinstance(data, dict) else data) or []
            hit = next((r for r in rows if isinstance(r, dict)
                        and str(r.get("order_id") or "") == str(order_no)), None)
            if hit is not None:
                receipt = _extract_receipt(hit, resource_id, resource_title)
                receipt["row"] = hit
                return receipt
        except RwjApiError as e:  # noqa: BLE001
            log.warning("[%s] exact lookup by order number %s failed, falling back to paging: %s", provider, order_no, e)
    if out_order_no:
        try:
            rows = c.order_query(type_, [str(out_order_no)])
            hit = next((r for r in rows if isinstance(r, dict)), None)
            if hit is not None:
                receipt = _extract_receipt(hit, resource_id, resource_title)
                receipt["row"] = hit
                return receipt
        except RwjApiError as e:  # noqa: BLE001
            log.warning("[%s] reverse lookup by downstream order number %s failed, falling back to paging: %s", provider, out_order_no, e)

    targeted = resource_id is not None or bool(resource_title)
    best: Optional[dict] = None          # best row matching this task's outlet
    fallback: Optional[dict] = None      # fallback row when the title matches but the row has no media info
    for page in range(1, max_pages + 1):
        data = c.articles(type_, pageNo=page, pageSize=page_size)
        rows = (data.get("data") if isinstance(data, dict) else data) or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("title") or "") != title:
                continue
            if targeted:
                media = _row_media(row)
                if media:
                    if not any(_media_matches(m, resource_id, resource_title) for m in media):
                        continue    # same title but not our outlet, keep looking
                else:
                    if fallback is None or _row_rank(row) > _row_rank(fallback):
                        fallback = row
                    continue
            if best is None or _row_rank(row) > _row_rank(best):
                best = row
        if not rows or (isinstance(data, dict) and page >= int(data.get("last_page") or 1)):
            break
    row = best if best is not None else fallback
    if row is None:
        return None
    receipt = _extract_receipt(row, resource_id, resource_title)
    receipt["row"] = row
    return receipt
