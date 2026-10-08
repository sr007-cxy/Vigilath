"""Unified model call exit. **All new code goes through here**; stop copying `_resolve_provider` + hand-built payloads.

Background: when the DeepSeek balance ran out on 2026-08-13, the repo had about 60 independent model call exits,
each "copy-pasted provider resolution + its own payload + discarded usage". This module consolidates
provider resolution, endpoint assembly, sending requests and accounting into one place.

Two levels, chosen by migration cost:

  · chat() / chat_json()   -- for new code. Handles provider resolution + payload + accounting.
  · post_and_record()      -- for migrating old exits. **Replaces only the requests.post line**;
                              payload / headers / exception semantics stay exactly the same, purely adding accounting.

There is one hard design discipline -- **this unifies accounting, not behavior**:
  · No "reasonable defaults": timeout / temperature / json_mode / max_tokens / retries are all
    passed explicitly. These parameters already differ per exit in the repo (60s~180s, 0.1~0.7); unifying them into one value
    would silently change the behavior of a dozen places.
  · No global default model. Historically the defaults were split (deepseek-chat in 6 places vs
    deepseek-v4-flash in 3); keep them split, with callers passing explicitly.
  · Known implicit differences are preserved as-is, not "fixed along the way", e.g. keyword_heat's OpenRouter branch
    has never passed response_format (the direct branch does) -- that is why the json_mode_openrouter parameter exists.
  Behavior changes go in a separate PR; do not mix them into the accounting migration.

Provider is not determined from base_url: DEEPSEEK_BASE_URL now points to the neolink aggregation gateway,
and guessing from the URL would always be wrong. Provider comes directly from this module's resolution; the egress domain is stored separately as endpoint_host.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

import requests

from geo.models.llm_usage import (
    STATUS_HTTP_ERROR, STATUS_NETWORK_ERROR, STATUS_OK, STATUS_PARSE_ERROR, STATUS_TRUNCATED,
)
from geo.services.llm_usage import record_llm_call

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_URL = f"{OPENROUTER_BASE_URL}/chat/completions"

# A model vendor going into arrears is a global operational failure and should not surface only inside one business error. Reuse the existing account pool
# Feishu/WeCom bots; a separate LLM_ALERT_WEBHOOK may split it into its own group later. Cool down in-process per provider+egress,
# so dozens of concurrent requests in one batch do not flood the group.
_LLM_ALERT_WEBHOOK = (
    os.environ.get("LLM_ALERT_WEBHOOK")
    or os.environ.get("ENGINE_POOL_ALERT_WEBHOOK")
    or ""
).strip()
_ACCOUNT_OVERDUE_ALERT_COOLDOWN_SEC = int(os.environ.get(
    "LLM_ACCOUNT_OVERDUE_ALERT_COOLDOWN_SEC", str(6 * 3600),
))
_account_overdue_alert_last: dict[str, float] = {}
_account_overdue_alert_lock = threading.Lock()


def _is_account_overdue_error(body: str) -> bool:
    """Only recognize arrears codes/messages explicitly returned by the vendor; ordinary 403s (permissions, bans) are not misreported as arrears."""
    text = (body or "").lower()
    return "accountoverdueerror" in text or "account has an overdue balance" in text


def _request_id_from_error(body: str) -> str:
    match = re.search(r"request\s*id\s*[:：]\s*([a-z0-9._-]{2,128})", body or "", re.I)
    return match.group(1) if match else "unknown"


def _send_llm_alert(text_msg: str) -> None:
    """Best-effort async group alert; an alert failure must not change the original exception semantics of the model call."""
    url = _LLM_ALERT_WEBHOOK
    if not url:
        log.warning("[llm-alert] LLM_ALERT_WEBHOOK/ENGINE_POOL_ALERT_WEBHOOK not configured: %s", text_msg)
        return

    def _send() -> None:
        try:
            if "qyapi.weixin" in url:
                payload = {"msgtype": "text", "text": {"content": text_msg}}
            else:
                payload = {"msg_type": "text", "content": {"text": text_msg}}
            r = requests.post(url, json=payload, timeout=8)
            if r.status_code >= 400:
                log.warning("[llm-alert] webhook HTTP %s: %s", r.status_code, r.text[:120])
        except Exception as e:  # noqa: BLE001
            log.warning("[llm-alert] webhook failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()


def _maybe_alert_account_overdue(*, provider: str, feature: str, url: str,
                                 http_status: int, body: str) -> None:
    if http_status < 400 or not _is_account_overdue_error(body):
        return
    endpoint_host = urlsplit(url).netloc or "unknown"
    key = f"{provider}:{endpoint_host}"
    now = time.monotonic()
    with _account_overdue_alert_lock:
        last = _account_overdue_alert_last.get(key)
        if last is not None and now - last < _ACCOUNT_OVERDUE_ALERT_COOLDOWN_SEC:
            return
        _account_overdue_alert_last[key] = now
    _send_llm_alert(
        "🚨 模型账户欠费：对外模型网关返回 AccountOverdueError，调用未进入模型推理。"
        f" provider={provider}，出口={endpoint_host}，HTTP={http_status}，"
        f"功能={feature or 'unknown'}，Request ID={_request_id_from_error(body)}。"
        "请立即检查上游账户账单/余额；"
        f"同一出口 {_ACCOUNT_OVERDUE_ALERT_COOLDOWN_SEC // 3600} 小时内不重复提醒。"
    )


class LlmUnavailable(RuntimeError):
    """No usable api key at all. Callers translate it per their own existing semantics (return None / raise 503 / ...)."""


@dataclass(frozen=True)
class Provider:
    name: str          # deepseek | openrouter
    url: str           # Full /chat/completions URL
    model: str
    api_key: str
    headers: dict


def resolve_provider(
    *,
    model: str,
    feature: str = "",
    openrouter_model: Optional[str] = None,
    on_missing: str = "raise",     # raise | none -- absorbs the four different "no key" semantics in the repo
) -> Optional[Provider]:
    """Pick a provider and build the endpoint. DeepSeek direct first, otherwise OpenRouter as fallback.

    model / openrouter_model must be passed explicitly -- see "no global default model" in the module docstring.
    feature goes into OpenRouter's X-Title (exits already fill that field with the feature name).
    """
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        return Provider(
            name="deepseek",
            url=f"{DEEPSEEK_BASE_URL}/chat/completions",
            model=model,
            api_key=ds_key,
            headers={"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"},
        )
    if or_key:
        om = openrouter_model or (model if "/" in model else f"deepseek/{model}")
        return Provider(
            name="openrouter",
            url=OPENROUTER_URL,
            model=om,
            api_key=or_key,
            headers={
                "Authorization": f"Bearer {or_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://www.vigilath.com",
                "X-Title": feature or "GEO",
            },
        )
    if on_missing == "none":
        return None
    raise LlmUnavailable("缺少 DEEPSEEK_API_KEY 或 OPENROUTER_API_KEY")


# ---------------------------------------------------------------------------
# For migrating old exits: replace only the requests.post line
# ---------------------------------------------------------------------------
def post_and_record(
    url: str,
    payload: dict,
    headers: dict,
    *,
    provider: str,
    feature: str,
    timeout: int,
    call_kind: str = "chat",
    model: Optional[str] = None,
    attempt_no: int = 0,
    call_group_id: str = "",
    session: Any = None,
) -> requests.Response:
    """Send one request and record one entry, returning the raw Response -- **the caller's downstream logic needs no change at all**.

    Exceptions are raised as-is (network exceptions record a network_error row first, then raise); HTTP status codes are not judged here
    and are left to the caller's existing semantics.

    Accounting metric definition: status is judged by the HTTP result. **Truncation (finish_reason=length) and response body parse failure,
    the two "200 but business failure, still billed by the provider" cases, cannot be recognized here** -- they are recorded as ok.
    Exits that care about the distinction (content_generator._post_chat) call record_llm_call themselves for finer records.
    """
    mdl = model or str(payload.get("model") or "")
    t0 = time.perf_counter()
    poster = session.post if session is not None else requests.post
    try:
        r = poster(url, json=payload, headers=headers, timeout=timeout)
    except Exception as e:  # noqa: BLE001 -- record then raise, without changing the exception the caller sees
        name = type(e).__name__.lower()
        record_llm_call(
            provider=provider, model=mdl, feature=feature, call_kind=call_kind,
            status=STATUS_NETWORK_ERROR,
            error_kind="timeout" if "timeout" in name else "network",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            attempt_no=attempt_no, endpoint=url, call_group_id=call_group_id,
            extra={"error": str(e)[:200]},
        )
        raise

    ok = r.status_code < 400
    body: Any = None
    if ok:
        try:
            body = r.json()
        except Exception:  # noqa: BLE001 -- missing usage does not stop recording a call count
            body = None
    record_llm_call(
        provider=provider, model=mdl, feature=feature, call_kind=call_kind,
        usage=body if isinstance(body, dict) else None,
        status=STATUS_OK if ok else STATUS_HTTP_ERROR,
        error_kind="" if ok else f"http_{r.status_code}",
        http_status=r.status_code,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        attempt_no=attempt_no, endpoint=url, call_group_id=call_group_id,
        extra=None if ok else {"error": r.text[:200]},
    )
    if not ok:
        _maybe_alert_account_overdue(
            provider=provider, feature=feature, url=url,
            http_status=r.status_code, body=r.text,
        )
    return r


# ---------------------------------------------------------------------------
# For new code: unified chat exit
# ---------------------------------------------------------------------------
_RETRY_STATUS_DEFAULT = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})


class LlmError(RuntimeError):
    """Error from the unified exit. kind: http_4xx|http_5xx|network|timeout|truncated|parse"""

    def __init__(self, message: str, *, kind: str, http_status: Optional[int] = None):
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status


def chat(
    *,
    feature: str,
    model: str,
    system: Optional[str] = None,
    user: Optional[str] = None,
    messages: Optional[list[dict]] = None,
    temperature: float,
    timeout: int,
    max_tokens: Optional[int] = None,
    json_mode: bool = False,
    json_mode_openrouter: Optional[bool] = None,
    openrouter_model: Optional[str] = None,
    retries: int = 0,
    retry_status: frozenset = _RETRY_STATUS_DEFAULT,
    retry_backoff_sec: float = 2.0,
    raise_on_truncated: bool = False,
    call_kind: str = "chat",
    on_missing: str = "raise",
    thinking: Optional[str] = None,
) -> str:
    """Send one chat and return message.content. All parameters explicit -- see the discipline in the module docstring.

    json_mode_openrouter: whether to also pass response_format via OpenRouter. Defaults to json_mode;
    passing False explicitly preserves the historical difference "direct wants JSON, OpenRouter does not" (some models
    return 400 on response_format).

    thinking: "disabled" / "enabled", only sent as `{"thinking": {"type": ...}}` on the direct deepseek channel
    (the neolink gateway was verified to accept this field on 2026-09-14; `reasoning_effort` only accepts low/medium/high/xhigh,
    no none). None = not sent, gateway/model default. A reasoning model's thinking tokens count toward max_tokens;
    exits that need no reasoning should turn it off: the same 8-term JSON dropped from 2.8-13s to 1.8s, with no more "thinking
    ate the budget, empty body" truncation. Not sent on the OpenRouter branch -- models differ in how they treat this field.
    """
    p = resolve_provider(model=model, feature=feature,
                         openrouter_model=openrouter_model, on_missing=on_missing)
    if p is None:
        raise LlmUnavailable("no api key")

    msgs = messages
    if msgs is None:
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": user or ""}]

    want_json = json_mode
    if p.name == "openrouter" and json_mode_openrouter is not None:
        want_json = json_mode_openrouter

    payload: dict = {"model": p.model, "messages": msgs, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if want_json:
        payload["response_format"] = {"type": "json_object"}
    if thinking and p.name == "deepseek":
        payload["thinking"] = {"type": thinking}

    group = os.urandom(8).hex()
    last: Optional[Exception] = None
    for attempt in range(max(0, retries) + 1):
        if attempt:
            time.sleep(retry_backoff_sec * (2 ** (attempt - 1)))
        try:
            r = post_and_record(p.url, payload, p.headers, provider=p.name, feature=feature,
                                timeout=timeout, call_kind=call_kind, model=p.model,
                                attempt_no=attempt, call_group_id=group)
        except Exception as e:  # noqa: BLE001
            last = LlmError(f"{p.name} 请求失败: {type(e).__name__}: {str(e)[:160]}",
                            kind="timeout" if "timeout" in type(e).__name__.lower() else "network")
            continue
        if r.status_code in retry_status:
            last = LlmError(f"{p.name} {r.status_code}: {r.text[:200]}",
                            kind=f"http_{r.status_code}", http_status=r.status_code)
            continue
        if r.status_code >= 400:
            raise LlmError(f"{p.name} {r.status_code}: {r.text[:200]}",
                           kind=f"http_{r.status_code}", http_status=r.status_code)
        try:
            data = r.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except Exception as e:  # noqa: BLE001
            # 200 but wrong shape -- the provider has billed; correct the ok row above to parse_error
            record_llm_call(provider=p.name, model=p.model, feature=feature,
                            call_kind=call_kind, status=STATUS_PARSE_ERROR, error_kind="parse",
                            http_status=r.status_code, endpoint=p.url, call_group_id=group,
                            requests_count=0,   # Count already recorded in post_and_record; this row only marks status
                            extra={"error": str(e)[:200], "correction_of": group})
            raise LlmError(f"unexpected {p.name} response shape: {e}", kind="parse")
        if raise_on_truncated and (choice.get("finish_reason") or "") == "length":
            raise LlmError(f"{p.name} 输出被截断(finish_reason=length,max_tokens={max_tokens})"
                           " — 重试无用,要缩短长度或加大输出预算", kind="truncated")
        return content
    raise last or LlmError(f"{p.name} 请求失败", kind="network")


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def parse_json_loose(text: str) -> dict:
    """JSON parsing tolerant of ```json fences / surrounding chatter.

    content_generator / solution_generator / query_expander in the repo each have a same-named private implementation;
    new code uses this one; the old ones converge when each migrates to the unified exit (do not touch them in the accounting migration).
    """
    s = (text or "").strip()
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        pass
    m = _JSON_FENCE.search(s)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            pass
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(s[i:j + 1])
        except Exception:  # noqa: BLE001
            pass
    raise ValueError(f"无法解析为 JSON:{s[:160]}")


def chat_json(**kwargs: Any) -> dict:
    """chat() + JSON parsing. json_mode on by default."""
    kwargs.setdefault("json_mode", True)
    return parse_json_loose(chat(**kwargs))
