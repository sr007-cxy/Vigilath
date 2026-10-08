"""Paid publishing "is the link still alive" checks + automatic appeal refunds within the validity period (Ruanwenjiang + Meijieku).

**Why it exists**: paid articles get taken down by media -- link 404s, or the page is still there but the article was emptied /
replaced with other content. The platform explicitly states "default minimum article validity is 30 days"; within that period you can "apply for
after-sales" on the member site (the reason allowlist includes 「时效内打不开链接」 ("link won't open within validity") and 「内容被删」 ("content deleted")), refunded via the original route once accepted;
after the window the platform no longer accepts. Clicking through by hand isn't realistic, so it's a scheduled check.

Verdict chain (conservative; prefer missing over misjudging -- a wrong appeal hurts account reputation):
  1. Fetch published_url and compare against **the platform-archived title / body** (fingerprint from `/Article/{t}/info`,
     not our local draft -- media often change titles; the platform archive is the version actually published)
  2. Fingerprint hit = alive; 404/410 or unreachable = won't open; 200 but fingerprint gone = content deleted
  3. 403/429/5xx, and "empty shell pages with almost no body" (usually JS rendering or anti-scraping) are always
     unknown, **not accumulated** toward death count, left to humans
  4. Appeal only after `RWJ_LIVENESS_STRIKES` consecutive dead verdicts (spaced >= `RWJ_LIVENESS_RECHECK_HOURS`)

Double gate: `RWJ_LIVENESS_ENABLED` controls checks (read-only, writes meta), `RWJ_APPEAL_ENABLED` controls appeals
(external action, **off by default**). With the gate closed it only outputs a list; ops reviews before enabling.

Idempotency: each task is appealed only once (skip if `channel_meta.appeal` exists); ticket status is checked back via
`/Aftersales/list`; platform "approved" = refunded -> task set to failed, and ops can place with another outlet.

Mounting: content_scheduler.attach runs it daily; in prod with no scheduler leader,
crontab calls `python -m geo.services.rwj_liveness`.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from geo.database import SessionLocal, settings
from geo.models.ai_telemetry import TopicDocPublishTaskORM, TopicGeneratedDocORM
from geo.services import compliance_audit
from geo.services.ruanwenjiang_publisher import (
    RwjNotConfigured, find_article, provider_of,
)
from geo.services.rwj_client import (
    DEFAULT_PROVIDER, SITES, RwjApiError, get_client, site,
)

log = logging.getLogger(__name__)

# ── Verdicts ────────────────────────────────────────────────────────────────
ALIVE = "alive"              # fingerprint still there, article alive
DEAD_HTTP = "dead_http"      # unreachable / 404 / 410 -- link won't open
DEAD_GONE = "dead_gone"      # 200 but our article is no longer on the page -- content deleted
UNKNOWN = "unknown"          # anti-scraping / transient failure / JS-rendered shell; doesn't count

_DEAD = (DEAD_HTTP, DEAD_GONE)

# Appeal reasons must be verbatim platform allowlist text (RwjClient.APPEAL_REASONS); this only maps verdict -> reason
_APPEAL_REASON = {DEAD_HTTP: "时效内打不开链接", DEAD_GONE: "内容被删"}

# Max length of the appeal description (platform column capacity is limited; overflow is rejected with an SQL error)
_APPEAL_INFO_MAX = 100

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Common notices for "page still there but content gone" (hit + fingerprint not found -> content deleted)
_SOFT_GONE = ("页面不存在", "内容不存在", "文章不存在", "信息不存在", "已被删除",
              "内容已删除", "信息已删除", "已下线", "已过期", "页面走丢",
              "404", "not found", "page not found")

# Anti-scraping challenge page signatures (Ruishu / Jiasule / CF etc.): page returns 200 with thousands of chars, but all script noise.
# Without recognizing them, links that "actually open" get judged content-deleted -- measured on lieju.com exactly so.
_CHALLENGE = ("arg1=", "acw_sc__", "_0x", "document.cookie", "seccode", "captcha",
              "安全验证", "滑动验证", "just a moment", "cloudflare", "请开启 javascript",
              "enable javascript")

# Cloud protection / WAF block pages (measured: Xianning News returned 502 + 「您的IP: … Event ID: …-waf05fst」 ("Your IP: ... Event ID: ...")):
# they block our egress IP and say nothing about whether the article is alive; must be unknown
_WAF = ("云防护", "waf", "您的ip:", "event id:", "访问被拒绝", "安全狗", "接入拒绝",
        "attention required", "access denied")

_CJK_RE = re.compile(r"[一-鿿]")

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s,，。、!！?？:：;；\"'“”‘’()（）【】\[\]<>《》—\-_/\\|~·.]+")


def _plain(html: str) -> str:
    """HTML -> plain text (strip scripts, styles and tags, collapse whitespace)."""
    body = _TAG_RE.sub(" ", html or "")
    body = _ANY_TAG_RE.sub(" ", body)
    return _WS_RE.sub(" ", body).strip()


def _key(text: str, n: int = 10) -> str:
    """Take a fingerprint: first n chars after stripping punctuation and whitespace (media often add their own prefix/suffix to titles)."""
    return _PUNCT_RE.sub("", text or "")[:n]


def _hit(html: str, fps: list[str]) -> bool:
    """Whether the fingerprint is on the page.

    **Both sides must have punctuation stripped before comparing**: the fingerprint is stripped; if page text isn't, a single
    question mark / dash in the title (Chinese titles almost always have one) means it never matches -- that exact bug in the first measured round
    judged pages on WeChat, Weibo and b2b168 where "the article was perfectly fine" as content-deleted.
    """
    if not fps:
        return False
    flat = _PUNCT_RE.sub("", _plain(html))
    return any(fp and fp in flat for fp in fps)


def _canon(url: str) -> str:
    """Normalize article links in tickets (same metric definition as publish_task_service dedupe)."""
    from geo.services.citation_match import canonicalize
    return canonicalize(url or "") or ""


def _meta_of(task: TopicDocPublishTaskORM) -> dict:
    try:
        m = json.loads(task.channel_meta_json or "{}")
        return m if isinstance(m, dict) else {}
    except ValueError:
        return {}


def _save_meta(task: TopicDocPublishTaskORM, meta: dict) -> None:
    task.channel_meta_json = json.dumps(meta, ensure_ascii=False, default=str)


# ── Fingerprint (platform-archived title + body snippet) ──────────────────────────────────────

def fingerprints(task: TopicDocPublishTaskORM, meta: dict, doc: Optional[TopicGeneratedDocORM],
                 *, provider: str, type_: str) -> list[str]:
    """Feature strings that should be visible on the page for this article; cached into channel_meta once computed.

    Prefer the platform archive (the version media actually published); if unavailable fall back to our local draft's title.
    """
    cached = meta.get("liveness_fp")
    if isinstance(cached, list) and cached:
        return [str(x) for x in cached if x]

    fps: list[str] = []
    article_id = meta.get("article_id")
    if article_id:
        try:
            info = get_client(provider).article_info(type_, int(article_id))
        except (RwjApiError, httpx.HTTPError) as e:  # noqa: BLE001
            log.warning("[rwj-liveness] task=%s failed to fetch platform archive: %s", task.id, e)
            info = {}
        if isinstance(info, dict):
            k = _key(str(info.get("title") or ""))
            if k:
                fps.append(k)
            body = _plain(str(info.get("content") or ""))
            if len(body) > 120:
                # Take the middle of the body: media often add a lead/disclaimer at the start; the middle is most stable
                mid = len(body) // 3
                seg = _key(body[mid:mid + 40], 16)
                if len(seg) >= 8:
                    fps.append(seg)
    if not fps and doc is not None:
        k = _key(doc.title or "")
        if k:
            fps.append(k)
    if fps:
        meta["liveness_fp"] = fps
        _save_meta(task, meta)
    return fps


# ── Fetch verdict ────────────────────────────────────────────────────────────────

def probe(url: str, fps: list[str], *, timeout: Optional[float] = None) -> dict:
    """Fetch the page once and judge liveness. Returns {verdict, http_status, reason, text_len}.

    Read-only, no DB changes; unit tests can feed fps directly to verify verdict logic.
    """
    out: dict[str, Any] = {"verdict": UNKNOWN, "http_status": None, "reason": "", "text_len": 0}
    try:
        # trust_env=False: articles are mostly on CN media sites and must not be thrown to the US via the SS egress proxy (same handling as rwj_client)
        with httpx.Client(follow_redirects=True, trust_env=False,
                          timeout=timeout or settings.RWJ_LIVENESS_TIMEOUT,
                          headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}) as c:
            r = c.get(url)
    except httpx.HTTPError as e:
        out.update(verdict=DEAD_HTTP, reason=f"请求失败:{type(e).__name__}")
        return out

    out["http_status"] = r.status_code
    out.update(judge(r.text, fps, r.status_code))
    return out


def judge(html: str, fps: list[str], status: int, *, prefer_content: bool = False) -> dict:
    """Pure verdict (no network): (page HTML, fingerprint, status code) -> {verdict, reason, text_len}.

    Misjudgment costs are asymmetric -- wrongly "alive" only misses one refund; wrongly "dead" files a bogus after-sales claim on the platform,
    hurting account reputation. So any case where "the real page couldn't be fetched" is UNKNOWN, handed to humans.

    prefer_content: used by the browser recheck path. Measured: some sites (Lanse Hepan, Discuz) return status 403
    but still render the full article -- in that case the rendered result is more trustworthy than the status code.
    """
    out: dict[str, Any] = {"verdict": UNKNOWN, "reason": "", "text_len": 0}
    if prefer_content and _hit(html, fps):
        return {"verdict": ALIVE, "reason": f"指纹命中(渲染后,HTTP {status})",
                "text_len": len(_plain(html))}
    if status in (401, 403, 429):
        # Login required / blocked by anti-scraping -- usually opens in a browser; can't judge dead
        out.update(verdict=UNKNOWN, reason=f"HTTP {status}(需登录或被反爬拦截)")
        return out
    if status >= 400:
        # 404/410 means deleted, 5xx means the site is broken -- to the customer both are "won't open", both count as dead
        out.update(verdict=DEAD_HTTP, reason=f"HTTP {status}")
        return out

    text = _plain(html)
    out["text_len"] = len(text)
    if _hit(html, fps):
        out.update(verdict=ALIVE, reason="指纹命中")
        return out
    low = text[:4000].lower()
    if any(w in low for w in _WAF):
        # Cloud protection/WAF blocked our egress IP -- unrelated to whether the article is alive
        out.update(verdict=UNKNOWN, reason="被站点云防护拦截(与文章是否还在无关)")
        return out
    if any(w in low for w in _SOFT_GONE):        # an explicit "content doesn't exist" is strong evidence; accept it first
        out.update(verdict=DEAD_GONE, reason="页面提示内容不存在/已删除")
        return out
    if any(w in low for w in _CHALLENGE) or len(_CJK_RE.findall(text[:3000])) < 30:
        # Script challenge page / almost no Chinese body -- real page not fetched, hand to humans
        out.update(verdict=UNKNOWN, reason="疑似反爬挑战页或脚本渲染,抓不到正文")
        return out
    if len(text) < 300:
        out.update(verdict=UNKNOWN, reason=f"页面几乎没有正文({len(text)} 字)")
        return out
    if not fps:
        out.update(verdict=UNKNOWN, reason="没有可用指纹,无法判定")
        return out
    out.update(verdict=DEAD_GONE, reason="页面有内容但找不到本文标题/正文")
    return out


def _verdict_of(html: str, fps: list[str], status: int) -> str:
    """Short form of the verdict chain, for self-check scripts."""
    return judge(html, fps, status)["verdict"]


# ── Browser recheck (second stage when plain HTTP can't fetch) ──────────────────────────────────
#
# First measured round: 63 of 95 were unknown via plain HTTP -- Lieju / 51sole are Ruishu challenge pages,
# Lanse Hepan 403, Baijiahao redirects to a captcha, Shangye Xinzhi returns 427. All open fine in a browser; without rechecks
# two thirds of placements can't be checked. Rechecks only run on the unknown ones, so the volume is small.

_browser: dict[str, Any] = {"pw": None, "browser": None, "ctx": None, "broken": False}


def _browser_ctx():
    """Reuse one headless chromium per process; if it can't start, permanently degrade (no repeated retries)."""
    if _browser["broken"]:
        return None
    if _browser["ctx"] is not None:
        return _browser["ctx"]
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        _browser.update(pw=pw, browser=browser,
                        ctx=browser.new_context(user_agent=_UA, locale="zh-CN"))
    except Exception as e:  # noqa: BLE001
        log.warning("[rwj-liveness] browser recheck unavailable (%s), using plain HTTP verdicts only", e)
        _browser["broken"] = True
        return None
    return _browser["ctx"]


