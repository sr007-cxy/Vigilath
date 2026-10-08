"""SEO check request/response models.

Deliberately reuses `CheckResult` from models.geo: the SEO suite emits the
exact same line protocol (`--- SEO: X ---` / `[PASS] …` / `[ FIX] …`) as the
GEO suite, so a second result-row type would only duplicate the i18n fields.
The two reports stay distinguishable by the `SEO: ` category prefix and by the
`SEO SCORE:` header the parser keys on.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from geo.models.geo import CheckResult, GeoTestResult


class SeoTestRequest(BaseModel):
    url: str = Field(..., description="Website URL to audit")
    include_fix: bool = Field(default=True, description="Include fix recommendations")
    force_refresh: bool = Field(
        default=False,
        description="Skip the cache and run fresh (used by 'verify after fix')",
    )


class SeoTestResult(BaseModel):
    url: str
    score: int
    grade: str
    checks: List[CheckResult]
    summary: Dict[str, Any]
    tier: Optional[str] = None
    locked_categories: Optional[List[str]] = None


class SeoCategoriesOut(BaseModel):
    all_categories: List[str]
    free_categories: List[str]


class CheckResultBundle(GeoTestResult):
    """A GEO result with the SEO result attached — the `include_seo` response.

    Deliberately declared here rather than as a `seo` field on GeoTestResult:
    this module already imports from models.geo, so the reverse import would be
    circular.

    `seo` is None when the caller didn't ask for it, and also when the SEO run
    failed. A broken SEO suite must never take the GEO response down with it,
    so the endpoint swallows SEO errors and leaves this null — the frontend
    renders a retry affordance on the SEO half and keeps the GEO half intact.
    """
    seo: Optional[SeoTestResult] = None

    # History row id written by this audit (logged-in users only). The frontend uses it to call
    # POST /account/detections/{id}/export-pdf to print a vector PDF via backend Chrome;
    # anonymous users have no history row, so this is None and export falls back to frontend html2canvas.
    record_id: Optional[int] = None
