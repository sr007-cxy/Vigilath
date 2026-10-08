"""Sales Agent service layer: entity read/write: company/contact serialization, lead and task queries, outreach event persistence."""
from __future__ import annotations

from typing import Any
from sqlalchemy import case
from sqlalchemy.orm import Session
from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentEmailEventORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSmsDeliveryORM,
    SalesAgentSmsEventORM,
)

from agents.sales_agent.service.common import SalesAgentError, _dumps, session_has_table


def serialize_company(row: SalesAgentCompanyORM, contacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data = {
        "id": row.id,
        "name": row.name,
        "industry": row.industry,
        "region": row.region,
        "website": row.website,
        "employee_size": row.employee_size,
        "source": row.source,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if contacts is not None:
        data["contacts"] = contacts
        if contacts:
            primary_contact = contacts[0]
            data["email"] = primary_contact.get("email", "")
            data["phone"] = primary_contact.get("phone", "")
            data["contact_form_url"] = primary_contact.get("contact_form_url", "")
    return data


def serialize_contact(row: SalesAgentCompanyContactORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "contact_name": row.contact_name,
        "position": row.position,
        "email": row.email,
        "phone": row.phone,
        "contact_form_url": row.contact_form_url,
        "source": row.source,
        "source_url": row.source_url,
        "confidence": row.confidence,
        "is_reachable": row.is_reachable,
        "consent_status": row.consent_status,
        "captured_at": row.captured_at.isoformat() if row.captured_at else None,
    }


def _company_contact_summary(db: Session, company_id: int, limit: int = 3) -> list[dict[str, Any]]:
    rows = (
        db.query(SalesAgentCompanyContactORM)
        .filter(SalesAgentCompanyContactORM.company_id == int(company_id))
        .order_by(
            case((SalesAgentCompanyContactORM.email.ilike("%@%.%"), 1), else_=0).desc(),
            SalesAgentCompanyContactORM.is_reachable.desc(),
            SalesAgentCompanyContactORM.id.desc(),
        )
        .limit(max(1, limit))
        .all()
    )
    return [serialize_contact(row) for row in rows]


def _campaign_or_latest(
    db: Session,
    user_id: int,
    campaign_id: int | None = None,
    seller_profile_id: int | None = None,
) -> SalesAgentRecommendationCampaignORM | None:
    """A named campaign takes top priority; if none is named but a brand is, take the latest among that brand's tasks only.

    `seller_profile_id` was added later: without a brand restriction "latest task" is account-level,
    and brand A's session would read the industry/region brand B set last round. Returns None if this brand has no task yet;
    leaving it empty so the flow asks again beats recommending with another business line's conditions.
    """
    q = db.query(SalesAgentRecommendationCampaignORM).filter(
        SalesAgentRecommendationCampaignORM.user_id == user_id,
        SalesAgentRecommendationCampaignORM.status != "undone",
    )
    if campaign_id is not None:
        q = q.filter(SalesAgentRecommendationCampaignORM.id == int(campaign_id))
    elif seller_profile_id is not None:
        q = q.filter(SalesAgentRecommendationCampaignORM.seller_profile_id == int(seller_profile_id))
    return q.order_by(SalesAgentRecommendationCampaignORM.id.desc()).first()


def _lead_company(db: Session, user_id: int, lead_id: int) -> tuple[SalesAgentLeadRecommendationORM, SalesAgentCompanyORM]:
    lead = db.query(SalesAgentLeadRecommendationORM).filter(SalesAgentLeadRecommendationORM.id == int(lead_id), SalesAgentLeadRecommendationORM.user_id == user_id).first()
    if lead is None:
        raise SalesAgentError("线索不存在或不属于当前用户。", status_code=404)
    company = db.get(SalesAgentCompanyORM, lead.company_id)
    if company is None:
        raise SalesAgentError("线索关联企业不存在。", status_code=404)
    return lead, company


def _event_metadata(user_agent: str = "", ip_hash: str = "", **extra: Any) -> dict[str, Any]:
    data = {k: v for k, v in extra.items() if v not in (None, "", {})}
    if user_agent:
        data["user_agent"] = user_agent[:500]
    if ip_hash:
        data["ip_hash"] = ip_hash
    return data


def _record_email_event(
    db: Session,
    delivery: SalesAgentEmailDeliveryORM | None,
    user_id: int,
    event_type: str,
    metadata: dict[str, Any] | None = None,
    lead_id: int | None = None,
    company_id: int | None = None,
) -> SalesAgentEmailEventORM | None:
    if not session_has_table(db, SalesAgentEmailEventORM.__tablename__):
        # Some SQLite unit tests only create the delivery table to verify list/pixel logic; in production Alembic creates the event table.
        return None
    row = SalesAgentEmailEventORM(
        delivery_id=delivery.id if delivery else None,
        user_id=user_id,
        lead_id=lead_id if lead_id is not None else (delivery.lead_id if delivery else None),
        company_id=company_id if company_id is not None else (delivery.company_id if delivery else None),
        event_type=event_type,
        metadata_json=_dumps(metadata or {}),
    )
    db.add(row)
    return row


def _record_sms_event(
    db: Session,
    delivery: SalesAgentSmsDeliveryORM | None,
    user_id: int,
    event_type: str,
    metadata: dict[str, Any] | None = None,
    lead_id: int | None = None,
    company_id: int | None = None,
) -> SalesAgentSmsEventORM | None:
    """Persist SMS events. Isomorphic to `_record_email_event`: the two event tables are parallel and not merged."""
    if not session_has_table(db, SalesAgentSmsEventORM.__tablename__):
        # Same concession as email: unit tests that only create the delivery table should not blow up for lack of the event table.
        return None
    row = SalesAgentSmsEventORM(
        delivery_id=delivery.id if delivery else None,
        user_id=user_id,
        lead_id=lead_id if lead_id is not None else (delivery.lead_id if delivery else None),
        company_id=company_id if company_id is not None else (delivery.company_id if delivery else None),
        event_type=event_type,
        metadata_json=_dumps(metadata or {}),
    )
    db.add(row)
    return row
