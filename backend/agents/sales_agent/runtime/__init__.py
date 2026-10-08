"""Sales Agent runtime guardrails — timeouts, usage caps, context budget, output validation.

This layer doesn't touch business logic; it only makes sure nothing "runs away": wrap up when the model dies, trim when history grows,
block when tools emit unreasonable results. Business is in ../service/, orchestration in ../api.py.
"""
