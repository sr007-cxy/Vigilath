# AI Request Cost Analysis

Advanced visibility, entity, and citation checks call external AI providers. Cost and latency therefore depend on query count, model pricing, retries, and response size.

## Relative workload

- Visibility performs many query/engine combinations and is normally the largest workload.
- Entity performs multiple provider lookups and aggregation steps.
- Citation performs a small series of provider queries with backoff.
- AEO uses local page analysis and normally makes no AI call.

Treat all dollar figures as estimates. Provider prices and model availability change; calculate current cost from the provider dashboard before setting limits.

## Cost controls

- Enforce per-account quotas before starting an advanced run.
- Bound retries and response tokens.
- Cache stable evidence for the duration of a request or explicitly configured TTL.
- Record provider, model, input tokens, output tokens, and status for each call.
- Return partial results when a non-critical provider fails.

Use `backend/geo_checker/modes/` and `backend/geo/services/advanced_runners.py` as the implementation references. Never put provider credentials or customer prompts in this document.
