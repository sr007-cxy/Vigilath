import unittest
from datetime import datetime, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.api.publish_dispatch import (  # noqa: E402
    CLAIM_STALE_MINUTES,
    ResolveStuckIn,
    _resolve_stuck_dispatch,
)
from geo.api.publish_accounts import local_today  # noqa: E402
from geo.models.ai_telemetry import TopicDocPublishTaskORM  # noqa: E402
from geo.models.publish_accounts import PublishAccountORM  # noqa: E402


class FakeDb:
    def __init__(self, account, task):
        self.account = account
        self.task = task

    def get(self, model, _id):
        if model is PublishAccountORM:
            return self.account
        if model is TopicDocPublishTaskORM:
            return self.task
        return None


def make_rows(now, *, status="running", claimed_minutes=None, used_today=2,
              lease_minutes=-1):
    claimed_at = None if claimed_minutes is None else now - timedelta(minutes=claimed_minutes)
    dispatch = SimpleNamespace(
        id=101,
        task_id=201,
        account_db_id=301,
        status=status,
        attempt=1,
        max_attempts=3,
        claimed_by_worker_id=9,
        claimed_at=claimed_at,
        finished_at=None,
        last_error=None,
        updated_at=None,
        draft_id="draft-1",
    )
    account = SimpleNamespace(
        id=301,
        status="active",
        lease_worker_id=9,
        lease_until=now + timedelta(minutes=lease_minutes),
        used_today=used_today,
        used_date=local_today(),
        updated_at=None,
    )
    task = SimpleNamespace(status="publishing", last_error=None)
    return dispatch, account, task


class ResolveStuckDispatchTests(unittest.TestCase):
    def test_running_before_stale_threshold_is_rejected(self):
        now = datetime(2026, 8, 26, 8, 0, 0)
        dispatch, account, task = make_rows(
            now, claimed_minutes=max(CLAIM_STALE_MINUTES - 1, 0))

        with self.assertRaises(HTTPException) as ctx:
            _resolve_stuck_dispatch(
                FakeDb(account, task),
                dispatch,
                ResolveStuckIn(reason="人工检查"),
                now=now,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(dispatch.status, "running")

    def test_stale_running_is_failed_and_not_requeued(self):
        now = datetime(2026, 8, 26, 8, 0, 0)
        dispatch, account, task = make_rows(
            now, claimed_minutes=CLAIM_STALE_MINUTES + 5)

        out = _resolve_stuck_dispatch(
            FakeDb(account, task),
            dispatch,
            ResolveStuckIn(reason="平台后台查不到结果"),
            operator_id=7,
            now=now,
        )

        self.assertTrue(out["resolved"])
        self.assertEqual(dispatch.status, "failed")
        self.assertEqual(dispatch.attempt, dispatch.max_attempts)
        self.assertNotEqual(dispatch.status, "queued")
        self.assertIsNone(account.lease_worker_id)
        self.assertIsNone(account.lease_until)
        self.assertEqual(account.used_today, 2)
        self.assertEqual(task.status, "failed")
        self.assertIn("禁止重试", dispatch.last_error)

    def test_confirmed_not_published_releases_claimed_quota(self):
        now = datetime(2026, 8, 26, 8, 0, 0)
        dispatch, account, task = make_rows(
            now, claimed_minutes=CLAIM_STALE_MINUTES + 5, used_today=2)

        _resolve_stuck_dispatch(
            FakeDb(account, task),
            dispatch,
            ResolveStuckIn(reason="平台后台确认未发布", confirmed_not_published=True),
            now=now,
        )

        self.assertEqual(account.used_today, 1)

    def test_stale_task_does_not_clear_newer_lease(self):
        now = datetime(2026, 8, 26, 8, 0, 0)
        dispatch, account, task = make_rows(
            now, claimed_minutes=CLAIM_STALE_MINUTES + 5, lease_minutes=10)

        _resolve_stuck_dispatch(
            FakeDb(account, task), dispatch,
            ResolveStuckIn(reason="旧任务超时"), now=now,
        )

        self.assertIsNotNone(account.lease_until)
        self.assertEqual(account.lease_worker_id, 9)

    def test_final_status_is_idempotent(self):
        now = datetime(2026, 8, 26, 8, 0, 0)
        dispatch, account, task = make_rows(now, status="failed")

        out = _resolve_stuck_dispatch(
            FakeDb(account, task),
            dispatch,
            ResolveStuckIn(reason="重复点击"),
            now=now,
        )

        self.assertFalse(out["resolved"])
        self.assertEqual(dispatch.status, "failed")
        self.assertEqual(account.used_today, 2)


if __name__ == "__main__":
    unittest.main()