def close_browser() -> None:
    for key in ("browser", "pw"):
        obj = _browser.get(key)
        if obj is not None:
            try:
                obj.close() if key == "browser" else obj.stop()
            except Exception:  # noqa: BLE001
                pass
    _browser.update(pw=None, browser=None, ctx=None)


def probe_browser(url: str, fps: list[str]) -> Optional[dict]:
    """Fetch again with a real browser (judge after rendering). Returns None if the browser is unavailable."""
    ctx = _browser_ctx()
    if ctx is None:
        return None
    page = ctx.new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded",
                         timeout=int((settings.RWJ_LIVENESS_TIMEOUT + 10) * 1000))
        page.wait_for_timeout(2500)          # wait for the JS challenge page to redirect on its own
        html = page.content()
        status = resp.status if resp is not None else 0
    except Exception as e:  # noqa: BLE001
        return {"verdict": UNKNOWN, "http_status": None,
                "reason": f"浏览器复核失败:{type(e).__name__}", "text_len": 0}
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
    out = {"http_status": status}
    out.update(judge(html, fps, status, prefer_content=True))
    out["reason"] = f"{out['reason']}(浏览器复核)"
    return out


# ── Single-task check ──────────────────────────────────────────────────────────────

def check_task(db: Session, task: TopicDocPublishTaskORM, *,
               appeal: Optional[bool] = None, force: bool = False,
               tickets: Optional[tuple[set, set]] = None,
               actor_id: Optional[int] = None) -> dict:
    """Check one published task: fetch link -> accumulate death count -> appeal when threshold reached and gate open.

    Does not commit; follows the caller's transaction. Returns {action, detail, verdict}.
    """
    url = (task.published_url or "").strip()
    if not url:
        return {"action": "skip", "detail": "没有已回填的链接", "verdict": UNKNOWN}

    meta = _meta_of(task)
    provider = provider_of(task.platform) or DEFAULT_PROVIDER
    type_ = str(meta.get("type") or "Website")
    doc = db.get(TopicGeneratedDocORM, task.doc_id)
    live = meta.get("liveness") if isinstance(meta.get("liveness"), dict) else {}

    # Check interval: no need to fetch the same one repeatedly
    last = None if force else live.get("checked_at")
    if last:
        try:
            gap = datetime.utcnow() - datetime.fromisoformat(str(last))
            if gap < timedelta(hours=settings.RWJ_LIVENESS_RECHECK_HOURS):
                return {"action": "skip", "detail": "距上次巡检不足间隔", "verdict": live.get("verdict") or UNKNOWN}
        except ValueError:
            pass

    fps = fingerprints(task, meta, doc, provider=provider, type_=type_)
    result = probe(url, fps)
    # Whenever plain HTTP doesn't say "alive", confirm once more with a browser -- slow, but every dead verdict deserves a recheck:
    # measured Shangye Xinzhi returns 427 to bare HTTP and Lanse Hepan 403, yet both open fine in a browser
    if result["verdict"] != ALIVE and settings.RWJ_LIVENESS_BROWSER:
        again = probe_browser(url, fps)
        if again is not None:
            result = again
    verdict = result["verdict"]

    strikes = int(live.get("strikes") or 0)
    strikes = strikes + 1 if verdict in _DEAD else (0 if verdict == ALIVE else strikes)
    history = live.get("history") if isinstance(live.get("history"), list) else []
    history.append({"at": datetime.utcnow().isoformat(timespec="seconds"),
                    "verdict": verdict, "http": result["http_status"], "reason": result["reason"]})
    meta["liveness"] = {
        "checked_at": datetime.utcnow().isoformat(timespec="seconds"),
        "verdict": verdict, "http_status": result["http_status"],
        "reason": result["reason"], "text_len": result["text_len"],
        "strikes": strikes,
        "first_dead_at": live.get("first_dead_at") or (
            datetime.utcnow().isoformat(timespec="seconds") if verdict in _DEAD else None),
        "history": history[-5:],
    }
    if verdict == ALIVE:
        meta["liveness"]["first_dead_at"] = None
    _save_meta(task, meta)

    if verdict not in _DEAD:
        # alive / unknown counted separately -- unknown means "couldn't fetch, needs a human"; don't mix it into normal
        return {"action": verdict, "detail": result["reason"], "verdict": verdict}

    detail = f"{result['reason']}(连续 {strikes} 次)"
    if strikes < settings.RWJ_LIVENESS_STRIKES:
        return {"action": "suspect", "detail": detail, "verdict": verdict}

    # ── Death threshold reached: record audit, then see whether we can appeal ──
    if meta.get("appeal"):
        return {"action": "appealed_before", "detail": "已申诉过,等平台处理", "verdict": verdict}

    days = _age_days(task)
    window = settings.RWJ_APPEAL_WINDOW_DAYS
    if days is not None and days > window:
        return {"action": "expired", "detail": f"发布已 {days} 天,超过 {window} 天售后窗口,平台不受理",
                "verdict": verdict}

    # The platform already has an after-sales ticket for this order (most likely filed manually) -- don't file again; record it as appealed
    if tickets is not None:
        orders, links = tickets
        if (str(meta.get("order_no") or "") in orders and orders) or _canon(url) in links:
            meta["appeal"] = {"at": datetime.utcnow().isoformat(timespec="seconds"),
                              "title": _APPEAL_REASON[verdict], "url": url,
                              "source": "平台上已有工单(未重复提交)", "ticket_status": 0}
            _save_meta(task, meta)
            return {"action": "ticket_exists", "detail": "平台上已有这一单的售后工单,不重复提",
                    "verdict": verdict}

    allow = settings.RWJ_APPEAL_ENABLED if appeal is None else bool(appeal)
    if not allow:
        return {"action": "appeal_pending", "detail": f"{detail};申诉闸未开(RWJ_APPEAL_ENABLED=0)",
                "verdict": verdict}

    article_id = meta.get("article_id") or _lookup_article_id(task, meta, doc, provider, type_)
    if not article_id:
        return {"action": "appeal_blocked", "detail": "平台侧查不到本条订单编号,无法申诉",
                "verdict": verdict}

    reason = _APPEAL_REASON[verdict]
    # The platform's buyer_info column has a length limit (measured: a long Toutiao link immediately gives
    # `Data too long for column 'buyer_info'`), and the ticket already carries article_link,
    # so the description needn't repeat the link -- keep it short with a clear rationale
    note = (f"系统巡检连续 {strikes} 次打不开({result['reason']},"
            f"最近检测 {_cn_now()});发布 {days if days is not None else '?'} 天,"
            f"在时效内,请核实退款。")[:_APPEAL_INFO_MAX]
    try:
        resp = get_client(provider).appeal(type_, article_id=int(article_id),
                                           title=reason, info=note)
    except RwjApiError as e:
        task.last_error = f"售后申诉提交失败(平台返回:{str(e)[:120]}),稍后重试"
        return {"action": "appeal_failed", "detail": str(e), "verdict": verdict}

    meta["appeal"] = {"at": datetime.utcnow().isoformat(timespec="seconds"),
                      "title": reason, "info": note, "article_id": int(article_id),
                      "url": url,
                      "resp": resp if isinstance(resp, (dict, list, str, int)) else str(resp),
                      "ticket_status": 0}
    _save_meta(task, meta)
    compliance_audit.record(
        db, topic_id=task.topic_id, event_type="paid_publish",
        object_type="publish_task", object_id=task.id,
        actor_type="system" if actor_id is None else "admin", actor_id=actor_id,
        actor_name=f"{site(provider).label}售后",
        detail={"action": "appeal_submitted", "reason": reason, "url": url,
                "verdict": verdict, "strikes": strikes},
    )
    return {"action": "appealed", "detail": f"已提交申诉:{reason}", "verdict": verdict}


