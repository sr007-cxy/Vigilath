"""P4 agency organization, member, enterprise authorization, assignment and audit API."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import get_current_user, require_admin, require_roles
from geo.database import settings
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
    SystemAuditLogORM,
)
from geo.models.user import ROLE_USER, UserORM
from geo.services.access_control import accessible_enterprise_user_ids, resolve_actor_context
from geo.services.organization_service import (
    add_member,
    add_member_by_identifier,
    assign_enterprise,
    change_member_role,
    create_agency,
    grant_enterprise,
    remove_member,
    revoke_assignment,
    revoke_grant,
    set_agency_status,
)
from geo.utils.error_handler import AppException


admin_router = APIRouter(prefix="/admin/agencies")
agency_router = APIRouter(prefix="/agency")
account_router = APIRouter(prefix="/account/agency")

require_platform_staff = require_roles("admin", "operator")
require_agency_actor = require_roles("agency_admin", "agency_operator")
require_agency_admin = require_roles("agency_admin")


def require_agency_enabled() -> None:
    if not settings.AGENCY_WORKBENCH_ENABLED:
        raise AppException(status_code=404, key="RESOURCE_NOT_FOUND", message="Resource not found")


class CreateAgencyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    first_admin_user_id: int
    contact_name: str | None = Field(default=None, max_length=128)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=32)


class AgencyStatusPayload(BaseModel):
    status: Literal["active", "disabled"]


class PlatformMemberPayload(BaseModel):
    user_id: int
    member_role: Literal["admin", "operator"] = "admin"


class MemberRolePayload(BaseModel):
    member_role: Literal["admin", "operator"]


class ExactMemberPayload(BaseModel):
    email: EmailStr | None = None
    phone: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def exactly_one_identifier(self):
        if bool(self.email) == bool(self.phone):
            raise ValueError("provide exactly one email or phone")
        return self


class GrantPayload(BaseModel):
    enterprise_user_id: int


class AssignmentPayload(BaseModel):
    member_id: int
    grant_id: int


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    visible = local[:1]
    return f"{visible}{'*' * max(2, min(6, len(local) - 1))}@{domain}"


def _mask_phone(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 7:
        return "*" * len(value)
    return f"{value[:3]}{'*' * (len(value) - 7)}{value[-4:]}"


def _safe_user(user: UserORM) -> dict:
    return {
        "id": user.id,
        "masked_email": _mask_email(user.email),
        "masked_phone": _mask_phone(user.phone),
        "display_name": user.name or user.wechat_nickname or _mask_email(user.email),
    }


def _organization(row: OrganizationORM) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "organization_type": row.organization_type,
        "status": row.status,
        "contact_name": row.contact_name,
        "contact_email": row.contact_email,
        "contact_phone": row.contact_phone,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _member(row: OrganizationMemberORM, user: UserORM) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "user": _safe_user(user),
        "member_role": row.member_role,
        "status": row.status,
        "joined_at": row.joined_at,
        "removed_at": row.removed_at,
    }


def _agency_context(db: Session, actor, *, admin_only: bool = False):
    context = resolve_actor_context(db, actor)
    if not context.valid or context.organization_id is None:
        raise AppException(status_code=404, key="RESOURCE_NOT_FOUND", message="Resource not found")
    if admin_only and context.member_role != MEMBER_ROLE_ADMIN:
        raise AppException(status_code=403, message="Agency admin only")
    return context


@admin_router.get("", dependencies=[Depends(require_agency_enabled)])
def list_agencies(
    status: Literal["active", "disabled"] | None = None,
    _actor=Depends(require_admin), db: Session = Depends(get_db),
):
    query = db.query(OrganizationORM).filter(
        OrganizationORM.organization_type == ORGANIZATION_TYPE_AGENCY
    )
    if status:
        query = query.filter(OrganizationORM.status == status)
    return [_organization(row) for row in query.order_by(OrganizationORM.id.desc()).all()]


@admin_router.get("/options", dependencies=[Depends(require_agency_enabled)])
def agency_options(
    _actor=Depends(require_platform_staff), db: Session = Depends(get_db),
):
    """Safe organization options for the platform authorization page; ops do not read members or contacts through this endpoint."""
    rows = db.query(
        OrganizationORM.id, OrganizationORM.name, OrganizationORM.status,
    ).filter(
        OrganizationORM.organization_type == ORGANIZATION_TYPE_AGENCY,
    ).order_by(OrganizationORM.name, OrganizationORM.id).all()
    return [{"id": row.id, "name": row.name, "status": row.status} for row in rows]


@admin_router.post("", status_code=201, dependencies=[Depends(require_agency_enabled)])
def create_agency_endpoint(
    payload: CreateAgencyPayload, actor=Depends(require_admin), db: Session = Depends(get_db),
):
    row = create_agency(
        db, actor_user_id=actor.id, name=payload.name,
        first_admin_user_id=payload.first_admin_user_id,
        contact_name=payload.contact_name,
        contact_email=str(payload.contact_email) if payload.contact_email else None,
        contact_phone=payload.contact_phone,
    )
    _commit(db)
    db.refresh(row)
    return _organization(row)


@admin_router.get("/{organization_id}", dependencies=[Depends(require_agency_enabled)])
def get_agency_endpoint(
    organization_id: int, _actor=Depends(require_admin), db: Session = Depends(get_db),
):
    row = db.query(OrganizationORM).filter(
        OrganizationORM.id == organization_id,
        OrganizationORM.organization_type == ORGANIZATION_TYPE_AGENCY,
    ).one_or_none()
    if row is None:
        raise AppException(status_code=404, key="AGENCY_NOT_FOUND", message="Agency not found")
    return _organization(row)


@admin_router.patch("/{organization_id}/status", dependencies=[Depends(require_agency_enabled)])
def set_agency_status_endpoint(
    organization_id: int, payload: AgencyStatusPayload,
    actor=Depends(require_admin), db: Session = Depends(get_db),
):
    row = set_agency_status(
        db, actor_user_id=actor.id, organization_id=organization_id, status=payload.status
    )
    _commit(db)
    return _organization(row)


@admin_router.get("/{organization_id}/members", dependencies=[Depends(require_agency_enabled)])
def platform_list_members(
    organization_id: int, _actor=Depends(require_admin), db: Session = Depends(get_db),
):
    rows = db.query(OrganizationMemberORM, UserORM).join(
        UserORM, UserORM.id == OrganizationMemberORM.user_id
    ).filter(
        OrganizationMemberORM.organization_id == organization_id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
    ).order_by(OrganizationMemberORM.id).all()
    return [_member(member, user) for member, user in rows]


@admin_router.post("/{organization_id}/members", status_code=201, dependencies=[Depends(require_agency_enabled)])
def platform_add_member(
    organization_id: int, payload: PlatformMemberPayload,
    actor=Depends(require_admin), db: Session = Depends(get_db),
):
    row = add_member(
        db, actor_user_id=actor.id, organization_id=organization_id,
        user_id=payload.user_id, member_role=payload.member_role,
    )
    _commit(db)
    user = db.get(UserORM, row.user_id)
    return _member(row, user)


@admin_router.patch("/{organization_id}/members/{member_id}", dependencies=[Depends(require_agency_enabled)])
def platform_change_member_role(
    organization_id: int, member_id: int, payload: MemberRolePayload,
    actor=Depends(require_admin), db: Session = Depends(get_db),
):
    row = change_member_role(
        db, actor_user_id=actor.id, organization_id=organization_id,
        member_id=member_id, member_role=payload.member_role,
    )
    _commit(db)
    return _member(row, db.get(UserORM, row.user_id))


@admin_router.delete("/{organization_id}/members/{member_id}", dependencies=[Depends(require_agency_enabled)])
def platform_remove_member(
    organization_id: int, member_id: int, reason: str | None = Query(default=None, max_length=500),
    actor=Depends(require_admin), db: Session = Depends(get_db),
):
    row = remove_member(
        db, actor_user_id=actor.id, organization_id=organization_id,
        member_id=member_id, reason=reason, allow_self=True,
    )
    _commit(db)
    return {"id": row.id, "status": row.status}


@admin_router.get("/{organization_id}/grants", dependencies=[Depends(require_agency_enabled)])
def platform_list_grants(
    organization_id: int, _actor=Depends(require_platform_staff), db: Session = Depends(get_db),
):
    rows = db.query(OrganizationGrantORM, UserORM).join(
        UserORM, UserORM.id == OrganizationGrantORM.target_user_id
    ).filter(
        OrganizationGrantORM.organization_id == organization_id,
        OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
    ).order_by(OrganizationGrantORM.id.desc()).all()
    return [{
        "id": grant.id, "organization_id": grant.organization_id,
        "enterprise": _safe_user(user), "status": grant.status,
        "granted_at": grant.granted_at, "revoked_at": grant.revoked_at,
    } for grant, user in rows]


@admin_router.post("/{organization_id}/grants", status_code=201, dependencies=[Depends(require_agency_enabled)])
def platform_grant_enterprise(
    organization_id: int, payload: GrantPayload,
    actor=Depends(require_platform_staff), db: Session = Depends(get_db),
):
    row = grant_enterprise(
        db, actor_user_id=actor.id, organization_id=organization_id,
        enterprise_user_id=payload.enterprise_user_id,
    )
    _commit(db)
    return {"id": row.id, "organization_id": row.organization_id,
            "enterprise_user_id": row.target_user_id, "status": row.status}


@admin_router.delete("/{organization_id}/grants/{grant_id}", dependencies=[Depends(require_agency_enabled)])
def platform_revoke_grant(
    organization_id: int, grant_id: int, reason: str | None = Query(default=None, max_length=500),
    actor=Depends(require_platform_staff), db: Session = Depends(get_db),
):
    row = revoke_grant(
        db, actor_user_id=actor.id, organization_id=organization_id,
        grant_id=grant_id, reason=reason,
    )
    _commit(db)
    return {"id": row.id, "status": row.status}


@admin_router.get("/{organization_id}/eligible-enterprises", dependencies=[Depends(require_agency_enabled)])
def eligible_enterprises(
    organization_id: int, email: str | None = None, phone: str | None = None,
    _actor=Depends(require_platform_staff), db: Session = Depends(get_db),
):
    if bool(email) == bool(phone):
        raise AppException(status_code=422, message="Provide exactly one email or phone")
    if db.query(OrganizationORM.id).filter(
        OrganizationORM.id == organization_id,
        OrganizationORM.organization_type == ORGANIZATION_TYPE_AGENCY,
    ).first() is None:
        raise AppException(status_code=404, key="AGENCY_NOT_FOUND", message="Agency not found")
    query = db.query(UserORM).filter(UserORM.role == ROLE_USER, UserORM.is_active == True)  # noqa: E712
    query = query.filter(UserORM.email == email.strip().lower()) if email else query.filter(UserORM.phone == phone.strip())
    query = query.filter(~db.query(OrganizationGrantORM.id).filter(
        OrganizationGrantORM.target_user_id == UserORM.id,
        OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
    ).exists())
    return [_safe_user(user) for user in query.limit(1).all()]


@admin_router.get("/{organization_id}/audit-logs", dependencies=[Depends(require_agency_enabled)])
def agency_audit_logs(
    organization_id: int, limit: int = Query(default=100, ge=1, le=500),
    _actor=Depends(require_platform_staff), db: Session = Depends(get_db),
):
    rows = db.query(SystemAuditLogORM).filter(
        SystemAuditLogORM.organization_id == organization_id
    ).order_by(SystemAuditLogORM.id.desc()).limit(limit).all()
    return [{
        "id": row.id, "trace_id": row.trace_id, "event_type": row.event_type,
        "actor_user_id": row.actor_user_id, "enterprise_user_id": row.enterprise_user_id,
        "action": row.action, "object_type": row.object_type, "object_id": row.object_id,
        "result": row.result, "reason": row.reason,
        "before": json.loads(row.before_snapshot_json) if row.before_snapshot_json else None,
        "after": json.loads(row.after_snapshot_json) if row.after_snapshot_json else None,
        "created_at": row.created_at,
    } for row in rows]


@agency_router.get("/me", dependencies=[Depends(require_agency_enabled)])
def agency_me(actor=Depends(require_agency_actor), db: Session = Depends(get_db)):
    context = _agency_context(db, actor)
    organization = db.get(OrganizationORM, context.organization_id)
    member = db.get(OrganizationMemberORM, context.organization_member_id)
    return {
        "organization": _organization(organization),
        "member": {"id": member.id, "member_role": member.member_role, "status": member.status},
        "capabilities": {
            "manage_members": context.member_role == MEMBER_ROLE_ADMIN,
            "manage_assignments": context.member_role == MEMBER_ROLE_ADMIN,
            "access_all_grants": context.member_role == MEMBER_ROLE_ADMIN,
        },
    }


@agency_router.get("/enterprises", dependencies=[Depends(require_agency_enabled)])
def agency_enterprises(actor=Depends(require_agency_actor), db: Session = Depends(get_db)):
    context = _agency_context(db, actor)
    enterprise_ids = accessible_enterprise_user_ids(db, actor, actor_context=context) or set()
    if not enterprise_ids:
        return []
    rows = db.query(OrganizationGrantORM, UserORM).join(
        UserORM, UserORM.id == OrganizationGrantORM.target_user_id
    ).filter(
        OrganizationGrantORM.organization_id == context.organization_id,
        OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
        OrganizationGrantORM.target_user_id.in_(enterprise_ids),
    ).order_by(OrganizationGrantORM.id).all()
    return [{"grant_id": grant.id, "enterprise": _safe_user(user), "status": grant.status}
            for grant, user in rows]


@agency_router.get("/members", dependencies=[Depends(require_agency_enabled)])
def agency_list_members(actor=Depends(require_agency_admin), db: Session = Depends(get_db)):
    context = _agency_context(db, actor, admin_only=True)
    rows = db.query(OrganizationMemberORM, UserORM).join(
        UserORM, UserORM.id == OrganizationMemberORM.user_id
    ).filter(
        OrganizationMemberORM.organization_id == context.organization_id,
        OrganizationMemberORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberORM.status == MEMBER_STATUS_ACTIVE,
    ).order_by(OrganizationMemberORM.id).all()
    return [_member(member, user) for member, user in rows]


@agency_router.post("/members", status_code=201, dependencies=[Depends(require_agency_enabled)])
def agency_add_operator(
    payload: ExactMemberPayload, actor=Depends(require_agency_admin), db: Session = Depends(get_db),
):
    context = _agency_context(db, actor, admin_only=True)
    row = add_member_by_identifier(
        db, actor_user_id=actor.id, organization_id=context.organization_id,
        email=str(payload.email).lower() if payload.email else None,
        phone=payload.phone, member_role=MEMBER_ROLE_OPERATOR,
    )
    _commit(db)
    return _member(row, db.get(UserORM, row.user_id))


@agency_router.delete("/members/{member_id}", dependencies=[Depends(require_agency_enabled)])
def agency_remove_member(
    member_id: int, reason: str | None = Query(default=None, max_length=500),
    actor=Depends(require_agency_admin), db: Session = Depends(get_db),
):
    context = _agency_context(db, actor, admin_only=True)
    row = remove_member(
        db, actor_user_id=actor.id, organization_id=context.organization_id,
        member_id=member_id, reason=reason,
    )
    _commit(db)
    return {"id": row.id, "status": row.status}


@agency_router.get("/assignments", dependencies=[Depends(require_agency_enabled)])
def agency_list_assignments(actor=Depends(require_agency_admin), db: Session = Depends(get_db)):
    context = _agency_context(db, actor, admin_only=True)
    rows = db.query(OrganizationMemberAssignmentORM).filter(
        OrganizationMemberAssignmentORM.organization_id == context.organization_id,
        OrganizationMemberAssignmentORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationMemberAssignmentORM.status == ASSIGNMENT_STATUS_ACTIVE,
    ).order_by(OrganizationMemberAssignmentORM.id).all()
    return [{"id": row.id, "member_id": row.member_id, "grant_id": row.grant_id,
             "status": row.status, "assigned_at": row.assigned_at} for row in rows]


@agency_router.post("/assignments", status_code=201, dependencies=[Depends(require_agency_enabled)])
def agency_assign_enterprise(
    payload: AssignmentPayload, actor=Depends(require_agency_admin), db: Session = Depends(get_db),
):
    context = _agency_context(db, actor, admin_only=True)
    row = assign_enterprise(
        db, actor_user_id=actor.id, organization_id=context.organization_id,
        member_id=payload.member_id, grant_id=payload.grant_id,
    )
    _commit(db)
    return {"id": row.id, "member_id": row.member_id, "grant_id": row.grant_id, "status": row.status}


@agency_router.delete("/assignments/{assignment_id}", dependencies=[Depends(require_agency_enabled)])
def agency_revoke_assignment(
    assignment_id: int, reason: str | None = Query(default=None, max_length=500),
    actor=Depends(require_agency_admin), db: Session = Depends(get_db),
):
    context = _agency_context(db, actor, admin_only=True)
    row = revoke_assignment(
        db, actor_user_id=actor.id, organization_id=context.organization_id,
        assignment_id=assignment_id, reason=reason,
    )
    _commit(db)
    return {"id": row.id, "status": row.status}


@account_router.get("", dependencies=[Depends(require_agency_enabled)])
def account_agency(actor=Depends(get_current_user), db: Session = Depends(get_db)):
    if getattr(actor, "role", ROLE_USER) != ROLE_USER:
        raise AppException(status_code=403, message="User account only")
    row = db.query(OrganizationGrantORM, OrganizationORM).join(
        OrganizationORM,
        (OrganizationORM.id == OrganizationGrantORM.organization_id)
        & (OrganizationORM.organization_type == OrganizationGrantORM.organization_type),
    ).filter(
        OrganizationGrantORM.target_user_id == actor.id,
        OrganizationGrantORM.organization_type == ORGANIZATION_TYPE_AGENCY,
        OrganizationGrantORM.status == GRANT_STATUS_ACTIVE,
        OrganizationORM.status == ORGANIZATION_STATUS_ACTIVE,
    ).one_or_none()
    if row is None:
        return {"agency": None, "grant": None}
    grant, organization = row
    return {
        "agency": {"id": organization.id, "name": organization.name, "status": organization.status},
        "grant": {"id": grant.id, "status": grant.status, "granted_at": grant.granted_at},
    }
