"""Kimi (kimi.com CN site) pure HTTP engine - no browser, calls the Connect API used by the web frontend directly.

Why it works: the kimi.com web app is a Bearer-token Connect (gRPC-Web style) JSON service,
and the answers, sources and `<REF>` inline citations obtained over pure HTTP match the web UI. Protocol facts, token lifecycle and
collection metric definitions are **recorded only** in docs/03-系统设计/引擎采集层/kimi引擎-接入设计与协议.md;
for endpoint / frame format / field name changes, reproduce with scripts/kimi_client.mjs first, then change this.

Same family as deepseek_http: the account pool is still used to get tokens (check-out / check-in happen in run_kimi_pool_query),
and run_kimi_http only handles "one query". Two Kimi specifics:
  1. access_token expires in 15 minutes, and refresh_token **rotates on every renewal**. A successful renewal must be written back to the pool
     **immediately** via on_refreshed, not wait for check-in - check-in only writes back storage_state
     on success, so "renewal succeeded, Chat failed" would lose the new token pair and leave the revoked old values in the DB.
  2. The same account cannot be used concurrently (if two each renew once, the second always gets 401). Serialization relies on the pool's
     session_min_gap_sec (backend _DEFAULT_MIN_GAP_SEC["kimi"]); this module adds no lock.
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import time
import uuid
from typing import Callable, Iterator, Optional
from urllib.parse import urlparse

import httpx

KIMI_ORIGIN = "https://www.kimi.com"
_CHAT_URL = f"{KIMI_ORIGIN}/apiv2/kimi.gateway.chat.v1.ChatService/Chat"
_REFRESH_URL = "https://auth.kimi.com/api/account.gateway.v1.AuthService/RefreshToken"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# The three keys in localStorage (login success is also judged by these three keys appearing)
_KEY_ACCESS = "access_token"
_KEY_REFRESH = "refresh_token"
_KEY_USER = "msh_user_id"
TOKEN_KEYS = (_KEY_ACCESS, _KEY_REFRESH, _KEY_USER)

# Renew first when access_token has less than this many seconds left, so it does not expire mid-Chat
_REFRESH_AHEAD_SEC = 120
_STATUS_COMPLETED = "MESSAGE_STATUS_COMPLETED"

# Inline citation marker in the body: <REF>cite✦tools://web_search:<searchId>#<chunkId>:~:text=<cited text></REF>
_REF_RE = re.compile(r"<REF>(.*?)</REF>", re.DOTALL)
_REF_INNER_RE = re.compile(r"cite✦tools://web_search:([^#]*)#([^:]*):~:text=(.*)", re.DOTALL)


# ── Configuration ─────────────────────────────────────────────────────────
def thinking_enabled() -> bool:
    """KIMI_THINKING=1/0. The default should match kimi.com's default state for a regular user who changes nothing
    (design doc open item #2; until settled, use the delivered client's true); read only at process startup, restart after changing."""
    return os.environ.get("KIMI_THINKING", "1").strip().lower() not in ("0", "false", "no")


def stream_timeout() -> float:
    """Hard cap (seconds) for the whole stream. thinking + web search at 85-90s is normal; the delivering party hit truncation at 90s."""
    try:
        return float(os.environ.get("KIMI_STREAM_TIMEOUT", "240"))
    except ValueError:
        return 240.0


# ── storage_state <-> three keys ─────────────────────────────────────────
def _iter_local_storage(storage_state: dict) -> Iterator[tuple[dict, list]]:
    for o in (storage_state or {}).get("origins", []) or []:
        if not isinstance(o, dict):
            continue
        ls = o.get("localStorage") or []
        if isinstance(ls, dict):
            ls = [{"name": k, "value": v} for k, v in ls.items()]
            o["localStorage"] = ls
        yield o, ls


def extract_tokens(storage_state: dict) -> dict:
    """Get access_token / refresh_token / msh_user_id from the login session's origins[].localStorage. Missing keys are omitted."""
    out: dict = {}
    for _o, ls in _iter_local_storage(storage_state):
        for it in ls:
            name = it.get("name")
            if name in TOKEN_KEYS and it.get("value"):
                out.setdefault(name, str(it["value"]))
    return out


def apply_tokens(storage_state: dict, tokens: dict) -> dict:
    """Write the new token pair back into storage_state (modified in place and also returned), keeping cookies and other localStorage items.
    Creates a kimi origin if missing - check-in write-back needs the full Playwright shape."""
    state = storage_state if isinstance(storage_state, dict) else {}
    state.setdefault("cookies", [])
    state.setdefault("origins", [])
    target = None
    for o, _ls in _iter_local_storage(state):
        if "kimi.com" in str(o.get("origin", "")):
            target = o
            break
    if target is None:
        target = {"origin": KIMI_ORIGIN, "localStorage": []}
        state["origins"].append(target)
    ls = target.setdefault("localStorage", [])
    for k, v in tokens.items():
        if not v:
            continue
        for it in ls:
            if it.get("name") == k:
                it["value"] = v
                break
        else:
            ls.append({"name": k, "value": v})
    return state


def state_has_kimi_auth(storage_state: dict) -> bool:
    """For the pool gate: of the three keys, at least access_token + refresh_token are needed to count as "has a login session"."""
    t = extract_tokens(storage_state)
    return bool(t.get(_KEY_ACCESS) and t.get(_KEY_REFRESH))


def jwt_exp(token: str) -> int:
    """JWT exp (unix seconds); returns 0 if it cannot be decoded (treated as expired, triggering renewal)."""
    try:
        parts = token.split(".")
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad))
        return int(payload.get("exp") or 0)
    except Exception:  # noqa: BLE001
        return 0


