"""/api/check/advanced-anon/*: the **no-login** entry for advanced audits.

None of the six "advanced audit" cards on the website was reachable from the skill side before. This opens them all up,
but splits them into two tiers by **real cost**, rather than vaguely saying "all free":

| Tier | Mode | Basis |
|---|---|---|
| Zero cost, unlimited | `aeo` / `crawl-test` | Only fetches pages and public data, no API key needed |
| **Free but rate-limited** | `authority` / `citation` / `entity` / `compare` / `visibility` / `competitive-intel` | Every run is a real engine call (¥1-5 per run) |

★ **Audits are never charged**: this is the product's billing boundary: viewing the current state is free, continuous tracking (batch-run subscription)
  and taking action (publishing placements) are charged. The limits here are **only for abuse prevention, not a billing gate**:
  over the limit returns 429 with "sign up for a higher quota", and **never steers toward payment**.

Limits reuse `services/anon_quota` (device + egress IP dual keys, rolling 30 days); no separate implementation.
**Quota is deducted at launch**: cost is incurred as soon as the call goes out, and failures are not refunded, otherwise failure retries would be a free unlimited channel.

★ Reuses the existing runners in `advanced_runners` (with 24h cache), without rewriting audit logic;
  this only adds the "no login + rate limit" shell.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from geo.database import SessionLocal
from geo.services import anon_quota
from geo.utils.error_handler import AppException
from geo.utils.validator import sanitize_url, validate_url

log = logging.getLogger(__name__)
router = APIRouter()

# The two zero-cost ones: not in the quota table (adding a gate to them would only be asking for trouble)
FREE_MODES = {"aeo", "crawl-test"}
# The four that cost money -> feature names in anon_quota
PAID_FEATURE = {
    "authority": "adv_authority",
    "citation": "adv_citation",
    "entity": "adv_entity",
    "compare": "adv_compare",
    "visibility": "adv_visibility",
    "competitive-intel": "adv_competitive",
}


def _url_or_400(url: str) -> str:
    if not validate_url(url or ""):
        raise AppException(status_code=400, message="网址格式不对")
    return sanitize_url(url)


def _spend(mode: str, request: Request, device_id: str, note: str = "") -> dict | None:
    """Paid modes: deduct quota first. If enough, return a quota snapshot; otherwise return None for the caller to respond 429."""
    feature = PAID_FEATURE.get(mode)
    if feature is None:
        return {}
    db = SessionLocal()
    try:
        try:
            return anon_quota.consume(db, feature, device_id, anon_quota.client_ip(request), note=note[:64])
        except anon_quota.QuotaExceeded as e:
            raise AppException(status_code=429, message=e.message)
    finally:
        db.close()


class UrlIn(BaseModel):
    url: str
    device_id: str = ""


class EntityIn(BaseModel):
    entity_name: str
    entity_type: str = "brand"      # brand | product | person
    device_id: str = ""


class CompareIn(BaseModel):
    urls: list[str]
    device_id: str = ""


def _note(mode: str, quota: dict | None) -> dict:
    tip = {
        "aeo": "AEO 审计:测的是页面内容对「AI 生成答案」的适配程度,和网站体检(check)不是一回事。零成本,不限次。",
        "crawl-test": "爬虫测试:验证主流 AI 爬虫能不能真抓到这个页面。零成本,不限次。",
        "authority": "权威信号:站外的评价、提及与第三方信任信号。**免费**,但每次都是真实引擎调用,所以有次数上限。",
        "citation": "引用检测:AI 引擎回答品牌相关问题时会不会引用这个站。**免费**,有次数上限。",
        "entity": "实体审计:不需要网址,测 AI 对这个品牌/产品/人物的认知。**免费**,有次数上限。",
        "compare": "多站对比:并排比 GEO 各类目得分。**免费**,有次数上限。",
        "visibility": "AI 可见性审计:跨多个主流 AI 引擎问一批问题,看这个站/品牌被不被提到。"
                      "比 mention 更全(更多引擎、更多问题)。**免费**,有次数上限。",
        "competitive-intel": "竞品情报:从 AI 应答里抽出谁在被推荐、信源偏好与竞品格局。**免费**,有次数上限。",
    }[mode]
    out = {"note": tip}
    if quota:
        out["quota"] = {k: quota.get(k) for k in ("device_used", "device_limit", "ip_used", "ip_limit", "window_days")}
    return out


async def _run(fn, *args):
    from geo.services import advanced_runners  # noqa: F401  (ensure the module is loaded)

    import anyio

    return await anyio.to_thread.run_sync(lambda: fn(*args))


@router.post("/check/advanced-anon/aeo")
async def anon_aeo(body: UrlIn, request: Request):
    """AEO visibility audit: no login, zero cost, unlimited."""
    from geo.services import advanced_runners

    url = _url_or_400(body.url)
    data = await _run(advanced_runners.run_aeo_visibility, url)
    return {**data, **_note("aeo", None)}


@router.post("/check/advanced-anon/crawl-test")
async def anon_crawl_test(body: UrlIn, request: Request):
    """AI crawler test: no login, zero cost, unlimited."""
    from geo.services import advanced_runners

    url = _url_or_400(body.url)
    data = await _run(advanced_runners.run_crawl_test, url)
    return {**data, **_note("crawl-test", None)}


@router.post("/check/advanced-anon/authority")
async def anon_authority(body: UrlIn, request: Request):
    """Authority signal audit: no login, **free**, but every run is a real engine call, so it is rate-limited against abuse."""
    from geo.services import advanced_runners

    url = _url_or_400(body.url)
    quota = _spend("authority", request, body.device_id, url)
    data = await _run(advanced_runners.run_authority_audit, url)
    return {**data, **_note("authority", quota)}


@router.post("/check/advanced-anon/citation")
async def anon_citation(body: UrlIn, request: Request):
    """AI citation check: no login, **free**, but every run is a real engine call, so it is rate-limited against abuse."""
    from geo.services import advanced_runners

    url = _url_or_400(body.url)
    quota = _spend("citation", request, body.device_id, url)
    data = await _run(advanced_runners.run_citation_check, url)
    return {**data, **_note("citation", quota)}


@router.post("/check/advanced-anon/entity")
async def anon_entity(body: EntityIn, request: Request):
    """Entity GEO audit: no URL needed; no login, **free**, but every run is a real engine call, so it is rate-limited against abuse."""
    from geo.services import advanced_runners

    name = (body.entity_name or "").strip()
    if not name:
        raise AppException(status_code=400, message="实体名称不能为空")
    etype = body.entity_type if body.entity_type in ("brand", "product", "person") else "brand"
    quota = _spend("entity", request, body.device_id, name)
    data = await _run(advanced_runners.run_entity_audit, name, etype)
    return {**data, **_note("entity", quota)}


@router.post("/check/advanced-anon/compare")
async def anon_compare(body: CompareIn, request: Request):
    """Multi-site comparison: no login, **free**, but every run is a real engine call, so it is rate-limited against abuse. 2-5 URLs."""
    from geo.services import advanced_runners

    urls = [_url_or_400(u) for u in (body.urls or [])][:5]
    if len(urls) < 2:
        raise AppException(status_code=400, message="至少要两个网址才能对比")
    quota = _spend("compare", request, body.device_id, urls[0])
    data = await _run(advanced_runners.run_compare, urls)
    return {**data, **_note("compare", quota)}


class VisibilityIn(BaseModel):
    url: str
    brand_name: str = ""
    queries: list[str] = []         # Custom questions; built-in templates are used if omitted
    device_id: str = ""


@router.post("/check/advanced-anon/visibility")
async def anon_visibility(body: VisibilityIn, request: Request):
    """AI visibility audit: the comprehensive version across multiple engines; no login, **free**, but every run is a real engine call, so it is rate-limited against abuse."""
    from geo.services import advanced_runners

    url = _url_or_400(body.url)
    qs = [q.strip() for q in (body.queries or []) if q and q.strip()][:10] or None
    quota = _spend("visibility", request, body.device_id, url)
    data = await _run(advanced_runners.run_ai_visibility, url, qs, False,
                      (body.brand_name or "").strip() or None)
    return {**data, **_note("visibility", quota)}


class CompetitiveIn(BaseModel):
    url: str
    competitor_domains: list[str] = []
    device_id: str = ""


@router.post("/check/advanced-anon/competitive-intel")
async def anon_competitive(body: CompetitiveIn, request: Request):
    """Competitive intelligence: no login, **free**, but every run is a real engine call, so it is rate-limited against abuse. Competitor domains are optional and inferred by the system."""
    from geo.services import advanced_runners

    url = _url_or_400(body.url)
    comps = [c.strip() for c in (body.competitor_domains or []) if c and c.strip()][:5] or None
    quota = _spend("competitive-intel", request, body.device_id, url)
    data = await _run(advanced_runners.run_competitive_intel, url, comps)
    return {**data, **_note("competitive-intel", quota)}
