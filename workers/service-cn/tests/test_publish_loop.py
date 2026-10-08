from __future__ import annotations

import asyncio
import os
from unittest import TestCase
from unittest.mock import patch

from app import publish_loop
from app.publish_loop import _claim, _claim_verifications, _handle_verification


class _Response:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {"tasks": []}


class _Client:
    def __init__(self): self.body = None
    async def post(self, _url, **kwargs):
        self.body = kwargs["json"]
        return _Response()


class PublishLoopClaimTests(TestCase):
    # Rule since 2c7dcae2 (08-25): without WECHATSYNC_PUBLISHER_URL configured nothing is claimed at all; PUBLISH_CORE_ACCOUNT_IDS
    # is a narrowing filter for all platforms (empty = all accounts), and PUBLISH_CORE_ACCOUNT_IDS_JSON overrides it per platform.
    _SIDECAR = {"WECHATSYNC_PUBLISHER_URL": "http://sidecar"}

    def test_nothing_is_claimed_without_sidecar_url(self):
        client = _Client()
        env = {"WECHATSYNC_PUBLISHER_URL": "",
               "PUBLISH_CORE_PLATFORMS": "zhihu,baijia",
               "PUBLISH_CORE_ACCOUNT_IDS": "7,3",
               "PUBLISH_CORE_ACCOUNT_IDS_JSON": ""}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(_claim(client, "http://backend", {}, 9, 1))
        self.assertEqual(client.body["platforms"], [])
        self.assertEqual(client.body["account_ids_by_platform"], {})

    def test_legacy_account_ids_narrow_every_platform(self):
        client = _Client()
        env = {**self._SIDECAR,
               "PUBLISH_CORE_PLATFORMS": "zhihu,baijia",
               "PUBLISH_CORE_ACCOUNT_IDS": "7,3",
               "PUBLISH_CORE_ACCOUNT_IDS_JSON": ""}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(_claim(client, "http://backend", {}, 9, 1))
        self.assertEqual(client.body["platforms"], ["baijia", "zhihu"])
        self.assertEqual(client.body["account_ids_by_platform"],
                         {"baijia": [3, 7], "zhihu": [3, 7]})

    def test_empty_account_ids_means_all_accounts(self):
        client = _Client()
        env = {**self._SIDECAR,
               "PUBLISH_CORE_PLATFORMS": "zhihu,baijia",
               "PUBLISH_CORE_ACCOUNT_IDS": "",
               "PUBLISH_CORE_ACCOUNT_IDS_JSON": ""}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(_claim(client, "http://backend", {}, 9, 1))
        self.assertEqual(client.body["platforms"], ["baijia", "zhihu"])
        self.assertEqual(client.body["account_ids_by_platform"], {})

    def test_core_claim_uses_per_platform_json_allowlist(self):
        client = _Client()
        env = {**self._SIDECAR,
               "PUBLISH_CORE_PLATFORMS": "zhihu,baijia,toutiao,csdn",
               "PUBLISH_CORE_ACCOUNT_IDS": "",
               "PUBLISH_CORE_ACCOUNT_IDS_JSON": '{"toutiao":[11,12],"csdn":["*"]}'}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(_claim(client, "http://backend", {}, 9, 1))
        self.assertEqual(client.body["platforms"], ["baijia", "csdn", "toutiao", "zhihu"])
        self.assertEqual(client.body["account_ids_by_platform"], {"toutiao": [11, 12]})

    def test_ctrip_verification_claim_and_report(self):
        class Client(_Client):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def post(self, url, **kwargs):
                self.calls.append((url, kwargs["json"]))
                return _Response()

        client = Client()
        jobs = asyncio.run(_claim_verifications(client, "http://backend", {}, 9, 2))
        self.assertEqual(jobs, [])
        self.assertEqual(client.calls[0][1], {"worker_id": 9, "max_tasks": 2})

        task = {
            "task_id": 4, "account_db_id": 7, "claim_token": "x" * 24,
            "platform": "ctrip", "external_post_id": "456", "account_id": "uid-1",
            "storage_state": {"cookies": []}, "proxy_url": "http://proxy",
        }

        async def fake_verify(**kwargs):
            self.assertEqual(kwargs["external_post_id"], "456")
            return {"ok": True, "status": "reviewing", "raw_status": 1,
                    "observed_account_id": "uid-1", "exit_ip": "1.2.3.4",
                    "storage_state": {"cookies": [{"name": "cticket"}]}}

        with patch.object(publish_loop, "verify_article_with_core", fake_verify):
            asyncio.run(_handle_verification(client, "http://backend", {}, task, 9))
        url, body = client.calls[-1]
        self.assertTrue(url.endswith("/publish-dispatch/verification/report"))
        self.assertEqual(body["status"], "reviewing")
        self.assertEqual(body["observed_account_id"], "uid-1")
        self.assertEqual(body["storage_state"]["cookies"][0]["name"], "cticket")
