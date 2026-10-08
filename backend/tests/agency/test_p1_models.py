"""P1 tests for the generic organization authorization model and database constraints."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from geo.models.organization import (
    AUTHORIZATION_MODE_ORGANIZATION,
    MEMBER_ROLE_ADMIN,
    MEMBER_ROLE_OPERATOR,
    OperationContextORM,
    OrganizationGrantORM,
    OrganizationMemberAssignmentORM,
    OrganizationMemberORM,
    OrganizationORM,
    SystemAuditLogORM,
)
import geo.api.admin_users as admin_users
from geo.api.ai_telemetry import get_db
from geo.api.auth import require_admin
from geo.models.user import ALLOWED_ROLES, DIRECT_ASSIGNABLE_ROLES, UserORM


REPO_ROOT = Path(__file__).resolve().parents[3]

P1_TABLES = (
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
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    UserORM.metadata.create_all(engine, tables=P1_TABLES)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        UserORM.metadata.drop_all(engine, tables=reversed(P1_TABLES))
        engine.dispose()


def _user(user_id: int, role: str, email: str | None = None) -> UserORM:
    return UserORM(
        id=user_id,
        email=email or f"u{user_id}@example.com",
        password_hash="x",
        role=role,
        is_admin=role == "admin",
        is_active=True,
    )


def _seed_users_and_organizations(db: Session) -> None:
    db.add_all(
        [
            _user(1, "admin"),
            _user(2, "agency_admin"),
            _user(3, "agency_operator"),
            _user(4, "user"),
            _user(5, "agency_operator"),
        ]
    )
    db.commit()
    db.add_all(
        [
            OrganizationORM(
                id=10,
                organization_type="agency",
                name="Agency A",
                created_by_user_id=1,
            ),
            OrganizationORM(
                id=20,
                organization_type="agency",
                name="Agency B",
                created_by_user_id=1,
            ),
        ]
    )
    db.commit()


def test_role_value_set_includes_agency_roles_and_rejects_unknown_role(db: Session):
    assert "agency_admin" in ALLOWED_ROLES
    assert "agency_operator" in ALLOWED_ROLES
    assert "agency_admin" not in DIRECT_ASSIGNABLE_ROLES
    assert "agency_operator" not in DIRECT_ASSIGNABLE_ROLES

    db.add(_user(99, "unknown_role"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_generic_admin_user_api_cannot_set_or_remove_agency_roles():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    UserORM.metadata.create_all(engine, tables=[UserORM.__table__])
    test_session = sessionmaker(bind=engine)
    with test_session() as db:
        db.add_all(
            [
                _user(1, "admin"),
                _user(7, "user"),
                _user(8, "agency_admin"),
            ]
        )
        db.commit()

    app = FastAPI()
    app.include_router(admin_users.router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=1)

    def _db():
        with test_session() as db:
            yield db

    app.dependency_overrides[get_db] = _db
    client = TestClient(app)
    try:
        set_agency = client.patch(
            "/admin/users/7/role", json={"role": "agency_operator"}
        )
        remove_agency = client.patch(
            "/admin/users/8/role", json={"role": "user"}
        )
        create_agency = client.post(
            "/admin/users",
            json={"email": "new-agency@example.com", "role": "agency_admin"},
        )
        assert set_agency.status_code == 409
        assert remove_agency.status_code == 409
        assert create_agency.status_code == 409
    finally:
        client.close()
        engine.dispose()


def test_active_agency_member_can_belong_to_only_one_agency(db: Session):
    _seed_users_and_organizations(db)
    db.add(
        OrganizationMemberORM(
            id=100,
            organization_id=10,
            organization_type="agency",
            user_id=2,
            member_role=MEMBER_ROLE_ADMIN,
            joined_by_user_id=1,
        )
    )
    db.commit()

    db.add(
        OrganizationMemberORM(
            id=101,
            organization_id=20,
            organization_type="agency",
            user_id=2,
            member_role=MEMBER_ROLE_ADMIN,
            joined_by_user_id=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_enterprise_can_have_only_one_active_agency_grant(db: Session):
    _seed_users_and_organizations(db)
    db.add(
        OrganizationGrantORM(
            id=200,
            organization_id=10,
            organization_type="agency",
            target_user_id=4,
            granted_by_user_id=1,
        )
    )
    db.commit()

    db.add(
        OrganizationGrantORM(
            id=201,
            organization_id=20,
            organization_type="agency",
            target_user_id=4,
            granted_by_user_id=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_assignment_member_and_grant_must_share_organization(db: Session):
    _seed_users_and_organizations(db)
    member = OrganizationMemberORM(
        id=100,
        organization_id=20,
        organization_type="agency",
        user_id=5,
        member_role=MEMBER_ROLE_OPERATOR,
        joined_by_user_id=1,
    )
    grant = OrganizationGrantORM(
        id=200,
        organization_id=10,
        organization_type="agency",
        target_user_id=4,
        granted_by_user_id=1,
    )
    db.add_all([member, grant])
    db.commit()

    db.add(
        OrganizationMemberAssignmentORM(
            organization_id=20,
            organization_type="agency",
            member_id=member.id,
            grant_id=grant.id,
            assigned_by_user_id=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_operation_context_and_system_audit_are_append_only_in_orm(db: Session):
    _seed_users_and_organizations(db)
    context = OperationContextORM(
        trace_id="trace-p1",
        actor_user_id=2,
        enterprise_user_id=4,
        authorization_mode=AUTHORIZATION_MODE_ORGANIZATION,
        action="topic.read",
        target_resource_type="topic",
        target_resource_id="123",
        organization_id=10,
    )
    db.add(context)
    db.flush()
    audit = SystemAuditLogORM(
        operation_context_id=context.id,
        trace_id=context.trace_id,
        organization_id=10,
        event_type="authorization_checked",
        actor_user_id=2,
        enterprise_user_id=4,
        action="topic.read",
        object_type="topic",
        object_id="123",
        result="success",
    )
    db.add(audit)
    db.commit()

    context.action = "topic.write"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()

    audit = db.get(SystemAuditLogORM, audit.id)
    db.delete(audit)
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()


def test_first_phase_async_models_reference_operation_context():
    from geo.models.ai_telemetry import (
        AiTelemetryRunORM,
        AiTelemetryTopicBriefingORM,
        AiTelemetryTopicSolutionORM,
        AiTelemetryTopicSourceStrategyORM,
        TopicContentGenBatchORM,
        TopicDocPublishTaskORM,
    )
    from geo.models.publish_accounts import PublishDispatchTaskORM
    from geo.models.sentiment import SentimentRunLogORM

    async_models = (
        AiTelemetryRunORM,
        AiTelemetryTopicBriefingORM,
        AiTelemetryTopicSourceStrategyORM,
        AiTelemetryTopicSolutionORM,
        TopicContentGenBatchORM,
        TopicDocPublishTaskORM,
        SentimentRunLogORM,
        PublishDispatchTaskORM,
    )
    for model in async_models:
        column = inspect(model).columns.operation_context_id
        assert column.nullable is True
        assert next(iter(column.foreign_keys)).target_fullname == "operation_contexts.id"


def test_migration_contains_database_append_only_and_partial_unique_guards():
    migration = (
        REPO_ROOT
        / "backend"
        / "alembic"
        / "versions"
        / "b7a2d9e4c631_add_organization_access_models.py"
    ).read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE" in migration
    assert "SELECT RAISE(ABORT" in migration
    assert "uq_active_agency_member" in migration
    assert "uq_active_agency_enterprise_grant" in migration
    assert "postgresql_where" in migration
    assert '"browser_jobs"' in migration
