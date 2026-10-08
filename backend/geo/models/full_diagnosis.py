"""One-click full diagnosis data model.

One input of website URL + brand name (+ optional competitor domains); the backend runs all 7 audits,
synthesizes a four-dimensional score + LLM narrative blocks, and persists a 9-chapter combined report.

State machine (same as solution_generator; all task state lives in the DB for multi-worker polling):
    generating → ready / failed
A single sub-audit failing does not fail the whole; it is failed only when neither base (25 categories) nor visibility returns data.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from geo.database import Base


class FullDiagnosisReportORM(Base):
    __tablename__ = "full_diagnosis_reports"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="generating")  # generating|ready|failed
    # Audit scope tier: full = all eight items; lite = only the four site-side items (base/aeo/crawlTest/authority).
    # Persisted so the report knows which items it ran when read back — the frontend relies on it to distinguish "not audited this time" from "audit failed",
    # and reuse/dedup must also be isolated by tier, otherwise a lite report would be reused as a full one.
    scope = Column(String(16), nullable=False, default="full", server_default="full")
    url = Column(String(512), nullable=False)
    brand_name = Column(String(128), nullable=False, default="")
    competitor_domains_json = Column(Text, nullable=False, default="[]")
    # User-entered industry terms (term source for ranking queries) and brand terms (term source for reputation queries)
    industry_terms_json = Column(Text, nullable=False, default="[]")
    brand_terms_json = Column(Text, nullable=False, default="[]")
    # {stage: "checks"|"llm", done, total, steps: [{mode, status, error?}]}
    progress_json = Column(Text, nullable=False, default="{}")
    # {mode: {status: "ok"|"failed"|"skipped", data|error|reason}}
    # Note: full visibility data can reach hundreds of KB; sqlite Text has no length limit,
    # a future switch to MySQL would need MEDIUMTEXT (watch out during migration).
    subresults_json = Column(Text, nullable=False, default="{}")
    # Manually specified term pack [{"text","scene"}]; when non-empty, orchestration skips build_query_plan
    # and uses it verbatim (term packs finalized in the consultant's plan are persisted directly, rerunnable and auditable)
    query_pack_json = Column(Text, nullable=False, default="[]", server_default="[]")
    # {total, grade, dimensions: [{key, score, weight, estimated, source}]}
    scores_json = Column(Text, nullable=False, default="{}")
    # LLM narrative blocks; stays "{}" when the LLM fails (the report is still ready, narrative slots are placeholders in the frontend)
    narrative_json = Column(Text, nullable=False, default="{}")
    llm_model = Column(String(64), nullable=False, default="")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True, default=None)


# ─────────────── API response models ───────────────


class FullDiagnosisStep(BaseModel):
    mode: str
    status: str  # pending|running|ok|failed|skipped
    error: Optional[str] = None


class FullDiagnosisProgress(BaseModel):
    stage: str = "checks"  # checks|llm
    done: int = 0
    total: int = 0
    steps: List[FullDiagnosisStep] = []


class FullDiagnosisOut(BaseModel):
    id: int
    status: str
    scope: str = "full"
    url: str
    brand_name: str
    competitor_domains: List[str] = []
    industry_terms: List[str] = []
    brand_terms: List[str] = []
    progress: Optional[FullDiagnosisProgress] = None
    # The three blocks below are only included in the ready state; generating poll responses deliberately omit them (every 3s, saving bandwidth)
    scores: Optional[Dict[str, Any]] = None
    narrative: Optional[Dict[str, Any]] = None
    subresults: Optional[Dict[str, Any]] = None
    llm_model: str = ""
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FullDiagnosisSummary(BaseModel):
    id: int
    url: str
    brand_name: str
    status: str
    total_score: Optional[int] = None
    grade: Optional[str] = None
    created_at: Optional[datetime] = None


class FullDiagnosisList(BaseModel):
    items: List[FullDiagnosisSummary]
    total: int
    page: int
    size: int


class QueryPackItem(BaseModel):
    text: str
    scene: str = ""   # brand/compare are ultimately forced by rules; the other four types trust this value


class GenerateFullDiagnosisPayload(BaseModel):
    url: str
    brand_name: str
    competitor_domains: List[str] = []
    industry_terms: List[str] = []   # Industry terms, ≥1, term source for ranking queries
    brand_terms: List[str] = []      # Brand terms, may be empty (defaults to the brand name), term source for reputation queries
    # Manual term pack: when non-empty, LLM planning / templates / quota trimming are **skipped entirely** and it is used as-is;
    # when it is passed, industry_terms is no longer required (it is only the planner's term source)
    queries: List[QueryPackItem] = []
    # Audit scope: full (default, eight items) / lite (four site-side items). Omitted means full -- existing callers are unaffected.
    scope: str = "full"
