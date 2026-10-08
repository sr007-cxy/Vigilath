"""Material source text ingestion -- uploaded / pasted materials are chunked whole, vectorized and written into agent_materials (2026-09-22).

Background: "AI smart fill" only extracted materials into the fixed BrandProfile form, without persisting the source text; content outside the form schema
(model specs, FAQs, case details, pricing policies...) was lost if it could not be extracted. The agent_materials vector table used to be written only by
the chat agent's ingest_material tool. Now both coexist: the form is the structured index, and source text goes into the vector store for
material_rag.retrieve (drafting) / ask_knowledge (agent) to fetch passages by similarity.

One material = N rows of the same (account_id, topic_id, source), one chunk per row (~1500 chars, paragraph boundaries preferred),
with "(i/N)" in the title. Re-uploading the same source counts as replacement (old rows deleted first). Embedding failure does not block -- rows are still written,
and retrieve automatically falls back to keyword search when embedding_json is empty.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime

from sqlalchemy import case, func

from geo.models.agent import AgentMaterialORM

log = logging.getLogger(__name__)

CHUNK_CHARS = int(os.environ.get("GEO_MATERIAL_INGEST_CHUNK_CHARS", "1500"))
MAX_CHUNKS = int(os.environ.get("GEO_MATERIAL_INGEST_MAX_CHUNKS", "60"))     # 60 x 1500 ~= 90k chars
_EMBED_BATCH = int(os.environ.get("GEO_MATERIAL_INGEST_EMBED_BATCH", "5"))
_EMBED_TIMEOUT = float(os.environ.get("GEO_MATERIAL_INGEST_EMBED_TIMEOUT", "30"))

_PARA_SEP = re.compile(r"\n\s*\n")


def split_document(text: str, *, size: int = CHUNK_CHARS, max_chunks: int = MAX_CHUNKS) -> list[str]:
    """Accumulate chunks by paragraph: cut when paragraphs reach size; hard-cut single overlong paragraphs by size."""
    out: list[str] = []
    buf = ""
    for para in _PARA_SEP.split((text or "").strip()):
        p = para.strip()
        if not p:
            continue
        while len(p) > size:                 # Hard-cut overlong paragraphs
            if buf:
                out.append(buf); buf = ""
            out.append(p[:size]); p = p[size:]
        if buf and len(buf) + 1 + len(p) > size:
            out.append(buf); buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
        if len(out) >= max_chunks:
            break
    if buf and len(out) < max_chunks:
        out.append(buf)
    return out[:max_chunks]


def default_title(text: str, fallback: str = "") -> str:
    """Title = first non-empty line (<= 60 chars); empty -> fallback."""
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[:60]
    return fallback


def text_source(text: str) -> str:
    """Pasted text has no filename, so a content hash serves as source; pasting the same passage again = replacement."""
    return "text:" + hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:12]


async def ingest_document(db, *, account_id: int, topic_id: int | None, source: str,
                          title: str, text: str) -> dict:
    """Ingest the whole document (replacement semantics). Returns {"source", "chunks", "embedded"}."""
    chunks = split_document(text)
    if not chunks:
        return {"source": source, "chunks": 0, "embedded": 0}
    source = (source or "").strip()[:512] or "untitled"
    title = (title or "").strip()[:400] or source

    # Vectors: small batches + long timeout + one retry on failure; if the whole batch fails, leave empty (retrieve falls back to keywords).
    # 2026-09-22 production saw "vectors not generated": embed_texts' default 8s is meant for a single query, and a batch of 10 long chunks cannot fit.
    vecs: list[list[float] | None] = [None] * len(chunks)
    embed_error = ""
    try:
        from geo.services.embedding import embed_texts, embedding_enabled, last_error
        if not embedding_enabled():
            embed_error = "no_provider"
        else:
            for i in range(0, len(chunks), _EMBED_BATCH):
                batch = chunks[i:i + _EMBED_BATCH]
                got = None
                for attempt in range(2):
                    got = await embed_texts(batch, feature="material_ingest", timeout=_EMBED_TIMEOUT)
                    if got and len(got) == len(batch):
                        break
                    embed_error = last_error() or "unknown"
                    log.warning("material ingest: embed batch %d attempt %d failed source=%s: %s",
                                i // _EMBED_BATCH, attempt + 1, source, embed_error)
                if got and len(got) == len(batch):
                    vecs[i:i + len(batch)] = got
    except Exception as e:  # noqa: BLE001
        embed_error = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning("material ingest: embedding failed source=%s -- writing rows without vectors", source, exc_info=True)

    import json as _json
    q = db.query(AgentMaterialORM).filter(
        AgentMaterialORM.account_id == account_id, AgentMaterialORM.source == source,
    )
    q = q.filter(AgentMaterialORM.topic_id == topic_id) if topic_id is not None \
        else q.filter(AgentMaterialORM.topic_id.is_(None))
    q.delete(synchronize_session="fetch")   # Remove the deleted old rows from the session identity map, so new rows do not collide on id
    now = datetime.utcnow()
    n = len(chunks)
    for i, (chunk, vec) in enumerate(zip(chunks, vecs)):
        db.add(AgentMaterialORM(
            account_id=account_id, topic_id=topic_id, source=source,
            title=f"{title} ({i + 1}/{n})" if n > 1 else title,
            text=chunk, embedding_json=_json.dumps(vec) if vec else None, created_at=now,
        ))
    db.flush()
    embedded = sum(1 for v in vecs if v)
    out = {"source": source, "chunks": n, "embedded": embedded}
    if embedded < n and embed_error:
        out["embed_error"] = embed_error      # Tell the frontend "why there are no vectors": no_provider = no key configured
    return out


def ingest_document_sync(db, **kw) -> dict:
    """For sync endpoints; inside an event loop asyncio.run is impossible, so degrade to ingesting without vectors."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(ingest_document(db, **kw))
    # Inside a loop (should not happen: sync endpoints run in the thread pool) -- conservatively only write rows without vectors
    log.warning("material ingest: called inside running loop -- writing rows without vectors")
    chunks = split_document(kw.get("text") or "")
    source = (kw.get("source") or "").strip()[:512] or "untitled"
    title = (kw.get("title") or "").strip()[:400] or source
    now = datetime.utcnow()
    for i, chunk in enumerate(chunks):
        db.add(AgentMaterialORM(
            account_id=kw["account_id"], topic_id=kw.get("topic_id"), source=source,
            title=f"{title} ({i + 1}/{len(chunks)})" if len(chunks) > 1 else title,
            text=chunk, embedding_json=None, created_at=now,
        ))
    db.flush()
    return {"source": source, "chunks": len(chunks), "embedded": 0, "embed_error": "called_inside_loop"}


