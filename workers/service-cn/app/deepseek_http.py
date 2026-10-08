"""
[2026-09-20 rollback note] develop once switched to a curl_cffi "disguised client" impersonating Chrome (#15, 2d6a96c3):
the test host ran it for 5 days and 10 accounts were banned for 68-215 hours (escalating to 7-9 days); with the same proxies and accounts,
other hosts on the old httpx client had not a single real mute. So the whole file was reverted to the release/cn version, keeping only
chain-of-thought parsing (_parse_stream_full, thinking off by default) and user_agent parameter compatibility.
DeepSeek pure HTTP engine - no browser, calls chat.deepseek.com's internal API directly.

Why it works: the deepseek web app is just a Bearer-token HTTP API. Flow:
  1. Get the Bearer token from the login session (storage_state.localStorage.userToken);
  2. POST /chat_session/create to create a session;
  3. POST /chat/create_pow_challenge to get the PoW challenge;
  4. Compute the PoW with deepseek's own wasm (sha3_wasm.wasm) - the algorithm lives entirely in the wasm,
     wasmtime calls wasm_solve directly (wasm-bindgen convention); if they change the algorithm, just re-download the wasm;
  5. POST /chat/completion with the X-Ds-Pow-Response header -> streams back the answer + web citations.

Results are **identical** to the web UI (same internal API), but in seconds, highly concurrent, with no cloakbrowser/Xvfb/DOM.
The account pool is still used to get tokens (check-out/check-in done by the caller); this module only handles "one query".
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

_BASE = "https://chat.deepseek.com/api/v0"
_WASM_PATH = os.path.join(os.path.dirname(__file__), "sha3_wasm.wasm")

# ── wasm PoW solver (in-process singleton) ──────────────────────────────────
_wasm = None


def _load_wasm():
    """Lazily load deepseek's sha3 wasm, returning (store, exports)."""
    global _wasm
    if _wasm is not None:
        return _wasm
    import wasmtime

    store = wasmtime.Store()
    module = wasmtime.Module.from_file(store.engine, _WASM_PATH)
    inst = wasmtime.Instance(store, module, [])
    ex = inst.exports(store)
    _wasm = (store, ex)
    return _wasm


def solve_pow(challenge: dict) -> Optional[int]:
    """Compute the PoW with deepseek's wasm, returning the answer (nonce); None on failure.

    challenge: {algorithm, challenge, salt, signature, difficulty, expire_at, target_path}
    Calling convention (wasm-bindgen): wasm_solve(retptr, ch_ptr, ch_len, prefix_ptr, prefix_len, difficulty_f64),
    result written at retptr: status(i32) + value(f64); prefix = "{salt}_{expire_at}_".
    """
    store, ex = _load_wasm()
    mem = ex["memory"]
    malloc = ex["__wbindgen_export_0"]
    add_sp = ex["__wbindgen_add_to_stack_pointer"]
    wasm_solve = ex["wasm_solve"]

    def w_str(s: str):
        b = s.encode("utf-8")
        ptr = malloc(store, len(b), 1)
        mem.write(store, b, ptr)
        return ptr, len(b)

    prefix = f"{challenge['salt']}_{challenge['expire_at']}_"
    retptr = add_sp(store, -16)
    cptr, clen = w_str(challenge["challenge"])
    pptr, plen = w_str(prefix)
    wasm_solve(store, retptr, cptr, clen, pptr, plen, float(challenge["difficulty"]))
    status = struct.unpack("<i", mem.read(store, retptr, retptr + 4))[0]
    value = struct.unpack("<d", mem.read(store, retptr + 8, retptr + 16))[0]
    add_sp(store, 16)
    return int(value) if status else None


