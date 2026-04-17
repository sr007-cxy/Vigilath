"""Thin wrappers around the 7 advanced modes in the geo_checker package.

Post-refactor (2026-04-17): geo_checker is now a proper package. We import
each mode module directly — no more importlib.spec_from_file_location hack.

Each runner:
  - Suppresses stdout per-thread so CLI formatting noise doesn't flood logs
    and concurrent requests don't corrupt each other's output.
  - Calls the mode function with return_data=True to get a dict back.
  - Lets RuntimeError/ValueError propagate so the router can map them to HTTP.
"""

from __future__ import annotations

import io
import sys
import threading
from typing import Any, Dict, List, Optional

# Core + mode modules — direct package imports now that the package exists.
# GEO_EMIT_STRUCTURED=1 is set by services/geo_checker.py on its import,
# which runs first (both are touched during FastAPI app startup). If the
# order ever flips, emit_check() will still work — the env var is just a
# flag, read at module load inside geo_checker.output.
import os
os.environ.setdefault("GEO_EMIT_STRUCTURED", "1")

from geo_checker import checks as _gc_checks
from geo_checker.modes import (
    compare as _mode_compare,
    crawl_test as _mode_crawl_test,
    authority_audit as _mode_authority,
    citation as _mode_citation,
    visibility as _mode_visibility,
    entity as _mode_entity,
    aeo as _mode_aeo,
)
from geo_checker import orchestrate as _gc_orchestrate

from geo.utils.timing import instrument_checks, instrument_funcs, time_block

# Timing instrumentation: wrap every check_* (defined in geo_checker.checks)
# and every top-level runner with elapsed-time logs. Idempotent via the
# _geo_timed marker inside timing._wrap, so running under both default and
# advanced paths does not double-wrap.
instrument_checks(_gc_checks)
# Also wrap orchestrate's local bindings — CHECK_REGISTRY lambdas resolve
# `check_https(url)` against orchestrate's globals, not _gc_checks. Missing
# this means per-check timing disappears from logs even though check_* in
# _gc_checks IS wrapped.
instrument_checks(_gc_orchestrate)
instrument_funcs(_gc_orchestrate, ["generate_score"])
instrument_funcs(_mode_compare, ["compare_urls"])
instrument_funcs(_mode_crawl_test, ["crawl_test"])
instrument_funcs(_mode_authority, ["authority_audit"])
instrument_funcs(_mode_citation, ["citation_check"])
instrument_funcs(_mode_visibility, ["ai_visibility"])
instrument_funcs(_mode_entity, ["entity_audit"])
instrument_funcs(_mode_aeo, ["aeo_visibility"])


class _ThreadLocalStdout:
    """Proxy that routes writes to a thread-local buffer when one is set,
    else falls back to the real stdout.

    Installed process-wide once at import time. `contextlib.redirect_stdout`
    is not safe here because FastAPI's threadpool runs multiple runners
    concurrently and redirect_stdout mutates the global sys.stdout — threads
    would see each other's buffers mid-execution.
    """

    _state = threading.local()

    def __init__(self, real):
        self._real = real

    def _target(self):
        return getattr(self._state, "buf", None) or self._real

    def write(self, s):
        return self._target().write(s)

    def flush(self):
        return self._target().flush()

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self._target(), name)


_stdout_proxy: _ThreadLocalStdout
if isinstance(sys.stdout, _ThreadLocalStdout):
    _stdout_proxy = sys.stdout  # type: ignore[assignment]
else:
    _stdout_proxy = _ThreadLocalStdout(sys.stdout)
    sys.stdout = _stdout_proxy  # type: ignore[assignment]


