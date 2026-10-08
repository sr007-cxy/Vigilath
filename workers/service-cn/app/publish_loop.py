"""Publish pool worker - pull-loop of claim -> call local /publish -> report receipt (the real publish executor of step 6).

Shaped after app/agent.py, but the publish pool differs from the engine pool in three ways (design §3.4):

  · The engine pool **picks** accounts at claim time; the publish pool's claim **only takes tasks**, with the account already pinned in the task -
    what the worker gets from the backend's claim response is "this draft x this account x this login session",
    delivered together with the publish body (title/body/...); the worker **does not query the business DB back** (the 1820dc75
    invariant: the claimed task and the published content must come from the same render).
  · The engine pool "claims a batch and each runs its own"; the publish pool runs whatever it claims, records every `reason`, and the worker does not need
    to queue itself - per-account concurrency=1 is guaranteed by the backend lease; here only vertical `max_tasks` throttling is done.
  · The report receipt is a **dual update of account state + queue state**, not the engine pool's "hand in the result and done".

## ENV

- `PUBLISH_DISPATCH_URL`  required to enable; when empty the whole loop is a no-op (does not touch the existing engine agent)
- `PUBLISH_SERVICE_TOKEN` token for calling the backend's /publish-dispatch/*; on the backend side, an unconfigured
  require_publish_service_token means no gate, so it is sent if available and omitted otherwise (same stance as
  publish_api.py)
- `WORKER_UID`            the claim's worker_id. **Immutable** while bound - owner_worker_id is a permanent binding,
  changing the uid makes it "a new worker" and none of its accounts can be claimed
- `PUBLISH_POLL_SEC`      claim polling interval (default 120). Short polling is itself a machine signature; do not set it too tight
- `PUBLISH_MAX_TASKS`     max tasks claimed per round (default 1)
- `PUBLISH_PUBLISH_URL`   address of the local /publish (default http://127.0.0.1:8092, i.e. itself).
  **Not derived from PUBLISH_DISPATCH_URL** - that is the backend address; deriving from it would send publish requests to the backend,
  while /publish only exists on the browser-service side
- `PUBLISH_CLIENT_TIMEOUT` client timeout for calling /publish. Derived from the server budget by default (see
  `_publish_client_timeout`); **do not lower it manually** - see the "client timeout" section below
- `PUBLISH_DRY_RUN` / `PUBLISH_ONCE`  equivalent to `--dry-run` / `--once` (for systemd)

## 🔴 Client timeout: lowering it is approving the same article being published twice

`/publish` is an endpoint that **touches the platform and is irreversible**. The client giving up before it returns does not mean that publish
did not happen - browser-service still runs the rest of the flow, including clicking the publish button.

So there are two hard rules here:

1. **The client timeout must exceed the server's own budget** (`PUBLISH_SEMAPHORE_TIMEOUT` +
   `PUBLISH_TIMEOUT`, default 120+240=360s). The client timing out first is the worst case: the server is still running
   while the worker is already writing the receipt - the two sides reach different conclusions about the same publish.
   (That is exactly how the first real publish went wrong on 2026-08-13: the client timed out at 30s and reported crash, while the adapter then
   finished clicking publish and judged submit_unknown. crash goes to requeue -> the queue row goes back to queued and can be reclaimed,
   and one claim later the same article was published a second time.)
2. **Client exceptions must be classified by "did the request get there"**, never blanket crash. See `_publish_once`.

## dry_run mode

When the loop is started with `--dry-run`, /publish is called with dry_run=True forced (it is also caught once more by publish_api's
account_id == "" branch, but this is a double safeguard), and receipts report result=success or fail_type as usual.
The goal: **verify the whole claim->publish->report loop without touching real accounts** (the acceptance
scaffold for F3/F4), seeing the closed black box work end to end without real publishing.

## Two startup forms

Production: `main.py`'s lifespan starts it alongside the engine agent when `PUBLISH_DISPATCH_URL` is configured.
Acceptance: `python -m app.publish_loop --dry-run --once` runs one round standalone - not resident, easy to assert on.
F4's "two workers competing for the same account" is run in two terminals with different `WORKER_UID`s, rather than embedding an
HTTP service to simulate it: the process boundary is the real worker boundary (fingerprints/egress of different machines are what must be isolated).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import zlib
from datetime import datetime, timezone
from typing import Optional

import httpx

from .core_publisher import core_claimable_platforms, verify_article_with_core

log = logging.getLogger("browser-service.publish_loop")


def worker_id() -> Optional[int]:
    """Stable numeric worker id. Same method as the backend / engine pool: crc32(WORKER_UID) & 0x7FFFFFFF.
    WORKER_UID unset -> None, and the backend's claim degrades to "no ownership restriction" (must be set in production)."""
    uid = (os.environ.get("WORKER_UID") or "").strip()
    if not uid:
        return None
    return zlib.crc32(uid.encode("utf-8")) & 0x7FFFFFFF


