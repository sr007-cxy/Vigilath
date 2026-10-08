"""geo_checker model call accounting + a shared implementation of "main request + one retry".

Two pieces of background:

1. A dozen `_query_*` functions in this package each hand-wrote the same two lines -- send once, on 429 sleep
   and send again. They're consolidated into `post_chat()` here, which also records calls in the model usage table.

2. geo_checker is a **zero-dependency CLI** (only imports re/time/urlparse/requests); `python
   geo_checker.py <url>` must run standalone without the backend. So accounting uses a **soft import**:
   running inside uvicorn / the geo package hits real accounting; running as a standalone CLI it silently degrades to a no-op.
   Don't import geo.* at module top level here.

Note: the root geo_checker.py and the copy under archive/ are frozen upstream merge baselines, **not a single line changes**
(see CLAUDE.md). Only the backend/geo_checker/ package is active code.
"""

import time

import requests

# Soft import: record if it can be imported, no-op if not (standalone CLI)
try:  # pragma: no cover
    from geo.services.llm_usage import record_llm_call as _record
except Exception:  # noqa: BLE001
    def _record(**_kwargs):
        return None


def post_chat(url, payload, headers, *, provider, engine, timeout=45,
              retry_status=(429,), retry_wait=5, call_kind="chat", attempt_base=0):
    """Send one /chat/completions; on retry_status wait a bit and send once more; returns the Response.

    Every `_query_*` used to hand-write this. Behavior is identical to before (**one** retry, not several),
    just with accounting added: the retry is recorded as its own row (attempt_no=1), because retries really cost money.

    Network exceptions are raised to the caller as-is (each `_query_*` catches requests.RequestException itself).

    retry_status=() disables the built-in retry -- use it when the caller has its own retry loop,
    and pass the round via attempt_base so attempt_no in the ledger lines up.
    """
    group = f"{engine}-{int(time.time() * 1000) % 100000000:08d}"

    def _send(attempt):
        t0 = time.perf_counter()
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except Exception as e:  # noqa: BLE001 —— record, then re-raise as-is
            name = type(e).__name__.lower()
            _record(service="checker", feature=f"checker.{engine}", provider=provider,
                    model=str(payload.get("model") or ""), endpoint=url, call_kind=call_kind,
                    status="network_error",
                    error_kind="timeout" if "timeout" in name else "network",
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    attempt_no=attempt, call_group_id=group)
            raise
        body = None
        if r.status_code < 400:
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = None
        _record(service="checker", feature=f"checker.{engine}", provider=provider,
                model=str(payload.get("model") or ""), endpoint=url, call_kind=call_kind,
                usage=body if isinstance(body, dict) else None,
                status="ok" if r.status_code < 400 else "http_error",
                error_kind="" if r.status_code < 400 else f"http_{r.status_code}",
                http_status=r.status_code,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                attempt_no=attempt, call_group_id=group)
        return r

    r = _send(attempt_base)
    if retry_status and r.status_code in retry_status:
        time.sleep(retry_wait)
        r = _send(attempt_base + 1)
    return r
