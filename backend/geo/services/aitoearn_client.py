"""aitoearn multi-platform distribution API client (protocol layer).

aitoearn (https://aitoearn.cn, open source: github.com/yikart/AiToEarn) is a multi-platform content
distribution SaaS: users authorize their social accounts (WeChat official account/Xiaohongshu/Douyin, etc.) in its backend, and the open API
creates a publish flow in one request; after async publishing we poll for the post link.

Protocol notes (verified against API docs docs.aitoearn.cn + open-source backend source):
  - Auth: header `X-Api-Key`, key bound to an aitoearn account
  - Response envelope {code, message, data, requestId}, code=0 means success
  - publishAt in POST /api/v2/channels/publish/flows is required (docs say optional, but in source
    CreatePublishFlowSchema z.coerce.date() has no .optional() -- source wins)
  - content.media / cover urls only accept the assets.aitoearn.cn domain -> images must first go through
    uploadSign -> direct upload to object storage -> confirm (wrapped in upload_asset, parsing centralized in one place)
  - GET /api/v2/channels/publish/flows/{flowId} fetches details by our external flowId,
    tasks[] directly contains status / platformWorkId / workLink / linkStatus / errorMsg

Numeric publish status enum (open source libs/mongodb/src/enums/publish.enum.ts):
  -1 Failed / 0 WaitingForPublish / 1 Published / 2 Publishing / 3 WaitingForUpdate
  / 4 Updating / 5 UpdatedFailed / 6 Queued / 7 PlatformScheduled
  / 8 WaitingForUserAction / 9 Canceled

Controls: raises AitoearnConfigError without sending if the key is not configured; global concurrency Semaphore(3) + 20s timeout.
Free channel (publishes to self-authorized accounts), no payment gate.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from typing import Any, Optional

import httpx

from geo.database import settings

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20.0
_UPLOAD_TIMEOUT = 60.0    # Direct upload to object storage; images may be several MB

# Publish task numeric status enum -> semantics (verified against source, see module docstring)
PUBLISH_STATUS = {
    -1: "failed",
    0: "waiting",
    1: "published",
    2: "publishing",
    3: "waiting_update",
    4: "updating",
    5: "update_failed",
    6: "queued",
    7: "platform_scheduled",
    8: "waiting_user_action",
    9: "canceled",
}


class AitoearnError(Exception):
    """Common base class for aitoearn call failures; message keeps the remote original text."""

    def __init__(self, message: str, *, code: Any = None,
                 request_id: str | None = None, raw: Any = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.raw = raw


class AitoearnConfigError(AitoearnError):
    """Local configuration problem (AITOEARN_API_KEY missing); should not be retried."""


class AitoearnApiError(AitoearnError):
    """Business failure with code!=0 from the remote (auth error/bad params/account invalid, etc.)."""


class AitoearnClient:
    def __init__(self, *, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.AITOEARN_API_BASE).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.AITOEARN_API_KEY
        if not self.api_key:
            raise AitoearnConfigError("AITOEARN_API_KEY 未配置,aitoearn 通道未开通")
        # The concurrency gate lives on the instance, not the module: get_client() creates a new one each time, so the semaphore is never reused across event loops
        # (the uvicorn main loop and the poller's asyncio.run are two loops; sharing would error out)
        self._sem = asyncio.Semaphore(3)

    # ---------- Low level ----------

    async def _request(self, method: str, path: str, *,
                       json_body: Any = None, params: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        async with self._sem:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.request(
                    method, url, json=json_body, params=params,
                    headers={"X-Api-Key": self.api_key},
                )
        try:
            payload = resp.json()
        except ValueError as e:
            raise AitoearnError(
                f"aitoearn 返回非 JSON(HTTP {resp.status_code}): {resp.text[:300]}"
            ) from e
        code = payload.get("code")
        if code != 0:
            message = str(payload.get("message") or "未知错误")
            # Aitoearn validation errors usually put field-level reasons in data/error/details;
            # showing only message degrades into a generic "Validation failed".
            detail = payload.get("data")
            if detail in (None, "", [], {}):
                detail = payload.get("error") or payload.get("details")
            if detail not in (None, "", [], {}):
                try:
                    rendered = json.dumps(detail, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    rendered = str(detail)
                if rendered and rendered not in message:
                    message = f"{message}: {rendered[:1200]}"
            if payload.get("requestId"):
                message += f" (requestId: {payload['requestId']})"
            raise AitoearnApiError(
                message,
                code=code, request_id=payload.get("requestId"), raw=payload,
            )
        return payload.get("data")

    # ---------- Accounts / platform metadata ----------

    async def list_accounts(self) -> list[dict]:
        """List of authorized accounts (fetched in full; filtering happens in the adapter layer)."""
        data = await self._request("GET", "/api/v2/channels/accounts") or {}
        rows = data.get("list") if isinstance(data, dict) else data
        return [r for r in (rows or []) if isinstance(r, dict)]

    async def start_account_auth(self, platform: str) -> dict:
        data = await self._request(
            "GET", f"/api/v2/channels/accounts/auth/{platform}")
        if not isinstance(data, dict) or not data.get("sessionId") or not data.get("url"):
            raise AitoearnApiError("授权接口返回缺少 sessionId 或 url", raw=data)
        return data

    async def get_account_auth_status(self, platform: str, session_id: str) -> dict:
        data = await self._request(
            "GET", f"/api/v2/channels/accounts/auth/{platform}/status/{session_id}")
        return data if isinstance(data, dict) else {}

    async def submit_account_auth_selections(
        self, session_id: str, accounts: list[dict],
    ) -> None:
        """Complete multi-account auth using the upstream session cookie.

        The upstream selection endpoint renders HTML instead of its normal JSON
        envelope, so this call intentionally checks only the HTTP status.
        """
        async with self._sem:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v2/channels/accounts/auth/selections",
                    json={"accounts": accounts},
                    headers={"X-Api-Key": self.api_key, "Cookie": f"_cas={session_id}"},
                )
        if resp.status_code >= 300:
            raise AitoearnApiError(
                f"提交授权账号选择失败(HTTP {resp.status_code}): {resp.text[:300]}")

    async def delete_account(self, account_id: str) -> None:
        await self._request("DELETE", f"/api/v2/channels/accounts/{account_id}")

    async def list_platforms(self) -> list[dict]:
        """Platform metadata: contentLimits (modes/word count/image count), editor, optionSchema, etc."""
        data = await self._request("GET", "/api/v2/channels/platforms")
        return [p for p in (data or []) if isinstance(p, dict)]

    # ---------- Publish flow ----------

    async def create_flow(self, *, flow_id: str, content: dict,
                          items: list[dict], publish_at: str) -> Any:
        """Create a publish flow. publish_at is required (ISO 8601); each item in items is
        {platform, accountId, option?, overrides?}."""
        return await self._request(
            "POST", "/api/v2/channels/publish/flows",
            json_body={
                "flowId": flow_id,
                "content": content,
                "publishAt": publish_at,
                "items": items,
            },
        )

    async def get_flow(self, flow_id: str) -> dict:
        """Flow details: {flowId, tasks:[{status, platformWorkId, workLink,
        linkStatus, linkError, errorMsg, ...}]}."""
        data = await self._request(
            "GET", f"/api/v2/channels/publish/flows/{flow_id}")
        return data if isinstance(data, dict) else {}

    async def list_records(self, *, account_id: str | None = None,
                           account_type: str | None = None,
                           status: int | None = None) -> list[dict]:
        """Publish history records (fallback for troubleshooting; normal reconciliation uses get_flow)."""
        params: dict[str, Any] = {}
        if account_id:
            params["accountId"] = account_id
        if account_type:
            params["accountType"] = account_type
        if status is not None:
            params["status"] = status
        data = await self._request(
            "GET", "/api/v2/channels/publish/records", params=params)
        rows = data.get("list") if isinstance(data, dict) else data
        return [r for r in (rows or []) if isinstance(r, dict)]

    # ---------- Asset upload (uploadSign -> direct upload -> confirm) ----------

    async def upload_asset(self, data: bytes, filename: str,
                           content_type: Optional[str] = None) -> str:
        """Move a file into assets.aitoearn.cn and return the final usable URL.

        Three steps: POST /api/assets/uploadSign to get {id, url, uploadUrl, uploadFields}
        -> direct upload to object storage (POST form if uploadFields present, else PUT raw bytes)
        -> POST /api/assets/{id}/confirm. Field assumptions are centralized in this method; fix based on real tests after first release.
        """
        content_type = content_type or mimetypes.guess_type(filename)[0] \
            or "application/octet-stream"
        sign = await self._request(
            "POST", "/api/assets/uploadSign",
            json_body={"filename": filename, "type": "publishMedia",
                       "size": len(data)},
        )
        if not isinstance(sign, dict) or not sign.get("id"):
            raise AitoearnApiError("uploadSign 返回缺少资源 id", raw=sign)
        upload_url = sign.get("uploadUrl")
        if upload_url:
            async with self._sem:
                async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
                    fields = sign.get("uploadFields")
                    if isinstance(fields, dict) and fields:
                        resp = await client.post(
                            upload_url, data=fields,
                            files={"file": (filename, data, content_type)},
                        )
                    else:
                        resp = await client.put(
                            upload_url, content=data,
                            headers={"Content-Type": content_type},
                        )
            if resp.status_code >= 300:
                raise AitoearnError(
                    f"素材直传失败(HTTP {resp.status_code}): {resp.text[:200]}")
        confirmed = await self._request(
            "POST", f"/api/assets/{sign['id']}/confirm")
        url = None
        if isinstance(confirmed, dict):
            url = confirmed.get("url")
        url = url or sign.get("url")
        if not url:
            raise AitoearnApiError("素材上传后未取得最终 URL", raw=confirmed)
        return str(url)


def get_client() -> AitoearnClient:
    """No session state; just create a new one each time (key validated in the constructor)."""
    return AitoearnClient()


def configured() -> bool:
    return bool(settings.AITOEARN_API_KEY)
