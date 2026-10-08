"""Model call accounting core -- context + usage parsing + persistence. **This module never sends HTTP requests.**

Background: see the docstring of geo/models/llm_usage.py (when the DeepSeek balance ran out on 2026-08-13 we found
the app layer had zero records of LLM calls).

Why the contract lives at the usage layer rather than the transport layer: the repo has three call shapes --
  · raw HTTP (a dozen-plus exits in geo/services/, telemetry, geo_checker)
  · openai SDK (sentinel-service/llm_client.py, reads resp.usage)
  · pydantic-ai (agents/geo/, reads result.usage(), already aggregating a whole round of tool round trips)
A unified HTTP client cannot wrap the latter two, but record_llm_call() can be fed by all three.
The unified chat exit is in geo/services/llm_gateway.py (a convenience layer on top of this module).

**Zero heavy dependencies**: only stdlib + SQLAlchemy. geo-agent runs in a separate venv (pydantic>=2.11),
the main backend is FastAPI 0.104 + pydantic 2.4, and both must be able to import it; geo_checker is a zero-dependency CLI
that soft-imports this module and silently degrades if unavailable. Do not import fastapi / pydantic / business modules here.

Three hard constraints (read before changing code):
  1. record_llm_call() **never raises**. An accounting failure at worst loses one row, never affecting business.
  2. **Never reuse the caller's db session** -- use this module's own session. Otherwise an accounting flush failure
     would roll back the business transaction with it.
  3. The writer uses a **separate engine** (pool_size=2, max_overflow=0), not occupying the business connection pool.
     prod PG max_connections=100, and geo-agent's 8 workers already consume 60+.
     Do not change it to "reuse SessionLocal for simplicity" -- it is a guardrail, not redundancy.

Usage (the simplest form for a raw HTTP exit):

    from geo.services.llm_usage import track

    with track("content_gen.article", "deepseek", model, endpoint=url) as t:
        r = requests.post(url, json=payload, headers=headers, timeout=90)
        t.http_status = r.status_code
        t.usage = r.json()          # <- the only new line

Context (set one layer upstream; exits need no signature change):

    from geo.services.llm_usage import usage_ctx
    with usage_ctx(topic_id=t.id, feature="content_gen", trace_id=uuid4().hex):
        ...
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

# Beijing date metric definition matches geo/api/ai_telemetry_reports.py:341 _bj_date (UTC+8).
# That module is not imported here: it is the API layer and would pull in a pile of business dependencies, conflicting with this module's "zero heavy dependencies".
# Change both places together when changing the metric definition.
BJ_OFFSET = timedelta(hours=8)

# Same source as _SECRET_RE in agents/sales_agent/runtime_log.py:20 (that one redacts JSONL logs)
_SECRET_RE = re.compile(
    r"(bearer\s+)[a-z0-9._\-]+|(api[_-]?key\s*[=:]\s*)[^\s,;]+|(sk-[a-z0-9_\-]{12,})|(tokenhub_[a-z0-9_\-]{12,})",
    re.IGNORECASE,
)

_HOST = socket.gethostname()[:32]

# Escape hatch to turn off accounting (for troubleshooting; normally never set)
_DISABLED = os.environ.get("LLM_USAGE_DISABLED", "").strip() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Context -- contextvar, zero signature changes at exits
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LlmCallCtx:
    feature: str = ""          # Feature scenario; empty -> persisted as "unknown" (leak metric)
    service: str = "backend"   # backend|telemetry|sentinel|agent|checker
    user_id: Optional[int] = None
    topic_id: Optional[int] = None
    ref_type: str = ""
    ref_id: str = ""
    trace_id: str = ""         # Ties N calls of one business action together


_EMPTY_CTX = LlmCallCtx()
_ctx: contextvars.ContextVar[LlmCallCtx] = contextvars.ContextVar("llm_usage_ctx", default=_EMPTY_CTX)


def current_ctx() -> LlmCallCtx:
    return _ctx.get()


@contextmanager
def usage_ctx(**fields: Any) -> Iterator[LlmCallCtx]:
    """Set the call context. **Nestable and inherited** -- inner layers only write the fields they care about, the rest inherited from outer layers.

    Typical usage: _run_generation sets topic_id/user_id/trace_id, _generate_one only sets
    feature and ref_id.
    """
    clean = {k: v for k, v in fields.items() if v is not None and k in LlmCallCtx.__dataclass_fields__}
    new = replace(_ctx.get(), **clean) if clean else _ctx.get()
    token = _ctx.set(new)
    try:
        yield new
    finally:
        _ctx.reset(token)


def spawn_thread(target, *args, **kwargs) -> threading.Thread:
    """Replacement for threading.Thread -- automatically carries the current contextvar snapshot into the new thread.

    Bare threading.Thread does not inherit contextvars; without this, feature/topic_id in the new thread
    are all empty, wrecking half of cost attribution. daemon stays consistent with the original call site (True).
    """
    ctx = contextvars.copy_context()
    return threading.Thread(target=lambda: ctx.run(target, *args, **kwargs), daemon=True)


def submit(pool, fn, *args, **kwargs):
    """Replacement for ThreadPoolExecutor.submit, also carrying the contextvar snapshot."""
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# usage parsing -- accepts dict / openai CompletionUsage / pydantic-ai RunUsage
# ---------------------------------------------------------------------------
@dataclass
class UsageTokens:
    prompt: Optional[int] = None
    completion: Optional[int] = None
    total: Optional[int] = None
    cached: Optional[int] = None
    reasoning: Optional[int] = None
    requests: int = 1
    source: str = "none"          # provider | none
    unknown: Optional[dict] = None  # Unrecognized raw fields, put into extra_json as-is, no guessing


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return None


def _usage_dict(obj: Any) -> Optional[dict]:
    """Pull the usage dict out of any shape. Feeding the whole response json works, so does feeding usage itself."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        # The whole /chat/completions response body
        if isinstance(obj.get("usage"), dict):
            return obj["usage"]
        # DashScope native: {"output": {...}, "usage": {...}} already handled above;
        # when usage itself is fed directly: recognize a few marker fields
        keys = set(obj.keys())
        if keys & {"prompt_tokens", "completion_tokens", "total_tokens",
                   "input_tokens", "output_tokens", "cached_tokens"}:
            return obj
        return None
    # openai SDK CompletionUsage / pydantic-ai RunUsage -- both are objects with attributes
    d: dict = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens",
                 "input_tokens", "output_tokens", "cache_read_tokens",
                 "cached_tokens", "requests",
                 "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        v = getattr(obj, name, None)
        if v is not None:
            d[name] = v
    for name in ("prompt_tokens_details", "completion_tokens_details", "details"):
        v = getattr(obj, name, None)
        if v is not None:
            d[name] = v
    return d or None


