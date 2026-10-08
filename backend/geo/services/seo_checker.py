"""Backend entry point for the SEO audit suite.

Mirrors services/geo_checker.py, with three deliberate differences:

  1. It reads the SEO score namespace (`generate_seo_score`), so the GEO score
     is untouched.
  2. It shares `_geo_checker_lock` with the GEO runner rather than taking its
     own. Both suites call `reset_state()`, which clears the shared
     `_page_cache` and BOTH score dicts — running them concurrently in one
     process would have each wipe the other's half-finished scores. One lock,
     one checker at a time, per process.
  3. Cache is L1 (Redis) only. The GEO path has an L2 fallback on
     `detection_records`; SEO history rows are written with mode="seo" for the
     history tab, but are not read back as a cache — one cache tier is enough
     until SEO checks show up in the slow-query logs.
"""
import io
import json
import logging
import os
import re
from typing import List, Optional

# Same env contract as the GEO service: geo_checker.output reads this at
# module-load time to decide whether to embed i18n key markers.
os.environ["GEO_EMIT_STRUCTURED"] = "1"

from geo_checker import state as _gc_state
from geo_checker import orchestrate as _gc_orchestrate
from geo_checker import seo_checks as _gc_seo_checks

from geo.models.geo import CheckResult
from geo.models.membership import Membership
from geo.models.seo import SeoTestResult
from geo.services import cache_service
from geo.services.geo_checker import _extract_key_marker, _geo_checker_lock
from geo.utils.timing import instrument_checks, time_block

from geo_checker.orchestrate import ALL_SEO_CATEGORIES, FREE_SEO_CATEGORIES

_log = logging.getLogger(__name__)

# Per-check timing, same as the GEO suite. Both the source module and the
# orchestrator's local bindings must be wrapped — SEO_CHECK_REGISTRY holds
# direct function references resolved at import time in orchestrate's globals.
instrument_checks(_gc_seo_checks)
instrument_checks(_gc_orchestrate)

_REPORT_TTL_S = 24 * 60 * 60

# Same tier set that unlocks GEO fix text.
FIX_ALLOWED_TIERS = frozenset({"starter", "growth", "scale"})

# Everything outside the free three, precomputed — the locked set for any
# unpaid caller, and for the anonymous endpoints which have no membership row.
LOCKED_FOR_FREE = [c for c in ALL_SEO_CATEGORIES if c not in FREE_SEO_CATEGORIES]


def locked_seo_categories(membership: Membership) -> List[str]:
    """SEO categories this tier may not see in detail.

    The membership table gates GEO categories only (`allowed_check_categories`),
    so SEO visibility is derived from the tier itself: paid tiers see all 12,
    free and pro see the three free ones. When a tier-level SEO whitelist
    column is added, this is the single place to read it from.

    Lives in the service rather than in api/seo.py because both the SEO
    endpoints and the combined `include_seo` path on /api/check need it, and
    two copies of a gating rule is how paid content leaks.
    """
    if membership.slug in FIX_ALLOWED_TIERS:
        return []
    return list(LOCKED_FOR_FREE)


def strip_locked_seo(result: SeoTestResult, locked: List[str]) -> SeoTestResult:
    """Hide locked categories' details and recount the summary over what's left.

    Always copies: run_seo_check may return a cached object shared across
    tiers, and mutating it would poison the cache for a later paid caller.
    `score` / `grade` are preserved on purpose — the number is the site's real
    state, not a function of what the caller paid for.
    """
    if not locked:
        return result
    locked_set = set(locked)
    stripped = result.model_copy(deep=True)
    stripped.checks = [c for c in stripped.checks if c.category not in locked_set]
    stripped.summary = {
        "pass_count": sum(1 for c in stripped.checks if c.status == "PASS"),
        "warn_count": sum(1 for c in stripped.checks if c.status == "WARN"),
        "fail_count": sum(1 for c in stripped.checks if c.status == "FAIL"),
        "info_count": sum(1 for c in stripped.checks if c.status == "INFO"),
        "total_checks": len(stripped.checks),
    }
    return stripped