def _map_failure(fail_type: Optional[str], ok: bool) -> str:
    """Map the fail_type returned by /publish to a PublishFailureType value that report recognizes.

    As long as the value is valid and has a matching disposition in the backend's _PUBLISH_QUARANTINE_POLICY, pass it straight through;
    only types from /publish that the backend does **not** judge (e.g. raw crash local failures) need normalizing.
    The return value is always a valid report.result string.
    """
    known = {
        "success", "login_lost", "rate_limited", "content_rejected", "banned",
        "identity_mismatch", "exit_ip_drift", "submit_unknown", "not_implemented",
        "dom_not_found", "timeout", "crash",
    }
    ft = (fail_type or "").strip().lower()
    if ft in known:
        return ft
    # Unknown / ok=False without a fail_type -> engineering failure, the backend accounts for it by threshold.
    return "crash" if not ok else "success"


def _report_body(task: dict, pub: dict, ok: bool, worker: Optional[int]) -> dict:
    """Assemble one /publish result into a report request body. Audit fields like URL/screenshot/egress are forwarded as-is."""
    fail_type = _map_failure(pub.get("fail_type"), ok)
    body = {
        "dispatch_id": task["dispatch_id"],
        "worker_id": worker,
        "result": fail_type,
        "error": (pub.get("error") or None),
        # 🔴 Tasks without a published_url **cannot** go to published - the report side will hold them at
        # pending_url accordingly. Only the real URL observed by /publish is passed through here; dry_run is always None.
        "published_url": (pub.get("published_url") or None),
        # How the URL was observed (url_redirect / creator_center_anchor). The URL stored in the DB is the same
        # string, and without the source traveling along, one cannot later tell "redirected via the address bar" from "read from the creator center".
        "url_source": (pub.get("url_source") or None),
        "draft_id": (pub.get("draft_id") or None),
        "draft_url": (pub.get("draft_url") or None),
        "screenshot_path": (pub.get("screenshot_path") or None),
        "exit_ip": (pub.get("exit_ip") or None),
        "observed_account_id": (pub.get("observed_account_id") or None),
        "rate_limited_until": (pub.get("rate_limited_until") or None),
        "failure_phase": (pub.get("failure_phase") or None),
        "confirmed_not_published": bool(pub.get("confirmed_not_published")),
        # Included only on success when /publish returned a new storage_state (for rolling renewal) - omitted otherwise,
        # never overwriting a good session with an empty shell (the report side already guards this; no extra fields fabricated here).
    }
    if ok and pub.get("storage_state"):
        body["storage_state"] = pub["storage_state"]
    return body


def _publish_client_timeout() -> float:
    """Client timeout for calling /publish. Default = the server's own worst-case budget + margin.

    The server-side limit of `POST /publish` is `PUBLISH_SEMAPHORE_TIMEOUT` (waiting for a concurrency slot) +
    `PUBLISH_TIMEOUT` (running the publish flow), default 120+240=360s; beyond that it returns **by itself** a 200 with a
    fail_type - that is the conclusion the worker should trust. As long as the client outlasts this budget, it will always
    get the server's verdict and never needs to guess.

    Reads the same-named env vars as publish_api.py:51-54: naturally consistent when the worker and /publish share a process;
    in split deployments both sides' env must be configured together - so the derived result is logged below, making misconfiguration visible.
    """
    override = (os.environ.get("PUBLISH_CLIENT_TIMEOUT") or "").strip()
    if override:
        return float(override)
    sem = float(os.environ.get("PUBLISH_SEMAPHORE_TIMEOUT", "120"))
    pub = float(os.environ.get("PUBLISH_TIMEOUT", "240"))
    return sem + pub + 60.0        # margin for connection setup/transfer/serialization