def _make_pow_header(challenge: dict) -> Optional[str]:
    ans = solve_pow(challenge)
    if ans is None:
        return None
    obj = {
        "algorithm": challenge["algorithm"],
        "challenge": challenge["challenge"],
        "salt": challenge["salt"],
        "answer": ans,
        "signature": challenge["signature"],
        "target_path": challenge["target_path"],
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()


# ── Token extraction ───────────────────────────────────────────────────
def extract_token(storage_state: dict) -> Optional[str]:
    """Get the Bearer token from the login session's localStorage.userToken."""
    for o in (storage_state or {}).get("origins", []):
        ls = o.get("localStorage", [])
        if isinstance(ls, dict):
            ls = [{"name": k, "value": v} for k, v in ls.items()]
        for it in ls:
            if it.get("name") == "userToken":
                raw = it.get("value")
                try:
                    return json.loads(raw).get("value")
                except Exception:  # noqa: BLE001
                    return raw
    return None


# ── Streaming response parsing ─────────────────────────────────────────────────
def _parse_stream(lines) -> tuple[str, list]:
    text, cites, _ = _parse_stream_full(lines)
    return text, cites


def _parse_stream_full(lines) -> tuple[str, list, str]:
    """Parse deepseek's path-based incremental stream:
      {"p":"response/content","o":"APPEND","v":"..."} + subsequent bare {"v":"..."} continue the answer;
      {"p":"response/thinking_content","o":"APPEND","v":"..."} is the "deep thinking" part
      (only with thinking_enabled=true; same continuation rules as content, distinguished by the current pointer);
      {"p":"response/search_results","v":[{url,title,snippet}...]} are citations.
    Returns (answer, citations, reasoning).
    """
    text = ""
    thinking = ""
    citations: list = []
    seen_urls: set = set()
    current = None
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            ev = json.loads(payload)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(ev, dict):
            continue
        if "p" in ev:
            current = ev["p"]
        v = ev.get("v")
        # Citations: accumulate + dedupe by url (deepseek may send several search_results events incrementally;
        # the earlier `citations = v` overwrite dropped earlier batches -> the citation count shrank to the last batch)
        if ev.get("p") == "response/search_results" and isinstance(v, list):
            for c in v:
                if isinstance(c, dict):
                    u = c.get("url", "")
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        citations.append(c)
        elif current == "response/content" and isinstance(v, str):
            text += v
        elif current and "thinking" in current and isinstance(v, str):
            thinking += v
    cites = []
    for i, c in enumerate(citations):
        url = c.get("url", "")
        try:
            domain = urlparse(url).netloc
        except Exception:  # noqa: BLE001
            domain = ""
        cites.append({
            "url": url,
            "domain": domain,
            "title": c.get("title", ""),
            "snippet": c.get("snippet", ""),
            "position": i + 1,
        })
    return text.strip(), cites, thinking.strip()


def _biz_data(resp) -> Optional[dict]:
    """Safely get data.biz_data from a deepseek API response; under risk control/rate limiting data may be null -> return None."""
    try:
        d = (resp.json() or {}).get("data")
    except Exception:  # noqa: BLE001
        return None
    bd = d.get("biz_data") if isinstance(d, dict) else None
    return bd if isinstance(bd, dict) else None


def _auth_failed(resp) -> bool:
    """Token invalidation check. **Cannot rely only on 401/403** - for an invalid token deepseek returns
    HTTP 200 + {"code":40003,"msg":"Authorization Failed (invalid token)","data":null}.

    Checking only the status code, it falls into the data=null branch below and is judged rate_limited: a kicked account treated as rate-limited,
    so it is not quarantined, does not trigger re-authorization, stays in the pool being dispatched, and wastes a round every time it is claimed. In the 2026-09-02 pool probe,
    7 of 40 deepseek accounts had been dead like this for weeks unnoticed (21/24/25/26/27/28/29).
    """
    if resp.status_code in (401, 403):
        return True
    try:
        body = resp.json() or {}
    except Exception:  # noqa: BLE001
        return False
    code, msg = body.get("code"), (body.get("msg") or "").lower()
    return code == 40003 or "authorization failed" in msg or "invalid token" in msg


# ── Session reuse ─────────────────────────────────────────────────────
# One account asking 60 questions a day creates 60 "one-message" conversations; no real person uses it like that (each account already has
# 300-500 in the DB). Instead ask DEEPSEEK_SESSION_REUSE questions (default 6) in one session before opening a new one;
# each question is sent from the root with parent_message_id=None, a **parallel branch** in the same session, so the model cannot see earlier questions
# and answers do not bleed (hit detection is not polluted by the previous question's brand). Sessions are tracked per token in-process and discarded on account switch / login loss /
# mute. Set DEEPSEEK_SESSION_REUSE=1 to go back to "one session per question".
_SESSION_REUSE = max(1, int(os.environ.get("DEEPSEEK_SESSION_REUSE", "6") or 1))
_SESSION_MAX_AGE_SEC = int(os.environ.get("DEEPSEEK_SESSION_MAX_AGE_SEC", "7200"))
_sessions: dict[str, dict] = {}       # token → {"id": sid, "uses": n, "at": ts}


def _session_key(token: str) -> str:
    import hashlib
    return hashlib.sha1(token.encode("utf-8")).hexdigest()[:16]


def _cached_session(token: str) -> Optional[str]:
    ent = _sessions.get(_session_key(token))
    if not ent:
        return None
    if ent["uses"] >= _SESSION_REUSE or (time.time() - ent["at"]) > _SESSION_MAX_AGE_SEC:
        _sessions.pop(_session_key(token), None)
        return None
    return ent["id"]


def _remember_session(token: str, sid: str) -> None:
    _sessions[_session_key(token)] = {"id": sid, "uses": 0, "at": time.time()}


def _drop_session(token: str) -> None:
    _sessions.pop(_session_key(token), None)


# ── Main entry point ───────────────────────────────────────────────────────
def run_deepseek_http(
    storage_state: dict,
    query: str,
    *,
    proxy: Optional[str] = None,
    search: bool = True,
    timeout: float = 90.0,
    user_agent: Optional[str] = None,   # pool UA passed by main.py; this old client does not impersonate a browser, so it is unused
) -> dict:
    """One deepseek query (pure HTTP). Returns {engine, query, answer, citations, error}.

    error value conventions (for the caller's check-in mapping to FailureType):
      'login_lost' - token invalid (401); 'pow_failed' - PoW could not be computed; anything else is the raw error string.
    """
    result = {"engine": "deepseek", "query": query, "answer": "", "citations": [], "error": None}
    token = extract_token(storage_state)
    if not token:
        result["error"] = "login_lost"
        return result
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    }
    try:
        with httpx.Client(timeout=timeout, headers=headers, proxy=proxy) as c:
            sid = _cached_session(token)
            reused = sid is not None
            if sid is None:
                s = c.post(f"{_BASE}/chat_session/create", json={"character_id": None})
                if _auth_failed(s):
                    result["error"] = "login_lost"
                    return result
                # Under risk control/rate limiting deepseek often returns 200 but data=null (or biz_data missing).
                # Indexing without a guard fails hard with TypeError, and that is not in the failover list -> no account switch.
                # Treat it as rate_limited here so the upper _deepseek_http_sync retries with the next account.
                sbd = _biz_data(s)
                if not sbd or "id" not in sbd:
                    result["error"] = "rate_limited"
                    return result
                sid = sbd["id"]
                _remember_session(token, sid)

            p = c.post(f"{_BASE}/chat/create_pow_challenge",
                       json={"target_path": "/api/v0/chat/completion"})
            if _auth_failed(p):
                result["error"] = "login_lost"
                return result
            pbd = _biz_data(p)
            if not pbd or "challenge" not in pbd:
                result["error"] = "rate_limited"
                return result
            challenge = pbd["challenge"]
            pow_header = _make_pow_header(challenge)
            if pow_header is None:
                result["error"] = "pow_failed"
                return result

            body = {
                "chat_session_id": sid,
                "parent_message_id": None,
                "prompt": query,
                "ref_file_ids": [],
                # Deep thinking is off by default. It was on for three days on the test host from 09-16: 0/59 succeeded. Use DEEPSEEK_THINKING=1 on a single machine for comparison.
                "thinking_enabled": _thinking_enabled(),
                "search_enabled": search,
            }
            with c.stream("POST", f"{_BASE}/chat/completion", json=body,
                          headers={"X-Ds-Pow-Response": pow_header}) as r:
                if r.status_code in (401, 403):
                    _drop_session(token)
                    result["error"] = "login_lost"
                    return result
                # Business-level errors (e.g. PoW rejected) are on the first stream line
                lines = list(r.iter_lines())
            if lines and lines[0].lstrip().startswith("{") and "INVALID_POW" in lines[0]:
                result["error"] = "pow_failed"
                return result
            # Same as _auth_failed: an invalid token at this step is also 200 + first-line code 40003, not 401
            if lines and ('"code":40003' in lines[0].replace(" ", "")
                          or "Authorization Failed" in lines[0]):
                result["error"] = "login_lost"
                return result
            # Account muted by deepseek risk control: the completion's first line returns biz_msg "user is muted"
            # -> mark rate_limited so the upper failover switches accounts (temporary mute, not login loss)
            if lines and '"user is muted"' in lines[0]:
                result["error"] = "rate_limited"
                try:
                    bd = json.loads(lines[0]).get("data", {}).get("biz_data", {})
                    result["mute_until"] = bd.get("mute_until")
                except Exception:  # noqa: BLE001
                    pass
                print(f"[deepseek-http][muted] mute_until={result.get('mute_until')}", flush=True)
                _drop_session(token)
                return result
            text, cites, thinking = _parse_stream_full(lines)
            ent = _sessions.get(_session_key(token))
            if ent and ent["id"] == sid:
                ent["uses"] += 1
            if reused:
                print(f"[deepseek-http] 复用会话 {sid[:8]}… 第 {ent['uses'] if ent else '?'} 题", flush=True)
            result["answer"] = text
            result["citations"] = cites
            result["reasoning"] = thinking if _thinking_enabled() else None
            if not text:
                result["error"] = "empty_answer"
    except Exception as e:  # noqa: BLE001
        result["error"] = repr(e)[:200]
    return result


def _thinking_enabled() -> bool:
    return os.environ.get("DEEPSEEK_THINKING", "0").strip().lower() in ("1", "true", "yes")


def deepseek_http_enabled() -> bool:
    """Enable the HTTP path when DEEPSEEK_HTTP_MODE=1 and the wasm file is in place."""
    return os.environ.get("DEEPSEEK_HTTP_MODE", "").strip() in ("1", "true", "True") \
        and os.path.exists(_WASM_PATH)
