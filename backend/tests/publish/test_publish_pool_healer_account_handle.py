from datetime import datetime
import json
import unittest

from geo.models.publish_accounts import PublishAccountORM
from geo.services.publish_pool_healer import _apply_probe_result


def _row(handle="旧昵称"):
    return PublishAccountORM(
        platform="zhihu",
        account_id="api.me.url_token:abc",
        account_handle=handle,
        channel="server_pool",
        status="active",
        fail_counts_json="{}",
        egress="host",
    )


class PublishPoolHealerAccountHandleTests(unittest.TestCase):
    def test_success_updates_account_handle_and_probe_snapshot(self):
        row = _row()
        notes = []

        label = _apply_probe_result(row, {
            "ok": True,
            "reachable": True,
            "account_id": row.account_id,
            "account_handle": "知乎号",
            "exit_ip": "1.2.3.4",
            "storage_state": None,
            "fail_type": None,
        }, datetime(2026, 8, 25, 12, 0, 0), notes)

        self.assertEqual(label, "ok")
        self.assertEqual(row.account_handle, "知乎号")
        probe = json.loads(row.identity_probe_json)
        self.assertEqual(probe["account_handle"], "知乎号")

    def test_success_keeps_old_handle_when_probe_handle_is_empty(self):
        row = _row()

        _apply_probe_result(row, {
            "ok": True,
            "reachable": True,
            "account_id": row.account_id,
            "account_handle": "",
            "exit_ip": "1.2.3.4",
            "storage_state": None,
            "fail_type": None,
        }, datetime(2026, 8, 25, 12, 0, 0), [])

        self.assertEqual(row.account_handle, "旧昵称")
        self.assertIsNone(json.loads(row.identity_probe_json)["account_handle"])

    def test_failures_do_not_overwrite_account_handle(self):
        cases = [
            {"ok": False, "reachable": False, "fail_type": "unreachable"},
            {"ok": False, "reachable": True, "fail_type": "login_lost"},
            {"ok": False, "reachable": True, "fail_type": "identity_mismatch",
             "account_id": "api.me.url_token:other"},
        ]
        for res in cases:
            with self.subTest(fail_type=res["fail_type"]):
                row = _row()
                _apply_probe_result(row, {**res, "account_handle": "新昵称"},
                                    datetime(2026, 8, 25, 12, 0, 0), [])
                self.assertEqual(row.account_handle, "旧昵称")


if __name__ == "__main__":
    unittest.main()
