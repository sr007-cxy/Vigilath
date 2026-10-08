"""P3 real-time authorization recheck and denial audit for the standalone Worker."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from geo.database import Base
from geo.models.organization import (
    OperationContextORM,
    OrganizationGrantORM,
    OrganizationMemberAssignmentORM,
    OrganizationMemberORM,
    OrganizationORM,
    SystemAuditLogORM,
)
from geo.models.user import UserORM


# worker-center (formerly services/telemetry-service) lives in the separate V-networks repo since 2026-09-21.
# By default it's looked up at ../V-networks next to the main repo, or a checkout via V_NETWORKS_ROOT; if not found the whole file is skipped --
# these two cases verify "both repos lock rows of the same table in the same order", which can't be checked without that code.
TELEMETRY_ROOT = Path(
    os.environ.get("V_NETWORKS_ROOT")
    or Path(__file__).resolve().parents[4] / "V-networks"
) / "worker-center"
if not (TELEMETRY_ROOT / "app" / "storage.py").exists():
    pytest.skip(f"requires V-networks checkout (V_NETWORKS_ROOT), not found at {TELEMETRY_ROOT}",
                allow_module_level=True)
if str(TELEMETRY_ROOT) not in sys.path:
    sys.path.insert(0, str(TELEMETRY_ROOT))

from app.storage import (  # noqa: E402
    append_system_audit_denied,
    revalidate_operation_context,
)


TABLES = (
    UserORM.__table__,
    OrganizationORM.__table__,
    OrganizationMemberORM.__table__,
    OrganizationGrantORM.__table__,
    OrganizationMemberAssignmentORM.__table__,
    OperationContextORM.__table__,
    SystemAuditLogORM.__table__,
)


def _session() -> tuple[Session, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine, tables=TABLES)
    return Session(engine), engine


def test_worker_revalidates_snapshot_and_audits_revocation():
    db, engine = _session()
    try:
        db.add_all([
            UserORM(
                id=1, email="platform@example.com", password_hash="x",
                role="admin", is_admin=True, is_active=True,
            ),
            UserORM(
                id=2, email="agency@example.com", password_hash="x",
                role="agency_operator", is_admin=False, is_active=True,
            ),
            UserORM(
                id=3, email="enterprise@example.com", password_hash="x",
                role="user", is_admin=False, is_active=True,
            ),
        ])
        db.commit()
        db.add(OrganizationORM(
            id=10,
            organization_type="agency",
            name="Agency",
            created_by_user_id=1,
        ))
        db.commit()
        db.add(OrganizationMemberORM(
            id=20,
            organization_id=10,
            organization_type="agency",
            user_id=2,
            member_role="operator",
            joined_by_user_id=1,
        ))
        db.commit()
        db.add(OrganizationGrantORM(
            id=30,
            organization_id=10,
            organization_type="agency",
            target_user_id=3,
            granted_by_user_id=1,
        ))
        db.commit()
        db.add(OrganizationMemberAssignmentORM(
            id=40,
            organization_id=10,
            organization_type="agency",
            member_id=20,
            grant_id=30,
            assigned_by_user_id=1,
        ))
        db.commit()
        context = OperationContextORM(
            id=50,
            trace_id="p3-worker",
            actor_user_id=2,
            enterprise_user_id=3,
            authorization_mode="organization",
            action="telemetry.run.create",
            target_resource_type="topic",
            target_resource_id="99",
            organization_id=10,
            organization_member_id=20,
            organization_grant_id=30,
            organization_member_assignment_id=40,
        )
        db.add(context)
        db.commit()

        assert revalidate_operation_context(db, context.id) is True

        assignment = db.get(OrganizationMemberAssignmentORM, 40)
        assignment.status = "revoked"
        assignment.revoked_by_user_id = 1
        db.commit()

        assert revalidate_operation_context(db, context.id) is False
        append_system_audit_denied(
            db,
            context.id,
            action="telemetry.run.execute",
            object_type="ai_telemetry_run",
            object_id=88,
        )
        db.commit()
        audit = db.query(SystemAuditLogORM).one()
        assert audit.operation_context_id == context.id
        assert audit.result == "denied"
        assert audit.reason == "RESOURCE_NOT_FOUND"
        assert audit.object_type == "ai_telemetry_run"
        assert audit.object_id == "88"
    finally:
        db.close()
        Base.metadata.drop_all(engine, tables=reversed(TABLES))
        engine.dispose()
