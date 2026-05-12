"""DB session for telemetry-service.

读写同一 SQLAlchemy DB(backend 主库,目前是 SQLite,sentinel v2 后切 MySQL).
Schema 由 backend 的 geo/__init__.py 创建,这里只复用同一张表的连接.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    # 默认指向 backend 同一 SQLite — 部署时通过 env 覆盖
    "sqlite:////home/DEV/GEO/backend/data/geo_checker.db",
)

_connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


# 注:这些 ORM 定义需要与 backend geo/models/ai_telemetry.py 保持字段一致.
# 选择独立声明而非共享 Base,是为了让 telemetry-service 可独立部署、不强依赖 backend 代码包.

class TopicORM(Base):
    __tablename__ = "ai_telemetry_topics"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String, nullable=False)
    queries_json = Column(Text, nullable=False, default="[]")
    engines_json = Column(Text, nullable=False, default="[]")
    enabled = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RunORM(Base):
    __tablename__ = "ai_telemetry_runs"
    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id"), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="running")
    error = Column(Text, nullable=True)


class ResponseORM(Base):
    __tablename__ = "ai_telemetry_responses"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("ai_telemetry_runs.id"), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id"), nullable=False, index=True)
    engine = Column(String, nullable=False)
    query = Column(Text, nullable=False)
    answer = Column(Text, nullable=False, default="")
    citations_json = Column(Text, nullable=False, default="[]")
    video_url = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


@contextmanager
def db_session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ── helpers ────────────────────────────────────────────────────


def list_enabled_topics(s: Session) -> list[TopicORM]:
    return s.query(TopicORM).filter(TopicORM.enabled == True).all()  # noqa: E712


def start_run(s: Session, topic_id: int) -> RunORM:
    r = RunORM(topic_id=topic_id, started_at=datetime.utcnow(), status="running")
    s.add(r)
    s.flush()
    # mark topic running
    t = s.get(TopicORM, topic_id)
    if t:
        t.last_run_at = r.started_at
        t.last_run_status = "running"
    return r


def finish_run(s: Session, run_id: int, status: str, error: str | None = None) -> None:
    r = s.get(RunORM, run_id)
    if not r:
        return
    r.finished_at = datetime.utcnow()
    r.status = status
    r.error = error
    t = s.get(TopicORM, r.topic_id)
    if t:
        t.last_run_status = status


def save_response(
    s: Session, *, run_id: int, topic_id: int, engine: str, query: str,
    answer: str, citations: list[dict], video_url: str | None, error: str | None,
) -> None:
    s.add(ResponseORM(
        run_id=run_id, topic_id=topic_id, engine=engine, query=query,
        answer=answer or "",
        citations_json=json.dumps(citations or [], ensure_ascii=False),
        video_url=video_url,
        error=error,
    ))


def parse_topic(t: TopicORM) -> tuple[list[str], list[str]]:
    queries = json.loads(t.queries_json or "[]")
    engines = json.loads(t.engines_json or "[]")
    return queries, engines
