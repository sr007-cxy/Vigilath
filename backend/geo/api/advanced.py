"""Advanced GEO check endpoints.

Wraps the 6 advanced modes from root geo_checker.py behind tier-gated HTTP
endpoints. Each endpoint:
  - Validates the URL / entity name.
  - Resolves the caller's membership (anonymous → free).
  - Rejects with 402 if the mode requires a higher tier than the caller has.
  - Calls the corresponding advanced_runners function in-process.
  - Maps RuntimeError (missing/invalid API key) → 503 so the frontend can tell
    the user it's a server-side configuration issue, not bad input.
"""

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from geo.models.advanced import (
    CompareRequest,
    CompareResponse,
    CrawlTestRequest,
    CrawlTestResponse,
    AuthorityAuditRequest,
    AuthorityAuditResponse,
    CitationCheckRequest,
    CitationCheckResponse,
    AiVisibilityRequest,
    AiVisibilityResponse,
    EntityAuditRequest,
    EntityAuditResponse,
    AeoVisibilityRequest,
    AeoVisibilityResponse,
    MODE_MIN_TIER,
    TIER_RANK,
)
from geo.services import advanced_runners
from geo.utils.validator import validate_url, sanitize_url
from geo.utils.error_handler import AppException
from geo.utils.request_log import request_log
from geo.api.geo import _resolve_membership_from_request


router = APIRouter()


def _ensure_tier(request: Request, mode_key: str) -> None:
    """Raise 402 if the caller's tier is below the mode's minimum."""
    min_tier = MODE_MIN_TIER.get(mode_key)
    if not min_tier:
        raise AppException(status_code=500, message=f"unknown mode {mode_key}")
    membership, _user_id = _resolve_membership_from_request(request)
    caller_rank = TIER_RANK.get(membership.slug, 0)
    needed_rank = TIER_RANK.get(min_tier, 99)
    if caller_rank < needed_rank:
        raise AppException(
            status_code=402,
            message=f"'{mode_key}' requires {min_tier} tier or higher",
            details={"current_tier": membership.slug, "required_tier": min_tier},
        )


def _check_url(url: str) -> str:
    if not validate_url(url):
        raise AppException(status_code=400, message="Invalid URL format")
    return sanitize_url(url)


async def _run_or_raise(fn, *args, **kwargs):
    # Runners make 10s–minutes of blocking HTTP calls. Offload to FastAPI's
    # threadpool so the event loop stays free for other requests.
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except RuntimeError as e:
        # API key missing / invalid. 503 = service unavailable on our side.
        raise AppException(status_code=503, message=str(e))
    except ValueError as e:
        raise AppException(status_code=400, message=str(e))
    except Exception as e:
        raise AppException(status_code=500, message=f"advanced check failed: {e}")


def _log_ctx(request: Request, mode: str, url: str):
    """Pull user_id + tier from the auth context and build the request_log CM."""
    membership, user_id = _resolve_membership_from_request(request)
    return request_log(
        f"check.advanced.{mode}",
        mode,
        url,
        user_id=user_id,
        tier=membership.slug if membership else "free",
    )


@router.post("/check/advanced/compare", response_model=CompareResponse)
async def advanced_compare(body: CompareRequest, request: Request):
    _ensure_tier(request, "compare")
    clean_urls = [_check_url(u) for u in body.urls]
    with _log_ctx(request, "compare", "+".join(clean_urls)) as rec:
        data = await _run_or_raise(advanced_runners.run_compare, clean_urls)
        rec["urls"] = clean_urls
        return CompareResponse(**data)


@router.post("/check/advanced/crawl-test", response_model=CrawlTestResponse)
async def advanced_crawl_test(body: CrawlTestRequest, request: Request):
    _ensure_tier(request, "crawlTest")
    clean_url = _check_url(body.url)
    with _log_ctx(request, "crawl-test", clean_url):
        data = await _run_or_raise(advanced_runners.run_crawl_test, clean_url)
        return CrawlTestResponse(**data)


@router.post("/check/advanced/authority", response_model=AuthorityAuditResponse)
async def advanced_authority(body: AuthorityAuditRequest, request: Request):
    _ensure_tier(request, "authority")
    clean_url = _check_url(body.url)
    with _log_ctx(request, "authority", clean_url):
        data = await _run_or_raise(advanced_runners.run_authority_audit, clean_url)
        return AuthorityAuditResponse(**data)


@router.post("/check/advanced/citation", response_model=CitationCheckResponse)
async def advanced_citation(body: CitationCheckRequest, request: Request):
    _ensure_tier(request, "citation")
    clean_url = _check_url(body.url)
    with _log_ctx(request, "citation", clean_url):
        data = await _run_or_raise(advanced_runners.run_citation_check, clean_url)
        return CitationCheckResponse(**data)


@router.post("/check/advanced/visibility", response_model=AiVisibilityResponse)
async def advanced_visibility(body: AiVisibilityRequest, request: Request):
    _ensure_tier(request, "visibility")
    clean_url = _check_url(body.url)
    with _log_ctx(request, "visibility", clean_url) as rec:
        data = await _run_or_raise(
            advanced_runners.run_ai_visibility,
            clean_url,
            body.custom_queries,
        )
        if body.custom_queries:
            rec["custom_queries"] = body.custom_queries
        return AiVisibilityResponse(**data)


@router.post("/check/advanced/entity", response_model=EntityAuditResponse)
async def advanced_entity(body: EntityAuditRequest, request: Request):
    _ensure_tier(request, "entity")
    entity_name = body.entity_name.strip()
    entity_type = body.entity_type.strip().lower()
    with _log_ctx(request, "entity", entity_name) as rec:
        rec["entity_type"] = entity_type
        data = await _run_or_raise(
            advanced_runners.run_entity_audit,
            entity_name,
            entity_type,
        )
        return EntityAuditResponse(**data)


@router.post("/check/advanced/aeo", response_model=AeoVisibilityResponse)
async def advanced_aeo(body: AeoVisibilityRequest, request: Request):
    _ensure_tier(request, "aeo")
    clean_url = _check_url(body.url)
    with _log_ctx(request, "aeo", clean_url):
        data = await _run_or_raise(advanced_runners.run_aeo_visibility, clean_url)
        return AeoVisibilityResponse(**data)