def _detail_get(container: Any, key: str) -> Optional[int]:
    if container is None:
        return None
    if isinstance(container, dict):
        return _as_int(container.get(key))
    return _as_int(getattr(container, key, None))


def parse_usage(obj: Any) -> UsageTokens:
    """Normalize usage across providers. **Unrecognized fields stay in unknown as-is, no guessing.**

    Field differences (measured metric definitions; do not change from memory):
      · DeepSeek direct  prompt_tokens / completion_tokens
                       + prompt_cache_hit_tokens / prompt_cache_miss_tokens
      · OpenAI/OpenRouter  + prompt_tokens_details.cached_tokens
                           + completion_tokens_details.reasoning_tokens
      · GLM / DashScope native / some Tongyi  input_tokens / output_tokens
      · pydantic-ai RunUsage  input_tokens / output_tokens / requests (already aggregated over the whole round)
      · Doubao ARK Bot  may not return usage at all -> all None, requests still counts as 1
    """
    u = _usage_dict(obj)
    if not u:
        return UsageTokens()

    prompt = _as_int(u.get("prompt_tokens"))
    if prompt is None:
        prompt = _as_int(u.get("input_tokens"))
    completion = _as_int(u.get("completion_tokens"))
    if completion is None:
        completion = _as_int(u.get("output_tokens"))
    total = _as_int(u.get("total_tokens"))
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)

    cached = _as_int(u.get("prompt_cache_hit_tokens"))
    if cached is None:
        cached = _as_int(u.get("cached_tokens"))
    if cached is None:
        cached = _as_int(u.get("cache_read_tokens"))
    if cached is None:
        cached = _detail_get(u.get("prompt_tokens_details"), "cached_tokens")

    reasoning = _detail_get(u.get("completion_tokens_details"), "reasoning_tokens")
    if reasoning is None:
        reasoning = _as_int(u.get("reasoning_tokens"))

    requests = _as_int(u.get("requests")) or 1

    known = {
        "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cached_tokens", "cache_read_tokens",
        "prompt_tokens_details", "completion_tokens_details", "reasoning_tokens", "requests",
    }
    unknown = {k: v for k, v in u.items() if k not in known}

    got = any(v is not None for v in (prompt, completion, total))
    return UsageTokens(
        prompt=prompt, completion=completion, total=total,
        cached=cached, reasoning=reasoning, requests=max(1, requests),
        source="provider" if got else "none",
        unknown=unknown or None,
    )


