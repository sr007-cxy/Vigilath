"""Vigilath agent runtimes — each subpackage is an independent agent business line.

  agents.geo         GEO/AEO optimization assistant (customer self-service + ops workspace), runs in geo-agent.service:8010
  agents.sales_agent  Sales lead agent, routes mounted in the same service

★ This `__init__.py` must not be deleted. `agents/geo` has the same name as `backend/geo`; once `backend/agents/`
  is inserted into sys.path (pytest does this when it infers rootdir from "the nearest dir without __init__.py"),
  `import geo` resolves to `agents/geo/` instead of `backend/geo/`, showing up as
  `ModuleNotFoundError: No module named 'geo.models'` — even though the file is right there.
  With it, `agents` is a regular package, rootdir walks up to `backend/`, and each geo stays where it belongs.
"""
