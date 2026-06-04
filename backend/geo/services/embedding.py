"""通义 DashScope text-embedding(OpenAI 兼容端)—— 给 agent 资料 RAG 做向量语义检索。

用现有 QWEN_API_KEY(通义/DashScope 账号级 key,可同时用于 generation + embedding);
无 key 时 embedding_enabled()=False,ask_knowledge 自动回退 bigram 关键词检索。
"""
from __future__ import annotations

import math
import os

import httpx

DASHSCOPE_EMBED_URL = os.environ.get(
    "DASHSCOPE_EMBED_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
)
EMBED_MODEL = os.environ.get("AGENT_EMBED_MODEL", "text-embedding-v3")


def _key() -> str:
    return (os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()


def embedding_enabled() -> bool:
    return bool(_key())


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """批量 embedding;失败/无 key 返回 None(调用方回退关键词)。input 截断到 ~2000 字。"""
    key = _key()
    cleaned = [(t or "")[:2000] for t in texts if (t or "").strip()]
    if not key or not cleaned:
        return None
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(DASHSCOPE_EMBED_URL, json={"model": EMBED_MODEL, "input": cleaned}, headers=headers)
            r.raise_for_status()
            data = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
            vecs = [d["embedding"] for d in data]
            return vecs if len(vecs) == len(cleaned) else None
    except Exception:  # noqa: BLE001 — embedding 失败不阻断,回退关键词
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
