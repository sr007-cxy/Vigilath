"""WechatSync Core sidecar client used for server_pool publishing."""
from __future__ import annotations

import json
import os
import uuid
from typing import Optional
from urllib.parse import urljoin, urlsplit

import httpx

CORE_PLATFORM_ALIASES = {
    "baijia": "baijiahao",
    "wxgzh": "weixin",
    "xhs": "xiaohongshu",
}
CORE_SUPPORTED_PLATFORMS = {
    "zhihu", "baijia", "toutiao", "sohu", "csdn",
    "smzdm", "wxgzh", "xhs", "douyin", "cnblogs", "ctrip",
}
_PUBLIC_ASSET_BASE_URL = "https://<asset-base-url>/"


def _csv(name: str) -> set[str]:
    return {v.strip().lower() for v in (os.environ.get(name) or "").split(",") if v.strip()}


def _account_allowlist_by_platform() -> dict[str, set[str]]:
    raw = (os.environ.get("PUBLISH_CORE_ACCOUNT_IDS_JSON") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, set[str]] = {}
    for platform, values in parsed.items():
        if isinstance(values, (list, tuple, set)):
            out[str(platform).strip().lower()] = {
                str(v).strip() for v in values if str(v).strip()
            }
    return out


def core_platform_type(platform: str) -> str:
    code = (platform or "").strip().lower()
    return CORE_PLATFORM_ALIASES.get(code, code)


def core_account_allowlist(platform: str) -> Optional[set[str]]:
    """Return the set of account IDs allowed to publish on Core platforms.

    {"*"} means all accounts on the platform are enabled. The environment variable now only serves as an optional narrowing filter;
    empty means all accounts supported by the Core sidecar can participate.
    """
    code = (platform or "").strip().lower()
    per_platform = _account_allowlist_by_platform()
    if code in per_platform:
        return per_platform[code]
    legacy = _csv("PUBLISH_CORE_ACCOUNT_IDS")
    return legacy or {"*"}


def core_claimable_platforms() -> dict[str, Optional[set[str]]]:
    if not (os.environ.get("WECHATSYNC_PUBLISHER_URL") or "").strip():
        return {}
    out: dict[str, Optional[set[str]]] = {}
    configured = _csv("PUBLISH_CORE_PLATFORMS") or CORE_SUPPORTED_PLATFORMS
    for platform in sorted(configured & CORE_SUPPORTED_PLATFORMS):
        allowed = core_account_allowlist(platform)
        if allowed:
            out[platform] = allowed
    return out


def _real_publish_gate(platform: str) -> bool:
    key = f"{(platform or '').strip().upper()}_ALLOW_REAL_PUBLISH"
    return bool(key.strip("_")) and os.environ.get(key) == "1"


def _sidecar_headers() -> dict[str, str]:
    token = (os.environ.get("WECHATSYNC_PUBLISHER_TOKEN") or "").strip()
    return {"X-Publisher-Token": token} if token else {}


def _backend_headers() -> dict[str, str]:
    token = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    return {"X-Publish-Service-Token": token} if token else {}


def _session(req) -> dict:
    session = {
        "storage_state": req.storage_state,
        "proxy_url": req.proxy_url,
        "user_agent": req.user_agent,
        "ua_platform": req.ua_platform,
    }
    official_api = getattr(req, "official_api", None)
    if official_api:
        session["official_api"] = official_api
    return session


def _session_from_parts(*, storage_state: dict, proxy_url: Optional[str],
                        user_agent: Optional[str] = None,
                        ua_platform: Optional[str] = None,
                        official_api: Optional[dict] = None) -> dict:
    session = {
        "storage_state": storage_state,
        "proxy_url": proxy_url,
        "user_agent": user_agent,
        "ua_platform": ua_platform,
    }
    if official_api:
        session["official_api"] = official_api
    return session


def _article_images(req) -> tuple[list[str], str, str]:
    """Resolve local-upload image paths before calling the sidecar."""
    raw_images = [value.strip() for value in (req.images or []) if value.strip()]
    relative = [value for value in raw_images if not urlsplit(value).scheme]
    replacements = {
        value: urljoin(_PUBLIC_ASSET_BASE_URL, value) for value in relative
    }

    markdown = req.source_markdown or req.body
    body = req.body
    for source, absolute in replacements.items():
        markdown = markdown.replace(source, absolute)
        body = body.replace(source, absolute)
    return [replacements.get(value, value) for value in raw_images], markdown, body


