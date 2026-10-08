"""Publishing account pool API — register / list / rebind / pool level / check-out / check-in.

The protocol copies `api/engine_sessions.py` (689 lines), with three deliberate tightenings (plan §3.4):

|            | Engine pool                     | Publishing pool |
|------------|--------------------------------|--------|
| Account choice | Load-balanced by use_count ASC | 🔴 caller specifies platform+account_id, no picking |
| Lease granularity | Worker-sticky, claimable by others after expiry | 🔴 account bound to a worker for life, **no preemption** |
| check-in   | Reports fail_type               | 🔴 must also report exit_ip; drift stops the account |

## Two routers, two auth schemes

`router`         JWT + require_roles("admin","maintainer"), for the platform ops console.
`require_publish_service_token` service token X-Publish-Service-Token, for workers
                 (workers sit behind residential networks and have no JWT, nor should they). The worker endpoints themselves are in
                 `api/publish_dispatch.py` — see below.

## Only one lease path

The **only** account claim entry is `POST /api/publish-dispatch/claim` (task-driven). This module deliberately
**does not provide** `/publish-pool/check-out`: the publishing pool does not "load-balance pick an account"; the account is specified by the task
(plan §3.4). Two lease paths would mean two places deciding quota and leases, and any definition mismatch means duplicate publishing.

## 🔴 Never leaked

**The ciphertexts** of storage_state / credentials never leave the backend. List endpoint serialization goes through
`models/publish_accounts.py::account_to_dict`, which has neither field;
claim is the only place plaintext login sessions are handed out, and only to workers holding the service token.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Annotated, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import require_roles
from geo.models.engine_sessions import EngineProxyORM, _account_hash
from geo.models.publish_accounts import (
    ALL_CHANNELS,
    ALL_EGRESS,
    CHANNEL_SERVER_POOL,
    EGRESS_HOST,
    EGRESS_PROXY,
    PublishAccountORM,
    PublishDispatchTaskORM,
    PublishAccountRebindIn,
    PublishAccountRegisterIn,
    PublishPlatformORM,
    PublishPoolStatusEntry,
    account_to_dict,
    effective_status,
    has_login_cookies,
    provisional_account_id,
    stable_account_id_from_probe,
)
from geo.utils.publish_crypto import PublishCryptoUnavailable, encrypt_required

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_roles("admin", "maintainer"))])


# ── Configuration ────────────────────────────────────────────────────────────

# Default per-account daily publishing cap (0 = unlimited). The account pool allows unlimited queuing and no longer truncates by day by default.
# env PUBLISH_DAILY_CAP_<PLATFORM> overrides; the per-account daily_cap column overrides env.
_DEFAULT_DAILY_CAP = 0
# Default daily cap during warm-up (the manual → automatic transition period).
WARMUP_DAILY_CAP = int(os.environ.get("PUBLISH_WARMUP_DAILY_CAP", "0"))
# Minimum interval (seconds) between two publishes of one account. Default 1 minute — 🔴 equal intervals are themselves a bot signature, so this is only
# a lower bound; real scheduling jitter is in step 9.
MIN_GAP_SEC = int(os.environ.get("PUBLISH_MIN_GAP_SEC", "60"))
# Days to extend expires_at after a successful publish. 🔴 **Counted from the last successful use**, not from capture —
# the engine pool already implements this semantics (models/engine_sessions.py:289-292); a hard TTL expiry would wrongly kill good accounts
# (6 of 8 Wenxin accounts died this way, see docs/06-发布运维/运维/排障-账号池误判与TTL死锁.md).
RENEW_TTL_DAYS = int(os.environ.get("PUBLISH_RENEW_TTL_DAYS", "14"))
# Lease duration. When a worker claims a task but its process is killed, this releases it automatically.
LEASE_TTL_MINUTES = int(os.environ.get("PUBLISH_LEASE_TTL_MINUTES", "30"))

BROWSER_SERVICE_URL = os.environ.get("BROWSER_SERVICE_AUTHORIZE_URL",
                                     "http://127.0.0.1:8092").rstrip("/")
BROWSER_VERIFY_TIMEOUT = float(os.environ.get("PUBLISH_VERIFY_HTTP_TIMEOUT", "120"))
PASSWORD_LOGIN_PLATFORMS = {"zhihu"}
WEB_LOGIN_PLATFORMS = {"zhihu", "baijia"}


def _browser_publish_capabilities() -> dict:
    """Read browser-service publish capabilities.

    Failure is non-fatal for the account-center catalog: the DB still owns the
    business platform list, while browser-service only answers which actions can
    be performed right now.
    """
    headers = {}
    tok = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if tok:
        headers["X-Publish-Service-Token"] = tok
    try:
        with httpx.Client(timeout=min(BROWSER_VERIFY_TIMEOUT, 10.0)) as c:
            r = c.get(f"{BROWSER_SERVICE_URL}/publish/platforms", headers=headers)
        if r.status_code != 200:
            return {
                "available": False,
                "note": f"browser-service /publish/platforms HTTP {r.status_code}",
                "platforms": set(),
                "password_login_platforms": set(),
                "web_login_platforms": set(),
                "claimable_platforms": set(),
            }
        data = r.json() or {}
        return {
            "available": True,
            "note": None,
            "platforms": {str(x) for x in data.get("platforms") or []},
            "password_login_platforms": {
                str(x) for x in data.get("password_login_platforms") or []
            },
            "web_login_platforms": {
                str(x) for x in data.get("web_login_platforms") or []
            },
            "claimable_platforms": {
                str(x) for x in data.get("claimable_platforms") or []
            },
        }
    except Exception as e:  # noqa: BLE001
        return {
            "available": False,
            "note": f"browser-service 发布能力探测失败:{type(e).__name__}",
            "platforms": set(),
            "password_login_platforms": set(),
            "web_login_platforms": set(),
            "claimable_platforms": set(),
        }


def platform_daily_cap(platform: str) -> int:
    ov = os.environ.get(f"PUBLISH_DAILY_CAP_{platform.upper()}")
    if ov and ov.isdigit():
        return int(ov)
    return _DEFAULT_DAILY_CAP


def local_today() -> str:
    """Local (Beijing) date string, used to reset daily usage. Same definition as the engine pool's _local_today."""
    return (datetime.utcnow() + timedelta(hours=8)).date().isoformat()


def effective_cap(row: PublishAccountORM, now: Optional[datetime] = None) -> int:
    """The account's daily cap right now; 0 = unlimited.

    During warm-up take min(normal cap, WARMUP_DAILY_CAP) — but **"unlimited" is not limited this way**:
    daily_cap=0 means unlimited, and a plain min would yield 0, interpreted again as unlimited, making warm-up pointless.
    """
    now = now or datetime.utcnow()
    base = row.daily_cap if row.daily_cap is not None else platform_daily_cap(row.platform)
    warming = effective_status(row, now) == "warming"
    if not warming:
        return base
    warmup_cap = row.warmup_daily_cap
    if warmup_cap is None:
        warmup_cap = WARMUP_DAILY_CAP
    if warmup_cap == 0:
        return 0
    if base == 0:
        return warmup_cap
    return min(base, warmup_cap)


