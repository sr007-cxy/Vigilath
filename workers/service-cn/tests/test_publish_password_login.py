from __future__ import annotations

import asyncio
import base64
import time
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import publish_api
from app.publish_api import (ManualLoginSessionIn, PasswordLoginIn, WebLoginIn,
                             login_password, login_web,
                             manual_login_cancel, manual_login_poll, publish_platforms)
from app.publishers.core_backed import CoreBackedPlatform, CoreBackedPublisher


class _FakeContextManager:
    def __init__(self, page=None):
        self.closed = False
        self.page = page

    async def __aenter__(self):
        return self.page

    async def __aexit__(self, *_args):
        self.closed = True


class _FakePage:
    def __init__(self):
        self.url = None
        self.screenshot_calls = 0

    async def goto(self, url, **_kwargs):
        self.url = url

    async def screenshot(self, **_kwargs):
        self.screenshot_calls += 1
        return b"png"


class _FakeLocator:
    def __init__(self, *, visible=False, png=b"qr", box=None, on_click=None):
        self.visible = visible
        self.png = png
        self.box = box or {"width": 200, "height": 200}
        self.clicked = False
        self.on_click = on_click

    @property
    def first(self):
        return self

    async def is_visible(self, **_kwargs):
        return self.visible

    async def click(self, **_kwargs):
        self.clicked = True
        if self.on_click:
            self.on_click()

    async def bounding_box(self):
        return self.box

    async def screenshot(self, **_kwargs):
        return self.png


class _FakeQrPage(_FakePage):
    def __init__(self):
        super().__init__()
        self.qr = _FakeLocator(visible=False, png=b"qr-png")
        self.entry = _FakeLocator(visible=True, on_click=lambda: setattr(self.qr, "visible", True))
        self.hidden = _FakeLocator(visible=False)
        self.locator_calls = 0

    def locator(self, selector):
        self.locator_calls += 1
        if selector == "text=扫码登录":
            return self.entry
        if selector == "[class*='qrcode']":
            return self.qr
        return self.hidden


class _FakePreferredQrPage(_FakePage):
    def __init__(self):
        super().__init__()
        self.platform_qr = _FakeLocator(visible=True, png=b"zhihu-canvas")
        self.default_qr = _FakeLocator(visible=True, png=b"default-qrcode")

    def locator(self, selector):
        if selector == "canvas":
            return self.platform_qr
        if selector == "[class*='qrcode']":
            return self.default_qr
        return _FakeLocator(visible=False)


class _FakeStorageContext:
    def __init__(self, cookies=None):
        self.cookies = cookies or []

    async def storage_state(self):
        return {"cookies": self.cookies}


class _FakeCorePage(_FakePage):
    def __init__(self, cookies=None):
        super().__init__()
        self.context = _FakeStorageContext(cookies)


class _FakeCoreQrPage(_FakeCorePage):
    def __init__(self, cookies=None):
        super().__init__(cookies)
        self.qr = _FakeLocator(visible=True)

    def locator(self, selector):
        if selector == "canvas":
            return self.qr
        return _FakeLocator(visible=False)


class _FakeAdapter:
    login_url = "https://www.zhihu.com/signin"

    def __init__(self, account_id=None, account_handle=None):
        self.account_id = account_id
        self._account_handle = account_handle

    async def verify_identity(self, _page):
        return self.account_id

    async def account_handle(self, _page):
        return self._account_handle


class _FakeCoreAdapter(CoreBackedPublisher):
    meta = CoreBackedPlatform(
        code="zhihu",
        login_url="https://www.zhihu.com/signin",
        home_url="https://www.zhihu.com/",
        domains=("zhihu.com",),
        auth_cookie_names=("z_c0",),
    )


