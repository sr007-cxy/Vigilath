"""Pydantic v2 models for browser service API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class SearchRequest(BaseModel):
    engine: str
    query: str
    # Per-request overrides (2026-07, workbench "engine live test"). None = keep the old behavior:
    # recording follows the GEO_RECORD_VIDEO environment variable, accounts use the default pool.
    record_video: Optional[bool] = None
    session_pool: Optional[str] = None


class CitationOut(BaseModel):
    url: str
    domain: str
    title: str = ""
    snippet: str = ""
    position: int = 0


class SearchResponse(BaseModel):
    engine: str
    query: str
    answer: str = ""
    citations: List[CitationOut] = []
    error: Optional[str] = None
    video_url: Optional[str] = None
    reasoning: Optional[str] = None      # chain of thought, see engines/base.py EngineResult.reasoning
    session_id: Optional[int] = None   # which pool account was used (aggregate per account when troubleshooting); empty with a local-file login session
    # Engine-side collection params/state (first used by kimi: thinking / status / search_triggered / inline_refs ...),
    # stored by the center in ai_telemetry_responses.meta_json; old centers ignore unknown fields. None = not provided by the engine.
    meta: Optional[dict] = None


class FetchTitleRequest(BaseModel):
    url: str
    timeout_ms: int = 15000


class FetchTitleResponse(BaseModel):
    url: str
    title: str = ""
    error: Optional[str] = None


class SessionInfo(BaseModel):
    engine: str
    has_session: bool
    name: str = ""


class EnginesResponse(BaseModel):
    region: str
    engines: List[str]


class HealthResponse(BaseModel):
    status: str
    browser_connected: bool
    region: str
    engine_count: int
