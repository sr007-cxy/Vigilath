"""Automatic media selection: pick a set within budget, not just one.

A placement's effect depends on coverage -- if one outlet publishes and another doesn't, the whole article is wasted. So it went from
"pick 1" to "pick 1-3 within budget", with **at most 1 per platform**: three links from the same source
are worth far less to the AI citation pool than three different sites, and pouring everything into one account carries high indexing risk on the platform side.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.balance import BalanceLedgerORM, BalancePriceORM, UserBalanceORM
from geo.models.user import UserORM
from geo.services import balance_service, media_select


def _cand(rid, title, price, rate=90.0, platform=None):
    return {"resource_id": rid, "title": title, "price": f"{price:.2f}",
            "publish_rate": rate, "platform": platform or title}


def test_picks_multiple_within_budget():
    """Pick more outlets when the budget allows -- this is the very purpose of this change."""
    got = media_select._pick_set(
        [_cand(1, "A站", 30), _cand(2, "B站", 40), _cand(3, "C站", 50)],
        budget_cents=12000)          # total ¥120, fits exactly
    assert len(got) == 3


def test_stops_at_budget():
    got = media_select._pick_set(
        [_cand(1, "A站", 30), _cand(2, "B站", 40), _cand(3, "C站", 50)],
        budget_cents=7500)
    assert len(got) == 2
    assert sum(float(x["price"]) for x in got) <= 75


def test_never_two_from_the_same_platform():
    """Pouring everything into Sohu accounts carries high indexing risk on the platform side."""
    got = media_select._pick_set(
        [_cand(1, "搜狐号·科技", 10, platform="搜狐号"),
         _cand(2, "搜狐号·财经", 10, platform="搜狐号"),
         _cand(3, "网易号·数码", 12, platform="网易号")],
        budget_cents=100000)
    assert len(got) == 2
    assert {x["platform"] for x in got} == {"搜狐号", "网易号"}


def test_respects_max_count():
    cands = [_cand(i, f"站{i}", 1, platform=f"P{i}") for i in range(10)]
    assert len(media_select._pick_set(cands, 100000)) <= media_select.MAX_PAID


def test_zero_budget_picks_nothing():
    assert media_select._pick_set([_cand(1, "A站", 30)], 0) == []


def test_ranks_by_value_for_money():
    """Value = publish rate / price. Expensive ones with only slightly higher publish rates shouldn't crowd out cheap, stable ones."""
    got = media_select._pick_set(
        [_cand(1, "贵站", 200, rate=99, platform="X"),
         _cand(2, "便宜站", 10, rate=95, platform="Y")],
        budget_cents=2000)          # only enough for one
    assert got[0]["title"] == "便宜站"


# ── Take the min of budget and wallet balance ────────────────────────────────────────────
@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__,
        BalanceLedgerORM.__table__, BalancePriceORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _topic(db, uid=1, role="user", balance=0):
    db.add(UserORM(id=uid, email=f"u{uid}@x.z", password_hash="x", role=role))
    db.commit()
    balance_service.get_or_create_account(db, uid)
    grant = balance_service.get_balance(db, uid)
    if grant:
        balance_service.charge(db, user_id=uid, kind="batch", amount_cents=grant,
                               idem_key=f"drain{uid}")
    if balance:
        balance_service.credit(db, user_id=uid, kind="recharge", amount_cents=balance,
                               idem_key=f"topup{uid}")
    db.commit()
    return SimpleNamespace(id=9, user_id=uid)


def test_budget_is_capped_by_wallet(db, monkeypatch):
    """Picking a set the user can't afford is pointless -- tasks get created but can't be published, and someone has to clean up by hand."""
    monkeypatch.setenv("RWJ_AUTO_BUDGET_CENTS", "100000")
    t = _topic(db, balance=3000)
    assert media_select._budget_for(db, t) == 3000


def test_staff_topics_are_not_capped_by_wallet(db, monkeypatch):
    monkeypatch.setenv("RWJ_AUTO_BUDGET_CENTS", "100000")
    t = _topic(db, uid=2, role="admin", balance=0)
    assert media_select._budget_for(db, t) == 100000
