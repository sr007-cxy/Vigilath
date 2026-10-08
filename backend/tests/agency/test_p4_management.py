"""P4 organization management transaction and API contract tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from geo.api import agency_management
from geo.api.auth import get_current_user
from geo.api.ai_telemetry import get_db
from geo.database import Base, settings
from geo.models.organization import (
    OrganizationGrantORM,
    OrganizationMemberAssignmentORM,
    OrganizationMemberORM,
    OrganizationORM,
    OperationContextORM,
    SystemAuditLogORM,
)
from geo.models.user import UserORM
from geo.services.organization_service import (
    AGENCY_LAST_ADMIN,
    add_member,
    assign_enterprise,
    create_agency,
    grant_enterprise,
    remove_member,
    revoke_grant,
)
from geo.services import organization_service
from geo.utils.error_handler import AppException, global_exception_handler


TABLES = (
    UserORM.__table__,
    OrganizationORM.__table__,
    OrganizationMemberORM.__table__,
    OrganizationGrantORM.__table__,
    OrganizationMemberAssignmentORM.__table__,
    OperationContextORM.__table__,
    SystemAuditLogORM.__table__,
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine, tables=TABLES)
    session = Session(engine)
    session.add_all([
        UserORM(id=1, email="admin@example.com", password_hash="x", role="admin",
                is_admin=True, is_active=True),
        UserORM(id=2, email="agency-admin@example.com", password_hash="x", role="user",
                is_admin=False, is_active=True, phone="13800000002", name="Agency Admin"),
        UserORM(id=3, email="operator@example.com", password_hash="x", role="user",
                is_admin=False, is_active=True, phone="13800000003", name="Operator"),
        UserORM(id=4, email="enterprise@example.com", password_hash="x", role="user",
                is_admin=False, is_active=True, phone="13800000004", name="Enterprise"),
        UserORM(id=5, email="second-admin@example.com", password_hash="x", role="user",
                is_admin=False, is_active=True, phone="13800000005"),
        UserORM(id=6, email="other-operator@example.com", password_hash="x", role="user",
                is_admin=False, is_active=True, phone="13800000006"),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine, tables=reversed(TABLES))
        engine.dispose()


def _actor(user_id: int, role: str):
    return SimpleNamespace(id=user_id, role=role, is_admin=role == "admin")


def test_member_role_assignment_recovery_and_audit_share_one_transaction(db: Session):
    agency = create_agency(
        db, actor_user_id=1, name="Agency A", first_admin_user_id=2
    )
    operator = add_member(
        db, actor_user_id=2, organization_id=agency.id,
        user_id=3, member_role="operator",
    )
    grant = grant_enterprise(
        db, actor_user_id=1, organization_id=agency.id, enterprise_user_id=4
    )
    assignment = assign_enterprise(
        db, actor_user_id=2, organization_id=agency.id,
        member_id=operator.id, grant_id=grant.id,
    )
    remove_member(
        db, actor_user_id=2, organization_id=agency.id, member_id=operator.id
    )
    db.commit()

    assert db.get(UserORM, 2).role == "agency_admin"
    assert db.get(UserORM, 3).role == "user"
    assert db.get(OrganizationMemberORM, operator.id).status == "removed"
    assert db.get(OrganizationMemberAssignmentORM, assignment.id).status == "revoked"
    assert db.get(OrganizationGrantORM, grant.id).status == "active"
    events = db.query(SystemAuditLogORM).order_by(SystemAuditLogORM.id).all()
    assert [row.event_type for row in events] == [
        "organization.created",
        "organization.member_added",
        "organization.member_added",
        "organization.grant_created",
        "organization.assignment_created",
        "organization.member_removed",
    ]
    assert all(row.actor_user_id is not None and row.organization_id == agency.id for row in events)


def test_grant_revocation_cascades_assignments_without_changing_enterprise_role(db: Session):
    agency = create_agency(db, actor_user_id=1, name="Agency", first_admin_user_id=2)
    operator = add_member(
        db, actor_user_id=2, organization_id=agency.id, user_id=3, member_role="operator"
    )
    grant = grant_enterprise(
        db, actor_user_id=1, organization_id=agency.id, enterprise_user_id=4
    )
    assignment = assign_enterprise(
        db, actor_user_id=2, organization_id=agency.id,
        member_id=operator.id, grant_id=grant.id,
    )
    revoke_grant(
        db, actor_user_id=1, organization_id=agency.id, grant_id=grant.id,
        reason="contract ended",
    )
    db.commit()

    assert db.get(OrganizationGrantORM, grant.id).status == "revoked"
    assert db.get(OrganizationMemberAssignmentORM, assignment.id).status == "revoked"
    assert db.get(UserORM, 4).role == "user"


def test_last_active_admin_cannot_be_removed(db: Session):
    agency = create_agency(db, actor_user_id=1, name="Agency", first_admin_user_id=2)
    db.commit()
    with pytest.raises(AppException) as exc:
        remove_member(
            db, actor_user_id=1, organization_id=agency.id,
            member_id=db.query(OrganizationMemberORM.id).filter(
                OrganizationMemberORM.organization_id == agency.id
            ).scalar(), allow_self=True,
        )
    assert exc.value.key == AGENCY_LAST_ADMIN
    db.rollback()
    assert db.get(UserORM, 2).role == "agency_admin"


def test_audit_failure_rolls_back_role_membership_and_organization(db: Session, monkeypatch):
    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(organization_service, "append_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        create_agency(db, actor_user_id=1, name="Rollback Agency", first_admin_user_id=2)
    db.rollback()

    assert db.get(UserORM, 2).role == "user"
    assert db.query(OrganizationORM).count() == 0
    assert db.query(OrganizationMemberORM).count() == 0
    assert db.query(SystemAuditLogORM).count() == 0


@pytest.fixture()
def client(db: Session):
    app = FastAPI()
    app.add_exception_handler(AppException, global_exception_handler)
    app.include_router(agency_management.admin_router, prefix="/api")
    app.include_router(agency_management.agency_router, prefix="/api")
    app.include_router(agency_management.account_router, prefix="/api")
    current = {"actor": _actor(1, "admin")}
    app.dependency_overrides[get_current_user] = lambda: current["actor"]
    app.dependency_overrides[get_db] = lambda: db
    previous = settings.AGENCY_WORKBENCH_ENABLED
    settings.AGENCY_WORKBENCH_ENABLED = True
    try:
        with TestClient(app) as test_client:
            yield test_client, current
    finally:
        settings.AGENCY_WORKBENCH_ENABLED = previous
        app.dependency_overrides.clear()


def test_api_exact_lookup_is_masked_and_agency_scope_is_enforced(client, db: Session):
    http, current = client
    created = http.post("/api/admin/agencies", json={
        "name": "API Agency", "first_admin_user_id": 2,
    })
    assert created.status_code == 201, created.text
    organization_id = created.json()["id"]

    eligible = http.get(
        f"/api/admin/agencies/{organization_id}/eligible-enterprises",
        params={"email": "enterprise@example.com"},
    )
    assert eligible.status_code == 200
    enterprise = eligible.json()[0]
    assert set(enterprise) == {"id", "masked_email", "masked_phone", "display_name"}
    assert enterprise["masked_email"] != "enterprise@example.com"
    assert enterprise["masked_phone"] != "13800000004"

    granted = http.post(
        f"/api/admin/agencies/{organization_id}/grants",
        json={"enterprise_user_id": 4},
    )
    assert granted.status_code == 201, granted.text

    current["actor"] = _actor(2, "agency_admin")
    added = http.post("/api/agency/members", json={"email": "operator@example.com"})
    assert added.status_code == 201, added.text
    member_id = added.json()["id"]
    assigned = http.post("/api/agency/assignments", json={
        "member_id": member_id, "grant_id": granted.json()["id"],
    })
    assert assigned.status_code == 201, assigned.text

    current["actor"] = _actor(3, "agency_operator")
    enterprises = http.get("/api/agency/enterprises")
    assert enterprises.status_code == 200
    assert [row["enterprise"]["id"] for row in enterprises.json()] == [4]
    assert "email" not in enterprises.json()[0]["enterprise"]

    current["actor"] = _actor(4, "user")
    account = http.get("/api/account/agency")
    assert account.status_code == 200
    assert account.json()["agency"]["id"] == organization_id


def test_feature_flag_hides_all_management_routes(db: Session):
    app = FastAPI()
    app.add_exception_handler(AppException, global_exception_handler)
    app.include_router(agency_management.admin_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: _actor(1, "admin")
    app.dependency_overrides[get_db] = lambda: db
    previous = settings.AGENCY_WORKBENCH_ENABLED
    settings.AGENCY_WORKBENCH_ENABLED = False
    try:
        response = TestClient(app).get("/api/admin/agencies")
        assert response.status_code == 404
        assert response.json()["error"]["key"] == "RESOURCE_NOT_FOUND"
    finally:
        settings.AGENCY_WORKBENCH_ENABLED = previous