def _age_days(task: TopicDocPublishTaskORM) -> Optional[int]:
    when = task.actual_published_at or task.verified_at or task.created_at
    if when is None:
        return None
    return max(0, (datetime.utcnow() - when.replace(tzinfo=None)).days)


def _cn_now() -> str:
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def _lookup_article_id(task: TopicDocPublishTaskORM, meta: dict,
                       doc: Optional[TopicGeneratedDocORM], provider: str,
                       type_: str) -> Optional[int]:
    """Old tasks have no article_id in meta -- query the platform once by "title + this task's outlet"."""
    if doc is None:
        return None
    title = (doc.title or doc.source_query_text or f"稿件 #{doc.id}").strip()
    res_id = meta.get("resource_id")
    try:
        receipt = find_article(
            type_, title, provider=provider,
            resource_id=int(res_id) if res_id else None,
            resource_title=str(meta.get("resource_title") or task.media_name or "") or None,
            order_no=str(meta.get("order_no") or "") or None,
            out_order_no=str(meta.get("out_order_no") or "") or None,
        )
    except (RwjApiError, RwjNotConfigured, httpx.HTTPError):  # noqa: BLE001
        return None
    aid = (receipt or {}).get("article_id")
    if aid:
        meta["article_id"] = int(aid)
        _save_meta(task, meta)
        return int(aid)
    return None


