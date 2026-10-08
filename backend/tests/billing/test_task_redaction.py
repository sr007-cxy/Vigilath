"""Task output redaction -- the user side can't see the supplier vendor, upstream resource id, purchase price or raw upstream response.

Changing only the vendor name in the frontend doesn't plug it: `task_to_dict` once sent the whole `channel_meta` as-is.
"""
from __future__ import annotations

import json

from geo.models.ai_telemetry import TopicDocPublishTaskORM
from geo.services import publish_task_service
from geo.services.ruanwenjiang_publisher import PLATFORM_NAMES

_SECRETS = ("provider", "resource_id", "resource_title", "quoted_price",
            "confirmed_price", "add_response")


def _task():
    return TopicDocPublishTaskORM(
        id=7, topic_id=1, doc_id=2, platform=PLATFORM_NAMES[0],
        media_name="搜狐网·科技频道", channel_type="paid", publish_mode="api",
        status="published", cost_cents=3500,
        channel_meta_json=json.dumps({
            "type": "Website", "delivery": "1", "draft_id": 99,
            "provider": "rwj", "resource_id": 12345,
            "resource_title": "搜狐网·科技频道",
            "quoted_price": "35.00", "confirmed_price": "35.00",
            "add_response": {"order_no": "X1", "raw": "..."},
        }),
    )


def test_redacted_by_default():
    """The default must be redaction -- this function has about ten call sites; one missed argument leaks everything."""
    out = publish_task_service.task_to_dict(_task())
    meta = out["channel_meta"]
    for k in _SECRETS:
        assert k not in meta, f"{k} 泄露给用户了"
    assert "cost_cents" not in out, "进货价泄露给用户了"
    assert out["platform_display"] == publish_task_service.PAID_PLATFORM_DISPLAY
    assert out["retail_price_yuan"] == "42.00"       # 35 × 1.2


def test_staff_still_sees_everything():
    out = publish_task_service.task_to_dict(_task(), redact=False)
    meta = out["channel_meta"]
    for k in _SECRETS:
        assert k in meta
    assert out["cost_cents"] == 3500


def test_vendor_name_never_reaches_the_user():
    """Even the platform field itself can't be the vendor's Chinese name -- not shown in the frontend != not sent."""
    out = publish_task_service.task_to_dict(_task())
    assert out["platform"] == publish_task_service.PAID_PLATFORM_DISPLAY
    assert PLATFORM_NAMES[0] not in json.dumps(out, ensure_ascii=False)
    # The frontend uses this boolean to tell "is this paid publishing", no longer the vendor name
    assert out["is_paid_media"] is True


def test_db_column_itself_is_untouched():
    """The platform column in the DB must still be the vendor's Chinese name -- _task_provider / poller / liveness
    all rely on it to look up the site; changing it means "charged but no URL back". Redaction happens only in output."""
    t = _task()
    publish_task_service.task_to_dict(t)
    assert t.platform == PLATFORM_NAMES[0]
    assert publish_task_service.task_to_dict(t, redact=False)["platform"] == PLATFORM_NAMES[0]


def test_non_paid_platform_keeps_its_name():
    t = _task()
    t.platform = "公众号"
    t.channel_meta_json = json.dumps({"type": "manual", "account_id": "a1"})
    out = publish_task_service.task_to_dict(t)
    assert out["platform_display"] == "公众号"