def effective_cooldown_minutes(row: PublishAccountORM) -> int:
    """Minimum interval between two publishes of an account; the account value takes precedence, empty falls back to global config."""
    if row.cooldown_minutes is not None:
        return row.cooldown_minutes
    return max(0, (MIN_GAP_SEC + 59) // 60)


def account_response(row: PublishAccountORM, *, now: datetime, today: str) -> dict:
    data = account_to_dict(row, now=now, today=today,
                           effective_cap=effective_cap(row, now))
    data["effective_cooldown_minutes"] = effective_cooldown_minutes(row)
    return data


# ── Service token ────────────────────────────────────────────────────────


def _tok_eq(got: Optional[str], expected: str) -> bool:
    """Constant-time comparison, avoiding the != timing side channel leaking prefix match length. Per engine_sessions.py:60."""
    if not expected:
        return False
    return hmac.compare_digest(got or "", expected)


def require_publish_service_token(
    x_publish_service_token: Optional[str] = Header(None),
) -> None:
    expected = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if not expected:
        # When not configured, disable the worker endpoints entirely rather than allowing — these endpoints hand out plaintext login sessions.
        raise HTTPException(503, "publish pool service endpoints disabled "
                                 "(PUBLISH_SERVICE_TOKEN not configured)")
    if not _tok_eq(x_publish_service_token, expected):
        raise HTTPException(401, "invalid X-Publish-Service-Token")


# ── Internal helpers ────────────────────────────────────────────────────────


def resolve_proxy_url(db: Session, proxy_id: Optional[int]) -> Optional[str]:
    """Resolve proxy_id into a full proxy string. Disabled proxies return None (equivalent to "no egress").

    Isomorphic to the engine pool's check-out resolution, but the publishing pool has **no** raw proxy_override string fallback —
    proxies must be centralized in the proxy library, so "change proxy_url in one place and all bound accounts follow",
    and accounts still run after the password in the proxy library is removed (F2 acceptance item).
    """
    if not proxy_id:
        return None
    p = db.get(EngineProxyORM, proxy_id)
    if p is None or p.disabled:
        return None
    return p.proxy_url


def _proxy_egress_key(proxy: EngineProxyORM) -> str:
    """One real egress IP may have several proxy records; allocation spreads by real egress first."""
    return f"ip:{proxy.last_exit_ip}" if proxy.last_exit_ip else f"proxy:{proxy.id}"


def _auto_select_proxy(db: Session, platform: str) -> EngineProxyORM:
    """Pick a fixed proxy from the available proxy library.

    Conservative rule: first spread accounts of the same platform, then spread accounts globally. When last_exit_ip is known, group by real
    egress, to avoid several proxy records that are actually the same IP.
    """
    proxies = (db.query(EngineProxyORM)
                 .filter(EngineProxyORM.disabled.is_(False))
                 .order_by(EngineProxyORM.id)
                 .all())
    if not proxies:
        raise HTTPException(422, "代理库没有可用代理，无法自动分配")

    proxy_ids = [p.id for p in proxies]
    proxy_keys = {p.id: _proxy_egress_key(p) for p in proxies}
    platform_counts: dict[str, int] = {}
    total_counts: dict[str, int] = {}
    platform_proxy_counts: dict[int, int] = {}
    total_proxy_counts: dict[int, int] = {}
    rows = (db.query(PublishAccountORM.proxy_id,
                     PublishAccountORM.platform,
                     PublishAccountORM.exit_ip)
              .filter(PublishAccountORM.channel == CHANNEL_SERVER_POOL,
                      PublishAccountORM.proxy_id.in_(proxy_ids),
                      PublishAccountORM.status != "retired")
              .all())
    for proxy_id, row_platform, exit_ip in rows:
        if proxy_id is None:
            continue
        key = f"ip:{exit_ip}" if exit_ip else proxy_keys.get(proxy_id, f"proxy:{proxy_id}")
        total_counts[key] = total_counts.get(key, 0) + 1
        total_proxy_counts[proxy_id] = total_proxy_counts.get(proxy_id, 0) + 1
        if row_platform == platform:
            platform_counts[key] = platform_counts.get(key, 0) + 1
            platform_proxy_counts[proxy_id] = platform_proxy_counts.get(proxy_id, 0) + 1

    return min(
        proxies,
        key=lambda p: (
            platform_counts.get(proxy_keys[p.id], 0),
            total_counts.get(proxy_keys[p.id], 0),
            platform_proxy_counts.get(p.id, 0),
            total_proxy_counts.get(p.id, 0),
            1 if p.last_exit_ip is None else 0,
            p.id,
        ),
    )


def platform_row(db: Session, code: str) -> Optional[PublishPlatformORM]:
    return (db.query(PublishPlatformORM)
              .filter(PublishPlatformORM.code == code).one_or_none())


def _call_verify_identity(platform: str, storage_state: dict, proxy_url: Optional[str],
                          user_agent: Optional[str] = None,
                          ua_platform: Optional[str] = None,
                          expected_account_id: Optional[str] = None) -> dict:
    """Call browser-service's /publish/verify-identity.

    🔴 proxy_url must be passed: the proxy used at registration must strictly equal the runtime proxy, otherwise the first publish
    is a remote login. The ENGINE_AUTHZ_PROXY fallback the engine pool keeps to bypass deepseek region restrictions
    must not be copied for publishing accounts (plan §4 P0 item 3).
    """
    body = {"platform": platform, "storage_state": storage_state}
    if proxy_url:
        body["proxy_url"] = proxy_url
    if user_agent:
        body["user_agent"] = user_agent
    if ua_platform:
        body["ua_platform"] = ua_platform
    if expected_account_id:
        body["expected_account_id"] = expected_account_id
    headers = {"Content-Type": "application/json"}
    tok = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if tok:
        headers["X-Publish-Service-Token"] = tok
    try:
        with httpx.Client(timeout=BROWSER_VERIFY_TIMEOUT) as c:
            r = c.post(f"{BROWSER_SERVICE_URL}/publish/verify-identity",
                       json=body, headers=headers)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"browser-service 身份解析失败:{type(e).__name__}: {e}")
    if r.status_code != 200:
        if r.status_code == 404:
            raise HTTPException(502, "browser-service 身份解析接口不存在: "
                                     f"POST {BROWSER_SERVICE_URL}/publish/verify-identity 返回 404。"
                                     "请确认 browser-service 已重启并加载发布账号 API。")
        raise HTTPException(502, f"browser-service /publish/verify-identity "
                                 f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _call_password_login(payload: dict) -> dict:
    """Proxy to browser-service. Neither the caller nor this function may log the payload."""
    headers = {"Content-Type": "application/json"}
    tok = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if tok:
        headers["X-Publish-Service-Token"] = tok
    try:
        with httpx.Client(timeout=BROWSER_VERIFY_TIMEOUT) as c:
            r = c.post(f"{BROWSER_SERVICE_URL}/publish/login-password",
                       json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"browser-service 密码登录失败:{type(e).__name__}")
    if r.status_code != 200:
        raise HTTPException(502, f"browser-service 密码登录 HTTP {r.status_code}")
    return r.json()


def _call_web_login(payload: dict) -> dict:
    """Have browser-service open the platform login page; the request contains no account, password or verification code."""
    headers = {"Content-Type": "application/json"}
    tok = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if tok:
        headers["X-Publish-Service-Token"] = tok
    try:
        with httpx.Client(timeout=BROWSER_VERIFY_TIMEOUT) as c:
            r = c.post(f"{BROWSER_SERVICE_URL}/publish/login-web",
                       json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"browser-service 网页登录失败:{type(e).__name__}")
    if r.status_code != 200:
        raise HTTPException(502, f"browser-service 网页登录 HTTP {r.status_code}")
    return r.json()


def _call_manual_login(action: str, session_id: str) -> dict:
    """Poll or cancel the manual takeover session held in browser-service memory."""
    headers = {"Content-Type": "application/json"}
    tok = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if tok:
        headers["X-Publish-Service-Token"] = tok
    try:
        with httpx.Client(timeout=BROWSER_VERIFY_TIMEOUT) as c:
            r = c.post(f"{BROWSER_SERVICE_URL}/publish/login-web/{action}",
                       json={"session_id": session_id}, headers=headers)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"browser-service 人工登录失败:{type(e).__name__}")
    if r.status_code != 200:
        raise HTTPException(502, f"browser-service 人工登录 HTTP {r.status_code}")
    return r.json()


def _mask_identifier(identifier: str) -> str:
    if "@" in identifier:
        user, domain = identifier.split("@", 1)
        return f"{user[:2]}***@{domain}"
    return f"{identifier[:3]}***{identifier[-2:]}" if len(identifier) > 5 else "***"


def _clean_account_handle(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text[:64] if text else None


class PublishPasswordLoginIn(BaseModel):
    platform: str = Field(..., min_length=1, max_length=64)
    identifier: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=512)
    proxy_id: Optional[int] = None
    auto_proxy: bool = False
    egress: Optional[str] = Field(None, description="proxy=代理库自动分配/固定代理;host=本机直连")
    credential_owner: Optional[str] = Field(None, max_length=128)
    warmup_enabled: bool = False
    warmup_days: int = Field(7, ge=1, le=90)
    warmup_daily_cap: int = Field(0, ge=0, le=10000)
    daily_cap: Optional[int] = Field(0, ge=0)
    account_db_id: Optional[int] = None


class PublishWebLoginIn(BaseModel):
    platform: str = Field(..., min_length=1, max_length=64)
    proxy_id: Optional[int] = None
    auto_proxy: bool = False
    egress: Optional[str] = Field(None, description="proxy=代理库自动分配/固定代理;host=本机直连")
    credential_owner: Optional[str] = Field(None, max_length=128)
    warmup_enabled: bool = False
    warmup_days: int = Field(7, ge=1, le=90)
    warmup_daily_cap: int = Field(0, ge=0, le=10000)
    daily_cap: Optional[int] = Field(0, ge=0)
    account_db_id: Optional[int] = None


class PublishPluginSessionImportIn(BaseModel):
    platform: str = Field(..., min_length=1, max_length=64)
    storage_state: dict
    credential_owner: Optional[str] = Field(None, max_length=128)
    warmup_enabled: bool = False
    warmup_days: int = Field(7, ge=1, le=90)
    warmup_daily_cap: int = Field(0, ge=0, le=10000)
    daily_cap: Optional[int] = Field(0, ge=0)
    proxy_id: Optional[int] = None
    auto_proxy: bool = False
    egress: Optional[str] = Field(None, description="proxy=代理库自动分配/固定代理;host=本机直连")
    account_db_id: Optional[int] = None
    account_handle: Optional[str] = Field(None, max_length=64)
    account_key: Optional[str] = Field(None, max_length=128)
    user_agent: Optional[str] = Field(None, max_length=512)
    ua_platform: Optional[str] = Field(None, max_length=128)