# ── Connect frame encoding/decoding ──────────────────────────────────────────────
def encode_frame(obj: dict) -> bytes:
    """[1B flags=0x00][4B big-endian length][JSON]. The request body must also be wrapped this way; bare JSON gets
    invalid_argument - a highly misleading error (design doc known pitfall #5)."""
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def iter_frames(chunks: Iterator[bytes]) -> Iterator[tuple[int, str]]:
    """Split the byte stream into (flags, payload_str). flags 0x00 = data frame, 0x02 = end frame."""
    buf = b""
    for chunk in chunks:
        if not chunk:
            continue
        buf += chunk
        while len(buf) >= 5:
            flags = buf[0]
            length = struct.unpack(">I", buf[1:5])[0]
            if len(buf) < 5 + length:
                break
            payload = buf[5:5 + length].decode("utf-8", errors="replace")
            buf = buf[5 + length:]
            yield flags, payload


# ── Request body ────────────────────────────────────────────────────────
def build_chat_request(query: str, *, thinking: bool, search: bool = True) -> dict:
    """A new conversation per question (chatId="") - continuing a chat carries context, and the account's "memory" leaks into answers (known pitfall #10).
    No model is passed (measured to land on k2d6-chat; passing "kimi-k2" gives invalid_argument, pitfall #6).
    oneof fields are flattened per the JSON wire format {"text":{"content":…}} (pitfall #7)."""
    tools = []
    if search:
        # force:true only "allows" web search rather than forcing it; measured 2 of 4 questions triggered it; without it 8/8 had zero sources
        tools.append({"type": "TOOL_TYPE_SEARCH", "metadata": {"search": {"force": True}}, "name": ""})
    return {
        "chatId": "",
        "kimiplusId": "",
        "scenario": "SCENARIO_CHAT",
        "projectId": "",
        "tools": tools,
        "message": {
            "id": "", "parentId": "", "childrenMessageIds": [],
            "role": "user",
            "blocks": [{"text": {"content": query}}],
            "labels": [], "references": [], "refVotes": [],
        },
        "options": {"thinking": bool(thinking)},
    }


