"""`sales_agent_account_icps`: one row per brand -- stop creating a new row every time recommendations run.

Before the change `_account_icp_by_legacy` short-circuited to None when `legacy_profile_id is None`,
and when the find-customers flow calls in, both `account_icp_id` and the legacy profile **are empty** (the comment on the
seller side in recommend.py already said so; only the seller half of the fallback was added back then). So every recommendation run
created a new row with `SalesAgentAccountICPORM(user_id=...)`.

On the surface nothing looked wrong: every read path is `order_by(id.desc()).first()`, always reading the newest row, and
"suitable customers" are re-selected every round anyway -- so this bug never surfaced. The real victims were the brand-level settings on that row
that are **not** re-selected every round (negative_icp / scoring_rubric / observable_signals):
new rows took column defaults, silently wiping them on every recommendation run.

The second group pins the `if/elif` in `_latest_account_icp`: of the five call sites passing both `account_icp_id` and
`seller_profile_id`, the latter was a dead parameter in all of them -- looking like brand isolation while actually only checking
`user_id`. And `account_icp_id` is echoed back by the model (tools.py requires the LLM to carry it along),
so in long multi-brand sessions the model repeated brand A's id, and brand B's recommendation ran on A's ICP.
Same failure mode as da38e54e's "two brands reading the same row".

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_account_icp_single_row.py -q
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.constants import DEFAULT_BUYER_ROLES
from agents.sales_agent.models import (
    SalesAgentAccountICPORM,
    SalesAgentBuyerPersonaORM,
    SalesAgentSellerProfileORM,
)
from agents.sales_agent.service import _profile_for_recommendation
from agents.sales_agent.service.profiles import _latest_account_icp
from geo.database import Base
from geo.models.user import UserORM

USER = 3100001

# Brand-level settings **not** re-selected every round. Under the old new-row-per-round code, these two were wiped on every recommendation run.
CUSTOM_EXCLUDES = ["同行服务商", "我们自己的代理商"]
CUSTOM_RUBRIC = {"官网陈旧": 30, "无 AI 曝光": 50}
CUSTOM_SIGNALS = ["官网有博客", "在招 SEO"]


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(UserORM(id=USER, email="icp@test.local", password_hash="x", is_active=True))
    session.commit()
    yield session
    session.close()


def make_brand(db, name: str, sort_order: int = 100) -> SalesAgentSellerProfileORM:
    row = SalesAgentSellerProfileORM(
        user_id=USER,
        name=name,
        brand_key=name.lower(),
        sort_order=sort_order,
        service_scope_json=json.dumps(["GEO 检测"], ensure_ascii=False),
        company_size="11-100 名员工",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def run_recommendation(db, typical: list[str]) -> None:
    """Go through the find-customers main path once: all three ids empty, exactly the branch that missed the fallback."""
    _profile_for_recommendation(
        db,
        USER,
        None,               # profile_id
        ["GEO 检测"],        # service_scope
        ["金融"],            # industries
        ["全国"],            # regions
        typical_customers=typical,
    )
    db.commit()


# --- One row per brand ---

def test_repeated_recommendations_reuse_one_icp_row(db):
    make_brand(db, "GEO-TAG")
    run_recommendation(db, ["有官网但搜索获客一般的公司"])
    run_recommendation(db, ["想提升 AI 曝光的企业"])
    run_recommendation(db, ["全行业中小企业"])
    assert db.query(SalesAgentAccountICPORM).count() == 1


def test_repeated_recommendations_keep_brand_level_config(db):
    """Running recommendations shouldn't reset exclusion rules and scoring rubrics to column defaults."""
    make_brand(db, "GEO-TAG")
    run_recommendation(db, ["有官网但搜索获客一般的公司"])

    icp = db.query(SalesAgentAccountICPORM).one()
    icp.negative_icp_json = json.dumps(CUSTOM_EXCLUDES, ensure_ascii=False)
    icp.scoring_rubric_json = json.dumps(CUSTOM_RUBRIC, ensure_ascii=False)
    icp.observable_signals_json = json.dumps(CUSTOM_SIGNALS, ensure_ascii=False)
    db.commit()

    run_recommendation(db, ["想提升 AI 曝光的企业"])

    icp = db.query(SalesAgentAccountICPORM).one()
    assert json.loads(icp.negative_icp_json) == CUSTOM_EXCLUDES
    assert json.loads(icp.scoring_rubric_json) == CUSTOM_RUBRIC
    # The file header names three items to guard, but only the first two were asserted -- observable_signals is also a brand-level setting not re-selected each round
    assert json.loads(icp.observable_signals_json) == CUSTOM_SIGNALS
    # "Suitable customers" are re-selected every round, so this item **should** change
    assert json.loads(icp.target_company_types_json) == ["想提升 AI 曝光的企业"]


