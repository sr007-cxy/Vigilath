"""Acceptance test of publish_loop's claim -> /publish -> report loop - no network, no real accounts.

Runs the loop sandwiched between a **fake backend** and a **fake /publish** (both local ASGI stubs), asserting:

  A. after claiming a task, the request body sent to /publish = the publish body delivered by claim (the worker does not query the DB back or render itself)
  B. in dry_run mode /publish always receives dry_run=True (the mechanical guarantee of not touching real accounts)
  C. success with published_url -> report result=success and the URL passed through as-is
  D. 🔴 submit_unknown -> report truthfully reports submit_unknown, and the loop **does not resubmit**
     (this is the only line of defense against "the same article published twice"; the failure is irreversible)
  E. /publish itself returns HTTP 500 -> still sends a receipt (submit_unknown), not leaving the task claimed and holding quota
  F. claim returns 404 -> not an error, no retry, no receipt (404 is part of the protocol)

Why stubs instead of real services: this layer verifies **protocol integration** (field names, failure mapping, no resubmission),
which is unrelated to browser behavior. Acceptance of real publishing is F3 (3 real publishes), which must use real accounts and is not this test's job.

Usage:
    cd workers/service-cn
    python verify_publish_loop.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import httpx  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        _failures.append(f"{name} {detail}".strip())


# One task delivered by claim. Field names follow the return value of _claim_one in api/publish_dispatch.py.
TASK = {
    "dispatch_id": 4242,
    "task_id": 77,
    "doc_id": 812,
    "title": "渲染好的标题",
    "body": "渲染好的正文\n\n第二段",
    "body_format": "markdown",
    "images": [],
    "render_warnings": [],
    "platform": "zhihu",
    "account_db_id": 1,
    "account_id": "api.me.url_token:3eff8907b3ccabcd",
    "attempt": 1,
    "storage_state": {"cookies": [{"name": "z_c0", "value": "x"}]},
    "proxy_url": "http://user:pass@proxy.example:8080",
    "ua_platform": "Win32",
    "expected_exit_ip": "1.2.3.4",
    "lease_until": "2026-08-13T12:00:00",
}


class Recorder:
    """Record every request the loop sends, for assertions."""

    def __init__(self, *, claim_returns: list, publish_returns: dict,
                 publish_status: int = 200):
        self.claim_returns = list(claim_returns)   # what each claim round returns (a list or 404)
        self.publish_returns = publish_returns
        self.publish_status = publish_status
        self.claims: list[dict] = []
        self.publishes: list[dict] = []
        self.reports: list[dict] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = {}
        if request.content:
            import json
            body = json.loads(request.content)
        if path.endswith("/publish-dispatch/claim"):
            self.claims.append(body)
            nxt = self.claim_returns.pop(0) if self.claim_returns else 404
            if nxt == 404:
                return httpx.Response(404, json={"detail": "no publishable account available"})
            return httpx.Response(200, json={"tasks": nxt})
        if path.endswith("/publish-dispatch/report"):
            self.reports.append(body)
            return httpx.Response(200, json={"account_status": "warming",
                                             "dispatch_status": "done", "notes": []})
        if path.endswith("/publish"):
            self.publishes.append(body)
            if self.publish_status != 200:
                return httpx.Response(self.publish_status, text="boom")
            return httpx.Response(200, json=self.publish_returns)
        return httpx.Response(404, json={"detail": f"unexpected path {path}"})


async def run_loop(rec: Recorder, *, dry: bool) -> None:
    """Replace the loop's httpx.AsyncClient with a MockTransport hitting the stubs and run one --once round."""
    from app import publish_loop as mod

    os.environ["PUBLISH_DISPATCH_URL"] = "http://backend.invalid/api"
    os.environ["PUBLISH_PUBLISH_URL"] = "http://worker.invalid"
    os.environ["WECHATSYNC_PUBLISHER_URL"] = "http://sidecar.invalid"
    os.environ["PUBLISH_ONCE"] = "1"
    os.environ["PUBLISH_DRY_RUN"] = "1" if dry else ""
    os.environ["PUBLISH_POLL_SEC"] = "0"

    transport = httpx.MockTransport(rec.handler)
    real_client = httpx.AsyncClient

    def _patched(*a, **kw):
        kw["transport"] = transport
        return real_client(*a, **kw)

    mod.httpx.AsyncClient = _patched          # type: ignore[assignment]
    try:
        await mod.publish_loop(asyncio.Event(), worker=990001)
    finally:
        mod.httpx.AsyncClient = real_client   # type: ignore[assignment]


