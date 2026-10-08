"""GEO Agent runtime guardrails — timeouts, usage caps, action gates, output validation.

This layer doesn't touch business logic; it only makes sure nothing "runs away": wrap up when the provider dies, trim when history grows,
block when tools emit unreasonable results. Business is in ../tools.py / ../admin_tools.py, orchestration in ../api.py.
"""