# ---------------------------------------------------------------------------
# Persistence -- separate engine + separate session, never raises
# ---------------------------------------------------------------------------
_writer_lock = threading.Lock()
_writer_session_factory = None  # type: ignore[var-annotated]


def _session_factory():
    """Session factory dedicated to accounting. Separate engine, never touching the business connection pool (see constraint 3 in the module docstring)."""
    global _writer_session_factory
    if _writer_session_factory is not None:
        return _writer_session_factory
    with _writer_lock:
        if _writer_session_factory is not None:
            return _writer_session_factory
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from geo.database import settings

        url = settings.DATABASE_URL
        if "sqlite" in url:
            # dev: SQLite already has WAL enabled (the pragma listener in geo/database.py applies to all Engines)
            eng = create_engine(url, connect_args={"check_same_thread": False})
        else:
            eng = create_engine(url, pool_size=2, max_overflow=0,
                                pool_pre_ping=True, pool_recycle=1800)
        _writer_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=eng)
        return _writer_session_factory


def bj_date(dt: datetime) -> str:
    return (dt + BJ_OFFSET).date().isoformat()


def redact(text: Any, limit: int = 200) -> str:
    s = str(text or "")[:limit]
    return _SECRET_RE.sub(lambda m: f"{m.group(1) or m.group(2) or ''}<redacted>", s)


def _endpoint_host(endpoint: str) -> str:
    """Take the host from base_url / a full url.

    provider cannot be guessed from base_url -- DEEPSEEK_BASE_URL now points to the neolink aggregation gateway,
    so guessing is always wrong. This column is only "which channel it went out through", distinguishing official direct / neolink / openrouter during reconciliation.
    """
    s = (endpoint or "").strip()
    if not s:
        return ""
    if "://" in s:
        s = s.split("://", 1)[1]
    return s.split("/", 1)[0][:48]


def record_llm_call(
    *,
    provider: str,
    model: str = "",
    feature: Optional[str] = None,
    service: Optional[str] = None,
    call_kind: str = "chat",
    usage: Any = None,
    status: str = "ok",
    error_kind: str = "",
    http_status: Optional[int] = None,
    latency_ms: int = 0,
    requests_count: Optional[int] = None,
    attempt_no: int = 0,
    endpoint: str = "",
    user_id: Optional[int] = None,
    topic_id: Optional[int] = None,
    ref_type: str = "",
    ref_id: str = "",
    trace_id: str = "",
    call_group_id: str = "",
    extra: Optional[dict] = None,
) -> None:
    """Record one call. **Never raises** -- an accounting failure at worst loses one row.

    Only call **after the HTTP request was actually sent**. Paths that degrade without a key and never send a request must not write a row,
    otherwise the failure rate is completely distorted.

    usage can be fed r.json() / resp.usage / result.usage() directly; parse_usage normalizes it.
    """
    # The accounting switch only governs persistence; the arrears alert is not bound by it -- turning off accounting should not also turn off alerts.
    try:
        _maybe_alert_payment_required(
            provider=provider, endpoint=endpoint, http_status=http_status,
            feature=feature or _ctx.get().feature or "", model=model,
        )
    except Exception:  # noqa: BLE001
        pass
    if _DISABLED:
        return
    try:
        ctx = _ctx.get()
        u = parse_usage(usage)
        now = datetime.utcnow()

        extra_payload: dict = dict(extra or {})
        if u.unknown:
            extra_payload["usage_unknown"] = u.unknown
        extra_json = None
        if extra_payload:
            try:
                extra_json = redact(json.dumps(extra_payload, ensure_ascii=False, default=str), 2000)
            except Exception:  # noqa: BLE001
                extra_json = None

        from geo.models.llm_usage import FEATURE_UNKNOWN, LlmCallLogORM

        row = LlmCallLogORM(
            created_at=now,
            bj_date=bj_date(now),
            service=(service or ctx.service or "backend")[:16],
            host=_HOST,
            feature=(feature or ctx.feature or FEATURE_UNKNOWN)[:64],
            provider=(provider or "")[:24],
            model=(model or "")[:64],
            endpoint_host=_endpoint_host(endpoint),
            call_kind=(call_kind or "chat")[:12],
            status=(status or "ok")[:16],
            error_kind=(error_kind or "")[:24],
            http_status=http_status,
            prompt_tokens=u.prompt,
            completion_tokens=u.completion,
            total_tokens=u.total,
            cached_tokens=u.cached,
            reasoning_tokens=u.reasoning,
            token_source=u.source,
            # If passed explicitly, use it as-is (**0 allowed** -- a correction row only changes status without counting again,
            # otherwise the same request would be counted twice); only when not passed use the count from usage, floor 1
            requests_count=(max(0, requests_count) if requests_count is not None
                            else max(1, u.requests)),
            attempt_no=attempt_no,
            latency_ms=max(0, int(latency_ms)),
            user_id=user_id if user_id is not None else ctx.user_id,
            topic_id=topic_id if topic_id is not None else ctx.topic_id,
            ref_type=(ref_type or ctx.ref_type or "")[:16],
            ref_id=str(ref_id or ctx.ref_id or "")[:64],
            trace_id=(trace_id or ctx.trace_id or "")[:64],
            call_group_id=(call_group_id or "")[:32],
            extra_json=extra_json,
        )

        db = _session_factory()()
        try:
            db.add(row)
            db.commit()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 -- accounting failures never affect business
        log.debug("llm usage record failed: %s", e)


