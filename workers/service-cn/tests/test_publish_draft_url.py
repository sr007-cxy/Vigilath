from __future__ import annotations

from unittest import TestCase

from app.publish_api import _unknown_or_timeout
from app.publishers.base import PublishRequest, PublishResult
from app.publishers.zhihu import ZhihuPublisher


class _Page:
    def __init__(self, url: str):
        self.url = url


class PublishDraftUrlTests(TestCase):
    def test_zhihu_accepts_only_observed_matching_editor_url(self):
        adapter = ZhihuPublisher()
        good = "https://zhuanlan.zhihu.com/p/123/edit"
        self.assertEqual(adapter.observed_draft_url(_Page(good), "123"), good)
        self.assertIsNone(adapter.observed_draft_url(
            _Page("https://zhuanlan.zhihu.com/p/999/edit"), "123"))
        self.assertIsNone(adapter.observed_draft_url(
            _Page("https://evil.example/p/123/edit"), "123"))

    def test_commit_and_timeout_preserve_observed_draft_url(self):
        req = PublishRequest(platform="zhihu", account_id="a", title="t", body="b")
        url = "https://zhuanlan.zhihu.com/p/123/edit"
        req.mark_committed(draft_id="123", draft_url=url)
        result = _unknown_or_timeout(req.commit, "timeout", "dirty")
        self.assertEqual(result["draft_url"], url)
        self.assertEqual(result["fail_type"], "submit_unknown")

    def test_result_serializes_draft_url(self):
        url = "https://zhuanlan.zhihu.com/p/123/edit"
        self.assertEqual(PublishResult(ok=False, draft_url=url).as_dict()["draft_url"], url)
