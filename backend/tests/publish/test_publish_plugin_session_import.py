from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from geo.api import publish_accounts as api
from geo.models.publish_accounts import CHANNEL_SERVER_POOL, EGRESS_HOST, EGRESS_PROXY


class _Db:
    def __init__(self, row=None):
        self.row = row
        self.committed = False
        self.refreshed = False

    def get(self, _model, _ident):
        return self.row

    def commit(self):
        self.committed = True

    def refresh(self, _row):
        self.refreshed = True


def _payload(**overrides):
    data = {
        "platform": "zhihu",
        "storage_state": {"cookies": [{"name": "z_c0", "value": "ticket"}]},
        "credential_owner": "ops",
    }
    data.update(overrides)
    return api.PublishPluginSessionImportIn(**data)


def _patch_common(existing=None, platform="zhihu"):
    return [
        patch.object(api, "_browser_publish_capabilities",
                     return_value={"platforms": {platform}}),
        patch.object(api, "_resolve_login_target",
                     return_value=(platform, existing, None, None, "ops",
                                   EGRESS_HOST)),
        patch.object(api, "platform_row",
                     return_value=SimpleNamespace(
                         enabled=True,
                         channel=CHANNEL_SERVER_POOL,
                         identity_cookies_json=None,
                     )),
    ]


