from __future__ import annotations

import asyncio
import hashlib
from unittest import TestCase

from app.publishers.baijia import BaijiaPublisher


class _Response:
    status = 200

    async def json(self):
        return {"errno": 0, "errmsg": "success",
                "data": {"user": {"userid": "user-1", "name": "测试号"}}}


class _Request:
    async def get(self, *_args, **_kwargs):
        return _Response()


class _Page:
    request = _Request()


class BaijiaPublisherTests(TestCase):
    def test_verify_identity_uses_appinfo_userid(self):
        value = asyncio.run(BaijiaPublisher().verify_identity(_Page()))
        expected = "api.appinfo.userid:" + hashlib.sha256(b"user-1").hexdigest()[:16]
        self.assertEqual(value, expected)

    def test_account_handle_uses_appinfo_name(self):
        value = asyncio.run(BaijiaPublisher().account_handle(_Page()))
        self.assertEqual(value, "测试号")

    def test_login_entry_is_exposed(self):
        self.assertEqual(BaijiaPublisher.login_url, "https://baijiahao.baidu.com/")
