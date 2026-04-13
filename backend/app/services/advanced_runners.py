"""Thin wrappers around the 6 advanced modes in root geo_checker.py.

The root file shadows the `geo_checker/` package, so a plain `import
geo_checker` resolves to the package (which only has `__init__.py` + a CLI
`__main__.py` that's out of sync). We sidestep that by loading `geo_checker.py`
from the project root as a freshly-named module via importlib.

Each runner:
  - Redirects stdout so CLI formatting noise doesn't flood FastAPI logs.
  - Calls the patched function with return_data=True to get a dict back.
  - Lets RuntimeError/ValueError propagate so the router can map them to HTTP.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
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


def _silent_call(fn, *args, **kwargs) -> Dict[str, Any]:
    """Invoke fn with stdout suppressed; return fn's structured dict result."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
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
