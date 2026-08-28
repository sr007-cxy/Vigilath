# Performance Guide

This document records current performance characteristics, diagnostics, and optimization targets.

## Request path and timeouts

Requests flow from the browser to nginx, FastAPI/Uvicorn, the GEO checker, and external websites or AI providers. The client timeout is 300 seconds and nginx allows 600 seconds. Individual page fetches are normally limited to 15 seconds, social probes to about 8 seconds, and AI calls to about 45 seconds. Keep P99 below the client timeout.

## Workload characteristics

Default checks issue many independent HTTP probes, so remote latency and serial orchestration dominate slow requests. Visibility and entity modes depend on repeated AI calls; citation depends on an external provider; AEO is usually local analysis. Compare and authority modes scale with URLs and external sources. Re-run measurements against the current deployment before capacity decisions.

## Timing and diagnostics

The backend emits `geo.timing` records to journald and adds `X-Process-Time` to responses:

```bash
sudo journalctl -u geo.service --since "10 minutes ago" | grep 'geo.timing'
sudo journalctl -u geo.service | grep 'geo.timing:http'
sudo journalctl -u geo.service | grep 'geo.timing' | awk -F'elapsed_ms=' '$2+0 > 5000'
```

Instrumentation is in `backend/geo/utils/timing.py`; HTTP middleware is in `backend/geo/main.py`.

## Optimization priorities

1. Run independent default checks concurrently while preserving dependencies such as sitemap discovery.
2. Parallelize independent technical crawlability and authority probes.
3. Bound visibility retries so upstream slowness cannot exceed the client timeout.
4. Replace module-global checker state and broad locks with request-local state.
5. Add load tests and track P50/P95/P99 latency, upstream errors, and worker utilization.

Concurrency changes must preserve rate limits, deterministic scoring, thread safety, and cancellation. Validate with the backend test suite and representative target sites.
