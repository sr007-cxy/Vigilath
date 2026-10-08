#!/usr/bin/env python3
"""geo_client unit tests - pure standard library, no network, runs offline.

Covers the points that "silently hurt users if broken": credential read/write, device id not wiping the token,
no-login paths not sending Authorization, 402 not treated as an error, amount conversion, command dispatch.

    python3 tests/test_geo_client.py
"""
import base64
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "vigilath-geo" / "scripts" / "geo_client.py"


@contextlib.contextmanager
def client_in(home: str, **env):
    """Load a fresh client under the given HOME; the environment is in effect for **the whole with block**.

    ★ Must not be written as "patch at load time, restore right after loading": the client only writes
      ~/.vigilath/config at call time, so once the environment is restored it writes into the **real HOME** -
      this file got bitten by that once and created the config directory on the dev machine.
    """
    envs = {"HOME": home}
    envs.update(env)
    for k in ("VIGILATH_AGENT_TOKEN", "VIGILATH_BASE", "VIGILATH_DEVICE_ID"):
        envs.setdefault(k, "")
    with mock.patch.dict(os.environ, envs, clear=False):
        spec = importlib.util.spec_from_file_location("gc_under_test", CLIENT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


class _Resp:
    """A good-enough fake response: supports with and read()."""

    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConfigTest(unittest.TestCase):
    def test_reads_config_file(self):
        home = tempfile.mkdtemp()
        os.makedirs(f"{home}/.vigilath")
        Path(f"{home}/.vigilath/config").write_text(
            "# 注释\nVIGILATH_AGENT_TOKEN=tok_abc\nVIGILATH_BASE=https://x.test/api/agent/v1\n",
            encoding="utf-8")
        with client_in(home) as m:
            self.assertEqual(m._conf("VIGILATH_AGENT_TOKEN"), "tok_abc")
            self.assertEqual(m.BASE, "https://x.test/api/agent/v1")

    def test_site_api_derived_from_base(self):
        """No-login endpoints live under the site API root and must be derived from BASE by stripping /agent/v1."""
        with client_in(tempfile.mkdtemp(), VIGILATH_BASE="https://x.test/api/agent/v1") as m:
            self.assertEqual(m.SITE_API, "https://x.test/api")

    def test_missing_token_dies_without_leaking(self):
        with client_in(tempfile.mkdtemp()) as m:
            err = io.StringIO()
            with mock.patch("sys.stderr", err), self.assertRaises(SystemExit):
                m._headers()
            self.assertNotIn("Bearer", err.getvalue())


class DeviceIdTest(unittest.TestCase):
    def test_generated_and_persisted_600(self):
        home = tempfile.mkdtemp()
        with client_in(home) as m:
            d1 = m._device_id()
            self.assertTrue(d1.startswith("dev_"))
            self.assertEqual(d1, m._device_id(), "同一进程内必须稳定")
            self.assertEqual(oct(os.stat(f"{home}/.vigilath/config").st_mode)[-3:], "600")

    def test_does_not_clobber_existing_token(self):
        """Generating a device id rewrites the config - it must never wipe existing credentials."""
        home = tempfile.mkdtemp()
        os.makedirs(f"{home}/.vigilath")
        Path(f"{home}/.vigilath/config").write_text(
            "VIGILATH_AGENT_TOKEN=tok_keepme\nVIGILATH_BASE=https://x.test/api/agent/v1\n",
            encoding="utf-8")
        with client_in(home) as m:
            m._device_id()
        body = Path(f"{home}/.vigilath/config").read_text(encoding="utf-8")
        self.assertIn("VIGILATH_AGENT_TOKEN=tok_keepme", body)
        self.assertIn("VIGILATH_DEVICE_ID=dev_", body)

    def test_survives_unwritable_home(self):
        """Being unable to write the config must not crash - the server still has the egress IP gate."""
        with client_in("/proc/nonexistent-readonly") as m:
            self.assertTrue(m._device_id().startswith("dev_"))


class AnonymousPathTest(unittest.TestCase):
    """No-login commands must never send Authorization - with it they require authorization and no-login is broken."""

    def _seen(self, m, call):
        seen = {}

        def fake_request(req, timeout=30):
            seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
            seen["url"] = req.full_url
            return _Resp({"url": "u", "score": 1, "grade": "A", "checks": [],
                          "summary": {}, "industries": []})

        with mock.patch.object(m, "_request", fake_request):
            call()
        return seen

    def test_check_has_no_auth_header(self):
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            seen = self._seen(m, lambda: m.check("https://example.com", include_seo=False))
            self.assertNotIn("authorization", seen["headers"])
            self.assertIn("/check/anonymous", seen["url"])

    def test_industry_has_no_auth_header(self):
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            seen = self._seen(m, lambda: m.industry())
            self.assertNotIn("authorization", seen["headers"])


def _patch_urlopen(m, **kw):
    """Put the mock at the **real network egress** so `_request` itself is under test.

    These cases used to be `mock.patch.object(m, "_request", ...)` - replacing `_request`
    entirely with a fake, so all that was tested was the caller's `except HTTPError: if e.code == 402`,
    and that logic itself was correct; what was wrong is that it **could never be reached**: the real `_request`
    calls `_die()` -> `sys.exit` directly inside `except HTTPError`. The test hit the inside of the mock
    boundary while the real break was outside, so CI stayed green while the top-up flow was dead.
    """
    return mock.patch.object(m.urllib.request, "urlopen", **kw)


def _http_error(code, payload: dict):
    return urllib.error.HTTPError(
        "u", code, "err", {}, io.BytesIO(json.dumps(payload).encode()))


class PaymentTest(unittest.TestCase):
    def test_402_is_not_an_error(self):
        """402 is a normal step in the top-up flow (carrying the payment requirement) and must not be raised as an exception."""
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            err = _http_error(402, {"x402Version": 1, "accepts": [{"payTo": "0xabc"}]})
            with _patch_urlopen(m, side_effect=err):
                out = m.topup("100")
            self.assertEqual(out["accepts"][0]["payTo"], "0xabc",
                             "402 的响应体必须完整送到调用方 —— 少了它就没有收款信息")
            self.assertIn("_hint", out, "要告诉 agent 这不是失败、下一步做什么")

    def test_402_body_survives_passthrough(self):
        """Guards the consumption order of e.read().

        `_explain_http` calls e.read() on its first line, and an HTTPError body is a one-shot stream.
        If anyone moves the passthrough check after `_explain_http`, this test goes red while all others stay green.
        """
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            err = _http_error(402, {"accepts": [{"payTo": "0xdead", "maxAmountRequired": "7"}]})
            with _patch_urlopen(m, side_effect=err):
                out = m.topup("100")
            self.assertEqual(out["accepts"][0]["maxAmountRequired"], "7")

    def test_402_without_passthrough_still_dies(self):
        """passthrough is an explicit allowlist, not a global pass - read endpoints hitting 402 should still produce a human message."""
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            with _patch_urlopen(m, side_effect=_http_error(402, {"detail": "no"})):
                with self.assertRaises(SystemExit):
                    m.get("wallet")

    def test_500_dies_with_human_message(self):
        """The passthrough change must not lose the normal error path."""
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            with _patch_urlopen(m, side_effect=_http_error(500, {})):
                with self.assertRaises(SystemExit):
                    m.get("wallet")


    def test_tx_hash_goes_in_x_payment_header(self):
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            seen = {}

            def fake_urlopen(req, timeout=30):
                seen.update({k.lower(): v for k, v in req.headers.items()})
                seen["body"] = req.data
                return _Resp({"status": "ok"})

            with _patch_urlopen(m, side_effect=fake_urlopen):
                m.topup("72", tx_hash="0xdeadbeef")
            self.assertEqual(json.loads(base64.b64decode(seen["x-payment"]))["tx_hash"], "0xdeadbeef")
            self.assertEqual(json.loads(seen["body"])["amount_cents"], 7200, "元 → 分")

    def test_amount_rejects_non_numeric(self):
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                m.topup("一百块")


class ErrorMessageTest(unittest.TestCase):
    def test_backend_message_wins_over_generic_hint(self):
        """The backend's message is always more accurate than the client fallback - the anonymous quota one distinguishes device exhausted vs egress exhausted."""
        with client_in(tempfile.mkdtemp()) as m:
            err = _http_error(429, {"error": {"code": 429,
                                              "message": "这台设备的检测次数用完了,30 天后重置。"}})
            with _patch_urlopen(m, side_effect=err):
                with self.assertRaises(SystemExit):
                    m.check("https://example.com", include_seo=False)

    def test_401_tells_user_to_self_serve_login(self):
        """401 must no longer say "contact Vigilath to reissue" - device code authorization exists precisely to eliminate that step."""
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            msg = m._explain_http(_http_error(401, {}), body="")
            self.assertIn("login", msg)
            self.assertNotIn("联系", msg)


class CheckShapeTest(unittest.TestCase):
    def test_half_aggregates_by_category(self):
        with client_in(tempfile.mkdtemp()) as m:
            raw = {"url": "u", "score": 28, "grade": "F", "summary": {},
                   "checks": [{"category": "HTTPS", "status": "PASS", "message": "ok"},
                              {"category": "Meta Tags", "status": "FAIL", "message": "缺 description"},
                              {"category": "Meta Tags", "status": "WARN", "message": "标题过长"}],
                   "locked_categories": ["llms.txt", "Schema"]}
            with mock.patch.object(m, "_request", lambda req, timeout=30: _Resp(raw)):
                out = m.check("https://example.com", include_seo=False)
            geo = out["geo"]
            self.assertEqual(geo["score"], 28)
            self.assertEqual(geo["visible_categories"]["Meta Tags"]["fail"], 1)
            self.assertEqual(geo["visible_categories"]["Meta Tags"]["warn"], 1)
            self.assertEqual(geo["locked_count"], 2, "锁住的类目要报数量,让 agent 知道没看全")
            self.assertIn("note", out)


class UrlNormalizeTest(unittest.TestCase):
    """URLs extracted by the host often come wrapped and often lack a scheme - they must be cleaned before being accepted.

    The first item of the 2026-08-30 product feedback was "the skill fetched the wrong URL".
    This guards two things: strip what should be stripped, and **change nothing that should not be changed** (adding www., switching to .com
    and similar "helpful" rewrites are exactly where "audited a different site" came from).
    """

    def test_adds_scheme(self):
        with client_in(tempfile.mkdtemp()) as m:
            self.assertEqual(m._normalize_url("example.com"), "https://example.com")

    def test_strips_wrapping_and_punctuation(self):
        with client_in(tempfile.mkdtemp()) as m:
            for raw in ('  "https://example.com"  ', "「https://example.com」",
                        "`https://example.com`", "https://example.com。",
                        "[官网](https://example.com)"):
                with self.subTest(raw=raw):
                    self.assertEqual(m._normalize_url(raw), "https://example.com")

    def test_keeps_host_and_path_untouched(self):
        with client_in(tempfile.mkdtemp()) as m:
            for raw in ("http://a.example.cn/x?y=1", "https://例子.测试/路径"):
                with self.subTest(raw=raw):
                    self.assertEqual(m._normalize_url(raw), raw)

    def test_rejects_non_http(self):
        with client_in(tempfile.mkdtemp()) as m:
            for raw in ("ftp://example.com", "javascript:alert(1)", "   "):
                with self.subTest(raw=raw):
                    with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                        m._normalize_url(raw)

    def test_check_echoes_what_it_actually_tested(self):
        """The response must include the address actually requested, so the agent can let the user verify it."""
        with client_in(tempfile.mkdtemp()) as m:
            raw = {"url": "", "score": 1, "grade": "A", "checks": [], "summary": {}}
            with mock.patch.object(m, "_request", lambda req, timeout=30: _Resp(raw)):
                out = m.check(" example.com ", include_seo=False)
            self.assertEqual(out["url_checked"], "https://example.com")


class MentionQuotaTest(unittest.TestCase):
    def test_no_quota_does_not_fire_the_check(self):
        """When the quota is used up, **do not start** - the server deducts at start time, and the user waits a round for nothing."""
        with client_in(tempfile.mkdtemp()) as m:
            calls = []

            def fake_request(req, timeout=30, passthrough=()):
                calls.append(req.full_url)
                return _Resp({"allowed": False, "unlimited": False,
                              "device_used": 2, "device_limit": 2,
                              "ip_used": 3, "ip_limit": 10, "window_days": 30})

            with mock.patch.object(m, "_request", fake_request):
                out = m.mention("某品牌", ["商用清洁机器人"])
            self.assertEqual(out["status"], "no_quota")
            self.assertEqual(len(calls), 1, "查完额度就该停,不该再打发起接口")
            self.assertIn("mention-quota", calls[0])


class IndustryNotInLibraryTest(unittest.TestCase):
    def test_404_returns_available_list_not_another_industry(self):
        """When the industry has no dataset, the list must be returned - without it the agent fills in with other data."""
        with client_in(tempfile.mkdtemp()) as m:
            err = _http_error(404, {"detail": {
                "code": "INDUSTRY_NOT_IN_LIBRARY",
                "message": "「工业清洁机器人」这个行业还没有建库。",
                "available": ["商用清洁机器人"], "sample_note": "逐个建库"}})
            with _patch_urlopen(m, side_effect=err):
                out = m.industry("工业清洁机器人")
            self.assertIs(out["已建库"], False)
            self.assertEqual(out["已建库的行业"], ["商用清洁机器人"])
            self.assertIn("绝不", out["note"])


class ReadabilityTest(unittest.TestCase):
    def test_check_surfaces_top_issues_not_just_category_names(self):
        """What gets read out by default is "which items fail and what the impact is", not 25 category names."""
        with client_in(tempfile.mkdtemp()) as m:
            raw = {"url": "u", "score": 40, "grade": "D", "summary": {},
                   "checks": [{"category": "Meta Tags", "status": "WARN", "message": "标题过长"},
                              {"category": "robots.txt", "status": "FAIL", "message": "挡住了 AI 爬虫"},
                              {"category": "HTTPS", "status": "PASS", "message": "已启用"}],
                   "locked_categories": []}
            with mock.patch.object(m, "_request", lambda req, timeout=30: _Resp(raw)):
                out = m.check("https://example.com", include_seo=False)
            top = out["geo"]["top_issues"]
            self.assertEqual(out["geo"]["issue_count"], 2, "PASS 不算问题")
            self.assertEqual(top[0]["严重度"], "不通过", "FAIL 必须排在 WARN 前面")
            self.assertEqual(top[0]["问题"], "挡住了 AI 爬虫")


class CliTest(unittest.TestCase):
    def test_removed_wechat_flag_explains_itself(self):
        """The endpoint `--wechat` called was never implemented. Removing it is fine, but say where to top up instead of just "unknown argument"."""
        with client_in(tempfile.mkdtemp(), VIGILATH_AGENT_TOKEN="tok_x") as m:
            err = io.StringIO()
            with mock.patch("sys.stderr", err), self.assertRaises(SystemExit):
                m.main(["geo_client.py", "topup", "100", "--wechat"])
            self.assertIn("网页", err.getvalue())

    def test_unknown_command_exits(self):
        with client_in(tempfile.mkdtemp()) as m:
            with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                m.main(["geo_client.py", "nosuchcmd"])

    def test_check_requires_url(self):
        with client_in(tempfile.mkdtemp()) as m:
            with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                m.main(["geo_client.py", "check"])

    def test_usage_lists_免登录_first(self):
        """No-login commands come first in the usage hint - what the agent sees first decides what it tries first."""
        with client_in(tempfile.mkdtemp()) as m:
            err = io.StringIO()
            with mock.patch("sys.stderr", err), self.assertRaises(SystemExit):
                m.main(["geo_client.py"])
            usage = err.getvalue()
            self.assertLess(usage.index("check"), usage.index("chat"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