# ---------------------------------------------------------------------------
# Arrears alert -- when the gateway returns 402, shout in the Feishu group
# ---------------------------------------------------------------------------
# 2026-09-21 13:52-14:45 (Beijing) the neolink gateway returned 402 for all model calls, all four query expansion scenarios failed,
# and nobody knew -- the arrears alert in llm_gateway only recognized the "AccountOverdueError" message and only covered
# exits going through the gateway; exits like query_expander that use httpx + track() directly never pass through it.
# Hooked onto record_llm_call here: **every** accounted exit passes through, and 402 means "Payment Required",
# no message guessing needed. Same provider+egress cools down for 1 hour, so a batch of concurrent requests does not flood.
# The webhook uses the same env as llm_gateway (LLM_ALERT_WEBHOOK, a Feishu bot configured in production);
# urllib is used here instead of requests -- this module must be soft-importable by the zero-dependency geo_checker.
_PAY_ALERT_WEBHOOK = (
    os.environ.get("LLM_ALERT_WEBHOOK")
    or os.environ.get("ENGINE_POOL_ALERT_WEBHOOK")
    or ""
).strip()
_PAY_ALERT_COOLDOWN_SEC = int(os.environ.get("LLM_PAYMENT_ALERT_COOLDOWN_SEC", str(3600)))
_pay_alert_last: dict[str, float] = {}
_pay_alert_lock = threading.Lock()


def _post_webhook(url: str, text_msg: str) -> None:
    import urllib.request

    if "qyapi.weixin" in url:                  # WeCom bot
        payload = {"msgtype": "text", "text": {"content": text_msg}}
    else:                                      # Feishu bot (default)
        payload = {"msg_type": "text", "content": {"text": text_msg}}
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        if r.status >= 400:
            log.warning("[llm-pay-alert] webhook HTTP %s", r.status)


