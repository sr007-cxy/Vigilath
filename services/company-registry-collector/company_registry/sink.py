"""Persist to DB: write collected Companies into the existing sales_agent_* tables.

Only reflects (autoload) existing tables and does INSERT/UPSERT; **it does not define or migrate the schema** -
the schema is owned by the backend's alembic (see CLAUDE.md). Target tables:
  - sales_agent_companies         company master table
  - sales_agent_company_contacts  contact details

Dedup: companies find-or-create by (name, region, source); contacts by (company_id, email, phone).

Note: reflected tables do not carry the Python-side default=datetime.utcnow from the backend ORM,
so all NOT NULL columns (including created_at/updated_at) are assigned explicitly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import MetaData, Table, create_engine, insert, select, update

from .models import Company

COMPANIES_TABLE = "sales_agent_companies"
CONTACTS_TABLE = "sales_agent_company_contacts"


@dataclass
class SinkStats:
    companies_inserted: int = 0
    companies_updated: int = 0
    contacts_inserted: int = 0
    skipped_non_target: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class DbSink:
    def __init__(self, database_url: Optional[str] = None):
        url = database_url or os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("缺少 DATABASE_URL(如 postgresql+psycopg://user:pw@host:5432/db)")
        self.engine = create_engine(url, future=True)
        md = MetaData()
        # Reflect existing tables; a missing table raises here, hinting to run alembic in the backend first
        self.companies = Table(COMPANIES_TABLE, md, autoload_with=self.engine)
        self.contacts = Table(CONTACTS_TABLE, md, autoload_with=self.engine)

    def upsert(self, companies: Iterable[Company], only_targets: bool = True) -> SinkStats:
        st = SinkStats()
        now = datetime.utcnow()
        with self.engine.begin() as conn:
            for c in companies:
                if only_targets and not c.is_target_company:
                    st.skipped_non_target += 1
                    continue
                cid = self._find_or_create_company(conn, c, now, st)
                self._add_contact(conn, cid, c, now, st)
        return st

    # ---- Internal ----
    def _extra_columns(self, c: Company) -> dict:
        """Also write the 4 columns added by migration b6f1a3c9e274 if they exist, otherwise skip (forward compatible)."""
        cols = self.companies.c
        m = {
            "credit_code": c.credit_code or "",
            "registered_capital": c.capital or "",
            "established_on": c.establish or "",
            "address": c.address or "",
        }
        return {k: v for k, v in m.items() if k in cols}

    def _find_or_create_company(self, conn, c: Company, now, st: SinkStats) -> int:
        t = self.companies
        extra = self._extra_columns(c)
        # Dedup key = (source, name): the company name is the identity; region is just the collection slice city
        # and must not be in the dedup key (otherwise the same company is inserted repeatedly by different city slices).
        row = conn.execute(
            select(t.c.id).where(t.c.name == c.name, t.c.source == c.source)
        ).first()
        if row:
            conn.execute(
                update(t).where(t.c.id == row[0]).values(
                    industry=c.industry or "", updated_at=now, **extra,
                )
            )
            st.companies_updated += 1
            return row[0]
        res = conn.execute(insert(t).values(
            name=c.name, industry=c.industry or "", region=c.city or "",
            website="", employee_size="", source=c.source or "",
            status="New", created_at=now, updated_at=now, **extra,
        ))
        st.companies_inserted += 1
        return res.inserted_primary_key[0]

    def _add_contact(self, conn, company_id: int, c: Company, now, st: SinkStats):
        email = c.emails[0] if c.emails else ""
        phone = c.phone or ""
        if not (email or phone or c.legal_rep):
            return
        t = self.contacts
        exists = conn.execute(
            select(t.c.id).where(
                t.c.company_id == company_id, t.c.email == email, t.c.phone == phone,
            )
        ).first()
        if exists:
            return
        conn.execute(insert(t).values(
            company_id=company_id,
            contact_name=c.legal_rep or "",
            position="法定代表人" if c.legal_rep else "",
            email=email, phone=phone,
            contact_form_url="",
            source=c.source or "",
            source_url=c.detail_url or "",
            captured_at=now,
            confidence="medium",
            is_reachable=bool(email or phone),
            consent_status="unknown",
            created_at=now,
        ))
        st.contacts_inserted += 1
