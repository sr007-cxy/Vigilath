"""Module-level mutable state: scores dict, page cache, fix flag.

Migrated from /geo_checker.py lines 27 (SHOW_FIX), 125 (_page_cache),
148-188 (_scores / track_score / get_ai_visibility_score / get_grade /
reset_state).

Originally these globals were single-threaded (upstream CLI design), which is
why backend/geo/services/geo_checker.py has `_geo_checker_lock` around the
entire request. With issue #14 (top-level generate_score concurrency), we now
need the 25 check_* to run in parallel WITHIN a single request — so the two
mutable globals below carry their own locks:

  - `_scores_lock`        — serializes `track_score` updates
  - `_page_cache_lock`    — serializes dict read/write in io.fetch

`SHOW_FIX` is a read-mostly flag toggled once per request (before parallel
check execution begins), so it doesn't need locking.

A future P2 refactor will migrate these to contextvars / explicit parameters
to remove the locks entirely.
"""

import threading

# Toggled by --fix CLI flag, and also by the backend when running a tier that
# should include fix recommendations. Read by output.fix / output.emit_fix.
# Read-mostly — set once before concurrent check execution starts.
SHOW_FIX = False

# URL → requests.Response cache. Populated by io.fetch; reset by reset_state.
_page_cache = {}
_page_cache_lock = threading.Lock()

# Category → {"earned": float, "max": float}. Accumulated by track_score;
# read by get_ai_visibility_score and generate_score's breakdown.
_scores = {}
_scores_lock = threading.Lock()

# Same shape as _scores, but a SEPARATE namespace for the SEO check suite
# (seo_checks.py). Deliberately not merged into _scores: the GEO AI Visibility
# Score must stay identical to the frozen root geo_checker.py baseline, and
# adding SEO categories to the shared dict would silently move every site's
# GEO score. Two namespaces, two scores, one process.
_seo_scores = {}
_seo_scores_lock = threading.Lock()


def track_score(category, earned, max_points):
    """Record earned/max points for a check category. Thread-safe."""
    with _scores_lock:
        if category not in _scores:
            _scores[category] = {"earned": 0.0, "max": 0.0}
        _scores[category]["earned"] += earned
        _scores[category]["max"] += max_points


def get_ai_visibility_score():
    """Calculate overall AI Visibility Score (0-100)."""
    total_earned = sum(v["earned"] for v in _scores.values())
    total_max = sum(v["max"] for v in _scores.values())
    if total_max == 0:
        return 0
    return round((total_earned / total_max) * 100)


def track_seo_score(category, earned, max_points):
    """Record earned/max points for a SEO check category. Thread-safe.

    Writes to `_seo_scores`, never to `_scores` — see the comment on
    `_seo_scores` for why the two namespaces stay apart.
    """
    with _seo_scores_lock:
        if category not in _seo_scores:
            _seo_scores[category] = {"earned": 0.0, "max": 0.0}
        _seo_scores[category]["earned"] += earned
        _seo_scores[category]["max"] += max_points


def get_seo_score():
    """Calculate overall SEO Score (0-100) from the SEO namespace."""
    total_earned = sum(v["earned"] for v in _seo_scores.values())
    total_max = sum(v["max"] for v in _seo_scores.values())
    if total_max == 0:
        return 0
    return round((total_earned / total_max) * 100)


def get_grade(score):
    """Convert 0-100 score to letter grade."""
    if score >= 90:
        return "A+"
    elif score >= 80:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 50:
        return "D"
    else:
        return "F"


def reset_state():
    """Reset global state for a fresh run. Called at the top of each CLI /
    API invocation so accumulated scores and cached pages don't bleed across
    requests. Hold both locks to guarantee no concurrent check sees a
    half-reset state.

    Mutate in place via .clear() — modules that did `from ..state import _scores`
    hold a reference to this exact dict; reassigning would leave them stuck on
    the pre-reset object and cause the read-side to see {} (e.g. aeo.py reading
    _scores at the end produced score=0 / categories={}).
    """
    with _scores_lock:
        _scores.clear()
    with _seo_scores_lock:
        _seo_scores.clear()
    with _page_cache_lock:
        _page_cache.clear()
