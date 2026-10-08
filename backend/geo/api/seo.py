"""SEO audit endpoints — the classic search-optimization counterpart to /check.

Gating mirrors the GEO endpoints exactly:
  - all 12 categories always run, so the score is tier-independent and real
  - categories outside the caller's tier are stripped from `checks[]` before
    the response is serialized
  - fix text is a paid perk enforced server-side
  - logged-in callers spend the same monthly detection quota as a GEO check
    (one pool — a check is a check, regardless of which suite ran)

These endpoints run the SEO suite on its own, for API and CLI callers. The
website runs both suites in one shot via `include_seo` on /api/check, which
charges one quota and writes one history row; see api/geo.py. The gating rules
live in services/seo_checker.py so both paths share one copy.
"""
import asyncio
from typing import Optional, Tuple

from fastapi import APIRouter, Request, Response
import jwt
from jwt import PyJWTError as JWTError

from geo.api.auth import SECRET_KEY, ALGORITHM
from geo.models.membership import Membership
from geo.models.seo import SeoCategoriesOut, SeoTestRequest, SeoTestResult
from geo.services.detection_service import save_detection
from geo.services.membership_service import membership_service
from geo.services.quota_service import check_and_increment_quota
from geo.services.seo_checker import (
    FIX_ALLOWED_TIERS as _FIX_ALLOWED_TIERS,
    LOCKED_FOR_FREE,
    locked_seo_categories,
    run_seo_check,
    strip_locked_seo,
)
from geo.services.user_service import user_service
from geo.utils.error_handler import AppException
from geo.utils.request_log import request_log
from geo.utils.validator import validate_url, sanitize_url

from geo_checker.orchestrate import ALL_SEO_CATEGORIES, FREE_SEO_CATEGORIES

router = APIRouter()

_BAD_URL_MESSAGE = "Unable to analyze this URL. Please check the address and try again."


def _resolve_membership(request: Request) -> Tuple[Membership, Optional[int]]:
    """Effective membership + user_id for the caller; free tier when anonymous."""
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                user = user_service.get_user_by_username(username)
                if user:
                    return membership_service.get_effective_membership(user.id), user.id
        except JWTError:
            pass
    free = membership_service.get_membership_by_slug("free")
    if free is None:
        raise AppException(status_code=500, message="Free membership tier not initialized")
    return free, None


async def _run_or_400(rec: dict, *args, **kwargs) -> SeoTestResult:
    """Run in a worker thread; turn any unhandled failure into a friendly 400.

    Preserves the original exception under `raw_error` for the request log —
    `request_log` overwrites `error` with the re-raised AppException.
    """
    try:
        return await asyncio.to_thread(run_seo_check, *args, **kwargs)
    except AppException:
        raise
    except Exception as exc:
        rec["raw_error"] = f"{type(exc).__name__}: {exc}"
        raise AppException(status_code=400, message=_BAD_URL_MESSAGE)


@router.get("/seo/categories", response_model=SeoCategoriesOut)
async def seo_categories():
    """The category vocabulary, so the frontend never hardcodes the list."""
    return SeoCategoriesOut(
        all_categories=list(ALL_SEO_CATEGORIES),
        free_categories=list(FREE_SEO_CATEGORIES),
    )


@router.post("/seo/check/anonymous", response_model=SeoTestResult)
async def seo_check_anonymous(body: SeoTestRequest, request: Request, response: Response):
    """Anonymous SEO audit — runs all 12, exposes the 3 free categories.

    No quota, same as the anonymous GEO check: this is the top of the funnel.
    """
    if not validate_url(body.url):
        raise AppException(status_code=400, message="Invalid URL format")
    sanitized_url = sanitize_url(body.url)

    with request_log("seo.anonymous", "seo", sanitized_url, tier="free") as rec:
        result = await _run_or_400(
            rec,
            sanitized_url,
            False,   # anonymous is free tier — no fix text
            None,    # run all 12 for a realistic score
            "free",
            body.force_refresh,
        )
        locked = list(LOCKED_FOR_FREE)
        result = strip_locked_seo(result, locked)
        result.tier = "free"
        result.locked_categories = locked
        rec["score"] = result.score
        rec["grade"] = result.grade
        return result


@router.post("/seo/check", response_model=SeoTestResult)
async def seo_check(body: SeoTestRequest, request: Request, response: Response):
    """Tiered SEO audit. Bearer token optional; quota applies when present."""
    if not validate_url(body.url):
        raise AppException(status_code=400, message="Invalid URL format")
    sanitized_url = sanitize_url(body.url)

    membership, user_id = _resolve_membership(request)
    if user_id is not None:
        check_and_increment_quota(user_id, membership)

    include_fix = body.include_fix and membership.slug in _FIX_ALLOWED_TIERS

    with request_log("seo.check", "seo", sanitized_url,
                     user_id=user_id, tier=membership.slug) as rec:
        result = await _run_or_400(
            rec,
            sanitized_url,
            include_fix,
            None,               # all 12 regardless of tier
            membership.slug,
            body.force_refresh,
        )
        locked = locked_seo_categories(membership)
        result = strip_locked_seo(result, locked)
        result.tier = membership.slug
        result.locked_categories = locked

        # History for logged-in users. mode="seo" keeps SEO runs distinguishable
        # from GEO runs in the personal center without a second table.
        if user_id is not None:
            save_detection(user_id=user_id, result=result, mode="seo")

        rec["score"] = result.score
        rec["grade"] = result.grade
        rec["fix"] = include_fix
        return result