class PublishPasswordLoginTests(TestCase):
    def tearDown(self):
        publish_api._manual_login_sessions.clear()

    def test_login_channels_are_disabled_by_default(self):
        result = asyncio.run(publish_platforms())
        self.assertEqual(result["password_login_platforms"], [])
        self.assertEqual(result["web_login_platforms"], [])
        self.assertIn("xhs", result["core_supported_platforms"])

    def test_web_login_channel_disabled_before_browser_launch(self):
        req = WebLoginIn(platform="zhihu", proxy_url=None)
        with patch.object(publish_api, "account_page") as account_page_mock, \
                self.assertRaises(HTTPException) as cm:
            asyncio.run(login_web(req, None))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("disabled", str(cm.exception.detail))
        account_page_mock.assert_not_called()

    def test_unsupported_web_login_fails_before_browser_launch(self):
        req = WebLoginIn(platform="unknown", proxy_url=None)
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(login_web(req, None))
        self.assertEqual(cm.exception.status_code, 400)

    def test_web_login_opens_platform_page_and_holds_session(self):
        page = _FakePage()
        cm = _FakeContextManager(page)
        req = WebLoginIn(platform="zhihu", proxy_url=None)
        with patch.object(publish_api, "account_page", return_value=cm), \
                patch.object(publish_api.asyncio, "sleep", new=AsyncMock()):
            result = asyncio.run(publish_api._login_web_inner(_FakeAdapter(), req))
        self.assertTrue(result["manual_required"])
        self.assertEqual(page.url, _FakeAdapter.login_url)
        self.assertIn(result["manual_session_id"], publish_api._manual_login_sessions)
        self.assertFalse(cm.closed)

    def test_host_egress_accepts_null_proxy_url(self):
        req = PasswordLoginIn(platform="zhihu", identifier="someone",
                              password="secret", proxy_url=None)
        self.assertIsNone(req.proxy_url)

    def test_unsupported_platform_fails_before_browser_launch(self):
        req = PasswordLoginIn(platform="toutiao", identifier="someone",
                              password="secret", proxy_url="http://127.0.0.1:8888")
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(login_password(req, None))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertNotIn("secret", str(cm.exception.detail))

    def test_password_login_channel_disabled_before_browser_launch(self):
        req = PasswordLoginIn(platform="zhihu", identifier="someone",
                              password="secret", proxy_url=None)
        with patch.object(publish_api, "account_page") as account_page_mock, \
                self.assertRaises(HTTPException) as cm:
            asyncio.run(login_password(req, None))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("disabled", str(cm.exception.detail))
        account_page_mock.assert_not_called()

    def test_manual_poll_keeps_pending_session_and_returns_preview(self):
        cm = _FakeContextManager()
        publish_api._manual_login_sessions["a" * 24] = {
            "cm": cm, "page": _FakePage(), "adapter": _FakeAdapter(),
            "platform": "zhihu", "expected_account_id": None,
            "created": time.monotonic(),
        }
        result = asyncio.run(manual_login_poll(ManualLoginSessionIn(session_id="a" * 24), None))
        self.assertEqual(result["status"], "pending")
        self.assertIn("qr_image", result)
        self.assertIsNone(result["qr_image"])
        self.assertTrue(result["preview_image"].startswith("data:image/png;base64,"))
        self.assertIn("a" * 24, publish_api._manual_login_sessions)
        self.assertFalse(cm.closed)

    def test_manual_qr_image_missing_locator_is_non_blocking(self):
        result = asyncio.run(publish_api._manual_qr_image("zhihu", _FakePage(), click_entry=True))
        self.assertIsNone(result)

    def test_manual_qr_image_skips_no_qr_platforms(self):
        page = _FakeQrPage()
        result = asyncio.run(publish_api._manual_qr_image("sohu", page, click_entry=True))
        self.assertIsNone(result)
        self.assertEqual(page.locator_calls, 0)

    def test_manual_qr_image_clicks_entry_and_returns_qr(self):
        page = _FakeQrPage()
        with patch.object(publish_api.asyncio, "sleep", new=AsyncMock()):
            result = asyncio.run(publish_api._manual_qr_image("unknown", page, click_entry=True))
        self.assertTrue(page.entry.clicked)
        self.assertTrue(result.startswith("data:image/png;base64,"))

    def test_manual_qr_image_prefers_platform_selector(self):
        page = _FakePreferredQrPage()
        result = asyncio.run(publish_api._manual_qr_image("zhihu", page, click_entry=False))
        want = "data:image/png;base64," + base64.b64encode(b"zhihu-canvas").decode("ascii")
        self.assertEqual(result, want)

    def test_hold_manual_login_skips_preview_when_qr_exists(self):
        page = _FakeQrPage()
        cm = _FakeContextManager(page)
        req = WebLoginIn(platform="unknown", proxy_url=None)
        with patch.object(publish_api.asyncio, "sleep", new=AsyncMock()):
            result = asyncio.run(publish_api._hold_manual_login(cm, page, _FakeAdapter(), req))
        self.assertTrue(result["qr_image"].startswith("data:image/png;base64,"))
        self.assertIsNone(result["preview_image"])
        self.assertEqual(page.screenshot_calls, 0)

    def test_manual_poll_reuses_cached_qr_image(self):
        page = _FakeQrPage()
        cm = _FakeContextManager()
        cached = "data:image/png;base64," + base64.b64encode(b"cached").decode("ascii")
        publish_api._manual_login_sessions["c" * 24] = {
            "cm": cm, "page": page, "adapter": _FakeAdapter(),
            "platform": "zhihu", "expected_account_id": None,
            "created": time.monotonic(), "qr_image": cached,
        }
        result = asyncio.run(manual_login_poll(ManualLoginSessionIn(session_id="c" * 24), None))
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["qr_image"], cached)
        self.assertIsNone(result["preview_image"])
        self.assertEqual(page.locator_calls, 0)
        self.assertEqual(page.screenshot_calls, 0)

    def test_core_manual_poll_skips_sidecar_until_login_cookie_exists(self):
        cached = "data:image/png;base64," + base64.b64encode(b"cached").decode("ascii")
        publish_api._manual_login_sessions["d" * 24] = {
            "cm": _FakeContextManager(), "page": _FakeCorePage(), "adapter": _FakeCoreAdapter(),
            "platform": "zhihu", "expected_account_id": None,
            "created": time.monotonic(), "qr_image": cached,
        }
        with patch.object(publish_api, "verify_identity_with_core", new=AsyncMock()) as verify:
            result = asyncio.run(manual_login_poll(ManualLoginSessionIn(session_id="d" * 24), None))
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["qr_image"], cached)
        verify.assert_not_awaited()

    def test_core_manual_poll_skips_sidecar_while_qr_is_visible(self):
        cached = "data:image/png;base64," + base64.b64encode(b"cached").decode("ascii")
        publish_api._manual_login_sessions["e" * 24] = {
            "cm": _FakeContextManager(),
            "page": _FakeCoreQrPage([{"name": "z_c0", "value": "guest", "domain": ".zhihu.com"}]),
            "adapter": _FakeCoreAdapter(),
            "platform": "zhihu", "expected_account_id": None,
            "created": time.monotonic(), "qr_image": cached,
        }
        with patch.object(publish_api, "verify_identity_with_core", new=AsyncMock()) as verify:
            result = asyncio.run(manual_login_poll(ManualLoginSessionIn(session_id="e" * 24), None))
        self.assertEqual(result["status"], "pending")
        verify.assert_not_awaited()

    def test_manual_cancel_closes_browser_session(self):
        cm = _FakeContextManager()
        publish_api._manual_login_sessions["b" * 24] = {
            "cm": cm, "page": _FakePage(), "adapter": _FakeAdapter(),
            "platform": "zhihu", "expected_account_id": None,
            "created": time.monotonic(),
        }
        result = asyncio.run(manual_login_cancel(ManualLoginSessionIn(session_id="b" * 24), None))
        self.assertTrue(result["ok"])
        self.assertTrue(cm.closed)
        self.assertNotIn("b" * 24, publish_api._manual_login_sessions)