class PublishWxgzhOfficialApiImportIn(BaseModel):
    appid: str = Field(..., min_length=1, max_length=128)
    appsecret: str = Field(..., min_length=1, max_length=256)
    credential_owner: Optional[str] = Field(None, max_length=128)
    warmup_enabled: bool = False
    warmup_days: int = Field(7, ge=1, le=90)
    warmup_daily_cap: int = Field(0, ge=0, le=10000)
    daily_cap: Optional[int] = Field(0, ge=0)
    egress: Optional[str] = Field(None, description="proxy=代理库自动分配/固定代理;host=本机直连")
    proxy_id: Optional[int] = None
    auto_proxy: bool = False


class PublishWxgzhOfficialApiProxyPreviewIn(BaseModel):
    credential_owner: Optional[str] = Field(None, max_length=128)
    proxy_id: Optional[int] = None
    auto_proxy: bool = False


def _plugin_identity_failure_message(platform: str, probe: dict,
                                     expected_account_id: Optional[str]) -> str:
    fail_type = probe.get("fail_type") or "unknown"
    raw_error = probe.get("error") or "身份校验服务没有返回具体错误"
    observed = (stable_account_id_from_probe(probe.get("account_id"))
                or stable_account_id_from_probe(probe.get("observed_account_id")))
    prefix = f"插件登录态身份解析失败:{platform} 身份校验失败(fail_type={fail_type})。"
    if fail_type == "identity_mismatch":
        return (prefix + "当前浏览器登录的账号与要覆盖的原账号不一致。"
                f"当前读到={observed or '未知'}，原账号={expected_account_id or '未知'}。"
                "请切换到原账号后再导入；如果这是新账号，请走“添加账号”入口。"
                f"sidecar 返回:{raw_error}")
    if fail_type == "login_lost":
        return (prefix + "插件导出的 cookies 存在，但 sidecar 读不到平台登录身份。"
                "请确认当前浏览器已登录该平台，刷新平台页面后重新导入；"
                "若只在其他浏览器或无痕窗口登录，插件读不到。"
                f"sidecar 返回:{raw_error}")
    if fail_type == "timeout":
        return (prefix + "sidecar 读取平台身份超时，通常是平台接口慢、网络卡住或登录态弹出二次验证。"
                f"sidecar 返回:{raw_error}")
    if fail_type == "crash":
        return (prefix + "sidecar 执行身份读取时异常，可能是插件/sidecar 代码版本未更新或平台适配器报错。"
                f"sidecar 返回:{raw_error}")
    return prefix + f"sidecar 返回:{raw_error}"


def _storage_cookie_summary(storage_state: dict) -> list[dict[str, str]]:
    cookies = storage_state.get("cookies") if isinstance(storage_state, dict) else None
    if not isinstance(cookies, list):
        return []
    summary: list[dict[str, str]] = []
    for item in cookies:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        summary.append({
            "name": item["name"],
            "domain": item.get("domain") if isinstance(item.get("domain"), str) else "",
        })
    return summary


class PublishManualLoginPollIn(PublishPasswordLoginIn):
    manual_session_id: str = Field(..., min_length=16, max_length=128)


class PublishWebLoginPollIn(PublishWebLoginIn):
    manual_session_id: str = Field(..., min_length=16, max_length=128)


class PublishManualLoginCancelIn(BaseModel):
    manual_session_id: str = Field(..., min_length=16, max_length=128)


class PublishAccountScheduleIn(BaseModel):
    """Admin adjusts account-level dispatch parameters. Omitted fields stay unchanged."""
    daily_cap: Optional[int] = Field(None, ge=0, le=10000,
                                     description="0=不限;None 配合 reset_daily_cap")
    reset_daily_cap: bool = False
    cooldown_minutes: Optional[int] = Field(None, ge=0, le=7 * 24 * 60,
                                             description="账号发布间隔分钟数;0=不限制")
    reason: str = Field(..., min_length=1, max_length=256)


def _resolve_login_target(payload: PublishPasswordLoginIn | PublishWebLoginIn,
                          db: Session, supported_platforms: set[str], mode_label: str):
    platform = payload.platform.strip()
    if platform not in supported_platforms:
        raise HTTPException(422, f"平台 {platform} 暂不支持{mode_label}")
    plat = platform_row(db, platform)
    if plat is None or not plat.enabled or plat.channel != CHANNEL_SERVER_POOL:
        raise HTTPException(422, f"平台 {platform} 未启用自建发布账号池")

    existing = db.get(PublishAccountORM, payload.account_db_id) if payload.account_db_id else None
    if payload.account_db_id and existing is None:
        raise HTTPException(404, f"未找到发布账号：id={payload.account_db_id}")
    auto_proxy = bool(getattr(payload, "auto_proxy", False))
    if existing:
        if existing.platform != platform:
            raise HTTPException(409, "重新登录的平台与原账号不一致")
        if existing.last_fail_type == "banned" or existing.status == "unschedulable":
            raise HTTPException(409, "封禁或终止调度账号不能通过重新登录自动恢复")
        if auto_proxy:
            raise HTTPException(409, "重新登录必须沿用原账号绑定的代理，不能自动分配")
        proxy_id = existing.proxy_id
        egress = existing.egress or (EGRESS_PROXY if proxy_id else EGRESS_HOST)
        if payload.proxy_id is not None and payload.proxy_id != proxy_id:
            raise HTTPException(409, "重新登录必须沿用原账号绑定的代理")
        if payload.egress is not None and payload.egress != egress:
            raise HTTPException(409, "重新登录必须沿用原账号的出口方式")
        credential_owner = existing.credential_owner
    else:
        # New accounts default to the publishing machine's own egress; if a proxy is needed, automatic or fixed proxy must be chosen explicitly.
        # The renewal branch keeps the original egress; re-login must not silently switch IPs.
        if payload.proxy_id is None and payload.egress is None:
            auto_proxy = False
        proxy_id = payload.proxy_id
        egress = (payload.egress or (
            EGRESS_PROXY if payload.proxy_id or auto_proxy else EGRESS_HOST
        )).strip().lower()
        credential_owner = (payload.credential_owner or "").strip()
        if not credential_owner:
            raise HTTPException(422, "新增账号必须填写凭据归属人")
    if egress not in (EGRESS_PROXY, EGRESS_HOST):
        raise HTTPException(422, f"egress={egress!r} 不支持")
    proxy = None
    if egress == EGRESS_PROXY:
        if auto_proxy:
            if proxy_id:
                raise HTTPException(422, "自动分配代理时不要同时指定 proxy_id")
            proxy = _auto_select_proxy(db, platform)
            proxy_id = proxy.id
        elif not proxy_id:
            raise HTTPException(422, "固定代理模式必须选择 proxy_id")
        else:
            proxy = db.get(EngineProxyORM, proxy_id)
            if proxy is None or proxy.disabled:
                raise HTTPException(422, f"proxy_id={proxy_id} 不存在或已停用")
    else:
        if auto_proxy:
            raise HTTPException(422, "本机直连模式不能自动分配代理")
        if proxy_id:
            raise HTTPException(422, "本机直连模式不能同时指定 proxy_id")
    return platform, existing, proxy_id, proxy, credential_owner, egress


def _finish_password_login(payload: PublishPasswordLoginIn, result: dict, db: Session,
                           current_user, existing, proxy_id: Optional[int],
                           credential_owner: str, egress: str):
    try:
        enc_credentials = encrypt_required(json.dumps({
            "identifier": payload.identifier, "password": payload.password,
        }, ensure_ascii=False))
    except PublishCryptoUnavailable as e:
        raise HTTPException(503, str(e))
    registered = _register_account(PublishAccountRegisterIn(
        platform=payload.platform.strip(), channel=CHANNEL_SERVER_POOL,
        storage_state=result.get("storage_state"), proxy_id=proxy_id,
        auth_type="password",
        account_handle=(_clean_account_handle(result.get("account_handle"))
                        or (existing.account_handle if existing else None)
                        or _mask_identifier(payload.identifier)),
        credential_owner=credential_owner,
        daily_cap=payload.daily_cap if not existing else existing.daily_cap,
        owner_worker_id=(existing.owner_worker_id if existing else result.get("worker_id")),
        egress=egress,
        warmup_enabled=payload.warmup_enabled,
        warmup_days=payload.warmup_days,
        warmup_daily_cap=payload.warmup_daily_cap,
        resolve_identity=True,
    ), db=db, current_user=current_user, preverified_probe=result)
    row = db.get(PublishAccountORM, registered["account"]["id"])
    if existing and row.id != existing.id:
        raise HTTPException(409, "重新登录身份与原账号不一致")
    now = datetime.utcnow()
    row.credentials_enc = enc_credentials
    row.user_agent = result.get("user_agent") or row.user_agent
    row.ua_platform = result.get("ua_platform") or row.ua_platform
    row.last_fail_reason = None
    if existing and payload.warmup_enabled:
        row.status = "warming"
        row.warmup_until = now + timedelta(days=payload.warmup_days)
        row.warmup_daily_cap = payload.warmup_daily_cap
    elif existing:
        row.status = "active"
        row.warmup_until = None
        row.warmup_daily_cap = None
    db.commit()
    db.refresh(row)
    return {"renewed": bool(existing or registered.get("renewed")),
            "account": account_response(row, now=now, today=local_today())}