# ── After-sales ticket check-back ────────────────────────────────────────────────────────────

def open_tickets(provider: str) -> tuple[set, set]:
    """After-sales tickets already on the platform: (set of order numbers, set of article links).

    **Humans also file after-sales on the member site** (measured: 4 on Ruanwenjiang were filed by hand); our idempotency only checks
    our own `channel_meta.appeal`; without checking this we'd file twice for the same order -- which looks like fraud on the platform side.
    """
    orders: set[str] = set()
    links: set[str] = set()
    c = get_client(provider)
    if not c.configured:
        return orders, links
    for type_ in ("Website", "Wemedia"):
        page = 1
        while page <= 10:
            try:
                data = c.aftersales(type_, pageNo=page, pageSize=50)
            except RwjApiError as e:  # noqa: BLE001
                log.warning("[rwj-liveness] %s/%s can't fetch ticket list: %s", provider, type_, e)
                break
            rows = (data.get("data") if isinstance(data, dict) else data) or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("order_id"):
                    orders.add(str(row["order_id"]))
                link = row.get("article_link")
                if isinstance(link, str) and link.startswith("http"):
                    links.add(_canon(link))
            if not rows or page >= int((data or {}).get("last_page") or 1):
                break
            page += 1
    return orders, links


def sync_tickets(db: Session, *, provider: str) -> int:
    """Pull after-sales tickets once and write status back to appealed tasks; approved (refunded) ones are set to failed.

    Platform tickets: type 1=refund 2=replacement link 3=revision; status 0=pending 1=approved 2=rejected;
    after acceptance `seller_info` reads 「已退款:X元」 ("refunded: X CNY").

    **Ticket rows have no article_id** (measured: only order_id / article_link / article_title /
    resource_title) -- matching by article_id never works, so fall back in three levels: order number -> article link ->
    (title, outlet).
    """
    label = site(provider).label
    tasks = (db.query(TopicDocPublishTaskORM)
               .filter(TopicDocPublishTaskORM.platform == label,
                       TopicDocPublishTaskORM.channel_meta_json.like('%"appeal"%'))
               .all())
    if not tasks:
        return 0
    by_order: dict[str, dict] = {}
    by_link: dict[str, dict] = {}
    c = get_client(provider)
    for type_ in ("Website", "Wemedia"):
        page = 1
        while page <= 10:
            data = c.aftersales(type_, pageNo=page, pageSize=50)
            rows = (data.get("data") if isinstance(data, dict) else data) or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("order_id"):
                    by_order[str(row["order_id"])] = row
                link = row.get("article_link")
                if isinstance(link, str) and link.startswith("http"):
                    by_link[_canon(link)] = row
            if not rows or page >= int((data or {}).get("last_page") or 1):
                break
            page += 1

    changed = 0
    for task in tasks:
        meta = _meta_of(task)
        appeal = meta.get("appeal")
        if not isinstance(appeal, dict):
            continue
        row = by_order.get(str(meta.get("order_no") or ""))
        if row is None:
            # The appeal submitted that exact link at the time; reverse lookup by it is most reliable
            row = by_link.get(_canon(str(appeal.get("url") or task.published_url or "")))
        if row is None:
            continue
        status = row.get("status")
        if appeal.get("ticket_status") == status:
            continue
        appeal["ticket_status"] = status
        appeal["ticket_type"] = row.get("type")
        appeal["seller_info"] = row.get("seller_info")
        _save_meta(task, meta)
        changed += 1
        if str(status) == "1":       # approved = platform refunded
            task.status = "failed"
            task.last_error = ("链接已失效,平台已受理售后"
                               f"({row.get('seller_info') or '已退款'}),可改投其它媒体")
            # ★ The platform refunds **our supplier account**; the user gets nothing back -- these are two independent ledgers.
            #   Without a user refund here, the user "paid, lost the article, and never got the money back".
            from geo.services import publish_refund

            refunded = publish_refund.refund_task(db, task, "appeal")
            compliance_audit.record(
                db, topic_id=task.topic_id, event_type="paid_publish",
                object_type="publish_task", object_id=task.id,
                actor_type="system", actor_id=None, actor_name=f"{label}售后",
                detail={"action": "appeal_approved", "url": task.published_url,
                        "refunded_cents": refunded},
            )
        elif str(status) == "2":
            task.last_error = "售后申诉被平台拒绝,请人工核实链接"
    return changed


