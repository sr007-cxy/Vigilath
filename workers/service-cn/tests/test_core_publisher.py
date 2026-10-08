from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx

from app import core_publisher, publish_api
from app.publishers.core_backed import ZhihuPublisher


class Response:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://test")
            raise httpx.HTTPStatusError("failed", request=request,
                                        response=httpx.Response(self.status_code, request=request))


def request(**overrides):
    values = dict(platform="zhihu", account_id="api.me.url_token:abc", title="标题",
                  body="正文", body_format="markdown", source_markdown="![图](https://x/a.png)",
                  images=[],
                  storage_state={"cookies": [{"name": "z_c0"}]}, proxy_url=None,
                  user_agent=None, ua_platform=None, dispatch_id=42, attempt=1,
                  account_db_id=7, worker_id=9, dry_run=False)
    values.update(overrides)
    return SimpleNamespace(**values)


class CorePublisherTests(TestCase):
    def test_claimable_platforms_require_sidecar_url(self):
        with patch.dict(os.environ, {"WECHATSYNC_PUBLISHER_URL": "",
                                    "PUBLISH_CORE_PLATFORMS": ""}, clear=False):
            self.assertEqual(core_publisher.core_claimable_platforms(), {})
        with patch.dict(os.environ, {"WECHATSYNC_PUBLISHER_URL": "http://sidecar",
                                    "PUBLISH_CORE_PLATFORMS": ""}, clear=False):
            self.assertIn("zhihu", core_publisher.core_claimable_platforms())
            self.assertIn("cnblogs", core_publisher.core_claimable_platforms())

    def test_publish_endpoint_always_uses_core_backed_adapter(self):
        async def fake_publish_with_core(req):
            return {"ok": True, "published_url": "https://zhuanlan.zhihu.com/p/1"}

        async def fail_python_publish(*args, **kwargs):
            raise AssertionError("Core-backed platforms must not fall back to Python publish")

        with patch.object(publish_api, "get_publisher", return_value=ZhihuPublisher()), \
                patch.object(publish_api, "publish_with_core", fake_publish_with_core), \
                patch.object(publish_api, "_publish_inner", fail_python_publish), \
                patch.dict(os.environ, {"PUBLISH_SERVICE_TOKEN": ""}, clear=False):
            result = asyncio.run(publish_api.publish(request(account_db_id=None)))
        self.assertTrue(result["ok"])
        self.assertEqual(result["published_url"], "https://zhuanlan.zhihu.com/p/1")

    def test_prepare_progress_commit_order(self):
        calls = []
        bodies = []

        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                calls.append(url)
                bodies.append(kwargs.get("json"))
                if url.endswith("/prepare"):
                    return Response({"ok": True, "operation_id": "op", "draft_id": "123",
                                     "draft_url": "https://zhuanlan.zhihu.com/p/123/edit",
                                     "screenshot_path": "core/zhihu/a.png",
                                     "storage_state": {"cookies": [{"name": "rotated"}]}})
                if url.endswith("/progress"):
                    return Response({"ok": True})
                return Response({"ok": True, "published_url": "https://zhuanlan.zhihu.com/p/123",
                                 "url_source": "creator_center_anchor", "draft_id": 123})

        env = {"WECHATSYNC_PUBLISHER_URL": "http://sidecar",
               "PUBLISH_DISPATCH_URL": "http://backend", "ZHIHU_ALLOW_REAL_PUBLISH": "1"}
        with patch.object(core_publisher.httpx, "AsyncClient", Client), patch.dict(os.environ, env):
            result = asyncio.run(core_publisher.publish_with_core(request(
                location={"name": "西湖风景名胜区", "city": "杭州", "address": "龙井路1号"},
                topics="香格臻品・踏雪鸡火塘火锅店")))
        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["http://sidecar/v1/operations/prepare",
                                "http://backend/publish-dispatch/progress",
                                "http://sidecar/v1/operations/op/commit"])
        self.assertEqual(bodies[2]["session"]["storage_state"],
                         {"cookies": [{"name": "rotated"}]})
        self.assertEqual(bodies[2]["article_title"], "标题")
        self.assertEqual(bodies[2]["article"]["markdown"], "![图](https://x/a.png)")
        self.assertEqual(bodies[0]["article"]["location"], {
            "name": "西湖风景名胜区", "city": "杭州", "address": "龙井路1号",
        })
        self.assertEqual(bodies[0]["article"]["topics"], "香格臻品・踏雪鸡火塘火锅店")
        self.assertEqual(bodies[1]["draft_url"],
                         "https://zhuanlan.zhihu.com/p/123/edit")
        self.assertEqual(result["draft_url"],
                         "https://zhuanlan.zhihu.com/p/123/edit")
        self.assertEqual(result["draft_id"], "123")

    def test_publish_with_core_forwards_official_api_session(self):
        bodies = []

        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                bodies.append(kwargs.get("json"))
                if url.endswith("/prepare"):
                    return Response({"ok": True, "operation_id": "op", "draft_id": "123"})
                if url.endswith("/progress"):
                    return Response({"ok": True})
                return Response({"ok": True, "published_url": "https://mp.weixin.qq.com/s/123",
                                 "url_source": "official_api", "draft_id": "123"})

        official_api = {"appid": "wx123", "appsecret": "secret"}
        env = {"WECHATSYNC_PUBLISHER_URL": "http://sidecar",
               "PUBLISH_DISPATCH_URL": "http://backend", "WXGZH_ALLOW_REAL_PUBLISH": "1"}
        with patch.object(core_publisher.httpx, "AsyncClient", Client), patch.dict(os.environ, env):
            result = asyncio.run(core_publisher.publish_with_core(request(
                platform="wxgzh", account_id="core.wxgzh.appid:abc",
                storage_state={"cookies": [], "origins": []},
                official_api=official_api,
            )))
        self.assertTrue(result["ok"])
        self.assertEqual(bodies[0]["session"]["official_api"], official_api)
        self.assertEqual(bodies[2]["session"]["official_api"], official_api)

    def test_relative_image_urls_use_geo_domain_before_sidecar(self):
        bodies = []

        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                bodies.append(kwargs.get("json"))
                return Response({"ok": True, "operation_id": "op", "draft_id": "123"})

        image = "/api/ai-telemetry/public/topic-media/1/a.jpg"
        env = {"WECHATSYNC_PUBLISHER_URL": "http://sidecar",
               "PUBLISH_DISPATCH_URL": "http://backend/api"}
        with patch.object(core_publisher.httpx, "AsyncClient", Client), patch.dict(os.environ, env):
            result = asyncio.run(core_publisher.publish_with_core(request(
                dry_run=True, body=f'<p><img src="{image}"></p>', body_format="html",
                source_markdown=f"![图]({image})", images=[image])))

        absolute = f"https://<asset-base-url>{image}"
        self.assertTrue(result["ok"])
        self.assertEqual(bodies[0]["article"]["images"], [absolute])
        self.assertIn(absolute, bodies[0]["article"]["markdown"])
        self.assertIn(absolute, bodies[0]["article"]["html"])

    def test_publish_endpoint_allows_wxgzh_official_api_without_cookies(self):
        async def fake_publish_with_core(req):
            return {"ok": True, "published_url": "https://mp.weixin.qq.com/s/1"}

        with patch.object(publish_api, "get_publisher", return_value=ZhihuPublisher()), \
                patch.object(publish_api, "publish_with_core", fake_publish_with_core), \
                patch.dict(os.environ, {"PUBLISH_SERVICE_TOKEN": ""}, clear=False):
            result = asyncio.run(publish_api.publish(request(
                platform="wxgzh", storage_state={"cookies": [], "origins": []},
                official_api={"appid": "wx123", "appsecret": "secret"},
            )))
        self.assertTrue(result["ok"])

    def test_progress_failure_prevents_commit(self):
        calls = []

        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                calls.append(url)
                if url.endswith("/prepare"):
                    return Response({"ok": True, "operation_id": "op", "draft_id": "123"})
                return Response({}, 409)

        env = {"WECHATSYNC_PUBLISHER_URL": "http://sidecar",
               "PUBLISH_DISPATCH_URL": "http://backend"}
        with patch.object(core_publisher.httpx, "AsyncClient", Client), patch.dict(os.environ, env):
            result = asyncio.run(core_publisher.publish_with_core(request()))
        self.assertFalse(result["ok"])
        self.assertEqual(result["fail_type"], "crash")
        self.assertEqual(result["failure_phase"], "pre_commit")
        self.assertTrue(result["confirmed_not_published"])
        self.assertFalse(any(url.endswith("/commit") for url in calls))

    def test_prepare_failure_is_confirmed_safe_to_retry(self):
        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                return Response({"ok": False, "fail_type": "login_lost",
                                 "error": "not logged in"})

        with patch.object(core_publisher.httpx, "AsyncClient", Client), patch.dict(
                os.environ, {"WECHATSYNC_PUBLISHER_URL": "http://sidecar"}):
            result = asyncio.run(core_publisher.publish_with_core(request()))
        self.assertEqual(result["failure_phase"], "prepare")
        self.assertTrue(result["confirmed_not_published"])

    def test_commit_failure_without_no_publish_proof_becomes_unknown(self):
        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                if url.endswith("/prepare"):
                    return Response({"ok": True, "operation_id": "op", "draft_id": "123"})
                if url.endswith("/progress"):
                    return Response({"ok": True})
                return Response({"ok": False, "fail_type": "crash", "error": "ambiguous"})

        env = {"WECHATSYNC_PUBLISHER_URL": "http://sidecar",
               "PUBLISH_DISPATCH_URL": "http://backend"}
        with patch.object(core_publisher.httpx, "AsyncClient", Client), patch.dict(os.environ, env):
            result = asyncio.run(core_publisher.publish_with_core(request()))
        self.assertEqual(result["fail_type"], "submit_unknown")
        self.assertEqual(result["failure_phase"], "commit")
        self.assertFalse(result["confirmed_not_published"])

    def test_baijia_uses_its_own_real_publish_gate(self):
        bodies = []

        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                bodies.append(kwargs.get("json"))
                if url.endswith("/prepare"):
                    return Response({"ok": True, "operation_id": "op", "draft_id": "123"})
                if url.endswith("/progress"):
                    return Response({"ok": True})
                return Response({"ok": True, "draft_id": "123",
                                 "publication_state": "accepted_pending_url"})

        env = {"WECHATSYNC_PUBLISHER_URL": "http://sidecar",
               "PUBLISH_DISPATCH_URL": "http://backend", "BAIJIA_ALLOW_REAL_PUBLISH": "1"}
        with patch.object(core_publisher.httpx, "AsyncClient", Client), patch.dict(os.environ, env):
            result = asyncio.run(core_publisher.publish_with_core(request(platform="baijia")))
        self.assertTrue(result["ok"])
        self.assertTrue(bodies[-1]["existing_publish_gate"])
        self.assertEqual(bodies[-1]["article"]["html"], None)

    def test_verify_identity_uses_sidecar_auth_endpoint(self):
        calls = []
        bodies = []

        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                calls.append(url)
                bodies.append(kwargs.get("json"))
                return Response({"ok": True, "account_id": "core.csdn.username:abc",
                                 "account_handle": "CSDN号", "exit_ip": "1.2.3.4"})

        with patch.object(core_publisher.httpx, "AsyncClient", Client), patch.dict(
                os.environ, {"WECHATSYNC_PUBLISHER_URL": "http://sidecar"}):
            result = asyncio.run(core_publisher.verify_identity_with_core(
                platform="csdn", storage_state={"cookies": [{"name": "UserName"}]},
                proxy_url="http://proxy", user_agent="Chrome UA", ua_platform="Win32"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["account_handle"], "CSDN号")
        self.assertEqual(calls, ["http://sidecar/v1/auth/verify"])
        self.assertEqual(bodies[0]["plugin_type"], "csdn")
        self.assertEqual(bodies[0]["session"]["proxy_url"], "http://proxy")
        self.assertEqual(bodies[0]["session"]["user_agent"], "Chrome UA")

    def test_core_keepalive_returns_account_handle(self):
        async def fake_verify_identity_with_core(**kwargs):
            return {"ok": True, "account_id": kwargs["expected_account_id"],
                    "account_handle": "知乎号", "exit_ip": "1.2.3.4",
                    "storage_state": {"cookies": [{"name": "rotated"}]}}

        req = publish_api.KeepaliveIn(
            platform="zhihu",
            expected_account_id="api.me.url_token:abc",
            storage_state={"cookies": [{"name": "z_c0"}]},
        )
        with patch.object(publish_api, "verify_identity_with_core",
                          fake_verify_identity_with_core):
            result = asyncio.run(publish_api._keepalive_inner(ZhihuPublisher(), req))

        self.assertTrue(result["ok"])
        self.assertEqual(result["account_handle"], "知乎号")

    def test_verify_identity_forwards_user_agent_to_core(self):
        captured = {}

        async def fake_verify_identity_with_core(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "account_id": "api.me.url_token:abc",
                    "account_handle": "知乎号", "exit_ip": "1.2.3.4"}

        req = publish_api.VerifyIdentityIn(
            platform="zhihu",
            storage_state={"cookies": [{"name": "z_c0"}]},
            proxy_url="http://proxy",
            user_agent="Chrome UA",
            ua_platform="Win32",
        )
        with patch.object(publish_api, "verify_identity_with_core",
                          fake_verify_identity_with_core), patch.dict(
                os.environ, {"WORKER_UID": "publish-test-worker"}):
            result = asyncio.run(publish_api._verify_inner(ZhihuPublisher(), req))

        self.assertTrue(result["ok"])
        self.assertIsInstance(result["worker_id"], int)
        self.assertGreater(result["worker_id"], 0)
        self.assertEqual(captured["user_agent"], "Chrome UA")
        self.assertEqual(captured["ua_platform"], "Win32")
