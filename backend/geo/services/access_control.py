"""Unified authorization core for enterprise resources.

This only resolves the data scope of "who can access which enterprise"; it is not bound to specific APIs. Resource endpoints must first
resolve the owning enterprise from topic/doc/task/account objects, then call this module; never add agency roles to the platform
``admin/operator`` allowlist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from sqlalchemy import false
from sqlalchemy.orm import Query, Session

from geo.models.organization import (
    ASSIGNMENT_STATUS_ACTIVE,
    GRANT_STATUS_ACTIVE,
    MEMBER_ROLE_ADMIN,
    MEMBER_ROLE_OPERATOR,
    MEMBER_STATUS_ACTIVE,
    ORGANIZATION_STATUS_ACTIVE,
    ORGANIZATION_TYPE_AGENCY,
    OrganizationGrantORM,
    OrganizationMemberAssignmentORM,
    OrganizationMemberORM,
    OrganizationORM,
    OperationContextORM,
)
from geo.models.user import (
    ROLE_ADMIN,
    ROLE_AGENCY_ADMIN,
    ROLE_AGENCY_OPERATOR,
    ROLE_OPERATOR,
    ROLE_USER,
    UserORM,
)
from geo.utils.error_handler import AppException


RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
ENTERPRISE_READ_ONLY = "ENTERPRISE_READ_ONLY"

ACTION_READ = "read"
ACTION_WRITE = "write"

AUTHORIZATION_SELF = "self"
AUTHORIZATION_PLATFORM = "platform"
AUTHORIZATION_ORGANIZATION = "organization"

_PLATFORM_BUSINESS_ROLES = (ROLE_ADMIN, ROLE_OPERATOR)
_AGENCY_MEMBER_ROLE = {
    ROLE_AGENCY_ADMIN: MEMBER_ROLE_ADMIN,
    ROLE_AGENCY_OPERATOR: MEMBER_ROLE_OPERATOR,
}


@dataclass(frozen=True)
class ActorContext:
    actor_user_id: int
    role: str
    organization_id: Optional[int] = None
    organization_member_id: Optional[int] = None
    member_role: Optional[str] = None
    valid: bool = True


@dataclass(frozen=True)
class EnterpriseContext:
    actor_user_id: int
    enterprise_user_id: int
    organization_id: Optional[int]
    organization_member_id: Optional[int]
    organization_grant_id: Optional[int]
    organization_member_assignment_id: Optional[int]
    authorization_mode: str
    is_delegated: bool
    can_read: bool
    can_write: bool
    can_manage_members: bool


def _role(actor) -> str:
    role = getattr(actor, "role", None)
    if role:
        return role
    return ROLE_ADMIN if getattr(actor, "is_admin", False) else ROLE_USER


def resource_not_found() -> AppException:
    return AppException(
        status_code=404,
        key=RESOURCE_NOT_FOUND,
        message="Resource not found",
    )


def enterprise_read_only() -> AppException:
    return AppException(
        status_code=403,
        key=ENTERPRISE_READ_ONLY,
        message="Enterprise is read-only while agency authorization is active",
    )


def resolve_actor_context(db: Session, actor) -> ActorContext:
    """Resolve the request principal; the three identity fields of an agency role must match exactly."""
    actor_id = int(actor.id)
    role = _role(actor)
    if role not in _AGENCY_MEMBER_ROLE:
        return ActorContext(actor_user_id=actor_id, role=role)

    rows = (
        db.query(OrganizationMemberORM)
        .join(
            OrganizationORM,
            (OrganizationORM.id == OrganizationMemberORM.organization_id)
            & (
                OrganizationORM.organization_type
                == OrganizationMemberORM.organization_type
            ),
        )
        .filter(
            OrganizationMemberORM.user_id == actor_id,
            OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
            OrganizationORM.status == ORGANIZATION_STATUS_ACTIVE,
        )
        .all()
    )
    if len(rows) != 1:
        return ActorContext(actor_user_id=actor_id, role=role, valid=False)

    member = rows[0]
    expected_member_role = _AGENCY_MEMBER_ROLE[role]
    if member.member_role != expected_member_role:
        return ActorContext(actor_user_id=actor_id, role=role, valid=False)

    return ActorContext(
        actor_user_id=actor_id,
        role=role,
        organization_id=member.organization_id,
        organization_member_id=member.id,
        member_role=member.member_role,
    )


def accessible_enterprise_user_ids(
    db: Session, actor, *, actor_context: ActorContext | None = None
) -> Optional[set[int]]:
    """Return visible enterprise IDs; ``None`` means platform business roles need no filtering."""
    context = actor_context or resolve_actor_context(db, actor)
    if context.role in _PLATFORM_BUSINESS_ROLES:
        return None
    if context.role == ROLE_USER:
        return {context.actor_user_id}
    if context.role not in _AGENCY_MEMBER_ROLE or not context.valid:
        return set()

    grants = db.query(OrganizationGrantORM).filter(
        OrganizationGrantORM.organization_id == context.organization_id,
        OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
    )
    if context.role == ROLE_AGENCY_ADMIN:
        return {row.target_user_id for row in grants.all()}

    rows = (
        grants.join(
            OrganizationMemberAssignmentORM,
            OrganizationMemberAssignmentORM.grant_id == OrganizationGrantORM.id,
        )
        .filter(
            OrganizationMemberAssignmentORM.organization_id
            == context.organization_id,
            OrganizationMemberAssignmentORM.organization_type
            == ORGANIZATION_TYPE_AGENCY,
            OrganizationMemberAssignmentORM.member_id
            == context.organization_member_id,
            OrganizationMemberAssignmentORM.status == ASSIGNMENT_STATUS_ACTIVE,
        )
        .all()
    )
    return {row.target_user_id for row in rows}


def apply_enterprise_scope(
    query: Query,
    db: Session,
    actor,
    owner_column,
    enterprise_user_id: int | None = None,
) -> Query:
    """Append the unified enterprise scope to an ORM list query.

    enterprise_user_id is the enterprise context currently selected in the agency workspace. It only narrows the existing
    accessible scope to a single enterprise; when omitted, the original role-scope semantics are fully preserved.
    """
    enterprise_ids = accessible_enterprise_user_ids(db, actor)
    if enterprise_ids is None:
        return query if enterprise_user_id is None else query.filter(owner_column == enterprise_user_id)
    if not enterprise_ids:
        return query.filter(false())
    if enterprise_user_id is not None:
        if enterprise_user_id not in enterprise_ids:
            return query.filter(false())
        return query.filter(owner_column == enterprise_user_id)
    return query.filter(owner_column.in_(enterprise_ids))


def _active_agency_grant(
    db: Session, organization_id: int, enterprise_user_id: int
) -> Optional[OrganizationGrantORM]:
    return (
        db.query(OrganizationGrantORM)
        .join(
            OrganizationORM,
            (OrganizationORM.id == OrganizationGrantORM.organization_id)
            & (
                OrganizationORM.organization_type
                == OrganizationGrantORM.organization_type
            ),
        )
        .filter(
            OrganizationGrantORM.organization_id == organization_id,
            OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            OrganizationGrantORM.target_user_id == enterprise_user_id,
            OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
            OrganizationORM.status == ORGANIZATION_STATUS_ACTIVE,
        )
        .one_or_none()
    )


def _enterprise_has_active_agency_grant(
    db: Session, enterprise_user_id: int
) -> bool:
    return (
        db.query(OrganizationGrantORM.id)
        .join(
            OrganizationORM,
            (OrganizationORM.id == OrganizationGrantORM.organization_id)
            & (
                OrganizationORM.organization_type
                == OrganizationGrantORM.organization_type
            ),
        )
        .filter(
            OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            OrganizationGrantORM.target_user_id == enterprise_user_id,
            OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
            OrganizationORM.status == ORGANIZATION_STATUS_ACTIVE,
        )
        .first()
        is not None
    )


def require_enterprise_access(
    db: Session,
    actor,
    enterprise_user_id: int,
    action: str = ACTION_READ,
) -> EnterpriseContext:
    """Require the principal to have access to the enterprise; all hidden denials return a uniform 404."""
    if action not in (ACTION_READ, ACTION_WRITE):
        raise ValueError(f"unsupported enterprise action: {action}")
    if db.get(UserORM, enterprise_user_id) is None:
        raise resource_not_found()

    context = resolve_actor_context(db, actor)
    if context.role in _PLATFORM_BUSINESS_ROLES:
        return EnterpriseContext(
            actor_user_id=context.actor_user_id,
            enterprise_user_id=enterprise_user_id,
            organization_id=None,
            organization_member_id=None,
            organization_grant_id=None,
            organization_member_assignment_id=None,
            authorization_mode=AUTHORIZATION_PLATFORM,
            is_delegated=False,
            can_read=True,
            can_write=True,
            can_manage_members=False,
        )

    if context.role == ROLE_USER and context.actor_user_id == enterprise_user_id:
        can_write = not _enterprise_has_active_agency_grant(db, enterprise_user_id)
        if action == ACTION_WRITE and not can_write:
            raise enterprise_read_only()
        return EnterpriseContext(
            actor_user_id=context.actor_user_id,
            enterprise_user_id=enterprise_user_id,
            organization_id=None,
            organization_member_id=None,
            organization_grant_id=None,
            organization_member_assignment_id=None,
            authorization_mode=AUTHORIZATION_SELF,
            is_delegated=False,
            can_read=True,
            can_write=can_write,
            can_manage_members=False,
        )

    if context.role not in _AGENCY_MEMBER_ROLE or not context.valid:
        raise resource_not_found()

    grant = _active_agency_grant(
        db, int(context.organization_id), int(enterprise_user_id)
    )
    if grant is None:
        raise resource_not_found()

    assignment_id = None
    if context.role == ROLE_AGENCY_OPERATOR:
        assignment = (
            db.query(OrganizationMemberAssignmentORM)
            .filter(
                OrganizationMemberAssignmentORM.organization_id
                == context.organization_id,
                OrganizationMemberAssignmentORM.organization_type
                == ORGANIZATION_TYPE_AGENCY,
                OrganizationMemberAssignmentORM.member_id
                == context.organization_member_id,
                OrganizationMemberAssignmentORM.grant_id == grant.id,
                OrganizationMemberAssignmentORM.status
                == ASSIGNMENT_STATUS_ACTIVE,
            )
            .one_or_none()
        )
        if assignment is None:
            raise resource_not_found()
        assignment_id = assignment.id

    return EnterpriseContext(
        actor_user_id=context.actor_user_id,
        enterprise_user_id=enterprise_user_id,
        organization_id=context.organization_id,
        organization_member_id=context.organization_member_id,
        organization_grant_id=grant.id,
        organization_member_assignment_id=assignment_id,
        authorization_mode=AUTHORIZATION_ORGANIZATION,
        is_delegated=True,
        can_read=True,
        can_write=True,
        can_manage_members=context.role == ROLE_AGENCY_ADMIN,
    )


def resource_enterprise_user_id(db: Session, resource) -> int:
    """Resolve the enterprise account owner from first-phase resource models."""
    for attr in ("owner_user_id", "user_id"):
        value = getattr(resource, attr, None)
        if value is not None:
            return int(value)

    topic_id = getattr(resource, "topic_id", None)
    if topic_id is not None:
        from geo.models.ai_telemetry import AiTelemetryTopicORM

        topic = db.get(AiTelemetryTopicORM, int(topic_id))
        if topic is not None:
            return int(topic.user_id)

    account_id = getattr(resource, "account_id", None)
    if account_id is not None:
        from geo.models.sentiment import SentimentAccountORM

        account = db.get(SentimentAccountORM, int(account_id))
        if account is not None:
            return int(account.user_id)

    raise resource_not_found()


def require_resource_access(
    db: Session,
    actor,
    resource,
    action: str = ACTION_READ,
) -> EnterpriseContext:
    return require_enterprise_access(
        db,
        actor,
        resource_enterprise_user_id(db, resource),
        action,
    )


def create_operation_context(
    db: Session,
    actor,
    enterprise_user_id: int,
    *,
    action: str,
    target_resource_type: str,
    target_resource_id: int | str | None = None,
    trace_id: str | None = None,
) -> tuple[EnterpriseContext, OperationContextORM]:
    """Validate write authorization and create an immutable operation context within the caller's transaction."""
    access = require_enterprise_access(db, actor, enterprise_user_id, ACTION_WRITE)
    row = OperationContextORM(
        trace_id=(trace_id or uuid4().hex),
        actor_user_id=access.actor_user_id,
        enterprise_user_id=access.enterprise_user_id,
        authorization_mode=access.authorization_mode,
        action=action[:64],
        target_resource_type=target_resource_type[:64],
        target_resource_id=(str(target_resource_id)[:64]
                            if target_resource_id is not None else None),
        organization_id=access.organization_id,
        organization_member_id=access.organization_member_id,
        organization_grant_id=access.organization_grant_id,
        organization_member_assignment_id=access.organization_member_assignment_id,
    )
    db.add(row)
    db.flush()
    return access, row


