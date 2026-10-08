from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from geo.api.publish_dispatch import ReportIn, _create_one, report
from geo.models.ai_telemetry import TopicDocPublishTaskORM
from geo.models.publish_accounts import (
    PublishAccountORM,
    PublishDispatchTaskORM,
    PublishFailureType,
)


class ReportDb:
    def __init__(self, dispatch, account, task):
        self.dispatch = dispatch
        self.account = account
        self.task = task

    def get(self, model, _id):
        return {
            PublishDispatchTaskORM: self.dispatch,
            PublishAccountORM: self.account,
            TopicDocPublishTaskORM: self.task,
        }.get(model)

    def query(self, model):
        query = MagicMock()
        query.filter.return_value.with_for_update.return_value.one_or_none.return_value = (
            self.dispatch if model is PublishDispatchTaskORM else None
        )
        return query

    def commit(self):
        pass


class PublishDispatchRetryTests(unittest.TestCase):
    def test_safe_automatic_retry_clears_previous_draft_evidence(self):
        now = datetime.utcnow()
        dispatch = SimpleNamespace(
            id=10, task_id=20, account_db_id=30, platform="zhihu",
            status="running", attempt=1, max_attempts=3,
            claimed_by_worker_id=9, claimed_at=now,
            draft_id="draft-old", draft_url="https://example.com/draft-old",
            screenshot_path="old.png", finished_at=None, visible_after=now,
            last_error=None, updated_at=now,
        )
        account = SimpleNamespace(
            id=30, status="active", exit_ip=None, fail_counts_json="{}",
            last_fail_type=None, last_fail_reason=None, last_fail_at=None,
            lease_worker_id=9, lease_until=now + timedelta(minutes=30),
            used_date=None, used_today=0, updated_at=now,
        )
        task = SimpleNamespace(status="publishing", last_error=None)

        report(ReportIn(
            dispatch_id=10, worker_id=9, result=PublishFailureType.CRASH,
            error="commit request not sent", draft_id="draft-old",
            failure_phase="pre_commit", confirmed_not_published=True,
        ), db=ReportDb(dispatch, account, task))

        self.assertEqual(dispatch.status, "queued")
        self.assertIsNone(dispatch.draft_id)
        self.assertIsNone(dispatch.draft_url)
        self.assertIsNone(dispatch.screenshot_path)

    def test_manual_retry_clears_business_failure_state(self):
        task = SimpleNamespace(
            id=20, status="failed", last_error="first attempt failed",
            operation_context_id=None, channel_meta_json="{}",
        )
        task_query = MagicMock()
        task_query.filter.return_value.one_or_none.return_value = task
        dispatch_query = MagicMock()
        dispatch_query.filter.return_value.first.return_value = None
        db = MagicMock()
        db.query.side_effect = [task_query, dispatch_query]
        doc = SimpleNamespace(id=1, vscl_action_id=None)
        topic = SimpleNamespace(id=2)
        account = SimpleNamespace(
            id=30, platform="zhihu", account_handle="账号", account_id="account-1",
        )

        result = _create_one(db, doc, topic, account, None, datetime.utcnow())

        self.assertEqual(result["action"], "reused_task")
        self.assertEqual(task.status, "planned")
        self.assertIsNone(task.last_error)


if __name__ == "__main__":
    unittest.main()