_SCORE_RE = re.compile(r"^SEO SCORE:\s*(\d+)/100\s*\(Grade: ([A-F+]+)\)")
_CATEGORY_RE = re.compile(r"^--- (SEO: .*) ---")
_STATUS_RE = re.compile(r"^\s*\[(PASS|WARN|FAIL|INFO| FIX)\] (.*)")
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _build_cache_key(url: str, include_fix: bool,
                     allowed_categories: Optional[List[str]], tier: str) -> str:
    categories_key = ",".join(sorted(allowed_categories)) if allowed_categories else "all"
    return cache_service.make_cache_key(
        "seo",
        url,
        tier=tier,
        include_fix=include_fix,
        extra={"categories": categories_key},
    )


def parse_seo_output(url: str, output: str) -> SeoTestResult:
    """Parse the SEO suite's text output into a structured result.

    Same line protocol as parse_geo_output, but only `--- SEO: X ---` headers
    are accepted as categories. That guard matters: if a combined run ever
    pipes both suites into one buffer, this parser takes only the SEO half
    instead of silently mixing GEO checks into the SEO score card.
    """
    output = _ANSI_RE.sub("", output)

    checks: List[CheckResult] = []
    current_category: Optional[str] = None
    score, grade = 0, "F"

    for line in output.split("\n"):
        line = line.strip()

        cat = _CATEGORY_RE.match(line)
        if cat:
            current_category = cat.group(1)
            continue

        status = _STATUS_RE.match(line)
        if status and current_category:
            tag = status.group(1)
            message, message_key, message_params = _extract_key_marker(status.group(2))
            if tag == " FIX":
                if checks:
                    checks[-1].fix = message.replace("\x02", "\n")
                    if message_key is not None:
                        checks[-1].fix_key = message_key
                        checks[-1].fix_params = message_params
            else:
                checks.append(CheckResult(
                    category=current_category,
                    status=tag,
                    message=message,
                    fix=None,
                    message_key=message_key,
                    message_params=message_params,
                ))

        hit = _SCORE_RE.match(line)
        if hit:
            score = int(hit.group(1))
            grade = hit.group(2)

    summary = {
        "pass_count": sum(1 for c in checks if c.status == "PASS"),
        "warn_count": sum(1 for c in checks if c.status == "WARN"),
        "fail_count": sum(1 for c in checks if c.status == "FAIL"),
        "info_count": sum(1 for c in checks if c.status == "INFO"),
        "total_checks": len(checks),
    }
    return SeoTestResult(url=url, score=score, grade=grade, checks=checks, summary=summary)


def run_seo_check(
    url: str,
    include_fix: bool,
    allowed_categories: Optional[List[str]] = None,
    tier: str = "free",
    force_refresh: bool = False,
) -> SeoTestResult:
    """Run the 12-category SEO audit in-process and return a structured result.

    L1 Redis cache, 24h TTL, keyed on (url, tier, include_fix, categories).
    `force_refresh` skips the read but still writes back, so a post-fix
    re-check overwrites the stale entry for everyone on that tier.
    """
    cache_key = _build_cache_key(url, include_fix, allowed_categories, tier)

    if not force_refresh:
        cached = cache_service.get_cached_report(cache_key)
        if cached:
            _log.info("SEO L1 cache HIT for %s", url)
            return SeoTestResult(**cached)

    try:
        buf = io.StringIO()
        from geo.services.advanced_runners import _stdout_proxy
        with _geo_checker_lock:
            old_show_fix = _gc_state.SHOW_FIX
            _gc_state.SHOW_FIX = include_fix
            _stdout_proxy._state.buf = buf
            try:
                with time_block(f"seo_check url={url}"):
                    _gc_orchestrate.generate_seo_score(
                        url, allowed_categories=allowed_categories
                    )
            finally:
                _stdout_proxy._state.buf = None
                _gc_state.SHOW_FIX = old_show_fix

        result = parse_seo_output(url, buf.getvalue())
        cache_service.set_cached_report(cache_key, result.model_dump(), ttl_s=_REPORT_TTL_S)
        return result
    except Exception as e:
        _log.exception("SEO check failed for %s", url)
        raise Exception(f"Failed to run SEO check: {e}")
