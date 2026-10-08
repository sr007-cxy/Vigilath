"""Slot state machine for the GEO diagnosis flow — pure data + pure functions; no DB, no services, no model calls.

Isomorphic with agents/sales_agent/flow/ (the state / router / slots trio), which already works;
each file here mirrors its counterpart rather than inventing a new abstraction.

Why the GEO line needs it: the one-click full diagnosis needs "brand + industry terms" before it can start, and before that
these two slots **existed only in the model's memory of the history**. That's how the 2026-08-27 <brand-a> chain reaction happened:
the model didn't get the industry terms -> made up a "if you're in footwear/apparel..." guess list; the terms the user answered had to be
re-remembered by the model next turn; and route_line judges the business line **message by message**, so an answer containing 「口碑」 ("reputation")
got routed to the sentiment line, and that turn couldn't even see run_full_diagnosis' schema.

With slots persisted to DB: the model only does the talking; "what's still missing" is computed by router.next_step, and nobody can change it.
"""
from agents.geo.flow.absorb import absorb
from agents.geo.flow.router import (
    ASKING, BRAND, DONE, INDUSTRY_TERMS, READY, RUNNING, next_step,
)
from agents.geo.flow.slots import extract
from agents.geo.flow.state import DiagnosisState

__all__ = [
    "DiagnosisState", "next_step", "extract", "absorb",
    "ASKING", "BRAND", "INDUSTRY_TERMS", "READY", "RUNNING", "DONE",
]
