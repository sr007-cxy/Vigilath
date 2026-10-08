"""GEO optimization Agent — conversational GEO/AEO optimization assistant (Pydantic AI implementation).

Design: see docs/07-复盘归档/Agent/实现设计-2026-06.md and docs/03-系统设计/Agent/最佳方案-决策.md.

Layers:
  deps.py     AgentDeps (tenant context) + assert_owns (ownership check)
  model.py    DeepSeek model factory (OpenAI compatible)
  agent.py    build_agent(): Pydantic AI Agent + tool registration
  tools.py    Tool implementations (@RunContext[AgentDeps]; wrap existing services)
  methods.py  Method layer (not exposed to the model: auth/guardrails/publish/scheduling/push)
  api.py      /api/agent/* routes (chat SSE / diagnose)

The framework appears only in model/agent/tools; deps/methods/api are decoupled from it, so switching frameworks leaves them untouched.
"""