# ── Streaming event accumulation ──────────────────────────────────────────────────
class _Accumulator:
    """Ported from chat() in kimi_client.mjs: op=set/append incremental patches, blocks sorted by id to assemble the body,
    heartbeats skipped, message.status captured unconditionally (the end event has only status and no role, pitfall #4),
    sources in message.refs.searchChunks (the API has no separate ref event)."""

    def __init__(self) -> None:
        self.blocks: dict[str, dict] = {}
        self.chat_id: Optional[str] = None
        self.model: Optional[str] = None
        self.status: Optional[str] = None
        self.search_chunks: dict[str, dict] = {}
        self.search_block_seen = False
        self.event_count = 0
        self.heartbeats = 0

    def feed(self, ev: dict) -> None:
        self.event_count += 1
        if "heartbeat" in ev:
            self.heartbeats += 1
            return
        op = ev.get("op")
        chat = ev.get("chat")
        if isinstance(chat, dict):
            if chat.get("id"):
                self.chat_id = chat["id"]
            m = ((chat.get("lastRequest") or {}).get("options") or {}).get("model")
            if m:
                self.model = m
        msg = ev.get("message")
        if isinstance(msg, dict):
            if msg.get("status"):
                self.status = msg["status"]
            refs = msg.get("refs")
            if isinstance(refs, dict):
                self._take_chunks(refs.get("searchChunks"))
        ref = ev.get("ref")
        if isinstance(ref, dict):
            self._take_chunks(ref.get("searchChunks"))
            if ref.get("base"):
                self._take_chunks([ref])
        blk = ev.get("block")
        if isinstance(blk, dict) and blk.get("id") is not None:
            bid = str(blk["id"])
            b = self.blocks.setdefault(bid, {"id": bid, "kind": "", "content": ""})
            if "think" in blk:
                b["kind"] = "think"
            elif "text" in blk:
                b["kind"] = "text"
            elif "stage" in blk:
                b["kind"] = "stage"
            elif "multiStage" in blk:
                b["kind"] = "multiStage"
            elif "search" in blk:
                b["kind"] = "search"
                self.search_block_seen = True
            elif "tool" in blk:
                b["kind"] = "tool"
            content = None
            if isinstance(blk.get("think"), dict) and "content" in blk["think"]:
                content = blk["think"]["content"]
            elif isinstance(blk.get("text"), dict) and "content" in blk["text"]:
                content = blk["text"]["content"]
            if content is not None:
                content = str(content)
                b["content"] = (b["content"] + content) if op == "append" else content

    def _take_chunks(self, chunks) -> None:
        if not isinstance(chunks, list):
            return
        for c in chunks:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id") or len(self.search_chunks))
            base = c.get("base") if isinstance(c.get("base"), dict) else c
            if not base.get("url"):
                continue
            self.search_chunks.setdefault(cid, {"id": cid, **base})

    def _join(self, kind: str) -> str:
        def _key(b: dict):
            try:
                return (0, int(b["id"]))
            except (TypeError, ValueError):
                return (1, b["id"])
        return "".join(b["content"] for b in sorted(self.blocks.values(), key=_key) if b["kind"] == kind)

    @property
    def answer(self) -> str:
        return self._join("text")

    @property
    def think(self) -> str:
        return self._join("think")

    def citations(self) -> list[dict]:
        out = []
        for i, c in enumerate(self.search_chunks.values(), start=1):
            url = str(c.get("url") or "")
            try:
                domain = urlparse(url).netloc.replace("www.", "")
            except Exception:  # noqa: BLE001
                domain = url
            out.append({"url": url, "domain": domain, "title": str(c.get("title") or ""),
                        "snippet": "", "position": i})
        return out

    @property
    def search_triggered(self) -> bool:
        return self.search_block_seen or bool(self.search_chunks)


def strip_inline_refs(answer: str) -> tuple[str, list[dict]]:
    """Strip <REF>…</REF> from the body, returning (clean body, [{search_id, chunk_id, text}]) - raw material for fragment-level provenance."""
    refs: list[dict] = []

    def _sub(m: re.Match) -> str:
        inner = m.group(1)
        mm = _REF_INNER_RE.match(inner)
        if mm:
            refs.append({"search_id": mm.group(1), "chunk_id": mm.group(2), "text": mm.group(3)})
        else:
            refs.append({"search_id": "", "chunk_id": "", "text": inner})
        return ""

    clean = _REF_RE.sub(_sub, answer)
    return clean, refs