# Client exception -> classification of "could the request possibly have reached /publish".
#
# 🔴 The criterion is not "how severe is the error" but **could the platform possibly have been touched**:
#   · never connected / did not get a pool connection -> not a single byte was sent -> nothing happened -> retryable
#   · any exception after connecting (read timeout/write timeout/protocol error/connection cut) -> the server may already be running the publish flow,
#     even having clicked the publish button -> **result unknown**, only submit_unknown (never retried, reconciled via draft_id)
#
# The cost of misclassifying is asymmetric: judging "did not happen" as submit_unknown costs one draft in the manual reconciliation queue;
# judging "may have happened" as retryable costs the same article published twice on the platform, irreversibly. So the
# default direction here is submit_unknown - only the few classes that prove the request was never sent are allowed to retry.
_NEVER_SENT = (
    httpx.ConnectError,        # TCP/TLS was never established
    httpx.ConnectTimeout,      # same as above, timeout form
    httpx.PoolTimeout,         # did not get a pooled connection, request not sent
    httpx.UnsupportedProtocol,
    httpx.InvalidURL,
)


def _classify_client_error(e: BaseException) -> tuple[str, str]:
    """Returns (fail_type, human-readable reason). See the criteria above `_NEVER_SENT`."""
    if isinstance(e, _NEVER_SENT):
        return ("crash",
                f"没能把请求送到 /publish({type(e).__name__}),平台未被触碰,可重试")
    return ("submit_unknown",
            f"请求已发出但没等到 /publish 的结论({type(e).__name__}: {e})—— "
            f"browser-service 可能仍在跑发布流程甚至已点过发布按钮。"
            f"🔴 结果未知,禁止重试;draft_id 只在 browser-service 的日志里,"
            f"对账去那里找 '即将点击发布'")


async def _claim(client: httpx.AsyncClient, base: str, headers: dict,
                 worker: Optional[int], max_tasks: int) -> list[dict]:
    """Claim tasks. 404 = nothing for now, return [] so the caller sleeps; other 5xx raise so the outer layer backs off."""
    # server_pool publishing always goes through Core. The environment variable only narrows the default set;
    # empty config means all platforms supported by the sidecar can claim tasks.
    core_platforms = core_claimable_platforms()
    platforms = []
    restrictions: dict[str, list[int]] = {}
    for platform, accounts in sorted(core_platforms.items()):
        if not accounts:
            continue
        if "*" not in accounts:
            numeric_ids = sorted(int(v) for v in accounts if v.isdigit())
            if not numeric_ids:
                continue
            restrictions[platform] = numeric_ids
        platforms.append(platform)
    body: dict = {"worker_id": worker, "platforms": platforms,
                  "account_ids_by_platform": restrictions, "max_tasks": max_tasks}
    r = await client.post(f"{base}/publish-dispatch/claim", json=body, headers=headers)
    if r.status_code == 404:
        # 🔴 404 is not an error but part of the protocol: "nothing to claim right now, tasks are backing off".
        # Do not raise, do not count as failure; warn after several consecutive rounds, hinting at possible misconfiguration.
        return []
    r.raise_for_status()
    return (r.json() or {}).get("tasks") or []


async def _claim_verifications(client: httpx.AsyncClient, base: str, headers: dict,
                               worker: int, max_tasks: int) -> list[dict]:
    r = await client.post(f"{base}/publish-dispatch/verification/claim", json={
        "worker_id": worker, "max_tasks": max_tasks,
    }, headers=headers)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return (r.json() or {}).get("tasks") or []