def _finish_web_login(payload: PublishWebLoginIn, result: dict, db: Session,
                      current_user, existing, proxy_id: Optional[int],
                      credential_owner: str, egress: str):
    """Save the login session produced after the user logs in on the platform page; no password or SMS code is stored."""
    registered = _register_account(PublishAccountRegisterIn(
        platform=payload.platform.strip(), channel=CHANNEL_SERVER_POOL,
        storage_state=result.get("storage_state"), proxy_id=proxy_id,
        auth_type="web",
        account_handle=(_clean_account_handle(result.get("account_handle"))
                        or (existing.account_handle if existing else None)),
        credential_owner=credential_owner,
        daily_cap=payload.daily_cap if not existing else existing.daily_cap,
        owner_worker_id=(existing.owner_worker_id if existing else result.get("worker_id")),
        egress=egress, warmup_enabled=payload.warmup_enabled,
        warmup_days=payload.warmup_days,
        warmup_daily_cap=payload.warmup_daily_cap,
        resolve_identity=True,
    ), db=db, current_user=current_user, preverified_probe=result)
    row = db.get(PublishAccountORM, registered["account"]["id"])
    if existing and row.id != existing.id:
        raise HTTPException(409, "重新登录身份与原账号不一致")
    now = datetime.utcnow()
    row.credentials_enc = None
    row.auth_type = "web"
    row.user_agent = result.get("user_agent") or row.user_agent
    row.ua_platform = result.get("ua_platform") or row.ua_platform
    row.last_fail_reason = None
    if existing and payload.warmup_enabled:
        row.status = "warming"
        row.warmup_until = now + timedelta(days=payload.warmup_days)
        row.warmup_daily_cap = payload.warmup_daily_cap
    elif existing:
        row.status = "active"
        row.warmup_until = None
        row.warmup_daily_cap = None
    db.commit()
    db.refresh(row)
    return {"renewed": bool(existing or registered.get("renewed")),
            "account": account_response(row, now=now, today=local_today())}


def _wx_api_error(prefix: str, payload: object) -> HTTPException:
    if isinstance(payload, dict):
        code = payload.get("errcode")
        msg = payload.get("errmsg") or payload.get("message") or "unknown"
        if code is not None:
            return HTTPException(422, f"{prefix}:errcode={code} errmsg={msg}")
        return HTTPException(422, f"{prefix}:{msg}")
    return HTTPException(422, prefix)


def _wxgzh_basic_name(payload: dict, fallback: str) -> str:
    for value in (
        payload.get("nickname"),
        payload.get("nick_name"),
        (payload.get("nickname_info") or {}).get("nickname")
        if isinstance(payload.get("nickname_info"), dict) else None,
        (payload.get("nick_name_info") or {}).get("nick_name")
        if isinstance(payload.get("nick_name_info"), dict) else None,
        payload.get("name"),
    ):
        name = _clean_account_handle(value)
        if name:
            return name
    return fallback


def _wxgzh_official_api_profile(appid: str, appsecret: str,
                                proxy_url: Optional[str]) -> dict:
    """Validate AppID/AppSecret with the WeChat official account API and read the display name."""
    client_kwargs = {"timeout": 15.0, "trust_env": False}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
    try:
        with httpx.Client(**client_kwargs) as client:
            token_resp = client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": appid,
                    "secret": appsecret,
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
            if not isinstance(token_data, dict) or not token_data.get("access_token"):
                raise _wx_api_error("微信公众号官方 API 获取 access_token 失败", token_data)
            basic_resp = client.post(
                "https://api.weixin.qq.com/cgi-bin/account/getaccountbasicinfo",
                params={"access_token": token_data["access_token"]},
                json={},
            )
            basic_resp.raise_for_status()
            basic = basic_resp.json()
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(422, f"微信公众号官方 API HTTP {e.response.status_code}")
    except httpx.HTTPError as e:
        raise HTTPException(422, f"微信公众号官方 API 请求失败:{type(e).__name__}")
    except ValueError:
        raise HTTPException(422, "微信公众号官方 API 返回不是 JSON")

    if not isinstance(basic, dict):
        raise HTTPException(422, "微信公众号官方 API 基础信息返回格式不正确")
    errcode = basic.get("errcode")
    if errcode not in (None, 0):
        raise _wx_api_error("微信公众号官方 API 获取基础信息失败", basic)
    return {"account_handle": _wxgzh_basic_name(basic, appid), "raw": basic}


@router.get("/publish-accounts/platforms")
def list_platforms(db: Session = Depends(get_db)):
    # The account center only manages self-hosted publishing accounts whose login sessions we hold. The aggregator channel is retired; plugin accounts only
    # exist on ops' local machines and are not part of this back-office account pool.
    caps = _browser_publish_capabilities()
    rows = (db.query(PublishPlatformORM)
              .filter(PublishPlatformORM.channel == CHANNEL_SERVER_POOL)
              .order_by(PublishPlatformORM.sort_order, PublishPlatformORM.code).all())
    items = []
    for row in rows:
        publish_supported = row.code in caps["platforms"]
        web_supported = row.code in caps["web_login_platforms"]
        password_supported = row.code in caps["password_login_platforms"]
        note = None
        if not caps["available"]:
            note = caps["note"]
        elif not publish_supported:
            note = "browser-service 尚未注册该平台发布适配器"
        elif not web_supported:
            note = "该平台暂未接入网页登录"
        items.append({
            "code": row.code, "name": row.name_zh or row.code, "channel": row.channel,
            "enabled": bool(row.enabled),
            "password_login_supported": (password_supported
                                         and row.channel == CHANNEL_SERVER_POOL),
            "web_login_supported": (web_supported and row.channel == CHANNEL_SERVER_POOL),
            "publish_supported": publish_supported,
            "claimable": row.code in caps["claimable_platforms"],
            "support_note": note,
        })
    return {"items": items}


@router.post("/publish-accounts/web-login", status_code=201)
def web_login(payload: PublishWebLoginIn,
              db: Session = Depends(get_db),
              current_user=Depends(require_roles("admin", "maintainer"))):
    """Open the platform login page. Account, password, SMS and QR content never pass through the account center API."""
    caps = _browser_publish_capabilities()
    platform, existing, proxy_id, proxy, _, _ = _resolve_login_target(
        payload, db, caps["web_login_platforms"], "网页登录")
    result = _call_web_login({
        "platform": platform,
        "proxy_url": proxy.proxy_url if proxy else None,
        "expected_account_id": existing.account_id if existing else None,
        "ua_platform": existing.ua_platform if existing else None,
    })
    if not result.get("ok") or not result.get("manual_session_id"):
        code = result.get("error") or "web_login_failed"
        detail = result.get("detail") or "打开平台登录页失败"
        raise HTTPException(422, f"{code}:{detail}")
    return {"manual_required": True,
            "manual_session_id": result["manual_session_id"],
            "qr_image": result.get("qr_image"),
            "preview_image": result.get("preview_image"),
            "interactive": bool(result.get("interactive")),
            "expires_in": result.get("expires_in", 600),
            "proxy_id": proxy_id,
            "detail": result.get("detail")}


@router.post("/publish-accounts/web-login/poll")
def web_login_poll(payload: PublishWebLoginPollIn,
                   db: Session = Depends(get_db),
                   current_user=Depends(require_roles("admin", "maintainer"))):
    """After web login completes, verify identity, save the login session and add to the account pool."""
    caps = _browser_publish_capabilities()
    _, existing, proxy_id, _, credential_owner, egress = \
        _resolve_login_target(payload, db, caps["web_login_platforms"], "网页登录")
    result = _call_manual_login("poll", payload.manual_session_id)
    status = result.get("status")
    if status == "pending":
        return {"status": "pending", "qr_image": result.get("qr_image"),
                "preview_image": result.get("preview_image"),
                "interactive": bool(result.get("interactive")),
                "expires_in": result.get("expires_in", 0)}
    if status == "expired":
        raise HTTPException(410, "网页登录会话已过期，请重新打开登录页")
    if status != "done" or not result.get("ok"):
        raise HTTPException(422, f"{result.get('error') or 'web_login_failed'}:"
                                 f"{result.get('detail') or '网页登录失败'}")
    base_payload = PublishWebLoginIn(**payload.dict(exclude={"manual_session_id"}))
    finished = _finish_web_login(base_payload, result, db, current_user, existing,
                                 proxy_id, credential_owner, egress)
    return {"status": "done", **finished}


@router.post("/publish-accounts/web-login/cancel")
def web_login_cancel(payload: PublishManualLoginCancelIn):
    _call_manual_login("cancel", payload.manual_session_id)
    return {"ok": True}


