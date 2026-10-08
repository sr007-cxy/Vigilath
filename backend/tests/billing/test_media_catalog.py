"""Unified media library -- merge, dedupe, redact, price metric definition.

User-side semantics is "one media library": when both suppliers have the same media, show only one row (the cheaper one),
and the output must not contain provider / resource_id / purchase price.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.rwj_media import RwjMediaResourceORM as R
from geo.services import media_catalog


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[R.__table__])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _add(db, *, provider, rid, title, cents, rate=90.0, res_type="Website",
         platform="", active=True, synced=True):
    db.add(R(
        provider=provider, res_type=res_type, resource_id=rid,
        title=title, title_key=media_catalog._title_key(title),
        platform=platform, channel_name="新闻资讯", region_name="全国",
        include_name="收录", ref_price_cents=cents, publish_rate=rate,
        is_active=active,
        ref_price_synced_at=datetime.now(timezone.utc) if synced else None,
    ))
    db.commit()


def test_same_media_from_two_sources_shows_once_and_cheapest(db):
    _add(db, provider="rwj", rid=1, title="搜狐网·科技频道", cents=5000)
    _add(db, provider="mjk", rid=999, title="搜狐网·科技频道", cents=3500)
    out = media_catalog.search(db)
    assert out["total"] == 1
    assert len(out["rows"]) == 1
    # 3500 × 1.2 = 4200
    assert out["rows"][0]["price_cents"] == 4200


def test_no_vendor_fields_for_normal_users(db):
    _add(db, provider="mjk", rid=7, title="网易新闻", cents=3500)
    row = media_catalog.search(db)["rows"][0]
    for k in ("provider", "resource_id", "ref_price_cents",
              "_provider", "_resource_id", "_upstream_cents"):
        assert k not in row, f"{k} 泄露给用户了"
    assert row["media_id"] > 0


def test_staff_gets_the_vendor_fields(db):
    _add(db, provider="mjk", rid=7, title="网易新闻", cents=3500)
    row = media_catalog.search(db, staff=True)["rows"][0]
    assert row["_provider"] == "mjk"
    assert row["_resource_id"] == 7
    assert row["_upstream_cents"] == 3500


def test_price_is_retail_never_cost(db):
    _add(db, provider="rwj", rid=1, title="小站", cents=100)   # 1 CNY -> triggers the ¥4 floor
    row = media_catalog.search(db)["rows"][0]
    assert row["price_cents"] == 400
    assert row["price"] == "4.00"


def test_delisted_rows_are_invisible_and_unresolvable(db):
    _add(db, provider="rwj", rid=1, title="已下架的站", cents=3500, active=False)
    assert media_catalog.search(db)["total"] == 0
    assert media_catalog.available(db) is False
    row_id = db.query(R).first().id
    assert media_catalog.resolve(db, [row_id]) == []


def test_resolve_is_the_only_source_of_provider(db):
    _add(db, provider="mjk", rid=42, title="凤凰网", cents=8000)
    mid = media_catalog.search(db)["rows"][0]["media_id"]
    got = media_catalog.resolve(db, [mid])
    assert got == [{
        "media_id": mid, "provider": "mjk", "res_type": "Website",
        "resource_id": 42, "title": "凤凰网",
        "ref_price_cents": 8000, "retail_price_cents": 9600,
    }]


def test_price_filter_is_expressed_in_retail_cents(db):
    _add(db, provider="rwj", rid=1, title="便宜站", cents=1000)     # retail 1200
    _add(db, provider="rwj", rid=2, title="贵站", cents=10000)      # retail 12000
    out = media_catalog.search(db, price_max_cents=5000)
    titles = [r["title"] for r in out["rows"]]
    assert titles == ["便宜站"]


def test_pagination_is_stable_across_pages(db):
    for i in range(1, 26):
        _add(db, provider="rwj", rid=i, title=f"站点{i:02d}", cents=1000 + i * 10)
    p1 = media_catalog.search(db, page=1, page_size=10)
    p2 = media_catalog.search(db, page=2, page_size=10)
    assert p1["total"] == 25
    ids1 = {r["media_id"] for r in p1["rows"]}
    ids2 = {r["media_id"] for r in p2["rows"]}
    assert len(ids1) == 10 and len(ids2) == 10
    assert not (ids1 & ids2), "翻页出现重复行"


def test_stale_price_is_flagged_not_hidden(db):
    _add(db, provider="rwj", rid=1, title="很久没同步的站", cents=3500, synced=False)
    row = media_catalog.search(db)["rows"][0]
    assert row["price_stale"] is True     # greyed out, not hidden -- the price is verified live at order time
