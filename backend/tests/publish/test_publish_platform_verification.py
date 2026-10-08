import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from geo.services import publish_task_service
from geo.api import content


class FakeDb:
    committed = False

    def flush(self):
        pass

    def commit(self):
        self.committed = True


def ctrip_task():
    meta = {
        "candidate_url": "https://m.ctrip.com/webapp/you/community/detail?articleId=456",
        "url_verification": {
            "candidate_url": "https://m.ctrip.com/webapp/you/community/detail?articleId=456",
            "verification_method": "platform_api",
            "platform_code": "ctrip",
            "external_post_id": "456",
            "attempts": 0,
        },
    }
    return SimpleNamespace(
        id=1,
        status="url_submitted",
        channel_meta_json=json.dumps(meta),
        proof_json="{}",
        published_url=None,
        actual_published_at=None,
        last_error=None,
        verified_at=None,
    )


def report(task, status, observed_at, **kwargs):
    return publish_task_service.report_platform_verification(
        FakeDb(), task,
        platform_code="ctrip",
        external_post_id="456",
        status=status,
        observation_id=f"{status}:{observed_at}",
        observed_at=observed_at,
        **kwargs,
    )


class PublishPlatformVerificationTests(unittest.TestCase):
    def setUp(self):
        self.t0 = (datetime.utcnow() - timedelta(days=2)).replace(microsecond=0)
        self.initial = patch.object(
            publish_task_service, "PLATFORM_VERIFICATION_INITIAL_DELAY", timedelta(minutes=5))
        self.confirm = patch.object(
            publish_task_service, "PLATFORM_VERIFICATION_CONFIRM_DELAY", timedelta(minutes=5))
        self.initial.start()
        self.confirm.start()

    def tearDown(self):
        self.confirm.stop()
        self.initial.stop()

    def test_first_published_only_starts_stability_window(self):
        task = ctrip_task()
        with patch.object(publish_task_service, "mark_published") as finalize:
            report(task, "published", f"{self.t0.isoformat()}Z", raw_status=2,
                   publish_time="2026-09-13 12:30:00")

        verification = json.loads(task.channel_meta_json)["url_verification"]
        self.assertEqual(task.status, "url_submitted")
        self.assertIsNone(task.published_url)
        self.assertEqual(verification["status"], "published_pending_confirmation")
        self.assertEqual(verification["first_published_at"], self.t0.isoformat())
        self.assertEqual(verification["next_at"], (self.t0 + timedelta(minutes=5)).isoformat())
        finalize.assert_not_called()

    def test_second_published_after_window_uses_shared_finalizer(self):
        task = ctrip_task()
        report(task, "published", f"{self.t0.isoformat()}Z", raw_status=2,
               publish_time="2026-09-13 12:30:00")
        captured = {}

        def fake_mark(_db, row, url, **kwargs):
            captured.update(url=url, **kwargs)
            row.status = "published"
            return row

        with patch.object(publish_task_service, "mark_published", side_effect=fake_mark):
            result = report(task, "published", f"{(self.t0 + timedelta(minutes=5)).isoformat()}Z", raw_status=2)

        self.assertEqual(result.status, "published")
        self.assertIn("articleId=456", captured["url"])
        self.assertEqual(captured["actual_published_at"], datetime(2026, 9, 13, 4, 30))
        self.assertEqual(captured["proof"]["source"], "platform_api")

    def test_early_and_duplicate_published_do_not_finalize(self):
        task = ctrip_task()
        report(task, "published", f"{self.t0.isoformat()}Z")
        with patch.object(publish_task_service, "mark_published") as finalize:
            early = f"{(self.t0 + timedelta(minutes=1)).isoformat()}Z"
            report(task, "published", early)
            report(task, "published", early)
        self.assertEqual(task.status, "url_submitted")
        finalize.assert_not_called()

    def test_transient_status_keeps_first_published_and_reschedules(self):
        task = ctrip_task()
        report(task, "published", f"{self.t0.isoformat()}Z")
        report(task, "reviewing", f"{(self.t0 + timedelta(minutes=5)).isoformat()}Z", raw_status=1)
        verification = json.loads(task.channel_meta_json)["url_verification"]
        self.assertEqual(task.status, "url_submitted")
        self.assertEqual(verification["status"], "published_pending_confirmation")
        self.assertEqual(verification["first_published_at"], self.t0.isoformat())
        self.assertEqual(verification["next_at"], (self.t0 + timedelta(minutes=10)).isoformat())

        report(task, "draft", f"{(self.t0 + timedelta(minutes=10)).isoformat()}Z", raw_status=99)
        verification = json.loads(task.channel_meta_json)["url_verification"]
        self.assertEqual(task.status, "url_submitted")
        self.assertEqual(verification["status"], "published_pending_confirmation")
        self.assertEqual(verification["last_platform_status"], "draft")

    def test_query_error_retries_without_clearing_first_published(self):
        task = ctrip_task()
        report(task, "published", f"{self.t0.isoformat()}Z")
        report(task, "query_error", f"{(self.t0 + timedelta(minutes=5)).isoformat()}Z", error_message="登录已失效")
        verification = json.loads(task.channel_meta_json)["url_verification"]
        self.assertEqual(task.status, "url_submitted")
        self.assertEqual(verification["first_published_at"], self.t0.isoformat())
        self.assertEqual(verification["last_error"], "登录已失效")
        self.assertEqual(verification["next_at"], (self.t0 + timedelta(minutes=10)).isoformat())

    def test_rejected_is_terminal_and_keeps_candidate_only(self):
        task = ctrip_task()
        report(task, "published", f"{self.t0.isoformat()}Z")
        with patch.object(publish_task_service, "mark_published") as finalize:
            report(task, "rejected", f"{(self.t0 + timedelta(minutes=5)).isoformat()}Z", raw_status=3,
                   audit_fail_reason="内容与旅行无关")
        verification = json.loads(task.channel_meta_json)["url_verification"]
        self.assertEqual(task.status, "failed")
        self.assertIsNone(task.published_url)
        self.assertEqual(verification["status"], "rejected")
        self.assertIn("内容与旅行无关", task.last_error)
        self.assertIn("articleId=456", verification["candidate_url"])
        finalize.assert_not_called()

    def test_platform_api_candidate_cannot_be_confirmed_manually(self):
        task = ctrip_task()
        with self.assertRaisesRegex(ValueError, "不能人工确认"):
            publish_task_service.confirm_candidate_url(FakeDb(), task)

        with self.assertRaisesRegex(ValueError, "不能通过普通链接回填"):
            publish_task_service.submit_url(FakeDb(), task, "https://example.com/article")

    def test_republished_task_replaces_current_url_with_new_candidate(self):
        task = ctrip_task()
        task.status = "published"
        task.published_url = "https://m.ctrip.com/webapp/you/community/detail?articleId=old"
        task.url_canonical = task.published_url
        task.actual_published_at = self.t0
        task.verified_at = self.t0
        task.retest_probe_id = 9
        new_url = "https://m.ctrip.com/webapp/you/community/detail?articleId=new"

        with patch.object(
            publish_task_service, "_validate_url_for_task", return_value=(new_url, new_url),
        ):
            publish_task_service.submit_candidate_url(
                FakeDb(), task, new_url,
                proof={
                    "verification_method": "platform_api",
                    "platform_code": "ctrip",
                    "external_post_id": "new",
                },
            )

        verification = json.loads(task.channel_meta_json)["url_verification"]
        self.assertEqual(task.status, "url_submitted")
        self.assertIsNone(task.published_url)
        self.assertIsNone(task.url_canonical)
        self.assertIsNone(task.actual_published_at)
        self.assertIsNone(task.verified_at)
        self.assertIsNone(task.retest_probe_id)
        self.assertEqual(verification["candidate_url"], new_url)
        self.assertEqual(verification["external_post_id"], "new")

    def test_manual_refresh_makes_existing_executor_due_now(self):
        task = ctrip_task()
        meta = json.loads(task.channel_meta_json)
        meta["url_verification"]["verification_executor"] = "server_pool"
        meta["url_verification"]["next_at"] = "2099-01-01T00:00:00"
        task.channel_meta_json = json.dumps(meta)
        db = FakeDb()
        user = SimpleNamespace(id=9, role="user", is_admin=False)

        with patch.object(content, "_own_task_or_404", return_value=task), \
                patch.object(publish_task_service, "task_to_dict", side_effect=lambda row, **_: {
                    "channel_meta": json.loads(row.channel_meta_json),
                }):
            result = content.refresh_publish_verification(1, current_user=user, db=db)

        verification = result["channel_meta"]["url_verification"]
        self.assertLessEqual(datetime.fromisoformat(verification["next_at"]), datetime.utcnow())
        self.assertEqual(verification["verification_executor"], "server_pool")
        self.assertTrue(db.committed)


if __name__ == "__main__":
    unittest.main()