# ── Periodic job ────────────────────────────────────────────────────────────────

def sweep(*, appeal: Optional[bool] = None, task_ids: Optional[list[int]] = None,
          force: bool = False, verbose: bool = False) -> dict:
    """Scan all published paid placement tasks within the window."""
    if not settings.RWJ_LIVENESS_ENABLED and task_ids is None:
        return {"skipped": "RWJ_LIVENESS_ENABLED=0"}
    live_providers = [p for p in SITES if get_client(p).configured]
    if not live_providers:
        return {"skipped": "两家凭据都没配"}
    labels = [site(p).label for p in live_providers]
    # Pull the ticket list only once per round, for "don't appeal twice" (humans also file after-sales on the member site)
    tickets = {p: open_tickets(p) for p in live_providers}
    cutoff = datetime.utcnow() - timedelta(days=settings.RWJ_APPEAL_WINDOW_DAYS)
    stats: dict[str, int] = {}
    db = SessionLocal()
    try:
        q = (db.query(TopicDocPublishTaskORM)
               .filter(TopicDocPublishTaskORM.platform.in_(labels),
                       TopicDocPublishTaskORM.status == "published",
                       TopicDocPublishTaskORM.published_url.isnot(None))
               .order_by(TopicDocPublishTaskORM.id))
        if task_ids:
            q = q.filter(TopicDocPublishTaskORM.id.in_(task_ids))
        else:
            # The platform doesn't accept after-sales for articles outside the window; checking is pointless
            q = q.filter(TopicDocPublishTaskORM.actual_published_at >= cutoff)
        tasks = q.all()
        for task in tasks:
            try:
                out = check_task(db, task, appeal=appeal, force=force or bool(task_ids),
                                 tickets=tickets.get(provider_of(task.platform) or DEFAULT_PROVIDER))
                db.commit()
            except RwjNotConfigured:
                db.rollback()
                continue
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("[rwj-liveness] task=%s check failed", task.id)
                stats["error"] = stats.get("error", 0) + 1
                continue
            stats[out["action"]] = stats.get(out["action"], 0) + 1
            if verbose or out["action"] not in (ALIVE, "skip"):
                log.info("[rwj-liveness] task=%s [%s/%s] %s: %s",
                         task.id, task.platform, task.media_name, out["action"], out["detail"])
        for p in live_providers:
            try:
                n = sync_tickets(db, provider=p)
                db.commit()
                if n:
                    stats["ticket_updated"] = stats.get("ticket_updated", 0) + n
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("[rwj-liveness] %s ticket check-back failed", p)
    finally:
        db.close()
        close_browser()
    return {"tasks": len(tasks), **stats}


