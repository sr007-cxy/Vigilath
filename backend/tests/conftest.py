"""Initialize the geo package before importing sales_agent.

`geo/__init__.py` imports `agents.sales_agent.models`, which in turn imports `geo.database`.
Entering from the sales_agent side hits this existing circular import (unrelated to the tests here; the real startup order is geo first).
"""
from __future__ import annotations

import geo  # noqa: F401  # isort:skip
