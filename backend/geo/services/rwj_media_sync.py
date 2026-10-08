"""Daily sync of the paid publishing media resource library -- full paging + upsert + delisting detection.

Covers two sites (`provider`: rwj=Ruanwenjiang, mjk=Meijieku), stored in the same table distinguished by the provider column.
The two resource pools share a source but quotes are computed per each site's membership level; price comparison needs both mirrored; sites without
configured credentials are skipped automatically. Sync scope is RWJ_SYNC_PROVIDERS.


Why persist to DB: the publishing plan picks placement platforms from measured cited sources (services/source_plan.py),
and we have no own accounts on Zhihu / Sohu / Baijiahao / Toutiao, so we can only rely on Ruanwenjiang paid publishing.
The Ruanwenjiang pool has about 65k entries, the list endpoint caps pageSize at 50 and **doesn't support direct lookup by resource_id**;
without a local mirror we can't answer "can we place on this platform, how many accounts, roughly how much".

Type priority (order of RWJ_SYNC_TYPES): Website / Wemedia are the two that can carry GEO articles and the
only two that can be ordered (ruanwenjiang_publisher.SUPPORTED_TYPES), so they go first -- even if later ones get
blocked by risk control, the two most important types are already fetched.

**Price metric definition**: all prices written by this module are reference prices (columns ref_*), only for scheduling filters /
candidate pool ranking / cost estimates. The Ruanwenjiang API doc explicitly requires "re-fetch list prices before submitting; don't use
long-term cached prices"; the ordering path (ruanwenjiang_publisher.verify_resource -> publish) must
keep verifying prices live at the source. **This module must not be referenced by ruanwenjiang_publisher.**

How to run (prod has no APScheduler leader, uses crontab, same pattern as compliance_monitor):
    python -m geo.services.rwj_media_sync                     # full, per configured site x type
    python -m geo.services.rwj_media_sync --types Website     # sync only some types
    python -m geo.services.rwj_media_sync --providers mjk     # sync only one site
    python -m geo.services.rwj_media_sync --max-pages 3 --dry-run   # trial run without writing to DB
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

import httpx
from sqlalchemy.orm import Session

from geo.database import SessionLocal, settings
from geo.models.rwj_media import RwjMediaResourceORM, RwjSyncRunORM
from geo.services.rwj_client import (
    DEFAULT_PROVIDER, RwjApiError, RwjAuthError, RwjClient, credentials,
    delivery_options, pick_delivery, site,
)

log = logging.getLogger(__name__)

MAX_RETRY = 3                 # per-page retries, backoff 2/6/15s
RETRY_BACKOFF = (2, 6, 15)
BAN_COOLDOWN = 120            # connection timeouts treated as a temporary WAF ban; retry once after cooldown
COVERAGE_MIN = 0.90           # coverage this round below this -> partial, skip delisting detection

# "Indexed / verified" fields taken per type, same source as api/rwj_channel.py::_INCLUDE_FIELD
INCLUDE_FIELD = {
    "Website": "field_5_name",
    "Wemedia": "field_6_name",
    "Shortvideo": "field_7_name",
    "Question": "field_4_name",
    "Ghostwrite": "field_4_name",
}


@dataclass
class SyncStat:
    res_type: str
    provider: str = DEFAULT_PROVIDER
    status: str = "running"
    total_reported: int = 0
    pages_fetched: int = 0
    rows_seen: int = 0
    unique_seen: int = 0          # deduplicated resource count; coverage is computed from it
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    price_changed: int = 0
    delisted: int = 0
    coverage_pct: float = 0.0
    error: str = ""
    samples: list = field(default_factory=list)   # with --dry-run holds the first few normalize results


# ────────────────────────── Pure functions (no DB / no network) ──────────────────────────


def _to_cents(v: Any) -> Optional[int]:
    """Price string -> cents. Uses Decimal so binary floats don't turn 8.10 into 8.099999."""
    if v is None or v == "":
        return None
    try:
        return int((Decimal(str(v)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_rate(v: Any) -> Optional[float]:
    """Publish rate: '98%' / '98' / 98 / '' -> 98.0 / 98.0 / 98.0 / None"""
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip().rstrip("%"))
    except (ValueError, TypeError):
        return None


def _title_key(title: str) -> str:
    return "".join(str(title or "").split()).lower()[:255]


def normalize_row(res_type: str, row: dict, *, level: int, now: datetime,
                  provider: str = DEFAULT_PROVIDER) -> dict:
    """Raw platform row -> table field dict. All tags (field_*) go as-is into two json columns; no information lost.

    Price: all three direct-publish tiers price_1/2/3 are stored (no full-table refresh when membership level changes); ref_price_cents
    is the reference price at the current level with the default placement method -- that's what scheduling and cost estimates use.
    Question type has no price_{level} column (it's price_{answers}_{level}); left empty if unavailable,
    and that type doesn't take part in publishing scheduling anyway.
    """
    fid: dict[str, Any] = {}
    fname: dict[str, str] = {}
    prices: dict[str, Any] = {}
    for k, v in row.items():
        if k.endswith("_name") and k.startswith("field_"):
            fname[k] = str(v or "")
        elif k.startswith("field_"):
            fid[k] = v
        elif k.startswith("price_") and k != "price_type":
            prices[k] = v

    # Reference price at the current level: taken by the default placement method's prefix (direct publish is usually price_{level})
    ref_cents: Optional[int] = None
    try:
        prefix = (pick_delivery(row) or {}).get("prefix") or "price_"
    except RwjApiError:
        prefix = "price_"
    for key in (f"{prefix}{level}", f"price_{level}", "price_1"):
        if key in row:
            ref_cents = _to_cents(row.get(key))
            if ref_cents is not None:
                break

    channel_name = str(row.get("field_1_name") or "")
    # Channel name -> our platform name (搜狐 -> 搜狐号, "Sohu" -> "Sohu account"). Unrecognized stays empty, filed under the 「软文匠」 ("Ruanwenjiang") portal bucket.
    from geo.services.source_plan import normalize_platform_name
    platform = normalize_platform_name(channel_name) or ""

    return {
        "provider": provider,
        "res_type": res_type,
        "resource_id": int(row.get("resource_id") or 0),
        "title": str(row.get("title") or "")[:255],
        "title_key": _title_key(row.get("title")),
        "remarks": str(row.get("remarks") or "") or None,
        "platform": platform,
        "channel_name": channel_name[:64],
        "region_name": str(row.get("field_3_name") or "")[:64],
        "include_name": str(row.get(INCLUDE_FIELD.get(res_type, "")) or "")[:64],
        "ref_price_cents": ref_cents,
        "ref_price_level": level,
        "ref_price_l1_cents": _to_cents(row.get("price_1")),
        "ref_price_l2_cents": _to_cents(row.get("price_2")),
        "ref_price_l3_cents": _to_cents(row.get("price_3")),
        "ref_price_json": json.dumps(prices, ensure_ascii=False),
        "delivery_json": json.dumps(delivery_options(row), ensure_ascii=False),
        "ref_price_synced_at": now,
        "publish_rate": _parse_rate(row.get("publish_rate")),
        "publish_time": str(row.get("publish_time") or "")[:64],
        "pc_weigh": _int_or_none(row.get("pc_weigh")),
        "wap_weigh": _int_or_none(row.get("wap_weigh")),
        "case_link": str(row.get("case_link") or "")[:512],
        "entrance_link": str(row.get("entrance_link") or "")[:512],
        "field_ids_json": json.dumps(fid, ensure_ascii=False),
        "field_names_json": json.dumps(fname, ensure_ascii=False),
        "raw_json": json.dumps(row, ensure_ascii=False),
    }


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ────────────────────────────── Network ──────────────────────────────


def sync_client(provider: str = DEFAULT_PROVIDER) -> RwjClient:
    """Client with its own token file -- doesn't use the get_client() singleton, to avoid kicking out the web process's login session."""
    from pathlib import Path

    spec = site(provider)
    mobile, password, api_base, _ = credentials(spec.provider)
    token_path = getattr(settings, f"{spec.provider.upper()}_SYNC_TOKEN_PATH", "") \
        or f"data/{spec.provider}_tokens_sync.json"
    return RwjClient(mobile=mobile, password=password, base_url=api_base,
                     token_path=Path(token_path), spec=spec)


def fetch_page(c: RwjClient, res_type: str, page: int, *, page_size: int) -> dict:
    """Fetch one page with backoff retries. Must go through list_resources -> default_list_body (full set of empty filter
    fields); with missing fields the server's total is wrong, and total is the baseline for coverage and the delisting safety valve."""
    last: Optional[Exception] = None
    for attempt in range(MAX_RETRY):
        try:
            return c.list_resources(res_type, pageNo=page, pageSize=page_size)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            # TCP-level errors are treated as a temporary WAF ban: cool down instead of retrying immediately; don't dig ourselves deeper
            last = e
            log.warning("[rwj-sync] %s p%d connection error, cooling down %ds: %s", res_type, page, BAN_COOLDOWN, e)
            time.sleep(BAN_COOLDOWN)
        except RwjAuthError:
            raise                       # request() already renewed and retried internally; raising again means the account state really is broken
        except RwjApiError as e:
            last = e
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            log.warning("[rwj-sync] %s p%d failed, retrying in %ds: %s", res_type, page, wait, e)
            time.sleep(wait)
    raise RwjApiError(f"{res_type} page {page} failed after {MAX_RETRY} retries: {last}")


# ────────────────────────────── DB ──────────────────────────────

# Fields included in the diff: full UPDATE only when changed; otherwise just refresh last_seen (light update)
_DIFF_FIELDS = (
    "title", "title_key", "remarks", "platform", "channel_name", "region_name",
    "include_name", "ref_price_cents", "ref_price_l1_cents", "ref_price_l2_cents",
    "ref_price_l3_cents", "ref_price_json", "delivery_json", "publish_rate",
    "publish_time", "pc_weigh", "wap_weigh", "case_link", "entrance_link",
    "field_ids_json", "field_names_json", "raw_json",
)
_PRICE_FIELDS = ("ref_price_cents", "ref_price_l1_cents", "ref_price_l2_cents",
                 "ref_price_l3_cents")


def upsert_page(db: Session, res_type: str, payloads: list[dict], *,
                batch_id: str, now: datetime,
                provider: str = DEFAULT_PROVIDER) -> dict:
    """Persist one page (<= 50 rows) to DB. Dialect-neutral (same code for dev SQLite / prod PG); doesn't use PG-specific
    ON CONFLICT -- diff overhead at 50 rows is negligible, in exchange for exact counts and price_changed semantics."""
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "price_changed": 0}
    if not payloads:
        return counts
    rids = [p["resource_id"] for p in payloads]
    existing = {
        r.resource_id: r
        for r in db.query(RwjMediaResourceORM)
                   .filter(RwjMediaResourceORM.provider == provider,
                           RwjMediaResourceORM.res_type == res_type,
                           RwjMediaResourceORM.resource_id.in_(rids))
                   .all()
    }
    for p in payloads:
        row = existing.get(p["resource_id"])
        if row is None:
            row = RwjMediaResourceORM(**p)
            row.first_seen_at = now
            row.last_seen_at = now
            row.last_seen_batch = batch_id
            row.is_active = True
            row.miss_count = 0
            db.add(row)
            counts["inserted"] += 1
            continue

        changed = [f for f in _DIFF_FIELDS if getattr(row, f) != p[f]]
        row.last_seen_at = now
        row.last_seen_batch = batch_id
        row.miss_count = 0
        if not row.is_active:           # reappeared after delisting -> revive
            row.is_active = True
            row.delisted_at = None
        if changed:
            for f in changed:
                setattr(row, f, p[f])
            row.ref_price_level = p["ref_price_level"]
            row.ref_price_synced_at = now
            if any(f in _PRICE_FIELDS for f in changed):
                row.ref_price_changed_at = now
                counts["price_changed"] += 1
            counts["updated"] += 1
        else:
            row.ref_price_synced_at = now
            counts["unchanged"] += 1
    return counts


def mark_delisted(db: Session, res_type: str, *, batch_id: str, now: datetime,
                  provider: str = DEFAULT_PROVIDER) -> int:
    """Delisting detection after a fully covered round -- two-phase, to avoid false kills from paging drift.

    If the platform lists a new entry while we're on page 400, all later rows shift back and some rows are missed; delisting
    directly on "not seen this round" would falsely kill them. So miss_count accumulates first; delist only after N consecutive misses.
    The caller guarantees this function is only called on full rounds that meet the coverage threshold.
    """
    q = (db.query(RwjMediaResourceORM)
           .filter(RwjMediaResourceORM.provider == provider,
                   RwjMediaResourceORM.res_type == res_type,
                   RwjMediaResourceORM.is_active.is_(True),
                   RwjMediaResourceORM.last_seen_batch != batch_id))
    missed = q.all()
    threshold = int(settings.RWJ_SYNC_MISS_THRESHOLD)
    n = 0
    for row in missed:
        row.miss_count = int(row.miss_count or 0) + 1
        if row.miss_count >= threshold:
            row.is_active = False
            row.delisted_at = now
            n += 1
    db.commit()
    return n


# ────────────────────────────── Orchestration ──────────────────────────────


def sync_type(db: Session, c: RwjClient, res_type: str, *, batch_id: str,
              page_size: int, level: int, max_pages: Optional[int] = None,
              dry_run: bool = False) -> SyncStat:
    """Full sync for one site and one type. Commit per page -- memory O(one page); an interruption doesn't lose what's already persisted.

    The site is determined by the passed client (c.provider); don't read it from global config again.
    """
    provider = c.provider
    stat = SyncStat(res_type=res_type, provider=provider)
    started = datetime.utcnow()
    # Coverage must be computed from the **deduplicated** resource count: platform sorting is unstable, and on deep pages the same entry
    # appears on multiple pages (measured: Website fetched 13019 rows but only 10234 unique resources). Counting rows
    # inflates it to 100%, which then triggers delisting detection next round, wrongly delisting 2785 entries that were never missed.
    seen_ids: set[int] = set()
    run: Optional[RwjSyncRunORM] = None
    if not dry_run:
        run = RwjSyncRunORM(batch_id=batch_id, provider=provider, res_type=res_type,
                            status="running", account_level=level, started_at=started)
        db.add(run)
        db.commit()

    sleep_base = float(settings.RWJ_SYNC_PAGE_SLEEP)
    try:
        page = 1
        last_page = 1
        while page <= last_page:
            data = fetch_page(c, res_type, page, page_size=page_size)
            rows = data.get("data") or []
            stat.total_reported = int(data.get("total") or stat.total_reported)
            last_page = int(data.get("last_page") or 1)
            if max_pages:
                last_page = min(last_page, max_pages)
            stat.pages_fetched += 1
            stat.rows_seen += len(rows)

            payloads = [normalize_row(res_type, r, level=level, now=datetime.utcnow(),
                                      provider=provider)
                        for r in rows if r.get("resource_id")]
            seen_ids.update(p["resource_id"] for p in payloads)
            if dry_run:
                if len(stat.samples) < 3:
                    stat.samples.extend(payloads[:3 - len(stat.samples)])
            else:
                counts = upsert_page(db, res_type, payloads, provider=provider,
                                     batch_id=batch_id, now=datetime.utcnow())
                db.commit()
                for k, v in counts.items():
                    setattr(stat, k, getattr(stat, k) + v)

            if page % 20 == 0 or page == last_page:
                log.info("[rwj-sync] %s/%s page %d/%d, %d rows so far (%d new, %d changed)",
                         provider, res_type, page, last_page, stat.rows_seen,
                         stat.inserted, stat.updated)
            page += 1
            if page <= last_page:
                time.sleep(sleep_base * random.uniform(0.7, 1.3))

        stat.unique_seen = len(seen_ids)
        stat.coverage_pct = (stat.unique_seen / stat.total_reported * 100
                             if stat.total_reported else 100.0)
        full = (not max_pages) and stat.coverage_pct >= COVERAGE_MIN * 100
        stat.status = "success" if full else "partial"
        if full and not dry_run:
            stat.delisted = mark_delisted(db, res_type, provider=provider,
                                          batch_id=batch_id, now=datetime.utcnow())
    except Exception as e:  # noqa: BLE001
        stat.status = "failed"
        stat.error = str(e)[:900]
        log.exception("[rwj-sync] %s/%s sync failed", provider, res_type)

    if run is not None:
        run.status = stat.status
        run.finished_at = datetime.utcnow()
        run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
        run.total_reported = stat.total_reported
        run.pages_fetched = stat.pages_fetched
        run.rows_seen = stat.rows_seen
        run.inserted = stat.inserted
        run.updated = stat.updated
        run.unchanged = stat.unchanged
        run.price_changed = stat.price_changed
        run.delisted = stat.delisted
        run.coverage_pct = stat.coverage_pct
        run.error = stat.error or None
        db.commit()
    return stat


def _resolve_providers(providers: Optional[Sequence[str]] = None) -> list[str]:
    """Which sites to sync: explicit > RWJ_SYNC_PROVIDERS; sites without credentials are dropped."""
    raw = list(providers) if providers else [
        p.strip() for p in (settings.RWJ_SYNC_PROVIDERS or "").split(",") if p.strip()]
    out: list[str] = []
    for p in raw or [DEFAULT_PROVIDER]:
        try:
            spec = site(p)
        except RwjApiError:
            log.warning("[rwj-sync] unknown site %s, skipping", p)
            continue
        mobile, password, _, _ = credentials(spec.provider)
        if not (mobile and password):
            log.info("[rwj-sync] %s credentials not configured, skipping", spec.label)
            continue
        if spec.provider not in out:
            out.append(spec.provider)
    return out


def sync_all(types: Optional[Sequence[str]] = None, *,
             providers: Optional[Sequence[str]] = None,
             page_size: Optional[int] = None, max_pages: Optional[int] = None,
             dry_run: bool = False) -> dict[str, SyncStat]:
    """Sync site by site x type by type in configured order. A single type/site failure doesn't block the rest.

    Returned keys are `{provider}:{res_type}` -- stats for the same type on both sites mustn't overwrite each other.
    """
    order = list(types) if types else [
        t.strip() for t in (settings.RWJ_SYNC_TYPES or "").split(",") if t.strip()]
    ps = int(page_size or settings.RWJ_SYNC_PAGE_SIZE)
    batch_id = uuid.uuid4().hex[:32]

    out: dict[str, SyncStat] = {}
    db = SessionLocal()
    try:
        for provider in _resolve_providers(providers):
            spec = site(provider)
            c = sync_client(provider)
            try:
                info = c.ensure_session()
            except (RwjApiError, httpx.RequestError) as e:
                # One site failing to log in shouldn't take down the other -- record a failure stat and continue
                log.exception("[rwj-sync] %s login failed, skipping this site", spec.label)
                for res_type in order:
                    out[f"{provider}:{res_type}"] = SyncStat(
                        res_type=res_type, provider=provider,
                        status="failed", error=str(e)[:900])
                continue
            level = int(info.get("level") or 1)
            log.info("[rwj-sync] batch=%s site=%s level=%d types=%s",
                     batch_id, spec.label, level, order)
            for res_type in order:
                out[f"{provider}:{res_type}"] = sync_type(
                    db, c, res_type, batch_id=batch_id, page_size=ps, level=level,
                    max_pages=max_pages, dry_run=dry_run)
    finally:
        db.close()
    return out


def renormalize_platforms() -> dict[str, int]:
    """Recompute platform for all rows using the current alias table, without re-crawling.

    Use this to backfill after adding a platform alias (e.g. 「知乎号」 ("Zhihu account")) -- a single UPDATE over tens of thousands of rows;
    no need to hit the platform's 990 pages again just to change a mapping table.
    """
    from geo.services.source_plan import normalize_platform_name

    db = SessionLocal()
    changed: dict[str, int] = {}
    try:
        rows = db.query(RwjMediaResourceORM).all()
        for r in rows:
            want = normalize_platform_name(r.channel_name) or ""
            if want != (r.platform or ""):
                r.platform = want
                changed[want or "(无)"] = changed.get(want or "(无)", 0) + 1
        db.commit()
    finally:
        db.close()
    return changed


def tick() -> None:
    """crontab entry point: exceptions are logged, not raised."""
    if not settings.RWJ_SYNC_ENABLED:
        log.info("[rwj-sync] disabled")
        return
    try:
        stats = sync_all()
        for key, st in stats.items():
            log.info("[rwj-sync] %s %s %d rows total (%d new, %d changed, %d price changes, %d delisted), coverage %.1f%%",
                     key, st.status, st.rows_seen, st.inserted, st.updated,
                     st.price_changed, st.delisted, st.coverage_pct)
    except Exception:  # noqa: BLE001
        log.exception("[rwj-sync] tick failed")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="付费发稿媒体资源库同步(软文匠 / 媒介库)")
    ap.add_argument("--types", default="", help="逗号分隔,默认按 RWJ_SYNC_TYPES")
    ap.add_argument("--providers", default="",
                    help="逗号分隔的站点(rwj/mjk),默认按 RWJ_SYNC_PROVIDERS")
    ap.add_argument("--page-size", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=0, help="每类型最多翻几页(试跑用)")
    ap.add_argument("--dry-run", action="store_true", help="只拉取不写库,打印前 3 行")
    ap.add_argument("--renorm", action="store_true",
                    help="只按当前别名表重算已入库行的 platform,不联网")
    args = ap.parse_args()

    if args.renorm:
        for p, n in sorted(renormalize_platforms().items(), key=lambda kv: -kv[1]):
            print(f"  {p:12} {n}")
        return

    types = [t.strip() for t in args.types.split(",") if t.strip()] or None
    provs = [p.strip() for p in args.providers.split(",") if p.strip()] or None
    stats = sync_all(types=types, providers=provs,
                     page_size=args.page_size or None,
                     max_pages=args.max_pages or None,
                     dry_run=args.dry_run)
    for key, st in stats.items():
        print(f"{key:18} {st.status:8} total={st.total_reported:6} rows={st.rows_seen:6} "
              f"唯一={st.unique_seen:6} 新增={st.inserted:5} 改={st.updated:5} "
              f"价变={st.price_changed:4} 下架={st.delisted:4} 覆盖={st.coverage_pct:.1f}%"
              + (f"  ERR={st.error}" if st.error else ""))
        for smp in st.samples:
            print("   sample:", {k: smp[k] for k in
                                 ("provider", "resource_id", "title", "platform",
                                  "channel_name", "ref_price_cents", "publish_rate",
                                  "region_name")})


if __name__ == "__main__":
    main()
