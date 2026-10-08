"""Cross-worker dedup for IM events — Redis SETNX preferred, falling back to process memory when Redis is unavailable.

Feishu/WeCom/DingTalk all deliver "at least once" and redeliver on timeout. With multiple uvicorn workers, each process keeps its own dict,
so a redelivery hitting another worker gets processed twice (the same message answered twice). Shared dedup via Redis fixes it at the root;
without Redis, fall back to process memory (still effective with a single worker).
"""
from __future__ import annotations

import os
import time

try:
    import redis.asyncio as _aioredis
except ImportError:  # pragma: no cover
    _aioredis = None

_redis_client = None
_redis_down = False
_mem: dict[str, float] = {}


def _get_redis():
    global _redis_client, _redis_down
    if _aioredis is None or _redis_down:
        return None
    if _redis_client is None:
        url = (os.environ.get("REDIS_URL") or "").strip() or "redis://127.0.0.1:6379/0"
        try:
            _redis_client = _aioredis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        except Exception:  # noqa: BLE001
            _redis_down = True
            return None
    return _redis_client


async def seen_before(key: str, ttl: int = 600) -> bool:
    """True = this key was already processed (duplicate event, should be dropped); False = first seen. Empty keys are treated as non-duplicates."""
    global _redis_down
    if not key:
        return False
    r = _get_redis()
    if r is not None:
        try:
            ok = await r.set(f"agent:im:seen:{key}", "1", nx=True, ex=ttl)
            return not ok          # set succeeded = first seen; not set (already exists) = duplicate
        except Exception:  # noqa: BLE001
            _redis_down = True      # If Redis glitches, permanently fall back to memory to avoid waiting for timeouts on every message
    now = time.time()
    for k in [k for k, t in _mem.items() if now - t > ttl]:
        _mem.pop(k, None)
    if key in _mem:
        return True
    _mem[key] = now
    return False
