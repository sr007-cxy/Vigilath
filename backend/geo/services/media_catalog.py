"""Unified media library -- the single list users see.

**Why not query both providers live and merge in memory**: each computes its own `total`/`last_page`, so cross-source
pagination cannot give a stable offset; `sort_expr` depends on a membership level suffix (`price_{lv}`), and different levels on the two sides
mean different sort bases; and the dimension numbers in `filter_fields` mean different things (`field_2` is "门户网站" ("portal sites") on Ruanwenjiang,
"综合门户" ("general portals") on Meijieku). Merged pagination would miss rows and duplicate rows.

So we use the mirror table `rwj_media_resources`: its unique key includes provider, both sites are fully synced, and it already normalizes
`platform / channel_name / region_name / include_name / publish_rate / ref_price_cents`
six columns, all indexed. `source_plan.pick_rwj_resources` has long been doing "rank both together, use whichever is cheaper";
this is the generalization of the same path.

Three hard constraints:

1. **Outputs never include provider / resource_id**, only `media_id` (the mirror table's surrogate primary key, unrelated to the
   supplier). At most a user can swap in another media_id, which is still some real media row; provider is always
   resolved server-side -- mixing up ids is structurally impossible. Staff additionally get `_provider` / `_resource_id`.
2. **All price outputs go through `rwj_pricing.retail_cents`**. `ref_price_cents` in the mirror table is
   the wholesale price, and not a single digit of it may be shown to users.
3. **Reference prices are only for search and display; orders must re-check prices live at the source** (verbatim from the rwj_media module).
   So each row carries `price_stale`, and the frontend grays out stale ones; the real price is whatever `verify_resource` says before ordering.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from geo.models.rwj_media import RwjMediaResourceORM as R
from geo.services import rwj_pricing

# Media types selectable by users. Matches the five tabs of the media selection page.
BROWSABLE_TYPES = ("Website", "Wemedia", "Shortvideo", "Question", "Ghostwrite")

# Sort allowlist -> mirror table columns. No longer goes through rwj_client.sort_expr (it is tied to the membership level suffix).
_SORTS = {
    "price_asc": (R.ref_price_cents.asc(), R.publish_rate.desc()),
    "price_desc": (R.ref_price_cents.desc(), R.publish_rate.desc()),
    "rate_desc": (R.publish_rate.desc(), R.ref_price_cents.asc()),
    "weigh_desc": (R.pc_weigh.desc(), R.ref_price_cents.asc()),
}
DEFAULT_SORT = "price_asc"

_NORM_RE = re.compile(r"[\s\-_·・|/\\()（）\[\]【】]+")


def stale_hours() -> int:
    try:
        return max(1, int(os.environ.get("RWJ_REF_PRICE_STALE_HOURS") or 36))
    except ValueError:
        return 36


def _title_key(title: str) -> str:
    return _NORM_RE.sub("", (title or "").strip().lower())


def _row_out(r: R, *, staff: bool, now_ts: Optional[float]) -> Optional[dict[str, Any]]:
    retail = rwj_pricing.retail_cents(r.ref_price_cents)
    if retail is None:
        return None                 # Rows without a retail price are not shown; never fall back to the wholesale price
    synced = r.ref_price_synced_at
    stale = True
    if synced is not None and now_ts is not None:
        stale = (now_ts - synced.timestamp()) > stale_hours() * 3600
    out: dict[str, Any] = {
        "media_id": r.id,
        "title": r.title,
        "res_type": r.res_type,
        "platform": r.platform or "",
        "channel": r.channel_name or "",
        "region": r.region_name or "",
        "include": r.include_name or "",
        "price_cents": retail,
        "price": f"{retail / 100:.2f}",
        "price_stale": stale,
        "publish_rate": r.publish_rate,
        "publish_time": r.publish_time or "",
        "pc_weigh": r.pc_weigh,
        "wap_weigh": r.wap_weigh,
        "remarks": r.remarks or "",
        "case_link": r.case_link or "",
    }
    if staff:
        out["_provider"] = r.provider or "rwj"
        out["_resource_id"] = r.resource_id
        out["_upstream_cents"] = r.ref_price_cents
    return out


def _base_query(db: Session, res_type: str):
    return db.query(R).filter(
        R.res_type == res_type,
        R.is_active.is_(True),
        R.ref_price_cents.isnot(None),
    )


def available(db: Session) -> bool:
    """Whether the mirror table has any placeable rows. On an empty table the endpoint must honestly say "temporarily unavailable",
    **never falling back to the live API** -- falling back would re-expose the provider differences."""
    return db.query(R.id).filter(R.is_active.is_(True)).first() is not None


def facets(db: Session, res_type: str) -> dict[str, list[str]]:
    """Filter options. Only exposes the four dimensions already normalized in the mirror table; the two providers' field_N differences stay out of the user-facing API."""
    if res_type not in BROWSABLE_TYPES:
        return {}
    out: dict[str, list[str]] = {}
    for key, col in (("platform", R.platform), ("channel", R.channel_name),
                     ("region", R.region_name), ("include", R.include_name)):
        vals = [
            v for (v,) in _base_query(db, res_type).with_entities(col).distinct().all()
            if (v or "").strip()
        ]
        out[key] = sorted(vals)[:200]
    return out


