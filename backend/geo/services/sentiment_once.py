"""One-off sentiment monitoring quick check -- one-time sentiment capability that creates no account.

Product-wise sentiment monitoring has **two lines**; don't mix them:

  - One-off (this module): Yuanbao WebSearch runs one round on the spot, **creates no sentiment_account, writes no tenant tables,
    joins no batch run**; results land only in a 24h cache. Just a brand name is enough to run.
  - Daily health: sentiment_account + sentinel crawl pipeline, subscription-based, continuous batch runs, with alerts and history.
    See geo/api/sentiment.py and agents/geo/tools.py::get_sentiment_today.

Why "start + poll" instead of waiting on the spot: one quick check can take up to 360s
(SENTIMENT_WEBSEARCH_TIMEOUT), while one agent conversation turn has a 240s wall-clock limit; waiting synchronously would inevitably
kill the whole turn and the user gets a timeout. So after starting it goes to a background thread, results go to a 24h cache, and the next message reads the cache.

Progress state lives in the cache (Redis), not in-process: production geo-agent runs 8 uvicorn workers,
start and poll almost always land in different processes; an in-process dict would never find it = the user forever sees "still running".

★ When Redis is unavailable **don't pretend to queue** -- run one round synchronously (with a hard cap); if it doesn't finish, say so honestly.
  "Queued but the result can't be stored" is the worst kind of bad: the user keeps waiting for something that will never arrive.
  (This pipeline has already tripped on it once: reports stuck in generating forever, see c0421011.)

The cache key is **shared** with the sentiment section of one-click diagnosis (cache_key_for), so one probe for the same brand and terms hits on both sides,
without burning Yuanbao calls twice. **The key is partitioned by user_id** -- the cache is readable across accounts; without partitioning it's unauthorized reads
(before the fix, B running a brand A had run would directly hit A's result, negative post bodies included). So every function in this module
requires user_id passed all the way down, no exceptions; the anonymous side passes f"anon:{device_id}".

**Category (industry) must be passed all the way down**: when Yuanbao WebSearch searches by brand name only, same-named entities in other industries
get pulled in, and brands like "GEO Analytics" with an industry word in their name are worse -- what comes back is the whole
GEO industry's negatives, attributed to this one brand (2026-09-03 feedback verbatim: "negative sentiment about the GEO industry
was also attributed to GEO Analytics"). `_account_snapshot` has long supported disambiguation by category (report #99's
<brand-a> vs <namesake-company> was fixed that way), but **the quick-check entry never passed it** -- industry was always None.
Now it's derived from the topic's industry/seed words and passed all the way down.

**The charging criterion is "did this round actually burn Yuanbao calls", not "did it produce a result"** (_worker):
cache hits (run by yourself within 24h, or a full diagnosis just ran the same content for free) aren't charged.
industry_terms is part of the cache key; when both sides pass the same category, the quick check the diagnosis just ran hits directly;
when they differ, `_free_diagnosis_result` recomputes the diagnosis key from the report row to look it up,
and if found reuses it and skips `bill_one`.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from geo.services import cache_service
from geo.services.full_diagnosis_sentiment import cache_key_for

log = logging.getLogger(__name__)

# TTL of the in-progress marker: a bit longer than the "runaway" threshold so the stale verdict gets a chance to take effect first
_RUNNING_TTL_S = 30 * 60
# No result after this long means this run failed -- allow restarting; never hang indefinitely
_STALE_AFTER_S = 15 * 60
# Hard cap (seconds) for the synchronous fallback when Redis is unavailable: must be < the agent's 240s turn, leaving room for model round trips
SYNC_CAP_S = 150


def _flag_key(user_id, brand: str, terms: Optional[List[str]],
              industry: Optional[List[str]] = None) -> str:
    return cache_key_for(brand, terms, user_id, industry) + ":running"


def _norm_terms(brand: str, terms: Optional[List[str]]) -> List[str]:
    out = [t.strip() for t in (terms or []) if t and t.strip()]
    return out or [brand]


def _norm_industry(industry: Optional[List[str]]) -> Optional[List[str]]:
    out = [t.strip() for t in (industry or []) if t and t.strip()][:3]
    return out or None


def peek(user_id, brand: str, terms: Optional[List[str]] = None,
         industry: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Result already produced (within 24h), else None."""
    return cache_service.get_cached_report(
        cache_key_for(brand, _norm_terms(brand, terms), user_id, _norm_industry(industry)))


