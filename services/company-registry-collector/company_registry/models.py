"""Data model: company record (provider-agnostic).

Field names align with the DB mapping in `docs/08-独立模块/SalesAgent/03-系统设计/企业推荐数据来源库.md` §4
(sales_agent_companies / _company_contacts / _company_signals). `source` records which provider the data came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import List

from .config import NON_COMPANY_PATTERN

_NON_COMPANY_RE = re.compile(NON_COMPANY_PATTERN)


@dataclass
class Company:
    id: str                              # provider-internal id
    source: str = ""                     # source provider name (source_type)
    name: str = ""                       # canonical company name (canonical_name)
    city: str = ""                       # city of the collection slice
    band: str = ""                       # capital band of the collection slice
    legal_rep: str = ""                  # legal representative
    capital: str = ""                    # registered capital
    establish: str = ""                  # date of establishment
    status: str = ""                     # registration status
    industry: str = ""                   # industry
    phone: str = ""                      # company phone
    emails: List[str] = field(default_factory=list)   # company emails (denoised, may be multiple)
    address: str = ""                    # registered/company address
    credit_code: str = ""                # unified social credit code (dedup key, filled when the provider has it)
    detail_url: str = ""                 # the company's detail page URL on the source site (persisted as source_url / evidence)

    @property
    def dedup_key(self) -> str:
        return self.credit_code or f"{self.source}:{self.id}"

    @property
    def is_target_company(self) -> bool:
        return not bool(_NON_COMPANY_RE.search(self.name))

    @property
    def has_contact(self) -> bool:
        return bool(self.phone or self.emails)

    def to_row(self) -> dict:
        d = asdict(self)
        d["emails"] = ";".join(self.emails)
        d["is_target_company"] = "是" if self.is_target_company else "否"
        return d


CSV_COLUMNS = [
    "id", "source", "name", "city", "band", "is_target_company", "credit_code",
    "legal_rep", "capital", "establish", "status", "industry", "phone", "emails",
    "address", "detail_url",
]
