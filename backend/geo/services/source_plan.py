"""Sources -> publishing plan: use measured cited sources to decide platform quotas in the publishing table.

Pipeline: start -> run a batch of baseline audits -> aggregate response citations by domain -> map domains to channels **we
can actually publish to** -> allocate platform slots for "N articles per day" by citation share -> lay out the publishing table.

Division of labor with services/source_strategy.py:
  - source_strategy: LLM layer, answers "why cited / writing requirements / media suggestions (natural language)"
  - source_plan (this module): pure aggregation + deterministic allocation, producing platforms that can be written directly into plan rows

Platform quotas deliberately **don't use the LLM** -- the plan is hard scheduling data; it must be explainable and recomputable, and must not fail to
schedule because the LLM timed out. LLM media suggestions still go through source_strategy into the optimization task center.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter, defaultdict
from typing import Any, Optional

from sqlalchemy.orm import Session

from geo.database import settings
from geo.models.ai_telemetry import AiTelemetryResponseORM, AiTelemetryTopicORM

log = logging.getLogger(__name__)

# Domain -> publishable channel (Chinese platform name = plan row platform / task table platform field value).
# Platform names align with the labels in frontend/src/constants/platformCatalog.ts and
# geo/api/platform_rules.py::DEFAULT_PLATFORMS; suffix matching (including subdomains).
PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "知乎": ("zhihu.com", "zhuanlan.zhihu.com"),
    "公众号": ("weixin.qq.com", "mp.weixin.qq.com", "wechat.com"),
    "小红书": ("xiaohongshu.com", "xhslink.com"),
    "微博": ("weibo.com", "weibo.cn"),
    "抖音": ("douyin.com", "iesdouyin.com"),
    "视频号": ("channels.weixin.qq.com",),
    "搜狐号": ("sohu.com",),
    "百家号": ("baijiahao.baidu.com", "mbd.baidu.com"),
    "头条号": ("toutiao.com", "toutiaoimg.com", "365yg.com"),
    # Bilibili / Kuaishou are supported by aitoearn but option is required when creating a flow, so they can't enter the publishable set yet --
    # still mapped, so they show up in the "cited but can't publish" hint instead of being lumped with unnamed domains
    "B站": ("bilibili.com", "b23.tv"),
    "快手": ("kuaishou.com", "kwai.com"),
    # 2026-08-04 -- a batch added after persisting the Ruanwenjiang resource library: we have no own accounts on these platforms, but Ruanwenjiang
    # has plenty of publishable resources (Yidian Zixun 1269 / Tencent account 917 / UC Toutiao 610 / NetEase account 178...),
    # and citations really do come from these domains; without mapping we can neither compute publishable channels nor recognize the sources.
    "网易号": ("163.com", "dy.163.com"),
    "腾讯号": ("new.qq.com", "xw.qq.com", "om.qq.com"),
    "新浪号": ("sina.com.cn", "sina.cn"),
    "凤凰号": ("ifeng.com",),
    "一点资讯": ("yidianzixun.com",),
    "UC头条": ("uc.cn", "myuc.cn"),
    "东方财富号": ("eastmoney.com",),
    "东方头条": ("dftoutiao.com",),
    "懂车帝": ("dongchedi.com",),
    "车家号": ("autohome.com.cn",),
    "雪球号": ("xueqiu.com",),
    "豆瓣": ("douban.com",),
}

# Sources outside content platforms (encyclopedias / vertical media / competitor sites / government sites...) can't be published to; analysis reference only,
# never entering the publishing table -- a row "submit to wikipedia" is a plan that can't be executed.

# The official blog auto-publishes via mediumsly, Ruanwenjiang is a paid channel: neither is in the aitoearn account pool,
# so availability is checked separately.
OWN_BLOG = "官网博客"
RWJ = "软文匠"

# Fallback order for cold start (no measured citation data) -- still intersected with "actually publishable".
COLD_START_ORDER = ("知乎", "公众号", OWN_BLOG, "百家号", "搜狐号", "头条号", "小红书")

# Channels with citation share below this threshold don't take scheduling slots (long-tail noise).
MIN_SHARE_PCT = 8.0

# Max fraction of daily articles paid channels (Ruanwenjiang publishing) can take -- the rest must go through own channels.
# Without this gate, the measured citation distribution would directly decide the wallet: a topic with Sohu account 51% + Zhihu 35% would turn
# 86% of scheduling into paid.
PAID_MAX_SHARE = float(os.environ.get("GEO_PLAN_PAID_MAX_SHARE", "0.6"))


# Some engines (Doubao / Yuanbao / Wenxin) return citations whose domain holds a **Chinese site name** instead of
# a domain (measured topic 20: 「知乎」 ("Zhihu") 29 times, 「华律网」 ("Hualv"), 「博客园」 ("Cnblogs")). Domain-suffix matching alone would
# miss these as "can't publish" and undercount quotas significantly -- so here's a Chinese site-name alias table.
PLATFORM_CN_ALIASES: dict[str, tuple[str, ...]] = {
    # Ruanwenjiang channel names (field_1_name) are normalized through this table too, so forms like 「知乎号」 ("Zhihu account") must be included --
    # missing 「知乎号」 loses 364 publishable resources, and Zhihu is precisely a top cited source
    "知乎": ("知乎", "知乎号", "知乎专栏"),
    "公众号": ("微信公众号", "公众号", "微信"),
    "小红书": ("小红书",),
    "微博": ("微博", "新浪微博"),
    "抖音": ("抖音",),
    "视频号": ("微信视频号", "视频号"),
    "搜狐号": ("搜狐", "搜狐号", "搜狐网"),
    "百家号": ("百家号", "百度百家"),
    "头条号": ("今日头条", "头条号", "头条"),
    "B站": ("哔哩哔哩", "B站", "bilibili"),
    "快手": ("快手",),
    "网易号": ("网易号", "网易"),
    "腾讯号": ("腾讯号", "企鹅号"),
    "新浪号": ("新浪号", "新浪"),
    "凤凰号": ("凤凰号", "凤凰网"),
    "一点资讯": ("一点资讯", "一点号"),
    "UC头条": ("UC头条", "uc头条", "大鱼号"),
    "东方财富号": ("东方财富号", "东方财富"),
    "东方头条": ("东方头条",),
    "懂车帝": ("懂车帝",),
    "车家号": ("车家号", "汽车之家"),
    "雪球号": ("雪球号", "雪球"),
    "豆瓣": ("豆瓣",),
}


def media_name_core(text: str) -> str:
    """「博客园(GEO秒发,可带联系方式)」 ("Cnblogs (GEO instant publish, contact info allowed)") -> 「博客园」 -- strip parenthetical/dash suffixes.

    Both places need it: Ruanwenjiang resource names (with channel notes), and platform names in LLM media suggestions
    (forms with a source like 「博客园(cnblogs.com/博客园)」). Without stripping none of them match.
    """
    core = str(text or "").strip()
    # Full-width parentheses must be listed explicitly -- resource names almost all use full-width 「（）」; stripping only half-width does nothing
    for br in ("(", "（", "[", "［", "【", "—", "-", "｜", "|"):
        i = core.find(br)
        if i > 0:
            core = core[:i]
    return core.strip()


def normalize_platform_name(text: str) -> Optional[str]:
    """Chinese site name / channel name -> our platform name; returns None if unrecognized.

    Exact match rather than substring -- 「知乎日报」 ("Zhihu Daily") isn't Zhihu, 「搜狐焦点」 ("Sohu Focus") isn't a Sohu account;
    better to recognize less than misrecognize (misrecognition schedules resources onto channels that can't publish).
    Ruanwenjiang resources' field_1_name (e.g. 「搜狐」 ("Sohu"), 「今日头条」 ("Toutiao")) is also normalized through this function.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    low = raw.lower()
    for platform, names in PLATFORM_CN_ALIASES.items():
        if raw in names or low in tuple(n.lower() for n in names):
            return platform
    return None


def map_domain(domain: str) -> Optional[str]:
    """Domain / Chinese site name -> publishable channel name; returns None if unmapped (encyclopedias / vertical media / competitor sites etc.)."""
    raw = (domain or "").strip()
    if not raw:
        return None
    d = raw.lower().lstrip(".")
    for platform, suffixes in PLATFORM_DOMAINS.items():
        for suf in suffixes:
            if d == suf or d.endswith("." + suf):
                return platform
    # No dot = not a domain; try matching again as a Chinese site name
    return normalize_platform_name(raw)


def rwj_platform_stats(db, *, price_cap_yuan: Optional[float] = None) -> dict[str, dict]:
    """Stats of publishable platforms in the Ruanwenjiang resource library within the unit price cap.

    Returns {platform name: {count, price_min, price_median, publish_rate_median}}, in CNY.
    This is the sole basis for "can we actually publish to Zhihu / Sohu / Toutiao accounts where we have no own accounts" --
    we have no accounts on these platforms, but Ruanwenjiang has thousands of publishable resources.
    When the resource table is empty (never synced) returns {}; the caller treats it as "own channels only".
    """
    from sqlalchemy import func

    from geo.models.rwj_media import RwjMediaResourceORM as R

    cap = float(price_cap_yuan if price_cap_yuan is not None
                else settings.RWJ_AUTO_PRICE_CAP_YUAN)
    cap_cents = int(cap * 100)
    rows = (
        db.query(R.platform, R.ref_price_cents, R.publish_rate)
          .filter(R.platform != "", R.is_active.is_(True),
                  R.ref_price_cents.isnot(None),
                  R.ref_price_cents <= cap_cents)
          .all()
    )
    agg: dict[str, list] = {}
    for platform, cents, rate in rows:
        agg.setdefault(platform, []).append((cents, rate))
    out: dict[str, dict] = {}
    for platform, vals in agg.items():
        prices = sorted(c for c, _ in vals)
        rates = sorted(r for _, r in vals if r is not None)
        out[platform] = {
            "count": len(vals),
            "price_min": round(prices[0] / 100, 2),
            "price_median": round(prices[len(prices) // 2] / 100, 2),
            "publish_rate_median": rates[len(rates) // 2] if rates else None,
        }
    _ = func  # keep the import for future aggregate queries
    return out


def pick_rwj_resources(db, platform: str, *, limit: int = 5,
                       price_cap_yuan: Optional[float] = None) -> list[dict]:
    """Pick candidate resources for a platform: publish rate desc -> price asc -> resource_id asc (recomputable).

    Returns top-N rather than a single one because scheduling rotates by day -- pouring 30 articles a month into one account
    carries a high indexing risk on the platform side. Pure SQL sorting, no LLM, no platform API.

    Candidates from both Ruanwenjiang / Meijieku are ranked together; for the same media whichever is cheaper wins -- so every row must
    carry provider, and the caller must write provider into the plan row too (ids aren't portable across sites).
    """
    from geo.models.rwj_media import RwjMediaResourceORM as R

    cap = float(price_cap_yuan if price_cap_yuan is not None
                else settings.RWJ_AUTO_PRICE_CAP_YUAN)
    rows = (
        db.query(R)
          .filter(R.platform == platform, R.is_active.is_(True),
                  R.ref_price_cents.isnot(None),
                  R.ref_price_cents <= int(cap * 100))
          .order_by(R.publish_rate.desc().nullslast(),
                    R.ref_price_cents.asc(), R.resource_id.asc())
          .limit(limit)
          .all()
    )
    return [{
        "provider": r.provider or "rwj",
        "resource_id": r.resource_id,
        "title": r.title,
        "res_type": r.res_type,
        "price_yuan": round((r.ref_price_cents or 0) / 100, 2),
        "publish_rate": r.publish_rate,
    } for r in rows]


# Directory / encyclopedia / company-info sites: Ruanwenjiang can't publish there, but **you can update company info yourself**
# (edit the business profile on Tianyancha, create an encyclopedia entry, add tags on Qichacha). These citations are exactly the factual
# sources AI loves to cite; judging them "can't publish" makes ops miss a zero-cost visibility fix.
PROFILE_SITE_DOMAINS: tuple[str, ...] = (
    "tianyancha.com", "qcc.com", "qichacha.com", "aiqicha.baidu.com", "qixin.com",
    "baike.baidu.com", "wikipedia.org", "baike.so.com", "baike.sogou.com",
    "wiki.mbalib.com", "gsxt.gov.cn", "crunchbase.com", "zhihu.com/org",
    "maigoo.com", "11467.com", "51pla.com",
)


# Engines sometimes return Chinese site names instead of domains (Doubao/Yuanbao/Wenxin), so one site splits into two rows:
# 「天眼查」 ("Tianyancha") 66 times judged reference-only, tianyancha.com 130 times judged updatable. Chinese names must be recognized too.
PROFILE_SITE_CN: tuple[str, ...] = (
    "天眼查", "企查查", "爱企查", "启信宝", "百度百科", "维基百科", "搜狗百科",
    "360百科", "MBA智库百科", "国家企业信用信息公示系统",
)


def profile_site_of(domain: str) -> bool:
    """Whether this domain / Chinese site name is a directory / encyclopedia site that "can't be published to but whose info you can edit"."""
    raw = (domain or "").strip()
    if raw in PROFILE_SITE_CN:
        return True
    d = raw.lower().lstrip(".")
    return any(d == s or d.endswith("." + s) for s in PROFILE_SITE_DOMAINS)


_domain_index_cache: dict[str, Any] = {"at": None, "data": None}


def rwj_domain_index(db, *, ttl_seconds: int = 600) -> dict[str, list[dict]]:
    """"Domain -> resource" index of the Ruanwenjiang resource library.

    Resource rows have no domain field, but case_link (sample article link) is 100% filled and entrance_link
    86%, and the site domain is in there. With this index, cited portal sites (China Business News, Cnblogs,
    CSDN...) can be matched to real publishable resources -- relying only on a hardcoded platform table would judge them all "can't publish",
    yet they're precisely top cited sources.

    Scanning 44k rows takes a few hundred ms; cached in-process for 10 minutes.
    """
    import time as _time
    from urllib.parse import urlparse

    from geo.models.rwj_media import RwjMediaResourceORM as R

    now = _time.time()
    if (_domain_index_cache["at"] and _domain_index_cache["data"] is not None
            and now - _domain_index_cache["at"] < ttl_seconds):
        return _domain_index_cache["data"]

    def _host(url: str) -> str:
        try:
            h = urlparse(url if "//" in url else "http://" + url).netloc.lower()
        except Exception:  # noqa: BLE001
            return ""
        h = h.split(":")[0]
        return h[4:] if h.startswith("www.") else h

    _title_core = media_name_core      # resource names and media suggestion names strip suffixes with the same metric definition

    idx: dict[str, list[dict]] = defaultdict(list)
    by_title: dict[str, list[dict]] = defaultdict(list)
    rows = (db.query(R.res_type, R.resource_id, R.title, R.ref_price_cents,
                     R.publish_rate, R.case_link, R.entrance_link, R.platform)
              .filter(R.is_active.is_(True), R.ref_price_cents.isnot(None))
              .all())
    for res_type, rid, title, cents, rate, case_link, entrance, platform in rows:
        # Those already normalized to own platform names (Sohu account / Zhihu...) don't go into the domain index -- they use the platform path,
        # otherwise an account page link like toutiao.com would hang the whole Toutiao account pool under the 「头条号」 ("Toutiao account") domain.
        if platform:
            continue
        for url in (case_link, entrance):
            h = _host(url or "")
            if not h:
                continue
            idx[h].append({
                "resource_id": rid, "title": title, "res_type": res_type,
                "price_yuan": round((cents or 0) / 100, 2), "publish_rate": rate,
            })
            break        # each resource is indexed only once under its main site
        core = _title_core(title)
        if core:
            by_title[core].append({
                "resource_id": rid, "title": title, "res_type": res_type,
                "price_yuan": round((cents or 0) / 100, 2), "publish_rate": rate,
            })
    # Each domain sorted by "publish rate desc -> price asc", top 5
    def _top5(v):
        return sorted(v, key=lambda x: (-(x["publish_rate"] or 0), x["price_yuan"]))[:5]

    # Domain index and Chinese site-name index merged into one table: Chinese names never collide with domain keys (domains always contain a dot)
    out = {h: _top5(v) for h, v in idx.items()}
    out.update({t: _top5(v) for t, v in by_title.items() if t not in out})
    _domain_index_cache.update({"at": now, "data": out})
    return out


def resolve_domain(db, domain: str, *, source_type: str = "",
                   index: Optional[dict] = None) -> dict:
    """What exactly can be done with a cited domain.

    kind:
      platform  -- maps to our platform name (Sohu account / Zhihu...), publish via the platform path
      rwj_site  -- this site is in the Ruanwenjiang resource library (portal web media), paid submission possible
      profile   -- directory / encyclopedia; can't publish but can update company info yourself
      none      -- content-format reference only
    """
    p = map_domain(domain)
    if p:
        return {"kind": "platform", "platform": p, "media": []}
    if profile_site_of(domain) or source_type == "directory":
        return {"kind": "profile", "platform": None, "media": []}
    idx = index if index is not None else rwj_domain_index(db)
    raw = (domain or "").strip()
    d = raw.lower().lstrip(".")
    if d.startswith("www."):
        d = d[4:]
    # Chinese site names (no dot) match by resource name: 「博客园」 -> 博客园(GEO秒发…) ("Cnblogs" -> "Cnblogs (GEO instant publish...)")
    hit = idx.get(raw) if "." not in raw else None
    hit = hit or idx.get(d)
    if not hit:
        # Subdomain fallback: blog.csdn.net -> csdn.net
        parts = d.split(".")
        for i in range(1, len(parts) - 1):
            hit = idx.get(".".join(parts[i:]))
            if hit:
                break
    if hit:
        return {"kind": "rwj_site", "platform": RWJ, "media": hit}
    return {"kind": "none", "platform": None, "media": []}


def _pool_platform_name(db, name: str) -> Optional[str]:
    """Whether the self-hosted account pool platform catalog has this Chinese name (Cnblogs / CSDN...); returns the original name if so."""
    from geo.models.publish_accounts import PublishPlatformORM

    try:
        hit = (db.query(PublishPlatformORM.name_zh)
                 .filter(PublishPlatformORM.name_zh == name,
                         PublishPlatformORM.enabled.is_(True))
                 .first())
    except Exception:  # noqa: BLE001
        return None
    return hit[0] if hit else None


def resolve_media_recommendation(db, text: str) -> dict:
    """The platform field of an LLM media suggestion -> whether it can become a publishing row's placement platform.

    Suggestions are written in all sorts of ways: 「知乎(zhuanlan.zhihu.com+知乎问答)」 ("Zhihu (+ Zhihu Q&A)"), 「博客园(cnblogs.com/博客园)」,
    「行业垂直门户(慧聪/行业协会)」 ("industry vertical portals (HC360 / industry associations)"). Strip suffixes to get the core name first, then go through resolve_domain's same rules,
    so there's no second platform metric definition here.

    Returns {kind, platform, media}:
      platform -- our platform name (Zhihu / Sohu account...), publishable via own account pool or paid publishing
      rwj_site -- a site with paid publishing resources (Cnblogs / CSDN...); platform is the site name itself, resource id still chosen by a human
      none/profile -- can't publish (encyclopedia / directory / generic "industry vertical portals"); can't go into the publishing table
    """
    core = media_name_core(text)
    if not core:
        return {"kind": "none", "platform": None, "media": []}
    # Suggestions naming two platforms like 「百家号+搜狐号」 ("Baijiahao + Sohu account"): one schedule row can only target one, take the first
    names = [core] + [s.strip() for s in re.split(r"[+＋/、]", core) if s.strip()][:1]
    for name in names:
        p = map_domain(name)
        if p:
            return {"kind": "platform", "platform": p, "media": []}
        # Self-hosted account pool platform catalog (Cnblogs / CSDN live only in publish_platforms, not in the domain table)
        if _pool_platform_name(db, name):
            return {"kind": "platform", "platform": name, "media": []}
    for name in names:
        hit = resolve_domain(db, name)
        if hit["kind"] == "rwj_site":
            # The site name itself is the plan row platform; channel/resource id aren't guessed here --
            # ids aren't portable between Ruanwenjiang/Meijieku; a wrong guess places the article on someone else's account
            return {"kind": "rwj_site", "platform": name, "media": hit["media"]}
        if hit["kind"] == "profile":
            return {"kind": "profile", "platform": None, "media": []}
    return {"kind": "none", "platform": None, "media": []}


def _recommended_media(db, platform: str, *, limit: int = 5) -> list[dict]:
    """Recommended media for the panel -- the same set scheduling actually binds (pick_rwj_resources),
    so the accounts seen on the panel are the accounts scheduled in the publishing table, not two different sets."""
    try:
        return pick_rwj_resources(db, platform, limit=limit)
    except Exception:  # noqa: BLE001
        log.exception("[source-plan] failed to fetch recommended media for %s", platform)
        return []


def available_platforms(db=None) -> tuple[set[str], list[str], set[str]]:
    """The set of channels **actually publishable** right now + notes + which of them are paid-only.

    - aitoearn: platforms covered by authorized accounts that support image-text posts (free)
    - official blog: available once the mediumsly channel is fully configured (free)
    - Ruanwenjiang: paid channel fully configured -> keep platform name 「软文匠」 ("Ruanwenjiang") = portal web media;
      additionally every normalized platform in the resource library (Zhihu / Sohu / Toutiao accounts...) also counts as publishable, but **paid**

    When aitoearn is unreachable, don't raise -- degrade to "own channels only" so the plan can still be scheduled.
    """
    notes: list[str] = []
    out: set[str] = set()
    paid_only: set[str] = set()

    try:
        from geo.services.aitoearn_client import configured as ae_configured
        from geo.services import aitoearn_publisher as ae_pub
        if ae_configured():
            accounts = asyncio.run(ae_pub.list_article_accounts())
            for acc in accounts:
                p = str(acc.get("platform") or "").strip()
                if p:
                    out.add(p)
            if not accounts:
                notes.append("aitoearn 已配置但没有已授权的图文账号")
        else:
            notes.append("aitoearn 通道未配置")
    except Exception as e:  # noqa: BLE001
        log.warning("[source-plan] aitoearn accounts unavailable: %s", e)
        notes.append(f"aitoearn 账号拉取失败({e})—— 本次只用自有渠道")

    try:
        from geo.database import settings as _s
        if _s.MEDIUMSLY_INTERNAL_API_TOKEN:
            out.add(OWN_BLOG)
    except Exception:  # noqa: BLE001
        pass

    rwj_ok = False
    try:
        from geo.services.rwj_client import get_client as _rwj_client
        # configured is a @property, not a method -- writing configured() earlier raised
        # TypeError swallowed by the except below, so Ruanwenjiang never entered the publishable set.
        rwj_ok = bool(_rwj_client().configured)
        if rwj_ok:
            out.add(RWJ)               # keep platform name 「软文匠」 ("Ruanwenjiang") = portal web media (Website type)
            paid_only.add(RWJ)         # it is paid publishing: subject to the 60% cap and counted in costs
    except Exception:  # noqa: BLE001
        pass

    # Ruanwenjiang resource library: platforms like Zhihu / Sohu / Toutiao accounts where we have no own accounts
    # count as **paid publishable** as long as the library has listed resources within the unit price cap.
    if rwj_ok and db is not None:
        try:
            stats = rwj_platform_stats(db)
            for platform in stats:
                if platform not in out:      # platforms own accounts can publish to for free don't count as paid channels
                    paid_only.add(platform)
                out.add(platform)
            if not stats:
                notes.append("软文匠资源库还没同步(python -m geo.services.rwj_media_sync)")
        except Exception as e:  # noqa: BLE001
            log.warning("[source-plan] rwj media library unavailable: %s", e)

    if not out:
        # Nothing available at all: still give the official blog so a plan can be scheduled; the existing pipeline errors at publish time
        out.add(OWN_BLOG)
        notes.append("没有可用的外部渠道,退到官网博客")
    return out, notes, paid_only


def build_query_sources(db: Session, topic_id: int) -> list[dict[str, Any]]:
    """Per-prompt source details (complete, including words with zero citations).

    Difference from per_query in source_strategy.build_source_stats: that one keeps only the top 15 with citations to feed the LLM;
    this is the complete list for the "source analysis" panel.
    """
    from geo.api.ai_telemetry import _load_owned_urls, _norm_url

    owned = _load_owned_urls(db, topic_id)
    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.error.is_(None))
          .all()
    )

    total: dict[str, int] = defaultdict(int)
    hits: dict[str, int] = defaultdict(int)
    domains: dict[str, Counter] = defaultdict(Counter)
    own_hits: dict[str, int] = defaultdict(int)
    engines: dict[str, set] = defaultdict(set)

    for r in rows:
        q = r.query or ""
        total[q] += 1
        engines[q].add(r.engine)
        if r.hit:
            hits[q] += 1
        try:
            cits = json.loads(r.citations_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        for c in cits:
            if not isinstance(c, dict) or not c.get("url"):
                continue
            if _norm_url(c["url"]) in owned:
                own_hits[q] += 1
                continue
            d = (c.get("domain") or "").strip()
            if d:
                domains[q][d] += 1

    out: list[dict[str, Any]] = []
    for q in sorted(total):
        top = [
            {"domain": d, "count": n, "platform": map_domain(d)}
            for d, n in domains[q].most_common(10)
        ]
        out.append({
            "query": q,
            "responses": total[q],
            "hit_pct": round(hits[q] / total[q] * 100, 1) if total[q] else 0.0,
            "engines": sorted(engines[q]),
            "citations": sum(domains[q].values()),
            "own_cited": own_hits[q],
            "top_domains": top,
        })
    # Missed / low-citation ones first -- the panel shows weaknesses first
    out.sort(key=lambda x: (x["hit_pct"], -x["citations"]))
    return out


def _largest_remainder(weights: dict[str, int], slots: int) -> dict[str, int]:
    """Largest-remainder allocation of slots; each selected platform gets at least 1."""
    if slots <= 0 or not weights:
        return {}
    ranked = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    # When there are more platforms than slots, take only the top slots (guaranteeing at least 1 article each)
    ranked = ranked[:slots]
    total = sum(n for _, n in ranked) or 1
    base: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    for name, n in ranked:
        exact = n / total * slots
        base[name] = max(1, int(exact))
        remainders.append((exact - int(exact), name))
    # max(1,...) may over-allocate; reclaim by remainder from smallest to largest
    over = sum(base.values()) - slots
    if over > 0:
        for _, name in sorted(remainders):
            while over > 0 and base[name] > 1:
                base[name] -= 1
                over -= 1
            if over <= 0:
                break
    left = slots - sum(base.values())
    for _, name in sorted(remainders, reverse=True):
        if left <= 0:
            break
        base[name] += 1
        left -= 1
    while left > 0:  # not enough platforms to fill slots: top up with the highest weight
        base[ranked[0][0]] += 1
        left -= 1
    return base


# notes are human-readable text; the frontend renders them as cards by type and decides whether to show action buttons like "go authorize",
# so a structured copy with codes is produced alongside. Prefix matching is used -- two notes carry interpolation
# (exception text from account fetch failure, cap percentage); exact matching would miss them.
# Notes that don't match are passed through as code="other", never swallowed.
_NOTE_CODES: list[tuple[str, str, str]] = [
    ("aitoearn 已配置但没有已授权", "aitoearn_no_account", "warn"),
    ("aitoearn 通道未配置", "aitoearn_unconfigured", "info"),
    ("aitoearn 账号拉取失败", "aitoearn_fetch_failed", "warn"),
    ("软文匠资源库还没同步", "rwj_lib_empty", "warn"),
    ("没有可用的外部渠道", "no_external_channel", "warn"),
    ("实测信源里没有可投渠道", "cold_start", "info"),
    ("付费渠道占比已封顶", "paid_capped", "info"),
    ("所有被引渠道占比都不到", "all_below_min_share", "info"),
]


def _structify_notes(notes: list[str]) -> list[dict]:
    out: list[dict] = []
    for text in notes:
        code, level = "other", "info"
        for prefix, c, lv in _NOTE_CODES:
            if text.startswith(prefix):
                code, level = c, lv
                break
        out.append({"code": code, "text": text, "level": level})
    return out


def build_platform_quota(
    db: Session, topic_id: int, per_day: int,
    *, top_sources: Optional[list[dict]] = None,
    cold_start_order: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Allocate placement platforms for "per_day articles per day" by citation share.

    Returns {slots, breakdown, unavailable, unmapped, notes, cold_start}:
      - slots: array of platform names of length per_day, fed directly to _build_plan_window
      - breakdown: citation count / share / article count / source domains per selected platform (panel display)
      - unavailable: channels measured as cited but currently not publishable (prompt ops to authorize)
      - unmapped: domains that can't map to a publishable channel (encyclopedias / vertical media / competitor sites; reference only)

    2026-08-06 -- scheduling rationale filled in (all new keys; old callers unaffected):
      - excluded: channels dropped by the long-tail threshold (they used to vanish, and the frontend couldn't answer "Zhihu was cited,
        why wasn't it scheduled")
      - paid_cap_moves: which articles the paid cap moved from whom to whom (used to be a single vague note)
      - notes_struct / thresholds / slot_plan[].reason
    These are trimmed by plan_basis and persisted with the plan, becoming evidence of "why it was scheduled this way".
    """
    per_day = max(1, int(per_day or 1))
    if top_sources is None:
        from geo.services.source_strategy import build_source_stats
        top_sources = build_source_stats(db, topic_id).get("top_sources") or []

    available, notes, paid_only = available_platforms(db)
    rwj_prices = rwj_platform_stats(db) if paid_only else {}

    by_platform: dict[str, int] = defaultdict(int)
    domains_of: dict[str, list[dict]] = defaultdict(list)
    unmapped: list[dict] = []
    profile_sites: list[dict] = []       # directory / encyclopedia sites that can't be published to but whose info can be edited
    rwj_site_media: dict[int, dict] = {}  # resources matched for portal sites, used to bind resources to the 「软文匠」 ("Ruanwenjiang") bucket
    idx = rwj_domain_index(db) if RWJ in available else {}
    for s in top_sources:
        domain = str(s.get("domain") or "")
        count = int(s.get("count") or 0)
        r = resolve_domain(db, domain, source_type=str(s.get("source_type") or ""),
                           index=idx)
        if r["kind"] == "profile":
            profile_sites.append({"domain": domain, "count": count,
                                  "source_type": s.get("source_type") or ""})
            continue
        if r["kind"] == "none":
            unmapped.append({"domain": domain, "count": count,
                             "source_type": s.get("source_type") or ""})
            continue
        p = r["platform"]
        by_platform[p] += count
        domains_of[p].append({"domain": domain, "count": count})
        for m in r.get("media") or []:
            rwj_site_media.setdefault(m["resource_id"], m)

    # Measured cited-channel ranking -- **not filtered by publishability**; it's the raw finding of "who did the batch run actually cite".
    # Scheduling quotas can only use the publishable part, but the panel must show the measured result first; otherwise in an environment
    # with no external accounts authorized yet, users only see an empty card "official blog 100%".
    measured_total = sum(by_platform.values()) or 1
    measured = [
        {
            "platform": p, "citations": n,
            "share_pct": round(n / measured_total * 100, 1),
            "investable": p in available,
            # free = publish directly via own accounts/sites; paid = only via Ruanwenjiang paid publishing; none = can't publish
            "investable_kind": ("paid" if p in paid_only
                                else "free" if p in available else "none"),
            "unit_price": rwj_prices.get(p, {}).get("price_median") if p in paid_only else 0.0,
            "resource_count": rwj_prices.get(p, {}).get("count") if p in paid_only else None,
            # Recommended media: the accounts scheduling will actually bind (publish rate desc -> price asc);
            # a panel that stops at platform level isn't actionable -- ops needs to know which account and how much.
            "media": ((sorted(rwj_site_media.values(),
                              key=lambda x: (-(x["publish_rate"] or 0), x["price_yuan"]))[:5]
                       if p == RWJ and rwj_site_media else _recommended_media(db, p))
                      if p in paid_only or p == RWJ else []),
            "domains": sorted(domains_of.get(p, []), key=lambda d: -d["count"])[:5],
        }
        for p, n in sorted(by_platform.items(), key=lambda kv: -kv[1])
    ]

    unavailable = [
        {"platform": p, "citations": n}
        for p, n in sorted(by_platform.items(), key=lambda kv: -kv[1])
        if p not in available
    ]
    usable = {p: n for p, n in by_platform.items() if p in available}
    # Long-tail noise threshold: channels with share below MIN_SHARE_PCT take no slots (otherwise a 98:1:1
    # distribution would be spread to 3:1:1 by "at least 1 per selected platform"). If all are filtered, keep the top one.
    # excluded records who was dropped -- this used to vanish, and the panel couldn't answer
    # "Xiaohongshu was clearly cited, why wasn't a single article scheduled".
    excluded: list[dict] = []
    if usable:
        total_usable = sum(usable.values())
        kept = {p: n for p, n in usable.items()
                if n / total_usable * 100 >= MIN_SHARE_PCT}
        dropped = {p: n for p, n in usable.items() if p not in kept}
        all_below = not kept
        if all_below:
            top = max(usable, key=lambda p: usable[p])
            kept = {top: usable[top]}
            dropped.pop(top, None)
        excluded = [
            {
                "platform": p, "citations": n,
                "share_pct": round(n / total_usable * 100, 1),
                "reason": "below_min_share",
                "threshold": MIN_SHARE_PCT,
            }
            for p, n in sorted(dropped.items(), key=lambda kv: -kv[1])
        ]
        if all_below:
            # Kept the top one when none met the threshold -- must be explained, otherwise "threshold 8% but a 5% one got scheduled"
            # looks like a bug on the panel
            top_name = next(iter(kept))
            notes.append(f"所有被引渠道占比都不到 {MIN_SHARE_PCT:g}%,"
                         f"已保留被引最多的「{top_name}」兜底")
        usable = kept

    cold_start = not usable
    if cold_start:
        # No measured publishable sources: take publishable channels in cold-start order with decreasing weight.
        # cold_start_order is passed by the placement strategy (knowledge base prior: platforms preferred by high-priority engines first),
        # otherwise the module constant -- cold start is by definition "order by experience without data"; where the experience comes from is swappable.
        order = tuple(cold_start_order) if cold_start_order else COLD_START_ORDER
        fallback = [p for p in order if p in available]
        if not fallback:
            fallback = sorted(available)
        usable = {p: len(fallback) - i for i, p in enumerate(fallback[:per_day])}
        notes.append("实测信源里没有可投渠道,按冷启动默认顺序排期")

    alloc = _largest_remainder(usable, per_day)
    # Paid share cap: measured distributions are often dominated by paid channels like "Sohu account 51% + Zhihu 35%";
    # without a cap 86% of scheduling becomes paid (5/day x 30 days ~= thousands of CNY a month generated silently).
    # Excess slots move, from lowest cited weight up, to the highest-weight free channel; if there's no free channel keep them.
    paid_cap = int(per_day * PAID_MAX_SHARE)
    paid_slots = sum(n for p, n in alloc.items() if p in paid_only)
    free_pool = [p for p in sorted(usable, key=lambda x: -usable[x]) if p not in paid_only]
    if not free_pool:
        # The citation data has no own channel at all (the norm: everything cited is a platform where we have no account).
        # The own site is always publishable; use it to absorb the over-cap part, otherwise the cap is meaningless.
        free_pool = [p for p in COLD_START_ORDER
                     if p in available and p not in paid_only]
        if not free_pool and OWN_BLOG in available:
            free_pool = [OWN_BLOG]
    # Per-platform move counts go into paid_cap_moves -- previously only a vague note was kept, unable to answer
    # "was it moved from Sohu or Baijiahao, and how many articles"
    paid_cap_moves: list[dict] = []
    if paid_slots > paid_cap and free_pool:
        over = paid_slots - paid_cap
        for p in sorted((x for x in alloc if x in paid_only), key=lambda x: usable[x]):
            moved = 0
            while over > 0 and alloc.get(p, 0) > 0:
                alloc[p] -= 1
                alloc[free_pool[0]] = alloc.get(free_pool[0], 0) + 1
                over -= 1
                moved += 1
            if moved:
                paid_cap_moves.append({"from": p, "to": free_pool[0], "slots": moved})
            if over <= 0:
                break
        alloc = {p: n for p, n in alloc.items() if n > 0}
        notes.append(f"付费渠道占比已封顶到 {int(PAID_MAX_SHARE * 100)}%,超出的篇数改投自有渠道")

    total = sum(usable.values()) or 1
    breakdown = [
        {
            "platform": p,
            # The own channel absorbing overflow after the cap isn't in usable (it was never cited),
            # citation count is 0, but the slots are real -- can't take usable[p] directly, it would KeyError.
            "citations": usable.get(p, 0),
            "share_pct": round(usable.get(p, 0) / total * 100, 1),
            "slots": n,
            "paid": p in paid_only,
            "unit_price": rwj_prices.get(p, {}).get("price_median", 0.0) if p in paid_only else 0.0,
            "domains": sorted(domains_of.get(p, []), key=lambda d: -d["count"])[:5],
        }
        for p, n in sorted(alloc.items(), key=lambda kv: -kv[1])
    ]

    slots: list[str] = []
    for row in breakdown:
        slots.extend([row["platform"]] * row["slots"])
    slots = slots[:per_day] or [OWN_BLOG] * per_day

    # slot_plan: same length as slots, but carries "which channel this article goes through and who the candidate resources are".
    # _build_plan_window uses it to write channel / rwj_resource_id / media into plan rows,
    # so after approval publish_preset knows whether to use Ruanwenjiang or an own channel.
    slot_plan: list[dict] = []
    cand_cache: dict[str, list[dict]] = {}
    # Each platform's rank and citation data in breakdown -- carried per slot, so _build_plan_window
    # can write "why this article goes here" for each row
    rank_of = {row["platform"]: i + 1 for i, row in enumerate(breakdown)}
    stat_of = {row["platform"]: row for row in breakdown}
    moved_to = {m["to"] for m in paid_cap_moves}
    for p in slots:
        paid = p in paid_only
        if paid and p not in cand_cache:
            try:
                cand_cache[p] = pick_rwj_resources(db, p)
            except Exception:  # noqa: BLE001
                log.exception("[source-plan] failed to fetch candidate resources for %s", p)
                cand_cache[p] = []
        st = stat_of.get(p, {})
        if cold_start:
            reason = "cold_start"
        elif st.get("citations", 0) == 0 and p in moved_to:
            # It wasn't cited originally; its articles were moved here by paid-cap overflow
            reason = "paid_cap_fallback"
        else:
            reason = "source_quota"
        slot_plan.append({
            "platform": p,
            "channel": "rwj" if paid else ("own" if p == OWN_BLOG else "aitoearn"),
            "paid": paid,
            "unit_price": rwj_prices.get(p, {}).get("price_median", 0.0) if paid else 0.0,
            "candidates": cand_cache.get(p, []) if paid else [],
            # ↓ scheduling rationale (_build_plan_window writes it into the row-level why)
            "reason": reason,
            "citations": st.get("citations", 0),
            "share_pct": st.get("share_pct", 0.0),
            "day_slots": st.get("slots", 0),
            "rank": rank_of.get(p),
            "pool_size": len(cand_cache.get(p, [])) if paid else 0,
        })

    # Cost estimate: roughly how much a month of placements under this quota costs. Uses the median reference price from the resource library;
    # the real amount is subject to live price verification before publishing (rwj_channel's two-step confirmation), hence only an "estimate".
    per_day_yuan = round(sum(r["slots"] * (r["unit_price"] or 0.0) for r in breakdown), 2)
    cost = {
        "per_day_yuan": per_day_yuan,
        "per_month_yuan": round(per_day_yuan * 30, 2),
        "paid_slots": sum(r["slots"] for r in breakdown if r["paid"]),
        "free_slots": sum(r["slots"] for r in breakdown if not r["paid"]),
        "basis": "reference_median",
        "disclaimer": "按软文匠参考价中位数估算,实际以发布前核价为准",
    }

    return {
        "slots": slots,
        "slot_plan": slot_plan,
        "measured": measured,
        "breakdown": breakdown,
        "unavailable": unavailable,
        "excluded": excluded,
        "paid_cap_moves": paid_cap_moves,
        "profile_sites": profile_sites[:20],
        "unmapped": unmapped[:20],
        "available": sorted(available),
        "paid_only": sorted(paid_only),
        "paid_max_share": PAID_MAX_SHARE,
        "cost": cost,
        "notes": notes,
        # Structured copy of notes (with code / level), rendered by type in the frontend; the plain-text notes array
        # is kept as-is -- the source analysis panel joins it directly; don't break it
        "notes_struct": _structify_notes(notes),
        # Snapshot of decision thresholds: so old plans' rationale still makes sense if constants change later
        "thresholds": {
            "min_share_pct": MIN_SHARE_PCT,
            "paid_max_share": PAID_MAX_SHARE,
            "cold_start_order": list(cold_start_order or COLD_START_ORDER),
        },
        "responses_total": measured_total if by_platform else 0,
        "cold_start": cold_start,
    }


def resolve_quota(db: Session, topic_id: int, per_day: int, mode: str,
                  *, months: int = 1) -> tuple[Optional[dict], Optional[dict]]:
    """Compute platform quotas with the scheduling metric definition -- the single entry point shared by start / reschedule / monthly renewal.

    mode:
      sources   allocate by measured citation share (default)
      strategy  produce the placement strategy first, then allocate by the citation distribution weighted by engine priority
      template  no quota computation; keep the template's own applicable platforms (returns (None, None))

    Returns (quota, strategy); strategy has a value only when mode=strategy.
    Consolidated into one function because when the metric definition was spread across three places, adding a mode missed one of them -- renewal missed it,
    and strategy-scheduled plans silently fell back to plain source mode the next month.
    """
    if mode == "strategy":
        from geo.services.placement_strategy import build_strategy_quota
        strategy, quota = build_strategy_quota(db, topic_id, per_day, months=months)
        return quota, strategy
    if mode == "sources":
        return build_platform_quota(db, topic_id, per_day), None
    return None, None


def topic_target(db: Session, topic_id: int) -> str:
    t = db.get(AiTelemetryTopicORM, topic_id)
    return (t.target or t.name or "") if t is not None else ""