def report() -> list[dict]:
    """Status list of checked tasks (read-only, no network) -- for ops to review / decide whether to open the appeal gate."""
    db = SessionLocal()
    try:
        labels = [site(p).label for p in SITES]
        rows = []
        for task in (db.query(TopicDocPublishTaskORM)
                       .filter(TopicDocPublishTaskORM.platform.in_(labels))
                       .order_by(TopicDocPublishTaskORM.id).all()):
            meta = _meta_of(task)
            live = meta.get("liveness")
            if not isinstance(live, dict):
                continue
            rows.append({
                "task_id": task.id, "platform": task.platform, "media": task.media_name,
                "verdict": live.get("verdict"), "reason": live.get("reason"),
                "strikes": live.get("strikes"), "age_days": _age_days(task),
                "appealed": bool(meta.get("appeal")),
                "ticket_status": (meta.get("appeal") or {}).get("ticket_status"),
                "url": task.published_url,
            })
        return rows
    finally:
        db.close()


def reset() -> int:
    """Clear recorded check results (restart counting after verdict logic changes; don't leave old death counts around)."""
    db = SessionLocal()
    n = 0
    try:
        labels = [site(p).label for p in SITES]
        for task in (db.query(TopicDocPublishTaskORM)
                       .filter(TopicDocPublishTaskORM.platform.in_(labels)).all()):
            meta = _meta_of(task)
            if "liveness" in meta:
                meta.pop("liveness", None)
                _save_meta(task, meta)
                n += 1
        db.commit()
    finally:
        db.close()
    return n


