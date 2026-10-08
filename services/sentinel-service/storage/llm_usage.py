"""Model call accounting for the sentiment monitoring service - writes a row to main DB public.llm_call_log.

Why a local copy instead of importing the backend implementation: sentinel-service is **deployed independently**
(in production it runs on a separate host, not the main site EC2), does not install the backend code package,
and has no SQLAlchemy; it has always used raw psycopg3. So this is a minimal mirror of the backend's
`geo/services/llm_usage.py`: field definitions must match, and the DDL is owned by the backend's alembic.

Two pitfalls, read before changing this file:
  1. **The table name must be written as `public.llm_call_log`**. When storage/db.py:connect() binds a tenant it does
     `SET search_path TO "tenant_N", public` - without an explicit schema it may write to the wrong place.
  2. **Do not reuse the business connection**. chat_create cannot get the caller's conn, and a business transaction rollback would take
     the accounting with it. A separate short connection is used here (connect_timeout=5); the tens of milliseconds of overhead
     are negligible compared with one LLM call (hundreds of milliseconds to tens of seconds).

Accounting failures are always silent - sentiment monitoring batch runs must not be interrupted because accounting failed.
"""
from __future__ import annotations

import logging
import os
import socket
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any, Optional

log = logging.getLogger(__name__)

BJ_OFFSET = timedelta(hours=8)
_HOST = socket.gethostname()[:32]

# The feature scenario currently running. Each of the 10 call sites calls set_feature(...), and chat_create reads it when persisting to DB.
_feature: ContextVar[str] = ContextVar("sentinel_llm_feature", default="sentinel.unknown")

_INSERT = """
INSERT INTO public.llm_call_log (
    created_at, bj_date, service, host, feature, provider, model, endpoint_host,
    call_kind, status, error_kind, http_status,
    prompt_tokens, completion_tokens, total_tokens, cached_tokens, reasoning_tokens,
    token_source, requests_count, attempt_no, latency_ms,
    user_id, topic_id, ref_type, ref_id, trace_id, call_group_id, extra_json
) VALUES (
    %s, %s, 'sentinel', %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    NULL, NULL, %s, %s, %s, %s, NULL
)
"""


def set_feature(name: str) -> None:
    """Tag which feature scenario the following model calls belong to (sentiment analysis / brief / response draft ...)."""
    _feature.set((name or "sentinel.unknown")[:64])


def current_feature() -> str:
    return _feature.get()


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    return None


def _parse_usage(usage: Any) -> dict:
    """Normalize the openai SDK's CompletionUsage.

    Fields differ by vendor: GLM / DeepSeek use prompt_tokens / completion_tokens;
    native DashScope uses input_tokens / output_tokens; cache hits are called
    prompt_cache_hit_tokens in DeepSeek and prompt_tokens_details.cached_tokens in the OpenAI family.
    **If unavailable, leave None (not obtained), never write 0** - 0 means "the provider explicitly returned 0".
    """
    if usage is None:
        return {"prompt": None, "completion": None, "total": None,
                "cached": None, "reasoning": None, "source": "none"}
    g = (lambda k: _as_int(getattr(usage, k, None)))
    prompt = g("prompt_tokens") or g("input_tokens")
    completion = g("completion_tokens") or g("output_tokens")
    total = g("total_tokens")
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    cached = g("prompt_cache_hit_tokens") or g("cached_tokens")
    if cached is None:
        det = getattr(usage, "prompt_tokens_details", None)
        cached = _as_int(getattr(det, "cached_tokens", None)) if det is not None else None
    det2 = getattr(usage, "completion_tokens_details", None)
    reasoning = _as_int(getattr(det2, "reasoning_tokens", None)) if det2 is not None else None
    got = any(v is not None for v in (prompt, completion, total))
    return {"prompt": prompt, "completion": completion, "total": total,
            "cached": cached, "reasoning": reasoning,
            "source": "provider" if got else "none"}


def _endpoint_host(base_url: str) -> str:
    s = (base_url or "").strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    return s.split("/", 1)[0][:48]


def record_llm_call(
    *,
    provider: str,
    model: str,
    usage: Any = None,
    status: str = "ok",
    error_kind: str = "",
    http_status: Optional[int] = None,
    latency_ms: int = 0,
    attempt_no: int = 0,
    base_url: str = "",
    feature: Optional[str] = None,
    trace_id: str = "",
    call_group_id: str = "",
) -> None:
    """Record one call. **Never raises** - an accounting failure loses at most one row."""
    if os.environ.get("LLM_USAGE_DISABLED", "").strip() in ("1", "true", "yes"):
        return
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return
    try:
        import psycopg

        u = _parse_usage(usage)
        now = datetime.utcnow()
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
            conn.execute(_INSERT, (
                now, (now + BJ_OFFSET).date().isoformat(), _HOST,
                (feature or _feature.get())[:64], (provider or "")[:24], (model or "")[:64],
                _endpoint_host(base_url),
                "chat", (status or "ok")[:16], (error_kind or "")[:24], http_status,
                u["prompt"], u["completion"], u["total"], u["cached"], u["reasoning"],
                u["source"], 1, attempt_no, max(0, int(latency_ms)),
                "", "", (trace_id or "")[:64], (call_group_id or "")[:32],
            ))
    except Exception as e:  # noqa: BLE001 - sentiment monitoring batch runs must not be interrupted because accounting failed
        log.debug("sentinel llm usage record failed: %s", e)
