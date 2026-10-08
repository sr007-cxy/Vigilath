from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from geo.api import publish_dispatch as dispatch


def account(owner_worker_id=None):
    return SimpleNamespace(
        channel=dispatch.CHANNEL_SERVER_POOL,
        status="active",
        owner_worker_id=owner_worker_id,
        expires_at=None,
        rate_limited_until=None,
        unschedulable_until=None,
        lease_until=None,
        cooldown_minutes=0,
        last_used_at=None,
    )


class PublishDispatchAutoBindTests(unittest.TestCase):
    @patch.object(dispatch, "_quota_ok", return_value=True)
    def test_unbound_account_is_schedulable_for_first_worker(self, _quota_ok):
        ok, reason = dispatch.account_schedulable(
            object(), account(), datetime.utcnow(), "2026-09-15", worker_id=9,
        )

        self.assertTrue(ok)
        self.assertIsNone(reason)

    @patch.object(dispatch, "_quota_ok", return_value=True)
    def test_bound_account_rejects_other_worker(self, _quota_ok):
        ok, reason = dispatch.account_schedulable(
            object(), account(owner_worker_id=8), datetime.utcnow(), "2026-09-15",
            worker_id=9,
        )

        self.assertFalse(ok)
        self.assertIn("归属 worker 8", reason)


if __name__ == "__main__":
    unittest.main()