async def _publish_once(client: httpx.AsyncClient, pub_base: str, pub_headers: dict,
                        task: dict, dry: bool, timeout: float) -> Optional[dict]:
    """Send one claimed task to the local /publish. Returns the body or a self-made failure body.

    `timeout` must exceed the server's own budget (see `_publish_client_timeout`): the client giving up first
    is the most dangerous form - the server is still publishing while the worker is already writing a conclusion.
    """
    payload = {
        "platform": task.get("platform"),
        "account_id": task.get("account_id", ""),
        "title": task.get("title", ""),
        "body": task.get("body", ""),
        "body_format": task.get("body_format", "plain"),
        "source_markdown": task.get("source_markdown", ""),
        "images": task.get("images") or [],
        "location": task.get("location"),
        "topics": task.get("topics"),
        "dry_run": dry,
        "storage_state": task.get("storage_state") or {},
        "official_api": task.get("official_api"),
        "proxy_url": task.get("proxy_url"),
        "ua_platform": task.get("ua_platform"),
        "user_agent": task.get("user_agent"),
        "dispatch_id": task.get("dispatch_id"),
        "attempt": task.get("attempt"),
        "account_db_id": task.get("account_db_id"),
        "worker_id": task.get("worker_id"),
    }
    try:
        r = await client.post(f"{pub_base}/publish", json=payload,
                              headers=pub_headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        # /publish deliberately **returns 200 even for business failures** (publish_api.py:176-179), so non-200 has only
        # two sources, and their retryability is exactly opposite:
        #   4xx - the request itself was blocked outside the flow (unknown platform/missing cookie/bad token/concurrency full 429),
        #          no browser was even opened, the platform was not touched -> crash (retryable).
        #   5xx - not produced by /publish itself (it wraps all exceptions into 200), meaning something went wrong at the ASGI layer,
        #          possibly **after** _publish_inner returned -> the publish may already have completed ->
        #          result unknown, submit_unknown.
        if r.status_code >= 500:
            log.error("[publish-loop] /publish HTTP %s dispatch=%s - error not produced by /publish itself, "
                      "unknown whether the publish completed, judged submit_unknown: %s",
                      r.status_code, task.get("dispatch_id"), r.text[:200])
            return {"ok": False, "fail_type": "submit_unknown",
                    "failure_phase": "commit", "confirmed_not_published": False,
                    "error": f"/publish HTTP {r.status_code}(结果未知,禁止重试):"
                             f"{r.text[:200]}"}
        log.warning("[publish-loop] /publish HTTP %s for dispatch=%s: %s",
                    r.status_code, task.get("dispatch_id"), r.text[:200])
        return {"ok": False, "fail_type": "crash",
                "failure_phase": "pre_commit", "confirmed_not_published": True,
                "error": f"/publish HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:  # noqa: BLE001
        fail_type, why = _classify_client_error(e)
        logf = log.error if fail_type == "submit_unknown" else log.warning
        logf("[publish-loop] /publish 未拿到结论 dispatch=%s → %s:%s",
             task.get("dispatch_id"), fail_type, why)
        return {"ok": False, "fail_type": fail_type, "error": why,
                "failure_phase": "pre_commit" if fail_type == "crash" else "commit",
                "confirmed_not_published": fail_type == "crash"}


async def _report(client: httpx.AsyncClient, base: str, headers: dict,
                  body: dict) -> bool:
    """Receipt. Success is judged by "it went out" - not by whether the backend returned 200: the publish is complete and
    irreversible; a 5xx on report only means the backend did not confirm, and must not be treated as "did not happen" and republished."""
    try:
        r = await client.post(f"{base}/publish-dispatch/report", json=body, headers=headers)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[publish-loop] report dispatch=%s failed: %s (publish completed, "
                    "backend did not confirm - task stays claimed until the lease expires and the backend falls back)",
                    body.get("dispatch_id"), e)
        return False


async def _handle_task(client: httpx.AsyncClient, base: str, headers: dict,
                       pub_base: str, pub_headers: dict, task: dict, dry: bool,
                       worker: Optional[int], pub_timeout: float) -> None:
    """Claim one -> publish -> receipt. The links between these steps are guarded by backend task-level locks; this only handles ordering."""
    pub = await _publish_once(client, pub_base, pub_headers, task, dry, pub_timeout)
    ok = bool(pub and pub.get("ok"))
    if not ok:
        log.warning("[publish-loop] publish not confirmed dispatch=%s fail_type=%s err=%s",
                    task.get("dispatch_id"), pub and pub.get("fail_type"),
                    (pub or {}).get("error"))
    body = _report_body(task, pub or {}, ok, worker)
    # 🔴 submit_unknown / result-unknown tasks are never resubmitted here - the backend's visible_after has already pushed the head
    # to >= 10 minutes later and attempt is maxed out, so no future claim can get it (a mechanical fallback).
    await _report(client, base, headers, body)


async def _handle_verification(client: httpx.AsyncClient, base: str, headers: dict,
                               task: dict, worker: int) -> None:
    result = await verify_article_with_core(
        platform=task["platform"],
        external_post_id=task["external_post_id"],
        storage_state=task.get("storage_state") or {},
        proxy_url=task.get("proxy_url"),
        expected_account_id=task.get("account_id") or "",
        user_agent=task.get("user_agent"),
        ua_platform=task.get("ua_platform"),
    )
    body = {
        "task_id": task["task_id"],
        "account_db_id": task["account_db_id"],
        "worker_id": worker,
        "claim_token": task["claim_token"],
        "status": result.get("status") if result.get("ok") else "query_error",
        "raw_status": result.get("raw_status"),
        "audit_fail_reason": result.get("audit_fail_reason"),
        "publish_time": result.get("publish_time"),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "error_message": result.get("error"),
        "exit_ip": result.get("exit_ip"),
        "observed_account_id": result.get("observed_account_id"),
        "storage_state": result.get("storage_state"),
    }
    try:
        response = await client.post(
            f"{base}/publish-dispatch/verification/report", json=body, headers=headers)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("[publish-loop] Ctrip review receipt failed task=%s: %s",
                    task.get("task_id"), exc)


async def publish_loop(stop: asyncio.Event, *, worker: Optional[int]) -> None:
    """Background polling loop. worker_id is decided by the caller (the same value used by rebind)."""
    base = (os.environ.get("PUBLISH_DISPATCH_URL") or "").rstrip("/")
    if not base:
        return  # not configured = no-op
    token = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    headers = {"X-Publish-Service-Token": token} if token else {}
    # Calls to the backend /publish-dispatch/* and to the local /publish use the **same** token and the same
    # header name (publish_api.py:69 also reads PUBLISH_SERVICE_TOKEN). Two names are used to
    # make "the two sides are actually two trust domains" visible in the code - when the worker is deployed separately and needs
    # two tokens, changing this one line is enough, instead of hunting for every call site that passes headers.
    pub_headers = headers
    # Local /publish. Defaults to calling itself (loop and publish_api share a process), not derived from base -
    # base is the **backend** address; deriving from it would send publish requests to backend port 8000, which has no /publish.
    pub_base = ((os.environ.get("PUBLISH_PUBLISH_URL") or "").rstrip("/")
                or "http://127.0.0.1:8092")
    dry = (os.environ.get("PUBLISH_DRY_RUN") or "").strip() in ("1", "true")
    # 🔴 See "client timeout" in the module docstring: must be > the server budget, otherwise every real publish would
    # hit the split conclusion of "client gave up first while the server is still publishing".
    pub_timeout = _publish_client_timeout()
    # Pull mode: the worker only wakes when there are new tasks, no need to keep a resident process.
    poll_sec = float(os.environ.get("PUBLISH_POLL_SEC", "120"))
    max_tasks = int(os.environ.get("PUBLISH_MAX_TASKS", "1"))
    once = (os.environ.get("PUBLISH_ONCE") or "").strip() in ("1", "true")

    log.info("[publish-loop] start base=%s worker=%s dry_run=%s pub=%s poll=%ss max=%d",
             base, worker, dry, pub_base, poll_sec, max_tasks)
    # Logged on its own line: a misconfiguration (smaller than PUBLISH_TIMEOUT) can only be spotted here, and the consequences are irreversible.
    log.info("[publish-loop] /publish client timeout=%.0fs (server budget sem=%s + pub=%s)",
             pub_timeout, os.environ.get("PUBLISH_SEMAPHORE_TIMEOUT", "120"),
             os.environ.get("PUBLISH_TIMEOUT", "240"))

    if worker is None:
        # The claim's worker_id is a required numeric field, and None gets a 422; also owner_worker_id
        # is a permanent binding, and without a uid there is no ownership at all - failing early beats a 422 every 120 seconds.
        log.error("[publish-loop] WORKER_UID not set - claim needs a stable worker_id, "
                  "and accounts' owner_worker_id is bound by it. Loop not started.")
        return
    consecutive_empty = 0

    # 30s is the claim/report timeout - they are just a few SQL statements; 30s slow means the backend is in trouble.
    # /publish does **not** use this value; it passes pub_timeout separately (see _publish_once): on the same
    # client the two kinds of requests differ in time scale by an order of magnitude, so sharing one timeout would shortchange one of them.
    async with httpx.AsyncClient(timeout=30) as client:
        while not stop.is_set():
            try:
                tasks = await _claim(client, base, headers, worker, max_tasks)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                # Cannot reach the backend at all -> do not spin the loop, back off and retry.
                log.warning("[publish-loop] claim failed: %s", e)
                await asyncio.sleep(15)
                continue
            for task in tasks:
                if stop.is_set():
                    # 🔴 Even when a shutdown signal arrives, already claimed tasks must **not** be dropped: on the backend they are claimed,
                    # holding the account lease and the daily quota. Dropping them would leave them waiting until the lease expires
                    # (30 minutes) before a fallback picks them up - and M1 has a quota of only 1 article per day. So finish publishing
                    # and send receipts as usual; shutdown takes effect after this batch.
                    log.info("[publish-loop] shutdown signal received, finishing already claimed dispatch=%s first",
                             task.get("dispatch_id"))
                await _handle_task(client, base, headers, pub_base, pub_headers,
                                   task, dry, worker, pub_timeout)

            try:
                verifications = await _claim_verifications(
                    client, base, headers, worker, max_tasks)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                log.warning("[publish-loop] verification claim failed: %s", e)
                verifications = []
            for task in verifications:
                await _handle_verification(client, base, headers, task, worker)

            if not tasks and not verifications:
                if once:
                    log.info("[publish-loop] --once: no publish or Ctrip review tasks this round, exiting")
                    break
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    log.info("[publish-loop] no tasks for %d rounds, worker idle", consecutive_empty)
                    consecutive_empty = 0
                await asyncio.sleep(poll_sec)
                continue
            consecutive_empty = 0
            log.info("[publish-loop] completed %d publishes, %d Ctrip reviews",
                     len(tasks), len(verifications))
            if once:
                break
            await asyncio.sleep(poll_sec)
    log.info("[publish-loop] stopped")


def main() -> int:
    """Standalone entry: python -m app.publish_loop --dry-run.

    Running this loop as a standalone process is the form for **local multi-worker verification of F4**: open two terminals, each with a different
    WORKER_UID, concurrently claiming the same account - only the owner can claim it, the other always gets 404.
    The production form is started in main.py's lifespan alongside the engine agent (see the module docstring).
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="调本机 /publish 时强制 dry_run=True,回执照常 —— 用于在不碰真实"
                         "账号的前提下验证 claim→report 闭环")
    ap.add_argument("--once", action="store_true",
                    help="只领一轮就退出(验收脚本用,不常驻)")
    ap.add_argument("--worker", type=int, default=None,
                    help="worker_id 覆盖 crc32(WORKER_UID)(一般不用).")
    args = ap.parse_args()

    if args.dry_run:
        os.environ["PUBLISH_DRY_RUN"] = "1"
    if args.once:
        os.environ["PUBLISH_ONCE"] = "1"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()
    worker = args.worker if args.worker is not None else worker_id()
    try:
        asyncio.run(publish_loop(stop, worker=worker))
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