def test_buyer_personas_do_not_multiply(db):
    """Personas have their own dedupe; their growth was dragged by the ICP -- stabilize the ICP and they stabilize.

    `create_default_buyer_personas` looks up before creating rows by
    (user_id, seller_profile_id, account_icp_id, role_name), and the caller
    has a second layer `if not _buyer_personas_for_context(...)`. Both layers depend on
    account_icp_id being stable; when it changes every round the lookup never finds anything, so a full set is created each round.

    Measured in production 2026-08-27: cleaned up 24 surplus ICP rows and 96 duplicate personas -- exactly 24x4
    (4 default roles), not one more or less.
    """
    make_brand(db, "GEO-TAG")
    for i in range(5):
        run_recommendation(db, [f"第{i}轮客户"])

    assert db.query(SalesAgentAccountICPORM).count() == 1
    personas = db.query(SalesAgentBuyerPersonaORM).all()
    assert len(personas) == len(DEFAULT_BUYER_ROLES)
    # All under the same ICP row, with no duplicate roles
    assert len({p.account_icp_id for p in personas}) == 1
    assert len({p.role_name for p in personas}) == len(personas)


def test_each_brand_gets_its_own_icp_row(db):
    """One row per brand != one row per account. Two brands get one row each, without overwriting each other."""
    a = make_brand(db, "GEO-TAG", sort_order=100)
    b = make_brand(db, "MoltsPay", sort_order=200)
    for brand in (a, b):
        _profile_for_recommendation(
            db, USER, None, ["GEO 检测"], ["金融"], ["全国"],
            seller_profile_id=brand.id,
            typical_customers=[f"{brand.name} 的客户"],
        )
        db.commit()
    rows = db.query(SalesAgentAccountICPORM).all()
    assert len(rows) == 2
    assert {r.seller_profile_id for r in rows} == {a.id, b.id}


# --- Brand isolation: the two filter conditions are combined, not either-or ---

def test_cross_brand_icp_id_is_not_resolved(db):
    """Looking up brand A's ICP id in brand B's context -- must find nothing, never silently return A's row."""
    a = make_brand(db, "GEO-TAG", sort_order=100)
    b = make_brand(db, "MoltsPay", sort_order=200)
    icp_a = SalesAgentAccountICPORM(user_id=USER, seller_profile_id=a.id)
    db.add(icp_a)
    db.commit()
    db.refresh(icp_a)

    assert _latest_account_icp(db, USER, icp_a.id, a.id) is icp_a      # own brand: found
    assert _latest_account_icp(db, USER, icp_a.id, b.id) is None       # cross-brand: not found


def test_orphan_icp_row_still_resolves(db):
    """`seller_profile_id IS NULL` marks existing rows from before the seller_profile table existed.

    No owner != belonging to another brand -- blocking them would make old accounts 404 outright.
    """
    brand = make_brand(db, "GEO-TAG")
    orphan = SalesAgentAccountICPORM(user_id=USER, seller_profile_id=None)
    db.add(orphan)
    db.commit()
    db.refresh(orphan)

    assert _latest_account_icp(db, USER, orphan.id, brand.id) is orphan


def test_other_users_icp_id_is_not_resolved(db):
    """The existing user_id isolation must not be loosened by this change."""
    other = 3100002
    db.add(UserORM(id=other, email="icp2@test.local", password_hash="x", is_active=True))
    db.commit()
    foreign = SalesAgentAccountICPORM(user_id=other, seller_profile_id=None)
    db.add(foreign)
    db.commit()
    db.refresh(foreign)

    assert _latest_account_icp(db, USER, foreign.id) is None
