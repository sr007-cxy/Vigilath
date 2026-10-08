"""Tenant portal (self-service queries after role=tenant logs into the web).

Endpoints (prefix /gateway-portal):
- GET /gateway-portal/overview — balance + per-engine usage/remaining today + ledger summary
- GET /gateway-portal/jobs     — call record stream of one's own tenant (paged/filtered)

Implementation = reverse proxy to telemetry-service /v1/admin/* (X-Admin-Token is held only server-side);
tenant_id is forcibly injected by the server from the logged-in account's binding, and client parameters are ignored — this is the key line
for tenant isolation.

The binding is read from api_tenants.user_id (a telemetry-service table). The two services currently share a DB,
so this is read-only raw SQL; if the DB is split in the future, switch to telemetry's query API.
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from geo.api.auth import _user_role, get_current_user
from geo.api.ai_telemetry import TELEMETRY_SERVICE_URL, _gateway_headers, get_db
from geo.models.user import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/gateway-portal")


def require_tenant_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> int:
    """Check role=tenant with a bound active tenant, return tenant_id."""
    if _user_role(current_user) != "tenant":
        raise HTTPException(403, "仅限租户账号访问")
    try:
        row = db.execute(
            text("SELECT id FROM api_tenants WHERE user_id = :uid AND status = 'active'"),
            {"uid": current_user.id},
        ).first()
    except Exception:
        log.warning("query api_tenants failed", exc_info=True)
        raise HTTPException(503, "租户服务暂不可用")
    if row is None:
        raise HTTPException(403, "账号未绑定租户,请联系管理员")
    return int(row[0])


@router.get("/overview")
async def portal_overview(tenant_id: int = Depends(require_tenant_user)):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{TELEMETRY_SERVICE_URL}/v1/admin/tenants/{tenant_id}/usage",
            headers=_gateway_headers())
        r.raise_for_status()
        return r.json()


@router.get("/jobs")
async def portal_jobs(
    limit: int = 50,
    offset: int = 0,
    engine: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    tenant_id: int = Depends(require_tenant_user),
):
    # tenant_id comes from the logged-in account's binding, client parameters not accepted — the key isolation line
    params: dict = {"tenant_id": tenant_id, "limit": limit, "offset": offset}
    if engine:
        params["engine"] = engine
    if status:
        params["status"] = status
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{TELEMETRY_SERVICE_URL}/v1/admin/jobs",
                             params=params, headers=_gateway_headers())
        r.raise_for_status()
        return r.json()
