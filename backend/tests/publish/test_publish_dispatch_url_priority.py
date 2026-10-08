import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo.api.publish_dispatch import (
    ReportIn,
    _normalize_report_result,
    _note_success_without_url,
    _queue_link,
)
from geo.models.publish_accounts import PublishFailureType


class PublishDispatchUrlPriorityTests(unittest.TestCase):
    def test_published_url_wins_over_submit_unknown(self):
        notes = []
        payload = ReportIn(
            dispatch_id=1,
            worker_id=9,
            result=PublishFailureType.SUBMIT_UNKNOWN,
            published_url="https://zhuanlan.zhihu.com/p/123",
        )

        result = _normalize_report_result(payload, notes)

        self.assertEqual(result, PublishFailureType.SUCCESS)
        self.assertIn("回执带 published_url", notes[0])

    def test_submit_unknown_without_url_stays_unknown(self):
        notes = []
        payload = ReportIn(
            dispatch_id=1,
            worker_id=9,
            result=PublishFailureType.SUBMIT_UNKNOWN,
            draft_id="123",
        )

        result = _normalize_report_result(payload, notes)

        self.assertEqual(result, PublishFailureType.SUBMIT_UNKNOWN)
        self.assertEqual(notes, [])

    def test_wechat_draft_id_finishes_as_draft_saved(self):
        task = SimpleNamespace(id=1, channel_meta_json="{}", status="publishing",
                               last_error="old")
        db = SimpleNamespace(get=lambda *_args: task)
        dispatch = SimpleNamespace(platform="wxgzh", task_id=1)
        payload = ReportIn(
            dispatch_id=1,
            worker_id=9,
            result=PublishFailureType.SUCCESS,
            draft_id="draft-123",
            draft_url="https://mp.weixin.qq.com/cgi-bin/appmsg?appmsgid=draft-123",
            url_source="wechat_draft_id",
        )
        notes = []

        _note_success_without_url(db, dispatch, payload, notes)

        self.assertEqual(task.status, "draft_saved")
        self.assertIsNone(task.last_error)
        self.assertIn("公众号草稿已保存", notes[0])

    def test_report_accepts_numeric_wechat_draft_id(self):
        payload = ReportIn(
            dispatch_id=1,
            worker_id=9,
            result=PublishFailureType.SUCCESS,
            draft_id=100000016,
            url_source="wechat_draft_id",
        )

        self.assertEqual(payload.draft_id, "100000016")

    def test_wechat_public_url_skips_candidate_probe(self):
        task = SimpleNamespace(id=1, channel_meta_json="{}", status="publishing",
                               last_error="old")
        db = SimpleNamespace(get=lambda *_args: task)
        dispatch = SimpleNamespace(platform="wxgzh", task_id=1, account_db_id=8)
        payload = ReportIn(
            dispatch_id=1,
            worker_id=9,
            result=PublishFailureType.SUCCESS,
            published_url="https://mp.weixin.qq.com/s/example",
            url_source="official_api",
        )
        notes = []

        with patch("geo.api.publish_dispatch.mark_published") as mark:
            _note_success_without_url(db, dispatch, payload, notes)

        mark.assert_called_once()
        self.assertIn("公众号发布回执已确认", notes[0])

    def test_draft_saved_queue_returns_draft_link(self):
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg?appmsgid=100000016"
        dispatch = SimpleNamespace(draft_url=None)
        task = SimpleNamespace(published_url=None,
                               channel_meta_json=f'{{"draft_url": "{url}"}}')

        self.assertEqual(_queue_link(dispatch, task, "draft_saved"), ("draft", url))


if __name__ == "__main__":
    unittest.main()
