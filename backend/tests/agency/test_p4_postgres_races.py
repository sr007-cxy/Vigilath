"""P4 PostgreSQL management concurrency acceptance; runs when P4_POSTGRES_URL is set."""
from __future__ import annotations

import os
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from geo.models.organization import (
    GRANT_STATUS_ACTIVE,
    MEMBER_ROLE_ADMIN,
    MEMBER_STATUS_ACTIVE,
    ORGANIZATION_TYPE_AGENCY,
    OrganizationGrantORM,
    OrganizationMemberORM,
)
from geo.models.user import UserORM
from geo.services.organization_service import (
    add_member,
    create_agency,
    grant_enterprise,
    remove_member,
)
from geo.utils.error_handler import AppException


DATABASE_URL = os.environ.get("P4_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires P4_POSTGRES_URL for PostgreSQL row-lock semantics",
)


def _engine():
    return create_engine(DATABASE_URL, future=True, pool_pre_ping=True)


def _base_id() -> int:
    return 1_500_000_000 + int(time.time_ns() % 400_000_000)


def _user(user_id: int, role: str = "user") -> UserORM:
    return UserORM(
        id=user_id,
        email=f"p4-race-{user_id}@example.com",
        password_hash="x",
        role=role,
        is_admin=role == "admin",
        is_active=True,
    )


def test_m01_two_organizations_cannot_grant_one_enterprise():
    engine = _engine()
    base = _base_id()
    platform_id, first_admin_a, first_admin_b, enterprise_id = range(base, base + 4)
    with Session(engine) as db:
        db.add_all([
            _user(platform_id, "admin"), _user(first_admin_a),
            _user(first_admin_b), _user(enterprise_id),
        ])
        db.commit()
        agency_a = create_agency(
            db, actor_user_id=platform_id, name="P4 Race A",
            first_admin_user_id=first_admin_a,
        )
        db.commit()
        agency_b = create_agency(
            db, actor_user_id=platform_id, name="P4 Race B",
            first_admin_user_id=first_admin_b,
        )
        db.commit()
        organization_ids = (agency_a.id, agency_b.id)

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def grant(organization_id: int):
        with Session(engine) as db:
            barrier.wait(timeout=5)
            try:
                grant_enterprise(
                    db, actor_user_id=platform_id,
                    organization_id=organization_id,
                    enterprise_user_id=enterprise_id,
                )
                db.commit()
                outcomes.append("granted")
            except AppException:
                db.rollback()
                outcomes.append("conflict")

    threads = [threading.Thread(target=grant, args=(organization_id,))
               for organization_id in organization_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["conflict", "granted"]
    with Session(engine) as db:
        assert db.query(OrganizationGrantORM).filter(
            OrganizationGrantORM.target_user_id == enterprise_id,
            OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
        ).count() == 1
    engine.dispose()


def test_m02_two_admins_cannot_remove_each_other_and_leave_no_admin():
    engine = _engine()
    base = _base_id()
    platform_id, admin_a_id, admin_b_id = range(base, base + 3)
    with Session(engine) as db:
        db.add_all([_user(platform_id, "admin"), _user(admin_a_id), _user(admin_b_id)])
        db.commit()
        agency = create_agency(
            db, actor_user_id=platform_id, name="P4 Admin Race",
            first_admin_user_id=admin_a_id,
        )
        db.commit()
        member_a = db.query(OrganizationMemberORM).filter(
            OrganizationMemberORM.organization_id == agency.id,
            OrganizationMemberORM.user_id == admin_a_id,
            OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
        ).one()
        member_b = add_member(
            db, actor_user_id=platform_id, organization_id=agency.id,
            user_id=admin_b_id, member_role=MEMBER_ROLE_ADMIN,
        )
        db.commit()
        organization_id = agency.id
        pairs = ((admin_a_id, member_b.id), (admin_b_id, member_a.id))

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def remove(actor_user_id: int, target_member_id: int):
        with Session(engine) as db:
            barrier.wait(timeout=5)
            try:
                remove_member(
                    db, actor_user_id=actor_user_id,
                    organization_id=organization_id,
                    member_id=target_member_id,
                )
                db.commit()
                outcomes.append("removed")
            except AppException:
                db.rollback()
                outcomes.append("protected")

    threads = [threading.Thread(target=remove, args=pair) for pair in pairs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["protected", "removed"]
    with Session(engine) as db:
        active_admins = db.query(OrganizationMemberORM).filter(
            OrganizationMemberORM.organization_id == organization_id,
            OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
            OrganizationMemberORM.member_role == MEMBER_ROLE_ADMIN,
        ).count()
        assert active_admins == 1
    engine.dispose()