def _silent_call(fn, *args, **kwargs) -> Dict[str, Any]:
    """Invoke fn with stdout captured into a per-thread buffer."""
    buf = io.StringIO()
    _stdout_proxy._state.buf = buf
    try:
        # Extra block-level timing so we see e.g. `block=advanced:ai_visibility
        # elapsed_ms=185320` as a single line per request, independent of the
        # function-level wrapping.
        with time_block(f"advanced:{fn.__name__}"):
            result = fn(*args, **kwargs)
    finally:
        _stdout_proxy._state.buf = None
    if not isinstance(result, dict):
        raise RuntimeError(
            f"{fn.__name__} did not return structured data — "
            f"make sure return_data=True and the function was patched."
        )
    return result


# ---------------------------------------------------------------------------
# Redis cache wrapper for advanced runs
# ---------------------------------------------------------------------------
# 24h TTL for all advanced modes. The paid ones (visibility / entity /
# citation) benefit most — each miss is $0.03-$0.84 of real OpenRouter cost.
# The free ones (aeo / compare / crawl-test / authority) also cache so we
# don't re-fetch + re-probe on repeat hits.
#
# Cache key: mode name + sha1 of (identifying params). Tier is baked into
# the key so "pro's visibility" and "scale's visibility" don't collide
# (though in practice both run the same code — tier gate is at the API
# layer, not here).
from geo.services import cache_service  # noqa: E402

_ADVANCED_TTL_S = 24 * 60 * 60


def _cached_run(mode: str, identity: Dict[str, Any], runner):
    """Generic L1 cache wrapper. `identity` is a dict that uniquely defines
    the request (e.g. {"url": ..., "custom_queries": [...]}). On miss, calls
    runner() and stores the dict return value. Runner must return a dict."""
    key = cache_service.make_cache_key(mode, identity.get("url", ""), extra=identity)
    cached = cache_service.get_cached_report(key)
    if cached is not None:
        return cached
    result = runner()
    if isinstance(result, dict):
        cache_service.set_cached_report(key, result, ttl_s=_ADVANCED_TTL_S)
    return result


def run_compare(urls: List[str]) -> Dict[str, Any]:
    # compare has no single URL — use joined list as the identity
    identity = {"url": "+".join(sorted(urls)), "urls": sorted(urls)}
    return _cached_run(
        "compare",
        identity,
        lambda: _silent_call(_mode_compare.compare_urls, urls, return_data=True),
    )


def run_crawl_test(url: str) -> Dict[str, Any]:
    return _cached_run(
        "crawl-test",
        {"url": url},
        lambda: _silent_call(_mode_crawl_test.crawl_test, url, return_data=True),
    )


def run_authority_audit(url: str) -> Dict[str, Any]:
    return _cached_run(
        "authority",
        {"url": url},
        lambda: _silent_call(_mode_authority.authority_audit, url, return_data=True),
    )


def run_citation_check(url: str) -> Dict[str, Any]:
    return _cached_run(
        "citation",
        {"url": url},
        lambda: _silent_call(_mode_citation.citation_check, url, return_data=True),
    )


def run_ai_visibility(url: str, custom_queries: Optional[List[str]] = None) -> Dict[str, Any]:
    identity = {"url": url, "custom_queries": sorted(custom_queries) if custom_queries else None}
    return _cached_run(
        "visibility",
        identity,
        lambda: _silent_call(
            _mode_visibility.ai_visibility,
            url,
            custom_queries=custom_queries,
            return_data=True,
        ),
    )


def run_entity_audit(entity_name: str, entity_type: str = "brand") -> Dict[str, Any]:
    # Entity audit keys on entity_name+entity_type (no URL)
    identity = {"url": entity_name, "entity_type": entity_type}
    return _cached_run(
        "entity",
        identity,
        lambda: _silent_call(
            _mode_entity.entity_audit,
            entity_name,
            entity_type=entity_type,
            return_data=True,
        ),
    )


def run_aeo_visibility(url: str) -> Dict[str, Any]:
    return _cached_run(
        "aeo",
        {"url": url},
        lambda: _silent_call(_mode_aeo.aeo_visibility, url, return_data=True),
    )
