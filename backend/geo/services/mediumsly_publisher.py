"""推送审完稿到 Mediumsly 站点。

约束:
  - 单 token(MEDIUMSLY_INTERNAL_API_TOKEN);整套调用是受信任的服务器到服务器调用。
  - 用户身份(bot 代发模型):**不**把真用户邮箱推到 Mediumsly,而是统一用
    `user-<id>@<bot_domain>` 这种受控邮箱注册作者。bot_domain 取自
    MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST 的第一项(排序后)。这样:
      • 同一 GEO user 在 Mediumsly 上是同一个稳定作者(email upsert key)
      • 真用户邮箱永不外泄到第三方站点
      • author.name 仍用 user.name(品牌方姓名照常展示)
  - 重推:有 mediumsly_post_id 走 PATCH,没有走 POST。PATCH 返回 404 说明 Mediumsly
    那边文章被手动删了 —— publisher 抛 MediumslyPostGone,调用方应清空本地
    mediumsly_post_id 后下次重试自动走 POST 重建。

硬化(都在本文件落地,不依赖运行环境):
  - HTTPS 强制(URL 必须 https://)
  - 必须配 MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST(为空 = 配置错误,拒发),
    publisher 用这个域合成 bot 邮箱,实际上锁死了 Mediumsly 上的作者命名空间
  - 并发 semaphore(防止 GEO 侧 bug 循环里调本函数把对端打爆)

完整对接文档:mediumsly repo 的 docs/04-internal-api.md + docs/05-geo-integration.md。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from geo.database import settings
from geo.models.ai_telemetry import TopicGeneratedDocORM
from geo.models.user import UserORM

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 8.0  # seconds — Mediumsly 写入正常 < 200ms,留余量给冷启动 / 跨地域 RTT

# 限并发,防止 GEO 侧 bug(循环里调 publish)瞬间打爆对端。
_CONCURRENCY = asyncio.Semaphore(5)


def _allowed_domains() -> frozenset[str]:
    """从 env 解析允许的 email 域名集合。"""
    raw = (settings.MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST or "").strip()
    if not raw:
        return frozenset()
    return frozenset(d.strip().lower() for d in raw.split(",") if d.strip())


class MediumslyError(Exception):
    """所有外发失败统一抛这个;publish endpoint 捕获后写入 mediumsly_last_error。"""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class MediumslyNotConfigured(MediumslyError):
    """MEDIUMSLY_INTERNAL_API_TOKEN 未设置;publish endpoint 看到这类异常就降级到只标记 publish_targets。"""


class MediumslyPostGone(MediumslyError):
    """PATCH 时 404 —— Mediumsly 那边文章已被删。调用方应清空 mediumsly_post_id 后重试 POST。"""


@dataclass
class PushResult:
    post_id: str
    url: str
    author_created: bool  # Mediumsly 那边是否新建了账号(POST 路径返回 True,PATCH 路径不带这字段固定 False)


def _platform_tags(doc: TopicGeneratedDocORM) -> list[str]:
    """从 doc.platform("抖音"/"小红书"/...)派生 Mediumsly tag,最多 5 个。"""
    tags: list[str] = []
    if doc.platform:
        tags.append(doc.platform)
    # 后续可加 topic 名作为 tag 等,先保持最小。
    return tags[:5]


def _bot_email_for(user: UserORM, allowed_domains: frozenset[str]) -> str:
    """合成 bot 邮箱:`user-<user_id>@<bot_domain>`。
    bot_domain 取 allowlist 第一项(排序后,deterministic)。
    """
    # sorted 让多域名时选择稳定,实际生产通常只配 1 个
    bot_domain = sorted(allowed_domains)[0]
    return f"user-{user.id}@{bot_domain}"


async def push(doc: TopicGeneratedDocORM, user: UserORM) -> PushResult:
    """把 doc 推到 Mediumsly。返回 PushResult 或抛 MediumslyError 子类。"""
    if not settings.MEDIUMSLY_INTERNAL_API_TOKEN:
        raise MediumslyNotConfigured("MEDIUMSLY_INTERNAL_API_TOKEN not set")

    base = (settings.MEDIUMSLY_API_URL or "").rstrip("/")
    if not base.startswith("https://"):
        raise MediumslyError(
            f"MEDIUMSLY_API_URL must be https:// (got {base!r})",
            code="CONFIG",
        )

    allowed = _allowed_domains()
    if not allowed:
        # bot 代发模型要求必须配 — 防止有人忘配后真用户邮箱被推到 Mediumsly
        raise MediumslyError(
            "MEDIUMSLY_EMAIL_DOMAIN_ALLOWLIST must be set (provides bot email domain)",
            code="CONFIG",
        )

    headers = {
        "Authorization": f"Bearer {settings.MEDIUMSLY_INTERNAL_API_TOKEN}",
        "Content-Type": "application/json",
    }

    body: dict = {
        "title": doc.title or doc.source_query_text or f"Topic doc #{doc.id}",
        "body_markdown": doc.body_markdown or "",
        "tags": _platform_tags(doc),
        "status": "published",
    }

    async with _CONCURRENCY, httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            if doc.mediumsly_post_id:
                # 原地更新已发布的文章。Mediumsly 不允许改 author —— 不带 author 字段。
                r = await client.patch(
                    f"{base}/api/internal/posts/{doc.mediumsly_post_id}",
                    headers=headers, json=body,
                )
                if r.status_code == 404:
                    raise MediumslyPostGone(
                        "Post not found at Mediumsly (was it deleted?)",
                        status=404, code="NOT_FOUND",
                    )
            else:
                # 第一次推:bot 代发 — 用 user-<id>@<bot_domain> 作为 Mediumsly 那边的
                # 作者 email(upsert key),真用户邮箱只用于 GEO 内部审核通知,
                # 永不外泄到 Mediumsly。展示用的 name 仍取自 user.name(品牌方姓名)。
                bot_email = _bot_email_for(user, allowed)
                body["author"] = {
                    "email": bot_email,
                    "name": user.name or (user.email.split("@")[0] if user.email else f"user-{user.id}"),
                }
                r = await client.post(
                    f"{base}/api/internal/posts",
                    headers=headers, json=body,
                )
        except httpx.RequestError as e:
            raise MediumslyError(f"Network error: {e!s}", status=None, code="NETWORK") from e

    if r.status_code >= 400:
        # Mediumsly 错误格式:{"error": {"code": "...", "message": "..."}}
        try:
            err = r.json().get("error", {}) or {}
        except Exception:  # noqa: BLE001
            err = {}
        raise MediumslyError(
            err.get("message") or (r.text or "")[:200] or f"HTTP {r.status_code}",
            status=r.status_code,
            code=err.get("code"),
        )

    data = r.json()
    post = data["post"]
    author = data.get("author") or {}
    return PushResult(
        post_id=str(post["id"]),
        url=str(post["url"]),
        author_created=bool(author.get("created", False)),
    )
