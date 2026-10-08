"""Build browser pages per account - the context entry for the publish channel.

A thin wrapper that does only two things:
  1. connect "explicit storage_state + explicit proxy" to browser.create_stealth_page;
  2. make **the same account get the same fingerprint on every publish**, and ensure cleanup goes through close_stealth_context.

Why the login session and proxy are passed explicitly instead of derived from engine_name like engines:
  · the login session comes from backend publish_accounts (decrypted and delivered over HTTP), not from session_store's engine slots;
  · 🔴 the proxy must be the one bound to this account and must not fall back to the PROXY_ENGINES allowlist or environment default -
    the login proxy must exactly equal the runtime proxy. The engine pool left an ENGINE_AUTHZ_PROXY fallback to get around
    deepseek login page regional restrictions; publish accounts may not copy that (design §4 P0 item 3:
    login proxy != runtime proxy -> off-site on the first publish).

The fingerprint layer (anti_detect's UA/WebGL/canvas/WebRTC spoofing) is done by apply_stealth_to_context inside
create_stealth_page; this only decides **which** profile to use.
"""
from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from typing import Optional

from ..anti_detect import _UA_PROFILES
from ..browser import close_stealth_context, create_stealth_page

_log = logging.getLogger(__name__)


def stable_profile(seed: Optional[str], ua_platform: Optional[str] = None) -> dict:
    """Deterministically pick a fingerprint profile per account - the same seed always gets the same one.

    **Cannot use anti_detect._pick_profile**: that function does `random.choice` among candidates, which is fine for engine
    queries (each query may well be "another device"), but for publishing, the same account switching fingerprints
    is switching devices. Different machines differ in TLS fingerprint, font list, time zone and kernel features, a
    **stealthier and harder** ban signal than IP drift (design §F4).

    ua_platform: the browser platform string recorded at registration ("MacIntel" / "Linux x86_64").
    If given, only profiles of that platform are picked - existing accounts' visit history came from a certain kind of device,
    and suddenly switching OS is equally a behavioral jump. When the fingerprint library has no profile for that platform, degrade to picking from the whole library
    and log a line, rather than silently pretending it is pinned.
    """
    pool = _UA_PROFILES
    if ua_platform:
        matched = [p for p in _UA_PROFILES if p.get("platform") == ua_platform]
        if matched:
            pool = matched
        else:
            _log.warning("[publish] no fingerprint profile for ua_platform=%r "
                         "— falling back to full pool (fingerprint NOT pinned to OS)",
                         ua_platform)
    if not seed:
        # Without a stable seed, consistency across runs cannot be guaranteed. Taking the first profile (rather than random) at least keeps it stable
        # within the same process and platform, and logs the fact - in production account_id always has a value.
        _log.warning("[publish] no fingerprint seed provided — profile is not account-stable")
        return pool[0]
    idx = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % len(pool)
    return pool[idx]


@asynccontextmanager
async def account_page(platform: str, storage_state: dict, *,
                       proxy_url: Optional[str] = None,
                       ua_platform: Optional[str] = None,
                       fingerprint_seed: Optional[str] = None,
                       headless: Optional[bool] = None):
    """Open a page bound to this account's login session + egress + fingerprint; always cleaned up on exit.

    Pass the account's account_id as fingerprint_seed - see the docstring of stable_profile.

    Pass `publish:<platform>` as engine_name: it is only used for logs and recording directory names, and this prefix
    guarantees it never matches any engine name in the PROXY_ENGINES allowlist - so even if proxy_url is not passed,
    it will not accidentally fall onto the engine pool's default proxy.
    """
    profile = stable_profile(fingerprint_seed, ua_platform)
    context = None
    try:
        page, context = await create_stealth_page(
            f"publish:{platform}",
            profile=profile,
            storage_state=storage_state,
            proxy_url=proxy_url,
            headless=headless,
        )
        yield page
    finally:
        # 🔴 Must use close_stealth_context, not context.close(): when going through a proxy,
        # create_stealth_page opens a dedicated browser + playwright instance and hangs the handles on the
        # context, so only close(context) leaks a chromium process per publish.
        # (main.py's /fetch-title uses a bare close and only gets away with it because its "_title_probe" never matches
        #  a proxy engine; publishing always uses a proxy.)
        await close_stealth_context(context)
