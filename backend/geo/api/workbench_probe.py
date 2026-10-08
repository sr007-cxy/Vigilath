"""Workbench "engine measurement" — enter a question + pick an engine, run browser automation once, return the answer + a recording.

Flow (independent of the dispatch center, does not enter the dispatch_tasks / browser_jobs queues):
  POST /workbench/probe/runs  → insert a queued row + BackgroundTask calls browser-service directly
  /search (real-browser Playwright, record_video=true + session_pool="workbench")
  → results written back in place; the frontend polls GET /workbench/probe/runs/{id} every 3s until done/failed.

Accounts: 5 dedicated accounts with pool='workbench' in engine_sessions (1 per engine),
fully isolated from the default pool used by sentiment monitoring.

Recordings: browser-service writes .webm to disk and returns /snapshot/{engine}/{file}; this module's
video endpoint reverse-proxies it with login auth for frontend playback (the browser host address is not exposed).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import List

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from geo.api.auth import require_workbench_or_maintainer
from geo.database import SessionLocal
from geo.models.engine_sessions import EngineSessionORM
from geo.models.workbench_probe import (
    PROBE_SESSION_POOL,
    PROBE_STATUS_DONE,
    PROBE_STATUS_FAILED,
    PROBE_STATUS_QUEUED,
    PROBE_STATUS_RUNNING,
    ProbeEngineOut,
    ProbeRunCreate,
    ProbeRunOut,
    WorkbenchProbeRunORM,
)
from geo.utils.error_handler import AppException

router = APIRouter(prefix="/workbench/probe")

# The 5 CN engines supported for measurement (one-to-one with workbench pool accounts)
_ENGINES: List[ProbeEngineOut] = [
    ProbeEngineOut(key="qwen", name="通义千问"),
    ProbeEngineOut(key="wenxin", name="文心一言"),
    ProbeEngineOut(key="deepseek", name="DeepSeek"),
    ProbeEngineOut(key="doubao", name="豆包"),
    ProbeEngineOut(key="yuanbao", name="腾讯元宝"),
]
_ENGINE_KEYS = {e.key for e in _ENGINES}

# Zombie detection: browser-service's 300s timeout + transfer margin. No terminal state beyond this = the executing process is gone
# (a uvicorn restart killed the BackgroundTask); mark failed in place so the frontend does not spin forever.
_STALE_AFTER = timedelta(seconds=420)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _pool_has_account(db: Session, engine: str) -> bool:
    now = datetime.utcnow()
    return (
        db.query(EngineSessionORM.id)
        .filter(EngineSessionORM.engine == engine)
        .filter(EngineSessionORM.pool == PROBE_SESSION_POOL)
        .filter(EngineSessionORM.status == "active")
        .filter((EngineSessionORM.expires_at == None) | (EngineSessionORM.expires_at > now))  # noqa: E711
        .first()
    ) is not None


def _to_out(row: WorkbenchProbeRunORM) -> ProbeRunOut:
    citations = []
    if row.citations_json:
        try:
            citations = json.loads(row.citations_json)
        except json.JSONDecodeError:
            citations = []
    video_url = None
    if row.video_engine and row.video_filename:
        video_url = f"/api/workbench/probe/runs/{row.id}/video"
    return ProbeRunOut(
        id=row.id,
        engine=row.engine,
        query=row.query,
        status=row.status,
        answer=row.answer,
        citations=citations,
        video_url=video_url,
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _reap_if_stale(db: Session, row: WorkbenchProbeRunORM) -> None:
    """queued/running stuck beyond the threshold → mark failed in place (the executing process was lost with a restart)."""
    if row.status not in (PROBE_STATUS_QUEUED, PROBE_STATUS_RUNNING):
        return
    if datetime.utcnow() - (row.created_at or datetime.utcnow()) <= _STALE_AFTER:
        return
    row.status = PROBE_STATUS_FAILED
    row.error = "执行超时,请重新执行"
    row.finished_at = datetime.utcnow()
    db.commit()


async def _execute(run_id: int) -> None:
    """BackgroundTask: call browser-service for a real-browser run and write results back to the run row."""
    from browser_engine.client import search_with_options

    db = SessionLocal()
    try:
        row = db.query(WorkbenchProbeRunORM).get(run_id)
        if row is None or row.status != PROBE_STATUS_QUEUED:
            return
        row.status = PROBE_STATUS_RUNNING
        row.started_at = datetime.utcnow()
        db.commit()
        engine, query = row.engine, row.query

        data = await search_with_options(
            engine, query,
            record_video=True,
            session_pool=PROBE_SESSION_POOL,
        )

        row = db.query(WorkbenchProbeRunORM).get(run_id)
        if row is None:
            return
        answer = (data.get("answer") or "").strip()
        error = data.get("error")
        row.answer = answer or None
        row.citations_json = json.dumps(data.get("citations") or [], ensure_ascii=False)
        video_url = data.get("video_url") or ""
        # browser-service returns "/snapshot/{engine}/{filename}" → split and store; playback goes through this service's reverse proxy
        parts = [p for p in video_url.split("/") if p]
        if len(parts) == 3 and parts[0] == "snapshot":
            row.video_engine = parts[1]
            row.video_filename = parts[2]
        if error and not answer:
            row.status = PROBE_STATUS_FAILED
            row.error = error
        else:
            # An answer counts as success; non-fatal engine-side errors (e.g. citation fetch failure) are shown alongside
            row.status = PROBE_STATUS_DONE
            row.error = error
        row.finished_at = datetime.utcnow()
        db.commit()
    except Exception as e:  # noqa: BLE001 — fallback: exceptions must also reach a terminal state, so frontend polling does not hang
        try:
            row = db.query(WorkbenchProbeRunORM).get(run_id)
            if row is not None and row.status in (PROBE_STATUS_QUEUED, PROBE_STATUS_RUNNING):
                row.status = PROBE_STATUS_FAILED
                row.error = str(e)[:500]
                row.finished_at = datetime.utcnow()
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/engines", response_model=List[ProbeEngineOut])
async def list_engines(_user=Depends(require_workbench_or_maintainer)):
    return _ENGINES


@router.post("/runs", response_model=ProbeRunOut, status_code=201)
async def create_run(
    payload: ProbeRunCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user=Depends(require_workbench_or_maintainer),
):
    if payload.engine not in _ENGINE_KEYS:
        raise AppException(status_code=400, message="不支持的引擎")
    query = payload.query.strip()
    if not query:
        raise AppException(status_code=400, message="请输入问题")

    # Debounce: if the same user already has a running run → return it (the frontend keeps polling it), without taking another account
    existing = (
        db.query(WorkbenchProbeRunORM)
        .filter(WorkbenchProbeRunORM.created_by == user.id)
        .filter(WorkbenchProbeRunORM.status.in_([PROBE_STATUS_QUEUED, PROBE_STATUS_RUNNING]))
        .order_by(WorkbenchProbeRunORM.id.desc())
        .first()
    )
    if existing is not None:
        _reap_if_stale(db, existing)
        if existing.status in (PROBE_STATUS_QUEUED, PROBE_STATUS_RUNNING):
            return _to_out(existing)

    # Pre-check: does the measurement pool have an available account for this engine; if not, do not waste a browser run
    if not _pool_has_account(db, payload.engine):
        raise AppException(status_code=400, message="该引擎的实测账号暂不可用,请联系管理员补号")

    row = WorkbenchProbeRunORM(
        engine=payload.engine,
        query=query,
        status=PROBE_STATUS_QUEUED,
        created_by=user.id,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    background_tasks.add_task(_execute, row.id)
    return _to_out(row)


@router.get("/runs", response_model=List[ProbeRunOut])
async def list_runs(
    limit: int = 20,
    db: Session = Depends(get_db),
    user=Depends(require_workbench_or_maintainer),
):
    """Recent run history (visible to the whole workbench, for viewing each other's demo results)."""
    rows = (
        db.query(WorkbenchProbeRunORM)
        .order_by(WorkbenchProbeRunORM.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    for r in rows:
        _reap_if_stale(db, r)
    return [_to_out(r) for r in rows]


@router.get("/runs/{run_id}", response_model=ProbeRunOut)
async def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user=Depends(require_workbench_or_maintainer),
):
    row = db.query(WorkbenchProbeRunORM).get(run_id)
    if row is None:
        raise AppException(status_code=404, message="执行记录不存在")
    _reap_if_stale(db, row)
    return _to_out(row)


@router.get("/runs/{run_id}/video")
async def get_run_video(
    run_id: int,
    db: Session = Depends(get_db),
    _user=Depends(require_workbench_or_maintainer),
):
    """Reverse-proxy browser-service's run recording (.webm). engine/filename come from the DB row
    (not user input, naturally safe from path traversal), streamed without reading the whole file into memory."""
    row = db.query(WorkbenchProbeRunORM).get(run_id)
    if row is None:
        raise AppException(status_code=404, message="执行记录不存在")
    if not (row.video_engine and row.video_filename):
        raise AppException(status_code=404, message="该次执行没有录像")
    if not row.video_filename.endswith(".webm"):
        raise AppException(status_code=400, message="录像格式异常")

    from browser_engine.client import get_snapshot_url

    url = get_snapshot_url(row.video_engine, row.video_filename)
    client = httpx.AsyncClient(timeout=120)
    try:
        req = client.build_request("GET", url)
        resp = await client.send(req, stream=True)
    except Exception as e:  # noqa: BLE001
        await client.aclose()
        raise AppException(status_code=502, message=f"录像服务不可用: {e}")
    if resp.status_code == 404:
        await resp.aclose()
        await client.aclose()
        raise AppException(status_code=404, message="录像文件已被清理")
    if resp.status_code != 200:
        code = resp.status_code
        await resp.aclose()
        await client.aclose()
        raise AppException(status_code=502, message=f"录像服务错误({code})")

    async def _stream():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        _stream(),
        media_type="video/webm",
        headers={"Content-Disposition": f'inline; filename="{row.video_filename}"'},
    )