def tick() -> None:
    log.info("[rwj-liveness] %s", sweep())


def main() -> int:
    ap = argparse.ArgumentParser(description="付费发稿链接存活巡检 / 到期前申诉退款")
    ap.add_argument("--appeal", action="store_true", help="本次强制允许申诉(不看 RWJ_APPEAL_ENABLED)")
    ap.add_argument("--no-appeal", action="store_true", help="本次只巡检,绝不申诉")
    ap.add_argument("--task", type=int, action="append", default=[], help="只巡检指定任务 id(忽略窗口与间隔限制)")
    ap.add_argument("--force", action="store_true", help="忽略巡检最小间隔,全部重抓一遍")
    ap.add_argument("--report", action="store_true", help="只打印现状清单(不出网、不改库)")
    ap.add_argument("--reset", action="store_true", help="清空已记录的巡检结果后再跑(判定逻辑改过时用)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # one GET log line per item floods the output
    if args.report:
        rows = report()
        order = {DEAD_HTTP: 0, DEAD_GONE: 1, UNKNOWN: 2, ALIVE: 3}
        for r in sorted(rows, key=lambda x: (order.get(x["verdict"], 9), x["task_id"])):
            flag = "已申诉" if r["appealed"] else ""
            print(f"#{r['task_id']:<5} {r['verdict']:<10} x{r['strikes']} {r['age_days']:>3}天 "
                  f"{(r['media'] or '')[:18]:<20} {r['reason'][:34]:<36} {flag} {r['url']}")
        from collections import Counter
        print(dict(Counter(r["verdict"] for r in rows)))
        return 0
    if args.reset:
        print(f"已清空 {reset()} 条任务的巡检记录")
    appeal = True if args.appeal else (False if args.no_appeal else None)
    print(json.dumps(sweep(appeal=appeal, task_ids=args.task or None,
                           force=args.force, verbose=args.verbose),
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
