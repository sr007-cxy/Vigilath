"""Self-check of the creator center recheck: when is flipping submit_unknown to success allowed - without touching the platform.

**Why this script exists**: this code is only reached when "publish was clicked but the address bar did not switch", a rare failure
where an article may be live while the result is reported as submit_unknown.
Self-healing has now been added, but self-healing itself has a more dangerous failure mode: **judging something not published as published**. That would put
a nonexistent article into published, re-testing and the reward window, far worse than the original false negative.

So what is pinned down is the **decision table**, without sending a single platform request (page is a stub):

  1) draft_id matches an anchor on the list            -> return that href
  2) draft_id no match, title uniquely matches         -> return that row's href
  3) title matches multiple rows (same-titled article published twice) -> None, ambiguous, no guessing
  4) list readable but this article absent             -> None, anchors>0 (this is **negative evidence**)
  5) list page cannot be opened                        -> None, anchors=0 (this is **no evidence**, different from 4)
  6) neither draft_id nor a long enough title          -> None, and **not a single navigation is sent**
  7) 🔴 draft_id given but no matching href on the list -> None. **Never construct a URL from draft_id**
  8) tracking params on the href are trimmed, host/path untouched -> rewriting the host amounts to constructing
  9) url_source passed all the way through to the report request body -> the DB can tell "redirect observed" from "read from list"

    cd workers/service-cn && python publish_recheck_selfcheck.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))  # publish_reconcile_draft lives in scripts/ (moved 2026-09-18)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

_fails: list[str] = []

DRAFT_ID = "12345678901234567890"
TITLE = "Example article title"
HREF = f"https://zhuanlan.zhihu.com/p/{DRAFT_ID}"


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        _fails.append(label)


class StubContext:
    """The minimal context enough for `page.context.new_page()`.

    Reason for existing, see 11): the recheck **must navigate on a new page**, and "which page was used" can only be verified
    by having the stub account for the two pages separately. The old stub had no context, so the recheck always took the degraded branch,
    and the real failure (editor page goto -> ERR_ABORTED) had no corresponding path in the self-check at all.
    """

    def __init__(self, owner: "StubPage"):
        self._owner = owner

    async def new_page(self):
        if self._owner.new_page_raises:
            raise RuntimeError("no more pages")
        p = StubPage(self._owner._anchors, goto_raises=self._owner._goto_raises,
                     body_text=self._owner._body_text,
                     edit_hrefs=self._owner._edit_hrefs,
                     close_raises=self._owner.close_raises)
        self._owner.opened.append(p)
        return p


class StubPage:
    """The minimal page enough for find_published_url_in_creator_center.

    screenshot deliberately throws: the adapter's self.screenshot swallows it and returns None -
    the self-check should not write images to disk, and "a screenshot failure does not fail the publish" is itself behavior that should be covered.
    """

    def __init__(self, anchors: list[dict], goto_raises: bool = False,
                 body_text: str = "", edit_hrefs: list[str] | None = None,
                 new_page_raises: bool = False, close_raises: bool = False):
        self._anchors = anchors
        self._goto_raises = goto_raises
        self._body_text = body_text
        self._edit_hrefs = edit_hrefs or []
        self.new_page_raises = new_page_raises
        self.close_raises = close_raises
        self.gotos: list[str] = []
        self.listeners: list = []
        self.removed: list = []
        self.opened: list["StubPage"] = []
        self.closed = False
        self.url = "https://zhuanlan.zhihu.com/p/x/edit"
        self.context = StubContext(self)

    async def goto(self, url, **kw):
        self.gotos.append(url)
        if self._goto_raises:
            raise TimeoutError("Timeout 20000ms exceeded")
        return None

    async def close(self):
        if self.close_raises:
            raise RuntimeError("page already closed")
        self.closed = True

    async def wait_for_timeout(self, ms):
        return None

    async def inner_text(self, selector):
        return self._body_text

    async def eval_on_selector_all(self, selector, expr):
        # The drafts query asks for /edit links, the content list asks for /p/ anchors - answer them separately.
        if "/edit" in selector:
            return list(self._edit_hrefs)
        return list(self._anchors)

    async def screenshot(self, **kw):
        raise RuntimeError("自检不落图")

    def on(self, event, fn):
        self.listeners.append((event, fn))

    def remove_listener(self, event, fn):
        self.removed.append((event, fn))


async def main() -> int:
    from app.publish_loop import _report_body
    from app.publishers.base import PublishResult
    from app.publishers.zhihu import ZhihuPublisher

    pub = ZhihuPublisher()
    find = pub.find_published_url_in_creator_center

    print("\n① draft_id 命中 → 返回列表上那个 href")
    page = StubPage([{"href": HREF, "text": TITLE},
                     {"href": "https://zhuanlan.zhihu.com/p/111", "text": "别的文章"}])
    r = await find(page, draft_id=DRAFT_ID, title=TITLE)
    check("返回的就是列表上那个 href", r["url"] == HREF, f"url={r['url']}")
    check("note 说清了凭什么命中", "draft_id" in (r["note"] or ""), r["note"])
    check("编辑器页一次都没被导航(见 ⑪)", page.gotos == [], f"gotos={page.gotos}")

    print("\n② draft_id 没命中,但标题在列表里唯一匹配")
    page = StubPage([{"href": HREF, "text": TITLE},
                     {"href": "https://zhuanlan.zhihu.com/p/111", "text": "别的文章"}])
    r = await find(page, draft_id="9999999999", title=TITLE)
    check("退到标题匹配并返回那一行的 href", r["url"] == HREF, f"url={r['url']}")
    check("note 写明是标题命中的", "标题特征" in (r["note"] or ""), r["note"])

    print("\n③ 标题匹配到多行 → 有歧义,不猜")
    page = StubPage([{"href": HREF, "text": TITLE},
                     {"href": "https://zhuanlan.zhihu.com/p/222", "text": TITLE}])
    r = await find(page, draft_id=None, title=TITLE)
    check("不返回 URL", r["url"] is None, f"url={r['url']}")
    check("note 点出歧义", "歧义" in (r["note"] or ""), r["note"])

    print("\n④ 列表可读但没这篇 → 负证据(anchors>0)")
    page = StubPage([{"href": "https://zhuanlan.zhihu.com/p/111", "text": "别的文章"}])
    r = await find(page, draft_id=DRAFT_ID, title=TITLE)
    check("不返回 URL", r["url"] is None)
    check("anchors>0 —— 调用方据此知道'看过了,确实没有'", r["anchors"] > 0,
          f"anchors={r['anchors']}")

    print("\n⑤ 列表页打不开 → 没证据(anchors=0),与 ④ 必须分得开")
    page = StubPage([{"href": HREF, "text": TITLE}], goto_raises=True)
    r = await find(page, draft_id=DRAFT_ID, title=TITLE)
    check("不返回 URL", r["url"] is None)
    check("anchors=0", r["anchors"] == 0)
    check("note 说的是'打不开'而不是'没有'", "打不开" in (r["note"] or ""), r["note"])
    check("导航失败也把新开的页关掉", page.opened and page.opened[0].closed)

    print("\n⑥ 既无 draft_id 又标题太短 → 一次导航都不该发")
    page = StubPage([{"href": HREF, "text": TITLE}])
    r = await find(page, draft_id=None, title="AI")
    check("不返回 URL", r["url"] is None)
    check("没有发生任何导航", page.gotos == [], f"gotos={page.gotos}")

    print("\n⑦ 🔴 核心不变量:给了 draft_id 但列表上没有它 → 绝不凭 draft_id 拼")
    page = StubPage([{"href": "https://zhuanlan.zhihu.com/p/333", "text": "无关"}])
    r = await find(page, draft_id=DRAFT_ID, title=None)
    check("不返回 URL", r["url"] is None, f"url={r['url']}")
    check("返回值里不含 draft_id 拼出来的地址", DRAFT_ID not in str(r.get("url") or ""))
    page = StubPage([])
    r = await find(page, draft_id=DRAFT_ID, title=None)
    check("空列表同样不许拼", r["url"] is None, f"url={r['url']}")

    print("\n⑧ 埋点参数裁掉,host/path 一个字都不动")
    page = StubPage([{"href": f"{HREF}?utm_source=x&hash=y#comment", "text": TITLE}])
    r = await find(page, draft_id=DRAFT_ID, title=TITLE)
    check("query/hash 被裁掉", r["url"] == HREF, f"url={r['url']}")
    # Site-relative links get resolved by the browser to www.zhihu.com/p/<id> - also a real address, returned as-is.
    www = f"https://www.zhihu.com/p/{DRAFT_ID}"
    page = StubPage([{"href": www, "text": TITLE}])
    r = await find(page, draft_id=DRAFT_ID, title=TITLE)
    check("www 形态原样返回,不改写成 zhuanlan", r["url"] == www, f"url={r['url']}")
    check("但要挂一句提醒", "形态不是" in (r["note"] or ""), r["note"])
    page = StubPage([{"href": "https://www.zhihu.com/people/abc", "text": TITLE}])
    r = await find(page, draft_id=None, title=TITLE)
    check("href 里没有 /p/<id> 的一律不收", r["url"] is None, f"url={r['url']}")

    print("\n⑨ url_source 从适配器一路走到 report 请求体")
    d = PublishResult(ok=True, published_url=HREF, draft_id=DRAFT_ID,
                      url_source="creator_center_anchor").as_dict()
    check("PublishResult.as_dict 带 url_source",
          d.get("url_source") == "creator_center_anchor")
    body = _report_body({"dispatch_id": 1}, d, True, 757280931)
    check("_report_body 透传 url_source",
          body.get("url_source") == "creator_center_anchor")
    check("published_url 也在", body.get("published_url") == HREF)
    try:
        backend = Path(__file__).resolve().parent.parent.parent.parent / "backend"
        sys.path.insert(0, str(backend))
        from geo.api.publish_dispatch import ReportIn
        rin = ReportIn(**{k: v for k, v in body.items() if v is not None})
        check("后端 ReportIn 收得下 url_source",
              rin.url_source == "creator_center_anchor")
    except Exception as e:  # noqa: BLE001
        print(f"  [SKIP] 后端 ReportIn 校验跳过({type(e).__name__}: {e})")

    print("\n⑩ 草稿箱信号:🔴 不许拿 page.content() 匹配 draft_id")
    from publish_reconcile_draft import _in_draft_box
    # Real scenario (measured 2026-08-14): the article was live, the drafts page showed 「暂无内容」 ("No content"), while the HTML
    # still carried this id (SPA hydration data). Looking at content() would judge it "still a draft" -> someone republishes it.
    page = StubPage([], body_text="草稿箱() 回答草稿 文章草稿 暂无内容", edit_hrefs=[])
    still, note = await _in_draft_box(page, DRAFT_ID, TITLE, None)
    check("空草稿箱 → still_draft=False", still is False, f"{still} / {note}")
    page = StubPage([], body_text="草稿箱(1)",
                    edit_hrefs=[f"https://zhuanlan.zhihu.com/p/{DRAFT_ID}/edit"])
    still, note = await _in_draft_box(page, DRAFT_ID, TITLE, None)
    check("草稿行里有它的 /edit 链接 → True", still is True, f"{still} / {note}")
    page = StubPage([], body_text="草稿箱(1)",
                    edit_hrefs=["https://zhuanlan.zhihu.com/p/999/edit"])
    still, note = await _in_draft_box(page, DRAFT_ID, TITLE, None)
    check("有别人的草稿但没有它 → False", still is False, f"{still} / {note}")
    page = StubPage([], body_text="创作中心 内容管理", edit_hrefs=[])
    still, note = await _in_draft_box(page, DRAFT_ID, TITLE, None)
    check("既无条目也无空态 → None(读不出不等于没有)", still is None, f"{still} / {note}")
    page = StubPage([], body_text="", goto_raises=True)
    still, note = await _in_draft_box(page, DRAFT_ID, TITLE, None)
    check("草稿箱页打不开 → None", still is None, f"{still} / {note}")

    print("\n⑪ 🔴 回查必须在**新开的页**上导航,不能动编辑器页")
    # On 2026-08-14 two real publishes hit it twice in a row: goto to the creator center from /p/<id>/edit ->
    # `net::ERR_ABORTED` (the editor's beforeunload; an accept listener does not help). And this method is called **only**
    # while on the editor page, meaning the second observation point fails 100% in the only scenario that needs it -
    # both live articles reported submit_unknown. The old self-check had no context, so not a single case went red.
    page = StubPage([{"href": HREF, "text": TITLE}])
    r = await find(page, draft_id=DRAFT_ID, title=TITLE)
    check("仍然读到 URL", r["url"] == HREF, f"url={r['url']}")
    check("编辑器页零导航(它一导航就 ERR_ABORTED)", page.gotos == [], f"{page.gotos}")
    check("新开了一页并在它上面导航",
          len(page.opened) == 1 and page.opened[0].gotos == [
              "https://www.zhihu.com/creator/manage/creation/article"],
          f"opened={len(page.opened)}")
    check("编辑器页上没挂 dialog 监听(不走那条路就不该动它)", page.listeners == [])
    check("新页用完就关(context 里不留脏页)", page.opened[0].closed)

    print("\n⑪b 新页开不出来 → 退回原页导航,并说明")
    page = StubPage([{"href": HREF, "text": TITLE}], new_page_raises=True)
    r = await find(page, draft_id=DRAFT_ID, title=TITLE)
    check("退化路径仍在原页导航", page.gotos == [
        "https://www.zhihu.com/creator/manage/creation/article"], f"{page.gotos}")
    check("退化路径才挂 dialog accept 监听,并摘干净",
          len(page.listeners) == 1 and len(page.removed) == 1)
    check("note 里说明退化了", "退回当前页" in (r["note"] or ""), r["note"])

    print("\n⑪c 关页失败不许把一次成功的观测变成异常")
    page = StubPage([{"href": HREF, "text": TITLE}], close_raises=True)
    r = await find(page, draft_id=DRAFT_ID, title=TITLE)
    check("URL 照样返回", r["url"] == HREF, f"url={r['url']}")

    print()
    if _fails:
        print(f"[FAIL] {len(_fails)} 项不过:")
        for f in _fails:
            print(f"   · {f}")
        return 1
    print("[OK] 回查判定表全绿 —— 只在**列表上真读到**那条 href 时才翻成成功。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