def _sidecar_result(response: httpx.Response, *, commit_sent: bool) -> dict:
    """Keep the sidecar's structured business conclusion; only 5xx/non-JSON is treated as transport-level uncertainty."""
    if response.status_code >= 500:
        response.raise_for_status()
    try:
        body = response.json()
    except Exception:
        response.raise_for_status()
        raise RuntimeError("sidecar 返回了非 JSON 响应")
    if not isinstance(body, dict):
        raise RuntimeError("sidecar 响应格式错误")
    if response.status_code >= 400 and "ok" not in body:
        return {"ok": False,
                "fail_type": "submit_unknown" if commit_sent else "crash",
                "error": body.get("error") or f"sidecar HTTP {response.status_code}"}
    if body.get("draft_id") is not None:
        body = {**body, "draft_id": str(body["draft_id"])}
    return body


async def verify_identity_with_core(*, platform: str, storage_state: dict,
                                    proxy_url: Optional[str],
                                    user_agent: Optional[str] = None,
                                    ua_platform: Optional[str] = None,
                                    expected_account_id: Optional[str] = None) -> dict:
    """Ask WechatSync sidecar to read identity with the same adapter used for publish."""
    base = (os.environ.get("WECHATSYNC_PUBLISHER_URL") or "").rstrip("/")
    if not base:
        return {"ok": False, "account_id": None, "account_handle": None,
                "exit_ip": None, "fail_type": "crash",
                "error": "WECHATSYNC_PUBLISHER_URL 未配置"}
    body = {
        "platform": platform,
        "plugin_type": core_platform_type(platform),
        "expected_account_id": expected_account_id,
        "session": _session_from_parts(storage_state=storage_state, proxy_url=proxy_url,
                                       user_agent=user_agent, ua_platform=ua_platform),
    }
    try:
        async with httpx.AsyncClient(timeout=float(
                os.environ.get("WECHATSYNC_PUBLISHER_TIMEOUT", "300"))) as client:
            response = await client.post(f"{base}/v1/auth/verify", json=body,
                                         headers=_sidecar_headers())
            result = _sidecar_result(response, commit_sent=False)
    except Exception as exc:
        return {"ok": False, "account_id": None, "account_handle": None,
                "exit_ip": None, "fail_type": "crash",
                "error": f"Core auth verify failed:{type(exc).__name__}: {exc}"}
    if (result or {}).get("ok") and expected_account_id:
        observed = result.get("account_id")
        if observed != expected_account_id:
            return {**result, "ok": False, "fail_type": "identity_mismatch",
                    "error": "当前登录身份与期望账号不一致"}
    return result


async def verify_article_with_core(*, platform: str, external_post_id: str,
                                   storage_state: dict, proxy_url: Optional[str],
                                   expected_account_id: str,
                                   user_agent: Optional[str] = None,
                                   ua_platform: Optional[str] = None) -> dict:
    """Query Ctrip review status using the same Core adapter and account login session as publishing."""
    base = (os.environ.get("WECHATSYNC_PUBLISHER_URL") or "").rstrip("/")
    if not base:
        return {"ok": False, "status": "query_error",
                "error": "WECHATSYNC_PUBLISHER_URL 未配置"}
    body = {
        "platform": platform,
        "external_post_id": external_post_id,
        "expected_account_id": expected_account_id,
        "session": _session_from_parts(
            storage_state=storage_state, proxy_url=proxy_url,
            user_agent=user_agent, ua_platform=ua_platform),
    }
    try:
        async with httpx.AsyncClient(timeout=float(
                os.environ.get("WECHATSYNC_PUBLISHER_TIMEOUT", "300"))) as client:
            response = await client.post(f"{base}/v1/articles/status", json=body,
                                         headers=_sidecar_headers())
            return _sidecar_result(response, commit_sent=False)
    except Exception as exc:
        return {"ok": False, "status": "query_error",
                "error": f"Core article status failed:{type(exc).__name__}: {exc}"}