def search(
    db: Session,
    *,
    res_type: str = "Website",
    platform: str = "",
    channel: str = "",
    region: str = "",
    include: str = "",
    keyword: str = "",
    price_min_cents: Optional[int] = None,
    price_max_cents: Optional[int] = None,
    sort: str = DEFAULT_SORT,
    page: int = 1,
    page_size: int = 20,
    staff: bool = False,
) -> dict[str, Any]:
    """One SQL produces globally ordered results; there is no cross-source merging.

    When same-named media exist on both sites only one row is output (the cheaper one) -- this is the core of the "merge into one media library"
    semantics: users see media, not suppliers.
    """
    import time

    if res_type not in BROWSABLE_TYPES:
        res_type = "Website"
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 50))

    q = _base_query(db, res_type)
    if platform:
        q = q.filter(R.platform == platform)
    if channel:
        q = q.filter(R.channel_name == channel)
    if region:
        q = q.filter(R.region_name == region)
    if include:
        q = q.filter(R.include_name == include)
    if keyword.strip():
        q = q.filter(R.title_key.like(f"%{_title_key(keyword)}%"))
    # Price ranges apply to the **retail price**, not the wholesale price -- the user enters the numbers they see
    if price_min_cents is not None:
        q = q.filter(R.ref_price_cents >= _upstream_floor(price_min_cents))
    if price_max_cents is not None:
        q = q.filter(R.ref_price_cents <= _upstream_ceiling(price_max_cents))

    # Same-name dedup: keep only the row with the smallest ref_price_cents per title_key
    sub = q.with_entities(
        R.title_key.label("tk"),
        func.min(R.ref_price_cents).label("min_cents"),
    ).group_by(R.title_key).subquery()

    dedup = q.join(
        sub,
        (R.title_key == sub.c.tk) & (R.ref_price_cents == sub.c.min_cents),
    )

    total = dedup.with_entities(func.count(func.distinct(R.title_key))).scalar() or 0

    order = _SORTS.get(sort, _SORTS[DEFAULT_SORT])
    # When both providers have the same name at the same price, id is the tie-breaker to keep pagination stable (otherwise offset drifts)
    rows = (
        dedup.order_by(*order, R.id.asc())
             .offset((page - 1) * page_size)
             .limit(page_size)
             .all()
    )

    now_ts = time.time()
    seen: set[str] = set()
    out_rows = []
    for r in rows:
        if r.title_key in seen:
            continue                 # With equal prices the join brings out two rows; take the first
        seen.add(r.title_key)
        row = _row_out(r, staff=staff, now_ts=now_ts)
        if row is not None:
            out_rows.append(row)

    return {
        "rows": out_rows,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "res_type": res_type,
        "sort": sort if sort in _SORTS else DEFAULT_SORT,
        "available": True,
        "stale_hours": stale_hours(),
    }


def _upstream_floor(retail_cents: int) -> int:
    """Retail price lower bound -> wholesale price lower bound. Markup is monotonic, so divide by the markup rate (floor, better to include a few extra rows)."""
    return max(0, int(retail_cents / max(rwj_pricing.markup(), 0.01)) - 1)


def _upstream_ceiling(retail_cents: int) -> int:
    return int(retail_cents / max(rwj_pricing.markup(), 0.01)) + 1


def resolve(db: Session, media_ids: list[int]) -> list[dict[str, Any]]:
    """media_id -> the four items needed to order. **provider can only come from here, never from the request body.**

    Rows not found / delisted are honestly removed and reported by the caller, never silently replaced with other media.
    """
    ids = [int(i) for i in media_ids if str(i).strip()]
    if not ids:
        return []
    rows = db.query(R).filter(R.id.in_(ids), R.is_active.is_(True)).all()
    by_id = {r.id: r for r in rows}
    out = []
    for i in ids:
        r = by_id.get(i)
        if r is None:
            continue
        out.append({
            "media_id": r.id,
            "provider": r.provider or "rwj",
            "res_type": r.res_type,
            "resource_id": r.resource_id,
            "title": r.title,
            "ref_price_cents": r.ref_price_cents,
            "retail_price_cents": rwj_pricing.retail_cents(r.ref_price_cents),
        })
    return out
