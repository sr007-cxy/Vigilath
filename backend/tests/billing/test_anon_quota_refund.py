"""Quota refund for no-login quick tests -- a round where "not a single engine answered" shouldn't count as used.

Product feedback verbatim: "quota is deducted even on failure; it says 2 per 30 days, but the first call failed and the second says no quota left".
Deducting quota **at start** is correct (cost is incurred as soon as engine calls go out; can't wait for results),
but starting isn't the same as using -- when the batch run center is unreachable, industry words can't form questions, or every engine errors,
the user got nothing. This file pins exactly that criterion.

    venv/bin/python -m pytest tests/billing/test_anon_quota_refund.py -q   # cwd = backend/
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.anon_usage import AnonUsageORM  # noqa: F401  needed to create tables
from geo.services import anon_quota

DEV, IP = "dev_test_1", "203.0.113.7"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[AnonUsageORM.__table__])
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _left(db) -> int:
    st = anon_quota.check(db, "mention", DEV, IP)
    return st["device_limit"] - st["device_used"]


def test_refund_gives_the_slot_back(db):
    before = _left(db)
    anon_quota.consume(db, "mention", DEV, IP, note="某品牌")
    assert _left(db) == before - 1
    assert anon_quota.refund(db, "mention", DEV, IP) is True
    assert _left(db) == before, "退回之后额度必须回到原样"


def test_refund_only_takes_back_one(db):
    anon_quota.consume(db, "mention", DEV, IP)
    anon_quota.consume(db, "mention", DEV, IP)
    used_two = _left(db)
    anon_quota.refund(db, "mention", DEV, IP)
    assert _left(db) == used_two + 1, "一次退一条,不能把这个设备的记录全清掉"


def test_refund_does_not_touch_other_devices(db):
    anon_quota.consume(db, "mention", "dev_other", IP)
    anon_quota.consume(db, "mention", DEV, IP)
    anon_quota.refund(db, "mention", DEV, IP)
    other = anon_quota.check(db, "mention", "dev_other", IP)
    assert other["device_used"] == 1, "别人的额度不能被顺手退掉"


def test_refund_when_nothing_to_refund(db):
    """Repeated refund / quota row already rolled out of the window -- returns False, no error, no quota created from thin air."""
    assert anon_quota.refund(db, "mention", DEV, IP) is False
    before = _left(db)
    anon_quota.consume(db, "mention", DEV, IP)
    assert anon_quota.refund(db, "mention", DEV, IP) is True
    assert anon_quota.refund(db, "mention", DEV, IP) is False
    assert _left(db) == before, "退第二次不能把额度退成负数(即多出一次)"


def test_unlimited_feature_is_a_noop(db):
    """Zero-cost capabilities never enter this table; refunding them is meaningless and shouldn't raise either."""
    assert anon_quota.refund(db, "site_check_not_in_limits", DEV, IP) is False
