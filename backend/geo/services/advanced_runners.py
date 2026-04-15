"""Thin wrappers around the 6 advanced modes in root geo_checker.py.

The root file shadows the `geo_checker/` package, so a plain `import
geo_checker` resolves to the package (which only has `__init__.py` + a CLI
`__main__.py` that's out of sync). We sidestep that by loading `geo_checker.py`
from the project root as a freshly-named module via importlib.

Each runner:
  - Suppresses stdout per-thread so CLI formatting noise doesn't flood logs
    and concurrent requests don't corrupt each other's output.
  - Calls the patched function with return_data=True to get a dict back.
  - Lets RuntimeError/ValueError propagate so the router can map them to HTTP.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import threading
from typing import Any, Dict, List, Optional


def _load_geo_checker_core():
    here = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    target = os.path.join(project_root, "geo_checker.py")
    if not os.path.isfile(target):
        raise RuntimeError(f"geo_checker.py not found at {target}")
    spec = importlib.util.spec_from_file_location("geo_checker_core", target)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to build spec for geo_checker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_geo = _load_geo_checker_core()


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
        result = fn(*args, **kwargs)
    finally:
        _stdout_proxy._state.buf = None
    if not isinstance(result, dict):
        raise RuntimeError(
            f"{fn.__name__} did not return structured data — "
            f"make sure return_data=True and the function was patched."
        )
    return result


def run_compare(urls: List[str]) -> Dict[str, Any]:
    return _silent_call(_geo.compare_urls, urls, return_data=True)


def run_crawl_test(url: str) -> Dict[str, Any]:
    return _silent_call(_geo.crawl_test, url, return_data=True)


def run_authority_audit(url: str) -> Dict[str, Any]:
    return _silent_call(_geo.authority_audit, url, return_data=True)


def run_citation_check(url: str) -> Dict[str, Any]:
    return _silent_call(_geo.citation_check, url, return_data=True)


def run_ai_visibility(url: str, custom_queries: Optional[List[str]] = None) -> Dict[str, Any]:
    return _silent_call(
        _geo.ai_visibility,
        url,
        custom_queries=custom_queries,
        return_data=True,
    )


def run_entity_audit(entity_name: str, entity_type: str = "brand") -> Dict[str, Any]:
    return _silent_call(
        _geo.entity_audit,
        entity_name,
        entity_type=entity_type,
        return_data=True,
    )
