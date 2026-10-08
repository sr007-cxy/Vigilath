"""Text embedding (multi-provider, OpenAI compatible) -- vector semantic search for agent material RAG.

Provider priority:
  0) neolink gateway (AGENT_EMBED_PROVIDER=neolink, reuses DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY,
     model defaults to text-embedding-v4) -- production test on 2026-09-22: DashScope 401, GLM embedding-3 400,
     OpenAI direct unreachable; only neolink produces vectors. Used only when explicitly specified, never as an automatic fallback
  1) OpenAI(OPENAI_API_KEY)
  2) GLM Zhipu (GLM_API_KEY, open.bigmodel.cn, model embedding-3)
  3) Tongyi DashScope (QWEN_API_KEY / DASHSCOPE_API_KEY, text-embedding-v3)
With no key at all embedding_enabled()=False, and ask_knowledge automatically falls back to bigram keyword search.
★ Changing provider = changing vector space: old vectors in agent_materials will not match in dimension / semantics (cosine returns 0 for different dimensions),
  so after switching materials must be deleted and re-uploaded (or run backfill).
"""
from __future__ import annotations

import math
import os
from geo.services.llm_usage import track


def _provider() -> tuple[str | None, str, str, str]:
    """Returns (provider, url, model, key); returns (None, ...) without a key.

    Order: OpenAI (has key and supports embedding) -> GLM Zhipu -> DashScope Tongyi.
    Can be forced with AGENT_EMBED_PROVIDER (openai/glm/dashscope).
    """
    force = (os.environ.get("AGENT_EMBED_PROVIDER") or "").strip().lower()

    if force == "neolink":
        base = (os.environ.get("EMBED_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or "https://neolink.com/api/v1").rstrip("/")
        key = (os.environ.get("EMBED_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if key:
            return "neolink", f"{base}/embeddings", os.environ.get("AGENT_EMBED_MODEL", "text-embedding-v4"), key
        return None, "", "", ""

    oa = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if oa and force in ("", "openai"):
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        return "openai", f"{base}/embeddings", os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small"), oa

    glm = (os.environ.get("GLM_API_KEY") or "").strip()
    if glm and force in ("", "glm"):
        base = (os.environ.get("GLM_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4/").rstrip("/")
        return "glm", f"{base}/embeddings", os.environ.get("GLM_EMBED_MODEL", "embedding-3"), glm

    qwen = (os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if qwen and force in ("", "dashscope"):
        url = os.environ.get("DASHSCOPE_EMBED_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
        return "dashscope", url, os.environ.get("AGENT_EMBED_MODEL", "text-embedding-v3"), qwen

    return None, "", "", ""


def embedding_enabled() -> bool:
    return _provider()[0] is not None


def current_provider() -> str | None:
    return _provider()[0]


# Reason for the last embed_texts failure (in-process, only for the caller to read immediately; do not use it for statistics)
_last_error: str = ""


def last_error() -> str:
    return _last_error


async def embed_texts(texts: list[str], *, feature: str = "embedding",
                      timeout: float = 8) -> list[list[float]] | None:
    """Batch embedding; returns None on failure/no key (caller falls back to keywords). input truncated to ~2000 chars.

    feature is only used for accounting categorization (default "embedding") -- has a default so old callers need no change.
    timeout defaults to 8s: for single agent / drafting queries, to fall back to bigram quickly when the provider is unavailable;
    ingesting whole materials sends a dozen-plus long chunks per batch and needs longer (material_store passes 30).
    **No accounting** without a key: that path never sent HTTP, and recording it would skew the failure rate.
    The failure reason goes into last_error(), for callers that need to show users "why there are no vectors".
    """
    global _last_error
    import httpx

    provider, url, model, key = _provider()
    cleaned = [(t or "")[:2000] for t in texts if (t or "").strip()]
    if not provider:
        _last_error = "no_provider"
        return None
    if not cleaned:
        return None
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    # The accounting provider column is the "model vendor"; the channel (neolink) is distinguished by endpoint_host -- see llm_usage._endpoint_host
    vendor = provider
    if provider == "neolink":
        vendor = "dashscope" if ("v4" in model or "qwen" in model.lower()) else "openai"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            with track(feature, vendor, model, call_kind="embedding", endpoint=url) as t:
                r = await client.post(url, json={"model": model, "input": cleaned}, headers=headers)
                t.http_status = r.status_code
                if r.status_code < 400:
                    t.usage = r.json()
                r.raise_for_status()
            data = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
            vecs = [d["embedding"] for d in data]
            if len(vecs) != len(cleaned):
                _last_error = f"{provider}: got {len(vecs)} vectors for {len(cleaned)} inputs"
                return None
            _last_error = ""
            return vecs
    except httpx.HTTPStatusError as e:  # noqa: BLE001 -- embedding failures do not block; fall back to keywords
        _last_error = f"{provider} http {e.response.status_code}: {e.response.text[:200]}"
        return None
    except Exception as e:  # noqa: BLE001
        _last_error = f"{provider} {type(e).__name__}: {str(e)[:200]}"
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
