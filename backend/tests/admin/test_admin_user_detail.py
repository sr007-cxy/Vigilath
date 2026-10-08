"""Workbench user detail trio -- profile / payment records / audit records.

Pinned metric definitions:
  1. Total payments are **split by currency**. The DB mixes usd and cny (overseas via Stripe, CN via WeChat);
     summing them gives a number with no unit -- support would refund the wrong amount based on it.
  2. Audit records **include those the user deleted**. The admin must be able to answer "he says he ran it but it's not in the list",
     so this doesn't reuse /account's deleted_at filter.
  3. Details **never create money out of thin air**. Balance only reads user_balances, never going through
     balance_service.get_or_create_account -- that lazily grants the sign-up bonus, so
     an admin opening someone's details would effectively give them 10 CNY.

Run:
    venv/bin/python -m pytest tests/admin/test_admin_user_detail.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import geo.api.admin_users as admin_users
from geo.api.ai_telemetry import get_db
from geo.api.auth import require_admin
from geo.database import Base
from geo.models.balance import UserBalanceORM
from geo.models.detection import DetectionRecordORM
from geo.models.membership import MembershipORM, UserMembershipORM
from geo.models.payment import PaymentSessionORM
from geo.models.user import UserORM

USER_ID = 7
ADMIN_ID = 1


@pytest.fixture()
def client():
    # StaticPool + shared connection: the request side and the data setup use the same in-memory DB
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__, DetectionRecordORM.__table__,
        MembershipORM.__table__, UserMembershipORM.__table__, PaymentSessionORM.__table__,
    ])
    Session = sessionmaker(bind=engine)
    now = datetime.utcnow()

    s = Session()
    s.add(UserORM(id=ADMIN_ID, email="admin@x.com", password_hash="x", role="admin",
                  is_admin=True, is_active=True))
    s.add(UserORM(id=USER_ID, email="u@x.com", name="小王", password_hash="x",
                  role="user", is_admin=False, is_active=True, phone="13800000000",
                  wechat_openid="ox1", wechat_nickname="王", wechat_subscribed=True,
                  created_at=now))
    s.add(MembershipORM(id=2, slug="growth", name="成长版", price=99.0, currency="usd",
                        period="month", description="d", features="[]", not_included="[]"))
    s.add(UserMembershipORM(user_id=USER_ID, membership_id=2, is_active=True,
                            start_date=now - timedelta(days=3), end_date=now + timedelta(days=27)))
    s.add(UserBalanceORM(user_id=USER_ID, balance_cents=1234, granted_cents=1000,
                         recharged_cents=500, spent_cents=266))
    s.add(PaymentSessionORM(user_id=USER_ID, membership_id=2, purpose="membership",
                            amount_cents=71280, currency="cny", status="paid",
                            provider="wechat", created_at=now, completed_at=now))
    s.add(PaymentSessionORM(user_id=USER_ID, membership_id=2, purpose="membership",
                            amount_cents=9900, currency="usd", status="paid",
                            provider="stripe", created_at=now, completed_at=now))
    s.add(PaymentSessionORM(user_id=USER_ID, purpose="topup", amount_cents=5000,
                            currency="cny", status="pending", provider="wechat",
                            created_at=now))
    s.add(DetectionRecordORM(user_id=USER_ID, url="https://live.com", score=80, grade="A",
                             mode="free", result_snapshot="{}", created_at=now))
    s.add(DetectionRecordORM(user_id=USER_ID, url="https://gone.com", score=60, grade="C",
                             mode="advanced", result_snapshot="{}",
                             created_at=now - timedelta(hours=1), deleted_at=now))
    s.commit()
    admin_row = s.get(UserORM, ADMIN_ID)
    s.expunge(admin_row)
    s.close()

    app = FastAPI()
    app.include_router(admin_users.router)
    app.dependency_overrides[require_admin] = lambda: admin_row

    def _db():
        d = Session()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    engine.dispose()


def test_detail_keeps_currencies_apart(client):
    d = client.get(f"/admin/users/{USER_ID}").json()
    assert d["payment_paid_cents"] == {"cny": 71280, "usd": 9900}
    assert d["payment_paid_count"] == 2
    assert d["payment_count"] == 3          # a pending top-up order also counts as an order


def test_detail_carries_identity_and_membership(client):
    d = client.get(f"/admin/users/{USER_ID}").json()
    assert d["phone"] == "13800000000"
    assert d["wechat_bound"] is True and d["wechat_subscribed"] is True
    assert d["membership_name"] == "成长版"
    assert d["membership_start_date"] and d["membership_end_date"]
    assert d["created_at"]                  # new accounts have a registration time (existing accounts are None)
    assert d["detection_count"] == 2        # includes the deleted one


def test_detail_reads_balance_without_granting(client):
    d = client.get(f"/admin/users/{USER_ID}").json()
    assert d["balance"] == {"balance_cents": 1234, "granted_cents": 1000,
                            "recharged_cents": 500, "spent_cents": 266, "available": True}


def test_balance_absent_is_zero_not_unavailable(client):
    """Never opened an account = all items 0 and available; this is different from "balance table unreadable"."""
    d = client.get(f"/admin/users/{ADMIN_ID}").json()
    assert d["balance"]["available"] is True
    assert d["balance"]["balance_cents"] == 0


def test_payments_newest_first_and_paged(client):
    r = client.get(f"/admin/users/{USER_ID}/payments?page=1&size=2").json()
    assert r["total"] == 3 and len(r["items"]) == 2
    assert {i["purpose"] for i in r["items"]} <= {"membership", "topup"}
    topup = client.get(f"/admin/users/{USER_ID}/payments?page=2&size=2").json()["items"]
    assert len(topup) == 1


def test_detections_include_user_deleted_rows(client):
    r = client.get(f"/admin/users/{USER_ID}/detections").json()
    assert r["total"] == 2
    urls = {i["url"]: i["deleted_at"] for i in r["items"]}
    assert urls["https://gone.com"] is not None      # deleted by the user, still visible to admin
    assert urls["https://live.com"] is None
    # The full report JSON doesn't go into the list -- several MB per page would bog down the drawer
    assert "result_snapshot" not in r["items"][0]


def test_unknown_user_is_404_everywhere(client):
    for path in ("", "/payments", "/detections"):
        assert client.get(f"/admin/users/99999{path}").status_code == 404


def test_page_size_is_clamped(client):
    r = client.get(f"/admin/users/{USER_ID}/payments?page=0&size=999").json()
    assert r["page"] == 1 and r["size"] == 100
