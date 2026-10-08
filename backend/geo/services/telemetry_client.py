"""Call layer for the batch run center (telemetry-service) /run-now -- with retries across restart windows.

Why a separate layer: geo-telemetry shutdown isn't instantaneous. After systemd sends SIGTERM, uvicorn first closes the
listen socket, then waits for in-flight requests; if they don't finish they're SIGKILLed when TimeoutStopSec expires. During this window
new callers get Connection refused / connection dropped. In the restart at 2026-08-13 17:07,
one-click diagnosis report 40's visibility, competitor and sentiment sections all hit these 90 seconds at once, all judged skipped, and the report
was finalized at 35 points F -- one ops restart ruined a user's report.

The retry criterion is "how fast it failed", not the exception type:
  - failed only after the full timeout -> the engine really is slow; retrying just burns another timeout; don't retry;
  - failed within tens of seconds -> the service isn't there (restart window / port not listening); trying again after a wait will most likely work.
The backoff sequence must span geo-telemetry's shutdown window (TimeoutStopSec + startup); default 30s/75s.

ENV:
    TELEMETRY_RUN_NOW_RETRIES   retry count, default 2 (0 = retries off)
    TELEMETRY_RUN_NOW_BACKOFF   comma-separated backoff seconds, default "30,75"
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_BACKOFF = "30,75"


def _retries() -> int:
    try:
        return max(0, int(os.environ.get("TELEMETRY_RUN_NOW_RETRIES", "2")))
    except ValueError:
        return 2


def _backoff() -> list[float]:
    raw = os.environ.get("TELEMETRY_RUN_NOW_BACKOFF", DEFAULT_BACKOFF)
    out: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(max(0.0, float(part)))
        except ValueError:
            continue
    return out or [30.0, 75.0]


# Seven business endpoints of the batch run center (/run-now /enqueue /run-topic /cell-insight(/feedback) /briefing/generate
# /suggest-queries) verify X-Internal-Token since 2026-09-21 (V-networks README section 7.3.1).
# If INTERNAL_TOKEN is unset, no header is sent -- symmetric with the center's "open if unset"; a missing setting on either side means open, not broken.
# The value lives in backend/.env, shared by the backend / agent / sales-scheduler containers.
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "").strip()


def internal_headers() -> dict[str, str]:
    return {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}


def post_run_now(
    base_url: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    label: str = "",
) -> Any:
    """POST {base_url}/run-now, retrying with backoff when the service is unreachable; returns the parsed JSON.

    Raises httpx.HTTPError (the last exception as-is) -- callers' existing
    `except httpx.HTTPError` branches need no change; they just get the result after retries are exhausted.
    """
    url = f"{base_url.rstrip('/')}/run-now"
    delays = _backoff()
    attempts = _retries() + 1
    last_exc: httpx.HTTPError | None = None

    for i in range(attempts):
        start = time.time()
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, json=payload, headers=internal_headers())
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            elapsed = time.time() - start
            last_exc = e
            # Failing after the full timeout means the engine is slow, not that the service is gone -- retrying is pointless
            exhausted = elapsed >= timeout * 0.9
            if i == attempts - 1 or exhausted:
                if exhausted and i < attempts - 1:
                    log.warning("run-now%s hit the full timeout (%.0fs), not retrying: %s",
                                f"[{label}]" if label else "", elapsed, e)
                raise
            delay = delays[min(i, len(delays) - 1)]
            log.warning("run-now%s attempt %d/%d failed (within %.0fs), retrying in %.0fs: %s",
                        f"[{label}]" if label else "", i + 1, attempts, elapsed, delay, e)
            time.sleep(delay)

    assert last_exc is not None  # the loop always returns or raises; this is only for type narrowing
    raise last_exc
