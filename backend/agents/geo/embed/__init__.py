"""External thin layer (embed) — lets external agents (Xiaolongxia) connect to this GEO agent.

See docs/03-系统设计/Agent/对外开放-契约.md. Core:
  - tokens.py   1-year account token issuance (claim) + validation (per request: verify signature + check enabled)
  - deps.py     embed token -> AgentDeps (account_id injected by backend, caps narrowed)
  - api.py      /api/agent/v1/* (chat / data / meta) stable external surface
"""
