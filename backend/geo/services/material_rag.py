"""User material vector store retrieval -- wires agent_materials into the content generation pipeline as grounding.

## Why this file exists

`agent_materials` (models/agent.py) used to be read only by the chat agent's `ask_knowledge`;
the drafting pipeline (content_generator) never queried it once -- real materials users uploaded in chat
(company intro, product specs, contact info) were stored in the DB but invisible when writing articles, so the model could only make things up.
This module fills that gap: the same store, the same vectors, with a **synchronous** retrieval entry point for the generation side.

## Two differences from ask_knowledge

1. **Synchronous**: drafting runs in a `threading.Thread` (content_generator.schedule_generation)
   with no event loop, so `asyncio.run` wraps `embed_texts` here; retrieval failures always fall back to
   bigram keywords, and if that fails return empty -- material retrieval must never break the drafting pipeline.
2. **Chunking**: one material in the store = one row = one whole-document vector, and embed input is truncated to 2000 chars
   (embedding.py), so whole-document vectors are blurry for long materials. So vectors here are only used to **coarsely filter materials**;
   once selected, the material body is sliced by `_CHUNK_CHARS` and bigram picks the most relevant slice to inject,
   avoiding stuffing tens of thousands of chars into the prompt, and avoiding the 2000-char truncation deciding all hits.

Tenant boundary: filtered by `account_id` (= user id); topic-level materials take priority over account-level materials.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

log = logging.getLogger(__name__)

# Max chars injected per material / max materials injected -- both can be lowered via env
_CHUNK_CHARS = int(os.environ.get("GEO_MATERIAL_CHUNK_CHARS", "700"))
_MAX_REFS = int(os.environ.get("GEO_MATERIAL_REF_MAX", "3"))
# Since 2026-09-22 uploaded materials are chunked whole into the store (material_store, dozens of rows per material), so 200 rows only fit three or four materials;
# raised to 1000 rows -- pure-Python cosine over a thousand 1024-dim vectors takes only a few hundred ms, negligible in the drafting pipeline.
_CANDIDATE_LIMIT = int(os.environ.get("GEO_MATERIAL_CANDIDATE_LIMIT", "1000"))
_SEM_THRESHOLD = 0.2            # Cosine threshold; below it is considered irrelevant (same as ask_knowledge)


def enabled() -> bool:
    """When disabled via env, skip entirely (no code rollback needed when something goes wrong)."""
    return (os.environ.get("GEO_CONTENT_MATERIAL_RAG", "1") or "1").strip() != "0"


def _tokens(s: str) -> list[str]:
    """Chinese bigrams + English/numeric words -- same metric definition as ask_knowledge's fallback tokenization."""
    s = (s or "").lower()
    toks = re.findall(r"[a-z0-9]{2,}", s)
    for run in re.findall(r"[一-鿿]+", s):
        toks += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return toks


def _chunks(text: str) -> list[str]:
    out: list[str] = []
    s = (text or "").strip()
    for i in range(0, len(s), _CHUNK_CHARS):
        piece = s[i:i + _CHUNK_CHARS].strip()
        if piece:
            out.append(piece)
        if len(out) >= 40:      # At most 40 chunks per material (~28000 chars); longer materials are not worth scanning in full
            break
    return out


def _best_chunk(text: str, qtoks: list[str]) -> str:
    """Pick the slice within one material most relevant to the query; fall back to the beginning if no token matches."""
    parts = _chunks(text)
    if not parts:
        return ""
    if not qtoks:
        return parts[0]
    best, best_score = parts[0], -1.0
    for p in parts:
        low = p.lower()
        hit = sum(1 for t in qtoks if t in low)
        if hit == 0:
            continue
        occ = sum(low.count(t) for t in qtoks)
        score = hit * 2 + min(occ, 20)
        if score > best_score:
            best, best_score = p, score
    return best


def _embed_query_sync(query: str) -> list[float] | None:
    """Get the query vector synchronously; no key / failure / already inside an event loop -> None (caller falls back to keywords)."""
    try:
        from geo.services.embedding import embed_texts, embedding_enabled
        if not embedding_enabled():
            return None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass                # No running loop, exactly what we want (drafting runs in its own thread)
        else:
            return None         # Called inside a loop -> cannot asyncio.run, fall back directly
        vecs = asyncio.run(embed_texts([query]))
        return vecs[0] if vecs else None
    except Exception:  # noqa: BLE001
        log.warning("material rag: embed query failed -- falling back to keyword search", exc_info=True)
        return None


def retrieve(db, *, account_id: int, topic_id: int | None, query: str,
             limit: int = _MAX_REFS) -> list[dict]:
    """Retrieve the slices in the account's material store most relevant to the query, returning [{title, source, snippet, mode}].

    Any failing step returns an empty list -- materials are an enhancement and must not end up blocking drafting.
    """
    if not enabled() or not account_id or not (query or "").strip() or limit <= 0:
        return []
    try:
        from geo.models.agent import AgentMaterialORM
        q = db.query(AgentMaterialORM).filter(AgentMaterialORM.account_id == account_id)
        if topic_id is not None:
            # Only this topic's + account-level (empty topic_id) ones; materials from the same account's other brand topics are not mixed in
            q = q.filter((AgentMaterialORM.topic_id == topic_id) | (AgentMaterialORM.topic_id.is_(None)))
        rows = q.order_by(AgentMaterialORM.id.desc()).limit(_CANDIDATE_LIMIT).all()
    except Exception:  # noqa: BLE001
        log.warning("material rag: failed to fetch materials account=%s -- skipping", account_id, exc_info=True)
        return []
    if not rows:
        return []

    qtoks = list(dict.fromkeys(_tokens(query)))[:40]
    # This topic's materials first: on ties, ranked ahead of account-level materials
    def _topic_bonus(m) -> float:
        return 1.0 if (topic_id is not None and getattr(m, "topic_id", None) == topic_id) else 0.0

    ranked: list[tuple[float, object]] = []
    mode = "keyword"
    qvec = _embed_query_sync(query)
    if qvec:
        from geo.services.embedding import cosine
        for m in rows:
            if not m.embedding_json:
                continue
            try:
                mv = json.loads(m.embedding_json)
            except Exception:  # noqa: BLE001
                continue
            s = cosine(qvec, mv)
            if s > _SEM_THRESHOLD:
                ranked.append((s + _topic_bonus(m), m))
        if ranked:
            mode = "semantic"

    if not ranked:                    # No vectors / no hits -> bigram fallback
        for m in rows:
            low = (m.text or "").lower()
            hit = sum(1 for t in qtoks if t in low)
            if hit == 0:
                continue
            occ = sum(low.count(t) for t in qtoks)
            ranked.append((hit * 2 + min(occ, 20) + _topic_bonus(m) * 5, m))

    if not ranked:
        return []
    ranked.sort(key=lambda x: -x[0])
    refs: list[dict] = []
    for _, m in ranked[:limit]:
        snippet = _best_chunk(m.text or "", qtoks)
        if not snippet:
            continue
        refs.append({
            "title": (m.title or "").strip() or "(无标题资料)",
            "source": (m.source or "").strip(),
            "snippet": snippet,
            "mode": mode,
        })
    return refs
