"""Route access -- live sources are ops-only; the media library is open to all logged-in users.

De-branding is guaranteed by "users can't reach the endpoints that expose vendors at all", not by the frontend not calling them.
The frontend not calling them is just today's fact; if the endpoints are open, someone will call them sooner or later.
"""
from __future__ import annotations

from geo.main import app


def _route(path: str, method: str = "POST"):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def _guarded_by_roles(route) -> bool:
    """require_roles returns a closure whose name doesn't match -- identify it by the role set captured in the closure."""
    for d in route.dependant.dependencies:
        closure = getattr(d.call, "__closure__", None) or ()
        for cell in closure:
            val = cell.cell_contents
            if isinstance(val, tuple) and {"admin", "operator"} <= set(map(str, val)):
                return True
    return False


def test_realtime_vendor_endpoints_are_staff_only():
    """These four return provider / vendor name / purchase price and must be ops-only."""
    for path, method in [
        ("/api/rwj/providers", "GET"),
        ("/api/rwj/status", "GET"),
        ("/api/rwj/media/filters", "GET"),
        ("/api/rwj/media/search", "POST"),
    ]:
        assert _guarded_by_roles(_route(path, method)), f"{method} {path} 没有限运营"


def test_media_library_is_open_to_any_logged_in_user():
    for path, method in [
        ("/api/media/facets", "GET"),
        ("/api/media/search", "POST"),
        ("/api/media/docs/{doc_id}/push-draft", "POST"),
    ]:
        r = _route(path, method)
        assert not _guarded_by_roles(r), f"{method} {path} 不该限运营"
        # Login is still required (router-level get_current_user)
        assert any("get_current_user" in getattr(d.call, "__name__", "")
                   for d in r.dependant.dependencies), f"{method} {path} 没有登录闸"