@router.post("/publish-accounts/wxgzh-official-api/import", status_code=201)
def import_wxgzh_official_api(payload: PublishWxgzhOfficialApiImportIn,
                              db: Session = Depends(get_db),
                              current_user=Depends(require_roles("admin", "maintainer"))):
    """Add an official-API publishing account using the WeChat official account AppID/AppSecret."""
    appid = payload.appid.strip()
    appsecret = payload.appsecret.strip()
    target = PublishWebLoginIn(
        platform="wxgzh",
        credential_owner=payload.credential_owner,
        warmup_enabled=payload.warmup_enabled,
        warmup_days=payload.warmup_days,
        warmup_daily_cap=payload.warmup_daily_cap,
        daily_cap=payload.daily_cap,
        proxy_id=payload.proxy_id,
        auto_proxy=payload.auto_proxy,
        egress=payload.egress,
    )
    caps = _browser_publish_capabilities()
    platform, existing, proxy_id, proxy, credential_owner, egress = \
        _resolve_login_target(target, db, caps["platforms"], "公众号官方 API 添加")

    profile = _wxgzh_official_api_profile(appid, appsecret, proxy.proxy_url if proxy else None)
    account_handle = _clean_account_handle(profile.get("account_handle")) or appid
    account_id = _account_hash("core.wxgzh.appid", appid)
    if existing and existing.account_id != account_id:
        raise HTTPException(409, "重新添加的 AppID 与原公众号账号不一致")
    try:
        enc_credentials = encrypt_required(json.dumps({
            "appid": appid,
            "appsecret": appsecret,
        }, ensure_ascii=False))
    except PublishCryptoUnavailable as e:
        raise HTTPException(503, str(e))

    registered = _register_account(PublishAccountRegisterIn(
        platform=platform,
        channel=CHANNEL_SERVER_POOL,
        storage_state={"cookies": [], "origins": []},
        proxy_id=proxy_id,
        auth_type="official_api",
        account_handle=account_handle,
        credential_owner=credential_owner,
        warmup_enabled=payload.warmup_enabled,
        warmup_days=payload.warmup_days,
        warmup_daily_cap=payload.warmup_daily_cap,
        daily_cap=payload.daily_cap if not existing else existing.daily_cap,
        owner_worker_id=existing.owner_worker_id if existing else None,
        egress=egress,
        resolve_identity=True,
    ), db=db, current_user=current_user, preverified_probe={
        "ok": True,
        "account_id": account_id,
        "account_handle": account_handle,
        "exit_ip": None,
    })
    row = db.get(PublishAccountORM, registered["account"]["id"])
    row.credentials_enc = enc_credentials
    row.auth_type = "official_api"
    row.account_handle = account_handle
    row.last_fail_reason = None
    db.commit()
    db.refresh(row)
    now = datetime.utcnow()
    return {"renewed": bool(existing or registered.get("renewed")),
            "account": account_response(row, now=now, today=local_today())}


@router.post("/publish-accounts/wxgzh-official-api/preview-proxy")
def preview_wxgzh_official_api_proxy(payload: PublishWxgzhOfficialApiProxyPreviewIn,
                                     db: Session = Depends(get_db),
                                     current_user=Depends(require_roles("admin", "maintainer"))):
    target = PublishWebLoginIn(
        platform="wxgzh",
        credential_owner=payload.credential_owner,
        proxy_id=payload.proxy_id,
        auto_proxy=payload.auto_proxy,
        egress=EGRESS_PROXY,
    )
    caps = _browser_publish_capabilities()
    _, _, proxy_id, proxy, _, _ = _resolve_login_target(
        target, db, caps["platforms"], "公众号官方 API 添加")
    if proxy is None or proxy_id is None:
        raise HTTPException(422, "公众号官方 API 添加必须绑定代理")
    if not proxy.last_exit_ip:
        raise HTTPException(422, f"代理 #{proxy_id} 尚未探测出口 IP，"
                                 "请先在代理库完成自检后再配置微信 IP 白名单")
    return {
        "proxy_id": proxy_id,
        "label": proxy.label,
        "last_exit_ip": proxy.last_exit_ip,
        "last_country": proxy.last_country,
    }


@router.post("/publish-accounts/plugin-session/import", status_code=201)
def import_plugin_session(payload: PublishPluginSessionImportIn,
                          db: Session = Depends(get_db),
                          current_user=Depends(require_roles("admin", "maintainer"))):
    """Import the current browser login session exported by the WechatSync extension into the account pool."""
    import_via_host = os.environ.get(
        "PUBLISH_PLUGIN_IMPORT_HOST_EGRESS", ""
    ).strip().lower() in ("1", "true", "yes", "on")
    target = PublishWebLoginIn(
        platform=payload.platform,
        credential_owner=payload.credential_owner,
        warmup_enabled=payload.warmup_enabled,
        warmup_days=payload.warmup_days,
        warmup_daily_cap=payload.warmup_daily_cap,
        daily_cap=payload.daily_cap,
        proxy_id=None if import_via_host else payload.proxy_id,
        auto_proxy=False if import_via_host else payload.auto_proxy,
        account_db_id=payload.account_db_id,
        egress=EGRESS_HOST if import_via_host else payload.egress,
    )
    caps = _browser_publish_capabilities()
    platform, existing, proxy_id, proxy, credential_owner, egress = \
        _resolve_login_target(target, db, caps["platforms"], "插件登录态导入")
    state = payload.storage_state or {}
    plat = platform_row(db, platform)
    if plat is None:
        raise HTTPException(422, f"插件登录态导入失败:平台 {platform} 未在发布平台表中配置")
    cookies = state.get("cookies") if isinstance(state, dict) else None
    if not isinstance(cookies, list):
        raise HTTPException(422, "插件导出的 storage_state.cookies 不是数组，"
                                 "请升级或重新加载 WechatSync 插件后再试")
    if not cookies:
        raise HTTPException(422, "插件没有导出 cookies，请确认当前浏览器已登录平台账号")
    if not has_login_cookies(platform, state, plat.identity_cookies_json):
        raise HTTPException(422, f"插件导出的登录态缺少 {platform} 票据 cookie，"
                                 "请确认导出的是已登录账号")

    probe = _call_verify_identity(
        platform, state, proxy.proxy_url if proxy else None,
        user_agent=payload.user_agent,
        ua_platform=payload.ua_platform,
        expected_account_id=existing.account_id if existing else None,
    )
    if not probe.get("ok"):
        code = 409 if probe.get("fail_type") == "identity_mismatch" else 422
        log.warning("[publish-pool] plugin session import verify failed: "
                    "platform=%s fail_type=%s error=%s cookies=%s",
                    platform, probe.get("fail_type"), probe.get("error"),
                    _storage_cookie_summary(state))
        raise HTTPException(code, _plugin_identity_failure_message(
            platform, probe, existing.account_id if existing else None))
    account_id = stable_account_id_from_probe(probe.get("account_id"))
    probe_handle = _clean_account_handle(probe.get("account_handle"))
    if existing and account_id != existing.account_id:
        raise HTTPException(409, _plugin_identity_failure_message(
            platform, {"ok": False, "fail_type": "identity_mismatch",
                       "account_id": account_id,
                       "error": "后端二次比对发现账号不一致"},
            existing.account_id))
    probe_worker_id = probe.get("worker_id")
    if existing is None and (isinstance(probe_worker_id, bool)
                             or not isinstance(probe_worker_id, int)
                             or probe_worker_id <= 0):
        raise HTTPException(422, "browser-service 未返回有效 worker_id；"
                                 "请为负责账号校验的服务配置稳定 WORKER_UID")

    registered = _register_account(PublishAccountRegisterIn(
        platform=platform, channel=CHANNEL_SERVER_POOL,
        storage_state=state, proxy_id=proxy_id,
        auth_type="plugin_cookie",
        account_handle=_clean_account_handle(payload.account_handle),
        credential_owner=credential_owner,
        warmup_enabled=payload.warmup_enabled,
        warmup_days=payload.warmup_days,
        warmup_daily_cap=payload.warmup_daily_cap,
        daily_cap=payload.daily_cap if not existing else existing.daily_cap,
        owner_worker_id=(existing.owner_worker_id if existing else probe_worker_id),
        egress=egress, resolve_identity=True,
    ), db=db, current_user=current_user, preverified_probe=probe)
    row = db.get(PublishAccountORM, registered["account"]["id"])
    if existing and row.id != existing.id:
        raise HTTPException(409, _plugin_identity_failure_message(
            platform, {"ok": False, "fail_type": "identity_mismatch",
                       "account_id": account_id,
                       "error": "入池时命中了另一个账号行"},
            existing.account_id))
    plugin_key = _clean_account_handle(payload.account_key)
    if (not _clean_account_handle(payload.account_handle) and not probe_handle
            and plugin_key and row.account_handle == plugin_key):
        row.account_handle = None
    row.user_agent = payload.user_agent or row.user_agent
    row.ua_platform = payload.ua_platform or row.ua_platform
    db.commit()
    db.refresh(row)
    return {"renewed": bool(existing or registered.get("renewed")),
            "identity_resolved": True,
            "account": account_response(row, now=datetime.utcnow(), today=local_today())}