def status(user_id, brand: str, terms: Optional[List[str]] = None,
           industry: Optional[List[str]] = None) -> Dict[str, Any]:
    """Current state of this run: ready / running / failed / stale / none."""
    terms = _norm_terms(brand, terms)
    industry = _norm_industry(industry)
    result = peek(user_id, brand, terms, industry)
    if result is not None:
        return {"state": "ready", "result": result}

    flag = cache_service.get_cached_report(_flag_key(user_id, brand, terms, industry)) or {}
    started = float(flag.get("started_at") or 0)
    if flag.get("failed"):
        return {"state": "failed", "error": str(flag.get("error") or "")[:200]}
    if not started:
        return {"state": "none"}
    elapsed = int(time.time() - started)
    if elapsed > _STALE_AFTER_S:
        # Runaway (process restart / upstream hung). Clear the marker so the user can restart instead of waiting forever.
        cache_service.delete_cached_report(_flag_key(user_id, brand, terms, industry))
        return {"state": "stale", "elapsed_s": elapsed}
    return {"state": "running", "elapsed_s": elapsed}


def _mark_failed(user_id, brand: str, terms: List[str], err: str,
                 industry: Optional[List[str]] = None) -> None:
    cache_service.set_cached_report(
        _flag_key(user_id, brand, terms, industry),
        {"failed": True, "error": err[:300], "started_at": time.time()},
        ttl_s=10 * 60,
    )


def bill_one(user_id, brand: str) -> None:
    """Charge once on success. **Only call after a real result** -- a failed round isn't billed to the user.

    The idempotency key pins "account x Beijing day x brand": repeating the same brand on the same day already hits the 24h
    cache (never reaching here); if the cache is lost and it reruns, it's still charged only once.
    Ops accounts and the anonymous side (user_id is an "anon:xxx" string) are never charged.
    """
    if not isinstance(user_id, int):
        return
    from geo.database import SessionLocal
    from geo.models.balance import KIND_SENTIMENT_ONCE, PRICE_SENTIMENT_ONCE
    from geo.models.user import UserORM
    from geo.services import balance_service

    db = SessionLocal()
    try:
        fee = balance_service.get_price(db, PRICE_SENTIMENT_ONCE)
        if fee <= 0:
            return
        user = db.get(UserORM, user_id)
        if user is None or (getattr(user, "role", "") or "user") in ("admin", "operator"):
            return
        day = balance_service._bj_date()
        balance_service.charge(
            db, user_id=user_id, kind=KIND_SENTIMENT_ONCE, amount_cents=fee,
            idem_key=f"sentquick:{user_id}:{day}:{brand[:64]}",
            ref_type="sentiment_once", ref_id=brand[:64],
            note={"brand": brand[:80], "bj_date": day, "mode": "quick"},
        )
        db.commit()
    except Exception:  # noqa: BLE001 — a billing failure shouldn't swallow a result that was already produced
        log.warning("[sentiment_once] billing failed user=%s brand=%s", user_id, brand, exc_info=True)
        db.rollback()
    finally:
        db.close()


def can_afford(user_id) -> bool:
    """Enough balance for one run? Ops / anonymous / zero price always True."""
    if not isinstance(user_id, int):
        return True
    from geo.database import SessionLocal
    from geo.models.balance import PRICE_SENTIMENT_ONCE
    from geo.models.user import UserORM
    from geo.services import balance_service

    db = SessionLocal()
    try:
        fee = balance_service.get_price(db, PRICE_SENTIMENT_ONCE)
        if fee <= 0:
            return True
        user = db.get(UserORM, user_id)
        if user is None or (getattr(user, "role", "") or "user") in ("admin", "operator"):
            return True
        return balance_service.can_afford(db, user_id, fee)
    except Exception:  # noqa: BLE001
        return True                       # if unreadable, allow; don't let billing block the feature
    finally:
        db.close()


def _free_diagnosis_result(user_id, brand: str) -> Optional[Dict[str, Any]]:
    """Whether the latest **full diagnosis** already ran sentiment monitoring for this brand for free.

    The diagnosis sentiment section shares `cache_key_for` with this module, but the two feed different arguments: diagnosis passes
    the report row's brand_terms / industry_terms, the quick check passes the result of `_norm_terms`,
    with industry_terms always None. `industry_terms` is part of the cache key, so as long as
    the user filled in industry words, the quick check is guaranteed to miss the content the diagnosis just produced -- so **the same content
    is given once for free and then charged again**.

    Here the diagnosis key is recomputed from the report row to look it up. If found, reuse it directly, free of charge:
    that round already ran; there is no second Yuanbao call.
    """
    if not isinstance(user_id, int) or not brand.strip():
        return None
    import json

    from geo.database import SessionLocal
    from geo.models.full_diagnosis import FullDiagnosisReportORM

    db = SessionLocal()
    try:
        row = (db.query(FullDiagnosisReportORM)
               .filter(FullDiagnosisReportORM.user_id == user_id,
                       FullDiagnosisReportORM.brand_name == brand[:128],
                       FullDiagnosisReportORM.deleted_at.is_(None))
               .order_by(FullDiagnosisReportORM.id.desc()).first())
        if row is None:
            return None

        def _list(raw: str) -> List[str]:
            try:
                v = json.loads(raw or "[]")
                return [str(x) for x in v] if isinstance(v, list) else []
            except Exception:  # noqa: BLE001
                return []

        key = cache_key_for(brand, _list(row.brand_terms_json) or None, user_id,
                            _list(row.industry_terms_json) or None)
    except Exception:  # noqa: BLE001 — reuse is an optimization; if the lookup fails run as usual, don't block the feature
        log.warning("[sentiment_once] lookup for reusable diagnosis result failed brand=%s", brand, exc_info=True)
        return None
    finally:
        db.close()
    return cache_service.get_cached_report(key)


