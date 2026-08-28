# Brand Growth Metrics

The Brand Growth area combines sources, platforms, competitors, matrices, insights, queries, responses, and published outcomes.

## Metric requirements

For every displayed number, record the source observation, calculation, time window, account, and freshness. Distinguish measured values from estimates and expose missing-data states instead of silently showing zero.

## Current implementation

The REST API is implemented in `backend/geo/api/ai_telemetry.py`; persistence and schemas are in `backend/geo/models/ai_telemetry.py`; the UI is under `frontend/src/pages/BrandGrowth/`. Keep this document aligned with those modules when fields or pages change.