@router.post("/publish-accounts/password-login", status_code=201)
def password_login(payload: PublishPasswordLoginIn,
                   db: Session = Depends(get_db),
                   current_user=Depends(require_roles("admin", "maintainer"))):
    """Add an account or re-login an offline account. The password is encrypted before any browser action and never returned."""
    caps = _browser_publish_capabilities()
    platform, existing, proxy_id, proxy, credential_owner, egress = \
        _resolve_login_target(payload, db, caps["password_login_platforms"], "账号密码登录")

    result = _call_password_login({
        "platform": platform, "identifier": payload.identifier,
        "password": payload.password, "proxy_url": proxy.proxy_url if proxy else None,
        "expected_account_id": existing.account_id if existing else None,
        "ua_platform": existing.ua_platform if existing else None,
    })
    if not result.get("ok"):
        if result.get("error") == "challenge_required" and result.get("manual_session_id"):
            # The password stays only in the frontend's current form memory; only an unguessable browser session id is returned here.
            return {"manual_required": True,
                    "manual_session_id": result["manual_session_id"],
                    "qr_image": result.get("qr_image"),
                    "preview_image": result.get("preview_image"),
                    "interactive": bool(result.get("interactive")),
                    "expires_in": result.get("expires_in", 600),
                    "proxy_id": proxy_id,
                    "detail": result.get("detail")}
        code = result.get("error") or "login_failed"
        detail = result.get("detail") or "账号密码登录失败"
        raise HTTPException(422, f"{code}:{detail}")
    return _finish_password_login(payload, result, db, current_user, existing, proxy_id,
                                  credential_owner, egress)


@router.post("/publish-accounts/password-login/manual-poll")
def password_login_manual_poll(payload: PublishManualLoginPollIn,
                               db: Session = Depends(get_db),
                               current_user=Depends(require_roles("admin", "maintainer"))):
    """After a human completes the verification code/QR scan, verify identity automatically and add to the pool."""
    caps = _browser_publish_capabilities()
    _, existing, proxy_id, _, credential_owner, egress = \
        _resolve_login_target(payload, db, caps["password_login_platforms"], "账号密码登录")
    result = _call_manual_login("poll", payload.manual_session_id)
    status = result.get("status")
    if status == "pending":
        return {"status": "pending", "qr_image": result.get("qr_image"),
                "preview_image": result.get("preview_image"),
                "interactive": bool(result.get("interactive")),
                "expires_in": result.get("expires_in", 0)}
    if status == "expired":
        raise HTTPException(410, "人工登录会话已过期，请重新登录")
    if status != "done" or not result.get("ok"):
        raise HTTPException(422, f"{result.get('error') or 'manual_login_failed'}:"
                                 f"{result.get('detail') or '人工登录失败'}")
    base_payload = PublishPasswordLoginIn(**payload.dict(exclude={"manual_session_id"}))
    finished = _finish_password_login(base_payload, result, db, current_user, existing,
                                      proxy_id, credential_owner, egress)
    return {"status": "done", **finished}


@router.post("/publish-accounts/password-login/manual-cancel")
def password_login_manual_cancel(payload: PublishManualLoginCancelIn):
    _call_manual_login("cancel", payload.manual_session_id)
    return {"ok": True}


# ── Admin endpoints ────────────────────────────────────────────────────────


@router.post("/publish-accounts/register", status_code=201)
def register_account(payload: PublishAccountRegisterIn,
                     db: Session = Depends(get_db),
                     current_user=Depends(require_roles("admin", "maintainer"))):
    return _register_account(payload, db, current_user)


def _register_account(payload: PublishAccountRegisterIn, db: Session, current_user,
                      preverified_probe: Optional[dict] = None):
    """Move an existing account's login session into the system (F1).

    The accounts are **existing**: already in use, with history, followers and weight. So this step involves no registration/ID verification/account nurturing;
    it is essentially "import login session + pin egress + resolve authoritative identity".

    Idempotent: a hit on (platform, account_id) is **renewed in place** without a second row — following
    engine_sessions.upload's renew-in-place.
    """
    platform = payload.platform.strip()
    plat = platform_row(db, platform)
    if plat is None:
        raise HTTPException(422, f"未知平台 {platform};请先 INSERT 进 publish_platforms "
                                 f"(seeds/publish_platforms.py)")
    if not plat.enabled:
        raise HTTPException(422, f"平台 {platform} 已停用")
    if payload.channel not in ALL_CHANNELS:
        raise HTTPException(422, f"channel 必须是 {ALL_CHANNELS} 之一")
    if payload.channel != plat.channel:
        # channel is a fixed attribute of the account row and must match the platform list — WeChat official accounts can only use aggregator;
        # mixing would break the idempotency unique constraint (plan §1.2).
        raise HTTPException(422, f"平台 {platform} 的通道是 {plat.channel},"
                                 f"不能按 {payload.channel} 登记")
    egress = (payload.egress or (
        EGRESS_PROXY if payload.proxy_id or payload.auto_proxy else EGRESS_HOST
    )).strip().lower()
    if egress is not None and egress not in ALL_EGRESS:
        raise HTTPException(422, f"egress 必须是 {ALL_EGRESS} 之一或留空")
    if payload.auto_proxy and payload.proxy_id:
        raise HTTPException(422, "自动分配代理时不要同时指定 proxy_id")
    if payload.auto_proxy and egress == EGRESS_HOST:
        raise HTTPException(422, "本机直连模式不能自动分配代理")

    if payload.channel != CHANNEL_SERVER_POOL:
        if payload.auto_proxy:
            raise HTTPException(422, f"{payload.channel} 通道不支持自动分配代理")
        # Login sessions of aggregator/plugin channels are not in our hands: authorized on the Aitoearn side / ops' local browser.
        # Accepting a storage_state would only create the illusion that "we have a login session".
        if payload.storage_state:
            raise HTTPException(422, f"{payload.channel} 通道的登录态不在我们手上,"
                                     f"不要传 storage_state")
        return _register_non_server_pool(db, payload, plat, current_user)

    # ── Below is the server_pool channel ─────────────────────────────────────
    state = payload.storage_state or {}
    is_official_api = payload.auth_type == "official_api"
    if not is_official_api and not state.get("cookies"):
        raise HTTPException(422, "storage_state 没有 cookies —— 大概是没登录的空壳")
    if not is_official_api and not has_login_cookies(platform, state, plat.identity_cookies_json):
        want = plat.identity_cookies_json
        raise HTTPException(422, f"storage_state 里找不到 {platform} 的票据 cookie({want}),"
                                 f"请确认导出的是**已登录**状态")
    proxy_id = payload.proxy_id
    if payload.auto_proxy:
        proxy = _auto_select_proxy(db, platform)
        proxy_id = proxy.id
        proxy_url = proxy.proxy_url
    elif not proxy_id:
        if egress != EGRESS_HOST:
            raise HTTPException(422, "server_pool 通道必须绑定 proxy_id —— 出口必须钉死,"
                                     "否则发布走 worker 本机 IP,首次发布即异地。"
                                     '如需本机直连请显式传 egress="host"')
        proxy = None
        proxy_url = None
        log.warning("[publish-pool] register platform=%s uses a **direct** egress (egress=host): "
                    "publishing will use the worker's own public IP", platform)
    else:
        if egress == EGRESS_HOST:
            raise HTTPException(422, 'egress="host" 表示直连,不要同时传 proxy_id —— '
                                     "两个出口声明冲突时不该由代码替你猜")
        proxy = db.get(EngineProxyORM, proxy_id)
        if proxy is None:
            raise HTTPException(422, f"proxy_id={proxy_id} 不存在")
        if proxy.disabled:
            raise HTTPException(422, f"proxy_id={proxy_id} 已停用,不能新绑")
        proxy_url = proxy.proxy_url

    # Encrypt **first**: if the key is not configured, fail before any browser action instead of opening a browser for nothing.
    try:
        enc = encrypt_required(json.dumps(state, ensure_ascii=False))
    except PublishCryptoUnavailable as e:
        raise HTTPException(503, str(e))

    now = datetime.utcnow()
    account_id: Optional[str] = None
    account_handle: Optional[str] = None
    exit_ip: Optional[str] = None
    probe: dict = {}
    identity_resolved = False

    if payload.resolve_identity:
        probe = preverified_probe or _call_verify_identity(platform, state, proxy_url)
        if not probe.get("ok"):
            raise HTTPException(422, f"身份解析失败({probe.get('fail_type')}):"
                                     f"{probe.get('error')}")
        account_id = stable_account_id_from_probe(probe.get("account_id"))
        account_handle = _clean_account_handle(probe.get("account_handle"))
        exit_ip = probe.get("exit_ip")
        identity_resolved = bool(account_id)
        if not identity_resolved:
            raise HTTPException(422, "browser-service 返回 ok 但没给出 account_id")
    else:
        # Escape hatch: no browser call. Persist a placeholder id from the ticket cookie hash, status stays pending_auth —
        # such ids change with cookie rotation, so they **do not participate** in verify_identity comparison,
        # and the backend never dispatches work to such accounts. Only for table-creation/claim tests in browser-less environments.
        account_id = provisional_account_id(platform, state, plat.identity_cookies_json)
        if not account_id:
            raise HTTPException(422, "既不解析身份、又提不到票据 cookie —— 无法确定账号身份")

    existing = (db.query(PublishAccountORM)
                  .filter(PublishAccountORM.platform == platform,
                          PublishAccountORM.account_id == account_id)
                  .one_or_none())

    renewed = existing is not None
    row = existing or PublishAccountORM(platform=platform, account_id=account_id,
                                        channel=payload.channel, created_at=now)
    was_retired = renewed and row.status == "retired"
    if renewed and row.channel != payload.channel:
        # Channel is a fixed attribute: switching = explicit migration (retire the old row + create a new row), not an in-place change.
        raise HTTPException(409, f"该账号已登记在 {row.channel} 通道;"
                                 f"切换通道请先下线旧行再新建")

    row.storage_state_enc = enc
    row.auth_type = payload.auth_type
    row.account_handle = (_clean_account_handle(payload.account_handle)
                          or account_handle or row.account_handle)
    row.credential_owner = payload.credential_owner.strip()
    row.group_id = payload.group_id
    row.proxy_id = proxy_id
    row.egress = egress or (EGRESS_PROXY if proxy_id else None)
    row.captured_at = now
    row.expires_at = now + timedelta(days=payload.ttl_days)
    row.updated_at = now
    # Clear historical failure traces on renewal (consistent with engine_sessions.upload): a new login session means "fixed",
    # and letting old failure counts keep accumulating would push a good account into quarantine at the next minor failure.
    row.last_fail_type = None
    row.last_fail_reason = None
    row.last_fail_at = None
    row.fail_counts_json = "{}"
    row.rate_limited_until = None
    # 🔴 Do not clear unschedulable: that is the terminal state for "egress IP drift / suspected ban"; re-importing cookies
    # does not prove the platform's remote-login record is gone. Recovery requires explicit manual action.
    if row.status != "unschedulable":
        row.status = "active" if identity_resolved else "pending_auth"
    if payload.daily_cap is not None:
        row.daily_cap = payload.daily_cap
    # Normal renewal keeps the original worker; deleting and re-adding binds to the worker that completed identity verification this time.
    if payload.owner_worker_id is not None and (row.owner_worker_id is None or was_retired):
        row.owner_worker_id = payload.owner_worker_id
    if identity_resolved:
        # Warm-up is decided explicitly by the toggle when adding the account; skipped by default.
        if payload.warmup_enabled and row.status != "unschedulable":
            row.warmup_until = now + timedelta(days=payload.warmup_days)
            row.warmup_daily_cap = payload.warmup_daily_cap
            row.status = "warming"
        elif row.status != "unschedulable":
            row.warmup_until = None
            row.warmup_daily_cap = None
            row.status = "active"
        if exit_ip:
            row.exit_ip = exit_ip
            row.exit_ip_at = now
        row.identity_probe_json = json.dumps(
            {"at": now.isoformat(), "account_id": account_id,
             "account_handle": account_handle, "exit_ip": exit_ip},
            ensure_ascii=False)

    if existing is None:
        db.add(row)
    db.commit()
    db.refresh(row)
    log.info("[publish-pool] register platform=%s account=%s renewed=%s "
             "identity_resolved=%s proxy=%s egress=%s by=%s",
             platform, account_id, renewed, identity_resolved, proxy_id,
             row.egress, getattr(current_user, "id", None))
    return {"renewed": renewed, "identity_resolved": identity_resolved,
            "account": account_response(row, now=now, today=local_today())}