def _worker(user_id: int, brand: str, terms: List[str],
            industry: Optional[List[str]] = None) -> None:
    from geo.services.full_diagnosis_sentiment import run_sentiment_cached

    try:
        key = cache_key_for(brand, terms, user_id, industry)
        # ★ The charging criterion is "did this round actually burn Yuanbao calls", not "did it produce a result".
        #   Cache hits (run by yourself within 24h, or a full diagnosis just ran the same content for free) aren't charged.
        reuse = cache_service.get_cached_report(key)
        if reuse is None:
            reuse = _free_diagnosis_result(user_id, brand)
            if reuse is not None:
                # Write back under the quick-check key so peek/status can read it (they don't recognize the diagnosis key)
                from geo.services.advanced_runners import _ADVANCED_TTL_S
                cache_service.set_cached_report(key, reuse, ttl_s=_ADVANCED_TTL_S)
        if reuse is None:
            # run_sentiment_cached hits/writes exactly the cache_key_for key, so polling naturally reads it after the run
            run_sentiment_cached(user_id, brand, brand_terms=terms,
                                 industry_terms=industry)
            bill_one(user_id, brand)
        cache_service.delete_cached_report(_flag_key(user_id, brand, terms, industry))
    except Exception as e:  # noqa: BLE001 — nobody catches background thread exceptions; must persist a "failed" state ourselves
        log.exception("[sentiment_once] quick check failed brand=%s", brand)
        _mark_failed(user_id, brand, terms, f"{type(e).__name__}: {e}", industry)


def start(user_id: int, brand: str, terms: Optional[List[str]] = None,
          industry: Optional[List[str]] = None) -> Dict[str, Any]:
    """Start one one-off quick check. industry is the category word for **brand disambiguation**; strongly recommended.

    Returned state:
      ready   -- already run within 24h, result usable directly (no repeated calls burned)
      started -- moved to background; fetch later with status()
      running -- someone just started the same run; reuse it
      degraded-- cache unavailable; caller should use the synchronous fallback (run_sync)
    """
    terms = _norm_terms(brand, terms)
    industry = _norm_industry(industry)
    st = status(user_id, brand, terms, industry)
    if st["state"] == "ready":
        return st
    if st["state"] == "running":
        return st

    # Write the in-progress marker: failure to write = Redis unavailable = the result can't be stored either; never pretend to queue then
    ok = cache_service.set_cached_report(
        _flag_key(user_id, brand, terms, industry), {"started_at": time.time()},
        ttl_s=_RUNNING_TTL_S,
    )
    if not ok:
        return {"state": "degraded"}

    threading.Thread(target=_worker, args=(user_id, brand, terms, industry),
                     name=f"sentiment-once-{brand[:20]}", daemon=True).start()
    return {"state": "started"}


def run_sync(user_id: int, brand: str, terms: Optional[List[str]] = None,
             industry: Optional[List[str]] = None) -> Dict[str, Any]:
    """Synchronous fallback when the cache is unavailable: run on the spot; if it exceeds SYNC_CAP_S, say honestly it didn't finish."""
    from geo.services.full_diagnosis_sentiment import run_sentiment_probe

    terms = _norm_terms(brand, terms)
    industry = _norm_industry(industry)
    box: Dict[str, Any] = {}

    def _go() -> None:
        try:
            box["result"] = run_sentiment_probe(user_id, brand, brand_terms=terms,
                                                industry_terms=industry)
        except Exception as e:  # noqa: BLE001
            box["error"] = f"{type(e).__name__}: {e}"

    th = threading.Thread(target=_go, name=f"sentiment-once-sync-{brand[:20]}", daemon=True)
    th.start()
    th.join(timeout=SYNC_CAP_S)
    if "result" in box:
        bill_one(user_id, brand)
        return {"state": "ready", "result": box["result"]}
    if "error" in box:
        return {"state": "failed", "error": box["error"][:200]}
    return {"state": "timeout"}
