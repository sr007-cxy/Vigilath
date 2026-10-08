"""Core-backed publisher adapters.

These adapters only make platforms appear in the publish account center and centrally maintain login metadata.
They deliberately do not implement the final publish click in Python: real publishing must go through the WechatSync Core sidecar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .base import (FAIL_NOT_IMPLEMENTED, PublisherAdapter, PublishRequest,
                   PublishResult, account_hash)

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoreBackedPlatform:
    code: str
    login_url: str
    home_url: str
    domains: tuple[str, ...]
    auth_cookie_names: tuple[str, ...] = ()
    auth_cookie_keywords: tuple[str, ...] = field(default_factory=lambda: (
        "session", "token", "auth", "sid", "uid", "user", "passport",
        "login", "ticket",
    ))


class CoreBackedPublisher(PublisherAdapter):
    """Base class for platforms whose publish and identity channels are both taken over by Core."""

    meta: CoreBackedPlatform

    @property
    def platform(self) -> str:
        return self.meta.code

    @property
    def login_url(self) -> str:
        return self.meta.login_url

    @property
    def home_url(self) -> str:
        return self.meta.home_url

    async def verify_identity(self, page) -> Optional[str]:
        try:
            state = await page.context.storage_state()
        except Exception as exc:  # noqa: BLE001
            _log.warning("[%s] storage_state identity probe failed: %s",
                         self.platform, exc)
            return None
        cookies = state.get("cookies") or []
        selected = self._pick_identity_cookie(cookies)
        if not selected:
            return None
        name, value = selected
        return account_hash(f"cookie.{self.platform}.{name}", value)

    def _pick_identity_cookie(self, cookies: list[dict]) -> Optional[tuple[str, str]]:
        scoped = []
        domains = tuple(d.lower().lstrip(".") for d in self.meta.domains)
        for cookie in cookies:
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            domain = str(cookie.get("domain") or "").lower().lstrip(".")
            if not name or not value or not domain:
                continue
            if not any(domain == d or domain.endswith(f".{d}") for d in domains):
                continue
            scoped.append((name, value))

        for wanted in self.meta.auth_cookie_names:
            for name, value in scoped:
                if name == wanted:
                    return name, value

        keywords = tuple(k.lower() for k in self.meta.auth_cookie_keywords)
        candidates = [
            (name, value) for name, value in scoped
            if any(k in name.lower() for k in keywords)
        ]
        if not candidates:
            return None
        # Prefer a stable-looking, high-entropy cookie and keep ordering stable.
        return sorted(candidates, key=lambda x: (-len(x[1]), x[0]))[0]

    async def publish(self, page, req: PublishRequest) -> PublishResult:
        return PublishResult(
            ok=False,
            fail_type=FAIL_NOT_IMPLEMENTED,
            error=f"{self.platform} 真实发布必须通过 WechatSync Core sidecar",
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


class ToutiaoPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="toutiao",
        login_url="https://mp.toutiao.com/auth/page/login/",
        home_url="https://mp.toutiao.com/profile_v4/",
        domains=("toutiao.com", "mp.toutiao.com"),
        auth_cookie_names=("sessionid", "sid_tt", "uid_tt", "passport_auth_status"),
    )


class ZhihuPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="zhihu",
        login_url="https://www.zhihu.com/signin",
        home_url="https://www.zhihu.com/",
        domains=("zhihu.com", "zhuanlan.zhihu.com"),
        auth_cookie_names=("z_c0",),
    )


class BaijiaPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="baijia",
        login_url="https://baijiahao.baidu.com/",
        home_url="https://baijiahao.baidu.com/builder/content/manage",
        domains=("baidu.com", "baijiahao.baidu.com"),
        auth_cookie_names=("BDUSS", "STOKEN", "BAIDUID"),
    )


class SohuPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="sohu",
        login_url="https://mp.sohu.com/login/home",
        home_url="https://mp.sohu.com/",
        domains=("sohu.com", "mp.sohu.com"),
        auth_cookie_names=("SUV", "gidinf", "ppinf", "pprdig"),
    )


class CsdnPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="csdn",
        login_url="https://passport.csdn.net/login",
        home_url="https://mp.csdn.net/mp_blog/manage/article",
        domains=("csdn.net",),
        auth_cookie_names=("UserToken", "UserName", "uuid_tt_dd"),
    )


class SmzdmPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="smzdm",
        login_url="https://zhiyou.smzdm.com/user/login/",
        home_url="https://post.smzdm.com/tougao/",
        domains=("smzdm.com",),
        auth_cookie_names=("sess", "smzdm_id", "user", "user_info"),
    )


class WxgzhPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="wxgzh",
        login_url="https://mp.weixin.qq.com/",
        home_url="https://mp.weixin.qq.com/",
        domains=("mp.weixin.qq.com", "weixin.qq.com"),
        auth_cookie_names=("slave_sid", "slave_user", "bizuin"),
    )


class XhsPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="xhs",
        login_url="https://creator.xiaohongshu.com/",
        home_url="https://creator.xiaohongshu.com/",
        domains=("xiaohongshu.com",),
        auth_cookie_names=("web_session", "a1", "xsecappid"),
    )


class DouyinPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="douyin",
        login_url="https://creator.douyin.com/creator-micro/home",
        home_url="https://creator.douyin.com/creator-micro/home",
        domains=("douyin.com",),
        auth_cookie_names=("sessionid", "sid_tt", "uid_tt", "passport_csrf_token"),
    )


class CnblogsPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="cnblogs",
        login_url="https://account.cnblogs.com/signin",
        home_url="https://i.cnblogs.com/posts/edit",
        domains=("cnblogs.com",),
        auth_cookie_names=(".Cnblogs.AspNetCore.Cookies", "UserToken"),
    )


class CtripPublisher(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="ctrip",
        login_url="https://passport.ctrip.com/user/login",
        home_url="https://we.ctrip.com/publish/contentManagement",
        domains=("ctrip.com", "m.ctrip.com", "we.ctrip.com", "passport.ctrip.com"),
        auth_cookie_names=("cticket", "weticket"),
    )
