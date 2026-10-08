"""Publish adapter registry.

Follows the ENGINE_MODULE_MAP at `app/main.py:38` - a hard-coded string table + importlib lazy loading,
no automatic directory scanning. Same reason as the engine side: an explicit table makes "is a platform wired up" checkable at a glance,
while automatic scanning could accidentally turn a half-finished file into an available platform.

Adding a new platform = write `publishers/<code>.py` + add one line here. Platform **capability parameters**
(title length limit/image count/body format) are not in code but in the `publish_platforms` config table.
"""
from __future__ import annotations

import importlib
import logging
from typing import Optional

from .base import (  # noqa: F401 - makes `from app.publishers import PublisherAdapter` work
    FAIL_BANNED,
    FAIL_CONTENT_REJECTED,
    FAIL_CRASH,
    FAIL_DOM_NOT_FOUND,
    FAIL_IDENTITY_MISMATCH,
    FAIL_LOGIN_LOST,
    FAIL_NOT_IMPLEMENTED,
    FAIL_RATE_LIMITED,
    FAIL_SUBMIT_UNKNOWN,
    FAIL_TIMEOUT,
    PublisherAdapter,
    PublishRequest,
    PublishResult,
    account_hash,
)

_log = logging.getLogger(__name__)

PUBLISHER_MODULE_MAP: dict[str, str] = {
    # server_pool publishing is handed entirely to the WechatSync Core sidecar.
    "zhihu": "app.publishers.core_backed:ZhihuPublisher",
    "baijia": "app.publishers.core_backed:BaijiaPublisher",
    "toutiao": "app.publishers.core_backed:ToutiaoPublisher",
    "sohu": "app.publishers.core_backed:SohuPublisher",
    "csdn": "app.publishers.core_backed:CsdnPublisher",
    "smzdm": "app.publishers.core_backed:SmzdmPublisher",
    "wxgzh": "app.publishers.core_backed:WxgzhPublisher",
    "xhs": "app.publishers.core_backed:XhsPublisher",
    "douyin": "app.publishers.core_backed:DouyinPublisher",
    "cnblogs": "app.publishers.core_backed:CnblogsPublisher",
    "ctrip": "app.publishers.core_backed:CtripPublisher",
}

_cache: dict[str, PublisherAdapter] = {}


def supported_platforms() -> list[str]:
    return sorted(PUBLISHER_MODULE_MAP)


def get_publisher(platform: str) -> Optional[PublisherAdapter]:
    """Get the adapter singleton by platform code; returns None if unregistered / failed to load (the caller turns it into 400).

    Load failures are only logged, not raised - consistent with _load_adapters: one bad adapter should not keep the whole
    service from starting / take down publishing for other platforms.
    """
    code = (platform or "").strip().lower()
    if not code:
        return None
    if code in _cache:
        return _cache[code]
    spec = PUBLISHER_MODULE_MAP.get(code)
    if not spec:
        return None
    try:
        mod_path, cls_name = spec.rsplit(":", 1)
        cls = getattr(importlib.import_module(mod_path), cls_name)
        _cache[code] = cls()
        return _cache[code]
    except Exception:  # noqa: BLE001
        _log.exception("[publishers] failed to load adapter for %s (%s)", code, spec)
        return None