# ── HTTP ─────────────────────────────────────────────────────────
def _headers(access_token: str, *, user_agent: Optional[str], device_id: str, streaming: bool) -> dict:
    return {
        "Content-Type": "application/connect+json" if streaming else "application/json",
        "Authorization": f"Bearer {access_token}",
        "Connect-Protocol-Version": "1",
        "x-msh-platform": "web",
        "x-msh-device-id": device_id,
        "User-Agent": user_agent or _UA,
        "Origin": KIMI_ORIGIN,
        "Referer": f"{KIMI_ORIGIN}/",
    }


def refresh_tokens(refresh_token: str, *, client: httpx.Client, device_id: str,
                   user_agent: Optional[str] = None) -> tuple[int, dict]:
    """POST RefreshToken. Returns (http_status, body). 200 -> {accessToken, refreshToken (rotated)};
    401 -> Session expired (account logged in elsewhere / refresh_token revoked), not self-healable."""
    r = client.post(_REFRESH_URL, json={"refreshToken": refresh_token}, headers={
        "Content-Type": "application/json",
        "x-msh-platform": "web",
        "x-msh-device-id": device_id,
        "User-Agent": user_agent or _UA,
        "Origin": KIMI_ORIGIN,
        "Referer": f"{KIMI_ORIGIN}/",
    })
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {"raw": (r.text or "")[:200]}
    return r.status_code, body if isinstance(body, dict) else {"raw": body}


def _stream_chat(client: httpx.Client, request: dict, headers: dict, *, deadline: float,
                 acc: _Accumulator) -> Optional[str]:
    """Make one Chat call and feed the events to acc. Returns an error string or None. 'http_401' is left to the caller to renew and retry."""
    body = encode_frame(request)
    with client.stream("POST", _CHAT_URL, content=body, headers=headers) as r:
        if r.status_code == 401:
            return "http_401"
        if r.status_code != 200:
            snippet = r.read()[:300].decode("utf-8", errors="replace")
            return f"http_{r.status_code}: {snippet}"

        def _chunks():
            for chunk in r.iter_bytes():
                if time.monotonic() > deadline:
                    raise TimeoutError("stream deadline")
                yield chunk

        try:
            for flags, payload in iter_frames(_chunks()):
                if flags & 0x02:
                    # End frame: normally the payload is "{}" (not an empty string); only one containing an error field is an error (pitfall #3)
                    text = payload.strip()
                    if text and text != "{}":
                        try:
                            eos = json.loads(text)
                        except Exception:  # noqa: BLE001
                            return f"stream_end_frame: {text[:200]}"
                        err = eos.get("error") if isinstance(eos, dict) else None
                        if err:
                            code = err.get("code") if isinstance(err, dict) else err
                            return f"stream_error: {str(code)[:200]}"
                    return None
                if not payload.strip():
                    continue
                try:
                    ev = json.loads(payload)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(ev, dict):
                    acc.feed(ev)
        except TimeoutError:
            return "timeout"
        except httpx.TimeoutException:
            return "timeout"
    return None


