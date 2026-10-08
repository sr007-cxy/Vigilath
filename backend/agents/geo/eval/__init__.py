"""Agent evaluation scaffold.

Two layers (see docs / memory[[reference_geo_agent_deploy]]):
- Component level (test_routing.py): **no LLM, deterministic**, locks down route_line + business-line tool isolation, serving as the CI regression gate.
- End to end (run_eval.py): **runs the real model**, collects tool traces + LLM judge scores, measuring "whether the user really got the right result".

The labeled set lives in dataset.py. When changing prompts / switching models / tuning route_line keywords:
first run component level to locate which layer broke, then run end to end to decide whether to ship.
"""