def _register_non_server_pool(db: Session, payload: PublishAccountRegisterIn,
                              plat: PublishPlatformORM, current_user) -> dict:
    """Registration for aggregator / plugin channels: bookkeeping only, no login session held.

    The account identity sources of these two channels differ (Aitoearn gives its own account id, the plugin gives the platform nickname),
    so account_id is provided by the caller via account_handle, with a channel prefix to avoid collisions. They do not enter check-out.
    """
    handle = (payload.account_handle or "").strip()
    if not handle:
        raise HTTPException(422, f"{payload.channel} 通道请用 account_handle 提供账号标识")
    account_id = f"{payload.channel}:{handle}"
    now = datetime.utcnow()
    existing = (db.query(PublishAccountORM)
                  .filter(PublishAccountORM.platform == plat.code,
                          PublishAccountORM.account_id == account_id).one_or_none())
    renewed = existing is not None
    row = existing or PublishAccountORM(platform=plat.code, account_id=account_id,
                                        channel=payload.channel, created_at=now)
    if renewed and row.channel != payload.channel:
        raise HTTPException(409, f"该账号已登记在 {row.channel} 通道")
    row.account_handle = handle
    row.credential_owner = payload.credential_owner.strip()
    row.group_id = payload.group_id
    row.auth_type = payload.auth_type
    row.status = "active"
    row.updated_at = now
    if payload.daily_cap is not None:
        row.daily_cap = payload.daily_cap
    if existing is None:
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"renewed": renewed, "identity_resolved": True,
            "account": account_response(row, now=now, today=local_today())}


