"""Provider abstract interface.

Each company data provider (Qichamao / Tianyancha / Qichacha / Aiqicha / national disclosure system ...) implements a Provider,
encapsulating its own login session, URLs, pagination and parsing. The upper-layer collector depends only on this interface, decoupled from specific providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from ..models import Company


@dataclass
class ListResult:
    companies: List[Company] = field(default_factory=list)
    ok: bool = True            # whether this request was in a valid logged-in / not-blocked state


class Provider(ABC):
    #: provider name, goes into Company.source and is also the CLI/registry selection key
    name: str = "base"
    #: capital band values supported by this provider (used by the collector for --bands all)
    capital_bands: List[str] = []
    #: default company-type filter value (filters out non-target entities such as government/public institutions)
    default_company_type: str = ""
    #: page limit per search keyword
    max_page: int = 5

    @abstractmethod
    def list_companies(self, word: str, city: str, band: str,
                       company_type: str, page: int) -> ListResult:
        """Fetch one page of search results. Empty city means no city slicing; empty band means no capital-band slicing."""

    @abstractmethod
    def enrich(self, company: Company) -> bool:
        """Enrich a company's contact/personnel detail fields (in place). Returns whether the login session is still valid."""

    def ready(self) -> bool:
        """Whether the provider has usable credentials (e.g. Cookie configured). Defaults to True."""
        return True