def _maybe_alert_payment_required(*, provider: str, endpoint: str,
                                  http_status: Optional[int], feature: str,
                                  model: str) -> None:
    """Gateway returns 402 -> Feishu group alert (no repeats within cooldown). Never raises."""
    if http_status != 402:
        return
    host = _endpoint_host(endpoint) or "unknown"
    key = f"{provider}:{host}"
    now = time.monotonic()
    with _pay_alert_lock:
        last = _pay_alert_last.get(key)
        if last is not None and now - last < _PAY_ALERT_COOLDOWN_SEC:
            return
        _pay_alert_last[key] = now
    msg = (
        f"🚨 模型网关欠费:{host} 对 {provider}/{model or '?'} 返回 HTTP 402(Payment Required),"
        f"调用没有进入推理。触发功能={feature or 'unknown'},主机={_HOST}。"
        "扩词 / 诊断 / 生成稿件都会跟着失败,请立即给网关账户充值。"
        f"同一出口 {_PAY_ALERT_COOLDOWN_SEC // 60} 分钟内不重复提醒。"
    )
    if not _PAY_ALERT_WEBHOOK:
        log.warning("[llm-pay-alert] LLM_ALERT_WEBHOOK not configured, logging only: %s", msg)
        return

    def _send() -> None:
        try:
            _post_webhook(_PAY_ALERT_WEBHOOK, msg)
        except Exception as e:  # noqa: BLE001
            log.warning("[llm-pay-alert] webhook failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()


# ---------------------------------------------------------------------------
# track -- the simplest form for exits
# ---------------------------------------------------------------------------
class _Track:
    """Writable fields inside `with track(...) as t`. Just feed the response body to t.usage."""

    __slots__ = ("usage", "http_status", "status", "error_kind", "model",
                 "provider", "requests_count", "extra", "skip")

    def __init__(self, provider: str, model: str):
        self.usage: Any = None
        self.http_status: Optional[int] = None
        self.status: Optional[str] = None      # If unset, determined automatically by whether an exception occurred
        self.error_kind: str = ""
        self.provider = provider
        self.model = model
        self.requests_count: Optional[int] = None
        self.extra: Optional[dict] = None
        self.skip = False                       # Set True to skip recording this call (e.g. no request was actually sent)


def _error_kind_of(exc: BaseException, http_status: Optional[int]) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "connection" in name or "connect" in name:
        return "network"
    if http_status:
        return f"http_{http_status}"
    return name[:24]


@contextmanager
def track(
    feature: str,
    provider: str,
    model: str = "",
    *,
    call_kind: str = "chat",
    endpoint: str = "",
    service: Optional[str] = None,
    attempt_no: int = 0,
    call_group_id: str = "",
    **ctx_fields: Any,
) -> Iterator[_Track]:
    """Wrap one provider request and record it automatically. Exceptions are re-raised as-is (this context manager never swallows business exceptions).

    Status determination:
      · normal exit -> t.status or ok (automatically http_error when http_status >= 400)
      · exception   -> error (error_kind inferred from exception type / http_status)
    """
    t = _Track(provider, model)
    t0 = time.perf_counter()
    try:
        yield t
    except BaseException as exc:  # noqa: BLE001
        try:
            if not t.skip and not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                from geo.models.llm_usage import STATUS_HTTP_ERROR, STATUS_NETWORK_ERROR

                hs = t.http_status
                status = STATUS_HTTP_ERROR if (hs and hs >= 400) else STATUS_NETWORK_ERROR
                record_llm_call(
                    provider=t.provider, model=t.model, feature=feature, service=service,
                    call_kind=call_kind, usage=t.usage,
                    status=t.status or status,
                    error_kind=t.error_kind or _error_kind_of(exc, hs),
                    http_status=hs,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    requests_count=t.requests_count, attempt_no=attempt_no,
                    endpoint=endpoint, call_group_id=call_group_id,
                    extra={**(t.extra or {}), "error": redact(exc)},
                    **ctx_fields,
                )
        except Exception:  # noqa: BLE001 -- an accounting failure must never mask the business exception
            pass
        raise
    else:
        try:
            if not t.skip:
                from geo.models.llm_usage import STATUS_HTTP_ERROR, STATUS_OK

                hs = t.http_status
                auto = STATUS_HTTP_ERROR if (hs and hs >= 400) else STATUS_OK
                record_llm_call(
                    provider=t.provider, model=t.model, feature=feature, service=service,
                    call_kind=call_kind, usage=t.usage,
                    status=t.status or auto,
                    error_kind=t.error_kind or ("" if auto == STATUS_OK else f"http_{hs}"),
                    http_status=hs,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    requests_count=t.requests_count, attempt_no=attempt_no,
                    endpoint=endpoint, call_group_id=call_group_id,
                    extra=t.extra,
                    **ctx_fields,
                )
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# selftest —— python -m geo.services.llm_usage --selftest
# ---------------------------------------------------------------------------
def _selftest(n: int = 1000) -> int:
    from geo.models.llm_usage import LlmCallLogORM

    marker = f"selftest-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    with usage_ctx(feature="selftest", trace_id=marker, service="backend"):
        for i in range(n):
            record_llm_call(
                provider="selftest", model="m", endpoint="https://api.deepseek.com/v1",
                usage={"usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                 "prompt_cache_hit_tokens": 3}},
                latency_ms=i % 100,
            )
    db = _session_factory()()
    try:
        got = db.query(LlmCallLogORM).filter(LlmCallLogORM.trace_id == marker).count()
    finally:
        db.close()
    print(f"selftest: wrote {n}, read back {got}  → {'OK' if got == n else 'MISMATCH'}")
    print("清理:DELETE FROM llm_call_log WHERE trace_id = '%s';" % marker)
    return 0 if got == n else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    if "--selftest" in sys.argv:
        idx = sys.argv.index("--selftest")
        cnt = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 and sys.argv[idx + 1].isdigit() else 1000
        raise SystemExit(_selftest(cnt))
    print(__doc__)
