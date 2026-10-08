"""Baijiahao account identity and web login adapter.

Real publishing is executed by the WechatSync Core sidecar; this adapter only handles account center login, health checks and
browser-service platform capability registration, avoiding two publish implementations maintained in Python and the plugin.
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import (FAIL_NOT_IMPLEMENTED, PublisherAdapter, PublishRequest,
                   PublishResult, account_hash)

_log = logging.getLogger(__name__)
APPINFO_API = "https://baijiahao.baidu.com/builder/app/appinfo"


class BaijiaPublisher(PublisherAdapter):
    platform = "baijia"
    login_url = "https://baijiahao.baidu.com/"
    home_url = "https://baijiahao.baidu.com/"

    async def _appinfo_user(self, page) -> Optional[dict]:
        try:
            response = await page.request.get(APPINFO_API, timeout=15_000)
            if response.status != 200:
                return None
            payload = await response.json()
            user = ((payload or {}).get("data") or {}).get("user") or {}
            if (payload or {}).get("errmsg") != "success" or not isinstance(user, dict):
                return None
            return user
        except Exception as exc:  # noqa: BLE001
            _log.warning("[baijia] appinfo identity probe failed: %s", exc)
            return None

    async def verify_identity(self, page) -> Optional[str]:
        user = await self._appinfo_user(page)
        if not user:
            return None
        try:
            value = str(user.get("userid") or "").strip()
            if not value:
                return None
            return account_hash("api.appinfo.userid", value)
        except Exception as exc:  # noqa: BLE001
            _log.warning("[baijia] appinfo identity probe failed: %s", exc)
            return None

    async def account_handle(self, page) -> Optional[str]:
        """Read the Baijiahao account nickname, only for account center display, not used in account_id comparison."""
        user = await self._appinfo_user(page)
        if not user:
            return None
        for field in ("name", "nickname", "display_name", "app_name", "username", "userid"):
            value = user.get(field)
            text = str(value).strip() if isinstance(value, (str, int)) else ""
            if text:
                return text[:64]
        return None

    async def publish(self, page, req: PublishRequest) -> PublishResult:
        return PublishResult(
            ok=False,
            fail_type=FAIL_NOT_IMPLEMENTED,
            error="百家号真实发布必须通过 WechatSync Core sidecar",
            confirmed_not_published=True,
            failure_phase="pre_commit",
        )

    async def is_available(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(self.home_url)
            return response.status_code < 500
        except Exception:  # noqa: BLE001
            return False