class PublishPluginSessionImportTests(unittest.TestCase):
    def test_new_login_defaults_to_host_egress(self):
        db = _Db()
        with patch.object(api, "platform_row", return_value=SimpleNamespace(
                enabled=True, channel=CHANNEL_SERVER_POOL)), \
                patch.object(api, "_auto_select_proxy") as select:
            result = api._resolve_login_target(
                api.PublishWebLoginIn(platform="zhihu", credential_owner="ops"),
                db, {"zhihu"}, "测试新增",
            )

        self.assertIsNone(result[2])
        self.assertIsNone(result[3])
        self.assertEqual(result[5], EGRESS_HOST)
        select.assert_not_called()

    def test_new_login_can_explicitly_use_host_egress(self):
        db = _Db()
        with patch.object(api, "platform_row", return_value=SimpleNamespace(
                enabled=True, channel=CHANNEL_SERVER_POOL)), \
                patch.object(api, "_auto_select_proxy") as select:
            result = api._resolve_login_target(
                api.PublishWebLoginIn(platform="zhihu", credential_owner="ops",
                                      egress=EGRESS_HOST),
                db, {"zhihu"}, "测试新增",
            )

        self.assertIsNone(result[2])
        self.assertIsNone(result[3])
        self.assertEqual(result[5], EGRESS_HOST)
        select.assert_not_called()

    def test_startup_setting_forces_plugin_import_to_host_egress(self):
        captured = {}

        def fake_resolve(target, *_args):
            captured["target"] = target
            return "zhihu", None, None, None, "ops", EGRESS_HOST

        with patch.dict(api.os.environ,
                        {"PUBLISH_PLUGIN_IMPORT_HOST_EGRESS": "1"}), \
                patch.object(api, "_browser_publish_capabilities",
                             return_value={"platforms": {"zhihu"}}), \
                patch.object(api, "_resolve_login_target", side_effect=fake_resolve), \
                patch.object(api, "platform_row", return_value=SimpleNamespace(
                    enabled=True, channel=CHANNEL_SERVER_POOL,
                    identity_cookies_json=None)):
            with self.assertRaises(HTTPException):
                api.import_plugin_session(
                    _payload(storage_state={"cookies": []},
                             egress=EGRESS_PROXY, auto_proxy=True),
                    db=_Db(), current_user=object())

        self.assertEqual(captured["target"].egress, EGRESS_HOST)
        self.assertIsNone(captured["target"].proxy_id)
        self.assertFalse(captured["target"].auto_proxy)

    def test_empty_cookies_are_rejected_before_identity_probe(self):
        db = _Db()
        payload = _payload(storage_state={"cookies": []})
        patches = _patch_common()
        with patches[0], patches[1], patches[2], \
                patch.object(api, "_call_verify_identity") as verify, \
                patch.object(api, "_register_account") as register:
            with self.assertRaises(HTTPException) as ctx:
                api.import_plugin_session(payload, db=db, current_user=object())

        self.assertEqual(ctx.exception.status_code, 422)
        verify.assert_not_called()
        register.assert_not_called()

    def test_non_array_cookies_are_rejected_before_identity_probe(self):
        db = _Db()
        payload = _payload(storage_state={"cookies": "z_c0=ticket"})
        patches = _patch_common()
        with patches[0], patches[1], patches[2], \
                patch.object(api, "_call_verify_identity") as verify, \
                patch.object(api, "_register_account") as register:
            with self.assertRaises(HTTPException) as ctx:
                api.import_plugin_session(payload, db=db, current_user=object())

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("storage_state.cookies 不是数组", str(ctx.exception.detail))
        verify.assert_not_called()
        register.assert_not_called()

    def test_identity_failure_does_not_register(self):
        db = _Db()
        patches = _patch_common()
        with patches[0], patches[1], patches[2], \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "_call_verify_identity",
                             return_value={"ok": False, "fail_type": "login_lost",
                                           "error": "empty"}), \
                patch.object(api, "_register_account") as register:
            with self.assertRaises(HTTPException) as ctx:
                api.import_plugin_session(_payload(), db=db, current_user=object())

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("login_lost", str(ctx.exception.detail))
        self.assertIn("sidecar 读不到平台登录身份", str(ctx.exception.detail))
        register.assert_not_called()

    def test_existing_account_identity_mismatch_returns_conflict(self):
        existing = SimpleNamespace(account_id="api.me.url_token:old",
                                   account_handle="old", daily_cap=None,
                                   owner_worker_id=None)
        db = _Db()
        patches = _patch_common(existing)
        with patches[0], patches[1], patches[2], \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "_call_verify_identity",
                             return_value={"ok": True,
                                           "account_id": "api.me.url_token:new"}), \
                patch.object(api, "_register_account") as register:
            with self.assertRaises(HTTPException) as ctx:
                api.import_plugin_session(
                    _payload(account_db_id=9),
                    db=db,
                    current_user=object(),
                )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("当前浏览器登录的账号与要覆盖的原账号不一致", str(ctx.exception.detail))
        self.assertIn("当前读到=api.me.url_token:new", str(ctx.exception.detail))
        register.assert_not_called()

    def test_success_registers_plugin_cookie_with_preverified_probe(self):
        row = SimpleNamespace(id=7, auth_type=None, user_agent=None,
                              ua_platform=None)
        db = _Db(row)
        probe = {"ok": True, "account_id": "api.me.url_token:abc",
                 "account_handle": "zhihu user", "exit_ip": "127.0.0.1",
                 "worker_id": 24680}
        captured = {}

        def fake_register(register_payload, db, current_user,
                          preverified_probe=None):
            captured["payload"] = register_payload
            captured["probe"] = preverified_probe
            row.auth_type = register_payload.auth_type
            return {"renewed": False, "account": {"id": row.id}}

        patches = _patch_common()
        with patches[0], patches[1], patches[2], \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "_call_verify_identity", return_value=probe) as verify, \
                patch.object(api, "_register_account", side_effect=fake_register), \
                patch.object(api, "account_response",
                             side_effect=lambda row, **_kwargs: {
                                 "id": row.id,
                                 "auth_type": row.auth_type,
                                 "user_agent": row.user_agent,
                                 "ua_platform": row.ua_platform,
                             }):
            result = api.import_plugin_session(
                _payload(user_agent="UA", ua_platform="Win32"),
                db=db,
                current_user=object(),
            )

        self.assertFalse(result["renewed"])
        self.assertTrue(result["identity_resolved"])
        self.assertEqual(result["account"]["auth_type"], "plugin_cookie")
        self.assertEqual(result["account"]["user_agent"], "UA")
        self.assertEqual(result["account"]["ua_platform"], "Win32")
        self.assertEqual(captured["payload"].egress, EGRESS_HOST)
        self.assertEqual(captured["payload"].auth_type, "plugin_cookie")
        self.assertEqual(captured["payload"].owner_worker_id, 24680)
        self.assertIs(captured["probe"], probe)
        verify.assert_called_once_with(
            "zhihu",
            {"cookies": [{"name": "z_c0", "value": "ticket"}]},
            None,
            user_agent="UA",
            ua_platform="Win32",
            expected_account_id=None,
        )
        self.assertTrue(db.committed)
        self.assertTrue(db.refreshed)

    def test_new_account_requires_identity_probe_worker_id(self):
        patches = _patch_common()
        with patches[0], patches[1], patches[2], \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "_call_verify_identity", return_value={
                    "ok": True,
                    "account_id": "api.me.url_token:abc",
                }), \
                patch.object(api, "_register_account") as register:
            with self.assertRaises(HTTPException) as ctx:
                api.import_plugin_session(
                    _payload(), db=_Db(), current_user=object())

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("WORKER_UID", str(ctx.exception.detail))
        register.assert_not_called()

    def test_readding_retired_account_rebinds_to_identity_probe_worker(self):
        row = SimpleNamespace(
            id=7,
            platform="zhihu",
            account_id="api.me.url_token:abc",
            channel=CHANNEL_SERVER_POOL,
            status="retired",
            owner_worker_id=13579,
        )

        class Query:
            def filter(self, *_args):
                return self

            def one_or_none(self):
                return row

        class Db:
            def query(self, *_args):
                return Query()

            def commit(self):
                pass

            def refresh(self, _row):
                pass

        payload = api.PublishAccountRegisterIn(
            platform="zhihu",
            channel=CHANNEL_SERVER_POOL,
            storage_state={"cookies": [{"name": "z_c0", "value": "ticket"}]},
            credential_owner="ops",
            owner_worker_id=24680,
            egress=EGRESS_HOST,
        )
        with patch.object(api, "platform_row", return_value=SimpleNamespace(
                enabled=True, channel=CHANNEL_SERVER_POOL,
                identity_cookies_json=None)), \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "encrypt_required", return_value="enc"), \
                patch.object(api, "account_response", return_value={"id": row.id}):
            result = api._register_account(
                payload,
                db=Db(),
                current_user=object(),
                preverified_probe={
                    "ok": True,
                    "account_id": row.account_id,
                    "account_handle": "zhihu user",
                    "exit_ip": "127.0.0.1",
                    "worker_id": 24680,
                },
            )

        self.assertTrue(result["renewed"])
        self.assertEqual(row.status, "active")
        self.assertEqual(row.owner_worker_id, 24680)

    def test_account_key_is_not_used_as_account_handle(self):
        row = SimpleNamespace(id=11, auth_type=None, user_agent=None,
                              ua_platform=None, account_handle=None)
        db = _Db(row)
        captured = {}

        def fake_register(register_payload, db, current_user,
                          preverified_probe=None):
            captured["payload"] = register_payload
            row.auth_type = register_payload.auth_type
            return {"renewed": False, "account": {"id": row.id}}

        patches = _patch_common()
        with patches[0], patches[1], patches[2], \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "_call_verify_identity",
                             return_value={"ok": True,
                                           "account_id": "api.me.url_token:abc",
                                           "account_handle": None,
                                           "worker_id": 24680}), \
                patch.object(api, "_register_account", side_effect=fake_register), \
                patch.object(api, "account_response",
                             side_effect=lambda row, **_kwargs: {"id": row.id}):
            api.import_plugin_session(
                _payload(account_key="zhihu:slot-1"),
                db=db,
                current_user=object(),
            )

        self.assertIsNone(captured["payload"].account_handle)
        self.assertIsNone(row.account_handle)

    def test_session_account_handle_is_forwarded_as_nickname(self):
        row = SimpleNamespace(id=12, auth_type=None, user_agent=None,
                              ua_platform=None, account_handle=None)
        db = _Db(row)
        captured = {}

        def fake_register(register_payload, db, current_user,
                          preverified_probe=None):
            captured["payload"] = register_payload
            row.auth_type = register_payload.auth_type
            row.account_handle = register_payload.account_handle
            return {"renewed": False, "account": {"id": row.id}}

        patches = _patch_common()
        with patches[0], patches[1], patches[2], \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "_call_verify_identity",
                             return_value={"ok": True,
                                           "account_id": "api.me.url_token:abc",
                                           "account_handle": None,
                                           "worker_id": 24680}), \
                patch.object(api, "_register_account", side_effect=fake_register), \
                patch.object(api, "account_response",
                             side_effect=lambda row, **_kwargs: {"id": row.id}):
            api.import_plugin_session(
                _payload(account_handle="知乎号", account_key="zhihu:slot-1"),
                db=db,
                current_user=object(),
            )

        self.assertEqual(captured["payload"].account_handle, "知乎号")
        self.assertEqual(row.account_handle, "知乎号")

    def test_wxgzh_official_api_import_stores_credentials_and_name(self):
        row = SimpleNamespace(id=21, credentials_enc=None, auth_type=None,
                              account_handle=None, last_fail_reason="old",
                              daily_cap=None, owner_worker_id=None)
        db = _Db(row)
        captured = {}
        proxy = SimpleNamespace(id=5, proxy_url="http://proxy")

        def fake_register(register_payload, db, current_user,
                          preverified_probe=None):
            captured["payload"] = register_payload
            captured["probe"] = preverified_probe
            row.auth_type = register_payload.auth_type
            row.account_handle = register_payload.account_handle
            return {"renewed": False, "account": {"id": row.id}}

        with patch.object(api, "_browser_publish_capabilities",
                          return_value={"platforms": {"wxgzh"}}), \
                patch.object(api, "_resolve_login_target",
                             return_value=("wxgzh", None, 5, proxy, "ops",
                                           EGRESS_PROXY)), \
                patch.object(api, "_wxgzh_official_api_profile",
                             return_value={"account_handle": "公众号名称"}), \
                patch.object(api, "encrypt_required", return_value="enc-json"), \
                patch.object(api, "_register_account", side_effect=fake_register), \
                patch.object(api, "account_response",
                             side_effect=lambda row, **_kwargs: {
                                 "id": row.id,
                                 "auth_type": row.auth_type,
                                 "account_handle": row.account_handle,
                             }):
            result = api.import_wxgzh_official_api(
                api.PublishWxgzhOfficialApiImportIn(
                    appid=" wx123 ", appsecret=" secret ",
                    credential_owner="ops", daily_cap=9, proxy_id=5,
                ),
                db=db,
                current_user=object(),
            )

        self.assertEqual(result["account"]["account_handle"], "公众号名称")
        self.assertEqual(row.credentials_enc, "enc-json")
        self.assertEqual(row.auth_type, "official_api")
        self.assertEqual(captured["payload"].storage_state, {"cookies": [], "origins": []})
        self.assertEqual(captured["payload"].auth_type, "official_api")
        self.assertEqual(captured["payload"].proxy_id, 5)
        self.assertEqual(captured["payload"].daily_cap, 9)
        self.assertEqual(captured["probe"]["account_id"],
                         api._account_hash("core.wxgzh.appid", "wx123"))
        self.assertTrue(db.committed)

    def test_wxgzh_official_api_import_failure_does_not_register(self):
        db = _Db()
        with patch.object(api, "_browser_publish_capabilities",
                          return_value={"platforms": {"wxgzh"}}), \
                patch.object(api, "_resolve_login_target",
                             return_value=("wxgzh", None, 5,
                                           SimpleNamespace(proxy_url="http://proxy"),
                                           "ops", EGRESS_PROXY)), \
                patch.object(api, "_wxgzh_official_api_profile",
                             side_effect=HTTPException(422, "bad appsecret")), \
                patch.object(api, "_register_account") as register:
            with self.assertRaises(HTTPException) as ctx:
                api.import_wxgzh_official_api(
                    api.PublishWxgzhOfficialApiImportIn(
                        appid="wx123", appsecret="bad",
                        credential_owner="ops", proxy_id=5,
                    ),
                    db=db,
                    current_user=object(),
                )

        self.assertEqual(ctx.exception.status_code, 422)
        register.assert_not_called()

    def test_wxgzh_official_api_host_egress(self):
        row = SimpleNamespace(id=22, credentials_enc=None, auth_type=None,
                              account_handle=None, last_fail_reason=None,
                              daily_cap=None, owner_worker_id=None)
        db = _Db(row)
        captured = {}

        def fake_resolve(target, db, supported_platforms, mode_label):
            captured["target"] = target
            return "wxgzh", None, None, None, "ops", EGRESS_HOST

        def fake_register(register_payload, db, current_user,
                          preverified_probe=None):
            captured["payload"] = register_payload
            row.auth_type = register_payload.auth_type
            row.account_handle = register_payload.account_handle
            return {"renewed": False, "account": {"id": row.id}}

        with patch.object(api, "_browser_publish_capabilities",
                             return_value={"platforms": {"wxgzh"}}), \
                patch.object(api, "_resolve_login_target", side_effect=fake_resolve), \
                patch.object(api, "_wxgzh_official_api_profile",
                             return_value={"account_handle": "本机测试号"}), \
                patch.object(api, "encrypt_required", return_value="enc-json"), \
                patch.object(api, "_register_account", side_effect=fake_register), \
                patch.object(api, "account_response",
                             side_effect=lambda row, **_kwargs: {"id": row.id}):
            api.import_wxgzh_official_api(
                api.PublishWxgzhOfficialApiImportIn(
                    appid="wx123", appsecret="secret",
                    credential_owner="ops",
                    egress=EGRESS_HOST,
                ),
                db=db,
                current_user=object(),
            )

        self.assertEqual(captured["target"].egress, EGRESS_HOST)
        self.assertIsNone(captured["target"].proxy_id)
        self.assertFalse(captured["target"].auto_proxy)
        self.assertEqual(captured["payload"].egress, EGRESS_HOST)
        self.assertIsNone(captured["payload"].proxy_id)

    def test_existing_account_key_placeholder_is_cleared(self):
        row = SimpleNamespace(id=13, auth_type=None, user_agent=None,
                              ua_platform=None, account_handle="zhihu:slot-1")
        db = _Db(row)

        def fake_register(register_payload, db, current_user,
                          preverified_probe=None):
            row.auth_type = register_payload.auth_type
            return {"renewed": False, "account": {"id": row.id}}

        patches = _patch_common()
        with patches[0], patches[1], patches[2], \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "_call_verify_identity",
                             return_value={"ok": True,
                                           "account_id": "api.me.url_token:abc",
                                           "account_handle": "",
                                           "worker_id": 24680}), \
                patch.object(api, "_register_account", side_effect=fake_register), \
                patch.object(api, "account_response",
                             side_effect=lambda row, **_kwargs: {"id": row.id}):
            api.import_plugin_session(
                _payload(account_key="zhihu:slot-1"),
                db=db,
                current_user=object(),
            )

        self.assertIsNone(row.account_handle)

    def test_baijia_success_uses_sidecar_account_id(self):
        row = SimpleNamespace(id=8, auth_type=None, user_agent=None,
                              ua_platform=None)
        db = _Db(row)
        probe = {"ok": True, "account_id": "api.appinfo.userid:61c72c4a410efdd2",
                 "account_handle": "百家号用户", "exit_ip": "127.0.0.1",
                 "worker_id": 24680}
        captured = {}

        def fake_register(register_payload, db, current_user,
                          preverified_probe=None):
            captured["payload"] = register_payload
            captured["probe"] = preverified_probe
            row.auth_type = register_payload.auth_type
            return {"renewed": False, "account": {"id": row.id}}

        patches = _patch_common(platform="baijia")
        with patches[0], patches[1], patches[2], \
                patch.object(api, "has_login_cookies", return_value=True), \
                patch.object(api, "_call_verify_identity", return_value=probe), \
                patch.object(api, "_register_account", side_effect=fake_register), \
                patch.object(api, "account_response",
                             side_effect=lambda row, **_kwargs: {
                                 "id": row.id,
                                 "auth_type": row.auth_type,
                             }):
            result = api.import_plugin_session(
                _payload(platform="baijia",
                         storage_state={"cookies": [{"name": "BDUSS",
                                                     "value": "ticket"}]}),
                db=db,
                current_user=object(),
            )

        self.assertTrue(result["identity_resolved"])
        self.assertEqual(captured["payload"].platform, "baijia")
        self.assertIs(captured["probe"], probe)

    def test_xhs_and_douyin_ticket_cookies_pass_backend_gate(self):
        for platform, cookie_name in (("xhs", "web_session"), ("douyin", "sessionid")):
            row = SimpleNamespace(id=9, auth_type=None, user_agent=None,
                                  ua_platform=None)
            db = _Db(row)
            captured = {}

            def fake_register(register_payload, db, current_user,
                              preverified_probe=None):
                captured["payload"] = register_payload
                row.auth_type = register_payload.auth_type
                return {"renewed": False, "account": {"id": row.id}}

            patches = _patch_common(platform=platform)
            with patches[0], patches[1], patches[2], \
                    patch.object(api, "_call_verify_identity",
                                 return_value={"ok": True,
                                               "account_id": f"core.{platform}.cookie.{cookie_name}:abc",
                                               "worker_id": 24680}), \
                    patch.object(api, "_register_account", side_effect=fake_register), \
                    patch.object(api, "account_response",
                                 side_effect=lambda row, **_kwargs: {"id": row.id}):
                result = api.import_plugin_session(
                    _payload(platform=platform,
                             storage_state={"cookies": [{"name": cookie_name,
                                                         "value": "ticket"}]}),
                    db=db,
                    current_user=object(),
                )

            self.assertTrue(result["identity_resolved"])
            self.assertEqual(captured["payload"].platform, platform)

    def test_wxgzh_stable_ticket_cookies_pass_backend_gate(self):
        row = SimpleNamespace(id=10, auth_type=None, user_agent=None,
                              ua_platform=None)
        db = _Db(row)
        captured = {}

        def fake_register(register_payload, db, current_user,
                          preverified_probe=None):
            captured["payload"] = register_payload
            return {"renewed": False, "account": {"id": row.id}}

        patches = _patch_common(platform="wxgzh")
        with patches[0], patches[1], patches[2], \
                patch.object(api, "_call_verify_identity",
                             return_value={"ok": True,
                                           "account_id": "core.wxgzh.user_id:abc",
                                           "worker_id": 24680}), \
                patch.object(api, "_register_account", side_effect=fake_register), \
                patch.object(api, "account_response",
                             side_effect=lambda row, **_kwargs: {"id": row.id}):
            result = api.import_plugin_session(
                _payload(platform="wxgzh",
                         storage_state={"cookies": [{"name": "slave_user",
                                                     "value": "gh_test"}]}),
                db=db,
                current_user=object(),
            )

        self.assertTrue(result["identity_resolved"])
        self.assertEqual(captured["payload"].platform, "wxgzh")


if __name__ == "__main__":
    unittest.main()
