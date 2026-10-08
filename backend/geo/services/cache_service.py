"""Redis-backed report cache service.

Design goals:
- All check reports (default + advanced) go through Redis db=7, TTL 24 hours
- Key is determined by SHA1 of (url, mode, tier, include_fix, extra); same key = shareable
- Values are gzip-compressed (report JSON about 27 KB, 5-10 KB compressed)
- Silently degrade when Redis is unavailable (return None / no-op on set), so the cache layer never takes down the business

Key naming convention:
    geo:report:<mode>:<sha1(url|tier|include_fix|extra)>

where mode is a visible level (`default` / `visibility` / `entity` / `citation` /
`aeo` / `authority` / `crawl-test` / `compare`), so ops can use
`SCAN 0 MATCH 'geo:report:visibility:*'` to inspect by category.

Usage:
    from geo.services.cache_service import get_cached_report, set_cached_report

    key = make_cache_key("default", url, tier="free", include_fix=True)
    cached = get_cached_report(key)
    if cached:
        return cached  # type: dict

    result = ...expensive check...
    set_cached_report(key, result, ttl_s=86400)

Degradation strategy:
- Redis unreachable / timeout / error -> log a warning once, degrade this request to "no cache"
- Exceptions must never bubble up to the business layer; even if Redis is fully down, the API still responds normally (just slower)
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
from typing import Any, Optional

import redis

_log = logging.getLogger(__name__)

# Default TTL 24 hours
DEFAULT_TTL_S = 24 * 60 * 60

# Redis connection URL. Convention is db=7 (db 0/1 belong to other apps, see docs/04-开发实现/issue_list.md and
# the discussion during this switch). Overridable via the REDIS_URL env var, convenient for switching DBs in dev/test.
_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/7")

# Global singleton Redis client. redis-py's Redis object has its own connection pool and is thread-safe.
# decode_responses=False because values are gzipped bytes.
_NO_CACHE = os.environ.get("GEO_NO_CACHE", "").strip().lower() in ("1", "true", "yes")

if _NO_CACHE:
    _client: Optional[redis.Redis] = None
    _log.info("cache_service: cache disabled via GEO_NO_CACHE=1")
else:
    try:
        _client = redis.Redis.from_url(
            _REDIS_URL,
            decode_responses=False,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
            health_check_interval=30,
        )
        # Connectivity probe; on failure keep _client, but later operations will raise RedisError -> swallowed by us
        _client.ping()
        _log.info("cache_service: Redis connected at %s", _REDIS_URL)
    except redis.RedisError as e:
        _log.warning("cache_service: Redis unavailable at %s: %s — cache disabled", _REDIS_URL, e)
        _client = None


def _stable_str(v: Any) -> str:
    """Convert any JSON-able object into a stable string (sorted keys)."""
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def make_cache_key(
    mode: str,
    url: str,
    *,
    tier: str = "free",
    include_fix: bool = True,
    extra: Any = None,
) -> str:
    """Build the cache key. Same (mode, url, tier, include_fix, extra) = same key."""
    payload = "|".join([
        url,
        tier,
        "1" if include_fix else "0",
        _stable_str(extra),
    ])
    h = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]
    return f"geo:report:{mode}:{h}"


def get_cached_report(key: str) -> Optional[dict]:
    """Returns a dict on hit, None on miss / Redis unavailable. Never raises."""
    if _client is None:
        return None
    try:
        raw = _client.get(key)
        if raw is None:
            return None
        try:
            payload = gzip.decompress(raw)
        except OSError:
            # Legacy uncompressed value
            payload = raw
        return json.loads(payload)
    except redis.RedisError as e:
        _log.warning("cache_service.get: %s — treating as miss", e)
        return None
    except (ValueError, TypeError) as e:
        # JSON parse failed, the value is corrupt; discard it
        _log.warning("cache_service.get: corrupt value for %s: %s", key, e)
        try:
            _client.delete(key)
        except redis.RedisError:
            pass
        return None


def set_cached_report(key: str, value: dict, ttl_s: int = DEFAULT_TTL_S) -> bool:
    """Write to cache. True on success / False on failure or Redis unavailable. Never raises."""
    if _client is None:
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        compressed = gzip.compress(payload, compresslevel=6)
        _client.setex(key, ttl_s, compressed)
        return True
    except redis.RedisError as e:
        _log.warning("cache_service.set: %s — skipping cache write", e)
        return False
    except (TypeError, ValueError) as e:
        _log.warning("cache_service.set: serialization failed: %s", e)
        return False


def delete_cached_report(key: str) -> bool:
    """Explicitly evict one cache entry. Returns True on success, False if Redis is unavailable."""
    if _client is None:
        return False
    try:
        _client.delete(key)
        return True
    except redis.RedisError as e:
        _log.warning("cache_service.delete: %s", e)
        return False


def is_available() -> bool:
    """Check whether Redis is currently available. For API-layer diagnostics."""
    if _client is None:
        return False
    try:
        _client.ping()
        return True
    except redis.RedisError:
        return False
