"""Workbench "engine probe" -- record of one browser automation run.

Submit-and-poll model: POST creates a queued row, a BackgroundTask calls browser-service /search
(real browser + forced recording + workbench-dedicated account pool), results are written back in place; the frontend polls for
answer + recording. Completely unrelated to the dispatch center's dispatch_tasks / browser_jobs.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from geo.database import Base

# State machine: queued -> running -> done | failed (no retries; on failure the user re-runs)
PROBE_STATUS_QUEUED = "queued"
PROBE_STATUS_RUNNING = "running"
PROBE_STATUS_DONE = "done"
PROBE_STATUS_FAILED = "failed"

# Marker for the probe-dedicated account pool (engine_sessions.pool)
PROBE_SESSION_POOL = "workbench"


class WorkbenchProbeRunORM(Base):
    __tablename__ = "workbench_probe_runs"
    __table_args__ = (
        Index("idx_probe_runs_creator_status", "created_by", "status"),
    )

    id = Column(Integer, primary_key=True)
    engine = Column(String, nullable=False)
    query = Column(Text, nullable=False)
    status = Column(String, nullable=False, default=PROBE_STATUS_QUEUED)
    answer = Column(Text, nullable=True)
    citations_json = Column(Text, nullable=True)
    # Recording location: browser-service returns video_url="/snapshot/{engine}/{file}", stored split;
    # playback goes through this service's reverse-proxy endpoint, never exposing the browser host's internal address to the frontend.
    video_engine = Column(String, nullable=True)
    video_filename = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


# ── Pydantic schemas ───────────────────────────────────────────────


class ProbeRunCreate(BaseModel):
    engine: str = Field(..., description="deepseek/doubao/qwen/wenxin/yuanbao")
    query: str = Field(..., min_length=1, max_length=2000)


class ProbeCitationOut(BaseModel):
    url: str = ""
    domain: str = ""
    title: str = ""
    snippet: str = ""
    position: int = 0


class ProbeRunOut(BaseModel):
    id: int
    engine: str
    query: str
    status: str
    answer: Optional[str] = None
    citations: List[ProbeCitationOut] = []
    video_url: Optional[str] = None    # When ready, this service's reverse-proxy URL /api/workbench/probe/runs/{id}/video
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ProbeEngineOut(BaseModel):
    key: str
    name: str