def main() -> int:
    print("=== publish_loop 闭环验收(stub,不出网)===\n")

    # ── A/B/C: dry_run success path ──────────────────────────────────────
    print("A/B/C dry_run 成功路径")
    rec = Recorder(claim_returns=[[TASK]], publish_returns={
        "ok": True, "published_url": None, "draft_id": "2068728366174410288",
        "screenshot_path": "zhihu/before-publish-1.png", "exit_ip": "1.2.3.4",
        "observed_account_id": TASK["account_id"], "fail_type": None, "error": None,
    })
    asyncio.run(run_loop(rec, dry=True))
    check("claim 带上 worker_id 与默认 Core 平台",
          rec.claims and rec.claims[0].get("worker_id") == 990001
          and "zhihu" in rec.claims[0].get("platforms", [])
          and "cnblogs" in rec.claims[0].get("platforms", []), str(rec.claims[:1]))
    p = rec.publishes[0] if rec.publishes else {}
    check("A /publish 的正文 = claim 下发的正文(worker 不自己渲染)",
          p.get("title") == TASK["title"] and p.get("body") == TASK["body"]
          and p.get("body_format") == TASK["body_format"], str(p)[:200])
    check("A /publish 带上该账号的登录态与代理",
          p.get("storage_state") == TASK["storage_state"]
          and p.get("proxy_url") == TASK["proxy_url"], str(p.get("proxy_url")))
    check("B dry_run 模式下 /publish 收到 dry_run=True", p.get("dry_run") is True,
          f"dry_run={p.get('dry_run')}")
    r = rec.reports[0] if rec.reports else {}
    check("C report result=success", r.get("result") == "success", str(r)[:200])
    check("C draft_id / 截图 / exit_ip 原样透传给 report",
          r.get("draft_id") == "2068728366174410288"
          and r.get("screenshot_path") == "zhihu/before-publish-1.png"
          and r.get("exit_ip") == "1.2.3.4", str(r)[:200])
    check("C dry_run 不产出 published_url(没有 URL 的任务不能进 published)",
          r.get("published_url") is None, str(r.get("published_url")))

    # ── Real publish success: the URL must be brought back as-is ───────────────────────────────
    print("\nC2 真实发布成功路径(URL 回填)")
    rec = Recorder(claim_returns=[[TASK]], publish_returns={
        "ok": True, "published_url": "https://zhuanlan.zhihu.com/p/123456",
        "draft_id": "123456", "screenshot_path": "zhihu/published-1.png",
        "exit_ip": "1.2.3.4", "observed_account_id": TASK["account_id"],
        "fail_type": None, "error": None,
    })
    asyncio.run(run_loop(rec, dry=False))
    check("非 dry_run 时 /publish 收到 dry_run=False",
          rec.publishes[0].get("dry_run") is False, str(rec.publishes[0].get("dry_run")))
    check("published_url 原样透传(mark_published 的唯一输入)",
          rec.reports[0].get("published_url") == "https://zhuanlan.zhihu.com/p/123456",
          str(rec.reports[0])[:200])

    # ── D: submit_unknown is never resubmitted ──────────────────────────────────
    print("\nD 🔴 submit_unknown 不重投(同一篇发两遍是不可逆错误)")
    rec = Recorder(claim_returns=[[TASK]], publish_returns={
        "ok": False, "fail_type": "submit_unknown",
        "error": "已点击发布但未等到文章 URL", "published_url": None,
        "draft_id": "999", "screenshot_path": "zhihu/commit-timeout.png",
        "exit_ip": "1.2.3.4", "observed_account_id": TASK["account_id"],
    })
    asyncio.run(run_loop(rec, dry=False))
    check("D report 如实上报 submit_unknown(不改判成可重试的类型)",
          rec.reports and rec.reports[0].get("result") == "submit_unknown",
          str(rec.reports[:1])[:200])
    check("D loop 只调了 1 次 /publish —— 没有任何重投",
          len(rec.publishes) == 1, f"publish 调用 {len(rec.publishes)} 次")
    check("D draft_id 带回(submit_unknown 对账的唯一依据)",
          rec.reports[0].get("draft_id") == "999", str(rec.reports[0].get("draft_id")))

    # ── Failure type mapping: identity_mismatch must be reported as-is ────────────────
    print("\nD2 identity_mismatch 原样上报(防发错账号)")
    rec = Recorder(claim_returns=[[TASK]], publish_returns={
        "ok": False, "fail_type": "identity_mismatch",
        "error": "当前登录身份与期望账号不一致", "published_url": None,
        "observed_account_id": "api.me.url_token:deadbeefdeadbeef",
    })
    asyncio.run(run_loop(rec, dry=False))
    check("D2 result=identity_mismatch", rec.reports[0].get("result") == "identity_mismatch",
          str(rec.reports[0])[:160])
    check("D2 observed_account_id 带回(排障要知道实际是谁)",
          rec.reports[0].get("observed_account_id") == "api.me.url_token:deadbeefdeadbeef")

    # ── E: even if /publish dies, a receipt must be sent ────────────────────────────────────
    print("\nE /publish HTTP 500 → 仍然回执(不把任务留在 claimed 上占配额)")
    rec = Recorder(claim_returns=[[TASK]], publish_returns={}, publish_status=500)
    asyncio.run(run_loop(rec, dry=True))
    check("E 有回执", len(rec.reports) == 1, f"{len(rec.reports)} 条")
    check("E result=submit_unknown(请求可能已送达,禁止重试)",
          rec.reports and rec.reports[0].get("result") == "submit_unknown",
          str(rec.reports[:1])[:160])

    # ── F: claim 404 is part of the protocol ─────────────────────────────────
    print("\nF claim 404 → 不当错误、不回执")
    rec = Recorder(claim_returns=[404], publish_returns={})
    asyncio.run(run_loop(rec, dry=True))
    check("F 没有调 /publish", not rec.publishes, str(rec.publishes))
    check("F 没有产生回执(没领到就没有什么要回执的)", not rec.reports, str(rec.reports))

    print()
    if _failures:
        print(f"[FAIL] {len(_failures)} 项未通过:")
        for f in _failures:
            print(f"  · {f}")
        return 1
    print("[PASS] claim → /publish → report 闭环全部通过。")
    print("       下一步是 F3 真实发布(需要:静态代理 + 代理出口上重新登录 + doc_id),")
    print("       那一步无法用 stub 代替。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