def run_kimi_http(
    storage_state: dict,
    query: str,
    *,
    proxy: Optional[str] = None,
    user_agent: Optional[str] = None,
    timeout: Optional[float] = None,
    on_refreshed: Optional[Callable[[dict], None]] = None,
) -> dict:
    """One Kimi query (pure HTTP). Returns {engine, query, answer, reasoning, citations, error, meta}.

    error value conventions (for the caller's check-in mapping to FailureType):
      'login_lost'          - RefreshToken 401 / Chat still 401 after renewal;
      'incomplete:<status>' - stream ended but message.status is not COMPLETED (no half answers returned, D3);
      'timeout'             - exceeded KIMI_STREAM_TIMEOUT;
      'empty_answer'        - COMPLETED but empty body; anything else is the raw error string.
    on_refreshed(new_storage_state): called back **immediately** after a successful renewal (before sending Chat); the caller writes it back to the pool.
    """
    thinking = thinking_enabled()
    total_timeout = float(timeout) if timeout else stream_timeout()
    meta: dict = {"thinking": thinking, "tools": ["TOOL_TYPE_SEARCH"], "model": None,
                  "status": None, "event_count": 0, "search_triggered": False,
                  "inline_refs": [], "refreshed": False, "attempts": 0}
    result: dict = {"engine": "kimi", "query": query, "answer": "", "reasoning": None,
                    "citations": [], "error": None, "meta": meta}

    tokens = extract_tokens(storage_state)
    if not (tokens.get(_KEY_ACCESS) and tokens.get(_KEY_REFRESH)):
        result["error"] = "login_lost"
        return result
    device_id = _device_id(tokens.get(_KEY_ACCESS, ""))

    # The per-read timeout is left for heartbeats; the whole-stream limit is governed by the deadline
    client = httpx.Client(proxy=proxy, timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=False)
    try:
        def _refresh() -> bool:
            status, body = refresh_tokens(tokens[_KEY_REFRESH], client=client, device_id=device_id,
                                          user_agent=user_agent)
            if status == 200 and body.get("accessToken") and body.get("refreshToken"):
                tokens[_KEY_ACCESS] = body["accessToken"]
                tokens[_KEY_REFRESH] = body["refreshToken"]
                apply_tokens(storage_state, tokens)
                meta["refreshed"] = True
                if on_refreshed is not None:
                    try:
                        on_refreshed(storage_state)
                    except Exception as e:  # noqa: BLE001
                        print(f"[kimi-http] on_refreshed 回调失败: {e!r}", flush=True)
                return True
            if status == 401:
                return False
            raise RuntimeError(f"refresh_http_{status}: {str(body)[:200]}")

        # Renew before use: if < 2min remain, get a new pair first so Chat does not hit 401 midway
        if jwt_exp(tokens[_KEY_ACCESS]) - time.time() < _REFRESH_AHEAD_SEC:
            if not _refresh():
                result["error"] = "login_lost"
                return result

        request = build_chat_request(query, thinking=thinking, search=True)
        deadline = time.monotonic() + total_timeout
        acc = _Accumulator()
        err: Optional[str] = None
        # Two kinds of retry, at most once each: Chat 401 -> resend after renewal (pitfall #2); COMPLETED but search not triggered -> ask again
        # (force:true only "allows" web search; reports must distinguish "zero citations" from "did not search"). Both are bound by the same deadline.
        retried_401 = False
        retried_search = False
        attempt = 0
        while True:
            attempt += 1
            meta["attempts"] = attempt
            acc = _Accumulator()
            headers = _headers(tokens[_KEY_ACCESS], user_agent=user_agent, device_id=device_id, streaming=True)
            err = _stream_chat(client, request, headers, deadline=deadline, acc=acc)
            if err == "http_401":
                if retried_401 or not _refresh():
                    result["error"] = "login_lost"
                    return result
                retried_401 = True
                continue
            if err is None and acc.status == _STATUS_COMPLETED and not acc.search_triggered \
                    and not retried_search and time.monotonic() < deadline:
                retried_search = True
                continue
            break

        meta.update({"model": acc.model, "status": acc.status, "event_count": acc.event_count,
                     "search_triggered": acc.search_triggered, "chat_id": acc.chat_id,
                     "heartbeats": acc.heartbeats})
        if err:
            result["error"] = err
            return result
        if acc.status != _STATUS_COMPLETED:
            # No half answers (D3): truncated samples go to the center's failure/retry path
            result["error"] = f"incomplete:{acc.status or 'unknown'}"
            return result
        answer, inline_refs = strip_inline_refs(acc.answer)
        meta["inline_refs"] = inline_refs
        result["answer"] = answer.strip()
        result["reasoning"] = acc.think if thinking else None
        result["citations"] = acc.citations()
        if not result["answer"]:
            result["error"] = "empty_answer"
    except Exception as e:  # noqa: BLE001
        result["error"] = repr(e)[:200]
    finally:
        client.close()
    return result