def list_documents(db, *, account_id: int, topic_id: int | None) -> list[dict]:
    """Aggregate by source: this topic's + account-level (empty topic_id, passed during the wizard)."""
    q = (
        db.query(
            AgentMaterialORM.source, AgentMaterialORM.topic_id,
            func.min(AgentMaterialORM.title).label("title"),
            func.count(AgentMaterialORM.id).label("chunks"),
            func.sum(func.length(AgentMaterialORM.text)).label("chars"),
            func.sum(case((AgentMaterialORM.embedding_json.isnot(None), 1), else_=0)).label("embedded"),
            func.max(AgentMaterialORM.created_at).label("created_at"),
        )
        .filter(AgentMaterialORM.account_id == account_id)
    )
    if topic_id is not None:
        q = q.filter((AgentMaterialORM.topic_id == topic_id) | (AgentMaterialORM.topic_id.is_(None)))
    else:
        q = q.filter(AgentMaterialORM.topic_id.is_(None))
    rows = q.group_by(AgentMaterialORM.source, AgentMaterialORM.topic_id) \
            .order_by(func.max(AgentMaterialORM.created_at).desc()).all()
    out: list[dict] = []
    for r in rows:
        title = re.sub(r"\s\(\d+/\d+\)$", "", r.title or "")
        out.append({
            "source": r.source, "topic_id": r.topic_id, "title": title or r.source,
            "chunks": int(r.chunks or 0), "chars": int(r.chars or 0), "embedded": int(r.embedded or 0),
            "created_at": r.created_at,
        })
    return out


def delete_document(db, *, account_id: int, topic_id: int | None, source: str) -> int:
    q = db.query(AgentMaterialORM).filter(
        AgentMaterialORM.account_id == account_id, AgentMaterialORM.source == source,
    )
    q = q.filter(AgentMaterialORM.topic_id == topic_id) if topic_id is not None \
        else q.filter(AgentMaterialORM.topic_id.is_(None))
    return q.delete(synchronize_session=False)