def create_resource_operation_context(
    db: Session,
    actor,
    resource,
    *,
    action: str,
    target_resource_type: str,
    target_resource_id: int | str | None = None,
    trace_id: str | None = None,
) -> tuple[EnterpriseContext, OperationContextORM]:
    return create_operation_context(
        db,
        actor,
        resource_enterprise_user_id(db, resource),
        action=action,
        target_resource_type=target_resource_type,
        target_resource_id=(target_resource_id
                            if target_resource_id is not None
                            else getattr(resource, "id", None)),
        trace_id=trace_id,
    )


def revalidate_operation_context(
    db: Session,
    operation_context_id: int,
) -> tuple[EnterpriseContext, OperationContextORM]:
    """Before an irreversible action, re-verify the creator's write authorization against current DB state."""
    # Lock order is fixed as context -> actor -> organization -> member -> grant -> assignment.
    # P4's revocation transaction must reuse the same order, so only one of "revocation committed" and "task enters execution" can happen first.
    row = (
        db.query(OperationContextORM)
        .filter(OperationContextORM.id == operation_context_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None or row.actor_user_id is None or row.enterprise_user_id is None:
        raise resource_not_found()
    actor = (
        db.query(UserORM)
        .filter(UserORM.id == row.actor_user_id)
        .with_for_update()
        .one_or_none()
    )
    if actor is None:
        raise resource_not_found()
    if row.authorization_mode == AUTHORIZATION_ORGANIZATION:
        (
            db.query(OrganizationORM)
            .filter(
                OrganizationORM.id == row.organization_id,
                OrganizationORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            )
            .with_for_update()
            .one_or_none()
        )
        (
            db.query(OrganizationMemberORM)
            .filter(OrganizationMemberORM.id == row.organization_member_id)
            .with_for_update()
            .one_or_none()
        )
        (
            db.query(OrganizationGrantORM)
            .filter(OrganizationGrantORM.id == row.organization_grant_id)
            .with_for_update()
            .one_or_none()
        )
        if row.organization_member_assignment_id is not None:
            (
                db.query(OrganizationMemberAssignmentORM)
                .filter(
                    OrganizationMemberAssignmentORM.id
                    == row.organization_member_assignment_id
                )
                .with_for_update()
                .one_or_none()
            )
    elif row.authorization_mode == AUTHORIZATION_SELF:
        # Enterprise self-writes and new agency grants must also be serialized; lock the current enterprise's grant rows.
        (
            db.query(OrganizationGrantORM)
            .filter(
                OrganizationGrantORM.target_user_id == row.enterprise_user_id,
                OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            )
            .order_by(OrganizationGrantORM.id)
            .with_for_update()
            .all()
        )
    access = require_enterprise_access(
        db, actor, row.enterprise_user_id, ACTION_WRITE,
    )
    if access.authorization_mode != row.authorization_mode:
        raise resource_not_found()
    if row.authorization_mode == AUTHORIZATION_ORGANIZATION:
        evidence = (
            access.organization_id,
            access.organization_member_id,
            access.organization_grant_id,
            access.organization_member_assignment_id,
        )
        snapshot = (
            row.organization_id,
            row.organization_member_id,
            row.organization_grant_id,
            row.organization_member_assignment_id,
        )
        if evidence != snapshot:
            raise resource_not_found()
    return access, row