def _device_id(access_token: str) -> str:
    """Stable device id: use device_id from the JWT if present (most like the real user), otherwise derive a fixed value from the token subject."""
    try:
        parts = access_token.split(".")
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad))
        if payload.get("device_id"):
            return str(payload["device_id"])
        sub = str(payload.get("sub") or "")
        if sub:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"kimi:{sub}"))
    except Exception:  # noqa: BLE001
        pass
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "kimi:anonymous"))


# ── Pool wrapper (check-out -> query -> check-in) ────────────────────────
def run_kimi_pool_query(query: str, max_attempts: int = 3) -> dict:
    """Same as main._deepseek_http_sync: check-out to get a token -> HTTP query -> check-in.
    All in the same thread (session_store's ContextVar is copied along with to_thread).

    Failover: on login_lost, quarantine the bad account and retry with another; other errors do not switch accounts (an incomplete generation is not the account's fault).
    See design doc D3 for the check-in result mapping. The renewed token pair is written back before Chat via
    renew_checked_out_session, so check-in no longer carries storage_state.
    """
    from .session_store import (load_storage_state, report_session_outcome, get_last_checkout_id,
                                renew_checked_out_session, session_user_agent)
    from .browser import _engine_proxy
    from .main import _proxy_dict_to_url, _resolve_exit_ip

    empty = {"engine": "kimi", "query": query, "answer": "", "reasoning": None, "citations": [],
             "source_url": None, "video_url": None, "error": None, "meta": None, "session_id": None}
    res: dict = {}
    err = None
    session_id = None
    for _attempt in range(max_attempts):
        state = load_storage_state("kimi")   # check out again each time -> switch to a non-quarantined account
        if not state:
            return {**empty, "error": "no session available"}
        proxy_url = _proxy_dict_to_url(_engine_proxy("kimi", state))
        session_id = get_last_checkout_id("kimi")
        res = run_kimi_http(
            state, query, proxy=proxy_url, user_agent=session_user_agent("kimi"),
            on_refreshed=lambda st: renew_checked_out_session("kimi", st),
        )
        err = res.get("error")
        exit_ip = _resolve_exit_ip(proxy_url)
        if err == "login_lost":
            report_session_outcome("kimi", result="login_lost", exit_ip=exit_ip)
        elif err == "empty_answer":
            report_session_outcome("kimi", result="empty_answer", exit_ip=exit_ip)
        elif err:
            # incomplete / timeout / protocol errors: the engine's or network's fault, do not penalize the account (crash does not count against the session)
            report_session_outcome("kimi", result="crash", error_msg=err, exit_ip=exit_ip)
        else:
            report_session_outcome("kimi", result="success", exit_ip=exit_ip)
        if err != "login_lost":
            break
    return {"engine": "kimi", "query": query, "answer": res.get("answer", ""),
            "reasoning": res.get("reasoning"),
            "citations": res.get("citations", []), "source_url": None, "video_url": None,
            "error": err, "meta": res.get("meta"), "session_id": session_id}


# ── Adapter (so ENGINE_MODULE_MAP / agent recognize kimi) ────────────────
from .engines.base import Citation, EngineAdapter, EngineResult  # noqa: E402


class KimiHttpAdapter(EngineAdapter):
    """No browser. search() must really work: the runner falls back to /search on /search-hot HTTP errors,
    and /search calls adapter.search() directly - an empty implementation would make the fallback path burn an account for no result."""
    name = "kimi"

    async def search(self, query: str) -> EngineResult:
        import asyncio
        d = await asyncio.to_thread(run_kimi_pool_query, query)
        r = EngineResult(
            engine="kimi", query=query, answer=d.get("answer") or "",
            citations=[Citation(url=c["url"], domain=c.get("domain", ""), title=c.get("title", ""),
                                snippet=c.get("snippet", ""), position=c.get("position", 0))
                       for c in (d.get("citations") or [])],
            error=d.get("error"), reasoning=d.get("reasoning"), meta=d.get("meta"),
        )
        return r

    async def is_available(self) -> bool:
        from .session_store import peek_session_exists
        return peek_session_exists("kimi")
