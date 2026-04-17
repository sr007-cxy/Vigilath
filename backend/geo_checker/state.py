"""Module-level mutable state: scores dict, page cache, fix flag.

Migrated from /geo_checker.py lines 27 (SHOW_FIX), 125 (_page_cache),
148-188 (_scores / track_score / get_ai_visibility_score / get_grade /
reset_state).

These globals are inherited wholesale from the upstream CLI design. They are
what necessitates `backend/geo/services/geo_checker.py::_geo_checker_lock` —
concurrent requests in the same process would race here. A future P2 refactor
will migrate these to contextvars or explicit parameters; for now, everything
runs under the lock.
"""

# Toggled by --fix CLI flag, and also by the backend when running a tier that
# should include fix recommendations. Read by output.fix / output.emit_fix.
SHOW_FIX = False

# URL → requests.Response cache. Populated by io.fetch; reset by reset_state.
_page_cache = {}

# Category → {"earned": float, "max": float}. Accumulated by track_score;
# read by get_ai_visibility_score and generate_score's breakdown.
_scores = {}


def track_score(category, earned, max_points):
    """Record earned/max points for a check category."""
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
    requests (when the lock has been released between them)."""
    global _scores, _page_cache
    _scores = {}
    _page_cache = {}
