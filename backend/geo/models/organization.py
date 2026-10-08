"""ORM for generic organization authorization, operation context and system audit.

This module only defines system-level relations; it contains no agency API or authorization logic. An agency is
the first policy implementation with ``organization_type='agency'``; later organization types can reuse the same tables.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)

from geo.database import Base


ORGANIZATION_TYPE_AGENCY = "agency"

ORGANIZATION_STATUS_ACTIVE = "active"
ORGANIZATION_STATUS_DISABLED = "disabled"
ORGANIZATION_STATUSES = (
    ORGANIZATION_STATUS_ACTIVE,
    ORGANIZATION_STATUS_DISABLED,
)

MEMBER_ROLE_ADMIN = "admin"
MEMBER_ROLE_OPERATOR = "operator"
MEMBER_ROLES = (MEMBER_ROLE_ADMIN, MEMBER_ROLE_OPERATOR)

MEMBER_STATUS_ACTIVE = "active"
MEMBER_STATUS_REMOVED = "removed"
MEMBER_STATUSES = (MEMBER_STATUS_ACTIVE, MEMBER_STATUS_REMOVED)

GRANT_STATUS_ACTIVE = "active"
GRANT_STATUS_REVOKED = "revoked"
GRANT_STATUSES = (GRANT_STATUS_ACTIVE, GRANT_STATUS_REVOKED)

ASSIGNMENT_STATUS_ACTIVE = "active"
ASSIGNMENT_STATUS_REVOKED = "revoked"
ASSIGNMENT_STATUSES = (ASSIGNMENT_STATUS_ACTIVE, ASSIGNMENT_STATUS_REVOKED)

AUTHORIZATION_MODE_SELF = "self"
AUTHORIZATION_MODE_PLATFORM = "platform"
AUTHORIZATION_MODE_ORGANIZATION = "organization"
AUTHORIZATION_MODE_SERVICE = "service"
AUTHORIZATION_MODES = (
    AUTHORIZATION_MODE_SELF,
    AUTHORIZATION_MODE_PLATFORM,
    AUTHORIZATION_MODE_ORGANIZATION,
    AUTHORIZATION_MODE_SERVICE,
)

AUDIT_RESULT_SUCCESS = "success"
AUDIT_RESULT_DENIED = "denied"
AUDIT_RESULT_FAILED = "failed"
AUDIT_RESULTS = (AUDIT_RESULT_SUCCESS, AUDIT_RESULT_DENIED, AUDIT_RESULT_FAILED)


class OrganizationORM(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint(
            "id", "organization_type", name="uq_organizations_id_type"
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_organizations_status",
        ),
        Index("ix_organizations_type_status", "organization_type", "status"),
    )

    id = Column(Integer, primary_key=True)
    organization_type = Column(String(32), nullable=False)
    name = Column(String(255), nullable=False)
    contact_name = Column(String(128), nullable=True)
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(32), nullable=True)
    status = Column(
        String(16), nullable=False, default=ORGANIZATION_STATUS_ACTIVE,
        server_default=ORGANIZATION_STATUS_ACTIVE,
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    disabled_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    disabled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class OrganizationMemberORM(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "organization_type"],
            ["organizations.id", "organizations.organization_type"],
            name="fk_organization_members_organization",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "organization_id", "organization_type",
            name="uq_organization_members_id_scope",
        ),
        CheckConstraint(
            "member_role IN ('admin', 'operator')",
            name="ck_organization_members_role",
        ),
        CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_organization_members_status",
        ),
        Index(
            "uq_active_agency_member",
            "user_id",
            unique=True,
            postgresql_where=text(
                "status = 'active' AND organization_type = 'agency'"
            ),
            sqlite_where=text(
                "status = 'active' AND organization_type = 'agency'"
            ),
        ),
        Index(
            "ix_organization_members_org_status",
            "organization_id", "status",
        ),
        Index("ix_organization_members_user_status", "user_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, nullable=False)
    organization_type = Column(String(32), nullable=False)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    member_role = Column(String(16), nullable=False)
    status = Column(
        String(16), nullable=False, default=MEMBER_STATUS_ACTIVE,
        server_default=MEMBER_STATUS_ACTIVE,
    )
    joined_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    removed_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    joined_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    removed_at = Column(DateTime, nullable=True)
    removal_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class OrganizationGrantORM(Base):
    __tablename__ = "organization_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "organization_type"],
            ["organizations.id", "organizations.organization_type"],
            name="fk_organization_grants_organization",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "organization_id", "organization_type",
            name="uq_organization_grants_id_scope",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_organization_grants_status",
        ),
        Index(
            "uq_active_agency_enterprise_grant",
            "target_user_id",
            unique=True,
            postgresql_where=text(
                "status = 'active' AND organization_type = 'agency'"
            ),
            sqlite_where=text(
                "status = 'active' AND organization_type = 'agency'"
            ),
        ),
        Index(
            "ix_organization_grants_org_status",
            "organization_id", "status",
        ),
        Index(
            "ix_organization_grants_target_status",
            "target_user_id", "status",
        ),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, nullable=False)
    organization_type = Column(String(32), nullable=False)
    target_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status = Column(
        String(16), nullable=False, default=GRANT_STATUS_ACTIVE,
        server_default=GRANT_STATUS_ACTIVE,
    )
    granted_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    granted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)
    revocation_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class OrganizationMemberAssignmentORM(Base):
    __tablename__ = "organization_member_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["member_id", "organization_id", "organization_type"],
            [
                "organization_members.id",
                "organization_members.organization_id",
                "organization_members.organization_type",
            ],
            name="fk_assignments_member_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["grant_id", "organization_id", "organization_type"],
            [
                "organization_grants.id",
                "organization_grants.organization_id",
                "organization_grants.organization_type",
            ],
            name="fk_assignments_grant_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_organization_member_assignments_status",
        ),
        Index(
            "uq_active_organization_member_assignment",
            "member_id", "grant_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "ix_organization_assignments_org_status",
            "organization_id", "status",
        ),
        Index(
            "ix_organization_assignments_grant_status",
            "grant_id", "status",
        ),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, nullable=False)
    organization_type = Column(String(32), nullable=False)
    member_id = Column(Integer, nullable=False)
    grant_id = Column(Integer, nullable=False)
    status = Column(
        String(16), nullable=False, default=ASSIGNMENT_STATUS_ACTIVE,
        server_default=ASSIGNMENT_STATUS_ACTIVE,
    )
    assigned_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    assigned_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)
    revocation_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class OperationContextORM(Base):
    """Immutable operation context produced by one authorization decision."""

    __tablename__ = "operation_contexts"
    __table_args__ = (
        CheckConstraint(
            "authorization_mode IN ('self', 'platform', 'organization', 'service')",
            name="ck_operation_contexts_authorization_mode",
        ),
        Index("ix_operation_contexts_trace", "trace_id"),
        Index("ix_operation_contexts_actor_created", "actor_user_id", "created_at"),
        Index(
            "ix_operation_contexts_enterprise_created",
            "enterprise_user_id", "created_at",
        ),
        Index(
            "ix_operation_contexts_target",
            "target_resource_type", "target_resource_id",
        ),
    )

    id = Column(Integer, primary_key=True)
    trace_id = Column(String(64), nullable=False)
    actor_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    enterprise_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    authorization_mode = Column(String(16), nullable=False)
    action = Column(String(64), nullable=False)
    target_resource_type = Column(String(64), nullable=False)
    target_resource_id = Column(String(64), nullable=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    organization_member_id = Column(
        Integer,
        ForeignKey("organization_members.id", ondelete="RESTRICT"),
        nullable=True,
    )
    organization_grant_id = Column(
        Integer,
        ForeignKey("organization_grants.id", ondelete="RESTRICT"),
        nullable=True,
    )
    organization_member_assignment_id = Column(
        Integer,
        ForeignKey("organization_member_assignments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SystemAuditLogORM(Base):
    """Append-only system-level identity, authorization and key state events."""

    __tablename__ = "system_audit_logs"
    __table_args__ = (
        CheckConstraint(
            "result IN ('success', 'denied', 'failed')",
            name="ck_system_audit_logs_result",
        ),
        Index("ix_system_audit_logs_trace_created", "trace_id", "created_at"),
        Index("ix_system_audit_logs_actor_created", "actor_user_id", "created_at"),
        Index(
            "ix_system_audit_logs_enterprise_created",
            "enterprise_user_id", "created_at",
        ),
        Index("ix_system_audit_logs_object_created", "object_type", "object_id", "created_at"),
        Index("ix_system_audit_logs_organization_created", "organization_id", "created_at"),
        Index("ix_system_audit_logs_member_created", "organization_member_id", "created_at"),
        Index("ix_system_audit_logs_grant_created", "organization_grant_id", "created_at"),
        Index(
            "ix_system_audit_logs_assignment_created",
            "organization_member_assignment_id", "created_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"), nullable=True
    )
    trace_id = Column(String(64), nullable=False)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    organization_member_id = Column(
        Integer,
        ForeignKey("organization_members.id", ondelete="RESTRICT"),
        nullable=True,
    )
    organization_grant_id = Column(
        Integer,
        ForeignKey("organization_grants.id", ondelete="RESTRICT"),
        nullable=True,
    )
    organization_member_assignment_id = Column(
        Integer,
        ForeignKey("organization_member_assignments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    event_type = Column(String(64), nullable=False)
    actor_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    enterprise_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    action = Column(String(64), nullable=False)
    object_type = Column(String(64), nullable=False)
    object_id = Column(String(64), nullable=True)
    result = Column(String(16), nullable=False)
    reason = Column(Text, nullable=True)
    before_snapshot_json = Column(Text, nullable=True)
    after_snapshot_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def _reject_immutable_change(_mapper, _connection, target) -> None:
    raise ValueError(f"{target.__class__.__name__} is append-only")


for _immutable_model in (OperationContextORM, SystemAuditLogORM):
    event.listen(_immutable_model, "before_update", _reject_immutable_change)
    event.listen(_immutable_model, "before_delete", _reject_immutable_change)
