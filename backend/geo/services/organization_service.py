"""Domain service for agency organization management.

All functions only ``flush``; the API caller commits or rolls back uniformly. Roles, memberships, authorization revocation,
assignment revocation and system audit are therefore in the same database transaction.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from geo.models.organization import (
    ASSIGNMENT_STATUS_ACTIVE,
    ASSIGNMENT_STATUS_REVOKED,
    GRANT_STATUS_ACTIVE,
    GRANT_STATUS_REVOKED,
    MEMBER_ROLE_ADMIN,
    MEMBER_ROLE_OPERATOR,
    MEMBER_STATUS_ACTIVE,
    MEMBER_STATUS_REMOVED,
    ORGANIZATION_STATUS_ACTIVE,
    ORGANIZATION_STATUS_DISABLED,
    ORGANIZATION_TYPE_AGENCY,
    OrganizationGrantORM,
    OrganizationMemberAssignmentORM,
    OrganizationMemberORM,
    OrganizationORM,
)
from geo.models.user import (
    ROLE_AGENCY_ADMIN,
    ROLE_AGENCY_OPERATOR,
    ROLE_USER,
    UserORM,
)
from geo.services.system_audit import append_event
from geo.utils.error_handler import AppException


AGENCY_NOT_FOUND = "AGENCY_NOT_FOUND"
AGENCY_CONFLICT = "AGENCY_CONFLICT"
AGENCY_MEMBER_NOT_ELIGIBLE = "AGENCY_MEMBER_NOT_ELIGIBLE"
AGENCY_LAST_ADMIN = "AGENCY_LAST_ADMIN"
AGENCY_SELF_REMOVAL = "AGENCY_SELF_REMOVAL"
AGENCY_GRANT_CONFLICT = "AGENCY_GRANT_CONFLICT"
AGENCY_ASSIGNMENT_CONFLICT = "AGENCY_ASSIGNMENT_CONFLICT"

_ROLE_BY_MEMBER_ROLE = {
    MEMBER_ROLE_ADMIN: ROLE_AGENCY_ADMIN,
    MEMBER_ROLE_OPERATOR: ROLE_AGENCY_OPERATOR,
}


def _error(status: int, key: str, message: str) -> AppException:
    return AppException(status_code=status, key=key, message=message)


def _agency(db: Session, organization_id: int, *, lock: bool = False) -> OrganizationORM:
    query = db.query(OrganizationORM).filter(
        OrganizationORM.id == organization_id,
        OrganizationORM.organization_type == ORGANIZATION_TYPE_AGENCY,
    )
    if lock:
        query = query.with_for_update()
    row = query.one_or_none()
    if row is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency not found")
    return row


def _active_member(
    db: Session, organization_id: int, member_id: int, *, lock: bool = False
) -> OrganizationMemberORM:
    query = db.query(OrganizationMemberORM).filter(
        OrganizationMemberORM.id == member_id,
        OrganizationMemberORM.organization_id == organization_id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
    )
    if lock:
        query = query.with_for_update()
    row = query.one_or_none()
    if row is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency member not found")
    return row


def _event(
    db: Session,
    *,
    actor_user_id: int,
    organization_id: int,
    event_type: str,
    action: str,
    object_type: str,
    object_id: int,
    member_id: int | None = None,
    grant_id: int | None = None,
    assignment_id: int | None = None,
    enterprise_user_id: int | None = None,
    reason: str | None = None,
    before=None,
    after=None,
) -> None:
    append_event(
        db,
        event_type=event_type,
        action=action,
        object_type=object_type,
        object_id=object_id,
        result="success",
        trace_id=uuid4().hex,
        actor_user_id=actor_user_id,
        enterprise_user_id=enterprise_user_id,
        organization_id=organization_id,
        organization_member_id=member_id,
        organization_grant_id=grant_id,
        organization_member_assignment_id=assignment_id,
        reason=reason,
        before=before,
        after=after,
    )


def find_exact_user(
    db: Session, *, email: str | None = None, phone: str | None = None, lock: bool = False
) -> UserORM | None:
    if bool(email) == bool(phone):
        raise _error(422, AGENCY_MEMBER_NOT_ELIGIBLE, "Provide exactly one email or phone")
    query = db.query(UserORM)
    if email:
        query = query.filter(UserORM.email == email.strip().lower())
    else:
        query = query.filter(UserORM.phone == phone.strip())
    if lock:
        query = query.with_for_update()
    return query.one_or_none()


def create_agency(
    db: Session,
    *,
    actor_user_id: int,
    name: str,
    first_admin_user_id: int,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
) -> OrganizationORM:
    user = (
        db.query(UserORM).filter(UserORM.id == first_admin_user_id).with_for_update().one_or_none()
    )
    if user is None or not user.is_active or user.role != ROLE_USER:
        raise _error(409, AGENCY_MEMBER_NOT_ELIGIBLE, "User is not eligible for agency membership")
    if db.query(OrganizationMemberORM.id).filter(
        OrganizationMemberORM.user_id == user.id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
    ).first():
        raise _error(409, AGENCY_MEMBER_NOT_ELIGIBLE, "User is not eligible for agency membership")

    organization = OrganizationORM(
        organization_type=ORGANIZATION_TYPE_AGENCY,
        name=name.strip(),
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        created_by_user_id=actor_user_id,
    )
    db.add(organization)
    db.flush()
    member = OrganizationMemberORM(
        organization_id=organization.id,
        organization_type=ORGANIZATION_TYPE_AGENCY,
        user_id=user.id,
        member_role=MEMBER_ROLE_ADMIN,
        joined_by_user_id=actor_user_id,
    )
    user.role = ROLE_AGENCY_ADMIN
    user.is_admin = False
    db.add(member)
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization.id,
        event_type="organization.created", action="agency.create",
        object_type="organization", object_id=organization.id,
        after={"status": organization.status, "name": organization.name},
    )
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization.id,
        event_type="organization.member_added", action="agency.member.add",
        object_type="organization_member", object_id=member.id, member_id=member.id,
        after={"user_id": user.id, "member_role": MEMBER_ROLE_ADMIN},
    )
    return organization


def set_agency_status(
    db: Session, *, actor_user_id: int, organization_id: int, status: str
) -> OrganizationORM:
    if status not in {ORGANIZATION_STATUS_ACTIVE, ORGANIZATION_STATUS_DISABLED}:
        raise _error(422, AGENCY_CONFLICT, "Unsupported agency status")
    current = _agency(db, organization_id)
    if current.status == status:
        return current
    users: dict[int, UserORM] = {}
    if status == ORGANIZATION_STATUS_ACTIVE:
        # Identity restoration and worker revalidation both lock users first, then organization, avoiding reverse-order deadlocks.
        user_ids = [row[0] for row in db.query(OrganizationMemberORM.user_id).filter(
            OrganizationMemberORM.organization_id == organization_id,
            OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
        ).order_by(OrganizationMemberORM.user_id).all()]
        users = {u.id: u for u in db.query(UserORM).filter(
            UserORM.id.in_(user_ids)
        ).order_by(UserORM.id).with_for_update().all()} if user_ids else {}
    organization = _agency(db, organization_id, lock=True)
    if organization.status == status:
        return organization
    if status == ORGANIZATION_STATUS_ACTIVE:
        members = db.query(OrganizationMemberORM).filter(
            OrganizationMemberORM.organization_id == organization_id,
            OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
            OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
        ).order_by(OrganizationMemberORM.id).with_for_update().all()
        if not any(m.member_role == MEMBER_ROLE_ADMIN for m in members) or any(
            users.get(m.user_id) is None
            or users[m.user_id].role != _ROLE_BY_MEMBER_ROLE[m.member_role]
            for m in members
        ):
            raise _error(409, AGENCY_CONFLICT, "Agency membership must be repaired before activation")
        organization.disabled_by_user_id = None
        organization.disabled_at = None
    else:
        organization.disabled_by_user_id = actor_user_id
        organization.disabled_at = datetime.utcnow()
    before = organization.status
    organization.status = status
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization.id,
        event_type=f"organization.{status}", action="agency.status.update",
        object_type="organization", object_id=organization.id,
        before={"status": before}, after={"status": status},
    )
    return organization


def add_member(
    db: Session,
    *,
    actor_user_id: int,
    organization_id: int,
    user_id: int,
    member_role: str,
) -> OrganizationMemberORM:
    if member_role not in _ROLE_BY_MEMBER_ROLE:
        raise _error(422, AGENCY_MEMBER_NOT_ELIGIBLE, "Unsupported agency member role")
    user = db.query(UserORM).filter(UserORM.id == user_id).with_for_update().one_or_none()
    if user is None or not user.is_active or user.role != ROLE_USER:
        raise _error(404, AGENCY_MEMBER_NOT_ELIGIBLE, "User is not eligible for agency membership")
    organization = _agency(db, organization_id, lock=True)
    if organization.status != ORGANIZATION_STATUS_ACTIVE:
        raise _error(409, AGENCY_CONFLICT, "Agency is disabled")
    if db.query(OrganizationMemberORM.id).filter(
        OrganizationMemberORM.user_id == user.id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
    ).first():
        raise _error(404, AGENCY_MEMBER_NOT_ELIGIBLE, "User is not eligible for agency membership")
    member = OrganizationMemberORM(
        organization_id=organization_id,
        organization_type=ORGANIZATION_TYPE_AGENCY,
        user_id=user.id,
        member_role=member_role,
        joined_by_user_id=actor_user_id,
    )
    user.role = _ROLE_BY_MEMBER_ROLE[member_role]
    user.is_admin = False
    db.add(member)
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization_id,
        event_type="organization.member_added", action="agency.member.add",
        object_type="organization_member", object_id=member.id, member_id=member.id,
        after={"user_id": user.id, "member_role": member_role},
    )
    return member


def add_member_by_identifier(
    db: Session, *, actor_user_id: int, organization_id: int,
    email: str | None = None, phone: str | None = None,
    member_role: str = MEMBER_ROLE_OPERATOR,
) -> OrganizationMemberORM:
    user = find_exact_user(db, email=email, phone=phone)
    if user is None:
        raise _error(404, AGENCY_MEMBER_NOT_ELIGIBLE, "User is not eligible for agency membership")
    return add_member(
        db, actor_user_id=actor_user_id, organization_id=organization_id,
        user_id=user.id, member_role=member_role,
    )


def change_member_role(
    db: Session, *, actor_user_id: int, organization_id: int,
    member_id: int, member_role: str,
) -> OrganizationMemberORM:
    if member_role not in _ROLE_BY_MEMBER_ROLE:
        raise _error(422, AGENCY_MEMBER_NOT_ELIGIBLE, "Unsupported agency member role")
    candidate = db.query(OrganizationMemberORM).filter(
        OrganizationMemberORM.id == member_id,
        OrganizationMemberORM.organization_id == organization_id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
    ).one_or_none()
    if candidate is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency member not found")
    user = db.query(UserORM).filter(UserORM.id == candidate.user_id).with_for_update().one()
    _agency(db, organization_id, lock=True)
    members = db.query(OrganizationMemberORM).filter(
        OrganizationMemberORM.organization_id == organization_id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
    ).order_by(OrganizationMemberORM.id).with_for_update().all()
    member = next((m for m in members if m.id == member_id), None)
    if member is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency member not found")
    if member.member_role == MEMBER_ROLE_ADMIN and member_role != MEMBER_ROLE_ADMIN:
        if sum(m.member_role == MEMBER_ROLE_ADMIN for m in members) <= 1:
            raise _error(409, AGENCY_LAST_ADMIN, "Agency must keep at least one active admin")
    if member.user_id != user.id:
        raise _error(409, AGENCY_CONFLICT, "Agency membership changed concurrently")
    before = member.member_role
    member.member_role = member_role
    user.role = _ROLE_BY_MEMBER_ROLE[member_role]
    user.is_admin = False
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization_id,
        event_type="organization.member_role_changed", action="agency.member.role.update",
        object_type="organization_member", object_id=member.id, member_id=member.id,
        before={"member_role": before}, after={"member_role": member_role},
    )
    return member


def remove_member(
    db: Session, *, actor_user_id: int, organization_id: int, member_id: int,
    reason: str | None = None, allow_self: bool = False,
) -> OrganizationMemberORM:
    candidate = db.query(OrganizationMemberORM).filter(
        OrganizationMemberORM.id == member_id,
        OrganizationMemberORM.organization_id == organization_id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
    ).one_or_none()
    if candidate is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency member not found")
    user = db.query(UserORM).filter(UserORM.id == candidate.user_id).with_for_update().one()
    _agency(db, organization_id, lock=True)
    members = db.query(OrganizationMemberORM).filter(
        OrganizationMemberORM.organization_id == organization_id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
    ).order_by(OrganizationMemberORM.id).with_for_update().all()
    member = next((m for m in members if m.id == member_id), None)
    if member is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency member not found")
    if member.user_id == actor_user_id and not allow_self:
        raise _error(409, AGENCY_SELF_REMOVAL, "Agency admin cannot remove self")
    if member.member_role == MEMBER_ROLE_ADMIN and sum(
        m.member_role == MEMBER_ROLE_ADMIN for m in members
    ) <= 1:
        raise _error(409, AGENCY_LAST_ADMIN, "Agency must keep at least one active admin")
    assignments = db.query(OrganizationMemberAssignmentORM).filter(
        OrganizationMemberAssignmentORM.organization_id == organization_id,
        OrganizationMemberAssignmentORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberAssignmentORM.member_id == member.id,
        OrganizationMemberAssignmentORM.status == ASSIGNMENT_STATUS_ACTIVE,
    ).order_by(OrganizationMemberAssignmentORM.id).with_for_update().all()
    if member.user_id != user.id:
        raise _error(409, AGENCY_CONFLICT, "Agency membership changed concurrently")
    now = datetime.utcnow()
    for assignment in assignments:
        assignment.status = ASSIGNMENT_STATUS_REVOKED
        assignment.revoked_by_user_id = actor_user_id
        assignment.revoked_at = now
        assignment.revocation_reason = "member_removed"
    member.status = MEMBER_STATUS_REMOVED
    member.removed_by_user_id = actor_user_id
    member.removed_at = now
    member.removal_reason = reason
    user.role = ROLE_USER
    user.is_admin = False
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization_id,
        event_type="organization.member_removed", action="agency.member.remove",
        object_type="organization_member", object_id=member.id, member_id=member.id,
        reason=reason, before={"status": MEMBER_STATUS_ACTIVE, "member_role": member.member_role},
        after={"status": MEMBER_STATUS_REMOVED, "revoked_assignments": len(assignments)},
    )
    return member


def grant_enterprise(
    db: Session, *, actor_user_id: int, organization_id: int, enterprise_user_id: int
) -> OrganizationGrantORM:
    # The enterprise row is the shared lock contended across organizations, ensuring grant creation for the same enterprise is serialized.
    enterprise = db.query(UserORM).filter(
        UserORM.id == enterprise_user_id
    ).with_for_update().one_or_none()
    if enterprise is None or not enterprise.is_active or enterprise.role != ROLE_USER:
        raise _error(409, AGENCY_GRANT_CONFLICT, "Enterprise is not eligible for agency grant")
    organization = _agency(db, organization_id, lock=True)
    if organization.status != ORGANIZATION_STATUS_ACTIVE:
        raise _error(409, AGENCY_CONFLICT, "Agency is disabled")
    if db.query(OrganizationGrantORM.id).filter(
        OrganizationGrantORM.target_user_id == enterprise_user_id,
        OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
    ).first():
        raise _error(409, AGENCY_GRANT_CONFLICT, "Enterprise already has an active agency grant")
    grant = OrganizationGrantORM(
        organization_id=organization_id,
        organization_type=ORGANIZATION_TYPE_AGENCY,
        target_user_id=enterprise_user_id,
        granted_by_user_id=actor_user_id,
    )
    db.add(grant)
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization_id,
        event_type="organization.grant_created", action="agency.grant.create",
        object_type="organization_grant", object_id=grant.id, grant_id=grant.id,
        enterprise_user_id=enterprise_user_id,
        after={"status": GRANT_STATUS_ACTIVE},
    )
    return grant


def revoke_grant(
    db: Session, *, actor_user_id: int, organization_id: int, grant_id: int,
    reason: str | None = None,
) -> OrganizationGrantORM:
    _agency(db, organization_id, lock=True)
    grant = db.query(OrganizationGrantORM).filter(
        OrganizationGrantORM.id == grant_id,
        OrganizationGrantORM.organization_id == organization_id,
        OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
    ).with_for_update().one_or_none()
    if grant is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency grant not found")
    assignments = db.query(OrganizationMemberAssignmentORM).filter(
        OrganizationMemberAssignmentORM.organization_id == organization_id,
        OrganizationMemberAssignmentORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberAssignmentORM.grant_id == grant.id,
        OrganizationMemberAssignmentORM.status == ASSIGNMENT_STATUS_ACTIVE,
    ).order_by(OrganizationMemberAssignmentORM.id).with_for_update().all()
    now = datetime.utcnow()
    for assignment in assignments:
        assignment.status = ASSIGNMENT_STATUS_REVOKED
        assignment.revoked_by_user_id = actor_user_id
        assignment.revoked_at = now
        assignment.revocation_reason = "grant_revoked"
    grant.status = GRANT_STATUS_REVOKED
    grant.revoked_by_user_id = actor_user_id
    grant.revoked_at = now
    grant.revocation_reason = reason
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization_id,
        event_type="organization.grant_revoked", action="agency.grant.revoke",
        object_type="organization_grant", object_id=grant.id, grant_id=grant.id,
        enterprise_user_id=grant.target_user_id, reason=reason,
        before={"status": GRANT_STATUS_ACTIVE},
        after={"status": GRANT_STATUS_REVOKED, "revoked_assignments": len(assignments)},
    )
    return grant


def assign_enterprise(
    db: Session, *, actor_user_id: int, organization_id: int,
    member_id: int, grant_id: int,
) -> OrganizationMemberAssignmentORM:
    organization = _agency(db, organization_id, lock=True)
    if organization.status != ORGANIZATION_STATUS_ACTIVE:
        raise _error(409, AGENCY_CONFLICT, "Agency is disabled")
    member = _active_member(db, organization_id, member_id, lock=True)
    if member.member_role != MEMBER_ROLE_OPERATOR:
        raise _error(409, AGENCY_ASSIGNMENT_CONFLICT, "Only active agency operators can be assigned")
    grant = db.query(OrganizationGrantORM).filter(
        OrganizationGrantORM.id == grant_id,
        OrganizationGrantORM.organization_id == organization_id,
        OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
    ).with_for_update().one_or_none()
    if grant is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency grant not found")
    if db.query(OrganizationMemberAssignmentORM.id).filter(
        OrganizationMemberAssignmentORM.member_id == member_id,
        OrganizationMemberAssignmentORM.grant_id == grant_id,
        OrganizationMemberAssignmentORM.status == ASSIGNMENT_STATUS_ACTIVE,
    ).first():
        raise _error(409, AGENCY_ASSIGNMENT_CONFLICT, "Enterprise is already assigned to member")
    assignment = OrganizationMemberAssignmentORM(
        organization_id=organization_id,
        organization_type=ORGANIZATION_TYPE_AGENCY,
        member_id=member_id,
        grant_id=grant_id,
        assigned_by_user_id=actor_user_id,
    )
    db.add(assignment)
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization_id,
        event_type="organization.assignment_created", action="agency.assignment.create",
        object_type="organization_member_assignment", object_id=assignment.id,
        member_id=member.id, grant_id=grant.id, assignment_id=assignment.id,
        enterprise_user_id=grant.target_user_id,
        after={"status": ASSIGNMENT_STATUS_ACTIVE},
    )
    return assignment


def revoke_assignment(
    db: Session, *, actor_user_id: int, organization_id: int, assignment_id: int,
    reason: str | None = None,
) -> OrganizationMemberAssignmentORM:
    _agency(db, organization_id, lock=True)
    candidate = db.query(OrganizationMemberAssignmentORM).filter(
        OrganizationMemberAssignmentORM.id == assignment_id,
        OrganizationMemberAssignmentORM.organization_id == organization_id,
        OrganizationMemberAssignmentORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberAssignmentORM.status == ASSIGNMENT_STATUS_ACTIVE,
    ).one_or_none()
    if candidate is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency assignment not found")
    # Shares the organization -> member -> grant -> assignment lock order with pre-execution revalidation.
    _active_member(db, organization_id, candidate.member_id, lock=True)
    grant = db.query(OrganizationGrantORM).filter(
        OrganizationGrantORM.id == candidate.grant_id
    ).with_for_update().one()
    assignment = db.query(OrganizationMemberAssignmentORM).filter(
        OrganizationMemberAssignmentORM.id == assignment_id,
        OrganizationMemberAssignmentORM.status == ASSIGNMENT_STATUS_ACTIVE,
    ).with_for_update().one_or_none()
    if assignment is None:
        raise _error(404, AGENCY_NOT_FOUND, "Agency assignment not found")
    assignment.status = ASSIGNMENT_STATUS_REVOKED
    assignment.revoked_by_user_id = actor_user_id
    assignment.revoked_at = datetime.utcnow()
    assignment.revocation_reason = reason
    db.flush()
    _event(
        db, actor_user_id=actor_user_id, organization_id=organization_id,
        event_type="organization.assignment_revoked", action="agency.assignment.revoke",
        object_type="organization_member_assignment", object_id=assignment.id,
        member_id=assignment.member_id, grant_id=assignment.grant_id,
        assignment_id=assignment.id, enterprise_user_id=grant.target_user_id,
        reason=reason, before={"status": ASSIGNMENT_STATUS_ACTIVE},
        after={"status": ASSIGNMENT_STATUS_REVOKED},
    )
    return assignment