async def _persist_progress(req, prepared: dict) -> None:
    base = (os.environ.get("PUBLISH_DISPATCH_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError("PUBLISH_DISPATCH_URL 未配置,不能在 commit 前持久化 draft_id")
    if req.dispatch_id is None or req.worker_id is None:
        raise RuntimeError("Core 真实发布必须携带 dispatch_id 与 worker_id")
    body = {"dispatch_id": req.dispatch_id, "worker_id": req.worker_id,
            "draft_id": prepared["draft_id"],
            "draft_url": prepared.get("draft_url"),
            "screenshot_path": prepared.get("screenshot_path")}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{base}/publish-dispatch/progress", json=body,
                                     headers=_backend_headers())
        response.raise_for_status()


async def publish_with_core(req) -> dict:
    """Strictly run prepare -> progress -> commit; never switch back to Python at any stage."""
    base = (os.environ.get("WECHATSYNC_PUBLISHER_URL") or "").rstrip("/")
    if not base:
        return {"ok": False, "fail_type": "crash",
                "error": "WECHATSYNC_PUBLISHER_URL 未配置"}
    operation_key = (f"{req.dispatch_id}:{req.attempt or 0}" if req.dispatch_id is not None
                     else f"manual:{uuid.uuid4()}")
    plugin_type = core_platform_type(req.platform)
    common = {"platform": req.platform, "expected_account_id": req.account_id,
              "plugin_type": plugin_type,
              "session": _session(req)}
    images, markdown, body = _article_images(req)
    prepare_body = {**common, "operation_key": operation_key,
                    "article": {"title": req.title,
                                "markdown": markdown,
                                "html": body if req.body_format == "html" else None,
                                "images": images,
                                "location": getattr(req, "location", None),
                                "topics": getattr(req, "topics", None)}}
    timeout = float(os.environ.get("WECHATSYNC_PUBLISHER_TIMEOUT", "300"))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base}/v1/operations/prepare",
                                         json=prepare_body, headers=_sidecar_headers())
            prepared = _sidecar_result(response, commit_sent=False)
            if not prepared.get("ok"):
                return {**prepared, "failure_phase": "prepare",
                        "confirmed_not_published": True}
            draft_id = prepared.get("draft_id")
            if not draft_id:
                return {"ok": False, "fail_type": "crash",
                        "failure_phase": "prepare", "confirmed_not_published": True,
                        "error": "Core prepare 未返回 draft_id"}
            evidence = {"draft_id": draft_id,
                        "draft_url": prepared.get("draft_url"),
                        "screenshot_path": prepared.get("screenshot_path"),
                        "exit_ip": prepared.get("exit_ip"),
                        "observed_account_id": prepared.get("observed_account_id")}
            if req.dry_run:
                return {"ok": True, "published_url": None, "url_source": None,
                        "storage_state": prepared.get("storage_state"), **evidence}

            try:
                await _persist_progress(req, prepared)  # stop on failure, never call commit
            except Exception as exc:
                return {"ok": False, "fail_type": "crash", **evidence,
                        "failure_phase": "pre_commit", "confirmed_not_published": True,
                        "error": f"Core progress 持久化失败:{type(exc).__name__}: {exc}"}
            operation_id = prepared.get("operation_id")
            commit_session = dict(common["session"])
            if prepared.get("storage_state"):
                # prepare may receive a platform-rotated Cookie; commit of the same operation prefers the new state.
                commit_session["storage_state"] = prepared["storage_state"]
            commit_body = {**common, "session": commit_session,
                           "article_title": req.title,
                           "article": prepare_body["article"],
                           "existing_publish_gate": _real_publish_gate(req.platform)}
            try:
                committed = await client.post(
                    f"{base}/v1/operations/{operation_id}/commit",
                    json=commit_body, headers=_sidecar_headers())
                result = _sidecar_result(committed, commit_sent=True)
                if not result.get("ok"):
                    if result.get("fail_type") == "submit_unknown":
                        result = {**result, "failure_phase": "commit",
                                  "confirmed_not_published": False}
                    elif result.get("confirmed_not_published"):
                        result = {**result,
                                  "failure_phase": result.get("failure_phase") or "commit",
                                  "confirmed_not_published": True}
                    else:
                        result = {**result, "ok": False, "fail_type": "submit_unknown",
                                  "failure_phase": "commit",
                                  "confirmed_not_published": False,
                                  "error": (result.get("error") or
                                            "Core commit 失败但未确认平台未发布")}
                return {**evidence, **result,
                        "screenshot_path": (result.get("screenshot_path")
                                            or evidence["screenshot_path"])}
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                # The request definitely was not delivered and the platform commit was not touched; the backend may retry per its crash policy.
                return {"ok": False, "fail_type": "crash", **evidence,
                        "failure_phase": "pre_commit", "confirmed_not_published": True,
                        "error": f"Core commit 请求未送达:{type(exc).__name__}: {exc}"}
            except Exception as exc:  # the request may have been delivered; the result is unknown by default
                return {"ok": False, "fail_type": "submit_unknown", **evidence,
                        "failure_phase": "commit", "confirmed_not_published": False,
                        "error": f"Core commit 已发出但未拿到结论:{type(exc).__name__}: {exc}"}
    except Exception as exc:
        # prepare at most produces an unpublished draft and never publishes externally twice; treat as an engineering failure.
        return {"ok": False, "fail_type": "crash",
                "failure_phase": "prepare", "confirmed_not_published": True,
                "error": f"Core prepare failed:{type(exc).__name__}: {exc}"}


async def fetch_core_screenshot(platform: str, filename: str) -> tuple[bytes, str]:
    base = (os.environ.get("WECHATSYNC_PUBLISHER_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError("WECHATSYNC_PUBLISHER_URL 未配置")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base}/v1/screenshots/{platform}/{filename}",
                                    headers=_sidecar_headers())
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "image/png")
