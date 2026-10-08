"""Self-check of the irreversible boundary: after "publish was clicked", no ending may be judged retryable - without touching the platform.

**Why this script exists**: these branches are only reached in rare failures (client timeout, server timeout,
fallback except), so they can live with bugs for a long time - that is exactly how the first real publish on 2026-08-13 went wrong:
the worker client timed out at 30s -> reported crash (retryable) -> the queue row went back to queued, while the adapter was clicking publish.
One more claim and the same article would have been published twice, irreversibly.

What is checked is **the classification rules themselves**, without sending a single platform request:

  1) client timeout budget > the server's own budget        - otherwise the client always gives up first, hitting it every time
  2) httpx exceptions -> classified by "did the request get there" - cannot connect = retryable; everything after connecting = result unknown
  3) non-200 -> 4xx retryable / 5xx result unknown
  4) the commit trace survives asyncio cancel               - this is the premise of the whole defense
  5) with a trace, timeouts/crashes are reclassified as submit_unknown - publish_api side
  6) with a trace, the adapter's fallback except also reclassifies       - adapter side (its conclusion takes precedence and overrides 5)

    cd workers/service-cn && python publish_commit_guard_selfcheck.py
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

import httpx

_fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        _fails.append(label)


async def main() -> int:
    from app.publish_api import _unknown_or_timeout
    from app.publish_loop import _classify_client_error, _publish_client_timeout
    from app.publishers.base import PublishRequest
    from app.publishers.zhihu import ZhihuPublisher

    print("\n① 客户端超时预算")
    sem = float(os.environ.get("PUBLISH_SEMAPHORE_TIMEOUT", "120"))
    pub = float(os.environ.get("PUBLISH_TIMEOUT", "240"))
    t = _publish_client_timeout()
    check("客户端超时 > 服务端最坏预算", t > sem + pub,
          f"client={t:.0f}s server={sem + pub:.0f}s(客户端先放弃 = 两边对同一次发布"
          f"给出不同结论)")

    print("\n② 客户端异常分类(判据:平台有没有可能已被触碰)")
    for exc, want in (
        (httpx.ConnectError("x"), "crash"),
        (httpx.ConnectTimeout("x"), "crash"),
        (httpx.PoolTimeout("x"), "crash"),
        (httpx.ReadTimeout("x"), "submit_unknown"),      # 🔴 this is exactly what went wrong on 8-13
        (httpx.WriteTimeout("x"), "submit_unknown"),
        (httpx.RemoteProtocolError("x"), "submit_unknown"),
        (httpx.ReadError("x"), "submit_unknown"),
        (RuntimeError("完全没见过的异常"), "submit_unknown"),  # the default direction must be conservative
    ):
        got, _why = _classify_client_error(exc)
        check(f"{type(exc).__name__} → {want}", got == want, f"实际 {got}")

    print("\n③ 非 200 的分类(真跑 _publish_once,只把传输层换成假的)")
    # /publish deliberately returns 200 even for business failures, so non-200 has only two sources with opposite retryability:
    #   4xx = request blocked outside the flow (no browser even opened)   5xx = an ASGI-layer issue, possibly after publishing
    from app.publish_loop import _publish_once
    for code, want in ((400, "crash"), (401, "crash"), (429, "crash"),
                       (500, "submit_unknown"), (502, "submit_unknown")):
        transport = httpx.MockTransport(
            lambda _req, _c=code: httpx.Response(_c, text="x"))
        async with httpx.AsyncClient(transport=transport) as c:
            got = await _publish_once(c, "http://x", {}, {"dispatch_id": 1,
                                                          "platform": "zhihu"},
                                      True, 5.0)
        check(f"HTTP {code} → {want}", got["fail_type"] == want, str(got["fail_type"]))
    # On 200 copy the server's verdict - the worker may not reach its own conclusion when one exists.
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, json={"ok": False,
                                               "fail_type": "content_rejected"}))
    async with httpx.AsyncClient(transport=transport) as c:
        got = await _publish_once(c, "http://x", {}, {"dispatch_id": 1,
                                                      "platform": "zhihu"}, True, 5.0)
    check("HTTP 200 → 原样采信服务端结论", got["fail_type"] == "content_rejected",
          str(got["fail_type"]))

    print("\n④ commit 痕迹活过 cancel(整套防御的前提)")
    commit: dict = {}

    async def _clicks_then_hangs() -> dict:
        # Simulated adapter: publish was clicked, then it got stuck "waiting for the article URL" - the slowest part of the flow.
        PublishRequest(platform="zhihu", account_id="a", title="t", body="b",
                       commit=commit).mark_committed(draft_id="D123")
        await asyncio.sleep(30)
        return {}

    try:
        await asyncio.wait_for(_clicks_then_hangs(), timeout=0.2)
        check("wait_for 应当超时", False, "没超时,用例本身失效了")
    except asyncio.TimeoutError:
        check("协程被 cancel 后 commit 仍可读", commit.get("clicked") is True,
              f"commit={commit}(它由调用方持有,所以协程栈消失也不影响)")
        check("draft_id 也留下了", commit.get("draft_id") == "D123")

    print("\n⑤ publish_api:有痕迹 → 改判 submit_unknown")
    r = _unknown_or_timeout({}, "publish timed out", "点过了")
    check("无痕迹的超时仍是 timeout(可重试)", r["fail_type"] == "timeout", str(r["fail_type"]))
    r = _unknown_or_timeout(commit, "publish timed out", "超时且已点击")
    check("有痕迹的超时 → submit_unknown", r["fail_type"] == "submit_unknown")
    check("并带上 draft_id(对账唯一凭据)", r["draft_id"] == "D123")
    check("published_url 恒为空", r["published_url"] is None,
          "不变量:没观测到就不许有 URL")
    r = _unknown_or_timeout({}, "boom", "崩了", clean_fail_type="crash")
    check("无痕迹的崩溃仍是 crash(可重试)", r["fail_type"] == "crash")
    r = _unknown_or_timeout(commit, "boom", "崩了且已点击", clean_fail_type="crash")
    check("有痕迹的崩溃 → submit_unknown", r["fail_type"] == "submit_unknown")

    print("\n⑥ 适配器兜底 except:同样改判(它的结论会盖掉 ⑤)")
    pubr = ZhihuPublisher()
    clean = PublishRequest(platform="zhihu", account_id="a", title="t", body="b")
    res = pubr._unknown_if_committed(clean, "timeout", "publish timed out", None)
    check("没点过 → 仍是 timeout(可重试)", res.fail_type == "timeout", str(res.fail_type))
    dirty = PublishRequest(platform="zhihu", account_id="a", title="t", body="b")
    dirty.mark_committed(draft_id="D456", screenshot_path="s.png")
    res = pubr._unknown_if_committed(dirty, "crash", "RuntimeError: x", None)
    check("点过 → submit_unknown", res.fail_type == "submit_unknown", str(res.fail_type))
    check("draft_id 透传", res.draft_id == "D456")
    check("ok 恒 False", res.ok is False)
    check("published_url 恒为空", res.published_url is None)
    check("截图退化到 pre-commit 那张", res.screenshot_path == "s.png",
          "点击后截图失败时,点击前那一帧是唯一能回答「点下去之前页面对不对」的证据")

    print()
    if _fails:
        print(f"[FAIL] {len(_fails)} 项不过:")
        for f in _fails:
            print(f"  · {f}")
        print("\n🔴 每一项都对应「同一篇文章发两遍」的一条路径,不要绕过。")
        return 1
    print("[PASS] 不可撤回边界的分类规则全部成立(一条平台请求都没发)。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
