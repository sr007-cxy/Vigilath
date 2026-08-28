# GEO Checker Non-Determinism Analysis

GEO scores can vary between runs because the checker combines live web requests, external search indexes, and probabilistic AI responses.

## Main sources of variation

1. Target pages change, return different cache variants, or intermittently fail.
2. Search engines and social platforms update their indexes asynchronously.
3. AI providers use sampling and may change models or prompts.
4. Retries, timeouts, and rate limits change which evidence is available.
5. Parallel checks can complete in a different order when shared state is involved.

## Mitigations

- Record the URL, timestamp, provider, model, and checker version with every run.
- Use bounded timeouts and explicit retry policies.
- Keep scoring rules deterministic once evidence has been collected.
- Isolate request state; avoid module-global mutable scores and caches.
- Display evidence and uncertainty instead of implying that a score is permanent.

Regression tests should use recorded fixtures and fixed provider responses. Live smoke tests are useful for availability, but should not assert an exact score.
