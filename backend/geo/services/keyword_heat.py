"""Keyword search heat collection -- external indexes + model estimates, results written to the keyword_heat cache table.

## Why this layer exists

The heatmap's previous "heat value" = number of AI hits on the term within a window, which is our exposure strength,
not real market search demand. This module adds the second metric definition, "search heat", collected from real external data sources.

## Collection is offline

The report API (competitive-analysis) **only reads the cache** and never calls out within a request: external sources are slow
(Baidu Index is one request per term and needs rate limiting), fail (risk control / 429), and may need LLM calls.
Collection relies on a scheduled job (`backend/scripts/refresh_keyword_heat.py`) + a manual refresh button on the page.
Expired cache is not deleted, only marked stale and still shown until the next collection round.

## Data source priority (per-term degradation, stops at the first success)

| Source | Meaning of the value | Prerequisite |
|---|---|---|
| `baidu_index` Baidu Index | real daily average search index | Baidu login cookie -- reuses storage_state of `engine='wenxin'` in the account pool (ERNIE Bot uses a Baidu account) |
| `google_trends` Google Trends | relative heat 0-100 | egress IP not hit by 429; datacenter IPs almost always fail, configure `KEYWORD_HEAT_HTTPS_PROXY` (residential proxy) |
| `oceanengine` Ocean Engine Suanshu | Douyin-side content heat | `OCEANENGINE_COOKIE` + `OCEANENGINE_HEAT_URL`; the whole source is skipped if not configured |
| `llm` model estimate | relative heat 0-100 from DeepSeek | just needs a key, always the fallback |

With geo='cn', try Baidu Index first; with geo='global', try Google Trends first; then Ocean Engine Suanshu -> model estimate in order.

## The reality of long-tail terms

Questions like "商用清洁机器人哪个品牌好" ("which brand of commercial cleaning robot is best") have no data in any search index (indexes only cover short terms with
enough search volume). So each prompt first has its "core term" extracted: external sources look up the core term, while the model estimate
scores the original full sentence. The core term prefers the model's extraction, falling back to rules when the model is unavailable.

## Normalization

Sources have completely different scales (Baidu Index from hundreds to tens of thousands, Google 0-100, model 0-100), so `heat`
is a relative value normalized to 0-100 by the max **within the same source and batch**, and `raw_value` keeps the source's raw number.
heat across sources is not strictly comparable -- so the frontend labels the source on every block and never mixes numbers from different sources in one ranking.

## ENV

    KEYWORD_HEAT_TTL_DAYS        cache validity (days), default 14
    KEYWORD_HEAT_BATCH_LIMIT     max terms per refresh, default 60
    KEYWORD_HEAT_HTTPS_PROXY     dedicated proxy for Google Trends, defaults to the process HTTPS_PROXY
                                 (on the test host it is the SS egress at http://127.0.0.1:18080;
                                  connecting directly to trends.google.com gives Network unreachable)
    BAIDU_INDEX_CIPHER_TEXT      Baidu Index Cipher-Text header (capture one from the browser and paste it in);
                                 without it only the cookie is sent -> very likely flagged 10018 abnormal access
    KEYWORD_HEAT_SOURCES         comma-separated allowlist of enabled sources, all on by default
    OCEANENGINE_COOKIE           Ocean Engine Suanshu login cookie
    OCEANENGINE_HEAT_URL         Ocean Engine Suanshu data URL template, with a {kw} placeholder
    OCEANENGINE_HEAT_JSON_PATH   dot path of the heat value in the response JSON, auto-detected by default
    DEEPSEEK_API_KEY             model estimate (falls back to OPENROUTER_API_KEY by default)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session, undefer

from geo.models.engine_sessions import EngineSessionORM
from geo.models.keyword_heat import (
    ALL_SOURCES,
    SOURCE_BAIDU_INDEX,
    SOURCE_GOOGLE_TRENDS,
    SOURCE_LLM,
    SOURCE_OCEANENGINE,
    KeywordHeatORM,
)
from geo.services.llm_usage import track

log = logging.getLogger(__name__)

TTL_DAYS = int(os.environ.get("KEYWORD_HEAT_TTL_DAYS", "14"))
BATCH_LIMIT = int(os.environ.get("KEYWORD_HEAT_BATCH_LIMIT", "60"))

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class ProviderUnavailable(Exception):
    """The whole source is currently unavailable (not configured / risk control / rate limited) -- skip it this round and degrade directly."""


# ----------------------- Term normalization / core terms -----------------------

def norm_key(word: str) -> str:
    """Cache key: trim leading/trailing whitespace + collapse inner whitespace + lowercase (Chinese unaffected)."""
    return re.sub(r"\s+", " ", (word or "").strip()).lower()


# Question / intent / modifier words -- stripped when extracting the core term, leaving only the category subject
_TAIL_NOISE = re.compile(
    r"(哪个品牌好|哪个牌子好|哪家好|哪家强|哪个好|哪些品牌|什么品牌|哪个厂家|"
    r"怎么样|怎么选|如何选择|如何挑选|值得买吗|贵不贵|多少钱|排行榜|排名|推荐|"
    r"有哪些|哪里买|靠谱吗|好用吗|测评|对比|区别|十大|前十|top\s*\d+|best|"
    r"which\s+is\s+best|how\s+to\s+choose|reviews?|vs\.?)",
    re.I,
)
_PUNCT = re.compile(r"[?？!!,,。;;::、\"'“”‘’()()\[\]【】]+")


def rule_core_word(word: str) -> str:
    """Rule-based core term extraction -- fallback when the model is unavailable. Strip question/intent words + punctuation, the rest is the category subject."""
    s = _PUNCT.sub(" ", word or "")
    s = _TAIL_NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Too short after stripping (e.g. the whole sentence was question phrasing) -> fall back to the original term without punctuation
    if len(s) < 2:
        s = re.sub(r"\s+", " ", _PUNCT.sub(" ", word or "")).strip()
    return s[:40]


# ----------------------- Cache reads (the request path only goes here) -----------------------

def read_cached(db: Session, keywords: Iterable[str], geo: str = "cn") -> dict[str, dict]:
    """Return cached heat by original term; terms never collected are simply missing as keys (the caller shows "pending collection").

    Expired rows are still returned with `stale=True` -- an old value is more useful than none; refreshing is the scheduled job's job.
    """
    keys = {norm_key(k): k for k in keywords if norm_key(k)}
    if not keys:
        return {}
    rows = (db.query(KeywordHeatORM)
            .filter(KeywordHeatORM.geo == geo, KeywordHeatORM.keyword.in_(list(keys.keys())))
            .all())
    now = datetime.utcnow()
    out: dict[str, dict] = {}
    for r in rows:
        orig = keys.get(r.keyword)
        if not orig:
            continue
        out[orig] = {
            "heat": int(r.heat or 0),
            "raw_value": r.raw_value,
            "source": r.source,
            "core_word": r.core_word or "",
            "stale": bool(r.expires_at and r.expires_at < now),
            "fetched_at": r.fetched_at,
        }
    return out


def cache_stats(db: Session, keywords: Iterable[str], geo: str = "cn") -> dict:
    """Cache coverage -- for "collected X / total Y" next to the frontend refresh button."""
    words = [w for w in {norm_key(k) for k in keywords} if w]
    cached = read_cached(db, [k for k in keywords if norm_key(k)], geo)
    now = datetime.utcnow()
    fresh = sum(1 for v in cached.values() if not v["stale"])
    latest = max((v["fetched_at"] for v in cached.values() if v["fetched_at"]), default=None)
    return {
        "total": len(words), "cached": len(cached), "fresh": fresh,
        "fetched_at": latest.isoformat() + "Z" if latest else None,
        "now": now.isoformat() + "Z",
    }


# ----------------------- Source 1: Baidu Index -----------------------

def _baidu_cookie(db: Session) -> str:
    """Get a Baidu login cookie from the account pool (ERNIE Bot = Baidu account).

    Only rows with status='active' and BDUSS in storage_state count -- BDUSS is Baidu's login ticket,
    and without it index.baidu.com always returns "not logged in / abnormal access".
    """
    rows = (db.query(EngineSessionORM)
            .options(undefer(EngineSessionORM.storage_state))   # Parsed row by row below, loaded in one go (#43)
            .filter(EngineSessionORM.engine == "wenxin", EngineSessionORM.status == "active")
            .order_by(EngineSessionORM.captured_at.desc())
            .limit(8).all())
    for row in rows:
        try:
            state = json.loads(row.storage_state or "{}")
        except Exception:  # noqa: BLE001
            continue
        pairs = []
        for c in state.get("cookies") or []:
            dom = str(c.get("domain") or "")
            if "baidu.com" not in dom:
                continue
            name, val = c.get("name"), c.get("value")
            if name and val:
                pairs.append(f"{name}={val}")
        if any(p.startswith("BDUSS=") for p in pairs):
            return "; ".join(pairs)
    raise ProviderUnavailable("账号池里没有带 BDUSS 的百度(文心)登录态")


def _baidu_decrypt(ptbk: str, encrypted: str) -> list[int]:
    """Baidu Index curve decryption: the first half of ptbk is the cipher alphabet, the second half the matching plaintext; substitute char by char."""
    half = len(ptbk) // 2
    table = {ptbk[i]: ptbk[half + i] for i in range(half)}
    plain = "".join(table.get(ch, ch) for ch in encrypted)
    out = []
    for seg in plain.split(","):
        seg = seg.strip()
        if seg.isdigit():
            out.append(int(seg))
    return out


def _baidu_index(db: Session, words: list[str], days: int = 30) -> dict[str, int]:
    """One request per term for the daily average search index over the last N days. Returns {core term: daily index}; terms without data are excluded.

    Measured 2026-08: with only the cookie it is flagged status=10018 "suspected abnormal access" -- Baidu Index now also requires
    a `Cipher-Text` header generated by frontend JS. Paste the Cipher-Text value from the browser (F12 -> any SearchApi request)
    into env `BAIDU_INDEX_CIPHER_TEXT` and it works for a while;
    expiry shows up as 10018 again. Fully maintenance-free would require browser-service to fetch within the page
    (letting the site sign it itself, same approach as qwen's half-browser HTTP).
    """
    cookie = _baidu_cookie(db)
    headers = {
        "Cookie": cookie, "User-Agent": UA,
        "Referer": "https://index.baidu.com/v2/main/index.html",
        "Accept": "application/json, text/plain, */*",
    }
    cipher = (os.environ.get("BAIDU_INDEX_CIPHER_TEXT") or "").strip()
    if cipher:
        headers["Cipher-Text"] = cipher
    out: dict[str, int] = {}
    fails = 0
    last_err = ""
    with httpx.Client(timeout=20, headers=headers, trust_env=False) as cli:
        for i, w in enumerate(words):
            if fails >= 3:
                # Consecutive failures most likely mean an invalid cookie / triggered risk control; abandon the whole source rather than burn the account
                raise ProviderUnavailable(f"百度指数连续 {fails} 次失败(cookie 失效或风控)")
            try:
                word_param = json.dumps([[{"name": w, "wordType": 1}]], ensure_ascii=False)
                r = cli.get("https://index.baidu.com/api/SearchApi/index",
                            params={"area": 0, "word": word_param, "days": days})
                body = r.json()
                if body.get("status") != 0:
                    fails += 1
                    # 10018 is the **only actionable** one: the cookie is fine, what is missing is the
                    # Cipher-Text header generated by browser JS. Single it out with an actionable message -- previously every status
                    # was lumped into "status=10018 您好,百度指数监测到..." ("Hello, Baidu Index detected..."), and the skip reason on the frontend
                    # was just "skipped: Baidu Index", so nobody knew a header had to be pasted.
                    if str(body.get("status")) == "10018":
                        raise ProviderUnavailable(
                            "百度指数要 Cipher-Text 头(cookie 是好的,status=10018)。"
                            "去浏览器打开 index.baidu.com → F12 → 任一 SearchApi 请求 → "
                            "复制请求头 Cipher-Text 的值 → 写进 env BAIDU_INDEX_CIPHER_TEXT "
                            "并重启后端。这个值会过期,再次出现本提示就是要重贴了。"
                            if not cipher else
                            "百度指数的 Cipher-Text 已过期(status=10018)。"
                            "按上次的做法从 index.baidu.com 的 SearchApi 请求头重新复制一个,"
                            "更新 env BAIDU_INDEX_CIPHER_TEXT 后重启后端。")
                    last_err = f"status={body.get('status')} {str(body.get('message') or '')[:60]}"
                    log.info("baidu_index %s → status=%s %s", w, body.get("status"), body.get("message"))
                    continue
                data = body.get("data") or {}
                idx = ((data.get("userIndexes") or [{}])[0].get("all") or {}).get("data") or ""
                uniqid = data.get("uniqid") or ""
                if not idx or not uniqid:
                    continue     # No index data for this term (search volume too low) -- normal, not a failure
                pt = cli.get("https://index.baidu.com/Interface/ptbk",
                             params={"uniqid": uniqid}).json()
                ptbk = pt.get("data") or ""
                vals = _baidu_decrypt(ptbk, idx) if ptbk else []
                if vals:
                    out[w] = int(round(sum(vals) / len(vals)))
                fails = 0
            except ProviderUnavailable:
                raise
            except Exception as e:  # noqa: BLE001
                fails += 1
                last_err = str(e)[:80]
                log.info("baidu_index %s failed: %s", w, e)
            if i < len(words) - 1:
                time.sleep(1.3)   # Rate limit: Baidu Index is very sensitive to high-frequency access
    # Nothing obtained and errors were indeed reported -> report the whole source as unavailable, otherwise "no data" and "risk-controlled"
    # look the same in stats (with few terms the consecutive failure count may not reach the threshold, making it easier to miss)
    if not out and last_err:
        raise ProviderUnavailable(f"百度指数取数失败({last_err})")
    return out


# ----------------------- Source 2: Google Trends -----------------------

def _trends_json(text: str) -> Any:
    """Google Trends responses are prefixed with ")]}'\\n"; strip it to get JSON."""
    i = text.find("{")
    return json.loads(text[i:]) if i >= 0 else {}


def _google_trends(words: list[str], geo_code: str = "", timeframe: str = "today 12-m") -> dict[str, int]:
    """explore for the token -> widgetdata/multiline for the curve, mean taken as heat (0-100)."""
    proxy = os.environ.get("KEYWORD_HEAT_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY") or None
    out: dict[str, int] = {}
    kw: dict[str, Any] = {"proxy": proxy} if proxy else {}
    with httpx.Client(timeout=25, headers={"User-Agent": UA}, follow_redirects=True,
                      trust_env=False, **kw) as cli:
        try:
            cli.get("https://trends.google.com/")     # Get the NID cookie to reduce 429s
        except Exception as e:  # noqa: BLE001
            raise ProviderUnavailable(f"谷歌趋势不可达: {e}") from e
        for i, w in enumerate(words):
            try:
                req = {"comparisonItem": [{"keyword": w, "geo": geo_code, "time": timeframe}],
                       "category": 0, "property": ""}
                r = cli.get("https://trends.google.com/trends/api/explore",
                            params={"hl": "zh-CN", "tz": "-480", "req": json.dumps(req, ensure_ascii=False)})
                if r.status_code == 429:
                    raise ProviderUnavailable("谷歌趋势 429(出口 IP 被限流,需住宅代理)")
                widgets = (_trends_json(r.text) or {}).get("widgets") or []
                ts = next((x for x in widgets if x.get("id") == "TIMESERIES"), None)
                if not ts:
                    continue
                # widgetdata hits rate limits more easily than explore; back off and retry once on 429 before declaring failure
                for attempt in range(2):
                    r2 = cli.get("https://trends.google.com/trends/api/widgetdata/multiline",
                                 params={"hl": "zh-CN", "tz": "-480",
                                         "req": json.dumps(ts.get("request") or {}, ensure_ascii=False),
                                         "token": ts.get("token") or ""})
                    if r2.status_code != 429:
                        break
                    if attempt == 0:
                        time.sleep(5)
                if r2.status_code == 429:
                    raise ProviderUnavailable("谷歌趋势 429(出口 IP 被限流,需住宅代理)")
                tl = ((_trends_json(r2.text) or {}).get("default") or {}).get("timelineData") or []
                vals = [int((p.get("value") or [0])[0]) for p in tl if p.get("value")]
                if vals:
                    out[w] = int(round(sum(vals) / len(vals)))
            except ProviderUnavailable:
                raise
            except Exception as e:  # noqa: BLE001
                log.info("google_trends %s failed: %s", w, e)
            if i < len(words) - 1:
                time.sleep(1.0)
    return out


# ----------------------- Source 3: Ocean Engine Suanshu -----------------------

def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except Exception:  # noqa: BLE001
                return None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _oceanengine(words: list[str]) -> dict[str, int]:
    """Ocean Engine Suanshu (Douyin) -- the open API varies a lot by account/version, so both the URL and the value path come from env config.

    `OCEANENGINE_HEAT_URL` uses {kw} as the keyword placeholder; `OCEANENGINE_HEAT_JSON_PATH`
    points to the heat number (dot path, array indexes supported). Missing either -> the whole source is skipped.
    """
    cookie = os.environ.get("OCEANENGINE_COOKIE") or ""
    url_tpl = os.environ.get("OCEANENGINE_HEAT_URL") or ""
    if not cookie or not url_tpl:
        raise ProviderUnavailable("巨量算数未配置(需要 OCEANENGINE_COOKIE + OCEANENGINE_HEAT_URL)")
    paths = [p for p in (os.environ.get("OCEANENGINE_HEAT_JSON_PATH") or "").split("|") if p]
    paths = paths or ["data.hot_score", "data.score", "data.list.0.hot_score", "data.value"]
    out: dict[str, int] = {}
    headers = {"Cookie": cookie, "User-Agent": UA, "Accept": "application/json, text/plain, */*",
               "Referer": "https://trendinsight.oceanengine.com/"}
    with httpx.Client(timeout=20, headers=headers, trust_env=False) as cli:
        for i, w in enumerate(words):
            try:
                r = cli.get(url_tpl.replace("{kw}", quote(w, safe="")))
                body = r.json()
                val = next((v for v in (_dig(body, p) for p in paths)
                            if isinstance(v, (int, float))), None)
                if val is not None:
                    out[w] = int(round(float(val)))
            except Exception as e:  # noqa: BLE001
                log.info("oceanengine %s failed: %s", w, e)
            if i < len(words) - 1:
                time.sleep(0.8)
    if not out:
        raise ProviderUnavailable("巨量算数无有效返回(cookie 过期或取值路径不对)")
    return out


# ----------------------- Source 4: model estimate -----------------------

_LLM_PROMPT = """你是搜索营销数据分析师。下面是某品牌在 AI 搜索里监测的一批查询词。

品牌:{brand}
行业 / 品类:{industry}
地域口径:{geo_hint}

对每一条查询词做两件事:
1. core:拆出这条词的「核心搜索词」—— 剥掉「哪个好 / 怎么样 / 推荐 / 排行」这类问法和修饰,
   只留能在搜索引擎里有搜索量的品类或产品短词(2-12 个字)。
2. heat:估算这条查询词在真实搜索里的需求热度,0-100 的相对值。
   100 = 该行业里搜索量最大的头部词;20 以下 = 冷门长尾。
   判断依据:词的宽泛程度、是否高购买意图、是否口语化长尾、季节性。
   同一批词之间要能明显区分开,不要都给相近的分数。

只输出 JSON,形如:
{{"items":[{{"query":"原词原样","core":"核心词","heat":57}}]}}

查询词清单:
{words}"""


def _llm_call(prompt: str, timeout: int = 90) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        with track("keyword_heat", "deepseek", DEEPSEEK_MODEL, endpoint=url) as t:
            r = httpx.post(url,
                           headers={"Authorization": f"Bearer {key}"},
                           json={"model": DEEPSEEK_MODEL,
                                 "messages": [{"role": "user", "content": prompt}],
                                 "response_format": {"type": "json_object"},
                                 "temperature": 0.2},
                           timeout=timeout)
            t.http_status = r.status_code
            if r.status_code < 400:
                t.usage = r.json()
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        # Note: this branch has historically **not passed response_format** (the direct branch above does).
        # This is an existing behavioral difference, not an omission -- some OpenRouter models return 400 on that field.
        with track("keyword_heat", "openrouter", OPENROUTER_DEEPSEEK_MODEL,
                   endpoint=OPENROUTER_URL) as t:
            r = httpx.post(OPENROUTER_URL, headers={"Authorization": f"Bearer {key}"},
                           json={"model": OPENROUTER_DEEPSEEK_MODEL,
                                 "messages": [{"role": "user", "content": prompt}],
                                 "temperature": 0.2},
                           timeout=timeout)
            t.http_status = r.status_code
            if r.status_code < 400:
                t.usage = r.json()
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    raise ProviderUnavailable("模型估算未配置(需要 DEEPSEEK_API_KEY 或 OPENROUTER_API_KEY)")


def _llm_estimate(words: list[str], brand: str, industry: str, geo: str) -> dict[str, dict]:
    """Batch estimate -> {original term: {"core": core term, "heat": 0-100}}. Raises ProviderUnavailable when the whole batch fails."""
    out: dict[str, dict] = {}
    geo_hint = "中国大陆(百度 / 微信 / 抖音搜索)" if geo == "cn" else "海外(谷歌搜索)"
    for i in range(0, len(words), 25):
        chunk = words[i:i + 25]
        prompt = _LLM_PROMPT.format(
            brand=brand or "(未提供)", industry=industry or "(未提供)", geo_hint=geo_hint,
            words="\n".join(f"- {w}" for w in chunk))
        try:
            txt = _llm_call(prompt)
            body = json.loads(re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip())
            for it in body.get("items") or []:
                q = str(it.get("query") or "").strip()
                if not q:
                    continue
                heat = it.get("heat")
                try:
                    heat = max(0, min(100, int(round(float(heat)))))
                except Exception:  # noqa: BLE001
                    continue
                out[q] = {"core": str(it.get("core") or "").strip()[:40], "heat": heat}
        except ProviderUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("model estimate batch failed (%d terms): %s", len(chunk), e)
    if not out:
        raise ProviderUnavailable("模型估算无有效返回")
    return out


# ----------------------- Refresh main flow -----------------------

def _enabled(source: str) -> bool:
    white = [s.strip() for s in (os.environ.get("KEYWORD_HEAT_SOURCES") or "").split(",") if s.strip()]
    return source in white if white else source in ALL_SOURCES


def _normalize(raw: dict[str, int]) -> dict[str, int]:
    """Normalize to 1-100 by the max within the same source and batch (scales differ across sources and cannot be normalized together)."""
    if not raw:
        return {}
    mx = max(raw.values()) or 1
    return {k: max(1, min(100, int(round(v / mx * 100)))) for k, v in raw.items()}


def pending_keywords(db: Session, keywords: Iterable[str], geo: str = "cn",
                     force: bool = False) -> list[str]:
    """Select terms needing collection: never collected + expired (with force=True, recollect all)."""
    uniq: list[str] = []
    seen: set[str] = set()
    for w in keywords:
        k = norm_key(w)
        if k and k not in seen:
            seen.add(k)
            uniq.append(w)
    if force:
        return uniq
    cached = read_cached(db, uniq, geo)
    return [w for w in uniq if w not in cached or cached[w]["stale"]]


def refresh(db: Session, keywords: Iterable[str], *, brand: str = "", industry: str = "",
            geo: str = "cn", force: bool = False, limit: Optional[int] = None) -> dict:
    """Collect and write the cache. Returns this round's stats, **never raises** -- each source degrades independently; if all fail only the model estimate remains.

    Callers: the scheduled job `scripts/refresh_keyword_heat.py` and the page's manual refresh endpoint.
    """
    todo = pending_keywords(db, keywords, geo, force)[: (limit or BATCH_LIMIT)]
    stats: dict[str, Any] = {"requested": len(todo), "written": 0, "by_source": {}, "skipped": {}}
    if not todo:
        return stats

    # (1) Model estimate runs first: gets both the core terms (needed by external sources) and fallback heat
    est: dict[str, dict] = {}
    if _enabled(SOURCE_LLM):
        try:
            est = _llm_estimate(todo, brand, industry, geo)
        except ProviderUnavailable as e:
            stats["skipped"][SOURCE_LLM] = str(e)
    cores = {w: (est.get(w, {}).get("core") or rule_core_word(w)) for w in todo}

    # (2) External sources prioritized by geo, fetched source by source (core term metric definition)
    order = ([SOURCE_BAIDU_INDEX, SOURCE_GOOGLE_TRENDS] if geo == "cn"
             else [SOURCE_GOOGLE_TRENDS, SOURCE_BAIDU_INDEX]) + [SOURCE_OCEANENGINE]
    external: dict[str, tuple[str, int, int]] = {}   # original term -> (source, raw, heat)
    for src in order:
        if not _enabled(src):
            continue
        rest = [w for w in todo if w not in external]
        if not rest:
            break
        core_list = sorted({cores[w] for w in rest if cores[w]})
        try:
            if src == SOURCE_BAIDU_INDEX:
                raw = _baidu_index(db, core_list)
            elif src == SOURCE_GOOGLE_TRENDS:
                raw = _google_trends(core_list, "CN" if geo == "cn" else "")
            else:
                raw = _oceanengine(core_list)
        except ProviderUnavailable as e:
            stats["skipped"][src] = str(e)
            continue
        except Exception as e:  # noqa: BLE001
            stats["skipped"][src] = f"异常: {e}"
            continue
        heats = _normalize(raw)
        for w in rest:
            c = cores[w]
            if c in raw:
                external[w] = (src, raw[c], heats[c])

    # (3) Persist: external sources first, model estimate for the rest; terms with neither are skipped (collected next round)
    now = datetime.utcnow()
    exp = now + timedelta(days=TTL_DAYS)
    for w in todo:
        key = norm_key(w)
        if w in external:
            src, raw_v, heat = external[w]
            detail = {"origin": w, "core": cores[w], "raw": raw_v}
        elif w in est:
            src, raw_v, heat = SOURCE_LLM, est[w]["heat"], est[w]["heat"]
            detail = {"origin": w, "core": cores[w], "model": DEEPSEEK_MODEL}
        else:
            continue
        row = (db.query(KeywordHeatORM)
               .filter(KeywordHeatORM.keyword == key, KeywordHeatORM.geo == geo)
               .one_or_none())
        if row is None:
            row = KeywordHeatORM(keyword=key, geo=geo)
            db.add(row)
        row.core_word = cores[w] or ""
        row.heat = heat
        row.raw_value = raw_v
        row.source = src
        row.detail_json = json.dumps(detail, ensure_ascii=False)
        row.fetched_at = now
        row.expires_at = exp
        stats["written"] += 1
        stats["by_source"][src] = stats["by_source"].get(src, 0) + 1
    db.commit()
    log.info("keyword_heat refresh: %s", stats)
    return stats
