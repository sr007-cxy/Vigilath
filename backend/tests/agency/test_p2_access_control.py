"""P2 tests for the unified enterprise data-scope authorization core."""
from __future__ import annotations

from types import SimpleNamespace
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from geo.database import Base
from geo.api.ai_telemetry import _get_topic_or_404, get_topic, list_topics
from geo.api.auth import is_staff, require_roles
from geo.api.content import _own_doc_or_404, _own_task_or_404
from geo.api.sentiment import _get_account_or_404, list_accounts
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM,
    AiTelemetryTopicExecutionPlanORM,
    ContentTemplateORM,
    TopicContentGenBatchORM,
    TopicDocPublishTaskORM,
    TopicGeneratedDocORM,
)
from geo.models.organization import (
    OrganizationGrantORM,
    OrganizationMemberAssignmentORM,
    OrganizationMemberORM,
    OrganizationORM,
    OperationContextORM,
    SystemAuditLogORM,
)
from geo.models.user import UserORM
from geo.models.aitoearn import AitoearnAccountBindingORM
from geo.models.sentiment import SentimentAccountORM
from geo.services.access_control import (
    ACTION_READ,
    ACTION_WRITE,
    ENTERPRISE_READ_ONLY,
    RESOURCE_NOT_FOUND,
    accessible_enterprise_user_ids,
    create_resource_operation_context,
    revalidate_operation_context,
    require_enterprise_access,
    resource_not_found,
)
from geo.utils.error_handler import AppException, global_exception_handler


TABLES = (
    UserORM.__table__,
    OrganizationORM.__table__,
    OrganizationMemberORM.__table__,
    OrganizationGrantORM.__table__,
    OrganizationMemberAssignmentORM.__table__,
    OperationContextORM.__table__,
    SystemAuditLogORM.__table__,
    AiTelemetryTopicORM.__table__,
    AiTelemetryTopicExecutionPlanORM.__table__,
    ContentTemplateORM.__table__,
    TopicContentGenBatchORM.__table__,
    TopicGeneratedDocORM.__table__,
    # topic_doc_publish_tasks.aitoearn_binding_id has a foreign key to it; with PRAGMA foreign_keys=ON it must be created first
    AitoearnAccountBindingORM.__table__,
    TopicDocPublishTaskORM.__table__,
    SentimentAccountORM.__table__,
)