@router.get("/publish-accounts")
def list_accounts(platform: Optional[str] = None, channel: Optional[str] = None,
                  status: Optional[str] = None,
                  anomaly: Optional[str] = None,
                  include_retired: bool = False,
                  page: Annotated[int, Query(ge=1)] = 1,
                  page_size: Annotated[int, Query(ge=1, le=200)] = 50,
                  db: Session = Depends(get_db)):
    """Paged list. 🔴 No storage_state / credentials in the response — not even ciphertext leaves the backend.

    Paging parameters use `Annotated[int, Query(...)]` rather than `page: int = Query(1)`: with the latter the
    Python default is a Query object, so `page - 1` blows up when scripts/tests call this function directly.
    In the Annotated form the default is a real int, and HTTP-side validation is unchanged.
    """
    q = db.query(PublishAccountORM)
    if platform:
        q = q.filter(PublishAccountORM.platform == platform)
    if channel:
        q = q.filter(PublishAccountORM.channel == channel)
    if status:
        q = q.filter(PublishAccountORM.status == status)
    if not include_retired:
        q = q.filter(PublishAccountORM.status != "retired")
    if anomaly == "banned":
        q = q.filter(PublishAccountORM.last_fail_type == "banned")
    elif anomaly == "offline":
        q = q.filter(PublishAccountORM.status == "quarantined",
                     PublishAccountORM.last_fail_type == "login_lost")
    elif anomaly:
        raise HTTPException(422, "anomaly 必须是 banned 或 offline")
    total = q.count()
    rows = (q.order_by(PublishAccountORM.platform.asc(), PublishAccountORM.priority.asc(),
                       PublishAccountORM.id.asc())
             .offset((page - 1) * page_size).limit(page_size).all())
    now, today = datetime.utcnow(), local_today()
    return {
        "items": [account_response(r, now=now, today=today) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


@router.delete("/publish-accounts/{account_db_id}")
def retire_account(account_db_id: int, db: Session = Depends(get_db),
                   current_user=Depends(require_roles("admin", "maintainer"))):
    """Soft-delete an account. Unclaimed tasks are cancelled synchronously; running tasks must finish first."""
    row = db.get(PublishAccountORM, account_db_id)
    if row is None:
        raise HTTPException(404, f"未找到发布账号：id={account_db_id}")
    if row.status == "retired":
        return {"ok": True, "cancelled": 0}
    running = (db.query(PublishDispatchTaskORM)
                 .filter(PublishDispatchTaskORM.account_db_id == row.id,
                         PublishDispatchTaskORM.status.in_(("claimed", "running"))).count())
    if running:
        raise HTTPException(409, f"账号有 {running} 个执行中任务，结束后才能删除")
    now = datetime.utcnow()
    queued = (db.query(PublishDispatchTaskORM)
                .filter(PublishDispatchTaskORM.account_db_id == row.id,
                        PublishDispatchTaskORM.status == "queued").all())
    for task in queued:
        task.status = "abandoned"
        task.finished_at = now
        task.last_error = "账号已从账号中心删除（软下线），任务取消"
        task.updated_at = now
    row.status = "retired"
    row.lease_worker_id = None
    row.lease_until = None
    row.updated_at = now
    db.commit()
    _audit("publish_account_retire", row.id, current_user,
           {"cancelled_queued_tasks": len(queued)})
    return {"ok": True, "cancelled": len(queued)}


@router.patch("/publish-accounts/{account_db_id}/schedule")
def update_account_schedule(account_db_id: int, payload: PublishAccountScheduleIn,
                            db: Session = Depends(get_db),
                            current_user=Depends(require_roles("admin"))):
    """Change the daily quota and account-level cooldown; does not preempt or cancel already claimed tasks."""
    row = db.get(PublishAccountORM, account_db_id)
    if row is None or row.status == "retired":
        raise HTTPException(404, f"未找到发布账号或账号已删除：id={account_db_id}")
    if payload.reset_daily_cap and payload.daily_cap is not None:
        raise HTTPException(422, "reset_daily_cap 与 daily_cap 不能同时设置")
    if (not payload.reset_daily_cap and payload.daily_cap is None
            and payload.cooldown_minutes is None):
        raise HTTPException(422, "至少修改日配额或冷却时间之一")

    now = datetime.utcnow()
    old = {"daily_cap": row.daily_cap, "cooldown_minutes": row.cooldown_minutes}
    if payload.reset_daily_cap:
        row.daily_cap = None
    elif payload.daily_cap is not None:
        row.daily_cap = payload.daily_cap
    if payload.cooldown_minutes is not None:
        row.cooldown_minutes = payload.cooldown_minutes
    row.updated_at = now
    db.commit()
    db.refresh(row)
    after = {"daily_cap": row.daily_cap, "cooldown_minutes": row.cooldown_minutes}
    _audit("publish_account_schedule_update", account_db_id, current_user,
           {"before": old, "after": after, "reason": payload.reason})
    return {"ok": True,
            "account": account_response(row, now=now, today=local_today())}


@router.post("/publish-accounts/{account_db_id}/rebind-worker")
def rebind_worker(account_db_id: int, payload: PublishAccountRebindIn,
                  db: Session = Depends(get_db),
                  current_user=Depends(require_roles("admin"))):
    """Rebind an account to another worker. **Admin only.**

    Why this escape hatch exists: owner_worker_id is a lifetime binding with no grab fallback — the benefit is that an account never
    appears on two egress IPs at once; the cost is that when a worker is retired, its accounts would be stranded forever.

    Why proxy_id must be confirmed at the same time: changing worker means changing machine, and the machine fingerprint (TLS fingerprint, font list,
    timezone, kernel features) changes wholesale. That is a stealthier and harder ban signal than IP drift, so when changing machines
    the egress usually needs to be thought through as well, not changed in passing.

    The account must have no in-flight lease — rebinding mid-publish would create "two egresses".
    """
    row = db.get(PublishAccountORM, account_db_id)
    if row is None:
        raise HTTPException(404, f"未找到发布账号：id={account_db_id}")
    now = datetime.utcnow()
    if row.lease_until and row.lease_until > now:
        raise HTTPException(409, f"账号有在途租约(至 {row.lease_until.isoformat()}),"
                                 f"等它结束或过期后再改绑")
    egress = (payload.egress or EGRESS_PROXY).strip().lower()
    if egress not in (EGRESS_PROXY, EGRESS_HOST):
        raise HTTPException(422, f"egress={egress!r} 不支持")
    proxy = None
    if egress == EGRESS_PROXY:
        if payload.auto_proxy and payload.proxy_id:
            raise HTTPException(422, "自动分配代理时不要同时指定 proxy_id")
        if payload.auto_proxy:
            proxy = _auto_select_proxy(db, row.platform)
        elif not payload.proxy_id:
            raise HTTPException(422, "固定代理模式必须选择 proxy_id")
        else:
            proxy = db.get(EngineProxyORM, payload.proxy_id)
            if proxy is None or proxy.disabled:
                raise HTTPException(422, f"proxy_id={payload.proxy_id} 不存在或已停用")
    elif payload.auto_proxy or payload.proxy_id:
        raise HTTPException(422, "本机直连模式不能同时指定代理")
    old = (row.owner_worker_id, row.proxy_id, row.egress)
    row.owner_worker_id = payload.owner_worker_id
    row.proxy_id = proxy.id if proxy else None
    row.egress = egress
    # The egress is about to change → the old exit_ip observation is invalid. Keeping it would make the next receipt misjudge "egress change" as
    # exit_ip_drift and push a good account to unschedulable.
    row.exit_ip = None
    row.exit_ip_at = None
    row.lease_worker_id = None
    row.lease_until = None
    row.updated_at = now
    db.commit()
    log.warning("[publish-pool] rebind account=%s worker %s→%s proxy %s→%s egress %s→%s by=%s reason=%r",
                account_db_id, old[0], payload.owner_worker_id, old[1], row.proxy_id,
                old[2], egress,
                getattr(current_user, "id", None), payload.reason)
    _audit("publish_account_rebind", account_db_id, current_user, {
        "from_worker": old[0], "to_worker": payload.owner_worker_id,
        "from_proxy": old[1], "to_proxy": row.proxy_id,
        "from_egress": old[2], "to_egress": egress, "reason": payload.reason,
    })
    return {"ok": True, "account": account_response(row, now=now, today=local_today())}


@router.post("/publish-accounts/{account_db_id}/reinstate")
def reinstate_account(account_db_id: int,
                      reason: Annotated[str, Query(min_length=1)],
                      db: Session = Depends(get_db),
                      current_user=Depends(require_roles("admin"))):
    """Put an unschedulable account back to active. **Admin only, and a reason is required.**

    unschedulable is the only state that cannot recover automatically (egress IP drift / suspected ban): further automatic retries
    would only turn "possibly flagged" into "definitely banned". So recovering it must be an explicit action by a person who checked the platform back office
    and confirmed the account is fine — not callable by the healer or any automatic path.
    """
    row = db.get(PublishAccountORM, account_db_id)
    if row is None:
        raise HTTPException(404, f"未找到发布账号：id={account_db_id}")
    if row.status != "unschedulable":
        raise HTTPException(409, f"账号当前状态是 {row.status},不需要 reinstate")
    now = datetime.utcnow()
    row.last_fail_type = None
    row.last_fail_reason = None
    row.last_fail_at = None
    row.fail_counts_json = "{}"
    row.status = "active"
    row.warmup_until = None
    row.updated_at = now
    db.commit()
    log.warning("[publish-pool] reinstate account=%s by=%s reason=%r",
                account_db_id, getattr(current_user, "id", None), reason)
    _audit("publish_account_reinstate", account_db_id, current_user, {"reason": reason})
    return {"ok": True, "account": account_response(row, now=now, today=local_today())}


@router.get("/publish-accounts/pool-status", response_model=list[PublishPoolStatusEntry])
def pool_status(db: Session = Depends(get_db)):
    """Pool level (F7). Four questions: how many accounts can still publish / how many articles can still go out today / which accounts are about to drop /
    are there signs of correlated bans.

    At scale this dashboard replaces the per-account view — nobody reads 80 accounts row by row.
    """
    now, today = datetime.utcnow(), local_today()
    soon = now + timedelta(hours=48)
    rows = (db.query(PublishAccountORM)
              .filter(PublishAccountORM.channel == CHANNEL_SERVER_POOL,
                      PublishAccountORM.status != "retired").all())
    by_platform: dict[str, list[PublishAccountORM]] = {}
    for r in rows:
        by_platform.setdefault(r.platform, []).append(r)

    out: list[PublishPoolStatusEntry] = []
    for platform in sorted(by_platform):
        group = by_platform[platform]
        eff = {r.id: effective_status(r, now) for r in group}
        unlimited = False
        remaining = 0
        for r in group:
            if eff[r.id] not in ("active", "warming"):
                continue
            cap = effective_cap(r, now)
            if cap == 0:
                unlimited = True
                continue
            used = r.used_today if r.used_date == today else 0
            remaining += max(0, cap - used)
        out.append(PublishPoolStatusEntry(
            platform=platform,
            total=len(group),
            schedulable=sum(1 for r in group if eff[r.id] in ("active", "warming")),
            warming=sum(1 for r in group if eff[r.id] == "warming"),
            rate_limited=sum(1 for r in group if eff[r.id] == "rate_limited"),
            quarantined=sum(1 for r in group if eff[r.id] == "quarantined"),
            unschedulable=sum(1 for r in group if eff[r.id] == "unschedulable"),
            expiring_soon=sum(1 for r in group
                              if r.expires_at and now < r.expires_at <= soon),
            remaining_today=None if unlimited else remaining,
            egress_groups_at_risk=_egress_risk(group, now),
        ))
    return out


def _egress_risk(group: list[PublishAccountORM], now: datetime) -> list[dict]:
    """Signs of correlated bans: groups with batch anomalies on the same egress (F7's fourth question).

    One account having a problem says little; **several accounts on the same egress failing together** is the signal that "this IP is flagged".
    Grouping uses proxy_id first, exit_ip as fallback — the same definition as _cool_down_siblings.
    """
    buckets: dict[str, list[PublishAccountORM]] = {}
    for r in group:
        key = f"proxy:{r.proxy_id}" if r.proxy_id else (r.exit_ip or "")
        if key:
            buckets.setdefault(key, []).append(r)
    out = []
    for key, rows in sorted(buckets.items()):
        bad = [r for r in rows
               if effective_status(r, now) in ("rate_limited", "quarantined",
                                               "unschedulable")]
        # ≥2 bad accounts and more than half — an egress with only 1 account never reports (that is a single-account failure)
        if len(bad) >= 2 and len(bad) * 2 >= len(rows):
            out.append({"egress": key, "total": len(rows), "bad": len(bad),
                        "account_ids": [r.id for r in bad][:20]})
    return out


def _audit(event: str, object_id: int, current_user, detail: dict) -> None:
    """Audit trail of account-level admin actions.

    Deliberately **not written** to compliance_audit_logs: that table is topic-scoped (topic_id NOT NULL,
    indexes built per topic, event_type a closed enum), while actions like "rebind worker" or "restore disabled account"
    have no topic at all. Stuffing topic_id=0 in would pollute the per-topic compliance audit view.

    So this uses structured application logs (WARNING level, all fields, indexable by log collection).
    The **per-publish** trail F10 requires (operator/account/timestamp/content snapshot/failure screenshot) is a separate thing:
    it has a topic_id and lands in topic_doc_publish_tasks.proof_json + publish_dispatch_tasks
    screenshot_path + compliance_audit channel_publish events — part of development step 6.
    """
    log.warning("[publish-pool][audit] event=%s account=%s actor=%s(%s) detail=%s",
                event, object_id, getattr(current_user, "id", None),
                getattr(current_user, "username", None) or "?",
                json.dumps(detail, ensure_ascii=False))
