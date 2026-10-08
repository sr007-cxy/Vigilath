"""Meijiexing agency API client (signing + HTTP + error classification).

API docs: docs/04-开发实现/内容与发布/媒体API/媒介星API接口(1).pdf, product plan: docs/04-开发实现/内容与发布/媒体API/产品文档-媒介星发文对接.md.

Protocol notes:
  - All HTTP POST, application/x-www-form-urlencoded, utf-8
  - Common params secret_id / timestamp (10-digit seconds) / signature
  - Signature: all params except signature sorted by name in ASCII ascending order joined as a=2&b=3,
    with &key=<secret_key> appended, MD5 then uppercased
  - Returns {code, msg, data}, code "200" success / "201" failure (measured: code may be int or str,
    always compared as str)
  - The order title must be urlencoded separately before signing (the other side verifies against the literal value received)

Controls (the parts implemented in the client layer):
  - Missing keys raise MjxConfigError immediately without sending
  - create_*_order is a paid action, additionally gated by settings.MJX_PUBLISH_ENABLED;
    query APIs (balance/media library/order status) are not gated and can be called any time
  - Global concurrency Semaphore(2) + 15s timeout
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from geo.database import settings

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15.0  # The other side is an old PHP site; leave plenty of headroom
_CONCURRENCY = asyncio.Semaphore(2)

# Order status codes (status returned by query_*_order)
ORDER_STATUS = {
    "-2": "deleted",      # Deleted
    "-1": "rejected",     # Rejected
    "0": "pending",       # Not processed
    "1": "publishing",    # Publishing
    "2": "published",     # Completed
}


class MjxError(Exception):
    """Common base class for Meijiexing call failures; msg keeps the remote original text."""

    def __init__(self, message: str, *, code: str | None = None, raw: Any = None):
        super().__init__(message)
        self.code = code
        self.raw = raw


class MjxConfigError(MjxError):
    """Local configuration problem (missing key / order gate not enabled); should not be retried."""


class MjxApiError(MjxError):
    """Business failure with code=201 from the remote (bad signature/duplicate order/insufficient balance/media invalid, etc.)."""


def _sign(params: dict[str, Any], secret_key: str) -> str:
    """ASCII ascending -> a=2&b=3 -> append &key= -> uppercase MD5. Skips empty values and signature itself."""
    parts = [
        f"{k}={v}"
        for k, v in sorted(params.items())
        if k != "signature" and v is not None and v != ""
    ]
    raw = "&".join(parts) + f"&key={secret_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


class MjxClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        secret_id: str | None = None,
        secret_key: str | None = None,
    ):
        self.base_url = (base_url or settings.MJX_API_BASE).rstrip("/")
        self.secret_id = secret_id if secret_id is not None else settings.MJX_SECRET_ID
        self.secret_key = secret_key if secret_key is not None else settings.MJX_SECRET_KEY
        if not self.secret_id or not self.secret_key:
            raise MjxConfigError("MJX_SECRET_ID / MJX_SECRET_KEY 未配置,媒介星渠道未开通")

    # ---------- Low level ----------

    async def _post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        body: dict[str, Any] = dict(params or {})
        body["secret_id"] = self.secret_id
        body["timestamp"] = int(time.time())
        body["signature"] = _sign(body, self.secret_key)
        url = f"{self.base_url}{path}"
        async with _CONCURRENCY:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(url, data=body)
        try:
            payload = resp.json()
        except ValueError as e:
            # On the remote's ThinkPHP error page, try to extract the <h1> exception summary for easier diagnosis
            import re

            m = re.search(r"<h1[^>]*>(.*?)</h1>", resp.text, re.S)
            detail = re.sub(r"\s+", " ", m.group(1)).strip() if m else resp.text[:300]
            raise MjxError(
                f"媒介星返回非 JSON(HTTP {resp.status_code}): {detail[:300]}"
            ) from e
        code = str(payload.get("code", ""))
        if code != "200":
            raise MjxApiError(str(payload.get("msg", "未知错误")), code=code, raw=payload)
        return payload.get("data")

    # ---------- Queries (not order-gated) ----------

    async def user_info(self) -> dict:
        """Balance query -> {money, username, level}."""
        return await self._post("/meijieapi/daili3/userInfo")

    async def media_list(
        self,
        *,
        page: int = 1,
        limit: int = 200,
        media_id: int | None = None,
        uptime: int | None = None,
    ) -> Any:
        """News media list; media_id for a single item, uptime as a second-level timestamp for increments. limit range 200~1000."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if media_id is not None:
            params["id"] = media_id
        if uptime is not None:
            params["uptime"] = uptime
        return await self._post("/meijieapi/daili3/media_lst", params)

    async def wmedia_list(self, *, page: int = 1, limit: int = 200) -> Any:
        """Self-media list."""
        return await self._post("/meijieapi/daili3/wmedia_lst", {"page": page, "limit": limit})

    async def get_ids(self, *, type_: int, status: int = 1) -> list:
        """Set of listed resource ids (for reconciliation). type: 1 media / 2 self-media / 6 short video."""
        return await self._post("/meijieapi/daili3/get_ids", {"status": status, "type": type_})

    async def query_media_order(self, nos: list[str]) -> Any:
        """News media order status; nos is our list of order numbers (<= 50 per batch recommended)."""
        return await self._post("/meijieapi/daili3/query_media_order", {"nostr": ",".join(nos)})

    async def query_wmedia_order(self, nos: list[str]) -> Any:
        """Self-media order status. Note: this API's base path differs from the others (as per the remote's docs)."""
        return await self._post("/api/wemedia/query_wmedia_order", {"nostr": ",".join(nos)})

    # ---------- Orders (paid, gated by MJX_PUBLISH_ENABLED) ----------

    def _order_params(
        self,
        *,
        title: str,
        preview_url: str,
        mid: int,
        no: str,
        remark: str,
        saling_price: str,
        media_name: str = "",
    ) -> dict[str, Any]:
        if not settings.MJX_PUBLISH_ENABLED:
            raise MjxConfigError("MJX_PUBLISH_ENABLED 未开启,拒绝真实下单(付费动作)")
        return {
            # The docs require title to be urlencoded separately (the signature is computed on the encoded literal)
            "title": quote(title, safe=""),
            "content": f'<a href="{preview_url}">{preview_url}</a>',
            "mid": mid,
            # Not in the PDF but the remote server always reads it; without it a 500 "未定义数组索引: media_name" ("undefined array index: media_name").
            # Pass the original media_name / wemedia_name from the media list.
            "media_name": media_name,
            "no": no,
            "remark": remark,
            "saling_price": saling_price,
        }

    async def create_media_order(
        self, *, title: str, preview_url: str, mid: int, no: str, remark: str,
        saling_price: str, media_name: str = "",
    ) -> Any:
        """Place a news media order. no is the idempotency key; retries must reuse the same no."""
        return await self._post(
            "/meijieapi/daili3/create_media_order",
            self._order_params(
                title=title, preview_url=preview_url, mid=mid, no=no,
                remark=remark, saling_price=saling_price, media_name=media_name,
            ),
        )

    async def create_wmedia_order(
        self, *, title: str, preview_url: str, mid: int, no: str, remark: str,
        saling_price: str, media_name: str = "",
    ) -> Any:
        """Place a self-media order."""
        return await self._post(
            "/meijieapi/daili3/create_wmedia_order",
            self._order_params(
                title=title, preview_url=preview_url, mid=mid, no=no,
                remark=remark, saling_price=saling_price, media_name=media_name,
            ),
        )