def _actor(user_id: int, role: str):
    return SimpleNamespace(id=user_id, role=role, is_admin=role == "admin")


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

    Base.metadata.create_all(engine, tables=TABLES)
    session = Session(engine)
    users = [
        UserORM(
            id=user_id,
            email=f"p2-{user_id}@example.com",
            password_hash="x",
            role=role,
            is_admin=role == "admin",
            is_active=True,
        )
        for user_id, role in (
            (1, "admin"),
            (2, "operator"),
            (3, "user"),
            (4, "user"),
            (5, "agency_admin"),
            (6, "agency_operator"),
            (7, "agency_operator"),
            (8, "agency_admin"),
            (9, "agency_operator"),
            (10, "agency_admin"),
            (11, "agency_admin"),
        )
    ]
    session.add_all(users)
    session.commit()
    session.add_all(
        [
            OrganizationORM(
                id=100,
                organization_type="agency",
                name="Agency A",
                created_by_user_id=1,
            ),
            OrganizationORM(
                id=200,
                organization_type="agency",
                name="Agency B",
                created_by_user_id=1,
            ),
            OrganizationORM(
                id=300,
                organization_type="agency",
                name="Disabled Agency",
                status="disabled",
                created_by_user_id=1,
                disabled_by_user_id=1,
            ),
        ]
    )
    session.commit()
    session.add_all(
        [
            OrganizationMemberORM(
                id=500,
                organization_id=100,
                organization_type="agency",
                user_id=5,
                member_role="admin",
                joined_by_user_id=1,
            ),
            OrganizationMemberORM(
                id=501,
                organization_id=100,
                organization_type="agency",
                user_id=6,
                member_role="operator",
                joined_by_user_id=1,
            ),
            OrganizationMemberORM(
                id=502,
                organization_id=100,
                organization_type="agency",
                user_id=7,
                member_role="operator",
                joined_by_user_id=1,
            ),
            OrganizationMemberORM(
                id=503,
                organization_id=200,
                organization_type="agency",
                user_id=8,
                member_role="admin",
                joined_by_user_id=1,
            ),
            OrganizationMemberORM(
                id=504,
                organization_id=100,
                organization_type="agency",
                user_id=9,
                member_role="operator",
                status="removed",
                joined_by_user_id=1,
                removed_by_user_id=1,
            ),
            OrganizationMemberORM(
                id=505,
                organization_id=100,
                organization_type="agency",
                user_id=10,
                member_role="operator",
                joined_by_user_id=1,
            ),
            OrganizationMemberORM(
                id=506,
                organization_id=300,
                organization_type="agency",
                user_id=11,
                member_role="admin",
                joined_by_user_id=1,
            ),
        ]
    )
    session.commit()
    session.add_all(
        [
            OrganizationGrantORM(
                id=600,
                organization_id=100,
                organization_type="agency",
                target_user_id=3,
                granted_by_user_id=1,
            ),
            OrganizationGrantORM(
                id=601,
                organization_id=200,
                organization_type="agency",
                target_user_id=4,
                granted_by_user_id=1,
            ),
            OrganizationGrantORM(
                id=602,
                organization_id=100,
                organization_type="agency",
                target_user_id=4,
                status="revoked",
                granted_by_user_id=1,
                revoked_by_user_id=1,
            ),
        ]
    )
    session.commit()
    session.add(
        OrganizationMemberAssignmentORM(
            id=700,
            organization_id=100,
            organization_type="agency",
            member_id=501,
            grant_id=600,
            assigned_by_user_id=5,
        )
    )
    session.commit()
    session.add_all(
        [
            AiTelemetryTopicORM(id=800, user_id=3, name="Enterprise A topic"),
            AiTelemetryTopicORM(id=801, user_id=4, name="Enterprise B topic"),
            SentimentAccountORM(
                id=980, user_id=3, target="Enterprise A", ticker="enterprise-a",
            ),
            SentimentAccountORM(
                id=981, user_id=4, target="Enterprise B", ticker="enterprise-b",
            ),
        ]
    )
    session.commit()
    session.add(
        TopicGeneratedDocORM(
            id=900,
            topic_id=800,
            title="Enterprise A doc",
            body_markdown="body",
        )
    )
    session.commit()
    session.add(
        TopicDocPublishTaskORM(
            id=950,
            topic_id=800,
            doc_id=900,
            platform="manual",
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine, tables=reversed(TABLES))
        engine.dispose()


def _assert_hidden(db: Session, actor, enterprise_user_id: int) -> None:
    with pytest.raises(AppException) as caught:
        require_enterprise_access(db, actor, enterprise_user_id, ACTION_READ)
    assert caught.value.status_code == 404
    assert caught.value.key == RESOURCE_NOT_FOUND
    assert caught.value.message == "Resource not found"


def test_a01_agency_admin_reads_every_granted_enterprise(db: Session):
    context = require_enterprise_access(db, _actor(5, "agency_admin"), 3)
    assert context.is_delegated is True
    assert context.organization_id == 100
    assert context.organization_grant_id == 600
    assert context.organization_member_assignment_id is None


def test_a02_operator_reads_assigned_enterprise(db: Session):
    context = require_enterprise_access(db, _actor(6, "agency_operator"), 3)
    assert context.organization_member_assignment_id == 700


def test_a03_a04_unassigned_and_other_organization_are_identical_404(db: Session):
    _assert_hidden(db, _actor(7, "agency_operator"), 3)
    _assert_hidden(db, _actor(5, "agency_admin"), 4)


def test_a05_disabled_removed_and_revoked_are_identical_404(db: Session):
    _assert_hidden(db, _actor(11, "agency_admin"), 3)
    _assert_hidden(db, _actor(9, "agency_operator"), 3)
    _assert_hidden(db, _actor(5, "agency_admin"), 4)


def test_a06_enterprise_business_write_is_read_only_during_active_grant(db: Session):
    readable = require_enterprise_access(db, _actor(3, "user"), 3, ACTION_READ)
    assert readable.can_read is True
    assert readable.can_write is False
    with pytest.raises(AppException) as caught:
        require_enterprise_access(db, _actor(3, "user"), 3, ACTION_WRITE)
    assert caught.value.status_code == 403
    assert caught.value.key == ENTERPRISE_READ_ONLY


def test_a09_role_and_member_role_mismatch_is_denied(db: Session):
    _assert_hidden(db, _actor(10, "agency_admin"), 3)


def test_platform_and_self_scopes_remain_compatible(db: Session):
    assert accessible_enterprise_user_ids(db, _actor(1, "admin")) is None
    assert accessible_enterprise_user_ids(db, _actor(2, "operator")) is None
    assert accessible_enterprise_user_ids(db, _actor(3, "user")) == {3}
    assert accessible_enterprise_user_ids(db, _actor(5, "agency_admin")) == {3}
    assert accessible_enterprise_user_ids(db, _actor(6, "agency_operator")) == {3}
    assert accessible_enterprise_user_ids(db, _actor(7, "agency_operator")) == set()


def test_resource_not_found_public_error_shape_is_stable():
    app = FastAPI()
    app.add_exception_handler(AppException, global_exception_handler)

    @app.get("/hidden")
    def hidden():
        raise resource_not_found()

    with TestClient(app) as client:
        response = client.get("/hidden", headers={"X-Locale": "en"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": 404,
            "key": "RESOURCE_NOT_FOUND",
            "message": "Resource not found",
        }
    }


def test_topic_list_and_detail_use_enterprise_scope(db: Session):
    agency_admin = _actor(5, "agency_admin")
    assert [topic.id for topic in list_topics(agency_admin, db)] == [800]
    assert get_topic(800, agency_admin, db).id == 800
    with pytest.raises(AppException) as caught:
        get_topic(801, agency_admin, db)
    assert caught.value.key == RESOURCE_NOT_FOUND


def test_topic_list_can_narrow_an_agency_scope_to_the_selected_enterprise(db: Session):
    agency_admin = _actor(5, "agency_admin")

    assert [topic.id for topic in list_topics(agency_admin, db)] == [800]
    assert [topic.id for topic in list_topics(agency_admin, db, enterprise_user_id=3)] == [800]
    assert list_topics(agency_admin, db, enterprise_user_id=4) == []

    # Non-agency regular users who don't pass an agency enterprise parameter still see only their own topics.
    assert [topic.id for topic in list_topics(_actor(3, "user"), db)] == [800]


def test_doc_sentiment_and_publish_task_helpers_share_the_same_scope(db: Session):
    agency_operator = _actor(6, "agency_operator")
    assert _own_doc_or_404(
        db, 900, agency_operator.id,
        allow_admin_user=agency_operator, action=ACTION_READ,
    ).id == 900
    assert _own_task_or_404(
        db, 950, agency_operator.id,
        allow_admin_user=agency_operator, action=ACTION_READ,
    ).id == 950
    assert _get_account_or_404(db, 980, agency_operator, ACTION_READ).id == 980
    assert [account.id for account in list_accounts(agency_operator, db)] == [980]


def test_platform_routes_do_not_treat_agency_roles_as_staff():
    agency_admin = _actor(5, "agency_admin")
    assert is_staff(agency_admin) is False
    dependency = require_roles("admin", "operator")
    with pytest.raises(AppException) as caught:
        import asyncio
        asyncio.run(dependency(current_user=agency_admin))
    assert caught.value.status_code == 403


def test_p3_operation_context_freezes_agency_authorization_evidence(db: Session):
    topic = db.get(AiTelemetryTopicORM, 800)
    access, operation = create_resource_operation_context(
        db,
        _actor(6, "agency_operator"),
        topic,
        action="telemetry.run.create",
        target_resource_type="topic",
    )
    db.commit()

    assert access.authorization_mode == "organization"
    assert operation.enterprise_user_id == 3
    assert operation.organization_id == 100
    assert operation.organization_member_id == 501
    assert operation.organization_grant_id == 600
    assert operation.organization_member_assignment_id == 700
    assert revalidate_operation_context(db, operation.id)[0] == access


def test_p3_revoked_grant_blocks_not_started_operation(db: Session):
    topic = db.get(AiTelemetryTopicORM, 800)
    _, operation = create_resource_operation_context(
        db,
        _actor(5, "agency_admin"),
        topic,
        action="content.generate.create",
        target_resource_type="topic",
    )
    db.commit()

    grant = db.get(OrganizationGrantORM, 600)
    grant.status = "revoked"
    grant.revoked_by_user_id = 1
    db.commit()

    with pytest.raises(AppException) as caught:
        revalidate_operation_context(db, operation.id)
    assert caught.value.key == RESOURCE_NOT_FOUND


def test_p3_revoked_assignment_blocks_operator_but_keeps_snapshot(db: Session):
    topic = db.get(AiTelemetryTopicORM, 800)
    _, operation = create_resource_operation_context(
        db,
        _actor(6, "agency_operator"),
        topic,
        action="publish.task.create",
        target_resource_type="document",
    )
    db.commit()
    snapshot_assignment_id = operation.organization_member_assignment_id

    assignment = db.get(OrganizationMemberAssignmentORM, 700)
    assignment.status = "revoked"
    assignment.revoked_by_user_id = 5
    db.commit()

    with pytest.raises(AppException) as caught:
        revalidate_operation_context(db, operation.id)
    assert caught.value.key == RESOURCE_NOT_FOUND
    assert operation.organization_member_assignment_id == snapshot_assignment_id == 700


def test_p3_platform_operation_is_invalid_after_role_demotion(db: Session):
    topic = db.get(AiTelemetryTopicORM, 800)
    _, operation = create_resource_operation_context(
        db,
        _actor(2, "operator"),
        topic,
        action="solution.generate",
        target_resource_type="topic",
    )
    db.commit()

    actor = db.get(UserORM, 2)
    actor.role = "user"
    db.commit()

    with pytest.raises(AppException) as caught:
        revalidate_operation_context(db, operation.id)
    assert caught.value.key == RESOURCE_NOT_FOUND


def test_p3_write_gate_is_shared_by_topic_doc_task_and_sentiment(db: Session):
    agency_operator = _actor(6, "agency_operator")
    assert _get_topic_or_404(
        db, 800, agency_operator.id,
        allow_admin_user=agency_operator, action=ACTION_WRITE,
    ).id == 800
    assert _own_doc_or_404(
        db, 900, agency_operator.id,
        allow_admin_user=agency_operator, action=ACTION_WRITE,
    ).id == 900
    assert _own_task_or_404(
        db, 950, agency_operator.id,
        allow_admin_user=agency_operator, action=ACTION_WRITE,
    ).id == 950
    assert _get_account_or_404(
        db, 980, agency_operator, ACTION_WRITE,
    ).id == 980

    enterprise = _actor(3, "user")
    for load in (
        lambda: _get_topic_or_404(
            db, 800, enterprise.id,
            allow_admin_user=enterprise, action=ACTION_WRITE,
        ),
        lambda: _own_doc_or_404(
            db, 900, enterprise.id,
            allow_admin_user=enterprise, action=ACTION_WRITE,
        ),
        lambda: _own_task_or_404(
            db, 950, enterprise.id,
            allow_admin_user=enterprise, action=ACTION_WRITE,
        ),
        lambda: _get_account_or_404(db, 980, enterprise, ACTION_WRITE),
    ):
        with pytest.raises(AppException) as caught:
            load()
        assert caught.value.key == ENTERPRISE_READ_ONLY


def test_t04_aitoearn_auto_publish_cancels_before_external_call(db: Session):
    from geo.api import aitoearn_channel

    task = db.get(TopicDocPublishTaskORM, 950)
    doc = db.get(TopicGeneratedDocORM, 900)
    topic = db.get(AiTelemetryTopicORM, 800)
    _, operation = create_resource_operation_context(
        db,
        _actor(5, "agency_admin"),
        doc,
        action="publish.aitoearn",
        target_resource_type="document",
    )
    task.operation_context_id = operation.id
    task.status = "planned"
    db.commit()

    grant = db.get(OrganizationGrantORM, 600)
    grant.status = "revoked"
    grant.revoked_by_user_id = 1
    db.commit()

    result = asyncio.run(aitoearn_channel._publish_one(
        db,
        task,
        doc,
        {"account_id": "must-not-be-used"},
        _actor(5, "agency_admin"),
        selected_media_ids=[],
        selected_media_urls=[],
    ))
    assert result["action"] == "cancelled"
    assert db.get(TopicDocPublishTaskORM, task.id).status == "cancelled"
    assert topic.user_id == operation.enterprise_user_id


def test_rwj_final_boundary_cancels_before_order_submission(
    db: Session, monkeypatch,
):
    from geo.api import rwj_channel

    task = db.get(TopicDocPublishTaskORM, 950)
    doc = db.get(TopicGeneratedDocORM, 900)
    topic = db.get(AiTelemetryTopicORM, 800)
    _, operation = create_resource_operation_context(
        db,
        _actor(5, "agency_admin"),
        doc,
        action="publish.rwj",
        target_resource_type="document",
    )
    task.operation_context_id = operation.id
    task.status = "planned"
    db.commit()

    grant = db.get(OrganizationGrantORM, 600)
    grant.status = "revoked"
    grant.revoked_by_user_id = 1
    db.commit()

    monkeypatch.setattr(rwj_channel, "publish_gate", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr(
        rwj_channel.rwj_pub,
        "publish",
        lambda *args, **kwargs: pytest.fail("revoked task reached external order"),
    )
    result = rwj_channel._publish_one_inner(
        db,
        task,
        doc,
        topic,
        {},
        "rwj",
        "news",
        1,
        "media",
        "1.00",
        _actor(5, "agency_admin"),
    )
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert db.get(TopicDocPublishTaskORM, task.id).status == "cancelled"
