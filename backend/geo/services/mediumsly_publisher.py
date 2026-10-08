"""Push approved drafts to the Mediumsly site.

Constraints:
  - Single token (MEDIUMSLY_INTERNAL_API_TOKEN); the whole call is a trusted server-to-server call.
  - Author identity (bot publishing model, per topic not per user): **never** push real user emails to
    Mediumsly. In GEO one user can own multiple topics (brands), each topic an independent author
    identity -- so the author email on the Mediumsly side is `topic-<topic_id>@<bot_domain>`
    (not merged per user). bot_domain is the first entry (after sorting) of
    MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST.
      • the same topic always maps to the same Mediumsly author (email upsert key)
      • real user emails never leak to third-party sites
      • author.name uses topic.name (the brand's name, usually Chinese), falling back
        to topic.target -> user.name -> email prefix
  - Republish: with mediumsly_post_id use PATCH, otherwise POST. A PATCH 404 means the article was
    manually deleted on Mediumsly -- the publisher raises MediumslyPostGone, and the caller should clear the local
    mediumsly_post_id so the next retry automatically recreates it via POST.

Hardening (all implemented in this file, independent of the runtime environment):
  - HTTPS enforced (URL must be https://)
  - MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST must be configured (empty = configuration error, refuse to send);
    the publisher synthesizes bot emails with this domain, effectively locking the author namespace on Mediumsly
  - Concurrency semaphore (prevents a GEO-side bug calling this function in a loop from overwhelming the other end)

Full integration docs: docs/04-internal-api.md + docs/05-geo-integration.md in the mediumsly repo.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from geo.database import settings
from geo.models.ai_telemetry import AiTelemetryTopicORM, TopicGeneratedDocORM
from geo.models.user import UserORM
from geo.services.content_marking import outbound_body

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 8.0  # seconds -- Mediumsly writes are normally < 200ms; headroom for cold starts / cross-region RTT

# Limit concurrency, so a GEO-side bug (calling publish in a loop) cannot instantly overwhelm the other end.
_CONCURRENCY = asyncio.Semaphore(5)


def _allowed_domains() -> frozenset[str]:
    """Parse the set of allowed email domains from env."""
    raw = (settings.MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST or "").strip()
    if not raw:
        return frozenset()
    return frozenset(d.strip().lower() for d in raw.split(",") if d.strip())


class MediumslyError(Exception):
    """All outbound failures raise this; the publish endpoint catches it and writes mediumsly_last_error."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class MediumslyNotConfigured(MediumslyError):
    """MEDIUMSLY_INTERNAL_API_TOKEN not set; the publish endpoint degrades to only marking publish_targets on this exception."""


class MediumslyPostGone(MediumslyError):
    """404 on PATCH -- the article was deleted on Mediumsly. The caller should clear mediumsly_post_id and retry with POST."""


@dataclass
class PushResult:
    post_id: str
    url: str
    author_created: bool  # Whether Mediumsly created a new account (True on the POST path; the PATCH path has no such field, always False)


def _platform_tags(doc: TopicGeneratedDocORM) -> list[str]:
    """Derive Mediumsly tags from doc.platform ("抖音"/"小红书"/... -- Douyin/Xiaohongshu), at most 5."""
    tags: list[str] = []
    if doc.platform:
        tags.append(doc.platform)
    # The topic name could be added as a tag later; kept minimal for now.
    return tags[:5]


def _bot_email_for_topic(topic: AiTelemetryTopicORM, allowed_domains: frozenset[str]) -> str:
    """Synthesize the bot email: `topic-<topic_id>@<bot_domain>`.
    bot_domain is the first allowlist entry (after sorting, deterministic).
    """
    # sorted keeps the choice stable with multiple domains; production usually configures only 1
    bot_domain = sorted(allowed_domains)[0]
    return f"topic-{topic.id}@{bot_domain}"


def _author_display_name(topic: AiTelemetryTopicORM, user: UserORM) -> str:
    """Choose the author name shown on Mediumsly, priority: topic.name > topic.target > user.name > email prefix."""
    for candidate in (topic.name, topic.target, user.name):
        if candidate and candidate.strip():
            return candidate.strip()
    if user.email and "@" in user.email:
        return user.email.split("@")[0]
    return f"topic-{topic.id}"


async def push(doc: TopicGeneratedDocORM, user: UserORM, topic: AiTelemetryTopicORM) -> PushResult:
    """Push the doc to Mediumsly. Returns a PushResult or raises a MediumslyError subclass."""
    if not settings.MEDIUMSLY_INTERNAL_API_TOKEN:
        raise MediumslyNotConfigured("MEDIUMSLY_INTERNAL_API_TOKEN not set")

    base = (settings.MEDIUMSLY_API_URL or "").rstrip("/")
    if not base.startswith("https://"):
        raise MediumslyError(
            f"MEDIUMSLY_API_URL must be https:// (got {base!r})",
            code="CONFIG",
        )

    allowed = _allowed_domains()
    if not allowed:
        # Required by the bot publishing model -- prevents real user emails being pushed to Mediumsly if someone forgets to configure it
        raise MediumslyError(
            "MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST must be set (provides bot email domain)",
            code="CONFIG",
        )

    headers = {
        "Authorization": f"Bearer {settings.MEDIUMSLY_INTERNAL_API_TOKEN}",
        "Content-Type": "application/json",
    }

    body: dict = {
        "title": doc.title or doc.source_query_text or f"Topic doc #{doc.id}",
        # Strip content label lines + zero-width watermarks from the outbound body (see content_marking.outbound_body)
        "body_markdown": outbound_body(doc.body_markdown),
        "tags": _platform_tags(doc),
        "status": "published",
    }

    async with _CONCURRENCY, httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            if doc.mediumsly_post_id:
                # Update the published article in place. Mediumsly does not allow changing the author -- no author field.
                r = await client.patch(
                    f"{base}/api/internal/posts/{doc.mediumsly_post_id}",
                    headers=headers, json=body,
                )
                if r.status_code == 404:
                    raise MediumslyPostGone(
                        "Post not found at Mediumsly (was it deleted?)",
                        status=404, code="NOT_FOUND",
                    )
            else:
                # First push: bot publishing -- use topic-<id>@<bot_domain> as the author
                # email on the Mediumsly side (upsert key), real user emails never leak. The same topic on Mediumsly
                # is always the same author (different brands of the same user are never merged).
                # Display uses topic.name (the brand's Chinese name), with fallback by priority.
                body["author"] = {
                    "email": _bot_email_for_topic(topic, allowed),
                    "name":  _author_display_name(topic, user),
                }
                r = await client.post(
                    f"{base}/api/internal/posts",
                    headers=headers, json=body,
                )
        except httpx.RequestError as e:
            raise MediumslyError(f"Network error: {e!s}", status=None, code="NETWORK") from e

    if r.status_code >= 400:
        # Mediumsly error format: {"error": {"code": "...", "message": "..."}}
        try:
            err = r.json().get("error", {}) or {}
        except Exception:  # noqa: BLE001
            err = {}
        raise MediumslyError(
            err.get("message") or (r.text or "")[:200] or f"HTTP {r.status_code}",
            status=r.status_code,
            code=err.get("code"),
        )

    data = r.json()
    post = data["post"]
    author = data.get("author") or {}
    return PushResult(
        post_id=str(post["id"]),
        url=str(post["url"]),
        author_created=bool(author.get("created", False)),
    )
