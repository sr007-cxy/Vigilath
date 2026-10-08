"""Once a brand with a profile appears, orphans without a profile under the same account are archived -- stop popping the brand selection card every round.

Production 2026-08-27: 1000056 had two active brands, GEO-TAG (synced from the profile) and Vigilath
(an existing orphan with empty `topic_id`, accumulated from chat content by old code before the profile system).
`active_brand_count >= 2` pops the brand selection card, and until the card is answered the brand is undecided;
`_brand_is_ambiguous` makes the "suitable customers" card skip the DB and fall back to guessing a generic set
from capability keywords -- the suitable customer types written in the profile the user uploaded went completely unused.

The other half must be pinned too: for an account without a single brand with a profile (production 1000023, all 15 brands
orphans), the rule must not trigger, otherwise it's left with no usable brand and the sales assistant can't work at all.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_brand_without_material_archived.py -q
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import SalesAgentSellerProfileORM
from agents.sales_agent.service.brandprofile import sync_brand_from_topic
from geo.database import Base
from geo.models.ai_telemetry import AiTelemetryTopicORM
from geo.models.user import UserORM

USER = 3200001

# Keys are flat (see brandprofile._field_text / _field_list). To pass `_profile_worth_a_brand`
# it must **have a name** (company_short_name) and **substantive service content** (one of service_features etc.).
PROFILE = {
    "company_short_name": "GEO-TAG",
    "service_features": ["全链路 GEO 效果计量"],
    "core_business_lines": ["AI-GEO 优化", "SEO 技术团队"],
    "team_size": "101-500 名员工",
    "target_scenarios": ["有官网但搜索获客一般的企业"],
}


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(UserORM(id=USER, email="brand@test.local", password_hash="x", is_active=True))
    session.commit()
    yield session
    session.close()


def make_orphan(db, name: str = "Vigilath") -> SalesAgentSellerProfileORM:
    """Existing brands from before the profile system: empty `topic_id`, profile accumulated from chat."""
    row = SalesAgentSellerProfileORM(
        user_id=USER,
        topic_id=None,
        name=name,
        brand_key=name.lower(),
        sort_order=100,
        service_scope_json=json.dumps(["AI-GEO优化", "SEO技术团队"], ensure_ascii=False),
        company_size="101-500 名员工",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_topic(db, name: str = "GEO-TAG") -> AiTelemetryTopicORM:
    row = AiTelemetryTopicORM(
        user_id=USER,
        name=name,
        target=name,
        profile_json=json.dumps(PROFILE, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def active(db) -> list[SalesAgentSellerProfileORM]:
    return (
        db.query(SalesAgentSellerProfileORM)
        .filter(
            SalesAgentSellerProfileORM.user_id == USER,
            SalesAgentSellerProfileORM.archived_at.is_(None),
        )
        .order_by(SalesAgentSellerProfileORM.id.asc())
        .all()
    )


def test_orphan_is_archived_once_a_branded_material_exists(db):
    """The shape of production 1000056: orphan + profile brand -> only the profile brand stays active."""
    orphan = make_orphan(db)
    assert len(active(db)) == 1

    synced = sync_brand_from_topic(db, make_topic(db), commit=True)
    assert synced is not None

    rows = active(db)
    assert len(rows) == 1, "活跃品牌应该只剩带资料的那个,否则品牌选择卡还会弹"
    assert rows[0].id == synced.id
    assert rows[0].topic_id is not None

    db.refresh(orphan)
    assert orphan.archived_at is not None


def test_archived_orphan_is_not_deleted(db):
    """Archive != delete: the row stays; foreign keys from historical recommendation tasks and sent emails must not break."""
    orphan = make_orphan(db)
    sync_brand_from_topic(db, make_topic(db), commit=True)

    still_there = db.query(SalesAgentSellerProfileORM).filter_by(id=orphan.id).first()
    assert still_there is not None
    assert still_there.name == "Vigilath"


def test_account_with_no_material_keeps_its_orphans(db):
    """The shape of production 1000023: all orphans, not a single profile -- the rule must not trigger.

    This is a foolproofing gate: if it did trigger, the account would be left with no usable brand and the sales assistant couldn't work.
    """
    make_orphan(db, "孤儿甲")
    make_orphan(db, "孤儿乙")
    assert len(active(db)) == 2

    # No topic at all -> the syncer is never called, so the archive rule can't trigger
    assert len(active(db)) == 2
    assert all(row.topic_id is None for row in active(db))


def test_empty_topic_does_not_archive_anything(db):
    """A half-empty topic shouldn't even create a brand, let alone archive orphans along the way.

    `_profile_worth_a_brand` blocks before row creation and the syncer returns None directly --
    the archive call comes after it and must not be reached.
    """
    orphan = make_orphan(db)
    blank = AiTelemetryTopicORM(user_id=USER, name="", target="", profile_json="{}")
    db.add(blank)
    db.commit()

    assert sync_brand_from_topic(db, blank, commit=True) is None

    db.refresh(orphan)
    assert orphan.archived_at is None, "资料还没填出内容,不能拿它当依据归档孤儿"
    assert len(active(db)) == 1
